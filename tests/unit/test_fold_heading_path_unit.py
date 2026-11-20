"""A fold cites passages of one heading path, before it is saved as on import (second report,
finding 5a).

The import refuses a claim that folds passages under different headings
(store.portable._page_version), since the body shows a claim under one section only. A
model composer may answer one claim citing the same words under two headings; the gate a
composer is held to refuses it first, so what is saved always exports and imports.

Beside it, the import's other rule of a version that a composer could break (third report,
must-fix 1, the second route): no two claims of a version share an id. Every gate refuses a
page that holds one id twice, with a reason of its own (compile.claim_id_repeated), so no
compile saves one. The composer interface (`compile_note(..., compose=...)`) exists for the
evaluation script alone: `serve` compiles with the extractive composer, which gives each
passage one claim, and the shipped model parser drops a claim it already gave. Made-up
notes; every model is a FakeModel."""

import io
import json

import pytest

from cairn.arrange import tidy_page
from cairn.compiler import (
    CandidatePage,
    Draft,
    DraftStats,
    compile_note,
    extractive,
    provenance_gate,
    render_body,
)
from cairn.gate import lint_gate, strict_lint_gate, verbatim_gate
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


# No two claims of a version share an id (the second route of must-fix 1)

GATES = (provenance_gate, lint_gate, strict_lint_gate, verbatim_gate)
REPEATED = "compile.claim_id_repeated"


def twice(title, spans) -> Draft:
    """A composer that returns one extractive Claim twice for each passage."""
    claims = extractive(title, spans).claims
    return Draft(claims=(*claims, *claims), stats=DraftStats(composer="twice"))


def round_trips(store: Store, tmp_path) -> None:
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
    finally:
        target.close()


@pytest.mark.parametrize("gate", GATES, ids=lambda gate: gate.__name__)
def test_every_gate_refuses_one_id_held_twice_with_a_reason_of_its_own(gate):
    (span,) = chunk("n0", "Ada keeps port 80 open.")
    (claim,) = extractive("Net", [span]).claims
    spans = {span.id: span}
    doubled = (claim, claim)
    page = CandidatePage(
        slug="p", title="Net", body=render_body("Net", doubled, spans), claims=doubled
    )
    violations = gate(page, spans).violations
    assert [v for v in violations if v.startswith(REPEATED)] == [
        f"{REPEATED}: {claim.id}: 2 claims of the page hold this id: give each claim its own"
    ]
    assert all("port 80" not in v for v in violations)  # never the claim's text
    once = CandidatePage(
        slug="p", title="Net", body=render_body("Net", [claim], spans), claims=(claim,)
    )
    assert gate(once, spans).passed


@pytest.mark.parametrize("gate", (None, *GATES), ids=lambda gate: getattr(gate, "__name__", "none"))
def test_a_composer_that_returns_one_claim_twice_saves_nothing(store, tmp_path, gate):
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, "Ada keeps port 80 open.", title="Net")
    result = compile_note(store, note, gate, compose=twice)
    assert result.status == "rejected"
    assert [v.split(":")[0] for v in result.gate.violations] == [REPEATED]
    assert store.get_page(project.id, "net") is None
    round_trips(store, tmp_path)


def test_a_drafted_claim_with_the_id_of_a_claim_of_the_page_saves_nothing(store, tmp_path):
    """One step on: the ids of the draft are each held once, and one is a claim's the page
    already holds."""
    project = store.create_project("alpha")
    first, _ = store.add_note(project.id, "Ada keeps port 80 open.", title="Net")
    assert compile_note(store, first).status == "saved"
    before = store.get_page(project.id, "net")

    def taking(title, spans) -> Draft:
        (claim,) = extractive(title, spans).claims
        taken = claim.model_copy(update={"id": before.claims[0].id})
        return Draft(claims=(taken,), stats=DraftStats(composer="taking"))

    second, _ = store.add_note(project.id, "Brook closes port 22.", title="Net")
    result = compile_note(store, second, compose=taking)
    assert result.status == "rejected"
    assert [v.split(":")[0] for v in result.gate.violations] == [REPEATED]
    assert store.get_page(project.id, "net") == before
    assert store.page_versions(project.id, "net") == [before.version]
    round_trips(store, tmp_path)


def test_a_page_that_already_holds_one_id_twice_takes_no_note_and_no_tidy(store):
    """One step on again: the version was saved before the rule (built here at the store's
    level). A note to it is refused by the same reason, with the extractive composer too, and
    a tidy answers "invalid page", as before; forgetting a note of the page drops the later
    of the two claims (test_forget_import_check_unit)."""
    project = store.create_project("alpha")
    first, _ = store.add_note(project.id, "Ada keeps port 80 open.", title="Net")
    assert compile_note(store, first, compose=extractive).status == "saved"
    page = store.get_page(project.id, "net")
    doubled = (*page.claims, *page.claims)
    spans = store.get_spans(page.claims[0].span_ids)
    body = render_body("Net", doubled, spans)
    held = store.save_page(project.id, "net", "Net", body, doubled, expected_version=page.version)
    second, _ = store.add_note(project.id, "Brook closes port 22.", title="Net")
    result = compile_note(store, second)
    assert result.status == "rejected"
    assert [v.split(":")[0] for v in result.gate.violations] == [REPEATED]
    assert tidy_page(store, project.id, "net").status == "invalid page"
    assert store.get_page(project.id, "net") == held
