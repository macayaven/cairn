"""Unit tests for erasure by zeroing: what the store turns on when it opens a database, and
how delete_account leaves the files. The inner loop under tests/acceptance/test_h5_data.py
and the third check of tests/system/test_invariants.py."""

import logging
import sqlite3
from pathlib import Path

import pytest

from cairn.compiler import compile_note
from cairn.store import Store
from cairn.store.schema import _FTS5_SECURE_DELETE


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


def workspace(store: Store, word: str) -> Store:
    view = store.for_account(store.create_account(word).id)
    project = view.create_project("core")
    for n in range(5):
        note, _ = view.add_note(project.id, f"# {word} {n}\n\nThe {word} fact is {n}.\n")
        assert compile_note(view, note).status == "saved"
    return view


def index_option(db: Path, account_id: str) -> object:
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            f"SELECT v FROM claim_search_{account_id}_config WHERE k = 'secure-delete'"
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def test_the_store_zeroes_what_it_deletes(db):
    store = Store(db)
    try:
        assert store._con.execute("PRAGMA secure_delete").fetchone()[0] == 1
    finally:
        store.close()


def test_opening_a_database_whose_indexes_are_set_writes_nothing(db):
    store = Store(db)
    workspace(store, "heron")
    store.close()
    before = db.read_bytes()
    Store(db).close()
    assert db.read_bytes() == before


def test_delete_account_truncates_the_write_ahead_log(db):
    store = Store(db)
    try:
        gone = workspace(store, "osprey")
        workspace(store, "heron")
        wal = db.parent / (db.name + "-wal")
        assert wal.stat().st_size > 0
        assert store.delete_account(gone.account_id) is True
        assert wal.stat().st_size == 0
        assert b"osprey" not in db.read_bytes().lower()
    finally:
        store.close()


def test_a_deletion_another_reader_keeps_in_the_log_still_succeeds_and_says_so(db, caplog):
    store = Store(db)
    store._con.execute("PRAGMA busy_timeout = 0")  # refuse at once, not after 5 s of retries
    reader = sqlite3.connect(db)
    try:
        gone = workspace(store, "osprey")
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM notes").fetchone()
        with caplog.at_level(logging.WARNING, logger="cairn.store"):
            assert store.delete_account(gone.account_id) is True
        assert store.get_account(gone.account_id) is None
        (record,) = caplog.records
        assert gone.account_id in record.getMessage() and "write-ahead log" in record.getMessage()
    finally:
        reader.close()
        store.close()


@pytest.mark.skipif(not _FTS5_SECURE_DELETE, reason="this SQLite has no FTS5 secure-delete")
def test_an_index_made_without_fts5_secure_delete_gets_it_at_its_next_write(db):
    store = Store(db)
    view = workspace(store, "heron")
    store.close()
    con = sqlite3.connect(db)
    con.execute(f"DELETE FROM claim_search_{view.account_id}_config WHERE k = 'secure-delete'")
    con.commit()
    con.close()
    store = Store(db)  # opening it changes nothing (migration tests rely on that)
    try:
        assert index_option(db, view.account_id) is None
        view = store.for_account(view.account_id)
        project = view.get_project("core")
        note, _ = view.add_note(project.id, "# heron 9\n\nThe heron fact is 9.\n")
        assert compile_note(view, note).status == "saved"
        assert index_option(db, view.account_id) == 1
    finally:
        store.close()
