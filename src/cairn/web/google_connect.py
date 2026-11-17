"""A Google account connected in the browser: `serve --google-connect`, its routes and its block
on the Sources page. The custody of what it obtains is cairn.store.hosted.

The switch (off unless the operator switches it on)
- `serve --google-connect` reads two variables of the server's environment, before anything
  is opened: CLIENT_VARIABLE, the JSON the Cloud console gives for an OAuth client of the type
  Web application (a "web" object holding client_id and client_secret), never the sign-in
  client (a client id equal to a sign-in provider's is refused); and KEY_VARIABLE, 32 bytes
  in base64, the key the refresh tokens are sealed with. Either missing or malformed: serve
  exits 2 with one sentence that names the variable and never a value. It needs --sign-in
  and --public-url. `--google-connect-for ADDRESS` (repeatable, which needs the switch) lists
  the signed-in people who are offered it; without it nobody is.
- Off (create_app without `google_connect`): no route, no block, no table read, nothing in
  any answer.

The flow (tests/unit/test_google_connect_unit.py)
- POST CONNECT_PATH (a signed-in session, same origin as every form post; offered to the
  person: listed, and an owner or admin), the kind as a form field (one of SERVICES), and
  `account` to reconnect one of the person's own: 303 to Google's authorization endpoint with
  exactly client_id, redirect_uri (<public url>BACK_PATH), response_type `code`, scope (the
  kind's one read-only scope, then `openid email`), state, code_challenge (S256),
  code_challenge_method, access_type `offline` and prompt `consent` (cli_google's
  authorization_url: docs/google-auth.md B.1 names access_type and prompt, the rest is from
  docs/google-oauth-installed.md). The request (`Flows`) is sealed by AES-GCM under the key
  the tokens are sealed with, with its own purpose (FLOW) as associated data and a fresh
  nonce, so the browser cannot read it and it still opens after a restart: the session's
  person and workspace, the kind, the account to reconnect, the state and the PKCE verifier,
  for TTL seconds by the wall clock. It is kept in an HttpOnly,
  SameSite=Lax cookie (COOKIE, Path=COOKIE_PATH): Google sees only the random state and the
  challenge, never the verifier.
- GET BACK_PATH is where Google sends the browser. It arrives from another site, so without
  the session cookie (SameSite=Strict): Auth passes it as an open path, and it only answers a
  page that moves on by itself (a meta refresh, no script) to FINISH_PATH with the same
  `code`, `state` and `error`, which is a request from this site and brings the session.
- GET FINISH_PATH (a session): the cookie's request must open (intact, unexpired, this step),
  its state must be the one given, and its person and workspace the session's; it is settled
  once, before the one call to Google. Otherwise ("stale"), or with Google's `error`, nothing
  is sent or stored. The code is exchanged server side (`exchange`); the answer must grant the
  kind's scope (NOT_GRANTED, per kind), hold a refresh token, and an ID token naming the
  account (`consented`). Then Store.connect_google keeps it, and the browser goes to
  /sources, where the block lists it. Each outcome goes to /sources as a code in a sealed
  cookie for its person (SAID_COOKIE, never the address), said once by the block, a refusal
  in role="alert"; nothing Google wrote is shown.
  Connecting writes no source and no note, and reads nothing but the exchange.
- POST DISCONNECT_PATH (`account`, one the viewer holds or, for an owner, any of the
  workspace's; `confirm`, its name typed): the row and the settings row are deleted and the
  account's sources switched off (Store.disconnect_google); then the revoking rule
  (`revoke_unless_held`): Google's permission is ended (`revoke`) only when no other row on
  this server, in any workspace, holds the same Google account, known by Google's own id of
  it (the ID token's sub), not by its address, which can change. The block says what is left.
  A removed person's and a deleted workspace's accounts go by the same rule (`Revoker`).
- A code, a token, the client's secret and the key are never logged, kept or shown.
"""

import argparse
import asyncio
import base64
import binascii
import contextlib
import hmac
import html
import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request, cookie_parser
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import cairn.store.hosted as hosted_store
from cairn.identity import Google
from cairn.memory import Forbidden, Memory
from cairn.oauth import SETTLED_MAX
from cairn.sources.google_api import TooLarge, Unreachable, fetch
from cairn.sources.google_auth import GOOGLE_TOKEN_URL, MAX_RESPONSE_BYTES, TOKEN
from cairn.sources.runner import SOURCE_TIMEOUT
from cairn.store.hosted import (
    FULL,
    KEY_BYTES,
    MINE_FULL,
    NAME_TAKEN,
    NOT_HERE,
    OTHER_GOOGLE_ACCOUNT,
    RECONNECT,
    GoogleCustody,
    Sealer,
    TokenUnusable,
    _subject,
)
from cairn.web.auth import MEMORY, _memory
from cairn.web.cli_google import (
    GOOGLE_AUTH_URL,
    SCOPES,
    authorization_url,
    challenge,
    new_state,
    new_verifier,
)
from cairn.web.templating import templates

log = logging.getLogger("cairn.google_connect")
CLIENT_VARIABLE = "CAIRN_GOOGLE_CONNECT_CLIENT"
KEY_VARIABLE = "CAIRN_GOOGLE_CONNECT_KEY"
SIGN_IN_CLIENT_IDS = ("CAIRN_GOOGLE_CLIENT_ID", "CAIRN_GITHUB_CLIENT_ID", "CAIRN_OIDC_CLIENT_ID")
CLIENT_VALUE = re.compile(r"[\x21-\x7e]{1,256}")  # as cairn.web.cli_google reads a client
ADDRESS = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}")
REFUSED = "cannot connect Google accounts that way: {}"


@dataclass(frozen=True)
class GoogleConnect:
    """The switch, on: the custody of the tokens (the key and the Web client) and the
    addresses of the signed-in people it is offered to (case-folded)."""

    custody: GoogleCustody = field(repr=False)
    offered: frozenset[str] = frozenset()


def read_switch(
    environ: Mapping[str, str], offered: list[str] | tuple[str, ...] = ()
) -> GoogleConnect:
    """The switch from the environment; ValueError in one sentence that names the variable or
    the option and never a value."""
    text = environ.get(CLIENT_VARIABLE, "")
    if not text.strip():
        raise ValueError(
            f"set {CLIENT_VARIABLE} to the JSON the Cloud console gives for an OAuth client of"
            " the type Web application"
        )
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        document = None
    client = document.get("web") if isinstance(document, dict) else None
    values = [
        client.get(name) if isinstance(client, dict) else None
        for name in ("client_id", "client_secret")
    ]
    if not all(isinstance(v, str) and CLIENT_VALUE.fullmatch(v) for v in values):
        raise ValueError(
            f'{CLIENT_VARIABLE} does not hold a Web application client\'s JSON (a "web" object'
            " with client_id and client_secret, as the Cloud console gives it)"
        )
    client_id, client_secret = values
    if any(environ.get(name, "").strip() == client_id for name in SIGN_IN_CLIENT_IDS):
        raise ValueError(
            f"{CLIENT_VARIABLE} holds the sign-in client: give a Web application client of its"
            " own, so that connecting an account stays apart from signing in"
        )
    raw = environ.get(KEY_VARIABLE, "").strip()
    if not raw:
        raise ValueError(
            f"set {KEY_VARIABLE} to {KEY_BYTES} random bytes in base64 (openssl rand -base64"
            f" {KEY_BYTES}): the key the tokens are sealed with"
        )
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != KEY_BYTES:
        raise ValueError(f"{KEY_VARIABLE} is not {KEY_BYTES} bytes in base64")
    addresses = set()
    for position, address in enumerate(offered, 1):
        if not isinstance(address, str) or not ADDRESS.fullmatch(address.strip()):
            raise ValueError(f"--google-connect-for number {position} is not one address")
        addresses.add(address.strip().casefold())
    custody = GoogleCustody(Sealer(key), client_id=client_id, client_secret=client_secret)
    return GoogleConnect(custody=custody, offered=frozenset(addresses))


def add_options(serve: argparse.ArgumentParser) -> None:
    serve.add_argument(
        "--google-connect",
        action="store_true",
        help=f"with --sign-in and --public-url: people connect a Google account on the Sources"
        f" page; the Web client's JSON comes from {CLIENT_VARIABLE} and the sealing key"
        f" from {KEY_VARIABLE}",
    )
    serve.add_argument(
        "--google-connect-for",
        action="append",
        default=[],
        dest="google_connect_for",
        metavar="ADDRESS",
        help="with --google-connect: a signed-in person, by address, who is offered it"
        " (repeatable); without it, nobody is",
    )


def serve_switch(args: argparse.Namespace, environ: Mapping[str, str]) -> GoogleConnect | None:
    """What `serve` gives create_app, read before anything is opened; ValueError otherwise."""
    if not args.google_connect:
        if args.google_connect_for:
            raise ValueError("--google-connect-for needs --google-connect")
        return None
    if not args.sign_in:
        raise ValueError("--google-connect needs --sign-in: a signed-in person connects an account")
    if not args.public_url:
        raise ValueError(
            "--google-connect needs --public-url: Google sends the browser back to"
            " <public url>/integrations/google/back"
        )
    return read_switch(environ, args.google_connect_for)


# The flow

FLOW = json.dumps(["google-connect-flow"]).encode()  # the flow's associated data (Flows)
TTL = 600  # seconds from Connect to the way back
COOKIE = "google_connect"  # no product name in it (B1)
COOKIE_PATH = "/integrations/google"
# What a connect or a disconnect came to, said once on the Sources page: written by the handler
# that knows it, sealed (OUTCOME as associated data) with the person and workspace it is for,
# read by the page's block, then cleared. Never the address: a crafted link says nothing.
SAID_COOKIE = "google_connect_said"
SAID_PATH = "/sources"
SAID_TTL = 300  # seconds from the handler to the page
OUTCOME = json.dumps(["google-connect-outcome"]).encode()
CONNECT_PATH = "/integrations/google/connect"
BACK_PATH = "/integrations/google/back"
FINISH_PATH = "/integrations/google/finish"
DISCONNECT_PATH = "/integrations/google/disconnect"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"  # docs/google-auth.md B.4
IDENTITY_SCOPES = ("openid", "email")  # to know which account consented
EXCHANGE_TIME = SOURCE_TIMEOUT  # seconds the exchange or the revocation may take in all
SERVICES = {  # each kind's service, and what its one scope lets cairn do, in words
    "gmail": ("Gmail", "reading your mail"),
    "google_calendar": ("Google Calendar", "reading your calendar"),
    "google_drive": ("Google Drive", "reading your Drive files"),
    "google_meet": ("Google Meet", "reading your Meet transcripts"),
}
WHAT = (
    "Connecting asks Google for one read-only permission for the service you choose, and keeps"
    " the token Google gives, sealed. It reads nothing and writes nothing until you make a"
    " source from the account and switch that source on; then the source reads only what you"
    " chose."
)
TESTING = (  # in the sources guide's words
    'While this server\'s Google consent screen is External and in "Testing", a connection'
    " stops working after 7 days, and you must be listed among its test users."
)
DISCONNECT_SAYS = (
    "Disconnect deletes the token held here and switches the account's sources off, while their"
    " notes stay, and it ends Google's permission too unless the same Google account is still"
    " connected elsewhere on this server, in which case that permission ends when the last"
    " one is disconnected."
)
NOT_OFFERED = "connecting a Google account is not offered to you on this server"
SAID = {  # what the block says on the way back, by the code in the address (never Google's text)
    "connected": "Connected. Make a source from it below: nothing is read until then.",
    "reconnected": "Reconnected: the account keeps its name, its sources and where they write.",
    "disconnected": (
        "Disconnected: its token is deleted here and its sources are switched off; their notes"
        " stay."
    ),
}
END_IT_YOURSELF = (
    "end it yourself in your Google account's settings, among the third-party apps and"
    " services with access to your account"
)
LEFT_SAID = {  # what is left of Google's permission once a token is given up, by the rule
    "revoked": "Google's permission for this server was ended.",
    "gone": (
        "Google said this token had ended already, so it could not end Google's permission"
        f" with it; if this server still shows in your account, {END_IT_YOURSELF}."
    ),
    "kept": (
        "This Google account is still connected elsewhere on this server (another service,"
        " person or workspace), so Google's permission stays: it ends when the last connection"
        " here is disconnected."
    ),
    "unreachable": (
        "Google could not be reached to end its permission; this server tries again later,"
        f" or {END_IT_YOURSELF}."
    ),
    "refused": f"Google refused to end its permission: {END_IT_YOURSELF}.",
    "unopened": (
        "This server could not open its token (its sealing key was changed), so it could not"
        f" end Google's permission: {END_IT_YOURSELF}."
    ),
}
REFUSALS = {  # a refusal's sentence, by the code in the address
    "stale": (
        "The connection was not finished: it was started too long ago, finished already, or"
        " started in another browser or by another person. Nothing was connected; start again."
    ),
    "denied": "You did not allow it at Google, so nothing was connected.",
    "policy": (
        "Your Google Workspace administrator's policy does not allow this permission"
        " (admin_policy_enforced), so nothing was connected."
    ),
    "org": (
        "This server's Google app is limited to accounts of its own organisation (org_internal),"
        " so nothing was connected."
    ),
    "client": (
        "Google refused this server's client, so nothing was connected: the person who runs"
        " this server checks its Google client."
    ),
    "no_token": (
        "Google gave no lasting token, so nothing was connected: take this server's access away"
        " in your Google account's settings, then connect again."
    ),
    "unreachable": "Google could not be reached, so nothing was connected. Try again later.",
    "refused": "Google refused the connection, so nothing was connected.",
    "unknown": "There is no such Google account of yours here; nothing was changed.",
    "confirm": "Type the account's name to disconnect it, as a check; nothing was changed.",
    "full": f"{FULL[:1].upper()}{FULL[1:]}.",
    "mine_full": f"{MINE_FULL[:1].upper()}{MINE_FULL[1:]}.",
}
# Refusals that can come after Google issued a token: the reason, then what is left of
# Google's permission (LEFT_REFUSED, by the revoking rule).
GIVEN_UP = {
    "identity": (
        "Google's answer did not name the account that consented in a way this server can check."
    ),
    "other": f"{OTHER_GOOGLE_ACCOUNT[:1].upper()}{OTHER_GOOGLE_ACCOUNT[1:]}.",
    "not_here": f"{NOT_HERE[:1].upper()}{NOT_HERE[1:]}.",
    "forbidden": "This workspace did not let you connect a Google account.",
    "full": f"{FULL[:1].upper()}{FULL[1:]}.",
    "mine_full": f"{MINE_FULL[:1].upper()}{MINE_FULL[1:]}.",
    "taken": f"{NAME_TAKEN[:1].upper()}{NAME_TAKEN[1:]}.",
    "refused_here": "This server could not keep the connection.",
}
NOT_GRANTED = "Google did not grant {reading}."
LEFT_REFUSED = {
    "none": "Nothing was connected.",
    "revoked": "Nothing was connected, and the permission Google had given was ended.",
    "kept": (
        "Nothing new was kept. This Google account is still connected elsewhere on this server,"
        " so Google's permission stays: it ends when the last connection here is disconnected."
    ),
    "unknown": (
        f"Nothing was kept here, but the permission Google gave may stay: {END_IT_YOURSELF}."
    ),
}
for _code in ("unreachable", "refused", "gone"):
    LEFT_REFUSED[_code] = LEFT_REFUSED["unknown"]
# Google's `error` codes, in the redirect or the exchange's answer (docs/google-auth.md B.5,
# docs/google-oauth-installed.md F.2), and the refusal each is said as. Only the code is read.
ERRORS = {
    "access_denied": "denied",
    "admin_policy_enforced": "policy",
    "org_internal": "org",
    "invalid_client": "client",
    "deleted_client": "client",
    "unauthorized_client": "client",
    "invalid_grant": "stale",
}


class _Refusal(Exception):
    """A refused connect, by its code; with the token and the address when Google had issued
    one before the refusal (they are given up by the revoking rule)."""

    def __init__(
        self,
        code: str,
        *,
        token: str | None = None,
        subject: str | None = None,
        issued: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.token = token
        self.subject = subject
        self.issued = issued or token is not None  # Google answered the exchange with a grant

    def __repr__(self) -> str:  # never the token
        return f"_Refusal({self.code!r})"


@dataclass(frozen=True)
class Granted:
    address: str
    subject: str  # Google's own id of the account (the ID token's sub)
    scopes: list[str]
    refresh_token: str = field(repr=False)


def _claims(id_token: object) -> dict:
    """The ID token's claims, unverified: it came over TLS from Google's token endpoint in
    exchange for this client's secret, as cairn.identity reads one (OpenID Connect Core,
    3.1.3.7)."""
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        raise _Refusal("identity")
    payload = id_token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, binascii.Error, RecursionError):
        raise _Refusal("identity") from None
    if not isinstance(claims, dict):
        raise _Refusal("identity")
    return claims


def consented(id_token: object, client_id: str, now: float) -> tuple[str, str]:
    """The address of the account that consented, and Google's own id of it (`sub`, at most
    SUBJECT_MAX printable ASCII characters): the ID token must name Google as its issuer
    (cairn.identity.Google.issuer), this client as its audience (and as `azp` when it names
    several), an `exp` after `now`, an email it marks verified, and the id."""
    claims = _claims(id_token)
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    expires = claims.get("exp")
    email = claims.get("email")
    if (
        claims.get("iss") != Google.issuer
        or client_id not in audiences
        or (len(audiences) > 1 and claims.get("azp") != client_id)
        or isinstance(expires, bool)
        or not isinstance(expires, int | float)
        or expires <= now
        or claims.get("email_verified") not in (True, "true")
        or not isinstance(email, str)
        or not ADDRESS.fullmatch(email.strip())
    ):
        raise _Refusal("identity")
    try:
        subject = _subject(claims.get("sub"))
    except ValueError:
        raise _Refusal("identity") from None
    return email.strip(), subject


async def exchange(
    make_http: Callable[[], httpx.AsyncClient],
    custody: GoogleCustody,
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
    kind: str,
    now: Callable[[], float] = time.time,
) -> Granted:
    """The code exchanged for tokens (one POST of the form docs/google-oauth-installed.md G
    and cli_google send, with this Web client), checked: _Refusal with a code otherwise. The
    access token is dropped here."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": custody.client_id,
        "client_secret": custody.client_secret,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    try:
        async with asyncio.timeout(EXCHANGE_TIME):
            async with make_http() as http:
                status, body = await fetch(
                    http, "POST", GOOGLE_TOKEN_URL, data=form,
                    headers={"Accept": "application/json"}, limit=MAX_RESPONSE_BYTES,
                )  # fmt: skip
    except (Unreachable, TooLarge, TimeoutError):
        raise _Refusal("unreachable") from None
    try:
        answer = json.loads(body)
    except (ValueError, RecursionError):
        answer = None
    if status != 200 or not isinstance(answer, dict):
        error = answer.get("error") if isinstance(answer, dict) else None
        raise _Refusal(ERRORS.get(error, "refused") if isinstance(error, str) else "refused")
    token = answer.get("refresh_token")
    refresh_token = token if isinstance(token, str) and TOKEN.fullmatch(token) else None
    try:  # first, so that every refusal after it knows whose permission it holds
        address, subject = consented(answer.get("id_token"), custody.client_id, now())
    except _Refusal as refusal:
        raise _Refusal(refusal.code, token=refresh_token, issued=True) from None
    scope = answer.get("scope")
    scopes = scope.split() if isinstance(scope, str) else []
    if not set(SCOPES[kind]) <= set(scopes):
        raise _Refusal("not_granted", token=refresh_token, subject=subject, issued=True)
    if refresh_token is None:
        raise _Refusal("no_token", issued=True)
    return Granted(address=address, subject=subject, scopes=scopes, refresh_token=refresh_token)


async def revoke(make_http: Callable[[], httpx.AsyncClient], token: str) -> str:
    """Google asked to end the permission `token` carries (docs/google-auth.md B.4, the token
    in the query as documented): "revoked" for 200; "gone" for 400, which is what a token
    already ended gets (nothing is left to end with it, but it proves nothing of the
    account's other tokens); "unreachable" for no answer, a 5xx or a 429 (tried again later);
    "refused" for another answer. Nothing about
    the request (its address holds the token) is raised, logged or kept."""
    try:
        async with asyncio.timeout(EXCHANGE_TIME):
            async with make_http() as http:
                status, _ = await fetch(
                    http, "POST", REVOKE_URL, params={"token": token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    limit=MAX_RESPONSE_BYTES,
                )  # fmt: skip
    except Exception:  # whatever it was, its text may hold the address
        return "unreachable"
    if status == 200:
        return "revoked"
    if status == 400:
        return "gone"
    return "unreachable" if status >= 500 or status == 429 else "refused"


async def revoke_unless_held(
    make_http: Callable[[], httpx.AsyncClient], store: Any, subject: str, token: str
) -> str:
    """The revoking rule, the one place it is decided. Called after the account's row is
    deleted: revoking ends the Google account's permission for every client of the Cloud
    project (docs/google-auth.md B.4), so every connection of that Google account on this
    server, in any workspace. Keyed on Google's own id of the account (`subject`, the ID
    token's sub), not its address, which can change. "kept" (nothing sent) while another row
    here holds it (Store.google_account_held); otherwise what `revoke` answers. Every caller
    sends a token it alone holds (its claimed waiting row, or one never kept), so only its
    own 200 marks a connection made meanwhile to be reconnected, and a 400 never does."""
    if await run_in_threadpool(store.google_account_held, subject):
        return "kept"
    left = await revoke(make_http, token)
    if left == "revoked" and await run_in_threadpool(store.google_account_held, subject):
        # connected again while the revocation was under way: Google may have ended it too
        await run_in_threadpool(store.google_account_ended, subject)
    return left


def _back_page(target: str) -> Response:
    """The way back from Google, which arrives with no session (it comes from another site,
    and the session cookie is SameSite=Strict): a page that moves on by itself to `target`
    on this site, where the browser brings its session. No stylesheet, nothing that names the
    product, and no referrer: its address holds the code."""
    shown = html.escape(target, quote=True)
    body = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={shown}"><title>One moment</title></head>'
        f'<body><p>One moment: this page moves on by itself. <a href="{shown}">Continue</a> if'
        " nothing happens.</p></body></html>"
    )
    return HTMLResponse(
        body,
        headers={
            "cache-control": "no-store",
            "referrer-policy": "no-referrer",
            "x-content-type-options": "nosniff",
            "content-security-policy": (
                "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
        },
    )


def _store_refusal(refused: Exception) -> str:
    """The code a refusal of Store.connect_google is said by, never a 500."""
    if isinstance(refused, TokenUnusable):
        return "not_here"
    if isinstance(refused, KeyError):
        return "unknown"
    if isinstance(refused, PermissionError):
        return "other" if str(refused) == OTHER_GOOGLE_ACCOUNT else "forbidden"
    if str(refused) == FULL:
        return "full"
    if str(refused) == MINE_FULL:
        return "mine_full"
    if str(refused) == NAME_TAKEN:
        return "taken"
    return "refused_here"


def _now() -> float:
    """The wall clock: a flow's deadline must mean the same after a restart."""
    return time.time()


class Flows:
    """A connect's request, carried by the browser instead of stored: sealed by AES-GCM under
    the custody key with FLOW as associated data (a token's associated data is a JSON list of
    three, so neither opens as the other), with a fresh nonce each time. `open` gives it back
    only intact and before its deadline; `settle` is True once per request id in this process,
    for at most `limit` ids at once (the nearest deadline is dropped first). After a restart
    an unexpired request could be settled once more: its code was redeemed at Google already,
    which refuses it again."""

    def __init__(self, sealer: Sealer, limit: int = SETTLED_MAX) -> None:
        self._sealer = sealer
        self._limit = limit
        self._settled: dict[str, float] = {}  # request id -> its deadline
        self._lock = threading.Lock()

    def seal(self, fields: dict[str, Any], ttl: float) -> str:
        request = {**fields, "id": secrets.token_urlsafe(16), "exp": _now() + ttl}
        return self._sealer.seal(json.dumps(request), FLOW)

    def open(self, sealed: object) -> dict[str, Any] | None:
        plain = self._sealer.open(sealed, FLOW)
        if plain is None:
            return None
        try:
            request = json.loads(plain)
        except ValueError:
            return None
        if not isinstance(request, dict) or not isinstance(request.get("exp"), (int, float)):
            return None
        return request if request["exp"] > _now() else None

    def settle(self, request: dict[str, Any]) -> bool:
        now = _now()
        with self._lock:
            for done in [id_ for id_, exp in self._settled.items() if exp <= now]:
                del self._settled[done]
            if request["id"] in self._settled:
                return False
            if len(self._settled) >= self._limit:
                del self._settled[min(self._settled, key=self._settled.__getitem__)]
            self._settled[request["id"]] = request["exp"]
            return True


def google_routes(config: GoogleConnect, public_url: str) -> list[Route]:
    """POST CONNECT_PATH, GET BACK_PATH (an open path: Auth passes it with no session),
    GET FINISH_PATH and POST DISCONNECT_PATH."""
    custody = config.custody
    states = Flows(custody.sealer)
    redirect_uri = public_url.rstrip("/") + BACK_PATH
    secure = public_url.lower().startswith("https://")

    def offered(memory: Memory) -> bool:
        person = memory.person
        return person is not None and person.email.strip().casefold() in config.offered

    def told(
        memory: Memory, said: str, kind: str | None = None, *, left: str | None = None
    ) -> RedirectResponse:
        """To the Sources page, the outcome in its sealed cookie for this person only."""
        outcome = {
            "said": said, "kind": kind, "left": left, "person": memory.store.user_id,
            "workspace": memory.store.account_id, "exp": _now() + SAID_TTL,
        }  # fmt: skip
        response = RedirectResponse(f"{SAID_PATH}#google-connect", status_code=303)
        response.set_cookie(
            SAID_COOKIE, custody.sealer.seal(json.dumps(outcome), OUTCOME), max_age=SAID_TTL,
            path=SAID_PATH, secure=secure, httponly=True, samesite="lax",
        )  # fmt: skip
        response.headers["cache-control"] = "no-store"
        return response

    def ended(response: Response) -> Response:
        response.delete_cookie(
            COOKIE, path=COOKIE_PATH, secure=secure, httponly=True, samesite="lax"
        )
        response.headers["cache-control"] = "no-store"
        return response

    async def connect(request: Request) -> Response:
        memory = _memory(request)
        if not await run_in_threadpool(offered, memory):
            raise Forbidden(NOT_OFFERED)
        form = await request.form()
        kind, account = form.get("kind"), form.get("account") or None
        if not isinstance(kind, str) or kind not in SERVICES:
            return told(memory, "unknown")
        if account is not None:
            own = await run_in_threadpool(memory.store.google_accounts)
            if not any(held.name == account and held.kind == kind for held in own):
                return told(memory, "unknown")
        elif (no_room := await run_in_threadpool(memory.store.google_room)) is not None:
            return told(memory, no_room, kind)
        verifier, state = new_verifier(), new_state()
        sealed = states.seal(
            {
                "person": memory.store.user_id, "workspace": memory.store.account_id,
                "kind": kind, "account": account, "state": state, "verifier": verifier,
            },
            TTL,
        )  # fmt: skip
        url = authorization_url(
            GOOGLE_AUTH_URL, client_id=custody.client_id, redirect_uri=redirect_uri,
            scopes=[*SCOPES[kind], *IDENTITY_SCOPES], state=state, challenge=challenge(verifier),
        )  # fmt: skip
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(
            COOKIE, sealed, max_age=TTL, path=COOKIE_PATH, secure=secure, httponly=True,
            samesite="lax",
        )  # fmt: skip
        response.headers["cache-control"] = "no-store"
        return response

    async def back(request: Request) -> Response:
        query = request.query_params
        kept = {
            name: query[name]
            for name in ("code", "state", "error")
            if len(query.getlist(name)) == 1 and len(query[name]) <= 2048
        }
        return _back_page(f"{FINISH_PATH}?{urlencode(kept)}")

    async def finish(request: Request) -> Response:
        memory = _memory(request)
        pending = states.open(request.cookies.get(COOKIE))
        query = request.query_params
        given = query.getlist("state")
        if (
            pending is None
            or len(given) != 1
            or not hmac.compare_digest(given[0].encode(), str(pending["state"]).encode())
            or pending["person"] != memory.store.user_id
            or pending["workspace"] != memory.store.account_id
        ):
            return ended(told(memory, "stale"))
        kind = pending["kind"]
        if "error" in query:  # Google, or the person, said no: nothing is settled or sent
            error = query["error"]
            return ended(told(memory, ERRORS.get(error, "refused"), kind))
        codes = query.getlist("code")
        if len(codes) != 1 or not codes[0] or not states.settle(pending):
            return ended(told(memory, "stale"))
        if not await run_in_threadpool(offered, memory):
            return ended(told(memory, "stale"))
        try:
            granted = await exchange(
                request.app.state.source_http, custody, code=codes[0],
                verifier=pending["verifier"], redirect_uri=redirect_uri, kind=kind,
            )  # fmt: skip
        except _Refusal as refusal:
            return ended(await given_up(request, refusal, kind))
        try:
            connected = await run_in_threadpool(
                memory.store.connect_google, kind, granted.address, granted.scopes,
                granted.refresh_token, subject=granted.subject, expect=pending["account"],
            )  # fmt: skip
        except Exception as refused:  # any refusal, never a 500 while a live token is held
            code = _store_refusal(refused)
            if code == "refused_here":
                log.warning("google-connect: a connection was not kept: %s", type(refused).__name__)
            refusal = _Refusal(code, token=granted.refresh_token, subject=granted.subject)
            return ended(await given_up(request, refusal, kind))
        return ended(told(memory, "reconnected" if connected.reconnected else "connected", kind))

    async def given_up(request: Request, refusal: _Refusal, kind: str) -> RedirectResponse:
        """A refused connect, said: when Google had issued a token, it is given up by the
        revoking rule, and what is left of Google's permission is said with it."""
        memory = _memory(request)
        if refusal.token is None:  # after Google's grant, a permission may stay with no token
            return told(memory, refusal.code, kind, left="unknown" if refusal.issued else "none")
        if refusal.subject is None:  # whose permission it is, nothing here can tell
            return told(memory, refusal.code, kind, left="unknown")
        left = await revoke_unless_held(
            request.app.state.source_http, memory.store, refusal.subject, refusal.token
        )
        return told(memory, refusal.code, kind, left=left)

    async def disconnect(request: Request) -> Response:
        memory = _memory(request)
        if memory.person is None:
            raise Forbidden(NOT_OFFERED)
        form = await request.form()
        account, confirm = form.get("account"), form.get("confirm")
        if not isinstance(account, str) or not account:
            return told(memory, "unknown")
        if not isinstance(confirm, str) or confirm.strip().casefold() != account.casefold():
            return told(memory, "confirm")
        try:
            dropped = await run_in_threadpool(memory.store.disconnect_google, account)
        except KeyError:
            return told(memory, "unknown")
        if dropped.token is None or dropped.subject is None:
            left = "unopened"
        else:
            left = await revoke_unless_held(
                request.app.state.source_http, memory.store, dropped.subject, dropped.token
            )
        if dropped.pending is not None:  # written claimed: no pass sends it meanwhile
            await _ended(memory.store, dropped.pending, dropped.claim, left)
        return told(memory, "disconnected", left=left)

    return [
        Route(CONNECT_PATH, connect, methods=["POST"]),
        Route(BACK_PATH, back, methods=["GET"]),
        Route(FINISH_PATH, finish, methods=["GET"]),
        Route(DISCONNECT_PATH, disconnect, methods=["POST"]),
    ]


# The block on the Sources page (templates/_google_connect.html)

BLOCK: ContextVar[Callable[[], dict[str, Any] | None] | None] = ContextVar(
    "google_connect_block", default=None
)


def _block() -> dict[str, Any] | None:
    """What the block shows to the request being answered; None (and nothing read) when the
    switch is off or the person is not offered it."""
    found = BLOCK.get()
    return found() if found is not None else None


templates.globals["google_connect"] = _block


def outcome_for(scope: Scope, config: GoogleConnect) -> dict[str, Any]:
    """The outcome a handler left for this request's person (SAID_COOKIE): {} when there is
    none, it does not open, it has expired, or it is another person's or workspace's."""
    memory = scope.get(MEMORY)
    sealed = cookie_parser(Headers(scope=scope).get("cookie", "")).get(SAID_COOKIE)
    plain = config.custody.sealer.open(sealed, OUTCOME) if sealed else None
    try:
        found = json.loads(plain) if plain is not None else None
    except ValueError:
        found = None
    if (
        not isinstance(found, dict)
        or not isinstance(memory, Memory)
        or not isinstance(found.get("exp"), (int, float))
        or found["exp"] <= _now()
        or found.get("person") != memory.store.user_id
        or found.get("workspace") != memory.store.account_id
    ):
        return {}
    return {name: found.get(name) or "" for name in ("said", "kind", "left")}


def block_for(
    scope: Scope, config: GoogleConnect, outcome: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    memory = scope.get(MEMORY)
    if not isinstance(memory, Memory) or memory.person is None:
        return None
    outcome = outcome or {}
    # Connect only for a listed person, whatever their role (a member's own account, decision
    # 145); the accounts and their Disconnect for whoever holds one (and an owner, for every
    # one of the workspace), listed or not; a member sees nothing of another's
    offered = memory.person.email.strip().casefold() in config.offered
    held = memory.store.google_accounts_to_manage()
    said = outcome.get("said") or ""
    if not offered and not held and said not in SAID and said not in REFUSALS | GIVEN_UP:
        return None  # what a Disconnect left is still said once the last account is gone
    kind = outcome.get("kind") or ""
    left = outcome.get("left") or ""
    refused = None
    if said == "not_granted" and kind in SERVICES:
        refused = NOT_GRANTED.format(reading=SERVICES[kind][1])
    elif said in GIVEN_UP and left in LEFT_REFUSED:
        refused = GIVEN_UP[said]
    elif said in REFUSALS:
        refused = REFUSALS[said]
    if refused is not None and (said == "not_granted" or said in GIVEN_UP) and left in LEFT_REFUSED:
        refused = f"{refused} {LEFT_REFUSED[left]}"
    done = SAID.get(said)
    if done is not None and left in LEFT_SAID:
        done = f"{done} {LEFT_SAID[left]}"
    accounts = [
        {
            "name": one.name,
            "kind": one.kind,
            "service": SERVICES[one.kind][0],
            "address": one.address,
            "refreshed_at": one.refreshed_at,
            "mine": one.owner == memory.store.user_id,
            "ended": RECONNECT if one.ended else None,
        }
        for one in held
    ]
    kinds = [{"kind": k, "service": s, "reading": r} for k, (s, r) in SERVICES.items()]
    return {
        "kinds": kinds if offered else [],
        "offered": offered,
        "accounts": accounts,
        "refused": refused,
        "done": done,
        "what": WHAT,
        "testing": TESTING,
        "disconnect": DISCONNECT_SAYS,
    }


class Block:
    """ASGI middleware, installed only with the switch on: each request carries, for the page
    that renders the block, the function that says what it shows (BLOCK), called only then.
    A page that read an outcome (SAID_COOKIE) clears it in its answer, so it is said once."""

    def __init__(self, app: ASGIApp, config: GoogleConnect, secure: bool = True) -> None:
        self.app = app
        self.config = config
        cleared = Response()
        cleared.delete_cookie(
            SAID_COOKIE, path=SAID_PATH, secure=secure, httponly=True, samesite="lax"
        )
        self.cleared = cleared.headers["set-cookie"]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        read: list[bool] = []

        def shown() -> dict[str, Any] | None:
            if SAID_COOKIE in cookie_parser(Headers(scope=scope).get("cookie", "")):
                read.append(True)
            return block_for(scope, self.config, outcome_for(scope, self.config))

        async def answer(message: Message) -> None:
            if message["type"] == "http.response.start" and read:
                headers = MutableHeaders(scope=message)
                headers.append("set-cookie", self.cleared)
                headers["cache-control"] = "no-store"
            await send(message)

        token = BLOCK.set(shown)
        try:
            await self.app(scope, receive, answer)
        finally:
            BLOCK.reset(token)


# What ends a waiting row: Google's 200 or 400. "kept" (another row holds the address) waits
# too, the address checked again on each pass, so the permission ends with the last connection
# however that one goes (the command line, the switch off, a token that no longer opens).
SETTLED = ("revoked", "gone")
RETRY_AFTER = 60.0  # seconds between the loop's passes over the waiting revocations
SHUTDOWN_BOUND = 15.0  # seconds the way out waits for a pass under way


async def _ended(store: Any, revocation: str, claim: str | None, left: str) -> bool:
    """A claimed row after its send: settled by Google's 200 or 400 (or a token that does not
    open), else released with its outcome, which is logged once per change of state (never
    "kept"), and a row stopped after REVOKE_TRIES refusals once, with its own code. Whether
    it is settled."""
    if left in SETTLED or left == "unopened":
        await run_in_threadpool(store.settle_google_revocation, revocation, claim)
        return True
    changed, stopped = await run_in_threadpool(
        store.release_google_revocation, revocation, claim, left
    )
    if changed and left != "kept":
        log.warning("google-connect: a dropped account's permission was not ended: %s", left)
    if stopped:
        log.warning(
            "google-connect: a dropped account's permission was given up after %d refusals:"
            " revoke_stopped",
            hosted_store.REVOKE_TRIES,  # read when said
        )
    return False


async def drain(
    make_http: Callable[[], httpx.AsyncClient], store: Any, custody: GoogleCustody
) -> bool:
    """One pass over the dropped accounts whose token waits in google_revocations
    (Store.pending_google_revocations), each by the revoking rule; never two at once in this
    process, and each row only once claimed (Store.claim_google_revocation), so no handler or
    other process sends it at once. Google's 200 or 400 settles a row, and so does a token that
    no longer opens here
    (logged "unopened": nothing could ever be sent); a row Google did not answer, refused, or
    the rule kept, waits for the next pass. One not ended is logged by its code (never an
    address or a token). Whether rows may remain."""
    waiting = custody.waiting
    if waiting.draining:
        return True
    waiting.draining = True
    remain = False
    try:
        for revocation, subject, token in await run_in_threadpool(store.pending_google_revocations):
            claim = await run_in_threadpool(store.claim_google_revocation, revocation)
            if claim is None:  # another sender holds it
                remain = True
                continue
            left = "unopened"
            if token is not None:
                try:
                    left = await revoke_unless_held(make_http, store, subject, token)
                except BaseException:  # stopped part way (cancelled): the next pass sends it
                    with contextlib.suppress(Exception):
                        store.release_google_revocation(revocation, claim)
                    raise
            if left == "unopened":
                log.warning(
                    "google-connect: a dropped account's permission was not ended: %s", left
                )
            if not await _ended(store, revocation, claim, left):
                remain = True
    except Exception as failed:  # the store, say: the rows wait for the next pass
        log.warning("google-connect: the revocations waiting were not sent: %s",
                    type(failed).__name__)  # fmt: skip
        remain = True
    finally:  # stopped part way (cancelled), the rows are still in the table for the next
        waiting.draining = False
    return remain


class Revoker:
    """The server's loop over the waiting revocations (`drain`), a background task of the
    application's lifespan, never inside a request: a pass at start-up, then one every
    RETRY_AFTER seconds, awaiting `wait(seconds)` (asyncio.sleep when None) as the other
    background loops do, or at once when a drop wakes it (Waiting.wake). `stop`: the loop
    ends after the pass under way, which the way out waits for (SHUTDOWN_BOUND). A failure of a
    pass is logged by its class, once until a pass succeeds, and the loop goes on."""

    def __init__(
        self,
        store: Any,
        custody: GoogleCustody,
        make_http: Callable[[], httpx.AsyncClient],
        wait: Callable[[float], Any] | None = None,
    ) -> None:
        self.store, self.custody, self.make_http = store, custody, make_http
        self.wait = wait or asyncio.sleep
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True
        self.custody.waiting.woken()

    async def loop(self) -> None:
        running = asyncio.get_running_loop()
        woken = asyncio.Event()
        self.custody.waiting.wake = lambda: running.call_soon_threadsafe(woken.set)
        failing: str | None = None  # the class last logged, so a failure is logged once
        try:
            while True:
                woken.clear()
                try:
                    await drain(self.make_http, self.store, self.custody)
                    failing = None
                except Exception as failed:  # never the loop's end: the next interval again
                    if type(failed).__name__ != failing:
                        failing = type(failed).__name__
                        with contextlib.suppress(Exception):
                            log.warning(
                                "google-connect: the loop over the waiting revocations failed: %s",
                                failing,
                            )
                if self._stopping:
                    return
                timer = asyncio.ensure_future(self.wait(RETRY_AFTER))
                waking = asyncio.ensure_future(woken.wait())
                try:
                    await asyncio.wait({timer, waking}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    timer.cancel()
                    waking.cancel()
                if self._stopping:
                    return
        finally:
            self.custody.waiting.wake = None

    async def stopped(self, task: "asyncio.Task[None]") -> None:
        """The way out: the loop stops after the pass under way, given SHUTDOWN_BOUND seconds,
        then cancelled. Rows not sent wait in the store for the next start."""
        self.stop()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), SHUTDOWN_BOUND)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
