"""Shared by every test: nothing from the developer's shell decides a result.

`serve` reads its settings from CAIRN_* variables, and some of them stop it at start when
they hold a value it cannot use (CAIRN_ALLOWED_EMAILS without --sign-in). A shell that
exports one for a real install would fail tests that have nothing to do with it. Each test
starts without any of them and sets what it needs itself.

The tests in tests/browser drive a real browser, which most people have not installed: they
are collected only with `--run-browser` (`uv run pytest tests/browser --run-browser`), so
every other run, the whole suite included, stays offline and needs no browser.
"""

import os
from pathlib import Path

import pytest

BROWSER_TESTS = Path(__file__).parent / "browser"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-browser",
        action="store_true",
        help="collect the browser tests in tests/browser (they need Playwright's Chromium)",
    )


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if collection_path == BROWSER_TESTS and not config.getoption("--run-browser"):
        return True  # a run from testpaths never walks into the folder
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """A run that names the folder or a file in it, without the flag, runs none of them."""
    if config.getoption("--run-browser"):
        return
    browser = [item for item in items if item.path.is_relative_to(BROWSER_TESTS)]
    if browser:
        config.hook.pytest_deselected(items=browser)
        items[:] = [item for item in items if not item.path.is_relative_to(BROWSER_TESTS)]


@pytest.fixture(autouse=True)
def no_cairn_settings_from_the_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.startswith("CAIRN_")]:
        monkeypatch.delenv(name)
