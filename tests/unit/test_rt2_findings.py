"""The shortest traces the checker (test_rt2_explore.py) found in job `release`'s step "Create
the release" after the fix that reads the release by its id (42be3760), each replayed as a
schedule written in words, and each asserting the invariant it breaks. They fail while the fault
is there; the trace is in the message. Made-up repository, run, commits and files.
"""

from __future__ import annotations

import copy

import pytest
from test_rt2_explore import LAGS, Execution, check, execute, lags, render
from test_rt2_github import (
    HEAD_SHA,
    KINDS,
    OTHER_SHA,
    RUN_SHA,
    TAGS,
    Config,
    Hub,
    Scheduler,
    Script,
    current_run,
    run_attempt,
)


def replay(steps, tag=TAGS["release"], **config) -> Execution:
    return execute(current_run(), Config(tag=tag, **config), scheduler=Script(steps))


def trace(ex: Execution) -> str:
    violations, _ = check(ex)
    return "\n".join([*map(str, violations), *render(ex)])


def broken(ex: Execution, invariant: str) -> list[str]:
    return [str(v) for v in check(ex)[0] if v.invariant == invariant]


# --- 1. the git tag: never read, and made again by the publication ------------------------------


@pytest.mark.parametrize("tag", TAGS.values())
def test_a_draft_whose_git_tag_a_person_deleted_is_published_and_the_tag_made_on_main(tag):
    """Attempt 1: the look finds nothing, the draft is made with its files (--verify-tag sees the
    tag), the listing shows it, the read by id judges it. A person deletes the git tag (to
    withdraw the version, say). The download and the second read by id see nothing of it: a
    draft keeps its tag name, which is only a string. The publication, sent without a tag name,
    makes GitHub create the tag on the default branch's head, where the release's files were
    not built, and the job ends well. Invariants 1 (a git tag made), 2 (published past a read by
    id that came after the deletion) and 3 (exit 0 on a release whose tag is not the run's
    commit)."""
    ex = replay([(r"person, before GET releases/tags", "deletes the git tag")], tag=tag)
    assert ex.attempts[0].code != 0 or ex.hub.truth["tags"].get(tag) == RUN_SHA, trace(ex)
    assert not broken(ex, "1") and not broken(ex, "2"), trace(ex)


def test_a_re_run_after_the_git_tag_was_deleted_publishes_the_draft_onto_main_s_head():
    """The same across a re-run, with nothing racing: attempt 1 stops (a read by id gets no
    answer, so it says to re-run and delete nothing); the person deletes the git tag, meaning
    to withdraw the version; a re-run of the failed job later publishes the draft, and the
    publication makes the tag on the default branch's head. Nobody edits the release while the
    job runs, so the README's limit ('do not edit this tag's release by hand while the
    `release` job runs') does not cover it. That a publication makes a missing tag was seen in
    round 6 (a draft whose tag did not exist, published by the job's PATCH, got its tag);
    that the tag lands on the default branch's head is GitHub's documented default for a
    release with no target, not measured here."""
    ex = replay(
        [
            (r"fate of GET releases/88$", "fails", 2),
            (r"person, before GET releases page 1", "deletes the git tag"),
        ],
        follow_advice=False,
    )
    assert ex.hub.truth["tags"].get(TAGS["release"]) in (RUN_SHA, None), trace(ex)
    assert [a.code for a in ex.attempts][1] != 0, trace(ex)


@pytest.mark.parametrize("tag", TAGS.values())
def test_a_tag_moved_to_another_commit_before_the_job_gets_the_run_s_files_and_exit_0(tag):
    """A forced push moves the tag before the step's first request (the tag's new run waits in
    the same concurrency group). The step makes its draft on the moved tag, compares title,
    notes and files, publishes and ends well: the public release holds files built from a
    commit its tag no longer names, and `gh attestation verify --source-digest` on the tag's
    commit, as the README says to check, then fails. Invariant 3."""
    ex = replay([(r"person, before GET releases page 1", "moves the git tag")], tag=tag)
    assert ex.hub.truth["tags"][tag] == OTHER_SHA
    assert ex.attempts[0].code != 0, trace(ex)


# --- 3. the last read by id covers names, not bytes --------------------------------------------


@pytest.mark.parametrize("tag", TAGS.values())
def test_a_file_replaced_between_the_last_download_and_the_last_read_by_id_ends_well(tag):
    """After the publication the job downloads the files, compares them, and then reads the
    release by its id as its last request, which judges the file names only. A wheel replaced
    between that download and that read keeps its name: the read passes and the job ends well
    on a release whose wheel is not the run's, although its own last request was made after the
    replacement and GitHub's answer to it carries the new asset's id and size. The README: 'it
    succeeds only on a published release that ... holds exactly those files, byte for byte',
    and the read by id is 'its last request before it succeeds'. Invariant 3 (narrow)."""
    ex = replay([(r"person, before GET releases/88$", "replaces the wheel", 3)], tag=tag)
    assert ex.attempts[0].code != 0, trace(ex)


# --- 1 (outside the documented latest flag): a stable backport becomes GitHub's Latest ---------


def test_publishing_a_stable_release_below_the_highest_makes_it_github_s_latest():
    """release_check takes v1.2.3 while v1.3.0 is released (it refuses only a lower version of
    the same X.Y). The publication sends draft=false alone, and GitHub's make_latest defaults
    to true for a release newly published, so v1.2.3 becomes the repository's Latest release
    and v1.3.0 stops being it, while job `aliases` points the image's `latest` at 1.3.0. The
    README: the publication 'sets draft to false and nothing else (for a candidate it says
    again that the release is not the latest)'."""
    hub = Hub(Config(tag=TAGS["release"]), Scheduler())
    higher = {
        "id": 40,
        "tag_name": "v1.3.0",
        "draft": False,
        "prerelease": False,
        "name": "v1.3.0",
        "body": "the notes of 1.3.0\n",
        "assets": [],
        "target_commitish": "main",
    }
    for state in (hub.truth, hub.initial):
        state["releases"][40] = copy.deepcopy(higher)
        state["tags"]["v1.3.0"] = HEAD_SHA
        state["latest"] = 40
    hub.attempt = 1
    done = run_attempt(current_run(), hub, hub.files)
    assert done.code == 0, done.stdout + done.stderr
    assert hub.truth["latest"] == 40, "the publication made v1.2.3 GitHub's Latest release"


# --- 6: a listing behind across a re-run makes a second draft ------------------------------------


def test_a_re_run_whose_first_look_is_behind_makes_a_second_draft():
    """Switch: the listing still behind when the re-run starts (one look; measured 2.6 s, so
    only for a re-run that starts within seconds). Attempt 1's creation is done and its reply
    lost: the job stops on the CLI's own error, with a draft and no files. The re-run's first
    look does not show it, and the step makes a second draft. README: 'It never makes a second
    release in one attempt': true of the step's own releases, and this is two attempts."""
    ex = replay(
        [(r"fate of POST", "reply lost")],
        persist_lag=True,
        lag=dict.fromkeys(KINDS, 1),
    )
    assert not broken(ex, "6"), trace(ex)


# --- a person's draft the listing does not show yet: "Any other release for the tag stops the job"


def test_a_draft_a_person_makes_during_the_job_is_not_seen_and_the_job_ends_well():
    """The listing one look behind a person's write (the README: 'a person's as much as the
    job's'). A person makes a second draft for the tag while the job downloads its draft's
    files; the look after the publication does not show it, and the job ends well with two
    releases for the tag. The README: 'Any other release for the tag stops the job.' The re-run
    then stops on the job's own whole public release with 'there are 2 releases for it'.
    Invariant 3 (one release for the tag)."""
    ex = replay(
        [
            (
                r"person, before GET releases/88 \(draft lookup\)",
                "second release for the tag, a draft",
            )
        ],
        lag=LAGS["person one behind"],
    )
    assert not broken(ex, "3"), trace(ex)


def test_the_job_makes_its_draft_beside_a_person_s_draft_its_first_look_missed():
    """The same lag before the first look: the look shows nothing, the job makes its draft
    beside the person's, and stops at the next look on 'there are 2 releases for it'. Nothing
    is lost; the step's own count of releases stays one."""
    ex = replay(
        [(r"person, before GET releases page 1", "second release for the tag, a draft")],
        lag=LAGS["person one behind"],
    )
    assert not broken(ex, "6"), trace(ex)


# --- 5: a stop line that names the listing's state, not GitHub's -------------------------------


def test_a_title_edited_in_the_stated_race_is_reported_on_a_draft_that_is_public():
    """The documented race: a person edits the title between the last read by id and the
    publication. The look after the publication, its listing one look behind the publication
    and current on the person's edit, stops on the title, and the line says 'a draft' of a
    release that is public. Its advice ('delete it ... if it is still a draft, or put back what
    the run wrote') stays safe. Invariant 5 (the line is true of GitHub's state)."""
    ex = replay([(r"person, before PATCH", "edits the title")], lag=lags(publish=1))
    assert not broken(ex, "5"), trace(ex)
