"""A fold cites passages of one heading path, before it is saved as on import (second report,
finding 5a).

The import refuses a claim that folds passages under different headings
(store.portable._page_version), since the body shows a claim under one section only. A
model composer may answer one claim citing the same words under two headings; the gate a
composer is held to refuses it first, so what is saved always exports and imports. Made-up
notes; every model is a FakeModel."""

import io
import json

import pytest

from cairn.compiler import compile_note
from cairn.gate import verbatim_gate
from cairn.llm import FakeModel, Reply
from cairn.llm_compiler import LLMComposer
from cairn.spans import chunk
from cairn.store import Claim, Store

NOTE = "## Ports\n\nAda keeps port 80 open.\n\n## Firewall\n\nAda keeps port 80 open."


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def folding_model(span_ids: list[str]) -> FakeModel:
    claim = {"text": "Ada keeps port 80 open.", "span_ids": span_ids}
    return FakeModel([Reply(text=json.dumps({"claims": [claim]}), prompt_tokens=9)] * 3)


def test_the_verbatim_gate_refuses_a_fold_under_two_headings():
    from cairn.compiler import CandidatePage, render_body

    spans = {s.id: s for s in chunk("n0", NOTE) if s.kind == "paragraph"}
    assert len({s.heading_path for s in spans.values()}) == 2
    claim = Claim(id="cl_00000000000000aa", text="Ada keeps port 80 open.", span_ids=tuple(spans))
    page = CandidatePage(
        slug="p", title="Net", body=render_body("Net", [claim], spans), claims=(claim,)
    )
    violations = verbatim_gate(page, spans).violations
    assert any(v.startswith(f"grounding.not_verbatim: {claim.id}:") for v in violations)
    assert all("port 80" not in v for v in violations)  # never the passage's text


def test_a_composed_fold_under_two_headings_is_not_saved_and_the_store_round_trips(store, tmp_path):
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, NOTE, title="Net")
    ids = [s.id for s in store.note_spans(note.id) if s.kind == "paragraph"]
    result = compile_note(store, note, compose=LLMComposer(folding_model(ids)))
    assert result.status == "rejected"
    assert store.get_page(project.id, "net") is None
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
    finally:
        target.close()


def test_a_composed_fold_under_one_heading_is_saved_and_round_trips(store, tmp_path):
    text = "## Ports\n\nAda keeps port 80 open.\n\nAda keeps port 80 open."
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, text, title="Net")
    ids = [s.id for s in store.note_spans(note.id) if s.kind == "paragraph"]
    assert len(ids) == 2
    result = compile_note(store, note, compose=LLMComposer(folding_model(ids)))
    assert result.status == "saved", result.gate.violations
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        page = target.get_page(target.get_project("alpha").id, "net")
        assert [c.text for c in page.claims] == ["Ada keeps port 80 open."]
    finally:
        target.close()
