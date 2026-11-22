"""Shared test helpers extracted from test_forget_cost_unit."""

from support.rt1_harness import Note, Plan
from support.store.rt1_fix_regressions import SPENT

RENDERS_OVER = 1  # renders past the plain loop's, at most, and only past sixteen named

PER_RENDER = 25  # parser characters, at most, per render and character of the body


def older() -> list[Note]:
    return [
        Note("item", "- item", "Ops"),
        Note("spent", SPENT, "Ops"),
        Note("parent", "- parent", "Ops"),
    ]


def alternating(count: int, headings: list[str]) -> Plan:
    """`count` siblings, each its own note, the nearest the tail the oldest, spelling the
    topic Ops and OPS in turn, so that the title changes at every step of the plain loop;
    then the paragraph at the margin, the tail, and an aside under each of `headings`,
    filed last so that no aside gives the title (test_forget_title_line_unit.py)."""
    inner = older()
    for n in range(count):
        inner = [Note(f"s{n}", f"    - ~~~\n      sample {n}", ("Ops", "OPS")[n % 2], inner=inner)]
    asides = [Note(f"aside{n}", f"## {h}\n\nAn aside {n}.", "Ops") for n, h in enumerate(headings)]
    return Plan(
        [*inner, Note("sep", "Between.", "Ops"), Note("tail", "        End.", "Ops"), *asides], []
    )
