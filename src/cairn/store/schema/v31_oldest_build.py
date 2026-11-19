"""Schema step 31: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator

from cairn.store.schema.v06_audit import _V6_SETTINGS
from cairn.store.schema.v07_actor_ids import _V7_AUDIT_EVENTS_BY_NUMBER

# From _OLDEST_BUILD, each event carries the value that chains it to the event before it in
# its workspace (cairn.store.base.audit_link; StoreBase._audit writes it).
_V31_AUDIT_EVENTS = """CREATE TABLE audit_events (
    seq INTEGER PRIMARY KEY,  -- the order of events, even if the clock steps back
    account_id TEXT NOT NULL,
    n INTEGER NOT NULL,  -- its number in its workspace, 1, 2, ...: the oldest are pruned
    at TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('key', 'token', 'session', 'system')),
    actor TEXT,  -- the key's label, the agent's client name or the person; NULL for system
    actor_id TEXT,  -- the key's id, the grant's or the person's (names repeat); NULL: system
    action TEXT NOT NULL,
    project TEXT,  -- the project's name when the event is about one
    detail TEXT NOT NULL,  -- a JSON object
    chain TEXT NOT NULL  -- SHA-256 hex over the event before it's chain and this one's fields
)"""
_V31_AUDIT = (_V6_SETTINGS, _V31_AUDIT_EVENTS, _V7_AUDIT_EVENTS_BY_NUMBER)
# The oldest build that runs on this database (_OLDEST_BUILD): one row, the lowest schema
# version whose cairn may open it. A step that only adds leaves it; any other raises it to
# itself (_upgrade). A cairn that meets a newer database reads it before anything else.
_V31_OLDEST_BUILD = """CREATE TABLE oldest_build (
    one INTEGER PRIMARY KEY CHECK (one = 1),  -- a single row
    version INTEGER NOT NULL  -- the lowest schema version whose cairn runs on this database
)"""


def _v31_link(
    previous: str,
    account_id: str,
    n: int,
    seq: int,
    at: str,
    actor_kind: str,
    actor: str | None,
    actor_id: str | None,
    action: str,
) -> str:
    """cairn.store.base.audit_link as _OLDEST_BUILD wrote it: this step's own copy, so a later
    change to the writer's value cannot change what this step wrote."""
    fields = [previous, account_id, n, seq, at, actor_kind, actor, actor_id, action]
    text = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _drop_retired(con: sqlite3.Connection) -> None:
    """Schema _LAST_USE to _OLDEST_BUILD. Three changes:

    - The tables billing kept, which nothing has read or written since cairn sold no plan,
      are dropped (subscriptions, billing_messages and its index, checkouts); any rows in
      them are lost with the step (none should be there).
    - Each audit event gains `chain`, the value that chains it to the event before it in its
      workspace (_v31_link), filled for every event that exists, workspace by workspace in
      the order of its numbers, the first from the start (a workspace pruned before this
      step starts at its oldest event kept). audit_events is rebuilt (as in _add_actor_ids)
      so that a migrated schema reads exactly like a fresh one; every event keeps its
      sequence and every field. The events are read from the renamed table through a
      cursor, one at a time, as they are written into the new one: the step holds one event
      in memory, whatever the table holds (it read them all into a list first until the
      second review; no database outside the tests had run that).
    - oldest_build is added, and _upgrade records this step's number in it.

    The step changes what an older build relies on (a build of _LAST_USE checks at open for
    the tables dropped here, and writes audit events with no value), so no build before this
    one runs on a database it brought forward: going back to one needs the database as it
    was before this step, a backup."""
    for table in ("subscriptions", "billing_messages", "checkouts"):
        con.execute(f"DROP TABLE {table}")  # and with it its index
    columns = "seq, account_id, n, at, actor_kind, actor, actor_id, action, project, detail"
    con.execute("ALTER TABLE audit_events RENAME TO audit_events_v30")
    con.execute(_V31_AUDIT_EVENTS)
    previous: dict[str, str] = {}

    def chained() -> Iterator[tuple]:
        for row in con.execute(  # one row at a time: executemany takes them as they come
            f"SELECT {columns} FROM audit_events_v30 ORDER BY account_id, n"
        ):
            seq, account_id, n, at, kind, actor, actor_id, action = row[:8]
            value = _v31_link(
                previous.get(account_id, "0" * 64),
                account_id, n, seq, at, kind, actor, actor_id, action,
            )  # fmt: skip
            previous[account_id] = value
            yield (*row, value)

    con.executemany(
        f"INSERT INTO audit_events ({columns}, chain) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        chained(),
    )
    con.execute("DROP TABLE audit_events_v30")  # and with it its index
    con.execute(_V7_AUDIT_EVENTS_BY_NUMBER)
    con.execute(_V31_OLDEST_BUILD)
