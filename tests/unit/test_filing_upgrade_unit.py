"""N1 and the notes a workspace already has: the upgrade (schema step 24) and an import of an
archive written before N1.

- A note stored before the upgrade is still the same note when it is sent again, whatever
  topic or title it comes with: `unchanged`, as it was answered before, over REST, MCP and a
  source's run. The two cases that a topic key alone would turn into a conflict are pinned:
  a note on a page that merged two topics, and a note whose title was cut at 80 characters
  when it was stored. A note stored after the upgrade is filed once, as N1 says.

Every database is built from history: version 2's tables, then each step to 23, as cairn
wrote them, with made-up notes. The clock is in 2099."""

import asyncio
import hashlib
import io
import json
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from starlette.testclient import TestClient
from test_store_unit import V2_SCHEMA

from cairn import server
from cairn.compiler import GateResult, claim_id, provenance_gate, render_body, slugify
from cairn.server import create_app
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.spans import chunk
from cairn.store import DEFAULT_ACCOUNT_ID, Claim, Store
from cairn.store.schema import _MIGRATIONS, _SOURCE_ITEMS
from cairn.web import filing

Y2099 = datetime(2099, 3, 2, 9, 0, tzinfo=UTC)
BASE = "http://127.0.0.1:8765"
PROJECT = "br_0000000000b1"
LONG = (
    "Quarterly reconciliation of the harbour ledger for the eastern docks, with the late"
    " invoices from the barge operators"
)  # past 80 characters: before N1 a note kept its first 80 as its title
assert len(LONG) > 80

# id: (title as stored, topic, text, source_ref)
OLD_NOTES = {
    "cap_0000000000c1": ("C++", None, "- C++ keeps templates.\n", "doc:cpp"),
    "cap_0000000000c2": ("C#", None, "- C# keeps generics.\n", "doc:csharp"),
    "cap_0000000000d1": (LONG[:80], None, "- The ledger closes at noon.\n", "doc:ledger"),
}
OLD_PAGES = [  # slug, version, title, the notes its claims cite
    ("c", 1, "C++", ["cap_0000000000c1"]),
    ("c", 2, "C#", ["cap_0000000000c1", "cap_0000000000c2"]),
    (slugify(LONG[:80]), 1, LONG[:80], ["cap_0000000000d1"]),
]
# How each old note is sent again: its text and source_ref, with the topic or title its
# sender gives it now (a topic key alone would file each elsewhere).
RESENT = {
    "cap_0000000000c1": {"title": "C++"},  # its own title: "c++", its page's key is "c#"
    "cap_0000000000d1": {"title": LONG},  # the title as given, uncut
}


def stamp(n: int) -> str:
    return f"2026-10-01T08:{n:02d}:00.000000+00:00"


def history(db: Path, notes: dict, pages: list) -> None:
    """A database at version 23, the last before N1: version 2's tables and every step
    since, then a project `ops` of the default workspace holding `notes`, with `pages` as the
    compiler wrote them then (found by slug alone)."""
    spans = {note: chunk(note, text) for note, (_, _, text, _) in notes.items()}
    con = sqlite3.connect(db, isolation_level=None)
    try:
        con.executescript(V2_SCHEMA)
        con.execute("BEGIN")
        con.execute(
            "INSERT INTO accounts (id, name, created_at) VALUES (?, 'default', ?)",
            (DEFAULT_ACCOUNT_ID, stamp(0)),
        )
        for version in range(3, _SOURCE_ITEMS + 1):
            _MIGRATIONS[version](con)
        con.execute(
            "INSERT INTO projects (id, account_id, name, description, created_at, kind, maker_id,"
            " everyone) VALUES (?, ?, 'ops', '', ?, 'general', NULL, 'write')",
            (PROJECT, DEFAULT_ACCOUNT_ID, stamp(0)),
        )
        for n, (note, (title, topic, text, ref)) in enumerate(notes.items(), start=1):
            con.execute(
                "INSERT INTO notes (id, project_id, text, text_sha256, title, topic,"
                " source_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (note, PROJECT, text, hashlib.sha256(text.encode()).hexdigest(), title, topic,
                 ref, stamp(n)),
            )  # fmt: skip
            con.executemany(
                "INSERT INTO spans (id, note_id, ordinal, kind, heading_path, start_line,"
                " end_line, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (s.id, note, i, s.kind, json.dumps(list(s.heading_path)), s.start_line,
                     s.end_line, s.text)
                    for i, s in enumerate(spans[note])
                ],
            )  # fmt: skip
        for n, (slug, version, title, cited) in enumerate(pages, start=20):
            content = [s for note in cited for s in spans[note] if s.kind != "heading"]
            claims = [Claim(id=claim_id(s.id), text=s.text, span_ids=(s.id,)) for s in content]
            body = render_body(title, claims, {s.id: s for s in content})
            con.execute(
                "INSERT INTO page_versions (project_id, slug, version, title, body, claims,"
                " metrics, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (PROJECT, slug, version, title, body,
                 json.dumps([c.model_dump(mode="json") for c in claims]),
                 json.dumps({"claims": len(claims), "spans_cited": len(claims)}), stamp(n)),
            )  # fmt: skip
        con.execute(f"PRAGMA user_version = {_SOURCE_ITEMS}")
        con.execute("COMMIT")
    finally:
        con.close()


def kept(db: Path) -> list[str]:
    """Every row of the database but the audit log's: what a resend must leave as it was."""
    con = sqlite3.connect(db)
    try:
        return [line for line in con.iterdump() if not line.startswith('INSERT INTO "audit')]
    finally:
        con.close()


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cairn.db"


@pytest.fixture
def upgraded(db):
    history(db, OLD_NOTES, OLD_PAGES)
    store = Store(db, clock=lambda: Y2099)  # the upgrade
    yield store
    store.close()


def page_of(store: Store, note: str) -> str:
    """The page whose latest version cites `note`."""
    spans = {span.id for span in store.note_spans(note)}
    (slug,) = [
        page.slug
        for page in store.list_pages(PROJECT)
        if spans & {i for claim in page.claims for i in claim.span_ids}
    ]
    return slug


# 1. A note stored before the upgrade is the same note when it is sent again


def test_the_upgrade_marks_the_notes_it_finds_and_no_later_one(upgraded):
    assert all(upgraded.get_note(note).before_topics for note in OLD_NOTES)
    later, _ = upgraded.add_note(PROJECT, "- A note of today.\n", topic="Today")
    assert later.before_topics is False
    assert upgraded.get_note(later.id).before_topics is False


@pytest.mark.parametrize("note", sorted(RESENT))
def test_an_old_note_sent_again_over_rest_with_its_given_title_is_unchanged(upgraded, db, note):
    _, _, text, ref = OLD_NOTES[note]
    key = upgraded.create_key("t")
    slug = page_of(upgraded, note)
    before = kept(db)
    with TestClient(create_app(upgraded, public_url=BASE), base_url=BASE) as client:
        body = {"text": text, "source_ref": ref, **RESENT[note]}
        r = client.post("/api/projects/ops/notes", headers={"x-api-key": key}, json=body)
        assert r.status_code == 201, r.text
        answer = r.json()
        assert (answer["note_id"], answer["created"], answer["status"]) == (
            note,
            False,
            "unchanged",
        )
        assert answer["page"] == slug
        again = {**body, "topic": "Something else entirely"}  # any topic: the same note
        r = client.post("/api/projects/ops/notes", headers={"x-api-key": key}, json=again)
        assert r.status_code == 201 and r.json()["status"] == "unchanged", r.text
    assert kept(db) == before  # nothing written but the audit log's "unchanged"
    events = [e.action for e in upgraded.list_audit()]
    assert events.count("note.unchanged") == 2


@pytest.mark.parametrize("note", sorted(RESENT))
def test_an_old_note_sent_again_over_mcp_is_unchanged(upgraded, db, note):
    _, _, text, ref = OLD_NOTES[note]
    key = upgraded.create_key("t")
    before = kept(db)
    with TestClient(create_app(upgraded, public_url=BASE), base_url=BASE) as client:
        arguments = {"project": "ops", "text": text, "source_ref": ref, **RESENT[note]}
        call = {"name": "note", "arguments": arguments}
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": call}
        headers = {"x-api-key": key, "accept": "application/json, text/event-stream"}
        result = client.post("/mcp", headers=headers, json=body).json()["result"]
    assert result["isError"] is False, result
    answer = json.loads(result["content"][0]["text"])
    assert (answer["note_id"], answer["status"]) == (note, "unchanged")
    assert kept(db) == before


class Feed:
    answers: ClassVar[list[Pulled]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    async def pull(self, cursor):
        return Feed.answers.pop(0)


@pytest.fixture
def feed(monkeypatch, upgraded):
    def _open(name, config, **options):
        return Feed(name)

    monkeypatch.setitem(KINDS, "feed", SourceKind(check=dict, open=_open, via="feed"))
    Feed.answers = []
    upgraded.add_source("ops-feed", "feed", {"projects": ["ops"]})
    return upgraded


def test_an_old_note_a_source_reads_again_is_unchanged_not_skipped(feed, db):
    drafts = []
    for note in sorted(RESENT):
        _, _, text, ref = OLD_NOTES[note]
        title = RESENT[note]["title"]
        drafts.append(NoteDraft("ops", title, text, source_ref=ref, label="doc"))
    Feed.answers = [Pulled(drafts=drafts, cursor="c1")]
    report = asyncio.run(run_source(feed, "ops-feed", http=httpx.AsyncClient(), env={}))
    assert (report.unchanged, report.skipped, report.reasons) == (len(RESENT), 0, {})
    assert [c.id for c in feed.list_notes(PROJECT)] == list(OLD_NOTES)


def test_a_note_stored_after_the_upgrade_is_filed_once(upgraded):
    key = upgraded.create_key("t")
    with TestClient(create_app(upgraded, public_url=BASE), base_url=BASE) as client:
        h = {"x-api-key": key}
        first = {"text": "- C# keeps records.\n", "topic": "C#", "source_ref": "doc:new"}
        r = client.post("/api/projects/ops/notes", headers=h, json=first)
        assert r.status_code == 201 and r.json()["page"] == "c", r.text
        r = client.post("/api/projects/ops/notes", headers=h, json={**first, "topic": "C++"})
        assert r.status_code == 409 and "'c'" in r.json()["error"], r.text


# 2. The upgrade does not leave behind the notes the old code left behind

LEFT_NOTES = {
    "cap_0000000000e1": ("Relay", None, "- The relay rotates its keys weekly.\n", "doc:relay"),
    "cap_0000000000e2": ("Backups", None, "- Backups run at two.\n", "doc:backups"),  # stored
    "cap_0000000000e3": ("Vault", None, "- Zyqvorth opens the vault.\n", "doc:vault"),  # rejected
}
LEFT_PAGES = [("relay", 1, "Relay", ["cap_0000000000e1"])]  # the one cited note


def gate(page, spans):
    """The gate then and now: it rejects any page with a claim about Zyqvorth."""
    if any("Zyqvorth" in claim.text for claim in page.claims):
        return GateResult(passed=False, violations=["made.up: -: Zyqvorth is not allowed"])
    return provenance_gate(page, spans)


def one_pass(store: Store, monkeypatch) -> None:
    """The filer's start pass, as `serve` runs it, then nothing more (no clock)."""
    filer = create_app(store, public_url=BASE, gate=gate, file_every=3600).state.filer

    async def first_wait(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(filing.asyncio, "sleep", first_wait)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(filer.loop())
    monkeypatch.undo()


def listed(db: Path, capsys) -> list[str]:
    """The first word of each line `waiting` prints: a note's id, or "No" for none."""
    capsys.readouterr()
    assert server.main(["waiting", "--db", str(db)]) == 0
    return [line.split()[0] for line in capsys.readouterr().out.splitlines() if line.strip()]


def files_what_was_left(store: Store, db: Path, project: str, monkeypatch, capsys) -> None:
    """The never-compiled note and the rejected one wait; one pass files the first on its
    page, records the second as rejected again, and neither waits after it."""
    assert listed(db, capsys) == ["cap_0000000000e2", "cap_0000000000e3"]
    used = store.usage()
    one_pass(store, monkeypatch)
    assert listed(db, capsys) == ["No"]  # "No note is waiting for its page."
    on_pages = {i for page in store.list_pages(project) for c in page.claims for i in c.span_ids}
    for note, filed in [("cap_0000000000e2", True), ("cap_0000000000e3", False)]:
        spans = {s.id for s in store.note_spans(note) if s.kind != "heading"}
        assert bool(spans & on_pages) is filed, note
    assert store.get_page(project, "backups") is not None
    rejected = [e for e in store.list_audit() if e.action == "note.rejected"]
    assert len(rejected) == 1
    assert store.usage() == used  # nothing counted again


def test_the_upgrade_makes_the_notes_no_page_cites_wait_and_serve_files_them(
    db, monkeypatch, capsys
):
    history(db, LEFT_NOTES, LEFT_PAGES)
    store = Store(db, clock=lambda: Y2099)  # the upgrade
    try:
        files_what_was_left(store, db, PROJECT, monkeypatch, capsys)
    finally:
        store.close()


def older(archive: bytes) -> bytes:
    """The archive as cairn wrote one before N1: no topic key and no mark on any line."""
    source, out = zipfile.ZipFile(io.BytesIO(archive)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as rebuilt:
        for name in source.namelist():
            data = source.read(name)
            if name.endswith(".jsonl"):
                lines = [json.loads(line) for line in data.decode().splitlines()]
                for line in lines:
                    line.pop("topic_key", None)
                    line.pop("before_topics", None)
                data = "".join(json.dumps(line) + "\n" for line in lines).encode()
            rebuilt.writestr(name, data)
    return out.getvalue()


def exported_history(tmp_path) -> bytes:
    old = tmp_path / "old.db"
    history(old, LEFT_NOTES, LEFT_PAGES)
    source = Store(old, clock=lambda: Y2099)
    archive = io.BytesIO()
    try:
        source.export(archive)
    finally:
        source.close()
    return archive.getvalue()


def test_an_import_of_an_older_archive_makes_its_uncited_notes_wait(tmp_path, monkeypatch, capsys):
    archive = older(exported_history(tmp_path))
    db = tmp_path / "moved.db"
    store = Store(db, clock=lambda: Y2099)
    try:
        store.import_export(io.BytesIO(archive))
        project = store.get_project("ops").id
        assert {c.before_topics for c in store.list_notes(project)} == {True}
        files_what_was_left(store, db, project, monkeypatch, capsys)
    finally:
        store.close()


def test_an_import_of_an_archive_written_since_n1_makes_nothing_wait(tmp_path, capsys):
    """Such an archive does not say which notes waited (cairn.store.portable)."""
    archive = exported_history(tmp_path)
    db = tmp_path / "moved.db"
    store = Store(db, clock=lambda: Y2099)
    try:
        store.import_export(io.BytesIO(archive))
    finally:
        store.close()
    assert listed(db, capsys) == ["No"]
