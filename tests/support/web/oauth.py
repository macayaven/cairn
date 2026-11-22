"""Shared test helpers extracted from test_oauth_unit."""

import base64
import hashlib
import secrets
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from starlette.testclient import TestClient

import cairn.store
from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"

RESOURCE = f"{BASE}/mcp"

REDIRECT = "http://127.0.0.1:33418/callback"

MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}

SAME_ORIGIN = {"sec-fetch-site": "same-origin"}

TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


class World:
    """One app over a real store with two accounts. `raw` carries no credentials; a person's
    browser is its own client with its own cookie jar."""

    def __init__(self, tmp_path, public_url: str | None = BASE) -> None:
        self.db = tmp_path / "cairn.db"
        self.store = Store(self.db)
        self.ada, self.brook = self.tenant("Ada"), self.tenant("Brook")
        self.app = create_app(self.store, public_url=public_url)
        self.base = public_url or BASE  # where a browser finds it: a Secure cookie needs https

    def tenant(self, name: str) -> SimpleNamespace:
        view = self.store.for_account(self.store.create_account(name).id)
        view.create_project("core")
        return SimpleNamespace(name=name, view=view, key=view.create_key("laptop"))

    def browser(self, tenant: SimpleNamespace | None = None) -> TestClient:
        client = TestClient(self.app, base_url=self.base, follow_redirects=False)
        if tenant is not None:
            signed_in = client.post("/login", data={"key": tenant.key}, headers=SAME_ORIGIN)
            assert signed_in.status_code == 303
        return client

    def count(self, table: str) -> int:
        con = cairn.store.sqlite3.connect(self.db)
        try:
            return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        finally:
            con.close()


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    with TestClient(w.app, base_url=BASE, follow_redirects=False) as raw:
        w.raw = raw
        yield w
    w.store.close()


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def register(client, name="unit-agent", uris=(REDIRECT,), **more):
    body = {"client_name": name, "redirect_uris": list(uris), **more}
    return client.post("/register", json={k: v for k, v in body.items() if v is not None})


def registered(world, name="unit-agent", uris=(REDIRECT,)) -> str:
    r = register(world.raw, name, uris)
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def authorize(client, client_id: str, challenge: str, **overrides):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "state-123",
        "resource": RESOURCE,
        **overrides,
    }
    return client.get("/authorize?" + urlencode({k: v for k, v in params.items() if v is not None}))


def answer(response) -> dict[str, str]:
    """The query of the URL a response redirects to."""
    query = parse_qs(urlsplit(response.headers.get("location", "")).query)
    return {name: values[0] for name, values in query.items()}


class Hidden(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "hidden":
            self.fields[attributes["name"]] = attributes.get("value") or ""


def form_of(page) -> dict[str, str]:
    assert page.status_code == 200, page.text
    parser = Hidden()
    parser.feed(page.text)
    assert set(parser.fields) == {"request"}
    return parser.fields


def consent_url(browser, client_id: str, challenge: str, **overrides) -> str:
    r = authorize(browser, client_id, challenge, **overrides)
    assert r.status_code == 302 and urlsplit(r.headers["location"]).path == "/oauth/consent", r.text
    return r.headers["location"]


def decide(browser, fields: dict[str, str], decision: str | None = "approve"):
    data = fields if decision is None else {**fields, "decision": decision}
    return browser.post("/oauth/consent", data=data, headers=SAME_ORIGIN)


def exchange(client, client_id: str, code: str, verifier: str):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    return client.post("/token", data=data)


def refresh(client, client_id: str, refresh_token: str):
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    return client.post("/token", data=data)


def approved(world, browser, client_id: str) -> tuple[str, str]:
    """(code, verifier) after the person approved the client."""
    verifier, challenge = pkce()
    page = browser.get(consent_url(browser, client_id, challenge))
    done = decide(browser, form_of(page))
    assert done.status_code == 303 and done.headers["location"].startswith(REDIRECT + "?")
    return answer(done)["code"], verifier


def connected(world, tenant, name="unit-agent") -> SimpleNamespace:
    client_id = registered(world, name)
    code, verifier = approved(world, world.browser(tenant), client_id)
    tokens = exchange(world.raw, client_id, code, verifier)
    assert tokens.status_code == 200, tokens.text
    body = tokens.json()
    return SimpleNamespace(
        client_id=client_id, access=body["access_token"], refresh=body["refresh_token"], body=body
    )


def mcp(client, **headers):
    return client.post("/mcp", headers={**MCP_HEADERS, **headers}, json=TOOLS_LIST)


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def dump(db) -> str:
    con = cairn.store.sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()
