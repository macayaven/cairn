"""B1 acceptance tests (frozen): a wall, a brand, and a sign-in that is remembered.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/server.py, src/cairn/web/auth.py and
src/cairn/web/signin.py.

Why: an operator puts a private pre-release in front of a few named people under their own
organisation's name. A visitor who is not signed in sees one thing: a sign-in page with the
organisation's logo, a short name and a button per identity provider. Once a listed person
has signed in on a machine, that machine stays signed in, and they can connect their agents.
Who may sign in is `allowed_emails` (frozen H8); signing in is frozen H4.

The settings
- `serve --wall` (only with `--sign-in`), `--brand-name NAME`, `--brand-logo FILE`,
  `--brand-logo-alt TEXT` and `--session-days N`. `create_app` takes them as keywords:
  `wall=False`, `brand_name=None`, `brand_logo=None` (a path), `brand_logo_alt=None` and
  `session_days=None`; left out, or given those values, nothing changes.
- A brand name is 1 to 60 characters, with no control character and no `<` or `>`. A logo is
  an SVG or a PNG file of at most 256 KB, told by its content, not its name. `--brand-logo`
  needs `--brand-name`; `--brand-logo-alt` is the logo's text alternative (the brand name
  when not given). `--session-days` is a whole number from 1 to 365.
- A setting that would do nothing is refused: `--brand-name` and `--session-days` only with
  `--sign-in`, `--brand-logo-alt` only with `--brand-logo`.
- A value that cannot be used, or a setting refused: exit 2 before anything is opened (the
  database file is not made), and the last line on stderr names the option (an argparse
  error's line does).

The wall: what a browser that is not signed in can receive (no web session, no API credential)
- GET or HEAD of any page address, known or not, answers 303 to the sign-in page (an unknown
  /.well-known/ address may instead answer a 404 that is not the not-found page). The
  landing page, the guides, the key-login page and the "no such page" page
  are not shown. The redirect carries `next` only for the addresses worth coming back to
  (the agent-consent page, the share target /new, an invitation /join/<token>), exactly as
  today; for every other address it carries nothing of the path. The sign-in page's own
  address is the sign-in page.
- Any other method on a page address: 401 with a short plain-text body, nothing written (a
  cross-origin form post may still be refused first, 403, as today).
- Not for search engines: every response to a signed-out browser for a page address (the
  redirect, the 401, the 403, the come-again page, a 404 for an unknown /.well-known/
  address), the sign-in page's address included, and every page the sign-in addresses
  answer with (a refused or failed sign-in, the page over the limit), carries an
  `X-Robots-Tag` header holding `noindex`. A `<meta name="robots">` may be there too.
- The sign-in page, the provider's start and callback addresses, /healthz and the files the
  sign-in page needs (its stylesheet, its fonts, the logo) answer as a person signing in needs.
  Over the sign-in limit (frozen: SIGN_INS starts, and callbacks, a minute per address), the
  answer is the sign-in page with its one sentence, as a refusal's.
- A browser another site sent to an address worth coming back to still gets the page that
  asks it to come again by itself (200, a refresh to that address), and that page shows
  nothing but what it needs.
- What machines use answers exactly as without the wall: /api/... and /mcp (their JSON 401),
  the OAuth metadata, registration, /authorize, /token and /revoke.
- A signed-in browser is not affected: every page is byte for byte as without the wall.
  Signing out lands on the sign-in page. An API key or an agent's token opens /api and /mcp
  as today, and no page.

The brand: the software's own name nowhere a signed-out browser can look
- With a brand name, wall on or off, no response a browser that is not signed in can get,
  on any address by any method, holds the software's name (these five letters in a row, in
  any case) in its body or in a header's name or value (a cookie's name and a redirect's
  target included), and no address a served page, stylesheet or manifest refers to holds
  it. That covers the sign-in page, a refused sign-in, the come-again page, a redirect, a
  plain-text or JSON refusal, the OAuth metadata (its scope included), the stylesheet's own
  text and the logo, through every provider (GitHub, Google, an OpenID Connect provider): a
  failed or cancelled sign-in, a person not on the list, an address the provider has not
  verified, a provider that changed, and a success. Without the wall, a signed-out browser
  at / is not shown the landing page (it names the software).
- The sign-in page shows the logo when there is one (an `img` whose `alt` is the
  alternative text), the brand name as its one heading and as its whole `<title>`, one link
  per provider ("Continue with GitHub", in the order the providers were given), and, after a
  refused sign-in, the refusal's
  sentence; nothing else: no navigation, no footer, no form, no other link, no version, no
  sentence about sessions or passwords. Its icon is the logo, or none.
- The logo is served, its bytes as the file holds them, by the server from one address, as an
  image only (never inlined), with
  its real media type, `X-Content-Type-Options: nosniff`, and for an SVG a
  Content-Security-Policy of `default-src 'none'` that lets it run and fetch nothing.
- The design is the product's own: the same stylesheet's tokens, light and dark.
- Signed in, the header's wordmark links home with the logo and the brand name, and each
  page's `<title>` carries the brand name where it carried the software's. What pages and
  guides say is unchanged.

A sign-in that is remembered (`session_days=N`)
- The web session a sign-in makes lasts N days: the stored session and the cookie's Max-Age
  agree. So does every web session a signed-in person is given: switching workspace, taking
  an invitation. A request with a session made or last renewed more than a day ago extends it to N
  days from now and sends the cookie again (same name and attributes, Max-Age N days); within
  a day of the last renewal a request changes nothing and sends no cookie. Signing out ends it
  at once. Without the setting: 12 hours, never renewed, as today.
- `python -m cairn.server session-end --db PATH --email ADDRESS` ends every web session of
  the person with that address, in every workspace, prints how many (the only number it
  prints), and prints no other address; nobody has it: exit 0 and 0. API keys and agents'
  grants are not touched.
- An agent's authorization from a browser that never signed in goes through the sign-in
  page and back to the consent page for the same request; a second agent on that browser
  goes straight to consent.

Agents outlive the brand
- The one scope has two names: the software's, and under a brand another one, the same for
  every brand (so a server with no brand knows it). The two names are one scope; the server
  takes either and says only its current one (in its metadata's `scopes_supported` and in a
  token answer's `scope`). An agent connected before the brand was set, or before it was
  removed, on the same database, keeps working: its access token opens /mcp, its refresh
  token refreshes (asking for either name, or none), and its registered client's new
  authorization, asking for either name, reaches consent and ends in a working token. Under
  the brand none of those answers names the software (the token answer, the agent's own,
  aside).
"""

import base64
import hashlib
import importlib.resources
import itertools
import json
import re
import secrets
import sqlite3
import struct
import time
import zlib
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urljoin, urlsplit

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

import cairn
from cairn import server
from cairn.identity import OIDC, GitHub, Google
from cairn.plans import Plan, Plans
from cairn.server import SignIn, create_app
from cairn.store import Store

# The software's own name, which a signed-out browser must never be shown under a brand.
NAME = cairn.__name__.lower()
HOST = "kb.example.test"  # name-free, so that the only way the name shows is the server's
BASE = f"https://{HOST}"
BRAND = "Knowledge Base"
ALT = "Juniper Field"
ADA, BROOK, CY, DEE = (f"{who}@example.test" for who in ("ada", "brook", "cy", "dee"))
ADA_GITHUB = {"id": 7001, "login": "ada", "name": "Ada Quill", "email": ADA}
BROOK_GITHUB = {"id": 7002, "login": "brook", "name": "Brook Ng", "email": BROOK}
CY_GITHUB = {"id": 7003, "login": "cy", "name": "Cy Moss", "email": CY}
CLIENT_SECRET = "-".join(("gh", "client", "secret", "made", "up"))
SECRETS = {
    "github": CLIENT_SECRET,
    "google": "-".join(("g", "client", "secret", "made", "up")),
    "fieldid": "-".join(("fid", "client", "secret", "made", "up")),
}
# A stand-in OpenID Connect provider, as an operator would configure one.
ISSUER = "https://id.example.test/pool-7"
OIDC_LABEL = "Field ID"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/oauth2/authorize",
    "token_endpoint": f"{ISSUER}/oauth2/token",
    "userinfo_endpoint": f"{ISSUER}/oauth2/userInfo",
    "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
}
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
CROSS_SITE = {"sec-fetch-site": "cross-site", "origin": "https://elsewhere.example.test"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
REDIRECT = "http://127.0.0.1:33418/callback"
# The store's clock in these tests. It is far ahead of any day the tests run on: the MCP SDK's
# token endpoint compares a code's and a refresh token's expiry, which the store stamps by
# this clock, with the real one, so a start the real clock has passed makes every code look
# expired (a start of 9:00 on the day this file was written failed from 9:05 that day).
START = datetime(2099, 11, 14, 9, 0, tzinfo=UTC)
DAY, HOUR, MINUTE = timedelta(days=1), timedelta(hours=1), timedelta(minutes=1)
TWELVE_HOURS = 12 * 3600
PLANS = Plans(
    {
        "free": Plan("Free", notes_per_month=1_000, projects=50, model_budget_micros=0),
        "solo": Plan("Solo", notes_per_month=10_000, projects=500, model_budget_micros=0),
    },
    default="free",
)
# A logo drawn for a light background: dark lettering.
SVG_LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 32" width="160" height="32">'
    '<text x="4" y="22" fill="#1b2a24" font-family="sans-serif" font-size="18">'
    f"{ALT}</text></svg>\n"
).encode()
SCRIPTED_LOGO = (
    '<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 32">'
    "<script>document.title = 'ran'</script>"
    f'<text x="4" y="22" fill="#1b2a24">{ALT}</text></svg>\n'
).encode()
# What the server says about itself on the pages a signed-out browser must not see; any
# segment of text at least this long that the sign-in page does not also have.
OWN_WORDS_MIN = 20
# The values the crawl puts into each path parameter of the app's routes. A route added
# later with a parameter missing here fails the crawl until it is added.
PLAIN_PARAMS = {
    "provider": ["github", "no-such-provider"],
    "project": ["core", "no-such-project"],
    "slug": ["retry-policy"],
    "grant": ["grant-1"],
    "label": ["laptop"],
    "set": ["set_000000"],
    "note": ["note-1"],
    "note_id": ["note-1"],
    "claim_id": ["claim-1"],
    "name": ["connect", "no-such-guide"],
    "invite": ["invite-1"],
    "user": ["usr_000000000000"],
    "account": ["acct-1"],
}
# Queries that the addresses worth coming back to carry in real use.
QUERIES = {"/new": ["title=a-shared-note"], "/oauth/consent": ["request=a.b"]}
_PLAIN_QUERY = re.compile(r"[A-Za-z0-9._~%=&+-]+")  # today's rule for the come-again page
MACHINE = frozenset({"/healthz", "/authorize", "/token", "/register", "/revoke"})


# What the software's name looks like in a response


_TOKEN = re.compile(r"[A-Za-z0-9_-]")


def names_software(text: str | bytes) -> bool:
    """Whether `text` holds the software's name, in any case. A match inside a long run of
    token characters (a random session token, a sealed state) is chance, not a name."""
    if isinstance(text, bytes):
        text = text.decode("latin-1")
    for found in re.finditer(re.escape(NAME), text, re.IGNORECASE):
        start, end = found.start(), found.end()
        while start > 0 and _TOKEN.match(text[start - 1]):
            start -= 1
        while end < len(text) and _TOKEN.match(text[end]):
            end += 1
        if end - start < 32:
            return True
    return False


def no_name_in(response: httpx.Response, what: str) -> None:
    assert not names_software(response.content), f"{what}: the body names the software"
    for header, value in response.headers.multi_items():
        assert not names_software(header), f"{what}: header {header!r} names the software"
        assert not names_software(value), f"{what}: header {header} names the software: {value}"


# Pages


class Page(HTMLParser):
    """A page's tags, its visible text in segments, its title, headings and links."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.texts: list[str] = []
        self.title = ""
        self.h1: list[str] = []
        self.anchors: list[dict] = []
        self._skip: str | None = None
        self._a: dict | None = None
        self._h1: list[str] | None = None
        self.feed(html)
        self.close()
        self.title = " ".join(self.title.split())

    def handle_starttag(self, tag, attrs):
        attributes = {name: value or "" for name, value in attrs}
        self.tags.append((tag, attributes))
        if tag in ("title", "script", "style"):
            self._skip = tag
        elif tag == "a":
            self._a = {"href": attributes.get("href"), "text": [], "imgs": []}
            self.anchors.append(self._a)
        elif tag == "img" and self._a is not None:
            self._a["imgs"].append(attributes)
        elif tag == "h1":
            self._h1 = []

    def handle_endtag(self, tag):
        if tag == self._skip:
            self._skip = None
        elif tag == "a":
            self._a = None
        elif tag == "h1" and self._h1 is not None:
            self.h1.append(" ".join("".join(self._h1).split()))
            self._h1 = None

    def handle_data(self, data):
        if self._skip == "title":
            self.title += data
            return
        if self._skip:
            return
        if self._a is not None:
            self._a["text"].append(data)
        if self._h1 is not None:
            self._h1.append(data)
        if text := " ".join(data.split()):
            self.texts.append(text)

    def named(self, *tags: str) -> list[dict[str, str]]:
        return [attributes for tag, attributes in self.tags if tag in tags]

    def refs(self) -> list[str]:
        """Every address the page refers to: links, images, scripts, forms, a refresh."""
        found = []
        for tag, attributes in self.tags:
            for attribute in ("href", "src", "action", "poster", "data", "formaction"):
                if attributes.get(attribute):
                    found.append(attributes[attribute])
            for part in attributes.get("srcset", "").split(","):
                if part.strip():
                    found.append(part.split()[0])
            if tag == "meta" and attributes.get("http-equiv", "").lower() == "refresh":
                found.append(refresh_target_of(attributes.get("content", "")))
        return found


def refresh_target_of(content: str) -> str:
    return content.split("=", 1)[1].strip() if "=" in content else ""


def refresh_target(html: str) -> str:
    targets = [refresh_target_of(m["content"]) for m in Page(html).named("meta") if
               m.get("http-equiv", "").lower() == "refresh"]  # fmt: skip
    assert len(targets) == 1, "no single meta refresh on the page"
    return targets[0]


def css_refs(css: str) -> list[str]:
    urls = re.findall(r"url\(\s*['\"]?([^'\")\s]+)", css)
    return urls + re.findall(r"@import\s+['\"]([^'\"]+)", css)


def manifest_refs(text: str) -> list[str]:
    data = json.loads(text)
    found = [data.get(key) for key in ("start_url", "scope", "id")]
    found += [icon.get("src") for icon in data.get("icons", [])]
    found.append((data.get("share_target") or {}).get("action"))
    return [value for value in found if isinstance(value, str)]


def local(address: str, at: str) -> str | None:
    """The path and query of `address`, as read on the page at `at`, when it is this server's."""
    if address.startswith(("data:", "mailto:", "javascript:")):
        return None
    parts = urlsplit(urljoin(BASE + at, address))
    if parts.netloc != HOST:
        return None
    return parts.path + (f"?{parts.query}" if parts.query else "")


def query(url: str) -> dict:
    return {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}


def hidden_fields(html: str) -> dict[str, str]:
    return {a["name"]: a.get("value", "") for a in Page(html).named("input")
            if a.get("type") == "hidden"}  # fmt: skip


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def png(width: int = 2, height: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    rows = b"".join(b"\x00" + b"\x1b\x2a\x24" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + (chunk(b"IEND", b""))
    )


# The world: a stand-in identity provider, a store on a clock


class FakeProviders:
    """GitHub's, Google's and an OpenID Connect provider's OAuth and profile endpoints, as an
    httpx mock (as frozen H4 builds them)."""

    def __init__(self) -> None:
        self.codes: dict[str, tuple[str, dict, bool, str | None]] = {}
        self.tokens: dict[str, tuple[dict, bool]] = {}

    def approve(
        self, profile: dict, provider: str = "github", verified: bool = True, nonce=None
    ) -> str:
        """The person approved at the provider; the code it redirects back with."""
        code = f"code-{provider}-{secrets.token_hex(4)}"
        self.codes[code] = (provider, profile, verified, nonce)
        return code

    def id_token(self, profile: dict, nonce: str | None) -> str:
        claims = {
            "iss": ISSUER,
            "aud": "fieldid-client-id",
            "sub": profile["sub"],
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": nonce,
        }

        def part(value: dict) -> str:
            return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

        return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part(claims)}.c2lnbmF0dXJl"

    def _token(self, provider: str, request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        sent = json.loads(body) if body.lstrip().startswith("{") else dict(parse_qsl(body))
        expected = self.codes.pop(sent.get("code", ""), None)
        if expected is None or expected[0] != provider:
            return httpx.Response(400, json={"error": "bad_verification_code"})
        token = f"provider-token-{secrets.token_hex(8)}"
        self.tokens[token] = (expected[1], expected[2])
        answer = {"access_token": token, "token_type": "bearer"}
        if provider == "fieldid":
            answer["id_token"] = self.id_token(expected[1], expected[3])
        return httpx.Response(200, json=answer)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json=DISCOVERY)
        if url == "https://github.com/login/oauth/access_token":
            return self._token("github", request)
        if url == "https://oauth2.googleapis.com/token":
            return self._token("google", request)
        if url == DISCOVERY["token_endpoint"]:
            return self._token("fieldid", request)
        found = self.tokens.get(request.headers.get("authorization", "").partition(" ")[2])
        if found is None:
            return httpx.Response(401, json={"message": "Bad credentials"})
        profile, verified = found
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={k: profile[k] for k in ("id", "login", "name")})
        if url == "https://api.github.com/user/emails":
            emails = [{"email": profile["email"], "primary": True, "verified": verified}]
            return httpx.Response(200, json=emails)
        if url == "https://openidconnect.googleapis.com/v1/userinfo":
            return httpx.Response(200, json={**profile, "email_verified": verified})
        if url == DISCOVERY["userinfo_endpoint"]:  # the flag as a string, as some send it
            return httpx.Response(200, json={**profile, "email_verified": str(verified).lower()})
        return httpx.Response(404)


def as_profile(person: dict, provider: str) -> dict:
    """A person's profile (given as GitHub's) as `provider` answers it."""
    if provider == "github":
        return person
    return {"sub": f"{provider}-{person['id']}", "name": person["name"], "email": person["email"]}


PROVIDERS = {
    "github": lambda: GitHub("github-client-id", SECRETS["github"]),
    "google": lambda: Google("google-client-id", SECRETS["google"]),
    "fieldid": lambda: OIDC("fieldid", OIDC_LABEL, ISSUER, "fieldid-client-id", SECRETS["fieldid"]),
}


class World:
    """One database on a clock the test moves, and the apps and browsers made on it."""

    def __init__(self, folder: Path, listed: tuple[str, ...]) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.db = folder / "kb.db"
        self.now = START
        self.store = Store(self.db, clock=lambda: self.now)
        self.idp = FakeProviders()
        self.listed = listed
        self.browsers: list[TestClient] = []

    def app(self, listed: tuple[str, ...] | None = None, providers=("github",), **settings):
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.idp.handler))
        sign_in = SignIn(
            providers=tuple(PROVIDERS[name]() for name in providers),
            http=http,
            join="personal",
            allowed_emails=self.listed if listed is None else listed,
        )
        return create_app(
            self.store,
            public_url=BASE,
            sign_in=sign_in,
            plans=PLANS,
            clock=lambda: self.now,
            **settings,
        )

    def browser(self, app) -> TestClient:
        client = TestClient(app, base_url=BASE)
        client.__enter__()
        self.browsers.append(client)
        return client

    def logo(self, content: bytes = SVG_LOGO, name: str = "logo.svg") -> Path:
        path = self.folder / name
        path.write_bytes(content)
        return path

    def branded(self, content: bytes = SVG_LOGO, name: str = "logo.svg", **more) -> dict:
        logo = self.logo(content, name)
        return {"brand_name": BRAND, "brand_logo": logo, "brand_logo_alt": ALT, **more}

    def alive(self, token: str, at: datetime) -> bool:
        """Whether the stored session `token` still works at `at` (the clock is put back)."""
        now, self.now = self.now, at
        try:
            return self.store.resolve_session(token) is not None
        finally:
            self.now = now


@pytest.fixture
def make(tmp_path):
    made: list[World] = []

    def build(name: str = "one", listed: tuple[str, ...] = (ADA, CY, DEE)) -> World:
        world = World(tmp_path / name, listed)
        made.append(world)
        return world

    yield build
    for world in made:
        for client in world.browsers:
            client.__exit__(None, None, None)
        world.store.close()


def set_cookies(response: httpx.Response) -> dict[str, tuple[str, dict]]:
    """Each cookie the response sets: its value and its attributes, in lowercase."""
    found = {}
    for header in response.headers.get_list("set-cookie"):
        jar = SimpleCookie()
        jar.load(header)
        for name, morsel in jar.items():
            attributes = {key.lower(): value for key, value in morsel.items() if value}
            found[name] = (morsel.value, attributes)
    return found


def session_set(response: httpx.Response) -> tuple[str, str, dict]:
    """The session cookie a response sets (Path=/, with a value): name, value, attributes."""
    found = [
        (name, value, attributes)
        for name, (value, attributes) in set_cookies(response).items()
        if value and attributes.get("path") == "/"
    ]
    said = response.headers.get_list("set-cookie")
    assert len(found) == 1, f"no single session cookie set: {said}"
    return found[0]


def start_sign_in(
    browser: TestClient, next_path: str | None = None, provider: str = "github"
) -> dict:
    path = f"/auth/{provider}" + (f"?next={quote(next_path, safe='')}" if next_path else "")
    r = browser.get(path, follow_redirects=False)
    assert r.status_code == 302, r.text
    return query(r.headers["location"])


def sign_in(
    world: World,
    browser: TestClient,
    profile: dict,
    next_path: str | None = None,
    *,
    provider: str = "github",
    verified: bool = True,
):
    """The whole trip: start, approve at the provider, come back to the callback."""
    sent = start_sign_in(browser, next_path, provider)
    code = world.idp.approve(as_profile(profile, provider), provider, verified, sent.get("nonce"))
    return browser.get(
        f"/auth/{provider}/callback?code={code}&state={sent['state']}", follow_redirects=False
    )


def signed_in(world: World, browser: TestClient, profile: dict) -> tuple[str, str]:
    """Sign in; the session cookie's name and value."""
    r = sign_in(world, browser, profile)
    assert r.status_code == 200, r.text
    name, token, _ = session_set(r)
    return name, token


def sign_in_address(browser: TestClient) -> str:
    """Where a walled app sends a signed-out browser that asks for /."""
    r = browser.get("/", follow_redirects=False)
    assert r.status_code == 303, (r.status_code, r.text[:200])
    target = urlsplit(r.headers["location"])
    assert target.netloc in ("", HOST) and not target.query
    return target.path


def fresh(app) -> TestClient:
    """Another browser on an app whose first browser already started it."""
    return TestClient(app, base_url=BASE)


# 1. The crawl: everything a signed-out browser can receive


def static_files() -> list[str]:
    """The files the package ships under static/, and one that does not exist."""
    found: list[str] = []

    def walk(node, prefix: str) -> None:
        for child in node.iterdir():
            if child.is_dir():
                walk(child, f"{prefix}{child.name}/")
            else:
                found.append(prefix + child.name)

    walk(importlib.resources.files(NAME) / "static", "")
    return [*sorted(found), "no-such-file.css"]


def routes_of(routes, prefix: str = "") -> list:
    """Every route, a mount's own routes included (a mount serving files is one route)."""
    found = []
    for route in routes:
        inner = getattr(route, "routes", None)
        if inner:
            found += routes_of(inner, prefix + route.path)
        else:
            found.append(route if not prefix else _Prefixed(prefix + route.path_format))
    return found


class _Prefixed:
    def __init__(self, path_format: str) -> None:
        self.path_format = path_format


def addresses(app, params: dict[str, list[str]]) -> list[str]:
    """Every address of the app's own route table (mounts included), each path parameter
    filled from `params`, plus addresses no route serves and the come-back queries."""
    found = []
    for route in routes_of(app.routes):
        template = route.path_format
        names = re.findall(r"\{(\w+)(?::\w+)?\}", template)
        unknown = [name for name in names if name not in params]
        if unknown:
            pytest.fail(
                f"the route {template} has a parameter {unknown[0]!r} that the crawl has no"
                " values for: add plausible ones to PLAIN_PARAMS"
            )
        for values in itertools.product(*(params[name] for name in names)):
            path = template
            for name, value in zip(names, values, strict=True):
                pattern = r"\{" + name + r"(?::\w+)?\}"
                path = re.sub(pattern, lambda _, v=value: v, path, count=1)
            found.append(path)
    found += ["/no-such-page", "/.well-known/no-such-thing", "/api/no-such-thing"]
    found += [f"{path}?{q}" for path, queries in QUERIES.items() for q in queries]
    return list(dict.fromkeys(found))


def comes_back(path: str) -> bool:
    """The addresses worth coming back to after signing in (today's rule)."""
    return path in ("/new", "/oauth/consent") or path.startswith("/join/")


def kind_of(path: str, signin: str, well_known: frozenset[str] = frozenset()) -> str:
    """What an address is to the wall. `well_known`: the /.well-known/ addresses the app
    serves (its OAuth metadata); any other is a path that does not exist, as any other."""
    if (
        path in ("/api", "/mcp")
        or path.startswith(("/api/", "/mcp/"))
        or path in well_known
        or path in MACHINE
    ):
        return "machine"
    if path.startswith("/static/"):
        return "static"
    if path == signin or path.startswith("/auth/"):
        return "signin"
    return "page"


def own_words(world: World) -> dict[str, set[str]]:
    """The words of the pages a walled browser must not be shown, as an app with none of the
    settings shows them: each segment of their text the sign-in page does not also have."""
    plain = fresh(world.app())
    signin = set(Page(plain.get("/login").text).texts)
    pages = {
        "landing page": plain.get("/"),
        "guide": plain.get("/docs/connect"),
        "guides' index": plain.get("/docs"),
        "not-found page": plain.get("/no-such-page"),
        "key-login page": fresh(create_app(world.store)).get("/login"),  # no sign-in: a key
    }
    assert [r.status_code for r in pages.values()] == [200, 200, 200, 404, 200]
    # Any error page's frame is not the not-found page's own: a machine's address may still
    # answer with an error page (POST /healthz, 405), as it does without the wall.
    frame = set(Page(plain.post("/healthz").text).texts)
    return {
        what: {text for text in Page(r.text).texts if len(text) >= OWN_WORDS_MIN}
        - signin
        - (frame if what == "not-found page" else set())
        for what, r in pages.items()
    }


class Crawl:
    """Requests as a signed-out browser, following nothing, each response checked."""

    def __init__(self, browser, *, signin, walled, banned=None, control=None) -> None:
        self.browser, self.signin, self.walled = browser, signin, walled
        self.banned = banned or {}
        self.control = control
        self.seen: dict[tuple[str, str, bool], httpx.Response] = {}
        self.well_known: frozenset[str] = frozenset()
        self.queue: list[str] = []

    def request(self, method: str, address: str, cross: bool = False) -> httpx.Response:
        headers = CROSS_SITE if cross else {}
        r = self.browser.request(method, address, headers=headers, follow_redirects=False)
        self.seen[(method, address, cross)] = r
        self.check(r, method, address, cross)
        return r

    def check(self, r: httpx.Response, method: str, address: str, cross: bool) -> None:
        what = f"{method} {address}" + (" from another site" if cross else "")
        no_name_in(r, what)
        shown = r.content.decode("utf-8", "replace")
        text = shown if "html" in r.headers.get("content-type", "") else ""  # pages only
        for page, words in self.banned.items():
            for words_of_it in words:
                assert words_of_it not in text, f"{what} shows the {page}: {words_of_it!r}"
        if self.walled:
            path, _, q = address.partition("?")
            kind = kind_of(path, self.signin, self.well_known)
            self.expect(kind, r, method, path, q, cross, what)
            # Not for search engines: every answer for a page address, the sign-in page's
            # included, and every page the sign-in addresses answer with.
            if kind == "page" or path == self.signin or (kind == "signin" and text):
                robots = r.headers.get("x-robots-tag", "").lower()
                assert "noindex" in robots, f"{what}: no X-Robots-Tag: noindex ({r.status_code})"
        self.collect(r, address)

    def redirected(self, r: httpx.Response, path: str, q: str, what: str) -> None:
        assert r.status_code == 303, f"{what}: {r.status_code}"
        location = r.headers["location"]
        target = urlsplit(location)
        assert target.netloc in ("", HOST) and target.path == self.signin, f"{what}: {location}"
        carried = parse_qs(target.query)
        if comes_back(path):
            assert carried == {"next": [path + (f"?{q}" if q else "")]}, f"{what}: {location}"
        else:
            assert "next" not in carried, f"{what}: {location}"
            if path != "/":
                assert path not in unquote(target.query), f"{what} echoes its path: {location}"

    def expect(self, kind, r, method, path, q, cross, what) -> None:
        if kind == "machine":
            assert self.control is not None
            headers = CROSS_SITE if cross else {}
            address = path + (f"?{q}" if q else "")
            other = self.control.request(method, address, headers=headers, follow_redirects=False)
            no_name_in(other, f"{what}, without the wall")
            assert (r.status_code, r.headers.get("content-type"), r.content) == (
                other.status_code,
                other.headers.get("content-type"),
                other.content,
            ), f"{what} answers otherwise than without the wall"
            if path.startswith(("/api", "/mcp")):
                assert r.status_code == 401, what
            return
        reads = method in ("GET", "HEAD")
        if kind == "page" and path.startswith("/.well-known/") and r.status_code in (404, 405):
            return  # an agent's probe of a missing address may get a 404 (not the page's words)
        if kind == "page" and reads:
            again = comes_back(path) and q and _PLAIN_QUERY.fullmatch(q)
            if cross and again:
                assert r.status_code == 200, f"{what}: {r.status_code}"  # come again
                if method == "GET":
                    assert refresh_target(r.text) == path + (f"?{q}" if q else ""), what
                    shown = Page(r.text)
                    assert not shown.named("nav", "footer", "form", "script"), what
            else:
                self.redirected(r, path, q, what)
        elif kind == "page":
            assert r.status_code in ((401, 403) if cross else (401,)), f"{what}: {r.status_code}"
            assert r.headers["content-type"].startswith("text/plain"), what
            assert 0 < len(r.content) <= 200, f"{what}: not a short refusal"
        elif reads and path == self.signin:
            assert r.status_code == 200, f"{what}: {r.status_code}"
        elif reads and r.status_code == 303:
            self.redirected(r, path, q, what)
        elif reads:
            allowed = (200, 404) if kind == "static" else (200, 302, 400, 403, 404, 429, 502)
            assert r.status_code in allowed, f"{what}: {r.status_code}"
        else:
            assert r.status_code in (401, 403, 404, 405), f"{what}: {r.status_code}"

    def collect(self, r: httpx.Response, address: str) -> None:
        if r.status_code != 200:
            return
        kind, path = r.headers.get("content-type", ""), address.partition("?")[0]
        if "html" in kind:
            refs = Page(r.text).refs()
        elif "css" in kind:
            refs = css_refs(r.text)
        elif "manifest" in kind or path.endswith(".webmanifest"):
            refs = manifest_refs(r.text)
        else:
            return
        for ref in refs:
            assert not names_software(ref), f"{address} refers to {ref}"
            found = local(ref, path)
            if found and ("GET", found, False) not in self.seen:
                self.queue.append(found)

    def follow_refs(self) -> None:
        while self.queue:
            address = self.queue.pop()
            if ("GET", address, False) not in self.seen:
                self.request("GET", address)

    def everything(self, app, params: dict[str, list[str]]) -> int:
        """GET, HEAD and POST of every address, from this site and from another."""
        listed = addresses(app, params)
        self.well_known = frozenset(
            route.path_format
            for route in app.routes
            if route.path_format.startswith("/.well-known/")
        )
        for address in listed:
            for method in ("GET", "HEAD", "POST"):
                for cross in (False, True):
                    self.request(method, address, cross)
        self.follow_refs()
        return len(listed)


def crawl_params(world: World) -> dict[str, list[str]]:
    """PLAIN_PARAMS, an open invitation and the shipped static files."""
    other = world.store.create_account("Brook's team")
    invite = world.store.for_account(other.id).invite(DEE)
    return {**PLAIN_PARAMS, "token": [invite.token, "no-such-invitation"], "path": static_files()}


def test_a_signed_out_browser_reaches_nothing_but_the_sign_in_page(make):
    world = make()
    banned = own_words(world)
    seeded = world.store.sign_in(  # Cy is known under another issuer: a changed provider
        "github", str(CY_GITHUB["id"]), CY, "Cy", issuer="https://github.example.test",
        join="personal",
    )  # fmt: skip
    world.store.end_session(seeded.token)
    app = world.app(wall=True, **world.branded())
    control = world.app(**world.branded())  # the same brand, without the wall
    browser, control_browser = world.browser(app), world.browser(control)
    signin = sign_in_address(browser)
    params = crawl_params(world)
    before = dump(world.db)

    crawl = Crawl(browser, signin=signin, walled=True, banned=banned, control=control_browser)
    crawled = crawl.everything(app, params)

    assert crawled > 60  # the route table was walked, not a list kept by hand
    assert dump(world.db) == before  # nothing a signed-out browser sent wrote anything
    # The sign-in page and everything it needs: the stylesheet, its fonts, the logo.
    page = crawl.seen[("GET", signin, False)]
    assert page.status_code == 200
    needed = [local(ref, signin) for ref in Page(page.text).refs()]
    sheets = [a["href"] for a in Page(page.text).named("link") if "stylesheet" in a.get("rel", "")]
    assert len(sheets) == 1 and all(needed)
    for address in needed:
        r = crawl.seen[("GET", address, False)]
        assert r.status_code in (200, 302), (address, r.status_code)  # 302: to the provider
    sheet = crawl.seen[("GET", local(sheets[0], signin), False)]
    assert sheet.headers["content-type"].startswith("text/css")
    fonts = [local(ref, local(sheets[0], signin)) for ref in css_refs(sheet.text)]
    assert fonts and all(crawl.seen[("GET", font, False)].status_code == 200 for font in fonts)

    # Sign-ins that fail, are cancelled or refused, and one that succeeds: each a response
    # to a browser that is not signed in.
    failed = fresh(app)
    sent = start_sign_in(failed)
    r = failed.get(f"/auth/github/callback?code=no-such-code&state={sent['state']}")
    assert r.status_code >= 400
    crawl.check(r, "GET", "/auth/github/callback", False)
    cancelled = fresh(app)
    sent = start_sign_in(cancelled)
    r = cancelled.get(
        f"/auth/github/callback?error=access_denied&state={sent['state']}", follow_redirects=False
    )
    crawl.check(r, "GET", "/auth/github/callback", False)
    assert r.status_code == 303
    told = cancelled.get(r.headers["location"], follow_redirects=False)
    assert told.status_code == 200
    crawl.check(told, "GET", r.headers["location"], False)
    for profile in (BROOK_GITHUB, CY_GITHUB):  # not on the list; a provider that changed
        r = sign_in(world, fresh(app), profile)
        assert r.status_code == 403, profile["email"]
        crawl.check(r, "GET", "/auth/github/callback", False)
    r = sign_in(world, fresh(app), ADA_GITHUB)
    assert r.status_code == 200
    crawl.check(r, "GET", "/auth/github/callback", False)  # the cookie's name included
    crawl.follow_refs()


def test_with_a_brand_and_no_wall_nothing_signed_out_names_the_software(make):
    world = make()
    landing = own_words(world)["landing page"]
    app = world.app(**world.branded())
    browser = world.browser(app)
    crawl = Crawl(browser, signin="/login", walled=False)

    crawl.everything(app, crawl_params(world))

    home = crawl.seen[("GET", "/", False)]
    shown = home.content.decode("utf-8", "replace")
    assert not [words for words in landing if words in shown], "/ is the landing page"
    for profile in (BROOK_GITHUB, ADA_GITHUB):
        r = sign_in(world, fresh(app), profile)
        crawl.check(r, "GET", "/auth/github/callback", False)
    crawl.follow_refs()


@pytest.mark.parametrize("provider", ["google", "fieldid"])
def test_through_google_and_openid_connect_too_a_sign_in_names_nothing(make, provider):
    world = make()
    banned = own_words(world)
    cy = as_profile(CY_GITHUB, provider)  # known under another issuer: a changed provider
    seeded = world.store.sign_in(provider, cy["sub"], CY, "Cy", join="personal",
                                 issuer="https://elsewhere.example.test")  # fmt: skip
    world.store.end_session(seeded.token)
    app = world.app(providers=("google", "fieldid"), wall=True, **world.branded())
    browser = world.browser(app)
    signin = sign_in_address(browser)
    crawl = Crawl(browser, signin=signin, walled=True, banned=banned)
    callback = f"/auth/{provider}/callback"
    before = dump(world.db)

    failed = fresh(app)
    sent = start_sign_in(failed, provider=provider)
    r = failed.get(f"{callback}?code=no-such-code&state={sent['state']}", follow_redirects=False)
    assert r.status_code >= 400
    crawl.check(r, "GET", callback, False)
    cancelled = fresh(app)
    sent = start_sign_in(cancelled, provider=provider)
    r = cancelled.get(
        f"{callback}?error=access_denied&state={sent['state']}", follow_redirects=False
    )
    crawl.check(r, "GET", callback, False)
    assert r.status_code == 303
    told = cancelled.get(r.headers["location"], follow_redirects=False)
    assert told.status_code == 200
    crawl.check(told, "GET", r.headers["location"], False)
    # Not on the list; an address the provider has not verified; a provider that changed.
    for profile, verified in ((BROOK_GITHUB, True), (ADA_GITHUB, False), (CY_GITHUB, True)):
        r = sign_in(world, fresh(app), profile, provider=provider, verified=verified)
        assert r.status_code == 403, (profile["email"], verified, r.status_code)
        crawl.check(r, "GET", callback, False)
        assert Page(r.text).title == BRAND
    assert dump(world.db) == before
    r = sign_in(world, fresh(app), ADA_GITHUB, provider=provider)
    assert r.status_code == 200
    crawl.check(r, "GET", callback, False)  # the cookie's name included
    assert session_set(r)
    crawl.follow_refs()


# 2. The sign-in page


def stylesheet_of(page: Page) -> str:
    sheets = [a["href"] for a in page.named("link") if "stylesheet" in a.get("rel", "").split()]
    assert len(sheets) == 1
    return sheets[0]


def refusal_sentence(world: World) -> str:
    """What a refused sign-in adds to the sign-in page of an app with none of the settings."""
    plain = world.app(listed=(ADA,))
    shown = set(Page(fresh(plain).get("/login").text).texts)
    refused = sign_in(world, fresh(plain), BROOK_GITHUB)
    assert refused.status_code == 403
    added = [text for text in Page(refused.text).texts if text not in shown]
    assert len(added) == 1
    return added[0].removeprefix("Refused.").strip()


def test_the_sign_in_page_shows_the_brand_and_nothing_else(make):
    world = make()
    sentence = refusal_sentence(world)
    app = world.app(wall=True, **world.branded())
    browser = world.browser(app)
    signin = sign_in_address(browser)
    r = browser.get(signin)

    assert r.status_code == 200
    page = Page(r.text)
    assert page.title == BRAND and page.h1 == [BRAND]
    images = page.named("img")
    assert len(images) == 1 and images[0].get("alt") == ALT
    logo = images[0]["src"]
    assert not logo.startswith("data:") and "<svg" not in r.text.lower()
    assert [a["href"] for a in page.anchors] == ["/auth/github"]
    assert ["".join(a["text"]).strip() for a in page.anchors] == ["Continue with GitHub"]
    assert not page.named("nav", "footer", "form", "input", "button", "script", "iframe")
    assert set(page.texts) == {BRAND, "Continue with GitHub"}
    assert cairn.__version__ not in r.text
    words = " ".join(page.texts).lower()
    assert "session" not in words and "password" not in words
    icons = [a["href"] for a in page.named("link") if "icon" in a.get("rel", "").split()]
    assert set(icons) <= {logo}  # the logo, or no icon at all
    # Its stylesheet keeps the product's design: every token, in light and in dark.
    sheet = browser.get(local(stylesheet_of(page), signin)).text
    plain = fresh(world.app())
    plain_sheet = plain.get(stylesheet_of(Page(plain.get("/login").text))).text
    tokens = set(re.findall(r"(--[\w-]+)\s*:\s*([^;}]+)", plain_sheet))
    assert tokens and tokens <= set(re.findall(r"(--[\w-]+)\s*:\s*([^;}]+)", sheet))
    assert "prefers-color-scheme: dark" in sheet

    # A refused sign-in adds its sentence and nothing else.
    refused = sign_in(world, fresh(app), BROOK_GITHUB)
    assert refused.status_code == 403
    shown = Page(refused.text)
    added = [text for text in shown.texts if text not in page.texts]
    assert len(added) == 1 and sentence in added[0]
    assert shown.title == BRAND and shown.h1 == [BRAND]
    assert [a["href"] for a in shown.anchors] == ["/auth/github"]


def test_without_a_logo_the_sign_in_page_has_the_name_and_no_image(make):
    world = make()
    browser = world.browser(world.app(wall=True, brand_name=BRAND))
    page = Page(browser.get(sign_in_address(browser)).text)
    assert page.title == BRAND and page.h1 == [BRAND]
    assert not page.named("img")
    assert not [a for a in page.named("link") if "icon" in a.get("rel", "").split()]
    assert set(page.texts) == {BRAND, "Continue with GitHub"}
    # A logo given no text alternative says the brand name.
    untold = world.app(wall=True, brand_name=BRAND, brand_logo=world.logo())
    images = Page(fresh(untold).get(sign_in_address(fresh(untold))).text).named("img")
    assert [image.get("alt") for image in images] == [BRAND]


def test_with_two_providers_the_sign_in_page_has_one_link_each_and_nothing_else(make):
    world = make()
    app = world.app(providers=("google", "fieldid"), wall=True, **world.branded())
    browser = world.browser(app)
    page = Page(browser.get(sign_in_address(browser)).text)
    labels = ["Continue with Google", f"Continue with {OIDC_LABEL}"]
    assert page.title == BRAND and page.h1 == [BRAND]
    assert [a["href"] for a in page.anchors] == ["/auth/google", "/auth/fieldid"]
    assert ["".join(a["text"]).strip() for a in page.anchors] == labels
    assert [image.get("alt") for image in page.named("img")] == [ALT]
    assert not page.named("nav", "footer", "form", "input", "button", "script", "iframe")
    assert set(page.texts) == {BRAND, *labels}


def test_too_many_sign_ins_from_one_address_get_the_sign_in_page_and_one_sentence(make):
    world = make()
    app = world.app(wall=True, **world.branded())
    browser = world.browser(app)
    signin = sign_in_address(browser)
    page = Page(browser.get(signin).text)
    crawl = Crawl(browser, signin=signin, walled=True, banned=own_words(world))
    for address, allowed in (
        ("/auth/github", 302),  # starts
        ("/auth/github/callback?code=no-such-code&state=no-such-state", 400),  # callbacks
    ):
        for _ in range(100):
            r = browser.get(address, follow_redirects=False)
            if r.status_code == 429:
                break
            assert r.status_code == allowed, (address, r.status_code)
        else:
            pytest.fail(f"{address}: no limit within 100 requests")
        crawl.check(r, "GET", address, False)
        busy = Page(r.text)
        assert busy.title == BRAND and busy.h1 == [BRAND], address
        added = [text for text in busy.texts if text not in page.texts]
        assert len(added) == 1 and set(page.texts) <= set(busy.texts), (address, busy.texts)
        assert [a["href"] for a in busy.anchors] == [a["href"] for a in page.anchors]
        assert busy.named("img") == page.named("img")
        assert not busy.named("nav", "footer", "form", "input", "button", "script", "iframe")
    crawl.follow_refs()


# 3. The logo's address


def csp(value: str) -> dict[str, list[str]]:
    parts = [part.split() for part in value.split(";") if part.strip()]
    return {part[0].lower(): part[1:] for part in parts}


@pytest.mark.parametrize(
    ("content", "file_name", "media_type"),
    [
        (SVG_LOGO, "logo.svg", "image/svg+xml"),
        (SCRIPTED_LOGO, "badge.png", "image/svg+xml"),  # told by its content, not its name
        (png(), "badge.svg", "image/png"),
    ],
    ids=["svg", "svg-with-a-script-named-png", "png-named-svg"],
)
def test_the_logo_is_served_from_one_address_as_an_image(make, content, file_name, media_type):
    world = make()
    app = world.app(wall=True, **world.branded(content, file_name))
    browser = world.browser(app)
    signin = sign_in_address(browser)
    r = browser.get(signin)
    page = Page(r.text)
    logo = page.named("img")[0]["src"]
    # The page only refers to it as an image (and maybe its icon), never inlines it.
    for tag, attributes in page.tags:
        if logo in attributes.values():
            assert tag == "img" or (tag == "link" and "icon" in attributes.get("rel", "")), tag
    assert not page.named("object", "embed", "iframe", "svg", "script")
    assert not [ref for ref in page.refs() if ref.startswith("data:")]

    served = fresh(app).get(local(logo, signin), follow_redirects=False)  # signed out

    assert served.status_code == 200 and served.content == content
    assert served.headers["content-type"].split(";")[0].strip() == media_type
    assert served.headers["x-content-type-options"] == "nosniff"
    if media_type == "image/svg+xml":
        policy = csp(served.headers["content-security-policy"])
        assert policy["default-src"] == ["'none'"]
        for directive, sources in policy.items():
            if "script" in directive or directive in (
                "connect-src", "frame-src", "child-src", "object-src", "worker-src",
            ):  # fmt: skip
                assert sources == ["'none'"], directive
            assert "'unsafe-eval'" not in sources and "*" not in sources, directive


# 4. Signed in


def test_signed_in_the_frame_carries_the_brand_and_the_wall_changes_nothing(make):
    world = make()
    settings = world.branded()
    walled, open_app = world.app(wall=True, **settings), world.app(**settings)
    browser = world.browser(walled)
    signin = sign_in_address(browser)
    logo = Page(fresh(walled).get(signin).text).named("img")[0]["src"]
    cookie, token = signed_in(world, browser, ADA_GITHUB)
    principal = world.store.resolve_session(token)
    ada = world.store.for_account(principal.account_id, principal.user_id)
    ada.create_project("core", "Ada's notes")
    other = world.browser(open_app)
    other.cookies.set(cookie, token)
    params = {**PLAIN_PARAMS, "token": ["no-such-invitation"], "path": ["no-such-file.css"]}
    pages = [
        address
        for address in addresses(walled, params)
        if kind_of(address.partition("?")[0], signin) == "page"
    ]
    checked = 0
    for address in pages:
        r = browser.get(address, follow_redirects=False)
        without = other.get(address, follow_redirects=False)
        assert (r.status_code, r.content) == (without.status_code, without.content), address
        if r.status_code == 405 or "html" not in r.headers.get("content-type", ""):
            continue
        page = Page(r.text)
        assert BRAND in page.title, (address, page.title)
        frame = page.title.replace(page.h1[0], "") if page.h1 else page.title
        assert not names_software(frame), (address, page.title)
        home = [a for a in page.anchors if a["href"] == "/" and BRAND in "".join(a["text"])]
        assert home and [img["src"] for img in home[0]["imgs"]] == [logo], address
        checked += 1
    assert checked > 20

    # Signing out lands on the sign-in page, and the session is over at once.
    out = browser.post("/logout", headers=SAME_ORIGIN, follow_redirects=False)
    assert out.status_code == 303 and urlsplit(out.headers["location"]).path == signin
    assert world.store.resolve_session(token) is None
    browser.cookies.set(cookie, token)
    assert browser.get("/", follow_redirects=False).status_code == 303


# 5. Machines are not behind the wall


def same(one: httpx.Response, other: httpx.Response) -> bool:
    keep = ("content-type", "www-authenticate", "location", "allow")
    return (one.status_code, one.content, [one.headers.get(h) for h in keep]) == (
        other.status_code,
        other.content,
        [other.headers.get(h) for h in keep],
    )


MCP_CALL = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "projects", "arguments": {}}}  # fmt: skip
MACHINE_REQUESTS = [
    ("GET", "/healthz", {}, {}),
    ("GET", "/api/projects", {}, {}),
    ("POST", "/api/projects", {}, {"json": {"name": "core"}}),
    ("GET", "/api/search?q=retry", {}, {}),
    ("POST", "/mcp", MCP_HEADERS, {"json": MCP_CALL}),
    ("POST", "/mcp", {**MCP_HEADERS, "authorization": "Bearer not-a-token"}, {"json": MCP_CALL}),
    ("GET", "/.well-known/oauth-authorization-server", {}, {}),
    ("GET", "/.well-known/oauth-protected-resource/mcp", {}, {}),
    ("POST", "/register", {}, {"json": {"client_name": "x"}}),
    ("POST", "/token", {}, {"data": {"grant_type": "authorization_code", "code": "nope"}}),
    ("POST", "/revoke", {}, {"data": {"token": "nope"}}),
    ("GET", "/authorize?client_id=nope", {}, {}),
    # Refusals that depend on what was sent: another grant, another scope.
    (
        "POST",
        "/register",
        {},
        {
            "json": {
                "client_name": "x",
                "redirect_uris": [REDIRECT],
                "grant_types": ["client_credentials"],
            }
        },
    ),
    ("POST", "/token", {}, {"data": {"grant_type": "client_credentials", "scope": "everything"}}),
    (
        "POST",
        "/token",
        {},
        {"data": {"grant_type": "refresh_token", "refresh_token": "nope", "scope": "everything"}},
    ),
]


def test_machines_are_not_behind_the_wall(make):
    world = make()
    before = dump(world.db)
    apps = [
        world.app(),
        world.app(wall=True),
        world.app(**world.branded()),
        world.app(wall=True, **world.branded()),
    ]
    clients = [world.browser(app) for app in apps]
    for method, address, headers, body in MACHINE_REQUESTS:
        answers = [
            c.request(method, address, headers=headers, follow_redirects=False, **body)
            for c in clients
        ]
        plain, walled, branded, both = answers
        assert same(plain, walled), (method, address, walled.status_code)
        assert same(branded, both), (method, address, both.status_code)
        for answer in (branded, both):
            no_name_in(answer, f"{method} {address} under the brand")
        if address.startswith(("/api", "/mcp")):
            assert both.status_code == 401 and both.headers["content-type"].startswith(
                "application/json"
            ), address
    metadata = clients[3].get("/.well-known/oauth-protected-resource/mcp").json()
    assert metadata["scopes_supported"]  # a scope still, by another name
    assert dump(world.db) == before


# 6. An agent connects through the wall


def register(browser: TestClient, name: str, heard=None) -> str:
    r = browser.post(
        "/register",
        json={
            "client_name": name,
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )
    assert r.status_code == 201, r.text
    if heard:
        heard(r, "/register")
    return r.json()["client_id"]


def scopes_of(browser: TestClient) -> list[str]:
    return browser.get("/.well-known/oauth-protected-resource/mcp").json()["scopes_supported"]


def authorize(
    browser: TestClient, client_id: str, scopes: list[str] | None = None, heard=None
) -> tuple[str, str]:
    """The agent's authorization request, opened in the browser: the verifier, and the
    consent page's address it leads to. `scopes`: what it asks for (the metadata's when not
    given); `heard`: called with the answer."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    scopes = scopes_of(browser) if scopes is None else scopes
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "agent-state",
        "resource": f"{BASE}/mcp",
        "scope": " ".join(scopes),
    }
    r = browser.get("/authorize?" + urlencode(params), follow_redirects=False)
    assert r.status_code == 302, r.text
    if heard:
        heard(r, "/authorize")
    return verifier, local(r.headers["location"], "/authorize")


def approve(
    browser: TestClient, consent: httpx.Response, client_id: str, verifier: str, whole=False
):
    """Approve on the consent page; the agent's access token (`whole`: the token answer)."""
    assert consent.status_code == 200, consent.status_code
    fields = hidden_fields(consent.text)
    assert fields, "the consent form's hidden fields were not found"
    decided = browser.post(
        "/oauth/consent",
        data={**fields, "decision": "approve"},
        headers=SAME_ORIGIN,
        follow_redirects=False,
    )
    assert decided.status_code == 303, decided.text
    token = browser.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": query(decided.headers["location"])["code"],
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200, token.text
    return token.json() if whole else token.json()["access_token"]


def projects_over_mcp(app, access: str, heard=None) -> list[str]:
    answer = fresh(app).post(
        "/mcp", headers={**MCP_HEADERS, "authorization": f"Bearer {access}"}, json=MCP_CALL
    )
    if heard:
        heard(answer, "/mcp")
    assert answer.status_code == 200, answer.text
    listed = json.loads(answer.json()["result"]["content"][0]["text"])["projects"]
    return [project["name"] for project in listed]


def test_an_agent_connects_through_the_wall_from_a_browser_that_never_signed_in(make):
    world = make()
    app = world.app(wall=True, session_days=30, **world.branded())
    first = world.browser(app)  # Ada has signed in before, elsewhere: this is not her first time
    _, token = signed_in(world, first, ADA_GITHUB)
    principal = world.store.resolve_session(token)
    world.store.for_account(principal.account_id).create_project("core", "Ada's notes")
    signin = sign_in_address(fresh(app))
    cold = fresh(app)

    agent = register(cold, "first-agent")
    verifier, consent = authorize(cold, agent)
    r = cold.get(consent, follow_redirects=False)
    assert r.status_code == 303
    to_sign_in = r.headers["location"]
    assert urlsplit(to_sign_in).path == signin and query(to_sign_in) == {"next": consent}
    page = cold.get(to_sign_in)
    assert page.status_code == 200
    started = cold.get(Page(page.text).anchors[0]["href"], follow_redirects=False)
    assert started.status_code == 302
    code = world.idp.approve(ADA_GITHUB)
    back = cold.get(
        f"/auth/github/callback?code={code}&state={query(started.headers['location'])['state']}",
        follow_redirects=False,
    )
    assert back.status_code == 200
    assert local(refresh_target(back.text), "/auth/github/callback") == consent  # `next` lived
    access = approve(cold, cold.get(consent), agent, verifier)
    assert projects_over_mcp(app, access) == ["core"]

    # A second agent on the same browser goes straight to the consent page.
    second = register(cold, "second-agent")
    verifier, consent = authorize(cold, second)
    page = cold.get(consent, follow_redirects=False)
    assert page.status_code == 200
    assert projects_over_mcp(app, approve(cold, page, second, verifier)) == ["core"]

    # A token or a key opens /api and /mcp, and no page.
    ada = world.store.for_account(principal.account_id, principal.user_id)
    key = ada.create_key("laptop")
    machine = fresh(app)
    for credential in ({"authorization": f"Bearer {access}"}, {"x-api-key": key}):
        for path in ("/", "/keys", "/docs"):
            r = machine.get(path, headers=credential, follow_redirects=False)
            assert r.status_code == 303 and urlsplit(r.headers["location"]).path == signin
    assert machine.get("/api/projects", headers={"x-api-key": key}).status_code == 200


def refresh(client: TestClient, client_id: str, token: str, scope: str | None):
    data = {"grant_type": "refresh_token", "refresh_token": token, "client_id": client_id}
    return client.post("/token", data={**data, **({"scope": scope} if scope else {})})


@pytest.mark.parametrize("branded_first", [False, True], ids=["brand-set", "brand-removed"])
def test_an_agent_outlives_the_brand_being_set_or_removed(make, branded_first):
    world = make()
    plain, branded = world.app(), world.app(wall=True, **world.branded())
    before, after = (branded, plain) if branded_first else (plain, branded)

    def heard_on(app):
        """Under the brand, what a party that is not signed in hears names nothing."""

        def heard(r: httpx.Response, what: str) -> None:
            if app is branded:
                no_name_in(r, f"{what} under the brand")

        return heard

    first = world.browser(before)
    old = scopes_of(first)
    _, token = signed_in(world, first, ADA_GITHUB)
    principal = world.store.resolve_session(token)
    world.store.for_account(principal.account_id).create_project("core", "Ada's notes")
    agent = register(first, "an-agent", heard_on(before))
    verifier, consent = authorize(first, agent, heard=heard_on(before))
    pair = approve(first, first.get(consent), agent, verifier, whole=True)
    assert pair["scope"] == old[0]

    # The server starts again on the same database, the brand set or removed.
    later = world.browser(after)
    new = scopes_of(later)
    assert len(old) == len(new) == 1 and old != new  # the metadata says only its current name
    assert "elsewhere" not in old + new

    heard = heard_on(after)
    signed_out = fresh(after)
    assert projects_over_mcp(after, pair["access_token"], heard) == ["core"]  # the access token
    for app, client in ((before, fresh(before)), (after, signed_out)):
        # A refresh asking for a scope that is neither name is refused, in words that name
        # nothing under the brand, and the token is kept.
        r = refresh(client, agent, pair["refresh_token"], "elsewhere")
        assert r.status_code == 400 and r.json()["error"] == "invalid_scope", r.text
        heard_on(app)(r, "a refused refresh")
    for asked in (old[0], new[0], None):  # the refresh token, asking either name, or none
        r = refresh(signed_out, agent, pair["refresh_token"], asked)
        assert r.status_code == 200, (asked, r.text)  # (the token answer itself is the agent's)
        pair = r.json()
        assert pair["scope"] == new[0], asked
        assert projects_over_mcp(after, pair["access_token"], heard) == ["core"]
    # A new authorization by the same registered client, asking either name.
    signed_in(world, later, ADA_GITHUB)
    for asked in (new, old):
        verifier, consent = authorize(later, agent, asked, heard)
        page = later.get(consent, follow_redirects=False)
        assert page.status_code == 200, asked
        got = approve(later, page, agent, verifier, whole=True)
        assert got["scope"] == new[0], asked
        assert projects_over_mcp(after, got["access_token"], heard) == ["core"]


# 7. The list still decides who signs in, and nothing else


def test_the_list_still_gates_sign_in_under_the_wall_and_the_brand(make):
    world = make(listed=(ADA, BROOK))
    sentence = refusal_sentence(world)
    settings = {"wall": True, "session_days": 30, **world.branded()}
    browser = world.browser(world.app(**settings))
    cookie, token = signed_in(world, browser, BROOK_GITHUB)
    # The server starts again with a shorter list, on the same database.
    later = world.app(listed=(ADA,), **settings)
    again = world.browser(later)
    again.cookies.set(cookie, token)
    home = again.get("/", follow_redirects=False)
    assert home.status_code == 200 and "Brook Ng" in home.text  # still open, still Brook
    stranger = fresh(later)
    before = dump(world.db)
    r = sign_in(world, stranger, BROOK_GITHUB)
    assert r.status_code == 403 and dump(world.db) == before
    assert not [c for c, (value, a) in set_cookies(r).items() if value and a.get("path") == "/"]
    no_name_in(r, "a refused sign-in")
    assert sentence in r.text and BROOK not in r.text and ADA not in r.text
    assert Page(r.text).title == BRAND


# 8. A sign-in that is remembered

N = 30  # days


def test_a_sign_in_lasts_n_days_and_use_renews_it(make):
    world = make()
    app = world.app(wall=True, session_days=N, **world.branded())
    browser = world.browser(app)
    signin = sign_in_address(fresh(app))
    r = sign_in(world, browser, ADA_GITHUB)
    assert r.status_code == 200
    cookie, token, made = session_set(r)
    assert int(made["max-age"]) == N * 86_400
    assert made["httponly"] and made["secure"] and made["samesite"].lower() == "strict"
    signed = world.now
    assert world.alive(token, signed + N * DAY - MINUTE)
    assert not world.alive(token, signed + N * DAY + MINUTE)

    # Within a day of the last renewal: nothing changes and no cookie is sent.
    world.now = signed + HOUR
    before = dump(world.db)
    r = browser.get("/", follow_redirects=False)
    assert r.status_code == 200 and cookie not in set_cookies(r)
    assert dump(world.db) == before
    assert not world.alive(token, signed + N * DAY + MINUTE)

    # More than a day after: the stored session and the cookie both last N days from now.
    world.now = signed + 25 * HOUR
    r = browser.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert cookie in set_cookies(r), "the renewed cookie was not sent again"
    token, renewed = set_cookies(r)[cookie]
    assert int(renewed["max-age"]) == N * 86_400
    assert {k: v for k, v in renewed.items() if k != "max-age"} == {
        k: v for k, v in made.items() if k != "max-age"
    }
    assert world.alive(token, world.now + N * DAY - MINUTE)
    assert not world.alive(token, world.now + N * DAY + MINUTE)
    # An hour after that renewal: a day is counted from it, not from the sign-in.
    world.now += HOUR
    before = dump(world.db)
    r = browser.get("/", follow_redirects=False)
    assert r.status_code == 200 and cookie not in set_cookies(r)
    assert dump(world.db) == before

    # Used every few days, the browser is still signed in after 3N days.
    while world.now < signed + 3 * N * DAY:
        world.now += 4 * DAY
        r = browser.get("/", follow_redirects=False)
        assert r.status_code == 200, world.now
    # Left alone for N days and a minute, it is sent to the sign-in page.
    world.now += N * DAY + MINUTE
    r = browser.get("/", follow_redirects=False)
    assert r.status_code == 303 and urlsplit(r.headers["location"]).path == signin


def test_signing_out_ends_a_remembered_session_at_once(make):
    world = make()
    app = world.app(wall=True, session_days=N, **world.branded())
    browser = world.browser(app)
    signin = sign_in_address(fresh(app))
    cookie, token = signed_in(world, browser, ADA_GITHUB)
    world.now += 2 * DAY
    assert browser.get("/", follow_redirects=False).status_code == 200  # renewed
    token = browser.cookies.get(cookie)
    out = browser.post("/logout", headers=SAME_ORIGIN, follow_redirects=False)
    assert out.status_code == 303 and urlsplit(out.headers["location"]).path == signin
    assert world.store.resolve_session(token) is None
    browser.cookies.set(cookie, token)
    assert browser.get("/", follow_redirects=False).status_code == 303


def test_without_the_setting_a_session_lasts_twelve_hours_and_is_never_renewed(make):
    world = make()
    app = world.app(wall=True, **world.branded())
    browser = world.browser(app)
    r = sign_in(world, browser, ADA_GITHUB)
    cookie, token, made = session_set(r)
    assert int(made["max-age"]) == TWELVE_HOURS
    signed = world.now
    assert world.alive(token, signed + 12 * HOUR - MINUTE)
    assert not world.alive(token, signed + 12 * HOUR + MINUTE)
    for hours in (1, 6, 11):
        world.now = signed + hours * HOUR
        before = dump(world.db)
        r = browser.get("/", follow_redirects=False)
        assert r.status_code == 200 and cookie not in set_cookies(r), hours
        assert dump(world.db) == before
    assert not world.alive(token, signed + 12 * HOUR + MINUTE)
    world.now = signed + 12 * HOUR + MINUTE
    assert browser.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("days", [N, None], ids=["n-days", "no-setting"])
def test_switching_workspace_and_taking_an_invitation_make_sessions_as_long(make, days):
    world = make(listed=(ADA, BROOK))
    remembered = {"session_days": days} if days else {}
    app = world.app(wall=True, **remembered, **world.branded())
    lasts = days * 86_400 if days else TWELVE_HOURS
    ada, brook = world.browser(app), fresh(app)
    _, token = signed_in(world, ada, ADA_GITHUB)
    home = world.store.resolve_session(token)
    _, token = signed_in(world, brook, BROOK_GITHUB)
    theirs = world.store.resolve_session(token)
    invite = world.store.for_account(theirs.account_id, theirs.user_id).invite(ADA)
    world.now += HOUR  # within a day of the sign-in: no renewal, nothing else sets a cookie

    for address, lands in (
        (f"/join/{invite.token}", theirs.account_id),
        (f"/workspaces/{home.account_id}/switch", home.account_id),
    ):
        r = ada.post(address, headers=SAME_ORIGIN, follow_redirects=False)
        assert r.status_code == 303, (address, r.status_code)
        _, session, made = session_set(r)
        assert int(made["max-age"]) == lasts, address
        assert world.store.resolve_session(session).account_id == lands, address
        assert world.alive(session, world.now + timedelta(seconds=lasts) - MINUTE), address
        assert not world.alive(session, world.now + timedelta(seconds=lasts) + MINUTE), address


# 9. session-end


def test_session_end_ends_every_web_session_of_one_person(tmp_path, capsys):
    db = tmp_path / "kb.db"
    store = Store(db)  # the real clock: the command opens the database with it
    try:
        ada = store.sign_in("github", "7001", ADA, "Ada Quill", issuer="https://github.com",
                            join="personal")  # fmt: skip
        brook = store.sign_in("github", "7002", BROOK, "Brook Ng", issuer="https://github.com",
                              join="personal")  # fmt: skip
        invite = store.for_account(brook.account_id, brook.user.id).invite(ADA)
        store.for_account(brook.account_id).accept_invite(invite.token, ada.user.id)
        there = store.for_account(brook.account_id, ada.user.id).create_session()
        key = store.for_account(ada.account_id, ada.user.id).create_key("laptop")
        assert store.resolve_session(there).account_id == brook.account_id
        capsys.readouterr()

        assert server.main(["session-end", "--db", str(db), "--email", ADA]) == 0

        said = capsys.readouterr()
        assert re.findall(r"\d+", said.out) == ["2"]
        assert "@" not in (said.out + said.err).lower().replace(ADA, "")
        assert store.resolve_session(ada.token) is None and store.resolve_session(there) is None
        assert store.resolve_session(brook.token) is not None  # nobody else's
        with TestClient(create_app(store), base_url=BASE) as machine:
            assert machine.get("/api/projects", headers={"x-api-key": key}).status_code == 200

        assert server.main(["session-end", "--db", str(db), "--email", CY]) == 0
        said = capsys.readouterr()
        assert re.findall(r"\d+", said.out) == ["0"]
        assert "@" not in (said.out + said.err).lower().replace(CY, "")
        assert store.resolve_session(brook.token) is not None
    finally:
        store.close()


# 10. The settings of serve

GITHUB_ENV = {
    "CAIRN_GITHUB_CLIENT_ID": "github-client-id",
    "CAIRN_GITHUB_CLIENT_SECRET": CLIENT_SECRET,
}


def serve(monkeypatch, tmp_path: Path, argv: list[str], *, sign_in: bool = True):
    """Run `serve` with uvicorn replaced: the exit code, the apps it would have run, the db."""
    monkeypatch.delenv("CAIRN_ALLOWED_EMAILS", raising=False)
    for name, value in GITHUB_ENV.items():
        monkeypatch.setenv(name, value)
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    db = tmp_path / "served" / "kb.db"
    db.parent.mkdir(exist_ok=True)
    providers = ["--sign-in", "github"] if sign_in else []
    try:
        code = server.main(["serve", "--db", str(db), "--public-url", BASE, *providers, *argv])
    except SystemExit as stopped:
        code = stopped.code
    return code, apps, db


@pytest.mark.parametrize(
    ("argv", "option"),
    [
        (["--brand-name", ""], "--brand-name"),
        (["--brand-name", "K" * 61], "--brand-name"),
        (["--brand-name", "Knowledge\x07Base"], "--brand-name"),
        (["--brand-name", "Knowledge\nBase"], "--brand-name"),
        (["--brand-name", "<b>Knowledge</b>"], "--brand-name"),
        (["--brand-name", "Base >"], "--brand-name"),
        (["--brand-name", "Knowledge < Base"], "--brand-name"),
        (["--brand-name", BRAND, "--brand-logo", "{missing}"], "--brand-logo"),
        (["--brand-name", BRAND, "--brand-logo", "{large}"], "--brand-logo"),
        (["--brand-name", BRAND, "--brand-logo", "{text}"], "--brand-logo"),
        (["--brand-logo", "{svg}"], "--brand-logo"),
        (["--session-days", "0"], "--session-days"),
        (["--session-days", "366"], "--session-days"),
        (["--session-days", "soon"], "--session-days"),
        (["--session-days", "7.5"], "--session-days"),
    ],
)
def test_serve_refuses_a_setting_it_cannot_use(tmp_path, monkeypatch, capsys, argv, option):
    files = {
        "{missing}": tmp_path / "no-such-logo.svg",
        "{large}": tmp_path / "large.svg",
        "{text}": tmp_path / "logo.svg",
        "{svg}": tmp_path / "fine.svg",
    }
    padding = b"<!--" + b"x" * (300 * 1024) + b"-->"
    files["{large}"].write_bytes(SVG_LOGO.replace(b"<text", padding + b"<text"))
    files["{text}"].write_bytes(b"A logo, they said. This is a line of text.\n")
    files["{svg}"].write_bytes(SVG_LOGO)
    argv = [str(files.get(value, value)) for value in argv]
    capsys.readouterr()

    code, apps, db = serve(monkeypatch, tmp_path, argv)

    err = capsys.readouterr().err
    assert code == 2 and apps == [] and not db.exists()
    assert option in err.strip().splitlines()[-1], err


def test_serve_refuses_a_wall_without_sign_in(tmp_path, monkeypatch, capsys):
    code, apps, db = serve(monkeypatch, tmp_path, ["--wall"], sign_in=False)
    err = capsys.readouterr().err
    assert code == 2 and apps == [] and not db.exists()
    assert "--wall" in err.strip().splitlines()[-1], err


@pytest.mark.parametrize(
    ("argv", "sign_in", "option"),
    [
        (["--brand-name", BRAND], False, "--brand-name"),
        (["--session-days", "30"], False, "--session-days"),
        (["--brand-name", BRAND, "--brand-logo-alt", ALT], True, "--brand-logo-alt"),
    ],
    ids=["brand-name-without-sign-in", "session-days-without-sign-in", "alt-without-logo"],
)
def test_serve_refuses_a_setting_that_would_do_nothing(
    tmp_path, monkeypatch, capsys, argv, sign_in, option
):
    capsys.readouterr()
    code, apps, db = serve(monkeypatch, tmp_path, argv, sign_in=sign_in)
    err = capsys.readouterr().err
    assert code == 2 and apps == [] and not db.exists()
    assert option in err.strip().splitlines()[-1], err


def test_serve_takes_the_settings(tmp_path, monkeypatch, capsys):
    logo = tmp_path / "mark.txt"  # told by its content
    logo.write_bytes(SVG_LOGO)
    argv = ["--wall", "--brand-name", BRAND, "--brand-logo", str(logo),
            "--brand-logo-alt", ALT, "--session-days", "30"]  # fmt: skip
    code, apps, _ = serve(monkeypatch, tmp_path, argv)
    assert code == 0 and len(apps) == 1
    browser = fresh(apps[0])  # no lifespan: serve has closed its store; no page here reads it
    signin = sign_in_address(browser)
    page = Page(browser.get(signin).text)
    assert page.title == BRAND and page.named("img")[0]["alt"] == ALT
    assert browser.get(local(page.named("img")[0]["src"], signin)).content == SVG_LOGO
    for edges in (
        ["--brand-name", "K", "--session-days", "1"],
        ["--brand-name", "K" * 60, "--session-days", "365"],
    ):
        assert serve(monkeypatch, tmp_path, edges)[0] == 0, edges
    capsys.readouterr()


# 11. With none of the settings, everything is as today


SNAPSHOT = ("/login", "/docs", "/docs/connect", "/", "/no-such-page", "/keys")


def snapshot(browser: TestClient) -> dict:
    taken = {}
    for address in SNAPSHOT:
        r = browser.get(address, follow_redirects=False)
        taken[address] = (r.status_code, r.headers.multi_items(), r.content)
    for ref in Page(browser.get("/login").text).refs():  # the stylesheet, the icon, the manifest
        if (found := local(ref, "/login")) and found.startswith("/static/"):
            r = browser.get(found, follow_redirects=False)
            taken[found] = (r.status_code, r.headers.multi_items(), r.content)
    return taken


def test_without_the_settings_every_byte_is_as_today(make):
    world = make()
    plain = world.app()
    defaults = world.app(
        wall=False, brand_name=None, brand_logo=None, brand_logo_alt=None, session_days=None
    )
    browser = world.browser(plain)
    signed_out = snapshot(fresh(plain))
    started = browser.get("/auth/github", follow_redirects=False)
    assert f"{NAME}_signin" in set_cookies(started)  # today's names
    r = sign_in(world, browser, ADA_GITHUB)
    cookie, token, made = session_set(r)
    assert cookie == f"{NAME}_session" and int(made["max-age"]) == TWELVE_HOURS
    signed = snapshot(browser)

    # An app with every setting, used in the same process, changes nothing for the others.
    other = world.app(wall=True, session_days=7, **world.branded())
    branded = world.browser(other)
    branded.get("/")
    branded.get(sign_in_address(branded))
    sign_in(world, fresh(other), ADA_GITHUB)

    assert snapshot(fresh(plain)) == signed_out
    assert snapshot(fresh(defaults)) == signed_out
    assert snapshot(browser) == signed
    with_defaults = fresh(defaults)
    with_defaults.cookies.set(cookie, token)
    assert snapshot(with_defaults) == signed
