"""The real server the browser tests start, and a browser context that talks only to it.

These tests run only when asked, with `uv run pytest tests/browser --run-browser`; tests/
conftest.py keeps every other run from collecting them. Each server is `python -m
cairn.server serve` on 127.0.0.1 at a port the system picks, with a database in a
temporary folder and a key made by `key-create`, and it is stopped by its process id. The
browser is Playwright's headless Chromium. Every request a page makes must go to that
server: any other address is refused and fails the test. Nothing here sleeps for a fixed
time; every wait is for a condition.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page

INSTALL = "uv run python -m playwright install --only-shell chromium"
STARTED = re.compile(r"Uvicorn running on (http://127\.0\.0\.1:\d+)")
START_SECONDS = 30
CAIRN = (sys.executable, "-m", "cairn.server")  # one process: its id is the server's


@dataclass
class Server:
    """One running `serve`: its address, its key and what it has written to its log."""

    base: str
    key: str = field(repr=False)
    process: subprocess.Popen[str]
    log: Path

    def api(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base, headers={"Authorization": f"Bearer {self.key}"}, timeout=30
        )


def start_server(folder: Path, *extra: str) -> Server:
    """Make a key, start `serve` on a port the system picks, and wait until it answers."""
    folder.mkdir(parents=True, exist_ok=True)
    db, key_file, log = folder / "cairn.db", folder / "browser.env", folder / "serve.log"
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CAIRN_")}
    subprocess.run(
        [*CAIRN, "key-create", "--db", str(db), "--label", "browser-walk", "--out", str(key_file)],
        check=True,
        capture_output=True,
        env=environment,
    )
    key = key_file.read_text(encoding="utf-8").split("=", 1)[1].strip()
    with log.open("w", encoding="utf-8") as sink:
        process = subprocess.Popen(
            [*CAIRN, "serve", "--db", str(db), "--host", "127.0.0.1", "--port", "0", *extra],
            stdout=sink,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
        )
    deadline = time.monotonic() + START_SECONDS
    while True:
        found = STARTED.search(log.read_text(encoding="utf-8"))
        if found:
            break
        if process.poll() is not None or time.monotonic() > deadline:
            stop_server(process)
            pytest.fail("serve did not start:\n" + log.read_text(encoding="utf-8")[-2000:])
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=0.1)  # wakes early only if serve stops
    return Server(found.group(1), key, process, log)


def stop_server(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@dataclass
class Visit:
    """A browser context for one server, and every request that went elsewhere."""

    server: Server
    context: BrowserContext
    page: Page
    elsewhere: list[str] = field(default_factory=list)

    def go(self, path: str) -> None:
        self.page.goto(self.server.base + path)

    def sign_in(self) -> None:
        self.go("/login")
        self.page.get_by_label("API key").fill(self.server.key)
        self.page.get_by_role("button", name="Sign in").click()
        self.page.wait_for_url(self.server.base + "/")


def open_visit(browser: Browser, server: Server) -> Visit:
    context = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="light")
    context.set_default_timeout(15_000)
    page = context.new_page()
    visit = Visit(server, context, page)

    def only_the_server(route, request) -> None:
        if request.url == server.base or request.url.startswith(server.base + "/"):
            route.continue_()
        else:
            visit.elsewhere.append(request.url)
            route.abort()

    context.route("**/*", only_the_server)
    return visit


def close_visit(visit: Visit) -> None:
    visit.context.close()
    assert visit.elsewhere == [], f"a page asked for another address: {visit.elsewhere}"
