"""What infra/gcp/README.md, "Deploy from GitHub Actions", says about the CI workflow."""

import hashlib
import json
import re
import shlex
import subprocess
import sys

import pytest
import yaml

from support.ci.ci_workflow import (
    ACTION_FILES,
    ALWAYS_ON,
    CHEAP,
    DRAFT_DEFAULT,
    EVENTS,
    JOBS,
    MAIN_PUSH,
    NIGHTLY_JOBS,
    NIGHTLY_RAW,
    RAW_WORKFLOW,
    REPO,
    REPOSITORY_VARIABLES,
    SHA,
    TAG,
    TAG_GATE,
    THE_GATE,
    WORKFLOW,
    ancestors,
    evaluate,
    image_tags,
    job_comment,
    jobs_for,
    jobs_that_run,
    needs,
    not_a_pull_request,
    pull_request,
    reaches_the_token,
    release_check,
    release_tags,
    rerun,
    stops,
)
from support.ci_shell import first_attempt_gh, run_step

STAGE_ACTION = yaml.safe_load(
    (REPO / ".github" / "actions" / "stage-oci-image" / "action.yml").read_text(encoding="utf-8")
)
DEPLOY = JOBS["deploy"]
TF_VARIABLES = (REPO / "infra" / "gcp" / "variables.tf").read_text(encoding="utf-8")


# Gate jobs that ALWAYS run without checking image/web outputs of `changes`.
IMAGE_GATED = ("image_build", "image_scan", "image", "stack", "host")
WEB_GATED = ("browser",)


def default_of(variable: str) -> str:
    match = re.search(rf'variable "{variable}" \{{.*?default\s*=\s*"([^"]*)"', TF_VARIABLES, re.S)
    assert match, variable
    return match.group(1)


PRIVILEGED_JOBS = {"publish", "aliases", "attest", "attest_main", "release", "deploy"}


def test_every_action_is_pinned_to_a_full_commit_sha():
    local_prefixes = ("./.github/actions/",)
    for name, job in JOBS.items():
        for step in job["steps"]:
            action = step.get("uses")
            if not action:
                continue
            if action.startswith(local_prefixes):
                assert name not in PRIVILEGED_JOBS, f"privileged job {name} must not use {action}"
                continue
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), action
    for job in NIGHTLY_JOBS.values():
        for step in job["steps"]:
            action = step.get("uses")
            if action and not action.startswith(local_prefixes):
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), action
    for action_file in ACTION_FILES:
        data = yaml.safe_load(action_file.read_text(encoding="utf-8"))
        for step in data["runs"]["steps"]:
            action = step.get("uses")
            if action and not action.startswith(local_prefixes):
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), action


def test_every_job_declares_timeout_minutes():
    for source, jobs in (("ci-cd.yml", JOBS), ("nightly.yml", NIGHTLY_JOBS)):
        for name, job in jobs.items():
            minutes = job.get("timeout-minutes")
            assert isinstance(minutes, int) and 1 <= minutes <= 90, (source, name, minutes)


def test_only_the_deploy_and_the_two_attest_jobs_may_ask_for_an_id_token():
    assert "id-token" not in WORKFLOW.get("permissions", {})
    holders = {name for name, job in JOBS.items() if "id-token" in job.get("permissions", {})}
    assert holders == {"deploy", "attest", "attest_main"}
    assert DEPLOY["permissions"]["id-token"] == "write"


def test_deploy_waits_for_tests_infra_both_image_smokes_publish_and_verify():
    assert needs(DEPLOY) == {"lint", "test", "test_py312", "infra", "image", "publish", "verify"}
    platforms = [entry["platform"] for entry in JOBS["image"]["strategy"]["matrix"]["include"]]
    assert sorted(platforms) == ["linux/amd64", "linux/arm64"]
    assert needs(JOBS["verify"]) == {"publish"}


def test_publish_waits_for_the_infrastructure_check_and_the_image_stack_and_host_jobs():
    assert needs(JOBS["publish"]) == {
        "changes",
        "test",
        "test_py312",
        "infra",
        "image_build",
        "image_scan",
        "image",
        "stack",
        "host",
        "release_check",
        "package_check",
        "browser",
        *ALWAYS_ON,
    }


def test_gate_runner_uses_json_override_and_defaults_to_hosted():
    # `package` runs the build backend, which the release runner must never run (decision 150:
    # code a job runs could copy the runner's registration).
    for name in (
        "changes",
        "test",
        "infra",
        "package_check",
        "image_build",
        "image",
        "stack",
        "host",
        "release_check",
        "package",
    ):
        expression = JOBS[name]["runs-on"]
        match = re.fullmatch(
            r"\$\{\{ fromJSON\(vars\.CAIRN_GATE_RUNNER \|\| '([^']+)'\) \}\}", expression
        )
        assert match, (name, expression)
        assert json.loads(match[1]) == "ubuntu-latest"


def test_release_runner_uses_separate_json_override_and_defaults_to_hosted():
    for name in ("publish", "verify", "deploy", "attest", "attest_main", "release", "aliases"):
        expression = JOBS[name]["runs-on"]
        match = re.fullmatch(
            r"\$\{\{ fromJSON\(vars\.CAIRN_RELEASE_RUNNER \|\| '([^']+)'\) \}\}", expression
        )
        assert match, (name, expression)
        assert json.loads(match[1]) == "ubuntu-latest"
        configuration = yaml.safe_dump(JOBS[name])
        assert "CAIRN_GATE_RUNNER" not in configuration
        assert "cairn-ci-arm64" not in configuration
    for draft in (True, False):
        for name in jobs_that_run(pull_request(draft), {"changes": {"image": "true"}}):
            configuration = yaml.safe_dump(JOBS[name])
            assert "CAIRN_RELEASE_RUNNER" not in configuration, name
            assert "cairn-release-arm64" not in configuration, name


@pytest.mark.parametrize("name", ["image_build", "verify"])
def test_release_qemu_installer_uses_the_native_platform_before_image_work(name):
    steps = JOBS[name]["steps"]
    qemu = next(step for step in steps if step.get("uses", "").startswith("docker/setup-qemu"))
    assert qemu.get("env", {}).get("DOCKER_DEFAULT_PLATFORM") == (
        "${{ runner.arch == 'ARM64' && 'linux/arm64' || 'linux/amd64' }}"
    )
    image_work = next(
        i
        for i, step in enumerate(steps)
        if step.get("uses", "").startswith("docker/setup-buildx")
        or "scripts/smoke_container.py" in step.get("run", "")
    )
    assert steps.index(qemu) < image_work
    if name == "image_build":
        # One build of both platforms, written as an archive: nothing is pushed or loaded.
        build = next(s for s in steps if s.get("uses", "").startswith("docker/build-push"))
        assert set(build["with"]["platforms"].split(",")) == {"linux/amd64", "linux/arm64"}
        # Named, so buildkit gives each attestation statement the image as its subject.
        assert build["with"]["outputs"] == (
            "type=oci,dest=${{ runner.temp }}/image/cairn.oci.tar,name=cairn"
        )
        assert "push" not in build["with"] and "load" not in build["with"]


@pytest.mark.parametrize(("runner_arch", "emulated"), [("ARM64", True), ("X64", False)])
def test_verify_selects_amd64_with_emulation_and_appropriate_health_budget(runner_arch, emulated):
    job = JOBS["verify"]
    assert job["env"]["DOCKER_DEFAULT_PLATFORM"] == "linux/amd64"
    qemu = next(
        step for step in job["steps"] if step.get("uses", "").startswith("docker/setup-qemu")
    )
    context = {"runner": {"arch": runner_arch}}
    assert evaluate(qemu["if"], context) is emulated
    smoke = next(
        step for step in job["steps"] if "scripts/smoke_container.py" in step.get("run", "")
    )
    budget = smoke["env"]["CAIRN_SMOKE_HEALTH_TIMEOUT"]
    match = re.fullmatch(r"\$\{\{ (.+) && '300' \|\| '90' \}\}", budget)
    assert match, budget
    assert evaluate(match[1], context) is emulated


def test_verify_pulls_and_runs_the_same_published_amd64_digest(tmp_path):
    job = JOBS["verify"]
    smoke = next(
        step for step in job["steps"] if "scripts/smoke_container.py" in step.get("run", "")
    )
    # The unchanged registry-name assignment needs Bash 4 on Linux. Supply its result
    # here so macOS Bash 3 can exercise the actual pull/smoke body and environment.
    assignment, _, commands = smoke["run"].partition("\n")
    # The amd64 image's own digest: the index's, with a platform flag, would leave the
    # inspect and the smoke check to pick a platform from the index.
    assert assignment == 'image="ghcr.io/${GITHUB_REPOSITORY,,}@$AMD64"'
    # The shell's pull selection and the smoke script's docker children must agree.
    for command in ("docker", "python3"):
        tool = tmp_path / command
        tool.write_text(
            "#!/bin/sh\n"
            'test "$DOCKER_DEFAULT_PLATFORM" = linux/amd64 || exit 77\n'
            f'printf "{command} %s\\n" "$*" >> "$CALLS"\n'
            + ('case "$1" in image) echo amd64 ;; esac\n' if command == "docker" else "")
        )
        tool.chmod(0o755)
    calls = tmp_path / "calls"
    digest = "sha256:" + "a" * 64
    result = run_step(
        commands,
        env={
            "PATH": str(tmp_path),
            "CALLS": str(calls),
            "image": f"ghcr.io/owner/repo@{digest}",
            "DOCKER_DEFAULT_PLATFORM": job.get("env", {}).get("DOCKER_DEFAULT_PLATFORM", ""),
        },
    )
    assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines() == [
        f"docker pull --quiet --platform linux/amd64 ghcr.io/owner/repo@{digest}",
        f"docker image inspect --format {{{{.Architecture}}}} ghcr.io/owner/repo@{digest}",
        f"python3 scripts/smoke_container.py ghcr.io/owner/repo@{digest} --platform linux/amd64",
    ]
    assert job["env"]["AMD64"] == "${{ needs.publish.outputs.amd64 }}"


GUIDE_PULL = "Pull the image as the run guide does, with no login"


def run_the_guide_pull(tmp_path, pulled: str, **ref: str) -> tuple[int, str, list[str]]:
    """Job `verify`'s anonymous pull as bash runs it, with a docker that records its calls and
    whose pull prints `Digest: PULLED`, as docker pull does after a pull (Docker's reference
    for docker image pull). The tag pushed is v1.2.3 of the public Owner/Repo, unless `ref`
    says otherwise."""
    step = next(s for s in JOBS["verify"]["steps"] if s.get("name") == GUIDE_PULL)
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
        'if [ "$1" = pull ]; then\n'
        '  printf "1.2.3: Pulling from owner/repo\\nDigest: %s\\n" "$PULLED"\n'
        '  printf "Status: Downloaded newer image for %s\\n%s\\n" "$2" "$2"\n'
        '  exit "${PULL_EXIT:-0}"\n'
        "fi\n"
    )
    docker.chmod(0o755)
    calls = tmp_path / "calls"
    done = run_step(
        step["run"],
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "CALLS": str(calls),
            "PULLED": pulled,
            "GITHUB_REPOSITORY": "Owner/Repo",
            "GITHUB_REF_NAME": "v1.2.3",
            "GITHUB_REF_TYPE": "tag",
            "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
            "REPOSITORY_PRIVATE": "false",
            "DIGEST": "sha256:" + "a" * 64,
            "RUNNER_TEMP": str(tmp_path),
            **ref,
        },
    )
    made = calls.read_text().splitlines() if calls.exists() else []
    return done.returncode, done.stdout, made


PUSHED_INDEX = "sha256:" + "a" * 64


def test_on_a_tag_verify_pulls_the_version_as_the_guide_says_with_no_login(tmp_path):
    steps = JOBS["verify"]["steps"]
    pull = next(s for s in steps if s.get("name") == GUIDE_PULL)
    # No condition of its own: it runs on main too, so the merge that comes before a release
    # has run it; what it pulls and whether it pulls is decided in the script, from `env:`.
    login = next(s for s in steps if s.get("uses", "").startswith("docker/login-action"))
    assert steps.index(pull) < steps.index(login)
    assert set(pull) == {"name", "env", "run"}
    assert pull["env"] == {"REPOSITORY_PRIVATE": "${{ github.event.repository.private }}"}
    code, _, calls = run_the_guide_pull(tmp_path, PUSHED_INDEX)
    # Logged out first; then the run guide's own command, by the version tag.
    assert code == 0
    assert calls == ["logout ghcr.io", "pull ghcr.io/owner/repo:1.2.3"]


def test_on_main_verify_pulls_the_commits_own_tag_with_no_login(tmp_path):
    """A push to main is published as `main` and `sha-<7>`: the commit's own tag is the one
    no later push moves, so that is what is pulled, and a tag's run, whose image is main's,
    pulls a digest main's run has pulled already."""
    code, _, calls = run_the_guide_pull(
        tmp_path, PUSHED_INDEX, GITHUB_REF_TYPE="branch", GITHUB_REF_NAME="main"
    )
    assert code == 0
    assert calls == ["logout ghcr.io", "pull ghcr.io/owner/repo:sha-0123456"]
    assert "type=sha,enable=" in RAW_WORKFLOW  # the tag publish gives a push to main


@pytest.mark.parametrize("private", ["true", "", "False"])
def test_in_a_private_repository_nothing_is_pulled_without_a_login(tmp_path, private):
    """Nobody pulls a private repository's image without a login, so there is nothing to
    check there, and its runs are not failed for it: only exactly `false` pulls."""
    code, out, calls = run_the_guide_pull(tmp_path, "unused", REPOSITORY_PRIVATE=private)
    assert code == 0 and calls == []
    assert out == "This repository is not public: nobody pulls its image without a login.\n"


@pytest.mark.parametrize(
    "pulled", ["sha256:" + "b" * 64, PUSHED_INDEX + "0", "", "sha256:" + "A" * 64]
)
def test_the_pull_with_no_login_fails_unless_this_pull_printed_the_published_digest(
    tmp_path, pulled
):
    """The digest this pull returned is compared, not what the engine knows the image by: an
    engine that kept another index under the same name could list the published digest beside
    the one it pulled."""
    code, out, _ = run_the_guide_pull(tmp_path, pulled)
    assert code == 1
    assert out.splitlines()[-1] == (
        f"::error::ghcr.io/owner/repo:1.2.3, pulled with no login, is {pulled or 'no digest'}"
        f" by the digest the pull printed, not {PUSHED_INDEX}, the digest publish pushed, so"
        " the tag was moved after publish; release nothing from this run, and re-run this job"
        " once the package's page shows the tag at the digest publish pushed"
    )


@pytest.mark.parametrize("status", ["1", "125"])
def test_a_pull_that_fails_after_printing_the_digest_fails_the_step_in_one_sentence(
    tmp_path, status
):
    """docker pull can print `Digest:` and then fail (Moby's pull writes the digest before it
    stores the reference). Run as GitHub runs the step, bash -e without pipefail, a pipe into
    sed would hide that status: the pull's status is checked on its own."""
    code, out, _ = run_the_guide_pull(tmp_path, PUSHED_INDEX, PULL_EXIT=status)
    assert code == 1
    (error,) = [line for line in out.splitlines() if line.startswith("::error::")]
    assert error == (
        f"::error::docker pull ghcr.io/owner/repo:1.2.3 with no login failed (exit {status}),"
        " so nobody can pull it as the run guide says now; check that the package is public on"
        " GitHub, then re-run this job"
    )


def test_the_pull_with_no_login_is_the_one_the_run_guide_gives():
    guide = (REPO / "src" / "cairn" / "guides" / "run.md").read_text(encoding="utf-8")
    run = next(s for s in JOBS["verify"]["steps"] if s.get("name") == GUIDE_PULL)["run"]
    assert re.search(r"^docker pull ghcr\.io/macayaven/cairn:\d+\.\d+\.\d+ &&$", guide, re.M)
    assert "the `docker pull` above prints `Digest: sha256:` and a digest" in guide
    assert "RepoDigests" not in guide
    lines = [line.strip() for line in run.splitlines()]
    assert (
        'if docker pull "$image" > "$RUNNER_TEMP/pull.log" 2>&1; then status=0; else status=$?; fi'
        in lines
    )
    assert 'pulled="$(sed -n \'s/^Digest: //p\' "$RUNNER_TEMP/pull.log")"' in lines
    assert not [line for line in lines if "docker pull" in line and "|" in line]


def test_release_jobs_do_not_run_the_application_environment_or_rehearsals_on_the_host():
    for name in ("publish", "verify", "deploy", "attest", "attest_main", "release", "aliases"):
        for step in JOBS[name]["steps"]:
            assert not step.get("uses", "").startswith("astral-sh/setup-uv"), name
            command = step.get("run", "")
            assert not re.search(r"\b(?:uv|pytest)\b|rehearse_(?:stack|host)\.py", command), name


@pytest.mark.parametrize(
    ("runner_arch", "image_arch", "emulated"),
    [
        ("X64", "amd64", False),
        ("X64", "arm64", True),
        ("ARM64", "amd64", True),
        ("ARM64", "arm64", False),
    ],
)
def test_image_emulation_and_health_budget_follow_the_runner(runner_arch, image_arch, emulated):
    steps = JOBS["image"]["steps"]
    qemu = next(step for step in steps if step.get("uses", "").startswith("docker/setup-qemu"))
    smoke = next(step for step in steps if "scripts/smoke_container.py" in step.get("run", ""))
    context = {"runner": {"arch": runner_arch}, "matrix": {"arch": image_arch}}
    assert evaluate(qemu["if"], context) is emulated
    budget = smoke["env"]["CAIRN_SMOKE_HEALTH_TIMEOUT"]
    match = re.fullmatch(r"\$\{\{ (.+) && '300' \|\| '90' \}\}", budget)
    assert match, budget
    assert evaluate(match[1], context) is emulated
    # The image check builds nothing: it runs image_build's build, this leg's platform of it.
    assert not any(s.get("uses", "").startswith("docker/build-push") for s in steps)
    build = next(
        s for s in JOBS["image_build"]["steps"] if s.get("uses", "").startswith("docker/build-push")
    )
    assert build["with"]["cache-from"] == "type=gha,scope=image"
    assert build["with"]["cache-to"] == (
        "${{ github.event_name != 'pull_request' && 'type=gha,mode=max,scope=image' || '' }}"
    )


@pytest.mark.parametrize("name", ["stack", "host"])
def test_rehearsals_select_amd64_and_only_emulate_on_arm(name):
    job = JOBS[name]
    assert job["env"]["DOCKER_DEFAULT_PLATFORM"] == "linux/amd64"
    qemu = next(
        step for step in job["steps"] if step.get("uses", "").startswith("docker/setup-qemu")
    )
    assert evaluate(qemu["if"], {"runner": {"arch": "ARM64"}})
    assert not evaluate(qemu["if"], {"runner": {"arch": "X64"}})
    # Installing binfmt must run natively, before AMD64 emulation is available.
    assert qemu.get("env", {}).get("DOCKER_DEFAULT_PLATFORM") == (
        "${{ runner.arch == 'ARM64' && 'linux/arm64' || 'linux/amd64' }}"
    )
    # The rehearsals take the build's amd64 image via stage-oci-image and build none.
    stage_index = next(
        i
        for i, step in enumerate(job["steps"])
        if step.get("uses") == "./.github/actions/stage-oci-image"
    )
    assert job["steps"].index(qemu) < stage_index
    stage_inputs = job["steps"][stage_index]["with"]
    assert stage_inputs["pull-platform"] == "linux/amd64"
    assert stage_inputs["pull-arch"] == "amd64"
    assert stage_inputs["stop-registry"] == "true"
    assert job["env"]["PLATFORM"] == "linux/amd64" and job["env"]["ARCH"] == "amd64"
    assert not any("docker build" in step.get("run", "") for step in job["steps"])
    if name == "host":
        # Emulated on an ARM64 runner the job took 31 minutes; 40 left no room.
        assert job["timeout-minutes"] == 60
        rehearsal = job["steps"][-1]["run"]
        assert '"DOCKER_DEFAULT_PLATFORM=$DOCKER_DEFAULT_PLATFORM"' in rehearsal


def test_the_units_the_host_rehearsal_starts_get_the_same_platform():
    """The units run docker themselves and do not see the job's environment; systemd's own
    environment reaches every unit started after it is set."""
    steps = JOBS["host"]["steps"]
    given = next(
        i for i, step in enumerate(steps) if "systemctl set-environment" in step.get("run", "")
    )
    assert shlex.split(steps[given]["run"]) == [
        "sudo",
        "systemctl",
        "set-environment",
        "DOCKER_DEFAULT_PLATFORM=$DOCKER_DEFAULT_PLATFORM",
    ]
    rehearsal = next(i for i, step in enumerate(steps) if "rehearse_host.py" in step.get("run", ""))
    qemu = next(
        i for i, step in enumerate(steps) if step.get("uses", "").startswith("docker/setup-qemu")
    )
    assert qemu < given < rehearsal


@pytest.mark.parametrize("name", ["stack", "host"])
@pytest.mark.parametrize(("architecture", "success"), [("amd64", True), ("arm64", False)])
def test_rehearsals_refuse_a_local_image_for_the_wrong_platform(
    tmp_path, name, architecture, success
):
    steps = JOBS[name]["steps"]
    stage = next(s for s in steps if s.get("uses") == "./.github/actions/stage-oci-image")
    preflight = next(
        s
        for s in STAGE_ACTION["runs"]["steps"]
        if s.get("name") == "Pull this platform of the build"
    )
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$1" in pull|tag|rm) exit 0 ;; esac\n'
        'test "$*" = "image inspect --format {{.Architecture}} cairn:ci" || exit 77\n'
        'printf "%s\\n" "$IMAGE_ARCH"\n'
    )
    docker.chmod(0o755)
    result = run_step(
        preflight["run"],
        env={
            "PATH": str(tmp_path),
            "IMAGE_ARCH": architecture,
            "IMAGE": "x@sha256:1",
            "LOCAL": stage["with"]["local-tag"],
            "PLATFORM": stage["with"]["pull-platform"],
            "ARCH": stage["with"]["pull-arch"],
            "STOP_REGISTRY": stage["with"]["stop-registry"],
        },
    )
    assert (result.returncode == 0) is success


def gate_jobs_missing_from_publish(jobs: dict) -> set[str]:
    """Every job that runs on a ready PR (when image/quick/web are all needed) must be an
    ancestor of `publish`."""
    gate = jobs_that_run(
        pull_request(draft=False),
        {"changes": {"image": "true", "quick": "true", "web": "true"}},
        jobs,
    )
    return gate - ancestors("publish", jobs)


def test_every_job_of_the_gate_is_an_ancestor_of_publish():
    assert gate_jobs_missing_from_publish(JOBS) == set()
    # A job added to the gate and left out of publish is found.
    added = {**JOBS, "docs_check": {"runs-on": "ubuntu-latest", "steps": []}}
    assert gate_jobs_missing_from_publish(added) == {"docs_check"}
    # So are `infra` and `package_check` dropped from publish's needs.
    dropped = {**JOBS, "publish": {**JOBS["publish"], "needs": ["image", "stack", "host"]}}
    missing = {"infra", "package_check", "browser", "image_scan", *ALWAYS_ON}
    assert gate_jobs_missing_from_publish(dropped) == missing


def test_deploy_runs_on_a_push_to_main_in_the_production_environment():
    condition = " ".join(DEPLOY["if"].split())
    assert "github.event_name == 'push'" in condition
    assert "github.ref == 'refs/heads/main'" in condition
    # Two repository variables turn the job on; without them it is skipped, not failed.
    assert set(re.findall(r"vars\.(\w+) != ''", condition)) == {"GCP_INSTANCE", "GCP_ZONE"}
    assert DEPLOY["environment"] == "production" == default_of("github_environment")


def test_verify_runs_the_published_image_without_a_token_or_an_environment():
    verify = JOBS["verify"]
    assert "environment" not in verify
    assert set(verify["permissions"]) == {"contents", "packages"}


def test_two_deploys_never_run_at_once_and_a_running_one_is_not_cancelled():
    assert DEPLOY["concurrency"]["cancel-in-progress"] is False
    assert DEPLOY["concurrency"]["group"]


def script_step(command: str) -> int:
    """The index of the deploy step that runs `deploy_production.sh COMMAND`."""
    found = [
        i
        for i, step in enumerate(DEPLOY["steps"])
        if re.search(rf"scripts/deploy_production\.sh {command}\b", step.get("run", ""))
    ]
    assert len(found) == 1, (command, found)
    return found[0]


def test_the_deploy_steps_come_in_the_order_the_guide_gives():
    order = ["copy", "check-head", "seq", "promote", "wait", "check-url"]
    assert [script_step(c) for c in order] == sorted(script_step(c) for c in order)


def test_wait_counts_only_reports_after_the_number_read_before_the_move():
    seq = DEPLOY["steps"][script_step("seq")]
    wait = DEPLOY["steps"][script_step("wait")]
    assert wait["env"]["CAIRN_AFTER_SEQ"] == "${{ steps." + seq["id"] + ".outputs.seq }}"
    assert '>> "$GITHUB_OUTPUT"' in seq["run"]


def test_deploy_moves_the_tag_the_vm_follows_and_waits_fifteen_minutes():
    promote = DEPLOY["steps"][script_step("promote")]
    assert promote["env"]["CAIRN_DEPLOY_TAG"] == default_of("deploy_tag")
    assert DEPLOY["steps"][script_step("wait")]["env"]["CAIRN_DEPLOY_TIMEOUT"] == "900"


def test_the_copy_is_tagged_with_the_full_commit_sha():
    copy = DEPLOY["steps"][script_step("copy")]
    assert copy["env"]["CAIRN_COPY_TAG"] == "sha-${{ github.sha }}"


def test_the_public_check_runs_only_when_the_variable_is_set():
    check = DEPLOY["steps"][script_step("check-url")]
    assert check["if"] == "vars.CAIRN_PUBLIC_URL != ''"


def test_deploy_runs_no_script_but_the_deploy_script():
    runs = [step["run"] for step in DEPLOY["steps"] if "run" in step]
    for run in runs:
        assert set(re.findall(r"scripts/\S+", run)) <= {"scripts/deploy_production.sh"}, run
        assert not re.search(r"\b(python3?|uv|pytest|docker)\b", run), run


# --- which jobs run, and when ----------------------------------------------------------------
# A draft pull request runs the tests and the infrastructure checks. A pull request that is
# ready for review also builds the image (job `image` on both platforms, `stack`, `host`). A
# push to main, a v* tag and a manual run run what they ran before. On top of that, a change
# whose every path is on scripts/ci_changes.py's list of paths no image job reads skips the
# image jobs and so everything after them; job `changes` runs the script and says so.

TRIGGERS = WORKFLOW.get("on", WORKFLOW.get(True))  # PyYAML reads the key `on` as True
BUILD_JOBS = ("image", "stack", "host")
NOT_A_DRAFT = "github.event_name != 'pull_request' || !github.event.pull_request.draft"
# Anything but an explicit 'false' from job `changes` runs the image jobs: an output that was
# never written is empty, and an empty output must not skip a check.
SHIPS = "needs.changes.outputs.image != 'false'"
REHEARSAL_CONDITION = f"({NOT_A_DRAFT}) && {SHIPS} && github.ref_type != 'tag'"


def test_the_pull_request_types_include_ready_for_review_and_the_three_it_had():
    assert TRIGGERS["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    ]


def test_the_other_triggers_the_permissions_and_the_concurrency_are_as_they_were():
    assert TRIGGERS["push"] == {"branches": ["main"], "tags": ["v*"]}
    assert TRIGGERS["workflow_dispatch"] is None
    assert set(TRIGGERS) == {"push", "pull_request", "workflow_dispatch"}
    assert WORKFLOW["permissions"] == {"contents": "read"}
    assert WORKFLOW["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }


def test_the_image_jobs_share_their_conditions_and_the_cheap_jobs_have_none():
    assert JOBS["image_build"]["if"] == SHIPS
    assert JOBS["image_scan"]["if"] == "needs.image_build.result == 'success'"
    assert [" ".join(JOBS[name]["if"].split()) for name in BUILD_JOBS] == [REHEARSAL_CONDITION] * 3
    assert JOBS["browser"]["if"] == "needs.changes.outputs.web != 'false'"
    for name in ("lint", "test", "infra", "changes", "package_check"):
        assert "if" not in JOBS[name], name


def test_the_changes_job_runs_the_script_on_the_whole_history_reading_runs_and_nothing_more():
    changes = JOBS["changes"]
    assert needs(changes) == set()
    # contents: read for the checkout, actions: read to list this workflow's runs and their
    # jobs on main. A job's own list sets every permission it does not name to none.
    assert changes["permissions"] == {"contents": "read", "actions": "read"}
    checkout, decide = changes["steps"]
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 0}
    assert decide["run"] == "python3 scripts/ci_changes.py"
    assert changes["outputs"] == {
        "image": "${{ steps." + decide["id"] + ".outputs.image }}",
        "quick": "${{ steps." + decide["id"] + ".outputs.quick }}",
        "web": "${{ steps." + decide["id"] + ".outputs.web }}",
    }
    # The job's token for the API; the rest the script reads from GitHub's own variables.
    assert decide["env"] == {"GITHUB_TOKEN": "${{ github.token }}"}
    assert set(decide) == {"name", "id", "env", "run"}


def test_the_jobs_that_come_after_keep_their_needs_and_conditions_exactly():
    # `image_build` needs `changes` so docs/tests-only PRs skip the multi-arch build too.
    assert needs(JOBS["image_build"]) == {"changes"}
    assert needs(JOBS["image_scan"]) == {"image_build"}
    assert needs(JOBS["image"]) == {"test", "changes", "image_build"}
    # The rehearsals rehearse that build too, its amd64 image, and build none of their own.
    assert needs(JOBS["host"]) == {"test", "changes", "image_build"}
    assert needs(JOBS["stack"]) == {"changes", "image_build"}
    assert needs(JOBS["publish"]) == {
        "changes",
        "lint",
        "test",
        "test_py312",
        "infra",
        "image_build",
        "image_scan",
        "image",
        "stack",
        "host",
        "browser",
        "release_check",
        "package_check",
        *ALWAYS_ON,
    }
    publish_if = " ".join(JOBS["publish"]["if"].split())
    assert (
        "!failure() && !cancelled() && github.event_name == 'push'"
        " && vars.CAIRN_PUBLISH != 'false'" in publish_if
    )
    assert needs(JOBS["verify"]) == {"publish"}
    assert " ".join(JOBS["verify"]["if"].split()) == (
        "!failure() && !cancelled() && "
        "github.event_name == 'push' && "
        "(github.ref == 'refs/heads/main' || github.ref_type == 'tag') && "
        "needs.publish.result == 'success'"
    )
    assert needs(DEPLOY) == {"lint", "test", "test_py312", "infra", "image", "publish", "verify"}
    assert " ".join(DEPLOY["if"].split()) == (
        "!failure() && !cancelled() && "
        "github.event_name == 'push' && github.ref == 'refs/heads/main' "
        "&& vars.GCP_INSTANCE != '' && vars.GCP_ZONE != '' && "
        "needs.lint.result == 'success' && needs.test.result == 'success' && "
        "needs.test_py312.result == 'success' && needs.infra.result == 'success' && "
        "needs.image.result == 'success' && needs.publish.result == 'success' && "
        "needs.verify.result == 'success'"
    )


# A small evaluator for the expressions the workflow's `if` lines use: context paths, string
# literals, `!`, `==`, `!=`, `&&`, `||`, parentheses and the status functions `success()`,
# `always()`, `failure()` and `cancelled()`. GitHub treats null, false, 0 and '' as false; a
# path that is not in the context is null.


# Job `changes` runs on every event, like the two cheap jobs, and takes a few seconds.
# So does `package_check`, the rehearsal of the build a tag runs.
# On main, `attest_main` signs the published image's digest for the commit after `verify`.
EVERYTHING_NO_WEB = (THE_GATE - {"browser"}) | {
    "test_py312",
    "release_check",
    "publish",
    "verify",
    "attest_main",
    "deploy",
}
EVERYTHING = EVERYTHING_NO_WEB | {"browser"}
# A v* tag runs the non-rehearsal gate + py312 + release path; it trusts main's attested rehearsals.
A_RELEASE = TAG_GATE | {
    "release_check",
    "publish",
    "verify",
    "package",
    "attest",
    "release",
    "aliases",
}


def test_which_jobs_run_for_each_event_when_changes_says_nothing():
    # No output from `changes` (it was never written) runs what isn't gated off.
    table = {
        "a push to a draft pull request": (pull_request(draft=True), DRAFT_DEFAULT),
        "a pull request marked ready for review": (pull_request(draft=False), THE_GATE),
        "a push to a pull request that is ready": (pull_request(draft=False), THE_GATE),
        "a push to main": (not_a_pull_request("push", "refs/heads/main"), EVERYTHING),
        "a manual run": (
            not_a_pull_request("workflow_dispatch", "refs/heads/work"),
            THE_GATE | {"test_py312"},
        ),
        "a v* tag": (not_a_pull_request("push", "refs/tags/v1.0.0"), A_RELEASE),
    }
    for event, (context, expected) in table.items():
        assert jobs_that_run(context) == expected, event


def test_publishing_has_an_off_switch_and_is_on_when_nothing_is_set():
    """A repository whose registry is not ready sets the variable CAIRN_PUBLISH to `false`:
    a push to main still runs the whole gate, and publishes, verifies and deploys nothing.
    With the variable unset, or set to anything else, a push publishes as before."""
    assert "github.event_name == 'push' && vars.CAIRN_PUBLISH != 'false'" in JOBS["publish"]["if"]
    push = not_a_pull_request("push", "refs/heads/main")
    assert jobs_that_run(push) == EVERYTHING
    for said, expected in (
        ("false", THE_GATE | {"test_py312"}),
        ("true", EVERYTHING),
        ("", EVERYTHING),
    ):
        variables = {**REPOSITORY_VARIABLES, "CAIRN_PUBLISH": said}
        assert jobs_that_run({**push, "vars": variables}) == expected, said
    tag = {**not_a_pull_request("push", "refs/tags/v1.0.0"), "vars": {"CAIRN_PUBLISH": "false"}}
    assert jobs_that_run(tag) == TAG_GATE


A_CHANGE_THAT_SHIPS = ["docs/spec-v1.md", "src/cairn/store.py"]
NOTHING_SHIPS = ["docs/spec-v1.md", "tests/unit/store/test_store_unit.py"]


def test_which_jobs_run_for_each_event_with_a_change_that_ships_and_one_that_does_not():
    pr_ships_no_web = THE_GATE - {"browser"}
    draft_ships_no_web = DRAFT_DEFAULT - {"browser"}
    table = {
        # event: (a change that ships store.py only, a change where nothing ships)
        "a push to a draft pull request": (draft_ships_no_web, CHEAP),
        "a pull request marked ready for review": (pr_ships_no_web, CHEAP),
        "a push to a pull request that is ready": (pr_ships_no_web, CHEAP),
        # On main, "the change" is from the last published head to the commit pushed.
        "a push to main": (EVERYTHING_NO_WEB, CHEAP | {"test_py312"}),
        # The changed paths of these two are not known, so they run everything for their event.
        "a manual run": (THE_GATE | {"test_py312"}, THE_GATE | {"test_py312"}),
        "a v* tag": (A_RELEASE, A_RELEASE),
    }
    assert set(table) == set(EVENTS)
    for event, (ships, nothing) in table.items():
        assert jobs_for(event, A_CHANGE_THAT_SHIPS) == ships, (event, "ships")
        assert jobs_for(event, NOTHING_SHIPS) == nothing, (event, "nothing ships")


def test_on_main_what_runs_depends_on_the_last_published_head_not_on_the_push():
    push = "a push to main"
    # A docs-only push after a run that published: nothing ships, nothing is deployed.
    assert jobs_for(push, NOTHING_SHIPS) == CHEAP | {"test_py312"}
    # The same push when the newest publishing run is older than a source change: ships without web.
    assert jobs_for(push, [*NOTHING_SHIPS, "src/cairn/server.py"]) == EVERYTHING_NO_WEB
    # Web changes trigger `browser` on main too.
    assert jobs_for(push, ["src/cairn/web/app.py"]) == EVERYTHING
    # No run that published, or a lookup that failed: `image` runs; `web` follows paths or unknown.
    assert jobs_for(push, NOTHING_SHIPS, base=None) == EVERYTHING_NO_WEB
    assert jobs_for(push, A_CHANGE_THAT_SHIPS, base=None) == EVERYTHING_NO_WEB
    # The published head is the head itself (this run is a re-run): empty diff runs everything.
    assert jobs_for(push, [], base=SHA) == EVERYTHING
    # Pull requests do not look up a base at all.
    assert jobs_for("a push to a pull request that is ready", NOTHING_SHIPS, base=None) == CHEAP


def test_an_output_that_is_not_exactly_false_runs_the_gate():
    for value in ("true", "", "False ", "skip"):
        ran = jobs_that_run(EVENTS["a push to main"], {"changes": {"image": value}})
        assert ran == EVERYTHING, value
    ran = jobs_that_run(EVENTS["a push to main"], {"changes": {"image": "false"}})
    assert ran == CHEAP | {"browser", "test_py312"}


def test_the_evaluator_skips_after_a_skipped_job_unless_the_condition_says_otherwise():
    jobs = {
        "first": {"if": "github.event_name == 'never'"},
        "plain": {"needs": "first"},
        "conditioned": {"needs": "first", "if": "github.event_name == 'push'"},
        "regardless": {"needs": "first", "if": "always()"},
        "after_regardless": {"needs": ["regardless"]},
        "reads": {"needs": ["regardless"], "if": "needs.regardless.outputs.x == 'y'"},
        "not_listed": {"needs": ["regardless"], "if": "always() && needs.first.result == ''"},
    }
    context = {"github": {"event_name": "push"}}
    ran = jobs_that_run(context, {"regardless": {"x": "y"}}, jobs)
    # "A failure or skip applies to all jobs in the dependency chain from the point of failure
    # or skip onwards": `after_regardless` and `reads` follow a job that ran, but `first`,
    # skipped, is still in their chain, so their implicit success() is false.
    assert ran == {"regardless", "not_listed"}
    assert evaluate("always() && !cancelled()", {}, {"always()": True, "cancelled()": False})
    assert not evaluate("success()", {}, {"success()": False})


def test_a_job_with_one_needed_job_that_succeeded_and_others_skipped_is_skipped():
    # GitHub, workflow syntax, jobs.<job_id>.needs: "If a job fails or is skipped, all jobs
    # that need it are skipped" unless the job's own condition says otherwise (always()).
    # https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idneeds
    jobs = {
        "ran": {},
        "skipped": {"if": "github.event_name == 'never'"},
        "waits": {"needs": ["ran", "skipped"], "if": "github.event_name == 'push'"},
    }
    context = {"github": {"event_name": "push"}}
    assert jobs_that_run(context, jobs=jobs) == {"ran"}
    # The same on main when nothing ships: `infra` passed, the image jobs were skipped, and
    # publish, which now needs both, is skipped. It does not fail.
    ran = jobs_that_run(EVENTS["a push to main"], {"changes": {"image": "false"}})
    assert "infra" in ran and not ran & {"image", "stack", "host", "publish"}


def test_the_evaluator_reads_the_conditions_as_github_does():
    draft = pull_request(draft=True)
    assert evaluate("github.event_name == 'pull_request' && github.event.pull_request.draft", draft)
    assert not evaluate(NOT_A_DRAFT, draft)
    assert evaluate(NOT_A_DRAFT, pull_request(draft=False))
    assert evaluate(NOT_A_DRAFT, not_a_pull_request("push", "refs/heads/main"))
    assert evaluate("!('a' == 'b')", draft)
    assert not evaluate("vars.GCP_ZONE != ''", {"vars": {}})


def test_the_comment_and_the_readme_say_when_the_image_is_built():
    header = (REPO / ".github" / "workflows" / "ci-cd.yml").read_text().split("\nname:")[0]
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for text in (header, readme):
        assert "draft" in text and "ready for review" in text
        assert "the gate a merge waits for" in " ".join(text.replace("# ", "").split())
        # The rule in plain words, and where the list lives.
        assert "scripts/ci_changes.py" in text
        assert "NO_IMAGE_WORK" in text
        # On main, the change is measured from the last run that published an image.
        flat = " ".join(text.replace("# ", "").split())
        assert "the newest run on main that published an image" in flat.replace("`", "")
    assert "runs on every pull request" not in readme


def test_the_comment_and_the_guides_say_what_publish_waits_for():
    header = (REPO / ".github" / "workflows" / "ci-cd.yml").read_text().split("\nname:")[0]
    flat = " ".join(header.replace("# ", "").split())
    assert (
        "once every job of the gate has passed: the tests, the infrastructure checks, the image"
        " check, the stack rehearsal and the host rehearsal. A failed infrastructure check"
        " stops the publication too." in flat
    )
    readme = " ".join((REPO / "README.md").read_text(encoding="utf-8").split())
    assert "**Publish:** once items 1 to 4 have all passed, pushes to `main`" in readme
    # "items 1 to 4" counts every job publish waits for: item 4 holds both rehearsals.
    item_4 = readme.split("4. **")[1].split("5. **")[0]
    assert "rehearse_stack.py" in item_4 and "rehearse_host.py" in item_4
    gcp = " ".join((REPO / "infra" / "gcp" / "README.md").read_text(encoding="utf-8").split())
    assert (
        "`publish` itself waits for the tests, the infrastructure checks, the image check and"
        " the stack and host rehearsals, so nothing reaches GHCR past a failed one." in gcp
    )


def guide_part(heading: str) -> str:
    """infra/gcp/README.md from HEADING to the next heading of its level or higher."""
    text = (REPO / "infra" / "gcp" / "README.md").read_text(encoding="utf-8")
    level = len(heading.split(" ")[0])
    lines = text[text.index(f"\n{heading}\n") + 1 :].splitlines()
    part, fenced = lines[:1], False
    for line in lines[1:]:
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and (found := re.match(r"(#+) ", line)) and len(found.group(1)) <= level:
            break
        part.append(line)
    return " ".join(" ".join(part).split())


def test_the_guide_says_what_the_next_push_to_main_deploys():
    first = guide_part("### First deploy (once, in order)")
    rollback = guide_part("## Rollback")
    # Gone: "the next push" deploys, whatever it changes.
    assert "Push to `main`, or re-run the latest `main` run" not in first
    assert "the next push to `main` deploys forward again" not in rollback
    assert "the next one deploys forward again" not in rollback
    # First deploy, step 7: a push that touches the image, or a re-run of a run that published.
    assert (
        "7. **Deploy.** Push to `main` a change that touches something the image is built from,"
        " or re-run the newest `main` run if its `publish` job ran, and approve the `deploy`"
        " job in the Actions tab where the environment has required reviewers;"
        " without them it starts by itself." in first
    )
    assert (
        "A push that changes only paths on `NO_IMAGE_WORK` in `scripts/ci_changes.py` builds"
        " and deploys nothing once the newest run on `main` that published an image has"
        " ended green." in first
    )
    # Rollback, after the tag is moved back. A run that published and then failed makes the
    # base unknown (scripts/ci_changes.py), so then any push runs the gate.
    assert (
        "After a rollback, the next push to `main` that changes something the image is built"
        " from deploys the head of `main` again. A push that changes only paths on"
        " `NO_IMAGE_WORK` in `scripts/ci_changes.py` deploys nothing, and the VM keeps the"
        " image you rolled back to, when the run that published the image you rolled back"
        " from ended green; when that run failed after it published (its deploy refused,"
        " timed out or rolled back by the VM), the next push of any kind runs the whole gate"
        " and deploys the head of `main`." in rollback
    )
    assert (
        "Hold pushes to `main` until the newer image is fixed: the next push that changes"
        " something the image is built from deploys forward again, and so does any push when"
        " the run that published the newer image did not end green;" in rollback
    )


def test_host_rehearsal_runs_through_metadata_fixture():
    step = next(s for s in JOBS["host"]["steps"] if s.get("name") == "Rehearse the VM's host setup")
    assert "scripts/ci_metadata_fixture.py" in step["run"]
    assert "scripts/rehearse_host.py cairn:ci --on-this-throwaway-machine" in step["run"]
    assert '"DOCKER_DEFAULT_PLATFORM=$DOCKER_DEFAULT_PLATFORM"' in step["run"]


def test_host_rehearsal_units_receive_the_amd64_platform():
    steps = JOBS["host"]["steps"]
    step = next(s for s in steps if "systemctl set-environment" in s.get("run", ""))
    assert (
        step["run"]
        == 'sudo systemctl set-environment "DOCKER_DEFAULT_PLATFORM=$DOCKER_DEFAULT_PLATFORM"'
    )
    assert steps.index(step) < next(
        i for i, s in enumerate(steps) if s.get("uses") == "./.github/actions/stage-oci-image"
    )
    assert JOBS["host"]["env"]["DOCKER_DEFAULT_PLATFORM"] == "linux/amd64"


def test_opentofu_is_set_up_without_its_wrapper():
    """The wrapper is a Node script; a runner without Node on its path ran `tofu` and got
    exit 127. No step reads the wrapper's outputs."""
    setups = [
        step
        for job in JOBS.values()
        for step in job["steps"]
        if step.get("uses", "").startswith("opentofu/setup-opentofu@")
    ]
    assert len(setups) == 3  # infra, host and quick
    for step in setups:
        assert step["with"]["tofu_wrapper"] is False
    assert "steps.tofu" not in (REPO / ".github" / "workflows" / "ci-cd.yml").read_text()


def test_the_infrastructure_job_checks_every_compose_file_the_guides_use():
    """compose.local.yaml is the README's first path; a file Compose cannot read fails here."""
    step = next(s for s in JOBS["infra"]["steps"] if s.get("name") == "Compose files")
    lines = [line.strip() for line in step["run"].splitlines()]
    assert "docker compose -f compose.local.yaml config --quiet" in lines
    assert "docker compose -f compose.yaml config --quiet" in lines
    assert "docker compose -f compose.yaml -f compose.backup.yaml config --quiet" in lines


def test_the_build_backend_is_one_version():
    """The package is built by a version that was run, not by whichever is newest that day."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    (requires,) = re.findall(r'^requires = \["([^"]+)"\]$', text, re.M)
    assert re.fullmatch(r"hatchling==\d+\.\d+\.\d+", requires), requires


# --- a release: a v* tag ---------------------------------------------------------------------
# A tag runs the whole gate as main does; `release_check` refuses a tag that is not the version
# before anything is published; `publish` pushes the version's tags; `verify` smoke-tests the
# published image; `release` makes the GitHub release with the changelog's section, the wheel
# and the source archive.


def test_every_action_names_its_version_in_a_comment():
    sources = [RAW_WORKFLOW, NIGHTLY_RAW, *(p.read_text(encoding="utf-8") for p in ACTION_FILES)]
    lines = [
        line.strip()
        for text in sources
        for line in text.splitlines()
        if line.strip().startswith(("uses: ", "- uses: "))
    ]
    assert len(lines) > 10
    for line in lines:
        if "./.github/actions/" in line:
            continue
        assert re.fullmatch(r"(- )?uses: [\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*", line), line


CHECK_COMMAND = (
    'python3 scripts/release_check.py "$GITHUB_REF_NAME" --repository-private'
    ' "$REPOSITORY_PRIVATE" --commit "$GITHUB_SHA" --branch origin/main --not-released'
    ' --github-output "$GITHUB_OUTPUT"'
)


def test_the_release_check_runs_on_a_tag_before_publish_and_needs_nothing_installed():
    job = JOBS["release_check"]
    assert needs(job) == {"changes"}
    assert "release_check" in needs(JOBS["publish"])
    # Read-only: gh lists releases with the job's token, which may read and nothing more.
    assert job["permissions"] == {"contents": "read"}
    checkout, check = job["steps"]
    # The whole history: git needs main's commits and every tag to place the tag among them.
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 0}
    assert " ".join(check["run"].split()) == CHECK_COMMAND
    # Every value from the event or the repository reaches the shell through env:, as text.
    assert check["env"] == {
        "REPOSITORY_PRIVATE": "${{ github.event.repository.private }}",
        "GH_TOKEN": "${{ github.token }}",
        "GH_REPO": "${{ github.repository }}",
    }
    # `latest` is no output: no job reads it, and job `aliases` decides it when it runs.
    assert job["outputs"] == {"candidate": "${{ steps." + check["id"] + ".outputs.candidate }}"}
    # The check itself runs on a tag; on main the job passes without it and publish goes on.
    assert evaluate(check["if"], TAG) and not evaluate(check["if"], MAIN_PUSH)
    part = RAW_WORKFLOW[RAW_WORKFLOW.index("  release_check:") :]
    part = " ".join(part[: part.index("  publish:")].replace("# ", "").split())
    assert (
        "The whole history (fetch-depth: 0): git places the tag's commit on main and among"
        " the repository's tags." in part
    )


def run_the_check_step(tmp_path, private: str, released: str = "[]") -> tuple[int, str, str]:
    """Job `release_check`'s step as the runner runs it (bash -e), in a repository made here:
    the real script, a tag v1.2.3 on main's head, origin/main, and a gh that prints
    RELEASED. Returns the exit code, stderr and what the step wrote to GITHUB_OUTPUT."""
    root, bin_ = tmp_path / "repo", tmp_path / "bin"
    (root / "scripts").mkdir(parents=True)
    bin_.mkdir()
    (root / "scripts" / "release_check.py").write_text(
        (REPO / "scripts" / "release_check.py").read_text(encoding="utf-8")
    )
    (root / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    (root / "CHANGELOG.md").write_text("## 1.2.3 - 2026-01-31\n\n- One.\n")
    (bin_ / "python3").symlink_to(sys.executable)
    (bin_ / "gh").write_text(f"#!/bin/sh\ncat <<'EOF'\n{released}\nEOF\n")
    (bin_ / "gh").chmod(0o755)
    env = {
        "PATH": f"{bin_}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
    }
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "."],
        ["commit", "-q", "-m", "one"],
        ["tag", "v1.2.3"],
        ["update-ref", "refs/remotes/origin/main", "HEAD"],
    ):
        subprocess.run(["git", "-C", str(root), *args], env=env, check=True)
    sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    output = tmp_path / "github_output"
    output.write_text("")
    step = JOBS["release_check"]["steps"][1]
    done = run_step(
        step["run"],
        cwd=root,
        env={
            **env,
            "GITHUB_REF_NAME": "v1.2.3",
            "GITHUB_SHA": sha,
            "GITHUB_OUTPUT": str(output),
            "REPOSITORY_PRIVATE": private,
        },
    )
    return done.returncode, done.stderr, output.read_text()


def test_the_check_step_passes_a_tag_of_the_public_repository_and_says_latest(tmp_path):
    code, err, output = run_the_check_step(tmp_path, private="false")
    assert (code, err, output) == (0, "", "latest=true\n")


def test_a_tag_pushed_to_a_private_copy_is_refused_and_publishes_and_releases_nothing(tmp_path):
    """The same workflow runs in a private copy of this repository; a tag pushed there by
    habit is refused by `release_check` in one line, and nothing after it runs."""
    code, err, output = run_the_check_step(tmp_path, private="true")
    assert code == 1 and output == ""
    assert err == (
        "release refused: the repository is private: a release is made only where the"
        " public reads it\n"
    )
    ran = jobs_that_run(TAG, failed=frozenset({"release_check"}))
    assert ran == TAG_GATE
    assert not ran & {"publish", "verify", "package", "attest", "release", "aliases", "deploy"}


def test_a_tag_that_already_has_a_release_is_refused_by_the_check_step(tmp_path):
    code, err, _ = run_the_check_step(tmp_path, private="false", released='[{"tagName":"v1.2.3"}]')
    assert code == 1
    assert err == (
        "release refused: v1.2.3 already has a GitHub release: a released version is"
        " published once\n"
    )


def test_no_shell_line_holds_an_expression():
    """Event text and other values reach a `run:` through env:, never `${{ }}` in the line."""
    runs = [step["run"] for job in JOBS.values() for step in job["steps"] if "run" in step]
    assert len(runs) > 20
    for run in runs:
        assert "${{" not in run, run


def test_the_header_says_what_a_tag_must_be_and_what_it_publishes():
    header = RAW_WORKFLOW.split("\nname:")[0]
    flat = " ".join(header.replace("# ", "").split())
    for sentence in (
        "A v* tag is first checked by job `release_check` (scripts/release_check.py), and"
        " `publish` needs it.",
        "It refuses, in one line, a tag pushed to a private repository (a private copy of this"
        " one runs the same workflow), a tag that is not `v` and the version in pyproject.toml,"
        " a version whose section of CHANGELOG.md is missing, has no entry, holds a control"
        " character other than a line feed or a tab, or is dated after"
        " the day of the run, a tag whose commit is not on main, a tag lower than another of"
        " its X.Y, and a version that already has a GitHub release.",
        "A tag is published as X.Y.Z, a full version that never moves: when the registry has"
        " it already, `publish` carries on with the same digest and stops at another one.",
        "A push to main is published as main and sha-<commit>, never latest, and sha-<commit>"
        " is written once, by the same rule. A tag writes no sha- tag.",
        "After a release, job `aliases`, one at a time across all runs, points latest at the"
        " highest released version and each X.Y at its highest X.Y.z, deciding from the"
        " releases and the registry as they are when it runs.",
        "Job `verify` smoke-tests the published image on main and on tags; in a public"
        " repository it first pulls the image with no login, as the run guide does (a tag's"
        " version, or on main the commit's own tag), and checks its digest.",
        "A run that failed after `publish` and before the release was made is finished by"
        " re-running only its failed jobs.",
    ):
        assert sentence in flat, sentence
    assert "A tag is published as X.Y.Z, X.Y and latest" not in flat
    assert "A tag is published as X.Y.Z and X.Y" not in flat


def test_a_push_to_main_still_publishes_verifies_and_deploys_and_makes_no_release():
    assert jobs_that_run(MAIN_PUSH) == EVERYTHING
    assert "release" not in jobs_that_run(MAIN_PUSH)


def test_a_tag_runs_the_gate_publishes_verifies_and_releases_but_never_deploys():
    assert jobs_that_run(TAG) == A_RELEASE
    assert "deploy" not in A_RELEASE
    # Nothing of a release runs from a pull request or a manual run.
    for event in (pull_request(draft=False), not_a_pull_request("workflow_dispatch", "x/y/z")):
        ran = jobs_that_run(event)
        assert not ran & {"release_check", "publish", "verify", "package", "release"}


def test_publish_pushes_the_full_version_only_and_never_latest_or_x_y():
    # The first tag, a patch on an older line, a higher version: the full version, alone.
    assert release_tags("v0.1.0", []) == {"0.1.0"}
    assert release_tags("v0.1.1", ["v0.1.0", "v0.2.0"]) == {"0.1.1"}
    assert release_tags("v0.2.0", ["v0.1.0", "v0.1.1"]) == {"0.2.0"}
    # A tag lower than another of its line is refused: release_check fails, nothing is
    # published, and X.Y never goes backwards.
    with pytest.raises(release_check.Refused):
        release_tags("v0.1.1", ["v0.1.0", "v0.1.2"])
    refused = jobs_that_run(TAG, failed=frozenset({"release_check"}))
    assert not refused & {"publish", "package", "release", "aliases"}
    # A push to main publishes main and the commit, never latest.
    assert image_tags(MAIN_PUSH) == {"main", f"sha-{SHA[:7]}"}
    assert image_tags(TAG) == {"0.1.0"}
    meta = next(s for s in JOBS["publish"]["steps"] if s.get("id") == "meta")
    assert "latest" not in meta["with"]["tags"] and "{{major}}" not in meta["with"]["tags"]
    # The build's provenance and bill of materials are made by image_build, not here.
    build = next(
        s for s in JOBS["image_build"]["steps"] if s.get("uses", "").startswith("docker/build-push")
    )
    assert build["with"]["provenance"] == "mode=max" and build["with"]["sbom"] is True


# --- the release's files: built by `package`, released by `release` ---------------------------

# .get: until the job exists, its tests fail one by one rather than the module.
PACKAGE = JOBS.get("package", {})
RELEASE = JOBS["release"]
CONSTRAINTS = REPO / "build-constraints.txt"


def test_package_builds_on_a_tag_after_the_check_publish_and_verify():
    assert needs(PACKAGE) == {"release_check", "publish", "verify"}
    ok_needs = {n: {"result": "success"} for n in needs(PACKAGE)}
    ok_status = {"failure()": False, "cancelled()": False}
    assert evaluate(PACKAGE["if"], {**TAG, "needs": ok_needs}, ok_status)
    assert not evaluate(PACKAGE["if"], {**MAIN_PUSH, "needs": ok_needs}, ok_status)
    assert "package" in jobs_that_run(TAG)
    assert "package" not in jobs_that_run(MAIN_PUSH)
    # The check refused the tag, or publish or verify failed: nothing is built or released.
    for failed in ("release_check", "publish", "verify"):
        ran = jobs_that_run(TAG, failed=frozenset({failed}))
        assert not ran & {"package", "release", "deploy"}, failed


def test_package_holds_no_write_permission_and_no_secret():
    assert PACKAGE["permissions"] == {}
    configuration = yaml.safe_dump(PACKAGE)
    assert "secrets." not in configuration
    assert "environment" not in PACKAGE
    # The checkout fetches with the job's read token and keeps none; nothing after it has one.
    assert [step["name"] for step in PACKAGE["steps"] if reaches_the_token(step)] == ["Check out"]
    assert PACKAGE["steps"][0]["with"] == {"persist-credentials": False}
    uv = next(s for s in PACKAGE["steps"] if s.get("uses", "").startswith("astral-sh/setup-uv"))
    # uv's version is pinned; no token, and no cache the gate's jobs wrote on this runner.
    assert uv["with"] == {"version": "0.11.19", "enable-cache": False, "github-token": ""}


def test_package_builds_with_hashed_build_requirements_and_keeps_the_files_briefly():
    runs = [step.get("run", "") for step in PACKAGE["steps"]]
    notes = next(r for r in runs if "release_check.py" in r)
    assert notes.splitlines() == [
        'mkdir "$RUNNER_TEMP/release"',
        'python3 scripts/release_check.py "$GITHUB_REF_NAME" --notes'
        ' > "$RUNNER_TEMP/release/notes.md"',
    ]
    build = next(r for r in runs if "uv build" in r)
    assert " ".join(build.split()) == (
        "uv build --build-constraint build-constraints.txt --require-hashes"
        ' --out-dir "$RUNNER_TEMP/release"'
    )
    used = [w for r in runs for w in re.findall(r"\buv \w+|\bpip\b|\bpytest\b|rehearse_\w+", r)]
    assert used == ["uv build"], used
    upload = PACKAGE["steps"][-1]
    assert upload["uses"].startswith("actions/upload-artifact@")
    # Hidden files stay out (the action's default): uv writes a .gitignore into the directory.
    assert upload["with"] == {
        "name": "release",
        "path": "${{ runner.temp }}/release/",
        "if-no-files-found": "error",
        "retention-days": 5,
        "overwrite": True,
    }


def constraints() -> dict[str, list[str]]:
    """build-constraints.txt as {name==version: [hashes]}."""
    text = CONSTRAINTS.read_text(encoding="utf-8").replace("\\\n", " ")
    found = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            requirement, *options = line.split()
            found[requirement] = [o.removeprefix("--hash=") for o in options]
            assert all(o.startswith("--hash=") for o in options), line
    return found


def test_every_build_requirement_is_pinned_with_its_hashes():
    pinned = constraints()
    assert len(pinned) > 1
    for requirement, hashes in pinned.items():
        assert re.fullmatch(r"[a-z0-9-]+==\d+(\.\d+)+", requirement), requirement
        assert hashes and all(re.fullmatch(r"sha256:[0-9a-f]{64}", h) for h in hashes), requirement
    # The backend is the version pyproject.toml asks for.
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    (requires,) = re.findall(r'^requires = \["([^"]+)"\]$', text, re.M)
    assert requires in pinned


def test_release_builds_nothing_installs_nothing_and_checks_out_nothing():
    assert needs(RELEASE) == {"release_check", "package", "publish", "verify", "attest"}
    release_if = " ".join(RELEASE["if"].split())
    package_if = " ".join(PACKAGE["if"].split())
    assert "github.event_name == 'push' && github.ref_type == 'tag'" in release_if
    assert "needs.package.result == 'success' && needs.attest.result == 'success'" in release_if
    assert "needs.publish.result == 'success' && needs.verify.result == 'success'" in package_if
    assert RELEASE["permissions"] == {"contents": "write"}
    steps = RELEASE["steps"]
    # (The first asks the runner's gh for its version: test_ci_release_gh_version_unit.)
    assert [s.get("uses", "run").split("@")[0] for s in steps] == [
        "run",
        "actions/download-artifact",
        "run",
        "run",
    ]
    configuration = yaml.safe_dump(RELEASE)
    assert not re.search(r"\buv\b|setup-uv|\bpip\b|actions/checkout", configuration)
    download = steps[1]
    assert download["with"] == {"name": "release", "path": "${{ runner.temp }}/release"}


def run_release_step(tmp_path, step: dict, files: list[str], tag: str = "v1.2.3"):
    """A step of job `release` as the runner runs it (bash -e), with FILES in the directory
    the download step fills, and a gh that keeps the arguments of each write, one a line."""
    release, bin_ = tmp_path / "release", tmp_path / "bin"
    release.mkdir()
    bin_.mkdir()
    for name in files:
        (release / name).write_text(name)
    # SHA256SUMS, as `package` writes it, over the files the case gives.
    sums = [f"{hashlib.sha256(n.encode()).hexdigest()}  {n}" for n in files if n != ".hidden"]
    (release / "SHA256SUMS").write_text("".join(f"{line}\n" for line in sums))
    (bin_ / "sha256sum").write_text(
        '#!/bin/sh\nif [ -x /usr/bin/sha256sum ]; then exec /usr/bin/sha256sum "$@"; fi\n'
        'exec shasum -a 256 "$@"\n'
    )
    (bin_ / "sha256sum").chmod(0o755)
    first_attempt_gh(bin_)  # no release for the tag yet, then the draft the step asks for
    return run_step(
        step["run"],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_}:/usr/bin:/bin",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_REF_NAME": tag,
            "GITHUB_SHA": "1a2b3c4d" * 5,
            "GH_REPO": "owner/repo",
        },
    )


THE_FILES = ["cairn_memory-1.2.3-py3-none-any.whl", "cairn_memory-1.2.3.tar.gz", "notes.md"]


def test_the_check_passes_one_wheel_and_one_source_archive_of_the_version(tmp_path):
    check = RELEASE["steps"][2]
    assert check["name"] == "Check the files"
    done = run_release_step(tmp_path, check, THE_FILES)
    assert (done.returncode, done.stderr) == (0, ""), done.stdout


@pytest.mark.parametrize(
    "files",
    [
        [*THE_FILES, "extra.txt"],
        [*THE_FILES, "cairn_memory-1.2.3-py3-none-manylinux_2_17_x86_64.whl"],
        ["cairn_memory-1.2.4-py3-none-any.whl", "cairn_memory-1.2.4.tar.gz", "notes.md"],
        ["cairn_memory-1.2.3-py3-none-any.whl", "notes.md"],
        ["cairn_memory-1.2.3-py3-none-any.whl", "cairn_memory-1.2.3.tar.gz"],
        [*THE_FILES, ".hidden"],
        [],
    ],
)
def test_the_check_refuses_anything_else(tmp_path, files):
    done = run_release_step(tmp_path, RELEASE["steps"][2], files)
    assert done.returncode == 1
    assert "::error::" in done.stdout


def test_the_release_is_made_with_the_two_files_by_name_and_the_notes(tmp_path):
    create = RELEASE["steps"][3]
    assert create["name"] == "Create the release"
    assert create["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "GH_REPO": "${{ github.repository }}",
        "CANDIDATE": "${{ needs.release_check.outputs.candidate }}",
    }
    done = run_release_step(tmp_path, create, THE_FILES)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (tmp_path / "gh-create").read_text().splitlines() == [
        "release",
        "create",
        "v1.2.3",
        "--draft",
        "--verify-tag",
        "--target",
        "1a2b3c4d" * 5,  # the run's commit, $GITHUB_SHA
        "--title",
        "v1.2.3",
        "--notes-file",
        f"{tmp_path}/body.md",
        "cairn_memory-1.2.3-py3-none-any.whl",
        "cairn_memory-1.2.3.tar.gz",
        "SHA256SUMS",
    ]


# The inputs of each action these jobs use that default to the workflow's token, read from the
# action's own action.yml at the pinned commit: actions/checkout@3d3c42e5 `token`,
# astral-sh/setup-uv@68559e99 `github-token` ("Used when downloading uv from GitHub releases").
# actions/upload-artifact@cf430e03 has no token input; actions/download-artifact@9000827c has
# `github-token` with no default (needed only for another repository or run): None.
# actions/attest@1e69f48a `github-token`, whose default is the workflow's token.
# Read on 2026-11-19: docker/build-push-action@c3c9e263 `github-token` ("used to authenticate
# against a repository for Git context") and docker/metadata-action@dc802804 `github-token`
# default to the token; docker/setup-qemu-action@99012661, docker/setup-buildx-action@f87e5991
# and docker/login-action@8aa5831f have no input that does.


def test_the_write_token_reaches_the_step_that_makes_the_release_and_nothing_else():
    steps = RELEASE["steps"]
    assert [step["name"] for step in steps if reaches_the_token(step)] == ["Create the release"]
    # The model sees a token handed to the download explicitly, and setup-uv's implicit one.
    download = steps[1]
    given = {**download, "with": {**download["with"], "github-token": "${{ github.token }}"}}
    assert reaches_the_token(given)
    uv = next(s for s in PACKAGE["steps"] if s.get("uses", "").startswith("astral-sh/setup-uv"))
    bare = {**uv, "with": {k: v for k, v in uv["with"].items() if k != "github-token"}}
    assert reaches_the_token(bare)
    assert (
        "The only job that may write to the repository, so it builds nothing, installs"
        " nothing and checks out nothing: its token reaches one step, the last, which makes"
        " the release." in job_comment("release")
    )


def test_only_the_release_job_may_write_to_the_repository():
    writers = {
        name for name, job in JOBS.items() if job.get("permissions", {}).get("contents") == "write"
    }
    assert writers == {"release"}
    assert WORKFLOW["permissions"] == {"contents": "read"}


def test_a_failed_tag_run_is_finished_without_publishing_again():
    # Of the jobs a tag runs (deploy, which needs verify, is skipped on a tag by its own
    # condition): verify failed: it, package and release run again; publish's digest is kept.
    assert rerun({"verify"}) & A_RELEASE == {"verify", "package", "attest", "release", "aliases"}
    # package failed, or release found no files (an expired artifact): package, attest and
    # release, and the aliases after them.
    assert rerun({"package"}) == {"package", "attest", "release", "aliases"}
    assert rerun({"release"}) == {"release", "aliases"}
    for names in ({"verify"}, {"package"}, {"release"}):
        assert not rerun(names) & {"release_check", "publish"}
    comment = job_comment("release_check")
    assert (
        "A run that failed after `publish` and before the release was made: re-run only its"
        " failed jobs (\"Re-run failed jobs\"), which keeps this job's result, image_build's"
        " archive and `publish`'s digest." in comment
    )
    assert (
        "A tag's run builds nothing, so re-running every job promotes the same digest again,"
        " until the release exists and this job refuses the tag." in comment
    )
    assert "Re-running every job would publish the version again" not in comment
    assert (
        "If `release` finds no files from `package` (they are kept five days), re-run"
        " `package` alone: GitHub re-runs `release` after it, and nothing that publishes."
        in comment
    )


def test_the_comments_say_where_the_files_are_built_and_why():
    package = job_comment("package")
    for sentence in (
        "a build backend or a dependency of it that turned hostile runs in a job that may only"
        " read the repository and holds no secret, on the gate's runner",
        "uv builds with the versions and hashes in build-constraints.txt (--build-constraint"
        " with --require-hashes: a package not listed, or a file whose hash differs, stops the"
        " build).",
        "The files and the release's text are kept as an artifact of this run for five days.",
    ):
        assert sentence in package, sentence
    header = " ".join(RAW_WORKFLOW.split("\nname:")[0].replace("# ", "").split())
    for sentence in (
        "On a tag, job `package` then builds the wheel and the source archive, with the build"
        " backend and its dependencies pinned by hash in build-constraints.txt, in a job that"
        " may only read the repository and holds no secret, and keeps them as an artifact of"
        " the run for five days.",
        "Job `release`, the only job that may write to the repository, builds and installs"
        " nothing: it checks that the artifact holds one wheel and one source archive of the"
        " tag's version, and makes the GitHub release with them and the version's section of"
        " CHANGELOG.md.",
        "CAIRN_GATE_RUNNER names the runner of the gate's jobs (`quick` and `browser` aside,"
        " which always run on ubuntu-latest) and of `package`, which runs the"
        " build backend; CAIRN_RELEASE_RUNNER names the runner of publish, verify, attest_main,"
        " attest, release, aliases and deploy, which no job of a pull request uses and which"
        " runs no build.",
    ):
        assert sentence in header, sentence


def build_line(job: dict) -> str:
    (line,) = [" ".join(s["run"].split()) for s in job["steps"] if "uv build" in s.get("run", "")]
    return line


def test_every_gate_run_rehearses_the_build_a_tag_runs():
    """A constraints file that no longer resolves, or a hash that no longer matches, fails a
    pull request or a push to main, never a tag after its image is published."""
    check = JOBS.get("package_check", {})
    assert build_line(check) == build_line(PACKAGE)
    assert needs(check) == set() and "if" not in check
    assert "package_check" in needs(JOBS["publish"])
    for context in (pull_request(draft=True), pull_request(draft=False), MAIN_PUSH, TAG):
        assert "package_check" in jobs_that_run(context)
    # No secret, no write, nothing uploaded; the same uv, with no token and no cache.
    assert check["permissions"] == {"contents": "read"}
    assert "secrets." not in yaml.safe_dump(check) and "environment" not in check
    assert [s["name"] for s in check["steps"] if reaches_the_token(s)] == ["Check out"]
    assert check["steps"][0]["with"] == {"persist-credentials": False}
    assert not any("upload-artifact" in s.get("uses", "") for s in check["steps"])
    uv = next(s for s in check["steps"] if s.get("uses", "").startswith("astral-sh/setup-uv"))
    assert uv == next(
        s for s in PACKAGE["steps"] if s.get("uses", "").startswith("astral-sh/setup-uv")
    )
    assert check["runs-on"] == PACKAGE["runs-on"]
    assert (
        "Every gate run, draft pull requests included, rehearses the build a tag runs (job"
        " `package_check`, the same uv build line as job `package`, uploading nothing)"
        in " ".join(RAW_WORKFLOW.split("\nname:")[0].replace("# ", "").split())
    )


def test_no_step_names_a_shell_so_every_step_runs_as_the_tests_run_it():
    """ci_shell.run_step runs a step as `bash -e`, GitHub's command for a step that names no
    shell; that holds only while no step, job or the workflow names one."""
    workflow = yaml.safe_load(RAW_WORKFLOW)
    assert "defaults" not in workflow
    for name, job in JOBS.items():
        assert "defaults" not in job, name
        for step in job.get("steps", []):
            assert "shell" not in step, (name, step.get("name"))


GH_JOBS = {"release_check", "image_build", "publish", "release", "aliases"}


def test_the_jobs_that_run_gh_are_named_with_their_runner_in_the_header_and_the_readme():
    """A runner of the operator's own needs the GitHub CLI for these jobs; GitHub's have it."""
    found = {
        name
        for name, job in JOBS.items()
        for s in job.get("steps", [])
        if re.search(r"(^|[\s(;|&$])gh\s", s.get("run", ""))
        or "release_check.py" in s.get("run", "")
    }
    assert found == GH_JOBS | {
        "package"
    }  # package runs release_check.py --notes, which needs no gh
    assert "def run_gh" in (REPO / "scripts" / "release_check.py").read_text()
    said = (
        "Five jobs run the GitHub CLI, gh, which GitHub's runners have: release_check and"
        " image_build, on a tag, on the runner CAIRN_GATE_RUNNER names, and publish, on a push"
        " to main, release and aliases, on the runner CAIRN_RELEASE_RUNNER names. image_build"
        " refuses a gh before 2.102.0, which fixed how gh attestation verify matches"
        " --signer-workflow and --source-ref (GHSA-wjmr-j3rp-mh2g, GHSA-4mq3-hpgx-9cx8)."
    )
    header = " ".join(RAW_WORKFLOW.split("\nname:")[0].replace("# ", "").split())
    assert said in header
    readme = " ".join((REPO / "README.md").read_text().replace("`", "").split())
    assert said in readme


def test_release_asks_for_the_gh_whose_source_its_handling_of_a_draft_was_read_in():
    """The fifth report's remaining suspicion: the version of gh is checked on the gate's
    runner, and `release` runs on another, with a gh of its own. What `release` does with a
    draft was read in gh's source at v2.102.0 and in no earlier version, so the header and the
    README ask for that version on the runner `release` runs on, whatever checks it."""
    said = (
        "Job release needs gh 2.102.0 or later too, on the runner CAIRN_RELEASE_RUNNER names:"
        " what gh does with a draft (it makes one and takes none back, and finds one by its"
        " tag) was read in that version's source and in no earlier one."
    )
    header = " ".join(RAW_WORKFLOW.split("\nname:")[0].replace("# ", "").split())
    assert header.count(said) == 1
    readme = " ".join((REPO / "README.md").read_text().replace("`", "").split())
    assert readme.count(said) == 1
    assert "CAIRN_RELEASE_RUNNER" in RELEASE["runs-on"]
    assert "CAIRN_GATE_RUNNER" in JOBS["image_build"]["runs-on"]
    # The version is the one the step's own sentences about gh quote its source at.
    create = next(s for s in RELEASE["steps"] if s.get("name") == "Create the release")
    assert "cli/cli at v2.102.0" in create["run"]
    assert "v2.102.0" in (REPO / "tests/unit/ci/test_ci_release_step_unit.py").read_text()


# A shell or python expansion in an error line, written `…` in the README's table.


def test_every_stop_of_the_workflow_has_its_row_in_the_readme_and_no_row_is_stale():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    part = readme[readme.index("### When a run stops") : readme.index("## Planned")]
    rows = [line for line in part.splitlines() if line.startswith("| `")]
    named = {}
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split(" | ")]
        assert len(cells) == 3 and all(cells), row
        named[cells[0].strip("`")] = cells
    assert len(stops()) > 25
    assert set(named) == stops()
