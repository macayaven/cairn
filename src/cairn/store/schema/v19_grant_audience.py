"""Schema step 19: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

from cairn.store.schema.v04_people import _V4_OAUTH_GRANTS_BY_ACCOUNT

# A grant is bound to the MCP address (the OAuth resource, public_url + "/mcp") its code was
# approved for: its tokens are refused at any other (store/grants.py).
_AUDIENCE_OAUTH_GRANTS = """CREATE TABLE oauth_grants (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    user_id TEXT,  -- the person who approved; NULL when a session opened with a key did
    projects TEXT,  -- JSON array of the project and set ids it reaches; NULL: every project
    access TEXT NOT NULL CHECK (access IN ('write', 'read')),
    audience TEXT  -- the MCP address, from its code; NULL: none (never one the server made)
)"""
_AUDIENCE_OAUTH_CODES = """CREATE TABLE oauth_codes (
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
    access TEXT NOT NULL CHECK (access IN ('write', 'read')),
    audience TEXT  -- the MCP address it was approved at, handed on to the grant
)"""


def _add_grant_audience(con: sqlite3.Connection) -> None:
    """Schema _HEADING_CHARGE to _GRANT_AUDIENCE: a code and a grant name the MCP address
    they are for.

    Every grant, code and token that exists is ended: none of them recorded the address it
    was approved at, so none can be bound to one, and a grant bound to none would be
    accepted at any address, which is the hole this step closes (an operator who served the
    same database at a second public URL had every grant valid there). Ending them is the
    safe choice, and a cheap one: no install was deployed with OAuth grants when this step
    was written. An agent connects again with a person's approval; its client registration
    is kept, so it does not register again. Nothing else changes; the audit log records no
    disconnection, since no one disconnected anything."""
    con.execute("DELETE FROM oauth_tokens")
    con.execute("DROP TABLE oauth_codes")
    con.execute("DROP TABLE oauth_grants")  # and with it its index
    con.execute(_AUDIENCE_OAUTH_CODES)
    con.execute(_AUDIENCE_OAUTH_GRANTS)
    con.execute(_V4_OAUTH_GRANTS_BY_ACCOUNT)
