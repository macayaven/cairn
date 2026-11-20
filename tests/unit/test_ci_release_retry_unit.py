"""A retry of `release` ends well when an earlier attempt made the release and was not recorded
as done (the runner lost after `gh release create` succeeded): the step recognises a release for
this tag only when it is not a draft, its pre-release flag is the one this tag gets, and its
files are byte for byte the ones this run attested; then it succeeds without making it again,
so `aliases` runs. Anything else stops the job in one sentence. gh is a stand-in that keeps the
release as a JSON file and runs the step's own --jq expression with jq."""

import json
import subprocess
from pathlib import Path

import pytest
from test_ci_workflow_unit import JOBS

(CREATE,) = [s for s in JOBS["release"]["steps"] if s.get("name") == "Create the release"]

GH = r"""#!/bin/bash
printf 'gh %s\n' "$*" >> "$CALLS"
case "$1 $2" in
  "release view")
    [ -e "$EXISTING/release.json" ] || { echo "release not found" >&2; exit 1; }
    jq_expr=""
    while [ $# -gt 0 ]; do
      if [ "$1" = "--jq" ]; then jq_expr="$2"; fi
      shift
    done
    jq -r "$jq_expr" "$EXISTING/release.json" ;;
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
    if existing is not None:
        (there / "release.json").write_text(json.dumps(existing))
        for name, data in (assets or {}).items():
            (there / "assets" / name).write_bytes(data)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text(GH)
    (bin_ / "gh").chmod(0o755)
    return bin_, there


def run(tmp_path: Path, tag: str, candidate: str, existing=None, assets=None):
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
    return {
        "tagName": tag,
        "isDraft": draft,
        "isPrerelease": prerelease,
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
            made_earlier("v1.2.3", prerelease=False, draft=True),
            attached("v1.2.3"),
            "it is a draft",
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
        (
            "v1.2.3",
            "",
            {**made_earlier("v1.2.3", False), "tagName": "v1.2.4"},
            attached("v1.2.3"),
            "its tag is v1.2.4",
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


def test_the_retry_is_said_in_the_readme_with_its_condition():
    readme = " ".join((Path(__file__).resolve().parents[2] / "README.md").read_text().split())
    assert (
        "A re-run of `release` after an attempt that made the release but was not recorded as"
        " done finds that release and, when it is not a draft, has this tag's pre-release flag"
        " and holds byte for byte the files this run attested, goes on without making it again,"
        " so `aliases` runs; any other release for the tag stops the job."
    ) in readme
