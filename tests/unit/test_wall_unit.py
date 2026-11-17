"""Unit tests for the wall (B1) and the one scope's two names: how Auth decides what a
browser that is not signed in gets, where `X-Robots-Tag: noindex` goes and where it does not,
what an error is behind the wall, and what OAuth says under a brand. They pin what
tests/acceptance/test_b1_wall.py leaves open."""

import httpx
import pytest
from starlette.testclient import TestClient

from cairn import oauth
from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web.auth import machine_path

BASE = "https://kb.example.test"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


class GitHubFake:
    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_made_up"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"id": 7, "login": "ada", "name": "Ada Quill"})
        if url == "https://api.github.com/user/emails":
            emails = [{"email": "ada@example.test", "primary": True, "verified": True}]
            return httpx.Response(200, json=emails)
        return httpx.Response(404)


@pytest.fixture
def store(tmp_path):
    made = Store(tmp_path / "kb.db")
    yield made
    made.close()


def app(store, **settings):
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake()))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    return create_app(store, public_url=BASE, sign_in=sign_in, **settings)


def sign_in(browser: TestClient) -> None:
    started = browser.get("/auth/github", follow_redirects=False)
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    back = browser.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert back.status_code == 200, back.text


# What is a machine's address


@pytest.mark.parametrize(
    ("path", "machine"),
    [
        ("/api/projects", True),
        ("/mcp", True),
        ("/healthz", True),
        ("/token", True),
        ("/authorize", True),
        ("/.well-known/oauth-authorization-server", True),
        ("/integrations/google/back", True),  # an open path, named by the app
        ("/login", False),
        ("/auth/github", False),
        ("/static/cairn.css", False),
        ("/oauth/consent", False),
        ("/", False),
        ("/apis", False),
    ],
)
def test_what_machines_use_is_named_by_the_address(path, machine):
    assert machine_path(path, frozenset({"/integrations/google/back"})) is machine


# The wall


def test_an_unknown_well_known_address_is_a_page_behind_the_wall(store):
    walled = TestClient(app(store, wall=True), base_url=BASE)
    r = walled.get("/.well-known/no-such-thing", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert r.headers["x-robots-tag"] == "noindex"
    assert walled.post("/.well-known/no-such-thing").status_code == 401
    # ... and the router's 404, as without the wall, once signed in
    sign_in(walled)
    open_one = TestClient(app(store), base_url=BASE)
    open_one.cookies.update(walled.cookies)
    inside = walled.get("/.well-known/no-such-thing")
    assert (inside.status_code, inside.content) == (404, open_one.get(inside.url.path).content)
    assert "x-robots-tag" not in inside.headers


def test_behind_the_wall_any_other_method_is_a_short_plain_401_that_writes_nothing(store):
    walled = TestClient(app(store, wall=True), base_url=BASE)
    r = walled.post("/projects", data={"name": "core"}, headers=SAME_ORIGIN)
    assert r.status_code == 401 and r.text == "Sign in first."
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["x-robots-tag"] == "noindex"


def test_behind_the_wall_an_error_to_a_visitor_is_one_line_of_text(store):
    walled = TestClient(app(store, wall=True), base_url=BASE)
    for method, address, status in (
        ("GET", "/static/no-such-file.css", 404),
        ("GET", "/auth/no-such-provider", 404),
        ("POST", "/auth/github", 405),
        ("POST", "/login", 404),
    ):
        r = walled.request(method, address, headers=SAME_ORIGIN)
        assert r.status_code == status, address
        assert r.headers["content-type"].startswith("text/plain"), address
        assert "<" not in r.text and len(r.text) < 40, address
        assert r.headers["x-robots-tag"] == "noindex", address
    allow = walled.post("/auth/github").headers["allow"]  # the error's headers, in any order
    assert set(allow.split(", ")) == {"GET", "HEAD"}


def test_a_cross_site_post_refused_behind_the_wall_is_not_for_search_engines(store):
    walled = TestClient(app(store, wall=True), base_url=BASE)
    r = walled.post("/login", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and r.headers["x-robots-tag"] == "noindex"


def test_a_signed_in_browser_gets_no_noindex_and_machines_never_do(store):
    walled = TestClient(app(store, wall=True), base_url=BASE)
    for address in ("/healthz", "/api/projects", "/.well-known/oauth-authorization-server"):
        assert "x-robots-tag" not in walled.get(address).headers, address
    sign_in(walled)
    home = walled.get("/", follow_redirects=False)
    assert home.status_code == 200 and "x-robots-tag" not in home.headers


def test_without_the_wall_nothing_carries_noindex(store):
    browser = TestClient(app(store), base_url=BASE)
    for address in ("/", "/login", "/no-such-page", "/docs"):
        assert "x-robots-tag" not in browser.get(address).headers, address


# A brand with no wall: no page that names the software for a visitor


def test_under_a_brand_a_visitor_gets_no_landing_and_no_public_page(store):
    browser = TestClient(app(store, brand_name="Knowledge Base"), base_url=BASE)
    for address in ("/", "/docs", "/docs/connect"):
        r = browser.get(address, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", address
        assert "x-robots-tag" not in r.headers  # no wall: no noindex either
    plain = TestClient(app(store), base_url=BASE)
    assert plain.get("/docs", follow_redirects=False).status_code == 200


def test_under_a_brand_the_come_again_page_names_nothing(store):
    browser = TestClient(app(store, brand_name="Knowledge Base"), base_url=BASE)
    r = browser.get("/new?title=a", headers={"sec-fetch-site": "cross-site"})
    refresh = '<meta http-equiv="refresh" content="0; url=/new?title=a">'
    assert r.status_code == 200 and refresh in r.text
    assert "cairn" not in r.text.lower() and "<title>Knowledge Base</title>" in r.text


# The one scope's two names


def oauth_app(store, **settings):
    return TestClient(app(store, **settings), base_url=BASE)


def test_the_server_says_only_its_current_name_for_the_scope(store):
    plain, branded = oauth_app(store), oauth_app(store, brand_name="Knowledge Base")
    cases = ((plain, "cairn", "cairn"), (branded, "memory", "Knowledge Base"))
    for client, name, resource in cases:
        found = client.get("/.well-known/oauth-protected-resource/mcp").json()
        assert found["scopes_supported"] == [name] and found["resource_name"] == resource
        metadata = client.get("/.well-known/oauth-authorization-server").json()
        assert metadata["scopes_supported"] == [name]
        registered = client.post(
            "/register",
            json={"client_name": "x", "redirect_uris": ["http://127.0.0.1:1/cb"]},
        )
        assert registered.status_code == 201 and registered.json()["scope"] == name


def test_refusals_name_this_server_under_a_brand(store):
    asked = {"client_name": "x", "redirect_uris": ["http://127.0.0.1:1/cb"]}
    only_credentials = {**asked, "grant_types": ["client_credentials"]}
    for client, said in (
        (oauth_app(store), "cairn only offers"),
        (oauth_app(store, brand_name="Knowledge Base"), "this server only offers"),
    ):
        r = client.post("/register", json=only_credentials)
        assert r.status_code == 400 and r.json()["error_description"].startswith(said)


def test_oauth_routes_take_only_the_two_names_of_the_one_scope():
    assert {oauth.SCOPE, oauth.BRANDED_SCOPE} == {"cairn", "memory"}
    with pytest.raises(ValueError, match="cairn or memory"):
        oauth.routes(BASE, None, None, None, scope="everything")  # type: ignore[arg-type]
