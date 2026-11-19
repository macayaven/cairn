"""Slack, both ways (K1): `serve --slack`, the three addresses Slack calls, and the background
loop that sweeps and forgets. What is kept is cairn.store.slack; the checks are
cairn.slack.verify; who answers is cairn.slack.dispatch.

The switch (off unless the operator gives it; decision D11)
- `serve --slack` needs --sign-in (a Slack user is linked to a person who signs in), an
  https:// --public-url (Slack calls this server there) and KEY_VARIABLE, 32 random bytes in
  base64, the key every install's two secrets are sealed with; beside it, optionally,
  KEY_VARIABLE_PREVIOUS, earlier keys that still open what they sealed
  (cairn.store.hosted.previous_keys). Without any of them, or with an earlier key malformed
  or equal to the current one, `serve` exits 2 before it opens the database, its last line
  the sentence that names what is missing or wrong (NEEDS_SIGN_IN, NEEDS_HTTPS, NO_KEY,
  BAD_KEY), never a value; create_app raises ValueError with the same sentence (`check`).
  Under a brand (`--brand-name`, B1) it is not offered yet, as what the bot says, the
  shortcut's name and the command's name would name the software: `serve` stops the same
  way and create_app raises, with UNDER_A_BRAND.
- Off (create_app without `slack`): none of the addresses exists (each is 404 as any address
  that is not a page), nothing is shown about Slack and no request reads its tables. The
  schema step is there all the same, and so are the store's duties: deleting a workspace,
  removing a person, deleting a project and forgetting a note take their Slack rows with
  them whether the switch is on or not.

The three addresses Slack calls (`slack_routes`)
- POST /integrations/slack/<install>/events, /commands and /actions (PATH), <install> an
  install's id (cairn.store.slack.INSTALL_ID). They are open paths of Auth (`open_paths`): no
  session, no key, no rate count, no same-origin check; what proves a request is Slack's
  signature. Every protection is here, in this order, and nothing of the body is parsed
  before the signature holds:
  1. The body is read with a limit: over 1 MiB is 413 (TOO_LARGE).
  2. An install that does not exist, is pending or is removed is 404 with the page every
     address that is not a page gets. One that is off (switched off, or its bot token
     revoked: its signing secret is held either way) answers at /events only; its /commands
     and /actions are 404 the same way.
  3. The timestamp and the signature (cairn.slack.verify.verified, under that install's
     signing secret, chosen by the address), computed for every request that comes this
     far. A request whose signature holds goes on, answered exactly as if no failure had
     ever been counted, at this install or any other, from this address or any other.
  4. A request whose signature fails is counted against its (install, client address)
     pair (SignatureFailures, never the address alone: failures at one install count
     nothing at another) and answered 401 (UNSIGNED); once that pair had
     SIGNATURE_FAILURES (20) failures in the last SIGNATURE_WINDOW (60) seconds, a further
     one is answered 429 (TOO_MANY, Retry-After) and not counted, until the oldest leaves
     the window. What the count keeps is bounded (SIGNATURE_KEYS pairs).
  5. The body is read (cairn.slack.verify.envelope): one that is not what Slack sends there
     is 400 (NOT_SLACK); one whose team is not the install's is 403 (OTHER_TEAM). Both carry
     `x-slack-no-retry: 1`, so Slack does not send it again, and nothing is done.
  6. An install that is off acts on a signed `app_uninstalled` or `tokens_revoked`
     (OFF_EVENTS) only; any other signed event, the challenge too, is answered 200 with an
     empty body and nothing is done, its id not even recorded.
  7. Slack's challenge (url_verification, to /events) is answered with its `challenge`, as
     text; an install that is ready becomes on (Store.slack_install_verified).
  8. An event whose event_id the install saw within a day is answered 200 and nothing happens
     again, whatever x-slack-retry-num says. Store.slack_event_seen records the id before
     the handler runs, so two deliveries at once run it once. A handler that raises is
     answered 500 and its event's id forgotten (Store.forget_slack_event; a forgetting that
     fails too is logged by its class, and the answer is still 500), so Slack's next
     delivery of it runs the handler again; one that returned is not run again.
  9. cairn.slack.dispatch answers the rest; this module registers what it owns:
     `app_uninstalled` (Store.remove_slack_install, D10) and `tokens_revoked` naming the
     install's bot (Store.revoke_slack_bot); another user's token revoked changes nothing.
- A refusal writes nothing. Each request is one record on the access log (cairn.access):
  its action (ACTIONS: slack_event, slack_command, slack_action), the install and the
  status, under the actor kind "slack"; never the body, a secret, the signature or a header.
- What verifying costs is counted in statements: one read of the install by its id, then a
  write for a challenge or an event's id (and one more to forget the id of an event whose
  handler failed), whatever the workspace holds.

The loop (Keeper): started in the app's lifespan with the switch on, a pass at start and then
every `Slack.every` seconds. First, on the event loop, Slack is asked whether each bot token
still works (auth.test, at most once every 6 hours for one install, at most 20 installs a
pass, each within 5 seconds): two answers in a row that say Slack ended it do what
tokens_revoked does, and anything else changes nothing. Then, on a worker thread:
Store.sweep_slack (offers past their time, event ids older than a day, claims never filed),
every install whose bot token Slack ended more than 3 days ago removed as an owner removes
it (Store.remove_gone_slack_installs), and every note whose day to be forgotten came
(Store.slack_notes_due) forgotten with cairn's own forgetting (Store.forget_note, H5b) by a
view of its workspace acting for no person. A check, a note or a pass that fails is logged by
its error's class and the next one is done."""

import argparse
import asyncio
import base64
import binascii
import contextlib
import importlib
import logging
import math
import pkgutil
import re
import threading
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.convertors import Convertor, register_url_convertor
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

import cairn.slack
from cairn.memory import Memory, access_log
from cairn.oauth import _network as network  # the sign-in limit's rule for an address
from cairn.slack.client import API, REPLY_HOSTS, SlackClient, SlackError
from cairn.slack.dispatch import Context, dispatch, on_event
from cairn.slack.verify import (
    CHALLENGE,
    NotSlack,
    OtherTeam,
    TooLarge,
    envelope,
    read_body,
    verified,
)
from cairn.store.hosted import KEY_BYTES, Sealer, previous_keys
from cairn.store.slack import ACCEPTING, ENDED
from cairn.web.brand import product_of
from cairn.web.templating import NO_PAGE, _page, brand_of

OFF_EVENTS = ("app_uninstalled", "tokens_revoked")  # what an install that is off acts on
log = logging.getLogger("cairn.slack")
KEY_VARIABLE = "CAIRN_SLACK_KEY"
NEEDS_SIGN_IN = "--slack needs --sign-in: a Slack user is linked to a person who signs in here"
NEEDS_HTTPS = (
    "--slack needs an https:// --public-url: Slack calls this server at"
    " <public url>/integrations/slack/<install>/events, /commands and /actions"
)
NO_KEY = (
    f"--slack needs {KEY_VARIABLE}, {KEY_BYTES} random bytes in base64 (openssl rand -base64"
    f" {KEY_BYTES}): the key every install's two secrets are sealed with"
)
BAD_KEY = f"{KEY_VARIABLE} is not {KEY_BYTES} bytes in base64"
BAD_PREVIOUS = (
    f"{KEY_VARIABLE}_PREVIOUS holds a key that is not {KEY_BYTES} bytes or is {KEY_VARIABLE}'s"
)
UNDER_A_BRAND = (
    "Slack is not offered under a brand yet: what the bot says, the shortcut's name and the"
    " command's name would name the software (leave out --slack or --brand-name)"
)
REFUSED = "cannot serve Slack: {}"
PREFIX = "/integrations/slack/"
PATH = PREFIX + "{install:slack_install}/{kind:slack_address}"
OPEN_PATH = re.compile(r"/integrations/slack/sl_[0-9a-f]{12}/(?:events|commands|actions)")
ACTIONS = {"events": "slack_event", "commands": "slack_command", "actions": "slack_action"}
SIGNATURE_FAILURES = 20
SIGNATURE_WINDOW = 60  # seconds
SIGNATURE_KEYS = 10_000  # (install, client address) pairs whose recent failures are kept
KEEP_EVERY = 60.0  # seconds between the loop's passes
TOO_LARGE = "This request is larger than Slack sends."
UNSIGNED = "This request is not signed by Slack for this address."
TOO_MANY = "Too many unsigned requests came from your address in the last minute."
NOT_SLACK = "This is not a request Slack sends."
OTHER_TEAM = "This request is not of this install's Slack team."
NO_RETRY = {"x-slack-no-retry": "1"}


@dataclass(frozen=True)
class Slack:
    """The switch, on: the key the installs' secrets are sealed with (never in a repr),
    `http`, what every call to Slack is made through (None: create_app's `source_http`),
    `every`, the seconds between the loop's passes, and where Slack is: `api`, the base of its
    Web API, and `reply_hosts`, the hosts a reply address may name (cairn.slack.client). Both
    are Slack's own unless a test says a stand-in's; `serve` has no option for either."""

    key: bytes = field(repr=False)
    http: Callable[[], httpx.AsyncClient] | None = field(default=None, repr=False)
    every: float = KEEP_EVERY
    api: str = API
    reply_hosts: frozenset[str] = REPLY_HOSTS
    # earlier keys, from KEY_VARIABLE_PREVIOUS: what they sealed still opens (never in a repr)
    previous: tuple[bytes, ...] = field(default=(), repr=False)

    def sealer(self) -> Sealer:
        return Sealer(self.key, self.previous)


def check(
    slack: Slack, *, sign_in: object, public_url: str | None, brand_name: str | None = None
) -> None:
    """ValueError, with the sentence `serve` says, when the switch cannot work: a brand
    (UNDER_A_BRAND), no sign-in, no https public URL, a key that is not KEY_BYTES bytes, an
    earlier key that is not or that is the current one (BAD_PREVIOUS)."""
    if brand_name is not None:
        raise ValueError(UNDER_A_BRAND)
    if sign_in is None:
        raise ValueError(NEEDS_SIGN_IN)
    if not (public_url or "").lower().startswith("https://"):
        raise ValueError(NEEDS_HTTPS)
    if not isinstance(slack.key, bytes) or len(slack.key) != KEY_BYTES:
        raise ValueError(BAD_KEY)
    try:  # the earlier keys, as previous_keys reads them
        slack.sealer()
    except ValueError:
        raise ValueError(BAD_PREVIOUS) from None


def read_switch(environ: Mapping[str, str]) -> Slack:
    """The switch from the environment; ValueError naming the variable, never its value."""
    raw = environ.get(KEY_VARIABLE, "").strip()
    if not raw:
        raise ValueError(NO_KEY)
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != KEY_BYTES:
        raise ValueError(BAD_KEY)
    return Slack(key=key, previous=previous_keys(environ, KEY_VARIABLE, key))


def add_options(serve: argparse.ArgumentParser) -> None:
    serve.add_argument(
        "--slack",
        action="store_true",
        help=f"with --sign-in and an https:// --public-url: one Slack app for each workspace,"
        f" set up by its owner on the Sources page; the sealing key comes from {KEY_VARIABLE}",
    )


def serve_switch(args: argparse.Namespace, environ: Mapping[str, str]) -> Slack | None:
    """What `serve` gives create_app, read before anything is opened; ValueError otherwise."""
    if not getattr(args, "slack", False):
        return None
    if getattr(args, "brand_name", None) is not None:
        raise ValueError(UNDER_A_BRAND)
    if not args.sign_in:
        raise ValueError(NEEDS_SIGN_IN)
    if not (args.public_url or "").lower().startswith("https://"):
        raise ValueError(NEEDS_HTTPS)
    return read_switch(environ)


class _InstallId(Convertor):
    regex = "sl_[0-9a-f]{12}"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


class _Address(_InstallId):
    regex = "events|commands|actions"


register_url_convertor("slack_install", _InstallId())
register_url_convertor("slack_address", _Address())


class _OpenPaths(frozenset):
    """Auth's open paths, and the three addresses of every install (OPEN_PATH)."""

    def __contains__(self, path: object) -> bool:
        return super().__contains__(path) or (
            isinstance(path, str) and OPEN_PATH.fullmatch(path) is not None
        )


def open_paths(paths: frozenset[str]) -> frozenset[str]:
    """`paths`, with the three addresses of every install."""
    return _OpenPaths(paths)


class SignatureFailures:
    """The requests that failed the signature at Slack's addresses (step 3 of the module), in
    this process's memory, counted per (install, client address) and never per address
    alone, so failures at one install count nothing at another. `failed(install, address)`
    takes one failure: 0 when it is counted (the request is answered 401), or, when the pair
    had `limit` counted in the last `window` seconds, the whole seconds (at least 1) until
    the oldest of them leaves the window (the request is answered 429, and is not counted).
    Only a request whose signature failed is ever given here: a request whose signature
    holds is answered as if nothing had been counted.

    The address is read as the sign-in limit reads one (cairn.oauth's rule, as
    CredentialFailures): one IPv6 /64 is one address, and requests without a usable address
    share one. What is kept is bounded whatever arrives: at most `keys` pairs, each with at
    most `limit` times, so at most keys x limit times in all (10,000 x 20 by default); when
    the table is full, the pair forgotten is one not at its limit, the one seen longest ago of
    those; a pair being refused is forgotten only when every pair is, and then the one seen
    longest ago. A new one is never refused. Choosing reads the pairs seen longest ago first
    and stops at the first not at its limit: at most `keys` of them. The counts start again
    when the server restarts. The
    time is `clock()` in seconds (the app's clock), never earlier than a pair's last failure,
    so a clock that steps back frees nothing. Thread-safe."""

    def __init__(
        self,
        clock: Callable[[], float],
        *,
        limit: int = SIGNATURE_FAILURES,
        window: float = SIGNATURE_WINDOW,
        keys: int = SIGNATURE_KEYS,
    ) -> None:
        if limit < 1 or not window > 0 or keys < 1:
            raise ValueError("a failure limit needs a limit, a window and room of at least 1")
        self._clock, self._limit, self._window = clock, limit, window
        self.keys = keys
        self._failures: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def failed(self, install: str, address: str | None) -> int:
        key = (install, network(address))
        with self._lock:
            failures = self._failures.pop(key, None) or deque()
            self._failures[key] = failures  # now the most recent
            while len(self._failures) > self.keys:
                del self._failures[self._victim(key, self._clock())]
            now = max(self._clock(), failures[-1]) if failures else self._clock()
            while failures and failures[0] <= now - self._window:
                failures.popleft()
            if len(failures) >= self._limit:
                return max(1, math.ceil(failures[0] + self._window - now))
            failures.append(now)
            return 0

    def _over(self, failures: deque[float], now: float) -> bool:
        """Whether a pair with `failures` is at its limit at `now` (it would be refused). A
        pair holds at most `limit` times, oldest first."""
        return len(failures) >= self._limit and failures[-self._limit] > now - self._window

    def _victim(self, key: tuple[str, str], now: float) -> tuple[str, str]:
        """The pair to forget, never `key`: the one seen longest ago that is not at its
        limit, else the one seen longest ago."""
        oldest = None
        for pair, failures in self._failures.items():  # seen longest ago first
            if pair == key:
                continue
            if not self._over(failures, now):
                return pair
            oldest = oldest or pair
        assert oldest is not None  # more than one is kept, so another than `key` is
        return oldest

    def tracked(self) -> int:
        """How many (install, address) pairs are kept."""
        with self._lock:
            return len(self._failures)

    def held(self) -> int:
        """How many failures' times are kept, over every pair."""
        with self._lock:
            return sum(len(failures) for failures in self._failures.values())


def signature_failures(clock: Callable[[], float]) -> SignatureFailures:
    """The count of failed signatures per (install, client address) (step 3 of the module)."""
    return SignatureFailures(clock)


class OneClient:
    """The one httpx client the handlers send to Slack through (the author lookup, the
    answer through the address Slack gave, the bot's message): made by `make` (the switch's
    `http`, or create_app's `source_http`) once. The app makes it at start (`open`, in its
    lifespan) and closes it at shutdown (`aclose`); a call before any start (a test's client
    that never starts the app) makes it then, once. Calling it gives that client: what
    cairn.slack.dispatch.Context.http is, so no handler makes a client of its own. The set-up
    and link pages keep their own short-lived clients."""

    def __init__(self, make: Callable[[], httpx.AsyncClient]) -> None:
        self._make = make
        self._client: httpx.AsyncClient | None = None

    def __repr__(self) -> str:
        return "OneClient()"

    def __call__(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._make()
        return self._client

    def open(self) -> httpx.AsyncClient:
        return self()

    async def aclose(self) -> None:
        made, self._client = self._client, None
        if made is not None:
            await made.aclose()


def _logged(action: str, install: str, status: int) -> None:
    with contextlib.suppress(Exception):  # a log handler that raises fails no request
        access_log.info(
            "%s for %s: %s",
            action,
            install,
            status,
            extra={
                "actor_kind": "slack",
                "actor": None,
                "actor_id": None,
                "action": action,
                "project": None,
                "status": status,
                "install": install,
            },
        )


@dataclass(frozen=True)
class Desk:
    """What the addresses get of the store (as Accounts.ledger hands OAuth its part): the
    install a request names, the challenge answered, an event seen, an event forgotten again
    (its handler failed), and a view of a workspace acting for no person."""

    inbound: Callable[[str], Any]
    verified: Callable[[str], Any]
    seen: Callable[[str, str], bool]
    view: Callable[[str], Any] = field(repr=False)
    forget: Callable[[str, str], Any] = field(repr=False)


def desk_of(store: Any) -> Desk:
    return Desk(
        inbound=store.slack_inbound,
        verified=store.slack_install_verified,
        seen=store.slack_event_seen,
        view=lambda account_id: store.for_account(account_id).acting_as("system", None),
        forget=lambda install_id, event_id: store.forget_slack_event(install_id, event_id),
    )


def _import_handlers() -> None:
    """Every module of the cairn.slack package, so each registers its handlers."""
    for module in pkgutil.iter_modules(cairn.slack.__path__):
        importlib.import_module(f"{cairn.slack.__name__}.{module.name}")


def slack_routes(
    config: Slack,
    *,
    desk: Desk,
    memory_for: Callable[[str, str], Memory | None],
    failures: SignatureFailures,
    now: Callable[[], datetime],
    public_url: str,
    http: Callable[[], httpx.AsyncClient],
    turn: Callable[[], AbstractAsyncContextManager[Any]] = contextlib.nullcontext,
) -> list[Route]:
    """The three addresses (the module). `memory_for(workspace, person)`: a person's Memory
    (Accounts); `failures`: signature_failures(clock); `now`: the app's clock; `http`: what
    gives the client calls to Slack are made through (create_app's OneClient); `turn`: one
    turn among the server's compiles (create_app's Compiles.turn), which a note written from
    Slack holds (cairn.slack.people.written)."""
    _import_handlers()

    def no_page(request: Request) -> Response:
        return _page("error.html", 404, brand=brand_of(request), status=404, message=NO_PAGE)

    async def receive(request: Request) -> Response:
        kind, install_id = request.path_params["kind"], request.path_params["install"]
        action = ACTIONS[kind]
        try:
            body = await read_body(request)
        except TooLarge:
            _logged(action, install_id, 413)
            return PlainTextResponse(TOO_LARGE, 413)
        inbound = await run_in_threadpool(desk.inbound, install_id)
        if inbound is None or (
            inbound.install.state not in ACCEPTING
            and (inbound.install.state != "off" or kind != "events")
        ):
            _logged(action, install_id, 404)
            return no_page(request)
        secret = inbound.signing_secret
        if secret is None or not verified(secret, request.headers, body, now().timestamp()):
            address = request.client.host if request.client else None
            if wait := failures.failed(install_id, address):
                _logged(action, install_id, 429)
                return PlainTextResponse(TOO_MANY, 429, headers={"retry-after": str(wait)})
            _logged(action, install_id, 401)
            return PlainTextResponse(UNSIGNED, 401)
        install = inbound.install
        try:
            found = envelope(kind, install, body, request.headers)
        except OtherTeam:
            _logged(action, install_id, 403)
            return PlainTextResponse(OTHER_TEAM, 403, headers=NO_RETRY)
        except NotSlack:
            _logged(action, install_id, 400)
            return PlainTextResponse(NOT_SLACK, 400, headers=NO_RETRY)
        if install.state == "off" and (found.type == CHALLENGE or found.name not in OFF_EVENTS):
            _logged(action, install_id, 200)  # off: acknowledged, and nothing is done
            return Response(status_code=200)
        if found.type == CHALLENGE:
            await run_in_threadpool(desk.verified, install.id)
            _logged(action, install_id, 200)
            return PlainTextResponse(found.payload["challenge"])
        if found.event_id is not None and not await run_in_threadpool(
            desk.seen, install.id, found.event_id
        ):
            _logged(action, install_id, 200)  # done once already
            return Response(status_code=200)
        view = await run_in_threadpool(desk.view, install.account_id)
        context = Context(
            install=install,
            view=view,
            memory_for=lambda user_id: memory_for(install.account_id, user_id),
            http=http,
            public_url=public_url,
            now=now,
            api=config.api,
            reply_hosts=config.reply_hosts,
            turn=turn,
            product=product_of(brand_of(request)).name,
        )
        try:
            answer = await dispatch(found, context)
        except Exception as failed:  # its class only: a message can quote what a person wrote
            log.warning("slack: a handler failed: %s", type(failed).__name__)
            if found.event_id is not None:  # so that Slack's next delivery runs it again
                try:
                    await run_in_threadpool(desk.forget, install.id, found.event_id)
                except Exception as unforgotten:
                    log.warning(
                        "slack: a failed event was not forgotten: %s", type(unforgotten).__name__
                    )
            _logged(action, install_id, 500)
            return Response(status_code=500)
        _logged(action, install_id, answer.status_code)
        return answer

    return [Route(PATH, receive, methods=["POST"])]


# What this part answers itself (D10)


@on_event("app_uninstalled")
async def _uninstalled(found: Any, context: Context) -> None:
    """Slack removed the app: the install is removed (Store.remove_slack_install)."""
    await run_in_threadpool(context.view.remove_slack_install, context.install.id)


@on_event("tokens_revoked")
async def _revoked(found: Any, context: Context) -> None:
    """Slack revoked tokens: when the bot's is among them, the install is switched off and its
    bot token deleted (Store.revoke_slack_bot); a person's token alone changes nothing."""
    tokens = found.payload["event"].get("tokens")
    bots = tokens.get("bot") if isinstance(tokens, dict) else None
    if isinstance(bots, list) and context.install.bot_user_id in bots:
        await run_in_threadpool(context.view.revoke_slack_bot, context.install.id)


# The loop


class Keeper:
    """The background loop (the module). `check()`: on the event loop, before each pass, the
    installs whose bot token is due a check (Store.take_slack_checks: those that hold one, at
    most once every CHECK_EVERY for one install, at most CHECKS_AT_ONCE in one pass) are each
    asked of Slack with auth.test (SlackClient.auth_test), through one short-lived client from
    `http` at `api`, within CHECK_WITHIN seconds in all and a 429 not waited out; what Slack
    said goes to Store.slack_token_checked: an answer in ENDED is "ended", a good one "works",
    anything else (a timeout, no connection, 429, a 5xx, another error, not JSON) "unclear".
    How many were asked; without `http`, none. `run_once()`: one pass on a worker thread,
    never two at once, none once stopped: Store.sweep_slack, Store.remove_gone_slack_installs,
    then every note due forgotten; (rows swept, notes forgotten), or None when it did not run
    or failed. A check or a note that fails is logged by its error's class and the next one is
    done; no token, note text or database text is in a log line."""

    CHECK_WITHIN = 5.0  # seconds one check of a bot token takes in all

    def __init__(
        self,
        store: Any,
        every: float = KEEP_EVERY,
        wait: Callable[[float], Any] | None = None,
        *,
        http: Callable[[], httpx.AsyncClient] | None = None,
        api: str = API,
    ) -> None:
        self._store = store
        self.every = every
        self.wait = wait or asyncio.sleep
        self._http = http
        self._api = api
        self._running = threading.Lock()
        self._stopped = threading.Event()

    async def check(self) -> int:
        if self._http is None or self._stopped.is_set():
            return 0
        try:
            return await self._check()
        except Exception as failed:  # its class only: a message can quote the database
            log.warning("slack: the checks of bot tokens failed: %s", type(failed).__name__)
            return 0

    async def _check(self) -> int:
        asked = 0
        due = await asyncio.to_thread(self._held, self._store.take_slack_checks)
        if not due:
            return asked
        async with self._http() as client:  # type: ignore[misc]
            slack = SlackClient(client, api=self._api, sleep=_no_wait)
            for check in due:
                if self._stopped.is_set():
                    break
                try:
                    answer = await self._ask(slack, check.token)
                    done = await asyncio.to_thread(
                        self._held, self._store.slack_token_checked, check, answer
                    )
                    asked += done is not None
                except Exception as failed:  # its class only
                    log.warning("slack: a check of a bot token failed: %s", type(failed).__name__)
        return asked

    def _held(self, call: Callable[..., Any], *args: Any) -> Any:
        """`call` on this worker thread while holding the pass's lock, so stop() waits for
        it; None, and nothing called, once stopped."""
        with self._running:
            return None if self._stopped.is_set() else call(*args)

    async def _ask(self, slack: SlackClient, token: str) -> str:
        try:
            async with asyncio.timeout(self.CHECK_WITHIN):
                await slack.auth_test(token)
        except SlackError as said:
            return "ended" if said.code in ENDED else "unclear"
        except TimeoutError:
            return "unclear"
        return "works"

    def run_once(self) -> tuple[int, int] | None:
        if self._stopped.is_set() or not self._running.acquire(blocking=False):
            return None
        try:
            swept = self._store.sweep_slack()
            self._store.remove_gone_slack_installs()
            forgotten = 0
            for account_id, note_id in self._store.slack_notes_due():
                try:
                    self._store.for_account(account_id).forget_note(note_id)
                    forgotten += 1
                except KeyError:  # the note or its workspace is gone, its record with it
                    pass
                except Exception as failed:  # its class only; the others are still forgotten
                    log.warning("slack: a note was not forgotten: %s", type(failed).__name__)
            return swept, forgotten
        except Exception as failed:  # its class only: a message can quote the database
            log.warning("slack: a pass of the loop failed: %s", type(failed).__name__)
            return None
        finally:
            self._running.release()

    async def loop(self) -> None:
        while True:
            await self.check()  # never raises: what fails is logged by its class
            await asyncio.to_thread(self.run_once)
            await self.wait(self.every)

    def stop(self) -> None:
        """No pass starts from now on, and one running ends before this returns."""
        self._stopped.set()
        with self._running:
            pass


async def _no_wait(seconds: float) -> None:
    """A check does not wait out a 429: it is unclear, and asked again in CHECK_EVERY."""
    raise SlackError("rate_limited")
