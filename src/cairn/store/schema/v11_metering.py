"""Schema step 11: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# Metering (H6). What each workspace used, by UTC month, counted in the transaction of the
# write it belongs to; and beside each account, the name of the server's plan it is on and
# the running total of the bytes its notes take, so no cap check counts a corpus.
_V11_USAGE = """CREATE TABLE usage (
    account_id TEXT NOT NULL,
    period TEXT NOT NULL,  -- the UTC month, "YYYY-MM"
    notes INTEGER NOT NULL DEFAULT 0,  -- notes stored; the same note again stores nothing
    note_bytes INTEGER NOT NULL DEFAULT 0,  -- their UTF-8 size
    model_tokens_in INTEGER NOT NULL DEFAULT 0,
    model_tokens_out INTEGER NOT NULL DEFAULT 0,
    model_micros INTEGER NOT NULL DEFAULT 0,  -- millionths of a US dollar
    PRIMARY KEY (account_id, period)
) WITHOUT ROWID"""
_V11_ACCOUNT_QUOTA = """CREATE TABLE account_quota (
    account_id TEXT PRIMARY KEY,
    plan TEXT,  -- a name in the server's plans (cairn.plans); NULL: its default plan
    stored_bytes INTEGER NOT NULL DEFAULT 0  -- the UTF-8 size of every note it holds
) WITHOUT ROWID"""
_V11_METERING = (_V11_USAGE, _V11_ACCOUNT_QUOTA)


def _add_metering(con: sqlite3.Connection) -> None:
    """Schema 10 to 11: usage by month and each workspace's plan and stored bytes. Every
    workspace is on the default plan (no name), and the notes it already holds are counted:
    their UTF-8 size in its total, and each in the UTC month it was written (the first
    seven characters of its ISO 8601 UTC timestamp). Nothing that existed changes."""
    for statement in _V11_METERING:
        con.execute(statement)
    con.execute(
        "INSERT INTO account_quota (account_id, stored_bytes)"
        " SELECT projects.account_id, SUM(LENGTH(CAST(notes.text AS BLOB)))"
        " FROM notes JOIN projects ON projects.id = notes.project_id"
        " GROUP BY projects.account_id"
    )
    con.execute(
        "INSERT INTO usage (account_id, period, notes, note_bytes)"
        " SELECT projects.account_id, substr(notes.created_at, 1, 7), COUNT(*),"
        " SUM(LENGTH(CAST(notes.text AS BLOB)))"
        " FROM notes JOIN projects ON projects.id = notes.project_id"
        " GROUP BY projects.account_id, substr(notes.created_at, 1, 7)"
    )
