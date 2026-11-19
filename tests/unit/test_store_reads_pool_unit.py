"""Unit tests: the store's pool of read-only connections (base.Readers) — its bound, that a
read never stays inside a transaction, the observers every connection takes, and closing.

Threads are held with events the test releases, never with timing: SAFETY only bounds how
long a failing test waits before it says so. Fixtures are synthetic.
"""

import os
import sqlite3
import threading
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cairn.store import Store, StoreUnusable
from cairn.store.base import READERS

SAFETY = 10.0
WEEKLY = "# Weekly\n\n- Brook owns the rollback.\n- Quill ships on Thursday.\n"


def fixed() -> datetime:
    return datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    opened = Store(db, clock=fixed)
    yield opened
    opened.close()


def readers(store: Store) -> list[sqlite3.Connection]:
    return store._state.readers.connections()


def wal(db: Path) -> int:
    return (db.parent / f"{db.name}-wal").stat().st_size


class HeldReads:
    """`n` reads on threads of their own, each held open inside its transaction until the
    test releases it; `entered` records the order things happened in."""

    def __init__(self, store: Store, n: int) -> None:
        self.order: list[str] = []
        self.lent: dict[int, int] = {}  # id(connection) -> reads holding it now
        self.overlap = False
        self._mark = threading.Lock()
        self._release = threading.Semaphore(0)
        self.inside = threading.Semaphore(0)
        self.out = threading.Semaphore(0)  # released as each read has ended
        self.threads = [
            threading.Thread(target=self._read, args=(store, k), daemon=True) for k in range(n)
        ]
        for thread in self.threads:
            thread.start()

    def _read(self, store: Store, k: int) -> None:
        with store._read() as con:
            con.execute("SELECT count(*) FROM projects").fetchone()
            with self._mark:
                self.lent[id(con)] = self.lent.get(id(con), 0) + 1
                self.overlap |= self.lent[id(con)] > 1
                self.order.append(f"read {k}")
            self.inside.release()
            self._release.acquire(timeout=SAFETY)
            with self._mark:
                self.lent[id(con)] -= 1
        self.out.release()

    def note(self, what: str) -> None:
        with self._mark:
            self.order.append(what)

    def release(self, n: int = 1) -> None:
        for _ in range(n):
            self._release.release()

    def join(self) -> None:
        for thread in self.threads:
            thread.join(SAFETY)


# A. A small pool: a bound, and a read that finds every reader lent waits for one


def test_the_pool_opens_at_most_its_bound_and_one_more_read_waits_for_a_reader(store):
    held = HeldReads(store, READERS + 1)
    try:
        for _ in range(READERS):
            assert held.inside.acquire(timeout=SAFETY)
        assert len(readers(store)) == READERS  # the bound, and not one more for the last read
        held.note("one released")
        held.release()
        assert held.inside.acquire(timeout=SAFETY)  # the last read, on the reader let go
    finally:
        held.release(READERS + 1)
        held.join()
    assert held.order.index("one released") == READERS  # nothing read past the bound before it
    assert len(readers(store)) == READERS and not held.overlap  # never two reads on one


def test_one_thread_reading_alone_reads_on_one_connection(store):
    project = store.create_project("alpha")
    for n in range(5):
        store.add_note(project.id, f"- Row {n} holds pears.\n")
        assert store.count_notes(project.id) == n + 1
        store.list_projects()
    assert len(readers(store)) == 1


# C. A reader never stays inside a transaction between calls


def test_the_stores_own_readers_never_keep_the_log_from_being_emptied(store, db, caplog):
    project = store.create_project("team")
    note, _ = store.add_note(project.id, WEEKLY)

    def unread() -> None:  # a cursor left before its last row
        with store._read() as con:
            con.execute("SELECT id FROM projects UNION ALL SELECT id FROM notes").fetchone()

    def failing() -> None:  # a read left by an exception
        with pytest.raises(LookupError), store._read() as con:
            con.execute("SELECT id FROM notes").fetchone()
            raise LookupError("left inside")

    def plain() -> None:
        assert store.count_notes(project.id) == 1

    held = HeldReads(store, 2)  # two readers lent at once, then given back
    for _ in range(2):
        assert held.inside.acquire(timeout=SAFETY)
    held.release(2)
    held.join()
    for work in (unread, failing, plain, unread):
        thread = threading.Thread(target=work)
        thread.start()
        thread.join(SAFETY)
    assert len(readers(store)) >= 2
    assert not any(con.in_transaction for con in readers(store))
    with caplog.at_level("WARNING", logger="cairn.store"):
        store.forget_note(note.id)
    assert not caplog.records  # the checkpoint was not kept busy
    assert wal(db) == 0  # the log is empty: what was forgotten is not in it


# D. One way to watch every connection of the store


def test_an_observer_sees_the_statements_steps_and_rows_of_reads_and_writes(store):
    statements: list[str] = []
    steps, rows = [0], [0]

    def step() -> int:
        steps[0] += 1
        return 0

    def row(cursor, values):
        rows[0] += 1
        return values

    store.create_project("alpha")
    store.list_projects()  # a reader opened before the observers are attached takes them too
    store._observe(trace=statements.append, progress=(step, 1), rows=row)
    store.create_project("beta")
    assert "BEGIN IMMEDIATE" in statements  # the write, on the writer
    assert any(s.startswith("INSERT INTO projects") for s in statements)
    wrote = (len(statements), steps[0], rows[0])
    seen = [p.name for p in store.list_projects()]  # the read, on a reader
    store._observe(trace=None, progress=None, rows=None)
    assert seen == ["alpha", "beta"]
    read = statements[wrote[0] :]
    assert read[0] == "BEGIN DEFERRED" and any("FROM projects" in s for s in read)
    assert steps[0] > wrote[1] and rows[0] >= wrote[2] + 2
    before = (len(statements), steps[0], rows[0])
    store.list_projects()
    store.create_project("gamma")
    assert (len(statements), steps[0], rows[0]) == before  # removed from every connection


def test_an_observer_left_out_is_kept(store):
    statements: list[str] = []
    store._observe(trace=statements.append)
    store._observe(rows=None)
    store.list_projects()
    store._observe(trace=None)
    assert statements


# E. A reader honours the writer's checks: closed, unusable


def test_closing_the_store_closes_every_reader_and_refuses_reads(store):
    view = store.for_account(store.create_account("Fable Works").id)
    held = HeldReads(store, 3)
    for _ in range(3):
        assert held.inside.acquire(timeout=SAFETY)
    opened = readers(store)
    held.release(2)
    for _ in range(2):  # two have ended; one is still reading
        assert held.out.acquire(timeout=SAFETY)
    store.close()
    assert len(readers(store)) == 1  # the two given back are closed; the one lent is not yet
    held.release()
    held.join()
    assert readers(store) == []  # the read in progress closed its reader as it ended
    for con in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            con.execute("SELECT 1")
    for call in (store.list_projects, view.list_projects):
        with pytest.raises(sqlite3.ProgrammingError):
            call()


def deny(*statements: str):
    def authorizer(action, arg1, *_):
        if action == sqlite3.SQLITE_TRANSACTION and arg1 in statements:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorizer


def test_an_unusable_store_refuses_reads_and_closes_its_readers(store):
    store.list_projects()
    opened = readers(store)
    store._con.set_authorizer(deny("COMMIT", "ROLLBACK"))
    with pytest.raises(StoreUnusable):
        store.create_project("beta")
    with pytest.raises(StoreUnusable):
        store.list_projects()
    assert readers(store) == []
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_a_reader_whose_rollback_fails_makes_the_store_unusable(store):
    store.list_projects()
    (reader,) = readers(store)
    reader.set_authorizer(deny("ROLLBACK"))
    with pytest.raises(StoreUnusable), store._read() as con:
        assert con is reader
        raise LookupError("a read that fails, whose rollback fails too")
    for call in (store.list_projects, lambda: store.create_project("gamma")):
        with pytest.raises(StoreUnusable):
            call()
    assert readers(store) == [] and store.health() == "unavailable"


# 5. Databases a second connection cannot open, and what the file looks like


@pytest.mark.parametrize("path", [":memory:", ""], ids=["memory", "temporary"])
def test_a_database_no_second_connection_can_open_reads_on_the_writer(path):
    store = Store(path, clock=fixed)
    try:
        assert store._state.readers is None
        project = store.create_project("alpha")
        store.add_note(project.id, WEEKLY)
        assert store.count_notes(project.id) == 1
        assert [p.name for p in store.for_account("acct_default").list_projects()] == ["alpha"]
    finally:
        store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        store.list_projects()


def test_a_path_with_characters_a_uri_reserves_is_read_the_same(tmp_path):
    folder = tmp_path / "Fable Works #2 ?50% & more"
    folder.mkdir()
    with ExitStack() as stack:
        store = Store(folder / "cairn.db", clock=fixed)
        stack.callback(store.close)
        store.create_project("alpha")
        assert [p.name for p in store.list_projects()] == ["alpha"]
        assert len(readers(store)) == 1


def test_readers_make_no_file_and_change_no_mode(store, db):
    store.create_project("alpha")
    os.chmod(db, 0o600)
    before = {p.name: p.stat().st_mode for p in db.parent.iterdir()}
    for _ in range(3):
        store.list_projects()
    assert {p.name: p.stat().st_mode for p in db.parent.iterdir()} == before
    assert store._con.execute("PRAGMA secure_delete").fetchone() == (1,)
    with pytest.raises(sqlite3.OperationalError), store._read() as con:
        con.execute("CREATE TABLE written (x)")  # a reader cannot write


def test_closing_after_reads_leaves_the_file_whole_as_before(db):
    """The readers close before the writer, so the writer is the last connection to the file:
    it copies the write-ahead log into the file and removes it, as when it was the only one."""
    store = Store(db, clock=fixed)
    store.create_project("alpha")
    store.list_projects()
    assert len(readers(store)) == 1
    store.close()
    assert not (db.parent / f"{db.name}-wal").exists()
    before = db.read_bytes()
    Store(db, clock=fixed).close()
    assert db.read_bytes() == before
