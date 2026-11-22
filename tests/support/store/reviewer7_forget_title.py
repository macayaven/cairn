"""Shared test helpers extracted from test_reviewer7_forget_title_unit."""

from cairn import compiler


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
