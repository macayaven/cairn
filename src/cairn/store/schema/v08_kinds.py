"""Schema step 8: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# A kind is an id of cairn.kinds.KINDS, checked by the store: kinds are data, so adding one
# needs no migration, and the schema lists none.
_V8_PROJECTS = """CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,  -- what the project is for (cairn.kinds)
    UNIQUE (account_id, name)
)"""


def _add_kinds(con: sqlite3.Connection) -> None:
    """Schema 7 to 8: every project gains a kind, "general" for each that existed. Projects are
    rebuilt (as in _add_accounts) so that a migrated schema reads exactly like a fresh one."""
    columns = "id, account_id, name, description, created_at"
    con.execute("ALTER TABLE projects RENAME TO projects_v7")
    con.execute(_V8_PROJECTS)
    con.execute(
        f"INSERT INTO projects ({columns}, kind)"
        f" SELECT {columns}, 'general' FROM projects_v7 ORDER BY rowid"
    )
    con.execute("DROP TABLE projects_v7")
