"""Schema step 22: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

_V22_SESSIONS = """CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL,
    user_id TEXT,  -- the person who signed in; NULL for a session opened with an API key
    label TEXT,  -- the label of the API key it was opened with; NULL otherwise
    key_id TEXT,  -- the id of the API key it was opened with; NULL otherwise
    vouched_email TEXT  -- the address the provider vouched for at the sign-in it came from
)"""


def _add_vouched_email(con: sqlite3.Connection) -> None:
    """Schema _SOURCE_SWITCH to _VOUCHED_EMAIL (B1): a session records the address the
    identity provider vouched for at the sign-in it was made by, or that the session it was
    made from carries (switching workspace, taking an invitation), so `session-end` finds a
    person by the address the allowlist checks now, not only by the one they first signed in
    with. Sessions are rebuilt (as in _add_actor_ids) so that a migrated schema reads exactly
    like a fresh one; every session that exists is kept as it was, with no address: one made
    before this step is found by its person's stored address only."""
    columns = "digest, account_id, expires_at, created_at, user_id, label, key_id"
    con.execute("ALTER TABLE sessions RENAME TO sessions_v21")
    con.execute(_V22_SESSIONS)
    con.execute(
        f"INSERT INTO sessions ({columns}) SELECT {columns} FROM sessions_v21 ORDER BY rowid"
    )
    con.execute("DROP TABLE sessions_v21")
