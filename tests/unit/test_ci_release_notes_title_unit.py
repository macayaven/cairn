"""Job `release` takes a release for its tag as this run's only when its title is the tag (or
there is none) and its notes are, byte for byte, the text the run wrote, and publishes nothing
else (fifth report, must-fix 2).

The fault: the step knew its release by the run's mark, an HTML comment in the notes, and by
the three files, names and bytes. It compared neither the notes around the mark nor the title,
so a draft whose notes a person had edited, the mark kept, was published by a re-run, and a
published one passed as finished, with a release tag's `aliases` after it.

Now each look reads the notes from the listing it already makes, as base64, and the step
compares them, as files, with the text it wrote: the version's notes, an empty line and the
mark. Nothing is normalised: a line end written CR LF, a space or a line end more or less at
the end, are differences. The title is read in the same listing: it must be the tag, exactly,
or missing, null or empty, since an empty title is not an edited one. The step
compares before it publishes a draft, and again on what the listing shows after the
publication. A release that differs stops the job in one line that says whether it is a draft
or public and what differs, and the job writes nothing more.

gh is test_ci_release_retry_unit's stand-in: it keeps the releases as a JSON file, runs the
step's own --jq expression with jq, and lets a person change a release just before the
publication arrives, or just after it."""

import json

import pytest
from test_ci_release_retry_unit import (
    CREATE,
    MARK,
    README,
    RUN_ID,
    attached,
    attempt,
    by_hand,
    cells,
    held,
    made_earlier,
    run,
    setup,
    the_error,
    writes,
)
from test_ci_workflow_unit import RAW_WORKFLOW, TAG, jobs_that_run

WROTE = f"the notes\n\n{MARK}\n"  # as test_ci_release_retry_unit's files give it: notes.md, then
EDITED = f"other notes\n\n{MARK}\n"  # the mark; and a person's text around the same mark
DIFFERS = (
    "::error::read by its id, the GitHub release for {tag}, {state}, carries run "
    + RUN_ID
    + "'s mark and differs from what that run wrote in its {what}; delete it on the"
    " repository's releases page if it is still a draft, or put back what the run wrote, and"
    " re-run this job"
)
BOTH = [("v1.2.3", ""), ("v1.2.3-rc.1", "true")]
STATES = [(True, "a draft"), (False, "public")]


def put(tmp_path, **fields) -> None:
    """A person, on the releases page or through the API: every release gets FIELDS."""
    after = [{**release, **fields} for release in held(tmp_path)]
    (tmp_path / "existing" / "releases.json").write_text(json.dumps(after))


# --- the notes, before anything is published -----------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize(("draft", "state"), STATES)
def test_a_release_whose_notes_were_edited_around_the_mark_stops_the_job_unwritten(
    tmp_path, tag, candidate, draft, state
):
    """The reviewer's input: the visible text changed, the hidden mark kept, the files whole.
    A draft is not published; a published one is not taken for finished."""
    existing = made_earlier(tag, candidate == "true", draft=draft, body=EDITED)
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag))
    assert done.returncode == 1
    assert the_error(done) == DIFFERS.format(tag=tag, state=state, what="notes")
    assert writes(calls) == [] and held(tmp_path) == [existing]
    assert not any(line.startswith("gh release download") for line in calls)


@pytest.mark.parametrize(("draft", "state"), STATES)
@pytest.mark.parametrize(
    "body",
    [
        WROTE.replace("\n", "\r\n"),
        WROTE.replace("\n", "\r"),
        WROTE.replace("the notes\n", "the notes\r\n"),
        WROTE + "\n",
        WROTE + " ",
        WROTE + "\t",
        WROTE[:-1],
        WROTE[:-1] + "\r\n",
        WROTE.replace("the notes\n", "the notes \n"),
        " " + WROTE,
        "\n" + WROTE,
        WROTE.replace("the notes", "the\tnotes"),
        WROTE.replace(MARK, MARK + " "),
        WROTE.replace("\n\n", "\n"),
        WROTE.replace("the notes", "the n\u00f6tes"),
        MARK,
        f"{WROTE}{WROTE}",
    ],
    ids=[
        "every line end CR LF",
        "every line end CR",
        "one line end CR LF",
        "a line end more at the end",
        "a space at the end",
        "a tab at the end",
        "no line end at the end",
        "CR LF at the end",
        "a space at the end of a line",
        "a space before the text",
        "a line end before the text",
        "a tab for a space",
        "a space after the mark",
        "no empty line before the mark",
        "one letter",
        "the mark alone",
        "the text twice",
    ],
)
def test_the_notes_are_compared_byte_for_byte_and_nothing_is_normalised(
    tmp_path, draft, state, body
):
    """Each of these is a difference: line ends, and white space at the end, among them. What
    the step wrote comes back from GitHub byte for byte (the coordinator's measurement on a
    draft and on the same release published), so its own release always compares equal."""
    existing = made_earlier("v1.2.3", False, draft=draft, body=body)
    done, calls = run(tmp_path, "v1.2.3", "", existing, attached("v1.2.3"))
    assert done.returncode == 1
    assert the_error(done) == DIFFERS.format(tag="v1.2.3", state=state, what="notes")
    assert writes(calls) == [] and held(tmp_path) == [existing]


@pytest.mark.parametrize(
    "notes",
    [
        "the notes",
        "the notes\n\n",
        "- `code`, \"quotes\", 'more', a \\ and a $HOME\n\n\n",
        "ends in a space \n",
        "\tbegins with a tab and holds \u00e9, \u2028 and \U0001f600\n",
        "",
    ],
    ids=[
        "no line end",
        "two line ends",
        "the shell's characters",
        "a space at the end",
        "wider characters",
        "empty",
    ],
)
@pytest.mark.parametrize("existing", [None, "draft", "published"])
def test_the_step_s_own_text_compares_equal_whatever_the_notes_hold(tmp_path, notes, existing):
    """One step past: the comparison must not refuse the step's own release because of what
    the notes hold or how they end. The text is `package`'s notes.md, whatever its bytes, then
    a line end, the mark and a line end; the step's reading drops or changes none of them."""
    wrote = f"{notes}\n{MARK}\n"
    held_ = None
    if existing:
        held_ = made_earlier("v1.2.3", False, draft=existing == "draft", body=wrote)
    setup(tmp_path, "v1.2.3", held_, attached("v1.2.3") if existing else None)
    (tmp_path / "release" / "notes.md").write_bytes(notes.encode())
    done, _ = attempt(tmp_path, "v1.2.3", "")
    assert done.returncode == 0, done.stdout + done.stderr
    (published,) = held(tmp_path)
    assert (published["draft"], published["body"]) == (False, wrote)
    # And one byte of it changed by hand is seen.
    put(tmp_path, body=wrote + " ")
    done, calls = attempt(tmp_path, "v1.2.3", "")
    assert done.returncode == 1 and writes(calls) == []
    assert the_error(done) == DIFFERS.format(tag="v1.2.3", state="public", what="notes")


# --- between the comparison and the publication, and after it ------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("existing", [None, "draft"], ids=["made now", "left by an attempt"])
@pytest.mark.parametrize(
    "moment", ["CHANGED_BEFORE_PUBLICATION", "CHANGED_ONCE_PUBLIC"], ids=["before", "after"]
)
def test_notes_edited_after_the_comparison_are_seen_by_the_second_and_the_release_is_public(
    tmp_path, tag, candidate, existing, moment
):
    """The stated limit, kept true: no request compares and publishes at once. A person edits
    the notes after the step compared them, just before the publication arrives or just after
    it. The release is public as changed; the step looks again, stops, says it is public and
    that its notes differ, and writes nothing after. The re-run says the same and writes
    nothing, until the text the run wrote is put back: then it succeeds, unwritten."""
    held_ = made_earlier(tag, candidate == "true", draft=True) if existing else None
    done, calls = run(
        tmp_path,
        tag,
        candidate,
        held_,
        attached(tag) if existing else None,
        **{moment: '.body |= sub("the notes"; "other notes")'},
    )
    assert done.returncode == 1
    assert the_error(done) == DIFFERS.format(tag=tag, state="public", what="notes")
    assert writes(calls)[-1].startswith("gh api --method PATCH ")
    # then one look, the read by id its row sends the job to, and no more
    assert calls.index(writes(calls)[-1]) == len(calls) - 3
    assert calls[-1].startswith("gh api repos/owner/repo/releases/")
    (public,) = held(tmp_path)
    assert (public["draft"], public["body"]) == (False, EDITED)
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 1 and writes(calls) == [] and held(tmp_path) == [public]
    assert the_error(done) == DIFFERS.format(tag=tag, state="public", what="notes")
    put(tmp_path, body=WROTE)
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    assert writes(calls) == [] and held(tmp_path) == [{**public, "body": WROTE}]


# --- what the line tells a person to do, done ----------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("remedy", ["delete", "put back"])
def test_the_edited_draft_deleted_is_made_again_and_put_back_is_published(
    tmp_path, tag, candidate, remedy
):
    existing = made_earlier(tag, candidate == "true", draft=True, body=EDITED)
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag))
    assert done.returncode == 1 and writes(calls) == []
    if remedy == "delete":
        by_hand(tmp_path, "delete")
    else:
        put(tmp_path, body=WROTE)
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    (published,) = held(tmp_path)
    assert published["id"] == (88 if remedy == "delete" else 77)
    assert (published["draft"], published["name"], published["body"]) == (False, tag, WROTE)
    assert [line.split()[1:3] for line in writes(calls)] == (
        [["release", "create"], ["api", "--method"]]
        if remedy == "delete"
        else [["api", "--method"]]
    )


# --- the title ------------------------------------------------------------------------------------

ABSENT = object()


def titled(release: dict, name) -> dict:
    """RELEASE with the title NAME; ABSENT is a reply that holds no `name` at all."""
    if name is ABSENT:
        return {key: value for key, value in release.items() if key != "name"}
    return {**release, "name": name}


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("draft", [True, False], ids=["draft", "published"])
@pytest.mark.parametrize("name", [ABSENT, None, ""], ids=["absent", "null", "empty"])
def test_a_release_with_no_title_counts_as_titled_with_the_tag(
    tmp_path, tag, candidate, draft, name
):
    """An empty title is not an edited one: a reply with no `name`, with null or with an empty
    one is finished like one titled with the tag, and its title is left as it is. (The
    coordinator's measurement of 2026-11-20: a title set to empty through GitHub's API reads
    back as the empty string.)"""
    existing = titled(made_earlier(tag, candidate == "true", draft=draft), name)
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag))
    assert done.returncode == 0, done.stdout + done.stderr
    assert held(tmp_path) == [{**existing, "draft": False}]
    assert len(writes(calls)) == (1 if draft else 0)


@pytest.mark.parametrize(("draft", "state"), STATES)
@pytest.mark.parametrize(
    "name",
    [
        "v1.2.3 (edited)",
        "v1.2.3 ",
        " v1.2.3",
        "V1.2.3",
        "1.2.3",
        "v1.2.3-rc.1",
        "v1.2.30",
        "v1.2.3\n",
        "v1.2.3\t",
        " ",
        "null",
        "false",
        'a "quoted" title',
        "$(touch injected) `touch injected` $GITHUB_REF_NAME",
        "::error::not this job's line",
        0,
        False,
    ],
)
def test_any_other_title_stops_the_job_unwritten(tmp_path, draft, state, name):
    """One step past the reviewer's `v1.2.3 (edited)`: the tag with white space beside it, in
    another case, a longer or shorter version, the words a shell or jq could read, and values
    that are not text. The title is compared in the listing's own expression, so it is never
    printed nor handed to the shell."""
    existing = titled(made_earlier("v1.2.3", False, draft=draft), name)
    done, calls = run(tmp_path, "v1.2.3", "", existing, attached("v1.2.3"))
    assert done.returncode == 1
    assert the_error(done) == DIFFERS.format(tag="v1.2.3", state=state, what="title")
    assert writes(calls) == [] and held(tmp_path) == [existing]
    assert not (tmp_path / "release" / "injected").exists()
    assert not any(line.startswith("gh release download") for line in calls)


def test_a_candidate_s_title_is_its_own_tag_not_the_release_s(tmp_path):
    existing = titled(made_earlier("v1.2.3-rc.1", True, draft=True), "v1.2.3")
    done, calls = run(tmp_path, "v1.2.3-rc.1", "true", existing, attached("v1.2.3-rc.1"))
    assert done.returncode == 1 and writes(calls) == []
    assert the_error(done) == DIFFERS.format(tag="v1.2.3-rc.1", state="a draft", what="title")


@pytest.mark.parametrize(("draft", "state"), STATES)
def test_a_title_and_notes_both_edited_are_both_named(tmp_path, draft, state):
    existing = made_earlier("v1.2.3", False, draft=draft, body=EDITED) | {"name": "Spring"}
    done, calls = run(tmp_path, "v1.2.3", "", existing, attached("v1.2.3"))
    assert done.returncode == 1 and writes(calls) == [] and held(tmp_path) == [existing]
    assert the_error(done) == DIFFERS.format(tag="v1.2.3", state=state, what="title and notes")


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("existing", [None, "draft"], ids=["made now", "left by an attempt"])
@pytest.mark.parametrize(
    "moment", ["CHANGED_BEFORE_PUBLICATION", "CHANGED_ONCE_PUBLIC"], ids=["before", "after"]
)
def test_a_title_edited_after_the_comparison_is_seen_by_the_second_and_the_release_is_public(
    tmp_path, tag, candidate, existing, moment
):
    held_ = made_earlier(tag, candidate == "true", draft=True) if existing else None
    done, calls = run(
        tmp_path,
        tag,
        candidate,
        held_,
        attached(tag) if existing else None,
        **{moment: '.name = "Spring"'},
    )
    assert done.returncode == 1
    assert the_error(done) == DIFFERS.format(tag=tag, state="public", what="title")
    # then one look, the read by id its row sends the job to, and no more
    assert calls.index(writes(calls)[-1]) == len(calls) - 3
    assert calls[-1].startswith("gh api repos/owner/repo/releases/")
    (public,) = held(tmp_path)
    assert (public["draft"], public["name"], public["body"]) == (False, "Spring", WROTE)
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 1 and writes(calls) == [] and held(tmp_path) == [public]
    # Put back as the tag, or taken away: either is what a reader saw before.
    put(tmp_path, name="" if existing else tag)
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    assert writes(calls) == []


# --- the documents -------------------------------------------------------------------------------


def test_the_readme_says_what_is_compared_and_the_row_says_what_the_tests_above_run():
    readme = " ".join(README.split())
    assert (
        "A release whose notes are not, byte for byte, the text the job wrote (the version's"
        " section of `CHANGELOG.md`, an empty line and the mark) stops the job too, a draft"
        " before it is published and a public one as it is: nothing is normalised, so a draft"
        " saved again in GitHub's web form can count as changed, since the form may rewrite its"
        " line ends. The title is compared as well: missing or empty it counts as the tag, since"
        " an empty title is not an edited one, and any other title that is not the tag stops the"
        " job, before and after the publication (the table has the line for both)."
    ) in readme
    # The rule's reason is its own: what GitHub's pages show for a release with no title is
    # not documented anywhere this repository points at, so no sentence leans on it.
    for said in (readme, " ".join(CREATE["run"].split()), " ".join(RAW_WORKFLOW.split())):
        assert "shows the tag for a release" not in said
    means, next_step = cells(
        "read by its id, the GitHub release for …, …, carries run …'s mark and differs from what"
        " that run wrote in its …"
    )
    assert means == (
        "Read by its id, the release's title is neither the tag nor empty, or its notes are not,"
        " byte for byte, the text the job wrote: a person edited them (saving the notes again in"
        " GitHub's web form can count, since the form may rewrite its line ends). The line says"
        " which, and whether the release, read by its id, is a draft or public; a public one was"
        " changed after the job compared it, or after an earlier attempt published it, and stays"
        " public as changed"
    )
    assert next_step == (
        "A draft: delete it on the repository's releases page and re-run the job, which makes"
        " the release again. A public one: put back what the job wrote (the tag as the title, or"
        " none) and re-run the job, which then compares it again and writes nothing; left as it"
        " is, the job stays failed, and a release tag's `aliases` then runs with another"
        " release, as item 7 says."
    )
    # Left as it is: `aliases` needs a `release` that passed (another release's then moves this
    # version's aliases: test_ci_retry_limits_unit runs it).
    released = {"release_check": {"candidate": ""}}
    assert "aliases" in jobs_that_run(TAG, released)
    assert "aliases" not in jobs_that_run(TAG, released, failed=frozenset({"release"}))
    assert "moves this version's aliases too" in readme
