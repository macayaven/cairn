"""Turn selected Slack message text into a note, without provider I/O."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urlencode, urlsplit

_EMAIL = re.compile(r"""(?:"[^"\n]+"|[\w.!#$%&'*+/=?^`{|}~-]+)@(?:\[[^\]\n]+\]|[\w.-]+)""")
_TOKENS = re.compile(r"```[\s\S]*?```|`[^`\n]*`|<[^>\n]+>")
_DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)
_TIMESTAMP = re.compile(r"[0-9]{1,12}\.[0-9]{6}")
_ATX_START = re.compile(r"(?m)^([ ]{0,3})(#{1,6})(?=[ \t])")
_ATX_AFTER_NL = re.compile(r"(\n[ ]{0,3})(#{1,6})(?=[ \t])")


def _redact(text: str) -> str:
    return _EMAIL.sub("[email removed]", text)


def _decode(text: str) -> str:
    # Slack documents only these three escapes, not general HTML entities.
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _literal(text: str) -> str:
    text = _redact(_decode(text))
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]()!#~])", r"\\\1", text)


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


def _escape_headings(text: str, *, line_start: bool) -> str:
    pattern = _ATX_START if line_start else _ATX_AFTER_NL
    return pattern.sub(r"\1\\\2", text)


def _render(text: str, names: Mapping[str, str]) -> str:
    pieces: list[str] = []
    offset = 0
    for match in _TOKENS.finditer(text):
        line_start = offset == 0 or text[offset - 1] == "\n"
        pieces.append(_escape_headings(_prose(text[offset : match.start()]), line_start=line_start))
        token = match.group()
        if token.startswith("`"):
            pieces.append(_redact(_decode(token)))
        else:
            target, separator, label = token[1:-1].partition("|")
            if target.startswith("@"):
                pieces.append("@" + _literal(names.get(target[1:], target[1:])))
            elif target.startswith("#"):
                shown_tag = _literal(names.get(target[1:], label or target[1:]))
                line_prefix = "".join(pieces).rsplit("\n", 1)[-1]
                if (
                    shown_tag[:1] in {" ", "\t"}
                    and len(line_prefix) <= 3
                    and line_prefix == " " * len(line_prefix)
                ):
                    pieces.append(r"\#" + shown_tag)
                else:
                    pieces.append("#" + shown_tag)
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
    line_start = offset == 0 or text[offset - 1] == "\n"
    pieces.append(_escape_headings(_prose(text[offset:]), line_start=line_start))
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
    raw = "".join(character for character in raw if character in "\n\t" or ord(character) >= 32)
    body = _render(raw.strip(), names).strip()
    if not body:
        raise NotText("The selected message has no text")
    title = next(line.strip() for line in body.splitlines() if line.strip())
    who = _literal(" ".join((author or "Unknown author").split()))
    heading = f"## {who} · {when:%Y-%m-%d %H:%M:%S} UTC"
    return Draft(title, f"{heading}\n\n{body}\n\n[Source in Slack]({source})", source)
