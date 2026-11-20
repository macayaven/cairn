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
- An underscore run that opens or closes emphasis next to a number, version or date reads as
  "*", the same emphasis, in extraction and in normalize(): `_2,700 left out._` holds 2,700
  (G1; see _emphasis). An underscore inside a word reads as it always did.
- No fact is read that starts or ends at a comma normalize() takes out when the digit across
  that comma is 0 to 9: it would be a piece of a run normalize() joins (250 of build_1,250, 1 of
  1,250x, 1234 of 1234,567, /logs/batch_10 of /logs/batch_10,000/out.txt), which lint never
  finds. A fact that holds the whole run reads as before, and so does normalize() (G2; see
  _whole_runs).
- `value == span.text[start:end]`; facts are sorted by (start, end); output is deterministic.
- `norm = normalize(value)`: whitespace collapsed, case folded, thousands separators removed.
- `lines`: for `code_block` facts, `normalize()` of each non-blank line of the block that is
  not a comment-only line (stripped text starting with "#" or "//"), in order. Code blocks
  are scored per line, so a page that keeps 3 of 4 command lines is measurable. Empty for
  every other kind.
"""

import bisect
import re
from collections.abc import Callable, Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict

from cairn.spans import Span, code_spans, fence_closes

FactKind = Literal["code_block", "inline_code", "url", "path", "date", "version", "flag", "number"]

_THOUSANDS_SEPARATOR = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_SPACE_RUN = re.compile(r"\s+")  # the white space str.split() splits at
# A fence line as spans.py opens one; it may also be indented or inside a block quote. The
# backtick run is possessive (`{3,}+): a shorter run is followed by a backtick, so it always
# failed "(?!.*`)", and trying each one read the rest of the line again.
_FENCE = re.compile(
    r"^(?P<prefix>[ \t>]*)(?P<fence>`{3,}+(?!.*`)|~{3,})(?P<info>.*)$", re.MULTILINE
)
# An ordered-list marker ("1." or "2)" before a space) is markup, not a number.
_ORDERED_MARKER = re.compile(r"^[ \t>]*\d{1,9}[.)](?=[ \t])", re.MULTILINE)
# A run of n backticks, then the value, then the next run of exactly n backticks on that line.
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(?P<value>.+?)(?<!`)\1(?!`)")
_TICKS = re.compile(r"`+")
_FEW_TICKS = 32  # backticks below which `_inline_codes` lets finditer try every opener
_NO_TRAILING_PUNCTUATION = r"(?<![.,;:!?)])"
_UNIT = r"days?|tokens?|files?|min|sec|ms|[KMGT]B|s|h|%"
_URL = re.compile(r"\b[A-Za-z][\w+.-]*://[^\s<>\"'`]+" + _NO_TRAILING_PUNCTUATION)
_SCHEME_RUN = re.compile(r"[\w+.-]+")  # a maximal run of the characters a url's scheme part holds
_SCHEME_START = re.compile(r"\b[A-Za-z]")  # where a url can start
_PATTERNS: tuple[tuple[FactKind, re.Pattern[str]], ...] = (
    ("inline_code", _INLINE_CODE),
    ("url", _URL),
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


# Emphasis (G1). A run of one to three underscores opens emphasis at the start of a text or
# after a space, ( [ { " ' * or a backtick, before a character that is not a space; it closes
# emphasis after a character that is not a space, at the end or before a space, . , ; : ! ? )
# ] } " ' * or a backtick. Such a run next to a number, a version or a date (right before its
# first character, or right after its last or after its last and a period), whose other side
# is another such run or a plain edge (the start, or what an opener may follow, before it; not
# a word character, / ~ @ + or a separator and a digit after it), is read as "*", the same
# emphasis: the fact is read whole ("_2,700 left out._" holds 2,700, never 700) and found
# whole by lint, since normalize() reads it the same way. Every other underscore reads as
# before; inside a word (max_retries_3, v1_2, a file name) it opens and closes nothing.
_OPENER = re.compile(r"(?:^|(?<=[\s(\[{\"'*`]))_{1,3}+(?=[^\s_])")
_CLOSER = re.compile(r"(?<=[^\s_])_{1,3}+(?=$|[\s.,;:!?)\]}\"'*`])")
_EDGE_BEFORE = re.compile(r"[\s(\[{\"'*`]")  # what an opener may follow, as a fact's edge
_INSIDE_AFTER = re.compile(r"[\w/~@+]|[,.]\d")  # a fact before one of these is inside a token
_EMPHASISED = ("date", "version", "number")


def _emphasis(text: str) -> str:
    """`text` with each underscore run that opens or closes emphasis next to a number, a
    version or a date written as "*", one for one, so every offset holds."""
    if "_" not in text:
        return text
    opens = {m.end(): m.start() for m in _OPENER.finditer(text)}
    closes = {m.start(): m.end() for m in _CLOSER.finditer(text)}
    if not opens and not closes:
        return text
    runs = [(start, end) for end, start in opens.items()] + list(closes.items())
    read = _stars(text, runs)  # every such run as "*", to read the facts next to them
    keep = []
    for kind, pattern in _PATTERNS:
        if kind not in _EMPHASISED:
            continue
        for m in pattern.finditer(read):
            start, end = m.span()
            stop = end + 1 if text[end : end + 1] == "." and end + 1 in closes else end
            left = (opens[start], start) if start in opens else None
            right = (stop, closes[stop]) if stop in closes else None
            if not (left or right):
                continue
            if not left and start and not _EDGE_BEFORE.match(text, start - 1):
                continue
            if not right and _INSIDE_AFTER.match(text, end):
                continue
            keep += [run for run in (left, right) if run]
    return _stars(text, keep) if keep else text


def _stars(text: str, runs: list[tuple[int, int]]) -> str:
    out = list(text)
    for start, end in runs:
        out[start:end] = "*" * (end - start)
    return "".join(out)


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
    """Normalise text for fact matching (an underscore of emphasis next to a fact reads as
    "*", as in extract_facts): case folded, each run of white space one space and the ends
    dropped, except that white space inside inline code is the code's own and kept as it is,
    as the gate reads prose (review blocker 3): `a  b.txt` and `a b.txt` are two facts."""
    text = _emphasis(value)
    return _read(text, _code(text), 0, len(text))


def _code(text: str) -> list[tuple[int, int]]:
    """Where inline code is in `text`: cairn.spans.code_spans, the gate's finder, with the
    backticks of each fence line (`_FENCE`: an opener or a closer, alone on its line) set
    aside, so a fence's own runs never pair. No fence is followed from its opener to its
    closer: code_spans pairs within a block, and every block of a passage is a block of the
    body that holds it, as written, so a passage and a body read each block alike, whatever
    a fence left open, opened in a quote or on a list marker's line, or indented four spaces
    does (the closer the renderer adds is a fence line, set aside too). Code lines are read
    the same way (`_code_lines`).

    Only the spans that hold white space are given: a span without any reads the same kept
    or collapsed, so a text whose code holds no white space reads as `_plain` reads it, at
    its cost."""
    if "`" not in text:
        return []
    return [
        (left, right)
        for left, right in code_spans(_FENCE.sub(_set_aside, text))
        if _SPACE_RUN.search(text, left, right)
    ]


def _set_aside(line: re.Match[str]) -> str:
    """A fence line with its backticks as tildes: its length and line breaks kept."""
    return line[0].replace("`", "~")


def _read(text: str, code: list[tuple[int, int]], start: int, end: int) -> str:
    """text[start:end] as normalize reads it, `code` being where inline code is in `text`:
    white space inside code as it is, each other run of it one space, the ends outside code
    dropped, then case folded and the thousands separators taken out."""
    first = bisect.bisect_right(code, start, key=lambda c: c[1]) if code else 0
    if first == len(code) or code[first][0] >= end:  # no code with white space in the range
        return _plain(text[start:end])
    pieces: list[tuple[bool, str]] = []  # (inside code, text)
    pos = start
    for n in range(first, len(code)):
        left, right = code[n]  # by index: a slice would copy the rest of `code` for each fact
        if left >= end:
            break
        if left > pos:
            pieces.append((False, text[pos:left]))
        pieces.append((True, text[max(left, pos) : min(right, end)]))
        pos = min(right, end)
    if pos < end:
        pieces.append((False, text[pos:end]))
    read = [(kept, piece if kept else _collapsed(piece)) for kept, piece in pieces]
    if read and not read[0][0]:
        read[0] = (False, read[0][1].lstrip(" "))
    if read and not read[-1][0]:
        read[-1] = (False, read[-1][1].rstrip(" "))
    return _THOUSANDS_SEPARATOR.sub("", "".join(piece for _, piece in read).casefold())


def _collapsed(piece: str) -> str:
    """`piece` with each run of white space one space, its ends kept as one space: what
    _SPACE_RUN.sub(" ", piece) gives, by str.split, which reads the same white space."""
    words = piece.split()
    if not words:
        return " " if piece else ""
    joined = " ".join(words)
    if piece[0].isspace():
        joined = " " + joined
    return joined + " " if piece[-1].isspace() else joined


def _plain(value: str) -> str:
    return _THOUSANDS_SEPARATOR.sub("", " ".join(value.split()).casefold())


def extract_facts(span: Span) -> list[Fact]:
    """Extract typed facts from one span, sorted by (start, end)."""
    facts, claimed = [], _Claimed(len(span.text))
    # The span's text with each underscore of emphasis by a fact as "*", one for one: facts are
    # found and normalized in it, as normalize() reads the whole span, and valued in the span.
    text = _emphasis(span.text)
    code = _code(text)  # inline code, whose white space a fact's norm keeps
    for start, end, body_start, body_end in _fenced(span.text):  # a code span is one such block
        claimed.add(start, end)
        facts.append(_fact(span, text, "code_block", body_start, body_end, code))
    for m in _ORDERED_MARKER.finditer(span.text):
        claimed.add(*m.span())  # claimed, no fact
    finders = _FAST if _joined(text) is None else _FINDERS  # G2's filter only where it acts
    for kind, pattern in _PATTERNS:
        group = "value" if "value" in pattern.groupindex else 0  # inline code drops its backticks
        for m in finders.get(kind, pattern.finditer)(text):
            if claimed.overlaps(*m.span()):
                continue  # an earlier kind already owns these characters
            claimed.add(*m.span())
            facts.append(_fact(span, text, kind, *m.span(group), code))
    return sorted(facts, key=lambda f: (f.start, f.end))


class _Claimed:
    """The characters of a text that earlier matches own, one flag per character.

    It replaces a list of claimed (start, end) ranges that every match was compared with, which
    cost the number of matches times the number of claims. A match [a, b) overlaps a claimed
    range [s, e) when a < e and s < b, that is when they share a character; every claimed
    range and every match of these patterns is non-empty, so "some claimed range overlaps
    [a, b)" is "some character in [a, b) is flagged". Ranges may overlap one another (an
    ordered-list marker inside a fence); flags do not care. Matches of one pattern do not
    overlap one another, so the flags each pattern reads and sets add up to the text's length.
    """

    def __init__(self, size: int) -> None:
        self.flags = bytearray(size)

    def add(self, start: int, end: int) -> None:
        self.flags[start:end] = b"\x01" * (end - start)

    def overlaps(self, start: int, end: int) -> bool:
        return self.flags.find(1, start, end) != -1


def _urls(text: str) -> Iterator[re.Match[str]]:
    """The matches `_URL.finditer(text)` yields, found by trying the pattern at fewer places.

    finditer tries the pattern at every position. In a long run of scheme characters ("a.a.a"
    or "a-a-a") every word start is a place it can begin, and each try reads to the run's end
    looking for "://": the run's length squared. But from a start, `[\\w+.-]*` reads exactly to
    the end of the run of such characters it is in (the next character is not one of them, and
    giving one back leaves one of them where ":" must be), so a match starts in a run that
    "://" follows. From every start in that run the match reaches the same "://", and the rest
    of the pattern reads only what comes after it; so the run's first start at or after the
    previous match (a letter at a word boundary) matches if any start in it does, and that is
    the match finditer finds. The pattern is then tried there alone."""
    if "://" not in text:
        return
    pos = 0  # where the previous match ended; finditer goes on from there
    for run in _SCHEME_RUN.finditer(text):
        if run.end() <= pos or not text.startswith("://", run.end()):
            continue
        start = _SCHEME_START.search(text, max(run.start(), pos), run.end())
        if start and (m := _URL.match(text, start.start())):
            yield m
            pos = m.end()


def _inline_codes(text: str) -> Iterator[re.Match[str]]:
    """The matches `_INLINE_CODE.finditer(text)` yields, found by trying the pattern at fewer
    places.

    A match opens with a whole run of backticks and closes at the next run of exactly that
    many on the same line ("." stops at a line break). finditer tries every run as an opener,
    and a run with no run of its length after it on its line reads to the end of the line
    before it fails; a line with runs of 1, 2, 3, ... backticks pays that once per length.
    Here a backwards pass notes, for each run, the next run of its length on its line; a run
    with none is skipped, as the pattern fails there, and the pattern is tried only from a run
    that has one, where it matches up to that run."""
    if text.count("`") <= _FEW_TICKS:  # finditer itself: at most that many openers each read
        yield from _INLINE_CODE.finditer(text)  # to their line's end, a bounded number of reads
        return
    runs = list(_TICKS.finditer(text))
    line, lines = 0, []
    for i, run in enumerate(runs):
        line += text.count("\n", runs[i - 1].start() if i else 0, run.start())
        lines.append(line)
    closes, last = [False] * len(runs), {}
    for i in reversed(range(len(runs))):
        key = (lines[i], len(runs[i][0]))
        closes[i] = key in last
        last[key] = i
    pos = 0  # where the previous match ended; finditer goes on from there
    for i, run in enumerate(runs):
        if run.start() >= pos and closes[i] and (m := _INLINE_CODE.match(text, run.start())):
            yield m
            pos = m.end()


# A comma normalize() takes out (_THOUSANDS_SEPARATOR), as lint sees it beside a fact (G2): a
# fact right after it touches the 0-9 digit before it, a fact that ends at it the 0-9 digit
# after it, once normalize() joins the two; lint's tokens are [0-9a-z_], so neither is found.
_ASCII_DIGITS = frozenset("0123456789")
_Joined = tuple[set[int], set[int]] | None
# The last text _joined read and its answer, as one tuple: every finder of one text asks for
# the same text object, and a single assignment never pairs one text with another's answer.
_last_joined: tuple[str | None, _Joined] = (None, None)


def _joined(text: str) -> _Joined:
    """(starts, ends): where no fact may start (right after a comma normalize() takes out, the
    digit before it 0-9) and where none may end (at such a comma, the digit after it 0-9). None
    when the text has no such comma, the usual case. Read once a text: the last answer is kept
    for the same text object, and any other text is read afresh."""
    global _last_joined
    if "," not in text:
        return None
    last_text, last = _last_joined
    if last_text is text:
        return last
    starts, ends = set(), set()
    comma = text.find(",", 1)
    while comma != -1:  # the commas _THOUSANDS_SEPARATOR takes out: \d is str.isdecimal
        group = text[comma + 1 : comma + 4]
        if (
            text[comma - 1].isdecimal()
            and len(group) == 3
            and group.isdecimal()
            and not text[comma + 4 : comma + 5].isdecimal()
        ):
            if text[comma - 1] in _ASCII_DIGITS:
                starts.add(comma + 1)
            if group[0] in _ASCII_DIGITS:
                ends.add(comma)
        comma = text.find(",", comma + 1)
    answer = (starts, ends) if starts or ends else None
    _last_joined = (text, answer)
    return answer


def _whole_runs(
    find: Callable[[str], Iterator[re.Match[str]]],
) -> Callable[[str], Iterator[re.Match[str]]]:
    """`find`'s matches, less those that start or end where _joined says: a piece of a run that
    normalize() joins, which lint never finds. A text that joins nothing gets `find`'s own."""

    def whole(text: str) -> Iterator[re.Match[str]]:
        joined = _joined(text)
        if joined is None:
            return find(text)
        starts, ends = joined
        return (m for m in find(text) if m.start() not in starts and m.end() not in ends)

    return whole


# Kinds whose matches are found without letting finditer try every position; the same matches.
_FAST = {"inline_code": _inline_codes, "url": _urls}
# Each kind's finder: as above, less the pieces of a joined run (G2). extract_facts uses _FAST
# for a text that joins nothing; cairn.store's count_facts reads these for every text.
_FINDERS = {kind: _whole_runs(_FAST.get(kind, pattern.finditer)) for kind, pattern in _PATTERNS}


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
    """Whether a fence line closes the opener: cairn.spans.fence_closes, the one closer rule
    (a run of the opener's character at least as long, alone, indented at most 3 more than
    the opener, a character a column), read past the block-quote markers, at the same
    block-quote depth. The markers count as indentation, one column each, as they did."""
    return line["prefix"].count(">") == opener["prefix"].count(">") and fence_closes(
        " " * len(line["prefix"]) + line["fence"] + line["info"],
        opener["fence"],
        len(opener["prefix"]) + 3,
    )


def _fact(
    span: Span,
    read: str,
    kind: FactKind,
    start: int,
    end: int,
    code: list[tuple[int, int]] | None = None,
) -> Fact:
    """One fact. Its norm is its text as normalize reads it in the span, `code` being where
    inline code is in it: an inline_code fact's white space inside a code span is kept, so it
    is found in the span's own text. With no `code`, every run of white space is one space."""
    value = span.text[start:end]
    lines = _code_lines(read, code or [], start, end) if kind == "code_block" else ()
    return Fact(
        kind=kind,
        value=value,
        norm=_read(read, code, start, end) if code else _plain(read[start:end]),
        span_id=span.id,
        start=start,
        end=end,
        lines=lines,
    )


def _code_lines(read: str, code: list[tuple[int, int]], start: int, end: int) -> tuple[str, ...]:
    """Normalised lines of the code block read[start:end], without blank and comment-only
    lines: each as normalize reads it in the span (`_read`), so inline code on a line (a
    shell's `date  +%s`) keeps its white space on both sides of the match. With no such code,
    each line is `_plain`'s reading of it, as `_read` would give."""
    if not code:
        return tuple(
            _plain(line)
            for line in read[start:end].splitlines()
            if line.strip() and not line.strip().startswith(("#", "//"))
        )
    lines, at = [], start
    for line in read[start:end].splitlines(keepends=True):
        bare = line.rstrip("\r\n\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
        if bare.strip() and not bare.strip().startswith(("#", "//")):
            lines.append(_read(read, code, at, at + len(bare)))
        at += len(line)
    return tuple(lines)
