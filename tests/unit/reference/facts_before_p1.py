# Reference copy for the P1 differential test (tests/unit/test_facts_lint_differential_unit.py).
# Below these three lines: src/cairn/facts.py byte for byte as of commit 0856d6b, before P1
# made it near-linear. Nothing imports it but that test. Do not edit, format or fix it.
"""Deterministic fact extraction from spans.

Contract (frozen by tests/acceptance/test_t2_facts.py and tests/gold/t2_facts_synthetic.jsonl):
- A `code` span yields exactly one `code_block` fact whose value is the content between the
  fence lines. Code is atomic: nothing else is extracted from inside it.
- Inline code (`...`) yields one `inline_code` fact whose value is the content between the
  backticks. Nothing else is extracted from inside inline code.
- Outside code, in this precedence order (a character claimed by an earlier kind is not
  reused): url, path (~/, ./, ../ or / roots), date (ISO yyyy-mm-dd as a whole token,
  including an attached ISO time such as T10:00:00Z; never inside a word or a filename
  like notes_2026-03-14.md, so the lint matcher always finds it), version (x.y.z with
  optional -suffix, or a v prefix), flag (--name or -x, not part of a hyphenated word),
  number (optional currency prefix, thousands separators, decimals, and an optional unit
  joined by a space or hyphen, e.g. "5 min", "1.2 GB", "40-file", "2,500 tokens").
- Trailing sentence punctuation is never part of a value.
- `value == span.text[start:end]`; facts are sorted by (start, end); output is deterministic.
- `norm = normalize(value)`: whitespace collapsed, case folded, thousands separators removed.
- `lines`: for `code_block` facts, `normalize()` of each non-blank line of the block that is
  not a comment-only line (stripped text starting with "#" or "//"), in order. Code blocks
  are scored per line, so a page that keeps 3 of 4 command lines is measurable. Empty for
  every other kind.
"""

import re
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict

from cairn.spans import Span

FactKind = Literal["code_block", "inline_code", "url", "path", "date", "version", "flag", "number"]

_THOUSANDS_SEPARATOR = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
# A fence line as spans.py opens one; it may also be indented or inside a block quote.
_FENCE = re.compile(r"^(?P<prefix>[ \t>]*)(?P<fence>`{3,}(?!.*`)|~{3,})(?P<info>.*)$", re.MULTILINE)
# An ordered-list marker ("1." or "2)" before a space) is markup, not a number.
_ORDERED_MARKER = re.compile(r"^[ \t>]*\d{1,9}[.)](?=[ \t])", re.MULTILINE)
# A run of n backticks, then the value, then the next run of exactly n backticks on that line.
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(?P<value>.+?)(?<!`)\1(?!`)")
_NO_TRAILING_PUNCTUATION = r"(?<![.,;:!?)])"
_UNIT = r"days?|tokens?|files?|min|sec|ms|[KMGT]B|s|h|%"
_PATTERNS: tuple[tuple[FactKind, re.Pattern[str]], ...] = (
    ("inline_code", _INLINE_CODE),
    ("url", re.compile(r"\b[A-Za-z][\w+.-]*://[^\s<>\"'`]+" + _NO_TRAILING_PUNCTUATION)),
    (
        "path",  # a ~/, ./, ../ or / root, then segments that start with a name character
        re.compile(
            r"(?<![\w.~/-])(?:~|\.\.?)?(?:/[\w.~@+][\w.~@+-]*)+/?" + _NO_TRAILING_PUNCTUATION
        ),
    ),
    (
        "date",  # a whole token: an ISO time joins it, a word or filename around it rules it out
        re.compile(
            r"(?<!\w)\d{4}-\d{2}-\d{2}"
            r"(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?(?!\w)"
        ),
    ),
    (
        "version",  # x.y.z or v-prefixed, an optional -suffix, and no letter or digit after it
        re.compile(
            r"(?<![\w.])(?:v\d+(?:\.\d+)*|\d+(?:\.\d+){2,})"
            r"(?:-[0-9A-Za-z.-]*[0-9A-Za-z])?(?!\.?\w)"
        ),
    ),
    # Not inside a hyphenated word, a dash run, or flush after inline code (`quill`-style).
    ("flag", re.compile(r"(?<![\w`-])--?[A-Za-z](?:[\w-]*\w)?")),
    (
        "number",  # a whole token: 4x, 100k or sha256 yield nothing rather than a partial value
        re.compile(
            r"(?<![\w.])[$€£]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
            rf"(?:[ -]?(?:{_UNIT}))?(?!\.?\w)"
        ),
    ),
)


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: FactKind
    value: str
    norm: str
    span_id: str
    start: int  # offset into span.text, inclusive
    end: int  # offset into span.text, exclusive
    lines: tuple[str, ...] = ()  # code_block only: normalised, non-blank, non-comment lines


def normalize(value: str) -> str:
    """Normalise text for fact matching."""
    return _THOUSANDS_SEPARATOR.sub("", " ".join(value.split()).casefold())


def extract_facts(span: Span) -> list[Fact]:
    """Extract typed facts from one span, sorted by (start, end)."""
    facts, claimed = [], []
    for start, end, body_start, body_end in _fenced(span.text):  # a code span is one such block
        claimed.append((start, end))
        facts.append(_fact(span, "code_block", body_start, body_end))
    claimed += [m.span() for m in _ORDERED_MARKER.finditer(span.text)]  # claimed, no fact
    for kind, pattern in _PATTERNS:
        group = "value" if "value" in pattern.groupindex else 0  # inline code drops its backticks
        for m in pattern.finditer(span.text):
            if any(m.start() < end and start < m.end() for start, end in claimed):
                continue  # an earlier kind already owns these characters
            claimed.append(m.span())
            facts.append(_fact(span, kind, *m.span(group)))
    return sorted(facts, key=lambda f: (f.start, f.end))


def _fenced(text: str) -> Iterator[tuple[int, int, int, int]]:
    """Yield (start, end, body_start, body_end) per fenced block; unclosed ones run to the end."""
    pos = 0
    while opener := _FENCE.search(text, pos):
        closer = next(
            (line for line in _FENCE.finditer(text, opener.end()) if _closes(line, opener)), None
        )
        body_start = min(opener.end() + 1, len(text))
        body_end = closer.start() - 1 if closer else len(text)
        pos = closer.end() if closer else len(text)
        yield opener.start(), pos, body_start, max(body_start, body_end)


def _closes(line: re.Match[str], opener: re.Match[str]) -> bool:
    """A closing fence repeats the opener's character at least as often, with no info string,
    at the same block-quote depth, indented at most 3 more than the opener (as in spans.py)."""
    return (
        line["fence"].startswith(opener["fence"])
        and not line["info"].strip()
        and line["prefix"].count(">") == opener["prefix"].count(">")
        and len(line["prefix"]) <= len(opener["prefix"]) + 3
    )


def _fact(span: Span, kind: FactKind, start: int, end: int) -> Fact:
    value = span.text[start:end]
    lines = _code_lines(value) if kind == "code_block" else ()
    return Fact(
        kind=kind,
        value=value,
        norm=normalize(value),
        span_id=span.id,
        start=start,
        end=end,
        lines=lines,
    )


def _code_lines(code: str) -> tuple[str, ...]:
    """Normalised lines of a code block, without blank and comment-only lines."""
    return tuple(
        normalize(line)
        for line in code.splitlines()
        if line.strip() and not line.strip().startswith(("#", "//"))
    )
