"""P1 acceptance tests (frozen): the product's pages, for a first visitor and for daily use.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstring of src/cairn/server.py.

Navigation
- A signed-in page's main navigation holds, in this order and nothing else: Projects (/),
  Search (/search), New note (/new), Workspace (/workspace), then the person's name (a
  link to /workspaces for a person; absent for a session opened with a key) and Sign out.
- GET /workspace is the workspace's own page: its name and one entry for each part the
  viewer may open, each a link with one sentence saying what it is for. In this order:
  Agents (/agents), Keys (/keys), Sets (/sets), Members (/members), Sources (/sources),
  Usage (/usage), Audit log (/audit), Setup
  (/start/connect) and Data and deletion (/account). The entries are the links inside the
  list with the id "parts". A viewer sees exactly the entries that open for them: one that
  would answer anything but 200 is not shown.

Public pages (no session needed)
- GET /docs lists the guides. GET /docs/<name> renders one from the Markdown shipped
  inside the package: "connect", "patterns", "security" and "privacy". A guide's headings
  become headings and its code becomes code; raw HTML in a guide is shown as text. Another
  name is 404.
- When people sign in through a provider and each gets a workspace of their own, GET /
  without a session is the landing page: the server's own MCP address in the commands that
  connect an agent, sections with the ids "connect", "checks" and "run", and links to
  /login, /docs, /docs/security and /docs/privacy.
- GET /pricing is 404: nothing is sold.

Every page
- `<html lang="en">`, one `<h1>`, no `<script>` and no `style` attribute.
- Every input, select and textarea has a name a screen reader can say: a `<label for>`,
  a wrapping `<label>` or an `aria-label`. Hidden fields and buttons aside.
- A time is shown as words inside `<time datetime>`, never as a raw timestamp.
- A table that lists things carries `data-label` on every body cell, so a narrow screen
  can show each row as a card.
- Every address a page links to on this server answers 200, or 303 to /login when it needs
  a session: nothing a visitor, a member or an owner can click is missing or broken.
"""

import re
from html.parser import HTMLParser
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.plans import Plan, Plans
from cairn.server import SignIn, create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
ISSUER = "https://id.acme.example/pool-1"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
NOTE = (
    "# Retry policy\n\nQuill retries a failed charge 3 times.\n\n"
    "```bash\nquill run --max-retries 3\n```\n"
)
PLANS = Plans(
    {
        "free": Plan("Free", notes_per_month=50, storage_bytes=5_000_000, projects=3),
        "solo": Plan("Solo", notes_per_month=5_000, storage_bytes=500_000_000, projects=40),
    },
    default="free",
)
MAIN_NAV = [
    ("Projects", "/"),
    ("Search", "/search"),
    ("New note", "/new"),
    ("Workspace", "/workspace"),
]
PARTS = [
    ("Agents", "/agents"),
    ("Keys", "/keys"),
    ("Sets", "/sets"),
    ("Members", "/members"),
    ("Sources", "/sources"),
    ("Usage", "/usage"),
    ("Audit log", "/audit"),
    ("Setup", "/start/connect"),
    ("Data and deletion", "/account"),
]


class Page(HTMLParser):
    """What the checks need from one page: its links, headings, fields and tables."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.links: list[str] = []
        self.nav: list[tuple[str, str]] = []
        self.h1 = 0
        self.lang = None
        self.styled = self.scripts = 0
        self.unnamed: list[str] = []
        self.unlabelled_cells = 0
        self._labels_for: set[str] = set()
        self._fields: list[dict] = []
        self._in_label = self._in_nav = self._in_tbody = 0
        self._nav_href = None
        self.feed(html)
        for field in self._fields:
            named = field.get("aria-label") or field.get("id") in self._labels_for
            if not (named or field["wrapped"]):
                self.unnamed.append(field.get("name") or "?")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.styled += "style" in a
        self.scripts += tag == "script"
        if tag == "html":
            self.lang = a.get("lang")
        if tag == "h1":
            self.h1 += 1
        if tag == "nav" and a.get("aria-label") == "Main":
            self._in_nav = 1
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
            self._nav_href = a["href"] if self._in_nav else None
        if tag == "label":
            self._in_label += 1
            if a.get("for"):
                self._labels_for.add(a["for"])
        if tag in ("input", "select", "textarea") and a.get("type") not in ("hidden", "submit"):
            self._fields.append({**a, "wrapped": self._in_label > 0})
        if tag == "tbody":
            self._in_tbody = 1
        if tag == "td" and self._in_tbody and not a.get("data-label"):
            self.unlabelled_cells += 1

    def handle_endtag(self, tag):
        if tag == "label":
            self._in_label -= 1
        if tag == "nav":
            self._in_nav = 0
        if tag == "tbody":
            self._in_tbody = 0
        if tag == "a":
            self._nav_href = None

    def handle_data(self, data):
        if self._nav_href and data.strip():
            self.nav.append((data.strip(), self._nav_href))


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "cairn.db")
    app = create_app(store, public_url=BASE, plans=PLANS)

    def person(name: str) -> SimpleNamespace:
        email = f"{name.lower()}@acme.example"
        signed = store.sign_in("sso", f"sub-{name}", email, name, issuer=ISSUER, join="default")
        view = store.for_account(signed.account_id, signed.user.id)
        cookie = {"cookie": f"__Host-cairn_session={signed.token}"}
        return SimpleNamespace(name=name, cookie=cookie, view=view)

    ada, brook = person("Ada"), person("Brook")
    project = ada.view.create_project("ops", kind="repo")
    ada.view.create_key("nightly-job")
    ada.view.create_set("work", (project.id,))
    ada.view.invite("cy@acme.example")
    with TestClient(app, base_url=BASE) as client:
        for text in (NOTE, NOTE + "\nLumen never retries.\n"):
            posted = client.post(
                "/b/ops/notes", data={"text": text}, headers={**ada.cookie, **SAME_ORIGIN}
            )
            assert posted.status_code in (200, 303), posted.text
        yield SimpleNamespace(store=store, client=client, ada=ada, brook=brook)
    store.close()


def get(world, path: str, who=None):
    world.client.cookies.clear()
    return world.client.get(path, headers=who.cookie if who else {}, follow_redirects=False)


def crawl(world, who, start: str) -> dict[str, tuple[int, str]]:
    """Every page reachable by links from `start`, as {path: (status, html)}."""
    seen: dict[str, tuple[int, str]] = {}
    queue = [start]
    while queue and len(seen) < 400:
        path = queue.pop(0)
        if path in seen:
            continue
        r = get(world, path, who)
        html = r.text if "text/html" in r.headers.get("content-type", "") else ""
        seen[path] = (r.status_code, html)
        if r.status_code == 200 and html:
            for href in Page(html).links:
                href = href.split("#")[0]
                if href.startswith("/") and not href.startswith(("//", "/static/")):
                    queue.append(href)
    return seen


# Navigation


def test_the_main_navigation_is_short_and_the_workspace_page_holds_the_rest(world):
    for who in (world.ada, world.brook):
        page = Page(get(world, "/", who).text)
        assert page.nav[:4] == MAIN_NAV
        assert page.nav[4:] == [(who.name, "/workspaces")]  # then the person; Sign out is a form
        assert len(Page(get(world, "/search", who).text).nav) == 5

    hub = get(world, "/workspace", world.ada)
    assert hub.status_code == 200
    listed = _entries(hub.text)
    assert listed == PARTS
    for _, href in listed:
        assert get(world, href, world.ada).status_code == 200, href

    mine = _entries(get(world, "/workspace", world.brook).text)
    assert mine and set(mine) < set(PARTS)
    assert ("Members", "/members") in mine
    for entry in PARTS:  # shown exactly when it opens for this person
        opens = get(world, entry[1], world.brook).status_code == 200
        assert (entry in mine) == opens, entry
    assert get(world, "/workspace").status_code == 303  # signed out


def _entries(html: str) -> list[tuple[str, str]]:
    """The workspace page's entries: links inside its list, as (text, href)."""
    section = html.split('id="parts"', 1)[1].split("</ul>", 1)[0]
    found = re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', section, re.S)
    return [(re.sub(r"<[^>]+>", "", text).strip(), href) for href, text in found]


# Public pages


def test_the_guides_are_served_from_the_package(world):
    index = get(world, "/docs")
    assert index.status_code == 200
    for name in ("connect", "patterns", "security", "privacy"):
        assert f'href="/docs/{name}"' in index.text
        guide = get(world, f"/docs/{name}")
        assert guide.status_code == 200, name
        page = Page(guide.text)
        assert page.h1 == 1 and page.scripts == 0 and "<h2" in guide.text
    connect = get(world, "/docs/connect").text
    assert "<pre><code" in connect and "claude mcp add" in connect and "```" not in connect
    assert get(world, "/docs/no-such-guide").status_code == 404
    assert get(world, "/docs/../server").status_code in (400, 404)


def test_a_first_visitor_sees_what_cairn_is_and_how_to_start(tmp_path):
    store = Store(tmp_path / "cairn.db")
    offline = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    github = GitHub("client-id", "client-secret")
    sign_in = SignIn(providers=(github,), http=offline, join="personal")
    app = create_app(store, public_url=BASE, sign_in=sign_in)
    with TestClient(app, base_url=BASE) as client:
        home = client.get("/", follow_redirects=False)
        assert home.status_code == 200
        page = Page(home.text)
        assert page.h1 == 1 and page.lang == "en" and page.scripts == 0 and page.styled == 0
        assert f"{BASE}/mcp" in home.text and "claude mcp add" in home.text
        for section in ("connect", "checks", "run"):
            assert f'id="{section}"' in home.text, section
        for href in ("/login", "/docs", "/docs/security", "/docs/privacy"):
            assert href in page.links, href
        assert client.get("/pricing").status_code == 404  # nothing is on sale here
        for href in {h.split("#")[0] for h in page.links if h.startswith("/")} - {""}:
            assert client.get(href, follow_redirects=False).status_code in (200, 303), href
    store.close()


# Every page


def test_every_page_is_whole_and_every_link_leads_somewhere(world):
    visits = {
        "visitor": crawl(world, None, "/docs"),
        "member": crawl(world, world.brook, "/"),
        "owner": crawl(world, world.ada, "/"),
    }
    assert len(visits["owner"]) > 20  # it found the product
    for who, pages in visits.items():
        for path, (status, html) in pages.items():
            allowed = (200, 303) if who == "visitor" else (200,)
            assert status in allowed or path.startswith("/account/export"), (who, path, status)
            if not html:
                continue
            page = Page(html)
            assert page.lang == "en" and page.h1 == 1, (who, path, page.h1)
            assert page.scripts == 0 and page.styled == 0, (who, path)
            assert page.unnamed == [], (who, path, page.unnamed)
            assert page.unlabelled_cells == 0, (who, path)
    for path in ("/keys", "/agents", "/members", "/audit", "/usage", "/b/ops", "/sets"):
        html = visits["owner"][path][1]
        shown = re.sub(r'datetime="[^"]*"', "", html)
        assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", shown), path
    audit = visits["owner"]["/audit"][1]
    assert "<time datetime=" in audit and "note.saved" in audit
