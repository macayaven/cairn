"""A forget that brings code fences together takes the claims at fault and no other: a
differential test (fifth report, must-fix 1; sixth report, must-fix 1; CORE9A).

The oracle is the plain loop, with no ceiling and no shortcut: title the page with the
topic of the oldest note its claims still cite, as a forget does; render the body
(compiler.left_to_run); when it names a claim whose fence runs on, drop that one claim and
do it again. What it leaves is what a forget should leave, and it costs a render for each
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

The notes spell their one topic two or three ways (`Ops`, `OPS`, `ops`: one page), and on
half the pages the heading says the topic too, so that its claims are written with no
heading for as long as the title is spelled the same. On more than half, the oldest note is
one that may leave whole, most often a single claim beside a paragraph at the margin: it is
stored first and filed in its place, as two writers interleave (`filed`). So the title, and
with it the body's order, can change while the forget works. One page in five is the sixth
report's page, generated (`said`): only the tail's heading says the title, the claim before
the paragraph is the oldest note, and on half of them the claims before that one are the
second oldest, spelled another way again. On the release before this rule the search finds
nine pages where a claim leaves that the oracle keeps, 44 claims in all.

For each page (`compared`, `beside`):

1. Survivors. The forget leaves what the oracle leaves, but for one class of claim, which
   may leave besides, and only when the oracle drops more than CLOSERS_MAX claims: a claim
   the body writes after the claim the oracle names seventeenth, in the body it named it
   in, that starts with indentation and has a line that opens a fence
   (erasure._may_open_a_fence). Every claim the forget took and the oracle kept is of that
   class, and it keeps no claim the oracle dropped. The page's title and body are those of
   its last render.
2. Cost. A forget renders no more often than the oracle (calls of compiler.left_to_run);
   in a render the body is parsed whole at most CLOSERS_MAX + 1 times and once for each
   question, and a question's text is never more than twice the body. No number of renders
   is promised: the most here is pinned, and test_forget_changed_title_unit.py has a page
   that takes more.
3. The export of the result imports, and the imported page's claims are the same.

What this search does not hold, and pages made for it show (test_forget_siblings_unit.py,
"What the declared class costs besides itself"): once a claim of the class has left, a
claim before the fault whose fence then covers what follows leaves too, though the oracle
kept it, and one the oracle dropped for covering such a claim stays. None of the pages
here comes to either.

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
SPELLINGS = ("Ops", "OPS", "ops")  # one topic, as three writers spell it: one page
STEPS = "## Steps\n\n"
MARKERS = ("- ", "-   ", "1. ")
INLINE = "Ada keeps `port 80` open."
FENCED = "~~~text\nBrook owns the rollback.\n~~~"
# In one render the body is parsed CLOSERS_MAX + 1 times at most, and once for each question
# asked of the claims before the one named (erasure._before_it): ASKS_MAX of them at most
# on these pages. No number of renders is promised: a forget never renders more often than
# the plain loop, and RENDERS_SEEN is the most it took here.
ASKS_MAX = 4
RENDERS_SEEN = CLOSERS_MAX + 2


def oracle(notes, claims: list[Claim], spans) -> tuple[list[Claim], list[Claim], dict[str, int]]:
    """(the claims the plain loop leaves; those it drops, in the order it drops them; where
    the body in which it named the seventeenth wrote each claim then left, by id). `notes`
    are the project's, the oldest stored first: the page is titled again in every render
    with the topic of the oldest note the claims left cite, as a forget titles it."""
    left, dropped, order = list(claims), [], {}
    while True:
        cited = {spans[i].source_id for claim in left for i in claim.span_ids}
        oldest = next(note for note in notes if note.id in cited)
        body, place = compiler.left_to_run(oldest.topic or oldest.title, left, spans)
        if not place:
            return left, dropped, order
        if len(dropped) == CLOSERS_MAX:
            order = {claim.id: body.index(f"\n{claim.text}\n") for claim in left}
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
    notes: list[tuple[str, str]]  # (text, its topic as spelled), in the order they are filed
    forget: int  # the place of the note to forget
    sites: int  # the places where the forget brings claims together
    old: tuple[
        int, ...
    ]  # the places of the notes stored first, the oldest first, though filed there


def said(rng: random.Random, numbers) -> Plan:
    """A page whose tail's heading says the title (the sixth report's page, generated). A
    list with no section spends the body's closers. Then a second list item, claims under
    it, and the last of them a note of its own and the oldest, so the title is its topic;
    a paragraph at the margin; and a tail under a heading that most often spells the topic
    as that note does, and is not written while the title says the same. Without the
    oldest note's claim the title is the next oldest note's, and the heading may be
    written, between the tail and the claims that stood before it. On half of these pages
    the claims before are the second oldest note, spelled another way again."""
    one, other, third = rng.sample(SPELLINGS, 3)
    head = rng.choice(MARKERS)
    count = rng.choice((2, 3, 5, 8, 17, 20))
    before = before_it(rng, numbers, len(head), count, 0, rng.choice(KINDS[:3]))
    after = after_it(rng, numbers, rng.choice((1, 2, 3)), "tail")
    spent = [*(running(next(numbers), 4) for _ in range(CLOSERS_MAX)), "  End of first."]
    heading = rng.choice((one, one, one, other, "Steps"))
    notes = [
        "- first",
        "\n\n".join(spent),
        f"{head}list {next(numbers)}",
        "\n\n".join(before[:-1]),
        before[-1],
        f"Between {next(numbers)}.",
        f"## {heading}\n\n" + "\n\n".join(after),
    ]
    spelled = [rng.choice((one, one, other)) for _ in notes]
    spelled[4], old = one, (4,)
    if rng.random() < 0.5:
        spelled[3], old = rng.choice((other, third)), (4, 3)
    return Plan(list(zip(notes, spelled, strict=True)), 5, 1, old)


def planned(case: int) -> Plan:
    rng = random.Random(f"{SEED}:{case}")
    numbers = itertools.count()
    if case % 5 == 3:
        return said(rng, numbers)
    # closers a list under "First" spends before the forget: none, half, all but one, all
    spent = CLOSERS_MAX if case in LARGE else rng.choice((0, 0, 8, 15, 16, 16, 16))
    spare = CLOSERS_MAX - spent
    where = rng.choice((("top",), ("steps",), ("top", "steps"), ("top", "steps")))
    if case in LARGE:  # the list under "First" is written before "Steps", and no other
        where = ("steps",)
    # The second heading says the topic on half the pages: its claims are written with no
    # heading, among those with no section, for as long as the title is spelled as it is.
    under = "Steps" if case in LARGE else rng.choice(("Steps", "Steps", "Steps", *SPELLINGS))
    places = {"top": "", "first": "## First\n\n", "steps": f"## {under}\n\n"}  # the body's order
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
        texts = {place: "\n\n".join(parts[place]) for place in places if parts.get(place)}
        top = texts.pop("top", "")
        below = "\n\n".join(places[place] + text for place, text in texts.items())
        if top and below:
            return [f"{top}\n\n{below}"] if rng.random() < 0.6 else [below, top]
        return [text for text in (top, below) if text]

    # On half the pages one claim that may leave is a note of its own, the oldest: the last
    # before a paragraph at the margin or the first after it. The title is its topic until
    # it leaves, so the title can change while the forget works.
    lone = None
    if rng.random() < 0.5:
        place = rng.choice(where)
        side = befores if rng.random() < 0.5 else afters
        if len(side[place]) > 1:
            lone = (side is afters, places[place] + side[place].pop(-1 if side is befores else 0))

    # Every claim under "First" is stored before any under the second heading, so the body
    # writes each heading once; a list item's note holds nothing after the item there.
    notes = notes_of({"top": heads["top"], "first": ["- item"] if spent else []})
    notes += notes_of(
        {"top": befores["top"], "first": first if spent else [], "steps": heads["steps"]}
    )
    may_leave = list(range(len(notes), len(notes) + len(notes_of({"steps": befores["steps"]}))))
    notes += notes_of({"steps": befores["steps"]})
    oldest = None
    if lone and not lone[0]:
        oldest = len(notes)
        notes.append(lone[1])
    forget = len(notes)
    notes.append("\n\n".join(places[place] + betweens[place][0] for place in betweens))
    if lone and lone[0]:
        oldest = len(notes)
        notes.append(lone[1])
    may_leave += range(len(notes), len(notes) + len(notes_of(afters)))
    notes += notes_of(afters)
    # One topic spelled several ways. The oldest note's spelling is not the first note's;
    # where no claim is a note of its own, a whole note that may leave is sometimes the oldest.
    spelled = [rng.choice(SPELLINGS[:2] if rng.random() < 0.8 else SPELLINGS) for _ in notes]
    if oldest is None and may_leave and rng.random() < 0.3:
        oldest = rng.choice(may_leave)
    if oldest is not None:
        spelled[oldest] = rng.choice([spelling for spelling in SPELLINGS if spelling != spelled[0]])
    return Plan(
        list(zip(notes, spelled, strict=True)),
        forget,
        len(where),
        () if oldest is None else (oldest,),
    )


# One page, forgotten


class Compared(NamedTuple):
    start: list[Claim]  # the page's claims without the forgotten note's
    order: dict[str, int]  # where the body wrote each claim when the oracle named its 17th
    expected: list[Claim]  # the oracle's survivors
    dropped: list[Claim]  # what the oracle drops, in its order
    actual: list[Claim]  # the forget's survivors
    titles: list[str]  # of each render of the forget
    asks: int  # the most questions (erasure.fences_reaching) in one render


def page(store: Store):
    return store.get_page(store.get_project("alpha").id, "ops")


def filed(store: Store, plan: Plan) -> list[str] | None:
    """The ids of the plan's notes, each filed on the page `ops`; None when a note is
    refused its page. The notes at `plan.old` are stored first and filed in their places,
    as writers interleave: the notes before one are filed while it is at its gate, and its
    own save, found stale, is made again."""
    ids: dict[int, str | None] = {}

    def file(place: int, first=None) -> None:
        waiting = [first] if first else []

        def gate(candidate, spans):
            if waiting:
                waiting.pop()()
            return lint_gate(candidate, spans)

        text, topic = plan.notes[place]
        answer = Memory(store, gate).note("alpha", text, title="Field notes", topic=topic)
        saved = (answer["status"], answer["page"]) == ("saved", "ops")
        ids[place] = answer["note_id"] if saved else None

    def before(end: int, old: tuple[int, ...]) -> None:
        """File the notes before `end` that are not filed yet, the first of `old` stored
        before the others: each of `old` stands before the one stored before it."""
        if old:
            file(old[0], first=lambda: before(old[0], old[1:]))
        for place in range(end):
            if place not in ids:
                file(place)

    before(len(plan.notes), plan.old)
    return None if None in ids.values() else [ids[place] for place in range(len(plan.notes))]


def compared(plan: Plan) -> Compared | None:
    """The plan's page, its note forgotten, beside the oracle; None when a note is refused
    its page."""
    with closing(Store(":memory:")) as store:
        project = store.create_project("alpha")
        ids = filed(store, plan)
        if ids is None:
            return None
        notes = store.list_notes(project.id)  # the oldest stored first
        assert [note.id for note in notes[: len(plan.old)]] == [ids[place] for place in plan.old]
        before = page(store)
        gone = {span.id for span in store.note_spans(ids[plan.forget])}
        start = [claim for claim in before.claims if not gone & set(claim.span_ids)]
        spans = store.get_spans(i for claim in start for i in claim.span_ids)
        expected, dropped, order = oracle(notes, start, spans)

        reads, asked, titles, bodies = [], [], [], []  # of each render
        real_parse, real_render, real_ask = (
            MarkdownIt.parse,
            compiler.left_to_run,
            erasure.fences_reaching,
        )

        def parse(reader, source, *args, **kwargs):
            if source.startswith(tuple(f"# {spelling}\n\n" for spelling in SPELLINGS)):
                reads[-1] += 1
            return real_parse(reader, source, *args, **kwargs)

        def render(title, claims, held):
            reads.append(0)
            asked.append(0)
            titles.append(title)
            bodies.append(real_render(title, claims, held))
            return bodies[-1]

        def ask(before, blocks, line):
            asked[-1] += 1
            # a question is never more than twice the body it is asked about
            built = len(before) + sum(len(block) + len(line) + 4 for block in blocks)
            assert built <= 2 * len(bodies[-1][0]), (built, len(bodies[-1][0]))
            return real_ask(before, blocks, line)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(MarkdownIt, "parse", parse)
            patch.setattr(compiler, "left_to_run", render)
            patch.setattr(erasure, "fences_reaching", ask)
            assert store.forget_note(ids[plan.forget]).rebuilt == ("ops",)
        # a render parses the body CLOSERS_MAX + 1 times at most, and once for a question
        assert all(n - q <= CLOSERS_MAX + 1 for n, q in zip(reads, asked, strict=True))
        after = page(store)
        actual = list(after.claims)
        assert after.body == bodies[-1][0] and after.title == titles[-1]
        archive = io.BytesIO()
        store.export(archive)
        with closing(Store(":memory:")) as target:
            target.import_export(io.BytesIO(archive.getvalue()))
            assert list(page(target).claims) == actual  # (3)
        return Compared(start, order, expected, dropped, actual, titles, max(asked))


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
    """The places of `claims` among those the forget starts from: never their text."""
    return [found.start.index(claim) for claim in claims]


def test_every_generated_forget_leaves_what_the_plain_loop_leaves_but_the_declared_class():
    pages = refused = sites = faulted = past = declared_claims = 0
    retitled = longer = largest = most_asks = most_dropped = 0
    for case in range(PAGES):
        plan = planned(case)
        found = compared(plan)
        if found is None:  # a note the gates refuse: not a page a user has
            refused += 1
            continue
        declared, others, stayed = beside(found)
        where = f"page {case}: the oracle dropped {len(found.dropped)}"
        assert not others, f"{where}; taken and not of the class: {places(found, others)}"
        assert not stayed, f"{where}; kept though the oracle dropped: {places(found, stayed)}"
        renders = len(found.titles)
        assert renders <= len(found.dropped) + 1, f"{where}; {renders} renders"
        assert found.asks <= ASKS_MAX, f"{where}; {found.asks} questions in one render"
        pages, sites = pages + 1, sites + plan.sites
        faulted, past = faulted + bool(found.dropped), past + (len(found.dropped) > CLOSERS_MAX)
        declared_claims += len(declared)
        retitled += len(set(found.titles)) > 1
        longer += renders > CLOSERS_MAX + 2
        largest, most_dropped = max(largest, renders), max(most_dropped, len(found.dropped))
        most_asks = max(most_asks, found.asks)
    # What the search ran, pinned: the pages, those left out for a refused note, and the
    # places a forget brought claims together; the pages where the oracle dropped a claim,
    # and more than CLOSERS_MAX; the most it dropped on one page; the pages whose title
    # changed while the forget worked, and those it rendered more than CLOSERS_MAX + 2
    # times; the most renders, and questions in a render; the claims of the declared class
    # it took. The class taken past the ceiling holds to the title's text (the eighth
    # round): loosened like the claims before, it took one more claim on two pages (82,
    # 199) and saved no render.
    assert (pages, refused, sites, faulted, past, most_dropped) == (PAGES, 0, 440, 113, 26, 300)
    assert (retitled, longer, largest, most_asks) == (15, 0, RENDERS_SEEN, ASKS_MAX)
    assert declared_claims == 41
