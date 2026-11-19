"""On the web, as for agents (8.5): a claim from a Slack message someone filed is outside
text, and its source says who filed it and that the words are its author's; a note typed
with the Slack command is the person's own. The page view reads it in the statement that
already read who wrote each note (Store.note_origins): no statement more. Made-up names."""

import pytest
from test_agent_slack_outside_unit import filed, row_hit
from test_slack_commands_unit import ADA_SLACK, Bench, ask
from test_slack_shortcuts_unit import CEDAR, cedar

from cairn.compiler import provenance_gate
from cairn.memory import SLACK_FILED, Memory
from cairn.web.ui import page_view, search_hits, writer_words


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    made.users[CEDAR] = cedar()
    made.link(ADA_SLACK, made.ada, made.library)
    yield made
    made.store.close()


def memory(bench: Bench) -> Memory:
    return Memory(bench.owner, provenance_gate)


def shown(bench: Bench, slug: str) -> dict:
    page = bench.owner.get_page(bench.library.id, slug)
    return page_view(memory(bench), bench.library, page)


def test_a_filed_message_is_outside_on_its_page_and_says_who_filed_it(bench):
    note_id = filed(bench)
    hit = row_hit(bench)
    (entry,) = hit["written_by"]  # what an agent is told: the same person, the same rule
    view = shown(bench, hit["page"])
    (source,) = [s for s in view["sources"] if s["span"].text == "Row 4."]
    assert source["note"].id == note_id and source["outside"] is True
    assert source["writer"] == writer_words(entry["kind"], entry["name"]) == "written by Someone"
    assert source["filed"] == SLACK_FILED


def test_a_note_typed_with_the_command_is_not_outside_on_its_page(bench):
    ask(bench, "note Shelf nine holds the atlases.")
    (hit,) = memory(bench).recall("atlases")["hits"]
    (source,) = shown(bench, hit["page"])["sources"]
    assert source["outside"] is False and source["filed"] is None


def test_a_search_hit_of_a_filed_message_says_who_filed_it(bench):
    filed(bench)
    hits = search_hits(memory(bench).recall("row")["hits"])
    (hit,) = [hit for hit in hits if hit["text"] == "Row 4."]
    assert hit["outside"] is True and hit["filed"] == ["written by Someone: " + SLACK_FILED]


def test_the_page_view_runs_no_statement_more(bench):
    """Measured before the change: the page view read who wrote each note with
    Store.note_writers, the same one statement note_origins runs."""
    filed(bench)
    slug = row_hit(bench)["page"]
    page = bench.owner.get_page(bench.library.id, slug)
    seen: list[str] = []
    bench.store._observe(trace=seen.append)
    try:
        page_view(memory(bench), bench.library, page)
    finally:
        bench.store._observe(trace=None)
    assert len(seen) == PAGE_VIEW


PAGE_VIEW = 12  # 3 reads, each its BEGIN, its membership check, its SELECT and COMMIT
