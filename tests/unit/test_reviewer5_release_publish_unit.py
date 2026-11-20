"""Run the release job's shell against one mutable release and the actual artifact bytes.

The stand-in assumes gh 2.102.0's create-with-assets behaviour. Implicit publication
creates a draft, uploads, then publishes. An upload or publish error takes its cleanup
path, which deletes by id even if the server already committed publication. An explicit
draft stays a draft on upload error. Separate upload and publish calls have no cleanup
delete. A lost acknowledgement is a client error after the server-side change. Listing
includes drafts, a draft's assets can be downloaded, and publication preserves all ids.
The job's own jq expressions run against the model's replies.
"""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

# The job invokes this file as gh in a separate, standard-library-only process.
if __name__ != "__main__":
    import pytest
    from ci_shell import SYSTEM, run_step
    from test_ci_release_unit import artifact, shim
    from test_ci_workflow_unit import JOBS, REPO
    from test_reviewer4_release_retry_unit import DELETE, RUN_ID, TAG, release_commands


def assets(files, first_id):
    return [
        {"id": first_id + i, "name": name, "size": len(data), "data": data.hex()}
        for i, (name, data) in enumerate(sorted(files.items()))
    ]


def files_of(release):
    return {a["name"]: bytes.fromhex(a["data"]) for a in release["assets"]}


def package_files(package):
    return {p.name: p.read_bytes() for p in package.iterdir() if p.name != "notes.md"}


def create_draft(state, tag, prerelease, body):
    rid = state["next_id"]
    state["next_id"] += 1
    state["release"] = {
        "id": rid,
        "tag_name": tag,
        "draft": True,
        "prerelease": prerelease,
        "body": body,
        "assets": [],
    }


def publish(state, *, by_job):
    release = state["release"]
    if release["draft"]:
        release["draft"] = False
        state["public"].append(deepcopy(release))
        if by_job and state["fault"] == "lost-ack":
            state["fault"] = None
            return 1
    return 0


def boundary(state):
    release = state["release"]
    if release is not None and release["draft"]:
        at = state["calls"]
        state["boundaries"].append(at)
        if at == state["after"]:
            state["acted"] = True
            if state["person"] == "publish":
                publish(state, by_job=False)
            else:
                state["release"] = None


def upload(state, paths):
    release = state["release"]
    for path in paths:
        data = path.read_bytes()
        if state["fault"] == "different-bytes" and path.suffix == ".whl":
            state["fault"] = None
            data = bytes([data[0] ^ 1]) + data[1:]
        if any(a["name"] == path.name for a in release["assets"]):
            return 1
        first_id = release["id"] * 100 + len(release["assets"])
        release["assets"].extend(assets({path.name: data}, first_id))
        if state["fault"] == "upload-error":
            state["fault"] = None
            return 1
    return 0


def api_reply(value, query):
    raw = json.dumps(value)
    if query:
        return subprocess.run(["jq", "-r", query], input=raw, text=True).returncode
    print(raw)
    return 0


def public_fields(release):
    listed = []
    for asset in release["assets"]:
        fields = {k: v for k, v in asset.items() if k != "data"}
        fields["digest"] = "sha256:" + hashlib.sha256(bytes.fromhex(asset["data"])).hexdigest()
        fields["state"] = "uploaded"
        listed.append(fields)
    return release | {"assets": listed}


def api(state, args):
    parser = argparse.ArgumentParser()
    parser.add_argument("endpoint")
    parser.add_argument("-X", "--method")
    parser.add_argument("-f", "--raw-field", action="append", default=[])
    parser.add_argument("-F", "--field", action="append", default=[])
    parser.add_argument("--input", dest="input_file")
    parser.add_argument("-H", "--header", action="append")
    parser.add_argument("--paginate", "--silent", action="store_true")
    parser.add_argument("--jq", "-q")
    opt = parser.parse_args(args)
    fields = dict(f.split("=", 1) for f in opt.raw_field)
    for field in opt.field:
        key, value = field.split("=", 1)
        if value.startswith("@"):
            fields[key] = Path(value[1:]).read_text()
        else:
            fields[key] = {"true": True, "false": False}.get(value, value)
    if opt.input_file:
        fields.update(
            json.load(sys.stdin)
            if opt.input_file == "-"
            else json.loads(Path(opt.input_file).read_text())
        )
    method = (opt.method or ("POST" if fields else "GET")).upper()
    prefix = "repos/wren/ledger/releases"
    endpoint = opt.endpoint.removeprefix("https://api.github.com/")
    path = endpoint.lstrip("/").partition("?")[0].replace("{owner}/{repo}", "wren/ledger")
    if path != prefix and not path.startswith(prefix + "/"):
        raise ValueError(f"unmodelled endpoint {path}")
    suffix = path[len(prefix) :]
    release = state["release"]
    if method == "GET" and not suffix:
        return api_reply([public_fields(release)] if release else [], opt.jq)
    if method not in ("GET", "HEAD"):
        state["writes"].append(method)
    if method == "POST" and not suffix:
        if release or fields.get("tag_name") != state["tag"]:
            return 1
        create_draft(
            state, fields["tag_name"], fields.get("prerelease", False), fields.get("body", "")
        )
        rc = 0 if fields.get("draft") is True else publish(state, by_job=True)
        return rc or api_reply(public_fields(state["release"]), opt.jq)
    if not release:
        return 1
    if suffix == f"/{release['id']}/assets" and method == "GET":
        return api_reply(public_fields(release)["assets"], opt.jq)
    if suffix.startswith("/assets/") and method == "GET":
        for asset in release["assets"]:
            if suffix == f"/assets/{asset['id']}":
                sys.stdout.buffer.write(bytes.fromhex(asset["data"]))
                return 0
        return 1
    if suffix not in (f"/{release['id']}", f"/tags/{release['tag_name']}"):
        return 1
    if method == "GET":
        return api_reply(public_fields(release), opt.jq)
    if method == "DELETE":
        state["release"] = None
        return 0
    if method == "PATCH" and fields.get("draft") is False:
        if "prerelease" in fields:
            release["prerelease"] = fields["prerelease"]
        rc = publish(state, by_job=True)
        return rc or api_reply(public_fields(release), opt.jq)
    raise ValueError(f"unmodelled {method} {suffix}")


def release_command(state, command, args):
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    if command == "view":
        parser.add_argument("--json", required=True)
        parser.add_argument("--jq", "-q")
        opt = parser.parse_args(args)
        release = state["release"]
        if release is None or release["tag_name"] != opt.tag:
            return 1
        fields = {
            "tagName": opt.tag,
            "isDraft": release["draft"],
            "isPrerelease": release["prerelease"],
            "databaseId": release["id"],
            "body": release["body"],
            "assets": public_fields(release)["assets"],
        }
        return api_reply({k: fields[k] for k in opt.json.split(",")}, opt.jq)
    if command == "download":
        parser.add_argument("--dir", "-D", type=Path, default=Path("."))
        parser.add_argument("--pattern", "-p", action="append")
        parser.add_argument("--clobber", action="store_true")
        opt = parser.parse_args(args)
        release = state["release"]
        if release is None or release["tag_name"] != opt.tag:
            return 1
        opt.dir.mkdir(parents=True, exist_ok=True)
        for name, data in files_of(release).items():
            if not opt.pattern or any(Path(name).match(pattern) for pattern in opt.pattern):
                destination = opt.dir / name
                if destination.exists() and not opt.clobber:
                    return 1
                destination.write_bytes(data)
        return 0
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--title", "-t")
    parser.add_argument("--notes-file", "-F", type=Path)
    parser.add_argument("--notes", "-n", default="")
    parser.add_argument("--verify-tag", action="store_true")
    parser.add_argument("--draft", "-d")
    parser.add_argument("--prerelease", "-p")
    parser.add_argument("--latest")
    # gh's boolean switches take an optional value with '=' and otherwise mean true.
    switches = {"--draft", "--prerelease", "--latest", "-d", "-p"}
    opt = parser.parse_intermixed_args([a + "=true" if a in switches else a for a in args])
    if opt.tag != state["tag"]:
        return 1
    release = state["release"]
    state["writes"].append(command)
    if command == "create":
        if release:
            return 1
        notes = opt.notes_file.read_text() if opt.notes_file else opt.notes
        create_draft(state, opt.tag, opt.prerelease == "true", notes)
        rc = upload(state, opt.files)
        if opt.draft != "true":
            rc = rc or publish(state, by_job=True)
            if rc and opt.files:
                state["release"] = None  # gh's cleanup by id, without rechecking its public state
        return rc
    if release is None:
        return 1
    if command == "edit" and opt.draft == "false":
        if opt.prerelease is not None:
            release["prerelease"] = opt.prerelease == "true"
        return publish(state, by_job=True)
    if command == "upload":
        return upload(state, opt.files)
    if command == "delete":
        state["release"] = None
        return 0
    raise ValueError(f"unmodelled gh release {command}")


def gh(args):
    path = Path(os.environ["RELEASE_STATE"])
    state = json.loads(path.read_text())
    state["calls"] += 1
    try:
        if args[0] == "api":
            rc = api(state, args[1:])
        elif args[0] == "release":
            rc = release_command(state, args[1], args[2:])
        else:
            raise ValueError(f"unmodelled gh {args[0]}")
    except (Exception, SystemExit) as error:
        # A failure in the stand-in cannot count as a release job correctly refusing a retry.
        state["unsupported"].append([args, str(error)])
        rc = 2
    boundary(state)
    path.write_text(json.dumps(state))
    return rc


if __name__ == "__main__":
    sys.exit(gh(sys.argv[1:]))

MARK = f"<!-- cairn-ci-cd run {RUN_ID} -->"
CANDIDATE = TAG + "-rc.1"


def initial(tag, package, *, existing=None, fault=None):
    release = None
    if existing:
        release = {
            "id": 77,
            "tag_name": tag,
            "draft": existing != "published",
            "body": (package / "notes.md").read_text() + "\n" + MARK + "\n",
            "prerelease": tag == CANDIDATE,
            "assets": assets(package_files(package), 700),
        }
    return {"release": release, "tag": tag, "next_id": 88, "fault": fault, "public": []}


@pytest.fixture
def package(tmp_path):
    return artifact(tmp_path)


def attempt(folder, package, remote, *, person=None, after=None):
    folder.mkdir()
    shutil.copytree(package, folder / "release")
    bin_dir = folder / "bin"
    shim(bin_dir)
    tool = bin_dir / "gh"
    tool.write_text(f'#!/bin/sh\nexec {shlex.join([sys.executable, "-IBS", __file__])} "$@"\n')
    tool.chmod(0o755)
    state = deepcopy(remote) | {
        "calls": 0,
        "writes": [],
        "unsupported": [],
        "boundaries": [],
        "person": person,
        "after": after,
        "acted": False,
    }
    boundary(state)
    path = folder / "state.json"
    path.write_text(json.dumps(state))
    env = {
        "PATH": f"{bin_dir}:{SYSTEM}",
        "RUNNER_TEMP": str(folder),
        "RELEASE_STATE": str(path),
        "GITHUB_WORKSPACE": str(REPO),
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_REF_NAME": state["tag"],
        "GH_REPO": "wren/ledger",
        "CANDIDATE": "true" if state["tag"] == CANDIDATE else "",
    }
    for step in JOBS["release"]["steps"]:
        if "run" in step:
            done = run_step(step["run"], env=env, cwd=REPO, shell=step.get("shell"))
            if done.returncode:
                break
    state = json.loads(path.read_text())
    assert state["calls"] and not state["unsupported"], (state["unsupported"], done.stderr)
    return done, state


def check_published(release, tag, package):
    assert release and not release["draft"] and release["tag_name"] == tag
    assert release["prerelease"] == (tag == CANDIDATE) and MARK in release["body"]
    assert files_of(release) == package_files(package)


@pytest.mark.parametrize("tag", [TAG, CANDIDATE])
def test_lost_publication_acknowledgement_preserves_the_release_on_retry(tmp_path, package, tag):
    remote = initial(tag, package, fault="lost-ack")
    _, first = attempt(tmp_path / "first", package, remote)
    done, retry = attempt(tmp_path / "retry", package, first)
    assert first["fault"] is None and first["public"]
    committed = first["public"][0]
    check_published(committed, tag, package)
    assert first["release"] == committed
    assert done.returncode == 0 and retry["release"] == committed


@pytest.mark.parametrize("existing", ["draft", "published"])
@pytest.mark.parametrize("tag", [TAG, CANDIDATE])
def test_matching_retry_finishes_the_same_release(tmp_path, package, tag, existing):
    remote = initial(tag, package, existing=existing)
    done, state = attempt(tmp_path / "retry", package, remote)
    assert done.returncode == 0, done.stdout
    assert state["release"] == remote["release"] | {"draft": False}
    if existing == "published":
        assert not state["writes"]


@pytest.mark.parametrize("existing", ["draft", "published"])
@pytest.mark.parametrize("difference", ["bytes", "missing", "extra", "mark", "prerelease"])
def test_different_release_stops_without_writes(tmp_path, package, existing, difference):
    remote = initial(CANDIDATE, package, existing=existing)
    release = remote["release"]
    if difference == "bytes":
        wheel = next(a for a in release["assets"] if a["name"].endswith(".whl"))
        wheel["data"] = "ff" + wheel["data"][2:]
    elif difference == "missing":
        release["assets"].pop()
    elif difference == "extra":
        release["assets"].extend(assets({"extra.txt": b"extra"}, 800))
    elif difference == "mark":
        release["body"] = release["body"].replace(RUN_ID, RUN_ID + "0")
    else:
        release["prerelease"] = False
    done, state = attempt(tmp_path / "retry", package, remote)
    assert done.returncode != 0 and not state["writes"]
    assert state["release"] == release


def test_fresh_upload_is_checked_before_publication(tmp_path, package):
    remote = initial(TAG, package, fault="different-bytes")
    done, state = attempt(tmp_path / "first", package, remote)
    assert state["fault"] is None
    for published in state["public"]:
        assert files_of(published) == package_files(package)
    if done.returncode == 0:
        check_published(state["release"], TAG, package)


def test_upload_error_preserves_the_release_id(tmp_path, package):
    remote = initial(CANDIDATE, package, fault="upload-error")
    done, state = attempt(tmp_path / "first", package, remote)
    assert state["fault"] is None
    assert state["release"] is not None
    assert state["release"]["id"] == remote["next_id"]
    if done.returncode == 0:
        check_published(state["release"], CANDIDATE, package)


@pytest.mark.parametrize("existing", [None, "draft", "partial"], ids=["new", "complete", "partial"])
def test_person_at_each_draft_boundary_cannot_lose_a_published_release(tmp_path, package, existing):
    remote = initial(CANDIDATE, package, existing=existing)
    if existing == "partial":
        remote["release"]["assets"].pop()
    _, baseline = attempt(tmp_path / "baseline", package, remote)
    assert baseline["boundaries"], "no draft was observable between calls"
    for at in baseline["boundaries"]:
        for person in ("publish", "delete"):
            done, state = attempt(
                tmp_path / f"{person}-{at}", package, remote, person=person, after=at
            )
            assert state["acted"], (person, at)
            for published in state["public"]:
                assert state["release"] == published, (person, at)
            if done.returncode == 0:
                check_published(state["release"], CANDIDATE, package)


def test_release_job_text_has_no_delete():
    assert [(place, line) for place, line in release_commands() if DELETE.search(line)] == []
