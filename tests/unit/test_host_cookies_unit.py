"""On an https site with no brand, the session cookie and the sign-in state cookie carry the
__Host- prefix, as the branded names do, so a sibling host of the same parent domain cannot
plant either. Over plain http the names stay as they are. A cookie under the old name signs
nobody in, and the answer clears it."""

from datetime import UTC, datetime
from http.cookies import SimpleCookie

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store

HTTPS = "https://kb.example.test"
HTTP = "http://localhost:8765"
START = datetime(2099, 11, 14, 9, 0, tzinfo=UTC)


class GitHubFake:
    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_made_up"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"id": 7, "login": "ada", "name": "Ada"})
        if url == "https://api.github.com/user/emails":
            emails = [{"email": "ada@example.test", "primary": True, "verified": True}]
            return httpx.Response(200, json=emails)
        return httpx.Response(404)


@pytest.fixture
def store(tmp_path):
    made = Store(tmp_path / "kb.db", clock=lambda: START)
    yield made
    made.close()


def app(store, public_url, **settings):
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake()))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    return create_app(store, public_url=public_url, sign_in=sign_in, **settings)


def cookies_set(response) -> dict[str, dict]:
    """Each cookie the response sets, with its value and attributes."""
    found = {}
    for header in response.headers.get_list("set-cookie"):
        jar = SimpleCookie()
        jar.load(header)
        for name, morsel in jar.items():
            kept = {k: v for k, v in morsel.items() if v and k != "expires"}  # the real clock's
            found[name] = {"value": morsel.value, **kept}
    return found


def sign_in(browser) -> tuple[dict, dict]:
    """The cookies the start and the callback set."""
    started = browser.get("/auth/github", follow_redirects=False)
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    back = browser.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert back.status_code == 200, back.text
    return cookies_set(started), cookies_set(back)


def test_on_https_without_a_brand_both_cookies_carry_the_host_prefix(store):
    started, back = sign_in(TestClient(app(store, HTTPS), base_url=HTTPS))
    (state_name,) = started
    assert state_name == "__Host-cairn_signin"
    assert started[state_name]["path"] == "/" and started[state_name]["secure"]
    session = back["__Host-cairn_session"]
    assert session["value"] and session["path"] == "/" and session["secure"]
    assert "domain" not in session
    assert "cairn_session" not in back and "cairn_signin" not in back


def test_over_plain_http_the_names_stay_as_they_are(store):
    started, back = sign_in(TestClient(app(store, HTTP), base_url=HTTP))
    assert list(started) == ["cairn_signin"] and started["cairn_signin"]["path"] == "/auth"
    assert back["cairn_session"]["value"] and "__Host-cairn_session" not in back


def test_a_cookie_under_the_old_name_signs_nobody_in_and_is_cleared(store):
    browser = TestClient(app(store, HTTPS), base_url=HTTPS)
    _, back = sign_in(browser)
    token = back["__Host-cairn_session"]["value"]
    planted = TestClient(app(store, HTTPS), base_url=HTTPS)
    planted.cookies.set("cairn_session", token)  # a live session, under the old name
    r = planted.get("/workspace", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    cleared = cookies_set(r)
    assert cleared == {"cairn_session": {"value": "", "max-age": "0", "path": "/",
                                         "secure": True, "httponly": True,
                                         "samesite": "strict"}}  # fmt: skip


def test_the_old_sign_in_state_cookie_is_cleared_where_it_is_sent(store):
    browser = TestClient(app(store, HTTPS), base_url=HTTPS)
    browser.cookies.set("cairn_signin", "left-over", path="/auth")
    started = browser.get("/auth/github", follow_redirects=False)
    found = cookies_set(started)
    assert found["cairn_signin"]["value"] == "" and found["cairn_signin"]["path"] == "/auth"
    assert found["__Host-cairn_signin"]["value"]


def test_the_old_names_are_not_cleared_where_they_are_today_s(store):
    """Over http, and under a brand, nothing changes: no cookie is cleared that was not."""
    browser = TestClient(app(store, HTTP), base_url=HTTP)
    sign_in(browser)
    r = browser.get("/", follow_redirects=False)
    assert r.status_code == 200 and "set-cookie" not in r.headers
    branded = TestClient(app(store, HTTPS, brand_name="Knowledge Base"), base_url=HTTPS)
    branded.cookies.set("cairn_session", "anything")
    r = branded.get("/workspace", follow_redirects=False)
    assert r.status_code == 303 and "set-cookie" not in r.headers


def test_an_api_request_with_the_old_cookie_gets_no_cookie_back(store):
    browser = TestClient(app(store, HTTPS), base_url=HTTPS)
    browser.cookies.set("cairn_session", "anything")
    r = browser.get("/api/projects")
    assert r.status_code == 401 and "set-cookie" not in r.headers
