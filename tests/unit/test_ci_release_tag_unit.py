"""Job `release` judges the git tag as part of the release: the tag must name this run's commit
(`$GITHUB_SHA`) when the step starts, before it writes anything; as the step's last request
before the publication; and as its last request before it says the release is finished.

The faults (the independent tester's violations 1 and 3): a person deleted the tag while the
job ran, the draft kept its tag name, which is only a string, and the publication made GitHub
create the tag again, on the default branch's head, where the release's files were not built;
the job ended well. Its re-run variant too: attempt 1 stopped, the tag was deleted, the re-run
published the draft. And a forced push moved the tag before the first request: the job made,
compared and published a release of files built from a commit the tag no longer named.

The tag is read with GitHub's "Get a commit" (`GET /repos/{owner}/{repo}/commits/{ref}`, its
ref `tags/<tag>`, with `Accept: application/vnd.github.sha`, which "Returns the commit's SHA-1
hash", GitHub's REST reference): the commit, for an annotated tag as for a lightweight one,
where "Get a reference" gives an annotated tag's own object. A read that fails stops the step
in a line of its own, whatever the status: which one GitHub gives for a tag that is gone was
not measured. And the draft is made with `--target` the run's commit, so that a tag GitHub makes
for it is made there: the API's `target_commitish` is "Unused if the Git tag already exists".

gh is test_ci_release_retry_unit's stand-in, which keeps the tag as the commit it names and,
when a release is published while its tag is gone, makes the tag where the draft's target says
(assumed, not measured: GitHub documents only the default, the default branch)."""

import pytest
from test_ci_release_retry_unit import (
    COMMIT,
    CREATE,
    MAIN_HEAD,
    OTHER,
    README,
    attached,
    attempt,
    cells,
    held,
    made_earlier,
    run,
    the_error,
    writes,
)

BOTH = [("v1.2.3", ""), ("v1.2.3-rc.1", "true")]
TAG_READ = "gh api -H Accept: application/vnd.github.sha repos/owner/repo/commits/tags/{tag}"
UNTAGGED = (
    "::error::the tag {tag} could not be read as a commit of the repository (it is gone, or"
    " GitHub did not answer: gh's own words, if any, are above), so this job writes nothing"
    " more for its release and does not call it finished; re-run this job if the repository's"
    " tags page shows {tag} at this run's commit {sha}, and otherwise publish nothing for it"
)
MOVED = (
    "::error::the tag {tag} names commit {other}, not this run's commit {sha}, whose files this"
    " run built and attested, so this job writes nothing more for its release and does not"
    " call it finished; delete this run's release for it on the repository's releases page if"
    " there is one and nobody should publish it, and re-run this job only once the tag names"
    " {sha} again"
)
FINISHED = "is this run's and is published"


def said(tag: str, becomes: str) -> str:
    if becomes == "gone":
        return UNTAGGED.format(tag=tag, sha=COMMIT)
    return MOVED.format(tag=tag, other=becomes, sha=COMMIT)


def tag_reads(calls: list[str], tag: str) -> list[int]:
    return [n for n, line in enumerate(calls) if line == TAG_READ.format(tag=tag)]


def the_tag(tmp_path) -> str:
    return (tmp_path / "existing" / "tag").read_text()


def tag_made(tmp_path) -> bool:
    return (tmp_path / "existing" / "tag.made").exists()


# --- where the tag is read ------------------------------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_the_tag_is_read_first_before_the_publication_and_before_success(tmp_path, tag, candidate):
    done, calls = run(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    (publication,) = [n for n, line in enumerate(calls) if line.startswith("gh api --method")]
    assert tag_reads(calls, tag) == [0, publication - 1, len(calls) - 1]
    assert calls[1].startswith("gh api --paginate")  # the look comes after it


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("left", ["draft", "public"])
def test_a_re_run_reads_the_tag_first_and_last_whatever_it_finds(tmp_path, tag, candidate, left):
    existing = made_earlier(tag, candidate == "true", draft=left == "draft")
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag))
    assert done.returncode == 0, done.stdout + done.stderr
    reads = tag_reads(calls, tag)
    assert reads[0] == 0 and reads[-1] == len(calls) - 1
    if left == "draft":
        (publication,) = [n for n, line in enumerate(calls) if line.startswith("gh api --method")]
        assert reads == [0, publication - 1, len(calls) - 1]
    else:
        assert reads == [0, len(calls) - 1] and writes(calls) == []


# --- a tag that is gone or names another commit ---------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("becomes", ["gone", OTHER], ids=["deleted", "moved"])
def test_a_tag_deleted_or_moved_before_the_job_starts_stops_it_before_anything_else(
    tmp_path, tag, candidate, becomes
):
    """The tester's violation 3 (a forced push before the first request), and a deletion at the
    same moment: the step stops on its first request and writes nothing."""
    done, calls = run(tmp_path, tag, candidate, TAG_AT_READ="1", TAG_BECOMES=becomes)
    assert done.returncode == 1
    assert the_error(done) == said(tag, becomes)
    assert calls == [TAG_READ.format(tag=tag)] and held(tmp_path) == []


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("becomes", ["gone", OTHER], ids=["deleted", "moved"])
@pytest.mark.parametrize("when", ["once made", "at the read before the publication"])
def test_a_tag_deleted_or_moved_after_the_draft_is_made_is_never_published(
    tmp_path, tag, candidate, becomes, when
):
    """The tester's violation 1: the tag deleted while the draft is compared. The read before
    the publication sees it, nothing is written after, and no tag is made on any commit."""
    knobs = {"TAG_ONCE_MADE": becomes}
    if when != "once made":
        knobs = {"TAG_AT_READ": "2", "TAG_BECOMES": becomes}
    done, calls = run(tmp_path, tag, candidate, **knobs)
    assert done.returncode == 1
    assert the_error(done) == said(tag, becomes)
    assert [line.split()[1:3] for line in writes(calls)] == [["release", "create"]]
    assert calls[-1] == TAG_READ.format(tag=tag)
    (draft,) = held(tmp_path)
    assert draft["draft"] is True and not tag_made(tmp_path)
    assert the_tag(tmp_path) == ("" if becomes == "gone" else OTHER)


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("becomes", ["gone", OTHER], ids=["deleted", "moved"])
def test_a_tag_deleted_or_moved_after_the_publication_is_not_called_finished(
    tmp_path, tag, candidate, becomes
):
    done, calls = run(tmp_path, tag, candidate, TAG_ONCE_PUBLIC=becomes)
    assert done.returncode == 1
    assert the_error(done) == said(tag, becomes)
    assert FINISHED not in done.stdout and calls[-1] == TAG_READ.format(tag=tag)


def test_a_re_run_after_the_tag_was_deleted_publishes_nothing_and_makes_no_tag(tmp_path):
    """The tester's re-run variant, with nothing racing: attempt 1 stops on a read that got no
    answer (it says to re-run and delete nothing); a person deletes the tag to withdraw the
    version; the re-run of the failed job stops on its first request. It used to publish the
    draft, and GitHub made the tag on the default branch's head."""
    done, calls = run(tmp_path, "v1.2.3", "", FAILS_AT_READ="2")
    assert done.returncode == 1 and [r["draft"] for r in held(tmp_path)] == [True]
    (tmp_path / "existing" / "tag").write_text("")
    done, calls = attempt(tmp_path, "v1.2.3", "")
    assert done.returncode == 1
    assert the_error(done) == said("v1.2.3", "gone")
    assert calls == [TAG_READ.format(tag="v1.2.3")]
    assert [r["draft"] for r in held(tmp_path)] == [True]
    assert the_tag(tmp_path) == "" and not tag_made(tmp_path)


# --- the tag's own two kinds, and a read that fails ----------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_an_annotated_tag_is_read_as_the_commit_it_names(tmp_path, tag, candidate):
    """An annotated tag is an object of its own, which names the commit: "Get a reference"
    answers with that object's id, "Get a commit" with the commit's. The step asks for the
    commit, so an annotated tag on the run's commit is the run's."""
    (tmp_path / "existing").mkdir()
    (tmp_path / "existing" / "tag.object").write_text("7a6b" * 10)
    done, calls = run(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(tag_reads(calls, tag)) == 3


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("at", [1, 2, 3])
def test_a_tag_read_that_fails_stops_the_step_and_a_plain_re_run_finishes(
    tmp_path, tag, candidate, at
):
    """GitHub's answer is not a commit: the line says the tag may be gone or GitHub did not
    answer (gh's own line above it says which), nothing is written after, and when the tag is
    there the re-run finishes."""
    done, calls = run(tmp_path, tag, candidate, TAG_FAILS_AT_READ=str(at))
    assert done.returncode == 1
    assert the_error(done) == UNTAGGED.format(tag=tag, sha=COMMIT)
    assert "HTTP 502" in done.stderr and calls[-1] == TAG_READ.format(tag=tag)
    assert len(writes(calls)) == {1: 0, 2: 1, 3: 2}[at]
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    assert [(r["id"], r["draft"]) for r in held(tmp_path)] == [(88, False)]


# --- the draft is made on the run's commit -------------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_the_draft_is_made_with_the_run_s_commit_as_its_target(tmp_path, tag, candidate):
    """Should GitHub ever make the tag for this draft, it makes it on the run's commit: the
    one moment the step cannot close, a tag deleted between its last read and the publication,
    ends with the tag made again where it was, never on the default branch's head."""
    done, calls = run(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    (create,) = [line for line in calls if line.startswith("gh release create")]
    assert f" --draft --verify-tag --target {COMMIT} --title {tag} " in create
    (tmp_path / "raced").mkdir()
    done, calls = run(tmp_path / "raced", tag, candidate, TAG_BEFORE_PUBLICATION="gone")
    assert done.returncode == 0, done.stdout + done.stderr
    assert tag_made(tmp_path / "raced") and the_tag(tmp_path / "raced") == COMMIT != MAIN_HEAD


def test_the_step_s_comment_quotes_what_the_target_does():
    comment = " ".join(CREATE["run"].replace("#", "").split())
    assert '`if opts.Target != "" {`, `params["target_commitish"] = opts.Target`' in comment
    assert "\"Unused if the Git tag already exists. Default: the repository's default" in comment
    assert "`GET /repos/{owner}/{repo}/commits/{ref}`" in comment


# --- the lines ------------------------------------------------------------------------------------


def test_the_two_lines_are_one_sentence_each_and_have_their_rows():
    for line in (UNTAGGED, MOVED):
        assert ". " not in line
    means, next_step = cells(
        "the tag … could not be read as a commit of the repository (it is gone, or GitHub did"
        " not answer: gh's own words, if any, are above), so this job writes nothing more for"
        " its release and does not call it finished"
    )
    assert "a person deleted the tag" in means and "Delete nothing" in next_step
    means, next_step = cells(
        "the tag … names commit …, not this run's commit …, whose files this run built and"
        " attested, so this job writes nothing more for its release and does not call it"
        " finished"
    )
    assert "forced push" in means
    readme = " ".join(README.split())
    assert "The tag is judged too:" in readme
