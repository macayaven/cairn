"""Schema step 30: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

_V30_SESSIONS = """CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL,
    user_id TEXT,  -- the person who signed in; NULL for a session opened with an API key
    label TEXT,  -- the label of the API key it was opened with; NULL otherwise
    key_id TEXT,  -- the id of the API key it was opened with; NULL otherwise
    vouched_email TEXT,  -- the address the provider vouched for at the sign-in it came from
    used_at TEXT  -- its last use recorded (at most once a minute, with --idle-minutes); NULL: none
)"""


def _add_last_use(con: sqlite3.Connection) -> None:
    """Schema _SLACK to _LAST_USE: a session records its last use (`used_at`), which `serve
    --idle-minutes` ends it by. Sessions are rebuilt (as in _add_vouched_email) so that a
    migrated schema reads exactly like a fresh one; every session that exists is kept as it
    was, with no use recorded: its last use reads as when it was made. It only adds
    (_ONLY_ADDS): the rebuilt table is the old one with one more column, which may be NULL; a
    session an older build writes records no use, read as when it was made."""
    columns = "digest, account_id, expires_at, created_at, user_id, label, key_id, vouched_email"
    con.execute("ALTER TABLE sessions RENAME TO sessions_v29")
    con.execute(_V30_SESSIONS)
    con.execute(
        f"INSERT INTO sessions ({columns}) SELECT {columns} FROM sessions_v29 ORDER BY rowid"
    )
    con.execute("DROP TABLE sessions_v29")
