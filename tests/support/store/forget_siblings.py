"""Shared test helpers extracted from test_forget_siblings_unit."""

from contextlib import closing

import pytest

from cairn import compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import CLOSERS_MAX
from cairn.store import Store

FIRST, STEPS = "## First\n\n", "## Steps\n\n"

INLINE = "Ada keeps `port 80` open."

SPENT = [f"    ~~~\n    first {n}" for n in range(CLOSERS_MAX)]  # each runs into the next

END = "  End of first."

CLOSED = "      ```text\n      Brook owns the rollback.\n      ```"


def narrow(count: int, word: str = "sample") -> list[str]:
    """Sibling items whose text starts at column six, a fence in each that no line closes."""
    return [f"    - ~~~\n      {word} {n}" for n in range(count)]


@pytest.fixture
def store():
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        yield store


def filed(store: Store, text: str) -> str:
    answer = Memory(store, lint_gate).note("alpha", text, title="Ops", topic="Ops")
    assert (answer["status"], answer["page"]) == ("saved", "ops"), answer
    return answer["note_id"]


def page(store: Store):
    return store.get_page(store.get_project("alpha").id, "ops")


def texts(store: Store) -> list[str]:
    return [claim.text for claim in page(store).claims]


def under_steps(store: Store, before: list[str], after: list[str], spent=SPENT) -> str:
    """The page: a paragraph with inline code; under "First" a list that spends a closer
    for each of `spent`; under "Steps" a list item, `before`, a paragraph at the margin and
    `after`. The note of the paragraph at the margin."""
    filed(store, INLINE)
    filed(store, FIRST + "- item")
    filed(store, FIRST + "\n\n".join([*spent, END]))
    filed(store, STEPS + "- parent")
    filed(store, STEPS + "\n\n".join(before))
    separator = filed(store, STEPS + "Between.")
    filed(store, STEPS + "\n\n".join(after))
    return separator


def plain_loop(store: Store, note_id: str) -> list[str]:
    """What the page's claims are once the note's own are gone and the loop with no
    ceiling has run: render, drop the one claim named, render again."""
    gone = {span.id for span in store.note_spans(note_id)}
    left = [claim for claim in page(store).claims if not gone & set(claim.span_ids)]
    spans = store.get_spans(i for claim in left for i in claim.span_ids)
    while place := compiler.left_to_run("Ops", left, spans)[1]:
        del left[place - 1]
    return [claim.text for claim in left]
