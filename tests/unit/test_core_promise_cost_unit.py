"""What a note's write and a page's read cost in statements, pinned before the core-promise
fixes (a page says only what its notes say) and held after them: none of the fixes reads or
writes more. Counted with the connection's trace callback, never a clock. Made-up notes."""

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
    memory.store._con.set_trace_callback(seen.append)
    try:
        answer = call()
    finally:
        memory.store._con.set_trace_callback(None)
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


FIRST_NOTE, SECOND_NOTE, PAGE_READ = 84, 102, 16  # measured at 653a4d2d, before the fixes
