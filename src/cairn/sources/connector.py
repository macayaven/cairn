"""The connector kind: items read from a connector of the operator's own, by protocol 1.

Contract (frozen by tests/acceptance/test_c9_connector.py; the protocol itself is
docs/connector-protocol.md, which says exactly what "The protocol" below says, and is the
document a person writing a connector reads). Pinned beside it by
tests/unit/test_connector_unit.py. cairn never writes to a connector.

Why: cairn reads some systems itself, each a module inside the server. A connector is a
small service of the operator's own, in any language, that answers one request; cairn asks
it for what is new and does everything else (the schedule, the switch, once-only, the
bounds, the guard against secrets, the deadlines, writing the notes). So a connector runs no
code inside the server, needs no image of its own, and can be checked against the protocol
(`connector-check`, cairn.sources.connector_check) before it is switched on.

The protocol, version 1 (one implementation of its rules, here: the kind and connector-check
both use read_item, examine, ask and check_url)
- The request: POST to the config's `url`, exactly, with `Authorization: Bearer <secret>`,
  `Content-Type: application/json`, `Accept: application/json`, and a body that is one JSON
  object: `protocol` (1), `since` (RFC 3339 in UTC with "Z": the source's last switch-on),
  `cursor` (null on the first request after a switch-on, else the cursor of the last answer
  whose items were all written or skipped) and `limit` (the config's, 1 to 200).
- The answer: status 200, `Content-Type: application/json`, a body of at most
  ANSWER_MAX_BYTES (4 MiB) that is one JSON object: `protocol` 1 (another value, or none:
  the run fails saying the connector speaks another version), `items` (a list of at most
  `limit`), `cursor` (a string of at most CURSOR_MAX, 2,000, characters, or null; never one
  that holds the secret) and `more` (true or false). Each item: `id` (required, a string of
  1 to ID_MAX, 200, characters), `text` (required, a string that is not blank, within a
  page's bounds: its passages at most cairn.store.PAGE_CHARS_MAX characters in all and
  PAGE_CLAIMS_MAX in number, read when it is read, K4; past them, too_long()), `title` and
  `topic` (optional strings of at most 200 characters) and `source_ref` (optional, at most
  500): a note's own bounds (cairn.store.FIELD_MAX); `at` (optional, RFC 3339 with its
  zone, "T" between date and time). A key whose value is null counts as absent. Any other
  key, of an item or of the answer, is ignored.
- `more` true must move on: the answer carries at least one item, or a cursor other than
  the one it was sent. One that does neither fails the run.
- Asked the same request again (after a run was cut off), a connector gives the items it
  gave before, in the same order: the same answer exactly when its `more` was true,
  possibly with newer items after them when it was false.
- Refusals: 401 or 403 (the run fails; nothing more is asked in that run); 429 (the run
  stops and asks nothing more: Retry-After means "not before the next run"); a redirect
  (never followed, whatever the client the kind is given would do), 5xx, any other status,
  a timeout, a network error, an answer that is not JSON or not this shape: the run fails
  and the cursor does not move. An error answer may carry `{"error": "a short sentence"}`:
  a string of at most ERROR_SENTENCE_MAX (200) characters, put on one line, is shown in
  the run's reason once it holds neither the secret (as `holds_secret` looks) nor anything
  the note guard's "secrets" rules find; nothing else of an error's body is ever read
  beyond ERROR_BYTES, shown or kept.
- Transport (check_url): https, or http only to 127.0.0.1 or localhost; never an address
  inside this machine's network plumbing (cairn.sources.url, the check every source shares,
  which the source client repeats on each request's resolved addresses); no user or password
  in the address and no fragment; written as the HTTP client sends it (so the request goes
  to the config's `url` exactly); at most 2,048 characters; nothing in it that looks like a
  credential (in a host label, a path segment or a query value). Every request runs under
  the pull's per-request deadline (cairn.sources.timing).

The kind ("connector" in cairn.sources.KINDS)
- It starts off. Its notes are written via "connector", with the label
  "connector". It is `withheld` (M2): cairn cannot know whose words a connector brings, so
  its notes go to no model unless the operator allows it. It proposes, for a source that
  names no project, the source's own name (SourceKind.own_project).
- Config (check_config): `url` (required, by the transport rule), `secret_env` (required:
  the name of the environment variable that holds the secret, capitals, digits and "_", at
  most 128; a value that looks like a credential is refused as such), `projects` (at most one
  project's name; none while it is off), `limit` (LIMIT: 50, a whole number from 1 to 200).
  No other setting. `check_config` returns the config with `projects` and `limit` filled in,
  and gives the same back when given it. A refusal is a ValueError naming the setting at
  fault and never repeating a value; a setting it does not take is named only when its name
  is a short plain word that does not look like a credential.
- `open` reads the secret from `env` (a missing or blank variable: SourceError naming the
  variable, never a value). The secret and its Bearer header are the source's `secrets`;
  the secret is sent only in that header and only to `url`.

What a pull does
- It asks from the stored cursor, and goes on while an answer says `more`, at most
  ANSWERS_PER_RUN answers and PULL_BYTES of answers a pull. Each item, in the connector's
  order, is a draft into the config's project: the item's text, its title (else none, so the
  note's own heading names it), its topic, its source_ref (else "<source name>:<id>"), the
  label "connector", and `item` the item's id. An item whose `at` is before `since`
  (compared as times, whatever the zone) is left out and not counted; one without `at` is
  written. An item that breaks the protocol is skipped with a fixed reason (never its id or
  text), and the others are written; an item is never cut, so one longer than a page holds is
  skipped too, as too_long() says, never as a full page. An answer that breaks the protocol
  as a whole (its shape, its cursor, `more` that does not move on) fails the pull.
- Once per id is the runner's (I1), never the kind's: each draft names its item, and the
  runner and the store write an item the first time it is stored and never again, whatever
  the connector later sends for that id, for as long as the source exists. An item that
  was refused (it held a secret, it broke the protocol) was never stored, so it is judged
  again when sent again. If a run dies between storing a note and recording its item and
  the connector then sends other text for that id, a second note is written (the window
  run_source's docstring describes). The kind keeps no record of what it wrote: its stored
  state is its config and the connector's cursor.
- The stored cursor (stored, resumed) is JSON this module owns: the connector's cursor and
  the switch-on (`since`) it was given under. After the source was switched off and on,
  the first request sends null, so the connector starts again from the new `since`.
- Each draft's `resume` is the cursor from before its answer, and the last draft of an
  answer the answer's own cursor: a run cut off inside an answer (out of time, a write that
  failed) asks for that answer again, and what it had written comes back unchanged.
- Where it stops (Pulled.stopped, kept in the run's report as SyncReport.said): at
  ANSWERS_PER_RUN answers or PULL_BYTES with `more` still true, the pull returns what it
  has and the cursor it reached, as a run that worked, and says so (and the runner adds
  that nothing was new when every item had been written before: a connector that repeats
  itself for ever does not look healthy). A 429 after at least one answer was received in
  the run ends the pull the same way, whether or not those answers gave anything to write
  (their items were written or skipped, so the cursor of the last of them is kept), saying
  the connector asked to slow down: a connector that rate-limits at the same point every
  run still moves on. A 429 on the run's first request fails the pull (the end state the
  Google kinds reach after their waits: the next run goes on), with no wait and no second
  request. The pull's deadline, after an answer, ends it the same way; before the first,
  it fails.
- A failed pull returns nothing, so no cursor moves. A connector that fails at the same
  answer every time keeps its source failing, with the reason on the Sources page; nothing
  retries around it.
"""

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from cairn.guard import PatternGuard
from cairn.sources import (
    ITEM_MAX,
    Clock,
    NoteDraft,
    Pulled,
    Skip,
    SourceError,
    SourceKind,
    fit,
    holds_secret,
)
from cairn.sources import url as source_url
from cairn.sources.config import check_env_name, check_projects
from cairn.sources.http_policy import read_bounded, request_failure, safe_error_text
from cairn.sources.timing import limit
from cairn.sources.url import URL_MAX as URL_MAX
from cairn.store.memory import FIELD_MAX

PROTOCOL = 1
VIA = "connector"
LABEL = "connector"
SETTINGS = ("url", "secret_env", "projects", "limit")
LIMIT = (50, 1, 200)  # the default, the least, the most items an answer holds
ID_MAX = ITEM_MAX  # 200 characters
TITLE_MAX, TOPIC_MAX = FIELD_MAX["title"], FIELD_MAX["topic"]  # 200 each: a note's own
SOURCE_REF_MAX = FIELD_MAX["source_ref"]  # 500
CURSOR_MAX = 2_000  # characters
ANSWER_MAX_BYTES = 4 * 1024 * 1024
ERROR_SENTENCE_MAX = 200  # characters of an error answer's sentence that may be shown
ERROR_BYTES = 64 * 1024  # of an error answer's body, read only for its sentence
ANSWERS_PER_RUN = 20  # answers one pull asks for at most
PULL_BYTES = 16 * 1024 * 1024  # of answers one pull holds at most before it stops asking
# http goes only to these two names of this machine (not [::1]: the rule the guide states).
NAMED_LOOPBACK = frozenset({"127.0.0.1", "localhost"})
_HTTP_ONLY_TO_THIS_MACHINE = "only to 127.0.0.1 or localhost (this machine)"
SETTING_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")  # a name a refusal may repeat
RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d{1,9})?([Zz]|[+-]\d{2}:\d{2})")
CURSOR_VERSION = 1

# The rules an answer is held to (examine), each named once: connector-check prints a line
# for each, in its own order, and the kind fails a pull on a finding of FATAL ones.
SHAPE = "shape"
UNIQUE = "unique ids"
BOUNDS = "bounds"
SINCE = "since"
MOVES_ON = "moves on"
CURSOR = "cursor"
FATAL = frozenset({SHAPE, MOVES_ON, CURSOR})

# Why an item breaks the protocol: fixed words, never its id or its text (the runner counts
# skips by reason).
NOT_AN_OBJECT = "an item that is not a JSON object"
BAD_ID = f"an item without an id of 1 to {ID_MAX} characters"
BAD_TEXT = "an item without text (a string that is not blank)"
BAD_AT = "an item whose at is not a time with its zone (RFC 3339)"
_BOUND = {"title": TITLE_MAX, "topic": TOPIC_MAX, "source_ref": SOURCE_REF_MAX}


def too_long() -> str:
    """Why an item whose text is past a page's bounds is skipped (K4): fixed words, the
    bounds as cairn.store holds them now (cairn.sources.fit). Never "page full": no page is
    full, the item alone is longer than one holds."""
    return (
        f"an item whose text is longer than a page holds: more than {fit.chars():,} characters"
        f" or {fit.claims():,} passages"
    )


def bad_field(key: str) -> str:
    """Why an item whose `key` (title, topic or source_ref) breaks its bound is skipped."""
    return f"an item whose {key} is not a string of at most {_BOUND[key]} characters"


# Where a pull stops while the connector has more (Pulled.stopped)
BOUND_REACHED = (
    f"The connector still had more after {ANSWERS_PER_RUN} answers, the most one run asks"
    " for; the next run goes on from there."
)
HELD_REACHED = (
    f"The connector still had more after {PULL_BYTES // (1024 * 1024)} MiB of answers, the"
    " most one run holds; the next run goes on from there."
)
SLOWED = (
    "The connector asked to slow down (HTTP 429); the next run goes on from there, and"
    " nothing more was asked in this one."
)
OUT_OF_TIME = (
    "The run's time for asking ran out while the connector had more; the next run goes on"
    " from there."
)


# The config


def looks_like_a_credential(value: str) -> bool:
    return bool(PatternGuard("secrets").check(value))


def check_url(value: object) -> str:
    """The full connector endpoint, including any query it needs."""
    return source_url.check_base_url(
        value,
        what="the connector",
        comes_from="the secret comes from the environment",
        named_by="secret_env",
        looks_like=looks_like_a_credential,
        full_endpoint=True,
        loopback=NAMED_LOOPBACK,
        http_said=_HTTP_ONLY_TO_THIS_MACHINE,
    )


def check_config(config: object) -> dict[str, Any]:
    """The config as stored, with `projects` and `limit` filled in, or ValueError naming the
    setting at fault and never repeating a value (see the module docstring)."""
    if not isinstance(config, Mapping):
        raise ValueError("a connector source's config is an object of settings")
    for setting in config:
        if setting in SETTINGS:
            continue
        if (
            isinstance(setting, str)
            and SETTING_NAME.fullmatch(setting)
            and not looks_like_a_credential(setting)
        ):
            raise ValueError(
                f"a connector source has no setting {setting!r}: it takes {', '.join(SETTINGS)}"
            )
        raise ValueError(
            f"a connector source does not take one of the settings given: it takes"
            f" {', '.join(SETTINGS)}"
        )
    if "url" not in config:
        raise ValueError("a connector source needs url: the connector's address")
    url = check_url(config["url"])
    secret_env = check_env_name(
        config,
        "secret_env",
        kind="connector",
        holds="the secret the connector checks",
        secret="secret",
        looks_like=looks_like_a_credential,
        wrong=(
            "secret_env names an environment variable (capitals, digits and _, at most 128),"
            " which holds the secret: the secret itself never goes in a config"
        ),
    )
    default, least, most = LIMIT
    asked = config.get("limit", default)
    if isinstance(asked, bool) or not isinstance(asked, int) or not least <= asked <= most:
        raise ValueError(f"limit is a whole number from {least} to {most}")
    return {
        "url": url,
        "secret_env": secret_env,
        "projects": check_projects(
            config.get("projects", []), kind="connector", looks_like=looks_like_a_credential
        ),
        "limit": asked,
    }


def describe(config: Mapping[str, Any]) -> str:
    """What a source reads, for GET /sources: where (the address without its query), and
    how."""
    parts = urlsplit(config["url"])
    where = f"{parts.scheme}://{parts.netloc}{parts.path}"
    return (
        f"Items from a connector of your own at {where}, at most {config['limit']} an answer;"
        " each item is written once, by its id"
    )


SWITCH_ON = (
    "Switching it on asks the connector only for what happens from then on: an item dated"
    " before then is not written, and an item written before is never written again."
)
SETTINGS_HELP = (
    '{"url": "https://...", "secret_env": "A_VARIABLE", "projects": [], "limit": 50}; url and'
    " secret_env are needed, the variable holds the secret the connector checks (sent only"
    " as its Bearer, only to url), url is https or http to 127.0.0.1 or localhost, limit is"
    " how many items an answer holds at most (1 to 200), each item is written once by its"
    " id, and the source starts off: check the connector first with connector-check, then"
    " switch it on with source-enable --project"
)


# Reading what a connector sends


def moment(value: object) -> datetime | None:
    """An RFC 3339 time with its zone as an aware datetime in UTC; None for anything else
    (a date alone, no zone, a space for the "T", a time that does not exist)."""
    if not isinstance(value, str) or not RFC3339.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value.upper().replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def stamp(when: datetime) -> str:
    """`when` as RFC 3339 in UTC with "Z", as a request's `since` is sent."""
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Item:
    """An item that keeps the protocol: what its draft is made of."""

    id: str
    text: str
    title: str | None
    topic: str | None
    source_ref: str | None
    at: datetime | None


def read_item(raw: object) -> Item | str:
    """The item, or why it breaks the protocol, in fixed words (never its id or text)."""
    if not isinstance(raw, dict):
        return NOT_AN_OBJECT
    item_id, text = raw.get("id"), raw.get("text")
    if not isinstance(item_id, str) or not 1 <= len(item_id) <= ID_MAX:
        return BAD_ID
    if not isinstance(text, str) or not text.strip():
        return BAD_TEXT
    for key, bound in _BOUND.items():
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > bound):
            return bad_field(key)
    if not fit.fits(text):
        return too_long()
    at = None
    if raw.get("at") is not None:
        at = moment(raw["at"])
        if at is None:
            return BAD_AT
    return Item(item_id, text, raw.get("title"), raw.get("topic"), raw.get("source_ref"), at)


def raw_id(raw: object) -> str | None:
    """An item's `id` when it is a string, whatever else it breaks."""
    return raw.get("id") if isinstance(raw, dict) and isinstance(raw.get("id"), str) else None


def shown_id(value: str, secrets: Sequence[str]) -> str:
    """An item's id as connector-check prints it and a finding names it: on one line, at
    most 60 characters of it, quoted; never the secret."""
    if holds_secret(value, tuple(secrets)):
        return "(an id that holds the secret)"
    flat = " ".join(value.split())
    return f"'{flat[:60]}...'" if len(flat) > 60 else f"'{flat}'"


@dataclass(frozen=True)
class Finding:
    """A rule of the protocol an answer breaks (one of the rule names above) and what was
    seen, in words that name an item only by its id and never quote its text or a secret."""

    rule: str
    said: str


@dataclass
class Answer:
    """What examine read of an answer: each item (an Item, or why it breaks the protocol,
    with its id when it had one as a string), the cursor, `more`, and the findings."""

    items: list[Item | str] = field(default_factory=list)
    ids: list[str | None] = field(default_factory=list)
    cursor: str | None = None
    more: bool = False
    findings: list[Finding] = field(default_factory=list)


def examine(
    body: object, *, sent: str | None, limit: int, since: datetime, secrets: Sequence[str]
) -> Answer:
    """An answer's body held to the protocol's rules for the request that sent `sent` as its
    cursor with `limit` and `since`. Its shape first (SHAPE, and nothing more is read when
    it breaks); then its cursor (CURSOR: too long, or holding one of `secrets`), whether
    `more` moves on (MOVES_ON) and each item (BOUNDS, UNIQUE, SINCE), in order."""
    answer = Answer()

    def shape(said: str) -> Answer:
        answer.findings.append(Finding(SHAPE, said))
        return answer

    if not isinstance(body, dict):
        return shape("the answer is not one JSON object")
    protocol = body.get("protocol")
    if isinstance(protocol, bool) or protocol != PROTOCOL:
        return shape(
            f"the connector speaks another version of the protocol, or says none: {PROTOCOL}"
            " is the version asked for, as the number 1"
        )
    items, cursor, more = body.get("items"), body.get("cursor"), body.get("more")
    if not isinstance(items, list):
        return shape("the answer's items are not a list")
    if len(items) > limit:
        return shape(f"the answer holds {len(items)} items, more than the limit of {limit}")
    if cursor is not None and not isinstance(cursor, str):
        return shape("the answer's cursor is neither a string nor null")
    if not isinstance(more, bool):
        return shape("the answer's more is not true or false")
    answer.cursor, answer.more = cursor, more
    if cursor is not None and len(cursor) > CURSOR_MAX:
        answer.findings.append(
            Finding(CURSOR, f"the cursor has {len(cursor):,} characters, past {CURSOR_MAX:,}")
        )
    elif holds_secret(cursor, tuple(secrets)):
        answer.findings.append(Finding(CURSOR, "the cursor holds the secret it was sent"))
    if more and not items and cursor == sent:
        answer.findings.append(
            Finding(
                MOVES_ON,
                "the answer says there is more but gives no item and the cursor it was sent",
            )
        )
    seen: set[str] = set()
    for raw in items:
        read, named = read_item(raw), raw_id(raw)
        answer.items.append(read)
        answer.ids.append(named)
        who = shown_id(named, secrets) if named is not None else "(an item without an id)"
        if isinstance(read, str):
            answer.findings.append(Finding(BOUNDS, f"item {who}: {read}"))
        elif read.at is not None and read.at < since:
            answer.findings.append(
                Finding(SINCE, f"item {who}: its at is before the since it was sent")
            )
        if named is not None and named in seen:
            answer.findings.append(Finding(UNIQUE, f"item {who} is in the answer twice"))
        if named is not None:
            seen.add(named)
    return answer


def error_sentence(data: bytes, secrets: Sequence[str]) -> str | None:
    """An error answer's `error`, when it is a string of at most ERROR_SENTENCE_MAX
    characters and holds neither a secret nor what looks like a credential; on one line."""
    if len(data) > ERROR_BYTES:
        return None
    try:
        said = json.loads(data).get("error")
    except (ValueError, RecursionError, AttributeError):
        return None
    if not isinstance(said, str) or len(said) > ERROR_SENTENCE_MAX:
        return None
    said = " ".join(said.split())
    if not safe_error_text(said, secrets, looks_like=looks_like_a_credential):
        return None
    return said


# One request


class Late(Exception):
    """The pull's deadline came before the answer did."""


@dataclass(frozen=True)
class Reply:
    """A connector's answer: its status, whether it says it is JSON, and its body (an error's
    read only up to ERROR_BYTES; its sentence, when clean, in `error`: the only part of it
    the kind shows)."""

    status: int
    is_json: bool
    data: bytes
    error: str | None = None


def request_body(since: datetime, cursor: str | None, asked: int) -> dict[str, Any]:
    return {"protocol": PROTOCOL, "since": stamp(since), "cursor": cursor, "limit": asked}


async def ask(
    http: httpx.AsyncClient,
    url: str,
    secret: str,
    body: Mapping[str, Any],
    *,
    deadline: datetime | None,
    clock: Clock,
    secrets: Sequence[str] = (),
) -> Reply:
    """One request of protocol 1 to `url`, never following a redirect, under what is left
    until `deadline` (timing.limit). Late when the deadline came first; SourceError when the
    connector could not be reached (by the error's class) or its answer is past
    ANSWER_MAX_BYTES."""
    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    content = json.dumps(body).encode()
    try:
        async with (
            limit(deadline, clock),
            http.stream(
                "POST", url, content=content, headers=headers, follow_redirects=False
            ) as response,
        ):
            status = response.status_code
            media = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if status != 200:
                data = await read_bounded(response, ERROR_BYTES)
                return Reply(
                    status,
                    media == "application/json",
                    data or b"",
                    error_sentence(data, secrets) if data is not None else None,
                )
            data = await read_bounded(response, ANSWER_MAX_BYTES)
            if data is None:
                raise SourceError(TOO_LARGE)
            return Reply(status, media == "application/json", data)
    except TimeoutError:
        raise Late() from None
    except httpx.HTTPError as error:
        said = request_failure(error, "cannot reach the connector at its url")
        raise SourceError(said) from None


TOO_LARGE = "the connector's answer is larger than 4 MiB, the most an answer may be"


def parsed(reply: Reply) -> tuple[object, Finding | None]:
    """A 200 answer's JSON, or the SHAPE finding of an answer that does not say it is JSON
    or is not."""
    if not reply.is_json:
        return None, Finding(SHAPE, "the answer is not JSON (its Content-Type is another)")
    try:
        return json.loads(reply.data), None
    except (ValueError, RecursionError):
        return None, Finding(SHAPE, "the answer is not JSON")


def refusal(reply: Reply, variable: str) -> str:
    """A SourceError's message for an answer that is not 200: from its status and, when it
    gave a clean one, the connector's own sentence."""
    status = reply.status
    said = f": {reply.error}" if reply.error else ""
    if status in (401, 403):
        return (
            f"the connector refused the secret in {variable} (HTTP {status}){said}; nothing more"
            " is asked until the next run"
        )
    if status == 429:
        return (
            f"the connector asked to slow down (HTTP 429){said} on the run's first request;"
            " the next run asks again"
        )
    if 300 <= status < 400:
        return (
            f"the connector answered with a redirect (HTTP {status}){said}, which is not"
            " followed: set url to the address that answers"
        )
    if status >= 500:
        return (
            f"the connector answered HTTP {status}, a fault on its side{said}; the next run"
            " asks again from where this one began"
        )
    return f"the connector answered HTTP {status}{said}; the next run asks again"


# The stored cursor


def stored(since: datetime, cursor: str | None) -> str:
    """What the source keeps: the connector's cursor under the switch-on it was given in."""
    kept = {"v": CURSOR_VERSION, "since": stamp(since), "cursor": cursor}
    return json.dumps(kept, separators=(",", ":"), ensure_ascii=False)


def resumed(kept: str | None, since: datetime) -> str | None:
    """The connector's cursor to send after `kept`: None before the first answer, and after
    the source was switched on again since `kept` was stored. SourceError for a stored
    cursor this kind did not write."""
    if kept is None:
        return None
    try:
        state = json.loads(kept)
    except ValueError:
        state = None
    if (
        not isinstance(state, dict)
        or state.get("v") != CURSOR_VERSION
        or not isinstance(state.get("since"), str)
        or not (state.get("cursor") is None or isinstance(state.get("cursor"), str))
    ):
        raise SourceError(
            "the stored cursor is not one a connector source wrote: remove the source and add"
            " it again to start afresh"
        )
    return state["cursor"] if state["since"] == stamp(since) else None


# The source


def _utc_now() -> datetime:
    return datetime.now(UTC)


def open_source(
    name: str,
    config: Mapping[str, Any],
    *,
    http: httpx.AsyncClient,
    env: Mapping[str, str],
    clock: Clock | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    deadline: datetime | None = None,
    since: datetime | None = None,
) -> "ConnectorSource":
    """The source a stored config describes, with its secret read from `env`. SourceError
    names a variable that is missing or blank, never a value. `sleep` is not used: a 429 is
    never waited out."""
    variable = config["secret_env"]
    secret = (env.get(variable) or "").strip()
    if not secret:
        raise SourceError(
            f"set {variable} in the environment of the process that syncs source {name!r}:"
            " it holds the secret the connector checks"
        )
    return ConnectorSource(
        name, config, secret, http=http, clock=clock, deadline=deadline, since=since
    )


class ConnectorSource:
    """Reads what one connector says is new (see the module docstring)."""

    def __init__(
        self,
        name: str,
        config: Mapping[str, Any],
        secret: str,
        *,
        http: httpx.AsyncClient,
        clock: Clock | None = None,
        deadline: datetime | None = None,
        since: datetime | None = None,
    ) -> None:
        self.name = name
        self.url = config["url"]
        self.variable = config["secret_env"]
        self.limit = config["limit"]
        self.project = config["projects"][0] if config["projects"] else ""
        self._secret = secret
        self.secrets = (secret, f"Bearer {secret}")
        self._http = http
        self._clock = clock or _utc_now
        self.deadline = deadline
        self.since = since

    def __repr__(self) -> str:
        return f"ConnectorSource({self.url!r}, name={self.name!r})"

    def _late(self) -> bool:
        return self.deadline is not None and self._clock() >= self.deadline

    def _draft(self, item: Item, resume: str) -> NoteDraft:
        return NoteDraft(
            project=self.project,
            title=item.title or "",
            text=item.text,
            source_ref=item.source_ref or f"{self.name}:{item.id}",
            label=LABEL,
            resume=resume,
            topic=item.topic,
            item=item.id,
        )

    async def pull(self, cursor: str | None) -> Pulled:
        since = self.since or self._clock()
        sent = resumed(cursor, since)
        drafts: list[NoteDraft] = []
        skipped: list[Skip] = []
        held = 0

        def done(stopped: str | None = None) -> Pulled:
            return Pulled(drafts, skipped, stored(since, sent), stopped=stopped)

        for asked in range(ANSWERS_PER_RUN):
            body = request_body(since, sent, self.limit)
            try:
                if self._late():
                    raise Late()
                reply = await ask(
                    self._http, self.url, self._secret, body, deadline=self.deadline,
                    clock=self._clock, secrets=self.secrets,
                )  # fmt: skip
            except Late:
                if asked:
                    return done(OUT_OF_TIME)
                raise SourceError(
                    "the run's time for asking ran out before the connector answered; the next"
                    " run asks again"
                ) from None
            if reply.status == 429 and asked:  # an answer came: its progress is kept
                return done(SLOWED)
            if reply.status != 200:
                raise SourceError(refusal(reply, self.variable))
            data, broken = parsed(reply)
            answer = (
                Answer(findings=[broken])
                if broken
                else examine(data, sent=sent, limit=self.limit, since=since, secrets=self.secrets)
            )
            fatal = [finding for finding in answer.findings if finding.rule in FATAL]
            if fatal:
                raise SourceError(f"the connector's answer breaks protocol 1: {fatal[0].said}")
            first = len(drafts)
            for read, named in zip(answer.items, answer.ids, strict=True):
                if isinstance(read, str):
                    skipped.append(Skip(named or "", read))
                elif read.at is None or read.at >= since:
                    drafts.append(self._draft(read, stored(since, sent)))
            sent = answer.cursor
            if len(drafts) > first:  # its last draft goes on after the whole answer
                drafts[-1] = replace(drafts[-1], resume=stored(since, sent))
            held += len(reply.data)
            if not answer.more:
                return done()
            if held >= PULL_BYTES:
                return done(HELD_REACHED)
        return done(BOUND_REACHED)


KIND = SourceKind(
    check=check_config,
    open=open_source,
    via=VIA,
    starts_off=True,
    describe=describe,
    switch_on=SWITCH_ON,
    withheld=True,
    own_project=True,
    settings=SETTINGS_HELP,
)
