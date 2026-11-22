"""Shared test helpers extracted from test_rt1_fix_regressions_unit."""

from cairn.spans import CLOSERS_MAX
from support.rt1_harness import Note

SPENT = "\n\n".join([f"    ~~~\n    first {n}" for n in range(CLOSERS_MAX)] + ["  End of first."])


def chain(count: int, older: list[Note], topic: str = "Ops") -> list[Note]:
    """`count` siblings, one a note, each filing the ones before it (and `older`) in its
    gate: stored in the reverse of their order on the page, before every other note."""
    inner = older
    for n in range(count):
        inner = [Note(f"s{n}", f"    - ~~~\n      sample {n}", topic, inner=inner)]
    return inner
