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
Every look judges the mark, the flag, the title and the notes it shows, so a draft edited as
soon as it was made stops the step at once, short of names or not. The first look of a run
never waits. What the step compares it reads by the release's id
(test_ci_release_fresh_read_unit); the listing's file names only say whether to wait.

gh is test_ci_release_retry_unit's stand-in, whose listing shows what was there before a
write until the k-th look after it. `sleep` is a stand-in on the path, as gh is: it keeps its
call and returns at once, so no test waits and each says how often the step slept."""

import json
import re

import pytest
import test_ci_release_retry_unit as retry
from test_ci_release_lines_unit import CROWDED
from test_ci_release_notes_title_unit import DIFFERS
from test_ci_release_retry_unit import (
    CREATE,
    DRAFT,
    LISTING,
    READ,
    README,
    REFUSED,
    attached,
    cells,
    fetches,
    held,
    looks,
    made_earlier,
    the_error,
    unfinished,
    untagged,
    writes,
)

RUN = CREATE["run"]


def run(*args, **env):
    """test_ci_release_retry_unit's run, its calls without the tag's reads, which
    test_ci_release_tag_unit places."""
    done, calls = retry.run(*args, **env)
    return done, untagged(calls)


def attempt(*args, **env):
    done, calls = retry.attempt(*args, **env)
    return done, untagged(calls)


def value(name: str) -> int:
    """The number the step gives NAME, on a line of its own; 0 when it gives none."""
    found = re.search(rf"^ *{name}=([0-9]+)$", RUN, re.M)
    return int(found[1]) if found else 0


LOOKS, APART = value("looks"), value("apart")
STALE = (
    "::error::GitHub's listing, at this job's last look, does not list the release for {tag} as"
    " this job has just left it, and it can be seconds behind a write; look at the repository's"
    " releases page first, then re-run this job, which starts from what is listed then"
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
    assert after(calls, "gh release create")[: len(waited) + 2] == [
        *waited,
        READ.format(id=88),
        fetches(88)[0],
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
    assert after(calls, "gh release create")[: len(waited) + 2] == [
        *waited,
        READ.format(id=88),
        fetches(88)[0],
    ]
    assert [(r["id"], r["draft"]) for r in held(tmp_path)] == [(88, False)]


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("kept", [0, 1, 2], ids=["none", "one", "two"])
def test_a_draft_that_lacks_a_file_ends_on_the_unfinished_line_after_the_wait(
    tmp_path, tag, candidate, kept
):
    """The draft holds fewer files than the step uploaded, at every look and when read by
    its id. The step cannot tell that from a listing short of names, so it looks its ten
    times; then the read says what the draft holds, and the step stops on the unfinished
    line, with nothing downloaded and nothing published. (A whole draft the listing never
    names in full is published after the same wait: test_ci_release_fresh_read_unit.)"""
    done, calls = run(tmp_path, tag, candidate, CHANGED_ONCE_MADE=f".assets |= .[:{kept}]")
    assert done.returncode == 1
    assert unfinished(the_error(done), tag)
    assert slept(calls) == [f"sleep {APART}"] * (LOOKS - 1)
    assert after(calls, "gh release create") == [
        LISTING,
        *[f"sleep {APART}", LISTING] * (LOOKS - 1),
        READ.format(id=88),
    ]
    assert [line.split()[1:3] for line in writes(calls)] == [["release", "create"]]
    (draft,) = held(tmp_path)
    assert (draft["id"], draft["draft"], len(draft["assets"])) == (88, True, kept)


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
    the shell would read as a pattern over the step's own directory, which holds the three.
    The draft holds them, in the listing and when read by its id: no wait, and the unfinished
    line at once."""
    held_ = json.dumps([{"name": name, "size": 1} for name in listed])
    done, calls = run(tmp_path, "v1.2.3", "", CHANGED_ONCE_MADE=f".assets = {held_}")
    assert done.returncode == 1
    assert unfinished(the_error(done), "v1.2.3")
    assert slept(calls) == []
    assert after(calls, "gh release create") == [LISTING, READ.format(id=88)]
    assert len(writes(calls)) == 1 and held(tmp_path)[0]["draft"] is True


def test_a_file_name_the_listing_alone_shows_is_not_judged(tmp_path):
    """The listing's names only say whether to wait. A name it shows that the draft does not
    hold stops no wait and no publication: the read by its id says what the draft holds."""
    done, calls = run(
        tmp_path, "v1.2.3", "", NAMES_LISTED_AT_FIRST=json.dumps([WHEEL, "other.txt"])
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert slept(calls) == [] and [(r["id"], r["draft"]) for r in held(tmp_path)] == [(88, False)]


@pytest.mark.parametrize("names", [[SUMS, WHEEL], [WHEEL], []], ids=["two", "one", "none"])
def test_a_draft_found_by_the_first_look_with_some_of_the_names_is_compared_at_once(
    tmp_path, names
):
    """Only the run that has just made the draft waits for its names: one an earlier attempt
    left is what it is, and stops on the unfinished line with no wait."""
    existing = made_earlier("v1.2.3", False, draft=True, names=names)
    done, calls = run(tmp_path, "v1.2.3", "", existing, {}, NAMES_LISTED_AT_FIRST=json.dumps(names))
    assert done.returncode == 1
    assert unfinished(the_error(done), "v1.2.3")
    assert slept(calls) == [] and held(tmp_path) == [existing]
    assert looks(calls) == [LISTING, READ.format(id=77)]


def test_the_looks_for_the_draft_and_for_its_names_are_the_same_ten(tmp_path):
    """Not listed at five looks, then listed with one name: the tenth look is the last, and
    the step goes on to the read by id, which finds the draft whole, and publishes it."""
    done, calls = run(
        tmp_path,
        "v1.2.3",
        "",
        MADE_SHOWN_FROM_LOOK="6",
        NAMES_LISTED_AT_FIRST=json.dumps([WHEEL]),
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(slept(calls)) == LOOKS - 1 and len(writes(calls)) == 2


@pytest.mark.parametrize("for_looks", ["1", ""], ids=["one look", "every look"])
def test_a_draft_edited_as_soon_as_it_is_made_and_short_of_names_stops_at_the_first_look(
    tmp_path, for_looks
):
    """The two together. Every look judges the title and the notes it shows, so the edit sends
    the job, at the first look after the creation, to a read by id, which shows it too, and the
    job stops there, whatever the names would have waited for (sixth report, must-fix 2: such a
    draft was waited for, and went on when a later listing showed it restored). Nothing is
    downloaded and nothing published."""
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
    assert slept(calls) == [] and after(calls, "gh release create") == [LISTING, READ.format(id=88)]
    assert [line.split()[1:3] for line in writes(calls)] == [["release", "create"]]
    assert not any(line.startswith("gh release download") for line in calls)


def test_a_draft_a_person_published_with_some_of_the_names_listed_is_not_waited_for(tmp_path):
    """The wait is for this run's draft. Published meanwhile with one file, it is a release,
    and what it holds is judged at once, on the read by its id."""
    done, calls = run(
        tmp_path,
        "v1.2.3",
        "",
        CHANGED_ONCE_MADE=".draft = false | .assets |= .[:1]",
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
        READ.format(id=88 if not existing else 77),
        *fetches(88 if not existing else 77),
        READ.format(id=88 if not existing else 77),
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
        ({"ANOTHER_ONCE_MADE": "1"}, CROWDED),
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
    """A row that is not this run's as it wrote it sends the job to a read by id at once, and
    the job stops on that read; two rows stop it on the listing."""
    done, calls = run(tmp_path, "v1.2.3", "", MADE_SHOWN_FROM_LOOK="1", **person)
    assert done.returncode == 1
    assert the_error(done).startswith(said)
    assert slept(calls) == [] and len(writes(calls)) == 1
    read = [READ.format(id=88)] if said.startswith("::error::read by its id") else []
    assert after(calls, "gh release create") == [LISTING, *read]


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
    listed as a draft, and for nothing else. An edited row is read by its id at once."""
    existing = made_earlier("v1.2.3", False, draft=True)
    done, calls = run(tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), **person)
    assert done.returncode == 1
    assert the_error(done).startswith(said)
    assert slept(calls) == [] and len(writes(calls)) == 1
    read = [READ.format(id=77)] if said.startswith("::error::read by its id") else []
    assert after(calls, "gh api --method PATCH") == [LISTING, *read]


@pytest.mark.parametrize(
    ("existing", "assets", "code", "said"),
    [
        (made_earlier("v1.2.3", False), attached("v1.2.3"), 0, None),
        (
            made_earlier("v1.2.3", False, draft=True, names=["SHA256SUMS"]),
            {},
            1,
            DRAFT.format(tag="v1.2.3", why="\0").split("\0")[0],
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
        " reads the draft by its id, and a re-run starts from what is listed then. Nothing else"
        " is waited for: a look whose row shows a mark, a flag, a title or notes that are not"
        " this run's reads the release by its id at once, and the job stops on what that read"
        " shows, and the first look of a run never waits."
    ) in readme
    means, next_step = cells(
        "GitHub's listing, at this job's last look, does not list the release for … as this job"
        " has just left it, and it can be seconds behind a write"
    )
    assert means == (
        "After the job made the draft, the listing showed no release for the tag at each of its"
        " ten looks, three seconds apart; or after it published the draft, the listing showed"
        " no release, or at each of those looks still a draft: GitHub's listing is further"
        " behind than the job waits, or a person deleted the release or made it a draft in that"
        " moment"
    )
    assert next_step == (
        "Look at the repository's releases page first, then re-run the job, which starts from"
        " what is listed then: it makes the draft if there is"
        " no release, publishes this run's draft, or compares the published release. It never"
        " makes a second release in one attempt."
    )
