"""Schema step 3: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# OAuth. A client belongs to no account; a code, a grant and a grant's tokens belong to the
# account that approved. Codes and tokens are stored as SHA-256 digests, like keys.
_OAUTH_CLIENTS = """CREATE TABLE oauth_clients (
    seq INTEGER PRIMARY KEY,  -- registration order
    id TEXT NOT NULL UNIQUE,  -- the public client_id; a client has no secret
    name TEXT NOT NULL,  -- what the client called itself
    redirect_uris TEXT NOT NULL,  -- JSON array of strings
    created_at TEXT NOT NULL,
    granted_at TEXT  -- when it first got a grant; a client that never does is removed
)"""
_OAUTH_TOKENS = """CREATE TABLE oauth_tokens (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    grant_id TEXT NOT NULL,
    -- 'rotated' is a refresh token that was exchanged: presenting it again ends the grant
    kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh', 'rotated')),
    expires_at TEXT NOT NULL,  -- valid while expires_at > now
    created_at TEXT NOT NULL
)"""
_OAUTH_TOKENS_BY_GRANT = "CREATE INDEX oauth_tokens_by_grant ON oauth_tokens (grant_id)"


def _add_oauth(con: sqlite3.Connection) -> None:
    """Schema 2 to 3: the OAuth tables, empty. Nothing that existed changes."""
    for statement in _V3_OAUTH:
        con.execute(statement)


# Version 3's OAuth tables: grants and codes as they were before version 4 rebuilt them.
_V3_OAUTH_GRANTS = """CREATE TABLE oauth_grants (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    created_at TEXT NOT NULL
)"""
_V3_OAUTH_CODES = """CREATE TABLE oauth_codes (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the authorization code
    account_id TEXT NOT NULL,  -- the account that approved
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INTEGER NOT NULL,  -- 1 when the authorization request named it
    code_challenge TEXT NOT NULL,  -- PKCE, S256
    expires_at TEXT NOT NULL,  -- valid while expires_at > now
    created_at TEXT NOT NULL,
    grant_id TEXT  -- set when redeemed; the row stays until it expires, to notice a replay
)"""
_V3_OAUTH_GRANTS_BY_ACCOUNT = "CREATE INDEX oauth_grants_by_account ON oauth_grants (account_id)"
_V3_OAUTH = (
    _OAUTH_CLIENTS,
    _V3_OAUTH_GRANTS,
    _V3_OAUTH_GRANTS_BY_ACCOUNT,
    _V3_OAUTH_CODES,
    _OAUTH_TOKENS,
    _OAUTH_TOKENS_BY_GRANT,
)
