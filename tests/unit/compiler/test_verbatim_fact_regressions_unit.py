"""A copied fact keeps the meaning of its delimiters in the original passage."""

from contextlib import closing

import pytest

from cairn.compiler import compile_note, extractive
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("See <https://example.invalid/(2_> today.", id="number"),
        pytest.param("See <https://example.invalid/(v1.2.3__> today.", id="version"),
        pytest.param("See <https://example.invalid/(2026-04-09___> today.", id="date"),
        pytest.param("See https://example.invalid/(2_<aside> today.", id="left-angle"),
        pytest.param('See "https://example.invalid/(2_" today.', id="closing-quote"),
        pytest.param("See <https://example.invalid/(2____> today.", id="four-underscores"),
    ],
)
def test_verbatim_fact_is_filed_and_cited(text):
    with closing(Store(":memory:")) as store:
        project = store.create_project("lark")
        memory = Memory(store, lint_gate)
        filed = memory.note("lark", text, title="Almanac", topic="Almanac")
        assert filed["status"] == "saved", filed["violations"]
        page = store.get_page(project.id, "almanac")
        assert page is not None
        (claim,) = page.claims
        assert claim.text == text
        evidence = memory.evidence("lark", page.slug, claim.id)["passages"]
        assert [(p["note_id"], p["text"]) for p in evidence] == [(filed["note_id"], text)]


@pytest.mark.parametrize(
    ("original", "changed"),
    [
        ("See <https://example.invalid/(2_> today.", "See <https://example.invalid/(2*> today."),
        ("Keep `map  file` here.", "Keep `map file` here."),
    ],
    ids=["literal-underscore", "inner-space"],
)
def test_changing_a_fact_is_still_refused(original, changed):
    def compose(title, spans):
        draft = extractive(title, spans)
        (claim,) = draft.claims
        return draft.model_copy(update={"claims": (claim.model_copy(update={"text": changed}),)})

    with closing(Store(":memory:")) as store:
        project = store.create_project("lark")
        note, _ = store.add_note(project.id, original, topic="Almanac")
        result = compile_note(store, note, lint_gate, compose=compose)
        assert result.status == "rejected"
        assert result.gate.violations
        assert store.get_page(project.id, "almanac") is None
