"""One build per commit: a tag run builds no image, it promotes the one main's run published.

A push to `main` builds the image once (job `image_build`), runs the whole gate on it and
publishes it as `main` and `sha-<commit>`; `sha-<commit>` is written once. A `v*` tag on that
commit, a candidate or a release, builds nothing: `image_build` reads `sha-<commit>` from the
registry by its digest into an OCI layout, the same script checks it, and the scan, the image
check and the rehearsals take it as they take a build, so every job after it acts on main's
image, and `publish` promotes that digest to the version's tag and to no `sha-` tag. A tag
whose commit main's run has not published stops before anything is published. The registry is
the stand-in of test_ci_promote_unit: files named by tag."""

import json
import re
import subprocess
import tarfile
from pathlib import Path

import pytest
from test_ci_promote_unit import RECEIVE, Runner, digest, step
from test_ci_workflow_unit import (
    JOBS,
    MAIN_PUSH,
    TAG,
    evaluate,
    expression,
    job_comment,
    jobs_that_run,
    needs,
)
from test_oci_archive_unit import as_oras_writes
from test_oci_archive_unit import build as oci_build

FETCH = "Fetch the image main's run published for this commit"
ATTESTED = "Check that main's run attested this image for this commit"
COPY = "Copy the image by its digest into a fresh layout and check it"
HAND_ON = "Hand on main's image as this run's archive"
SAME = "Check that the copy is the build"
ARCHIVE = "Check the archive and give its digests"
DECIDE = "Look for this build in the registry"
PUSH = "Push the scanned build"
REPO = Path(__file__).resolve().parents[2]
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
    assert runner.oras_calls() == ["manifest fetch ghcr.io/owner/repo:sha-ccccccc"]
    # The digest of the bytes read, which the attestation check and the copy then name: a write
    # to sha-ccccccc after this read cannot change what is checked.
    assert (tmp_path / "github_output").read_text() == (
        f"image=ghcr.io/owner/repo\ndigest={main}\n"
    )
    assert (tmp_path / "main.json").read_bytes() == MAIN


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
    assert (tmp_path / "github_output").read_text() == ""


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
        elif s.get("name") in (FETCH, ATTESTED, COPY, HAND_ON, "Install oras"):
            assert (on_tag, on_main) == (True, False), s.get("name")
        else:
            assert (on_tag, on_main) == (True, True), s.get("name")
    # The archive step and the upload are the same on both paths; on a tag, nothing is copied
    # before main's attestation of the digest is checked.
    names = [s.get("name") for s in job["steps"]]
    order = [FETCH, ATTESTED, COPY, HAND_ON, ARCHIVE]
    assert [names.index(name) for name in order] == sorted(names.index(name) for name in order)


def test_on_a_tag_the_archive_step_takes_main_s_digest_as_the_root():
    """The archive step is the build's on a branch; on a tag the layout oras wrote lists more
    than the index, and the script takes the index by the digest the attestation was checked
    for, so no other root can pass."""
    archive = step("image_build", ARCHIVE)
    assert archive["run"] == (
        'python3 scripts/oci_archive.py "$RUNNER_TEMP/image/cairn.oci.tar"'
        ' ${ROOT:+--root "$ROOT"} --github-output "$GITHUB_OUTPUT"'
    )
    assert archive["env"] == {"ROOT": "${{ steps.main.outputs.digest }}"}
    assert step("image_build", HAND_ON)["run"] == (
        'mv "$RUNNER_TEMP/copied/cairn.oci.tar" "$RUNNER_TEMP/image/cairn.oci.tar"'
    )


# --- the attestation main's run made, checked before a tag promotes anything -------------------


GH_STANDIN = r"""#!/bin/bash
printf 'gh' >> "$CALLS"; printf ' [%s]' "$@" >> "$CALLS"; printf '\n' >> "$CALLS"
if [ "$1" = --version ]; then
  printf '%s\n' "${GH_VERSION-gh version 2.105.0 (2026-11-04)}"
  printf 'https://github.com/cli/cli/releases/tag/v2.105.0\n'
  exit 0
fi
[ -z "$GH_ERR" ] || printf '%s\n' "$GH_ERR" >&2
exit "${GH_EXIT:-0}"
"""


def test_image_build_may_read_attestations_and_nothing_else_new():
    assert JOBS["image_build"]["permissions"] == {"contents": "read", "attestations": "read"}


@pytest.mark.parametrize("verified", [True, False])
def test_a_tag_promotes_only_a_digest_main_s_run_attested_for_its_commit(tmp_path, verified):
    """gh attestation verify, on the bytes fetched (their SHA-256 is the digest): signed by this
    repository's ci-cd.yml (--repo, --signer-workflow), for a run on refs/heads/main
    (--source-ref) at the tag's full commit (--source-digest), with the default predicate type,
    SLSA provenance v1, which actions/attest makes."""
    runner = Runner(tmp_path)
    (runner.bin / "gh").write_text(GH_STANDIN)
    s = step("image_build", ATTESTED)
    assert s["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "MAIN": "${{ steps.main.outputs.digest }}",
    }
    done = runner.run(
        s["run"],
        GITHUB_SHA=COMMIT,
        MAIN=digest(MAIN),
        GH_EXIT="0" if verified else "1",
        GH_ERR="" if verified else "no attestations found",
    )
    calls = [line for line in runner.calls.read_text().splitlines() if line.startswith("gh")]
    assert calls == [
        "gh [--version]",
        f"gh [attestation] [verify] [{tmp_path}/main.json] [--repo] [Owner/Repo]"
        " [--signer-workflow] [github.com/Owner/Repo/.github/workflows/ci-cd.yml]"
        f" [--source-ref] [refs/heads/main] [--source-digest] [{COMMIT}]",
    ]
    assert (done.returncode == 0) is verified
    if not verified:
        (error,) = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
        assert error.count(". ") == 0
        assert digest(MAIN) in error and COMMIT in error and "refs/heads/main" in error
        assert (
            "run this tag's workflow again once main's run for that commit has attested it" in error
        )


def test_a_gate_runner_without_gh_is_told_so_and_not_that_main_s_run_attested_nothing(tmp_path):
    """CAIRN_GATE_RUNNER may name a runner of the operator's own, which need not have gh. The
    step's PATH holds nothing at all, since GitHub's Ubuntu image, where this test also runs,
    has gh in /usr/bin."""
    runner = Runner(tmp_path)
    nothing = tmp_path / "nothing"
    nothing.mkdir()
    done = runner.run(
        step("image_build", ATTESTED)["run"],
        PATH=str(nothing),
        GITHUB_SHA=COMMIT,
        MAIN=digest(MAIN),
    )
    assert done.returncode == 1
    (error,) = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    assert error.count(". ") == 0
    assert "has no attestation" not in error
    assert "gh is not on this runner" in error and "CAIRN_GATE_RUNNER" in error


def attestation_check(tmp_path, **env: str):
    runner = Runner(tmp_path)
    (runner.bin / "gh").write_text(GH_STANDIN)
    done = runner.run(
        step("image_build", ATTESTED)["run"], GITHUB_SHA=COMMIT, MAIN=digest(MAIN), **env
    )
    calls = [line for line in runner.calls.read_text().splitlines() if line.startswith("gh")]
    errors = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    return done, calls, errors


@pytest.mark.parametrize("version", ["2.102.0", "2.105.0", "2.110.12", "3.0.0"])
def test_a_gh_at_or_after_the_fix_of_both_advisories_checks_the_attestation(tmp_path, version):
    done, calls, _ = attestation_check(tmp_path, GH_VERSION=f"gh version {version} (2026-11-01)")
    assert done.returncode == 0
    assert calls[0] == "gh [--version]" and calls[1].startswith("gh [attestation] [verify]")


@pytest.mark.parametrize(
    ("printed", "said"),
    [
        ("gh version 2.101.0 (2026-09-15)", "2.101.0"),
        ("gh version 2.51.3 (2024-06-01)", "2.51.3"),
        ("gh version 1.999.0 (2021-01-01)", "1.999.0"),
        ("gh version (devel)", "of a version it does not print"),
        ("", "of a version it does not print"),
    ],
)
def test_a_gh_before_2_102_0_is_not_trusted_with_the_check(tmp_path, printed, said):
    """GHSA-wjmr-j3rp-mh2g (--signer-workflow matched as a prefix, v2.51.0 to v2.101.0) and
    GHSA-4mq3-hpgx-9cx8 (--source-ref matched without case, v2.68.0 to v2.101.0) were both fixed
    in v2.102.0, as the advisories and that release's notes say."""
    done, calls, errors = attestation_check(tmp_path, GH_VERSION=printed)
    assert done.returncode == 1
    assert calls == ["gh [--version]"]  # nothing is asked of it
    assert errors == [
        f"::error::gh on this runner is {said}, before 2.102.0, which fixed how gh attestation"
        " verify matches --signer-workflow and --source-ref, so it is not trusted with main's"
        " attestation; update GitHub CLI on the runner CAIRN_GATE_RUNNER names, or unset it to"
        " use GitHub's"
    ]


@pytest.mark.parametrize(
    "said",
    [
        "no attestations found",
        "expected SourceRepositoryRef to be refs/heads/main, got refs/tags/v1.2.3",
        "expected SourceRepositoryDigest to be 0123, got 4567",
        'verifying with issuer "sigstore.dev"',
    ],
)
def test_no_attestation_that_matches_is_said_apart_from_a_failure_of_the_tool(tmp_path, said):
    """gh prints nothing but the error off a terminal (its handler writes only to a TTY), and
    these are the errors of v2.102.0 and v2.105.0 for no attestation, and for one that is not
    main's at this commit or not signed as the workflow says."""
    done, _, errors = attestation_check(tmp_path, GH_EXIT="1", GH_ERR=said)
    assert done.returncode == 1
    (error,) = errors
    assert error.startswith(f"::error::{digest(MAIN)} has no attestation signed by")
    assert said in done.stdout + done.stderr  # gh's own words stay in the log


@pytest.mark.parametrize(
    "said",
    [
        "HTTP 502: Bad Gateway (https://api.github.com/repos/Owner/Repo/attestations/sha256:1)",
        "error connecting to api.github.com",
        # GitHub's REST reference lists only 200 for List attestations, and gh turns an empty
        # list into "no attestations found"; a 404 is the API failing, and re-running is right.
        "HTTP 404: Not Found (https://api.github.com/repos/Owner/Repo/attestations/sha256:1)",
        "HTTP 401: Bad credentials",
        "",
    ],
)
def test_a_tool_or_api_failure_is_not_taken_for_a_missing_attestation(tmp_path, said):
    done, _, errors = attestation_check(tmp_path, GH_EXIT="1", GH_ERR=said)
    assert done.returncode == 1
    assert errors == [
        f"::error::gh attestation verify could not check {digest(MAIN)}: the tool failed or"
        " GitHub's API could not be reached, as its own words above say; re-run this job"
    ]


def test_main_s_run_attests_its_published_image_with_nothing_but_the_signing_rights():
    """attest_main: after publish and verify on a push to main, the same two steps as attest's
    image attestation, holding an identity token and the right to store attestations; it checks
    out nothing, runs nothing from the repository and pushes nothing to the registry
    (push-to-registry is off by default, so no packages permission)."""
    job = JOBS["attest_main"]
    assert job["permissions"] == {"id-token": "write", "attestations": "write"}
    assert set(needs(job)) == {"publish", "verify"}
    assert job["if"] == "github.event_name == 'push' && github.ref_type == 'branch'"
    assert job["runs-on"] == JOBS["attest"]["runs-on"]
    names = [s.get("name") for s in job["steps"]]
    assert names == ["The image's name", "Attest the image"]
    for name in names:
        assert step("attest_main", name) == step("attest", name), name
    assert "push-to-registry" not in step("attest_main", "Attest the image")["with"]
    assert not any("checkout" in s.get("uses", "") for s in job["steps"])
    ran = jobs_that_run(MAIN_PUSH)
    assert "attest_main" in ran and "attest" not in ran
    assert "attest_main" not in jobs_that_run(TAG, {"release_check": {"candidate": "false"}})


# --- the round trip a tag does, proved by every pull request ----------------------------------


def oras_layout(tmp_path: Path, **case) -> tuple[Path, str]:
    """A layout as oras 1.3.6 writes it for the made-up build of test_oci_archive_unit."""
    built, root = oci_build(tmp_path, oras=as_oras_writes, **case)
    layout = tmp_path / "written"
    layout.mkdir()
    with tarfile.open(built) as archive:
        archive.extractall(layout, filter="data")
    built.unlink()
    return layout, root


def copy_runner(tmp_path: Path) -> Runner:
    runner = Runner(tmp_path)
    (tmp_path / "scripts").symlink_to(REPO / "scripts")
    return runner


def test_the_copy_step_is_one_step_in_image_build_and_in_every_scan():
    on_tag, in_scan = step("image_build", COPY), step("image_scan", COPY)
    bare = [{k: v for k, v in s.items() if k not in ("env", "if")} for s in (on_tag, in_scan)]
    assert bare[0] == bare[1]
    assert on_tag["env"] == {
        "SOURCE": "${{ steps.main.outputs.image }}",
        "DIGEST": "${{ steps.main.outputs.digest }}",
    }
    assert in_scan["env"] == {
        "SOURCE": "localhost:5000/cairn",
        "DIGEST": "${{ needs.image_build.outputs.digest }}",
    }
    assert "if" not in in_scan  # every scan, on every pull request
    names = [s.get("name") for s in JOBS["image_scan"]["steps"]]
    assert names.index(RECEIVE) < names.index(COPY) < names.index(SAME)
    assert names.index(SAME) < names.index("Check that this platform's image is built for it")


def test_the_copy_of_a_layout_as_oras_writes_it_passes_the_real_script(tmp_path):
    layout, root = oras_layout(tmp_path)
    runner = copy_runner(tmp_path)
    done = runner.run(
        step("image_scan", COPY)["run"], SOURCE="localhost:5000/cairn", DIGEST=root,
        ORAS_LAYOUT=str(layout),
    )  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    assert runner.oras_calls() == [
        f"cp --to-oci-layout localhost:5000/cairn@{root} {tmp_path}/copied/layout:main"
    ]
    outputs = dict(
        line.split("=", 1) for line in (tmp_path / "github_output").read_text().splitlines()
    )
    assert outputs["digest"] == root and set(outputs) == {"digest", "archive", "amd64", "arm64"}
    same = step("image_scan", SAME)
    assert same["env"] == {
        "BUILT": "${{ needs.image_build.outputs.digest }} ${{ needs.image_build.outputs.amd64 }}"
        " ${{ needs.image_build.outputs.arm64 }}",
        "COPIED": "${{ steps.copied.outputs.digest }} ${{ steps.copied.outputs.amd64 }}"
        " ${{ steps.copied.outputs.arm64 }}",
    }
    copied = f"{outputs['digest']} {outputs['amd64']} {outputs['arm64']}"
    assert runner.run(same["run"], BUILT=copied, COPIED=copied).returncode == 0
    other = f"{outputs['digest']} {outputs['arm64']} {outputs['amd64']}"
    refused = runner.run(same["run"], BUILT=other, COPIED=copied)
    assert refused.returncode == 1
    assert refused.stdout.startswith(f"::error::the copy read back from the registry is {copied}")


def test_a_copy_missing_a_manifest_s_blob_is_refused_before_anything_is_scanned(tmp_path):
    layout, root = oras_layout(tmp_path)
    index = json.loads((layout / "index.json").read_text())
    child = next(e for e in index["manifests"] if e["digest"] != root)
    (layout / "blobs" / "sha256" / child["digest"].split(":")[1]).unlink()
    runner = copy_runner(tmp_path)
    done = runner.run(
        step("image_scan", COPY)["run"], SOURCE="localhost:5000/cairn", DIGEST=root,
        ORAS_LAYOUT=str(layout),
    )  # fmt: skip
    assert done.returncode == 1
    assert "::error::the build's archive is refused: the layout has no blob for" in done.stderr


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
        "GH_TOKEN": "${{ github.token }}",
    }


def test_a_candidate_and_its_release_on_one_commit_promote_main_s_digest(tmp_path):
    runner = Runner(tmp_path)
    runner.put("sha-ccccccc", MAIN)
    runner.put("main", MAIN)
    (tmp_path / "image" / "cairn.oci.tar").unlink()  # a tag run needs no archive at all
    main = digest(MAIN)
    for ref, version in (("v1.2.3-rc.1", "1.2.3-rc.1"), ("v1.2.3", "1.2.3")):
        assert fetch(runner).returncode == 0
        assert (tmp_path / "github_output").read_text().endswith(f"digest={main}\n")
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


def test_a_tag_run_re_run_whole_promotes_the_same_digest_again_and_finishes(tmp_path):
    """The README's re-run sentence for a tag: its run builds nothing, so a second attempt of
    every job reads the same `sha-<commit>`, finds the version holding that digest and carries
    on, where main's full re-run builds again and stops."""
    runner = Runner(tmp_path)
    runner.put("sha-ccccccc", MAIN)
    (tmp_path / "image" / "cairn.oci.tar").unlink()
    main = digest(MAIN)
    for _attempt in (1, 2):
        assert fetch(runner).returncode == 0
        assert (tmp_path / "github_output").read_text().endswith(f"digest={main}\n")
        decided, pushed, fetched = promote(runner, "v1.2.3", ["1.2.3"], main)
        assert decided.returncode == 0, decided.stdout + decided.stderr
        assert pushed.returncode == 0, pushed.stdout + pushed.stderr
        assert not fetched
    assert "is this build already" in decided.stdout
    assert runner.tags() == {"sha-ccccccc": main, "1.2.3": main}


def test_a_tag_whose_digest_the_registry_does_not_give_copies_the_archive_by_that_digest(
    tmp_path,
):
    """The third report: `publish` asks the registry for the digest and takes any failure, an
    error as much as "not there", for "copy the archive", on a tag as on main (the step tells
    neither the failures nor the refs apart). On a tag the archive is main's image, read by
    that digest in image_build, so what is copied is the same digest and the version names it."""
    decide = step("publish", DECIDE)["run"]
    lookup = 'if [ "$copy" = true ] && ! oras manifest fetch "$image@$DIGEST" > /dev/null; then'
    assert lookup in decide and decide.count("GITHUB_REF_TYPE") == 1  # the `first` name only
    runner = Runner(tmp_path)  # a registry that does not give the digest
    built = digest(runner.built)
    decided, pushed, fetched = promote(runner, "v1.2.3", ["1.2.3"], built)
    assert decided.returncode == 0 and pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert fetched
    assert f"cp --from-oci-layout layout@{built} ghcr.io/owner/repo:1.2.3" in runner.oras_calls()
    assert runner.tags() == {"1.2.3": built}
    comment = " ".join(job_comment("publish").split())
    assert (
        "A tag's digest is main's, in the registry already, so a tag's run tags it there and"
        " copies no archive, unless the registry does not give the digest when asked (it is"
        " gone, or the request failed): then the archive is copied on a tag too, by the same"
        " digest, and on a tag the archive is main's image as image_build read it." in comment
    )


def test_with_publishing_off_a_tag_still_reads_main_s_image_and_so_stops():
    """The README's CAIRN_PUBLISH sentence: `publish` is off on main, so main writes no
    sha-<commit>, and nothing in image_build's tag steps looks at the variable, so a tag's run
    reads the registry and stops as a tag main has not published does."""
    assert "CAIRN_PUBLISH" in JOBS["publish"]["if"]
    job = JOBS["image_build"]
    assert "CAIRN_PUBLISH" not in str(job.get("if", ""))
    fetch_step = next(s for s in job["steps"] if s.get("name") == FETCH)
    assert fetch_step["if"] == "github.ref_type == 'tag'"


# --- a retried publish never moves `main` back ------------------------------------------------

NEWER = b'{"manifests":"a newer commit\'s build"}'


def test_publish_asks_for_the_head_of_main_with_the_run_s_token():
    push = step("publish", PUSH)
    assert push["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert 'gh api "repos/$GITHUB_REPOSITORY/branches/main" --jq .commit.sha' in push["run"]
    assert JOBS["publish"]["permissions"]["contents"] == "read"


def test_an_old_run_retried_after_main_moved_writes_its_sha_tag_and_leaves_main(tmp_path):
    """The old run pushed sha-ccccccc and failed; a newer push moved main; the old run's retry
    finishes its own commit's tag and leaves `main`, which the newer commit's run moves."""
    runner = Runner(tmp_path)
    built = digest(runner.built)
    runner.put("sha-ccccccc", runner.built)
    runner.put("main", NEWER)
    decided, pushed, _ = promote(runner, "main", ["main", "sha-ccccccc"], built, MAIN_HEAD="d" * 40)
    assert decided.returncode == 0 and pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert runner.tags() == {"main": digest(NEWER), "sha-ccccccc": built}
    (notice,) = [line for line in pushed.stdout.splitlines() if line.startswith("::notice::")]
    assert "d" * 40 in notice and COMMIT in notice and notice.count(". ") == 0
    assert (tmp_path / "github_output").read_text().endswith(f"digest={built}\n")


def test_the_head_run_moves_main(tmp_path):
    runner = Runner(tmp_path)
    built = digest(runner.built)
    runner.put("main", NEWER)
    _, pushed, _ = promote(runner, "main", ["main", "sha-ccccccc"], built, MAIN_HEAD=COMMIT)
    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert runner.tags() == {"main": built, "sha-ccccccc": built}


def test_a_head_that_cannot_be_read_stops_before_main_moves(tmp_path):
    runner = Runner(tmp_path)
    built = digest(runner.built)
    runner.put("main", NEWER)
    _, pushed, _ = promote(runner, "main", ["main", "sha-ccccccc"], built, HEAD_FAILS="1")
    assert pushed.returncode == 1
    (error,) = [line for line in pushed.stdout.splitlines() if line.startswith("::error::")]
    assert "the head of main could not be read" in error and "re-run" in error
    assert runner.tags()["main"] == digest(NEWER)


def test_a_tag_never_asks_for_the_head_of_main(tmp_path):
    runner = Runner(tmp_path)
    runner.put("sha-ccccccc", MAIN)
    _, pushed, _ = promote(runner, "v1.2.3", ["1.2.3"], digest(MAIN), HEAD_FAILS="1")
    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert "gh api" not in runner.calls.read_text()
