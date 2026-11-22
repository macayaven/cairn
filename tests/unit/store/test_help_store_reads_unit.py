"""The store's narrow reads for Help (web/docs_helper.py): each is limited to the view's reach
as its neighbour is (first_page_heads as page_heads, page_claims as get_page, spans_within
as get_spans, note_sources as get_notes), and none returns a text past its bound."""

import pytest

from cairn.compiler import provenance_gate
from cairn.memory import Memory
from cairn.store import Store


@pytest.fixture
def made(tmp_path):
    store = Store(tmp_path / "cairn.db")
    inside, outside = store.create_project("inside"), store.create_project("outside")
    memory = Memory(store, provenance_gate)
    for project in (inside, outside):
        memory.note(project.name, "# Topic\n\nA short passage.\n", via="api", title="topic")
    yield store, inside, outside
    store.close()


def _all(view, project):
    heads = view.first_page_heads(project.id, 10)
    found = [view.page_claims(project.id, head.slug, 10_000) for head in heads]
    span_ids = [i for _, claims, _ in found for claim in claims for i in claim.span_ids]
    spans = view.spans_within(span_ids, 10_000)
    notes = view.note_sources([span.source_id for span in spans.values()], 10_000)
    return heads, found, spans, notes


def test_each_read_finds_what_is_in_reach(made):
    store, inside, _ = made
    view = store.narrow([inside.id], "read")
    heads, found, spans, notes = _all(view, inside)
    assert len(heads) == 1 and found[0][0] == 1 and found[0][1]
    assert [span.text for span in spans.values()] == ["A short passage."]
    assert list(notes.values()) == [(inside.id, "topic", None, "api")]


def test_nothing_out_of_reach_is_read(made):
    store, inside, outside = made
    heads, found, spans, _ = _all(store, outside)  # what the whole store sees
    view = store.narrow([inside.id], "read")
    assert view.first_page_heads(outside.id, 10) == []
    assert view.page_claims(outside.id, heads[0].slug, 10_000) is None
    span_ids = [i for claim in found[0][1] for i in claim.span_ids]
    assert view.spans_within(span_ids, 10_000) == {}
    assert view.note_sources([span.source_id for span in spans.values()], 10_000) == {}


def test_a_text_past_its_bound_is_not_returned(made):
    store, inside, _ = made
    heads, _, spans, _ = _all(store, inside)
    version, claims, metrics = store.page_claims(inside.id, heads[0].slug, 5)
    assert version == 1 and claims is None and metrics is None
    assert store.spans_within(list(spans), len("A short passage.") - 1) == {}
    assert store.first_page_heads(inside.id, 0) == []
