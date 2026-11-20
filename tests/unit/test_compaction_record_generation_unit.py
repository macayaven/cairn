"""Each deletion gives the compaction record a generation of its own (second review's second
report, finding 6). A rewrite marks the generation it read; emptying the log clears only the
generation whose log it emptied, in one statement that changes nothing once the record has
moved on. So emptying the log in one process never clears what another process's later
rewrite left due, and two deletions recorded with one clock value are two generations. Two
stores on one file stand for two processes; a reader that holds the write-ahead log stands
for a replica; each step is placed by a patched call, never by timing. The clock is pinned."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from cairn.store import CompactIncomplete, Store, base, portable

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "_clock", lambda: T0)  # every deletion has the same time
    return tmp_path / "cairn.db"


@pytest.fixture
def two(db):
    """The server's store and the command line's, on one file."""
    a, b = Store(db), Store(db)
    for store in (a, b):  # a held log is refused at once, not after 5 s of retries
        store._con.execute("PRAGMA busy_timeout = 0")
    yield a, b
    a.close()
    b.close()


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


def deleted(store) -> None:
    store.delete_account(store.create_account("Gone").id)


def generation(db) -> str | None:
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "SELECT updated_at FROM settings WHERE name = ?", (base.COMPACT_DUE,)
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def before_the_first_write(store, monkeypatch, step) -> list[int]:
    """Run `step` once, when `store` next opens its writer: in _empty_log, between its
    successful truncation of the log and its clearing of the record."""
    ran: list[int] = []
    real = store._write

    @contextmanager
    def first(*args, **kwargs):
        if not ran:
            ran.append(1)
            step()
        with real(*args, **kwargs) as con:
            yield con

    monkeypatch.setattr(store, "_write", first)
    return ran


def test_emptying_the_log_does_not_clear_what_a_later_rewrite_left_due(two, db, monkeypatch):
    server, command = two
    deleted(server)

    def the_other_process():
        deleted(command)  # another workspace goes
        with replica(db), pytest.raises(CompactIncomplete) as raised:
            command.compact()  # rewritten; the log is held
        assert raised.value.rewrote is True

    ran = before_the_first_write(server, monkeypatch, the_other_process)
    server.compact()  # its log truncated, then the other process, then its clearing
    assert ran == [1]
    state = server.compaction_state()
    assert state.deleted_at is not None  # the other rewrite's log is still due
    assert state.rewritten is True  # and only its log: no third rewrite


def test_the_same_through_empty_log_alone(two, db, monkeypatch):
    """The neighbour: the compactor's call when only the log is due."""
    server, command = two
    deleted(server)
    with replica(db), pytest.raises(CompactIncomplete):
        server.compact()
    assert server.compaction_state().rewritten is True

    def the_other_process():
        deleted(command)
        with replica(db), pytest.raises(CompactIncomplete):
            command.compact()

    before_the_first_write(server, monkeypatch, the_other_process)
    server.empty_log()
    state = server.compaction_state()
    assert state.deleted_at is not None and state.rewritten is True


def test_two_deletions_at_one_instant_are_two_generations(db):
    store = Store(db)
    try:
        deleted(store)
        first = generation(db)
        deleted(store)
        assert generation(db) != first
    finally:
        store.close()


def test_a_deletion_at_the_same_instant_between_rewrite_and_record_stays_due(two, db, monkeypatch):
    """Last round's race, with the clock that cannot tell the two deletions apart."""
    command, server = two
    deleted(command)
    real = portable._transaction
    ran: list[int] = []

    @contextmanager
    def late(con):
        if not ran:
            ran.append(1)
            deleted(server)
        with real(con):
            yield

    monkeypatch.setattr(portable, "_transaction", late)
    command.compact()
    assert ran == [1]
    state = server.compaction_state()
    assert state.deleted_at is not None and state.rewritten is False


def test_the_records_value_is_still_the_first_deletions_time(db):
    """What `stats`, `compact --if-due` and the host's nightly job read: the row's value."""
    store = Store(db)
    try:
        deleted(store)
        deleted(store)
        assert store.compaction_state().deleted_at == T0.isoformat(timespec="microseconds")
    finally:
        store.close()


def test_when_the_last_truncation_is_busy_the_record_is_gone_and_a_later_deletion_is_due(
    two, db, monkeypatch
):
    """After the record is cleared the log is truncated once more, for the clearing's own
    change. When a reader holds the log then, the record stays cleared: the log holds only
    changes made after its truncation, and a deletion among them is recorded again."""
    server, command = two
    deleted(server)
    held: list[sqlite3.Connection] = []
    real = server._write

    @contextmanager
    def then_hold(*args, **kwargs):
        with real(*args, **kwargs) as con:
            yield con
        if not held:
            deleted(command)  # after the clearing's generation was read: a new one
            reader = sqlite3.connect(db)
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM settings").fetchone()
            held.append(reader)

    monkeypatch.setattr(server, "_write", then_hold)
    try:
        server.compact()  # no error: the rewrite and its log are done
    finally:
        for reader in held:
            reader.rollback()
            reader.close()
    state = server.compaction_state()
    assert state.deleted_at is not None and state.rewritten is False  # the later one is due


def test_a_record_an_earlier_build_left_rewritten_is_still_cleared(db):
    """A row whose `updated_at` is the bare marker (as this release's earlier build wrote it)
    still reads as rewritten, and emptying the log clears it."""
    store = Store(db)
    try:
        deleted(store)
        con = sqlite3.connect(db)
        with con:
            con.execute(
                "UPDATE settings SET updated_at = ? WHERE name = ?",
                (base.REWRITTEN, base.COMPACT_DUE),
            )
        con.close()
        assert store.compaction_state().rewritten is True
        store.empty_log()
        assert store.compaction_state().deleted_at is None
    finally:
        store.close()
