"""Schema step 17: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3


def _end_key_sessions(con: sqlite3.Connection) -> None:
    """Schema _DISK_BOUNDS to _KEY_SESSIONS: no table changes. From now on revoking a key
    ends the browser sessions opened with it, found by the key's id (`sessions.key_id`,
    kept since schema 7). A session opened with a key that this step cannot tie to a key
    that still exists ends here: one recorded before key ids were kept (a label and no key
    id), and one whose key was revoked before this version, which outlived it. Its browser
    signs in again. Sessions opened through an identity provider (no label) are kept."""
    con.execute(
        "DELETE FROM sessions WHERE label IS NOT NULL"
        " AND (key_id IS NULL OR key_id NOT IN (SELECT id FROM api_keys))"
    )
