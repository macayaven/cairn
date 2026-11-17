"""The brand (B1): an operator's own name, and maybe its logo, shown in place of cairn's.

The contract is in cairn/server.py ("A wall, a brand"), frozen by
tests/acceptance/test_b1_wall.py.

One `Brand` is made at start from `serve --brand-name`, `--brand-logo` and `--brand-logo-alt`
(create_app's `brand_name`, `brand_logo`, `brand_logo_alt`), by `make_brand`, and never
changes: a frozen dataclass holding the name, the logo's bytes and media type, and its text
alternative. It is not kept anywhere global. create_app hands it to Auth, which puts it on
every request's scope (auth.BRAND), and to what decides a name at start (the OAuth scope, the
cookies). A page reads it from its request (templating.brand_of) and the frame says what it
says once, in base.html. No brand is None, and with None every path is today's.

The name: 1 to NAME_MAX characters, not blank, with no control or format character (they
can hide or reorder text) and no `<` or `>`, and text a page can be written in: no lone
surrogate, which is what Python makes of a byte in an argument that is not UTF-8 (every
branded page would fail to encode it). It is shown escaped anyway; the rule keeps it one
plain line. The logo's text alternative follows the same rule.

The logo is read once, here, and judged by its content, never by its file name:
- A PNG by its signature and the IHDR chunk that must follow it.
- An SVG by being one XML document whose root element is `svg` in the SVG namespace. It is
  parsed with expat, with no handler that resolves anything: a document type declaration
  (an external DTD, an internal subset, entities) is refused as soon as the parser meets
  it, before anything it names is read or expanded, so reading a logo fetches nothing,
  opens no other file, and cannot be made to expand without bound. The file is at most
  LOGO_MAX bytes, so the parse is bounded too.
A file that is missing, a folder, larger, or neither stops the server at start (ValueError
naming the option). No refusal says the file's path, and no page or address does: the logo
is served from `Logo.path`, /static/logo-<the first 16 hex digits of its SHA-256>.<svg or
png>, which changes when the file does (app.py serves it, with its media type, nosniff, a
year's immutable caching and, for an SVG and a PNG alike, a policy that lets it run and
fetch nothing and lets no page frame it; its bytes are never rewritten or filtered).

B3 (frozen by tests/acceptance/test_b3_brand_inside.py): under a brand, the signed-in
product says the brand's name, to people and to their agents.
- The name is said in one place. `product_of(brand)` is the Product an app is: the brand's
  name and its slug, or SOFTWARE for both without a brand. Nothing else spells the name:
  every template asks for `product.name` (templating._page gives each page the product of
  its brand, as B1 gave it the title's suffix), every guide writes a marker the renderer
  fills (web/markdown.py), and every sentence in Python that names the product is given
  its name by its caller (guard.rule_words and policy_words, a source's switch-on sentence,
  the start page's refusal, the audit's word for the system, the MCP server's
  instructions). tests/unit/test_brand_inside_unit.py fails when a template or a guide
  spells the name outside an identifier.
- Code keeps identifiers and takes the slug for what a person names. Templates and guides
  say which is which as they are written: an identifier the person types exactly (the
  module in `python -m cairn.server`, a `CAIRN_...` variable, `cairn.db`, `cairn.env`) is
  written as it is; a name the person chooses (an agent's connection, a placeholder host or
  file, a label) is `product.slug`, or the guide's `{{slug}}` / `{{SLUG}}`. Nothing guesses
  by pattern at render time.
- The slug (`slug_of`): the name in lower case, each run of anything but an ASCII letter or
  digit one dash, the ends trimmed; NO_SLUG ("memory", the word the OAuth scope says under
  a brand) when nothing is left. Without a brand the slug is SOFTWARE, so every byte is
  today's.
- What a person wrote is theirs: a note, a title, a project's name, the operator's own notes
  are shown as written. Output is never rewritten; the right word is said at its source.
- What stays the software's: identifiers, the token prefixes, a guard rule's name in an
  API's answer (`cairn_credential`), stored identifiers (what a tidy is recorded under,
  arrange.TIDY: a page's metrics give it as stored over MCP and REST, and a web page shows
  the product's name for it), the logs, the command line, the operator's files and the
  database.
"""

import hashlib
import re
import unicodedata
import xml.parsers.expat
from dataclasses import dataclass, field
from pathlib import Path

from cairn import SOFTWARE  # how the software names itself with no brand

NAME_MAX = 60  # characters of a brand name
ALT_MAX = 200  # characters of a logo's text alternative
LOGO_MAX = 256 * 1024  # bytes of a logo file
SVG = "image/svg+xml"
PNG = "image/png"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SVG_ROOT = "http://www.w3.org/2000/svg svg"  # expat's name for it, namespace first
_EXTENSIONS = {SVG: "svg", PNG: "png"}


@dataclass(frozen=True)
class Logo:
    """A logo file as it was read: its bytes, untouched, and its media type."""

    data: bytes = field(repr=False)
    media_type: str

    @property
    def path(self) -> str:
        """The one address it is served from: its digest in it, its file's path never."""
        digest = hashlib.sha256(self.data).hexdigest()[:16]
        return f"/static/logo-{digest}.{_EXTENSIONS[self.media_type]}"


@dataclass(frozen=True)
class Brand:
    """The operator's name, shown in place of cairn's, and maybe a logo with its text."""

    name: str
    logo: Logo | None = None
    logo_alt: str | None = None


NEUTRAL = "this server"  # ... and how a sign-in refusal names it under any brand (B1)
NO_SLUG = "memory"  # the slug of a brand with no ASCII letter or digit (the scope's word)
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def server_name(brand: Brand | None) -> str:
    """How a refusal sentence names the server: SOFTWARE, or NEUTRAL under a brand."""
    return SOFTWARE if brand is None else NEUTRAL


def slug_of(name: str) -> str:
    """The brand's name as a name a person types (B3): lower case, each run of anything but
    an ASCII letter or digit one dash, the ends trimmed; NO_SLUG when nothing is left.
    "Knowledge Base" is "knowledge-base"."""
    return _NOT_SLUG.sub("-", name.lower()).strip("-") or NO_SLUG


@dataclass(frozen=True)
class Product:
    """What the product is called (B3): `name`, as people read it, and `slug`, what a
    person types where they choose a name (an agent's connection, a placeholder)."""

    name: str
    slug: str


def product_of(brand: Brand | None) -> Product:
    """The product an app is: the brand's name and slug, or the software's (both SOFTWARE)
    without a brand. Every page, guide and sentence that names the product asks this."""
    if brand is None:
        return Product(SOFTWARE, SOFTWARE)
    return Product(brand.name, slug_of(brand.name))


def _plain(value: str, option: str, most: int) -> str:
    """`value` when it is 1 to `most` characters, not blank, with no control or format
    character and no < or >; ValueError naming `option` otherwise."""
    if not isinstance(value, str) or not value.strip() or len(value) > most:
        raise ValueError(f"{option} is 1 to {most} characters, not blank")
    if any(unicodedata.category(c) in ("Cc", "Cf") or c in "<>" for c in value):
        raise ValueError(f"{option} has no control character and no < or >")
    try:  # a lone surrogate (a byte that is not UTF-8, as argv carries one) is no text
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{option} is text a page can be written in (UTF-8)") from None
    return value


def _is_png(data: bytes) -> bool:
    return data.startswith(_PNG_SIGNATURE) and data[12:16] == b"IHDR"


class _NotSvg(Exception):
    pass


def _refuse(*_: object) -> None:
    raise _NotSvg("a document type is not read")


def _is_svg(data: bytes) -> bool:
    """One XML document whose root is `svg` in the SVG namespace, with no document type."""
    parser = xml.parsers.expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse
    roots: list[str] = []
    depth = [0]

    def start(name: str, attributes: object) -> None:
        if depth[0] == 0:
            roots.append(name)
        depth[0] += 1

    def end(name: str) -> None:
        depth[0] -= 1

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(data, True)
    except (_NotSvg, xml.parsers.expat.ExpatError):
        return False
    return roots == [_SVG_ROOT]


def read_logo(path: str | Path) -> Logo:
    """The logo file at `path`, read once and judged by its content. ValueError naming
    --brand-logo, and never the path, for a file that cannot be used."""
    file = Path(path)
    if not file.is_file():
        raise ValueError("--brand-logo names no file that can be read")
    try:
        with file.open("rb") as opened:
            data = opened.read(LOGO_MAX + 1)
    except OSError:
        raise ValueError("--brand-logo names no file that can be read") from None
    if len(data) > LOGO_MAX:
        raise ValueError(f"--brand-logo is at most {LOGO_MAX // 1024} KB")
    if _is_png(data):
        return Logo(data, PNG)
    if _is_svg(data):
        return Logo(data, SVG)
    raise ValueError(
        "--brand-logo is a PNG file or an SVG file (one XML document whose root is svg, with"
        " no document type)"
    )


def make_brand(
    name: str | None, logo: str | Path | Logo | None = None, alt: str | None = None
) -> Brand | None:
    """The brand of these settings; None without a name. `logo`: a file's path, or a Logo
    already read (serve reads it before it opens anything). ValueError naming the option."""
    if name is None:
        if logo is not None:
            raise ValueError("--brand-logo needs --brand-name")
        if alt is not None:
            raise ValueError("--brand-logo-alt needs --brand-logo")
        return None
    _plain(name, "--brand-name", NAME_MAX)
    if logo is None:
        if alt is not None:
            raise ValueError("--brand-logo-alt needs --brand-logo")
        return Brand(name)
    if alt is not None:
        _plain(alt, "--brand-logo-alt", ALT_MAX)
    read = logo if isinstance(logo, Logo) else read_logo(logo)
    return Brand(name, read, name if alt is None else alt)
