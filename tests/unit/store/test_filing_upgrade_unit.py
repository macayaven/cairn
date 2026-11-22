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

from cairn.server import create_app
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.store import Store
from support.store.filing_upgrade import (
    BASE,
    LEFT_NOTES,
    LEFT_PAGES,
    LONG,
    OLD_NOTES,
    OLD_PAGES,
    PROJECT,
    files_what_was_left,
    history,
    listed,
)
from support.store.filing_upgrade import db as db

Y2099 = datetime(2099, 3, 2, 9, 0, tzinfo=UTC)
assert len(LONG) > 80

# id: (title as stored, topic, text, source_ref)
# How each old note is sent again: its text and source_ref, with the topic or title its
# sender gives it now (a topic key alone would file each elsewhere).
RESENT = {
    "cap_0000000000c1": {"title": "C++"},  # its own title: "c++", its page's key is "c#"
    "cap_0000000000d1": {"title": LONG},  # the title as given, uncut
}


def kept(db: Path) -> list[str]:
    """Every row of the database but the audit log's: what a resend must leave as it was."""
    con = sqlite3.connect(db)
    try:
        return [line for line in con.iterdump() if not line.startswith('INSERT INTO "audit')]
    finally:
        con.close()


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
