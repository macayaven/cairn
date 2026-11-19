"""Schema step 10: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

from cairn.store.schema.v05_reach import _V5_API_KEYS_BY_LABEL

# Who reaches a project (T1): its maker, its rule for everyone, and the people it names
# (project_people). Owners reach every project whatever the rule says.
_V10_PROJECTS = """CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,  -- what the project is for (cairn.kinds)
    maker_id TEXT,  -- the person whose view made it; NULL: a key, the operator, or they left
    everyone TEXT NOT NULL CHECK (everyone IN ('write', 'read', 'none')),  -- any member's
    UNIQUE (account_id, name)
)"""
_V10_PROJECT_PEOPLE = """CREATE TABLE project_people (
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    access TEXT NOT NULL CHECK (access IN ('write', 'read')),
    PRIMARY KEY (project_id, user_id)
) WITHOUT ROWID"""
_V10_PROJECT_PEOPLE_BY_USER = "CREATE INDEX project_people_by_user ON project_people (user_id)"
# An invitation to a workspace, for one email address (compared ignoring case), with the role
# it gives. Its token is stored as a SHA-256 digest; using or revoking it removes the row.
_V10_INVITES = """CREATE TABLE invites (
    seq INTEGER PRIMARY KEY,  -- creation order
    id TEXT NOT NULL UNIQUE,  -- "inv_" + 12 hex chars
    digest TEXT NOT NULL UNIQUE,  -- SHA-256 hex of the token; the token is never stored
    account_id TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- open while expires_at > now
    UNIQUE (account_id, email)  -- inviting an address again replaces its invite
)"""
# A person removed from a workspace, so that signing in again (join "default") does not put
# them back: a digest of the workspace id and their lower-cased email, never the address. A
# fresh invitation, once taken, removes it; so does deleting the workspace.
_V10_REMOVALS = """CREATE TABLE removals (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of "<account id>\\n<lower-cased email>"
    account_id TEXT NOT NULL
) WITHOUT ROWID"""
_V10_ACCESS = (_V10_PROJECT_PEOPLE, _V10_PROJECT_PEOPLE_BY_USER, _V10_INVITES, _V10_REMOVALS)
_V10_API_KEYS = """CREATE TABLE api_keys (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,  -- "key_" + 12 hex chars: the key in the audit log; never reused
    digest TEXT NOT NULL UNIQUE,  -- SHA-256 hex of the key; the key itself is never stored
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    projects TEXT,  -- JSON array of the project and set ids it reaches; NULL: every project
    access TEXT NOT NULL CHECK (access IN ('write', 'read')),
    user_id TEXT  -- the person who made it, for whom it acts; NULL: the workspace's own key
)"""


def _add_access(con: sqlite3.Connection) -> None:
    """Schema 9 to 10: every project gains a maker, none (who made it was not recorded), and a
    rule that lets every member write, as every member could before; every key gains its
    maker, none, so it stays the workspace's own key; project_people and invites are added,
    empty. Projects and keys are rebuilt (as in _add_accounts) so that a migrated schema reads
    exactly like a fresh one."""
    for statement in _V10_ACCESS:
        con.execute(statement)
    columns = "id, account_id, name, description, created_at, kind"
    con.execute("ALTER TABLE projects RENAME TO projects_v9")
    con.execute(_V10_PROJECTS)
    con.execute(
        f"INSERT INTO projects ({columns}, maker_id, everyone)"
        f" SELECT {columns}, NULL, 'write' FROM projects_v9 ORDER BY rowid"
    )
    con.execute("DROP TABLE projects_v9")
    columns = "seq, id, digest, account_id, label, created_at, projects, access"
    con.execute("ALTER TABLE api_keys RENAME TO api_keys_v9")
    con.execute(_V10_API_KEYS)
    con.execute(
        f"INSERT INTO api_keys ({columns}, user_id)"
        f" SELECT {columns}, NULL FROM api_keys_v9 ORDER BY seq"
    )
    con.execute("DROP TABLE api_keys_v9")  # and with it its index
    con.execute(_V5_API_KEYS_BY_LABEL)
