"""Schema step 5: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

from cairn.store.base import _has_table, _index_name
from cairn.store.ranking import count_index

_V5_API_KEYS = """CREATE TABLE api_keys (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    digest TEXT NOT NULL UNIQUE,  -- SHA-256 hex of the key; the key itself is never stored
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    projects TEXT,  -- JSON array of the project and set ids it reaches; NULL: every project
    access TEXT NOT NULL CHECK (access IN ('write', 'read'))
)"""
_V5_OAUTH_GRANTS = """CREATE TABLE oauth_grants (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    user_id TEXT,  -- the person who approved; NULL when a session opened with a key did
    projects TEXT,  -- JSON array of the project and set ids it reaches; NULL: every project
    access TEXT NOT NULL CHECK (access IN ('write', 'read'))
)"""
_V5_OAUTH_CODES = """CREATE TABLE oauth_codes (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the authorization code
    account_id TEXT NOT NULL,  -- the account that approved
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INTEGER NOT NULL,  -- 1 when the authorization request named it
    code_challenge TEXT NOT NULL,  -- PKCE, S256
    expires_at TEXT NOT NULL,  -- valid while expires_at > now
    created_at TEXT NOT NULL,
    grant_id TEXT,  -- set when redeemed; the row stays until it expires, to notice a replay
    user_id TEXT,  -- the person who approved, handed on to the grant
    projects TEXT,  -- the reach the person chose, handed on to the grant (as on oauth_grants)
    access TEXT NOT NULL CHECK (access IN ('write', 'read'))
)"""
# Project sets: a named group of a workspace's projects. A connection's reach can name a set, and
# then covers whatever the set holds at the time of each request.
_V5_PROJECT_SETS = """CREATE TABLE project_sets (
    seq INTEGER PRIMARY KEY,  -- creation order
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    project_ids TEXT NOT NULL,  -- JSON array of project ids, in the order given
    created_at TEXT NOT NULL,
    UNIQUE (account_id, name)
)"""
# Search statistics (cairn.store.ranking), kept by save_page in its own transaction: one row
# per claim in an account's index (its project and its length in tokens, by its rowid there),
# and each project's claim and token counts, so a search learns N and the average length of
# the projects it reaches without reading them. Ids and counts only: no claim's words.
_V5_SEARCH_CLAIMS = """CREATE TABLE search_claims (
    account_id TEXT NOT NULL,
    row INTEGER NOT NULL,  -- the claim's rowid in the account's index
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    tokens INTEGER NOT NULL,  -- FTS5's count, title and text
    PRIMARY KEY (account_id, row)
) WITHOUT ROWID"""
_V5_SEARCH_CLAIMS_BY_PAGE = (
    "CREATE INDEX search_claims_by_page ON search_claims (account_id, project_id, slug)"
)
_V5_SEARCH_PROJECTS = """CREATE TABLE search_projects (
    project_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    claims INTEGER NOT NULL,
    tokens INTEGER NOT NULL
)"""
_V5_SEARCH = (_V5_SEARCH_CLAIMS, _V5_SEARCH_CLAIMS_BY_PAGE, _V5_SEARCH_PROJECTS)


def _add_reach(con: sqlite3.Connection) -> None:
    """Schema 4 to 5: project sets, empty, the reach of keys, codes and grants, and search
    statistics. Every key, code and grant that existed reaches every project, to write, as it
    did before. The three tables are rebuilt (as in _add_accounts) so that a migrated schema
    reads exactly like a fresh one. Each account's index is counted as it stands."""
    con.execute(_V5_PROJECT_SETS)
    for statement in _V5_SEARCH:
        con.execute(statement)
    for (account_id,) in con.execute("SELECT id FROM accounts ORDER BY seq").fetchall():
        index = _index_name(account_id)
        if _has_table(con, index):
            count_index(con, account_id, index)
    for table, ddl, columns in (
        ("api_keys", _V5_API_KEYS, "seq, digest, account_id, label, created_at"),
        (
            "oauth_grants",
            _V5_OAUTH_GRANTS,
            "seq, id, account_id, client_id, created_at, user_id",
        ),
        (
            "oauth_codes",
            _V5_OAUTH_CODES,
            "digest, account_id, client_id, redirect_uri, redirect_uri_explicit,"
            " code_challenge, expires_at, created_at, grant_id, user_id",
        ),
    ):
        con.execute(f"ALTER TABLE {table} RENAME TO {table}_v4")
        con.execute(ddl)
        con.execute(
            f"INSERT INTO {table} ({columns}, projects, access)"
            f" SELECT {columns}, NULL, 'write' FROM {table}_v4 ORDER BY rowid"
        )
        con.execute(f"DROP TABLE {table}_v4")  # and with it the indexes on keys and grants
    con.execute(_V5_API_KEYS_BY_LABEL)
    con.execute(_V5_OAUTH_GRANTS_BY_ACCOUNT)


_V5_API_KEYS_BY_LABEL = "CREATE INDEX api_keys_by_label ON api_keys (account_id, label)"
_V5_OAUTH_GRANTS_BY_ACCOUNT = "CREATE INDEX oauth_grants_by_account ON oauth_grants (account_id)"
