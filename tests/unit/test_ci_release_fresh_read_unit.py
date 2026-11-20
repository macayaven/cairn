"""Job `release` judges a release on a read by its id, never on the listing (sixth report,
must-fix 2).

The fault: the listing of releases can be behind a write, and the step waits for it to show
the step's own. It is as far behind a person's write. With a first listing that still showed
this run's matching draft while the draft was already edited, the step downloaded the files,
published the edited release, then saw the difference and failed; with a release already
public and edited, it downloaded matching files and exited zero. And one state was waited for
that should have stopped it: a draft short of file names with a wrong title, which went on
when a later listing showed the title restored.

Now the listing finds the release and shows whether the tag has more than one. What is
compared is read by the release's id, which was measured to show each write at once: once
after the listing gave the id, before any download; again as the last read of the release
before the publication; and again as the last before the step says it is finished. Only the
tag's read comes after each (test_ci_release_tag_unit), and the tests here leave it out. The
mark, the tag, the two flags, the title, the notes and the file names are judged on each such
read.
Every look, a wait's among them, still stops at once on a missing mark, another flag, another
title or other notes. A read that fails stops the step in a line of its own, one for a release
that is not found and one for a read that got no answer, and nothing is written after it.

gh is test_ci_release_retry_unit's stand-in: a read by id answers from what is there, and the
listing can show another record for k looks, behind the step's write or behind a person's."""

import json

import pytest
import test_ci_release_retry_unit as retry
from test_ci_release_listing_lag_unit import APART, LOOKS, WHEEL, slept
from test_ci_release_notes_title_unit import DIFFERS, WROTE
from test_ci_release_retry_unit import (
    CREATE,
    DRAFT,
    LISTING,
    MARK,
    README,
    REFUSED,
    attached,
    cells,
    fetches,
    held,
    looks,
    made_earlier,
    the_error,
    untagged,
    writes,
)

BOTH = [("v1.2.3", ""), ("v1.2.3-rc.1", "true")]
EDITS = {
    "notes": {"body": f"other notes\n\n{MARK}\n"},
    "title": {"name": "Spring"},
    "the mark": {"body": "the notes\n"},
    "the flag": {"prerelease": None},  # the other one, set where it is used
}
READ = "gh api repos/owner/repo/releases/{id} --jq "
NOT_FOUND = (
    "::error::the GitHub release for {tag}, which the listing showed, was not found when read"
    " by its id, so it has probably been deleted; re-run this job, which starts from what is"
    " listed then"
)
NO_ANSWER = (
    "::error::the GitHub release for {tag} could not be read by its id, as the lines above"
    " say, so nothing was compared; re-run this job, and delete nothing"
)
STALE = (
    "::error::GitHub does not list the release for {tag} as this job has just left it; re-run"
    " this job, which starts from what is listed then"
)


def run(*args, **env):
    """test_ci_release_retry_unit's run, its calls without the tag's reads."""
    done, calls = retry.run(*args, **env)
    return done, untagged(calls)


def attempt(*args, **env):
    done, calls = retry.attempt(*args, **env)
    return done, untagged(calls)


def reads(calls: list[str]) -> list[int]:
    """Where in CALLS the reads by id stand."""
    return [
        n for n, line in enumerate(calls) if line.startswith("gh api repos/owner/repo/releases/")
    ]


def edited(release: dict, what: str) -> dict:
    change = dict(EDITS[what])
    if what == "the flag":
        change["prerelease"] = not release["prerelease"]
    return {**release, **change}


def said(tag: str, state: str, what: str) -> str:
    """The line an edit of WHAT stops the step with."""
    if what in ("notes", "title"):
        return DIFFERS.format(tag=tag, state=state, what=what)
    flag = "true, not false" if "-rc." not in tag else "false, not true"
    why = {
        "the mark": "its text does not carry run 4242's mark",
        "the flag": f"its pre-release flag is {flag}",
    }[what]
    return REFUSED.format(tag=tag) + why


# --- the reviewer's two inputs: the listing behind a person's edit --------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("what", EDITS)
@pytest.mark.parametrize("behind_until", [None, 2], ids=["at every look", "at the first look"])
def test_an_edited_draft_the_listing_still_shows_as_it_was_is_not_published(
    tmp_path, tag, candidate, what, behind_until
):
    """The listing shows this run's draft as the run made it; read by its id, the draft is
    edited. The step used to download its files and publish it. It stops before the
    publication, on the read, in the line of that edit, and has written nothing."""
    matching = made_earlier(tag, candidate == "true", draft=True)
    existing = edited(matching, what)
    done, calls = run(
        tmp_path, tag, candidate, existing, attached(tag), stale=([matching], behind_until)
    )
    assert done.returncode == 1
    assert the_error(done).startswith(said(tag, "a draft", what))
    assert writes(calls) == [] and held(tmp_path) == [existing]
    assert not any(line.startswith("gh release download") for line in calls)
    assert looks(calls)[0] == LISTING and reads(calls) == [1] and len(calls) == 2


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("what", EDITS)
def test_an_edited_public_release_the_listing_still_shows_as_it_was_is_not_finished(
    tmp_path, tag, candidate, what
):
    """The same with a release an earlier attempt published: the step used to download the
    files, which match, and exit zero, with a release tag's `aliases` after it."""
    matching = made_earlier(tag, candidate == "true")
    existing = edited(matching, what)
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag), stale=([matching], None))
    assert done.returncode == 1
    assert the_error(done).startswith(said(tag, "public", what))
    assert writes(calls) == [] and held(tmp_path) == [existing]
    assert "is this run's and is published" not in done.stdout


@pytest.mark.parametrize("what", EDITS)
def test_a_draft_edited_between_the_first_read_and_the_publication_is_not_published(tmp_path, what):
    """One step past: the edit comes after the read that follows the listing, while the files
    are fetched. The read after the fetches, the last of the release before the publication,
    sees it."""
    existing = made_earlier("v1.2.3", False, draft=True)
    change = {
        "notes": '.body |= sub("the notes"; "other notes")',
        "title": '.name = "Spring"',
        "the mark": '.body = "the notes\\n"',
        "the flag": ".prerelease = true",
    }[what]
    done, calls = run(
        tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), AT_READ="2", AT_READ_CHANGE=change
    )
    assert done.returncode == 1
    assert the_error(done).startswith(said("v1.2.3", "a draft", what))
    assert writes(calls) == [] and held(tmp_path)[0]["draft"] is True
    assert reads(calls) == [1, 5] and calls[2:5] == fetches(77)


def test_a_file_removed_from_a_draft_the_listing_still_shows_whole_is_not_downloaded(tmp_path):
    """The file names too are judged on the read: listed whole, the draft holds two files."""
    matching = made_earlier("v1.2.3", False, draft=True)
    existing = made_earlier("v1.2.3", False, draft=True, names=["SHA256SUMS", WHEEL])
    done, calls = run(tmp_path, "v1.2.3", "", existing, {}, stale=([matching], None))
    assert done.returncode == 1 and the_error(done) == DRAFT.format(tag="v1.2.3")
    assert writes(calls) == [] and reads(calls) == [1] and len(calls) == 2


# --- the listing behind the step's own write: the read decides, and the step succeeds -------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_a_whole_draft_the_listing_never_shows_with_its_third_name_is_published_after_the_wait(
    tmp_path, tag, candidate
):
    """The other direction. The step waits its ten looks for the listing to name the three
    files; then the read by id says what the draft holds, and a whole draft is compared and
    published. It used to end on the unfinished line, which says to delete it."""
    done, calls = run(tmp_path, tag, candidate, NAMES_LISTED_AT_FIRST=json.dumps([WHEEL]))
    assert done.returncode == 0, done.stdout + done.stderr
    assert slept(calls) == [f"sleep {APART}"] * (LOOKS - 1)
    assert [(r["id"], r["draft"]) for r in held(tmp_path)] == [(88, False)]


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_a_draft_an_earlier_attempt_left_whole_and_listed_short_is_published(
    tmp_path, tag, candidate
):
    """Found by the first look, which never waits: the listing is short of a name, the read
    is not, and the step compares and publishes."""
    existing = made_earlier(tag, candidate == "true", draft=True)
    short = {**existing, "assets": existing["assets"][:2]}
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag), stale=([short], 2))
    assert done.returncode == 0, done.stdout + done.stderr
    assert slept(calls) == [] and held(tmp_path) == [{**existing, "draft": False}]


# --- where the reads stand ------------------------------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_the_read_by_id_is_the_last_read_of_the_release_before_the_publication_and_success(
    tmp_path, tag, candidate
):
    done, calls = run(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    read = READ.format(id=88)
    seen = [read if line.startswith(read) else line for line in looks(calls)]
    assert seen[2:] == [
        LISTING,
        read,
        *fetches(88),
        read,
        writes(calls)[-1],
        LISTING,
        read,
        *fetches(88),
        read,
    ]
    assert writes(calls)[-1].startswith("gh api --method PATCH repos/owner/repo/releases/88 ")
    # The read asks for one release by the id the listing gave, with the listing's own fields
    # first, and then what the fetches and the comparison of two reads take.
    (listing,) = {line for line in calls if line.startswith(LISTING)}
    (asked,) = {line for line in calls if line.startswith(read)}
    fields = listing.split(" then (", 1)[1].rsplit(") elif ", 1)[0]
    assert asked.startswith(read + f'select(.tag_name == "{tag}") | ({fields}), ')


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_a_release_found_public_is_read_by_id_before_the_step_says_it_is_finished(
    tmp_path, tag, candidate
):
    existing = made_earlier(tag, candidate == "true")
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag))
    assert done.returncode == 0, done.stdout + done.stderr
    assert writes(calls) == [] and reads(calls) == [1, 5] and len(calls) == 6
    assert calls[2:5] == fetches(77)


# --- three states of one release the two sources can disagree on ----------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_a_release_listed_public_that_is_a_draft_by_its_id_is_this_run_s_draft(
    tmp_path, tag, candidate
):
    """Listed as published, and a draft when read: the read is what is there. It is this
    run's draft, whole, so the step compares it and publishes it, as it does any draft of its
    own; it does not take the listing's word that there is nothing to do."""
    existing = made_earlier(tag, candidate == "true", draft=True)
    done, calls = run(
        tmp_path, tag, candidate, existing, attached(tag), stale=([{**existing, "draft": False}], 2)
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(writes(calls)) == 1 and held(tmp_path) == [{**existing, "draft": False}]


@pytest.mark.parametrize("what", ["notes", "title", "the mark", "the flag"])
def test_a_draft_edited_as_soon_as_it_is_made_while_the_listing_shows_it_as_made_is_not_published(
    tmp_path, what
):
    """One step past: the edit follows the step's own creation, and the listing, behind the
    edit and not behind the creation, shows the whole draft as the step made it. No look has
    anything to stop on or to wait for; the first read by id stops the step, before any
    download."""
    change = {
        "notes": '.body |= sub("the notes"; "other notes")',
        "title": '.name = "Spring"',
        "the mark": '.body = "the notes\\n"',
        "the flag": ".prerelease = true",
    }[what]
    done, calls = run(tmp_path, "v1.2.3", "", CHANGED_ONCE_MADE=change, PERSON_LISTED_FROM_LOOK="9")
    assert done.returncode == 1
    assert the_error(done).startswith(said("v1.2.3", "a draft", what))
    assert slept(calls) == [] and len(writes(calls)) == 1 and held(tmp_path)[0]["draft"] is True
    assert reads(calls) == [3] and len(calls) == 4


def test_a_release_listed_public_and_matching_that_is_an_edited_draft_is_not_published(tmp_path):
    """Both at once: the listing says published and matching; read by its id the release is
    a draft again, and edited. The read is what is there: a draft that differs."""
    matching = made_earlier("v1.2.3", False)
    existing = {**matching, "draft": True, "body": f"other notes\n\n{MARK}\n"}
    done, calls = run(
        tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), stale=([matching], None)
    )
    assert done.returncode == 1 and writes(calls) == [] and held(tmp_path) == [existing]
    assert the_error(done) == DIFFERS.format(tag="v1.2.3", state="a draft", what="notes")


def test_a_release_whose_tag_was_changed_is_not_this_tag_s_whatever_the_listing_shows(tmp_path):
    """One step past: the listing still shows the draft under this tag; read by its id, it is
    another tag's. Publishing it would publish that tag's release."""
    matching = made_earlier("v1.2.3", False, draft=True)
    existing = {**matching, "tag_name": "v9.9.9"}
    done, calls = run(
        tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), stale=([matching], None)
    )
    assert done.returncode == 1 and writes(calls) == [] and held(tmp_path) == [existing]
    assert the_error(done).startswith(
        REFUSED.format(tag="v1.2.3") + "read by its id it is not a release for this tag"
    )


def test_a_release_returned_to_a_draft_after_the_publication_is_not_finished(tmp_path):
    """The listing shows the publication; a person makes the release a draft again before the
    last read. The step does not say it is published."""
    existing = made_earlier("v1.2.3", False, draft=True)
    done, calls = run(
        tmp_path,
        "v1.2.3",
        "",
        existing,
        attached("v1.2.3"),
        AT_READ="3",
        AT_READ_CHANGE=".draft = true",
        PERSON_LISTED_FROM_LOOK="5",
    )
    assert done.returncode == 1 and the_error(done) == STALE.format(tag="v1.2.3")
    assert "is this run's and is published" not in done.stdout and len(writes(calls)) == 1


# --- every look judges what it sees ---------------------------------------------------------------


def test_a_draft_short_of_names_with_a_wrong_title_stops_at_the_first_look_it_is_seen(tmp_path):
    """The reviewer's third input. The draft the step has just made is listed with one file
    name and another title; two looks later a person has put the title back and the names are
    the three. The step used to wait for the names and go on. It stops at the first look."""
    done, calls = run(
        tmp_path,
        "v1.2.3",
        "",
        CHANGED_ONCE_MADE='.name = "Spring"',
        NAMES_LISTED_AT_FIRST=json.dumps([WHEEL]),
        NAMES_LISTED_FOR_LOOKS="2",
        AT_LOOK="3",
        AT_LOOK_CHANGE='.name = "v1.2.3"',
    )
    assert done.returncode == 1
    assert the_error(done) == DIFFERS.format(tag="v1.2.3", state="a draft", what="title")
    assert slept(calls) == [] and len(writes(calls)) == 1 and held(tmp_path)[0]["draft"] is True
    assert reads(calls) == []


@pytest.mark.parametrize("what", ["notes", "title", "the mark", "the flag"])
@pytest.mark.parametrize("wait", ["made", "published"])
def test_every_look_of_a_wait_stops_on_an_edit_whatever_the_wait_was_for(tmp_path, what, wait):
    """In the wait for the names after the creation, and in the wait for the listing to show
    the publication: the third look shows the edit, and the step stops there."""
    change = {
        "notes": '.body |= sub("the notes"; "other notes")',
        "title": '.name = "Spring"',
        "the mark": '.body = "the notes\\n"',
        "the flag": ".prerelease = true",
    }[what]
    env = {"AT_LOOK": "3", "AT_LOOK_CHANGE": change}
    if wait == "made":  # the third look after the creation
        env["NAMES_LISTED_AT_FIRST"] = json.dumps([WHEEL])
    else:  # the second after the publication: still listed as a draft, and edited
        env |= {"PUBLISHED_SHOWN_FROM_LOOK": "9", "PERSON_SHOWS_BEHIND": "1"}
    done, calls = run(tmp_path, "v1.2.3", "", **env)
    assert done.returncode == 1
    assert the_error(done).startswith(said("v1.2.3", "a draft", what))
    assert len(writes(calls)) == (1 if wait == "made" else 2)
    assert len(slept(calls)) == (2 if wait == "made" else 1)


# --- a read that fails ----------------------------------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("at", [1, 2, 3])
@pytest.mark.parametrize(
    ("knob", "line"), [("DELETED_BEFORE_READ", NOT_FOUND), ("FAILS_AT_READ", NO_ANSWER)]
)
def test_a_read_that_fails_stops_the_step_in_a_line_that_says_which_and_nothing_is_written_after(
    tmp_path, tag, candidate, at, knob, line
):
    """At each of the three reads of a first run: before the download, before the publication,
    before success. A release that is not found has probably been deleted; a read that got no
    answer says nothing about the release. Neither is a release that differs."""
    done, calls = run(tmp_path, tag, candidate, **{knob: str(at)})
    assert done.returncode == 1
    assert the_error(done) == line.format(tag=tag)
    assert reads(calls)[-1] == len(calls) - 1 and len(reads(calls)) == at
    assert len(writes(calls)) == (1 if at < 3 else 2)
    assert "is this run's and is published" not in done.stdout
    # gh's own words stay in the log, above the line.
    assert ("HTTP 404" if knob == "DELETED_BEFORE_READ" else "HTTP 502") in done.stderr
    if knob == "FAILS_AT_READ":  # a plain re-run finishes
        done, calls = attempt(tmp_path, tag, candidate)
        assert done.returncode == 0, done.stdout + done.stderr
        assert [(r["id"], r["draft"]) for r in held(tmp_path)] == [(88, False)]


def test_the_two_lines_have_their_rows_and_neither_tells_anyone_to_delete():
    for line in (NOT_FOUND, NO_ANSWER):
        assert "delete it" not in line and ". " not in line
    assert cells(
        "the GitHub release for …, which the listing showed, was not found when read by its id,"
        " so it has probably been deleted"
    ) == (
        "The listing gave the job a release for the tag, and GitHub answered 404 for that id: a"
        " person deleted it meanwhile, or the listing was behind the deletion",
        "Re-run the job, which starts from what is listed then.",
    )
    assert cells(
        "the GitHub release for … could not be read by its id, as the lines above say, so"
        " nothing was compared"
    ) == (
        "GitHub did not answer the job's read of the release (the CLI's own line above the"
        " error names the status): that says nothing about the release",
        "Re-run the job. Delete nothing.",
    )


def test_the_step_s_comment_and_the_readme_say_what_is_read_where():
    comment = " ".join(CREATE["run"].replace("#", "").split())
    assert (
        "(b) The listing, which finds the release and shows whether the tag has more than one;"
        " it can be behind a write, a person's as much as this job's, so nothing is compared on"
        " it. (c) The release, read by its id, which was measured to show each write at once:"
        " it judges the tag, the mark, the two flags, the title, the notes and the file names,"
        " and gives each file's asset id, state and size."
    ) in comment
    assert (
        "(e) The release by its id again, which must be as (c) showed it: the same assets (ids,"
        " names, sizes, update times), title, notes and flags, so no file was replaced and"
        " nothing edited while the files were fetched. (f) The tag again. Then the publication;"
        " then (b) until the listing shows it, and (c) to (f) once more before this step says"
        " the release is finished."
    ) in comment
    assert (
        "What a person changes after the last read of the release, before the publication or"
        " before this step ends, is not seen: that moment is the stated race."
    ) in comment
    readme = " ".join(README.split())
    assert (
        "The listing only finds the release and shows whether the tag has more than one: what"
        " the job compares it reads by the release's id, which shows a write at once where the"
        " listing can be seconds behind one, a person's as much as the job's. It reads it after"
        " the look that gave the id, as its last read of the release before the publication,"
        " and as its last before it succeeds."
    ) in readme
    assert WROTE.endswith("\n")
