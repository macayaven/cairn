"""Who a request acts for: the Auth middleware, Accounts (the only holder of the unscoped
store), the refusal a request without a credential gets, the rate limit of a workspace's
requests, the cross-origin check and the transport security of /mcp."""

import contextlib
import dataclasses
import functools
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cairn import oauth
from cairn.compiler import Gate
from cairn.guard import Guard
from cairn.identity import Profile
from cairn.memory import DENIED, Memory, access_log, record_access, unavailable
from cairn.plans import Plans
from cairn.sources.runner import sync_forever
from cairn.store import (
    ACCESS_TOKEN_PREFIX,
    CannotWrite,
    Principal,
    SignedIn,
    Store,
    StoreUnusable,
    User,
    WaitingNote,
)
from cairn.web.brand import Brand
from cairn.web.ratelimit import TOO_MANY_FAILURES, CredentialFailures, RateLimiter
from cairn.web.templating import BRAND, NO_PAGE, PATH, _page, brand_of
from cairn.web.turns import SCOPE_KEY, Compiles

SESSION_COOKIE = "cairn_session"
BRANDED_SESSION_COOKIE = "session"  # its name under a brand (B1): no cookie names cairn
HOST_ONLY = "__Host-"  # a browser takes such a cookie only Secure, at Path=/, with no Domain
# Every name the session cookie has had: signing out ends and clears each one a browser sends.
SESSION_COOKIES = (SESSION_COOKIE, BRANDED_SESSION_COOKIE, HOST_ONLY + BRANDED_SESSION_COOKIE)
MEMORY = "cairn.memory"  # the ASGI scope key under which Auth puts the caller's Memory
COMPILES = SCOPE_KEY  # ... and the server's turns of compiles (G1; cairn.web.turns)
WALLED = "cairn.walled"  # ... and whether the wall answers for the address (B1)
SESSION_TTL = 43_200  # seconds; the cookie and the stored session expire together
RENEW_AFTER = 86_400  # seconds after its last renewal that a use renews a session (B1)
PUBLIC_PATHS = frozenset({"/healthz", "/login"}) | oauth.PUBLIC_PATHS
SIGN_IN_PREFIX = "/auth/"  # /auth/<provider>[/callback]: how a person gets a session
SHARE_PATH = "/new"  # the web app's share target (cairn.web.compose)
JOIN_PREFIX = "/join/"  # /join/<token>: an invitation, taken after signing in
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
RATE_LIMITED = "rate_limited"  # the action of a request refused for its workspace's rate


def branded_cookie(name: str, secure: bool) -> str:
    """The name of a cookie under a brand (B1): `name` with the __Host- prefix when the site
    is https (`secure`), else `name` as it is (a browser refuses the prefix without Secure).

    Why the prefix: another host under the same parent domain can set a cookie of a plain
    name for the whole parent domain, and the browser sends it along with this server's own,
    so a planted `session` would put a person in the planter's workspace and a planted
    `signin` would sign them in as the planter. No host can set a __Host- cookie for another
    host, and the server reads only the name it sets. The session cookie and the sign-in
    state cookie (cairn.web.signin) both take their names from here.

    Without a brand the names stay `cairn_session` and `cairn_signin`, with no prefix: frozen
    tests pin them, and every byte without the settings is today's. Over plain http the
    branded names carry no prefix either; there a planted cookie can still shadow them."""
    return f"{HOST_ONLY}{name}" if secure else name


@dataclass(frozen=True)
class Sessions:
    """How this app keeps the web session a person gets by signing in: the cookie's name, how
    long a session lasts, and whether use renews it. create_app makes one and hands it to
    Accounts, which every route that sets or clears the cookie already reaches. B1: `serve
    --session-days N` makes `seconds` N days and `renews` true; a brand gives the cookie its
    neutral name, branded_cookie(BRANDED_SESSION_COOKIE): `__Host-session` on https, else
    `session`. Without them it is today's: cairn_session, SESSION_TTL (12 hours), never
    renewed. A session opened with an API key (POST /login, only without sign-in, so never
    with --session-days) keeps SESSION_TTL. Only a cookie of this name is ever read; signing
    out also ends and clears each other name in SESSION_COOKIES that the browser sends.

    The cookie is HttpOnly, SameSite=Strict, Path=/, Secure when `secure` (public_url is
    https), and its Max-Age is the stored session's life whenever it is sent: at sign-in, on
    switching workspace and on taking an invitation, and on renewal (`resent`)."""

    cookie: str = SESSION_COOKIE
    seconds: int = SESSION_TTL
    renews: bool = False
    secure: bool = False

    def set(self, response: Response, token: str, seconds: int | None = None) -> None:
        response.set_cookie(
            self.cookie,
            token,
            max_age=self.seconds if seconds is None else seconds,
            path="/",
            secure=self.secure,
            httponly=True,
            samesite="strict",
        )

    def clear(self, response: Response, name: str | None = None) -> None:
        """Clear the cookie, or the one named `name` (one of SESSION_COOKIES)."""
        name = self.cookie if name is None else name
        secure = self.secure or name.startswith(HOST_ONLY)  # the prefix's rule
        response.delete_cookie(name, path="/", secure=secure, httponly=True, samesite="strict")

    def resent(self, send: Send, token: str) -> Send:
        """`send`, with the cookie of the renewed session `token` added to the response,
        unless the response sets that cookie itself (signing out clears it; switching
        workspace sets another session's)."""
        made = Response()
        self.set(made, token)
        header = next(value for name, value in made.raw_headers if name == b"set-cookie")
        own = f"{self.cookie}=".encode("latin-1")

        async def sending(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(n.lower() == b"set-cookie" and v.startswith(own) for n, v in headers):
                    message = {**message, "headers": [*headers, (b"set-cookie", header)]}
            await send(message)

        return sending


def _is_api(path: str) -> bool:
    return path in ("/api", "/mcp") or path.startswith(("/api/", "/mcp/"))


def machine_path(path: str, open_paths: frozenset[str] = frozenset()) -> bool:
    """An address machines use (B1: the wall leaves it as it is): /api and /mcp, /healthz,
    the OAuth endpoints, `open_paths` and any /.well-known/ address (Auth.machine narrows
    the last to the ones a route serves)."""
    return (
        _is_api(path)
        or path == "/healthz"
        or path in oauth.PUBLIC_PATHS
        or path in open_paths
        or path.startswith(oauth.WELL_KNOWN)
    )


def _noindex(send: Send) -> Send:
    """`send`, with `X-Robots-Tag: noindex` on the response (B1: behind the wall, nothing
    a visitor can reach is for search engines)."""

    async def sending(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = [*message.get("headers", []), (b"x-robots-tag", b"noindex")]
            message = {**message, "headers": headers}
        await send(message)

    return sending


def _no_store(send: Send) -> Send:
    """`send`, with `Cache-Control: no-store` as the response's only Cache-Control (H1): what
    /api and /mcp answer is one caller's, refused or not, and no cache between keeps it."""

    async def sending(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = [
                (name, value)
                for name, value in message.get("headers", [])
                if name.lower() != b"cache-control"
            ]
            message = {**message, "headers": [*headers, (b"cache-control", b"no-store")]}
        await send(message)

    return sending


def public_page(path: str) -> bool:
    """A page anyone can read, signed in or not: the guides."""
    return path == "/docs" or path.startswith("/docs/")


def _is_mcp(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


def _bearer(headers: Headers) -> str:
    scheme, _, credentials = headers.get("authorization", "").partition(" ")
    return credentials.strip() if scheme.lower() == "bearer" else ""


def _presented_key(headers: Headers) -> str:
    """The API key from `x-api-key`, else from `Authorization: Bearer <key>`."""
    return headers.get("x-api-key", "").strip() or _bearer(headers)


# A query string that can go into a page as it is: what a sealed consent request is made of.
_PLAIN_QUERY = re.compile(r"[A-Za-z0-9._~%=&+-]+")


def _refusal(request: HTTPConnection, issuer: str | None = None) -> Response:
    """What a request without a valid credential gets. /api and /mcp: a JSON 401, which on
    /mcp also says where OAuth starts when it is on (`issuer`). The web UI: the way to the
    login form. Only the consent page, the share target (/new, whose query holds what was
    shared) and an invitation (/join/<token>) are worth coming back to after signing in, so
    only they travel along as `next`;
    and when another site sent the browser there, the session cookie (SameSite=Strict) was
    left behind, so the page first asks the browser to come again by itself."""
    path = request.scope["path"]
    if _is_api(path):
        error = {"error": "a valid API key is required: x-api-key or Authorization: Bearer"}
        headers = {}
        if issuer and _is_mcp(path):
            error = {"error": "a valid API key or OAuth access token is required"}
            presented = bool(_presented_key(request.headers))
            headers["www-authenticate"] = oauth.challenge(issuer, refused=presented)
        return JSONResponse(error, status_code=401, headers=headers)
    query = request.scope["query_string"].decode("latin-1")
    comes_back = (
        path == SHARE_PATH
        or path.startswith(JOIN_PREFIX)
        or (issuer and path == oauth.CONSENT_PATH)
    )
    if comes_back and request.scope["method"] in ("GET", "HEAD"):
        target = f"{path}?{query}" if query else path
        if request.headers.get("sec-fetch-site") == "cross-site" and _PLAIN_QUERY.fullmatch(query):
            brand = brand_of(request)  # under a brand, a page of its own that names nothing
            return _page("brand_retry.html" if brand else "retry.html", brand=brand, url=target)
        return RedirectResponse(f"/login?{urlencode({'next': target})}", status_code=303)
    return RedirectResponse("/login", status_code=303)


def _too_fast(request: HTTPConnection, memory: Memory, wait: int, limit: int) -> Response:
    """What a request over its workspace's rate gets: 429 with Retry-After, before any route
    runs. On /api and /mcp it is one access record, under its caller, with no project."""
    message = (
        f"This workspace has made {limit} requests in the last minute, as many as its plan"
        f" allows: try again in {wait} s."
    )
    headers = {"retry-after": str(wait)}
    if _is_api(request.scope["path"]):
        kind, actor = memory.store.actor
        record_access(kind, actor, memory.store.actor_id, RATE_LIMITED, None, 429)
        return JSONResponse({"error": message}, status_code=429, headers=headers)
    page = _page(
        "error.html",
        status_code=429,
        brand=brand_of(request),
        person=memory.person,
        signed_in=True,
        status=429,
        message=message,
    )
    page.headers.update(headers)
    return page


def _too_many_failures(wait: int) -> Response:
    """What /api and /mcp answer an address over its refused credentials (H1): 429, the
    fixed sentence, and Retry-After."""
    return JSONResponse(
        {"error": TOO_MANY_FAILURES}, status_code=429, headers={"retry-after": str(wait)}
    )


class Auth:
    """Default deny: public paths pass, /api needs an API key, /mcp an API key or an OAuth
    access token, and everything else is the web UI and needs a session. Websockets are
    refused (cairn serves none). An admitted request carries its account's Memory in
    scope[MEMORY], which is all a handler gets.

    The OAuth endpoints authenticate nobody by themselves (they are how a client gets a
    token), so they pass, and so does every /.well-known path: when OAuth is off the router
    has no such route and answers 404. /oauth/consent is not one of them: it is a UI page.
    The sign-in routes under /auth/ pass for the same reason, and are 404 without sign-in.
    With `landing` (a function that renders it), GET / without a session is the public
    landing page instead of a redirect to /login; it is rendered here, and no handler runs
    without an account.

    The public pages (`public_page`: /docs and /docs/<name>) need no session, and
    a request that carries one is admitted as any other, so the page can keep the app's
    navigation for a signed-in reader; one without a session reaches the route with no
    account, and those handlers read the caller with web.public.viewer, never _memory.

    With `limiter`, an admitted request whose workspace's plan has `requests_per_minute`
    is counted against it (cairn.web.ratelimit, in memory): past it, 429 with Retry-After
    and no route runs. Requests without a credential and the public paths are not counted.

    `open_paths`: exact paths that pass with no credential, no rate count and no
    cross-origin check, for any method; the route checks the request itself (the address
    Google sends a browser back to after connecting an account, web/google_connect.py).

    H1: every answer on /api and /mcp carries `Cache-Control: no-store` and no other
    Cache-Control (`_no_store`), set here, before anything answers: a refusal, a 429, the MCP
    transport's own 421 and every route's answer alike. Only an error no handler turns into
    an answer (Starlette's own 500, outside this middleware) goes without it.

    H1, `failures` (cairn.web.ratelimit.CredentialFailures; create_app always gives one):
    on /api and /mcp a credential is looked up as always, and one refused counts against the
    client's address; one accepted never counts, and a request with none counts nothing. A
    correct credential is admitted from any address, over the limit or not. From an address
    over the limit, a request whose credential was refused, or that carries none where one
    is required, is answered 429 (TOO_MANY_FAILURES, Retry-After) in place of 401; one with
    no credential to /mcp is answered as before (401 and where OAuth starts). The 429 is one
    access record with no actor and no project, as a 401 is.

    `routed(scope)`: whether a route serves the path, for any method. Outside /api and /mcp,
    a path no route serves is 404 for everyone, signed in or not: it is no page, so there is
    nothing to sign in for. The credential is still read, only so that a signed-in reader
    keeps the app's navigation on the error page. None: every path is routed.

    B1, `brand` (cairn.web.brand.Brand, None: none): put on every request's scope
    (templating.BRAND), where each page reads it, so the frame names the brand and not
    cairn. Under a brand a visitor (a request with no session) is shown nothing that names
    the software: no landing page and no public page (they get the way to the sign-in page,
    as any other page), and the come-again page is a page of its own.

    B1, `wall`: a browser that is not signed in is shown the sign-in page and nothing else.
    It is decided here, in one place, by what an address is, never by a list of pages:
    - What machines use answers exactly as without the wall (`machine`): /api and /mcp,
      /healthz, the OAuth endpoints and the /.well-known/ addresses a route serves,
      `open_paths`.
    - The sign-in addresses (/login, /auth/...) and the files under /static/ pass, as without
      the wall: the sign-in page, its stylesheet, its fonts and the logo are what a person
      signing in needs.
    - Every other address, routed or not (an unknown /.well-known/ address included), is a
      page: a request with no session gets the way to the sign-in page for GET and HEAD
      (_refusal: 303, `next` only for the addresses worth coming back to, or the come-again
      page) and a short plain-text 401 for any other method. A session opened with an API
      key's or an agent's credential is no session: the web UI takes only the cookie.
    - Every answer to a request that was not admitted, on an address that is not a
      machine's, carries `X-Robots-Tag: noindex` (`_noindex`), the cross-origin refusal and
      the sign-in pages included. A signed-in request is answered as without the wall."""

    def __init__(
        self,
        app: ASGIApp,
        accounts: "Accounts",
        public_url: str | None = None,
        landing: Callable[[], Response] | None = None,
        limiter: RateLimiter | None = None,
        open_paths: frozenset[str] = frozenset(),
        routed: Callable[[Scope], bool] | None = None,
        brand: Brand | None = None,
        wall: bool = False,
        failures: CredentialFailures | None = None,
        draft: Callable[[Request], Awaitable[Response | None]] | None = None,
    ) -> None:
        self.app = app
        self.failures = failures
        self.accounts = accounts
        self.public_origin = _origin(public_url) if public_url else None
        self.issuer = oauth.issuer(public_url)
        self.landing = landing if brand is None else None  # it names the software
        self.limiter = limiter
        self.open_paths = open_paths
        self.routed = routed
        self.brand = brand
        self.wall = wall
        self.draft = draft

    def machine(self, scope: Scope) -> bool:
        """Whether the address is one machines use, which the wall leaves as it is."""
        path = scope["path"]
        if path.startswith(oauth.WELL_KNOWN):
            return self.routed is None or self.routed(scope)
        return machine_path(path, self.open_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            return await send({"type": "websocket.close", "code": 1008, "reason": ""})
        path = scope["path"]
        if _is_api(path):  # H1: every answer to a machine, whatever answers it, is no-store
            send = _no_store(send)
        PATH.set(path)  # where the reader is, for the page that answers (templating.section)
        scope[BRAND] = self.brand  # what every page's frame names (templating.brand_of)
        walled = self.wall and not self.machine(scope)
        scope[WALLED] = walled  # app._error: an error to a visitor here is a line of text
        answer = _noindex(send) if walled else send  # until the request is admitted
        if path in self.open_paths:
            return await self.app(scope, receive, send)
        if (
            not _is_api(path)
            and path not in oauth.CORS_PATHS  # clients call these from any origin, cookieless
            and scope["method"] in UNSAFE_METHODS
            and _cross_origin(Headers(scope=scope), self.public_origin)
        ):
            refusal = Response("cross-origin form posts are refused", 403, media_type="text/plain")
            return await refusal(scope, receive, answer)
        if (
            path in PUBLIC_PATHS
            or path.startswith(("/static/", SIGN_IN_PREFIX))
            or (path.startswith(oauth.WELL_KNOWN) and not walled)
        ):
            return await self.app(scope, receive, answer)
        request = HTTPConnection(scope)
        counted = _is_api(path) and self.failures is not None
        presented = _presented_key(request.headers) if counted else ""
        address = request.client.host if request.client else None
        try:
            memory = await run_in_threadpool(self.accounts.admit, request)
        except (CannotWrite, StoreUnusable) as error:  # R1: the store's trouble, not a bug
            return await self._unavailable(request, error, walled)(scope, receive, answer)
        if counted and memory is None and (wait := self._over(path, presented, address)):
            record_access("none", None, None, DENIED, None, 429)  # never what it presented
            return await _too_many_failures(wait)(scope, receive, send)
        if counted and memory is None and presented:  # H1: a credential looked up, refused
            self.failures.failed(address)
        if memory is None and self.draft is not None and scope["method"] == "POST":
            # R1.7: the note form sent after the sign-in ended comes back with its draft
            kept = await self.draft(Request(scope, receive))
            if kept is not None:
                return await kept(scope, receive, answer)
        if memory is None and walled:  # B1: the sign-in page, and nothing else
            if scope["method"] in ("GET", "HEAD"):
                return await _refusal(request, self.issuer)(scope, receive, answer)
            refused = Response("Sign in first.", 401, media_type="text/plain")
            return await refused(scope, receive, answer)
        if walled:  # admitted: answered as without the wall
            scope[WALLED] = False
            if path.startswith(oauth.WELL_KNOWN):  # an unknown one: the router's 404
                return await self.app(scope, receive, send)
        if self.routed is not None and not _is_api(path) and not self.routed(scope):
            person = {"signed_in": True, "person": memory.person} if memory else {}
            brand = self.brand
            no_page = _page("error.html", 404, brand=brand, status=404, message=NO_PAGE, **person)
            return await no_page(scope, receive, send)
        if memory is None:
            if self.landing and path == "/" and scope["method"] in ("GET", "HEAD"):
                return await self.landing()(scope, receive, send)
            if public_page(path) and self.brand is None:  # no account: the route reads none
                return await self.app(scope, receive, send)
            if _is_api(path):  # no route ran: the record says only that one was refused
                record_access("none", None, None, DENIED, None, 401)
            return await _refusal(request, self.issuer)(scope, receive, send)
        limit = memory.plan[1].requests_per_minute if memory.plan else None
        wait = 0
        if self.limiter is not None and limit is not None:
            wait = self.limiter.take(memory.store.account_id, limit)
        if wait:
            return await _too_fast(request, memory, wait, limit)(scope, receive, send)
        scope[MEMORY] = memory
        scope[COMPILES] = self.accounts.compiles
        sessions = self.accounts.sessions
        if sessions.renews and not _is_api(path):  # B1: use renews a remembered sign-in
            token = request.cookies.get(sessions.cookie)
            try:
                if token and await run_in_threadpool(self.accounts.renew, token):
                    send = sessions.resent(send, token)
            except CannotWrite:  # R1: a full disk; a later use renews the sign-in
                pass
            except StoreUnusable as error:
                return await self._unavailable(request, error, False)(scope, receive, send)
        return await self.app(scope, receive, send)

    def _over(self, path: str, presented: str, address: str | None) -> int:
        """H1: for a request on /api or /mcp whose credential was looked up and refused (or
        that carried none), the seconds `address` must wait, to be answered 429 in place of
        401, or 0 to answer as before. A request with no credential at all to /mcp never
        waits, since its 401 is how every agent finds where OAuth starts."""
        assert self.failures is not None
        if not presented and _is_mcp(path):
            return 0
        return self.failures.blocked(address)

    def _unavailable(
        self, request: HTTPConnection, error: CannotWrite | StoreUnusable, walled: bool
    ) -> Response:
        """503 for the store's own trouble before a route ran (R1): the fixed sentence
        (cairn.memory.unavailable) as JSON on /api and /mcp, as the error page elsewhere, and
        the bare status line to a visitor behind the wall, as cairn.web.app._error answers."""
        said = str(unavailable(error))
        if _is_api(request.url.path):
            return JSONResponse({"error": said}, status_code=503)
        if walled:
            return Response("Service Unavailable", 503, media_type="text/plain")
        return _page("error.html", 503, brand=self.brand, status=503, message=said)


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CREDENTIAL_ROLES = frozenset({"owner", "admin"})  # roles that manage a workspace's keys
OWNER_ROLES = frozenset({"owner"})  # the role that may delete a workspace


def may_manage_credentials(memory: Memory, roles: frozenset[str] = CREDENTIAL_ROLES) -> bool:
    """Whether the caller manages the workspace: makes and revokes its keys, changes its
    sets, invites and removes people and changes their roles, and sees its sources and audit
    log. The store decides (Store.manages, the rule each of those calls enforces): a caller
    that acts for no person and reaches the whole workspace does, as before people existed;
    a person does while their role in the workspace is owner or admin. `roles` narrows it
    for a person: OWNER_ROLES (deleting the workspace) admits only its owners."""
    if not memory.store.manages():
        return False
    return memory.store.user_id is None or memory.store.role() in roles


def _origin(url: str) -> str:
    """scheme://host[:port] as a browser sends it in Origin: lowercase, no default port."""
    parts = urlsplit(url.strip())
    scheme, netloc = parts.scheme.lower(), parts.netloc.rpartition("@")[2].lower()
    default = {"http": ":80", "https": ":443"}.get(scheme, "")
    return f"{scheme}://{netloc.removesuffix(default) if default else netloc}"


def _cross_origin(headers: Headers, public_origin: str | None) -> bool:
    """A UI form post sent from another origin. SameSite=Strict cookies still go along from
    other hosts of the same site (every device on one tailnet is one site), so fetch metadata
    decides when the browser sends it; otherwise Origin must be this server's own origin. A
    request with neither header is not a browser form post."""
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site not in ("same-origin", "none")
    origin = headers.get("origin")
    if origin is None:
        return False
    host = headers.get("host", "").lower()
    return origin.lower() not in {f"http://{host}", f"https://{host}", public_origin}


def _without_port(host: str) -> str:
    name, colon, port = host.rpartition(":")
    return name if colon and port.isdigit() else host


def transport_security(
    allowed_hosts: Sequence[str], public_url: str | None
) -> TransportSecuritySettings:
    """Loopback, plus public_url's host and each allowed host, with and without a port: a
    proxy serving https on 443 sends a Host header without one."""
    hosts = [f"{name}:*" for name in LOOPBACK]
    origins = [f"http://{name}:*" for name in LOOPBACK]
    entries = [*allowed_hosts, urlsplit(public_url).netloc if public_url else ""]
    for entry in entries:
        host = entry.rpartition("@")[2].strip().removesuffix(":*")  # no userinfo, no wildcard
        if not host:
            continue
        variants = [host, f"{_without_port(host)}:*"]
        hosts += variants
        origins += [f"{scheme}://{v}" for scheme in ("http", "https") for v in variants]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(hosts)),
        allowed_origins=list(dict.fromkeys(origins)),
    )


class Accounts:
    """The only holder of the unscoped store. It turns a request's credential into a Principal
    and a Principal into that account's Memory; handlers and tools get the Memory and nothing
    else. Nothing is kept between requests, so a revoked key or grant stops at the next one.
    `tokens` is this server's MCP address (OAuth's resource) when OAuth is on, else None:
    only then is an access token a credential, and only one of a grant bound to that
    address (Store.identify_token's audience). `guard` replaces the note guard; None:
    each workspace's own policy (its "guard.policy").
    `plans`: each request's view is limited by its workspace's plan, read at each request;
    None: nothing is capped. `saved(account id, project id, slug)` is called after a request's
    note saves a page (Memory.saved; the app's arranger queues it); None: nothing is.
    `compiles`: the turns of compiles every note takes (G1); None: COMPILES of its own."""

    def __init__(
        self,
        store: Store,
        gate: Gate,
        *,
        tokens: str | None = None,
        guard: Guard | None = None,
        plans: Plans | None = None,
        saved: Callable[[str, str, str], None] | None = None,
        sessions: Sessions | None = None,
        compiles: Compiles | None = None,
    ) -> None:
        self.sessions = sessions or Sessions()  # the web session's cookie and life (B1)
        # G1: the notes this server compiles at once (cairn.web.turns), shared by every way
        # a note reaches a compile: requests, the sources' runs and the filer.
        self.compiles = compiles or Compiles()
        self._store = store
        self._gate = gate
        self._tokens = tokens
        self._guard = guard
        self._plans = plans
        self._saved = saved  # (account id, project id, slug) after a request's note saves a page

    def serving(self) -> bool:
        """Whether this server can serve (/healthz): the store's bounded read worked, or
        found the store busy at work (Store.health). False for anything else, an error
        this does not know included: a probe is told the store cannot serve, never why."""
        try:
            return self._store.health() != "unavailable"
        except Exception:
            return False

    def principal(self, request: HTTPConnection) -> tuple[Principal, str | None] | None:
        """Who the request acts for, and the actor id that tells them apart from others of
        the same name (Store.identify_*). The web UI takes the session cookie and nothing else.
        /api takes an API key and nothing else. /mcp takes an API key, or an OAuth access
        token as `Authorization: Bearer`: the token's prefix, which no key can have, says
        which of the two a bearer value is, so each is looked up once and never as the
        other. A token presented anywhere but on /mcp is refused."""
        path = request.scope["path"]
        if not _is_api(path):
            token = request.cookies.get(self.sessions.cookie)
            return self._store.identify_session(token) if token else None
        key, bearer = request.headers.get("x-api-key", "").strip(), _bearer(request.headers)
        if not key and bearer.startswith(ACCESS_TOKEN_PREFIX):
            usable = self._tokens is not None and _is_mcp(path)
            return self._store.identify_token(bearer, audience=self._tokens) if usable else None
        return self._store.identify_key(key or bearer) if key or bearer else None

    def memory(
        self, principal: Principal, actor_id: str | None = None, key_id: str | None = None
    ) -> Memory | None:
        """The principal's account, and only that, acting for the principal's person when it
        has one, narrowed to the principal's reach: a key or a grant reaches the projects and
        sets it was made with, to write or only to read; a session has every project. The view
        acts as the caller (`Store.acting_as`): a key as "key" and its label, an agent as
        "token" and its client's name, a session as "session" and the person's name, or the
        label of the key it was opened with. The view writes as the caller (A1,
        `Store.writing_as`), from how the caller was admitted and never from what a request
        says: a key as the key, an agent as the agent, a session opened with a key as that
        key (`key_id`: its id, which a session opened with a key a person made does not give
        as its actor id; `admit` looks it up), and any other session as its person, by name.
        A guard given to the app replaces the
        workspace's policy on the view (`Store.with_guard`). With plans, the view is
        limited by the workspace's plan as it is recorded now (`Store.limited`), and the
        Memory knows the plan. The plan is the one in force now (`Plans.in_force`: the
        operator's, else the default), one read. None: the account is
        gone, or the person is no longer one of its members (either can change between
        resolving a credential and this call)."""
        member = {"user_id": principal.user_id} if principal.user_id else {}
        try:
            view = self._store.for_account(principal.account_id, **member)
        except KeyError:
            return None
        person = view.get_user(principal.user_id) if principal.user_id else None
        if principal.user_id and person is None:
            return None
        name = person.name if person and principal.kind == "session" else principal.label
        view = view.acting_as(principal.kind, name, actor_id)
        view = _writing(view, principal, person, actor_id, key_id)
        if self._guard is not None:
            view = view.with_guard(self._guard)
        plan = None
        if self._plans is not None:
            try:
                plan = self._plans.in_force(view, principal.account_id)
            except KeyError:
                return None
        view = view.narrow(principal.projects, principal.access)
        if plan is not None:  # the last layer: caps are the workspace's, whatever the reach
            # Each write reads the plan in force again, inside its own transaction.
            view = self._plans.limit(view, plan[1])  # type: ignore[union-attr]
        return Memory(view, self._gate, person, plan, plans=self._plans)

    def admit(self, request: HTTPConnection) -> Memory | None:
        found = self.principal(request)
        key_id = None
        if found and found[0].kind == "session" and found[0].label and found[0].user_id:
            # A session opened with a key a person made names the person as its actor: the
            # key it was opened with is looked up, by the session's own token (A1).
            key_id = self._store.session_key(request.cookies.get(self.sessions.cookie, ""))
        memory = self.memory(*found, key_id) if found else None
        if memory is not None and self._saved is not None:  # T4b: a saved page is queued
            memory.saved = functools.partial(self._saved, memory.store.account_id)
        return memory

    def open_session(self, key: str) -> str | None:
        """A new session token in the account of `key`; None when the key is not accepted. A
        session has the whole workspace, so a key limited to chosen projects or read-only is
        not accepted (its narrowed view refuses to make one)."""
        found = self._store.identify_key(key) if key else None
        memory = self.memory(*found) if found else None
        try:
            return memory.store.create_session(SESSION_TTL) if memory else None
        except PermissionError:
            return None

    def delete_workspace(self, memory: Memory) -> None:
        """Delete the caller's workspace through the caller's own view (Store.delete_account,
        which zeroes what it deletes and truncates the write-ahead log, so the notes' text
        leaves the files at once), which decides inside the write that deletes that the
        caller is an owner now, or acts for no person: PermissionError, and nothing deleted,
        for a person demoted or removed since the page checked them. The deletion cannot be
        recorded in the audit log it deletes: it is one line on the access log. The web view
        never compacts the database: that is the operator's (`compact`, or `serve
        --compact-every`)."""
        account_id = memory.store.account_id
        kind, actor = memory.store.actor
        memory.store.delete_account(account_id)
        with contextlib.suppress(Exception):  # a log handler that raises fails no request
            access_log.info(
                "workspace.deleted %s by %s %r (%s)",
                account_id,
                kind,
                actor,
                memory.store.actor_id,
                extra={
                    "actor_kind": kind,
                    "actor": actor,
                    "actor_id": memory.store.actor_id,
                    "action": "workspace.deleted",
                    "project": None,
                    "status": 303,
                    "account_id": account_id,
                },
            )

    def join(self, memory: Memory, token: str, came_with: str | None = None) -> str:
        """The caller's person takes the invitation `token` (Store.accept_invite, through
        their own view, so the audit log names them), and a new session token in its
        workspace, made from `came_with` as `switch` makes one. KeyError: a dead invitation.
        PermissionError: a caller acting for no person, or a person the invitation is not
        for; nothing is written on either."""
        user_id = memory.store.user_id
        if user_id is None:
            raise PermissionError("sign in as yourself to take an invitation")
        found = memory.store.find_invite(token)
        if found is None:
            raise KeyError("this invitation is not open")
        memory.store.accept_invite(token, user_id)
        session = self.switch(memory, found.account_id, came_with)
        if session is None:  # removed again between joining and now
            raise KeyError("this invitation is not open")
        return session

    def switch(self, memory: Memory, account_id: str, came_with: str | None = None) -> str | None:
        """A new session token for the caller's person in another of their workspaces; None
        when the caller acts for no person or the person is not a member there. The session
        the caller came with (`came_with`, its token: the cookie Auth admitted) stays, and
        the new one carries the address its sign-in was vouched with (Store.create_session's
        `made_from`, B1), so session-end of that address ends both."""
        user_id = memory.store.user_id
        if user_id is None:
            return None
        principal = Principal(account_id=account_id, kind="session", user_id=user_id)
        there = self.memory(principal, user_id)
        if there is None:
            return None
        return there.store.create_session(self.sessions.seconds, made_from=came_with)

    def sign_in(self, provider: str, issuer: str, profile: Profile, join: str) -> SignedIn:
        """A session for the person a provider vouched for, made as Store.sign_in says
        (ProviderChanged included). The sign-in callback is the one handler that takes a
        credential itself, so it asks here."""
        return self._store.sign_in(
            provider,
            profile.subject,
            profile.email,
            profile.name,
            issuer=issuer,
            join=join,  # type: ignore[arg-type]  # checked when the app was made
            ttl_seconds=self.sessions.seconds,
        )

    def end_session(self, token: str) -> None:
        """End the web session `token`, in whichever workspace it is (signing out ends the
        sessions a browser holds under the session cookie's other names, which may be in
        another of the person's workspaces). Holding the token is what lets a caller end it."""
        found = self._store.resolve_session(token)
        if found is not None:
            self._store.for_account(found.account_id).end_session(token)

    def renew(self, token: str) -> bool:
        """Renew the session `token` when it is due (Store.renew_session: a person's session
        made by signing in, last renewed more than RENEW_AFTER ago, lasts its full length
        again); whether it was. Auth asks on each admitted use when `sessions.renews`."""
        return self._store.renew_session(token, self.sessions.seconds, RENEW_AFTER)

    def ledger(self) -> oauth.Ledger:
        """What the OAuth endpoints get of the store: its operations on clients, codes and
        tokens, which belong to no account, and nothing that reads or writes an account's
        data. Each is looked up on the store when it is called."""

        def operation(name: str) -> Any:
            return lambda *args, **named: getattr(self._store, name)(*args, **named)

        names = [field.name for field in dataclasses.fields(oauth.Ledger)]
        return oauth.Ledger(**{name: operation(name) for name in names})

    async def sync_sources(
        self, every: float, *, http: httpx.AsyncClient, env: Mapping[str, str]
    ) -> None:
        """Every workspace's sources, every `every` seconds, with the app's gate, guard,
        plans and arranger (a page a note saved is queued), until cancelled
        (cairn.sources.sync_forever)."""
        await sync_forever(
            self._store,
            every,
            http=http,
            env=env,
            gate=self._gate,
            guard=self._guard,
            plans=self._plans,
            saved=self._saved,
            turn=self.compiles.turn,
        )

    # N1: the notes that wait for their page, filed by the app's filer (cairn.web.filing)

    def waiting_notes(self) -> list[WaitingNote]:
        """Every workspace's notes waiting for their page (Store.waiting_notes)."""
        return self._store.waiting_notes()

    def file_waiting(self, account_id: str, note_id: str) -> str | None:
        """File one waiting note as a request's note is filed (Memory.file_waiting): by its
        workspace's view acting as the system, under the plan in force and the app's gate,
        a saved page queued for the arranger. None: the workspace or the note is gone."""
        try:
            view = self._store.for_account(account_id).acting_as("system", None)
        except KeyError:
            return None
        plan = None
        if self._plans is not None:
            try:
                plan = self._plans.in_force(view, account_id)
            except KeyError:
                return None
            view = self._plans.limit(view, plan[1])
        memory = Memory(view, self._gate, None, plan, plans=self._plans)
        if self._saved is not None:
            memory.saved = functools.partial(self._saved, account_id)
        return memory.file_waiting(note_id)

    def tried_waiting(self, account_id: str, note_id: str) -> int:
        """Count one more try of a waiting note (Store.tried_waiting)."""
        return self._store.for_account(account_id).tried_waiting(note_id)


def _writing(
    view: Store,
    principal: Principal,
    person: User | None,
    actor_id: str | None,
    key_id: str | None,
) -> Store:
    """`view`, writing as the caller (A1; Accounts.memory): a key as the key, by its label,
    with its id; an agent as the agent; a session opened with a key as that key, with the
    key's id (`key_id`, or the session's actor id when no person is named); any other
    session as its person."""
    if principal.kind == "key":
        return view.writing_as("key", principal.label, actor_id)
    if principal.kind == "token":
        return view.writing_as("agent", principal.label)
    if principal.label is not None:  # a session opened with a key
        made_through = key_id if principal.user_id else actor_id
        return view.writing_as("key", principal.label, made_through)
    return view.writing_as("person", person.name if person else None)


def _memory(request: object) -> Memory:
    """The caller's account, as Auth put it on the request. It fails closed: a request Auth
    did not admit has no account, and there is no default to fall back on."""
    memory = request.scope.get(MEMORY) if isinstance(request, HTTPConnection) else None
    if not isinstance(memory, Memory):
        raise RuntimeError("this request was not authenticated: it has no account")
    return memory


def _view(request: Request) -> Store:
    """The caller's own view of the store: what the consent page works with."""
    return _memory(request).store
