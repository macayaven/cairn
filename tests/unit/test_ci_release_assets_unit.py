"""Job `release` compares the files of the release it judges and of no other. Each file is
fetched by its asset id, from the release read by its id, and only once that read shows the
asset uploaded and of the size of the file this run attested; after the files are compared the
release is read by its id again, and it must be the release the first read showed: the same
assets (ids, names, sizes, update times), title, notes and flags. So before the publication and
before the step says the release is finished.

The faults (the independent tester's violations 2 and 5). The files were downloaded by the tag,
`gh release download <tag>`, which finds "a published repository release by its tagName, or a
draft release by its pending tag name" (cli/cli at v2.102.0, pkg/cmd/release/shared/fetch.go),
not the release the step had read by its id: with two drafts for the tag, the step compared
the whole files of one and published the other, whose upload was cut. And a file a person
replaced after the download and before the read that followed it kept its name: the read
passed, and the step published, or called finished, a release whose file was not the run's.

The request: GitHub's "Get a release asset", `GET /repos/{owner}/{repo}/releases/assets/{id}`
with `Accept: application/octet-stream`, which answers with the bytes or a redirect to them
(GitHub's REST reference); gh follows the redirect and drops the token on another host
(api/http_client.go), and writes bytes with an escape character only when asked to with
--allow-escape-sequences (pkg/cmd/api/api.go). A fetch that fails says nothing about the files,
as a download that failed did: the step stops on the line of a download that fails.

gh is test_ci_release_retry_unit's stand-in, which gives each asset an id, a state, a size and
an update time, keeps a file's bytes per release where a test gives them, and still answers
`gh release download` as the CLI does, so a test shows what the step did before."""

import pytest
from test_ci_release_retry_unit import (
    CREATE,
    FETCH,
    LISTING,
    README,
    REFUSED,
    TAG_READ,
    attached,
    attempt,
    cells,
    fetches,
    held,
    looks,
    made_earlier,
    run,
    setup,
    the_error,
    unfinished,
    untagged,
    writes,
)

BOTH = [("v1.2.3", ""), ("v1.2.3-rc.1", "true")]
WHEEL, SOURCE, SUMS = (
    "cairn_memory-1.2.3-py3-none-any.whl",
    "cairn_memory-1.2.3.tar.gz",
    "SHA256SUMS",
)
READ = "gh api repos/owner/repo/releases/{id} --jq "
CHANGED = (
    "::error::the GitHub release for {tag}, {state}, changed between this job's two reads of it"
    " by its id, around the download of its files, so the files it compared may not be the"
    " ones the release holds; re-run this job, which reads and compares it again"
)
UNREAD = (
    "::error::the files of the GitHub release for {tag}, {state} as read by its id, could not be"
    " downloaded, so"
    " they were not compared and nothing shows that they differ; re-run this job, and delete"
    " nothing unless the releases page shows a file of the draft missing or unfinished"
)


def fetched(calls: list[str]) -> list[str]:
    return [line for line in calls if line.startswith(FETCH.split("{")[0])]


def assets_of(release: dict, **change: dict) -> list[dict]:
    """RELEASE's assets, each with what CHANGE gives its name (a state, an id, a time)."""
    return [{**a, **change.get(a["name"], {})} for a in release["assets"]]


# --- where the files come from ----------------------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("existing", [None, "draft", "published"])
def test_each_file_is_fetched_by_its_asset_id_between_two_reads_by_id(
    tmp_path, tag, candidate, existing
):
    held_ = None
    if existing:
        held_ = made_earlier(tag, candidate == "true", draft=existing == "draft")
    done, calls = run(tmp_path, tag, candidate, held_, attached(tag) if existing else None)
    assert done.returncode == 0, done.stdout + done.stderr
    rid = 77 if existing else 88
    read = READ.format(id=rid)
    compared = [read, *fetches(rid), read]
    seen = looks(untagged(calls))
    publication = [line for line in seen if line.startswith("gh api --method PATCH")]
    expected = {
        None: [LISTING, *made_by(calls), LISTING, *compared, *publication, LISTING, *compared],
        "draft": [LISTING, *compared, *publication, LISTING, *compared],
        "published": [LISTING, *compared],
    }[existing]
    assert seen == expected
    assert not any(line.startswith("gh release download") for line in calls)


def made_by(calls: list[str]) -> list[str]:
    return [line for line in calls if line.startswith("gh release create")]


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_a_cut_draft_beside_a_whole_one_the_listing_does_not_show_yet_is_never_published(
    tmp_path, tag, candidate
):
    """The tester's violation 2. Draft 77 is this run's, its wheel's upload cut: the asset is
    not `uploaded` and holds four bytes. Draft 78, made by a later attempt, is whole, and the
    listing does not show it yet. `gh release download <tag>` answers with 78's files, which
    are the run's; the step used to compare those and publish 77. It reads 77's assets and
    stops on its wheel without fetching it, the line of an unfinished draft."""
    cut = made_earlier(tag, candidate == "true", draft=True)
    cut["assets"] = assets_of(cut, **{WHEEL: {"state": "starter"}})
    whole = {**made_earlier(tag, candidate == "true", draft=True), "id": 78}
    setup(tmp_path, tag, [cut, whole], attached(tag))
    (tmp_path / "existing" / "held" / "77").mkdir(parents=True)
    (tmp_path / "existing" / "held" / "77" / WHEEL).write_bytes(attached(tag)[WHEEL][:4])
    done, calls = attempt(tmp_path, tag, candidate, stale=([cut], None))
    assert done.returncode == 1
    assert unfinished(the_error(done), tag)
    assert writes(calls) == [] and [r["draft"] for r in held(tmp_path)] == [True, True]
    assert fetched(calls) == []


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("left", ["draft", "public"])
@pytest.mark.parametrize(
    ("change", "bytes_"),
    [({"state": "starter"}, None), ({}, b"a sou")],
    ids=["not uploaded", "of another size"],
)
def test_an_asset_not_uploaded_or_of_another_size_is_not_fetched(
    tmp_path, tag, candidate, left, change, bytes_
):
    """The read by id says what each asset is before it is fetched: one that is not
    `uploaded`, or whose size is not the size of the file this run attested, is a draft that
    is not whole (the line says to delete it) or a public release this run cannot finish."""
    existing = made_earlier(tag, candidate == "true", draft=left == "draft")
    existing["assets"] = assets_of(existing, **{SOURCE: change})
    files = attached(tag)
    if bytes_ is not None:
        files[SOURCE] = bytes_
    done, calls = run(tmp_path, tag, candidate, existing, files)
    assert done.returncode == 1 and writes(calls) == [] and held(tmp_path) == [existing]
    assert fetched(calls) == fetches(77)[:1]  # the wheel, which comes first, and no more
    if left == "draft":
        assert unfinished(the_error(done), tag)
    else:
        assert the_error(done).startswith(
            REFUSED.format(tag=tag) + f"{SOURCE} is not the file this run attested"
        )


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_a_file_whose_bytes_differ_at_the_size_attested_is_fetched_and_not_published(
    tmp_path, tag, candidate
):
    existing = made_earlier(tag, candidate == "true", draft=True)
    files = {**attached(tag), SOURCE: b"A SOURCE ARCHIVE"}  # sixteen bytes, as the run's
    done, calls = run(tmp_path, tag, candidate, existing, files)
    assert done.returncode == 1 and unfinished(the_error(done), tag)
    assert writes(calls) == [] and fetched(calls) == fetches(77)[:2]


# --- a release changed while its files are fetched ------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("when", ["before the publication", "after it"])
def test_a_file_replaced_after_its_fetch_and_before_the_next_read_is_seen_on_that_read(
    tmp_path, tag, candidate, when
):
    """The tester's violation 5. A person replaces the source archive once the step has fetched
    the three files: a new asset, with a new id and an update time of its own, holding other
    bytes. The read that follows sees it, and the step stops in the line of a release that
    changed between its two reads, writes nothing after, and does not call it finished."""
    existing = made_earlier(tag, candidate == "true", draft=True)
    after = "3" if when == "before the publication" else "6"
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag), REPLACED_AFTER_FETCH=after)
    assert done.returncode == 1
    state = "a draft" if when == "before the publication" else "public"
    assert the_error(done) == CHANGED.format(tag=tag, state=state)
    assert untagged(calls)[-1].startswith(READ.format(id=77))
    assert len(writes(calls)) == (0 if when == "before the publication" else 1)
    assert "is this run's and is published" not in done.stdout
    (release,) = held(tmp_path)
    assert release["draft"] is (when == "before the publication")


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_a_file_replaced_before_its_fetch_is_not_found_by_its_old_id(tmp_path, tag, candidate):
    """The same a moment earlier: the asset the read named is gone when its fetch arrives. A
    fetch that fails says nothing about the files: the step stops in that line, and the
    re-run reads the new asset's id and compares its bytes, which are not the run's."""
    existing = made_earlier(tag, candidate == "true", draft=True)
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag), REPLACED_BEFORE_FETCH="2")
    assert done.returncode == 1
    assert the_error(done) == UNREAD.format(tag=tag, state="a draft")
    assert writes(calls) == [] and fetched(calls) == fetches(77)[:2]
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 1 and unfinished(the_error(done), tag)
    assert writes(calls) == [] and fetched(calls)[1] == FETCH.format(aid=999)


@pytest.mark.parametrize(
    ("what", "change"),
    [
        ("an update time", '.assets[0].updated_at = "2026-11-20T13:00:00Z"'),
        ("an asset id", ".assets[2].id = 990"),
        ("the draft flag: a person published it", ".draft = false"),
    ],
)
def test_a_release_whose_second_read_differs_from_the_first_stops_the_step(tmp_path, what, change):
    """Whatever differs between the two reads stops the step, though each read alone is this
    run's: the assets' ids and update times, and the flags, a draft a person published while
    its files were fetched among them. The re-run reads again and finishes."""
    existing = made_earlier("v1.2.3", False, draft=True)
    existing["assets"] = assets_of(
        existing, **{n: {"updated_at": "2026-11-20T12:00:00Z"} for n in attached("v1.2.3")}
    )
    done, calls = run(
        tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), AT_READ="2", AT_READ_CHANGE=change
    )
    assert done.returncode == 1, what
    state = "public" if what.startswith("the draft flag") else "a draft"
    assert the_error(done) == CHANGED.format(tag="v1.2.3", state=state)
    assert writes(calls) == []
    done, calls = attempt(tmp_path, "v1.2.3", "")
    assert done.returncode == 0, done.stdout + done.stderr
    assert held(tmp_path)[0]["draft"] is False


def test_a_fetch_that_fails_stops_the_step_in_the_line_of_a_download_that_fails(tmp_path):
    existing = made_earlier("v1.2.3", False, draft=True)
    done, calls = run(tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), FETCH_FAILS="2")
    assert done.returncode == 1
    assert the_error(done) == UNREAD.format(tag="v1.2.3", state="a draft")
    assert writes(calls) == [] and untagged(calls)[-1] == FETCH.format(aid=771)
    assert "HTTP 502" in done.stderr


# --- the documents --------------------------------------------------------------------------------


def test_the_changed_line_is_one_sentence_and_has_its_row():
    for state in ("a draft", "public"):
        assert ". " not in CHANGED.format(tag="v1.2.3", state=state)
    means, next_step = cells(
        "the GitHub release for …, …, changed between this job's two reads of it by its id,"
        " around the download of its files, so the files it compared may not be the ones the"
        " release holds"
    )
    assert "a person changed" in means and next_step.startswith("Re-run the job")


def test_the_step_s_comment_and_the_readme_say_where_the_files_come_from():
    comment = " ".join(CREATE["run"].replace("#", "").split())
    assert "`GET /repos/{owner}/{repo}/releases/assets/{asset_id}`" in comment
    assert "gh release download" not in CREATE["run"].replace("`gh release download", "")
    readme = " ".join(README.split())
    assert (
        "Each file is fetched by the id the release's read gives it, once that read shows it"
        " uploaded and of the size of the run's file, and the release is read again after the"
        " comparison and must be as the first read showed it: the same files (ids, names,"
        " sizes, update times), title, notes and flags."
    ) in readme
    assert (
        "What a person changes in the release, its title, notes, files or flags, in the moment"
        " after the job's last read of it, before the publication or before the job ends, the"
        " job does not see: that moment is the stated race."
    ) in readme
    assert TAG_READ.startswith("gh api -H")
