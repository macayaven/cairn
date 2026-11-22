"""Unit tests for sign-in in the web service, keys in the UI and the onboarding pages. They pin
what tests/acceptance/test_h4_signin.py leaves open."""

import asyncio
import base64
import json
import logging
import re
import sqlite3
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

import cairn.web.cli
from cairn import oauth, server
from cairn.identity import OIDC, GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web.signin import PROVIDER_CALLS, SIGN_INS, Continuations
from support.cookie_names import session_cookie, signin_cookie

BASE = "https://cairn.example.test"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
ISSUER = "https://id.acme.example"


class GitHubFake:
    """GitHub's endpoints for one person, who approves every time."""

    def __init__(self, user=None, email="ada@example.test") -> None:
        self.user = user or {"id": 7, "login": "ada", "name": "Ada Lovelace"}
        self.email = email
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        self.calls.append(url)
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_secret_token"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json=self.user)
        if url == "https://api.github.com/user/emails":
            return httpx.Response(
                200, json=[{"email": self.email, "primary": True, "verified": True}]
            )
        return httpx.Response(404)


def app_with(store, handler, providers=None, public_url=BASE, **options):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sign_in = SignIn(providers=providers or (GitHub("gh-id", "gh-secret"),), http=http, **options)
    return create_app(store, public_url=public_url, sign_in=sign_in)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


@pytest.fixture
def fake():
    return GitHubFake()


@pytest.fixture
def client(store, fake):
    with TestClient(app_with(store, fake), base_url=BASE) as c:
        yield c


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def sign_in(client, next_path=None, provider="github"):
    path = f"/auth/{provider}" + (f"?next={quote(next_path, safe='')}" if next_path else "")
    started = client.get(path, follow_redirects=False)
    state = query(started.headers["location"])["state"]
    return client.get(f"/auth/{provider}/callback?code=c&state={state}", follow_redirects=False)


def dump(store_path) -> str:
    con = sqlite3.connect(store_path)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


# Configuration


@pytest.mark.parametrize(
    ("providers", "options", "public_url"),
    [
        ((), {}, BASE),  # nobody could sign in
        ((GitHub("a", "b"), GitHub("c", "d")), {}, BASE),  # two providers on one route
        ((GitHub("a", "b"),), {"join": "everyone"}, BASE),
        ((GitHub("a", "b"),), {}, BASE + "/cairn"),  # the routes live at the root
    ],
)
def test_a_sign_in_that_cannot_work_is_refused_at_start(store, providers, options, public_url):
    http = httpx.AsyncClient()
    with pytest.raises(ValueError):
        create_app(
            store,
            public_url=public_url,
            sign_in=SignIn(providers=providers, http=http, **options),
        )


def test_email_domains_are_compared_without_case(store, fake):
    fake.email = "ada@Acme.Example"
    app = app_with(store, fake, join="default", allowed_email_domains=(" ACME.example ",))
    with TestClient(app, base_url=BASE) as client:
        assert sign_in(client).status_code == 200


# The sign-in round trip


def test_the_state_cookie_is_host_only_on_https_and_cleared_after(client):
    started = client.get("/auth/github", follow_redirects=False)
    cookie = next(c for c in started.headers.get_list("set-cookie") if "cairn_signin=" in c)
    assert cookie.startswith("__Host-cairn_signin=") and "path=/;" in cookie.lower()
    state = query(started.headers["location"])["state"]
    done = client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert done.status_code == 200
    cleared = next(c for c in done.headers.get_list("set-cookie") if "cairn_signin=" in c)
    assert "max-age=0" in cleared.lower() or "expires=" in cleared.lower()
    assert done.headers["cache-control"] == "no-store"


def test_an_unknown_provider_has_no_routes(client):
    assert client.get("/auth/gitlab", follow_redirects=False).status_code == 404
    assert client.get("/auth/gitlab/callback?code=c&state=s").status_code == 404


def test_a_provider_error_without_the_state_is_refused_and_its_text_never_shown(client):
    started = client.get("/auth/github", follow_redirects=False)
    state = query(started.headers["location"])["state"]
    assert client.get("/auth/github/callback?error=access_denied&state=nope").status_code == 400
    r = client.get(
        f"/auth/github/callback?error=%3Cb%3Eevil%3C%2Fb%3E&state={state}",
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = client.get(r.headers["location"])
    assert "evil" not in page.text and 'role="alert"' in page.text
    assert "evil" not in client.get("/login?error=%3Cb%3Eevil%3C%2Fb%3E").text


def test_callbacks_without_a_code_settle_nothing_and_call_no_provider(store, fake, monkeypatch):
    made = []

    class Recorded(oauth.SealedRequests):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(oauth, "SealedRequests", Recorded)
    app = app_with(store, fake)

    def at(address: str) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(address, 40000))
        return httpx.AsyncClient(transport=transport, base_url=BASE)

    async def flood() -> None:
        async with at("198.18.0.1") as client:
            started = await client.get("/auth/github")
        state = query(started.headers["location"])["state"]
        cookie = {"cookie": f"{signin_cookie('https')}={started.cookies[signin_cookie('https')]}"}
        each = SIGN_INS - 5  # under the per-address limit, from 400 addresses: 10,000 calls
        for n in range(10_000 // each):
            async with at(f"198.18.{1 + n // 200}.{1 + n % 200}") as client:
                for m in range(each):  # the provider says no, or someone just calls it
                    error = "&error=access_denied" if m % 2 else ""
                    r = await client.get(
                        f"/auth/github/callback?state={state}{error}", headers=cookie
                    )
                    assert r.status_code == (303 if error else 400), (n, m)
        async with at("198.18.9.9") as client:
            page = await client.get(f"/auth/github/callback?state={state}", headers=cookie)
        assert 'role="alert"' in page.text and "<h1>" in page.text  # the sign-in page, with why
        cleared = next(c for c in page.headers.get_list("set-cookie") if f"{signin_cookie()}=" in c)
        assert "max-age=0" in cleared.lower() or "expires=" in cleared.lower()

    asyncio.run(flood())
    assert made and all(requests.settled == 0 for requests in made)
    assert fake.calls == []


def test_sign_in_starts_and_callbacks_are_limited_per_client_address(store, fake, monkeypatch):
    made = []

    class Recorded(oauth.SealedRequests):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(oauth, "SealedRequests", Recorded)
    app = app_with(store, fake)
    limit = SIGN_INS

    async def run() -> None:
        def at(address: str) -> httpx.AsyncClient:
            transport = httpx.ASGITransport(app=app, client=(address, 40000))
            return httpx.AsyncClient(transport=transport, base_url=BASE)

        async with at("203.0.113.5") as one, at("203.0.113.6") as other:
            started = await one.get("/auth/github")  # one start of the address's allowance
            state = query(started.headers["location"])["state"]
            for _ in range(limit):  # the callback's allowance, spent on a made-up state
                assert (await one.get("/auth/github/callback?code=c&state=x")).status_code == 400
            over = await one.get(f"/auth/github/callback?code=c&state={state}")
            assert over.status_code == 429 and over.headers["retry-after"] == "60"
            assert 'role="alert"' in over.text and "<h1>" in over.text  # the sign-in page
            assert fake.calls == [] and all(r.settled == 0 for r in made)  # nothing more done
            for _ in range(limit - 1):
                assert (await one.get("/auth/github")).status_code == 302
            refused = await one.get("/auth/github")
            assert refused.status_code == 429 and refused.headers["retry-after"] == "60"
            # Another address has its own allowance.
            started = await other.get("/auth/github")
            state = query(started.headers["location"])["state"]
            signed = await other.get(f"/auth/github/callback?code=c&state={state}")
            assert signed.status_code == 200 and fake.calls

    asyncio.run(run())


def test_token_exchanges_with_the_provider_have_a_ceiling_whoever_asks(store, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="cairn.signin")
    exchanges = []

    def provider(request: httpx.Request) -> httpx.Response:  # refuses every code, and counts
        if str(request.url).startswith("https://github.com/login/oauth/access_token"):
            exchanges.append(request)
            return httpx.Response(400, json={"error": "bad_verification_code"})
        return httpx.Response(404)

    made = []

    class Recorded(oauth.SealedRequests):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(oauth, "SealedRequests", Recorded)
    app = app_with(store, provider)
    addresses = [f"198.18.{n // 250}.{1 + n % 250}" for n in range(700)]

    def at(address: str) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(address, 40000))
        return httpx.AsyncClient(transport=transport, base_url=BASE)

    async def run() -> list[httpx.Response]:
        begun = []
        for address in addresses:  # 700 sign-ins started, one per address
            async with at(address) as client:
                started = await client.get("/auth/github")
                begun.append((query(started.headers["location"])["state"], started.cookies))
        answers = []
        for address, (state, cookies) in zip(addresses, begun, strict=True):
            async with at(address) as client:
                client.cookies = cookies
                answers.append(await client.get(f"/auth/github/callback?code=c&state={state}"))
        return answers

    answers = asyncio.run(run())  # well inside one minute
    assert len(exchanges) == PROVIDER_CALLS == 600
    over = [r for r in answers if r.status_code == 429]
    assert len(over) == 100
    assert all(r.headers["retry-after"] and 'role="alert"' in r.text for r in over)
    assert sum(requests.settled for requests in made) == 600  # the refused settled nothing
    assert len([r for r in caplog.records if r.name == "cairn.signin"]) == 1  # once a minute


SHARED = "/new?title=Shared+page&text=Remember+this+line&url=https%3A%2F%2Fnews.example%2Fa"


def test_a_signed_out_share_comes_back_with_its_text_after_a_provider_sign_in(store, client):
    signed_out = client.get(SHARED, follow_redirects=False)
    assert signed_out.status_code == 303
    assert signed_out.headers["location"] == "/login?" + urlencode({"next": SHARED})
    login = client.get(signed_out.headers["location"]).text
    link = re.search(r'href="(/auth/github[^"]*)"', login).group(1).replace("&amp;", "&")
    started = client.get(link, follow_redirects=False)
    state = query(started.headers["location"])["state"]
    done = client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    target = re.search(r'url=([^"]+)"', done.text).group(1).replace("&amp;", "&")
    assert target == f"/welcome?next={quote(SHARED, safe='')}"  # a new person: welcome first
    # (with no project yet the composer offers a new project instead: M1's rule)
    account = store.resolve_session(client.cookies[session_cookie("https")]).account_id
    store.for_account(account).create_project("inbox")
    welcome = client.get(target).text
    onward = re.search(r"continue there", welcome) and re.search(r'href="(/new\?[^"]*)"', welcome)
    composer = client.get(onward.group(1).replace("&amp;", "&"))
    assert "Remember this line" in composer.text and "news.example/a" in composer.text
    assert "Ada Lovelace" in composer.text  # the composer names the person too

    client.post("/logout", headers=SAME_ORIGIN)
    client.cookies.clear()
    again = sign_in(client, next_path=SHARED)  # returning: straight back to the composer
    assert re.search(r'url=([^"]+)"', again.text).group(1).replace("&amp;", "&") == SHARED


def test_a_signed_out_share_comes_back_after_a_key_login(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        key = store.create_key("phone")
        store.create_project("inbox")
        with TestClient(create_app(store), base_url="http://127.0.0.1:8765") as client:
            r = client.get(SHARED, follow_redirects=False)
            assert r.headers["location"] == "/login?" + urlencode({"next": SHARED})
            assert (
                f'value="{SHARED.replace("&", "&amp;")}"' in client.get(r.headers["location"]).text
            )
            back = client.post("/login", data={"key": key, "next": SHARED}, follow_redirects=False)
            assert back.headers["location"] == SHARED
            assert "Remember this line" in client.get(SHARED).text
            # Other pages still go to plain /login (frozen by W2).
            assert client.post("/logout", headers=SAME_ORIGIN).status_code == 200
            client.cookies.clear()
            assert client.get("/setup", follow_redirects=False).headers["location"] == "/login"
    finally:
        store.close()


def test_a_way_back_too_long_for_the_state_cookie_is_held_whole_on_the_server(client, tmp_path):
    long_share = "/new?text=" + "x" * 3000
    started = client.get(f"/auth/github?next={quote(long_share, safe='')}", follow_redirects=False)
    cookie = next(c for c in started.headers.get_list("set-cookie") if f"{signin_cookie()}=" in c)
    assert len(cookie) < 1500  # the cookie carries a handle, not the way back
    state = query(started.headers["location"])["state"]
    done = client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert f"url=/welcome?next={quote(long_share, safe='')}" in done.text
    assert "xxxxxxxx" not in dump(tmp_path / "cairn.db")


def test_a_way_back_over_8_kib_keeps_only_its_path(client):
    huge = "/new?text=" + "x" * 9000
    started = client.get(f"/auth/github?next={quote(huge, safe='')}", follow_redirects=False)
    state = query(started.headers["location"])["state"]
    done = client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert "url=/welcome?next=%2Fnew" in done.text and "xxx" not in done.text


def test_held_ways_back_are_bounded_single_use_and_expire():
    now = [1000.0]
    held = Continuations(limit=3, size=50, ttl=600, clock=lambda: now[0])
    assert held.hold("/" + "x" * 50) is None  # over the size: not held
    first, second, third = (held.hold(f"/p{n}") for n in range(3))
    assert held.take(second) == "/p1"
    assert held.take(second) == "/"  # once only
    fourth, fifth = held.hold("/p3"), held.hold("/p4")
    assert len(held) == 3  # full: the oldest (first) was dropped
    assert held.take(first) == "/" and held.take(third) == "/p2"
    now[0] += 600
    assert held.take(fourth) == "/"  # ten minutes on: gone
    assert len(held) == 0 and held.take(fifth) == "/"
    assert held.take("not-a-handle") == "/" and held.take("") == "/"


def long_consent_url(client) -> str:
    """The consent page of an agent whose authorization request is long (a long `state`)."""
    registered = client.post(
        "/register", json={"client_name": "agent", "redirect_uris": ["http://127.0.0.1:9/cb"]}
    ).json()
    params = {
        "response_type": "code",
        "client_id": registered["client_id"],
        "redirect_uri": "http://127.0.0.1:9/cb",
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "code_challenge_method": "S256",
        "state": "s" * 2200,
    }
    to_consent = client.get("/authorize", params=params, follow_redirects=False)
    consent = to_consent.headers["location"]
    assert consent.startswith("/oauth/consent?") and len(consent) >= 3000
    return consent


def approve(client, consent: str) -> str:
    """Approve on the consent page; returns the state the agent gets back."""
    page = client.get(consent)
    assert page.status_code == 200, page.text
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page.text))
    decided = client.post(
        "/oauth/consent",
        data={**fields, "decision": "approve"},
        headers=SAME_ORIGIN,
        follow_redirects=False,
    )
    assert decided.status_code == 303, decided.text
    return query(decided.headers["location"])["state"]


def test_a_long_consent_survives_a_provider_sign_in(store, client):
    consent = long_consent_url(client)
    to_login = client.get(consent, follow_redirects=False)
    assert to_login.status_code == 303
    login = client.get(to_login.headers["location"]).text
    link = re.search(r'href="(/auth/github[^"]*)"', login).group(1).replace("&amp;", "&")
    started = client.get(link, follow_redirects=False)
    state = query(started.headers["location"])["state"]
    done = client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    target = re.search(r'url=([^"]+)"', done.text).group(1).replace("&amp;", "&")
    assert query(target)["next"] == consent  # a new person: welcome first, then on
    assert approve(client, consent) == "s" * 2200


def test_a_long_consent_survives_a_key_login(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        key = store.create_key("laptop")
        base = "http://127.0.0.1:8765"
        with TestClient(create_app(store, public_url=base), base_url=base) as client:
            consent = long_consent_url(client)
            to_login = client.get(consent, follow_redirects=False)
            assert to_login.status_code == 303
            form = client.get(to_login.headers["location"]).text
            carried = re.search(r'name="next" value="([^"]*)"', form).group(1)
            back = client.post(
                "/login",
                data={"key": key, "next": carried.replace("&amp;", "&")},
                follow_redirects=False,
            )
            assert back.headers["location"] == consent
            assert approve(client, consent) == "s" * 2200
    finally:
        store.close()


def test_the_way_back_is_kept_when_a_new_person_lands_on_welcome(client):
    consent = "/oauth/consent?request=abc.def"
    r = sign_in(client, next_path=consent)
    assert r.status_code == 200
    assert f"url=/welcome?next={quote(consent, safe='')}" in r.text.replace("&amp;", "&")
    welcome = client.get(f"/welcome?next={quote(consent, safe='')}")
    assert f'href="{consent}"' in welcome.text
    elsewhere = client.get("/welcome?next=https://evil.example/")
    assert "evil.example" not in elsewhere.text


def test_a_sign_in_that_took_an_invitation_lands_on_the_workspace_not_the_used_link(store, fake):
    olive = store.sign_in("acme", "o", "olive@example.test", "Olive", issuer=ISSUER, join="default")
    owner = store.for_account(olive.account_id, olive.user.id)
    app = app_with(store, fake, join="default", allowed_email_domains=("example.test",))
    with TestClient(app, base_url=BASE) as client:
        assert sign_in(client).status_code == 200  # Ada joins the default workspace
        ada = next(m for m in owner.list_members() if m.email == "ada@example.test")
        owner.remove_member(ada.user_id)
        token = owner.invite("ada@example.test").token
        r = sign_in(client, next_path=f"/join/{token}")  # the sign-in takes the invitation
        assert r.status_code == 200
        assert token not in r.text  # the link is used: it would be a 404
        assert 'content="0; url=/"' in r.text
        assert client.get(f"/join/{token}").status_code == 404
        assert client.get("/", follow_redirects=False).status_code == 200


def test_the_login_page_carries_the_way_back_to_every_provider(client):
    page = client.get("/login?next=/oauth/consent%3Frequest%3Dabc").text
    assert 'href="/auth/github?next=%2Foauth%2Fconsent%3Frequest%3Dabc"' in page
    assert 'href="/auth/github"' in client.get("/login").text


def test_a_provider_that_cannot_be_reached_at_start_is_a_failed_sign_in(store, tmp_path):
    def down(request):
        raise httpx.ConnectError("down", request=request)

    acme = OIDC("acme", "Acme", ISSUER, "acme-id", "acme-secret")
    before = dump(tmp_path / "cairn.db")
    with TestClient(app_with(store, down, providers=(acme,)), base_url=BASE) as client:
        r = client.get("/auth/acme", follow_redirects=False)
    assert r.status_code == 502 and "acme-secret" not in r.text
    assert dump(tmp_path / "cairn.db") == before


def test_a_person_removed_from_their_workspace_is_signed_out(store, client, tmp_path):
    assert sign_in(client).status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 200
    with sqlite3.connect(tmp_path / "cairn.db") as con:
        con.execute("DELETE FROM memberships")
    con.close()
    assert client.get("/", follow_redirects=False).status_code == 200  # the landing page
    assert "Ada Lovelace" not in client.get("/").text
    assert client.get("/keys", follow_redirects=False).status_code == 303


class OIDCFake:
    """An OpenID Connect issuer at `issuer` that signs in one person, `subject`."""

    def __init__(self, issuer: str, subject: str, email: str) -> None:
        self.issuer, self.subject, self.email = issuer, subject, email
        self.nonce = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == f"{self.issuer}/.well-known/openid-configuration":
            names = ("authorization", "token", "userinfo")
            endpoints = {f"{n}_endpoint": f"{self.issuer}/{n}" for n in names}
            return httpx.Response(200, json={"issuer": self.issuer, **endpoints})
        if url == f"{self.issuer}/token":
            claims = {"iss": self.issuer, "aud": "acme-id", "sub": self.subject}
            claims |= {"exp": 4_000_000_000, "nonce": self.nonce}
            payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
            return httpx.Response(
                200, json={"access_token": "at", "id_token": f"e30.{payload}.c2ln"}
            )
        if url == f"{self.issuer}/userinfo":
            person = {"sub": self.subject, "email": self.email, "email_verified": True}
            return httpx.Response(200, json={**person, "name": "Ada"})
        return httpx.Response(404)


def oidc_sign_in(client, fake: OIDCFake, provider="sso"):
    started = client.get(f"/auth/{provider}", follow_redirects=False)
    sent = query(started.headers["location"])
    fake.nonce = sent["nonce"]
    return client.get(
        f"/auth/{provider}/callback?code=c&state={sent['state']}", follow_redirects=False
    )


def test_a_provider_replaced_under_the_same_name_cannot_take_over_its_people(store, tmp_path):
    old, new = "https://id.acme.example/old", "https://id.acme.example/new"
    first = OIDCFake(old, "s-1", "ada@acme.example")
    sso = OIDC("sso", "SSO", old, "acme-id", "acme-secret")
    with TestClient(app_with(store, first, providers=(sso,)), base_url=BASE) as client:
        assert oidc_sign_in(client, first).status_code == 200
    before = dump(tmp_path / "cairn.db")

    # The operator points "sso" at another issuer, where someone else has the same subject.
    second = OIDCFake(new, "s-1", "mallory@elsewhere.example")
    replaced = OIDC("sso", "SSO", new, "acme-id", "acme-secret")
    with TestClient(app_with(store, second, providers=(replaced,)), base_url=BASE) as client:
        r = oidc_sign_in(client, second)
        assert r.status_code == 403 and session_cookie() not in client.cookies
        assert "provider" in r.text and "operator" in r.text
    assert dump(tmp_path / "cairn.db") == before


def test_the_provider_token_and_secret_never_reach_the_browser_or_the_database(client, tmp_path):
    r = sign_in(client)
    seen = r.text + str(r.headers) + client.get("/welcome").text
    assert "gho_secret_token" not in seen and "gh-secret" not in seen
    assert "gho_secret_token" not in dump(tmp_path / "cairn.db")


# Pages for a signed-in person


def test_the_consent_page_names_the_person_as_well_as_the_workspace(store, fake):
    fake.email = "ada@acme.example"
    app = app_with(store, fake, join="default", allowed_email_domains=("acme.example",))
    with TestClient(app, base_url=BASE) as client:
        sign_in(client)
        registered = client.post(
            "/register",
            json={"client_name": "agent", "redirect_uris": ["http://127.0.0.1:9/cb"]},
        ).json()
        params = {
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": "http://127.0.0.1:9/cb",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "code_challenge_method": "S256",
            "state": "s",
        }
        to_consent = client.get("/authorize", params=params, follow_redirects=False)
        page = client.get(to_consent.headers["location"]).text
    assert "Ada Lovelace" in page and "default" in page


def test_pages_opened_with_an_api_key_name_no_person(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        key = store.create_key("laptop")
        with TestClient(create_app(store), base_url="http://127.0.0.1:8765") as client:
            client.post("/login", data={"key": key})
            home = client.get("/").text
            assert 'class="person"' not in home
            for path in ("/keys", "/welcome", "/setup"):
                assert client.get(path).status_code == 200, path
    finally:
        store.close()


# Keys in the UI


def test_keys_are_managed_from_a_session_opened_with_a_key(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        key = store.create_key("laptop")
        with TestClient(create_app(store), base_url="http://127.0.0.1:8765") as client:
            client.post("/login", data={"key": key})
            listed = client.get("/keys").text
            assert "laptop" in listed and key not in listed
            too_long = client.post("/keys", data={"label": "x" * 81}, headers=SAME_ORIGIN)
            assert too_long.status_code == 400
            made = client.post("/keys", data={"label": " ci "}, headers=SAME_ORIGIN)
            assert made.status_code == 200 and made.headers["cache-control"] == "no-store"
            assert [k.label for k in store.list_keys()] == ["laptop", "ci"]
            missing = client.post("/keys/nobody/revoke", headers=SAME_ORIGIN)
            assert missing.status_code == 404
            slash = store.create_key("team/ci")
            r = client.post("/keys/team%2Fci/revoke", headers=SAME_ORIGIN, follow_redirects=False)
            assert r.status_code == 303 and store.resolve_key(slash) is None
    finally:
        store.close()


def test_only_an_owner_or_admin_manages_a_shared_workspaces_keys(store, fake, tmp_path):
    fake.email = "ada@acme.example"
    app = app_with(store, fake, join="default", allowed_email_domains=("acme.example",))
    with TestClient(app, base_url=BASE) as client:
        sign_in(client)  # the first person owns the workspace
        made = client.post("/keys", data={"label": "ada-ci"}, headers=SAME_ORIGIN)
        assert made.status_code == 200
        owners_page = client.get("/keys").text
        assert 'action="/keys"' in owners_page and "/keys/ada-ci/revoke" in owners_page
        client.post("/logout", headers=SAME_ORIGIN)
        client.cookies.clear()

        fake.user, fake.email = {"id": 8, "login": "brook", "name": "Brook"}, "brook@acme.example"
        sign_in(client)  # a member
        assert [m.role for m in store.list_members()] == ["owner", "member"]
        before = dump(tmp_path / "cairn.db")
        listed = client.get("/keys")
        assert listed.status_code == 200 and "ada-ci" in listed.text
        assert 'action="/keys"' not in listed.text and "/revoke" not in listed.text
        refused = client.post("/keys", data={"label": "brook-ci"}, headers=SAME_ORIGIN)
        assert refused.status_code == 403 and "owner or an admin" in refused.text
        revoke = client.post("/keys/ada-ci/revoke", headers=SAME_ORIGIN, follow_redirects=False)
        assert revoke.status_code == 403 and "owner or an admin" in revoke.text
        assert dump(tmp_path / "cairn.db") == before

        brook = store.list_members()[1].user_id
        with sqlite3.connect(tmp_path / "cairn.db") as con:  # until roles can be changed (T1)
            con.execute("UPDATE memberships SET role = 'admin' WHERE user_id = ?", (brook,))
        con.close()
        assert client.post("/keys", data={"label": "brook-ci"}, headers=SAME_ORIGIN).is_success
        assert 'action="/keys"' in client.get("/keys").text


def test_a_views_role_is_its_persons_in_its_workspace(store):
    clara = store.sign_in("acme", "c", "clara@acme.example", "Clara", issuer=ISSUER, join="default")
    dev = store.sign_in("acme", "d", "dev@acme.example", "Dev", issuer=ISSUER, join="default")
    assert store.for_account(clara.account_id, user_id=clara.user.id).role() == "owner"
    assert store.for_account(dev.account_id, user_id=dev.user.id).role() == "member"
    assert store.for_account(dev.account_id).role() is None  # a key acts for no person


def test_keys_of_one_workspace_are_out_of_reach_of_another(store, fake):
    other = store.for_account(store.create_account("Other").id)
    theirs = other.create_key("theirs")
    with TestClient(app_with(store, fake), base_url=BASE) as client:
        sign_in(client)
        assert "theirs" not in client.get("/keys").text
        r = client.post("/keys/theirs/revoke", headers=SAME_ORIGIN)
        assert r.status_code == 404 and store.resolve_key(theirs) is not None


# Setup


def test_setup_shows_the_one_url_commands_when_agents_can_connect_by_url(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        key = store.create_key("laptop")
        app = create_app(store, public_url="http://127.0.0.1:8765")
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            client.post("/login", data={"key": key})
            text = client.get("/setup").text
        assert "claude mcp add --transport http cairn http://127.0.0.1:8765/mcp" in text
        assert "codex mcp add cairn --url http://127.0.0.1:8765/mcp" in text
        assert "cairn.env" in text  # self-hosted keeps the env-file route
    finally:
        store.close()


# The command line


SIGN_IN_ENV = {
    "CAIRN_GITHUB_CLIENT_ID": "gh-id-from-env",
    "CAIRN_GITHUB_CLIENT_SECRET": "gh-secret-from-env",
    "CAIRN_OIDC_ISSUER": ISSUER,
    "CAIRN_OIDC_CLIENT_ID": "acme-id-from-env",
    "CAIRN_OIDC_CLIENT_SECRET": "acme-secret-from-env",
    "CAIRN_OIDC_LABEL": "Acme SSO",
}


def serve(monkeypatch, tmp_path, argv, env=SIGN_IN_ENV):
    """Run `serve` with uvicorn replaced; returns (exit code, what the app answered)."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    seen = {}

    def run(app, **options):
        with TestClient(app, base_url=BASE) as client:
            seen["login"] = client.get("/login").text
            seen["post"] = client.post("/login", data={"key": "k"}, headers=SAME_ORIGIN)
            seen["start"] = client.get("/auth/github", follow_redirects=False)

    monkeypatch.setattr(uvicorn, "run", run)
    db = ["--db", str(tmp_path / "cairn.db"), "--public-url", BASE]
    return server.main(["serve", *db, *argv]), seen


def test_serve_turns_sign_in_on_from_the_environment(tmp_path, monkeypatch):
    argv = ["--sign-in", "github", "--sign-in", "oidc", "--join", "default"]
    argv += ["--allowed-email-domain", "acme.example"]
    code, seen = serve(monkeypatch, tmp_path, argv)
    assert code == 0
    assert 'href="/auth/github"' in seen["login"] and 'href="/auth/sso"' in seen["login"]
    assert "Acme SSO" in seen["login"] and seen["post"].status_code == 404
    sent = query(seen["start"].headers["location"])
    assert sent["client_id"] == "gh-id-from-env"
    assert "gh-secret-from-env" not in seen["start"].headers["location"]


def test_serve_without_sign_in_keeps_the_key_form(tmp_path, monkeypatch):
    code, seen = serve(monkeypatch, tmp_path, [], env={})
    assert code == 0 and 'name="key"' in seen["login"]


def test_serve_names_a_missing_variable_and_never_prints_a_value(tmp_path, monkeypatch, capsys):
    env = {k: v for k, v in SIGN_IN_ENV.items() if k != "CAIRN_GITHUB_CLIENT_SECRET"}
    monkeypatch.delenv("CAIRN_GITHUB_CLIENT_SECRET", raising=False)
    code, seen = serve(monkeypatch, tmp_path, ["--sign-in", "github"], env=env)
    err = capsys.readouterr().err
    assert code == 2 and seen == {} and "CAIRN_GITHUB_CLIENT_SECRET" in err
    assert not any(value in err for value in SIGN_IN_ENV.values() if "secret" in value)


def test_serve_refuses_a_shared_workspace_open_to_everyone(tmp_path, monkeypatch, capsys):
    code, seen = serve(monkeypatch, tmp_path, ["--sign-in", "github", "--join", "default"])
    assert code == 2 and seen == {} and "allowed_email_domains" in capsys.readouterr().err


def test_serve_refuses_an_oidc_shared_workspace_nobody_closed(tmp_path, monkeypatch, capsys):
    code, seen = serve(monkeypatch, tmp_path, ["--sign-in", "oidc", "--join", "default"])
    err = capsys.readouterr().err
    assert code == 2 and seen == {}
    for way in ("--allowed-email-domain", "CAIRN_OIDC_ALLOWED_GROUPS", "--closed-directory"):
        assert way in err, way
    assert not any(value in err for value in SIGN_IN_ENV.values() if "secret" in value)


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["--allowed-email-domain", "acme.example"], {}),
        ([], {"CAIRN_OIDC_GROUPS_CLAIM": "groups", "CAIRN_OIDC_ALLOWED_GROUPS": "staff"}),
        (["--closed-directory"], {}),
    ],
)
def test_serve_admits_an_oidc_shared_workspace_closed_one_of_three_ways(
    tmp_path, monkeypatch, argv, env
):
    argv = ["--sign-in", "oidc", "--join", "default", *argv]
    code, seen = serve(monkeypatch, tmp_path, argv, env={**SIGN_IN_ENV, **env})
    assert code == 0 and 'href="/auth/sso"' in seen["login"]


def test_serve_lets_oidc_give_people_workspaces_of_their_own_without_closing_it(
    tmp_path, monkeypatch
):
    code, seen = serve(monkeypatch, tmp_path, ["--sign-in", "oidc"])
    assert code == 0 and 'href="/auth/sso"' in seen["login"]


@pytest.mark.parametrize(
    ("env", "named"),
    [
        ({"CAIRN_OIDC_GROUPS_CLAIM": "groups", "CAIRN_OIDC_ALLOWED_GROUPS": " , ,"}, "ALLOWED"),
        ({"CAIRN_OIDC_GROUPS_CLAIM": "groups", "CAIRN_OIDC_ALLOWED_GROUPS": ""}, "ALLOWED"),
        ({"CAIRN_OIDC_GROUPS_CLAIM": "  ", "CAIRN_OIDC_ALLOWED_GROUPS": "staff"}, "CLAIM"),
        ({"CAIRN_OIDC_GROUPS_CLAIM": " "}, "CLAIM"),
        ({"CAIRN_OIDC_ALLOWED_GROUPS": "staff"}, "CLAIM"),
    ],
)
def test_serve_refuses_group_settings_that_would_silently_admit_everyone(
    tmp_path, monkeypatch, capsys, env, named
):
    for name in ("CAIRN_OIDC_GROUPS_CLAIM", "CAIRN_OIDC_ALLOWED_GROUPS"):
        monkeypatch.delenv(name, raising=False)
    argv = ["--sign-in", "oidc", "--join", "default", "--closed-directory"]
    code, seen = serve(monkeypatch, tmp_path, argv, env={**SIGN_IN_ENV, **env})
    err = capsys.readouterr().err
    assert code == 2 and seen == {}
    variable = {"ALLOWED": "CAIRN_OIDC_ALLOWED_GROUPS", "CLAIM": "CAIRN_OIDC_GROUPS_CLAIM"}
    assert variable[named] in err
    assert "staff" not in err and "acme-secret-from-env" not in err


def test_serve_takes_no_secret_as_an_argument():
    serve_command = cairn.web.cli._parser()._subparsers._group_actions[0].choices["serve"]
    options = serve_command._option_string_actions
    assert not any("secret" in option or "client" in option for option in options)


def test_serve_with_oidc_and_no_issuer_exits_2_naming_the_variable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CAIRN_OIDC_ISSUER", raising=False)
    env = {k: v for k, v in SIGN_IN_ENV.items() if k != "CAIRN_OIDC_ISSUER"}
    code, seen = serve(monkeypatch, tmp_path, ["--sign-in", "oidc"], env=env)
    err = capsys.readouterr().err
    assert (code, seen) == (2, {})
    assert err == "set CAIRN_OIDC_ISSUER in the environment to sign in with that provider\n"
