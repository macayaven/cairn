"""Schema step 4: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

_V4_SESSIONS = """CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL,
    user_id TEXT  -- the person who signed in; NULL for a session opened with an API key
)"""
_V4_OAUTH_GRANTS = """CREATE TABLE oauth_grants (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    user_id TEXT  -- the person who approved; NULL when a session opened with a key did
)"""
_V4_OAUTH_GRANTS_BY_ACCOUNT = "CREATE INDEX oauth_grants_by_account ON oauth_grants (account_id)"
_V4_OAUTH_CODES = """CREATE TABLE oauth_codes (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the authorization code
    account_id TEXT NOT NULL,  -- the account that approved
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INTEGER NOT NULL,  -- 1 when the authorization request named it
    code_challenge TEXT NOT NULL,  -- PKCE, S256
    expires_at TEXT NOT NULL,  -- valid while expires_at > now
    created_at TEXT NOT NULL,
    grant_id TEXT,  -- set when redeemed; the row stays until it expires, to notice a replay
    user_id TEXT  -- the person who approved, handed on to the grant
)"""
# People. A user signs in through one or more identities (a provider's subject) and is a
# member of workspaces (accounts) with a role. One user per email address: a new identity
# whose verified email is a user's is that user.
_V4_USERS = """CREATE TABLE users (
    seq INTEGER PRIMARY KEY,  -- creation order
    id TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
)"""
_V4_IDENTITIES = """CREATE TABLE identities (
    provider TEXT NOT NULL,  -- the sign-in route's name: "github", "google", an OIDC name
    subject TEXT NOT NULL,  -- the provider's stable id for the person
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    issuer TEXT NOT NULL,  -- who vouched for the subject: the provider's issuer URL
    PRIMARY KEY (provider, subject)
)"""
_V4_IDENTITIES_BY_USER = "CREATE INDEX identities_by_user ON identities (user_id)"
_V4_MEMBERSHIPS = """CREATE TABLE memberships (
    seq INTEGER PRIMARY KEY,  -- joining order
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    created_at TEXT NOT NULL,
    UNIQUE (account_id, user_id)
)"""
_V4_MEMBERSHIPS_BY_USER = "CREATE INDEX memberships_by_user ON memberships (user_id)"
_PEOPLE = (
    _V4_USERS,
    _V4_IDENTITIES,
    _V4_IDENTITIES_BY_USER,
    _V4_MEMBERSHIPS,
    _V4_MEMBERSHIPS_BY_USER,
)


def _add_people(con: sqlite3.Connection) -> None:
    """Schema 3 to 4: users, identities and memberships, empty, and a column for the person
    on sessions, codes and grants, empty on every row that existed. The three tables are
    rebuilt (as in _add_accounts) so that a migrated schema reads exactly like a fresh one."""
    for statement in _PEOPLE:
        con.execute(statement)
    for table, ddl, columns in (
        ("sessions", _V4_SESSIONS, "digest, account_id, expires_at, created_at"),
        ("oauth_grants", _V4_OAUTH_GRANTS, "seq, id, account_id, client_id, created_at"),
        (
            "oauth_codes",
            _V4_OAUTH_CODES,
            "digest, account_id, client_id, redirect_uri, redirect_uri_explicit,"
            " code_challenge, expires_at, created_at, grant_id",
        ),
    ):
        con.execute(f"ALTER TABLE {table} RENAME TO {table}_v3")
        con.execute(ddl)
        con.execute(
            f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {table}_v3 ORDER BY rowid"
        )
        con.execute(f"DROP TABLE {table}_v3")  # and with it the index on oauth_grants
    con.execute(_V4_OAUTH_GRANTS_BY_ACCOUNT)
