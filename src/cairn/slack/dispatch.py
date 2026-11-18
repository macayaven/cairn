"""Who answers a request Slack sent, once cairn.web.slack has verified it: three registries,
filled by the modules that answer, none of which edits this one.

    @on_event("message")          # an event's `event.type`
    @on_command("find")           # a slash command's first word, in lowercase ("" for none)
    @on_action("file_in_cairn")   # an action's callback_id, or its first action's action_id
    async def handler(envelope: Envelope, context: Context) -> Response | None: ...

A name is registered once: a second handler for it is Taken (ValueError), so two modules never
answer the same thing; `unregister` takes one back (for tests). Every module of the
cairn.slack package is imported before the first request (cairn.web.slack.slack_routes), so
a module registers by being there.

`dispatch(envelope, context)` runs the handler of the envelope's registry and name and
answers with what it returns: a Starlette Response as it is, None as 200 with an empty body.
An event, command or action nobody registered is acknowledged the same way, 200 with an empty
body, and nothing else happens: Slack takes an event as received and does not retry it, and a
command or an action then shows the person nothing. Slack's challenge (url_verification) never
reaches a registry: the address answers it (cairn.web.slack). A handler that raises is not
caught here.

What a handler is given (Context): the install (never its secrets), `view`, the store's view of
the install's workspace acting for no person (links, offers, project, notes recorded), and
`memory_for(user_id)`, the Memory of a person of the workspace, as a request of theirs would
have it (Accounts.memory: their reach, the gate, the guard, the plan), or None when they are
no longer one. `http()` makes the client every call to Slack goes through (create_app's, so a
test hands in an httpx.MockTransport); `bot_token()` opens the install's bot token (a read of
the store: call it on a worker thread). `slack()` is the client for them (cairn.slack.client).
`public_url` is the server's address; `now()` its clock. The store's calls block: a handler runs them on a worker thread
(starlette.concurrency.run_in_threadpool), as cairn.web.slack's own handlers do."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
from starlette.responses import Response

from cairn.slack.client import API, REPLY_HOSTS, SlackClient
from cairn.slack.verify import Envelope
from cairn.store.slack import SlackInstall

if TYPE_CHECKING:
    from cairn.memory import Memory
    from cairn.store import Store


@dataclass(frozen=True)
class Context:
    install: SlackInstall
    view: "Store" = field(repr=False)
    memory_for: Callable[[str], "Memory | None"] = field(repr=False)
    http: Callable[[], httpx.AsyncClient] = field(repr=False)
    public_url: str = ""
    now: Callable[[], datetime] = field(default=datetime.now, repr=False)
    api: str = API
    reply_hosts: frozenset[str] = REPLY_HOSTS

    def slack(self) -> SlackClient:
        """The client every call to Slack is made with (cairn.slack.client), at the server's
        base and reply hosts: Slack's own, unless a test's switch says a stand-in's."""
        return SlackClient(self.http(), api=self.api, reply_hosts=self.reply_hosts)

    def bot_token(self) -> str | None:
        """The install's bot token, opened; None when it holds none or it does not open."""
        return self.view.slack_bot_token(self.install.id)


Handler = Callable[[Envelope, Context], Awaitable[Response | None]]
EVENTS: dict[str, Handler] = {}
COMMANDS: dict[str, Handler] = {}
ACTIONS: dict[str, Handler] = {}
REGISTRIES = {"event": EVENTS, "command": COMMANDS, "action": ACTIONS}
BY_ADDRESS = {"events": EVENTS, "commands": COMMANDS, "actions": ACTIONS}


class Taken(ValueError):
    """A name registered twice."""


def _register(kind: str, name: str) -> Callable[[Handler], Handler]:
    if not isinstance(name, str):
        raise TypeError("a handler is registered by name")
    registry = REGISTRIES[kind]

    def registering(handler: Handler) -> Handler:
        if name in registry:
            raise Taken(f"a Slack {kind} named {name!r} has a handler already")
        registry[name] = handler
        return handler

    return registering


def on_event(name: str) -> Callable[[Handler], Handler]:
    """Register the handler of the events of type `name` (`event.type`)."""
    return _register("event", name)


def on_command(name: str) -> Callable[[Handler], Handler]:
    """Register the handler of the slash command whose first word is `name` (lowercase)."""
    return _register("command", name.lower())


def on_action(name: str) -> Callable[[Handler], Handler]:
    """Register the handler of the action whose callback_id (or first action_id) is `name`."""
    return _register("action", name)


def unregister(kind: str, name: str) -> None:
    """Take back the handler of `kind` ("event", "command" or "action") named `name`."""
    REGISTRIES[kind].pop(name, None)


ACKNOWLEDGED = 200


async def dispatch(envelope: Envelope, context: Context) -> Response:
    """The answer to a verified request (the module)."""
    handler = BY_ADDRESS[envelope.kind].get(envelope.name)
    if handler is None:
        return Response(status_code=ACKNOWLEDGED)
    answer: Any = await handler(envelope, context)
    return answer if isinstance(answer, Response) else Response(status_code=ACKNOWLEDGED)
