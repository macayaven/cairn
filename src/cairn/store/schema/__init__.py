"""The schema: its version, the upgrade loop, the list of steps in order and what opens and
checks a database. Each step is a module of its own holding its statements and its function
(v02_accounts to v31_oldest_build), the current schema is in current, and what the steps and
the store share is in shared. Every name schema.py held is importable from here."""
# Every name below is imported to be importable from the package, as it was from schema.py.
# ruff: noqa: F401

import functools
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

from cairn.store.base import DEFAULT_ACCOUNT_ID, _has_table, _insert_account, _transaction
from cairn.store.models import Account
from cairn.store.schema.current import (
    _ACCESS,
    _API_KEYS,
    _AUDIT,
    _DISK_COUNTERS,
    _FORGOTTEN_NOTES,
    _METERING,
    _NOTES,
    _NOTES_BY_TEXT,
    _OAUTH,
    _OAUTH_CODES,
    _OAUTH_GRANTS,
    _OAUTH_GRANTS_BY_ACCOUNT,
    _PAGE_VERSIONS,
    _PROJECT_SETS,
    _PROJECTS,
    _RESERVATIONS,
    _SCHEMA,
    _SEARCH,
    _SESSIONS,
    _SOURCE_ITEMS_TABLE,
    _SOURCES,
    _SPANS,
)
from cairn.store.schema.shared import (
    _AFTER,
    _CLAIM_INDEX,
    _FTS5_SECURE_DELETE,
    _STAMP,
    _create_index,
    _delete_securely,
    _secure_index,
)
from cairn.store.schema.v02_accounts import (
    _ACCOUNTS,
    _API_KEYS_BY_LABEL,
    _V0_COLUMNS,
    _V2_API_KEYS,
    _V2_NOTES,
    _V2_NOTES_BY_TEXT,
    _V2_PROJECTS,
    _V2_SESSIONS,
    _add_accounts,
)
from cairn.store.schema.v03_oauth import (
    _OAUTH_CLIENTS,
    _OAUTH_TOKENS,
    _OAUTH_TOKENS_BY_GRANT,
    _V3_OAUTH,
    _V3_OAUTH_CODES,
    _V3_OAUTH_GRANTS,
    _V3_OAUTH_GRANTS_BY_ACCOUNT,
    _add_oauth,
)
from cairn.store.schema.v04_people import (
    _PEOPLE,
    _V4_IDENTITIES,
    _V4_IDENTITIES_BY_USER,
    _V4_MEMBERSHIPS,
    _V4_MEMBERSHIPS_BY_USER,
    _V4_OAUTH_CODES,
    _V4_OAUTH_GRANTS,
    _V4_OAUTH_GRANTS_BY_ACCOUNT,
    _V4_SESSIONS,
    _V4_USERS,
    _add_people,
)
from cairn.store.schema.v05_reach import (
    _V5_API_KEYS,
    _V5_API_KEYS_BY_LABEL,
    _V5_OAUTH_CODES,
    _V5_OAUTH_GRANTS,
    _V5_OAUTH_GRANTS_BY_ACCOUNT,
    _V5_PROJECT_SETS,
    _V5_SEARCH,
    _V5_SEARCH_CLAIMS,
    _V5_SEARCH_CLAIMS_BY_PAGE,
    _V5_SEARCH_PROJECTS,
    _add_reach,
)
from cairn.store.schema.v06_audit import (
    _V6_AUDIT,
    _V6_AUDIT_EVENTS,
    _V6_AUDIT_EVENTS_BY_ACCOUNT,
    _V6_SESSIONS,
    _V6_SETTINGS,
    _add_audit,
)
from cairn.store.schema.v07_actor_ids import (
    _V7_API_KEYS,
    _V7_AUDIT,
    _V7_AUDIT_EVENTS,
    _V7_AUDIT_EVENTS_BY_NUMBER,
    _V7_SESSIONS,
    _add_actor_ids,
)
from cairn.store.schema.v08_kinds import _V8_PROJECTS, _add_kinds
from cairn.store.schema.v09_sources import _V9_SOURCES, _add_sources
from cairn.store.schema.v10_access import (
    _V10_ACCESS,
    _V10_API_KEYS,
    _V10_INVITES,
    _V10_PROJECT_PEOPLE,
    _V10_PROJECT_PEOPLE_BY_USER,
    _V10_PROJECTS,
    _V10_REMOVALS,
    _add_access,
)
from cairn.store.schema.v11_metering import (
    _V11_ACCOUNT_QUOTA,
    _V11_METERING,
    _V11_USAGE,
    _add_metering,
)
from cairn.store.schema.v12_billing import (
    _V12_BILLING,
    _V12_BILLING_MESSAGES,
    _V12_BILLING_MESSAGES_BY_ACCOUNT,
    _V12_SUBSCRIPTIONS,
    _add_billing,
)
from cairn.store.schema.v13_billing_times import _V13_SUBSCRIPTIONS, _add_subscription_times
from cairn.store.schema.v14_reservations import (
    _V14_MODEL_RESERVATIONS,
    _V14_MODEL_RESERVATIONS_BY_ACCOUNT,
    _V14_RESERVATIONS,
    _add_reservations,
)
from cairn.store.schema.v15_forgotten import _V15_FORGOTTEN, _add_forgotten
from cairn.store.schema.v16_disk_counters import (
    _V16_ACCOUNT_DISK,
    _V16_COUNTERS,
    _V16_PASSAGE,
    _V16_SIZE,
    _add_disk_counters,
)
from cairn.store.schema.v17_key_sessions import _end_key_sessions
from cairn.store.schema.v18_heading_charge import _V18_CHARGE, _V18_PASSAGES, _charge_headings
from cairn.store.schema.v19_grant_audience import (
    _AUDIENCE_OAUTH_CODES,
    _AUDIENCE_OAUTH_GRANTS,
    _add_grant_audience,
)
from cairn.store.schema.v20_manage_level import _V20_PROJECT_PEOPLE, _add_manage_level
from cairn.store.schema.v21_source_switch import _V21_SOURCES, _add_source_switch
from cairn.store.schema.v22_vouched_email import _V22_SESSIONS, _add_vouched_email
from cairn.store.schema.v23_source_items import _V23_SOURCE_ITEMS, _add_source_items
from cairn.store.schema.v24_topic_keys import (
    _V24_EDGE,
    _V24_FOLDED,
    _V24_NOTE_COLUMNS,
    _V24_NOTES,
    _V24_PAGE_KEYS,
    _V24_SEPARATORS,
    _V24_SPAN_TRIGGERS,
    _V24_WAITING,
    _add_topic_keys,
    _v24_topic_key,
)
from cairn.store.schema.v25_writers import (
    _V25_MADE_WITH,
    _V25_MADE_WITH_BY_KEY,
    _V25_NOTE_WRITERS,
    _V25_RECORDED_SINCE,
    _V25_WRITERS,
    _add_writers,
)
from cairn.store.schema.v26_page_heads import (
    _V26_HEAD_REMOVED,
    _V26_HEAD_SAVED,
    _V26_HEADS,
    _V26_PAGE_HEADS,
    _add_page_heads,
)
from cairn.store.schema.v27_checkouts import _V27_CHECKOUTS, _add_checkouts
from cairn.store.schema.v28_credential_tokens import (
    _V28_CREDENTIAL_TOKENS,
    _V28_GOOGLE_REVOCATIONS,
    _add_credential_tokens,
)
from cairn.store.schema.v29_slack import (
    _V29_SLACK,
    _V29_SLACK_EVENTS,
    _V29_SLACK_INSTALLS,
    _V29_SLACK_LINK_OFFERS,
    _V29_SLACK_NOTES,
    _V29_SLACK_NOTES_DUE,
    _V29_SLACK_PEOPLE,
    _V29_SLACK_TEAM,
    _add_slack,
)
from cairn.store.schema.v30_last_use import _V30_SESSIONS, _add_last_use
from cairn.store.schema.v31_oldest_build import (
    _V31_AUDIT,
    _V31_AUDIT_EVENTS,
    _V31_OLDEST_BUILD,
    _drop_retired,
    _v31_link,
)
from cairn.store.schema.v32_own_passages import _hold_to_own_projects

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
# The one place the Slack step's number is written (K1).
_SLACK = 29
# The one place the last-use step's number is written (sessions that are not used end).
_LAST_USE = 30
# The one place the oldest-build step's number is written (the retired tables go, the audit
# trail is chained, and the database records the oldest build that runs on it).
_OLDEST_BUILD = 31
# The one place the own-passages step's number is written (each page is held to its own
# project's notes; report 8, must-fix 2), and so the newest version.
_OWN_PASSAGES = 32
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
# notes that came through it (cairn.store.slack); _LAST_USE: a session records when it was
# last used (`serve --idle-minutes`); _OLDEST_BUILD: the tables billing kept go, and the
# database records the oldest build that runs on it (oldest_build); _OWN_PASSAGES: every page
# that cites a note of another project is rewritten with its own project's notes alone
SCHEMA_VERSION = _OWN_PASSAGES
# The steps that only add (tables, columns with defaults, indexes): a build older than such a
# step still runs on a database the step brought forward, so the floor stays where it was.
# Every other step changes what an older build relies on, and raises the database's floor to
# itself (_upgrade). The steps before _SLACK never said, and count as changing.
_ONLY_ADDS = frozenset({_SLACK, _LAST_USE})


class SchemaTooNew(RuntimeError):
    """The database was written by a newer cairn than this one, and one that this one may
    not run on: its floor (oldest_build) is above this cairn's version, or it records none.
    It is left untouched."""


class SchemaTooOld(RuntimeError):
    """The database has an older schema than this cairn's, and the store was opened with
    upgrade=False (Store): refused while it opens, under the write lock of the step that would
    have brought it up, before anything is written; the file is left as it was. `found` is
    the database's version; the message names it and SCHEMA_VERSION."""

    def __init__(self, found: int) -> None:
        self.found = found
        super().__init__(
            f"the database has schema version {found}; this cairn's is {SCHEMA_VERSION}"
        )


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


# Schema versions


def _upgrade(con: sqlite3.Connection, stamp: Callable[[], str], upgrade: bool = True) -> None:
    """Bring the database to SCHEMA_VERSION, one transaction per step, so a step that fails
    leaves the database as it was. With `upgrade` False, an older database is refused
    (SchemaTooOld) by the first step, from the version it reads under its write lock, and
    the step rolls back having written nothing; an empty file is still made at this schema.
    A current database is not written to. A newer one is
    opened as it is, with no step run and nothing written, when this cairn's version is at or
    above the floor it records (oldest_build), and refused before anything is written
    otherwise (SchemaTooNew). From _OLDEST_BUILD on, a step that changes what an older build
    relies on (not in _ONLY_ADDS) raises the floor to itself, in its own transaction. What a
    step says (_AFTER) is said once that transaction has committed, never when it rolls back.
    `stamp`: the store's clock, as an ISO timestamp."""
    reset = _STAMP.set(stamp)
    try:
        while _schema_version(con) < SCHEMA_VERSION:
            said: list[Callable[[], None]] = []
            after = _AFTER.set(said)
            try:
                with _transaction(con):
                    _run_step(con, upgrade)
            finally:
                _AFTER.reset(after)
            for say in said:  # committed: the step's transaction is over
                say()
        _check_tables(con)
    finally:
        _STAMP.reset(reset)


def _run_step(con: sqlite3.Connection, upgrade: bool = True) -> None:
    """One step of _upgrade, in its transaction."""
    # Read again under the write lock: another process may have done this step.
    version = _schema_version(con)
    if version == 0:
        _create(con)
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version < SCHEMA_VERSION:
        if not upgrade:
            raise SchemaTooOld(version)
        step = version + 1
        _MIGRATIONS[step](con)
        if step >= _OLDEST_BUILD and step not in _ONLY_ADDS:
            _record_floor(con, step)
        con.execute(f"PRAGMA user_version = {step}")


def _floor_of(version: int) -> int:
    """The floor of a database this cairn's steps brought to `version`: the newest step up
    to it that is not in _ONLY_ADDS."""
    return max(step for step in range(1, version + 1) if step not in _ONLY_ADDS)


def _record_floor(con: sqlite3.Connection, version: int) -> None:
    con.execute(
        "INSERT INTO oldest_build (one, version) VALUES (1, ?)"
        " ON CONFLICT (one) DO UPDATE SET version = excluded.version",
        (version,),
    )


def _recorded_floor(con: sqlite3.Connection, version: int) -> int:
    """The floor a database at `version` records; `version` itself when it records none (no
    oldest_build table, or no row in it): such a database is run by its own version only."""
    if not _has_table(con, "oldest_build"):
        return version
    row = con.execute("SELECT version FROM oldest_build WHERE one = 1").fetchone()
    return version if row is None else row[0]


def _runs_on(con: sqlite3.Connection) -> bool:
    """Whether this cairn runs on the database as it is now: its version is this cairn's, or
    newer with a floor at or below this cairn's. A read that writes nothing (Store.health)."""
    try:
        return _schema_version(con) >= SCHEMA_VERSION
    except SchemaTooNew:
        return False


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
    version 0 with tables reads as 1. A newer version is returned as it is when this cairn
    may run on it (its floor is at or below SCHEMA_VERSION); SchemaTooNew otherwise, in one
    sentence that names the database's version, its floor and this cairn's version."""
    (version,) = con.execute("PRAGMA user_version").fetchone()
    if version > SCHEMA_VERSION:
        floor = _recorded_floor(con, version)
        if floor > SCHEMA_VERSION:
            raise SchemaTooNew(
                f"the database has schema version {version} and needs a cairn of schema"
                f" version {floor} or later; this cairn knows up to {SCHEMA_VERSION}"
            )
        return version
    if version == 0 and con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
        return 1
    return version


def _create(con: sqlite3.Connection) -> None:
    """The current schema, in an empty database."""
    for statement in _SCHEMA:
        con.execute(statement)
    _insert_account(con, Account(id=DEFAULT_ACCOUNT_ID, name="default", created_at=_STAMP.get()()))
    con.execute("INSERT INTO recorded_since (at) VALUES (?)", (_STAMP.get()(),))
    _record_floor(con, _floor_of(SCHEMA_VERSION))


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
    _LAST_USE: _add_last_use,
    _OLDEST_BUILD: _drop_retired,
    _OWN_PASSAGES: _hold_to_own_projects,
}
