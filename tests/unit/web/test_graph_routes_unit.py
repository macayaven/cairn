"""The unregistered route factory validates input and delegates to the graph projection."""

import importlib.util

from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from support.cookie_names import session_cookie


def test_api_graph_route_checks_query_scope_and_unknown_fields(tmp_path):
    assert importlib.util.find_spec("cairn.web.graph_routes") is not None, "graph routes missing"
    from cairn.web.graph_routes import graph_routes

    store = Store(tmp_path / "routes.db")
    try:
        alpha = store.create_project("alpha")
        store.create_project("beta")
        key = store.create_key("reader", projects=[alpha.id], access="read")
        app = create_app(store, public_url="http://127.0.0.1:8765")
        app.router.routes[0:0] = graph_routes()
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            headers = {"x-api-key": key}
            response = client.get("/api/graph?project=alpha&limit=1", headers=headers)
            assert response.status_code == 200
            assert response.json()["nodes"][0]["label"] == "alpha"
            assert client.get("/api/graph?project=beta", headers=headers).status_code == 404
            assert client.get("/api/graph?project=missing", headers=headers).status_code == 404
            for query in (
                "",
                "?project=alpha&limit=0",
                "?project=alpha&limit=21",
                "?project=alpha&limit=abc",
                "?project=alpha&extra=1",
            ):
                assert client.get("/api/graph" + query, headers=headers).status_code == 400
    finally:
        store.close()


def test_route_returns_same_unavailable_response_for_unknown_and_existing_project(tmp_path):
    from cairn.web.graph_routes import graph_routes

    store = Store(tmp_path / "unavailable.db")
    try:
        project = store.create_project("alpha")
        group = store.create_set("group", [project.id])
        key = store.create_key("reader", projects=[group.id], access="read")
        with store._write() as con:
            con.execute(
                "UPDATE project_sets SET project_ids = ? WHERE id = ?",
                ('["' + "a" * 70000 + '"]', group.id),
            )
        app = create_app(store, public_url="http://127.0.0.1:8765")
        app.router.routes[0:0] = graph_routes()
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            headers = {"x-api-key": key}
            existing = client.get("/api/graph?project=alpha", headers=headers)
            missing = client.get("/api/graph?project=missing", headers=headers)
            assert existing.status_code == missing.status_code == 503
            assert existing.json() == missing.json()
    finally:
        store.close()


def test_html_route_renders_accessible_content_with_existing_csp(tmp_path):
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

        app = Starlette(routes=graph_routes(), exception_handlers={RequestError: _on_request_error})
        app.add_middleware(Admit)
        with TestClient(app) as client:
            response = client.get("/b/alpha/graph?extra=ignored")
            assert response.status_code == 200
            assert "<svg" in response.text
            assert "Graph content" in response.text
            assert "desk:1" in response.text
            assert "default-src 'none'" in response.headers["content-security-policy"]
            assert client.get("/b/missing/graph").status_code == 404
    finally:
        store.close()


def test_graph_page_stylesheets_are_available_under_both_branding_modes(tmp_path):
    import re
    from html.parser import HTMLParser

    import httpx
    from starlette.routing import Match

    from cairn.identity import GitHub
    from cairn.server import SignIn
    from cairn.web.auth import Auth
    from cairn.web.graph_routes import graph_routes

    class Stylesheets(HTMLParser):
        def __init__(self):
            super().__init__()
            self.hrefs = []
            self.graph_region = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "link" and attrs.get("rel") == "stylesheet":
                self.hrefs.append(attrs["href"])
            if attrs.get("aria-label") == "Graph diagram":
                self.graph_region = attrs.get("tabindex") == "0"

    for brand in (None, "Example brand"):
        store = Store(tmp_path / ("plain.db" if brand is None else "branded.db"))
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(404)))
        try:
            store.create_project("alpha")
            sign_in = SignIn(providers=(GitHub("example-id", "example-secret"),), http=http)
            app = create_app(
                store, public_url="http://127.0.0.1:8765", sign_in=sign_in, brand_name=brand
            )
            app.router.routes[0:0] = graph_routes()
            # In production the route factory is not registered yet. The test adds it to
            # the actual router and its auth middleware's route-existence check together.
            middleware = next(m for m in app.user_middleware if m.cls is Auth)
            middleware.kwargs["routed"] = lambda scope, routes=app.router.routes: any(
                r.matches(scope)[0] is not Match.NONE for r in routes
            )
            session = store.create_session()
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                client.cookies.set(session_cookie(branded=bool(brand)), session)
                response = client.get("/b/alpha/graph")
                assert response.status_code == 200
                parser = Stylesheets()
                parser.feed(response.text)
                assert parser.graph_region
                assert parser.hrefs
                styles = []
                for href in parser.hrefs:
                    sheet = client.get(href)
                    assert sheet.status_code == 200
                    styles.append(sheet.text)
                # Check the HTTP-delivered styles used by this region, in either brand.
                assert re.search(
                    r"\.graph-diagram\s*\{[^}]*\boverflow-x\s*:\s*(?:auto|scroll)\s*;",
                    "\n".join(styles),
                )
        finally:
            store.close()
