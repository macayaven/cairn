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
RUN_SHA = "a1" * 20
UPLOADED_AT = "2026-11-20T12:00:00Z"


def release_tools(bin_dir):
    shim(bin_dir)
    sleep = bin_dir / "sleep"
    sleep.write_text('#!/bin/sh\necho "$1" >> "$RUNNER_TEMP/waits"\n')
    sleep.chmod(0o755)


def refusal(tag, reason):
    return (
        f"::error::read by its id, a GitHub release for {tag} exists "
        f"and is not one this run can finish: {reason}"
    )


def file_error(tag, draft, reason):
    if not draft:
        return refusal(tag, reason)
    return (
        f"::error::read by its id, the GitHub release for {tag} is this run's draft "
        f"and does not hold exactly the files this run attested: {reason}"
    )


# Each invocation reads the preceding invocation's state. Publishing after a read happens
# after its reply has been computed, even if the next invocation is a write by the same id.
GH = r"""#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("gh version 2.102.0 (2026-01-01)")
    print("https://github.com/cli/cli/releases/tag/v2.102.0")
    sys.exit(0)
path = Path(os.environ["RELEASE_STATE"])
state = json.loads(path.read_text())
state["calls"].append(args)
release = state["release"]
rc = 0
method = None
if args[0] == "api":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-X", "--method", type=str.upper)
    parser.add_argument("-f", "-F", "--field", "--raw-field", "--input", dest="data")
    parser.add_argument("-H", "--header", action="append", default=[])
    options, _ = parser.parse_known_args(args[1:])
    method = options.method or ("POST" if options.data is not None else "GET")
if method in {"GET", "HEAD"}:
    prefix = "repos/wren/ledger/releases"
    paths = (arg.lstrip("/") for arg in args if arg.lstrip("/").startswith("repos/"))
    endpoint = next(paths, "").partition("?")[0]
    entry = None if not release else release | {
        "assets": [{k: v for k, v in asset.items() if k != "data"} for asset in release["assets"]]
    }
    reply = None
    error = "gh: Not Found (HTTP 404)"
    if endpoint == f"repos/wren/ledger/commits/tags/{state['tag']}":
        if state["tag_sha"] is None:
            rc = 1
            error = f"gh: No commit found for SHA: tags/{state['tag']} (HTTP 422)"
        elif "Accept: application/vnd.github.sha" in options.header:
            print(state["tag_sha"])
        else:
            reply = {"sha": state["tag_sha"]}
    elif endpoint == prefix:
        reply = [entry] if entry else []
    elif entry and endpoint == f"{prefix}/{entry['id']}":
        reply = entry
    elif release and endpoint.startswith(prefix + "/assets/"):
        asset = next(
            (a for a in release["assets"] if endpoint == f"{prefix}/assets/{a['id']}"), None
        )
        if asset is None:
            rc = 1
        elif "Accept: application/octet-stream" in options.header:
            sys.stdout.buffer.write(bytes.fromhex(asset["data"]))
        else:
            reply = {k: v for k, v in asset.items() if k != "data"}
    else:
        rc = 1
    query = next((args[i + 1] for i, arg in enumerate(args) if arg in ("--jq", "-q")), None)
    if rc:
        print(error, file=sys.stderr)
    elif reply is not None and query:
        done = subprocess.run(["jq", "-r", query], input=json.dumps(reply), text=True)
        rc = done.returncode
    elif reply is not None:
        print(json.dumps(reply))
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
        assets = []
        for n, name in enumerate(state["files"]):
            if name in args[3:]:
                data = Path(name).read_bytes()
                assets.append({
                    "id": 200 + n,
                    "name": name,
                    "state": "uploaded",
                    "size": len(data),
                    "updated_at": state["uploaded_at"],
                    "data": data.hex(),
                })
        state["release"] = {
            "id": 88,
            "tag_name": args[2],
            "draft": "--draft" in args,
            "assets": assets,
            "prerelease": "--prerelease" in args,
            "name": args[args.index("--title") + 1],
            "body": Path(args[args.index("--notes-file") + 1]).read_text(),
            "target_commitish": args[args.index("--target") + 1] if "--target" in args else None,
        }
elif method == "PATCH" and release and f"repos/wren/ledger/releases/{release['id']}" in args:
    state["writes"].append(args)
    if "draft=false" in args:
        release["draft"] = False
        if "make_latest=false" not in args:
            state["latest_id"] = release["id"]
    else:
        rc = 1
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
        "name": tag,
        "prerelease": "-rc." in tag,
        "draft": draft and publish_after != 0,
        "body": (package / "notes.md").read_text() + f"\n<!-- cairn-ci-cd run {RUN_ID} -->\n",
        "assets": [
            {
                "id": n,
                "name": name,
                "state": "uploaded",
                "size": len(data),
                "updated_at": UPLOADED_AT,
                "data": data.hex(),
            }
            for n, (name, data) in enumerate(remote.items(), 100)
        ],
    }
    path = folder / "state.json"
    path.write_text(
        json.dumps(
            {
                "release": existing,
                "tag": tag,
                "tag_sha": RUN_SHA,
                "uploaded_at": UPLOADED_AT,
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
    release_tools(bin_dir)
    gh = bin_dir / "gh"
    gh.write_text(GH)
    gh.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{SYSTEM}",
        "RUNNER_TEMP": str(folder),
        "GITHUB_WORKSPACE": str(REPO),
        "GITHUB_REF_NAME": tag,
        "GITHUB_SHA": RUN_SHA,
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


# The case "complete" (a marked draft that holds every file stops, unwritten) left with the
# fourth report's must-fix 1, which has such a draft compared and published: its cases are
# test_reviewer5_release_publish_unit's "matching retry" and "person at each draft boundary".
@pytest.mark.parametrize(
    ("tag", "assets"),
    [(TAG + "-rc.1", {"SHA256SUMS": b"short"}), (TAG, {})],
    ids=["partial-candidate", "empty"],
)
def test_marked_draft_stops_without_writes(tmp_path, tag, assets):
    done, errors, state, existing = retry(tmp_path / "draft", tag=tag, assets=assets)
    assert state["writes"] == []
    assert done.returncode != 0 and len(errors) == 1
    names = " ".join(sorted(assets))
    assert errors[0].startswith(file_error(tag, True, f"its files are [{names}], not ["))
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
    if not same:
        wheel = next(a["name"] for a in existing["assets"] if a["name"].endswith(".whl"))
        assert errors[0].startswith(
            file_error(tag, False, f"{wheel} is not the file this run attested")
        )


# The all-files race belongs to round five, which checks the job's own publication as well.
@pytest.mark.parametrize(
    ("tag", "assets"),
    [(TAG + "-rc.1", {"SHA256SUMS": b"part"})],
    ids=["partial-candidate"],
)
def test_publication_at_each_call_boundary_preserves_release_and_assets(tmp_path, tag, assets):
    # Until the person acts, the draft follows the undisturbed path. Replay each boundary
    # where the original draft still exists, including the instant after its last listing.
    done, errors, draft, _ = retry(tmp_path / "undisturbed", tag=tag, assets=assets)
    reason = f"its files are [{' '.join(sorted(assets))}], not ["
    assert done.returncode != 0 and errors[0].startswith(file_error(tag, True, reason))
    for after in draft["boundaries"]:
        done, errors, state, original = retry(
            tmp_path / f"after-{after}", tag=tag, assets=assets, publish_after=after
        )
        assert state["release"] == original | {"draft": False} and not state["writes"], after
        assert f"repos/wren/ledger/releases/{original['id']}" in state["calls"][-1]
        # Publication after the last reply leaves that read's diagnosis about a draft true.
        was_draft = after == len(state["calls"])
        assert done.returncode != 0 and len(errors) == 1
        assert errors[0].startswith(file_error(tag, was_draft, reason)), after


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
