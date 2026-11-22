"""Job `browser` of .github/workflows/ci-cd.yml: always runs so `publish` never accepts a
skipped `browser` job, on hosted ubuntu-latest, reading the repository only. Its Chromium steps
in `tests/browser` run whenever `changes.web` is not `false` (`src/`, `tests/browser/`,
dependencies, workflows, and every tag), and `publish` always needs its success."""

import re

import yaml

from support.ci.ci_workflow import (
    EVENTS,
    JOBS,
    TAG,
    evaluate,
    jobs_for,
    jobs_that_run,
    needs,
    not_a_pull_request,
    pull_request,
)
from support.paths import REPO_ROOT

REPO = REPO_ROOT
README = (REPO / "README.md").read_text(encoding="utf-8")
BROWSER, TEST = JOBS["browser"], JOBS["test"]
ON = "needs.changes.outputs.web != 'false'"
INSTALL = "uv run python -m playwright install --with-deps --only-shell chromium"
RUN = "uv run pytest tests/browser --run-browser"


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_job_publish_needs_runs_on_hosted_ubuntu_reading_the_repository_only():
    assert BROWSER["name"] == "browser tests (Chromium)"
    assert BROWSER["timeout-minutes"] == 15
    assert "browser" in needs(JOBS["publish"])
    assert needs(BROWSER) == {"changes"}
    # No job-level condition: a skipped job would let publish bypass browser checks.
    assert "if" not in BROWSER
    publish_if = flat(JOBS["publish"]["if"])
    assert "needs.browser.result == 'success'" in publish_if
    assert "needs.browser.result == 'skipped'" not in publish_if
    # The library installs the browser's system packages for the hosted image, so not
    # CAIRN_GATE_RUNNER, which may name another.
    assert BROWSER["runs-on"] == "ubuntu-latest"
    assert BROWSER["permissions"] == {"contents": "read"}
    configuration = yaml.safe_dump(BROWSER)
    assert "secrets." not in configuration and "github.token" not in configuration
    assert "environment" not in BROWSER and "env" not in BROWSER
    assert all("env" not in step for step in BROWSER["steps"])


def test_the_checkout_and_uv_are_the_pinned_actions_the_test_job_uses():
    assert BROWSER["steps"][0] == TEST["steps"][0]
    assert BROWSER["steps"][0]["with"] == {"persist-credentials": False}
    assert {k: v for k, v in BROWSER["steps"][1].items() if k != "if"} == TEST["steps"][1]


def test_it_installs_the_locked_environment_caches_playwright_and_runs_the_browser_tests():
    steps = BROWSER["steps"]
    assert len(steps) == 7
    assert "if" not in steps[0]
    for step in steps[1:-1]:
        assert step["if"] == ON, step.get("name")
    assert steps[-1]["name"] == "Nothing to check"
    assert steps[-1]["if"] == "needs.changes.outputs.web == 'false'"
    for said, runs in (("true", True), ("false", False), ("", True), (None, True)):
        outputs = {} if said is None else {"web": said}
        context = {"needs": {"changes": {"result": "success", "outputs": outputs}}}
        assert evaluate(ON, context) is runs, said
    runs = [" ".join(step["run"].split()) for step in steps[1:-1] if "run" in step]
    assert runs == [
        "uv sync --locked --no-default-groups --group dev --group browser",
        INSTALL,
        RUN,
    ]
    cache = steps[3]
    assert cache["uses"].startswith("actions/cache@")
    assert cache["with"]["path"] == "~/.cache/ms-playwright"


def test_the_browser_and_the_option_are_the_ones_the_readme_gives_a_person():
    (said_install,) = re.findall(r"^uv run python -m playwright install .+$", README, re.M)
    (said_run,) = re.findall(r"^uv run pytest tests/browser.*$", README, re.M)
    # The job adds only --with-deps: the system packages a fresh runner lacks.
    assert INSTALL.replace(" --with-deps", "") == said_install
    assert said_run == RUN


def test_the_job_runs_for_every_event_and_publish_waits_for_it():
    for event, context in EVENTS.items():
        assert "browser" in jobs_that_run(context), event
    assert "browser" in jobs_that_run(TAG)
    assert "browser" in jobs_that_run(pull_request(draft=True))
    assert "browser" in jobs_that_run(pull_request(draft=False))
    # Even when web == 'false' (docs-only change), the job itself runs ("Nothing to check")
    # so `publish` always requires `needs.browser.result == 'success'`.
    for paths in (["src/cairn/server.py"], ["docs/spec-v1.md"]):
        assert "browser" in jobs_for("a push to a pull request that is ready", paths)
    # Every push to main that builds an image (any src/ change) triggers Chromium tests too,
    # and `publish` never accepts a skipped `browser`.
    ran = jobs_for("a push to main", ["src/cairn/store/grants.py"])
    assert {"browser", "publish", "verify", "deploy"} <= ran
    for context in (TAG, not_a_pull_request("push", "refs/heads/main")):
        ran = jobs_that_run(context, failed=frozenset({"browser"}))
        assert not ran & {"publish", "verify", "package", "attest", "release", "deploy"}


def test_the_readme_says_when_browser_runs_in_the_gate():
    ci = flat(README[README.index("## CI/CD") : README.index("1. **")])
    assert (
        "Job `browser` runs on every push and pull request (its Chromium steps in"
        " `tests/browser` run whenever `changes.web` is not `false`: any change to `src/`,"
        " `tests/browser/`, dependencies, or workflows, and on every tag), and `publish`"
        " always needs it." in ci
    )


def test_the_hosting_guide_says_which_three_jobs_hold_an_identity_token_and_why():
    guide = flat((REPO / "infra" / "gcp" / "README.md").read_text(encoding="utf-8"))
    holders = {name for name, job in JOBS.items() if "id-token" in job.get("permissions", {})}
    assert holders == {"deploy", "attest", "attest_main"}
    assert "`id-token: write` on two jobs only" not in guide
    assert (
        "`id-token: write` on three jobs only: `deploy`, which uses it to sign in to Google Cloud"
        " and runs nothing but pinned actions, shell steps written in the workflow and the"
        " deploy script; `attest`, on a tag, which uses it to sign the release's build"
        " provenance; and `attest_main`, on `main`, which uses it to sign the published image's"
        " digest for its commit. The two attest jobs run no code from the repository" in guide
    )
