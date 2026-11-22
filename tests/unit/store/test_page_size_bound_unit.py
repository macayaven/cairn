"""G1 item 3: a page holds at most cairn.store.PAGE_CHARS_MAX characters of claims, beside
the PAGE_CLAIMS_MAX claims it held before. Every save of a page lints and indexes the whole
page again, so a page's size bounds what each note added to it costs.

A note that would take its page past either bound is refused before it is stored, by the same
check (Memory._room_on_page) and the same refusal (PageFull, 409), answered the same way over
REST, MCP, the composer and a source's run; its sentence says which bound was met. The store
refuses a note's save past it all the same (PageTooLong), when another note filled the page
meanwhile: the note is kept and its sentence says so. A page already larger when the bound
came stays as it is: it can still be read, forgotten from and arranged, and takes no more
notes, which its sentence says."""

import asyncio
from typing import ClassVar

import httpx
import pytest
from starlette.testclient import TestClient

import cairn.store.memory
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.memory import PAGE_FULL, Memory, PageFull
from cairn.server import create_app
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.store import PAGE_CHARS_MAX, PAGE_CLAIMS_MAX, Claim, PageTooLong, Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
WORDS = ("the", "ledger", "holds", "every", "entry", "once")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def passage(size: int, mark: str) -> str:
    """A passage of exactly `size` characters, of words and no fact, starting with `mark`."""
    text = (mark + " " + " ".join(WORDS * (size // 10 + 1)))[:size]
    return text[:-1] + "s" if text.endswith(" ") else text


def note(sizes: list[int], mark: str, topic: str = "Ledger") -> str:
    return f"# {topic}\n\n" + "\n\n".join(passage(s, f"{mark}{i}") for i, s in enumerate(sizes))


def chars_of(store: Store, project_id: str, slug: str = "ledger") -> int:
    return sum(len(claim.text) for claim in store.get_page(project_id, slug).claims)


QUARTER = PAGE_CHARS_MAX // 4


def test_the_bound():
    assert PAGE_CHARS_MAX == 400_000


def test_a_note_past_its_pages_characters_is_refused_before_it_is_stored(store):
    project = store.create_project("ops")
    memory = Memory(store, lint_gate)
    assert memory.note("ops", note([QUARTER] * 3, "a"))["status"] == "saved"
    rest = PAGE_CHARS_MAX - chars_of(store, project.id)
    assert memory.note("ops", note([rest], "b"))["status"] == "saved"  # exactly the bound
    assert chars_of(store, project.id) == PAGE_CHARS_MAX
    held = len(store.list_notes(project.id))
    with pytest.raises(PageFull) as full:
        memory.note("ops", "# Ledger\n\nKingfisher.\n")
    said = str(full.value)
    assert full.value.status == 409 and full.value.reason == PAGE_FULL
    assert f"{PAGE_CHARS_MAX:,} characters" in said and "ledger" in said and "topic" in said
    assert "claims a page holds" not in said  # which bound: the characters, not the claims
    assert "Kingfisher" not in said and "ledger holds" not in said  # nothing of the note
    assert len(store.list_notes(project.id)) == held
    assert memory.note("ops", note([rest], "b"))["status"] == "unchanged"  # a note it holds
    other = memory.note("ops", "# Ledger\n\nKingfisher.\n", topic="Ledger, second book")
    assert other["status"] == "saved"


def test_a_note_larger_than_any_page_says_to_split_it(store):
    store.create_project("ops")
    with pytest.raises(PageFull) as full:
        Memory(store, lint_gate).note("ops", note([QUARTER] * 4 + [10], "a"))
    said = str(full.value)
    assert "split" in said and f"{PAGE_CHARS_MAX:,} characters" in said
    assert full.value.status == 409


def test_the_claims_bound_is_said_as_before(store, monkeypatch):
    import cairn.memory

    monkeypatch.setattr(cairn.memory, "PAGE_CLAIMS_MAX", 2)
    store.create_project("ops")
    memory = Memory(store, lint_gate)
    memory.note("ops", note([10, 10], "a"))
    with pytest.raises(PageFull) as full:
        memory.note("ops", note([10], "b"))
    assert "claims" in str(full.value) and "characters" not in str(full.value)


def test_the_store_saves_no_note_past_its_pages_characters(store):
    project = store.create_project("ops")
    claims = [Claim(id=f"c{i}", text="x" * QUARTER, span_ids=()) for i in range(4)]
    claims.append(Claim(id="c4", text="y", span_ids=()))
    with pytest.raises(PageTooLong) as long:
        store.save_page(project.id, "ledger", "Ledger", "# Ledger\n", claims)
    assert long.value.chars == PAGE_CHARS_MAX + 1
    assert f"{PAGE_CHARS_MAX + 1:,} characters" in str(long.value)
    assert store.get_page(project.id, "ledger") is None
    store.save_page(project.id, "ledger", "Ledger", "# Ledger\n", claims[:-1])  # exactly the bound


def test_a_page_filled_meanwhile_keeps_the_note_and_says_which_bound(store, monkeypatch):
    project = store.create_project("ops")
    memory = Memory(store, lint_gate)
    memory.note("ops", note([QUARTER] * 4, "a"))
    monkeypatch.setattr(Memory, "_room_on_page", lambda self, *args: None)  # a race lost
    with pytest.raises(PageFull) as full:
        memory.note("ops", "# Ledger\n\nKingfisher.\n")
    said = str(full.value)
    assert full.value.status == 409 and said.startswith("The note was kept")
    assert f"{PAGE_CHARS_MAX:,} characters" in said and "Kingfisher" not in said
    assert len(store.list_notes(project.id)) == 2
    assert chars_of(store, project.id) == PAGE_CHARS_MAX


def larger_page(store: Store, monkeypatch) -> tuple[str, str]:
    """A page past the bound, as saved before it (the bound raised for that save): the project's
    id, and the id of the note that made it."""
    project = store.create_project("ops")
    monkeypatch.setattr(cairn.store.memory, "PAGE_CHARS_MAX", 10 * PAGE_CHARS_MAX)
    big, _ = store.add_note(project.id, note([QUARTER] * 5, "a"))
    assert compile_note(store, big, lint_gate).status == "saved"
    small, _ = store.add_note(project.id, "# Ledger\n\nA short line.\n")
    assert compile_note(store, small, lint_gate).status == "saved"
    monkeypatch.undo()
    assert chars_of(store, project.id) > PAGE_CHARS_MAX
    return project.id, small.id


def test_a_page_larger_already_stays_and_takes_no_more_notes(store, monkeypatch):
    project_id, small_id = larger_page(store, monkeypatch)
    before = store.get_page(project_id, "ledger")
    with pytest.raises(PageFull) as full:
        Memory(store, lint_gate).note("ops", "# Ledger\n\nKingfisher.\n")
    said = str(full.value)
    assert f"{before_chars(before):,} characters" in said and f"{PAGE_CHARS_MAX:,}" in said
    assert "stays as it is" in said and "takes no more notes" in said
    assert store.get_page(project_id, "ledger").version == before.version
    # It is still read, and a note can still be forgotten from it: the rebuilt page is saved.
    forgotten = store.forget_note(small_id)
    assert list(forgotten.rebuilt) == ["ledger"]
    assert store.get_page(project_id, "ledger").version == before.version + 1


def before_chars(page) -> int:
    return sum(len(claim.text) for claim in page.claims)


def test_a_page_larger_already_can_still_be_arranged(store, monkeypatch):
    """Arranging reorders and folds a page's claims, never adds one: its save is not a
    note's, and the bound does not refuse it."""
    project_id, _ = larger_page(store, monkeypatch)
    page = store.get_page(project_id, "ledger")
    claims = list(reversed(page.claims))
    saved = store.save_page(
        project_id,
        "ledger",
        page.title,
        page.body,
        claims,
        expected_version=page.version,
        event="page.arranged",
        detail={"from_version": page.version, "model": "tidy", "moved": 1, "merged": 0},
    )
    assert saved.version == page.version + 1


def test_rest_mcp_and_the_composer_answer_it(tmp_path):
    store = Store(tmp_path / "web.db")
    try:
        key = store.create_key("laptop")
        project = store.create_project("ops")
        Memory(store, lint_gate).note("ops", note([QUARTER] * 4, "a"))
        text = "# Ledger\n\nOne more line, kingfisher.\n"
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            rest = client.post(
                "/api/projects/ops/notes", headers={"x-api-key": key}, json={"text": text}
            )
            said = rest.json()["error"]
            assert rest.status_code == 409 and f"{PAGE_CHARS_MAX:,} characters" in said
            assert "kingfisher" not in said
            call = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "note", "arguments": {"project": "ops", "text": text}},
            }
            headers = {**MCP_HEADERS, "x-api-key": key}
            answer = client.post("/mcp", headers=headers, json=call).json()["result"]
            assert answer["isError"] is True
            assert f"{PAGE_CHARS_MAX:,} characters" in answer["content"][0]["text"]
            client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            form = client.post("/b/ops/notes", data={"text": text}, headers=SAME_ORIGIN)
            assert form.status_code == 409 and f"{PAGE_CHARS_MAX:,} characters" in form.text
            assert "kingfisher" in form.text  # what was typed stays in the form
        assert len(store.list_notes(project.id)) == 1
    finally:
        store.close()


class Feed:
    answers: ClassVar[list[Pulled]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    async def pull(self, cursor):
        return Feed.answers.pop(0)


def test_a_sources_run_skips_it_as_a_full_page(store, monkeypatch):
    def _open(name, config, **options):
        return Feed(name)

    monkeypatch.setitem(KINDS, "feed", SourceKind(check=dict, open=_open, via="feed"))
    ops = store.create_project("ops")
    Memory(store, lint_gate).note("ops", note([QUARTER] * 4, "a"))
    store.add_source("ops-feed", "feed", {"projects": ["ops"]})
    full = NoteDraft("ops", "Ledger", "One more line.\n", source_ref="feed:1", label="feed")
    other = NoteDraft("ops", "Other", "One more line.\n", source_ref="feed:2", label="feed")
    Feed.answers = [Pulled(drafts=[full, other], cursor="c1")]
    report = asyncio.run(run_source(store, "ops-feed", http=httpx.AsyncClient(), env={}))
    assert report.reasons == {PAGE_FULL: 1} and report.saved == 1
    assert len(store.list_notes(ops.id)) == 2


def test_the_claims_bound_is_unchanged():
    assert PAGE_CLAIMS_MAX == 2_000
