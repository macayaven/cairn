"""The app factory: routes, middleware, error handlers, the lifespan and the health check.

The health check (R1): GET /healthz is 200 and exactly `{"status":"ok"}` when this server
can serve, and 503 and exactly `{"status":"unavailable"}` when it cannot: the store is
closed, unusable (a rollback failed), cannot be read or holds a schema version this code
does not know. Nothing else is said, no path and no error. It is Accounts.serving, one
bounded read of the store that writes nothing (Store.health): a store whose lock another
call holds past the bound, as compact holds it on purpose, is busy and alive, so 200. The
read runs on threads of its own (HEALTH_THREADS), so requests waiting for the store's lock
do not hold up the answer. Docker's probe, the hosted updater and the uptime alert read it.
Like every machine address it needs no credential, behind the wall and under a brand too."""

import asyncio
import contextlib
import math
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Match, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from cairn import oauth
from cairn.compiler import Gate, provenance_gate
from cairn.guard import Guard
from cairn.memory import OverQuota, Refused, RequestError, unavailable
from cairn.plans import Plans
from cairn.slack.link import link_routes
from cairn.slack.setup import Block as SlackBlock
from cairn.slack.setup import Outcomes as SlackOutcomes
from cairn.slack.setup import block as slack_block
from cairn.slack.setup import office_of, setup_routes
from cairn.sources.runner import MIN_SYNC_EVERY, source_client
from cairn.store import CannotWrite, Principal, Store, StoreUnusable
from cairn.web.account import account_routes
from cairn.web.auth import (
    BRANDED_SESSION_COOKIE,
    MEMORY,
    SESSION_COOKIE,
    SESSION_TTL,
    WALLED,
    Accounts,
    Auth,
    Sessions,
    _is_api,
    _view,
    branded_cookie,
    transport_security,
)
from cairn.web.brand import Brand, Logo, make_brand, server_name
from cairn.web.compactor import Compactor
from cairn.web.compose import signed_out_draft
from cairn.web.erasure import erasure_routes
from cairn.web.filing import Filer
from cairn.web.google_connect import BACK_PATH, Block, GoogleConnect, Revoker, google_routes
from cairn.web.graph_routes import graph_routes
from cairn.web.graph_view import GRAPH_SCRIPT, GRAPH_STYLESHEET
from cairn.web.mcp import build_mcp
from cairn.web.members import member_routes
from cairn.web.public import Public
from cairn.web.ratelimit import CredentialFailures, RateLimiter
from cairn.web.rest import rest_routes
from cairn.web.signin import SignIn, signin_routes
from cairn.web.slack import Keeper, Slack, desk_of, open_paths, signature_failures, slack_routes
from cairn.web.slack import check as check_slack
from cairn.web.stats import CountResponses, Responses, StatsLog
from cairn.web.sweeper import Sweeper
from cairn.web.templating import (
    BRANDED_CSS,
    BRANDED_STYLESHEET,
    HERE,
    NO_PAGE,
    _page,
    brand_of,
    templates,
)
from cairn.web.turns import COMPILES, Compiles
from cairn.web.ui import _ui, session_routes, ui_routes
from cairn.web.workspace import workspace_routes

if TYPE_CHECKING:  # never at run time: without an arranger no model code is imported
    from cairn.arrange import Arranger

# K1b: the Sources page's Slack block (templates/_slack.html), nothing unless the switch's
# middleware (cairn.slack.setup.Block) says what it shows
templates.globals["slack_block"] = slack_block

MAX_BODY = 4 * 1024 * 1024  # bytes, the same cap as the MCP transport's
# Media types cairn names itself for its static files, rather than reading them from the
# host's tables: Python's own table has no font type, and a slim image may have no
# /etc/mime.types, which would serve the typefaces as application/octet-stream.
STATIC_TYPES = {".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8"}


class _Static(StaticFiles):
    """/static/: the stylesheet, the icon, the manifest, the typefaces and their licences.
    `served`: when given, the only paths it serves, compared exactly as the request names
    them; any other is 404, as if it were not there (branded_static). None: every file.
    The graph explorer's two files (G2: graph_view.GRAPH_SCRIPT and GRAPH_STYLESHEET) are
    never served from here, by any spelling: a path that reaches either file, as the file
    system resolves it, is 404. With the explorer on, routes of their own serve them."""

    def __init__(self, *, served: frozenset[str] | None = None, **options: Any) -> None:
        super().__init__(**options)
        self.served = served
        self.withheld = set()  # (device, inode) of each file it never serves
        for name in (GRAPH_SCRIPT, GRAPH_STYLESHEET):
            with contextlib.suppress(FileNotFoundError):  # a folder without them
                found = os.stat(os.path.join(options["directory"], name))
                self.withheld.add((found.st_dev, found.st_ino))

    def get_path(self, scope: Scope) -> str:
        path = super().get_path(scope)
        if self.served is not None and path not in self.served:
            raise HTTPException(status_code=404)
        return path

    def lookup_path(self, path: str) -> tuple[str, os.stat_result | None]:
        full_path, found = super().lookup_path(path)
        if found is not None and (found.st_dev, found.st_ino) in self.withheld:
            return "", None
        return full_path, found

    def file_response(self, full_path, stat_result, scope, status_code=200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        kind = STATIC_TYPES.get(os.path.splitext(full_path)[1].lower())
        if kind and response.status_code != 304:
            response.headers["content-type"] = kind
        return response


def _error(request: Request, status: int, message: str) -> Response:
    if _is_api(request.url.path):
        return JSONResponse({"error": message}, status_code=status)
    memory = request.scope.get(MEMORY)  # signed in: the app's navigation stays
    if request.scope.get(WALLED) and memory is None:  # B1: behind the wall, no page but one
        try:
            said = HTTPStatus(status).phrase
        except ValueError:
            said = "Refused"
        return Response(said, status, media_type="text/plain")
    person = getattr(memory, "person", None)
    signed_in = memory is not None
    brand = brand_of(request)
    return _page(
        "error.html",
        status,
        brand=brand,
        person=person,
        signed_in=signed_in,
        status=status,
        message=message,
    )


async def _on_request_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestError)
    if isinstance(exc, Refused) and _is_api(request.url.path):
        body = {"error": str(exc), "findings": exc.listed()}
        return JSONResponse(body, status_code=exc.status)
    if isinstance(exc, OverQuota) and _is_api(request.url.path):
        return JSONResponse({"error": str(exc), **exc.fields()}, status_code=exc.status)
    response = _error(request, exc.status, str(exc))
    if wait := getattr(exc, "retry_after", None):  # 429: when to ask again
        response.headers["retry-after"] = str(wait)
    return response


async def _on_http_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, HTTPException)
    response = _error(request, exc.status_code, exc.detail)
    response.headers.update(exc.headers or {})
    return response


async def _on_store_error(request: Request, exc: Exception) -> Response:
    """The store's own trouble outside a REST route or tool call (a page, a form): 503 and
    the fixed sentence, as Memory.access answers it there."""
    assert isinstance(exc, CannotWrite | StoreUnusable)
    return _error(request, 503, str(unavailable(exc)))


# The health check's own threads: a probe never queues behind requests that wait for the
# store's lock (compact holds it for a whole rewrite of the file). Each probe takes at most
# about twice cairn.store.HEALTH_WAIT, so a few threads serve every prober there is.
HEALTH_THREADS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cairn-healthz")
HEALTHY = {"status": "ok"}
UNHEALTHY = {"status": "unavailable"}


def _healthz(accounts: Accounts) -> Callable[[Request], Any]:
    async def healthz(request: Request) -> Response:
        loop = asyncio.get_running_loop()
        if await loop.run_in_executor(HEALTH_THREADS, accounts.serving):
            return JSONResponse(HEALTHY)
        return JSONResponse(UNHEALTHY, status_code=503)

    return healthz


def _oauth_page(asked: Request, name: str, status_code: int = 200, **context: Any) -> Response:
    """The page renderer OAuth's consent pages get: the brand of the request `asked`
    frames it (the consent form has a field of its own named `request`)."""
    return _page(name, status_code, brand=brand_of(asked), **context)


BRANDED_FOLDER = "fonts"  # under a brand, the one folder of static/ that is served


def branded_static(directory: str | os.PathLike[str]) -> _Static:
    """/static/ under a brand: only what a branded page needs and names nothing, the fonts
    and their licence texts (the files of `directory`/fonts, listed once, here), each by its
    exact name. The brand's stylesheet and logo have routes of their own (_branded_routes).
    Nothing is guessed about how the file system folds names: a name that is not one of
    these exactly (another case, a character a file system folds to another, a path through
    ..) is 404, so the stylesheet, the icon and the manifest, which name the software, are
    never served by any spelling. There is no manifest under a brand: the pages link none,
    so the server is not offered as an app to install under a name not the operator's."""
    folder = os.path.join(directory, BRANDED_FOLDER)
    served = frozenset(
        os.path.normpath(os.path.join(BRANDED_FOLDER, entry.name))
        for entry in os.scandir(folder)
        if entry.is_file()
    )
    return _Static(directory=directory, served=served)


IMMUTABLE = "public, max-age=31536000, immutable"  # an address that changes with the content
# What the logo may do when opened by itself, an SVG or a PNG alike: draw, with its own
# inline style, and fetch nothing, run nothing (sandbox: no script, no form, no navigation,
# no other origin), and be framed by no page (frame-ancestors).
LOGO_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox; frame-ancestors 'none'"
)


def _branded_routes(brand: Brand) -> list[Route]:
    """Under a brand, what takes the place of the files that name the software: the
    stylesheet (templating.BRANDED_CSS, at BRANDED_STYLESHEET) and the logo at its digest's
    address, its bytes as the file held them, with LOGO_POLICY whatever its type. Both
    addresses change with the content, so both may be kept for a year."""
    sheet = BRANDED_CSS.encode()
    kept = {"x-content-type-options": "nosniff", "cache-control": IMMUTABLE}

    def stylesheet(request: Request) -> Response:
        return Response(sheet, media_type="text/css; charset=utf-8", headers=kept)

    routes = [Route(urlsplit(BRANDED_STYLESHEET).path, stylesheet, methods=["GET"])]
    if brand.logo is not None:
        logo = brand.logo
        headers = {**kept, "content-security-policy": LOGO_POLICY}  # a PNG's too

        def served_logo(request: Request) -> Response:
            return Response(logo.data, media_type=logo.media_type, headers=headers)

        routes.append(Route(logo.path, served_logo, methods=["GET"]))
    return routes


async def _stopped(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _limited(sign_in: SignIn) -> bool:
    """Whether only approved people can sign in: an allowed address or domain is listed, or
    no provider lets anyone get an account."""
    listed = sign_in.allowed_emails or sign_in.allowed_email_domains
    return bool(listed) or not any(provider.open_to_anyone for provider in sign_in.providers)


def create_app(
    store: Store,
    *,
    gate: Gate | None = None,
    allowed_hosts: Sequence[str] = (),
    public_url: str | None = None,
    sign_in: SignIn | None = None,
    guard: Guard | None = None,
    sync_every: float | None = None,
    source_http: Callable[[], httpx.AsyncClient] = source_client,
    compact_every: float = 0,
    sweep_every: float | None = None,
    registrations_per_hour: int | None = None,
    plans: Plans | None = None,
    clock: Callable[[], datetime] | None = None,
    arranger: "Arranger | None" = None,
    operator_notes: str | None = None,
    wall: bool = False,
    test_instance: bool = False,
    brand_name: str | None = None,
    brand_logo: "str | os.PathLike[str] | Logo | None" = None,
    brand_logo_alt: str | None = None,
    session_days: int | None = None,
    file_every: float | None = None,
    compiles: int = COMPILES,
    keep_versions: int | None = None,
    stats_every: float | None = None,
    stats_wait: Callable[[float], Awaitable[Any]] | None = None,
    docs_artifact: str | os.PathLike[str] | None = None,
    docs_release: str | None = None,
    docs_reader_env: str | None = None,
    google_connect: GoogleConnect | None = None,
    graph_explorer: bool = False,
    slack: Slack | None = None,
) -> Starlette:
    """The store goes to `Accounts` and nowhere else: no handler is defined where it is in reach.
    `sign_in`: people sign in through identity providers instead of with an API key
    (ValueError when it cannot work; see cairn.web.signin.check). `guard`: the note guard
    every note passes, instead of each workspace's own policy. `sync_every`: while the app
    runs, every workspace's sources are synced in the background every that many seconds
    (ValueError under MIN_SYNC_EVERY), through a client from `source_http` and with the
    credentials in this process's environment; shutdown stops it, then closes the client.
    `compact_every`: hours between background compactions after a workspace is deleted
    (cairn.web.compactor; 0, the default: never). `sweep_every`: seconds between background
    sweeps of what has run out (Store.sweep_expired; cairn.web.sweeper), the first when the
    app starts; None, the default: never; 0 or less is a ValueError; `app.state.sweep_every`
    holds it. The compactor and the sweeper are the only other holders of the store, and
    neither serves a request. `registrations_per_hour`: how many agents one client address
    may register in any hour (cairn.oauth.REGISTRATIONS, 20, when None; a whole number of at
    least 1, else ValueError); `app.state.registrations_per_hour` holds it. The background
    tasks start and stop independently; none waits for another.
    `plans`: every request, and the background sync, acts through a view limited by its
    workspace's plan, and a plan's `requests_per_minute` is enforced in memory; None (a
    self-hosted install): nothing is capped or rate limited. `clock`: what the rate limit
    reads the time from (an aware datetime), and so does the limit on refused credentials per
    client address (H1; cairn.web.ratelimit.CredentialFailures, on every install, at
    `app.state.credential_failures`); the store has its own (`Store(path, clock)`).
    `arranger` (T4b, a cairn.arrange.Arranger): every note a request or a source sync
    saves queues its page there, `guard` (when given) is what it screens a page with, and
    when the arranger has `every`, it runs in the background beside the other two tasks,
    started and stopped on its own (shutdown waits cairn.arrange.STOP_GRACE seconds at most
    for the pages in hand). None: nothing is queued and no model code is imported.
    `operator_notes` (`serve --operator-notes FILE`): Markdown the operator wrote, shown as
    the last section of /docs/privacy (cairn.web.public.privacy_guide).
    B1 (the contract is in cairn/server.py, "A wall, a brand"): `wall` (`serve --wall`): a
    browser that is not signed in is shown the sign-in page and nothing else (Auth).
    `test_instance` (`serve --test-instance`): a browser that is not signed in is told the
    install is a private test instance whose sign-in is limited to approved testers, on the
    landing page and the sign-in page, which lead with the run guide (Public, signin_routes),
    and the guides' header links to /login as "Tester sign-in". Under a brand a visitor is
    shown what they are shown without it. Who is admitted does not change. It needs
    `sign_in` limited to allowed addresses or domains, or with no provider open to anyone
    (ValueError naming the option otherwise).
    `brand_name`, `brand_logo` (a file's path, or a cairn.web.brand.Logo already read) and
    `brand_logo_alt`: the operator's name in place of cairn's (cairn.web.brand). `session_days`
    (a whole number from 1 to 365): a sign-in lasts that many days and use renews it
    (auth.Sessions). Each needs `sign_in` (ValueError otherwise, as for a value that cannot be
    used); left out, nothing changes.
    `file_every` (N1; `serve` sets it, cairn.web.filing.FILE_EVERY): the notes waiting for
    their page are filed in the background, those of the start once the app runs, then a
    look every that many seconds (cairn.web.filing); shutdown waits for the note in hand.
    None, the default: nothing is filed but by the request that stored a note.
    `compiles` (G1; `serve --compiles N`, COMPILES, 2, by default): this app compiles at most
    that many notes at a time, a whole number of at least 1 (ValueError otherwise); the
    others wait their turn in arrival order, on the event loop (cairn.web.turns). Requests'
    notes, the sources' runs and the filer all take a turn; nothing else does.
    `app.state.compiles` holds the Compiles.
    `keep_versions` (Q1; `serve` passes its --keep-versions, 50 unless told otherwise): every
    save, on any view of `store`'s connection, keeps the newest that many versions of its
    page and removes the older ones in its own transaction (Store.bound_history); reading
    removes none. None, the default: every version is kept. With `plans` a plan's own
    versions_per_page rules, so `keep_versions` beside them is a ValueError, as is anything
    but None or a whole number of at least 1.
    `docs_artifact`, `docs_release` and `docs_reader_env` configure a dedicated read-only
    documentation release (docs/documentation-helper.md): /help and the footer's Help link exist
    only when one of the three is given. `clock` also times its VERIFY_EVERY. The helper
    calls no model.
    `stats_every` (Q1; `serve --stats-every`, 15 unless told otherwise): the install's
    numbers are logged on the logger cairn.web.stats.STATS_LOGGER, never the access log's, as
    one line, `stats` and a JSON object (cairn.web.stats: the totals of `stats --json`
    without `workspaces`, and `requests`, the responses this app sent since the last line by
    class, counted around the whole app), at start and then after each wait of that many
    minutes, `stats_wait(seconds)` (asyncio.sleep when None), without end. The numbers are
    gathered on a worker thread, each count in a read of its own. A pass that fails logs its
    error's class only, and the next one runs; under low disk (free space under twice the
    database's size) the line is a WARNING followed by one that says writes fail and that
    compaction and backup cannot run. Nothing else changes, /healthz included, and no route
    serves the numbers. None or 0, the default: no line, no loop, no counting.
    `app.state.stats_every` holds the minutes, None when off. A value that is not a number
    of 0 or more is a ValueError.
    `google_connect` (`serve --google-connect`; cairn.web.google_connect): a signed-in person
    it is offered to connects a Google account on the Sources page, and the store keeps and
    uses its sealed token (cairn.store.hosted). It needs `sign_in` and `public_url`
    (ValueError otherwise). None, the default: no route, no block, no table read.
    `graph_explorer` (G2; `serve --graph-explorer`): a project's graph page becomes the
    explorer (cairn.web.graph_routes), the one page that loads a script, cairn's own
    /static/graph-view.js, by a nonce and its hash; it needs nothing else. False, the
    default: the graph page is decision 134's, with no script, and /static/graph-view.js,
    /static/graph-view.css and /b/<project>/graph/notes/<id> are 404. True or False only
    (ValueError otherwise); `app.state.graph_explorer` holds it.
    `slack` (K1; `serve --slack`; cairn.web.slack): the three addresses Slack calls, open paths
    of Auth with their own protection, and the loop that sweeps and forgets
    (`app.state.slack_keeper`, started in the lifespan); the set-up and linking addresses of a
    signed-in browser and the Sources page's block (cairn.slack.setup, cairn.slack.link); the
    store seals and opens the
    installs' secrets with its key. It needs `sign_in` and an https:// `public_url`, and a key
    of 32 bytes (ValueError otherwise, with the sentence `serve` says). None, the default: no
    route, no block, no table read."""
    if type(graph_explorer) is not bool:
        raise ValueError("graph_explorer is True or False")
    if slack is not None:
        # ValueError, serve's sentence; under a brand too, for now (UNDER_A_BRAND)
        check_slack(slack, sign_in=sign_in, public_url=public_url, brand_name=brand_name)
    if google_connect is not None and (sign_in is None or not public_url):
        raise ValueError("--google-connect needs --sign-in and --public-url")
    if plans is not None and keep_versions is not None:
        raise ValueError("with plans, each plan says how many versions of a page it keeps")
    if stats_every is not None and (
        isinstance(stats_every, bool)
        or not isinstance(stats_every, int | float)
        or not math.isfinite(stats_every)
        or stats_every < 0
    ):
        raise ValueError("--stats-every is a number of minutes, 0 or more")
    stats_every = stats_every or None  # 0: off
    if keep_versions is not None and (type(keep_versions) is not int or keep_versions < 1):
        raise ValueError("--keep-versions is a whole number, 1 or more, or all")
    if sign_in is None:
        for option, given in (
            ("--wall", wall),
            ("--test-instance", test_instance),
            ("--brand-name", brand_name is not None),
            ("--session-days", session_days is not None),
        ):
            if given:
                raise ValueError(f"{option} needs --sign-in")
    elif test_instance and not _limited(sign_in):
        raise ValueError(
            "--test-instance needs sign-in limited to allowed addresses, allowed domains or"
            " providers nobody can join on their own"
        )
    if session_days is not None and (type(session_days) is not int or not 1 <= session_days <= 365):
        raise ValueError("--session-days is a whole number from 1 to 365")
    brand = make_brand(brand_name, brand_logo, brand_logo_alt)  # ValueError naming the option
    turns = Compiles(compiles)  # ValueError naming the option
    if sync_every is not None and not sync_every >= MIN_SYNC_EVERY:
        raise ValueError(f"sync sources at most every {MIN_SYNC_EVERY:g} s")
    sweeper = Sweeper(store, sweep_every) if sweep_every is not None else None  # ValueError
    if registrations_per_hour is None:
        registrations_per_hour = oauth.REGISTRATIONS
    if type(registrations_per_hour) is not int or registrations_per_hour < 1:
        raise ValueError("registrations_per_hour is a whole number, 1 or more")
    issuer = oauth.issuer(public_url)  # None: OAuth is off
    secure = (public_url or "").lower().startswith("https://")
    sessions = Sessions(
        cookie=branded_cookie(BRANDED_SESSION_COOKIE, secure) if brand else SESSION_COOKIE,
        seconds=session_days * 86_400 if session_days else SESSION_TTL,
        renews=session_days is not None,
        secure=secure,
    )
    accounts = Accounts(
        store,
        provenance_gate if gate is None else gate,
        tokens=issuer + oauth.MCP_PATH if issuer else None,
        guard=guard,
        plans=plans,
        saved=arranger.queue if arranger is not None else None,
        sessions=sessions,
        compiles=turns,
    )
    if arranger is not None and guard is not None:  # what it screens a page with, too
        arranger.guard = guard
    filer = Filer(accounts, file_every, turns) if file_every is not None else None  # ValueError
    responses = Responses() if stats_every else None  # Q1: counted only for the line
    stats_log = StatsLog(store, stats_every, responses, stats_wait) if responses else None
    limiter = RateLimiter(clock or _utc_now) if plans is not None else None
    now = clock or _utc_now
    failures = CredentialFailures(lambda: now().timestamp())  # H1: every install has it
    people = (
        signin_routes(sign_in, accounts, public_url, brand, test_instance=test_instance, wall=wall)
        if sign_in
        else []
    )
    oauth_routes = (
        oauth.routes(
            issuer,
            accounts.ledger(),
            _oauth_page,
            _view,
            registrations_per_hour,
            scope=oauth.BRANDED_SCOPE if brand else oauth.SCOPE,
            resource_name=brand.name if brand else oauth.RESOURCE_NAME,
            server=server_name(brand),
        )
        if issuer
        else []
    )
    mcp = build_mcp(brand)
    mcp_app = mcp.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=transport_security(allowed_hosts, public_url),
    )

    public = Public(
        f"{public_url.rstrip('/') if public_url else 'https://<your-host>'}/mcp",
        providers=[provider.label for provider in sign_in.providers] if sign_in else (),
        operator_notes=operator_notes,
        test_instance=test_instance,
    )
    compactor = Compactor(store, compact_every) if compact_every else None
    keeper = Keeper(store, slack.every) if slack is not None else None  # K1
    told = (  # K1b: what a Slack form came to, said once on the Sources page
        SlackOutcomes(slack.sealer(), now, secure, lambda scope: scope.get(MEMORY))
        if slack is not None
        else None
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        # each background task on its own: either, both or neither, stopped in turn
        async with mcp.session_manager.run(), contextlib.AsyncExitStack() as running:
            if sync_every is not None:
                http = await running.enter_async_context(source_http())
                syncing = asyncio.create_task(
                    accounts.sync_sources(sync_every, http=http, env=os.environ)
                )
                running.push_async_callback(_stopped, syncing)
            if compactor is not None:
                compacting = asyncio.create_task(compactor.loop())
                # on the way out, last pushed first: the loop is stopped, then a run ends
                running.push_async_callback(asyncio.to_thread, compactor.wait)
                running.push_async_callback(_stopped, compacting)
            if sweeper is not None:
                sweeping = asyncio.create_task(sweeper.loop())
                # on the way out, last pushed first: the loop is stopped, then a run ends
                running.push_async_callback(asyncio.to_thread, sweeper.stop)
                running.push_async_callback(_stopped, sweeping)
            if arranger is not None and arranger.every:
                arranging = asyncio.create_task(arranger.loop())
                # on the way out, last pushed first: the loop is stopped, then the page in
                # hand gets a bounded wait (a model call can outlast any shutdown)
                running.push_async_callback(asyncio.to_thread, arranger.stop)
                running.push_async_callback(_stopped, arranging)
            if filer is not None:
                filing = asyncio.create_task(filer.loop())
                # on the way out, last pushed first: the loop is stopped, then the note in
                # hand ends, so nothing is written once the app has stopped
                running.push_async_callback(asyncio.to_thread, filer.stop)
                running.push_async_callback(_stopped, filing)
            if google_connect is not None:  # what waits to be revoked at Google, by a timer
                revoker = Revoker(store, google_connect.custody, app.state.source_http)
                revoking = asyncio.create_task(revoker.loop())
                running.push_async_callback(revoker.stopped, revoking)
            if keeper is not None:  # K1: Slack's sweep, and the notes due to be forgotten
                keeping = asyncio.create_task(keeper.loop())
                running.push_async_callback(asyncio.to_thread, keeper.stop)
                running.push_async_callback(_stopped, keeping)
            if stats_log is not None:
                logging_stats = asyncio.create_task(stats_log.loop())
                # on the way out, last pushed first: the loop is stopped, then a pass that is
                # reading the store ends, so nothing reads it once the app has stopped
                running.push_async_callback(asyncio.to_thread, stats_log.stop)
                running.push_async_callback(_stopped, logging_stats)
            yield

    from cairn.web.docs_helper import HelpLink, help_routes

    docs_on = any(v is not None for v in (docs_artifact, docs_release, docs_reader_env))
    routes = [
        *help_routes(
            store.identify_key,
            accounts,
            docs_artifact,
            docs_release,
            docs_reader_env,
            now,
        ),
        Route("/healthz", _healthz(accounts)),
        *people,
        *session_routes(accounts, key_login=sign_in is None, failures=failures),
        *(  # K1b: a signed-in browser's set-up and linking, through Auth as every form; none
            # ends as a source's /integrations/<name>/off, /on, /remove or /preview does
            [
                *setup_routes(
                    told, office=office_of(store), http=slack.http or source_http, api=slack.api
                ),
                *link_routes(
                    told,
                    http=slack.http or source_http,
                    api=slack.api,
                    page_of=_ui,
                    no_page=NO_PAGE,
                ),
            ]
            if slack is not None and told is not None
            else []
        ),
        *ui_routes(public_url, sign_in=sign_in is not None),
        *workspace_routes(own_guard=guard is not None),
        *erasure_routes(),
        *public.routes(),
        *member_routes(accounts, public_url),
        *rest_routes(),
        *(google_routes(google_connect, public_url) if google_connect and public_url else []),
        *(
            slack_routes(
                slack,
                desk=desk_of(store),
                memory_for=lambda account_id, user_id: accounts.memory(
                    Principal(account_id=account_id, kind="session", user_id=user_id), user_id
                ),
                failures=signature_failures(lambda: now().timestamp()),
                now=now,
                public_url=public_url or "",
                http=slack.http or source_http,
            )
            if slack is not None
            else []
        ),
        # a project's graph: GET /api/graph and GET /b/<project>/graph; with the explorer
        # on (G2), its note pages and its two files under /static/, before the Mount
        *graph_routes(explorer=graph_explorer),
        *oauth_routes,
        *account_routes(accounts),
        Route("/mcp", mcp_app),  # an ASGI app on one path: unmatched paths stay with this router
        *(_branded_routes(brand) if brand else []),  # before the files: their places
        Mount(
            "/static",
            branded_static(HERE / "static") if brand else _Static(directory=HERE / "static"),
            name="static",
        ),
    ]
    app = Starlette(
        routes=routes,
        middleware=[
            # Q1: outermost, so every response is counted, Auth's refusals and 429s included
            *([Middleware(CountResponses, responses=responses)] if responses else []),
            *(
                [
                    Middleware(
                        Block,
                        config=google_connect,
                        secure=(public_url or "").lower().startswith("https://"),
                    )
                ]
                if google_connect
                else []
            ),
            *(  # K1b: the Sources page's Slack block, and its outcome said once
                [Middleware(SlackBlock, outcomes=told, public_url=public_url or "")]
                if told is not None
                else []
            ),
            *([Middleware(HelpLink)] if docs_on else []),  # the footer's Help link
            Middleware(
                Auth,
                accounts=accounts,
                public_url=public_url,
                landing=public.landing if sign_in and sign_in.join == "personal" else None,
                limiter=limiter,
                open_paths=(open_paths if slack else frozenset)(
                    frozenset({BACK_PATH} if google_connect else set())
                ),
                routed=lambda scope: any(r.matches(scope)[0] is not Match.NONE for r in routes),
                brand=brand,
                wall=wall,
                failures=failures,
                draft=signed_out_draft,
            ),
        ],
        exception_handlers={
            RequestError: _on_request_error,
            HTTPException: _on_http_error,
            CannotWrite: _on_store_error,
            StoreUnusable: _on_store_error,
        },
        lifespan=lifespan,
        max_body_size=MAX_BODY,
    )
    app.state.source_http = source_http  # R1: a preview reads a source through it
    app.state.compactor = compactor
    app.state.sweeper = sweeper
    app.state.sweep_every = sweep_every
    app.state.filer = filer
    app.state.compiles = turns
    app.state.registrations_per_hour = registrations_per_hour
    app.state.arranger = arranger
    app.state.credential_failures = failures
    app.state.stats_every = stats_every  # Q1: minutes; None when off
    app.state.graph_explorer = graph_explorer  # G2
    app.state.slack_keeper = keeper  # K1: None when off
    if google_connect is not None:  # the key and the client, for every view made from now on
        store.google_custody = google_connect.custody
    if slack is not None:  # K1: the installs' secrets, for every view made from now on
        store.slack_sealer = slack.sealer()
    store.bound_history(keep_versions)  # last: an app refused above changes nothing
    return app
