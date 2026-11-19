"""Who is allowed is checked on every request, not only at sign-in. When the operator limits
who may sign in (CAIRN_ALLOWED_EMAILS, --allowed-email-domain), a person whose address is no
longer allowed is refused as soon as the server starts with the new limits: their session,
and every key and agent that acts for them, is answered as an ended one. Nothing is deleted,
so allowing them again restores it. The check reads no more of the database than admitting
a request did. Every name is made up."""

from datetime import UTC, datetime

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web.auth import HOST_ONLY, SESSION_COOKIE

BASE = "https://kb.example.test"
MCP = f"{BASE}/mcp"
START = datetime(2099, 11, 14, 9, 0, tzinfo=UTC)
ADA, BROOK = "ada@example.test", "brook@fable.test"
COOKIE = HOST_ONLY + SESSION_COOKIE
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


class GitHubFake:
    def __init__(self, email: str = ADA) -> None:
        self.email = email

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_made_up"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"id": 7, "login": "ada", "name": "Ada"})
        if url == "https://api.github.com/user/emails":
            return httpx.Response(200, json=[{"email": self.email, "primary": True,
                                              "verified": True}])  # fmt: skip
        return httpx.Response(404)


@pytest.fixture
def store(tmp_path):
    made = Store(tmp_path / "kb.db", clock=lambda: START)
    yield made
    made.close()


def app(store, email: str = ADA, **limits):
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake(email)))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http, **limits)
    return create_app(store, public_url=BASE, sign_in=sign_in, clock=lambda: START)


def sign_in(client: TestClient) -> httpx.Response:
    started = client.get("/auth/github", follow_redirects=False)
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    return client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)


class Ada:
    """Ada signed in, with a key she made and an agent she approved."""

    def __init__(self, store) -> None:
        browser = TestClient(app(store), base_url=BASE)
        assert sign_in(browser).status_code == 200
        self.session = browser.cookies[COOKIE]
        principal = store.resolve_session(self.session)
        view = store.for_account(principal.account_id, principal.user_id)
        self.key = view.create_key("laptop")
        client = store.register_client("an-agent", ["http://127.0.0.1:1/cb"])
        code = view.create_code(client.id, "http://127.0.0.1:1/cb", "c" * 43, audience=MCP)
        self.token = store.redeem_code(code, client.id, audience=MCP).access_token


def initialize(client: TestClient, token: str) -> httpx.Response:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "allowed", "version": "0"},
        },
    }
    headers = {**MCP_HEADERS, "authorization": f"Bearer {token}"}
    return client.post("/mcp", headers=headers, json=body)


def reaches(made, ada: Ada) -> tuple[bool, bool, bool]:
    """Whether Ada's session, key and agent are admitted by the app `made`."""
    client = TestClient(made, base_url=BASE)
    client.cookies.set(COOKIE, ada.session)
    page = client.get("/workspace", follow_redirects=False)
    assert page.status_code in (200, 303)
    keyed = TestClient(made, base_url=BASE).get("/api/projects", headers={"x-api-key": ada.key})
    assert keyed.status_code in (200, 401)
    with TestClient(made, base_url=BASE) as running:  # the MCP transport runs in the lifespan
        agent = initialize(running, ada.token)
    assert agent.status_code in (200, 401)
    return page.status_code == 200, keyed.status_code == 200, agent.status_code == 200


def test_without_limits_everything_ada_holds_works(store):
    ada = Ada(store)
    assert reaches(app(store), ada) == (True, True, True)


@pytest.mark.parametrize(
    "limits",
    [
        {"allowed_emails": (BROOK,)},
        {"allowed_email_domains": ("fable.test",)},
        {"allowed_emails": (BROOK,), "allowed_email_domains": ("fable.test",)},
    ],
    ids=["list", "domain", "both"],
)
def test_once_her_address_is_no_longer_allowed_her_session_keys_and_agents_end(store, limits):
    ada = Ada(store)
    assert reaches(app(store, **limits), ada) == (False, False, False)
    client = TestClient(app(store, **limits), base_url=BASE)
    client.cookies.set(COOKIE, ada.session)
    r = client.get("/workspace", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"  # as an ended one


@pytest.mark.parametrize(
    "limits",
    [
        {"allowed_emails": (" ADA@Example.TEST ", BROOK)},
        {"allowed_email_domains": ("example.test",)},
        {"allowed_emails": (BROOK,), "allowed_email_domains": ("Example.Test",)},
    ],
    ids=["list", "domain", "either"],
)
def test_while_her_address_is_allowed_nothing_changes(store, limits):
    ada = Ada(store)
    assert reaches(app(store, **limits), ada) == (True, True, True)


def test_nothing_is_deleted_so_allowing_her_again_restores_it(store):
    ada = Ada(store)
    with store._read() as con:
        before = [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone() for t in
                  ("sessions", "api_keys", "oauth_grants", "users", "memberships")]  # fmt: skip
    assert reaches(app(store, allowed_emails=(BROOK,)), ada) == (False, False, False)
    with store._read() as con:
        after = [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone() for t in
                 ("sessions", "api_keys", "oauth_grants", "users", "memberships")]  # fmt: skip
    assert after == before
    assert reaches(app(store, allowed_emails=(ADA,)), ada) == (True, True, True)


def test_a_session_is_checked_by_the_address_its_sign_in_was_vouched_with(store):
    """Ada first signed in as old@legacy.test; GitHub now vouches for ada@example.test, which
    the list admits. Her new session is admitted on the next request, as at sign-in. A key
    she made is checked by the address she is stored under, the first one."""
    store.sign_in("github", "7", "old@legacy.test", "Ada", issuer="https://github.com",
                  join="personal")  # fmt: skip
    limited = app(store, ADA, allowed_emails=(ADA,))
    browser = TestClient(limited, base_url=BASE)
    assert sign_in(browser).status_code == 200
    assert browser.get("/workspace", follow_redirects=False).status_code == 200
    principal = store.resolve_session(browser.cookies[COOKIE])
    key = store.for_account(principal.account_id, principal.user_id).create_key("phone")
    keyed = browser.get("/api/projects", headers={"x-api-key": key})
    assert keyed.status_code == 401


def test_a_key_made_on_the_command_line_acts_for_nobody_and_is_not_limited(store):
    key = store.create_key("server")
    made = app(store, allowed_emails=(BROOK,))
    r = TestClient(made, base_url=BASE).get("/api/projects", headers={"x-api-key": key})
    assert r.status_code == 200


# The cost: no more of the database than admitting a request read before


def traced(store, run) -> list[str]:
    seen: list[str] = []
    store._con.set_trace_callback(seen.append)
    try:
        run()
    finally:
        store._con.set_trace_callback(None)
    return seen


def test_the_check_reads_nothing_more_than_admitting_did(store):
    ada = Ada(store)

    def apps():
        return app(store), app(store, allowed_emails=(ADA,), allowed_email_domains=("x.test",))

    def session(client):
        client.cookies.set(COOKIE, ada.session)
        return lambda: client.get("/workspace")

    def key(client):
        return lambda: client.get("/api/projects", headers={"x-api-key": ada.key})

    def agent(client):
        return lambda: initialize(client, ada.token)

    for credential in (session, key, agent):
        plain, limited = apps()
        with TestClient(plain, base_url=BASE) as one, TestClient(limited, base_url=BASE) as other:
            without, with_limits = credential(one), credential(other)
            without(), with_limits()  # each app's first request reads what it reads once
            assert traced(store, with_limits) == traced(store, without), credential.__name__
