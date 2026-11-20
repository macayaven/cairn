"""Turn selected Slack message text into a note, without provider I/O.

The note holds one heading, its first line: who wrote the message and when, as the caller
verified. Where a heading begins is asked of the note as written, never worked out from the
message: of the passage splitter (cairn.spans.chunk), whose heading paths say whose words a
passage is, of the Markdown parser as the web view reads, and of that parser reading each
passage alone, as a page shows a claim (_headings). Every line one of them finds a heading
on is written as text, and no other line where a line at a reading can tell (_one_heading):
the note says whose words it holds, so a backslash no reader asked for is a false quotation.

What Slack displayed as it is, is markup nowhere. A code block of Slack's own is a fence on
lines of its own, which every reader reads as one block of code; code within a line stands
between the note's only bare backticks (_render). A line both readers read as code in the
finished note, where every character is shown as it is, holds what Slack displayed and none
of the marks that write prose (_code_as_shown). The splitter's line boundaries
(str.splitlines: more than a line feed) are each written as a line feed first, a CR LF as
one.
"""

import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from urllib.parse import quote, unquote, urlencode, urlsplit

from markdown_it import MarkdownIt
from markdown_it.common.utils import isMdAsciiPunct, isPunctChar, isWhiteSpace

from cairn.spans import chunk
from cairn.store.memory import TITLE_MAX
from cairn.topics import one_line

# An email address. Every pattern here reads each character of what it is run over a bounded
# number of times, however long the text (the third tester, finding 9): a run of the
# characters an address may start with is read once, from its start (`(?<!...)`, possessive),
# since a match from inside it ends where one from its start does; and a bracketed domain
# holds no "[", so it is read up to the next one at most. _redact finds a match that starts
# right where the last one ended, as one search from there would.
_LOCAL = r"[\w.!#$%&'*+/=?^`{|}~-]"
_AT = r"""@(?:\[[^\[\]\n]++\]|[\w.-]+)"""
_EMAIL = re.compile(rf"""(?:"[^"\n]++"|(?<!{_LOCAL}){_LOCAL}++){_AT}""")
_EMAIL_HERE = re.compile(rf"""(?:"[^"\n]++"|{_LOCAL}++){_AT}""")
_TOKEN_START = re.compile(r"[`<]")  # where a token may start (_tokens)
_DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)
_TIMESTAMP = re.compile(r"[0-9]{1,12}\.[0-9]{6}")
_PARSER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
# A line that can underline the text above it into a heading, inside a quote or not.
_UNDERLINE = re.compile(r"[ \t>]*(=+|-+)[ \t]*")
# A line that can make the one above it a table's first row, and cut that row at its bars.
_SECOND_ROW = re.compile(r"[ \t>]*[|:-][|: \t-]*")
# What stands before a code block on its line when the block is in a quotation, as Slack
# sends it; and a quotation's mark at the start of a line, as Slack displayed it.
_QUOTED = re.compile(r"(?:[ \t]*&gt;)+[ \t]*")  # each blank one place it can stand
_QUOTE_MARK = re.compile(r"^[ \t]*>[ \t]?")
# What may stand before a fence on its line: blanks, a quotation's marks, a list item's.
_MARKS = " \t>*+-0123456789.)"
_FENCE_LEAD = re.compile(r"^([ \t>*+\-0-9.)]*+)~(?=~~)", re.MULTILINE)
# What a title is cut between (_title): an escape, an entity, Slack's bold, a character; and
# the run of "#" that would end the page's heading of it, and be dropped.
_ATOM = re.compile(r"\\.|&(?:amp|lt|gt);|\*\*|.", re.DOTALL)
_CLOSING = re.compile(r"(?:^|(?<=[ \t]))#+$")
_CODE_NODES = 10_000  # see _displayed_code
_PASSES = 8  # see _one_heading


def _redact(text: str) -> str:
    """`text` with each email address removed, as one substitution of the pattern from left
    to right removes them (_EMAIL)."""
    if "@" not in text:
        return text
    pieces, at = [], 0
    while found := (at and _EMAIL_HERE.match(text, at)) or _EMAIL.search(text, at):
        pieces += [text[at : found.start()], "[email removed]"]
        at = found.end()
    return "".join([*pieces, text[at:]])


def _decode(text: str) -> str:
    # Slack documents only these three escapes, not general HTML entities.
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _shown(text: str) -> str:
    """Text as Slack displayed it, an email address removed: what _literal and _prose start
    from, and what a line both readers read as code holds (_code_as_shown)."""
    return _redact(_decode(text))


def _plain(text: str) -> str:
    """Text as Slack displayed it, written so that Markdown displays each character as it is."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]()!#~])", r"\\\1", text)


def _literal(text: str) -> str:
    return _plain(_redact(_decode(text)))


def _name(name: str, literal: Callable[[str], str] = _literal) -> str:
    """A name as Slack gives it, on one line: it cannot start a line of the note."""
    return literal(" ".join(name.splitlines()))


def _prose(
    text: str, clear: bool = True, last: str = " ", then: str = " ", lead: str | None = ""
) -> str:
    """Text Slack displayed as prose, as Markdown that displays the same. A backtick here is
    one Slack paired with no other and displayed as it is: left bare, Markdown would pair it
    with the backtick that opens a code span (_render) and read that span's code as prose.

    Struck text (`~done~`) is written as it was typed: a page cannot strike, and shows the
    tildes, where doubled ones would show two of each. Slack has no fence of tildes, so a run
    of three tildes or more that only blanks and the marks of a quotation or a list item
    stand before on its line (_FENCE_LEAD: where a reader could take it for a fence, at any
    indentation and in any container) gets a backslash before its first tilde. `clear`:
    whether what was written on the line before `text` is such marks alone, or nothing.
    Emphasis is written where Slack shows it and nowhere else (_emphasis; `last` and
    `then`: what the note writes just before and just after `text`, `lead` what of the
    marks that start its line _CONTAINER has yet to read: _lead)."""
    text = _emphasis(_redact(_decode(text)), last, then, lead)
    if clear:
        return _FENCE_LEAD.sub(r"\1\\~", text)
    first, feed, rest = text.partition("\n")
    return first + feed + _FENCE_LEAD.sub(r"\1\\~", rest)


def _paired(text: str) -> dict[int, int]:
    """The places of the `*`, `_` and `~` that Slack shows as emphasis in `text`, prose as
    Slack displayed it, each with the place of the mark it pairs with. Slack's page on
    message text gives `*bold*`, `_italic_` and `~strike~` and says nothing of where a mark
    opens or closes; the rule is the third tester's model of it (its `slack_styles`) with
    "not inside a word": from left to right, a mark opens where neither a blank nor a mark
    follows it and neither a letter nor a digit stands before it, and closes at the first
    same mark on its line, a character on at least, that has no blank before it and
    neither a letter nor a digit after it; the marks between are characters. Where none
    closes it, it is a character. Each character is read a bounded number of times: the
    closers of each mark are found once, and looked for from a place that only moves on."""
    closers: dict[str, list[int]] = {"*": [], "_": [], "~": []}
    for at, mark in enumerate(text):
        after = text[at + 1 : at + 2]
        if mark in closers and at and not text[at - 1].isspace() and not after.isalnum():
            closers[mark].append(at)
    next_one = dict.fromkeys(closers, 0)
    feed, paired, at = _Next(text, "\n", len(text)), {}, 0
    while at < len(text) - 1:
        mark, after, before = text[at], text[at + 1], text[at - 1 : at]
        opens = not after.isspace() and after not in closers and not before.isalnum()
        if mark in closers and opens:
            ends, n = closers[mark], next_one[mark]
            while n < len(ends) and ends[n] < at + 2:
                n += 1
            next_one[mark] = n
            if n < len(ends) and ends[n] < feed(at):
                paired.update({at: ends[n], ends[n]: at})
                at = ends[n] + 1
                continue
        at += 1
    return paired


_ESCAPED = {"&": "&amp;", "<": "&lt;", **{mark: "\\" + mark for mark in "\\[]()!`"}}
BLANKS = {"  ", " \t", "\t ", "\t\t"}  # a blank each side
# The marks of quotations and list items that may start a line (_emphasis), and a list
# item's mark as far as it is written while the line's next character is still to come.
_CONTAINER = re.compile(r"(?:[ \t>]|(?:[-+*]|[0-9]{1,9}[.)])(?=[ \t]))*")
_PARTIAL = re.compile(r"[-+*]|[0-9]{1,9}[.)]?")


def _lead(lead: str | None, written: str) -> str | None:
    """What of the marks that start a line _CONTAINER has yet to read once `written` is
    written on it, `lead` before: "" where they are all read or none is written yet, the
    start of a list item's mark whose blank is still to come, and None where the line holds
    something past them. The note's line, not the message's: a reference's words count."""
    _, feed, last = written.rpartition("\n")
    if not feed and lead is None:
        return None
    line = last if feed else lead + last
    rest = line[_CONTAINER.match(line).end() :]
    return rest if not rest or _PARTIAL.fullmatch(rest) else None


def _emphasis(text: str, last: str = " ", then: str = " ", lead: str | None = "") -> str:
    """`text`, prose as Slack displayed it, as Markdown: a `*` and a `_` that Slack shows as
    emphasis (_paired) written `**` and `_`, so the page shows bold and italic, and a `~` as
    typed (a page cannot strike). Every other `*` and `_` is the character typed, with a
    backslash before it unless no reader could take it for a mark: a `_` between two
    letters or digits, a `*` between two blanks past the marks of a quotation or a list
    that start its line (_CONTAINER: never a list's mark, nor emphasis, nor a rule). The
    characters Markdown reads as markup are escaped as everywhere in prose.

    Where a span of bold or italic ends right where another of the same mark starts, the two
    marks between are left out: the page shows one span of the same words, where it would
    read `**a****b**` otherwise. And a span is written so only where the page's parser reads
    its two marks as emphasis (_reads): `last` and `then` are the characters the note writes
    just before and just after `text` (a blank where it starts or ends a line). Else its
    marks are typed too, with their backslashes: `*(b)*` before a date's words shows so.
    `lead`: what of the marks that start the line `text` starts on is written before it and
    yet to be read (_lead), None where the line holds more."""
    paired, written = _paired(text), []
    for at, character in enumerate(text):
        if at in paired:
            touches = at + 1 if paired[at] < at else at - 1  # the other side of the mark
            if (
                paired.get(touches, at) != at
                and text[touches] == character != "~"
                and (paired[touches] > touches) == (paired[at] < at)
            ):
                written.append("")  # a span ends where another starts: the two make one
                continue
            written.append("**" if character == "*" else character)
        elif character in "*_":
            written.append("\\" + character)
        else:
            written.append(_ESCAPED.get(character, character))
    for at, close in paired.items():  # Markdown's reading needs the marks as written
        if close < at or text[at] == "~" or not written[at]:
            continue
        chain = [at, close]  # the marks of the spans that make one, each read once
        while not written[chain[-1]]:
            chain += [chain[-1] + 1, paired[chain[-1] + 1]]
        close = chain[-1]
        before = written[at - 1][-1:] if at else last
        after = written[close + 1][:1] if close + 1 < len(text) else then
        if not _reads(text[at], before, written[at + 1][:1], written[close - 1][-1:], after):
            for mark in chain:
                written[mark] = "\\" + text[mark]
    line = 0 if lead is None else _CONTAINER.match(lead + text).end() - len(lead)
    for at, character in enumerate(text):  # `line`: where the marks that start its line end
        if at and text[at - 1] == "\n":
            line = _CONTAINER.match(text, at).end()
        before, after = text[at - 1 : at], text[at + 1 : at + 2]
        if at in paired:
            continue
        if character == "_" and before.isalnum() and after.isalnum():
            written[at] = character  # inside a word: never a mark to Markdown
        if character == "*" and at >= line and before + after in BLANKS:
            written[at] = character  # between blanks past a line's marks: never one either
    return "".join(written)


def _reads(mark: str, before: str, first: str, end: str, after: str) -> bool:
    """Whether the page's parser reads a span of `mark` as emphasis: the characters the note
    writes before its opening mark and after its closing one, and its first and last, as
    markdown-it decides whether a run of marks can open or close (CommonMark's flanking)."""

    def kind(character: str) -> str:
        if not character or isWhiteSpace(ord(character)):
            return "blank"
        return "mark" if isMdAsciiPunct(ord(character)) or isPunctChar(character) else "word"

    outside, inside = kind(before), kind(first)
    opens = inside != "blank" and (inside != "mark" or outside != "word")
    if mark == "_":  # a `_` touching a letter or a digit outside neither opens nor closes
        opens = opens and outside != "word"
    inside, outside = kind(end), kind(after)
    closes = inside != "blank" and (inside != "mark" or outside != "word")
    if mark == "_":
        closes = closes and outside != "word"
    return opens and closes


def _link_target(target: str) -> str | None:
    target = _decode(target)
    if any(character.isspace() or ord(character) < 32 for character in target):
        return None
    if "\\" in target:
        return None
    decoded = unquote(target)
    # Do not retain email addresses hidden in URLs, including nested percent encodings.
    for _ in range(4):
        if _EMAIL.search(decoded):
            return None
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    if "%25" in decoded.lower() or "%40" in decoded.lower():
        return None
    try:
        parsed = urlsplit(target)
        if (
            parsed.scheme.lower() not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        _ = parsed.port  # Also reject malformed port metadata.
    except ValueError:
        return None
    return quote(target, safe=":/?#%=&+@,;~-._")


def _special(target: str, label: str) -> str | None:
    """What Slack displays for one of its special forms, `<!target|label>`, as its page on
    message text describes them and nothing else: "@here", "@channel" and "@everyone"; a
    user group's mention or a date as the fallback text the message gives (`label`), and
    without one "[user group]" or "[date]", since the page leaves what Slack shows then to
    Slack. None for any other reference."""
    if target in ("!here", "!channel", "!everyone"):
        return "@" + target[1:]
    for form, word in (("!subteam^", "[user group]"), ("!date^", "[date]")):
        if target.startswith(form):
            return label or word
    return None


def _headings(text: str) -> dict[int, str]:
    """Each line after the first that makes a heading in `text`, with the character that makes
    it one: a line's first "#", or the "=" or "-" of a line that underlines a heading. Three
    readings find them: the splitter's; the Markdown parser's of the whole; and the parser's
    of each passage alone, which is what a page shows of a claim and what its body holds one
    after another (dashes under a line that a quotation takes in are a rule to the note
    whole, and underline that line where it stands alone). The third parses a passage only
    when a line of it could make a heading there.

    A line written as text joins the paragraph above it, and a line of "=" or "-" below would
    underline that paragraph next; so once a heading is found, every such line below it that
    is not in a code fence is given too, and a run of them costs one more reading, not one
    each. (Indented code is not spared: the paragraph a line joins can take it in.) Not every
    line so given would read as a heading: _one_heading says when they are all written."""
    lines, passages = text.split("\n"), chunk("", text)
    found = {span.start_line - 1: "#" for span in passages if span.kind == "heading"}
    code: set[int] = set()
    for first, read in [(0, text), *[(span.start_line - 1, span.text) for span in passages[1:]]]:
        rest = read.split("\n")[1:] if first else [""]
        if not first or "#" in read or any(_UNDERLINE.fullmatch(row) for row in rest):
            for token in _PARSER.parse(read):
                if token.type == "heading_open" and token.map:
                    underlined = token.markup in ("=", "-")
                    at = first + (token.map[1] - 1 if underlined else token.map[0])
                    found.setdefault(at, token.markup[0])
                elif token.type == "fence" and token.map and not first:
                    code.update(range(*token.map))
    found.pop(0, None)
    for below in range(min(found, default=len(lines)) + 1, len(lines)):
        if (
            below not in found
            and below not in code
            and (under := _UNDERLINE.fullmatch(lines[below]))
        ):
            found[below] = under[1][0]
    return found


def _made_text(text: str, found: dict[int, str], every: bool) -> str | None:
    """`text` with a backslash before what makes a line of `found` a heading, so the words stay
    and read as text: the first such line, or `every` one. Then it is read again (_headings),
    and so on: _PASSES readings with the one that gave `found`. None when the last of them
    still finds a heading."""
    lines = text.split("\n")
    for _ in range(_PASSES - 1):
        if not found:
            break
        for index in found if every else [min(found)]:
            lines[index] = lines[index].replace(found[index], "\\" + found[index], 1)
        found = _headings("\n".join(lines))
    return None if found else "\n".join(lines)


def _code(note: str) -> set[int]:
    """The lines the Markdown parser reads as code in `note`, fenced or indented."""
    return {
        line
        for token in _PARSER.parse(note)
        if token.type in ("fence", "code_block") and token.map
        for line in range(*token.map)
    }


def _read(note: str) -> tuple[list[tuple[str, int, int]], set[int], set[int]]:
    """How the two readers read `note`: the passages the splitter cuts it into; the lines the
    Markdown parser reads as code in the whole of it; and those of them it also reads as code
    in their own passage alone, which is what a claim's text is and what a page shows."""
    passages, code = chunk("", note), _code(note)
    alone = {
        span.start_line - 1 + line
        for span in passages
        if not code.isdisjoint(range(span.start_line - 1, span.end_line))
        for line in _code(span.text)
    }
    return [(span.kind, span.start_line, span.end_line) for span in passages], code, code & alone


def _code_as_written(text: str, note: str) -> str:
    """`note`, made of `text` by backslashes alone, with each line its Markdown parser reads
    as code as `text` has it, but for a backslash where a reader then finds a heading (the
    passage splitter reads headings in some lines the parser reads as code): a line at a
    reading (_made_text), so none is one too many. Should _PASSES readings not finish that,
    every line of code the splitter then reads as a heading keeps the backslash it had, at
    once; and `note` itself unless one more reading then finds one heading."""
    written, made, code = text.split("\n"), note.split("\n"), _code(note)
    lines = [written[at] if at in code else line for at, line in enumerate(made)]
    if lines == made:
        return note
    put_back = "\n".join(lines)
    kept = _made_text(put_back, _headings(put_back), False)
    if kept is not None:
        return kept
    for span in chunk("", put_back):
        if span.kind == "heading" and span.start_line > 1:
            lines[span.start_line - 1] = made[span.start_line - 1]
    return note if _headings("\n".join(lines)) else "\n".join(lines)


def _one_heading(text: str) -> str:
    """`text` with a backslash before what makes any line after its first a heading, and
    nowhere else where that can be told.

    What a line reads as depends on the lines above it, and on no backslash written below it.
    So the first line a reading finds is a heading whatever is done below it, and it alone
    gets the backslash; the note is read again, and the lines above the next one found are
    then as they will stay. Every backslash written so is needed: without it, a reader finds
    a second heading. It costs a reading a line, so it is done only when the first reading
    gives fewer than _PASSES lines that could need one (a line of "=" or "-" under a blank
    line underlines nothing and is not counted).

    A message with more such lines, or one _PASSES readings do not finish, is written from
    the start as it was before: every line _headings gives at each reading, the lines its
    underline rule adds among them, a rule or indented code that no reader took for a heading
    too. Code is then put back as written (_code_as_written); the other lines keep the
    backslash, which is the limit. ValueError rather than a note with a second heading,
    should _PASSES readings not be enough then either, after twice _PASSES readings less
    one; a note costs three times _PASSES at most."""
    text = "\n".join(text.splitlines())
    lines, found = text.split("\n"), _headings(text)
    if sum(mark == "#" or bool(lines[at - 1].strip()) for at, mark in found.items()) < _PASSES:
        note = _made_text(text, found, False)
        if note is not None:
            return note
    note = _made_text(text, found, True)
    if note is None:
        raise ValueError("The message's headings could not be written as text")
    return _code_as_written(text, note)


def _code_as_shown(text: str, shown: str) -> str:
    """The note of a message: `text`, the message as the prose rules write it, with one
    heading (_one_heading), and each line both readers read as code in that note (_read: the
    parser in the note whole, and in the line's own passage alone) as `shown` has it: the
    message as Slack displayed it, line for line. Slack has no indented code and Markdown
    has, so the rules that write prose (a backslash before a bracket, a doubled asterisk)
    wrote on lines that the note shows as they are.

    What is displayed is not escaped, so it may be read as Markdown nowhere: a line the note
    reads as code and its passage does not (a fence the parser reads on and the splitter
    ends) would be a link or a heading on a page, and keeps the prose. And which lines are
    code is asked of the finished note, so it is made twice: of `text`, and then of `text`
    with those lines as `shown` has them. The second is the note only if both readers read
    it as they read the first, passage for passage and line for line: a link's label of
    tildes is "~~~" as displayed and would end the fence it stands in. Then, or should the
    second making refuse or the two texts not have the same lines, the note is the first, as
    prose throughout, but for the white space that ends a line of it both read as code (the
    message's last: prose is cut there), if they then read it as before. One making when no
    such line differs between the two texts; so the readings of _one_heading, or twice them."""
    first = _one_heading(text)
    written, displayed = text.split("\n"), shown.split("\n")
    if len(written) != len(displayed):
        return first
    differs = {at for at, line in enumerate(written) if line != displayed[at]}
    read = differs and _read(first)
    code = read and differs & read[2]
    if not code:
        return first
    try:
        second = _one_heading(
            "\n".join(displayed[at] if at in code else line for at, line in enumerate(written))
        )
    except ValueError:
        second = None
    if second is not None and _read(second) == read:
        return second
    kept = first.split("\n")
    for at in code:
        ends = displayed[at][len(written[at]) :]
        if displayed[at].startswith(written[at]) and not ends.strip():
            kept[at] += ends
    ended = "\n".join(kept)
    return ended if ended != first and _read(ended) == read else first


def _apart(text: str, below: bool, above: bool, more: bool, fresh: bool) -> str:
    """`text`, what a message holds between two of its tokens, with a code block of Slack's
    own kept on lines of its own: when one stands above it (`below`), the blanks that follow
    the block on its last line go and, if anything follows (`more`), it starts a line; when
    one stands under it (`above`), the blanks before the block go if nothing else is on its
    line (`fresh`: nothing was written on it before `text`). Else they end that line, as
    they were displayed, and _render ends it."""
    if below:
        text = text.lstrip(" \t")
        if (text or more) and not text.startswith("\n"):
            text = "\n" + text
    before, feed, blanks = text.rpartition("\n")
    return before + feed if above and not blanks.strip(" \t") and (feed or fresh) else text


def _lines(text: str) -> str:
    """`text` with each boundary the passage splitter breaks at as a line feed, and no other
    control character but the tab."""
    text = "\n".join(text.splitlines())
    return "".join(character for character in text if character in "\n\t" or ord(character) >= 32)


def _displayed_code(message: Mapping) -> list[tuple[bool, str]]:
    """What Slack's own account of the message, its rich text, displayed as code, in order:
    whether as a block, and the characters. A message's text marks code with backticks and
    so cannot say which backticks were displayed inside it; where the rich text names a
    block or a span, that is what Slack displayed. Anything not of that shape is passed over
    (the first _CODE_NODES elements are looked at), and the text then decides alone."""
    found: list[tuple[bool, str]] = []
    left: list[object] = [message.get("blocks")]
    for _ in range(_CODE_NODES):
        if not left:
            break
        node = left.pop()
        if isinstance(node, list):
            left.extend(reversed(node))
        elif isinstance(node, Mapping):
            inside = node.get("elements")
            if node.get("type") == "rich_text_preformatted" and isinstance(inside, list):
                parts = [part.get("text") for part in inside if isinstance(part, Mapping)]
                if len(parts) == len(inside) and all(isinstance(part, str) for part in parts):
                    found.append((True, _lines("".join(parts))))
            elif isinstance(node.get("style"), Mapping) and node["style"].get("code") is True:
                if isinstance(node.get("text"), str) and node.get("type") == "text":
                    found.append((False, _lines(node["text"])))
            else:
                left.append(inside)
    return found


def _known_code(text: str, code: list[tuple[bool, str]]) -> list[tuple[int, int, str, object]]:
    """Where `text` holds the `code` Slack's rich text names (_displayed_code), each after
    the one before: its start and end with its backticks, "block" or "code", and what it
    displayed. In the text it stands between backticks with its "&", "<" and ">" as
    entities, a block with or without a line feed after its opening and before its closing
    fence: at the first place it can start (three backticks, or one), the longest way there.
    From the first one that is not found so, the rest is left to the backticks of the text:
    the rich text no longer tells that text's code.

    Each place is looked at once, whatever the code (the tester's second attack, finding 6:
    each way was searched for to the text's end): the places a block or a span can start
    are found from where the last one ended on (_Next), and whether a way stands at one is
    told by the digests of _Hashes, in a step, however long the way."""
    places: list[tuple[int, int, str, object]] = []
    if not code:
        return places
    digests, at = _Hashes(text), 0
    starts = {True: _Next(text, "```", len(text)), False: _Next(text, "`", len(text))}
    for block, shown in code:
        sent = shown.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if block:
            ways = [f"```{above}{sent}{below}```" for above in ("\n", "") for below in ("\n", "")]
        else:
            ways = [f"`{sent}`"] if sent and "\n" not in sent else []
        ways = sorted(ways, key=len, reverse=True)  # the longest first, in their order
        known, found = [(way, digests.of(way)) for way in ways], None
        where = starts[block](at) if known else len(text)
        while found is None and where < len(text):
            found = next((way for way, value in known if digests.holds(way, where, value)), None)
            where = where if found else starts[block](where + 1)
        if found is None:
            break
        at = where + len(found)
        lines = _redact(shown).split("\n")
        places.append((where, at, "block", lines) if block else (where, at, "code", lines[0]))
    return places


class _Hashes:
    """A digest of each start of `text`, so that whether a stretch of it is a given string is
    told in one step, however long the string (_known_code): a polynomial hash modulo a
    Mersenne prime, and the characters compared only where the digests are equal."""

    _BASE, _MOD = 1_000_003, (1 << 61) - 1

    def __init__(self, text: str) -> None:
        self.text, self.sums, self.powers = text, [0], [1]
        for character in text:
            self.sums.append((self.sums[-1] * self._BASE + ord(character)) % self._MOD)
            self.powers.append(self.powers[-1] * self._BASE % self._MOD)

    def of(self, way: str) -> int:
        """The digest of `way`."""
        value = 0
        for character in way:
            value = (value * self._BASE + ord(character)) % self._MOD
        return value

    def holds(self, way: str, at: int, value: int) -> bool:
        """Whether `text` holds `way`, whose digest is `value`, at `at`."""
        end = at + len(way)
        if end > len(self.text):
            return False
        here = (self.sums[end] - self.sums[at] * self.powers[len(way)]) % self._MOD
        return here == value and self.text.startswith(way, at)


class _Next:
    """Where `what` next stands in `text` before `end`, at or after a place asked for, each
    place asked for after the one before: the text is read once, however often it is asked."""

    def __init__(self, text: str, what: str, end: int) -> None:
        self.text, self.what, self.end = text, what, end
        self.since, self.found = end + 1, end  # nothing read yet

    def __call__(self, at: int) -> int:
        """The place, or `end` when there is none."""
        if not self.since <= at <= self.found:
            found = self.text.find(self.what, at, self.end)
            self.since, self.found = at, self.end if found < 0 else found
        return self.found


def _tokens(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The start and end of each token of `text` between `start` and `end`, in order: a code
    block (three backticks, anything, three backticks), code within a line (a backtick, no
    backtick and no line feed, a backtick), a reference (`<`, no ">" and no line feed, `>`):
    as the pattern ```[\\s\\S]*?```|`[^`\\n]*`|<[^>\\n]+> finds them from left to right, with
    each character read a bounded number of times, where that pattern read the rest of a
    line again from each "<" with no ">" after it."""
    found: list[tuple[int, int]] = []
    fence, tick = _Next(text, "```", end), _Next(text, "`", end)
    feed, close = _Next(text, "\n", end), _Next(text, ">", end)
    at = start
    while (candidate := _TOKEN_START.search(text, at, end)) is not None:
        at = candidate.start()
        if text[at] == "`":
            closer = fence(at + 3) if text.startswith("```", at, end) else end
            if closer < end:
                found.append((at, closer + 3))
            elif (after := tick(at + 1)) < min(end, feed(at + 1)):
                found.append((at, after + 1))
            else:
                at += 1
                continue
        elif at + 1 < (after := close(at + 1)) < min(end, feed(at + 1)):
            found.append((at, after + 1))
        else:
            at += 1
            continue
        at = found[-1][1]
    return found


def _block_lines(token: str, quoted: bool) -> list[str]:
    """The lines a code block of Slack's own displayed, the white space at their ends
    included, as the backticks of the message's text bound it: from the first three to the
    next three. What stands on the opening fence's line after it, or on the closing fence's
    before it, is a line of the block unless it is blank; and in a quotation (`quoted`:
    only a quotation's marks stand before the block on its line) the mark that starts each
    line is the quotation's."""
    lines = _shown(token[3:-3]).split("\n")
    if quoted:
        lines = [_QUOTE_MARK.sub("", line, count=1) for line in lines]
    for edge in (0, -1):
        if lines and not lines[edge].strip(" \t") and (len(lines) > 1 or not lines[edge]):
            del lines[edge]
    return lines


def _fence(lines: list[str]) -> str:
    """A code block of Slack's own as a Markdown fence that the parser and the splitter both
    read as one block of code: the opening and the closing fence on lines of their own
    (_apart) and at the margin, longer than any run of backticks inside, and between them the
    lines Slack displayed. Whether both do read it so is asked of the finished note
    (_not_one_fence): a fence of tildes open about it may not let them."""
    fence = "`" * max(3, 1 + max(map(len, re.findall("`+", "\n".join(lines))), default=0))
    return "\n".join([fence, *lines, fence])


def _not_one_fence(note: str, wrote: list[tuple[int, str]]) -> set[int]:
    """Which of the fences written for a message's code blocks (`wrote`: each block's place
    among them and its fence, in order) `note` does not hold as one block of code for both
    readers: a fence to the Markdown parser and one passage of code to the splitter, on the
    same lines. A line of only backticks is such a fence's in the note, or part of none."""
    if not wrote:
        return set()
    lines = note.split("\n")
    read = {
        tuple(token.map) for token in _PARSER.parse(note) if token.type == "fence" and token.map
    }
    read &= {
        (span.start_line - 1, span.end_line) for span in chunk("", note) if span.kind == "code"
    }
    missing, at = set(), 0
    for place, fence in wrote:
        rows = fence.split("\n")
        ends = (
            n + len(rows)
            for n in range(at, len(lines))
            if lines[n] == rows[0] and lines[n : n + len(rows)] == rows
        )
        end = next(ends, None)
        if end is None or (end - len(rows), end) not in read:
            missing.add(place)
        at = end or at
    return missing


def _render(
    text: str,
    names: Mapping[str, str],
    displayed: bool = False,
    bars: bool = True,
    code: list[tuple[bool, str]] | None = None,
    plain: Collection[int] = (),
    wrote: list[tuple[int, str]] | None = None,
    title: bool = False,
) -> str:
    """`text` as the note holds it: its prose by _prose and a name or a link's label by
    _literal; a code block of Slack's own as a fence (_fence); and code within a line between
    backticks, more of them than any run it holds, the same number for every such code of
    the message, and the only bare backticks of the note's prose, so they pair as Slack
    paired them. What is code is what the rich text says (`code`: _known_code), else what the
    backticks of the text bound. Without `bars`, code within a line that holds a bar is
    written by _literal, backticks and all: a table's row is cut at its bars before its code
    is read. `displayed`: the whole of it as Slack displayed it (_shown, and code within a
    line between the backticks it was sent with), with the same lines. The code blocks whose
    place among the message's is in `plain` are written as plain characters, backticks and
    all, on the same lines; the fences of the others are given in `wrote`. `title`: as the
    note's title is taken from it (_title), where nothing is code and nothing a link: a code
    block's lines as plain characters, on lines of their own and with no fence, code within a
    line as plain characters, backticks and all, and a link as its words."""
    literal = _shown if displayed else _literal

    def prose(text: str, then: str = " ") -> str:  # `then`: what the note writes after it
        last = next((piece[-1] for piece in reversed(pieces) if piece), " ")  # and before
        if displayed:
            return _shown(text)
        return _prose(text, clear, last.strip("\n") or " ", then, lead)

    tokens: list[tuple[int, int, str, object]] = []
    # A block's line: where it starts, read once (`read`: up to where), and where the marks
    # of a quotation that start it end (`quotes`), read once a line.
    offset, line, read, quotes = 0, 0, 0, None
    for known in [*_known_code(text, code or []), (len(text), len(text), "", None)]:
        for start, end in _tokens(text, offset, known[0]):
            token = text[start:end]
            if token.startswith("```"):
                began, read = text.rfind("\n", read, start) + 1, start
                if began or quotes is None:
                    line = max(line, began)
                    marks = _QUOTED.match(text, line)
                    quotes = marks.end() if marks else -1
                found: tuple[str, object] = ("block", _block_lines(token, quotes == start))
            elif token.startswith("`") and token != "``":
                found = ("code", _shown(token[1:-1]))
            else:  # a reference, or two backticks with no code between them
                found = ("", token)
            tokens.append((start, end, *found))
        tokens.append(known)
        offset = known[1]
    held = [what for _, _, kind, what in tokens if kind == "code" and isinstance(what, str)]
    ticks = "`" * (1 + max(map(len, re.findall("`+", "\n".join(held))), default=0))
    pieces: list[str] = []
    # fresh: nothing is written on the line; clear: only what may stand before a fence (_prose)
    # gap: the text before a reference that shows nothing, which is as if it were not there
    # lead: what of the marks that start the line is yet to be read (_lead)
    offset, below, fresh, clear, place, gap, lead = 0, False, True, True, -1, "", ""
    for start, end, kind, what in tokens[:-1]:
        reference = None
        if not kind and text[start] != "`":
            reference = _reference(text[start + 1 : end - 1], names, literal, title)
            if not reference:
                gap, offset = gap + text[offset:start], end
                continue
        before = gap + text[offset:start]
        then = "\n" if isinstance(what, list) else reference[:1] if reference else "`"
        pieces.append(prose(_apart(before, below, kind == "block", True, fresh), then))
        gap, fresh = "", pieces[-1].endswith("\n") if pieces[-1] else fresh
        clear, lead = _clear(clear, pieces[-1]), _lead(lead, pieces[-1])
        if isinstance(what, list):
            place += 1
            if title:
                fence = "\n".join(map(_plain, what))
            elif place in plain:
                fence = "\n".join(["```", *what, "```"])
                fence = fence if displayed else "\n".join(map(_plain, fence.split("\n")))
            else:
                fence = _fence(what)
                if wrote is not None:
                    wrote.append((place, fence))
            pieces.append(fence if fresh else "\n" + fence)
        elif kind == "code" and isinstance(what, str):
            if displayed:
                pieces.append(_shown(text[start:end]))
            elif title:  # its code alone is what an address is removed from, as below
                pieces.append(_plain(f"`{what}`"))
            elif "|" in what and not bars:
                pieces.append(literal(text[start:end]))
            else:  # a space each side where Markdown would take one, or read a longer run
                pad = " " if what.strip(" ") and (what[0] in "` " or what[-1] in "` ") else ""
                pieces.append(f"{ticks}{pad}{what}{pad}{ticks}")
        elif reference is not None:
            pieces.append(reference)
        else:  # two backticks with no code between them
            pieces.append(prose(text[start:end]))
        offset, below, fresh = end, kind == "block", fresh and not pieces[-1]
        clear, lead = _clear(clear, pieces[-1]), _lead(lead, pieces[-1])
    pieces.append(prose(_apart(gap + text[offset:], below, False, False, fresh)))
    return "".join(pieces)


def _reference(
    inner: str, names: Mapping[str, str], literal: Callable[[str], str], title: bool
) -> str:
    """What the note writes for a reference, `<inner>` (_render): a mention as "@" and a
    name, a conversation as "#" and its name, one of Slack's special forms as Slack displays
    it (_special), a link as `[its words](its address)` (its words alone in a title), a
    reference to no address as its words, and else "[link omitted]"."""
    target, separator, label = inner.partition("|")
    if target.startswith("@"):
        return "@" + _name(names.get(target[1:], target[1:]), literal)
    if target.startswith("#"):
        return "#" + _name(names.get(target[1:], label or target[1:]), literal)
    if (special := _special(target, label)) is not None:
        return _name(special, literal)
    url = _link_target(target)
    shown = literal(label if separator else target)
    if url and title:
        return shown
    if url:
        return f"[{shown}]({url})"
    if separator:
        return shown
    return _plain("[link omitted]") if title else "[link omitted]"


def _clear(clear: bool, written: str) -> bool:
    """Whether a line holds only what may stand before a fence (_MARKS), or nothing, once
    `written` is written on it; `clear`: whether it did before."""
    _, feed, last = written.rpartition("\n")
    return (clear or bool(feed)) and not last.strip(_MARKS)


def _shows(title: str) -> str:
    """What a page's heading of `title` displays: its characters, emphasis aside."""
    heading = _PARSER.parse(f"# {title}")[1]
    return "".join(child.content for child in heading.children or [])


def displayed_title(title: str) -> str:
    """A note's title as a reply in Slack names it: what the page's heading of it displays,
    which for a filed message is what Slack displayed (the third tester, finding 7), never
    the escapes and entities the title is stored with."""
    return " ".join(_shows(one_line(title)).split("\n")).strip()


def _unclosed(title: str) -> str:
    """`title` with a backslash before a run of "#" that would close the page's heading of it,
    where the run would be dropped (the run is all of it, or a blank stands before it)."""
    return _CLOSING.sub(lambda run: "\\" + run.group(), title, count=1)


def _within(atoms: list[str]) -> int:
    """How many of `atoms`, from the first, fit in TITLE_MAX characters."""
    size, end = 0, 0
    while end < len(atoms) and size + len(atoms[end]) <= TITLE_MAX:
        size, end = size + len(atoms[end]), end + 1
    return end


def _title(text: str, names: Mapping[str, str], code: list[tuple[bool, str]]) -> str:
    """The note's title: the first line the message displays, its first line of text or the
    first line of a code block it opens with, never a line the note writes for itself (a
    fence), nor one of a quotation's marks alone. Written as prose is (_render, `title`), so
    the page's heading of it reads as the characters Slack displayed and holds no code and
    no link; cut to TITLE_MAX characters where the heading then displays the start of what
    the whole line displays, so no escape is cut in two. A line no cut keeps so (all of it
    bold, say) is written as the plain characters it displays, and a message that displays
    no line as the plain characters of its first."""
    lines = _render(text, names, code=code, title=True).split("\n")
    line = next((line.strip() for line in lines if line.strip(" \t>")), "")
    if not line:
        whole = _shown(text.split("\n", 1)[0].strip())
    else:
        whole, atoms = _shows(_unclosed(line)), _ATOM.findall(line)
        end = _within(atoms)
        while end:  # at most TITLE_MAX tries, each a parse of a title's length
            cut = _unclosed("".join(atoms[:end]).rstrip())
            if len(cut) <= TITLE_MAX and cut and whole.startswith(_shows(cut)):
                return cut
            end -= 1
    plain = _ATOM.findall(_plain(whole.strip()))
    return "".join(plain[: _within(plain)]).rstrip()


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ValueError("Invalid Slack timestamp")
    try:
        return datetime.fromtimestamp(int(value.partition(".")[0]), UTC)
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError("Invalid Slack timestamp") from error


@dataclass(frozen=True)
class Draft:
    title: str
    text: str
    source_ref: str
    shown: str = ""  # the title as Slack displayed it (displayed_title), for the reply


class NotText(ValueError):
    """The selected message has no text to file."""


def note_from_message(
    message: dict,
    *,
    team_id: str,
    team_domain: str,
    conversation_id: str,
    author: str | None,
    names: Mapping[str, str],
) -> Draft:
    """Convert text after the caller independently verified the author's affiliation.

    ``team_id`` remains part of the shared interface; message metadata cannot prove
    author membership. Display names and message content are untrusted data.
    """
    raw = message.get("text")
    if not isinstance(raw, str) or not raw.strip():
        raise NotText("The selected message has no text")
    if not isinstance(team_domain, str) or not _DOMAIN.fullmatch(team_domain):
        raise ValueError("Invalid Slack workspace domain")
    if not isinstance(conversation_id, str) or not re.fullmatch(r"[CDG][A-Z0-9]+", conversation_id):
        raise ValueError("Invalid Slack conversation ID")
    timestamp = message.get("ts")
    when = _timestamp(timestamp)
    source = (
        f"https://{team_domain.lower()}.slack.com/archives/"
        f"{conversation_id}/p{timestamp.replace('.', '')}"
    )
    if "thread_ts" in message:
        root = message["thread_ts"]
        _timestamp(root)
        if root != timestamp:
            source += "?" + urlencode({"thread_ts": root, "cid": conversation_id})
    raw, code = _lines(raw), _displayed_code(message)
    who = _literal(" ".join((author or "Unknown author").split()))
    heading = f"## {who} · {when:%Y-%m-%d %H:%M:%S} UTC\n\n"
    link = f"\n\n[Source in Slack]({source})"
    # As displayed, the white space that ends the message's last line is kept: a line both
    # readers read as code holds it (_code_as_shown), and prose never did.
    ended = raw.strip() + raw[len(raw.rstrip()) :].split("\n", 1)[0]
    plain: Collection[int] = ()
    for every in (False, False, True, True):
        # Each code block must be one fence for both readers (_not_one_fence): the message as
        # written is asked first, and the first block that is none is written again as plain
        # characters; should one fail then, they all are. The finished note is asked too.
        wrote: list[tuple[int, str]] = []
        body = _render(raw.strip(), names, code=code, plain=plain, wrote=wrote).strip()
        if not body:
            raise NotText("The selected message has no text")
        rows = body.split("\n")
        if any(
            "|" in above and "-" in row and _SECOND_ROW.fullmatch(row)
            for above, row in pairwise(rows)
        ):  # it may hold a table
            body = _render(raw.strip(), names, bars=False, code=code, plain=plain).strip()
        missing = _not_one_fence(heading + body + link, wrote)
        if not missing:
            shown = _render(ended, names, displayed=True, code=code, plain=plain)
            lines = shown.lstrip().split("\n")
            while not lines[-1].strip():
                del lines[-1]
            text = _code_as_shown(heading + body + link, heading + "\n".join(lines) + link)
            if not _not_one_fence(text, wrote):
                break
        plain = range(len(raw)) if plain or every or not missing else {min(missing)}
    title = _title(raw.strip(), names, code)
    return Draft(title, text, source, displayed_title(title))
