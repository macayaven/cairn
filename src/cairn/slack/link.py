"""Linking a Slack user to a person of the workspace (K1, decision D5):
the page a one-time address opens, the yes, and unlink. The value is made by the bot
(Store.offer_slack_link, cairn.slack's commands) and sent as <public url>LINK_PATH?t=<value>;
what is kept is cairn.store.slack. A link takes two steps, both ends pinned before a code
exists: the offer is the Slack user's who asked; the signed-in person's yes ties it to them
and shows them a code, once; the same Slack user types `<command> link <code>` in Slack
(cairn.slack.commands), and only then are they linked. Someone who sends their address to
another person gains nothing from that person's yes.

- It is opened by a click in Slack, so Auth treats it as an invitation's link (cairn.web.auth,
  `comes_back`, with the switch on): a person not signed in is sent to sign in and comes back
  to the same address, the value with it (as `next`, sealed in the sign-in's state cookie);
  a signed-in browser that another site sent here left its session cookie (SameSite=Strict)
  behind, so it is first shown the page that loads the same address again by itself.
- GET LINK_PATH?t=<value>, for a signed-in person (Auth sends anyone else to sign in): when the
  value is one offer of an install of the person's workspace, within its ten minutes, that
  nobody said yes to (Store.slack_offer), the page (templates/slack_link.html) leads with
  what cannot be chosen, the Slack user's id and the Slack team's domain, then the name Slack
  gives (users.info through cairn.slack.client with the install's bot token, within
  NAME_WITHIN seconds; `slack_name`), marked as a name that user set themself, and the
  person and the workspace. It says to go on only if you typed the command in Slack yourself
  a moment ago, and to close the page if someone sent you this address. It shows no code.
  Its form posts back to the same address: the value is in no log line, and in no page or
  cookie but the ways back to this address above. Opening it changes nothing, however often.
- POST LINK_PATH?t=<value>, the yes (Store.claim_slack_offer): the offer is tied to the
  person, nothing is linked, and the answer is 200 with the page that shows the code, once
  (templates/slack_link_code.html, `cache-control: no-store`, `referrer-policy:
  no-referrer`): what to type in Slack with the install's command name, by when, to type it
  as the command and not in a message to the bot, and to give the code to no one. After it
  the address is no page: the person who lost the code starts again in Slack.
- POST UNLINK_PATH, `slack_user`: the person's own link to that Slack user undone
  (Store.unlink_slack_user); a link that is not theirs is not touched.
- Anything else: no value, more than one, a value used, run out, of another workspace, or a
  person who is not a member of the install's workspace, gets the page every address that is
  not a page gets (404), and nothing changes.
- Each request is one record on the access log (ACTION, cairn.slack.setup.logged): who, the
  install when one was found, and the outcome (`code_shown` for the yes); never the value
  or the code."""

from collections.abc import Callable
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from cairn.memory import Memory, NotFound
from cairn.slack.client import SlackClient, SlackError
from cairn.slack.setup import PREFIX, Noted, Outcomes, recorded

LINK_PATH = f"{PREFIX}/link"
UNLINK_PATH = f"{PREFIX}/unlink"
ACTION = "slack_link"  # the access log's action
NAME_WITHIN = 3.0  # seconds users.info may take in all
NAME_MAX = 80  # characters of a Slack user's name shown
VALUE_MAX = 128  # characters of a one-time value read
SAID = {"unlinked": "Unlinked: the bot no longer acts as you for that Slack user."}
# On the code page: a message to the bot from someone not linked makes a new offer, which
# ends the one this code belongs to.
AS_THE_COMMAND = "Type it as the command above: sent to the bot as a message, it starts over."


def slack_name(user: dict[str, Any], fallback: str) -> str:
    """The name Slack gives a user (`real_name`, then the profile's display name and real
    name, then `name`), at most NAME_MAX characters; `fallback` when it gives none."""
    profile = user.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    for found in (
        user.get("real_name"), profile.get("display_name"), profile.get("real_name"),
        user.get("name"),
    ):  # fmt: skip
        if isinstance(found, str) and found.strip():
            return found.strip()[:NAME_MAX]
    return fallback


def _value(request: Request) -> str | None:
    """The one-time value of the address: one `t`, 1 to VALUE_MAX characters; else None."""
    given = request.query_params.getlist("t")
    if len(given) != 1 or not 0 < len(given[0]) <= VALUE_MAX:
        return None
    return given[0]


def link_routes(
    outcomes: Outcomes,
    *,
    http: Callable[[], httpx.AsyncClient],
    api: str,
    page_of: Callable[..., Response],
    no_page: str,
) -> list[Route]:
    """The three addresses (the module). `http`: what the call to Slack is made through;
    `api`: the base of Slack's Web API (the switch's); `page_of(request, template, **context)`:
    a page for the signed-in caller; `no_page`: what an address that is not a page says. The
    last two are the web layer's, handed in by cairn.web.app."""

    async def offered(request: Request, memory: Memory, noted: Noted) -> Any:
        value = _value(request)
        offer = await run_in_threadpool(memory.store.slack_offer, value) if value else None
        if offer is None or memory.person is None:
            noted.outcome = "no_offer"
            raise NotFound(no_page)
        noted.install = offer.install_id
        return offer

    async def asked(request: Request, memory: Memory, noted: Noted) -> Response:
        offer = await offered(request, memory, noted)
        store = memory.store
        token = await run_in_threadpool(store.slack_bot_token, offer.install_id)
        name = offer.slack_user_id
        if token:
            try:
                async with http() as client:
                    user = await SlackClient(client, api=api).user(
                        token, offer.slack_user_id, within=NAME_WITHIN
                    )
                name = slack_name(user, offer.slack_user_id)
            except SlackError:
                pass
        install = await run_in_threadpool(store.get_slack_install)
        account = await run_in_threadpool(store.get_account, store.account_id)
        noted.outcome = "asked"
        response = page_of(
            request, "slack_link.html", slack_name=name, slack_user=offer.slack_user_id,
            team_domain=install.team_domain if install else None,
            workspace=account.name if account else "", expires_at=offer.expires_at.isoformat(),
        )  # fmt: skip
        response.headers["cache-control"] = "no-store"
        response.headers["referrer-policy"] = "no-referrer"
        return response

    async def yes(request: Request, memory: Memory, noted: Noted) -> Response:
        offer = await offered(request, memory, noted)
        store = memory.store
        try:
            code = await run_in_threadpool(store.claim_slack_offer, _value(request))
        except (KeyError, PermissionError):
            noted.outcome = "no_offer"
            raise NotFound(no_page) from None
        install = await run_in_threadpool(store.get_slack_install)
        noted.outcome = "code_shown"
        response = page_of(
            request, "slack_link_code.html", code=code, slack_user=offer.slack_user_id,
            command=install.command if install else "", expires_at=offer.expires_at.isoformat(),
            as_the_command=AS_THE_COMMAND,
        )  # fmt: skip
        response.headers["cache-control"] = "no-store"
        response.headers["referrer-policy"] = "no-referrer"
        return response

    async def unlink(request: Request, memory: Memory, noted: Noted) -> Response:
        slack_user = (await request.form()).get("slack_user")
        store = memory.store
        install = await run_in_threadpool(store.get_slack_install)
        person = None
        if install is not None and isinstance(slack_user, str) and memory.person is not None:
            person = await run_in_threadpool(store.slack_person, install.id, slack_user)
        if person is None or person.user_id != store.user_id:
            noted.outcome = "not_linked"
            raise NotFound(no_page)
        noted.install = install.id
        await run_in_threadpool(store.unlink_slack_user, install.id, slack_user)
        noted.outcome = "unlinked"
        return outcomes.told(memory, "unlinked")

    return [
        Route(LINK_PATH, recorded(ACTION, asked, outcomes), methods=["GET"]),
        Route(LINK_PATH, recorded(ACTION, yes, outcomes), methods=["POST"]),
        Route(UNLINK_PATH, recorded(ACTION, unlink, outcomes), methods=["POST"]),
    ]
