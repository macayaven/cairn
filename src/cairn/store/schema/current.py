"""The current schema, one statement each, as a new database is made (_SCHEMA): most are
a step's own statements, named after the version that made them."""

from cairn.store.schema.v02_accounts import _ACCOUNTS, _API_KEYS_BY_LABEL, _V2_NOTES_BY_TEXT
from cairn.store.schema.v03_oauth import _OAUTH_CLIENTS, _OAUTH_TOKENS, _OAUTH_TOKENS_BY_GRANT
from cairn.store.schema.v04_people import _PEOPLE, _V4_OAUTH_GRANTS_BY_ACCOUNT
from cairn.store.schema.v05_reach import _V5_PROJECT_SETS, _V5_SEARCH
from cairn.store.schema.v10_access import (
    _V10_API_KEYS,
    _V10_INVITES,
    _V10_PROJECT_PEOPLE_BY_USER,
    _V10_PROJECTS,
    _V10_REMOVALS,
)
from cairn.store.schema.v11_metering import _V11_METERING
from cairn.store.schema.v14_reservations import _V14_RESERVATIONS
from cairn.store.schema.v15_forgotten import _V15_FORGOTTEN
from cairn.store.schema.v16_disk_counters import _V16_ACCOUNT_DISK, _V16_COUNTERS
from cairn.store.schema.v18_heading_charge import _V18_PASSAGES
from cairn.store.schema.v19_grant_audience import _AUDIENCE_OAUTH_CODES, _AUDIENCE_OAUTH_GRANTS
from cairn.store.schema.v20_manage_level import _V20_PROJECT_PEOPLE
from cairn.store.schema.v21_source_switch import _V21_SOURCES
from cairn.store.schema.v23_source_items import _V23_SOURCE_ITEMS
from cairn.store.schema.v24_topic_keys import _V24_NOTES, _V24_PAGE_KEYS, _V24_WAITING
from cairn.store.schema.v25_writers import _V25_WRITERS
from cairn.store.schema.v26_page_heads import _V26_HEADS
from cairn.store.schema.v28_credential_tokens import _V28_CREDENTIAL_TOKENS, _V28_GOOGLE_REVOCATIONS
from cairn.store.schema.v29_slack import _V29_SLACK
from cairn.store.schema.v30_last_use import _V30_SESSIONS
from cairn.store.schema.v31_oldest_build import _V31_AUDIT, _V31_OLDEST_BUILD

# The current schema, one statement each: a migration runs inside a transaction, and
# executescript would commit it.
_PROJECTS = _V10_PROJECTS
_ACCESS = (_V20_PROJECT_PEOPLE, _V10_PROJECT_PEOPLE_BY_USER, _V10_INVITES, _V10_REMOVALS)
_API_KEYS = _V10_API_KEYS
_SESSIONS = _V30_SESSIONS
_NOTES = _V24_NOTES
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
_OAUTH_GRANTS = _AUDIENCE_OAUTH_GRANTS
_OAUTH_GRANTS_BY_ACCOUNT = _V4_OAUTH_GRANTS_BY_ACCOUNT
_OAUTH_CODES = _AUDIENCE_OAUTH_CODES
_OAUTH = (
    _OAUTH_CLIENTS,
    _OAUTH_GRANTS,
    _OAUTH_GRANTS_BY_ACCOUNT,
    _OAUTH_CODES,
    _OAUTH_TOKENS,
    _OAUTH_TOKENS_BY_GRANT,
)
_PROJECT_SETS = _V5_PROJECT_SETS
_SEARCH = _V5_SEARCH
_AUDIT = _V31_AUDIT
_SOURCES = _V21_SOURCES
_SOURCE_ITEMS_TABLE = _V23_SOURCE_ITEMS
_METERING = _V11_METERING
_RESERVATIONS = _V14_RESERVATIONS
_FORGOTTEN_NOTES = _V15_FORGOTTEN
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
    *_RESERVATIONS,
    _FORGOTTEN_NOTES,
    *_DISK_COUNTERS,
    _SOURCE_ITEMS_TABLE,
    _V24_PAGE_KEYS,
    _V24_WAITING,
    *_V25_WRITERS,
    *_V26_HEADS,
    _V28_CREDENTIAL_TOKENS,
    _V28_GOOGLE_REVOCATIONS,
    *_V29_SLACK,
    _V31_OLDEST_BUILD,
)
