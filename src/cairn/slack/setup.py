"""The browser's side of a workspace's Slack install (K1, decisions D1, D10 and D11): set up,
the two secrets, off, on, remove and "forget them now", on the Sources page. What is kept is
cairn.store.slack; the addresses Slack calls are cairn.web.slack; linking a Slack user to a
person is cairn.slack.link.

The addresses (`setup_routes`, only with `serve --slack`)
- POST SETUP_PATH, SECRETS_PATH, PAUSE_PATH (off), RESUME_PATH (on), DISCONNECT_PATH (remove)
  and FORGET_PATH: forms of a signed-in browser. No address here, nor cairn.slack.link's,
  ends as a source's own does (/integrations/<source>/off, /on, /remove, /preview), so a
  source named "slack" keeps its own. They go through Auth as every form does (a session,
  the same-origin check, the rate count). Each is an owner's or an admin's (Store.manages,
  as the Members and Keys pages ask it): anyone else is refused 403 with NOT_A_MANAGER and
  nothing is read or asked of Slack; FORGET_PATH is an owner's only
  (cairn.store.slack.NOT_AN_OWNER).
- Set up (Store.make_slack_install): the workspace's install, pending, or the one it has. The
  block then shows the link that opens Slack's "create an app" page with this install's
  manifest filled in (cairn.slack.manifest.manifest_link of build_manifest, with this
  server's public URL and the install's id), the same manifest as JSON text, what each of the
  four permissions is for (PERMISSIONS) and two password fields, never filled back in.
- The secrets: both trimmed and of the shape Slack shows (cairn.store.slack.SECRET_VALUE),
  else BLANK; then Slack is asked whose token it is (auth.test through
  cairn.slack.client.SlackClient, within CHECK_WITHIN seconds). Slack refusing it is
  not_accepted; a Slack that does not answer is unreachable; an answer with no bot is
  not_a_bot; one that does not name its team, its workspace's address (`team_domain`) and the
  bot's user (`identity`) is unclear; a team for which another workspace of this server holds
  an install that is ready, on or off (`Office.team_held`, and the store's own index) is
  taken; a team other than the one this install holds while it is ready, on or off is
  other_team (cairn.store.slack.NotItsTeam), which says to remove Slack first. Each is one
  sentence (REFUSALS) and keeps nothing. Otherwise
  Store.ready_slack_install records the team and seals both secrets. When Slack revoked the
  bot's token (SlackInstall.needs_bot_token), only the token is asked for and the signing
  secret kept is used (`Office.kept_secret`); when it does not open (the key changed), that
  is said (unopened), and removing Slack and setting it up again asks for both. A secret
  goes from the form to auth.test and to the store and nowhere else: not into a cookie, a
  log line, an outcome or a page.
- Off and on (Store.switch_slack_install): on again needs nothing pasted, unless Slack revoked
  the token (needs_token). Remove (Store.remove_slack_install, decision D10) needs the
  workspace's name typed as a check (confirm); next to it the block says that removing
  forgets, 10 days later, every note Slack created in the workspace, in projects the person
  removing does not reach too. A team made ready elsewhere between the check and the write is
  refused by the store (TeamHeld), and said as taken. Forget now: Store.forget_slack_notes_now.
- Each answer is 303 to the Sources page (ANCHOR), its outcome a code in a cookie sealed with
  the switch's key and OUTCOME as associated data, for its person and workspace, for SAID_TTL
  seconds by the app's clock (`Outcomes`): the block says it once (SAID, or REFUSALS in
  role="alert") and the page clears it. A crafted address says nothing.
- Each request is one record on the access log (ACTION, `logged`): who, the install and the
  outcome, never a value of the form. A write is one record on the audit log, the store's.

The block (templates/_slack.html, `Block`, `block_for`): nothing unless the switch is on and
the reader is a person. An owner or an admin sees the install's state and what is left to do
(STATES), the forms that state takes, and, while notes that came through a removed install
wait to be forgotten, how many and the day, with "Forget them now" for an owner while the
install is removed. While Slack no longer accepts the bot token (SlackInstall.bot_gone_at,
cairn.store.slack), it says since when, the day by which a new one must be pasted, and that
after that day Slack is removed here and the notes Slack created are forgotten 10 days
later. Anyone else sees only an outcome said to them (cairn.slack.link). Every person,
whatever their role, is shown their own links (Store.own_slack_links), each Slack user by
its id with "Unlink" (cairn.slack.link.UNLINK_PATH), and nobody else's.

The web layer depends on this package, never the other way round (tests/unit/test_layers_unit):
what this module needs of it is handed in by cairn.web.app, `memory_in` (the request's Memory
as Auth put it on the scope) and the template global `slack_block` (`block`)."""

import contextlib
import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, Headers, MutableHeaders
from starlette.requests import Request, cookie_parser
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cairn.memory import Forbidden, Memory, RequestError, access_log
from cairn.slack.client import SlackClient, SlackError
from cairn.slack.manifest import SCOPES, build_manifest, manifest_link
from cairn.store.hosted import Sealer
from cairn.store.slack import (
    DOMAIN,
    GONE_AFTER,
    NOT_AN_OWNER,
    SECRET_VALUE,
    SLACK_ID,
    NotItsTeam,
    SlackInstall,
    TeamHeld,
)

TEMPLATES = Path(__file__).parents[1] / "templates"
PREFIX = "/integrations/slack"
SETUP_PATH = f"{PREFIX}/setup"
SECRETS_PATH = f"{PREFIX}/secrets"
# Off, on and remove: not /off, /on and /remove, which are a source named "slack"'s own
# (cairn.web.ui serves /integrations/<source>/off, /on, /remove and /preview).
PAUSE_PATH = f"{PREFIX}/pause"
RESUME_PATH = f"{PREFIX}/resume"
DISCONNECT_PATH = f"{PREFIX}/disconnect"
FORGET_PATH = f"{PREFIX}/forget"
ACTION = "slack_setup"  # the access log's action
ANCHOR = "/sources#slack"
SAID_COOKIE = "slack_said"
SAID_PATH = "/sources"
SAID_TTL = 300  # seconds from the handler to the page
OUTCOME = json.dumps(["slack-outcome"]).encode()
CHECK_WITHIN = 10.0  # seconds auth.test may take in all
SLACK_HOSTS = ("slack.com", "slack-gov.com")  # whose subdomains name a Slack workspace
UNREACHABLE = frozenset({"timeout", "rate_limited", "http_error", "not_json"})
NOT_A_MANAGER = "Only an owner or an admin of this workspace can set Slack up, switch or remove it."
PERMISSIONS = {  # what each scope of the manifest (cairn.slack.manifest.SCOPES) is for
    "chat:write": "Post the bot's answers, to the person who asked.",
    "commands": "Receive the slash command and the message shortcut a person uses.",
    "im:history": (
        "Read the direct messages people send the bot, so it can answer them, and nothing"
        " among people: no conversation between people is read."
    ),
    "users:read": (
        "Know a Slack user's name, and whether a message's author is of this Slack workspace"
        " before it is filed."
    ),
}
CHANGED_AT_SLACK = (  # said with the secrets form while Slack is on or off
    "If the signing secret or the bot token was changed at Slack, paste both here again. A"
    " signing secret other than the one kept has to be proven anew: Slack is then ready, and"
    " on once Slack verifies the events address again. Pasted while Slack is off, they"
    " switch it on again."
)
STATES = {  # the install's state, and what is left to do
    "pending": (
        "Set up started. Create the app at Slack from the link below, install it to your Slack"
        " workspace, then paste its signing secret (Basic Information) and its bot token"
        " (OAuth & Permissions) here."
    ),
    "ready": (
        "Ready: Slack accepted the bot token. The signing secret is not proven yet: only a"
        " request that Slack signed can prove it. Left to do: in the app's settings at Slack,"
        " have Slack verify the events address below. If Slack reports that the address did"
        " not answer, either Slack cannot reach this server or the signing secret pasted here"
        " is not this app's: check the address, then paste the secret again."
    ),
    "on": (
        "On: Slack verified the events address, so a request signed with this app's signing"
        " secret was verified, and the slash command, the message shortcut and direct messages"
        " to the bot are answered."
    ),
    "off": (
        "Off: commands and actions are refused, and nothing is deleted. Switch it on again to"
        " answer them; nothing needs to be pasted."
    ),
    "revoked": (
        "Off, because Slack revoked the bot's token. Paste the new bot token from the app's"
        " OAuth & Permissions page to switch it on again; the signing secret is kept."
    ),
    "removed": (
        "Removed: the signing secret, the bot token and every link between a Slack user and a"
        " person were deleted."
    ),
}
SAID = {  # what the block says after a form, by the code in the outcome
    "made": "Set up started: create the app at Slack, then paste its two secrets here.",
    "ready": "Slack accepted the bot token: both secrets are kept, sealed, and never shown again.",
    "off": "Switched off: nothing was deleted.",
    "on": "Switched on.",
    "removed": "Removed.",
    "forgotten": "The notes that came through Slack were forgotten.",
}
REFUSALS = {  # each one sentence; none quotes what was typed
    "none": "Set Slack up first; nothing was kept.",
    "blank": "Paste the signing secret and the bot token as Slack shows them; nothing was kept.",
    "not_accepted": "Slack did not accept this bot token, so nothing was kept.",
    "unreachable": "Slack could not be reached, so nothing was kept; try again later.",
    "not_a_bot": "This is not the app's bot token, so nothing was kept.",
    "unclear": (
        "Slack's answer did not name its workspace and its bot in a way this server can check,"
        " so nothing was kept."
    ),
    "taken": (
        "Another workspace on this server has Slack set up for this Slack workspace already, so"
        " nothing was kept."
    ),
    "other_team": (
        "This bot token is of another Slack workspace than the one set up here: to connect"
        " that one, remove Slack first; nothing was kept."
    ),
    "unkept": "This server could not keep these secrets, so nothing was kept.",
    "unopened": (
        "The signing secret kept here no longer opens, as this server's key changed: remove"
        " Slack and set it up again, so that both are asked for; nothing was kept."
    ),
    "needs_token": "Slack revoked the bot's token: paste the new one to switch Slack on again.",
    "confirm": "Type the workspace's name to remove Slack, as a check; nothing was changed.",
    "state": "That cannot be done while Slack is in this state; nothing was changed.",
}


# What Slack's answer to auth.test must hold


def team_domain(url: object) -> str | None:
    """The workspace's domain from the address Slack gives for it ("https://<domain>.slack.com/"),
    or None when it is not one of Slack's (SLACK_HOSTS) over https with one label before."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username is not None or parts.port is not None:
        return None
    for suffix in SLACK_HOSTS:
        label = host.removesuffix(f".{suffix}")
        if label != host and "." not in label and DOMAIN.fullmatch(label):
            return label
    return None


@dataclass(frozen=True)
class Identity:
    """Whose token it is, as auth.test says: what Store.ready_slack_install records."""

    team_id: str
    team_domain: str
    bot_user_id: str
    enterprise_id: str | None = None


def _id(value: object) -> str | None:
    return value if isinstance(value, str) and SLACK_ID.fullmatch(value) else None


def identity(answer: dict[str, Any]) -> Identity | None:
    """The team, its domain, the bot's user id and the enterprise (when any) of auth.test's
    answer for a bot token; None when one of them is missing or not of its shape."""
    team, bot, domain = _id(answer.get("team_id")), _id(answer.get("user_id")), None
    enterprise = answer.get("enterprise_id")
    if enterprise is not None and _id(enterprise) is None:
        return None
    if not _id(answer.get("bot_id")) or team is None or bot is None:
        return None
    domain = team_domain(answer.get("url"))
    return Identity(team, domain, bot, enterprise) if domain else None


# What the browser's side asks of the store beyond the request's own view


@dataclass(frozen=True)
class Office:
    """`kept_secret(install, workspace)`: the install's signing secret, opened, when the
    install is the workspace's (None otherwise, or when it does not open); `team_held(team,
    workspace)`: whether another workspace's install that is ready, on or off is for that
    Slack team."""

    kept_secret: Callable[[str, str], str | None] = field(repr=False)
    team_held: Callable[[str, str], bool] = field(repr=False)


def office_of(store: Any) -> Office:
    """The Office of the server's store: `team_held` is Store.slack_team_held, one statement
    whatever the number of workspaces."""

    def kept_secret(install_id: str, account_id: str) -> str | None:
        found = store.slack_inbound(install_id)
        if found is None or found.install.account_id != account_id:
            return None
        return found.signing_secret

    return Office(kept_secret=kept_secret, team_held=store.slack_team_held)


# An outcome, said once on the Sources page


class Outcomes:
    """The outcome a handler leaves for its person (the module): `told` writes it, `read`
    reads it back for the request's person, {} when there is none for them now."""

    def __init__(
        self,
        sealer: Sealer,
        now: Callable[[], datetime],
        secure: bool,
        memory_in: Callable[[Scope], Any],
    ) -> None:
        self.sealer = sealer
        self.now = now
        self.secure = secure
        self.memory_in = memory_in  # the request's Memory, as Auth put it on the scope

    def memory(self, request: Request) -> Memory:
        """The caller's Memory; it fails closed, as a request Auth did not admit has none."""
        memory = self.memory_in(request.scope)
        if not isinstance(memory, Memory):
            raise RuntimeError("this request was not authenticated: it has no account")
        return memory

    def __repr__(self) -> str:
        return "Outcomes()"

    def told(self, memory: Memory, said: str) -> RedirectResponse:
        outcome = {
            "said": said, "person": memory.store.user_id, "workspace": memory.store.account_id,
            "exp": self.now().timestamp() + SAID_TTL,
        }  # fmt: skip
        response = RedirectResponse(ANCHOR, status_code=303)
        response.set_cookie(
            SAID_COOKIE, self.sealer.seal(json.dumps(outcome), OUTCOME), max_age=SAID_TTL,
            path=SAID_PATH, secure=self.secure, httponly=True, samesite="lax",
        )  # fmt: skip
        response.headers["cache-control"] = "no-store"
        return response

    def read(self, scope: Scope) -> str:
        memory = self.memory_in(scope)
        sealed = cookie_parser(Headers(scope=scope).get("cookie", "")).get(SAID_COOKIE)
        plain = self.sealer.open(sealed, OUTCOME) if sealed else None
        try:
            found = json.loads(plain) if plain is not None else None
        except ValueError:
            found = None
        if (
            not isinstance(found, dict)
            or not isinstance(memory, Memory)
            or not isinstance(found.get("exp"), int | float)
            or found["exp"] <= self.now().timestamp()
            or found.get("person") != memory.store.user_id
            or found.get("workspace") != memory.store.account_id
            or not isinstance(found.get("said"), str)
        ):
            return ""
        return found["said"]


# The access log


def logged(action: str, memory: Memory, install: str | None, status: int, outcome: str) -> None:
    """One record on the access log: who, the action, the install and the outcome."""
    kind, actor = memory.store.actor
    with contextlib.suppress(Exception):  # a log handler that raises fails no request
        access_log.info(
            "%s by %s %r (%s), install %s: %s %s",
            action, kind, actor, memory.store.actor_id, install, status, outcome,
            extra={
                "actor_kind": kind, "actor": actor, "actor_id": memory.store.actor_id,
                "action": action, "project": None, "status": status, "install": install,
                "outcome": outcome,
            },
        )  # fmt: skip


@dataclass
class Noted:
    """What a handler noted for its access record."""

    install: str | None = None
    outcome: str = ""


Handler = Callable[[Request, Memory, Noted], Awaitable[Response]]


def recorded(
    action: str, handler: Handler, outcomes: Outcomes
) -> Callable[[Request], Awaitable[Response]]:
    """`handler`, with one access record for whatever it ends with (`logged`)."""

    async def answer(request: Request) -> Response:
        memory = outcomes.memory(request)
        noted, status = Noted(), 500
        try:
            response = await handler(request, memory, noted)
            status = response.status_code
            return response
        except RequestError as refused:
            status = refused.status
            noted.outcome = noted.outcome or type(refused).__name__.lower()
            raise
        finally:
            logged(action, memory, noted.install, status, noted.outcome or "failed")

    return answer


# The addresses


def _typed(form: FormData, name: str) -> str:
    value = form.get(name)
    return value.strip() if isinstance(value, str) else ""


def setup_routes(
    outcomes: Outcomes,
    *,
    office: Office,
    http: Callable[[], httpx.AsyncClient],
    api: str,
) -> list[Route]:
    """The six addresses (the module). `http`: what the call to Slack is made through; `api`:
    the base of Slack's Web API (the switch's)."""

    async def manager(memory: Memory) -> None:
        if not await run_in_threadpool(memory.store.manages):
            raise Forbidden(NOT_A_MANAGER)

    async def told(memory: Memory, noted: Noted, said: str) -> Response:
        noted.outcome = said
        return outcomes.told(memory, said)

    async def installed(memory: Memory, noted: Noted) -> SlackInstall | None:
        await manager(memory)
        found = await run_in_threadpool(memory.store.get_slack_install)
        noted.install = found.id if found is not None else None
        return found

    async def set_up(request: Request, memory: Memory, noted: Noted) -> Response:
        await manager(memory)
        try:
            made = await run_in_threadpool(memory.store.make_slack_install)
        except PermissionError:
            raise Forbidden(NOT_A_MANAGER) from None
        noted.install = made.id
        return await told(memory, noted, "made")

    async def secrets(request: Request, memory: Memory, noted: Noted) -> Response:
        install = await installed(memory, noted)
        form = await request.form()
        secret, token = _typed(form, "signing_secret"), _typed(form, "bot_token")
        if install is None or install.state == "removed":
            return await told(memory, noted, "none")
        account = memory.store.account_id
        if not secret and install.needs_bot_token:
            secret = await run_in_threadpool(office.kept_secret, install.id, account) or ""
            if not secret:
                return await told(memory, noted, "unopened")
        if not (SECRET_VALUE.fullmatch(secret) and SECRET_VALUE.fullmatch(token)):
            return await told(memory, noted, "blank")
        try:
            async with http() as client:
                answer = await SlackClient(client, api=api).call(
                    "auth.test", token, within=CHECK_WITHIN
                )
        except SlackError as refused:
            unreachable = refused.code in UNREACHABLE or refused.code.startswith("http_")
            return await told(memory, noted, "unreachable" if unreachable else "not_accepted")
        if not _id(answer.get("bot_id")):
            return await told(memory, noted, "not_a_bot")
        found = identity(answer)
        if found is None:
            return await told(memory, noted, "unclear")
        if await run_in_threadpool(office.team_held, found.team_id, account):
            return await told(memory, noted, "taken")
        try:
            await run_in_threadpool(
                memory.store.ready_slack_install, install.id, signing_secret=secret,
                bot_token=token, team_id=found.team_id, team_domain=found.team_domain,
                bot_user_id=found.bot_user_id, enterprise_id=found.enterprise_id,
            )  # fmt: skip
        except PermissionError:
            raise Forbidden(NOT_A_MANAGER) from None
        except KeyError:
            return await told(memory, noted, "none")
        except TeamHeld:  # made ready for this team elsewhere since team_held was asked
            return await told(memory, noted, "taken")
        except NotItsTeam:  # ready, on or off for another Slack team: remove Slack first
            return await told(memory, noted, "other_team")
        except ValueError:
            return await told(memory, noted, "unkept")
        return await told(memory, noted, "ready")

    def switch(on: bool) -> Handler:
        async def switching(request: Request, memory: Memory, noted: Noted) -> Response:
            install = await installed(memory, noted)
            if install is None or install.state == "removed":
                return await told(memory, noted, "none")
            if on and install.needs_bot_token:
                return await told(memory, noted, "needs_token")
            try:
                await run_in_threadpool(memory.store.switch_slack_install, install.id, on=on)
            except PermissionError:
                raise Forbidden(NOT_A_MANAGER) from None
            except (KeyError, ValueError):
                return await told(memory, noted, "state")
            return await told(memory, noted, "on" if on else "off")

        return switching

    async def remove(request: Request, memory: Memory, noted: Noted) -> Response:
        install = await installed(memory, noted)
        confirm = _typed(await request.form(), "confirm")
        if install is None:
            return await told(memory, noted, "none")
        account = await run_in_threadpool(memory.store.get_account, memory.store.account_id)
        if account is None or confirm != account.name:
            return await told(memory, noted, "confirm")
        try:
            await run_in_threadpool(memory.store.remove_slack_install, install.id)
        except PermissionError:
            raise Forbidden(NOT_A_MANAGER) from None
        except KeyError:
            return await told(memory, noted, "none")
        return await told(memory, noted, "removed")

    async def forget(request: Request, memory: Memory, noted: Noted) -> Response:
        install = await installed(memory, noted)
        if await run_in_threadpool(memory.store.role) != "owner":
            raise Forbidden(f"{NOT_AN_OWNER[:1].upper()}{NOT_AN_OWNER[1:]}.")
        if install is None:
            return await told(memory, noted, "none")
        try:
            await run_in_threadpool(memory.store.forget_slack_notes_now, install.id)
        except PermissionError:
            raise Forbidden(f"{NOT_AN_OWNER[:1].upper()}{NOT_AN_OWNER[1:]}.") from None
        except (KeyError, ValueError):
            return await told(memory, noted, "state")
        return await told(memory, noted, "forgotten")

    return [
        Route(path, recorded(ACTION, handler, outcomes), methods=["POST"])
        for path, handler in (
            (SETUP_PATH, set_up), (SECRETS_PATH, secrets), (PAUSE_PATH, switch(False)),
            (RESUME_PATH, switch(True)), (DISCONNECT_PATH, remove), (FORGET_PATH, forget),
        )
    ]  # fmt: skip


# The block on the Sources page (templates/_slack.html)

BLOCK: ContextVar[Callable[[], dict[str, Any] | None] | None] = ContextVar(
    "slack_block", default=None
)


def block() -> dict[str, Any] | None:
    """What the block shows to the request being answered; None (and nothing read) when the
    switch is off. cairn.web.app makes it the template global `slack_block`."""
    found = BLOCK.get()
    return found() if found is not None else None


def _state(install: SlackInstall | None) -> str | None:
    if install is None:
        return None
    return "revoked" if install.needs_bot_token else install.state


def block_for(memory: Any, public_url: str, said: str = "") -> dict[str, Any] | None:
    """What the block shows the request's person, `memory` (the module), or None."""
    from cairn.slack.link import SAID as LINKED  # it imports this module

    if not isinstance(memory, Memory) or memory.person is None:
        return None
    store = memory.store
    words = {**SAID, **LINKED}
    shown = {"done": words.get(said), "refused": REFUSALS.get(said)}
    mine = [found.slack_user_id for found in store.own_slack_links()]  # theirs alone
    if not store.manages():
        if shown["done"] or shown["refused"] or mine:
            return {"manages": False, "links": mine, **shown}
        return None
    install = store.get_slack_install()
    state = _state(install)
    base = public_url.rstrip("/")
    manifest = (
        build_manifest(public_url=base, install=install.id, command=install.command)
        if install is not None and state == "pending"
        else None
    )
    due = store.slack_notes_to_forget()
    gone = install.bot_gone_at if install is not None and state != "removed" else None
    return {
        "manages": True,
        "links": mine,
        **shown,
        "state": state,
        "says": STATES.get(state or ""),
        "again": CHANGED_AT_SLACK if state in ("on", "off") else None,
        "install": install,
        "events": f"{base}{PREFIX}/{install.id}/events" if install is not None else None,
        "manifest": json.dumps(manifest, indent=2) if manifest else None,
        "manifest_link": manifest_link(manifest) if manifest else None,
        "permissions": [(scope, PERMISSIONS[scope]) for scope in SCOPES],
        "due": {"notes": due.notes, "at": due.at.isoformat()} if due is not None else None,
        # Slack ended the bot token: since when, and the day after which Slack is removed here
        "gone": (
            {"since": gone, "by": (datetime.fromisoformat(gone) + GONE_AFTER).isoformat()}
            if gone
            else None
        ),
        "forgets": state == "removed" and store.role() == "owner",  # "Forget them now"
        "workspace": store.get_account(store.account_id).name,
    }


class Block:
    """ASGI middleware, installed only with the switch on: each request carries, for the page
    that renders the block, the function that says what it shows (BLOCK), called only then.
    A page that read an outcome (SAID_COOKIE) clears it in its answer, so it is said once."""

    def __init__(self, app: ASGIApp, outcomes: Outcomes, public_url: str) -> None:
        self.app = app
        self.outcomes = outcomes
        self.public_url = public_url
        cleared = Response()
        cleared.delete_cookie(
            SAID_COOKIE, path=SAID_PATH, secure=outcomes.secure, httponly=True, samesite="lax"
        )
        self.cleared = cleared.headers["set-cookie"]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        read: list[bool] = []

        def shown() -> dict[str, Any] | None:
            if SAID_COOKIE in cookie_parser(Headers(scope=scope).get("cookie", "")):
                read.append(True)
            memory = self.outcomes.memory_in(scope)
            return block_for(memory, self.public_url, self.outcomes.read(scope))

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
