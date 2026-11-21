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
    others = [] if view.get("hide_others") else state["other_releases"]
    release = deepcopy(state["release"])
    if release is None or view.get("absent"):
        return others
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
        return [release, release | {"id": release["id"] + 1}, *others]
    return [release, *others]


def change_asset_after_reply(state, args):
    change = state.get("asset_change")
    if not change or args[0] != "api" or "PATCH" in args:
        return
    release = state["release"]
    if release["draft"] != change["draft"]:
        return
    wheel = next(a for a in release["assets"] if a["name"].endswith(".whl"))
    endpoint = (
        f"{RELEASES}/{release['id']}"
        if change.get("before_fetch")
        else f"{RELEASES}/assets/{wheel['id']}"
    )
    if endpoint in args:
        # The release or asset reply just sent describes the original wheel.
        wheel.update(change["fields"])
        state["asset_change"] = None


if __name__ == "__main__":
    sys.exit(
        six.serve_gh(five, sys.argv[1:], listing=listing, after_reply=change_asset_after_reply)
    )


def job(tmp_path, tag, existing, views, *, extra_state=None, **kwargs):
    return six.retry(
        tmp_path,
        tag,
        existing,
        gh_script=__file__,
        extra_state={"looks": 0, "listings": views} | (extra_state or {}),
        **kwargs,
    )


@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("existing", ["draft", "published"])
@pytest.mark.parametrize("field", ["title", "notes"])
def test_matching_listing_cannot_authorize_an_edited_release(tmp_path, tag, existing, field):
    done, state, original = job(tmp_path, tag, existing, [{"matching": True}], changed=field)
    assert state["looks"] >= 1
    assert not state["public"], "the edited draft was published"
    assert done.returncode != 0, "the job reported success for the edited public release"
    assert state["release"] == original and not state["writes"]
    six.check_diagnostic(done, original, field)


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
    six.check_diagnostic(done, edited, field)
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
    look, read = state["calls"][-2:]
    assert any(arg.partition("?")[0] == RELEASES for arg in look)
    assert f"{RELEASES}/{state['release']['id']}" in read
    observed = "a draft" if created else "public"
    assert (
        f"::error::GitHub's listing shows the release for {five.CANDIDATE} without this run's "
        f"mark, pre-release flag, title or notes, while read by its id it is {observed} "
        "and has all four as this run wrote them"
    ) in done.stdout


@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize(
    ("initial", "lag"),
    [(None, {"absent": True}), (None, {"partial": True}), ("draft", {"draft": True})],
    ids=["after-creation", "during-uploads", "after-publication"],
)
def test_listing_one_look_behind_the_jobs_write_finishes(tmp_path, tag, initial, lag):
    done, state, _ = job(tmp_path, tag, initial, [{}, lag])
    assert done.returncode == 0, done.stdout + done.stderr
    assert state["waits"] == ["3"]
    assert state["writes"] == (["PATCH"] if initial else ["create", "PATCH"])
    assert state["public"] == [state["release"]]
    five.check_published(state["release"], tag, tmp_path / "release")


def test_exhausted_poll_reports_what_the_listing_still_shows(tmp_path):
    views = [{}] + [{"draft": True}] * 10
    done, state, original = job(tmp_path, five.TAG, "draft", views)
    assert done.returncode != 0 and state["writes"] == ["PATCH"]
    assert state["looks"] == 11 and state["waits"] == ["3"] * 9
    assert state["release"] == original | {"draft": False}
    assert (
        f"::error::GitHub's listing, at this job's last look, does not list the release for "
        f"{five.TAG} as this job has just left it"
    ) in done.stdout


def test_discovery_refuses_two_releases_for_the_tag(tmp_path):
    done, state, original = job(tmp_path, five.TAG, "draft", [{"duplicate": True}])
    assert done.returncode != 0 and state["looks"] == 1
    assert not state["writes"] and not state["waits"]
    assert state["release"] == original
    assert f"::error::GitHub's listing shows 2 releases for {five.TAG}," in done.stdout


@pytest.mark.parametrize("existing", ["draft", "published"])
def test_comparisons_use_the_discovered_release_id(tmp_path, existing):
    done, state, original = job(tmp_path, five.TAG, existing, [])
    assert done.returncode == 0, done.stdout + done.stderr
    endpoint = f"{RELEASES}/{original['id']}"
    tag_read = f"{five.REPO_API}/commits/tags/{five.TAG}"
    first, *calls = state["calls"][1:]
    assert tag_read in first
    patches = [i for i, args in enumerate(calls) if "PATCH" in args]
    if existing == "draft":
        (published,) = patches
        assert endpoint in calls[published]
        comparisons = [calls[:published], calls[published + 1 :]]
    else:
        assert not patches
        comparisons = [calls]
    owned = sorted(f"{RELEASES}/assets/{a['id']}" for a in original["assets"])
    for look, before, *fetches, after, last in comparisons:
        assert any(arg.partition("?")[0] == RELEASES for arg in look)
        assert endpoint in before and endpoint in after
        assert tag_read in last
        assert all("--allow-escape-sequences" in args for args in fetches)
        fetched = [arg for args in fetches for arg in args if arg.startswith(f"{RELEASES}/assets/")]
        assert sorted(fetched) == owned and len(fetches) == len(owned)


@pytest.mark.parametrize("draft", [True, False], ids=["before-publication", "before-success"])
@pytest.mark.parametrize(
    ("fields", "before_fetch"),
    [
        ({"id": 9901}, True),
        ({"id": 9901}, False),
        ({"updated_at": "2026-11-20T12:00:01Z"}, False),
    ],
    ids=["replaced-before-fetch", "replaced-after-fetch", "changed-update-time"],
)
def test_asset_change_between_the_reads_stops_the_job(tmp_path, draft, fields, before_fetch):
    change = {"draft": draft, "fields": fields, "before_fetch": before_fetch}
    done, state, original = job(
        tmp_path,
        five.TAG,
        "draft",
        [],
        extra_state={"asset_change": change},
    )
    assert done.returncode != 0 and state["asset_change"] is None
    assert state["writes"] == ([] if draft else ["PATCH"])
    assert state["public"] == ([] if draft else [original | {"draft": False}])
    expected = deepcopy(original) | {"draft": draft}
    wheel = next(a for a in expected["assets"] if a["name"].endswith(".whl"))
    old_endpoint = f"{RELEASES}/assets/{wheel['id']}"
    wheel.update(fields)
    assert state["release"] == expected
    observed = "a draft" if draft else "public"
    if before_fetch:
        assert old_endpoint in state["calls"][-1]
        assert "gh: Not Found (HTTP 404)" in done.stderr
        message = (
            f"::error::the files of the GitHub release for {five.TAG}, {observed} as read by "
            "its id, could not be downloaded"
        )
    else:
        assert f"{RELEASES}/{original['id']}" in state["calls"][-1]
        message = (
            f"::error::the GitHub release for {five.TAG}, {observed}, changed between this job's "
            "two reads of it by its id, around the download of its files"
        )
    assert message in done.stdout, done.stdout + done.stderr


def test_higher_stable_first_listed_after_publication_is_reported(tmp_path):
    higher = five.published_version("v1.10.0", 92)
    done, state, original = job(
        tmp_path,
        five.TAG,
        "draft",
        [{"hide_others": True}, {}],
        extra_state={"other_releases": [higher], "latest_id": higher["id"]},
    )
    assert done.returncode != 0 and state["writes"] == ["PATCH"]
    assert state["patches"] == [{"draft": False, "make_latest": "true"}]
    assert state["public"] == [original | {"draft": False}]
    assert state["latest_id"] == original["id"] and state["other_releases"] == [higher]
    assert f"{five.REPO_API}/commits/tags/{five.TAG}" in state["calls"][-1]
    assert (
        f"::error::the GitHub release for {five.TAG}, read by its id, is this run's and public,"
        in done.stdout
    )
    assert (
        f"GitHub's listing after the publication shows {higher['tag_name']} published, "
        "a higher stable version"
    ) in done.stdout


def test_cli_release_candidate_is_refused_before_any_release_request(tmp_path):
    done, state, original = six.retry(tmp_path, five.TAG, "draft", gh_version="2.102.0-rc.1")
    assert (done.returncode != 0, state["failed_step"]) == (True, 0)
    assert state["calls"] == [["--version"]]
    assert not state["writes"] and state["release"] == original


@pytest.mark.parametrize("round_number", [4, 5, 6])
def test_earlier_cli_returns_the_current_listing_entry_by_id(tmp_path, round_number):
    state = five.initial(five.TAG, artifact(tmp_path), existing="draft") | {
        "calls": [],
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
