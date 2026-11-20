"""Job `release` checks the GitHub CLI of its own runner before it does anything else (fifth
report, "remaining suspicion").

What the job does with a draft (gh makes one and takes none back; gh finds one by its tag) was
read in gh's source at v2.102.0, and in no earlier version. `image_build` refuses a gh before
2.102.0, but on the gate's runner (`CAIRN_GATE_RUNNER`); `release` runs on the runner
`CAIRN_RELEASE_RUNNER` names, which has a gh of its own. So the job's first step reads the
version as `image_build` does and refuses an earlier one in one line, before the files are
fetched and before any request: it holds no token and asks gh for nothing else.

gh is a stand-in that prints what GH_VERSION holds and keeps each call."""

import pytest
from ci_shell import SYSTEM, run_step
from test_ci_release_retry_unit import README, cells
from test_ci_workflow_unit import JOBS, RAW_WORKFLOW, REPO, reaches_the_token

RELEASE = JOBS["release"]
CHECK = RELEASE["steps"][0]
GH = """#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
[ "$1" = --version ] || exit 2
printf '%s\\n' "$GH_VERSION"
[ -z "$GH_VERSION" ] || echo "https://github.com/cli/cli/releases/tag/v0"
"""
OLD = (
    "::error::gh on this runner is {said}, before 2.102.0, the version in whose source this"
    " job's handling of a draft was read, so it is not trusted with the release; update GitHub"
    " CLI on the runner CAIRN_RELEASE_RUNNER names, or unset it to use GitHub's"
)
NONE = (
    "::error::gh is not on this runner, so the GitHub release for v1.2.3 cannot be made;"
    " install GitHub CLI 2.102.0 or later on the runner CAIRN_RELEASE_RUNNER names, or unset it"
    " to use GitHub's"
)


def check(tmp_path, steps=(CHECK,), **env: str):
    """STEPS of the job in their order, as the runner runs them, until one fails: the last
    one's result, the calls gh got and the `::error::` lines. With no GH_VERSION the runner
    has no gh, and a PATH is given that holds nothing at all."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    if "GH_VERSION" in env:
        (bin_ / "gh").write_text(GH)
        (bin_ / "gh").chmod(0o755)
    calls = tmp_path / "calls"
    calls.write_text("")
    for step in steps:
        done = run_step(
            step["run"],
            cwd=tmp_path,
            env={
                "PATH": f"{bin_}:{SYSTEM}",
                "RUNNER_TEMP": str(tmp_path),
                "GITHUB_REF_NAME": "v1.2.3",
                "GITHUB_RUN_ID": "4242",
                "GH_REPO": "owner/repo",
                "CALLS": str(calls),
                **env,
            },
        )
        if done.returncode:
            break
    errors = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    return done, calls.read_text().splitlines(), errors


def test_the_check_is_the_job_s_first_step_and_holds_no_token():
    assert [step["name"] for step in RELEASE["steps"]] == [
        "Check the GitHub CLI",
        "Fetch what package built",
        "Check the files",
        "Create the release",
    ]
    assert set(CHECK) == {"name", "run"}  # no env, no condition, no shell of its own
    assert not reaches_the_token(CHECK)
    assert "CAIRN_RELEASE_RUNNER" in RELEASE["runs-on"]


@pytest.mark.parametrize(
    "version", ["2.102.0", "2.102.1", "2.105.0", "2.110.12", "3.0.0", "10.0.0"]
)
def test_a_gh_at_or_after_2_102_0_passes_and_is_asked_for_nothing_else(tmp_path, version):
    done, calls, errors = check(tmp_path, GH_VERSION=f"gh version {version} (2026-11-01)")
    assert (done.returncode, errors) == (0, []), done.stdout + done.stderr
    assert calls == ["--version"]


@pytest.mark.parametrize(
    ("printed", "said"),
    [
        ("gh version 2.101.0 (2026-09-15)", "2.101.0"),
        ("gh version 2.101.99 (2026-09-15)", "2.101.99"),
        ("gh version 2.51.3 (2024-06-01)", "2.51.3"),
        ("gh version 2.9.0 (2022-04-01)", "2.9.0"),
        ("gh version 1.999.0 (2021-01-01)", "1.999.0"),
        ("gh version (devel)", "of a version it does not print"),
        ("version 2.102.0", "of a version it does not print"),
        ("", "of a version it does not print"),
    ],
)
def test_a_gh_before_2_102_0_is_refused_in_one_line_and_asked_for_nothing_else(
    tmp_path, printed, said
):
    done, calls, errors = check(tmp_path, GH_VERSION=printed)
    assert done.returncode == 1
    assert calls == ["--version"]
    assert errors == [OLD.format(said=said)] and ". " not in errors[0]


def test_a_runner_with_no_gh_is_refused_in_one_line(tmp_path):
    """CAIRN_RELEASE_RUNNER may name a runner of the operator's own, which need not have gh.
    The step's PATH holds nothing at all, since GitHub's Ubuntu image, where this test also
    runs, has gh in /usr/bin."""
    done, calls, errors = check(tmp_path, PATH=str(tmp_path / "bin"))
    assert done.returncode == 1 and calls == []
    assert errors == [NONE] and ". " not in errors[0]


def test_an_old_gh_stops_the_job_before_it_reads_a_file_or_sends_a_request(tmp_path):
    """The job's shell steps in their order, as a runner runs them: with an old gh the first
    stops the job, so the files are not read and gh is asked for its version and nothing
    else (no listing, no creation, no publication)."""
    steps = [step for step in RELEASE["steps"] if "run" in step]
    assert steps[0] is CHECK and len(steps) == 3
    done, calls, errors = check(tmp_path, steps, GH_VERSION="gh version 2.101.0 (2026-09-15)")
    assert done.returncode == 1 and calls == ["--version"]
    assert errors == [OLD.format(said="2.101.0")]
    assert not (tmp_path / "body.md").exists()


def test_the_version_is_read_and_compared_as_image_build_reads_it():
    """The same refusal: the line that reads the version and the comparison are, word for
    word, the ones of `image_build`'s step, which test_ci_one_build_unit runs."""
    gate = next(
        s for s in JOBS["image_build"]["steps"] if s.get("name", "").startswith("Check that main")
    )
    theirs = [line.strip() for line in gate["run"].splitlines()]
    ours = [line.strip() for line in CHECK["run"].splitlines()]
    for line in (
        "version=\"$(gh --version | sed -n 's/^gh version"
        " \\([0-9][0-9]*\\.[0-9][0-9]*\\.[0-9][0-9]*\\).*/\\1/p')\"",
        'IFS=. read -r major minor _ <<< "${version:-0.0.0}"',
        'if [ "$major" -lt 2 ] || { [ "$major" = 2 ] && [ "$minor" -lt 102 ]; }; then',
    ):
        assert ours.count(line) == 1 and theirs.count(line) == 1, line


def test_the_header_the_readme_and_the_two_rows_say_what_the_step_does():
    said = (
        " tag) was read in that version's source and in no earlier one. Its first step refuses"
        " an earlier gh, before it fetches or writes anything."
    )
    header = " ".join(RAW_WORKFLOW.split("\nname:")[0].replace("# ", "").split())
    assert header.count(said) == 1
    assert " ".join(README.replace("`", "").split()).count(said) == 1
    assert cells("gh is not on this runner, so the GitHub release for … cannot be made") == (
        "The release's runner has no GitHub CLI; nothing was fetched or written",
        "Install `gh` 2.102.0 or later on the runner `CAIRN_RELEASE_RUNNER` names, or unset it,"
        " then re-run the job.",
    )
    assert cells(
        "gh on this runner is …, before 2.102.0, the version in whose source this job's handling"
        " of a draft was read, so it is not trusted with the release"
    ) == (
        "The runner's `gh` is older than the one the job was read against; nothing was fetched"
        " or written",
        "Update `gh` on the runner `CAIRN_RELEASE_RUNNER` names, or unset it, then re-run the job.",
    )
    assert (REPO / "tests" / "unit" / "test_ci_one_build_unit.py").is_file()
