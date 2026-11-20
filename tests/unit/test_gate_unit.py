"""Unit tests for cairn.gate (W3): the inner TDD loop under tests/acceptance/test_w3_gate.py.

Candidate pages are built by hand from chunk() of the synthetic runbook, the way the extractive
compiler builds them, so these tests need no store.
"""

from pathlib import Path

from cairn.compiler import CandidatePage, render_body
from cairn.gate import lint_gate
from cairn.spans import Span, chunk
from cairn.store import Claim

RUNBOOK = Path(__file__).resolve().parents[1] / "fixtures" / "runbook_synthetic.md"
TITLE = "Quill and Lumen runbook"
CONTENT = [
    s
    for s in chunk("cap_runbook", RUNBOOK.read_text(encoding="utf-8"))
    if s.kind not in ("heading", "frontmatter")
]
QUIET = next(s for s in CONTENT if "--quiet" in s.text)
TIMEOUT = next(s for s in CONTENT if "**Timeout:**" in s.text)


def verbatim(span: Span) -> Claim:
    return Claim(id="cl_" + span.id.removeprefix("sp_"), text=span.text, span_ids=(span.id,))


def baseline_body(spans: list[Span]) -> str:
    """The title as an H1, then each span's text, with an H2 wherever the section changes."""
    blocks, previous = [f"# {TITLE}"], None
    for span in spans:
        if span.heading_path != previous and span.heading_path[-1] != TITLE:
            blocks.append(f"## {span.heading_path[-1]}")
        blocks.append(span.text)
        previous = span.heading_path
    return "\n\n".join(blocks) + "\n"


def candidate(*claims: Claim, body: str | None = None) -> CandidatePage:
    if body is None:  # what the compiler writes of them: the only body a gate is given
        body = render_body(TITLE, claims, {s.id: s for s in CONTENT})
    return CandidatePage(slug="runbook", title=TITLE, body=body, claims=claims)


def prefixes(violations: list[str]) -> list[tuple[str, str]]:
    """(code, claim id or "-") of each "<code>: <claim_id or ->: <message>" string."""
    out = []
    for v in violations:
        code, claim, message = v.split(": ", 2)
        assert message
        out.append((code, claim))
    return out


def test_a_verbatim_baseline_page_passes_with_full_scores():
    page = candidate(*map(verbatim, CONTENT), body=baseline_body(CONTENT))
    result = lint_gate(page, {s.id: s for s in CONTENT})
    assert (result.passed, result.violations) == (True, [])
    assert result.metrics == {
        "claims": len(CONTENT),
        "spans_cited": len(CONTENT),
        "retention": 1.0,
        "code_line_retention": 1.0,
        "span_coverage": 1.0,
        "omission_rate": 0.0,
    }


def test_violations_read_code_then_claim_id_or_a_dash_then_message():
    claim = Claim(id="cl_q", text="Add --force too.", span_ids=(QUIET.id,))
    result = lint_gate(candidate(claim), {QUIET.id: QUIET})
    assert not result.passed
    assert prefixes(result.violations) == [
        ("grounding.fact_not_in_cited_passages", "cl_q"),
        ("retention.fact_dropped", "-"),
    ]
    assert result.metrics["retention"] == 0.0


def test_the_gate_lints_only_the_spans_its_claims_cite():
    result = lint_gate(candidate(verbatim(QUIET)), {QUIET.id: QUIET, TIMEOUT.id: TIMEOUT})
    assert (result.passed, result.violations) == (True, [])
    assert (result.metrics["retention"], result.metrics["span_coverage"]) == (1.0, 1.0)


def test_a_span_missing_from_the_mapping_is_unknown_and_still_counts_as_cited():
    claims = (
        Claim(id="a", text=QUIET.text, span_ids=(QUIET.id, QUIET.id)),
        Claim(id="b", text=TIMEOUT.text, span_ids=(TIMEOUT.id, "sp_0000000000000000")),
    )
    result = lint_gate(candidate(*claims), {QUIET.id: QUIET, TIMEOUT.id: TIMEOUT})
    assert prefixes(result.violations) == [("provenance.unknown_passage", "b")]
    assert (result.metrics["claims"], result.metrics["spans_cited"]) == (2, 3)


def test_a_page_without_claims_passes_with_the_empty_denominator_defaults():
    result = lint_gate(candidate(), {})
    assert result.passed
    assert result.metrics == {
        "claims": 0,
        "spans_cited": 0,
        "retention": 1.0,
        "code_line_retention": 1.0,
        "span_coverage": 1.0,
        "omission_rate": 0.0,
    }
