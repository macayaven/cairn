"""What a forget leaves on a page is the bytes of the passages that are left (third report,
must-fix 1; CORE6A, decision 1).

A fold keeps its first claim's text and cites every passage that says the same under
cairn.spans.compared_text, so its bytes are one note's spelling. When that note is forgotten
the claim that stays takes the text of the first passage it still cites, byte for byte, and
the body is rendered again from the claims: no white space of the forgotten note stays, and
the version is what an extractive compile of the notes left would hold. The same holds for a
claim of the page that cited none of the forgotten note's passages and is the whole of its
own: a spelling left by a forget made before this rule goes at the next one.
Made-up notes."""

import pytest

from cairn.arrange import tidy_page
from cairn.compiler import claim_id, render_body
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import chunk
from cairn.store import Claim, Store
from cairn.store.erasure import kept_claims

TWINS = ("A  \nB", "A\nB", "A\t\nB")  # one text under the rule, three spellings


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def folded(store: Store, texts=TWINS):
    """The notes `texts` under one topic, tidied into one claim: (project, note ids, the
    passage of each note)."""
    project = store.create_project("alpha")
    memory = Memory(store, lint_gate)
    notes = [memory.note("alpha", text, title="Log", topic="Log")["note_id"] for text in texts]
    assert tidy_page(store, project.id, "log").status == "arranged"
    passages = [store.note_spans(note_id)[0] for note_id in notes]
    (fold,) = store.get_page(project.id, "log").claims
    assert (fold.text, fold.span_ids) == (texts[0], tuple(p.id for p in passages))
    return project, notes, passages


def test_the_claim_that_stays_takes_the_bytes_of_the_first_passage_left(store):
    project, notes, (_, second, third) = folded(store)
    store.forget_note(notes[0])
    page = store.get_page(project.id, "log")
    (claim,) = page.claims
    assert (claim.text, claim.span_ids) == (TWINS[1], (second.id, third.id))
    assert claim.id == claim_id(second.id)  # as a compile of the second note alone names it
    assert page.body == render_body("Log", [claim], {second.id: second, third.id: third})
    assert page.body == "# Log\n\nA\nB\n"  # no space of the forgotten spelling
    assert store.page_versions(project.id, "log") == [page.version]

    store.forget_note(notes[1])  # and again: the third spelling, a tab
    (claim,) = store.get_page(project.id, "log").claims
    assert (claim.text, claim.span_ids) == (TWINS[2], (third.id,))


def test_forgetting_a_later_note_leaves_the_first_spelling_and_its_id(store):
    project, notes, (first, second, _) = folded(store)
    before = store.get_page(project.id, "log").claims[0]
    store.forget_note(notes[2])
    (claim,) = store.get_page(project.id, "log").claims
    assert (claim.id, claim.text, claim.span_ids) == (before.id, TWINS[0], (first.id, second.id))


def test_the_page_is_what_a_compile_of_the_notes_left_holds(store, tmp_path):
    """The same notes without the forgotten one, filed in a store that never held it."""
    project, notes, _ = folded(store, TWINS[:2])
    store.forget_note(notes[0])
    page = store.get_page(project.id, "log")
    other = Store(tmp_path / "other.db")
    try:
        other.create_project("alpha")
        Memory(other, lint_gate).note("alpha", TWINS[1], title="Log", topic="Log")
        fresh = other.get_page(other.get_project("alpha").id, "log")
        assert (page.title, page.body) == (fresh.title, fresh.body)
        assert [c.text for c in page.claims] == [c.text for c in fresh.claims]
    finally:
        other.close()


def test_a_passage_the_store_does_not_hold_is_passed_over_never_an_error():
    """kept_claims is given the passages still held: a cited id that is not among them (a
    page saved by hand) is not the first passage left."""
    gone, held = chunk("n1", TWINS[0])[0], chunk("n2", TWINS[1])[0]
    claim = Claim(id="cl_x", text=gone.text, span_ids=(gone.id, "sp_unheld", held.id))
    (kept,) = kept_claims([claim], frozenset({gone.id}), {held.id: held})
    assert (kept.text, kept.span_ids) == (held.text, ("sp_unheld", held.id))


def test_a_spelling_left_by_an_earlier_forget_goes_at_the_next(store):
    """A version as a forget wrote it before this rule: the forgotten note's seven bytes,
    citing the three that stayed. Forgetting another note of the page rewrites it."""
    project = store.create_project("alpha")
    memory = Memory(store, lint_gate)
    memory.note("alpha", TWINS[1], title="Log", topic="Log")
    other = memory.note("alpha", "Quill keeps the log.", title="Log", topic="Log")
    page = store.get_page(project.id, "log")
    stale = page.claims[0].model_copy(update={"text": "A    \nB"})
    claims = [stale, *page.claims[1:]]
    spans = store.get_spans([i for claim in claims for i in claim.span_ids])
    body = render_body("Log", claims, spans)
    store.save_page(project.id, "log", "Log", body, claims, expected_version=page.version)

    store.forget_note(other["note_id"])
    page = store.get_page(project.id, "log")
    assert [(c.id, c.text) for c in page.claims] == [(stale.id, TWINS[1])]
    assert page.body == "# Log\n\nA\nB\n"


def test_a_claim_that_is_not_its_passage_whole_is_not_respelled():
    """Only a claim that says what every passage it cites says takes a passage's bytes: one
    that says other words (saved under a gate named in the verbatim gate's place) and loses
    no citation is kept as it is, as before."""
    span = chunk("n1", "Ada owns the deploy and the rollback.")[0]
    claim = Claim(id="cl_y", text="Ada owns the deploy.", span_ids=(span.id,))
    assert kept_claims([claim], frozenset({"sp_gone"}), {span.id: span}) == (claim,)


def test_a_page_an_earlier_forget_left_past_the_bound_takes_no_note_until_a_forget(store):
    """What this rule does not repair by itself. A version as a forget wrote it before: the
    forgotten spelling, fifteen bytes, citing the three that stayed, beside a claim of one
    byte: past twice its passages' text, so an import refuses it. Every gate now asks the
    import's check, so a note to the page is refused where the lint gate saved it (and the
    export stayed refused); a tidy has nothing to change. Forgetting a note of the page
    rewrites the version, and the note is then filed."""
    project = store.create_project("alpha")
    memory = Memory(store, lint_gate)
    memory.note("alpha", TWINS[1], title="Log", topic="Log")
    other = memory.note("alpha", "C", title="Log", topic="Log")
    page = store.get_page(project.id, "log")
    stale = page.claims[0].model_copy(update={"text": "A" + " " * 12 + "\nB"})
    claims = [stale, *page.claims[1:]]
    spans = store.get_spans([i for claim in claims for i in claim.span_ids])
    body = render_body("Log", claims, spans)
    held = store.save_page(project.id, "log", "Log", body, claims, expected_version=page.version)

    waits = memory.note("alpha", "D", title="Log", topic="Log")
    assert waits["status"] == "rejected"
    assert [v.split(":")[0] for v in waits["violations"]] == ["import.text"]
    assert tidy_page(store, project.id, "log").status == "unchanged"
    assert store.get_page(project.id, "log") == held

    store.forget_note(other["note_id"])
    assert [c.text for c in store.get_page(project.id, "log").claims] == [TWINS[1]]
    again = memory.note("alpha", "D", title="Log", topic="Log")
    assert (again["note_id"], again["status"]) == (waits["note_id"], "saved")
    assert [c.text for c in store.get_page(project.id, "log").claims] == [TWINS[1], "D"]
