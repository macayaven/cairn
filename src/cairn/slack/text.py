"""Turn selected Slack message text into a note, without provider I/O.

The note holds one heading, its first line: who wrote the message and when, as the caller
verified. Where a heading begins is asked of the note as written, never worked out from the
message: of the passage splitter (cairn.spans.chunk), whose heading paths say whose words a
passage is, and of the Markdown parser as the web view reads. Every line either finds a
heading on is written as text (_one_heading). The splitter's line boundaries (str.splitlines:
more than a line feed) are each written as a line feed first, a CR LF as one.
"""

import re
from collections.abc import Mapping
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


def _literal(text: str) -> str:
    text = _redact(_decode(text))
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]()!#~])", r"\\\1", text)


def _name(name: str) -> str:
    """A name as Slack gives it, on one line: it cannot start a line of the note."""
    return _literal(" ".join(name.splitlines()))


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
    each. (Indented code is not spared: the paragraph a line joins can take it in.)"""
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


def _one_heading(text: str) -> str:
    """`text` with a backslash before what makes any line after its first a heading, so the
    words stay and read as text. It is read until no heading is found: twice as a rule, once
    more for each list item that takes in a line indented past it once the line above is
    text. ValueError rather than a note with a second heading, should _PASSES readings not be
    enough."""
    text = "\n".join(text.splitlines())
    for _ in range(_PASSES):
        found = _headings(text)
        if not found:
            return text
        lines = text.split("\n")
        for index, mark in found.items():
            lines[index] = lines[index].replace(mark, "\\" + mark, 1)
        text = "\n".join(lines)
    raise ValueError("The message's headings could not be written as text")


def _render(text: str, names: Mapping[str, str]) -> str:
    pieces: list[str] = []
    offset = 0
    for match in _TOKENS.finditer(text):
        pieces.append(_prose(text[offset : match.start()]))
        token = match.group()
        if token.startswith("`"):
            pieces.append(_redact(_decode(token)))
        else:
            target, separator, label = token[1:-1].partition("|")
            if target.startswith("@"):
                pieces.append("@" + _name(names.get(target[1:], target[1:])))
            elif target.startswith("#"):
                pieces.append("#" + _name(names.get(target[1:], label or target[1:])))
            else:
                url = _link_target(target)
                shown = _literal(label if separator else target)
                if url:
                    pieces.append(f"[{shown}]({url})")
                elif separator:
                    pieces.append(shown)
                else:
                    pieces.append("[link omitted]")
        offset = match.end()
    pieces.append(_prose(text[offset:]))
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
    heading = f"## {who} · {when:%Y-%m-%d %H:%M:%S} UTC"
    text = _one_heading(f"{heading}\n\n{body}\n\n[Source in Slack]({source})")
    title = next(line.strip() for line in text.split("\n")[1:] if line.strip())
    return Draft(title, text, source)
