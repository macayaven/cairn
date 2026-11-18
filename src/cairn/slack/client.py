"""The calls cairn makes to Slack (docs/slack-api.md), each through an httpx client it is
handed, so a test gives it an httpx.MockTransport.

- `call(method, token, fields)`: a POST to API/<method> with the token in the Authorization
  header and nowhere else. The answer is Slack's JSON object; one Slack marks not ok is a
  SlackError named by Slack's own `error`, and an answer that is not Slack's is one too
  (`http_<status>`, `not_json`, `unknown`).
- A 429 is waited out by its Retry-After (WAIT_MAX seconds at most) and the call made again,
  WAITS times; after that it is SlackError("rate_limited").
- `within` seconds: one limit for the whole call, the connection and every read included.
  Nothing is waited for inside it: a 429 is "rate_limited" at once, and a Slack that has not
  answered when it is up is "timeout". A handler that must answer Slack in three seconds
  calls this way.
- `auth_test`, `user` (users.info) and `post` (chat.postMessage) are the calls there are.
  There is none that reads a conversation's history, and none is to be added without a
  decision row (decision D3).
- `reply(address, message)` answers through an address Slack gave in a signed request
  (`response_url`): only over https and only to one of REPLY_HOSTS, whatever the payload says,
  and private to the person unless `private=False`. It carries no token.

A SlackError holds a code and never a token, an address or a message's text."""

import asyncio
from collections.abc import Awaitable, Callable, Collection
from typing import Any
from urllib.parse import urlsplit

import httpx

API = "https://slack.com/api"
REPLY_HOSTS = frozenset({"hooks.slack.com", "hooks.slack-gov.com"})
WAITS = 2  # times a 429 is waited out before giving up
WAIT_MAX = 30  # seconds one wait lasts at most
JSON_METHODS = frozenset({"chat.postMessage"})  # the rest take a form


class SlackError(Exception):
    """A call to Slack that did not do what was asked. `code`: Slack's own `error`, or
    `timeout`, `rate_limited`, `http_<status>`, `not_json`, `unknown`, `bad_reply_address`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SlackClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        api: str = API,
        reply_hosts: Collection[str] = REPLY_HOSTS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._api = api.rstrip("/")
        self._reply_hosts = frozenset(reply_hosts)
        self._sleep = sleep
        # A test's stand-in listens on loopback over http; Slack's own hosts never do.
        self._plain = self._api.startswith("http://")

    def __repr__(self) -> str:
        return "SlackClient()"

    async def call(
        self,
        method: str,
        token: str,
        fields: dict[str, Any] | None = None,
        *,
        within: float | None = None,
    ) -> dict[str, Any]:
        """Slack's answer to `method` (the module)."""
        if within is None:
            return await self._call(method, token, fields or {}, waits=WAITS)
        try:
            async with asyncio.timeout(within):
                return await self._call(method, token, fields or {}, waits=0)
        except TimeoutError:
            raise SlackError("timeout") from None

    async def _call(self, method: str, token: str, fields: dict[str, Any], *, waits: int) -> dict:
        headers = {"authorization": f"Bearer {token}"}
        body = {"json": fields} if method in JSON_METHODS else {"data": fields}
        for attempt in range(waits + 1):
            try:
                response = await self._http.post(f"{self._api}/{method}", headers=headers, **body)
            except httpx.HTTPError:
                raise SlackError("http_error") from None
            if response.status_code != 429:
                break
            if attempt == waits:
                raise SlackError("rate_limited")
            await self._sleep(_seconds(response.headers.get("retry-after")))
        if response.status_code != 200:
            raise SlackError(f"http_{response.status_code}")
        try:
            answer = response.json()
        except ValueError:
            raise SlackError("not_json") from None
        if not isinstance(answer, dict):
            raise SlackError("not_json")
        if answer.get("ok") is not True:
            code = answer.get("error")
            raise SlackError(code if isinstance(code, str) and code else "unknown")
        return answer

    async def auth_test(self, token: str) -> dict[str, Any]:
        """Whose token this is: `team_id`, `team`, `url`, `user_id`, `bot_id`."""
        return await self.call("auth.test", token)

    async def user(self, token: str, user_id: str, *, within: float | None = None) -> dict:
        """Slack's `user` object for `user_id` (users.info)."""
        answer = await self.call("users.info", token, {"user": user_id}, within=within)
        found = answer.get("user")
        if not isinstance(found, dict):
            raise SlackError("unknown")
        return found

    async def post(self, token: str, conversation: str, message: dict[str, Any]) -> None:
        """Post `message` (cairn.slack.blocks) to a conversation the bot is in."""
        await self.call("chat.postMessage", token, {"channel": conversation, **message})

    async def reply(self, address: str, message: dict[str, Any], *, private: bool = True) -> None:
        """Answer through a `response_url` (the module)."""
        try:
            parts = urlsplit(address)
            host, port_ok = parts.hostname, parts.port is None or self._plain
        except ValueError:
            raise SlackError("bad_reply_address") from None
        scheme_ok = parts.scheme == "https" or (self._plain and parts.scheme == "http")
        if not scheme_ok or host not in self._reply_hosts or parts.username is not None:
            raise SlackError("bad_reply_address")
        if not port_ok:
            raise SlackError("bad_reply_address")
        body = {
            **message,
            "response_type": "ephemeral" if private else "in_channel",
            "replace_original": False,
        }
        try:
            response = await self._http.post(address, json=body)
        except httpx.HTTPError:
            raise SlackError("http_error") from None
        if response.status_code != 200:
            raise SlackError(f"http_{response.status_code}")


def _seconds(value: str | None) -> int:
    """A Retry-After, in whole seconds, between 1 and WAIT_MAX."""
    if value is None or not value.isdigit():
        return 1
    return max(1, min(WAIT_MAX, int(value)))
