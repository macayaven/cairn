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

import bisect
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.rules_inline.backticks import backtick
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


# Inline code, as the Markdown parser reads it (second report, finding 2). A passage is
# handed to the parser as the note has it: a quote's ">" and a list item's marker and
# indentation are in its text, so its container is the parser's to read. Two readings are
# asked, raw HTML off (as the web view renders) and on (as a reader of the raw Markdown may),
# and code is where either finds it: reading too much as code keeps two texts apart, the safe
# side.


_LINE_BREAK = re.compile("\n")
_PARSER_LINES = re.compile(r"\r\n?|\n")
_TAG_START = re.compile(r"<[A-Za-z/!?]")  # where raw HTML or an autolink may start


def _placed_rule(rule):
    """The parser's backtick rule, noting where each code span it makes starts and ends in
    the text it parses (`meta["at"]`), and in `env` when it leaves a run as text."""

    def placed(state, silent: bool) -> bool:
        start, made = state.pos, len(state.tokens)
        if not rule(state, silent):
            return False
        if not silent:
            if len(state.tokens) > made and state.tokens[-1].type == "code_inline":
                state.tokens[-1].meta["at"] = (start, state.pos, state.src)
            else:
                state.env.setdefault("text_ticks", set()).add(state.src)
        return True

    return placed


def _parser(html: bool) -> MarkdownIt:
    """CommonMark with tables, raw HTML off or on, reading only what decides where inline code
    is: every block rule, and of the inline rules escapes, code spans, autolinks and raw HTML
    (which bind as tightly as code spans or more), never emphasis, links, images or entities
    (which code spans bind more tightly than: they move no code span)."""
    md = MarkdownIt("commonmark", {"html": html}).enable("table")
    md.disable(["emphasis", "link", "image", "entity", "balance_pairs", "fragments_join"])
    md.disable("text_join")
    md.inline.ruler.at("backticks", _placed_rule(backtick))
    return md


_PARSERS = (_parser(html=False), _parser(html=True))


def code_spans(text: str) -> list[tuple[int, int]] | None:
    """Where inline code is in Markdown text, as the parser (markdown-it-py, CommonMark with
    tables) reads the text as a document of its own: (start, end) of each code span, its
    backtick runs included, in order and apart. The one finder of inline code: the gate's
    reading of prose (cairn.gate), the repeat key that shares it (cairn.arrange) and the fact
    check's normalize (cairn.facts) call it.

    None when the parser's answer cannot be trusted to be the reader's, and the caller then
    reads the whole text exactly, white space included: a backtick run the parser pairs with
    nothing in the text's first paragraph when that paragraph is in no quote or list (the
    one container a passage's text may not show: cairn.spans ends a quote at a line without
    ">", which Markdown reads on as the quote's, so a backtick the quote leaves open may pair
    with one of the passage's), or code whose place in the text the
    parser's own text does not show (a tab partly read as indentation).

    A text with no backtick is not parsed. CR LF is read as a line break and a lone CR as
    one, as the parser reads them; the places are the text's own."""
    if "`" not in text:
        return []
    memo = _READ_ONCE.get()
    if memo is not None and text in memo:
        return memo[text]
    found = _code_spans(text)
    if memo is not None:
        memo[text] = found
    return found


_READ_ONCE: ContextVar[dict[str, list[tuple[int, int]] | None] | None] = ContextVar(
    "cairn_code_read_once", default=None
)


@contextmanager
def code_read_once() -> Iterator[None]:
    """Within the block, code_spans parses each text once and gives the same answer each time
    it is asked again (a passage's facts and its norm, a claim and the passage it copies).
    The answers are dropped when the outermost block ends: nothing is kept between calls."""
    if _READ_ONCE.get() is not None:
        yield
        return
    token = _READ_ONCE.set({})
    try:
        yield
    finally:
        _READ_ONCE.reset(token)


def _code_spans(text: str) -> list[tuple[int, int]] | None:
    """code_spans, parsed."""
    src = text.replace("\r\n", " \n").replace("\r", "\n").replace("\0", "\ufffd")
    found: list[tuple[int, int]] = []
    for md in _PARSERS if _TAG_START.search(text) else _PARSERS[:1]:
        read = _code_read(md, src)
        if read is None:
            return None
        found.extend(read)
    return _apart(found)


def _code_read(md: MarkdownIt, src: str) -> list[tuple[int, int]] | None:
    """The code spans one parser reads in `src`, placed in it; None as code_spans says."""
    env: dict = {}
    tokens = md.parse(src, env)
    ticks = env.get("text_ticks", set())
    lines = src.split("\n")
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    cursors: dict[int, int] = {}  # each line's place past the last cell found on it
    found: list[tuple[int, int]] = []
    for n, token in enumerate(tokens):
        if token.type != "inline" or not token.children:
            continue
        opener = tokens[n - 1]
        if token.content in ticks and _may_go_on(opener):
            return None
        codes = [c.meta["at"] for c in token.children if c.type == "code_inline"]
        if not codes:
            continue
        content = token.content
        if any(read_in is not content and read_in != content for _, _, read_in in codes[:1]):
            return None  # read in a text of its own, at places of its own
        rows = _rows_placed(token, opener.type, lines, cursors)
        if rows is None:
            return None
        breaks = [0, *(m.end() for m in _LINE_BREAK.finditer(content))]
        for left, right, _ in codes:
            at = (
                _place(breaks, rows, starts, token.map[0], left),
                _place(breaks, rows, starts, token.map[0], right),
            )
            if src[at[0] : at[1]] != content[left:right]:
                return None
            found.append(at)
    return found


def _may_go_on(opener) -> bool:
    """Whether a paragraph may be the lazy end of one the note writes before the passage: the
    first block of the text, a paragraph outside any quote or list (cairn.spans ends a quote
    at a line without ">", where Markdown reads that line on as the quote's)."""
    return opener.type == "paragraph_open" and opener.level == 0 and opener.map[0] == 0


def _rows_placed(token, opener: str, lines: list[str], cursors: dict[int, int]) -> list[int] | None:
    """Where each line of an inline token's text starts in its line of the source: a
    paragraph's lines are the ends of theirs, its last up to its trailing white space; a
    heading's text is the last of its line holding it, and a table's cells are found left to
    right. None when a line of the token's text is not so found in its line."""
    rows = token.content.split("\n")
    first, end = token.map
    if len(rows) != end - first:
        return None
    placed = []
    for i, row in enumerate(rows):
        line = lines[first + i]
        if opener == "paragraph_open":
            whole = line if i < len(rows) - 1 else line.rstrip()
            if not whole.endswith(row):
                return None
            placed.append(len(whole) - len(row))
        elif opener == "heading_open":
            at = line.rfind(row)
            if at < 0:
                return None
            placed.append(at)
        else:  # a table cell
            at = line.find(row, cursors.get(first + i, 0))
            if at < 0:
                return None
            cursors[first + i] = at + len(row)
            placed.append(at)
    return placed


def _place(breaks: list[int], rows: list[int], starts: list[int], first: int, at: int) -> int:
    """The place in the source of place `at` of an inline token's text, whose lines start at
    `breaks` and are found at `rows` in the source's lines from line `first`."""
    row = bisect.bisect_right(breaks, at) - 1
    return starts[first + row] + rows[row] + at - breaks[row]


def _apart(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The spans in order, those that overlap or touch made one."""
    out: list[tuple[int, int]] = []
    for left, right in sorted(spans):
        if out and left <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], right))
        else:
            out.append((left, right))
    return out


_PROBE = ""  # a character of the private use area: no note's Markdown means it


def open_fence_closer(text: str) -> str | None:
    """The line that closes the code fence the Markdown parser (as the web view reads it)
    leaves open at the end of `text`, or None when none is: the opener's run, written at the
    opener's column (tabs read as Markdown reads them, to the next stop of four).

    A fence is open when a line written after `text` and a blank line, indented past every
    list item `text` may open, is read as the fence's code: so a fence in a list item counts
    when the next block could still be the item's, and one in a quote never does, as the blank
    line ends the quote. A text with no backtick and no tilde holds no fence and is not
    parsed."""
    if "`" not in text and "~" not in text:
        return None
    lines = _PARSER_LINES.split(text)  # as the parser breaks lines: CR LF, a lone CR, LF
    depth = max(len(line.expandtabs(4)) for line in lines) + 1
    tokens = _PARSERS[0].parse(f"{text}\n\n{' ' * depth}{_PROBE}")
    for token in tokens:
        if token.type == "fence" and _PROBE in token.content and token.map:
            opener = lines[token.map[0]].expandtabs(4)
            return " " * opener.find(token.markup) + token.markup
    return None
