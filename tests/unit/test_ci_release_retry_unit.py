"""A retry of `release` ends well when an earlier attempt of the same run made the release and
was not recorded as done, and stops, deleting nothing, when that attempt left it a draft (gh
release create makes a draft, uploads the files, then publishes it, in separate requests: gh's
own help for the command).

The release's text carries a mark, `<!-- cairn-ci-cd run RUN_ID -->`, an HTML comment GitHub
does not show and its API returns in the release's `body`; a re-run of a job keeps its run's id.
The step lists every release of the repository, drafts included, and takes the one for its tag
as this run's only when it carries this run's mark and has this tag's pre-release flag. A
published one must hold exactly the files this run attested, byte for byte, and is left as it
is. A draft stops the job in one line that says what a person does: delete it on the releases
page and re-run the job, which makes the release again, or publish it there, if its files are
whole, and re-run the job, which then compares them. The third report's must-fix 2: the step
used to delete its draft by id, and a person who published it between the step's last look
and that delete lost a public release. So the job's own text issues no delete of a release
or of a release's file and no write to a release that exists (what gh release create does to
the draft it makes itself is gh's); test_generated_release_retry_unit runs the orderings.
Anything else stops the job in one sentence. gh is a stand-in that keeps the releases as a
JSON file, runs the step's own --jq expression with jq, performs a delete it is asked for (so
one would show), and can publish the draft between two listings."""

import hashlib
import json
import re
from pathlib import Path

import pytest
from ci_shell import SYSTEM, run_step
from test_ci_release_unit import files_run
from test_ci_workflow_unit import JOBS, rerun, stops

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
    """What `package` hands on: the two files, the text, and SHA256SUMS over the three, as its
    step writes it."""
    made = {
        f"cairn_memory-{version}-py3-none-any.whl": b"a wheel",
        f"cairn_memory-{version}.tar.gz": b"a source archive",
        "notes.md": b"the notes\n",
    }
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in made.items())
    return {**made, "SHA256SUMS": sums.encode()}


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


def attempt(tmp_path: Path, tag: str, candidate: str, **env: str):
    """One attempt of the step on what `setup` left and the attempts before it changed; the
    calls are this attempt's."""
    calls = tmp_path / "calls"
    calls.write_text("")
    done = run_step(
        CREATE["run"],
        cwd=tmp_path / "release",
        env={
            "PATH": f"{tmp_path / 'bin'}:{SYSTEM}",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_REF_NAME": tag,
            "GITHUB_RUN_ID": RUN_ID,
            "CANDIDATE": candidate,
            "CALLS": str(calls),
            "EXISTING": str(tmp_path / "existing"),
            "GH_REPO": "owner/repo",
            **env,
        },
    )
    return done, calls.read_text().splitlines()


def run(tmp_path: Path, tag: str, candidate: str, existing=None, assets=None, **env: str):
    setup(tmp_path, tag, existing, assets)
    return attempt(tmp_path, tag, candidate, **env)


def held(tmp_path: Path) -> list[dict]:
    return json.loads((tmp_path / "existing" / "releases.json").read_text())


def by_hand(tmp_path: Path, what: str) -> None:
    """A person, on the releases page: publishes every draft (GitHub keeps its id) or deletes
    every release."""
    after = [{**r, "draft": False} for r in held(tmp_path)] if what == "publish" else []
    (tmp_path / "existing" / "releases.json").write_text(json.dumps(after))


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


DRAFT = (
    "::error::the GitHub release for {tag} is a draft that an earlier attempt of run "
    + RUN_ID
    + " left unfinished, which this job neither deletes nor publishes; delete it on the"
    " repository's releases page and re-run this job, which makes the release again"
)
CUT_OFF = {"cairn_memory-1.2.3.tar.gz": b"a sou"}


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
@pytest.mark.parametrize(
    "names",
    [None, ["SHA256SUMS"], [], ["cairn_memory-1.2.3.tar.gz"]],
    ids=["all", "one", "none", "cut-off"],
)
def test_this_run_s_draft_stops_the_job_which_writes_nothing_and_says_what_a_person_does(
    tmp_path, tag, candidate, names
):
    """Whatever files the draft holds, and a file cut off mid-upload, which GitHub may not even
    serve, is never read: the job looks once, stops, and leaves the draft as it is."""
    existing = made_earlier(tag, candidate == "true", draft=True, names=names)
    done, calls = run(tmp_path, tag, candidate, existing, CUT_OFF)
    assert done.returncode == 1
    assert calls == [line for line in calls if line.startswith("gh api --paginate")]
    assert len(calls) == 1 and writes(calls) == []
    assert the_error(done) == DRAFT.format(tag=tag)
    assert held(tmp_path) == [existing]


def test_a_draft_a_person_publishes_while_the_job_looks_at_it_is_not_touched(tmp_path):
    """The job saw a draft; that it is published a moment later changes nothing the job does,
    since it does nothing to it. (Every other moment: test_generated_release_retry_unit.)"""
    existing = made_earlier("v1.2.3", False, draft=True)
    done, calls = run(
        tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), PUBLISHED_AFTER_FIRST_LISTING="1"
    )
    assert done.returncode == 1
    assert writes(calls) == []
    assert the_error(done) == DRAFT.format(tag="v1.2.3")
    assert held(tmp_path) == [{**existing, "draft": False}]


# --- what the line tells a person to do, done; and what the job does if they publish instead ----


def left_a_draft(tmp_path, tag: str, candidate: str, assets: dict[str, bytes]) -> None:
    existing = made_earlier(tag, candidate == "true", draft=True, names=list(assets))
    done, calls = run(tmp_path, tag, candidate, existing, assets)
    assert done.returncode == 1 and writes(calls) == []
    assert the_error(done) == DRAFT.format(tag=tag)


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
def test_the_draft_a_person_deletes_is_made_again_by_the_re_run(tmp_path, tag, candidate):
    left_a_draft(tmp_path, tag, candidate, CUT_OFF)
    by_hand(tmp_path, "delete")
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    (create,) = writes(calls)
    assert create.startswith(f"gh release create {tag} --verify-tag")
    assert create.endswith(
        "cairn_memory-1.2.3-py3-none-any.whl cairn_memory-1.2.3.tar.gz SHA256SUMS"
    )


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
def test_a_whole_draft_a_person_published_anyway_is_compared_and_kept_by_the_re_run(
    tmp_path, tag, candidate
):
    """The line does not offer it (no short check tells a person the files are whole), but a
    draft published by hand is a published release like any other to the job."""
    left_a_draft(tmp_path, tag, candidate, attached(tag))
    by_hand(tmp_path, "publish")
    before = held(tmp_path)
    done, calls = attempt(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    assert writes(calls) == [] and held(tmp_path) == before
    assert f"gh release download {tag} --dir {tmp_path}/published" in calls
    assert f"The GitHub release {tag} is this run's" in done.stdout


SUMS_CUT = attached("v1.2.3")["SHA256SUMS"].splitlines(keepends=True)


@pytest.mark.parametrize(
    ("assets", "said"),
    [
        (
            {**attached("v1.2.3"), **CUT_OFF},
            "cairn_memory-1.2.3.tar.gz is not the file this run attested",
        ),
        (
            {n: d for n, d in attached("v1.2.3").items() if n != "SHA256SUMS"},
            "its files are [cairn_memory-1.2.3-py3-none-any.whl cairn_memory-1.2.3.tar.gz], not",
        ),
        # Both files whole and SHA256SUMS cut off after their two lines: a person who checked
        # each file against it would have seen nothing wrong.
        (
            {**attached("v1.2.3"), "SHA256SUMS": b"".join(SUMS_CUT[:2])},
            "SHA256SUMS is not the file this run attested",
        ),
    ],
    ids=["a file cut off", "a file missing", "the checksums cut off"],
)
def test_a_draft_published_by_hand_that_is_not_whole_is_a_public_release_the_job_cannot_finish(
    tmp_path, assets, said
):
    """Why the row says not to publish it: the job's comparison is refused in the sentence every
    release this run cannot finish gets, the release is public by then, and nothing is deleted."""
    left_a_draft(tmp_path, "v1.2.3", "", assets)
    by_hand(tmp_path, "publish")
    before = held(tmp_path)
    done, calls = attempt(tmp_path, "v1.2.3", "")
    assert done.returncode == 1 and writes(calls) == [] and held(tmp_path) == before
    error = the_error(done)
    assert error.startswith(REFUSED.format(tag="v1.2.3")) and error.endswith(NEXT)
    assert said in error


# --- the job's own text issues no delete ---------------------------------------------------------

FORBIDDEN = {
    "the API's delete": r"DELETE",
    "gh release delete or delete-asset": r"\bgh\s+release\s+delete",
    "an upload that replaces a file": r"--clobber",
    "an upload onto a release that exists": r"\bgh\s+release\s+upload\b",
    "an edit of a release": r"\bgh\s+release\s+edit\b",
    "another program that speaks HTTP": r"\b(curl|wget)\b",
}
# Every line of the job that names gh, whole: the listing (a read: no method, field or input),
# the download and the creation. Any other line that names it is one too many.
GH_LINES = [
    'gh api --paginate "repos/$GH_REPO/releases?per_page=100" --jq ".[] |'
    ' select(.tag_name == \\"$GITHUB_REF_NAME\\") | [.id, .draft, .prerelease, ((.body // \\"\\")'
    ' | contains(\\"$mark\\")), ([.assets[].name] | sort | join(\\" \\"))] | @tsv"',
    'gh release download "$GITHUB_REF_NAME" --dir "$RUNNER_TEMP/published" || refuse "its files'
    ' could not be downloaded to compare"',
    'gh release create "$GITHUB_REF_NAME" --verify-tag --title "$GITHUB_REF_NAME" --notes-file'
    ' "$RUNNER_TEMP/body.md" "${candidate[@]}" "${files[@]}"',
]
NAMES_GH = re.compile(r"(^|[^\w.-])gh(?![\w.-])")


def commands(job: dict) -> list[str]:
    """The lines of JOB's shell steps, comments too, a line continued with a backslash made
    one line."""
    text = "\n".join(re.sub(r"\s*\\\n\s*", " ", s.get("run", "")) for s in job["steps"])
    return [line.strip() for line in text.splitlines()]


def can_delete(job: dict) -> list[str]:
    lines = commands(job)
    text = "\n".join(lines)
    found = [what for what, shape in FORBIDDEN.items() if re.search(shape, text)]
    return found + [
        f"another line names gh: {line}"
        for line in lines
        if NAMES_GH.search(line) and line not in GH_LINES
    ]


def test_the_text_of_release_issues_no_delete_and_no_write_to_a_release_that_exists():
    """On the job's text, so the delete cannot come back unseen: no DELETE, no gh release
    delete, delete-asset, upload or edit, no --clobber, no curl; the lines that name gh are the
    listing, the download and the creation, each exactly as written here; and it runs no file."""
    release = JOBS["release"]
    assert can_delete(release) == []
    assert [line for line in commands(release) if NAMES_GH.search(line)] == GH_LINES
    assert files_run(release) == []
    assert [s["uses"].split("@")[0] for s in release["steps"] if "uses" in s] == [
        "actions/download-artifact"
    ]
    # What the check sees, each added to the job as it is: the delete the job had, in the
    # ways gh takes a method, and the other ways to remove or replace what a release holds.
    other = "another line names gh: "
    for added, seen in {
        'gh api -X DELETE "repos/$GH_REPO/releases/$id"': ["the API's delete", other],
        'gh api -XDELETE "repos/$GH_REPO/releases/$id"': ["the API's delete", other],
        'gh api --method=delete "repos/$GH_REPO/releases/$id"': [other],
        'gh api "repos/$GH_REPO/releases/$id" \\\n  -F draft=false': [other],
        'gh api --paginate "repos/$GH_REPO/releases?per_page=100" --input body.json': [other],
        'curl -X DELETE "$url"': ["the API's delete", "another program that speaks HTTP"],
        'wget --method=delete "$url"': ["another program that speaks HTTP"],
        'gh release delete "$GITHUB_REF_NAME" --yes': ["gh release delete or delete-asset", other],
        'gh  release \\\n  delete-asset "$GITHUB_REF_NAME" SHA256SUMS': [
            "gh release delete or delete-asset",
            other,
        ],
        'gh release upload "$GITHUB_REF_NAME" SHA256SUMS': [
            "an upload onto a release that exists",
            other,
        ],
        'gh release create "$GITHUB_REF_NAME" --clobber': ["an upload that replaces a file", other],
        'gh release edit "$GITHUB_REF_NAME" --draft=false': ["an edit of a release", other],
        '"$(command -v gh)" release view "$GITHUB_REF_NAME"': [other],
        "# then gh removes it": [other],
    }.items():
        job = {"steps": [*release["steps"], {"name": "added", "run": added}]}
        got = can_delete(job)
        assert [other if what.startswith(other) else what for what in got] == seen, (added, got)


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


README = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")


def test_the_retry_is_said_in_the_readme_with_its_condition():
    readme = " ".join(README.split())
    assert (
        "The release's text ends in a mark, an HTML comment GitHub does not show, with the id of"
        " the run that made it. A re-run of `release` lists the repository's releases, drafts"
        " included (a listing that fails stops the job), and takes the one for its tag as its"
        " own only when it carries this run's mark and this tag's pre-release flag: a published"
        " one that holds exactly the files this run attested, byte for byte, is left as it is"
        " and the job succeeds, so that a release tag's `aliases` runs (a candidate's run has"
        " none); a draft, which `gh release create` leaves when its runner is lost while it"
        " uploads, stops the job, and a person deletes it on the releases page and re-runs the"
        " job, which makes the release again (the table below has the line). Any other release"
        " for the tag stops the job. The job's own text issues no delete of a release or of a"
        " release's file and no write to a release that exists, since a person can publish a"
        " draft between any look at it and a write, and a published release keeps its id; its"
        " one write is `gh release create`, reached only when the listing showed no release for"
        " the tag."
    ) in readme
    assert "deleted if it is still the same draft" not in readme
    assert "either way `aliases` runs" not in readme


def test_the_draft_s_stop_has_its_row_and_the_row_says_what_the_tests_above_run():
    key = (
        "the GitHub release for … is a draft that an earlier attempt of run … left unfinished,"
        " which this job neither deletes nor publishes"
    )
    assert key in stops()
    (row,) = [line for line in README.splitlines() if line.startswith(f"| `{key}` |")]
    _, means, next_step = (cell.strip() for cell in row.strip("|").split(" | "))
    assert means == (
        "An earlier attempt's runner was lost while `gh release create` uploaded the files: the"
        " draft carries this run's mark and may hold none of them, some, or one cut off"
    )
    assert next_step == (
        "Delete it on the repository's releases page and re-run the job, which makes the release"
        " again (once the five days the run's files are kept have passed, re-run `package`"
        " instead, which `release` follows). Do not publish it by hand: its files may be cut"
        " off, and the job then finds a public release it cannot finish. And do not publish or"
        " edit this tag's release by hand while the `release` job runs: `gh release create`"
        " deletes the release it made when an upload or the publication fails."
    )
    # `package` re-run alone is followed by `release`, which then finds no release and makes it.
    assert "release" in rerun({"package"})


def test_the_readme_states_what_gh_release_create_does_to_the_release_it_made():
    """A limit, stated and not built around: the job's one write is gh's own command, and gh's
    source (cli/cli, trunk, read on 2026-11-20,
    https://raw.githubusercontent.com/cli/cli/trunk/pkg/cmd/release/create/create.go) says of
    it: `if hasAssets && !opts.Draft {` ... `// Save the release initially as draft and publish
    it after all assets have finished uploading` ... `params["draft"] = true`; then
    `shared.ConcurrentUpload(...)` and, `if err != nil`, `return cleanupDraftRelease(err)`; then
    `publishRelease(..., newRelease.APIURL, ...)` and the same on its error; and
    `cleanupDraftRelease` calls `deleteRelease(httpClient, baseRepo.RepoHost(),
    safeurl.NewImmutableSafeURL(newRelease.APIURL))`, an HTTP DELETE of the address GitHub gave
    the release it made (create/http.go), which names that release's id, whatever its state
    by then. No flag turns that off; `--draft` skips it and publishes nothing. So a person who
    publishes that draft while the files upload can lose it if an upload then fails. Nothing
    here can run gh's code: the test holds the sentence and the two conditions it rests on in
    the job's line, files given and no --draft."""
    readme = " ".join(README.split())
    assert (
        "`gh release create` itself, given files, makes the release as a draft, uploads them"
        " and then publishes it, and when an upload or the publication fails it deletes the"
        " release it made, by the address GitHub gave that release, not by its tag: so do not"
        " publish or edit this tag's release by hand while the `release` job runs, or a draft"
        " published by hand in that time can be deleted with it." in readme
    )
    create = GH_LINES[-1]
    assert create.startswith("gh release create ") and create.endswith('"${files[@]}"')
    assert not re.search(r"(^|\s)(--draft|-d)(\s|=|$)", create)
    assert "the GitHub CLI deletes the draft it made itself when an upload or the" in " ".join(
        CREATE["run"].replace("#", "").split()
    )
