"""Run the release job against a local gh with editable release notes and titles."""

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from copy import deepcopy
from pathlib import Path

if __name__ != "__main__":
    import pytest
    import test_reviewer5_release_publish_unit as model
    from ci_shell import SYSTEM, run_step
    from test_ci_release_unit import artifact
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


def serve_gh(gh, args, *, listing=None, after_reply=None):
    path = Path(os.environ["RELEASE_STATE"])
    state = json.loads(path.read_text())
    state["calls"].append(args)
    try:
        if args == ["--version"]:
            version = state["gh_version"]
            print(f"gh version {version} (2026-01-01)")
            print(f"https://github.com/cli/cli/releases/tag/v{version}")
            rc = 0
        elif args[0] == "api":
            # Only discovery can lag. A GET by id reads the current release object.
            rc = gh.api(state, args[1:], listing=listing)
        elif args[0] == "release":
            rc = gh.release_command(state, args[1], args[2:])
        else:
            raise ValueError(args)
        if rc == 0 and after_reply:
            after_reply(state, args)
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


def retry(
    tmp_path,
    tag,
    existing,
    *,
    changed=None,
    edit_when_public=None,
    gh_version="2.102.0",
    gh_script=__file__,
    extra_state=None,
):
    package = artifact(tmp_path)
    notes = release_check.notes("1.2.3", CHANGELOG, tag)
    (package / "notes.md").write_bytes(notes.encode())
    files = sorted(p for p in package.iterdir() if p.name != "SHA256SUMS")
    (package / "SHA256SUMS").write_text(
        "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files)
    )
    remote = model.initial(tag, package, existing=existing)
    original = remote["release"]
    if changed:
        edit(original, changed)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            remote
            | {
                "gh_version": gh_version,
                "calls": [],
                "writes": [],
                "unsupported": [],
                "edit_when_public": edit_when_public,
                "writes_at_edit": None,
            }
            | (extra_state or {})
        )
    )
    bin_dir = tmp_path / "bin"
    model.release_tools(bin_dir)
    if gh_version is None:
        # Keep the shell tools for the artifact and listing steps, even on hosts with /usr/bin/gh.
        search_path = str(bin_dir)
        for name in ("cat", "cut", "ls", "sed", "shasum", "sort", "tr"):
            if executable := shutil.which(name, path=SYSTEM):
                (bin_dir / name).symlink_to(executable)
    else:
        search_path = f"{bin_dir}:{SYSTEM}"
        tool = bin_dir / "gh"
        tool.write_text(f'#!/bin/sh\nexec {shlex.join([sys.executable, "-IB", gh_script])} "$@"\n')
        tool.chmod(0o755)
    env = {
        "PATH": search_path,
        "RUNNER_TEMP": str(tmp_path),
        "RELEASE_STATE": str(state_path),
        "GITHUB_WORKSPACE": str(REPO),
        "GITHUB_RUN_ID": model.RUN_ID,
        "GITHUB_REF_NAME": tag,
        "GITHUB_SHA": model.RUN_SHA,
        "GH_REPO": "wren/ledger",
        "CANDIDATE": "true" if tag == model.CANDIDATE else "",
    }
    failed_step = None
    for index, step in enumerate(JOBS["release"]["steps"]):
        if "run" in step:
            done = run_step(step["run"], env=env, cwd=REPO, shell=step.get("shell"))
            if done.returncode:
                failed_step = index
                break
    state = json.loads(state_path.read_text())
    state["failed_step"] = failed_step
    waits = tmp_path / "waits"
    state["waits"] = waits.read_text().splitlines() if waits.exists() else []
    assert not state["unsupported"], (state["unsupported"], done.stderr)
    if gh_version is not None:
        assert state["calls"], done.stdout + done.stderr
    return done, state, original


@pytest.mark.parametrize(
    "version", ["2.101.0", "devel", None], ids=["too-old", "unreadable", "missing"]
)
def test_release_runner_rejects_gh_before_reading_or_writing_a_release(tmp_path, version):
    done, state, original = retry(tmp_path, model.TAG, "draft", gh_version=version)
    assert all(args == ["--version"] for args in state["calls"]), state["calls"]
    assert state["release"] == original
    output = done.stdout + "\n" + done.stderr
    assert done.returncode != 0, output
    assert re.search(r"^::error::.*\b2\.102\.0\b", output, re.MULTILINE), output
    assert state["failed_step"] == 0


@pytest.mark.parametrize("version", ["2.102.0", "2.105.0"])
def test_release_runner_checks_supported_gh_then_publishes(tmp_path, version):
    done, state, original = retry(tmp_path, model.TAG, "draft", gh_version=version)
    assert state["calls"][0] == ["--version"], state["calls"]
    assert done.returncode == 0, done.stdout + done.stderr
    assert state["release"] == original | {"draft": False}


def check_diagnostic(done, release, field):
    assert done.returncode != 0
    tag, draft = release["tag_name"], release["draft"]
    if field == "file-name":
        names = " ".join(sorted(a["name"] for a in release["assets"]))
        message = model.file_error(tag, draft, f"its files are [{names}], not [")
    elif field == "file-bytes":
        wheel = next(a["name"] for a in release["assets"] if a["name"].endswith(".whl"))
        message = model.file_error(tag, draft, f"{wheel} is not the file this run attested")
    else:
        state = "a draft" if draft else "public"
        what = "notes" if field == "final-newline" else field
        message = (
            f"::error::read by its id, the GitHub release for {tag}, {state}, carries "
            f"run {model.RUN_ID}'s mark and differs from what that run wrote in its {what}"
        )
    assert message in done.stdout, done.stdout + done.stderr


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
    check_diagnostic(done, original, field)
    assert not state["writes"]
    assert state["release"] == original


@pytest.mark.parametrize("existing", ["draft", "published"])
@pytest.mark.parametrize("field", ["file-name", "file-bytes"])
def test_matching_text_still_requires_identical_files(tmp_path, existing, field):
    done, state, original = retry(tmp_path, model.TAG, existing, changed=field)
    check_diagnostic(done, original, field)
    assert not state["writes"]
    assert state["release"] == original


@pytest.mark.parametrize("field", ["notes", "title", "file-name", "file-bytes"])
@pytest.mark.parametrize("tag", [model.TAG, model.CANDIDATE], ids=["stable", "candidate"])
def test_edit_after_publication_is_reported(tmp_path, field, tag):
    done, state, original = retry(tmp_path, tag, "draft", edit_when_public=field)
    assert state["public"] == [original | {"draft": False}]
    assert state["writes_at_edit"] and state["writes"] == state["writes_at_edit"]
    expected = deepcopy(original) | {"draft": False}
    edit(expected, field)
    assert state["release"] == expected
    check_diagnostic(done, expected, field)


@pytest.mark.parametrize("commit", [None, "b2" * 20], ids=["deleted", "moved"])
@pytest.mark.parametrize(
    ("read", "writes"),
    [(1, []), (2, ["create"]), (3, ["create", "PATCH"])],
    ids=["initial", "before-publication", "before-success"],
)
def test_changed_tag_stops_at_each_commit_read(tmp_path, read, writes, commit):
    tag = model.TAG
    done, state, _ = retry(
        tmp_path,
        tag,
        None,
        extra_state={"tag_commits": [model.RUN_SHA] * (read - 1) + [commit]},
    )
    assert done.returncode != 0 and state["writes"] == writes
    calls = state["calls"][1:]  # the runner checked the CLI version first
    endpoint = f"{model.REPO_API}/commits/tags/{tag}"
    assert endpoint in calls[0] and endpoint in calls[-1]
    assert sum(endpoint in args for args in calls) == read
    assert state["tag_sha"] == commit and not state["tag_commits"]
    if read == 1:
        assert len(calls) == 1 and state["release"] is None
    else:
        release = state["release"]
        assert f"{model.REPO_API}/releases/{release['id']}" in calls[-2]
        assert release["target_commitish"] == model.RUN_SHA
        assert model.files_of(release) == model.package_files(tmp_path / "release")
        assert release["draft"] == (read == 2)
        assert state["public"] == ([] if read == 2 else [release])
    if commit is None:
        assert f"gh: No commit found for SHA: tags/{tag} (HTTP 422)" in done.stderr
        assert f"::error::the tag {tag} could not be read as a commit" in done.stdout
    else:
        assert (
            f"::error::the tag {tag} names commit {commit}, not this run's commit {model.RUN_SHA}"
            in done.stdout
        )
    assert f"The GitHub release {tag} is this run's and is published" not in done.stdout
