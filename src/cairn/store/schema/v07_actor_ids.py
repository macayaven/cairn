"""Schema step 7: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import secrets
import sqlite3

from cairn.store.schema.v05_reach import _V5_API_KEYS_BY_LABEL
from cairn.store.schema.v06_audit import _V6_SETTINGS

_V7_API_KEYS = """CREATE TABLE api_keys (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,  -- "key_" + 12 hex chars: the key in the audit log; never reused
    digest TEXT NOT NULL UNIQUE,  -- SHA-256 hex of the key; the key itself is never stored
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    projects TEXT,  -- JSON array of the project and set ids it reaches; NULL: every project
    access TEXT NOT NULL CHECK (access IN ('write', 'read'))
)"""
_V7_SESSIONS = """CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL,
    user_id TEXT,  -- the person who signed in; NULL for a session opened with an API key
    label TEXT,  -- the label of the API key it was opened with; NULL otherwise
    key_id TEXT  -- the id of the API key it was opened with; NULL otherwise
)"""
_V7_AUDIT_EVENTS = """CREATE TABLE audit_events (
    seq INTEGER PRIMARY KEY,  -- the order of events, even if the clock steps back
    account_id TEXT NOT NULL,
    n INTEGER NOT NULL,  -- its number in its workspace, 1, 2, ...: the oldest are pruned
    at TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('key', 'token', 'session', 'system')),
    actor TEXT,  -- the key's label, the agent's client name or the person; NULL for system
    actor_id TEXT,  -- the key's id, the grant's or the person's (names repeat); NULL: system
    action TEXT NOT NULL,
    project TEXT,  -- the project's name when the event is about one
    detail TEXT NOT NULL  -- a JSON object
)"""
_V7_AUDIT_EVENTS_BY_NUMBER = (
    "CREATE UNIQUE INDEX audit_events_by_number ON audit_events (account_id, n)"
)
_V7_AUDIT = (_V6_SETTINGS, _V7_AUDIT_EVENTS, _V7_AUDIT_EVENTS_BY_NUMBER)


def _add_actor_ids(con: sqlite3.Connection) -> None:
    """Schema 6 to 7: every key gets an id of its own ("key_" + 12 hex chars), sessions the
    id of the key they were opened with (empty on every session that existed: their key is
    not known by id), and audit events the id of their actor (empty on every event already
    recorded) and their number in their workspace (counted in the order they were
    recorded), indexed by it instead of by seq. Each table is rebuilt (as in _add_accounts),
    so that a migrated schema reads exactly like a fresh one."""
    con.execute("ALTER TABLE api_keys RENAME TO api_keys_v6")
    con.execute(_V7_API_KEYS)
    columns = "seq, digest, account_id, label, created_at, projects, access"
    for (seq,) in con.execute("SELECT seq FROM api_keys_v6 ORDER BY seq").fetchall():
        con.execute(
            f"INSERT INTO api_keys (id, {columns}) SELECT ?, {columns} FROM api_keys_v6"
            " WHERE seq = ?",
            ("key_" + secrets.token_hex(6), seq),
        )
    con.execute("DROP TABLE api_keys_v6")  # and with it its index
    con.execute(_V5_API_KEYS_BY_LABEL)
    columns = "digest, account_id, expires_at, created_at, user_id, label"
    con.execute("ALTER TABLE sessions RENAME TO sessions_v6")
    con.execute(_V7_SESSIONS)
    con.execute(
        f"INSERT INTO sessions ({columns}) SELECT {columns} FROM sessions_v6 ORDER BY rowid"
    )
    con.execute("DROP TABLE sessions_v6")
    columns = "seq, account_id, at, actor_kind, actor, action, project, detail"
    con.execute("ALTER TABLE audit_events RENAME TO audit_events_v6")
    con.execute(_V7_AUDIT_EVENTS)
    con.execute(
        f"INSERT INTO audit_events (n, {columns})"
        f" SELECT ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY seq), {columns}"
        " FROM audit_events_v6 ORDER BY seq"
    )
    con.execute("DROP TABLE audit_events_v6")  # and with it its index
    con.execute(_V7_AUDIT_EVENTS_BY_NUMBER)
