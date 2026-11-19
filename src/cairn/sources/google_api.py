"""What every Google source shares when it calls a Google API: the request, the bounded read
of its answer, the reason in an error's body, and the backoff after a rate limit.

Nothing here is about one API: the source names the service and the method in the words a
SourceError says, and decides what a status means for it. The access token comes from
cairn.sources.google_auth; this module never sees a key, a secret or a refresh token. Google's
error shapes and the backoff are recorded in docs/google-auth.md (C.2),
docs/google-meet-api.md (G) and docs/google-drive-api.md (J, K).

- `fetch(http, method, url, *, params, data, headers, limit)`: one request through the
  `http` client the source was opened with (whose request lines never reach a log: see
  cairn.sources.source_client), redirects never followed, the body read to at most `limit`
  bytes. A network error raises Unreachable (its class name only), a 200 past `limit`
  TooLarge; another status past it comes back with an empty body.
- `error_reasons(body)`: the reasons an error's JSON gives, in either of Google's shapes:
  `error.details[].reason` (an ErrorInfo, AIP-193) and `error.errors[].reason` (the older
  shape). Only a short word is taken (REASON); the message, the metadata and every other
  field are never read, so no part of a body reaches a message, a log line or a cursor.
- `rate_limited(status, reasons)`: a 429, or a 403 whose reason is RATE_LIMIT_EXCEEDED,
  rateLimitExceeded or userRateLimitExceeded (the spellings the notes quote).
  dailyLimitExceeded is not one: it is a cap the project's owner set. Nor is Calendar's usage
  limit, quotaExceeded (docs/google-calendar-api.md H): waiting does not lift it.
- `backoff(attempt, jitter)`: the n-th wait, n from 0: 2^n seconds and `jitter()` (from 0 to
  1) more, at most MAX_BACKOFF: Google's "min(((2^n)+random_number_milliseconds),
  maximum_backoff)".
- `Reader`: one pull's GETs with one access token, sent only as `Authorization: Bearer`.
  `get(url, params, *, service, what)` answers a Reply: the JSON of a 200, or another status
  with its reasons (never its body). A rate limit is waited out through the injected `sleep`
  at most RETRIES times; after that the Reply says `limited`. No request starts once the
  `deadline` has come or the pull has made `budget` requests, and no wait is begun that
  would pass the deadline: those raise Spent, for the source to return what it finished.
  A request under way is given what is left until the deadline and no more
  (cairn.sources.timing: at least its FLOOR), so an answer that drips in or never comes
  raises Spent (LATE) too, as the deadline would between two requests.
  With `raw`, a 200's body is the bytes as they came (asked for with `Accept: */*`), and a
  200 past `limit` raises TooLarge, for the source to decide what that means.
  A network error, a 200 that is not JSON or one past `limit` is a SourceError in words
  ("cannot reach Google Meet for conferenceRecords.list: ReadTimeout"), never with the
  address, the query or the body.

What every Google source reads the same way, in its config and in Google's answers
- `moment(value)`: an RFC 3339 timestamp (0 to 9 fractional digits, "Z" or an offset) as an
  aware datetime in UTC, to the microsecond; None when it is not one. `stamp(moment)`: a
  moment written so, in UTC with milliseconds and "Z".
- `digest(name)`: the first 16 hex digits of a name's SHA-256, what a cursor keeps of an item
  instead of its name or id.
- ENV_NAME (a variable's name), SETTING_NAME (a setting's name a refusal may repeat) and
  EMAIL (one email address), as configs give them.
- `looks_like_a_credential(value)`: JSON, a PEM key, one of Google's token or secret
  prefixes, or a pattern of the "secrets" guard. A config's check refuses such a value
  without repeating it.
- `check_base_url(value, api)`: a source's `base_url` as stored (https, or http on a loopback
  host; no user, password, query or fragment; no piece that looks like a credential; a
  trailing "/" dropped), or ValueError naming base_url and never repeating the value.
"""

import hashlib
import json
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx

from cairn.guard import PatternGuard
from cairn.sources import Clock, SourceError
from cairn.sources import url as source_url
from cairn.sources.http_policy import read_bounded, request_failure
from cairn.sources.timing import limit
from cairn.sources.url import PrivateAddress

LATE = "its deadline, which an answer still arriving passed"
SIGN_IN_LATE = (
    "Google did not finish signing in within one pull's deadline, so the cursor cannot move:"
    " the next run tries again"
)
RETRIES = 4  # waits after a rate limit, then the Reply says `limited`
MAX_BACKOFF = 32.0  # seconds: Google's "typically 32 or 64"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REASON = re.compile(r"[A-Za-z][A-Za-z_]{0,63}")
RATE_REASONS = frozenset({"RATE_LIMIT_EXCEEDED", "rateLimitExceeded", "userRateLimitExceeded"})
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")  # what credentials_env may name
SETTING_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")  # an unknown setting a refusal may repeat
_ADDRESS_CHAR = r"[^@\s,;<>()\[\]\"']"
# An address starts only where a run of its characters starts (the look-behind), so a search
# scans each run once instead of from every position in it: linear on any text.
EMAIL = re.compile(rf"(?<!{_ADDRESS_CHAR}){_ADDRESS_CHAR}+@{_ADDRESS_CHAR}+\.{_ADDRESS_CHAR}+")
TIMESTAMP = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})", re.IGNORECASE
)
_GOOGLE_PREFIXES = ("ya29.", "1//", "GOCSPX-", "AIza", "{", "[")


# What every Google source reads the same way


def moment(value: object) -> datetime | None:
    """An RFC 3339 timestamp (0 to 9 fractional digits, "Z" or an offset) as an aware
    datetime in UTC, to the microsecond; None when it is not one."""
    if not isinstance(value, str):
        return None
    found = TIMESTAMP.fullmatch(value.strip())
    if found is None:
        return None
    whole, fraction, zone = found.groups()
    try:
        base = datetime.fromisoformat(whole)
    except ValueError:
        return None
    micro = int((fraction or "").ljust(6, "0")[:6])
    try:  # an offset of a day or more, or a moment outside years 1 to 9999 in UTC, is not one
        if zone.upper() == "Z":
            offset = UTC
        else:
            sign = 1 if zone[0] == "+" else -1
            offset = timezone(sign * timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])))
        return base.replace(microsecond=micro, tzinfo=offset).astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def stamp(moment_: datetime) -> str:
    """RFC 3339 in UTC with milliseconds and "Z"."""
    utc = moment_.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def digest(name: str) -> str:
    """What a cursor keeps of an item: never its name."""
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def looks_like_a_credential(value: str) -> bool:
    """Whether a config's value looks like a credential pasted where a name belongs."""
    stripped = value.strip()
    return (
        stripped.startswith(_GOOGLE_PREFIXES)
        or "PRIVATE KEY" in stripped
        or bool(PatternGuard("secrets").check(stripped))
    )


def check_base_url(value: object, api: str) -> str:
    """`base_url` as stored, or ValueError naming it (`api` says what it is the address of)
    and never repeating the value (cairn.sources.url.check_base_url)."""
    return source_url.check_base_url(
        value,
        what=api,
        comes_from="the credential comes from the environment",
        named_by="credentials_env",
        looks_like=looks_like_a_credential,
    )


# Requests


class Unreachable(Exception):
    """The request did not get an answer. The reason is safe to show."""

    def __init__(self, error: httpx.HTTPError) -> None:
        self._address_refused = isinstance(error, PrivateAddress)
        super().__init__(request_failure(error))

    def sentence(self, reaching: str) -> str:
        return str(self) if self._address_refused else f"{reaching}: {self}"


class TooLarge(Exception):
    """A 200 whose body is past the limit."""


class Spent(Exception):
    """The pull's deadline or its budget of requests came: what is left waits for the next."""


async def fetch(
    http: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: Mapping[str, str] | None = None,
    data: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    limit: int = MAX_RESPONSE_BYTES,
) -> tuple[int, bytes]:
    """(status, body): one request, redirects never followed, the body read to `limit`."""
    try:
        async with http.stream(
            method,
            url,
            params=params,
            data=data,
            headers=headers,
            follow_redirects=False,
        ) as response:
            status = response.status_code
            body = await read_bounded(response, limit)
            if body is None and status == 200:
                raise TooLarge
            return status, body or b""
    except httpx.HTTPError as error:
        raise Unreachable(error) from None


def error_reasons(body: bytes) -> frozenset[str]:
    """The short reason words of an error's body, in either of Google's shapes; nothing else
    of it is read."""
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        return frozenset()
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(error, dict):
        return frozenset()
    found = set()
    for key in ("details", "errors"):
        items = error.get(key)
        for item in items if isinstance(items, list) else ():
            reason = item.get("reason") if isinstance(item, dict) else None
            if isinstance(reason, str) and REASON.fullmatch(reason):
                found.add(reason)
    return frozenset(found)


def rate_limited(status: int, reasons: frozenset[str]) -> bool:
    """A 429, or a 403 that says it is a rate limit."""
    return status == 429 or (status == 403 and bool(reasons & RATE_REASONS))


def backoff(attempt: int, jitter: Callable[[], float] = random.random) -> float:
    """The wait before retry number `attempt` + 1 (attempt from 0), in seconds."""
    return min(2.0**attempt + min(max(jitter(), 0.0), 1.0), MAX_BACKOFF)


@dataclass(frozen=True)
class Reply:
    """A Google API's answer: the JSON of a 200, or another status and its reasons."""

    status: int
    body: Any = field(default=None, repr=False)
    reasons: frozenset[str] = frozenset()
    limited: bool = False  # still rate limited after RETRIES waits


class Reader:
    """One pull's GETs to a Google API with one access token (see the module docstring)."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        token: str,
        clock: Clock,
        sleep: Callable[[float], Awaitable[None]],
        deadline: datetime | None,
        budget: int,
        jitter: Callable[[], float] = random.random,
        limit: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._http = http
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._clock = clock
        self._sleep = sleep
        self.deadline = deadline
        self.budget = budget
        self._jitter = jitter
        self._limit = limit
        self.made = 0  # requests sent so far

    def __repr__(self) -> str:
        return f"Reader(made={self.made}, budget={self.budget})"

    def _spent(self) -> str | None:
        if self.deadline is not None and self._clock() >= self.deadline:
            return "its deadline"
        if self.made >= self.budget:
            return f"its budget of {self.budget:,} requests"
        return None

    async def get(
        self, url: str, params: Mapping[str, str], *, service: str, what: str, raw: bool = False
    ) -> Reply:
        """The answer to a GET of `url`, after waiting out a rate limit at most RETRIES
        times. Spent when the pull may not send it; SourceError in words for an answer that
        cannot be read. With `raw`, a 200's body is its bytes, and TooLarge past `limit`."""
        headers = {**self._headers, "Accept": "*/*"} if raw else self._headers
        for attempt in range(RETRIES + 1):
            spent = self._spent()
            if spent:
                raise Spent(spent)
            self.made += 1
            try:
                async with limit(self.deadline, self._clock):
                    status, body = await fetch(
                        self._http, "GET", url, params=params, headers=headers,
                        limit=self._limit,
                    )  # fmt: skip
            except TimeoutError:
                raise Spent(LATE) from None
            except Unreachable as error:
                raise SourceError(error.sentence(f"cannot reach {service} for {what}")) from None
            except TooLarge:
                if raw:
                    raise
                raise SourceError(
                    f"{service}'s answer to {what} is larger than this server reads"
                ) from None
            if status == 200 and raw:
                return Reply(200, body)
            if status == 200:
                try:
                    return Reply(200, json.loads(body))
                except (ValueError, RecursionError):
                    raise SourceError(f"{service}'s answer to {what} is not JSON") from None
            reasons = error_reasons(body)
            if not rate_limited(status, reasons):
                return Reply(status, reasons=reasons)
            if attempt == RETRIES:
                return Reply(status, reasons=reasons, limited=True)
            wait = backoff(attempt, self._jitter)
            if (
                self.deadline is not None
                and self._clock() + timedelta(seconds=wait) > self.deadline
            ):
                raise Spent("its deadline, which a wait for a rate limit would pass")
            await self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover
