"""The graph explorer (G2) with the switch on: the draft's own route and rendering tests,
each on an app made with `graph_explorer=True` or on graph_routes(explorer=True). The
contract is in tests/acceptance/test_g2_graph_explorer.py; with the switch off,
test_graph_routes_unit.py, test_graph_view_unit.py and test_graph_reachable_unit.py pin
the graph as decision 134 has it."""

import base64
import hashlib
import logging
import re
from html import unescape

import httpx
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"


def explorer(store: Store, **settings):
    return create_app(store, public_url=BASE, graph_explorer=True, **settings)


def test_the_page_ignores_what_it_does_not_take_and_keeps_the_csp(tmp_path):
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware

    from cairn.memory import Memory, RequestError
    from cairn.web.app import _on_request_error
    from cairn.web.auth import MEMORY
    from cairn.web.graph_routes import graph_routes

    store = Store(tmp_path / "html.db")
    try:
        project = store.create_project("alpha")
        store.add_note(project.id, "A note.\n", source_ref="desk:1")
        memory = Memory(store, None)

        class Admit(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.scope[MEMORY] = memory
                return await call_next(request)

        app = Starlette(
            routes=graph_routes(explorer=True),
            exception_handlers={RequestError: _on_request_error},
        )
        app.add_middleware(Admit)
        with TestClient(app) as client:
            response = client.get("/b/alpha/graph?focus=notes&extra=ignored")
            assert response.status_code == 200
            assert "<svg" in response.text and "Graph content" in response.text
            assert "desk:1" in response.text
            assert "default-src 'none'" in response.headers["content-security-policy"]
            assert client.get("/b/missing/graph").status_code == 404
    finally:
        store.close()


def test_a_batch_links_the_next_one_and_an_unreadable_continuation_the_start(tmp_path):
    store = Store(tmp_path / "explore.db")
    try:
        project = store.create_project("alpha")
        store.save_page(project.id, "first", "First synthetic page", "", [])
        store.save_page(project.id, "second", "Second synthetic page", "", [])
        with TestClient(explorer(store), base_url=BASE) as client:
            client.cookies.set(session_cookie(), store.create_session())
            response = client.get("/b/alpha/graph?limit=1")
            assert response.status_code == 200
            assert "First synthetic page" in response.text
            assert "Second synthetic page" not in response.text
            assert 'href="/b/alpha/graph?focus=page%3Afirst' in response.text
            link = re.search(r'<a rel="next" href="([^"]+)"', response.text)
            assert link is not None
            following = client.get(unescape(link.group(1)))
            assert following.status_code == 200
            assert "Second synthetic page" in following.text
            assert "First synthetic page" not in following.text
            assert 'rel="next"' not in following.text

            invalid = client.get("/b/alpha/graph?limit=1&after=unusable")
            assert invalid.status_code == 400
            assert '<a href="/b/alpha/graph">Start again</a>' in invalid.text
            assert client.get("/b/alpha/graph?limit=21").status_code == 400
            assert client.get("/b/alpha/graph?focus=page%3Anone").status_code == 404
    finally:
        store.close()


def test_a_note_link_opens_only_its_own_projects_note(tmp_path):
    store = Store(tmp_path / "source.db")
    try:
        alpha = store.create_project("alpha")
        beta = store.create_project("beta")
        note, _ = store.add_note(
            alpha.id,
            "Synthetic note detail.\n",
            source_ref="https://example.test/record",
            via="synthetic import",
        )
        with TestClient(explorer(store), base_url=BASE) as client:
            client.cookies.set(session_cookie(), store.create_session())
            href = f"/b/alpha/graph/notes/{note.id}"
            response = client.get("/b/alpha/graph?focus=notes")
            assert response.status_code == 200
            assert f'href="{href}"' in response.text
            assert "synthetic import" in response.text
            detail = client.get(href)
            assert detail.status_code == 200
            assert "Synthetic note detail." in detail.text
            assert "synthetic import" in detail.text
            assert 'href="https://example.test/record"' in detail.text
            assert client.get(f"/b/{beta.name}/graph/notes/{note.id}").status_code == 404
    finally:
        store.close()


def test_a_long_note_is_read_a_part_at_a_time(tmp_path):
    store = Store(tmp_path / "long.db")
    try:
        project = store.create_project("alpha")
        text = "".join(f"Line {i} of a synthetic log.\n" for i in range(2000))
        note, _ = store.add_note(project.id, text, source_ref="desk:long")
        with TestClient(explorer(store), base_url=BASE) as client:
            client.cookies.set(session_cookie(), store.create_session())
            href, parts = f"/b/alpha/graph/notes/{note.id}", 0
            while href:
                part = client.get(href)
                assert part.status_code == 200 and "<script" not in part.text
                parts += 1
                link = re.search(r'<a rel="next" href="([^"]+)"', part.text)
                href = unescape(link.group(1)) if link else None
            assert parts > 2 and "Line 1999 of a synthetic log." in part.text
            assert 'href="desk:long"' not in part.text and "desk:long" in part.text
    finally:
        store.close()


def test_the_script_is_pinned_and_allowed_on_the_graph_page_only(tmp_path):
    store = Store(tmp_path / "script.db")
    try:
        project = store.create_project("alpha")
        note, _ = store.add_note(project.id, "Synthetic source.\n")
        with TestClient(explorer(store), base_url=BASE) as client:
            client.cookies.set(session_cookie(), store.create_session())
            graph = client.get("/b/alpha/graph")
            assert graph.status_code == 200
            asset = client.get("/static/graph-view.js")
            assert asset.status_code == 200
            assert "javascript" in asset.headers["content-type"]
            digest = hashlib.sha256(asset.content).digest()
            integrity = "sha256-" + base64.b64encode(digest).decode("ascii")
            nonce = re.search(r'nonce="([^"]+)"', graph.text).group(1)
            script = (
                f'<script src="/static/graph-view.js" integrity="{integrity}"'
                f' nonce="{nonce}" defer></script>'
            )
            assert script in graph.text
            policy = graph.headers["content-security-policy"]
            assert policy.endswith(f"; script-src 'nonce-{nonce}'")
            assert "default-src 'none'" in policy and "connect-src" not in policy
            for path in ("/", "/docs/privacy", "/b/alpha", f"/b/alpha/graph/notes/{note.id}"):
                other = client.get(path)
                assert other.status_code == 200
                assert "<script" not in other.text
                assert "script-src" not in other.headers["content-security-policy"]
    finally:
        store.close()


def test_the_explorers_files_are_served_under_both_branding_modes(tmp_path):
    for brand in (None, "Example brand"):
        store = Store(tmp_path / ("plain.db" if brand is None else "branded.db"))
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(404)))
        try:
            store.create_project("alpha")
            sign_in = SignIn(providers=(GitHub("example-id", "example-secret"),), http=http)
            app = explorer(store, sign_in=sign_in, brand_name=brand)
            with TestClient(app, base_url=BASE) as client:
                client.cookies.set(session_cookie(branded=bool(brand)), store.create_session())
                page = client.get("/b/alpha/graph")
                assert page.status_code == 200
                sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', page.text)
                assert any(s.startswith("/static/graph-view.css?v=") for s in sheets)
                for href in [*sheets, "/static/graph-view.js"]:
                    assert client.get(href).status_code == 200, (brand, href)
        finally:
            store.close()


def test_each_request_is_one_access_log_record_named_graph(tmp_path, caplog):
    store = Store(tmp_path / "log.db")
    try:
        project = store.create_project("alpha")
        note, _ = store.add_note(project.id, "# Quill runbook\n\nQuill retries 3 times.\n")
        assert compile_note(store, note, gate=lint_gate).status in ("saved", "unchanged")
        with TestClient(explorer(store), base_url=BASE) as client:
            client.cookies.set(session_cookie(), store.create_session())
            caplog.set_level(logging.INFO, logger="cairn.access")
            for path in (
                "/b/alpha/graph",
                "/b/alpha/graph?focus=notes",
                f"/b/alpha/graph/notes/{note.id}",
            ):
                assert client.get(path).status_code == 200, path
            assert client.get("/b/none/graph").status_code == 404
            records = [
                (r.action, r.project, r.status) for r in caplog.records if r.name == "cairn.access"
            ]
            assert records == [("graph", "alpha", 200)] * 3 + [("graph", None, 404)]
    finally:
        store.close()


def test_the_explorer_links_each_node_to_its_own_page_on_this_server():
    from cairn.web.graph_view import render_explorer

    graph = {
        "nodes": [
            {"id": "project:1", "kind": "project", "label": "Alpha"},
            {"id": "page:x", "kind": "page", "label": "Page", "slug": "x", "focus": "page:x"},
            {
                "id": "claim:0:x",
                "kind": "claim",
                "label": "Claim",
                "slug": "x",
                "claim_id": "claim 1",
                "focus": "claim:0:x",
            },
            {"id": "passage:s", "kind": "passage", "label": "Evidence"},
            {
                "id": "note:1",
                "kind": "note",
                "label": "Note",
                "note_id": "1",
                "source_ref": "https://example.com",
                "via": None,
            },
        ],
        "edges": [{"kind": "cites", "from": "claim:0:x", "to": "passage:s"}],
        "truncated": True,
        "focus": "project",
        "next": None,
    }
    html = render_explorer("alpha", graph, brand=None, person=None).body.decode()
    assert html.count('href="/b/alpha"') >= 2
    assert html.count('href="/b/alpha/p/x#claim%201"') == 2
    assert html.count('href="/b/alpha/p/x#s"') == 2
    assert html.count('href="/b/alpha/graph/notes/1"') == 2
    assert 'href="https://example.com"' not in html
    assert 'href="/b/alpha/graph?focus=page%3Ax"' in html
    assert "1 page shown" in html and 'rel="next"' not in html
    assert "This view shows part of the graph" in html
    assert "Graph legend" in html
    for kind in ("project", "page", "claim", "passage", "note"):
        assert f'data-graph-kind="{kind}"' in html
    assert " style=" not in html


def test_a_stale_note_continuation_is_409_with_a_way_back(tmp_path, monkeypatch):
    """graph_note_detail binds a continuation to the note's row; should that row change, the
    note page answers as a stale page batch does: 409 and a link to the graph's start."""
    from cairn import graph
    from cairn.web import graph_routes

    store = Store(tmp_path / "stale-note.db")
    try:
        project = store.create_project("alpha")
        note, _ = store.add_note(project.id, "Synthetic note.\n")

        def changed(*args, **kwargs):
            raise graph.GraphStale("The note changed; restart its source detail.")

        monkeypatch.setattr(graph_routes, "graph_note_detail", changed)
        with TestClient(explorer(store), base_url=BASE) as client:
            client.cookies.set(session_cookie(), store.create_session())
            stale = client.get(f"/b/alpha/graph/notes/{note.id}?after=made-before")
            assert stale.status_code == 409
            assert '<a href="/b/alpha/graph">Start again</a>' in stale.text
    finally:
        store.close()
