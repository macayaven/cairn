"""8.1: each hit of recall, each claim of page and each passage of evidence says when the note
behind it was written (`written_at`, the ISO time the store keeps; for a claim that cites
several notes, the newest of them), over MCP and REST, with no statement more than before."""

from datetime import UTC, datetime

import pytest

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store
from cairn.store.models import Claim

FIRST = datetime(2099, 2, 3, 4, 5, tzinfo=UTC)
LATER = datetime(2099, 3, 4, 5, 6, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = FIRST

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    store = Store(tmp_path / "cairn.db", clock=clock)
    project = store.create_project("ops")
    older, _ = store.add_note(project.id, "# Deploy\n\nThe deploy runs at noon on Fridays.\n")
    clock.now = LATER
    newer, _ = store.add_note(project.id, "# Deploy\n\nThe deploy runs at nine on Fridays.\n")
    spans = {
        note.id: next(s.id for s in store.note_spans(note.id) if s.kind == "paragraph")
        for note in (older, newer)
    }
    claims = [
        Claim(id="cl_000000000001", text="The deploy runs at noon.", span_ids=(spans[older.id],)),
        Claim(
            id="cl_000000000002",
            text="The deploy runs on Fridays.",
            span_ids=(spans[older.id], spans[newer.id]),
        ),
    ]
    store.save_page(project.id, "deploy", "Deploy", "# Deploy\n", claims, {})
    yield store, Memory(store, lint_gate), older, newer
    store.close()


def test_the_time_is_the_iso_time_the_store_keeps(world):
    store, _, older, newer = world
    assert store.get_note(older.id).created_at == "2099-02-03T04:05:00.000000+00:00"
    assert store.get_note(newer.id).created_at == "2099-03-04T05:06:00.000000+00:00"


def test_a_recall_hit_says_when_its_note_was_written_the_newest_of_several(world):
    _, memory, older, newer = world
    hits = {hit["claim_id"]: hit for hit in memory.recall("deploy")["hits"]}
    assert hits["cl_000000000001"]["written_at"] == older.created_at
    assert hits["cl_000000000002"]["written_at"] == newer.created_at


def test_each_claim_of_a_page_says_when_its_note_was_written(world):
    _, memory, older, newer = world
    claims = memory.page("ops", "deploy")["claims"]
    assert [(c["id"], c["written_at"]) for c in claims] == [
        ("cl_000000000001", older.created_at),
        ("cl_000000000002", newer.created_at),
    ]


def test_each_passage_of_evidence_says_when_its_note_was_written(world):
    _, memory, older, newer = world
    passages = memory.evidence("ops", "deploy", "cl_000000000002")["passages"]
    assert [(p["note_id"], p["written_at"]) for p in passages] == [
        (older.id, older.created_at),
        (newer.id, newer.created_at),
    ]


def test_a_claim_whose_passages_are_gone_says_no_time(world):
    store, memory, _, _ = world
    project = store.get_project("ops")
    gone = Claim(id="cl_000000000003", text="Gone.", span_ids=("sp_000000000000",))
    store.save_page(project.id, "gone", "Gone", "# Gone\n", [gone], {})
    (claim,) = memory.page("ops", "gone")["claims"]
    assert claim["written_at"] is None


def statements(store: Store, call) -> tuple[int, int]:
    """(the SELECTs, every statement) the store's connections run during `call`: the writer
    and the read-only connections reads run on (Store._observe watches them all)."""
    seen: list[str] = []
    store._observe(trace=seen.append)
    try:
        call()
    finally:
        store._observe(trace=None)
    selects = [s for s in seen if s.lstrip().upper().startswith("SELECT")]
    return len(selects), len(seen)


def test_saying_when_runs_no_statement_more_than_before(world):
    """The counts at 653a4d2d, before `written_at`: recall 8 SELECTs of 26 statements, page
    6 of 16, evidence 5 of 15. `written_at` is read in the statement that already read who
    wrote each note (Store.note_origins), so it adds none. Reads have run since on the
    store's read-only connections (cairn.store.base.Readers): the first search on a
    connection pays FTS5 loading the index's settings into it, one SELECT that SQLite also
    reports once more as a nested statement (tests/unit/test_search_cost_unit.py's
    SETTINGS); at 653a4d2d the writer had loaded them when the page was saved. A second
    recall runs 8 of 25 now as at 653a4d2d (FTS5's own nested reads of its index, which
    SQLite reports as statements, are one fewer once its structure is loaded)."""
    store, memory, _, _ = world
    assert statements(store, lambda: memory.recall("deploy")) == (8 + 1, 26 + 2)
    assert statements(store, lambda: memory.recall("deploy")) == (8, 25)
    assert statements(store, lambda: memory.page("ops", "deploy")) == (6, 16)
    assert statements(store, lambda: memory.evidence("ops", "deploy", "cl_000000000002")) == (5, 15)
