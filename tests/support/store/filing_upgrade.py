"""Shared test helpers extracted from test_filing_upgrade_unit."""

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from cairn import server
from cairn.compiler import GateResult, claim_id, provenance_gate, render_body, slugify
from cairn.server import create_app
from cairn.spans import chunk
from cairn.store import DEFAULT_ACCOUNT_ID, Claim, Store
from cairn.store.schema import _MIGRATIONS, _SOURCE_ITEMS
from cairn.web import filing
from support.store.store import V2_SCHEMA

BASE = "http://127.0.0.1:8765"

PROJECT = "br_0000000000b1"

LONG = (
    "Quarterly reconciliation of the harbour ledger for the eastern docks, with the late"
    " invoices from the barge operators"
)  # past 80 characters: before N1 a note kept its first 80 as its title

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


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cairn.db"


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
