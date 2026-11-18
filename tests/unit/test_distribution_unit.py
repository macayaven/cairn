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


README = (ROOT / "README.md").read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_readme_says_what_a_tag_publishes_and_releases():
    ci = flat(README[README.index("## CI/CD") : README.index("## Planned")])
    for sentence in (
        "5. **Publish:** once items 1 to 4 have all passed, pushes to `main` and `v*` tags"
        " publish one multi-arch image, with provenance and an SBOM, to"
        " `ghcr.io/<owner>/<repo>`.",
        "A tag is published only when it is `v` and the version in `pyproject.toml` and"
        " `CHANGELOG.md` has a dated section for that version: `scripts/release_check.py`"
        " checks it first.",
        "A tag is published as `X.Y.Z`, `X.Y` and `latest`; a push to `main` as `main` and"
        " `sha-<commit>`, never `latest`.",
        "the gate runs as before, and items 5 to 7 are skipped.",
        "6. **Verify and deploy:** on `main` and on tags, the published image is smoke-tested."
        " On `main`, a `deploy` job that waits for approval moves the production VM to it",
        "7. **Release:** a tag whose published image passed becomes a GitHub release, with the"
        " version's section of `CHANGELOG.md` as its text and the wheel and source archive"
        " that `uv build` makes attached. A tag never deploys.",
    ):
        assert sentence in ci, sentence
    assert "Tags are the version, `major.minor`, the branch and the commit." not in ci
