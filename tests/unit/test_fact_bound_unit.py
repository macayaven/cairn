"""G1 item 2: a note holds at most cairn.store.FACTS_MAX facts (numbers, dates, versions,
paths, links, flags, inline code and code blocks: cairn.facts). A note with more is refused
before anything of it is stored, as a note past its headings' bound is: the store raises
TooManyFacts, Memory turns it into ManyFacts (a Malformed, 400), and REST, MCP, the composer
and a source's run answer it as they answer any Malformed note. The sentence gives the count
and the bound and holds nothing of the note.

The count is store.count_facts: what len(extract_facts(span)) gives, summed over the note's
spans, found by the same patterns in one pass each without building a Fact."""

import asyncio
import gc
import io
import random
import sys
import time
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from starlette.testclient import TestClient
from test_facts_lint_differential_unit import HOSTILE, document, emphasised, literals

import cairn.store.memory
from cairn import facts
from cairn.facts import extract_facts
from cairn.gate import lint_gate
from cairn.memory import Malformed, ManyFacts, Memory
from cairn.server import create_app
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.spans import Span, chunk
from cairn.store import FACTS_MAX, Store, TooManyFacts, count_facts

TESTS = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def numbers(n: int, first: int = 10_000) -> str:
    """A note of n number facts and nothing else that is a fact."""
    return "# Counts\n\nKingfisher " + " ".join(str(first + i) for i in range(n)) + "\n"


def span_of(text: str) -> Span:
    return Span(id="sp_x", source_id="s", kind="paragraph", heading_path=(), start_line=1,
                end_line=1, text=text)  # fmt: skip


# The count


def counted(text: str) -> None:
    spans = chunk("s", text)
    assert count_facts(spans) == sum(len(extract_facts(s)) for s in spans), text[:200]
    assert count_facts([span_of(text)]) == len(extract_facts(span_of(text))), text[:200]


def test_the_count_is_what_extract_facts_gives_for_every_input():
    for text in literals():
        counted(text)
    for path in sorted((TESTS / "fixtures").glob("*.md")):
        counted(path.read_text(encoding="utf-8"))
    for make in HOSTILE.values():
        for n in (0, 1, 7, 200):
            counted(make(n))
    rng = random.Random(20261119)
    for _ in range(2_000):
        counted(document(rng))
    for _ in range(1_000):  # underscores of emphasis by facts read as "*" (G1 item 5)
        counted(emphasised(rng))


def test_counting_builds_no_fact(store, monkeypatch):
    """The count reads the patterns' matches and keeps none: no Fact, no list of them."""

    def built(*args, **kwargs):
        raise AssertionError("a Fact was built")

    monkeypatch.setattr(facts, "_fact", built)
    assert count_facts(chunk("s", numbers(5_000))) == 5_000
    project = store.create_project("ops")
    with pytest.raises(TooManyFacts):
        store.add_note(project.id, numbers(FACTS_MAX + 1))


def test_counting_is_near_linear():
    """Eight times the facts take about eight times as long, not 64 (P1's cost tests)."""

    def best(spans):
        gc.collect()
        gc.disable()
        try:
            times = []
            for _ in range(5):
                started = time.perf_counter()
                count_facts(spans)
                times.append(time.perf_counter() - started)
            return min(times)
        finally:
            gc.enable()

    small, large = chunk("s", numbers(4_000)), chunk("s", numbers(32_000))
    assert best(large) < 24 * best(small)


# The refusal


def test_the_bound():
    assert FACTS_MAX == 30_000


def test_a_note_past_the_bound_is_refused_before_anything(store, monkeypatch):
    project = store.create_project("ops")
    screened = []
    monkeypatch.setattr(Store, "_screen", lambda self, fields: screened.append(1) or [])
    with pytest.raises(TooManyFacts) as many:
        store.add_note(project.id, numbers(FACTS_MAX + 1))
    said = str(many.value)
    assert f"{FACTS_MAX + 1:,} facts" in said and f"{FACTS_MAX:,}" in said
    assert "Kingfisher" not in said and "10000" not in said and "10,000" not in said
    assert screened == [] and store.list_notes(project.id) == []


def test_facts_across_passages_and_kinds_add_up(store):
    project = store.create_project("ops")
    half = FACTS_MAX // 2
    text = numbers(half) + "\n" + " ".join(f"`c{i}`" for i in range(half)) + "\n"
    store.add_note(project.id, text)  # exactly the bound: kept
    with pytest.raises(TooManyFacts):
        store.add_note(project.id, text + "\nOne more: 42.\n")


def test_memory_refuses_it_as_malformed(store):
    store.create_project("ops")
    with pytest.raises(ManyFacts) as many:
        Memory(store, lint_gate).note("ops", numbers(FACTS_MAX + 1))
    assert isinstance(many.value, Malformed) and many.value.status == 400
    assert many.value.reason == "facts past their bound"
    assert f"{FACTS_MAX:,}" in str(many.value)


def test_rest_mcp_and_the_composer_answer_it(tmp_path):
    store = Store(tmp_path / "web.db")
    try:
        key = store.create_key("laptop")
        store.create_project("ops")
        app = create_app(store, public_url=BASE)
        text = numbers(FACTS_MAX + 1)
        with TestClient(app, base_url=BASE) as client:
            rest = client.post(
                "/api/projects/ops/notes", headers={"x-api-key": key}, json={"text": text}
            )
            assert rest.status_code == 400 and f"{FACTS_MAX:,}" in rest.json()["error"]
            assert "Kingfisher" not in rest.json()["error"]
            call = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "note", "arguments": {"project": "ops", "text": text}},
            }
            headers = {**MCP_HEADERS, "x-api-key": key}
            answer = client.post("/mcp", headers=headers, json=call).json()["result"]
            assert answer["isError"] is True and f"{FACTS_MAX:,}" in answer["content"][0]["text"]
            client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            form = client.post("/b/ops/notes", data={"text": text}, headers=SAME_ORIGIN)
            assert form.status_code == 400 and f"{FACTS_MAX:,}" in form.text
            assert "Kingfisher" in form.text  # what was typed stays in the form
        assert store.list_notes(store.get_project("ops").id) == []
    finally:
        store.close()


class Feed:
    answers: ClassVar[list[Pulled]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    async def pull(self, cursor):
        return Feed.answers.pop(0)


def test_a_sources_run_skips_it_and_goes_on(store, monkeypatch):
    def _open(name, config, **options):
        return Feed(name)

    monkeypatch.setitem(KINDS, "feed", SourceKind(check=dict, open=_open, via="feed"))
    ops = store.create_project("ops")
    store.add_source("ops-feed", "feed", {"projects": ["ops"]})
    big = NoteDraft("ops", "Counts", numbers(FACTS_MAX + 1), source_ref="feed:1", label="feed")
    small = NoteDraft("ops", "Counts", numbers(3), source_ref="feed:2", label="feed")
    Feed.answers = [Pulled(drafts=[big, small], cursor="c1")]
    report = asyncio.run(run_source(store, "ops-feed", http=httpx.AsyncClient(), env={}))
    assert report.reasons == {"facts past their bound": 1} and report.malformed == 1
    assert report.saved == 1 and len(store.list_notes(ops.id)) == 1


def test_an_import_with_a_note_past_the_bound_imports_nothing(store, tmp_path, monkeypatch):
    """As for headings: an archive is refused for it, never cut. Its notes would otherwise be
    compiled when they wait for a page (N1)."""
    source = Store(tmp_path / "source.db")
    try:
        project = source.create_project("ops")
        monkeypatch.setattr(cairn.store.memory, "FACTS_MAX", 10**9)  # as an older build
        source.add_note(project.id, numbers(FACTS_MAX + 1))
        monkeypatch.undo()
        archive = io.BytesIO()
        source.export(archive)
    finally:
        source.close()
    target = store.for_account(store.create_account("Brook").id)
    with pytest.raises(ValueError, match="facts") as refused:
        target.import_export(archive)
    assert f"{FACTS_MAX:,}" in str(refused.value) and "Kingfisher" not in str(refused.value)
    assert target.list_projects() == []


def test_no_note_of_the_fixtures_or_the_gold_file_comes_near_the_bound():
    """The largest count among them, for the G1 report."""
    texts = [p.read_text(encoding="utf-8") for p in sorted((TESTS / "fixtures").glob("*"))]
    texts += (TESTS / "gold" / "t2_facts_synthetic.jsonl").read_text(encoding="utf-8").splitlines()
    largest = max(count_facts(chunk("s", text)) for text in texts if text.strip())
    print(f"\nG1: the largest fact count of a fixture or gold note is {largest}", file=sys.stderr)
    assert largest < FACTS_MAX // 10
