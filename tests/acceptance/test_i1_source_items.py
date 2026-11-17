"""I1 acceptance tests: a source's item is written once, for as long as the source exists.

The contract
- A draft may name its item. `NoteDraft.item` (None by default) is the source's own stable
  name for the thing the draft is, 1 to 200 characters. A kind that sets it promises that two
  drafts of one source with the same item are the same thing. A draft without an item is
  written exactly as before; a kind that sets one says so in its module.
- Once. For a draft with an item, the runner does not write it when that source already
  wrote that item: it counts it as `unchanged` in the run's report, declines nothing and logs
  nothing for it. "Already wrote" means that `Memory.note` returned for that item before,
  with any status in which the note is stored: "saved", "unchanged" or "rejected". When
  nothing was stored (the guard refused it, it was malformed, its project is gone or not
  allowed, the workspace is over a cap, its text is forgotten, its page is full, it held a
  credential) the item is not recorded, and sent again it is judged again. The text does
  not matter: the same item with other text, another title or another link is not written
  again. The same item twice in one pull is written once.
- Per source. Two sources of one workspace with the same item each write it. A source
  switched off and on keeps its record. A source removed loses it, so one added again under
  the same name starts clean; a deleted workspace loses it too. Nothing of a removed source
  or a deleted workspace is left in the table.
- Where it lives. The store's table `source_items` (schema step 23): one row per source and
  item ever written, holding a digest of the item, never the item itself, and when it was
  written. The runner reads it once per pull for all of the pull's items, whatever their
  number, and records each item after its note returns, only while the run still holds the
  source's claim: a run another run took over records nothing.
- The window. Recording is a write of its own, after the note's. A run that dies between
  the two leaves the item unrecorded; the next run proposes it again, the note path answers
  "unchanged" for the same text, and it is recorded then. If the source sends other text in
  that window, a second note is written.
- What travels. A workspace's export carries no source, so it carries none of a source's
  items either: an import restores none, and a source of the same name in the workspace
  imported into writes the item.
- A database at schema 22 opens, gains the table empty, and its sources go on.
"""

import asyncio
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from cairn.compiler import GateResult
from cairn.gate import lint_gate
from cairn.guard import Finding
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.store import DEFAULT_ACCOUNT_ID, SOURCE_CLAIM_TTL, Store
from cairn.store.usage import Limits

STORE_AT = datetime(2099, 3, 1, 9, 0, tzinfo=UTC)
RUN_AT = datetime(2099, 3, 1, 9, 0, 30, tzinfo=UTC)
OTHER_TEXT = "Ledger line {n} was corrected to {m} units after the audit."


class Ledger:
    """A source whose pulls answer what the test queued, in order."""

    answers: ClassVar[list[Pulled]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    async def pull(self, cursor: str | None) -> Pulled:
        return Ledger.answers.pop(0)


def check(config):
    if not isinstance(config, dict) or not isinstance(config.get("projects"), list):
        raise ValueError("projects")
    return dict(config)


def opened(name, config, *, http, env, clock=None, sleep=None, deadline=None, since=None):
    return Ledger(name)


@pytest.fixture(autouse=True)
def ledger_kind(monkeypatch):
    monkeypatch.setitem(KINDS, "ledger", SourceKind(check=check, open=opened, via="ledger"))
    Ledger.answers = []


class World:
    def __init__(self, db: Path) -> None:
        self.db = db
        self.now = [STORE_AT]
        self.store = Store(db, clock=lambda: self.now[0])
        self.alpha = self.store.create_project("alpha")
        self.store.add_source("books", "ledger", {"projects": ["alpha"]})

    def notes(self, project: str = "alpha") -> list[str]:
        return [c.text for c in self.store.list_notes(self.store.get_project(project).id)]


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path / "cairn.db")
    yield w
    w.store.close()


def item(n: int) -> str:
    return f"ledger-entry-{n:04d}"


def entry(n: int, *, text: str | None = None, project: str = "alpha", **fields) -> NoteDraft:
    body = text if text is not None else f"Ledger line {n} reads {n * 7} units."
    return NoteDraft(
        project=project,
        title=fields.get("title", f"Entry {n}"),
        text=f"# Entry {n}\n\n{body}\n" if body.strip() else body,
        source_ref=fields.get("source_ref", f"https://ledger.example.test/e/{n}"),
        label="ledger",
        topic=f"entry-{n}",
        item=fields.get("item", item(n)),
    )


def plain(n: int, *, text: str | None = None) -> NoteDraft:
    """A draft with no item, as a kind that names none makes one."""
    body = text if text is not None else f"Ledger line {n} reads {n * 7} units."
    return NoteDraft(
        project="alpha",
        title=f"Entry {n}",
        text=f"# Entry {n}\n\n{body}\n",
        source_ref=f"https://ledger.example.test/e/{n}",
        label="ledger",
        topic=f"entry-{n}",
    )


def run(view, name="books", drafts=(), cursor="c", **kwargs):
    Ledger.answers.append(Pulled(drafts=list(drafts), cursor=cursor))
    return asyncio.run(
        run_source(view, name, http=httpx.AsyncClient(), env={}, clock=lambda: RUN_AT, **kwargs)
    )


def counts(report) -> tuple[int, ...]:
    """(saved, unchanged, refused, rejected, skipped, malformed, capped)"""
    return (
        report.saved, report.unchanged, report.refused, report.rejected, report.skipped,
        report.malformed, report.capped,
    )  # fmt: skip


def rows(db: Path, sql: str = "SELECT * FROM source_items ORDER BY 1, 2") -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# Once


def test_an_item_is_written_once_in_a_pull_in_later_pulls_and_after_off_and_on(world):
    report = run(world.store, drafts=[entry(1), entry(1, text=OTHER_TEXT.format(n=1, m=9)),
                                      entry(2)])  # fmt: skip
    assert counts(report) == (2, 1, 0, 0, 0, 0, 0)
    assert len(world.notes()) == 2 and len(rows(world.db)) == 2

    moved = entry(1, text=OTHER_TEXT.format(n=1, m=11), title="Entry one, corrected",
                  source_ref="https://ledger.example.test/moved/1")  # fmt: skip
    report = run(world.store, drafts=[moved])
    assert counts(report) == (0, 1, 0, 0, 0, 0, 0) and report.declined == []
    assert len(world.notes()) == 2

    world.store.disable_source("books")
    world.store.enable_source("books")
    report = run(world.store, drafts=[entry(1), entry(2), entry(3)])
    assert counts(report) == (1, 2, 0, 0, 0, 0, 0)
    assert len(world.notes()) == 3 and len(rows(world.db)) == 3
    assert world.store.get_source("books").last_report["unchanged"] == 2


def test_the_table_holds_a_digest_of_each_item_never_the_item(world):
    run(world.store, drafts=[entry(1), entry(2)])
    held = repr(rows(world.db))
    assert len(rows(world.db)) == 2
    assert item(1) not in held and item(2) not in held and "ledger-entry" not in held


def test_two_sources_with_one_item_each_write_it(world):
    world.store.create_project("beta")
    world.store.add_source("north", "ledger", {"projects": ["beta"]})
    assert counts(run(world.store, "books", [entry(1)])) == (1, 0, 0, 0, 0, 0, 0)
    assert counts(run(world.store, "north", [entry(1, project="beta")])) == (1, 0, 0, 0, 0, 0, 0)
    assert len(world.notes("alpha")) == len(world.notes("beta")) == 1
    assert len(rows(world.db)) == 2


# Nothing stored: not recorded, judged again


class RefuseWord:
    def __init__(self, word: str) -> None:
        self.word = word

    def check(self, text):
        found = self.word in text
        return [Finding(rule="test.word", kind="secret", line=1)] if found else []


def test_a_draft_the_guard_refused_is_not_recorded_and_is_written_once_accepted(world):
    held = entry(1, text="The Forbidden ledger line reads 7 units.")
    report = run(world.store, drafts=[held], guard=RefuseWord("Forbidden"))
    assert counts(report) == (0, 0, 1, 0, 0, 0, 0)
    assert rows(world.db) == [] and world.notes() == []

    assert counts(run(world.store, drafts=[held])) == (1, 0, 0, 0, 0, 0, 0)
    assert len(rows(world.db)) == 1 and len(world.notes()) == 1


def test_a_malformed_draft_is_not_recorded_and_is_written_once_whole(world):
    report = run(world.store, drafts=[entry(1, text="   \n")])
    assert counts(report) == (0, 0, 0, 0, 1, 1, 0)
    assert rows(world.db) == []

    assert counts(run(world.store, drafts=[entry(1)])) == (1, 0, 0, 0, 0, 0, 0)
    assert len(rows(world.db)) == 1 and len(world.notes()) == 1


def test_a_draft_over_the_workspaces_cap_is_not_recorded_and_is_written_later(world):
    report = run(world.store.limited(Limits(notes_per_month=1)), drafts=[entry(1), entry(2)])
    assert counts(report) == (1, 0, 0, 0, 0, 0, 1)
    assert len(rows(world.db)) == 1  # entry 1's item only

    assert counts(run(world.store, drafts=[entry(1), entry(2)])) == (1, 1, 0, 0, 0, 0, 0)
    assert len(rows(world.db)) == 2 and len(world.notes()) == 2


def test_a_draft_whose_project_is_gone_is_not_recorded_and_is_written_when_it_is_back(world):
    con = sqlite3.connect(world.db)
    try:  # the project disappears from under the source: its config still names it
        con.execute("DELETE FROM projects WHERE name = 'alpha'")
        con.commit()
    finally:
        con.close()
    report = run(world.store, drafts=[entry(1)])
    assert counts(report) == (0, 0, 0, 0, 1, 0, 0) and report.reasons == {"no such project": 1}
    assert rows(world.db) == []

    world.store.create_project("alpha")
    assert counts(run(world.store, drafts=[entry(1)])) == (1, 0, 0, 0, 0, 0, 0)
    assert len(rows(world.db)) == 1 and len(world.notes()) == 1


# Stored, though rejected: recorded


def reject_all(page, spans):
    return GateResult(passed=False, violations=["test.rejected: not on this page"])


def test_a_rejected_note_is_recorded_and_not_written_again(world):
    report = run(world.store, drafts=[entry(1)], gate=reject_all)
    assert counts(report) == (0, 0, 0, 1, 0, 0, 0)
    assert len(rows(world.db)) == 1 and len(world.notes()) == 1  # the note is stored

    again = run(world.store, drafts=[entry(1, text=OTHER_TEXT.format(n=1, m=3))])
    assert counts(again) == (0, 1, 0, 0, 0, 0, 0)
    assert len(world.notes()) == 1


# The window that is left, and a run that lost its claim


def run_that_dies_before_the_record(world: World, draft: NoteDraft) -> None:
    """A run whose process stops after the note returned and before its item is recorded."""

    def dying(self, claim, item):
        raise RuntimeError("the process stopped here")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Store, "record_source_item", dying)
        with pytest.raises(RuntimeError):
            run(world.store, drafts=[draft])


def test_a_run_that_dies_before_the_record_is_recorded_by_the_next_as_unchanged(world):
    run_that_dies_before_the_record(world, entry(1))
    assert len(world.notes()) == 1 and rows(world.db) == []
    assert world.store.get_source("books").cursor is None  # the cursor did not move

    assert counts(run(world.store, drafts=[entry(1)])) == (0, 1, 0, 0, 0, 0, 0)
    assert len(rows(world.db)) == 1 and len(world.notes()) == 1


def test_other_text_sent_in_that_window_is_a_second_note(world):
    """The window is not closed: pinned so that nobody reads the contract as if it were."""
    run_that_dies_before_the_record(world, entry(1))

    again = run(world.store, drafts=[entry(1, text=OTHER_TEXT.format(n=1, m=8))])
    assert counts(again) == (1, 0, 0, 0, 0, 0, 0)
    assert len(world.notes()) == 2 and len(rows(world.db)) == 1


def test_a_run_whose_claim_was_taken_during_a_note_records_nothing(world):
    taken = []

    def taken_meanwhile(page, spans):
        world.now[0] += SOURCE_CLAIM_TTL + timedelta(minutes=1)
        taken.append(world.store.claim_source("books"))  # another run, after the limit
        return lint_gate(page, spans)

    report = run(world.store, drafts=[entry(1), entry(2)], gate=taken_meanwhile)
    assert report.superseded and taken[0] is not None
    assert len(world.notes()) == 1 and rows(world.db) == []

    world.store.fail_source_run(taken[0], "a test claim")  # the other run lets go
    assert counts(run(world.store, drafts=[entry(1), entry(2)])) == (1, 1, 0, 0, 0, 0, 0)
    assert len(rows(world.db)) == 2


# Removing a source, deleting a workspace


def test_a_removed_source_leaves_no_row_and_one_added_again_starts_clean(world):
    run(world.store, drafts=[entry(1), entry(2)])
    assert len(rows(world.db)) == 2
    assert world.store.remove_source("books") is True
    assert rows(world.db) == []

    world.store.add_source("books", "ledger", {"projects": ["alpha"]})
    again = run(world.store, drafts=[entry(1, text=OTHER_TEXT.format(n=1, m=5))])
    assert counts(again) == (1, 0, 0, 0, 0, 0, 0)  # judged again: a second note
    assert len(world.notes()) == 3 and len(rows(world.db)) == 1


def test_a_deleted_workspace_leaves_no_row(world):
    run(world.store, drafts=[entry(1)])
    kept = rows(world.db)
    other = world.store.create_account("Tarn")
    view = world.store.for_account(other.id)
    view.create_project("alpha")
    view.add_source("books", "ledger", {"projects": ["alpha"]})
    run(view, drafts=[entry(1), entry(2)])
    assert len(rows(world.db)) == 3

    assert world.store.delete_account(other.id) is True
    assert rows(world.db) == kept


# Drafts without an item


def test_drafts_without_an_item_are_written_as_before_and_never_touch_the_table(world):
    seen: list[str] = []
    world.store._con.set_trace_callback(seen.append)
    try:
        first = run(world.store, drafts=[plain(1), plain(1), plain(2)])
        second = run(world.store, drafts=[plain(1, text=OTHER_TEXT.format(n=1, m=4))])
    finally:
        world.store._con.set_trace_callback(None)
    # As every run before items: the same text and link is "unchanged", other text a new note.
    assert counts(first) == (2, 1, 0, 0, 0, 0, 0)
    assert counts(second) == (1, 0, 0, 0, 0, 0, 0)
    assert len(world.notes()) == 3
    assert rows(world.db) == []
    assert [s for s in seen if "source_items" in s] == []


# One read per pull


def test_a_pull_of_300_items_reads_the_table_once(world):
    drafts = [entry(n) for n in range(1, 301)]
    for expected, inserts in (((300, 0), 300), ((0, 300), 0)):
        seen: list[str] = []
        world.store._con.set_trace_callback(seen.append)
        try:
            report = run(world.store, drafts=drafts)
        finally:
            world.store._con.set_trace_callback(None)
        on_table = [s.lstrip().upper() for s in seen if "source_items" in s]
        assert (report.saved, report.unchanged) == expected
        assert len([s for s in on_table if s.startswith("SELECT")]) == 1
        assert len([s for s in on_table if s.startswith("INSERT")]) == inserts
    assert len(rows(world.db)) == 300 and len(world.notes()) == 300


# What travels


def test_an_export_carries_no_item_and_an_import_restores_none(world, tmp_path):
    run(world.store, drafts=[entry(1)])
    out = io.BytesIO()
    world.store.export(out)
    archive = zipfile.ZipFile(io.BytesIO(out.getvalue()))
    digest = hashlib.sha256(item(1).encode()).hexdigest()[:16]
    for name in archive.namelist():
        data = archive.read(name)
        assert item(1).encode() not in data and digest.encode() not in data, name

    elsewhere = Store(tmp_path / "elsewhere.db", clock=lambda: STORE_AT)
    try:
        target = elsewhere.for_account(elsewhere.create_account("Fjord").id)
        target.import_export(io.BytesIO(out.getvalue()))
        assert rows(tmp_path / "elsewhere.db") == []
        target.add_source("books", "ledger", {"projects": ["alpha"]})
        report = run(target, drafts=[entry(1, text=OTHER_TEXT.format(n=1, m=2))])
        assert counts(report) == (1, 0, 0, 0, 0, 0, 0)
        assert len(rows(tmp_path / "elsewhere.db")) == 1
    finally:
        elsewhere.close()


# A database at schema 22


def normalized(sql: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", sql).split())


def schema_of(db: Path) -> list[str]:
    found = rows(
        db,
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
        " AND name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name",
    )
    return [normalized(sql) for (sql,) in found]


OLD_VERSION = 22  # the schema before source_items
OLD_AT = "2099-02-01T08:00:00.000000+00:00"
OLD_REPORT = {"saved": 2, "unchanged": 0, "refused": 0, "rejected": 0, "skipped": 0}


def schema_22_database(db: Path) -> None:
    """A database as cairn wrote it at schema 22, built from history: version 2's tables,
    then each migration step from 3 to 22, so that no later step changes what it is. Then,
    by SQL, the default workspace's project "alpha" and its source "books" of the test's kind:
    on, with a cursor and the report of a run that saved two notes. No note is written by
    SQL (a note, its spans and its page): the path of a note stored before its item was
    recorded is pinned by the test of a run that dies before the record."""
    from cairn.store.schema import _MIGRATIONS  # history: the steps from version 2 on

    con = sqlite3.connect(db, isolation_level=None)
    try:
        con.executescript(V2_TABLES)
        con.execute("BEGIN")
        con.execute(
            "INSERT INTO accounts (id, name, created_at) VALUES (?, 'default', ?)",
            (DEFAULT_ACCOUNT_ID, OLD_AT),
        )
        for version in range(3, OLD_VERSION + 1):
            _MIGRATIONS[version](con)
        con.execute(
            "INSERT INTO projects (id, account_id, name, description, created_at, kind, maker_id,"
            " everyone) VALUES ('br_00000000i122', ?, 'alpha', '', ?, 'general', NULL, 'write')",
            (DEFAULT_ACCOUNT_ID, OLD_AT),
        )
        con.execute(
            "INSERT INTO sources (account_id, name, kind, config, cursor, created_at,"
            " last_run_at, last_report, enabled, enabled_at)"
            " VALUES (?, 'books', 'ledger', ?, 'c22', ?, ?, ?, 1, ?)",
            (DEFAULT_ACCOUNT_ID, json.dumps({"projects": ["alpha"]}), OLD_AT, OLD_AT,
             json.dumps(OLD_REPORT), OLD_AT),
        )  # fmt: skip
        con.execute(f"PRAGMA user_version = {OLD_VERSION}")
        con.execute("COMMIT")
    finally:
        con.close()


def test_a_schema_22_database_with_a_source_opens_and_goes_on(tmp_path):
    db = tmp_path / "cairn.db"
    schema_22_database(db)
    statements = re.split(r";\n(?=CREATE |\Z)", SCHEMA_22.strip() + "\n")
    assert schema_of(db) == [normalized(s) for s in statements if s.strip()]  # what cairn wrote

    store = Store(db, clock=lambda: STORE_AT)
    try:
        assert rows(db, "PRAGMA user_version")[0][0] > OLD_VERSION
        assert rows(db, "SELECT name FROM sqlite_master WHERE name = 'source_items'") == [
            ("source_items",)
        ]
        assert rows(db) == []
        books = store.get_source("books")
        assert (books.enabled, books.cursor, books.last_report) == (True, "c22", OLD_REPORT)

        report = run(store, drafts=[entry(1), entry(2)])
        assert counts(report) == (2, 0, 0, 0, 0, 0, 0)
        assert len(rows(db)) == 2
        assert len(store.list_notes(store.get_project("alpha").id)) == 2
        again = run(store, drafts=[entry(1, text=OTHER_TEXT.format(n=1, m=6)), entry(2)])
        assert counts(again) == (0, 2, 0, 0, 0, 0, 0)  # recorded: not written again
    finally:
        store.close()


# Version 2's tables, as cairn wrote them, which every later step starts from.
V2_TABLES = """
CREATE TABLE accounts (
    seq INTEGER PRIMARY KEY,
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
    seq INTEGER PRIMARY KEY,
    digest TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX api_keys_by_label ON api_keys (account_id, label);
CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE notes (
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
CREATE INDEX notes_by_text ON notes (project_id, text_sha256);
CREATE TABLE spans (
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
CREATE TABLE page_versions (
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
"""


# The schema as cairn wrote it at version 22 (2026-11-14), from a fresh database's
# sqlite_master, ordered by name, with comments dropped and long lines wrapped: compared
# with white space collapsed.
SCHEMA_22 = """
CREATE TABLE account_disk (
    account_id TEXT PRIMARY KEY,
    passages INTEGER NOT NULL DEFAULT 0,
    history_bytes INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE TABLE account_quota (
    account_id TEXT PRIMARY KEY,
    plan TEXT,
    stored_bytes INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE TABLE accounts (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE api_keys (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    digest TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    projects TEXT,
    access TEXT NOT NULL CHECK (access IN ('write', 'read')),
    user_id TEXT
);
CREATE INDEX api_keys_by_label ON api_keys (account_id, label);
CREATE TABLE audit_events (
    seq INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    at TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('key', 'token', 'session', 'system')),
    actor TEXT,
    actor_id TEXT,
    action TEXT NOT NULL,
    project TEXT,
    detail TEXT NOT NULL
);
CREATE UNIQUE INDEX audit_events_by_number ON audit_events (account_id, n);
CREATE TABLE billing_messages (
    provider TEXT NOT NULL,
    id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (provider, id)
) WITHOUT ROWID;
CREATE INDEX billing_messages_by_account ON billing_messages (account_id);
CREATE TABLE forgotten (
    project_id TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    PRIMARY KEY (project_id, text_sha256)
) WITHOUT ROWID;
CREATE TABLE identities (
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    issuer TEXT NOT NULL,
    PRIMARY KEY (provider, subject)
);
CREATE INDEX identities_by_user ON identities (user_id);
CREATE TABLE invites (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    digest TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE (account_id, email)
);
CREATE TABLE memberships (
    seq INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    created_at TEXT NOT NULL,
    UNIQUE (account_id, user_id)
);
CREATE INDEX memberships_by_user ON memberships (user_id);
CREATE TABLE model_reservations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    period TEXT NOT NULL,
    micros INTEGER NOT NULL,
    reserved_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX model_reservations_by_account ON model_reservations (account_id);
CREATE TABLE notes (
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
CREATE INDEX notes_by_text ON notes (project_id, text_sha256);
CREATE TABLE oauth_clients (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,
    created_at TEXT NOT NULL,
    granted_at TEXT
);
CREATE TABLE oauth_codes (
    digest TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INTEGER NOT NULL,
    code_challenge TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    grant_id TEXT,
    user_id TEXT,
    projects TEXT,
    access TEXT NOT NULL CHECK (access IN ('write', 'read')),
    audience TEXT
);
CREATE TABLE oauth_grants (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    user_id TEXT,
    projects TEXT,
    access TEXT NOT NULL CHECK (access IN ('write', 'read')),
    audience TEXT
);
CREATE INDEX oauth_grants_by_account ON oauth_grants (account_id);
CREATE TABLE oauth_tokens (
    digest TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh', 'rotated')),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX oauth_tokens_by_grant ON oauth_tokens (grant_id);
CREATE TABLE page_versions (
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
CREATE TRIGGER passage_added AFTER INSERT ON spans WHEN ((new.kind IN ('paragraph',
    'bullet', 'code', 'table', 'quote')) + MAX(0, LENGTH(CAST(new.heading_path AS BLOB)) - 4
    * json_array_length(new.heading_path)) / 40) > 0
BEGIN
    INSERT INTO account_disk (account_id, passages)
    SELECT projects.account_id, ((new.kind IN ('paragraph', 'bullet', 'code', 'table',
        'quote')) + MAX(0, LENGTH(CAST(new.heading_path AS BLOB)) - 4 *
        json_array_length(new.heading_path)) / 40)
    FROM notes JOIN projects ON projects.id = notes.project_id
    WHERE notes.id = new.note_id
    ON CONFLICT (account_id) DO UPDATE SET passages = passages + excluded.passages;
END;
CREATE TRIGGER passage_removed AFTER DELETE ON spans
WHEN ((old.kind IN ('paragraph', 'bullet', 'code', 'table', 'quote')) + MAX(0,
    LENGTH(CAST(old.heading_path AS BLOB)) - 4 * json_array_length(old.heading_path)) / 40)
    > 0
BEGIN
    UPDATE account_disk SET passages = MAX(0, passages - ((old.kind IN ('paragraph',
        'bullet', 'code', 'table', 'quote')) + MAX(0, LENGTH(CAST(old.heading_path AS BLOB))
        - 4 * json_array_length(old.heading_path)) / 40))
    WHERE account_id = (SELECT projects.account_id FROM notes
    JOIN projects ON projects.id = notes.project_id WHERE notes.id = old.note_id);
END;
CREATE TABLE project_people (
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    access TEXT NOT NULL CHECK (access IN ('manage', 'write', 'read')),
    PRIMARY KEY (project_id, user_id)
) WITHOUT ROWID;
CREATE INDEX project_people_by_user ON project_people (user_id);
CREATE TABLE project_sets (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    project_ids TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (account_id, name)
);
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    maker_id TEXT,
    everyone TEXT NOT NULL CHECK (everyone IN ('write', 'read', 'none')),
    UNIQUE (account_id, name)
);
CREATE TABLE removals (
    digest TEXT PRIMARY KEY,
    account_id TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE search_claims (
    account_id TEXT NOT NULL,
    row INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    PRIMARY KEY (account_id, row)
) WITHOUT ROWID;
CREATE INDEX search_claims_by_page ON search_claims (account_id, project_id, slug);
CREATE TABLE search_projects (
    project_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    claims INTEGER NOT NULL,
    tokens INTEGER NOT NULL
);
CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    user_id TEXT,
    label TEXT,
    key_id TEXT,
    vouched_email TEXT
);
CREATE TABLE settings (
    account_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, name)
) WITHOUT ROWID;
CREATE TABLE sources (
    seq INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    kind TEXT NOT NULL,
    config TEXT NOT NULL,
    cursor TEXT,
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    last_report TEXT,
    last_error TEXT,
    claim TEXT,
    claimed_at TEXT,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    enabled_at TEXT,
    UNIQUE (account_id, name)
);
CREATE TABLE spans (
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
CREATE TABLE subscriptions (
    account_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    plan TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'past_due', 'canceled')),
    period_end TEXT,
    since TEXT NOT NULL,
    customer_ref TEXT,
    subscription_ref TEXT,
    started TEXT,
    past_due_since TEXT
) WITHOUT ROWID;
CREATE TABLE usage (
    account_id TEXT NOT NULL,
    period TEXT NOT NULL,
    notes INTEGER NOT NULL DEFAULT 0,
    note_bytes INTEGER NOT NULL DEFAULT 0,
    model_tokens_in INTEGER NOT NULL DEFAULT 0,
    model_tokens_out INTEGER NOT NULL DEFAULT 0,
    model_micros INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, period)
) WITHOUT ROWID;
CREATE TABLE users (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER version_added AFTER INSERT ON page_versions
BEGIN
    INSERT INTO account_disk (account_id, history_bytes)
    SELECT account_id, LENGTH(CAST(new.body AS BLOB)) + LENGTH(CAST(new.claims AS BLOB))
        FROM projects WHERE id = new.project_id
    ON CONFLICT (account_id) DO UPDATE SET
    history_bytes = history_bytes + excluded.history_bytes;
END;
CREATE TRIGGER version_removed AFTER DELETE ON page_versions
BEGIN
    UPDATE account_disk SET history_bytes = MAX(0, history_bytes - (LENGTH(CAST(old.body AS
        BLOB)) + LENGTH(CAST(old.claims AS BLOB))))
    WHERE account_id = (SELECT account_id FROM projects WHERE id = old.project_id);
END;
"""
