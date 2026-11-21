"""Schema step 9: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# A workspace's sources (cairn.sources): the config its kind checked (never a credential:
# it names environment variables), where the next run starts, how the last one went, and
# the claim of the run that holds it now (a compare-and-set, seen by every process).
_V9_SOURCES = """CREATE TABLE sources (
    seq INTEGER PRIMARY KEY,  -- creation order
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    kind TEXT NOT NULL,  -- a name of cairn.sources.KINDS
    config TEXT NOT NULL,  -- a JSON object, as the kind checked it
    cursor TEXT,  -- the source's own, opaque; NULL before the first run that worked
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    last_report TEXT,  -- a JSON object: the counts of the last run that worked
    last_error TEXT,  -- why the last run failed; NULL once one works
    claim TEXT,  -- the token of the run that holds the source; NULL when none does
    claimed_at TEXT,
    UNIQUE (account_id, name)
)"""


def _add_sources(con: sqlite3.Connection) -> None:
    """Schema 8 to 9: sources, empty. Nothing that existed changes."""
    con.execute(_V9_SOURCES)
