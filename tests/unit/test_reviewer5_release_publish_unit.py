"""Run the release job's shell against mutable releases and the actual artifact bytes.

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
    from test_ci_release_unit import artifact
    from test_ci_workflow_unit import JOBS, REPO
    from test_reviewer4_release_retry_unit import (
        DELETE,
        RUN_ID,
        RUN_SHA,
        TAG,
        file_error,
        refusal,
        release_commands,
        release_tools,
    )

REPO_API = "repos/wren/ledger"
UPLOADED_AT = "2026-11-20T12:00:00Z"


def assets(files, first_id):
    return [
        {
            "id": first_id + i,
            "name": name,
            "size": len(data),
            "state": "uploaded",
            "updated_at": UPLOADED_AT,
            "data": data.hex(),
        }
        for i, (name, data) in enumerate(sorted(files.items()))
    ]


def files_of(release):
    return {a["name"]: bytes.fromhex(a["data"]) for a in release["assets"]}


def package_files(package):
    return {p.name: p.read_bytes() for p in package.iterdir() if p.name != "notes.md"}


def create_draft(state, tag, prerelease, body, title, target):
    rid = state["next_id"]
    state["next_id"] += 1
    state["release"] = {
        "id": rid,
        "tag_name": tag,
        "target_commitish": target,
        "name": title,
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
        at = len(state["calls"])
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


def asset_fields(asset):
    fields = {k: v for k, v in asset.items() if k != "data"}
    fields["digest"] = "sha256:" + hashlib.sha256(bytes.fromhex(asset["data"])).hexdigest()
    return fields


def public_fields(release):
    return release | {"assets": [asset_fields(a) for a in release["assets"]]}


def releases(state):
    # Other drafts can be found first by tag even when discovery only lists the run's draft.
    others = state["other_releases"]
    return [*others, state["release"]] if state["release"] is not None else others


def release_for_tag(state, tag):
    return next((r for r in releases(state) if r["tag_name"] == tag), None)


def not_found():
    print("gh: Not Found (HTTP 404)", file=sys.stderr)
    return 1


def tag_reply(state, opt):
    if state["tag_commits"]:
        state["tag_sha"] = state["tag_commits"].pop(0)
    if state["tag_sha"] is None:
        print(f"gh: No commit found for SHA: tags/{state['tag']} (HTTP 422)", file=sys.stderr)
        return 1
    if "Accept: application/vnd.github.sha" in opt.header:
        print(state["tag_sha"])
        return 0
    return api_reply({"sha": state["tag_sha"]}, opt.jq)


def asset_reply(state, asset_id, opt):
    for entry in releases(state):
        for asset in entry["assets"]:
            if str(asset["id"]) == asset_id:
                if "Accept: application/octet-stream" not in opt.header:
                    return api_reply(asset_fields(asset), opt.jq)
                data = bytes.fromhex(asset["data"])
                if b"\x1b" in data and not opt.allow_escape_sequences:
                    return 1
                sys.stdout.buffer.write(data)
                return 0
    return not_found()


def api(state, args, *, listing=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("endpoint")
    parser.add_argument("-X", "--method")
    parser.add_argument("-f", "--raw-field", action="append", default=[])
    parser.add_argument("-F", "--field", action="append", default=[])
    parser.add_argument("--input", dest="input_file")
    parser.add_argument("-H", "--header", action="append", default=[])
    parser.add_argument("--paginate", action="store_true")
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--allow-escape-sequences", action="store_true")
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
    prefix = f"{REPO_API}/releases"
    endpoint = opt.endpoint.removeprefix("https://api.github.com/")
    path = endpoint.lstrip("/").partition("?")[0].replace("{owner}/{repo}", "wren/ledger")
    if method == "GET" and path == f"{REPO_API}/commits/tags/{state['tag']}":
        return tag_reply(state, opt)
    if path != prefix and not path.startswith(prefix + "/"):
        raise ValueError(f"unmodelled endpoint {path}")
    suffix = path[len(prefix) :]
    release = state["release"]
    if method == "GET" and not suffix:
        shown = (
            listing(state)
            if listing
            else [r for r in releases(state) if r["id"] not in state["unlisted_ids"]]
        )
        return api_reply([public_fields(entry) for entry in shown], opt.jq)
    if method not in ("GET", "HEAD"):
        state["writes"].append(method)
    if method == "POST" and not suffix:
        if release or fields.get("tag_name") != state["tag"]:
            return 1
        create_draft(
            state,
            fields["tag_name"],
            fields.get("prerelease", False),
            fields.get("body", ""),
            fields.get("name"),
            fields.get("target_commitish"),
        )
        rc = 0 if fields.get("draft") is True else publish(state, by_job=True)
        return rc or api_reply(public_fields(state["release"]), opt.jq)
    if suffix.startswith("/assets/") and method == "GET":
        return asset_reply(state, suffix.removeprefix("/assets/"), opt)
    if method == "GET":
        for entry in releases(state):
            if suffix == f"/{entry['id']}/assets":
                return api_reply(public_fields(entry)["assets"], opt.jq)
            if suffix in (f"/{entry['id']}", f"/tags/{entry['tag_name']}"):
                return api_reply(public_fields(entry), opt.jq)
        return not_found()
    if not release or suffix != f"/{release['id']}":
        return not_found()
    if method == "DELETE":
        state["release"] = None
        return 0
    if method == "PATCH" and fields.get("draft") is False:
        state["patches"].append(fields)
        if fields.get("make_latest", "true") == "true":
            state["latest_id"] = release["id"]
        if "prerelease" in fields:
            release["prerelease"] = fields["prerelease"]
        rc = publish(state, by_job=True)
        return rc if rc or opt.silent else api_reply(public_fields(release), opt.jq)
    raise ValueError(f"unmodelled {method} {suffix}")


def release_command(state, command, args):
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    if command == "view":
        parser.add_argument("--json", required=True)
        parser.add_argument("--jq", "-q")
        opt = parser.parse_args(args)
        release = release_for_tag(state, opt.tag)
        if release is None:
            return 1
        fields = {
            "tagName": opt.tag,
            "name": release["name"],
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
        release = release_for_tag(state, opt.tag)
        if release is None:
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
    parser.add_argument("--target")
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
        if release or (opt.verify_tag and state["tag_sha"] is None):
            return 1
        notes = opt.notes_file.read_text() if opt.notes_file else opt.notes
        create_draft(state, opt.tag, opt.prerelease == "true", notes, opt.title, opt.target)
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
    if args == ["--version"]:
        print("gh version 2.102.0 (2026-01-01)")
        print("https://github.com/cli/cli/releases/tag/v2.102.0")
        return 0
    path = Path(os.environ["RELEASE_STATE"])
    state = json.loads(path.read_text())
    state["calls"].append(args)
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
            "name": tag,
            "draft": existing != "published",
            "body": (package / "notes.md").read_text() + "\n" + MARK + "\n",
            "prerelease": tag == CANDIDATE,
            "assets": assets(package_files(package), 700),
        }
    return {
        "release": release,
        "other_releases": [],
        "unlisted_ids": [],
        "tag": tag,
        "tag_sha": RUN_SHA,
        "tag_commits": [],
        "next_id": 88,
        "fault": fault,
        "public": [],
        "latest_id": None,
        "patches": [],
    }


@pytest.fixture
def package(tmp_path):
    return artifact(tmp_path)


def attempt(folder, package, remote, *, person=None, after=None):
    folder.mkdir()
    shutil.copytree(package, folder / "release")
    bin_dir = folder / "bin"
    release_tools(bin_dir)
    tool = bin_dir / "gh"
    tool.write_text(f'#!/bin/sh\nexec {shlex.join([sys.executable, "-IBS", __file__])} "$@"\n')
    tool.chmod(0o755)
    state = deepcopy(remote) | {
        "calls": [],
        "writes": [],
        "patches": [],
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
        "GITHUB_SHA": RUN_SHA,
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
    failed, first = attempt(tmp_path / "first", package, remote)
    done, retry = attempt(tmp_path / "retry", package, first)
    assert failed.returncode != 0 and first["fault"] is None
    assert first["writes"] == ["create", "PATCH"]
    (committed,) = first["public"]
    check_published(committed, tag, package)
    assert committed["target_commitish"] == RUN_SHA
    assert first["release"] == committed
    assert done.returncode == 0 and retry["release"] == committed
    assert not retry["writes"] and not retry["patches"]


@pytest.mark.parametrize("existing", ["draft", "published"])
@pytest.mark.parametrize("tag", [TAG, CANDIDATE])
def test_matching_retry_finishes_the_same_release(tmp_path, package, tag, existing):
    remote = initial(tag, package, existing=existing)
    done, state = attempt(tmp_path / "retry", package, remote)
    assert done.returncode == 0, done.stdout
    assert state["release"] == remote["release"] | {"draft": False}
    if existing == "published":
        assert not state["writes"] and not state["patches"]
    else:
        latest = "false" if tag == CANDIDATE else "true"
        assert state["writes"] == ["PATCH"]
        assert state["patches"] == [{"draft": False, "make_latest": latest}]


@pytest.mark.parametrize("existing", ["draft", "published"])
@pytest.mark.parametrize(
    "difference", ["bytes", "missing", "extra", "mark", "prerelease", "size", "unfinished-upload"]
)
def test_different_release_stops_without_writes(tmp_path, package, existing, difference):
    remote = initial(CANDIDATE, package, existing=existing)
    release = remote["release"]
    wheel = next(a for a in release["assets"] if a["name"].endswith(".whl"))
    if difference == "bytes":
        wheel["data"] = "ff" + wheel["data"][2:]
    elif difference == "size":
        wheel["data"] = wheel["data"][2:]
        wheel["size"] -= 1
    elif difference == "unfinished-upload":
        wheel["state"] = "starter"
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
    if difference == "mark":
        assert refusal(CANDIDATE, f"its text does not carry run {RUN_ID}'s mark") in done.stdout
    elif difference == "prerelease":
        assert refusal(CANDIDATE, "its pre-release flag is false, not true") in done.stdout
    elif difference in ("missing", "extra"):
        names = " ".join(sorted(a["name"] for a in release["assets"]))
        assert file_error(CANDIDATE, release["draft"], f"its files are [{names}], not [") in (
            done.stdout
        )
    else:
        reason = f"{wheel['name']} is not the file this run attested"
        assert file_error(CANDIDATE, release["draft"], reason) in done.stdout
        endpoint = f"{REPO_API}/releases/assets/{wheel['id']}"
        fetched = any(endpoint in args for args in state["calls"])
        assert fetched == (difference == "bytes")


def test_fresh_upload_is_checked_before_publication(tmp_path, package):
    remote = initial(TAG, package, fault="different-bytes")
    done, state = attempt(tmp_path / "first", package, remote)
    assert done.returncode != 0 and state["fault"] is None
    assert state["writes"] == ["create"] and not state["public"] and state["release"]["draft"]
    wheel = next(package.glob("*.whl"))
    assert file_error(TAG, True, f"{wheel.name} is not the file this run attested") in done.stdout


def test_upload_error_preserves_the_release_id(tmp_path, package):
    remote = initial(CANDIDATE, package, fault="upload-error")
    done, state = attempt(tmp_path / "first", package, remote)
    assert done.returncode != 0 and state["fault"] is None
    assert state["writes"] == ["create"] and not state["public"]
    release = state["release"]
    assert release["id"] == remote["next_id"] and release["draft"]
    assert files_of(release).items() < package_files(package).items()
    assert release["assets"]


def test_another_drafts_matching_bytes_cannot_excuse_the_discovered_draft(tmp_path, package):
    remote = initial(TAG, package, existing="draft")
    judged = remote["release"]
    other = judged | {"id": 91, "assets": assets(package_files(package), 9100)}
    remote["other_releases"] = [other]
    remote["unlisted_ids"] = [other["id"]]
    wheel = next(a for a in judged["assets"] if a["name"].endswith(".whl"))
    wheel["data"] = "1b" + wheel["data"][2:]  # same size, with an escape byte in the raw reply
    assert files_of(release_for_tag(remote, TAG)) == package_files(package)

    done, state = attempt(tmp_path / "retry", package, remote)
    assert done.returncode != 0 and not state["writes"] and not state["public"]
    assert state["release"] == judged and state["other_releases"] == [other]
    assert file_error(TAG, True, f"{wheel['name']} is not the file this run attested") in (
        done.stdout
    )
    assert f"{REPO_API}/releases/assets/{wheel['id']}" in state["calls"][-1]


def published_version(tag, release_id):
    return {
        "id": release_id,
        "tag_name": tag,
        "name": tag,
        "draft": False,
        "prerelease": False,
        "body": "",
        "assets": [],
    }


@pytest.mark.parametrize("existing", [None, "draft"], ids=["new", "retry"])
def test_backport_keeps_the_higher_stable_release_latest(tmp_path, package, existing):
    higher = published_version("v1.10.0", 92)
    remote = initial(TAG, package, existing=existing)
    remote.update(other_releases=[higher], latest_id=higher["id"])
    done, state = attempt(tmp_path / "backport", package, remote)
    assert done.returncode == 0, done.stdout + done.stderr
    check_published(state["release"], TAG, package)
    assert state["writes"] == (["PATCH"] if existing else ["create", "PATCH"])
    assert state["patches"] == [{"draft": False, "make_latest": "false"}]
    assert state["latest_id"] == higher["id"] and state["other_releases"] == [higher]


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
