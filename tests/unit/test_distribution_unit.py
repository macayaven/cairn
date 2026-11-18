"""The versioned distribution: one version, in pyproject.toml, said the same way by the
package, the pages and the command line."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from starlette.testclient import TestClient

import cairn
from cairn.server import create_app
from cairn.store import Store

ROOT = Path(__file__).resolve().parents[2]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
BASE = "http://127.0.0.1:8765"


def test_the_version_is_0_1_0():
    assert VERSION == "0.1.0"


def test_the_package_the_pages_and_the_command_line_say_the_version(tmp_path):
    assert cairn.__version__ == VERSION
    store = Store(tmp_path / "cairn.db")
    try:
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            page = client.get("/docs").text
        footer = page[page.index('<footer class="colophon">') :]
        assert f"cairn {VERSION}</span>" in footer
    finally:
        store.close()
    said = subprocess.run(
        [sys.executable, "-m", "cairn.server", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert said.returncode == 0, said.stderr
    assert said.stdout == f"python -m cairn.server {VERSION}\n"


def test_the_changelog_dates_the_version_and_keeps_a_place_for_the_slack_entry():
    changes = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"\n## {VERSION} - 2026-11-19\n" in changes
    section = changes[changes.index(f"## {VERSION} - ") :]
    assert "<!-- 0.1.0: Slack, when it lands -->\n" in section
    assert changes.index("## Unreleased") < changes.index(f"## {VERSION} - ")
