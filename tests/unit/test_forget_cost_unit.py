"""What a forget costs, as numbers (RT1; the eighth round).

A forget keeps what the plain loop keeps (drop the claim named, render again:
test_rt1_harness.py). Here, what it costs beside that loop:

- Renders. Until it has named sixteen claims each render is a render of the plain loop's
  and takes at least the claim the plain loop names there, so it renders no more often;
  past that the declared class can change the body (the security guide), and on every page
  tried it rendered at most RENDERS_OVER more often than the plain loop. It renders more
  than sixteen times only where the plain loop does too.
- Parser input. Every character handed to the Markdown parser in the whole forget, against
  its renders times its body (the largest it rendered): at most PER_RENDER. A count from the
  pages tried, not a bound: a render parses the body whole at most CLOSERS_MAX + 1 times,
  each claim alone, and asks about the claims before the one named in a few questions of
  twice the body at most (store.erasure._before_it).

The tester's pages, which cost a render for each sibling after the sixth report's fix (101
renders and 1,171 bodies of parser input for a hundred notes stored in reverse; 68 renders
for two hundred siblings before an 8,000-character line; 335 renders, a minute, for a
thousand before a line of 40,000), cost two renders again, as before that fix. Counts are
asserted, never time (measured on this machine: 0.03 s, 0.05 s and 0.21 s). Made-up notes."""

import sys

import pytest
from test_forget_siblings_unit import CLOSED
from test_guide_facts_slack_unit import says
from test_rt1_fix_regressions_unit import SPENT, chain
from test_rt1_harness import Note, Plan, Run, Watch, expected, generated, signature

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import CLOSERS_MAX

RENDERS_OVER = 1  # renders past the plain loop's, at most, and only past sixteen named
PER_RENDER = 25  # parser characters, at most, per render and character of the body
CHECKS = "## What the checks on pages catch"


def older() -> list[Note]:
    return [
        Note("item", "- item", "Ops"),
        Note("spent", SPENT, "Ops"),
        Note("parent", "- parent", "Ops"),
    ]


def siblings(count: int, line: str) -> Plan:
    """`count` siblings in one note, the paragraph at the margin, then `line`."""
    items = "\n\n".join(f"    - ~~~\n      sample {n}" for n in range(count))
    return Plan(
        [
            *older(),
            Note("run", items, "Ops"),
            Note("sep", "Between.", "Ops"),
            Note("tail", line, "Ops"),
        ],
        [],
    )


def eighteen() -> Plan:
    """test_rt1_fix_regressions_unit's page: sixteen notes stored in reverse, a note of two
    before them, and an example closed where it stands after the tail."""
    pair = Note(
        "pair", "\n\n".join(f"    - ~~~\n      early {n}" for n in range(2)), "Ops", inner=older()
    )
    return Plan(
        [
            *chain(16, [pair]),
            Note("sep", "Between.", "Ops"),
            Note("tail", "        End of samples.", "Ops"),
            Note("example", CLOSED, "Ops"),
        ],
        [],
    )


PAGES = {
    "a hundred notes stored in reverse": lambda: Plan(
        [
            *chain(100, older()),
            Note("sep", "Between.", "Ops"),
            Note("tail", "        End of samples.", "Ops"),
        ],
        [],
    ),
    "two hundred siblings, a line of 8,000": lambda: siblings(200, "        " + "tail " * 1600),
    "a thousand siblings, a line of 40,000": lambda: siblings(1000, "        " + "tail " * 8000),
    "eighteen notes and an example after the tail": eighteen,
}


@pytest.mark.parametrize("name", list(PAGES))
def test_the_testers_pages_cost_two_renders(name):
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20_000))
    run = Run(PAGES[name](), check_restore=False)
    try:
        assert set(run.status.values()) == {"saved"}, run.status
        store, pid = run.store, run.pid
        before = [claim.text for claim in store.get_page(pid, "ops").claims]
        if "siblings" in name:  # the plain loop renders once a sibling: what is left is known
            want = [text for text in before if "sample" not in text and text != "Between."]
        else:
            want = [claim.text for claim in expected(store, pid, run.ids["sep"])["ops"].kept]
        watch = Watch()
        with watch.on():
            Memory(store, lint_gate).forget("alpha", run.ids["sep"])
        assert [claim.text for claim in store.get_page(pid, "ops").claims] == want
        body = max(len(rendered[3]) for rendered in watch.renders)
        assert len(watch.renders) == 2
        assert sum(watch.inputs) <= PER_RENDER * len(watch.renders) * body
        assert max(watch.inputs) <= 2 * body
        if name.startswith("eighteen"):
            assert CLOSED in want  # past sixteen only where the plain loop is
    finally:
        run.close()


SEEDS = range(200, 300)  # the tester's generated test runs 0:200


def test_generated_forgets_cost_what_the_plain_loop_costs():
    """RT1's generated pages, each forget beside the plain loop's renders."""
    worst_over = worst_input = past = 0
    for seed in SEEDS:
        run = Run(generated(seed), check_restore=False)
        plain: list[int] = []
        forget = run.forget

        def watched(step, note_id, run=run, plain=plain, forget=forget):
            plain.append(sum(len(e.titles) for e in expected(run.store, run.pid, note_id).values()))
            forget(step, note_id)

        run.forget = watched
        try:
            run.run()
            assert not signature(run), (seed, run.faults)
            for stat, loop in zip(run.stats, plain, strict=True):
                where = f"seed {seed}: {stat['renders']} renders, the plain loop {loop}"
                assert stat["renders"] <= loop + RENDERS_OVER, where
                if stat["naming"] <= CLOSERS_MAX:
                    assert stat["renders"] <= loop, where
                if stat["renders"] > CLOSERS_MAX:
                    assert loop > CLOSERS_MAX, where
                    past += 1
                # a page left with no claim is removed without a render
                multiple = stat["chars"] / (max(stat["renders"], 1) * stat["body"])
                assert multiple <= PER_RENDER, f"{where}: {multiple:.1f} bodies a render"
                worst_over = max(worst_over, stat["renders"] - loop)
                worst_input = max(worst_input, multiple)
        finally:
            run.close()
    # what these pages came to, pinned: some pass sixteen renders, none costs more than the
    # plain loop, and the parser was handed at most this much
    assert (past, worst_over, round(worst_input, 1)) == (6, 0, 20.2)
    says(
        "security",
        CHECKS,
        "Until a fence still runs on after sixteen readings, a forget reads the page no more"
        " often than it would by reading it again after each claim that leaves.",
    )
