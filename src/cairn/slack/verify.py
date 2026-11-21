"""What a request Slack sends must be before cairn reads any of it, and what a handler is given
once it is (docs/slack-api.md, Verifying and acknowledging inbound requests).

- The body is read with a limit (`read_body`): a Content-Length over BODY_MAX (1 MiB), or a
  body that reaches a byte past it, is TooLarge, and nothing more is read.
- `verified(secret, headers, body, now)`: X-Slack-Request-Timestamp is whole seconds (digits
  only) within SKEW (300) seconds of `now`, and X-Slack-Signature is "v0=" and the
  HMAC-SHA256, in lowercase hexadecimal, of "v0:<timestamp>:<the body's bytes>" under the
  install's signing secret (`signature`). The two are compared as bytes by
  hmac.compare_digest, which takes the same time wherever they differ; a header that is not
  ASCII is refused before it. The secret is chosen by the address, never by the body.
- `envelope(kind, install, body, headers)`, only after `verified`: the body read as Slack sends
  it to that address (events: JSON; commands: a form; actions: a form whose `payload` field
  is JSON), as an Envelope. A body that is not that is NotSlack; one whose team is not the
  install's, or names none (Slack's url_verification alone carries no team), is OtherTeam.
  Neither says anything of the body.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

from cairn.store.slack import SlackInstall

BODY_MAX = 1024 * 1024  # bytes
SKEW = 300  # seconds a request's timestamp may be from the server's clock
FIELDS_MAX = 100  # fields a form may carry
TIMESTAMP = "x-slack-request-timestamp"
SIGNATURE = "x-slack-signature"
RETRY = "x-slack-retry-num"
WHOLE_SECONDS = re.compile(r"[0-9]{1,12}")
RETRY_NUMBER = re.compile(r"[0-9]{1,3}")
KINDS = ("events", "commands", "actions")
CHALLENGE = "url_verification"


class TooLarge(Exception):
    """A body over BODY_MAX."""


class NotSlack(ValueError):
    """A body that is not what Slack sends to that address. Its message is SAID, always."""

    SAID = "this is not a request Slack sends"

    def __init__(self) -> None:
        super().__init__(self.SAID)


class OtherTeam(ValueError):
    """A body whose team is not the install's, or that names none."""

    def __init__(self) -> None:
        super().__init__("this request is not of this install's Slack team")


async def read_body(request: Any, limit: int = BODY_MAX) -> bytes:
    """The raw body, at most `limit` bytes; TooLarge past it, reading no further."""
    said = request.headers.get("content-length")
    if said is not None and (not said.isdigit() or int(said) > limit):
        raise TooLarge()
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise TooLarge()
    return bytes(body)


def signature(secret: str, timestamp: str, body: bytes) -> bytes:
    """Slack's signature of `body` sent at `timestamp`, under `secret`, as the header holds it."""
    base = b"v0:" + timestamp.encode("ascii") + b":" + body
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return b"v0=" + digest.encode("ascii")


def verified(secret: str, headers: Mapping[str, str], body: bytes, now: float) -> bool:
    """Whether `body`, with `headers`, is Slack's, signed with `secret` within SKEW of `now`."""
    timestamp = headers.get(TIMESTAMP) or ""
    given = headers.get(SIGNATURE) or ""
    if not WHOLE_SECONDS.fullmatch(timestamp) or abs(now - int(timestamp)) > SKEW:
        return False
    try:
        presented = given.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(signature(secret, timestamp, body), presented)


@dataclass(frozen=True)
class Envelope:
    """A request Slack sent, verified and read. `kind`: which of the three addresses (KINDS).
    `type`: Slack's own type of it (event_callback, url_verification, message_action,
    shortcut, block_actions, ...; "command" for a slash command). `name`: what the registries
    are keyed by (cairn.slack.dispatch): an event's `event.type`, a command's first word in
    lowercase ("" when there is none), an action's `callback_id` or its first action's
    `action_id`. `install_id`, `account_id` and `team_id`: the install's. `user_id`: the Slack
    user who acted, when the body names one. `payload`: the body as read (never in a repr).
    `event_id` and `retry` (x-slack-retry-num): an event's."""

    kind: str
    type: str
    name: str
    install_id: str
    account_id: str
    team_id: str | None
    user_id: str | None
    payload: dict[str, Any] = field(repr=False)
    event_id: str | None = None
    retry: int | None = None


def _json(text: str) -> dict[str, Any]:
    try:
        found = json.loads(text)
    except (ValueError, RecursionError):
        raise NotSlack() from None
    if not isinstance(found, dict):
        raise NotSlack()
    return found


def _form(body: bytes) -> dict[str, str]:
    try:
        text = body.decode("utf-8")
        pairs = parse_qsl(text, keep_blank_values=True, max_num_fields=FIELDS_MAX)
    except ValueError:  # not UTF-8, or too many fields
        raise NotSlack() from None
    found = dict(pairs)
    if len(found) != len(pairs):  # a field given twice
        raise NotSlack()
    return found


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _id_of(value: object) -> str | None:
    return _text(value.get("id")) if isinstance(value, dict) else None


def envelope(kind: str, install: SlackInstall, body: bytes, headers: Mapping[str, str]) -> Envelope:
    """The Envelope of a verified request to the address `kind` of `install` (the module)."""
    if kind == "events":
        try:
            payload = _json(body.decode("utf-8"))
        except UnicodeDecodeError:
            raise NotSlack() from None
        type_ = _text(payload.get("type"))
        if type_ is None:
            raise NotSlack()
        if type_ == CHALLENGE:
            if not isinstance(payload.get("challenge"), str):
                raise NotSlack()
            return Envelope(kind, type_, "", install.id, install.account_id, None, None, payload)
        event = payload.get("event")
        if not isinstance(event, dict) or _text(event.get("type")) is None:
            raise NotSlack()
        team, user, name = _text(payload.get("team_id")), _text(event.get("user")), event["type"]
        event_id = _text(payload.get("event_id"))
    elif kind == "commands":
        payload = _form(body)
        words = payload.get("text", "").split()
        team, user, type_ = _text(payload.get("team_id")), _text(payload.get("user_id")), "command"
        name, event_id = (words[0].lower() if words else ""), None
    elif kind == "actions":
        form = _form(body)
        if "payload" not in form:
            raise NotSlack()
        payload = _json(form["payload"])
        type_ = _text(payload.get("type")) or ""
        team, user = _id_of(payload.get("team")), _id_of(payload.get("user"))
        actions = payload.get("actions")
        first = actions[0] if isinstance(actions, list) and actions else None
        name = _text(payload.get("callback_id")) or (
            _text(first.get("action_id")) if isinstance(first, dict) else None
        ) or ""  # fmt: skip
        event_id = None
    else:
        raise NotSlack()
    if team is None or team != install.team_id:
        raise OtherTeam()
    retry = headers.get(RETRY)
    number = int(retry) if retry is not None and RETRY_NUMBER.fullmatch(retry) else None
    return Envelope(
        kind, type_, name, install.id, install.account_id, team, user, payload, event_id, number
    )
