"""The browser tests in tests/browser run only when asked, with `--run-browser`.

`uv run pytest` and the whole-suite command (`uv run pytest -o addopts="" -q -p
no:cacheprovider -rfE --ignore=.claude`) collect none of them, so a person without the
browser installed runs the suite as before, offline. Each check here runs pytest itself, as
those commands do, collecting only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
DEFAULT: tuple[str, ...] = ()
WHOLE_SUITE = ("-o", "addopts=", "-q", "-p", "no:cacheprovider", "-rfE", "--ignore=.claude")


def collect(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )


def everything_but_the_browser_and_the_smoke_test() -> list[str]:
    """--ignore for every other folder and file under tests/, so a collection from the
    configured `testpaths` stays short and still walks into tests/browser."""
    keep = {"browser", "conftest.py", "test_smoke.py"}
    return [f"--ignore={child}" for child in sorted(TESTS.iterdir()) if child.name not in keep]


def test_neither_command_collects_the_browser_tests_from_testpaths():
    ignores = everything_but_the_browser_and_the_smoke_test()
    for command in (DEFAULT, WHOLE_SUITE):
        run = collect(*command, *ignores)
        assert run.returncode == 0, (command, run.stdout[-2000:], run.stderr[-2000:])
        assert "tests/test_smoke.py" in run.stdout, command  # it collected from testpaths
        assert "tests/browser" not in run.stdout, (command, run.stdout[-2000:])


def test_naming_the_folder_without_the_flag_collects_none_of_them():
    for command in (DEFAULT, WHOLE_SUITE):
        run = collect(*command, "tests/browser")
        assert "tests/browser/" not in run.stdout, (command, run.stdout[-2000:])
        assert run.returncode == 5, (command, run.returncode, run.stdout[-2000:])  # none


def test_the_flag_collects_them_without_starting_a_browser():
    run = collect("tests/browser", "--run-browser")
    assert run.returncode == 0, (run.stdout[-2000:], run.stderr[-2000:])
    assert "tests/browser/test_browser_walk.py" in run.stdout
