"""A retry of `release` ends well when an earlier attempt made the release and was not recorded
as done, or left it a draft (gh release create makes a draft, uploads the files, then publishes
it, in separate requests: gh's own help for the command): the step lists every release of the
repository, drafts included, and recognises the one for this tag only when its pre-release flag
is the one this tag gets and every file it holds is byte for byte one this run attested; a
published one must hold them all, and a draft gets the ones it lacks and is published. Then it
succeeds without making a release again, so `aliases` runs. A listing that fails is not taken
for none, and anything else stops the job in one sentence. gh is a stand-in that keeps the
releases as a JSON file and runs the step's own --jq expression with jq."""

import json
import subprocess
from pathlib import Path

import pytest
from test_ci_workflow_unit import JOBS

(CREATE,) = [s for s in JOBS["release"]["steps"] if s.get("name") == "Create the release"]

GH = r"""#!/bin/bash
printf 'gh %s\n' "$*" >> "$CALLS"
case "$1 $2" in
  "api --paginate")
    [ -z "$LIST_FAILS" ] || { echo "HTTP 502" >&2; exit 1; }
    jq_expr=""
    while [ $# -gt 0 ]; do
      if [ "$1" = "--jq" ]; then jq_expr="$2"; fi
      shift
    done
    jq -r "$jq_expr" "$EXISTING/releases.json" ;;
  "release upload")
    shift 3
    cp "$@" "$EXISTING/assets/" ;;
  "release edit")
    exit 0 ;;
  "release download")
    dir=""
    while [ $# -gt 0 ]; do
      if [ "$1" = "--dir" ]; then dir="$2"; fi
      shift
    done
    mkdir -p "$dir"
    cp "$EXISTING"/assets/* "$dir"/ ;;
  "release create")
    exit 0 ;;
  *) exit 2 ;;
esac
"""


def files_of(version: str) -> dict[str, bytes]:
    return {
        f"cairn_memory-{version}-py3-none-any.whl": b"a wheel",
        f"cairn_memory-{version}.tar.gz": b"a source archive",
        "SHA256SUMS": b"the checksums",
        "notes.md": b"the notes",
    }


def setup(tmp_path: Path, tag: str, existing: dict | None, assets: dict | None = None):
    version = tag[1:].split("-rc.")[0]
    release = tmp_path / "release"
    release.mkdir()
    for name, data in files_of(version).items():
        (release / name).write_bytes(data)
    there = tmp_path / "existing"
    (there / "assets").mkdir(parents=True)
    listed = [] if existing is None else existing if isinstance(existing, list) else [existing]
    (there / "releases.json").write_text(json.dumps(listed))
    if existing is not None:
        for name, data in (assets or {}).items():
            (there / "assets" / name).write_bytes(data)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text(GH)
    (bin_ / "gh").chmod(0o755)
    return bin_, there


def run(tmp_path: Path, tag: str, candidate: str, existing=None, assets=None, **env: str):
    bin_, there = setup(tmp_path, tag, existing, assets)
    calls = tmp_path / "calls"
    done = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-e", "-c", CREATE["run"]],
        env={
            "PATH": f"{bin_}:/usr/bin:/bin",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_REF_NAME": tag,
            "CANDIDATE": candidate,
            "CALLS": str(calls),
            "EXISTING": str(there),
            "GH_REPO": "owner/repo",
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    made = [line for line in calls.read_text().splitlines() if line.startswith("gh release create")]
    return done, made


def made_earlier(tag: str, prerelease: bool, draft: bool = False, names=None) -> dict:
    version = tag[1:].split("-rc.")[0]
    attached = names or [n for n in files_of(version) if n != "notes.md"]
    # As GET repos/OWNER/REPO/releases lists one (GitHub's REST reference).
    return {
        "id": 7,
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "assets": [{"name": n, "size": 1} for n in attached],
    }


def attached(tag: str) -> dict[str, bytes]:
    version = tag[1:].split("-rc.")[0]
    return {n: d for n, d in files_of(version).items() if n != "notes.md"}


def test_no_release_yet_makes_it(tmp_path):
    done, made = run(tmp_path, "v1.2.3", "")
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(made) == 1 and made[0].startswith("gh release create v1.2.3 --verify-tag")


@pytest.mark.parametrize(("tag", "candidate"), [("v1.2.3", ""), ("v1.2.3-rc.1", "true")])
def test_this_run_s_release_made_by_an_earlier_attempt_is_recognised_not_made_again(
    tmp_path, tag, candidate
):
    done, made = run(
        tmp_path, tag, candidate, made_earlier(tag, candidate == "true"), attached(tag)
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert made == []
    assert f"The GitHub release {tag} is this run's" in done.stdout


@pytest.mark.parametrize(
    ("tag", "candidate", "existing", "assets", "said"),
    [
        (
            "v1.2.3",
            "",
            made_earlier("v1.2.3", prerelease=True),
            attached("v1.2.3"),
            "its pre-release flag is true, not false",
        ),
        (
            "v1.2.3-rc.1",
            "true",
            made_earlier("v1.2.3-rc.1", prerelease=False),
            attached("v1.2.3-rc.1"),
            "its pre-release flag is false, not true",
        ),
        (
            "v1.2.3",
            "",
            made_earlier("v1.2.3", prerelease=True, draft=True),
            attached("v1.2.3"),
            "its pre-release flag is true, not false",
        ),
        (
            "v1.2.3",
            "",
            made_earlier("v1.2.3", False, draft=True, names=["SHA256SUMS", "other.txt"]),
            {"SHA256SUMS": b"the checksums", "other.txt": b"x"},
            "it holds other.txt, which this run did not attest",
        ),
        (
            "v1.2.3",
            "",
            made_earlier("v1.2.3", False, draft=True, names=["cairn_memory-1.2.3.tar.gz"]),
            {"cairn_memory-1.2.3.tar.gz": b"a source arch"},  # cut off mid-upload
            "cairn_memory-1.2.3.tar.gz is not the file this run attested",
        ),
        (
            "v1.2.3",
            "",
            [made_earlier("v1.2.3", False), made_earlier("v1.2.3", False, draft=True)],
            attached("v1.2.3"),
            "there are 2 releases for it",
        ),
        (
            "v1.2.3",
            "",
            made_earlier("v1.2.3", False, names=["SHA256SUMS", "cairn_memory-1.2.3.tar.gz"]),
            attached("v1.2.3"),
            "its files are [SHA256SUMS cairn_memory-1.2.3.tar.gz]",
        ),
        (
            "v1.2.3",
            "",
            made_earlier("v1.2.3", False),
            {**attached("v1.2.3"), "cairn_memory-1.2.3.tar.gz": b"another source archive"},
            "cairn_memory-1.2.3.tar.gz is not the file this run attested",
        ),
    ],
)
def test_a_release_that_is_not_this_run_s_stops_the_job_in_one_sentence(
    tmp_path, tag, candidate, existing, assets, said
):
    done, made = run(tmp_path, tag, candidate, existing, assets)
    assert done.returncode == 1
    assert made == []
    (error,) = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    assert error.startswith(f"::error::a GitHub release for {tag} exists and is not this run's: ")
    assert said in error
    assert error.count(". ") == 0


def test_the_files_are_compared_in_a_directory_of_their_own(tmp_path):
    """The release's files are downloaded beside, not over, the ones this run attested."""
    tag = "v1.2.3"
    done, _ = run(tmp_path, tag, "", made_earlier(tag, False), attached(tag))
    assert done.returncode == 0
    calls = (tmp_path / "calls").read_text()
    assert f"gh release download {tag} --dir {tmp_path}/published" in calls
    assert (tmp_path / "release" / "notes.md").read_bytes() == b"the notes"


def test_a_release_of_another_tag_is_none_of_this_one_s(tmp_path):
    done, made = run(tmp_path, "v1.2.3", "", made_earlier("v1.2.4", False), attached("v1.2.3"))
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(made) == 1


def test_a_listing_that_fails_is_not_taken_for_no_release(tmp_path):
    done, made = run(tmp_path, "v1.2.3", "", LIST_FAILS="1")
    assert done.returncode == 1 and made == []
    (error,) = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    assert error == (
        "::error::the repository's releases could not be listed, so whether v1.2.3 has one is"
        " not known; re-run this job"
    )


def test_the_listing_takes_every_page_and_drafts_by_their_tag():
    run_ = CREATE["run"]
    assert 'gh api --paginate "repos/$GH_REPO/releases?per_page=100"' in run_
    assert CREATE["env"]["GH_REPO"] == "${{ github.repository }}"
    assert JOBS["release"]["permissions"] == {"contents": "write"}  # drafts are listed to it


WHL, TGZ = "cairn_memory-1.2.3-py3-none-any.whl", "cairn_memory-1.2.3.tar.gz"
PUBLISH = "gh release edit v1.2.3 --draft=false"


@pytest.mark.parametrize(
    ("tag", "candidate", "names", "uploaded", "edit"),
    [
        ("v1.2.3", "", None, [], PUBLISH),
        ("v1.2.3-rc.1", "true", None, [], f"{PUBLISH.replace("3", "3-rc.1")} --latest=false"),
        ("v1.2.3", "", ["SHA256SUMS"], [WHL, TGZ], PUBLISH),
        ("v1.2.3", "", [], [WHL, TGZ, "SHA256SUMS"], PUBLISH),
    ],
)  # fmt: skip
def test_a_draft_left_by_a_lost_attempt_gets_its_missing_files_and_is_published(
    tmp_path, tag, candidate, names, uploaded, edit
):
    names = attached(tag) if names is None else {n: attached(tag)[n] for n in names}
    existing = made_earlier(tag, candidate == "true", draft=True, names=list(names))
    existing["assets"] = [{"name": n, "size": 1} for n in names]
    done, made = run(tmp_path, tag, candidate, existing, names)
    assert done.returncode == 0, done.stdout + done.stderr
    assert made == []
    calls = (tmp_path / "calls").read_text().splitlines()
    uploads = [line for line in calls if line.startswith("gh release upload")]
    assert uploads == ([f"gh release upload {tag} {' '.join(uploaded)}"] if uploaded else [])
    assert [line for line in calls if line.startswith("gh release edit")] == [edit]
    assert f"The draft GitHub release {tag} is this run's" in done.stdout


def test_the_retry_is_said_in_the_readme_with_its_condition():
    readme = " ".join((Path(__file__).resolve().parents[2] / "README.md").read_text().split())
    assert (
        "A re-run of `release` lists the repository's releases, drafts included (a listing that"
        " fails stops the job), and takes the one for its tag as this run's when it has this"
        " tag's pre-release flag and every file it holds is byte for byte one this run"
        " attested: a published one that holds them all is left as it is, and a draft, which"
        " gh leaves when its runner is lost while it uploads, gets the files it lacks and is"
        " published; either way `aliases` runs. Any other release for the tag stops the job."
    ) in readme
