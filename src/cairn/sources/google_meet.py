"""The Google Meet source: transcripts of a person's meetings, read from the Meet REST API v2,
a page a meeting.

Contract (frozen by tests/acceptance/test_c4_google_meet.py; the API's shapes are recorded in
docs/google-meet-api.md and docs/google-auth.md, with what Google's pages do not state marked
as such and what cairn decided marked **[cairn]**). cairn never writes to Google. It signs in
through cairn.sources.google_auth and calls the API through cairn.sources.google_api.

The kind ("google_meet" in cairn.sources.KINDS)
- It is withheld (SourceKind.withheld): a page that cites one of its notes is not sent to a
  model unless the operator allows it (cairn.arrange), since Google's rules for its users'
  data restrict passing it on, and whether the operator's model may read meeting transcripts
  is theirs to decide.
- It starts off: added off, read only once an owner or admin switches it on with a project, and
  then only meetings that ended at or after that switch-on (`since`). Its notes are written
  via "google-meet", with the label "google-meet". It proposes the project "meet-transcripts"
  (DEFAULT_PROJECT). It asks for one scope, SCOPE.
- The config: `credentials_env` (required: the name of the environment variable that holds
  the credential, capitals, digits and "_"), `subject` (one email address: the person whose
  meetings a service account reads; required with a service account and refused with a
  refresh token, which `open` checks), `projects` (at most one project name; none while it is
  off), `base_url` (DEFAULT_BASE_URL unless given; https, or http on a loopback host; no
  user, password, query or fragment; a trailing "/" dropped), `wait_hours` (48, 1 to
  168) and `meeting_code` (none unless given: the code of one meeting space, groups of
  letters joined by "-", or the ten letters of abc-mnop-xyz without dashes, in either case;
  stored lower case with its dashes; anything else is refused with CODE_REFUSED). No other
  setting. A source made from an account must name `meeting_code` (cairn.sources.CHOOSING).
  A refusal names the setting at fault and never repeats a value: a
  value that looks like a credential (JSON, a PEM key, one of Google's token or secret
  prefixes, or a pattern of the "secrets" guard) is refused as such.
- `open` loads the credential (google_auth.load_credential: a SourceError naming the
  variable) and checks `subject` against its kind. Its `secrets` are the credential's, and
  the access token (and its Bearer header) once the pull has it.
- `describe` says whose meetings it reads: the `subject`, an address the operator typed,
  which owners and admins see on the Sources page; never the variable or a credential.

Finding the meetings
- Each pull asks for one access token, then lists conference records with exactly the
  filter `start_time>="<RFC 3339, milliseconds, Z>"`, OVERLAP (a day) before the cursor's
  mark, the same filter beside every pageToken. **[cairn]** The mark is the clock at the
  start of the last pull that finished (the first pull's is `since`; switched on again, the
  later of the two), so a meeting of up to a day is listed once it has ended even where
  Google lists only finished meetings. A meeting longer than that may be missed. With
  `meeting_code`, the filter is that bound, " AND ", then CODE_CONDITION with the code:
  `start_time>="..." AND space.meeting_code = "abc-mnop-xyz"`, so only that space's records
  are listed. **[cairn]** A 400 to that listing fails the pull with FILTER_REFUSED: nothing is
  read, and the listing is never asked again without the code. **[cairn]** The read does not
  rely on the filter: before listing, each pull asks `spaces.get` once for `spaces/<code>`
  and keeps the space's `name`; a listed record whose `space` is not that name is not looked
  at (no request about it), and the pull's `stopped` says how many were left unread
  (unasked). A 404 or 403 to that `spaces.get` fails the pull with no_space, which names the
  code. The chosen meeting's title takes the code without asking for its space again.
- What is looked at, sorted by end time, then by name, whatever order Google used: every
  listed meeting that has ended, at or after `since`, and the meetings the cursor waits on
  that the listing no longer reaches. A meeting with no `endTime` is going on: it is looked
  at again by a later pull. A meeting whose digest is settled is not asked about.
- Looking at a meeting: list its transcripts (every page). A transcript already settled is
  passed over. One in FILE_GENERATED is read: every page of its entries, then, once a
  meeting and a pull, the meeting's participants (every page), the record itself when the
  listing did not give it (for its start and space), and its space (`spaces.get` on the
  record's `space`, for `meetingCode`). **[cairn]** A 404 from `spaces.get`, or a space
  without a code of the documented form, leaves the title without one. A transcript in any
  other state (STARTED, ENDED, or a state the notes do not list) is waited for.
- After a look, the meeting waits until `wait_hours` after it ended, and is looked at by
  every pull until then: Google may list a transcript only once its file is generated. Once
  they have passed, it is dropped without a request; when its last look found a transcript
  not yet generated, that is reported once (NOT_GENERATED). **[cairn]** A meeting first seen
  after its wait has passed (sync was down) is looked at once, then dropped the same way.
- A meeting that answers 404 (to its transcripts, its participants or its record) is
  skipped once, GONE_MEETING, and settled; a transcript whose entries answer 404 is skipped
  once, GONE_TRANSCRIPT; one with no entries, NO_ENTRIES; one longer than MAX_PAGES pages,
  TOO_LONG; one cairn fails on while it makes its drafts, UNREADABLE (one transcript never
  stops the source). Each skip reason is a fixed sentence without an id: the runner counts
  them. **[cairn]** Every list (records, transcripts, entries, participants) is read for at
  most MAX_PAGES (500) pages and, since Google chooses how many items a page holds, at most
  as many items as full pages hold (500 x its page size: 50,000 records, transcripts or
  entries, 125,000 participants); past either, a transcript's entries are TOO_LONG and any
  other list fails the pull with a SourceError.

The cursor (opaque JSON this module owns, version 1; never a token, a key, a name, an
address, a transcript's id or any text)
- `mark`: the moment the listing's lower bound is OVERLAP before.
- `waiting`: the meetings looked at whose wait has not passed, by the id of their record:
  their end in Unix microseconds (so `since` is compared at full precision) and 1 when the
  last look found a transcript not yet generated, else 0.
- `settled`: the first 16 hex digits of the SHA-256 of the resource name of each transcript
  written, skipped or empty and of each meeting dropped or gone, with its meeting's end in
  whole Unix seconds. **[cairn]** Kept for RETENTION (30 days) after the end, while Google
  still keeps the record, so a settled meeting is never asked about again, then let go: the
  cursor holds about a month of meetings however many weeks it runs.
- `parts`: a transcript half written (a run stopped between two of its parts), by digest:
  the index of the next part and the end. It goes on from there when next read.
- Each draft carries `resume`: the cursor the pull started from, with what this pull settled
  up to and including that draft, so a run that stops there is picked up at the next draft.
  At its deadline or its BUDGET of requests (**[cairn]** 2,000: a pull stays near the
  quota's 600 reads a minute), a pull returns what it finished with such a cursor: the mark
  does not move. A pull that runs out before it has listed the meetings, or before it has
  finished one, raises a SourceError instead.

When Google says no (a message never holds a token, a body or an address)
- 401 or 403 that is not a rate limit: a SourceError saying access was refused and what to
  check (the scope, the delegation, that the API is enabled, that the person may use Meet),
  not retried. 429, or a 403 whose reason is a rate limit: waited out (google_api: 2^n
  seconds and up to one more, n from 0) at most 4 times, then a SourceError. 404 on the
  listing, 400, a redirect (never followed), 5xx, a network error (by its class), or an
  answer not of the documented shape: a SourceError. A failed pull returns nothing, so no
  cursor moves.

What it writes, for each generated transcript, into the config's project
- `title` and `topic`: "Meet <meeting code>, YYYY-MM-DD HH:MM UTC" (the meeting's start),
  or "Meet, YYYY-MM-DD HH:MM UTC" without a code: the Meet API has no meeting title. Every
  transcript of a meeting goes on its page.
- `text`: "# <title>", a blank line, the facts ("- Started: ", "- Ended: ", "- Transcribed: "
  in UTC, and "- Participants: " every participant by display name, signed-in ones first,
  then anonymous and by phone, each by name, and how many have no name), a blank line, then
  a line a turn: "- HH:MM:SS <speaker>: <what was said>". A turn is the consecutive entries
  of one participant, joined; its time counts from the meeting's start. A participant with
  no display name, or one not in the list, is UNKNOWN. **[cairn]** A speaker who did not
  sign in is marked as in the facts ("<name> (anonymous)", "<name> (by phone)"): a guest
  types the name it is shown by, so a guest who typed a signed-in participant's name is not
  written as that person. **[cairn]** A display name holding
  "@" (a guest who typed an address) counts as no name, so no address is written; the check
  is cairn.sources.names's, so an at sign of another width (U+FF20, U+FE6B) counts too. Names and
  text are put on one line.
- At most TURNS_PER_PART turns a draft: later parts have the topic "<title>, part N", the
  heading "# <title>, part N" and only turns. Each part also fits a page by both of its
  bounds (K4: cairn.sources.fit.packed, a page's bounds read when the drafts are made, the
  facts counted in the first): a part ends where its next turn would pass them, and a turn
  longer than a page holds goes on, with the same time and speaker, in the next part, so
  the parts hold the whole transcript. A part still past a page's bounds (a list of
  participants no meeting has) is cut at its end with NOTE_CUT. `source_ref`:
  "meet:<record id>/<transcript id>", "/N" from the second part. `label`: "google-meet".
"""

import asyncio
import json
import math
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from cairn.sources import Clock, NoteDraft, Pulled, Skip, SourceError, SourceKind, fit, names
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
    moment,
)
from cairn.sources.google_api import looks_like_a_credential as _looks_like_a_credential
from cairn.sources.google_api import stamp as _stamp
from cairn.sources.google_auth import (
    AuthorizedUser,
    Credential,
    ServiceAccount,
    access_token,
    load_credential,
)
from cairn.sources.timing import limit
from cairn.store.memory import is_project_name

DEFAULT_BASE_URL = "https://meet.googleapis.com/v2"
SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"
SCOPES = [SCOPE]
SERVICE = "Google Meet"
VIA = LABEL = "google-meet"
ORIGIN = "meet:"
DEFAULT_PROJECT = "meet-transcripts"
UNKNOWN = "Unknown speaker"
SETTINGS = ("credentials_env", "subject", "projects", "base_url", "wait_hours", "meeting_code")
WAIT_HOURS = (48, 1, 168)  # the default, the least, the most
OVERLAP = timedelta(days=1)
RETENTION = timedelta(days=30)
TURNS_PER_PART = 1_500
NOTE_CUT = "The rest of this note is left out: it is longer than a page holds."
RECORDS_PAGE = TRANSCRIPTS_PAGE = ENTRIES_PAGE = 100  # Google's largest
PARTICIPANTS_PAGE = 250
MAX_PAGES = 500  # of one list: 50,000 entries of a transcript
BUDGET = 2_000  # requests of one pull
CURSOR_VERSION = 1
DIGEST = re.compile(r"[0-9a-f]{16}")
ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
CODE = re.compile(r"[a-z]+-[a-z]+-[a-z]+")
CODE_MAX = 40  # a longer code would crowd the page's slug
UNDASHED = re.compile(r"([a-z]{3})([a-z]{4})([a-z]{3})")  # abcmnopxyz, as abc-mnop-xyz
# **[cairn]** The condition that lists one meeting space's records, in the spelling of the
# reference's example (docs/google-meet-api.md, B: snake_case, the code quoted), joined to the
# time bound with AND. Not checked against the real service: should it want another spelling,
# this is the one place to change.
CODE_CONDITION = 'space.meeting_code = "{}"'
CODE_REFUSED = (
    "meeting_code is the code of one meeting, from its address: three groups of letters, such"
    " as abc-mnop-xyz (abcmnopxyz is taken too)"
)
SPACE = re.compile(r"spaces/[A-Za-z0-9_-]{1,128}")  # a space's resource name


def unasked(count: int) -> str:
    """The pull's sentence (Pulled.stopped) when the listing gave `count` meetings of other
    spaces than its meeting_code's: they were left unread."""
    meetings = "meeting" if count == 1 else "meetings"
    return (
        f"{SERVICE} returned {count} {meetings} this source did not ask for, of other meetings"
        f" than its meeting_code: they were left unread, so {SERVICE} ignored the filter"
    )


def no_space(code: str, status: int) -> str:
    """The SourceError's sentence when spaces.get finds no space for the chosen code."""
    said = (
        f"{SERVICE} has no meeting {code} that this account can reach (HTTP {status} to"
        " spaces.get), so nothing was read: check meeting_code"
    )
    return said + (f", and that the credential carries the scope {SCOPE}" if status == 403 else "")


FILTER_REFUSED = (
    f"{SERVICE} refused the filter for one meeting (HTTP 400 to conferenceRecords.list), so"
    " nothing was read and no other meeting is read in its place: check meeting_code"
)

GONE_MEETING = (
    "gone: Google Meet no longer has this meeting (deleted, or past its 30 days, since it was"
    " listed); it is not asked about again"
)
GONE_TRANSCRIPT = (
    "gone: Google Meet no longer has this transcript's entries (deleted, or past their 30"
    " days, since it was listed); it is not read again"
)
NO_ENTRIES = (
    "no entries: Google Meet gave this transcript no entries (it may have deleted them); it is"
    " not read again"
)
NOT_GENERATED = (
    "no transcript was generated: a transcript of this meeting was started, but its file was"
    " not generated within wait_hours of the meeting's end; one generated later is not read"
)
UNREADABLE = (
    "unreadable: this server failed on what Google Meet gave for this transcript and could not make"
    " a note of it; it is not read again"
)
TOO_LONG = (
    f"too long: this transcript has more than {MAX_PAGES * ENTRIES_PAGE:,} entries, more than"
    " this server reads; it is not read"
)


# The config


def check_config(config: object) -> dict[str, Any]:
    """The config as stored, with the defaults filled in, or ValueError naming the setting at
    fault and never a value."""
    if not isinstance(config, Mapping):
        raise ValueError("a google_meet source's config is an object of settings")
    for setting in config:
        if setting in SETTINGS:
            continue
        if (
            isinstance(setting, str)
            and SETTING_NAME.fullmatch(setting)
            and not _looks_like_a_credential(setting)
        ):
            raise ValueError(
                f"a google_meet source has no setting {setting!r}: it takes {', '.join(SETTINGS)}"
            )
        raise ValueError(
            "a google_meet source does not take one of the settings given: it takes"
            f" {', '.join(SETTINGS)}"
        )
    variable = config.get("credentials_env")
    if variable is None:
        raise ValueError(
            "a google_meet source needs credentials_env: the name of the environment variable"
            " that holds the Google credential"
        )
    if isinstance(variable, str) and _looks_like_a_credential(variable):
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
    checked: dict[str, Any] = {"credentials_env": variable}
    subject = config.get("subject")
    if subject is not None:
        if isinstance(subject, str) and _looks_like_a_credential(subject):
            raise ValueError("subject holds what looks like a credential, not an email address")
        if not isinstance(subject, str) or len(subject) > 254 or not EMAIL.fullmatch(subject):
            raise ValueError(
                "subject is one email address: the person whose meetings a service account reads"
            )
        checked["subject"] = subject
    checked["projects"] = _check_projects(config)
    checked["base_url"] = _check_base_url(config.get("base_url", DEFAULT_BASE_URL))
    default, least, most = WAIT_HOURS
    hours = config.get("wait_hours", default)
    if isinstance(hours, bool) or not isinstance(hours, int) or not least <= hours <= most:
        raise ValueError(f"wait_hours is a whole number from {least} to {most}")
    checked["wait_hours"] = hours
    if "meeting_code" in config:
        checked["meeting_code"] = _check_code(config["meeting_code"])
    return checked


def _check_code(value: object) -> str:
    """A meeting's code in the documented form (lower case, groups of letters joined by "-"),
    from that form or the ten letters of abc-mnop-xyz without dashes, in either case, with
    or without spaces around it."""
    if isinstance(value, str) and _looks_like_a_credential(value):
        raise ValueError("meeting_code holds what looks like a credential, not a meeting's code")
    if isinstance(value, str) and value.isascii():
        code = value.strip().lower()
        undashed = UNDASHED.fullmatch(code)
        if undashed is not None:
            code = "-".join(undashed.groups())
        if len(code) <= CODE_MAX and CODE.fullmatch(code):
            return code
    raise ValueError(CODE_REFUSED)


def _check_projects(config: Mapping[str, Any]) -> list[str]:
    projects = config.get("projects", [])
    if not isinstance(projects, list | tuple):
        raise ValueError("projects is a list of at most one project's name")
    if len(projects) > 1:
        raise ValueError("projects names at most one project: a google_meet source writes into one")
    for project in projects:
        if isinstance(project, str) and _looks_like_a_credential(project):
            raise ValueError("projects holds a value that looks like a credential")
        if not isinstance(project, str) or not is_project_name(project):
            raise ValueError("projects holds a project's name (not its id)")
    return list(projects)


def _check_base_url(value: object) -> str:
    return check_base_url(value, "the Google Meet REST API")


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
) -> "MeetSource":
    """The source a stored config describes, with its credential read from `env`.
    SourceError names a variable or a setting, never a value."""
    credential = load_credential(config["credentials_env"], env)
    subject = config.get("subject")
    if isinstance(credential, ServiceAccount) and subject is None:
        raise SourceError(
            f"source {name!r} reads with a service account, which needs subject in its config:"
            " the address of the person whose meetings it reads (domain-wide delegation)"
        )
    if isinstance(credential, AuthorizedUser) and subject is not None:
        raise SourceError(
            f"source {name!r} reads with a refresh token, which reads the meetings of the"
            " person who authorised it: remove subject from its config"
        )
    return MeetSource(
        name, config, credential, http=http, clock=clock, sleep=sleep, deadline=deadline,
        since=since, jitter=jitter,
    )  # fmt: skip


def describe(config: Mapping[str, Any]) -> str:
    """What a source reads, for GET /sources: whose meetings, from where, and how long it
    looks for a transcript."""
    whose = config.get("subject") or "the person who authorised its credential"
    hours = config.get("wait_hours", WAIT_HOURS[0])
    code = config.get("meeting_code")
    which = f"the meeting {code}, as Google lists it" if code else "the meetings Google lists"
    return (
        f"Google Meet transcripts of {which} for {whose}, at"
        f" {config['base_url']}; each is read once its file is generated, looked for until"
        f" {hours} hour{'s' if hours != 1 else ''} after the meeting ends"
    )


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


# Times, filters and digests


def _utc_now() -> datetime:
    return datetime.now(UTC)


def record_filter(lower: datetime, code: str | None = None) -> str:
    """conferenceRecords.list's filter: the documented form, a start-time lower bound, and,
    for a source of one meeting, AND the condition on its space's code (CODE_CONDITION)."""
    bound = f'start_time>="{_stamp(lower)}"'
    return bound if code is None else f"{bound} AND {CODE_CONDITION.format(code)}"


def _seconds(moment_: datetime) -> int:
    return math.floor(moment_.timestamp())


def _micros(moment_: datetime) -> int:
    delta = moment_.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta // timedelta(microseconds=1)


def _from_micros(value: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)


# The cursor


@dataclass(frozen=True)
class Waiting:
    """A meeting looked at whose wait has not passed: when it ended, and whether its last
    look found a transcript not yet generated."""

    end: datetime
    unfinished: bool


@dataclass(frozen=True)
class Cursor:
    """What a pull starts from (see the module docstring)."""

    mark: datetime
    waiting: dict[str, Waiting] = field(default_factory=dict)
    settled: dict[str, int] = field(default_factory=dict)
    parts: dict[str, tuple[int, int]] = field(default_factory=dict)

    @classmethod
    def first(cls, since: datetime) -> "Cursor":
        return cls(mark=since)

    def switched_on(self, since: datetime) -> "Cursor":
        """The cursor for a source last switched on at `since`: the mark is at least `since`,
        and a meeting that ended before it is not waited for."""
        return replace(
            self,
            mark=max(self.mark, since),
            waiting={c: w for c, w in self.waiting.items() if w.end >= since},
        )

    def pruned(self, now: datetime) -> "Cursor":
        """What Google no longer keeps (RETENTION after its meeting ended) let go."""
        least = _seconds(now - RETENTION)
        return replace(
            self,
            settled={d: e for d, e in self.settled.items() if e >= least},
            parts={d: p for d, p in self.parts.items() if p[1] >= least},
        )

    def encode(self) -> str:
        return json.dumps(
            {
                "v": CURSOR_VERSION,
                "mark": _stamp(self.mark)[:-1] + f"{self.mark.microsecond % 1000:03d}Z",
                "waiting": {
                    c: [_micros(w.end), int(w.unfinished)] for c, w in sorted(self.waiting.items())
                },
                "settled": {d: self.settled[d] for d in sorted(self.settled)},
                "parts": {d: list(self.parts[d]) for d in sorted(self.parts)},
            },
            separators=(",", ":"),
        )

    @classmethod
    def decode(cls, text: str) -> "Cursor":
        broken = SourceError(
            "the stored cursor is not one this Google Meet source wrote: remove the source and"
            " add it again to start afresh"
        )
        try:
            state = json.loads(text)
        except (ValueError, RecursionError):
            raise broken from None
        if not isinstance(state, dict) or state.get("v") != CURSOR_VERSION:
            raise broken
        mark = moment(state.get("mark"))
        waiting, settled, parts = state.get("waiting"), state.get("settled"), state.get("parts")
        if mark is None or not all(isinstance(x, dict) for x in (waiting, settled, parts)):
            raise broken
        if not all(
            ID.fullmatch(c)
            and isinstance(w, list)
            and len(w) == 2
            and _count(w[0])
            and w[1] in (0, 1)
            and not isinstance(w[1], bool)
            for c, w in waiting.items()
        ):
            raise broken
        if not all(DIGEST.fullmatch(d) and _count(e) for d, e in settled.items()):
            raise broken
        if not all(
            DIGEST.fullmatch(d) and isinstance(p, list) and len(p) == 2 and all(map(_count, p))
            for d, p in parts.items()
        ):
            raise broken
        return cls(
            mark=mark,
            waiting={c: Waiting(_from_micros(w[0]), bool(w[1])) for c, w in waiting.items()},
            settled=dict(settled),
            parts={d: (p[0], p[1]) for d, p in parts.items()},
        )


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# What the API answers


@dataclass(frozen=True)
class Meeting:
    """A conference record: its id, start, end (None: going on) and space."""

    cid: str
    start: datetime | None  # None: not listed this pull (a meeting the cursor waits on)
    end: datetime | None
    space: str | None

    @property
    def name(self) -> str:
        return f"conferenceRecords/{self.cid}"


@dataclass(frozen=True)
class Transcript:
    name: str
    state: str
    start: datetime | None
    end: datetime | None

    @property
    def tid(self) -> str:
        return self.name.rsplit("/", 1)[1]


@dataclass(frozen=True)
class Person:
    """A participant: its resource name, display name (None: none) and kind."""

    name: str
    display: str | None
    kind: str  # "signedinUser", "anonymousUser" or "phoneUser"


@dataclass(frozen=True)
class Entry:
    participant: str
    text: str
    start: datetime


def _shape(what: str) -> SourceError:
    return SourceError(f"{SERVICE}'s answer to {what} is not the documented shape")


def _record(item: object, what: str) -> Meeting:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise _shape(what)
    found = re.fullmatch(r"conferenceRecords/([A-Za-z0-9_-]{1,128})", item["name"])
    start = moment(item.get("startTime"))
    end = moment(item.get("endTime")) if item.get("endTime") is not None else None
    if found is None or start is None or (item.get("endTime") is not None and end is None):
        raise _shape(what)
    space = item.get("space")
    if not (isinstance(space, str) and re.fullmatch(r"spaces/[A-Za-z0-9_-]{1,128}", space)):
        space = None
    return Meeting(found.group(1), start, end, space)


def _transcript(item: object, meeting: Meeting) -> Transcript:
    what = "transcripts.list"
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise _shape(what)
    pattern = re.escape(meeting.name) + r"/transcripts/[A-Za-z0-9_-]{1,128}"
    if not re.fullmatch(pattern, item["name"]) or not isinstance(item.get("state"), str):
        raise _shape(what)
    return Transcript(
        item["name"], item["state"], moment(item.get("startTime")), moment(item.get("endTime"))
    )


def _entry(item: object) -> Entry:
    what = "transcripts.entries.list"
    if not isinstance(item, dict):
        raise _shape(what)
    participant, text, start = (
        item.get("participant"),
        item.get("text"),
        moment(item.get("startTime")),
    )
    if not isinstance(participant, str) or not isinstance(text, str) or start is None:
        raise _shape(what)
    return Entry(participant, text, start)


def _person(item: object) -> Person:
    what = "participants.list"
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise _shape(what)
    if not re.fullmatch(
        r"conferenceRecords/[A-Za-z0-9_-]{1,128}/participants/[^/\s]{1,128}", item["name"]
    ):
        raise _shape(what)
    for kind in ("signedinUser", "anonymousUser", "phoneUser"):
        user = item.get(kind)
        if isinstance(user, dict):
            return Person(item["name"], _name(user.get("displayName")), kind)
    return Person(item["name"], None, "")


# What a draft says


def _one_line(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _name(value: object) -> str | None:
    """A display name on one line; None when there is none, or when it holds an address."""
    return names.shown(value)


def title(code: str | None, start: datetime) -> str:
    """The meeting's title and page: its code when it has one of the documented form, and
    its start in UTC."""
    when = f"{start.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
    if code is not None and len(code) <= CODE_MAX and CODE.fullmatch(code):
        return f"Meet {code}, {when}"
    return f"Meet, {when}"


@dataclass(frozen=True)
class Turn:
    speaker: str
    start: datetime
    text: str


def turns(entries: list[Entry], people: Mapping[str, Person]) -> list[Turn]:
    """The entries, by start, as turns: consecutive entries of one participant joined."""
    runs: list[tuple[str, datetime, list[str]]] = []  # joined once, at the end
    last: str | None = None
    for entry in sorted(entries, key=lambda e: e.start):
        said = _one_line(entry.text)
        if runs and entry.participant == last:
            runs[-1][2].append(said)
            continue
        person = people.get(entry.participant)
        shown = _shown(person)
        speaker = f"{shown}{_KIND_SAID.get(person.kind, '')}" if person and shown else UNKNOWN
        runs.append((speaker, entry.start, [said]))
        last = entry.participant
    return [Turn(who, start, " ".join(p for p in pieces if p)) for who, start, pieces in runs]


def offset(moment_: datetime, zero: datetime) -> str:
    """`moment_` as HH:MM:SS since `zero`, with a minus sign before it."""
    seconds = math.floor((moment_ - zero).total_seconds())
    sign, seconds = ("-" if seconds < 0 else ""), abs(seconds)
    return f"{sign}{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


_KIND_RANK = {"signedinUser": 0, "anonymousUser": 1, "phoneUser": 2}
_KIND_SAID = {"anonymousUser": " (anonymous)", "phoneUser": " (by phone)"}


def _shown(person: Person | None) -> str | None:
    """The name a participant is written by: None when there is none or it holds "@"."""
    return _name(person.display) if person is not None else None


def _participants(people: list[Person]) -> str:
    named = sorted(
        (p for p in people if _shown(p)),
        key=lambda p: (_KIND_RANK.get(p.kind, 3), (_shown(p) or "").casefold(), p.name),
    )
    shown = [f"{_shown(p)}{_KIND_SAID.get(p.kind, '')}" for p in named]
    unnamed = len(people) - len(named)
    if unnamed:
        shown.append(f"{'and ' if shown else ''}{unnamed} without a name")
    return ", ".join(shown)


def _span(start: datetime, end: datetime) -> str:
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    later = f"{end:%H:%M}" if end.date() == start.date() else f"{end:%Y-%m-%d %H:%M}"
    return f"{start:%Y-%m-%d %H:%M} to {later} UTC"


def drafts(
    meeting: Meeting,
    transcript: Transcript,
    entries: list[Entry],
    people: list[Person],
    code: str | None,
    project: str,
) -> list[NoteDraft]:
    """A generated transcript's drafts: the first with the facts, at most TURNS_PER_PART
    turns each. The meeting's start must be known."""
    assert meeting.start is not None and meeting.end is not None
    named = title(code, meeting.start)
    lines = [
        (f"- {offset(t.start, meeting.start)} {t.speaker}: ", t.text)
        for t in turns(entries, {p.name: p for p in people})
    ]
    facts = [
        f"- Started: {meeting.start:%Y-%m-%d %H:%M} UTC",
        f"- Ended: {meeting.end.astimezone(UTC):%Y-%m-%d %H:%M} UTC",
    ]
    if transcript.start is not None and transcript.end is not None:
        facts.append(f"- Transcribed: {_span(transcript.start, transcript.end)}")
    if people:
        facts.append(f"- Participants: {_participants(people)}")
    ref = f"{ORIGIN}{meeting.cid}/{transcript.tid}"
    made = []
    turns_most, chars = min(TURNS_PER_PART, fit.claims()), fit.chars()  # K4: read now
    first = (min(TURNS_PER_PART, fit.claims() - len(facts)), chars - sum(map(len, facts)))
    for index, part in enumerate(fit.packed(lines, first, (turns_most, chars))):
        number = index + 1
        topic = named if number == 1 else f"{named}, part {number}"
        head = [f"# {topic}", ""] + ([*facts, ""] if number == 1 else [])
        text = fit.fitted("\n".join([*head, *part]) + "\n", NOTE_CUT)
        made.append(
            NoteDraft(
                project=project,
                title=topic,
                text=text,
                source_ref=ref + (f"/{number}" if number > 1 else ""),
                label=LABEL,
                topic=topic,
            )
        )
    return made


# Wording of a refusal


def refusal(reply: Reply, what: str, variable: str) -> str:
    """A SourceError's message for an answer that is not 200, from its status alone."""
    status = reply.status
    if reply.limited:
        return (
            f"{SERVICE} still answered with a rate limit to {what} after {RETRIES} retries:"
            " the next run goes on"
        )
    if status in (401, 403):
        return (
            f"{SERVICE} refused access (HTTP {status}) to {what}: check that the credential in"
            f" {variable} carries the scope {SCOPE} (for a service account, in its domain-wide"
            " delegation), that the Google Meet REST API is enabled in its Cloud project, and"
            " that the person it reads for may use Meet"
        )
    if 300 <= status < 400:
        return (
            f"{SERVICE} answered {what} with a redirect (HTTP {status}), which this server does not"
            " follow: check base_url"
        )
    if status == 404 and what == "conferenceRecords.list":
        return f"{SERVICE} answered 404 to {what}: check base_url (it ends in /v2)"
    if status >= 500:
        return (
            f"{SERVICE} answered HTTP {status} to {what}, a fault on its side: the next run"
            " tries again"
        )
    if 400 <= status < 500:
        return f"{SERVICE} refused the request to {what} (HTTP {status})"
    return f"{SERVICE} answered HTTP {status} to {what}"


# The source


class _Gone(Exception):
    """A meeting answered 404."""


class _TooLong(Exception):
    """A list ran past MAX_PAGES."""


@dataclass
class _Progress:
    """What a pull has done so far, and the cursor a run that stops after it stores."""

    start: Cursor
    state: Cursor
    drafts: list[NoteDraft] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    finished: int = 0  # meetings looked at to the end

    def resume(self) -> str:
        return replace(self.state, mark=self.start.mark).encode()

    def settle(self, key: str, end: datetime) -> None:
        self.state.settled[key] = _seconds(end)
        self.state.parts.pop(key, None)

    def skip(self, ref: str, reason: str, key: str, end: datetime) -> None:
        self.skipped.append(Skip(ref, reason))
        self.settle(key, end)

    def write(self, made: list[NoteDraft], key: str, end: datetime) -> None:
        first = self.state.parts.get(key, (0, 0))[0]
        for index in range(first, len(made)):
            if index == len(made) - 1:
                self.settle(key, end)
            else:
                self.state.parts[key] = (index + 1, _seconds(end))
            self.drafts.append(replace(made[index], resume=self.resume()))


class MeetSource:
    """Reads the transcripts of the meetings Google lists for one person (see the module
    docstring)."""

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
        self.project = config["projects"][0] if config["projects"] else ""
        self.wait = timedelta(hours=config["wait_hours"])
        self.code = config.get("meeting_code")
        self._space: str | None = None  # the chosen code's space, resolved once a pull
        self._credential = credential
        self._token: str | None = None
        self._http = http
        self._clock = clock or _utc_now
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter
        self.deadline = deadline
        self.since = since

    def __repr__(self) -> str:
        return f"MeetSource({self.base_url!r}, name={self.name!r})"

    @property
    def secrets(self) -> tuple[str, ...]:
        """The credential's secrets and, once the pull has it, the access token."""
        token = (self._token, f"Bearer {self._token}") if self._token else ()
        return (*self._credential.secrets, *token)

    async def pull(self, cursor: str | None) -> Pulled:
        now = self._clock().astimezone(UTC)
        since = (self.since or now).astimezone(UTC)
        start = (Cursor.first(since) if cursor is None else Cursor.decode(cursor)).switched_on(
            since
        )
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
        unasked_count = 0
        try:
            if self.code is not None:
                self._space = await self._resolve(reader, self.code)
            listed = await self._records(reader, start.mark - OVERLAP)
            if self.code is not None:  # only the chosen space's meetings, whatever was listed
                kept = [meeting for meeting in listed if meeting.space == self._space]
                unasked_count, listed = len(listed) - len(kept), kept
        except Spent as spent:
            raise SourceError(
                f"{SERVICE}'s meetings could not be listed within one pull's bounds ({spent}),"
                " so the cursor cannot move: the next run tries again"
            ) from None

        state = start.pruned(now)
        state = replace(
            state,
            waiting=dict(state.waiting),
            settled=dict(state.settled),
            parts=dict(state.parts),
        )
        progress = _Progress(start=start, state=state)
        said = unasked(unasked_count) if unasked_count else None
        meetings = {meeting.cid: meeting for meeting in listed}
        for cid, waiting in start.waiting.items():
            meetings.setdefault(cid, Meeting(cid, None, waiting.end, None))
        due: list[Meeting] = []
        for meeting in meetings.values():
            if meeting.end is None or meeting.end < since:
                continue  # going on, or ended before it was switched on
            key = digest(meeting.name)
            if key in state.settled:
                continue  # dropped or gone
            waiting = state.waiting.get(meeting.cid)
            if waiting is not None and now >= meeting.end + self.wait:
                self._drop(progress, meeting, waiting.unfinished)
                continue
            due.append(meeting)
        due.sort(key=lambda meeting: (meeting.end, meeting.name))

        for meeting in due:
            try:
                await self._look(reader, progress, meeting, now)
            except Spent as spent:
                if not progress.finished and not progress.drafts:
                    raise SourceError(
                        f"{SERVICE} did not finish one meeting within one pull's bounds"
                        f" ({spent}), so the cursor cannot move: the next run tries again"
                    ) from None
                return Pulled(progress.drafts, progress.skipped, progress.resume(), stopped=said)
            progress.finished += 1
        following = replace(progress.state, mark=max(start.mark, now))
        return Pulled(progress.drafts, progress.skipped, following.encode(), stopped=said)

    def _drop(self, progress: _Progress, meeting: Meeting, unfinished: bool) -> None:
        progress.state.waiting.pop(meeting.cid, None)
        assert meeting.end is not None
        key = digest(meeting.name)
        if unfinished:
            progress.skip(f"{ORIGIN}{meeting.cid}", NOT_GENERATED, key, meeting.end)
        else:
            progress.settle(key, meeting.end)

    async def _look(
        self, reader: Reader, progress: _Progress, meeting: Meeting, now: datetime
    ) -> None:
        """Look at one ended meeting: write what is generated, and wait for or drop it."""
        assert meeting.end is not None
        if self.code is not None and meeting.space is None:
            # Carried in the cursor from before this code was chosen: ask whose it is before
            # anything of it is read, and let go of one that is another meeting's.
            reply = await self._get(
                reader, f"/{meeting.name}", {}, "conferenceRecords.get", allow=(404,)
            )
            record = None if reply.status == 404 else _record(reply.body, "conferenceRecords.get")
            if record is None or record.space != self._space:
                progress.state.waiting.pop(meeting.cid, None)
                return
            if record.cid != meeting.cid or record.end is None:
                raise _shape("conferenceRecords.get")
            meeting = record
        try:
            unfinished = await self._transcripts(reader, progress, meeting)
        except _Gone:
            progress.state.waiting.pop(meeting.cid, None)
            progress.skip(f"{ORIGIN}{meeting.cid}", GONE_MEETING, digest(meeting.name), meeting.end)
            return
        if now >= meeting.end + self.wait:  # first seen after its wait: this look is its last
            self._drop(progress, meeting, unfinished)
        else:
            progress.state.waiting[meeting.cid] = Waiting(meeting.end, unfinished)

    async def _transcripts(self, reader: Reader, progress: _Progress, meeting: Meeting) -> bool:
        """Read the meeting's generated transcripts; whether one is not generated yet."""
        path = f"/{meeting.name}/transcripts"
        found = await self._list(reader, path, "transcripts", TRANSCRIPTS_PAGE, "transcripts.list")
        if found is None:
            raise _Gone
        listed = sorted(
            (_transcript(item, meeting) for item in found),
            key=lambda t: (t.start is None, t.start or meeting.end, t.name),
        )
        unfinished = False
        known: tuple[Meeting, list[Person], str | None] | None = None
        for transcript in listed:
            key = digest(transcript.name)
            if key in progress.state.settled:
                continue
            if transcript.state != "FILE_GENERATED":
                unfinished = True
                continue
            ref = f"{ORIGIN}{meeting.cid}/{transcript.tid}"
            assert meeting.end is not None
            try:
                items = await self._list(
                    reader, f"/{transcript.name}/entries", "transcriptEntries", ENTRIES_PAGE,
                    "transcripts.entries.list",
                )  # fmt: skip
            except _TooLong:
                progress.skip(ref, TOO_LONG, key, meeting.end)
                continue
            if items is None:
                progress.skip(ref, GONE_TRANSCRIPT, key, meeting.end)
                continue
            entries = [_entry(item) for item in items]
            if not entries:
                progress.skip(ref, NO_ENTRIES, key, meeting.end)
                continue
            if known is None:
                known = await self._about(reader, meeting)
            full, people, code = known
            try:
                made = drafts(full, transcript, entries, people, code, self.project)
            except SourceError:
                raise
            except Exception:  # one transcript cairn fails on must not stop the source for good
                progress.skip(ref, UNREADABLE, key, meeting.end)
                continue
            progress.write(made, key, meeting.end)
        return unfinished

    async def _about(
        self, reader: Reader, meeting: Meeting
    ) -> tuple[Meeting, list[Person], str | None]:
        """What the drafts need beyond the transcript: the record (when the listing did not
        give it), the participants and the meeting code. _Gone when the meeting is."""
        found = await self._list(
            reader, f"/{meeting.name}/participants", "participants", PARTICIPANTS_PAGE,
            "participants.list",
        )  # fmt: skip
        if found is None:
            raise _Gone
        people = [_person(item) for item in found]
        if meeting.start is None:
            reply = await self._get(
                reader, f"/{meeting.name}", {}, "conferenceRecords.get", allow=(404,)
            )
            if reply.status == 404:
                raise _Gone
            record = _record(reply.body, "conferenceRecords.get")
            if record.cid != meeting.cid or record.end is None:
                raise _shape("conferenceRecords.get")
            meeting = record
            if self.code is not None and meeting.space != self._space:
                # Left waiting under no code, or another one: not this source's meeting now.
                raise _Gone
        code = None
        if self.code is not None and meeting.space == self._space:
            return meeting, people, self.code  # its space was resolved once this pull
        if meeting.space is not None:
            reply = await self._get(reader, f"/{meeting.space}", {}, "spaces.get", allow=(404,))
            if reply.status == 200:
                if not isinstance(reply.body, dict):
                    raise _shape("spaces.get")
                given = reply.body.get("meetingCode")
                code = given if isinstance(given, str) else None
        return meeting, people, code

    # Requests

    async def _resolve(self, reader: Reader, code: str) -> str:
        """The name of the space that holds the chosen code (spaces.get takes
        `spaces/{meetingCode}`, SP-GET); SourceError naming the code when there is none this
        account can reach (404 or 403)."""
        what = "spaces.get"
        reply = await self._get(reader, f"/spaces/{code}", {}, what, allow=(403, 404))
        if reply.limited:  # a 403 that is a rate limit, still: said as any other
            raise SourceError(refusal(reply, what, self.variable))
        if reply.status in (403, 404):
            raise SourceError(no_space(code, reply.status))
        name = reply.body.get("name") if isinstance(reply.body, dict) else None
        if not isinstance(name, str) or not SPACE.fullmatch(name):
            raise _shape(what)
        return name

    async def _records(self, reader: Reader, lower: datetime) -> list[Meeting]:
        found = await self._list(
            reader, "/conferenceRecords", "conferenceRecords", RECORDS_PAGE,
            "conferenceRecords.list", {"filter": record_filter(lower, self.code)}, gone=False,
            said={400: FILTER_REFUSED} if self.code else None,
        )  # fmt: skip
        assert found is not None
        return [_record(item, "conferenceRecords.list") for item in found]

    async def _list(
        self,
        reader: Reader,
        path: str,
        key: str,
        size: int,
        what: str,
        params: Mapping[str, str] | None = None,
        *,
        gone: bool = True,
        said: Mapping[int, str] | None = None,
    ) -> list[Any] | None:
        """Every item of a list, every page, `params` beside each page's token; None when it
        answers 404 and `gone` allows it. _TooLong past MAX_PAGES. `said`: the SourceError's
        sentence for a status, in place of refusal's."""
        items: list[Any] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            asked = {**(params or {}), "pageSize": str(size)}
            if token:
                asked["pageToken"] = token
            reply = await self._get(
                reader, path, asked, what, allow=(404,) if gone else (), said=said
            )
            if reply.status == 404:
                return None
            body = reply.body
            if not isinstance(body, dict):
                raise _shape(what)
            page = body.get(key, [])
            token = body.get("nextPageToken")
            if not isinstance(page, list) or not (token is None or isinstance(token, str)):
                raise _shape(what)
            items += page
            if len(items) > MAX_PAGES * size:  # a page may hold more than asked
                break
            if not token:
                return items
        if what == "transcripts.entries.list":
            raise _TooLong
        raise SourceError(f"{SERVICE} listed more than this server reads in one {what}")

    async def _get(
        self,
        reader: Reader,
        path: str,
        params: Mapping[str, str],
        what: str,
        allow: tuple[int, ...] = (),
        said: Mapping[int, str] | None = None,
    ) -> Reply:
        reply = await reader.get(self.base_url + path, params, service=SERVICE, what=what)
        if reply.status == 200 or reply.status in allow:
            return reply
        raise SourceError((said or {}).get(reply.status) or refusal(reply, what, self.variable))
