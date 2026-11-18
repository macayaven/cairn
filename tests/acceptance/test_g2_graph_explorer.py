"""G2 acceptance tests: a project's graph you can explore, behind a switch that is off.

The contract goes in the docstrings of src/cairn/graph.py, src/cairn/web/graph_routes.py and
src/cairn/web/graph_view.py; decision 134 is the graph as it is without the switch.

The switch
- `serve --graph-explorer` and `create_app(..., graph_explorer=False)`. Off unless the
  operator gives it, and it needs nothing else switched on. `create_app` refuses anything but
  True or False.

Off (the default): nothing changes
- GET /b/<project>/graph is the page decision 134 describes: the same template output, the
  same query handling (a parameter it does not take is ignored), the same policy header and
  no `<script>`.
- /static/graph-view.js and /static/graph-view.css are 404, with a brand and without, by any
  spelling. /b/<project>/graph/notes/<id> is 404 like any address that is not a page.
- GET /api/graph and the agent's resource `graph://project/<name>/<limit>` answer the same
  bytes in both states.

On
- The graph page is the explorer: two views, Pages and Notes, a search within the view, a
  "Next batch" link while there is more, a legend, and from each page its claims, from each
  claim its passages and their notes, and from each note its own page with its text,
  `source_ref` and `via`. A continuation that is stale is answered 409, and one that cannot
  be read 400, with a page that says so and links the graph's start.
- A project out of the person's reach is answered as one that does not exist, on every
  view, continuation and note page, with no label or count from it.
- The signed-in graph page, and no other response, loads one script: cairn's own file,
  /static/graph-view.js, by a tag that carries its SHA-256 as `integrity` and a nonce made
  for that response; that response's policy adds `script-src 'nonce-...'` and nothing else.
- Without the script every node, link and continuation is in the markup; the controls the
  script needs are `hidden` until it shows them.
- Under a brand the script and its stylesheet are served by their exact names and name
  nothing.
- The script makes no request, evaluates no text, writes no markup or inline style, and
  answers the keyboard; it animates nothing, so a person who asks for reduced motion is
  shown nothing that moves by itself.
- What a request costs is counted in statements and rows, never time: a batch, a note page
  and a claim's passages run as many statements and hand back as many rows in a project of
  8 pages and notes as in one of 48.
"""

import base64
import contextlib
import hashlib
import json
import re
from html.parser import HTMLParser
from importlib.resources import files
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn import server
from cairn.graph import project_graph
from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Claim, Store
from cairn.web import cli
from cairn.web.graph_view import render_graph
from cairn.web.templating import PATH, UI_HEADERS

BASE = "https://cairn.example.test"
ISSUER = "https://id.acme.example/pool-1"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
SCRIPT = files("cairn") / "static" / "graph-view.js"
SHEET = files("cairn") / "static" / "graph-view.css"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
NO_PAGE = "There is no page at this address."


class Page(HTMLParser):
    """What the checks need from one page: its links, scripts, headings and fields."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.links: list[str] = []
        self.scripts: list[dict] = []
        self.styled = self.h1 = 0
        self.lang = None
        self.unnamed: list[str] = []
        self.hidden_controls = 0
        self.svg_nodes: list[str] = []
        self.list_nodes: list[str] = []
        self.svg_edges: list[str] = []
        self.list_edges: list[str] = []
        self.next: list[str] = []
        self._labels_for: set[str] = set()
        self._fields: list[dict] = []
        self._in_label = 0
        self.feed(html)
        for field in self._fields:
            named = field.get("aria-label") or field.get("id") in self._labels_for
            if not (named or field["wrapped"]):
                self.unnamed.append(field.get("name") or "?")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.styled += "style" in a
        if tag == "script":
            self.scripts.append(a)
        if tag == "html":
            self.lang = a.get("lang")
        self.h1 += tag == "h1"
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
            if a.get("rel") == "next":
                self.next.append(a["href"])
        if "data-graph-controls" in a:
            self.hidden_controls += "hidden" in a
        if "data-graph-node" in a:
            where = self.svg_nodes if a.get("data-graph-area") == "svg" else self.list_nodes
            where.append(a["data-graph-node"])
        if "data-graph-edge" in a:
            where = self.svg_edges if a.get("data-graph-area") == "svg" else self.list_edges
            where.append(a["data-graph-edge"])
        if tag == "label":
            self._in_label += 1
            if a.get("for"):
                self._labels_for.add(a["for"])
        if tag in ("input", "select", "textarea") and a.get("type") not in ("hidden", "submit"):
            self._fields.append({**a, "wrapped": self._in_label > 0})

    def handle_endtag(self, tag):
        if tag == "label":
            self._in_label -= 1


def directives(policy: str) -> dict[str, list[str]]:
    parsed = {}
    for part in policy.split(";"):
        words = part.split()
        if words:
            parsed[words[0].lower()] = words[1:]
    return parsed


def integrity() -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(SCRIPT.read_bytes()).digest()).decode()


def unescape(href: str) -> str:
    return href.replace("&amp;", "&")


def build(tmp_path, *, explorer: bool, pages: int = 3, notes: int = 3, name: str = "cairn.db"):
    """Ada owns the workspace and Brook is a member. "ops" is open to members; "vault" is
    Ada's alone. Each holds `pages` pages and `notes` notes; the first page's one claim cites
    a passage of each note."""
    store = Store(tmp_path / name)

    def person(who: str) -> SimpleNamespace:
        email = f"{who.lower()}@acme.example"
        signed = store.sign_in("sso", f"sub-{who}", email, who, issuer=ISSUER, join="default")
        return SimpleNamespace(
            name=who,
            user=signed.user,
            cookie={"cookie": f"cairn_session={signed.token}"},
            view=store.for_account(signed.account_id, signed.user.id),
        )

    ada, brook = person("Ada"), person("Brook")
    made = {}
    for project, everyone, word in (("ops", "write", "Harbour"), ("vault", "none", "Juniper")):
        made[project] = ada.view.create_project(project, everyone=everyone)
        kept = []
        for i in range(notes):
            note, _ = ada.view.add_note(
                made[project].id,
                f"{word} note {i} says the tide is {i} metres.\n",
                title=f"{word} note {i}",
                source_ref=f"https://example.test/{project}/{i}",
                via="synthetic import",
            )
            kept.append(note)
        spans = tuple(ada.view.note_spans(note.id)[0].id for note in kept)
        for i in range(pages):
            claims = [Claim(id="tide", text=f"{word} claim {i}", span_ids=spans if i == 0 else ())]
            ada.view.save_page(made[project].id, f"page-{i:03}", f"{word} page {i}", "", claims)
        made[project + "_notes"] = kept
    app = create_app(store, public_url=BASE, graph_explorer=explorer)
    client = TestClient(app, base_url=BASE)
    return SimpleNamespace(store=store, app=app, client=client, ada=ada, brook=brook, **made)


@pytest.fixture
def on(tmp_path):
    w = build(tmp_path, explorer=True)
    with w.client:
        yield w
    w.store.close()


@pytest.fixture
def off(tmp_path):
    w = build(tmp_path, explorer=False)
    with w.client:
        yield w
    w.store.close()


def get(w, path: str, who=None):
    w.client.cookies.clear()
    return w.client.get(path, headers=who.cookie if who else {}, follow_redirects=False)


# The switch


def test_the_switch_is_off_unless_given_and_takes_true_or_false_only(tmp_path, monkeypatch):
    made = []

    def note(store, **settings):
        made.append(settings.get("graph_explorer"))
        raise SystemExit(0)

    monkeypatch.setattr(cli, "create_app", note)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    for argv in ([], ["--graph-explorer"]):
        with pytest.raises(SystemExit):
            server.main(["serve", "--db", str(tmp_path / "kb.db"), *argv])
    assert made == [False, True]
    store = Store(tmp_path / "app.db")
    try:
        assert create_app(store).state.graph_explorer is False
        assert create_app(store, graph_explorer=True).state.graph_explorer is True
        for wrong in ("yes", 1, None):
            with pytest.raises(ValueError, match="graph_explorer"):
                create_app(store, graph_explorer=wrong)
    finally:
        store.close()


# Off: nothing changes


def test_off_the_graph_page_is_the_one_decision_134_describes(off):
    plain = get(off, "/b/ops/graph", off.ada)
    assert plain.status_code == 200
    at = PATH.set("/b/ops/graph")  # what Auth sets for the page's navigation
    try:
        graph = project_graph(off.ada.view, "ops")
        expected = render_graph("ops", graph, brand=None, person=off.ada.user)
    finally:
        PATH.reset(at)
    assert plain.text == expected.body.decode()
    assert plain.headers["content-security-policy"] == UI_HEADERS["content-security-policy"]
    assert "<script" not in plain.text.lower()
    asked = "/b/ops/graph?focus=notes&after=unreadable&q=tide&extra=1"
    assert get(off, asked, off.ada).text == plain.text  # what it does not take, it ignores
    note = off.ops_notes[0]
    for path in (f"/b/ops/graph/notes/{note.id}", "/b/ops/graph/notes/none"):
        missing = get(off, path, off.ada)
        assert missing.status_code == 404 and NO_PAGE in missing.text, path


def test_off_the_script_and_its_stylesheet_are_not_served_by_any_spelling(tmp_path):
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    sign_in = SignIn(providers=(GitHub("client-id", "client-secret"),), http=http)
    for brand in (None, "Juniper Field"):
        store = Store(tmp_path / f"{brand is None}.db")
        app = create_app(store, public_url=BASE, sign_in=sign_in, brand_name=brand)
        try:
            with TestClient(app, base_url=BASE) as client:
                for path in (
                    "/static/graph-view.js",
                    "/static/Graph-View.js",
                    "/static/graph-view.JS",
                    "/static/fonts/../graph-view.js",
                    "/static/graph-view.css",
                    "/static/GRAPH-VIEW.CSS",
                ):
                    assert client.get(path).status_code == 404, (brand, path)
        finally:
            store.close()


def test_the_api_and_the_agents_resource_answer_the_same_bytes_in_both_states(tmp_path):
    w = build(tmp_path, explorer=False)
    key = w.ada.view.create_key("graph-reader")
    w.store.close()
    answers = []
    for explorer in (False, True):
        store = Store(tmp_path / "cairn.db")
        try:
            app = create_app(store, public_url=BASE, graph_explorer=explorer)
            with TestClient(app, base_url=BASE) as client:
                api = client.get("/api/graph?project=ops&limit=2", headers={"x-api-key": key})
                body = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "resources/read",
                    "params": {"uri": "graph://project/ops/2"},
                }
                read = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
                assert api.status_code == read.status_code == 200
                assert "error" not in read.json()
                answers.append((api.content, dict(api.headers), read.content))
        finally:
            store.close()
    off_answer, on_answer = answers
    assert off_answer[0] == on_answer[0] and off_answer[2] == on_answer[2]
    for header in ("content-type", "cache-control", "content-security-policy"):
        assert off_answer[1].get(header) == on_answer[1].get(header), header


# On: the explorer


def test_on_the_graph_page_is_the_explorer_and_every_step_has_a_link(on):
    first = get(on, "/b/ops/graph?limit=2", on.ada)
    assert first.status_code == 200
    page = Page(first.text)
    assert "Harbour page 0" in first.text and "Harbour page 1" in first.text
    assert "Harbour page 2" not in first.text
    assert 'href="/b/ops/graph?focus=notes&amp;limit=2"' in first.text  # the Notes view
    assert 'name="q"' in first.text and "Graph legend" in first.text
    (following,) = page.next
    second = get(on, unescape(following), on.ada)
    assert second.status_code == 200 and "Harbour page 2" in second.text
    assert "Harbour page 0" not in second.text and not Page(second.text).next

    explore = re.search(r'href="(/b/ops/graph\?focus=page%3Apage-000[^"]*)"', first.text)
    claims = get(on, unescape(explore[1]), on.ada)
    assert claims.status_code == 200 and "Harbour claim 0" in claims.text
    focus = re.search(r'href="(/b/ops/graph\?focus=claim%3A[^"]*)"', claims.text)
    passages, href = "", unescape(focus[1])  # two passages a batch: limit=2 is kept
    while href:
        batch = get(on, href, on.ada)
        assert batch.status_code == 200
        passages += batch.text
        href = unescape(Page(batch.text).next[0]) if Page(batch.text).next else ""
    for i in range(3):
        assert f"Harbour note {i} says the tide is {i} metres." in passages
    note = on.ops_notes[1]
    assert f'href="/b/ops/graph/notes/{note.id}"' in passages

    shown = get(on, f"/b/ops/graph/notes/{note.id}", on.ada)
    assert shown.status_code == 200
    assert "Harbour note 1 says the tide is 1 metres." in shown.text
    assert "https://example.test/ops/1" in shown.text and "synthetic import" in shown.text

    notes = get(on, "/b/ops/graph?focus=notes", on.ada)
    assert all(f"/b/ops/graph/notes/{n.id}" in notes.text for n in on.ops_notes)
    found = get(on, "/b/ops/graph?focus=notes&q=note%202", on.ada)
    assert "Harbour note 2" in found.text and "Harbour note 0" not in found.text
    ignored = get(on, "/b/ops/graph?limit=2&extra=1", on.ada)
    assert ignored.status_code == 200 and "Harbour page 1" in ignored.text


def test_a_stale_continuation_is_409_and_an_unreadable_one_400_with_a_way_back(on):
    batch = get(on, "/b/ops/graph?focus=page%3Apage-000&limit=1", on.ada)
    assert batch.status_code == 200
    on.ada.view.save_page(
        on.ops.id,
        "page-000",
        "Harbour page 0",
        "",
        [
            Claim(id="a", text="Harbour claim a", span_ids=()),
            Claim(id="b", text="Harbour claim b", span_ids=()),
        ],
    )
    on.ada.view.save_page(
        on.ops.id,
        "page-001",
        "Harbour page 1",
        "",
        [
            Claim(id="a", text="Harbour claim a", span_ids=()),
            Claim(id="b", text="Harbour claim b", span_ids=()),
        ],
    )
    first = get(on, "/b/ops/graph?focus=page%3Apage-001&limit=1", on.ada)
    (following,) = Page(first.text).next
    on.ada.view.save_page(
        on.ops.id,
        "page-001",
        "Harbour page 1",
        "",
        [Claim(id="c", text="Harbour claim c", span_ids=())],
    )
    stale = get(on, unescape(following), on.ada)
    assert stale.status_code == 409
    unreadable = get(on, "/b/ops/graph?after=unreadable", on.ada)
    assert unreadable.status_code == 400
    note = get(on, f"/b/ops/graph/notes/{on.ops_notes[0].id}?after=unreadable", on.ada)
    assert note.status_code == 400
    for answer in (stale, unreadable, note):
        assert '<a href="/b/ops/graph">Start again</a>' in answer.text
        assert "<script" not in answer.text.lower()
        assert answer.headers["content-security-policy"] == UI_HEADERS["content-security-policy"]


def test_a_project_out_of_reach_is_answered_as_one_that_does_not_exist(on):
    ada_page = get(on, "/b/vault/graph?focus=page%3Apage-000&limit=1", on.ada)
    claim = re.search(r'href="/b/vault/graph\?(focus=claim%3A[^"]*)"', ada_page.text)[1]
    ada_pages = get(on, "/b/vault/graph?limit=1", on.ada)
    after = re.search(r'rel="next" href="/b/vault/graph\?([^"]*)"', ada_pages.text)[1]
    vault_note = on.vault_notes[0].id
    asked = [
        "",
        "?focus=notes",
        "?focus=page%3Apage-000",
        "?" + unescape(claim),
        "?" + unescape(after),
    ]

    def out_of_reach_for_brook():
        for query in asked:
            hidden = get(on, "/b/vault/graph" + query, on.brook)
            missing = get(on, "/b/nowhere/graph" + query, on.brook)
            assert hidden.status_code == missing.status_code == 404, query
            assert hidden.text.replace("vault", "X") == missing.text.replace("nowhere", "X")
            assert "Juniper" not in hidden.text and "<script" not in hidden.text
        note = get(on, f"/b/vault/graph/notes/{vault_note}", on.brook)
        none = get(on, "/b/nowhere/graph/notes/none", on.brook)
        assert note.status_code == none.status_code == 404
        assert note.text == none.text and "Juniper" not in note.text

    out_of_reach_for_brook()
    # Given the vault, Brook reads it; once it is taken back, every address is refused again,
    # a continuation and a view made while he had it included.
    on.ada.view.set_project_access(on.vault.id, people={on.brook.user.id: "read"})
    for query in asked:
        assert get(on, "/b/vault/graph" + query, on.brook).status_code == 200, query
    assert get(on, f"/b/vault/graph/notes/{vault_note}", on.brook).status_code == 200
    on.ada.view.set_project_access(on.vault.id, people={})
    out_of_reach_for_brook()
    # A note of another project is not reached through one in reach.
    across = get(on, f"/b/ops/graph/notes/{vault_note}", on.ada)
    unknown = get(on, "/b/ops/graph/notes/none", on.ada)
    assert across.status_code == unknown.status_code == 404
    assert across.text == unknown.text and "Juniper" not in across.text
    # Ada reaches the vault, so it is there for her.
    assert get(on, f"/b/vault/graph/notes/{vault_note}", on.ada).status_code == 200


# On: one script, on one page


def test_the_graph_page_loads_one_script_by_its_hash_and_a_nonce_of_its_own(on):
    served = get(on, "/static/graph-view.js")
    assert served.status_code == 200 and served.content == SCRIPT.read_bytes()
    assert "javascript" in served.headers["content-type"]
    assert served.headers["x-content-type-options"] == "nosniff"
    sheet = get(on, "/static/graph-view.css")
    assert sheet.status_code == 200 and sheet.content == SHEET.read_bytes()
    assert sheet.headers["content-type"].startswith("text/css")
    nonces = []
    for _ in range(2):
        r = get(on, "/b/ops/graph", on.ada)
        (tag,) = Page(r.text).scripts
        assert tag["src"] == "/static/graph-view.js" and tag["integrity"] == integrity()
        assert "defer" in tag and len(base64.urlsafe_b64decode(tag["nonce"] + "==")) >= 16
        policy = directives(r.headers["content-security-policy"])
        assert policy.pop("script-src") == [f"'nonce-{tag['nonce']}'"]
        assert policy == directives(UI_HEADERS["content-security-policy"])
        assert r.headers["cache-control"] == "no-store"
        nonces.append(tag["nonce"])
    assert nonces[0] != nonces[1]


def crawl(w, who, start: str) -> dict[str, object]:
    """Every response reachable by links from `start`, as {path: response}."""
    seen: dict[str, object] = {}
    queue = [start]
    while queue and len(seen) < 400:
        path = queue.pop(0)
        if path in seen:
            continue
        r = get(w, path, who)
        seen[path] = r
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            for href in Page(r.text).links:
                href = unescape(href.split("#")[0])
                if href.startswith("/") and not href.startswith(("//", "/static/")):
                    queue.append(href)
    return seen


def test_only_the_graph_page_has_a_script_for_a_visitor_a_member_and_an_owner(on):
    visits = {
        "visitor": crawl(on, None, "/docs"),
        "member": crawl(on, on.brook, "/"),
        "owner": crawl(on, on.ada, "/"),
    }
    graphs = 0
    for who, pages in visits.items():
        for path, r in pages.items():
            allowed = (200, 303) if who == "visitor" else (200,)
            assert r.status_code in allowed or path.startswith("/account/export"), (who, path)
            html = r.text if "text/html" in r.headers.get("content-type", "") else ""
            policy = directives(r.headers.get("content-security-policy", ""))
            is_graph = re.fullmatch(r"/b/[^/?]+/graph(\?.*)?", path) and r.status_code == 200
            if not is_graph:
                assert "script-src" not in policy, (who, path)
                assert "<script" not in html.lower(), (who, path)
                if html:
                    assert Page(html).scripts == [], (who, path)
                continue
            graphs += 1
            page = Page(html)
            (tag,) = page.scripts
            assert tag["src"] == "/static/graph-view.js" and tag["integrity"] == integrity()
            assert html.lower().count("<script") == 1, (who, path)
            assert policy["script-src"] == [f"'nonce-{tag['nonce']}'"], (who, path)
            assert page.lang == "en" and page.h1 == 1 and page.styled == 0, (who, path)
            assert page.unnamed == [], (who, path, page.unnamed)
    assert graphs > 3  # the views, the batches and the claims were walked
    assert any("/graph/notes/" in path for path in visits["owner"])


def test_without_the_script_every_node_link_and_continuation_is_in_the_markup(on):
    for path in (
        "/b/ops/graph?limit=1",
        "/b/ops/graph?focus=notes&limit=1",
        "/b/ops/graph?focus=page%3Apage-000",
    ):
        r = get(on, path, on.ada)
        page = Page(r.text)
        assert page.hidden_controls == 1, path  # shown by the script, and only by it
        assert page.svg_nodes and sorted(page.svg_nodes) == sorted(page.list_nodes), path
        assert sorted(page.svg_edges) == sorted(page.list_edges), path
        if "limit=1" in path:
            assert len(page.next) == 1, path
    claims = get(on, "/b/ops/graph?focus=page%3Apage-000", on.ada).text
    focus = re.search(r'href="(/b/ops/graph\?focus=claim%3A[^"]*)"', claims)
    page = Page(get(on, unescape(focus[1]), on.ada).text)
    for note in on.ops_notes:  # each note of the claim's passages, by a link in the list
        assert f"/b/ops/graph/notes/{note.id}" in page.links


def test_under_a_brand_the_script_is_served_by_its_exact_name_and_names_nothing(tmp_path):
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    sign_in = SignIn(providers=(GitHub("client-id", "client-secret"),), http=http)
    store = Store(tmp_path / "brand.db")
    try:
        store.create_project("ops")
        app = create_app(
            store,
            public_url="http://127.0.0.1:8765",
            sign_in=sign_in,
            brand_name="Juniper Field",
            graph_explorer=True,
        )
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            for path, source in (
                ("/static/graph-view.js", SCRIPT),
                ("/static/graph-view.css", SHEET),
            ):
                served = client.get(path)
                assert served.status_code == 200 and served.content == source.read_bytes()
                said = served.text.lower()
                assert "cairn" not in said and "macayaven" not in said, path
            for path in ("/static/Graph-View.js", "/static/graph-view.JS", "/static/cairn.css"):
                assert client.get(path).status_code == 404, path
            client.cookies.set("session", store.create_session())
            r = client.get("/b/ops/graph")
            assert r.status_code == 200
            (tag,) = Page(r.text).scripts
            assert tag["integrity"] == integrity()
            assert "cairn" not in r.text.lower().replace('content="', "")
    finally:
        store.close()


def test_the_script_reads_as_the_contract_says():
    text = SCRIPT.read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    for forbidden in (
        "fetch",
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "sendBeacon",
        "import",
        "eval",
        "Function(",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "createElement",
        ".style",
        "'style'",
        '"style"',
        "cssText",
        "location",
        "window.open",
        "localStorage",
        "sessionStorage",
        "cookie",
        "requestAnimationFrame",
        "setInterval",
        "animate",
        "transition",
        "postMessage",
        "Worker",
    ):
        assert forbidden not in code, forbidden
    assert not re.search(r"\.on[a-z]+\s*=|setAttribute\(\s*['\"]on", code)  # inline handlers
    assert not re.search(r"setTimeout\(\s*['\"`]", code)  # text run as code
    # It looks only inside the graph it was given.
    assert re.findall(r"document\.\w+", code) == ["document.querySelectorAll"]
    assert "document.querySelectorAll('[data-graph-interactive]')" in code
    assert "window." not in code
    # The keyboard: it moves and zooms the diagram when the diagram has focus, and leaves a
    # key held with Ctrl, Cmd or Alt to the browser.
    assert "keydown" in code
    for modifier in ("ctrlKey", "metaKey", "altKey"):
        assert modifier in code, modifier
    assert "cairn" not in text.lower() and "macayaven" not in text.lower()
    sheet = re.sub(r"/\*.*?\*/", "", SHEET.read_text(encoding="utf-8"), flags=re.S)
    assert "cairn" not in sheet.lower() and "@import" not in sheet and "url(" not in sheet
    assert "transition" not in sheet and "animation" not in sheet


# Cost: statements and rows, never time


QUIET = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}  # Q1's Watch's


class Watch:
    """The statements the store's connection runs during a call and the rows it hands back."""

    def __init__(self, store: Store) -> None:
        self.con = store._con

    @contextlib.contextmanager
    def __call__(self):
        seen = SimpleNamespace(statements=0, rows=0)
        before = self.con.row_factory

        def traced(statement: str) -> None:
            text = statement.lstrip()
            if text and not text.startswith("--") and text.split(None, 1)[0].upper() not in QUIET:
                seen.statements += 1

        def rows(cursor, row):
            seen.rows += 1
            return row if before is None else before(cursor, row)

        self.con.set_trace_callback(traced)
        self.con.row_factory = rows
        try:
            yield seen
        finally:
            self.con.set_trace_callback(None)
            self.con.row_factory = before


def test_a_batch_a_claims_passages_and_a_note_page_cost_the_same_in_a_larger_project(
    tmp_path,
):
    costs = []
    for size in (8, 48):
        w = build(tmp_path, explorer=True, pages=size, notes=size, name=f"{size}.db")
        try:
            with w.client:
                claims = get(w, "/b/ops/graph?focus=page%3Apage-000", w.ada).text
                focus = re.search(r'href="/b/ops/graph\?(focus=claim%3A[^"&]*)', claims)[1]
                paths = [
                    "/b/ops/graph?limit=5",
                    "/b/ops/graph?focus=notes&limit=5",
                    "/b/ops/graph?limit=5&q=page%201",
                    f"/b/ops/graph?{focus}&limit=5",
                    f"/b/ops/graph/notes/{w.ops_notes[3].id}",
                ]
                watch, measured = Watch(w.store), []
                for number, path in enumerate(paths):
                    with watch() as seen:
                        assert get(w, path, w.ada).status_code == 200, path
                    measured.append((number, seen.statements, seen.rows))
                costs.append(measured)
        finally:
            w.store.close()
    assert costs[0] == costs[1]
    assert all(statements > 0 and 0 < rows <= 120 for _, statements, rows in costs[1])
    assert json.dumps(costs)  # numbers only
