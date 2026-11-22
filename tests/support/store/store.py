"""Shared test helpers extracted from test_store_unit."""

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import cairn.store
import cairn.topics
from cairn.spans import chunk
from cairn.store import (
    DEFAULT_ACCOUNT_ID,
    Claim,
    Store,
)


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


def save(store, project, slug, title, *texts):
    claims = [
        Claim(id=f"cl_{slug}{n}", text=t, span_ids=(f"sp_{slug}{n}",)) for n, t in enumerate(texts)
    ]
    return store.save_page(project.id, slug, title, f"# {title}\n", claims)


V0_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the key; the key itself is never stored
    label TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS api_keys_by_label ON api_keys (label);
CREATE TABLE IF NOT EXISTS sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
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
);
CREATE INDEX IF NOT EXISTS notes_by_text ON notes (project_id, text_sha256);
-- Spans are stored, never re-chunked on read: their ids are permanent citations.
CREATE TABLE IF NOT EXISTS spans (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,  -- document order within the note
    kind TEXT NOT NULL,
    heading_path TEXT NOT NULL,  -- JSON array of heading texts
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (note_id, ordinal)
);
CREATE TABLE IF NOT EXISTS page_versions (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    claims TEXT NOT NULL,  -- JSON array of {"id", "text", "span_ids"}
    metrics TEXT NOT NULL,  -- JSON object: the gate's metrics for this version
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, slug, version)
);
-- One row per claim of each page's latest version; save_page replaces the page's rows.
CREATE VIRTUAL TABLE IF NOT EXISTS claim_search USING fts5 (
    project_id UNINDEXED, slug UNINDEXED, claim_id UNINDEXED, span_ids UNINDEXED, title, text
);
"""

LIVE, EXPIRED = "2999-01-01T00:00:00.000000+00:00", "2020-01-01T00:00:00.000000+00:00"


@contextmanager
def connect(db: Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(db)
    try:
        yield con
    finally:
        con.close()


def rows(db: Path, sql: str, *params: Any) -> list[tuple]:
    with connect(db) as con:
        return con.execute(sql, params).fetchall()


def tables(db: Path) -> set[str]:
    return {name for (name,) in rows(db, "SELECT name FROM sqlite_master WHERE type = 'table'")}


def user_version(db: Path) -> int:
    return rows(db, "PRAGMA user_version")[0][0]


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def stamp(day: int) -> str:
    return datetime(2026, 1, day, tzinfo=UTC).isoformat(timespec="microseconds")


def index_of(account_id: str) -> str:
    return f"claim_search_{account_id}"


def empty_v0_database(db: Path) -> None:
    with connect(db) as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(V0_SCHEMA)
    assert user_version(db) == 0


REDIRECT = "http://127.0.0.1:33418/callback"

CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # RFC 7636, appendix B

V2_SCHEMA = """
CREATE TABLE accounts (
    seq INTEGER PRIMARY KEY,  -- creation order, the default account first
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (account_id, name)
);
CREATE TABLE api_keys (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    digest TEXT NOT NULL UNIQUE,  -- SHA-256 hex of the key; the key itself is never stored
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX api_keys_by_label ON api_keys (account_id, label);
CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL
);
CREATE TABLE notes (
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
);
CREATE INDEX notes_by_text ON notes (project_id, text_sha256);
CREATE TABLE spans (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,  -- document order within the note
    kind TEXT NOT NULL,
    heading_path TEXT NOT NULL,  -- JSON array of heading texts
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (note_id, ordinal)
);
CREATE TABLE page_versions (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    claims TEXT NOT NULL,  -- JSON array of {"id", "text", "span_ids"}
    metrics TEXT NOT NULL,  -- JSON object: the gate's metrics for this version
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, slug, version)
);
CREATE VIRTUAL TABLE claim_search_acct_aaaaaaaaaaaa USING fts5 (
    project_id UNINDEXED, slug UNINDEXED, claim_id UNINDEXED, span_ids UNINDEXED, title, text
);
"""

V2_ADA = "acct_aaaaaaaaaaaa"


def statements(db: Path) -> list[str]:
    with connect(db) as con:
        return list(con.iterdump())


def v2_database(db: Path) -> dict[str, Any]:
    """A database as cairn wrote it at schema version 2: the default account and Ada, who has
    a project, a key, a live session, a note and a page in her own search index."""
    text = "# Relay notes\n\nThe relay retries twice.\n"
    spans = chunk("cap_000000000001", text)
    claim = {"id": "cl_1", "text": "The relay retries twice.", "span_ids": [spans[-1].id]}
    with connect(db) as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(V2_SCHEMA)
        con.executemany(
            "INSERT INTO accounts (id, name, created_at) VALUES (?, ?, ?)",
            [(DEFAULT_ACCOUNT_ID, "default", stamp(1)), (V2_ADA, "Ada", stamp(2))],
        )
        con.execute(
            "INSERT INTO projects VALUES ('br_aaaaaaaaaaaa', ?, 'core', 'hers', ?)",
            (V2_ADA, stamp(2)),
        )
        con.execute(
            "INSERT INTO api_keys (digest, account_id, label, created_at) VALUES (?, ?, ?, ?)",
            (digest("key-ada"), V2_ADA, "laptop", stamp(2)),
        )
        con.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (digest("token-ada"), V2_ADA, LIVE, stamp(2)),
        )
        con.execute(
            "INSERT INTO notes (id, project_id, text, text_sha256, title, created_at)"
            " VALUES ('cap_000000000001', 'br_aaaaaaaaaaaa', ?, ?, 'Relay notes', ?)",
            (text, digest(text), stamp(3)),
        )
        con.executemany(
            "INSERT INTO spans (id, note_id, ordinal, kind, heading_path, start_line,"
            " end_line, text) VALUES (?, 'cap_000000000001', ?, ?, ?, ?, ?, ?)",
            [
                (s.id, n, s.kind, json.dumps(s.heading_path), s.start_line, s.end_line, s.text)
                for n, s in enumerate(spans)
            ],
        )
        con.execute(
            "INSERT INTO page_versions VALUES ('br_aaaaaaaaaaaa', 'relay-notes', 1,"
            " 'Relay notes', '# Relay notes\n', ?, '{}', ?)",
            (json.dumps([claim]), stamp(3)),
        )
        con.execute(
            f"INSERT INTO {index_of(V2_ADA)} (project_id, slug, claim_id, span_ids, title, text)"
            " VALUES ('br_aaaaaaaaaaaa', 'relay-notes', 'cl_1', ?, 'Relay notes', ?)",
            (json.dumps(claim["span_ids"]), claim["text"]),
        )
        con.execute("PRAGMA user_version = 2")
        con.commit()
    assert user_version(db) == 2
    return {"spans": spans, "claim": claim, "statements": statements(db)}


V3_OAUTH_SCHEMA = """
CREATE TABLE oauth_clients (
    seq INTEGER PRIMARY KEY,  -- registration order
    id TEXT NOT NULL UNIQUE,  -- the public client_id; a client has no secret
    name TEXT NOT NULL,  -- what the client called itself
    redirect_uris TEXT NOT NULL,  -- JSON array of strings
    created_at TEXT NOT NULL,
    granted_at TEXT  -- when it first got a grant; a client that never does is removed
);
CREATE TABLE oauth_grants (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX oauth_grants_by_account ON oauth_grants (account_id);
CREATE TABLE oauth_codes (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the authorization code
    account_id TEXT NOT NULL,  -- the account that approved
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INTEGER NOT NULL,  -- 1 when the authorization request named it
    code_challenge TEXT NOT NULL,  -- PKCE, S256
    expires_at TEXT NOT NULL,  -- valid while expires_at > now
    created_at TEXT NOT NULL,
    grant_id TEXT  -- set when redeemed; the row stays until it expires, to notice a replay
);
CREATE TABLE oauth_tokens (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    grant_id TEXT NOT NULL,
    -- 'rotated' is a refresh token that was exchanged: presenting it again ends the grant
    kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh', 'rotated')),
    expires_at TEXT NOT NULL,  -- valid while expires_at > now
    created_at TEXT NOT NULL
);
CREATE INDEX oauth_tokens_by_grant ON oauth_tokens (grant_id);
"""


def v3_database(db: Path) -> dict[str, Any]:
    """Version 2's database plus an agent connected to Ada's account (a grant and its live
    tokens) and an approval still waiting to be redeemed, as cairn wrote them at version 3."""
    v2_database(db)
    with connect(db) as con:
        con.executescript(V3_OAUTH_SCHEMA)
        con.execute(
            "INSERT INTO oauth_clients (id, name, redirect_uris, created_at, granted_at)"
            " VALUES ('client_1', 'agent', ?, ?, ?)",
            (json.dumps([REDIRECT]), stamp(2), stamp(3)),
        )
        con.execute(
            "INSERT INTO oauth_grants (id, account_id, client_id, created_at)"
            " VALUES ('grant_aaaaaaaaaaaa', ?, 'client_1', ?)",
            (V2_ADA, stamp(3)),
        )
        con.executemany(
            "INSERT INTO oauth_tokens VALUES (?, 'grant_aaaaaaaaaaaa', ?, ?, ?)",
            [
                (digest("cairn.at.ada"), "access", LIVE, stamp(3)),
                (digest("cairn.rt.ada"), "refresh", LIVE, stamp(3)),
            ],
        )
        con.execute(
            "INSERT INTO oauth_codes VALUES (?, ?, 'client_1', ?, 1, ?, ?, ?, NULL)",
            (digest("code-ada"), V2_ADA, REDIRECT, CHALLENGE, LIVE, stamp(3)),
        )
        con.execute("PRAGMA user_version = 3")
        con.commit()
    assert user_version(db) == 3
    return {"statements": statements(db)}


def v4_database(db: Path) -> dict[str, Any]:
    v3_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[4](con)
        con.execute(
            "INSERT INTO users (id, email, name, created_at)"
            " VALUES ('usr_aaaaaaaaaaaa', 'ada@example.test', 'Ada', ?)",
            (stamp(4),),
        )
        con.execute(
            "INSERT INTO memberships (account_id, user_id, role, created_at)"
            " VALUES (?, 'usr_aaaaaaaaaaaa', 'owner', ?)",
            (V2_ADA, stamp(4)),
        )
        con.execute("UPDATE oauth_grants SET user_id = 'usr_aaaaaaaaaaaa'")
        con.execute("PRAGMA user_version = 4")
        con.commit()
    assert user_version(db) == 4
    return {"statements": statements(db)}


def v5_database(db: Path) -> dict[str, Any]:
    v4_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[5](con)
        con.execute(
            "INSERT INTO project_sets (id, account_id, name, project_ids, created_at)"
            " VALUES ('set_aaaaaaaaaaaa', ?, 'work', '[\"br_aaaaaaaaaaaa\"]', ?)",
            (V2_ADA, stamp(5)),
        )
        con.execute("PRAGMA user_version = 5")
        con.commit()
    assert user_version(db) == 5
    return {"statements": statements(db)}


def v6_database(db: Path) -> dict[str, Any]:
    v5_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[6](con)
        con.execute(
            "INSERT INTO settings (account_id, name, value, updated_at)"
            " VALUES (?, 'guard.policy', 'off', ?)",
            (V2_ADA, stamp(6)),
        )
        con.execute(
            "INSERT INTO sessions (digest, account_id, expires_at, created_at, user_id, label)"
            " VALUES (?, ?, ?, ?, NULL, 'laptop')",
            (hashlib.sha256(b"token-keyed").hexdigest(), V2_ADA, "2999-01-01", stamp(6)),
        )
        for account, action in (
            (V2_ADA, "setting.changed"),
            (DEFAULT_ACCOUNT_ID, "project.created"),  # another workspace's, in between
            (V2_ADA, "project.created"),
        ):
            con.execute(
                "INSERT INTO audit_events (account_id, at, actor_kind, actor, action, project,"
                " detail) VALUES (?, ?, 'key', 'laptop', ?, NULL, '{}')",
                (account, stamp(6), action),
            )
        con.execute("PRAGMA user_version = 6")
        con.commit()
    assert user_version(db) == 6
    return {"statements": statements(db)}


def v7_database(db: Path) -> dict[str, Any]:
    v6_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[7](con)
        (key_id,) = con.execute(
            "SELECT id FROM api_keys WHERE account_id = ? ORDER BY seq", (V2_ADA,)
        ).fetchone()
        (n,) = con.execute(
            "SELECT MAX(n) + 1 FROM audit_events WHERE account_id = ?", (V2_ADA,)
        ).fetchone()
        con.execute(
            "INSERT INTO audit_events (account_id, n, at, actor_kind, actor, actor_id, action,"
            " project, detail)"
            " VALUES (?, ?, ?, 'key', 'laptop', ?, 'project.created', 'core', '{}')",
            (V2_ADA, n, stamp(7), key_id),
        )
        con.execute("PRAGMA user_version = 7")
        con.commit()
    assert user_version(db) == 7
    return {"statements": statements(db)}


def v8_database(db: Path) -> dict[str, Any]:
    v7_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[8](con)
        con.execute("PRAGMA user_version = 8")
        con.commit()
    assert user_version(db) == 8
    return {"statements": statements(db)}


def v9_database(db: Path) -> dict[str, Any]:
    v8_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[9](con)
        con.execute("PRAGMA user_version = 9")
        con.commit()
    assert user_version(db) == 9
    return {"statements": statements(db)}


V10_ACCENTED = "# Größe\n\nDer Spalt misst 5 µm.\n"


def v10_database(db: Path) -> dict[str, Any]:
    v9_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[10](con)
        con.execute(
            "INSERT INTO notes (id, project_id, text, text_sha256, title, created_at)"
            " VALUES ('cap_000000000002', 'br_aaaaaaaaaaaa', ?, ?, 'Größe', ?)",
            (V10_ACCENTED, digest(V10_ACCENTED), "2026-02-03T00:00:00.000000+00:00"),
        )
        con.execute("PRAGMA user_version = 10")
        con.commit()
    assert user_version(db) == 10
    return {"statements": statements(db)}


def v11_database(db: Path) -> dict[str, Any]:
    v10_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[11](con)
        con.execute(
            "INSERT INTO account_quota (account_id, plan) VALUES (?, 'solo')"
            " ON CONFLICT (account_id) DO UPDATE SET plan = 'solo'",
            (V2_ADA,),
        )
        con.execute("PRAGMA user_version = 11")
        con.commit()
    assert user_version(db) == 11
    return {"statements": statements(db)}


def v13_database(db: Path) -> dict[str, Any]:
    v11_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        for version in (12, 13):
            cairn.store.schema._MIGRATIONS[version](con)
        con.execute(
            "INSERT INTO usage (account_id, period, model_tokens_in, model_micros)"
            " VALUES (?, '2026-11', 300, 500)",
            (V2_ADA,),
        )
        con.execute("PRAGMA user_version = 13")
        con.commit()
    assert user_version(db) == 13
    return {"statements": statements(db)}


def v14_database(db: Path) -> dict[str, Any]:
    v13_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[14](con)
        con.execute("PRAGMA user_version = 14")
        con.commit()
    assert user_version(db) == 14
    return {"statements": statements(db)}


def v15_database(db: Path) -> dict[str, Any]:
    v14_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._FORGOTTEN](con)
        con.execute(f"PRAGMA user_version = {cairn.store.schema._FORGOTTEN}")
        con.commit()
    assert user_version(db) == 15
    return {"statements": statements(db)}


def v16_database(db: Path) -> dict[str, Any]:
    """A version 16 database whose default workspace has a key, a person's session, a
    session opened with the key, one opened with a key revoked before, and one recorded
    before sessions kept the key's id."""
    v15_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        version = cairn.store.schema._DISK_BOUNDS
        cairn.store.schema._MIGRATIONS[version](con)
        con.execute(
            "INSERT INTO api_keys (id, digest, account_id, label, created_at, projects, access,"
            " user_id) VALUES ('key_live', 'd-live', ?, 'laptop', '2026-11-01', NULL, 'write',"
            " NULL)",
            (DEFAULT_ACCOUNT_ID,),
        )
        later = "9999-01-01T00:00:00.000000+00:00"
        for digest, label, key_id in (
            ("s-person", None, None),
            ("s-live", "laptop", "key_live"),
            ("s-revoked", "phone", "key_gone"),
            ("s-old", "laptop", None),
        ):
            con.execute(
                "INSERT INTO sessions (digest, account_id, expires_at, created_at, user_id,"
                " label, key_id) VALUES (?, ?, ?, '2026-11-01', NULL, ?, ?)",
                (digest, DEFAULT_ACCOUNT_ID, later, label, key_id),
            )
        con.execute(f"PRAGMA user_version = {version}")
        con.commit()
    assert user_version(db) == 16
    return {"statements": statements(db)}


LONG = "h" * 390

ACCENTED = "é" * 100  # 200 UTF-8 bytes; 600 as \u escapes


def v17_database(db: Path) -> dict[str, Any]:
    v16_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        version = cairn.store.schema._KEY_SESSIONS
        cairn.store.schema._MIGRATIONS[version](con)
        con.execute(f"PRAGMA user_version = {version}")
        ((project,),) = con.execute(
            "SELECT id FROM projects WHERE account_id = ? ORDER BY id LIMIT 1", (V2_ADA,)
        ).fetchall()
        text = f"# {LONG}\n\n- one\n\n## {ACCENTED}\n\n- two\n"
        con.execute(
            "INSERT INTO notes (id, project_id, text, text_sha256, title, created_at)"
            " VALUES ('cap_0000000000aa', ?, ?, ?, 'T', '2026-11-01T00:00:00+00:00')",
            (project, text, hashlib.sha256(text.encode()).hexdigest()),
        )
        paths = [
            ("heading", json.dumps([LONG], ensure_ascii=False)),
            ("bullet", json.dumps([LONG], ensure_ascii=False)),
            ("heading", json.dumps([LONG, ACCENTED])),  # escaped, as written before
            ("bullet", json.dumps([LONG, ACCENTED])),
        ]
        for n, (kind, path) in enumerate(paths):
            con.execute(
                "INSERT INTO spans VALUES (?, 'cap_0000000000aa', ?, ?, ?, ?, ?, 'x')",
                (f"sp_00000000000{n}", n, kind, path, n + 1, n + 1),
            )
        con.commit()
    assert user_version(db) == 17
    return {"statements": statements(db)}


def v18_database(db: Path) -> dict[str, Any]:
    v17_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._HEADING_CHARGE](con)
        when, later = "2026-11-10T12:00:00+00:00", "2026-12-10T12:00:00+00:00"
        con.execute(
            "INSERT INTO oauth_clients (id, name, redirect_uris, created_at, granted_at)"
            " VALUES ('client_18', 'old-agent', ?, ?, ?)",
            (json.dumps([REDIRECT]), when, when),
        )
        con.execute(
            "INSERT INTO oauth_grants (id, account_id, client_id, created_at, projects, access)"
            " VALUES ('grant_000000000018', ?, 'client_18', ?, NULL, 'write')",
            (V2_ADA, when),
        )
        for digest_, grant_id in (("c" * 64, "grant_000000000018"), ("d" * 64, None)):
            con.execute(
                "INSERT INTO oauth_codes (digest, account_id, client_id, redirect_uri,"
                " redirect_uri_explicit, code_challenge, expires_at, created_at, grant_id,"
                " access) VALUES (?, ?, 'client_18', ?, 1, ?, ?, ?, ?, 'write')",
                (digest_, V2_ADA, REDIRECT, CHALLENGE, later, when, grant_id),
            )
        con.executemany(
            "INSERT INTO oauth_tokens (digest, grant_id, kind, expires_at, created_at)"
            " VALUES (?, 'grant_000000000018', ?, ?, ?)",
            [("a" * 64, "access", later, when), ("b" * 64, "refresh", later, when)],
        )
        con.execute(f"PRAGMA user_version = {cairn.store.schema._HEADING_CHARGE}")
        con.commit()
    assert user_version(db) == 18
    return {"statements": statements(db)}


V19_DEE, V19_ELI = "usr_d19d19d19d19", "usr_e19e19e19e19"

V19_ATLAS = "br_a19a19a19a19"


def v19_database(db: Path) -> dict[str, Any]:
    v18_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._GRANT_AUDIENCE](con)
        when = "2026-11-12T09:00:00+00:00"
        for user_id, name in ((V19_DEE, "Dee"), (V19_ELI, "Eli")):
            con.execute(
                "INSERT INTO users (id, email, name, created_at) VALUES (?, ?, ?, ?)",
                (user_id, f"{name.lower()}@example.test", name, when),
            )
            con.execute(
                "INSERT INTO memberships (account_id, user_id, role, created_at)"
                " VALUES (?, ?, 'member', ?)",
                (V2_ADA, user_id, when),
            )
        con.execute(
            "INSERT INTO projects (id, account_id, name, description, created_at, kind, maker_id,"
            " everyone) VALUES (?, ?, 'atlas', '', ?, 'general', NULL, 'none')",
            (V19_ATLAS, V2_ADA, when),
        )
        con.execute("UPDATE projects SET everyone = 'none' WHERE id = 'br_aaaaaaaaaaaa'")
        con.executemany(
            "INSERT INTO project_people (project_id, user_id, access) VALUES (?, ?, ?)",
            [("br_aaaaaaaaaaaa", V19_DEE, "read"), ("br_aaaaaaaaaaaa", V19_ELI, "write")],
        )
        con.execute(f"PRAGMA user_version = {cairn.store.schema._GRANT_AUDIENCE}")
        con.commit()
    assert user_version(db) == 19
    return {"statements": statements(db)}


V20_ADDED, V20_LATER = "2026-11-12T10:00:00.000000+00:00", "2026-11-12T11:30:00.000000+00:00"


def v20_database(db: Path) -> dict[str, Any]:
    v19_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._MANAGE_LEVEL](con)
        config = json.dumps({"projects": ["core"], "token_env": "V20_TOKEN"})
        con.executemany(
            "INSERT INTO sources (account_id, name, kind, config, cursor, created_at,"
            " last_run_at, last_report, last_error, claim, claimed_at)"
            " VALUES (?, ?, 'feed', ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (V2_ADA, "ran", config, "cursor-20", V20_ADDED, V20_LATER,
                 json.dumps({"saved": 1}), None, "token-20", V20_LATER),
                (V2_ADA, "Fresh", config, None, V20_LATER, None, None, None, None, None),
            ],
        )  # fmt: skip
        con.execute(f"PRAGMA user_version = {cairn.store.schema._MANAGE_LEVEL}")
        con.commit()
    assert user_version(db) == 20
    return {"statements": statements(db)}


V21_SESSION = hashlib.sha256(b"token-v21").hexdigest()

_WITH_CHAIN = re.compile(r",'[0-9a-f]{64}'\);$")  # an audit event's chain, its last column


def before_step_31(lines: list[str]) -> list[str]:
    """Statements of a database migrated past step 31, as step 30 left them: step 31 rebuilds
    audit_events with each event's chain (its last column), adds oldest_build with its row,
    and drops the tables billing kept (put back here as step 30 left them, empty: no database
    here holds rows in them by then; the tests of steps 11 to 13 run those steps alone).
    Anything else step 31 changed stays, and fails the comparison."""
    schema = cairn.store.schema
    out = []
    for line in lines:
        if line.startswith('INSERT INTO "audit_events"'):
            assert _WITH_CHAIN.search(line), line
            line = _WITH_CHAIN.sub(");", line)
        elif line == schema._V31_AUDIT_EVENTS + ";":
            line = schema._V7_AUDIT_EVENTS + ";"
        elif line == schema._V31_OLDEST_BUILD + ";" or line.startswith(
            'INSERT INTO "oldest_build"'
        ):
            continue
        out.append(line)
    retired = (
        schema._V13_SUBSCRIPTIONS,
        schema._V12_BILLING_MESSAGES,
        schema._V12_BILLING_MESSAGES_BY_ACCOUNT,
        schema._V27_CHECKOUTS,
    )
    return out + [statement + ";" for statement in retired if statement + ";" not in out]


def before_step_30(lines: list[str]) -> list[str]:
    """Statements of a database migrated past step 30, as step 29 left them: step 30 gives
    each session its last use (its last column), NULL on every session that existed. Every
    database here that is past step 30 is past step 31 too, whose change goes first."""
    out = []
    for line in before_step_31(lines):
        if line.startswith('INSERT INTO "sessions"'):
            assert line.endswith(",NULL);"), line
            line = line.removesuffix(",NULL);") + ");"
        out.append(line)
    return out


def v21_database(db: Path) -> dict[str, Any]:
    v20_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._SOURCE_SWITCH](con)
        con.execute(
            "INSERT INTO sessions (digest, account_id, expires_at, created_at, user_id)"
            " VALUES (?, ?, ?, ?, 'usr_aaaaaaaaaaaa')",
            (V21_SESSION, V2_ADA, LIVE, V20_LATER),
        )
        con.execute(f"PRAGMA user_version = {cairn.store.schema._SOURCE_SWITCH}")
        con.commit()
    assert user_version(db) == 21
    return {"statements": statements(db)}


def v22_database(db: Path) -> dict[str, Any]:
    v21_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._VOUCHED_EMAIL](con)
        con.execute(f"PRAGMA user_version = {cairn.store.schema._VOUCHED_EMAIL}")
        con.commit()
    assert user_version(db) == 22
    return {"statements": statements(db)}


def v23_database(db: Path) -> dict[str, Any]:
    """v22_database after step 23, with three more notes in Ada's project: "C++" and "C#",
    both on the page `c` (titled "C#" at its latest version), as a slug alone filed them,
    and one never compiled, whose topic is "Loose ends"."""
    v22_database(db)
    notes = {
        "cap_000000000c01": ("C++", None, "- C++ keeps templates.\n"),
        "cap_000000000c02": ("C#", None, "- C# keeps generics.\n"),
        "cap_000000000c03": ("Loose", "Loose ends", "- Loose ends wait.\n"),
    }
    spans = {note: chunk(note, text) for note, (_, _, text) in notes.items()}
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._SOURCE_ITEMS](con)
        for n, (note, (title, topic, text)) in enumerate(notes.items(), start=4):
            con.execute(
                "INSERT INTO notes (id, project_id, text, text_sha256, title, topic, created_at)"
                " VALUES (?, 'br_aaaaaaaaaaaa', ?, ?, ?, ?, ?)",
                (note, text, digest(text), title, topic, stamp(n)),
            )
            con.executemany(
                "INSERT INTO spans (id, note_id, ordinal, kind, heading_path, start_line,"
                " end_line, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (s.id, note, i, s.kind, json.dumps(s.heading_path), s.start_line,
                     s.end_line, s.text)
                    for i, s in enumerate(spans[note])
                ],
            )  # fmt: skip
        for version, title, cited in [(1, "C++", list(notes)[:1]), (2, "C#", list(notes)[:2])]:
            claims = [
                {"id": f"cl_{s.id}", "text": s.text, "span_ids": [s.id]}
                for note in cited
                for s in spans[note]
            ]
            con.execute(
                "INSERT INTO page_versions VALUES ('br_aaaaaaaaaaaa', 'c', ?, ?, '', ?, '{}', ?)",
                (version, title, json.dumps(claims), stamp(6 + version)),
            )
        con.execute(f"PRAGMA user_version = {cairn.store.schema._SOURCE_ITEMS}")
        con.commit()
    assert user_version(db) == 23
    return {"statements": statements(db)}
