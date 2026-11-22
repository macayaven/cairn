"""Step 31 holds a bounded number of audit events in memory, whatever the table holds (second
review, should-fix 13). It chains every event already stored; it reads them from the renamed
table through a cursor, one row at a time, as it writes them into the new one, instead of
reading the whole table into a list first. Counted in rows fetched at once through a wrapped
cursor, never in bytes or time. Made-up events."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

import cairn.store.schema as schema
from cairn.store import DEFAULT_ACCOUNT_ID
from cairn.store.audit import check_trail
from support.store.schema_floor import v30_database
from support.store.store import connect

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


# Many workspaces (the second report's remark on 13): the step held the last value of every
# workspace it had read, so its memory grew with their number. Measured as the items held by
# the containers of the generator executemany reads from (its locals and the values it
# closes over), sampled at every row taken: the same at 3 workspaces as at 600.


class Sampling(sqlite3.Connection):
    """executemany here notes, at each row it takes, how many items the producer's own
    containers hold."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.most = 0
        self.sampled = 0  # rows at which the producer's frame was read

    def executemany(self, sql: str, parameters: Any, /) -> sqlite3.Cursor:
        def taken():
            for row in parameters:
                frame = getattr(parameters, "gi_frame", None)
                if frame is not None:
                    self.sampled += 1
                    held = sum(
                        len(value)
                        for name, value in frame.f_locals.items()
                        if name != "row" and isinstance(value, dict | list | set | tuple)
                    )
                    self.most = max(self.most, held)
                yield row

        return super().executemany(sql, taken())


def with_workspaces(db: Path, workspaces: int, each: int = 2) -> None:
    v30_database(db)
    with connect(db) as con:
        con.execute("DELETE FROM audit_events")
        con.executemany(
            "INSERT INTO audit_events (account_id, n, at, actor_kind, actor, actor_id, action,"
            " project, detail) VALUES (?, ?, '2026-01-01T00:00:00', 'key', 'laptop', 'k1',"
            " 'note.saved', 'ops', '{}')",
            ((f"acct_{w:012x}", n) for w in range(workspaces) for n in range(1, each + 1)),
        )
        con.commit()


def most_held(db: Path) -> tuple[int, int]:
    con = sqlite3.connect(db, factory=Sampling, isolation_level=None)
    try:
        con.execute("BEGIN")
        schema._MIGRATIONS[31](con)
        con.execute("COMMIT")
        return con.most, con.sampled
    finally:
        con.close()


def test_step_31_holds_the_same_whatever_the_number_of_workspaces(tmp_path):
    few, many = tmp_path / "few.db", tmp_path / "many.db"
    with_workspaces(few, 3)
    with_workspaces(many, 600)
    (most_few, seen_few), (most_many, seen_many) = most_held(few), most_held(many)
    assert (seen_few, seen_many) == (3 * 2, 600 * 2)  # every row taken was sampled
    assert most_many == most_few


def test_each_workspace_still_chains_from_the_start(tmp_path):
    """The one step past: with one value kept, a workspace's first event still starts from
    the start value, not from the workspace read before it."""
    from cairn.store.audit import AUDIT_START, audit_link

    db = tmp_path / "cairn.db"
    with_workspaces(db, 3, each=2)
    most_held(db)
    with connect(db) as con:
        rows = con.execute(
            "SELECT seq, account_id, n, at, actor_kind, actor, actor_id, action, chain"
            " FROM audit_events ORDER BY account_id, n"
        ).fetchall()
    previous: dict[str, str] = {}
    for seq, account, n, at, kind, actor, actor_id, action, chain in rows:
        value = audit_link(
            previous.get(account, AUDIT_START), account, n, seq, at, kind, actor, actor_id, action
        )
        assert chain == value
        previous[account] = value
