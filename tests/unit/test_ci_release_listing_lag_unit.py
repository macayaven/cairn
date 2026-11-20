"""Job `release` waits for GitHub's listing to show the job's own write, a bounded number of
looks, and waits for nothing else.

Measured against GitHub's API on 2026-11-20 (the coordinator's measurement): a draft made
without files was absent from `GET /repos/{repo}/releases` about a second after its creation
returned and present 2.6 s after it, in three of three trials, while a read of the release by
its id showed each write at once. So the look the step makes right after its own write could
see what was there before it, and stop a first run in which nothing was wrong: on no release
after the creation, or on a release still a draft after the publication.

Now a look that follows the step's own write is made again while the listing shows exactly
that: no release for the tag after the creation, or this run's draft with some, not all, of
its three file names (the uploads take about as long as the listing is behind); this run's
release, still a draft, after the publication. At most `looks` looks, `apart` seconds apart,
the two values at the top of the step; then what the step did before. Any other state stops
at once, as before: a release that is not this run's, an edited one, two for the tag, a file
name that is not one of the three, a listing that fails, no release after the publication.
(One state is both: this run's draft, edited as soon as it was made and listed with some of
its file names. The step waits for the names, as for any draft of its own, and then stops on
the edit; nothing is published either way.) The first look of a run never waits.

gh is test_ci_release_retry_unit's stand-in, whose listing shows what was there before a
write until the k-th look after it. `sleep` is a stand-in on the path, as gh is: it keeps its
call and returns at once, so no test waits and each says how often the step slept."""

import json
import re

import pytest
from test_ci_release_notes_title_unit import DIFFERS
from test_ci_release_retry_unit import (
    CREATE,
    DRAFT,
    LISTING,
    README,
    REFUSED,
    attached,
    attempt,
    cells,
    held,
    looks,
    made_earlier,
    run,
    the_error,
    writes,
)

RUN = CREATE["run"]


def value(name: str) -> int:
    """The number the step gives NAME, on a line of its own; 0 when it gives none."""
    found = re.search(rf"^ *{name}=([0-9]+)$", RUN, re.M)
    return int(found[1]) if found else 0


LOOKS, APART = value("looks"), value("apart")
STALE = (
    "::error::GitHub does not list the release for {tag} as this job has just left it; re-run"
    " this job, which starts from what is listed then"
)
BOTH = [("v1.2.3", ""), ("v1.2.3-rc.1", "true")]


def slept(calls: list[str]) -> list[str]:
    return [line for line in calls if line.startswith("sleep")]


def after(calls: list[str], write: str) -> list[str]:
    """CALLS from the last one that starts with WRITE, each listing cut to its start."""
    seen = looks(calls)
    at = max(n for n, line in enumerate(seen) if line.startswith(write))
    return seen[at + 1 :]


def test_the_two_values_stand_at_the_top_of_the_step_and_its_comment_says_the_measurement():
    lines = [line.strip() for line in RUN.splitlines()]
    values = [line for line in lines if not line.startswith("#")][:3]
    assert values == ['cd "$RUNNER_TEMP/release"', "looks=10", "apart=3"]
    assert (LOOKS, APART) == (10, 3)
    comment = " ".join(RUN.replace("#", "").split())
    assert (
        "GitHub's listing of releases can be behind a write: measured against its API on"
        " 2026-11-20, a draft made without files was absent from a listing made about a second"
        " after its creation returned and present 2.6 s after it, in three of three trials,"
        " while a read of the release by its id showed each write at once."
    ) in comment
    assert (
        "at most $looks looks, $apart seconds apart (10 and 3, so 27 seconds at the longest)"
    ) in comment
    # The values are used by name where the step waits, and nowhere as bare numbers.
    assert '[ "$n" -lt "$looks" ]' in RUN and 'sleep "$apart"' in RUN
    assert not re.search(r"\bsleep [0-9]", RUN)


# --- after the creation ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("shown_from", [1, 2, 10])
def test_a_draft_listed_from_the_k_th_look_after_its_creation_is_published_in_the_end(
    tmp_path, tag, candidate, shown_from
):
    done, calls = run(tmp_path, tag, candidate, MADE_SHOWN_FROM_LOOK=str(shown_from))
    assert done.returncode == 0, done.stdout + done.stderr
    assert slept(calls) == [f"sleep {APART}"] * (shown_from - 1)
    # The first look of the run, then the creation with nothing between them; after it, a look,
    # and a wait and a look for each look that did not show the draft; then the step goes on.
    seen = looks(calls)
    assert seen[0] == LISTING and seen[1].startswith(f"gh release create {tag} --draft ")
    waited = [LISTING, *[f"sleep {APART}", LISTING] * (shown_from - 1)]
    assert after(calls, "gh release create")[: len(waited) + 1] == [
        *waited,
        f"gh release download {tag} --dir {tmp_path}/draft",
    ]
    assert [line.split()[1:3] for line in writes(calls)] == [
        ["release", "create"],
        ["api", "--method"],
    ]
    assert [(r["id"], r["draft"]) for r in held(tmp_path)] == [(88, False)]


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_a_draft_not_listed_at_the_last_look_after_its_creation_stops_the_job_unwritten(
    tmp_path, tag, candidate
):
    """One look more than the step makes. It stops as it did, having written nothing after
    the draft, and never makes a second release; the re-run finds the draft, compares it and
    publishes it."""
    done, calls = run(tmp_path, tag, candidate, MADE_SHOWN_FROM_LOOK=str(LOOKS + 1))
    assert done.returncode == 1
    assert the_error(done) == STALE.format(tag=tag)
    assert slept(calls) == [f"sleep {APART}"] * (LOOKS - 1)
    assert after(calls, "gh release create") == [
        LISTING,
        *[f"sleep {APART}", LISTING] * (LOOKS - 1),
    ]
    assert [line.split()[1:3] for line in writes(calls)] == [["release", "create"]]
    (draft,) = held(tmp_path)
    assert (draft["id"], draft["draft"]) == (88, True)
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    assert slept(calls) == []
    assert [line.split()[1:3] for line in writes(calls)] == [["api", "--method"]]
    assert held(tmp_path) == [{**draft, "draft": False}]


def test_a_draft_a_person_deletes_as_soon_as_it_is_made_reads_as_one_not_listed_yet(tmp_path):
    """What the wait cannot tell from a listing that is behind: no release after the creation.
    The step looks its ten times, stops as it did, and the re-run makes the release again."""
    done, calls = run(tmp_path, "v1.2.3", "", DELETED_ONCE_MADE="1")
    assert done.returncode == 1
    assert the_error(done) == STALE.format(tag="v1.2.3")
    assert len(slept(calls)) == LOOKS - 1 and len(writes(calls)) == 1 and held(tmp_path) == []
    done, calls = attempt(tmp_path, "v1.2.3", "")
    assert done.returncode == 0, done.stdout + done.stderr
    assert slept(calls) == [] and [(r["id"], r["draft"]) for r in held(tmp_path)] == [(88, False)]


# --- after the creation: the draft is listed before all its file names are ----------------------

WHEEL, SOURCE, SUMS = (
    "cairn_memory-1.2.3-py3-none-any.whl",
    "cairn_memory-1.2.3.tar.gz",
    "SHA256SUMS",
)


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("listed", [[], [WHEEL], [SUMS, WHEEL]], ids=["none", "one", "two"])
@pytest.mark.parametrize("for_looks", [1, 9])
def test_a_draft_listed_with_some_of_its_file_names_is_waited_for_and_published_in_the_end(
    tmp_path, tag, candidate, listed, for_looks
):
    """The three uploads take about as long as the listing is behind, so the look after the
    creation can show this run's draft with fewer file names than were uploaded. That is a
    write of the step's own not listed yet: it waits, within the same looks, and goes on when
    the names are the three."""
    done, calls = run(
        tmp_path,
        tag,
        candidate,
        NAMES_LISTED_AT_FIRST=json.dumps(listed),
        NAMES_LISTED_FOR_LOOKS=str(for_looks),
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert slept(calls) == [f"sleep {APART}"] * for_looks
    waited = [LISTING, *[f"sleep {APART}", LISTING] * for_looks]
    assert after(calls, "gh release create")[: len(waited) + 1] == [
        *waited,
        f"gh release download {tag} --dir {tmp_path}/draft",
    ]
    assert [(r["id"], r["draft"]) for r in held(tmp_path)] == [(88, False)]


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("listed", [[], [WHEEL], [SOURCE, WHEEL]], ids=["none", "one", "two"])
def test_a_draft_never_listed_with_its_third_file_name_ends_on_the_unfinished_line_after_the_wait(
    tmp_path, tag, candidate, listed
):
    """What a draft that really lacks a file reads as. The step looks its ten times, then
    compares exactly as before: the unfinished line, nothing downloaded and nothing published.
    (Here the files are whole and only the listing is short, so the re-run publishes.)"""
    done, calls = run(tmp_path, tag, candidate, NAMES_LISTED_AT_FIRST=json.dumps(listed))
    assert done.returncode == 1
    assert the_error(done) == DRAFT.format(tag=tag)
    assert slept(calls) == [f"sleep {APART}"] * (LOOKS - 1)
    assert after(calls, "gh release create") == [
        LISTING,
        *[f"sleep {APART}", LISTING] * (LOOKS - 1),
    ]
    assert [line.split()[1:3] for line in writes(calls)] == [["release", "create"]]
    (draft,) = held(tmp_path)
    assert (draft["id"], draft["draft"]) == (88, True)
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    assert slept(calls) == [] and held(tmp_path) == [{**draft, "draft": False}]


@pytest.mark.parametrize(
    "listed",
    [
        ["other.txt"],
        [WHEEL, "other.txt"],
        [SOURCE, WHEEL, "other.txt"],
        [SUMS, SOURCE, WHEEL, "other.txt"],
        ["cairn_memory-1.2.3-py3-none-any.whl.part"],
        ["cairn_memory-1.2.3"],
        ["*"],
        ["cairn*"],
        ["SHA256SUMS SHA256SUMS"],
    ],
    ids=[
        "alone",
        "beside one",
        "beside two",
        "beside the three",
        "a longer name",
        "a shorter name",
        "a star",
        "a pattern of two of them",
        "one name twice in one",
    ],
)
def test_a_file_name_that_is_not_one_of_the_three_is_no_reason_to_wait(tmp_path, listed):
    """One step past: a name beside the three, one that begins like one of them, and names
    the shell would read as a pattern over the step's own directory, which holds the three."""
    done, calls = run(tmp_path, "v1.2.3", "", NAMES_LISTED_AT_FIRST=json.dumps(listed))
    assert done.returncode == 1
    assert the_error(done) == DRAFT.format(tag="v1.2.3")
    assert slept(calls) == [] and after(calls, "gh release create") == [LISTING]
    assert len(writes(calls)) == 1 and held(tmp_path)[0]["draft"] is True


@pytest.mark.parametrize("names", [[SUMS, WHEEL], [WHEEL], []], ids=["two", "one", "none"])
def test_a_draft_found_by_the_first_look_with_some_of_the_names_is_compared_at_once(
    tmp_path, names
):
    """Only the run that has just made the draft waits for its names: one an earlier attempt
    left is what it is, and stops on the unfinished line with no wait."""
    existing = made_earlier("v1.2.3", False, draft=True, names=names)
    done, calls = run(tmp_path, "v1.2.3", "", existing, {}, NAMES_LISTED_AT_FIRST=json.dumps(names))
    assert done.returncode == 1
    assert the_error(done) == DRAFT.format(tag="v1.2.3")
    assert slept(calls) == [] and looks(calls) == [LISTING] and held(tmp_path) == [existing]


def test_the_looks_for_the_draft_and_for_its_names_are_the_same_ten(tmp_path):
    """Not listed at five looks, then listed with one name: the tenth look is the last, and
    the step goes on to the comparison, which stops on the unfinished line."""
    done, calls = run(
        tmp_path,
        "v1.2.3",
        "",
        MADE_SHOWN_FROM_LOOK="6",
        NAMES_LISTED_AT_FIRST=json.dumps([WHEEL]),
    )
    assert done.returncode == 1 and the_error(done) == DRAFT.format(tag="v1.2.3")
    assert len(slept(calls)) == LOOKS - 1 and len(writes(calls)) == 1


@pytest.mark.parametrize("for_looks", ["1", ""], ids=["one look", "every look"])
def test_a_draft_edited_as_soon_as_it_is_made_and_short_of_names_is_waited_for_then_stopped_on(
    tmp_path, for_looks
):
    """The two together: the names are waited for first, since the title and the notes are
    compared only when the look is done, and then the edit stops the job, with the line of an
    edited draft. Nothing is downloaded and nothing published, with the wait or without."""
    done, calls = run(
        tmp_path,
        "v1.2.3",
        "",
        CHANGED_ONCE_MADE=EDITED,
        NAMES_LISTED_AT_FIRST=json.dumps([WHEEL]),
        NAMES_LISTED_FOR_LOOKS=for_looks,
    )
    assert done.returncode == 1
    assert the_error(done) == DIFFERS.format(tag="v1.2.3", state="a draft", what="notes")
    assert len(slept(calls)) == (1 if for_looks else LOOKS - 1)
    assert [line.split()[1:3] for line in writes(calls)] == [["release", "create"]]
    assert not any(line.startswith("gh release download") for line in calls)


def test_a_draft_a_person_published_with_some_of_the_names_listed_is_not_waited_for(tmp_path):
    """The wait is for this run's draft. Published meanwhile, it is a release, and the names
    listed for it are compared at once, as before."""
    done, calls = run(
        tmp_path,
        "v1.2.3",
        "",
        NAMES_LISTED_AT_FIRST=json.dumps([WHEEL]),
        CHANGED_ONCE_MADE=".draft = false",
    )
    assert done.returncode == 1 and slept(calls) == []
    assert the_error(done).startswith(REFUSED.format(tag="v1.2.3") + f"its files are [{WHEEL}]")


# --- after the publication ------------------------------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("existing", [None, "draft"], ids=["made now", "left by an attempt"])
@pytest.mark.parametrize("shown_from", [1, 2, 10])
def test_a_publication_listed_from_the_k_th_look_after_it_is_compared_and_the_job_succeeds(
    tmp_path, tag, candidate, existing, shown_from
):
    held_ = made_earlier(tag, candidate == "true", draft=True) if existing else None
    assets = attached(tag) if existing else None
    done, calls = run(
        tmp_path, tag, candidate, held_, assets, PUBLISHED_SHOWN_FROM_LOOK=str(shown_from)
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert slept(calls) == [f"sleep {APART}"] * (shown_from - 1)
    assert after(calls, "gh api --method PATCH") == [
        LISTING,
        *[f"sleep {APART}", LISTING] * (shown_from - 1),
        f"gh release download {tag} --dir {tmp_path}/published",
    ]
    (public,) = held(tmp_path)
    assert public["draft"] is False and len(writes(calls)) == (1 if existing else 2)


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("existing", [None, "draft"], ids=["made now", "left by an attempt"])
def test_a_publication_not_listed_at_the_last_look_after_it_stops_the_job_unwritten(
    tmp_path, tag, candidate, existing
):
    """The release is public, the listing still shows the draft at the tenth look, and the
    step stops as it did, with nothing written after the publication. The re-run finds the
    release published, compares it and succeeds, unwritten."""
    held_ = made_earlier(tag, candidate == "true", draft=True) if existing else None
    assets = attached(tag) if existing else None
    done, calls = run(
        tmp_path, tag, candidate, held_, assets, PUBLISHED_SHOWN_FROM_LOOK=str(LOOKS + 1)
    )
    assert done.returncode == 1
    assert the_error(done) == STALE.format(tag=tag)
    assert slept(calls) == [f"sleep {APART}"] * (LOOKS - 1)
    assert after(calls, "gh api --method PATCH") == [
        LISTING,
        *[f"sleep {APART}", LISTING] * (LOOKS - 1),
    ]
    (public,) = held(tmp_path)
    assert public["draft"] is False
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    assert slept(calls) == [] and writes(calls) == [] and held(tmp_path) == [public]


def test_both_writes_are_waited_for_each_with_its_own_looks(tmp_path):
    done, calls = run(
        tmp_path, "v1.2.3", "", MADE_SHOWN_FROM_LOOK="3", PUBLISHED_SHOWN_FROM_LOOK="10"
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(slept(calls)) == 2 + 9
    assert [(r["id"], r["draft"]) for r in held(tmp_path)] == [(88, False)]


# --- any other state stops at once, and the first look never waits -------------------------------

EDITED = '.body |= sub("the notes"; "other notes")'


@pytest.mark.parametrize(
    ("person", "said"),
    [
        (
            {"CHANGED_ONCE_MADE": EDITED},
            DIFFERS.format(tag="v1.2.3", state="a draft", what="notes"),
        ),
        (
            {"CHANGED_ONCE_MADE": '.name = "Spring"'},
            DIFFERS.format(tag="v1.2.3", state="a draft", what="title"),
        ),
        (
            {"CHANGED_ONCE_MADE": '.body = "made by hand"'},
            REFUSED.format(tag="v1.2.3") + "its text does not carry run 4242's mark",
        ),
        (
            {"CHANGED_ONCE_MADE": ".prerelease = true"},
            REFUSED.format(tag="v1.2.3") + "its pre-release flag is true, not false",
        ),
        ({"ANOTHER_ONCE_MADE": "1"}, REFUSED.format(tag="v1.2.3") + "there are 2 releases for it"),
        (
            {"LIST_FAILS_AFTER_A_WRITE": "1"},
            "::error::the repository's releases could not be listed, so whether v1.2.3 has one is"
            " not known",
        ),
    ],
    ids=["notes edited", "title edited", "not this run's", "another flag", "two", "no listing"],
)
def test_after_the_creation_any_state_but_no_release_stops_at_the_first_look(
    tmp_path, person, said
):
    done, calls = run(tmp_path, "v1.2.3", "", MADE_SHOWN_FROM_LOOK="1", **person)
    assert done.returncode == 1
    assert the_error(done).startswith(said)
    assert slept(calls) == [] and len(writes(calls)) == 1
    assert after(calls, "gh release create") == [LISTING]


@pytest.mark.parametrize(
    ("person", "said"),
    [
        (
            {"CHANGED_ONCE_PUBLIC": EDITED},
            DIFFERS.format(tag="v1.2.3", state="public", what="notes"),
        ),
        (
            {"CHANGED_ONCE_PUBLIC": '.name = "Spring"'},
            DIFFERS.format(tag="v1.2.3", state="public", what="title"),
        ),
        (
            {"CHANGED_ONCE_PUBLIC": '.body = "made by hand"'},
            REFUSED.format(tag="v1.2.3") + "its text does not carry run 4242's mark",
        ),
        ({"DELETED_ONCE_PUBLIC": "1"}, STALE.format(tag="v1.2.3")),
        (
            {"LIST_FAILS_AFTER_A_WRITE": "1"},
            "::error::the repository's releases could not be listed, so whether v1.2.3 has one is"
            " not known",
        ),
    ],
    ids=["notes edited", "title edited", "not this run's", "no release", "no listing"],
)
def test_after_the_publication_any_state_but_a_draft_still_stops_at_the_first_look(
    tmp_path, person, said
):
    """Among them no release at all: after the publication the wait is for a release still
    listed as a draft, and for nothing else."""
    existing = made_earlier("v1.2.3", False, draft=True)
    done, calls = run(tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), **person)
    assert done.returncode == 1
    assert the_error(done).startswith(said)
    assert slept(calls) == [] and len(writes(calls)) == 1
    assert after(calls, "gh api --method PATCH") == [LISTING]


@pytest.mark.parametrize(
    ("existing", "assets", "code", "said"),
    [
        (made_earlier("v1.2.3", False), attached("v1.2.3"), 0, None),
        (
            made_earlier("v1.2.3", False, draft=True, names=["SHA256SUMS"]),
            {},
            1,
            DRAFT.format(tag="v1.2.3"),
        ),
        (
            made_earlier("v1.2.3", False, draft=True, body="made by hand"),
            {},
            1,
            REFUSED.format(tag="v1.2.3") + "its text does not carry run 4242's mark",
        ),
        (
            made_earlier("v1.2.3", False, body="other notes\n\n<!-- cairn-ci-cd run 4242 -->\n"),
            attached("v1.2.3"),
            1,
            DIFFERS.format(tag="v1.2.3", state="public", what="notes"),
        ),
    ],
    ids=["published and whole", "an unfinished draft", "not this run's", "edited"],
)
def test_the_first_look_of_a_run_never_waits(tmp_path, existing, assets, code, said):
    """No write of this attempt precedes it, so nothing is waited for: what it shows is what
    the step starts from, a listing that would lag behind a write included."""
    done, calls = run(
        tmp_path,
        "v1.2.3",
        "",
        existing,
        assets,
        MADE_SHOWN_FROM_LOOK="5",
        PUBLISHED_SHOWN_FROM_LOOK="5",
    )
    assert done.returncode == code, done.stdout + done.stderr
    assert slept(calls) == [] and writes(calls) == [] and held(tmp_path) == [existing]
    if said:
        assert the_error(done).startswith(said)


def test_a_listing_that_fails_at_the_first_look_is_not_waited_for(tmp_path):
    done, calls = run(tmp_path, "v1.2.3", "", LIST_FAILS="1")
    assert done.returncode == 1 and slept(calls) == [] and looks(calls) == [LISTING]


# --- the documents --------------------------------------------------------------------------------


def test_the_readme_says_the_wait_with_the_step_s_two_values_and_the_row_says_what_it_means():
    readme = " ".join(README.split())
    assert (LOOKS, APART) == (10, 3)
    assert (
        "GitHub's listing of releases can be a few seconds behind a write, so the look that"
        " follows the job's own write is made again while it shows what was there before it (no"
        " release after the creation, or this run's draft with some, not all, of its three file"
        " names; this run's release still a draft after the publication): at most ten looks,"
        " three seconds apart, and then the job goes on as it would have at once: it stops, or"
        " compares the draft's files, and a re-run starts from what is listed then. Nothing"
        " else is waited for, and the first look of a run never waits."
    ) in readme
    means, next_step = cells("GitHub does not list the release for … as this job has just left it")
    assert means == (
        "After the job made the draft, the listing showed no release for the tag at each of its"
        " ten looks, three seconds apart; or after it published the draft, the listing showed"
        " no release, or at each of those looks still a draft: GitHub's listing is further"
        " behind than the job waits, or a person deleted the release in that moment"
    )
    assert next_step == (
        "Re-run the job, which starts from what is listed then: it makes the draft if there is"
        " no release, publishes this run's draft, or compares the published release. It never"
        " makes a second release in one attempt."
    )
