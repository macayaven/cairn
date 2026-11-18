"""The schema: its DDL, its version, the upgrade loop and each migration step."""

import functools
import re
import secrets
import sqlite3
import unicodedata
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path

from cairn.store.base import (
    DEFAULT_ACCOUNT_ID,
    _has_table,
    _index_name,
    _insert_account,
    _transaction,
)
from cairn.store.models import Account
from cairn.store.ranking import count_index

# The one place the subscription-times step's number is written (H7 fix round): another
# step may take this number first, and then this one takes the next.
_SUBSCRIPTION_TIMES = 13
# The one place the forgotten-notes step's number is written (H5b), for the same reason.
_FORGOTTEN = 15
# The one place the disk-bounds step's number is written (H6c).
_DISK_BOUNDS = 16
# The one place the key-sessions step's number is written (the third security pass).
_KEY_SESSIONS = 17
# The one place the heading-charge step's number is written (H6c, finding 2).
_HEADING_CHARGE = 18
# The one place the grant-audience step's number is written (review 4a-2).
_GRANT_AUDIENCE = 19
# The one place the manage-level step's number is written (T2), and so the newest version.
_MANAGE_LEVEL = 20
# The one place the source-switch step's number is written (S2).
_SOURCE_SWITCH = 21
# The one place the vouched-address step's number is written (B1).
_VOUCHED_EMAIL = 22
# The one place the source-items step's number is written (I1).
_SOURCE_ITEMS = 23
# The one place the topic-keys step's number is written (N1).
_TOPIC_KEYS = 24
# The one place the writers step's number is written (A1).
_WRITERS = 25
# The one place the page-heads step's number is written (Q1).
_PAGE_HEADS = 26
# The one place the checkouts step's number is written (R3).
_CHECKOUTS = 27
# The one place the credential-tokens step's number is written (a Google account connected in
# the browser).
_CREDENTIAL_TOKENS = 28
# The one place the Slack step's number is written (K1), and so the newest version.
_SLACK = 29
# The newest step. 1: v0's schema, which shipped unstamped; 2: accounts; 3: OAuth; 4: people;
# 5: reach (project sets, and the projects and access of each key and grant), search statistics;
# 6: settings and the audit log, and the key a session was opened with; 7: actor ids (each
# key's id, the id of the key a session was opened with, and each audit event's actor id
# and its number in its workspace, by which the oldest beyond AUDIT_KEEP are pruned);
# 8: project kinds; 9: sources (cairn.sources); 10: who reaches a project (its maker and rule),
# the person who made a key, and invitations; 11: metering and plans (H6); 12: billing (H7);
# _SUBSCRIPTION_TIMES: when a subscription started and when it became past due (H7 fixes)
# 14: model-use reservations (T4b); _FORGOTTEN: the digests of forgotten notes (H5b);
# _DISK_BOUNDS: each workspace's passages and page history, counted (H6c);
# _KEY_SESSIONS: the sessions opened with a key that cannot be told apart end;
# _HEADING_CHARGE: the passages counter charges the heading path stored with each span;
# _GRANT_AUDIENCE: the MCP address each code and grant is for (every grant ended);
# _MANAGE_LEVEL: a person's level on a project may be "manage" (project_people rebuilt);
# _SOURCE_SWITCH: a source is on or off, and records when it was last switched on (S2);
# _VOUCHED_EMAIL: a session records the address its sign-in was vouched with (B1);
# _SOURCE_ITEMS: the items each source wrote, by digest (I1);
# _TOPIC_KEYS: each note's and each page's topic key, and the notes waiting for their page (N1);
# _WRITERS: who wrote each note, the key each key or grant was made with, and when this
# began to be recorded (A1); _PAGE_HEADS: each page's latest version, its number, title,
# claim count and time, kept beside its versions (Q1); _CHECKOUTS: each workspace's open
# checkout, kept in the store so a restart or a second server process finds it (R3);
# _CREDENTIAL_TOKENS: the sealed refresh token of each Google account a person connected in
# the browser (cairn.store.hosted), and those whose Google permission is still to be ended;
# _SLACK: each workspace's Slack install, its links, one-time offers, events seen and the
# notes that came through it (cairn.store.slack)
SCHEMA_VERSION = _SLACK
_FTS5_SECURE_DELETE = sqlite3.sqlite_version_info >= (3, 42, 0)  # the option's first release


class SchemaTooNew(RuntimeError):
    """The database was written by a newer cairn than this one; it is left untouched."""


class UnknownSchema(RuntimeError):
    """The database has this code's schema version but lacks a table of the schema this code
    makes, so this version of cairn did not make it. Raised while the store opens, before it
    writes anything; the file is left as it was. The message names the version and the
    first table missing."""


class CannotOpen(sqlite3.DatabaseError):
    """The path cannot be opened as a cairn database: SQLite may not open or create a file
    there (SQLITE_CANTOPEN), may not write it (SQLITE_READONLY), or finds that the file is not
    a database (SQLITE_NOTADB). Raised while the store opens, before it writes anything, from
    SQLite's own error. `path` is the path as given; `not_a_database` tells the last apart.
    The message names the path only, never anything read from the file."""

    def __init__(self, path: str | Path, not_a_database: bool = False) -> None:
        self.path = Path(path)
        self.not_a_database = not_a_database
        what = "is not a database" if not_a_database else "cannot be opened or created"
        super().__init__(f"{self.path} {what}")


def _refuse(path: str | Path, error: sqlite3.Error) -> None:
    """Raise CannotOpen from `error` when it is SQLite's own refusal to open the file at
    `path` (by its primary result code: SQLITE_NOTADB, SQLITE_CANTOPEN, SQLITE_READONLY);
    return for anything else, a busy or locked database and an error cairn raised among them."""
    code = getattr(error, "sqlite_errorcode", None)
    if code is None:
        return
    if code & 0xFF == sqlite3.SQLITE_NOTADB:
        raise CannotOpen(path, not_a_database=True) from error
    if code & 0xFF in (sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_READONLY):
        raise CannotOpen(path) from error


# The current schema, one statement each: a migration runs inside a transaction, and
# executescript would commit it.
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
_PROJECTS = _V10_PROJECTS
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
# From _MANAGE_LEVEL a person's level on a project is one of three (T2): "manage" is "write" and
# deciding who reaches the project (cairn.store.access).
_V20_PROJECT_PEOPLE = """CREATE TABLE project_people (
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    access TEXT NOT NULL CHECK (access IN ('manage', 'write', 'read')),
    PRIMARY KEY (project_id, user_id)
) WITHOUT ROWID"""
_ACCESS = (_V20_PROJECT_PEOPLE, _V10_PROJECT_PEOPLE_BY_USER, _V10_INVITES, _V10_REMOVALS)
_V5_API_KEYS = """CREATE TABLE api_keys (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    digest TEXT NOT NULL UNIQUE,  -- SHA-256 hex of the key; the key itself is never stored
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    projects TEXT,  -- JSON array of the project and set ids it reaches; NULL: every project
    access TEXT NOT NULL CHECK (access IN ('write', 'read'))
)"""
_V7_API_KEYS = """CREATE TABLE api_keys (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,  -- "key_" + 12 hex chars: the key in the audit log; never reused
    digest TEXT NOT NULL UNIQUE,  -- SHA-256 hex of the key; the key itself is never stored
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    projects TEXT,  -- JSON array of the project and set ids it reaches; NULL: every project
    access TEXT NOT NULL CHECK (access IN ('write', 'read'))
)"""
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
_API_KEYS = _V10_API_KEYS
_API_KEYS_BY_LABEL = "CREATE INDEX api_keys_by_label ON api_keys (account_id, label)"
_V4_SESSIONS = """CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL,
    user_id TEXT  -- the person who signed in; NULL for a session opened with an API key
)"""
_V6_SESSIONS = """CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL,
    user_id TEXT,  -- the person who signed in; NULL for a session opened with an API key
    label TEXT  -- the label of the API key it was opened with; NULL otherwise
)"""
_V7_SESSIONS = """CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL,
    user_id TEXT,  -- the person who signed in; NULL for a session opened with an API key
    label TEXT,  -- the label of the API key it was opened with; NULL otherwise
    key_id TEXT  -- the id of the API key it was opened with; NULL otherwise
)"""
_V22_SESSIONS = """CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL,
    user_id TEXT,  -- the person who signed in; NULL for a session opened with an API key
    label TEXT,  -- the label of the API key it was opened with; NULL otherwise
    key_id TEXT,  -- the id of the API key it was opened with; NULL otherwise
    vouched_email TEXT  -- the address the provider vouched for at the sign-in it came from
)"""
_SESSIONS = _V22_SESSIONS
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
# N1: a note keeps the topic key it was stored with (cairn.topics), made once from its topic,
# else its title, as given, never again from a cut or shown form. `before_topics` is 1 for
# every note step 24 found, the notes stored before notes were filed by topic, and 0 for each
# note stored since: sent again (the same project, text and source_ref), a note marked so is the
# same note whatever topic or title it comes with, as it was before (Store.add_note).
_V24_NOTES = """CREATE TABLE notes (
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
    created_at TEXT NOT NULL,
    topic_key TEXT NOT NULL,  -- the topic it is filed under (cairn.topics.topic_key)
    before_topics INTEGER NOT NULL DEFAULT 0 CHECK (before_topics IN (0, 1))  -- 1: stored
        -- before N1 (schema step 24): sent again, it is the same note whatever its topic
)"""
_NOTES = _V24_NOTES
# Finds a note sent again (Store.add_note: the same project, text and source_ref) and a
# project's notes; source_ref is compared among the rows of one text.
_V2_NOTES_BY_TEXT = "CREATE INDEX notes_by_text ON notes (project_id, text_sha256)"
_NOTES_BY_TEXT = _V2_NOTES_BY_TEXT
# Spans are stored, never re-chunked on read: their ids are permanent citations.
_SPANS = """CREATE TABLE spans (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,  -- document order within the note
    kind TEXT NOT NULL,
    heading_path TEXT NOT NULL,  -- JSON array of heading texts
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (note_id, ordinal)
)"""
_PAGE_VERSIONS = """CREATE TABLE page_versions (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    claims TEXT NOT NULL,  -- JSON array of {"id", "text", "span_ids"}
    metrics TEXT NOT NULL,  -- JSON object: the gate's metrics for this version
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, slug, version)
)"""
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
_V4_OAUTH_GRANTS = """CREATE TABLE oauth_grants (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    user_id TEXT  -- the person who approved; NULL when a session opened with a key did
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
_OAUTH_GRANTS = _AUDIENCE_OAUTH_GRANTS
_V4_OAUTH_GRANTS_BY_ACCOUNT = "CREATE INDEX oauth_grants_by_account ON oauth_grants (account_id)"
_OAUTH_GRANTS_BY_ACCOUNT = _V4_OAUTH_GRANTS_BY_ACCOUNT
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
_OAUTH_CODES = _AUDIENCE_OAUTH_CODES
_OAUTH_TOKENS = """CREATE TABLE oauth_tokens (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    grant_id TEXT NOT NULL,
    -- 'rotated' is a refresh token that was exchanged: presenting it again ends the grant
    kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh', 'rotated')),
    expires_at TEXT NOT NULL,  -- valid while expires_at > now
    created_at TEXT NOT NULL
)"""
_OAUTH_TOKENS_BY_GRANT = "CREATE INDEX oauth_tokens_by_grant ON oauth_tokens (grant_id)"
_OAUTH = (
    _OAUTH_CLIENTS,
    _OAUTH_GRANTS,
    _OAUTH_GRANTS_BY_ACCOUNT,
    _OAUTH_CODES,
    _OAUTH_TOKENS,
    _OAUTH_TOKENS_BY_GRANT,
)
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
_PROJECT_SETS = _V5_PROJECT_SETS
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
_SEARCH = _V5_SEARCH
# A workspace's settings (cairn.store.audit.SETTINGS), and its audit log: its writes and
# security events, one row each, written in the transaction of the write it records. Names,
# ids and counts only: never note text, a secret or a digest.
_V6_SETTINGS = """CREATE TABLE settings (
    account_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, name)
) WITHOUT ROWID"""
_V6_AUDIT_EVENTS = """CREATE TABLE audit_events (
    seq INTEGER PRIMARY KEY,  -- the order of events, even if the clock steps back
    account_id TEXT NOT NULL,
    at TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('key', 'token', 'session', 'system')),
    actor TEXT,  -- the key's label, the agent's client name or the person; NULL for system
    action TEXT NOT NULL,
    project TEXT,  -- the project's name when the event is about one
    detail TEXT NOT NULL  -- a JSON object
)"""
_V6_AUDIT_EVENTS_BY_ACCOUNT = (
    "CREATE INDEX audit_events_by_account ON audit_events (account_id, seq)"
)
_V6_AUDIT = (_V6_SETTINGS, _V6_AUDIT_EVENTS, _V6_AUDIT_EVENTS_BY_ACCOUNT)
_V7_AUDIT_EVENTS = """CREATE TABLE audit_events (
    seq INTEGER PRIMARY KEY,  -- the order of events, even if the clock steps back
    account_id TEXT NOT NULL,
    n INTEGER NOT NULL,  -- its number in its workspace, 1, 2, ...: the oldest are pruned
    at TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('key', 'token', 'session', 'system')),
    actor TEXT,  -- the key's label, the agent's client name or the person; NULL for system
    actor_id TEXT,  -- the key's id, the grant's or the person's (names repeat); NULL: system
    action TEXT NOT NULL,
    project TEXT,  -- the project's name when the event is about one
    detail TEXT NOT NULL  -- a JSON object
)"""
_V7_AUDIT_EVENTS_BY_NUMBER = (
    "CREATE UNIQUE INDEX audit_events_by_number ON audit_events (account_id, n)"
)
_V7_AUDIT = (_V6_SETTINGS, _V7_AUDIT_EVENTS, _V7_AUDIT_EVENTS_BY_NUMBER)
_AUDIT = _V7_AUDIT
# A workspace's sources (cairn.sources): the config its kind checked (never a credential:
# it names environment variables), where the next run starts, how the last one went, and
# the claim of the run that holds it now (a compare-and-set, seen by every process).
_V9_SOURCES = """CREATE TABLE sources (
    seq INTEGER PRIMARY KEY,  -- creation order
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    kind TEXT NOT NULL,  -- a name of cairn.sources.KINDS
    config TEXT NOT NULL,  -- a JSON object, as the kind checked it
    cursor TEXT,  -- the source's own, opaque; NULL before the first run that worked
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    last_report TEXT,  -- a JSON object: the counts of the last run that worked
    last_error TEXT,  -- why the last run failed; NULL once one works
    claim TEXT,  -- the token of the run that holds the source; NULL when none does
    claimed_at TEXT,
    UNIQUE (account_id, name)
)"""
# S2: a source can be off, and it remembers when it was last switched on (what its kind
# may read from).
_V21_SOURCES = """CREATE TABLE sources (
    seq INTEGER PRIMARY KEY,  -- creation order
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    kind TEXT NOT NULL,  -- a name of cairn.sources.KINDS
    config TEXT NOT NULL,  -- a JSON object, as the kind checked it
    cursor TEXT,  -- the source's own, opaque; NULL before the first run that worked
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    last_report TEXT,  -- a JSON object: the counts of the last run that worked
    last_error TEXT,  -- why the last run failed; NULL once one works
    claim TEXT,  -- the token of the run that holds the source; NULL when none does
    claimed_at TEXT,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),  -- 0: off, no run reads it
    enabled_at TEXT,  -- when it was last switched on; NULL while it never was
    UNIQUE (account_id, name)
)"""
_SOURCES = _V21_SOURCES
# The items each source wrote (I1; cairn.store.sources, "Items"): one row per source and
# item, so that a draft naming an item its source already wrote is not written again. The
# item is kept as the first 32 hex characters (128 bits) of its SHA-256, never as itself, as
# the kinds' cursors keep digests; `source` is the source's `seq`. Rows go with their source
# (remove_source) and their workspace (delete_account), and are never exported.
# The primary key is the only index, and it is the table (WITHOUT ROWID).
# Size: a row is the source's seq (1 to 3 bytes), the digest (32 bytes) and the time it was
# written (32 bytes, ISO 8601 with microseconds), plus a few bytes of record header and cell
# pointer: about 70 bytes. A million items, written by the sources of one database in any
# order, took 82 MB of pages when measured (SQLite 3, 4 KiB pages, B-tree pages not full).
# A row is never pruned while its source exists: there is one per item ever written, not
# one per run, and the table grows with what the sources wrote.
_V23_SOURCE_ITEMS = """CREATE TABLE source_items (
    source INTEGER NOT NULL,  -- sources.seq of the source that wrote the item
    item TEXT NOT NULL,  -- the first 32 hex characters of the item's SHA-256
    written_at TEXT NOT NULL,  -- when it was recorded, after its note returned
    PRIMARY KEY (source, item)
) WITHOUT ROWID"""
_SOURCE_ITEMS_TABLE = _V23_SOURCE_ITEMS
# Topics (N1). Each page's topic key, fixed when the page is made: a note goes to the page of
# its key (cairn.compiler), and no two pages of a project have one key. A page whose title's
# key another page held when it got its row has none, and no note goes to it. Rows go with
# their page (forget_note removes a page left with no claim), their project and their
# workspace; an export carries each page's key and an import restores it.
_V24_PAGE_KEYS = """CREATE TABLE page_keys (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    PRIMARY KEY (project_id, slug),
    UNIQUE (project_id, topic_key)
) WITHOUT ROWID"""
# The notes stored and not yet on their page (N1): a row is written with the note and
# removed with its outcome (its page saved, or note.unchanged or note.rejected recorded), in
# the same transaction. `serve` files them (cairn.web.filing), counting in `tries` each try
# that ended with no outcome. Rows go with their note, project and workspace; never exported.
_V24_WAITING = """CREATE TABLE waiting (
    note_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID"""
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
# Page heads (Q1). One row per page: its latest version's number, title, claim count and
# time, kept by two triggers on page_versions in the transaction of each insert and delete,
# so a list of a project's pages reads these short rows, never a version (each version holds
# the whole page, and its body's overflow pages lie between its title and its claims).
# Measured on 2,000 pages of 50 KB bodies: listing every page read 24,698 database pages
# without this table and 50 with it; the first 100 by slug, 1,258 and 12. A save inserts the
# new latest; removing an older version changes nothing; removing the latest takes the next
# one's row, and removing a page's last version removes its head. Rows are not exported:
# an import's versions make them, and they go with their page, project and workspace.
_V26_PAGE_HEADS = """CREATE TABLE page_heads (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,  -- the page's latest version
    title TEXT NOT NULL,
    claims INTEGER NOT NULL,  -- how many claims that version has
    updated_at TEXT NOT NULL,  -- when that version was saved
    PRIMARY KEY (project_id, slug)
) WITHOUT ROWID"""
_V26_HEAD_SAVED = """CREATE TRIGGER head_saved AFTER INSERT ON page_versions
BEGIN
    INSERT INTO page_heads (project_id, slug, version, title, claims, updated_at)
    VALUES (new.project_id, new.slug, new.version, new.title, json_array_length(new.claims),
    new.created_at)
    ON CONFLICT (project_id, slug) DO UPDATE SET version = excluded.version,
    title = excluded.title, claims = excluded.claims, updated_at = excluded.updated_at
    WHERE excluded.version >= page_heads.version;
END"""
_V26_HEAD_REMOVED = """CREATE TRIGGER head_removed AFTER DELETE ON page_versions
WHEN old.version = (SELECT version FROM page_heads
    WHERE project_id = old.project_id AND slug = old.slug)
BEGIN
    DELETE FROM page_heads WHERE project_id = old.project_id AND slug = old.slug;
    INSERT INTO page_heads (project_id, slug, version, title, claims, updated_at)
    SELECT project_id, slug, version, title, json_array_length(claims), created_at
    FROM page_versions WHERE project_id = old.project_id AND slug = old.slug
    ORDER BY version DESC LIMIT 1;
END"""
_V26_HEADS = (_V26_PAGE_HEADS, _V26_HEAD_SAVED, _V26_HEAD_REMOVED)
# Open checkouts (R3). At most one row per workspace: the checkout the
# payment provider is asked for or gave, while it is open. Its idempotency key is made with
# the row, before the provider is asked, and sent with every ask for it, so the provider
# answers a repeated ask with the same checkout. A row goes when it ends (a later write
# removes it), when a provider's message is applied for its workspace, when the provider
# answers the ask with a final failure, and with its workspace. Never exported.
_V27_CHECKOUTS = """CREATE TABLE checkouts (
    account_id TEXT PRIMARY KEY,  -- one open checkout per workspace, whatever the plan
    plan TEXT NOT NULL,  -- the plan it buys
    idempotency_key TEXT NOT NULL,  -- sent to the provider with each ask for it
    url TEXT,  -- the address the provider gave; NULL until it answered
    opened_at TEXT NOT NULL,  -- when it was opened, by the web service's clock
    ends_at TEXT NOT NULL  -- when it stops counting as open
) WITHOUT ROWID"""
# A Google account a person connected in the browser (cairn.store.hosted): one row per
# credential whose settings row (cairn.store.sources, CREDENTIAL_PREFIX) says it is hosted.
# The refresh token is sealed with AES-GCM under the key in the server's environment, a fresh
# nonce each time, and the workspace, credential name and owner as associated data, so a
# sealed value copied to another row does not open. The address and the granted scopes are in
# clear. A row goes with its credential and with its workspace. Never exported.
_V28_CREDENTIAL_TOKENS = """CREATE TABLE credential_tokens (
    account_id TEXT NOT NULL,  -- the workspace
    credential TEXT NOT NULL,  -- the credential's name, as its settings row names it
    kind TEXT NOT NULL,  -- gmail, google_calendar, google_drive or google_meet
    owner TEXT NOT NULL,  -- the person's user id
    address TEXT NOT NULL,  -- the Google account's address, as its ID token said: for people
    subject TEXT NOT NULL,  -- Google's own id of the account (the ID token's sub): the rule's
    scopes TEXT NOT NULL,  -- the scopes Google granted, separated by spaces
    sealed TEXT NOT NULL,  -- base64url of the nonce, then the sealed refresh token and its tag
    added_at TEXT NOT NULL,  -- when it was first connected
    refreshed_at TEXT NOT NULL,  -- when Google last issued the token held (connect, reconnect)
    ended_at TEXT,  -- when a revocation of the same Google account may have ended it
    PRIMARY KEY (account_id, credential)
) WITHOUT ROWID"""
# A dropped connected account whose Google permission is still to be ended (the revoking
# rule, cairn.web.google_connect): its token sealed again under the row's own id as
# associated data, so a restart finds it and sends it. No workspace and no person: a deleted
# workspace leaves only the account's id, its address and the sealed token here, until
# Google answers the revocation with 200 or 400.
_V28_GOOGLE_REVOCATIONS = """CREATE TABLE google_revocations (
    id TEXT PRIMARY KEY,  -- random, the sealed token's associated data
    subject TEXT NOT NULL,  -- Google's id of the account (the ID token's sub), for the rule
    address TEXT NOT NULL,  -- its address when it was dropped
    sealed TEXT NOT NULL,  -- base64url of the nonce, then the sealed refresh token and its tag
    ended_at TEXT NOT NULL,  -- when its account was dropped here
    claimed_by TEXT,  -- the one sending it now (random), so no two send it at once
    claimed_at TEXT,  -- when; a claim older than CLAIM_STALE is taken again
    tries INTEGER NOT NULL DEFAULT 0,  -- Google's refusals so far (REVOKE_TRIES: no more sent)
    due_at TEXT,  -- not sent again before this (a refusal backs off); NULL: on the next pass
    said TEXT  -- the outcome last logged, so each change of state is logged once
) WITHOUT ROWID"""
# A workspace's Slack install (K1, cairn.store.slack): one a workspace, keyed by an id of its
# own that the three addresses Slack calls carry. Its signing secret and bot token are sealed
# with AES-GCM under the key in the server's environment (CAIRN_SLACK_KEY), a fresh nonce
# each time, and the install, the workspace and which of the two it is as associated data,
# so a copy of the database opens neither and a sealed value moved does not open. Removing
# it deletes both at once. Never exported.
_V29_SLACK_INSTALLS = """CREATE TABLE slack_installs (
    id TEXT PRIMARY KEY,  -- "sl_" and 12 hexadecimal characters
    account_id TEXT NOT NULL UNIQUE,  -- the workspace: one install for each
    state TEXT NOT NULL,  -- pending, ready, on, off or removed
    team_id TEXT,  -- the Slack team the bot token belongs to; kept when removed
    enterprise_id TEXT,
    team_domain TEXT,
    bot_user_id TEXT,  -- the bot's own Slack user id, as auth.test names it
    signing_secret TEXT,  -- sealed; NULL until ready, and once removed
    bot_token TEXT,  -- sealed; NULL until ready, once removed, and once Slack revoked it
    command TEXT NOT NULL,  -- the slash command's name, "/cairn" unless changed
    made_by TEXT,  -- the person who made it
    made_at TEXT NOT NULL,
    removed_at TEXT,  -- when it was last removed; NULL while it is not
    checked_at TEXT,  -- when Slack was last asked whether the bot token still works
    dead_checks INTEGER NOT NULL DEFAULT 0,  -- answers in a row that said it does not
    bot_gone_at TEXT  -- since when no bot token is held because Slack ended it; else NULL
) WITHOUT ROWID"""
# A Slack user linked to a person of the install's workspace, and the project their notes go to.
_V29_SLACK_PEOPLE = """CREATE TABLE slack_people (
    install_id TEXT NOT NULL,
    slack_user_id TEXT NOT NULL,
    user_id TEXT NOT NULL,  -- the person
    project_id TEXT,  -- their Slack project; NULL: none chosen, or it was deleted
    linked_at TEXT NOT NULL,
    PRIMARY KEY (install_id, slack_user_id)
) WITHOUT ROWID"""
# A one-time offer of a link, kept as the SHA-256 digest of the value the Slack user is given;
# the person who said yes to it in the browser, and the digest of the code they were shown,
# which the same Slack user types back to be linked. A used offer is deleted.
_V29_SLACK_LINK_OFFERS = """CREATE TABLE slack_link_offers (
    digest TEXT PRIMARY KEY,
    install_id TEXT NOT NULL,
    slack_user_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- ten minutes after it was made, for both steps
    user_id TEXT,  -- the person who said yes in the browser; NULL until then
    code_digest TEXT,  -- of the code that person was shown; NULL until then
    tries INTEGER NOT NULL DEFAULT 0,  -- wrong codes this Slack user typed since
    UNIQUE (install_id, slack_user_id)  -- one offer for a Slack user at a time
) WITHOUT ROWID"""
# The event ids an install has seen, so an event is done once; swept after a day.
_V29_SLACK_EVENTS = """CREATE TABLE slack_events (
    install_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (install_id, event_id)
) WITHOUT ROWID"""
# Every note an install created: how (a filed message, with its conversation and time, unique
# within the install; or a typed note), who brought it and when, and when it is to be forgotten
# once the install was removed (NULL: not due). A filed message is claimed first, a row with no
# note yet, so two filings of it store one note; a claim with no note is swept after ten minutes.
_V29_SLACK_NOTES = """CREATE TABLE slack_notes (
    id INTEGER PRIMARY KEY,
    note_id TEXT UNIQUE,  -- NULL while a filed message is claimed and not yet stored
    install_id TEXT NOT NULL,
    how TEXT NOT NULL,  -- filed or typed
    conversation_id TEXT,  -- a filed message's
    message_time TEXT,  -- a filed message's time, as Slack writes it
    brought_by TEXT,  -- the person
    brought_at TEXT NOT NULL,
    forget_at TEXT,  -- NULL until its install is removed
    UNIQUE (install_id, conversation_id, message_time)
)"""
_V29_SLACK_NOTES_DUE = "CREATE INDEX slack_notes_due ON slack_notes (forget_at)"
# One Slack team, one workspace: an install that is pending or removed holds no team.
_V29_SLACK_TEAM = """CREATE UNIQUE INDEX slack_installs_team ON slack_installs (team_id)
    WHERE state IN ('ready', 'on', 'off')"""
_V29_SLACK = (
    _V29_SLACK_INSTALLS,
    _V29_SLACK_PEOPLE,
    _V29_SLACK_LINK_OFFERS,
    _V29_SLACK_EVENTS,
    _V29_SLACK_NOTES,
    _V29_SLACK_NOTES_DUE,
    _V29_SLACK_TEAM,
)
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
_METERING = _V11_METERING
# Nothing reads or writes the tables billing kept any more (subscriptions, billing_messages
# and, above, checkouts): cairn sells no plan. They stay so that every schema step stays and
# an older database opens; deleting a workspace deletes its rows in them, and an export
# leaves them out.
# Billing (H7). The subscription a workspace holds, as its payment provider last said, with
# the provider's references for the customer and the subscription (the portal needs the
# customer's; nothing about a payment method is kept). And every provider message applied
# or refused as out of order, by (provider, id), so a message counts once; each names its
# workspace, so deleting the workspace deletes them. Seen messages are never pruned.
_V12_SUBSCRIPTIONS = """CREATE TABLE subscriptions (
    account_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,  -- the BillingProvider's name
    plan TEXT NOT NULL,  -- a name in the server's plans (cairn.plans)
    status TEXT NOT NULL CHECK (status IN ('active', 'past_due', 'canceled')),
    period_end TEXT,  -- the end of the paid period; NULL: the provider named none
    since TEXT NOT NULL,  -- when the provider made the newest message applied
    customer_ref TEXT,  -- the provider's own references, shown nowhere
    subscription_ref TEXT
) WITHOUT ROWID"""
_V12_BILLING_MESSAGES = """CREATE TABLE billing_messages (
    provider TEXT NOT NULL,
    id TEXT NOT NULL,  -- the provider's message id
    account_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (provider, id)
) WITHOUT ROWID"""
_V12_BILLING_MESSAGES_BY_ACCOUNT = (
    "CREATE INDEX billing_messages_by_account ON billing_messages (account_id)"
)
_V12_BILLING = (_V12_SUBSCRIPTIONS, _V12_BILLING_MESSAGES, _V12_BILLING_MESSAGES_BY_ACCOUNT)
# When the subscription held began, as its provider said (which of two subscriptions was
# bought later), and when it became past due (the seven days count from there).
_V13_SUBSCRIPTIONS = """CREATE TABLE subscriptions (
    account_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,  -- the BillingProvider's name
    plan TEXT NOT NULL,  -- a name in the server's plans (cairn.plans)
    status TEXT NOT NULL CHECK (status IN ('active', 'past_due', 'canceled')),
    period_end TEXT,  -- the end of the paid period; NULL: the provider named none
    since TEXT NOT NULL,  -- when the provider made the newest message applied
    customer_ref TEXT,  -- the provider's own references, shown nowhere
    subscription_ref TEXT,
    started TEXT,  -- when the subscription began; NULL: the provider did not say
    past_due_since TEXT  -- when it became past due; NULL unless it is
) WITHOUT ROWID"""
_BILLING = (_V13_SUBSCRIPTIONS, _V12_BILLING_MESSAGES, _V12_BILLING_MESSAGES_BY_ACCOUNT)
# Model-use reservations (T4b; store/reservations.py). What a model call may cost, booked into
# its month's usage.model_micros before the call and replaced by what it spent after; a row
# is a reservation not settled yet, counted as spent in full.
_V14_MODEL_RESERVATIONS = """CREATE TABLE model_reservations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    period TEXT NOT NULL,  -- the UTC month it is booked in, "YYYY-MM"
    micros INTEGER NOT NULL,  -- millionths of a US dollar
    reserved_at TEXT NOT NULL
) WITHOUT ROWID"""
_V14_MODEL_RESERVATIONS_BY_ACCOUNT = (
    "CREATE INDEX model_reservations_by_account ON model_reservations (account_id)"
)
_V14_RESERVATIONS = (_V14_MODEL_RESERVATIONS, _V14_MODEL_RESERVATIONS_BY_ACCOUNT)
_RESERVATIONS = _V14_RESERVATIONS
# Forgotten notes (H5b; store/erasure.py). The SHA-256 digest of each note forgotten in a
# project, so the same text is never stored in that project again; never the text itself. Rows go
# with their project and their workspace, and are never exported.
_V15_FORGOTTEN = """CREATE TABLE forgotten (
    project_id TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,  -- hex, as notes.text_sha256
    PRIMARY KEY (project_id, text_sha256)
) WITHOUT ROWID"""
_FORGOTTEN_NOTES = _V15_FORGOTTEN
# What the disk bounds count (H6c, cairn.store.usage), per workspace: its notes' passages
# (spans of a kind the compiler makes a claim of) and the bytes of every page version it
# keeps (body and claims, as stored). Kept by triggers, so every write that adds or removes
# a span or a version counts it in its own transaction, whichever area makes it. A span is
# counted through its note's project, so spans are deleted before their notes (as every
# deletion here does).
_V16_ACCOUNT_DISK = """CREATE TABLE account_disk (
    account_id TEXT PRIMARY KEY,
    passages INTEGER NOT NULL DEFAULT 0,
    history_bytes INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID"""
_V16_PASSAGE = "new.kind IN ('paragraph', 'bullet', 'code', 'table', 'quote')"
_V16_SIZE = "LENGTH(CAST({row}.body AS BLOB)) + LENGTH(CAST({row}.claims AS BLOB))"
_V16_COUNTERS = (
    _V16_ACCOUNT_DISK,
    f"""CREATE TRIGGER passage_added AFTER INSERT ON spans WHEN {_V16_PASSAGE}
BEGIN
    INSERT INTO account_disk (account_id, passages)
    SELECT projects.account_id, 1 FROM notes JOIN projects ON projects.id = notes.project_id
    WHERE notes.id = new.note_id
    ON CONFLICT (account_id) DO UPDATE SET passages = passages + 1;
END""",
    f"""CREATE TRIGGER passage_removed AFTER DELETE ON spans
WHEN {_V16_PASSAGE.replace("new.", "old.")}
BEGIN
    UPDATE account_disk SET passages = MAX(0, passages - 1) WHERE account_id =
    (SELECT projects.account_id FROM notes JOIN projects ON projects.id = notes.project_id
     WHERE notes.id = old.note_id);
END""",
    f"""CREATE TRIGGER version_added AFTER INSERT ON page_versions
BEGIN
    INSERT INTO account_disk (account_id, history_bytes)
    SELECT account_id, {_V16_SIZE.format(row="new")} FROM projects WHERE id = new.project_id
    ON CONFLICT (account_id) DO UPDATE SET
    history_bytes = history_bytes + excluded.history_bytes;
END""",
    f"""CREATE TRIGGER version_removed AFTER DELETE ON page_versions
BEGIN
    UPDATE account_disk SET history_bytes = MAX(0, history_bytes - ({_V16_SIZE.format(row="old")}))
    WHERE account_id = (SELECT account_id FROM projects WHERE id = old.project_id);
END""",
)
# From _HEADING_CHARGE, what the passages counter counts for a span is its charge
# (cairn.store.usage.charge_of): one if it is a passage, and one for every full 40 bytes
# (BYTES_PER_PASSAGE) of its heading path as stored, less JSON's brackets, quotes and
# separators (4 a heading, as json.dumps writes a list; 2 for an empty path's brackets,
# which the division drops).
_V18_CHARGE = (
    "(({row}.kind IN ('paragraph', 'bullet', 'code', 'table', 'quote'))"
    " + MAX(0, LENGTH(CAST({row}.heading_path AS BLOB))"
    " - 4 * json_array_length({row}.heading_path)) / 40)"
)
_V18_PASSAGES = (
    f"""CREATE TRIGGER passage_added AFTER INSERT ON spans WHEN {_V18_CHARGE.format(row="new")} > 0
BEGIN
    INSERT INTO account_disk (account_id, passages)
    SELECT projects.account_id, {_V18_CHARGE.format(row="new")}
    FROM notes JOIN projects ON projects.id = notes.project_id
    WHERE notes.id = new.note_id
    ON CONFLICT (account_id) DO UPDATE SET passages = passages + excluded.passages;
END""",
    f"""CREATE TRIGGER passage_removed AFTER DELETE ON spans
WHEN {_V18_CHARGE.format(row="old")} > 0
BEGIN
    UPDATE account_disk SET passages = MAX(0, passages - {_V18_CHARGE.format(row="old")})
    WHERE account_id = (SELECT projects.account_id FROM notes
    JOIN projects ON projects.id = notes.project_id WHERE notes.id = old.note_id);
END""",
)
_DISK_COUNTERS = (_V16_ACCOUNT_DISK, *_V18_PASSAGES, *_V16_COUNTERS[3:])
_SCHEMA = (
    _ACCOUNTS,
    _PROJECTS,
    _API_KEYS,
    _API_KEYS_BY_LABEL,
    _SESSIONS,
    _NOTES,
    _NOTES_BY_TEXT,
    _SPANS,
    _PAGE_VERSIONS,
    *_OAUTH,
    *_PEOPLE,
    _PROJECT_SETS,
    *_SEARCH,
    *_AUDIT,
    _SOURCES,
    *_ACCESS,
    *_METERING,
    *_BILLING,
    *_RESERVATIONS,
    _FORGOTTEN_NOTES,
    *_DISK_COUNTERS,
    _SOURCE_ITEMS_TABLE,
    _V24_PAGE_KEYS,
    _V24_WAITING,
    *_V25_WRITERS,
    *_V26_HEADS,
    _V27_CHECKOUTS,
    _V28_CREDENTIAL_TOKENS,
    _V28_GOOGLE_REVOCATIONS,
    *_V29_SLACK,
)
# One search index per account, made by the account's first save_page, where its searches
# match (cairn.store.ranking scores them with the statistics above, never FTS5's own). One row
# per claim of each page's latest version; save_page replaces the page's rows.
_CLAIM_INDEX = """CREATE VIRTUAL TABLE {index} USING fts5 (
    project_id UNINDEXED, slug UNINDEXED, claim_id UNINDEXED, span_ids UNINDEXED, title, text
)"""


# Schema versions


# The store's clock (StoreBase._stamp) while _upgrade runs: a step takes the connection
# alone, and the one that stamps a row (the default account's creation) reads it from here.
_STAMP: ContextVar[Callable[[], str]] = ContextVar("cairn_store_upgrade_stamp")


def _upgrade(con: sqlite3.Connection, stamp: Callable[[], str]) -> None:
    """Bring the database to SCHEMA_VERSION, one transaction per step, so a step that fails
    leaves the database as it was. A current database is not written to, and a newer one is
    refused before anything is. `stamp`: the store's clock, as an ISO timestamp."""
    reset = _STAMP.set(stamp)
    try:
        while _schema_version(con) < SCHEMA_VERSION:
            with _transaction(con):
                # Read again under the write lock: another process may have done this step.
                version = _schema_version(con)
                if version == 0:
                    _create(con)
                    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                elif version < SCHEMA_VERSION:
                    _MIGRATIONS[version + 1](con)
                    con.execute(f"PRAGMA user_version = {version + 1}")
        _check_tables(con)
    finally:
        _STAMP.reset(reset)


_TABLES = tuple(
    re.match(r"CREATE TABLE (\w+)", statement).group(1)
    for statement in _SCHEMA
    if statement.startswith("CREATE TABLE ")
)


@functools.cache
def _columns() -> dict[str, tuple[str, ...]]:
    """Each table of the current schema and its columns, as SQLite reads the schema itself."""
    made = sqlite3.connect(":memory:")
    try:
        for statement in _SCHEMA:
            made.execute(statement)
        return {
            table: tuple(row[1] for row in made.execute(f"PRAGMA table_info({table})"))
            for table in _TABLES
        }
    finally:
        made.close()


def _check_tables(con: sqlite3.Connection) -> None:
    """UnknownSchema when a table of the current schema, or a column of one, is not in the
    database: a read that writes nothing."""
    held = {name for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for table, columns in _columns().items():
        if table not in held:
            raise UnknownSchema(
                "the database was not made by this version of cairn: it has schema version"
                f" {SCHEMA_VERSION} but no table {table}"
            )
        found = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        missing = [column for column in columns if column not in found]
        if missing:
            raise UnknownSchema(
                "the database was not made by this version of cairn: it has schema version"
                f" {SCHEMA_VERSION} but its table {table} has no column {missing[0]}"
            )


def _schema_version(con: sqlite3.Connection) -> int:
    """`PRAGMA user_version`, where 0 is an empty file. v0 never stamped its schema, so
    version 0 with tables reads as 1. SchemaTooNew: a newer cairn wrote the database."""
    (version,) = con.execute("PRAGMA user_version").fetchone()
    if version > SCHEMA_VERSION:
        raise SchemaTooNew(
            f"the database has schema version {version}; this cairn knows up to {SCHEMA_VERSION}"
        )
    if version == 0 and con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
        return 1
    return version


def _create(con: sqlite3.Connection) -> None:
    """The current schema, in an empty database."""
    for statement in _SCHEMA:
        con.execute(statement)
    _insert_account(con, Account(id=DEFAULT_ACCOUNT_ID, name="default", created_at=_STAMP.get()()))
    con.execute("INSERT INTO recorded_since (at) VALUES (?)", (_STAMP.get()(),))


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


def _add_audit(con: sqlite3.Connection) -> None:
    """Schema 5 to 6: settings and the audit log, empty, and sessions gain the label of the key
    they were opened with, empty on every session that existed. Sessions are rebuilt (as in
    _add_accounts) so that a migrated schema reads exactly like a fresh one."""
    for statement in _V6_AUDIT:
        con.execute(statement)
    columns = "digest, account_id, expires_at, created_at, user_id"
    con.execute("ALTER TABLE sessions RENAME TO sessions_v5")
    con.execute(_V6_SESSIONS)
    con.execute(
        f"INSERT INTO sessions ({columns}) SELECT {columns} FROM sessions_v5 ORDER BY rowid"
    )
    con.execute("DROP TABLE sessions_v5")


def _add_actor_ids(con: sqlite3.Connection) -> None:
    """Schema 6 to 7: every key gets an id of its own ("key_" + 12 hex chars), sessions the
    id of the key they were opened with (empty on every session that existed: their key is
    not known by id), and audit events the id of their actor (empty on every event already
    recorded) and their number in their workspace (counted in the order they were
    recorded), indexed by it instead of by seq. Each table is rebuilt (as in _add_accounts),
    so that a migrated schema reads exactly like a fresh one."""
    con.execute("ALTER TABLE api_keys RENAME TO api_keys_v6")
    con.execute(_V7_API_KEYS)
    columns = "seq, digest, account_id, label, created_at, projects, access"
    for (seq,) in con.execute("SELECT seq FROM api_keys_v6 ORDER BY seq").fetchall():
        con.execute(
            f"INSERT INTO api_keys (id, {columns}) SELECT ?, {columns} FROM api_keys_v6"
            " WHERE seq = ?",
            ("key_" + secrets.token_hex(6), seq),
        )
    con.execute("DROP TABLE api_keys_v6")  # and with it its index
    con.execute(_V5_API_KEYS_BY_LABEL)
    columns = "digest, account_id, expires_at, created_at, user_id, label"
    con.execute("ALTER TABLE sessions RENAME TO sessions_v6")
    con.execute(_V7_SESSIONS)
    con.execute(
        f"INSERT INTO sessions ({columns}) SELECT {columns} FROM sessions_v6 ORDER BY rowid"
    )
    con.execute("DROP TABLE sessions_v6")
    columns = "seq, account_id, at, actor_kind, actor, action, project, detail"
    con.execute("ALTER TABLE audit_events RENAME TO audit_events_v6")
    con.execute(_V7_AUDIT_EVENTS)
    con.execute(
        f"INSERT INTO audit_events (n, {columns})"
        f" SELECT ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY seq), {columns}"
        " FROM audit_events_v6 ORDER BY seq"
    )
    con.execute("DROP TABLE audit_events_v6")  # and with it its index
    con.execute(_V7_AUDIT_EVENTS_BY_NUMBER)


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


def _add_sources(con: sqlite3.Connection) -> None:
    """Schema 8 to 9: sources, empty. Nothing that existed changes."""
    con.execute(_V9_SOURCES)


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


def _add_billing(con: sqlite3.Connection) -> None:
    """Schema 11 to 12: subscriptions and the provider messages seen, both empty. Every
    workspace stays on the plan the operator set. Nothing that existed changes."""
    for statement in _V12_BILLING:
        con.execute(statement)


def _add_subscription_times(con: sqlite3.Connection) -> None:
    """Schema 12 to _SUBSCRIPTION_TIMES: each subscription gains when it started and when
    it became past due, both unknown for the rows that exist (a past-due one keeps counting
    its seven days from its message, as before). Every row is kept."""
    columns = "account_id, provider, plan, status, period_end, since, customer_ref,"
    columns += " subscription_ref"
    con.execute("ALTER TABLE subscriptions RENAME TO subscriptions_v12")
    con.execute(_V13_SUBSCRIPTIONS)
    con.execute(f"INSERT INTO subscriptions ({columns}) SELECT {columns} FROM subscriptions_v12")
    con.execute("DROP TABLE subscriptions_v12")


def _add_reservations(con: sqlite3.Connection) -> None:
    """Schema 13 to 14: model-use reservations, empty. Nothing that existed changes."""
    for statement in _V14_RESERVATIONS:
        con.execute(statement)


def _add_forgotten(con: sqlite3.Connection) -> None:
    """Schema 14 to _FORGOTTEN: the digests of forgotten notes, empty. Nothing that existed
    changes."""
    con.execute(_V15_FORGOTTEN)


def _add_disk_counters(con: sqlite3.Connection) -> None:
    """Schema 15 to _DISK_BOUNDS: each workspace's passages and page history, counted from
    the rows it holds, and the triggers that keep both counts from now on. Nothing that
    existed changes."""
    con.execute(_V16_ACCOUNT_DISK)
    con.execute(
        "INSERT INTO account_disk (account_id, passages)"
        " SELECT projects.account_id, COUNT(*) FROM spans"
        " JOIN notes ON notes.id = spans.note_id"
        " JOIN projects ON projects.id = notes.project_id"
        f" WHERE {_V16_PASSAGE.replace('new.', 'spans.')} GROUP BY projects.account_id"
    )
    con.execute(
        "INSERT INTO account_disk (account_id, history_bytes)"
        f" SELECT projects.account_id, SUM({_V16_SIZE.format(row='page_versions')})"
        " FROM page_versions JOIN projects ON projects.id = page_versions.project_id"
        " WHERE true GROUP BY projects.account_id"
        " ON CONFLICT (account_id) DO UPDATE SET history_bytes = excluded.history_bytes"
    )
    for statement in _V16_COUNTERS[1:]:
        con.execute(statement)


def _end_key_sessions(con: sqlite3.Connection) -> None:
    """Schema _DISK_BOUNDS to _KEY_SESSIONS: no table changes. From now on revoking a key
    ends the browser sessions opened with it, found by the key's id (`sessions.key_id`,
    kept since schema 7). A session opened with a key that this step cannot tie to a key
    that still exists ends here: one recorded before key ids were kept (a label and no key
    id), and one whose key was revoked before this version, which outlived it. Its browser
    signs in again. Sessions opened through an identity provider (no label) are kept."""
    con.execute(
        "DELETE FROM sessions WHERE label IS NOT NULL"
        " AND (key_id IS NULL OR key_id NOT IN (SELECT id FROM api_keys))"
    )


def _charge_headings(con: sqlite3.Connection) -> None:
    """Schema _KEY_SESSIONS to _HEADING_CHARGE: the passages counter charges the heading
    path stored with each span (_V18_CHARGE), from the rows each workspace holds, and its
    triggers count so from now on. A path stored with JSON's \\u escapes, as cairn wrote
    them before paths were stored as UTF-8, is charged as stored. Nothing else changes, but
    a workspace's count rises and can pass its plan's cap: then its new notes are refused
    until some are removed or the plan changes."""
    con.execute("DROP TRIGGER passage_added")
    con.execute("DROP TRIGGER passage_removed")
    con.execute("UPDATE account_disk SET passages = 0")
    con.execute(
        "INSERT INTO account_disk (account_id, passages)"
        f" SELECT projects.account_id, SUM({_V18_CHARGE.format(row='spans')}) FROM spans"
        " JOIN notes ON notes.id = spans.note_id"
        " JOIN projects ON projects.id = notes.project_id"
        " WHERE true GROUP BY projects.account_id"
        " ON CONFLICT (account_id) DO UPDATE SET passages = excluded.passages"
    )
    for statement in _V18_PASSAGES:
        con.execute(statement)


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


def _add_source_switch(con: sqlite3.Connection) -> None:
    """Schema _MANAGE_LEVEL to _SOURCE_SWITCH (S2): a source is on or off, and records when
    it was last switched on. Every source that exists read from the moment it was added, so
    each is on, switched on at its `created_at`. Sources are rebuilt (as in _add_kinds) so
    that a migrated schema reads exactly like a fresh one; every other column is kept, a
    claim a run holds included."""
    columns = (
        "seq, account_id, name, kind, config, cursor, created_at, last_run_at, last_report,"
        " last_error, claim, claimed_at"
    )
    con.execute("ALTER TABLE sources RENAME TO sources_v20")
    con.execute(_V21_SOURCES)
    con.execute(
        f"INSERT INTO sources ({columns}, enabled, enabled_at)"
        f" SELECT {columns}, 1, created_at FROM sources_v20 ORDER BY seq"
    )
    con.execute("DROP TABLE sources_v20")


def _add_vouched_email(con: sqlite3.Connection) -> None:
    """Schema _SOURCE_SWITCH to _VOUCHED_EMAIL (B1): a session records the address the
    identity provider vouched for at the sign-in it was made by, or that the session it was
    made from carries (switching workspace, taking an invitation), so `session-end` finds a
    person by the address the allowlist checks now, not only by the one they first signed in
    with. Sessions are rebuilt (as in _add_actor_ids) so that a migrated schema reads exactly
    like a fresh one; every session that exists is kept as it was, with no address: one made
    before this step is found by its person's stored address only."""
    columns = "digest, account_id, expires_at, created_at, user_id, label, key_id"
    con.execute("ALTER TABLE sessions RENAME TO sessions_v21")
    con.execute(_V22_SESSIONS)
    con.execute(
        f"INSERT INTO sessions ({columns}) SELECT {columns} FROM sessions_v21 ORDER BY rowid"
    )
    con.execute("DROP TABLE sessions_v21")


def _add_source_items(con: sqlite3.Connection) -> None:
    """Schema _VOUCHED_EMAIL to _SOURCE_ITEMS (I1): the items each source wrote, empty. No
    source recorded an item before, so every source starts with none; nothing that existed
    changes."""
    con.execute(_V23_SOURCE_ITEMS)


# The topic key as N1 defined it (cairn.topics.topic_key on 2026-11-14), copied here so that
# what _add_topic_keys did never changes when the rule does: a step is history.
_V24_SEPARATORS = re.compile(r"[\s\-_/]+")
_V24_EDGE = ".,;:!?\"'()[]{}\u2026\u2013\u2014"
_V24_FOLDED = ("LATIN ", "GREEK ")


def _v24_topic_key(title: str) -> str:
    """N1's topic key, as it was when _add_topic_keys was written."""
    text = unicodedata.normalize("NFD", unicodedata.normalize("NFD", title).casefold())
    kept: list[str] = []
    folds = False
    for char in text:
        if unicodedata.combining(char):
            if folds:
                continue
        else:
            folds = unicodedata.name(char, "").startswith(_V24_FOLDED)
        kept.append(char)
    words = (word.strip(_V24_EDGE) for word in _V24_SEPARATORS.split("".join(kept)))
    return unicodedata.normalize("NFC", " ".join(word for word in words if word))


_V24_NOTE_COLUMNS = (
    "seq, id, project_id, text, text_sha256, title, topic, label, source_ref, via, created_at"
)
_V24_SPAN_TRIGGERS = ("passage_added", "passage_removed")  # the triggers that name notes


def _add_topic_keys(con: sqlite3.Connection) -> None:
    """Schema _SOURCE_ITEMS to _TOPIC_KEYS (N1): each page and each note gets a topic key,
    and the notes no page cites wait for their page.

    Pages: each page keeps its slug and takes the key of its latest version's title, by the
    rule as it was when this step was written (_v24_topic_key). A page that merged topics
    before (C++ and C# notes filed on `c`) stays merged under the key of its latest title.
    When two pages' latest titles have one key (only pages saved outside the compiler can),
    the first by slug takes it and the other has none.

    Notes: a note filed on a page (a span of it cited by the page's latest version) takes
    that page's key, so it is the same note on the page it is on, whatever its own title
    says; any other note takes the key of its topic, else its title (stored cut to 80
    characters before this step). Every note this step finds is marked `before_topics`:
    sent again, it is the same note whatever topic or title it comes with, as it was before
    this step, so a resend that was answered "unchanged" still is. Notes are rebuilt (as
    in _add_vouched_email) so that a migrated schema reads exactly like a fresh one, every
    row kept in its order; the two triggers on spans that name notes are dropped first
    and made again after, as they were.

    Waiting: every note that no page's latest version cites waits for its page, so that
    `serve` files it (cairn.web.filing): a note stored and never compiled gets its page; one
    the gate rejected, or refused for a full page, is compiled once more, and its outcome,
    recorded, ends its wait. A note cited only by a page's older versions waits too: it is
    on no page today."""
    con.create_function("cairn_v24_topic_key", 1, _v24_topic_key, deterministic=True)
    con.execute(_V24_PAGE_KEYS)
    con.execute(
        "INSERT OR IGNORE INTO page_keys (project_id, slug, topic_key)"
        " SELECT project_id, slug, cairn_v24_topic_key(title) FROM page_versions AS p"
        " WHERE version = (SELECT MAX(version) FROM page_versions"
        "  WHERE project_id = p.project_id AND slug = p.slug)"
        " ORDER BY project_id, slug"
    )
    con.execute(
        "CREATE TABLE filed_v24 (note_id TEXT PRIMARY KEY, topic_key TEXT NOT NULL) WITHOUT ROWID"
    )
    con.execute(
        "INSERT OR IGNORE INTO filed_v24 (note_id, topic_key)"
        " SELECT spans.note_id, page_keys.topic_key FROM page_versions AS p"
        " JOIN page_keys ON page_keys.project_id = p.project_id AND page_keys.slug = p.slug,"
        " json_each(p.claims) AS claim, json_each(claim.value, '$.span_ids') AS cited"
        " JOIN spans ON spans.id = cited.value"
        " WHERE p.version = (SELECT MAX(version) FROM page_versions"
        "  WHERE project_id = p.project_id AND slug = p.slug)"
    )
    for trigger in _V24_SPAN_TRIGGERS:
        con.execute(f"DROP TRIGGER {trigger}")
    con.execute("ALTER TABLE notes RENAME TO notes_v23")
    con.execute(_V24_NOTES)
    columns = _V24_NOTE_COLUMNS
    con.execute(
        f"INSERT INTO notes ({columns}, topic_key, before_topics)"
        f" SELECT {', '.join(f'c.{name.strip()}' for name in columns.split(','))},"
        " COALESCE(filed_v24.topic_key, cairn_v24_topic_key(COALESCE(NULLIF(c.topic, ''),"
        " c.title))), 1 FROM notes_v23 AS c"
        " LEFT JOIN filed_v24 ON filed_v24.note_id = c.id ORDER BY c.seq"
    )
    con.execute("DROP TABLE notes_v23")  # and with it its index
    con.execute("DROP TABLE filed_v24")
    con.execute(_V2_NOTES_BY_TEXT)
    for statement in _V18_PASSAGES:
        con.execute(statement)
    con.execute(_V24_WAITING)
    con.execute(
        "INSERT INTO waiting (note_id, project_id) SELECT id, project_id FROM notes"
        " WHERE id NOT IN (SELECT spans.note_id FROM page_versions AS p,"
        " json_each(p.claims) AS claim, json_each(claim.value, '$.span_ids') AS cited"
        " JOIN spans ON spans.id = cited.value"
        " WHERE p.version = (SELECT MAX(version) FROM page_versions"
        "  WHERE project_id = p.project_id AND slug = p.slug))"
    )


def _add_writers(con: sqlite3.Connection) -> None:
    """Schema _TOPIC_KEYS to _WRITERS (A1): who wrote each note, and the key each key or
    agent's grant was made with, both empty, so every note that exists has no recorded writer
    ("unknown") and every key and grant that exists records no maker; and the moment this
    step ran, read from the store's clock, by which a key made before it is known to have
    made what nothing records. Nothing that existed changes."""
    for statement in _V25_WRITERS:
        con.execute(statement)
    con.execute("INSERT INTO recorded_since (at) VALUES (?)", (_STAMP.get()(),))


def _add_page_heads(con: sqlite3.Connection) -> None:
    """Schema _WRITERS to _PAGE_HEADS (Q1): page_heads and its triggers, filled with every
    page's latest version as it is. Nothing that existed changes."""
    for statement in _V26_HEADS:
        con.execute(statement)
    con.execute(
        "INSERT INTO page_heads (project_id, slug, version, title, claims, updated_at)"
        " SELECT project_id, slug, version, title, json_array_length(claims), created_at"
        " FROM page_versions AS v WHERE version = (SELECT MAX(version) FROM page_versions"
        " WHERE project_id = v.project_id AND slug = v.slug)"
    )


def _add_checkouts(con: sqlite3.Connection) -> None:
    """Schema _PAGE_HEADS to _CHECKOUTS (R3): the checkouts table, empty. A checkout open
    in a server's memory when it stopped is not carried over. Nothing that existed
    changes."""
    con.execute(_V27_CHECKOUTS)


def _add_credential_tokens(con: sqlite3.Connection) -> None:
    """Schema _CHECKOUTS to _CREDENTIAL_TOKENS: the credential_tokens and google_revocations
    tables, empty. The settings rows of the credentials registered before stay as they are.
    Nothing that existed changes. One step for both: google_revocations joined it before any
    database outside the tests ran its earlier form, and a step runs in one transaction with
    its version, so a database at 28 holds both tables; one that does not is refused on
    opening (_check_tables). The same holds for the columns the step's tables gained before
    any database outside the tests ran it (credential_tokens.subject and ended_at; the
    waiting rows' subject, claim and back-off): a database at 28 that lacks a column of them
    is refused too."""
    con.execute(_V28_CREDENTIAL_TOKENS)
    con.execute(_V28_GOOGLE_REVOCATIONS)


def _add_slack(con: sqlite3.Connection) -> None:
    """Schema _CREDENTIAL_TOKENS to _SLACK (K1): the five Slack tables, empty, whether or not
    the server is ever started with --slack. Nothing that existed changes."""
    for statement in _V29_SLACK:
        con.execute(statement)


# Each step takes the schema from one version to the next, keyed by the version it leaves. A
# step is history: it uses its own copy of every DDL statement that a later version changes
# (the _V<n>_ constants), never the current schema's.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    2: _add_accounts,
    3: _add_oauth,
    4: _add_people,
    5: _add_reach,
    6: _add_audit,
    7: _add_actor_ids,
    8: _add_kinds,
    9: _add_sources,
    10: _add_access,
    11: _add_metering,
    12: _add_billing,
    _SUBSCRIPTION_TIMES: _add_subscription_times,
    14: _add_reservations,
    _FORGOTTEN: _add_forgotten,
    _DISK_BOUNDS: _add_disk_counters,
    _KEY_SESSIONS: _end_key_sessions,
    _HEADING_CHARGE: _charge_headings,
    _GRANT_AUDIENCE: _add_grant_audience,
    _MANAGE_LEVEL: _add_manage_level,
    _SOURCE_SWITCH: _add_source_switch,
    _VOUCHED_EMAIL: _add_vouched_email,
    _SOURCE_ITEMS: _add_source_items,
    _TOPIC_KEYS: _add_topic_keys,
    _WRITERS: _add_writers,
    _PAGE_HEADS: _add_page_heads,
    _CHECKOUTS: _add_checkouts,
    _CREDENTIAL_TOKENS: _add_credential_tokens,
    _SLACK: _add_slack,
}


def _create_index(con: sqlite3.Connection, index: str) -> None:
    con.execute(_CLAIM_INDEX.format(index=index))
    _delete_securely(con, index)


def _secure_index(con: sqlite3.Connection, index: str) -> None:
    """Turn FTS5's secure-delete on for an index made without it (by an SQLite older than
    3.42), at a write to it, never at open: opening a current database writes nothing."""
    if (
        _FTS5_SECURE_DELETE
        and not con.execute(
            f"SELECT 1 FROM {index}_config WHERE k = 'secure-delete' AND v = 1"
        ).fetchone()
    ):
        _delete_securely(con, index)


def _delete_securely(con: sqlite3.Connection, index: str) -> None:
    """Make the index remove a replaced claim's terms instead of marking them deleted. An
    SQLite without the option keeps the marks until FTS5 merges the segments."""
    if _FTS5_SECURE_DELETE:
        con.execute(f"INSERT INTO {index} ({index}, rank) VALUES ('secure-delete', 1)")
