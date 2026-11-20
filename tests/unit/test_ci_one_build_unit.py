"""One build per commit: a tag run builds no image, it promotes the one main's run published.

A push to `main` builds the image once (job `image_build`), runs the whole gate on it and
publishes it as `main` and `sha-<commit>`; `sha-<commit>` is written once. A `v*` tag on that
commit, a candidate or a release, builds nothing: `image_build` reads `sha-<commit>` from the
registry by its digest into an OCI layout, the same script checks it, and the scan, the image
check and the rehearsals take it as they take a build, so every job after it acts on main's
image, and `publish` promotes that digest to the version's tag and to no `sha-` tag. A tag
whose commit main's run has not published stops before anything is published. The registry is
the stand-in of test_ci_promote_unit: files named by tag."""

import re
import subprocess

import pytest
from test_ci_promote_unit import Runner, digest, step
from test_ci_workflow_unit import JOBS, MAIN_PUSH, TAG, evaluate, expression, jobs_that_run

FETCH = "Fetch the image main's run published for this commit"
IS_MAIN = "Check that the archive is main's image"
DECIDE = "Look for this build in the registry"
PUSH = "Push the scanned build"
COMMIT = "c" * 40  # the commit of every context and run here; sha-ccccccc
MAIN = b'{"manifests":"main\'s build of the commit"}'


def candidate(context: dict) -> dict:
    github = {**context["github"], "ref": "refs/tags/v0.1.0-rc.1", "ref_name": "v0.1.0-rc.1"}
    return {**context, "github": github}


def builds(s: dict) -> bool:
    run = s.get("run", "")
    return (
        s.get("uses", "").startswith(("docker/build-push", "docker/setup-buildx"))
        or "docker build" in run
        or "buildx build" in run
    )


def step_runs(s: dict, context: dict) -> bool:
    return "if" not in s or evaluate(s["if"], context)


# --- a tag run builds no image ------------------------------------------------------------------


@pytest.mark.parametrize("context", [TAG, candidate(TAG)], ids=["release", "candidate"])
def test_a_tag_run_builds_no_image(context):
    ran = jobs_that_run(context, {"release_check": {"candidate": "false"}})
    assert {"image_build", "image_scan", "image", "stack", "host", "publish"} <= ran
    building = [
        (name, s.get("name"))
        for name in ran
        for s in JOBS[name]["steps"]
        if builds(s) and step_runs(s, context)
    ]
    assert building == []


def test_main_builds_the_image_once_and_only_in_image_build():
    building = {
        (name, s.get("name"))
        for name in jobs_that_run(MAIN_PUSH)
        for s in JOBS[name]["steps"]
        if builds(s) and step_runs(s, MAIN_PUSH)
    }
    assert {name for name, _ in building} == {"image_build"}


def test_the_rehearsals_rehearse_the_build_and_not_one_of_their_own():
    for name in ("stack", "host"):
        job = JOBS[name]
        assert "image_build" in (job["needs"] if isinstance(job["needs"], list) else [job["needs"]])
        assert job["env"]["IMAGE"] == "localhost:5000/cairn@${{ needs.image_build.outputs.amd64 }}"
        assert job["env"]["PLATFORM"] == "linux/amd64" and job["env"]["ARCH"] == "amd64"
        assert job["env"]["LOCAL"] == "cairn:ci"
        names = [s.get("name") for s in job["steps"]]
        for shared in (
            "Push the build to a registry on the runner, as publish pushes it",
            "Check that this platform's image is built for it",
            "Pull this platform of the build",
        ):
            assert step(name, shared) == step("image", shared), (name, shared)
        assert names.index("Pull this platform of the build") < next(
            i for i, s in enumerate(job["steps"]) if "scripts/rehearse_" in s.get("run", "")
        )


# --- image_build on a tag: main's image, read by its digest -------------------------------------


def fetch(runner: Runner, **env: str) -> subprocess.CompletedProcess:
    (runner.tmp / "image").mkdir(exist_ok=True)
    return runner.run(step("image_build", FETCH)["run"], GITHUB_SHA=COMMIT, **env)


def test_on_a_tag_image_build_reads_sha_commit_by_its_digest(tmp_path):
    runner = Runner(tmp_path)
    runner.put("sha-ccccccc", MAIN)
    runner.put("sha-ddddddd", b"another commit's build")
    done = fetch(runner)
    assert done.returncode == 0, done.stdout + done.stderr
    main = digest(MAIN)
    assert runner.oras_calls() == [
        "manifest fetch ghcr.io/owner/repo:sha-ccccccc",
        # By digest: a write to sha-ccccccc after this read cannot change what is checked.
        f"cp --to-oci-layout ghcr.io/owner/repo@{main} {tmp_path}/image/layout:main",
    ]
    assert (tmp_path / "github_output").read_text() == f"digest={main}\n"
    listed = subprocess.run(
        ["tar", "-tf", str(tmp_path / "image" / "cairn.oci.tar")],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    # The names oci_archive.py reads: index.json at the top, blobs/sha256/<hex>.
    assert "index.json" in listed and "oci-layout" in listed
    assert f"blobs/sha256/{main.split(':')[1]}" in listed


def test_a_tag_whose_commit_main_has_not_published_stops_in_one_sentence(tmp_path):
    runner = Runner(tmp_path)
    runner.put("sha-ddddddd", b"another commit's build")
    (tmp_path / "image" / "cairn.oci.tar").unlink()
    done = fetch(runner)
    assert done.returncode == 1
    (error,) = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    assert error.count(". ") == 0
    assert "ghcr.io/owner/repo:sha-ccccccc" in error and COMMIT in error
    assert "run this tag's workflow again once main's run for that commit has published it" in (
        error
    )
    assert not any(call.startswith("cp ") for call in runner.oras_calls())
    assert not (tmp_path / "image" / "cairn.oci.tar").exists()


def test_the_tag_path_names_the_commit_as_main_s_publish_names_it():
    """sha-<the first 7 characters>: metadata-action's default short SHA (its README: the length
    is 7 unless DOCKER_METADATA_SHORT_SHA_LENGTH says otherwise), which the workflow never sets."""
    import yaml

    assert "DOCKER_METADATA_SHORT_SHA_LENGTH" not in yaml.safe_dump(JOBS)
    assert (
        'commit="sha-$(printf \'%s\' "$GITHUB_SHA" | cut -c1-7)"'
        in step("image_build", FETCH)["run"]
    )


def test_image_build_s_tag_steps_run_on_a_tag_only_and_its_build_on_a_branch_only():
    job = JOBS["image_build"]
    for s in job["steps"]:
        on_tag, on_main = step_runs(s, TAG), step_runs(s, MAIN_PUSH)
        if builds(s) or s.get("uses", "").startswith("docker/setup-qemu"):
            assert (on_tag, on_main) == (False, True), s.get("name")
        elif s.get("name") in (FETCH, IS_MAIN) or (s.get("name") == "Install oras" and "if" in s):
            assert (on_tag, on_main) == (True, False), s.get("name")
        else:
            assert (on_tag, on_main) == (True, True), s.get("name")
    # The archive step and the upload are the same on both paths.
    names = [s.get("name") for s in job["steps"]]
    assert names.index(FETCH) < names.index("Check the archive and give its digests")
    assert names.index("Check the archive and give its digests") < names.index(IS_MAIN)


@pytest.mark.parametrize(("archived", "fails"), [("same", False), ("other", True)])
def test_the_archive_must_be_main_s_image(tmp_path, archived, fails):
    main = digest(MAIN)
    other = "sha256:" + "e" * 64
    s = step("image_build", IS_MAIN)
    assert s["env"] == {
        "MAIN": "${{ steps.main.outputs.digest }}",
        "ARCHIVED": "${{ steps.archive.outputs.digest }}",
    }
    done = Runner(tmp_path).run(s["run"], MAIN=main, ARCHIVED=main if archived == "same" else other)
    assert (done.returncode != 0) is fails
    if fails:
        assert done.stdout.startswith(
            f"::error::the archive holds {other}, not main's image {main}"
        )


# --- publish: a tag promotes main's digest; nothing writes sha- twice ----------------------------


def tags_for(context: dict) -> list[str]:
    """The tags the metadata step asks for, as its inputs say."""
    meta = next(s for s in JOBS["publish"]["steps"] if s.get("id") == "meta")
    rules = [line.strip() for line in meta["with"]["tags"].splitlines() if line.strip()]
    names = []
    for rule in rules:
        enable = re.search(r"enable=(\$\{\{ .+ \}\})", rule)
        if enable and not evaluate(expression(enable[1]), {**context, "needs": {}}):
            continue
        names.append(rule.split(",")[0])
    return names


def test_a_tag_run_asks_for_no_sha_tag_and_main_for_one():
    assert tags_for(TAG) == ["type=semver", "type=ref"]
    assert tags_for(candidate(TAG)) == ["type=semver", "type=ref"]
    assert "type=sha" in tags_for(MAIN_PUSH)


def promote(runner: Runner, ref: str, names: list[str], digest_: str, **env: str):
    """Job `publish` as the runner runs it: the decision, the artifact only if it asks for it,
    then the push. Returns (decision, push or None, whether the archive was fetched)."""
    ref_type = "tag" if not ref.startswith("main") else "branch"
    base = {
        "GITHUB_REF_TYPE": ref_type,
        "GITHUB_REF_NAME": ref,
        "GITHUB_SHA": COMMIT,
        "TAGS": "\n".join(f"ghcr.io/owner/repo:{name}" for name in names),
        "DIGEST": digest_,
        **env,
    }
    decided = runner.run(step("publish", DECIDE)["run"], **base)
    if decided.returncode != 0:
        return decided, None, False
    outputs = dict(
        line.split("=", 1) for line in (runner.tmp / "github_output").read_text().splitlines()
    )
    fetched = outputs["archive"] == "true"
    if fetched and not (runner.tmp / "image" / "cairn.oci.tar").exists():
        raise AssertionError("the run asked for an artifact that is gone")
    pushed = runner.run(
        step("publish", PUSH)["run"],
        **base,
        IMAGE=outputs["image"],
        NAMES=outputs["names"],
        FIRST=outputs["first"],
        COPY=outputs["copy"],
        FROM_ARCHIVE=outputs["archive"],
    )
    return decided, pushed, fetched


def test_publish_decides_before_it_fetches_the_archive():
    job = JOBS["publish"]
    names = [s.get("name") for s in job["steps"]]
    fetch_build = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/download"))
    assert names.index(DECIDE) < names.index(fetch_build["name"]) < names.index(PUSH)
    assert fetch_build["if"] == "steps.decide.outputs.archive == 'true'"
    assert step("publish", PUSH)["env"] == {
        "DIGEST": "${{ needs.image_build.outputs.digest }}",
        "ARCHIVE": "${{ needs.image_build.outputs.archive }}",
        "IMAGE": "${{ steps.decide.outputs.image }}",
        "NAMES": "${{ steps.decide.outputs.names }}",
        "FIRST": "${{ steps.decide.outputs.first }}",
        "COPY": "${{ steps.decide.outputs.copy }}",
        "FROM_ARCHIVE": "${{ steps.decide.outputs.archive }}",
    }


def test_a_candidate_and_its_release_on_one_commit_promote_main_s_digest(tmp_path):
    runner = Runner(tmp_path)
    runner.put("sha-ccccccc", MAIN)
    runner.put("main", MAIN)
    (tmp_path / "image" / "cairn.oci.tar").unlink()  # a tag run needs no archive at all
    main = digest(MAIN)
    for ref, version in (("v1.2.3-rc.1", "1.2.3-rc.1"), ("v1.2.3", "1.2.3")):
        assert fetch(runner).returncode == 0
        assert (tmp_path / "github_output").read_text() == f"digest={main}\n"
        (tmp_path / "image" / "layout").rename(tmp_path / f"layout-{version}")
        decided, pushed, fetched = promote(runner, ref, [version], main)
        assert decided.returncode == 0, decided.stdout + decided.stderr
        assert pushed.returncode == 0, pushed.stdout + pushed.stderr
        assert not fetched
    assert runner.tags() == {
        "sha-ccccccc": main,
        "main": main,
        "1.2.3-rc.1": main,
        "1.2.3": main,
    }
    assert not any(call.startswith("cp --from-oci-layout") for call in runner.oras_calls())


def test_main_publishes_its_build_from_the_archive_as_main_and_sha_commit(tmp_path):
    runner = Runner(tmp_path)
    built = digest(runner.built)
    decided, pushed, fetched = promote(runner, "main", ["main", "sha-ccccccc"], built)
    assert decided.returncode == 0 and pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert fetched
    assert runner.tags() == {"main": built, "sha-ccccccc": built}
    assert (tmp_path / "github_output").read_text().endswith(f"digest={built}\n")


def test_a_retry_after_the_artifact_expired_finishes_without_it(tmp_path):
    """The first attempt pushed sha-ccccccc and failed after; five days later the artifact is
    gone, and re-running the failed jobs still moves `main` to the same digest."""
    runner = Runner(tmp_path)
    built = digest(runner.built)
    runner.put("sha-ccccccc", runner.built)
    (tmp_path / "image" / "cairn.oci.tar").unlink()
    decided, pushed, fetched = promote(runner, "main", ["main", "sha-ccccccc"], built)
    assert decided.returncode == 0, decided.stdout + decided.stderr
    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert not fetched and "is this build already" in decided.stdout
    assert runner.tags() == {"main": built, "sha-ccccccc": built}


def test_a_version_retried_after_the_artifact_expired_finishes_without_it(tmp_path):
    runner = Runner(tmp_path)
    runner.put("sha-ccccccc", MAIN)
    runner.put("1.2.3", MAIN)
    (tmp_path / "image" / "cairn.oci.tar").unlink()
    decided, pushed, fetched = promote(runner, "v1.2.3", ["1.2.3"], digest(MAIN))
    assert decided.returncode == 0 and pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert not fetched
    assert not any(call.startswith(("cp ", "tag ")) for call in runner.oras_calls())


@pytest.mark.parametrize(
    ("ref", "names", "first", "said"),
    [
        ("main", ["main", "sha-ccccccc"], "sha-ccccccc", "a commit's image is published once"),
        ("v1.2.3", ["1.2.3"], "1.2.3", "a full version is published once"),
    ],
)
def test_sha_commit_and_a_version_are_each_written_once(tmp_path, ref, names, first, said):
    runner = Runner(tmp_path)
    runner.put(first, b"the build an earlier run published")
    before = runner.tags()
    built = digest(runner.built)
    decided, pushed, fetched = promote(runner, ref, names, built)
    assert decided.returncode == 1 and pushed is None and not fetched
    assert runner.tags() == before
    (error,) = [line for line in decided.stdout.splitlines() if line.startswith("::error::")]
    assert error == (
        f"::error::ghcr.io/owner/repo:{first} is {before[first]} already, not this run's build"
        f" {built}: {said}; to finish the run that published it, re-run only its failed jobs"
    )


def test_a_tag_s_names_hold_no_sha_tag_even_if_asked(tmp_path):
    runner = Runner(tmp_path)
    runner.put("sha-ccccccc", MAIN)
    decided, pushed, _ = promote(runner, "v1.2.3", ["1.2.3", "sha-ccccccc"], digest(MAIN))
    assert decided.returncode == 1 and pushed is None
    assert "a tag run writes no sha- tag" in decided.stdout
