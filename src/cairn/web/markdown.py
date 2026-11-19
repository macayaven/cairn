"""How the web view shows a note's own words: claims as Markdown, dates as dates.

A claim is somebody's text, often an agent's, so rendering it is the one place where text
from outside could become markup. The rules, each with a test in
tests/unit/test_markdown_unit.py:

- CommonMark with tables. Raw HTML in the text is never passed through: it is shown as the
  characters that were typed.
- A link is a link only for http, https and mailto (the renderer's own rule), and opens
  with rel="noopener noreferrer nofollow". Images are not rendered: a remote image would
  tell its host who read the page. Headings are not rendered either: a page's headings are
  cairn's, and a claim is never one.
- Text longer than MAX_RENDERED characters is shown as preformatted text and not parsed,
  so one page cannot cost more than its size to draw.
- A table's body cells carry their column's name as `data-label` (a narrow screen shows a
  row as a card), and a column's alignment is a class, never a `style` attribute.

A guide (`guide`: the Markdown shipped in cairn/guides and served at /docs/<name>) is
cairn's own text and is rendered by the same rules, raw HTML off included, except that its
headings are headings: the first level-1 heading is the guide's title (the page shows it as
its one <h1>), a later one becomes a level-2 heading, and every heading gets the id GitHub
would give it, so links between sections work in the repository and on the server. A link
to another guide by its file name (`patterns.md#x`) leads to /docs/patterns#x.

A guide never spells the product's name (B3): it writes a marker that `guide` fills in from
the product it is given (brand.product_of: the brand's, or the software's without one).
MARKERS: `{{name}}`, the name as people read it, in prose or in text an agent reads;
`{{slug}}`, a name the person types and may choose (an agent's connection, a placeholder
file); `{{SLUG}}`, the slug in capitals (a placeholder host, `https://YOUR-{{SLUG}}/mcp`).
The software's identifiers (its module, its variables, the file names it chooses) are
written as they are. The markers are filled after parsing, in each text and code token, so
a brand is shown exactly as it is written, whatever characters it holds, and the title,
the headings' ids and a table's labels are made from the filled text. Without a brand each
marker is the software's name, and every byte is what the name spelled out gives. A claim
is never filled: `render` knows no marker.

The page's Content-Security-Policy (web/templating.py) allows no script and no remote
resource in any case; this module is the first line, not the only one.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.rules_core import StateCore
from markdown_it.token import Token
from markupsafe import Markup, escape

from cairn.store.ranking import words
from cairn.web.brand import Product, product_of

MAX_RENDERED = 100_000  # characters of one claim; beyond this it is shown, not parsed
_LINK_REL = "noopener noreferrer nofollow"
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _link_open(
    renderer: RendererHTML, tokens: list[Token], index: int, options: object, env: object
) -> str:
    tokens[index].attrSet("rel", _LINK_REL)
    return renderer.renderToken(tokens, index, options, env)


def _text(inline: Token) -> str:
    """What a reader sees of an inline token: its text and code, without the markup."""
    children = inline.children or []
    return "".join(c.content for c in children if c.type in ("text", "code_inline")).strip()


_ALIGN = re.compile(r"text-align:\s*(left|center|right)")


def _cells(state: StateCore) -> None:
    """Each body cell gets its column's header text as data-label ("Column N" when the
    header is blank); an alignment the parser wrote as a style becomes a class."""
    headers: list[str] = []
    column = 0
    tokens = state.tokens
    for index, token in enumerate(tokens):
        if token.type == "thead_open":
            headers = []
        elif token.type == "tr_open":
            column = 0
        elif token.type in ("th_open", "td_open"):
            style = token.attrs.pop("style", None)
            if style is not None and (found := _ALIGN.search(str(style))):
                token.attrSet("class", f"align-{found.group(1)}")
            column += 1
            if token.type == "th_open":
                following = tokens[index + 1] if index + 1 < len(tokens) else None
                headers.append(_text(following) if following is not None else "")
            else:
                given = headers[column - 1] if column <= len(headers) else ""
                token.attrSet("data-label", given or f"Column {column}")


def _parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    parser.enable("table").disable(["image", "heading", "lheading"])
    parser.core.ruler.push("cairn_cells", _cells)
    parser.add_render_rule("link_open", _link_open)
    return parser


_PARSER = _parser()


def render(text: str) -> Markup:
    """`text` as HTML that is safe to place in a page. See the module's rules."""
    if len(text) > MAX_RENDERED:
        return Markup("<pre>") + escape(text) + Markup("</pre>\n")
    return Markup(_PARSER.render(text))  # nosec B704 # _PARSER has raw HTML off


# Search matches

_TAG = re.compile(r"(<[^>]*>)")
_TOKEN = re.compile(r"[^\W_]+")  # a token as FTS5's unicode61 tokenizer splits text


def _folded(word: str) -> str:
    """A token as the search index compares it: case folded, diacritics removed."""
    decomposed = unicodedata.normalize("NFKD", word.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def mark_words(html: Markup, query: str) -> Markup:
    """Rendered `html` with each word the search for `query` matched inside <mark>: the
    tokens of the text, never of the markup, that equal a token of the query's words
    (cairn.store.ranking.words, each split at "_" as the index splits it). Text is read
    unescaped and escaped again, so an entity is never split."""
    wanted = {_folded(part) for word in words(query) for part in word.split("_") if part}
    if not wanted:
        return Markup(html)  # nosec B704 # html is rendered Markup already
    pieces = []
    for piece in _TAG.split(str(html)):
        if not piece or piece.startswith("<"):
            pieces.append(piece)
            continue
        text, at, out = unescape(piece), 0, []
        for found in _TOKEN.finditer(text):
            if _folded(found.group()) in wanted:
                out += [escape(text[at : found.start()]), Markup("<mark>")]
                out += [escape(found.group()), Markup("</mark>")]
                at = found.end()
        out.append(escape(text[at:]))
        pieces.append("".join(out))
    return Markup("".join(pieces))  # nosec B704 # tags from html, its text escaped above


# Guides


@dataclass(frozen=True)
class Guide:
    """A guide as its page shows it: the title, the level-2 sections as (anchor, heading)
    for the list at the top, and the rest of the body as HTML."""

    title: str
    sections: list[tuple[str, str]]
    html: Markup


_GUIDE_LINK = re.compile(r"([a-z0-9-]+)\.md(#[\w-]*)?")
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.I)


def _guide_link_open(
    renderer: RendererHTML, tokens: list[Token], index: int, options: object, env: object
) -> str:
    """A link out of cairn opens as a claim's link does; a link within it stays plain."""
    if _SCHEME.match(str(tokens[index].attrGet("href") or "")):
        tokens[index].attrSet("rel", _LINK_REL)
    return renderer.renderToken(tokens, index, options, env)


MARKER = re.compile(r"\{\{(name|slug|SLUG)\}\}")  # a guide's markers (the module's rules)


def fill(text: str, product: Product) -> str:
    """`text` with each marker (MARKER) filled from `product`."""
    said = {"name": product.name, "slug": product.slug, "SLUG": product.slug.upper()}
    return MARKER.sub(lambda found: said[found.group(1)], text)


_FILLED = ("text", "code_inline", "code_block", "fence")


def _names(state: StateCore) -> None:
    """Each marker of a guide's text and code filled from the product in the parse's env,
    before the headings' ids and the cells' labels are read."""
    product = state.env["product"]
    for token in state.tokens:
        for one in [token, *(token.children or [])]:
            if one.type in _FILLED and "{{" in one.content:
                one.content = fill(one.content, product)


def _guide_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    parser.enable("table").disable("image")
    parser.core.ruler.push("cairn_names", _names)
    parser.core.ruler.push("cairn_cells", _cells)
    parser.add_render_rule("link_open", _guide_link_open)
    return parser


_GUIDE_PARSER = _guide_parser()


def anchor(heading: str) -> str:
    """The id GitHub gives a heading: lowercase, punctuation dropped, spaces as dashes."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def guide(text: str, known: Iterable[str], product: Product | None = None) -> Guide:
    """A guide's Markdown as its page shows it (see the module's rules). `known`: the names
    of the guides served beside it, which its links to `<name>.md` lead to. `product`: what
    its markers say (the software's when None)."""
    names = set(known)
    tokens = _GUIDE_PARSER.parse(text, {"product": product or product_of(None)})
    title, sections, used, kept, skip = "", [], set(), [], 0
    for index, token in enumerate(tokens):
        if skip:
            skip -= 1
            continue
        if token.type == "heading_open":
            words = _text(tokens[index + 1])
            if token.tag == "h1" and not title:
                title, skip = words, 2  # the page's own <h1>
                continue
            if token.tag == "h1":
                token.tag = tokens[index + 2].tag = "h2"
            base = anchor(words) or "section"
            name, n = base, 0
            while name in used:
                n += 1
                name = f"{base}-{n}"
            used.add(name)
            token.attrSet("id", name)
            if token.tag == "h2":
                sections.append((name, words))
        for child in token.children or []:
            href = str(child.attrGet("href") or "") if child.type == "link_open" else ""
            if (found := _GUIDE_LINK.fullmatch(href)) and found.group(1) in names:
                child.attrSet("href", f"/docs/{found.group(1)}{found.group(2) or ''}")
        kept.append(token)
    html = _GUIDE_PARSER.renderer.render(kept, _GUIDE_PARSER.options, {})
    markup = Markup(html)  # nosec B704 # _GUIDE_PARSER has raw HTML off
    return Guide(title=title, sections=sections, html=markup)


def _utc(stamp: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else None


def when(stamp: str) -> str:
    """An ISO 8601 timestamp as "10 November 2026, 22:39 UTC". Anything else comes back
    as it was, so a page never fails over a date."""
    moment = _utc(stamp)
    if moment is None:
        return stamp
    return f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}, {moment:%H:%M} UTC"


def day(stamp: str) -> str:
    """The UTC day of an ISO 8601 timestamp as "10 Nov 2026", for tables."""
    moment = _utc(stamp)
    if moment is None:
        return stamp
    return f"{moment.day} {_MONTHS[moment.month - 1][:3]} {moment.year}"
