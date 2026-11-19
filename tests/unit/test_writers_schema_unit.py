"""Schema step 25 (A1): who wrote each note, the key each key or grant was made with, and the
moment the step ran. A database built from history: test_store_unit's version 23, then step
24, as cairn wrote them. The store's clock is in 2099."""

from datetime import UTC, datetime

import pytest
from test_store_unit import (
    before_step_30,
    connect,
    rows,
    statements,
    user_version,
    v23_database,
)

import cairn.store.schema
from cairn.store import SCHEMA_VERSION, Store

MOMENT = datetime(2099, 5, 6, 7, 8, 9, tzinfo=UTC)


def v24_database(db) -> dict:
    v23_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._TOPIC_KEYS](con)
        con.execute(f"PRAGMA user_version = {cairn.store.schema._TOPIC_KEYS}")
        con.commit()
    assert user_version(db) == 24
    return {"statements": statements(db)}


def test_step_25_adds_three_empty_tables_and_the_moment_it_ran(tmp_path):
    db = tmp_path / "cairn.db"
    v24 = v24_database(db)
    assert cairn.store.schema._WRITERS == 25 and SCHEMA_VERSION >= 25
    Store(db, clock=lambda: MOMENT).close()
    assert user_version(db) == SCHEMA_VERSION
    assert rows(db, "SELECT COUNT(*) FROM note_writers") == [(0,)]  # every note: unknown
    assert rows(db, "SELECT COUNT(*) FROM made_with") == [(0,)]  # every key: made by none
    assert rows(db, "SELECT at FROM recorded_since") == [
        (MOMENT.isoformat(timespec="microseconds"),)
    ]
    # Step 30 rebuilds sessions with each one's last use (`used_at`, last, NULL on every
    # session there was): read the sessions as step 29 left them.
    rebuilt = cairn.store.schema._V30_SESSIONS + ";"
    assert rebuilt in statements(db)
    before = {line for line in v24["statements"] if not line.startswith("CREATE TABLE sessions")}
    added = [line for line in before_step_30(statements(db)) if line not in before]
    added = [line for line in added if line != rebuilt]
    assert before <= set(before_step_30(statements(db)))
    later = (
        "page_heads",
        "head_saved",
        "head_removed",
        "checkouts",
        "credential_tokens",
        "google_revocations",
        "slack_",
    )
    assert all(
        "note_writers" in line
        or "made_with" in line
        or "recorded_since" in line
        or any(name in line for name in later)
        for line in added
    ), added  # every row and table there was is kept as it was


def test_a_new_database_records_the_moment_it_was_made(tmp_path):
    db = tmp_path / "cairn.db"
    Store(db, clock=lambda: MOMENT).close()
    assert rows(db, "SELECT at FROM recorded_since") == [
        (MOMENT.isoformat(timespec="microseconds"),)
    ]


def test_a_database_migrated_from_version_24_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v24_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)


def test_a_failed_step_25_leaves_the_version_24_database_as_it_was(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    v24 = v24_database(db)
    version = cairn.store.schema._WRITERS
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v24["statements"] and user_version(db) == 24


def test_the_new_tables_are_keyed_as_they_are_read(tmp_path):
    db = tmp_path / "cairn.db"
    Store(db).close()
    assert rows(db, "SELECT name, pk FROM pragma_table_info('note_writers') ORDER BY cid") == [
        ("note_id", 1),
        ("kind", 0),
        ("name", 0),
    ]
    assert rows(db, "SELECT name, pk FROM pragma_table_info('made_with') ORDER BY cid") == [
        ("id", 1),
        ("account_id", 0),
        ("key_id", 0),
    ]
    assert rows(
        db, "SELECT name FROM sqlite_master WHERE tbl_name = 'made_with' ORDER BY name"
    ) == [("made_with",), ("made_with_by_key",)]
