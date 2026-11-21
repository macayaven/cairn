"""Schema step 21: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# S2: a source can be off, and it remembers when it was last switched on (what its kind
# may read from).
_V21_SOURCES = """CREATE TABLE sources (
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
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),  -- 0: off, no run reads it
    enabled_at TEXT,  -- when it was last switched on; NULL while it never was
    UNIQUE (account_id, name)
)"""


def _add_source_switch(con: sqlite3.Connection) -> None:
    """Schema _MANAGE_LEVEL to _SOURCE_SWITCH (S2): a source is on or off, and records when
    it was last switched on. Every source that exists read from the moment it was added, so
    each is on, switched on at its `created_at`. Sources are rebuilt (as in _add_kinds) so
    that a migrated schema reads exactly like a fresh one; every other column is kept, a
    claim a run holds included."""
    columns = (
        "seq, account_id, name, kind, config, cursor, created_at, last_run_at, last_report,"
        " last_error, claim, claimed_at"
    )
    con.execute("ALTER TABLE sources RENAME TO sources_v20")
    con.execute(_V21_SOURCES)
    con.execute(
        f"INSERT INTO sources ({columns}, enabled, enabled_at)"
        f" SELECT {columns}, 1, created_at FROM sources_v20 ORDER BY seq"
    )
    con.execute("DROP TABLE sources_v20")
