"""B2 acceptance tests: several sign-ins can be under way in one browser.

Why: on a live install a person started a sign-in, and before they came back from the
identity provider a second sign-in was started in the same browser. The server kept one
sign-in under way per browser, in one cookie, so the second start replaced the first, and the
first person's return was refused with "This sign-in is not valid any more". They read it as
"I cannot be signed in twice". That is not the rule: a person may be signed in on as many
browsers and machines as they like. Two tabs did the same thing.

The contract
- Each sign-in a browser starts is its own. A start does not replace what the browser already
  holds: several sign-ins can be under way in one browser at once, and each return from the
  provider is matched to the start it belongs to, in whatever order they come back. At most 5
  are kept under way (a sixth start drops the oldest, and only the oldest), each for the ten
  minutes a sign-in has, each usable once. A finished, refused or cancelled sign-in takes
  only its own state out; the others stay and still work.
- The state cookie keeps its name and its attributes: `__Host-cairn_signin` (Path=/) without
  a brand on https, `__Host-signin` (Path=/) under a brand on https, `signin` (Path=/auth)
  under a brand on plain http; HttpOnly, SameSite=Lax, Secure on https, Max-Age ten minutes.
  Only what the server writes inside it changes, and that stays sealed: a state from another browser, a
  tampered cookie and a return with no cookie are refused as before (400, nothing written,
  no call to the provider). The whole cookie stays under 4,000 bytes whatever the providers'
  names and the `next` addresses are: the oldest are dropped until it fits.
- A return whose state is unknown, used or expired is refused as before: 400, the sign-in
  page with the sentence "This sign-in is not valid any more", no call to the provider,
  nothing written and no cookie set or cleared, whether or not the browser is signed in.
  (A session cookie is SameSite=Strict, so a real return from a provider arrives without it:
  a rule that let a signed-in browser through would hold only in tests.)
- A person is signed in wherever they signed in: the same person signing in from a second
  browser gets a second session and the first stays; signing out in one leaves the other;
  `session-end` for their address ends both.

Everything here runs in process over a real store in a temporary folder, with a stand-in
GitHub and a stand-in OpenID Connect provider; nothing leaves the process.
"""

import base64
import json
import re
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

from cairn import server
from cairn.identity import OIDC, GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web.auth import BRANDED_SESSION_COOKIE, HOST_ONLY, SESSION_COOKIE, branded_cookie
from cairn.web.signin import BRANDED_STATE_COOKIE, STATE_COOKIE, STATE_PATH, STATE_TTL

HOST = "kb.example.test"
BASE = f"https://{HOST}"
LOCAL = "http://127.0.0.1:8765"  # plain http is allowed on a loopback host
BRAND = "Knowledge Base"
STALE = "This sign-in is not valid any more"
ADA, BROOK, CY, DEE, EVE = (f"{who}@example.test" for who in ("ada", "brook", "cy", "dee", "eve"))
LISTED = (ADA, BROOK, CY, DEE)  # Eve is not on the list
PEOPLE = {
    ADA: {"id": 8101, "login": "ada", "name": "Ada Quill"},
    BROOK: {"id": 8102, "login": "brook", "name": "Brook Ng"},
    CY: {"id": 8103, "login": "cy", "name": "Cy Moss"},
    DEE: {"id": 8104, "login": "dee", "name": "Dee Holm"},
    EVE: {"id": 8105, "login": "eve", "name": "Eve Stray"},
}
LONG_NAME = "juniper-field-identity-provider1"  # as long as a provider's name can be
ISSUER = "https://id.example.test/pool-9"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/oauth2/authorize",
    "token_endpoint": f"{ISSUER}/oauth2/token",
    "userinfo_endpoint": f"{ISSUER}/oauth2/userInfo",
    "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
}
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def query(url: str) -> dict:
    return {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}


def refresh_target(html: str) -> str:
    """Where a `<meta http-equiv="refresh" content="0; url=...">` page sends the browser."""
    tag = re.search(r"<meta[^>]+http-equiv=[\"']refresh[\"'][^>]*>", html, re.IGNORECASE)
    assert tag, "no meta refresh on the page"
    content = re.search(r"content=[\"']([^\"']+)[\"']", tag.group(0), re.IGNORECASE).group(1)
    return content.split("=", 1)[1].strip().replace("&amp;", "&")


def cookies_set(response: httpx.Response) -> dict[str, tuple[str, dict]]:
    """Each cookie the response sets: its value and its attributes, in lowercase."""
    found = {}
    for header in response.headers.get_list("set-cookie"):
        jar = SimpleCookie()
        jar.load(header)
        for name, morsel in jar.items():
            attributes = {key.lower(): value for key, value in morsel.items() if value}
            found[name] = (morsel.value, attributes)
    return found


class Providers:
    """A stand-in GitHub and OpenID Connect provider, as an httpx mock. Every call is
    recorded, so a test can say the provider was not asked."""

    def __init__(self) -> None:
        self.codes: dict[str, tuple[str, str, str | None]] = {}  # code -> provider, email, nonce
        self.tokens: dict[str, str] = {}  # access token -> email
        self.calls: list[str] = []

    def approve(self, provider: str, email: str, nonce: str | None = None) -> str:
        """The person approved at the provider: the code it sends them back with."""
        code = f"code-{secrets.token_hex(6)}"
        self.codes[code] = (provider, email, nonce)
        return code

    def _token(self, provider: str, request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        sent = json.loads(body) if body.lstrip().startswith("{") else dict(parse_qsl(body))
        expected = self.codes.pop(sent.get("code", ""), None)
        if expected is None or expected[0] != provider:
            return httpx.Response(400, json={"error": "bad_verification_code"})
        token = f"provider-token-{secrets.token_hex(8)}"
        self.tokens[token] = expected[1]
        answer = {"access_token": token, "token_type": "bearer"}
        if provider == LONG_NAME:
            answer["id_token"] = self._id_token(expected[1], expected[2])
        return httpx.Response(200, json=answer)

    def _id_token(self, email: str, nonce: str | None) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": "field-client-id", "sub": f"sub-{PEOPLE[email]['id']}",
                  "iat": now, "exp": now + 300, "nonce": nonce}  # fmt: skip

        def part(value: dict) -> str:
            return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

        return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part(claims)}.c2lnbmF0dXJl"

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        self.calls.append(url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json=DISCOVERY)
        if url == "https://github.com/login/oauth/access_token":
            return self._token("github", request)
        if url == DISCOVERY["token_endpoint"]:
            return self._token(LONG_NAME, request)
        email = self.tokens.get(request.headers.get("authorization", "").partition(" ")[2])
        if email is None:
            return httpx.Response(401, json={"message": "Bad credentials"})
        person = PEOPLE[email]
        if url == "https://api.github.com/user":
            return httpx.Response(200, json=person)
        if url == "https://api.github.com/user/emails":
            return httpx.Response(200, json=[{"email": email, "primary": True, "verified": True}])
        if url == DISCOVERY["userinfo_endpoint"]:
            claims = {"sub": f"sub-{person['id']}", "name": person["name"], "email": email}
            return httpx.Response(200, json={**claims, "email_verified": "true"})
        return httpx.Response(404)


# The three ways an install can name its cookies: no brand; a brand and the wall on https; a
# brand on plain http, where browsers refuse the __Host- prefix.
SETUPS = {
    "plain": (BASE, {}),
    "branded": (BASE, {"wall": True, "brand_name": BRAND}),
    "branded-http": (LOCAL, {"wall": True, "brand_name": BRAND}),
}


class World:
    """One database, one app with GitHub and a long-named OpenID Connect provider, and the
    browsers made on it."""

    def __init__(self, folder: Path, setup: str = "plain") -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.base, settings = SETUPS[setup]
        self.secure = self.base.startswith("https://")
        branded = "brand_name" in settings
        self.state_cookie = branded_cookie(
            BRANDED_STATE_COOKIE if branded else STATE_COOKIE, self.secure
        )
        self.session_cookie = branded_cookie(
            BRANDED_SESSION_COOKIE if branded else SESSION_COOKIE, self.secure
        )
        self.state_path = "/" if self.state_cookie.startswith(HOST_ONLY) else STATE_PATH
        self.db = folder / "kb.db"
        self.store = Store(self.db)
        self.idp = Providers()
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.idp.handler))
        github = GitHub("github-client-id", "-".join(("gh", "secret", "made", "up")))
        field_secret = "-".join(("field", "secret", "made", "up"))
        field = OIDC(LONG_NAME, "Juniper Field", ISSUER, "field-client-id", field_secret)
        sign_in = SignIn(
            providers=(github, field),
            http=http,
            join="personal",
            allowed_emails=LISTED,
        )
        self.app = create_app(self.store, public_url=self.base, sign_in=sign_in, **settings)
        self.browsers: list[TestClient] = []

    def browser(self) -> TestClient:
        """A browser of its own. The first one runs the app's start-up, which runs once."""
        client = TestClient(self.app, base_url=self.base)
        if not self.browsers:
            client.__enter__()
        self.browsers.append(client)
        return client

    def close(self) -> None:
        if self.browsers:
            self.browsers[0].__exit__(None, None, None)
        self.store.close()

    # A sign-in, step by step

    def start(self, browser: TestClient, provider: str = "github", next_path: str | None = None):
        """GET /auth/<provider>: what was sent to the provider (state, nonce), and the answer."""
        path = f"/auth/{provider}" + (f"?next={quote(next_path, safe='')}" if next_path else "")
        r = browser.get(path, follow_redirects=False)
        assert r.status_code == 302, r.text
        return {"provider": provider, **query(r.headers["location"])}, r

    def finish(self, browser: TestClient, sent: dict, email: str) -> httpx.Response:
        """The person approves at the provider and comes back with the code."""
        code = self.idp.approve(sent["provider"], email, sent.get("nonce"))
        return self.back(browser, sent, f"code={code}")

    def back(self, browser: TestClient, sent: dict, what: str) -> httpx.Response:
        """A return to the callback carrying `what` and the sign-in's own state."""
        address = f"/auth/{sent['provider']}/callback?{what}&state={sent['state']}"
        return browser.get(address, follow_redirects=False)

    def signed_in_as(self, response: httpx.Response) -> str:
        """The address of the person whose session the response sets."""
        value, attributes = cookies_set(response)[self.session_cookie]
        principal = self.store.resolve_session(value)
        assert principal is not None and principal.user_id and attributes.get("path") == "/"
        return self.store.get_user(principal.user_id).email

    def known(self, *emails: str) -> None:
        """Each person has signed in before, elsewhere: a sign-in now lands on its `next`."""
        for email in emails:
            sent, _ = self.start(self.browser())
            assert self.finish(self.browsers[-1], sent, email).status_code == 200

    def state_sent(self, browser: TestClient) -> str | None:
        """The state cookie's value as the browser holds it."""
        for cookie in browser.cookies.jar:
            if cookie.name == self.state_cookie:
                return cookie.value
        return None


@pytest.fixture
def make(tmp_path):
    made: list[World] = []

    def build(setup: str = "plain") -> World:
        world = World(tmp_path / f"{setup}-{len(made)}", setup)
        made.append(world)
        return world

    yield build
    for world in made:
        world.close()


def refused_as_stale(r: httpx.Response) -> None:
    assert r.status_code == 400, (r.status_code, r.text[:300])
    assert STALE in r.text
    assert "set-cookie" not in r.headers, r.headers.get_list("set-cookie")


# 1. Each sign-in a browser starts is its own


@pytest.mark.parametrize("setup", ["plain", "branded"])
@pytest.mark.parametrize("first_back", ["last", "first"])
def test_two_sign_ins_started_in_one_browser_both_finish(make, setup, first_back):
    world = make(setup)
    world.known(ADA, BROOK)
    browser = world.browser()
    ada, _ = world.start(browser, next_path="/keys")
    brook, _ = world.start(browser, LONG_NAME, next_path="/agents")

    order = [(brook, BROOK, "/agents"), (ada, ADA, "/keys")]
    if first_back == "first":
        order.reverse()
    for sent, email, onward in order:
        r = world.finish(browser, sent, email)
        assert r.status_code == 200, (email, r.status_code, r.text[:300])
        assert refresh_target(r.text) == onward, email
        assert world.signed_in_as(r) == email


@pytest.mark.parametrize("setup", ["plain", "branded"])
def test_sign_ins_under_way_finish_in_any_order_and_each_works_once(make, setup):
    world = make(setup)
    world.known(ADA, BROOK, CY)
    browser = world.browser()
    started = [
        world.start(browser, "github", "/b/one")[0],  # Ada
        world.start(browser, LONG_NAME, "/b/two")[0],  # cancelled at the provider
        world.start(browser, "github", "/b/three")[0],  # Brook
        world.start(browser, "github", "/b/four")[0],  # Eve, who is not on the list
        world.start(browser, LONG_NAME, "/b/five")[0],  # Cy
    ]
    ada, cancelled, brook, eve, cy = started

    r = world.back(browser, cancelled, "error=access_denied")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?error=cancelled")
    r = world.finish(browser, eve, EVE)
    assert r.status_code == 403 and world.session_cookie not in cookies_set(r)

    for sent, email, onward in ((brook, BROOK, "/b/three"), (cy, CY, "/b/five")):
        r = world.finish(browser, sent, email)
        assert r.status_code == 200 and refresh_target(r.text) == onward, email
        assert world.signed_in_as(r) == email

    # Used, cancelled or refused: each is gone, and refused as any stale state is.
    for sent in (brook, cy, cancelled, eve):
        before, calls = dump(world.db), len(world.idp.calls)
        refused_as_stale(world.finish(browser, sent, ADA))
        assert dump(world.db) == before and len(world.idp.calls) == calls

    r = world.finish(browser, ada, ADA)  # the one left, finished last
    assert r.status_code == 200 and refresh_target(r.text) == "/b/one"
    assert world.signed_in_as(r) == ADA


@pytest.mark.parametrize("setup", ["plain", "branded"])
def test_a_sixth_start_drops_the_oldest_and_only_the_oldest(make, setup):
    world = make(setup)
    world.known(ADA)
    browser = world.browser()
    started = [world.start(browser, next_path=f"/b/n{n}")[0] for n in range(6)]

    refused_as_stale(world.finish(browser, started[0], ADA))
    for n in (3, 1, 5, 2, 4):
        r = world.finish(browser, started[n], ADA)
        assert r.status_code == 200 and refresh_target(r.text) == f"/b/n{n}", n


@pytest.mark.parametrize("setup", ["plain", "branded"])
def test_the_cookie_stays_under_4000_bytes_and_the_newest_sign_in_always_fits(make, setup):
    world = make(setup)
    world.known(ADA)
    browser = world.browser()
    assert len(LONG_NAME) == 32
    for n in range(7):
        onward = f"/b/n{n}?q=" + "x" * (1000 - len(f"/b/n{n}?q="))
        sent, r = world.start(browser, LONG_NAME, next_path=onward)
        headers = r.headers.get_list("set-cookie")
        header = next(h for h in headers if h.startswith(world.state_cookie))
        assert len(header) < 4000, (n, len(header))
        assert len(world.state_sent(browser)) < 4000
    r = world.finish(browser, sent, ADA)  # the newest: kept whole, its `next` included
    assert r.status_code == 200 and refresh_target(r.text) == onward

    # Five short ways back fit together, whatever the provider's name.
    short = [world.start(browser, LONG_NAME, next_path=f"/b/s{n}")[0] for n in range(5)]
    for n in (4, 0, 2, 1, 3):
        r = world.finish(browser, short[n], ADA)
        assert r.status_code == 200 and refresh_target(r.text) == f"/b/s{n}", n


# 2. The cookie keeps its name and its attributes


@pytest.mark.parametrize("setup", list(SETUPS))
def test_the_state_cookie_keeps_its_name_and_attributes(make, setup):
    world = make(setup)
    world.known(ADA)
    browser = world.browser()
    expected = {"path": world.state_path, "httponly": True, "samesite": "lax"}
    if world.secure:
        expected["secure"] = True

    def same_cookie(r: httpx.Response, *, cleared: bool = False) -> None:
        set_now = cookies_set(r)
        assert world.state_cookie in set_now, (setup, list(set_now))
        assert [n for n in set_now if "signin" in n] == [world.state_cookie]  # no other name
        value, attributes = set_now[world.state_cookie]
        assert "domain" not in attributes
        for name, wanted in expected.items():
            assert attributes.get(name) == wanted, (setup, name, attributes)
        if not world.secure:
            assert "secure" not in attributes
        if cleared:
            gone = attributes.get("max-age") == "0" or "expires" in attributes
            assert gone and not value, attributes
        else:
            assert int(attributes["max-age"]) == STATE_TTL and value, attributes

    # The frozen tests' names (H4, B1).
    names = {"plain": "__Host-cairn_signin", "branded": "__Host-signin", "branded-http": "signin"}
    assert world.state_cookie == names[setup]
    assert world.state_path == {"plain": "/", "branded": "/", "branded-http": "/auth"}[setup]

    started = []
    for n in range(6):
        sent, r = world.start(browser, next_path=f"/b/n{n}")
        same_cookie(r)
        started.append(sent)
    r = world.finish(browser, started[3], ADA)  # others are still under way: they stay
    assert r.status_code == 200
    same_cookie(r)
    for sent in started[1:3] + started[4:5]:
        assert world.finish(browser, sent, ADA).status_code == 200
    r = world.finish(browser, started[5], ADA)  # the last one under way: the cookie goes
    assert r.status_code == 200
    same_cookie(r, cleared=True)
    assert world.state_sent(browser) is None


# The seal: what a browser cannot do


@pytest.mark.parametrize("setup", ["plain", "branded"])
def test_a_state_from_another_browser_a_tampered_cookie_or_none_is_refused(make, setup):
    world = make(setup)
    world.known(ADA)
    mine, other, bare = world.browser(), world.browser(), world.browser()
    before, calls = dump(world.db), len(world.idp.calls)
    sent, _ = world.start(mine)
    world.start(other)  # the other browser has a sign-in of its own under way

    refused_as_stale(world.finish(other, sent, ADA))  # the URL, not the cookie
    refused_as_stale(world.finish(bare, sent, ADA))  # no cookie at all

    held = world.state_sent(mine)
    flipped = held[:10] + ("A" if held[10] != "A" else "B") + held[11:]
    forged = world.browser()
    forged.cookies.set(world.state_cookie, flipped, path=world.state_path)
    refused_as_stale(world.finish(forged, sent, ADA))
    assert dump(world.db) == before and len(world.idp.calls) == calls

    r = world.finish(mine, sent, ADA)  # its own browser still finishes it
    assert r.status_code == 200 and world.signed_in_as(r) == ADA


# 3. A return that is not valid any more is refused, signed in or not


@pytest.mark.parametrize("setup", ["plain", "branded"])
def test_a_stale_return_is_refused_as_today_whether_or_not_the_browser_is_signed_in(make, setup):
    world = make(setup)
    world.known(ADA)
    browser = world.browser()
    used, _ = world.start(browser)
    r = world.finish(browser, used, ADA)
    assert r.status_code == 200
    session = cookies_set(r)[world.session_cookie][0]
    under_way, _ = world.start(browser)  # another under way, so the cookie is there
    made_up = {"provider": "github", "state": secrets.token_urlsafe(32)}

    answers = {}
    for signed in (True, False):
        if not signed:
            browser.cookies.delete(world.session_cookie)
        for name, sent in (("used", used), ("never issued", made_up)):
            before, calls = dump(world.db), len(world.idp.calls)
            r = world.finish(browser, sent, ADA)
            refused_as_stale(r)
            assert dump(world.db) == before and len(world.idp.calls) == calls, name
            answers[(signed, name)] = (r.status_code, r.content)
    # The same page and status, signed in or not.
    for name in ("used", "never issued"):
        assert answers[(True, name)] == answers[(False, name)], name
    assert world.store.resolve_session(session) is not None  # the session was not touched

    r = world.finish(browser, under_way, ADA)  # the one still under way is unharmed
    assert r.status_code == 200 and world.signed_in_as(r) == ADA


# 4. A person is signed in wherever they signed in


@pytest.mark.parametrize("setup", ["plain", "branded"])
def test_one_person_in_two_browsers_has_two_sessions(make, setup, capsys):
    world = make(setup)
    laptop, phone = world.browser(), world.browser()

    def sign_in(browser: TestClient) -> str:
        sent, _ = world.start(browser)
        r = world.finish(browser, sent, ADA)
        assert r.status_code == 200 and world.signed_in_as(r) == ADA
        return cookies_set(r)[world.session_cookie][0]

    first, second = sign_in(laptop), sign_in(phone)
    assert first != second
    one, two = world.store.resolve_session(first), world.store.resolve_session(second)
    assert (one.account_id, one.user_id) == (two.account_id, two.user_id)
    for browser in (laptop, phone):
        assert browser.get("/keys", follow_redirects=False).status_code == 200

    out = laptop.post("/logout", headers=SAME_ORIGIN, follow_redirects=False)
    assert out.status_code == 303
    assert world.store.resolve_session(first) is None
    assert world.store.resolve_session(second) is not None
    assert phone.get("/keys", follow_redirects=False).status_code == 200

    third = sign_in(laptop)
    capsys.readouterr()
    assert server.main(["session-end", "--db", str(world.db), "--email", ADA]) == 0
    assert re.findall(r"\d+", capsys.readouterr().out) == ["2"]
    assert world.store.resolve_session(second) is None
    assert world.store.resolve_session(third) is None
    for browser in (laptop, phone):
        assert browser.get("/keys", follow_redirects=False).status_code == 303
