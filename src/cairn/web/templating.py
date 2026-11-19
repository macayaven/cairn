"""The template environment and the page renderer that the UI, the refusals and the OAuth
pages share."""

import hashlib
import re
from contextvars import ContextVar
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup
from starlette.requests import HTTPConnection
from starlette.responses import HTMLResponse

from cairn import __version__
from cairn.guard import rule_words
from cairn.kinds import KINDS, kind_of
from cairn.memory import ACCESS_LABELS, reach_label
from cairn.plans import quantity
from cairn.web import components, markdown
from cairn.web.brand import SOFTWARE, Brand, product_of

HERE = Path(__file__).resolve().parent.parent  # src/cairn: templates/ and static/
# The stylesheet's address carries a digest of the file, read once at start: an upgrade that
# changes the file changes the address, so no browser keeps serving itself the old one.
_CSS = hashlib.sha256((HERE / "static" / "cairn.css").read_bytes()).hexdigest()[:12]
STYLESHEET = f"/static/cairn.css?v={_CSS}"


def _unnamed(css: str) -> str:
    """The stylesheet with each comment that names the software left out, and nothing else
    changed: every rule and token stays as it is."""
    comments = re.compile(r"/\*.*?\*/\n?", re.DOTALL)
    return comments.sub(lambda c: "" if SOFTWARE in c.group().lower() else c.group(), css)


# Under a brand (B1) the pages link this one instead: the same stylesheet with no comment
# that names the software, and the rules of the logo's plate and the brand's sign-in page
# (web/brand.css). Every brand has the same text, so it is made once, here, with its address.
BRANDED_CSS = _unnamed((HERE / "static" / "cairn.css").read_text("utf-8")) + (
    HERE / "web" / "brand.css"
).read_text("utf-8")
BRANDED_STYLESHEET = f"/static/site.css?v={hashlib.sha256(BRANDED_CSS.encode()).hexdigest()[:12]}"
UI_HEADERS = {
    "content-security-policy": (
        "default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; "
        "form-action 'self'; manifest-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "cache-control": "no-store",
}

NO_PAGE = "There is no page at this address."
# cairn.memory's NotFound messages, which REST and MCP give as they are, as a reader is told.
_MISSING = (
    (r"no project (.+)", "There is no project named {} here."),
    (
        r"no version (\d+) of page (.+) in project (.+)",
        "There is no version {} of the page {} in the project {}.",
    ),
    (r"no page (.+) in project (.+)", "There is no page {} in the project {}."),
    (
        r"no claim (.+) on page (.+) in project (.+)",
        "There is no claim {} on the page {} in the project {}.",
    ),
)


def error_sentence(status: int, message: str) -> str:
    """The heading of the HTML error page: `message`, the one REST and MCP give, as a
    sentence. A missing project, page, version or claim is said in full ("There is no project
    named 'hiring' here."); a bare HTTP reason phrase in sentence case, or NO_PAGE for 404;
    anything else with a capital first letter and a full stop."""
    for pattern, sentence in _MISSING:
        if found := re.fullmatch(pattern, message):
            return sentence.format(*found.groups())
    try:
        phrase = HTTPStatus(status).phrase
    except ValueError:
        phrase = None
    if message == phrase:
        return NO_PAGE if status == 404 else message[:1] + message[1:].lower() + "."
    text = message[:1].upper() + message[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


# Who reaches a private project besides its people, said in the same words wherever one is
# made: the new-project form and, on /start, the card of a kind made private.
PRIVATE_REACH = (
    "Only you, the people you name, and the workspace's owners reach it, along with the keys"
    " and sources that belong to whoever runs the server, such as a key made on its command"
    " line."
)

templates = Environment(
    loader=FileSystemLoader(HERE / "templates"),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
templates.globals["version"] = __version__
templates.globals["stylesheet"] = STYLESHEET  # base.html links it
templates.globals["branded_stylesheet"] = BRANDED_STYLESHEET  # ... or this, under a brand
templates.globals["software"] = SOFTWARE
templates.globals["person"] = None  # who signed in, on the pages that have one (ui._ui)
templates.globals["signed_in"] = False  # the app's navigation, not a visitor's (ui._ui)
templates.filters["seg"] = lambda value: quote(str(value), safe="")  # one path segment
templates.filters["md"] = markdown.render  # a claim's own Markdown, made safe
templates.filters["when"] = markdown.when  # an ISO timestamp, as a person reads a date
templates.filters["day"] = markdown.day  # the same, day only, for tables
templates.filters["a_role"] = lambda role: ("an " if str(role)[:1] in "aeiou" else "a ") + role
templates.globals["reach_label"] = reach_label  # a connection's projects and sets by name
templates.globals["access_labels"] = ACCESS_LABELS
templates.globals["private_reach"] = Markup(PRIVATE_REACH)  # our own words, as written
templates.globals["kinds"] = KINDS  # what a project can be for (cairn.kinds), in order
templates.globals["kind_of"] = kind_of  # a project's kind; "general" for one no longer shipped
templates.globals["error_sentence"] = error_sentence
templates.globals["rule_words"] = rule_words  # a guard rule, as the audit page says it
templates.globals["quantity"] = quantity  # an amount of a limit, as /usage says it
templates.filters["marked"] = markdown.mark_words  # a search hit's matched words on mark
templates.globals["actor_kind"] = components.actor_kind  # an audit actor's kind, as a word
templates.globals["made_with_words"] = components.made_with_words  # A1: a key's maker key
templates.globals["unrecorded_words"] = components.unrecorded_words  # A1: before recording
templates.globals["arranger"] = components.arranger  # who arranged a page, as a word
templates.globals["scope_of"] = components.scope_of  # a key's or agent's reach, and beyond


# The path of the request being answered. Auth sets it first thing, so every page knows where
# its reader is: the main navigation marks that section, and a page the workspace lists leads
# back to it.
PATH: ContextVar[str] = ContextVar("cairn_path", default="")
# Whether the app answering has /help (a documentation release is configured:
# web/docs_helper.py). Its middleware sets it on every request, so the footer links to Help
# only then.
HELP: ContextVar[bool] = ContextVar("cairn_help", default=False)


def section() -> str | None:
    """The main navigation's entry the current page belongs to, by its href: "/" for the
    projects, everything in one and the first run's choice of projects (/start); "/search",
    "/new" for a note being written anywhere, "/workspace" for that page and every page it
    lists (its Setup, /start/connect, the first run's next step, among them), "/workspaces"
    for the person's own list. None for any other page."""
    from cairn.web.workspace import PARTS  # it renders through this module

    path = PATH.get()
    if path == "/new" or (path.startswith("/b/") and path.endswith("/new")):
        return "/new"
    if path in ("/", "/start") or path.startswith("/b/"):
        return "/"
    if path in ("/search", "/workspaces"):
        return path
    if path == "/workspace" or path in {part.href for part in PARTS}:
        return "/workspace"
    return None


templates.globals["section"] = section
templates.globals["path"] = PATH.get
templates.globals["help"] = HELP.get


BRAND = "cairn.brand"  # the ASGI scope key under which Auth puts the app's Brand, or None


def brand_of(request: HTTPConnection) -> Brand | None:
    """The brand of the app answering `request`, as Auth put it on the request (B1); None
    without one, and for a request Auth did not see."""
    return request.scope.get(BRAND)


def _page(
    name: str, status_code: int = 200, *, brand: Brand | None, **context: Any
) -> HTMLResponse:
    """The template `name`, as the brand (or none) frames it: base.html names the server
    once, from `brand` (B1). `brand` is required, so a page cannot forget it. Every page
    names the product as `product` (brand.product_of: the brand's name and slug, or the
    software's), the one name a template asks for (B3)."""
    html = templates.get_template(name).render(brand=brand, product=product_of(brand), **context)
    return HTMLResponse(html, status_code=status_code, headers=UI_HEADERS)
