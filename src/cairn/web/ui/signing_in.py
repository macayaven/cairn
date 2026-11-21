"""Signing in and sessions: login with a key and logout (session_routes), where a sign-in may
send the browser on to (_local_path, welcome_path), and /setup and /welcome, where a new
person learns to connect an agent."""

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from cairn.web.auth import SESSION_COOKIES, SESSION_TTL, Accounts, _memory
from cairn.web.ratelimit import TOO_MANY_FAILURES, CredentialFailures
from cairn.web.templating import _page, brand_of
from cairn.web.ui.common import _ui


def _local_path(value: object) -> str:
    """`value` when it is a path on this server, else "/": the only places a sign-in sends
    the browser on to. A path starts with one "/"; "//host" and "/\\host" (which browsers
    read the same way) name another server, and so can anything holding a character a browser
    drops or rewrites, so only printable ASCII without a backslash passes."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return "/"
    if any(not "!" <= char <= "~" or char == "\\" for char in value):
        return "/"
    return value


def welcome_path(target: str) -> str:
    """/welcome, carrying the way on to `target` (a path from _local_path) unless it is /."""
    return "/welcome" if target == "/" else f"/welcome?next={quote(target, safe='')}"


def session_routes(
    accounts: Accounts, *, key_login: bool = True, failures: CredentialFailures | None = None
) -> list[Route]:
    """Login and logout. Login is the one handler that takes a credential itself (the form's
    key), so it asks `accounts` for the session, and counts a refused key against the client's
    address in `failures` as Auth does on /api (H1): the key is looked up first, and a correct
    one opens a session from any address; from an address over the limit, a refused key, or
    none, is answered 429 on the login form, with Retry-After and TOO_MANY_FAILURES, in
    place of 401. Logout ends the session Auth
    admitted, and
    ends and clears every other session cookie the browser sends under a name the server has
    used for it (auth.SESSION_COOKIES: cairn_session, session, __Host-session and
    __Host-cairn_session, the last two its names on https), so a session
    left under another name does not come back when a brand is switched on or off.
    Without `key_login` only logout is here: people sign in through a provider instead.
    The cookie is `accounts.sessions`' (auth.Sessions); a key's session lasts SESSION_TTL."""
    sessions = accounts.sessions

    def login_form(request: Request) -> Response:
        target = _local_path(request.query_params.get("next"))
        return _page("login.html", brand=brand_of(request), error=None, next=target)

    async def login(request: Request) -> Response:
        form = await request.form()
        key, target = form.get("key"), _local_path(form.get("next"))
        key = key.strip() if isinstance(key, str) else ""
        token = await run_in_threadpool(accounts.open_session, key)
        if token is None:
            brand = brand_of(request)
            address = request.client.host if request.client else None
            if failures is not None and (wait := failures.blocked(address)):
                busy = _page("login.html", 429, brand=brand, error=TOO_MANY_FAILURES, next=target)
                busy.headers.update({"retry-after": str(wait), "cache-control": "no-store"})
                return busy
            if key and failures is not None:
                failures.failed(address)
            refused = "That key was not accepted."
            return _page("login.html", 401, brand=brand, error=refused, next=target)
        response = RedirectResponse(target, status_code=303)
        sessions.set(response, token, SESSION_TTL)
        return response

    async def logout(request: Request) -> Response:
        if token := request.cookies.get(sessions.cookie):
            await run_in_threadpool(_memory(request).store.end_session, token)
        response = RedirectResponse("/login", status_code=303)
        sessions.clear(response)
        for name in SESSION_COOKIES:  # B1: a name the cookie had before a brand was switched
            if name != sessions.cookie and (other := request.cookies.get(name)):
                await run_in_threadpool(accounts.end_session, other)
                sessions.clear(response, name)
        return response

    logins = [
        Route("/login", login_form, methods=["GET"]),
        Route("/login", login, methods=["POST"]),
    ]
    return [*(logins if key_login else []), Route("/logout", logout, methods=["POST"])]


def signing_in_handlers(
    app_url: str, by_url: bool, mcp_url: str, public_url: str | None, sign_in: bool
) -> tuple[Callable[..., Any], ...]:
    """The handlers of /setup and /welcome, as ui_routes registers them."""

    def setup(request: Request) -> Response:
        return RedirectResponse("/start/connect", status_code=303)

    def welcome(request: Request) -> Response:
        """Where a new person lands: how to connect an agent, and the way back to where they
        were going (`next`), when there was one."""
        target = _local_path(request.query_params.get("next"))
        return _ui(
            request,
            "welcome.html",
            mcp_url=mcp_url,
            oauth=by_url,
            next=None if target == "/" else target,
            start=not _memory(request).store.list_projects(),  # nothing yet: lead to /start
        )

    return setup, welcome
