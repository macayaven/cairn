"""Unit tests: a read of the store does not wait for a write (review, architecture 1).

Reads run on read-only connections of their own (base.Readers); writes keep the one writer
connection and its lock. Every test that shows a read not waiting holds the other side open
with an event the test releases, never with timing: SAFETY only bounds how long a test that
fails waits before it says so. Fixtures are synthetic.
"""

import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cairn.store import Store

SAFETY = 10.0  # seconds a failing test waits before it fails; a passing one never waits it
WEEKLY = "# Weekly\n\n- Brook owns the rollback.\n"


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


class Background:
    """`work` on a thread of its own: `done` is set when it has returned or raised."""

    def __init__(self, work: Callable[[], object]) -> None:
        self.result: object = None
        self.error: BaseException | None = None
        self.done = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(work,), daemon=True)
        self._thread.start()

    def _run(self, work: Callable[[], object]) -> None:
        try:
            self.result = work()
        except BaseException as error:  # handed to the test
            self.error = error
        finally:
            self.done.set()

    def alive(self) -> bool:
        return self._thread.is_alive()

    def join(self) -> object:
        self._thread.join(SAFETY)
        if self.error is not None:
            raise self.error
        return self.result


class HeldWrite:
    """A write of `view` held open inside its transaction until `release`."""

    def __init__(self, view: Store, inside: Callable[[sqlite3.Connection], None] = lambda c: None):
        self.inside_done = threading.Event()
        self._release = threading.Event()

        def write() -> None:
            with view._write() as con:
                inside(con)
                self.inside_done.set()
                self._release.wait(SAFETY)

        self.thread = Background(write)
        assert self.inside_done.wait(SAFETY), "the write never began"

    def release(self) -> None:
        self._release.set()
        self.thread.join()


# 1. A read does not wait for a write of any workspace


@pytest.mark.parametrize("writer", ["same workspace", "another workspace"])
def test_a_read_returns_while_a_write_is_held_open(store, writer):
    store.create_project("alpha")
    other = store.for_account(store.create_account("Fable Works").id)
    held = HeldWrite(store if writer == "same workspace" else other)
    try:
        read = Background(store.list_projects)
        assert read.done.wait(SAFETY), "the read waited for the write"
        assert held.thread.alive()  # the write is still open
        assert [p.name for p in read.join()] == ["alpha"]
    finally:
        held.release()


# 2. A read sees every write that returned before it began, on any thread, through any view


def test_a_read_sees_each_write_that_returned_before_it_on_other_threads_and_views(store):
    ada = store.create_account("Fable Works")
    writing, reading = store.for_account(ada.id), store.for_account(ada.id)
    project = Background(lambda: writing.create_project("north")).join()
    narrowed = store.for_account(ada.id).narrow([project.id], "read")
    for n in range(1, 21):
        Background(lambda n=n: writing.add_note(project.id, f"- Row {n} holds pears.\n")).join()
        seen = [
            Background(lambda view=view: view.count_notes(project.id)).join()
            for view in (reading, store.for_account(ada.id), narrowed)
        ]
        assert seen == [n, n, n]
    assert [p.name for p in Background(reading.list_projects).join()] == ["north"]


# 3. Reads inside a write keep seeing that write's own changes


def test_a_read_inside_a_write_sees_the_write_and_a_read_beside_it_does_not(store):
    project = store.create_project("alpha", description="before")

    def inside(con: sqlite3.Connection) -> None:
        con.execute("UPDATE projects SET description = 'during' WHERE id = ?", (project.id,))
        (seen,) = con.execute(
            "SELECT description FROM projects WHERE id = ?", (project.id,)
        ).fetchone()
        assert seen == "during"  # the connection it was handed holds its own change

    held = HeldWrite(store, inside)
    try:
        beside = Background(lambda: store.get_project("alpha").description)
        assert beside.done.wait(SAFETY)
        assert beside.join() == "before"  # not committed yet: another read does not see it
    finally:
        held.release()
    assert Background(lambda: store.get_project("alpha").description).join() == "during"


# 4. The truncating checkpoint after a deletion, and compaction, do not make readers wait


def held_by_another_reader(db: Path) -> sqlite3.Connection:
    """Another process reading the file, as a replica does: a read transaction left open."""
    other = sqlite3.connect(db, isolation_level=None)
    other.execute("BEGIN")
    other.execute("SELECT count(*) FROM projects").fetchone()
    return other


def checkpoint_begins(store: Store) -> threading.Event:
    """Set when the writer starts its truncating checkpoint, whose busy wait follows: SQLite
    reports a statement to the trace callback as it starts it."""
    began = threading.Event()

    def traced(statement: str) -> None:
        if statement == "PRAGMA wal_checkpoint(TRUNCATE)":
            began.set()

    store._con.set_trace_callback(traced)  # the writer's own connection: it checkpoints
    return began


@pytest.mark.parametrize("deletion", ["forget a note", "compact"])
def test_a_read_returns_while_the_truncating_checkpoint_waits(store, db, deletion):
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, WEEKLY)
    store._con.execute("PRAGMA busy_timeout = 60000")  # the wait cannot run out in the test
    other = held_by_another_reader(db)
    began = checkpoint_begins(store)
    deleting = Background(
        (lambda: store.forget_note(note.id)) if deletion == "forget a note" else store.compact
    )
    try:
        assert began.wait(SAFETY), "the checkpoint never began"
        read = Background(store.list_projects)
        assert read.done.wait(SAFETY), "the read waited for the checkpoint"
        assert deleting.alive()  # still in its busy wait: the other reader holds the log
        assert [p.name for p in read.join()] == ["alpha"]
    finally:
        other.execute("COMMIT")
        other.close()
    deleting.join()
    store._con.set_trace_callback(None)
    assert (db.parent / f"{db.name}-wal").stat().st_size == 0  # emptied once it was let go


# 6. The health check answers within its limit while a write is held


def test_health_answers_busy_within_its_limit_while_a_write_is_held(store):
    held = HeldWrite(store)
    try:
        answer = Background(lambda: store.health(wait=0.05))
        assert answer.done.wait(SAFETY)
        assert answer.join() == "busy"
    finally:
        held.release()
    assert store.health() == "ok"
