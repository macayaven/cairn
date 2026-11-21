"""Unit tests for the compiler's compose seam (T4): who drafts the claims for new spans."""

import pytest

from cairn.compiler import (
    CONTENT_KINDS,
    ComposeError,
    Draft,
    DraftStats,
    GateResult,
    candidate_page,
    claim_id,
    compile_note,
    extractive,
    provenance_gate,
    render_body,
)
from cairn.gate import lint_gate, strict_lint_gate
from cairn.spans import Span
from cairn.store import Claim, Store


def span(span_id: str, path: tuple[str, ...], text: str) -> Span:
    return Span(
        id=span_id,
        source_id="cap_000000000000",
        kind="paragraph",
        heading_path=path,
        start_line=1,
        end_line=1,
        text=text,
    )


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def project(store):
    return store.create_project("alpha")


def content(store, note):
    return [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]


def test_the_extractive_composer_makes_one_verbatim_claim_per_span():
    spans = [span("sp_1", ("T",), "one"), span("sp_2", ("T", "A"), "two")]
    draft = extractive("T", spans)
    assert draft.claims == tuple(
        Claim(id=claim_id(s.id), text=s.text, span_ids=(s.id,)) for s in spans
    )
    assert draft.stats == DraftStats(composer="extractive")
    assert (draft.stats.attempts, draft.stats.prompt_tokens, draft.stats.latency_s) == (0, None, 0)


def test_a_candidate_can_take_drafted_claims_for_its_new_spans():
    one, two = span("sp_1", ("T",), "one"), span("sp_2", ("T", "A"), "two")
    merged = Claim(id="cl_merged", text="one and two", span_ids=(one.id, two.id))
    page = candidate_page("t", "T", None, [one, two], claims=[merged])
    assert page.claims == (merged,)
    assert (
        page.body
        == render_body("T", [merged], {one.id: one, two.id: two})
        == ("# T\n\none and two\n")
    )


def test_the_default_compile_reports_its_draft_and_the_page_it_gated(store, project):
    note, _ = store.add_note(project.id, "# T\n\none\n\ntwo\n")
    result = compile_note(store, note)
    page = store.get_page(project.id, "t")
    assert result.draft == DraftStats(composer="extractive")
    assert (result.candidate.body, result.candidate.claims) == (page.body, page.claims)


def test_drafted_claims_follow_the_existing_ones_and_the_gate_sees_only_offered_spans(
    store, project
):
    first, _ = store.add_note(project.id, "# T\n\nold fact\n")
    compile_note(store, first)
    other_project = store.create_project("beta")
    stranger, _ = store.add_note(other_project.id, "# T\n\nforeign fact\n")
    second, _ = store.add_note(project.id, "# T\n\nnew one\n\nnew two\n")
    new = content(store, second)
    seen = {}

    def compose(title, spans):
        seen["offered"] = (title, [s.id for s in spans])
        claim = Claim(
            id="cl_00000000000000ae",
            text="new one and new two",
            span_ids=tuple(s.id for s in spans),
        )
        return Draft(claims=(claim,), stats=DraftStats(composer="test", attempts=1))

    def gate(page, spans):
        seen["gated"] = list(spans)
        return provenance_gate(page, spans)

    result = compile_note(store, second, gate, compose=compose)
    old = content(store, first)
    assert seen["offered"] == ("T", [s.id for s in new])
    assert seen["gated"] == [old[0].id, *(s.id for s in new)]
    assert content(store, stranger)[0].id not in seen["gated"]
    assert (result.status, result.version, result.draft.composer) == ("saved", 2, "test")
    assert [c.id for c in store.get_page(project.id, "t").claims] == [
        claim_id(old[0].id),
        "cl_00000000000000ae",
    ]


def test_a_compose_error_is_a_rejection_that_keeps_the_reason_and_saves_nothing(store, project):
    note, _ = store.add_note(project.id, "# T\n\none\n")
    stats = DraftStats(composer="m", attempts=3, prompt_tokens=90, completion_tokens=9)

    def compose(title, spans):
        raise ComposeError("compile.invalid_output", "no usable reply in 3 attempts", stats)

    result = compile_note(store, note, compose=compose)
    assert (result.status, result.version, result.candidate) == ("rejected", None, None)
    assert result.gate == GateResult(
        passed=False, violations=["compile.invalid_output: -: no usable reply in 3 attempts"]
    )
    assert result.draft == stats
    assert store.list_pages(project.id) == []


def test_nothing_new_never_calls_the_composer(store, project):
    note, _ = store.add_note(project.id, "# T\n\none\n")
    compile_note(store, note)

    def compose(title, spans):
        raise AssertionError("the composer was called with nothing to draft")

    result = compile_note(store, note, compose=compose)
    assert (result.status, result.version, result.draft, result.candidate) == (
        "unchanged",
        1,
        None,
        None,
    )


def test_a_compose_error_reads_as_a_violation():
    error = ComposeError("compile.provider_error", "HTTP 503", DraftStats(composer="m"))
    assert error.violation == "compile.provider_error: -: HTTP 503"
    assert str(error) == "compile.provider_error: -: HTTP 503"


def test_coverage_is_the_gates_job_so_the_caller_chooses_it(store, project):
    """compile_note runs the gate it is given: strict_lint_gate rejects a draft that leaves
    a new span uncited, and lint_gate saves it. A unit test pins this, not a frozen one, so
    enforcing coverage for every composer stays open."""
    note, _ = store.add_note(project.id, "# T\n\none\n\ntwo\n")
    one, _ = content(store, note)

    def compose(title, spans):
        claim = Claim(id=claim_id(one.id), text=one.text, span_ids=(one.id,))
        return Draft(claims=(claim,), stats=DraftStats(composer="test", attempts=1))

    strict = compile_note(store, note, strict_lint_gate, compose=compose)
    assert strict.status == "rejected"
    assert [v.split(": ", 1)[0] for v in strict.gate.violations] == ["coverage.passage_unaccounted"]
    assert compile_note(store, note, lint_gate, compose=compose).status == "saved"


# Only offered spans may be cited


def passing_gate(page, spans):
    return GateResult(passed=True, metrics={"claims": len(page.claims)})


def citing(*extra_ids):
    """A composer whose one claim cites every offered span plus `extra_ids`."""

    def compose(title, spans):
        ids = (*(s.id for s in spans), *extra_ids)
        claim = Claim(id="cl_drafted", text=" ".join(s.text for s in spans), span_ids=ids)
        return Draft(claims=(claim,), stats=DraftStats(composer="test", attempts=1))

    return compose


def test_a_drafted_citation_of_a_span_not_offered_is_rejected_whatever_the_gate_says(
    store, project
):
    first, _ = store.add_note(project.id, "# T\n\nold fact\n")
    compile_note(store, first)
    (old,) = content(store, first)
    second, _ = store.add_note(project.id, "# T\n\nnew fact\n")
    for extra in (old.id, "sp_0000000000000000"):  # on the page already, or nowhere
        result = compile_note(store, second, passing_gate, compose=citing(extra))
        assert result.status == "rejected"
        assert result.gate.violations == [
            f"provenance.passage_not_offered: cl_drafted: {extra} was not offered to the composer"
        ]
        assert result.gate.metrics == {"claims": 2} and result.candidate is not None
    assert store.page_versions(project.id, "t") == [1]


def test_a_gate_that_already_failed_reports_only_its_own_violations(store, project):
    """Ordering kept for T4's frozen tests: an offered-span violation is added only when the
    gate passed, so a rejection keeps exactly the gate's reasons."""
    first, _ = store.add_note(project.id, "# T\n\nold fact\n")
    compile_note(store, first)
    (old,) = content(store, first)
    second, _ = store.add_note(project.id, "# T\n\nnew fact\n")
    failing = lambda page, spans: GateResult(passed=False, violations=["test.no"])  # noqa: E731
    result = compile_note(store, second, failing, compose=citing(old.id))
    assert (result.status, result.gate.violations) == ("rejected", ["test.no"])


def test_existing_claims_are_not_held_to_the_offered_spans(store, project):
    first, _ = store.add_note(project.id, "# T\n\nold fact\n")
    compile_note(store, first)
    second, _ = store.add_note(project.id, "# T\n\nnew fact\n")
    assert compile_note(store, second, passing_gate, compose=citing()).status == "saved"


# The default gate


def test_any_composer_but_the_extractive_one_meets_the_verbatim_gate_by_default(store, project):
    note, _ = store.add_note(project.id, "# T\n\nThe service is safe.\n")
    (fact,) = content(store, note)

    def rewording(title, spans):
        claim = Claim(id="cl_00000000000000af", text="The service is unsafe.", span_ids=(fact.id,))
        return Draft(claims=(claim,), stats=DraftStats(composer="m", attempts=1))

    refused = compile_note(store, note, compose=rewording)
    assert refused.status == "rejected"
    assert [v.split(": ", 1)[0] for v in refused.gate.violations] == ["grounding.not_verbatim"]
    assert compile_note(store, note, lint_gate, compose=rewording).status == "saved"


def test_a_wrapped_extractive_composer_is_not_the_extractive_one(store, project):
    note, _ = store.add_note(project.id, "# T\n\none\n")
    result = compile_note(store, note, compose=lambda t, s: extractive(t, s))
    assert result.status == "saved" and "retention" in result.gate.metrics  # verbatim's metrics
    plain, _ = store.add_note(store.create_project("beta").id, "# T\n\none\n")
    assert "retention" not in compile_note(store, plain).gate.metrics  # provenance_gate's


# Usage


def test_usage_is_complete_by_default_and_for_the_extractive_draft(store, project):
    assert DraftStats(composer="x").usage_complete is True
    note, _ = store.add_note(project.id, "# T\n\none\n")
    assert compile_note(store, note).draft.usage_complete is True


def test_redrafts_are_complete_only_when_every_draft_was(store, project):
    note, _ = store.add_note(project.id, "# T\n\none\n\ntwo\n")
    one, _ = content(store, note)
    calls = []

    def compose(title, spans):
        calls.append(spans)
        if len(calls) == 1:
            claim = Claim(id=claim_id(one.id), text=one.text, span_ids=(one.id,))
            store.save_page(project.id, "t", "T", "# T\n\none\n", (claim,))
        stats = DraftStats(composer="m", attempts=1, usage_complete=len(calls) > 1)
        return Draft(claims=extractive(title, spans).claims, stats=stats)

    result = compile_note(store, note, passing_gate, compose=compose)
    assert (result.status, len(calls), result.draft.usage_complete) == ("saved", 2, False)
