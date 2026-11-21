"""Fixtures for the browser tests: a real server and a real browser.

These tests run only when asked, with `uv run pytest tests/browser --run-browser`; tests/
conftest.py keeps every other run from collecting them. browser_server.py says how a server
is started and stopped, and how a page is kept to it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
from browser_server import (
    INSTALL,
    Server,
    Visit,
    close_visit,
    open_visit,
    start_server,
    stop_server,
)

if TYPE_CHECKING:
    from axe_playwright_python.sync_playwright import Axe
    from playwright.sync_api import Browser, Playwright


@pytest.fixture(scope="session")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    """The server as it ships: no flag beyond the address."""
    running = start_server(tmp_path_factory.mktemp("serve"))
    yield running
    stop_server(running.process)


@pytest.fixture(scope="session")
def explorer_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    """The server with the graph's interactive view, `--graph-explorer`."""
    running = start_server(tmp_path_factory.mktemp("explorer"), "--graph-explorer")
    yield running
    stop_server(running.process)


@pytest.fixture(scope="session")
def playwright() -> Iterator[Playwright]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as driver:
        yield driver


@pytest.fixture(scope="session")
def browser(playwright: Playwright) -> Iterator[Browser]:
    try:
        launched = playwright.chromium.launch(headless=True)
    except Exception as error:  # Playwright raises its own Error for a missing browser
        if "Executable doesn't exist" in str(error):
            pytest.fail(f"Chromium is not installed for Playwright. Run: {INSTALL}", pytrace=False)
        raise
    yield launched
    launched.close()


@pytest.fixture
def visit(browser: Browser, server: Server) -> Iterator[Visit]:
    opened = open_visit(browser, server)
    yield opened
    close_visit(opened)


@pytest.fixture
def explorer_visit(browser: Browser, explorer_server: Server) -> Iterator[Visit]:
    opened = open_visit(browser, explorer_server)
    yield opened
    close_visit(opened)


@pytest.fixture(scope="session")
def axe() -> Axe:
    """axe-core from the pinned package's own copy, put into the page; never fetched."""
    from axe_playwright_python.sync_playwright import Axe

    return Axe()
