"""Schema step 2: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

from cairn.store.base import DEFAULT_ACCOUNT_ID, _has_table, _index_name, _insert_account
from cairn.store.models import Account
from cairn.store.schema.shared import _STAMP, _delete_securely

_ACCOUNTS = """CREATE TABLE accounts (
    seq INTEGER PRIMARY KEY,  -- creation order, the default account first
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
)"""
_V2_PROJECTS = """CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (account_id, name)
)"""
_API_KEYS_BY_LABEL = "CREATE INDEX api_keys_by_label ON api_keys (account_id, label)"
# Notes, spans and pages are keyed by project; their account is their project's account.
_V2_NOTES = """CREATE TABLE notes (
    seq INTEGER PRIMARY KEY,  -- insertion order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    title TEXT NOT NULL,
    topic TEXT,
    label TEXT,
    source_ref TEXT,
    via TEXT,
    created_at TEXT NOT NULL
)"""
# Finds a note sent again (Store.add_note: the same project, text and source_ref) and a
# project's notes; source_ref is compared among the rows of one text.
_V2_NOTES_BY_TEXT = "CREATE INDEX notes_by_text ON notes (project_id, text_sha256)"


def _add_accounts(con: sqlite3.Connection) -> None:
    """Schema 1 (v0) to 2: every project, key and session moves to the default account, and
    v0's search index becomes the default account's.

    SQLite cannot alter a UNIQUE constraint or add a NOT NULL column without a default, so the
    three tables are rebuilt: the old one steps aside, the new one is created from the current
    DDL and filled in the old insertion order."""
    for table, columns in _V0_COLUMNS.items():
        if not _has_table(con, table):
            raise sqlite3.DatabaseError(f"not a cairn database: no table {table!r}")
        found = tuple(row[1] for row in con.execute(f"PRAGMA table_info({table})"))
        if found != columns:  # a rebuild copies v0's columns only: anything else would be lost
            raise sqlite3.DatabaseError(
                f"not a cairn database: table {table!r} has columns {found}, not {columns}"
            )
    con.execute(_ACCOUNTS)
    _insert_account(con, Account(id=DEFAULT_ACCOUNT_ID, name="default", created_at=_STAMP.get()()))
    for table, ddl, columns in (
        ("projects", _V2_PROJECTS, "id, name, description, created_at"),
        ("api_keys", _V2_API_KEYS, "digest, label, created_at"),
        ("sessions", _V2_SESSIONS, "digest, expires_at, created_at"),
    ):
        con.execute(f"ALTER TABLE {table} RENAME TO {table}_v1")
        con.execute(ddl)
        con.execute(
            f"INSERT INTO {table} ({columns}, account_id)"
            f" SELECT {columns}, ? FROM {table}_v1 ORDER BY rowid",
            (DEFAULT_ACCOUNT_ID,),
        )
        con.execute(f"DROP TABLE {table}_v1")  # and with it v0's index on api_keys
    con.execute(_API_KEYS_BY_LABEL)
    index = _index_name(DEFAULT_ACCOUNT_ID)
    con.execute(f"ALTER TABLE claim_search RENAME TO {index}")
    _delete_securely(con, index)


# v0's tables and their columns, in order. A file is only migrated when it has exactly these.
_V0_COLUMNS = {
    "projects": ("id", "name", "description", "created_at"),
    "api_keys": ("digest", "label", "created_at"),
    "sessions": ("digest", "expires_at", "created_at"),
    "notes": (
        "seq",
        "id",
        "project_id",
        "text",
        "text_sha256",
        "title",
        "topic",
        "label",
        "source_ref",
        "via",
        "created_at",
    ),
    "spans": (
        "id",
        "note_id",
        "ordinal",
        "kind",
        "heading_path",
        "start_line",
        "end_line",
        "text",
    ),
    "page_versions": (
        "project_id",
        "slug",
        "version",
        "title",
        "body",
        "claims",
        "metrics",
        "created_at",
    ),
    "claim_search": ("project_id", "slug", "claim_id", "span_ids", "title", "text"),
}


# Version 2's api_keys table, which version 5 rebuilt.
_V2_API_KEYS = """CREATE TABLE api_keys (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    digest TEXT NOT NULL UNIQUE,  -- SHA-256 hex of the key; the key itself is never stored
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL
)"""
# Version 2's sessions table, which version 4 rebuilt.
_V2_SESSIONS = """CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL
)"""
