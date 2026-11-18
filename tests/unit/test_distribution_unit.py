"""The versioned distribution: one version, in pyproject.toml, said the same way by the
package, the pages and the command line."""

from __future__ import annotations

import re
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


def test_the_changelog_dates_the_version_and_says_what_slack_was_tested_against():
    changes = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"\n## {VERSION} - 2026-11-19\n" in changes
    section = changes[changes.index(f"## {VERSION} - ") :]
    slack = next(line for line in section.splitlines() if line.startswith("- **From Slack.**"))
    assert "Off unless switched on with `serve --slack`." in slack
    assert slack.endswith(
        "It is tested against a stand-in for Slack and has not been run against Slack itself."
    )
    assert "<!--" not in section
    assert changes.index("## Unreleased") < changes.index(f"## {VERSION} - ")
    # What the top of the changelog says about releases is what release_check.py does.
    assert (
        "Each release has a section headed `## <version> - <date>`, and the tag `v<version>`"
        " publishes it: `scripts/release_check.py` refuses a tag that is not the version in"
        " `pyproject.toml` or has no dated section here with at least one entry, and the"
        " section is the GitHub release's text." in flat(changes)
    )


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


# --- the guides offer the released image beside the source build ----------------------------

RUN_GUIDE = (ROOT / "src" / "cairn" / "guides" / "run.md").read_text(encoding="utf-8")
IMAGE = f"ghcr.io/macayaven/cairn:{VERSION}"


def guide_section(text: str, heading: str) -> str:
    """From HEADING to the next heading of its level or higher."""
    level = len(heading.split(" ", 1)[0])
    start = text.index(f"\n{heading}\n") + 1
    rest = text[start + len(heading) :]
    for match in re.finditer(r"^(#+) ", rest, re.M):
        if len(match.group(1)) <= level:
            return text[start : start + len(heading) + match.start()]
    return text[start:]


def bash_blocks(text: str) -> list[list[str]]:
    return [
        [line.strip() for line in block.strip().splitlines()]
        for block in re.findall(r"```bash\n(.*?)```", text, re.S)
    ]


def test_every_image_the_guides_name_is_the_version_in_pyproject():
    """A version tag does not move; the guides name the one this commit is, so they cannot
    drift from pyproject.toml. No guide offers `latest` or `main`."""
    for name, text in (("run.md", RUN_GUIDE), ("README.md", README)):
        named = re.findall(r"ghcr\.io/macayaven/cairn:([^\s`\"']+)", text)
        assert named, name
        assert set(named) <= {VERSION, "$VERSION"}, (name, named)
        for value in re.findall(r"^\s*VERSION=(\S+)$", text, re.M):
            assert value == VERSION, (name, value)


def test_the_run_guide_builds_the_source_first_and_offers_the_image_second():
    start = guide_section(RUN_GUIDE, "## Start it")
    assert start.index("git clone https://github.com/macayaven/cairn.git") < start.index(IMAGE)
    image = flat(guide_section(RUN_GUIDE, "### Or pull a released image"))
    assert bash_blocks(guide_section(RUN_GUIDE, "### Or pull a released image"))[0] == [
        f"docker pull {IMAGE} &&",
        f"docker tag {IMAGE} cairn:local",
    ]
    for sentence in (
        "Each release of cairn is also published as an image for linux/amd64 and linux/arm64,"
        " built by the repository's workflow from the release's tag.",
        f"A version such as `{VERSION}` names one release, and is never moved to newer code.",
        "Instead of the clone and the build above:",
        "The second line gives the pulled image the name this guide's commands use, so run"
        " them as written, starting with the `docker run` above.",
    ):
        assert sentence in image, sentence


def test_the_run_guide_says_how_to_check_what_was_pulled():
    """True of what job `publish` pushes: a multi-arch index by digest, with the provenance
    (mode=max) and the bill of materials BuildKit attaches beside each platform's image."""
    image = flat(guide_section(RUN_GUIDE, "### Or pull a released image"))
    for sentence in (
        f"`docker inspect --format '{{{{index .RepoDigests 0}}}}' {IMAGE}` prints the digest"
        " you pulled; it must be the digest the repository's package page on GitHub shows"
        f" for `{VERSION}`.",
        f"`docker buildx imagetools inspect {IMAGE}` lists the image for each platform and,"
        " beside each, an attestation that holds the provenance of the build that published"
        " it and its bill of materials.",
    ):
        assert sentence in image, sentence
    publish = (ROOT / ".github" / "workflows" / "ci-cd.yml").read_text(encoding="utf-8")
    assert "provenance: mode=max" in publish and "sbom: true" in publish
    assert "linux/amd64,linux/arm64" in publish


def test_the_run_guide_upgrades_a_pulled_image_after_the_same_cold_backup():
    upgrade = guide_section(RUN_GUIDE, "## Upgrade")
    blocks = bash_blocks(upgrade)
    assert blocks[1] == [
        "(",
        "set -e",
        f"VERSION={VERSION}",
        'docker pull "ghcr.io/macayaven/cairn:$VERSION"',
        'docker tag "ghcr.io/macayaven/cairn:$VERSION" cairn:local',
        "docker stop cairn",
        "docker rm cairn",
        ")",
    ]
    said = flat(upgrade)
    for sentence in (
        "With a pulled image, take the same [cold database backup](#back-it-up) and note the"
        " running image, then pull the next version and replace the container.",
        f"Set `VERSION` to the release you move to, in place of `{VERSION}`:",
    ):
        assert sentence in said, sentence
    # The source path stays first.
    assert blocks[0][2] == "git pull --ff-only origin main"


def test_the_readme_names_the_released_image_beside_the_source_build():
    part = flat(guide_section(README, "## Options of `serve` in a container"))
    assert (
        "Each release is also published as an image, such as"
        f" `{IMAGE}`; the run guide says how to pull it and check what you pulled." in part
    )
