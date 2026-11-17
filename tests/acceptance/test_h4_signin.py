"""H4 acceptance tests (frozen): sign-in, users, workspaces, keys in the UI and onboarding.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/server.py and src/cairn/store.py.

Turning it on
- `create_app(..., sign_in=SignIn(providers=(...), http=<httpx.AsyncClient>, join="personal",
  allowed_email_domains=()))`. It needs a `public_url` that is https, or http on a loopback
  host (ValueError otherwise). Without `sign_in`, cairn signs people in with an API key, as
  before. With it, POST /login is 404 and GET /login offers one link per provider and no
  key form.
- Providers, from `cairn.identity`: `GitHub(client_id, client_secret)`,
  `Google(client_id, client_secret)` and `OIDC(name, label, issuer, client_id,
  client_secret, groups_claim=None, allowed_groups=())` for any OpenID Connect provider (its
  endpoints come from `<issuer>/.well-known/openid-configuration`, fetched when the provider
  is first used). Every call to a provider goes through the given `http` client. A
  provider's routes are /auth/<name> and /auth/<name>/callback, where the name is "github",
  "google" or the OIDC provider's `name`.
- An OIDC provider's request carries a `nonce`. The ID token its token endpoint returns must
  name this issuer, this client as audience and that nonce, and not be expired (400
  otherwise, and nothing is created). Its signature is not checked: the token arrives over
  TLS straight from the provider. With `allowed_groups`, the person must be in one of them,
  as listed under `groups_claim` in the ID token or the userinfo answer (403 otherwise).
- `join` says where a new person lands. "personal": in a new workspace (account) of their
  own, named after them, which they own; GET / without a session is then a public landing
  page that links to /login. "default": in the default workspace, as its owner if it has no
  member yet and as a member otherwise; GET / without a session then redirects to /login.
- `allowed_email_domains`, when not empty, refuses (403) anyone whose verified email is in
  another domain. For Google the account must also belong to one of those domains as an
  organisation (the `hd` claim), which a personal Google account never has.
- A shared workspace is never open to everyone: `join="default"` with GitHub or Google among
  the providers and no `allowed_email_domains` is a ValueError.

People
- A user (id "usr_" + 12 hex chars, email, name) is a member of workspaces with a role:
  "owner", "admin" or "member". `Store.get_user(id)`, and on a view `list_members()`
  (user_id, email, name, role; in joining order). A session made by signing in carries the
  user: `resolve_session(token)` returns a Principal with `user_id` set (None for a session
  made with an API key).
- The identity (provider, subject) decides the user. A new identity whose verified email
  equals an existing user's email (ignoring case) is the same user; otherwise a new user is
  created, named after the profile's name (or login), with that email. An email the provider
  has not verified is refused (403) and creates nothing. Some providers send
  `email_verified` as the string "true".
- `delete_account` removes the workspace's memberships, and a user left with no workspace is
  removed with their identities.

Sign-in
- GET /auth/<provider>[?next=/path] redirects (302) to the provider's authorize URL with
  `client_id`, `redirect_uri` = public_url + "/auth/<provider>/callback", `response_type=code`,
  a `state` and a scope, and remembers the state in a cookie (HttpOnly, SameSite=Lax, Secure
  on https, at most ten minutes). It writes nothing to the database.
- GET /auth/<provider>/callback?code=...&state=... checks the state against the cookie (400
  when it is missing, different or already used, and then no call to the provider is made),
  exchanges the code, and reads the profile: GitHub's /user and /user/emails (the primary
  email), Google's or the OIDC provider's userinfo endpoint.
- On success it sets the session cookie (HttpOnly, SameSite=Strict, Secure on https) and
  answers 200 with a `<meta http-equiv="refresh">` to the target, so the next request is a
  same-site navigation that carries the cookie: /welcome for a new user, otherwise `next`
  when it is a path on this server, otherwise "/". This callback is the one GET that writes.
- ?error=... from the provider leads back to /login (303), where a message is shown.
- Signed-in pages show the person's name.
- The provider's client secret and the tokens it issues never appear in a response, a
  redirect or the database.

Keys in the UI (with or without `sign_in`)
- GET /keys lists the workspace's key labels. POST /keys with `label` creates a key and
  shows it exactly once, on that response; POST /keys/<label>/revoke revokes it.

Onboarding
- GET /welcome and GET /setup show how to connect with only the URL: the MCP URL and the
  `claude mcp add` and `codex mcp add` commands for it. With `sign_in`, pages do not mention
  the homelab's env file or renderer.

Together with H3: a person who signed in through a provider can approve an agent. The
agent's token then acts for that person in that workspace: `resolve_token` returns a
Principal with their `user_id`.
"""

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import time
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import OIDC, GitHub, Google
from cairn.server import SignIn, create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
ISSUER = "https://id.acme.example/pool-1"
SECRETS = {
    "github": "gh-client-secret-do-not-leak",
    "google": "g-client-secret-do-not-leak",
    "acme": "acme-client-secret-do-not-leak",
}
AUTHORIZE = {
    "github": "https://github.com/login/oauth/authorize",
    "google": "https://accounts.google.com/o/oauth2/v2/auth",
    "acme": "https://id.acme.example/pool-1/oauth2/authorize",
}
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": AUTHORIZE["acme"],
    "token_endpoint": f"{ISSUER}/oauth2/token",
    "userinfo_endpoint": f"{ISSUER}/oauth2/userInfo",
    "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
}
CLARA_ACME = {"sub": "acme-clara", "name": "Clara Diaz", "email": "clara@acme.example"}
DEV_ACME = {"sub": "acme-dev", "name": "Dev Patel", "email": "dev@acme.example"}
GUEST_ACME = {"sub": "acme-guest", "name": "A Guest", "email": "guest@elsewhere.example"}
ADA_GITHUB = {"id": 990017734, "login": "ada", "name": "Ada Lovelace", "email": "ada@example.test"}
ADA_GOOGLE = {"sub": "g-ada-1", "name": "Ada L.", "email": "ADA@Example.test"}
BROOK_GITHUB = {"id": 2002, "login": "brook", "name": None, "email": "brook@example.test"}
REDIRECT = "http://127.0.0.1:33418/callback"


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


class HiddenFields(HTMLParser):
    """Every hidden input on a page, by name."""

    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "hidden":
            self.fields[attributes["name"]] = attributes.get("value") or ""


def hidden_fields(html: str) -> dict[str, str]:
    parser = HiddenFields()
    parser.feed(html)
    return parser.fields


class FakeProviders:
    """GitHub's and Google's OAuth and profile endpoints, as an httpx mock."""

    def __init__(self) -> None:
        self.codes: dict[str, tuple[str, dict, bool, str | None]] = {}
        self.tokens: dict[str, tuple[dict, bool]] = {}
        self.calls: list[str] = []
        # What the OIDC provider puts in its ID tokens; tests change these to forge one.
        self.id_token_issuer = ISSUER
        self.id_token_audience = "acme-client-id"
        self.id_token_lifetime = 300
        self.id_token_nonce: str | None = None

    def approve(
        self, provider: str, profile: dict, verified: bool = True, nonce: str | None = None
    ) -> str:
        """The person approved at the provider; returns the code it redirects back with."""
        code = f"code-{provider}-{secrets.token_hex(4)}"
        self.codes[code] = (provider, profile, verified, nonce)
        return code

    def id_token(self, profile: dict, nonce: str | None) -> str:
        """An ID token as the OIDC provider issues it. The signature is a placeholder: the
        contract trusts the TLS connection to the token endpoint, not the signature."""
        claims = {
            "iss": self.id_token_issuer,
            "aud": self.id_token_audience,
            "sub": profile["sub"],
            "iat": int(time.time()),
            "exp": int(time.time()) + self.id_token_lifetime,
            "nonce": self.id_token_nonce or nonce,
        }
        if "groups" in profile:
            claims["cognito:groups"] = profile["groups"]

        def part(value: dict) -> str:
            return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

        return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part(claims)}.c2lnbmF0dXJl"

    def _token(self, provider: str, request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        sent = json.loads(body) if body.lstrip().startswith("{") else dict(parse_qsl(body))
        basic = request.headers.get("authorization", "")
        if basic.lower().startswith("basic "):
            client_id, _, secret = base64.b64decode(basic[6:]).decode().partition(":")
            sent.setdefault("client_id", client_id)
            sent.setdefault("client_secret", secret)
        expected = self.codes.pop(sent.get("code", ""), None)
        if (
            expected is None
            or expected[0] != provider
            or sent.get("client_id") != f"{provider}-client-id"
            or sent.get("client_secret") != SECRETS[provider]
            or sent.get("redirect_uri") != f"{BASE}/auth/{provider}/callback"
        ):
            return httpx.Response(400, json={"error": "bad_verification_code"})
        token = f"provider-access-token-{secrets.token_hex(8)}"
        self.tokens[token] = (expected[1], expected[2])
        answer = {"access_token": token, "token_type": "bearer"}
        if provider == "acme":
            answer["id_token"] = self.id_token(expected[1], expected[3])
        return httpx.Response(200, json=answer)

    def _profile(self, request: httpx.Request) -> tuple[dict, bool] | None:
        return self.tokens.get(request.headers.get("authorization", "").partition(" ")[2])

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        self.calls.append(url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json=DISCOVERY)
        if url == "https://github.com/login/oauth/access_token":
            return self._token("github", request)
        if url == "https://oauth2.googleapis.com/token":
            return self._token("google", request)
        if url == DISCOVERY["token_endpoint"]:
            return self._token("acme", request)
        found = self._profile(request)
        if found is None:
            return httpx.Response(401, json={"message": "Bad credentials"})
        profile, verified = found
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={k: profile[k] for k in ("id", "login", "name")})
        if url == "https://api.github.com/user/emails":
            emails = [
                {"email": "old@example.test", "primary": False, "verified": True},
                {"email": profile["email"], "primary": True, "verified": verified},
            ]
            return httpx.Response(200, json=emails)
        if url == "https://openidconnect.googleapis.com/v1/userinfo":
            claims = {**profile, "email_verified": verified}
            return httpx.Response(200, json=claims)
        if url == DISCOVERY["userinfo_endpoint"]:
            # Like Cognito: the flag is a string, and groups are only in the ID token.
            claims = {k: v for k, v in profile.items() if k != "groups"}
            claims["email_verified"] = "true" if verified else "false"
            return httpx.Response(200, json=claims)
        return httpx.Response(404)


def providers() -> tuple:
    return (
        GitHub("github-client-id", SECRETS["github"]),
        Google("google-client-id", SECRETS["google"]),
        OIDC("acme", "Acme SSO", ISSUER, "acme-client-id", SECRETS["acme"]),
    )


def world(tmp_path, offered: tuple | None = None, **sign_in):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "cairn.db"
    store = Store(db)
    fake = FakeProviders()
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    config = SignIn(providers=offered or providers(), http=http, **sign_in)
    app = create_app(store, public_url=BASE, sign_in=config)
    return SimpleNamespace(store=store, db=db, fake=fake, app=app, seen=[])


@pytest.fixture
def env(tmp_path):
    """The hosted service: every new person gets a workspace of their own."""
    w = world(tmp_path, join="personal")
    with TestClient(w.app, base_url=BASE) as client:
        w.client = client
        yield w
    w.store.close()


@pytest.fixture
def org(tmp_path):
    """An organisation's own install: everyone joins the default workspace."""
    w = world(tmp_path, join="default", allowed_email_domains=("acme.example",))
    with TestClient(w.app, base_url=BASE) as client:
        w.client = client
        yield w
    w.store.close()


def start(env, provider: str, next_path: str | None = None) -> dict:
    """GET /auth/<provider>: returns the query cairn sent the browser to the provider with."""
    path = f"/auth/{provider}" + (f"?next={quote(next_path, safe='')}" if next_path else "")
    r = env.client.get(path, follow_redirects=False)
    assert r.status_code == 302, r.text
    location = r.headers["location"]
    env.seen.append(location)
    assert location.split("?")[0] == AUTHORIZE[provider]
    sent = query(location)
    assert sent["client_id"] == f"{provider}-client-id" and sent["response_type"] == "code"
    assert SECRETS[provider] not in location
    assert sent["redirect_uri"] == f"{BASE}/auth/{provider}/callback"
    assert len(sent["state"]) >= 20 and sent["scope"]
    return sent


def sign_in(env, provider: str, profile: dict, *, next_path: str | None = None, verified=True):
    """The whole trip: start, approve at the provider, come back to the callback."""
    sent = start(env, provider, next_path)
    code = env.fake.approve(provider, profile, verified, nonce=sent.get("nonce"))
    r = env.client.get(
        f"/auth/{provider}/callback?code={code}&state={sent['state']}", follow_redirects=False
    )
    env.seen.append(r.text)
    return r


def session_of(env):
    """Who the browser's current session belongs to."""
    principal = env.store.resolve_session(env.client.cookies["cairn_session"])
    assert principal is not None and principal.kind == "session" and principal.user_id
    return principal


def account_of(env) -> str:
    return session_of(env).account_id


def user_of(env):
    return env.store.get_user(session_of(env).user_id)


def sign_out(env) -> None:
    assert (
        env.client.post("/logout", headers=SAME_ORIGIN, follow_redirects=False).status_code == 303
    )
    env.client.cookies.clear()


# Pages that need no session


def test_hosted_mode_has_a_public_landing_page_and_provider_sign_in(env):
    before = dump(env.db)
    home = env.client.get("/", follow_redirects=False)
    assert home.status_code == 200 and 'href="/login"' in home.text
    login = env.client.get("/login")
    assert login.status_code == 200
    for name in ("github", "google", "acme"):
        assert f'href="/auth/{name}"' in login.text
    assert "Acme SSO" in login.text
    assert 'name="key"' not in login.text
    posted = env.client.post("/login", data={"key": "any-key"}, headers=SAME_ORIGIN)
    assert posted.status_code == 404
    for path in ("/b/core", "/setup", "/keys", "/agents", "/welcome"):
        r = env.client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), path
    assert "agent-vault" not in home.text and "client-mcp-manager" not in home.text
    assert dump(env.db) == before


def test_starting_a_sign_in_sets_a_state_cookie_and_writes_nothing(env):
    before = dump(env.db)
    r = env.client.get("/auth/github?next=/keys", follow_redirects=False)
    assert r.status_code == 302
    cookies = [c.lower() for c in r.headers.get_list("set-cookie")]
    state_cookie = next(c for c in cookies if "cairn_session=" not in c)
    assert "httponly" in state_cookie and "samesite=lax" in state_cookie
    assert "secure" in state_cookie
    max_age = int(re.search(r"max-age=(\d+)", state_cookie).group(1))
    assert 0 < max_age <= 600
    assert SECRETS["github"] not in r.headers["location"]
    assert env.fake.calls == [] and dump(env.db) == before


def test_self_hosted_mode_has_no_provider_routes(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        key = store.create_key("laptop")
        app = create_app(store, public_url="http://127.0.0.1:8765")
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            assert client.get("/auth/github", follow_redirects=False).status_code == 404
            assert client.get("/auth/github/callback?code=x&state=y").status_code == 404
            assert client.get("/", follow_redirects=False).status_code == 303
            assert 'name="key"' in client.get("/login").text
            r = client.post("/login", data={"key": key}, follow_redirects=False)
            assert r.status_code == 303
    finally:
        store.close()


def test_sign_in_needs_a_usable_public_url(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        sign_in = SignIn(providers=(GitHub("id", "secret"),), http=httpx.AsyncClient())
        for bad in (None, "http://cairn.example.test"):
            with pytest.raises(ValueError):
                create_app(store, public_url=bad, sign_in=sign_in)
        create_app(store, public_url="http://127.0.0.1:8765", sign_in=sign_in)
    finally:
        store.close()


# Sign-in


def test_first_sign_in_creates_a_user_a_workspace_and_a_session(env):
    r = sign_in(env, "github", ADA_GITHUB)
    assert r.status_code == 200
    assert refresh_target(r.text) == "/welcome"
    session = next(c for c in r.headers.get_list("set-cookie") if "cairn_session=" in c).lower()
    assert "httponly" in session and "samesite=strict" in session and "secure" in session

    accounts = [a for a in env.store.list_accounts() if a.id != "acct_default"]
    assert [a.name for a in accounts] == ["Ada Lovelace"]
    assert account_of(env) == accounts[0].id
    user = user_of(env)
    assert user.id.startswith("usr_") and len(user.id) == 16
    assert (user.name, user.email) == ("Ada Lovelace", "ada@example.test")
    members = env.store.for_account(accounts[0].id).list_members()
    assert [(m.user_id, m.email, m.role) for m in members] == [
        (user.id, "ada@example.test", "owner")
    ]
    assert env.store.list_members() == []  # nobody joined the default workspace
    assert env.fake.calls == [
        "https://github.com/login/oauth/access_token",
        "https://api.github.com/user",
        "https://api.github.com/user/emails",
    ]

    welcome = env.client.get("/welcome")
    assert welcome.status_code == 200 and f"{BASE}/mcp" in welcome.text
    home = env.client.get("/")
    assert home.status_code == 200 and "Ada Lovelace" in home.text  # signed in: the app, named


def test_signing_in_again_returns_to_the_same_account(env):
    sign_in(env, "github", ADA_GITHUB)
    first = account_of(env)
    view = env.store.for_account(first)
    view.create_project("core", "kept between sign-ins")
    sign_out(env)
    assert env.client.get("/b/core", follow_redirects=False).status_code == 303

    r = sign_in(env, "github", {**ADA_GITHUB, "name": "Ada King"}, next_path="/b/core")
    assert r.status_code == 200 and refresh_target(r.text) == "/b/core"
    assert account_of(env) == first
    assert len(env.store.list_accounts()) == 2  # the default workspace and Ada's
    assert env.store.get_account(first).name == "Ada Lovelace"  # a rename upstream changes nothing
    assert user_of(env).name == "Ada Lovelace"
    assert "kept between sign-ins" in env.client.get("/").text

    for outside in ("https://evil.example/", "//evil.example/", "/\\evil.example"):
        sign_out(env)
        r = sign_in(env, "github", ADA_GITHUB, next_path=outside)
        assert refresh_target(r.text) == "/", outside


def test_a_second_provider_with_the_same_verified_email_is_the_same_person(env):
    sign_in(env, "github", ADA_GITHUB)
    first, ada = account_of(env), user_of(env)
    sign_out(env)
    r = sign_in(env, "google", ADA_GOOGLE)  # the same address, in another case
    assert r.status_code == 200 and refresh_target(r.text) == "/"
    assert account_of(env) == first and len(env.store.list_accounts()) == 2
    assert user_of(env) == ada
    assert env.fake.calls[-2:] == [
        "https://oauth2.googleapis.com/token",
        "https://openidconnect.googleapis.com/v1/userinfo",
    ]
    sign_out(env)
    other = {"sub": "g-someone-else", "name": "Someone Else", "email": "else@example.test"}
    sign_in(env, "google", other)
    assert account_of(env) != first and len(env.store.list_accounts()) == 3


def test_an_unverified_email_is_refused_and_creates_nothing(env):
    before = dump(env.db)
    for provider, profile in (("github", ADA_GITHUB), ("google", ADA_GOOGLE), ("acme", CLARA_ACME)):
        r = sign_in(env, provider, profile, verified=False)
        assert r.status_code == 403, provider
        assert "cairn_session" not in env.client.cookies
        assert not any("cairn_session=" in c for c in r.headers.get_list("set-cookie"))
    assert dump(env.db) == before
    # A verified sign-in later does not join anything left over from the refused ones.
    sign_in(env, "github", ADA_GITHUB)
    assert len(env.store.list_accounts()) == 2


def test_two_people_get_two_isolated_workspaces(env):
    sign_in(env, "github", ADA_GITHUB)
    ada = account_of(env)
    env.store.for_account(ada).create_project("core", "Ada's private project")
    sign_out(env)
    sign_in(env, "github", BROOK_GITHUB)
    brook = account_of(env)
    assert brook != ada
    assert env.store.get_account(brook).name == "brook"  # no profile name: the login
    assert user_of(env).name == "brook"
    home = env.client.get("/").text
    assert "Ada's private project" not in home and "Ada Lovelace" not in home
    assert env.client.get("/b/core").status_code == 404


def test_the_callback_needs_the_state_it_issued(env):
    before = dump(env.db)
    state = start(env, "github")["state"]
    code = env.fake.approve("github", ADA_GITHUB)
    wrong = env.client.get(f"/auth/github/callback?code={code}&state={state}x")
    assert wrong.status_code == 400

    env.client.cookies.clear()  # another browser: it has the URL but not the cookie
    stolen = env.client.get(f"/auth/github/callback?code={code}&state={state}")
    assert stolen.status_code == 400
    assert env.client.get("/auth/github/callback?code=x").status_code == 400
    # A state issued for one provider does not open the other's callback.
    state = start(env, "github")["state"]
    crossed = env.client.get(f"/auth/google/callback?code={code}&state={state}")
    assert crossed.status_code == 400
    assert env.fake.calls == [] and dump(env.db) == before

    # A state works once.
    r = sign_in(env, "github", ADA_GITHUB)
    assert r.status_code == 200
    used = query(env.seen[-2])["state"]
    again = env.fake.approve("github", ADA_GITHUB)
    replay = env.client.get(f"/auth/github/callback?code={again}&state={used}")
    assert replay.status_code == 400


def test_a_cancelled_or_failed_sign_in_goes_back_to_login(env):
    before = dump(env.db)
    state = start(env, "github")["state"]
    r = env.client.get(
        f"/auth/github/callback?error=access_denied&state={state}", follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    page = env.client.get(r.headers["location"])
    assert page.status_code == 200 and 'role="alert"' in page.text

    state = start(env, "github")["state"]  # the provider rejects the code
    failed = env.client.get(f"/auth/github/callback?code=made-up&state={state}")
    assert failed.status_code in (400, 502) and "cairn_session" not in env.client.cookies
    assert dump(env.db) == before


def test_provider_secrets_and_tokens_stay_out_of_responses_and_the_database(env):
    sign_in(env, "github", ADA_GITHUB)
    sign_out(env)
    sign_in(env, "google", ADA_GOOGLE)
    pages = [env.client.get(path).text for path in ("/", "/welcome", "/setup", "/keys", "/agents")]
    everything = "\n".join([*env.seen, *pages, dump(env.db)])
    for secret in (*SECRETS.values(), *env.fake.tokens):
        assert secret not in everything
    assert env.fake.tokens  # the providers did issue tokens

    account, user = account_of(env), user_of(env)
    assert env.store.delete_account(account) is True
    assert env.store.get_user(user.id) is None  # no workspace left: the person goes too
    left = dump(env.db).lower()
    assert "ada@example.test" not in left and "g-ada-1" not in left and "990017734" not in left


# An organisation's own install


def test_an_oidc_provider_signs_people_into_the_shared_workspace(org):
    before = org.client.get("/", follow_redirects=False)
    assert before.status_code == 303 and before.headers["location"].startswith("/login")

    r = sign_in(org, "acme", CLARA_ACME)
    assert r.status_code == 200 and refresh_target(r.text) == "/welcome"
    assert len(query(org.seen[0])["nonce"]) >= 20
    assert org.fake.calls == [
        f"{ISSUER}/.well-known/openid-configuration",
        DISCOVERY["token_endpoint"],
        DISCOVERY["userinfo_endpoint"],
    ]
    clara = user_of(org)
    assert account_of(org) == "acct_default" and clara.email == "clara@acme.example"
    org.store.create_project("handbook", "shared by the team")
    assert "Clara Diaz" in org.client.get("/").text
    sign_out(org)

    sign_in(org, "acme", DEV_ACME)
    assert account_of(org) == "acct_default"  # the same workspace, not one of his own
    assert "shared by the team" in org.client.get("/").text
    assert [(m.email, m.role) for m in org.store.list_members()] == [
        ("clara@acme.example", "owner"),
        ("dev@acme.example", "member"),
    ]
    assert [a.id for a in org.store.list_accounts()] == ["acct_default"]


def test_an_install_can_be_limited_to_its_own_email_domains(org):
    before = dump(org.db)
    r = sign_in(org, "acme", GUEST_ACME)
    assert r.status_code == 403 and "cairn_session" not in org.client.cookies
    r = sign_in(org, "github", ADA_GITHUB)  # verified, but not the organisation's domain
    assert r.status_code == 403
    assert dump(org.db) == before
    assert sign_in(org, "acme", CLARA_ACME).status_code == 200


def test_google_accounts_must_belong_to_the_organisation(org):
    before = dump(org.db)
    personal = {"sub": "g-clara-personal", "name": "Clara D", "email": "clara@acme.example"}
    assert sign_in(org, "google", personal).status_code == 403  # the address, but no `hd`
    elsewhere = {**personal, "sub": "g-other", "hd": "elsewhere.example"}
    assert sign_in(org, "google", elsewhere).status_code == 403
    assert dump(org.db) == before
    managed = {**personal, "sub": "g-clara-work", "hd": "acme.example"}
    assert sign_in(org, "google", managed).status_code == 200
    assert user_of(org).email == "clara@acme.example"


def test_a_shared_workspace_is_never_open_to_everyone(tmp_path):
    store = Store(tmp_path / "cairn.db")
    http = httpx.AsyncClient()
    github = GitHub("github-client-id", SECRETS["github"])
    google = Google("google-client-id", SECRETS["google"])
    oidc = OIDC("acme", "Acme SSO", ISSUER, "acme-client-id", SECRETS["acme"])
    try:
        for open_to_all in ((github,), (google,), (oidc, google)):
            with pytest.raises(ValueError):
                create_app(
                    store,
                    public_url=BASE,
                    sign_in=SignIn(providers=open_to_all, http=http, join="default"),
                )
        # An organisation's own provider alone, or public ones with a domain list, are fine.
        create_app(
            store, public_url=BASE, sign_in=SignIn(providers=(oidc,), http=http, join="default")
        )
        create_app(
            store,
            public_url=BASE,
            sign_in=SignIn(
                providers=(github, google),
                http=http,
                join="default",
                allowed_email_domains=("acme.example",),
            ),
        )
    finally:
        store.close()


def test_an_oidc_provider_can_admit_only_named_groups(tmp_path):
    staff_only = OIDC(
        "acme",
        "Acme SSO",
        ISSUER,
        "acme-client-id",
        SECRETS["acme"],
        groups_claim="cognito:groups",
        allowed_groups=("staff", "admins"),
    )
    w = world(tmp_path, offered=(staff_only,), join="default")
    try:
        with TestClient(w.app, base_url=BASE) as client:
            w.client = client
            before = dump(w.db)
            student = {**DEV_ACME, "groups": ["students"]}
            assert sign_in(w, "acme", student).status_code == 403
            assert sign_in(w, "acme", GUEST_ACME).status_code == 403  # no groups at all
            assert "cairn_session" not in client.cookies and dump(w.db) == before

            staff = {**CLARA_ACME, "groups": ["therapists", "staff"]}
            assert sign_in(w, "acme", staff).status_code == 200
            assert user_of(w).email == "clara@acme.example"
    finally:
        w.store.close()


def test_an_id_token_must_be_this_sign_ins_own(org):
    before = dump(org.db)
    forgeries = (
        ("id_token_nonce", "someone-elses-nonce"),
        ("id_token_issuer", "https://id.evil.example/pool-1"),
        ("id_token_audience", "another-client"),
        ("id_token_lifetime", -3600),  # expired an hour ago
    )
    for field, value in forgeries:
        original = getattr(org.fake, field)
        setattr(org.fake, field, value)
        try:
            r = sign_in(org, "acme", CLARA_ACME)
            assert r.status_code == 400, field
            assert "cairn_session" not in org.client.cookies, field
        finally:
            setattr(org.fake, field, original)
    assert dump(org.db) == before
    assert sign_in(org, "acme", CLARA_ACME).status_code == 200


# Keys


def test_keys_are_created_shown_once_and_revoked_in_the_ui(env):
    sign_in(env, "github", ADA_GITHUB)
    account = account_of(env)
    assert env.client.get("/keys").status_code == 200

    created = env.client.post("/keys", data={"label": "ci-runner"}, headers=SAME_ORIGIN)
    assert created.status_code == 200
    key = next(
        candidate
        for candidate in re.findall(r"[A-Za-z0-9_-]{40,}", created.text)
        if env.store.resolve_key(candidate) is not None
    )
    principal = env.store.resolve_key(key)
    assert (principal.account_id, principal.label) == (account, "ci-runner")
    listed = env.client.get("/keys")
    assert "ci-runner" in listed.text and key not in listed.text  # shown once only
    assert env.client.get("/api/projects", headers={"x-api-key": key}).status_code == 200

    blank = env.client.post("/keys", data={"label": "   "}, headers=SAME_ORIGIN)
    assert blank.status_code == 400
    cross = env.client.post("/keys", data={"label": "x"}, headers={"sec-fetch-site": "cross-site"})
    assert cross.status_code == 403

    revoked = env.client.post("/keys/ci-runner/revoke", headers=SAME_ORIGIN, follow_redirects=False)
    assert revoked.status_code == 303
    assert env.store.resolve_key(key) is None
    assert "ci-runner" not in env.client.get("/keys").text


# Onboarding


def test_hosted_pages_show_one_url_connect_and_no_homelab_steps(env):
    sign_in(env, "github", ADA_GITHUB)
    for path in ("/welcome", "/setup"):
        text = env.client.get(path).text
        assert f"{BASE}/mcp" in text, path
        assert "claude mcp add" in text and "codex mcp add" in text, path
        assert "agent-vault" not in text and "client-mcp-manager" not in text, path


# With H3: sign in through a provider, then let an agent in


def test_a_signed_in_person_can_connect_an_agent_by_url(env):
    sign_in(env, "github", ADA_GITHUB)
    account, person = account_of(env), user_of(env)
    env.store.for_account(account).create_project("core", "Ada's")
    c = env.client

    registered = c.post(
        "/register",
        json={
            "client_name": "hosted-agent",
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )
    assert registered.status_code == 201
    client_id = registered.json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    prm = c.get("/.well-known/oauth-protected-resource/mcp").json()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "agent-state",
        "resource": f"{BASE}/mcp",
        "scope": " ".join(prm.get("scopes_supported") or []),
    }
    r = c.get("/authorize?" + urlencode(params), follow_redirects=False)
    assert r.status_code == 302
    page = c.get(r.headers["location"])
    assert page.status_code == 200 and "hosted-agent" in page.text and "Ada Lovelace" in page.text
    fields = hidden_fields(page.text)
    assert fields, "the consent form's hidden fields were not found"
    decided = c.post(
        "/oauth/consent",
        data={**fields, "decision": "approve"},
        headers=SAME_ORIGIN,
        follow_redirects=False,
    )
    assert decided.status_code == 303
    code = query(decided.headers["location"])["code"]
    token = c.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200, token.text
    access = token.json()["access_token"]

    c.cookies.clear()  # the agent has no browser session, only its token
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "projects", "arguments": {}},
    }
    answer = c.post("/mcp", headers={**MCP_HEADERS, "authorization": f"Bearer {access}"}, json=body)
    assert answer.status_code == 200
    listed = json.loads(answer.json()["result"]["content"][0]["text"])["projects"]
    assert [(b["name"], b["description"]) for b in listed] == [("core", "Ada's")]
    principal = env.store.resolve_token(access)
    assert (principal.account_id, principal.user_id) == (account, person.id)
