"""B3 acceptance tests (frozen): under a brand, the signed-in product says the brand's name.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/web/brand.py, src/cairn/web/public.py and
src/cairn/web/mcp.py.

Why: B1 (frozen tests/acceptance/test_b1_wall.py) gave an install its operator's own name:
behind the wall nothing a signed-out browser can receive names the software, and for a
signed-in person the page's frame (the wordmark, the title, the footer) says the brand. What
the pages themselves said was left alone, so the first person to sign in under a brand was
asked to "Choose what you will use cairn for" and given a command that names their agent's
connection after the software, under a header that says the brand. Under a brand, the
signed-in product says the brand's name, to people and to their agents.

"The name" is the software's own name: these five letters in a row, in any case. "Under a
brand" is with B1's `brand_name` set, the wall on or off. What follows holds for a signed-in
person and for an agent that holds a key or a grant.

Pages
1. No page names the software in what it says. On every page a signed-in person can open
   (every route of the app by GET, as an owner and as a plain member; the guides, the error
   pages and the pages a form answers with included), the name is not in the visible text,
   the `<title>`, or an attribute a person meets: `alt`, `title`, `placeholder`,
   `aria-label`, a button's or an input's `value`, a `meta` description. Where a page said
   the name it says the brand's, as it is written, with a plain possessive.
2. Text to type keeps the software's identifiers and nothing else of its name. Inside `code`,
   `pre`, `kbd` and `samp` the name appears only inside one of IDENTIFIERS (below): the module
   in `python -m cairn.server`, an environment variable `CAIRN_...`, a file name the software
   chooses (`cairn.db`, `cairn.env`), the container image's name, a token's prefix.
3. A name the person chooses is the brand's slug (lower case, letters, digits and dashes:
   `knowledge-base` for "Knowledge Base"): the connection's name in every command or config
   that adds the server to an agent (a command line's `mcp add`, an editor's `--add-mcp`, a
   JSON or TOML config), on the welcome page, the setup pages and the connect guide. The address
   in those snippets on the app's pages is the install's own, unchanged.
4. No address a page refers to holds the name (links, images, scripts, stylesheets, form
   actions, a refresh). A file a signed-in person downloads (an export) has a file name
   that starts with the slug; what is inside an export is data and is not covered.
5. No response header's name or value holds the name.
6. /docs/run (which documents the software for its operator) is not shown under a brand: it
   answers exactly as the address of a guide that does not exist, and no page lists or links
   it. Every other guide is shown, says the brand's name in its prose where the guide without
   a brand says the software's, and keeps its commands: each code element is the one the
   guide has without a brand, every identifier as it is, each connection's name the slug,
   and only the other places that held the name changed, to words that do not hold it.
7. Three places read as written for the brand: the welcome page's start button is "Choose
   what you will use Knowledge Base for"; the connect guide's first sentence, whose subject
   was the software's name, has the brand's name for its subject and is otherwise as it was;
   the Agents page says "Knowledge Base also ends it if one of its replaced tokens is used
   again". A brand is shown as it is written, whatever characters it holds (`&`, `*`, `_`).

Agents
8. The MCP server introduces itself by the brand. For an agent with a grant, the `initialize`
   answer's server name is the brand's name, and nothing in that answer or in `tools/list`
   (the instructions, each tool's description, each parameter's description) holds the name.
   The tools' names are unchanged. The instructions' first sentence starts with the brand's
   name, its subject.
9. What tools and the REST API answer does not name the software where the words are the
   product's own: guidance, an error's message, a refusal. What a person wrote (a note's
   text) is theirs and comes back unchanged, over MCP, over REST and on its page. A note
   guard's rule is named in a refusal as machines know it (frozen G1: `cairn_credential`);
   that identifier may appear in an API's answer, and nothing else of the name.

What stays the software's
- Identifiers, credentials' prefixes, the `Server` header of the HTTP server, log lines, the
  command line, the operator's files, the database.
- Without a brand, everything: the name is where it is today (the welcome page's start
  button, the MCP server's name, /docs/run, an export's file name), and pages, the MCP
  `initialize` answer and `tools/list` are byte for byte the same before and after a branded
  app ran in the same process.
"""

import base64
import hashlib
import itertools
import json
import re
import secrets
from datetime import UTC, datetime
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urljoin, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

import cairn
from cairn.arrange import tidy_page
from cairn.identity import GitHub
from cairn.plans import Plan, Plans
from cairn.server import SignIn, create_app
from cairn.sources import KINDS
from cairn.store import Store

# The software's own name, which a signed-in person and an agent must not be told under a brand.
NAME = cairn.__name__.lower()
HOST = "kb.example.test"  # name-free, so that the only way the name shows is the server's
BASE = f"https://{HOST}"
MCP_URL = f"{BASE}/mcp"
BRAND = "Knowledge Base"
SLUG = "knowledge-base"
ALT = "Juniper Field"
# A brand with characters that mean something to Markdown and to HTML: shown as it is written.
ODD_BRAND = "R&D *Field* _Notes_"
SLUG_SHAPE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Rule 7's words, with the brand in place of {}.
START_BUTTON = "Choose what you will use {} for"
AGENTS_SENTENCE = "{} also ends it if one of its replaced tokens is used again"
ADA, BROOK, CY, DEE = (f"{who}@example.test" for who in ("ada", "brook", "cy", "dee"))
ADA_GITHUB = {"id": 7001, "login": "ada", "name": "Ada Quill", "email": ADA}
BROOK_GITHUB = {"id": 7002, "login": "brook", "name": "Brook Ng", "email": BROOK}
CY_GITHUB = {"id": 7003, "login": "cy", "name": "Cy Moss", "email": CY}
CLIENT_SECRET = "-".join(("gh", "client", "secret", "made", "up"))
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
REDIRECT = "http://127.0.0.1:33418/callback"
# The store's clock. It is far ahead of any day the tests run on: the MCP SDK's token endpoint
# compares a code's expiry, which the store stamps by this clock, with the real one.
START = datetime(2099, 11, 14, 9, 0, tzinfo=UTC)
PLANS = Plans(
    {
        "free": Plan("Free", notes_per_month=1_000, projects=50, model_budget_micros=0),
        "solo": Plan("Solo", notes_per_month=10_000, projects=500, model_budget_micros=0),
    },
    default="free",
)
SVG_LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 32" width="160" height="32">'
    '<text x="4" y="22" fill="#1b2a24" font-family="sans-serif" font-size="18">'
    f"{ALT}</text></svg>\n"
).encode()

# Rule 2: the identifiers a person types exactly, the only places code may hold the name.
IDENTIFIERS = (
    re.compile(r"python3? -m cairn(?:\.[a-z_]+)*\b"),  # the module
    re.compile(r"(?<![A-Za-z0-9_])CAIRN_[A-Z0-9_]+"),  # an environment variable
    re.compile(r"(?<![A-Za-z0-9_-])cairn\.(?:db|env)\b"),  # a file name the software chooses
    re.compile(r"\bghcr\.io/[\w.-]+/cairn(?::[\w.-]+)?"),  # the container image
    re.compile(r"(?<![A-Za-z0-9_-])cairn\.(?:at|rt)\."),  # a token's prefix
)
# Rule 9: a note guard's rule, as machines know it (frozen G1), in an API's refusal.
RULE_NAME = "cairn_credential"
# Rule 3: where a snippet that adds the server to an agent says the connection's name.
_NAMED = r"([^\s\"'\[\]{}()`]+)"
CONNECTIONS = (
    re.compile(r"\bmcp add(?: --(?:transport|scope) \S+)* " + _NAMED),  # an agent's command line
    re.compile(r"\bmcp (?:login|logout) " + _NAMED),
    re.compile(r"/mcp auth " + _NAMED),
    re.compile(r'"([^"]+)"\s*:\s*\{\s*"(?:type|url|httpUrl)"'),  # a JSON config's entry
    re.compile(r"\[mcp_servers\.([^\]\s]+)\]"),  # a TOML config's table
    re.compile(r"--add-mcp '\{\s*\"name\"\s*:\s*\"([^\"]+)\""),  # an editor's command line
)
# A note whose text holds the name: the person's own words, which nothing may change.
SENTENCE = f"Ada built a {NAME} of flat stones at the pass."
NOTE_TEXT = f"## Ridge\n\n{SENTENCE}\n"
# What the operator of an install wrote for its privacy guide: theirs, name and all.
OPERATOR_SAID = f"Brook keeps this {NAME} on a machine in the hallway cupboard."
OPERATOR_NOTES = f"- **Where it runs.** {OPERATOR_SAID}\n"
# A made-up agent token of the software's own shape, which the note guard refuses.
TOKEN = f"{NAME}.at." + "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zA" + "aB3dE5gH7jK"


# What the name looks like in a response


_TOKEN_CHAR = re.compile(r"[A-Za-z0-9_-]")


def _by_chance(text: str, start: int, end: int) -> bool:
    """Whether the name at text[start:end] sits inside a long run of token characters (a
    random key or session token), where it is chance, not a name."""
    while start > 0 and _TOKEN_CHAR.match(text[start - 1]):
        start -= 1
    while end < len(text) and _TOKEN_CHAR.match(text[end]):
        end += 1
    return end - start >= 32


def names_software(text: str | bytes) -> bool:
    """Whether `text` holds the software's name, in any case, other than by chance."""
    if isinstance(text, bytes):
        text = text.decode("latin-1")
    return any(
        not _by_chance(text, m.start(), m.end())
        for m in re.finditer(re.escape(NAME), text, re.IGNORECASE)
    )


def no_name_in(response: httpx.Response, what: str) -> None:
    assert not names_software(response.content), f"{what}: the body names the software"
    for header, value in response.headers.multi_items():
        assert not names_software(header), f"{what}: header {header!r} names the software"
        assert not names_software(value), f"{what}: header {header} names the software: {value}"


def spans_of(patterns, text: str) -> list[tuple[int, int]]:
    return [m.span() for pattern in patterns for m in pattern.finditer(text)]


def strays_in_code(text: str) -> list[str]:
    """Each place in the text of a code element that holds the name outside an identifier
    (rule 2), with a little of what is around it."""
    kept = spans_of(IDENTIFIERS, text)
    return [
        text[max(0, m.start() - 30) : m.end() + 30]
        for m in re.finditer(re.escape(NAME), text, re.IGNORECASE)
        if not any(s <= m.start() and m.end() <= e for s, e in kept)
        and not _by_chance(text, m.start(), m.end())
    ]


def without(text: str, own: tuple[str, ...]) -> str:
    """`text` with what the person wrote taken out: theirs, not the product's words."""
    for words in own:
        text = text.replace(words, " ")
    return text


# Pages


CODE_TAGS = ("code", "pre", "kbd", "samp")
BLOCK_TAGS = ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6")
MET = ("alt", "title", "placeholder", "aria-label")  # attributes a person meets


class Page(HTMLParser):
    """A page: its tags, its visible text outside code (in segments), the text of each code
    element (the outermost of nested ones), its title, its blocks (paragraphs, list items,
    headings: their text in pieces, each marked as code or not, in document order), its links
    and its buttons."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.title = ""
        self.prose: list[str] = []
        self.code: list[str] = []
        self.blocks: list[tuple[str, list[tuple[bool, str]]]] = []
        self.anchors: list[dict] = []
        self.buttons: list[str] = []
        self._skip: str | None = None
        self._depth = 0
        self._coded: list[str] = []
        self._open: list[tuple[str, list[tuple[bool, str]]]] = []
        self._a: dict | None = None
        self._button: list[str] | None = None
        self.feed(html)
        self.close()
        self.title = " ".join(self.title.split())

    def handle_starttag(self, tag, attrs):
        attributes = {name: value or "" for name, value in attrs}
        self.tags.append((tag, attributes))
        if tag in ("title", "script", "style"):
            self._skip = tag
        elif tag in CODE_TAGS:
            if self._depth == 0:
                self._coded = []
            self._depth += 1
        elif tag in BLOCK_TAGS:
            block: tuple[str, list[tuple[bool, str]]] = (tag, [])
            self.blocks.append(block)
            self._open.append(block)
        if tag == "a":
            self._a = {"href": attributes.get("href"), "text": []}
            self.anchors.append(self._a)
        elif tag == "button":
            self._button = []

    def handle_endtag(self, tag):
        if tag == self._skip:
            self._skip = None
        elif tag in CODE_TAGS and self._depth:
            self._depth -= 1
            if self._depth == 0:
                self.code.append("".join(self._coded))
        elif tag in BLOCK_TAGS:
            for n in range(len(self._open) - 1, -1, -1):
                if self._open[n][0] == tag:
                    del self._open[n]
                    break
        if tag == "a":
            self._a = None
        elif tag == "button" and self._button is not None:
            self.buttons.append(" ".join("".join(self._button).split()))
            self._button = None

    def handle_data(self, data):
        if self._skip == "title":
            self.title += data
            return
        if self._skip:
            return
        coded = self._depth > 0
        if coded:
            self._coded.append(data)
        elif text := " ".join(data.split()):
            self.prose.append(text)
        for _, pieces in self._open:
            pieces.append((coded, data))
        if self._a is not None:
            self._a["text"].append(data)
        if self._button is not None:
            self._button.append(data)

    def named(self, *tags: str) -> list[dict[str, str]]:
        return [attributes for tag, attributes in self.tags if tag in tags]

    def met(self) -> list[tuple[str, str, str]]:
        """The attributes a person meets: (tag, attribute, value)."""
        found = []
        for tag, attributes in self.tags:
            for name in MET:
                if name in attributes:
                    found.append((tag, name, attributes[name]))
            hidden = tag == "input" and attributes.get("type", "").lower() == "hidden"
            if "value" in attributes and tag in ("button", "input") and not hidden:
                found.append((tag, "value", attributes["value"]))
            if tag == "meta" and attributes.get("name", "").lower() == "description":
                found.append((tag, "content", attributes.get("content", "")))
        return found

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
                content = attributes.get("content", "")
                found.append(content.split("=", 1)[1].strip() if "=" in content else "")
        return found

    def link_texts(self) -> list[str]:
        """The text of each link and each button, as a person reads it."""
        said = [" ".join("".join(a["text"]).split()) for a in self.anchors]
        return said + self.buttons


def block_text(pieces: list[tuple[bool, str]], code: bool = True) -> str:
    return " ".join("".join(text for coded, text in pieces if code or not coded).split())


def prose_blocks(page: Page) -> list[str]:
    """The text of each block outside code: what the page says in its own words."""
    return [block_text(pieces, code=False) for _, pieces in page.blocks]


def local(address: str, at: str) -> str | None:
    """The path and query of `address`, as read on the page at `at`, when it is this server's."""
    if address.startswith(("data:", "mailto:", "javascript:")):
        return None
    parts = urlsplit(urljoin(BASE + at, address))
    if parts.netloc != HOST:
        return None
    return parts.path + (f"?{parts.query}" if parts.query else "")


def to_run_guide(address: str, at: str) -> bool:
    found = local(address, at)
    if found is None:
        return False
    path = urlsplit(found).path.rstrip("/")
    return path in ("/docs/run", "/docs/run.md")


def query(url: str) -> dict:
    return {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}


def hidden_fields(html: str) -> dict[str, str]:
    return {a["name"]: a.get("value", "") for a in Page(html).named("input")
            if a.get("type") == "hidden"}  # fmt: skip


def filename_of(response: httpx.Response) -> str:
    said = response.headers.get("content-disposition", "")
    found = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", said)
    assert found, f"no file name: {said!r}"
    return found.group(1)


def problems(r: httpx.Response, at: str, own: tuple[str, ...] = (), api: bool = False) -> list[str]:
    """What in one response names the software against rules 1, 2, 4, 5, 6 and 9. `at`: the
    address it answered; `own`: what the person wrote; `api`: an answer to an agent or a
    script, which may name a guard rule by its identifier."""
    found = []
    for header, value in r.headers.multi_items():
        if names_software(header) or names_software(value):
            found.append(f"header {header}: {value}")
    kind = r.headers.get("content-type", "")
    if "html" in kind:
        page = Page(r.text)
        found += [f"text: {text!r}" for text in page.prose if names_software(without(text, own))]
        if names_software(page.title):
            found.append(f"title: {page.title!r}")
        found += [
            f"<{tag} {attribute}={value!r}>"
            for tag, attribute, value in page.met()
            if names_software(without(value, own))
        ]
        for code in page.code:
            found += [f"code: {stray!r}" for stray in strays_in_code(without(code, own))]
        for ref in page.refs():
            if names_software(ref):
                found.append(f"refers to {ref}")
            if to_run_guide(ref, urlsplit(at).path):
                found.append(f"links the operator's guide: {ref}")
    elif kind.startswith(("application/json", "text/")):
        body = without(r.text, own)
        if api:
            body = body.replace(RULE_NAME, " ")
        if names_software(body):
            found.append(f"body: {body[:300]!r}")
    return found


def check(r: httpx.Response, at: str, what: str, own: tuple[str, ...] = (), api=False) -> None:
    found = problems(r, at, own, api)
    assert not found, f"{what} ({r.status_code}) names the software:\n" + "\n".join(found)


# The world: a stand-in identity provider, a store on a clock


class FakeGitHub:
    """GitHub's OAuth and profile endpoints, as an httpx mock (as frozen H4 builds them)."""

    def __init__(self) -> None:
        self.codes: dict[str, dict] = {}
        self.tokens: dict[str, dict] = {}

    def approve(self, profile: dict) -> str:
        code = f"code-github-{secrets.token_hex(4)}"
        self.codes[code] = profile
        return code

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            body = request.content.decode()
            sent = json.loads(body) if body.lstrip().startswith("{") else dict(parse_qsl(body))
            profile = self.codes.pop(sent.get("code", ""), None)
            if profile is None:
                return httpx.Response(400, json={"error": "bad_verification_code"})
            token = f"provider-token-{secrets.token_hex(8)}"
            self.tokens[token] = profile
            return httpx.Response(200, json={"access_token": token, "token_type": "bearer"})
        profile = self.tokens.get(request.headers.get("authorization", "").partition(" ")[2])
        if profile is None:
            return httpx.Response(401, json={"message": "Bad credentials"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={k: profile[k] for k in ("id", "login", "name")})
        if url == "https://api.github.com/user/emails":
            return httpx.Response(
                200, json=[{"email": profile["email"], "primary": True, "verified": True}]
            )
        return httpx.Response(404)


class World:
    """One database on a clock, and the apps and browsers made on it."""

    def __init__(self, folder) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.db = folder / "kb.db"
        self.now = START
        self.store = Store(self.db, clock=lambda: self.now)
        self.idp = FakeGitHub()
        self.browsers: list[TestClient] = []

    def app(self, **settings):
        sign_in = SignIn(
            providers=(GitHub("github-client-id", CLIENT_SECRET),),
            http=httpx.AsyncClient(transport=httpx.MockTransport(self.idp.handler)),
            join="personal",
            allowed_emails=(ADA, BROOK, CY, DEE),
        )
        return create_app(
            self.store,
            public_url=BASE,
            sign_in=sign_in,
            plans=PLANS,
            clock=lambda: self.now,
            **settings,
        )

    def branded(self, **more) -> dict:
        logo = self.folder / "logo.svg"
        logo.write_bytes(SVG_LOGO)
        return {"brand_name": BRAND, "brand_logo": logo, "brand_logo_alt": ALT, **more}

    def browser(self, app) -> TestClient:
        client = TestClient(app, base_url=BASE)
        client.__enter__()
        self.browsers.append(client)
        return client


@pytest.fixture
def make(tmp_path):
    made: list[World] = []

    def build(name: str = "one") -> World:
        world = World(tmp_path / name)
        made.append(world)
        return world

    yield build
    for world in made:
        for client in world.browsers:
            client.__exit__(None, None, None)
        world.store.close()


def fresh(app) -> TestClient:
    """Another client on an app whose first browser already started it."""
    return TestClient(app, base_url=BASE)


def signed_in(world: World, browser: TestClient, profile: dict) -> str:
    """Sign in through the stand-in GitHub; the session's token, as the store knows it."""
    r = browser.get("/auth/github", follow_redirects=False)
    assert r.status_code == 302, r.text
    state = query(r.headers["location"])["state"]
    code = world.idp.approve(profile)
    r = browser.get(f"/auth/github/callback?code={code}&state={state}", follow_redirects=False)
    assert r.status_code == 200, r.text
    tokens = []
    for header in r.headers.get_list("set-cookie"):
        jar = SimpleCookie()
        jar.load(header)
        tokens += [m.value for m in jar.values() if m.value and m["path"] == "/"]
    assert len(tokens) == 1, r.headers.get_list("set-cookie")
    return tokens[0]


# An agent connects (as frozen B1 does it)


def register(browser: TestClient, name: str) -> str:
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
    return r.json()["client_id"]


def authorize(browser: TestClient, client_id: str) -> tuple[str, str]:
    """The agent's authorization request, opened in the browser: the verifier, and the
    consent page's address it leads to."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    scopes = browser.get("/.well-known/oauth-protected-resource/mcp").json()["scopes_supported"]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "agent-state",
        "resource": MCP_URL,
        "scope": " ".join(scopes),
    }
    r = browser.get("/authorize?" + urlencode(params), follow_redirects=False)
    assert r.status_code == 302, r.text
    return verifier, local(r.headers["location"], "/authorize")


def approve(browser: TestClient, consent: str, client_id: str, verifier: str) -> str:
    """Approve on the consent page; the agent's access token."""
    page = browser.get(consent)
    assert page.status_code == 200, page.status_code
    decided = browser.post(
        "/oauth/consent",
        data={**hidden_fields(page.text), "decision": "approve"},
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
    return token.json()["access_token"]


def connect_agent(browser: TestClient, name: str = "Field agent") -> str:
    client_id = register(browser, name)
    verifier, consent = authorize(browser, client_id)
    return approve(browser, consent, client_id, verifier)


def rpc(app, access: str, method: str, params: dict | None = None) -> httpx.Response:
    """One JSON-RPC request to /mcp with the agent's token."""
    return fresh(app).post(
        "/mcp",
        headers={**MCP_HEADERS, "authorization": f"Bearer {access}"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


INITIALIZE = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "probe", "version": "1"},
}


def result_of(answer: httpx.Response) -> dict:
    assert answer.status_code == 200, answer.text
    return answer.json()["result"]


# A workspace with something on every page


def a_source_kind() -> str:
    """A kind of source the software ships, taken from its own list rather than spelled
    here (each kind it ships is named after a product this file does not name): the first
    that is added off and reads with a key from an environment variable."""
    for kind, spec in KINDS.items():
        try:
            spec.check({"key_env": "MINUTES_KEY", "projects": []})
        except ValueError:
            continue
        if spec.starts_off:
            return kind
    pytest.fail("no kind of source is added off and takes key_env and projects")


class Furnished:
    """Ada's workspace on `app`, with rows on its pages, and the values that address them."""

    def __init__(self, world: World, app) -> None:
        self.world, self.app = world, app
        self.ada = world.browser(app)
        principal = world.store.resolve_session(signed_in(world, self.ada, ADA_GITHUB))
        self.account = principal.account_id
        view = world.store.for_account(self.account, principal.user_id)
        system = world.store.for_account(self.account)  # acts for no person: "the system"
        core = view.create_project("core", "What spans projects")
        runbooks = view.create_project("runbooks", "How the team runs things", "team")
        self.key = view.create_key("laptop")
        self.api = fresh(app)
        self.api.headers["x-api-key"] = self.key
        saved = [
            self.note("core", "## Rollout\n\nShip on Tuesdays after the standup.\n\n"
                      "## Rollback\n\nKeep the last image for a week.\n", "deploys"),
            self.note("core", "## Rollout\n\nRun the canary for an hour first.\n", "deploys"),
            self.note("runbooks", "## Pager\n\nThe pager rotates every Monday.\n", "pager"),
        ]  # fmt: skip
        self.stored = saved[0]["note_id"]
        assert tidy_page(system, core.id, "deploys").status == "arranged"  # by the software
        claims = self.api.get("/api/projects/core/pages/deploys").json()["claims"]
        refused = self.api.post("/api/projects/core/notes", json={"text": f"token {TOKEN}\n"})
        assert refused.status_code == 422, refused.text  # the guard's refusal, audited
        self.set = view.create_set("work", [core.id, runbooks.id]).id
        system.add_source("minutes", a_source_kind(), {"key_env": "MINUTES_KEY", "projects": []})
        self.access = connect_agent(self.ada)
        self.grant = view.list_grants()[0].id
        _, self.consent = authorize(self.ada, register(self.ada, "Second agent"))  # pending
        self.invite = view.invite(DEE)  # left open
        self.brook = fresh(app)  # the app is running: its first browser started it
        signed_in(world, self.brook, BROOK_GITHUB)
        joined = self.brook.post(
            f"/join/{view.invite(BROOK).token}", headers=SAME_ORIGIN, follow_redirects=False
        )
        assert joined.status_code == 303, joined.text
        members = {member.email: member for member in view.list_members()}
        assert members[BROOK].role == "member"
        self.brook_id = members[BROOK].user_id
        self.invite_id = next(i.id for i in view.list_invites() if i.email == DEE)
        self.params = {
            "provider": ["github", "no-such-provider"],
            "project": ["core", "runbooks", "no-such-project"],
            "slug": ["deploys", "pager", "no-such-page"],
            "grant": [self.grant, "no-such-grant"],
            "label": ["laptop", "no-such-key"],
            "set": [self.set, "no-such-set"],
            "note": [self.stored, "no-such-note"],
            "note_id": [self.stored, "no-such-note"],
            "claim_id": [claims[0]["id"], "no-such-claim"],
            "name": [*guide_names(self.ada), "no-such-guide", "minutes"],
            "invite": [self.invite_id, "no-such-invitation"],
            "user": [self.brook_id, "usr_000000000000"],
            "account": [self.account, "acct-1"],
            "token": [self.invite.token, "no-such-invitation"],
            "path": ["no-such-file.css"],
        }

    def note(self, project: str, text: str, topic: str) -> dict:
        r = self.api.post(f"/api/projects/{project}/notes", json={"text": text, "topic": topic})
        assert r.status_code in (200, 201), r.text
        return r.json()


def guide_names(browser: TestClient) -> list[str]:
    """The guides the index lists, by the name in their address."""
    page = Page(browser.get("/docs").text)
    names = [urlsplit(a["href"]).path.removeprefix("/docs/") for a in page.anchors]
    return [name for name in dict.fromkeys(names) if name and "/" not in name]


# 1. The crawl: every page a signed-in person can open


def routes_of(routes, prefix: str = "") -> list[str]:
    """Every route's path, a mount's own routes included."""
    found = []
    for route in routes:
        inner = getattr(route, "routes", None)
        if inner:
            found += routes_of(inner, prefix + route.path)
        else:
            found.append(prefix + route.path_format)
    return found


def addresses(app, params: dict[str, list[str]], extra: list[str]) -> list[str]:
    """Every address of the app's own route table (mounts included), each path parameter
    filled from `params`, plus `extra`."""
    found = []
    for template in routes_of(app.routes):
        names = re.findall(r"\{(\w+)(?::\w+)?\}", template)
        unknown = [name for name in names if name not in params]
        if unknown:
            pytest.fail(
                f"the route {template} has a parameter {unknown[0]!r} that the crawl has no"
                " values for: add ones that exist and ones that do not to Furnished.params"
            )
        for values in itertools.product(*(params[name] for name in names)):
            path = template
            for name, value in zip(names, values, strict=True):
                pattern = r"\{" + name + r"(?::\w+)?\}"
                filled = quote(value, safe="")
                path = re.sub(pattern, lambda _, v=filled: v, path, count=1)
            found.append(path)
    found += ["/no-such-page", *extra]
    return list(dict.fromkeys(found))


class Crawl:
    """GET requests as one signed-in person, following nothing but the links of the pages,
    each response checked by rules 1, 2, 4, 5, 6 and 9."""

    def __init__(self, browser: TestClient, who: str) -> None:
        self.browser, self.who = browser, who
        self.seen: dict[str, httpx.Response] = {}
        self.pages = 0
        self.queue: list[str] = []
        self.found: list[str] = []

    def get(self, address: str) -> httpx.Response:
        r = self.browser.get(address, follow_redirects=False)
        self.seen[address] = r
        api = address.startswith(("/api", "/mcp"))
        self.found += [f"GET {address}: {it}" for it in problems(r, address, api=api)]
        if "html" in r.headers.get("content-type", ""):
            self.pages += 1
            path = urlsplit(address).path
            for a in Page(r.text).anchors:
                found = local(a["href"] or "", path)
                if found and not found.startswith("/static/"):
                    self.queue.append(found.partition("#")[0])
        return r

    def everything(self, addresses: list[str]) -> None:
        for address in addresses:
            self.get(address)
        while self.queue:
            address = self.queue.pop()
            if address not in self.seen:
                assert len(self.seen) < 3000, "the crawl does not end"
                self.get(address)

    def passed(self) -> None:
        """Every problem the crawl found, all at once."""
        said = "\n".join(dict.fromkeys(self.found))
        assert not self.found, f"what {self.who} is shown names the software:\n{said}"


@pytest.mark.parametrize("wall", [False, True], ids=["no-wall", "wall"])
def test_no_page_a_signed_in_person_opens_names_the_software(make, wall):
    world = make()
    app = world.app(wall=wall, **world.branded())
    home = Furnished(world, app)
    listed = addresses(app, home.params, [home.consent, "/search?q=canary", "/search?q=zzzq"])

    owner = Crawl(home.ada, "the owner")
    owner.everything(listed)
    member = Crawl(home.brook, "a member")
    member.everything(listed)
    cy = fresh(app)  # an owner of a workspace with nothing in it yet: the empty states
    signed_in(world, cy, CY_GITHUB)
    empty = Crawl(cy, "an owner with no project")
    empty.everything(addresses(app, home.params, []))

    owner.passed()
    member.passed()
    empty.passed()
    assert owner.pages > 40 and member.pages > 40  # the route table was walked
    # The rows were there to be shown: the projects, the pages, the key, the agent, the set,
    # the source, the invitation, the member, the guard's refusal, the software's own tidy.
    shown = {address: Page(r.text) for address, r in owner.seen.items() if r.status_code == 200}
    said = {address: " ".join(page.prose) for address, page in shown.items()}
    assert "deploys" in said["/b/core"] and "laptop" in said["/keys"]
    assert "Field agent" in said["/agents"] and "minutes" in said["/sources"]
    assert DEE in said["/members"] and "Brook Ng" in said["/members"]
    assert "Rollback" in said["/b/core/p/deploys"] and "Second agent" in said[home.consent]
    assert "for an hour first" in said["/search?q=canary"]  # a search with a hit
    assert member.seen["/b/core/p/deploys"].status_code == 200
    # The export is named after the brand.
    export = owner.seen["/account/export"]
    assert export.status_code == 200 and filename_of(export).startswith(SLUG)


# 2. The pages forms answer with


def test_the_pages_forms_answer_with_name_nothing(make):
    world = make()
    app = world.app(**world.branded())
    home = Furnished(world, app)
    ada = home.ada
    cy = fresh(app)
    signed_in(world, cy, CY_GITHUB)
    # Each with the status it answers with today, so that it is the form's answer that is read.
    posts = [
        (ada, "/projects", {"name": "core", "description": ""}, (), 409),  # a name that is taken
        (ada, "/projects", {"name": "br_x"}, (), 400),  # a name that cannot be
        (ada, "/notes", {"project": "core", "text": ""}, (), 400),  # an empty note
        (ada, "/b/core/notes", {"text": "   "}, (), 400),
        (ada, "/b/core/notes", {"text": f"Deploy with {TOKEN}\n"}, (TOKEN,), 422),  # the guard
        (ada, "/members/invite", {"email": "not-an-address", "role": "member"}, (), 400),
        (ada, "/members/invite", {"email": CY, "role": "member"}, (), 200),  # made: shown once
        (ada, "/keys", {"label": "ci runner", "scope": "all", "access": "write"}, (), 200),
        (ada, "/sets", {"name": "work"}, (), 409),  # taken
        (cy, "/start", {}, (), 400),  # nothing ticked
        (cy, "/start", {"use": "no-such-use"}, (), 400),
    ]
    for browser, address, data, own, status in posts:
        r = browser.post(address, data=data, headers=SAME_ORIGIN, follow_redirects=True)
        assert r.status_code == status, (address, data, r.status_code)
        assert "html" in r.headers.get("content-type", ""), (address, r.status_code)
        check(r, address, f"POST {address} {data}", own)


# 3. The snippets that add the server to an agent


def connection_names(code: str) -> list[str]:
    return [m.group(1) for pattern in CONNECTIONS for m in pattern.finditer(code)]


def snippets(page: Page) -> list[str]:
    return [code for code in page.code if connection_names(code)]


@pytest.mark.parametrize("brand", [BRAND, ODD_BRAND], ids=["plain", "odd-characters"])
def test_the_snippets_name_the_connection_after_the_brand(make, brand):
    world = make()
    branded, plain = world.app(wall=True, brand_name=brand), world.app()
    ours, theirs = world.browser(branded), world.browser(plain)
    signed_in(world, ours, CY_GITHUB)  # a workspace with no project: the welcome page's start
    signed_in(world, theirs, CY_GITHUB)
    pages = ["/welcome", "/setup"]
    welcome = Page(ours.get("/welcome").text)
    ours.post("/start", data={"use": "repo"}, headers=SAME_ORIGIN)  # projects: /start/connect
    pages.append("/start/connect")

    slugs = set()
    for address in [*pages, "/docs/connect"]:
        mine, without_brand = Page(ours.get(address).text), Page(theirs.get(address).text)
        named = [name for code in mine.code for name in connection_names(code)]
        before = [name for code in without_brand.code for name in connection_names(code)]
        assert before and set(before) == {NAME}, (address, before)  # as today, the software's
        assert len(named) == len(before), (address, named, before)  # every snippet is there
        slugs |= set(named)
        if address != "/docs/connect":  # the app's own pages: the install's own address
            for code in snippets(mine):
                for line in code.splitlines():
                    if "mcp add" in line:
                        assert MCP_URL in line, (address, line)
    assert snippets(welcome)  # the welcome page shows the commands
    assert len(slugs) == 1, slugs  # one name for the connection, wherever it is given
    slug = slugs.pop()
    assert SLUG_SHAPE.fullmatch(slug) and not names_software(slug), slug
    if brand == BRAND:
        assert slug == SLUG


# 4. The guides


def code_template(code: str) -> re.Pattern:
    """What a guide's code element may be under the brand, from what it is without one: the
    same text, each identifier as it is, each connection's name the slug, and each other run
    of characters that holds the name free to be other words (that do not hold it)."""
    kept = spans_of(IDENTIFIERS, code)
    named = [m.span(1) for pattern in CONNECTIONS for m in pattern.finditer(code)]
    if code.strip().lower() == NAME:  # a name to type into an app's form: the connection's
        named.append((code.index(code.strip()), code.index(code.strip()) + len(NAME)))
    parts, at = [], 0
    for run in re.finditer(r"[A-Za-z0-9_-]*" + re.escape(NAME) + r"[A-Za-z0-9_-]*", code, re.I):
        inside = [m.span() for m in re.finditer(re.escape(NAME), run.group(), re.IGNORECASE)]
        if all(
            any(s <= run.start() + a and run.start() + b <= e for s, e in kept) for a, b in inside
        ):
            continue  # an identifier, kept as it is
        parts.append(re.escape(code[at : run.start()]))
        parts.append(re.escape(SLUG) if run.span() in named else r"([^\n]{0,80}?)")
        at = run.end()
    parts.append(re.escape(code[at:]))
    return re.compile("".join(parts))


def test_the_guides_say_the_brand_and_keep_their_commands(make):
    world = make()
    notes = {"operator_notes": OPERATOR_NOTES}  # the privacy guide's last section
    branded = world.app(wall=True, **notes, **world.branded())
    plain = world.app(**notes)
    ours, theirs = world.browser(branded), world.browser(plain)
    signed_in(world, ours, ADA_GITHUB)
    signed_in(world, theirs, ADA_GITHUB)
    names = guide_names(theirs)
    assert "run" in names and "connect" in names  # the guides without a brand, as today
    missing = ours.get("/docs/no-such-guide")
    assert missing.status_code == 404

    shown = 0
    for name in names:
        r, before = ours.get(f"/docs/{name}"), theirs.get(f"/docs/{name}")
        assert before.status_code == 200, name
        if name == "run":  # the operator's guide: as a guide that does not exist
            assert r.status_code == missing.status_code, r.status_code
            assert Page(r.text).prose == Page(missing.text).prose
            assert r.content == missing.content.replace(b"no-such-guide", b"run")
            continue
        assert r.status_code == 200, name
        check(r, f"/docs/{name}", f"the guide {name}", own=(OPERATOR_SAID,))
        mine, without_brand = Page(r.text), Page(before.text)
        if name == "privacy":  # the operator's own words, as they wrote them
            assert any(OPERATOR_SAID in text for text in prose_blocks(mine))
        if any(names_software(text) for text in prose_blocks(without_brand)):
            assert any(BRAND in text for text in prose_blocks(mine)), name
        assert len(mine.code) == len(without_brand.code), name
        for old, new in zip(without_brand.code, mine.code, strict=True):
            found = code_template(old).fullmatch(new)
            assert found, f"/docs/{name}: a code element changed beyond the brand:\n{old}\n{new}"
            assert not any(names_software(hole) for hole in found.groups()), (name, new)
        shown += 1
    assert shown == len(names) - 1
    index = Page(ours.get("/docs").text)
    assert not [ref for ref in index.refs() if to_run_guide(ref, "/docs")]
    assert "run" not in guide_names(ours)
    assert sorted(guide_names(ours)) == sorted(n for n in names if n != "run")


# 5. Three places, word for word


@pytest.mark.parametrize("brand", [BRAND, ODD_BRAND], ids=["plain", "odd-characters"])
def test_three_places_read_as_written_for_the_brand(make, brand):
    world = make()
    branded, plain = world.app(wall=True, brand_name=brand), world.app()
    ours, theirs = world.browser(branded), world.browser(plain)
    signed_in(world, ours, CY_GITHUB)  # a workspace with no project: the welcome page's start
    signed_in(world, theirs, CY_GITHUB)

    welcome = Page(ours.get("/welcome").text)
    assert START_BUTTON.format(brand) in welcome.link_texts(), welcome.link_texts()
    agents = Page(ours.get("/agents").text)
    assert any(AGENTS_SENTENCE.format(brand) in text for text in prose_blocks(agents))
    # The connect guide's first sentence whose subject was the software's name.
    before = Page(theirs.get("/docs/connect").text)
    pieces = next(
        pieces
        for tag, pieces in before.blocks
        if tag == "p" and block_text(pieces).lower().startswith(NAME + " ")
    )
    first = next(text for coded, text in pieces if not coded and text.strip())
    first = " ".join(first.split())
    mine = Page(ours.get("/docs/connect").text)
    said = [
        " ".join(next((text for coded, text in pieces if not coded and text.strip()), "").split())
        for tag, pieces in mine.blocks
        if tag == "p"
    ]
    assert brand + first[len(NAME) :] in said, (first, said[:4])


# 6. Agents


def test_the_mcp_server_introduces_itself_by_the_brand(make):
    world = make()
    branded, plain = world.app(wall=True, **world.branded()), world.app()
    ada = world.browser(branded)
    world.browser(plain)
    principal = world.store.resolve_session(signed_in(world, ada, ADA_GITHUB))
    view = world.store.for_account(principal.account_id, principal.user_id)
    view.create_project("trail", "Walks")
    access = connect_agent(ada)
    own = (SENTENCE,)

    hello = rpc(branded, access, "initialize", INITIALIZE)
    check(hello, "/mcp", "initialize", api=True)
    said = result_of(hello)
    assert said["serverInfo"]["name"] == BRAND
    assert said["instructions"].startswith(BRAND + " "), said["instructions"][:80]
    listed = rpc(branded, access, "tools/list")
    check(listed, "/mcp", "tools/list", api=True)
    tools = [tool["name"] for tool in result_of(listed)["tools"]]
    assert tools == [tool["name"] for tool in result_of(rpc(plain, access, "tools/list"))["tools"]]

    def call(name: str, arguments: dict, refused: bool = False) -> dict:
        answer = rpc(branded, access, "tools/call", {"name": name, "arguments": arguments})
        check(answer, "/mcp", f"{name} {arguments}", own, api=True)
        result = result_of(answer)
        assert bool(result.get("isError")) == refused, (name, arguments, result)
        return result

    def text_of(result: dict) -> str:
        return result["content"][0]["text"]

    call("projects", {})
    call("new_project", {"name": "ridge"})
    call("new_project", {"name": "trail"}, refused=True)  # taken
    call("new_project", {"name": "br_nope"}, refused=True)
    written = {"project": "trail", "text": NOTE_TEXT, "topic": "ridge-markers", "source_ref": "w-1"}
    noted = json.loads(text_of(call("note", written)))
    call("note", {"project": "no-such-project", "text": "A plain note.\n"}, refused=True)
    call("note", {"project": "trail", "text": ""}, refused=True)
    call("note", {"project": "trail", "text": f"Deploy with {TOKEN}\n"}, refused=True)  # guard
    assert SENTENCE in text_of(call("recall", {"query": "flat stones"}))
    call("recall", {"query": "flat stones", "project": "no-such-project"}, refused=True)
    page = call("page", {"project": "trail", "slug": noted["page"]})
    assert SENTENCE in text_of(page)
    claim = json.loads(text_of(page))["claims"][0]["id"]
    call("page", {"project": "trail", "slug": "no-such-page"}, refused=True)
    assert SENTENCE in text_of(call("evidence", {"project": "trail", "slug": noted["page"],
                                               "claim_id": claim}))  # fmt: skip
    call(
        "evidence", {"project": "trail", "slug": noted["page"], "claim_id": "no-such"}, refused=True
    )
    call("overview", {"project": "trail"})
    call("overview", {"project": "no-such-project"}, refused=True)

    # The person's own words, on the page they compiled into.
    address = f"/b/trail/p/{noted['page']}"
    r = ada.get(address)
    assert r.status_code == 200
    check(r, address, "the note's page", own)
    assert any(SENTENCE in text for text in prose_blocks(Page(r.text)))


def test_the_rest_api_names_nothing_but_what_people_wrote(make):
    world = make()
    app = world.app(wall=True, **world.branded())
    ada = world.browser(app)
    principal = world.store.resolve_session(signed_in(world, ada, ADA_GITHUB))
    view = world.store.for_account(principal.account_id, principal.user_id)
    view.create_project("trail", "Walks")
    api = fresh(app)
    api.headers["x-api-key"] = view.create_key("script")
    own = (SENTENCE,)

    noted = api.post("/api/projects/trail/notes", json={"text": NOTE_TEXT, "topic": "ridge"})
    assert noted.status_code in (200, 201), noted.text
    check(noted, "/api/projects/trail/notes", "a note", own, api=True)
    found = api.get("/api/search", params={"q": "flat stones"})
    assert found.status_code == 200 and SENTENCE in found.text
    check(found, "/api/search", "a search", own, api=True)
    for method, address, body, status in (
        ("GET", "/api/projects/no-such-project/pages", None, 404),
        ("GET", "/api/projects/trail/pages/no-such-page", None, 404),
        ("GET", "/api/projects/trail/pages/ridge/claims/no-such-claim/evidence", None, 404),
        ("POST", "/api/projects", {"content": b"not json"}, 400),
        ("POST", "/api/projects", {"json": {"name": "trail"}}, 409),
        ("POST", "/api/projects", {"json": {"name": "br_nope"}}, 400),
        ("POST", "/api/projects/trail/notes", {"json": {}}, 400),
        ("POST", "/api/projects/trail/notes", {"json": {"text": f"Use {TOKEN}\n"}}, 422),
        ("POST", "/api/projects/no-such-project/notes", {"json": {"text": "A note.\n"}}, 404),
        ("DELETE", "/api/projects/no-such-project", None, 404),
        ("GET", "/api/search?q=x&limit=nope", None, 400),
    ):
        r = api.request(method, address, **(body or {}))
        assert r.status_code == status, (method, address, r.status_code, r.text)
        check(r, address, f"{method} {address}", own, api=True)
    export = api.get("/api/account/export")
    assert export.status_code == 200 and filename_of(export).startswith(SLUG)
    check(export, "/api/account/export", "an export", api=True)


# 7. Without a brand, everything is as before


SNAPSHOT = (
    "/welcome", "/", "/setup", "/start", "/start/connect", "/agents", "/keys", "/workspace",
    "/members", "/account", "/docs", "/docs/connect", "/docs/patterns", "/docs/sources",
    "/docs/security", "/docs/privacy", "/docs/run", "/docs/no-such-guide", "/no-such-page",
)  # fmt: skip


def snapshot(browser: TestClient, app, access: str) -> dict:
    taken = {}
    for address in SNAPSHOT:
        r = browser.get(address, follow_redirects=False)
        taken[address] = (r.status_code, r.headers.multi_items(), r.content)
    for method, params in (("initialize", INITIALIZE), ("tools/list", None)):
        r = rpc(app, access, method, params)
        taken[method] = (r.status_code, r.headers.get("content-type"), r.content)
    return taken


def test_without_a_brand_everything_is_as_before(make):
    world = make()
    plain = world.app()
    browser = world.browser(plain)
    token = signed_in(world, browser, CY_GITHUB)  # no project yet: the welcome page's start
    access = connect_agent(browser)

    # The software's name where it is today.
    welcome = Page(browser.get("/welcome").text)
    assert START_BUTTON.format(NAME) in welcome.link_texts()
    hello = result_of(rpc(plain, access, "initialize", INITIALIZE))
    assert hello["serverInfo"]["name"] == NAME
    assert hello["instructions"].lower().startswith(NAME + " ")
    assert browser.get("/docs/run").status_code == 200
    assert filename_of(browser.get("/account/export")).startswith(NAME)
    before = snapshot(browser, plain, access)
    # No brand's words in the app's pages or the MCP answers, whatever ran before in this
    # process (other tests' branded apps).
    for what, (*_, content) in before.items():
        if not what.startswith("/docs"):  # (the operator's guide shows the setting)
            words = [BRAND, SLUG, "R&amp;D", "R&D"]
            assert not [w for w in words if w.encode() in content], what

    # A branded app, made and used in the same process, changes nothing for the others: the
    # app made before it, and one made after it with every setting at its default.
    branded = world.app(wall=True, **world.branded())
    other = world.browser(branded)
    signed_in(world, other, CY_GITHUB)
    for address in SNAPSHOT:
        other.get(address)
    rpc(branded, access, "initialize", INITIALIZE)
    rpc(branded, access, "tools/list")
    assert snapshot(browser, plain, access) == before
    defaults = world.app(wall=False, brand_name=None, brand_logo=None, brand_logo_alt=None)
    later = world.browser(defaults)
    for cookie in browser.cookies.jar:
        later.cookies.set(cookie.name, cookie.value)
    assert token in [cookie.value for cookie in later.cookies.jar]
    assert snapshot(later, defaults, access) == before
