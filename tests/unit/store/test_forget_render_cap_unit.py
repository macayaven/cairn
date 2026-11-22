"""Past a cap of renders a forget takes the claims a body left open together (eighth round),
and past a second one every claim that can open a fence (the tenth: a bound by a number).

The plain loop (drop the claim named, render again) is the reference for what leaves. It
cannot also be the reference for what a forget may cost: a page can be built on which it
renders once for each claim (notes stored in reverse that spell the title two ways in turn,
with a heading that says one spelling: test_forget_title_line_unit.py). So once a forget has
made erasure.RENDERS_MAX renders that each named a claim, at every render after that the
claim named leaves with every claim of its section (the claims under the same headings, up
to the next heading the body writes) that the body left open: each one whose code fence is
still open where the claim ends and after which the body wrote no closing line, on a body
that has written all its closers, wherever in the section it stands (the ninth round: the
run of them directly before the claim named stopped at a list item at the margin); and
every claim written after it that can open a code fence (the class a forget
takes past sixteen renders, test_rt1_harness.declared). All of them at once, without asking
the parser what follows, whatever the title does at any step: past the cap the forget is no
longer exact, and a title that changes at every step must not bring a render a claim back.
A second stated class, which the security guide says.

Each class closed one chain, and the same chain was rebuilt one step further out (the run,
then the section, then a pair a section: 300 pairs, 301 renders, 109 s). So at the render
after erasure.RENDERS_HARD the claim named leaves with every claim of the page that can open
a fence, wherever it stands (`the_page`): the third stated class. Then no claim left can
open one, and the next render names none. A claim that cannot open a fence stays at every
level. Should a render after that still name a claim, the parser would have read one text
two ways: the forget then takes every claim with a backtick or a tilde and stops, so that it
renders at most RENDERS_HARD + 3 times whatever the page (test_forget_ceiling_order_unit.py
forces that end with a reader that misreads).

What each class takes is checked here by its own definition (`at_the_cap`, `the_page`, read
from the body's parse and the claims' lines, not from the forget's code), beside the plain
loop (test_rt1_harness). Made-up notes."""

import itertools
import re
import sys
from collections import Counter

import pytest
from markdown_it import MarkdownIt

from cairn import compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import CLOSERS_MAX, fences_kept_in
from cairn.store import erasure
from support.guides.guide_facts_slack import says
from support.rt1_harness import (
    PROJECT,
    Note,
    Plan,
    Run,
    Watch,
    declared,
    expected,
    generated,
    key,
    opens_a_fence_line,
)
from support.store.forget_render_cap import (
    AT_THE_MARGIN,
    CAP,
    EXAMPLE,
    HARD,
    INLINE,
    WIDE,
    cascade,
    sectioned,
    spelling,
)
from support.store.rt1_fix_regressions import SPENT

CHECKS = "## What the checks on pages catch"
CAPPED = (
    "After 24 readings that each find a claim at fault, a forget stops asking which claims"
    " before the one at fault would be at fault next: from the 25th, the claim at fault leaves"
    " with every claim of its section whose code fence was never closed (a list item's fence"
    " with no closing line, which ends where its item ends), wherever in the section it"
    " stands, though some of them would have stayed; the claims after it that can open a code"
    " fence leave too, whatever the title does."
)
PAGED = (
    "At the 33rd, the claim at fault leaves with every claim on the page that can open a code"
    " fence, though some would have stayed, and with any claim the next reading would find at"
    " fault for another reason, so the next reading finds none."
)
NEVER = (
    "A claim that cannot open a code fence, such as a paragraph with inline code or a fenced"
    " block that starts at the margin, is never taken for one."
)
PROMISED = (
    "So a forget reads a page at most 35 times, whatever it holds: a 35th reading comes only"
    " if the 34th still finds a claim at fault, which would mean the parser read one text"
    " two ways, and then every claim with a backtick or a tilde leaves."
)
PARSER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as cairn reads a body
LINES = re.compile(r"\r\n?|\n")
QUOTED = "    > ~~~\n    > a quoted example"  # a fence in a quotation, after the tail


def left_open(render, held) -> list:
    """The claims the cap takes at `render` (title, claims, place, body: a render the forget
    made) before the claim named, by the class's definition: those of its section, from the
    heading before it (or the title), each one after which the body wrote nothing and whose
    code fence, in the body's parse, starts in it and covers the line after its last; none
    when the body has not written all its CLOSERS_MAX closers."""
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
        if claim is None:  # a heading, or the title: the section begins after it
            break
        first, end = starts[n], starts[n] + len(LINES.split(claim.text))
        if written[n] == claim.text and any(first <= a < end < b for a, b in fences):
            out.append(claim)
    return out


def after_it(render, held) -> list:
    """The claims written after the one `render` names that can open a code fence: they begin
    with indentation and a line of theirs opens one, read leniently
    (test_rt1_harness.opens_a_fence_line)."""
    title, claims, place, _ = render
    blocks = compiler._blocks(title, claims, held, str, compiler.section_headings, str)
    order = [claim for _, claim in blocks if claim is not None]
    at = order.index(claims[place - 1])
    return [c for c in order[at + 1 :] if c.text[:1] in (" ", "\t") and opens_a_fence_line(c.text)]


def at_the_cap(render, held) -> list:
    """What the cap's class takes at `render` besides the claim named, by its definition."""
    return [*left_open(render, held), *after_it(render, held)]


def the_page(render) -> list:
    """What the third class takes at `render`: every claim of the page that can open a code
    fence, by the guide's words read leniently: it begins with indentation and a line of
    its opens one (test_rt1_harness.opens_a_fence_line), wherever the body writes it."""
    return [c for c in render[1] if c.text[:1] in (" ", "\t") and opens_a_fence_line(c.text)]


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


def test_the_caps_are_twenty_four_and_thirty_two_renders_and_the_guide_says_what_each_takes():
    assert (erasure.RENDERS_MAX, erasure.RENDERS_HARD) == (CAP, HARD)
    for sentence in (CAPPED, PAGED, NEVER, PROMISED):
        says("security", CHECKS, sentence)


@pytest.mark.parametrize("count", [CAP, CAP + 1])
def test_at_the_cap_a_page_that_loses_nothing_to_it_is_the_plain_loops(count):
    """The plain loop names one sibling a render: CAP, then CAP + 1. At CAP the cap is not
    reached. At CAP + 1 the render after it names the last sibling, and nothing before it
    is left open (the list's parent holds no fence) and nothing after it can open a fence:
    the same claims and the same renders as the plain loop, one a sibling and one that
    finds nothing."""
    plain, renders, kept, made, held = forgotten(cascade(count))
    assert (kept, len(made)) == (plain, renders) and renders == count + 1
    naming = [render for render in made if render[2] is not None]
    assert [at_the_cap(render, held) for render in naming[CAP:]] == [[]] * (count - CAP)


@pytest.mark.parametrize("count", [CAP, CAP + 1])
def test_past_the_cap_a_sibling_left_open_before_the_named_leaves_though_the_plain_loop_keeps_it(
    count,
):
    """The smallest page found where the cap and the plain loop differ before the claim
    named: CAP + 1 siblings the plain loop names one a render, and before them a sibling
    whose item would not hold the tail, so its fence never covers it and the plain loop
    keeps it. At render CAP + 1 the forget names the last of them and takes the wide one
    with it, left open in the body as it is: the cap's class, and nothing else. With CAP
    the cap is not reached and the wide sibling stays, as in the plain loop."""
    plain, renders, kept, made, held = forgotten(cascade(count, wide=True))
    assert WIDE in plain and len(made) == renders == count + 1
    naming = [render for render in made if render[2] is not None]
    if count == CAP:
        assert kept == plain and len(naming) == CAP
    else:
        assert kept == [text for text in plain if text != WIDE]
        assert [[c.text for c in left_open(r, held)] for r in naming[CAP:]] == [[WIDE]]


@pytest.mark.parametrize("count", [CAP, CAP + 1])
def test_past_the_cap_the_claims_after_the_named_leave_whatever_the_title_does(count):
    """And after the claim named: the same siblings and an indented example after the tail,
    closed where it stands, which the plain loop keeps. Past sixteen renders it is of the
    class a forget takes after the claim named, but within the cap that class leaves only
    while the title's text holds, and here each sibling named is the oldest note left, so
    the title changes with each, the last one's too (it spells the title OPS, the notes
    before the list Ops): the example stays, as in the plain loop. At render CAP + 1 it
    leaves with the claim named, whatever the title does."""
    plain, renders, kept, made, held = forgotten(
        cascade(count, example=EXAMPLE, spellings=("OPS", "Ops"))
    )
    assert EXAMPLE in plain and len(made) == renders == count + 1
    naming = [render for render in made if render[2] is not None]
    if count == CAP:
        assert kept == plain
    else:
        assert kept == [text for text in plain if text != EXAMPLE]
        assert [[c.text for c in at_the_cap(r, held)] for r in naming[CAP:]] == [[EXAMPLE]]


def test_past_the_cap_a_fence_in_a_quotation_after_the_named_stays():
    """A claim after the one named that begins with indentation and whose only fence is in a
    quotation: the fence ends with the quotation, so it opens none that runs past the claim
    (erasure._may_open_a_fence), and it stays past the cap as in the plain loop."""
    plain, renders, kept, made, _ = forgotten(
        cascade(CAP + 1, example=QUOTED, spellings=("OPS", "Ops"))
    )
    assert QUOTED in plain and (kept, len(made)) == (plain, renders) and renders == CAP + 2
    says(
        "security",
        CHECKS,
        "A forget does that sixteen times; if a fence still runs on then, the claim that opens"
        " it leaves with every claim written after it on the page that begins with indentation"
        " and has a line that opens a fence outside a quotation, even one whose fence would"
        " have closed inside the claim.",
    )


LONE = "    - ~~~\n      a lone sample"


def uncovering(count: int, *, lone: bool = False) -> Plan:
    """`count` pairs, each a list item at the margin, a sibling whose fence has no closing
    line, and a tail indented six under a heading that spells the topic as no other heading
    does (spelling(k)). The siblings are stored in reverse, the last the oldest, each its
    note's topic spelled as its own tail's heading; the rest is newer, and an intro older
    than all titles the page spelling(0) while it stands, so every tail is under its
    heading and nothing runs on. Forgetting the intro titles the page by the last sibling:
    its tail is written with no heading, after it, and its fence covers it. Without it,
    the next sibling's spelling, and so on. With `lone`, a list item at the margin and a
    sibling under it before the first pair, with no tail: the plain loop keeps it."""
    inner = [Note("item", "- item", spelling(0)), Note("spent", SPENT, spelling(0))]
    if lone:
        inner += [Note("p0", "- parent 0", spelling(0)), Note("lone", LONE, spelling(0))]
    inner.append(Note("p1", "- parent 1", spelling(0)))
    for k in range(1, count + 1):
        sibling = Note(f"y{k}", f"    - ~~~\n      sample {k}", spelling(k), inner=inner)
        tail = Note(f"t{k}", f"## {spelling(k)}\n\n      tail {k}", spelling(0))
        inner = [
            sibling,
            tail,
            *([Note(f"p{k + 1}", f"- parent {k + 1}", spelling(0))] * (k < count)),
        ]
    return Plan([Note("intro", "Intro.", spelling(0)), *inner], [])


def uncovered(plan: Plan):
    """The plain loop's claims and renders and the forget's, forgetting the intro."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20_000))
    run = Run(plan, check_restore=False)
    try:
        assert set(run.status.values()) == {"saved"}, run.status
        slug = run.store.list_pages(run.pid)[0].slug
        loop = expected(run.store, run.pid, run.ids["intro"])[slug]
        watch = Watch()
        with watch.on():
            Memory(run.store, lint_gate).forget(PROJECT, run.ids["intro"])
        kept = [claim.text for claim in run.store.get_page(run.pid, slug).claims]
        return [c.text for c in loop.kept], len(loop.titles), kept, watch.renders
    finally:
        run.close()


@pytest.mark.parametrize("lone", [False, True], ids=["pairs", "and a lone sibling"])
@pytest.mark.parametrize("count", [CAP, CAP + 2])
def test_past_the_cap_every_claim_of_the_section_left_open_leaves_across_items_at_the_margin(
    count, lone
):
    """The page that cost a render a claim past the cap while the class was the run directly
    before the claim named (the ninth round: 900 claims, 301 renders, 58 seconds): each
    sibling named is the oldest note's, and its going gives the page a title that one
    heading alone says, over a tail that then stands after the next sibling, with a list
    item at the margin between each sibling and the one before. Within the cap one render
    a sibling, as in the plain loop. At render CAP + 1 every sibling left in the section
    leaves with the one named, across the list items at the margin: CAP + 2 renders, the
    plain loop's claims. With a lone sibling no tail ever follows, the plain loop keeps
    it; the cap takes it too."""
    plain, renders, kept, made = uncovered(uncovering(count, lone=lone))
    assert (LONE in plain) == lone and renders == count + 1
    assert [title for title, *_ in made[:2]] == [spelling(count), spelling(count - 1)]
    if count == CAP:
        assert (kept, len(made)) == (plain, renders)
    else:
        assert kept == [text for text in plain if text != LONE] and len(made) == CAP + 2


SPENT_CLAIMS = SPENT.split("\n\n")  # sixteen indented fences the body closes, and a line


@pytest.mark.parametrize("count", [HARD, HARD + 1, HARD + 2])
def test_at_the_render_after_the_second_cap_every_claim_that_can_open_a_fence_leaves(count):
    """What cost a render a claim past the first cap: the same chain, each pair in a section
    of its own. The class past the cap stops at the section's heading, nothing else in the
    section is left open, and nothing after the sibling can open a fence: one render a
    sibling, as in the plain loop, up to HARD. At HARD + 1 (the smallest such page), the
    claim named leaves with every claim of the page that can open a fence: the siblings
    left, and the sixteen closed fences the plain loop keeps. The paragraph with inline code
    and the fenced block at the margin stay, as every claim that cannot open a fence does.
    No claim left can open one, and the next render names none: HARD + 2 renders."""
    plain, renders, kept, made = uncovered(sectioned(count))
    assert {INLINE, AT_THE_MARGIN, *SPENT_CLAIMS} <= set(plain) and renders == count + 1
    assert [title for title, *_ in made[:2]] == [spelling(count), spelling(count - 1)]
    if count == HARD:
        assert (kept, len(made)) == (plain, renders)
    else:
        closed = [text for text in SPENT_CLAIMS if opens_a_fence_line(text)]
        assert kept == [text for text in plain if text not in closed]
        assert len(made) == HARD + 2 and len(closed) == CLOSERS_MAX


def test_with_the_pages_class_leave_the_claims_the_next_render_would_name_for_another_fault(
    monkeypatch,
):
    """With both caps at 0 the first render that names a claim is the page's. Its claims
    leave, and with them each claim the next render would name for a fault other than a
    fence, asked without that render (version_faults with no fence; here a reader that
    names the aside so, as a bound on citations would): the next render names none, and
    the end, which would take the fenced block at the margin, is not reached. HARD + 2
    renders, HARD being 0."""
    monkeypatch.setattr(erasure, "RENDERS_MAX", 0)
    monkeypatch.setattr(erasure, "RENDERS_HARD", 0)
    real, aside = erasure.version_faults, "An aside."

    def faults(claims, spans, **asked):
        yield from real(claims, spans, **asked)
        if asked["run_on"] is None and not asked.get("ask_alone", True):
            yield from (
                (n, erasure.Refusal.CITATIONS) for n, c in enumerate(claims, 1) if c.text == aside
            )

    monkeypatch.setattr(erasure, "version_faults", faults)
    plain, _, kept, made, _ = forgotten(cascade(1, example=AT_THE_MARGIN))
    assert {aside, AT_THE_MARGIN} <= set(plain)
    assert aside not in kept and AT_THE_MARGIN in kept and len(made) == 2


# (the two caps, seeds, the claims each class took that the plain loop keeps, and the pages
# whose forget reached the page's class): at 0 and 0 the page's class acts at the first
# render that names a claim; at 0 and 1 the section's does, and the page's at the second;
# at 17 and 18 the class taken past sixteen renders acts first
LOW = [
    (0, 0, range(0, 200), {"past sixteen": 0, "the section": 0, "the page": 1281, "paged": 45}),
    (0, 1, range(200, 300), {"past sixteen": 0, "the section": 272, "the page": 0, "paged": 0}),
    (17, 18, range(100, 200), {"past sixteen": 94, "the section": 12, "the page": 0, "paged": 0}),
]


@pytest.mark.parametrize(
    ("low", "hard", "seeds", "took"),
    LOW,
    ids=["caps 0 and 0", "caps 0 and 1", "caps 17 and 18"],
)
def test_generated_forgets_with_low_caps_take_only_the_three_stated_classes(
    monkeypatch, low, hard, seeds, took
):
    """The tester's generated pages (test_rt1_harness.generated), forgotten with the caps at
    `low` and `hard` so that they are reached: every claim a forget took that the plain loop
    keeps is of the page's class (`the_page`, at the render after `hard`), of the section's
    (`at_the_cap`, at a render past `low`), each read here by its own definition, or of the
    classes the tester reads from the guide (test_rt1_harness.declared); each begins with
    indentation and has a line that opens a fence, so no claim that cannot open one is
    taken; and the forget keeps no claim the plain loop drops. No page's forget names a
    claim after the page's class acted: the end is never reached with the parser cairn
    uses. Counted by class, a claim of more than one as the page's, then the section's; with
    the caps at twenty-four and thirty-two no generated page reaches either."""
    monkeypatch.setattr(erasure, "RENDERS_MAX", low)
    monkeypatch.setattr(erasure, "RENDERS_HARD", hard)
    counts: Counter[str] = Counter({"past sixteen": 0, "the section": 0, "the page": 0})
    counts["paged"] = 0
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
                page = store.get_page(pid, slug)
                left = page.claims if page else []
                naming = [  # this page's renders that named a claim: each but the last
                    render
                    for render in watch.renders
                    if {key(c) for c in render[1]} <= start and len(render[1]) > len(left)
                ]
                assert len(naming) <= hard + 1, (seed, step, slug)  # never the end
                counts["paged"] += len(naming) == hard + 1
                have = {key(claim) for claim in left}
                plain = [key(claim) for claim in e.kept]
                assert have <= set(plain), (seed, step, slug)
                gone = {k for k in plain if k not in have}
                if not gone:
                    continue
                assert all(t[:1] in (" ", "\t") and opens_a_fence_line(t) for t, _ in gone)
                paged = {key(c) for render in naming[hard:] for c in the_page(render)}
                capped = {
                    key(c)
                    for render in naming[low:hard]
                    if render[2] is not None
                    for c in at_the_cap(render, e.held)
                }
                stated = declared(watch, e.held, notes, slug)
                unstated = gone - paged - capped - stated
                assert not unstated, (seed, step, slug, unstated)
                counts["the page"] += len(gone & paged)
                counts["the section"] += len(gone & capped - paged)
                counts["past sixteen"] += len(gone - paged - capped)

        run.forget = forget
        try:
            run.run()
        finally:
            run.close()
    assert counts == took
