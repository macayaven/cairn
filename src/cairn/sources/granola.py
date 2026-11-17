"""The Granola source: meeting notes read from Granola's REST API, a page a meeting.

Contract (frozen by tests/acceptance/test_c3_granola.py; the API's shapes are recorded in
docs/granola-api.md, with what Granola's documentation does not state marked as such and
what cairn decided marked **[cairn]**). cairn never writes to Granola.

The kind ("granola" in cairn.sources.KINDS)
- It starts off: added off, read only once an owner or admin switches it on with a project,
  and then only notes created at or after that switch-on (`since`). Its notes are written
  via "granola", with the label "granola".
- The config: `key_env` (required: the name of the environment variable that holds the API
  key, capitals, digits and "_"), `projects` (at most one project name; none while it is off),
  `base_url` (DEFAULT_BASE_URL unless given; https, or http on a loopback host; no user,
  password, query or fragment; a path is kept, a trailing "/" dropped), `folder_id` or
  `space_id` (at most one, in the API's id patterns, kept as given: ids are case-insensitive),
  `transcripts` (false), `settle_minutes` (30, 0 to 1440) and `lookback_days` (30, 1 to
  365). No other setting. `check` returns the config with the defaults filled in. A refusal
  names the setting at fault and never repeats a value: a value that looks like a key (it
  starts "grn_", or a pattern of the "secrets" guard), in key_env, a project's name, or
  base_url as a whole or in a host label or path segment, is refused as such; a setting
  cairn does not take is named only when its name is a short plain word that is not one.
- `open` reads the key from `env` (a missing or blank variable is a SourceError naming it)
  and sends it only as `Authorization: Bearer <key>`; the key and that header are the
  source's `secrets`. It reads through the `http` client it is given (the runner's
  source_client keeps httpx's request lines out of every log).

Finding the notes
- Every pull lists what changed: List notes with `updated_after` OVERLAP before the
  cursor's mark (the first pull's mark is `since`; a mark older than `since`, after the
  source was switched off and on again, counts as `since`). At most once a day by the
  injected clock (RESCAN_EVERY; the first pull too) it also lists by creation:
  `created_after` OVERLAP before the later of `since` and `lookback_days` ago, which finds
  a note that became readable late without its `updated_at` moving (a summary generated
  late, a note shared later). Each scan sends its filter (folder_id or space_id) and
  page_size 30 on every page, its cursor beside them, and follows `hasMore` (an empty page
  that has more is not the end; more without a cursor is a SourceError), at most
  MAX_LIST_PAGES pages (a SourceError that says to lower lookback_days or name a folder or
  space). **[cairn]** Granola chooses how many notes a page holds, so a scan also ends, the
  same way, past as many notes as full pages hold (MAX_LIST_PAGES x 30: 30,000).
- Every bound is whole seconds rounded down, with "Z", OVERLAP (one hour) before the
  moment it stands for. Why an hour: Granola states neither whether a bound is inclusive
  nor the precision of its times, and the mark is cairn's clock set against Granola's
  `updated_at`, so the overlap has to cover an exclusive bound, a clock some minutes off
  and a list that shows an edit a little late. A note that shows up later than that with
  an old `updated_at` is the daily rescan's to find, so a longer overlap would only list
  the same notes again. cairn checks `created_at >= since` itself, whatever the bounds do.
- A listed note is left alone when it was created before `since`, has `deleted_at` set,
  was read already (its digest is in the cursor, or it was created before the cursor's
  floor) or is still settling (`updated_at` less than `settle_minutes` ago: it waits for a
  later pull, and the mark does not pass it). The rest are read oldest `created_at` first,
  then by id ignoring case, whatever order the API listed them in. A listed id not of the
  documented form fails the pull before it goes into a path. **[cairn]** One pull reads at
  most PER_PULL (200) notes (one Get note says is deleted does not count); then it returns
  as at its deadline (below), and the next pull goes on with the rest.

Reading a note, once
- Get note (with include=transcript when `transcripts` is on). 404: skipped (GONE) and
  counted as read. 413 (TRANSCRIPT_TOO_LARGE): Get note again without the transcript, which
  is then read from Get transcript. A note Get note says is deleted is not written and not
  remembered. Otherwise its summary draft and, with transcripts, its transcript drafts.
- The transcript: the inline one when Get note gave a list (an empty list: skipped,
  NO_TRANSCRIPT); when it gave null, or answered 413, Get transcript page by page
  (page_size 100), at most MAX_TRANSCRIPT_PAGES pages (more: skipped, "transcript longer
  than this server reads"). **[cairn]** The same skip for a transcript, paged or inline, of more
  items than full pages hold (MAX_TRANSCRIPT_PAGES x 100: 50,000). Get transcript's 404 or
  an empty transcript: NO_TRANSCRIPT. Its 403 with code FORBIDDEN: TRANSCRIPTS_OFF; any
  other 403 (a suspended account, an inactive subscription, an IP allowlist) fails the
  pull, since a note is read once and a transcript skipped for a passing reason would be
  lost for good. In every skip the
  summary draft is still written, and the note is reported once.
- A note cairn fails on while it makes its drafts (anything but a SourceError) is skipped,
  UNREADABLE, and counted as read; when it fails on the transcript only, the summary draft
  is still written. One note never stops the source.
- After its drafts are written a note is read: a later edit, a regenerated summary, or its
  deletion at Granola changes nothing in cairn (decision 26). A skip reason is a fixed
  sentence that says what happened and what follows, never an id: the runner counts them.

The cursor (opaque JSON this module owns, version 1; never an id, a title or a key)
- `mark`: the next `updated_after` is OVERLAP before it. After a pull it is `settle_minutes`
  before the pull's clock, or the oldest `updated_at` still settling when that is earlier,
  and never earlier than it was.
- `rescan`: when the last rescan ran (null before the first).
- `floor`: a whole second; a note created before it counts as read. After a pull it is the
  earlier of the oldest creation a rescan can still reach (the later of `since` and
  `lookback_days` ago) and the creation of every note still settling or half written, and
  never lower than it was. So a note read long ago whose digest has been let go is not
  written again when it is edited, and a note still waiting is never passed.
- `seen`: the notes read, as the first 16 hex digits of the SHA-256 of the lowercased id,
  each with its creation in whole Unix seconds rounded down; only those at or above the
  floor are kept, so the cursor holds about one lookback window of notes, however many
  pass through.
- `parts`: the notes half written (a run stopped after a note's first drafts), by digest:
  the index of the next draft and the creation. That note goes on from there when it is
  next read, and the floor does not pass it. **[cairn]** A note half written and created
  before `since` (a run stopped in it before the source was switched off and on again) is
  never read again, as no note created before `since` is: its remaining drafts are given
  up, so nothing written while the source was off is read, and its entry is let go when
  a pull starts, so it does not hold the floor down for good.
- Each draft carries `resume`: the cursor the pull started from with `seen` and `parts` as
  they stood right after that draft, so a run that stops there is picked up at the next
  draft, the same scans being asked again. At its `deadline` (no request starts after it,
  and a 429 whose wait would pass it is not waited for) a pull returns the notes it
  finished, with the cursor it started from plus their digests: mark, floor and rescan do
  not move. A pull that reaches its deadline before it has listed the notes, or before it
  has read one, raises a SourceError instead of returning a cursor that never moves. A
  request under way is given what is left until the deadline and no more
  (cairn.sources.timing, at least its FLOOR): an answer that drips in or never comes counts
  as the deadline reached.

When Granola says no (a message never holds a body, an address, a query or the key)
- 429: waited out through the injected `sleep` (Retry-After seconds, at most
  MAX_RETRY_AFTER; DEFAULT_RETRY_AFTER, the API's burst window, when absent), at most
  RETRIES times, then a SourceError. 401 or 403: a SourceError naming `key_env` and, from
  the error's `code` only, what to do. 404 or 400 on List notes, 5xx, a redirect (never
  followed), a network error (by its class), an answer over MAX_RESPONSE_BYTES, not JSON, or
  not the documented shape: a SourceError. A failed pull returns nothing, so no cursor
  moves; the next pull writes what it did not, and nothing twice.

What it writes, for each note, into the config's one project
- `title`: the note's title on one line, or UNTITLED. `source_ref`: "meeting:granola/<note
  id>". `label`: "granola".
- `topic`: the title, a comma and the meeting's date, "Payments weekly, 2026-11-11" (UTC;
  the calendar event's scheduled start, else `created_at`), so each meeting has its own
  page and a weekly one does not pile onto one. The title is cut (at a word when it can be)
  to 80 characters and so that the page's slug keeps the date and a transcript part's
  suffix (see `topic`). Two meetings with the same title on one day share a page; so do
  two whose titles have no ASCII letter or digit.
- `text`: "# <title>", a blank line, the facts as a list ("- When: 2026-11-11 22:30 UTC,
  for 30 minutes" from the calendar event, or "..., when the note was created" without
  one; "- Attendees: " the names, and how many more have no name; "- Owner: " the name),
  a blank line, then the summary as Granola gave it (`summary_markdown`, else
  `summary_text`, else NO_SUMMARY), unchanged. Titles and names are put on one line, so
  none of them starts a heading. cairn writes names, never the address fields (owner,
  attendees, invitees, organiser) and never the owner's private notes. A name is typed by
  a person and is often an address, so a name (an owner's, an attendee's, a speaker's or
  a diarization label) that holds "@", of any width by cairn.sources.names's check,
  counts as no name: an attendee is counted, the owner left out, a speaker named by the
  next rule below. The summary is
  not rewritten: an address Granola put in it reaches the note guard, whose policy
  decides, as for any note.
- With `transcripts`: drafts whose `topic` is the summary's plus " transcript" (", part N"
  from the second), `source_ref` the summary's plus "/transcript" ("/N" from the second),
  title the note's plus the same suffix, and text "# <topic>", a blank line and a list item
  a turn: "- HH:MM:SS <speaker>: <what was said>". A turn is the consecutive items of one
  speaker, joined; the speaker is the item's name, else its diarization label, else the
  owner's name for "me", "another participant" for "them", else "an unknown speaker".
  Times count from the calendar event's scheduled start, else from the first item (not
  from `created_at`: a note can be opened long before its meeting), with a minus sign for
  a turn before the start. At most TURNS_PER_PART turns a draft, so no draft fills a page
  alone.
- Every note fits a page by both of its bounds (K4: cairn.sources.fit, read when the note is
  made). A summary too long for a page is cut between two passages where it can (a fence
  it cuts through closed) and followed by SUMMARY_CUT. A transcript's part also ends where
  its next turn would take its characters past a page's, and a turn longer than a page
  holds goes on, with the same time and speaker, in the next part: the parts hold the whole
  transcript between them. A note still past a page's bounds is cut at its end with
  NOTE_CUT.
"""

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from cairn.guard import PatternGuard
from cairn.sources import Clock, NoteDraft, Pulled, Skip, SourceError, SourceKind, fit, names
from cairn.sources.timing import limit
from cairn.store.memory import TITLE_MAX, is_project_name

DEFAULT_BASE_URL = "https://public-api.granola.ai/v1"
VIA = "granola"
LABEL = "granola"
ORIGIN = "meeting:granola/"
UNTITLED = "Untitled meeting"
DEFAULT_PROJECT = "meetings"  # what /sources proposes as the project a source writes into
NO_SUMMARY = "Granola gave no summary."
TURNS_PER_PART = 1_500
SUMMARY_CUT = "The rest of the summary is left out: it is longer than a page holds."
NOTE_CUT = "The rest of this note is left out: it is longer than a page holds."
TOPIC_SLUG_ROOM = 34  # of the slug's 64: ", YYYY-MM-DD transcript, part NN" takes the rest
RETRIES = 3  # of one request answered 429
DEFAULT_RETRY_AFTER = 5.0  # seconds: the API's burst window, when a 429 does not say
MAX_RETRY_AFTER = 60.0
OVERLAP = timedelta(hours=1)  # every bound sent is this far before the moment it stands for
RESCAN_EVERY = timedelta(days=1)
LIST_PAGE = 30  # the API's maximum
TRANSCRIPT_PAGE = 100  # the API's maximum
MAX_LIST_PAGES = 1_000  # of one scan: 30,000 notes
MAX_TRANSCRIPT_PAGES = 500  # of one transcript: 50,000 items
PER_PULL = 200  # notes one pull reads and keeps the drafts of; the rest wait for the next
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024  # of an error's body, read only for its code
ERROR_CODE = re.compile(r"[A-Z][A-Z_]{0,63}")
GONE = (
    "gone: Granola no longer shows this note (its summary removed, or the note deleted or"
    " unshared after it was listed); it is not read again"
)
NO_TRANSCRIPT = (
    "no transcript: Granola has none for this note (none was recorded, or it was deleted);"
    " the summary was written"
)
TRANSCRIPTS_OFF = (
    "transcripts are switched off for this key: a Granola admin turned off reading"
    " transcripts over the API; the summary was written, and this transcript is not read later"
)
UNREADABLE = (
    "unreadable: this server failed on what Granola gave for this note and could not make a note of"
    " it (or of its transcript, when its summary was written); it is not read again"
)
CURSOR_VERSION = 1
DIGEST_HEX = 16
DIGEST = re.compile(r"[0-9a-f]{16}")
SETTINGS = (
    "key_env",
    "projects",
    "base_url",
    "folder_id",
    "space_id",
    "transcripts",
    "settle_minutes",
    "lookback_days",
)
SETTLE_MINUTES = (30, 0, 1440)  # the default, the least, the most
LOOKBACK_DAYS = (30, 1, 365)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
SETTING_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")  # a name a refusal may repeat
FOLDER_ID = re.compile(r"[Ff][Oo][Ll]_[a-zA-Z0-9]{14}")
SPACE_ID = re.compile(r"[Ss][Pp][Aa]_[a-zA-Z0-9]{14}")
NOTE_ID = re.compile(r"[Nn][Oo][Tt]_[a-zA-Z0-9]{14}")
_KEY_PREFIX = "grn_"  # how every key in Granola's examples starts


# The config


def _looks_like_a_credential(value: str) -> bool:
    """The rule every setting is held to: a Granola key's prefix, or a pattern of the
    "secrets" guard."""
    return value.casefold().startswith(_KEY_PREFIX) or bool(PatternGuard("secrets").check(value))


def _whole(config: Mapping[str, Any], setting: str, bounds: tuple[int, int, int]) -> int:
    default, least, most = bounds
    value = config.get(setting, default)
    if isinstance(value, bool) or not isinstance(value, int) or not least <= value <= most:
        raise ValueError(f"{setting} is a whole number from {least} to {most}")
    return value


def check_config(config: object) -> dict[str, Any]:
    """The config as stored, with the defaults filled in, or ValueError. A refusal names the
    setting at fault and never repeats a value."""
    if not isinstance(config, Mapping):
        raise ValueError("a granola source's config is an object of settings")
    for setting in config:
        if setting in SETTINGS:
            continue
        if (
            isinstance(setting, str)
            and SETTING_NAME.fullmatch(setting)
            and not _looks_like_a_credential(setting)
        ):
            raise ValueError(
                f"a granola source has no setting {setting!r}: it takes {', '.join(SETTINGS)}"
            )
        raise ValueError(
            f"a granola source does not take one of the settings given: it takes"
            f" {', '.join(SETTINGS)}"
        )
    if "key_env" not in config:
        raise ValueError(
            "a granola source needs key_env: the name of the environment variable that holds"
            " the Granola API key"
        )
    key_env = config["key_env"]
    if isinstance(key_env, str) and _looks_like_a_credential(key_env):
        raise ValueError(
            "key_env holds what looks like a key: put the key in an environment variable of"
            " the process that syncs, and name that variable here"
        )
    if not isinstance(key_env, str) or not ENV_NAME.fullmatch(key_env):
        raise ValueError(
            "key_env names an environment variable (capitals, digits and _, at most 128),"
            " which holds the Granola API key: the key itself never goes in a config"
        )
    checked: dict[str, Any] = {"key_env": key_env, "projects": _check_projects(config)}
    checked["base_url"] = _check_base_url(config.get("base_url", DEFAULT_BASE_URL))
    given = [s for s in ("folder_id", "space_id") if config.get(s) is not None]
    if len(given) > 1:
        raise ValueError(
            "folder_id and space_id are both given: a source reads one folder or one space"
        )
    for setting, pattern, what in (
        ("folder_id", FOLDER_ID, "a folder's id (fol_ and 14 letters or digits)"),
        ("space_id", SPACE_ID, "a space's id (spa_ and 14 letters or digits)"),
    ):
        if setting in given:
            value = config[setting]
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise ValueError(f"{setting} is {what}, as Granola's List folders or spaces gives")
            checked[setting] = value
    transcripts = config.get("transcripts", False)
    if not isinstance(transcripts, bool):
        raise ValueError("transcripts is true or false")
    checked["transcripts"] = transcripts
    checked["settle_minutes"] = _whole(config, "settle_minutes", SETTLE_MINUTES)
    checked["lookback_days"] = _whole(config, "lookback_days", LOOKBACK_DAYS)
    return checked


def _check_projects(config: Mapping[str, Any]) -> list[str]:
    projects = config.get("projects", [])
    if not isinstance(projects, list | tuple):
        raise ValueError("projects is a list of at most one project's name")
    if len(projects) > 1:
        raise ValueError("projects names at most one project: a granola source writes into one")
    for project in projects:
        if isinstance(project, str) and _looks_like_a_credential(project):
            raise ValueError("projects holds a value that looks like a credential")
        if not isinstance(project, str) or not is_project_name(project):
            raise ValueError("projects holds a project's name (not its id)")
    return list(projects)


def _check_base_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("base_url is the address of Granola's API")
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
    except ValueError:
        raise ValueError("base_url is not a URL") from None
    if "@" in parts.netloc:
        raise ValueError(
            "base_url must not hold a user or password: the key comes from the environment"
        )
    scheme = parts.scheme.lower()
    if not host or not (scheme == "https" or (scheme == "http" and host in LOOPBACK_HOSTS)):
        raise ValueError("base_url is https, or http on this machine")
    if parts.query or parts.fragment or "?" in value or "#" in value:
        raise ValueError("base_url has no query or fragment")
    pieces = [value, *host.split("."), *(unquote(s) for s in parts.path.split("/"))]
    if any(_looks_like_a_credential(piece) for piece in pieces if piece):
        raise ValueError(
            "base_url looks like it holds a credential: the key comes from the environment,"
            " named by key_env"
        )
    return f"{scheme}://{parts.netloc}{parts.path.rstrip('/')}"


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
) -> "GranolaSource":
    """The source a stored config describes, with its key read from `env`. SourceError
    names a variable that is missing or blank, never a value."""
    variable = config["key_env"]
    key = (env.get(variable) or "").strip()
    if not key:
        raise SourceError(
            f"set {variable} in the environment of the process that syncs source {name!r}:"
            " it holds the Granola API key"
        )
    return GranolaSource(
        name, config, key, http=http, clock=clock, sleep=sleep, deadline=deadline, since=since
    )


def describe(config: Mapping[str, Any]) -> str:
    """What a source reads, for GET /sources: which notes, whether transcripts, from where,
    and when a note is read."""
    if config.get("folder_id"):
        which = f"in folder {config['folder_id']}"
    elif config.get("space_id"):
        which = f"in space {config['space_id']}"
    else:
        which = "the key can read"
    extent = "with their transcripts" if config.get("transcripts") else "summaries only"
    minutes = config.get("settle_minutes", SETTLE_MINUTES[0])
    when = (
        f"once it has not changed for {minutes} minute{'s' if minutes != 1 else ''}"
        if minutes
        else "as soon as it is listed"
    )
    return f"Meeting notes {which}, {extent}, at {config['base_url']}; each is read {when}"


KIND = SourceKind(
    check=check_config,
    open=open_source,
    via=VIA,
    starts_off=True,
    describe=describe,
    default_project=DEFAULT_PROJECT,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _moment(value: object) -> datetime | None:
    """A timestamp from the API as an aware datetime in UTC; None when it is not one with a
    time zone."""
    if not isinstance(value, str):
        return None
    try:  # a date that is not one, or a moment outside years 1 to 9999 once in UTC
        moment = datetime.fromisoformat(value)
        return moment.astimezone(UTC) if moment.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _second(moment: datetime) -> datetime:
    """`moment` in UTC, rounded down to a whole second."""
    return moment.astimezone(UTC).replace(microsecond=0)


def _seconds(moment: datetime) -> int:
    """`moment` as whole Unix seconds, rounded down: how the cursor keeps a creation."""
    return math.floor(moment.timestamp())


def _bound(moment: datetime) -> str:
    """A bound for List notes: OVERLAP before `moment`, in whole seconds rounded down, with
    "Z", as Granola's examples write one."""
    return _second(moment - OVERLAP).isoformat().replace("+00:00", "Z")


def _one_line(value: object) -> str:
    """Text from Granola on one line: a title, a name or a turn never starts a heading."""
    return " ".join(value.split()) if isinstance(value, str) else ""


# The cursor


def digest(note_id: str) -> str:
    """What the cursor keeps of a note: never its id. Ids are case-insensitive."""
    return hashlib.sha256(note_id.lower().encode()).hexdigest()[:DIGEST_HEX]


@dataclass(frozen=True)
class Cursor:
    """What a pull starts from (see the module docstring): the mark, the last rescan, the
    floor, the notes read (digest: creation, in Unix seconds) and the notes half written
    (digest: (the next draft's index, creation))."""

    mark: datetime
    rescan: datetime | None
    floor: datetime
    seen: dict[str, int] = field(default_factory=dict)
    parts: dict[str, tuple[int, int]] = field(default_factory=dict)

    @classmethod
    def first(cls, since: datetime) -> "Cursor":
        return cls(mark=since, rescan=None, floor=_second(since))

    def pruned(self, floor: datetime) -> "Cursor":
        """The cursor with its floor raised to `floor` (never lowered) and what lies below
        the floor let go: a note created before it counts as read. The floor is a whole
        second, rounded down, so a note's creation kept as whole seconds (rounded down)
        is below it only when the note is."""
        floor = max(_second(floor), self.floor)
        least = floor.timestamp()
        return replace(
            self,
            floor=floor,
            seen={d: c for d, c in self.seen.items() if c >= least},
            parts={d: p for d, p in self.parts.items() if p[1] >= least},
        )

    def encode(self) -> str:
        return json.dumps(
            {
                "v": CURSOR_VERSION,
                "mark": _iso(self.mark),
                "rescan": _iso(self.rescan) if self.rescan else None,
                "floor": _iso(self.floor),
                "seen": {d: self.seen[d] for d in sorted(self.seen)},
                "parts": {d: list(self.parts[d]) for d in sorted(self.parts)},
            },
            separators=(",", ":"),
        )

    @classmethod
    def decode(cls, text: str) -> "Cursor":
        broken = SourceError(
            "the stored cursor is not one this Granola source wrote: remove the source and add"
            " it again to start afresh"
        )
        try:
            state = json.loads(text)
        except ValueError:
            raise broken from None
        if not isinstance(state, dict) or state.get("v") != CURSOR_VERSION:
            raise broken
        mark, floor = _moment(state.get("mark")), _moment(state.get("floor"))
        rescan = _moment(state.get("rescan"))
        seen, parts = state.get("seen"), state.get("parts")
        if mark is None or floor is None or (state.get("rescan") is not None and rescan is None):
            raise broken
        if not isinstance(seen, dict) or not isinstance(parts, dict):
            raise broken
        if not all(DIGEST.fullmatch(d) and _count(c) for d, c in seen.items()):
            raise broken
        if not all(
            DIGEST.fullmatch(d) and isinstance(p, list) and len(p) == 2 and all(map(_count, p))
            for d, p in parts.items()
        ):
            raise broken
        return cls(mark, rescan, floor, dict(seen), {d: (p[0], p[1]) for d, p in parts.items()})


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# What a draft says


@dataclass(frozen=True)
class Meeting:
    """What Get note answered, as far as a draft uses it: never an email address and never
    the owner's private notes."""

    id: str
    title: str  # one line, or UNTITLED
    owner: str | None
    created: datetime
    start: datetime | None  # the calendar event's scheduled start
    end: datetime | None
    attendees: list[str]  # names, one line each
    unnamed: int  # attendees without a name
    summary: str
    transcript: list[Any] | None
    deleted: bool


def parse_note(body: object, note_id: str) -> Meeting:
    """Get note's answer for `note_id`, or SourceError when it is not the documented shape."""

    def wrong() -> SourceError:
        return SourceError("Granola's answer to Get note is not the documented shape")

    if not isinstance(body, dict) or not isinstance(body.get("id"), str):
        raise wrong()
    if body["id"].lower() != note_id.lower():
        raise SourceError("Granola's answer to Get note is about another note")
    created, owner = _moment(body.get("created_at")), body.get("owner")
    event, attendees = body.get("calendar_event"), body.get("attendees")
    summary_text, summary_markdown = body.get("summary_text"), body.get("summary_markdown")
    transcript = body.get("transcript")
    if created is None or not isinstance(owner, dict) or not isinstance(attendees, list):
        raise wrong()
    if not all(isinstance(a, dict) for a in attendees) or not isinstance(summary_text, str):
        raise wrong()
    if not (event is None or isinstance(event, dict)) or "deleted_at" not in body:
        raise wrong()
    if not (summary_markdown is None or isinstance(summary_markdown, str)):
        raise wrong()
    if not (transcript is None or isinstance(transcript, list)):
        raise wrong()
    start = end = None
    if event is not None:
        start = _moment(event.get("scheduled_start_time"))
        end = _moment(event.get("scheduled_end_time"))
    named = [names.shown(a.get("name")) for a in attendees]
    summary = summary_markdown if summary_markdown and summary_markdown.strip() else summary_text
    return Meeting(
        id=body["id"],
        title=_one_line(body.get("title")) or UNTITLED,
        owner=names.shown(owner.get("name")),
        created=created,
        start=start,
        end=end,
        attendees=[n for n in named if n],
        unnamed=sum(1 for n in named if not n),
        summary=summary if summary.strip() else NO_SUMMARY,
        transcript=transcript,
        deleted=body["deleted_at"] is not None,
    )


def topic(title: str, day: datetime) -> str:
    """The page a meeting goes on: its title, a comma and its date. The title is cut (at a
    word when it can be) so that the page's slug, which the compiler cuts at 64 characters,
    keeps the date and a transcript part's suffix: two meetings never share a page for want
    of room."""
    from cairn.compiler import slugify  # the compiler imports the store, which imports this

    cut = title[:TITLE_MAX].strip()
    while len(slugify(cut)) > TOPIC_SLUG_ROOM:
        shorter = cut.rsplit(" ", 1)[0] if " " in cut else cut[:-1]
        cut = shorter.rstrip(" ,")
    return f"{cut or UNTITLED}, {day.astimezone(UTC):%Y-%m-%d}"


def _length(span: timedelta) -> str:
    minutes = int(span.total_seconds()) // 60
    hours, minutes = divmod(minutes, 60)
    said = []
    if hours:
        said.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not hours:
        said.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(said)


def summary_text(meeting: Meeting) -> str:
    """The note's Markdown: a heading with the title, the facts, then the summary as Granola
    gave it, unchanged."""
    if meeting.start is not None:
        when = f"{meeting.start:%Y-%m-%d %H:%M} UTC"
        if meeting.end is not None and meeting.end > meeting.start:
            when += f", for {_length(meeting.end - meeting.start)}"
    else:
        when = f"{meeting.created:%Y-%m-%d %H:%M} UTC, when the note was created"
    facts = [f"- When: {when}"]
    if meeting.attendees or meeting.unnamed:
        who = ", ".join(meeting.attendees)
        if meeting.unnamed:
            more = f"{meeting.unnamed} more without a name"
            who = f"{who}, and {more}" if who else more
        facts.append(f"- Attendees: {who}")
    if meeting.owner:
        facts.append(f"- Owner: {meeting.owner}")
    head = "\n".join([f"# {meeting.title}", "", *facts, "", ""])
    room = fit.chars() - len(head) - len(SUMMARY_CUT) - 2
    summary, _, was_cut = fit.cut(meeting.summary, max(1, fit.claims() - len(facts) - 1), room)
    if not was_cut:
        summary = meeting.summary  # unchanged, as Granola gave it
    text = head + summary + (f"\n\n{SUMMARY_CUT}" if was_cut else "")
    return fit.fitted(text if text.endswith("\n") else text + "\n", NOTE_CUT)


def summary_draft(meeting: Meeting, project: str) -> NoteDraft:
    return NoteDraft(
        project=project,
        title=meeting.title,
        text=summary_text(meeting),
        source_ref=f"{ORIGIN}{meeting.id}",
        label=LABEL,
        topic=topic(meeting.title, meeting.start or meeting.created),
    )


@dataclass(frozen=True)
class Turn:
    """Consecutive transcript items of one speaker: who, when the first began, what."""

    speaker: str
    start: datetime
    text: str


def _speaker(speaker: dict[str, Any], owner: str | None) -> str:
    named = names.shown(speaker.get("name")) or names.shown(speaker.get("diarization_label"))
    if named:
        return named
    if speaker.get("attribution") == "me":
        return owner or "the note's owner"
    if speaker.get("attribution") == "them":
        return "another participant"
    return "an unknown speaker"


def turns(items: list[Any], owner: str | None) -> list[Turn]:
    """The transcript's items as turns, in the order Granola gave them; SourceError for an
    item not of the documented shape."""
    runs: list[tuple[str, datetime, list[str]]] = []  # joined once, at the end
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("speaker"), dict):
            raise SourceError("Granola's transcript is not the documented shape")
        start = _moment(item.get("start_time"))
        if start is None or not isinstance(item.get("text"), str):
            raise SourceError("Granola's transcript is not the documented shape")
        who, said = _speaker(item["speaker"], owner), _one_line(item["text"])
        if runs and runs[-1][0] == who:
            runs[-1][2].append(said)
        else:
            runs.append((who, start, [said]))
    return [Turn(who, start, " ".join(p for p in pieces if p)) for who, start, pieces in runs]


def offset(moment: datetime, zero: datetime) -> str:
    """`moment` as HH:MM:SS from `zero`, with a minus sign before it."""
    seconds = int((moment - zero).total_seconds())
    sign, seconds = ("-" if seconds < 0 else ""), abs(seconds)
    return f"{sign}{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def transcript_drafts(summary: NoteDraft, meeting: Meeting, said: list[Turn]) -> list[NoteDraft]:
    """The transcript's drafts: a line a turn, at most TURNS_PER_PART turns a draft and
    within a page's bounds (cairn.sources.fit.packed: a turn too long goes on in the next
    part). Times count from the calendar event's start, else from the first item."""
    if not said:
        return []
    zero = meeting.start or said[0].start
    lines = [(f"- {offset(turn.start, zero)} {turn.speaker}: ", turn.text) for turn in said]
    bound = (min(TURNS_PER_PART, fit.claims()), fit.chars())
    drafts = []
    for index, part in enumerate(fit.packed(lines, bound, bound)):
        number = index + 1
        suffix = " transcript" + (f", part {number}" if number > 1 else "")
        named = summary.topic + suffix  # type: ignore[operator]
        body = "\n".join([f"# {named}", "", *part, ""])
        drafts.append(
            NoteDraft(
                project=summary.project,
                title=meeting.title + suffix,
                text=fit.fitted(body, NOTE_CUT),
                source_ref=summary.source_ref
                + "/transcript"
                + (f"/{number}" if number > 1 else ""),
                label=LABEL,
                topic=named,
            )
        )
    return drafts


# Requests: waits and wording


def retry_wait(header: str | None) -> float:
    """How long to wait after a 429: its Retry-After seconds, at most MAX_RETRY_AFTER, else
    DEFAULT_RETRY_AFTER (Granola documents no Retry-After)."""
    try:
        seconds = float(header or "")
    except ValueError:
        return DEFAULT_RETRY_AFTER
    if not math.isfinite(seconds) or seconds < 0:
        return DEFAULT_RETRY_AFTER
    return min(seconds, MAX_RETRY_AFTER)


_KEY_REFUSED = {
    "INVALID_API_KEY": "the key is unknown, revoked or expired: make a new one in Granola"
    " (Settings, Connectors, API keys) and put it in {variable}",
    "MISSING_API_KEY": "it did not get the key: check that {variable} holds it",
    "IP_ALLOWLIST_DENIED": "this server's address is outside the workspace's IP allowlist:"
    " a Granola admin can add it",
    "ACCOUNT_SUSPENDED": "the account that made the key in {variable} is suspended",
    "SUBSCRIPTION_INACTIVE": "the workspace's subscription is not active: the API needs a"
    " Business or Enterprise plan",
    "FORBIDDEN": "the key in {variable} may not do this: check its access scopes",
}


def refusal(status: int, code: str | None, endpoint: str, variable: str) -> str:
    """A SourceError's message for an answer that is not 200: what happened and what to do,
    from the status and Granola's error code, never from the body's message."""
    if status in (401, 403):
        why = _KEY_REFUSED.get(code or "", "check the key in {variable}").format(variable=variable)
        return f"Granola refused the API key in {variable} (HTTP {status}): {why}"
    if status == 429:
        return (
            f"Granola still answered 429 (rate limited) to {endpoint} after {RETRIES} retries:"
            " every key of a workspace shares its rate, so the next run goes on"
        )
    if 300 <= status < 400:
        return (
            f"Granola answered {endpoint} with a redirect (HTTP {status}), which this server does"
            " not follow: set base_url to the address that answers"
        )
    if status == 404 and endpoint == "List notes":
        return (
            "Granola answered 404 to List notes: check base_url (it ends in /v1), and that the"
            " key may list notes (a Notes by ID key cannot)"
        )
    if status == 400 and endpoint == "List notes":
        return (
            "Granola refused the request to List notes (HTTP 400): check folder_id or space_id"
            " against Granola's List folders or List spaces"
        )
    if status >= 500:
        return (
            f"Granola answered HTTP {status} to {endpoint}, a fault on its side: the next run"
            " tries again"
        )
    return f"Granola answered HTTP {status} to {endpoint}"


class GranolaSource:
    """Reads the meeting notes one Granola API key may read (see the module docstring)."""

    def __init__(
        self,
        name: str,
        config: Mapping[str, Any],
        key: str,
        *,
        http: httpx.AsyncClient,
        clock: Clock | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        deadline: datetime | None = None,
        since: datetime | None = None,
    ) -> None:
        self.name = name
        self.base_url = config["base_url"]
        self.variable = config["key_env"]
        self.project = config["projects"][0] if config["projects"] else ""
        self.filters = {s: config[s] for s in ("folder_id", "space_id") if s in config}
        self.transcripts = config["transcripts"]
        self.settle = timedelta(minutes=config["settle_minutes"])
        self.lookback = timedelta(days=config["lookback_days"])
        self._headers = {"Authorization": f"Bearer {key}"}
        self.secrets = (key, f"Bearer {key}")
        self._http = http
        self._clock = clock or _utc_now
        self._sleep = sleep or asyncio.sleep
        self.deadline = deadline
        self.since = since

    def __repr__(self) -> str:
        return f"GranolaSource({self.base_url!r}, name={self.name!r})"

    def _late(self) -> bool:
        return self.deadline is not None and self._clock() >= self.deadline

    async def pull(self, cursor: str | None) -> Pulled:
        now = self._clock().astimezone(UTC)
        since = (self.since or now).astimezone(UTC)
        start = Cursor.first(since) if cursor is None else Cursor.decode(cursor)
        start = replace(  # switched on again since: a half note from before it is let go
            start,
            mark=max(start.mark, since),
            parts={d: p for d, p in start.parts.items() if p[1] + 1 > since.timestamp()},
        )
        horizon = max(since, now - self.lookback)  # the oldest creation a rescan reaches
        rescanning = start.rescan is None or now - start.rescan >= RESCAN_EVERY
        try:
            listed = await self._list({"updated_after": _bound(start.mark)})
            if rescanning:
                listed += await self._list({"created_after": _bound(horizon)})
        except _Spent as spent:
            raise SourceError(
                f"Granola's notes could not be listed within one pull's bounds ({spent}), so"
                " the cursor cannot move: the next run tries again"
            ) from None
        found: dict[str, _Listed] = {}
        for note in listed:
            known = found.get(note.id.lower())
            if known is None or note.updated > known.updated:
                found[note.id.lower()] = note
        settled_by = now - self.settle
        todo, waiting = [], []
        for note in found.values():
            if note.created < since or note.deleted or note.created < start.floor:
                continue  # before it was on; deleted; or read already (below the floor)
            if digest(note.id) in start.seen:
                continue
            (waiting if note.updated > settled_by else todo).append(note)
        todo.sort(key=lambda note: (note.created, note.id.lower()))

        seen, parts = dict(start.seen), dict(start.parts)
        drafts: list[NoteDraft] = []
        skipped: list[Skip] = []
        moved, kept = False, 0
        for note in todo:
            if kept == PER_PULL:  # the rest wait for the next pull, as at the deadline
                return Pulled(drafts, skipped, replace(start, seen=seen, parts=parts).encode())
            key, created = digest(note.id), _seconds(note.created)
            try:
                read = await self._read(note)
            except _Spent:
                if not moved:
                    raise SourceError(
                        "Granola did not answer for one note within one pull's bounds (its"
                        " deadline), so the cursor cannot move: the next run tries again"
                    ) from None
                return Pulled(drafts, skipped, replace(start, seen=seen, parts=parts).encode())
            if read is None:  # deleted when it was read: not written, not remembered
                continue
            produced, skips = read
            first = parts.pop(key, (0, 0))[0]
            for index in range(first, len(produced)):
                if index == len(produced) - 1:
                    seen[key] = created
                else:
                    parts[key] = (index + 1, created)
                resume = replace(start, seen=dict(seen), parts=dict(parts)).encode()
                drafts.append(replace(produced[index], resume=resume))
                parts.pop(key, None)
            if first == 0:
                skipped += skips
            seen[key] = created
            moved, kept = True, kept + 1

        mark = settled_by
        if waiting:
            mark = min([mark, *(note.updated for note in waiting)])
        floor = min(
            [
                horizon,
                *(note.created for note in waiting),
                *(datetime.fromtimestamp(c, UTC) for _, c in parts.values()),
            ]
        )
        following = replace(
            start,
            mark=max(mark, start.mark),
            rescan=now if rescanning else start.rescan,
            seen=seen,
            parts=parts,
        ).pruned(floor)
        return Pulled(drafts=drafts, skipped=skipped, cursor=following.encode())

    # Reading one note

    async def _read(self, note: "_Listed") -> tuple[list[NoteDraft], list[Skip]] | None:
        """The note's drafts and skips; None when Granola says it was deleted."""
        path = f"/notes/{note.id}"
        inline = self.transcripts
        asked = {"include": "transcript"} if inline else {}
        status, body = await self._get(path, "Get note", asked, allow=(404, 413))
        if status == 413:  # TRANSCRIPT_TOO_LARGE: the note alone, the transcript in pages
            inline = False
            status, body = await self._get(path, "Get note", {}, allow=(404,))
        if status == 404:
            return [], [Skip(note.id, GONE)]
        try:
            meeting = parse_note(body, note.id)
            summary = summary_draft(meeting, self.project)
        except SourceError:
            raise
        except Exception:  # one note cairn fails on must not stop the source for good
            return [], [Skip(note.id, UNREADABLE)]
        if meeting.deleted:
            return None
        if not self.transcripts:
            return [summary], []
        if inline and meeting.transcript is not None:
            items: list[Any] | str = meeting.transcript
            if len(items) > MAX_TRANSCRIPT_PAGES * TRANSCRIPT_PAGE:
                items = _too_long()
        else:
            items = await self._transcript(note.id)
        if isinstance(items, str):  # why there is none
            return [summary], [Skip(note.id, items)]
        try:
            said = turns(items, meeting.owner)
            made = transcript_drafts(summary, meeting, said)
        except SourceError:
            raise
        except Exception:  # its summary is still written
            return [summary], [Skip(note.id, UNREADABLE)]
        if not said:
            return [summary], [Skip(note.id, NO_TRANSCRIPT)]
        return [summary, *made], []

    async def _transcript(self, note_id: str) -> list[Any] | str:
        """Every item of the note's transcript, or why there is none to write."""
        items: list[Any] = []
        cursor: str | None = None
        for _ in range(MAX_TRANSCRIPT_PAGES):
            params = {"page_size": str(TRANSCRIPT_PAGE), **({"cursor": cursor} if cursor else {})}
            status, body = await self._get(
                f"/notes/{note_id}/transcript", "Get transcript", params, allow=(403, 404)
            )
            if status == 404:
                return NO_TRANSCRIPT
            if status == 403:
                if body == "FORBIDDEN":
                    return TRANSCRIPTS_OFF
                raise SourceError(refusal(403, body, "Get transcript", self.variable))
            page, cursor = _page(body, "transcript", "Get transcript")
            items += page
            if len(items) > MAX_TRANSCRIPT_PAGES * TRANSCRIPT_PAGE:  # more than asked a page
                break
            if cursor is None:
                return items
        return _too_long()

    # Requests

    async def _list(self, bound: dict[str, str]) -> list["_Listed"]:
        """Every note List notes gives for `bound` and the config's filter, every page."""
        params = {**bound, **self.filters, "page_size": str(LIST_PAGE)}
        listed: list[_Listed] = []
        cursor: str | None = None
        for _ in range(MAX_LIST_PAGES):
            asked = {**params, "cursor": cursor} if cursor else params
            _, body = await self._get("/notes", "List notes", asked)
            page, cursor = _page(body, "notes", "List notes")
            listed += [_listed(item) for item in page]
            if len(listed) > MAX_LIST_PAGES * LIST_PAGE:  # a page may hold more than asked
                break
            if cursor is None:
                return listed
        raise SourceError(
            f"Granola listed more than {MAX_LIST_PAGES * LIST_PAGE:,} notes in one scan: lower"
            " lookback_days, or limit the source to a folder or space with folder_id or space_id"
        )

    async def _get(
        self, path: str, endpoint: str, params: dict[str, str], allow: tuple[int, ...] = ()
    ) -> tuple[int, Any]:
        """(200, the JSON body), or (a status in `allow`, Granola's error code or None).
        Anything else is a SourceError; _Spent at the deadline."""
        for attempt in range(RETRIES + 1):
            if self._late():
                raise _Spent("its deadline")
            try:
                async with (
                    limit(self.deadline, self._clock),
                    self._http.stream(
                        "GET",
                        self.base_url + path,
                        params=params,
                        headers=self._headers,
                        follow_redirects=False,
                    ) as response,
                ):
                    status = response.status_code
                    if status == 200:
                        return status, _json(await _bounded(response, endpoint), endpoint)
                    code = await _error_code(response)
                    wait = retry_wait(response.headers.get("retry-after"))
            except TimeoutError:
                raise _Spent("its deadline, which an answer still arriving passed") from None
            except httpx.HTTPError as error:
                raise SourceError(
                    f"cannot reach Granola for {endpoint}: {type(error).__name__}"
                ) from None
            if status in allow:
                return status, code
            if status == 429 and attempt < RETRIES:
                if (
                    self.deadline is not None
                    and self._clock() + timedelta(seconds=wait) > self.deadline
                ):
                    raise _Spent("its deadline, which a wait for a 429 would pass")
                await self._sleep(wait)
                continue
            raise SourceError(refusal(status, code, endpoint, self.variable))
        raise AssertionError("unreachable")  # pragma: no cover


def _too_long() -> str:
    """The skip reason of a transcript of more items than MAX_TRANSCRIPT_PAGES full pages."""
    return (
        f"transcript longer than this server reads ({MAX_TRANSCRIPT_PAGES * TRANSCRIPT_PAGE:,}"
        " items): the summary was written"
    )


class _Spent(Exception):
    """The pull's deadline came: what is left waits for the next pull."""


@dataclass(frozen=True)
class _Listed:
    """A note as List notes gives it."""

    id: str
    created: datetime
    updated: datetime
    deleted: bool


def _listed(item: object) -> _Listed:
    if not isinstance(item, dict):
        raise SourceError("Granola's answer to List notes is not the documented shape")
    note_id = item.get("id")
    if not isinstance(note_id, str) or not NOTE_ID.fullmatch(note_id):
        raise SourceError(
            "Granola's answer to List notes holds a note id not of the documented form"
        )
    created, updated = _moment(item.get("created_at")), _moment(item.get("updated_at"))
    deleted = item.get("deleted_at")
    if created is None or updated is None or "deleted_at" not in item:
        raise SourceError("Granola's answer to List notes holds a note without its times")
    if deleted is not None and _moment(deleted) is None:
        raise SourceError("Granola's answer to List notes holds a note without its times")
    return _Listed(note_id, created, updated, deleted is not None)


def _page(body: object, items: str, endpoint: str) -> tuple[list[Any], str | None]:
    """A page's items and the cursor of the next page (None: the last page)."""
    if not isinstance(body, dict) or not isinstance(body.get(items), list):
        raise SourceError(f"Granola's answer to {endpoint} is not the documented shape")
    more, cursor = body.get("hasMore"), body.get("cursor")
    if not isinstance(more, bool):
        raise SourceError(f"Granola's answer to {endpoint} is not the documented shape")
    if more and (not isinstance(cursor, str) or not cursor):
        raise SourceError(f"Granola's answer to {endpoint} has more without a cursor")
    return body[items], cursor if more else None


async def _bounded(response: httpx.Response, endpoint: str) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise SourceError(f"Granola's answer to {endpoint} is larger than this server reads")
    body = bytearray()
    async for part in response.aiter_bytes():
        body += part
        if len(body) > MAX_RESPONSE_BYTES:
            raise SourceError(f"Granola's answer to {endpoint} is larger than this server reads")
    return bytes(body)


def _json(body: bytes, endpoint: str) -> Any:
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise SourceError(f"Granola's answer to {endpoint} is not JSON") from None


async def _error_code(response: httpx.Response) -> str | None:
    """The error's `code` (VALIDATION_ERROR, FORBIDDEN, ...): the only part of an error's
    body cairn reads. Its message is never read, shown or kept."""
    body = bytearray()
    async for part in response.aiter_bytes():
        body += part
        if len(body) > MAX_ERROR_BYTES:
            return None
    try:
        code = json.loads(body).get("code")
    except (ValueError, RecursionError, AttributeError):
        return None
    return code if isinstance(code, str) and ERROR_CODE.fullmatch(code) else None
