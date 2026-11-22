"""Unit tests for several sign-ins under way in one browser (B2). They pin what
tests/acceptance/test_b2_sign_ins_under_way.py leaves open: the bound on the cookie's size
with the longest `next` there can be, the expiry of one state among several, and what a state
sealed before B2, or a cookie that is not one this server wrote, does when it comes back."""

from urllib.parse import parse_qs, quote, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

from cairn import oauth
from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web.signin import COOKIE_MAX, NEXT_MAX, SEPARATOR, STATE_TTL, UNDER_WAY
from support.cookie_names import session_cookie, signin_cookie

HOST = "kb.example.test"
BASE = f"https://{HOST}"
STALE = "This sign-in is not valid any more"


def github(request: httpx.Request) -> httpx.Response:
    """GitHub for one made-up person, who approves every time."""
    url = str(request.url).split("?")[0]
    if url == "https://github.com/login/oauth/access_token":
        return httpx.Response(200, json={"access_token": "gho_made_up"})
    if url == "https://api.github.com/user":
        return httpx.Response(200, json={"id": 9101, "login": "ada", "name": "Ada Quill"})
    if url == "https://api.github.com/user/emails":
        email = {"email": "ada@example.test", "primary": True, "verified": True}
        return httpx.Response(200, json=[email])
    return httpx.Response(404)


class Clock:
    """oauth's clock, moved by the test."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def world(tmp_path, monkeypatch):
    """An app, its browser, its clock and the SealedRequests its sign-in seals with."""
    made: list[oauth.SealedRequests] = []

    class Recorded(oauth.SealedRequests):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    clock = Clock()
    monkeypatch.setattr(oauth, "_clock", clock)
    monkeypatch.setattr(oauth, "SealedRequests", Recorded)
    store = Store(tmp_path / "kb.db")
    http = httpx.AsyncClient(transport=httpx.MockTransport(github))
    app = create_app(
        store, public_url=BASE, sign_in=SignIn(providers=(GitHub("id", "secret"),), http=http)
    )
    with TestClient(app, base_url=BASE) as browser:
        probe = TestClient(app, base_url=BASE)  # which of the app's seals is the sign-in's
        probe.get("/auth/github", follow_redirects=False)
        value = probe.cookies.get(signin_cookie("https"))
        (signs,) = [seals for seals in made if seals.open(value, "sign-in:github") is not None]
        yield browser, clock, signs
    store.close()


def start(browser: TestClient, next_path: str | None = None) -> tuple[str, httpx.Response]:
    path = "/auth/github" + (f"?next={quote(next_path, safe='')}" if next_path else "")
    r = browser.get(path, follow_redirects=False)
    assert r.status_code == 302, r.text
    return parse_qs(urlsplit(r.headers["location"]).query)["state"][0], r


def back(browser: TestClient, state: str) -> httpx.Response:
    return browser.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)


def held(browser: TestClient) -> list[str]:
    """The sealed states the browser's cookie holds."""
    value = browser.cookies.get(signin_cookie("https"), path="/")
    return value.split(SEPARATOR) if value else []


def test_the_longest_next_there_can_be_still_fits_and_pushes_the_oldest_out(world):
    browser, _, _ = world
    # The longest `next` the cookie carries, of the character that grows most when sealed (a
    # quote is two characters in JSON, then a third more in base64).
    longest = "/" + '"' * (NEXT_MAX - 1)
    states = []
    for _ in range(UNDER_WAY):
        state, r = start(browser, longest)
        states.append(state)
        header = next(
            h
            for h in r.headers.get_list("set-cookie")
            if h.startswith(f"{signin_cookie('https')}=")
        )
        assert len(header) < 4000 and len(SEPARATOR.join(held(browser))) <= COOKIE_MAX
    kept = len(held(browser))
    assert 1 <= kept < UNDER_WAY  # they cannot all fit: the oldest went first
    for state in states[: UNDER_WAY - kept]:
        r = back(browser, state)
        assert r.status_code == 400 and STALE in r.text
    for state in states[UNDER_WAY - kept :]:
        assert back(browser, state).status_code == 200


def test_one_state_among_several_expires_on_its_own(world):
    browser, clock, _ = world
    early, _ = start(browser)
    clock.now += STATE_TTL / 2
    later, _ = start(browser)
    clock.now += STATE_TTL / 2  # the first is ten minutes old: over

    r = back(browser, early)
    assert r.status_code == 400 and STALE in r.text and "set-cookie" not in r.headers
    assert len(held(browser)) == 2  # still in the cookie, until the next write drops it
    third, _ = start(browser)
    assert len(held(browser)) == 2  # the expired one went, the two alive stay
    assert back(browser, later).status_code == 200
    assert back(browser, third).status_code == 200
    assert held(browser) == []


def test_an_expired_state_is_refused_in_a_signed_in_browser_too(world):
    browser, clock, _ = world
    assert back(browser, start(browser)[0]).status_code == 200  # signed in
    session = browser.cookies.get(session_cookie("https"))
    assert browser.get("/keys", follow_redirects=False).status_code == 200
    late, _ = start(browser)
    clock.now += STATE_TTL

    r = back(browser, late)
    assert r.status_code == 400 and STALE in r.text and "set-cookie" not in r.headers
    assert browser.cookies.get(session_cookie("https")) == session
    assert browser.get("/keys", follow_redirects=False).status_code == 200


def test_a_state_sealed_before_b2_is_taken_as_it_was(world):
    browser, _, states = world
    # The cookie's value before B2: one state, sealed alone with these fields.
    sealed = states.seal(
        {"state": "s" * 43, "nonce": "n" * 43, "next": "/keys", "held": None},
        "sign-in:github",
        ttl=STATE_TTL,
    )
    browser.cookies.set(signin_cookie("https"), sealed, domain=HOST, path="/")
    r = back(browser, "s" * 43)
    assert r.status_code == 200 and "url=/welcome?next=%2Fkeys" in r.text
    assert held(browser) == []  # cleared, as before


@pytest.mark.parametrize(
    "value",
    [
        "not-a-seal",
        "~~~",
        "a.b~c.d~~e.f",
        "eyJzdGF0ZSI6IDF9.c2lnbmF0dXJl",  # a body that decodes, with a signature that does not
        "x" * 3990,
    ],
    ids=["no-dot", "separators", "pieces", "forged", "long"],
)
def test_a_cookie_this_server_did_not_write_is_refused_never_a_500(world, value):
    browser, _, _ = world
    browser.cookies.set(signin_cookie("https"), value, domain=HOST, path="/")
    r = back(browser, "any-state")
    assert r.status_code == 400 and STALE in r.text
    state, _ = start(browser)  # a start drops what it cannot open and keeps going
    assert held(browser) and value not in held(browser)
    assert back(browser, state).status_code == 200


def test_a_state_sealed_by_another_process_is_refused_and_dropped(world):
    browser, _, _ = world
    elsewhere = oauth.SealedRequests()  # another secret: the server before a restart
    sealed = elsewhere.seal({"state": "s" * 43, "nonce": "n", "next": "/", "held": None},
                            "sign-in:github", ttl=STATE_TTL)  # fmt: skip
    browser.cookies.set(signin_cookie("https"), sealed, domain=HOST, path="/")
    r = back(browser, "s" * 43)
    assert r.status_code == 400 and STALE in r.text
    state, _ = start(browser)
    assert len(held(browser)) == 1 and sealed not in held(browser)
    assert back(browser, state).status_code == 200


def test_the_refusal_gives_its_causes_as_the_usual_ones_and_says_nothing_else_is_affected():
    """The sentence a return that is not valid any more gets. Three cases reach it for which
    none of "it expired, was used already, or was started in another browser" is true: a
    sign-in pushed out as the oldest of six, one started before the server restarted, and a
    return with neither a code nor an error. So the causes are given as the usual ones, not
    the only ones. A person who read the old sentence as "I cannot be signed in twice" is
    told the opposite: nothing they are already signed in to is affected."""
    from cairn.web import signin

    said = signin._STALE
    assert said.startswith("This sign-in is not valid any more: usually it took too long,")
    assert "was used already, or was started in another browser. Start again." in said
    assert said.endswith("Nothing you are already signed in to is affected.")
