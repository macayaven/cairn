"""The tester's findings in job `release`'s step "Create the release", restated to the
implementer's decisions of round rl8 (7e89e04e): each case is replayed as a schedule written in
words. A finding a decision fixes is held as fixed (the test asserts the invariant holds); a
finding the README states as a limit is pinned by the README's sentence that states it (the
test asserts the sentence is there and the step does what it says); a finding neither fixes nor
states fails, with its trace in the message. Made-up repository, run, commits and files.
"""

from __future__ import annotations

import re

import pytest
from test_rt2_explore import LAGS, Execution, check, execute, lags, render
from test_rt2_github import (
    KINDS,
    OTHER_SHA,
    REPO_ROOT,
    RUN_SHA,
    TAGS,
    Config,
    Script,
    current_run,
)


def replay(steps, tag=TAGS["release"], **config) -> Execution:
    return execute(current_run(), Config(tag=tag, **config), scheduler=Script(steps))


def trace(ex: Execution) -> str:
    violations, notes = check(ex)
    return "\n".join([*map(str, violations), *notes, *render(ex)])


def broken(ex: Execution, invariant: str) -> list[str]:
    return [str(v) for v in check(ex)[0] if v.invariant == invariant]


def readme_says(*fragments: str) -> None:
    """The README, its lines joined, holds each fragment as written."""
    text = re.sub(r"\s+", " ", (REPO_ROOT / "README.md").read_text())
    for fragment in fragments:
        assert fragment in text, f"the README no longer says: {fragment}"


def public(ex: Execution) -> list[int]:
    return [r["id"] for r in ex.hub.releases_for(ex.hub.truth) if not r["draft"]]


# --- decision 1: the tag is judged (held as fixed) ----------------------------------------------


@pytest.mark.parametrize("tag", TAGS.values())
def test_held_a_tag_deleted_before_the_publication_stops_the_job_and_nothing_is_published(tag):
    """Was: a person deletes the git tag after the draft is judged; the publication made GitHub
    create the tag on the default branch's head and the job ended well. Now the job reads the
    tag as its last request before the publication and stops: nothing is published, no tag is
    made, and the line says the tag could not be read as a commit."""
    ex = replay([(r"person, before GET commits/tags", "deletes the git tag", 2)], tag=tag)
    assert ex.attempts[0].code != 0, trace(ex)
    assert not public(ex) and tag not in ex.hub.truth["tags"], trace(ex)
    assert not check(ex)[0], trace(ex)


def test_held_a_re_run_after_the_tag_was_deleted_publishes_nothing():
    """Was: attempt 1 stops on a read by id that got no answer; the person deletes the tag to
    withdraw the version; a re-run published the draft and made the tag on the default branch's
    head. Now the re-run's first request reads the tag and stops, writing nothing."""
    ex = replay(
        [
            (r"fate of GET releases/88$", "fails", 2),
            (r"person, before GET commits/tags", "deletes the git tag"),
        ],
        follow_advice=False,
    )
    assert ex.attempts[1].code != 0, trace(ex)
    assert TAGS["release"] not in ex.hub.truth["tags"] and not public(ex), trace(ex)
    assert not check(ex)[0], trace(ex)


@pytest.mark.parametrize("tag", TAGS.values())
def test_held_a_tag_moved_before_the_job_starts_stops_it_before_any_write(tag):
    """Was: a forced push moved the tag before the step's first request; the job made, compared
    and published its draft on the moved tag and ended well. Now its first request reads the
    tag, names the other commit and stops before writing."""
    ex = replay([(r"person, before GET commits/tags", "moves the git tag")], tag=tag)
    assert ex.hub.truth["tags"][tag] == OTHER_SHA
    assert ex.attempts[0].code != 0, trace(ex)
    assert not ex.hub.releases_for(ex.hub.truth), trace(ex)
    assert not check(ex)[0], trace(ex)


@pytest.mark.parametrize("tag", TAGS.values())
def test_held_a_draft_is_made_with_the_run_s_commit_as_its_target(tag):
    ex = replay([], tag=tag)
    (r,) = ex.hub.releases_for(ex.hub.truth)
    assert r["target_commitish"] == RUN_SHA and not r["draft"], trace(ex)


@pytest.mark.parametrize("tag", TAGS.values())
def test_held_an_annotated_tag_reads_as_its_commit(tag):
    """GET commits/tags/T resolves the ref to a commit, so an annotated tag (an object of its
    own naming the commit) answers the commit as a lightweight one does."""
    ex = replay([], tag=tag, annotated=True)
    assert [a.code for a in ex.attempts] == [0, 0, 0] and not check(ex)[0], trace(ex)


@pytest.mark.parametrize("tag", TAGS.values())
def test_pinned_a_tag_deleted_after_its_last_read_is_made_again_on_the_run_s_commit(tag):
    """The README: a person who deletes the tag between the last read and the publication
    'makes GitHub create it again, meant to be on the run's commit ... which is not measured'.
    The model makes it from target_commitish, the run's commit (GitHub's REST docs)."""
    readme_says("makes GitHub create it again, meant to be on the run's commit")
    ex = replay([(r"person, before PATCH", "deletes the git tag")], tag=tag)
    assert ex.hub.truth["tags"].get(tag) == RUN_SHA, trace(ex)
    assert ex.attempts[0].code == 0 and not check(ex)[0], trace(ex)


# --- decision 2: the bytes compared are the judged release's own (held as fixed) ----------------


@pytest.mark.parametrize("tag", TAGS.values())
@pytest.mark.parametrize("nth", [2, 4])
def test_held_a_file_replaced_after_its_download_and_before_the_next_read_stops_the_job(tag, nth):
    """Was: a wheel replaced (same name) between the last download and the last read by id
    passed that read, which judged names only, and the job ended well. Now the read after the
    fetches must equal the read before them (asset ids, sizes, update times): before the
    publication (2nd read) nothing is published; after it (4th) the job does not succeed."""
    ex = replay([(r"person, before GET releases/88$", "replaces the wheel", nth)], tag=tag)
    assert ex.attempts[0].code != 0, trace(ex)
    assert "changed between this job's two reads" in ex.attempts[0].errors[0], trace(ex)
    if nth == 2:  # nothing published in that attempt
        assert not [e for e in ex.hub.events if e.attempt == 1 and e.kind == "patch"], trace(ex)
    assert not check(ex)[0], trace(ex)


def test_held_a_re_run_never_publishes_a_cut_draft_it_did_not_fetch_from():
    """Was (switch: the listing behind across re-runs): the re-run compared the bytes of its own
    new draft, found by tag, and published the cut one by id. Now every file is fetched by the
    asset id the judged release's read gives, once that read shows it uploaded and of the size
    of the run's file."""
    ex = replay(
        [(r"fate of upload \S+\.whl to 88", "cut")],
        persist_lag=True,
        lag=dict.fromkeys(KINDS, 1),
    )
    assert not broken(ex, "2") and not broken(ex, "3"), trace(ex)


# --- decision 4: the publication says latest or not (held as fixed) ----------------------------


def test_held_a_stable_backport_below_the_highest_leaves_github_s_latest_where_it_is():
    """Was: the publication sent draft=false alone, so GitHub's make_latest default (true for a
    release newly published) made the backport v1.2.3 Latest over v1.3.0."""
    ex = replay([], existing=("v1.3.0",))
    assert ex.attempts[0].code == 0, trace(ex)
    assert ex.hub.truth["latest"] == 40, trace(ex)
    assert not check(ex)[0], trace(ex)


# --- decision 1 (rl8, 1926b04a): Latest checked by the look after the publication -------------

HAND = (
    "A release published by hand, or one GitHub's listing shows only after the job's last look, "
    "can still leave GitHub's latest on a lower version, and the job cannot see it."
)
LATER = ("person, before GET commits/tags", "publishes v1.3.0 by hand", 2)


@pytest.mark.parametrize(
    "gap", [LATER, ("person, before GET asset", "publishes v1.3.0 by hand", 1)]
)
def test_held_a_higher_version_published_after_the_last_look_stops_the_job_on_its_own_line(gap):
    """Was: another tag's run published v1.3.0 after this job's last look and before its
    publication; the publication said latest and the job ended well with GitHub's Latest on
    v1.2.3. Now job `release` runs one at a time across the repository (no other tag's job
    publishes meanwhile), and a person who does it by hand is caught by the look after the
    publication: the job stops with the lost-latest line, the person sets v1.3.0 as Latest as
    it says, and the re-run finds the release published and succeeds."""
    ex = replay([gap], existing=("v1.2.2",), by_hand=("v1.3.0",))
    assert "GitHub's listing after the publication shows v1.3.0" in ex.attempts[0].errors[0], trace(
        ex
    )
    assert [a.code for a in ex.attempts][1:] == [0, 0], trace(ex)
    assert ex.hub.truth["releases"][ex.hub.truth["latest"]]["tag_name"] == "v1.3.0", trace(ex)
    assert not check(ex)[0], trace(ex)


def test_held_a_higher_version_missing_from_the_look_before_the_publication_is_caught_after():
    """The listing one look behind a person: v1.3.0, published by hand before the look that
    follows the creation, is missing from it, so the publication says latest; the look after the
    publication lists it and the job stops on the lost-latest line."""
    readme_says(
        "a higher version published seconds before the job's last look can be missing from it, "
        "and a backport is then said to be the latest"
    )
    ex = replay(
        [("person, before GET releases page 1", "publishes v1.3.0 by hand", 2)],
        existing=("v1.2.2",),
        by_hand=("v1.3.0",),
        lag=LAGS["person one behind"],
    )
    assert "after the publication shows v1.3.0" in ex.attempts[0].errors[0], trace(ex)
    assert not check(ex)[0], trace(ex)


def test_pinned_a_higher_version_the_listing_shows_only_later_leaves_latest_lower():
    """The stated limit: v1.3.0, published by hand in the moment before the publication, is
    missing from the look after it too (the listing nine looks behind a person); the job ends
    well and GitHub's Latest stays on v1.2.3."""
    readme_says(HAND)
    ex = replay([LATER], existing=("v1.2.2",), by_hand=("v1.3.0",), lag=LAGS["person nine behind"])
    assert ex.attempts[0].code == 0 and ex.hub.truth["latest"] == 88, trace(ex)
    assert any(n.startswith("L (stated") for n in check(ex)[1]), trace(ex)
    assert not check(ex)[0], trace(ex)


def test_pinned_a_re_run_after_the_lost_latest_stop_succeeds_without_looking_at_latest():
    """The stated limit (the coordinator's ruling on the tester's finding): the lost-latest line
    says to set v1.3.0 as Latest and then re-run. A person who only re-runs gets a run that finds
    the release published and sends no publication, so Latest is not checked: it ends well with
    GitHub's Latest on v1.2.3, below v1.3.0. Closing it needs a read of GitHub's current Latest,
    recorded for the next version."""
    readme_says(
        "a re-run of the job after this stop sends no publication and succeeds without looking "
        "at GitHub's latest again, so re-running does not repair it"
    )
    ex = replay(
        [LATER, ("after attempt 1, the person", "only re-runs")],
        existing=("v1.2.2",),
        by_hand=("v1.3.0",),
    )
    assert "after the publication shows v1.3.0" in ex.attempts[0].errors[0], trace(ex)
    assert ex.attempts[1].code == 0 and ex.hub.truth["latest"] == 88, trace(ex)
    violations, notes = check(ex)
    assert any("re-running does not repair it" in n for n in notes), trace(ex)
    assert not violations, trace(ex)


# --- decision 6: a second release missing from the listing (pinned limits) ----------------------

SECOND = (
    "one a person makes while the job runs, or the draft of an earlier attempt just before a "
    "re-run, can be missing from the listing, which can be seconds behind a write, so the job "
    "does not see it and can make or publish its own release beside it: that is a stated limit"
)


def test_pinned_a_re_run_whose_first_look_is_behind_makes_a_second_draft():
    """Attempt 1's creation is done and its reply lost; the re-run's first look, one look behind
    (the switch: the listing still behind when the re-run starts), shows nothing, and the step
    makes a second draft beside the earlier attempt's."""
    readme_says(SECOND)
    ex = replay([(r"fate of POST", "reply lost")], persist_lag=True, lag=dict.fromkeys(KINDS, 1))
    assert any("6 (stated limit)" in n and "an earlier attempt" in n for n in check(ex)[1]), trace(
        ex
    )


def test_pinned_a_draft_a_person_makes_during_the_job_is_not_seen_and_the_job_ends_well():
    readme_says(SECOND)
    ex = replay(
        [(r"person, before GET asset", "second release for the tag, a draft")],
        lag=LAGS["person one behind"],
    )
    assert ex.attempts[0].code == 0, trace(ex)
    assert any("3 (stated limit)" in n for n in check(ex)[1]), trace(ex)


def test_pinned_the_job_makes_its_draft_beside_a_person_s_draft_its_first_look_missed():
    readme_says(SECOND)
    ex = replay(
        [(r"person, before GET releases page 1", "second release for the tag, a draft")],
        lag=LAGS["person one behind"],
    )
    assert any("6 (stated limit)" in n and "a person" in n for n in check(ex)[1]), trace(ex)


# --- decision 2 (rl8, 1926b04a): each line says where it read what it states (held) ------------


def test_held_a_title_edited_in_the_stated_race_is_reported_as_public_from_the_read_by_id():
    """Was: a title edited between the last read by id and the publication; the look after the
    publication, its listing one look behind the publication, judged the row and the line said
    'a draft' of a public release. Now a row that does not fit is read by its id at once, and
    the line, written from that read, says 'public'."""
    ex = replay([(r"person, before PATCH", "edits the title")], lag=lags(publish=1))
    assert "read by its id, the GitHub release for v1.2.3, public," in ex.attempts[0].errors[0], (
        trace(ex)
    )
    assert not check(ex)[0], trace(ex)


def test_held_a_title_put_back_while_the_listing_still_shows_it_is_said_to_be_the_listing_s():
    """Was (switch: the listing behind across re-runs): the person puts the title back as the
    line says and re-runs at once; the listing still shows the edit and the line said the
    release differs. Now the row is read by its id, which shows the run's title, and the line
    says the listing is behind."""
    ex = replay(
        [(r"person, before PATCH", "edits the title")],
        persist_lag=True,
        lag=lags(person=2),
    )
    lines = [a.errors[0] for a in ex.attempts if a.errors]
    assert any("GitHub's listing shows the release for v1.2.3 without" in x for x in lines), trace(
        ex
    )
    assert not broken(ex, "5"), trace(ex)


# --- decision 3 (rl8, 1926b04a): the draft that does not hold the run's files (held) -----------


def test_held_a_draft_a_person_changed_in_the_first_attempt_says_what_it_holds():
    """Was: a wheel replaced (same size) before the first read by id stopped attempt 1 on 'is a
    draft that an earlier attempt ... left unfinished', though this attempt made the draft. Now
    the line says, read by its id, that it is this run's draft and does not hold exactly the
    files this run attested, and which."""
    ex = replay([(r"person, before GET releases/88$", "replaces the wheel")])
    line = ex.attempts[0].errors[0]
    assert "is this run's draft and does not hold exactly the files this run attested" in line, (
        trace(ex)
    )
    assert "earlier attempt" not in line
    assert not check(ex)[0], trace(ex)


# --- decision 4 (rl8, 1926b04a): a tag moved in the moment before the publication (pinned) ------


@pytest.mark.parametrize("tag", TAGS.values())
def test_pinned_a_tag_moved_after_its_last_read_is_published_and_the_job_stops_on_it(tag):
    readme_says(
        "A person who moves the tag in that moment has the release published, on the moved tag, "
        "and the job's read of the tag after the publication then stops it with the line of a "
        "moved tag"
    )
    ex = replay([(r"person, before PATCH", "moves the git tag")], tag=tag)
    assert f"names commit {OTHER_SHA}" in ex.attempts[0].errors[0], trace(ex)
    assert not check(ex)[0], trace(ex)
