"""W3 acceptance tests (frozen): the lint gate between the compiler and the store.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract is in the docstring of src/cairn/gate.py.
"""

from pathlib import Path

import pytest

from cairn.compiler import CONTENT_KINDS, CandidatePage, compile_note, render_body
from cairn.gate import lint_gate
from cairn.store import Claim, Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RUNBOOK = (FIXTURES / "runbook_synthetic.md").read_text(encoding="utf-8")
SLUG = "quill-and-lumen-runbook"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def content_spans(store, text):
    project = store.get_project("alpha") or store.create_project("alpha")
    note, _ = store.add_note(project.id, text)
    return project, note, [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]


def single(claim: Claim, body: str | None = None, spans=None) -> CandidatePage:
    """A page of one claim; its body is what the compiler writes of it, unless one is given."""
    written = render_body("x", (claim,), spans or {})
    return CandidatePage(slug="x", title="x", body=body or written, claims=(claim,))


def test_baseline_pages_pass_with_full_scores(store):
    project, note, spans = content_spans(store, RUNBOOK)
    result = compile_note(store, note, gate=lint_gate)
    assert result.status == "saved", result.gate.violations
    page = store.get_page(project.id, SLUG)
    m = page.metrics
    assert (m["retention"], m["code_line_retention"], m["span_coverage"]) == (1.0, 1.0, 1.0)
    assert (m["claims"], m["spans_cited"], m["omission_rate"]) == (len(spans), len(spans), 0.0)


def test_a_claim_that_adds_an_ungrounded_flag_is_rejected(store):
    _, _, spans = content_spans(store, RUNBOOK)
    span = next(s for s in spans if "--quiet" in s.text)
    claim = Claim(id="cl_bad", text=span.text + " Add --force too.", span_ids=(span.id,))
    result = lint_gate(single(claim, spans={span.id: span}), {span.id: span})
    assert not result.passed
    assert result.violations == [result.violations[0]]
    assert result.violations[0].startswith("grounding.fact_not_in_cited_passages: cl_bad: ")


def test_a_page_that_drops_a_command_line_is_rejected(store):
    _, _, spans = content_spans(store, RUNBOOK)
    code = next(s for s in spans if s.kind == "code")
    first_command = next(line for line in code.text.splitlines() if line.startswith("quill run"))
    claim = Claim(id="cl_cut", text=first_command, span_ids=(code.id,))
    result = lint_gate(single(claim), {code.id: code})
    assert not result.passed
    assert any(v.startswith("retention.code_line_dropped") for v in result.violations)
    assert result.metrics["code_line_retention"] < 1.0


def test_a_citation_to_an_unknown_span_is_rejected(store):
    _, _, spans = content_spans(store, RUNBOOK)
    span = spans[0]
    claim = Claim(id="cl_x", text=span.text, span_ids=(span.id, "sp_0000000000000000"))
    result = lint_gate(single(claim), {span.id: span})
    assert any(v.startswith("provenance.unknown_passage: cl_x: ") for v in result.violations)
