"""A forget chooses its title again from the earliest still-cited note on every render."""

from contextlib import closing

import pytest
from test_reviewer6_forget_siblings_unit import restore

from cairn import compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store

SAFE = "    -   item one\n        ~~~\n        almanac"
MIDDLE = "    -   item two\n        ~~~\n        sundial"
NAMED = "    -   last item\n        ~~~\n        compass"
TAIL = "        Tail."


@pytest.fixture
def desk():
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        yield Memory(store, lint_gate)


def file_note(desk, text, topic="OPS", *, before_save=None):
    # Interleave ordinary note writes at the gate. The outer writer retries its stale save.
    if before_save is not None:
        pending = [before_save]

        def gate(candidate, spans):
            if pending:
                pending.pop()()
            return lint_gate(candidate, spans)

        desk = Memory(desk.store, gate)
    result = desk.note("alpha", text, title="Field notes", topic=topic)
    assert (result["status"], result["page"]) == ("saved", "ops"), result
    return result["note_id"]


def first_list(desk, extra):
    file_note(desk, "- first")
    separator = file_note(desk, "Between.")
    examples = [f"    ~~~\n    first {n}" for n in range(16 + extra)]
    file_note(desk, "\n\n".join([*examples, "  End of first."]))
    return separator


def oracle(store, forgotten):
    project = store.get_project("alpha")
    notes = store.list_notes(project.id)  # oldest stored first, independent of filing order
    page = store.get_page(project.id, "ops")
    spans = store.get_spans(i for c in page.claims for i in c.span_ids)
    kept = [c for c in page.claims if all(spans[i].source_id != forgotten for i in c.span_ids)]
    assert len(kept) == len(page.claims) - 1
    steps = []
    while True:
        sources = {spans[i].source_id for c in kept for i in c.span_ids}
        oldest = next(note for note in notes if note.id in sources)
        title = oldest.topic or oldest.title
        body, place = compiler.left_to_run(title, kept, spans)
        if place is None:
            return steps, title, body, tuple(kept)
        assert 1 <= place <= len(kept)
        steps.append((title, kept.pop(place - 1).text))


def almanac_hits(desk):
    return [hit["text"] for hit in desk.recall("almanac", project="alpha")["hits"]]


def check_both_copies(desk, forgotten, title, body, claims, *, independent=None):
    with closing(Store(":memory:")) as copy:
        restore(desk.store, copy)
        pages, counts = [], []
        for view in (desk, Memory(copy, lint_gate)):
            page = view.store.get_page(view.store.get_project("alpha").id, "ops")
            assert view.store.get_note(forgotten) is None
            if independent:
                assert view.store.get_note(independent) is not None
                counts.append((len(page.claims), almanac_hits(view)))
            pages.append((page.title, page.body, page.claims))
        if independent:
            assert counts == [(len(claims), [SAFE])] * 2
        assert pages == [(title, body, claims)] * 2


@pytest.mark.parametrize(
    ("extra", "heading", "three"),
    [
        pytest.param(0, "Ops", False, id="first-fault-seven-notes"),
        pytest.param(1, "Ops", False, id="second-fault"),
        pytest.param(15, "Ops", False, id="new-title-on-render-seventeen"),
        pytest.param(16, "Ops", False, id="seventeenth-fault"),
        pytest.param(0, "Ops", True, id="three-successive-spellings"),
        pytest.param(0, "OPS", False, id="heading-matches-only-the-later-spelling"),
    ],
)
def test_a_title_change_preserves_the_preceding_sibling(desk, extra, heading, three):
    def file_neighbours():
        nonlocal separator, independent
        separator = first_list(desk, extra)
        file_note(desk, "- parent")
        independent = file_note(desk, SAFE)

    def file_middle():
        file_note(desk, MIDDLE, "ops", before_save=file_neighbours)

    separator = independent = None
    file_note(desk, NAMED, "Ops", before_save=file_middle if three else file_neighbours)
    file_note(desk, f"## {heading}\n\n{TAIL}")
    if three:
        file_note(desk, "## ops\n\n        Second tail.")

    page = desk.page("alpha", "ops")
    notes = desk.store.list_notes(desk.store.get_project("alpha").id)
    assert len(notes) == (9 if three else 7)
    assert len(page["claims"]) == 23 + extra + 2 * three
    assert almanac_hits(desk) == [SAFE]

    steps, title, body, claims = oracle(desk.store, separator)
    expected_steps = [("Ops", f"    ~~~\n    first {n}") for n in range(16, 16 + extra)]
    if heading == "Ops":
        expected_steps.append(("Ops", NAMED))
    if three:
        expected_steps.append(("ops", MIDDLE))
    assert steps == expected_steps
    assert title == ("OPS" if heading == "Ops" else "Ops")
    assert f"\n## {heading}\n" in body
    assert len(claims) == (21 if heading == "Ops" else 22) + three

    assert desk.forget("alpha", separator)["rebuilt"] == ["ops"]
    check_both_copies(desk, separator, title, body, claims, independent=independent)


def test_title_change_after_seventeen_faults_exposes_an_eighteenth_in_nineteen_renders(
    desk, monkeypatch
):
    def file_neighbours():
        nonlocal separator
        separator = first_list(desk, 16)
        file_note(desk, "- earlier parent")
        file_note(desk, SAFE)
        file_note(desk, "## OPS\n\n        Earlier tail.")
        file_note(desk, "- later parent")

    separator = None
    file_note(desk, NAMED, "Ops", before_save=file_neighbours)
    file_note(desk, TAIL, "Ops")
    steps, title, body, claims = oracle(desk.store, separator)
    assert steps[16:] == [("Ops", NAMED), ("OPS", SAFE)]
    assert len(claims) == 22

    renders = []
    render = compiler.left_to_run

    def observe(title, claims, spans):
        renders.append(title)
        return render(title, claims, spans)

    with monkeypatch.context() as patch:
        patch.setattr(compiler, "left_to_run", observe)
        assert desk.forget("alpha", separator)["rebuilt"] == ["ops"]
    assert renders == ["Ops"] * 17 + ["OPS"] * 2
    check_both_copies(desk, separator, title, body, claims)
