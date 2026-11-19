"""Job `browser` of .github/workflows/ci-cd.yml: the browser tests in tests/browser run in
headless Chromium on every pull request and every push, with no path condition (a page can
break from a template, a style sheet, a route, a dependency or a test), on hosted
ubuntu-latest, reading the repository only. `publish` needs the job. The browser and the
option are the ones README.md gives a person, so the two cannot drift."""

import re
from pathlib import Path

import yaml
from test_ci_workflow_unit import (
    EVENTS,
    JOBS,
    TAG,
    jobs_that_run,
    needs,
    not_a_pull_request,
)

REPO = Path(__file__).resolve().parents[2]
README = (REPO / "README.md").read_text(encoding="utf-8")
BROWSER, TEST = JOBS["browser"], JOBS["test"]
INSTALL = "uv run python -m playwright install --with-deps --only-shell chromium"
RUN = "uv run pytest tests/browser --run-browser"


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_job_publish_needs_runs_on_hosted_ubuntu_reading_the_repository_only():
    assert BROWSER["name"] == "browser tests (Chromium)"
    assert "browser" in needs(JOBS["publish"])
    assert needs(BROWSER) == set()
    # The library installs the browser's system packages for the hosted image, so not
    # CAIRN_GATE_RUNNER, which may name another.
    assert BROWSER["runs-on"] == "ubuntu-latest"
    assert BROWSER["permissions"] == {"contents": "read"}
    configuration = yaml.safe_dump(BROWSER)
    assert "secrets." not in configuration and "github.token" not in configuration
    assert "environment" not in BROWSER and "env" not in BROWSER
    # No condition at all: a skipped job would skip `publish` after it.
    assert "if" not in BROWSER
    assert all("if" not in step and "env" not in step for step in BROWSER["steps"])


def test_the_checkout_and_uv_are_the_pinned_actions_the_test_job_uses():
    assert BROWSER["steps"][:2] == TEST["steps"][:2]
    assert BROWSER["steps"][0]["with"] == {"persist-credentials": False}


def test_it_installs_the_locked_environment_and_chromium_then_runs_the_browser_tests():
    runs = [" ".join(step["run"].split()) for step in BROWSER["steps"][2:]]
    assert runs == ["uv sync --locked", INSTALL, RUN]
    assert len(BROWSER["steps"]) == 5


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
    for context in (TAG, not_a_pull_request("push", "refs/heads/main")):
        ran = jobs_that_run(context, failed=frozenset({"browser"}))
        assert not ran & {"publish", "verify", "package", "attest", "release", "deploy"}


def test_the_readme_says_what_the_job_runs_where_it_lists_the_gate():
    ci = flat(README[README.index("## CI/CD") : README.index("## Planned")])
    item_1 = ci.split("1. **")[1].split("2. **")[0]
    assert (
        "Job `browser` runs the browser tests (`tests/browser`) in headless Chromium on every"
        " change, and `publish` needs it." in item_1
    )


def test_the_hosting_guide_says_which_two_jobs_hold_an_identity_token_and_why():
    guide = flat((REPO / "infra" / "gcp" / "README.md").read_text(encoding="utf-8"))
    holders = {name for name, job in JOBS.items() if "id-token" in job.get("permissions", {})}
    assert holders == {"deploy", "attest"}
    assert "`id-token: write` on the `deploy` job only" not in guide
    assert (
        "`id-token: write` on two jobs only: `deploy`, which uses it to sign in to Google Cloud"
        " and runs nothing but pinned actions, two shell steps written in the workflow and the"
        " deploy script, and `attest`, on a tag, which uses it to sign the release's build"
        " provenance and runs no code from the repository" in guide
    )
