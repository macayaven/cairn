"""No code fence of the assembled body runs from one claim into the next (third report, 4).

Pages of several claims from several notes are generated from a small grammar (lists, nested
lists, quotes, indented text, fences of both kinds at every indentation 0 to 7 and inside list
items and quotes, tabs, claims that start with indented lines, one-line claims, and headings
the body writes), assembled by render_body and parsed whole by the Markdown parser the web
view uses. Each line of the body is owned by the claim the renderer wrote it for (a closer
line written after a claim is that claim's), by a heading the body writes, or by nobody (a
blank line between blocks); the owners are found here from the claims' texts, not from the
renderer. The property: no fence token covers a line owned by a claim other than the one it
starts in, or a heading. A page the gate refuses (compile.fence_left_open) is never saved and
is counted, not checked. Seeded, so every run builds the same pages. Made-up notes."""

import random

import pytest
from markdown_it import MarkdownIt

from cairn.compiler import CandidatePage, render_body
from cairn.gate import FENCE_LEFT_OPEN, unclaimed_violations
from cairn.spans import chunk, claim_id
from cairn.store import Claim

SEED, CASES = 20261120, 400
BODY = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
WORDS = ["Ada", "Brook", "Quill", "keeps", "the", "log", "Fable", "Works", "port"]
KINDS = {"paragraph", "bullet", "code", "table", "quote"}


def words(rng: random.Random) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3))) + "."


def fenced(rng: random.Random, pad: str) -> str:
    """A fence, every line after `pad`: closed, closed too deep, or left open."""
    run = rng.choice(["```", "~~~", "````", "~~~~"])
    lines = [pad + run, pad + words(rng)]
    shape = rng.randrange(4)
    if shape == 0:
        lines.append(pad + run)
    elif shape == 1:
        lines.append(pad + " " * rng.randint(1, 5) + run)
    elif shape == 2:
        lines.append(pad + "\t" + run)
    return "\n".join(lines)


def block(rng: random.Random) -> str:
    kind = rng.randrange(13)
    pad = " " * rng.randint(0, 7)
    if kind == 0:
        return "- " + words(rng)
    if kind == 1:
        return f"- {words(rng)}\n  - {words(rng)}" + rng.choice(["", f"\n    - {words(rng)}"])
    if kind == 2:
        return f"{rng.randint(1, 9)}. {words(rng)}"
    if kind == 3:
        return "> " + words(rng)
    if kind == 4:  # indented text, one or two lines, perhaps a tab
        lead = rng.choice([pad, "\t", " \t"])
        return lead + words(rng) + rng.choice(["", f"\n{lead}{words(rng)}"])
    if kind in (5, 6):  # a fence at an indentation of 0 to 7
        return fenced(rng, pad)
    if kind == 7:  # a fence with tabs
        return fenced(rng, rng.choice(["\t", " \t", "  \t"]))
    if kind == 8:  # a fence inside a list item, on its marker line or below it
        if rng.random() < 0.5:
            return "- " + fenced(rng, "").replace("\n", "\n  ")
        return f"- {words(rng)}\n\n" + fenced(rng, "  ")
    if kind == 9:  # a fence inside a quote
        return "\n".join("> " + line for line in fenced(rng, "").split("\n"))
    if kind == 10:  # the reviewer's shape: four spaces, a fence and a line, both indented
        return fenced(rng, rng.choice(["  ", "    ", "     "]))
    if kind == 11:  # a heading the body writes before the claims under it
        return f"## {rng.choice(WORDS)}\n\n{words(rng)}"
    return words(rng)


def page(rng: random.Random) -> tuple[list[Claim], dict]:
    """The claims of several notes, each a passage whole, and the passages by id."""
    claims, spans = [], {}
    for n in range(rng.randint(2, 6)):
        text = "\n\n".join(block(rng) for _ in range(rng.randint(1, 2)))
        for span in chunk(f"n{n}", text):
            spans[span.id] = span
            if span.kind in KINDS:
                claims.append(Claim(id=claim_id(span.id), text=span.text, span_ids=(span.id,)))
    return claims, spans


def cases(seed: int = SEED, count: int = CASES):
    rng = random.Random(seed)
    for n in range(count):
        yield (f"p{n:03d}", *page(rng))


def owners(body: str, claims: list[Claim], spans: dict) -> list[object]:
    """Who owns each line of `body`: a claim's index, "heading", or None for a blank line.
    Found from the claims' texts in the order the body shows them (those with no section
    first): each claim's text starts a block, and the lines after it up to the next blank
    line are its too (a closer)."""
    sectioned = [bool(next(spans[i].heading_path for i in c.span_ids)) for c in claims]
    order = [n for n, s in enumerate(sectioned) if not s] + [
        n for n, s in enumerate(sectioned) if s
    ]
    lines = body.split("\n")
    owned: list[object] = [None] * len(lines)
    at = 1  # the title is line 0
    for n in order:
        texts = claims[n].text.split("\n")
        while lines[at : at + len(texts)] != texts:
            if lines[at].startswith("#"):
                owned[at] = "heading"
            at += 1
        for k in range(len(texts)):
            owned[at + k] = n
        at += len(texts)
        while at < len(lines) and lines[at] != "" and not lines[at].startswith("#"):
            owned[at] = n  # a closer the body writes after the claim
            at += 1
    for k in range(at, len(lines)):
        owned[k] = "heading" if lines[k].startswith("#") else None
    return owned


def fences_cross(body: str, claims: list[Claim], spans: dict) -> list[str]:
    """Each fence token that covers a line owned by a claim other than the one it starts
    in, or a heading: "claim i into claim j" or "claim i into a heading"."""
    owned = owners(body, claims, spans)
    found = []
    for token in BODY.parse(body):
        if token.type != "fence" or not token.map:
            continue
        start, end = token.map
        mine = owned[start]
        for line in range(start, min(end, len(owned))):
            other = owned[line]
            if other is not None and other != mine:
                into = "a heading" if other == "heading" else f"claim {other}"
                found.append(f"claim {mine} into {into}")
                break
    return found


def joined_blocks(body: str, claims: list[Claim], spans: dict) -> int:
    """How many claims have a line inside a top-level block (not a fence) that starts in an
    earlier claim: a paragraph that becomes part of a list item, an indented block that
    joins an earlier one. A measure of a class out of this batch's scope, never a check."""
    owned = owners(body, claims, spans)
    joined: set[int] = set()
    for token in BODY.parse(body):
        if token.level != 0 or not token.map or token.nesting == -1 or token.type == "fence":
            continue
        start, end = token.map
        for line in range(start, min(end, len(owned))):
            if isinstance(owned[line], int) and owned[line] != owned[start]:
                joined.add(owned[line])
    return len(joined)


def refused(title: str, claims: list[Claim], spans: dict, body: str) -> bool:
    candidate = CandidatePage(slug="p", title=title, body=body, claims=claims)
    return any(v.startswith(FENCE_LEFT_OPEN) for v in unclaimed_violations(candidate, spans))


def test_no_fence_of_the_assembled_body_runs_from_one_claim_into_another():
    faults, checked, held = [], 0, 0
    for name, claims, spans in cases():
        body = render_body("Ops", claims, spans)
        if refused("Ops", claims, spans, body):
            held += 1
            continue
        checked += 1
        faults.extend(f"{name}: {where}" for where in fences_cross(body, claims, spans))
    assert checked > CASES * 3 // 4, (checked, held)
    assert faults == []


@pytest.mark.parametrize(
    "texts",
    [
        ["- item", "    ~~~\n    sample", "  After."],  # the reviewer's three notes
    ],
)
def test_the_property_finds_the_reviewers_page(texts):
    spans = {s.id: s for n, t in enumerate(texts) for s in chunk(f"n{n}", t)}
    claims = [Claim(id=claim_id(i), text=s.text, span_ids=(i,)) for i, s in spans.items()]
    body = render_body("Ops", claims, spans)
    assert not refused("Ops", claims, spans, body)
    assert fences_cross(body, claims, spans) == []
