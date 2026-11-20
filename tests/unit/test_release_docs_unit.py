"""What the documents a first reader meets say about a numbered release, pinned to the code
so they cannot go false unnoticed: the security policy, the README's first screen, the run
guide's first section, and the sentences that say where a promise stops (records the law
protects; text kept in the log file beside a continuous backup; the order of passages on a
page a model arranged; one server for one database).

Guide sentences are checked as a person reads them (`says`: in that section, once)."""

import re
import tomllib
from pathlib import Path

import yaml
from test_guide_facts_slack_unit import flat, guide_source, says, section

from cairn.web import cli

ROOT = Path(__file__).resolve().parents[2]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
IMAGE = f"ghcr.io/macayaven/cairn:{VERSION}"
RULES = ("HIPAA", "FERPA", "COPPA")


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def part(text: str, heading: str) -> str:
    """The text under one `## ` heading of a root document, as one line."""
    body = text.split(f"\n{heading}\n", 1)[1]
    return " ".join(re.split(r"\n## ", body, maxsplit=1)[0].split())


def workflow_step(name: str) -> str:
    """The workflow from the step called NAME to the next step."""
    rest = read(".github/workflows/ci-cd.yml").split(f"- name: {name}\n", 1)[1]
    return rest.split("\n      - name: ", 1)[0]


def test_the_security_policy_says_which_versions_get_fixes_as_the_workflow_tags_them():
    """SECURITY.md: the newest release gets fixes; a full version never moves; the two-part
    tag and `latest` do."""
    fixes = part(read("SECURITY.md"), "## Which versions get fixes")
    assert "no numbered release" not in fixes
    assert "The newest release gets fixes." in fixes
    minor = ".".join(VERSION.split(".")[:2])
    assert f"`{VERSION}`" in fixes and f"`{minor}`" in fixes and "`latest`" in fixes
    assert "never moves" in fixes
    # What the sentences rest on. `publish` pushes the full version, and only when the registry
    # does not have it: there already as the same build it is carried on, as another it stops,
    # so a re-run never moves it (tests/unit/test_ci_promote_unit.py runs both).
    tags = workflow_step("Image tags")
    assert "type=semver,pattern={{version}}" in tags
    assert "{{major}}.{{minor}}" not in tags and "latest" not in tags.split("tags: |")[1]
    decide = workflow_step("Look for this build in the registry")
    assert 'grep -Fxq "$first"' in decide and "a full version is published once" in decide
    # Job `aliases` moves X.Y to the newest X.Y.z and latest to the highest release.
    aliases = workflow_step("Point latest and each X.Y at the highest release")
    assert "point latest " in aliases and 'point "$line" ' in aliases
    # They move after the release is made, not with it: the interval is said.
    assert (
        "The tag `0.1` moves to each newer `0.1.x`, and `latest` names the highest release,"
        " each once job `aliases` has run after the release is made: until then they name"
        " what they named before, nothing before the first release." in fixes
    )
    workflow = yaml.safe_load(read(".github/workflows/ci-cd.yml"))
    assert "release" in workflow["jobs"]["aliases"]["needs"]


def test_the_policy_and_the_readme_say_it_is_not_assessed_for_protected_records():
    """SECURITY.md and README.md name the three rules and send the reader to the privacy
    guide's section, which exists under that heading."""
    for name in ("SECURITY.md", "README.md"):
        text = read(name)
        said = next(p for p in text.split("\n\n") if all(rule in p for rule in RULES))
        assert "has not been assessed" in said, name
        assert "docs/guides/privacy.md#records-the-law-protects" in said, name
    assert "## Records the law protects" in guide_source("privacy")


def test_the_privacy_guide_says_what_is_missing_for_protected_records_and_names_real_options():
    """privacy.md, "Records the law protects": what an operator would have to add, each
    switch it names being one `serve` has."""
    text = flat(section("privacy", "## Records the law protects"))
    for rule in RULES:
        assert rule in text
    for gap in (
        "has not been assessed",
        "does not encrypt the database file itself",
        "can read every project",
        "no record of the person a note is about",
        "no consent",
        "no legal hold",
    ):
        assert gap in text, gap
    options = {
        option
        for action in cli._parser()._subparsers._group_actions[0].choices["serve"]._actions
        for option in action.option_strings
    }
    for switch in ("--audit-reads", "--require-mfa", "--idle-minutes"):
        assert f"`{switch}`" in text and switch in options, switch


def test_the_readme_opens_on_a_release_and_offers_its_image_beside_the_source_build():
    """README.md: no "Pre-alpha"; the released image of this version, pulled with Compose,
    stands beside the source build; Docker's own install page is linked by the version it
    asks for."""
    text = read("README.md")
    opening = text.split("\n## ", 1)[0]
    assert "Pre-alpha" not in text
    assert f"Version {VERSION}" in opening
    start = text.split("\n## Run it on your computer\n", 1)[1].split("\n### Make a key", 1)[0]
    assert IMAGE in start
    assert "docker compose pull cairn" in start and "docker compose up -d --no-build" in start
    assert "docker compose up -d --build" in start  # the source build stays
    assert "https://docs.docker.com/engine/install/" in start
    assert start.index(IMAGE) < start.index("--build")  # the release comes first


def test_the_compose_file_takes_the_image_the_readme_sets():
    """What the README's released path rests on: the Compose file reads CAIRN_IMAGE."""
    assert "image: ${CAIRN_IMAGE:-cairn:local}" in read("deploy/compose.local.yaml")


def test_the_run_guide_points_at_the_released_image_before_its_first_command():
    """run.md, "Start it": the pinned image is named before the commands of the source
    build, which still says it follows the main branch."""
    start = section("run", "## Start it")
    before = start.split("```", 1)[0]
    assert "[pull a released image](#or-pull-a-released-image)" in before
    assert "main branch" in before and "not a pinned release" in before


def test_the_security_guide_points_up_to_the_sections_above_it():
    """security.md: the operator's section links two sections that stand above it."""
    source = guide_source("security")
    link = "[above](#google-accounts-connected-in-the-browser), [Slack](#slack)"
    assert link in source
    assert source.index("## Google accounts connected in the browser") < source.index(link)


def test_the_security_guide_says_the_order_of_passages_is_not_checked():
    """security.md: "The checks take each claim by itself. They do not check that two
    passages that stood next to each other in a note are next to each other on a page a model
    arranged, so a sentence that leans on the one before it can stand apart from it. Each
    claim still opens its own lines in its note, where the order is the writer's." """
    says(
        "security",
        "## What the checks on pages catch",
        "The checks take each claim by itself. They do not check that two passages that stood"
        " next to each other in a note are next to each other on a page a model arranged, so a"
        " sentence that leans on the one before it can stand apart from it. Each claim still"
        " opens its own lines in its note, where the order is the writer's.",
    )


def test_removing_slack_says_where_the_deleted_secrets_can_stay():
    """connect.md: "Where this install keeps a continuous backup, the deleted secrets, still
    sealed, can stay in the database's log file until the operator compacts it, and in backup
    copies until they expire." """
    says(
        "connect",
        "## From Slack",
        "Where this install keeps a continuous backup, the deleted secrets, still sealed, can"
        " stay in the database's log file until the operator compacts it, and in backup copies"
        " until they expire.",
    )


def test_compact_says_what_a_continuous_backup_keeps():
    """run.md: "Beside a continuous backup the replica service holds the log file, so what was
    removed can stay in it until that service is stopped for a compaction, and in backup
    copies until they expire." """
    says(
        "run",
        "## How much history a page keeps",
        "Beside a continuous backup the replica service holds the log file, so what was removed"
        " can stay in it until that service is stopped for a compaction, and in backup copies"
        " until they expire.",
    )


def test_one_server_holds_one_database_and_the_guide_says_so(tmp_path):
    """run.md: "One `serve` holds a database: a second one started on the same file stops and
    says that another server is running on it." """
    says(
        "run",
        "## Where your data lives",
        "One `serve` holds a database: a second one started on the same file stops and says"
        " that another server is running on it.",
    )
    db = tmp_path / "cairn.db"
    first = cli.take_serve_lock(db)
    try:
        assert first is not None and cli.take_serve_lock(db) is None
        assert "another server is running on this database" in cli.ANOTHER_SERVER
    finally:
        first.release()


def test_the_readme_says_how_an_install_of_the_released_image_is_updated():
    """README.md, "Update": with the released image, the version in `CAIRN_IMAGE` changes and
    the image is pulled; nothing is built."""
    text = read("README.md")
    update = text.split("\n### Update\n", 1)[1].split("\n### ", 1)[0]
    assert "change the version in the `CAIRN_IMAGE` line of `.env`" in update
    assert "`docker compose pull cairn` and `docker compose up -d --no-build`" in update
    assert "apart from [Update](#update)" in text


def test_the_newest_events_removed_leave_a_chain_that_still_holds_and_the_guide_says_so(tmp_path):
    """security.md: "Removing the newest events leaves a shorter chain that still follows, so
    that, too, shows only against that copy." """
    import sqlite3

    from cairn.store import DEFAULT_ACCOUNT_ID, Store
    from cairn.store.audit import check_trail

    says(
        "security",
        "## Logs",
        "Removing the newest events leaves a shorter chain that still follows, so that, too,"
        " shows only against that copy.",
    )
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        ops = store.create_project("ops")
        for n in range(4):
            store.record_note(ops.id, "unchanged", {"page": f"p{n}"})
    finally:
        store.close()
    con = sqlite3.connect(db)
    try:
        (before,) = check_trail(con, DEFAULT_ACCOUNT_ID)
        newest = con.execute("SELECT MAX(n) FROM audit_events").fetchone()[0]
        con.execute("DELETE FROM audit_events WHERE n >= ?", (newest - 1,))
        con.commit()
        (after,) = check_trail(con, DEFAULT_ACCOUNT_ID)
    finally:
        con.close()
    assert before.broken is None and after.broken is None
    assert after.checked == before.checked - 2


def test_the_readme_points_at_the_quick_deploy_for_both_clouds():
    """README.md, "Run it for other people": one sentence leads to the quick deploy on AWS or
    Google Cloud, whose guide and definitions exist and say they were not applied."""
    others = part(read("README.md"), "## Run it for other people")
    assert "[quick deploy](infra/quick/README.md)" in others
    assert "AWS" in others and "Google Cloud" in others
    guide = read("infra/quick/README.md")
    for cloud in ("aws", "gcp"):
        assert (ROOT / "infra" / "quick" / cloud / "main.tf").is_file()
    assert "applied to a real account" in " ".join(guide.split())


def test_slack_is_called_through_the_checked_client_and_the_guide_says_so():
    """security.md: "The server's calls to Slack go through the same check." The app gives
    Slack's client, its keeper and its replies the client the sources use unless a test hands
    it another."""
    says(
        "security",
        "## What cairn leaves to the operator",
        "The server's calls to Slack go through the same check.",
    )
    app = (ROOT / "src" / "cairn" / "web" / "app.py").read_text(encoding="utf-8")
    assert "OneClient(slack.http or source_http)" in app
    assert app.count("http=slack.http or source_http") >= 3
    assert "source_http: Callable[[], httpx.AsyncClient] = source_client" in app


ORDER = (
    "The claims of a page stand in an order that was chosen when the page was arranged, and"
    " each was checked by itself: do not read two claims as one statement because they stand"
    " together. `evidence` shows a claim in its note, in the order its writer gave it."
)


def test_an_agent_is_told_that_the_order_of_claims_is_not_the_writers():
    """What an agent is told at initialize says, once, that a page's order is not checked,
    as the security guide says to people."""
    from cairn.web.mcp import TREAT_AS_INFORMATION, instructions

    told = " ".join(instructions("cairn").split())
    assert told.count(ORDER) == 1
    assert told.endswith(TREAT_AS_INFORMATION)  # the last paragraph stays the last


def test_the_run_guide_links_dockers_install_pages_beside_the_engine_it_asks_for():
    """run.md, "Start it": the row that asks for Engine 28.3.3 links Docker's install pages."""
    start = section("run", "## Start it")
    (row,) = [line for line in start.splitlines() if "28.3.3" in line]
    assert "https://docs.docker.com/engine/install/" in row


def test_the_security_guide_says_a_body_is_the_notes_own_markdown_and_the_web_view_reads_html_off():
    """security.md: "A page's body is the notes' own Markdown: ... Read the body with raw HTML
    off, as the web view does: ..." The behaviour is pinned in test_claim_markdown_unit.py;
    here, that the web view's parser has raw HTML off, which the second sentence rests on."""
    says(
        "security",
        "## What the checks on pages catch",
        "A page's body is the notes' own Markdown: a setext heading in a note puts the claims"
        " after it under that heading, and a link definition shows nothing; the web view shows"
        " each claim by itself, with its own source.",
        "Read the body with raw HTML off, as the web view does: with it on, a comment a note"
        " opens and does not close hides what follows.",
    )
    source = (ROOT / "src" / "cairn" / "web" / "markdown.py").read_text(encoding="utf-8")
    parsers = [line for line in source.splitlines() if "MarkdownIt(" in line]
    assert parsers and all('"html": False' in line for line in parsers), parsers
    assert (ROOT / "tests" / "unit" / "test_claim_markdown_unit.py").is_file()
