"""Schema step 31 and the oldest build a database records (the floor).

Step 31 drops the three tables billing kept, which nothing reads or writes. From step 31 on, a
database records the lowest schema version whose cairn still runs on it: each step says
whether it only adds (tables, columns with defaults, indexes) or changes what an older build
relies on, and a step of the second kind raises the floor to itself. A build that meets a
newer database opens it when its own version is at or above the floor, runs normally and never
runs a step; below the floor it refuses with SchemaTooNew, in one sentence naming the numbers.

The mechanism is proven with steps invented here, run by the real upgrade loop: one that only
adds and one that does not, each one version past this build."""

import sqlite3
from pathlib import Path

import pytest
from test_open_checkouts_unit import v26_database
from test_store_unit import connect, rows, save, statements, tables, user_version

import cairn.store.schema as schema
from cairn.store import DEFAULT_ACCOUNT_ID, SCHEMA_VERSION, SchemaTooNew, Store

ROOT = Path(__file__).resolve().parents[2]
RETIRED = ("subscriptions", "billing_messages", "checkouts")
LATER = SCHEMA_VERSION + 1  # the version of the step invented here


def v30_database(db: Path) -> None:
    """A database at step 30, as cairn's own steps left it (built from history)."""
    v26_database(db)
    with connect(db) as con:
        for step in range(27, 31):
            con.execute("BEGIN")
            schema._MIGRATIONS[step](con)
            con.execute(f"PRAGMA user_version = {step}")
            con.commit()
    assert user_version(db) == 30


def billing_rows(db: Path) -> None:
    with connect(db) as con:
        con.execute(
            "INSERT INTO subscriptions (account_id, provider, plan, status, since)"
            " VALUES (?, 'till', 'solo', 'active', '2026-01-01T00:00:00+00:00')",
            (DEFAULT_ACCOUNT_ID,),
        )
        con.execute(
            "INSERT INTO billing_messages (provider, id, account_id, received_at)"
            " VALUES ('till', 'evt-1', ?, '2026-01-01T00:00:00+00:00')",
            (DEFAULT_ACCOUNT_ID,),
        )
        con.execute(
            "INSERT INTO checkouts (account_id, plan, idempotency_key, opened_at, ends_at)"
            " VALUES (?, 'solo', 'key-1', '2026-01-01T00:00:00+00:00',"
            " '2026-01-02T00:00:00+00:00')",
            (DEFAULT_ACCOUNT_ID,),
        )
        con.commit()


def floor(db: Path) -> list[tuple]:
    return rows(db, "SELECT version FROM oldest_build")


# Step 31: the retired tables go


def test_step_31_drops_the_tables_billing_kept_with_any_rows_in_them(tmp_path):
    db = tmp_path / "cairn.db"
    v30_database(db)
    billing_rows(db)
    Store(db).close()
    assert schema._OLDEST_BUILD == 31 and user_version(db) == SCHEMA_VERSION == 32  # 32: 0.1.0
    assert not set(RETIRED) & tables(db)
    assert not [line for line in statements(db) if any(name in line for name in RETIRED)]
    doc = " ".join(schema._MIGRATIONS[31].__doc__.split())
    assert "rows in them are lost with the step" in doc


def test_a_fresh_database_has_none_of_the_retired_tables():
    made = sqlite3.connect(":memory:")
    try:
        for statement in schema._SCHEMA:
            made.execute(statement)
        held = {n for (n,) in made.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        made.close()
    assert not set(RETIRED) & held


def test_no_code_that_runs_names_the_retired_tables_but_the_steps_that_are_history():
    """Steps 12, 13 and 27 made them and stay (an older database still runs them, then 31
    drops them); nothing else in the package names them."""
    for path in sorted((ROOT / "src" / "cairn").rglob("*.py")):
        if path.parent == ROOT / "src" / "cairn" / "store" / "schema":  # the steps and DDL
            continue
        text = path.read_text(encoding="utf-8").replace("event_subscriptions", "")
        for name in RETIRED:
            assert name not in text, f"{path.relative_to(ROOT)} names {name}"
    for step in (12, schema._SUBSCRIPTION_TIMES, schema._CHECKOUTS, schema._OLDEST_BUILD):
        assert step in schema._MIGRATIONS


# Each step says its kind, and the floor follows


def test_steps_29_and_30_only_add_and_31_does_not():
    assert schema._SLACK in schema._ONLY_ADDS and schema._LAST_USE in schema._ONLY_ADDS
    assert schema._OLDEST_BUILD not in schema._ONLY_ADDS
    assert schema._floor_of(schema._CREDENTIAL_TOKENS) == 28
    assert schema._floor_of(schema._SLACK) == 28
    assert schema._floor_of(schema._LAST_USE) == 28
    assert schema._floor_of(schema._OLDEST_BUILD) == 31
    assert schema._OWN_PASSAGES not in schema._ONLY_ADDS
    assert schema._floor_of(schema._OWN_PASSAGES) == 32


def test_a_fresh_database_records_its_floor(tmp_path):
    db = tmp_path / "cairn.db"
    Store(db).close()
    assert floor(db) == [(schema._floor_of(SCHEMA_VERSION),)] == [(32,)]


def test_a_database_brought_to_31_and_32_records_its_floor(tmp_path):
    db = tmp_path / "cairn.db"
    v30_database(db)
    Store(db).close()
    assert floor(db) == [(32,)]  # step 31 records 31, step 32 raises it to 32


def test_a_current_database_opens_without_a_write(tmp_path):
    db = tmp_path / "cairn.db"
    Store(db).close()
    before = statements(db)
    Store(db).close()
    assert statements(db) == before


# A step invented here, one version past this build


def invented(con: sqlite3.Connection) -> None:
    """What a later step that only adds may do: a column with a default on every table this
    build knows, a table and an index it does not know."""
    for table in schema._TABLES:
        con.execute(f"ALTER TABLE {table} ADD COLUMN later_mark TEXT NOT NULL DEFAULT 'x'")
    con.execute("CREATE TABLE later_things (id INTEGER PRIMARY KEY, what TEXT NOT NULL)")
    con.execute("CREATE INDEX later_things_by_what ON later_things (what)")
    con.execute("INSERT INTO later_things (what) VALUES ('kept')")


def moved_ahead(db: Path, monkeypatch, *, only_adds: bool) -> None:
    """Run the invented step through the real upgrade loop, as a build one version newer
    would, then put this build back."""
    with monkeypatch.context() as later:
        later.setattr(schema, "SCHEMA_VERSION", LATER)
        later.setattr(schema, "_MIGRATIONS", {**schema._MIGRATIONS, LATER: invented})
        if only_adds:
            later.setattr(schema, "_ONLY_ADDS", schema._ONLY_ADDS | {LATER})
        with connect(db) as con:
            con.isolation_level = None
            schema._upgrade(con, lambda: "2026-01-01T00:00:00.000000+00:00")
    assert user_version(db) == LATER


def test_a_database_one_additive_step_ahead_opens_and_reads_and_writes(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    first = Store(db)
    try:
        project = first.create_project("alpha")
        first.add_note(project.id, "# Relay\n\nThe relay runs at night.\n")
        save(first, project, "relay", "Relay", "the relay runs at night")
    finally:
        first.close()
    moved_ahead(db, monkeypatch, only_adds=True)
    assert floor(db) == [(32,)]  # it only adds: the floor stays
    store = Store(db)
    try:
        assert store.health() == "ok"
        alpha = store.get_project("alpha")
        assert [h.text for h in store.search("relay")] == ["the relay runs at night"]
        assert store.get_page(alpha.id, "relay").title == "Relay"
        beta = store.create_project("beta")
        store.add_note(beta.id, "# Lamp\n\nThe lamp is blue.\n")
        save(store, beta, "lamp", "Lamp", "the lamp is blue")
        assert [h.text for h in store.search("lamp")] == ["the lamp is blue"]
        assert [e.action for e in store.list_audit(2)] == ["note.saved", "project.created"]
        other = store.create_account("Fable Works")
        assert store.delete_account(other.id)
    finally:
        store.close()
    assert user_version(db) == LATER  # no step ran, nothing went back
    assert floor(db) == [(32,)]
    assert rows(db, "SELECT what FROM later_things") == [("kept",)]
    assert rows(db, "SELECT DISTINCT later_mark FROM projects") == [("x",)]


def test_a_database_one_changing_step_ahead_is_refused_naming_both_numbers(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    Store(db).close()
    moved_ahead(db, monkeypatch, only_adds=False)
    assert floor(db) == [(LATER,)]
    before = db.read_bytes()
    with pytest.raises(SchemaTooNew) as refused:
        Store(db)
    assert str(refused.value) == (
        f"the database has schema version {LATER} and needs a cairn of schema version {LATER}"
        f" or later; this cairn knows up to {SCHEMA_VERSION}"
    )
    assert db.read_bytes() == before


def test_a_newer_database_that_records_no_floor_is_refused(tmp_path):
    db = tmp_path / "cairn.db"
    Store(db).close()
    with connect(db) as con:
        con.execute("DROP TABLE oldest_build")
        con.execute(f"PRAGMA user_version = {LATER}")
        con.commit()
    with pytest.raises(SchemaTooNew, match=f"needs a cairn of schema version {LATER} or later"):
        Store(db)


def test_the_health_check_answers_by_the_floor(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        assert store.health() == "ok"
        with connect(db) as con:
            con.execute(f"PRAGMA user_version = {LATER}")  # a later step that only adds
            con.commit()
        assert store.health() == "ok"
        with connect(db) as con:
            con.execute("UPDATE oldest_build SET version = ?", (LATER,))  # one that does not
            con.commit()
        assert store.health() == "unavailable"
    finally:
        store.close()
