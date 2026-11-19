"""Step 31 holds a bounded number of audit events in memory, whatever the table holds (second
review, should-fix 13). It chains every event already stored; it reads them from the renamed
table through a cursor, one row at a time, as it writes them into the new one, instead of
reading the whole table into a list first. Counted in rows fetched at once through a wrapped
cursor, never in bytes or time. Made-up events."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from test_schema_floor_unit import v30_database
from test_store_unit import connect

import cairn.store.schema as schema
from cairn.store import DEFAULT_ACCOUNT_ID
from cairn.store.audit import check_trail

# The most events step 31 holds at once: the one it is chaining.
BOUND = 1


class Held:
    """How many rows have been read from a cursor and not yet handed to an insert, at most:
    a list made from the cursor holds them all, a stream one."""

    def __init__(self) -> None:
        self.read = self.written = 0
        self.peak = 0

    def note(self, read: int) -> None:
        self.read += read
        self.peak = max(self.peak, self.read - self.written)


class Counted:
    """A cursor that counts the rows each fetch hands over."""

    def __init__(self, cursor: sqlite3.Cursor, held: Held) -> None:
        self._cursor, self._held = cursor, held

    def fetchall(self) -> list[Any]:
        rows = self._cursor.fetchall()
        self._held.note(len(rows))
        return rows

    def fetchmany(self, size: int = 1) -> list[Any]:
        rows = self._cursor.fetchmany(size)
        self._held.note(len(rows))
        return rows

    def fetchone(self) -> Any:
        row = self._cursor.fetchone()
        self._held.note(row is not None)
        return row

    def __iter__(self):
        for row in self._cursor:
            self._held.note(1)
            yield row

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class Counting(sqlite3.Connection):
    """Reads through Counted; each row executemany takes is one no longer held."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.held = Held()

    def execute(self, sql: str, parameters: Any = (), /) -> Any:
        return Counted(super().execute(sql, parameters), self.held)

    def executemany(self, sql: str, parameters: Any, /) -> sqlite3.Cursor:
        def taken():
            for row in parameters:
                self.held.written += 1
                yield row

        return super().executemany(sql, taken())


def with_events(db: Path, count: int) -> None:
    v30_database(db)
    with connect(db) as con:
        con.execute("DELETE FROM audit_events")
        con.executemany(
            "INSERT INTO audit_events (account_id, n, at, actor_kind, actor, actor_id, action,"
            " project, detail) VALUES (?, ?, ?, 'key', 'laptop', 'k1', 'note.saved', 'ops',"
            ' \'{"page": "p"}\')',
            (
                (DEFAULT_ACCOUNT_ID, n, f"2026-01-01T00:00:{n % 60:02d}")
                for n in range(1, count + 1)
            ),
        )
        con.commit()


def step_31(db: Path) -> int:
    con = sqlite3.connect(db, factory=Counting, isolation_level=None)
    try:
        con.execute("BEGIN")
        schema._MIGRATIONS[31](con)
        con.execute("PRAGMA user_version = 31")
        con.execute("COMMIT")
        return con.held.peak
    finally:
        con.close()


@pytest.mark.parametrize("count", [10, 2_000])
def test_step_31_holds_a_bounded_number_of_events_at_once(tmp_path, count):
    db = tmp_path / "cairn.db"
    with_events(db, count)
    peak = step_31(db)
    assert peak <= BOUND, peak
    with connect(db) as con:
        (found,) = check_trail(con, DEFAULT_ACCOUNT_ID)
    assert found.checked == count and found.broken is None
