"""C3 acceptance tests (frozen): the Granola source, meeting notes read from Granola's REST API.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/sources/granola.py and
src/cairn/sources/__init__.py. The API's request and response shapes are recorded in
docs/granola-api.md; what Granola's documentation does not state is marked there as such, and
the connector depends on none of it (the stand-in below behaves either way where it matters).

The kind
- `KINDS["granola"]` (cairn.sources.granola). It starts off (`starts_off`). Its notes are
  written via "granola".
- The config is a JSON object. `check` returns it as stored, with the defaults filled in, or
  raises ValueError naming the setting at fault, never a value that could be a credential:
  - `key_env` (required): the name of the environment variable that holds the API key. A
    value that is itself a key (it starts "grn_") or looks like a credential is refused.
  - `projects`: a list of at most one project name, where it writes. May be empty while it is off.
  - `base_url`: "https://public-api.granola.ai/v1" unless given; https, or http on this
    machine; no user or password in it.
  - `folder_id` or `space_id` (optional, at most one): only that folder's or that space's
    notes, in the id patterns the API gives (fol_ or spa_ and 14 letters or digits).
  - `transcripts` (default false): also write each meeting's transcript.
  - `settle_minutes` (default 30, 0 to 1440): a note is read only once its `updated_at` is at
    least that old.
  - `lookback_days` (default 30, 1 to 365): how far back the daily rescan looks.
  - No other setting.
- `open(name, config, *, http, env, clock, sleep, deadline, since)` reads the key from `env`
  (a missing or empty variable is a SourceError naming the variable). The key is sent only in
  the `Authorization: Bearer` header and is among the source's `secrets`.

What it reads
- Only notes created at or after `since` (when the source was last switched on), whatever
  their `updated_at`. Each note once: after its draft is written, later edits or a
  regenerated summary at Granola change nothing in cairn (decision 26). A note marked deleted
  (`deleted_at` set) is not written; a note already written stays when Granola deletes it.
  With `folder_id` or `space_id`, only the notes the API lists for it.

How it finds them
- Every pull asks List notes (GET <base_url>/notes) for what changed: `updated_after` a fixed
  overlap before the cursor's mark (on the first pull, `since`), every page followed while
  `hasMore` is true, the filters repeated beside `cursor`, `page_size` 30.
- At most once a day (by the injected clock, recorded in the cursor) a pull also rescans by
  creation: `created_after` the later of `since` and `lookback_days` ago. That finds a note
  that became readable late without its `updated_at` moving.
- The API lists newest first by creation and states no order for ties or under
  `updated_after`: a pull's drafts come in the connector's own order, oldest `created_at`
  first, then by note id.
- The cursor is JSON the connector owns: the mark, when it last rescanned, and short digests of
  the notes it wrote (never an id, a title or a key), kept only while a rescan could still list
  them, so it stays bounded. A note created before the oldest moment a rescan can still reach
  counts as read already, so an old note edited later is not written again once its digest is
  gone. A note newer than `settle_minutes` waits and the mark does not pass it. At the run's
  `deadline` a pull returns what it has; every draft carries a `resume` cursor from which the
  next pull goes on without a draft twice or one skipped.

What it writes, for each note, into the config's project
- `title`: the note's title, or "Untitled meeting". `topic`: the title and the meeting's date
  (YYYY-MM-DD, UTC, from the calendar event's start, else from `created_at`), so each meeting
  has its own page. `text`: Markdown, a first heading with the title, the facts (when it took
  place, in UTC; who attended, by name; the owner, by name), then the summary as Granola gives
  it (`summary_markdown`, else `summary_text`), unchanged. No email address, no private notes.
  `source_ref`: "meeting:granola/<note id>". `label`: "granola".
- With `transcripts`: a second draft, `topic` the first one's plus " transcript", `source_ref`
  ".../transcript", one line per turn (consecutive segments of one speaker joined) with the
  speaker and the start as HH:MM:SS from the meeting's start. Over 1,500 turns it is split into
  parts of at most 1,500 turns (".../transcript/2", topic "... transcript, part 2"). A
  transcript refused (403), absent or deleted: the summary draft is still written and the note
  is reported once as skipped ("no transcript", "transcripts are switched off for this key").
- Get note answering 404 (the summary gone, or deleted between the list and the get): skipped
  ("gone") and treated as written. Get note answering 413 TRANSCRIPT_TOO_LARGE: fetched again
  without the transcript, the transcript then read from the transcript endpoint.

When the provider says no
- 401 or 403 on listing: SourceError naming the variable, never the key. 429: retried at most
  3 times through the injected `sleep` (Retry-After when given, else a short fixed wait), then
  SourceError. 5xx, a timeout, a body not of the documented shape: SourceError. No message
  holds a response body; a failed pull returns nothing, so no cursor moves, and the next pull
  writes what it did not, and nothing twice.

Together with the framework: off when added; switched on with a project, `run_source` writes
the settled notes created since, one page a meeting; again, nothing new; the audit log's
"source.synced" counts them; the database, the audit log and the command line's output
(`source-add --kind granola`, `source-enable --project`, `source-sync`) hold no key.
"""

import asyncio
import hashlib
import itertools
import json
import random
import re
import sqlite3
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import cairn.sources.runner
from cairn.server import main
from cairn.sources import KINDS, SourceError, run_source
from cairn.store import Store

KEY_ENV = "GRANOLA_C3_KEY"
KEY = "grn_" + "c3" + "MadeUp" + "Key0f3a9c7e"  # never a real key
WRONG_KEY = "grn_" + "c3" + "Wrong" + "Key1b2d"
ENV = {KEY_ENV: KEY}
CANARY = "c3-" + "body-" + "canary-7d1e"  # in every error body the stand-in sends
BASE_URL = "https://granola.example.test/v1"
DEFAULT_BASE_URL = "https://public-api.granola.ai/v1"
FOLDER = "fol_" + "C3Folder000001"
OTHER_FOLDER = "fol_" + "C3Folder000002"
SPACE = "spa_" + "C3Space0000001"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=10)  # when the source was switched on
HOUR, DAY = timedelta(hours=1), timedelta(days=1)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def z(moment: datetime) -> str:
    """A timestamp as the API writes one: UTC with "Z" (fractions only when there are any)."""
    spec = "microseconds" if moment.microsecond else "seconds"
    return moment.astimezone(UTC).isoformat(timespec=spec).replace("+00:00", "Z")


def parsed(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def nid(n: int) -> str:
    return f"not_c3{n:012d}"


def ref(n: int) -> str:
    return f"meeting:granola/{nid(n)}"


def refs(pulled) -> list[str]:
    return [draft.source_ref for draft in pulled.drafts]


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def segment(name: str, text: str, start: datetime, seconds: int = 3, **speaker) -> dict:
    return {
        "speaker": {"source": "microphone", "name": name, **speaker},
        "text": text,
        "start_time": z(start),
        "end_time": z(start + timedelta(seconds=seconds)),
    }


@dataclass
class Meeting:
    """One note at the stand-in, with everything Get note can say about it."""

    id: str
    title: str | None
    created: datetime
    updated: datetime
    summary_markdown: str | None
    summary_text: str
    owner: tuple = ("Dee Quinn", "dee@example.test")
    attendees: list = field(
        default_factory=lambda: [
            ("Ada Park", "ada@example.test"),
            ("Brook Lane", "brook@example.test"),
        ]
    )
    calendar: dict | None = None
    private: str | None = "Dee's private aside: ask about the budget."
    segments: list | None = None
    transcript: str = "present"  # "present"; "empty": []; "null": null, then an empty page;
    # "deleted": null, then 404 from the transcript endpoint
    deleted: datetime | None = None
    folder: str | None = None
    space: str | None = None
    visible: bool = True  # False: no summary yet, so the API shows nothing of it
    gone: bool = False  # Get note answers 404 although List notes still lists it


class FakeGranola:
    """List notes, Get note and Get transcript as docs/granola-api.md describes them, as an
    httpx mock. It checks the bearer key, honours the filters, pages with an opaque cursor that
    holds the filters it was made for and dies when the pull ends (`new_pull`), enforces the
    documented page sizes and id patterns, and can be told to treat bounds as exclusive,
    shuffle what it lists, send short and empty pages that still have more, list deleted
    notes, refuse transcripts, and answer 401, 403, 429, 500, a timeout or a malformed body.
    Every request is kept with the clock's time, its pull, its parameters and its status."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.tick = timedelta(0)  # the clock moves this much with every request
        self.notes: dict[str, Meeting] = {}
        self.requests: list[SimpleNamespace] = []
        self.slept: list[float] = []
        self.gen = 0
        self.exclusive = False  # created_after and updated_after: strictly after
        self.shuffle = False  # list in an order of its own, not newest first
        self.short_pages = False  # an empty first page, then 7 a page, while hasMore
        self.sees_deleted = False  # a key with access to all workspace notes
        self.transcripts_off = False  # an admin turned off transcript access over the API
        self.inline_limit = 1_000  # transcript items Get note returns inline, at most
        self.forced: list[tuple[str, int | str, dict]] = []  # (endpoint, status, headers)
        self.always: dict[str, tuple[int | str, dict]] = {}

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def new_pull(self) -> None:
        self.gen += 1

    def note(self, n: int, title: str | None = "Payments weekly", **given) -> Meeting:
        created = given.pop("created", SINCE + DAY)
        given.setdefault("updated", created + timedelta(minutes=20))
        summary = f"### Decisions\n\n- Item {n}: Ada moves the release to Friday.\n"
        given.setdefault("summary_markdown", summary)
        given.setdefault("summary_text", f"Decisions. Item {n}: Ada moves the release to Friday.")
        meeting = Meeting(id=nid(n), title=title, created=created, **given)
        self.notes[meeting.id] = meeting
        return meeting

    # What the tests ask about the requests

    def of_pull(self, gen: int | None = None) -> list[SimpleNamespace]:
        gen = self.gen if gen is None else gen
        return [r for r in self.requests if r.gen == gen]

    def lists(self, gen: int | None = None) -> list[SimpleNamespace]:
        return [r for r in self.of_pull(gen) if r.endpoint == "list"]

    def rescanned(self, gen: int | None = None) -> bool:
        return any("created_after" in r.params for r in self.lists(gen))

    # The API

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.clock.now += self.tick
        path = request.url.path
        rest = path[len("/v1") :] if path.startswith("/v1/") else ""
        if rest == "/notes":
            endpoint = "list"
        elif rest.startswith("/notes/") and rest.endswith("/transcript"):
            endpoint = "transcript"
        elif rest.startswith("/notes/"):
            endpoint = "get"
        else:
            endpoint = "other"
        record = SimpleNamespace(
            at=self.clock(),
            gen=self.gen,
            url=str(request.url),
            endpoint=endpoint,
            note=rest.split("/")[2] if rest.startswith("/notes/") else None,
            params=dict(request.url.params),
            auth=request.headers.get("authorization"),
            status=None,
            count=None,
        )
        self.requests.append(record)
        response = self._answer(request, endpoint, record)
        record.status = response.status_code
        return response

    def _error(self, request: httpx.Request, status: int, code: str, **headers) -> httpx.Response:
        body = {
            "code": code,
            "message": f"{CANARY}: refused {request.headers.get('authorization')}",
            "requestId": f"{CANARY}-request",
            "timestamp": z(self.clock()),
        }
        return httpx.Response(status, json=body, headers=headers)

    def _forced(self, request: httpx.Request, status: int | str, headers: dict) -> httpx.Response:
        if status == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if status == "not json":
            return httpx.Response(200, text=f"<html>{CANARY}</html>")
        if status == "wrong shape":
            return httpx.Response(200, json={"notes": CANARY, "hasMore": False, "cursor": None})
        if status == "note without created_at":
            item = {"id": nid(999), "object": "note", "title": CANARY, "owner": {"name": None}}
            return httpx.Response(200, json={"notes": [item], "hasMore": False, "cursor": None})
        if status == "array":
            return httpx.Response(200, json=[CANARY])
        codes = {401: "INVALID_API_KEY", 403: "FORBIDDEN", 429: "RATE_LIMITED"}
        return self._error(request, status, codes.get(status, "INTERNAL_ERROR"), **headers)

    def _answer(self, request: httpx.Request, endpoint: str, record) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return self._error(request, 401, "INVALID_API_KEY")
        for index, (which, status, headers) in enumerate(self.forced):
            if which == endpoint:
                del self.forced[index]
                return self._forced(request, status, headers)
        if endpoint in self.always:
            return self._forced(request, *self.always[endpoint])
        if endpoint == "list":
            return self._list(request, record)
        if endpoint in ("get", "transcript"):
            note_id = record.note or ""
            if not _NOTE_ID.fullmatch(note_id):
                return self._error(request, 400, "VALIDATION_ERROR")
            meeting = self.notes.get(note_id.lower())
            readable = meeting is not None and meeting.visible and not meeting.gone
            if readable and meeting.deleted is not None and not self.sees_deleted:
                readable = False
            if not readable:
                return self._error(request, 404, "NOTE_NOT_FOUND")
            if endpoint == "get":
                return self._get(request, meeting)
            return self._transcript(request, meeting)
        return self._error(request, 404, "NOT_FOUND")

    def _cursor(self, offset: int, filters: dict) -> str:
        state = json.dumps({"g": self.gen, "o": offset, "f": filters}, sort_keys=True)
        return urlsafe_b64encode(state.encode()).decode()

    def _offset(self, cursor: str | None, filters: dict) -> int | None:
        if cursor is None:
            return 0
        try:
            state = json.loads(urlsafe_b64decode(cursor.encode()))
        except ValueError:
            return None
        if state.get("g") != self.gen or state.get("f") != filters:
            return None  # from another pull, or the filters were not repeated
        return state["o"]

    def _after(self, moment: datetime, bound: str) -> bool:
        limit = parsed(bound)
        return moment > limit if self.exclusive else moment >= limit

    def _list(self, request: httpx.Request, record) -> httpx.Response:
        params = record.params
        names = ("created_after", "created_before", "updated_after", "folder_id", "space_id")
        filters = {name: params[name] for name in names if name in params}
        try:
            size = int(params.get("page_size", "10"))
            for name in ("created_after", "created_before", "updated_after"):
                if name in filters:
                    parsed(filters[name])
        except ValueError:
            return self._error(request, 400, "VALIDATION_ERROR")
        if not 1 <= size <= 30:
            return self._error(request, 400, "VALIDATION_ERROR")
        if "folder_id" in filters and not _FOLDER_ID.fullmatch(filters["folder_id"]):
            return self._error(request, 400, "VALIDATION_ERROR")
        if "space_id" in filters and not _SPACE_ID.fullmatch(filters["space_id"]):
            return self._error(request, 400, "VALIDATION_ERROR")
        offset = self._offset(params.get("cursor"), filters)
        if offset is None:
            return self._error(request, 400, "VALIDATION_ERROR")
        found = []
        for meeting in self.notes.values():
            if not meeting.visible or (meeting.deleted is not None and not self.sees_deleted):
                continue
            if "created_after" in filters and not self._after(
                meeting.created, filters["created_after"]
            ):
                continue
            if "created_before" in filters and not meeting.created < parsed(
                filters["created_before"]
            ):
                continue
            if "updated_after" in filters and not self._after(
                meeting.updated, filters["updated_after"]
            ):
                continue
            if (
                "folder_id" in filters
                and (meeting.folder or "").lower() != filters["folder_id"].lower()
            ):
                continue
            if (
                "space_id" in filters
                and (meeting.space or "").lower() != filters["space_id"].lower()
            ):
                continue
            found.append(meeting)
        found.sort(key=lambda m: (m.created, m.id), reverse=True)  # ties: by id, descending
        if self.shuffle:
            found.sort(key=lambda m: hashlib.sha256(f"order:{m.id}".encode()).hexdigest())
        if self.short_pages:
            size = min(size, 7) if "cursor" in params else 0
        page = found[offset : offset + size]
        more = offset + size < len(found)
        if self.shuffle:
            random.Random(offset).shuffle(page)
        record.count = len(page)
        body = {
            "notes": [self._summary(m) for m in page],
            "hasMore": more,
            "cursor": self._cursor(offset + size, filters) if more else None,
        }
        return httpx.Response(200, json=body)

    def _summary(self, meeting: Meeting) -> dict:
        return {
            "id": meeting.id,
            "object": "note",
            "title": meeting.title,
            "owner": {"name": meeting.owner[0], "email": meeting.owner[1]},
            "created_at": z(meeting.created),
            "updated_at": z(meeting.updated),
            "deleted_at": z(meeting.deleted) if meeting.deleted else None,
        }

    def _get(self, request: httpx.Request, meeting: Meeting) -> httpx.Response:
        include = request.url.params.get("include")
        if include not in (None, "transcript"):
            return self._error(request, 400, "VALIDATION_ERROR")
        transcript = None
        if include == "transcript" and not self.transcripts_off:
            if meeting.transcript == "present":
                if len(meeting.segments or []) > self.inline_limit:
                    return self._error(request, 413, "TRANSCRIPT_TOO_LARGE")
                transcript = list(meeting.segments or [])
            elif meeting.transcript == "empty":
                transcript = []
        folders = [
            {"id": meeting.folder, "object": "folder", "name": "Weekly", "parent_folder_id": None}
        ]
        body = {
            **self._summary(meeting),
            "web_url": f"https://notes.granola.example.test/d/{meeting.id}",
            "calendar_event": meeting.calendar,
            "attachments": [],
            "attendees": [{"name": name, "email": mail} for name, mail in meeting.attendees],
            "folder_membership": folders if meeting.folder else [],
            "space_membership": [{"id": meeting.space, "object": "space", "name": "Team"}]
            if meeting.space
            else [],
            "summary_text": meeting.summary_text,
            "summary_markdown": meeting.summary_markdown,
            "private_notes_text": meeting.private,
            "private_notes_markdown": f"**{meeting.private}**" if meeting.private else None,
            "transcript": transcript,
        }
        return httpx.Response(200, json=body)

    def _transcript(self, request: httpx.Request, meeting: Meeting) -> httpx.Response:
        if self.transcripts_off:
            return self._error(request, 403, "FORBIDDEN")
        if meeting.transcript == "deleted":
            return self._error(request, 404, "NOT_FOUND")
        try:
            size = int(request.url.params.get("page_size", "50"))
        except ValueError:
            return self._error(request, 400, "VALIDATION_ERROR")
        if not 1 <= size <= 100:
            return self._error(request, 400, "VALIDATION_ERROR")
        items = list(meeting.segments or []) if meeting.transcript == "present" else []
        offset = self._offset(request.url.params.get("cursor"), {"note": meeting.id})
        if offset is None:
            return self._error(request, 400, "VALIDATION_ERROR")
        more = offset + size < len(items)
        body = {
            "transcript": items[offset : offset + size],
            "hasMore": more,
            "cursor": self._cursor(offset + size, {"note": meeting.id}) if more else None,
        }
        return httpx.Response(200, json=body)


_NOTE_ID = re.compile(r"[Nn][Oo][Tt]_[a-zA-Z0-9]{14}")
_FOLDER_ID = re.compile(r"[Ff][Oo][Ll]_[a-zA-Z0-9]{14}")
_SPACE_ID = re.compile(r"[Ss][Pp][Aa]_[a-zA-Z0-9]{14}")


@pytest.fixture
def kind():
    return KINDS["granola"]


@pytest.fixture
def fake(kind):
    return FakeGranola(Clock(NOW))


def config(**settings) -> dict:
    return {"key_env": KEY_ENV, "projects": ["meetings"], "base_url": BASE_URL, **settings}


def opened(kind, fake, *, since=SINCE, deadline=None, env=ENV, **settings):
    checked = kind.check(config(**settings))
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return kind.open(
        "granola", checked, http=http, env=env, clock=fake.clock, sleep=fake.sleep,
        deadline=deadline, since=since,
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
    with pytest.raises(SourceError) as failed:
        pull(fake, source, cursor)
    return failed.value


# The kind and its config


def test_the_kind_starts_off_and_its_config_has_these_defaults(kind):
    assert kind.starts_off is True and kind.via == "granola"
    checked = kind.check({"key_env": KEY_ENV, "projects": []})
    assert (checked["key_env"], checked["projects"]) == (KEY_ENV, [])
    assert checked["base_url"] == DEFAULT_BASE_URL
    assert checked["transcripts"] is False
    assert (checked["settle_minutes"], checked["lookback_days"]) == (30, 30)
    assert checked.get("folder_id") is None and checked.get("space_id") is None
    assert kind.check(checked) == checked  # what is stored is checked again on switch-on

    given = {
        "key_env": KEY_ENV,
        "projects": ["meetings"],
        "base_url": BASE_URL,
        "folder_id": FOLDER,
        "transcripts": True,
        "settle_minutes": 0,
        "lookback_days": 365,
    }
    kept = kind.check(given)
    for setting, value in given.items():
        if setting == "folder_id":  # ids are case-insensitive
            assert kept[setting].lower() == value.lower()
        else:
            assert kept[setting] == value, setting
    assert kind.check(kept) == kept
    for accepted in (
        {"space_id": SPACE},
        {"folder_id": "FOL_" + "C3Folder000001"},  # ids are case-insensitive
        {"settle_minutes": 1440, "lookback_days": 1},
        {"base_url": "http://127.0.0.1:8123/v1"},
        {"base_url": "http://localhost:8123/v1"},
    ):
        assert kind.check(config(**accepted))["projects"] == ["meetings"], accepted


@pytest.mark.parametrize(
    ("setting", "settings"),
    [
        ("key_env", {"key_env": None}),
        ("key_env", {"key_env": 7}),
        ("key_env", {"key_env": ""}),
        ("projects", {"projects": ["meetings", "planning"]}),
        ("projects", {"projects": "meetings"}),
        ("base_url", {"base_url": "http://granola.example.test/v1"}),
        ("base_url", {"base_url": "ftp://granola.example.test/v1"}),
        ("base_url", {"base_url": "https://ada:hunter2-c3@granola.example.test/v1"}),
        ("folder_id", {"folder_id": "fol_short"}),
        ("folder_id", {"folder_id": "spa_" + "C3Space0000001"}),
        ("space_id", {"space_id": "spa_" + "C3Space00000012"}),
        ("transcripts", {"transcripts": "yes"}),
        ("settle_minutes", {"settle_minutes": -1}),
        ("settle_minutes", {"settle_minutes": 1441}),
        ("settle_minutes", {"settle_minutes": "30"}),
        ("lookback_days", {"lookback_days": 0}),
        ("lookback_days", {"lookback_days": 366}),
        ("color", {"color": "blue"}),
    ],
)
def test_check_refuses_a_bad_setting_by_its_name(kind, setting, settings):
    given = config(**settings)
    if settings.get("key_env", "") is None:
        del given["key_env"]
    with pytest.raises(ValueError) as refused:
        kind.check(given)
    assert setting in str(refused.value)
    assert "hunter2-c3" not in str(refused.value)


def test_check_refuses_both_a_folder_and_a_space_and_a_config_that_is_not_an_object(kind):
    with pytest.raises(ValueError) as refused:
        kind.check(config(folder_id=FOLDER, space_id=SPACE))
    assert "folder_id" in str(refused.value) or "space_id" in str(refused.value)
    for odd in (None, [], "key_env"):
        with pytest.raises(ValueError):
            kind.check(odd)


def test_a_key_in_the_config_is_refused_and_never_repeated(kind, tmp_path):
    token = "ghp_" + "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zAaB3d"  # looks like a credential
    for leaked in (
        config(key_env=KEY),
        config(key_env=token),
        config(api_key=KEY),
    ):
        with pytest.raises(ValueError) as refused:
            kind.check(leaked)
        said = str(refused.value)
        assert KEY not in said and token not in said
        if "api_key" not in leaked:
            assert "key_env" in said

    store = Store(tmp_path / "cairn.db")
    try:
        store.create_project("meetings")
        with pytest.raises(ValueError):
            store.add_source("granola", "granola", config(key_env=KEY))
        assert store.list_sources() == []
    finally:
        store.close()
    assert KEY not in dump(tmp_path / "cairn.db")


def test_the_key_comes_from_the_environment_and_goes_only_in_the_bearer_header(kind, fake):
    for env in ({}, {KEY_ENV: ""}, {"ANOTHER_VARIABLE": KEY}):
        with pytest.raises(SourceError) as missing:
            opened(kind, fake, env=env)
        assert KEY_ENV in str(missing.value) and KEY not in str(missing.value)

    fake.note(1, created=SINCE + DAY)
    source = opened(kind, fake)
    assert KEY in source.secrets
    assert refs(pull(fake, source)) == [ref(1)]
    assert fake.requests
    for request in fake.requests:
        assert request.auth == f"Bearer {KEY}"
        assert KEY not in request.url and KEY.lower() not in request.url.lower()
        assert request.url.startswith(BASE_URL + "/notes")

    default = KINDS["granola"].check({"key_env": KEY_ENV, "projects": ["meetings"]})
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    plain = kind.open(
        "granola", default, http=http, env=ENV, clock=fake.clock, sleep=fake.sleep,
        deadline=None, since=SINCE,
    )  # fmt: skip
    fake.requests.clear()
    assert refs(pull(fake, plain)) == [ref(1)]
    assert all(r.url.startswith(DEFAULT_BASE_URL + "/notes") for r in fake.requests)


# What it reads, and how it finds it


@pytest.mark.parametrize("exclusive", [False, True], ids=["inclusive-bounds", "exclusive-bounds"])
def test_the_first_pull_writes_what_was_created_since_it_was_switched_on_oldest_first(
    kind, fake, exclusive
):
    fake.exclusive = exclusive
    tie = SINCE + 2 * DAY
    fake.note(1, "Before it was on", created=SINCE - HOUR, updated=SINCE + HOUR)
    fake.note(2, "Long before", created=SINCE - 3 * DAY, updated=NOW - 2 * HOUR)
    fake.note(3, "Right as it was switched on", created=SINCE, updated=SINCE + 5 * HOUR / 60)
    fake.note(5, "Tied, second by id", created=tie)
    fake.note(4, "Tied, first by id", created=tie)
    fake.note(6, "Just settled", created=SINCE + DAY, updated=NOW - timedelta(minutes=30))
    fake.note(7, "Still being edited", created=NOW - 3 * HOUR, updated=NOW - 10 * HOUR / 60)

    read = reader(kind, fake)
    first = read()
    assert refs(first) == [ref(3), ref(6), ref(4), ref(5)]
    assert {d.project for d in first.drafts} == {"meetings"}
    asked = [r.params["updated_after"] for r in fake.lists() if "updated_after" in r.params]
    assert asked and SINCE - DAY <= parsed(asked[0]) <= SINCE  # the first pull: since

    fake.clock.now = NOW + HOUR  # the edited note has settled meanwhile
    second = read(first.cursor)
    assert refs(second) == [ref(7)]
    assert read(second.cursor).drafts == []


def test_a_note_still_being_edited_waits_and_the_mark_does_not_pass_it(kind, fake):
    fake.note(1, "Settled long ago", created=NOW - 2 * DAY, updated=NOW - DAY)
    edited = fake.note(2, "Long edit", created=NOW - HOUR, updated=NOW - 40 * HOUR / 60)
    read = reader(kind, fake, settle_minutes=600)  # 10 hours
    cursor, written = None, []
    for hour in range(14):
        fake.clock.now = NOW + hour * HOUR
        got = read(cursor)
        cursor = got.cursor
        written += [(hour, r) for r in refs(got)]
        if hour <= 10:  # until it is written, every look at what changed reaches it
            for request in fake.lists():
                if "updated_after" in request.params:
                    assert parsed(request.params["updated_after"]) <= edited.updated, hour
    assert written == [(0, ref(1)), (10, ref(2))]  # settled at 9:20 after NOW: read at 10


def test_seventy_notes_come_over_three_pages_and_any_order_gives_the_same_drafts(kind, fake):
    def stocked() -> FakeGranola:
        stand_in = FakeGranola(Clock(NOW))
        for n in range(1, 71):
            stand_in.note(n, f"Standup {n}", created=SINCE + n * HOUR, folder=FOLDER)
        for n in range(71, 76):
            stand_in.note(n, f"Elsewhere {n}", created=SINCE + n * HOUR, folder=OTHER_FOLDER)
        return stand_in

    seen = {}
    for mode in ("documented", "shuffled", "short pages"):
        stand_in = stocked()
        stand_in.shuffle = mode == "shuffled"
        stand_in.short_pages = mode == "short pages"
        source = opened(kind, stand_in, folder_id=FOLDER)
        got = pull(stand_in, source)
        assert refs(got) == [ref(n) for n in range(1, 71)], mode
        seen[mode] = [(d.source_ref, d.title, d.topic, d.text, d.project) for d in got.drafts]

        lists = stand_in.lists()
        assert all(r.status == 200 for r in lists), mode
        for request in lists:
            assert request.params["page_size"] == "30"
            assert request.params["folder_id"].lower() == FOLDER.lower()
            assert "updated_after" in request.params or "created_after" in request.params
        for by in ("updated_after", "created_after"):
            scan = [r for r in lists if by in r.params]
            if not scan:
                continue
            assert "cursor" not in scan[0].params
            assert all("cursor" in r.params for r in scan[1:])
            first = {k: v for k, v in scan[0].params.items() if k != "cursor"}
            assert all({k: v for k, v in r.params.items() if k != "cursor"} == first for r in scan)
            if mode == "documented":
                assert [r.count for r in scan] == [30, 30, 10]
    assert seen["documented"] == seen["shuffled"] == seen["short pages"]


def test_a_source_limited_to_a_space_reads_only_what_the_api_lists_for_it(kind, fake):
    for n in (1, 2, 3):
        fake.note(n, f"Team sync {n}", created=SINCE + n * HOUR, space=SPACE)
    fake.note(4, "Private sync", created=SINCE + 4 * HOUR)
    got = pull(fake, opened(kind, fake, space_id=SPACE))
    assert refs(got) == [ref(1), ref(2), ref(3)]
    assert all(r.params["space_id"].lower() == SPACE.lower() for r in fake.lists())


def test_a_note_is_read_once_whatever_happens_to_it_later(kind, fake):
    first_note = fake.note(1, "Payments weekly", created=NOW - 2 * DAY)
    second_note = fake.note(2, "Hiring sync", created=NOW - DAY)
    read = reader(kind, fake, lookback_days=7)
    written = Counter()
    got = read()
    written.update(refs(got))
    assert refs(got) == [ref(1), ref(2)]

    first_note.summary_markdown = "### Decisions\n\n- Changed after it was written.\n"
    first_note.updated = NOW + HOUR  # edited
    second_note.summary_markdown = "### Decisions\n\n- Regenerated.\n"  # updated_at unmoved
    cursor = got.cursor
    for hours in (3, 30):
        fake.clock.now = NOW + hours * HOUR
        got = read(cursor)
        cursor = got.cursor
        assert got.drafts == [], hours

    for day in range(2, 13):  # well past lookback_days, a pull a day
        fake.clock.now = NOW + day * DAY
        got = read(cursor)
        cursor = got.cursor
        written.update(refs(got))
    edited = fake.clock.now + 10 * HOUR / 60
    first_note.updated = edited  # edited again, long after it was written
    first_note.summary_markdown = "### Decisions\n\n- Changed once more.\n"
    fake.note(3, "Retro", created=edited)
    fake.clock.now += 2 * HOUR
    got = read(cursor)
    written.update(refs(got))
    assert refs(got) == [ref(3)]
    assert written == Counter({ref(1): 1, ref(2): 1, ref(3): 1})


def test_the_cursor_holds_no_id_title_or_key_and_stays_bounded(kind, fake):
    read = reader(kind, fake, since=NOW, lookback_days=7)
    spacing = 56 * DAY / 400
    cursor, written, rescans, lengths, added = None, Counter(), [], {}, 0
    for step in range(1, 56 * 4 + 2):  # a pull every six hours for eight weeks
        fake.clock.now = NOW + step * 6 * HOUR
        while added < 400 and NOW + spacing * (added + 1) <= fake.clock.now - HOUR:
            added += 1
            created = NOW + spacing * added
            fake.note(added, f"Quill review {added}", created=created, updated=created + HOUR / 2)
        got = read(cursor)
        written.update(refs(got))
        cursor = got.cursor
        json.loads(cursor)  # opaque, but JSON
        shown = cursor.lower()
        assert "not_" not in shown and KEY.lower() not in shown
        assert not any(m.title in cursor for m in fake.notes.values())
        if fake.rescanned():
            rescans.append(fake.clock.now)
        if step == 14 * 4:
            lengths["two weeks"] = len(cursor)
    assert added == 400 and written == Counter({ref(n): 1 for n in range(1, 401)})
    assert len(cursor) <= 1.5 * lengths["two weeks"] + 200  # it did not grow with the notes
    changed = [r for r in fake.lists() if "updated_after" in r.params]
    assert changed and fake.clock.now - parsed(changed[-1].params["updated_after"]) < 2 * DAY
    assert len(rescans) >= 28
    assert all(later - earlier >= DAY for earlier, later in itertools.pairwise(rescans))


def test_a_note_readable_late_with_an_old_updated_at_is_found_by_the_daily_rescan(kind, fake):
    fake.note(1, "Kickoff", created=NOW - 2 * DAY)
    late = fake.note(2, "Shared later", created=NOW - 5 * DAY, updated=NOW - 5 * DAY)
    late.visible = False  # its summary is generated, or it is shared, two hours from now
    read = reader(kind, fake)
    cursor, written, rescans = None, {}, []
    for hour in range(61):
        fake.clock.now = NOW + hour * HOUR
        if hour == 2:
            late.visible = True
        got = read(cursor)
        cursor = got.cursor
        if fake.rescanned():
            rescans.append(hour)
        for found in refs(got):
            written.setdefault(found, []).append(hour)
    assert written[ref(1)] == [0]
    found_by = min(hour for hour in rescans if hour >= 2)
    assert written[ref(2)] == [found_by]  # by the first rescan after it appeared, not before
    assert found_by - 2 <= 26
    assert len(rescans) >= 2 and all(b - a >= 24 for a, b in itertools.pairwise(rescans))


@pytest.mark.parametrize(
    ("since", "lookback_days"),
    [
        (NOW - 10 * DAY, 30),  # since is the later
        (NOW - 100 * DAY, 30),  # lookback_days ago is the later
    ],
)
def test_the_rescan_looks_back_lookback_days_and_never_before_since(
    kind, fake, since, lookback_days
):
    read = reader(kind, fake, since=since, lookback_days=lookback_days)
    cursor, checked = None, 0
    for hour in range(30):
        fake.clock.now = NOW + hour * HOUR
        cursor = read(cursor).cursor
        for request in fake.lists():
            if "created_after" in request.params:
                expected = max(since, fake.clock.now - lookback_days * DAY)
                assert expected - DAY <= parsed(request.params["created_after"]) <= expected
                checked += 1
    assert checked


# What it writes


def test_the_draft_says_what_the_meeting_was_without_emails_or_private_notes(kind, fake):
    summary = (
        "### Decisions\n\n- Ship the refund fix on Friday.\n- Brook owns the retry queue.\n\n"
        "### Next steps\n\n1. Ada drafts the rollout note.\n2. Cy checks the *ledger* totals.\n"
    )
    fake.note(
        1,
        "Payments weekly",
        created=datetime(2026, 11, 10, 21, 0, tzinfo=UTC),  # the evening before
        updated=datetime(2026, 11, 12, 1, 0, tzinfo=UTC),
        summary_markdown=summary,
        summary_text="Decisions: ship the refund fix on Friday.",
        owner=("Dee Quinn", "dee@example.test"),
        attendees=[
            ("Ada Park", "ada@example.test"),
            ("Brook Lane", "brook@example.test"),
            (None, "cy@example.test"),
        ],
        calendar={
            "event_title": "Payments weekly",
            "invitees": [{"email": "ada@example.test"}, {"email": "cy@example.test"}],
            "organiser": "dee@example.test",
            "calendar_event_id": "c3event0001_20261111T223000Z",
            "scheduled_start_time": "2026-11-12T00:30:00+02:00",  # 22:30 UTC the day before
            "scheduled_end_time": "2026-11-12T01:00:00+02:00",
        },
    )
    fake.note(
        2,
        "Payments weekly",
        created=datetime(2026, 11, 4, 20, 0, tzinfo=UTC),
        calendar={
            "event_title": "Payments weekly",
            "invitees": [],
            "organiser": None,
            "calendar_event_id": None,
            "scheduled_start_time": "2026-11-04T22:30:00Z",
            "scheduled_end_time": "2026-11-04T23:00:00Z",
        },
    )
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(2), ref(1)]
    earlier, meeting = got.drafts
    assert (meeting.title, meeting.label, meeting.project) == (
        "Payments weekly",
        "granola",
        "meetings",
    )
    assert meeting.source_ref == f"meeting:granola/{nid(1)}"
    assert "Payments weekly" in meeting.topic and "2026-11-11" in meeting.topic
    assert "2026-11-12" not in meeting.topic and "2026-11-10" not in meeting.topic
    assert "2026-11-04" in earlier.topic and earlier.topic != meeting.topic  # a page a meeting

    text = meeting.text
    assert text.startswith("# Payments weekly\n")
    assert "2026-11-11" in text and "22:30" in text  # when, in UTC
    for name in ("Ada Park", "Brook Lane", "Dee Quinn"):
        assert name in text
    assert summary in text  # the summary, unchanged
    assert text.index("Ada Park") < text.index(summary)
    for draft in got.drafts:
        for value in (draft.text, draft.title, draft.topic):
            assert "@" not in value and "example.test" not in value
            assert "private aside" not in value and "budget" not in value


def test_an_untitled_note_and_a_note_with_a_plain_summary(kind, fake):
    fake.note(1, None, created=datetime(2026, 11, 6, 15, 0, tzinfo=UTC))
    fake.note(
        2,
        "Vendor call",
        created=datetime(2026, 11, 7, 15, 0, tzinfo=UTC),
        summary_markdown=None,
        summary_text="Brook asked the vendor for a refund window of five days.",
    )
    untitled, plain = pull(fake, opened(kind, fake)).drafts
    assert untitled.title == "Untitled meeting"
    assert untitled.text.startswith("# Untitled meeting\n")
    assert "Untitled meeting" in untitled.topic and "2026-11-06" in untitled.topic
    assert "Brook asked the vendor for a refund window of five days." in plain.text
    assert "Vendor call" in plain.topic and "2026-11-07" in plain.topic


def meeting_with_transcript(fake, n: int, start: datetime, title="Payments weekly") -> Meeting:
    segments = [
        segment("Ada Park", "Morning, everyone.", start, attribution="me"),
        segment(
            "Ada Park", "Let's start with refunds.", start + timedelta(seconds=3), attribution="me"
        ),
        segment(
            "Brook Lane",
            "The retry queue is green.",
            start + timedelta(seconds=90),
            attribution="them",
        ),
        segment(
            "Ada Park",
            "Good, then we ship Friday.",
            start + timedelta(seconds=3723),
            attribution="me",
        ),
    ]
    return fake.note(n, title, created=start, segments=segments)


def test_transcripts_are_left_out_unless_the_config_asks_for_them(kind, fake):
    meeting_with_transcript(fake, 1, SINCE + DAY)
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(1)]
    assert not any(r.endpoint == "transcript" for r in fake.requests)
    assert not any("include" in r.params for r in fake.requests)


def test_a_transcript_is_a_line_a_turn_with_its_speaker_and_time(kind, fake):
    start = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    meeting_with_transcript(fake, 1, start)
    got = pull(fake, opened(kind, fake, transcripts=True))
    assert sorted(refs(got)) == [ref(1), ref(1) + "/transcript"]
    summary = next(d for d in got.drafts if d.source_ref == ref(1))
    (said,) = [d for d in got.drafts if d.source_ref == ref(1) + "/transcript"]
    assert said.topic == summary.topic + " transcript"
    assert (said.label, said.project) == ("granola", "meetings")
    assert said.text.startswith("#")

    def line_with(words: str) -> str:
        (line,) = [line for line in said.text.splitlines() if words in line]
        return line

    opening = line_with("Morning, everyone.")
    assert "Let's start with refunds." in opening  # one speaker's segments, one turn
    assert opening.index("Morning") < opening.index("Let's start")
    assert "Ada Park" in opening and "00:00:00" in opening
    reply = line_with("The retry queue is green.")
    assert "Brook Lane" in reply and "00:01:30" in reply
    closing = line_with("Good, then we ship Friday.")
    assert "Ada Park" in closing and "01:02:03" in closing
    assert len({opening, reply, closing}) == 3
    assert "@" not in said.text


def test_a_long_transcript_is_split_into_parts_and_read_past_the_inline_limit(kind, fake):
    start = SINCE + DAY
    names = ("Ada Park", "Brook Lane")
    segments = [
        segment(names[k % 2], f"Point {k:04d} about the ledger.", start + timedelta(seconds=k))
        for k in range(3_200)
    ]
    fake.note(1, "Planning offsite", created=start, segments=segments)
    fake.inline_limit = 500  # Get note with the transcript inline answers 413
    got = pull(fake, opened(kind, fake, transcripts=True))
    summary = next(d for d in got.drafts if d.source_ref == ref(1))
    parts = [d for d in got.drafts if d.source_ref != ref(1)]
    base = ref(1) + "/transcript"
    assert [d.source_ref for d in parts] == [base, base + "/2", base + "/3"]
    assert [d.topic for d in parts] == [
        summary.topic + " transcript",
        summary.topic + " transcript, part 2",
        summary.topic + " transcript, part 3",
    ]
    for part, (low, high) in zip(parts, [(0, 1500), (1500, 3000), (3000, 3200)], strict=True):
        held = {k for k in range(3_200) if f"Point {k:04d} " in part.text}
        assert held == set(range(low, high))

    gets = [r for r in fake.requests if r.endpoint == "get"]
    if any(r.status == 413 for r in gets):  # asked inline: then without it, and paged
        too_large = next(i for i, r in enumerate(gets) if r.status == 413)
        assert any("include" not in r.params and r.status == 200 for r in gets[too_large:])
    pages = [r for r in fake.requests if r.endpoint == "transcript"]
    assert len(pages) >= 32 and all(r.status == 200 for r in pages)


def test_a_refused_or_absent_transcript_still_writes_the_summary_and_says_so_once(kind, fake):
    for n, state in ((1, "empty"), (2, "null"), (3, "deleted")):
        meeting = meeting_with_transcript(fake, n, SINCE + n * DAY)
        meeting.transcript = state
    read = reader(kind, fake, transcripts=True)
    got = read()
    assert refs(got) == [ref(1), ref(2), ref(3)]
    for n in (1, 2, 3):
        (skip,) = [s for s in got.skipped if nid(n) in s.ref]
        assert "no transcript" in skip.reason.lower()
    fake.clock.now += DAY
    again = read(got.cursor)
    assert again.drafts == [] and again.skipped == []

    off = FakeGranola(Clock(NOW))
    off.transcripts_off = True
    meeting_with_transcript(off, 4, SINCE + DAY)
    read_off = reader(kind, off, transcripts=True)
    got = read_off()
    assert refs(got) == [ref(4)]
    (skip,) = got.skipped
    assert nid(4) in skip.ref
    assert "transcripts are switched off for this key" in skip.reason.lower()
    off.clock.now += DAY
    again = read_off(got.cursor)
    assert again.drafts == [] and again.skipped == []


@pytest.mark.parametrize("sees_deleted", [False, True], ids=["deleted-hidden", "deleted-listed"])
def test_a_note_deleted_at_granola_is_not_written(kind, fake, sees_deleted):
    fake.sees_deleted = sees_deleted
    fake.note(1, "Kept", created=SINCE + DAY)
    gone = fake.note(2, "Deleted", created=SINCE + 2 * DAY)
    gone.deleted = SINCE + 3 * DAY
    gone.updated = gone.deleted
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(1)]
    fake.clock.now += DAY
    assert read(got.cursor).drafts == []


def test_a_note_gone_between_the_list_and_the_get_is_skipped_once(kind, fake):
    fake.note(1, "Kept", created=SINCE + DAY)
    gone = fake.note(2, "Summary removed", created=SINCE + 2 * DAY)
    gone.gone = True
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(1)]
    (skip,) = got.skipped
    assert nid(2) in skip.ref and "gone" in skip.reason.lower()

    gone.updated = NOW + HOUR  # still listed as changed, and settled by the next pull
    fake.clock.now = NOW + 2 * HOUR
    again = read(got.cursor)
    assert again.drafts == [] and again.skipped == []
    assert not any(r.endpoint == "get" and r.note == nid(2) for r in fake.of_pull())


# When the provider says no


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_an_error_naming_the_variable_and_never_the_key(kind, fake, status):
    fake.note(1, created=SINCE + DAY)
    fake.always["list"] = (status, {})
    said = str(failed_pull(fake, opened(kind, fake)))
    assert KEY_ENV in said and KEY not in said and CANARY not in said

    del fake.always["list"]
    wrong = str(failed_pull(fake, opened(kind, fake, env={KEY_ENV: WRONG_KEY})))
    assert KEY_ENV in wrong and WRONG_KEY not in wrong and CANARY not in wrong


@pytest.mark.parametrize("retry_after", ["7", None], ids=["retry-after", "no-retry-after"])
def test_rate_limits_are_waited_out_through_sleep_a_bounded_number_of_times(
    kind, fake, retry_after
):
    fake.note(1, created=SINCE + DAY)
    headers = {"retry-after": retry_after} if retry_after else {}
    fake.forced = [("list", 429, headers), ("list", 429, headers)]
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(1)]
    assert len(fake.slept) == 2
    if retry_after:
        assert fake.slept == [7, 7]
    else:
        assert fake.slept[0] == fake.slept[1] and 0 < fake.slept[0] <= 60

    fake.slept.clear()
    fake.always["list"] = (429, headers)
    said = str(failed_pull(fake, opened(kind, fake)))
    assert 1 <= len(fake.slept) <= 3
    assert len(fake.lists()) <= 4
    assert KEY not in said and CANARY not in said


@pytest.mark.parametrize(
    ("endpoint", "status"),
    [
        ("list", 500),
        ("list", 503),
        ("get", 500),
        ("list", "timeout"),
        ("get", "timeout"),
        ("list", "not json"),
        ("list", "wrong shape"),
        ("list", "note without created_at"),
        ("get", "array"),
    ],
)
def test_a_server_error_a_timeout_or_a_malformed_answer_fails_the_pull(
    kind, fake, endpoint, status
):
    fake.note(1, created=SINCE + DAY)
    fake.note(2, created=SINCE + 2 * DAY)
    fake.always[endpoint] = (status, {})
    said = str(failed_pull(fake, opened(kind, fake)))
    assert said and KEY not in said and CANARY not in said


def test_a_pull_cut_by_its_deadline_and_every_resume_point_go_on_without_loss_or_repeat(kind, fake):
    for n in range(1, 7):
        meeting_with_transcript(fake, n, SINCE + n * DAY, title=f"Standup {n}")
    whole = pull(fake, opened(kind, fake, transcripts=True))
    full = refs(whole)
    assert sorted(full) == sorted(
        [ref(n) for n in range(1, 7)] + [ref(n) + "/transcript" for n in range(1, 7)]
    )
    assert all(d.resume for d in whole.drafts)
    for index, draft in enumerate(whole.drafts):  # a run that stops right after this draft
        fake.clock.now += HOUR
        rest = pull(fake, opened(kind, fake, transcripts=True), draft.resume)
        assert refs(rest) == full[index + 1 :], index

    fake.clock.now += HOUR
    fake.tick = timedelta(minutes=1)  # every request takes a minute
    hurried = opened(kind, fake, transcripts=True, deadline=fake.clock.now + 7 * fake.tick)
    cut = pull(fake, hurried)
    assert 0 < len(cut.drafts) < len(full)
    assert refs(cut) == full[: len(cut.drafts)]
    assert all(d.resume for d in cut.drafts)
    fake.tick = timedelta(0)
    fake.clock.now += HOUR
    rest = pull(fake, opened(kind, fake, transcripts=True), cut.cursor)
    assert refs(rest) == full[len(cut.drafts) :]


# Together with the framework


@pytest.fixture
def world(tmp_path, fake):
    switched_on = SINCE
    clock = Clock(switched_on - DAY)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    for name in ("meetings", "planning"):
        store.create_project(name)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    yield SimpleNamespace(store=store, db=db, clock=clock, http=http, switched_on=switched_on)
    store.close()


def run(world, fake, at: datetime):
    fake.clock.now = at
    fake.new_pull()
    return asyncio.run(
        run_source(
            world.store, "granola", http=world.http, env=ENV, clock=fake.clock, sleep=fake.sleep
        )
    )


def notes(world, project: str = "meetings") -> list:
    return world.store.list_notes(world.store.get_project(project).id)


def test_through_the_runner_a_source_is_off_until_switched_on_then_writes_each_meeting_once(
    world, fake
):
    fake.note(1, "Before it was on", created=SINCE - HOUR)
    fake.note(2, "Payments weekly", created=SINCE + HOUR)
    fake.note(3, "Hiring sync", created=SINCE + 2 * DAY)
    fake.note(4, "Payments weekly", created=SINCE + 7 * DAY + HOUR)
    fake.note(5, "Retro", created=NOW - HOUR, updated=NOW - 5 * HOUR / 60)

    added = world.store.add_source("granola", "granola", config(projects=[]))
    assert added.enabled is False
    assert run(world, fake, NOW).off is True and fake.requests == []

    world.clock.now = world.switched_on
    world.store.enable_source("granola", projects=["meetings"])
    report = run(world, fake, NOW)
    assert (report.saved, report.unchanged, report.refused, report.rejected) == (3, 0, 0, 0)
    held = notes(world)
    assert sorted(c.source_ref for c in held) == [ref(2), ref(3), ref(4)]
    assert {(c.via, c.label) for c in held} == {("granola", "granola")}
    assert len(world.store.list_pages(world.store.get_project("meetings").id)) == 3
    assert notes(world, "planning") == []
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind) == ("source.synced", "system")
    assert (event.detail["source"], event.detail["saved"]) == ("granola", 3)

    again = run(world, fake, NOW + 5 * HOUR / 60)
    assert (again.saved, again.unchanged) == (0, 0)
    later = run(world, fake, NOW + HOUR)  # the retro has settled
    assert later.saved == 1 and ref(5) in {c.source_ref for c in notes(world)}

    fake.notes[nid(2)].deleted = NOW + HOUR  # deleted at Granola: what cairn wrote stays
    fake.notes[nid(2)].updated = NOW + HOUR
    assert run(world, fake, NOW + 3 * HOUR).saved == 0
    assert len(notes(world)) == 4

    stored = dump(world.db)
    assert KEY not in stored and KEY.lower() not in stored.lower()
    for event in world.store.list_audit(limit=100):
        assert KEY not in json.dumps(event.detail)


def test_through_the_runner_a_failed_run_moves_no_cursor_and_the_next_writes_the_rest(world, fake):
    world.store.add_source("granola", "granola", config(projects=["meetings"]))
    world.clock.now = world.switched_on
    world.store.enable_source("granola")
    fake.note(1, "Kickoff", created=SINCE + DAY)
    assert run(world, fake, NOW).saved == 1
    before = world.store.get_source("granola")

    fake.note(2, "Payments weekly", created=NOW + HOUR)
    fake.note(3, "Hiring sync", created=NOW + 2 * HOUR)
    fake.forced = [("get", 500, {})]  # the first of the two new notes cannot be read
    with pytest.raises(SourceError):
        run(world, fake, NOW + 4 * HOUR)
    failed = world.store.get_source("granola")
    assert failed.cursor == before.cursor and failed.last_error
    assert KEY not in failed.last_error and CANARY not in failed.last_error
    assert len(notes(world)) == 1

    report = run(world, fake, NOW + 5 * HOUR)
    assert report.saved == 2 and world.store.get_source("granola").last_error is None
    assert sorted(c.source_ref for c in notes(world)) == [ref(1), ref(2), ref(3)]
    assert run(world, fake, NOW + 6 * HOUR).saved == 0


def test_the_command_line_adds_switches_on_and_syncs_without_showing_the_key(
    tmp_path, fake, monkeypatch, capsys
):
    db = tmp_path / "cairn.db"
    store = Store(db)
    store.create_project("meetings")
    store.close()
    monkeypatch.setenv(KEY_ENV, KEY)
    routed = httpx.MockTransport(fake.handler)

    async def through_the_stand_in(self, request):  # every request cairn sends, offline
        return await routed.handle_async_request(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", through_the_stand_in)

    async def a_public_address(host: str, port: int) -> list[str]:  # no lookup leaves the test
        return ["192.0.2.10"]

    # The command line's client resolves each name before a request (cairn.sources.url)
    monkeypatch.setattr(cairn.sources.runner, "system_resolve", a_public_address)
    said: list[str] = []

    def cli(*argv: str) -> int:
        code = main(list(argv))
        out = capsys.readouterr()
        said.extend([out.out, out.err])
        return code

    settings = json.dumps(config(projects=[], settle_minutes=0))
    assert cli("source-add", "--db", str(db), "--name", "granola", "--kind", "granola",
               "--config", settings) == 0  # fmt: skip
    assert cli("source-sync", "--db", str(db), "--name", "granola") == 0
    assert fake.requests == []  # off: nothing read
    assert cli("source-enable", "--db", str(db), "--name", "granola", "--project", "meetings") == 0

    store = Store(db)
    try:
        since = parsed(store.get_source("granola").enabled_at)
    finally:
        store.close()
    fake.note(1, "Before it was on", created=since - HOUR, updated=since - HOUR / 2)
    just_after = since + timedelta(milliseconds=1)
    fake.note(2, "Payments weekly", created=just_after, updated=just_after)
    while datetime.now(UTC) <= just_after + timedelta(milliseconds=1):
        pass  # the real clock, which the command line reads, has passed it
    assert cli("source-sync", "--db", str(db), "--name", "granola") == 0
    assert cli("source-list", "--db", str(db)) == 0

    monkeypatch.setenv(KEY_ENV, WRONG_KEY)
    assert cli("source-sync", "--db", str(db), "--name", "granola") == 1
    assert KEY_ENV in said[-1]

    store = Store(db)
    try:
        held = store.list_notes(store.get_project("meetings").id)
        assert [c.source_ref for c in held] == [ref(2)]
    finally:
        store.close()
    shown = "\n".join(said)
    assert KEY not in shown and WRONG_KEY not in shown and CANARY not in shown
    assert KEY not in dump(db) and WRONG_KEY not in dump(db)
