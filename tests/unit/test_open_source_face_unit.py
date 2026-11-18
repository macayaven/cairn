"""The repository's face to a stranger: README.md's first steps against the files they run,
CONTRIBUTING.md and SECURITY.md, and no word of payments in the documents a self-hoster
reads. Nothing here runs Docker; the commands were run by hand against a build (see the
pull request)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
DEPLOY_GUIDE = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
RUN_GUIDE = (ROOT / "src" / "cairn" / "guides" / "run.md").read_text(encoding="utf-8")
LOCAL = yaml.safe_load((ROOT / "deploy" / "compose.local.yaml").read_text(encoding="utf-8"))

_spec = importlib.util.spec_from_file_location("rehearse_face", ROOT / "scripts/rehearse_stack.py")
rehearse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rehearse)


def section(text: str, heading: str) -> str:
    return rehearse.guide_section(text, heading)


def blocks(text: str) -> list[list[str]]:
    return [rehearse._commands(b) for b in re.findall(r"```bash\n(.*?)```", text, re.S)]


FIRST = section(README, "## Run it on your computer")


def test_the_first_section_turns_on_the_local_compose_file_and_starts_it():
    assert blocks(FIRST)[0] == [
        "git clone https://github.com/macayaven/cairn.git &&",
        "cd cairn/deploy &&",
        "echo 'COMPOSE_FILE=compose.local.yaml' > .env &&",
        "docker compose up -d --build",
    ]
    assert (ROOT / "deploy" / "compose.local.yaml").is_file()


def test_the_port_the_readme_names_is_the_one_the_file_publishes():
    published = LOCAL["services"]["cairn"]["ports"][0]
    variable, default = re.fullmatch(r"127\.0\.0\.1:\$\{(\w+):-(\d+)\}:8765", published).groups()
    assert default == "8765" and variable == "CAIRN_PORT"
    assert "http://127.0.0.1:8765" in FIRST
    assert f"echo '{variable}=18765' >> .env" in blocks(FIRST)[1]


def test_the_key_commands_are_the_deploy_guides_which_the_stack_rehearsal_runs():
    key = section(README, "### Make a key")
    ours = rehearse.guide_lines(key, "laptop.env")
    theirs = rehearse.guide_lines(DEPLOY_GUIDE, "laptop.env")
    assert ours == theirs and len(ours) == 3


def test_the_volume_the_readme_names_is_the_one_compose_makes():
    where = section(README, "### Where the data lives")
    volume = f"{LOCAL['name']}_{next(iter(LOCAL['volumes']))}"
    assert volume == "cairn_cairn-data"
    assert f"`{volume}`" in where and f"docker volume inspect {volume}" in where


def test_every_docker_command_of_the_first_section_goes_through_compose():
    for block in blocks(FIRST):
        for command in block:
            assert not command.startswith("docker ") or command.startswith("docker compose "), (
                command
            )


def test_the_readme_names_the_licence_and_it_is_mit():
    part = section(README, "## Licence")
    assert "[LICENSE](LICENSE)" in part and "MIT License" in part
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License\n")


def test_contributing_says_how_to_set_up_test_and_propose_a_change():
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for command in ("uv sync", 'uv run pytest tests/unit -o addopts="" -q -p no:cacheprovider'):
        assert command in text, command
    for rule in ("see it fail", "offline", "synthetic", "pull request"):
        assert rule in text.lower(), rule


def test_security_points_at_private_reporting_and_names_no_address():
    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "private vulnerability reporting" in text.lower()
    assert "@" not in text  # no person's address


PAYMENT_WORDS = re.compile(
    # "checkout" is left out: the guides speak of a git checkout.
    r"stripe|billing|coupon|subscription|pricing|price|payment|selling|sell|"
    r"\bbuy|purchase|paid|invoice",
    re.I,
)
# A Google Cloud project needs a billing account linked to it; that is the operator's cloud
# bill, not a payment to cairn, so the deploy guide's first-deploy step may say it.
CLOUD_BILL = re.compile(
    r"gcloud billing|billing-account|BILLING_ACCOUNT_ID|billingEnabled|"
    r"\*\*Project and billing\.\*\*"
)


@pytest.mark.parametrize(
    "name",
    [
        "README.md",
        "deploy/README.md",
        "src/cairn/guides/run.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "deploy/compose.yaml",
        "deploy/compose.local.yaml",
    ],
)
def test_no_document_a_self_hoster_reads_speaks_of_payments(name):
    text = CLOUD_BILL.sub("", (ROOT / name).read_text(encoding="utf-8"))
    found = sorted({m.group(0).lower() for m in PAYMENT_WORDS.finditer(text)})
    assert found == [], (name, found)


def test_the_run_guide_names_every_file_serve_keeps_beside_the_database():
    """Run on the image: a running `serve` keeps cairn.db, cairn.db-wal, cairn.db-shm and its
    lock, cairn.db.serve.lock, in /data; after a kill all four are left there."""
    from cairn.web import cli

    where = section(RUN_GUIDE, "## Where your data lives")
    for name in ("cairn.db-wal", "cairn.db-shm", f"cairn.db{cli.SERVE_LOCK}"):
        assert f"`{name}`" in where, name


def test_the_readme_says_mit_for_cairns_own_code_and_not_for_everything():
    for wrong in ("every file", "every part", "Every part", "Every file"):
        assert wrong not in README, wrong
    part = section(README, "## Licence")
    assert "cairn's own code" in part


def test_the_licence_section_names_the_third_party_licences_that_exist():
    part = section(README, "## Licence")
    fonts = sorted((ROOT / "src" / "cairn" / "static" / "fonts").glob("*.OFL.txt"))
    assert len(fonts) == 2
    for font in fonts:
        assert f"`{font.relative_to(ROOT).as_posix()}`" in part, font.name
    assert "certifi" in part and "MPL-2.0" in part
    assert "pywin32" in part
    assert "dist-info" in part  # where the image keeps each dependency's licence


def test_the_planned_list_names_nothing_that_is_built():
    planned = section(README, "## Planned")
    assert "graph view" not in planned  # src/cairn/graph.py, /api/graph, graph://project/...
    assert (ROOT / "src" / "cairn" / "graph.py").is_file()


def test_the_readme_says_whose_the_quoted_provider_documentation_is():
    """docs/*-api.md are notes on other services' public documentation, with quotes; the
    licence section says the quotes are not cairn's to license."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## Licence") : readme.index("## Guides")]
    assert "`docs/`" in section and "quote" in section
    assert "remain their owners'" in section
    # The quotes that stay are under licences that ask for this: the licence named and linked.
    assert "https://creativecommons.org/licenses/by/4.0/" in section
    assert "developers.google.com" in section and "GitHub Docs" in section
    # The notes' convention: Langfuse's documentation is among the sources given in our own words.
    assert "Linear's pages and Langfuse's documentation say" in section
    assert "is given in cairn's own words" in section
    notes = sorted(p.name for p in (ROOT / "docs").glob("*-api.md"))
    assert notes, "the notes the sentence is about are gone: remove the sentence"


def test_no_committed_text_names_a_private_directory_of_one_persons_machine():
    for path in ("docs/spec-v0.md", "docs/documentation-publisher.md", "README.md"):
        assert "agent-vault" not in (ROOT / path).read_text(encoding="utf-8"), path


def test_the_repository_has_what_a_newcomer_looks_for():
    """A code of conduct, templates for an issue and a pull request, and a changelog, each
    saying the thing a reader opens it for."""
    conduct = (ROOT / "CODE_OF_CONDUCT.md").read_text(encoding="utf-8")
    assert "SECURITY.md" in conduct and "@" not in conduct  # where to report; no address
    templates = ROOT / ".github" / "ISSUE_TEMPLATE"
    assert {p.name for p in templates.iterdir()} == {
        "bug_report.md",
        "feature_request.md",
        "config.yml",
    }
    bug = (templates / "bug_report.md").read_text(encoding="utf-8")
    assert bug.startswith("---\nname: ") and "python -m cairn.server" in bug
    pull = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert "uv run pytest" in pull and "uv run ruff check ." in pull
    changes = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## Unreleased" in changes
    for name in ("CODE_OF_CONDUCT.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md"):
        assert "macayaven" not in (ROOT / name).read_text(encoding="utf-8") or name == "SECURITY.md"


def test_the_repository_names_itself_where_it_lives():
    """Source installs and the publisher name the public repository."""
    run = (ROOT / "src" / "cairn" / "guides" / "run.md").read_text(encoding="utf-8")
    assert "git clone https://github.com/macayaven/cairn.git" in run
    assert "ghcr.io/macayaven/cairn:main" not in run
    publisher = (ROOT / "src" / "cairn" / "docs_publisher.py").read_text(encoding="utf-8")
    assert 'REPOSITORY = "https://github.com/macayaven/cairn"' in publisher


def test_the_first_run_builds_the_code_that_was_cloned():
    """Without --build Compose starts an image named cairn:local that an earlier checkout
    left, whatever the clone holds."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme[readme.index("## Run it on your computer") : readme.index("### Make a key")]
    assert "docker compose up -d --build\n" in start
    assert "docker compose up -d\n" not in start


def test_the_privacy_guide_says_how_many_versions_are_kept():
    """`serve` without a plans file keeps the newest KEEP_VERSIONS of each page."""
    from cairn.web import cli

    privacy = (ROOT / "src" / "cairn" / "guides" / "privacy.md").read_text(encoding="utf-8")
    assert f"the newest {cli.KEEP_VERSIONS} versions of each page" in privacy
    assert "every version of each page, or fewer" not in privacy


def test_the_landing_page_tells_a_visitor_to_build_what_they_cloned():
    """It showed a `docker run` of a published image, which nobody can pull until the image
    is public. The clone and the Compose file are what every visitor can use."""
    page = (ROOT / "src" / "cairn" / "templates" / "landing.html").read_text(encoding="utf-8")
    assert "docker run -d --name cairn" not in page and "ghcr.io" not in page
    assert "git clone https://github.com/macayaven/cairn" in page
    assert "docker compose up -d --build" in page
    assert 'href="/docs/run"' in page
    assert 'href="https://github.com/macayaven/cairn/blob/main/README.md#update"' in page


def test_local_upgrades_build_from_source_before_replacing_the_container():
    compose = section(README, "### Update")
    assert blocks(compose)[0] == [
        "(",
        "set -e",
        "git pull --ff-only origin main",
        "docker compose build cairn",
        "docker compose up -d",
        ")",
    ]
    assert "backup" in compose.lower() and "volume stays" in compose.lower()
    direct = section(RUN_GUIDE, "## Upgrade")
    assert blocks(direct)[0] == [
        "(",
        "set -e",
        "git pull --ff-only origin main",
        "docker build -t cairn:local .",
        "docker stop cairn",
        "docker rm cairn",
        ")",
    ]
    assert "backup" in direct.lower()
    assert "same `docker run` command" in direct
    assert "environment" in direct and "options" in direct
    assert "README.md#update" in direct
    start = section(RUN_GUIDE, "## Start it")
    commands = blocks(start)
    assert commands[0] == [
        "git clone https://github.com/macayaven/cairn.git &&",
        "cd cairn &&",
        "docker build -t cairn:local .",
    ]
    assert commands[1][-1].endswith("cairn:local")
    # A pull is the second way only, of a released image by its version (test_distribution_unit).
    pulls = re.findall(r"docker pull (\S+)", RUN_GUIDE)
    assert pulls and set(pulls) <= {
        "ghcr.io/macayaven/cairn:0.1.0",
        '"ghcr.io/macayaven/cairn:$VERSION"',
    }, pulls
    assert RUN_GUIDE.index("docker pull") > RUN_GUIDE.index("docker build -t cairn:local .")
    assert "wherever this guide writes" not in RUN_GUIDE


def test_a_clone_that_fails_stops_the_first_commands_before_they_touch_a_folder():
    """In a folder that already holds a `cairn`, `git clone` fails. Run one line at a time,
    the next lines would then enter that older checkout, overwrite its `.env` and build it.
    Each first-install block is one chain: a step runs only when the one before it worked,
    and the reader ends in the folder the later commands are written for."""
    landing = (ROOT / "src" / "cairn" / "templates" / "landing.html").read_text(encoding="utf-8")
    shown = re.search(r"<pre>(git clone.*?)</pre>", landing, re.S).group(1)
    shown = shown.replace("&amp;", "&").replace("&gt;", ">")
    for commands in (
        blocks(FIRST)[0],
        blocks(section(RUN_GUIDE, "## Start it"))[0],
        shown.splitlines(),
    ):
        assert commands[0].startswith("git clone ")
        assert all(line.endswith(" &&") for line in commands[:-1]), commands
        assert not commands[-1].endswith("&&")
        assert "(" not in commands[0]  # no subshell: the `cd` has to last
