"""Sentences of the user guides (src/cairn/guides/*.md) that are checked against the guides
themselves and the configuration files cairn ships: links that lead somewhere, kinds that
exist, a recipe that follows its own table, and the backup settings the privacy guide states.
Each check is a function of text, so it can be run on a doctored copy to see it fail."""

import re
from importlib.resources import files
from pathlib import Path

import yaml

from cairn.kinds import KINDS
from cairn.web.public import GUIDES, read_guide

ROOT = Path(__file__).resolve().parents[2]


def guide_text(name: str) -> str:
    return (files("cairn") / "guides" / f"{name}.md").read_text(encoding="utf-8")


# connect.md and the others: every link to a section leads to one


def unresolved_links(rendered: dict[str, str]) -> tuple[list[str], int]:
    """Each in-page link (`#x`) and each link to another guide's section (`/docs/g#x`) in
    the rendered guides whose target id is not in that guide; and how many links there were."""
    ids = {name: set(re.findall(r'\bid="([^"]+)"', html)) for name, html in rendered.items()}
    missing, seen = [], 0
    for name, html in rendered.items():
        for target, fragment in re.findall(r'href="(/docs/[a-z]+)?#([^"]*)"', html):
            seen += 1
            guide = target.removeprefix("/docs/") if target else name
            if fragment not in ids.get(guide, set()):
                missing.append(f"{name}: {target}#{fragment}")
    return missing, seen


def test_every_link_to_a_section_of_a_guide_leads_to_that_section():
    rendered = {name: str(read_guide(name).html) for name in GUIDES}
    missing, seen = unresolved_links(rendered)
    assert missing == []
    assert seen >= 13  # connect's table and its Claude Code link, patterns', privacy's
    # The check sees a broken link: a heading renamed under its link.
    doctored = {**rendered, "connect": rendered["connect"].replace('id="cursor"', 'id="kursor"')}
    assert unresolved_links(doctored)[0] == ["connect: #cursor"]


# patterns.md and sources.md: every kind they name is a kind cairn has


def named_kinds(patterns: str, sources: str) -> dict[str, list[str]]:
    """The kinds the two guides name, by where: the Kind column of patterns' worked example,
    the italic kinds of sources' "One project per kind of source", and the bold kinds of each
    recipe's "*Project:* kind ..." line."""
    table = patterns.split("| Project | Kind |", 1)[1].split("\n\n", 1)[0]
    rows = [line for line in table.splitlines()[1:] if line.startswith("|")]
    column = [line.split("|")[2].strip() for line in rows if not set(line) <= set("|- ")]
    shares = next(line for line in sources.splitlines() if "**One project per kind" in line)
    italic = re.findall(r"(?<!\*)\*([^*]+)\*(?!\*)", shares)
    bold = [
        name
        for line in sources.splitlines()
        if line.startswith("*Project:* kind ")
        for name in re.findall(r"\*\*([^*]+)\*\*", line)
    ]
    return {"patterns table": column, "sources italic": italic, "sources recipes": bold}


def test_every_kind_the_guides_name_is_a_kind_cairn_offers():
    named = named_kinds(guide_text("patterns"), guide_text("sources"))
    assert all(named.values()), named  # each place still names kinds the way read here
    titles = {kind.title for kind in KINDS.values()}
    for where, names in named.items():
        assert set(names) <= titles, (where, set(names) - titles)
    # The check sees a kind cairn does not have.
    doctored = guide_text("sources").replace("kind **Meetings**", "kind **Meeting notes**")
    assert not set(named_kinds(guide_text("patterns"), doctored)["sources recipes"]) <= titles


# sources.md: each recipe's source_ref is one of the forms the guide's table lists


def source_ref_shapes(sources: str) -> tuple[set[str], list[str]]:
    """The table's `source_ref` forms and each recipe's `source_ref:` line, with every
    `<placeholder>` written as `<>`."""

    def shape(form: str) -> str:
        return re.sub(r"<[^>]*>", "<>", form.strip().strip("`"))

    table = sources.split("| Source | `source_ref` |", 1)[1].split("\n\n", 1)[0]
    forms = {
        shape(line.split("|")[2])
        for line in table.splitlines()[1:]
        if line.startswith("|") and not set(line) <= set("|- ")
    }
    recipes = [shape(found) for found in re.findall(r"^- source_ref: (.+)$", sources, re.M)]
    return forms, recipes


def test_every_recipes_source_ref_is_a_form_the_guides_table_lists():
    forms, recipes = source_ref_shapes(guide_text("sources"))
    assert len(forms) >= 6 and len(recipes) == 5  # meetings, mail, calendar, documents, to-dos
    assert [r for r in recipes if r not in forms] == []
    # The check sees a recipe that strays from the table.
    doctored = guide_text("sources").replace(
        "- source_ref: calendar:week/<Monday's date>", "- source_ref: cal:<Monday's date>"
    )
    forms, recipes = source_ref_shapes(doctored)
    assert "cal:<>" in recipes and "cal:<>" not in forms


# privacy.md: the shipped backup settings are the ones it states


def backup_settings(litestream: str, main_tf: str, variables_tf: str) -> dict[str, object]:
    """What the shipped configuration sets, read from the files as they are shipped."""
    replica = yaml.safe_load(litestream)
    (db,) = replica["dbs"]
    policy = main_tf.split('resource "google_compute_resource_policy" "data_daily"', 1)[1]
    policy = policy.split("\nresource ", 1)[0]
    bucket_delete = re.search(r"soft_delete_policy\s*\{([^}]*)\}", main_tf).group(1)
    soft_days = variables_tf.split('variable "replica_soft_delete_days"', 1)[1]
    return {
        "sync": db["replica"]["sync-interval"],
        "snapshot every": replica["snapshot"]["interval"],
        "snapshot kept": replica["snapshot"]["retention"],
        "disk snapshot days in cycle": int(re.search(r"days_in_cycle\s*=\s*(\d+)", policy)[1]),
        "disk snapshot kept days": int(re.search(r"max_retention_days\s*=\s*(\d+)", policy)[1]),
        "bucket soft delete": " ".join(bucket_delete.split()),
        "bucket soft delete days": int(re.search(r"default\s*=\s*(\d+)", soft_days)[1]),
    }


def test_the_shipped_backup_settings_are_the_ones_the_privacy_guide_states():
    shipped = backup_settings(
        (ROOT / "deploy" / "litestream.yml").read_text(),
        (ROOT / "infra" / "gcp" / "main.tf").read_text(),
        (ROOT / "infra" / "gcp" / "variables.tf").read_text(),
    )
    assert shipped == {
        "sync": "10s",  # "copies changes every 10 seconds"
        "snapshot every": "24h",  # "a daily snapshot"
        "snapshot kept": "192h",  # "for eight days", "Eight days in the bucket"
        "disk snapshot days in cycle": 1,  # "snapshots the machine's data disk daily"
        "disk snapshot kept days": 14,  # "keeps each snapshot 14 days"
        "bucket soft delete": "retention_duration_seconds = var.replica_soft_delete_days * 86400",
        "bucket soft delete days": 30,  # "restorable for 30 more days"
    }
    privacy = " ".join(guide_text("privacy").split())
    for said in (
        "The shipped configuration copies changes every 10 seconds and keeps a daily snapshot"
        " for eight days; the shipped Google Cloud configuration also snapshots the machine's"
        " data disk daily and keeps each snapshot 14 days.",
        "Eight days in the bucket; on Google Cloud, the bucket keeps what is removed from it"
        " restorable for 30 more days, and disk snapshots are kept 14 days",
        "With the shipped configuration that is eight days for the copies in the storage bucket.",
    ):
        assert said in privacy, said
