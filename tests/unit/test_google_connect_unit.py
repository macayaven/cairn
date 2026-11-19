"""A Google account connected in the browser (`serve --google-connect`): the switch, the flow,
a run, reconnect and disconnect, and the page.

Google is an httpx.MockTransport written from docs/google-auth.md (and, where it is silent,
docs/google-oauth-installed.md and cli_google.py, as the module says). Every name, address,
code and token here is made up; nothing leaves this machine.
"""

import asyncio
import base64
import dataclasses
import hashlib
import html
import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient
from test_store_unit import rows

import cairn.oauth
import cairn.store
import cairn.store.hosted
import cairn.web.google_connect as gc
from cairn import server
from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.sources import KINDS, NoteDraft, Pulled, SourceError, holds_secret
from cairn.sources.google_auth import load_credential
from cairn.sources.runner import (
    HELD_TITLE,
    WITHHELD,
    preview_new_source,
    preview_source,
    run_source,
    source_client,
    sync_all,
)
from cairn.store import Store
from cairn.store.hosted import RECONNECT, UNOPENED
from cairn.web.google_connect import read_switch
from wait_helpers import wait_for_event

BASE = "https://cairn.example.test"
CLIENT_ID = "g2-web-client.apps.example.test"
CLIENT_SECRET = "g2-web-secret-0123456789"
KEY = bytes(range(32))
CLIENT_JSON = json.dumps({"web": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}})
KEY_B64 = base64.b64encode(KEY).decode()
SIGN_IN_ENV = {"CAIRN_GITHUB_CLIENT_ID": "gh-id", "CAIRN_GITHUB_CLIENT_SECRET": "gh-secret"}
SWITCH_ENV = {"CAIRN_GOOGLE_CONNECT_CLIENT": CLIENT_JSON, "CAIRN_GOOGLE_CONNECT_KEY": KEY_B64}


# The switch: serve


def serve(monkeypatch, tmp_path, argv, env):
    for name in ("CAIRN_GOOGLE_CONNECT_CLIENT", "CAIRN_GOOGLE_CONNECT_KEY", "CAIRN_ALLOWED_EMAILS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    db = tmp_path / "data" / "cairn.db"
    argv = ["serve", "--db", str(db), "--public-url", BASE, "--sign-in", "github", *argv]
    return server.main(argv), apps, db


def test_serve_with_the_switch_and_both_variables_starts(tmp_path, monkeypatch, capsys):
    argv = ["--google-connect", "--google-connect-for", "ada@example.test"]
    code, apps, _db = serve(monkeypatch, tmp_path, argv, {**SIGN_IN_ENV, **SWITCH_ENV})
    assert code == 0 and len(apps) == 1
    said = capsys.readouterr()
    for secret in (CLIENT_SECRET, KEY_B64, CLIENT_ID):
        assert secret not in said.out + said.err


@pytest.mark.parametrize(
    "env, named",
    [
        ({"CAIRN_GOOGLE_CONNECT_KEY": KEY_B64}, "CAIRN_GOOGLE_CONNECT_CLIENT"),
        ({"CAIRN_GOOGLE_CONNECT_CLIENT": CLIENT_JSON}, "CAIRN_GOOGLE_CONNECT_KEY"),
        ({**SWITCH_ENV, "CAIRN_GOOGLE_CONNECT_CLIENT": "{not json " + CLIENT_SECRET}, "_CLIENT"),
        (
            {
                **SWITCH_ENV,
                "CAIRN_GOOGLE_CONNECT_CLIENT": json.dumps(
                    {"installed": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}}
                ),
            },
            "_CLIENT",
        ),
        (
            {
                **SWITCH_ENV,
                "CAIRN_GOOGLE_CONNECT_CLIENT": json.dumps({"web": {"client_id": CLIENT_ID}}),
            },
            "_CLIENT",
        ),
        ({**SWITCH_ENV, "CAIRN_GOOGLE_CONNECT_KEY": base64.b64encode(bytes(16)).decode()}, "_KEY"),
        ({**SWITCH_ENV, "CAIRN_GOOGLE_CONNECT_KEY": "not base64 at all!" + KEY_B64}, "_KEY"),
        ({**SWITCH_ENV, "CAIRN_GOOGLE_CONNECT_KEY": "   "}, "CAIRN_GOOGLE_CONNECT_KEY"),
        (  # the sign-in client's own id: source permission stays apart from sign-in
            {
                **SWITCH_ENV,
                "CAIRN_GITHUB_CLIENT_ID": CLIENT_ID,
            },
            "sign-in",
        ),
    ],
)
def test_serve_refuses_a_missing_or_malformed_client_or_key_before_the_database(
    tmp_path, monkeypatch, capsys, env, named
):
    code, apps, db = serve(monkeypatch, tmp_path, ["--google-connect"], {**SIGN_IN_ENV, **env})
    assert code == 2 and apps == []
    assert not db.parent.exists()  # nothing was opened, not even its folder
    said = capsys.readouterr().err
    assert named in said and len(said.strip().splitlines()) == 1
    for secret in (CLIENT_SECRET, KEY_B64, CLIENT_ID, "{not json", "not base64"):
        assert secret not in said


def test_the_sign_in_providers_google_client_is_never_the_connect_client(
    tmp_path, monkeypatch, capsys
):
    env = {
        **SIGN_IN_ENV,
        **SWITCH_ENV,
        "CAIRN_GOOGLE_CLIENT_ID": CLIENT_ID,
        "CAIRN_GOOGLE_CLIENT_SECRET": "x",
    }
    code, apps, _ = serve(monkeypatch, tmp_path, ["--google-connect"], env)
    assert code == 2 and apps == [] and "sign-in" in capsys.readouterr().err


def test_the_addresses_need_the_switch(tmp_path, monkeypatch, capsys):
    argv = ["--google-connect-for", "ada@example.test"]
    code, apps, db = serve(monkeypatch, tmp_path, argv, {**SIGN_IN_ENV, **SWITCH_ENV})
    assert code == 2 and apps == [] and not db.parent.exists()
    assert "--google-connect" in capsys.readouterr().err


def test_an_address_that_is_not_one_is_refused(tmp_path, monkeypatch, capsys):
    argv = ["--google-connect", "--google-connect-for", "not an address"]
    code, apps, db = serve(monkeypatch, tmp_path, argv, {**SIGN_IN_ENV, **SWITCH_ENV})
    assert code == 2 and apps == [] and not db.parent.exists()
    assert "not an address" not in capsys.readouterr().err


def test_the_switch_needs_sign_in_and_a_public_url(tmp_path, monkeypatch, capsys):
    for name, value in SWITCH_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: None)
    db = tmp_path / "data" / "cairn.db"
    assert server.main(["serve", "--db", str(db), "--public-url", BASE, "--google-connect"]) == 2
    assert "--sign-in" in capsys.readouterr().err
    for name, value in SIGN_IN_ENV.items():
        monkeypatch.setenv(name, value)
    argv = ["serve", "--db", str(db), "--sign-in", "github", "--google-connect"]
    assert server.main(argv) == 2
    assert "--public-url" in capsys.readouterr().err
    assert not db.parent.exists()


# The flow, against a stand-in for Google

ADA = "ada@example.test"  # who signs in to cairn
BOB = "bob@example.test"
ADA_GOOGLE = "ada.lovelace@gmail.example.test"  # the Google account that consents
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GITHUB_IDS = {ADA: 7, BOB: 8}
NOT_GRANTED_MAIL = "Google did not grant reading your mail. Nothing was connected"
NOT_GRANTED_DRIVE = "Google did not grant reading your Drive files. Nothing was connected"


def b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def id_token(email: str, **claims) -> str:
    body = {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": f"sub-{email}",
        "email": email, "email_verified": True, "exp": 4_102_444_800, "iat": 4_102_441_200,
        **claims,
    }  # fmt: skip
    return f"{b64({'alg': 'RS256', 'typ': 'JWT'})}.{b64(body)}.c2lnbmF0dXJl"


class GoogleStandIn:
    """Google's token and revocation endpoints as docs/google-auth.md and
    docs/google-oauth-installed.md describe them, and the consent the person gives."""

    def __init__(self) -> None:
        self.email = ADA_GOOGLE
        self.grant = None  # the scope the exchange answers; None: what was asked
        self.refresh = True
        self.error = None  # an `error` the exchange answers with, 400
        self.claims: dict = {}
        self.down = False
        self.revoke_down = False
        self.revoke_status = None  # a status the revocation endpoint answers with instead
        self.refresh_error = None  # an `error` a refresh of a live token answers with
        self.issued: dict[str, dict] = {}
        self.tokens: dict[str, str] = {}  # live refresh token -> Google account
        self.exchanges = 0
        self.revoked: list[str] = []
        self.seen: list[httpx.Request] = []

    def consent(self, location: str) -> dict:
        """The person consents at Google: the code and state it sends the browser back with."""
        url = httpx.URL(location)
        assert str(url.copy_with(query=None)) == AUTH_URL
        code = f"4/g2-code-{len(self.issued)}-{len(self.seen)}-0123456789"
        self.issued[code] = {
            "challenge": url.params["code_challenge"],
            "redirect_uri": url.params["redirect_uri"],
            "scope": url.params["scope"],
            "email": self.email,
        }
        return {"code": code, "state": url.params["state"]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.down:
            raise httpx.ConnectError("down", request=request)
        url = str(request.url.copy_with(query=None))
        if url == TOKEN_URL:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            assert form["client_id"] == CLIENT_ID and form["client_secret"] == CLIENT_SECRET
            if form["grant_type"] == "refresh_token":
                if form["refresh_token"] not in self.tokens:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                if self.refresh_error:
                    return httpx.Response(401, json={"error": self.refresh_error})
                return httpx.Response(
                    200, json={"access_token": "ya29.g2-access-0123", "expires_in": 3599,
                               "token_type": "Bearer", "scope": GMAIL_SCOPE},
                )  # fmt: skip
            assert form["grant_type"] == "authorization_code"
            self.exchanges += 1
            issued = self.issued.pop(form["code"], None)
            digest = hashlib.sha256(form["code_verifier"].encode()).digest()
            if (
                issued is None
                or issued["challenge"] != base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            ):
                return httpx.Response(400, json={"error": "invalid_grant"})
            assert form["redirect_uri"] == issued["redirect_uri"]
            if self.error:
                return httpx.Response(400, json={"error": self.error})
            answer = {
                "access_token": "ya29.g2-access-0123", "expires_in": 3599,
                "token_type": "Bearer", "scope": self.grant or issued["scope"],
                "id_token": id_token(issued["email"], **self.claims),
            }  # fmt: skip
            if self.refresh:
                token = f"1//g2-refresh-{len(self.seen)}-0123456789abcdef"
                self.tokens[token] = issued["email"]
                answer["refresh_token"] = token
            return httpx.Response(200, json=answer)
        if url == REVOKE_URL:
            assert request.method == "POST"
            if self.revoke_down:
                raise httpx.ConnectError("down", request=request)
            if self.revoke_status is not None:
                return httpx.Response(self.revoke_status, json={"error": "server_error"})
            token = request.url.params["token"]
            address = self.tokens.get(token)
            if address is None:
                return httpx.Response(400, json={"error": "invalid_token"})
            # docs/google-auth.md B.4: every grant of that account to the project goes with it
            for other in [t for t, a in self.tokens.items() if a == address]:
                del self.tokens[other]
            self.revoked.append(token)
            return httpx.Response(200)
        return self.api(request)

    def api(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"an unexpected request: {request.method} {request.url.path}")


class World:
    """A server with the switch on (or off), GitHub sign-in and Google stand-ins."""

    def __init__(self, tmp_path, offered=(ADA, BOB), switch=True) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.db = tmp_path / "cairn.db"
        self.store = Store(self.db)
        self.google = GoogleStandIn()
        self.signing_in = ADA
        self.offered, self.switch = offered, switch
        self.restart()
        self.clients: list[TestClient] = []

    def restart(self) -> None:
        """A new server process on the same database and the same environment."""
        sign_in = SignIn(
            providers=(GitHub("gh-id", "gh-secret"),),
            http=httpx.AsyncClient(transport=httpx.MockTransport(self.github)),
            join="personal",
        )
        self.app = create_app(
            self.store,
            public_url=BASE,
            sign_in=sign_in,
            source_http=lambda: source_client(httpx.MockTransport(self.google.handler)),
            google_connect=read_switch(SWITCH_ENV, self.offered) if self.switch else None,
        )

    def github(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        email = self.signing_in
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_token"})
        if url == "https://api.github.com/user":
            name = email.split("@")[0]
            return httpx.Response(200, json={"id": GITHUB_IDS[email], "login": name, "name": name})
        if url == "https://api.github.com/user/emails":
            return httpx.Response(200, json=[{"email": email, "primary": True, "verified": True}])
        return httpx.Response(404)

    def person(self, email: str) -> TestClient:
        client = TestClient(self.app, base_url=BASE)
        self.clients.append(client)
        self.signing_in = email
        started = client.get("/auth/github", follow_redirects=False)
        state = httpx.URL(started.headers["location"]).params["state"]
        signed = client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
        assert signed.status_code == 200, signed.text
        return client

    def close(self) -> None:
        for client in self.clients:
            client.close()
        self.store.close()


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.close()


def dump(db) -> str:
    """The database's content, but for the sessions table (sign-in writes it)."""
    con = sqlite3.connect(db)
    try:
        return "\n".join(
            line
            for line in con.iterdump()
            if not line.startswith(('INSERT INTO "sessions"', 'INSERT INTO "audit'))
        )
    finally:
        con.close()


def start(client: TestClient, kind: str = "gmail", account: str | None = None) -> httpx.Response:
    form = {"kind": kind, **({"account": account} if account else {})}
    return client.post("/integrations/google/connect", data=form, follow_redirects=False)


def come_back(world: World, client: TestClient, query: dict) -> httpx.Response:
    """Google sends the browser back: it arrives with no session (from another site), gets the
    page that moves on, and follows it as this site's request, with its session."""
    stranger = TestClient(world.app, base_url=BASE)  # no cookie at all, as a browser sends it
    page = stranger.get(f"/integrations/google/back?{urlencode(query)}", follow_redirects=False)
    stranger.close()
    assert page.status_code == 200 and "set-cookie" not in page.headers
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store"
    assert "stylesheet" not in page.text and "<script" not in page.text
    onward = html.unescape(re.search(r'url=([^"]+)"', page.text).group(1))
    assert onward.startswith("/integrations/google/finish?")
    return client.get(onward, follow_redirects=False)


def connect(world: World, client: TestClient, kind: str = "gmail", account=None):
    started = start(client, kind, account)
    assert started.status_code == 303, started.text
    return come_back(world, client, world.google.consent(started.headers["location"]))


def said(client: TestClient, response: httpx.Response) -> str:
    assert response.status_code == 303, response.text
    told(response)
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    return html.unescape(page.text)


def told(response: httpx.Response) -> dict:
    """The outcome a handler wrote: in its sealed cookie, never in the address."""
    assert response.headers["location"] == "/sources#google-connect"
    found = [
        value for value in response.headers.get_list("set-cookie")
        if value.startswith(f"{gc.SAID_COOKIE}=")
    ]  # fmt: skip
    assert len(found) == 1
    attributes = found[0].lower()
    assert "path=/sources" in attributes and "httponly" in attributes
    assert "samesite=lax" in attributes and "secure" in attributes
    sealed = found[0].split(";", 1)[0].split("=", 1)[1]
    return json.loads(cairn.store.hosted.Sealer(KEY).open(sealed, gc.OUTCOME))


def held(db) -> list[tuple]:
    return rows(
        db, "SELECT account_id, credential, kind, owner, address, scopes FROM credential_tokens"
    )


def test_connect_sends_the_browser_to_googles_consent_with_exactly_these_parameters(world):
    ada = world.person(ADA)
    started = start(ada)
    assert started.status_code == 303
    url = httpx.URL(started.headers["location"])
    assert str(url.copy_with(query=None)) == AUTH_URL
    pairs = parse_qsl(url.query.decode())
    names = sorted(name for name, _ in pairs)
    assert names == sorted(
        ["client_id", "redirect_uri", "response_type", "scope", "state", "code_challenge",
         "code_challenge_method", "access_type", "prompt"]
    )  # fmt: skip
    params = dict(pairs)
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == f"{BASE}/integrations/google/back"
    assert params["response_type"] == "code"
    assert params["scope"] == f"{GMAIL_SCOPE} openid email"
    assert params["code_challenge_method"] == "S256" and len(params["code_challenge"]) == 43
    assert params["access_type"] == "offline" and params["prompt"] == "consent"
    assert len(params["state"]) >= 32
    cookie = started.headers["set-cookie"]
    assert cookie.startswith("google_connect=")
    for flag in ("httponly", "path=/integrations/google", "samesite=lax", "secure", "max-age=600"):
        assert flag in cookie.lower()
    drive = httpx.URL(start(ada, "google_drive").headers["location"]).params["scope"]
    assert drive == f"{DRIVE_SCOPE} openid email"
    assert world.google.seen == []  # nothing was sent to Google by the server yet


def test_success_stores_one_sealed_row_and_writes_no_source_and_no_note(world):
    ada = world.person(ADA)
    before = rows(world.db, "SELECT count(*) FROM sources") + rows(
        world.db, "SELECT count(*) FROM notes"
    )
    page = said(ada, connect(world, ada))
    assert "Connected." in page and ADA_GOOGLE in page
    assert world.google.exchanges == 1 and len(world.google.seen) == 1
    ((account_id, name, kind, owner, address, scopes),) = held(world.db)
    assert (kind, address, scopes) == ("gmail", ADA_GOOGLE, f"{GMAIL_SCOPE} openid email")
    (token,) = world.google.tokens
    assert not holds_secret(dump(world.db), (token,))
    assert token not in page
    assert world.store.for_account(account_id, owner).google_token(name) == token
    after = rows(world.db, "SELECT count(*) FROM sources") + rows(
        world.db, "SELECT count(*) FROM notes"
    )
    assert after == before
    assert ada.cookies.get("google_connect") is None  # the request is over


def test_the_way_back_alone_writes_nothing_and_needs_the_session(world):
    ada = world.person(ADA)
    started = start(ada)
    query = world.google.consent(started.headers["location"])
    before = dump(world.db)
    stranger = TestClient(world.app, base_url=BASE)
    page = stranger.get(f"/integrations/google/back?{urlencode(query)}", follow_redirects=False)
    finish = html.unescape(re.search(r'url=([^"]+)"', page.text).group(1))
    assert stranger.get(finish, follow_redirects=False).status_code in (303, 401, 403)
    stranger.close()
    assert dump(world.db) == before and world.google.exchanges == 0


@pytest.mark.parametrize("spoil", ["wrong", "old", "another person", "no cookie"])
def test_a_state_that_is_not_this_ones_sends_and_stores_nothing(world, monkeypatch, spoil):
    ada = world.person(ADA)
    started = start(ada)
    query = world.google.consent(started.headers["location"])
    client = ada
    if spoil == "wrong":
        query["state"] = query["state"][:-2] + ("xx" if not query["state"].endswith("xx") else "yy")
    elif spoil == "old":
        later = time.time() + 601
        monkeypatch.setattr(gc, "_now", lambda: later)
    elif spoil == "another person":
        bob = world.person(BOB)
        sealed = started.headers["set-cookie"].split(";")[0].split("=", 1)[1]
        bob.cookies.set(
            "google_connect", sealed, domain="cairn.example.test", path="/integrations/google"
        )
        client = bob
    else:
        ada.cookies.clear()
        client = world.person(ADA)
    before = dump(world.db)
    response = come_back(world, client, query)
    assert told(response)["said"] == "stale"
    page = said(client, response)
    assert "The connection was not finished" in page and 'role="alert"' in page
    assert dump(world.db) == before and world.google.exchanges == 0


def test_the_flows_cookie_reads_as_nothing_and_opens_after_a_restart(world):
    ada = world.person(ADA)
    started = start(ada)
    sealed = started.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    query = world.google.consent(started.headers["location"])
    raw = base64.urlsafe_b64decode(sealed + "=" * (-len(sealed) % 4))
    for plain in (query["state"], "verifier", "gmail", "workspace", "person"):
        assert plain.encode() not in raw
    assert sealed != start(ada).headers["set-cookie"].split(";")[0].split("=", 1)[1]
    world.restart()
    again = TestClient(world.app, base_url=BASE)
    world.clients.append(again)
    again.cookies.update(ada.cookies)
    again.cookies.set(
        "google_connect", sealed, domain="cairn.example.test", path="/integrations/google"
    )
    assert "Connected." in said(again, come_back(world, again, query))


def test_the_flow_is_sealed_for_its_own_purpose():
    sealer = cairn.store.hosted.Sealer(KEY)
    flows = gc.Flows(sealer)
    sealed = flows.seal({"state": "s"}, ttl=60)
    assert flows.open(sealed)["state"] == "s"
    assert sealer.open(sealed, cairn.store.hosted.associated("w", "c", None)) is None
    token = sealer.seal(
        '{"state": "s", "exp": 1e12}', cairn.store.hosted.associated("w", "c", None)
    )
    assert flows.open(token) is None
    assert gc.Flows(cairn.store.hosted.Sealer(bytes(32))).open(sealed) is None
    assert flows.open("not sealed") is None and flows.open(None) is None


def test_a_state_is_settled_once(world):
    ada = world.person(ADA)
    started = start(ada)
    sealed = started.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    query = world.google.consent(started.headers["location"])
    assert "Connected." in said(ada, come_back(world, ada, query))
    ada.cookies.set(
        "google_connect", sealed, domain="cairn.example.test", path="/integrations/google"
    )
    before, exchanges = dump(world.db), world.google.exchanges
    response = come_back(world, ada, query)
    assert told(response)["said"] == "stale"
    assert dump(world.db) == before and world.google.exchanges == exchanges


@pytest.mark.parametrize(
    "kind, setting, words",
    [
        ("gmail", {"grant": "openid email"}, NOT_GRANTED_MAIL),
        ("google_drive", {"grant": f"{GMAIL_SCOPE} openid email"}, NOT_GRANTED_DRIVE),
        ("gmail", {"refresh": False}, "Google gave no lasting token"),
        ("gmail", {"error": "admin_policy_enforced"}, "administrator's policy"),
        ("gmail", {"error": "org_internal"}, "its own organisation"),
        ("gmail", {"error": "invalid_client"}, "checks its Google client"),
        ("gmail", {"error": "something_new"}, "Google refused the connection"),
        ("gmail", {"down": True}, "Google could not be reached"),
        ("gmail", {"claims": {"aud": "someone-else"}}, "did not name the account"),
        ("gmail", {"claims": {"iss": "https://id.example.test"}}, "did not name the account"),
        ("gmail", {"claims": {"email_verified": False}}, "did not name the account"),
        ("gmail", {"claims": {"exp": 1}}, "did not name the account"),
        ("gmail", {"claims": {"sub": ""}}, "did not name the account"),
        ("gmail", {"claims": {"sub": None}}, "did not name the account"),
        ("gmail", {"claims": {"sub": "x" * 256}}, "did not name the account"),
    ],
)  # fmt: skip
def test_each_refusal_stores_nothing_and_is_said_on_the_page(world, kind, setting, words):
    ada = world.person(ADA)
    for name, value in setting.items():
        setattr(world.google, name, value)
    before = dump(world.db)
    page = said(ada, connect(world, ada, kind))
    assert words in page and 'role="alert"' in page
    assert dump(world.db) == before and held(world.db) == []
    for token in world.google.tokens:
        assert token not in page


def test_a_refusal_after_google_issued_a_token_ends_that_permission(world):
    ada = world.person(ADA)
    world.google.grant = "openid email"
    page = said(ada, connect(world, ada))
    assert gc.LEFT_REFUSED["revoked"] in page and held(world.db) == []
    assert len(world.google.revoked) == 1 and world.google.tokens == {}


def test_a_refusal_keeps_the_permission_another_connection_still_holds(world):
    ada = world.person(ADA)
    assert "Connected." in said(ada, connect(world, ada, "google_drive"))
    world.google.grant = "openid email"
    page = said(ada, connect(world, ada, "gmail"))
    assert "Google did not grant reading your mail." in page
    assert gc.LEFT_REFUSED["kept"] in page and "Nothing was connected" not in page
    assert world.google.revoked == [] and len(world.google.tokens) == 2


def test_consenting_as_another_account_on_reconnect_ends_that_accounts_new_permission(world):
    ada = world.person(ADA)
    connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    world.google.email = "someone.else@gmail.example.test"
    before = dump(world.db)
    page = said(ada, connect(world, ada, "gmail", account=name))
    assert "You consented as another Google account" in page
    assert gc.LEFT_REFUSED["revoked"] in page and dump(world.db) == before
    assert len(world.google.revoked) == 1 and len(world.google.tokens) == 1  # Ada's stays


def test_an_answer_that_names_no_account_leaves_the_permission_and_says_where_to_end_it(world):
    ada = world.person(ADA)
    world.google.claims = {"email_verified": False}
    page = said(ada, connect(world, ada))
    assert gc.LEFT_REFUSED["unknown"] in page and "third-party apps" in page
    assert world.google.revoked == []  # whose permission it is, nothing here can tell


def test_a_refusal_after_google_answered_never_says_no_permission_is_left(world):
    ada = world.person(ADA)
    world.google.refresh = False  # Google answered, and gave no token to end it with
    world.google.grant = "openid email"
    page = said(ada, connect(world, ada))
    assert "Google did not grant reading your mail." in page
    assert "Nothing was connected" not in page and gc.END_IT_YOURSELF in page


def test_a_full_workspace_is_refused_before_google_is_asked(world, monkeypatch):
    ada = world.person(ADA)
    assert "Connected." in said(ada, connect(world, ada, "google_drive"))
    monkeypatch.setattr(cairn.store.hosted, "CREDENTIALS_MAX", 1)
    response = start(ada, "gmail")
    assert told(response)["said"] == "full"
    assert "google_connect=" not in response.headers.get("set-cookie", "")
    page = html.unescape(ada.get(response.headers["location"]).text)
    (alert,) = re.findall(r'<p[^>]*role="alert"[^>]*>(.*?)</p>', page, re.S)
    refusal = re.sub(r"<[^>]+>", "", alert)
    assert cairn.store.hosted.FULL in refusal.lower() and "100" not in refusal  # no other counts
    assert gc.END_IT_YOURSELF not in page  # Google was never asked: nothing to end


def test_a_person_connects_at_most_eight_and_is_told_of_their_own(world, monkeypatch):
    assert cairn.store.hosted.CONNECTED_PER_PERSON == 8
    monkeypatch.setattr(cairn.store.hosted, "CONNECTED_PER_PERSON", 2)
    ada = world.person(ADA)
    bob = world.person(BOB)
    joins(world, bob, BOB, ADA, "member")
    world.google.email = BOB_GOOGLE
    for kind in ("gmail", "google_drive"):
        assert "Connected." in said(bob, connect(world, bob, kind))
    response = start(bob, "google_calendar")
    assert told(response)["said"] == "mine_full"
    page = html.unescape(bob.get(response.headers["location"]).text)
    assert (
        cairn.store.hosted.MINE_FULL.lower() in page.lower()
        and "workspace" not in cairn.store.hosted.MINE_FULL
    )
    world.google.email = ADA_GOOGLE
    assert "Connected." in said(ada, connect(world, ada, "google_calendar"))  # others still can


def test_a_workspace_filled_meanwhile_is_a_refusal_not_an_error(world, monkeypatch):
    ada = world.person(ADA)
    started = start(ada, "gmail")
    query = world.google.consent(started.headers["location"])
    monkeypatch.setattr(cairn.store.hosted, "CREDENTIALS_MAX", 0)
    response = come_back(world, ada, query)
    assert response.status_code == 303
    assert told(response)["said"] == "full"
    assert held(world.db) == [] and len(world.google.revoked) == 1


@pytest.mark.parametrize(
    "refused, code",
    [
        (ValueError(cairn.store.hosted.NAME_TAKEN), "taken"),
        (ValueError("anything else"), "refused_here"),
        (PermissionError("anything else"), "forbidden"),
        (sqlite3.OperationalError("disk I/O error"), "refused_here"),
    ],
)
def test_any_other_refusal_of_the_store_is_a_refusal_not_an_error(
    world, monkeypatch, refused, code
):
    ada = world.person(ADA)

    def refuse(self, *args, **kwargs):
        raise refused

    monkeypatch.setattr(cairn.store.Store, "connect_google", refuse)
    response = connect(world, ada)
    assert response.status_code == 303
    assert told(response)["said"] == code and told(response)["left"] == "revoked"
    assert len(world.google.revoked) == 1
    page = html.unescape(ada.get(response.headers["location"]).text)
    assert gc.GIVEN_UP[code] in page and gc.LEFT_REFUSED["revoked"] in page


@pytest.mark.parametrize(
    "error, words",
    [
        ("access_denied", "You did not allow it"),
        ("admin_policy_enforced", "administrator's policy"),
    ],
)
def test_a_refusal_in_the_redirect_sends_nothing_and_stores_nothing(world, error, words):
    ada = world.person(ADA)
    started = start(ada)
    state = world.google.consent(started.headers["location"])["state"]
    before = dump(world.db)
    page = said(ada, come_back(world, ada, {"error": error, "state": state}))
    assert words in page and dump(world.db) == before and world.google.exchanges == 0


def owned(world: World, email: str) -> tuple[str, str]:
    """The workspace `email` owns, and their id."""
    ((account_id, user_id),) = rows(
        world.db,
        "SELECT m.account_id, u.id FROM memberships m JOIN users u ON u.id = m.user_id"
        " WHERE u.email = ? AND m.role = 'owner'",
        email,
    )
    return account_id, user_id


def joins(world: World, client: TestClient, email: str, inviter: str, role: str) -> None:
    """`email` (signed in on `client`) takes `inviter`'s invitation, as `role`."""
    account_id, user_id = owned(world, inviter)
    invite = world.store.for_account(account_id, user_id).invite(email, role)
    assert client.post(f"/join/{invite.token}", follow_redirects=False).status_code == 303


def test_a_request_is_bound_to_its_person_in_the_same_workspace(world):
    ada = world.person(ADA)
    bob = world.person(BOB)
    joins(world, bob, BOB, ADA, "admin")
    started = start(ada)
    query = world.google.consent(started.headers["location"])
    sealed = started.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    bob.cookies.set(
        "google_connect", sealed, domain="cairn.example.test", path="/integrations/google"
    )
    before = dump(world.db)
    response = come_back(world, bob, query)
    assert told(response)["said"] == "stale"
    assert dump(world.db) == before and world.google.exchanges == 0


def test_a_listed_member_connects_their_own_and_sees_nothing_of_anothers(world):
    ada = world.person(ADA)
    assert "Connected." in said(ada, connect(world, ada))
    (adas,) = credentials(world, "gmail")
    bob = world.person(BOB)
    joins(world, bob, BOB, ADA, "member")
    page = html.unescape(bob.get("/sources").text)
    assert page.count("Connect Google:") == 4
    for anothers in (ADA_GOOGLE, adas, "connected by another person", "Connected Google"):
        assert anothers not in page, anothers
    world.google.email = BOB_GOOGLE
    page = said(bob, connect(world, bob))
    assert "Connected." in page and BOB_GOOGLE in page
    for anothers in (ADA_GOOGLE, adas, "connected by another person"):
        assert anothers not in page, anothers
    (bobs,) = [name for name in credentials(world, "gmail") if name != adas]
    account_id, _ = owned(world, ADA)
    ((bob_id,),) = rows(world.db, "SELECT id FROM users WHERE email = ?", BOB)
    view = world.store.for_account(account_id, bob_id)
    view.add_source_from("bob-mail", bobs, {"label": "x"}, {}, None, new_project="bob")
    ((ada_id,),) = rows(world.db, "SELECT id FROM users WHERE email = ?", ADA)
    world.store.for_account(account_id, ada_id).create_project("mail-of-ada", everyone="write")
    with pytest.raises(PermissionError):  # into a project he does not manage (decision 145)
        view.add_source_from("bob-mail-2", bobs, {"label": "y"}, {}, "mail-of-ada")
    with pytest.raises((KeyError, PermissionError)):  # Ada's account is not his to use
        view.add_source_from("bob-mail-3", adas, {"label": "z"}, {}, None, new_project="b2")
    refused = disconnect(bob, adas)
    assert told(refused)["said"] == "unknown"
    assert adas in credentials(world, "gmail") and world.google.revoked == []
    assert gc.LEFT_SAID["revoked"] in said(ada, disconnect(ada, bobs))  # an owner can
    assert credentials(world, "gmail") == [adas]


def test_not_offered_to_an_address_that_is_not_listed(tmp_path):
    world = World(tmp_path, offered=(ADA,))
    try:
        bob = world.person(BOB)
        page = bob.get("/sources")
        assert page.status_code == 200 and "Connect Google" not in page.text
        before = dump(world.db)
        assert start(bob).status_code == 403
        assert dump(world.db) == before
        ada = world.person(ADA)
        assert "Connect Google: Gmail" in ada.get("/sources").text
    finally:
        world.close()


def test_switched_off_there_is_no_route_no_button_and_no_table_read(tmp_path):
    off = World(tmp_path / "off", switch=False)
    on = World(tmp_path / "on", offered=(BOB,))  # on, but not for Ada
    try:
        statements = {}
        for name, world in (("off", off), ("on", on)):
            client = world.person(ADA)
            seen: list[str] = []
            world.store._observe(trace=seen.append)
            page = client.get("/sources")
            world.store._observe(trace=None)
            assert page.status_code == 200 and "Connect Google" not in page.text
            assert 'id="google-connect"' not in page.text
            if world is off:  # on, the table is read: does Ada hold a connected account?
                assert not any("credential_tokens" in s for s in seen)
            statements[name] = page.text
        assert statements["off"] == statements["on"]  # the same page, switched on or off
        client = off.clients[0]
        for path in ("/integrations/google/back", "/integrations/google/finish"):
            assert client.get(path, follow_redirects=False).status_code == 404
        for path in ("/integrations/google/connect", "/integrations/google/disconnect"):
            assert client.post(path, data={"kind": "gmail"}).status_code in (404, 405)
        assert off.store.google_custody is None
    finally:
        off.close()
        on.close()


# A run of a source made from a connected account


def connected_source(world: World, client: TestClient, name: str = "ada-mail"):
    """Ada connects Gmail and makes a source from it, switched on: her view, and the token."""
    assert "Connected." in said(client, connect(world, client))
    account_id, user_id = owned(world, ADA)
    view = world.store.for_account(account_id, user_id)
    ((credential, *_),) = rows(world.db, "SELECT credential FROM credential_tokens")
    view.add_source_from(name, credential, {"label": "x"}, {}, None, new_project="mail")
    view.enable_source(name)
    (token,) = world.google.tokens
    return view, token


class Spy:
    """The Gmail kind, opened as usual, but whose pull reads nothing: what `open` was given."""

    def __init__(self, monkeypatch, cursor=None) -> None:
        self.opened: list[tuple[dict, dict]] = []
        real = KINDS["gmail"]

        def opened(name, config, *, env, **options):
            self.opened.append((dict(config), dict(env)))
            credential = load_credential(config["credentials_env"], env)
            assert credential.variable == config["credentials_env"]
            return Quiet(name, cursor(credential) if cursor else None)

        monkeypatch.setitem(KINDS, "gmail", dataclasses.replace(real, open=opened))


class Quiet:
    def __init__(self, name: str, cursor: str | None = None) -> None:
        self.name = name
        self.cursor = cursor

    async def pull(self, cursor):
        return Pulled(cursor=self.cursor)


def run(world: World, view, name: str = "ada-mail", env=None):
    async def go():
        async with source_client(httpx.MockTransport(world.google.handler)) as http:
            return await run_source(view, name, http=http, env=env or {})

    return asyncio.run(go())


def test_a_run_hands_the_kind_the_document_under_a_name_no_variable_can_have(world, monkeypatch):
    ada = world.person(ADA)
    view, token = connected_source(world, ada)
    spy = Spy(monkeypatch)
    report = run(world, view, env={"CAIRN_CONNECTED_GOOGLE_ACCOUNT": "not this"})
    assert report.saved == 0 and not report.off and not report.busy
    ((config, env),) = spy.opened
    variable = config["credentials_env"]
    assert "=" in variable  # no environment variable can be named so
    assert "CAIRN_CONNECTED_GOOGLE_ACCOUNT" in env  # the process's own variables are passed on
    document = json.loads(env[variable])
    assert document == {
        "type": "authorized_user",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": token,
    }
    assert view.get_source("ada-mail").last_error is None
    stored = dump(world.db)
    assert not holds_secret(stored, (token, CLIENT_SECRET))


def test_the_root_store_of_the_background_sync_reaches_the_token(world, monkeypatch):
    ada = world.person(ADA)
    connected_source(world, ada)
    spy = Spy(monkeypatch)

    async def go():
        async with source_client(httpx.MockTransport(world.google.handler)) as http:
            return await sync_all(world.store, http=http, env={})

    outcomes = [o for o in asyncio.run(go()) if o.source == "ada-mail"]
    assert [o.error for o in outcomes] == [None] and len(spy.opened) == 1


def test_a_revoked_token_stops_the_run_with_reconnect_and_writes_nothing(world, caplog):
    ada = world.person(ADA)
    view, token = connected_source(world, ada)
    world.google.tokens.clear()  # the person took the access away at Google
    notes = rows(world.db, "SELECT count(*) FROM notes")
    with pytest.raises(SourceError) as stopped:
        run(world, view)
    assert str(stopped.value) == RECONNECT and "reconnect your Google account" in RECONNECT
    assert view.get_source("ada-mail").last_error == RECONNECT
    assert rows(world.db, "SELECT count(*) FROM notes") == notes
    assert rows(world.db, "SELECT cursor FROM sources WHERE name = 'ada-mail'") == [(None,)]
    assert not holds_secret(dump(world.db) + caplog.text, (token, CLIENT_SECRET))


def test_another_refusal_names_the_account_in_words_not_the_variable(world):
    ada = world.person(ADA)
    view, _ = connected_source(world, ada)
    world.google.refresh_error = "invalid_client"
    with pytest.raises(SourceError) as stopped:
        run(world, view)
    said_ = str(stopped.value)
    assert said_.startswith("Google refused the token request for the connected Google account:")
    assert "=" not in said_ and view.get_source("ada-mail").last_error == said_


@pytest.mark.parametrize("held", ["refresh_token", "client_secret"])
def test_what_the_document_holds_is_a_secret_of_the_run(world, monkeypatch, held):
    ada = world.person(ADA)
    view, _ = connected_source(world, ada)
    Spy(monkeypatch, cursor=lambda credential: f"after-{getattr(credential, held)}")
    with pytest.raises(SourceError, match="cursor held its credentials"):
        run(world, view)
    assert rows(world.db, "SELECT cursor FROM sources WHERE name = 'ada-mail'") == [(None,)]


def test_a_revoked_token_is_said_as_reconnect_in_a_preview(world):
    ada = world.person(ADA)
    _view, token = connected_source(world, ada)
    world.google.tokens.clear()
    before = dump(world.db)
    page = ada.post("/integrations/ada-mail/preview", data={"days": "7"})
    text = html.unescape(page.text)
    assert "reconnect your Google account" in text
    assert "=" + "the" not in text and token not in text and CLIENT_SECRET not in text
    assert dump(world.db) == before


def test_a_token_that_does_not_open_stops_the_run_before_google_is_asked(world):
    ada = world.person(ADA)
    view, _ = connected_source(world, ada)
    other = read_switch(
        {**SWITCH_ENV, "CAIRN_GOOGLE_CONNECT_KEY": base64.b64encode(bytes(32)).decode()}
    )
    world.store.google_custody = other.custody
    view = world.store.for_account(view.account_id, view.user_id)
    asked = len(world.google.seen)
    with pytest.raises(SourceError) as stopped:
        run(world, view)
    assert str(stopped.value) == UNOPENED and len(world.google.seen) == asked


def test_a_token_that_does_not_open_is_disconnected_without_asking_google(world):
    ada = world.person(ADA)
    connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    other = read_switch(
        {**SWITCH_ENV, "CAIRN_GOOGLE_CONNECT_KEY": base64.b64encode(bytes(32)).decode()}
    )
    world.store.google_custody = other.custody
    asked = len(world.google.seen)
    page = said(ada, disconnect(ada, name))
    assert gc.LEFT_SAID["unopened"] in page and world.google.seen[asked:] == []
    assert gc.LEFT_SAID["unreachable"] not in page and held(world.db) == []


def test_without_the_switch_a_connected_source_says_so_and_reads_nothing(world):
    ada = world.person(ADA)
    view, _ = connected_source(world, ada)
    world.store.google_custody = None
    view = world.store.for_account(view.account_id, view.user_id)
    asked = len(world.google.seen)
    with pytest.raises(SourceError, match="--google-connect"):
        run(world, view)
    assert len(world.google.seen) == asked


# Reconnect and disconnect


def preview(world: World, view, name: str = "ada-mail", credential: str | None = None):
    """The stored source's preview, or, with `credential`, an unsaved one made from it."""

    async def go():
        async with source_client(httpx.MockTransport(world.google.handler)) as http:
            if credential is None:
                return await preview_source(view, name, http=http, env={})
            return await preview_new_source(
                view, name, credential, {"label": "y"}, {}, None, new_project="mail-preview",
                http=http, env={},
            )  # fmt: skip

    return asyncio.run(go())


def test_both_previews_hand_the_kind_the_document_a_run_does(world, monkeypatch):
    ada = world.person(ADA)
    view, token = connected_source(world, ada)
    (credential,) = credentials(world, "gmail")
    spy = Spy(monkeypatch)
    run(world, view)
    stored = preview(world, view)
    unsaved = preview(world, view, "ada-mail-unsaved", credential)
    assert stored.read and unsaved.read, (stored, unsaved)
    (ran, *previews) = spy.opened
    assert len(previews) == 2
    for config, env in previews:
        assert config["credentials_env"] == ran[0]["credentials_env"] and env == ran[1]
    assert view.get_source("ada-mail-unsaved") is None  # nothing stored
    assert not holds_secret(dump(world.db), (token, CLIENT_SECRET))


class Leaky:
    """A pull that says the credential it was opened with, in an error or in a title."""

    def __init__(self, name: str, secret: str, where: str) -> None:
        self.name, self.secret, self.where = name, secret, where

    async def pull(self, cursor):
        if self.where == "error":
            raise SourceError(f"the service said no to {self.secret}")
        draft = NoteDraft(project="", title=f"about {self.secret}", text="t",
                          source_ref="https://mail.example.test/1", label="x")  # fmt: skip
        return Pulled(drafts=[draft])


@pytest.mark.parametrize("where", ["error", "title"])
@pytest.mark.parametrize("part", ["refresh_token", "client_secret"])
def test_a_preview_of_a_connected_account_leaks_nothing_a_run_does_not(
    world, monkeypatch, where, part
):
    ada = world.person(ADA)
    view, token = connected_source(world, ada)
    (credential,) = credentials(world, "gmail")
    real = KINDS["gmail"]

    def opened(name, config, *, env, **options):
        document = json.loads(env[config["credentials_env"]])
        return Leaky(name, document[part], where)

    monkeypatch.setitem(KINDS, "gmail", dataclasses.replace(real, open=opened))
    secrets = (token, CLIENT_SECRET)
    for shown in (preview(world, view), preview(world, view, "ada-mail-unsaved", credential)):
        assert not holds_secret(repr(shown), secrets), shown
        if where == "error":
            assert shown.read is False and shown.said == WITHHELD
        else:
            assert shown.titles == [HELD_TITLE]
    try:  # what a run keeps of the same pull, for comparison: the error stops it
        run(world, view)
    except SourceError:
        assert where == "error"
    assert not holds_secret(dump(world.db), secrets)
    assert view.get_source("ada-mail").last_error in (None, WITHHELD)


def source_row(world: World, name: str = "ada-mail") -> tuple:
    return rows(world.db, "SELECT name, kind, config, enabled FROM sources WHERE name = ?", name)[0]


def test_reconnecting_keeps_the_name_the_sources_and_where_they_write(world):
    ada = world.person(ADA)
    view, old = connected_source(world, ada)
    ((name, *_),) = rows(world.db, "SELECT credential FROM credential_tokens")
    before = source_row(world)
    page = said(ada, connect(world, ada, "gmail", account=name))
    assert "Reconnected" in page and 'role="alert"' not in page
    assert source_row(world) == before
    ((again, *_),) = rows(world.db, "SELECT credential FROM credential_tokens")
    assert again == name
    new = view.google_token(name)
    assert new != old and new in world.google.tokens
    assert not holds_secret(dump(world.db), (old, new))


def test_reconnecting_as_another_google_account_is_refused_and_changes_nothing(world):
    ada = world.person(ADA)
    connected_source(world, ada)
    ((name, *_),) = rows(world.db, "SELECT credential FROM credential_tokens")
    world.google.email = "someone.else@gmail.example.test"
    before = dump(world.db)
    page = said(ada, connect(world, ada, "gmail", account=name))
    assert "You consented as another Google account" in page and 'role="alert"' in page
    assert dump(world.db) == before


def test_reconnecting_an_account_that_is_not_yours_is_refused_before_google(world):
    ada = world.person(ADA)
    connected_source(world, ada)
    before = dump(world.db)
    response = start(ada, "gmail", account="gmail.someone.0123456789")
    assert told(response)["said"] == "unknown"
    assert "google_connect=" not in response.headers.get("set-cookie", "")
    assert dump(world.db) == before


def disconnect(client: TestClient, name: str, confirm: str | None = None) -> httpx.Response:
    data = {"account": name, "confirm": name if confirm is None else confirm}
    return client.post("/integrations/google/disconnect", data=data, follow_redirects=False)


def credentials(world: World, kind: str) -> list[str]:
    found = rows(world.db, "SELECT credential, kind FROM credential_tokens ORDER BY credential")
    return [name for name, held_kind in found if held_kind == kind]


def test_disconnecting_the_last_connection_ends_googles_permission_and_switches_off(world):
    ada = world.person(ADA)
    view, token = connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    asked = len(world.google.seen)
    page = said(ada, disconnect(ada, name))
    assert gc.SAID["disconnected"] in page and gc.LEFT_SAID["revoked"] in page
    (revocation,) = world.google.seen[asked:]
    assert revocation.method == "POST" and str(revocation.url.copy_with(query=None)) == REVOKE_URL
    assert world.google.revoked == [token]
    assert held(world.db) == [] and view.list_credentials() == []
    assert source_row(world)[3] == 0  # the source stays, switched off
    assert token not in page


def test_disconnecting_asks_for_the_name_typed_as_a_check(world):
    ada = world.person(ADA)
    connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    before, asked = dump(world.db), len(world.google.seen)
    response = disconnect(ada, name, confirm="something else")
    assert told(response)["said"] == "confirm"
    assert dump(world.db) == before and len(world.google.seen) == asked


def test_while_another_connection_holds_the_address_the_permission_stays(world):
    ada = world.person(ADA)
    connected_source(world, ada)
    assert "Connected." in said(ada, connect(world, ada, "google_drive"))
    (mail,) = credentials(world, "gmail")
    asked = len(world.google.seen)
    page = said(ada, disconnect(ada, mail))
    assert gc.LEFT_SAID["kept"] in page and gc.LEFT_SAID["revoked"] not in page
    assert world.google.seen[asked:] == [] and len(world.google.tokens) == 2
    (drive,) = credentials(world, "google_drive")
    page = said(ada, disconnect(ada, drive))
    assert gc.LEFT_SAID["revoked"] in page and world.google.tokens == {}


def test_the_same_google_account_in_another_workspace_keeps_the_permission(world):
    ada = world.person(ADA)
    connected_source(world, ada)
    bob = world.person(BOB)  # his own workspace; he consents as the same Google account
    assert "Connected." in said(bob, connect(world, bob))
    assert len(credentials(world, "gmail")) == 2
    (mine,) = world.store.for_account(*owned(world, ADA)).google_accounts()
    mine = mine.name
    asked = len(world.google.seen)
    page = said(ada, disconnect(ada, mine))
    assert gc.LEFT_SAID["kept"] in page and world.google.seen[asked:] == []


def test_a_400_from_revocation_settles_and_says_the_token_had_ended(world):
    ada = world.person(ADA)
    connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    world.google.tokens.clear()  # ended already, say by a crash between revoking and deleting
    page = said(ada, disconnect(ada, name))
    assert gc.LEFT_SAID["gone"] in page and gc.LEFT_SAID["revoked"] not in page
    assert held(world.db) == [] and waiting(world) == []


def test_disconnecting_with_google_unreachable_deletes_anyway_and_says_where_to_end_it(world):
    ada = world.person(ADA)
    _view, token = connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    world.google.revoke_down = True
    page = said(ada, disconnect(ada, name))
    assert gc.LEFT_SAID["unreachable"] in page and "third-party apps" in page
    assert held(world.db) == [] and source_row(world)[3] == 0
    assert token not in page and world.google.revoked == []


def waiting(world: World) -> list[tuple]:
    return rows(world.db, "SELECT id, address FROM google_revocations")


@pytest.mark.parametrize(
    "status, left", [(503, "unreachable"), (500, "unreachable"), (429, "unreachable"),
                     (401, "refused")],
)  # fmt: skip
def test_only_200_or_400_settles_a_revocation(world, status, left):
    ada = world.person(ADA)
    connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    world.google.revoke_status = status
    page = said(ada, disconnect(ada, name))
    assert gc.LEFT_SAID[left] in page and held(world.db) == []
    assert len(waiting(world)) == 1  # it waits, and is tried again
    assert asyncio.run(gc.revoke(lambda: source_client(httpx.MockTransport(world.google.handler)),
                                 "1//x")) == left  # fmt: skip


def test_a_revocation_not_sent_waits_sealed_and_is_sent_after_a_restart(world, caplog):
    ada, _bob, bob_id, _name, token = bob_connected_in_adas_workspace(world)
    world.google.revoke_down = True
    assert ada.post(f"/members/{bob_id}/remove", follow_redirects=False).status_code == 303
    assert [address for _, address in waiting(world)] == [BOB_GOOGLE]
    assert not holds_secret(dump(world.db), (token,))
    world.google.revoke_down = False
    world.restart()  # a new process: nothing in memory, the row in the database
    with TestClient(world.app, base_url=BASE) as again:  # its start-up sends it
        again.get("/healthz")
    assert world.google.revoked == [token] and waiting(world) == []


def test_the_loop_passes_every_minute_never_inside_a_request(world):
    ada = world.person(ADA)
    _view, token = connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    world.google.revoke_down = True
    assert gc.LEFT_SAID["unreachable"] in said(ada, disconnect(ada, name))
    world.google.revoke_down = False
    ada.get("/sources")  # a request sends nothing
    assert world.google.revoked == [] and len(waiting(world)) == 1
    world.google.revoke_down = True  # the loop's first pass is not answered either
    waited: list[float] = []
    make = lambda: source_client(httpx.MockTransport(world.google.handler))  # noqa: E731
    revoker = gc.Revoker(world.store, world.store.google_custody, make)

    async def wait(seconds):
        waited.append(seconds)
        world.google.revoke_down = False
        if len(waited) == 2:
            revoker.stop()

    revoker.wait = wait
    asyncio.run(revoker.loop())
    assert waited == [gc.RETRY_AFTER, gc.RETRY_AFTER]
    assert world.google.revoked == [token] and waiting(world) == []
    assert world.store.google_custody.waiting.wake is None  # the loop is gone


def test_a_failure_outside_the_pass_is_logged_and_the_loop_goes_on(world, monkeypatch, caplog):
    passes: list[int] = []

    async def failing(make_http, store, custody):
        passes.append(1)
        if len(passes) <= 2:
            raise RuntimeError("the address ada@example.test")  # never in the log
        revoker.stop()
        return False

    async def wait(seconds):
        waited.append(seconds)

    waited: list[float] = []
    monkeypatch.setattr(gc, "drain", failing)
    revoker = gc.Revoker(world.store, world.store.google_custody, lambda: None, wait=wait)
    with caplog.at_level(logging.WARNING):
        asyncio.run(revoker.loop())
    assert len(passes) == 3 and waited == [gc.RETRY_AFTER, gc.RETRY_AFTER]
    logged = [r.getMessage() for r in caplog.records if "google-connect" in r.getMessage()]
    assert logged == ["google-connect: the loop over the waiting revocations failed: RuntimeError"]
    assert "ada@" not in caplog.text


def test_a_drop_wakes_the_loop(world, monkeypatch):
    _ada, _bob, bob_id, _name, token = bob_connected_in_adas_workspace(world)
    custody = world.store.google_custody
    make = lambda: source_client(httpx.MockTransport(world.google.handler))  # noqa: E731

    ready, revoked = asyncio.Event(), asyncio.Event()
    drain = gc.drain

    async def never(seconds):
        ready.set()
        await asyncio.Event().wait()

    async def observed(*args):
        await drain(*args)
        if world.google.revoked and not waiting(world):
            revoked.set()

    monkeypatch.setattr(gc, "drain", observed)
    revoker = gc.Revoker(world.store, custody, make, wait=never)

    async def go():
        looping = asyncio.create_task(revoker.loop())
        try:
            await wait_for_event(ready, "revocation loop did not start waiting")
            view = world.store.for_account(*owned(world, ADA))
            await asyncio.to_thread(view.remove_member, bob_id)
            await wait_for_event(revoked, "dropping the member did not wake revocation")
        finally:
            await revoker.stopped(looping)

    asyncio.run(go())
    assert world.google.revoked == [token]


def test_a_waiting_revocation_keeps_waiting_when_the_address_is_connected_again(world, monkeypatch):
    ada = world.person(ADA)
    connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    world.google.revoke_down = True
    said(ada, disconnect(ada, name))
    world.google.revoke_down = False
    world.google.email = "ada.renamed@example.test"  # the same Google account, renamed
    world.google.claims = {"sub": f"sub-{ADA_GOOGLE}"}
    assert "Connected." in said(ada, connect(world, ada, "google_drive"))
    send_waiting(world)
    assert len(waiting(world)) == 1 and world.google.revoked == []  # kept: it would end both


def send_waiting(world: World) -> None:
    """One pass over the revocations waiting, as the server's loop makes it."""
    custody = world.store.google_custody
    make = lambda: source_client(httpx.MockTransport(world.google.handler))  # noqa: E731
    asyncio.run(gc.drain(make, world.store, custody))


def test_a_kept_permission_waits_until_no_connection_holds_the_address(world):
    ada = world.person(ADA)
    assert "Connected." in said(ada, connect(world, ada, "gmail"))
    assert "Connected." in said(ada, connect(world, ada, "google_drive"))  # the same address
    (mail,), (drive,) = credentials(world, "gmail"), credentials(world, "google_drive")
    assert gc.LEFT_SAID["kept"] in said(ada, disconnect(ada, mail))
    assert len(waiting(world)) == 1  # kept: it waits, the address checked again each pass
    send_waiting(world)
    assert len(waiting(world)) == 1 and world.google.revoked == []
    custody, world.store.google_custody = world.store.google_custody, None
    world.store.for_account(*owned(world, ADA)).remove_credential(drive)  # as the command line
    world.store.google_custody = custody
    send_waiting(world)
    assert waiting(world) == [] and len(world.google.revoked) == 1 and world.google.tokens == {}


def test_a_cancelled_pass_leaves_the_rows_to_the_next(world, monkeypatch):
    ada = world.person(ADA)
    _view, token = connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    world.google.revoke_down = True
    said(ada, disconnect(ada, name))
    world.google.revoke_down = False

    async def cancelled(*args):
        raise asyncio.CancelledError

    real = gc.revoke_unless_held
    monkeypatch.setattr(gc, "revoke_unless_held", cancelled)
    with pytest.raises(asyncio.CancelledError):
        send_waiting(world)
    assert not world.store.google_custody.waiting.draining and len(waiting(world)) == 1
    monkeypatch.setattr(gc, "revoke_unless_held", real)
    send_waiting(world)
    assert waiting(world) == [] and world.google.revoked == [token]


def test_a_refusal_is_logged_once_and_a_stopped_row_once_with_its_code(world, monkeypatch, caplog):
    monkeypatch.setattr(cairn.store.hosted, "REVOKE_TRIES", 3)
    ada = world.person(ADA)
    connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    world.google.revoke_status = 401  # the OAuth client was deleted, say
    caplog.set_level(logging.WARNING)
    assert gc.LEFT_SAID["refused"] in said(ada, disconnect(ada, name))  # try 1
    for _ in range(4):  # tries 2 and 3, then nothing: stopped
        con = sqlite3.connect(world.db)
        with con:
            con.execute("UPDATE google_revocations SET due_at = NULL")  # due now
        con.close()
        send_waiting(world)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("google-")]
    assert lines == [
        "google-connect: a dropped account's permission was not ended: refused",
        "google-connect: a dropped account's permission was given up after 3 refusals:"
        " revoke_stopped",
    ]
    assert len(waiting(world)) == 1  # kept, for the operator
    revokes = [s for s in world.google.seen if str(s.url).startswith(REVOKE_URL)]
    assert len(revokes) == 3
    guide = (Path(gc.__file__).parents[1] / "guides" / "security.md").read_text()
    assert "revoke_stopped" in guide


def test_every_waiting_token_of_an_account_is_sent_once_none_holds_it(world):
    ada = world.person(ADA)
    for kind in ("gmail", "google_drive"):
        assert "Connected." in said(ada, connect(world, ada, kind))
    (mail,), (drive,) = credentials(world, "gmail"), credentials(world, "google_drive")
    older, newer = list(world.google.tokens)
    assert gc.LEFT_SAID["kept"] in said(ada, disconnect(ada, mail))  # the drive holds it
    del world.google.tokens[newer]  # the newer token ended on its own; the older is live
    world.google.revoke_down = True
    said(ada, disconnect(ada, drive))
    assert len(waiting(world)) == 2  # both kept: either may be the one that still matters
    world.google.revoke_down = False
    send_waiting(world)
    assert world.google.revoked == [older] and world.google.tokens == {}
    assert waiting(world) == []


def test_the_waiting_table_has_a_hard_cap_said_with_its_own_code(world, monkeypatch, caplog):
    assert cairn.store.hosted.REVOCATIONS_MAX == 10_000
    monkeypatch.setattr(cairn.store.hosted, "REVOCATIONS_MAX", 1)
    ada = world.person(ADA)
    for kind in ("gmail", "google_drive", "google_calendar"):
        assert "Connected." in said(ada, connect(world, ada, kind))
    caplog.set_level(logging.WARNING)
    for kind in ("gmail", "google_drive"):
        (name,) = credentials(world, kind)
        said(ada, disconnect(ada, name))
    assert len(waiting(world)) == 1  # the one waiting stays; the newer one is not kept
    assert "revocations_full" in caplog.text and ADA_GOOGLE not in caplog.text


def test_a_waiting_token_that_no_longer_opens_is_settled_with_its_own_code(world, caplog):
    ada = world.person(ADA)
    connected_source(world, ada)
    (name,) = credentials(world, "gmail")
    world.google.revoke_down = True
    said(ada, disconnect(ada, name))
    con = sqlite3.connect(world.db)
    with con:
        con.execute("UPDATE google_revocations SET sealed = 'bm90IHNlYWxlZCBhdCBhbGwgaGVyZQ'")
    con.close()
    world.google.revoke_down = False
    caplog.set_level(logging.WARNING)
    send_waiting(world)
    assert waiting(world) == [] and world.google.revoked == []
    assert "unopened" in caplog.text


def test_a_400_never_marks_a_connection_made_meanwhile(world, monkeypatch):
    ada = world.person(ADA)
    view, token = connected_source(world, ada)
    (mail,) = credentials(world, "gmail")
    del world.google.tokens[token]  # the token ended on its own: Google answers 400
    raced = "1//g2-raced-0123456789abcdef"
    real = gc.revoke

    async def racing(make_http, sent):
        await asyncio.to_thread(
            view.connect_google, "google_drive", ADA_GOOGLE,
            [*gc.SCOPES["google_drive"], "openid", "email"], raced, subject=f"sub-{ADA_GOOGLE}",
        )  # fmt: skip
        world.google.tokens[raced] = ADA_GOOGLE
        return await real(make_http, sent)

    monkeypatch.setattr(gc, "revoke", racing)
    page = said(ada, disconnect(ada, mail))
    assert gc.LEFT_SAID["gone"] in page and gc.LEFT_SAID["revoked"] not in page
    assert waiting(world) == []  # a 400 settles the row
    (drive,) = credentials(world, "google_drive")
    assert view.google_token(drive) == raced  # healthy, not marked to reconnect


def test_the_loop_never_sends_a_row_the_disconnect_is_sending(world, monkeypatch):
    ada = world.person(ADA)
    _view, token = connected_source(world, ada)
    (mail,) = credentials(world, "gmail")
    real = gc.revoke
    sends: list[str] = []

    async def meanwhile(make_http, sent):  # a pass of the loop runs while the handler sends
        sends.append(sent)
        await asyncio.to_thread(send_waiting, world)
        return await real(make_http, sent)

    monkeypatch.setattr(gc, "revoke", meanwhile)
    assert gc.LEFT_SAID["revoked"] in said(ada, disconnect(ada, mail))
    assert sends == [token] and world.google.revoked == [token] and waiting(world) == []


def test_a_connection_made_while_its_address_was_revoked_says_reconnect(world, monkeypatch):
    ada = world.person(ADA)
    view, _token = connected_source(world, ada)
    (mail,) = credentials(world, "gmail")
    raced = "1//g2-raced-0123456789abcdef"
    real = gc.revoke

    async def racing(make_http, token):  # the same address connects while Google ends it
        await asyncio.to_thread(
            view.connect_google, "google_drive", ADA_GOOGLE,
            [*gc.SCOPES["google_drive"], "openid", "email"], raced, subject=f"sub-{ADA_GOOGLE}",
        )  # fmt: skip
        world.google.tokens[raced] = ADA_GOOGLE
        return await real(make_http, token)

    monkeypatch.setattr(gc, "revoke", racing)
    said(ada, disconnect(ada, mail))
    monkeypatch.setattr(gc, "revoke", real)
    assert raced not in world.google.tokens  # Google ended it with the other
    (drive,) = credentials(world, "google_drive")
    with pytest.raises(cairn.store.hosted.TokenUnusable) as told:
        view.google_token(drive)
    assert str(told.value) == RECONNECT
    view.add_source_from(
        "ada-drive", drive, {"folder_id": "folder-0123456789"}, {}, None, new_project="drive"
    )
    view.enable_source("ada-drive")
    with pytest.raises(SourceError) as stopped:
        run(world, view, "ada-drive")
    assert str(stopped.value) == RECONNECT
    page = html.unescape(ada.get("/sources").text)
    assert RECONNECT in page
    assert "Reconnected" in said(ada, connect(world, ada, "google_drive", account=drive))
    page = html.unescape(ada.get("/sources").text)
    block = page[page.index('id="google-connect"') :]
    assert (
        RECONNECT not in block[: block.index("</section>")]
    )  # the source's last run still says it


def test_disconnecting_needs_the_account_to_be_yours(world):
    ada = world.person(ADA)
    connected_source(world, ada)
    bob = world.person(BOB)
    joins(world, bob, BOB, ADA, "admin")
    (name,) = credentials(world, "gmail")
    before, asked = dump(world.db), len(world.google.seen)
    response = disconnect(bob, name)
    assert told(response)["said"] == "unknown"
    assert dump(world.db) == before and len(world.google.seen) == asked


BOB_GOOGLE = "bob.babbage@gmail.example.test"


def bob_connected_in_adas_workspace(world: World, role: str = "admin"):
    """Bob joins Ada's workspace and connects his own Google account there, with a source on."""
    ada = world.person(ADA)
    bob = world.person(BOB)
    joins(world, bob, BOB, ADA, role)
    world.google.email = BOB_GOOGLE
    assert "Connected." in said(bob, connect(world, bob))
    world.google.email = ADA_GOOGLE
    account_id, _ = owned(world, ADA)
    ((bob_id,),) = rows(world.db, "SELECT id FROM users WHERE email = ?", BOB)
    view = world.store.for_account(account_id, bob_id)
    (name,) = credentials(world, "gmail")
    view.add_source_from("bob-mail", name, {"label": "x"}, {}, None, new_project="bob")
    view.enable_source("bob-mail")
    (token,) = world.google.tokens
    return ada, bob, bob_id, name, token


def test_removing_a_person_drops_their_accounts_and_ends_googles_permission(world):
    ada, _bob, bob_id, name, token = bob_connected_in_adas_workspace(world)
    removed = ada.post(f"/members/{bob_id}/remove", follow_redirects=False)
    assert removed.status_code == 303
    assert held(world.db) == []
    account_id, _ = owned(world, ADA)
    settings = rows(world.db, "SELECT name FROM settings WHERE account_id = ?", account_id)
    assert not any(name.casefold() in row[0] for row in settings if row[0].startswith("credential"))
    assert source_row(world, "bob-mail")[3] == 0  # switched off: its account was disconnected
    send_waiting(world)  # the server's loop, woken by the removal
    assert world.google.revoked == [token]  # once the removal was written


def test_removing_a_person_keeps_the_permission_another_row_still_holds(world):
    ada, bob, bob_id, _name, token = bob_connected_in_adas_workspace(world)
    world.google.email = BOB_GOOGLE  # Bob's own workspace holds the same Google account
    bob.post(f"/workspaces/{owned(world, BOB)[0]}/switch", follow_redirects=False)
    assert "Connected." in said(bob, connect(world, bob))
    ada.post(f"/members/{bob_id}/remove", follow_redirects=False)
    assert world.google.revoked == [] and token in world.google.tokens
    assert len(held(world.db)) == 1


def test_a_failed_revocation_is_logged_by_a_code_and_the_removal_stands(world, caplog):
    ada, _bob, bob_id, _name, token = bob_connected_in_adas_workspace(world)
    world.google.revoke_down = True
    caplog.set_level(logging.WARNING)
    assert ada.post(f"/members/{bob_id}/remove", follow_redirects=False).status_code == 303
    assert held(world.db) == []
    send_waiting(world)
    (line,) = [r for r in caplog.records if "google" in r.getMessage().lower()]
    assert "unreachable" in line.getMessage()
    assert not holds_secret(caplog.text.replace("httpx2", ""), (token, BOB_GOOGLE))


def test_deleting_a_workspace_ends_googles_permission_for_its_accounts(world):
    ada = world.person(ADA)
    _view, token = connected_source(world, ada)
    account_id, _ = owned(world, ADA)
    ((workspace,),) = rows(world.db, "SELECT name FROM accounts WHERE id = ?", account_id)
    gone = ada.post("/account/delete", data={"confirm": workspace}, follow_redirects=False)
    assert gone.status_code == 303 and held(world.db) == []
    send_waiting(world)  # the server's loop, woken by the deletion
    assert world.google.revoked == [token]


def test_an_owner_disconnects_any_connected_account_of_the_workspace(world):
    ada, _bob, _bob_id, name, token = bob_connected_in_adas_workspace(world)
    page = html.unescape(ada.get("/sources").text)
    assert BOB_GOOGLE in page and f'value="{name}"' in page  # listed, with its Disconnect
    assert gc.LEFT_SAID["revoked"] in said(ada, disconnect(ada, name))
    assert held(world.db) == [] and world.google.revoked == [token]


def test_an_admin_does_not_disconnect_another_persons_account(world):
    ada, bob, _bob_id, _name, _token = bob_connected_in_adas_workspace(world)
    assert "Connected." in said(ada, connect(world, ada))
    mine = [a.name for a in world.store.for_account(*owned(world, ADA)).google_accounts()]
    before = dump(world.db)
    response = disconnect(bob, mine[0])
    assert told(response)["said"] == "unknown"
    assert dump(world.db) == before


def test_whoever_holds_an_account_sees_it_and_its_disconnect_but_connects_only_if_listed(
    tmp_path,
):
    world = World(tmp_path, offered=(BOB,))  # Bob connects; then the list no longer names him
    try:
        _ada, bob, _bob_id, name, _token = bob_connected_in_adas_workspace(world)
        # the server started again, its list no longer naming Bob
        unlisted = create_app(
            world.store, public_url=BASE, sign_in=SignIn(
                providers=(GitHub("gh-id", "gh-secret"),),
                http=httpx.AsyncClient(transport=httpx.MockTransport(world.github)),
                join="personal",
            ),
            source_http=lambda: source_client(httpx.MockTransport(world.google.handler)),
            google_connect=read_switch(SWITCH_ENV, ()),
        )  # fmt: skip
        world.app = unlisted
        bob = world.person(BOB)
        bob.post(f"/workspaces/{owned(world, ADA)[0]}/switch", follow_redirects=False)
        page = html.unescape(bob.get("/sources").text)
        assert BOB_GOOGLE in page and f'value="{name}"' in page and "Disconnect" in page
        assert "Connect Google" not in page and "Reconnect" not in page
        assert start(bob).status_code == 403
        assert gc.LEFT_SAID["revoked"] in said(bob, disconnect(bob, name))
    finally:
        world.close()


def test_a_member_who_holds_an_account_can_still_disconnect_it(world):
    _ada, bob, bob_id, name, token = bob_connected_in_adas_workspace(world)
    world.store.for_account(*owned(world, ADA)).set_role(bob_id, "member")
    assert start(bob).status_code == 303  # a listed member connects their own
    response = disconnect(bob, name)
    assert told(response)["said"] == "disconnected"
    assert held(world.db) == [] and world.google.revoked == [token]


def test_the_revoking_rule_is_one_function_with_two_branches(world):
    ada = world.person(ADA)
    connected_source(world, ada)
    (token,) = world.google.tokens
    other = "sub-someone.else@gmail.example.test"

    async def rule(subject):
        return await gc.revoke_unless_held(
            lambda: source_client(httpx.MockTransport(world.google.handler)),
            world.store, subject, token,
        )  # fmt: skip

    assert asyncio.run(rule(f"sub-{ADA_GOOGLE}")) == "kept"  # a row holds Google's id of it
    assert world.google.revoked == []
    assert asyncio.run(rule(other)) == "revoked"
    assert world.google.revoked == [token]


# The page and the guides


def test_the_block_says_what_connecting_does_has_no_script_and_lists_only_ones_own(world):
    ada = world.person(ADA)
    connected_source(world, ada)
    page = html.unescape(ada.get("/sources").text)
    block = page[page.index('id="google-connect"') :]
    block = block[: block.index("</section>")]
    assert "<script" not in block and " on" + "click" not in block
    assert gc.WHAT in block and gc.TESTING in block and gc.DISCONNECT_SAYS in block
    for service in ("Gmail", "Google Calendar", "Google Drive", "Google Meet"):
        assert f"Connect Google: {service}" in block
    assert ADA_GOOGLE in block and "Reconnect" in block and "Disconnect" in block
    bob = world.person(BOB)
    assert ADA_GOOGLE not in bob.get("/sources").text


def test_a_refusal_comes_first_in_the_block_in_role_alert(world):
    ada = world.person(ADA)
    started = start(ada, "gmail")
    query = world.google.consent(started.headers["location"])
    page = said(ada, come_back(world, ada, {"state": query["state"], "error": "access_denied"}))
    block = page[page.index('id="google-connect"') :]
    alert = block.index('role="alert"')
    assert alert < block.index(gc.WHAT)
    assert gc.REFUSALS["denied"] in block


def test_the_page_says_only_what_happened_once_and_to_its_person(world):
    ada = world.person(ADA)
    assert "Connected." in said(ada, connect(world, ada))
    (name,) = credentials(world, "gmail")
    # a crafted address says nothing: the outcome is not read from it
    for crafted in ("?google=disconnected&left=revoked", "?google=denied&kind=gmail"):
        page = html.unescape(ada.get(f"/sources{crafted}").text)
        assert gc.SAID["disconnected"] not in page and gc.REFUSALS["denied"] not in page
    response = disconnect(ada, name)
    sealed = response.cookies[gc.SAID_COOKIE]
    first = ada.get(response.headers["location"])
    assert gc.SAID["disconnected"] in html.unescape(first.text)
    assert first.headers["cache-control"] == "no-store"
    cleared = [v for v in first.headers.get_list("set-cookie") if v.startswith(gc.SAID_COOKIE)]
    assert len(cleared) == 1 and "max-age=0" in cleared[0].lower()
    assert gc.SAID["disconnected"] not in html.unescape(ada.get("/sources").text)  # once
    bob = world.person(BOB)  # another person's outcome, carried over, is not said
    joins(world, bob, BOB, ADA, "member")
    bob.cookies.set(gc.SAID_COOKIE, sealed, domain="cairn.example.test", path="/sources")
    assert gc.SAID["disconnected"] not in html.unescape(bob.get("/sources").text)


@pytest.mark.parametrize("which", ["as_written", "expired", "workspace", "garbage", "flow"])
def test_an_outcome_that_is_not_this_persons_now_is_not_said_and_is_cleared(world, which):
    ada = world.person(ADA)
    workspace, person = owned(world, ADA)
    outcome = {
        "said": "disconnected", "kind": None, "left": "revoked", "person": person,
        "workspace": "another" if which == "workspace" else workspace,
        "exp": time.time() + (-1 if which == "expired" else 60),
    }  # fmt: skip
    associated = gc.FLOW if which == "flow" else gc.OUTCOME  # the flow's seal does not open
    sealed = cairn.store.hosted.Sealer(KEY).seal(json.dumps(outcome), associated)
    if which == "garbage":
        sealed = "not-a-sealed-value"
    ada.cookies.set(gc.SAID_COOKIE, sealed, domain="cairn.example.test", path="/sources")
    page = ada.get("/sources")
    assert (gc.SAID["disconnected"] in html.unescape(page.text)) == (which == "as_written")
    cleared = [v for v in page.headers.get_list("set-cookie") if v.startswith(gc.SAID_COOKIE)]
    assert len(cleared) == 1 and "max-age=0" in cleared[0].lower()


def test_switched_off_the_include_adds_not_one_byte_to_the_page():
    from cairn.web.templating import templates

    assert templates.get_template("_google_connect.html").render() == ""
    around = templates.from_string('a\n{% include "_google_connect.html" %}\nb')
    assert around.render() == "a\nb"


def test_the_security_guide_says_what_changing_the_sealing_key_does_and_in_what_order():
    guide = (Path(gc.__file__).parents[1] / "guides" / "security.md").read_text()
    section = guide[guide.index("## Google accounts connected in the browser") :]
    section = section[: section.index("\n## ")]
    said = " ".join(section.split())
    assert "Changing `CAIRN_GOOGLE_CONNECT_KEY`" in said and "reconnect" in said
    first = said.index("disconnect every connected account first")
    assert first < said.index("then change the key")
    assert "revocations_full" in said  # the cap's own code, for the operator


def test_the_sources_guide_names_what_the_code_reads():
    guide = (Path(gc.__file__).parents[1] / "guides" / "sources.md").read_text()
    # The bound that is real is docs_publisher.MAX_FILE_BYTES (128 KiB): test_docs_helper shows a
    # sources guide past 70,000 bytes published and searched. This lower one only makes growth
    # a choice someone states; it was raised from 70,000 for the sentence on changing the key.
    assert len(guide.encode()) < 72_000
    section = guide[guide.index("### A Google account connected in the browser") :]
    for fact in (
        gc.CLIENT_VARIABLE,
        gc.KEY_VARIABLE,
        f"<public url>{gc.BACK_PATH}",
        "--google-connect-for ADDRESS",
        "**Web application**",
        "(#one-persons-own-google-authorisation)",  # how long a connection lasts, said once
        "Reconnect",
        "Disconnect",
        "a Google Cloud project of its own",
        "without checking its signature",  # what stays as it is
        "recorded as granted",
        "passes through the address",
        "Reconnecting does not revoke",
        "Before this is offered to anyone",  # three checks against the real Google
        "`iss`",
        "`email_verified`",
        "Safari, Firefox and Chrome",
        "still connected elsewhere on this server",  # the revoking rule
    ):
        assert fact in section, fact
    said = " ".join(section.split())
    lost = said.index("every account must be connected again.")
    change = said.index(
        "To change the key without anyone connecting again: put the new key in"
        f" `{gc.KEY_VARIABLE}` and the old one in `{gc.KEY_VARIABLE}_PREVIOUS`, restart the"
        " server, run the `reseal` command with `--google-connect`, then remove"
        f" `{gc.KEY_VARIABLE}_PREVIOUS` and restart."
    )
    assert lost < change
    assert gc.KEY_BYTES == 32 and "32 random bytes" in section
    assert "takes the access away at Google" not in guide  # it does so only by the rule


def test_disconnect_says_what_it_reaches_in_one_sentence():
    from cairn.store.sources import HOSTED_DISCONNECT

    said = gc.DISCONNECT_SAYS
    assert said.endswith(".") and said.count(". ") == 0 and said.count(";") <= 1
    assert "last" in said and "same Google account" in said and "notes stay" in said
    assert "access away at Google" not in HOSTED_DISCONNECT
    assert "last" in HOSTED_DISCONNECT


def test_no_token_code_or_secret_reaches_any_log(world, caplog):
    caplog.set_level(logging.DEBUG)
    ada = world.person(ADA)
    started = start(ada)
    query = world.google.consent(started.headers["location"])
    sealed = started.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    said(ada, come_back(world, ada, query))
    ((name, *_),) = rows(world.db, "SELECT credential FROM credential_tokens")
    (first,) = world.google.tokens
    said(ada, connect(world, ada, "gmail", account=name))
    said(ada, disconnect(ada, name))
    secrets = (first, *world.google.revoked, query["code"], sealed, CLIENT_SECRET, KEY_B64)
    # the test's browser (Starlette's TestClient, logger "httpx2") logs the addresses it opens,
    # the code among them, as a browser's history would: everything else is the server's
    server = [r for r in caplog.records if r.name != "httpx2"]
    assert world.google.revoked and server
    assert any("google" in r.getMessage() for r in caplog.records if r.name == "httpx2")
    assert not holds_secret("\n".join(r.getMessage() for r in server), secrets)
    assert not holds_secret("\n".join(str(r.args) for r in server), secrets)


def test_with_the_switch_off_removing_a_connected_account_says_googles_permission_stays(world):
    from cairn.store.sources import HOSTED_LEFT

    ada = world.person(ADA)
    assert "Connected." in said(ada, connect(world, ada))
    (name,) = credentials(world, "gmail")
    world.switch = False
    world.store.google_custody = None  # as a process started without the switch has it
    world.restart()
    off = TestClient(world.app, base_url=BASE)
    world.clients.append(off)
    off.cookies.update(ada.cookies)
    where = f"/integrations/accounts/{name}/disconnect"
    asked = off.post(where, follow_redirects=False)
    assert asked.status_code == 200 and HOSTED_LEFT in html.unescape(asked.text)
    done = off.post(where, data={"confirm": name}, follow_redirects=False)
    assert done.status_code == 200 and HOSTED_LEFT in html.unescape(done.text)
    assert held(world.db) == [] and world.google.revoked == []


def test_with_the_switch_off_the_api_says_googles_permission_stays(world):
    from cairn.store.sources import HOSTED_LEFT

    ada = world.person(ADA)
    assert "Connected." in said(ada, connect(world, ada))
    (name,) = credentials(world, "gmail")
    world.switch = False
    world.store.google_custody = None  # as a process started without the switch has it
    world.restart()
    key = world.store.for_account(*owned(world, ADA)).create_key("ada-desk")
    api = TestClient(world.app, base_url=BASE)
    world.clients.append(api)
    answer = api.delete(f"/api/credentials/{name}", params={"confirm": name},
                        headers={"x-api-key": key})  # fmt: skip
    assert answer.status_code == 200, answer.text
    assert answer.json()["google"] == HOSTED_LEFT
    assert held(world.db) == [] and world.google.revoked == []


def test_with_the_switch_on_the_generic_disconnect_of_a_connected_account_is_refused(world):
    from cairn.store.sources import HOSTED_LEFT

    ada = world.person(ADA)
    assert "Connected." in said(ada, connect(world, ada))
    (name,) = credentials(world, "gmail")
    where = f"/integrations/accounts/{name}/disconnect"
    asked = ada.post(where, follow_redirects=False)
    assert HOSTED_LEFT not in html.unescape(asked.text)
    refused = ada.post(where, data={"confirm": name}, follow_redirects=False)
    assert refused.status_code == 403 and len(held(world.db)) == 1


def test_with_the_switch_off_the_way_back_answers_as_any_unknown_address_did(tmp_path):
    off = World(tmp_path, switch=False)
    try:
        stranger = TestClient(off.app, base_url=BASE)
        back = stranger.get("/integrations/google/back?code=c&state=s", follow_redirects=False)
        other = stranger.get("/integrations/made-up/back?code=c&state=s", follow_redirects=False)
        stranger.close()
        assert (back.status_code, back.text) == (other.status_code, other.text)
    finally:
        off.close()
