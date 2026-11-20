"""The versioned distribution: one version, in pyproject.toml, said the same way by the
package, the pages and the command line."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml
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
        "Each release has a section headed `## <version> - <date>`, and the tag `v<version>`,"
        " pushed to a public repository on a commit of `main`, publishes it:"
        " `scripts/release_check.py` refuses a tag that is not the version in `pyproject.toml`,"
        " whose section here is missing, has no entry or is dated after the day the check runs"
        " (UTC), or whose version already has a release. The section is the GitHub release's"
        " text." in flat(changes)
    )


WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "ci-cd.yml").read_text())
README = (ROOT / "README.md").read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_readme_says_what_a_tag_publishes_and_releases():
    ci = flat(README[README.index("## CI/CD") : README.index("## Planned")])
    for sentence in (
        "5. **Publish:** once items 1 to 4 have all passed, pushes to `main` and `v*` tags"
        " publish one multi-arch image, with provenance and an SBOM, to"
        " `ghcr.io/<owner>/<repo>`: the build items 1 and 3 checked, by its digest.",
        "A tag is published only from a public repository, when it is `v` and the version"
        " in `pyproject.toml`, `CHANGELOG.md` has a section for that version with an entry,"
        " dated no later than the day of the run, the tag's commit is on `main`, no tag of its"
        " `X.Y` is a higher version, and the version has no GitHub release yet:"
        " `scripts/release_check.py` checks it first.",
        "`X.Y.Z` never moves",
        "A push to `main` is published as `main` and `sha-<commit>`, never `latest`, and"
        " `sha-<commit>` is written once",
        "the gate runs as before on pull requests and on `main`, and items 5 to 7 are"
        " skipped; a `v*` tag's run stops in `image_build`, since `main` published no"
        " `sha-<commit>` for it.",
        "(a change to documents alone, or an earlier commit of a push)",
        "6. **Verify and deploy:** on `main` and on tags, the published image is smoke-tested."
        " In a public repository the image is first pulled with no login, as the run guide"
        " does (a tag's version, or on `main` the commit's own tag), and must"
        " be the digest just published. On `main`, job `deploy` moves the production VM to it,"
        " in the GitHub environment `production`: whether it waits for a person's approval is"
        " that environment's setting (required reviewers), which the workflow cannot see, so"
        " set it in the repository's settings for a deploy to wait",
        "7. **Release:** a tag whose published image passed becomes a GitHub release, with the"
        " version's section of `CHANGELOG.md` as its text and the wheel and source archive"
        " attached.",
        "Job `package` builds them in a job that holds no permission at all, with the build"
        " backend and its dependencies pinned by hash in `build-constraints.txt`; job"
        " `release`, the only one that may write to the repository, builds and installs"
        " nothing, checks that it got one wheel and one source archive of the tag's version,"
        " and makes the release. A tag never deploys.",
        "A tag's run builds nothing, so re-running all its jobs promotes the same digest again"
        " and finishes it, until its GitHub release exists, when `release_check` refuses the"
        " tag.",
        "On `main`, re-running every job builds again and `publish` stops, since"
        " `sha-<commit>` holds another digest: re-run only the failed jobs.",
    ):
        assert sentence in ci, sentence
    # What the deploy sentence rests on: the workflow names the environment and nothing else
    # about it, so whether a person approves is the repository's setting.
    assert WORKFLOW["jobs"]["deploy"]["environment"] == "production"
    assert "that `uv build` makes attached" not in ci
    assert "Tags are the version, `major.minor`, the branch and the commit." not in ci
    assert "A tag is published as `X.Y.Z`, `X.Y` and `latest`" not in ci


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
    assert "built by the repository's workflow from the release's tag" not in flat(
        guide_section(RUN_GUIDE, "### Or pull a released image")
    )
    for sentence in (
        "Each release of cairn is also published as an image for linux/amd64 and linux/arm64:"
        " the image the repository's workflow built and checked for the release's commit on"
        " `main`, which the release's tag publishes under its version without building it"
        " again.",
        f"A version such as `{VERSION}` names one release: it is published once and never"
        " moved to newer code.",
        "Pulling it needs no login: the workflow pulls each release this way, logged out,"
        " before it makes the release.",
        "Instead of the clone and the build above:",
        "The second line gives the pulled image the name this guide's commands use, so run"
        " them as written, starting with the `docker run` above.",
    ):
        assert sentence in image, sentence


def test_the_run_guide_says_how_to_check_what_was_pulled():
    """True of what job `publish` pushes: a multi-arch index by digest, with the provenance
    (mode=max) and the bill of materials BuildKit attaches to each platform's image. Docker's
    documentation: attestation manifests sit in the image index after the runnable manifests,
    each applying to one platform's manifest, with the platform `unknown/unknown`
    (https://docs.docker.com/build/metadata/attestations/attestation-storage/); `--raw`
    prints the index, and `--format "{{json .Provenance}}"` and `"{{json .SBOM}}"` print the
    provenance and the SBOM
    (https://docs.docker.com/reference/cli/docker/buildx/imagetools/inspect/)."""
    image = flat(guide_section(RUN_GUIDE, "### Or pull a released image"))
    for sentence in (
        "To check what you pulled: the `docker pull` above prints `Digest: sha256:` and a"
        " digest, that of the release's image index as this pull read it, which must be the"
        f" digest the repository's package page on GitHub shows for `{VERSION}`.",
        f"`docker buildx imagetools inspect --raw {IMAGE}` prints the release's image index:"
        " a manifest for each platform and, after them, attestation manifests listed with the"
        " platform `unknown/unknown`, each holding the attestations of one platform's image:"
        " the provenance of the build that made it, in the job that builds once for every"
        " change before anything is published, and its bill of materials.",
        "The same command's `--format` option prints those two themselves, as Docker's"
        " reference for `docker buildx imagetools inspect` shows.",
    ):
        assert sentence in image, sentence
    assert "beside each, an attestation" not in image
    assert "the build that published it" not in image
    # image_build makes them; `publish` pushes its OCI layout whole, by the index's digest
    # (oras copies an index with every manifest in it), so the attestation manifests survive.
    workflow = (ROOT / ".github" / "workflows" / "ci-cd.yml").read_text(encoding="utf-8")
    build = workflow.split("\n  image_build:\n", 1)[1].split("\n\n", 1)[0]
    assert "provenance: mode=max" in build and "sbom: true" in build
    assert "linux/amd64,linux/arm64" in build
    assert 'oras cp --from-oci-layout "layout@$DIGEST"' in workflow


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
