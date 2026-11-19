"""Schema step 27 (R3) added the checkouts table, which nothing reads or writes any more; the
step stays, so a database of any earlier version opens. A database built from history:
test_page_heads_unit's version 25, then step 26, as cairn wrote them."""

import pytest
from test_page_heads_unit import v25_database
from test_store_unit import before_step_30, connect, rows, statements, user_version

import cairn.store.schema
from cairn.store import SCHEMA_VERSION, Store

COLUMNS = ["account_id", "plan", "idempotency_key", "url", "opened_at", "ends_at"]


def v26_database(db) -> dict:
    v25_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._PAGE_HEADS](con)
        con.execute(f"PRAGMA user_version = {cairn.store.schema._PAGE_HEADS}")
        con.commit()
    assert user_version(db) == 26
    return {"statements": statements(db)}


def test_step_27_adds_the_empty_checkouts_table_and_changes_nothing_else(tmp_path):
    db = tmp_path / "cairn.db"
    v26 = v26_database(db)
    assert cairn.store.schema._CHECKOUTS == 27 and SCHEMA_VERSION >= 27
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION
    assert [row[1] for row in rows(db, "PRAGMA table_info(checkouts)")] == COLUMNS
    assert rows(db, "SELECT COUNT(*) FROM checkouts") == [(0,)]
    before = set(v26["statements"])
    added = [line for line in statements(db) if line not in before]
    later = ("credential_tokens", "google_revocations", "slack_")  # 28, 29: empty tables
    later += ("sessions",)  # 30: sessions rebuilt with their last use
    assert added and all(
        "checkouts" in line or any(name in line for name in later) for line in added
    ), added
    rebuilt = {line for line in v26["statements"] if line.startswith("CREATE TABLE sessions")}
    kept = set(before_step_30(statements(db)))
    assert set(v26["statements"]) - rebuilt <= kept  # every row there was is kept


def test_a_database_migrated_from_version_26_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v26_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)


def test_a_failed_step_27_leaves_the_version_26_database_as_it_was(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    v26 = v26_database(db)
    version = cairn.store.schema._CHECKOUTS
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v26["statements"] and user_version(db) == 26
