"""A retry of `release` ends well when an earlier attempt of the same run made the release and
was not recorded as done, or left it a draft (gh release create makes a draft, uploads the
files, then publishes it, in separate requests: gh's own help for the command).

The release's text carries a mark, `<!-- cairn-ci-cd run RUN_ID -->`, an HTML comment GitHub
does not show and its API returns in the release's `body`; a re-run of a job keeps its run's id.
The step lists every release of the repository, drafts included, and takes the one for its tag
as this run's only when it carries this run's mark and has this tag's pre-release flag. A
published one must hold exactly the files this run attested, byte for byte, and is left as it
is. A draft is listed again just before it is touched, and only if it is still the same draft
is it deleted, by its id, and the release made again as on a first attempt: nothing is ever
written to a release that is not a draft of this run's. Anything else stops the job in one
sentence. gh is a stand-in that keeps the releases as a JSON file, runs the step's own --jq
expression with jq, and can publish the draft between two listings."""

import json
from pathlib import Path

import pytest
from ci_shell import SYSTEM, run_step
from test_ci_workflow_unit import JOBS

(CREATE,) = [s for s in JOBS["release"]["steps"] if s.get("name") == "Create the release"]
RUN_ID = "4242"
MARK = f"<!-- cairn-ci-cd run {RUN_ID} -->"

GH = r"""#!/bin/bash
printf 'gh %s\n' "$*" >> "$CALLS"
case "$1 $2" in
  "api --paginate")
    [ -z "$LIST_FAILS" ] || { echo "HTTP 502" >&2; exit 1; }
    listed="$(cat "$EXISTING/listed" 2>/dev/null || echo 0)"
    echo $((listed + 1)) > "$EXISTING/listed"
    jq_expr=""
    while [ $# -gt 0 ]; do
      if [ "$1" = "--jq" ]; then jq_expr="$2"; fi
      shift
    done
    jq -r "$jq_expr" "$EXISTING/releases.json"
    if [ -n "$PUBLISHED_AFTER_FIRST_LISTING" ]; then  # a person publishes the draft meanwhile
      jq 'map(.draft = false)' "$EXISTING/releases.json" > "$EXISTING/next.json"
      mv "$EXISTING/next.json" "$EXISTING/releases.json"
    fi ;;
  "api -X")
    [ "$3" = DELETE ] || exit 2
    id="${4##*/}"
    jq "map(select(.id != $id))" "$EXISTING/releases.json" > "$EXISTING/next.json"
    mv "$EXISTING/next.json" "$EXISTING/releases.json" ;;
  "release download")
    [ -z "$DOWNLOAD_FAILS" ] || { echo "HTTP 404" >&2; exit 1; }
    dir=""
    while [ $# -gt 0 ]; do
      if [ "$1" = "--dir" ]; then dir="$2"; fi
      shift
    done
    mkdir -p "$dir"
    cp "$EXISTING"/assets/* "$dir"/ ;;
  "release create")
    while [ $# -gt 0 ]; do
      if [ "$1" = "--notes-file" ]; then cp "$2" "$EXISTING/body"; fi
      shift
    done ;;
  *) exit 2 ;;
esac
"""


def files_of(version: str) -> dict[str, bytes]:
    return {
        f"cairn_memory-{version}-py3-none-any.whl": b"a wheel",
        f"cairn_memory-{version}.tar.gz": b"a source archive",
        "SHA256SUMS": b"the checksums",
        "notes.md": b"the notes\n",
    }


def setup(tmp_path: Path, tag: str, existing, assets: dict | None = None):
    version = tag[1:].split("-rc.")[0]
    release = tmp_path / "release"
    release.mkdir()
    for name, data in files_of(version).items():
        (release / name).write_bytes(data)
    there = tmp_path / "existing"
    (there / "assets").mkdir(parents=True)
    listed = [] if existing is None else existing if isinstance(existing, list) else [existing]
    (there / "releases.json").write_text(json.dumps(listed))
    for name, data in (assets or {}).items():
        (there / "assets" / name).write_bytes(data)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text(GH)
    (bin_ / "gh").chmod(0o755)
    return bin_, there, release


def run(tmp_path: Path, tag: str, candidate: str, existing=None, assets=None, **env: str):
    bin_, there, release = setup(tmp_path, tag, existing, assets)
    calls = tmp_path / "calls"
    calls.write_text("")
    done = run_step(
        CREATE["run"],
        cwd=release,
        env={
            "PATH": f"{bin_}:{SYSTEM}",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_REF_NAME": tag,
            "GITHUB_RUN_ID": RUN_ID,
            "CANDIDATE": candidate,
            "CALLS": str(calls),
            "EXISTING": str(there),
            "GH_REPO": "owner/repo",
            **env,
        },
    )
    return done, calls.read_text().splitlines()


def made(calls: list[str]) -> list[str]:
    return [line for line in calls if line.startswith("gh release create")]


def writes(calls: list[str]) -> list[str]:
    return [line for line in calls if line.startswith(("gh api -X", "gh release create"))]


def made_earlier(tag: str, prerelease: bool, draft: bool = False, names=None, body=None) -> dict:
    version = tag[1:].split("-rc.")[0]
    attached = [n for n in files_of(version) if n != "notes.md"] if names is None else names
    # As GET repos/OWNER/REPO/releases lists one (GitHub's REST reference).
    return {
        "id": 77,
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "body": f"the notes\n\n{MARK}\n" if body is None else body,
        "assets": [{"name": n, "size": 1} for n in attached],
    }


def attached(tag: str) -> dict[str, bytes]:
    version = tag[1:].split("-rc.")[0]
    return {n: d for n, d in files_of(version).items() if n != "notes.md"}


def the_error(done) -> str:
    (error,) = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    assert error.count(". ") == 0
    return error


def test_no_release_yet_makes_it_with_this_run_s_mark_after_the_notes(tmp_path):
    done, calls = run(tmp_path, "v1.2.3", "")
    assert done.returncode == 0, done.stdout + done.stderr
    (create,) = made(calls)
    assert create.startswith("gh release create v1.2.3 --verify-tag")
    assert f"--notes-file {tmp_path}/body.md" in create
    assert (tmp_path / "existing" / "body").read_text() == f"the notes\n\n{MARK}\n"
    # notes.md itself is one of the attested files, so it is left as it is.
    assert (tmp_path / "release" / "notes.md").read_bytes() == b"the notes\n"


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
def test_this_run_s_published_release_is_recognised_and_left_as_it_is(tmp_path, tag, candidate):
    existing = made_earlier(tag, candidate == "true")
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag))
    assert done.returncode == 0, done.stdout + done.stderr
    assert writes(calls) == []
    assert f"The GitHub release {tag} is this run's" in done.stdout
    assert f"gh release download {tag} --dir {tmp_path}/published" in calls


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
@pytest.mark.parametrize(
    "names",
    [None, ["SHA256SUMS"], [], ["cairn_memory-1.2.3.tar.gz"]],
    ids=["all", "one", "none", "cut-off"],
)
def test_this_run_s_draft_is_listed_again_deleted_by_its_id_and_the_release_made_again(
    tmp_path, tag, candidate, names
):
    """Whatever files the draft holds, and a file cut off mid-upload, which GitHub may not even
    serve, is never read: the draft is still this run's, so it goes."""
    existing = made_earlier(tag, candidate == "true", draft=True, names=names)
    done, calls = run(tmp_path, tag, candidate, existing, {"cairn_memory-1.2.3.tar.gz": b"a sou"})
    assert done.returncode == 0, done.stdout + done.stderr
    assert sum(line.startswith("gh api --paginate") for line in calls) == 2
    assert writes(calls)[0] == "gh api -X DELETE repos/owner/repo/releases/77"
    assert len(writes(calls)) == 2 and writes(calls)[1].startswith(f"gh release create {tag}")
    assert not [line for line in calls if line.startswith("gh release download")]
    assert f"The draft GitHub release {tag} is this run's" in done.stdout


def test_a_draft_published_between_the_listing_and_the_write_is_not_touched(tmp_path):
    existing = made_earlier("v1.2.3", False, draft=True)
    done, calls = run(
        tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), PUBLISHED_AFTER_FIRST_LISTING="1"
    )
    assert done.returncode == 1
    assert writes(calls) == []
    assert "it changed while this job looked at it" in the_error(done)


REFUSED = "::error::a GitHub release for {tag} exists and is not one this run can finish: "
NEXT = (
    "; look at it on the repository's releases page, delete it if nobody should publish it,"
    " and re-run this job"
)


@pytest.mark.parametrize(
    ("existing", "assets", "said"),
    [
        (
            made_earlier("v1.2.3", False, draft=True, names=[], body="hand-made"),
            {},
            f"its text does not carry run {RUN_ID}'s mark",
        ),
        (
            made_earlier("v1.2.3", False, body=None) | {"body": None},
            attached("v1.2.3"),
            f"its text does not carry run {RUN_ID}'s mark",
        ),
        (
            made_earlier("v1.2.3", False, draft=True, body="<!-- cairn-ci-cd run 42420 -->"),
            {},
            f"its text does not carry run {RUN_ID}'s mark",
        ),
        (
            made_earlier("v1.2.3", False, draft=True, body="<!-- cairn-ci-cd run 424 -->"),
            {},
            f"its text does not carry run {RUN_ID}'s mark",
        ),
        (
            made_earlier("v1.2.3", prerelease=True),
            attached("v1.2.3"),
            "its pre-release flag is true, not false",
        ),
        (
            made_earlier("v1.2.3", prerelease=True, draft=True),
            {},
            "its pre-release flag is true, not false",
        ),
        (
            [made_earlier("v1.2.3", False), made_earlier("v1.2.3", False, draft=True)],
            attached("v1.2.3"),
            "there are 2 releases for it",
        ),
        (
            made_earlier("v1.2.3", False, names=["SHA256SUMS", "cairn_memory-1.2.3.tar.gz"]),
            attached("v1.2.3"),
            "its files are [SHA256SUMS cairn_memory-1.2.3.tar.gz], not [SHA256SUMS"
            " cairn_memory-1.2.3-py3-none-any.whl cairn_memory-1.2.3.tar.gz]",
        ),
        (
            made_earlier("v1.2.3", False, names=[*attached("v1.2.3"), "other.txt"]),
            attached("v1.2.3"),
            "its files are [",
        ),
        (
            made_earlier("v1.2.3", False),
            {**attached("v1.2.3"), "cairn_memory-1.2.3.tar.gz": b"another source archive"},
            "cairn_memory-1.2.3.tar.gz is not the file this run attested",
        ),
    ],
)
def test_a_release_this_run_cannot_finish_stops_the_job_in_one_sentence(
    tmp_path, existing, assets, said
):
    done, calls = run(tmp_path, "v1.2.3", "", existing, assets)
    assert done.returncode == 1
    assert writes(calls) == []
    error = the_error(done)
    assert error.startswith(REFUSED.format(tag="v1.2.3")) and error.endswith(NEXT)
    assert said in error


def test_a_published_release_whose_files_cannot_be_read_is_refused(tmp_path):
    existing = made_earlier("v1.2.3", False)
    done, calls = run(tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), DOWNLOAD_FAILS="1")
    assert done.returncode == 1 and writes(calls) == []
    assert "its files could not be downloaded to compare" in the_error(done)


def test_a_release_of_another_tag_is_none_of_this_one_s(tmp_path):
    done, calls = run(tmp_path, "v1.2.3", "", made_earlier("v1.2.4", False), attached("v1.2.3"))
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(made(calls)) == 1


def test_a_listing_that_fails_is_not_taken_for_no_release(tmp_path):
    done, calls = run(tmp_path, "v1.2.3", "", LIST_FAILS="1")
    assert done.returncode == 1 and writes(calls) == []
    assert the_error(done) == (
        "::error::the repository's releases could not be listed, so whether v1.2.3 has one is"
        " not known; re-run this job"
    )


def test_the_listing_takes_every_page_and_drafts_by_their_tag():
    run_ = CREATE["run"]
    assert 'gh api --paginate "repos/$GH_REPO/releases?per_page=100"' in run_
    assert CREATE["env"]["GH_REPO"] == "${{ github.repository }}"
    assert JOBS["release"]["permissions"] == {"contents": "write"}  # drafts are listed to it
    assert "--clobber" not in run_ and "gh release upload" not in run_


def test_the_retry_is_said_in_the_readme_with_its_condition():
    readme = " ".join((Path(__file__).resolve().parents[2] / "README.md").read_text().split())
    assert (
        "The release's text ends in a mark, an HTML comment GitHub does not show, with the id of"
        " the run that made it. A re-run of `release` lists the repository's releases, drafts"
        " included (a listing that fails stops the job), and takes the one for its tag as its"
        " own only when it carries this run's mark and this tag's pre-release flag: a published"
        " one that holds exactly the files this run attested, byte for byte, is left as it is,"
        " and a draft, which gh leaves when its runner is lost while it uploads, is listed again,"
        " deleted if it is still the same draft, and the release made again; either way"
        " `aliases` runs. Any other release for the tag stops the job, and nothing is written to"
        " it."
    ) in readme
