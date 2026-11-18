"""What infra/gcp/README.md, "Deploy from GitHub Actions", says about the CI workflow."""

import importlib.util
import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "ci_changes_for_workflow", REPO / "scripts" / "ci_changes.py"
)
ci_changes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_changes)
WORKFLOW = yaml.safe_load((REPO / ".github" / "workflows" / "ci-cd.yml").read_text())
JOBS = WORKFLOW["jobs"]
DEPLOY = JOBS["deploy"]
TF_VARIABLES = (REPO / "infra" / "gcp" / "variables.tf").read_text(encoding="utf-8")


def needs(job: dict) -> set[str]:
    value = job.get("needs", [])
    return {value} if isinstance(value, str) else set(value)


def default_of(variable: str) -> str:
    match = re.search(rf'variable "{variable}" \{{.*?default\s*=\s*"([^"]*)"', TF_VARIABLES, re.S)
    assert match, variable
    return match.group(1)


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = [step["uses"] for job in JOBS.values() for step in job["steps"] if "uses" in step]
    assert len(uses) > 10
    for action in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), action


def test_only_the_deploy_job_may_ask_for_an_id_token():
    assert "id-token" not in WORKFLOW.get("permissions", {})
    holders = {name for name, job in JOBS.items() if "id-token" in job.get("permissions", {})}
    assert holders == {"deploy"}
    assert DEPLOY["permissions"]["id-token"] == "write"


def test_deploy_waits_for_tests_infra_both_image_smokes_publish_and_verify():
    assert needs(DEPLOY) == {"test", "infra", "image", "publish", "verify"}
    platforms = [entry["platform"] for entry in JOBS["image"]["strategy"]["matrix"]["include"]]
    assert sorted(platforms) == ["linux/amd64", "linux/arm64"]
    assert needs(JOBS["verify"]) == {"publish"}


def test_publish_waits_for_the_infrastructure_check_and_the_image_stack_and_host_jobs():
    assert needs(JOBS["publish"]) == {"infra", "image", "stack", "host", "release_check"}


def test_gate_runner_uses_json_override_and_defaults_to_hosted():
    for name in ("changes", "test", "infra", "image", "stack", "host", "release_check"):
        expression = JOBS[name]["runs-on"]
        match = re.fullmatch(
            r"\$\{\{ fromJSON\(vars\.CAIRN_GATE_RUNNER \|\| '([^']+)'\) \}\}", expression
        )
        assert match, (name, expression)
        assert json.loads(match[1]) == "ubuntu-latest"


def test_release_runner_uses_separate_json_override_and_defaults_to_hosted():
    for name in ("publish", "verify", "deploy", "release"):
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


@pytest.mark.parametrize("name", ["publish", "verify"])
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
    if name == "publish":
        build = next(step for step in steps if step.get("id") == "build")
        assert set(build["with"]["platforms"].split(",")) == {"linux/amd64", "linux/arm64"}
        assert build["with"]["push"] is True


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
    assert assignment == 'image="ghcr.io/${GITHUB_REPOSITORY,,}@$DIGEST"'
    # The shell's pull selection and the smoke script's docker children must agree.
    for command in ("docker", "python3"):
        tool = tmp_path / command
        tool.write_text(
            "#!/bin/sh\n"
            'test "$DOCKER_DEFAULT_PLATFORM" = linux/amd64 || exit 77\n'
            f'printf "{command} %s\\n" "$*" >> "$CALLS"\n'
        )
        tool.chmod(0o755)
    calls = tmp_path / "calls"
    digest = "sha256:" + "a" * 64
    result = subprocess.run(
        ["/bin/bash", "-e", "-c", commands],
        env={
            "PATH": str(tmp_path),
            "CALLS": str(calls),
            "image": f"ghcr.io/owner/repo@{digest}",
            "DOCKER_DEFAULT_PLATFORM": job.get("env", {}).get("DOCKER_DEFAULT_PLATFORM", ""),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines() == [
        f"docker pull --quiet --platform linux/amd64 ghcr.io/owner/repo@{digest}",
        f"python3 scripts/smoke_container.py ghcr.io/owner/repo@{digest}",
    ]


def test_release_jobs_do_not_run_the_application_environment_or_rehearsals_on_the_host():
    for name in ("publish", "verify", "deploy"):
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
    build = next(step for step in steps if step.get("uses", "").startswith("docker/build-push"))
    assert build["with"]["platforms"] == "${{ matrix.platform }}"
    assert build["with"]["cache-from"] == "type=gha,scope=${{ matrix.arch }}"
    assert build["with"]["cache-to"] == "type=gha,mode=max,scope=${{ matrix.arch }}"


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
    build_index = next(
        i for i, step in enumerate(job["steps"]) if "docker build" in step.get("run", "")
    )
    assert job["steps"].index(qemu) < build_index
    build = shlex.split(job["steps"][build_index]["run"])
    assert build[build.index("--platform") + 1] == "linux/amd64"
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
    preflight = next(step for step in steps if step.get("name") == "Check the image platform")
    assert steps.index(preflight) == len(steps) - 2
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'test "$*" = "image inspect --format {{.Architecture}} cairn:ci" || exit 77\n'
        'printf "%s\\n" "$IMAGE_ARCH"\n'
    )
    docker.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", "-e", "-c", preflight["run"]],
        env={"PATH": str(tmp_path), "IMAGE_ARCH": architecture},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is success


def ancestors(name: str, jobs: dict = JOBS) -> set[str]:
    """Every job `name` waits for, directly or through the jobs it needs."""
    direct = needs(jobs[name])
    return direct.union(*(ancestors(job, jobs) for job in direct))


def gate_jobs_missing_from_publish(jobs: dict) -> set[str]:
    """The gate is read from the workflow, not listed here: the jobs that run for a pull
    request that is ready for review whose change needs the image (the run a merge waits for,
    as the comment at the top of the workflow says). Publish must stand behind every one."""
    gate = jobs_that_run(pull_request(draft=False), {"changes": {"image": "true"}}, jobs)
    return gate - ancestors("publish", jobs)


def test_every_job_of_the_gate_is_an_ancestor_of_publish():
    assert gate_jobs_missing_from_publish(JOBS) == set()
    # A job added to the gate and left out of publish is found.
    added = {**JOBS, "docs_check": {"runs-on": "ubuntu-latest", "steps": []}}
    assert gate_jobs_missing_from_publish(added) == {"docs_check"}
    # So is `infra` dropped from publish's needs.
    dropped = {**JOBS, "publish": {**JOBS["publish"], "needs": ["image", "stack", "host"]}}
    assert gate_jobs_missing_from_publish(dropped) == {"infra"}


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
IMAGE_CONDITION = f"({NOT_A_DRAFT}) && {SHIPS}"


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


def test_the_three_image_jobs_share_one_condition_and_the_cheap_jobs_have_none():
    assert [" ".join(JOBS[name]["if"].split()) for name in BUILD_JOBS] == [IMAGE_CONDITION] * 3
    assert "if" not in JOBS["test"]
    assert "if" not in JOBS["infra"]
    assert "if" not in JOBS["changes"]


def test_the_changes_job_runs_the_script_on_the_whole_history_reading_runs_and_nothing_more():
    changes = JOBS["changes"]
    assert needs(changes) == set()
    # contents: read for the checkout, actions: read to list this workflow's runs and their
    # jobs on main. A job's own list sets every permission it does not name to none.
    assert changes["permissions"] == {"contents": "read", "actions": "read"}
    checkout, decide = changes["steps"]
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 0}
    assert decide["run"] == "python3 scripts/ci_changes.py"
    assert changes["outputs"] == {"image": "${{ steps." + decide["id"] + ".outputs.image }}"}
    # The job's token for the API; the rest the script reads from GitHub's own variables.
    assert decide["env"] == {"GITHUB_TOKEN": "${{ github.token }}"}
    assert set(decide) == {"name", "id", "env", "run"}


def test_the_jobs_that_come_after_keep_their_needs_and_conditions_exactly():
    # The three image jobs also need `changes`, the only way to read its output.
    assert needs(JOBS["image"]) == {"test", "changes"}
    assert needs(JOBS["host"]) == {"test", "changes"}
    assert needs(JOBS["stack"]) == {"changes"}
    assert needs(JOBS["publish"]) == {"infra", "image", "stack", "host", "release_check"}
    assert JOBS["publish"]["if"] == "github.event_name == 'push' && vars.CAIRN_PUBLISH != 'false'"
    assert needs(JOBS["verify"]) == {"publish"}
    assert JOBS["verify"]["if"] == (
        "github.event_name == 'push' &&"
        " (github.ref == 'refs/heads/main' || github.ref_type == 'tag')"
    )
    assert needs(DEPLOY) == {"test", "infra", "image", "publish", "verify"}
    assert " ".join(DEPLOY["if"].split()) == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
        " && vars.GCP_INSTANCE != '' && vars.GCP_ZONE != ''"
    )


# A small evaluator for the expressions the workflow's `if` lines use: context paths, string
# literals, `!`, `==`, `!=`, `&&`, `||`, parentheses and the status functions `success()`,
# `always()`, `failure()` and `cancelled()`. GitHub treats null, false, 0 and '' as false; a
# path that is not in the context is null.


def evaluate(expression: str, context: dict, functions: dict | None = None) -> bool:
    text = " ".join(expression.split())
    tokens = re.findall(r"'[^']*'|==|!=|&&|\|\||[!()]|\w+\(\)|[\w.]+", text)
    assert "".join(tokens) == text.replace(" ", ""), expression  # nothing was skipped
    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else None

    def take():
        nonlocal position
        position += 1
        return tokens[position - 1]

    def truthy(value) -> bool:
        return value not in (None, False, 0, "")

    def operand():
        token = take()
        if token == "(":
            value = either()
            assert take() == ")"
            return value
        if token == "!":
            return not truthy(operand())
        if token.startswith("'"):
            return token[1:-1]
        if token.endswith("()"):
            return (functions or {})[token]
        value = context
        for part in token.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value

    def comparison():
        value = operand()
        while peek() in ("==", "!="):
            same = take() == "=="
            # GitHub compares null as if it were an empty string.
            value = ((value or "") == (operand() or "")) == same
        return value

    def both():
        value = comparison()
        while peek() == "&&":
            take()
            right = comparison()
            value = right if truthy(value) else value
        return value

    def either():
        value = both()
        while peek() == "||":
            take()
            right = both()
            value = value if truthy(value) else right
        return value

    result = either()
    assert position == len(tokens), (expression, tokens[position:])
    return truthy(result)


STATUS_FUNCTIONS = ("success()", "always()", "failure()", "cancelled()")


def jobs_that_run(context: dict, outputs: dict | None = None, jobs: dict = JOBS) -> set[str]:
    """Which jobs run, as GitHub documents `needs` and `if` (workflow syntax, jobs.<id>.needs;
    expressions, status check functions): a job's `if` without a status function means
    `success() && (if)`, and "a failure or skip applies to all jobs in the dependency chain
    from the point of failure or skip onwards", so success() is false when any job before it
    in its chain was skipped, unless that job's own condition says otherwise (always()). The
    `needs` context holds the jobs a job lists, with their result and, when they ran, their
    OUTPUTS. Nothing fails or is cancelled here: every job that runs succeeds."""
    results: dict[str, str] = {}
    chain: dict[str, set[str]] = {}  # every job before this one, through needs
    while len(results) < len(jobs):
        ready = [n for n, job in jobs.items() if n not in results and needs(job) <= set(results)]
        assert ready, f"a cycle in needs: {set(jobs) - set(results)}"
        for name in ready:
            job = jobs[name]
            chain[name] = set().union(*({n} | chain[n] for n in needs(job)))
            seen = {
                n: {
                    "result": results[n],
                    "outputs": (outputs or {}).get(n, {}) if results[n] == "success" else {},
                }
                for n in needs(job)
            }
            before = [results[n] for n in chain[name]]
            status = {
                "success()": all(result == "success" for result in before),
                "always()": True,
                "failure()": any(result == "failure" for result in before),
                "cancelled()": False,
            }
            condition = job.get("if", "success()")
            if not any(function in condition for function in STATUS_FUNCTIONS):
                condition = f"success() && ({condition})"
            runs = evaluate(condition, {**context, "needs": seen}, status)
            results[name] = "success" if runs else "skipped"
    return {name for name, result in results.items() if result == "success"}


REPOSITORY_VARIABLES = {"GCP_INSTANCE": "vm", "GCP_ZONE": "zone"}


SHA, BASE, HEAD, BEFORE = "c" * 40, "b" * 40, "d" * 40, "a" * 40


def pull_request(draft: bool) -> dict:
    return {
        "github": {
            "event_name": "pull_request",
            "ref": "refs/pull/7/merge",
            "ref_type": "branch",
            "sha": SHA,
            "event": {"pull_request": {"draft": draft, "head": {"sha": HEAD}}},
        },
        "vars": REPOSITORY_VARIABLES,
    }


def not_a_pull_request(event_name: str, ref: str) -> dict:
    event = {}
    if event_name == "push":
        event = {"before": BEFORE, "after": SHA, "created": False, "forced": False}
    return {
        "github": {
            "event_name": event_name,
            "ref": ref,
            "ref_type": "tag" if ref.startswith("refs/tags/") else "branch",
            "ref_name": ref.split("/", 2)[2],
            "sha": SHA,
            "event": event,
        },
        "vars": REPOSITORY_VARIABLES,
    }


# Job `changes` runs on every event, like the two cheap jobs, and takes a few seconds.
CHEAP = {"test", "infra", "changes"}
THE_GATE = CHEAP | {"image", "stack", "host"}
EVERYTHING = THE_GATE | {"release_check", "publish", "verify", "deploy"}
# A v* tag runs the gate, checks the tag against the version, publishes, smoke-tests what it
# published and makes the GitHub release; it never deploys.
A_RELEASE = THE_GATE | {"release_check", "publish", "verify", "release"}


def test_which_jobs_run_for_each_event_when_changes_says_nothing():
    # No output from `changes` (it was never written) runs what ran before it existed.
    table = {
        "a push to a draft pull request": (pull_request(draft=True), CHEAP),
        "a pull request marked ready for review": (pull_request(draft=False), THE_GATE),
        "a push to a pull request that is ready": (pull_request(draft=False), THE_GATE),
        "a push to main": (not_a_pull_request("push", "refs/heads/main"), EVERYTHING),
        "a manual run": (not_a_pull_request("workflow_dispatch", "refs/heads/work"), THE_GATE),
        "a v* tag": (not_a_pull_request("push", "refs/tags/v1.0.0"), A_RELEASE),
    }
    for event, (context, expected) in table.items():
        assert jobs_that_run(context) == expected, event


def test_publishing_has_an_off_switch_and_is_on_when_nothing_is_set():
    """A repository whose registry is not ready sets the variable CAIRN_PUBLISH to `false`:
    a push to main still runs the whole gate, and publishes, verifies and deploys nothing.
    With the variable unset, or set to anything else, a push publishes as before."""
    assert JOBS["publish"]["if"] == "github.event_name == 'push' && vars.CAIRN_PUBLISH != 'false'"
    push = not_a_pull_request("push", "refs/heads/main")
    assert jobs_that_run(push) == EVERYTHING
    for said, expected in (("false", THE_GATE), ("true", EVERYTHING), ("", EVERYTHING)):
        variables = {**REPOSITORY_VARIABLES, "CAIRN_PUBLISH": said}
        assert jobs_that_run({**push, "vars": variables}) == expected, said
    tag = {**not_a_pull_request("push", "refs/tags/v1.0.0"), "vars": {"CAIRN_PUBLISH": "false"}}
    assert jobs_that_run(tag) == THE_GATE


EVENTS = {
    "a push to a draft pull request": pull_request(draft=True),
    "a pull request marked ready for review": pull_request(draft=False),
    "a push to a pull request that is ready": pull_request(draft=False),
    "a push to main": not_a_pull_request("push", "refs/heads/main"),
    "a manual run": not_a_pull_request("workflow_dispatch", "refs/heads/work"),
    "a v* tag": not_a_pull_request("push", "refs/tags/v1.0.0"),
}


PUBLISHED = "e" * 40  # the head of the newest run on main that published an image


def fake_git(paths: list[str]):
    """git as job `changes` sees it: the merge commit's parents, an ancestor check that
    passes, and the changed paths."""

    def git(*args: str) -> str:
        if args[0] == "rev-list":
            return f"{SHA} {BASE} {HEAD}\n"
        if args[0] == "merge-base":
            return ""
        assert args[0] == "diff", args
        return "".join(path + "\0" for path in paths)

    return git


def jobs_for(event: str, paths: list[str], base: str | None = PUBLISHED) -> set[str]:
    """Job `changes` decides with scripts/ci_changes.py; the other jobs follow its output.
    On main the paths are those between BASE, the head of the newest run that published an
    image (None: no such run found), and the commit pushed."""
    github = EVENTS[event]["github"]
    run, _ = ci_changes.decide(
        github["event_name"],
        github["ref"],
        github["sha"],
        github["event"],
        fake_git(paths),
        lambda: base,
    )
    return jobs_that_run(EVENTS[event], {"changes": {"image": "true" if run else "false"}})


A_CHANGE_THAT_SHIPS = ["docs/spec-v1.md", "src/cairn/store.py"]
NOTHING_SHIPS = ["docs/spec-v1.md", "tests/unit/test_store_unit.py"]


def test_which_jobs_run_for_each_event_with_a_change_that_ships_and_one_that_does_not():
    table = {
        # event: (a change that ships, a change where nothing ships)
        "a push to a draft pull request": (CHEAP, CHEAP),
        "a pull request marked ready for review": (THE_GATE, CHEAP),
        "a push to a pull request that is ready": (THE_GATE, CHEAP),
        # On main, "the change" is from the last published head to the commit pushed.
        "a push to main": (EVERYTHING, CHEAP),
        # The changed paths of these two are not known, so they run as before either way.
        "a manual run": (THE_GATE, THE_GATE),
        "a v* tag": (A_RELEASE, A_RELEASE),
    }
    assert set(table) == set(EVENTS)
    for event, (ships, nothing) in table.items():
        assert jobs_for(event, A_CHANGE_THAT_SHIPS) == ships, (event, "ships")
        assert jobs_for(event, NOTHING_SHIPS) == nothing, (event, "nothing ships")


def test_on_main_what_runs_depends_on_the_last_published_head_not_on_the_push():
    push = "a push to main"
    # A docs-only push after a run that published: nothing ships, nothing is deployed.
    assert jobs_for(push, NOTHING_SHIPS) == CHEAP
    # The same push when the newest publishing run is older than a source change (that run
    # failed, was cancelled, or its deploy was refused): the paths since then ship.
    assert jobs_for(push, [*NOTHING_SHIPS, "src/cairn/server.py"]) == EVERYTHING
    # No run that published (a first run, or none among the runs looked at), or a lookup
    # that failed: not known, so the whole gate runs and deploys the head.
    assert jobs_for(push, NOTHING_SHIPS, base=None) == EVERYTHING
    assert jobs_for(push, A_CHANGE_THAT_SHIPS, base=None) == EVERYTHING
    # The published head is the head itself (this run is a re-run): an empty diff runs it all.
    assert jobs_for(push, [], base=SHA) == EVERYTHING
    # Pull requests do not look up a base at all.
    assert jobs_for("a push to a pull request that is ready", NOTHING_SHIPS, base=None) == CHEAP


def test_an_output_that_is_not_exactly_false_runs_the_gate():
    for value in ("true", "", "False ", "skip"):
        ran = jobs_that_run(EVENTS["a push to main"], {"changes": {"image": value}})
        assert ran == EVERYTHING, value
    ran = jobs_that_run(EVENTS["a push to main"], {"changes": {"image": "false"}})
    assert ran == CHEAP


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
        i for i, s in enumerate(steps) if "docker build" in s.get("run", "")
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
    assert len(setups) == 2
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

RAW_WORKFLOW = (REPO / ".github" / "workflows" / "ci-cd.yml").read_text(encoding="utf-8")
TAG = not_a_pull_request("push", "refs/tags/v0.1.0")
MAIN_PUSH = not_a_pull_request("push", "refs/heads/main")


def expression(value: str) -> str:
    """The expression inside `${{ ... }}`."""
    found = re.fullmatch(r"\$\{\{ (.+) \}\}", value.strip())
    assert found, value
    return found[1]


def test_every_action_names_its_version_in_a_comment():
    lines = [line.strip() for line in RAW_WORKFLOW.splitlines() if "uses: " in line]
    assert len(lines) > 10
    for line in lines:
        assert re.fullmatch(r"(- )?uses: [\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*", line), line


def test_the_release_check_runs_on_a_tag_before_publish_and_needs_nothing_installed():
    job = JOBS["release_check"]
    assert needs(job) == {"changes"}
    assert "release_check" in needs(JOBS["publish"])
    assert job["permissions"] == {"contents": "read"}
    checkout, check = job["steps"]
    assert checkout["with"] == {"persist-credentials": False}
    assert check["run"] == 'python3 scripts/release_check.py "$GITHUB_REF_NAME"'
    # The check itself runs on a tag; on main the job passes without it and publish goes on.
    assert evaluate(check["if"], TAG) and not evaluate(check["if"], MAIN_PUSH)


def test_a_push_to_main_still_publishes_verifies_and_deploys_and_makes_no_release():
    assert jobs_that_run(MAIN_PUSH) == EVERYTHING
    assert "release" not in jobs_that_run(MAIN_PUSH)


def test_a_tag_runs_the_gate_publishes_verifies_and_releases_but_never_deploys():
    assert jobs_that_run(TAG) == A_RELEASE
    assert "deploy" not in A_RELEASE
    # Nothing of a release runs from a pull request or a manual run.
    for event in (pull_request(draft=False), not_a_pull_request("workflow_dispatch", "x/y/z")):
        assert not jobs_that_run(event) & {"release_check", "publish", "verify", "release"}


def image_tags(context: dict) -> set[str]:
    """The tags docker/metadata-action makes from the publish job's `tags` and `flavor`, as its
    README documents them: `type=semver` works on a push of a semver tag only ({{version}}
    is MAJOR.MINOR.PATCH, {{major}}.{{minor}} the first two); `type=ref,event=branch` gives
    the branch's name on a branch push; `type=sha` gives `sha-` and the short commit;
    `type=raw` gives its value; `enable` turns an entry off. `flavor: latest=false` adds no
    `latest` of its own. Only the entry types this workflow uses are modelled."""
    step = next(s for s in JOBS["publish"]["steps"] if s.get("id") == "meta")
    assert step["with"]["flavor"].strip() == "latest=false"
    github = context["github"]
    version = None
    if github["ref_type"] == "tag":
        found = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", github["ref_name"])
        version = found.groups() if found else None
    patterns = {"{{version}}": 3, "{{major}}.{{minor}}": 2}
    made = set()
    for line in step["with"]["tags"].strip().splitlines():
        kind, *rest = line.split(",")
        attributes = dict(part.split("=", 1) for part in rest)
        enable = attributes.pop("enable", "true")
        if enable != "true" and not evaluate(expression(enable), context):
            continue
        if kind == "type=semver":
            parts = patterns[attributes.pop("pattern")]
            if version:
                made.add(".".join(version[:parts]))
        elif kind == "type=raw":
            made.add(attributes.pop("value"))
        elif kind == "type=ref":
            assert attributes.pop("event") == "branch"
            if github["ref_type"] == "branch":
                made.add(github["ref_name"])
        elif kind == "type=sha":
            made.add(f"sha-{github['sha'][:7]}")
        else:
            raise AssertionError(line)
        assert attributes == {}, line
    return made


def test_a_tag_publishes_the_version_its_minor_and_latest_and_main_never_latest():
    assert image_tags(TAG) == {"0.1.0", "0.1", "latest"}
    assert image_tags(MAIN_PUSH) == {"main", f"sha-{SHA[:7]}"}
    build = next(s for s in JOBS["publish"]["steps"] if s.get("id") == "build")
    assert build["with"]["tags"] == "${{ steps.meta.outputs.tags }}"
    assert build["with"]["provenance"] == "mode=max" and build["with"]["sbom"] is True


def test_the_release_is_made_from_the_tag_with_the_changelog_and_the_built_files():
    job = JOBS["release"]
    assert needs(job) == {"publish", "verify"}
    assert evaluate(job["if"], TAG) and not evaluate(job["if"], MAIN_PUSH)
    assert job["permissions"] == {"contents": "write"}
    runs = [step.get("run", "") for step in job["steps"]]
    notes = next(r for r in runs if "release_check.py" in r)
    assert notes == (
        'python3 scripts/release_check.py "$GITHUB_REF_NAME" --notes > "$RUNNER_TEMP/notes.md"'
    )
    build = next(r for r in runs if "uv build" in r)
    assert "uv build --out-dir dist" in build
    # The release runner runs no test suite and no rehearsal (decision 150): `uv build` makes
    # the two files, and nothing installs or runs the application's environment.
    used = [w for r in runs for w in re.findall(r"\buv \w+|\bpytest\b|rehearse_\w+", r)]
    assert used == ["uv build"], used
    create = next(step for step in job["steps"] if "gh release create" in step.get("run", ""))
    assert " ".join(create["run"].split()) == (
        'gh release create "$GITHUB_REF_NAME" --verify-tag --title "$GITHUB_REF_NAME"'
        ' --notes-file "$RUNNER_TEMP/notes.md" dist/*'
    )
    # The token reaches only the step that makes the release; the checkout keeps none.
    holders = [step for step in job["steps"] if "github.token" in str(step)]
    assert holders == [create] and create["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "GH_REPO": "${{ github.repository }}",
    }
    uv = next(s for s in job["steps"] if s.get("uses", "").startswith("astral-sh/setup-uv"))
    assert uv["with"]["enable-cache"] is False
    assert job["steps"][0]["with"] == {"persist-credentials": False}


def test_only_the_release_job_may_write_to_the_repository():
    writers = {
        name for name, job in JOBS.items() if job.get("permissions", {}).get("contents") == "write"
    }
    assert writers == {"release"}
    assert WORKFLOW["permissions"] == {"contents": "read"}
