"""H1 acceptance tests (frozen): accounts in the store.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstring of src/cairn/store.py; this is what it must say.

- Every project, API key and session belongs to an account. `Store(path)` is bound to the
  default account, id DEFAULT_ACCOUNT_ID ("acct_default"), which always exists; everything the
  v0 contract (tests/acceptance/test_w1_store.py) says still holds for it, unedited.
- `create_account(name) -> Account` (id "acct_" + 12 hex chars; the name is stripped, and a
  blank name or one over 80 characters is a ValueError). `get_account(id) -> Account | None`.
  `list_accounts()` is in creation order, the default account first.
- `for_account(id) -> Store` returns a view bound to that account over the same database
  (KeyError for an unknown account); `view.account_id` names it. A view is a drop-in for
  Store: every data method works on it, scoped to its account. Closing a view does nothing;
  closing the store that opened the database closes it.
- Scoping: through a view, another account's project, note, span or page id behaves exactly
  like an id that does not exist (None, [], {} or KeyError, as the v0 contract says for
  unknown ids). `search` only ever returns the view's own account's claims, and its scores
  depend only on that account's pages: ranking statistics are never shared between accounts.
  Project names are unique per account, not across accounts.
- Keys and sessions belong to the account of the view that created them. `check_key`,
  `revoke_key`, `check_session` and `end_session` only see the view's own account.
  `list_keys() -> list[KeyInfo]` (label, created_at; oldest first; never a digest).
  `resolve_key(key)` and `resolve_session(token)` look in every account and return a
  `Principal` (account_id, kind "key" or "session", label: the key's label, None for a
  session) or None; an expired session resolves to None.
- `delete_account(id) -> bool` removes the account and everything it owns (False for an
  unknown id, ValueError for the default account) and nothing of any other account.
- Reads never write, including the new ones.
- Schema: `PRAGMA user_version` holds SCHEMA_VERSION (>= 2). Opening a v0 database
  (user_version 0, v0's tables) migrates it in place: every project, key and session moves to
  the default account and nothing is lost. Opening a database whose version is higher than
  SCHEMA_VERSION raises SchemaTooNew and leaves the file untouched.
"""

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cairn.compiler import claim_id, compile_note
from cairn.spans import chunk
from cairn.store import (
    DEFAULT_ACCOUNT_ID,
    SCHEMA_VERSION,
    Account,
    KeyInfo,
    Principal,
    SchemaTooNew,
    Store,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RUNBOOK = (FIXTURES / "runbook_synthetic.md").read_text(encoding="utf-8")
SLUG = "quill-and-lumen-runbook"
FOLLOWUP = "# Quill and Lumen runbook\n\nQuill retries 3 times before failing.\n"
OTHER = "# Kestrel notes\n\nKestrel caches quill results for 90 seconds.\n"
CONTENT_KINDS = {"paragraph", "bullet", "code", "table", "quote"}

# The schema cairn v0 shipped (commit b521be8), frozen here: a v0 database has exactly these
# tables and `PRAGMA user_version` 0.
V0_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    digest TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS api_keys_by_label ON api_keys (label);
CREATE TABLE IF NOT EXISTS sessions (
    digest TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    seq INTEGER PRIMARY KEY,
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
CREATE TABLE IF NOT EXISTS spans (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL,
    heading_path TEXT NOT NULL,
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
    claims TEXT NOT NULL,
    metrics TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, slug, version)
);
CREATE VIRTUAL TABLE IF NOT EXISTS claim_search USING fts5 (
    project_id UNINDEXED, slug UNINDEXED, claim_id UNINDEXED, span_ids UNINDEXED, title, text
);
"""


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def user_version(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


@pytest.fixture
def two(store):
    """Two accounts, each with a project named "core" holding one compiled note."""
    a = store.for_account(store.create_account("Ada").id)
    b = store.for_account(store.create_account("Brook").id)
    out = {}
    for name, view, text in (("a", a, RUNBOOK), ("b", b, OTHER)):
        project = view.create_project("core", f"{name}'s core project")
        note, _ = view.add_note(project.id, text)
        result = compile_note(view, note)
        assert result.status == "saved"
        page = view.get_page(project.id, result.slug)
        out[name] = {"view": view, "project": project, "note": note, "page": page}
    return out


def test_the_default_account_always_exists_and_the_store_is_bound_to_it(store):
    assert DEFAULT_ACCOUNT_ID == "acct_default"
    assert store.account_id == DEFAULT_ACCOUNT_ID
    default = store.get_account(DEFAULT_ACCOUNT_ID)
    assert isinstance(default, Account) and default.id == DEFAULT_ACCOUNT_ID
    assert [a.id for a in store.list_accounts()] == [DEFAULT_ACCOUNT_ID]
    project = store.create_project("alpha")
    assert store.for_account(DEFAULT_ACCOUNT_ID).get_project("alpha") == project


def test_accounts_are_created_listed_and_named(store):
    ada = store.create_account("  Ada Lovelace ")
    brook = store.create_account("Brook")
    assert ada.id.startswith("acct_") and len(ada.id) == 17 and ada.id != brook.id
    assert ada.name == "Ada Lovelace" and ada.created_at
    assert store.get_account(ada.id) == ada
    assert store.get_account("acct_000000000000") is None
    assert [a.id for a in store.list_accounts()] == [DEFAULT_ACCOUNT_ID, ada.id, brook.id]
    assert store.create_account("Brook").id != brook.id  # names are labels, not identities
    for bad in ("", "   ", "x" * 81):
        with pytest.raises(ValueError):
            store.create_account(bad)


def test_a_view_is_bound_to_its_account_and_shares_the_database(store, db):
    ada = store.create_account("Ada")
    view = store.for_account(ada.id)
    assert view.account_id == ada.id
    with pytest.raises(KeyError):
        store.for_account("acct_000000000000")
    project = view.create_project("core")
    assert store.for_account(ada.id).get_project("core") == project  # a second view sees it
    assert view.for_account(DEFAULT_ACCOUNT_ID).account_id == DEFAULT_ACCOUNT_ID
    view.close()  # closing a view does nothing
    assert store.for_account(ada.id).get_project("core") == project
    assert store.list_projects() == []  # the default account holds nothing


def test_project_names_are_unique_per_account_not_across_accounts(two):
    a, b = two["a"], two["b"]
    assert a["project"].name == b["project"].name == "core" and a["project"].id != b["project"].id
    with pytest.raises(ValueError):
        a["view"].create_project("CORE")  # still unique within an account, ignoring case
    with pytest.raises(ValueError):
        a["view"].create_project("br_x")  # v0's naming rules still apply
    assert [x.name for x in a["view"].list_projects()] == ["core"]
    assert a["view"].get_project("core") == a["project"]
    assert b["view"].get_project("core") == b["project"]


def test_another_accounts_ids_behave_like_unknown_ids(two, db):
    a, b = two["a"], two["b"]
    view = b["view"]  # Brook's view, probing with Ada's ids
    project, note, page = a["project"], a["note"], a["page"]
    span_ids = [s.id for s in a["view"].note_spans(note.id)]
    assert span_ids and page.claims
    before = dump(db)

    assert view.get_project(project.id) is None
    assert view.get_note(note.id) is None
    assert view.list_notes(project.id) == []
    assert view.note_spans(note.id) == []
    assert view.get_spans(span_ids) == {}
    assert view.get_page(project.id, page.slug) is None
    assert view.get_page(project.id, page.slug, version=1) is None
    assert view.list_pages(project.id) == []
    assert view.page_versions(project.id, page.slug) == []
    assert view.search("quill lumen", project_ids=[project.id]) == []
    with pytest.raises(KeyError):
        view.add_note(project.id, "an intruder's note")
    with pytest.raises(KeyError):
        view.save_page(project.id, page.slug, "t", "b", page.claims)
    compile_note(view, note)  # compiling a foreign note through the view saves nothing
    assert dump(db) == before

    # Ada's own view still sees all of it.
    assert a["view"].get_note(note.id) == note
    assert set(a["view"].get_spans(span_ids)) == set(span_ids)
    assert a["view"].get_page(project.id, page.slug) == page


def test_search_never_crosses_accounts(two):
    a, b = two["a"], two["b"]
    # Both notes mention "quill"; each account only ever finds its own claims.
    hits_a = a["view"].search("quill")
    hits_b = b["view"].search("quill")
    assert hits_a and {h.project_id for h in hits_a} == {a["project"].id}
    assert hits_b and {h.project_id for h in hits_b} == {b["project"].id}
    assert all("Kestrel" not in h.text for h in hits_a)
    assert a["view"].search("kestrel") == []
    assert b["view"].search("lumen") == []
    assert a["view"].search("quill", project_ids=[a["project"].id, b["project"].id]) == hits_a


def test_search_scores_do_not_depend_on_other_accounts(two):
    """Ranking statistics are per account: a score must not reveal how often a word occurs
    in anyone else's notes."""
    a, b = two["a"], two["b"]
    before = a["view"].search("quill lumen")
    assert before
    for n in range(5):
        memo = f"# Quill memo {n}\n\nQuill and lumen appear again in memo {n}.\n"
        note, _ = b["view"].add_note(b["project"].id, memo)
        assert compile_note(b["view"], note).status == "saved"
    assert a["view"].search("quill lumen") == before  # same hits, same order, same scores


def test_the_same_note_in_two_accounts_is_two_notes(two):
    a, b = two["a"], two["b"]
    note, created = b["view"].add_note(b["project"].id, RUNBOOK)
    assert created and note.id != a["note"].id
    assert compile_note(b["view"], note).status == "saved"
    assert a["view"].get_page(a["project"].id, SLUG).version == 1  # Ada's page did not move
    assert b["view"].get_page(b["project"].id, SLUG).version == 1


def test_keys_belong_to_one_account(store):
    ada = store.for_account(store.create_account("Ada").id)
    brook = store.for_account(store.create_account("Brook").id)
    key_a, key_b = ada.create_key("laptop"), brook.create_key("laptop")
    assert ada.check_key(key_a) and not ada.check_key(key_b)
    assert brook.check_key(key_b) and not brook.check_key(key_a)
    assert not store.check_key(key_a)  # the default account holds neither

    who = store.resolve_key(key_a)
    assert who == Principal(account_id=ada.account_id, kind="key", label="laptop")
    assert brook.resolve_key(key_a) == who  # resolving looks in every account
    assert store.resolve_key("not-a-key") is None

    keys = ada.list_keys()
    assert [k.label for k in keys] == ["laptop"] and isinstance(keys[0], KeyInfo)
    assert keys[0].created_at and key_a not in json.dumps([k.model_dump() for k in keys])

    assert ada.revoke_key("laptop") is True  # only Ada's key of that label
    assert store.resolve_key(key_a) is None and ada.list_keys() == []
    assert brook.check_key(key_b) and store.resolve_key(key_b).account_id == brook.account_id
    assert ada.revoke_key("laptop") is False


def test_sessions_belong_to_one_account(store):
    ada = store.for_account(store.create_account("Ada").id)
    brook = store.for_account(store.create_account("Brook").id)
    token = ada.create_session()
    assert ada.check_session(token) and not brook.check_session(token)
    assert store.resolve_session(token) == Principal(
        account_id=ada.account_id, kind="session", label=None
    )
    assert store.resolve_session("not-a-token") is None
    assert store.resolve_session(ada.create_session(ttl_seconds=-1)) is None  # expired
    brook.end_session(token)  # another account cannot end it
    assert ada.check_session(token)
    ada.end_session(token)
    assert store.resolve_session(token) is None


def test_deleting_an_account_removes_everything_it_owns_and_nothing_else(two, store, db):
    a, b = two["a"], two["b"]
    key_a, key_b = a["view"].create_key("k"), b["view"].create_key("k")
    token_a = a["view"].create_session()
    kept = store.create_project("kept")  # in the default account
    span_ids = [s.id for s in a["view"].note_spans(a["note"].id)]
    ada_id = a["view"].account_id

    with pytest.raises(ValueError):
        store.delete_account(DEFAULT_ACCOUNT_ID)
    assert store.delete_account("acct_000000000000") is False
    assert store.delete_account(ada_id) is True

    assert store.get_account(ada_id) is None
    with pytest.raises(KeyError):
        store.for_account(ada_id)
    assert store.resolve_key(key_a) is None and store.resolve_session(token_a) is None
    text = dump(db)
    for gone in (a["project"].id, a["note"].id, span_ids[0], "Lumen", digest(key_a)):
        assert gone not in text  # no row of Ada's is left, the search index included

    assert store.get_project("kept") == kept
    assert b["view"].get_page(b["project"].id, b["page"].slug) == b["page"]
    assert b["view"].search("kestrel") and b["view"].check_key(key_b)
    assert [x.id for x in store.list_accounts()] == [DEFAULT_ACCOUNT_ID, b["view"].account_id]


def test_reads_never_write(two, store, db):
    a = two["a"]
    key, token = a["view"].create_key("k"), a["view"].create_session()
    before = dump(db)
    store.get_account(a["view"].account_id)
    store.list_accounts()
    view = store.for_account(a["view"].account_id)
    view.list_keys()
    view.list_projects()
    view.search("quill")
    store.resolve_key(key)
    store.resolve_session(token)
    store.resolve_key("nope")
    view.check_key(key)
    view.check_session(token)
    assert dump(db) == before


def test_views_are_safe_to_use_from_several_threads(store):
    views = [store.for_account(store.create_account(f"account {n}").id) for n in range(2)]
    projects = [view.create_project("core") for view in views]
    errors = []

    def work(view, project, worker):
        try:
            for n in range(10):
                note, _ = view.add_note(project.id, f"# Note {worker}-{n}\n\nfact {n}.\n")
                compile_note(view, note)
        except Exception as error:  # pragma: no cover - reported below
            errors.append(error)

    threads = [
        threading.Thread(target=work, args=(views[w % 2], projects[w % 2], w)) for w in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    for view, project in zip(views, projects, strict=True):
        assert len(view.list_notes(project.id)) == 20
        assert len(view.list_pages(project.id)) == 20
        assert {h.project_id for h in view.search("fact", limit=50)} == {project.id}


def test_accounts_work_in_memory():
    store = Store(":memory:")
    try:
        view = store.for_account(store.create_account("Ada").id)
        project = view.create_project("core")
        note, _ = view.add_note(project.id, RUNBOOK)
        assert compile_note(view, note).status == "saved"
        assert view.search("quill") and store.search("quill") == []
    finally:
        store.close()


def build_v0_database(db: Path) -> dict:
    """A database as cairn v0 wrote it: one project, a key, a live session, a compiled note."""
    now = datetime.now(UTC)
    key, token = "v0-key-for-the-migration-test", "v0-session-token"
    note_id = "cap_0123456789ab"
    spans = chunk(note_id, RUNBOOK)
    content = [s for s in spans if s.kind in CONTENT_KINDS]
    claims = [{"id": claim_id(s.id), "text": s.text, "span_ids": [s.id]} for s in content]
    title = "Quill and Lumen runbook"
    con = sqlite3.connect(db)
    try:
        con.executescript(V0_SCHEMA)
        con.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?)",
            ("br_0123456789ab", "Homelab", "the v0 project", iso(now)),
        )
        con.execute("INSERT INTO api_keys VALUES (?, ?, ?)", (digest(key), "laptop", iso(now)))
        con.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (digest(token), iso(now + timedelta(hours=1)), iso(now)),
        )
        con.execute(
            "INSERT INTO notes (id, project_id, text, text_sha256, title, topic, label,"
            " source_ref, via, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                note_id,
                "br_0123456789ab",
                RUNBOOK,
                hashlib.sha256(RUNBOOK.encode()).hexdigest(),
                title,
                None,
                None,
                "session:v0",
                "mcp",
                iso(now),
            ),
        )
        con.executemany(
            "INSERT INTO spans (id, note_id, ordinal, kind, heading_path, start_line,"
            " end_line, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (s.id, note_id, n, s.kind, json.dumps(s.heading_path), s.start_line,
                 s.end_line, s.text)
                for n, s in enumerate(spans)
            ],
        )  # fmt: skip
        con.execute(
            "INSERT INTO page_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("br_0123456789ab", SLUG, 1, title, f"# {title}\n", json.dumps(claims),
             json.dumps({"retention": 1.0}), iso(now)),
        )  # fmt: skip
        con.executemany(
            "INSERT INTO claim_search (project_id, slug, claim_id, span_ids, title, text)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("br_0123456789ab", SLUG, c["id"], json.dumps(c["span_ids"]), title, c["text"])
                for c in claims
            ],
        )
        con.commit()
    finally:
        con.close()
    assert user_version(db) == 0
    return {"key": key, "token": token, "note_id": note_id, "spans": spans, "claims": claims}


def test_a_v0_database_is_migrated_in_place_and_nothing_is_lost(db):
    v0 = build_v0_database(db)
    store = Store(db)
    try:
        assert SCHEMA_VERSION >= 2 and user_version(db) == SCHEMA_VERSION
        assert [a.id for a in store.list_accounts()] == [DEFAULT_ACCOUNT_ID]
        project = store.get_project("homelab")
        assert project is not None and project.id == "br_0123456789ab"
        assert project.description == "the v0 project"
        assert store.check_key(v0["key"])
        assert store.resolve_key(v0["key"]) == Principal(
            account_id=DEFAULT_ACCOUNT_ID, kind="key", label="laptop"
        )
        assert store.check_session(v0["token"])
        note = store.get_note(v0["note_id"])
        assert note.text == RUNBOOK and note.source_ref == "session:v0"
        assert store.note_spans(note.id) == v0["spans"]
        page = store.get_page(project.id, SLUG)
        assert page.version == 1 and page.metrics == {"retention": 1.0}
        assert [c.model_dump(mode="json") for c in page.claims] == v0["claims"]
        hits = store.search("quill")
        assert hits and {h.project for h in hits} == {"Homelab"}

        # The migrated database is a working one: a follow-up note adds version 2, and a
        # second account can reuse the project's name.
        followup, _ = store.add_note(project.id, FOLLOWUP)
        assert compile_note(store, followup).version == 2
        other = store.for_account(store.create_account("Ada").id)
        assert other.create_project("homelab").id != project.id
    finally:
        store.close()

    migrated = dump(db)
    Store(db).close()  # opening an up-to-date database changes nothing
    assert dump(db) == migrated and user_version(db) == SCHEMA_VERSION


def test_a_fresh_database_records_the_schema_version(db):
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION


def test_a_newer_schema_is_refused_and_left_untouched(db):
    Store(db).close()
    con = sqlite3.connect(db)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    con.close()
    before = dump(db)
    with pytest.raises(SchemaTooNew):
        Store(db)
    assert dump(db) == before and user_version(db) == SCHEMA_VERSION + 1
