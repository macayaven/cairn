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


class Counted:
    """A cursor that notes how many rows each fetch hands over at once."""

    def __init__(self, cursor: sqlite3.Cursor, seen: list[int]) -> None:
        self._cursor, self._seen = cursor, seen

    def fetchall(self) -> list[Any]:
        rows = self._cursor.fetchall()
        self._seen.append(len(rows))
        return rows

    def fetchmany(self, size: int = 1) -> list[Any]:
        rows = self._cursor.fetchmany(size)
        self._seen.append(len(rows))
        return rows

    def fetchone(self) -> Any:
        row = self._cursor.fetchone()
        self._seen.append(row is not None)
        return row

    def __iter__(self):
        for row in self._cursor:
            self._seen.append(1)
            yield row

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class Counting(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seen: list[int] = []

    def execute(self, sql: str, parameters: Any = (), /) -> Any:
        return Counted(super().execute(sql, parameters), self.seen)


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


def step_31(db: Path) -> list[int]:
    con = sqlite3.connect(db, factory=Counting, isolation_level=None)
    try:
        con.execute("BEGIN")
        schema._MIGRATIONS[31](con)
        con.execute("PRAGMA user_version = 31")
        con.execute("COMMIT")
        return con.seen
    finally:
        con.close()


@pytest.mark.parametrize("count", [10, 2_000])
def test_step_31_holds_a_bounded_number_of_events_at_once(tmp_path, count):
    db = tmp_path / "cairn.db"
    with_events(db, count)
    seen = step_31(db)
    assert max(seen) <= BOUND, max(seen)
    with connect(db) as con:
        (found,) = check_trail(con, DEFAULT_ACCOUNT_ID)
    assert found.checked == count and found.broken is None
