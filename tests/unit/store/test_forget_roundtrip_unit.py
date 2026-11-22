"""Forgetting, composing and rebuilding must leave versions the store can use again."""

import pytest

from cairn.arrange import tidy_page
from cairn.compiler import compile_note, extractive, render_body
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import claim_id
from cairn.store import Claim
from support.store.reviewer4_forget_roundtrip import assert_round_trip, versions
from support.store.reviewer4_forget_roundtrip import store as store


@pytest.mark.parametrize(
    ("texts", "forget"),
    [
        pytest.param(("A    \nB", "A\nB"), 0, id="seven-bytes-to-three"),
        pytest.param(("A    \nB", "A\nB"), 1, id="forget-second"),
        pytest.param(("A\nB", "A    \nB"), 0, id="short-filed-first"),
        pytest.param(("A   \nB", "A\nB"), 0, id="exactly-twice"),
        pytest.param(("A\t\nB", "A  \nB"), 0, id="surviving-spaces"),
    ],
)
def test_fold_survives_forget_and_restore(store, texts, forget):
    project = store.create_project("sedge")
    memory = Memory(store, lint_gate)
    # The archive also has history on a page untouched by erasure.
    for text in ("Wren keeps the map.", "Finch keeps the key."):
        assert memory.note("sedge", text, topic="Index")["status"] == "saved"
    filed = [memory.note("sedge", text, title="Ledger", topic="Ledger") for text in texts]
    assert [note["status"] for note in filed] == ["saved", "saved"]
    ids = [note["note_id"] for note in filed]
    spans = [store.note_spans(note_id)[0].id for note_id in ids]
    assert tidy_page(store, project.id, "ledger").status == "arranged"
    (fold,) = store.get_page(project.id, "ledger").claims
    assert set(fold.span_ids) == set(spans)

    assert memory.forget("sedge", ids[forget])["note_id"] == ids[forget]
    assert store.get_note(ids[forget]) is None
    assert_round_trip(store, project)

    kept = 1 - forget
    (claim,) = store.get_page(project.id, "ledger").claims
    assert (claim.text, claim.span_ids) == (texts[kept], (spans[kept],))
    for page in versions(store, project.id).values():
        for part in (page.title, page.body, *(c.text for c in page.claims)):
            assert texts[forget].encode() not in part.encode()
        for item in page.claims:
            assert spans[forget] not in item.span_ids
            cited = store.get_spans(item.span_ids)
            assert item.text in [span.text for span in cited.values()]


def test_forget_drops_excess_copies_and_keeps_independent_evidence(store):
    """An unrelated long note can put three exact copies under the version's byte allowance."""
    project = store.create_project("wren")
    memory = Memory(store, lint_gate)
    gone, kept, other = (
        store.add_note(project.id, text, topic="Ledger")[0]
        for text in ("Sedge keeps the long ledger in the loft.", "Plover roosts.", "Fin.")
    )
    one, two, anchor = (store.note_spans(note.id)[0] for note in (gone, kept, other))
    independent = Claim(id=claim_id(anchor.id), text=anchor.text, span_ids=(anchor.id,))
    copies = [Claim(id=f"cl_{n:016x}", text=two.text, span_ids=(two.id,)) for n in range(3)]
    claims = [Claim(id=claim_id(one.id), text=one.text, span_ids=(one.id,)), independent, *copies]
    spans = {span.id: span for span in (one, two, anchor)}
    store.save_page(project.id, "ledger", "Ledger", render_body("Ledger", claims, spans), claims)
    assert_round_trip(store, project)

    assert memory.forget("wren", gone.id)["note_id"] == gone.id
    assert store.get_note(gone.id) is None
    assert_round_trip(store, project)
    page = store.get_page(project.id, "ledger")
    assert independent in page.claims
    assert sum(two.id in c.span_ids for c in page.claims) <= 2
    for claim in page.claims:
        assert one.id not in claim.span_ids
        assert claim.text in [spans[i].text for i in claim.span_ids]


@pytest.mark.parametrize("collision", ["in-draft", "with-existing"])
def test_composer_id_collision_is_refused(store, collision):
    project = store.create_project("hazel")
    existing_id = None
    if collision == "with-existing":
        earlier, _ = store.add_note(project.id, "Sedge flowers.", topic="Ledger")
        assert compile_note(store, earlier).status == "saved"
        existing_id = store.get_page(project.id, "ledger").claims[0].id
    before = versions(store, project.id)
    note, _ = store.add_note(project.id, "Wren nests.", topic="Ledger")

    def compose(title, spans):
        draft = extractive(title, spans)
        (claim,) = draft.claims
        claims = (
            (claim, claim)
            if existing_id is None
            else (claim.model_copy(update={"id": existing_id}),)
        )
        return draft.model_copy(update={"claims": claims})

    result = compile_note(store, note, compose=compose)
    assert result.status == "rejected"
    assert result.gate and not result.gate.passed and any(result.gate.violations)
    assert versions(store, project.id) == before


@pytest.mark.parametrize("others", [1_998, 1_999])
def test_old_fold_at_the_page_limit(store, others):
    project = store.create_project("larch")
    texts = ["Larch  grows.", "Larch grows.", *(f"Twig {n}." for n in range(others))]
    note, _ = store.add_note(project.id, "\n\n".join(texts), topic="Ledger")
    passages = store.note_spans(note.id)
    assert [span.text for span in passages] == texts
    first, second, *rest = passages
    fold = Claim(id=claim_id(first.id), text=first.text, span_ids=(first.id, second.id))
    claims = [fold, *(Claim(id=claim_id(s.id), text=s.text, span_ids=(s.id,)) for s in rest)]
    body = render_body("Ledger", claims, {s.id: s for s in passages})
    before = store.save_page(project.id, "ledger", "Ledger", body, claims)

    if others == 1_999:
        other_outcomes = {"arranged", "unchanged", "gone", "stale", "failed"}
        assert tidy_page(store, project.id, "ledger").status not in other_outcomes
        assert store.get_page(project.id, "ledger") == before
        assert store.page_versions(project.id, "ledger") == [before.version]
    else:
        assert tidy_page(store, project.id, "ledger").status == "arranged"
        page = store.get_page(project.id, "ledger")
        assert page.version == before.version + 1
        assert [(c.text, c.span_ids) for c in page.claims] == [(s.text, (s.id,)) for s in passages]
        assert page.claims[0].id == fold.id and page.claims[2:] == tuple(claims[1:])
        assert len({c.id for c in page.claims}) == 2_000
        assert store.get_page(project.id, "ledger", before.version) == before
