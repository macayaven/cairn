"""Schema step 20: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

from cairn.store.schema.v10_access import _V10_PROJECT_PEOPLE_BY_USER

# From _MANAGE_LEVEL a person's level on a project is one of three (T2): "manage" is "write" and
# deciding who reaches the project (cairn.store.access).
_V20_PROJECT_PEOPLE = """CREATE TABLE project_people (
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    access TEXT NOT NULL CHECK (access IN ('manage', 'write', 'read')),
    PRIMARY KEY (project_id, user_id)
) WITHOUT ROWID"""


def _add_manage_level(con: sqlite3.Connection) -> None:
    """Schema _GRANT_AUDIENCE to _MANAGE_LEVEL: a person's level on a project may be "manage".
    SQLite cannot change a CHECK in place, so project_people is rebuilt (as in
    _add_subscription_times: it is a WITHOUT ROWID table, so its rows are copied in key
    order) and its index made again once the old table, which took it along, is gone. It has
    no foreign key and no trigger names it. Every row is kept at its level."""
    columns = "project_id, user_id, access"
    con.execute("ALTER TABLE project_people RENAME TO project_people_v19")
    con.execute(_V20_PROJECT_PEOPLE)
    con.execute(
        f"INSERT INTO project_people ({columns}) SELECT {columns} FROM project_people_v19"
        " ORDER BY project_id, user_id"
    )
    con.execute("DROP TABLE project_people_v19")  # and with it its index
    con.execute(_V10_PROJECT_PEOPLE_BY_USER)
