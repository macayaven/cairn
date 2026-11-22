"""A new title that changes the title line alone (the eighth round, decision 1 loosened).

A forget titles the page in every render by the oldest note left, and writes a claim whose
first heading says the title with no heading (compiler.section_headings, comparing the text
as stored). So a new title changes the blocks below the title only where a claim left has a
first heading that says the old title or the new one. Where none does, the bodies before
and after differ in the title line alone, which the parser reads as one heading whatever it
says, so every block after it reads as it did; and the claims go on leaving together.

The page: notes stored in reverse, each a sibling whose fence runs on once the paragraph at
the margin is forgotten, spelling the topic Ops and OPS in turn, so that the title changes at
every step of the plain loop (the eighth round's first input: a hundred cost 101 renders, as
many as the plain loop), and an aside under a heading that says neither spelling, one, the
other or both. Each forget runs in the differential harness (test_rt1_harness: it keeps what
the plain loop keeps, and an export of the page restores it), its renders beside the plain
loop's. Made-up notes."""

import sys

import pytest

from cairn.spans import CLOSERS_MAX
from support.rt1_harness import Note, Plan, Run, expected
from support.store.forget_cost import RENDERS_OVER, alternating, older

COUNT = CLOSERS_MAX + 8  # siblings: where every step changes the body, past the ceiling

HEADINGS = {  # the asides' headings, and whether a title change moves anything below it
    "no aside": ([], False),
    "a heading that says neither spelling": (["Other"], False),
    "a heading that says the first spelling": (["Ops"], True),
    "a heading that says the second": (["OPS"], True),
    "headings that say both": (["Ops", "OPS"], True),
}


@pytest.mark.parametrize("name", list(HEADINGS))
def test_claims_leave_together_while_a_new_title_moves_nothing_below_it(name):
    """Where no heading says either spelling, the whole run leaves in the first render and
    a second finds nothing: two renders, against the plain loop's one a sibling. Where one
    does, the body really differs at each step (the aside's heading is written or left
    out), and the forget renders as often as the plain loop: the stated worst case."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20_000))
    headings, moves = HEADINGS[name]
    run = Run(alternating(COUNT, headings), check_restore=True)
    try:
        assert set(run.status.values()) == {"saved"}, run.status
        plain = sum(len(e.titles) for e in expected(run.store, run.pid, run.ids["sep"]).values())
        assert plain == COUNT + 1  # the plain loop names one sibling a render
        run.forget(0, run.ids["sep"])
        renders = run.stats[-1]["renders"]
        assert renders <= plain + RENDERS_OVER
        if moves:  # within the harness's ceiling on renders since RT1's ninth round (the
            # cap and two, or the plain loop's), as by the plain loop
            assert (renders, run.faults) == (plain, [])
        else:
            assert (renders, run.faults) == (2, [])
    finally:
        run.close()


def headed(count: int, at: int, heading: str) -> Plan:
    """`alternating` with no aside, and the sibling at `at` under `## heading` in its own
    note: the claim whose heading says a spelling is one of those that leave."""
    inner = older()
    for n in range(count):
        text = f"    - ~~~\n      sample {n}"
        if n == at:
            text = f"## {heading}\n\n{text}"
        inner = [Note(f"s{n}", text, ("Ops", "OPS")[n % 2], inner=inner)]
    return Plan([*inner, Note("sep", "Between.", "Ops"), Note("tail", "        End.", "Ops")], [])


@pytest.mark.parametrize("heading", ["Ops", "OPS"])
def test_a_heading_counts_while_its_claim_is_on_the_page(heading):
    """The heading that says a spelling is over the twelfth sibling from the margin. Until
    that sibling leaves, every new title moves it (into the run or under its heading after
    the tail), and each sibling nearer the tail leaves in a render of its own, as in the
    plain loop; from the step at which it leaves no claim left has such a heading, so the
    rest leave with it. Thirteen renders, against the plain loop's twenty-four or
    twenty-five (under `## Ops`, fourteen were the headings counted over the whole render
    and not over the claims left at each step)."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20_000))
    run = Run(headed(COUNT, 12, heading), check_restore=True)
    try:
        assert set(run.status.values()) == {"saved"}, run.status
        plain = sum(len(e.titles) for e in expected(run.store, run.pid, run.ids["sep"]).values())
        run.forget(0, run.ids["sep"])
        renders = run.stats[-1]["renders"]
        assert (renders, run.faults) == (COUNT - 11, [])
        assert renders <= plain + RENDERS_OVER and plain >= COUNT
    finally:
        run.close()
