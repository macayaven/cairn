"""Run the release shell with independently observable listing lag and fresh reads by id."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
import test_reviewer4_release_retry_unit as four
import test_reviewer5_release_publish_unit as five
import test_reviewer6_release_notes_unit as six
from ci_shell import SYSTEM
from test_ci_release_unit import artifact

TAGS = [five.TAG, five.CANDIDATE]
RELEASES = "repos/wren/ledger/releases"


def listing(state):
    index = state["looks"]
    state["looks"] += 1
    views = state["listings"]
    view = views[index] if index < len(views) else {}
    release = deepcopy(state["release"])
    if release is None or view.get("absent"):
        return []
    if view.get("matching"):
        notes = Path(os.environ["RUNNER_TEMP"], "release", "notes.md").read_text()
        release.update(name=state["tag"], body=f"{notes}\n{five.MARK}\n")
    if view.get("draft"):
        release["draft"] = True
    if view.get("partial"):
        release["assets"] = release["assets"][:1]
    field = view.get("edit")
    if field in ("notes", "title"):
        six.edit(release, field)
    elif field == "mark":
        release["body"] = release["body"].replace(five.MARK, "")
    elif field == "prerelease":
        release["prerelease"] = not release["prerelease"]
    if view.get("duplicate"):
        return [release, release | {"id": release["id"] + 1}]
    return [release]


if __name__ == "__main__":
    sys.exit(six.serve_gh(five, sys.argv[1:], listing=listing))


def job(tmp_path, tag, existing, views, **kwargs):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sleep = bin_dir / "sleep"
    sleep.write_text('#!/bin/sh\necho "$1" >> "$RUNNER_TEMP/waits"\n')
    sleep.chmod(0o755)
    done, state, original = six.retry(
        tmp_path,
        tag,
        existing,
        gh_script=__file__,
        extra_state={"looks": 0, "listings": views},
        **kwargs,
    )
    waits = tmp_path / "waits"
    state["waits"] = waits.read_text().splitlines() if waits.exists() else []
    return done, state, original


@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("existing", ["draft", "published"])
@pytest.mark.parametrize("field", ["title", "notes"])
def test_matching_listing_cannot_authorize_an_edited_release(tmp_path, tag, existing, field):
    done, state, original = job(tmp_path, tag, existing, [{"matching": True}], changed=field)
    assert state["looks"] >= 1
    assert not state["public"], "the edited draft was published"
    assert done.returncode != 0, "the job reported success for the edited public release"
    assert state["release"] == original and not state["writes"]
    six.check_diagnostic(done, field)


@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("field", ["title", "notes"])
def test_edit_after_publish_is_seen_even_when_the_final_listing_matches(tmp_path, tag, field):
    done, state, original = job(
        tmp_path, tag, "draft", [{}, {"matching": True}], edit_when_public=field
    )
    published = original | {"draft": False}
    assert state["public"] == [published]
    edited = deepcopy(published)
    six.edit(edited, field)
    assert state["release"] == edited
    assert done.returncode != 0, "the final comparison used stale release text"
    assert state["writes_at_edit"] == state["writes"]


@pytest.mark.parametrize("after", ["create", "publish"])
@pytest.mark.parametrize("field", ["title", "notes", "mark", "prerelease"])
def test_first_conflicting_poll_stops_before_sleep_or_another_write(tmp_path, after, field):
    created = after == "create"
    view = {"draft": True, "partial": created, "edit": field}
    done, state, _ = job(tmp_path, five.CANDIDATE, None if created else "draft", [{}, view])
    assert (done.returncode != 0, state["looks"], state["waits"], state["writes"]) == (
        True,
        2,
        [],
        ["create"] if created else ["PATCH"],
    )


@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize(
    ("initial", "lag"),
    [(None, {"absent": True}), (None, {"partial": True}), ("draft", {"draft": True})],
    ids=["after-creation", "during-uploads", "after-publication"],
)
def test_listing_one_look_behind_the_jobs_write_finishes(tmp_path, tag, initial, lag):
    done, state, _ = job(tmp_path, tag, initial, [{}, lag])
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(state["waits"]) == 1
    assert state["writes"] == (["PATCH"] if initial else ["create", "PATCH"])
    assert state["public"] == [state["release"]]
    five.check_published(state["release"], tag, tmp_path / "release")


def test_discovery_refuses_two_releases_for_the_tag(tmp_path):
    done, state, original = job(tmp_path, five.TAG, "draft", [{"duplicate": True}])
    assert done.returncode != 0 and state["looks"] == 1
    assert not state["writes"] and not state["waits"]
    assert state["release"] == original


@pytest.mark.parametrize("existing", ["draft", "published"])
def test_comparisons_use_the_discovered_release_id(tmp_path, existing):
    done, state, original = job(tmp_path, five.TAG, existing, [])
    assert done.returncode == 0, done.stdout + done.stderr
    endpoint = f"{RELEASES}/{original['id']}"
    calls = state["calls"]
    looked = next(
        i for i, args in enumerate(calls) if any(a.partition("?")[0] == RELEASES for a in args)
    )
    by_id = [i for i, args in enumerate(calls) if args[0] == "api" and endpoint in args]
    reads = [i for i in by_id if "PATCH" not in calls[i]]
    assert any(i > looked for i in reads), "no fresh GET for the discovered id"
    if existing == "draft":
        (published,) = [i for i in by_id if "PATCH" in calls[i]]
        assert any(looked < i < published for i in reads)
        assert any(i > published for i in reads)


def test_cli_release_candidate_is_refused_before_any_release_request(tmp_path):
    done, state, original = six.retry(tmp_path, five.TAG, "draft", gh_version="2.102.0-rc.1")
    assert (done.returncode != 0, state["failed_step"]) == (True, 0)
    assert state["calls"] == [["--version"]]
    assert not state["writes"] and state["release"] == original


@pytest.mark.parametrize("round_number", [4, 5, 6])
def test_earlier_cli_returns_the_current_listing_entry_by_id(tmp_path, round_number):
    state = five.initial(five.TAG, artifact(tmp_path), existing="draft") | {
        "calls": 0 if round_number == 5 else [],
        "writes": [],
        "after": None,
        "publish_after": None,
        "boundaries": [],
        "unsupported": [],
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    commands = {
        4: [sys.executable, "-IB", "-c", four.GH],
        5: [sys.executable, "-IBS", five.__file__],
        6: [sys.executable, "-IB", six.__file__],
    }

    def read(endpoint):
        return subprocess.run(
            [*commands[round_number], "api", endpoint, "--method", "GET", "--jq", "."],
            env={"PATH": SYSTEM, "RELEASE_STATE": str(path)},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    response = read(RELEASES)
    assert response.returncode == 0, response.stdout
    (entry,) = json.loads(response.stdout)
    state = json.loads(path.read_text())
    state["release"].update(name="Revised title", body="Revised notes.\n")
    path.write_text(json.dumps(state))
    response = read(f"{RELEASES}/{entry['id']}")
    assert response.returncode == 0, response.stdout
    assert json.loads(response.stdout) == entry | {
        "name": "Revised title",
        "body": "Revised notes.\n",
    }
    assert read(f"{RELEASES}/99999").returncode != 0
    assert not json.loads(path.read_text())["unsupported"]
