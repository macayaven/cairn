"""Schema step 23: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# The items each source wrote (I1; cairn.store.sources, "Items"): one row per source and
# item, so that a draft naming an item its source already wrote is not written again. The
# item is kept as the first 32 hex characters (128 bits) of its SHA-256, never as itself, as
# the kinds' cursors keep digests; `source` is the source's `seq`. Rows go with their source
# (remove_source) and their workspace (delete_account), and are never exported.
# The primary key is the only index, and it is the table (WITHOUT ROWID).
# Size: a row is the source's seq (1 to 3 bytes), the digest (32 bytes) and the time it was
# written (32 bytes, ISO 8601 with microseconds), plus a few bytes of record header and cell
# pointer: about 70 bytes. A million items, written by the sources of one database in any
# order, took 82 MB of pages when measured (SQLite 3, 4 KiB pages, B-tree pages not full).
# A row is never pruned while its source exists: there is one per item ever written, not
# one per run, and the table grows with what the sources wrote.
_V23_SOURCE_ITEMS = """CREATE TABLE source_items (
    source INTEGER NOT NULL,  -- sources.seq of the source that wrote the item
    item TEXT NOT NULL,  -- the first 32 hex characters of the item's SHA-256
    written_at TEXT NOT NULL,  -- when it was recorded, after its note returned
    PRIMARY KEY (source, item)
) WITHOUT ROWID"""


def _add_source_items(con: sqlite3.Connection) -> None:
    """Schema _VOUCHED_EMAIL to _SOURCE_ITEMS (I1): the items each source wrote, empty. No
    source recorded an item before, so every source starts with none; nothing that existed
    changes."""
    con.execute(_V23_SOURCE_ITEMS)
