"""What a person can still re-run of a tag's run, and until when (third report, "Release
retries"): the README's sentences, each beside what it rests on in the workflow.

Two clocks. The run keeps its two artifacts for five days: `image`, the build's archive, and
`release`, the files `package` built. GitHub re-runs a run, whole or in part, "up to 30 days
after its initial run", and "a workflow run can be re-run a maximum of 50 times" (Re-running
workflows and jobs, read on 2026-11-20); those two numbers are GitHub's and nothing here can
run them. Job by job, after the five days:

- before the image's version is published, a failed job of the gate that takes the archive
  cannot be re-run alone; all jobs can, since a tag's run builds nothing (image_build reads
  main's image again), while `release_check` still passes the tag;
- once the version is published and until the release exists, `verify` and `package` take no
  artifact, and `package` re-run alone builds the files again, followed by `attest` and
  `release`;
- once the release exists, `release_check` refuses a whole re-run; `aliases` takes no
  artifact, re-runs alone, and runs only after a `release` that passed; a candidate has no
  `aliases`;
- `release` recorded as failed although it made the release compares it with what `package`
  built again, and stops unless the bytes are the same, so `aliases` cannot run in that run:
  it decides from every release, so another release's moves this version's aliases too;
- when nothing can be re-run, a release tag's way on is the next patch version, and a
  candidate's the next candidate, until its release is tagged: then it is refused for good.

The registry is the stand-in of test_ci_promote_unit."""

import json
from pathlib import Path

import pytest
from test_ci_promote_unit import Runner, digest, manifest, run_aliases
from test_ci_release_unit import CANDIDATE
from test_ci_workflow_unit import JOBS, TAG, jobs_that_run, release_check, rerun

README = " ".join(
    (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8").split()
)
CI = README[README.index("## CI/CD") : README.index("## Planned")]
RELEASED = {"release_check": {"candidate": ""}}


def takes(name: str) -> set[str]:
    """The jobs with a step that downloads the artifact NAME."""
    return {
        job
        for job, definition in JOBS.items()
        for step in definition["steps"]
        if step.get("uses", "").startswith("actions/download-artifact")
        and step["with"]["name"] == name
    }


def test_the_jobs_that_take_an_artifact_are_the_ones_the_readme_names():
    assert takes("image") == {"image_scan", "image", "stack", "host", "publish"}
    assert takes("release") == {"attest", "release"}
    # `publish` takes the archive only when the registry lacks the digest.
    (fetch,) = [s for s in JOBS["publish"]["steps"] if s.get("uses", "").startswith("actions/d")]
    assert fetch["if"] == "steps.decide.outputs.archive == 'true'"
    for free in ("release_check", "verify", "package", "aliases"):
        assert free not in takes("image") | takes("release")
    assert (
        "after them, `publish` still finishes when the registry already holds the digest, and a"
        " job re-run alone that needs an artifact fails (the scan, the image check and the"
        " rehearsals need the archive; `attest` and `release` need the files), so on `main` the"
        " next push publishes a new build." in CI
    )


def test_until_the_release_exists_a_tag_is_finished_whole_or_from_package():
    assert (
        "On a tag, after the five days: while the tag has no GitHub release, re-run all its"
        " jobs, or, once its image is published, job `package` alone, which builds the files"
        " again and which `attest` and `release` follow; either way a draft an earlier attempt"
        " left is deleted by hand first, as its line says." in CI
    )
    # (Whole: test_ci_one_build_unit.test_a_tag_run_re_run_whole_promotes_the_same_digest...;
    # the draft: test_ci_release_retry_unit.test_the_draft_a_person_deletes_is_made_again...)
    assert rerun({"package"}) == {"package", "attest", "release", "aliases"}
    assert not rerun({"package"}) & {"release_check", "image_build", "publish", "verify"}
    package = JOBS["package"]
    assert [s["with"]["name"] for s in package["steps"] if "upload-artifact" in s.get("uses", "")]
    assert any("uv build" in s.get("run", "") for s in package["steps"])
    # A candidate's `package` is followed by the same jobs, and its run has no `aliases`.
    assert "aliases" not in jobs_that_run(CANDIDATE, {"release_check": {"candidate": "true"}})


def listing(*tags: str):
    return lambda args: (0, json.dumps([{"tagName": tag} for tag in tags]))


def test_once_the_release_exists_only_aliases_is_left_and_it_waits_for_a_release_that_passed():
    assert (
        "Once the release exists, `release_check` refuses a whole re-run and nothing is left to"
        " re-run but a failed `aliases`, which needs no artifact (a candidate's run has none)."
        in CI
    )
    with pytest.raises(release_check.Refused, match=r"v1\.2\.3 already has a GitHub release"):
        release_check.not_released("v1.2.3", listing("v1.2.2", "v1.2.3"))
    release_check.not_released("v1.2.3", listing("v1.2.2"))  # and passes until then
    assert rerun({"aliases"}) == {"aliases"}
    assert "aliases" in jobs_that_run(TAG, RELEASED)
    assert "aliases" not in jobs_that_run(TAG, RELEASED, failed=frozenset({"release"}))
    assert (
        "One case does not recover in its own run: a `release` recorded as failed although it"
        " made the release, re-run after the five days, compares the release with files"
        " `package` built again and stops unless they are the same bytes, and `aliases` runs"
        " only after a `release` that passed; another release's `aliases` then moves this"
        " version's aliases, as item 7 says." in CI
    )
    # (The comparison: test_ci_release_retry_unit, "is not the file this run attested".)


def test_a_failed_aliases_is_re_run_while_github_re_runs_the_run_and_another_release_s_after():
    assert (
        "a failed `release` within the five days the run's files are kept and a failed `aliases`"
        " for as long as GitHub re-runs the run: 30 days from its first run, and 50 re-runs at"
        " most. After that, `aliases` runs with the next release, in that tag's run, or re-run"
        " alone in any release's run that GitHub still re-runs, and moves this version's"
        " aliases too, since it decides from every release and the registry as they are then." in CI
    )
    assert "at any time" not in CI


@pytest.mark.parametrize(
    ("next_release", "after"),
    [
        ("0.2.0", {"latest": "0.2.0", "0.1": "0.1.1", "0.2": "0.2.0"}),
        ("0.1.2", {"latest": "0.1.2", "0.1": "0.1.2"}),
    ],
    ids=["the next release", "the next patch version"],
)
def test_another_release_s_aliases_moves_the_aliases_a_release_never_moved(
    tmp_path, next_release, after
):
    """v0.1.1 was released and its `aliases` never ran: `latest` and `0.1` still name 0.1.0.
    The `aliases` of the next release's run, whatever its tag, leaves them where they belong."""
    runner = Runner(tmp_path)
    for version in ("0.1.0", "0.1.1", next_release):
        runner.put(version, manifest(version))
    for alias in ("latest", "0.1"):
        runner.put(alias, manifest("0.1.0"))
    done = run_aliases(runner, ["v0.1.0", "v0.1.1", f"v{next_release}"])
    assert done.returncode == 0, done.stdout + done.stderr
    tags = runner.tags()
    assert {alias: tags[alias] for alias in after} == {
        alias: digest(manifest(version)) for alias, version in after.items()
    }


def test_when_nothing_can_be_re_run_the_next_patch_version_is_tagged():
    assert (
        "When nothing can be re-run (GitHub's 30 days or 50 re-runs are over, or `release_check`"
        " refuses the tag because a higher version of its `X.Y` is tagged), tag the next patch"
        " version." in CI
    )
    with pytest.raises(release_check.Refused, match=r"v0\.1\.2 is a higher version of 0\.1 "):
        release_check.is_highest("v0.1.1", ["v0.1.0", "v0.1.1", "v0.1.2"])
    assert release_check.is_highest("v0.1.2", ["v0.1.0", "v0.1.1", "v0.1.2"])
    # A candidate: the next one passes beside the first, with its own pre-release or without;
    # once the release is tagged, or released, every candidate of it is refused.
    assert (
        "For a candidate it is the next candidate, while `vX.Y.Z` is not tagged; once it is,"
        " `release_check` refuses every candidate of it and there is nothing left to recover." in CI
    )
    assert release_check.candidate("v1.2.3-rc.2")
    release_check.before_its_release("v1.2.3-rc.2", "1.2.3", ["v1.2.2", "v1.2.3-rc.1"])
    release_check.not_released("v1.2.3-rc.2", listing("v1.2.2", "v1.2.3-rc.1"), "1.2.3")
    for tag in ("v1.2.3-rc.1", "v1.2.3-rc.2"):
        with pytest.raises(release_check.Refused, match=r"v1\.2\.3 is tagged already"):
            release_check.before_its_release(tag, "1.2.3", ["v1.2.3-rc.1", "v1.2.3"])
        with pytest.raises(release_check.Refused, match=r"v1\.2\.3 already has a GitHub release"):
            release_check.not_released(tag, listing("v1.2.3"), "1.2.3")
    assert "a tag's run is re-run whole" not in CI


def test_both_uploads_replace_an_artifact_of_the_same_name_so_their_jobs_can_be_re_run():
    """actions/upload-artifact's README at the commit the workflow pins (cf430e03, v7.0.2,
    https://raw.githubusercontent.com/actions/upload-artifact/cf430e030ddbb5b0abf93d22962f4752f3646cd9/README.md)
    says of `overwrite`: "If true, an artifact with a matching name will be deleted before a new
    one is uploaded. If false, the action will fail if an artifact for the given name already
    exists. Does not fail if the artifact does not exist. Optional. Default is 'false'". It
    names no exception for a re-run of the job, so without it `package`, re-run while its
    first attempt's `release` artifact is still kept, would stop at its upload, and the
    README's "re-run `package`" and "a re-run of job `package` builds files of its own and
    `attest` attests those too" would hold only after the five days. Both uploads set it."""
    uploads = {
        step["with"]["name"]: (job, step["with"].get("overwrite"))
        for job, definition in JOBS.items()
        for step in definition["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    }
    assert uploads == {"image": ("image_build", True), "release": ("package", True)}
    assert {
        step["uses"]
        for definition in JOBS.values()
        for step in definition["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    } == {"actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"}
    assert JOBS["package"]["permissions"] == {}  # the upload uses the runner's own token
