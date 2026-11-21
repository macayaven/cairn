"""The verbatim gate's comparison as it stood before CORE5, kept for the import alone.

An archive written before CORE5 may hold a page version whose claim is a fold of passages
that differ only by white space: a tidy, the arranger or a model's draft saved it, and every
save went through the gate of its day. Today such a claim is not the whole of every passage
it cites (cairn.gate.whole), so the import (store.portable._page_version) accepts a version
when each claim is whole by today's rule or by `whole_before_core5`, and rewrites nothing; a
tidy or a compile rebuilds the latest version where it is (compiler.rebuilt_folds).

The code below is the gate's comparison at commit 951a904d, copied as it was: the widest of
the gates cairn has had. The first public version (07e89b76) compared the same way and also
refused a claim that leaves a code fence open; 951a904d dropped that refusal; every later
gate up to CORE5 (77122cf8, d3304e9d) accepted a subset of what 951a904d accepts (the white
space inside inline code kept, a passage whose code the parser could not place compared
exactly). Only `whole_before_core5` is called from outside; nothing in this module is edited
to agree with the new rule. A no-break space was text for that gate too."""

import re
from collections.abc import Iterable, Mapping

from cairn.spans import Span
from cairn.store.models import Claim

PROSE_KINDS = frozenset({"paragraph", "bullet", "quote"})
LINE_KINDS = frozenset({"code", "table"})
# Whitespace is these four characters and nothing else: a no-break space, a zero-width
# character or a bidirectional control is text, compared like any other character.
_WHITESPACE = re.compile(r"[ \t\r\n]+")
# On a line whose white space is collapsed: one that opens with a list marker ("- ", "* ",
# "+ ", or one alone) or a quote marker (">"), and one that is nothing but such markers.
_LEADS = re.compile(r"[-*+](?: |$)|>")
_MARKERS_ALONE = re.compile(r"(?:[-*+](?: |$)|> ?)+")
_QUOTED = re.compile(r"^[ \t]*(?:> ?)+")  # a line's quote markers, set aside to find code
_ITEM = re.compile(r"(?:[-*+]|\d{1,9}[.)])(?: |$)")  # a list item's marker, at its text's start
# A line's text (indentation and quote markers aside) that Markdown reads as structure.
_STRUCTURE = re.compile(
    r"`{3,}|~{3,}"  # a code fence
    r"|\||:?-+:?[ \t]*\|"  # a table row or delimiter row
    r"|(?:=+|-+|(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})[ \t]*$"  # a rule or underline
    r"|</?[A-Za-z]|<!|<\?"  # an HTML tag, comment or declaration
    r"|\[[^\]]+\]:"  # a link-reference or footnote definition
)
# How a passage is compared, and what it is under that comparison.
_LINES, _EXACT, _TEXT = "lines", "exact", "text"
_Reading = tuple[str, str | tuple[str, ...]] | None
_WHOLE = "a claim must be the whole of each passage it cites, copied exactly"


def whole_before_core5(claim: Claim, spans: Mapping[str, Span]) -> bool:
    """Whether the claim is the whole of every passage it cites that `spans` holds, by the
    gate's comparison at 951a904d (and cites at least one)."""
    return _not_verbatim(claim, spans) is None


def _not_verbatim(
    claim: Claim, spans: Mapping[str, Span], readings: dict[str, _Reading] | None = None
) -> str | None:
    """The claim's grounding.not_verbatim violation, or None when it is every passage it cites,
    whole. `readings` keeps each passage's reading between calls of one gate."""
    readings = {} if readings is None else readings
    lines = claim.text.split("\n")
    read: dict[str, str | tuple[str, ...]] = {}  # the claim, read each way a passage asks

    def claim_as(how: str) -> str | tuple[str, ...]:
        if how not in read:
            read[how] = _text(lines) if how == _TEXT else _rows(lines, how == _EXACT)
        return read[how]

    # An id the gate was not given is the strict gate's provenance.unknown_passage; here it is
    # passed over, and never repeated, since a model wrote it.
    known = [span_id for span_id in dict.fromkeys(claim.span_ids) if span_id in spans]
    if not known:
        return _refusal(
            claim, f"{_WHOLE}; it cites no passage that was given: cite the id of the one it copies"
        )
    structure: list[int | None] = []  # the claim's first line of structure, once found
    for span_id in known:
        span = spans[span_id]
        if span_id not in readings:
            readings[span_id] = _reading(span)
        if (reading := readings[span_id]) is None:
            return _refusal(
                claim,
                f"{_WHOLE}; {span_id} is a {span.kind}, which no claim copies: cite only "
                "the passage it copies",
            )
        how, text = reading
        if claim_as(how) != text:
            return _refusal(
                claim,
                f"{_WHOLE}; it is not the whole of {span_id}: copy all of {span_id}, leave "
                "nothing out, add nothing, and join no other passage to it",
            )
        if how == _TEXT and not structure:
            structure.append(_added_structure(lines))
        if how == _TEXT and (at := structure[0]) is not None:
            return _refusal(
                claim,
                f"{_WHOLE}; line {at} adds a line break, a rule or indentation that "
                f"{span_id} does not have: keep the lines of {span_id} as they are",
            )
    return None


def _reading(span: Span) -> _Reading:
    """How a passage is compared and what it is so read, or None for a kind no claim copies:
    code and tables line by line with white space at the end of each line aside, prose that
    holds structure line by line exactly, and other prose as text."""
    lines = span.text.split("\n")
    if span.kind in LINE_KINDS:
        return _LINES, _rows(lines, exact=False)
    if span.kind not in PROSE_KINDS:
        return None
    if _structure(lines) is not None:
        return _EXACT, _rows(lines, exact=True)
    return _TEXT, _text(lines)


def _rows(lines: list[str], exact: bool) -> tuple[str, ...]:
    """Lines compared one by one: CR (and, unless exact, white space) at each end of line
    aside, and the blank lines before the first and after the last line of text dropped."""
    rows = [line.rstrip("\r" if exact else " \t\r") for line in lines]
    first = next((n for n, row in enumerate(rows) if row.strip(" \t\r")), len(rows))
    last = next((n for n in range(len(rows), first, -1) if rows[n - 1].strip(" \t\r")), first)
    return tuple(rows[first:last])


def _text(lines: Iterable[str]) -> str:
    """Prose read as text: each run of white space one space and the ends dropped, except that
    a line break stays before a line that opens with a list or quote marker and after a line
    that is markers alone, so every marker stays at the start of its line."""
    out: list[str] = []
    alone = False
    for raw in lines:
        line = _WHITESPACE.sub(" ", raw).strip(" ")
        if not line:
            continue
        if out:
            out.append("\n" if alone or _LEADS.match(line) else " ")
        out.append(line)
        alone = _MARKERS_ALONE.fullmatch(line) is not None
    return "".join(out)


def _added_structure(lines: list[str]) -> int | None:
    """The number of the first line from the claim's first line of text on that Markdown reads
    as structure, or None: a claim read as text may hold none."""
    first = next((n for n, line in enumerate(lines) if line.strip(" \t\r")), len(lines))
    at = _structure(lines[first:])
    return None if at is None else first + at


def _structure(lines: Iterable[str]) -> int | None:
    """The number of the first line that Markdown reads as structure rather than as running
    text, or None. One test for passages and claims: a passage with such a line is compared
    line for line, and a claim compared as text may have none.

    Structure is, once a line's quote markers are set aside: a line indented four columns or
    more past the text of the list item it sits in (or past the margin), a code fence, a table
    row or delimiter row, a rule or heading underline, a hard line break (two spaces or a
    backslash at the end), a line that opens an HTML tag, a link-reference or footnote
    definition, or a list marker or number with nothing after it on its line. In doubt a line
    is structure. A quote marker alone is a quote's blank line, not structure."""
    items: list[int] = []  # the text column of each list item the current line may sit in
    for number, raw in enumerate(lines, 1):
        line = raw.rstrip("\r")
        if line.endswith(("  ", "\\")):
            return number
        expanded = line.expandtabs(4)
        outer = len(expanded) - len(expanded.lstrip(" "))  # before any quote marker
        quoted = _QUOTED.match(expanded)
        margin = quoted.end() if quoted else 0  # where a quote's text may start
        text = expanded[margin:].lstrip(" ")
        if not text:
            continue
        if outer - (items[-1] if items else 0) >= 4:
            return number
        column = len(expanded) - len(text)
        while items and column < items[-1]:
            items.pop()
        if column - max(items[-1] if items else 0, margin) >= 4 or _STRUCTURE.match(text):
            return number
        if item := _ITEM.match(text):
            rest = text[item.end() :]
            if not rest.strip(" ") or rest.startswith("    ") or _STRUCTURE.match(rest):
                return number  # an item with no text on its line, or with code or a rule
            items.append(column + item.end())
    return None


def _refusal(claim: Claim, message: str) -> str:
    return f"grounding.not_verbatim: {claim.id}: {message}"
