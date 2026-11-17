"""Unit tests for the SQLite store: the inner TDD loop under tests/acceptance/test_w1_store.py."""

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import cairn.store
import cairn.topics
from cairn.spans import chunk
from cairn.store import (
    DEFAULT_ACCOUNT_ID,
    SCHEMA_VERSION,
    Claim,
    FiledElsewhere,
    SchemaTooNew,
    StalePage,
    Store,
)
from cairn.store.grants import REFRESH_TOKEN_TTL, ROTATION_WINDOW, ROTATIONS, TooManyRotations


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


@pytest.fixture
def alpha(store):
    return store.create_project("alpha")


# Projects


@pytest.mark.parametrize("name", ["a", "A.b_c-9", "x" * 64])
def test_project_names_at_the_edges_of_the_pattern_are_accepted(store, name):
    assert store.create_project(name).name == name


@pytest.mark.parametrize("name", ["alpha\n", "ä", ".dot", "_under", "tab\tname"])
def test_project_names_must_match_the_whole_pattern(store, name):
    with pytest.raises(ValueError):
        store.create_project(name)


def test_an_id_shaped_name_cannot_shadow_another_project(store, alpha):
    with pytest.raises(ValueError):
        store.create_project(alpha.id)  # it would make alpha's id resolve to two projects
    assert store.get_project(alpha.id) == alpha
    assert store.get_project(alpha.id.upper()) is None  # ids match exactly; no name looks like one


def test_file_databases_use_write_ahead_logging(store, db):
    con = sqlite3.connect(db)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        con.close()


# Keys and sessions


def test_revoke_key_revokes_every_key_with_that_label_and_no_other(store):
    first, second, other = (store.create_key(label) for label in ("laptop", "laptop", "phone"))
    assert store.revoke_key("laptop") is True
    assert not store.check_key(first) and not store.check_key(second)
    assert store.check_key(other)


def test_keys_and_session_tokens_are_not_interchangeable(store):
    key, token = store.create_key("k"), store.create_session()
    assert not store.check_session(key)
    assert not store.check_key(token)


def test_ending_a_session_ends_only_that_session(store):
    first, second = store.create_session(), store.create_session()
    store.end_session(first)
    store.end_session("never-issued")
    assert not store.check_session(first)
    assert store.check_session(second)


def test_a_session_lasts_exactly_its_ttl(store, monkeypatch):
    start = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(cairn.store.base, "_clock", lambda: start)
    token = store.create_session(ttl_seconds=60)
    monkeypatch.setattr(cairn.store.base, "_clock", lambda: start + timedelta(seconds=59.999999))
    assert store.check_session(token)
    monkeypatch.setattr(cairn.store.base, "_clock", lambda: start + timedelta(seconds=60))
    assert not store.check_session(token)


# Notes and spans


@pytest.mark.parametrize(
    ("text", "given", "title"),
    [
        ("---\ntitle: From front matter\n---\n\n# Heading\n", None, "From front matter"),
        ("---\ndate: 2026-03-14\n---\n\n# Heading\n", None, "Heading"),
        ("---\ntitle: 2.10\n---\n", None, "2.10"),  # front matter values stay text
        ("---\ntitle: [unclosed\n---\n\n# Heading\n", None, "Heading"),  # bad YAML
        ("```\n# a comment\n```\n\n## Real heading\n", None, "Real heading"),
        ("---\ndate: 2026-03-14\n---\n\nFirst line\nsecond\n", None, "First line"),
        ("# Heading\n", "   ", "Heading"),  # a blank title is no title
        ("text\n", "  " + "w" * 79 + " x", "w" * 79),  # cut to 80, then no trailing space
        ("---\ndate: 2026-03-14\n---\n", None, ""),  # nothing outside the front matter
        ("", None, ""),
    ],
)
def test_note_title_derivation(store, alpha, text, given, title):
    note, _ = store.add_note(alpha.id, text, title=given)
    assert note.title == title


def test_note_keeps_its_metadata(store, alpha):
    note, _ = store.add_note(
        alpha.id, "body\n", topic="Topic", label="Label", source_ref="ref:1", via="cli"
    )
    assert (note.topic, note.label, note.source_ref, note.via) == (
        "Topic",
        "Label",
        "ref:1",
        "cli",
    )
    assert store.get_note(note.id) == note


def test_duplicates_match_text_and_source_ref_within_one_project(store, alpha):
    beta = store.create_project("beta")
    first, created = store.add_note(alpha.id, "same\n", topic="first")
    assert created
    assert store.add_note(alpha.id, "same\n", topic="FIRST.") == (first, False)  # one key
    with pytest.raises(FiledElsewhere) as refused:  # N1: another topic is not the same note
        store.add_note(alpha.id, "same\n", topic="second")
    assert refused.value.held == first
    with_ref, created = store.add_note(alpha.id, "same\n", source_ref="ref")
    assert created and with_ref.id != first.id
    assert store.add_note(alpha.id, "same\n", source_ref="ref") == (with_ref, False)
    assert store.add_note(beta.id, "same\n")[1] is True


def test_note_for_an_unknown_project_writes_nothing(store, alpha, db):
    before = dump(db)
    with pytest.raises(KeyError):
        store.add_note("br_000000000000", "text\n")
    assert dump(db) == before


def test_list_notes_is_in_insertion_order_and_scoped_to_its_project(store, alpha, monkeypatch):
    beta = store.create_project("beta")
    times = iter([datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)])
    monkeypatch.setattr(cairn.store.base, "_clock", lambda: next(times))  # the clock steps back
    first, _ = store.add_note(alpha.id, "one\n")
    second, _ = store.add_note(alpha.id, "two\n")
    monkeypatch.undo()
    store.add_note(beta.id, "three\n")
    assert store.list_notes(alpha.id) == [first, second]


def test_spans_are_stored_at_note_time_not_rechunked(store, alpha, monkeypatch):
    text = "# Title\n\nbody\n\n- item\n"
    note, _ = store.add_note(alpha.id, text)
    monkeypatch.setattr(cairn.store.memory, "chunk", lambda *_: [])  # a chunker that has changed
    assert store.note_spans(note.id) == chunk(note.id, text)


def test_unknown_notes_have_no_spans(store):
    assert store.get_note("cap_000000000000") is None
    assert store.note_spans("cap_000000000000") == []


def test_get_spans_takes_any_iterable_and_keeps_request_order(store, alpha):
    note, _ = store.add_note(alpha.id, "one\n\ntwo\n\nthree\n")
    a, _, c = store.note_spans(note.id)
    wanted = iter([c.id, "sp_missing", a.id, c.id, *(f"sp_{i:016x}" for i in range(5000))])
    got = store.get_spans(wanted)
    assert list(got.items()) == [(c.id, c), (a.id, a)]
    assert store.get_spans([]) == {}


# Pages

CLAIMS = (Claim(id="cl_1", text="one", span_ids=("sp_1",)),)


def test_saved_page_reads_back_unchanged(store, alpha):
    metrics = {"claims": 1, "nested": {"ratio": 0.5, "codes": ["a", "b"]}}
    saved = store.save_page(alpha.id, "s", "S", "# S\n\none\n", CLAIMS, metrics=metrics)
    assert saved == store.get_page(alpha.id, "s")
    assert (saved.version, saved.claims, saved.metrics) == (1, CLAIMS, metrics)
    assert store.save_page(alpha.id, "s", "S", "# S\n", ()).metrics == {}


def test_save_page_for_an_unknown_project_writes_nothing(store, alpha, db):
    before = dump(db)
    with pytest.raises(KeyError):
        store.save_page("br_000000000000", "s", "S", "# S\n", CLAIMS)
    assert dump(db) == before


def test_versions_count_per_project_and_slug(store, alpha):
    beta = store.create_project("beta")
    store.save_page(alpha.id, "s", "S", "# S\n", CLAIMS)
    store.save_page(alpha.id, "s", "S", "# S\n", CLAIMS)
    assert store.save_page(beta.id, "s", "S", "# S\n", CLAIMS).version == 1
    assert store.save_page(alpha.id, "other", "O", "# O\n", CLAIMS).version == 1
    assert store.page_versions(alpha.id, "s") == [1, 2]
    assert store.page_versions(beta.id, "s") == [1]


def test_list_pages_has_the_latest_version_of_each_slug_in_slug_order(store, alpha):
    beta = store.create_project("beta")
    store.save_page(alpha.id, "b", "B", "# B\n", CLAIMS)
    store.save_page(alpha.id, "a", "A", "# A\n", CLAIMS)
    store.save_page(alpha.id, "b", "B2", "# B2\n", CLAIMS)
    store.save_page(beta.id, "c", "C", "# C\n", CLAIMS)
    assert [(p.slug, p.version, p.title) for p in store.list_pages(alpha.id)] == [
        ("a", 1, "A"),
        ("b", 2, "B2"),
    ]


def test_unknown_pages_and_versions_are_absent(store, alpha):
    store.save_page(alpha.id, "s", "S", "# S\n", CLAIMS)
    assert store.get_page(alpha.id, "missing") is None
    assert store.get_page(alpha.id, "s", version=2) is None
    assert store.page_versions(alpha.id, "missing") == []
    assert store.list_pages("br_000000000000") == []


def test_a_save_on_a_stale_version_raises_and_writes_nothing(store, alpha, db):
    store.save_page(alpha.id, "s", "S", "# S\n\none\n", CLAIMS)
    before = dump(db)
    newer = (Claim(id="cl_2", text="replacement", span_ids=("sp_2",)),)
    for stale in (0, 2, 7):
        with pytest.raises(StalePage):
            store.save_page(alpha.id, "s", "S", "# S\n", newer, expected_version=stale)
    assert dump(db) == before  # no version and no search row
    assert [h.text for h in store.search("one")] == ["one"]
    assert store.search("replacement") == []


def test_expected_version_zero_means_the_page_does_not_exist_yet(store, alpha):
    assert store.save_page(alpha.id, "s", "S", "# S\n", CLAIMS, expected_version=0).version == 1
    assert store.save_page(alpha.id, "s", "S", "# S\n", CLAIMS, expected_version=1).version == 2


def test_an_unknown_project_is_a_key_error_before_any_version_check(store, alpha):
    with pytest.raises(KeyError):
        store.save_page("br_000000000000", "s", "S", "# S\n", CLAIMS, expected_version=5)


def test_a_stale_page_names_the_versions_and_nothing_else():
    error = StalePage(expected=1, found=2)
    assert (error.expected, error.found) == (1, 2)
    assert "1" in str(error) and "2" in str(error)


# Search


def save(store, project, slug, title, *texts):
    claims = [
        Claim(id=f"cl_{slug}{n}", text=t, span_ids=(f"sp_{slug}{n}",)) for n, t in enumerate(texts)
    ]
    return store.save_page(project.id, slug, title, f"# {title}\n", claims)


def test_search_ranks_claims_that_match_more_words_first(store, alpha):
    save(store, alpha, "s", "S", "quill only", "quill run fast", "lumen exec", "lumen", "other")
    hits = store.search("quill run", project_ids=[alpha.id])
    assert [h.text for h in hits] == ["quill run fast", "quill only"]
    assert hits[0].score > hits[1].score > 0


def test_search_hits_carry_the_claim_and_its_page(store, alpha):
    save(store, alpha, "s", "Some page", "first claim", "second claim")
    (hit,) = store.search("second")
    assert (hit.project_id, hit.project, hit.slug, hit.title) == (
        alpha.id,
        "alpha",
        "s",
        "Some page",
    )
    assert (hit.claim_id, hit.text, hit.span_ids) == ("cl_s1", "second claim", ("sp_s1",))


def test_a_title_match_hits_every_claim_of_the_page(store, alpha):
    save(store, alpha, "z", "Zebra handbook", "stripes", "hooves")
    save(store, alpha, "o", "Other", "nothing")
    assert sorted(h.claim_id for h in store.search("zebra")) == ["cl_z0", "cl_z1"]


def test_search_limit_caps_hits_and_a_non_positive_limit_finds_nothing(store, alpha):
    save(store, alpha, "s", "S", "word one", "word two", "word three")
    assert len(store.search("word", limit=2)) == 2
    assert store.search("word", limit=0) == []
    assert store.search("word", limit=-1) == []


def test_search_in_no_projects_finds_nothing(store, alpha):
    save(store, alpha, "s", "S", "word")
    assert store.search("word", project_ids=[]) == []


@pytest.mark.parametrize("query", ["_", "...", "\u0301", " ".join(f"w{i}" for i in range(5000))])
def test_search_survives_odd_and_huge_queries(store, alpha, query):
    save(store, alpha, "s", "S", "word")
    assert store.search(query) == []


def test_reindexing_a_page_leaves_the_same_slug_in_other_projects(store, alpha):
    beta = store.create_project("beta")
    save(store, alpha, "s", "S", "shared word")
    save(store, beta, "s", "S", "shared word")
    save(store, alpha, "s", "S", "replaced")
    assert [h.project for h in store.search("shared")] == ["beta"]


# Threads


def test_threads_writing_at_once_neither_collide_nor_duplicate(store, alpha):
    n = 16
    start = threading.Barrier(n)

    def save_page(_):
        start.wait()
        return store.save_page(alpha.id, "s", "S", "# S\n", CLAIMS).version

    def add_note(_):
        start.wait()
        return store.add_note(alpha.id, "same text\n")[1]

    with ThreadPoolExecutor(n) as pool:
        versions = sorted(pool.map(save_page, range(n)))
    with ThreadPoolExecutor(n) as pool:
        created = list(pool.map(add_note, range(n)))
    assert versions == list(range(1, n + 1))
    assert created.count(True) == 1


@pytest.mark.parametrize("name", ["br_0123456789ab", "BR_anything", "br_"])
def test_project_names_cannot_look_like_project_ids(name):
    s = Store(":memory:")
    try:
        with pytest.raises(ValueError):
            s.create_project(name)
    finally:
        s.close()


# Accounts: the inner TDD loop under tests/acceptance/test_h1_accounts.py

# The schema cairn v0 shipped (commit b521be8), comments included: SQLite stores them.
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
V0_SEARCH = (
    "SELECT claim_search.project_id, projects.name, slug, title, claim_id, text, span_ids,"
    " -bm25(claim_search) FROM claim_search JOIN projects ON projects.id = claim_search.project_id"
    " WHERE claim_search MATCH ? ORDER BY bm25(claim_search), claim_search.rowid LIMIT 10"
)
V0_TABLES = ("projects", "api_keys", "sessions", "notes", "spans", "page_versions")
LIVE, EXPIRED = "2999-01-01T00:00:00.000000+00:00", "2020-01-01T00:00:00.000000+00:00"
MARKER = "Zyqvorth"  # only ever capitalised in a note: lowercase, it is a search index term


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


def v0_database(db: Path) -> dict[str, Any]:
    """A populated database as cairn v0 wrote it: three projects, three keys made while the
    clock stepped back, a live and two expired sessions, notes in two projects, and a page that
    was saved twice (so its search rows were deleted and written again)."""
    empty_v0_database(db)
    projects = [
        ("br_aaaaaaaaaaaa", "Zeta", "last by name", stamp(1)),
        ("br_bbbbbbbbbbbb", "alpha", "", stamp(2)),
        ("br_cccccccccccc", "Mid", "the middle one", stamp(3)),
    ]
    keys = [("key-one", "laptop", stamp(3)), ("key-two", "laptop", stamp(2))]
    keys.append(("key-three", "phone", stamp(1)))
    sessions = [("token-live", LIVE), ("token-old", EXPIRED), ("token-older", EXPIRED)]
    notes = [
        ("cap_000000000001", "br_aaaaaaaaaaaa", "# Relay notes\n\nThe relay retries twice.\n"),
        ("cap_000000000002", "br_aaaaaaaaaaaa", "# Relay notes\n\nThe relay then gives up.\n"),
        ("cap_000000000003", "br_bbbbbbbbbbbb", "# Relay notes\n\nAnother relay, same slug.\n"),
    ]
    spans = {note_id: chunk(note_id, text) for note_id, _, text in notes}

    def claims(*note_ids: str) -> list[dict]:
        return [
            {"id": "cl_" + s.id.removeprefix("sp_"), "text": s.text, "span_ids": [s.id]}
            for note_id in note_ids
            for s in spans[note_id]
            if s.kind == "paragraph"
        ]

    pages = [  # (project, version, claims)
        ("br_aaaaaaaaaaaa", 1, claims("cap_000000000001")),
        ("br_aaaaaaaaaaaa", 2, claims("cap_000000000001", "cap_000000000002")),
        ("br_bbbbbbbbbbbb", 1, claims("cap_000000000003")),
    ]
    with connect(db) as con:
        con.executemany("INSERT INTO projects VALUES (?, ?, ?, ?)", projects)
        con.executemany(
            "INSERT INTO api_keys VALUES (?, ?, ?)", [(digest(k), label, t) for k, label, t in keys]
        )
        con.executemany(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            [(digest(token), expires_at, stamp(1)) for token, expires_at in sessions],
        )
        for note_id, project_id, text in notes:
            con.execute(
                "INSERT INTO notes (id, project_id, text, text_sha256, title, topic, label,"
                " source_ref, via, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (note_id, project_id, text, digest(text), "Relay notes", None, "a label",
                 f"ref:{note_id}", "mcp", stamp(4)),
            )  # fmt: skip
            con.executemany(
                "INSERT INTO spans (id, note_id, ordinal, kind, heading_path, start_line,"
                " end_line, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (s.id, note_id, n, s.kind, json.dumps(s.heading_path), s.start_line,
                     s.end_line, s.text)
                    for n, s in enumerate(spans[note_id])
                ],
            )  # fmt: skip
        for project_id, version, page_claims in pages:
            con.execute(
                "INSERT INTO page_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, "relay-notes", version, "Relay notes", "# Relay notes\n",
                 json.dumps(page_claims), json.dumps({"claims": len(page_claims)}), stamp(5)),
            )  # fmt: skip
            con.execute(
                "DELETE FROM claim_search WHERE project_id = ? AND slug = ?",
                (project_id, "relay-notes"),
            )
            con.executemany(
                "INSERT INTO claim_search (project_id, slug, claim_id, span_ids, title, text)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (project_id, "relay-notes", c["id"], json.dumps(c["span_ids"]), "Relay notes",
                     c["text"])
                    for c in page_claims
                ],
            )  # fmt: skip
            con.commit()
        con.commit()
    return {
        "projects": projects,
        "keys": keys,
        "notes": notes,
        "spans": spans,
        "pages": pages,
        "counts": {t: rows(db, f"SELECT count(*) FROM {t}")[0][0] for t in V0_TABLES},
        "search": rows(db, V0_SEARCH, '"relay"'),
    }


def checkpoint(db: Path) -> None:
    """Fold the write-ahead log into the database file and truncate it, as an operator would
    from a second connection."""
    (busy, _, _) = rows(db, "PRAGMA wal_checkpoint(TRUNCATE)")[0]
    assert busy == 0


def on_disk(db: Path) -> bytes:
    """The raw bytes of the database file and of its write-ahead log."""
    wal = db.with_name(db.name + "-wal")
    return db.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


def test_the_default_account_is_created_with_the_database(store):
    default = store.get_account(DEFAULT_ACCOUNT_ID)
    assert (default.id, default.name) == ("acct_default", "default") and default.created_at


def test_account_names_are_measured_after_stripping(store):
    assert store.create_account("  " + "x" * 80 + "\n").name == "x" * 80
    with pytest.raises(ValueError):
        store.create_account(" " + "x" * 81 + " ")


def test_accounts_and_keys_list_in_creation_order_even_if_the_clock_steps_back(store, monkeypatch):
    times = iter(datetime(2026, 1, day, tzinfo=UTC) for day in (4, 3, 2, 1))
    monkeypatch.setattr(cairn.store.base, "_clock", lambda: next(times))
    first, second = store.create_account("first"), store.create_account("second")
    store.create_key("one")
    store.create_key("two")
    assert first.created_at > second.created_at
    assert [a.name for a in store.list_accounts()] == ["default", "first", "second"]
    assert [k.label for k in store.list_keys()] == ["one", "two"]


def test_a_view_made_from_a_view_is_bound_to_the_account_asked_for(store):
    ada, brook = store.create_account("Ada"), store.create_account("Brook")
    ada_view = store.for_account(ada.id)
    brook_view = ada_view.for_account(brook.id)
    assert (ada_view.account_id, brook_view.account_id) == (ada.id, brook.id)
    project = brook_view.create_project("core")
    assert brook_view.get_project("core") == project
    assert ada_view.get_project(project.id) is None and ada_view.list_projects() == []
    assert store.list_projects() == []
    brook_view.close()
    ada_view.close()  # neither close reaches the database
    assert store.for_account(brook.id).list_projects() == [project]
    with pytest.raises(KeyError):
        brook_view.for_account("acct_000000000000")


def test_a_view_cannot_be_rebound_to_another_account(store):
    view = store.for_account(store.create_account("Ada").id)
    with pytest.raises(AttributeError):
        view.account_id = DEFAULT_ACCOUNT_ID


def test_closing_the_store_closes_the_database_for_its_views(db):
    s = Store(db)
    view = s.for_account(s.create_account("Ada").id)
    s.close()
    with pytest.raises(sqlite3.ProgrammingError):
        view.list_projects()


@pytest.mark.parametrize(
    "account_id",
    ["", "acct_", "ACCT_DEFAULT", "acct_default ", "acct_0123456789AB", "acct_0123456789abc",
     'acct_default" --', "acct_default; DROP TABLE accounts", "claim_search"],
)  # fmt: skip
def test_ids_the_store_could_not_have_made_are_unknown_accounts(store, db, account_id):
    before = dump(db)
    with pytest.raises(KeyError):
        store.for_account(account_id)
    assert store.get_account(account_id) is None
    assert store.delete_account(account_id) is False
    assert dump(db) == before


def test_for_account_refuses_a_deleted_account(store):
    ada = store.create_account("Ada")
    view = store.for_account(ada.id)
    assert store.delete_account(ada.id) is True
    for holder in (store, view):
        with pytest.raises(KeyError):
            holder.for_account(ada.id)
    assert store.delete_account(ada.id) is False


def test_a_view_of_a_deleted_account_reads_nothing_and_writes_nothing(store, db):
    ada = store.create_account("Ada")
    view = store.for_account(ada.id)
    project = view.create_project("core")
    note, _ = view.add_note(project.id, "# Relay notes\n\nThe relay retries twice.\n")
    save(view, project, "s", "S", "relay word")
    key, token = view.create_key("k"), view.create_session()
    assert store.delete_account(ada.id) is True
    before = dump(db)

    assert view.list_projects() == [] and view.get_project("core") is None
    assert view.get_note(note.id) is None and view.note_spans(note.id) == []
    assert view.get_page(project.id, "s") is None and view.search("relay") == []
    assert view.list_keys() == [] and not view.check_key(key) and not view.check_session(token)
    assert view.revoke_key("k") is False
    for write in (
        lambda: view.create_project("again"),
        lambda: view.create_key("again"),
        lambda: view.create_session(),
        lambda: view.add_note(project.id, "text\n"),
        lambda: view.save_page(project.id, "s", "S", "# S\n", CLAIMS),
    ):
        with pytest.raises(KeyError):
            write()
    assert dump(db) == before  # no orphan row, no index for an account that is gone
    assert store.resolve_key(key) is None and store.resolve_session(token) is None


def test_get_spans_through_a_view_keeps_its_own_spans_and_request_order(store):
    views = [store.for_account(store.create_account(name).id) for name in ("Ada", "Brook")]
    spans = []
    for view in views:
        project = view.create_project("core")
        note, _ = view.add_note(project.id, "one\n\ntwo\n")
        spans.append(view.note_spans(note.id))
    (a1, a2), (b1, b2) = spans
    wanted = [b2.id, a2.id, b1.id, a1.id]
    assert list(views[0].get_spans(wanted).items()) == [(a2.id, a2), (a1.id, a1)]
    assert list(views[1].get_spans(wanted).items()) == [(b2.id, b2), (b1.id, b1)]
    assert store.get_spans(wanted) == {}


# Deleting an account


def fill(view, word: str) -> dict[str, Any]:
    """A project "core" with two notes, a page "s" in two versions, a key "k" and a session."""
    project = view.create_project("core", "same name in every account")
    notes = [
        view.add_note(project.id, f"# Relay notes\n\nThe {word} relay retries {n} times.\n")[0]
        for n in range(2)
    ]
    save(view, project, "s", "Relay notes", f"The {word} relay retries 0 times.")
    save(view, project, "s", "Relay notes", f"The {word} relay retries 1 times.", "shared words")
    return {"project": project, "notes": notes, "key": view.create_key("k"),
            "token": view.create_session()}  # fmt: skip


def state(view, filled: dict[str, Any]) -> dict[str, Any]:
    """Everything a view can read back of what `fill` wrote."""
    project = filled["project"]
    return {
        "projects": view.list_projects(),
        "notes": view.list_notes(project.id),
        "spans": [view.note_spans(note.id) for note in filled["notes"]],
        "pages": [view.get_page(project.id, "s", version) for version in (1, 2, None)],
        "listed": view.list_pages(project.id),
        "hits": view.search("relay shared words"),
        "keys": view.list_keys(),
        "auth": (view.check_key(filled["key"]), view.check_session(filled["token"])),
    }


def test_deleting_an_account_spares_the_same_project_name_and_slug_elsewhere(store, db):
    ada = store.for_account(store.create_account("Ada").id)
    brook = store.for_account(store.create_account("Brook").id)
    filled = {view: fill(view, word) for view, word in ((ada, "ada"), (brook, "brook"))}
    filled[store] = fill(store, "default")
    kept = {view: state(view, filled[view]) for view in (brook, store)}
    assert kept[brook]["hits"] and kept[brook]["auth"] == (True, True)

    assert store.delete_account(ada.account_id) is True

    assert {view: state(view, filled[view]) for view in (brook, store)} == kept
    gone = {
        "accounts WHERE id": ada.account_id,
        "projects WHERE account_id": ada.account_id,
        "api_keys WHERE account_id": ada.account_id,
        "sessions WHERE account_id": ada.account_id,
        "notes WHERE project_id": filled[ada]["project"].id,
        "page_versions WHERE project_id": filled[ada]["project"].id,
    }
    for where, value in gone.items():
        assert rows(db, f"SELECT count(*) FROM {where} = ?", value) == [(0,)]
    assert rows(db, "SELECT count(*) FROM spans") == [(8,)]  # 2 notes x 2 spans x 2 accounts
    assert brook.create_project("second").name == "second"  # the others still take writes


def test_deleting_an_account_drops_its_search_index(store, db):
    ada = store.for_account(store.create_account("Ada").id)
    brook = store.for_account(store.create_account("Brook").id)
    for view in (ada, brook):
        save(view, view.create_project("core"), "s", "S", "relay word")
    assert {index_of(ada.account_id), index_of(brook.account_id)} <= tables(db)
    store.delete_account(ada.account_id)
    left = tables(db)
    assert not {name for name in left if name.startswith(index_of(ada.account_id))}
    assert index_of(brook.account_id) in left and brook.search("relay")


def test_deleting_an_account_that_never_saved_a_page(store):
    ada = store.for_account(store.create_account("Ada").id)
    ada.create_project("core")
    assert store.delete_account(ada.account_id) is True
    assert store.get_account(ada.account_id) is None


# One search index per account


def test_search_in_an_account_with_no_pages_finds_nothing_and_creates_nothing(store, db):
    ada = store.for_account(store.create_account("Ada").id)
    project = ada.create_project("core")
    ada.add_note(project.id, "# Relay notes\n\nThe relay retries twice.\n")
    before = dump(db)
    for view in (store, ada):  # neither account has saved a page yet
        assert view.search("relay") == []
        assert view.search("relay", project_ids=[project.id]) == []
    assert dump(db) == before
    assert not {name for name in tables(db) if name.startswith("claim_search")}


def test_an_account_gets_its_index_with_its_first_page(store, db):
    ada = store.for_account(store.create_account("Ada").id)
    project = ada.create_project("core")
    assert index_of(ada.account_id) not in tables(db)
    save(ada, project, "s", "S", "relay word")
    save(ada, project, "t", "T", "relay again")
    assert index_of(ada.account_id) in tables(db)
    assert index_of(DEFAULT_ACCOUNT_ID) not in tables(db)
    assert [h.slug for h in ada.search("relay")] == ["s", "t"] and store.search("relay") == []
    assert rows(db, f"SELECT count(*) FROM {index_of(ada.account_id)}") == [(2,)]


def test_project_filters_still_narrow_a_search_within_an_account(store):
    ada = store.for_account(store.create_account("Ada").id)
    one, two = ada.create_project("one"), ada.create_project("two")
    save(ada, one, "s", "S", "relay word")
    save(ada, two, "s", "S", "relay word")
    assert [h.project for h in ada.search("relay")] == ["one", "two"]
    assert [h.project for h in ada.search("relay", project_ids=[two.id])] == ["two"]


# Schema versions and migrations


def test_an_empty_v0_database_migrates_to_a_working_store(db):
    empty_v0_database(db)
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION
        assert [a.id for a in s.list_accounts()] == [DEFAULT_ACCOUNT_ID]
        assert s.list_projects() == [] and s.list_keys() == [] and s.search("relay") == []
        project = s.create_project("alpha")
        save(s, project, "s", "S", "relay word")
        assert [h.project for h in s.search("relay")] == ["alpha"]
        assert s.check_key(s.create_key("k")) and s.check_session(s.create_session())
    finally:
        s.close()


def test_a_populated_v0_database_keeps_every_row(db):
    v0 = v0_database(db)
    default = DEFAULT_ACCOUNT_ID
    s = Store(db)
    try:
        for table in ("projects", "api_keys", "sessions"):  # everything v0 held has one owner
            assert rows(db, f"SELECT DISTINCT account_id FROM {table}") == [(default,)]
        assert [(b.id, b.name, b.description, b.created_at) for b in s.list_projects()] == [
            v0["projects"][1],  # alpha, Mid, Zeta: by name, ignoring case
            v0["projects"][2],
            v0["projects"][0],
        ]
        assert [(k.label, k.created_at) for k in s.list_keys()] == [
            (label, created_at) for _, label, created_at in v0["keys"]
        ]  # in v0's insertion order
        for key, label, _ in v0["keys"]:
            assert s.check_key(key) and s.resolve_key(key).label == label
            assert s.resolve_key(key).account_id == default
        assert s.check_session("token-live") and s.resolve_session("token-live")
        assert not s.check_session("token-old") and s.resolve_session("token-older") is None
        for note_id, project_id, text in v0["notes"]:
            note = s.get_note(note_id)
            assert (note.project_id, note.text) == (project_id, text)
            assert (note.title, note.topic) == ("Relay notes", None)
            assert (note.label, note.source_ref) == ("a label", f"ref:{note_id}")
            assert (note.via, note.created_at) == ("mcp", stamp(4))
            assert s.note_spans(note_id) == v0["spans"][note_id]
        assert [c.id for c in s.list_notes("br_aaaaaaaaaaaa")] == [
            "cap_000000000001",
            "cap_000000000002",
        ]
        assert s.page_versions("br_aaaaaaaaaaaa", "relay-notes") == [1, 2]
        for project_id, version, claims in v0["pages"]:
            page = s.get_page(project_id, "relay-notes", version)
            assert [c.model_dump(mode="json") for c in page.claims] == claims
            assert page.metrics == {"claims": len(claims)} and page.updated_at == stamp(5)
        with pytest.raises(ValueError):
            s.create_project("ALPHA")  # names are still unique ignoring case
        assert s.for_account(s.create_account("Ada").id).create_project("alpha")
    finally:
        s.close()
    counts = {t: rows(db, f"SELECT count(*) FROM {t}")[0][0] for t in V0_TABLES}
    assert counts == {**v0["counts"], "projects": v0["counts"]["projects"] + 1}  # plus Ada's


def test_v0s_search_index_becomes_the_default_accounts_index(db):
    v0 = v0_database(db)
    assert len(v0["search"]) == 3  # two claims of Zeta's page (version 2), one of alpha's
    s = Store(db)
    try:
        hits = s.search("relay")
        # Scored in Python now, v0's by SQLite's C: the same up to the last bits.
        expected = [(*hit[:-1], pytest.approx(hit[-1], rel=1e-12)) for hit in v0["search"]]
        assert [
            (h.project_id, h.project, h.slug, h.title, h.claim_id, h.text, json.dumps(h.span_ids),
             h.score)
            for h in hits
        ] == expected  # fmt: skip
        # The migrated index keeps working: version 3 replaces the page's rows.
        save(s, s.get_project("zeta"), "relay-notes", "Relay notes", "a new relay claim")
        assert [h.text for h in s.search("relay", project_ids=["br_aaaaaaaaaaaa"])] == [
            "a new relay claim"
        ]
    finally:
        s.close()
    index = index_of(DEFAULT_ACCOUNT_ID)  # v0's table and its shadows, renamed
    assert {name for name in tables(db) if name.startswith("claim_search")} == {
        index,
        *(f"{index}_{shadow}" for shadow in ("config", "content", "data", "docsize", "idx")),
    }


def test_a_migrated_schema_is_a_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    empty_v0_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert {"accounts", "projects", "api_keys", "sessions", "notes"} <= tables(fresh)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_migration_that_fails_midway_leaves_a_working_v0_database(db, monkeypatch):
    v0 = v0_database(db)
    before = dump(db)
    step = cairn.store.schema._MIGRATIONS[2]
    ran = []

    def fail_after_the_real_work(con):
        step(con)
        ran.append({name for (name,) in con.execute("SELECT name FROM sqlite_master")})
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 2, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert "accounts" in ran[0] and "claim_search" not in ran[0]  # the step had done its work
    assert dump(db) == before and user_version(db) == 0
    assert rows(db, V0_SEARCH, '"relay"') == v0["search"]  # v0's own query still works
    with connect(db) as con:  # and v0 could still write to it
        con.execute("INSERT INTO projects VALUES ('br_dddddddddddd', 'New', '', ?)", (stamp(6),))
        con.commit()

    monkeypatch.undo()
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION
        assert [b.name for b in s.list_projects()] == ["alpha", "Mid", "New", "Zeta"]
    finally:
        s.close()


def test_a_failed_open_closes_its_connection(db, monkeypatch):
    empty_v0_database(db)
    opened = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        opened.append(real_connect(*args, **kwargs))
        return opened[-1]

    def fail(con):
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 2, fail)
    monkeypatch.setattr(cairn.store.sqlite3, "connect", tracking_connect)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    monkeypatch.undo()
    with connect(db) as con:
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    monkeypatch.setattr(cairn.store.sqlite3, "connect", tracking_connect)
    with pytest.raises(SchemaTooNew):
        Store(db)
    assert len(opened) == 2
    for con in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            con.execute("SELECT 1")


def test_a_newer_schema_is_refused_before_a_single_byte_is_written(db):
    with connect(db) as con:  # not even in WAL mode: switching it would rewrite the header
        con.execute("CREATE TABLE from_the_future (x)")
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        con.commit()
    before = db.read_bytes()
    with pytest.raises(SchemaTooNew):
        Store(db)
    assert db.read_bytes() == before
    assert sorted(p.name for p in db.parent.iterdir()) == [db.name]  # no -wal, no -journal


def test_a_database_that_is_not_cairns_is_refused_and_left_alone(db):
    with connect(db) as con:
        con.execute("CREATE TABLE notes (body TEXT)")
        con.execute("INSERT INTO notes VALUES ('not a cairn database')")
        con.commit()
    before = db.read_bytes()
    with pytest.raises(sqlite3.DatabaseError, match="not a cairn database"):
        Store(db)
    assert db.read_bytes() == before and user_version(db) == 0


def test_a_v0_database_that_lost_a_table_is_refused_and_left_alone(db):
    v0_database(db)
    with connect(db) as con:
        con.execute("DROP TABLE spans")
        con.commit()
    before = dump(db)
    with pytest.raises(sqlite3.DatabaseError, match="not a cairn database: no table 'spans'"):
        Store(db)
    assert dump(db) == before and user_version(db) == 0


def test_a_database_with_v0s_table_names_but_other_columns_is_refused_and_left_alone(db):
    """The right table names are not enough: rebuilding a table copies only the columns v0
    had, so anything else in it would be dropped without a word."""
    v0_database(db)
    with connect(db) as con:
        con.execute("ALTER TABLE projects ADD COLUMN private TEXT")
        con.execute("UPDATE projects SET private = 'would be lost'")
        con.commit()
    before = dump(db)
    with pytest.raises(sqlite3.DatabaseError, match="not a cairn database: table 'projects'"):
        Store(db)
    assert dump(db) == before and user_version(db) == 0
    assert "would be lost" in dump(db)


def test_a_database_whose_search_table_has_another_shape_is_refused_and_left_alone(db):
    empty_v0_database(db)
    with connect(db) as con:
        con.execute("DROP TABLE claim_search")
        con.execute("CREATE VIRTUAL TABLE claim_search USING fts5 (text)")
        con.commit()
    before = dump(db)
    with pytest.raises(sqlite3.DatabaseError, match="not a cairn database: table 'claim_search'"):
        Store(db)
    assert dump(db) == before and user_version(db) == 0


@pytest.mark.parametrize(
    "account_id",
    ["", "default", "acct_", "ACCT_DEFAULT", "acct_default ", "acct_default\n",
     "acct_0123456789AB", "acct_0123456789a", "acct_0123456789abc", "acct_x; DROP TABLE projects",
     'acct_default" USING fts5 (x); --'],
)  # fmt: skip
def test_only_generated_account_ids_become_index_names(account_id):
    with pytest.raises(ValueError):
        cairn.store.base._index_name(account_id)


def test_index_names_are_built_from_generated_account_ids(store):
    assert cairn.store.base._index_name("acct_default") == "claim_search_acct_default"
    account = store.create_account("Ada")
    assert cairn.store.ACCOUNT_ID.fullmatch(account.id)
    assert cairn.store.base._index_name(account.id) == f"claim_search_{account.id}"


def test_an_account_row_the_store_did_not_write_never_becomes_sql_text(store, db):
    evil = "acct_default; DROP TABLE projects"
    store.create_project("alpha")
    with connect(db) as con:  # a hand edit: the store itself only writes ids it generated
        con.execute(
            "INSERT INTO accounts (id, name, created_at) VALUES (?, 'evil', ?)", (evil, "t")
        )
        con.commit()
    before = dump(db)
    for use in (store.for_account, store.delete_account):
        with pytest.raises(ValueError, match="not an account id"):
            use(evil)
    assert dump(db) == before and store.get_project("alpha") is not None


def test_a_row_misfiled_in_another_accounts_index_is_never_returned(store, db):
    """The index is per account already; the join on the account is the second lock."""
    ada = store.for_account(store.create_account("Ada").id)
    brook = store.for_account(store.create_account("Brook").id)
    ada_project, brook_project = ada.create_project("core"), brook.create_project("core")
    save(ada, ada_project, "s", "S", "relay word")
    save(brook, brook_project, "s", "S", "relay word")
    with connect(db) as con:  # a bug or a hand edit files Brook's claim in Ada's index
        con.execute(
            f"INSERT INTO {index_of(ada.account_id)} (project_id, slug, claim_id, span_ids, title,"
            " text) VALUES (?, 's', 'cl_x', '[]', 'S', 'relay leak')",
            (brook_project.id,),
        )
        con.commit()
    assert [h.text for h in ada.search("relay")] == ["relay word"]
    assert [h.text for h in ada.search("relay", project_ids=[brook_project.id])] == []


def test_a_database_migrated_by_another_process_meanwhile_is_not_migrated_twice(db, monkeypatch):
    """Two processes open the same v0 file: the second reads version 0, then waits for the
    write lock while the first migrates. Simulated by migrating just before the lock."""
    v0_database(db)
    real_connect = sqlite3.connect

    class Overtaken(sqlite3.Connection):
        overtaken = False

        def execute(self, sql, *args):
            if sql.startswith("BEGIN") and not self.overtaken:
                type(self).overtaken = True
                monkeypatch.undo()
                Store(db).close()  # the other process, start to finish
            return super().execute(sql, *args)

    monkeypatch.setattr(
        cairn.store.sqlite3, "connect", lambda *a, **kw: real_connect(*a, factory=Overtaken, **kw)
    )
    s = Store(db)
    try:
        assert Overtaken.overtaken and user_version(db) == SCHEMA_VERSION
        assert [a.id for a in s.list_accounts()] == [DEFAULT_ACCOUNT_ID]
        assert [b.name for b in s.list_projects()] == ["alpha", "Mid", "Zeta"]
    finally:
        s.close()


# Erasure: what is left in the file itself. These two tests show the mechanism (freed rows and
# pages zeroed, the index dropped) leaving nothing behind in one small database. They are not
# proof of erasure: in larger databases, stale copies of deleted rows were found in live pages
# after the same steps, and only VACUUM removed them (see the contract in cairn/store.py).


def test_in_a_small_database_a_deleted_accounts_bytes_are_zeroed(store, db):
    ada = store.for_account(store.create_account("Ada").id)
    brook = store.for_account(store.create_account("Brook").id)
    secrets_of = {}
    for view, word in ((ada, MARKER), (brook, "Kestrel")):
        project = view.create_project("core")
        long_note = "# Long note\n\n" + "filler words " * 1000 + f"then {word} at the end.\n"
        for n, text in enumerate([f"# Relay notes\n\nThe {word} relay retries.\n", long_note] * 2):
            view.add_note(project.id, text, source_ref=f"ref:{n}")
            save(view, project, f"s{n % 3}", "Relay notes", f"The {word} relay, take {n}.", text)
        secrets_of[view] = digest(view.create_key("k")).encode()
    checkpoint(db)
    before = on_disk(db)
    assert MARKER.encode() in before  # the notes as written: notes, spans, pages
    assert MARKER.lower().encode() in before  # lowercase: only ever a search index term
    assert secrets_of[ada] in before

    assert store.delete_account(ada.account_id) is True
    checkpoint(db)

    after = on_disk(db)
    assert MARKER.lower().encode() not in after.lower()
    assert secrets_of[ada] not in after and ada.account_id.encode() not in after
    assert b"Kestrel" in after and b"kestrel" in after and secrets_of[brook] in after
    assert brook.search("kestrel")


def test_in_a_small_database_a_replaced_claims_terms_leave_the_index(store, alpha, db):
    save(store, alpha, "s", "S", f"The {MARKER} relay retries twice.")
    checkpoint(db)
    assert MARKER.lower().encode() in on_disk(db)
    save(store, alpha, "s", "S", "The relay retries twice.")
    checkpoint(db)
    after = on_disk(db)
    assert MARKER.lower().encode() not in after  # the index no longer knows the word
    assert MARKER.encode() in after  # version 1 of the page is still readable
    assert MARKER in store.get_page(alpha.id, "s", version=1).claims[0].text


def test_indexes_delete_securely_when_this_sqlite_can(db):
    assert cairn.store.schema._FTS5_SECURE_DELETE is (sqlite3.sqlite_version_info >= (3, 42, 0))
    v0_database(db)
    s = Store(db)
    try:
        ada = s.for_account(s.create_account("Ada").id)
        save(ada, ada.create_project("core"), "s", "S", "relay word")
    finally:
        s.close()
    for index in (index_of(DEFAULT_ACCOUNT_ID), index_of(ada.account_id)):
        setting = rows(db, f"SELECT v FROM {index}_config WHERE k = 'secure-delete'")
        assert setting == ([(1,)] if cairn.store.schema._FTS5_SECURE_DELETE else [])


def test_an_sqlite_without_fts5_secure_delete_still_indexes_and_migrates(db, monkeypatch):
    monkeypatch.setattr(cairn.store.schema, "_FTS5_SECURE_DELETE", False)
    v0_database(db)
    s = Store(db)
    try:
        ada = s.for_account(s.create_account("Ada").id)
        save(ada, ada.create_project("core"), "s", "S", "relay word")
        assert ada.search("relay") and len(s.search("relay")) == 3
    finally:
        s.close()
    for index in (index_of(DEFAULT_ACCOUNT_ID), index_of(ada.account_id)):
        assert rows(db, f"SELECT v FROM {index}_config WHERE k = 'secure-delete'") == []


# OAuth clients, codes, grants and tokens: the inner TDD loop under
# tests/acceptance/test_h3_oauth.py. Lifetimes, cleanup, reuse detection and the schema 3
# migration, with the clock under the test's control.

REDIRECT = "http://127.0.0.1:33418/callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # RFC 7636, appendix B
T0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
OAUTH_TABLES = ("oauth_clients", "oauth_grants", "oauth_codes", "oauth_tokens")


def at(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(cairn.store.base, "_clock", lambda: moment)


def count(db: Path, *tables_: str) -> dict[str, int]:
    return {t: rows(db, f"SELECT count(*) FROM {t}")[0][0] for t in tables_}


@pytest.fixture
def ada(store):
    return store.for_account(store.create_account("Ada").id)


@pytest.fixture
def agent(store):
    return store.register_client("test-agent", [REDIRECT])


def granted(view, client):
    """What an approval followed by a code exchange leaves: a grant and its first token pair."""
    pair = view.redeem_code(view.create_code(client.id, REDIRECT, CHALLENGE), client.id)
    assert pair is not None
    return pair


def test_a_registered_client_is_read_back_by_its_id(store):
    uris = [REDIRECT, "https://app.example/callback"]
    client = store.register_client("  test-agent\n", uris)
    assert re.fullmatch(r"client_[0-9a-f]{32}", client.id)
    assert (client.name, client.redirect_uris) == ("test-agent", tuple(uris))
    assert store.get_client(client.id) == client
    assert store.get_client("client_" + "0" * 32) is None
    assert store.register_client("test-agent", uris).id != client.id  # a name is not an identity


@pytest.mark.parametrize(
    ("name", "uris"),
    [
        ("", [REDIRECT]),
        (" \n", [REDIRECT]),
        ("x" * 81, [REDIRECT]),
        ("agent", []),
        ("agent", [REDIRECT] * 11),
        ("agent", ["https://a.example/" + "x" * 238]),  # 256 characters
        ("agent", [""]),
    ],
)
def test_register_client_bounds_what_it_stores(store, db, name, uris):
    with pytest.raises(ValueError):
        store.register_client(name, uris)
    assert count(db, "oauth_clients") == {"oauth_clients": 0}


def test_register_client_accepts_values_at_its_limits(store):
    longest = "https://a.example/" + "x" * 237  # 255 characters
    client = store.register_client("x" * 80, [longest] * 10)
    assert len(client.name) == 80 and client.redirect_uris == (longest,) * 10


def test_a_code_is_redeemed_for_a_token_pair_in_the_account_that_approved(store, ada, agent):
    code = ada.create_code(agent.id, REDIRECT, CHALLENGE, redirect_uri_explicit=False)
    found = store.find_code(code)
    assert (found.client_id, found.redirect_uri) == (agent.id, REDIRECT)
    assert (found.code_challenge, found.redirect_uri_explicit) == (CHALLENGE, False)
    assert store.find_code("never-issued") is None and ada.list_grants() == []

    pair = store.redeem_code(code, agent.id)
    assert pair.access_token.startswith(cairn.store.ACCESS_TOKEN_PREFIX)
    assert pair.refresh_token.startswith(cairn.store.REFRESH_TOKEN_PREFIX)
    assert pair.expires_in == 3600
    assert store.resolve_token(pair.access_token) == cairn.store.Principal(
        account_id=ada.account_id, kind="token", label="test-agent"
    )
    assert store.refresh_token_client(pair.refresh_token) == agent.id
    assert [(g.client_id, g.client_name) for g in ada.list_grants()] == [(agent.id, "test-agent")]
    assert store.list_grants() == []  # the default account approved nothing


def test_tokens_keys_and_sessions_are_not_interchangeable(store, ada, agent):
    pair, key, session = granted(ada, agent), ada.create_key("k"), ada.create_session()
    for other in (pair.refresh_token, key, session, "", "never-issued"):
        assert store.resolve_token(other) is None
    for token in pair.access_token, pair.refresh_token:
        assert store.resolve_key(token) is None and store.resolve_session(token) is None
    assert store.refresh_token_client(pair.access_token) is None
    # A key can never be taken for a token: both prefixes hold a character keys never contain.
    key_alphabet = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    for prefix in (cairn.store.ACCESS_TOKEN_PREFIX, cairn.store.REFRESH_TOKEN_PREFIX):
        assert set(prefix) - key_alphabet
    assert cairn.store.ACCESS_TOKEN_PREFIX != cairn.store.REFRESH_TOKEN_PREFIX


def test_a_code_is_bound_to_its_client(store, ada, agent):
    other = store.register_client("another-agent", [REDIRECT])
    code = ada.create_code(agent.id, REDIRECT, CHALLENGE)
    assert store.redeem_code(code, other.id) is None
    assert store.redeem_code(code, agent.id) is not None  # the attempt did not burn it


def test_replaying_a_redeemed_code_ends_the_grant_it_made(store, ada, agent):
    code = ada.create_code(agent.id, REDIRECT, CHALLENGE)
    pair = store.redeem_code(code, agent.id)
    bystander = granted(ada, agent)
    assert store.find_code(code) is not None  # kept until it expires, so a replay is noticed
    assert store.redeem_code(code, agent.id) is None
    assert store.resolve_token(pair.access_token) is None
    assert store.rotate_refresh_token(pair.refresh_token, agent.id) is None
    assert store.resolve_token(bystander.access_token) is not None
    assert len(ada.list_grants()) == 1


def test_a_code_redeemed_from_many_threads_yields_one_pair(store, ada, agent):
    code = ada.create_code(agent.id, REDIRECT, CHALLENGE)
    with ThreadPoolExecutor(max_workers=8) as pool:
        pairs = list(pool.map(lambda _: store.redeem_code(code, agent.id), range(8)))
    assert sum(pair is not None for pair in pairs) == 1


def test_create_code_needs_a_known_client_and_a_living_account(store, ada, agent, db):
    with pytest.raises(KeyError):
        ada.create_code("client_" + "0" * 32, REDIRECT, CHALLENGE)
    gone = store.for_account(store.create_account("Gone").id)
    store.delete_account(gone.account_id)
    with pytest.raises(KeyError):
        gone.create_code(agent.id, REDIRECT, CHALLENGE)
    assert count(db, "oauth_codes") == {"oauth_codes": 0}


def test_a_code_lasts_two_minutes(store, ada, agent, monkeypatch):
    at(monkeypatch, T0)
    code, late = (ada.create_code(agent.id, REDIRECT, CHALLENGE) for _ in range(2))
    assert store.find_code(code).expires_at == T0 + timedelta(seconds=120)
    at(monkeypatch, T0 + timedelta(seconds=119.999999))
    assert store.find_code(code) is not None and store.redeem_code(code, agent.id) is not None
    at(monkeypatch, T0 + timedelta(seconds=120))
    assert store.find_code(late) is None and store.redeem_code(late, agent.id) is None
    assert len(ada.list_grants()) == 1


def test_an_access_token_lasts_one_hour(store, ada, agent, monkeypatch):
    at(monkeypatch, T0)
    pair = granted(ada, agent)
    at(monkeypatch, T0 + timedelta(seconds=3599.999999))
    assert store.resolve_token(pair.access_token) is not None
    at(monkeypatch, T0 + timedelta(hours=1))
    assert store.resolve_token(pair.access_token) is None
    assert store.rotate_refresh_token(pair.refresh_token, agent.id) is not None  # still connected


def test_a_refresh_token_lasts_30_days_from_its_last_rotation(store, ada, agent, monkeypatch):
    at(monkeypatch, T0)
    first = granted(ada, agent)
    at(monkeypatch, T0 + timedelta(days=29))
    second = store.rotate_refresh_token(first.refresh_token, agent.id)
    at(monkeypatch, T0 + timedelta(days=59) - timedelta(microseconds=1))
    third = store.rotate_refresh_token(second.refresh_token, agent.id)
    assert third is not None and len(ada.list_grants()) == 1
    at(monkeypatch, T0 + timedelta(days=89) - timedelta(microseconds=1))
    assert ada.list_grants() == []  # 30 days after the last rotation the grant has ended
    assert store.resolve_token(third.access_token) is None
    assert store.rotate_refresh_token(third.refresh_token, agent.id) is None


def test_rotation_retires_the_old_pair(store, ada, agent):
    first = granted(ada, agent)
    second = store.rotate_refresh_token(first.refresh_token, agent.id)
    assert second.expires_in == 3600
    assert {first.access_token, first.refresh_token}.isdisjoint(
        {second.access_token, second.refresh_token}
    )
    assert store.resolve_token(first.access_token) is None
    assert store.resolve_token(second.access_token).account_id == ada.account_id
    assert len(ada.list_grants()) == 1  # the same grant, not a new one


def test_any_rotated_token_ends_the_grant_for_30_days_after_its_rotation(
    store, ada, agent, db, monkeypatch
):
    at(monkeypatch, T0)
    first = granted(ada, agent)
    pair = store.rotate_refresh_token(first.refresh_token, agent.id)
    for hours in (1, 2, 3):  # the client refreshes each hour; `first` is three tokens back
        at(monkeypatch, T0 + timedelta(hours=hours))
        pair = store.rotate_refresh_token(pair.refresh_token, agent.id)
    at(monkeypatch, T0 + timedelta(days=30) - timedelta(microseconds=1))
    bystander = granted(ada, agent)
    assert store.refresh_token_client(first.refresh_token) == agent.id  # its digest is kept
    assert store.rotate_refresh_token(first.refresh_token, agent.id) is None
    assert store.rotate_refresh_token(pair.refresh_token, agent.id) is None  # the grant ended
    assert store.refresh_token_client(first.refresh_token) is None  # gone with the grant
    assert rows(db, "SELECT count(*) FROM oauth_tokens WHERE kind = 'rotated'") == [(0,)]
    assert len(ada.list_grants()) == 1
    assert store.rotate_refresh_token(bystander.refresh_token, agent.id) is not None


def test_a_thief_who_rotates_twice_is_caught_when_the_client_refreshes(store, ada, agent):
    """The reviewer's sequence: a thief copies the current refresh token and rotates twice
    before the client refreshes. The client's token is then two rotations back."""
    client = granted(ada, agent)
    stolen = store.rotate_refresh_token(client.refresh_token, agent.id)
    stolen = store.rotate_refresh_token(stolen.refresh_token, agent.id)
    assert store.rotate_refresh_token(client.refresh_token, agent.id) is None
    assert ada.list_grants() == []
    assert store.resolve_token(stolen.access_token) is None
    assert store.rotate_refresh_token(stolen.refresh_token, agent.id) is None


def test_a_rotated_token_is_unknown_31_days_after_its_rotation(store, ada, agent, db, monkeypatch):
    at(monkeypatch, T0)
    first = granted(ada, agent)
    second = store.rotate_refresh_token(first.refresh_token, agent.id)  # `first` rotated at T0
    at(monkeypatch, T0 + timedelta(days=29))
    third = store.rotate_refresh_token(second.refresh_token, agent.id)
    at(monkeypatch, T0 + timedelta(days=31))
    store.revoke_token("cairn.rt.never-issued", agent.id)  # removes what expired meanwhile
    before = dump(db)
    assert store.refresh_token_client(first.refresh_token) is None
    assert store.rotate_refresh_token(first.refresh_token, agent.id) is None
    assert store.revoke_token(first.refresh_token, agent.id) is False
    assert dump(db) == before  # nothing written, nothing ended
    assert len(ada.list_grants()) == 1
    # The grant's next rotation removes the row it no longer needs, in the same write.
    assert store.rotate_refresh_token(third.refresh_token, agent.id) is not None
    remembered = "SELECT count(*) FROM oauth_tokens WHERE kind = 'rotated'"
    assert rows(db, remembered) == [(2,)]  # `second` and `third`


def test_the_seventh_rotation_in_an_hour_is_refused_and_writes_nothing(
    store, ada, agent, db, monkeypatch
):
    assert (ROTATIONS, ROTATION_WINDOW) == (6, 3_600)
    at(monkeypatch, T0)
    first = granted(ada, agent)
    second_agent = store.register_client("other-agent", [REDIRECT])
    other = granted(ada, second_agent)
    pair = first
    for minutes in range(ROTATIONS):  # T0 to T0 + 5 minutes
        at(monkeypatch, T0 + timedelta(minutes=minutes))
        pair = store.rotate_refresh_token(pair.refresh_token, agent.id)
    ada.create_code(agent.id, REDIRECT, CHALLENGE)  # pending, and expired by the attempt below
    at(monkeypatch, T0 + timedelta(minutes=59, seconds=59))
    before = dump(db)
    with pytest.raises(TooManyRotations):
        store.rotate_refresh_token(pair.refresh_token, agent.id)
    assert dump(db) == before  # not even what had expired is removed
    # The count is the database's: a store opened anew on it counts the same rotations.
    again = Store(db)
    try:
        with pytest.raises(TooManyRotations):
            again.rotate_refresh_token(pair.refresh_token, agent.id)
    finally:
        again.close()
    assert dump(db) == before
    assert store.rotate_refresh_token(other.refresh_token, second_agent.id)  # its own count
    # An hour after the first of the six, the next is allowed, with the same refresh token.
    at(monkeypatch, T0 + timedelta(hours=1))
    assert store.rotate_refresh_token(pair.refresh_token, agent.id) is not None


def test_a_rotated_token_ends_the_grant_even_past_the_rate(store, ada, agent, monkeypatch):
    at(monkeypatch, T0)
    first = pair = granted(ada, agent)
    for _ in range(ROTATIONS):
        pair = store.rotate_refresh_token(pair.refresh_token, agent.id)
    with pytest.raises(TooManyRotations):
        store.rotate_refresh_token(pair.refresh_token, agent.id)
    assert store.rotate_refresh_token(first.refresh_token, agent.id) is None  # replayed
    assert ada.list_grants() == []
    assert store.rotate_refresh_token("cairn.rt.never-issued", agent.id) is None  # not counted


def test_a_grant_remembers_at_most_4320_rotated_tokens(store, ada, agent, db, monkeypatch):
    """Six rotations in any hour, each remembered for 30 days: 6 x 24 x 30 = 4,320 rows at
    most for one grant. A client asks seven times at the top of every hour, one more than the
    rate allows, until 10,000 rotations went through: about 69 days, so the oldest rows run
    out and are removed as the newest come. Without the rate it would hold 7 x 24 x 30."""
    bound = ROTATIONS * (REFRESH_TOKEN_TTL // ROTATION_WINDOW)
    assert bound == 4_320
    pair = granted(ada, agent)
    remembered = "SELECT count(*) FROM oauth_tokens WHERE kind = 'rotated'"
    rotations = refused = most = 0
    attempts = 0
    while rotations < 10_000:
        hour, minute = divmod(attempts, ROTATIONS + 1)
        at(monkeypatch, T0 + timedelta(hours=hour, minutes=minute))
        attempts += 1
        try:
            pair = store.rotate_refresh_token(pair.refresh_token, agent.id)
        except TooManyRotations:
            refused += 1
            continue
        rotations += 1
        ((held,),) = rows(db, remembered)
        most = max(most, held)
        assert held <= bound, rotations
    assert most == bound and refused == attempts - rotations > 1_600  # reached, never passed
    assert store.resolve_token(pair.access_token) is not None


MCP_URL = "https://cairn.example.net/mcp"
MOVED = "https://memory.example.org/mcp"


def test_a_grant_is_bound_to_the_address_its_code_was_approved_for(store, ada, agent, db):
    code = ada.create_code(agent.id, REDIRECT, CHALLENGE, audience=MCP_URL)
    before = dump(db)
    assert store.redeem_code(code, agent.id, audience=MOVED) is None
    assert dump(db) == before  # refused before anything is written: the code is not burned
    first = store.redeem_code(code, agent.id, audience=MCP_URL)
    assert first is not None
    assert store.identify_token(first.access_token, audience=MOVED) is None
    assert store.identify_token(first.access_token, audience=MCP_URL)[0].account_id == (
        ada.account_id
    )
    assert store.resolve_token(first.access_token) is not None  # a lookup that checks no address

    before = dump(db)
    assert store.rotate_refresh_token(first.refresh_token, agent.id, audience=MOVED) is None
    assert dump(db) == before
    second = store.rotate_refresh_token(first.refresh_token, agent.id, audience=MCP_URL)
    assert second is not None
    # The token just replaced, presented at another address, is unknown there: no grant ends.
    assert store.rotate_refresh_token(first.refresh_token, agent.id, audience=MOVED) is None
    assert store.identify_token(second.access_token, audience=MCP_URL) is not None
    assert rows(db, "SELECT audience FROM oauth_grants") == [(MCP_URL,)]


def test_a_code_made_without_an_address_is_bound_to_none(store, ada, agent):
    """Only a caller of the store makes one (the server always names its address): the grant
    it is redeemed for is accepted at any address."""
    code = ada.create_code(agent.id, REDIRECT, CHALLENGE)
    pair = store.redeem_code(code, agent.id, audience=MOVED)
    assert store.identify_token(pair.access_token, audience=MCP_URL) is not None
    assert store.rotate_refresh_token(pair.refresh_token, agent.id, audience=MCP_URL) is not None


def test_another_client_cannot_rotate_or_end_a_grant(store, ada, agent):
    other = store.register_client("another-agent", [REDIRECT])
    first = granted(ada, agent)
    second = store.rotate_refresh_token(first.refresh_token, agent.id)
    for token in (second.refresh_token, first.refresh_token):  # the live one, a rotated one
        assert store.rotate_refresh_token(token, other.id) is None
    for token in (second.access_token, second.refresh_token, first.refresh_token):
        assert store.revoke_token(token, other.id) is False
    assert store.resolve_token(second.access_token) is not None
    assert store.rotate_refresh_token(second.refresh_token, agent.id) is not None


@pytest.mark.parametrize("which", ["access", "refresh", "rotated"])
def test_revoking_any_token_of_a_grant_ends_the_whole_grant(store, ada, agent, db, which):
    first = granted(ada, agent)
    bystander = granted(ada, agent)
    second = store.rotate_refresh_token(first.refresh_token, agent.id)
    token = {
        "access": second.access_token,
        "refresh": second.refresh_token,
        "rotated": first.refresh_token,
    }[which]
    assert store.revoke_token("never-issued", agent.id) is False
    assert store.revoke_token(token, agent.id) is True
    assert store.revoke_token(token, agent.id) is False
    assert store.resolve_token(second.access_token) is None
    assert store.rotate_refresh_token(second.refresh_token, agent.id) is None
    assert store.resolve_token(bystander.access_token) is not None
    assert count(db, "oauth_grants", "oauth_tokens") == {"oauth_grants": 1, "oauth_tokens": 2}


def test_grants_are_listed_and_revoked_per_account(store, ada, agent, monkeypatch):
    brook = store.for_account(store.create_account("Brook").id)
    second_agent = store.register_client("second-agent", [REDIRECT])
    times = iter(T0 - timedelta(seconds=n) for n in range(40))  # the clock steps back
    monkeypatch.setattr(cairn.store.base, "_clock", lambda: next(times))
    mine, also_mine, theirs = granted(ada, agent), granted(ada, second_agent), granted(brook, agent)
    at(monkeypatch, T0)

    listed = ada.list_grants()
    assert [g.client_name for g in listed] == ["test-agent", "second-agent"]  # oldest first
    assert all(re.fullmatch(r"grant_[0-9a-f]{12}", g.id) and g.created_at for g in listed)
    assert [g.client_name for g in brook.list_grants()] == ["test-agent"]

    assert brook.revoke_grant(listed[0].id) is False  # a foreign id is an unknown id
    assert ada.revoke_grant("grant_000000000000") is False
    assert store.resolve_token(mine.access_token) is not None
    assert ada.revoke_grant(listed[0].id) is True
    assert ada.revoke_grant(listed[0].id) is False
    assert store.resolve_token(mine.access_token) is None
    assert store.rotate_refresh_token(mine.refresh_token, agent.id) is None
    assert [g.id for g in ada.list_grants()] == [listed[1].id]
    for pair in (also_mine, theirs):
        assert store.resolve_token(pair.access_token) is not None


def test_deleting_an_account_removes_its_grants_codes_and_tokens_and_no_one_elses(
    store, ada, agent, db
):
    brook = store.for_account(store.create_account("Brook").id)
    pending = ada.create_code(agent.id, REDIRECT, CHALLENGE)
    first = granted(ada, agent)
    second = store.rotate_refresh_token(first.refresh_token, agent.id)
    theirs, their_code = granted(brook, agent), brook.create_code(agent.id, REDIRECT, CHALLENGE)
    assert count(db, *OAUTH_TABLES) == {
        "oauth_clients": 1,
        "oauth_grants": 2,
        "oauth_codes": 4,  # two pending, two redeemed and kept until they expire
        "oauth_tokens": 5,  # Ada: access, refresh, one rotated; Brook: access, refresh
    }

    assert store.delete_account(ada.account_id) is True

    assert count(db, *OAUTH_TABLES) == {
        "oauth_clients": 1,  # a client belongs to no account
        "oauth_grants": 1,
        "oauth_codes": 2,
        "oauth_tokens": 2,
    }
    text = dump(db)
    assert ada.account_id not in text
    for secret in (pending, first.refresh_token, second.access_token, second.refresh_token):
        assert digest(secret) not in text
    assert store.resolve_token(second.access_token) is None
    assert store.find_code(pending) is None and store.redeem_code(pending, agent.id) is None
    assert store.rotate_refresh_token(second.refresh_token, agent.id) is None
    assert ada.list_grants() == [] and ada.revoke_grant("grant_000000000000") is False
    assert store.resolve_token(theirs.access_token).account_id == brook.account_id
    assert store.redeem_code(their_code, agent.id) is not None


def test_codes_and_tokens_are_stored_as_digests_only(store, ada, agent, db):
    code = ada.create_code(agent.id, REDIRECT, CHALLENGE)
    first = store.redeem_code(code, agent.id)
    second = store.rotate_refresh_token(first.refresh_token, agent.id)
    text = dump(db)
    issued = (first.access_token, first.refresh_token, second.access_token, second.refresh_token)
    for secret in (code, *issued):
        assert secret not in text
        assert secret.rpartition(".")[2] not in text  # nor its random part without the prefix
    for kept in (code, first.refresh_token, second.access_token, second.refresh_token):
        assert digest(kept) in text
    assert digest(first.access_token) not in text  # retired by the rotation
    shown = repr((ada.list_grants(), store.find_code(code), store.get_client(agent.id)))
    assert not any(digest(secret) in shown for secret in (code, *issued))  # nor is a digest shown


def test_oauth_reads_never_write_not_even_past_an_expiry(store, ada, agent, db, monkeypatch):
    at(monkeypatch, T0)
    code = ada.create_code(agent.id, REDIRECT, CHALLENGE)
    pair = granted(ada, agent)
    before = dump(db)
    for moment in (T0, T0 + timedelta(minutes=5), T0 + timedelta(days=31)):
        at(monkeypatch, moment)
        store.get_client(agent.id)
        store.get_client("client_" + "0" * 32)
        store.find_code(code)
        store.find_code("never-issued")
        store.resolve_token(pair.access_token)
        store.resolve_token("never-issued")
        store.refresh_token_client(pair.refresh_token)
        ada.list_grants()
        assert dump(db) == before, moment


def test_expired_rows_are_removed_by_the_next_oauth_write(store, ada, agent, db, monkeypatch):
    at(monkeypatch, T0)
    ada.create_code(agent.id, REDIRECT, CHALLENGE)  # approved, never redeemed
    stale = granted(ada, agent)  # connected once, never refreshed
    kept = granted(ada, agent)
    assert count(db, "oauth_codes", "oauth_grants", "oauth_tokens") == {
        "oauth_codes": 3,
        "oauth_grants": 2,
        "oauth_tokens": 4,
    }

    at(monkeypatch, T0 + timedelta(days=20))
    kept = store.rotate_refresh_token(kept.refresh_token, agent.id)
    assert count(db, "oauth_codes", "oauth_grants", "oauth_tokens") == {
        "oauth_codes": 0,
        "oauth_grants": 2,
        "oauth_tokens": 4,  # stale: its refresh token; kept: one rotated digest and a new pair
    }

    at(monkeypatch, T0 + timedelta(days=30))
    assert len(ada.list_grants()) == 1  # reads already see the stale grant as ended
    store.register_client("another-agent", [REDIRECT])
    assert count(db, "oauth_grants", "oauth_tokens") == {"oauth_grants": 1, "oauth_tokens": 2}
    assert digest(stale.refresh_token) not in dump(db)
    assert store.rotate_refresh_token(kept.refresh_token, agent.id) is not None


def test_old_clients_that_never_got_a_grant_are_removed_by_a_later_registration(
    store, ada, db, monkeypatch
):
    at(monkeypatch, T0)
    abandoned = store.register_client("abandoned", [REDIRECT])
    approved = store.register_client("approved-never-redeemed", [REDIRECT])
    slow = store.register_client("approved-late", [REDIRECT])
    connected = store.register_client("connected-then-revoked", [REDIRECT])
    ada.create_code(approved.id, REDIRECT, CHALLENGE)  # expires unredeemed: still no grant
    granted(ada, connected)
    ada.revoke_grant(ada.list_grants()[0].id)

    at(monkeypatch, T0 + timedelta(days=7) - timedelta(seconds=60))
    recent = store.register_client("recent", [REDIRECT])
    pending = ada.create_code(slow.id, REDIRECT, CHALLENGE)  # approved just now
    for client in (abandoned, approved):
        assert store.get_client(client.id) is not None  # not old enough yet

    at(monkeypatch, T0 + timedelta(days=7))
    assert store.get_client(abandoned.id) is not None  # and no read removes it
    newest = store.register_client("newest", [REDIRECT])
    assert store.get_client(abandoned.id) is None and store.get_client(approved.id) is None
    # Kept: one that had a grant once, one whose code can still be redeemed, the young ones.
    for client in (connected, slow, recent, newest):
        assert store.get_client(client.id) == client
    assert store.redeem_code(pending, slow.id) is not None
    assert count(db, "oauth_clients") == {"oauth_clients": 4}

    at(monkeypatch, T0 + timedelta(days=30))
    store.register_client("much-later", [REDIRECT])
    assert store.get_client(slow.id) == slow  # it got its grant, so it stays


# The schema cairn wrote at version 2 (accounts, before OAuth), as SQLite stored it.
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


# Step _GRANT_AUDIENCE ends every grant, code and token that exists (_add_grant_audience):
# a database opened from an older version keeps every row but those.
ENDED_AT_GRANT_AUDIENCE = ("oauth_grants", "oauth_codes", "oauth_tokens")


def kept_rows(lines: list[str]) -> list[str]:
    """The INSERT statements of a database at an older version that survive every step."""
    return [
        line
        for line in lines
        if line.startswith('INSERT INTO "') and line.split('"')[1] not in ENDED_AT_GRANT_AUDIENCE
    ]


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


def test_a_version_2_database_migrates_keeping_every_row(db, monkeypatch):
    v2 = v2_database(db)
    step_4 = cairn.store.schema._MIGRATIONS[4]
    at_3 = []

    def look_before_step_4(con):  # step 3 alone: later steps change tables of their own
        at_3.append(list(con.iterdump()))
        step_4(con)

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 4, look_before_step_4)
    s = Store(db)
    try:
        assert SCHEMA_VERSION >= 3 and user_version(db) == SCHEMA_VERSION
        after = at_3[0]
        assert [line for line in after if "oauth_" not in line] == v2["statements"]
        added = [line for line in after if "oauth_" in line]
        assert added and all(line.startswith(("CREATE TABLE", "CREATE INDEX")) for line in added)
        assert set(OAUTH_TABLES) <= tables(db)

        ada = s.for_account(V2_ADA)  # what version 2 held still works
        assert s.resolve_key("key-ada").account_id == V2_ADA and s.resolve_session("token-ada")
        assert [b.name for b in ada.list_projects()] == ["core"]
        assert ada.note_spans("cap_000000000001") == v2["spans"]
        assert [h.claim_id for h in ada.search("relay")] == ["cl_1"]

        agent = s.register_client("test-agent", [REDIRECT])  # and what version 3 adds works
        pair = granted(ada, agent)
        assert s.resolve_token(pair.access_token).account_id == V2_ADA
        assert s.delete_account(V2_ADA) is True and s.resolve_token(pair.access_token) is None
    finally:
        s.close()


def test_a_database_migrated_from_version_2_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v2_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert set(OAUTH_TABLES) <= tables(fresh)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_3_leaves_the_version_2_database_as_it_was(db, monkeypatch):
    v2 = v2_database(db)
    step = cairn.store.schema._MIGRATIONS[3]
    ran = []

    def fail_after_the_real_work(con):
        step(con)
        ran.append({name for (name,) in con.execute("SELECT name FROM sqlite_master")})
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 3, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert set(OAUTH_TABLES) <= ran[0]  # the step had done its work
    assert statements(db) == v2["statements"] and user_version(db) == 2

    monkeypatch.undo()
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION and s.resolve_key("key-ada") is not None
    finally:
        s.close()


def test_a_v0_database_migrates_through_every_version(db):
    v0_database(db)
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION and set(OAUTH_TABLES) <= tables(db)
        agent = s.register_client("test-agent", [REDIRECT])
        assert s.resolve_token(granted(s, agent).access_token).account_id == DEFAULT_ACCOUNT_ID
    finally:
        s.close()


# Schema 4: people. A database as cairn wrote it at version 3 is version 2's plus the OAuth
# tables, as SQLite stored them.
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
PEOPLE_TABLES = ("users", "identities", "memberships")


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


def test_a_version_3_database_opens_keeping_every_row_with_no_person_on_them(db, monkeypatch):
    v3 = v3_database(db)
    step_5 = cairn.store.schema._MIGRATIONS[5]
    at_4 = []

    def look_before_step_5(con):  # step 4 alone: later steps change tables of their own
        at_4.append(list(con.iterdump()))
        step_5(con)

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 5, look_before_step_5)
    s = Store(db)
    try:
        assert SCHEMA_VERSION >= 4 and user_version(db) == SCHEMA_VERSION
        assert set(PEOPLE_TABLES) <= tables(db)
        assert count(db, *PEOPLE_TABLES) == dict.fromkeys(PEOPLE_TABLES, 0)
        after = at_4[0]
        moved = ("sessions", "oauth_grants", "oauth_codes")
        for line in (line for line in v3["statements"] if line.startswith('INSERT INTO "')):
            # the rebuilt tables hold the same rows, each with no person on it yet
            if line.split('"')[1] in moved:
                assert line.removesuffix(");") + ",NULL);" in after, line
            else:
                assert line in after, line

        assert s.resolve_session("token-ada").user_id is None
        # Step 4 kept the grant and the code (at_4), and the grant-audience step ended them.
        assert s.resolve_token("cairn.at.ada") is None and s.find_code("code-ada") is None
        assert s.for_account(V2_ADA).list_grants() == []

        signed = s.sign_in(
            "github", "1", "ada@example.test", "Ada", issuer="https://github.com", join="personal"
        )
        assert s.resolve_session(signed.token).user_id == signed.user.id  # what 4 adds works
        assert s.delete_account(V2_ADA) is True and s.resolve_token("cairn.at.ada") is None
    finally:
        s.close()


def test_a_database_migrated_from_version_3_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v3_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert set(PEOPLE_TABLES) <= tables(fresh)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_4_leaves_the_version_3_database_as_it_was(db, monkeypatch):
    v3 = v3_database(db)
    step = cairn.store.schema._MIGRATIONS[4]
    ran = []

    def fail_after_the_real_work(con):
        step(con)
        ran.append({name for (name,) in con.execute("SELECT name FROM sqlite_master")})
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 4, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert set(PEOPLE_TABLES) <= ran[0]
    assert statements(db) == v3["statements"] and user_version(db) == 3

    monkeypatch.undo()
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION
        assert s.resolve_session("token-ada").account_id == V2_ADA
    finally:
        s.close()


# Schema 5: reach. A database as cairn wrote it at version 4 is version 3's after step 4 (a
# step is history: it is never edited, so it builds exactly what cairn wrote then), with a
# person on the grant.
REACH_TABLES = ("api_keys", "oauth_grants", "oauth_codes")
_WITH_KEY_ID = re.compile(r"(INSERT INTO \"api_keys\" VALUES\(\d+,)'key_[0-9a-f]{12}',")


KEYED_SESSION = hashlib.sha256(b"token-keyed").hexdigest()


def kept_at_17(lines: list[str]) -> list[str]:
    """The fixture's statements a migration to the newest version keeps: all but the session
    opened with a key before sessions kept the key's id, which step 17 (_KEY_SESSIONS) ends."""
    return [line for line in lines if KEYED_SESSION not in line]


def before_step_7(lines: list[str]) -> list[str]:
    """Statements of a database migrated past step 7, as step 6 left them: step 7 gives each
    key an id ("key_" + 12 hex chars, second column) and each session a key_id (the last,
    NULL on every session that existed). Anything else step 7 changed stays, and fails the
    comparison."""
    out = []
    for line in lines:
        if line.startswith('INSERT INTO "api_keys"'):
            found = _WITH_KEY_ID.match(line)
            assert found, line
            line = found.group(1) + line[found.end() :]
        elif line.startswith('INSERT INTO "sessions"'):
            assert line.endswith(",NULL);"), line
            line = line.removesuffix(",NULL);") + ");"
        out.append(line)
    return out


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


def test_a_version_4_database_opens_with_every_key_and_grant_reaching_every_project(db):
    v4 = v4_database(db)
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION >= 5 and "project_sets" in tables(db)
        assert count(db, "project_sets") == {"project_sets": 0}
        after = before_step_7(before_step_10(before_step_24(before_step_22(statements(db)))))
        for line in kept_rows(v4["statements"]):
            # the rebuilt tables hold the same rows, each reaching every project, to write
            if line.split('"')[1] in REACH_TABLES:
                assert line.removesuffix(");") + ",NULL,'write');" in after, line
            elif line.split('"')[1] == "sessions":  # step 6 rebuilds them: opened by no key
                assert line.removesuffix(");") + ",NULL);" in after, line
            elif line.split('"')[1] == "projects":  # step 7 rebuilds them: each one general
                assert line.removesuffix(");") + ",'general');" in after, line
            else:
                assert line in after, line

        key = s.resolve_key("key-ada")
        assert (key.projects, key.access) == (None, "write")
        # The grant step 5 widened is ended by the grant-audience step.
        assert s.resolve_token("cairn.at.ada") is None
        ada = s.for_account(V2_ADA)
        assert ada.list_grants() == []
        assert [(k.label, k.projects, k.access) for k in ada.list_keys()] == [
            ("laptop", None, "write")
        ]
        core = ada.get_project("core")
        work = ada.create_set("work", (core.id,))  # what 5 adds works
        reader = ada.create_key("ci", projects=(work.id,), access="read")
        who = s.resolve_key(reader)
        assert [b.name for b in ada.narrow(who.projects, who.access).list_projects()] == ["core"]
        assert s.delete_account(V2_ADA) is True and count(db, "project_sets") == {"project_sets": 0}
    finally:
        s.close()


def test_a_version_4_index_gets_its_search_statistics_and_ranks_as_a_fresh_one(tmp_path):
    """Version 4 kept no per-project counts: the step to 5 counts them from the index, so Ada's
    page ranks exactly as the same page saved by version 5 does."""
    old, fresh = tmp_path / "old.db", tmp_path / "fresh.db"
    v4_database(old)
    s, f = Store(old), Store(fresh)
    try:
        ada = s.for_account(V2_ADA)
        (page,) = ada.list_pages(ada.get_project("core").id)
        twin = f.create_project("core")
        f.save_page(twin.id, page.slug, page.title, page.body, page.claims)
        assert rows(old, "SELECT claims, tokens FROM search_projects") == rows(
            fresh, "SELECT claims, tokens FROM search_projects"
        )
        assert [(h.claim_id, h.score) for h in ada.search("relay retries")] == [
            (h.claim_id, h.score) for h in f.search("relay retries")
        ]
    finally:
        s.close()
        f.close()


def test_a_database_migrated_from_version_4_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v4_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert "project_sets" in tables(fresh)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_5_leaves_the_version_4_database_as_it_was(db, monkeypatch):
    v4 = v4_database(db)
    step = cairn.store.schema._MIGRATIONS[5]
    ran = []

    def fail_after_the_real_work(con):
        step(con)
        ran.append({name for (name,) in con.execute("SELECT name FROM sqlite_master")})
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 5, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert "project_sets" in ran[0]
    assert statements(db) == v4["statements"] and user_version(db) == 4

    monkeypatch.undo()
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION and s.resolve_key("key-ada").access == "write"
    finally:
        s.close()


# Schema 6: settings and the audit log, and sessions remember the key they were opened with.
# A database as cairn wrote it at version 5 is version 4's after step 5, with a set.


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


def test_a_version_5_database_opens_keeping_every_row_with_no_key_on_its_sessions(db):
    v5 = v5_database(db)
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION >= 6
        assert count(db, "settings", "audit_events") == {"settings": 0, "audit_events": 0}
        after = before_step_7(before_step_10(before_step_24(before_step_22(statements(db)))))
        for line in kept_rows(v5["statements"]):
            if line.split('"')[1] == "sessions":  # rebuilt: the same rows, opened by no key
                assert line.removesuffix(");") + ",NULL);" in after, line
            elif line.split('"')[1] == "projects":  # step 7 rebuilds them: each one general
                assert line.removesuffix(");") + ",'general');" in after, line
            else:
                assert line in after, line
        session = s.resolve_session("token-ada")
        assert (session.account_id, session.label, session.user_id) == (V2_ADA, None, None)
        ada = s.for_account(V2_ADA)
        assert ada.list_audit() == [] and ada.get_setting("guard.policy") is None
        ada.set_setting("guard.policy", "off")  # what 6 adds works
        laptop = ada.acting_as("key", "laptop")
        assert s.resolve_session(laptop.create_session()).label == "laptop"
        assert [e.action for e in ada.list_audit()] == ["setting.changed"]
        assert s.delete_account(V2_ADA) is True
        assert count(db, "audit_events") == {"audit_events": 0}
        # the one row left is the store's record that a compaction is due, not Ada's
        assert rows(db, "SELECT account_id, name FROM settings") == [
            (DEFAULT_ACCOUNT_ID, "store.compact_due")
        ]
    finally:
        s.close()


def test_a_database_migrated_from_version_5_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v5_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert {"settings", "audit_events"} <= tables(fresh)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_6_leaves_the_version_5_database_as_it_was(db, monkeypatch):
    v5 = v5_database(db)
    step = cairn.store.schema._MIGRATIONS[6]
    ran = []

    def fail_after_the_real_work(con):
        step(con)
        ran.append({name for (name,) in con.execute("SELECT name FROM sqlite_master")})
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 6, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert {"settings", "audit_events"} <= ran[0]
    assert statements(db) == v5["statements"] and user_version(db) == 5

    monkeypatch.undo()
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION and s.resolve_session("token-ada") is not None
    finally:
        s.close()


# Schema 7: actor ids. Each key gets an id, sessions the id of the key they were opened with,
# audit events the id of their actor. A database as cairn wrote it at version 6 is version
# 5's after step 6, with a setting, a session opened by a key and two recorded events.


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


def test_a_version_6_database_opens_with_an_id_for_every_key_and_no_actor_id_yet(db):
    v6 = v6_database(db)
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION >= 7
        after = before_step_10(before_step_24(before_step_22(statements(db))))
        numbers: dict[str, int] = {}  # per workspace, in recording order
        for line in kept_rows(kept_at_17(v6["statements"])):
            table = line.split('"')[1]
            if table == "audit_events":  # rebuilt: numbered in its workspace, no actor id
                head, tail = line.split(",'key','laptop',")
                account, at = head.split("','", 1)
                workspace = account.rsplit("'", 1)[1]
                numbers[workspace] = numbers.get(workspace, 0) + 1
                expected = f"{account}',{numbers[workspace]},'{at},'key','laptop',NULL,{tail}"
                assert expected in after, line
            elif table == "projects":  # step 8: the same rows, each of kind general
                assert line.removesuffix(");") + ",'general');" in after, line
            elif table not in ("api_keys", "sessions"):
                assert line in after, line
        for table in ("api_keys", "sessions"):  # the same rows, with ids and no session key
            mark = f'INSERT INTO "{table}"'
            old = [line for line in kept_at_17(v6["statements"]) if line.startswith(mark)]
            assert [line for line in before_step_7(after) if line.startswith(mark)] == old
        ids = [key_id for (key_id,) in rows(db, "SELECT id FROM api_keys ORDER BY seq")]
        assert len(ids) == len(set(ids)) >= 1 and all(
            re.fullmatch(r"key_[0-9a-f]{12}", i) for i in ids
        )
        ada = s.for_account(V2_ADA)
        assert [(e.action, e.actor, e.actor_id) for e in ada.list_audit()] == [
            ("project.created", "laptop", None),
            ("setting.changed", "laptop", None),
        ]
        assert s.identify_session("token-keyed") is None  # step 17 ends it: no key id
        principal, key_id = s.identify_key("key-ada")
        assert key_id in ids  # what 7 adds works
        ada.acting_as("key", principal.label, key_id).create_project("after")
        assert ada.list_audit()[0].actor_id == key_id
    finally:
        s.close()


def test_a_database_migrated_from_version_6_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v6_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_7_leaves_the_version_6_database_as_it_was(db, monkeypatch):
    v6 = v6_database(db)
    step = cairn.store.schema._MIGRATIONS[7]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 7, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v6["statements"] and user_version(db) == 6
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema 8: every project has a kind (cairn.kinds). A database as cairn wrote it at version 7
# is version 6's after step 7, with an event recorded under a key's id.


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


def test_a_version_7_database_opens_with_every_project_general(db):
    v7 = v7_database(db)
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION >= 8
        after = before_step_10(before_step_24(before_step_22(statements(db))))
        for line in kept_rows(kept_at_17(v7["statements"])):
            if line.split('"')[1] == "projects":  # rebuilt: the same rows, each of kind general
                assert line.removesuffix(");") + ",'general');" in after, line
            else:
                assert line in after, line
        ada = s.for_account(V2_ADA)
        core = ada.get_project("core")
        assert (core.id, core.description, core.kind) == ("br_aaaaaaaaaaaa", "hers", "general")
        assert ada.create_project("payments-api", kind="repo").kind == "repo"  # what 8 adds works
        with pytest.raises(ValueError):
            ada.create_project("CORE", kind="repo")  # names are still unique ignoring case
        assert [b.kind for b in ada.list_projects()] == ["general", "repo"]
    finally:
        s.close()


def test_a_database_migrated_from_version_7_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v7_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_8_leaves_the_version_7_database_as_it_was(db, monkeypatch):
    v7 = v7_database(db)
    step = cairn.store.schema._MIGRATIONS[8]
    ran = []

    def fail_after_the_real_work(con):
        step(con)
        ran.append([row[1] for row in con.execute("PRAGMA table_info(projects)")])
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 8, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert "kind" in ran[0]
    assert statements(db) == v7["statements"] and user_version(db) == 7

    monkeypatch.undo()
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION and s.for_account(V2_ADA).get_project("core")
    finally:
        s.close()


# Schema 9: sources (cairn.sources). A database as cairn wrote it at version 8 is version 7's
# after step 8.


def v8_database(db: Path) -> dict[str, Any]:
    v7_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[8](con)
        con.execute("PRAGMA user_version = 8")
        con.commit()
    assert user_version(db) == 8
    return {"statements": statements(db)}


def test_a_version_8_database_opens_with_no_source_and_every_row_kept(db):
    v8 = v8_database(db)
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION >= 9 and "sources" in tables(db)
        after = before_step_10(before_step_24(before_step_22(statements(db))))
        assert all(line in after for line in kept_rows(kept_at_17(v8["statements"])))
        ada = s.for_account(V2_ADA)
        assert ada.list_sources() == []
        config = {
            "base_url": "https://langfuse.example.test",
            "projects": ["core"],
            "public_key_env": "LANGFUSE_PUBLIC_KEY",
            "secret_key_env": "LANGFUSE_SECRET_KEY",
        }
        assert ada.add_source("traces", "langfuse", config).config == config  # what 9 adds
    finally:
        s.close()


def test_a_database_migrated_from_version_8_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v8_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_9_leaves_the_version_8_database_as_it_was(db, monkeypatch):
    v8 = v8_database(db)
    step = cairn.store.schema._MIGRATIONS[9]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 9, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v8["statements"] and user_version(db) == 8
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema 10: who reaches a project (its maker and rule), the person who made a key, and
# invitations. A database as cairn wrote it at version 9 is version 8's after step 9.


def before_step_10(lines: list[str]) -> list[str]:
    """Statements of a database migrated past step 10, as step 9 left them: step 10 gives
    each project a maker, NULL, and a rule, 'write' (its last two columns), and each key a
    maker, NULL (its last). Anything else step 10 changed stays, and fails the comparison."""
    out = []
    for line in lines:
        if line.startswith('INSERT INTO "projects"'):
            assert line.endswith(",NULL,'write');"), line
            line = line.removesuffix(",NULL,'write');") + ");"
        elif line.startswith('INSERT INTO "api_keys"'):
            assert line.endswith(",NULL);"), line
            line = line.removesuffix(",NULL);") + ");"
        out.append(line)
    return out


def v9_database(db: Path) -> dict[str, Any]:
    v8_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[9](con)
        con.execute("PRAGMA user_version = 9")
        con.commit()
    assert user_version(db) == 9
    return {"statements": statements(db)}


def test_a_version_9_database_opens_with_every_project_open_and_every_key_the_workspaces(db):
    v9 = v9_database(db)
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION >= 10
        assert count(db, "project_people", "invites") == {"project_people": 0, "invites": 0}
        after = before_step_10(before_step_24(before_step_22(statements(db))))
        assert all(line in after for line in kept_rows(kept_at_17(v9["statements"])))
        assert s.resolve_key("key-ada").user_id is None  # still the workspace's own key
        ada = s.for_account(V2_ADA, "usr_aaaaaaaaaaaa")
        assert ada.get_project("core").maker_id is None
        journal = ada.create_project("journal", everyone="none")  # what 10 adds works
        assert journal.maker_id == "usr_aaaaaaaaaaaa"
        assert ada.invite("brook@example.test").role == "member"
    finally:
        s.close()


def test_a_database_migrated_from_version_9_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v9_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_10_leaves_the_version_9_database_as_it_was(db, monkeypatch):
    v9 = v9_database(db)
    step = cairn.store.schema._MIGRATIONS[10]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 10, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v9["statements"] and user_version(db) == 9
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema 11: metering and plans (H6). A database as cairn wrote it at version 10 is version
# 9's after step 10, with one more note of Ada's: accented text, in another month.

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


def test_a_version_10_database_opens_with_its_notes_metered_and_every_row_kept(db):
    v10 = v10_database(db)
    relay = "# Relay notes\n\nThe relay retries twice.\n"  # v2_database's note, in January
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION >= 11
        after = before_step_24(before_step_22(statements(db)))
        assert all(line in after for line in kept_rows(kept_at_17(v10["statements"])))
        ada = s.for_account(V2_ADA)
        assert ada.stored_bytes() == len(relay.encode()) + len(V10_ACCENTED.encode())
        assert len(V10_ACCENTED.encode()) > len(V10_ACCENTED)  # bytes, not characters
        january, february = ada.usage("2026-01"), ada.usage("2026-02")
        assert (january.notes, january.note_bytes) == (1, len(relay.encode()))
        assert (february.notes, february.note_bytes) == (1, len(V10_ACCENTED.encode()))
        assert s.stored_bytes() == 0 and s.get_account(V2_ADA).plan is None
        s.set_plan(V2_ADA, "solo")  # what 11 adds
        assert s.get_account(V2_ADA).plan == "solo"
    finally:
        s.close()


def test_a_database_migrated_from_version_10_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v10_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_11_leaves_the_version_10_database_as_it_was(db, monkeypatch):
    v10 = v10_database(db)
    step = cairn.store.schema._MIGRATIONS[11]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 11, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v10["statements"] and user_version(db) == 10
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema 12: billing (H7). A database as cairn wrote it at version 11 is version 10's after
# step 11, with Ada's workspace put on a plan by the operator.


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


def test_a_version_11_database_opens_with_an_empty_subscriptions_table_and_every_row_kept(db):
    v11 = v11_database(db)
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION >= 12
        after = before_step_24(before_step_22(statements(db)))
        assert all(line in after for line in kept_rows(kept_at_17(v11["statements"])))
        assert s.plan_in_force(V2_ADA) == "solo"  # the operator's, as before
        assert rows(db, "SELECT COUNT(*) FROM subscriptions") == [(0,)]
    finally:
        s.close()


def test_a_database_migrated_from_version_11_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v11_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_12_leaves_the_version_11_database_as_it_was(db, monkeypatch):
    v11 = v11_database(db)
    step = cairn.store.schema._MIGRATIONS[12]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 12, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v11["statements"] and user_version(db) == 11
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


def subscribed_database(db: Path) -> dict[str, Any]:
    """A database one step before the subscriptions keep a start and a past-due instant
    (H7 fix round): Ada holds a subscription."""
    step = cairn.store.schema._SUBSCRIPTION_TIMES
    v11_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        for version in range(12, step):
            cairn.store.schema._MIGRATIONS[version](con)
        con.execute(
            "INSERT INTO subscriptions (account_id, provider, plan, status, period_end, since,"
            " customer_ref, subscription_ref) VALUES (?, 'till', 'team', 'past_due',"
            " '2026-12-10T12:00:00.000000+00:00', '2026-11-10T12:00:00.000000+00:00',"
            " 'cus_1', 'sub_1')",
            (V2_ADA,),
        )
        con.execute(f"PRAGMA user_version = {step - 1}")
        con.commit()
    return {"statements": statements(db), "version": step - 1}


def test_a_database_before_subscription_times_opens_with_its_subscription_kept(db):
    before = subscribed_database(db)
    s = Store(db, clock=lambda: datetime(2026, 11, 12, tzinfo=UTC))
    try:
        assert user_version(db) == SCHEMA_VERSION > before["version"]
        assert rows(
            db, "SELECT account_id, plan, status, started, past_due_since FROM subscriptions"
        ) == [(V2_ADA, "team", "past_due", None, None)]
        assert s.plan_in_force(V2_ADA) == "solo"  # the operator's: nothing reads the row
    finally:
        s.close()


def test_a_database_migrated_to_subscription_times_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    subscribed_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)


def test_a_failed_step_to_subscription_times_leaves_the_database_as_it_was(db, monkeypatch):
    before = subscribed_database(db)
    version = cairn.store.schema._SUBSCRIPTION_TIMES
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == before["statements"] and user_version(db) == version - 1
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema 14: model-use reservations (T4b). A database as cairn wrote it at version 13 is
# version 11's after steps 12 and 13, with a month of model use recorded for Ada's workspace.


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


def test_a_version_13_database_opens_with_no_reservation_and_its_use_kept(db):
    v13 = v13_database(db)
    s = Store(db, clock=lambda: datetime(2026, 11, 10, tzinfo=UTC))
    try:
        assert user_version(db) == SCHEMA_VERSION
        after = before_step_24(before_step_22(statements(db)))
        assert all(line in after for line in kept_rows(kept_at_17(v13["statements"])))
        ada = s.for_account(V2_ADA)
        assert ada.model_reservations() == [] and ada.usage().model_micros == 500
        assert ada.reserve_model_use(1) is not None
    finally:
        s.close()


def test_a_database_migrated_from_version_13_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v13_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_14_leaves_the_version_13_database_as_it_was(db, monkeypatch):
    v13 = v13_database(db)
    step = cairn.store.schema._MIGRATIONS[14]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, 14, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v13["statements"] and user_version(db) == 13
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema 15: the digests of forgotten notes (H5b). A database as cairn wrote it at version 14
# is version 13's after step 14, with one note in a project of Ada's workspace.


def v14_database(db: Path) -> dict[str, Any]:
    v13_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[14](con)
        con.execute("PRAGMA user_version = 14")
        con.commit()
    assert user_version(db) == 14
    return {"statements": statements(db)}


def test_a_version_14_database_opens_with_nothing_forgotten_and_every_row_kept(db):
    v14 = v14_database(db)
    s = Store(db, clock=lambda: datetime(2026, 11, 10, tzinfo=UTC))
    try:
        assert user_version(db) == SCHEMA_VERSION >= 15
        after = before_step_24(before_step_22(statements(db)))
        assert all(line in after for line in kept_rows(kept_at_17(v14["statements"])))
        assert rows(db, "SELECT COUNT(*) FROM forgotten") == [(0,)]
        ada = s.for_account(V2_ADA)
        project = ada.create_project("later")
        note, created = ada.add_note(project.id, "# Later\n\nA note.\n")
        assert created and ada.forget_note(note.id).note_id == note.id
        assert rows(db, "SELECT COUNT(*) FROM forgotten") == [(1,)]
    finally:
        s.close()


def test_a_database_migrated_from_version_14_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v14_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_15_leaves_the_version_14_database_as_it_was(db, monkeypatch):
    v14 = v14_database(db)
    version = cairn.store.schema._FORGOTTEN
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v14["statements"] and user_version(db) == 14
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema 16: each workspace's passages and page history, counted (H6c). A database as cairn
# wrote it at version 15 is version 14's after step 15: Ada's workspace holds a note with its
# spans and a page with a version.


def v15_database(db: Path) -> dict[str, Any]:
    v14_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._FORGOTTEN](con)
        con.execute(f"PRAGMA user_version = {cairn.store.schema._FORGOTTEN}")
        con.commit()
    assert user_version(db) == 15
    return {"statements": statements(db)}


def counted_from_rows(db: Path, account_id: str) -> tuple[int, int]:
    """(passages, history bytes) of a workspace, recomputed from its rows."""
    ((passages,),) = rows(
        db,
        "SELECT COUNT(*) FROM spans JOIN notes ON notes.id = spans.note_id"
        " JOIN projects ON projects.id = notes.project_id WHERE projects.account_id = ?"
        " AND spans.kind IN ('paragraph', 'bullet', 'code', 'table', 'quote')",
        account_id,
    )
    ((history,),) = rows(
        db,
        "SELECT COALESCE(SUM(LENGTH(CAST(body AS BLOB)) + LENGTH(CAST(claims AS BLOB))), 0)"
        " FROM page_versions JOIN projects ON projects.id = page_versions.project_id"
        " WHERE projects.account_id = ?",
        account_id,
    )
    return passages, history


def test_a_version_15_database_opens_with_its_passages_and_history_counted(db):
    v15 = v15_database(db)
    held = counted_from_rows(db, V2_ADA)
    assert held[0] > 0 and held[1] > 0  # the note's passages and the page's version
    s = Store(db, clock=lambda: datetime(2026, 11, 10, tzinfo=UTC))
    try:
        assert user_version(db) == SCHEMA_VERSION and cairn.store.schema._DISK_BOUNDS == 16
        after = before_step_24(before_step_22(statements(db)))
        assert all(line in after for line in kept_rows(kept_at_17(v15["statements"])))
        ada = s.for_account(V2_ADA)
        assert (ada.passages(), ada.history_bytes()) == held
        assert (s.passages(), s.history_bytes()) == counted_from_rows(db, DEFAULT_ACCOUNT_ID)
        project = ada.create_project("later")
        ada.add_note(project.id, "# Later\n\nOne.\n\n- two\n- three\n")
        assert ada.passages() == held[0] + 3 == counted_from_rows(db, V2_ADA)[0]
    finally:
        s.close()


def test_a_database_migrated_from_version_15_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v15_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_16_leaves_the_version_15_database_as_it_was(db, monkeypatch):
    v15 = v15_database(db)
    version = cairn.store.schema._DISK_BOUNDS
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v15["statements"] and user_version(db) == 15
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


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


def test_a_version_16_database_ends_the_key_sessions_it_cannot_tie_to_a_live_key(db):
    v16 = v16_database(db)
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION and cairn.store.schema._KEY_SESSIONS == 17
    left = {digest for (digest,) in rows(db, "SELECT digest FROM sessions")}
    assert {"s-live", "s-person"} <= left
    assert not left & {"s-revoked", "s-old", KEYED_SESSION}
    after = before_step_24(before_step_22(statements(db)))
    ended = ("'s-revoked'", "'s-old'", KEYED_SESSION)
    kept = [line for line in kept_rows(v16["statements"]) if not any(e in line for e in ended)]
    assert all(line in after for line in kept)


def test_a_database_migrated_from_version_16_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v16_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_17_leaves_the_version_16_database_as_it_was(db, monkeypatch):
    v16 = v16_database(db)
    version = cairn.store.schema._KEY_SESSIONS
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v16["statements"] and user_version(db) == 16
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema _HEADING_CHARGE (18): the passages counter charges heading paths, one for every full
# 40 bytes of the path stored with a span. A database as cairn wrote it at version 17 is
# version 16's after step 17, with a note of Ada's whose spans sit under long headings, one
# path written with JSON's \u escapes, as cairn wrote them before paths were stored as UTF-8.

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


def charged_from_rows(db: Path, account_id: str) -> int:
    """A workspace's passages as charged: one for each passage, and one for every full 40
    bytes of each span's heading path as stored (its JSON less the quotes, separators and
    brackets)."""
    total = 0
    for kind, path in rows(
        db,
        "SELECT spans.kind, spans.heading_path FROM spans"
        " JOIN notes ON notes.id = spans.note_id"
        " JOIN projects ON projects.id = notes.project_id WHERE projects.account_id = ?",
        account_id,
    ):
        headings = json.loads(path)
        stored = len(path.encode()) - (4 * len(headings) if headings else 2)
        total += (kind in ("paragraph", "bullet", "code", "table", "quote")) + stored // 40
    return total


def test_a_version_17_database_opens_with_its_heading_paths_charged(db):
    v17 = v17_database(db)
    assert counted_from_rows(db, V2_ADA)[0] < charged_from_rows(db, V2_ADA)
    s = Store(db, clock=lambda: datetime(2026, 11, 10, tzinfo=UTC))
    try:
        assert user_version(db) == SCHEMA_VERSION and cairn.store.schema._HEADING_CHARGE == 18
        after = before_step_24(before_step_22(statements(db)))
        kept = kept_rows(v17["statements"])
        recounted = 'INSERT INTO "account_disk"'  # the one table the step rewrites
        assert all(line in after for line in kept if not line.startswith(recounted))
        ada = s.for_account(V2_ADA)
        held = charged_from_rows(db, V2_ADA)
        long_ones = 9 + 10 + (0 + 24) + (1 + 24)  # 390 B; then 390 + 600 B as escaped
        assert ada.passages() == held >= long_ones
        assert s.passages() == charged_from_rows(db, DEFAULT_ACCOUNT_ID)
        project = ada.create_project("later")
        note, _ = ada.add_note(project.id, f"# {LONG}\n\n- three\n")
        assert ada.passages() == held + 19 == charged_from_rows(db, V2_ADA)
        ada.forget_note(note.id)
        ada.forget_note("cap_0000000000aa")
        assert ada.passages() == held - long_ones == charged_from_rows(db, V2_ADA)
    finally:
        s.close()


def test_a_database_migrated_from_version_17_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v17_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_version_18_leaves_the_version_17_database_as_it_was(db, monkeypatch):
    v17 = v17_database(db)
    version = cairn.store.schema._HEADING_CHARGE
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v17["statements"] and user_version(db) == 17
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema _GRANT_AUDIENCE: a code and a grant name the MCP address they are for. A database as
# cairn wrote it at version 18 is version 17's after step 18, plus an agent Ada connected: its
# client, its redeemed code, its grant and the grant's tokens, and a code still pending.


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


def test_a_version_18_database_opens_with_its_grants_ended_and_its_clients_kept(db):
    v18 = v18_database(db)
    held = count(db, *OAUTH_TABLES)
    assert held["oauth_grants"] and held["oauth_codes"] and held["oauth_tokens"]
    s = Store(db, clock=lambda: datetime(2026, 11, 11, tzinfo=UTC))
    try:
        assert user_version(db) == SCHEMA_VERSION and cairn.store.schema._GRANT_AUDIENCE == 19
        assert count(db, *OAUTH_TABLES) == {
            "oauth_clients": held["oauth_clients"],  # an agent connects again, unregistered
            "oauth_grants": 0,
            "oauth_codes": 0,
            "oauth_tokens": 0,
        }
        after = before_step_24(before_step_22(statements(db)))
        kept = [line for line in v18["statements"] if line.startswith("INSERT")]
        assert all(line in after for line in kept if "oauth_" not in line or "clients" in line)
        ada = s.for_account(V2_ADA)
        assert ada.list_grants() == [] and s.get_client("client_18").name == "old-agent"
        code = ada.create_code("client_18", REDIRECT, CHALLENGE, audience=MCP_URL)
        assert s.redeem_code(code, "client_18", audience=MCP_URL) is not None
    finally:
        s.close()


def test_a_database_migrated_from_version_18_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v18_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_the_grant_audience_leaves_the_version_18_database_as_it_was(
    db, monkeypatch
):
    v18 = v18_database(db)
    version = cairn.store.schema._GRANT_AUDIENCE
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v18["statements"] and user_version(db) == 18
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema _MANAGE_LEVEL: a person's level on a project may be "manage" (T2), and project_people is
# rebuilt to allow it. A database as cairn wrote it at version 19 is version 18's after step
# 19, plus two members of Ada's workspace: Dee reads "core" and Eli writes it, and "atlas", a
# private project Ada's workspace holds, names nobody.

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


def test_a_version_19_database_opens_with_every_persons_level_kept(db):
    v19 = v19_database(db)
    s = Store(db)
    try:
        assert user_version(db) == SCHEMA_VERSION and cairn.store.schema._MANAGE_LEVEL == 20
        after = before_step_24(before_step_22(statements(db)))
        assert all(line in after for line in kept_rows(v19["statements"]))
        assert rows(db, "SELECT project_id, user_id, access FROM project_people ORDER BY 2") == [
            ("br_aaaaaaaaaaaa", V19_DEE, "read"),
            ("br_aaaaaaaaaaaa", V19_ELI, "write"),
        ]
        operator = s.for_account(V2_ADA)
        held = {V19_DEE: "read", V19_ELI: "write"}
        assert operator.project_access("br_aaaaaaaaaaaa").people == held
        assert operator.project_access(V19_ATLAS).people == {}
        dee = s.for_account(V2_ADA, V19_DEE)
        with pytest.raises(PermissionError):
            dee.add_note("br_aaaaaaaaaaaa", "# Core\n\nDee reads core only.\n")
        operator.set_project_access("br_aaaaaaaaaaaa", people={V19_DEE: "manage"})
        assert dee.project_access("br_aaaaaaaaaaaa").people == {V19_DEE: "manage"}
    finally:
        s.close()
    with connect(db) as con, pytest.raises(sqlite3.IntegrityError):
        con.execute(  # the new CHECK: three levels, and "admin" is not one
            "INSERT INTO project_people (project_id, user_id, access) VALUES (?, ?, 'admin')",
            (V19_ATLAS, V19_ELI),
        )


def test_a_database_migrated_from_version_19_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v19_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_the_manage_level_leaves_the_version_19_database_as_it_was(
    db, monkeypatch
):
    v19 = v19_database(db)
    version = cairn.store.schema._MANAGE_LEVEL
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v19["statements"] and user_version(db) == 19
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema _SOURCE_SWITCH (S2): a source is on or off, and records when it was last switched
# on. A database as cairn wrote it at version 20 is version 19's after step 20, plus a source
# of Ada's workspace that has run once and holds a claim, and one that never ran.

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


def test_a_version_20_database_opens_with_every_source_on_since_it_was_added(db):
    v20_database(db)
    held = rows(db, "SELECT * FROM sources ORDER BY seq")
    s = Store(db, clock=lambda: datetime(2026, 12, 1, tzinfo=UTC))
    try:
        assert user_version(db) == SCHEMA_VERSION > cairn.store.schema._SOURCE_SWITCH == 21
        # Every column it had is kept, a run's claim included; each is on since it was added.
        assert rows(db, "SELECT * FROM sources ORDER BY seq") == [(*row, 1, row[6]) for row in held]
        operator = s.for_account(V2_ADA)
        ran, fresh = operator.get_source("ran"), operator.get_source("fresh")
        assert (ran.enabled, ran.enabled_at, ran.cursor) == (True, V20_ADDED, "cursor-20")
        assert (fresh.name, fresh.enabled, fresh.enabled_at) == ("Fresh", True, V20_LATER)
    finally:
        s.close()
    with connect(db) as con, pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE sources SET enabled = 2")  # the new CHECK: on or off


def test_a_database_migrated_from_version_20_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v20_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_the_source_switch_leaves_the_version_20_database_as_it_was(
    db, monkeypatch
):
    v20 = v20_database(db)
    version = cairn.store.schema._SOURCE_SWITCH
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v20["statements"] and user_version(db) == 20
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema _VOUCHED_EMAIL (B1): a session records the address the identity provider vouched for
# at the sign-in it came from. A database as cairn wrote it at version 21 is version 20's
# after step 21, plus a person's session made by signing in.

V21_SESSION = hashlib.sha256(b"token-v21").hexdigest()


def before_step_22(lines: list[str]) -> list[str]:
    """Statements of a database migrated past step 22, as step 21 left them: step 22 gives
    each session a vouched address (its last column), NULL on every session that existed.
    Anything else step 22 changed stays, and fails the comparison."""
    out = []
    for line in lines:
        if line.startswith('INSERT INTO "sessions"'):
            assert line.endswith(",NULL);"), line
            line = line.removesuffix(",NULL);") + ");"
        out.append(line)
    return out


_WITH_TOPIC_KEY = re.compile(r",'(?:[^']|'')*',[01]\);$")  # topic_key, before_topics


def before_step_24(lines: list[str]) -> list[str]:
    """Statements of a database migrated past step 24, as step 23 left them: step 24 gives
    each note a topic key and its mark of a note stored before N1 (its last two columns).
    Anything else step 24 changed stays, and fails the comparison; its new tables hold rows
    of their own, which no earlier database had, and are left out, as is the one row step 25
    writes (A1: the moment it ran) and the heads step 26 derives from page_versions (Q1)."""
    out = []
    for line in lines:
        if line.startswith('INSERT INTO "notes"'):
            found = _WITH_TOPIC_KEY.search(line)
            assert found, line
            line = line[: found.start()] + ");"
        elif line.startswith(
            (
                'INSERT INTO "page_keys"',
                'INSERT INTO "waiting"',
                'INSERT INTO "recorded_since"',
                'INSERT INTO "page_heads"',
            )
        ):
            continue
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


def test_a_version_21_database_keeps_every_session_with_no_vouched_address(db):
    v21 = v21_database(db)
    held = rows(db, "SELECT * FROM sessions ORDER BY rowid")
    s = Store(db, clock=lambda: datetime(2026, 12, 1, tzinfo=UTC))
    try:
        assert user_version(db) == SCHEMA_VERSION > cairn.store.schema._VOUCHED_EMAIL == 22
        assert rows(db, "SELECT * FROM sessions ORDER BY rowid") == [(*r, None) for r in held]
        inserted = [line for line in v21["statements"] if line.startswith("INSERT")]
        after = [
            line
            for line in before_step_24(before_step_22(statements(db)))
            if line.startswith("INSERT")
        ]
        assert sorted(after) == sorted(inserted)  # every row of every table kept
        # Found by its person's stored address, as before the step.
        assert s.end_person_sessions("ada@example.test") == {V2_ADA: 1}
    finally:
        s.close()


def test_a_database_migrated_from_version_21_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v21_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_the_vouched_address_leaves_the_version_21_database_as_it_was(
    db, monkeypatch
):
    v21 = v21_database(db)
    version = cairn.store.schema._VOUCHED_EMAIL
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v21["statements"] and user_version(db) == 21
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


# Schema _SOURCE_ITEMS (I1): the items each source wrote, by digest. A database as cairn wrote
# it at version 22 is version 21's after step 22.


def v22_database(db: Path) -> dict[str, Any]:
    v21_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._VOUCHED_EMAIL](con)
        con.execute(f"PRAGMA user_version = {cairn.store.schema._VOUCHED_EMAIL}")
        con.commit()
    assert user_version(db) == 22
    return {"statements": statements(db)}


def test_step_23_adds_the_source_items_table_and_keeps_every_row(db):
    v22 = v22_database(db)
    assert cairn.store.schema._SOURCE_ITEMS == 23
    with connect(db) as con:  # step 23 alone, whatever steps come after it
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._SOURCE_ITEMS](con)
        con.commit()
    added = f"{cairn.store.schema._V23_SOURCE_ITEMS};"
    assert sorted(statements(db)) == sorted([*v22["statements"], added])


def test_a_version_22_database_opens_with_no_item_recorded(db):
    v22_database(db)
    s = Store(db, clock=lambda: datetime(2026, 12, 1, tzinfo=UTC))
    try:
        assert user_version(db) == SCHEMA_VERSION > 22
        assert rows(db, "SELECT COUNT(*) FROM source_items") == [(0,)]
        operator = s.for_account(V2_ADA)
        assert operator.get_source("ran").cursor == "cursor-20"  # the sources go on
    finally:
        s.close()


def test_a_database_migrated_from_version_22_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v22_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_the_source_items_leaves_the_version_22_database_as_it_was(
    db, monkeypatch
):
    v22 = v22_database(db)
    version = cairn.store.schema._SOURCE_ITEMS
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v22["statements"] and user_version(db) == 22
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


def test_the_source_items_table_is_keyed_by_source_and_item_with_no_other_index(store, db):
    assert rows(
        db, "SELECT name FROM sqlite_master WHERE tbl_name = 'source_items' ORDER BY name"
    ) == [("source_items",)]  # WITHOUT ROWID: the primary key is the table
    columns = rows(db, "SELECT name, pk FROM pragma_table_info('source_items') ORDER BY cid")
    assert columns == [("source", 1), ("item", 2), ("written_at", 0)]


# Step 24 (N1): every page and note gets a topic key; nothing waits


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


def test_step_24_keys_every_page_by_its_latest_title_and_every_note_by_its_page(db):
    v23 = v23_database(db)
    assert cairn.store.schema._TOPIC_KEYS == 24
    with connect(db) as con:  # step 24 alone, whatever steps come after it
        con.execute("BEGIN")
        cairn.store.schema._MIGRATIONS[cairn.store.schema._TOPIC_KEYS](con)
        con.commit()
    assert rows(db, "SELECT project_id, slug, topic_key FROM page_keys ORDER BY slug") == [
        ("br_aaaaaaaaaaaa", "c", "c#"),  # merged before: it stays merged, C#'s
        ("br_aaaaaaaaaaaa", "relay-notes", "relay notes"),
    ]
    assert rows(db, "SELECT id, topic_key FROM notes ORDER BY seq") == [
        ("cap_000000000001", "relay notes"),
        ("cap_000000000002", "grosse"),  # v10_database's "Größe": on no page either
        ("cap_0000000000aa", "t"),  # v17_database's "T"
        ("cap_000000000c01", "c#"),  # filed on `c`: the note is where it is
        ("cap_000000000c02", "c#"),
        ("cap_000000000c03", "loose ends"),  # on no page: its topic's key
    ]
    # The notes no page's latest version cites wait, for `serve` to file them; every note
    # found is marked as stored before N1.
    assert rows(db, "SELECT note_id, project_id, tries FROM waiting ORDER BY note_id") == [
        ("cap_000000000002", "br_aaaaaaaaaaaa", 0),
        ("cap_0000000000aa", "br_aaaaaaaaaaaa", 0),
        ("cap_000000000c03", "br_aaaaaaaaaaaa", 0),
    ]
    assert rows(db, "SELECT COUNT(*) FROM notes WHERE before_topics = 1") == [(6,)]
    inserts = sorted(line for line in v23["statements"] if line.startswith("INSERT"))
    after = before_step_24(statements(db))
    assert sorted(line for line in after if line.startswith("INSERT")) == inserts


def test_step_24_keeps_its_own_copy_of_the_rule(db, monkeypatch):
    """A step is history: changing today's rule (cairn.topics) changes nothing it does."""
    v23_database(db)
    monkeypatch.setattr(cairn.topics, "topic_key", lambda title: "changed")
    Store(db).close()
    assert {key for (key,) in rows(db, "SELECT topic_key FROM page_keys")} == {"c#", "relay notes"}


def test_two_pages_whose_latest_titles_share_a_key_give_it_to_the_first_by_slug(db):
    """Only pages saved outside the compiler can: the first takes the key, the other none."""
    v23_database(db)
    with connect(db) as con:
        con.execute(
            "INSERT INTO page_versions VALUES ('br_aaaaaaaaaaaa', 'a-relay', 1, 'RELAY NOTES',"
            " '', '[]', '{}', ?)",
            (stamp(9),),
        )
        con.commit()
    Store(db).close()
    assert rows(db, "SELECT slug FROM page_keys WHERE topic_key = 'relay notes'") == [("a-relay",)]
    assert rows(db, "SELECT COUNT(*) FROM page_keys WHERE slug = 'relay-notes'") == [(0,)]


def test_a_version_23_database_opens_keeping_its_triggers_and_files_by_page(db):
    v23_database(db)
    s = Store(db, clock=lambda: datetime(2099, 1, 1, tzinfo=UTC))
    try:
        assert user_version(db) == SCHEMA_VERSION >= 24
        ada = s.for_account(V2_ADA)
        before = ada.passages()
        note, created = ada.add_note("br_aaaaaaaaaaaa", "# More\n\nA passage.\n")
        assert created and ada.passages() > before  # the triggers on spans still count
        assert ada.topic_page("br_aaaaaaaaaaaa", "c#") == "c"
        assert ada.topic_page("br_aaaaaaaaaaaa", "c++") is None  # merged: C#'s
        old = ["cap_000000000002", "cap_0000000000aa", "cap_000000000c03"]  # on no page
        assert [n.note_id for n in s.waiting_notes()] == [*old, note.id]
    finally:
        s.close()


def test_a_database_migrated_from_version_23_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v23_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)
    assert user_version(fresh) == user_version(migrated) == SCHEMA_VERSION


def test_a_failed_step_to_the_topic_keys_leaves_the_version_23_database_as_it_was(db, monkeypatch):
    v23 = v23_database(db)
    version = cairn.store.schema._TOPIC_KEYS
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v23["statements"] and user_version(db) == 23
    monkeypatch.undo()
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


def test_the_topic_tables_are_keyed_with_no_other_index(store, db):
    assert rows(
        db,
        "SELECT name FROM sqlite_master WHERE tbl_name IN ('page_keys', 'waiting') ORDER BY name",
    ) == [("page_keys",), ("sqlite_autoindex_page_keys_2",), ("waiting",)]
    assert rows(db, "SELECT name, pk FROM pragma_table_info('page_keys') ORDER BY cid") == [
        ("project_id", 1),
        ("slug", 2),
        ("topic_key", 0),
    ]
    assert rows(db, "SELECT name, pk FROM pragma_table_info('waiting') ORDER BY cid") == [
        ("note_id", 1),
        ("project_id", 0),
        ("tries", 0),
    ]


def test_a_project_has_a_kind_and_an_unknown_kind_writes_nothing(store, db):
    assert store.create_project("notes").kind == "general"
    before = dump(db)
    with pytest.raises(ValueError, match="kind"):
        store.create_project("other", kind="spreadsheet")
    assert dump(db) == before


def test_creating_a_project_records_its_kind(store):
    store.create_project("story-bible", kind="feature")
    (event,) = store.list_audit()
    assert (event.action, event.project, event.detail) == (
        "project.created",
        "story-bible",
        {"kind": "feature"},
    )


# R1: a transaction that fails, an unusable store, a disk that cannot be written, health


def _deny(*statements: str):
    def authorizer(action, arg1, *_):
        if action == sqlite3.SQLITE_TRANSACTION and arg1 in statements:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorizer


def _broken(store: Store) -> None:
    """Make `store` unusable the way the contract names: a COMMIT and its ROLLBACK both fail."""
    store._con.set_authorizer(_deny("COMMIT", "ROLLBACK"))
    with pytest.raises(cairn.store.StoreUnusable):
        store.create_project("beta")


def test_a_busy_begin_is_a_busy_error_and_the_store_goes_on(store, db):
    """A command beside the server holds the write lock: the BEGIN opened nothing, so there
    is nothing to roll back and the store is not made unusable."""
    store._con.execute("PRAGMA busy_timeout = 20")
    other = sqlite3.connect(db, isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError) as busy:
            store.create_project("alpha")
        assert not isinstance(busy.value, cairn.store.CannotWrite)
        other.execute("ROLLBACK")
    finally:
        other.close()
    assert store._con.in_transaction is False
    assert store.create_project("alpha").name == "alpha"
    assert store.health() == "ok"


def test_a_transaction_left_open_is_rolled_back_by_the_next_call(store, alpha):
    """Something began a transaction on the connection and left it: the next call's BEGIN
    fails, its handler rolls the leftover back, and the call after it works."""
    store._con.execute("BEGIN")
    store._con.execute("DELETE FROM projects")
    with pytest.raises(sqlite3.OperationalError):
        store.list_projects()
    assert store._con.in_transaction is False
    assert [b.name for b in store.list_projects()] == ["alpha"]


def test_a_view_made_before_the_store_broke_is_unusable_too(store):
    view = store.for_account(DEFAULT_ACCOUNT_ID)
    _broken(store)
    for call in (view.list_projects, lambda: view.create_project("gamma"), store.compact):
        with pytest.raises(cairn.store.StoreUnusable) as unusable:
            call()
        assert str(unusable.value) == cairn.store.UNUSABLE


def test_an_unusable_store_says_so_on_the_store_logger(store, caplog):
    with caplog.at_level("ERROR", logger="cairn.store"):
        _broken(store)
    assert [r.getMessage() for r in caplog.records if r.name == "cairn.store"] == [
        "a rollback failed: the store is unusable until it is opened again"
    ]


def test_closing_an_unusable_store_is_quiet(store):
    _broken(store)
    store.close()
    store.close()


class _Raising:
    """The store's connection, with every write's COMMIT raising `error` while armed."""

    def __init__(self, con, error):
        self._real, self.error, self.armed, self._writing = con, error, True, False

    def execute(self, sql, *args):
        if sql.startswith("BEGIN"):
            self._writing = sql == "BEGIN IMMEDIATE"
        if self.armed and self._writing and sql == "COMMIT":
            raise self.error
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _sqlite_error(kind, message: str, code: int | None):
    error = kind(message)
    if code is not None:
        error.sqlite_errorcode = code
        error.sqlite_errorname = "SQLITE_X"
    return error


@pytest.mark.parametrize(
    "error",
    [
        _sqlite_error(sqlite3.OperationalError, "database or disk is full", None),
        _sqlite_error(sqlite3.OperationalError, "disk I/O error", None),
        _sqlite_error(sqlite3.OperationalError, "attempt to write a readonly database", None),
        _sqlite_error(sqlite3.OperationalError, "whatever it says", sqlite3.SQLITE_FULL),
        _sqlite_error(sqlite3.OperationalError, "x", sqlite3.SQLITE_IOERR | (13 << 8)),
        _sqlite_error(sqlite3.OperationalError, "x", sqlite3.SQLITE_READONLY),
        _sqlite_error(sqlite3.OperationalError, "x", sqlite3.SQLITE_CANTOPEN),
    ],
    ids=["full", "ioerr", "readonly", "full-code", "ioerr-extended", "readonly-code", "cantopen"],
)
def test_a_write_the_disk_refuses_is_cannot_write_with_sqlites_code(store, db, error):
    real = store._con
    store._con = _Raising(real, error)
    with pytest.raises(cairn.store.CannotWrite) as refused:
        store.create_project("alpha")
    assert str(refused.value) == cairn.store.CANNOT_WRITE
    assert refused.value.__cause__ is error
    assert refused.value.sqlite_errorcode == getattr(error, "sqlite_errorcode", None)
    assert isinstance(refused.value, sqlite3.OperationalError)
    assert real.in_transaction is False
    store._con.armed = False
    assert store.create_project("alpha").name == "alpha"
    store._con = real


@pytest.mark.parametrize(
    "error",
    [
        _sqlite_error(sqlite3.OperationalError, "database is locked", sqlite3.SQLITE_BUSY),
        _sqlite_error(sqlite3.DatabaseError, "not authorized", sqlite3.SQLITE_AUTH),
        _sqlite_error(sqlite3.OperationalError, "something else", None),
    ],
    ids=["busy", "auth", "no-code"],
)
def test_any_other_failed_commit_goes_on_as_it_was(store, error):
    real = store._con
    store._con = _Raising(real, error)
    with pytest.raises(type(error)) as failed:
        store.create_project("alpha")
    assert failed.value is error and not isinstance(failed.value, cairn.store.CannotWrite)
    assert real.in_transaction is False
    store._con = real
    assert store.list_projects() == []


def test_a_read_is_never_turned_into_cannot_write(store):
    """Reads write nothing: their errors go on as SQLite raised them."""
    error = _sqlite_error(sqlite3.OperationalError, "database or disk is full", None)

    class ReadFails(_Raising):
        def execute(self, sql, *args):
            if sql == "BEGIN DEFERRED":
                raise self.error
            return self._real.execute(sql, *args)

    real = store._con
    store._con = ReadFails(real, error)
    with pytest.raises(sqlite3.OperationalError) as failed:
        store.list_projects()
    assert failed.value is error
    store._con = real


def test_health_is_ok_on_a_working_store_and_writes_nothing(store, db, alpha):
    before, changes = dump(db), store._con.total_changes
    assert [store.health() for _ in range(3)] == ["ok"] * 3
    assert dump(db) == before and store._con.total_changes == changes
    assert store._con.in_transaction is False
    assert store._con.execute("PRAGMA busy_timeout").fetchone() == (5000,)  # put back


def test_health_is_busy_while_this_process_holds_the_lock(store):
    with store._lock:
        assert store.health(wait=0.01) == "busy"
    assert store.health() == "ok"


def test_health_is_busy_while_another_connection_holds_the_file_and_waits_little(store, db):
    """Another connection holds the file exclusively: the read meets SQLITE_BUSY after the
    bound, not after the connection's own 5 seconds, and the 5 seconds are put back."""
    other = sqlite3.connect(db, isolation_level=None)
    try:
        other.execute("PRAGMA locking_mode = EXCLUSIVE")
        other.execute("BEGIN EXCLUSIVE")
        other.execute("CREATE TABLE held (x)")
        other.execute("COMMIT")  # an exclusive locking mode keeps the lock after the commit
        asked: list[str] = []
        store._con.set_trace_callback(asked.append)  # what SQLite was asked, not the clock
        try:
            assert store.health(wait=0.05) == "busy"
        finally:
            store._con.set_trace_callback(None)
        assert asked.index("PRAGMA busy_timeout = 50") < asked.index("PRAGMA user_version")
        assert "PRAGMA busy_timeout = 5000" not in asked[: asked.index("PRAGMA user_version")]
    finally:
        other.close()
    assert store._con.execute("PRAGMA busy_timeout").fetchone() == (5000,)
    assert store.health() == "ok"


def test_health_is_unavailable_closed_unusable_or_on_another_schema(db):
    closed = Store(db)
    closed.close()
    assert closed.health() == "unavailable"
    broken = Store(db)
    _broken(broken)
    assert broken.health() == "unavailable"
    broken.close()
    other = Store(db)
    try:
        con = sqlite3.connect(db)
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
        con.commit()
        con.close()
        assert other.health() == "unavailable"  # older is not this code's either
    finally:
        other.close()


def test_health_of_a_view_is_the_stores(store):
    view = store.for_account(DEFAULT_ACCOUNT_ID).narrow([], "read")
    assert view.health() == "ok"
    _broken(store)
    assert view.health() == "unavailable"
