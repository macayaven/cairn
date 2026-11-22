"""What the upgrade to schema 32 and the `repair` command say is what they committed (report
9, must-fix 2). `repair` never upgrades a database by the way: its open refuses one of an
older schema, as every command's but `serve`'s does, and the file is left as it was. The
upgrade's counts reach the store's log only once the step's transaction, floor and version
included, has committed: a step that fails after the repair logs nothing and leaves the file
at 31 with every page. A removal the upgrade commits marks a compaction due, as a
forget does; a database with no such page is not marked. Made-up notes."""

import logging
from contextlib import closing

import pytest

import cairn.store.schema as schema
from cairn.store import Store
from cairn.web import cli
from support.store.repair_says_what_committed import at_28_file, at_31, clean_31
from support.store.schema_step32 import floor, table_rows
from support.store.store import user_version


def repairs(caplog) -> list[tuple]:
    return [r.args for r in caplog.records if r.getMessage().startswith("repair:")]


@pytest.mark.parametrize("build", [at_31, at_28_file, clean_31], ids=["31", "28", "clean-31"])
def test_repair_refuses_a_database_of_an_older_schema_and_leaves_it_as_it_was(
    tmp_path, capsys, build
):
    db = build(tmp_path)
    version, held = user_version(db), table_rows(db)
    assert cli.main(["repair", "--db", str(db)]) == 1
    said = capsys.readouterr()
    assert said.out == ""
    assert said.err == (
        f"the database at {db} has an older schema (version {version}; this cairn's is"
        f" {schema.SCHEMA_VERSION}): start the server once on it (serve --db) to bring it up to"
        " date, which holds every page to its own project's notes and logs how many pages it"
        " rewrote and removed; only serve upgrades it, and repair changed nothing\n"
    )
    assert user_version(db) == version and table_rows(db) == held


def test_the_upgrade_logs_its_counts_only_after_its_step_commits(tmp_path, caplog, monkeypatch):
    """The reviewer's forced failure: the floor's update fails after the repair. SQLite rolls
    the step back; nothing is logged, and the file is at 31 with every page."""
    db = at_31(tmp_path)
    held = table_rows(db)
    recorded = schema._record_floor

    def failing(con, version):
        if version == 32:
            raise RuntimeError("the floor's update failed")
        recorded(con, version)

    monkeypatch.setattr(schema, "_record_floor", failing)
    with (
        caplog.at_level(logging.WARNING, logger="cairn.store"),
        pytest.raises(RuntimeError, match="floor"),
    ):
        Store(db)
    assert [r.getMessage() for r in caplog.records if r.name.startswith("cairn.store")] == []
    assert user_version(db) == 31 and floor(db) == [(31,)] and table_rows(db) == held
    monkeypatch.undo()
    with caplog.at_level(logging.WARNING, logger="cairn.store"), closing(Store(db)):
        pass
    assert len(repairs(caplog)) == 2 and user_version(db) == 32


def test_a_removal_the_upgrade_commits_marks_a_compaction_due(tmp_path):
    db = at_31(tmp_path)
    with closing(Store(db)) as store:
        assert store.compaction_state().deleted_at is not None


def test_an_upgrade_that_changes_no_page_marks_none(tmp_path, caplog):
    db = clean_31(tmp_path)
    with caplog.at_level(logging.WARNING, logger="cairn.store"), closing(Store(db)) as store:
        assert store.compaction_state().deleted_at is None
    assert repairs(caplog) == []
