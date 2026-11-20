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

import random
import re
from collections import Counter

import pytest
from markdown_it import MarkdownIt

from cairn.compiler import CandidatePage, render_body
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


def fenced(rng: random.Random, pad: str) -> str:
    """A fence, every line after `pad`: closed, closed too deep, closed after a tab, closed
    at the margin, or left open; perhaps with an info string or a blank line inside."""
    run = rng.choice(RUNS)
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


def block(rng: random.Random) -> str:
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
        return fenced(rng, rng.choice(LEADS))
    if kind == 8:  # a fence on a list item's marker line
        return inside(mark, fenced(rng, ""))
    if kind == 9:  # a fence below a list item's first line
        return inside(mark, words(rng) + rng.choice(["\n", "\n\n"]) + fenced(rng, ""))
    if kind == 10:  # a fence in an item indented four to seven: alone it is indented text
        return inside(" " * rng.randint(4, 7), inside("- ", fenced(rng, "")))
    if kind == 11:  # a fence inside a quote
        return "\n".join("> " + line for line in fenced(rng, "").split("\n"))
    if kind == 12:  # a claim that is only a fence run
        return rng.choice(LEADS) + rng.choice(RUNS)
    if kind == 13:  # a claim under a heading the body writes
        return f"## {rng.choice(['Ports', 'Logs'])}\n\n{block(rng)}"
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
    """The claims of several notes, each a passage whole, and the passages by id."""
    claims, spans = [], {}
    for n in range(rng.randint(2, 7)):
        text = "\n\n".join(block(rng) for _ in range(1 if rng.random() < 0.7 else 2))
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
    "a list goes on" (the only difference: its first item joins the list before it)."""
    lines, owned = owners(body, claims, spans)
    lists = ("bullet_list_open", "ordered_list_open")
    count: Counter = Counter()
    for n in range(len(claims)):
        mine = [k for k, owner in enumerate(owned) if owner == n]
        first, after = mine[0], mine[-1] + 1
        there = blocks_of(body, first, after)
        alone = blocks_of("\n".join(lines[first:after]))
        count["claims"] += 1
        if there != alone:
            count["otherwise"] += 1
            same_but_lists = [b for b in there if b[0] not in lists] == [
                b for b in alone if not (b[0] in lists and b[2] == 0)
            ]
            count["a list goes on"] += same_but_lists
    return count


# The tests


def test_the_grammar_makes_the_shapes_it_names():
    """So no test below passes for want of cases: every shape is made, many times, and each
    claim a composer typed is still the whole of its passage for the gate."""
    seen: Counter = Counter()
    for _, claims, spans in cases():
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
