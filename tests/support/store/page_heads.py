"""Shared test helpers extracted from test_page_heads_unit."""

import pytest

import cairn.store.schema
from support.store.store import connect, statements, user_version
from support.store.writers_schema import v24_database


def v25_database(db) -> dict:
    v24_database(db)
    stamped = cairn.store.schema._STAMP.set(lambda: "2099-10-31T09:00:00.000000+00:00")
    try:
        with connect(db) as con:
            con.execute("BEGIN")
            cairn.store.schema._MIGRATIONS[cairn.store.schema._WRITERS](con)
            con.execute(f"PRAGMA user_version = {cairn.store.schema._WRITERS}")
            con.commit()
    finally:
        cairn.store.schema._STAMP.reset(stamped)
    assert user_version(db) == 25
    return {"statements": statements(db)}


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cairn.db"
