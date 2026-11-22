"""Shared test helpers extracted from test_generated_assembled_body_unit."""

import re

from markdown_it import MarkdownIt

from cairn.store import Claim

BODY = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads

LINE_END = re.compile(r"\r\n?|\n")  # where CommonMark ends a line

FENCE_RUN = re.compile(r"[ \t>]*(`{3,}|~{3,})")  # a line that is only a run: a closer


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
