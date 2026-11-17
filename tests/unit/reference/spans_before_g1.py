# Reference copy for the G1 differential test (tests/unit/test_spans_differential_unit.py).
# Below these three lines: src/cairn/spans.py byte for byte as of commit e8b3f04, before G1
# made its fence pattern linear. Nothing imports it but that test. Do not edit, format or fix it.
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
- Front matter: YAML between a `---` on line 1 and the next `---` line.
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
from collections import Counter
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict

SpanKind = Literal["frontmatter", "heading", "paragraph", "bullet", "code", "table", "quote"]

# Matched against a line stripped of its indentation, which is at most 3 spaces.
_HEADING = re.compile(r"(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*")  # drops a closing "#" run
_FENCE = re.compile(r"`{3,}(?!.*`)|~{3,}")  # a backtick fence's info string has no backticks
_BULLET = re.compile(r"(?:[-*+]|\d{1,9}[.)])[ \t]")


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
    if i:
        yield "frontmatter", 0, i
    while i < len(lines):
        kind = _kind(lines[i])
        if kind is None:
            i += 1
            continue
        end = _block_end(kind, lines, i)
        yield kind, i, end
        i = end


def _frontmatter_end(lines: list[str]) -> int:
    """Index after the `---` that closes front matter opened on line 1; 0 if there is none."""
    if lines and lines[0] == "---":
        for j in range(1, len(lines)):
            if lines[j] == "---":
                return j + 1
    return 0


def _kind(line: str) -> SpanKind | None:
    """The kind of block a line starts, or None for a blank line."""
    if not line.strip():
        return None
    body = line.lstrip(" ")  # a leading tab stays, and no block pattern accepts it
    if len(line) - len(body) > 3:  # four spaces of indentation never start a block
        return "paragraph"
    if _HEADING.fullmatch(body):
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
            j = _fence_end(lines, j) if _FENCE.match(line[_content_column(line) :]) else j + 1
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
    column = _content_column(lines[i])
    fence = _FENCE.match(lines[i][column:]).group()
    for j in range(i + 1, len(lines)):
        closer = lines[j].strip()
        if (
            len(closer) >= len(fence)
            and set(closer) == {fence[0]}
            and _indent(lines[j]) <= column + 3
        ):
            return j + 1
    end = len(lines)
    while not lines[end - 1].strip():
        end -= 1
    return end


def _heading(line: str) -> tuple[int, str]:
    """(level, title) of an ATX heading line."""
    hashes, title = _HEADING.fullmatch(line.lstrip(" ")).groups()
    return len(hashes), title


def _span_id(source_id: str, heading_path: tuple[str, ...], text: str, occurrence: int) -> str:
    """Hash a JSON encoding of the fields: unlike joining with separators, it is injective."""
    key = json.dumps([source_id, heading_path, text, occurrence])
    return "sp_" + hashlib.sha256(key.encode()).hexdigest()[:16]
