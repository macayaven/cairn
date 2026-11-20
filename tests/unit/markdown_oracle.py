"""Where inline code is, by the Markdown parser itself, for tests: a second implementation of
cairn.spans.code_spans that shares none of its placing.

The parser is markdown-it-py with every CommonMark rule and tables, raw HTML off and on (code
is where either reading finds it), as cairn reads. Where a character of a block's text comes
from in the source is what the parser's own StateBlock.getLines takes (its container markers
and indentation set aside), recorded as it takes it: cairn.spans instead finds each line of a
block's text at the end of its source line. Where a code span starts and ends in a block's
text is the parser's position when its backtick rule makes the span. A heading's text is the
source between its markers, and a table cell's is found in its row from the left.

`code_places(text)` is the set of places of every character of inline code, its backticks
included; None where cairn.spans says it reads the text exactly (a backtick the parser leaves
as text in the first paragraph, outside any quote or list), so a test can say what it expects
then."""

from markdown_it import MarkdownIt
from markdown_it.rules_block.state_block import StateBlock
from markdown_it.rules_inline.backticks import backtick
from markdown_it.token import Token


class _MappedState(StateBlock):
    """A StateBlock that keeps, for the text its last getLines gave, each character's place
    in the source (None for a space the parser adds for a tab it splits)."""

    def getLines(self, begin: int, end: int, indent: int, keepLastLF: bool) -> str:
        got = super().getLines(begin, end, indent, keepLastLF)
        places: list[int | None] = []
        for line in range(begin, end):
            line_indent, first = 0, self.bMarks[line]
            start = first
            last = self.eMarks[line] + 1 if line + 1 < end or keepLastLF else self.eMarks[line]
            while first < last and line_indent < indent:
                ch = self.src[first]
                if ch in " \t":
                    if ch == "\t":
                        line_indent += 4 - (line_indent + self.bsCount[line]) % 4
                    else:
                        line_indent += 1
                elif first - start < self.tShift[line]:
                    line_indent += 1
                else:
                    break
                first += 1
            places.extend([None] * max(0, line_indent - indent))
            places.extend(range(first, min(last, len(self.src))))
        assert len(places) == len(got), "the map must cover what getLines gives"
        self.last_lines = (got, places)
        return got


def _paragraph_mapped(rule):
    def mapped(state, start_line, end_line, silent):
        made = len(state.tokens)
        if not rule(state, start_line, end_line, silent):
            return False
        lines, places = getattr(state, "last_lines", ("", []))
        for token in state.tokens[made:]:
            if token.type == "inline":
                lead = len(lines) - len(lines.lstrip())
                token.meta["places"] = places[lead : lead + len(token.content)]
        return True

    return mapped


def _heading_mapped(rule):
    def mapped(state, start_line, end_line, silent):
        made = len(state.tokens)
        begin = state.bMarks[start_line] + state.tShift[start_line]
        raw = state.src[begin : state.eMarks[start_line]]
        if not rule(state, start_line, end_line, silent):
            return False
        for token in state.tokens[made:]:
            if token.type == "inline":
                at = raw.lstrip("#").find(token.content)
                skip = len(raw) - len(raw.lstrip("#"))
                if at >= 0:
                    token.meta["places"] = list(
                        range(begin + skip + at, begin + skip + at + len(token.content))
                    )
        return True

    return mapped


def _placed_backticks(state, silent):
    start, made = state.pos, len(state.tokens)
    if not backtick(state, silent):
        return False
    if not silent:
        if len(state.tokens) > made and state.tokens[-1].type == "code_inline":
            state.tokens[-1].meta["at"] = (start, state.pos)
        else:
            state.env.setdefault("left_as_text", []).append(state.src)
    return True


def _parser(html: bool) -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": html}).enable("table")
    mapped = (("paragraph", _paragraph_mapped), ("lheading", _paragraph_mapped))
    for name, wrap in (*mapped, ("heading", _heading_mapped)):
        rule = next(r for r in md.block.ruler.__rules__ if r.name == name)
        md.block.ruler.at(name, wrap(rule.fn), {"alt": list(rule.alt)})  # keeps what it ends
    md.inline.ruler.at("backticks", _placed_backticks)
    return md


_PARSERS = (_parser(False), _parser(True))


def _normalised(text: str) -> tuple[str, list[int]]:
    """The text as the parser reads it (CR LF and a lone CR one line break, NUL as U+FFFD),
    and for each of its characters its place in `text`."""
    out, where, n = [], [], 0
    while n < len(text):
        if text[n] == "\r":
            out.append("\n")
            where.append(n)
            n += 2 if text[n + 1 : n + 2] == "\n" else 1
            continue
        out.append("�" if text[n] == "\0" else text[n])
        where.append(n)
        n += 1
    return "".join(out), where


def _tokens(md: MarkdownIt, src: str, env: dict) -> list[Token]:
    tokens: list[Token] = []
    state = _MappedState(src, md, env, tokens)
    md.block.tokenize(state, state.line, state.lineMax)
    for token in tokens:
        if token.type == "inline":
            token.children = []
            md.inline.parse(token.content, md, env, token.children)
    return tokens


def code_places(text: str) -> set[int] | None:
    if "`" not in text:
        return set()
    src, where = _normalised(text)
    places: set[int] = set()
    for md in _PARSERS:
        env: dict = {}
        tokens = _tokens(md, src, env)
        left = env.get("left_as_text", [])
        cells: dict[int, int] = {}
        for n, token in enumerate(tokens):
            if token.type != "inline":
                continue
            opener = tokens[n - 1]
            first = opener.type == "paragraph_open" and opener.level == 0 and token.map[0] == 0
            if first and token.content in left:
                return None
            codes = [c.meta["at"] for c in token.children or () if c.type == "code_inline"]
            if not codes:
                continue
            mapped = token.meta.get("places")
            if mapped is None:  # a table cell: found in its row from the left
                row = token.map[0]
                begin = sum(len(line) + 1 for line in src.split("\n")[:row])
                line = src.split("\n")[row]
                at = line.find(token.content, cells.get(row, 0))
                if at < 0:
                    return None  # the parser's cell is not its row's text (an escaped pipe)
                cells[row] = at + len(token.content)
                mapped = list(range(begin + at, begin + at + len(token.content)))
            for left_at, right_at in codes:
                for place in mapped[left_at:right_at]:
                    if place is None:
                        return None  # a space the parser added for a split tab
                    places.add(where[place])
                    if text[where[place] : where[place] + 2] == "\r\n":
                        places.add(where[place] + 1)  # a CR LF is one line break
    return places
