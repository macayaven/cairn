"""Retry the release step against a local release with an id and byte-valued assets."""

import json
import re
import sys
from pathlib import Path

import pytest
from ci_shell import SYSTEM, run_step
from test_ci_release_unit import artifact, shim
from test_ci_workflow_unit import JOBS, REPO

TAG = "v1.2.3"
RUN_ID = "4242"

# Each invocation reads the preceding invocation's state. Publishing after a read happens
# after its reply has been computed, even if the next invocation is a write by the same id.
GH = r"""#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

path = Path(os.environ["RELEASE_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
state["calls"].append(args)
release = state["release"]
rc = 0
method = None
if args[0] == "api":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-X", "--method", type=str.upper)
    parser.add_argument("-f", "-F", "--field", "--raw-field", "--input", dest="data")
    options, _ = parser.parse_known_args(args[1:])
    method = options.method or ("POST" if options.data is not None else "GET")
if method in {"GET", "HEAD"}:
    listing = [release] if release else []
    if "--jq" in args:
        query = args[args.index("--jq") + 1]
        done = subprocess.run(["jq", "-r", query], input=json.dumps(listing), text=True)
        rc = done.returncode
    else:
        print(json.dumps(listing))
elif args[:2] == ["release", "download"]:
    dest = Path(args[args.index("--dir") + 1])
    dest.mkdir(parents=True, exist_ok=True)
    for asset in release["assets"]:
        (dest / asset["name"]).write_bytes(bytes.fromhex(asset["data"]))
elif args[:2] == ["release", "create"]:
    state["writes"].append(args)
    if release:
        rc = 1
    else:
        assets = [{"id": 200 + n, "name": name, "data": Path(name).read_bytes().hex()}
                  for n, name in enumerate(state["files"])]
        state["release"] = {"id": 88, "tag_name": args[2], "draft": False, "assets": assets}
elif args[:2] == ["release", "delete-asset"]:
    state["writes"].append(args)
    release["assets"] = [a for a in release["assets"] if a["name"] != args[3]]
elif args[:2] == ["release", "delete"] or method == "DELETE":
    state["writes"].append(args)
    endpoint = next((arg for arg in args if "/releases/" in arg), "")
    if "/assets/" in endpoint:
        asset_id = endpoint.rsplit("/", 1)[-1]
        release["assets"] = [a for a in release["assets"] if str(a["id"]) != asset_id]
    elif args[:2] == ["release", "delete"] or endpoint.endswith("/" + str(release["id"])):
        state["release"] = None
    else:
        rc = 1
elif method or args[:2] in (["release", "edit"], ["release", "upload"]):
    state["writes"].append(args)
    rc = 1
else:
    state["unsupported"].append(args)
    rc = 2
remaining = state["release"]
if remaining and remaining["id"] == 77 and remaining["draft"]:
    state["boundaries"].append(len(state["calls"]))
    if len(state["calls"]) == state["publish_after"]:
        remaining["draft"] = False
path.write_text(json.dumps(state))
sys.exit(rc)
"""


def retry(folder, *, tag=TAG, draft=True, assets=None, change_wheel=False, publish_after=None):
    folder.mkdir()
    package = artifact(folder)
    files = {p.name: p.read_bytes() for p in sorted(package.iterdir()) if p.name != "notes.md"}
    remote = files.copy() if assets is None else assets
    if change_wheel:
        wheel = next(n for n in files if n.endswith(".whl"))
        remote[wheel] = b"?" + remote[wheel][1:]  # same name and size
    existing = {
        "id": 77,
        "tag_name": tag,
        "prerelease": "-rc." in tag,
        "draft": draft and publish_after != 0,
        "body": (package / "notes.md").read_text() + f"\n<!-- cairn-ci-cd run {RUN_ID} -->\n",
        "assets": [
            {"id": n, "name": name, "size": len(data), "data": data.hex()}
            for n, (name, data) in enumerate(remote.items(), 100)
        ],
    }
    path = folder / "state.json"
    path.write_text(
        json.dumps(
            {
                "release": existing,
                "calls": [],
                "writes": [],
                "unsupported": [],
                "publish_after": publish_after,
                "boundaries": [0] if existing["draft"] else [],
                "files": list(files),
            }
        )
    )
    bin_dir = folder / "bin"
    shim(bin_dir)
    gh = bin_dir / "gh"
    gh.write_text(GH)
    gh.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{SYSTEM}",
        "RUNNER_TEMP": str(folder),
        "GITHUB_WORKSPACE": str(REPO),
        "GITHUB_REF_NAME": tag,
        "GITHUB_RUN_ID": RUN_ID,
        "CANDIDATE": "true" if existing["prerelease"] else "",
        "GH_REPO": "wren/ledger",
        "RELEASE_STATE": str(path),
    }
    errors = []
    for step in JOBS["release"]["steps"]:
        if "run" in step:
            done = run_step(step["run"], cwd=REPO, env=env, shell=step.get("shell"))
            lines = (done.stdout + "\n" + done.stderr).splitlines()
            errors.extend(line for line in lines if line.startswith("::error::"))
            if done.returncode:
                break
    state = json.loads(path.read_text())
    assert state["calls"], done.stdout + done.stderr
    assert not state["unsupported"], state["unsupported"]
    return done, errors, state, existing


@pytest.mark.parametrize(
    ("tag", "assets"),
    [(TAG, None), (TAG + "-rc.1", {"SHA256SUMS": b"short"}), (TAG, {})],
    ids=["complete", "partial-candidate", "empty"],
)
def test_marked_draft_stops_without_writes(tmp_path, tag, assets):
    done, errors, state, existing = retry(tmp_path / "draft", tag=tag, assets=assets)
    assert state["writes"] == []
    assert done.returncode != 0 and len(errors) == 1
    assert errors[0].removeprefix("::error::").strip()
    assert state["release"] == existing


@pytest.mark.parametrize(
    ("tag", "same"),
    [(TAG, True), (TAG + "-rc.1", True), (TAG, False)],
    ids=["matching", "matching-candidate", "different-bytes"],
)
def test_published_retry_compares_the_files(tmp_path, tag, same):
    done, errors, state, existing = retry(
        tmp_path / "published", tag=tag, draft=False, change_wheel=not same
    )
    assert state["release"] == existing and state["writes"] == []
    assert (done.returncode == 0) == same, done.stdout + done.stderr
    assert len(errors) == (0 if same else 1)


@pytest.mark.parametrize(
    ("tag", "assets"),
    [(TAG, None), (TAG + "-rc.1", {"SHA256SUMS": b"part"})],
    ids=["all-files", "partial-candidate"],
)
def test_publication_at_each_call_boundary_preserves_release_and_assets(tmp_path, tag, assets):
    # Until the person acts, the draft follows the undisturbed path. Replay each boundary
    # where the original draft still exists, including the instant after its last listing.
    _, _, draft, _ = retry(tmp_path / "undisturbed", tag=tag, assets=assets)
    lost = []
    for after in draft["boundaries"]:
        _, _, state, original = retry(
            tmp_path / f"after-{after}", tag=tag, assets=assets, publish_after=after
        )
        if state["release"] != (original | {"draft": False}) or state["writes"]:
            lost.append((after, state["writes"]))
    assert not lost, lost


DELETE = re.compile(
    r"\bgh\s+release\s+delete(?:-asset)?\b|(?:-X[=\s]*|--(?:method|request)[=\s]+)DELETE\b"
    r"|\bDELETE\s+/?repos/[^\n]*?/releases\b|\bdeleteRelease(?:Asset)?\b"
    r"|\bdelete_release(?:_asset)?\b|\bmethod\s*[:=]\s*DELETE\b"
    r"|\b(?:requests|httpx|client|session)\.delete\s*\("
    r"|\bgh\s+release\s+upload\b[^\n]*?--clobber\b",
    re.IGNORECASE,
)
SCRIPT = re.compile(
    r"(?<![\w.-])((?:\./|scripts/)[\w./-]+|(?:[\w.-]+/)*[\w.-]+\.(?:sh|bash|py|[cm]?js))"
)


def local_scripts(base, line):
    for name in SCRIPT.findall(line):
        for parent in (base, REPO):
            path = (parent / name).resolve()
            if not path.is_relative_to(REPO):
                continue
            candidates = (path / "action.yml", path / "action.yaml") if path.is_dir() else (path,)
            script = next((p for p in candidates if p.is_file()), None)
            if script is not None:
                yield script
                break


def release_commands():
    """Inspect each step, then the local scripts it names, including their own helpers."""
    pending = [
        (
            REPO,
            step.get("name", "release step"),
            "\n".join(
                (step.get("run", ""), step.get("uses", ""), step.get("with", {}).get("script", ""))
            ),
        )
        for step in JOBS["release"]["steps"]
    ]
    seen = set()
    while pending:
        base, label, source = pending.pop()
        # Join shell continuations and Python or JS argv literals, even on successive lines.
        source = re.sub(r"""['"]\s*,\s*['"]""", " ", source.replace("\\\n", " "))
        for line in source.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # An instruction printed for the operator is ordinary text.
            if (
                re.fullmatch(r"""(?:echo|printf)(?:\s+(?:"[^"]*"|'[^']*'))+""", line)
                and "$(" not in line
                and "`" not in line
            ):
                continue
            yield label, line.replace('"', "").replace("'", "")
            for script in local_scripts(base, line):
                if script not in seen:
                    seen.add(script)
                    pending.append(
                        (script.parent, str(script.relative_to(REPO)), script.read_text())
                    )


def test_release_job_contains_no_delete():
    deletes = [(place, line) for place, line in release_commands() if DELETE.search(line)]
    assert deletes == []
