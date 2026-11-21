"""What a note's write and a page's read cost in statements, pinned before the core-promise
fixes (a page says only what its notes say) and held after them: none of the fixes reads or
writes more. Counted with the trace callback of every connection of the store, never a
clock: reads have run since on the store's read-only connections (cairn.store.base.Readers),
and a callback on the writer alone no longer sees them (Store._observe watches them all).
The counts are those of 653a4d2d: a page's read reads when each note was written
(Store.note_origins) in the statement that already read who wrote it, and it reads no
search index, so no reader pays FTS5's loading of an index's settings
(tests/unit/test_search_cost_unit.py's SETTINGS). A note's write costs one statement more
since the forget across projects: its page's save checks whose passages its claims cite
(save_page's refusal of another project's passage), one statement by the spans' primary
key. Made-up notes."""

import pytest

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store

FIRST = "# Runbook\n\nQuill restarts the queue at night.\n"
SECOND = "# Runbook\n\n```python\nrestart(queue)\n```\n\nBrook checks the queue each morning.\n"


@pytest.fixture
def memory(tmp_path):
    store = Store(tmp_path / "cairn.db")
    store.create_project("alpha")
    yield Memory(store, lint_gate)
    store.close()


def counted(memory, call):
    seen: list[str] = []
    memory.store._observe(trace=seen.append)
    try:
        answer = call()
    finally:
        memory.store._observe(trace=None)
    return answer, len(seen)


def test_a_note_written_onto_a_page_costs_the_statements_it_did(memory):
    first, cost_first = counted(memory, lambda: memory.note("alpha", FIRST, title="Runbook"))
    second, cost_second = counted(
        memory, lambda: memory.note("alpha", SECOND, title="Runbook", topic="Runbook")
    )
    assert first["status"] == second["status"] == "saved"
    assert (cost_first, cost_second) == (FIRST_NOTE, SECOND_NOTE)


def test_a_page_read_costs_the_statements_it_did(memory):
    memory.note("alpha", FIRST, title="Runbook")
    slug = memory.note("alpha", SECOND, title="Runbook", topic="Runbook")["page"]
    page, cost = counted(memory, lambda: memory.page("alpha", slug))
    assert page["version"] == 2
    assert cost == PAGE_READ


# Measured at 653a4d2d, before the fixes, as 84, 102 and 16; each note's write then one more:
# its save's check of whose passages it cites. The second note's three more since report 8:
# the compile asks, in a read of its own (begin, one statement, commit), which passages the
# page it builds on cites of another project's notes (compiler.own_claims). The first note's
# one fewer since ranking.index_page takes a page's rows out of the index by rowid: FTS5's own
# scan of its content table, which the delete by project and slug ran even on a page's first
# save, is gone; cairn's own statements are the same.
FIRST_NOTE, SECOND_NOTE, PAGE_READ = 84, 106, 16
