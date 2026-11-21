"""Schema step 6: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

_V6_SESSIONS = """CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL,
    user_id TEXT,  -- the person who signed in; NULL for a session opened with an API key
    label TEXT  -- the label of the API key it was opened with; NULL otherwise
)"""
# A workspace's settings (cairn.store.audit.SETTINGS), and its audit log: its writes and
# security events, one row each, written in the transaction of the write it records. Names,
# ids and counts only: never note text, a secret or a digest.
_V6_SETTINGS = """CREATE TABLE settings (
    account_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, name)
) WITHOUT ROWID"""
_V6_AUDIT_EVENTS = """CREATE TABLE audit_events (
    seq INTEGER PRIMARY KEY,  -- the order of events, even if the clock steps back
    account_id TEXT NOT NULL,
    at TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('key', 'token', 'session', 'system')),
    actor TEXT,  -- the key's label, the agent's client name or the person; NULL for system
    action TEXT NOT NULL,
    project TEXT,  -- the project's name when the event is about one
    detail TEXT NOT NULL  -- a JSON object
)"""
_V6_AUDIT_EVENTS_BY_ACCOUNT = (
    "CREATE INDEX audit_events_by_account ON audit_events (account_id, seq)"
)
_V6_AUDIT = (_V6_SETTINGS, _V6_AUDIT_EVENTS, _V6_AUDIT_EVENTS_BY_ACCOUNT)


def _add_audit(con: sqlite3.Connection) -> None:
    """Schema 5 to 6: settings and the audit log, empty, and sessions gain the label of the key
    they were opened with, empty on every session that existed. Sessions are rebuilt (as in
    _add_accounts) so that a migrated schema reads exactly like a fresh one."""
    for statement in _V6_AUDIT:
        con.execute(statement)
    columns = "digest, account_id, expires_at, created_at, user_id"
    con.execute("ALTER TABLE sessions RENAME TO sessions_v5")
    con.execute(_V6_SESSIONS)
    con.execute(
        f"INSERT INTO sessions ({columns}) SELECT {columns} FROM sessions_v5 ORDER BY rowid"
    )
    con.execute("DROP TABLE sessions_v5")
