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
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterator, Sequence
from itertools import pairwise
from typing import Literal, NamedTuple

from markdown_it import MarkdownIt
from markdown_it.common.utils import MD_TRIM_CHARS
from markdown_it.rules_inline import backtick, image
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


# Whether a claim leaves a code fence open, one of two put to the Markdown parser (code_read),
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


def fences_reaching(before: str, blocks: Sequence[str], line: str) -> list[bool]:
    """Of each of `blocks`, whether a code fence that starts in it takes `line` in, where
    `line` is written after it as the first line of the next block of a body is: after a
    blank line. `before` is the body up to the first of them, ending in its blank line; the
    blocks follow one another as a body writes them, with no code fence running from one
    into the next (the claims a forget asks of: the body closed or named every such fence
    before them), each with `line` after it as `asked_line` writes it.

    The parser's line map decides, as in _leaving_fence: the fence's token starts in the
    block and its map ends past the line. One parse, of `before` and the blocks.

    What an answer is worth. A line that a fence takes in is code, or the fence's closing
    line, and the first line of the next block ends that fence and its list item as it did
    in the body (it is not the fence's, or the fence would run into it): the lines after
    are read as if the line were not there. A line that no fence takes is a block of its
    own, which may change how the blocks after it are read. So the answers up to the first
    False, that one included, are the parser's own for the body in which that block is
    followed by a block starting with `line`; the answers after it are a guess. A caller
    that acts on them asks again, without the lines before (store.erasure._before_it).

    What an answer depends on (asked_line): whether a fence that is open after the block
    and its blank line takes a line in is whether the line is blank or indented at least
    as far as the fence's list item: the parser ends such a fence at the first line that
    holds anything and is indented less, and takes any other line in, as code or as the
    fence's closing line (tests/unit/test_fences_reaching_unit.py, a table and generated
    blocks). So the line is written as asked_line cuts it, and the answers up to the first
    False are the same as with it whole; the guesses after it can differ."""
    parts, first, asked, count = [before], [], [], len(_PARSER_LINES.findall(before))
    for block in blocks:
        first.append(count)
        count += len(_PARSER_LINES.findall(f"{block}\n\n"))
        asked.append(count)  # where `line` stands after this block
        count += 2
        parts.append(f"{block}\n\n{asked_line(line, block)}\n\n")
    taken = [False] * len(first)
    for token in _PARSER.parse("".join(parts)):
        if token.type == "fence" and token.map:
            at = bisect_right(first, token.map[0]) - 1
            if at >= 0 and token.map[0] < asked[at] < token.map[1]:
                taken[at] = True
    return taken


def asked_line(line: str, block: str) -> str:
    """The line fences_reaching writes after `block` to ask whether a fence of it takes
    `line` in: `line` itself when it is no longer than the block's longest line, so a
    question never holds more of it than of the block; else cut to what the answer depends
    on (fences_reaching): nothing for a line of spaces and tabs only, which the parser
    reads as blank, and for any other its indentation and its first other character. An
    indentation deeper than any line of the block reaches (tabs read to the next stop of
    four, as the parser reads them) is written as deep as that, in tabs and then spaces: a
    fence's list item starts its text on a line of the block, so no deeper indentation
    gets another answer. One character more than that indentation, whatever the line's
    length (a closing line of either character, a quotation or list marker, a run of fence
    characters with a letter far along: tests/unit/test_fences_reaching_unit.py)."""
    rows = _PARSER_LINES.split(block)
    if len(line) <= max(map(len, rows)):
        return line
    rest = line.lstrip(" \t")  # the parser's own blanks: no other character indents
    if not rest:
        return ""
    indentation = line[: len(line) - len(rest)]
    deepest = max(len(row.expandtabs(4)) for row in rows)
    if len(indentation.expandtabs(4)) > deepest:
        indentation = "\t" * (deepest // 4) + " " * (deepest % 4)
    return indentation + rest[0]


def _closer(opener: str, run: str) -> str:
    """The line to write as the closer of a fence the parser opened with `run` on the line
    `opener`: the run after what the line holds before it, its white space as written and
    every other character there a space, so a list item's marker does not start another
    item. Not assumed to close: fences_kept_in asks the parser."""
    before = opener[: max(opener.find(run), 0)]
    return "".join(char if char in " \t" else " " for char in before) + run


# Where the Markdown parser reads code in a text read alone (code_read): the parser the web
# view reads with (_PARSER's settings), told to say where it reads each code span. Its token
# of a block of text says which lines the block stands on and the text it parsed; that of a
# code span says only what the span holds, so the backtick rule notes where the span stood in
# the block's text, and the block's text is placed in the lines it was read from (`_placed`).
# Emphasis is not read: its marks are resolved after every code span is found, and never
# decide what is code (tests/unit/test_spans_unit.py reads both ways), and reading them is
# most of a parse of text dense with them.
_CODE_READER = MarkdownIt("commonmark", {"html": False}).enable("table").disable("emphasis")
_PARAGRAPHS = ("paragraph_open", "heading_open=", "heading_open-")  # of whole lines
_CELLS = ("th_open", "td_open")  # a table's cell: its bars written "\\|", read "|"


def _each_block(state) -> None:
    """The parser's own step that parses the text of each block, noting which block it is,
    and where code_read keeps what the rules below note."""
    state.env.setdefault("code", [])
    state.env.setdefault("bases", [0])
    for token in state.tokens:
        if token.type == "inline":
            state.env["block"] = token
            token.children = [] if token.children is None else token.children
            state.md.inline.parse(token.content, state.md, state.env, token.children)


def _code_span(state, silent: bool) -> bool:
    """The parser's own backtick rule, noting where the code of each span it reads stands in
    the block's text, between the span's two runs, and how long a run is."""
    start, count = state.pos, len(state.tokens)
    if not backtick(state, silent):
        return False
    if not silent and len(state.tokens) > count and state.tokens[-1].type == "code_inline":
        run, base = len(state.tokens[-1].markup), state.env["bases"][-1]
        found = (state.env["block"], base + start + run, base + state.pos - run, run)
        state.env["code"].append(found)
    return True


def _picture(state, silent: bool) -> bool:
    """The parser's own rule for a picture, which parses the picture's words as a text of
    their own: they start two characters after the rule does (`![`)."""
    if silent:
        return image(state, silent)
    state.env["bases"].append(state.env["bases"][-1] + state.pos + 2)
    try:
        return image(state, silent)
    finally:
        state.env["bases"].pop()


_CODE_READER.core.ruler.at("inline", _each_block)
_CODE_READER.inline.ruler.at("backticks", _code_span)
_CODE_READER.inline.ruler.at("image", _picture)


class _Block(NamedTuple):
    """A block's text as the parser parsed it, and where it stands in the text it was read
    from: where each of its lines begins in it, where each starts in that text (None where
    it cannot be placed: `_placed`), and where it holds a bar the text writes "\\|"."""

    first: int  # the line of the text its first line was read from
    begins: list[int]
    starts: list[int] | None
    bars: list[int]

    def row(self, offset: int) -> int:
        return bisect_right(self.begins, offset) - 1

    def at(self, offset: int) -> int:
        """Where the character at `offset` of the block's text stands in the text read."""
        row = self.row(offset)
        assert self.starts is not None
        before = bisect_left(self.bars, offset) - bisect_left(self.bars, self.begins[row])
        return self.starts[row] + offset - self.begins[row] + before


def code_read(text: str) -> list[tuple[int, int]]:
    """The stretches of `text` the Markdown parser reads as code, `text` read alone as the web
    view reads it, as (start, end) in its characters, in the order they start: each fenced
    or indented block of code, its lines whole (a fence's opening and closing lines
    included); and the code of each code span, between its backtick runs, the line ends it
    runs across included (in a picture's words too). Text indented under a list item is the
    item's prose, and the parser says so. A text with no backtick, tilde, tab or four spaces
    in a row holds no code and is not parsed. Where a block's text cannot be placed in the
    lines it was read from (`_placed`), or a code span placed does not stand between two
    runs of backticks, the span covers the lines it stands on: never less than its code.
    One parse, and work in proportion to the text and its code spans."""
    if not any(mark in text for mark in ("`", "~", "\t", "    ")):
        return []
    lines = _PARSER_LINES.split(text)
    starts = [0, *(found.end() for found in _PARSER_LINES.finditer(text))]
    env: dict = {}
    tokens = _CODE_READER.parse(text, env)
    read = [
        (starts[first], starts[last - 1] + len(lines[last - 1]))
        for token in tokens
        if token.type in ("fence", "code_block") and token.map
        for first, last in [token.map]
    ]
    blocks: dict[int, _Block] = {}
    after: dict[int, int] = {}  # each line: where the text of the last cell placed on it ends
    for opener, block in pairwise(tokens):
        if block.type == "inline" and block.map:
            kind = opener.type + (opener.markup if opener.type == "heading_open" else "")
            blocks[id(block)] = _placed(block.content, kind, block.map[0], lines, starts, after)
    for token, start, end, run in env["code"]:
        block = blocks[id(token)]
        bounds = block.starts and (block.at(start), block.at(end))
        if not bounds or not (
            bounds[0] <= bounds[1]
            and text[bounds[0] - run : bounds[0]] == text[bounds[1] : bounds[1] + run] == "`" * run
        ):
            first, last = block.first + block.row(start), block.first + block.row(end)
            bounds = (starts[first], starts[last] + len(lines[last]))
        read.append(bounds)
    return sorted(read)


def _placed(
    content: str,
    kind: str,
    first: int,
    lines: Sequence[str],
    starts: Sequence[int],
    after: dict[int, int],
) -> _Block:
    """A block's text placed in the text it was read from, from line `first` on (`_Block`).
    The lines of a paragraph are the ends of the lines they were read from, after their
    indentation and the marks of their quotation or list item, the first without its blanks
    before and the last without those after it; the parser writes a tab it reads in part as
    spaces, which stand before the line's end. A heading's or a cell's text is a piece of
    its one line, the first such piece after the cell placed before it on that line, a
    cell's with each bar it reads "|" written "\\|"."""
    pieces = content.split("\n")
    begins = [0]
    for piece in pieces[:-1]:
        begins.append(begins[-1] + len(piece) + 1)
    bars = [at for at, char in enumerate(content) if char == "|"] if kind in _CELLS else []
    placed: list[int] | None = []
    for n, piece in enumerate(pieces):
        line, last = lines[first + n], n == len(pieces) - 1
        if kind in _PARAGRAPHS:
            ends = (line.rstrip(MD_TRIM_CHARS), line.rstrip()) if last else (line,)
            end = next((len(end) for end in ends if end.endswith(piece.lstrip(" "))), None)
            if end is None:
                placed = None
                break
            placed.append(starts[first + n] + end - len(piece))
        else:
            written = piece.replace("|", "\\|") if kind in _CELLS else piece
            at = line.find(written, after.get(first + n, 0))
            if at < 0:
                placed = None
                break
            after[first + n] = at + len(written)
            placed.append(starts[first + n] + at)
    return _Block(first, begins, placed, bars)
