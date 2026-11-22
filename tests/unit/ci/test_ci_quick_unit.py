"""Job `quick` of .github/workflows/ci-cd.yml: the quick deploy's OpenTofu definitions
(infra/quick/aws and infra/quick/gcp) are formatted, initialised from their lock files,
validated and tested against their mocked providers, with no cloud credentials, on linux/amd64,
when a change touches them and on every tag. `publish` needs the job."""

import yaml

from support.ci.ci_workflow import (
    EVENTS,
    JOBS,
    TAG,
    evaluate,
    jobs_that_run,
    needs,
    not_a_pull_request,
)

QUICK = JOBS["quick"]
ON = "needs.changes.outputs.quick != 'false'"


def test_the_job_publish_needs_runs_on_hosted_amd64_reading_the_repository_only():
    assert QUICK["timeout-minutes"] == 15
    assert "quick" in needs(JOBS["publish"])
    assert needs(QUICK) == {"changes"}
    # The hosted runner is where the stacks' `tofu test` has been seen to pass; the locks now
    # hold hashes for five platforms, so this is a choice of where it is proven, not a need.
    assert QUICK["runs-on"] == "ubuntu-latest"
    assert QUICK["permissions"] == {"contents": "read"}
    configuration = yaml.safe_dump(QUICK)
    assert "secrets." not in configuration and "github.token" not in configuration
    assert "environment" not in QUICK and "env" not in QUICK
    # No job-level condition: a skipped job would skip `publish` after it.
    assert "if" not in QUICK
    # Step-level TF_PLUGIN_CACHE_DIR + CAIRN_REQUIRE_TOFU only on the two tofu execution steps.
    for s in QUICK["steps"]:
        if "TF_PLUGIN_CACHE_DIR" in s.get("env", {}):
            assert s["env"]["TF_PLUGIN_CACHE_DIR"] == "${{ runner.temp }}/tofu-plugin-cache"
        else:
            assert "env" not in s


def test_opentofu_is_the_pinned_action_and_version_the_infra_job_uses():
    def setup(job):
        (step,) = [s for s in job["steps"] if s.get("uses", "").startswith("opentofu/")]
        return step

    assert setup(QUICK)["uses"] == setup(JOBS["infra"])["uses"]
    assert setup(QUICK)["with"] == {"tofu_version": "1.13.9", "tofu_wrapper": False}


def test_each_definition_is_formatted_initialised_read_only_validated_and_tested():
    (check,) = [s for s in QUICK["steps"] if 'tofu -chdir="$dir"' in s.get("run", "")]
    lines = [line.strip() for line in check["run"].splitlines() if line.strip()]
    assert lines == [
        'mkdir -p "$TF_PLUGIN_CACHE_DIR"',
        "for dir in infra/quick/aws infra/quick/gcp; do",
        'tofu -chdir="$dir" fmt -check -diff',
        'tofu -chdir="$dir" init -backend=false -input=false -lockfile=readonly',
        'tofu -chdir="$dir" validate -no-color',
        'tofu -chdir="$dir" test -no-color',
        "done",
    ]
    (contract,) = [s for s in QUICK["steps"] if "pytest -m opentofu" in s.get("run", "")]
    assert contract["env"]["CAIRN_REQUIRE_TOFU"] == "1"
    contract_lines = [line.strip() for line in contract["run"].splitlines() if line.strip()]
    assert contract_lines == [
        "bash -n infra/quick/bootstrap.sh",
        "shellcheck infra/quick/bootstrap.sh",
        "uv run pytest -m opentofu",
    ]
    (sync_step,) = [s for s in QUICK["steps"] if "uv sync" in s.get("run", "")]
    assert sync_step["run"] == "uv sync --locked --no-default-groups --group dev"


def test_the_checks_run_on_the_output_of_changes_and_say_so_when_they_do_not():
    steps = QUICK["steps"]
    assert "if" not in steps[0]  # the checkout
    for step in steps[1:-1]:
        assert step["if"] == ON
    assert steps[-1]["if"] == "needs.changes.outputs.quick == 'false'"
    for said, runs in (("true", True), ("false", False), ("", True), (None, True)):
        outputs = {} if said is None else {"quick": said}
        context = {"needs": {"changes": {"result": "success", "outputs": outputs}}}
        assert evaluate(ON, context) is runs, said


def test_the_job_runs_for_every_event_and_publish_waits_for_it():
    for event, context in EVENTS.items():
        assert "quick" in jobs_that_run(context), event
    assert "quick" in jobs_that_run(TAG)
    # Failed: nothing is published, nothing is released.
    for context in (TAG, not_a_pull_request("push", "refs/heads/main")):
        ran = jobs_that_run(context, failed=frozenset({"quick"}))
        assert not ran & {"publish", "verify", "package", "attest", "release", "deploy"}
