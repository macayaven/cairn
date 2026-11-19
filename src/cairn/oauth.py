"""cairn as the OAuth 2.1 authorization server for its own /mcp endpoint.

The contract is in cairn/server.py ("OAuth"), frozen by tests/acceptance/test_h3_oauth.py
and tests/acceptance/test_h3b_oauth_strict.py. This module holds what serves it: when OAuth
is on, the redirect-URI policy, the bounds on unauthenticated registration, the consent
step, and the provider behind the MCP SDK's authorization and token handlers.

Who does what
- The MCP SDK parses /authorize and /token and enforces PKCE (S256), the redirect-URI match
  and the binding of a code or refresh token to its client. cairn mounts its handlers behind
  its own wrappers and gives them `Provider`. A redirect URI is matched as a URL: the SDK
  reads the registered URI and the requested one the same way, so another spelling of the
  registered URI (an upper-case host, the default port) is the same URI, and the browser is
  sent to the URI as cairn keeps it from the registration. One that is another address
  after that reading ("/cb/../other", "/cb/") is not the client's: 400 and no redirect.
- The wrappers check what the SDK reads loosely, on the same parse of the query or form the
  handler reads next, and refuse before anything is looked up or written:
  - A parameter of GET /authorize, or a field of POST /token or POST /revoke, named twice
    is 400 `invalid_request` (RFC 6749, 3.1), whatever the two values; at /authorize there
    is no redirect. `resource` alone may be given more than once (RFC 8707), and every
    value must name this server's /mcp.
  - POST /token for a code with a `code_verifier` that is not 43 to 128 characters of
    A-Z a-z 0-9 - . _ ~ (RFC 7636, 4.1) is 400 `invalid_request`: the same answer whether
    the code exists or not, and the code stays good for its real verifier. A client that
    made its challenge from a shorter verifier never gets a token.
- After the SDK token handler has answered, its refusals keep their status and error code
  and get cairn's description, one per error (_TOKEN_REFUSALS): an unknown code, a wrong
  verifier and a code issued to another client get the same body, and no answer repeats
  what the client sent. The same holds for what the authorization handler answers without
  a redirect, when the client or the redirect URI could not be verified
  (_AUTHORIZE_REFUSALS): an unknown client and a redirect URI that is not the client's get
  one body, without `state`. A refusal redirected to the verified redirect URI carries `error` and
  `state` (RFC 6749, 4.1.2.1), as the SDK makes it.
- cairn's own: /register (public clients only, bounded and rate-limited), /revoke (a public
  client sends no secret), the consent pages, and everything a token's life depends on, which
  is the store's: single-use codes, rotation, reuse detection, expiry, and the bound on
  rotation (a grant rotates at most cairn.store.grants.ROTATIONS times in any hour, counted
  in the database; past it /token answers 429). The store reads the time once it holds the
  write lock, so a code or refresh token that expires while its exchange waits for the lock
  is expired: nothing is issued and nothing is rotated.
- One resource and one scope. A request naming another resource is refused, at /authorize
  and at /token (the SDK's token handler parses `resource` and checks nothing, so cairn's
  wrapper does, before the handler runs). Every code and grant is bound to that resource,
  its audience: the store refuses it at any other, so moving `public_url` ends the grants
  made at the old one (their tokens are refused, and they expire). The one scope has two
  names (B1): SCOPE, and BRANDED_SCOPE, which a server under any brand says instead, so
  that no answer names the software; every server knows both, and no grant stores either.
  The scopes a registration or an authorization request asks for are not read, and the
  token response states the one scope granted by the server's current name (RFC 6749,
  3.3), as do the metadata and a registration's answer. A refresh request may ask for
  either name, or none; the SDK refuses one that asks for any other scope.
- Clients are public: PKCE and the redirect URI protect a code, not a client secret.
  A registration asking for another authentication method is given "none" (RFC 7591 lets the
  server replace a requested value), because cairn keeps no secret it could not store as a
  digest.

Between GET /authorize and the decision nothing is stored. The validated request travels
through the browser sealed (HMAC-SHA256) with a secret made when the process starts: one
process serves cairn, and a restart only makes a pending approval fail. The form on the
consent page carries a second sealing that also names the signed-in account, so a decision
only counts from the account that was shown the page. A request is settled once; the ids of
settled requests are kept in memory until they would have expired anyway, at most
SETTLED_MAX of them (`SealedRequests.settle` says what a full table costs).

This module imports nothing from cairn.server: the server hands it the page renderer, the
caller's account view and a `Ledger`.
"""

import base64
import functools
import hashlib
import heapq
import hmac
import ipaddress
import json
import logging
import re
import secrets
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import SplitResult, urlencode, urlsplit

from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.routes import build_metadata, create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import RequestBodyLimitMiddleware
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from mcp.shared.inbound import MCP_PROTOCOL_VERSION_HEADER
from pydantic import AnyUrl, TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, QueryParams
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from cairn.memory import BadRequest, choose_reach
from cairn.store import (
    CLIENT_NAME_MAX,
    REDIRECT_URI_MAX,
    REDIRECT_URIS_MAX,
    CodeInfo,
    OAuthClient,
    Store,
    TokenPair,
)
from cairn.store.grants import ROTATION_WINDOW, TooManyRotations

SCOPE = "cairn"  # the one thing a grant gives: this account's memory over /mcp
BRANDED_SCOPE = "memory"  # its other name, said under a brand (B1); the same for every brand
RESOURCE_NAME = "cairn"  # the protected resource's name in its metadata, without a brand
MCP_PATH = "/mcp"
CONSENT_PATH = "/oauth/consent"
METADATA_PATH = "/.well-known/oauth-authorization-server"
RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource" + MCP_PATH
# Machine endpoints: no session, and the last three also answer other origins (CORS).
PUBLIC_PATHS = frozenset({"/authorize", "/token", "/register", "/revoke"})
CORS_PATHS = frozenset({"/token", "/register", "/revoke"})
WELL_KNOWN = "/.well-known/"

CONSENT_TTL = 600  # seconds a person has to sign in and decide
MAX_BODY = 64 * 1024  # bytes, for /register, /token and /revoke
# Settled request ids kept at once (SealedRequests). One costs about 200 bytes, measured
# with tracemalloc: about 70 for its 22-character id, about 130 for its dict entry, its
# deadline and the (deadline, id) pair on the heap. A full table is about 10 MB, and there
# are two (sign-in and consent), so a flood costs at most about 20 MB.
SETTLED_MAX = 50_000
SETTLED_WARN_EVERY = 60  # seconds between two warnings that the table is full
REGISTRATIONS = 20  # per client address ...
REGISTRATION_WINDOW = 3_600  # ... in any hour
UNNAMED = "Unnamed client"
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")  # base64url of a SHA-256 digest, unpadded
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")  # RFC 7636, 4.1
REPEATABLE = frozenset({"resource"})  # RFC 8707; every other parameter is given once
_ONCE = "a parameter is given at most once; only resource may be given more than once"
_VERIFIER_FORM = "code_verifier is 43 to 128 characters of A-Z a-z 0-9 - . _ ~"
_ELSEWHERE = "this server only issues tokens for its own MCP URL"
# What POST /token says when the SDK's handler refuses: one fixed text per error, so an
# answer never repeats what the client sent (a grant type, a scope) and never says why a
# code or refresh token cannot be used.
_TOKEN_REFUSALS = {
    "invalid_request": "the request is missing a field or has one that is not valid",
    "invalid_client": "unknown client_id",
    "invalid_grant": (
        "the code or refresh token is unknown, expired, already used or another client's,"
        " or the code_verifier does not match"
    ),
    "unsupported_grant_type": "this client uses the authorization_code and refresh_token grants",
    "invalid_scope": f"the one scope is {SCOPE}",  # routes() says the current name
}
# What GET /authorize says when the SDK's handler refuses without a redirect (the client or
# the redirect URI could not be verified): one fixed text per error, and no `state`, since
# the answer goes to whoever opened the URL and not to the client.
_AUTHORIZE_REFUSALS = {
    "invalid_request": (
        "unknown client_id, a redirect_uri the client did not register,"
        " or a parameter that is missing or not valid"
    ),
    "unsupported_response_type": "response_type is code",
}
_ANY_URL = TypeAdapter(AnyUrl)
_GONE = (
    "This authorization request is not valid any more: it expired, it was already answered, "
    "or it was opened under another account. Go back to your agent and connect again."
)


def _clock() -> float:
    """Seconds that only ever count up. Nothing timed here outlives the process."""
    return time.monotonic()


# URLs: parsed, never pattern-matched


def _web_url(url: object) -> tuple[SplitResult, str, int | None] | None:
    """(parts, host, port) of an absolute http or https URL written the plain way, else None.

    Plain: printable ASCII with no space and no backslash (browsers read "\\" as "/" and drop
    tabs and newlines, so a URL holding one is not the URL it looks like), no userinfo, no
    fragment, and a host that is a DNS name of letter-digit-hyphen labels or an IP literal in
    its usual spelling. A name with a trailing dot, an empty label or a numeric last label
    (which a browser would read as an IPv4 address: "127.1", "0x7f.1") is refused."""
    if not isinstance(url, str) or any(not "!" <= char <= "~" or char == "\\" for char in url):
        return None
    if "#" in url:
        return None
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:  # an unbalanced "[", or a port that is not a number below 65536
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    if "@" in parts.netloc or parts.netloc.endswith(":"):
        return None
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
        return (parts, host, port) if "%" not in host else None  # no zone identifiers
    labels = host.split(".")
    if labels[-1].isdigit() or labels[-1].startswith("0x"):
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            return None
    elif len(host) > 253 or not all(_LABEL.fullmatch(label) for label in labels):
        return None
    return parts, host, port


def _origin(parts: SplitResult, host: str, port: int | None) -> str:
    """scheme://host[:port] as a browser writes an origin: lowercase, no default port."""
    name = f"[{host}]" if ":" in host else host
    default = {"http": 80, "https": 443}[parts.scheme]
    return f"{parts.scheme}://{name}" + ("" if port in (None, default) else f":{port}")


def _secure(parts: SplitResult, host: str) -> bool:
    """https anywhere, or http on this machine: exactly localhost, 127.0.0.1 or [::1]."""
    return parts.scheme == "https" or host in LOOPBACK


def issuer(public_url: str | None) -> str | None:
    """The issuer when OAuth is on, else None. It is on when `public_url` is the bare origin
    cairn is served at (no path: the OAuth endpoints live at the root), over https or over
    http on a loopback host."""
    parsed = _web_url(public_url)
    if parsed is None:
        return None
    parts, host, _ = parsed
    if not _secure(parts, host) or parts.path not in ("", "/") or "?" in str(public_url):
        return None
    return _origin(*parsed)


def redirect_uri_problem(uri: object) -> str | None:
    """Why a client may not register this redirect URI; None when it may. A code is only ever
    sent to https, or to http on the machine the browser runs on."""
    if not isinstance(uri, str):
        return "a redirect URI is a string"
    if not 1 <= len(uri) <= REDIRECT_URI_MAX:
        return f"a redirect URI has 1 to {REDIRECT_URI_MAX} characters"
    parsed = _web_url(uri)
    if parsed is None:
        return (
            "a redirect URI is an absolute https or http URL without userinfo, fragment, "
            "spaces or backslashes, and with a plain host name or IP address"
        )
    parts, host, _ = parsed
    if not _secure(parts, host):
        return "a redirect URI uses https, or http on localhost, 127.0.0.1 or [::1]"
    return None


def answer_uri(redirect_uri: str, **answer: str | None) -> str:
    """The redirect URI with the authorization answer appended. The registered URI is kept
    exactly as it is, its own query included: a client may check or sign that query, and
    rebuilding it (as the SDK's helper does) drops blank values."""
    given = urlencode({name: value for name, value in answer.items() if value is not None})
    if not given:
        return redirect_uri
    base, hash_mark, fragment = redirect_uri.partition("#")
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}{given}{hash_mark}{fragment}"


def origin_of(uri: str) -> str:
    """The origin of a redirect URI that passed `redirect_uri_problem`."""
    parsed = _web_url(uri)
    if parsed is None:
        raise ValueError("not a web URL")
    return _origin(*parsed)


def _same_resource(given: str, ours: str) -> bool:
    """Whether a client's `resource` names this server's /mcp: compared as URLs, so case,
    a default port and a trailing slash do not matter."""
    parsed = _web_url(given)
    if parsed is None or parsed[0].query:
        return False
    return _origin(*parsed) + parsed[0].path.rstrip("/") == ours


# Registration: unauthenticated, so bounded


class RateLimit:
    """At most `limit` allowed requests per client address in any `window` seconds, counted in
    memory. One IPv6 /64 is one address (it is one subscriber), and requests without a usable
    address share one count. No more than `max_addresses` are tracked: when the table is full,
    the address forgotten is one not at its limit, the one seen longest ago of those, and the
    new one is admitted; an address being refused is forgotten only when every address is,
    and then the one seen longest ago. So a flood from many addresses neither grows the table,
    nor frees the addresses it holds back, nor shuts out an address only because the table is
    full. The price is that a forgotten address starts a fresh count. A call is O(1), except
    that a new address at a full table reads, oldest first, until it finds one not at its
    limit: at most `max_addresses` entries. Called on the event loop only."""

    def __init__(
        self,
        limit: int,
        window: float,
        clock: Callable[[], float] | None = None,
        max_addresses: int = 10_000,
    ) -> None:
        self._limit, self._window, self._max = limit, window, max_addresses
        self._clock = clock or _clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()  # seen longest ago first

    @property
    def tracked(self) -> int:
        return len(self._hits)

    def _over(self, hits: deque[float], now: float) -> bool:
        """Whether an address with `hits` is at its limit at `now` (it would be refused).
        A count holds at most `limit` times, oldest first; a limit below 1 refuses all."""
        if self._limit < 1:
            return True
        return len(hits) >= self._limit and hits[-self._limit] > now - self._window

    def _victim(self, now: float) -> str:
        """The address to forget: the one seen longest ago that is not at its limit, else the
        one seen longest ago."""
        for address, hits in self._hits.items():  # seen longest ago first
            if not self._over(hits, now):
                return address
        return next(iter(self._hits))

    def allow(self, address: str | None) -> bool:
        now, key = self._clock(), _network(address)
        hits = self._hits.get(key)
        if hits is None:
            while len(self._hits) >= self._max:
                del self._hits[self._victim(now)]
            hits = self._hits[key] = deque()
        else:
            self._hits.move_to_end(key)
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True


def _network(address: str | None) -> str:
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return "unknown"
    if ip.version == 4:
        return str(ip)
    if ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return str(ipaddress.IPv6Address(int(ip) >> 64 << 64)) + "/64"


def _client_name(given: str | None) -> str:
    """A client's name as cairn keeps and shows it: control and format characters removed
    (they can reorder or hide text), whitespace collapsed, cut to the store's limit. A name
    is the client's own claim, which is why the consent page shows the redirect URI too."""
    spaced = " ".join((given or "").split())  # a newline or a tab is a space, not nothing
    visible = "".join(c for c in spaced if unicodedata.category(c) not in ("Cc", "Cf"))
    return " ".join(visible.split())[:CLIENT_NAME_MAX].rstrip() or UNNAMED


class _Client(OAuthClientInformationFull):
    """A registered client as the SDK's handlers see it."""

    def validate_scope(self, requested_scope: str | None) -> list[str] | None:
        """One scope, whatever was asked for: see the module docstring. `scope` is the
        server's current name for it (_record)."""
        return [self.scope or SCOPE]


def _record(client: OAuthClient, scope: str = SCOPE) -> _Client:
    return _Client(
        client_id=client.id,
        client_name=client.name,
        redirect_uris=list(client.redirect_uris),
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=scope,
        client_id_issued_at=int(datetime.fromisoformat(client.created_at).timestamp()),
    )


# The request between /authorize and the decision

log = logging.getLogger("cairn.oauth")


class SealedRequests:
    """Authorization requests carried by the browser instead of stored: `seal` signs the
    fields with this process's secret, `open` returns them (plus "id", "exp" and "step")
    only for an intact, unexpired value sealed for the same step. `reseal` moves an opened
    request to another step under the same id and deadline. `settle` is True once per id.

    The settled ids are bounded: at most `limit` (SETTLED_MAX) at once, each forgotten at
    its deadline, in deadline order from a heap, so a settlement costs O(log n) and never a
    scan of the table. When the table is full, the id nearest its deadline is dropped to
    make room, and a warning is logged at most once a minute: anyone can start a sign-in,
    so a flood must not make sign-in unavailable or ever more expensive. The price is that
    a dropped id could be settled once more before its deadline. Where the table is used,
    that buys nothing:
    - Sign-in (cairn.web.signin): settling a state again needs the browser's own state
      cookie (HttpOnly; Path=/auth, or Path=/ as `__Host-signin` under a brand on https),
      and then lets that browser finish a sign-in once more: with a code the provider
      already redeemed, which it refuses, or with a fresh code from going round the
      provider again under the same state, which is that browser's own sign-in, one it
      could start anew anyway.
    - Consent (POST /oauth/consent): settling a request again needs the person's own
      session and a same-origin form post of the page they were shown, and can only send
      the client a second answer that person chose: a denial, or another authorization
      code for the same client, redirect URI and PKCE challenge (with the reach ticked
      this time), which only the holder of the verifier can redeem. That is what the
      person could do by connecting the agent again."""

    def __init__(self, limit: int = SETTLED_MAX) -> None:
        self._secret = secrets.token_bytes(32)
        self._limit = limit
        self._settled: dict[str, float] = {}  # request id -> its deadline
        self._deadlines: list[tuple[float, str]] = []  # a heap of (deadline, request id)
        self._warned: float | None = None  # when the table was last said to be full
        self._lock = threading.Lock()

    def seal(self, fields: dict[str, Any], step: str, ttl: float) -> str:
        request = {**fields, "id": secrets.token_urlsafe(16), "exp": _clock() + ttl}
        return self.reseal(request, step)

    def reseal(self, request: dict[str, Any], step: str, **more: Any) -> str:
        body = _b64(json.dumps({**request, **more, "step": step}).encode())
        return f"{body}.{self._signature(body)}"

    def open(self, sealed: object, step: str) -> dict[str, Any] | None:
        if not isinstance(sealed, str):
            return None
        body, dot, signature = sealed.partition(".")
        expected = self._signature(body)
        if not dot or not hmac.compare_digest(signature.encode(), expected.encode()):
            return None
        request = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if request["step"] != step or request["exp"] <= _clock():
            return None
        return request

    def settle(self, request: dict[str, Any]) -> bool:
        """True the first time `request` (an opened one: its "id" and "exp") is settled
        before its deadline; False again until then. An id is added once, so the heap and
        the dict always hold the same ids."""
        now = _clock()
        with self._lock:
            while self._deadlines and self._deadlines[0][0] <= now:
                del self._settled[heapq.heappop(self._deadlines)[1]]
            if request["id"] in self._settled:
                return False
            if len(self._settled) >= self._limit:
                del self._settled[heapq.heappop(self._deadlines)[1]]
                if self._warned is None or now - self._warned >= SETTLED_WARN_EVERY:
                    self._warned = now
                    log.warning(
                        "%d settled sign-in or consent requests are held, the most kept:"
                        " the one nearest its deadline was dropped to make room",
                        self._limit,
                    )
            self._settled[request["id"]] = request["exp"]
            heapq.heappush(self._deadlines, (request["exp"], request["id"]))
            return True

    @property
    def settled(self) -> int:
        return len(self._settled)

    def _signature(self, body: str) -> str:
        return _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# The provider behind the SDK's handlers


@dataclass(frozen=True)
class Ledger:
    """The store's operations on clients, codes and tokens that belong to no account: what
    the OAuth endpoints work with before anyone is signed in. They get these and nothing
    else of the store (cairn.server's `Accounts` makes it)."""

    register_client: Callable[[str, Sequence[str]], OAuthClient]
    get_client: Callable[[str], OAuthClient | None]
    find_code: Callable[[str], CodeInfo | None]
    # (code, client id, audience=): see Store.redeem_code
    redeem_code: Callable[..., TokenPair | None]
    refresh_token_client: Callable[[str], str | None]
    # (token, client id, audience=): see Store.rotate_refresh_token; may raise
    # TooManyRotations
    rotate_refresh_token: Callable[..., TokenPair | None]
    revoke_token: Callable[[str, str], bool]


class Provider:
    """The part of the SDK's OAuthAuthorizationServerProvider protocol that its authorization
    and token handlers call. Expiry and the rotation rate are the store's to judge, on the
    store's clock. Every redemption and
    rotation names `resource` as the audience: a code or grant bound to another is unknown."""

    def __init__(
        self, ledger: Ledger, requests: SealedRequests, resource: str, scope: str = SCOPE
    ) -> None:
        self._ledger, self._requests, self._resource = ledger, requests, resource
        self._scope = scope  # the one scope's current name (B1); the other is taken too
        self._names = [scope, *(name for name in (SCOPE, BRANDED_SCOPE) if name != scope)]

    async def get_client(self, client_id: str) -> _Client | None:
        client = await run_in_threadpool(self._ledger.get_client, client_id)
        return _record(client, self._scope) if client else None

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """The request is valid (the SDK checked the client, the redirect URI and PKCE's
        method): seal it and send the browser to the consent page. Nothing is written."""
        if params.resource is not None and not _same_resource(params.resource, self._resource):
            raise AuthorizeError("invalid_target", _ELSEWHERE)
        if not _CHALLENGE.fullmatch(params.code_challenge):
            raise AuthorizeError(
                "invalid_request", "code_challenge is not a base64url SHA-256 digest"
            )
        fields = {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "explicit": params.redirect_uri_provided_explicitly,
            "challenge": params.code_challenge,
            "state": params.state,
        }
        sealed = self._requests.seal(fields, "authorize", ttl=CONSENT_TTL)
        return f"{CONSENT_PATH}?{urlencode({'request': sealed})}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        found = await run_in_threadpool(self._ledger.find_code, authorization_code)
        if found is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=[self._scope],
            expires_at=found.expires_at.timestamp(),
            client_id=found.client_id,
            code_challenge=found.code_challenge,
            redirect_uri=AnyUrl(found.redirect_uri),
            redirect_uri_provided_explicitly=found.redirect_uri_explicit,
            resource=self._resource,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        redeem = functools.partial(self._ledger.redeem_code, audience=self._resource)
        pair = await run_in_threadpool(redeem, authorization_code.code, client.client_id)
        if pair is None:
            raise TokenError("invalid_grant", "authorization code does not exist")
        return _token_response(pair, self._scope)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        """Also a refresh token that was already rotated: only the exchange may find that
        out, because finding it ends the grant. Its scopes are both names of the one scope
        (B1), so a refresh asking for either is taken, under a brand or not."""
        owner = await run_in_threadpool(self._ledger.refresh_token_client, refresh_token)
        if owner is None:
            return None
        return RefreshToken(
            token=refresh_token, client_id=owner, scopes=self._names, resource=self._resource
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        """TooManyRotations (the store's): the grant is over its rate, and nothing was
        written. The SDK's token handler turns a TokenError into a 400, so this is not one:
        cairn's wrapper answers 429."""
        rotate = functools.partial(self._ledger.rotate_refresh_token, audience=self._resource)
        token, client_id = refresh_token.token, client.client_id
        pair = await run_in_threadpool(rotate, token, client_id)
        if pair is None:
            raise TokenError("invalid_grant", "refresh token does not exist")
        return _token_response(pair, self._scope)


def _token_response(pair: TokenPair, scope: str) -> OAuthToken:
    return OAuthToken(
        access_token=pair.access_token,
        expires_in=pair.expires_in,
        scope=scope,
        refresh_token=pair.refresh_token,
    )


# Routes


def challenge(issuer_url: str, *, refused: bool) -> str:
    """The WWW-Authenticate value of a 401 from /mcp: where the resource's metadata is, and
    `invalid_token` when a credential was presented and refused (RFC 6750, section 3)."""
    error = 'error="invalid_token", ' if refused else ""
    return f'Bearer {error}resource_metadata="{issuer_url}{RESOURCE_METADATA_PATH}"'


def _refused(status: int, error: str, description: str, **headers: str) -> Response:
    body = {"error": error, "error_description": description}
    return JSONResponse(body, status_code=status, headers={"cache-control": "no-store", **headers})


def _repeated(params: QueryParams | FormData) -> bool:
    """Whether a parameter other than `resource` is named twice (RFC 6749, 3.1). `params` is
    the request's own parse, the one the SDK's handler reads after cairn."""
    names = [name for name, _ in params.multi_items() if name not in REPEATABLE]
    return len(names) != len(set(names))


def _well_formed_verifier(value: object) -> bool:
    return isinstance(value, str) and _VERIFIER.fullmatch(value) is not None


def _reworded(answer: Response, texts: dict[str, str], otherwise: str, **headers: str) -> Response:
    """An SDK handler's answer, a refusal rebuilt with cairn's description for its error
    (`texts`, else `otherwise`): its status and error code are kept, and nothing else of
    its body (no `state`, no text the client sent). Any other answer is passed on."""
    if answer.status_code < 400:
        return answer
    try:
        error = json.loads(answer.body)["error"]
    except (ValueError, KeyError, TypeError):
        return answer
    if not isinstance(error, str):
        return answer
    return _refused(answer.status_code, error, texts.get(error, otherwise), **headers)


def _with_form_action(policy: str, origin: str) -> str:
    """The page's policy with one change: its form may also be followed to `origin`. Browsers
    apply form-action to the redirect that answers a form post."""
    kept = [d.strip() for d in policy.split(";") if d.strip().split()[:1] != ["form-action"]]
    return "; ".join([*kept, f"form-action 'self' {origin}"])


def _reach_ids(view: Store) -> set[str]:
    """The ids a reach chosen on the consent page may name: the projects and sets it listed."""
    return {project.id for project in view.list_projects()} | {s.id for s in view.list_sets()}


def routes(
    issuer_url: str,
    ledger: Ledger,
    page: Callable[..., Response],
    account: Callable[[Request], Store],
    registrations: int = REGISTRATIONS,
    *,
    scope: str = SCOPE,
    resource_name: str = RESOURCE_NAME,
    server: str = "cairn",
) -> list[Route]:
    """Every route OAuth adds. `page(request, template, status_code, **context)` renders a
    UI page; `account(request)` is the signed-in caller's view of the store.
    `registrations`: how many clients one address may register in any hour (REGISTRATIONS
    unless the operator said). B1, what the server calls itself, each today's by default:
    `scope`, the one scope's current name (SCOPE or BRANDED_SCOPE), which every answer
    says; `resource_name`, the protected resource's name in its metadata; `server`, how a
    refusal sentence names the server."""
    if scope not in (SCOPE, BRANDED_SCOPE):
        raise ValueError(f"the one scope is named {SCOPE} or {BRANDED_SCOPE}")
    token_refusals = {**_TOKEN_REFUSALS, "invalid_scope": f"the one scope is {scope}"}
    resource = issuer_url + MCP_PATH
    settings = AuthSettings(  # the SDK's URL types, which keep an issuer free of a final "/"
        issuer_url=issuer_url, resource_server_url=resource, validate_token_resource=True
    )
    assert settings.resource_server_url is not None
    requests = SealedRequests()
    provider = Provider(ledger, requests, resource, scope)
    limit = RateLimit(registrations, REGISTRATION_WINDOW)
    authorizations = AuthorizationHandler(provider)
    tokens = TokenHandler(provider, ClientAuthenticator(provider))

    metadata = build_metadata(
        settings.issuer_url,
        None,
        ClientRegistrationOptions(enabled=True, valid_scopes=[scope], default_scopes=[scope]),
        RevocationOptions(enabled=True),
    )
    metadata.token_endpoint_auth_methods_supported = ["none"]
    metadata.revocation_endpoint_auth_methods_supported = ["none"]

    async def register(request: Request) -> Response:
        try:
            data = json.loads(await request.body())
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return _refused(400, "invalid_client_metadata", "the body must be a JSON object")
        uris = data.get("redirect_uris")
        if not isinstance(uris, list) or not 1 <= len(uris) <= REDIRECT_URIS_MAX:
            wanted = f"redirect_uris is a list of 1 to {REDIRECT_URIS_MAX} URIs"
            return _refused(400, "invalid_redirect_uri", wanted)
        try:
            asked = OAuthClientMetadata.model_validate(data)
            kept = [str(_ANY_URL.validate_python(uri)) for uri in uris if isinstance(uri, str)]
        except ValidationError as error:
            fields = ", ".join(sorted({str(e["loc"][0]) for e in error.errors() if e["loc"]}))
            return _refused(400, "invalid_client_metadata", f"not valid: {fields or 'the body'}")
        # `kept` is each URI as the SDK reads one in an authorization request, which is what
        # cairn stores, matches and redirects to: "http://127.0.0.1:1234" is kept with its
        # "/" (one URL, RFC 3986 6.2.3), or the request naming it would never match. The
        # policy holds for a URI as the client wrote it and as it is kept.
        if problem := next(filter(None, map(redirect_uri_problem, [*uris, *kept])), None):
            return _refused(400, "invalid_redirect_uri", problem)
        if "authorization_code" not in asked.grant_types or "code" not in asked.response_types:
            wanted = f"{server} only offers the authorization code grant"
            return _refused(400, "invalid_client_metadata", wanted)
        if not limit.allow(request.client.host if request.client else None):
            wait = {"retry-after": str(REGISTRATION_WINDOW)}
            return _refused(429, "too_many_requests", "too many registrations", **wait)
        try:
            name = _client_name(asked.client_name)
            client = await run_in_threadpool(ledger.register_client, name, kept)
        except ValueError as error:
            return _refused(400, "invalid_client_metadata", str(error))
        record = _record(client, scope).model_dump_json(exclude_none=True)
        headers = {"cache-control": "no-store"}
        return Response(record, 201, headers=headers, media_type="application/json")

    async def authorization(request: Request) -> Response:
        """The SDK's authorization handler, after cairn's checks of the query, which the
        handler then reads as the same parsed object: a parameter named twice (other than
        `resource`) is 400 `invalid_request`, and several `resource` values must all name
        this server's /mcp, else 400 `invalid_target`. Neither redirects: the redirect URI
        has not been checked yet. One `resource` is the provider's to check, as before.
        The handler's refusals that are not redirects get cairn's description
        (_AUTHORIZE_REFUSALS) and no `state`; one redirected to the verified redirect URI
        carries `error` and `state` as the SDK made it."""
        query = request.query_params
        if _repeated(query):
            return _refused(400, "invalid_request", _ONCE)
        named = query.getlist("resource")
        if len(named) > 1 and not all(_same_resource(r, resource) for r in named):
            return _refused(400, "invalid_target", _ELSEWHERE)
        refused = "the authorization request was refused"
        return _reworded(await authorizations.handle(request), _AUTHORIZE_REFUSALS, refused)

    async def token(request: Request) -> Response:
        """The SDK's token handler, after cairn's checks, in this order, each a 400 before
        anything is read from the store or written: a field named twice (other than
        `resource`) is `invalid_request`; so is an authorization_code request whose
        `code_verifier` is not 43 to 128 unreserved characters (RFC 7636, 4.1); and every
        `resource` must name this server's /mcp (RFC 8707), else `invalid_target`. The
        handler reads the same parsed form. Its refusals keep their status and error code
        and get cairn's description (_TOKEN_REFUSALS); a grant that refreshes too often is
        429."""
        form = await request.form()
        if _repeated(form):
            return _refused(400, "invalid_request", _ONCE, pragma="no-cache")
        verifier = form.get("code_verifier")
        if form.get("grant_type") == "authorization_code" and not _well_formed_verifier(verifier):
            return _refused(400, "invalid_request", _VERIFIER_FORM, pragma="no-cache")
        named = form.getlist("resource")
        if any(not isinstance(r, str) or not _same_resource(r, resource) for r in named):
            return _refused(400, "invalid_target", _ELSEWHERE, pragma="no-cache")
        try:
            answer = await tokens.handle(request)
        except TooManyRotations:
            wait = {"retry-after": str(ROTATION_WINDOW)}
            return _refused(429, "too_many_requests", "too many refreshes for this grant", **wait)
        refused = "the token request was refused"
        return _reworded(answer, token_refusals, refused, pragma="no-cache")

    async def revoke(request: Request) -> Response:
        """RFC 7009 for public clients: `token` and `client_id`, no secret. Any token of a
        grant ends the grant; an unknown token is not an error. A field named twice is 400
        `invalid_request`, and nothing is revoked."""
        form = await request.form()
        if _repeated(form):
            return _refused(400, "invalid_request", _ONCE)
        token, client_id = form.get("token"), form.get("client_id")
        if not isinstance(token, str) or not token or not isinstance(client_id, str):
            return _refused(400, "invalid_request", "token and client_id are required")
        if await run_in_threadpool(ledger.get_client, client_id) is None:
            return _refused(401, "invalid_client", "unknown client_id")
        await run_in_threadpool(ledger.revoke_token, token, client_id)
        return Response(status_code=200, headers={"cache-control": "no-store"})

    async def consent_page(request: Request) -> Response:
        view = account(request)
        pending = requests.open(request.query_params.get("request"), "authorize")
        client = (
            await run_in_threadpool(ledger.get_client, pending["client_id"]) if pending else None
        )
        who = await run_in_threadpool(view.get_account, view.account_id) if client else None
        if pending is None or client is None or who is None:
            return page(request, "error.html", 400, status=400, message=_GONE)
        person = await run_in_threadpool(view.get_user, view.user_id) if view.user_id else None
        projects = await run_in_threadpool(view.list_projects)
        sets = await run_in_threadpool(view.list_sets)
        names = {item.id: item.name for item in (*projects, *sets)}
        response = page(
            request,
            "consent.html",
            client_name=client.name,
            redirect_uri=pending["redirect_uri"],
            account_name=who.name,
            person=person,
            request=requests.reseal(pending, "consent", account=view.account_id),
            projects=projects,
            sets=sets,
            names=names,
        )
        policy = response.headers["content-security-policy"]
        origin = origin_of(pending["redirect_uri"])
        response.headers["content-security-policy"] = _with_form_action(policy, origin)
        return response

    async def decide(request: Request) -> Response:
        view = account(request)
        form = await request.form()
        pending, decision = requests.open(form.get("request"), "consent"), form.get("decision")
        if pending is None or pending["account"] != view.account_id:
            return page(request, "error.html", 400, status=400, message=_GONE)
        if decision not in ("approve", "deny"):
            return page(request, "error.html", 400, status=400, message="Choose Approve or Deny.")
        projects, access = None, "write"
        if decision == "approve":
            # The person's choice of reach is checked against the workspace before the request
            # is settled, so a wrong choice can be corrected on the same page.
            try:
                projects, access = choose_reach(
                    form.get("scope"), form.getlist("projects"), form.get("access")
                )
            except BadRequest as error:
                return page(request, "error.html", 400, status=400, message=str(error))
            known = await run_in_threadpool(_reach_ids, view)
            if projects is not None and not set(projects) <= known:
                unknown = "Selected projects: tick only projects and sets of this workspace."
                return page(request, "error.html", 400, status=400, message=unknown)
        if not requests.settle(pending):
            return page(request, "error.html", 400, status=400, message=_GONE)
        answer: dict[str, str | None] = {"error": "access_denied"}
        if decision == "approve":
            create = functools.partial(
                view.create_code,
                pending["client_id"],
                pending["redirect_uri"],
                pending["challenge"],
                redirect_uri_explicit=pending["explicit"],
                projects=projects,
                access=access,
                audience=resource,
            )
            try:
                answer = {"code": await run_in_threadpool(create)}
            except KeyError:  # the client or the account was removed meanwhile
                return page(request, "error.html", 400, status=400, message=_GONE)
            except ValueError:  # a ticked project or set was removed meanwhile
                return page(request, "error.html", 400, status=400, message=_GONE)
        target = answer_uri(pending["redirect_uri"], **answer, state=pending["state"])
        return RedirectResponse(target, status_code=303, headers={"cache-control": "no-store"})

    def machine(path: str, endpoint: Callable[..., Any], method: str = "POST") -> Route:
        """An endpoint clients call from anywhere, browsers included: CORS, a small body."""
        methods = [method, "OPTIONS"]
        cors = Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=methods,
            allow_headers=[MCP_PROTOCOL_VERSION_HEADER],
        )
        small = Middleware(RequestBodyLimitMiddleware, max_body_size=MAX_BODY)
        return Route(path, endpoint, methods=methods, middleware=[cors, small])

    return [
        machine(METADATA_PATH, MetadataHandler(metadata).handle, "GET"),
        *create_protected_resource_routes(
            resource_url=settings.resource_server_url,
            authorization_servers=[settings.issuer_url],
            scopes_supported=[scope],
            resource_name=resource_name,
        ),
        Route("/authorize", authorization, methods=["GET"]),
        machine("/token", token),
        machine("/register", register),
        machine("/revoke", revoke),
        Route(CONSENT_PATH, consent_page, methods=["GET"]),
        Route(CONSENT_PATH, decide, methods=["POST"]),
    ]
