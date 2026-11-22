"""Shared test helpers extracted from test_relating_cost_unit."""

import contextlib
from types import SimpleNamespace

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Claim, Store, relating
from support.store.search_steps import PERIOD, merge

QUIET = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}


def settings_loaded(statements: list[str]) -> int:
    """How many of `statements` are FTS5 loading an index's settings (SETTINGS)."""
    return sum(1 for s in statements if s.startswith("SELECT k, v FROM 'main'.'claim_search_"))


@contextlib.contextmanager
def watched(store: Store):
    """The statements the store runs (FTS5's own, which SQLite reports as comments, apart), the
    rows it is handed and the longest text in them, the claims FTS5 reads by row, SQLite's
    steps, and the rows of the index the read was handed for its terms and title (`visited`,
    relating.ROWS_MAX at most)."""
    seen = SimpleNamespace(statements=[], rows=0, claims=0, steps=0, longest=0, counts=[])
    before = store._state.observers.rows
    counting = relating._Visits

    class Counted(counting):
        def __init__(self) -> None:
            super().__init__()
            seen.counts.append(self)

    def traced(statement: str) -> None:
        text = statement.lstrip()
        if text.startswith("--"):
            seen.claims += "_content'" in text
        elif text and text.split()[0].upper() not in QUIET:
            seen.statements.append(text)

    def rows(cursor, row):
        seen.rows += 1
        texts = [len(value) for value in row if isinstance(value, str)]
        seen.longest = max(seen.longest, *texts, 0)  # the longest text a statement hands back
        return row if before is None else before(cursor, row)

    def stepped() -> int:
        seen.steps += PERIOD
        return 0

    store._observe(trace=traced)
    store._observe(rows=rows)
    store._observe(progress=(stepped, PERIOD))
    relating._Visits = Counted
    try:
        yield seen
    finally:
        relating._Visits = counting
        store._observe(trace=None)
        store._observe(rows=before)
        store._observe(progress=None)
        seen.visited = sum(count.total for count in seen.counts)


def mentioned(folder, said: str, by_hand: bool = False) -> tuple[Store, str]:
    """A page "Quill ledger" and a page "Other" whose one claim is `said`: the text of its
    passage, as a note is compiled; or, `by_hand`, a claim saved with a short passage of
    other words."""
    store = Store(folder / "cairn.db")
    project = store.create_project("ops")
    memory = Memory(store, lint_gate)
    memory.note("ops", "Ada balances it at dusk.\n", title="Quill ledger", topic="Quill ledger")
    if by_hand:
        note, _ = store.add_note(project.id, "# Other\n\nShe opens the Quill ledger.\n")
        (span,) = [s.id for s in store.note_spans(note.id) if s.kind == "paragraph"]
        store.save_page(
            project.id, "other", "Other", "", [Claim(id="cl_h", text=said, span_ids=[span])]
        )
    else:
        memory.note("ops", said + "\n", title="Other", topic="Other")
    merge(store)
    return store, project.id
