"""Shared test helpers extracted from test_open_checkouts_unit."""

import cairn.store.schema
from support.store.page_heads import v25_database
from support.store.store import connect, statements, user_version


def v26_database(db) -> dict:
    v25_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._PAGE_HEADS](con)
        con.execute(f"PRAGMA user_version = {cairn.store.schema._PAGE_HEADS}")
        con.commit()
    assert user_version(db) == 26
    return {"statements": statements(db)}
