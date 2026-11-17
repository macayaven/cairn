"""What a note a source writes may hold: a page's bounds, read when asked (K4).

A page holds at most cairn.store.PAGE_CLAIMS_MAX claims and PAGE_CHARS_MAX characters of
claims, and a note past either alone is refused before it is stored (cairn.memory, G1). Every
kind of source writes notes that fit: a kind that cuts cuts to fit both, with a line saying so;
a kind that splits into parts makes each part fit, with nothing lost between parts; a
connector's item, which is not cut, is skipped with a reason of its own
(tests/acceptance/test_k4_notes_fit_a_page.py).

The bounds are read from cairn.store.memory each time they are asked for (`claims`, `chars`),
never copied at import, so a change of the store's constants moves every kind with them. A
note is measured as cairn.memory measures it before storing it (`measure`): its passages
(cairn.store.usage.PASSAGE_KINDS, each a claim once compiled) and the characters of their
texts. A text's own length is never less than that, so a kind that keeps a note's text within
`chars()` characters keeps it within the bound.
"""

import itertools
import re

from cairn.spans import Span, chunk
from cairn.store import memory as _store
from cairn.store.usage import PASSAGE_KINDS

SPAN_SOURCE = "cap_000000000000"  # chunk wants a source id; the spans are only counted
PROBE = "a-line-after-the-text"
# A line that opens a fence, after a list marker or a quote's ">" when there is one.
OPENER = re.compile(r"[ \t]*(?:>[ \t]?)*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)?(`{3,}|~{3,})")


def claims() -> int:
    """The most claims a page holds (cairn.store.PAGE_CLAIMS_MAX), read now."""
    return _store.PAGE_CLAIMS_MAX


def chars() -> int:
    """The most characters a page's claims hold (cairn.store.PAGE_CHARS_MAX), read now."""
    return _store.PAGE_CHARS_MAX


def measure(text: str) -> tuple[int, int]:
    """(passages, characters of their texts): a note's text as cairn.memory counts it
    against a page's bounds before storing it."""
    found = [span for span in chunk(SPAN_SOURCE, text) if span.kind in PASSAGE_KINDS]
    return len(found), sum(len(span.text) for span in found)


def fits(text: str) -> bool:
    """Whether a note of this text fits an empty page by both of its bounds."""
    passages, held = measure(text)
    return passages <= claims() and held <= chars()


def _spans(text: str) -> list[Span]:
    """The spans of `text` as they are when it follows a line of the note: a blank first
    line, so that nothing in it is read as front matter. Line n of `text` is line n + 2."""
    return chunk(SPAN_SOURCE, "\n" + text)


def _open_span(text: str) -> Span | None:
    """The span a fence left open at the end of `text` runs in, or None: a line after it
    would be part of that span."""
    last = _spans(text.rstrip("\n") + "\n\n" + PROBE)[-1]
    return None if last.text == PROBE else last


def fenced(text: str) -> str:
    """`text` with a fence left open at its end closed by a line of its own run, so that
    what the note says after it is not code. When that does not close it, the text is cut
    before the span the fence is in."""
    span = _open_span(text)
    if span is None:
        return text
    runs = [found.group(1) for line in span.text.splitlines() if (found := OPENER.match(line))]
    if runs:
        run = runs[0] if span.kind == "code" else runs[-1]
        closed = text.rstrip("\n") + "\n" + run
        if _open_span(closed) is None:
            return closed
    lines = text.splitlines()
    return "\n".join(lines[: span.start_line - 2]).rstrip("\n")


def cut(text: str, bound: int, chars: int) -> tuple[str, int, bool]:
    """(the text kept, the passages it holds, whether it was cut): `text` with its fences
    closed, kept whole up to `bound` passages as the store counts them (cairn.store.usage.
    passages_of) and `chars` characters, and cut just before the passage that would pass
    either: between two spans, so never inside a fence, a list item or a table. When its
    first passage alone is longer than `chars` (one fence or one paragraph can be), that
    passage is cut at the end of a line within `chars`, and a fence it leaves open closed."""
    text = fenced(text)
    lines = text.splitlines()
    ends = list(itertools.accumulate(len(line) + 1 for line in lines))  # through each line
    count = 0
    for span in _spans(text):
        if span.kind not in PASSAGE_KINDS:
            continue
        if count == bound or ends[span.end_line - 2] - 1 > chars:
            kept = "\n".join(lines[: span.start_line - 2]).rstrip("\n")
            if count == 0:
                kept = fenced(clipped(text, chars))
                count = sum(one.kind in PASSAGE_KINDS for one in _spans(kept))
            return kept, count, True
        count += 1
    return text.rstrip("\n"), count, False


def clipped(text: str, chars: int) -> str:
    """The first `chars` characters of `text`, back to the end of a line when there is one."""
    head = text[:chars]
    return (head.rsplit("\n", 1)[0] if "\n" in head else head).rstrip("\n")


def fitted(text: str, line: str) -> str:
    """`text` when a note of it fits a page (`fits`); else the text cut to fit (`cut`, its
    first line, the note's heading, always kept) and `line`, a sentence in plain words
    saying that the rest is left out, after it. What every kind does last to a note it
    made, so that one whose parts were sized by its own rules still fits."""
    if fits(text):
        return text
    room = max(1, chars() - len(line))
    kept, _, _ = cut(text, max(0, claims() - 1), room)
    return f"{kept}\n\n{line}\n"


def pieces(text: str, chars: int) -> list[str]:
    """One passage's text (a span's lines) in pieces of at most `chars` characters each,
    nothing of it lost: cut at the end of a line, else at the last space within the bound
    (that space is dropped), else at the bound. A fence is closed at the end of each piece
    and opened again, with its own opening line, at the start of the next. For a passage
    longer than a page holds: each piece is a note's part of its own."""
    lines = text.split("\n")
    opener = OPENER.match(lines[0]) if lines else None
    closer = ""
    if opener is not None and len(lines) > 1:
        head, run = lines[0], opener.group(1)
        last = lines[-1].strip()
        if last and set(last) == {run[0]} and len(last) >= len(run):
            closer, lines = lines[-1], lines[1:-1]
        else:
            closer, lines = run, lines[1:]
        room = max(1, chars - len(head) - len(closer) - 2)
        return [f"{head}\n{body}\n{closer}" for body in _bodies(lines, room)]
    return _bodies(lines, chars)


def opened_up(text: str, chars: int) -> str:
    """`text` with each passage longer than `chars` characters replaced by its `pieces`, one
    after the other with a blank line between them, so that each is a passage of its own a
    part can hold. A list item's later pieces lose their lines' leading blanks, so that each
    is a paragraph of its own and not more of the item. Every other line stays as it is (its
    line end written "\\n"); a text with no such passage is given back as it came."""
    spans = _spans(text)
    long = [s for s in spans if s.kind in PASSAGE_KINDS and len(s.text) > chars]
    if not long:
        return text
    lines = text.splitlines()  # as chunk reads them
    made: list[str] = []
    at = 0  # the next line of `lines` not yet in `made`
    for span in long:
        first, last = span.start_line - 2, span.end_line - 2  # lines of `text`, 0-based
        made += lines[at:first]
        cut = pieces(span.text, chars)
        if span.kind == "bullet":  # an indented piece after a blank line stays in the item
            cut = cut[:1] + ["\n".join(line.lstrip() for line in p.split("\n")) for p in cut[1:]]
        made.append("\n\n".join(cut))
        at = last + 1
    made += lines[at:]
    return "\n".join(made)


def packed(
    lines: list[tuple[str, str]], first: tuple[int, int], rest: tuple[int, int]
) -> list[list[str]]:
    """Lines of a list, each given as (its lead, its text) ("- 00:01:02 Ada Park: " and what
    was said), packed in order into parts: the first holds at most first[0] lines of
    first[1] characters in all, each later one rest[0] and rest[1]. Each line is a passage
    of its own, so a part's passages and their characters are its lines. A line longer than
    a later part holds is cut at a space (dropped), or at the bound, into lines with the same
    lead, so nothing of it is lost. A first part with no room for the next line is left
    without lines (its own head, the facts, may be all it holds)."""
    parts: list[list[str]] = [[]]
    size = 0
    for lead, text in lines:
        room = max(1, rest[1] - len(lead))
        for piece in _short(text, room):
            line = lead + piece
            most_lines, most_chars = first if len(parts) == 1 else rest
            full = len(parts[-1]) >= most_lines or size + len(line) > most_chars
            if full and (parts[-1] or len(parts) == 1):
                parts.append([])
                size = 0
            parts[-1].append(line)
            size += len(line)
    return parts


def _bodies(lines: list[str], chars: int) -> list[str]:
    """`lines` joined into texts of at most `chars` characters, a line too long cut at a
    space (or at the bound) into lines of its own."""
    made: list[str] = []
    current: list[str] = []
    size = 0
    for whole in lines:
        for line in _short(whole, chars):
            grown = size + len(line) + (1 if current else 0)
            if current and grown > chars:
                made.append("\n".join(current))
                current, size = [], 0
                grown = len(line)
            current.append(line)
            size = grown
    if current:
        made.append("\n".join(current))
    return made


def _short(line: str, chars: int) -> list[str]:
    """A line in lines of at most `chars` characters: cut at the last space within the
    bound (dropped), else at the bound."""
    out = []
    while len(line) > chars:
        space = line.rfind(" ", 1, chars + 1)
        if space > 0:
            out.append(line[:space])
            line = line[space + 1 :]
        else:
            out.append(line[:chars])
            line = line[chars:]
    out.append(line)
    return out
