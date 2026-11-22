"""Shared test helpers extracted from test_hosted_accounts_unit."""

import sqlite3
from contextlib import closing

import cairn.store.schema
from support.store.open_checkouts import v26_database
from support.store.store import statements, user_version


def v27_database(db) -> dict:
    v26_database(db)
    with closing(sqlite3.connect(db)) as con:
        cairn.store.schema._MIGRATIONS[cairn.store.schema._CHECKOUTS](con)
        con.execute(f"PRAGMA user_version = {cairn.store.schema._CHECKOUTS}")
        con.commit()
    assert user_version(db) == 27
    return {"statements": statements(db)}
