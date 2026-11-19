"""The Google Calendar source: the events of one calendar, each read once after it has ended,
a page an event.

Contract (frozen by tests/acceptance/test_c5_google_calendar.py; the API's shapes are recorded
in docs/google-calendar-api.md and docs/google-auth.md, with what Google's pages do not state
marked as such and what cairn decided marked **[cairn]**). cairn never writes to Google. It
signs in through cairn.sources.google_auth, calls the API through cairn.sources.google_api,
and turns a description's HTML into text with cairn.sources.markup.html_to_text.

The kind ("google_calendar" in cairn.sources.KINDS)
- It is withheld (SourceKind.withheld): a page that cites one of its notes is not sent to a
  model unless the operator allows it (cairn.arrange), since Google's rules for its users'
  data restrict passing it on, and whether the operator's model may read calendar events is theirs
  to decide.
- It starts off: added off, read only once an owner or admin switches it on with a project, and
  then only events that ended at or after that switch-on (`since`). Its notes are written
  via "google-calendar", with the label "google-calendar". It proposes the project "calendar"
  (DEFAULT_PROJECT). It asks for one scope, SCOPE.
- The config: `credentials_env` (required: the name of the environment variable that holds
  the credential, capitals, digits and "_"), `subject` (one email address: the person a
  service account acts for, by domain-wide delegation), `calendar_id` (the one calendar it
  reads: "primary" unless given; text of 1 to 254 characters with no white space and no
  control character, and not "." or "..", which a URL would resolve away), `projects` (at
  most one project name; none while it is off) and `base_url` (DEFAULT_BASE_URL unless given;
  https, or http on a loopback host; no user, password, query or fragment; a trailing "/"
  dropped). No other setting. A refusal names the setting
  at fault and never repeats a value: a value that looks like a credential (JSON, a PEM key,
  one of Google's token or secret prefixes, or a pattern of the "secrets" guard) is refused
  as such.
- `open` loads the credential (google_auth.load_credential: a SourceError naming the
  variable). A refresh token reads as the person who authorised it, so `subject` is refused
  with one. A service account reads as `subject` when given; without one it acts as itself,
  has no primary calendar to read, and `calendar_id` must name a calendar shared with its
  address. Its `secrets` are the credential's, and the access token (and its Bearer header)
  once the pull has it. Nothing is sent before the first pull.
- `describe` says which calendar it reads and as whom: the `calendar_id` and the `subject`,
  values the operator typed, which owners and admins see on the Sources page; never the
  variable or a credential.

Finding the events
- Each pull asks for one access token, then lists the calendar's events with exactly
  `singleEvents=true`, `orderBy=startTime`, `timeMin` OVERLAP (two days) before the cursor's
  mark, `timeMax` the clock's time when the pull began, both RFC 3339 in UTC with
  milliseconds, and `maxResults` PAGE (2,500, Google's largest), the same beside every
  `pageToken`, at `<base_url>/calendars/<calendar_id>/events` with the id percent-encoded.
  **[cairn]** The mark is the clock at the start of the last pull that finished (the first
  pull's is `since`; switched on again, the later of the two).
- Every page's `accessRole` is read: "freeBusyReader" fails the pull, saying to share the
  event details. The calendar's zone is the first `timeZone` a page gives that the zone
  database (zoneinfo, with the tzdata package) knows; **[cairn]** an absent or unknown name,
  or one that is not an IANA name's shape, is never an error: it leaves the fallback.
- Every event listed is read before any is written: one that is not cancelled must have an
  `id` (text of 1 to 1,024 characters, no white space) and a `start` and an `end` that are
  both timed (RFC 3339 with an offset) or both all-day (a date), or the pull fails. A
  cancelled event needs only its id.
- When an event has ended: a timed event at its `end`; an all-day event at 00:00 on its
  `end.date` (exclusive) in the calendar's zone, or, in the fallback, at 12:00 UTC on it.
- What is written, by end time, then by id, whatever order Google used and however it paged:
  each event listed that has ended by the pull's start, at or after `since`, not cancelled,
  of `eventType` "default" (or none), not "private" or "confidential", not declined by the
  attendee who is the calendar's own (`self`), and whose digest is not in the cursor. An event
  listed twice is read once. What is passed over is not reported as skipped: nothing went
  wrong. The one skip: an event cairn fails on while it makes its draft (UNREADABLE, the
  Skip's ref its id) is skipped and kept in the cursor like one written, so one event never
  stops the source.

The cursor (opaque JSON this module owns, version 1; never a token, a key, an event's id, a
title, a name or an address)
- `mark`: the moment the listing's lower bound is OVERLAP before, to the microsecond.
- `seen`: the digest (google_api.digest, 16 hex digits of the SHA-256 of the event's id) of
  each event written, with when it ended in whole Unix seconds. An event listed again while
  its digest is kept is not written again, and its end is moved to the later of the two (an
  event moved a little later is still the event that was written). **[cairn]** A digest is
  let go once its end is more than a minute before the next listing's lower bound: Google's
  listing can no longer return it (for an all-day event in the fallback Google's end is
  earlier still). So the cursor holds about two days of events however many weeks it runs,
  and an event moved to a later time than that is read again, as the event it then is, once
  it has ended there.
- Each draft carries `resume`: the cursor the pull started from with the digests written up
  to and including that draft, so a run that stops there is picked up at the next draft.
- The bounds of a pull: its `deadline` and BUDGET requests (**[cairn]** 100: 250,000 events at
  Google's largest page, far inside the quota's 600 a minute). A pull that reaches one
  before it signs in, or before its first page, fails with a SourceError; one that reaches
  it later writes what the pages it has hold, with the resume cursor (the mark does not
  move), and fails when they hold nothing new. **[cairn]** Google chooses how many events a
  page holds, so a pull also keeps at most MAX_EVENTS (BUDGET x PAGE: 250,000) events of the
  listing, and stops listing there the same way.

When Google says no (a message never holds a token, a body, an id or an address)
- 401, or 403 that is not a rate limit or quotaExceeded: access refused, and what to check
  (the scope, the delegation, that the API is enabled, that the account may read the
  calendar). 403 quotaExceeded: a calendar usage limit, not retried. 404: no calendar for
  `calendar_id`, or not shared with the account that reads it. 429, or a 403 whose reason is
  rateLimitExceeded or userRateLimitExceeded: waited out (google_api: 2^n seconds and up to
  one more, n from 0) at most 4 times, then a SourceError. 410, 400, a redirect (never
  followed), 5xx, a network error (by its class), or an answer not of the documented shape:
  a SourceError. A failed pull returns nothing, so no cursor moves.

What it writes, for each event, into the config's project
- `title`: the event's `summary` on one line with any email address left out, cut at a word
  to TITLE_MAX characters and TITLE_BYTES bytes as the store counts a heading; or UNTITLED.
- `topic`: the title, a comma and the start date in UTC (an all-day event's `start.date`),
  "Quarterly planning, 2026-11-12", so each occurrence of a weekly meeting has its own page.
  The title's part is cut (at a word when it can be) so that its slug takes at most
  TOPIC_SLUG_ROOM of the 64 the compiler keeps, and the date is never what is cut.
- `text`: "# <title>", a blank line, then the facts, each a list item on one line:
  "- When: YYYY-MM-DD HH:MM to YYYY-MM-DD HH:MM UTC" (an all-day event: "- When: YYYY-MM-DD,
  all day", or its first and last day, the last being the day before `end.date`, "... to
  YYYY-MM-DD, all day"); "- Where: " the location; "- Organiser: " the organiser's display
  name; "- Attendees: " the people by display name, in Google's order, then "and N more" for
  those Google gives no name (a room, `resource`, is not a person and is left out); "- Meet: "
  the meeting code, for a conference whose `conferenceSolution.key.type` is "hangoutsMeet",
  exactly as Google gives `conferenceId` (the code every guest sees in the meeting's address,
  which the Meet source titles its pages with). A fact Google does not give is left out. A
  name holding "@" counts as no name, an address in the location is left out, and a code
  holding "@" or white space is not written: no email address is among the title and the
  facts. "Holding "@"" is cairn.sources.names's check, so an at sign of another width
  (U+FF20, U+FE6B) counts too. Then a blank line and the description, html_to_text of it,
  its words otherwise unchanged (an address in it is the description's, and the note
  guard decides on it). A description too long for a page (K4: cairn.sources.fit, a page's
  bounds read when the note is made) is cut between two passages where it can, a fence it
  cuts through closed, and followed by DESCRIPTION_CUT; a note still past a page's bounds
  (a list of attendees no event has) is cut at its end and ends with NOTE_CUT.
- `source_ref`: "calendar:<event id>". `label`: "google-calendar".
"""

import asyncio
import json
import math
import random
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from cairn.sources import Clock, NoteDraft, Pulled, Skip, SourceError, SourceKind, fit, names
from cairn.sources.config import check_projects
from cairn.sources.google_api import (
    EMAIL,
    ENV_NAME,
    RETRIES,
    SETTING_NAME,
    SIGN_IN_LATE,
    Reader,
    Reply,
    Spent,
    check_base_url,
    digest,
    looks_like_a_credential,
    moment,
    stamp,
)
from cairn.sources.google_auth import (
    AuthorizedUser,
    Credential,
    ServiceAccount,
    access_token,
    load_credential,
)
from cairn.sources.markup import html_to_text
from cairn.sources.timing import limit

DEFAULT_BASE_URL = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
SCOPES = [SCOPE]
SERVICE = "Google Calendar"
API = "the Google Calendar API"
WHAT = "events.list"
KIND_NAME = "google_calendar"
VIA = LABEL = "google-calendar"
ORIGIN = "calendar:"
DEFAULT_PROJECT = "calendar"
PRIMARY = "primary"
UNTITLED = "Untitled event"
SETTINGS = ("credentials_env", "subject", "calendar_id", "projects", "base_url")
CALENDAR_ID_MAX = 254
OVERLAP = timedelta(days=2)
MARGIN = 60  # seconds a digest is kept past the next listing's lower bound
PAGE = 2_500  # Google's largest
BUDGET = 100  # requests of one pull
MAX_EVENTS = BUDGET * PAGE  # of the listing one pull keeps: 250,000, as many as full pages hold
CURSOR_VERSION = 1
TITLE_MAX = 150  # characters, inside the store's 200
TITLE_BYTES = 300  # as the store counts a heading (JSON in UTF-8), inside its 400
TOPIC_SLUG_ROOM = 53  # of the slug's 64: "-YYYY-MM-DD" takes the rest
CODE_MAX = 128
DIGEST = re.compile(r"[0-9a-f]{16}")
EVENT_ID = re.compile(r"\S{1,1024}")
ZONE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*")
FALLBACK_HOUR = time(12)  # when an all-day event ends, in UTC, without a known zone
PASSED_VISIBILITY = frozenset({"private", "confidential"})


# The config


def _what_it_takes() -> str:
    return f"it takes {', '.join(SETTINGS)}"


def check_config(config: object) -> dict[str, Any]:
    """The config as stored, with the defaults filled in, or ValueError naming the setting at
    fault and never a value."""
    if not isinstance(config, Mapping):
        raise ValueError(f"a {KIND_NAME} source's config is an object of settings")
    for setting in config:
        if setting in SETTINGS:
            continue
        if (
            isinstance(setting, str)
            and SETTING_NAME.fullmatch(setting)
            and not looks_like_a_credential(setting)
        ):
            raise ValueError(f"a {KIND_NAME} source has no setting {setting!r}: {_what_it_takes()}")
        raise ValueError(
            f"a {KIND_NAME} source does not take one of the settings given: {_what_it_takes()}"
        )
    checked: dict[str, Any] = {"credentials_env": _check_variable(config.get("credentials_env"))}
    subject = config.get("subject")
    if subject is not None:
        if isinstance(subject, str) and looks_like_a_credential(subject):
            raise ValueError("subject holds what looks like a credential, not an email address")
        if not isinstance(subject, str) or len(subject) > 254 or not EMAIL.fullmatch(subject):
            raise ValueError(
                "subject is one email address: the person a service account reads the calendar for"
            )
        checked["subject"] = subject
    checked["calendar_id"] = _check_calendar_id(config.get("calendar_id", PRIMARY))
    checked["projects"] = check_projects(
        config.get("projects", []), kind="google_calendar", looks_like=looks_like_a_credential
    )
    checked["base_url"] = check_base_url(config.get("base_url", DEFAULT_BASE_URL), API)
    return checked


def _check_variable(variable: object) -> str:
    if variable is None:
        raise ValueError(
            f"a {KIND_NAME} source needs credentials_env: the name of the environment variable"
            " that holds the Google credential"
        )
    if isinstance(variable, str) and looks_like_a_credential(variable):
        raise ValueError(
            "credentials_env holds what looks like a credential: put the credential in an"
            " environment variable of the process that syncs, and name that variable here"
        )
    if not isinstance(variable, str) or not ENV_NAME.fullmatch(variable):
        raise ValueError(
            "credentials_env names an environment variable (capitals, digits and _, at most"
            " 128), which holds the Google credential: the credential itself never goes in a"
            " config"
        )
    return variable


def _check_calendar_id(value: object) -> str:
    if isinstance(value, str) and looks_like_a_credential(value):
        raise ValueError("calendar_id holds what looks like a credential, not a calendar's id")
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= CALENDAR_ID_MAX
        or any(ch.isspace() or unicodedata.category(ch)[0] == "C" for ch in value)
        or value in (".", "..")  # quote() leaves them, and a URL resolves them away
    ):
        raise ValueError(
            f"calendar_id is the id of one calendar (at most {CALENDAR_ID_MAX} characters, no"
            ' spaces): "primary" for the calendar of the account it reads as, or another'
            " calendar's id, which looks like an email address"
        )
    return value


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
    jitter: Callable[[], float] = random.random,
) -> "CalendarSource":
    """The source a stored config describes, with its credential read from `env`.
    SourceError names a variable or a setting, never a value."""
    credential = load_credential(config["credentials_env"], env)
    subject = config.get("subject")
    if isinstance(credential, AuthorizedUser) and subject is not None:
        raise SourceError(
            f"source {name!r} reads with a refresh token, which reads as the person who"
            " authorised it: remove subject from its config"
        )
    alone = isinstance(credential, ServiceAccount) and subject is None
    if alone and config["calendar_id"] == PRIMARY:
        raise SourceError(
            f"source {name!r} reads with a service account acting as itself (no subject),"
            " which has no calendar of its own to read: set calendar_id to a calendar"
            " shared with the service account's address, or set subject to the person it"
            " reads for (domain-wide delegation)"
        )
    return CalendarSource(
        name, config, credential, http=http, clock=clock, sleep=sleep, deadline=deadline,
        since=since, jitter=jitter,
    )  # fmt: skip


def describe(config: Mapping[str, Any]) -> str:
    """What a source reads, for GET /sources: which calendar, as whom, from where."""
    who = config.get("subject") or "the account its credential signs in as"
    calendar = config["calendar_id"]
    which = (
        f"the primary calendar of {who}"
        if calendar == PRIMARY
        else f"calendar {calendar}, read as {who}"
    )
    return f"Events of {which}, at {config['base_url']}; each is read once, after it has ended"


KIND = SourceKind(
    check=check_config,
    open=open_source,
    via=VIA,
    starts_off=True,
    describe=describe,
    default_project=DEFAULT_PROJECT,
    withheld=True,
    ref_form=ORIGIN.removesuffix(":"),  # M3: what its notes' source_ref starts with
)


# Times and zones


def _utc_now() -> datetime:
    return datetime.now(UTC)


def zone_of(value: object) -> tzinfo | None:
    """The zone a listing's `timeZone` names, or None when it names none the database knows."""
    if not isinstance(value, str) or len(value) > 64 or not ZONE_NAME.fullmatch(value):
        return None
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def ended_at(end: datetime | date, zone: tzinfo | None) -> datetime:
    """When an event counts as ended: a timed event at its end; an all-day event at 00:00 on
    its end date in `zone`, or at 12:00 UTC on it without one."""
    if isinstance(end, datetime):
        return end
    if zone is not None:
        try:
            return datetime.combine(end, time(0), zone).astimezone(UTC)
        except (ValueError, OverflowError):  # before year 1 once in UTC: the fallback
            pass
    return datetime.combine(end, FALLBACK_HOUR, UTC)


def listing(lower: datetime, upper: datetime) -> dict[str, str]:
    """events.list's parameters, beside which each page's token goes."""
    return {
        "singleEvents": "true",
        "orderBy": "startTime",
        "timeMin": stamp(lower),
        "timeMax": stamp(upper),
        "maxResults": str(PAGE),
    }


def _seconds(moment_: datetime) -> int:
    return math.floor(moment_.timestamp())


def _micro_stamp(moment_: datetime) -> str:
    return moment_.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# The cursor


@dataclass(frozen=True)
class Cursor:
    """What a pull starts from (see the module docstring)."""

    mark: datetime
    seen: dict[str, int] = field(default_factory=dict)

    def switched_on(self, since: datetime) -> "Cursor":
        """The cursor for a source last switched on at `since`: the mark is at least it."""
        return replace(self, mark=max(self.mark, since))

    def pruned(self, lower: datetime) -> "Cursor":
        """What a listing from `lower` can no longer return let go."""
        least = _seconds(lower) - MARGIN
        return replace(self, seen={d: e for d, e in self.seen.items() if e >= least})

    def encode(self) -> str:
        return json.dumps(
            {
                "v": CURSOR_VERSION,
                "mark": _micro_stamp(self.mark),
                "seen": {d: self.seen[d] for d in sorted(self.seen)},
            },
            separators=(",", ":"),
        )

    @classmethod
    def decode(cls, text: str) -> "Cursor":
        broken = SourceError(
            "the stored cursor is not one this Google Calendar source wrote: remove the source"
            " and add it again to start afresh"
        )
        try:
            state = json.loads(text)
        except (ValueError, RecursionError):
            raise broken from None
        if not isinstance(state, dict) or state.get("v") != CURSOR_VERSION:
            raise broken
        mark, seen = moment(state.get("mark")), state.get("seen")
        if mark is None or not isinstance(seen, dict):
            raise broken
        if not all(
            DIGEST.fullmatch(d) and isinstance(e, int) and not isinstance(e, bool) and e >= 0
            for d, e in seen.items()
        ):
            raise broken
        return cls(mark=mark, seen=dict(seen))


# What the API answers


@dataclass(frozen=True)
class Event:
    """An event that is not cancelled: its id, its start and end (datetimes in UTC, or dates
    for an all-day event), when it counts as ended, and the fields a draft reads."""

    eid: str
    start: datetime | date
    end: datetime | date
    ended: datetime
    item: Mapping[str, Any] = field(repr=False)


def _shape(why: str = "") -> SourceError:
    said = f" ({why})" if why else ""
    return SourceError(f"{SERVICE}'s answer to {WHAT} is not the documented shape{said}")


def _when(value: object) -> datetime | date | None:
    """An EventDateTime as a moment in UTC (timed) or a date (all-day); None when neither."""
    if not isinstance(value, dict):
        return None
    if "dateTime" in value:
        return moment(value["dateTime"])
    day = value.get("date")
    if isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        try:
            return date.fromisoformat(day)
        except ValueError:
            return None
    return None


def read_event(item: object, zone: tzinfo | None) -> Event | None:
    """A listed event, or None when it is cancelled; SourceError when it is not the
    documented shape (never quoting it)."""
    if not isinstance(item, dict):
        raise _shape("an event is not an object")
    eid = item.get("id")
    if not isinstance(eid, str) or not EVENT_ID.fullmatch(eid):
        raise _shape("an event has no usable id")
    status = item.get("status", "confirmed")
    if not isinstance(status, str):
        raise _shape("an event's status is not text")
    if status == "cancelled":
        return None
    start, end = _when(item.get("start")), _when(item.get("end"))
    if end is None:
        raise _shape("an event has no usable end")
    if start is None or isinstance(start, datetime) != isinstance(end, datetime):
        raise _shape("an event has no usable start")
    return Event(eid, start, end, ended_at(end, zone), item)


def passed_over(event: Event) -> bool:
    """Whether an event is not a meeting of the calendar's person: another type than
    "default", private or confidential, or declined by the calendar's own attendee."""
    item = event.item
    if item.get("eventType", "default") != "default":
        return True
    visibility = item.get("visibility")
    if isinstance(visibility, str) and visibility in PASSED_VISIBILITY:
        return True
    attendees = item.get("attendees")
    return isinstance(attendees, list) and any(
        isinstance(a, dict) and a.get("self") is True and a.get("responseStatus") == "declined"
        for a in attendees
    )


# What a draft says


def _one_line(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _without_addresses(value: object) -> str:
    return _one_line(EMAIL.sub(" ", _one_line(value)))


def _name(value: object) -> str | None:
    """A display name on one line; None when there is none, or when it holds an address."""
    return names.shown(value)


def _heading_bytes(text: str) -> int:
    return len(json.dumps(text, ensure_ascii=False).encode())


def _cut(text: str, too_long: Callable[[str], bool], most: int) -> str:
    """`text` cut at a word (or a character, when it is one word) until it is not too long.
    `most` is the most characters it may keep: the text is first cut to one more than that,
    so the work does not grow with the text's length."""
    text = text[: most + 1]
    while text and too_long(text):
        text = (text.rsplit(" ", 1)[0] if " " in text else text[:-1]).rstrip(" ,")
    return text


def title_of(summary: object) -> str:
    """The event's title: its summary on one line, without addresses, within its bounds."""
    title = _cut(  # addresses go first: a cut through one would leave a piece of it
        _without_addresses(summary),
        lambda t: len(t) > TITLE_MAX or _heading_bytes(t) > TITLE_BYTES,
        TITLE_MAX,
    )
    return title or UNTITLED


def topic(title: str, day: date) -> str:
    """The page an event goes on: its title, a comma and its start date. The title is cut so
    the page's slug, which the compiler cuts at 64 characters, keeps the date."""
    from cairn.compiler import slugify  # the compiler imports the store, which imports this

    cut = _cut(title, lambda t: len(slugify(t)) > TOPIC_SLUG_ROOM, TITLE_MAX)
    return f"{cut or UNTITLED}, {day.isoformat()}"


def _start_day(event: Event) -> date:
    return event.start.date() if isinstance(event.start, datetime) else event.start


def _when_line(event: Event) -> str:
    start, end = event.start, event.end
    if isinstance(start, datetime) and isinstance(end, datetime):
        return f"- When: {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC"
    assert isinstance(start, date) and isinstance(end, date)
    last = max(end - timedelta(days=1), start)
    days = start.isoformat() if last == start else f"{start.isoformat()} to {last.isoformat()}"
    return f"- When: {days}, all day"


def _attendees(item: Mapping[str, Any]) -> str | None:
    given = item.get("attendees")
    if not isinstance(given, list):
        return None
    people = [a for a in given if isinstance(a, dict) and a.get("resource") is not True]
    named = [n for n in (_name(a.get("displayName")) for a in people) if n]
    unnamed = len(people) - len(named)
    if not named:
        return f"{unnamed} without a name" if unnamed else None
    return ", ".join(named) + (f" and {unnamed} more" if unnamed else "")


def _meet_code(item: Mapping[str, Any]) -> str | None:
    conference = item.get("conferenceData")
    if not isinstance(conference, dict):
        return None
    solution = conference.get("conferenceSolution")
    key = solution.get("key") if isinstance(solution, dict) else None
    if not isinstance(key, dict) or key.get("type") != "hangoutsMeet":
        return None
    code = conference.get("conferenceId")
    if not isinstance(code, str) or not code or len(code) > CODE_MAX:
        return None
    if names.holds_an_address(code) or any(ch.isspace() for ch in code):
        return None
    return code


def text(event: Event, title: str | None = None) -> str:
    """The note's Markdown: a heading with the title (`title`, made from the summary when not
    given), the facts, then the description."""
    item = event.item
    facts = [_when_line(event)]
    where = _without_addresses(item.get("location"))
    if where:
        facts.append(f"- Where: {where}")
    organizer = item.get("organizer")
    named = _name(organizer.get("displayName")) if isinstance(organizer, dict) else None
    if named:
        facts.append(f"- Organiser: {named}")
    attendees = _attendees(item)
    if attendees:
        facts.append(f"- Attendees: {attendees}")
    code = _meet_code(item)
    if code:
        facts.append(f"- Meet: {code}")
    lines = [f"# {title or title_of(item.get('summary'))}", "", *facts]
    description = item.get("description")
    said = html_to_text(description) if isinstance(description, str) else ""
    if said:
        size = sum(len(line) + 1 for line in lines) + 1
        room = fit.chars() - size - len(DESCRIPTION_CUT) - 2
        kept, _, was_cut = fit.cut(said, max(1, fit.claims() - len(facts) - 1), max(1, room))
        lines += ["", kept, *(["", DESCRIPTION_CUT] if was_cut else [])]
    return fit.fitted("\n".join(lines) + "\n", NOTE_CUT)


def draft(event: Event, project: str) -> NoteDraft:
    """The event's draft, on its own page."""
    title = title_of(event.item.get("summary"))
    return NoteDraft(
        project=project,
        title=title,
        text=text(event, title),
        source_ref=f"{ORIGIN}{event.eid}",
        label=LABEL,
        topic=topic(title, _start_day(event)),
    )


# Wording of a refusal


def refusal(reply: Reply, variable: str) -> str:
    """A SourceError's message for an answer to events.list that is not 200, from its status
    and reasons alone."""
    status = reply.status
    if reply.limited:
        return (
            f"{SERVICE} still answered with a rate limit to {WHAT} after {RETRIES} retries:"
            " the next run goes on"
        )
    if status == 403 and "quotaExceeded" in reply.reasons:
        return (
            f"{SERVICE} answered that a calendar usage limit was reached (quotaExceeded):"
            " waiting a few seconds does not lift it, so it is not retried; the next run tries"
            " again"
        )
    if status in (401, 403):
        return (
            f"{SERVICE} refused access (HTTP {status}) to {WHAT}: check that the credential in"
            f" {variable} carries the scope {SCOPE} (for a service account, in its domain-wide"
            " delegation), that the Google Calendar API is enabled in its Cloud project, and"
            " that the account it reads as may read calendar_id"
        )
    if status == 404:
        return (
            f"{SERVICE} found no calendar for calendar_id (HTTP 404): check that it names a"
            " calendar, and that the calendar is shared with the account that reads it"
        )
    if status == 410:
        return (
            f"{SERVICE} answered 410 (gone) to {WHAT}, which this server does not expect without a"
            " sync token: the next run tries again"
        )
    if 300 <= status < 400:
        return (
            f"{SERVICE} answered {WHAT} with a redirect (HTTP {status}), which this server does not"
            " follow: check base_url"
        )
    if status >= 500:
        return (
            f"{SERVICE} answered HTTP {status} to {WHAT}, a fault on its side: the next run"
            " tries again"
        )
    if 400 <= status < 500:
        return f"{SERVICE} refused the request to {WHAT} (HTTP {status})"
    return f"{SERVICE} answered HTTP {status} to {WHAT}"


DESCRIPTION_CUT = "The rest of the description is left out: it is longer than a page holds."
NOTE_CUT = "The rest of this note is left out: it is longer than a page holds."
UNREADABLE = (
    "unreadable: this server failed on what Google Calendar gave for this event and could not make"
    " a note of it; it is not read again"
)
FREE_BUSY = (
    "calendar_id names a calendar the account that reads it sees as free/busy only, which hides"
    " each event's title and details: share the calendar with that account so that its event"
    " details are visible, and the next run reads it"
)


# The source


@dataclass
class _Listed:
    """What the pages of one listing held: the events by id, the zone, and whether the
    listing was cut short by the pull's bounds."""

    items: dict[str, Any] = field(default_factory=dict)
    zone: tzinfo | None = None
    pages: int = 0
    cut: str | None = None


class CalendarSource:
    """Reads the ended events of one calendar (see the module docstring)."""

    def __init__(
        self,
        name: str,
        config: Mapping[str, Any],
        credential: Credential,
        *,
        http: httpx.AsyncClient,
        clock: Clock | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        deadline: datetime | None = None,
        since: datetime | None = None,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.name = name
        self.base_url = config["base_url"]
        self.variable = config["credentials_env"]
        self.subject = config.get("subject")
        self.calendar_id = config["calendar_id"]
        self.project = config["projects"][0] if config["projects"] else ""
        self._credential = credential
        self._token: str | None = None
        self._http = http
        self._clock = clock or _utc_now
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter
        self.deadline = deadline
        self.since = since

    def __repr__(self) -> str:
        return f"CalendarSource({self.base_url!r}, name={self.name!r})"

    @property
    def secrets(self) -> tuple[str, ...]:
        """The credential's secrets and, once the pull has it, the access token."""
        token = (self._token, f"Bearer {self._token}") if self._token else ()
        return (*self._credential.secrets, *token)

    async def pull(self, cursor: str | None) -> Pulled:
        now = self._clock().astimezone(UTC)
        since = (self.since or now).astimezone(UTC)
        start = (Cursor(mark=since) if cursor is None else Cursor.decode(cursor)).switched_on(since)
        if self.deadline is not None and now >= self.deadline:
            raise SourceError(
                "no time was left to sign in to Google within one pull's deadline, so the"
                " cursor cannot move: the next run tries again"
            )
        try:
            async with limit(self.deadline, self._clock):
                self._token = await access_token(
                    self._credential, scopes=SCOPES, subject=self.subject, http=self._http,
                    clock=self._clock,
                )  # fmt: skip
        except TimeoutError:
            raise SourceError(SIGN_IN_LATE) from None
        reader = Reader(
            self._http, token=self._token, clock=self._clock, sleep=self._sleep,
            deadline=self.deadline, budget=BUDGET, jitter=self._jitter,
        )  # fmt: skip
        lower = min(start.mark - OVERLAP, now - timedelta(seconds=1))
        listed = await self._list(reader, listing(lower, now))
        if listed.cut is not None and not listed.pages:
            raise SourceError(
                f"{SERVICE} could not list the calendar within one pull's bounds"
                f" ({listed.cut}), so the cursor cannot move: the next run tries again"
            )

        seen = dict(start.seen)
        due: list[Event] = []
        for item in listed.items.values():
            event = read_event(item, listed.zone)
            if event is None or passed_over(event):
                continue
            if event.ended > now or event.ended < since:
                continue  # not ended yet, or ended before it was switched on
            key = digest(event.eid)
            if key in seen:
                seen[key] = max(seen[key], _seconds(event.ended))
                continue
            due.append(event)
        due.sort(key=lambda event: (event.ended, event.eid))

        made: list[NoteDraft] = []
        skipped: list[Skip] = []
        for event in due:
            try:
                written = draft(event, self.project)
            except SourceError:
                raise
            except Exception:  # one event cairn fails on must not stop the source for good
                written = None
            seen[digest(event.eid)] = _seconds(event.ended)
            if written is None:
                skipped.append(Skip(event.eid, UNREADABLE))
                continue
            resume = replace(start, seen=dict(seen)).encode()
            made.append(replace(written, resume=resume))
        if listed.cut is not None:
            if not made and not skipped:
                raise SourceError(
                    f"{SERVICE} could not list the calendar within one pull's bounds"
                    f" ({listed.cut}), and the pages it listed held nothing new, so the cursor"
                    " cannot move: the next run tries again"
                )
            return Pulled(made, skipped, replace(start, seen=dict(seen)).encode())
        following = Cursor(mark=max(start.mark, now), seen=seen)
        following = following.pruned(following.mark - OVERLAP)
        return Pulled(made, skipped, following.encode())

    async def _list(self, reader: Reader, params: dict[str, str]) -> _Listed:
        """Every page of the listing, `params` beside each page's token, until the last page
        or the pull's bounds."""
        listed = _Listed()
        url = f"{self.base_url}/calendars/{quote(self.calendar_id, safe='')}/events"
        token: str | None = None
        while True:
            asked = {**params, **({"pageToken": token} if token else {})}
            try:
                reply = await reader.get(url, asked, service=SERVICE, what=WHAT)
            except Spent as spent:
                listed.cut = str(spent)
                return listed
            if reply.status != 200:
                raise SourceError(refusal(reply, self.variable))
            body = reply.body
            if not isinstance(body, dict):
                raise _shape()
            if body.get("accessRole") == "freeBusyReader":
                raise SourceError(FREE_BUSY)
            page, token = body.get("items", []), body.get("nextPageToken")
            if not isinstance(page, list) or not (token is None or isinstance(token, str)):
                raise _shape()
            if listed.zone is None:
                listed.zone = zone_of(body.get("timeZone"))
            listed.pages += 1
            for item in page:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise _shape("an event has no usable id")
                if len(listed.items) == MAX_EVENTS and item["id"] not in listed.items:
                    listed.cut = f"{MAX_EVENTS:,} events"  # a page may hold more than asked
                    return listed
                listed.items[item["id"]] = item
            if not token:
                return listed
