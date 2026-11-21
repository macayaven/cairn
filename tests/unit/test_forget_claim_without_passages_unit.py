"""A forget is not stopped by a claim that cites no passage (the first reviewer, on 22f498b6).

A page saved past the gates (Store.save_page gates nothing; an earlier build) can hold a
claim with no citation. Its id may be the one cairn makes from a passage of the note being
forgotten (spans.claim_id), and erasure.kept_claims then gave it a new id made from the first
passage it cites: there is none, so forget_note and forget_preview raised IndexError before
the import's check could take the claim. A forget is never refused: the claim leaves the
version as any claim an import would refuse does, counted in the audit event's `claims_left`,
and what the forget wrote is exported and imported.

Every page here is built at the store's level. Made-up notes."""

import io
import logging
from contextlib import closing

import pytest

from cairn.compiler import render_body
from cairn.spans import claim_id
from cairn.store import Claim, Store
from cairn.store.erasure import kept_claims
from cairn.store.portable import Refusal, version_faults

GATE, LOG = "Ada keeps the gate.", "Quill keeps the log."
ORPHAN = "Brook keeps nothing."
OTHER_ID, UNUSED_ID = "cl_00000000000000aa", "cl_00000000000000bb"


@pytest.fixture
def store():
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        yield store


def page_with(store: Store, orphan_id, *, beside: bool = True):
    """The page `net` of two notes, one passage each: a claim of the first note's passage
    under an id of its own, a claim that cites nothing under `orphan_id` (a function of the
    first passage's id), and, with `beside`, the second note's claim. (the notes, the page)"""
    project = store.get_project("alpha")
    notes = [store.add_note(project.id, text, title="Net", topic="Net")[0] for text in (GATE, LOG)]
    (first,), (second,) = (store.note_spans(note.id) for note in notes)
    claims = [
        Claim(id=OTHER_ID, text=first.text, span_ids=(first.id,)),
        Claim(id=orphan_id(first.id), text=ORPHAN, span_ids=()),
    ]
    if beside:
        claims.append(Claim(id=claim_id(second.id), text=second.text, span_ids=(second.id,)))
    spans = {first.id: first, second.id: second}
    body = render_body("Net", claims, spans)
    return notes, store.save_page(project.id, "net", "Net", body, claims)


def claims_left(store: Store) -> int | None:
    (event,) = [e for e in store.list_audit(100) if e.action == "note.forgotten"]
    return event.detail.get("claims_left")


def restored_claims(store: Store) -> tuple[Claim, ...]:
    archive = io.BytesIO()
    store.export(archive)
    with closing(Store(":memory:")) as target:
        target.import_export(io.BytesIO(archive.getvalue()))
        return target.get_page(target.get_project("alpha").id, "net").claims


IDS = {
    "the id made from the forgotten passage": claim_id,
    "an id no passage makes": lambda _: UNUSED_ID,
}


@pytest.mark.parametrize("which", list(IDS))
def test_a_claim_that_cites_nothing_leaves_with_the_note_and_the_forget_completes(
    store, caplog, which
):
    notes, before = page_with(store, IDS[which])
    said = store.forget_preview(notes[0].id)
    with caplog.at_level(logging.WARNING, logger="cairn.store"):
        done = store.forget_note(notes[0].id)
    assert done == said and (done.rebuilt, done.removed) == (("net",), ())
    page = store.get_page(before.project_id, "net")
    assert [(claim.text, claim.span_ids) for claim in page.claims] == [
        (LOG, before.claims[2].span_ids)
    ]
    assert claims_left(store) == 1  # the claim that cites nothing, and no other
    assert restored_claims(store) == page.claims
    (line,) = [r.getMessage() for r in caplog.records if r.name == "cairn.store"]
    assert line == (
        f"forget: 1 claim(s) that an import would refuse left page 'net' at its version"
        f" {before.version + 1}"
    )
    assert store.get_note(notes[1].id) is not None


@pytest.mark.parametrize("which", list(IDS))
def test_a_page_left_with_that_claim_alone_is_removed(store, which):
    """One step past: no claim of a note that stays is beside it, so the page goes."""
    notes, _ = page_with(store, IDS[which], beside=False)
    said = store.forget_preview(notes[0].id)
    done = store.forget_note(notes[0].id)
    assert done == said and (done.rebuilt, done.removed) == ((), ("net",))
    assert store.list_pages(store.get_project("alpha").id) == []
    assert claims_left(store) == 1
    archive = io.BytesIO()
    store.export(archive)
    with closing(Store(":memory:")) as target:
        assert target.import_export(io.BytesIO(archive.getvalue())).pages == 0


def test_the_kept_claim_that_cites_nothing_holds_no_id_made_from_a_forgotten_passage(store):
    """kept_claims itself: the claim is kept for the import's check to judge, under an id
    drawn from its place (it keeps no passage to make one from), which no other kept claim
    holds; and the import's check names it, as a claim that is not what a passage says."""
    notes, page = page_with(store, claim_id)
    (first,), (second,) = (store.note_spans(note.id) for note in notes)
    kept = kept_claims(page.claims, frozenset({first.id}), {second.id: second})
    assert [(claim.text, claim.span_ids) for claim in kept] == [
        (ORPHAN, ()),
        (LOG, (second.id,)),
    ]
    assert kept[0].id != claim_id(first.id) and len({claim.id for claim in kept}) == 2
    assert kept[1] == page.claims[2]
    faults = list(version_faults(kept, {second.id: second}, legacy=True, run_on=None))
    assert faults == [(1, Refusal.NOT_WHOLE)]
