"""C5 acceptance tests (frozen): the Google Calendar source, ended events read from one calendar.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/sources/google_calendar.py,
src/cairn/sources/markup.py and src/cairn/sources/__init__.py. Google's request and response
shapes are recorded in docs/google-calendar-api.md (signing in: docs/google-auth.md, pinned by
tests/acceptance/test_c4_google_meet.py and not tested again here). Where the notes say Google's
pages are silent or disagree, the connector depends on neither reading: the stand-in below
behaves both ways, and the tests run under both where it matters.

The kind
- `KINDS["google_calendar"]` (cairn.sources.google_calendar). It starts off. Via and label
  "google-calendar". It asks for one scope,
  https://www.googleapis.com/auth/calendar.events.readonly. Its `default_project` is "calendar".
  `describe(config)` says in one sentence which calendar it reads (the calendar id and, when
  there is one, the subject), never a credential or a variable's value.
- `check(config)` returns the config with its defaults filled in, or raises ValueError naming
  the setting at fault, never a value that could be a credential:
  - `credentials_env` (required): the name of the environment variable holding the Google
    credential (as for Meet). A credential pasted there is refused and not repeated.
  - `subject` (optional): one email address, the person the service account acts for.
  - `calendar_id` (optional): the one calendar it reads, "primary" unless given; a non-empty
    string of at most 254 characters with no space and no control character.
  - `projects`: at most one project; may be empty while the source is off.
  - `base_url`: "https://www.googleapis.com/calendar/v3" unless given; https, or http on this
    machine.
  - No other setting.
- `open`: with a refresh-token credential, `subject` must be absent (SourceError naming
  `subject`). With a service account, either `subject` is given, or the account acts as itself
  and `calendar_id` must name a calendar other than "primary" (SourceError naming `calendar_id`
  and saying that such an account reads a calendar shared with its address). Nothing is sent
  before the first pull.
- Signing in goes through cairn.sources.google_auth (`load_credential`, `access_token`), as for
  Meet: one access token per pull, asked for by the pull with the scope above (and the subject,
  when there is one; none when the account acts as itself) and used for all of it, sent only in
  the `Authorization: Bearer` header. The private key, the client secret and the refresh token
  are in the source's `secrets` once it is opened, and the access token once the pull has it,
  so the runner withholds a draft that holds any of them (HELD_CREDENTIALS). None of them is
  ever in a URL, a log line, an exception message or the cursor. A refusal by the token
  endpoint fails the pull before the calendar is asked anything.
- The calendar is read at `base_url`: `<base_url>/calendars/<calendar_id>/events`, the id
  percent-encoded in the path (it holds "@", and may hold "#").

What it reads
- The events of that one calendar that ended at or after `since` (the last switch-on) and have
  ended by the clock's now. An event still going on, or not begun, is left for a later pull; one
  that ended before `since` is never written. Each event once: after its draft is written, an
  edit or a cancellation at Google changes nothing in cairn (decision 26). An event that is
  moved to a later time ends again: once the cursor has let its digest go (the overlap), it is
  read when it has ended at its new time, as the event it then is. Recurring events are read as
  their instances (`singleEvents=true`), each with its own id.
- Passed over silently (not written, not reported as skipped): `status` "cancelled"; an
  `eventType` other than "default" (absent counts as "default"); `visibility` "private" or
  "confidential"; an event the calendar's own person declined (the attendee with `self` true
  has `responseStatus` "declined").
- A timed event has ended at its `end.dateTime`. An all-day event (`end.date`, exclusive) ends
  at 00:00 on its `end.date` in the calendar's own time zone: the listing's top-level
  `timeZone`, an IANA name, read with Python's `zoneinfo`. That is the moment Google's own
  `timeMin` uses for it, and the moment both the `since` line and "has ended" are drawn at: an
  all-day event that was over locally before the switch-on is not written. The implementer
  declares the `tzdata` package as a dependency, so the zone database is on every machine.
- The fallback: only when the listing gives no `timeZone`, or a name `zoneinfo` does not know,
  an all-day event counts as ended at 12:00 UTC on its `end.date`. The day is then over in every
  zone west of UTC+12, so nothing is read before it is over (the two days of overlap cover what
  the later moment costs). An unknown zone is never an error.

How it finds them
- Every pull calls events.list for the calendar with `singleEvents=true`, `orderBy=startTime`,
  `timeMin` two days before the cursor's mark (the first pull's mark is `since`), `timeMax` the
  clock's now (RFC 3339, UTC), `maxResults=2500`, and nothing else: no `syncToken`,
  `showDeleted`, `maxAttendees` or `q`. Every page is asked for with the same parameters beside
  its `pageToken`. It does not rely on Google's order: it writes by end time, then event id.
- The two days of overlap: under the fallback, an all-day event on a calendar east of UTC
  leaves Google's listing (midnight there) up to 26 hours before cairn counts it as ended, and
  is still read; an event added to the calendar up to a day after it ended is still read; and
  `timeMin` being exclusive loses nothing.
- The cursor is JSON the connector owns: the mark, and short digests of the events it wrote,
  kept while a listing could still return them. It holds no token, key, secret, event id, title,
  name or address, and stays bounded however many weeks it runs.
- A pull that reaches its `deadline` stops asking for pages and returns what it finished; every
  draft carries a `resume`, and the next pull goes on with no draft written twice and none
  skipped.

What it writes, one draft an event, into the config's project
- `title`: the event's `summary`, or "Untitled event". `topic`: the title, a comma and the
  start date in UTC ("Quarterly planning, 2026-11-12"; an all-day event's `start.date`), as the
  Granola source gives its topics: each occurrence of a weekly meeting has its own page. A long
  title is shortened (at a word where it can) so the page's slug, cut at 64 characters, keeps
  the date.
- `text`: Markdown. A heading with the title, then the facts: when (start and end in UTC as
  "YYYY-MM-DD HH:MM"; an all-day event's day or days, the last being the day before `end.date`);
  where (`location`); the organizer by display name; the attendees by display name, people only
  (a `resource`, a room, is left out), those without a name counted ("and 3 more"); and, for a
  "hangoutsMeet" conference with a `conferenceId`, a line beginning "Meet" with the code exactly
  as Google gives it. Google's page says a `conferenceId` is not for display; for
  "hangoutsMeet" it is the meeting code, the same code that is in the meeting's address, which
  every guest sees, and the Meet source titles its pages with it. No email address is among the
  title and the facts. Then the description:
  `cairn.sources.markup.html_to_text` of it, its words otherwise unchanged.
- `source_ref`: "calendar:<event id>". `label`: "google-calendar".

HTML to text: `cairn.sources.markup.html_to_text(html: str) -> str`, the one conversion every
source uses (the Gmail source's test checks the same example pairs). It is pure (no network, no
clock), uses only the standard library, and never raises on malformed HTML: it does what it can.
1. Comments, and the whole content of `script`, `style`, `head`, `title` and `template`
   elements, are dropped.
2. `br` is a line break. Each of `p`, `div`, `section`, `article`, `header`, `footer`, `h1` to
   `h6`, `ul`, `ol`, `table`, `blockquote`, `pre`, `hr` begins and ends a paragraph (a blank line
   between it and what is around it). `li` and `tr` each begin a new line.
3. A list item's line begins with "- " (in `ul` and in `ol` alike; nesting is not shown).
4. The cells of a table row (`td`, `th`) are on one line, separated by " | ".
5. A heading's text is a paragraph of its own, with no "#" in front: a description must not
   start sections in the note it is written into.
6. A link `<a href="URL">words</a>` is written "words (URL)". When the words are empty, or equal
   the URL (ignoring a trailing "/" and an "http://" or "https://" in front), it is written as
   the URL alone. A `mailto:` or `tel:` link is written as its words alone, and a link whose
   `href` is empty, "#..." or "javascript:..." as its words alone.
7. An image is written as its `alt` text when it has one, and dropped otherwise. No address of
   an image is written.
8. Every other tag is removed and its text kept. Character references are decoded (`&amp;`,
   `&#39;`, `&nbsp;` as an ordinary space).
9. White space: inside a line, a run of spaces and tabs is one space, and a line has none at
   either end; inside `pre`, line breaks are kept (spaces are still collapsed); more than one
   blank line in a row is one; the result has no blank line at its start or end. Text with no
   tag at all comes back with its own line breaks kept and the same white-space rules applied.
The example pairs are HTML_EXAMPLES below, exactly as the shared specification gives them.

When Google says no
- 401, and 403 for a reason that is not a rate limit: SourceError saying access was refused and
  what to check (the word "scope" among it), not retried. 404: SourceError naming `calendar_id`
  (not found, or not shared with the account that reads it). A listing whose `accessRole` is
  "freeBusyReader": SourceError naming `calendar_id`, saying to share the event details; nothing
  is written. 429, and 403 with reason `rateLimitExceeded` or `userRateLimitExceeded`: retried
  at most 4 times, the n-th wait (through the injected `sleep`) 2^n seconds plus up to one,
  n from 0 or 1; then SourceError. 403 `quotaExceeded` (a usage limit): not retried, SourceError
  saying so. 410, 5xx, a timeout, a body not of the documented shape (an event without `id`, or
  without a usable `end` when it is not cancelled): SourceError. No message holds a token or a
  body. A failed pull returns nothing, so no cursor moves, and the next pull writes what it did
  not.

Together with the framework: a calendar source is off when added and reads nothing; switched on
with a project, `run_source` writes the events that ended since, a page an event; again, nothing
new; an event that ends later is written by the run after. The database, the audit log and the
command line's output hold no private key, token, client secret or refresh token.
"""

import asyncio
import hashlib
import json
import logging
import sqlite3
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, unquote
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from cairn.server import main
from cairn.sources import HELD_CREDENTIALS, KINDS, SourceError, holds_secret, run_source
from cairn.store import Store

SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
CAL = "https://www.googleapis.com/calendar/v3"
CRED_ENV = "GOOGLE_C5_CREDENTIALS"
SA, USER = "service_account", "authorized_user"
ADA = SUBJECT = "ada@example.test"
CLIENT_EMAIL = "cairn-reader@c5-project.iam.example.test"
SHARED = "ops#team@group.calendar.example.test"  # a calendar shared with the service account
KEY_ID = "c5" + "7e1d4b2a" * 4  # made up
SA_CLIENT_ID = "1" + "05" * 10
USER_CLIENT_ID = "c5-" + "desktop-client" + ".apps.example.test"
CLIENT_SECRET = "GOCSPX-" + "c5" + "MadeUp" + "Secret9b4c"  # never a real secret
REFRESH_TOKEN = "1//" + "0c5" + "MadeUpRefresh" + "Token2e8d"
CANARY = "c5-" + "body-" + "canary-6f1a"  # in every error body the stand-in sends
NOW = datetime(2026, 11, 13, 15, 30, tzinfo=UTC)
SINCE = NOW - timedelta(days=1)  # when the source was switched on
MINUTE, HOUR, DAY, WEEK = (
    timedelta(minutes=1),
    timedelta(hours=1),
    timedelta(days=1),
    timedelta(weeks=1),
)
MADRID = "Europe/Madrid"  # the default calendar's zone: +01:00 in November
NAMES = ("Ada Park", "Brook Lane", "Cy Moss", "Dee Ward")
UNSET = object()

# The shared HTML-to-text specification's example pairs, exactly.
HTML_EXAMPLES = [
    ("Plain words &amp; more", "Plain words & more"),
    ("<p>First&nbsp;point.</p><p>Second   point.</p>", "First point.\n\nSecond point."),
    ("Line one<br>Line two<br/>Line three", "Line one\nLine two\nLine three"),
    (
        "<ul><li>Bring the <b>figures</b></li><li>Agree the date</li></ul>",
        "- Bring the figures\n- Agree the date",
    ),
    (
        "<p>Agenda:</p><ol><li>Budget</li><li>Hiring</li></ol><p>Thanks</p>",
        "Agenda:\n\n- Budget\n- Hiring\n\nThanks",
    ),
    (
        'See <a href="https://docs.example.test/plan">the plan</a> first.',
        "See the plan (https://docs.example.test/plan) first.",
    ),
    (
        '<a href="https://docs.example.test/plan">https://docs.example.test/plan</a>',
        "https://docs.example.test/plan",
    ),
    ('Write to <a href="mailto:ada@example.test">Ada</a>.', "Write to Ada."),
    ("<h2>Notes</h2><p>Read them.</p>", "Notes\n\nRead them."),
    ("<style>p{color:red}</style><p>Kept</p><script>alert(1)</script><!-- gone -->", "Kept"),
    (
        "<table><tr><th>Who</th><th>What</th></tr><tr><td>Ada</td><td>Slides</td></tr></table>",
        "Who | What\nAda | Slides",
    ),
    (
        '<p>Logo <img src="https://img.example.test/a.png" alt="Acme"> here <img src="x.png"></p>',
        "Logo Acme here",
    ),
    ("<div>One</div><div>Two</div>", "One\n\nTwo"),
    ("<p>Unclosed <b>bold<p>Next", "Unclosed bold\n\nNext"),
    ("Two lines\nof plain text", "Two lines\nof plain text"),
]


def html_to_text(html: str) -> str:
    """The shared conversion, imported when a test needs it (so each test fails on its own
    while the module does not exist)."""
    from cairn.sources.markup import html_to_text as convert

    return convert(html)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class RealClock:
    """The wall clock, for the command line, which reads it."""

    def __call__(self) -> datetime:
        return datetime.now(UTC)


def parsed(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(value)
    return moment


def unb64(segment: str) -> bytes:
    return urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def eid(n: int) -> str:
    """An event id: base32hex as Google's are, with letters no hex digest holds."""
    return f"c5qv{n:05d}"


def ref(n: int) -> str:
    return f"calendar:{eid(n)}"


def refs(pulled) -> list[str]:
    return [draft.source_ref for draft in pulled.drafts]


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def fact_lines(text: str) -> list[str]:
    """The text's lines, each without a leading list marker."""
    return [
        line.strip().removeprefix("- ").removeprefix("* ").strip() for line in text.splitlines()
    ]


def meet_lines(text: str) -> list[str]:
    return [line for line in fact_lines(text) if line.startswith("Meet")]


def ended_at(event: "Event", zone: str | None) -> datetime:
    """When cairn counts an event as ended: its end; for an all-day event, 00:00 on its end date
    in the calendar's zone, or (zone None: the fallback) 12:00 UTC on it."""
    if isinstance(event.end, datetime):
        return event.end
    if zone is None:
        return datetime.combine(event.end, time(12), UTC)
    return datetime.combine(event.end, time(0), ZoneInfo(zone))


@dataclass
class Event:
    eid: str
    start: datetime | date  # a date: an all-day event
    end: datetime | date  # exclusive, as Google gives it
    summary: str | None = None
    status: str = "confirmed"
    event_type: str | None = None  # None: the field is absent
    visibility: str | None = None
    attendees: list[dict] | None = None
    organizer: dict | None = None
    creator: dict | None = None
    description: str | None = None
    location: str | None = None
    conference: dict | None = None
    hangout: str | None = None
    series: str | None = None  # recurringEventId, for an instance
    original: datetime | None = None  # originalStartTime, for an instance
    added: datetime | None = None  # when it was put on the calendar; None: always there
    offset: timedelta | None = (
        None  # the offset its times are written in; None: the calendar's zone
    )
    updated: datetime | None = None


@dataclass
class Calendar:
    cid: str
    zone: str  # its IANA time zone, which decides an all-day event's day at Google
    roles: dict[str, str]  # who may read it, and as what
    told: str | None  # the listing's `timeZone`: the zone, None (absent), or a name it gives
    events: dict[str, Event] = field(default_factory=dict)


def service_account(keys, **changes) -> dict:
    doc = {
        "type": "service_account",
        "project_id": "c5-project",
        "private_key_id": KEY_ID,
        "private_key": keys.pem,
        "client_email": CLIENT_EMAIL,
        "client_id": SA_CLIENT_ID,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": TOKEN_URL,
    }
    doc.update(changes)
    return {key: value for key, value in doc.items() if value is not None}


def authorized_user(**changes) -> dict:
    doc = {
        "type": "authorized_user",
        "client_id": USER_CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    }
    doc.update(changes)
    return {key: value for key, value in doc.items() if value is not None}


class FakeGoogle:
    """Google's token endpoint and the Calendar API v3's events.list, as
    docs/google-calendar-api.md describes them, as one httpx mock. The token endpoint issues a
    token to a service account (for its `sub`, or for itself) or to a refresh token (Ada's),
    records the scope and the subject asked for, and can refuse; the Calendar API accepts only
    the latest token, resolves "primary" to the token's person, answers 404 for a calendar the
    caller cannot read, and lists events as the notes say: `timeMin` exclusive on an event's end,
    `timeMax` exclusive on its start, an all-day event's bounds at midnight in the calendar's own
    IANA zone (zoneinfo), instances only with `singleEvents=true`, `orderBy=startTime`, pages of at
    most `maxResults` whose tokens die with the pull and with any change of the other
    parameters, the listing's top-level fields (`accessRole` among them), and a `syncToken`
    beside the time bounds refused. Switches for what the notes leave open: cancelled events
    listed bare or not at all, an empty page's `items` absent or [], short and empty pages,
    another order, times in the calendar's zone or in UTC, a listing whose `timeZone` is absent
    or a name no zone database knows; and it can answer 401, 403 by
    reason, 404, 410, 429, 5xx, a timeout or a malformed body. Every request is kept."""

    def __init__(self, clock, keys) -> None:
        self.clock = clock
        self.keys = keys
        self.base = CAL
        self.tick = timedelta(0)  # the clock moves this much with every request
        self.calendars: dict[str, Calendar] = {}
        self.users = {ADA}  # the people of the domain
        self.requests: list[SimpleNamespace] = []
        self.slept: list[float] = []
        self.issued: list[str] = []
        self.valid: tuple[str, datetime, str] | None = None  # token, expiry, whose
        self.gen = 0
        self.shows_cancelled = False
        self.omit_empty = False
        self.short_pages: int | None = None
        self.shuffle = False
        self.utc_times = False
        self.forced: list[tuple[str, object]] = []  # (endpoint, how): once each
        self.always: dict[str, object] = {}
        self.refusal: tuple[int, str, str] | None = None
        self.calendar(ADA, MADRID, {ADA: "owner"})

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def new_pull(self) -> None:
        self.gen += 1

    def env(self, cred: str = SA, **changes) -> dict[str, str]:
        doc = service_account(self.keys, **changes) if cred == SA else authorized_user(**changes)
        return {CRED_ENV: json.dumps(doc)}

    def calendar(self, cid: str, zone: str, roles: dict, told: object = UNSET) -> Calendar:
        """A calendar in `zone`; `told` is the `timeZone` its listing gives (the zone unless
        given; None: absent)."""
        said = zone if told is UNSET else told
        self.calendars[cid] = Calendar(cid, zone, dict(roles), said)  # type: ignore[arg-type]
        return self.calendars[cid]

    def event(self, n: int, start, end, *, cal: str = ADA, summary=UNSET, **fields) -> Event:
        me = {"email": ADA, "displayName": "Ada Park", "self": True}
        fields.setdefault("organizer", dict(me))
        fields.setdefault("creator", dict(me))
        made = Event(
            eid=fields.pop("eid", eid(n)),
            start=start,
            end=end,
            summary=f"Review {n}" if summary is UNSET else summary,
            **fields,
        )
        self.calendars[cal].events[made.eid] = made
        return made

    def calls(self, endpoint: str | None = None, *, every: bool = False) -> list[SimpleNamespace]:
        return [
            r
            for r in self.requests
            if (every or r.gen == self.gen) and (endpoint is None or r.endpoint == endpoint)
        ]

    # Time

    def _when(self, moment: datetime, zone: tzinfo) -> str:
        local = moment.astimezone(zone)
        if self.utc_times or not local.utcoffset():
            return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return local.isoformat()

    def _bounds(self, cal: Calendar, e: Event) -> tuple[datetime, datetime]:
        if isinstance(e.start, datetime):
            return e.start, e.end  # type: ignore[return-value]
        local = ZoneInfo(cal.zone)  # Google: the calendar's time zone decides an all-day day
        return datetime.combine(e.start, time(0), local), datetime.combine(e.end, time(0), local)

    # Routing

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.tick:
            self.clock.now += self.tick
        url = request.url
        where = f"{url.scheme}://{url.netloc.decode()}{url.path}"
        raw = url.raw_path.decode("ascii").split("?", 1)[0]
        endpoint, calendar_id = "other", None
        if url.path == "/token" and where == TOKEN_URL:
            endpoint = "token"
        elif where.startswith(self.base + "/calendars/") and raw.endswith("/events"):
            prefix = httpx.URL(self.base).raw_path.decode("ascii").rstrip("/") + "/calendars/"
            segment = raw[len(prefix) : -len("/events")]
            if "/" not in segment:
                endpoint, calendar_id = "events", unquote(segment)
        record = SimpleNamespace(
            at=self.clock(),
            gen=self.gen,
            method=request.method,
            url=str(url),
            endpoint=endpoint,
            calendar=calendar_id,
            params=dict(url.params),
            auth=request.headers.get("authorization"),
            form=None,
            claims=None,
            status=None,
            count=None,
        )
        self.requests.append(record)
        response = self._answer(request, record)
        record.status = response.status_code
        return response

    def _forced_for(self, endpoint: str) -> object:
        for index, (which, how) in enumerate(self.forced):
            if which == endpoint:
                del self.forced[index]
                return how
        return self.always.get(endpoint)

    def _answer(self, request: httpx.Request, record) -> httpx.Response:
        if record.endpoint == "token":
            return self._token(request, record)
        if record.endpoint == "other":
            return httpx.Response(404, text=CANARY)
        if request.method != "GET":
            return self._error(400, "global", "badRequest")
        if not (
            self.valid is not None
            and record.auth == f"Bearer {self.valid[0]}"
            and self.clock() < self.valid[1]
        ):
            return self._error(401, "global", "authError")
        how = self._forced_for("events")
        if how is not None:
            return self._failure(request, how)
        whose = self.valid[2]
        cid = whose if record.calendar == "primary" else record.calendar
        cal = self.calendars.get(cid)  # type: ignore[arg-type]
        role = cal.roles.get(whose) if cal is not None else None
        if cal is None or role is None:
            return self._error(404, "global", "notFound")
        return self._events(record, cal, role)

    # The token endpoint

    def _token(self, request: httpx.Request, record) -> httpx.Response:
        how = self._forced_for("token")
        if how is not None:
            return self._failure(request, how)
        record.form = dict(parse_qsl(request.content.decode("ascii"), keep_blank_values=True))
        whose = None
        if record.form.get("grant_type") == JWT_GRANT:
            try:
                record.claims = json.loads(unb64(record.form["assertion"].split(".")[1]))
            except (KeyError, IndexError, ValueError):
                return self._refused(400, "invalid_request", CANARY)
            whose = record.claims.get("sub") or record.claims.get("iss")
            if "sub" in record.claims and record.claims["sub"] not in self.users:
                return self._refused(400, "invalid_grant", "Invalid email or User ID.")
        elif record.form.get("grant_type") == "refresh_token":
            given = (
                record.form.get("client_id"),
                record.form.get("client_secret"),
                record.form.get("refresh_token"),
            )
            if given != (USER_CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN):
                return self._refused(400, "invalid_grant", CANARY)
            whose = ADA  # the refresh token is Ada's own authorisation
        else:
            return self._refused(400, "unsupported_grant_type", CANARY)
        if self.refusal is not None:
            return self._refused(*self.refusal)
        token = "ya29." + f"c5{len(self.issued) + 1:04d}" + "MadeUp" + "AccessToken"
        self.issued.append(token)
        self.valid = (token, self.clock() + HOUR, whose)
        body = {"access_token": token, "expires_in": 3599, "token_type": "Bearer"}
        return httpx.Response(200, json=body)

    def _refused(self, status: int, error: str, description: str) -> httpx.Response:
        return httpx.Response(status, json={"error": error, "error_description": description})

    # Answers

    def _error(self, code: int, domain: str, reason: str) -> httpx.Response:
        item = {"domain": domain, "reason": reason, "message": f"{CANARY}: {reason}"}
        body = {"error": {"errors": [item], "code": code, "message": f"{CANARY}: {reason}"}}
        return httpx.Response(code, json=body)

    def _shell(self, items: list) -> dict:
        return {
            "kind": "calendar#events",
            "summary": "Ada Park",
            "timeZone": "Europe/Madrid",
            "accessRole": "owner",
            "items": items,
        }

    def _failure(self, request: httpx.Request, how: object) -> httpx.Response:
        good = {
            "id": eid(900),
            "status": "confirmed",
            "summary": "Fine",
            "start": {"dateTime": "2026-11-13T08:00:00Z"},
            "end": {"dateTime": "2026-11-13T09:00:00Z"},
        }
        if how == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if how == "not json":
            return httpx.Response(200, text=f"<html>{CANARY}</html>")
        if how == "array":
            return httpx.Response(200, json=[CANARY])
        if how == "items not a list":
            return httpx.Response(200, json={**self._shell([]), "items": CANARY})
        if how == "event without id":
            bad = {k: v for k, v in good.items() if k != "id"} | {"summary": CANARY}
            return httpx.Response(200, json=self._shell([good, bad]))
        if how == "event without end":
            bad = {k: v for k, v in good.items() if k != "end"} | {"id": eid(901)}
            return httpx.Response(200, json=self._shell([good, bad]))
        if how == "event with an empty end":
            return httpx.Response(200, json=self._shell([good, {**good, "end": {}}]))
        if how == "event with a bad end":
            bad = {**good, "id": eid(901), "end": {"dateTime": CANARY}}
            return httpx.Response(200, json=self._shell([good, bad]))
        if how == "all-day event with a bad date":
            bad = {**good, "id": eid(901), "start": {"date": "2026-11-12"}}
            bad["end"] = {"date": "2026-13-45"}
            return httpx.Response(200, json=self._shell([good, bad]))
        if how == "429, no body":
            return httpx.Response(429)
        if isinstance(how, str) and how.startswith("403 "):
            reason = how.removeprefix("403 ")
            usage = reason in ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded")
            return self._error(403, "usageLimits" if usage else "global", reason)
        reasons = {
            401: ("global", "authError"),
            404: ("global", "notFound"),
            410: ("calendar", "fullSyncRequired"),
            429: ("usageLimits", "rateLimitExceeded"),
        }
        status = int(how)  # type: ignore[call-overload]
        return self._error(status, *reasons.get(status, ("global", "backendError")))

    def _page_token(self, offset: int, query: dict) -> str:
        state = json.dumps({"g": self.gen, "o": offset, "q": sorted(query.items())})
        return urlsafe_b64encode(state.encode()).decode()

    def _offset(self, token: str | None, query: dict) -> int | None:
        if token is None:
            return 0
        try:
            state = json.loads(urlsafe_b64decode(token.encode()))
        except ValueError:
            return None
        if state.get("g") != self.gen or state.get("q") != [list(i) for i in sorted(query.items())]:
            return None  # from another pull, or the other parameters were not repeated
        return state["o"]

    def _events(self, record, cal: Calendar, role: str) -> httpx.Response:
        params = record.params
        query = {k: v for k, v in params.items() if k != "pageToken"}
        if "syncToken" in params and ({"timeMin", "timeMax", "orderBy", "q"} & set(params)):
            return self._error(400, "global", "invalid")
        single = params.get("singleEvents") == "true"
        if params.get("orderBy") == "startTime" and not single:
            return self._error(400, "global", "invalid")
        try:
            low = parsed(params["timeMin"]) if "timeMin" in params else None
            high = parsed(params["timeMax"]) if "timeMax" in params else None
            size = int(params.get("maxResults", "250"))
        except ValueError:
            return self._error(400, "global", "invalid")
        if low is not None and high is not None and low >= high:
            return self._error(400, "calendar", "timeRangeEmpty")
        if size < 1:
            return self._error(400, "global", "invalid")
        offset = self._offset(params.get("pageToken"), query)
        if offset is None:
            return self._error(400, "global", "invalid")
        now = self.clock()
        deleted = self.shows_cancelled or params.get("showDeleted") == "true"
        shown: list[tuple[datetime, str, dict]] = []
        masters: set[str] = set()
        for e in cal.events.values():
            if e.added is not None and e.added > now:
                continue
            if e.status == "cancelled" and not deleted:
                continue
            start, end = self._bounds(cal, e)
            if (low is not None and end <= low) or (high is not None and start >= high):
                continue
            if e.series and not single:  # the recurring event itself, not its instances
                if e.series not in masters:
                    masters.add(e.series)
                    master = self._item(cal, e, role) | {"id": e.series}
                    master["recurrence"] = ["RRULE:FREQ=WEEKLY"]
                    for key in ("recurringEventId", "originalStartTime"):
                        master.pop(key, None)
                    shown.append((start, e.series, master))
                continue
            shown.append((start, e.eid, self._item(cal, e, role)))
        if params.get("orderBy") == "startTime":
            shown.sort(key=lambda s: (s[0], s[1]))
        else:  # "an unspecified, stable order"
            shown.sort(key=lambda s: hashlib.sha256(s[1].encode()).hexdigest())
        if self.shuffle:
            shown.sort(key=lambda s: hashlib.sha256(f"{self.gen}:{s[1]}".encode()).hexdigest())
        items = [item for _, _, item in shown]
        size = min(size, 2500)
        if self.short_pages:
            size = min(size, self.short_pages) if "pageToken" in params else 0
        page = items[offset : offset + size]
        record.count = len(page)
        body: dict = {
            "kind": "calendar#events",
            "etag": f'"{self.gen}"',
            "summary": cal.cid,
            "updated": self._when(now, UTC),
            "accessRole": role,
            "defaultReminders": [{"method": "popup", "minutes": 10}],
        }
        if cal.told is not None:
            body["timeZone"] = cal.told
        if page or not self.omit_empty:
            body["items"] = page
        if offset + size < len(items):
            body["nextPageToken"] = self._page_token(offset + size, query)
        else:
            body["nextSyncToken"] = "CPDAlvWDx70CEPDAlvWDx70CGAU="
        return httpx.Response(200, json=body)

    def _item(self, cal: Calendar, e: Event, role: str) -> dict:
        zone = ZoneInfo(cal.zone) if e.offset is None else timezone(e.offset)

        def moment(value) -> dict:
            if isinstance(value, datetime):
                given = {"dateTime": self._when(value, zone)}
                return given | ({"timeZone": cal.zone} if e.series else {})
            return {"date": value.isoformat()}

        if e.status == "cancelled":  # only the id (and an instance's own fields) are certain
            bare = {"kind": "calendar#event", "etag": '"0"', "id": e.eid, "status": "cancelled"}
            if e.series:
                bare["recurringEventId"] = e.series
                bare["originalStartTime"] = moment(e.original or e.start)
            return bare
        item: dict = {
            "kind": "calendar#event",
            "etag": f'"{e.eid}"',
            "id": e.eid,
            "status": e.status,
            "htmlLink": f"https://calendar.example.test/event?eid={e.eid}",
            "created": self._when(e.updated or self.clock() - 3 * WEEK, UTC),
            "updated": self._when(e.updated or self.clock() - 3 * WEEK, UTC),
            "start": moment(e.start),
            "end": moment(e.end),
            "iCalUID": f"{e.series or e.eid}@google.example.test",
            "sequence": 0,
            "reminders": {"useDefault": True},
        }
        if role == "freeBusyReader":  # what such a reader sees is not stated: no details here
            return {k: item[k] for k in ("kind", "etag", "id", "status", "start", "end")}
        for key, value in (
            ("summary", e.summary),
            ("description", e.description),
            ("location", e.location),
            ("creator", e.creator),
            ("organizer", e.organizer),
            ("eventType", e.event_type),
            ("visibility", e.visibility),
            ("attendees", e.attendees),
            ("conferenceData", e.conference),
            ("hangoutLink", e.hangout),
            ("recurringEventId", e.series),
        ):
            if value is not None:
                item[key] = value
        if e.series:
            item["originalStartTime"] = moment(e.original or e.start)
        return item


@pytest.fixture(scope="session")
def keys():
    """A key pair made for this run: no key is written in this file."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    body = [line for line in pem.splitlines() if len(line) >= 16 and not line.startswith("-----")]
    return SimpleNamespace(pem=pem, body=body)


@pytest.fixture
def kind():
    return KINDS["google_calendar"]


@pytest.fixture
def fake(keys):
    return FakeGoogle(Clock(NOW), keys)


def leaked(text: str | None, fake: FakeGoogle) -> list[str]:
    """The secrets `text` holds, as written or percent-decoded: a line of the private key, the
    client secret, the refresh token, or an access token the stand-in issued."""
    if not text:
        return []
    secrets = [*fake.keys.body, CLIENT_SECRET, REFRESH_TOKEN, *fake.issued]
    return [s for s in secrets if s in text or s in unquote(text)]


def config(cred: str = SA, **settings) -> dict:
    given: dict = {"credentials_env": CRED_ENV, "projects": ["calendar"]}
    if cred == SA:
        given["subject"] = SUBJECT
    given.update(settings)
    return {key: value for key, value in given.items() if value is not None}


def opened(kind, fake, *, cred=SA, env=None, since=SINCE, deadline=None, **settings):
    checked = kind.check(config(cred, **settings))
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return kind.open(
        "calendar", checked, http=http, env=fake.env(cred) if env is None else env,
        clock=fake.clock, sleep=fake.sleep, deadline=deadline, since=since,
    )  # fmt: skip


def pull(fake, source, cursor=None):
    fake.new_pull()
    return asyncio.run(source.pull(cursor))


def reader(kind, fake, **settings):
    """Each pull through a source opened afresh, as the runner opens one for every run: what
    a pull knows of the last one is in the cursor it is given."""

    def read(cursor=None):
        return pull(fake, opened(kind, fake, **settings), cursor)

    return read


def failed_pull(fake, source, cursor=None) -> SourceError:
    fake.new_pull()
    with pytest.raises(SourceError) as failed:
        asyncio.run(source.pull(cursor))
    return failed.value


def backed_off(slept: list[float]) -> None:
    """The notes' backoff: the n-th wait is 2^n seconds and up to one more, n from 0 or 1."""
    assert any(
        all(2 ** (first + n) <= s <= 2 ** (first + n) + 1 for n, s in enumerate(slept))
        for first in (0, 1)
    ), slept


# The kind and its config


def test_the_kind_starts_off_and_its_config_has_these_defaults(kind):
    assert kind.starts_off is True and kind.via == "google-calendar"
    assert kind.default_project == "calendar"
    checked = kind.check({"credentials_env": CRED_ENV, "projects": []})
    assert (checked["credentials_env"], checked["projects"]) == (CRED_ENV, [])
    assert (checked["calendar_id"], checked["base_url"]) == ("primary", CAL)
    assert checked.get("subject") is None
    assert kind.check(checked) == checked  # what is stored is checked again on switch-on

    given = {
        "credentials_env": CRED_ENV,
        "subject": SUBJECT,
        "calendar_id": SHARED,
        "projects": ["calendar"],
        "base_url": "https://calendar.example.test/v3",
    }
    kept = kind.check(given)
    for setting, value in given.items():
        assert kept[setting] == value, setting
    assert kind.check(kept) == kept
    for accepted in (
        {"calendar_id": "c" * 254},
        {"calendar_id": "primary"},
        {"calendar_id": "en.uk#holiday@group.v.calendar.example.test"},
        {"base_url": "http://127.0.0.1:8782/calendar/v3"},
        {"base_url": "http://localhost:8782/calendar/v3"},
    ):
        assert kind.check(config(**accepted))["projects"] == ["calendar"], accepted


def test_describe_says_which_calendar_it_reads_and_shows_no_credential(kind, fake):
    plain = kind.describe(kind.check(config(USER)))
    assert isinstance(plain, str) and plain.strip() and "\n" not in plain.strip()
    assert "primary" in plain
    shared = kind.describe(kind.check(config(SA, calendar_id=SHARED)))
    assert SHARED in shared and SUBJECT in shared
    alone = kind.describe(kind.check(config(SA, subject=None, calendar_id=SHARED)))
    assert SHARED in alone and SUBJECT not in alone
    for said in (plain, shared, alone):
        assert not leaked(said, fake) and fake.env(SA)[CRED_ENV] not in said
        assert CLIENT_EMAIL not in said and KEY_ID not in said


@pytest.mark.parametrize(
    ("setting", "settings"),
    [
        ("credentials_env", {"credentials_env": None}),
        ("credentials_env", {"credentials_env": 7}),
        ("credentials_env", {"credentials_env": ""}),
        ("subject", {"subject": "ada"}),
        ("subject", {"subject": "ada@example.test, brook@example.test"}),
        ("subject", {"subject": ["ada@example.test"]}),
        ("calendar_id", {"calendar_id": ""}),
        ("calendar_id", {"calendar_id": "c" * 255}),
        ("calendar_id", {"calendar_id": "team calendar"}),
        ("calendar_id", {"calendar_id": "team\tcalendar"}),
        ("calendar_id", {"calendar_id": "team\x01calendar"}),
        ("calendar_id", {"calendar_id": 7}),
        ("calendar_id", {"calendar_id": ["primary"]}),
        ("projects", {"projects": ["calendar", "planning"]}),
        ("projects", {"projects": "calendar"}),
        ("base_url", {"base_url": "http://calendar.example.test/v3"}),
        ("base_url", {"base_url": "ftp://calendar.example.test/v3"}),
        ("base_url", {"base_url": 7}),
        ("color", {"color": "blue"}),
        ("scope", {"scope": SCOPE}),
    ],
)
def test_check_refuses_a_bad_setting_by_its_name(kind, setting, settings):
    given = config(**settings)  # a setting given as None is left out
    with pytest.raises(ValueError) as refused:
        kind.check(given)
    assert setting in str(refused.value)


def test_check_refuses_a_config_that_is_not_an_object(kind):
    for odd in (None, [], "credentials_env"):
        with pytest.raises(ValueError):
            kind.check(odd)


def test_a_credential_pasted_into_the_config_is_refused_and_never_repeated(kind, fake, tmp_path):
    document = json.dumps(service_account(fake.keys))
    token = "ya29." + "c5" + "PastedAccessToken" + "4d5e6f7a"
    for setting, value in (
        ("credentials_env", document),
        ("credentials_env", fake.keys.pem),
        ("credentials_env", token),
        ("credentials_env", REFRESH_TOKEN),
        ("credentials_env", CLIENT_SECRET),
        ("subject", token),
        ("calendar_id", document),
        ("calendar_id", fake.keys.pem),
        ("credentials", document),
    ):
        with pytest.raises(ValueError) as refused:
            kind.check(config(**{setting: value}))
        said = str(refused.value)
        assert setting in said, setting
        assert not leaked(said, fake) and token not in said
        assert CLIENT_EMAIL not in said and KEY_ID not in said

    store = Store(tmp_path / "cairn.db")
    try:
        store.create_project("calendar")
        with pytest.raises(ValueError):
            store.add_source("calendar", "google_calendar", config(credentials_env=document))
        assert store.list_sources() == []
    finally:
        store.close()
    assert not leaked(dump(tmp_path / "cairn.db"), fake)


# Opening, and signing in


def test_a_service_account_reads_the_subjects_calendar_with_one_token_for_the_scope(kind, fake):
    for n in (1, 2, 3):
        fake.event(n, SINCE + n * HOUR, SINCE + n * HOUR + 30 * MINUTE)
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(1), ref(2), ref(3)]

    (asked,) = fake.calls("token")
    assert asked.form["grant_type"] == JWT_GRANT
    assert asked.claims["scope"] == SCOPE and asked.claims["sub"] == SUBJECT
    listed = fake.calls("events")
    assert listed and {r.auth for r in listed} == {f"Bearer {fake.issued[0]}"}
    assert {r.calendar for r in listed} == {"primary"} and all(r.status == 200 for r in listed)
    assert not any(r.endpoint == "other" for r in fake.requests)

    fake.clock.now += HOUR
    read(got.cursor)
    assert len(fake.calls("token")) == 1 and len(fake.issued) == 2  # the next pull: its own


def test_a_refresh_token_reads_the_persons_own_calendar(kind, fake):
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.event(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR)
    got = pull(fake, opened(kind, fake, cred=USER))
    assert refs(got) == [ref(1), ref(2)]
    (asked,) = fake.calls("token")
    assert asked.form == {
        "client_id": USER_CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    }
    listed = fake.calls("events")
    assert listed and {r.auth for r in listed} == {f"Bearer {fake.issued[0]}"}


def test_a_service_account_acting_as_itself_reads_a_calendar_shared_with_its_address(kind, fake):
    fake.calendar(SHARED, "UTC", {CLIENT_EMAIL: "reader"})
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR, cal=SHARED)
    fake.event(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR)  # on Ada's calendar: not this source's
    got = pull(fake, opened(kind, fake, subject=None, calendar_id=SHARED))
    assert refs(got) == [ref(1)]
    (asked,) = fake.calls("token")
    assert asked.claims["scope"] == SCOPE and "sub" not in asked.claims
    listed = fake.calls("events")
    assert {r.calendar for r in listed} == {SHARED}  # the id arrives whole, "#" and "@" too

    fake.calendars[SHARED].roles = {}  # no longer shared with it
    said = str(failed_pull(fake, opened(kind, fake, subject=None, calendar_id=SHARED)))
    assert "calendar_id" in said and CANARY not in said and not leaked(said, fake)


def test_the_calendar_api_is_read_at_the_base_url_given_on_this_machine(kind, fake):
    fake.base = "http://127.0.0.1:8782/calendar/v3"
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR)
    got = pull(fake, opened(kind, fake, base_url=fake.base))
    assert refs(got) == [ref(1)]
    listed = fake.calls("events")
    assert listed and all(r.url.startswith(fake.base + "/calendars/") for r in listed)
    assert not any(r.endpoint == "other" for r in fake.requests)


def test_opening_refuses_a_subject_or_calendar_that_cannot_work_by_its_setting(kind, fake):
    cases = [
        (USER, {"subject": SUBJECT}, "subject"),
        (SA, {"subject": None}, "calendar_id"),  # acting as itself, "primary" by default
        (SA, {"subject": None, "calendar_id": "primary"}, "calendar_id"),
    ]
    for cred, settings, setting in cases:
        checked = kind.check(config(cred, **settings))
        http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
        with pytest.raises(SourceError) as refused:
            kind.open(
                "calendar", checked, http=http, env=fake.env(cred), clock=fake.clock,
                sleep=fake.sleep, deadline=None, since=SINCE,
            )  # fmt: skip
        said = str(refused.value)
        assert setting in said, (cred, settings)
        if setting == "calendar_id":
            assert "shar" in said.lower(), said
        assert not leaked(said, fake) and CLIENT_EMAIL not in said
    assert fake.requests == []


@pytest.mark.parametrize("cred", [SA, USER])
def test_the_secrets_are_the_sources_and_never_in_a_url_a_log_or_the_cursor(
    kind, fake, cred, caplog
):
    caplog.set_level(logging.DEBUG)
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.event(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR)
    source = opened(kind, fake, cred=cred)
    got = pull(fake, source)
    assert refs(got) == [ref(1), ref(2)]
    (token,) = fake.issued
    kept = tuple(source.secrets)
    assert holds_secret(f"the token {token} again", kept)
    if cred == SA:
        assert holds_secret(fake.keys.pem, kept)
    else:
        assert holds_secret(f"said {REFRESH_TOKEN} once", kept)
    for request in fake.requests:
        assert not leaked(request.url, fake)
        assert not {"access_token", "key", "token", "oauth_token"} & set(request.params)
    assert not leaked(caplog.text, fake)
    assert not leaked(got.cursor, fake)
    for draft in got.drafts:
        assert not leaked(json.dumps(vars(draft)), fake)


@pytest.mark.parametrize("cred", [SA, USER])
def test_a_refusal_by_the_token_endpoint_fails_the_pull_without_the_body(kind, fake, cred):
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.refusal = (400, "invalid_grant", f"{CANARY} refused")
    said = str(failed_pull(fake, opened(kind, fake, cred=cred)))
    assert said.strip() and CANARY not in said and not leaked(said, fake)
    assert fake.calls("events") == []


# What it reads


@pytest.mark.parametrize("times", ["in the calendar's zone", "in UTC"])
def test_the_first_pull_writes_what_ended_since_switch_on_and_later_pulls_the_rest(
    kind, fake, times
):
    fake.utc_times = times == "in UTC"
    fake.event(1, SINCE - 3 * HOUR, SINCE - HOUR)  # ended before it was switched on
    fake.event(2, SINCE - 5 * HOUR, SINCE + HOUR)  # began before, ended after
    fake.event(3, SINCE - HOUR, SINCE)  # ended right as it was switched on
    fake.event(4, SINCE + 2 * HOUR, SINCE + 3 * HOUR)
    fake.event(5, NOW - 20 * MINUTE, NOW + 20 * MINUTE)  # going on
    fake.event(6, NOW + HOUR, NOW + 2 * HOUR)  # not begun
    read = reader(kind, fake)

    first = read()
    assert refs(first) == [ref(3), ref(2), ref(4)]
    assert first.skipped == []
    assert {draft.project for draft in first.drafts} == {"calendar"}
    for request in fake.calls("events"):
        assert abs(parsed(request.params["timeMin"]) - (SINCE - 2 * DAY)) < timedelta(seconds=1)
        assert abs(parsed(request.params["timeMax"]) - NOW) < timedelta(seconds=1)

    fake.clock.now = NOW + 30 * MINUTE
    second = read(first.cursor)
    assert refs(second) == [ref(5)] and second.skipped == []
    fake.clock.now = NOW + 90 * MINUTE  # the sixth is going on
    assert read(second.cursor).drafts == []
    fake.clock.now = NOW + 3 * HOUR
    third = read(second.cursor)
    assert refs(third) == [ref(6)]
    fake.clock.now = NOW + 4 * HOUR
    assert read(third.cursor).drafts == []


@pytest.mark.parametrize("cancelled", ["listed bare", "not listed"])
def test_what_is_not_a_meeting_of_the_person_is_passed_over_silently(kind, fake, cancelled):
    fake.shows_cancelled = cancelled == "listed bare"
    others = [
        {
            "email": "brook@example.test",
            "displayName": "Brook Lane",
            "organizer": True,
            "responseStatus": "accepted",
        },
    ]

    def me(answer: str) -> list[dict]:
        return [
            {"email": ADA, "displayName": "Ada Park", "self": True, "responseStatus": answer},
            *others,
        ]

    passed = [
        {"status": "cancelled"},
        {"event_type": "focusTime"},
        {"event_type": "outOfOffice"},
        {"event_type": "workingLocation"},
        {"event_type": "fromGmail"},
        {"event_type": "someTypeGoogleAddsLater"},
        {"visibility": "private"},
        {"visibility": "confidential"},
        {"attendees": me("declined")},
    ]
    kept = [
        {},
        {"event_type": "default"},
        {"visibility": "public"},
        {"visibility": "default"},
        {"attendees": me("needsAction")},
        {"attendees": me("accepted")},
        {"attendees": me("tentative")},
        {"attendees": []},
        {
            "attendees": [
                *me("accepted"),
                {
                    "email": "dee@example.test",
                    "displayName": "Dee Ward",
                    "responseStatus": "declined",
                },
            ]
        },
    ]
    for n, fields in enumerate(passed + kept, start=1):
        start = SINCE + n * HOUR
        fake.event(n, start, start + 30 * MINUTE, **fields)
    day = date(2026, 11, 12)  # an all-day birthday, over before NOW
    fake.event(40, day, day + DAY, event_type="birthday", summary="Brook's birthday")

    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(n) for n in range(len(passed) + 1, len(passed) + len(kept) + 1)]
    assert got.skipped == []


def all_day_cases(fake) -> None:
    fake.event(1, date(2026, 11, 11), date(2026, 11, 12), summary="Offsite")  # over everywhere
    fake.event(2, date(2026, 11, 12), date(2026, 11, 13), summary="Planning day")
    fake.event(3, date(2026, 11, 14), date(2026, 11, 15), summary="Hack day")
    fake.event(4, date(2026, 11, 14), date(2026, 11, 17), summary="Conference")
    late = timezone(timedelta(hours=2))
    fake.event(
        5,
        datetime(2026, 11, 15, 1, 0, tzinfo=late),
        datetime(2026, 11, 15, 2, 0, tzinfo=late),
        summary="Late review",
        offset=timedelta(hours=2),
    )


def pulls_every_three_hours(read, fake, first: datetime) -> tuple[list, dict]:
    cursor, written, drafts = None, [], {}
    at = first
    while at <= datetime(2026, 11, 20, 23, 0, tzinfo=UTC):
        fake.clock.now = at
        got = read(cursor)
        cursor = got.cursor
        written += [(at, found) for found in refs(got)]
        drafts.update({draft.source_ref: draft for draft in got.drafts})
        assert got.skipped == []
        at += 3 * HOUR
    return written, drafts


def expected_writes(fake, zone: str | None, since: datetime, first: datetime) -> list:
    """Each event that ended at or after `since`, by the first pull at or after its end."""
    expected = []
    for e in fake.calendars[ADA].events.values():
        ended = ended_at(e, zone)
        if ended < since:
            continue
        at = first
        while at < ended:
            at += 3 * HOUR
        expected.append((at, ended, f"calendar:{e.eid}"))
    return [(at, found) for at, _, found in sorted(expected)]


@pytest.mark.parametrize(
    "zone", ["Pacific/Auckland", "Pacific/Pago_Pago", "Europe/Madrid", "UTC"]
)  # +13:00, -11:00, +01:00 and +00:00 in November
def test_an_all_day_event_ends_at_midnight_of_its_end_date_in_the_calendars_zone(kind, fake, zone):
    fake.calendar(ADA, zone, {ADA: "owner"})
    since = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)  # switched on at 09:00 UTC
    all_day_cases(fake)
    first = since  # then every three hours: 09:00, 12:00, ..., 00:00 UTC among them
    written, drafts = pulls_every_three_hours(reader(kind, fake, since=since), fake, first)

    assert written == expected_writes(fake, zone, since, first)
    found = [f for _, f in written]
    assert len(found) == len(set(found))  # each exactly once
    assert ref(1) not in found
    # "Planning day" ended at local midnight starting 2026-11-13: before 09:00 UTC in Auckland,
    # Madrid and UTC, so it was over before the switch-on; at 11:00 UTC in Pago Pago, after it.
    assert (ref(2) in found) == (zone == "Pacific/Pago_Pago")
    for event in (3, 4, 5):
        assert ref(event) in found
    one, three, timed = drafts[ref(3)], drafts[ref(4)], drafts[ref(5)]
    assert one.topic == "Hack day, 2026-11-14" and "2026-11-14" in one.text
    assert "2026-11-15" not in one.text  # end.date is exclusive
    assert three.topic == "Conference, 2026-11-14"
    assert "2026-11-14" in three.text and "2026-11-16" in three.text
    assert "2026-11-17" not in three.text
    assert timed.topic == "Late review, 2026-11-14"  # 01:00 at +02:00 is the day before in UTC
    assert "2026-11-14 23:00" in timed.text and "2026-11-15 00:00" in timed.text


@pytest.mark.parametrize("told", [None, "Mars/Olympus_Mons"], ids=["no timeZone", "unknown"])
@pytest.mark.parametrize("zone", ["Pacific/Auckland", "Pacific/Pago_Pago"])
def test_without_a_known_zone_an_all_day_event_ends_at_noon_utc_and_is_not_lost(
    kind, fake, told, zone
):
    """The fallback: the listing names no zone cairn knows. Google still bounds the event in the
    calendar's real zone (Auckland's midnight is 25 hours before noon UTC): the overlap keeps it
    in the listing until cairn counts it as ended."""
    fake.calendar(ADA, zone, {ADA: "owner"}, told=told)
    since = datetime(2026, 11, 13, 0, 0, tzinfo=UTC)
    all_day_cases(fake)
    first = datetime(2026, 11, 13, 1, 30, tzinfo=UTC)  # every three hours, off 12:00
    written, _ = pulls_every_three_hours(reader(kind, fake, since=since), fake, first)
    assert written == expected_writes(fake, None, since, first)
    assert written[0] == (datetime(2026, 11, 13, 13, 30, tzinfo=UTC), ref(2))
    assert (datetime(2026, 11, 15, 13, 30, tzinfo=UTC), ref(3)) in written
    assert (datetime(2026, 11, 17, 13, 30, tzinfo=UTC), ref(4)) in written


@pytest.mark.parametrize("cancelled", ["listed bare", "not listed"])
def test_a_recurring_event_is_read_as_its_instances(kind, fake, cancelled):
    fake.shows_cancelled = cancelled == "listed bare"
    base = NOW - 4 * WEEK + HOUR
    for k, n in enumerate((11, 12, 13, 14)):
        start = base + k * WEEK
        fake.event(
            n, start, start + 45 * MINUTE, summary="Weekly sync", series="c5series0wk",
            original=start, status="cancelled" if k == 1 else "confirmed",
            eid=f"c5rk{7 * n:05d}vq",  # an instance's id: opaque, unrelated to the series id
        )  # fmt: skip
    got = pull(fake, opened(kind, fake, since=base - HOUR))
    assert refs(got) == [f"calendar:c5rk{7 * n:05d}vq" for n in (11, 13, 14)]
    assert {draft.title for draft in got.drafts} == {"Weekly sync"}
    assert [draft.topic for draft in got.drafts] == [
        f"Weekly sync, {base + k * WEEK:%Y-%m-%d}" for k in (0, 2, 3)
    ]
    assert got.skipped == []


# How it finds them


def test_every_page_asks_for_the_documented_listing_and_the_same_beside_its_token(kind, fake):
    fake.short_pages = 7  # an empty first page with a token, then pages of seven
    for n in range(1, 21):
        start = SINCE + n * HOUR
        fake.event(n, start, start + 30 * MINUTE)
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(n) for n in range(1, 21)]

    lists = fake.calls("events")
    assert len(lists) >= 3 and all(r.status == 200 for r in lists)
    first = lists[0].params
    assert set(first) == {"singleEvents", "orderBy", "timeMin", "timeMax", "maxResults"}
    assert (first["singleEvents"], first["orderBy"], first["maxResults"]) == (
        "true",
        "startTime",
        "2500",
    )
    for bound in ("timeMin", "timeMax"):
        assert first[bound].endswith(("Z", "+00:00")), first[bound]  # RFC 3339, in UTC
    assert abs(parsed(first["timeMin"]) - (SINCE - 2 * DAY)) < timedelta(seconds=1)
    assert abs(parsed(first["timeMax"]) - NOW) < timedelta(seconds=1)
    for later in lists[1:]:
        assert "pageToken" in later.params
        assert {k: v for k, v in later.params.items() if k != "pageToken"} == first

    fake.clock.now = NOW + 2 * HOUR
    read(got.cursor)
    again = fake.calls("events")
    assert again and set(again[0].params) == set(first)
    assert abs(parsed(again[0].params["timeMax"]) - fake.clock.now) < timedelta(seconds=1)


@pytest.mark.parametrize("mode", ["documented", "shuffled", "short pages"])
def test_any_order_and_any_paging_give_the_same_drafts_by_end_then_id(kind, fake, mode):
    fake.shuffle = mode == "shuffled"
    fake.short_pages = 3 if mode == "short pages" else None
    fake.omit_empty = mode == "short pages"
    base = SINCE + HOUR
    fake.event(4, base, base + 2 * HOUR)
    fake.event(2, base + 30 * MINUTE, base + HOUR)
    fake.event(3, base + 10 * MINUTE, base + 3 * HOUR)
    fake.event(1, base + 20 * MINUTE, base + 3 * HOUR)  # ends with 3: by id
    fake.event(5, date(2026, 11, 12), date(2026, 11, 13))  # ends 2026-11-12 23:00 UTC (Madrid)
    fake.event(6, NOW - DAY, datetime(2026, 11, 12, 23, 0, tzinfo=UTC))  # ends with 5
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(2), ref(4), ref(1), ref(3), ref(5), ref(6)]


# What it writes


def test_the_draft_says_what_the_event_was_and_holds_no_address(kind, fake):
    start = datetime(2026, 11, 12, 9, 0, tzinfo=timezone(timedelta(hours=2)))
    description = (
        "<p>Plain words &amp; more</p>"
        + HTML_EXAMPLES[4][0]
        + HTML_EXAMPLES[5][0]
        + HTML_EXAMPLES[7][0]
    )
    fake.event(
        1,
        start,
        start + 90 * MINUTE,
        summary="Quarterly planning",
        location="Harbour Street, room 4",
        organizer={"email": "cy@example.test", "displayName": "Cy Moss"},
        creator={"email": "creator@example.test"},
        attendees=[
            {"email": ADA, "displayName": "Ada Park", "self": True, "responseStatus": "accepted"},
            {
                "email": "brook@example.test",
                "displayName": "Brook Lane",
                "organizer": True,
                "responseStatus": "accepted",
            },
            {
                "email": "dee@example.test",
                "displayName": "Dee Ward",
                "responseStatus": "needsAction",
            },
            {
                "email": "board-4@resource.example.test",
                "displayName": "Board Room Four",
                "resource": True,
                "responseStatus": "accepted",
            },
            {"email": "guest1@example.test", "responseStatus": "needsAction"},
            {"email": "guest2@example.test", "responseStatus": "accepted"},
            {"email": "guest3@example.test", "responseStatus": "tentative"},
        ],
        conference={
            "conferenceId": "abc-defg-hij",
            "conferenceSolution": {"key": {"type": "hangoutsMeet"}, "name": "Google Meet"},
            "entryPoints": [
                {
                    "entryPointType": "video",
                    "uri": "https://meet.google.com/abc-defg-hij",
                    "label": "meet.google.com/abc-defg-hij",
                }
            ],
        },
        hangout="https://meet.google.com/abc-defg-hij",
        description=description,
        offset=timedelta(hours=2),
    )
    other = start + 3 * HOUR
    fake.event(
        2, other, other + HOUR, summary="Vendor call", organizer={"email": "brook@example.test"},
        conference={
            "conferenceId": "zm-4471-9020",
            "conferenceSolution": {"key": {"type": "addOn"}, "name": "Another provider"},
        },
    )  # fmt: skip
    pending = other + 2 * HOUR
    fake.event(
        3, pending, pending + HOUR, summary="Budget review",
        conference={"createRequest": {"requestId": "c5req1", "status": {"statusCode": "pending"},
                                      "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
    )  # fmt: skip

    got = pull(fake, opened(kind, fake, since=start - HOUR))
    assert refs(got) == [ref(1), ref(2), ref(3)]
    first, vendor, budget = got.drafts
    assert first.title == "Quarterly planning"
    assert first.topic == "Quarterly planning, 2026-11-12"
    assert (first.label, first.project, first.source_ref) == ("google-calendar", "calendar", ref(1))
    text = first.text
    heading = text.splitlines()[0]
    assert heading.startswith("#") and heading.lstrip("#").strip() == "Quarterly planning"
    assert "2026-11-12 07:00" in text and "2026-11-12 08:30" in text  # in UTC
    assert "Harbour Street, room 4" in text
    for name in ("Ada Park", "Brook Lane", "Dee Ward"):
        assert name in text, name
    assert "Cy Moss" in text  # the organizer, who is not among the attendees
    assert "Board Room Four" not in text  # a room is not a person
    assert "3 more" in text
    (meet,) = meet_lines(text)
    assert "abc-defg-hij" in meet
    assert html_to_text(description) in text
    for words in ("Plain words & more", "- Budget\n- Hiring", "Write to Ada."):
        assert words in text, words
    assert "the plan (https://docs.example.test/plan)" in text
    for draft in got.drafts:
        for value in (draft.title, draft.topic, draft.text):
            assert "@" not in value, value
        for part in ("brook@", "cy@", "creator@", "dee@", "guest1", "board-4"):
            assert part not in draft.text
    assert meet_lines(vendor.text) == [] and "zm-4471-9020" not in vendor.text
    assert meet_lines(budget.text) == []


def test_an_untitled_event_and_a_plain_description(kind, fake):
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR, summary=None)
    fake.event(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR, summary="")
    fake.event(
        3, SINCE + 5 * HOUR, SINCE + 6 * HOUR, summary="Vendor call",
        description="Bring the figures for the vendor.\nQuestions to dee@example.test first.",
    )  # fmt: skip
    got = pull(fake, opened(kind, fake))
    untitled, empty, plain = got.drafts
    for draft in (untitled, empty):
        assert draft.title == "Untitled event"
    assert untitled.topic == f"Untitled event, {SINCE + HOUR:%Y-%m-%d}"
    assert "Bring the figures for the vendor." in plain.text
    assert "Questions to dee@example.test first." in plain.text  # the description's own words


@pytest.mark.parametrize(("html", "text"), HTML_EXAMPLES)
def test_html_to_text_gives_the_shared_examples_exactly(html, text):
    assert html_to_text(html) == text


def test_a_long_title_is_shortened_and_the_date_is_kept(kind, fake):
    title = (
        "Northern region quarterly operations planning and budget alignment review with our vendors"
    )
    assert len(title) == 90
    first = NOW - 2 * WEEK
    for n, start in ((1, first), (2, first + WEEK)):
        fake.event(n, start, start + HOUR, summary=title)
    got = pull(fake, opened(kind, fake, since=first - HOUR))
    topics = [draft.topic for draft in got.drafts]
    assert len(set(topics)) == 2
    for draft, start in zip(got.drafts, (first, first + WEEK), strict=True):
        assert draft.title == title
        cut, day = draft.topic.rsplit(", ", 1)
        assert day == f"{start:%Y-%m-%d}"
        assert cut and title.startswith(cut) and len(cut) < len(title)
        assert title[len(cut)] == " "  # cut at a word


# Read once, and the cursor


def test_an_event_is_read_once_whatever_happens_to_it_later(kind, fake):
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR, summary="Kickoff")
    fake.event(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR, summary="Design review")
    fake.event(3, SINCE + 5 * HOUR, SINCE + 6 * HOUR, summary="Retro")
    late = fake.event(4, NOW + 30 * MINUTE, NOW + HOUR, summary="Added late")
    late.added = NOW + 2 * HOUR  # put on the calendar an hour after it ended
    read = reader(kind, fake)
    first = read()
    assert refs(first) == [ref(1), ref(2), ref(3)]

    one, two, three = (fake.calendars[ADA].events[eid(n)] for n in (1, 2, 3))
    one.summary, one.updated = "Kickoff (notes added)", NOW
    two.end, two.updated = two.end + HOUR, NOW  # moved
    three.status, three.updated = "cancelled", NOW
    fake.shows_cancelled = True
    fake.clock.now = NOW + 90 * MINUTE
    second = read(first.cursor)
    assert second.drafts == [] and second.skipped == []
    fake.clock.now = NOW + 3 * HOUR
    third = read(second.cursor)
    assert refs(third) == [ref(4)]
    fake.clock.now = NOW + 4 * HOUR
    assert read(third.cursor).drafts == []


def test_the_cursor_holds_no_secret_id_title_or_name_and_stays_bounded_over_weeks(kind, fake):
    fake.calendar(ADA, "Pacific/Auckland", {ADA: "owner"})
    weeks = 9
    made = []
    people = [
        {"email": f"{name.split()[0].lower()}@example.test", "displayName": name} for name in NAMES
    ]
    for n in range(1, weeks * 7 * 3 + 1):  # an event every eight hours
        start = NOW + n * 8 * HOUR - 4 * HOUR
        made.append(
            fake.event(n, start, start + 30 * MINUTE, summary=f"Standup {n}", attendees=people)
        )
    for w in range(weeks):  # and an all-day event every week
        day = (NOW + w * WEEK + 2 * DAY).date()
        made.append(fake.event(500 + w, day, day + DAY, summary=f"Field day {w}"))
    read = reader(kind, fake, cred=USER, since=NOW)
    cursor, written, length = None, Counter(), 0
    for step in range(1, weeks * 7 * 4 + 1):  # a pull every six hours
        fake.clock.now = NOW + step * 6 * HOUR
        got = read(cursor)
        cursor = got.cursor
        written.update(refs(got))
        assert got.skipped == []
        assert isinstance(json.loads(cursor), dict | list)  # opaque, but JSON
        assert not leaked(cursor, fake) and "@" not in cursor
        for words in (*NAMES, "Standup", "Field day"):
            assert words not in cursor, words
        assert not any(found.removeprefix("calendar:") in cursor for found in written)
        if step == 6 * 7 * 4:
            length = len(cursor)

    last = fake.clock.now
    ended = [e for e in made if ended_at(e, "Pacific/Auckland") <= last]
    assert written == Counter({f"calendar:{e.eid}": 1 for e in ended})
    assert len(cursor) <= 1.25 * length + 200  # it did not grow with the events
    bounds = [parsed(r.params["timeMin"]) for r in fake.calls("events")]
    assert bounds and last - min(bounds) < 4 * DAY  # the listing keeps up with the clock


# When Google says no


@pytest.mark.parametrize(
    "how",
    [401, "403 forbidden", "403 insufficientPermissions", "403 accessNotConfigured"],
)
def test_access_refused_by_the_calendar_api_says_what_to_check(kind, fake, how):
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.always["events"] = how
    said = str(failed_pull(fake, opened(kind, fake)))
    assert "scope" in said.lower()
    assert CANARY not in said and not leaked(said, fake)
    assert fake.slept == [] and len(fake.calls("events")) == 1


def test_a_calendar_usage_limit_is_not_retried_and_says_so(kind, fake):
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.always["events"] = "403 quotaExceeded"
    said = str(failed_pull(fake, opened(kind, fake)))
    assert "limit" in said.lower()
    assert CANARY not in said and not leaked(said, fake)
    assert fake.slept == [] and len(fake.calls("events")) == 1


@pytest.mark.parametrize("why", ["Google answers 404", "the calendar is not shared"])
def test_a_calendar_not_found_names_the_calendar_id_setting(kind, fake, why):
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR)
    if why == "Google answers 404":
        fake.always["events"] = 404
        source = opened(kind, fake)
    else:
        fake.calendar(SHARED, "UTC", {"brook@example.test": "owner"})
        source = opened(kind, fake, calendar_id=SHARED)
    said = str(failed_pull(fake, source))
    assert "calendar_id" in said
    assert "not found" in said.lower() or "shar" in said.lower()
    assert CANARY not in said and not leaked(said, fake)


@pytest.mark.parametrize("events", ["some", "none"])
def test_a_calendar_shared_as_free_busy_only_fails_and_says_to_share_its_details(
    kind, fake, events
):
    fake.calendar(SHARED, "UTC", {ADA: "freeBusyReader"})
    if events == "some":
        fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR, cal=SHARED)
    said = str(failed_pull(fake, opened(kind, fake, calendar_id=SHARED)))
    assert "calendar_id" in said and "detail" in said.lower()
    assert not leaked(said, fake)


@pytest.mark.parametrize(
    "how", [429, "429, no body", "403 rateLimitExceeded", "403 userRateLimitExceeded"]
)
def test_rate_limits_are_retried_with_the_documented_backoff_through_sleep(kind, fake, how):
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.forced = [("events", how), ("events", how)]
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(1)]
    assert len(fake.slept) == 2
    backed_off(fake.slept)

    fake.slept.clear()
    fake.always["events"] = how
    said = str(failed_pull(fake, opened(kind, fake)))
    assert 1 <= len(fake.slept) <= 4
    backed_off(fake.slept)
    assert len(fake.calls("events")) == len(fake.slept) + 1
    assert CANARY not in said and not leaked(said, fake)


@pytest.mark.parametrize(
    ("endpoint", "how"),
    [
        ("events", 410),
        ("events", 500),
        ("events", 503),
        ("events", "timeout"),
        ("events", "not json"),
        ("events", "array"),
        ("events", "items not a list"),
        ("events", "event without id"),
        ("events", "event without end"),
        ("events", "event with an empty end"),
        ("events", "event with a bad end"),
        ("events", "all-day event with a bad date"),
        ("token", 500),
        ("token", "timeout"),
    ],
)
def test_a_server_error_a_timeout_or_an_odd_body_fails_the_pull(kind, fake, endpoint, how):
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.event(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR)
    fake.always[endpoint] = how
    said = str(failed_pull(fake, opened(kind, fake)))
    assert said.strip() and CANARY not in said and not leaked(said, fake)


# The deadline


def test_a_pull_cut_by_its_deadline_and_every_resume_point_go_on_without_loss_or_repeat(kind, fake):
    fake.short_pages = 7  # an empty first page, then pages of seven
    for n in range(1, 21):
        start = SINCE + n * HOUR
        fake.event(n, start, start + 30 * MINUTE)
    whole = pull(fake, opened(kind, fake))
    full = refs(whole)
    assert full == [ref(n) for n in range(1, 21)]
    assert all(draft.resume for draft in whole.drafts)
    for index, draft in enumerate(whole.drafts):  # a run that stops right after this draft
        fake.clock.now += HOUR
        rest = pull(fake, opened(kind, fake), draft.resume)
        assert refs(rest) == full[index + 1 :], index

    fake.clock.now += HOUR
    fake.tick = MINUTE  # every request takes a minute
    deadline = fake.clock.now + 3 * MINUTE + 30 * timedelta(seconds=1)
    cut = pull(fake, opened(kind, fake, deadline=deadline))
    assert sum(1 for r in fake.calls() if r.at > deadline) <= 1  # it stopped asking for pages
    done = refs(cut)
    assert 0 < len(done) < len(full) and len(set(done)) == len(done)
    assert done == [found for found in full if found in done]  # by end time, then id
    assert all(draft.resume for draft in cut.drafts)
    fake.tick = timedelta(0)
    for index, draft in enumerate(cut.drafts):
        fake.clock.now += HOUR
        rest = pull(fake, opened(kind, fake), draft.resume)
        assert refs(rest) == [found for found in full if found not in done[: index + 1]], index
    fake.clock.now += HOUR
    rest = pull(fake, opened(kind, fake), cut.cursor)
    assert refs(rest) == [found for found in full if found not in done]


# Together with the framework


@pytest.fixture
def world(tmp_path, fake):
    clock = Clock(SINCE - DAY)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    for name in ("calendar", "planning"):
        store.create_project(name)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    yield SimpleNamespace(store=store, db=db, clock=clock, http=http)
    store.close()


def run(world, fake, at: datetime, cred: str = SA):
    fake.clock.now = at
    fake.new_pull()
    return asyncio.run(
        run_source(
            world.store, "calendar", http=world.http, env=fake.env(cred), clock=fake.clock,
            sleep=fake.sleep,
        )
    )  # fmt: skip


def notes(world, project: str = "calendar") -> list:
    return world.store.list_notes(world.store.get_project(project).id)


def pages(world, project: str = "calendar") -> list:
    return world.store.list_pages(world.store.get_project(project).id)


def test_through_the_runner_a_source_is_off_until_switched_on_then_writes_each_event_once(
    world, fake
):
    fake.event(1, SINCE - 3 * HOUR, SINCE - HOUR, summary="Before")
    fake.event(2, SINCE - 5 * HOUR, SINCE + HOUR, summary="Across")
    fake.event(3, SINCE + 2 * HOUR, SINCE + 3 * HOUR, summary="Within")
    fake.event(4, NOW - HOUR, NOW + HOUR, summary="Going on")
    fake.event(5, SINCE + 4 * HOUR, SINCE + 5 * HOUR, summary="Within", event_type="focusTime")

    added = world.store.add_source("calendar", "google_calendar", config(projects=[]))
    assert added.enabled is False
    assert run(world, fake, NOW).off is True and fake.requests == []

    world.clock.now = SINCE
    world.store.enable_source("calendar", projects=["calendar"])
    report = run(world, fake, NOW)
    assert (report.saved, report.unchanged, report.refused, report.rejected) == (2, 0, 0, 0)
    assert report.skipped == 0
    held = notes(world)
    assert sorted(c.source_ref for c in held) == [ref(2), ref(3)]
    assert {(c.via, c.label) for c in held} == {("google-calendar", "google-calendar")}
    assert len(pages(world)) == 2  # a page an event
    assert notes(world, "planning") == []
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind) == ("source.synced", "system")
    assert (event.detail["source"], event.detail["saved"]) == ("calendar", 2)

    again = run(world, fake, NOW + 5 * MINUTE)
    assert (again.saved, again.unchanged, again.skipped) == (0, 0, 0)
    after = run(world, fake, NOW + 2 * HOUR)  # the fourth has ended
    assert after.saved == 1 and ref(4) in {c.source_ref for c in notes(world)}
    assert len(pages(world)) == 3

    assert not leaked(dump(world.db), fake)
    for event in world.store.list_audit(limit=100):
        assert not leaked(json.dumps(event.detail), fake)


def test_through_the_runner_each_occurrence_of_a_weekly_event_with_a_long_title_has_its_page(
    world, fake
):
    title = (
        "Northern region quarterly operations planning and budget alignment review with our vendors"
    )
    first = NOW - 2 * WEEK
    for k, n in enumerate((1, 2)):
        start = first + k * WEEK
        fake.event(n, start, start + HOUR, summary=title, series="c5series0lt", original=start)
    world.store.add_source("calendar", "google_calendar", config())
    world.clock.now = first - HOUR
    world.store.enable_source("calendar")
    report = run(world, fake, NOW)
    assert report.saved == 2
    shown = pages(world)
    assert len(shown) == 2
    assert sorted(page.slug[-10:] for page in shown) == [
        f"{first:%Y-%m-%d}",
        f"{first + WEEK:%Y-%m-%d}",
    ]


def test_through_the_runner_a_calendar_shared_as_free_busy_only_writes_nothing_and_says_why(
    world, fake
):
    fake.calendar(SHARED, "UTC", {ADA: "freeBusyReader"})
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR, cal=SHARED)
    world.store.add_source("calendar", "google_calendar", config(calendar_id=SHARED))
    world.clock.now = SINCE
    world.store.enable_source("calendar")
    with pytest.raises(SourceError):
        run(world, fake, NOW)
    failed = world.store.get_source("calendar")
    assert failed.cursor is None and "calendar_id" in failed.last_error
    assert notes(world) == []


@pytest.mark.parametrize(
    ("cred", "echoes"), [(SA, ("token",)), (USER, ("token", "refresh", "secret"))]
)
def test_through_the_runner_a_secret_google_sends_back_is_never_stored(world, fake, cred, echoes):
    for n, echo in enumerate(echoes, start=1):
        event = fake.event(n, SINCE + n * HOUR, SINCE + n * HOUR + 30 * MINUTE)
        event.description = f"echo:{echo}"
    fake.event(9, SINCE + 9 * HOUR, SINCE + 10 * HOUR)
    original = fake._item

    def echoing(cal, e, role):  # a server that repeats a secret in what it sends
        item = original(cal, e, role)
        said = {
            "token": (fake.valid or ("",))[0],
            "refresh": REFRESH_TOKEN,
            "secret": CLIENT_SECRET,
        }
        if str(item.get("description", "")).startswith("echo:"):
            item["description"] = f"The server says {said[item['description'][5:]]}"
        return item

    fake._item = echoing
    world.store.add_source("calendar", "google_calendar", config(cred))
    world.clock.now = SINCE
    world.store.enable_source("calendar")
    report = run(world, fake, NOW, cred=cred)
    assert report.saved == 1 and [c.source_ref for c in notes(world)] == [ref(9)]
    assert report.reasons.get(HELD_CREDENTIALS) == len(echoes)
    assert not leaked(dump(world.db), fake)


def test_through_the_runner_a_failed_run_moves_no_cursor_and_the_next_writes_the_rest(world, fake):
    world.store.add_source("calendar", "google_calendar", config())
    world.clock.now = SINCE
    world.store.enable_source("calendar")
    fake.event(1, SINCE + HOUR, SINCE + 2 * HOUR)
    assert run(world, fake, NOW).saved == 1
    before = world.store.get_source("calendar")

    fake.event(2, NOW + HOUR, NOW + 2 * HOUR)
    fake.event(3, NOW + 3 * HOUR, NOW + 4 * HOUR)
    fake.forced = [("events", 500)]
    with pytest.raises(SourceError):
        run(world, fake, NOW + 5 * HOUR)
    failed = world.store.get_source("calendar")
    assert failed.cursor == before.cursor and failed.last_error
    assert CANARY not in failed.last_error and not leaked(failed.last_error, fake)
    assert len(notes(world)) == 1

    report = run(world, fake, NOW + 6 * HOUR)
    assert report.saved == 2 and world.store.get_source("calendar").last_error is None
    assert sorted(c.source_ref for c in notes(world)) == [ref(1), ref(2), ref(3)]
    assert run(world, fake, NOW + 7 * HOUR).saved == 0


def test_the_command_line_adds_switches_on_and_syncs_without_showing_a_secret(
    tmp_path, kind, keys, monkeypatch, capsys
):
    db = tmp_path / "cairn.db"
    store = Store(db)
    store.create_project("calendar")
    store.close()
    fake = FakeGoogle(RealClock(), keys)
    monkeypatch.setenv(CRED_ENV, fake.env(SA)[CRED_ENV])
    routed = httpx.MockTransport(fake.handler)

    async def through_the_stand_in(self, request):  # every request cairn sends, offline
        return await routed.handle_async_request(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", through_the_stand_in)
    said: list[str] = []

    def cli(*argv: str) -> int:
        code = main(list(argv))
        out = capsys.readouterr()
        said.extend([out.out, out.err])
        return code

    settings = json.dumps(config(projects=[]))
    assert cli("source-add", "--db", str(db), "--name", "calendar", "--kind", "google_calendar",
               "--config", settings) == 0  # fmt: skip
    assert cli("source-sync", "--db", str(db), "--name", "calendar") == 0
    assert fake.requests == []  # off: nothing read, no token asked for
    assert cli("source-enable", "--db", str(db), "--name", "calendar", "--project", "calendar") == 0

    store = Store(db)
    try:
        since = parsed(store.get_source("calendar").enabled_at)
    finally:
        store.close()
    fake.event(1, since - 2 * HOUR, since - HOUR)
    just_after = since + timedelta(milliseconds=2)
    fake.event(2, since - 30 * MINUTE, just_after)
    while datetime.now(UTC) <= just_after + timedelta(milliseconds=1):
        pass  # the real clock, which the command line reads, has passed it
    assert cli("source-sync", "--db", str(db), "--name", "calendar") == 0
    assert cli("source-list", "--db", str(db)) == 0
    assert len(fake.calls("token", every=True)) == 1

    monkeypatch.setenv(CRED_ENV, json.dumps(authorized_user(client_secret=None)))
    assert cli("source-sync", "--db", str(db), "--name", "calendar") == 1
    assert CRED_ENV in said[-1]

    store = Store(db)
    try:
        held = store.list_notes(store.get_project("calendar").id)
        assert [c.source_ref for c in held] == [ref(2)]
    finally:
        store.close()
    shown = "\n".join(said)
    assert not leaked(shown, fake) and CANARY not in shown
    assert not leaked(dump(db), fake)
