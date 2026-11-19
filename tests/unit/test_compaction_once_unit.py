"""A rewrite that succeeded is not done again (review 6.2). Rewriting the file and emptying
the write-ahead log are recorded apart: when the rewrite succeeded and the log could not be
emptied (another connection held it, as a replica process does), what stays due is emptying
the log, which costs no rewrite, for the next interval and across a restart of the server.
A deletion that comes after starts a new rewrite. Statements are counted over every
connection of the store (Store._observe); nothing sleeps."""

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from cairn.store import CompactIncomplete, Store, base
from cairn.web.compactor import Compactor

T0 = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db, monkeypatch):
    monkeypatch.setattr(base, "_clock", lambda: T0)
    s = Store(db)
    yield s
    s.close()


@contextmanager
def replica(db):
    """As Litestream: a connection that keeps reading the write-ahead log."""
    reader = sqlite3.connect(db)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM settings").fetchone()
    try:
        yield
    finally:
        reader.rollback()
        reader.close()


@pytest.fixture
def vacuums(store):
    seen: list[str] = []
    store._observe(trace=seen.append)
    return lambda: sum(s.strip().upper() == "VACUUM" for s in seen)


def deleted(store) -> None:
    store.delete_account(store.create_account("Gone").id)


def lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("compact")]


def test_a_rewrite_whose_log_was_held_leaves_only_the_log_due(store, db, vacuums):
    deleted(store)
    with replica(db), pytest.raises(CompactIncomplete) as raised:
        store.compact()
    assert raised.value.rewrote is True
    assert vacuums() == 1
    state = store.compaction_state()
    assert state.deleted_at is not None  # still due: what the nightly job reads
    assert state.rewritten is True  # the rewrite is done; emptying the log is what is left


def test_a_restarted_server_empties_the_log_without_rewriting_the_file(store, db, vacuums, caplog):
    """The reviewer's second probe: a fresh compactor, as after a restart, repeated the
    whole rewrite."""
    deleted(store)
    with replica(db):
        assert Compactor(store, hours=24, clock=lambda: T0).run_once() is False
        assert vacuums() == 1
        restarted = Compactor(store, hours=24, clock=lambda: T0)
        assert restarted.due()  # the log is still due
        with caplog.at_level(logging.INFO, logger="cairn.access"):
            assert restarted.run_once() is False  # held still: not emptied, not rewritten
        assert vacuums() == 1
    again = Compactor(store, hours=24, clock=lambda: T0)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert again.run_once() is True  # the replica gone: the log is emptied
    assert vacuums() == 1  # one rewrite in all, for one deletion
    state = store.compaction_state()
    assert state.deleted_at is None and state.rewritten is False
    assert not again.due()
    assert os.path.getsize(f"{db}-wal") == 0
    held, done = lines(caplog)
    assert held.startswith("compact not finished: another connection held the file")
    assert "no rewrite" in held
    assert done.startswith("compact done") and "no rewrite" in done


def test_a_deletion_after_the_rewrite_starts_a_new_rewrite(store, db, vacuums):
    deleted(store)
    with replica(db), pytest.raises(CompactIncomplete):
        store.compact()
    assert store.compaction_state().rewritten is True
    deleted(store)  # after the rewrite: what it removed is in no rewrite yet
    state = store.compaction_state()
    assert state.deleted_at is not None and state.rewritten is False
    assert Compactor(store, hours=24, clock=lambda: T0).run_once() is True
    assert vacuums() == 2
    assert store.compaction_state().deleted_at is None


def test_the_command_line_compact_always_rewrites(store, db, vacuums):
    """`compact` by hand (and the nightly job) rewrites even when only the log is due: it
    prunes old versions first and gives their space back."""
    deleted(store)
    with replica(db), pytest.raises(CompactIncomplete):
        store.compact()
    store.compact()
    assert vacuums() == 2
    state = store.compaction_state()
    assert state.deleted_at is None and state.rewritten is False
    assert os.path.getsize(f"{db}-wal") == 0


def test_emptying_the_log_is_the_operators_call(store):
    with pytest.raises(PermissionError):
        store.for_account(store.create_account("Other").id).empty_log()


def test_what_the_operator_sees_in_the_log_for_each_case(store, db, caplog):
    """The lines the run guide quotes: a rewrite and its log both done, a rewrite whose log
    was held, and the log alone held then done."""
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        deleted(store)
        Compactor(store, hours=24, clock=lambda: T0).run_once()
        deleted(store)
        with replica(db):
            Compactor(store, hours=24, clock=lambda: T0).run_once()
            Compactor(store, hours=24, clock=lambda: T0).run_once()
        Compactor(store, hours=24, clock=lambda: T0).run_once()
    said = [line.rsplit(" in ", 1) for line in lines(caplog)]
    assert [first for first, _ in said] == [
        "compact done",
        "compact not finished: another connection held the file"
        " (rewritten; the write-ahead log was not emptied)",
        "compact not finished: another connection held the file"
        " (no rewrite: only the write-ahead log was due)",
        "compact done (no rewrite: only the write-ahead log was due)",
    ]
    assert all(last.endswith(" ms") for _, last in said)
