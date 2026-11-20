"""Job `release`'s step "Create the release" in the independent tester's model of GitHub
(test_rt2_github), taught the requests the step sends now (rt2_new_requests): the tester's own
findings, each run and each held to reach the moment its schedule names; the git tag deleted
or moved before each request of three attempts; and a bounded exploration.

The tester's files are not edited. A finding whose schedule names a request the step no longer
sends passes without reaching it, so each run here records the schedule's steps the run did not
take, and a finding counts only when it took them all.

Where the coordinator's decisions leave a fault as a stated limit, the finding is run here and
still fails: decision 6 (a release the listing does not show yet is not seen) and the one
moment the step cannot close (the tag deleted between its last read and the publication). One
finding no decision covers (a title edited in the stated race, reported as on a draft) is run
and still fails too."""

from __future__ import annotations

import copy

import pytest
import rt2_new_requests as new
import test_rt2_explore as explore
import test_rt2_findings as findings
from test_rt2_github import (
    HEAD_SHA,
    OTHER_SHA,
    TAGS,
    Config,
    Script,
    current_run,
    run_attempt,
)


@pytest.fixture
def model(monkeypatch):
    """The taught model in the tester's modules, and the steps of each replayed schedule that
    its run did not take."""
    new.patch_in(monkeypatch)
    untaken: list[list] = []
    replay = findings.replay

    def recorded(steps, *args, **config):
        ex = replay(steps, *args, **config)
        untaken.append(list(ex.hub.s.steps))
        return ex

    monkeypatch.setattr(findings, "replay", recorded)
    return untaken


def violations(ex) -> list[str]:
    return [str(v) for v in new.check2(ex)[0]]


# --- the tester's findings the decisions fix ---------------------------------------------------

FIXED = [
    # decision 1: the tag read first, before the publication and before success
    ("test_a_re_run_after_the_git_tag_was_deleted_publishes_the_draft_onto_main_s_head", None),
    *(
        ("test_a_tag_moved_to_another_commit_before_the_job_gets_the_run_s_files_and_exit_0", t)
        for t in TAGS.values()
    ),
    # decision 5: the release read by its id on each side of the fetches
    *(
        ("test_a_file_replaced_between_the_last_download_and_the_last_read_by_id_ends_well", t)
        for t in TAGS.values()
    ),
    # decision 4: the publication says whether the release is GitHub's latest
    ("test_publishing_a_stable_release_below_the_highest_makes_it_github_s_latest", None),
    # decision 2: the bytes compared are the judged release's, fetched by asset id
    ("test_a_re_run_publishes_the_cut_draft_after_comparing_the_bytes_of_its_own_new_one", None),
]


@pytest.mark.parametrize(("name", "tag"), FIXED, ids=[f"{n}[{t}]" for n, t in FIXED])
def test_the_tester_s_finding_passes_and_its_schedule_was_taken(model, name, tag):
    finding = getattr(findings, name)
    finding(tag) if tag else finding()
    assert all(steps == [] for steps in model), model


@pytest.mark.parametrize("tag", TAGS.values())
def test_the_tag_deleted_finding_names_a_request_the_step_no_longer_sends(model, tag):
    """Decision 1's first finding schedules the person before `GET releases/tags`, the request
    `gh release download` made; the step fetches by asset id now, so the schedule is not taken
    and the finding passes without testing anything. The traces below delete the tag before
    every request instead."""
    findings.test_a_draft_whose_git_tag_a_person_deleted_is_published_and_the_tag_made_on_main(tag)
    assert model and model[-1] == [(r"person, before GET releases/tags", "deletes the git tag", 1)]


# --- the tag deleted or moved before each request -----------------------------------------------


def person_points(tag: str) -> list[tuple[int, str, list[str]]]:
    """Each moment of three undisturbed attempts at which a person may act, as (index, label,
    options)."""
    ex = explore.execute(current_run(), Config(tag=tag), {}, 1)
    return [
        (j, label, options)
        for j, (label, options) in enumerate(ex.hub.s.points)
        if label.startswith("person, ")
    ]


# The one moment the step cannot close: the tag deleted after its last read, before the
# publication. The model makes the tag again on the default branch's head (what GitHub does with
# the creation's target then is not measured), which is the tester's invariant 1.
RACE = "person, before PATCH"
TAG_MADE = (
    "1: the publication made a git tag (on the default branch's head): the draft's tag was gone"
)


@pytest.mark.parametrize("tag", TAGS.values())
@pytest.mark.parametrize("act", ["deletes the git tag", "moves the git tag"])
def test_the_tag_changed_before_any_request_is_never_published_or_called_finished(model, tag, act):
    points = person_points(tag)
    tried = []
    for j, label, options in points:
        pick = next((i for i, o in enumerate(options) if o.startswith(act)), None)
        if pick is None:
            continue
        ex = explore.execute(current_run(), Config(tag=tag), {j: pick}, 1)
        found = violations(ex)
        tried.append(label)
        trace = "\n".join(explore.render(ex))
        if found:
            # only in the race the step cannot close, and the attempt does not end well
            assert label.startswith(RACE) and act == "deletes the git tag", (label, found, trace)
            assert found == [TAG_MADE] and ex.attempts[0].code != 0, trace
            continue
        assert ex.hub.truth["tags"].get(tag) in (None, OTHER_SHA), trace
    assert len(tried) >= 20, tried
    assert any(label.startswith(RACE) for label in tried)


# --- a bounded exploration ------------------------------------------------------------------------


@pytest.mark.parametrize("tag", TAGS.values())
def test_every_schedule_of_one_deviation_breaks_nothing_but_the_race_at_the_publication(model, tag):
    """The tester's own search with one deviation (every request of three attempts may fail,
    lose its reply or be cut, and before each a person may do anything the menu holds), the
    listing current: the one violation left is the tag deleted after the step's last read of it
    and before the publication."""
    run = current_run()
    config = Config(tag=tag)
    points = explore.execute(run, config, {}, 1).hub.s.points
    executions = 0
    for j, (label, options) in enumerate(points):
        for alt in range(1, len(options)):
            ex = explore.execute(run, config, {j: alt}, 1)
            executions += 1
            found = violations(ex)
            if found:
                assert label.startswith(RACE) and options[alt] == "deletes the git tag", (
                    label,
                    options[alt],
                    found,
                    "\n".join(explore.render(ex)),
                )
                assert found == [TAG_MADE]
    assert executions >= 470, executions


# --- the stated limits ----------------------------------------------------------------------------

LIMITS = [
    # decision 6: a release the listing does not show yet is not seen (the re-run's own earlier
    # draft is put under it too, in the README's sentence; the coordinator's decision names a
    # person's release)
    "test_a_re_run_whose_first_look_is_behind_makes_a_second_draft",
    "test_the_job_makes_its_draft_beside_a_person_s_draft_its_first_look_missed",
    # under no decision: a title edited in the stated race, after the step's last read and
    # before its publication, is reported by the look after the publication, whose listing is
    # one look behind, as on "a draft" of a release that is public. The line's advice ("delete
    # it ... if it is still a draft, or put back what the run wrote") stays safe.
    "test_a_title_edited_in_the_stated_race_is_reported_on_a_draft_that_is_public",
]


@pytest.mark.parametrize("name", LIMITS)
def test_the_tester_s_finding_of_a_stated_limit_still_fails(model, name):
    with pytest.raises(AssertionError):
        getattr(findings, name)()
    assert all(steps == [] for steps in model), model


def test_a_person_s_draft_the_listing_does_not_show_yet_is_the_stated_limit(model):
    """The tester's finding of it schedules the person before the draft lookup of `gh release
    download`, which the step no longer sends, so it passes without reaching its moment. The
    same act now, before the step fetches its draft's first file, with the listing one look
    behind a person's write: the job does not see the person's draft and ends well with two
    releases for the tag (decision 6, the README's stated limit), and the one it published is
    its own, whole; the re-run stops on the two, and after the person deletes theirs, as the
    line says, it ends well on the same release."""
    findings.test_a_draft_a_person_makes_during_the_job_is_not_seen_and_the_job_ends_well()
    assert model[-1] == [
        (
            r"person, before GET releases/88 \(draft lookup\)",
            "second release for the tag, a draft",
            1,
        )
    ]
    ex = findings.replay(
        [(r"person, before GET asset", "second release for the tag, a draft")],
        lag=explore.LAGS["person one behind"],
    )
    assert model[-1] == []
    assert violations(ex) == ["3: exit 0 with 2 releases for the tag when it last listed them"]
    assert [a.code for a in ex.attempts] == [0, 1, 0]
    (left,) = ex.hub.truth["releases"].values()
    assert left["id"] == 88 and not left["draft"] and left["name"] == TAGS["release"]


# --- three interleavings a reviewer would try -----------------------------------------------------


def test_a_wheel_replaced_between_two_fetches_of_the_draft_stops_before_the_publication(model):
    """The person replaces the draft's wheel after the step fetched it and before it fetches
    the next file: every fetch succeeds, and the read by id after them shows another asset, so
    the step stops on the change and publishes nothing."""
    ex = findings.replay([(r"person, before GET asset \d+ by its id", "replaces the wheel", 2)])
    assert model[-1] == []
    assert violations(ex) == []
    assert ex.attempts[0].code == 1
    assert "changed between this job's two reads of it by its id" in ex.attempts[0].errors[0]
    assert not any(e.kind == "patch" for e in ex.hub.events if e.attempt == 1)


def test_a_wheel_replaced_while_the_job_waits_after_its_publication_is_seen(model):
    """The listing nine looks behind the publication, and the person replaces the wheel during
    the looks that wait for it: the read by id after the wait gives the new asset, its bytes
    are fetched by that id and differ, and the step stops instead of calling it finished."""
    ex = findings.replay(
        [(r"person, before GET releases page 1", "replaces the wheel", 4)],
        lag=explore.lags(publish=9),
    )
    assert model[-1] == []
    assert violations(ex) == []
    assert ex.attempts[0].code == 1
    assert "is not the file this run attested" in ex.attempts[0].errors[0]


def test_a_backport_whose_publication_reply_was_lost_leaves_github_s_latest(model):
    """v1.3.0 is released; the publication of v1.2.3 is done and its reply lost, so the job
    fails; the re-run finds the release published and leaves it: the one publication said
    `make_latest` false, and v1.3.0 stays GitHub's latest."""
    hub = new.Hub2(Config(tag=TAGS["release"]), Script([(r"fate of PATCH", "reply lost")]))
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
    codes = []
    for n in (1, 2):
        hub.attempt, hub.command = n, 0
        codes.append(run_attempt(current_run(), hub, hub.files).code)
    assert codes == [1, 0] and hub.s.steps == []
    sent = [e.shown for e in hub.events if e.what == "request" and e.kind == "patch"]
    assert sent == [{"draft": False, "make_latest": "false"}]
    assert hub.truth["latest"] == 40
