"""Run the release job against a local gh with editable release notes and titles."""

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path

if __name__ != "__main__":
    import pytest
    import test_reviewer5_release_publish_unit as model
    from ci_shell import SYSTEM, run_step
    from test_ci_release_unit import artifact, shim
    from test_ci_workflow_unit import JOBS, REPO
    from test_release_check_unit import CHANGELOG, release_check


def edit(release, field):
    if field == "title":
        release["name"] += " (edited)"
    elif field == "notes":
        assert "The first entry." in release["body"]
        release["body"] = release["body"].replace("The first entry.", "An edited entry.")
    elif field == "final-newline":
        release["body"] = release["body"].removesuffix("\n")
    elif field == "file-name":
        release["assets"][0]["name"] += ".old"
    elif field == "file-bytes":
        asset = next(a for a in release["assets"] if a["name"].endswith(".whl"))
        raw = bytes.fromhex(asset["data"])
        asset["data"] = (bytes([raw[0] ^ 1]) + raw[1:]).hex()
    else:
        raise ValueError(field)


def release_view(gh, release, args):
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--json", required=True)
    parser.add_argument("--jq", "-q")
    opt = parser.parse_args(args)
    if not release or opt.tag != release["tag_name"]:
        return 1
    fields = {
        "tagName": release["tag_name"],
        "name": release["name"],
        "body": release["body"],
        "databaseId": release["id"],
        "isDraft": release["draft"],
        "isPrerelease": release["prerelease"],
        "assets": gh.public_fields(release)["assets"],
    }
    return gh.api_reply({key: fields[key] for key in opt.json.split(",")}, opt.jq)


def serve_gh(gh, args):
    path = Path(os.environ["RELEASE_STATE"])
    state = json.loads(path.read_text())
    state["calls"] += 1
    try:
        if args[0] == "api":
            rc = gh.api(state, args[1:])
        elif args[:2] == ["release", "view"]:
            rc = release_view(gh, state["release"], args[2:])
        elif args[0] == "release":
            rc = gh.release_command(state, args[1], args[2:])
        else:
            raise ValueError(args)
        # The publication reply describes what the job wrote. The next read sees the edit.
        if state["public"] and state["edit_when_public"] and state["writes_at_edit"] is None:
            edit(state["release"], state["edit_when_public"])
            state["writes_at_edit"] = list(state["writes"])
    except (Exception, SystemExit) as error:
        state["unsupported"].append([args, str(error)])
        rc = 2
    path.write_text(json.dumps(state))
    return rc


if __name__ == "__main__":
    # The isolated gh process loads the same release and asset model used by round five.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_reviewer5_release_publish_unit as gh

    sys.exit(serve_gh(gh, sys.argv[1:]))


def retry(tmp_path, tag, existing, *, changed=None, edit_when_public=None):
    package = artifact(tmp_path)
    notes = release_check.notes("1.2.3", CHANGELOG, tag)
    (package / "notes.md").write_bytes(notes.encode())
    files = sorted(p for p in package.iterdir() if p.name != "SHA256SUMS")
    (package / "SHA256SUMS").write_text(
        "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files)
    )
    remote = model.initial(tag, package, existing=existing)
    original = remote["release"]
    original["name"] = tag
    if changed:
        edit(original, changed)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            remote
            | {
                "calls": 0,
                "writes": [],
                "unsupported": [],
                "edit_when_public": edit_when_public,
                "writes_at_edit": None,
            }
        )
    )
    bin_dir = tmp_path / "bin"
    shim(bin_dir)
    tool = bin_dir / "gh"
    tool.write_text(f'#!/bin/sh\nexec {shlex.join([sys.executable, "-IB", __file__])} "$@"\n')
    tool.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{SYSTEM}",
        "RUNNER_TEMP": str(tmp_path),
        "RELEASE_STATE": str(state_path),
        "GITHUB_WORKSPACE": str(REPO),
        "GITHUB_RUN_ID": model.RUN_ID,
        "GITHUB_REF_NAME": tag,
        "GH_REPO": "wren/ledger",
        "CANDIDATE": "true" if tag == model.CANDIDATE else "",
    }
    for step in JOBS["release"]["steps"]:
        if "run" in step:
            done = run_step(step["run"], env=env, cwd=REPO, shell=step.get("shell"))
            if done.returncode:
                break
    state = json.loads(state_path.read_text())
    assert state["calls"] and not state["unsupported"], (state["unsupported"], done.stderr)
    return done, state, original


def check_diagnostic(done, field):
    assert done.returncode != 0
    what = r"\b(?:title|name)\b" if field == "title" else r"\b(?:notes?|body|text|changelog)\b"
    why = r"differ|chang|match|expect|edit|wrong|instead|\bnot\b|should|must|mismatch"
    lines = [
        line
        for line in (done.stdout + "\n" + done.stderr).casefold().splitlines()
        if not line.lstrip().startswith(("{", "["))
    ]
    assert any(re.search(what, line) and re.search(why, line) for line in lines), lines


@pytest.mark.parametrize("tag", [model.TAG, model.CANDIDATE], ids=["stable", "candidate"])
@pytest.mark.parametrize("existing", ["draft", "published"])
def test_identical_retry_finishes_the_same_release(tmp_path, tag, existing):
    done, state, original = retry(tmp_path, tag, existing)
    assert done.returncode == 0, done.stdout + done.stderr
    assert state["release"] == original | {"draft": False}
    if existing == "published":
        assert not state["writes"]


@pytest.mark.parametrize("existing", ["draft", "published"])
@pytest.mark.parametrize("field", ["notes", "title", "final-newline"])
@pytest.mark.parametrize("tag", [model.TAG, model.CANDIDATE], ids=["stable", "candidate"])
def test_edited_release_stops_without_writing(tmp_path, existing, field, tag):
    done, state, original = retry(tmp_path, tag, existing, changed=field)
    assert model.MARK in original["body"]
    check_diagnostic(done, field)
    assert not state["writes"]
    assert state["release"] == original


@pytest.mark.parametrize("existing", ["draft", "published"])
@pytest.mark.parametrize("field", ["file-name", "file-bytes"])
def test_matching_text_still_requires_identical_files(tmp_path, existing, field):
    done, state, original = retry(tmp_path, model.TAG, existing, changed=field)
    assert done.returncode != 0
    assert not state["writes"]
    assert state["release"] == original


@pytest.mark.parametrize("field", ["notes", "title", "file-name", "file-bytes"])
@pytest.mark.parametrize("tag", [model.TAG, model.CANDIDATE], ids=["stable", "candidate"])
def test_edit_after_publication_is_reported(tmp_path, field, tag):
    done, state, original = retry(tmp_path, tag, "draft", edit_when_public=field)
    assert state["public"] == [original | {"draft": False}]
    assert state["writes_at_edit"] and state["writes"] == state["writes_at_edit"]
    expected = original | {"draft": False}
    edit(expected, field)
    assert state["release"] == expected
    if field in ("notes", "title"):
        check_diagnostic(done, field)
    else:
        assert done.returncode != 0
