"""Every line job `release`'s step "Create the release" stops with is true of what it read, and
says where it read it.

The tester's finding of round rl9: with GitHub's listing one look behind, the step said "a
draft ... differs in its title" of a release that was public, "differs" of an edit a person had
already undone, and "there are 2 releases" when GitHub held one. Each look judged the listing's
row and stopped on it.

So a look still judges its row, to stop early, but a row that is not this run's is read by its
id before the step stops on it, and the line is written from that read; when the read shows this
run's release as it wrote it, the step stops on the row, with a line that says what each showed,
and writes nothing. A line that states what a release is (a draft or public, its title, its
notes, its files) is written from a read by its id and says so ("read by its id"); a line that
rests on what the listing shows says so ("GitHub's listing") and tells a person to look at the
releases page first. A line on the tag rests on the read of the tag. And a draft that does not
hold this run's files is said to be that, not "left unfinished" by an earlier attempt, which
the draft the attempt itself made and a person then changed is not.

gh is test_ci_release_retry_unit's stand-in."""

import re

import pytest
from test_ci_release_retry_unit import (
    CREATE,
    READ,
    README,
    RUN_ID,
    attached,
    cells,
    held,
    looks,
    made_earlier,
    run,
    the_error,
    writes,
)

DRAFT = made_earlier("v1.2.3", False, draft=True)  # this run's, id 77, left by an attempt
WHEEL = "cairn_memory-1.2.3-py3-none-any.whl"


# --- a row the listing shows is read by its id before the job stops on it ----------------------


BEHIND = (
    "::error::GitHub's listing shows the release for v1.2.3 without this run's mark, pre-release"
    " flag, title or notes, while read by its id it is {state} and has all four as this run"
    " wrote them; the listing can be seconds behind a write, an edit a person has since undone"
    " included, so look at the repository's releases page first, then re-run this job, which"
    " starts from what is listed then"
)


@pytest.mark.parametrize(
    ("ours", "state"),
    [(DRAFT, "a draft"), (made_earlier("v1.2.3", False), "public")],
    ids=["a draft", "public"],
)
@pytest.mark.parametrize(
    "shown",
    [
        {"name": "Spring"},
        {"body": "by hand\n"},
        {"prerelease": True},
    ],
    ids=["title", "mark and notes", "flag"],
)
def test_a_row_its_read_by_id_does_not_bear_out_stops_the_job_on_the_listing(
    tmp_path, ours, state, shown
):
    """The listing shows this run's release with a title, notes, a mark or a flag that, read by
    its id, it does not have (an edit a person has since undone): the job reads it by its id at
    once, and stops on the row with a line that says what each showed. Nothing is written and
    nothing waited for."""
    done, calls = run(
        tmp_path, "v1.2.3", "", ours, attached("v1.2.3"), stale=([{**ours, **shown}], None)
    )
    assert done.returncode == 1 and writes(calls) == []
    assert the_error(done) == BEHIND.format(state=state)
    cut = looks(calls)
    first = cut.index(next(line for line in cut if "--paginate" in line))
    assert cut[first + 1] == READ.format(id=ours["id"])
    assert held(tmp_path) == [ours]


DIFFERS = (
    "::error::read by its id, the GitHub release for v1.2.3, {state}, carries run"
    f" {RUN_ID}'s mark and differs from what that run wrote in its title; delete it on the"
    " repository's releases page if it is still a draft, or put back what the run wrote, and"
    " re-run this job"
)


def test_a_title_edited_just_before_the_publication_is_said_of_the_public_release(tmp_path):
    """The stated race: a person edits the title after the job's last read and before its
    publication, and the listing is one look behind the publication. The row still shows a
    draft; read by its id the release is public, and the line says so."""
    done, _ = run(
        tmp_path,
        "v1.2.3",
        "",
        CHANGED_BEFORE_PUBLICATION='.name = "Spring"',
        PUBLISHED_SHOWN_FROM_LOOK="2",
    )
    assert done.returncode == 1
    assert the_error(done) == DIFFERS.format(state="public")


def test_a_title_a_person_edits_on_the_draft_is_said_of_the_draft(tmp_path):
    done, _ = run(tmp_path, "v1.2.3", "", {**DRAFT, "name": "Spring"}, attached("v1.2.3"))
    assert done.returncode == 1
    assert the_error(done) == DIFFERS.format(state="a draft")


REFUSED = (
    "::error::read by its id, a GitHub release for v1.2.3 exists and is not one this run can"
    f" finish: its text does not carry run {RUN_ID}'s mark; look at it on the repository's"
    " releases page, delete it if nobody should publish it, and re-run this job"
)


def test_a_release_without_the_mark_is_refused_from_its_read_by_id(tmp_path):
    done, calls = run(tmp_path, "v1.2.3", "", {**DRAFT, "body": "by hand\n"}, attached("v1.2.3"))
    assert done.returncode == 1 and writes(calls) == []
    assert the_error(done) == REFUSED
    assert READ.format(id=77) in looks(calls)


ELSEWHERE = (
    "::error::the release GitHub's listing showed for v1.2.3 is, read by its id, a release of"
    " another tag; look at it on the repository's releases page, and re-run this job, which"
    " starts from what is listed then"
)


def test_a_release_whose_tag_a_person_changed_is_said_to_be_another_tag_s(tmp_path):
    done, calls = run(
        tmp_path,
        "v1.2.3",
        "",
        DRAFT,
        attached("v1.2.3"),
        AT_READ="1",
        AT_READ_CHANGE='.tag_name = "v9.9.9"',
    )
    assert done.returncode == 1 and writes(calls) == []
    assert the_error(done) == ELSEWHERE


GONE = (
    "::error::the GitHub release for v1.2.3, which GitHub's listing showed, was not found when"
    " read by its id, so it has probably been deleted; re-run this job, which starts from what"
    " is listed then"
)


def test_a_release_the_listing_shows_and_its_id_does_not_find_is_said_so(tmp_path):
    done, _ = run(tmp_path, "v1.2.3", "", DRAFT, attached("v1.2.3"), DELETED_BEFORE_READ="1")
    assert done.returncode == 1
    assert the_error(done) == GONE


# --- what rests on the listing says so ---------------------------------------------------------


CROWDED = (
    "::error::GitHub's listing shows 2 releases for v1.2.3, and this job finishes a release only"
    " when it is its tag's only one; the listing can be seconds behind a write, so look at the"
    " repository's releases page first: when it shows more than one, delete those nobody should"
    " publish, keeping a public release of this run that holds its files, and re-run this job"
)


def test_two_releases_the_listing_shows_are_said_to_be_the_listing_s(tmp_path):
    done, calls = run(tmp_path, "v1.2.3", "", [DRAFT, {**DRAFT, "id": 78}], attached("v1.2.3"))
    assert done.returncode == 1 and writes(calls) == []
    assert the_error(done) == CROWDED


UNSEEN = (
    "::error::GitHub's listing, at this job's last look, does not list the release for v1.2.3 as"
    " this job has just left it, and it can be seconds behind a write; look at the repository's"
    " releases page first, then re-run this job, which starts from what is listed then"
)


@pytest.mark.parametrize(
    "lag",
    [{"MADE_SHOWN_FROM_LOOK": "100"}, {"PUBLISHED_SHOWN_FROM_LOOK": "100"}],
    ids=["after the creation", "after the publication"],
)
def test_a_write_the_listing_does_not_show_is_said_to_be_the_listing_s(tmp_path, lag):
    done, _ = run(tmp_path, "v1.2.3", "", **lag)
    assert done.returncode == 1
    assert the_error(done) == UNSEEN


DRAFTED = (
    "::error::the GitHub release for v1.2.3, read by its id after this job published it, is a"
    " draft again; look at it on the repository's releases page, and re-run this job, which"
    " compares it and publishes it again if it is still this run's"
)


def test_a_release_read_as_a_draft_after_the_publication_is_said_from_that_read(tmp_path):
    """The listing shows the release published; read by its id it is a draft again, which a
    line on the listing ("does not list the release ... as this job has just left it") does not
    say."""
    done, _ = run(
        tmp_path,
        "v1.2.3",
        "",
        CHANGED_ONCE_PUBLIC=".draft = true",
        PERSON_LISTED_FROM_LOOK="100",
    )
    assert done.returncode == 1
    assert the_error(done) == DRAFTED


# --- a draft that does not hold this run's files ------------------------------------------------


UNFINISHED = (
    "::error::read by its id, the GitHub release for v1.2.3 is this run's draft and does not"
    " hold exactly the files this run attested: {why}; this job neither deletes nor publishes"
    " it; delete it on the repository's releases page and re-run this job, which makes the"
    " release again"
)


def test_a_draft_this_attempt_made_that_lost_a_file_is_not_said_left_by_an_earlier_one(tmp_path):
    """The tester's finding: the attempt that made the draft stopped on "an earlier attempt of
    run ... left unfinished". A person removes its wheel as soon as it is made."""
    done, _ = run(
        tmp_path, "v1.2.3", "", CHANGED_ONCE_MADE=f'.assets |= map(select(.name != "{WHEEL}"))'
    )
    assert done.returncode == 1
    names = f"SHA256SUMS {WHEEL} cairn_memory-1.2.3.tar.gz"
    assert the_error(done) == UNFINISHED.format(
        why=f"its files are [SHA256SUMS cairn_memory-1.2.3.tar.gz], not [{names}]"
    )


def test_a_draft_whose_wheel_holds_other_bytes_is_said_to_lack_the_run_s_files(tmp_path):
    assets = {**attached("v1.2.3"), WHEEL: b"a whee!"}  # other bytes, the same size
    done, _ = run(tmp_path, "v1.2.3", "", DRAFT, assets)
    assert done.returncode == 1
    assert the_error(done) == UNFINISHED.format(why=f"{WHEEL} is not the file this run attested")


# --- every line of the step, sorted by where it was read --------------------------------------


# Each ::error:: line of the step, by how it starts, and what it rests on: the listing, a read
# by id (with the fetches by asset id that read gives), or the read of the tag.
SORTED = [
    ("the repository's releases could not be listed", "listing"),
    ("GitHub's listing shows $count releases for", "listing"),
    ("GitHub's listing, at this job's last look, does not list", "listing"),
    ("GitHub's listing shows the release for $GITHUB_REF_NAME without", "by id"),
    ("read by its id, a GitHub release for $GITHUB_REF_NAME exists", "by id"),
    ("read by its id, the GitHub release for $GITHUB_REF_NAME is this run's draft", "by id"),
    ("the files of the GitHub release for $GITHUB_REF_NAME, $state as read by its id", "by id"),
    ("read by its id, the GitHub release for $GITHUB_REF_NAME, $state, carries", "by id"),
    ("the GitHub release for $GITHUB_REF_NAME, $state, changed between this job's two", "by id"),
    ("the GitHub release for $GITHUB_REF_NAME, read by its id after this job", "by id"),
    ("the release GitHub's listing showed for $GITHUB_REF_NAME is, read by its id", "by id"),
    ("the GitHub release for $GITHUB_REF_NAME, which GitHub's listing showed, was", "by id"),
    ("the GitHub release for $GITHUB_REF_NAME could not be read by its id", "by id"),
    ("the GitHub release for $GITHUB_REF_NAME, read by its id, is this run's and", "by id"),
    ("the tag $GITHUB_REF_NAME could not be read as a commit", "tag"),
    ("the tag $GITHUB_REF_NAME names commit", "tag"),
]


def error_lines() -> list[str]:
    found = re.findall(r'"::error::([^"]*)"', CREATE["run"])
    return found


def test_every_line_of_the_step_is_sorted_and_says_where_it_was_read():
    lines = error_lines()
    starts = [start for start, _ in SORTED]
    assert sorted(next(s for s in starts if line.startswith(s)) for line in lines) == sorted(
        starts
    ), lines
    for line in lines:
        (rests,) = [r for s, r in SORTED if line.startswith(s)]
        if rests == "listing":
            assert "listing" in line or "listed" in line, line
            assert "by its id" not in line, line
            if "GitHub's listing" in line:  # it states what the listing shows
                assert "releases page first" in line, line
        elif rests == "by id":
            assert "by its id" in line, line
        else:
            assert "the tag $GITHUB_REF_NAME" in line, line


def test_what_a_release_is_is_said_only_from_a_read_by_its_id():
    """A draft or public, its title, its notes or its files: what each line states, before its
    advice."""
    for line in error_lines():
        said = line.split(";")[0]
        if re.search(r"\$state|differs|its files are|is a draft|public", said):
            assert "by its id" in said, line


# --- the documents --------------------------------------------------------------------------


def test_the_readme_says_what_the_row_decides_and_what_a_read_by_id_decides():
    readme = " ".join(README.split())
    assert "what the job compares it reads by the release's id" not in readme
    assert "every look stops at once on a mark, a flag, a title or notes" not in readme
    assert (
        "Each look judges the listing's row only to stop early: a row that does not show this"
        " run's release as the job wrote it is read by the release's id at once, and the job"
        " stops on what that read shows, or on the row, with a line that says what each showed,"
        " when the read shows this run's release as the job wrote it. What is published, and"
        " what the job succeeds on, is judged on reads by the release's id,"
    ) in readme
    assert (
        "A line the job stops with that says what a release is (a draft or public, its title,"
        " its notes, its files) is written from a read by its id and says so; a line that rests"
        " on what the listing shows says so too, and sends a person to the releases page first."
    ) in readme
    assert (
        "Nothing else is waited for: a look whose row shows a mark, a flag, a title or notes"
        " that are not this run's reads the release by its id at once, and the job stops, on"
        " what that read shows or on the row, and the first look of a run never waits."
    ) in readme


def test_the_readme_says_what_a_tag_moved_in_the_race_does():
    readme = " ".join(README.split())
    assert "moved while the job ran, or before a re-run, is not published" not in readme
    assert (
        "so a tag a person deleted or moved while the job ran, up to that last read before the"
        " publication, or before a re-run, is not published"
    ) in readme
    assert (
        "A person who moves the tag in that moment has the release published, on the moved"
        " tag, and the job's read of the tag after the publication then stops it with the line"
        " of a moved tag, which says to delete this run's release if nobody should publish it."
    ) in readme


@pytest.mark.parametrize(
    "row",
    [
        "| `GitHub's listing shows … releases for …, and this job finishes a release only when it"
        " is its tag's only one` |",
        "| `GitHub's listing, at this job's last look, does not list the release for … as this"
        " job has just left it, and it can be seconds behind a write` |",
        "| `read by its id, a GitHub release for … exists and is not one this run can finish: …` |",
        "| `read by its id, the GitHub release for … is this run's draft and does not hold exactly"
        " the files this run attested: …` |",
        "| `the files of the GitHub release for …, … as read by its id, could not be downloaded,",
        "| `read by its id, the GitHub release for …, …, carries run …'s mark and differs from",
        "| `the GitHub release for …, read by its id after this job published it, is a draft"
        " again` |",
        "| `the release GitHub's listing showed for … is, read by its id, a release of another"
        " tag` |",
        "| `the GitHub release for …, which GitHub's listing showed, was not found when read by its"
        " id, so it has probably been deleted` |",
        "| `GitHub's listing shows the release for … without this run's mark, pre-release flag,"
        " title or notes, while read by its id it is … and has all four as this run wrote them` |",
    ],
)
def test_the_readme_s_table_has_each_line_as_the_step_writes_it(row):
    assert row in " ".join(README.split())


def test_the_row_the_read_by_id_does_not_bear_out_has_its_row():
    means, next_step = cells(
        "GitHub's listing shows the release for … without this run's mark, pre-release flag,"
        " title or notes, while read by its id it is … and has all four as this run wrote them"
    )
    assert means == (
        "The listing showed the release with a mark, a flag, a title or notes that, read by its"
        " id at once, it does not have: the listing was behind, an edit a person has undone for"
        " one; the job wrote nothing after that look"
    )
    assert next_step == (
        "Look at the release on the repository's releases page first; when it is this run's as"
        " the job wrote it, re-run the job, which starts from what is listed then."
    )


def test_the_readme_s_table_no_longer_has_the_lines_that_were_not_true():
    readme = " ".join(README.split())
    assert "is a draft that an earlier attempt of run … left unfinished" not in readme
    assert "| `GitHub does not list the release for … as this job has just left it` |" not in readme
    assert "or the release, read by its id at the end, was a draft again" not in readme


def test_the_step_s_comment_says_the_row_only_sends_the_job_to_a_read_by_id():
    comment = " ".join(CREATE["run"].replace("#", "").split())
    assert "so nothing of the release is compared on it" not in comment
    assert "Each look still stops at once on what it shows" not in comment
    for said in (
        "A look judges its row only to stop early: two rows for the tag stop the job on the"
        " listing, which its line says, and a row that does not show this run's release as it"
        " wrote it is read by its id at once, and the job stops on what that read shows, or on"
        " the row when that read shows this run's release as it wrote it, its line saying both.",
        "so its row only stops the job or sends it to a read by id at once, nothing is published"
        " or said finished on it,",
        "A look whose row shows a missing mark, another flag, another title or other notes reads"
        " the release by its id at once, in a wait too, and the job stops on what that read"
        " shows, or on the row when that read shows this run's release as it wrote it.",
        "A tag a person moves in that moment is published on, and the read of the tag after the"
        " publication stops the job with its line.",
    ):
        assert said in comment, said
