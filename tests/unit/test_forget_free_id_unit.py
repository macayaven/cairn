"""A claim that outlives a forgotten note never takes an id another claim of its page holds
(fourth report, must-fix 5).

A fold whose id was made from the forgotten passage takes another: the id of the first
passage it keeps, and when a claim holds that one, an id drawn from its place and passages.
The draw was made once and not looked at, so a page that held it too (an archive an import
took, or a composer's page: any id of cairn's shape is accepted) ended with one id on two
claims, and the import's check then dropped the later of them, a claim of a note that was
never forgotten. No test here knows how an id is drawn: each page is built by asking
`kept_claims` which id the survivor takes and giving that id to another claim, four times
over. Made-up notes, at the store's level (Store.save_page gates nothing)."""

import io
import logging

import pytest

from cairn.compiler import render_body
from cairn.spans import claim_id
from cairn.store import Claim, Store
from cairn.store.erasure import kept_claims
from cairn.store.portable import version_faults

FOLDED = ("Ada keeps port 80 open.  ", "Ada keeps port 80 open.")  # one text, two notes
OTHERS = ("Brook owns the rollback.", "Quill keeps the log.", "Wren reads the log.", "Fin.")


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def notes_of(store, texts=(*FOLDED, *OTHERS)):
    project = store.create_project("alpha")
    notes = [store.add_note(project.id, text, title="Net", topic="Net")[0] for text in texts]
    return project, notes, [store.note_spans(note.id)[0] for note in notes]


def crowded(passages, place: int, rounds: int = len(OTHERS)) -> list[Claim]:
    """A page of a fold of the first two passages, at `place`, and one claim for each other
    passage, where `rounds` of those claims hold, one after another, each id the fold takes
    when the first passage is forgotten. Every id on it is held once."""
    gone, stays, *rest = passages
    held = {span.id: span for span in passages[1:]}
    fold = Claim(id=claim_id(gone.id), text=gone.text, span_ids=(gone.id, stays.id))
    others = [Claim(id=claim_id(span.id), text=span.text, span_ids=(span.id,)) for span in rest]
    for n in range(rounds):
        claims = [*others[:place], fold, *others[place:]]
        wanted = kept_claims(claims, frozenset({gone.id}), held)[place].id
        others[n] = others[n].model_copy(update={"id": wanted})
    claims = [*others[:place], fold, *others[place:]]
    assert len({claim.id for claim in claims}) == len(claims), "the survivor was given a held id"
    return claims


@pytest.mark.parametrize("place", [0, 2, len(OTHERS)], ids=["first", "between", "last"])
@pytest.mark.parametrize("rounds", range(1, len(OTHERS) + 1))
def test_the_survivor_takes_an_id_no_claim_of_the_page_holds(store, place, rounds):
    """However many of the ids it would take are held, before it or after it."""
    _, _, passages = notes_of(store)
    gone, stays = passages[:2]
    claims = crowded(passages, place, rounds)
    kept = kept_claims(claims, frozenset({gone.id}), {span.id: span for span in passages[1:]})
    assert [(claim.text, claim.span_ids) for claim in kept] == [
        (stays.text, (stays.id,)) if n == place else (claim.text, claim.span_ids)
        for n, claim in enumerate(claims)
    ]
    assert len({claim.id for claim in kept}) == len(claims)
    assert [claim.id for n, claim in enumerate(kept) if n != place] == [
        claim.id for n, claim in enumerate(claims) if n != place
    ]  # and no other claim's id moves
    assert kept[place].id != claim_id(gone.id)  # nothing made from the forgotten passage


@pytest.mark.parametrize("place", [0, len(OTHERS)], ids=["first", "last"])
def test_a_forget_on_a_page_that_holds_every_id_it_would_take_drops_no_claim(
    store, tmp_path, caplog, place
):
    """The page is one an import takes; after the forget every claim whose note stays is on
    it, each id once, nothing is counted as left, and the export imports."""
    project, notes, passages = notes_of(store)
    claims = crowded(passages, place)
    spans = {span.id: span for span in passages}
    assert list(version_faults(claims, spans, legacy=True, run_on=None)) == []
    store.save_page(project.id, "net", "Net", render_body("Net", claims, spans), claims)
    with caplog.at_level(logging.INFO, logger="cairn.store"):
        assert store.forget_note(notes[0].id).rebuilt == ("net",)
    page = store.get_page(project.id, "net")
    texts = [claim.text for claim in claims]
    texts[place] = FOLDED[1]
    assert [claim.text for claim in page.claims] == texts
    assert len({claim.id for claim in page.claims}) == len(claims)
    assert [r for r in caplog.records if r.name == "cairn.store"] == []
    (event,) = [e for e in store.list_audit(100) if e.action == "note.forgotten"]
    assert "claims_left" not in event.detail
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        restored = target.get_page(target.get_project("alpha").id, "net")
        assert restored.claims == page.claims
    finally:
        target.close()


def test_two_folds_renamed_in_one_forget_take_two_ids_beside_a_claim_that_holds_the_draw(store):
    """The case beside it: two folds lose the passage their id was made from and keep one
    passage between them, so the second wants the id the first was just given, and a third
    claim holds the id it draws instead."""
    texts = ("Ada keeps port 80 open.  ", "Ada keeps port 80 open. ", "Ada keeps port 80 open.")
    _, _, (one, two, stays, other) = notes_of(store, (*texts, "Brook owns the rollback."))
    gone, held = frozenset({one.id, two.id}), {stays.id: stays, other.id: other}
    folds = [
        Claim(id=claim_id(span.id), text=span.text, span_ids=(span.id, stays.id))
        for span in (one, two)
    ]
    beside = Claim(id=claim_id(other.id), text=other.text, span_ids=(other.id,))
    drawn = kept_claims([*folds, beside], gone, held)[1].id
    assert drawn not in {claim_id(stays.id), beside.id}
    claims = [*folds, beside.model_copy(update={"id": drawn})]
    kept = kept_claims(claims, gone, held)
    assert [claim.span_ids for claim in kept] == [(stays.id,), (stays.id,), (other.id,)]
    assert (kept[0].id, kept[2].id) == (claim_id(stays.id), drawn)
    assert len({claim.id for claim in kept}) == 3
