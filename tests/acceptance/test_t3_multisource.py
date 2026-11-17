"""T3 acceptance tests (frozen): the in-memory lint core over spans from several sources.

Written by the orchestrator before implementation. Implementers must not edit this file.
A compiled page cites spans from many notes; `lint_pages` applies the T3 rules (see
tests/acceptance/test_t3_lint.py) to spans from any number of sources.
"""

from cairn.lint import ClaimInput, PageInput, lint_pages
from cairn.spans import chunk

A = chunk("cap_a", "# Alpha\n\nRun `alpha go` with --fast for 5 min.\n")
B = chunk("cap_b", "# Beta\n\nBeta needs 3 retries.\n")
SPANS = A + B
(A_TEXT,) = [s for s in A if s.kind == "paragraph"]
(B_TEXT,) = [s for s in B if s.kind == "paragraph"]


def page(*claims: ClaimInput) -> PageInput:
    body = "\n\n".join(c.text for c in claims)
    return PageInput(slug="combined", text=f"# Combined\n\n{body}\n", claims=list(claims))


def test_a_page_may_cite_spans_from_several_sources():
    report = lint_pages(
        [
            page(
                ClaimInput(id="c1", text=A_TEXT.text, span_ids=[A_TEXT.id]),
                ClaimInput(id="c2", text=B_TEXT.text, span_ids=[B_TEXT.id]),
            )
        ],
        SPANS,
    )
    assert report.violations == []
    assert report.modes == {"combined": "cairn"}
    assert report.metrics["cairn"]["passage_coverage"] == 1.0


def test_grounding_uses_only_the_cited_source():
    report = lint_pages(
        [
            page(
                ClaimInput(id="c1", text=A_TEXT.text, span_ids=[A_TEXT.id]),
                ClaimInput(id="c2", text="Beta needs 3 retries with --fast.", span_ids=[B_TEXT.id]),
            )
        ],
        SPANS,
    )
    assert [(f.code, f.claim_id) for f in report.violations] == [
        ("grounding.fact_not_in_cited_passages", "c2")
    ]


def test_unknown_spans_and_uncited_sources_are_violations():
    claim = ClaimInput(id="c1", text=A_TEXT.text, span_ids=[A_TEXT.id, "sp_0000000000000000"])
    codes = sorted(f.code for f in lint_pages([page(claim)], SPANS).violations)
    assert codes == ["coverage.passage_unaccounted", "provenance.unknown_passage"]
