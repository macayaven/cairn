"""The Langfuse trace source: traces marked for memory become notes, with a link back.

Contract (frozen by tests/acceptance/test_c2_langfuse.py; the API's shapes are recorded in
docs/langfuse-api.md, with what Langfuse's documentation does not state marked as such):

Marking
- A trace is marked by a CATEGORICAL score named `score_name` ("cairn") whose value is the
  name of the cairn project it belongs in, or "skip". The score's comment says why. cairn never
  writes to Langfuse.

Reading (the only two requests; HTTP Basic auth, public key as user, secret key as password)
- GET <base_url>/api/public/v3/scores with name=<score_name>, dataType=CATEGORICAL,
  fromTimestamp and toTimestamp (a closed window [from, to), to the millisecond; the API
  documents the first as inclusive and the second as exclusive), fields=details,subject,
  limit=100 and, only for a window of one instant, cursor. A pull reads [from, now - LAG)
  (LAG is 30 s: the present is never read while scores are still landing in it) in windows
  small enough to arrive in one page: a window whose first answer has a next-page cursor
  is halved, the older half read first. A window of one millisecond that still has more is
  paged to its end (an empty page with a cursor is not the end; a missing or null
  `meta.cursor` is) and then read a second time, and the two readings must hold the same
  scores, or the pull fails ("changed"). An answer holding a score outside the window it
  asked for comes from a server that does not honour the bounds: cairn keeps only what is
  inside, and for the rest of that pull halves nothing and pages each window whole, twice.
  A window's scores are handled in (time, id) order: the order the server sends them in is
  not documented.
- For each score to note, GET <base_url>/api/public/v2/observations with traceId,
  fields=core,basic,io,trace_context, fromStartTime = the score's time - `trace_window`,
  toStartTime = that time + one day and, after the first page, cursor, until the trace's
  root observation (`isRootObservation` true) is found, at most MAX_TRACE_PAGES pages
  and, once MAX_TRACE_BYTES have arrived, no further page.
- A pull's bounds: MAX_SCORE_PAGES requests for scores (so at most that many windows),
  MAX_PULL_TRACE_PAGES pages of observations (it starts no trace once fewer than
  MAX_TRACE_PAGES remain), MAX_PULL_BYTES bytes of answers (it starts no trace without room
  for MAX_TRACE_BYTES and one more answer), and its `deadline` (from the runner: no request
  starts after it, a request under way is given what is left of it and no more, at least
  cairn.sources.timing.FLOOR, so an answer still arriving, its headers or its body, stops at
  it, and a 429 whose wait would pass it is not waited for). A pull that reaches one stops
  cleanly: the window it was reading is left whole, or the score it was at is left
  unhandled, and its cursor starts there (to the millisecond), with no look-back, so every
  such pull moves the cursor on and what is left waits for the next pull, with no error. A
  pull that reaches one before it has read one window or handled one score cannot move on
  (one millisecond holding more than the pages allow, a server that does not honour
  toTimestamp with more than that, or 429s until the deadline): a SourceError saying so
  ("one pull's bounds"), rather than a pull that repeats for ever.
- Every request: no redirect is followed (a 3xx is a SourceError, so the Basic header never
  goes to another address); a body over MAX_RESPONSE_BYTES is not parsed; 429 is retried
  after its Retry-After seconds (capped at MAX_RETRY_AFTER; 1 s when absent or not a
  number), through the injected `sleep`, at most RETRIES times, then SourceError; 401 or
  403 is a SourceError about the credentials that names the variables of `public_key_env`
  and `secret_key_env` (never a value); 404 from the observations endpoint says the
  server must be Langfuse v4 or later; any other status, an answer that is not the
  documented `{"data": [...], "meta": {...}}`, or a network error is a SourceError naming
  the status, the endpoint or the error class, never a body, the URL's query or a key.
- An answer that leaves out a field group cairn asked for (a score without `subject`, an
  observation without `isRootObservation`, `input` or `output`) is a SourceError, never a
  guess; so is a score whose id is not 1 to 128 printable ASCII characters without
  whitespace, or without a timestamp with a time zone. A score whose name or type is not
  the one asked for is left alone.

Handling, oldest first
- A score stamped after now - LAG, in the future included, is deferred: not drafted, not
  remembered, and the cursor never moves past the clock, so it hides no score stamped
  before it; it is read once the clock has passed it by LAG.
- A score is read once (a digest of its id is remembered in the cursor); editing or
  deleting it in Langfuse later changes nothing in cairn. So a draft cairn refused or
  rejected is final for that score: the run's report lists its trace link and codes.
- The server has the keys (it checks them), so whatever it sends may hold them: a score
  whose note would hold either key, the Basic header or its token, in what the server
  sent (before any cut or encoding) or in the draft, is skipped ("held the source's
  credentials"; `secrets` lists them for the runner, which looks again).
- A subject of kind "trace" names that trace, "observation" the trace in its `traceId`;
  any other subject, or one with no trace id, is skipped ("unsupported subject"). The value
  "skip" (exactly) is skipped ("marked skip").
- No root observation: while the score is younger than `grace` it is neither drafted nor
  skipped and the pull stops there, so the cursor stays at or before it; older, it is
  skipped ("no root observation"). A root not found within MAX_TRACE_PAGES pages, or
  MAX_TRACE_BYTES, is skipped ("too many observations"). A score cairn fails on while it
  makes the note of its trace (anything but a SourceError) is skipped (UNREADABLE), so one
  score never stops the source.
- The cursor (JSON: `from`, the next pull's fromTimestamp; `mark`, the newest score
  handled; `seen`, each score handled at or after `from`, by the first 32 hex digits of
  the SHA-256 of its id, with its time: nothing the server chose; version 1, which kept
  the ids, is read and written back as version 2) moves to `overlap` before the newest
  score handled, never back, and never past a score the pull stopped at. So a score that
  reaches Langfuse late, stamped up to `overlap` before the newest one handled, is still
  read, and the cursor holds one id per score handled in that window. A pull stopped by
  its bounds looks back by nothing (see above), so the look-back reaches only as far as
  the start of the pull that caught up. `overlap` must be at least `grace` (ValueError).
  Each draft's `resume` is a cursor whose `from` is the draft's score time and whose
  `seen` holds every score handled at that time up to it: a run that stops right after
  the draft picks up at the next one.

The note (a pure function of what Langfuse answered, so a pull repeated after a crash
drafts the same text, which the store keeps once)
- "# <title>" (the trace's name, else the root observation's, else "trace <id>"; one
  line, whitespace collapsed, at most 80 characters), "Langfuse trace <id>, started <start
  time>.", "## Why it was kept" with the comment (or "No reason was given."), then
  "## Input" and "## Output": those four headings and no other, whatever Langfuse sent.
- A value is the raw string Langfuse returns. One that parses as a JSON object or array is
  shown indented (2 spaces) in a json fence. Anything else, a JSON string or number
  included, and every comment, is cut at `max_chars` first and then shown word for word
  when it is one paragraph of text; when it holds anything Markdown reads as structure (a
  heading, a fence, a setext underline or a rule, a table row or delimiter row, a block
  quote or a list item, HTML anywhere, four columns of indentation, or a blank line
  between two lines of text) it goes in a text fence. A fence is longer than any run of
  backticks in what it holds, so it cannot be closed from inside. A missing value is
  "(none)". A value cut at `max_chars` is followed, after its fence, by "(N more
  characters were left out.)". cairn shows that text and never acts on it.
- The note fits a page (K4): a value is cut at `max_chars` or, when it is less, at a third
  of a page's characters (cairn.store.PAGE_CHARS_MAX, read when the note is made) less
  RESERVE, so the comment, the input and the output fit together. A note still past a
  page's bounds (a fence made long by a value's runs of backticks) is cut at its end and
  ends with NOTE_CUT (cairn.sources.fit.fitted).
- `source_ref` is "<base_url>/project/<projectId>/traces/<traceId>" (both ids
  percent-encoded); `label` is "langfuse".

The config ("langfuse" in cairn.sources.KINDS): `base_url` (https, or http on a loopback
host; no user, password, query or fragment; a path is kept), `projects` (at least one project
name), `public_key_env` and `secret_key_env` (names of environment variables: capitals,
digits and "_") and optionally `score_name` (no comma: the API reads one as a list). No
other key, and no value that looks like a credential: base_url is held to that rule as a
whole and in each host label and path segment, and its refusal names it; a refusal names no
other setting it found one in, and never repeats the value.
"""

import asyncio
import base64
import contextlib
import dataclasses
import hashlib
import json
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from cairn.guard import PatternGuard
from cairn.sources import (
    HELD_CREDENTIALS,
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
from cairn.sources.timing import limit
from cairn.store.memory import is_project_name

SCORES_PATH = "/api/public/v3/scores"
OBSERVATIONS_PATH = "/api/public/v2/observations"
SCORE_FIELDS = "details,subject"
OBSERVATION_FIELDS = "core,basic,io,trace_context"
SCORE_PAGE = 100  # the API's maximum
MAX_SCORE_PAGES = 400  # requests for scores one pull makes, at most (so windows too)
MAX_TRACE_PAGES = 50  # pages of one trace's observations read to find its root
MAX_PULL_TRACE_PAGES = 1_000  # pages of observations one pull reads, at most
MAX_RESPONSE_BYTES = 8 * 1024 * 1024  # Langfuse Cloud caps a response at 5 MB
MAX_PULL_BYTES = 64 * 1024 * 1024  # of every answer one pull reads, at most
MAX_TRACE_BYTES = 16 * 1024 * 1024  # of one trace's observations read to find its root
RETRIES = 5  # of one request answered 429
MAX_RETRY_AFTER = 60.0  # seconds: a Retry-After above this waits this long
DEFAULT_RETRY_AFTER = 1.0  # seconds, when a 429 does not say
SKIP = "skip"
LAG = timedelta(seconds=30)  # the newest window ends this far behind the clock
MS = timedelta(milliseconds=1)  # the API's precision, and the narrowest window
TITLE_MAX = 80
SCORE_NAME_MAX = 200
VIA = "langfuse"
LABEL = "langfuse"
CONFIG_KEYS = ("base_url", "projects", "public_key_env", "secret_key_env", "score_name")
REQUIRED_KEYS = CONFIG_KEYS[:4]
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
CURSOR_VERSION = 2  # 1: `seen` held the ids themselves; read, and written back as 2
SCORE_ID = re.compile(r"[!-~]{1,128}")  # printable ASCII, no whitespace
DIGEST = re.compile(r"[0-9a-f]{32}")
_KEY_PREFIXES = ("pk-lf-", "sk-lf-")  # how Langfuse's keys start
_WAIT = object()  # a score whose trace has not arrived yet


UNREADABLE = "unreadable: this server failed on what Langfuse gave for its trace"
RESERVE = 2_000  # characters of a note that are not its three values: headings, fences, lines
NOTE_CUT = "(The rest of this note was left out: it is longer than a page holds.)"


class _Spent(Exception):
    """A bound of this pull is reached (its pages, its bytes or its deadline): what is
    left waits for the next pull."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    """ISO 8601 in UTC to the millisecond, cut (never rounded up), with "Z"."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _moment(value: object) -> datetime | None:
    """A timestamp from the API, as an aware datetime in UTC; None when it is not one with a
    time zone."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else None


def _ms(moment: datetime) -> datetime:
    """`moment` cut to the millisecond, the precision the API reads times at."""
    return moment.replace(microsecond=moment.microsecond - moment.microsecond % 1000)


def _one_line(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


# The config


def check_config(config: object) -> dict[str, Any]:
    """The config as stored, or ValueError. A message names a setting, never its value."""
    if not isinstance(config, Mapping):
        raise ValueError("a langfuse source's config is an object of settings")
    if set(config) - set(CONFIG_KEYS):
        raise ValueError(f"a langfuse source takes only these settings: {', '.join(CONFIG_KEYS)}")
    for key in REQUIRED_KEYS:
        if key not in config:
            raise ValueError(f"a langfuse source needs {key}")
    checked: dict[str, Any] = {"base_url": _check_base_url(config["base_url"])}
    projects = config["projects"]
    if not isinstance(projects, list | tuple) or not projects:
        raise ValueError("projects lists the names of the projects the source may write to")
    kept: dict[str, str] = {}
    for project in projects:
        if not isinstance(project, str) or not is_project_name(project):
            raise ValueError("projects holds project names (not ids)")
        kept.setdefault(project.casefold(), project)
    checked["projects"] = list(kept.values())
    for key in ("public_key_env", "secret_key_env"):
        if not isinstance(config[key], str) or not ENV_NAME.fullmatch(config[key]):
            raise ValueError(
                f"{key} names an environment variable (capitals, digits and _), which holds"
                " the key: the key itself never goes in a config"
            )
        checked[key] = config[key]
    if "score_name" in config:
        name = config["score_name"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > SCORE_NAME_MAX
            or "," in name
            or not name.isprintable()
        ):
            raise ValueError(
                f"score_name is 1 to {SCORE_NAME_MAX} printable characters, without a comma"
            )
        checked["score_name"] = name
    _no_credential(checked)
    return checked


def _check_base_url(value: object) -> str:
    return source_url.check_base_url(
        value,
        what="the Langfuse server",
        comes_from="the keys come from the environment",
        named_by="public_key_env and secret_key_env",
        holds="a credential",
        http_said="on a loopback host",
        looks_like=_looks_like_a_credential,
    )


def _looks_like_a_credential(value: str) -> bool:
    """The one rule every setting is held to: a Langfuse key's prefix, or a pattern of the
    "secrets" guard."""
    return value.casefold().startswith(_KEY_PREFIXES) or bool(PatternGuard("secrets").check(value))


def _no_credential(config: Mapping[str, Any]) -> None:
    values = [config["base_url"], *config["projects"], config["public_key_env"]]
    values += [config["secret_key_env"], config.get("score_name") or ""]
    for value in values:
        if _looks_like_a_credential(value):
            raise ValueError(
                "a value in this config looks like a credential: name the environment variable"
                " that holds it instead"
            )


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
) -> "LangfuseSource":
    """The source a stored config describes, with its keys read from `env`. SourceError
    names a variable that is missing or blank, never a value. `since` (the source's last
    switch-on, S2) is not used: a trace is chosen by a person marking it, so a mark made
    while the source was off is read once it is on again, from where its cursor stood."""
    keys = []
    for setting, which in (("public_key_env", "public"), ("secret_key_env", "secret")):
        variable = config[setting]
        value = (env.get(variable) or "").strip()
        if not value:
            raise SourceError(
                f"set {variable} in the environment of the process that syncs source"
                f" {name!r}: it holds the Langfuse {which} key"
            )
        keys.append(value)
    return LangfuseSource(
        config["base_url"],
        *keys,
        http=http,
        name=name,
        score_name=config.get("score_name", "cairn"),
        clock=clock,
        sleep=sleep,
        deadline=deadline,
        variables=(config["public_key_env"], config["secret_key_env"]),
    )


def describe(config: Mapping[str, Any]) -> str:
    """What a source reads, for GET /sources: the score that marks a trace, in quotes so a
    score named like a word reads as a name, and where."""
    score = config.get("score_name", "cairn")
    return f"Traces marked with the score “{score}” at {config['base_url']}"


KIND = SourceKind(
    check=check_config,
    open=open_source,
    via=VIA,
    describe=describe,
    switch_on=(
        "Switched on again, it goes on from where it stopped: traces marked for {product} while it"
        " was off are read too."
    ),
    withheld=True,
)


# Showing a value


def shown(value: object, max_chars: int) -> str:
    """A trace's input or output as the note shows it (see the module docstring)."""
    if value is None:
        return "(none)"
    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            parsed = None
    body = value if isinstance(value, str) else None
    if isinstance(parsed, dict | list):
        with contextlib.suppress(ValueError, RecursionError):
            return _bounded_text(
                json.dumps(parsed, indent=2, ensure_ascii=False), max_chars, "json"
            )
    if body is None:  # a value Langfuse sent already parsed, and not an object or array
        body = json.dumps(value, ensure_ascii=False, default=str)
    return _bounded_text(body, max_chars)


def shown_text(text: str, max_chars: int) -> str:
    """A score's comment as the note shows it: cut and fenced like a plain value, never read
    as JSON."""
    return _bounded_text(text, max_chars)


# A line Markdown reads as more than a paragraph's text: a heading, a fence, a setext
# underline or a rule, a table row, a block quote or a list item (either can hold a
# heading), or four columns of indentation (code). HTML is looked for anywhere.
_STRUCTURE = re.compile(
    r" {0,3}(?:#{1,6}(?:[ \t]|$)|`{3,}|~{3,}|=+[ \t]*$|-+[ \t]*$|(?:[-*_][ \t]*){3,}$"
    r"|\||>|[-+*](?:[ \t]|$)|\d{1,9}[.)](?:[ \t]|$))"
    r"|.*\|[ \t]*:?-+:?[ \t]*(?:\||$)"
)
_HTML = re.compile(r"<[A-Za-z/!?]")
_LINE_END = re.compile(r"\r\n|\r|\n")


def _structured(text: str) -> bool:
    """Whether Markdown would read `text` as anything but one paragraph of text."""
    if _HTML.search(text):
        return True
    lines = _LINE_END.split(text)
    # Blank lines around a value change nothing. Found by index: popping the first of many
    # blank lines one at a time took time growing with the square of their number.
    first = next((i for i, line in enumerate(lines) if line.strip()), len(lines))
    last = next((i for i in range(len(lines) - 1, first - 1, -1) if lines[i].strip()), first - 1)
    for line in lines[first : last + 1]:
        if not line.strip():
            return True  # more than one paragraph
        columns = line.expandtabs(4)
        if len(columns) - len(columns.lstrip(" ")) >= 4 or _STRUCTURE.match(line):
            return True
    return False


def _bounded_text(body: str, max_chars: int, kind: str | None = None) -> str:
    """`body` cut at `max_chars`, then in a `kind` fence (json; text, when it holds
    anything Markdown reads as structure) longer than any run of backticks in it, so it
    cannot close its fence; then how much was left out."""
    left = len(body) - max_chars
    if left > 0:
        body = body[:max_chars]
    if kind is None and _structured(body):
        kind = "text"
    if kind is not None:
        runs = re.findall(r"`+", body)
        fence = "`" * max(3, *(len(run) + 1 for run in runs)) if runs else "```"
        body = f"{fence}{kind}\n{body}\n{fence}"
    if left > 0:
        body += f"\n\n({left:,} more characters were left out.)"
    return body


# The source


class LangfuseSource:
    """Reads marked traces from one Langfuse project (see the module docstring)."""

    def __init__(
        self,
        base_url: str,
        public_key: str,
        secret_key: str,
        *,
        http: httpx.AsyncClient,
        name: str = "langfuse",
        score_name: str = "cairn",
        max_chars: int = 20_000,
        lookback: timedelta = timedelta(days=7),
        trace_window: timedelta = timedelta(days=30),
        grace: timedelta = timedelta(hours=1),
        overlap: timedelta = timedelta(hours=1),
        clock: Clock | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        deadline: datetime | None = None,
        variables: tuple[str, str] | None = None,
    ) -> None:
        for option, length in (
            ("lookback", lookback),
            ("trace_window", trace_window),
            ("grace", grace),
            ("overlap", overlap),
        ):
            if length <= timedelta(0):
                raise ValueError(f"{option} is a positive length of time")
        if overlap < grace:
            raise ValueError("overlap is at least grace, or a score still waiting could be lost")
        if max_chars < 1:
            raise ValueError("max_chars is at least 1")
        self.base_url = _check_base_url(base_url)
        self.name = name
        self.score_name = score_name
        self.max_chars = max_chars
        self.lookback, self.trace_window = lookback, trace_window
        self.grace, self.overlap = grace, overlap
        self._auth = httpx.BasicAuth(public_key, secret_key)
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.secrets = (public_key, secret_key, f"Basic {token}", token)
        self.variables = variables  # the names of the two keys' variables, never their values
        self._http = http
        self._clock = clock or _utc_now
        self._sleep = sleep or asyncio.sleep
        self.deadline = deadline  # no request starts, and no wait ends, after it
        self._trace_pages = 0
        self._score_pages = 0
        self._bytes = 0
        self._bounded = True
        self._reading: datetime | None = None  # the start of the window being read

    def __repr__(self) -> str:
        return f"LangfuseSource({self.base_url!r}, name={self.name!r})"

    def _late(self) -> bool:
        return self.deadline is not None and self._clock() >= self.deadline

    async def pull(self, cursor: str | None) -> Pulled:
        now = self._clock().astimezone(UTC)
        if cursor is None:
            start, mark, seen = now - self.lookback, None, {}
        else:
            start, mark, seen = _decode(cursor)
        start, end = _ms(start), _ms(now - LAG)
        self._trace_pages = self._score_pages = self._bytes = 0
        self._bounded, self._reading = True, None
        drafts: list[NoteDraft] = []
        skipped: list[Skip] = []
        stopped: datetime | None = None
        spent: str | None = None  # the bound that stopped this pull
        handled = 0
        windows = self._windows(start, end)
        try:
            async with contextlib.aclosing(windows):
                async for scores in windows:
                    for moment, score_id, score in scores:
                        if _digest(score_id) in seen:
                            continue
                        try:
                            outcome = await self._handle(score, score_id, moment, now)
                        except _Spent as bound:  # the score is not handled: the next pull is
                            stopped, spent = moment, str(bound)
                            break
                        if outcome is _WAIT:
                            stopped = moment
                            break
                        handled += 1
                        seen[_digest(score_id)] = moment
                        mark = moment if mark is None else max(mark, moment)
                        if isinstance(outcome, NoteDraft):  # resumed from: after it alone
                            done = {key: m for key, m in seen.items() if m >= moment}
                            resume = _encode(moment, mark, done)
                            drafts.append(dataclasses.replace(outcome, resume=resume))
                        else:
                            skipped.append(outcome)  # type: ignore[arg-type]
                    if stopped is not None:
                        break
        except _Spent as bound:  # the window being read is left whole to the next pull
            stopped, spent = self._reading, str(bound)
        if spent is not None and stopped is not None:
            stopped = _ms(stopped)
            if stopped <= start and not handled:
                raise SourceError(
                    f"Langfuse's scores from {_stamp(start)} on cannot be read within one"
                    f" pull's bounds ({spent}), so the cursor cannot move: a single"
                    " millisecond holds too many, or the server does not honour toTimestamp,"
                    " or it kept answering 429 until the pull's deadline"
                )
            following = stopped  # no look-back: what is left is read first
        else:
            following = start if mark is None else max(start, mark - self.overlap)
            if stopped is not None:
                following = min(following, stopped)
        following = min(following, now)  # never past the clock
        kept = {score_id: m for score_id, m in seen.items() if m >= following}
        return Pulled(drafts=drafts, skipped=skipped, cursor=_encode(following, mark, kept))

    # Scores

    async def _windows(
        self, start: datetime, end: datetime
    ) -> AsyncIterator[list[tuple[datetime, str, dict[str, Any]]]]:
        """The scores marked for cairn in [start, end), one closed window at a time, oldest
        first, each window's in (time, id) order (see the module docstring)."""
        pending = [(start, end)] if start < end else []
        while pending:
            low, high = pending.pop()
            self._reading = low
            items, following = await self._score_page(low, high, None)
            if following is not None and self._bounded and high - low > MS:
                middle = low + MS * ((high - low) // MS // 2)
                pending += [(middle, high), (low, middle)]  # the older half first
                continue
            if following is not None:  # one instant, or a server that ignores the bounds
                items = await self._rest(low, high, items, following)
                again = await self._rest(low, high, *await self._score_page(low, high, None))
                if _identities(self._inside(items, low, high)) != _identities(
                    self._inside(again, low, high)
                ):
                    raise SourceError(
                        f"the scores Langfuse holds between {_stamp(low)} and {_stamp(high)}"
                        " changed between two readings of them: the next pull reads them again"
                    )
            yield self._inside(items, low, high)

    async def _rest(
        self,
        low: datetime,
        high: datetime,
        items: list[tuple[datetime, str, dict[str, Any]]],
        following: str | None,
    ) -> list[tuple[datetime, str, dict[str, Any]]]:
        """`items`, then every later page of the window's answer."""
        items = list(items)
        while following is not None:
            more, following = await self._score_page(low, high, following)
            items += more
        return items

    async def _score_page(
        self, low: datetime, high: datetime, cursor: str | None
    ) -> tuple[list[tuple[datetime, str, dict[str, Any]]], str | None]:
        """One page of scores for the window [low, high), each checked, with its time; and
        the next page's cursor. A score outside the window means the server does not honour
        the bounds: no more halving in this pull."""
        if self._score_pages >= MAX_SCORE_PAGES:
            raise _Spent(f"{MAX_SCORE_PAGES} requests for scores")
        self._score_pages += 1
        params = {
            "name": self.score_name,
            "dataType": "CATEGORICAL",
            "fromTimestamp": _stamp(low),
            "toTimestamp": _stamp(high),
            "fields": SCORE_FIELDS,
            "limit": str(SCORE_PAGE),
        }
        data, following = await self._page(SCORES_PATH, params, cursor)
        found = []
        for score in data:
            score_id, moment = score.get("id"), _moment(score.get("timestamp"))
            if not isinstance(score_id, str) or not SCORE_ID.fullmatch(score_id):
                raise SourceError(
                    "Langfuse sent a score whose id is not 1 to 128 printable ASCII characters"
                    " without spaces"
                )
            if moment is None:
                raise SourceError("Langfuse sent a score without a time with a time zone")
            if not low <= moment < high:
                self._bounded = False
            found.append((moment, score_id, score))
        return found, following

    def _inside(
        self, items: list[tuple[datetime, str, dict[str, Any]]], low: datetime, high: datetime
    ) -> list[tuple[datetime, str, dict[str, Any]]]:
        """The window's scores marked for cairn, in (time, id) order."""
        found = []
        for moment, score_id, score in items:
            if not low <= moment < high:
                continue  # sent although the window does not hold it
            if score.get("name") != self.score_name or score.get("dataType") != "CATEGORICAL":
                continue  # not a mark for cairn, whatever the server sent
            if "subject" not in score:
                raise SourceError(
                    "Langfuse sent scores without their subject although fields=subject asked"
                    " for it: the server does not serve the v3 scores API as documented"
                )
            found.append((moment, score_id, score))
        return sorted(found, key=lambda item: (item[0], item[1]))

    async def _handle(
        self, score: dict[str, Any], score_id: str, moment: datetime, now: datetime
    ) -> NoteDraft | Skip | object:
        value = score.get("value")
        if value == SKIP:
            return Skip(score_id, "marked skip")
        subject = score.get("subject")
        trace_id = None
        if isinstance(subject, dict):
            if subject.get("kind") == "trace":
                trace_id = subject.get("id")
            elif subject.get("kind") == "observation":
                trace_id = subject.get("traceId")
        if not isinstance(trace_id, str) or not trace_id:
            return Skip(score_id, "unsupported subject")
        if not isinstance(value, str) or not value:
            return Skip(score_id, "no project named")
        langfuse_project = score.get("projectId")
        if not isinstance(langfuse_project, str) or not langfuse_project:
            raise SourceError("Langfuse sent a score without its projectId")
        if MAX_PULL_TRACE_PAGES - self._trace_pages < MAX_TRACE_PAGES:
            raise _Spent(f"{MAX_PULL_TRACE_PAGES} pages of observations")
        if MAX_PULL_BYTES - self._bytes < MAX_TRACE_BYTES + MAX_RESPONSE_BYTES:
            raise _Spent(f"{MAX_PULL_BYTES} bytes of answers")  # no room for one more trace
        root, complete = await self._root(trace_id, moment)
        if root is None:
            if not complete:
                return Skip(score_id, "too many observations")
            if now - moment < self.grace:
                return _WAIT
            return Skip(score_id, "no root observation")
        try:
            return self._draft(score, score_id, trace_id, value, langfuse_project, root)
        except SourceError:
            raise
        except Exception:  # one score cairn fails on must not stop the source for good
            return Skip(score_id, UNREADABLE)

    def _draft(
        self,
        score: dict[str, Any],
        score_id: str,
        trace_id: str,
        value: str,
        langfuse_project: str,
        root: dict[str, Any],
    ) -> NoteDraft | Skip:
        """The note of a score whose trace's root was found (no request is made here)."""
        title = (
            _one_line(root.get("traceName")) or _one_line(root.get("name")) or f"trace {trace_id}"
        )[:TITLE_MAX].strip()
        most = self.cut_at()
        started = _moment(root.get("startTime"))
        comment = score.get("comment")
        reason = shown_text(comment, most) if isinstance(comment, str) and comment.strip() else None
        text = "\n".join(
            [
                f"# {title}",
                "",
                f"Langfuse trace {_one_line(trace_id)}, started"
                f" {_stamp(started) if started else 'at an unknown time'}.",
                "",
                "## Why it was kept",
                "",
                reason or "No reason was given.",
                "",
                "## Input",
                "",
                shown(root["input"], most),
                "",
                "## Output",
                "",
                shown(root["output"], most),
                "",
            ]
        )
        text = fit.fitted(text, NOTE_CUT)
        origin = f"{self.base_url}/project/{quote(langfuse_project, safe='')}/traces/"
        draft = NoteDraft(
            project=value,
            title=title,
            text=text,
            source_ref=origin + quote(trace_id, safe=""),
            label=LABEL,
        )
        # What the server chose, raw (a value cut at max_chars could cut a key in two, and
        # a link percent-encodes one), and the draft as written.
        raw = [value, comment, trace_id, langfuse_project, root.get("traceName"), root.get("name")]
        raw += [root["input"], root["output"], *vars(draft).values()]
        if any(holds_secret(item, self.secrets) for item in raw):
            return Skip(score_id, HELD_CREDENTIALS)
        return draft

    def cut_at(self) -> int:
        """The characters a value is cut at: `max_chars`, or its share of a page when that is
        less (K4: a third of a page's characters, read now, less RESERVE for the lines the
        note says itself), so the three values together fit a page."""
        return max(1, min(self.max_chars, (fit.chars() - RESERVE) // 3))

    async def _root(self, trace_id: str, moment: datetime) -> tuple[dict[str, Any] | None, bool]:
        """The trace's root observation, and whether every page was read."""
        params = {
            "traceId": trace_id,
            "fields": OBSERVATION_FIELDS,
            "fromStartTime": _stamp(moment - self.trace_window),
            "toStartTime": _stamp(moment + timedelta(days=1)),
        }
        cursor: str | None = None
        began = self._bytes
        for _ in range(MAX_TRACE_PAGES):
            if self._bytes - began >= MAX_TRACE_BYTES:
                break  # read as much of this trace as one trace may take
            self._trace_pages += 1
            data, cursor = await self._page(OBSERVATIONS_PATH, params, cursor)
            for observation in data:
                if "isRootObservation" not in observation:
                    raise SourceError(
                        "Langfuse sent observations without isRootObservation although"
                        " fields=basic asked for it"
                    )
                if (
                    observation.get("traceId") != trace_id
                    or observation["isRootObservation"] is not True
                ):
                    continue
                if "input" not in observation or "output" not in observation:
                    raise SourceError(
                        "Langfuse sent an observation without input and output although"
                        " fields=io asked for them"
                    )
                return observation, True
            if cursor is None:
                return None, True
        return None, False

    # Requests

    async def _pages(
        self, path: str, params: dict[str, str], most: int
    ) -> tuple[list[dict[str, Any]], bool]:
        """Every item of every page, at most `most` pages; and whether that was all."""
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(most):
            data, cursor = await self._page(path, params, cursor)
            items += data
            if cursor is None:
                return items, True
        return items, False

    async def _page(
        self, path: str, params: dict[str, str], cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        body = await self._get(path, {**params, "cursor": cursor} if cursor else params)
        data, meta = body.get("data"), body.get("meta")
        if not isinstance(data, list) or not isinstance(meta, dict):
            raise SourceError(f"Langfuse's answer to GET {path} is not the documented shape")
        following = meta.get("cursor")
        if following is not None and (not isinstance(following, str) or not following):
            raise SourceError(f"Langfuse's answer to GET {path} has a cursor that is not one")
        if not all(isinstance(item, dict) for item in data):
            raise SourceError(f"Langfuse's answer to GET {path} is not the documented shape")
        return data, following

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = self.base_url + path
        for attempt in range(RETRIES + 1):
            if self._bytes >= MAX_PULL_BYTES:
                raise _Spent(f"{MAX_PULL_BYTES} bytes of answers")
            if self._late():
                raise _Spent("its deadline")
            try:
                async with (
                    limit(self.deadline, self._clock),
                    self._http.stream(
                        "GET", url, params=params, auth=self._auth, follow_redirects=False
                    ) as response,
                ):
                    status = response.status_code
                    if status == 200:
                        body = await _bounded(response, path, self._late)
                        self._bytes += len(body)
                        return _parsed(body, path)
                    wait = _retry_after(response.headers.get("retry-after"))
            except TimeoutError:
                raise _Spent("its deadline, which an answer still arriving passed") from None
            except httpx.HTTPError as error:
                raise SourceError(
                    f"cannot reach Langfuse for GET {path}: {type(error).__name__}"
                ) from None
            if status == 429 and attempt < RETRIES:
                if (
                    self.deadline is not None
                    and self._clock() + timedelta(seconds=wait) > self.deadline
                ):
                    raise _Spent("its deadline, which a wait for a 429 would pass")
                await self._sleep(wait)
                continue
            raise SourceError(_refusal(status, path, self.variables))
        raise AssertionError("unreachable")  # pragma: no cover


async def _bounded(
    response: httpx.Response, path: str, late: Callable[[], bool] = lambda: False
) -> bytes:
    """The body, at most MAX_RESPONSE_BYTES; `late()` is asked after every chunk, so a body
    that drips in cannot outlast the pull's deadline."""
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise SourceError(f"Langfuse's answer to GET {path} is larger than this server reads")
    body = bytearray()
    async for part in response.aiter_bytes():
        if late():
            raise _Spent("its deadline, which an answer still arriving passed")
        body += part
        if len(body) > MAX_RESPONSE_BYTES:
            raise SourceError(f"Langfuse's answer to GET {path} is larger than this server reads")
    return bytes(body)


def _parsed(body: bytes, path: str) -> dict[str, Any]:
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        parsed = None
    if not isinstance(parsed, dict):
        raise SourceError(f"Langfuse's answer to GET {path} is not the documented JSON object")
    return parsed


def _retry_after(value: str | None) -> float:
    try:
        seconds = float(value or "")
    except ValueError:
        return DEFAULT_RETRY_AFTER
    if not math.isfinite(seconds) or seconds < 0:
        return DEFAULT_RETRY_AFTER
    return min(seconds, MAX_RETRY_AFTER)


def _refusal(status: int, path: str, variables: tuple[str, str] | None = None) -> str:
    if status == 429:
        return f"Langfuse still answered 429 (rate limited) to GET {path} after {RETRIES} retries"
    if status in (401, 403) and variables:
        public, secret = variables
        return (
            f"Langfuse refused the credentials (HTTP {status}): check that {public} holds the"
            f" Langfuse public key and {secret} the Langfuse secret key"
        )
    if status in (401, 403):  # a source built without its settings: no names to give
        return (
            f"Langfuse refused the credentials (HTTP {status}): check the public and secret key"
            " in the environment variables the source names"
        )
    if 300 <= status < 400:
        return (
            f"Langfuse answered GET {path} with a redirect (HTTP {status}), which this server does"
            " not follow: set base_url to the address that answers"
        )
    if status == 404 and path == OBSERVATIONS_PATH:
        return (
            f"GET {path} answered 404: this source needs Langfuse v4 or later (Langfuse Cloud,"
            " or a self-hosted server on v4)"
        )
    if status == 404 and path == SCORES_PATH:
        return f"GET {path} answered 404: this source needs Langfuse v3.179.0 or later"
    return f"Langfuse answered HTTP {status} to GET {path}"


# The cursor


def _identities(items: list[tuple[datetime, str, dict[str, Any]]]) -> list[tuple]:
    return [(moment, score_id) for moment, score_id, _ in items]


def _digest(score_id: str) -> str:
    """What the cursor keeps of a score id: never the id, which the server chose."""
    return hashlib.sha256(score_id.encode()).hexdigest()[:32]


def _encode(start: datetime, mark: datetime | None, seen: dict[str, datetime]) -> str:
    return json.dumps(
        {
            "v": CURSOR_VERSION,
            "from": start.isoformat(),
            "mark": mark.isoformat() if mark else None,
            "seen": {score_id: seen[score_id].isoformat() for score_id in sorted(seen)},
        },
        separators=(",", ":"),
    )


def _decode(cursor: str) -> tuple[datetime, datetime | None, dict[str, datetime]]:
    def broken() -> SourceError:
        return SourceError("the stored cursor is not one this Langfuse source wrote")

    try:
        state = json.loads(cursor)
    except ValueError:
        raise broken() from None
    if not isinstance(state, dict) or state.get("v") not in (1, CURSOR_VERSION):
        raise broken()
    start = _moment(state.get("from"))
    mark = _moment(state.get("mark")) if state.get("mark") is not None else None
    seen_raw = state.get("seen")
    if start is None or not isinstance(seen_raw, dict):
        raise broken()
    seen = {}
    for key, value in seen_raw.items():
        moment = _moment(value)
        if moment is None:
            raise broken()
        if state["v"] == 1:  # it held the ids themselves
            key = _digest(key)
        elif not DIGEST.fullmatch(key):
            raise broken()
        seen[key] = moment
    if state.get("mark") is not None and mark is None:
        raise broken()
    return start, mark, seen
