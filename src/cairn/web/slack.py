"""Slack, both ways (K1): `serve --slack`, the three addresses Slack calls, and the background
loop that sweeps and forgets. What is kept is cairn.store.slack; the checks are
cairn.slack.verify; who answers is cairn.slack.dispatch.

The switch (off unless the operator gives it; decision D11)
- `serve --slack` needs --sign-in (a Slack user is linked to a person who signs in), an
  https:// --public-url (Slack calls this server there) and KEY_VARIABLE, 32 random bytes in
  base64, the key every install's two secrets are sealed with. Without any of them `serve`
  exits 2 before it opens the database, its last line the sentence that names what is
  missing (NEEDS_SIGN_IN, NEEDS_HTTPS, NO_KEY, BAD_KEY), never a value; create_app raises
  ValueError with the same sentence (`check`).
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
  3. A client address (as CredentialFailures counts one) that failed the signature
     SIGNATURE_FAILURES (20) times in the last SIGNATURE_WINDOW (60) seconds is answered 429
     (TOO_MANY, Retry-After) and the signature is not computed, until the oldest failure
     leaves the window.
  4. The timestamp and the signature (cairn.slack.verify.verified, under that install's
     signing secret, chosen by the address): otherwise 401 with UNSIGNED, and one failure
     counted against the address.
  5. The body is read (cairn.slack.verify.envelope): one that is not what Slack sends there
     is 400 (NOT_SLACK); one whose team is not the install's is 403 (OTHER_TEAM). Both carry
     `x-slack-no-retry: 1`, so Slack does not send it again, and nothing is done.
  6. An install that is off acts on a signed `app_uninstalled` or `tokens_revoked`
     (OFF_EVENTS) only; any other signed event, the challenge too, is answered 200 with an
     empty body and nothing is done, its id not even recorded.
  7. Slack's challenge (url_verification, to /events) is answered with its `challenge`, as
     text; an install that is ready becomes on (Store.slack_install_verified).
  8. An event whose event_id the install saw within a day is answered 200 and nothing happens
     again, whatever x-slack-retry-num says (Store.slack_event_seen records it first, so a
     handler that fails is not run again by a retry).
  9. cairn.slack.dispatch answers the rest; this module registers what it owns:
     `app_uninstalled` (Store.remove_slack_install, D10) and `tokens_revoked` naming the
     install's bot (Store.revoke_slack_bot); another user's token revoked changes nothing.
- A refusal writes nothing. Each request is one record on the access log (cairn.access):
  its action (ACTIONS: slack_event, slack_command, slack_action), the install and the
  status, under the actor kind "slack"; never the body, a secret, the signature or a header.
- What verifying costs is counted in statements: one read of the install by its id, then a
  write for a challenge or an event's id, whatever the workspace holds.

The loop (Keeper): started in the app's lifespan with the switch on, a pass at start and then
every `Slack.every` seconds on a worker thread: Store.sweep_slack (offers past their time,
event ids older than a day), then every note whose day to be forgotten came
(Store.slack_notes_due) forgotten with cairn's own forgetting (Store.forget_note, H5b) by a
view of its workspace acting for no person. A pass that fails is logged by its error's class
and the next one runs."""

import argparse
import asyncio
import base64
import binascii
import contextlib
import importlib
import logging
import pkgutil
import re
import threading
from collections.abc import Callable, Mapping
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
from cairn.store.hosted import KEY_BYTES, Sealer
from cairn.store.slack import ACCEPTING
from cairn.web.ratelimit import CredentialFailures
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
REFUSED = "cannot serve Slack: {}"
PREFIX = "/integrations/slack/"
PATH = PREFIX + "{install:slack_install}/{kind:slack_address}"
OPEN_PATH = re.compile(r"/integrations/slack/sl_[0-9a-f]{12}/(?:events|commands|actions)")
ACTIONS = {"events": "slack_event", "commands": "slack_command", "actions": "slack_action"}
SIGNATURE_FAILURES = 20
SIGNATURE_WINDOW = 60  # seconds
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
    `http`, what every call to Slack is made through (None: create_app's `source_http`), and
    `every`, the seconds between the loop's passes."""

    key: bytes = field(repr=False)
    http: Callable[[], httpx.AsyncClient] | None = field(default=None, repr=False)
    every: float = KEEP_EVERY

    def sealer(self) -> Sealer:
        return Sealer(self.key)


def check(slack: Slack, *, sign_in: object, public_url: str | None) -> None:
    """ValueError, with the sentence `serve` says, when the switch cannot work: no sign-in,
    no https public URL, a key that is not KEY_BYTES bytes."""
    if sign_in is None:
        raise ValueError(NEEDS_SIGN_IN)
    if not (public_url or "").lower().startswith("https://"):
        raise ValueError(NEEDS_HTTPS)
    if not isinstance(slack.key, bytes) or len(slack.key) != KEY_BYTES:
        raise ValueError(BAD_KEY)


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
    return Slack(key=key)


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


def signature_failures(clock: Callable[[], float]) -> CredentialFailures:
    """The count of failed signatures per client address (step 3 of the module)."""
    return CredentialFailures(clock, limit=SIGNATURE_FAILURES, window=SIGNATURE_WINDOW)


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
    install a request names, the challenge answered, an event seen, and a view of a workspace
    acting for no person."""

    inbound: Callable[[str], Any]
    verified: Callable[[str], Any]
    seen: Callable[[str, str], bool]
    view: Callable[[str], Any] = field(repr=False)


def desk_of(store: Any) -> Desk:
    return Desk(
        inbound=store.slack_inbound,
        verified=store.slack_install_verified,
        seen=store.slack_event_seen,
        view=lambda account_id: store.for_account(account_id).acting_as("system", None),
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
    failures: CredentialFailures,
    now: Callable[[], datetime],
    public_url: str,
    http: Callable[[], httpx.AsyncClient],
) -> list[Route]:
    """The three addresses (the module). `memory_for(workspace, person)`: a person's Memory
    (Accounts); `failures`: signature_failures(clock); `now`: the app's clock; `http`: what
    calls to Slack are made through."""
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
        address = request.client.host if request.client else None
        if wait := failures.blocked(address):
            _logged(action, install_id, 429)
            return PlainTextResponse(TOO_MANY, 429, headers={"retry-after": str(wait)})
        secret = inbound.signing_secret
        if secret is None or not verified(secret, request.headers, body, now().timestamp()):
            failures.failed(address)
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
        )
        try:
            answer = await dispatch(found, context)
        except Exception as failed:  # its class only: a message can quote what a person wrote
            log.warning("slack: a handler failed: %s", type(failed).__name__)
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
    """The background loop (the module). `run_once()`: one pass, never two at once, none
    once stopped; (rows swept, notes forgotten), or None when it did not run or failed."""

    def __init__(
        self, store: Any, every: float = KEEP_EVERY, wait: Callable[[float], Any] | None = None
    ) -> None:
        self._store = store
        self.every = every
        self.wait = wait or asyncio.sleep
        self._running = threading.Lock()
        self._stopped = threading.Event()

    def run_once(self) -> tuple[int, int] | None:
        if self._stopped.is_set() or not self._running.acquire(blocking=False):
            return None
        try:
            swept = self._store.sweep_slack()
            forgotten = 0
            for account_id, note_id in self._store.slack_notes_due():
                try:
                    self._store.for_account(account_id).forget_note(note_id)
                    forgotten += 1
                except KeyError:  # the note or its workspace is gone, its record with it
                    pass
            return swept, forgotten
        except Exception as failed:  # its class only: a message can quote the database
            log.warning("slack: a pass of the loop failed: %s", type(failed).__name__)
            return None
        finally:
            self._running.release()

    async def loop(self) -> None:
        while True:
            await asyncio.to_thread(self.run_once)
            await self.wait(self.every)

    def stop(self) -> None:
        """No pass starts from now on, and one running ends before this returns."""
        self._stopped.set()
        with self._running:
            pass
