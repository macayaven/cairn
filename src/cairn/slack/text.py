"""Turn selected Slack message text into a note, without provider I/O.

The note holds one heading, its first line: who wrote the message and when, as the caller
verified. Where a heading begins is asked of the note as written, never worked out from the
message: of the passage splitter (cairn.spans.chunk), whose heading paths say whose words a
passage is, and of the Markdown parser as the web view reads. Every line either finds a
heading on is written as text, and no other line where a line at a reading can tell
(_one_heading): the note says whose words it holds, so a backslash no reader asked for is a
false quotation. For the same reason a line the parser reads as code in the finished note,
where every character is shown as it is, holds what Slack displayed and none of the marks
that write prose (_code_as_shown). The splitter's line boundaries (str.splitlines: more than
a line feed) are each written as a line feed first, a CR LF as one.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urlencode, urlsplit

from markdown_it import MarkdownIt

from cairn.spans import chunk

_EMAIL = re.compile(r"""(?:"[^"\n]+"|[\w.!#$%&'*+/=?^`{|}~-]+)@(?:\[[^\]\n]+\]|[\w.-]+)""")
_TOKENS = re.compile(r"```[\s\S]*?```|`[^`\n]*`|<[^>\n]+>")
_DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)
_TIMESTAMP = re.compile(r"[0-9]{1,12}\.[0-9]{6}")
_PARSER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
# A line that can underline the text above it into a heading, inside a quote or not.
_UNDERLINE = re.compile(r"[ \t>]*(=+|-+)[ \t]*")
_PASSES = 8  # see _one_heading


def _redact(text: str) -> str:
    return _EMAIL.sub("[email removed]", text)


def _decode(text: str) -> str:
    # Slack documents only these three escapes, not general HTML entities.
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _shown(text: str) -> str:
    """Text as Slack displayed it, an email address removed: what _literal and _prose start
    from, and what a line the note reads as code holds (_code_as_shown)."""
    return _redact(_decode(text))


def _literal(text: str) -> str:
    text = _redact(_decode(text))
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]()!#~])", r"\\\1", text)


def _name(name: str, literal: Callable[[str], str] = _literal) -> str:
    """A name as Slack gives it, on one line: it cannot start a line of the note."""
    return literal(" ".join(name.splitlines()))


def _prose(text: str) -> str:
    text = _redact(_decode(text))
    text = text.replace("&", "&amp;").replace("<", "&lt;")
    text = re.sub(r"([\\\[\]()!])", r"\\\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"**\1**", text)
    return re.sub(r"(?<!~)~([^~\n]+)~(?!~)", r"~~\1~~", text)


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


def _headings(text: str) -> dict[int, str]:
    """Each line after the first that makes a heading in `text`, with the character that makes
    it one: a line's first "#", or the "=" or "-" of a line that underlines a heading. A line
    written as text joins the paragraph above it, and a line of "=" or "-" below would
    underline that paragraph next; so once a heading is found, every such line below it that
    is not in a code fence is given too, and a run of them costs one more reading, not one
    each. (Indented code is not spared: the paragraph a line joins can take it in.) Not every
    line so given would read as a heading: _one_heading says when they are all written."""
    lines = text.split("\n")
    found = {span.start_line - 1: "#" for span in chunk("", text) if span.kind == "heading"}
    code: set[int] = set()
    for token in _PARSER.parse(text):
        if token.type == "heading_open" and token.map:
            underlined = token.markup in ("=", "-")
            found[token.map[1] - 1 if underlined else token.map[0]] = token.markup[0]
        elif token.type == "fence" and token.map:
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
    heading (_one_heading), and each line the Markdown parser reads as code in that note,
    fenced or indented, as `shown` has it: the message as Slack displayed it, line for line.
    Slack has no indented code and Markdown has, so the rules that write prose (a backslash
    before a bracket, a doubled asterisk) wrote on lines that the note shows as they are.

    Which lines are code is asked of the finished note, so it is made twice: of `text`, and
    then of `text` with the lines that were code as `shown` has them. The second is the note
    only if the parser reads exactly those lines of it as code. What is displayed is not
    escaped, and may be read as Markdown nowhere else: a link's label of tildes is "~~~" as
    displayed and would end the fence it stands in. Then, or should the second making refuse
    or the two texts not have the same lines, the note is the first, as prose throughout.
    One making when no line that is code differs between the two texts; so the readings of
    _one_heading, or twice them."""
    first = _one_heading(text)
    written, displayed = text.split("\n"), shown.split("\n")
    if len(written) != len(displayed):
        return first
    differs = {at for at, line in enumerate(written) if line != displayed[at]}
    code = differs and differs & _code(first)
    if not code:
        return first
    try:
        second = _one_heading(
            "\n".join(displayed[at] if at in code else line for at, line in enumerate(written))
        )
    except ValueError:
        return first
    return second if differs & _code(second) == code else first


def _render(
    text: str,
    names: Mapping[str, str],
    prose: Callable[[str], str] = _prose,
    literal: Callable[[str], str] = _literal,
) -> str:
    """`text` as the note holds it: its prose by `prose`, a name or a link's label by
    `literal`, a code span or block of Slack's own as it was displayed. With _shown for both,
    the whole of it as Slack displayed it, and the same lines."""
    pieces: list[str] = []
    offset = 0
    for match in _TOKENS.finditer(text):
        pieces.append(prose(text[offset : match.start()]))
        token = match.group()
        if token.startswith("`"):
            pieces.append(_shown(token))
        else:
            target, separator, label = token[1:-1].partition("|")
            if target.startswith("@"):
                pieces.append("@" + _name(names.get(target[1:], target[1:]), literal))
            elif target.startswith("#"):
                pieces.append("#" + _name(names.get(target[1:], label or target[1:]), literal))
            else:
                url = _link_target(target)
                shown = literal(label if separator else target)
                if url:
                    pieces.append(f"[{shown}]({url})")
                elif separator:
                    pieces.append(shown)
                else:
                    pieces.append("[link omitted]")
        offset = match.end()
    pieces.append(prose(text[offset:]))
    return "".join(pieces)


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
    raw = "\n".join(raw.splitlines())  # each boundary the passage splitter breaks at: a line feed
    raw = "".join(character for character in raw if character in "\n\t" or ord(character) >= 32)
    body = _render(raw.strip(), names).strip()
    if not body:
        raise NotText("The selected message has no text")
    who = _literal(" ".join((author or "Unknown author").split()))
    heading = f"## {who} · {when:%Y-%m-%d %H:%M:%S} UTC\n\n"
    link = f"\n\n[Source in Slack]({source})"
    shown = _render(raw.strip(), names, _shown, _shown).strip()
    text = _code_as_shown(heading + body + link, heading + shown + link)
    title = next(line.strip() for line in text.split("\n")[1:] if line.strip())
    return Draft(title, text, source)
