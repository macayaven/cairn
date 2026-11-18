"""Pure Slack message builders. Callers own authorization and reply visibility."""

from urllib.parse import quote, urlsplit


def escape(text: str) -> str:
    """Escape Slack's three text control characters (not HTML in general)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text(value: object, limit: int) -> str:
    """Bound the rendered length without cutting an escape sequence."""
    raw = str(value)
    rendered = escape(raw[:limit])
    if len(raw) <= limit and len(rendered) <= limit:
        return rendered
    pieces: list[str] = []
    size = 0
    for char in raw:
        encoded = escape(char)
        if size + len(encoded) > limit - 1:
            break
        pieces.append(encoded)
        size += len(encoded)
    return "".join(pieces) + "…"


def _url(url: str) -> str | None:
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        return None
    try:
        parts = urlsplit(url)
        if parts.port == 0:
            return None
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
        return None
    # Preserve URL structure and existing escapes; encode Slack's link delimiters.
    encoded = escape(quote(url, safe=":/?#[]@!$&'()*+,;=%"))
    return encoded if len(encoded) <= 800 else None


def _link(title: str, url: str) -> str:
    # A literal pipe separates Slack's URL from its label, even inside untrusted titles.
    label = _text(title or "Untitled", 450).replace("|", "\N{FULLWIDTH VERTICAL LINE}")
    target = _url(url)
    return f"<{target}|{label}>" if target else f"{label} (link unavailable)"


def _message(fallback: str, sections: list[str]) -> dict:
    return {
        "text": fallback,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": text, "verbatim": True}}
            for text in sections
        ],
    }


def _one(raw: str) -> dict:
    text = _text(raw, 3000)
    return _message(text, [text])


def found(hits: list[dict], *, query: str, base_url: str) -> dict:
    """Render only the first five authorized recall hits, with claim-position links."""
    if not hits:
        return nothing_found(query)
    heading = "Found claims for: " + _text(query, 300)
    sections = [heading]
    fallback = [heading]
    for hit in hits[:5]:
        project = str(hit["project"])
        page = str(hit["page"])
        claim_id = str(hit["claim_id"])
        url = (
            f"{base_url.rstrip('/')}/b/{quote(project, safe='')}/p/{quote(page, safe='')}"
            f"#{quote(claim_id, safe='')}"
        )
        title = str(hit["title"])
        metadata = f"Project: {_text(project, 350)} · Version: {_text(hit['version'], 100)}"
        evidence = _text(hit["text"], 1100) or "(Empty claim)"
        sections.append(f"{_link(title, url)}\n{metadata}\n{evidence}")
        fallback.append(
            f"{_text(title or 'Untitled', 150)}\n"
            f"Project: {_text(project, 100)} · Version: {_text(hit['version'], 50)}\n"
            f"{_text(hit['text'], 300) or '(Empty claim)'}"
        )
    return _message("\n".join(fallback), sections)


def nothing_found(query: str) -> dict:
    return _one(f"Nothing found for: {query}" if query else "Nothing found.")


def filed(*, title: str, page_url: str, project: str, already: bool = False) -> dict:
    status = "Already filed" if already else "Filed"
    fallback = f"{status}: {_text(title or 'Untitled', 450)}\nProject: {_text(project, 350)}"
    return _message(
        fallback, [f"{status}: {_link(title, page_url)}\nProject: {_text(project, 350)}"]
    )


def refused(reason: str) -> dict:
    return _one(f"Cannot do that: {reason or 'No reason was provided.'}")


def link_prompt(url: str, command: str = "/cairn") -> dict:
    """The two steps of a link, for a Slack user who is not linked: the address to open
    signed in, which shows a code, and the code typed back here with `<command> link`."""
    name = _text(command or "/cairn", 200)
    explanation = (
        "Link your Slack user to your cairn account in two steps, both within 10 minutes."
        " Open the address below, signed in to cairn. It shows you a code."
        f" Then type `{name} link` and the code here."
        " The address is yours alone and works once: send it to no one."
    )
    fallback = f"{explanation}\n{_url(url) or 'Link unavailable. Request a new link.'}"
    return _message(fallback, [f"{explanation}\n{_link('Open cairn to get your code', url)}"])


def linked(person: str) -> dict:
    return _one(f"Linked to {person or 'your cairn account'}.")


def project_is(project: str | None, *, writable: list[str]) -> dict:
    selection = f"Slack project: {_text(project, 500)}" if project else "No project chosen."
    if writable:
        choices = ", ".join(_text(name, 150) or "(unnamed)" for name in writable[:10])
        suffix = " (first 10 shown)" if len(writable) > 10 else ""
        text = f"{selection}\nWritable projects: {choices}{suffix}"
    else:
        text = f"{selection}\nNo writable projects available."
    return _message(text, [text])


def help_text(command: str) -> dict:
    name = _text(command or "/cairn", 200)
    text = "\n".join(
        [
            "cairn help",
            f"Link your cairn account with `{name} link`.",
            f"Unlink your cairn account with `{name} unlink`.",
            f"Find claims with `{name} find <words>`.",
            f"File a note with `{name} note <text>`.",
            f"Show or choose your Slack project with `{name} project [name]`.",
            f"Show this help with `{name} help`.",
            "In a direct message, enter search words, `note: <text>`, or `help`.",
            'Use the message shortcut "File in cairn" to file a selected message.',
        ]
    )
    # Escape only the static placeholders; the custom command is already escaped.
    text = text.replace("<words>", "&lt;words&gt;").replace("<text>", "&lt;text&gt;")
    return _message(text, [text])
