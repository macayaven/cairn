"""The list of a project's notes names a note's page only when that page cites it (second
review, should-fix 16). Two notes on one topic both see room on a nearly full page; one
fills it before the other saves; the other is refused (PageTooLong), stays stored and waits
no more. The list found "its" page by topic alone, a page with no claim of it, and the
Notes page said it was filed there. The page is now the one whose latest version cites a
passage of the note; a note no page cites and that is not waiting is not filed, and keeps
its forget link. The second note arrives between the first's room check and its save
through a patched call, not by timing. Made-up notes."""

import re
from html import unescape

import pytest
from starlette.testclient import TestClient

import cairn.memory as memory_module
import cairn.store.memory as store_memory
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.memory import Memory, PageFull
from cairn.server import create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
FIRST = "# Relay\n\nQuill rotates the relay at dawn.\n"
SECOND = "# Relay\n\nBrook checks the relay at noon.\n"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


@pytest.fixture
def raced(store, monkeypatch):
    """A page holds one claim; the second note checks for room on an empty page, then the
    first is filed, then the second saves and is refused: (the filed note, the refused)."""
    monkeypatch.setattr(store_memory, "PAGE_CLAIMS_MAX", 1)
    monkeypatch.setattr(memory_module, "PAGE_CLAIMS_MAX", 1)
    memory = Memory(store, lint_gate)
    store.create_project("ops")
    real = Memory._room_on_page
    done: list[dict] = []

    def room_then_the_other(self, *args, **kwargs):
        real(self, *args, **kwargs)
        if not done:
            done.append({})
            done[0] = self.note("ops", FIRST)

    monkeypatch.setattr(Memory, "_room_on_page", room_then_the_other)
    with pytest.raises(PageFull):
        memory.note("ops", SECOND)
    monkeypatch.setattr(Memory, "_room_on_page", real)
    (filed,) = done
    assert filed["status"] == "saved"
    project = store.get_project("ops")
    refused = next(n for n in store.list_notes(project.id) if n.id != filed["note_id"])
    return filed["note_id"], refused.id


def test_a_note_refused_by_a_full_page_is_not_shown_on_it(store, raced):
    filed, refused = raced
    heads = {h.id: h for h in store.notes_part(store.get_project("ops").id, 10)[0]}
    assert (heads[filed].waiting, heads[filed].page) == (False, "relay")
    assert (heads[refused].waiting, heads[refused].page) == (False, None)


def test_a_note_the_gate_rejected_is_not_shown_on_its_topics_page(store):
    """The neighbour: a note recorded as rejected for another reason, on a topic whose page
    another note made, is on no page either."""
    project = store.create_project("ops")
    first, _ = store.add_note(project.id, FIRST)
    assert compile_note(store, first, gate=lint_gate).status == "saved"
    second, _ = store.add_note(project.id, SECOND)
    store.record_note(project.id, "rejected", {"page": "relay"}, filed=second.id)
    heads = {h.id: h for h in store.notes_part(project.id, 10)[0]}
    assert heads[first.id].page == "relay"
    assert (heads[second.id].waiting, heads[second.id].page) == (False, None)


def test_the_notes_page_says_the_refused_note_is_on_no_page_and_keeps_forget(store, raced):
    filed, refused = raced
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        shown = client.get("/b/ops/notes")
    assert shown.status_code == 200
    rows = {
        note_id: " ".join(unescape(re.sub(r"<[^>]+>", " ", row)).split())
        for row in shown.text.split("<tr>")[2:]
        for note_id in (filed, refused)
        if f"/notes/{note_id}/forget" in row
    }
    assert (
        "/b/ops/p/relay"
        not in shown.text.split(f"/notes/{refused}/forget")[0].rsplit("<tr>", 1)[-1]
    )
    assert "On no page" in rows[refused]
    assert "relay" in rows[filed]


def test_the_part_stays_one_statement_and_limit_rows_with_pages_that_cite(store):
    """Counted again with the citation read: one statement a part, `limit` rows. The check of
    a row's page is a subquery inside that statement and returns no row of its own; it reads
    one page version's claims at most (a page holds PAGE_CLAIMS_MAX)."""
    project = store.create_project("ops")
    for n in range(12):
        made, _ = store.add_note(project.id, f"# Topic {n % 3}\n\nQuill line {n}.\n")
        compile_note(store, made, gate=lint_gate)
    seen: list[str] = []
    rows = [0]

    def counted(cursor, values):
        rows[0] += 1
        return values

    store.notes_part(project.id, 5)  # a reader opened before the observers are attached
    store._observe(trace=seen.append, rows=counted)
    try:
        part, _ = store.notes_part(project.id, 5)
    finally:
        store._observe(trace=None, rows=None)
    selects = [s for s in seen if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1 and rows[0] == 5
    assert all(head.page == f"topic-{head.title[-1]}" for head in part)


def test_a_note_whose_filing_ended_unchanged_is_still_on_its_page(store):
    """The page already cites the note: compiled again it is "unchanged", recorded as such,
    and the list still names the page."""
    project = store.create_project("ops")
    made, _ = store.add_note(project.id, FIRST)
    assert compile_note(store, made, gate=lint_gate).status == "saved"
    again = compile_note(store, made, gate=lint_gate)
    assert again.status == "unchanged"
    store.record_note(project.id, "unchanged", {"page": again.slug}, filed=made.id)
    (head,) = store.notes_part(project.id, 10)[0]
    assert (head.waiting, head.page) == (False, "relay")


def test_a_filed_note_is_still_on_its_page_after_export_and_import(store, tmp_path):
    import io

    project = store.create_project("ops")
    made, _ = store.add_note(project.id, FIRST)
    assert compile_note(store, made, gate=lint_gate).status == "saved"
    archive = io.BytesIO()
    store.export(archive)
    fresh = Store(tmp_path / "fresh.db")
    try:
        fresh.import_export(io.BytesIO(archive.getvalue()))
        (head,) = fresh.notes_part(fresh.get_project("ops").id, 10)[0]
    finally:
        fresh.close()
    assert (head.waiting, head.page) == (False, "relay")
