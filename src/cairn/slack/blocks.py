"""Pure Slack message builders. Callers own authorization and reply visibility. A message
that names the product names it as the caller says (`product`; PRODUCT when not said)."""

from urllib.parse import quote, urlsplit

from cairn.topics import one_line

PRODUCT = "cairn"  # the name a message gives the product when the caller names none
SHOWN = 5  # the claims a search answer shows; the rest are counted and linked
OUTSIDE = "outside text"  # a hit whose note a source wrote: the search page's own mark


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


def _title(title: str, limit: int) -> str:
    """A page's or note's title on one line (cairn.topics.one_line, as the page body writes
    it), so a title with a line break never adds a line to the message; bounded and escaped."""
    return _text(one_line(title or "Untitled"), limit)


def _link(title: str, url: str) -> str:
    # A literal pipe separates Slack's URL from its label, even inside untrusted titles.
    label = _title(title, 450).replace("|", "\N{FULLWIDTH VERTICAL LINE}")
    target = _url(url)
    return f"<{target}|{label}>" if target else f"{label} (link unavailable)"


def _message(fallback: str, sections: list[str], plain: str | None = None) -> dict:
    """A message of mrkdwn sections, the reply's own words, and then `plain`, words of a
    message or of a name, in a section of its own as plain text: Slack's means for text it
    does not format ("Disabling formatting", on its page on message text), so nothing of it
    is bold, a mention or a link. It is escaped as every text object is. The fallback text
    then holds those words too, and the message's own "mrkdwn" field says it is not to be
    read as mrkdwn ("Determines whether the text field is rendered according to mrkdwn")."""
    made = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text, "verbatim": True}}
        for text in sections
    ]
    if plain is None:
        return {"text": fallback, "blocks": made}
    made.append({"type": "section", "text": {"type": "plain_text", "text": plain}})
    return {"text": fallback, "blocks": made, "mrkdwn": False}


def _one(raw: str) -> dict:
    text = _text(raw, 3000)
    return _message(text, [text])


def found(hits: list[dict], *, query: str, base_url: str, asked: int | None = None) -> dict:
    """Render only the first SHOWN authorized recall hits, with claim-position links, each
    hit of outside text marked as the search page marks it. With more hits than that, say
    how many there are ("5 of 12", "or more" when the search found as many as it `asked`
    for) and give the search page's address for the rest; the rest are only counted."""
    if not hits:
        return nothing_found(query)
    heading = "Found claims for: " + _text(query, 300)
    sections = [heading]
    fallback = [heading]
    for hit in hits[:SHOWN]:
        project = str(hit["project"])
        page = str(hit["page"])
        claim_id = str(hit["claim_id"])
        url = (
            f"{base_url.rstrip('/')}/b/{quote(project, safe='')}/p/{quote(page, safe='')}"
            f"#{quote(claim_id, safe='')}"
        )
        title = str(hit["title"])
        metadata = f"Project: {_text(project, 350)} · Version: {_text(hit['version'], 100)}"
        if hit.get("outside"):
            metadata += f" · {OUTSIDE}"
        evidence = _text(hit["text"], 1100) or "(Empty claim)"
        sections.append(f"{_link(title, url)}\n{metadata}\n{evidence}")
        fallback.append(
            f"{_title(title, 150)}\n"
            f"Project: {_text(project, 100)} · Version: {_text(hit['version'], 50)}"
            f"{f' · {OUTSIDE}' if hit.get('outside') else ''}\n"
            f"{_text(hit['text'], 300) or '(Empty claim)'}"
        )
    if len(hits) > SHOWN:
        more = " or more" if asked is not None and len(hits) >= asked else ""
        count = f"{SHOWN} of {len(hits)}{more} claims shown."
        rest = f"{base_url.rstrip('/')}/search?q={quote(query, safe='')}"
        sections.append(f"{count} {_link('See them all on the search page', rest)}")
        fallback.append(f"{count} The search page has them all: {_url(rest) or 'unavailable'}")
    return _message("\n".join(fallback), sections)


def nothing_found(query: str) -> dict:
    return _one(f"Nothing found for: {query}" if query else "Nothing found.")


def filed(
    *,
    title: str,
    page_url: str | None,
    project: str,
    already: bool = False,
    waiting: bool = False,
    project_url: str | None = None,
) -> dict:
    """A note filed, or filed already, linking its page. A note without a page links its
    project (`project_url`) and says whether it is still waiting. The note's title, a
    message's words, is the last section, in plain text (_message)."""
    named = _title(title, 450)
    if waiting and not already:
        status = f"Note stored in {_text(project, 350)}. Its page will appear shortly."
        shown = (
            f"Note stored in {_link(project, project_url) if project_url else _text(project, 350)}"
        )
        return _message(f"{status}\n{named}", [f"{shown}. Its page will appear shortly."], named)
    if page_url is None:
        where = _link(project, project_url) if project_url else _text(project, 350)
        said = "Its page will appear shortly." if waiting else "No page holds it."
        status = "Already filed" if already else "Note stored"
        return _message(
            f"{status} in {_text(project, 350)}: {named}. {said}",
            [f"{status} in {where}. {said}"],
            named,
        )
    status = "Already filed" if already else "Filed"
    fallback = f"{status}: {named}\nProject: {_text(project, 350)}"
    return _message(
        fallback,
        [f"{status}. {_link('Open its page', page_url)}\nProject: {_text(project, 350)}"],
        named,
    )


def name_of(product: str) -> str:
    """The product's name as a message says it, escaped for Slack."""
    return _text(product or PRODUCT, 100)


def refused(reason: str) -> dict:
    return _one(f"Cannot do that: {reason or 'No reason was provided.'}")


def link_prompt(url: str, command: str = "/cairn", *, product: str = PRODUCT) -> dict:
    """The two steps of a link, for a Slack user who is not linked: the address to open
    signed in, which shows a code, and the code typed back here with `<command> link`."""
    name = _text(command or "/cairn", 200)
    explanation = (
        f"Link your Slack user to your {name_of(product)} account in two steps, both within 10"
        f" minutes. Open the address below, signed in to {name_of(product)}. It shows you a"
        " code."
        f" Then type `{name} link` and the code here."
        " The address is yours alone and works once: send it to no one."
    )
    fallback = f"{explanation}\n{_url(url) or 'Link unavailable. Request a new link.'}"
    return _message(fallback, [f"{explanation}\n{_link(f'Open {product} to get your code', url)}"])


def linked(person: str, *, product: str = PRODUCT) -> dict:
    return _one(f"Linked to {person or f'your {product} account'}.")


def unlinked(command: str = "/cairn") -> dict:
    name = _text(command or "/cairn", 200)
    text = f"You are unlinked. The bot no longer acts as you here. Link again with `{name} link`."
    return _message(text, [text])


def project_is(project: str | None, *, writable: list[str]) -> dict:
    selection = f"Slack project: {_text(project, 500)}" if project else "No project chosen."
    if writable:
        choices = ", ".join(_text(name, 150) or "(unnamed)" for name in writable[:10])
        suffix = " (first 10 shown)" if len(writable) > 10 else ""
        text = f"{selection}\nWritable projects: {choices}{suffix}"
    else:
        text = f"{selection}\nNo writable projects available."
    return _message(text, [text])


def help_text(command: str, *, product: str = PRODUCT) -> dict:
    name = _text(command or "/cairn", 200)
    called = name_of(product)
    text = "\n".join(
        [
            f"{called} help",
            f"Link your {called} account with `{name} link`.",
            f"Unlink your {called} account with `{name} unlink`.",
            f"Find claims with `{name} find <words>`.",
            f"File a note with `{name} note <text>`.",
            f"Show or choose your Slack project with `{name} project [name]`.",
            f"Show this help with `{name} help`.",
            "In a direct message, enter search words, `note: <text>`, or `help`.",
            f'Use the message shortcut "File in {called}" to file a selected message.',
        ]
    )
    # Escape only the static placeholders; the custom command is already escaped.
    text = text.replace("<words>", "&lt;words&gt;").replace("<text>", "&lt;text&gt;")
    return _message(text, [text])
