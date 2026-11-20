"""Past a cap of renders a forget takes the claims a body left open together (eighth round).

The plain loop (drop the claim named, render again) is the reference for what leaves. It
cannot also be the reference for what a forget may cost: a page can be built on which it
renders once for each claim (notes stored in reverse that spell the title two ways in turn,
with a heading that says one spelling: test_forget_title_line_unit.py). So once a forget has
made erasure.RENDERS_MAX renders that each named a claim, at every render after that the
claim named leaves with every claim written directly before it that the body left open: back
to a heading or any other claim, each one whose code fence is still open where the claim
ends and after which the body wrote no closing line, on a body that has written all its
closers. All of them at once, without asking the parser what follows, whatever the title
does; and the claims after it of the class a forget takes past sixteen renders. A second
stated class, which the security guide says.

What the cap takes is checked here by its own definition (`left_open`, read from the body's
parse, not from the forget's code), beside the plain loop (test_rt1_harness). Made-up
notes."""

import itertools
import re
import sys
from collections import Counter

import pytest
from markdown_it import MarkdownIt
from test_forget_cost_unit import older
from test_guide_facts_slack_unit import says
from test_rt1_harness import PROJECT, Note, Plan, Run, Watch, declared, expected, generated, key

from cairn import compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import CLOSERS_MAX, fences_kept_in
from cairn.store import erasure

CAP = 64  # erasure.RENDERS_MAX, as the guide says it
CHECKS = "## What the checks on pages catch"
CAPPED = (
    "After 64 readings that each find a claim at fault, a forget stops asking which claims"
    " before the one at fault would be at fault next: from the 65th, the claim at fault leaves"
    " with every claim directly before it whose code fence was never closed (a list item's"
    " fence with no closing line, which ends where its item ends), back to the first heading"
    " or claim that has none, though some of them would have stayed; the claims after it leave"
    " as they do after sixteen readings."
)
PARSER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as cairn reads a body
LINES = re.compile(r"\r\n?|\n")
WIDE = "    -   ~~~\n        wide sibling"  # its item would not hold a line indented six


def left_open(render, held) -> list:
    """The claims the cap takes at `render` (title, claims, place, body: a render the forget
    made) besides the claim named, by the class's definition: those written directly before
    it, back to a heading or any other claim, each one after which the body wrote nothing
    and whose code fence, in the body's parse, starts in it and covers the line after its
    last; none when the body has not written all its CLOSERS_MAX closers."""
    title, claims, place, body = render
    if not place:
        return []
    closed = compiler._blocks(
        title, claims, held, compiler._closed, compiler.section_headings, compiler.heading_text
    )
    written, _ = fences_kept_in([text for text, _ in closed])
    assert "\n\n".join(written) + "\n" == body  # the body the forget rendered
    pairs = zip(written, closed, strict=True)
    spent = sum(len(LINES.split(w)) - len(LINES.split(c)) for w, (c, _) in pairs)
    if spent != CLOSERS_MAX:
        return []
    starts = list(itertools.accumulate((len(LINES.split(w)) + 1 for w in written), initial=0))
    fences = [token.map for token in PARSER.parse(body) if token.type == "fence" and token.map]
    named = claims[place - 1]
    at = next(n for n, (_, claim) in enumerate(closed) if claim is named)
    out = []
    for n in range(at - 1, -1, -1):
        claim = closed[n][1]
        if claim is None or written[n] != claim.text:
            break
        first, end = starts[n], starts[n] + len(LINES.split(claim.text))
        if not any(first <= start < end < stop for start, stop in fences):
            break
        out.append(claim)
    return out


def cascade(count: int, *, wide: bool = False) -> Plan:
    """`count` siblings stored in reverse, spelling the topic Ops and OPS in turn, an aside
    under `## Ops`, and a tail indented six: the plain loop renders once for each sibling
    (test_forget_cost_unit.alternating). With `wide`, a sibling before them all whose item
    would not hold the tail: the plain loop keeps it."""
    inner = [*older(), *([Note("wide", WIDE, "Ops")] if wide else [])]
    for n in range(count):
        inner = [Note(f"s{n}", f"    - ~~~\n      sample {n}", ("Ops", "OPS")[n % 2], inner=inner)]
    return Plan(
        [
            *inner,
            Note("sep", "Between.", "Ops"),
            Note("tail", "      End.", "Ops"),
            Note("aside", "## Ops\n\nAn aside.", "Ops"),
        ],
        [],
    )


def forgotten(plan: Plan):
    """The plain loop's claims and renders, the forget's claims and renders, and the renders
    it made, for forgetting the paragraph at the margin."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20_000))
    run = Run(plan, check_restore=False)
    try:
        assert set(run.status.values()) == {"saved"}, run.status
        loop = expected(run.store, run.pid, run.ids["sep"])["ops"]
        watch = Watch()
        with watch.on():
            Memory(run.store, lint_gate).forget(PROJECT, run.ids["sep"])
        kept = [claim.text for claim in run.store.get_page(run.pid, "ops").claims]
        return [c.text for c in loop.kept], len(loop.titles), kept, watch.renders, loop.held
    finally:
        run.close()


def test_the_cap_is_sixty_four_renders_and_the_guide_says_what_it_takes():
    assert erasure.RENDERS_MAX == CAP
    says("security", CHECKS, CAPPED)


@pytest.mark.parametrize("count", [CAP, CAP + 1])
def test_at_the_cap_a_page_that_loses_nothing_to_it_is_the_plain_loops(count):
    """The plain loop names one sibling a render: sixty-four, then sixty-five. At sixty-four
    the cap is not reached. At sixty-five the render after it names the last sibling, and
    nothing before it is left open (the list's parent holds no fence): the same claims and
    the same renders as the plain loop, one a sibling and one that finds nothing."""
    plain, renders, kept, made, held = forgotten(cascade(count))
    assert (kept, len(made)) == (plain, renders) and renders == count + 1
    naming = [render for render in made if render[2] is not None]
    assert [left_open(render, held) for render in naming[CAP:]] == [[]] * (count - CAP)


@pytest.mark.parametrize("count", [CAP, CAP + 1])
def test_past_the_cap_a_sibling_left_open_before_the_named_leaves_though_the_plain_loop_keeps_it(
    count,
):
    """The smallest page found where the cap and the plain loop differ: sixty-five siblings
    the plain loop names one a render, and before them a sibling whose item would not hold
    the tail, so its fence never covers it and the plain loop keeps it. At the sixty-fifth
    render the forget names the last of the sixty-five and takes the wide one with it, left
    open in the body as it is: the cap's class, and nothing else. With sixty-four the cap is
    not reached and the wide sibling stays, as in the plain loop."""
    plain, renders, kept, made, held = forgotten(cascade(count, wide=True))
    assert WIDE in plain and len(made) == renders == count + 1
    naming = [render for render in made if render[2] is not None]
    if count == CAP:
        assert kept == plain and len(naming) == CAP
    else:
        assert kept == [text for text in plain if text != WIDE]
        assert [[c.text for c in left_open(r, held)] for r in naming[CAP:]] == [[WIDE]]


# (cap, seeds, the claims each class took that the plain loop keeps): at 0 the cap acts at
# every render that names a claim; at 1 the class taken past sixteen renders acts too
LOW = [
    (0, range(0, 100), {"past sixteen": 1, "the cap": 14}),
    (1, range(100, 200), {"past sixteen": 97, "the cap": 8}),
]


@pytest.mark.parametrize(("low", "seeds", "took"), LOW, ids=["cap 0", "cap 1"])
def test_generated_forgets_with_a_low_cap_take_only_the_two_stated_classes(
    monkeypatch, low, seeds, took
):
    """The tester's generated pages (test_rt1_harness.generated), forgotten with the cap at
    `low` so that it is reached: every claim a forget took that the plain loop keeps is of
    the class taken past sixteen renders (test_rt1_harness.declared) or of the cap's
    (`left_open`, at a render past the cap), each by its own definition, and the forget
    keeps no claim the plain loop drops. Counted by class (a claim of both is counted
    once, as the first); with the cap at sixty-four no generated page reaches it."""
    monkeypatch.setattr(erasure, "RENDERS_MAX", low)
    counts: Counter[str] = Counter({"past sixteen": 0, "the cap": 0})
    for seed in seeds:
        run = Run(generated(seed), check_restore=False)

        def forget(step, note_id, run=run, seed=seed):
            store, pid = run.store, run.pid
            want = expected(store, pid, note_id)
            notes = [note for note in store.list_notes(pid) if note.id != note_id]
            watch = Watch()
            with watch.on():
                Memory(store, lint_gate).forget(PROJECT, note_id)
            for slug, e in want.items():
                start = {key(claim) for claim in e.start}
                naming = [  # this page's renders that named a claim
                    render
                    for render in watch.renders
                    if render[2] is not None and {key(c) for c in render[1]} <= start
                ]
                page = store.get_page(pid, slug)
                have = {key(claim) for claim in page.claims} if page else set()
                plain = [key(claim) for claim in e.kept]
                assert have <= set(plain), (seed, step, slug)
                gone = {k for k in plain if k not in have}
                if not gone:
                    continue
                first = declared(watch, e.held, notes, slug)
                second = {key(c) for render in naming[low:] for c in left_open(render, e.held)}
                assert gone <= first | second, (seed, step, slug, gone - first - second)
                counts["past sixteen"] += len(gone & first)
                counts["the cap"] += len(gone - first)

        run.forget = forget
        try:
            run.run()
        finally:
            run.close()
    assert counts == took
