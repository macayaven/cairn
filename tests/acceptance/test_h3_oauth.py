"""H3 acceptance tests (frozen): an agent connects with only the URL (OAuth 2.1 on /mcp).

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstring of src/cairn/server.py; this is what it must add.

When
- OAuth is on when `public_url` is https, or http on a loopback host. The issuer is
  `public_url` and the protected resource is `public_url + "/mcp"`. Without such a
  `public_url` the OAuth paths answer 404 and API keys are the only way in.

Discovery (public, no key or session, and they write nothing)
- /mcp without valid credentials: 401 with `WWW-Authenticate: Bearer ...
  resource_metadata="<public_url>/.well-known/oauth-protected-resource/mcp"`.
- GET /.well-known/oauth-protected-resource/mcp and
  GET /.well-known/oauth-authorization-server (issuer, authorize, token, register and revoke
  endpoints at the root, PKCE S256).

Registration (POST /register, RFC 7591)
- Public clients (`token_endpoint_auth_method` "none") register without a secret. A
  redirect URI must be https, or http on a loopback host (localhost, 127.0.0.1, [::1]);
  anything else is refused with 400.

Authorization
- GET /authorize validates the request and redirects to GET /oauth/consent?...; neither
  writes. /oauth/consent needs a web session. Without one it answers 303 to
  /login?next=<its own path and query>, except when the browser says the navigation came
  from another site (`Sec-Fetch-Site: cross-site`): then it answers 200 with a
  `<meta http-equiv="refresh">` to the same URL, so the retry carries the SameSite=Strict
  session cookie if there is one.
- GET /login?next=... keeps `next` in the form; POST /login redirects to `next` after a
  successful sign-in when it is a path on this server ("/..." but not "//..."), else to "/".
- The consent page names the client, shows its redirect URI in full and the account that
  will be shared, and carries the request in hidden fields of one form posting to
  /oauth/consent with a `decision` of "approve" or "deny". Its Content-Security-Policy
  allows form-action 'self' plus the redirect URI's origin, nothing wider.
- Approve: 303 to the redirect URI with `code` and `state`. Deny: 303 to it with
  `error=access_denied` and `state`. A request naming another `resource` gets no code.

Tokens (POST /token, POST /revoke)
- A code works once, only with its PKCE verifier. The access token is opaque, bound to the
  account that approved and to the /mcp resource: /mcp accepts it, /api does not.
- A refresh rotates both tokens. Presenting a refresh token that was already rotated is
  refused and ends the whole grant.
- POST /revoke ends the grant for a public client that sends only `token` and `client_id`.
- Tokens, like keys, are stored only as SHA-256 digests. `Store.resolve_token(access_token)`
  returns Principal(account_id, kind "token", label = the client's name) or None.
  Deleting an account ends its grants.

Browser-based clients
- /register, /token and /revoke answer cross-origin requests (CORS) and are exempt from the
  UI's cross-origin refusal; /login and /oauth/consent are not.

The person stays in control
- GET /agents (web session) lists the account's connected agents by client name;
  POST /agents/<grant id>/revoke ends one, and its tokens stop working at once.

Unchanged
- API keys work as before on /mcp and /api, next to OAuth. Reads never write.
"""

import asyncio
import base64
import hashlib
import json
import re
import secrets
import sqlite3
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata
from pydantic import AnyUrl

from cairn.server import create_app
from cairn.store import Principal, Store

BASE = "http://127.0.0.1:8765"
RESOURCE = f"{BASE}/mcp"
PRM_PATH = "/.well-known/oauth-protected-resource/mcp"
REDIRECT = "http://127.0.0.1:33418/callback"
CLIENT_NAME = "h3-test-agent"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
TOOLS = {"projects", "new_project", "note", "recall", "page", "evidence", "overview"}
NOTE = "# Retry policy\n\nQuill retries 3 times before failing.\n"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def rpc(name: str, **arguments) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def bearer(token: str) -> dict:
    return {**MCP_HEADERS, "authorization": f"Bearer {token}"}


def payload(response) -> dict:
    """The JSON a tool returned, from a raw /mcp response."""
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def query(location: str) -> dict:
    return {name: values[0] for name, values in parse_qs(urlsplit(location).query).items()}


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class ConsentForm(HTMLParser):
    """The hidden fields of the form that posts to /oauth/consent."""

    def __init__(self) -> None:
        super().__init__()
        self.inside = False
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.inside = urlsplit(attributes.get("action") or "").path == "/oauth/consent"
        elif tag == "input" and self.inside and attributes.get("type") == "hidden":
            self.fields[attributes["name"]] = attributes.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False


def hidden_fields(html: str) -> dict[str, str]:
    form = ConsentForm()
    form.feed(html)
    assert form.fields, "the consent page has no form posting to /oauth/consent"
    return form.fields


class Person:
    """Someone's browser: a cookie jar, and the API key they sign in with."""

    def __init__(self, transport, key: str) -> None:
        self.http = httpx2.AsyncClient(transport=transport, base_url=BASE)
        self.key = key

    async def sign_in(self, next_path: str = "/"):
        data = {"key": self.key, "next": next_path}
        return await self.http.post("/login", data=data, headers=SAME_ORIGIN)

    async def consent_page(self, authorization_url: str):
        r = await self.http.get(authorization_url)
        assert r.status_code == 302, r.text
        consent_url = r.headers["location"]
        assert urlsplit(consent_url).path == "/oauth/consent"
        r = await self.http.get(consent_url)
        if r.status_code == 303:  # not signed in yet: /login?next=<consent>, then back
            location = r.headers["location"]
            assert location.startswith("/login?next="), location
            target = query(location)["next"]
            signed_in = await self.sign_in(target)
            assert signed_in.status_code == 303 and signed_in.headers["location"] == target
            r = await self.http.get(target)
        assert r.status_code == 200, r.text
        return r

    async def decide(self, page, decision: str) -> str:
        data = {**hidden_fields(page.text), "decision": decision}
        r = await self.http.post("/oauth/consent", data=data, headers=SAME_ORIGIN)
        assert r.status_code == 303, r.text
        return r.headers["location"]

    async def approve(self, authorization_url: str) -> str:
        return await self.decide(await self.consent_page(authorization_url), "approve")


class World:
    def __init__(self, tmp_path, public_url: str | None = BASE) -> None:
        self.db = tmp_path / "cairn.db"
        self.store = Store(self.db)
        self.ada = self.tenant("Ada", "core")
        self.brook = self.tenant("Brook", "other")
        self.app = create_app(self.store, public_url=public_url)

    def tenant(self, name: str, project: str) -> SimpleNamespace:
        view = self.store.for_account(self.store.create_account(name).id)
        view.create_project(project, f"{name}'s project")
        return SimpleNamespace(name=name, view=view, key=view.create_key("laptop"), project=project)


def play(tmp_path, scenario, public_url: str | None = BASE):
    """Run `scenario(world, transport, raw)` against a live app; `raw` sends no credentials."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    world = World(tmp_path, public_url)

    async def main():
        async with world.app.router.lifespan_context(world.app):
            transport = httpx2.ASGITransport(app=world.app)
            async with httpx2.AsyncClient(transport=transport, base_url=BASE) as raw:
                return await scenario(world, transport, raw)

    try:
        return asyncio.run(main())
    finally:
        world.store.close()


async def scopes(raw) -> str:
    supported = (await raw.get(PRM_PATH)).json().get("scopes_supported") or []
    return " ".join(supported)


async def register(raw, redirect_uris=(REDIRECT,), name: str = CLIENT_NAME):
    body = {
        "client_name": name,
        "redirect_uris": list(redirect_uris),
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }
    return await raw.post("/register", json=body)


async def authorization_url(raw, client_id: str, challenge: str, **overrides) -> str:
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
    if scope := await scopes(raw):
        params.setdefault("scope", scope)
    return "/authorize?" + urlencode(params)


async def exchange(raw, client_id: str, code: str, verifier: str):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": RESOURCE,
    }
    return await raw.post("/token", data=data)


async def refresh(raw, client_id: str, refresh_token: str):
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    return await raw.post("/token", data=data)


async def connect(raw, person: Person, name: str = CLIENT_NAME) -> SimpleNamespace:
    """The whole dance by hand: register, authorize, approve, exchange."""
    registered = await register(raw, name=name)
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]
    verifier, challenge = pkce()
    location = await person.approve(await authorization_url(raw, client_id, challenge))
    assert location.startswith(REDIRECT + "?"), location
    answer = query(location)
    assert answer["state"] == "state-123"
    tokens = await exchange(raw, client_id, answer["code"], verifier)
    assert tokens.status_code == 200, tokens.text
    body = tokens.json()
    assert body["token_type"].lower() == "bearer" and body["access_token"] and body["refresh_token"]
    return SimpleNamespace(
        client_id=client_id, access=body["access_token"], refresh=body["refresh_token"]
    )


class Memory(TokenStorage):
    def __init__(self) -> None:
        self.tokens = None
        self.client_info = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info):
        self.client_info = client_info


# Discovery


def test_mcp_challenges_and_the_metadata_is_public_and_read_only(tmp_path):
    async def scenario(world, transport, raw):
        before = dump(world.db)
        challenge = f'resource_metadata="{BASE}{PRM_PATH}"'
        for headers in (MCP_HEADERS, bearer("not-a-token"), {**MCP_HEADERS, "x-api-key": "nope"}):
            r = await raw.post("/mcp", headers=headers, json=rpc("projects"))
            assert r.status_code == 401
            assert challenge in r.headers["www-authenticate"]
            assert r.headers["www-authenticate"].lower().startswith("bearer")

        prm = await raw.get(PRM_PATH)
        assert prm.status_code == 200
        assert prm.json()["resource"] == RESOURCE
        assert prm.json()["authorization_servers"] == [BASE]

        meta = (await raw.get("/.well-known/oauth-authorization-server")).json()
        assert meta["issuer"] == BASE
        assert meta["authorization_endpoint"] == f"{BASE}/authorize"
        assert meta["token_endpoint"] == f"{BASE}/token"
        assert meta["registration_endpoint"] == f"{BASE}/register"
        assert meta["revocation_endpoint"] == f"{BASE}/revoke"
        assert meta["code_challenge_methods_supported"] == ["S256"]
        assert "none" in meta["token_endpoint_auth_methods_supported"]
        assert dump(world.db) == before

    play(tmp_path, scenario)


def test_without_a_usable_public_url_oauth_is_off(tmp_path):
    async def scenario(world, transport, raw):
        for path in (PRM_PATH, "/.well-known/oauth-authorization-server", "/authorize"):
            assert (await raw.get(path)).status_code == 404, path
        assert (await raw.post("/register", json={})).status_code == 404
        assert (await raw.post("/token", data={})).status_code == 404
        r = await raw.post("/mcp", headers=MCP_HEADERS, json=rpc("projects"))
        assert r.status_code == 401 and "resource_metadata" not in r.headers.get(
            "www-authenticate", ""
        )
        keyed = {**MCP_HEADERS, "x-api-key": world.ada.key}
        assert payload(await raw.post("/mcp", headers=keyed, json=rpc("projects")))["projects"]

    play(tmp_path, scenario, public_url=None)
    play(tmp_path / "plain-http", scenario, public_url="http://cairn.example.net")


# The whole flow, driven by the MCP SDK's own client


def test_the_sdk_client_connects_with_only_the_url(tmp_path):
    async def scenario(world, transport, raw):
        person = Person(transport, world.ada.key)
        storage, seen = Memory(), {}

        async def redirect_handler(url: str) -> None:
            seen["location"] = await person.approve(url)

        async def callback_handler() -> AuthorizationCodeResult:
            answer = query(seen["location"])
            return AuthorizationCodeResult(code=answer["code"], state=answer["state"])

        oauth = OAuthClientProvider(
            server_url=RESOURCE,
            client_metadata=OAuthClientMetadata(
                client_name=CLIENT_NAME,
                redirect_uris=[AnyUrl(REDIRECT)],
                token_endpoint_auth_method="none",
                grant_types=["authorization_code", "refresh_token"],
            ),
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        http = httpx2.AsyncClient(transport=transport, auth=oauth, timeout=30)
        async with (
            http,
            Client(streamable_http_client(RESOURCE, http_client=http), mode="legacy") as client,
        ):
            assert {tool.name for tool in (await client.list_tools()).tools} == TOOLS
            listed = json.loads((await client.call_tool("projects", {})).content[0].text)
            assert [b["name"] for b in listed["projects"]] == ["core"]  # Ada's, never Brook's
            noted = await client.call_tool("note", {"project": "core", "text": NOTE})
            assert json.loads(noted.content[0].text)["status"] == "saved"
            found = await client.call_tool("recall", {"query": "quill retries"})
            assert json.loads(found.content[0].text)["hits"]

        assert seen["location"].startswith(REDIRECT + "?")
        access, renewal = storage.tokens.access_token, storage.tokens.refresh_token
        assert world.store.resolve_token(access) == Principal(
            account_id=world.ada.view.account_id, kind="token", label=CLIENT_NAME
        )
        assert world.store.resolve_token("not-a-token") is None
        text = dump(world.db)
        assert access not in text and renewal not in text  # digests only
        assert world.ada.view.list_notes(world.ada.view.get_project("core").id)
        assert world.brook.view.search("quill") == []

    play(tmp_path, scenario)


# Consent


def test_the_consent_page_says_who_gets_access_to_what(tmp_path):
    async def scenario(world, transport, raw):
        person = Person(transport, world.ada.key)
        client_id = (await register(raw)).json()["client_id"]
        _, challenge = pkce()
        url = await authorization_url(raw, client_id, challenge)
        before = dump(world.db)

        # Signed out: straight to the login form, which keeps `next`.
        consent_url = (await raw.get(url)).headers["location"]
        consent_path = urlsplit(consent_url).path + "?" + urlsplit(consent_url).query
        signed_out = await raw.get(consent_url)
        assert signed_out.status_code == 303
        assert query(signed_out.headers["location"])["next"] == consent_path
        form = await raw.get(signed_out.headers["location"])
        assert form.status_code == 200 and 'name="next"' in form.text
        # Sent here by another site: one same-site retry, so a Strict cookie can come along.
        bounced = await raw.get(consent_url, headers={"sec-fetch-site": "cross-site"})
        assert bounced.status_code == 200 and 'http-equiv="refresh"' in bounced.text.lower()
        assert "decision" not in bounced.text
        assert dump(world.db) == before  # nothing so far has written

        page = await person.consent_page(url)
        assert CLIENT_NAME in page.text and REDIRECT in page.text and "Ada" in page.text
        assert "Brook" not in page.text and world.ada.key not in page.text
        assert 'value="approve"' in page.text and 'value="deny"' in page.text
        policy = page.headers["content-security-policy"]
        form_action = next(p for p in policy.split(";") if p.strip().startswith("form-action"))
        assert form_action.split() == ["form-action", "'self'", "http://127.0.0.1:33418"]

    play(tmp_path, scenario)


def test_deny_returns_an_error_and_grants_nothing(tmp_path):
    async def scenario(world, transport, raw):
        person = Person(transport, world.ada.key)
        client_id = (await register(raw)).json()["client_id"]
        verifier, challenge = pkce()
        page = await person.consent_page(await authorization_url(raw, client_id, challenge))
        fields = hidden_fields(page.text)
        location = await person.decide(page, "deny")
        assert location.startswith(REDIRECT + "?")
        assert query(location) == {"error": "access_denied", "state": "state-123"}
        # The same form cannot be turned into an approval afterwards.
        again = await person.http.post(
            "/oauth/consent", data={**fields, "decision": "approve"}, headers=SAME_ORIGIN
        )
        assert "code=" not in again.headers.get("location", "")
        assert (await person.http.get("/agents")).status_code == 200
        assert CLIENT_NAME not in (await person.http.get("/agents")).text
        assert (await exchange(raw, client_id, "made-up-code", verifier)).status_code == 400

    play(tmp_path, scenario)


def test_login_only_follows_next_to_this_server(tmp_path):
    async def scenario(world, transport, raw):
        for target, expected in (
            ("/agents", "/agents"),
            ("https://evil.example/", "/"),
            ("//evil.example/", "/"),
            ("/\\evil.example/", "/"),
            ("", "/"),
        ):
            person = Person(transport, world.ada.key)
            r = await person.sign_in(target)
            assert r.status_code == 303 and r.headers["location"] == expected, target

    play(tmp_path, scenario)


# Codes and tokens


def test_a_code_works_once_and_only_with_its_verifier(tmp_path):
    async def scenario(world, transport, raw):
        person = Person(transport, world.ada.key)
        client_id = (await register(raw)).json()["client_id"]
        verifier, challenge = pkce()
        location = await person.approve(await authorization_url(raw, client_id, challenge))
        code = query(location)["code"]

        wrong = await exchange(raw, client_id, code, "w" * 64)
        assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
        first = await exchange(raw, client_id, code, verifier)
        assert first.status_code == 200
        second = await exchange(raw, client_id, code, verifier)
        assert second.status_code == 400 and second.json()["error"] == "invalid_grant"

        other = (await register(raw, name="another-agent")).json()["client_id"]
        _, challenge = pkce()
        location = await person.approve(await authorization_url(raw, client_id, challenge))
        stolen = await exchange(raw, other, query(location)["code"], verifier)
        assert stolen.status_code == 400  # a code belongs to the client it was issued to

    play(tmp_path, scenario)


def test_a_token_is_bound_to_the_mcp_resource_and_its_account(tmp_path):
    async def scenario(world, transport, raw):
        ada = await connect(raw, Person(transport, world.ada.key))
        brook = await connect(raw, Person(transport, world.brook.key), name="brooks-agent")

        mine = payload(await raw.post("/mcp", headers=bearer(ada.access), json=rpc("projects")))
        theirs = payload(await raw.post("/mcp", headers=bearer(brook.access), json=rpc("projects")))
        assert [b["name"] for b in mine["projects"]] == ["core"]
        assert [b["name"] for b in theirs["projects"]] == ["other"]

        # The REST API is another audience: it takes keys, not this token.
        api = await raw.get("/api/projects", headers={"authorization": f"Bearer {ada.access}"})
        assert api.status_code == 401
        keyed = await raw.get("/api/projects", headers={"authorization": f"Bearer {world.ada.key}"})
        assert keyed.status_code == 200

        # A request for some other resource never yields a code.
        person = Person(transport, world.ada.key)
        _, challenge = pkce()
        url = await authorization_url(
            raw, ada.client_id, challenge, resource="https://elsewhere.example/mcp"
        )
        r = await person.http.get(url)
        location = r.headers.get("location", "")
        assert "code=" not in location
        if urlsplit(location).path == "/oauth/consent":
            page = await person.http.get(location)
            assert page.status_code >= 400 or "decision" not in page.text

    play(tmp_path, scenario)


def test_refresh_rotates_and_reuse_ends_the_grant(tmp_path):
    async def scenario(world, transport, raw):
        grant = await connect(raw, Person(transport, world.ada.key))
        renewed = await refresh(raw, grant.client_id, grant.refresh)
        assert renewed.status_code == 200, renewed.text
        new = renewed.json()
        assert new["access_token"] != grant.access and new["refresh_token"] != grant.refresh
        ok = await raw.post("/mcp", headers=bearer(new["access_token"]), json=rpc("projects"))
        assert ok.status_code == 200
        old = await raw.post("/mcp", headers=bearer(grant.access), json=rpc("projects"))
        assert old.status_code == 401  # rotation retires the old access token too

        replay = await refresh(raw, grant.client_id, grant.refresh)
        assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
        dead = await raw.post("/mcp", headers=bearer(new["access_token"]), json=rpc("projects"))
        assert dead.status_code == 401  # someone replayed a rotated token: the grant is over
        assert (await refresh(raw, grant.client_id, new["refresh_token"])).status_code == 400

    play(tmp_path, scenario)


def test_a_public_client_can_revoke_its_token(tmp_path):
    async def scenario(world, transport, raw):
        grant = await connect(raw, Person(transport, world.ada.key))
        data = {"token": grant.access, "client_id": grant.client_id}
        assert (await raw.post("/revoke", data=data)).status_code == 200
        gone = await raw.post("/mcp", headers=bearer(grant.access), json=rpc("projects"))
        assert gone.status_code == 401
        assert (await refresh(raw, grant.client_id, grant.refresh)).status_code == 400
        assert world.store.resolve_token(grant.access) is None

    play(tmp_path, scenario)


# Registration


def test_redirect_uris_must_be_https_or_loopback(tmp_path):
    async def scenario(world, transport, raw):
        for good in (
            "https://claude.ai/api/mcp/auth_callback",
            "http://localhost:8787/callback",
            "http://127.0.0.1:33418",
            "http://[::1]:9999/cb",
        ):
            r = await register(raw, redirect_uris=[good])
            assert r.status_code == 201, (good, r.text)
            assert r.json().get("client_secret") in (None, "")
        for bad in (
            "javascript:alert(1)",
            "data:text/html,x",
            "http://evil.example/callback",
            "http://localhost.evil.example/callback",
            "myapp://callback",
            "https://ok.example/cb#fragment",
        ):
            r = await register(raw, redirect_uris=[bad])
            assert r.status_code == 400, (bad, r.text)
            mixed = await register(raw, redirect_uris=[REDIRECT, bad])
            assert mixed.status_code == 400, bad
        assert (await register(raw, redirect_uris=[])).status_code == 400

    play(tmp_path, scenario)


def test_browser_clients_reach_the_oauth_endpoints_but_not_the_forms(tmp_path):
    async def scenario(world, transport, raw):
        cross = {"origin": "https://app.example", "sec-fetch-site": "cross-site"}
        registered = await raw.post(
            "/register",
            headers=cross,
            json={
                "client_name": "web-agent",
                "redirect_uris": ["https://app.example/callback"],
                "token_endpoint_auth_method": "none",
            },
        )
        assert registered.status_code == 201
        assert registered.headers["access-control-allow-origin"] in ("*", "https://app.example")
        token = await raw.post("/token", headers=cross, data={"grant_type": "authorization_code"})
        assert token.status_code != 403 and "access-control-allow-origin" in token.headers
        preflight = await raw.options(
            "/token", headers={**cross, "access-control-request-method": "POST"}
        )
        assert preflight.status_code in (200, 204)

        for path in ("/login", "/oauth/consent", "/logout"):
            assert (await raw.post(path, headers=cross, data={"x": "y"})).status_code == 403, path

    play(tmp_path, scenario)


# The person stays in control


def test_connected_agents_are_listed_and_revocable(tmp_path):
    async def scenario(world, transport, raw):
        ada, brook = Person(transport, world.ada.key), Person(transport, world.brook.key)
        first = await connect(raw, ada)
        second = await connect(raw, ada, name="second-agent")
        await connect(raw, brook, name="brooks-agent")

        page = await ada.http.get("/agents")
        assert page.status_code == 200
        assert CLIENT_NAME in page.text and "second-agent" in page.text
        assert "brooks-agent" not in page.text
        assert first.access not in page.text and world.ada.key not in page.text
        assert (await raw.get("/agents")).status_code == 303  # the page needs a session

        actions = sorted(set(re.findall(r'action="(/agents/[^"/]+/revoke)"', page.text)))
        assert len(actions) == 2, actions
        outsider = await brook.http.post(actions[0], headers=SAME_ORIGIN)
        assert outsider.status_code == 404  # another account cannot revoke it
        assert (await ada.http.post(actions[0], headers=SAME_ORIGIN)).status_code == 303

        results = {}
        for name, grant in (("first", first), ("second", second)):
            r = await raw.post("/mcp", headers=bearer(grant.access), json=rpc("projects"))
            results[name] = r.status_code
        assert sorted(results.values()) == [200, 401]  # exactly one grant ended
        ended = first if results["first"] == 401 else second
        assert (await refresh(raw, ended.client_id, ended.refresh)).status_code == 400
        left = re.findall(r'action="(/agents/[^"/]+/revoke)"', (await ada.http.get("/agents")).text)
        assert left == [actions[1]]

    play(tmp_path, scenario)


def test_deleting_an_account_ends_its_grants(tmp_path):
    async def scenario(world, transport, raw):
        ada = await connect(raw, Person(transport, world.ada.key))
        brook = await connect(raw, Person(transport, world.brook.key), name="brooks-agent")
        assert world.store.delete_account(world.ada.view.account_id) is True
        gone = await raw.post("/mcp", headers=bearer(ada.access), json=rpc("projects"))
        assert gone.status_code == 401
        assert world.store.resolve_token(ada.access) is None
        kept = await raw.post("/mcp", headers=bearer(brook.access), json=rpc("projects"))
        assert kept.status_code == 200

    play(tmp_path, scenario)


# Unchanged


def test_api_keys_work_next_to_oauth_and_reads_never_write(tmp_path):
    async def scenario(world, transport, raw):
        grant = await connect(raw, Person(transport, world.ada.key))
        person = Person(transport, world.ada.key)
        await person.sign_in()
        client_id = (await register(raw, name="pending-agent")).json()["client_id"]
        _, challenge = pkce()
        url = await authorization_url(raw, client_id, challenge)
        before = dump(world.db)

        for headers in (
            {**MCP_HEADERS, "x-api-key": world.ada.key},
            bearer(world.ada.key),
            bearer(grant.access),
        ):
            listed = payload(await raw.post("/mcp", headers=headers, json=rpc("projects")))
            assert [b["name"] for b in listed["projects"]] == ["core"]
            payload(await raw.post("/mcp", headers=headers, json=rpc("recall", query="quill")))
        await raw.get(PRM_PATH)
        await raw.get("/.well-known/oauth-authorization-server")
        await person.consent_page(url)  # GET /authorize, GET /oauth/consent
        await person.http.get("/agents")
        assert dump(world.db) == before

    play(tmp_path, scenario)
