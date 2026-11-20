"""Job `release` makes the release as a draft, compares the draft's files with the ones this run
attested, publishes it with a request of its own and looks again; a retry starts from whatever
an earlier attempt of the same run left, and nothing the job runs takes a release back.

The release's text carries a mark, `<!-- cairn-ci-cd run RUN_ID -->`, an HTML comment GitHub
does not show and its API returns in the release's `body`; a re-run of a job keeps its run's id.
The step lists every release of the repository, drafts included, and takes the one for its tag
as this run's only when it carries this run's mark and has this tag's pre-release flag. None:
it makes a draft with the files and looks again. This run's draft: when it holds exactly the
files this run attested, names and bytes, the step publishes it, by its id, and looks again;
when it does not (an upload was cut) the step stops in one line that says what a person does:
delete it on the releases page and re-run the job. A published one must hold exactly those
files, byte for byte, and is left as it is. Anything else stops the job in one sentence.

The third report's must-fix 2: the step used to delete its draft by id, and a person who
published it between the step's last look and that delete lost a public release. The fourth
report's must-fix 1: `gh release create`, given files and not asked for a draft, published its
own draft and deleted it by id when the publication's reply was an error, though GitHub had
published it. So the job's text issues no delete of a release or of a release's file, asks
gh for a draft, and publishes with one request that only sets `draft` to false;
test_generated_release_retry_unit runs the orderings and the requests that fail. gh is a
stand-in that keeps the releases as a JSON file, runs the step's own --jq expression with jq,
performs a delete it is asked for (so one would show), and can publish the draft between two
listings."""

import hashlib
import json
import re
import shutil
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
  "api --method")  # the publication: PATCH of a release's id, draft=false
    [ "$3" = PATCH ] && [ "$5 $6 $7" = "--silent -F draft=false" ] || exit 2
    id="${4##*/}"
    jq -e "any(.id == $id)" "$EXISTING/releases.json" > /dev/null || exit 1  # HTTP 404
    jq "map(if .id == $id then .draft = false else . end)" "$EXISTING/releases.json" \
      > "$EXISTING/next.json"
    mv "$EXISTING/next.json" "$EXISTING/releases.json"
    # a person deletes it as soon as it is public
    [ -z "$DELETED_ONCE_PUBLIC" ] || echo "[]" > "$EXISTING/releases.json" ;;
  "release download")
    [ -z "$DOWNLOAD_FAILS" ] || { echo "HTTP 404" >&2; exit 1; }
    dir=""
    while [ $# -gt 0 ]; do
      if [ "$1" = "--dir" ]; then dir="$2"; fi
      shift
    done
    [ ! -e "$dir" ] || exit 1  # gh keeps a file that is there, and the step gives a new place
    mkdir -p "$dir"
    cp "$EXISTING"/assets/* "$dir"/ ;;
  "release create")  # the release asked for, as GitHub would list it, and its files
    tag="$3"; draft=false; pre=false; names=()
    shift 3
    while [ $# -gt 0 ]; do
      case "$1" in
        --draft) draft=true ;;
        --prerelease) pre=true ;;
        --notes-file) cp "$2" "$EXISTING/body"; shift ;;
        --title) shift ;;
        --*) ;;
        *) cp "$1" "$EXISTING/assets/"; names+=("$1") ;;
      esac
      shift
    done
    jq --arg tag "$tag" --rawfile body "$EXISTING/body" --argjson draft "$draft" \
      --argjson pre "$pre" '. + [{id: 88, tag_name: $tag, draft: $draft, prerelease: $pre,
        body: $body, assets: ($ARGS.positional | map({name: ., size: 1}))}]' \
      "$EXISTING/releases.json" --args "${names[@]}" > "$EXISTING/next.json"
    mv "$EXISTING/next.json" "$EXISTING/releases.json" ;;
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
    for downloaded in ("draft", "published"):  # a re-run's runner is a new one
        shutil.rmtree(tmp_path / downloaded, ignore_errors=True)
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
    kinds = ("gh api -X", "gh api --method", "gh release create")
    return [line for line in calls if line.startswith(kinds)]


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


LISTING = "gh api --paginate repos/owner/repo/releases?per_page=100 --jq "
BOTH = [
    ("v1.2.3", "", "", ""),
    ("v1.2.3-rc.1", "true", " --prerelease --latest=false", " -f make_latest=false"),
]


def looks(calls: list[str]) -> list[str]:
    """CALLS, each listing cut to where its --jq expression starts."""
    return [LISTING if line.startswith(LISTING) else line for line in calls]


@pytest.mark.parametrize(("tag", "candidate", "flags", "latest"), BOTH)
def test_no_release_yet_makes_a_draft_compares_it_publishes_it_and_looks_again(
    tmp_path, tag, candidate, flags, latest
):
    done, calls = run(tmp_path, tag, candidate)
    assert done.returncode == 0, done.stdout + done.stderr
    assert looks(calls) == [
        LISTING,
        f"gh release create {tag} --draft --verify-tag --title {tag} --notes-file"
        f" {tmp_path}/body.md{flags} cairn_memory-1.2.3-py3-none-any.whl"
        " cairn_memory-1.2.3.tar.gz SHA256SUMS",
        LISTING,
        f"gh release download {tag} --dir {tmp_path}/draft",
        f"gh api --method PATCH repos/owner/repo/releases/88 --silent -F draft=false{latest}",
        LISTING,
        f"gh release download {tag} --dir {tmp_path}/published",
    ]
    (made_,) = held(tmp_path)
    assert (made_["draft"], made_["prerelease"]) == (False, candidate == "true")
    assert made_["body"] == f"the notes\n\n{MARK}\n"
    # notes.md itself is one of the attested files, so it is left as it is.
    assert (tmp_path / "release" / "notes.md").read_bytes() == b"the notes\n"
    assert f"The GitHub release {tag} is this run's and is published" in done.stdout


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


@pytest.mark.parametrize(("tag", "candidate", "flags", "latest"), BOTH)
def test_this_run_s_draft_with_exactly_this_run_s_files_is_published_by_its_id(
    tmp_path, tag, candidate, flags, latest
):
    """What a retry does with the draft an attempt left whole, and what the first attempt does
    with the draft it has just made: compare, publish with the one request, look again."""
    existing = made_earlier(tag, candidate == "true", draft=True)
    done, calls = run(tmp_path, tag, candidate, existing, attached(tag))
    assert done.returncode == 0, done.stdout + done.stderr
    assert looks(calls) == [
        LISTING,
        f"gh release download {tag} --dir {tmp_path}/draft",
        f"gh api --method PATCH repos/owner/repo/releases/77 --silent -F draft=false{latest}",
        LISTING,
        f"gh release download {tag} --dir {tmp_path}/published",
    ]
    assert held(tmp_path) == [{**existing, "draft": False}]


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
@pytest.mark.parametrize(
    "names",
    [["SHA256SUMS"], [], ["cairn_memory-1.2.3.tar.gz"], [*attached("v1.2.3"), "other.txt"]],
    ids=["one", "none", "cut-off", "one more"],
)
def test_this_run_s_draft_with_other_names_stops_the_job_which_reads_and_writes_nothing(
    tmp_path, tag, candidate, names
):
    """A file cut off mid-upload, which GitHub may not even serve, is never read: the job
    looks once, stops, and leaves the draft as it is."""
    existing = made_earlier(tag, candidate == "true", draft=True, names=names)
    done, calls = run(tmp_path, tag, candidate, existing, CUT_OFF)
    assert done.returncode == 1
    assert looks(calls) == [LISTING]
    assert the_error(done) == DRAFT.format(tag=tag)
    assert held(tmp_path) == [existing]


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
@pytest.mark.parametrize(
    "assets",
    [
        {**attached("v1.2.3"), **CUT_OFF},
        {**attached("v1.2.3"), "SHA256SUMS": attached("v1.2.3")["SHA256SUMS"][:70]},
        {**attached("v1.2.3"), "other.txt": b"listed under none of the three names"},
    ],
    ids=["a file cut off", "the checksums cut off", "one more than listed"],
)
def test_this_run_s_draft_with_the_names_and_other_bytes_is_not_published(
    tmp_path, tag, candidate, assets
):
    """The names listed are this run's, and what the download gives is not: a file's bytes, or
    a file more than the listing named. The draft stays a draft."""
    existing = made_earlier(tag, candidate == "true", draft=True)
    done, calls = run(tmp_path, tag, candidate, existing, assets)
    assert done.returncode == 1
    assert looks(calls) == [LISTING, f"gh release download {tag} --dir {tmp_path}/draft"]
    assert the_error(done) == DRAFT.format(tag=tag)
    assert held(tmp_path) == [existing]


def test_a_draft_a_person_publishes_while_the_job_compares_it_is_what_the_job_would_make_it(
    tmp_path,
):
    """The job saw a draft and a person publishes it a moment later: the job's publication
    then changes nothing, and the job ends as on a release it found published. (Every other
    moment, and a draft deleted or changed: test_generated_release_retry_unit.)"""
    existing = made_earlier("v1.2.3", False, draft=True)
    done, calls = run(
        tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), PUBLISHED_AFTER_FIRST_LISTING="1"
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert writes(calls) == [
        "gh api --method PATCH repos/owner/repo/releases/77 --silent -F draft=false"
    ]
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
    create, publish = writes(calls)
    assert create.startswith(f"gh release create {tag} --draft --verify-tag")
    assert create.endswith(
        "cairn_memory-1.2.3-py3-none-any.whl cairn_memory-1.2.3.tar.gz SHA256SUMS"
    )
    assert publish.startswith("gh api --method PATCH repos/owner/repo/releases/88 ")
    (made_,) = held(tmp_path)
    assert (made_["id"], made_["draft"]) == (88, False)


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


# --- the job's own text issues no delete, and one write to a release that exists ----------------

FORBIDDEN = {
    "the API's delete": r"DELETE",
    "gh release delete or delete-asset": r"\bgh\s+release\s+delete",
    "an upload that replaces a file": r"--clobber",
    "an upload onto a release that exists": r"\bgh\s+release\s+upload\b",
    "an edit of a release": r"\bgh\s+release\s+edit\b",
    "another program that speaks HTTP": r"\b(curl|wget)\b",
}
# Every line of the job that names gh, whole, in the order the text has them: the listing (a
# read: no method, field or input), the download, the creation of a draft, and the publication,
# the one write to a release that exists. Any other line that names it is one too many.
GH_LINES = [
    'gh api --paginate "repos/$GH_REPO/releases?per_page=100" --jq ".[] |'
    ' select(.tag_name == \\"$GITHUB_REF_NAME\\") | [.id, .draft, .prerelease, ((.body // \\"\\")'
    ' | contains(\\"$mark\\")), ([.assets[].name] | sort | join(\\" \\"))] | @tsv"',
    'gh release download "$GITHUB_REF_NAME" --dir "$RUNNER_TEMP/$2" || refuse "its files could'
    ' not be downloaded to compare"',
    'gh release create "$GITHUB_REF_NAME" --draft --verify-tag --title "$GITHUB_REF_NAME"'
    ' --notes-file "$RUNNER_TEMP/body.md" "${candidate[@]}" "${files[@]}"',
    'gh api --method PATCH "repos/$GH_REPO/releases/$id" --silent -F draft=false "${latest[@]}"',
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


def test_the_text_of_release_issues_no_delete_and_one_write_to_a_release_that_exists():
    """On the job's text, so the delete cannot come back unseen: no DELETE, no gh release
    delete, delete-asset, upload or edit, no --clobber, no curl; the lines that name gh are the
    listing, the download, the creation and the publication, each exactly as written here; and
    it runs no file. The publication sends `draft=false`, for a candidate `make_latest=false`
    with it, and nothing else, to the id the listing gave."""
    release = JOBS["release"]
    assert can_delete(release) == []
    assert [line for line in commands(release) if NAMES_GH.search(line)] == GH_LINES
    assert files_run(release) == []
    assert [s["uses"].split("@")[0] for s in release["steps"] if "uses" in s] == [
        "actions/download-artifact"
    ]
    run_ = CREATE["run"]
    assert re.findall(r"^ *(latest=.*)", run_, re.M) == [
        "latest=()",
        "latest=(-f make_latest=false)",
    ]
    assert re.findall(r"\bid=", run_) == [] and "read -r id draft pre ours names <<<" in run_
    # What the check sees, each added to the job as it is: the delete the job had, in the
    # ways gh takes a method, and the other ways to remove or replace what a release holds.
    other = "another line names gh: "
    for added, seen in {
        'gh api -X DELETE "repos/$GH_REPO/releases/$id"': ["the API's delete", other],
        'gh api -XDELETE "repos/$GH_REPO/releases/$id"': ["the API's delete", other],
        'gh api --method=delete "repos/$GH_REPO/releases/$id"': [other],
        'gh api "repos/$GH_REPO/releases/$id" \\\n  -F draft=false': [other],
        'gh api --method PATCH "repos/$GH_REPO/releases/$id" --silent -F draft=false'
        ' -F prerelease=true "${latest[@]}"': [other],
        'gh api --method PATCH "repos/$GH_REPO/releases/$id/assets" --silent -F draft=false'
        ' "${latest[@]}"': [other],
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
        'gh release create "$GITHUB_REF_NAME" --verify-tag --title "$GITHUB_REF_NAME"'
        ' --notes-file "$RUNNER_TEMP/body.md" "${candidate[@]}" "${files[@]}"': [other],
        'gh release edit "$GITHUB_REF_NAME" --draft=false': ["an edit of a release", other],
        '"$(command -v gh)" release view "$GITHUB_REF_NAME"': [other],
        "# then gh removes it": [other],
    }.items():
        job = {"steps": [*release["steps"], {"name": "added", "run": added}]}
        got = can_delete(job)
        assert [other if what.startswith(other) else what for what in got] == seen, (added, got)


def test_gh_is_asked_for_a_draft_so_the_release_it_makes_is_never_its_own_to_delete():
    """Why the creation carries --draft. gh's source (cli/cli at v2.102.0, read on 2026-11-20,
    pkg/cmd/release/create/create.go) says of `gh release create`, lines 497 to 512:
    `hasAssets := len(opts.Assets) > 0`, `draftWhileUploading := false`,
    `if hasAssets && !opts.Draft {` ... `// Save the release initially as draft and publish it
    after all assets have finished uploading`, `draftWhileUploading = true`,
    `params["draft"] = true`; lines 533 to 541: `cleanupDraftRelease := func(err error) error {`
    `if !draftWhileUploading {` `return err` `}` and only then `deleteRelease(httpClient,
    baseRepo.RepoHost(), safeurl.NewImmutableSafeURL(newRelease.APIURL))`; lines 550 to 560:
    `err = shared.ConcurrentUpload(...)`, `if err != nil {` `return cleanupDraftRelease(err)`,
    and `if draftWhileUploading {` `rel, err := publishRelease(...)`, `if err != nil {`
    `return cleanupDraftRelease(err)`. `deleteRelease` (create/http.go, 208 to 213) is an HTTP
    DELETE of the address GitHub gave the release, which names its id, whatever its state by
    then. So without --draft a publication GitHub made and whose reply was an error was
    followed by the delete of the published release (the fourth report's must-fix 1); with
    --draft, `draftWhileUploading` stays false, nothing is published and the cleanup returns
    the error and deletes nothing, so a failed upload leaves the draft with the files that
    arrived. The publication is then the job's: `publishRelease` (http.go, 182 to 206) sends
    PATCH to the release's address with `"draft": false` and, when --latest was given,
    `"make_latest"`, and the job's line sends the same and has nothing after it to undo.
    The upload (pkg/cmd/release/shared/upload.go): `ConcurrentUpload` runs
    `uploadWithDelete` for each file (111 to 128), which deletes a file only under
    `if a.ExistingURL != nil && a.ExistingURL.String() != "" {` (141 to 146) and then
    `backoff.Retry(...)` of `uploadAsset` with `backoff.WithMaxRetries(bo, 3)`, again only when
    `shouldRetry`: a network error or `httpError.StatusCode >= 500` (130 to 155);
    `AssetsFromArgs`, which `create` takes its files from (create.go, 172), builds each
    `AssetForUpload` with Open, Size, Name, Label and MIMEType and no `ExistingURL` (upload.go,
    62 to 70), and nothing in create.go or create/http.go sets one. No line of upload.go
    deletes a release. So a failed upload leaves the draft, and leaves it public when a person
    published it meanwhile (test_generated_release_retry_unit, "a draft a person publishes
    while its files upload"). The download (pkg/cmd/release/download/download.go, 167):
    `shared.FetchRelease(ctx, httpClient, baseRepo, opts.TagName)`, which is "FetchRelease
    finds a published repository release by its tagName, or a draft release by its pending
    tag name" (shared/fetch.go, 182): beside `releases/tags/TAG` it runs `fetchDraftRelease`
    (237 to 270), a GraphQL query `release(tagName: $tagName)` for `DatabaseID` and `IsDraft`
    and then REST `releases/<DatabaseID>`, and it takes "a release found by either lookup"
    (204 to 218). It lists nothing, and needs a token GraphQL shows the draft to; the source
    says only that the draft lookup fails "when unauthenticated". Every asset is then fetched
    by its own API address with `Accept: application/octet-stream` (download.go, 198 to 208,
    243, 313), a draft's like any other. Nothing here can run gh's code: the test holds the
    flag on the job's line, the README's sentence, the step's own sentences, and that the
    sentence about gh deleting its release is gone."""
    create, publish = GH_LINES[2:]
    assert re.search(r"^gh release create \S+ --draft ", create)
    assert create.endswith('"${files[@]}"')
    assert publish.startswith('gh api --method PATCH "repos/$GH_REPO/releases/$id" ')
    readme = " ".join(README.split())
    assert (
        "The job's own text issues no delete of a release or of a release's file and replaces no"
        " file, and `gh release create`, asked for a draft, publishes nothing and does not"
        " delete the release it made when an upload fails. The job's one write to a release"
        " that exists is the publication, which sets `draft` to false and nothing else (for a"
        " candidate it says again that the release is not the latest): on a draft a person"
        " published meanwhile it changes nothing, and on one a person deleted it fails."
    ) in readme
    assert "it deletes the release it made" not in readme
    assert "can be deleted with it" not in readme
    comment = " ".join(CREATE["run"].replace("#", "").split())
    assert (
        "Asked for a draft it publishes nothing, and its cleanup of the release it made is only"
        " for a draft it meant to publish itself. It sends a file again, three times at most,"
        " only after a failed connection or a 5xx, and removes a file only when given the"
        " address of one already there, which a creation never gives it. So an upload that"
        " fails leaves this draft, with the files that arrived, and leaves it public if a"
        " person published it meanwhile."
    ) in comment
    assert "(the CLI finds a draft by its tag, as it finds a release)" in comment


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
    """Each sentence, and the test that runs it: the four requests in their order and a re-run
    from each thing an attempt can leave (the tests at the top of this file); the reply lost
    (test_generated_release_retry_unit, "a publication done whose reply is lost"); the draft
    that is not whole, deleted and made again (below the draft's tests); the draft changed
    between the comparison and the publication (test_generated_release_retry_unit, "a draft
    changed between")."""
    readme = " ".join(README.split())
    assert (
        "The release's text ends in a mark, an HTML comment GitHub does not show, with the id of"
        " the run that made it. Job `release` makes the release with separate requests and takes"
        " none back: it makes a draft with the files (`gh release create --draft`), compares the"
        " draft's files with the ones this run attested, names and bytes, publishes the draft"
        " with a request of its own, to the draft's id, and looks again; it succeeds only on a"
        " published release that holds exactly those files, byte for byte. Each look lists the"
        " repository's releases, drafts included (a listing that fails stops the job), and takes"
        " the one for its tag as its own only when it carries this run's mark and this tag's"
        " pre-release flag. So a re-run of `release` starts from what an earlier attempt left:"
        " no release, and it makes the draft; this run's draft with exactly this run's files,"
        " and it publishes it; a published release with exactly those files, and it leaves it"
        " as it is and succeeds, so that a release tag's `aliases` runs (a candidate's run has"
        " none). A publication GitHub made while its reply was lost fails the job and loses"
        " nothing: the re-run finds the release published. A draft that does not hold exactly"
        " this run's files, as when an upload was cut, stops the job, and a person deletes it on"
        " the releases page and re-runs the job, which makes the release again (the table below"
        " has the line). Any other release for the tag stops the job."
    ) in readme
    (limits,) = [" ".join(p.split()) for p in README.split("\n") if p.startswith("Three limits.")]
    assert limits.endswith(
        " The third limit is job `release`'s: no request compares and publishes at once, so a"
        " draft a person changes between the job's comparison and its publication is published"
        " as changed; the job compares again after it publishes, stops on the difference and"
        " deletes nothing, so the changed release stays public: do not edit this tag's release"
        " by hand while the `release` job runs."
    )
    assert "deleted if it is still the same draft" not in readme
    assert "either way `aliases` runs" not in readme
    assert "no write to a release that exists" not in readme


def cells(key: str) -> tuple[str, str]:
    assert key in stops()
    (row,) = [line for line in README.splitlines() if line.startswith(f"| `{key}` |")]
    _, means, next_step = (cell.strip() for cell in row.strip("|").split(" | "))
    return means, next_step


def test_the_draft_s_stop_has_its_row_and_the_row_says_what_the_tests_above_run():
    means, next_step = cells(
        "the GitHub release for … is a draft that an earlier attempt of run … left unfinished,"
        " which this job neither deletes nor publishes"
    )
    assert means == (
        "A draft for the tag carries this run's mark and does not hold exactly the files this"
        " run attested: an earlier attempt's upload was cut, and it may hold none of them, some,"
        " or one cut off"
    )
    assert next_step == (
        "Delete it on the repository's releases page and re-run the job, which makes the release"
        " again (once the five days the run's files are kept have passed, re-run `package`"
        " instead, which `release` follows). Do not publish it by hand: the job then finds a"
        " public release it cannot finish."
    )
    # `package` re-run alone is followed by `release`, which then finds no release and makes it.
    assert "release" in rerun({"package"})


def test_a_look_that_does_not_show_the_job_s_own_write_has_its_row(tmp_path):
    """(The listing that is behind, and that a second release is never made:
    test_generated_release_retry_unit, "a listing that does not show the step's own write".)"""
    means, next_step = cells("GitHub does not list the release for … as this job has just left it")
    assert means == (
        "Just after the job made the draft, or published it, the listing shows no release for"
        " the tag, or still a draft: a person deleted it in that moment, or the listing did not"
        " show the job's own write"
    )
    assert next_step == (
        "Re-run the job, which starts from what is listed then: it makes the draft if there is"
        " no release, publishes this run's draft, or compares the published release. It never"
        " makes a second release in one attempt."
    )
    # A person deletes this run's whole draft just after the job published it: the job says so,
    # and the re-run, finding no release, makes it.
    existing = made_earlier("v1.2.3", False, draft=True)
    done, calls = run(tmp_path, "v1.2.3", "", existing, attached("v1.2.3"), DELETED_ONCE_PUBLIC="1")
    assert done.returncode == 1 and len(writes(calls)) == 1 and held(tmp_path) == []
    assert the_error(done) == (
        "::error::GitHub does not list the release for v1.2.3 as this job has just left it;"
        " re-run this job, which starts from what is listed then"
    )
    done, calls = attempt(tmp_path, "v1.2.3", "")
    assert done.returncode == 0, done.stdout + done.stderr
    assert [line.split()[1:3] for line in writes(calls)] == [
        ["release", "create"],
        ["api", "--method"],
    ]
    assert [(r["id"], r["draft"]) for r in held(tmp_path)] == [(88, False)]
