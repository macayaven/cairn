"""Count long suffix copies during a forget, including work before the parser sees its input."""

import sys
from contextlib import closing

import pytest
from test_forget_siblings_unit import narrow, page, under_steps

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store


def suffix_copies_during(tail, work):
    """Count distinct strings holding the unindented tail in cairn's locals and return values.

    Keeping each object alive distinguishes another copy from a new reference to the same
    string. The measurement does not depend on which helper builds the question or whether
    it obtains the suffix by trimming, splitting or slicing. Reading just the first character
    can avoid materializing a suffix altogether.
    """
    suffix = tail.lstrip()
    copies = {}

    def observe(value):
        if isinstance(value, str) and value == suffix:
            copies[id(value)] = value

    def trace(frame, event, value):
        if event == "call":
            return trace if frame.f_globals.get("__name__", "").startswith("cairn.") else None
        if event == "return":
            observe(value)
        for local in frame.f_locals.values():
            observe(local)
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        work()
    finally:
        sys.settrace(previous)
    return sum(map(len, copies.values()))


@pytest.mark.parametrize(("count", "width"), [(14, 400), (28, 800), (56, 1600)])
def test_copying_the_following_line_costs_at_most_four_body_lengths(count, width):
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        siblings = narrow(count)
        tail = " " * 8 + "x" * (width - 8)
        separator = under_steps(store, siblings, [tail])
        before = page(store)
        removed = {"Between.", *siblings}
        expected = [c.text for c in before.claims if c.text not in removed]
        assert len(expected) == len(before.claims) - count - 1 and tail in expected

        desk = Memory(store, lint_gate)
        copied = suffix_copies_during(tail, lambda: desk.forget("alpha", separator))
        assert [c.text for c in page(store).claims] == expected
        assert copied <= 4 * len(before.body), (count, len(before.body), copied)
