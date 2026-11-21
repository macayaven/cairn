"""Each deletion gives the compaction record a generation of its own (second review's second
report, finding 6). A rewrite marks the generation it read; emptying the log clears only the
generation whose log it emptied, in one statement that changes nothing once the record has
moved on. So emptying the log in one process never clears what another process's later
rewrite left due, and two deletions recorded with one clock value are two generations. Two
stores on one file stand for two processes; a reader that holds the write-ahead log stands
for a replica; each step is placed by a patched call, never by timing. The clock is pinned."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from cairn.store import CompactIncomplete, CompactionRunning, Store, base, portable

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


@pytest.mark.parametrize("through", ["compact", "empty_log"])
def test_emptying_the_log_does_not_clear_what_a_later_deletion_left_due(
    two, db, monkeypatch, through
):
    """Between the server's truncation of the log and its clearing, another process deletes
    a workspace (a generation of its own) and tries to compact, which is refused while the
    server's compaction runs; a reader holds the log from before that deletion through the
    server's clearing and its last truncation. The deletion stays due, and its change is
    still in the log."""
    server, command = two
    deleted(server)
    if through == "empty_log":  # rewritten; only the log is due
        with replica(db), pytest.raises(CompactIncomplete):
            server.compact()
        assert server.compaction_state().rewritten is True
    readers: list[sqlite3.Connection] = []

    def the_other_process():
        reader = sqlite3.connect(db)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM settings").fetchone()
        readers.append(reader)  # held through the server's clearing and last truncation
        deleted(command)  # another workspace goes
        with pytest.raises(CompactionRunning):
            command.compact()

    ran = before_the_first_write(server, monkeypatch, the_other_process)
    try:
        server.compact() if through == "compact" else server.empty_log()
        assert ran == [1]
        state = server.compaction_state()
        assert state.deleted_at is not None and state.rewritten is False  # still due
        assert wal_size(db) > 0  # and its change is still in the log
    finally:
        for reader in readers:
            reader.rollback()
            reader.close()


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


def test_the_records_value_is_still_the_first_deletions_time(db, monkeypatch):
    """What `stats`, `compact --if-due` and the host's nightly job read: the row's value.
    The two deletions have different times, so a second that overwrote the first would show."""
    store = Store(db)
    try:
        deleted(store)
        first = generation(db)
        monkeypatch.setattr(base, "_clock", lambda: T0 + timedelta(hours=1))
        deleted(store)
        assert generation(db) != first
        assert store.compaction_state().deleted_at == T0.isoformat(timespec="microseconds")
    finally:
        store.close()


@pytest.mark.parametrize("deleting", [False, True])
def test_when_the_last_truncation_is_busy(two, db, monkeypatch, caplog, deleting):
    """After the record is cleared the log is truncated once more, for the clearing's own
    change. With a reader holding the log then, one line says so, nothing is raised, and the
    record stays as it is: cleared when nothing came since (the log keeps the clearing only),
    due again when a deletion came after the clearing."""
    import logging

    server, command = two
    deleted(server)
    held: list[sqlite3.Connection] = []
    real = server._write

    @contextmanager
    def then_hold(*args, **kwargs):
        with real(*args, **kwargs) as con:
            yield con
        if not held:
            reader = sqlite3.connect(db)
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM settings").fetchone()
            held.append(reader)
            if deleting:  # after the clearing, with the log held: a generation of its own
                deleted(command)

    monkeypatch.setattr(server, "_write", then_hold)
    try:
        with caplog.at_level(logging.INFO, logger="cairn.store"):
            server.compact()  # no error: the rewrite and its log are done
        assert wal_size(db) > 0  # the last truncation was refused
    finally:
        for reader in held:
            reader.rollback()
            reader.close()
    said = [
        r.getMessage()
        for r in caplog.records
        if r.name == "cairn.store" and r.getMessage().startswith("compact")
    ]
    assert said == [
        "compact: the write-ahead log keeps the changes made after it was emptied: another"
        " connection held it"
    ]
    state = server.compaction_state()
    if deleting:
        assert state.deleted_at is not None and state.rewritten is False
    else:
        assert state.deleted_at is None


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


RUN_SAYS = (
    "a rewrite is not repeated for the same deletion once it is recorded (one killed before"
    " its record runs again)"
)


def test_the_run_guide_says_a_rewrite_is_not_repeated_once_recorded_and_runs_it(
    two, db, monkeypatch
):
    """The sentence, run as it says, through the decision the server's loop takes (due) and
    then its run: a recorded rewrite whose log was held is not done again by the next try;
    one killed before its record is, once the loop finds it due."""
    from guide_source import guide_source

    from cairn.web.compactor import Compactor

    assert " ".join(guide_source("run").split()).count(RUN_SAYS) == 1
    server, _ = two
    vacuums: list[str] = []
    server._observe(trace=lambda s: vacuums.append(s) if s.strip().upper() == "VACUUM" else None)
    deleted(server)
    with replica(db), pytest.raises(CompactIncomplete):
        server.compact()  # recorded: rewritten, only the log is due
    ticks = [0.0]
    compactor = Compactor(server, hours=24, clock=lambda: T0, ticks=lambda: ticks[0])
    assert compactor.due() is True  # only the log is left: due at once
    assert compactor.run_once() is True
    assert len(vacuums) == 1  # the log alone: no second rewrite

    deleted(server)

    class Killed(BaseException):
        pass

    @contextmanager
    def killed(con):
        raise Killed
        yield  # pragma: no cover

    real = portable._transaction
    monkeypatch.setattr(portable, "_transaction", killed)
    with pytest.raises(Killed):
        server.compact()  # rewritten, and killed before its record
    monkeypatch.setattr(portable, "_transaction", real)
    assert server.compaction_state().rewritten is False  # a rewrite is still due
    day = 24 * 3600.0
    assert compactor.due() is False  # the interval since the last run has not passed
    ticks[0] += day
    assert compactor.due() is False  # nor since the last rewrite began, placed by its age
    ticks[0] += day
    assert compactor.due() is True
    assert compactor.run_once() is True
    assert len(vacuums) == 3  # it ran again
    assert server.compaction_state().deleted_at is None


def wal_size(db) -> int:
    import os

    path = f"{db}-wal"
    return os.path.getsize(path) if os.path.exists(path) else 0


def test_stats_and_compact_if_due_read_a_record_a_real_rewrite_made(db, capsys):
    """What the record now holds, read by the two commands that read it: `stats --json`
    says only the log is left, and `compact --if-due` empties the log alone; the host's
    nightly job reads the row's value, still the first deletion's time."""
    import json

    from cairn.web import cli

    store = Store(db)
    store._con.execute("PRAGMA busy_timeout = 0")
    try:
        deleted(store)
        with replica(db), pytest.raises(CompactIncomplete):
            store.compact()
        assert generation(db).startswith(f"{base.REWRITTEN}:")
    finally:
        store.close()
    con = sqlite3.connect(db)
    (value,) = con.execute(
        "SELECT value FROM settings WHERE name = ?", (base.COMPACT_DUE,)
    ).fetchone()
    con.close()
    assert value == T0.isoformat(timespec="microseconds")  # what host.sh reads
    capsys.readouterr()
    assert cli.main(["stats", "--db", str(db), "--json"]) == 0
    compaction = json.loads(capsys.readouterr().out)["compaction"]
    assert compaction["rewritten"] is True and compaction["deleted_at"] == value
    assert cli.main(["compact", "--if-due", "--db", str(db)]) == 0
    assert "emptied the write-ahead log" in capsys.readouterr().out
    assert generation(db) is None  # done: the record goes


@pytest.mark.parametrize("through", ["compact", "empty_log"])
def test_a_rewrite_with_no_new_deletion_between_truncation_and_clearing_stays_due(
    two, db, monkeypatch, through
):
    """The mark of each rewrite, apart from the lock: the other store's lock is a no-op here,
    so its rewrite (with no new deletion) can land between the server's truncation and its
    clearing, as two compactions could before the lock. A reader holds the log from then
    on, through the server's clearing and last truncation. Its rewrite is in the log and
    stays due: the server clears only its own mark."""
    import contextlib as _contextlib

    server, command = two
    deleted(server)
    with replica(db), pytest.raises(CompactIncomplete):
        server.compact()  # rewritten; only the log is due
    monkeypatch.setattr(command, "compacting", _contextlib.nullcontext)
    readers: list[sqlite3.Connection] = []

    def the_other_process():
        reader = sqlite3.connect(db)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM settings").fetchone()
        readers.append(reader)
        with pytest.raises(CompactIncomplete):
            command.compact()  # no new deletion: a rewrite of its own, its log held

    before_the_first_write(server, monkeypatch, the_other_process)
    try:
        server.compact() if through == "compact" else server.empty_log()
        state = server.compaction_state()
        assert wal_size(db) > 0  # the other rewrite is still in the log
        assert state.deleted_at is not None and state.rewritten is True
    finally:
        for reader in readers:
            reader.rollback()
            reader.close()
