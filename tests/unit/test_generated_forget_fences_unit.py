"""A forget that brings code fences together takes the claims at fault and no other: a
differential test (fifth report, must-fix 1; CORE9A).

The oracle is the plain loop, with no ceiling and no shortcut: render the body
(compiler.left_to_run); when it names a claim whose fence runs on, drop that one claim and
render again. What it leaves is what a forget should leave, and it costs a render for each
claim it drops, which is why a forget does not run it.

Each page is generated from a seed, filed through Memory.note one note at a time (every
note is saved: these are pages a user has), and one note is forgotten through
Store.forget_note: the note of the paragraphs at the margin that kept what stands after them
apart from the lists before them. A page has one or two such places (one among the claims
with no section, one under a heading: the two the body writes in their own order, whatever
order they were stored in), and around each:

- before it, under a list item whose marker is `- `, `-   ` or `1. `: sibling items with a
  fenced example that no line closes (the fence ends where the item ends), their markers of
  differing widths, some nested in an item that has no fence; indented examples with their
  own closing line, before, between and after them; indented fences that run into the next
  claim, which spend the body's closers; plain indented lines;
- after it: tails at indentations from two to twelve, plain and fence-capable; fences that
  run on one after another; examples with their own closing line; more siblings;
- on most pages a list under another heading, written before it, that has spent eight,
  fifteen or all sixteen of the body's closers before the forget.

For each page (`compared`, `beside`):

1. Survivors. The forget leaves what the oracle leaves, but for one class of claim, which
   may leave besides, and only when the oracle drops more than CLOSERS_MAX claims: a claim
   the body writes after the claim the oracle names seventeenth, that starts with
   indentation and has a line that opens a fence (erasure._may_open_a_fence). Every claim
   the forget took and the oracle kept is of that class. And a claim the oracle dropped
   stays only where a claim of the class has left: the oracle named it after its
   seventeenth, for a fence that covered that claim and now covers nothing.
2. Renders (calls of compiler.left_to_run) are at most CLOSERS_MAX + 2, whatever the number
   of siblings, and the body is parsed whole at most PARSES_MAX times.
3. The export of the result imports, and the imported page's claims are the same.

What this search does not hold, and a page made for it shows
(test_forget_siblings_unit.py, "What the declared class costs besides itself"): once a
claim of the class has left, a claim before the fault whose fence then covers what follows
leaves too, though the oracle kept it. None of the pages here comes to that.

Seeded: a page depends on its number alone, so one page replays without the others. A
failure names the page's number and places, never a note's text. Made-up notes."""

import io
import itertools
import random
from contextlib import closing
from typing import NamedTuple

import pytest
from markdown_it import MarkdownIt

from cairn import compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import CLOSERS_MAX
from cairn.store import Claim, Store, erasure

SEED, PAGES = 20261122, 300
LARGE = {7: 300, 61: 120, 211: 64}  # the pages with that many siblings in a row
TITLE = "Ops"
STEPS = "## Steps\n\n"
PLACES = {"top": "", "first": "## First\n\n", "steps": STEPS}  # in the body's order
MARKERS = ("- ", "-   ", "1. ")
INLINE = "Ada keeps `port 80` open."
FENCED = "~~~text\nBrook owns the rollback.\n~~~"
# A forget's renders at most, and its parses of a whole body: in each render the body at
# most CLOSERS_MAX + 1 times, and in each render that names a claim the questions asked of
# the claims before it (erasure._before_it), at most ASKS_MAX of them on these pages.
RENDERS_MAX = CLOSERS_MAX + 2
ASKS_MAX = 4
PARSES_MAX = RENDERS_MAX * (CLOSERS_MAX + 1) + (CLOSERS_MAX + 1) * ASKS_MAX


def oracle(title: str, claims: list[Claim], spans) -> tuple[list[Claim], list[Claim]]:
    """(the claims the plain loop leaves, those it drops in the order it drops them)."""
    left, dropped = list(claims), []
    while True:
        _, place = compiler.left_to_run(title, left, spans)
        if not place:
            return left, dropped
        dropped.append(left.pop(place - 1))


# The pages


def pad(indent: int) -> str:
    return " " * indent


def sibling(rng: random.Random, n: int, indent: int, marker: str) -> str:
    """A list item whose fenced example no line closes: the fence ends with the item."""
    inner = pad(indent + len(marker))
    if rng.random() < 0.5:
        return f"{pad(indent)}{marker}~~~\n{inner}sample {n}"
    return f"{pad(indent)}{marker}item {n}\n{inner}~~~\n{inner}sample {n}"


def closed(n: int, indent: int) -> str:
    return f"{pad(indent)}```text\n{pad(indent)}closed {n}\n{pad(indent)}```"


def running(n: int, indent: int) -> str:
    return f"{pad(indent)}~~~\n{pad(indent)}running {n}"


def plain(n: int, indent: int) -> str:
    return f"{pad(indent)}plain {n}"


KINDS = (
    ["sibling"],
    ["sibling"] * 8 + ["closed", "wide"],
    ["sibling"] * 4 + ["under", "closed", "plain", "running", "wide"],
    ["closed", "plain", "running", "under"],
)
MOSTLY_SIBLINGS = ["sibling"] * 150 + ["closed", "wide"]


def before_it(rng: random.Random, numbers, column: int, count: int, spare: int, kinds) -> list[str]:
    """What stands under a list item whose text starts at `column`, before the paragraph at
    the margin: `count` claims of `kinds`. About `spare` of them stand where a fence before
    them is open, so the body spends a closer there; the others stand clear of it."""
    base = max(4, column + rng.choice((0, 0, 1)))
    marker = rng.choice(MARKERS)
    out, indent, inner = [], base, None  # inner: the text column of an item whose fence is open
    for _ in range(count):
        kind, n = rng.choice(kinds), next(numbers)
        if kind == "running" and spare <= 0:
            kind = "plain"
        if kind in ("sibling", "wide"):  # wide: a sibling whose marker is another width
            mark = marker if kind == "sibling" else rng.choice([m for m in MARKERS if m != marker])
            out.append(sibling(rng, n, indent, mark))
            inner = indent + len(mark)
        elif kind == "under":  # an item with no fence: what follows is nested in it
            out.append(f"{pad(indent)}{marker}item {n}")
            indent, inner = indent + len(marker), None
        else:
            at = indent
            if inner is not None and spare > 0 and rng.random() < 0.5:
                at, spare = inner + rng.choice((0, 2)), spare - 1
            if kind == "running":
                spare -= 1
            out.append({"closed": closed, "running": running, "plain": plain}[kind](n, at))
            inner = None
        if rng.random() < 0.1:
            indent = base
    return out


def after_it(rng: random.Random, numbers, count: int, shape: str) -> list[str]:
    """What stands after the paragraph at the margin: until the forget, indented text."""
    if shape == "tail":
        out = [plain(next(numbers), rng.choice((2, 4, 5, 6, 7, 8, 8, 10, 12)))]
        if rng.random() < 0.3:
            out.append(rng.choice((plain, closed, running))(next(numbers), rng.choice((4, 8))))
        return out
    if shape == "running":
        at = rng.choice((4, 4, 6, 8))
        out = [running(next(numbers), at) for _ in range(count)]
        for _ in range(rng.choice((0, 1, 1, 2, 3))):
            kind = rng.choice((plain, plain, closed, running))
            out.append(kind(next(numbers), rng.choice((2, 4, 6, 8, 10))))
        return out
    out = []
    for _ in range(count):
        kind, n = rng.choice("ppcrrs"), next(numbers)
        indent = rng.choice((4, 5, 6, 8, 10))
        if kind == "p":
            out.append(plain(n, rng.choice((2, indent))))
        elif kind == "c":
            out.append(closed(n, indent))
        elif kind == "r":
            out.append(running(n, indent))
        else:
            out.append(sibling(rng, n, indent, rng.choice(MARKERS)))
    return out


SHAPES = ("tail", "tail", "tail", "running", "running", "mixed", "mixed")


class Plan(NamedTuple):
    notes: list[str]  # in the order they are filed
    forget: int  # the place of the note to forget
    sites: int  # the places where the forget brings claims together


def planned(case: int) -> Plan:
    rng = random.Random(f"{SEED}:{case}")
    numbers = itertools.count()
    # closers a list under "First" spends before the forget: none, half, all but one, all
    spent = CLOSERS_MAX if case in LARGE else rng.choice((0, 0, 8, 15, 16, 16, 16))
    spare = CLOSERS_MAX - spent
    where = rng.choice((("top",), ("steps",), ("top", "steps"), ("top", "steps")))
    if case in LARGE:  # the list under "First" is written before "Steps", and no other
        where = ("steps",)
    heads, befores, betweens, afters = {}, {}, {}, {}
    for place in ("top", "steps"):
        head = rng.choice(MARKERS)
        heads[place] = [f"{head}list {next(numbers)}"]
        large = LARGE.get(case) if place == where[0] else None
        count = large or rng.choice((0, 1, 2, 3, 5, 8, 12, 17, 20, 24, 36))
        runs = 0 if large else min(spare, rng.choice((0, 0, 0, 1, 3, 8, 16)))
        spare -= runs
        kinds = rng.choice(KINDS)
        if large:
            kinds = ["sibling"] if large == max(LARGE.values()) else MOSTLY_SIBLINGS
        befores[place] = before_it(rng, numbers, len(head), count, runs, kinds)
        afters[place] = [plain(next(numbers), 2)] if rng.random() < 0.5 else []
        if place in where:
            betweens[place] = [f"Between {next(numbers)}."]
            count = rng.choice((1, 2, 3, 5, 9, 17, 18, 20))
            afters[place] = after_it(rng, numbers, count, rng.choice(SHAPES))
            if large:  # held by every sibling
                afters[place] = [plain(next(numbers), 12)]
    first = [*(running(next(numbers), 4) for _ in range(spent)), "  End of first."]
    if rng.random() < 0.5:
        heads["top"].insert(0, INLINE)
    if rng.random() < 0.5:
        afters["top"] += [FENCED, "Quill keeps `short` labels, even ~~~ in prose."]

    def notes_of(parts: dict[str, list[str]]) -> list[str]:
        """The claims with no section and those under the headings as one note, or as two
        with those under the headings stored first: the body's order is not the stored one."""
        texts = {place: "\n\n".join(parts[place]) for place in PLACES if parts.get(place)}
        top = texts.pop("top", "")
        under = "\n\n".join(PLACES[place] + text for place, text in texts.items())
        if top and under:
            return [f"{top}\n\n{under}"] if rng.random() < 0.6 else [under, top]
        return [text for text in (top, under) if text]

    # Every claim under "First" is stored before any under "Steps", so the body writes each
    # heading once; a list item's note holds nothing after the item under its heading.
    notes = notes_of({"top": heads["top"], "first": ["- item"] if spent else []})
    notes += notes_of(
        {"top": befores["top"], "first": first if spent else [], "steps": heads["steps"]}
    )
    notes += notes_of({"steps": befores["steps"]})
    forget = len(notes)
    notes += [
        notes_of(betweens)[0]
        if len(betweens) == 1
        else "\n\n".join([betweens["top"][0], STEPS + betweens["steps"][0]])
    ]
    notes += notes_of(afters)
    return Plan(notes, forget, len(where))


# One page, forgotten


class Compared(NamedTuple):
    start: list[Claim]  # the page's claims without the forgotten note's
    order: dict[str, int]  # where the body the forget starts from writes each claim, by id
    expected: list[Claim]  # the oracle's survivors
    dropped: list[Claim]  # what the oracle drops, in its order
    actual: list[Claim]  # the forget's survivors
    renders: int
    parses: int  # of a whole body
    asks: int  # the most questions (erasure.fences_reaching) in one render


def page(store: Store):
    return store.get_page(store.get_project("alpha").id, "ops")


def compared(notes: list[str], forget: int) -> Compared | None:
    """The page of `notes`, the note at `forget` forgotten, beside the oracle; None when a
    note is refused its page."""
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        memory, ids = Memory(store, lint_gate), []
        for text in notes:
            answer = memory.note("alpha", text, title=TITLE, topic=TITLE)
            if answer["status"] != "saved":
                return None
            ids.append(answer["note_id"])
        before = page(store)
        gone = {span.id for span in store.note_spans(ids[forget])}
        start = [claim for claim in before.claims if not gone & set(claim.span_ids)]
        spans = store.get_spans(i for claim in start for i in claim.span_ids)
        body = compiler.render_body(before.title, start, spans)
        order = {claim.id: body.index(f"\n{claim.text}\n") for claim in start}
        expected, dropped = oracle(before.title, start, spans)

        reads, asked = [], []  # in each render: parses of a whole body, questions
        real_parse, real_render, real_ask = (
            MarkdownIt.parse,
            compiler.left_to_run,
            erasure.fences_reaching,
        )

        def parse(reader, source, *args, **kwargs):
            if source.startswith(f"# {TITLE}\n\n"):
                reads[-1] += 1
            return real_parse(reader, source, *args, **kwargs)

        def render(title, claims, held):
            reads.append(0)
            asked.append(0)
            return real_render(title, claims, held)

        def ask(*args):
            asked[-1] += 1
            return real_ask(*args)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(MarkdownIt, "parse", parse)
            patch.setattr(compiler, "left_to_run", render)
            patch.setattr(erasure, "fences_reaching", ask)
            assert store.forget_note(ids[forget]).rebuilt == ("ops",)
        # a render parses the body CLOSERS_MAX + 1 times at most, and once for a question
        assert all(n - q <= CLOSERS_MAX + 1 for n, q in zip(reads, asked, strict=True))
        actual = list(page(store).claims)
        archive = io.BytesIO()
        store.export(archive)
        with closing(Store(":memory:")) as target:
            target.import_export(io.BytesIO(archive.getvalue()))
            assert list(page(target).claims) == actual  # (3)
        return Compared(start, order, expected, dropped, actual, len(reads), sum(reads), max(asked))


def beside(found: Compared) -> tuple[list[Claim], list[Claim], list[Claim]]:
    """The claims the forget took and the oracle kept, as (those of the declared class,
    the others), and the claims the oracle dropped and the forget kept."""
    kept, wanted = {c.id for c in found.actual}, {c.id for c in found.expected}
    took = [c for c in found.start if c.id not in kept and c.id in wanted]
    stayed = [c for c in found.start if c.id in kept and c.id not in wanted]
    declared = []
    if len(found.dropped) > CLOSERS_MAX:
        fault = found.order[found.dropped[CLOSERS_MAX].id]  # the seventeenth it names
        declared = [
            c for c in took if found.order[c.id] > fault and erasure._may_open_a_fence(c.text)
        ]
    return declared, [c for c in took if c not in declared], stayed


def places(found: Compared, claims: list[Claim]) -> list[int]:
    """Where the body the forget starts from writes `claims`: their places, never text."""
    ranked = sorted(found.order.values())
    return [ranked.index(found.order[claim.id]) for claim in claims]


def test_every_generated_forget_leaves_what_the_plain_loop_leaves_but_the_declared_class():
    pages = sites = faulted = past = declared_claims = stayed_claims = 0
    largest = most_asks = most_parses = most_dropped = 0
    for case in range(PAGES):
        plan = planned(case)
        found = compared(plan.notes, plan.forget)
        assert found is not None, f"page {case}: a note was refused its page"
        declared, others, stayed = beside(found)
        where = f"page {case}: the oracle dropped {len(found.dropped)}"
        assert not others, f"{where}; taken and not of the class: {places(found, others)}"
        # A claim the oracle dropped stays only where a claim of the class had left: the
        # oracle named it after its seventeenth, for a fence that covered that claim.
        late = found.dropped[CLOSERS_MAX:]
        assert all(claim in late for claim in stayed) and (declared or not stayed), (
            f"{where}; kept though the oracle dropped: {places(found, stayed)}"
        )
        assert found.renders <= RENDERS_MAX, f"{where}; {found.renders} renders"
        assert found.asks <= ASKS_MAX, f"{where}; {found.asks} questions in one render"
        assert found.parses <= PARSES_MAX, f"{where}; {found.parses} parses of the body"
        pages, sites = pages + 1, sites + plan.sites
        faulted, past = faulted + bool(found.dropped), past + (len(found.dropped) > CLOSERS_MAX)
        declared_claims, stayed_claims = (
            declared_claims + len(declared),
            stayed_claims + len(stayed),
        )
        largest, most_dropped = max(largest, found.renders), max(most_dropped, len(found.dropped))
        most_asks, most_parses = max(most_asks, found.asks), max(most_parses, found.parses)
    # What the search ran, pinned: the pages and the places a forget brought claims
    # together; the pages where the oracle dropped a claim, and more than CLOSERS_MAX; the
    # most it dropped on one page, and the most renders, questions in a render and parses
    # of a body a forget took; the claims of the declared class it took, and those it kept
    # that the oracle dropped.
    assert (pages, sites, faulted, past, most_dropped) == (PAGES, 471, 116, 33, 300)
    assert (largest, most_asks, most_parses) == (RENDERS_MAX, ASKS_MAX, 340)
    assert (declared_claims, stayed_claims) == (140, 1)
