"""A claim that survives a forgotten note still says what its remaining passages say (review
7.5): `kept_claims` keeps a claim that cited a forgotten passage only when its text is, by
verbatim_gate's own equality, the whole of every passage it still cites. Made-up notes."""

import pytest

from cairn.arrange import tidy_page
from cairn.compiler import CandidatePage, render_body
from cairn.gate import lint_gate, verbatim_gate
from cairn.memory import Memory
from cairn.spans import chunk
from cairn.store import Claim, Store
from cairn.store.erasure import kept_claims


def one(source: str, text: str):
    return chunk(source, text)[0]


def test_a_negating_passage_does_not_hold_up_the_claim_it_contains():
    gone, negating = (
        one("n1", "Enable feature flag X."),
        one("n3", "Never Enable feature flag X. in prod."),
    )
    claim = Claim(id="cl_test", text="Enable feature flag X.", span_ids=(gone.id, negating.id))
    assert kept_claims([claim], frozenset({gone.id}), {negating.id: negating}) == ()


def test_a_fold_of_the_same_passage_rewrapped_goes_with_the_note_whose_words_it_holds():
    """CORE5: a line break is not a space, so such a fold (one a tidy made before) is not the
    whole of the passage left: it goes, as any claim that says other words does."""
    gone, again = (
        one("n1", "Brook rotates the keys\nevery month."),
        one("n2", "Brook rotates the keys every month."),
    )
    claim = Claim(id="cl_fold", text=gone.text, span_ids=(gone.id, again.id))
    assert kept_claims([claim], frozenset({gone.id}), {again.id: again}) == ()
    same = one("n3", "Brook rotates the keys\nevery month.")
    fold = Claim(id="cl_fold", text=gone.text, span_ids=(gone.id, same.id))
    (kept,) = kept_claims([fold], frozenset({gone.id}), {same.id: same})
    assert (kept.text, kept.span_ids) == (gone.text, (same.id,))


def test_code_kept_by_the_gates_equality_not_by_containment():
    gone = one("n1", "```sh\nrestart queue   \n```")
    again = one("n2", "```sh\nrestart queue\n```")
    longer = one("n3", "```sh\nrestart queue\nrestart worker\n```")
    claim = Claim(id="cl_code", text=gone.text, span_ids=(gone.id, again.id))
    assert kept_claims([claim], frozenset({gone.id}), {again.id: again})[0].span_ids == (again.id,)
    cut = Claim(id="cl_cut", text="```sh\nrestart queue\n```", span_ids=(gone.id, longer.id))
    assert kept_claims([cut], frozenset({gone.id}), {longer.id: longer}) == ()


def test_a_claim_is_kept_only_when_every_passage_it_still_cites_says_it():
    gone, same, other = (
        one("n1", "Quill retries three times."),
        one("n2", "Quill retries three times."),
        one("n3", "Quill retries three times, then stops."),
    )
    claim = Claim(id="cl_x", text=gone.text, span_ids=(gone.id, same.id, other.id))
    spans = {same.id: same, other.id: other}
    assert kept_claims([claim], frozenset({gone.id}), spans) == ()


def test_a_claim_that_cites_no_forgotten_passage_is_kept_as_it_is():
    span = one("n1", "Ada owns the deploy.")
    claim = Claim(id="cl_y", text="Ada owns the deploy.", span_ids=(span.id,))
    assert kept_claims([claim], frozenset({"sp_gone"}), {span.id: span}) == (claim,)


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def test_a_page_left_after_forgetting_a_folded_note_passes_the_gate(store):
    store.create_project("alpha")
    memory = Memory(store, lint_gate)
    first = memory.note("alpha", "# Keys\n\nBrook rotates the keys every month.\n", title="Keys")
    memory.note(
        "alpha",
        "# Keys\n\nBrook rotates the keys\nevery month.\n",
        title="Keys",
        topic="Keys",
        source_ref="again",
    )
    project = store.get_project("alpha")
    assert tidy_page(store, project.id, first["page"]).merged == 0  # CORE5: two texts
    store.forget_note(first["note_id"])
    page = store.get_page(project.id, first["page"])
    spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
    candidate = CandidatePage(
        slug=page.slug,
        title=page.title,
        body=render_body(page.title, page.claims, spans),
        claims=page.claims,
    )
    assert [claim.text for claim in page.claims] == ["Brook rotates the keys\nevery month."]
    assert verbatim_gate(candidate, spans).passed
