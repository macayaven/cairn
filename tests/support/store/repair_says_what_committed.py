"""Shared test helpers extracted from test_repair_says_what_committed_unit."""

from contextlib import closing

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store
from cairn.store.base import COMPACT_DUE, DEFAULT_ACCOUNT_ID
from support.store.schema_step32 import at_28, made, stamped_31
from support.store.store import connect


def undue(db) -> None:
    """No compaction due: as a database whose last compaction covered every deletion."""
    with connect(db) as con:
        con.execute(
            "DELETE FROM settings WHERE account_id = ? AND name = ?",
            (DEFAULT_ACCOUNT_ID, COMPACT_DUE),
        )
        con.commit()


def at_31(tmp_path):
    db = tmp_path / "cairn.db"
    made(db)
    undue(db)
    stamped_31(db)
    return db


def at_28_file(tmp_path):
    source, db = tmp_path / "made.db", tmp_path / "cairn.db"
    made(source)
    at_28(db, source)
    return db


def clean_31(tmp_path):
    db = tmp_path / "cairn.db"
    with closing(Store(db)) as store:
        store.create_project("east")
        Memory(store, lint_gate).note("east", "# Far\n\nWren keeps the far log.\n")
    undue(db)
    stamped_31(db)
    return db
