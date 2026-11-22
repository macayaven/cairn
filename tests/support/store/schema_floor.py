"""Shared test helpers extracted from test_schema_floor_unit."""

import sqlite3
from pathlib import Path

import cairn.store.schema as schema
from cairn.store import SCHEMA_VERSION
from support.store.open_checkouts import v26_database
from support.store.store import connect, user_version

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
