"""Section-aware spans: the smallest citable units of a source document.

Contract (frozen by tests/acceptance/test_t1_spans.py and test_t1_regressions.py):
- Every non-blank source line belongs to exactly one span; spans are ordered and disjoint,
  never start or end on a blank line, and `text` is the exact source lines joined by "\\n".
  Blank lines inside a fence or a list item stay inside its span.
- `heading_path` is the chain of enclosing ATX heading texts; a heading span's path ends
  with its own text. Front matter has an empty path. Lines inside fences are never headings.
- `id` is "sp_" + 16 hex chars of a content hash over source_id, heading_path, text and the
  occurrence index of identical (heading_path, text) pairs. Ids are stable across runs and
  unchanged by edits elsewhere in the document, including edits that shift line numbers.

Supported Markdown, one span kind per block. Headings, fences, list items, tables and quotes
start on a line indented at most 3 spaces.
- Front matter: the lines between a `---` on line 1 and the next `---` line, when they read
  as metadata (review 7.4): each line is a key and its value (a key is one word of letters,
  digits, "_", "." and "-", or a quoted string, then ":" and a blank or the line's end), a
  line that continues the value of a key above it (indented, or a list item "- "), a comment
  ("#"), or blank, with at least one key; no lines, or blank lines only, count too. Anything
  else is content: the first `---` is a thematic break and every line of the block, both
  `---` lines included, is one paragraph span, so the spans after it are the spans they were
  (same lines, same ids) when the block was read as front matter, and a page that cites them
  still does. It is told by the lines' shape, not by parsing YAML: "Production rule: never
  drop it." is a YAML mapping, but its key is two words, so it is prose; and metadata that
  is not valid YAML ("title: [x") stays front matter, for the reader of front matter to
  report. Its limit: a one-word label and its sentence ("Note: never drop it.") reads as a
  key and its value, so it stays front matter, as before.
- ATX headings: 1 to 6 `#`, a space or tab, then the text; a closing `#` run is dropped.
- Fenced code, backtick or tilde: atomic, fence lines included, never split. A closing fence
  is a run of the opening character, at least as long, alone on its line, and indented at
  most 3 spaces more than its opening fence; less is fine. An absolute 3-space limit would
  break fences inside list items, where the closer is legitimately indented. An unclosed
  fence runs to the last non-blank line.
- Top-level list items (`-`, `*`, `+`, `1.`, `1)`): a line belongs to the item when its
  indentation reaches the item's content column: marker indent + marker width + the spaces
  after the marker. A lazy continuation line (plain paragraph text directly after item text)
  also belongs. After a blank line, only lines that reach the content column continue the
  item; the blank lines between then sit inside the item span. Nested items stay with their
  parent. A fence inside an item, even one opened on a marker line, stays inside that item
  and is never split.
- Pipe tables: consecutive lines starting with `|`.
- Block quotes: consecutive lines starting with `>`.
- Paragraphs: consecutive lines that start no other block.

Known limits:
- An empty ATX heading with no text (`#`) is read as a paragraph.
- A heading whose only text is a closing `#` run (`## ##`) takes that run as its text.
- Tables without a leading pipe are read as paragraphs.
- No lazy block-quote continuation: a line without `>` ends the quote.
- Prose that starts with `|` is read as a table.
- An empty list item (`-` alone) is read as paragraph text.
- Setext headings (text underlined with `===` or `---`) are read as paragraph text.
- HTML blocks are not recognised; their lines are read as Markdown, so `# x` is a heading.
- Indented code blocks are read as paragraphs.
- Tabs count as one column of indentation.
"""

import hashlib
import json
import re
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterator, Sequence
from typing import Literal

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict

SpanKind = Literal["frontmatter", "heading", "paragraph", "bullet", "code", "table", "quote"]

# Matched against a line stripped of its indentation, which is at most 3 spaces.
# The start of an ATX heading: its "#" run and the blanks after it. The rest of the line is
# its title, less a closing "#" run (_title). It was one pattern, (#{1,6})[ \t]+(.*?)
# (?:[ \t]+#+)?[ \t]* matched whole, whose lazy title tried every length and read the blanks
# after it again each time: a heading with a long run of blanks cost their number squared.
_HEADING = re.compile(r"(#{1,6})[ \t]+")
# A backtick fence's info string has no backticks. The backtick run is possessive (`{3,}+), as
# in cairn.facts: a shorter run is followed by a backtick, so it always failed "(?!.*`)", and
# trying each one read the rest of the line again (G1).
_FENCE = re.compile(r"`{3,}+(?!.*`)|~{3,}")
_BULLET = re.compile(r"(?:[-*+]|\d{1,9}[.)])[ \t]")
# A front-matter key at a line's start: one word, or a quoted string, then ":" and a blank or
# the line's end.
_META_KEY = re.compile(r"""(?:[A-Za-z0-9_][A-Za-z0-9_.-]*|"[^"]*"|'[^']*'):(?:[ \t]|$)""")


class Span(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    source_id: str
    kind: SpanKind
    heading_path: tuple[str, ...]
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    text: str


def chunk(source_id: str, text: str) -> list[Span]:
    """Split a Markdown document into spans, in document order."""
    lines = text.splitlines()
    spans = []
    sections: list[tuple[int, str]] = []  # (level, title) of each enclosing heading
    seen: Counter[tuple[tuple[str, ...], str]] = Counter()  # (heading_path, text) occurrences
    for kind, start, end in _blocks(lines):
        if kind == "heading":
            level, title = _heading(lines[start])
            while sections and sections[-1][0] >= level:
                sections.pop()
            sections.append((level, title))
        heading_path = tuple(title for _, title in sections)
        block = "\n".join(lines[start:end])
        occurrence = seen[heading_path, block]
        seen[heading_path, block] += 1
        spans.append(
            Span(
                id=_span_id(source_id, heading_path, block, occurrence),
                source_id=source_id,
                kind=kind,
                heading_path=heading_path,
                start_line=start + 1,
                end_line=end,
                text=block,
            )
        )
    return spans


def _blocks(lines: list[str]) -> Iterator[tuple[SpanKind, int, int]]:
    """Yield (kind, start, end) per block, as 0-based line indexes with `end` exclusive."""
    i = _frontmatter_end(lines)
    if i:  # metadata, else one paragraph of content, so every span after it is as it was
        yield "frontmatter" if _metadata(lines[1 : i - 1]) else "paragraph", 0, i
    while i < len(lines):
        kind = _kind(lines[i])
        if kind is None:
            i += 1
            continue
        end = _block_end(kind, lines, i)
        yield kind, i, end
        i = end


def _frontmatter_end(lines: list[str]) -> int:
    """Index after the `---` that closes a block opened by `---` on line 1; 0 if there is
    none. The block is front matter when its lines read as metadata (_metadata)."""
    if lines and lines[0] == "---":
        for j in range(1, len(lines)):
            if lines[j] == "---":
                return j + 1
    return 0


def _metadata(lines: list[str]) -> bool:
    """Whether lines read as metadata: every one a key and its value, a continuation of a key
    above it (indented, or a list item), a comment or blank, with at least one key unless
    every line is blank (see the module)."""
    keyed = False
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        if _META_KEY.match(line):
            keyed = True
        elif not (keyed and (line[0] in " \t" or line == "-" or line.startswith("- "))):
            return False
    return keyed or not any(line.strip() for line in lines)


def _kind(line: str) -> SpanKind | None:
    """The kind of block a line starts, or None for a blank line."""
    if not line.strip():
        return None
    body = line.lstrip(" ")  # a leading tab stays, and no block pattern accepts it
    if len(line) - len(body) > 3:  # four spaces of indentation never start a block
        return "paragraph"
    if _HEADING.match(body):
        return "heading"
    if _FENCE.match(body):
        return "code"
    if body.startswith("|"):
        return "table"
    if body.startswith(">"):
        return "quote"
    if _BULLET.match(body):
        return "bullet"
    return "paragraph"


def _block_end(kind: SpanKind, lines: list[str], i: int) -> int:
    if kind == "heading":
        return i + 1
    if kind == "code":
        return _fence_end(lines, i)
    if kind == "bullet":
        return _item_end(lines, i)
    # Paragraphs, tables and quotes run on while the next line is of the same kind.
    end = i + 1
    while end < len(lines) and _kind(lines[end]) == kind:
        end += 1
    return end


def _item_end(lines: list[str], i: int) -> int:
    """End of the list item on line i: one past its last non-blank line.

    It keeps every line that reaches its content column, across blank lines, and each lazy
    continuation line: paragraph text directly after a line of the item.
    """
    column = _content_column(lines[i])
    end = j = i
    while j < len(lines):
        line = lines[j]
        if not line.strip():
            j += 1
            continue
        if j == i or _indent(line) >= column:  # item text, a nested item, or a fence kept whole
            j = _fence_end(lines, j) if fence_opener(line) else j + 1
        elif j == end and _kind(line) == "paragraph":  # a lazy continuation line
            j += 1
        else:
            break
        end = j
    return end


def _content_column(line: str) -> int:
    """Column where a line's text starts: past its indentation and any list marker."""
    body = line.lstrip()
    if marker := _BULLET.match(body):
        body = body[marker.end() :].lstrip()
    return len(line) - len(body)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _fence_end(lines: list[str], i: int) -> int:
    """End of the fence opened on line i: after its closing line, else its last non-blank line.

    The opening fence may follow a list marker. A closing line holds only a run of the opening
    character, at least as long as the opening run, indented at most 3 spaces more than the
    opening fence (less is fine).
    """
    column, fence = fence_opener(lines[i]) or (0, "")
    for j in range(i + 1, len(lines)):
        if fence_closes(lines[j], fence, column + 3):
            return j + 1
    end = len(lines)
    while not lines[end - 1].strip():
        end -= 1
    return end


def fence_opener(line: str) -> tuple[int, str] | None:
    """The code fence a line opens, as (column, run), or None: past the line's indentation and
    any list marker, a run of three or more backticks with no backtick after it on the line,
    or of three or more tildes. The one rule for openers, here and in the body the compiler
    writes (compiler.render_body). The column counts characters, a tab as one."""
    column = _content_column(line)
    found = _FENCE.match(line[column:])
    return (column, found.group()) if found else None


def fence_closes(line: str, run: str, deepest: int) -> bool:
    """Whether a line closes a fence opened with `run`: it holds only a run of the same
    character, at least as long, indented `deepest` columns at most. The one rule for closers,
    here and in the body the compiler writes: chunk gives `deepest` as the opener's column
    plus 3 (see the module), the body as Markdown's own limit (compiler.render_body)."""
    closer = line.strip()
    return len(closer) >= len(run) > 0 and set(closer) == {run[0]} and _indent(line) <= deepest


def _heading(line: str) -> tuple[int, str]:
    """(level, title) of an ATX heading line."""
    body = line.lstrip(" ")
    start = _HEADING.match(body)
    return len(start.group(1)), _title(body[start.end() :])


def _title(rest: str) -> str:
    """A heading's text from what follows its blanks: the shortest the old pattern's lazy
    title took. Trailing blanks go, then a closing "#" run when blanks precede it, then the
    blanks before that run. A run with no blank before it stays: "## ##" is titled "##"."""
    rest = rest.rstrip(" \t")
    text = rest.rstrip("#")
    if len(text) < len(rest) and text != text.rstrip(" \t"):
        return text.rstrip(" \t")
    return rest


def _span_id(source_id: str, heading_path: tuple[str, ...], text: str, occurrence: int) -> str:
    """Hash a JSON encoding of the fields: unlike joining with separators, it is injective."""
    key = json.dumps([source_id, heading_path, text, occurrence])
    return "sp_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def claim_id(span_id: str) -> str:
    """ "cl_" + the span id's hex part."""
    return "cl_" + span_id.removeprefix("sp_")


# What is the same text (CORE5): two passages, a claim and a passage, two claims are the same
# only when their text is, character for character, but for what `compared_text` leaves out.


def compared_text(text: str) -> str:
    """`text` as two texts are compared: each line without the spaces, tabs and carriage
    returns at its end, and without the blank lines before the first and after the last line
    that holds anything else. Nothing else is left out: indentation, runs of white space
    inside a line, tabs, line breaks, no-break and every other Unicode space are text. The
    verbatim gate (a claim is the whole of each passage it cites), the repeat key (two claims
    fold) and the fact check (a page's facts are its claims') compare by this one function."""
    rows = [line.rstrip(" \t\r") for line in text.split("\n")]
    first = next((n for n, row in enumerate(rows) if row), len(rows))
    last = next((n for n in range(len(rows), first, -1) if rows[n - 1]), first)
    return "\n".join(rows[first:last])


# Whether a claim leaves a code fence open, the one question still put to the Markdown parser,
# of each claim alone and then of the body (fences_kept_in): render_body closes such a fence
# before the next claim (review 7.2).

_PARSER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
_PARSER_LINES = re.compile(r"\r\n?|\n")  # how the parser breaks lines: CR LF, a lone CR, LF
_KNOWN = "cairn"  # the line written after a claim's text, to see what holds it


class FenceLeftOpen(ValueError):
    """A claim's text leaves a code fence open that no closer written after it closes."""


def open_fence_closer(text: str) -> str | None:
    """The line that closes the code fence the Markdown parser leaves open at the end of
    `text`, or None when it leaves none open; FenceLeftOpen when one is open and neither
    closer below closes it.

    The question, asked of the parser's line map and of no character of the text: a known
    line is written after `text` and a blank line, as render_body writes the next claim,
    indented past every column `text` reaches, so a fence in a list item counts when the next
    block could still be the item's. The fence is open when a fence token that starts in
    `text` covers that line. Its closer is the opener's run at the opener's column (tabs read
    to the next stop of four), else the run at the margin; a closer is kept only when, written
    after `text`, no fence covers the known line any more. A text with no backtick and no
    tilde holds no fence and is not parsed."""
    if "`" not in text and "~" not in text:
        return None
    lines = _PARSER_LINES.split(text)
    fence = _covering_fence(text, len(lines), lines)
    if fence is None:
        return None
    opener = lines[fence.map[0]].expandtabs(4)
    for closer in (" " * max(opener.find(fence.markup), 0) + fence.markup, fence.markup):
        if _covering_fence(f"{text}\n{closer}", len(lines) + 1, lines) is None:
            return closer
    raise FenceLeftOpen("a claim leaves a code fence open that no closer line closes")


def _covering_fence(text: str, count: int, lines: list[str]):
    """The fence token that starts in the first `count` lines of `text` and covers the known
    line written after it and a blank line, or None."""
    depth = max(len(line.expandtabs(4)) for line in lines) + 1
    known = count + 1  # the line's index: `count` lines, then the blank one
    for token in _PARSER.parse(f"{text}\n\n{' ' * depth}{_KNOWN}"):
        if token.type == "fence" and token.map and token.map[0] < count <= known < token.map[1]:
            return token
    return None


# The same question, asked of the body: what a claim's lines are depends on the blocks written
# before it (a list item holds what is indented under it, across a blank line), so a passage
# that alone is indented text may open a fence there, and a closer may read as an opener
# (third report, must-fix 4).


CLOSERS_MAX = 16  # the closers fences_kept_in writes in one body, so the parses of a render


def fences_kept_in(blocks: Sequence[str]) -> tuple[list[str], int | None]:
    """`blocks`, the title, headings and claims of a body in its order, which render_body
    joins with a blank line: each as given, or with a line after it that closes a code fence
    the Markdown parser, reading the whole body, opens in that block and runs into a later
    one; and the index of the first block whose fence is left to run on, else None: the
    line did not close it, or CLOSERS_MAX closers were written before it. That block and the
    blocks after it are then as given.

    The parser's line map decides, as in open_fence_closer: a fence leaves its block when its
    token starts in the block and covers the first line of the next. The first such fence
    gets a closer after its block (`_closer`) and the body is parsed again; the closer is
    kept only when no fence that starts in that block, the closer's own line included,
    leaves it any more. Then the next such fence, which starts in a later block. One parse
    when no fence leaves its block and one more for each closer, each of the whole body, so
    at most CLOSERS_MAX + 1 whatever the notes hold (a note's author cannot make a render
    cost a parse for each claim); blocks that hold no backtick and no tilde are not parsed."""
    blocks = list(blocks)
    if not any("`" in block or "~" in block for block in blocks):
        return blocks, None
    found = _leaving_fence(blocks)
    for _ in range(CLOSERS_MAX):
        if found is None:
            break
        at, closer = found
        closed = [*blocks[:at], f"{blocks[at]}\n{closer}", *blocks[at + 1 :]]
        found = _leaving_fence(closed)
        if found is not None and found[0] <= at:
            return blocks, at
        blocks = closed
    return blocks, None if found is None else found[0]


def _leaving_fence(blocks: list[str]) -> tuple[int, str] | None:
    """The first code fence of the body `blocks` make that starts in one block and covers the
    first line of the next, as (the index of the block it starts in, the closer to write
    after that block); None when there is none."""
    body = "\n\n".join(blocks) + "\n"
    first, line = [], 0  # the line of the body each block starts on
    for block in blocks:
        first.append(line)
        line += len(_PARSER_LINES.findall(f"{block}\n\n"))
    for token in _PARSER.parse(body):
        if token.type != "fence" or not token.map:
            continue
        at = bisect_right(first, token.map[0]) - 1
        if at + 1 < len(blocks) and token.map[1] > first[at + 1]:
            return at, _closer(_PARSER_LINES.split(body)[token.map[0]], token.markup)
    return None


def _closer(opener: str, run: str) -> str:
    """The line to write as the closer of a fence the parser opened with `run` on the line
    `opener`: the run after what the line holds before it, its white space as written and
    every other character there a space, so a list item's marker does not start another
    item. Not assumed to close: fences_kept_in asks the parser."""
    before = opener[: max(opener.find(run), 0)]
    return "".join(char if char in " \t" else " " for char in before) + run
