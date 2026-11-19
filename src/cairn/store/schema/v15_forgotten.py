"""Schema step 15: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# Forgotten notes (H5b; store/erasure.py). The SHA-256 digest of each note forgotten in a
# project, so the same text is never stored in that project again; never the text itself. Rows go
# with their project and their workspace, and are never exported.
_V15_FORGOTTEN = """CREATE TABLE forgotten (
    project_id TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,  -- hex, as notes.text_sha256
    PRIMARY KEY (project_id, text_sha256)
) WITHOUT ROWID"""


def _add_forgotten(con: sqlite3.Connection) -> None:
    """Schema 14 to _FORGOTTEN: the digests of forgotten notes, empty. Nothing that existed
    changes."""
    con.execute(_V15_FORGOTTEN)
