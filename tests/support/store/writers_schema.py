"""Shared test helpers extracted from test_writers_schema_unit."""

import cairn.store.schema
from support.store.store import (
    connect,
    statements,
    user_version,
    v23_database,
)


def v24_database(db) -> dict:
    v23_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._TOPIC_KEYS](con)
        con.execute(f"PRAGMA user_version = {cairn.store.schema._TOPIC_KEYS}")
        con.commit()
    assert user_version(db) == 24
    return {"statements": statements(db)}
