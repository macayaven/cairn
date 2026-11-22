"""Job `browser` of .github/workflows/ci-cd.yml: the browser tests in tests/browser run in
headless Chromium whenever `changes.web` is not `false` (any change to web UI, templates,
static files, dependencies, or workflow/browser files), on hosted ubuntu-latest, reading
the repository only. `publish` needs the job whenever `web` is true."""

import re

import yaml

from support.ci.ci_workflow import (
    JOBS,
    TAG,
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
INSTALL = "uv run python -m playwright install --with-deps --only-shell chromium"
RUN = "uv run pytest tests/browser --run-browser"


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_job_publish_needs_runs_on_hosted_ubuntu_reading_the_repository_only():
    assert BROWSER["name"] == "browser tests (Chromium)"
    assert BROWSER["timeout-minutes"] == 15
    assert "browser" in needs(JOBS["publish"])
    assert needs(BROWSER) == {"changes"}
    assert BROWSER["if"] == "needs.changes.outputs.web != 'false'"
    # The library installs the browser's system packages for the hosted image, so not
    # CAIRN_GATE_RUNNER, which may name another.
    assert BROWSER["runs-on"] == "ubuntu-latest"
    assert BROWSER["permissions"] == {"contents": "read"}
    configuration = yaml.safe_dump(BROWSER)
    assert "secrets." not in configuration and "github.token" not in configuration
    assert "environment" not in BROWSER and "env" not in BROWSER
    assert all("if" not in step and "env" not in step for step in BROWSER["steps"])


def test_the_checkout_and_uv_are_the_pinned_actions_the_test_job_uses():
    assert BROWSER["steps"][:2] == TEST["steps"][:2]
    assert BROWSER["steps"][0]["with"] == {"persist-credentials": False}


def test_it_installs_the_locked_environment_caches_playwright_and_runs_the_browser_tests():
    runs = [" ".join(step["run"].split()) for step in BROWSER["steps"] if "run" in step]
    assert runs == [
        "uv sync --locked --no-default-groups --group dev --group browser",
        INSTALL,
        RUN,
    ]
    assert len(BROWSER["steps"]) == 6
    cache = BROWSER["steps"][3]
    assert cache["uses"].startswith("actions/cache@")
    assert cache["with"]["path"] == "~/.cache/ms-playwright"


def test_the_browser_and_the_option_are_the_ones_the_readme_gives_a_person():
    (said_install,) = re.findall(r"^uv run python -m playwright install .+$", README, re.M)
    (said_run,) = re.findall(r"^uv run pytest tests/browser.*$", README, re.M)
    # The job adds only --with-deps: the system packages a fresh runner lacks.
    assert INSTALL.replace(" --with-deps", "") == said_install
    assert said_run == RUN


def test_the_job_runs_for_web_and_unknown_events_and_skips_cleanly_for_non_web_changes():
    assert "browser" in jobs_that_run(pull_request(draft=True))
    assert "browser" in jobs_that_run(pull_request(draft=False))
    assert "browser" in jobs_for("a push to a pull request that is ready", ["src/cairn/web/app.py"])
    assert "browser" not in jobs_for(
        "a push to a pull request that is ready", ["src/cairn/store.py"]
    )
    assert "browser" in jobs_for("a manual run", ["src/cairn/store.py"])
    assert "browser" in jobs_for("a v* tag", ["src/cairn/store.py"])
    # Skipping `browser` when web == 'false' must not block `publish` or `deploy` on main.
    ran = jobs_for("a push to main", ["src/cairn/store.py"])
    assert "browser" not in ran and {"publish", "verify", "deploy"} <= ran
    # Failing `browser` always blocks release/publish/deploy.
    for context in (TAG, not_a_pull_request("push", "refs/heads/main")):
        ran = jobs_that_run(context, failed=frozenset({"browser"}))
        assert not ran & {"publish", "verify", "package", "attest", "release", "deploy"}


def test_the_readme_says_when_browser_runs_in_the_gate():
    ci = flat(README[README.index("## CI/CD") : README.index("1. **")])
    assert (
        "Job `browser` runs headless Chromium tests (`tests/browser`) whenever `changes.web`"
        " is not `false` (any change to web UI, templates, static files, dependencies, or"
        " workflow/browser files), and `publish` needs it whenever `web` is true." in ci
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
