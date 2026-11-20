"""No code fence of the assembled body runs from one claim into the next (third report, 4).

Pages of several claims from several notes are generated from a small grammar, assembled by
render_body and parsed whole by the Markdown parser the package reads a body with. The
property is tracked by which claim owns each line of the body:

- no fence token covers a line of a claim other than the one it starts in, or a heading the
  body writes, and none starts on a line no claim owns;
- a claim that alone holds no fence and no code is never wholly inside a fence that starts
  outside it.

Who owns a line is found here, from the claims' texts in the order the body shows them, never
from the renderer: a claim owns the lines of its text and the lines written straight after
them, which may only be fence runs (the closers the body writes); a line that is neither a
claim's, a heading's nor blank fails the test. Lines are told apart as CommonMark ends them
(CR LF, a lone CR, LF), not by "\\n" alone.

The grammar: list items of every marker (bullets, ordered, wide ones, indented ones), nested
lists, quotes, indented text at 0 to 7 spaces and after tabs, fences of both kinds at every
indentation 0 to 7, after tabs, inside list items (on the marker line and below it, in an
indented item too) and inside quotes, closed, closed too deep, closed at the margin or left
open, with an info string or a blank line inside; claims that are one line, claims that are
only a fence run, claims under headings the body writes; and claims as a composer may type
them, the same text but for what the verbatim gate leaves out (white space and CR at a line's
end, blank lines around). A page the gate refuses (compile.fence_left_open) is never saved:
it is counted, not checked. Seeded, so every run builds the same pages. Made-up notes."""

import difflib
import random
import re
from collections import Counter

import pytest
from markdown_it import MarkdownIt

from cairn.compiler import CandidatePage, render_body, render_body_before_review_four
from cairn.gate import FENCE_LEFT_OPEN, unclaimed_violations, whole
from cairn.spans import chunk, claim_id
from cairn.store import Claim

SEED, CASES = 20261120, 400
BODY = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
LINE_END = re.compile(r"\r\n?|\n")  # where CommonMark ends a line
FENCE_RUN = re.compile(r"[ \t>]*(`{3,}|~{3,})")  # a line that is only a run: a closer
WORDS = ["Ada", "Brook", "Quill", "keeps", "the", "log", "Fable", "Works", "port"]
KINDS = {"paragraph", "bullet", "code", "table", "quote"}
MARKS = ["- ", "* ", "+ ", "-   ", "1. ", "7) ", "10. "]
RUNS = ["```", "~~~", "````", "~~~~"]
LEADS = [" " * n for n in range(8)] + ["\t", " \t", "  \t"]


# The grammar


def words(rng: random.Random) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3))) + "."


def fenced(rng: random.Random, pad: str, run: str | None) -> str:
    """A fence of `run` (else of any run), every line after `pad`: closed, closed too deep,
    closed after a tab, closed at the margin, or left open; perhaps with an info string or a
    blank line inside."""
    run = run or rng.choice(RUNS)
    lines = [pad + run + rng.choice(["", "", " sh"]), pad + words(rng)]
    if rng.random() < 0.25:
        lines += ["", pad + words(rng)]
    shape = rng.randrange(5)
    if shape == 0:
        lines.append(pad + run)
    elif shape == 1:
        lines.append(pad + " " * rng.randint(1, 5) + run)
    elif shape == 2:
        lines.append(pad + "\t" + run)
    elif shape == 3:
        lines.append(run)
    return "\n".join(lines)


def inside(mark: str, text: str) -> str:
    """`text` as the content of a list item that starts with `mark`."""
    return mark + text.replace("\n", "\n" + " " * len(mark))


def block(rng: random.Random, run: str | None = None) -> str:
    """One block of a note; its fences are of `run` when the page has one run for them all."""
    kind = rng.randrange(16)
    mark = rng.choice(MARKS)
    if kind == 0:  # a list item, at the margin or up to seven spaces in
        return " " * rng.randint(0, 7) + mark + words(rng)
    if kind == 1:  # a nested list, two or three deep
        deeper = rng.choice(["", "\n" + " " * (len(mark) + 2) + "- " + words(rng)])
        return f"{mark}{words(rng)}\n{' ' * len(mark)}- {words(rng)}{deeper}"
    if kind == 2:  # a quote, perhaps of a list item
        return "> " + rng.choice(["", "- "]) + words(rng)
    if kind in (3, 4):  # indented text, one or two lines
        lead = rng.choice(LEADS)
        return lead + words(rng) + rng.choice(["", f"\n{lead}{words(rng)}"])
    if kind in (5, 6, 7):  # a fence at an indentation of 0 to 7, or after tabs
        return fenced(rng, rng.choice(LEADS), run)
    if kind == 8:  # a fence on a list item's marker line
        return inside(mark, fenced(rng, "", run))
    if kind == 9:  # a fence below a list item's first line
        return inside(mark, words(rng) + rng.choice(["\n", "\n\n"]) + fenced(rng, "", run))
    if kind == 10:  # a fence in an item indented four to seven: alone it is indented text
        return inside(" " * rng.randint(4, 7), inside("- ", fenced(rng, "", run)))
    if kind == 11:  # a fence inside a quote
        return "\n".join("> " + line for line in fenced(rng, "", run).split("\n"))
    if kind == 12:  # a claim that is only a fence run
        return rng.choice(LEADS) + (run or rng.choice(RUNS))
    if kind == 13:  # a claim under a heading the body writes
        return f"## {rng.choice(['Ports', 'Logs'])}\n\n{block(rng, run)}"
    if kind == 14:  # two passages: indented text that holds a run, then a closed fence of it
        run, deep, near = run or rng.choice(RUNS), " " * rng.randint(4, 7), " " * rng.randint(0, 5)
        return f"{deep}{run}\n{deep}{words(rng)}\n\n{near}{run}\n{near}{words(rng)}\n{near}{run}"
    return words(rng)


def typed(rng: random.Random, text: str) -> str:
    """The text as a composer may type it: the same for the verbatim gate, which leaves out
    the white space and CR at a line's end and the blank lines around."""
    edit = rng.randrange(6)
    if edit == 0:
        return text.replace("\n", "\r\n")
    if edit == 1:
        return text + "\r"
    if edit == 2:
        return text.replace("\n", "\r\r\n", 1)  # a blank line, as CommonMark ends lines
    if edit == 3:
        return text.replace("\n", "  \n", 1) + " "
    if edit == 4:
        return "\n" + text
    return text + "\n"


def page(rng: random.Random) -> tuple[list[Claim], dict]:
    """The claims of several notes, each a passage whole, and the passages by id. On four
    pages in ten every fence is of one run, so one claim's fence line may close another's."""
    claims, spans = [], {}
    run = rng.choice(RUNS) if rng.random() < 0.4 else None
    for n in range(rng.randint(2, 7)):
        text = "\n\n".join(block(rng, run) for _ in range(1 if rng.random() < 0.7 else 2))
        for span in chunk(f"n{n}", text):
            spans[span.id] = span
            if span.kind in KINDS:
                said = typed(rng, span.text) if rng.random() < 0.12 else span.text
                claims.append(Claim(id=claim_id(span.id), text=said, span_ids=(span.id,)))
    return claims, spans


def cases(seed: int = SEED, count: int = CASES):
    rng = random.Random(seed)
    for n in range(count):
        yield (f"p{n:03d}", *page(rng))


def made(texts: list[str]) -> tuple[list[Claim], dict]:
    """The claims and passages of notes given one by one, as an extractive compile makes them."""
    spans = {s.id: s for n, t in enumerate(texts) for s in chunk(f"n{n}", t)}
    claims = [
        Claim(id=claim_id(i), text=s.text, span_ids=(i,))
        for i, s in spans.items()
        if s.kind in KINDS
    ]
    return claims, spans


# Who owns each line of the body


def rows(text: str) -> list[str]:
    """The lines `text` takes when a line end follows it, as CommonMark ends lines."""
    return LINE_END.split(text + "\n")[:-1]


def shown(claims: list[Claim], spans: dict) -> list[int]:
    """The claims' indexes in the order the body shows them: those with no section first."""
    sectioned = [bool(spans[c.span_ids[0]].heading_path) for c in claims]
    return [n for n, s in enumerate(sectioned) if not s] + [n for n, s in enumerate(sectioned) if s]


def owners(body: str, claims: list[Claim], spans: dict) -> tuple[list[str], list[object]]:
    """The body's lines, and who owns each: a claim's index, "title", "heading", or None for
    a blank line between blocks. A claim owns the lines of its text, found in the order the
    body shows the claims, and the lines straight after them up to the next blank one, which
    must be fence runs. AssertionError for a line that is nobody's."""
    assert body.endswith("\n")
    lines = LINE_END.split(body)[:-1]
    owned: list[object] = [None] * len(lines)
    assert lines[0].startswith("# "), "the title"
    owned[0], at = "title", 1
    for n in shown(claims, spans):
        text = rows(claims[n].text)
        while lines[at : at + len(text)] != text:
            if lines[at].strip(" \t"):
                assert re.match(r"#{2,6} \S", lines[at]), f"line {at + 1} is nobody's"
                owned[at] = "heading"
            at += 1
        owned[at : at + len(text)] = [n] * len(text)
        at += len(text)
        while at < len(lines) and lines[at].strip(" \t"):
            assert FENCE_RUN.fullmatch(lines[at]), f"line {at + 1} is neither text nor a closer"
            owned[at] = n
            at += 1
    assert not any(line.strip(" \t") for line in lines[at:]), "lines after the last claim"
    return lines, owned


def crossings(body: str, claims: list[Claim], spans: dict) -> list[str]:
    """Every breach of the property in `body`, as claim numbers, never text."""
    lines, owned = owners(body, claims, spans)
    fences = [token for token in BODY.parse(body) if token.type == "fence"]
    found = []
    for token in fences:
        first, after = token.map
        mine = owned[first]
        if not isinstance(mine, int):
            found.append(f"a fence starts on line {first + 1}, which no claim owns")
            continue
        other = next((o for o in owned[first:after] if o is not None and o != mine), None)
        if other is not None:
            into = f"claim {other}" if isinstance(other, int) else f"a {other}"
            found.append(f"a fence of claim {mine} covers {into}")
    for n, claim in enumerate(claims):
        if any(token.type in ("fence", "code_block") for token in BODY.parse(claim.text)):
            continue
        mine = [k for k, owner in enumerate(owned) if owner == n and lines[k].strip(" \t")]
        for token in fences:
            first, after = token.map
            if owned[first] != n and all(first <= k < after for k in mine):
                found.append(f"claim {n}, no code alone, is inside a fence of {owned[first]}")
    return found


def kinds_of_crossing(body: str, claims: list[Claim], spans: dict) -> Counter:
    """What each fence that covers another claim's line is, in a body that holds some (one
    the renderer wrote before the fix): where it opens, of which run, and how it ends."""
    lines, owned = owners(body, claims, spans)
    tokens = BODY.parse(body)
    seen: Counter = Counter()
    for token in tokens:
        first, after = token.map or (0, 0)
        mine = owned[first]
        if token.type != "fence" or all(o in (None, mine) for o in owned[first:after]):
            continue
        holder = next(
            (
                t.type
                for t in tokens
                if t.level == 0 and t.map and t.nesting == 1 and t.map[0] <= first < t.map[1]
            ),
            "at the top",
        )
        last = max(k for k in range(first, after) if lines[k].strip(" \t"))
        theirs = [k for k, owner in enumerate(owned) if owner == owned[last]]
        seen["crossing"] += 1
        seen[holder] += 1
        seen["of " + token.markup[0]] += 1
        seen["ends before a later claim"] += any(o is not None for o in owned[after:])
        if owned[last] != mine and FENCE_RUN.fullmatch(lines[last]) and theirs[0] == last:
            seen["closed by the next claim's first line"] += 1
            seen["and that claim then opens a fence"] += any(
                t.type == "fence" and last < t.map[0] <= theirs[-1] for t in tokens
            )
    return seen


def refused(claims: list[Claim], spans: dict, body: str) -> bool:
    candidate = CandidatePage(slug="p", title="Ops", body=body, claims=claims)
    return any(v.startswith(FENCE_LEFT_OPEN) for v in unclaimed_violations(candidate, spans))


# How a claim reads in the body and alone: a measure, never a check


def blocks_of(text: str, first: int = 0, after: int | None = None) -> list[tuple]:
    """The block tokens that start in lines `first` to `after` of `text`, as (type, depth,
    first line, line after), the lines counted from `first` and cut at `after`."""
    after = len(rows(text)) if after is None else after
    return [
        (token.type, token.level, token.map[0] - first, min(token.map[1], after) - first)
        for token in BODY.parse(text)
        if token.map and token.nesting >= 0 and token.type != "inline"
        if first <= token.map[0] < after
    ]


def read_otherwise(body: str, claims: list[Claim], spans: dict) -> Counter:
    """How many claims' blocks read in the body other than the claim's own lines read alone:
    "claims", "otherwise" (any difference in a block's type, depth or lines), and of those
    "a list goes on" (the only difference: its first item joins the list before it) and
    "in an earlier claim's block" (its first line is inside a list, a quote or code that
    starts in a claim before it)."""
    lines, owned = owners(body, claims, spans)
    lists = ("bullet_list_open", "ordered_list_open")
    outer = [t.map for t in BODY.parse(body) if t.level == 0 and t.map and t.nesting >= 0]
    count: Counter = Counter()
    for n in range(len(claims)):
        mine = [k for k, owner in enumerate(owned) if owner == n]
        first, after = mine[0], mine[-1] + 1
        there = blocks_of(body, first, after)
        alone = blocks_of("\n".join(lines[first:after]))
        count["claims"] += 1
        if there != alone:
            count["otherwise"] += 1
            goes_on = there == [b for b in alone if b[:3] not in [(kind, 0, 0) for kind in lists]]
            count["a list goes on"] += goes_on
            count["in an earlier claim's block"] += not goes_on and any(
                start < first < end for start, end in outer
            )
    return count


# The tests


def test_the_grammar_makes_the_shapes_it_names():
    """So no test below passes for want of cases: every shape is made, many times, and each
    claim a composer typed is still the whole of its passage for the gate."""
    seen: Counter = Counter()
    for _, claims, spans in cases():
        seen += kinds_of_crossing(
            render_body_before_review_four("Ops", claims, spans), claims, spans
        )
        seen["pages"] += 1
        seen["sectioned pages"] += any(spans[c.span_ids[0]].heading_path for c in claims)
        for claim in claims:
            assert whole(claim, spans)
            alone = {token.type for token in BODY.parse(claim.text)}
            first = claim.text.lstrip("\r\n").split("\n")[0]
            seen["claims"] += 1
            seen["typed by a composer"] += claim.text != spans[claim.span_ids[0]].text
            seen["a fence alone"] += "fence" in alone
            seen["indented code alone"] += "code_block" in alone
            seen["a list alone"] += bool(alone & {"bullet_list_open", "ordered_list_open"})
            seen["a quote alone"] += "blockquote_open" in alone
            seen["one line"] += len(rows(claim.text)) == 1
            seen["starts indented"] += first[:1] in (" ", "\t")
            seen["holds a tab"] += "\t" in claim.text
            seen["only a run"] += FENCE_RUN.fullmatch(claim.text) is not None
    assert seen["pages"] == CASES
    for shape, least in {
        "sectioned pages": 100,
        "claims": 1500,
        "typed by a composer": 150,
        "a fence alone": 300,
        "indented code alone": 250,
        "a list alone": 400,
        "a quote alone": 150,
        "one line": 700,
        "starts indented": 600,
        "holds a tab": 200,
        "only a run": 60,
        # the fault, as the renderer before the fix wrote it: the reviewer's five contexts
        "crossing": 40,
        "bullet_list_open": 15,
        "ordered_list_open": 15,
        "of ~": 15,
        "of `": 15,
        "ends before a later claim": 25,
        "closed by the next claim's first line": 8,
        "and that claim then opens a fence": 5,
    }.items():
        assert seen[shape] >= least, (shape, seen[shape])


def test_no_fence_of_the_assembled_body_runs_from_one_claim_into_another():
    faults, held = [], 0
    for name, claims, spans in cases():
        body = render_body("Ops", claims, spans)
        if refused(claims, spans, body):
            held += 1
            continue
        faults.extend(f"{name}: {where}" for where in crossings(body, claims, spans))
    assert faults == []
    assert held == 0  # no page of these is one the body cannot hold


def test_only_a_page_that_held_the_fault_is_written_otherwise_than_before():
    """The renderer as it was (kept for the import) is the reference: where its body holds
    no breach, the body is the same byte for byte; where it holds one, the only difference
    is whole lines written in, each a fence run."""
    changed = closers = 0
    for name, claims, spans in cases():
        before = render_body_before_review_four("Ops", claims, spans)
        now = render_body("Ops", claims, spans)
        if not crossings(before, claims, spans):
            assert now == before, name
            continue
        changed += 1
        old, new = LINE_END.split(before), LINE_END.split(now)
        steps = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
        assert {tag for tag, *_ in steps} == {"equal", "insert"}, name
        added = [line for tag, _, _, j1, j2 in steps if tag == "insert" for line in new[j1:j2]]
        assert all(FENCE_RUN.fullmatch(line) for line in added), name
        closers += len(added)
    assert (changed, closers) == (42, 52)


def test_how_many_claims_read_in_the_body_other_than_alone():
    """A count of a class this test does not judge, pinned so a change to it is seen: a
    claim's blocks depend on the claims before it in other ways than fences (a paragraph
    becomes part of an earlier list item, an indented block joins the one before)."""
    count: Counter = Counter()
    for _, claims, spans in cases():
        count += read_otherwise(render_body("Ops", claims, spans), claims, spans)
    assert dict(count) == {
        "claims": 2639,
        "otherwise": 658,
        "a list goes on": 26,
        "in an earlier claim's block": 619,
    }


@pytest.mark.parametrize(
    "texts",
    [
        ["- item", "    ~~~\n    sample", "  After."],  # the reviewer's three notes
    ],
)
def test_the_property_holds_of_a_page_named_here(texts):
    claims, spans = made(texts)
    body = render_body("Ops", claims, spans)
    assert not refused(claims, spans, body)
    assert crossings(body, claims, spans) == []
