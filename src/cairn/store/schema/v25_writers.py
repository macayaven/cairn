"""Schema step 25: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

from cairn.store.schema.shared import _STAMP

# Writers (A1). Who wrote each note, one row per note stored since step 25, written in the
# note's own transaction: the kind of writer and its name (cairn.store.audit.WRITER_KINDS).
# A note with no row was stored before (its writer is "unknown"). Rows go with their note,
# project and workspace, and an export carries them.
_V25_NOTE_WRITERS = """CREATE TABLE note_writers (
    note_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('person', 'key', 'agent', 'source', 'operator')),
    name TEXT  -- never an address; NULL for the operator, and for a source's imported note
) WITHOUT ROWID"""
# What a key made (A1): one row for each key made, and each agent approved, from a web
# session opened with a key, naming that key by its id. `id` is the new key's id, the
# grant's, or "code:" and the digest of an approval not yet redeemed (the row moves to the
# grant when the code is). A key or grant made any other way has no row. A row outlives the
# key it names, so what a revoked key made is still found through it; rows go with their
# workspace, and a code's row with its code.
_V25_MADE_WITH = """CREATE TABLE made_with (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    key_id TEXT NOT NULL  -- the id of the key whose session made it
) WITHOUT ROWID"""
_V25_MADE_WITH_BY_KEY = "CREATE INDEX made_with_by_key ON made_with (key_id)"
# The moment writers and makers began to be recorded, by the store's clock: when step 25 ran,
# or when the database was made. A key made before it may have made what nothing records.
_V25_RECORDED_SINCE = """CREATE TABLE recorded_since (
    at TEXT NOT NULL
)"""
_V25_WRITERS = (_V25_NOTE_WRITERS, _V25_MADE_WITH, _V25_MADE_WITH_BY_KEY, _V25_RECORDED_SINCE)


def _add_writers(con: sqlite3.Connection) -> None:
    """Schema _TOPIC_KEYS to _WRITERS (A1): who wrote each note, and the key each key or
    agent's grant was made with, both empty, so every note that exists has no recorded writer
    ("unknown") and every key and grant that exists records no maker; and the moment this
    step ran, read from the store's clock, by which a key made before it is known to have
    made what nothing records. Nothing that existed changes."""
    for statement in _V25_WRITERS:
        con.execute(statement)
    con.execute("INSERT INTO recorded_since (at) VALUES (?)", (_STAMP.get()(),))
