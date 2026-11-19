"""C4 acceptance tests (frozen): Google Meet transcripts, and how cairn signs in to Google.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/sources/google_auth.py,
src/cairn/sources/google_meet.py and src/cairn/sources/__init__.py. Google's request and
response shapes are recorded in docs/google-auth.md and docs/google-meet-api.md. Where those
notes say Google's pages are silent or disagree, the connector depends on neither reading:
the stand-ins below behave both ways, and the tests run under both.

Signing in to Google as a server (cairn.sources.google_auth)
- A credential is one JSON document: the text (never a path) of the environment variable the
  config names in `credentials_env`. Its `type` says which kind it is:
  - "service_account": Google's key file as downloaded (`private_key_id`, `private_key`,
    `client_email`, `token_uri`, ...). The config must then name `subject`, the address of the
    person whose meetings are read: the token is asked for that person (domain-wide delegation).
  - "authorized_user": `client_id`, `client_secret` and `refresh_token`, one person's own
    authorisation. The config must then not name `subject`.
  Another type, a missing field, a private key that does not load, a missing or empty
  variable, or text that is not a JSON object: SourceError (from `open`, or from the pull before
  it sends anything) that names the variable and what is wrong, never a field's value.
  `subject` missing with a service account, or given with a refresh token: SourceError from
  `open` naming `subject`.
- A service account's token request: POST to the address its `token_uri` leads to (below), a form
  (application/x-www-form-urlencoded) of exactly `grant_type`
  ("urn:ietf:params:oauth:grant-type:jwt-bearer") and `assertion`, a JWT signed RS256 with the key:
  header `alg` "RS256", `typ` "JWT" (and `kid`, if sent, the `private_key_id`); claims `iss` the
  `client_email`, `scope` the scopes asked for (space-separated, in the order given), `aud`
  "https://oauth2.googleapis.com/token" whatever the `token_uri`, `sub` the subject when one is
  given (the Meet kind always gives one; without one the account acts as itself and the claim is
  absent), `iat` the time on the `clock` the source was opened with, and `exp` after `iat` by at
  most an hour. Base64url with or without padding.
- A refresh token's request: POST to https://oauth2.googleapis.com/token, a form of exactly
  `client_id`, `client_secret`, `grant_type` ("refresh_token") and `refresh_token`, whatever
  scopes are asked for (it gets what the person authorised).
- The scopes are an argument, never a constant of google_auth: the mail, calendar and
  documents sources ask for theirs through the same module. Its seam, and all of it this file
  pins: `load_credential(variable, env)` returns the credential held in that variable of `env`
  (a mapping) or raises SourceError as above; `await access_token(credential, scopes=...,
  subject=..., http=..., clock=...)` takes a list of scope strings, the subject (None: a refresh
  token, or a service account acting as itself), an httpx.AsyncClient and the clock, and
  returns the access token or raises SourceError as below.
- A token request goes only to https://oauth2.googleapis.com/token, or (tests and stand-ins) to
  this machine: a `token_uri` on 127.0.0.1 or localhost is used as it is. A key file may name
  either address Google's own key files carry, https://oauth2.googleapis.com/token or
  https://accounts.google.com/o/oauth2/token: the request goes to the first whichever it names.
  Any other `token_uri` (look-alikes included) is refused before anything is signed or sent.
- One access token per pull, asked for by the pull and used for all of it, sent only in the
  `Authorization: Bearer` header. The private key, the client secret and the refresh token are
  in the source's `secrets` once it is opened, and the access token once the pull has it; the
  runner reads `secrets` again after the pull, so a draft that holds any of them is withheld
  (HELD_CREDENTIALS). None of them is ever in a URL, a log line, an exception message or the
  cursor.
- A refusal by the token endpoint is a SourceError in words an operator can act on, chosen by its
  `error` code whatever the HTTP status, never quoting the response (not even its
  `error_description`): for a service account `unauthorized_client` and `access_denied` say the
  domain-wide delegation was not granted for the scope (the word "delegation"), and
  `invalid_grant` that the person is not in the domain or the clock is wrong (the word "clock");
  for a refresh token `invalid_grant` says it was revoked or has expired (the word "revoked" or
  "expired"). These three are different sentences. Any other code, a 5xx, a timeout, or an answer
  that is not JSON or has no `access_token`: SourceError, without the body.

The kind
- `KINDS["google_meet"]` (cairn.sources.google_meet). It starts off. Via and label
  "google-meet". It asks for one scope, https://www.googleapis.com/auth/meetings.space.readonly.
  It proposes the project "meet-transcripts" as its destination: `SourceKind.default_project`, an
  optional field of the seam, None for a kind that proposes none (langfuse). What the Sources
  page does with it is tested with the framework, not here.
- `check(config)` returns the config with its defaults filled in, or raises ValueError naming
  the setting at fault, never a value that could be a credential:
  - `credentials_env` (required): the name of the variable. JSON, a key, a token or a secret
    given there instead of a name is refused.
  - `subject` (optional here; see above): one email address.
  - `projects`: a list of at most one project name, where it writes; may be empty while it is off.
  - `base_url`: "https://meet.googleapis.com/v2" unless given; https, or http on this machine.
  - `wait_hours`: 48 unless given; a whole number from 1 to 168.
  - No other setting.

What it reads, and how it finds it
- Transcripts of meetings (conference records) that ended at or after `since`, whatever
  `conferenceRecords.list` returns for the signed-in person (the notes disagree on whether that
  is every meeting they joined or only those they organized). A meeting that ended before
  `since` is never written, even if it began after the last pull.
- Every pull lists conference records with the documented filter, exactly the form
  `start_time>="<RFC 3339>"` (no other filter), a day before the cursor's mark (the first
  pull's mark is `since`), so that a meeting of up to a day is found once it has ended even where
  Google lists only finished meetings, the same filter repeated beside every `pageToken`, and sorts
  what it collected by end time, then by name, whatever order Google used. Its drafts come in
  that order.
- For each ended meeting it lists the transcripts, on every pull until `wait_hours` after the
  meeting ended: Google may list a transcript only once its file is generated, so what one look
  found does not settle a meeting. A transcript whose state is FILE_GENERATED is read, once:
  every page of its entries, and the meeting's participants and space; entries are never asked
  for before that state. When `wait_hours` have passed the meeting is dropped (nothing more is
  asked about it, and a transcript generated later is not written) and, if a transcript of it
  was listed and never generated, that is reported once as skipped, "no transcript was
  generated". A meeting nobody transcribed is dropped the same way, silently. A meeting that is
  gone (404) is not asked about again. A meeting still going on (no `endTime`) is looked at
  again, whether or not Google lists meetings still going on (meetings of 40 minutes and of 20
  hours are the cases tested).
- Google may or may not list transcripts still STARTED or ENDED; an empty list may come as `[]`
  or with its key absent; timestamps have 0 to 9 fractional digits; a page may be short, or
  empty with a `nextPageToken`; page tokens do not outlive the pull. Lists ask for Google's
  largest page (250 entries come in three pages).
- The cursor is JSON the connector owns: the mark, the meetings it is waiting on (resource names
  and end times), and short digests of the transcripts it wrote, kept while Google keeps them
  (30 days). It holds no token, key or secret, no person's name or address, no id of a written
  transcript and no text, and it stays bounded however many weeks it runs; the listing's lower
  bound keeps up with the clock.
- A pull that reaches its `deadline` returns what it finished. Every draft carries a `resume`
  cursor from which the next pull goes on with no draft written twice and none skipped.

What it writes, for each generated transcript, into the config's project
- `title` and `topic`: "Meet", the meeting code when the space gives one (`meetingCode`), then
  the start in UTC as "YYYY-MM-DD HH:MM" (an optional "UTC" after it). Two transcripts of one
  meeting share the page.
- `text`: Markdown. A heading with the title; the facts (start and end in UTC; every
  participant by display name, signed in, anonymous or by phone); then one line per turn:
  consecutive entries of one participant joined, the speaker's display name and the time since
  the meeting (not the transcript) started as HH:MM:SS. A participant with no display name, or
  one not in the list, is "Unknown speaker". No email address.
- Over 1,500 turns: parts of at most 1,500 turns, each a draft, topic "<title>, part 2", ...
- `source_ref`: "meet:<conference record id>/<transcript id>", and "/2", "/3" for later parts.
  `label`: "google-meet".
- A generated transcript with no entries: skipped once, with a reason in words.

When Google says no
- 401 or 403 from the Meet API: SourceError saying access was refused and what to check (the
  word "scope" among it), without a token or the body, and not retried.
- 404 for a meeting or a transcript listed a moment ago: skipped ("gone"), once.
- 429, and a 403 whose reason is a rate limit (`RATE_LIMIT_EXCEEDED` in `error.details`, or
  `rateLimitExceeded` in `error.errors`): retried at most 4 times, the n-th wait (through the
  injected `sleep`) 2^n seconds plus up to one second, n counted from 0 or from 1; then
  SourceError.
- 5xx, a timeout, a body not of the documented shape: SourceError without the body. A failed
  pull returns nothing, so no cursor moves, and the next pull writes what it did not.

Together with the framework: a Meet source is off when added and reads nothing; switched on with
a project, `run_source` writes the transcripts of meetings that ended since, one page a meeting;
again, nothing new; a transcript generated later is written by the run after. The database, the
audit log and the command line's output (`source-add --kind google_meet`, `source-enable
--project`, `source-sync`, `source-list`) hold no private key, token, client secret or refresh
token.
"""

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, unquote

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import cairn.sources.runner
from cairn.server import main
from cairn.sources import HELD_CREDENTIALS, KINDS, SourceError, holds_secret, run_source
from cairn.sources.google_auth import access_token, load_credential
from cairn.store import Store

SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
MEET = "https://meet.googleapis.com/v2"
CRED_ENV = "GOOGLE_C4_CREDENTIALS"
SA, USER = "service_account", "authorized_user"
SUBJECT = "ada@example.test"
CLIENT_EMAIL = "cairn-reader@c4-project.iam.example.test"
KEY_ID = "c4" + "0f3a9c7e" * 4  # made up
SA_CLIENT_ID = "1" + "04" * 10
USER_CLIENT_ID = "c4-" + "desktop-client" + ".apps.example.test"
CLIENT_SECRET = "GOCSPX-" + "c4" + "MadeUp" + "Secret5d2e"  # never a real secret
REFRESH_TOKEN = "1//" + "0c4" + "MadeUpRefresh" + "Token7a1f"
NOT_A_KEY = "c4" + "NotAKey"
CANARY = "c4-" + "body-" + "canary-3b8e"  # in every error body the stand-ins send
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=1)  # when the source was switched on
MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
LIST_KEYS = ("conferenceRecords", "transcripts", "transcriptEntries", "participants")
FILTER = re.compile(r'\s*start_time\s*>=\s*"([^"]+)"\s*(?:AND\s+start_time\s*<=\s*"([^"]+)"\s*)?')
UNSET = object()


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
    """A JWT segment: base64url, with or without padding (the notes show both)."""
    if re.search(r"[^A-Za-z0-9_\-=]", segment):
        raise ValueError(segment)
    return urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def cid(n: int) -> str:
    return f"c4conf{n:05d}"


def sid(n: int) -> str:
    return f"c4space{n:05d}"


def tid(n: int, k: int = 0) -> str:
    return f"c4tr{n:05d}{'abcdefgh'[k]}"


def ref(n: int, k: int = 0) -> str:
    return f"meet:{cid(n)}/{tid(n, k)}"


def meeting_code(n: int) -> str:
    return "abc-" + "".join(chr(ord("a") + int(d)) for d in f"{n:04d}") + "-xyz"


def refs(pulled) -> list[str]:
    return [draft.source_ref for draft in pulled.drafts]


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def line_with(text: str, words: str) -> str:
    (line,) = [line for line in text.splitlines() if words in line]
    return line


@dataclass
class Person:
    pid: str
    kind: str  # "signedinUser", "anonymousUser" or "phoneUser"
    display: str | None


@dataclass
class Entry:
    pid: str
    text: str
    start: datetime


@dataclass
class Transcript:
    tid: str
    start: datetime
    generated: datetime | None  # when its file is generated; None: never
    entries: list[Entry]
    end: datetime | None = None  # None: when the meeting ends
    gone: bool = False  # still listed, but reading it answers 404 (expired in between)
    echo: str | None = None  # its entries also say "token", "refresh" or "secret" (a bad server)


@dataclass
class Conference:
    n: int
    start: datetime
    end: datetime | None  # None: it does not end while the test runs
    code: str | None
    transcripts: list[Transcript]
    people: list[Person]
    own: bool = True  # organized by the person signed in
    gone: bool = False  # still listed, but reading it answers 404 (expired in between)

    @property
    def name(self) -> str:
        return f"conferenceRecords/{cid(self.n)}"


CAST = [
    Person("p1", "signedinUser", "Ada Park"),
    Person("p2", "signedinUser", "Brook Lane"),
    Person("p3", "anonymousUser", "Cy Moss"),
    Person("p4", "phoneUser", "+1 555-xxx-xx42"),
]
NAMES = ("Ada Park", "Brook Lane", "Cy Moss", "555-xxx-xx42")


def chat(n: int, start: datetime) -> list[Entry]:
    return [
        Entry("p1", f"Item {n}: Ada moves the release to Friday.", start + MINUTE),
        Entry("p2", f"Brook agrees to item {n}.", start + 2 * MINUTE),
    ]


def service_account(keys, **changes) -> dict:
    doc = {
        "type": "service_account",
        "project_id": "c4-project",
        "private_key_id": KEY_ID,
        "private_key": keys.pem,
        "client_email": CLIENT_EMAIL,
        "client_id": SA_CLIENT_ID,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": TOKEN_URL,
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/c4-reader",
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


def broken_key() -> str:
    label = "PRIVATE" + " KEY"  # built from parts, so no scanner takes this for a key
    return f"-----BEGIN {label}-----\n" + NOT_A_KEY * 8 + f"\n-----END {label}-----\n"


class FakeGoogle:
    """Google's token endpoint and the Meet REST API v2, as docs/google-auth.md and
    docs/google-meet-api.md describe them, as one httpx mock. The token endpoint checks a
    service account's JWT (signature, header and claims) or a refresh token's form and issues a
    new access token each time; the Meet API accepts only the latest one, while it lasts. Meet
    holds conferences, transcripts, entries, participants and spaces that follow the clock
    (started, ended, generated), pages them with tokens that die with the pull, accepts only the
    documented record filter, and can be told to hide meetings still going on, hide transcripts
    not yet generated, list only what the person organized, omit empty lists, send short and
    empty pages, shuffle the records, write 0, 3 or 9 fractional digits, and answer 401, 403,
    404, 429, 5xx, a timeout or a malformed body. Every request is kept."""

    def __init__(self, clock, keys) -> None:
        self.clock = clock
        self.keys = keys
        self.meet_base = MEET
        self.tick = timedelta(0)  # the clock moves this much with every request
        self.conferences: dict[str, Conference] = {}
        self.users = {SUBJECT}  # the people of the domain
        self.requests: list[SimpleNamespace] = []
        self.slept: list[float] = []
        self.issued: list[str] = []
        self.valid: tuple[str, datetime] | None = None
        self.gen = 0
        self.lists_ongoing = True
        self.lists_unfinished = True
        self.organizer_only = False
        self.omit_empty = False
        self.short_pages = False
        self.shuffle = False
        self.digits = 3
        self.forced: list[tuple[str, object]] = []  # (endpoint, how): once each
        self.always: dict[str, object] = {}
        self.refusal: tuple[int, str, str] | None = None  # (status, error, description)
        self.scope = SCOPE  # what a service account's JWT must ask for, space-separated

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def new_pull(self) -> None:
        self.gen += 1

    def env(self, cred: str = SA, **changes) -> dict[str, str]:
        doc = service_account(self.keys, **changes) if cred == SA else authorized_user(**changes)
        return {CRED_ENV: json.dumps(doc)}

    def meeting(
        self, n, start, end, *, code=UNSET, people=None, transcripts=UNSET, ready=UNSET, own=True
    ) -> Conference:
        if transcripts is UNSET:
            when = end + 10 * MINUTE if ready is UNSET else ready
            transcripts = [Transcript(tid(n), start + MINUTE, when, chat(n, start))]
        conference = Conference(
            n=n,
            start=start,
            end=end,
            code=meeting_code(n) if code is UNSET else code,
            transcripts=list(transcripts),
            people=list(CAST if people is None else people),
            own=own,
        )
        self.conferences[cid(n)] = conference
        return conference

    # What the tests ask about the requests

    def calls(self, endpoint: str | None = None, *, every: bool = False) -> list[SimpleNamespace]:
        return [
            r
            for r in self.requests
            if (every or r.gen == self.gen) and (endpoint is None or r.endpoint == endpoint)
        ]

    # Time

    def ts(self, moment: datetime) -> str:
        moment = moment.astimezone(UTC)
        base = moment.strftime("%Y-%m-%dT%H:%M:%S")
        if not self.digits:
            return base + "Z"
        return f"{base}.{f'{moment.microsecond:06d}000'[: self.digits]}Z"

    def _ended(self, c: Conference) -> bool:
        return c.end is not None and c.end <= self.clock()

    def _exists(self, c: Conference) -> bool:
        now = self.clock()
        return c.start <= now and not (self._ended(c) and c.end + 30 * DAY < now)

    def _listed(self, c: Conference) -> bool:
        if not self._exists(c) or (self.organizer_only and not c.own):
            return False
        return self.lists_ongoing or self._ended(c)

    def _state(self, c: Conference, t: Transcript) -> str:
        now = self.clock()
        stopped = t.end or c.end
        if stopped is None or stopped > now:
            return "STARTED"
        if t.generated is None or t.generated > now:
            return "ENDED"
        return "FILE_GENERATED"

    # Routing

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.tick:
            self.clock.now += self.tick
        url = request.url
        where = f"{url.scheme}://{url.netloc.decode()}{url.path}"
        endpoint, parts = self._endpoint(url, where)
        record = SimpleNamespace(
            at=self.clock(),
            gen=self.gen,
            method=request.method,
            url=str(url),
            where=where,
            endpoint=endpoint,
            params=dict(url.params),
            auth=request.headers.get("authorization"),
            content_type=request.headers.get("content-type", ""),
            form=None,
            header=None,
            claims=None,
            problem=None,
            status=None,
            state=None,
            count=None,
            bound=None,
            conference=None,
            transcript=None,
        )
        self.requests.append(record)
        response = self._answer(request, record, parts)
        record.status = response.status_code
        return response

    def _endpoint(self, url: httpx.URL, where: str) -> tuple[str, list[str]]:
        if url.path == "/token" and (where == TOKEN_URL or url.host in ("127.0.0.1", "localhost")):
            return "token", []
        if not where.startswith(self.meet_base + "/"):
            return "other", []
        parts = where[len(self.meet_base) + 1 :].split("/")
        shapes = {
            ("conferenceRecords",): "records",
            ("conferenceRecords", "*"): "record",
            ("conferenceRecords", "*", "transcripts"): "transcripts",
            ("conferenceRecords", "*", "transcripts", "*"): "transcript",
            ("conferenceRecords", "*", "transcripts", "*", "entries"): "entries",
            ("conferenceRecords", "*", "participants"): "participants",
            ("conferenceRecords", "*", "participants", "*"): "participant",
            ("spaces", "*"): "space",
        }
        shape = tuple("*" if i % 2 else p for i, p in enumerate(parts))
        return shapes.get(shape, "other"), parts

    def _forced_for(self, endpoint: str) -> object:
        for index, (which, how) in enumerate(self.forced):
            if which == endpoint:
                del self.forced[index]
                return how
        return self.always.get(endpoint)

    def _answer(self, request: httpx.Request, record, parts: list[str]) -> httpx.Response:
        if record.endpoint == "token":
            return self._token(request, record)
        if record.endpoint == "other":
            return httpx.Response(404, text=CANARY)
        if request.method != "GET":
            return self._error(400, "INVALID_ARGUMENT")
        if not (
            self.valid is not None
            and record.auth == f"Bearer {self.valid[0]}"
            and self.clock() < self.valid[1]
        ):
            return self._error(401, "UNAUTHENTICATED", "ACCESS_TOKEN_EXPIRED")
        if parts[0] == "conferenceRecords" and len(parts) > 1:
            record.conference = parts[1]
        if parts[0] == "spaces":
            record.conference = next(
                (cid(c.n) for c in self.conferences.values() if sid(c.n) == parts[1]), None
            )
        how = self._forced_for(record.endpoint)
        if how is not None:
            return self._failure(request, how)
        if record.endpoint == "records":
            return self._records(record)
        if record.endpoint == "space":
            return self._space(parts[1])
        conference = self.conferences.get(parts[1])
        if conference is None or not self._exists(conference) or conference.gone:
            return self._error(404, "NOT_FOUND")
        if record.endpoint == "record":
            return httpx.Response(200, json=self._conference(conference))
        if record.endpoint == "participants":
            people = [self._person(conference, p) for p in conference.people]
            scope = ("participants", conference.name, record.params.get("filter"))
            return self._page(record, "participants", people, scope, 100, 250)
        if record.endpoint == "participant":
            found = [p for p in conference.people if p.pid == parts[3]]
            if not found:
                return self._error(404, "NOT_FOUND")
            return httpx.Response(200, json=self._person(conference, found[0]))
        if record.endpoint == "transcripts":
            shown = [
                self._transcript(conference, t)
                for t in sorted(conference.transcripts, key=lambda t: (t.start, t.tid))
                if t.start <= self.clock()
                and (self.lists_unfinished or self._state(conference, t) == "FILE_GENERATED")
            ]
            scope = ("transcripts", conference.name)
            return self._page(record, "transcripts", shown, scope, 10, 100)
        found = [t for t in conference.transcripts if t.tid == parts[3]]
        if not found or found[0].gone or found[0].start > self.clock():
            return self._error(404, "NOT_FOUND")
        transcript = found[0]
        if record.endpoint == "transcript":
            return httpx.Response(200, json=self._transcript(conference, transcript))
        record.transcript = transcript.tid
        record.state = self._state(conference, transcript)
        entries = [
            self._entry(conference, transcript, i, e, record.auth)
            for i, e in enumerate(sorted(transcript.entries, key=lambda e: e.start))
        ]
        scope = ("entries", transcript.tid)
        return self._page(record, "transcriptEntries", entries, scope, 10, 100)

    # The token endpoint

    def _refused(self, status: int, error: str, description: str) -> httpx.Response:
        body = {
            "error": error,
            "error_description": description,
            "error_uri": f"https://{CANARY}.example.test/",
        }
        return httpx.Response(status, json=body)

    def _token(self, request: httpx.Request, record) -> httpx.Response:
        how = self._forced_for("token")
        if how is not None:
            return self._failure(request, how)
        try:
            body = request.content.decode("ascii")
            record.form = dict(parse_qsl(body, keep_blank_values=True, strict_parsing=True))
        except (UnicodeDecodeError, ValueError):
            record.form = {}
        problem = self._token_problem(request, record)
        if problem == "sub":
            record.problem = "the subject is not a user of the domain"
            return self._refused(400, "invalid_grant", "Invalid email or User ID.")
        if problem:
            record.problem = problem
            return self._refused(400, "invalid_request", f"{CANARY}: {problem}")
        if self.refusal is not None:
            return self._refused(*self.refusal)
        token = "ya29." + f"c4{len(self.issued) + 1:04d}" + "MadeUp" + "AccessToken"
        self.issued.append(token)
        self.valid = (token, self.clock() + HOUR)
        body = {
            "access_token": token,
            "expires_in": 3599,
            "scope": self.scope,
            "token_type": "Bearer",
        }
        return httpx.Response(200, json=body)

    def _token_problem(self, request: httpx.Request, record) -> str | None:
        if request.method != "POST" or request.url.query:
            return "a token request is a POST without a query string"
        if not record.content_type.startswith("application/x-www-form-urlencoded"):
            return "a token request is a form"
        form = record.form
        if form.get("grant_type") == JWT_GRANT:
            if set(form) != {"grant_type", "assertion"}:
                return f"a JWT grant has grant_type and assertion, not {sorted(form)}"
            return self._jwt_problem(form["assertion"], record)
        if form.get("grant_type") == "refresh_token":
            if record.where != TOKEN_URL:
                return "a refresh token goes to Google's token endpoint"
            if set(form) != {"client_id", "client_secret", "grant_type", "refresh_token"}:
                return f"a refresh has its four fields, not {sorted(form)}"
            given = (form["client_id"], form["client_secret"], form["refresh_token"])
            if given != (USER_CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN):
                return "the client or the refresh token is not the one issued"
            return None
        return "grant_type"

    def _jwt_problem(self, assertion: str, record) -> str | None:
        segments = assertion.split(".")
        if len(segments) != 3:
            return "the assertion is not header.claims.signature"
        try:
            header, claims = (json.loads(unb64(s)) for s in segments[:2])
            signature = unb64(segments[2])
        except ValueError:
            return "a segment of the assertion is not base64url JSON"
        record.header, record.claims = header, claims
        if not isinstance(header, dict) or not isinstance(claims, dict):
            return "the header and the claims are JSON objects"
        if header.get("alg") != "RS256" or header.get("typ") != "JWT":
            return "the header is alg RS256, typ JWT"
        if "kid" in header and header["kid"] != KEY_ID:
            return "kid is the key's private_key_id"
        signed = f"{segments[0]}.{segments[1]}".encode("ascii")
        try:
            self.keys.public.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            return "the signature does not verify with the service account's key"
        for claim, wanted in (("iss", CLIENT_EMAIL), ("scope", self.scope), ("aud", TOKEN_URL)):
            if claims.get(claim) != wanted:
                return f"{claim} is {wanted}"
        iat, exp = claims.get("iat"), claims.get("exp")
        if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (iat, exp)):
            return "iat and exp are seconds since the epoch"
        now = self.clock().timestamp()
        if not now - 300 <= iat <= now + 60:
            return "iat is the clock's now"
        if not 0 < exp - iat <= 3600:
            return "exp is within the hour after iat"
        if "sub" in claims and claims["sub"] not in self.users:  # absent: the account itself
            return "sub"
        return None

    # Answers

    def _error(self, code: int, status: str, reason: str | None = None) -> httpx.Response:
        detail = {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": reason or status,
            "domain": "googleapis.com",
            "metadata": {"service": "meet.googleapis.com", "consumer": CANARY},
        }
        body = {
            "error": {
                "code": code,
                "message": f"{CANARY}: {status}",
                "status": status,
                "details": [detail],
            }
        }
        return httpx.Response(code, json=body)

    def _failure(self, request: httpx.Request, how: object) -> httpx.Response:
        if how == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if how == "not json":
            return httpx.Response(200, text=f"<html>{CANARY}</html>")
        if how == "array":
            return httpx.Response(200, json=[CANARY])
        if how == "wrong shape":
            return httpx.Response(200, json=dict.fromkeys(LIST_KEYS, CANARY))
        if how == "record without startTime":
            item = {"name": f"conferenceRecords/{cid(999)}", "space": f"spaces/{CANARY}"}
            return httpx.Response(200, json={"conferenceRecords": [item]})
        if how == "no access_token":
            body = {"token_type": "Bearer", "expires_in": 3599, "scope": SCOPE, "note": CANARY}
            return httpx.Response(200, json=body)
        if how == "rate limited":
            return self._error(429, "RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED")
        if how == "rate limited, no body":
            return httpx.Response(429)
        if how == "quota 403, details":
            return self._error(403, "PERMISSION_DENIED", "RATE_LIMIT_EXCEEDED")
        if how in ("quota 403, errors", "403, errors"):
            quota = how == "quota 403, errors"
            item = {
                "domain": "usageLimits" if quota else "global",
                "reason": "rateLimitExceeded" if quota else "forbidden",
                "message": CANARY,
            }
            body = {"error": {"errors": [item], "code": 403, "message": CANARY}}
            return httpx.Response(403, json=body)
        names = {401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND"}
        reasons = {401: "ACCESS_TOKEN_EXPIRED", 403: "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}
        status = int(how)  # type: ignore[call-overload]
        return self._error(status, names.get(status, "INTERNAL"), reasons.get(status))

    def _page_token(self, offset: int, scope: tuple) -> str:
        state = json.dumps({"g": self.gen, "o": offset, "s": list(scope)})
        return urlsafe_b64encode(state.encode()).decode()

    def _offset(self, token: str | None, scope: tuple) -> int | None:
        if token is None:
            return 0
        try:
            state = json.loads(urlsafe_b64decode(token.encode()))
        except ValueError:
            return None
        if state.get("g") != self.gen or state.get("s") != list(scope):
            return None  # from another pull, or the filter was not repeated
        return state["o"]

    def _page(self, record, key: str, items: list, scope: tuple, default: int, largest: int):
        try:
            size = int(record.params.get("pageSize", default))
        except ValueError:
            return self._error(400, "INVALID_ARGUMENT")
        if size < 0:
            return self._error(400, "INVALID_ARGUMENT")
        size = min(size or default, largest)
        token = record.params.get("pageToken")
        offset = self._offset(token, scope)
        if offset is None:
            return self._error(400, "INVALID_ARGUMENT")
        if self.short_pages:
            size = min(size, 7) if token else 0
        page = items[offset : offset + size]
        record.count = len(page)
        body: dict = {}
        if page or not self.omit_empty:
            body[key] = page
        if offset + size < len(items):
            body["nextPageToken"] = self._page_token(offset + size, scope)
        return httpx.Response(200, json=body)

    def _records(self, record) -> httpx.Response:
        given = record.params.get("filter")
        lower = upper = None
        if given is not None:
            match = FILTER.fullmatch(given)
            if match is None:
                return self._error(400, "INVALID_ARGUMENT")
            try:
                lower = parsed(match.group(1))
                upper = parsed(match.group(2)) if match.group(2) else None
            except ValueError:
                return self._error(400, "INVALID_ARGUMENT")
        record.bound = lower
        found = [
            c
            for c in self.conferences.values()
            if self._listed(c)
            and (lower is None or c.start >= lower)
            and (upper is None or c.start <= upper)
        ]
        found.sort(key=lambda c: (c.start, c.n), reverse=True)  # documented: newest start first
        if self.shuffle:
            found.sort(key=lambda c: hashlib.sha256(f"{self.gen}:{c.n}".encode()).hexdigest())
        items = [self._conference(c) for c in found]
        return self._page(record, "conferenceRecords", items, ("records", given), 25, 100)

    def _conference(self, c: Conference) -> dict:
        body = {"name": c.name, "startTime": self.ts(c.start), "space": f"spaces/{sid(c.n)}"}
        if self._ended(c):
            body["endTime"] = self.ts(c.end)
            body["expireTime"] = self.ts(c.end + 30 * DAY)
        return body

    def _space(self, space: str) -> httpx.Response:
        found = [c for c in self.conferences.values() if sid(c.n) == space]
        if not found:
            return self._error(404, "NOT_FOUND")
        body = {
            "name": f"spaces/{space}",
            "config": {"accessType": "TRUSTED", "entryPointAccess": "ALL"},
        }
        if found[0].code:
            body["meetingUri"] = f"https://meet.google.com/{found[0].code}"
            body["meetingCode"] = found[0].code
        return httpx.Response(200, json=body)

    def _transcript(self, c: Conference, t: Transcript) -> dict:
        state = self._state(c, t)
        body = {
            "name": f"{c.name}/transcripts/{t.tid}",
            "state": state,
            "startTime": self.ts(t.start),
        }
        if state != "STARTED":
            body["endTime"] = self.ts(t.end or c.end)
        if state == "FILE_GENERATED":
            document = f"c4doc{t.tid}"
            body["docsDestination"] = {
                "document": document,
                "exportUri": f"https://docs.google.com/document/d/{document}/view",
            }
        return body

    def _entry(self, c: Conference, t: Transcript, i: int, e: Entry, auth: str | None) -> dict:
        text = e.text
        if t.echo == "token":
            text += f" (the server says {(auth or '').removeprefix('Bearer ')})"
        elif t.echo == "refresh":
            text += f" (the server says {REFRESH_TOKEN})"
        elif t.echo == "secret":
            text += f" (the server says {CLIENT_SECRET})"
        return {
            "name": f"{c.name}/transcripts/{t.tid}/entries/{i:05d}",
            "participant": f"{c.name}/participants/{e.pid}",
            "text": text,
            "languageCode": "en-US",
            "startTime": self.ts(e.start),
            "endTime": self.ts(e.start + timedelta(seconds=1)),
        }

    def _person(self, c: Conference, p: Person) -> dict:
        body: dict = {
            "name": f"{c.name}/participants/{p.pid}",
            "earliestStartTime": self.ts(c.start),
        }
        if self._ended(c):
            body["latestEndTime"] = self.ts(c.end)
        user: dict = {} if p.display is None else {"displayName": p.display}
        if p.kind == "signedinUser":
            user["user"] = "users/" + str(1000 + c.people.index(p))
        body[p.kind] = user
        return body


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
    return SimpleNamespace(pem=pem, public=private.public_key(), body=body)


@pytest.fixture
def kind():
    return KINDS["google_meet"]


@pytest.fixture
def fake(kind, keys):
    return FakeGoogle(Clock(NOW), keys)


def leaked(text: str | None, fake: FakeGoogle) -> list[str]:
    """The secrets `text` holds, as written or percent-decoded: a line of the private key, the
    client secret, the refresh token, or an access token the stand-in issued."""
    if not text:
        return []
    secrets = [*fake.keys.body, CLIENT_SECRET, REFRESH_TOKEN, *fake.issued]
    return [s for s in secrets if s in text or s in unquote(text)]


def config(cred: str = SA, **settings) -> dict:
    given: dict = {"credentials_env": CRED_ENV, "projects": ["meetings"]}
    if cred == SA:
        given["subject"] = SUBJECT
    given.update(settings)
    return {key: value for key, value in given.items() if value is not None}


def opened(kind, fake, *, cred=SA, env=None, since=SINCE, deadline=None, **settings):
    checked = kind.check(config(cred, **settings))
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return kind.open(
        "meet", checked, http=http, env=fake.env(cred) if env is None else env,
        clock=fake.clock, sleep=fake.sleep, deadline=deadline, since=since,
    )  # fmt: skip


def pull(fake, source, cursor=None):
    fake.new_pull()
    try:
        return asyncio.run(source.pull(cursor))
    except SourceError as error:
        refused = [r.problem for r in fake.calls("token") if r.problem]
        if refused:
            raise AssertionError(f"the token endpoint refused: {refused[-1]}") from error
        raise


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


def refused_before_sending(kind, fake, *, cred=SA, env, **settings) -> SourceError:
    with pytest.raises(SourceError) as refused:
        pull(fake, opened(kind, fake, cred=cred, env=env, **settings))
    assert fake.requests == []  # nothing signed and sent, nothing read
    return refused.value


def backed_off(slept: list[float]) -> None:
    """The notes' backoff: the n-th wait is 2^n seconds and up to one more, n from 0 or 1."""
    assert any(
        all(2 ** (first + n) <= s <= 2 ** (first + n) + 1 for n, s in enumerate(slept))
        for first in (0, 1)
    ), slept


# The kind and its config


def test_the_kind_starts_off_and_its_config_has_these_defaults(kind):
    assert kind.starts_off is True and kind.via == "google-meet"
    assert kind.default_project == "meet-transcripts" and KINDS["langfuse"].default_project is None
    checked = kind.check({"credentials_env": CRED_ENV, "projects": []})
    assert (checked["credentials_env"], checked["projects"]) == (CRED_ENV, [])
    assert (checked["base_url"], checked["wait_hours"]) == (MEET, 48)
    assert checked.get("subject") is None
    assert kind.check(checked) == checked  # what is stored is checked again on switch-on

    given = {
        "credentials_env": CRED_ENV,
        "subject": SUBJECT,
        "projects": ["meetings"],
        "base_url": "https://meet.example.test/v2",
        "wait_hours": 168,
    }
    kept = kind.check(given)
    for setting, value in given.items():
        assert kept[setting] == value, setting
    assert kind.check(kept) == kept
    for accepted in (
        {"wait_hours": 1},
        {"base_url": "http://127.0.0.1:8781/v2"},
        {"base_url": "http://localhost:8781/v2"},
    ):
        assert kind.check(config(**accepted))["projects"] == ["meetings"], accepted


@pytest.mark.parametrize(
    ("setting", "settings"),
    [
        ("credentials_env", {"credentials_env": None}),
        ("credentials_env", {"credentials_env": 7}),
        ("credentials_env", {"credentials_env": ""}),
        ("subject", {"subject": "ada"}),
        ("subject", {"subject": "ada@example.test, brook@example.test"}),
        ("subject", {"subject": ["ada@example.test"]}),
        ("projects", {"projects": ["meetings", "planning"]}),
        ("projects", {"projects": "meetings"}),
        ("base_url", {"base_url": "http://meet.example.test/v2"}),
        ("base_url", {"base_url": "ftp://meet.example.test/v2"}),
        ("base_url", {"base_url": 7}),
        ("wait_hours", {"wait_hours": 0}),
        ("wait_hours", {"wait_hours": 169}),
        ("wait_hours", {"wait_hours": "48"}),
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
    token = "ya29." + "c4" + "PastedAccessToken" + "0a1b2c3d"
    for setting, value in (
        ("credentials_env", document),
        ("credentials_env", fake.keys.pem),
        ("credentials_env", token),
        ("credentials_env", REFRESH_TOKEN),
        ("credentials_env", CLIENT_SECRET),
        ("subject", token),
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
        store.create_project("meetings")
        with pytest.raises(ValueError):
            store.add_source("meet", "google_meet", config(credentials_env=document))
        assert store.list_sources() == []
    finally:
        store.close()
    assert not leaked(dump(tmp_path / "cairn.db"), fake)


# Signing in


def test_a_service_account_asks_once_a_pull_for_the_subjects_token_with_a_signed_jwt(kind, fake):
    for n in (1, 2, 3):
        fake.meeting(n, SINCE + n * HOUR, SINCE + n * HOUR + 30 * MINUTE)
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(1), ref(2), ref(3)]

    (asked,) = fake.calls("token")
    assert (asked.method, asked.url) == ("POST", TOKEN_URL)
    assert asked.content_type.startswith("application/x-www-form-urlencoded")
    assert set(asked.form) == {"grant_type", "assertion"}
    assert asked.form["grant_type"] == JWT_GRANT
    assert asked.problem is None
    header, claims = asked.header, asked.claims
    assert (header["alg"], header["typ"], header.get("kid", KEY_ID)) == ("RS256", "JWT", KEY_ID)
    assert claims["iss"] == CLIENT_EMAIL and claims["sub"] == SUBJECT
    assert claims["scope"] == SCOPE and claims["aud"] == TOKEN_URL
    assert abs(claims["iat"] - asked.at.timestamp()) <= 60
    assert 0 < claims["exp"] - claims["iat"] <= 3600
    signed, signature = asked.form["assertion"].rsplit(".", 1)
    fake.keys.public.verify(unb64(signature), signed.encode(), padding.PKCS1v15(), hashes.SHA256())

    meet = [r for r in fake.calls() if r.endpoint != "token"]
    assert len(meet) >= 6 and {r.auth for r in meet} == {f"Bearer {fake.issued[0]}"}
    assert all(r.url.startswith(MEET + "/") for r in meet)
    assert not any(r.endpoint == "other" for r in fake.requests)

    fake.clock.now += HOUR
    read(got.cursor)
    assert len(fake.calls("token")) == 1 and len(fake.issued) == 2  # the next pull: its own


def test_a_refresh_token_asks_googles_token_endpoint_with_its_four_fields(kind, fake):
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.meeting(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR)
    got = pull(fake, opened(kind, fake, cred=USER))
    assert refs(got) == [ref(1), ref(2)]
    (asked,) = fake.calls("token")
    assert (asked.method, asked.url) == ("POST", TOKEN_URL)
    assert asked.content_type.startswith("application/x-www-form-urlencoded")
    assert asked.form == {
        "client_id": USER_CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    }
    meet = [r for r in fake.calls() if r.endpoint != "token"]
    assert meet and {r.auth for r in meet} == {f"Bearer {fake.issued[0]}"}


def sign_in(fake, cred: str, scopes: list[str], subject: str | None) -> str:
    """google_auth on its own: no kind, no source."""
    credential = load_credential(CRED_ENV, fake.env(cred))

    async def ask() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
            return await access_token(
                credential, scopes=scopes, subject=subject, http=http, clock=fake.clock
            )

    fake.new_pull()
    return asyncio.run(ask())


@pytest.mark.parametrize(
    "scopes",
    [
        ["https://www.googleapis.com/auth/calendar.events.readonly"],
        [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/documents.readonly",
        ],
    ],
    ids=["one scope", "two scopes"],
)
def test_google_auth_asks_for_the_scopes_it_is_given_and_holds_none_of_its_own(keys, scopes):
    fake = FakeGoogle(Clock(NOW), keys)
    fake.scope = " ".join(scopes)
    token = sign_in(fake, SA, scopes, SUBJECT)
    (asked,) = fake.calls("token")
    assert asked.problem is None and asked.url == TOKEN_URL
    assert asked.claims["scope"] == " ".join(scopes) and SCOPE not in asked.claims["scope"]
    assert asked.claims["sub"] == SUBJECT
    assert token == fake.issued[-1]

    token = sign_in(fake, SA, scopes, None)  # the service account acting as itself
    (asked,) = fake.calls("token")
    assert asked.problem is None and asked.url == TOKEN_URL
    assert set(asked.form) == {"grant_type", "assertion"} and asked.form["grant_type"] == JWT_GRANT
    assert asked.claims["scope"] == " ".join(scopes) and "sub" not in asked.claims
    assert asked.claims["iss"] == CLIENT_EMAIL and asked.claims["aud"] == TOKEN_URL
    assert token == fake.issued[-1]

    token = sign_in(fake, USER, scopes, None)
    (asked,) = fake.calls("token")
    assert asked.problem is None and asked.url == TOKEN_URL
    assert asked.form == {
        "client_id": USER_CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    }
    assert token == fake.issued[-1]

    fake.refusal = (400, "invalid_grant", f"{CANARY} expired")
    with pytest.raises(SourceError) as refused:
        sign_in(fake, USER, scopes, None)
    said = str(refused.value)
    assert CANARY not in said and not leaked(said, fake)
    with pytest.raises(SourceError) as missing:
        load_credential(CRED_ENV, {})
    assert CRED_ENV in str(missing.value)


@pytest.mark.parametrize(
    "token_uri", ["http://127.0.0.1:8765/token", "http://localhost:8765/token"]
)
def test_a_service_account_may_name_a_token_endpoint_on_this_machine(kind, fake, token_uri):
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    got = pull(fake, opened(kind, fake, env=fake.env(SA, token_uri=token_uri)))
    assert refs(got) == [ref(1)]
    (asked,) = fake.calls("token")
    assert asked.url == token_uri and asked.claims["aud"] == TOKEN_URL


def test_a_key_file_naming_googles_older_token_address_asks_the_token_endpoint(kind, fake):
    """The Console's key files have named https://accounts.google.com/o/oauth2/token; the
    assertion still goes to https://oauth2.googleapis.com/token, once, and nowhere else."""
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    env = fake.env(SA, token_uri="https://accounts.google.com/o/oauth2/token")
    got = pull(fake, opened(kind, fake, env=env))
    assert refs(got) == [ref(1)]
    (asked,) = fake.calls("token")
    assert asked.url == TOKEN_URL and asked.claims["aud"] == TOKEN_URL
    assert not any(r.endpoint == "other" for r in fake.requests)


def test_meet_is_read_at_the_base_url_given_on_this_machine(kind, fake):
    fake.meet_base = "http://127.0.0.1:8781/v2"
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    got = pull(fake, opened(kind, fake, base_url=fake.meet_base))
    assert refs(got) == [ref(1)]
    meet = [r for r in fake.calls() if r.endpoint != "token"]
    assert meet and all(r.url.startswith(fake.meet_base + "/") for r in meet)
    assert not any(r.endpoint == "other" for r in fake.requests)


@pytest.mark.parametrize(
    "token_uri",
    [
        "https://accounts.google.com/o/oauth2/auth",
        "http://accounts.google.com/o/oauth2/token",
        "https://accounts.google.com.example.test/o/oauth2/token",
        "https://oauth2.googleapis.com/o/oauth2/token",
        "http://oauth2.googleapis.com/token",
        "https://oauth2.example.test/token",
        "https://oauth2.googleapis.com.example.test/token",
        "https://oauth2.googleapis.com@oauth2.example.test/token",
        "http://oauth2.example.test/token",
        "https://127.0.0.1.example.test/token",
    ],
)
def test_a_token_uri_on_another_host_is_refused_before_anything_is_sent(kind, fake, token_uri):
    said = str(refused_before_sending(kind, fake, env=fake.env(SA, token_uri=token_uri)))
    assert CRED_ENV in said and not leaked(said, fake)


def bad_credential(case: str, keys) -> tuple[str, dict[str, str]]:
    """(the kind of config, the environment) for each malformed credential."""

    def text(doc: object) -> dict[str, str]:
        return {CRED_ENV: json.dumps(doc)}

    return {
        "missing": (USER, {"ANOTHER_VARIABLE": json.dumps(authorized_user())}),
        "empty": (USER, {CRED_ENV: "  "}),
        "not json": (USER, {CRED_ENV: f"client_secret={CLIENT_SECRET}"}),
        "a JSON array": (USER, text([authorized_user()])),
        "no type": (USER, text(authorized_user(type=None))),
        "another type": (USER, text(authorized_user(type="external_account"))),
        "a key file without its key": (SA, text(service_account(keys, private_key=None))),
        "a key file without its email": (SA, text(service_account(keys, client_email=None))),
        "a key that does not load": (SA, text(service_account(keys, private_key=broken_key()))),
        "a refresh token without the secret": (USER, text(authorized_user(client_secret=None))),
        "a refresh token without the token": (USER, text(authorized_user(refresh_token=None))),
        "a refresh token without the client": (USER, text(authorized_user(client_id=None))),
    }[case]


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "empty",
        "not json",
        "a JSON array",
        "no type",
        "another type",
        "a key file without its key",
        "a key file without its email",
        "a key that does not load",
        "a refresh token without the secret",
        "a refresh token without the token",
        "a refresh token without the client",
    ],
)
def test_a_malformed_credential_is_refused_naming_the_variable_and_no_value(kind, fake, case):
    cred, env = bad_credential(case, fake.keys)
    said = str(refused_before_sending(kind, fake, cred=cred, env=env))
    assert CRED_ENV in said
    assert not leaked(said, fake) and NOT_A_KEY not in said
    for value in (CLIENT_EMAIL, KEY_ID, SA_CLIENT_ID, USER_CLIENT_ID):
        assert value not in said


def test_subject_is_required_with_a_service_account_and_refused_with_a_refresh_token(kind, fake):
    for cred, settings in ((SA, {"subject": None}), (USER, {"subject": SUBJECT})):
        checked = kind.check(config(cred, **settings))
        http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
        with pytest.raises(SourceError) as refused:
            kind.open(
                "meet", checked, http=http, env=fake.env(cred), clock=fake.clock,
                sleep=fake.sleep, deadline=None, since=SINCE,
            )  # fmt: skip
        said = str(refused.value)
        assert "subject" in said, cred
        assert not leaked(said, fake) and CLIENT_EMAIL not in said
    assert fake.requests == []


@pytest.mark.parametrize("cred", [SA, USER])
def test_the_secrets_are_the_sources_and_never_in_a_url_a_log_or_the_cursor(
    kind, fake, cred, caplog
):
    caplog.set_level(logging.DEBUG)
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.meeting(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR)
    source = opened(kind, fake, cred=cred)
    kept = tuple(source.secrets)
    assert all(isinstance(secret, str) and len(secret) >= 16 for secret in kept if secret)
    if cred == SA:
        assert holds_secret(fake.keys.pem, kept)
    else:
        assert holds_secret(f"said {CLIENT_SECRET} once", kept)
        assert holds_secret(f"said {REFRESH_TOKEN} once", kept)

    got = pull(fake, source)
    assert refs(got) == [ref(1), ref(2)]
    (token,) = fake.issued
    assert holds_secret(f"the token {token} again", tuple(source.secrets))
    for request in fake.requests:
        assert not leaked(request.url, fake)
        assert not {"access_token", "key", "token"} & set(request.params)
    assert not leaked(caplog.text, fake)
    assert not leaked(got.cursor, fake)
    for draft in got.drafts:
        assert not leaked(json.dumps(vars(draft)), fake)


TOKEN_REFUSALS = [
    (SA, 400, "unauthorized_client", "Unauthorized client or scope in request.", ("delegation",)),
    (
        SA,
        401,
        "unauthorized_client",
        "Client is unauthorized to retrieve access tokens using this method, or client not"
        " authorized for any of the scopes requested.",
        ("delegation",),
    ),
    (SA, 400, "access_denied", f"{CANARY} scope not granted", ("delegation",)),
    (SA, 403, "access_denied", f"{CANARY} scope not granted", ("delegation",)),
    (SA, 400, "invalid_grant", "Invalid email or User ID.", ("clock",)),
    (
        SA,
        400,
        "invalid_grant",
        "Invalid JWT: Token must be a short-lived token (60 minutes) and in a reasonable"
        " timeframe. Check your 'iat' and 'exp' values and use a clock with skew to account for"
        " clock differences between systems.",
        ("clock",),
    ),
    (SA, 400, "admin_policy_enforced", f"{CANARY} policy", None),
    (SA, 401, "invalid_client", f"{CANARY} client", None),
    (SA, 400, "disabled_client", "The OAuth client was disabled.", None),
    (SA, 400, "org_internal", "This client is restricted to users within its organization.", None),
    (SA, 400, "invalid_scope", "Invalid OAuth scope or ID token audience provided.", None),
    (USER, 400, "invalid_grant", "Token has been expired or revoked.", ("revoked", "expired")),
    (USER, 401, "invalid_grant", f"{CANARY} bad grant", ("revoked", "expired")),
    (USER, 401, "invalid_client", f"{CANARY} client", None),
    (USER, 400, "deleted_client", f"{CANARY} deleted", None),
    (USER, 400, "server_error", f"{CANARY} a code the notes do not list", None),
]


@pytest.mark.parametrize(("cred", "status", "error", "description", "words"), TOKEN_REFUSALS)
def test_a_refusal_by_the_token_endpoint_is_a_sentence_without_the_body(
    kind, fake, cred, status, error, description, words
):
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.refusal = (status, error, description)
    said = str(failed_pull(fake, opened(kind, fake, cred=cred)))
    assert said.strip()
    assert CANARY not in said and description not in said
    assert not leaked(said, fake) and CLIENT_EMAIL not in said
    if words:
        assert any(word in said.lower() for word in words), said
    assert fake.calls("token") and all(r.endpoint == "token" for r in fake.calls())


def test_the_token_refusals_an_operator_must_tell_apart_are_different_sentences(kind, fake):
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    said = {}
    for cred, error in ((SA, "access_denied"), (SA, "invalid_grant"), (USER, "invalid_grant")):
        fake.refusal = (400, error, CANARY)
        said[cred, error] = str(failed_pull(fake, opened(kind, fake, cred=cred)))
    assert len(set(said.values())) == 3, said


# What it reads, and how it finds it


@pytest.mark.parametrize("google", ["lists-everything", "hides-what-is-unfinished"])
def test_the_first_pull_writes_what_ended_since_switch_on_and_later_pulls_the_rest(
    kind, fake, google
):
    hiding = google == "hides-what-is-unfinished"
    fake.lists_unfinished = fake.lists_ongoing = not hiding
    fake.omit_empty = hiding
    fake.digits = 9 if hiding else 3
    fake.meeting(1, SINCE - 3 * HOUR, SINCE - HOUR)  # ended before it was switched on
    fake.meeting(2, SINCE - 5 * HOUR, SINCE + HOUR)  # began before, ended after
    fake.meeting(3, SINCE - HOUR, SINCE)  # ended right as it was switched on
    fake.meeting(4, SINCE + 2 * HOUR, SINCE + 3 * HOUR)
    fake.meeting(5, NOW - 3 * HOUR, NOW - HOUR, ready=NOW + 2 * HOUR)  # still being generated
    fake.meeting(6, SINCE + 4 * HOUR, SINCE + 5 * HOUR, transcripts=[])  # nobody transcribed it
    fake.meeting(7, NOW - 20 * MINUTE, NOW + 20 * MINUTE, ready=NOW + 2 * HOUR)  # going on
    read = reader(kind, fake)

    first = read()
    assert refs(first) == [ref(3), ref(2), ref(4)]
    assert first.skipped == []
    assert {draft.project for draft in first.drafts} == {"meetings"}
    (bound,) = {r.bound for r in fake.calls("records")}  # every page: the same filter
    assert abs(bound - (SINCE - DAY)) <= HOUR

    fake.clock.now = NOW + HOUR
    second = read(first.cursor)
    assert second.drafts == [] and second.skipped == []
    fake.clock.now = NOW + 3 * HOUR
    third = read(second.cursor)
    assert refs(third) == [ref(5), ref(7)]
    fake.clock.now = NOW + 4 * HOUR
    fourth = read(third.cursor)
    assert fourth.drafts == [] and fourth.skipped == []

    read_entries = fake.calls("entries", every=True)
    assert read_entries and all(r.state == "FILE_GENERATED" for r in read_entries)
    assert not any(r.conference == cid(1) for r in read_entries)


@pytest.mark.parametrize("google", ["lists-unfinished", "hides-unfinished"])
def test_a_transcript_never_generated_is_dropped_after_wait_hours_and_reported_once(
    kind, fake, google
):
    fake.lists_unfinished = google == "lists-unfinished"
    end = NOW - HOUR
    fake.meeting(1, end - HOUR, end, ready=None)  # transcribed, never generated
    fake.meeting(2, end - HOUR, end, ready=end + 5 * HOUR)  # generated within the wait
    fake.meeting(3, end - HOUR, end, transcripts=[])  # nobody transcribed it
    read = reader(kind, fake, wait_hours=6)
    window = end + 6 * HOUR
    cursor, written, skipped, looked = None, [], [], {}
    for step in range(12):
        fake.clock.now = end + 90 * MINUTE + step * HOUR
        if fake.clock.now > window + 2 * HOUR:  # generated, too late
            fake.conferences[cid(1)].transcripts[0].generated = fake.clock.now
        got = read(cursor)
        cursor = got.cursor
        written += [(fake.clock.now, found) for found in refs(got)]
        skipped += [(fake.clock.now, skip) for skip in got.skipped]
        looked[fake.clock.now] = {r.conference for r in fake.calls() if r.conference}

    assert written == [(end + 330 * MINUTE, ref(2))]
    assert cid(1) in looked[end + 330 * MINUTE]  # still looked at within the wait
    if fake.lists_unfinished:
        ((at, skip),) = skipped
        assert window < at <= window + HOUR
        assert cid(1) in skip.ref and "no transcript was generated" in skip.reason.lower()
    else:  # Google never showed it a transcript: nothing to say
        assert skipped == []
    for at, seen in looked.items():
        if at >= window + 2 * HOUR:
            assert not {cid(1), cid(3)} & seen, at


@pytest.mark.parametrize("google", ["lists-ongoing", "hides-ongoing"])
def test_a_meeting_still_going_on_is_looked_at_again(kind, fake, google):
    fake.lists_ongoing = google == "lists-ongoing"
    fake.meeting(1, NOW - 10 * MINUTE, NOW + 30 * MINUTE, ready=NOW + 50 * MINUTE)
    fake.meeting(2, NOW - 2 * HOUR, NOW - HOUR)
    read = reader(kind, fake)
    cursor, written = None, []
    for step in range(6):
        fake.clock.now = NOW + step * 30 * MINUTE
        got = read(cursor)
        cursor = got.cursor
        written += [(step, found) for found in refs(got)]
    assert written == [(0, ref(2)), (2, ref(1))]


@pytest.mark.parametrize("google", ["lists-ongoing", "hides-ongoing"])
def test_a_meeting_of_20_hours_is_found_once_it_has_ended(kind, fake, google):
    """Where Google lists a meeting only once it has ended, a later pull must still reach back
    to its start: every pull's lower bound is a day before the cursor's mark."""
    fake.lists_ongoing = google == "lists-ongoing"
    start, end = NOW - 3 * HOUR, NOW + 17 * HOUR
    fake.meeting(1, start, end, ready=end + 30 * MINUTE)
    read = reader(kind, fake)
    cursor, written, skipped = None, [], []
    for step in range(17):  # a pull every two hours, through the meeting and a day after
        fake.clock.now = NOW + step * 2 * HOUR
        got = read(cursor)
        cursor = got.cursor
        written += [(fake.clock.now, found) for found in refs(got)]
        skipped += got.skipped
    assert written == [(NOW + 18 * HOUR, ref(1))]
    assert skipped == []


@pytest.mark.parametrize("google", ["lists-unfinished", "hides-unfinished"])
def test_a_transcript_listed_hours_after_another_of_its_meeting_is_still_written(
    kind, fake, google
):
    """One look does not settle a meeting: where Google lists a transcript only once its file
    is generated, a meeting with one transcript written and another still being generated looks
    finished, and is looked at again until `wait_hours` after it ended."""
    fake.lists_unfinished = google == "lists-unfinished"
    start, end = NOW - 2 * HOUR, NOW - HOUR
    meeting = fake.meeting(1, start, end, ready=end + 10 * MINUTE)
    later = start + 30 * MINUTE
    meeting.transcripts.append(Transcript(tid(1, 1), later, end + 5 * HOUR, chat(1, later)))
    read = reader(kind, fake)
    cursor, written, skipped = None, [], []
    for at in (NOW, NOW + 6 * HOUR, NOW + 12 * HOUR, NOW + DAY, NOW + 2 * DAY, NOW + 3 * DAY):
        fake.clock.now = at
        got = read(cursor)
        cursor = got.cursor
        written += [(at, found) for found in refs(got)]
        skipped += got.skipped
    assert written == [(NOW, ref(1)), (NOW + 6 * HOUR, ref(1, 1))]
    assert skipped == []


@pytest.mark.parametrize("order", ["documented", "shuffled"])
def test_drafts_come_by_end_time_then_name_whatever_order_google_lists_them_in(kind, fake, order):
    fake.shuffle = order == "shuffled"
    base = SINCE + HOUR
    fake.meeting(1, base, base + 2 * HOUR)
    fake.meeting(2, base + 30 * MINUTE, base + HOUR)
    fake.meeting(3, base + 10 * MINUTE, base + 3 * HOUR)
    fake.meeting(4, base + 20 * MINUTE, base + 3 * HOUR)  # ends with 3: by name
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(2), ref(1), ref(3), ref(4)]


def test_records_entries_and_participants_are_read_across_pages_of_any_shape(kind, fake):
    crowd = [
        Person(
            f"q{j:04d}", ("signedinUser", "anonymousUser", "phoneUser")[j % 3], f"Person {j:04d}"
        )
        for j in range(260)
    ]

    def stocked(mode: str) -> FakeGoogle:
        stand_in = FakeGoogle(Clock(NOW), fake.keys)
        stand_in.short_pages = stand_in.omit_empty = mode == "short pages"
        stand_in.digits = {"documented": 3, "short pages": 9, "shuffled": 0}[mode]
        stand_in.shuffle = mode == "shuffled"
        for n in range(1, 131):
            start = SINCE + n * 10 * MINUTE
            stand_in.meeting(n, start, start + 5 * MINUTE, ready=start + 6 * MINUTE)
        start = SINCE + 22 * HOUR
        said = [
            Entry(
                crowd[(k * 7) % 260].pid,
                f"Line {k:03d} of the review.",
                start + k * 4 * MINUTE / 60,
            )
            for k in range(250)
        ]
        review = Transcript(tid(131), start, start + 31 * MINUTE, said)
        stand_in.meeting(131, start, start + 30 * MINUTE, people=crowd, transcripts=[review])
        return stand_in

    seen = {}
    for mode in ("documented", "short pages", "shuffled"):
        stand_in = stocked(mode)
        got = pull(stand_in, opened(kind, stand_in))
        assert refs(got) == [ref(n) for n in range(1, 132)], mode
        seen[mode] = [(d.source_ref, d.title, d.topic, d.text, d.project) for d in got.drafts]
        review = got.drafts[-1].text
        for j in range(260):
            assert f"Person {j:04d}" in review, (mode, j)
        for k in range(250):
            assert f"Line {k:03d} of the review." in review, (mode, k)

        lists = stand_in.calls("records")
        assert all(r.status == 200 for r in lists), mode
        assert len({r.params.get("filter") for r in lists}) == 1
        assert "pageToken" not in lists[0].params
        assert all("pageToken" in r.params for r in lists[1:])
        assert len(lists) >= 2  # 131 records do not fit one page
        entries = [r for r in stand_in.calls("entries") if r.transcript == tid(131)]
        if mode == "documented":
            assert len(entries) == 3  # the largest page: 100, 100, 50
    assert seen["documented"] == seen["short pages"] == seen["shuffled"]


@pytest.mark.parametrize("organizer_only", [False, True], ids=["joined", "organized"])
def test_whatever_google_lists_for_the_person_is_read(kind, fake, organizer_only):
    fake.organizer_only = organizer_only
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.meeting(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR, own=False)
    got = pull(fake, opened(kind, fake))
    assert refs(got) == ([ref(1)] if organizer_only else [ref(1), ref(2)])


# What it writes


def test_the_draft_names_the_meeting_and_gives_a_line_a_turn(kind, fake):
    start = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    people = [*CAST, Person("p5", "signedinUser", None)]
    said = [
        Entry("p1", "Morning, everyone.", start + MINUTE),
        Entry("p1", "Let's start with refunds.", start + MINUTE + timedelta(seconds=3)),
        Entry("p2", "The retry queue is green.", start + timedelta(minutes=2, seconds=30)),
        Entry("p5", "Can everyone hear me?", start + 4 * MINUTE),
        Entry("p3", "I joined from the lobby.", start + timedelta(minutes=4, seconds=30)),
        Entry("p9", "Sorry, wrong room.", start + 5 * MINUTE),  # not among the participants
        Entry("p1", "Good, then we ship Friday.", start + timedelta(hours=1, minutes=2, seconds=3)),
    ]
    later = [Entry("p2", "Back after the break.", start + 40 * MINUTE)]
    transcripts = [
        Transcript(tid(1, 0), start + MINUTE, start + 70 * MINUTE, said),
        Transcript(tid(1, 1), start + 40 * MINUTE, start + 70 * MINUTE, later),
    ]
    end = start + 65 * MINUTE
    fake.meeting(1, start, end, code="abc-mnop-xyz", people=people, transcripts=transcripts)
    afternoon = datetime(2026, 11, 12, 14, 30, tzinfo=UTC)
    fake.meeting(2, afternoon, afternoon + 30 * MINUTE, code=None)

    got = pull(fake, opened(kind, fake, since=start - HOUR))
    assert sorted(refs(got)) == sorted([ref(1, 0), ref(1, 1), ref(2)])
    drafts = {draft.source_ref: draft for draft in got.drafts}
    first, second, plain = drafts[ref(1, 0)], drafts[ref(1, 1)], drafts[ref(2)]

    title = first.title
    assert re.fullmatch(r"Meet\W+abc-mnop-xyz\W+2026-11-12 09:00(\W+UTC)?\W*", title), title
    assert first.topic == second.topic == second.title == title  # one page for the meeting
    assert re.fullmatch(r"Meet\W+2026-11-12 14:30(\W+UTC)?\W*", plain.title), plain.title
    assert plain.topic == plain.title and plain.topic != title
    for draft in got.drafts:
        assert (draft.label, draft.project) == ("google-meet", "meetings")

    text = first.text
    assert re.fullmatch(r"#{1,6} " + re.escape(title), text.splitlines()[0])
    opening = line_with(text, "Morning, everyone.")
    assert "Let's start with refunds." in opening  # one participant's entries, one turn
    assert opening.index("Morning") < opening.index("Let's start")
    assert "Ada Park" in opening and "00:01:00" in opening
    reply = line_with(text, "The retry queue is green.")
    assert "Brook Lane" in reply and "00:02:30" in reply
    unnamed = line_with(text, "Can everyone hear me?")
    assert "Unknown speaker" in unnamed and "00:04:00" in unnamed
    guest = line_with(text, "I joined from the lobby.")
    assert "Cy Moss" in guest and "00:04:30" in guest
    stray = line_with(text, "Sorry, wrong room.")
    assert "Unknown speaker" in stray and "00:05:00" in stray
    closing = line_with(text, "Good, then we ship Friday.")
    assert "Ada Park" in closing and "01:02:03" in closing
    assert len({opening, reply, unnamed, guest, stray, closing}) == 6
    facts = text[: text.index(opening)]
    for name in ("Ada Park", "Brook Lane", "Cy Moss", "+1 555-xxx-xx42"):
        assert name in facts, name
    assert "2026-11-12" in facts and "09:00" in facts and "10:05" in facts
    back = line_with(second.text, "Back after the break.")
    assert "Brook Lane" in back and "00:40:00" in back  # from the meeting's start

    for draft in got.drafts:
        for value in (draft.title, draft.topic, draft.text):
            assert "@" not in value and "example.test" not in value


def test_a_transcript_of_3200_turns_is_written_in_three_parts(kind, fake):
    start = SINCE + HOUR
    said = []
    for k in range(3_200):
        who = ("p1", "p2")[k % 2]
        said.append(
            Entry(who, f"Point {k:04d} about the ledger.", start + timedelta(seconds=2 * k))
        )
        if k % 3 == 0:  # a turn of two entries: more entries than turns
            said.append(Entry(who, f"(more on {k:04d})", start + timedelta(seconds=2 * k + 1)))
    transcript = Transcript(tid(1), start, start + 3 * HOUR, said)
    fake.meeting(1, start, start + 2 * HOUR, transcripts=[transcript])
    got = pull(fake, opened(kind, fake))

    parts = got.drafts
    assert [d.source_ref for d in parts] == [ref(1), ref(1) + "/2", ref(1) + "/3"]
    title = parts[0].title
    assert [d.topic for d in parts] == [title, f"{title}, part 2", f"{title}, part 3"]
    for part, (low, high) in zip(parts, [(0, 1500), (1500, 3000), (3000, 3200)], strict=True):
        held = {k for k in range(3_200) if f"Point {k:04d} " in part.text}
        assert held == set(range(low, high))
        assert (part.label, part.project) == ("google-meet", "meetings")
    assert "(more on 0000)" in line_with(parts[0].text, "Point 0000 ")
    assert "(more on 3198)" in line_with(parts[2].text, "Point 3198 ")
    assert len(fake.calls("entries")) >= 43  # every page of 4,267 entries


@pytest.mark.parametrize("empty", ["[]", "absent"])
def test_a_transcript_with_no_entries_is_skipped_once(kind, fake, empty):
    fake.omit_empty = empty == "absent"
    blank = Transcript(tid(1), SINCE + HOUR, SINCE + 3 * HOUR, [])  # entries already deleted
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR, transcripts=[blank])
    fake.meeting(2, SINCE + 4 * HOUR, SINCE + 5 * HOUR)
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(2)]
    (skip,) = got.skipped
    assert tid(1) in skip.ref and skip.reason.strip()
    fake.clock.now += HOUR
    again = read(got.cursor)
    assert again.drafts == [] and again.skipped == []


# Read once, and the cursor


def test_the_cursor_holds_no_secret_name_or_text_and_stays_bounded_over_weeks(kind, fake):
    weeks = 9
    for n in range(1, weeks * 7 * 3 + 1):  # a meeting every eight hours
        start = NOW + n * 8 * HOUR - 4 * HOUR
        end = start + 30 * MINUTE
        if n % 5 == 0:
            fake.meeting(n, start, end, transcripts=[])
        elif n % 7 == 0:
            fake.meeting(n, start, end, ready=None)
        else:
            fake.meeting(n, start, end, ready=end + HOUR)
    read = reader(kind, fake, cred=USER, since=NOW)
    cursor, written, skipped, lengths = None, Counter(), Counter(), {}
    for step in range(1, weeks * 7 * 4 + 1):  # a pull every six hours
        fake.clock.now = NOW + step * 6 * HOUR
        got = read(cursor)
        cursor = got.cursor
        written.update(refs(got))
        for skip in got.skipped:
            assert "no transcript was generated" in skip.reason.lower()
            skipped.update(c.n for c in fake.conferences.values() if cid(c.n) in skip.ref)
        assert isinstance(json.loads(cursor), dict | list)  # opaque, but JSON
        assert not leaked(cursor, fake) and "@" not in cursor
        for words in (*NAMES, "release to Friday", "agrees to item", "Item "):
            assert words not in cursor, words
        assert not any(found.rsplit("/", 1)[1] in cursor for found in written)
        if step == 6 * 7 * 4:
            lengths["six weeks"] = len(cursor)

    last = fake.clock.now
    made = list(fake.conferences.values())
    expected = [c.n for c in made if c.transcripts and c.transcripts[0].generated]
    assert written == Counter({ref(n): 1 for n in expected})
    for c in made:
        never = c.transcripts and c.transcripts[0].generated is None
        if never and c.end + 54 * HOUR <= last:  # the wait and a pull have passed
            assert skipped[c.n] == 1, c.n
        elif never and c.end + 48 * HOUR < last:
            assert skipped[c.n] <= 1, c.n
        else:
            assert skipped[c.n] == 0, c.n
    assert len(cursor) <= 1.25 * lengths["six weeks"] + 200  # it did not grow with the meetings
    bounds = [r.bound for r in fake.calls("records")]
    assert bounds and last - min(bounds) < 4 * DAY


# When Google says no


@pytest.mark.parametrize(
    ("endpoint", "how"),
    [
        ("records", 401),
        ("records", 403),
        ("records", "403, errors"),
        ("transcripts", 403),
        ("entries", 401),
        ("participants", 403),
    ],
)
def test_access_refused_by_meet_says_what_to_check(kind, fake, endpoint, how):
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.always[endpoint] = how
    said = str(failed_pull(fake, opened(kind, fake)))
    assert "scope" in said.lower()
    assert CANARY not in said and not leaked(said, fake)
    assert fake.slept == []


def test_a_meeting_or_a_transcript_gone_since_it_was_listed_is_skipped_once(kind, fake):
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR).gone = True
    fake.meeting(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR).transcripts[0].gone = True
    fake.meeting(3, SINCE + 5 * HOUR, SINCE + 6 * HOUR)
    # Its first transcript is gone; its second is still being generated, so the meeting is
    # looked at again, and the gone one must not be reported a second time.
    four = fake.meeting(4, SINCE + 7 * HOUR, SINCE + 8 * HOUR)
    four.transcripts[0].gone = True
    later = four.start + 30 * MINUTE
    four.transcripts.append(Transcript(tid(4, 1), later, NOW + 30 * MINUTE, chat(4, later)))
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(3)]
    assert len(got.skipped) == 3
    (meeting,) = [s for s in got.skipped if cid(1) in s.ref]
    (transcript,) = [s for s in got.skipped if tid(2) in s.ref]
    (first,) = [s for s in got.skipped if tid(4) in s.ref]
    assert "gone" in meeting.reason.lower() and "gone" in transcript.reason.lower()
    assert "gone" in first.reason.lower()
    fake.clock.now += HOUR
    again = read(got.cursor)
    assert refs(again) == [ref(4, 1)] and again.skipped == []


@pytest.mark.parametrize(
    "how", ["rate limited", "rate limited, no body", "quota 403, details", "quota 403, errors"]
)
def test_rate_limits_are_retried_with_the_documented_backoff_through_sleep(kind, fake, how):
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.forced = [("entries", how), ("entries", how)]
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(1)]
    assert len(fake.slept) == 2
    backed_off(fake.slept)

    fake.slept.clear()
    fake.always["records"] = how
    said = str(failed_pull(fake, opened(kind, fake)))
    assert 1 <= len(fake.slept) <= 4
    backed_off(fake.slept)
    assert len(fake.calls("records")) == len(fake.slept) + 1
    assert CANARY not in said and not leaked(said, fake)


@pytest.mark.parametrize(
    ("endpoint", "how"),
    [
        ("records", 500),
        ("records", 503),
        ("transcripts", 500),
        ("entries", 502),
        ("participants", "timeout"),
        ("records", "timeout"),
        ("records", "not json"),
        ("records", "wrong shape"),
        ("records", "record without startTime"),
        ("transcripts", "array"),
        ("entries", "wrong shape"),
        ("token", 500),
        ("token", "timeout"),
        ("token", "not json"),
        ("token", "no access_token"),
    ],
)
def test_a_server_error_a_timeout_or_an_odd_body_fails_the_pull(kind, fake, endpoint, how):
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    fake.meeting(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR)
    fake.always[endpoint] = how
    said = str(failed_pull(fake, opened(kind, fake)))
    assert said.strip() and CANARY not in said and not leaked(said, fake)


# The deadline


def test_a_pull_cut_by_its_deadline_and_every_resume_point_go_on_without_loss_or_repeat(kind, fake):
    for n in range(1, 5):
        start = SINCE + n * HOUR
        fake.meeting(n, start, start + 30 * MINUTE)
    two = fake.conferences[cid(2)]
    two.transcripts.append(
        Transcript(tid(2, 1), two.start + 20 * MINUTE, two.end + 10 * MINUTE, chat(2, two.start))
    )
    start = SINCE + 6 * HOUR
    said = [
        Entry(("p1", "p2")[k % 2], f"Step {k:04d} of the plan.", start + timedelta(seconds=2 * k))
        for k in range(1_600)
    ]
    fake.meeting(
        5, start, start + HOUR, transcripts=[Transcript(tid(5), start, start + 2 * HOUR, said)]
    )

    whole = pull(fake, opened(kind, fake))
    full = refs(whole)
    assert sorted(full) == sorted(
        [ref(1), ref(2, 0), ref(2, 1), ref(3), ref(4), ref(5), ref(5) + "/2"]
    )
    assert all(draft.resume for draft in whole.drafts)
    for index, draft in enumerate(whole.drafts):  # a run that stops right after this draft
        fake.clock.now += HOUR
        rest = pull(fake, opened(kind, fake), draft.resume)
        assert refs(rest) == full[index + 1 :], index

    fake.clock.now += HOUR
    fake.tick = MINUTE  # every request takes a minute
    hurried = opened(kind, fake, deadline=fake.clock.now + 15 * MINUTE)
    cut = pull(fake, hurried)
    assert 0 < len(cut.drafts) < len(full)
    assert refs(cut) == full[: len(cut.drafts)]
    assert all(draft.resume for draft in cut.drafts)
    fake.tick = timedelta(0)
    fake.clock.now += HOUR
    rest = pull(fake, opened(kind, fake), cut.cursor)
    assert refs(rest) == full[len(cut.drafts) :]


# Together with the framework


@pytest.fixture
def world(tmp_path, fake):
    clock = Clock(SINCE - DAY)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    for name in ("meetings", "planning"):
        store.create_project(name)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    yield SimpleNamespace(store=store, db=db, clock=clock, http=http)
    store.close()


def run(world, fake, at: datetime, cred: str = SA):
    fake.clock.now = at
    fake.new_pull()
    return asyncio.run(
        run_source(
            world.store, "meet", http=world.http, env=fake.env(cred), clock=fake.clock,
            sleep=fake.sleep,
        )
    )  # fmt: skip


def notes(world, project: str = "meetings") -> list:
    return world.store.list_notes(world.store.get_project(project).id)


def pages(world, project: str = "meetings") -> list:
    return world.store.list_pages(world.store.get_project(project).id)


def test_through_the_runner_a_source_is_off_until_switched_on_then_writes_each_meeting_once(
    world, fake
):
    fake.meeting(1, SINCE - 3 * HOUR, SINCE - HOUR)
    two = fake.meeting(2, SINCE - 5 * HOUR, SINCE + HOUR)
    later = two.start + 2 * HOUR
    two.transcripts.append(Transcript(tid(2, 1), later, two.end + 10 * MINUTE, chat(2, later)))
    fake.meeting(3, SINCE + 2 * HOUR, SINCE + 3 * HOUR)
    fake.meeting(4, NOW - 3 * HOUR, NOW - HOUR, ready=NOW + 2 * HOUR)
    fake.meeting(5, SINCE + 4 * HOUR, SINCE + 5 * HOUR, transcripts=[])

    added = world.store.add_source("meet", "google_meet", config(projects=[]))
    assert added.enabled is False
    assert run(world, fake, NOW).off is True and fake.requests == []

    world.clock.now = SINCE
    world.store.enable_source("meet", projects=["meetings"])
    report = run(world, fake, NOW)
    assert (report.saved, report.unchanged, report.refused, report.rejected) == (3, 0, 0, 0)
    assert report.skipped == 0
    held = notes(world)
    assert sorted(c.source_ref for c in held) == sorted([ref(2, 0), ref(2, 1), ref(3)])
    assert {(c.via, c.label) for c in held} == {("google-meet", "google-meet")}
    assert len(pages(world)) == 2  # a page a meeting
    assert notes(world, "planning") == []
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind) == ("source.synced", "system")
    assert (event.detail["source"], event.detail["saved"]) == ("meet", 3)

    again = run(world, fake, NOW + 5 * MINUTE)
    assert (again.saved, again.unchanged, again.skipped) == (0, 0, 0)
    after = run(world, fake, NOW + 3 * HOUR)  # the fourth meeting's transcript is ready
    assert after.saved == 1 and ref(4) in {c.source_ref for c in notes(world)}
    assert len(pages(world)) == 3

    assert not leaked(dump(world.db), fake)
    for event in world.store.list_audit(limit=100):
        assert not leaked(json.dumps(event.detail), fake)


@pytest.mark.parametrize(
    ("cred", "echoes"), [(SA, ("token",)), (USER, ("token", "refresh", "secret"))]
)
def test_through_the_runner_a_secret_google_sends_back_is_never_stored(world, fake, cred, echoes):
    for n, echo in enumerate(echoes, start=1):
        fake.meeting(n, SINCE + n * HOUR, SINCE + n * HOUR + 30 * MINUTE).transcripts[0].echo = echo
    fake.meeting(9, SINCE + 9 * HOUR, SINCE + 10 * HOUR)
    world.store.add_source("meet", "google_meet", config(cred))
    world.clock.now = SINCE
    world.store.enable_source("meet")
    report = run(world, fake, NOW, cred=cred)
    assert report.saved == 1 and [c.source_ref for c in notes(world)] == [ref(9)]
    assert report.reasons.get(HELD_CREDENTIALS) == len(echoes)
    assert not leaked(dump(world.db), fake)


def test_through_the_runner_a_failed_run_moves_no_cursor_and_the_next_writes_the_rest(world, fake):
    world.store.add_source("meet", "google_meet", config())
    world.clock.now = SINCE
    world.store.enable_source("meet")
    fake.meeting(1, SINCE + HOUR, SINCE + 2 * HOUR)
    assert run(world, fake, NOW).saved == 1
    before = world.store.get_source("meet")

    fake.meeting(2, NOW + HOUR, NOW + 2 * HOUR)
    fake.meeting(3, NOW + 3 * HOUR, NOW + 4 * HOUR)
    fake.forced = [("entries", 500)]  # the first of the two new transcripts cannot be read
    with pytest.raises(SourceError):
        run(world, fake, NOW + 5 * HOUR)
    failed = world.store.get_source("meet")
    assert failed.cursor == before.cursor and failed.last_error
    assert CANARY not in failed.last_error and not leaked(failed.last_error, fake)
    assert len(notes(world)) == 1

    report = run(world, fake, NOW + 6 * HOUR)
    assert report.saved == 2 and world.store.get_source("meet").last_error is None
    assert sorted(c.source_ref for c in notes(world)) == [ref(1), ref(2), ref(3)]
    assert run(world, fake, NOW + 7 * HOUR).saved == 0


def test_the_command_line_adds_switches_on_and_syncs_without_showing_a_secret(
    tmp_path, kind, keys, monkeypatch, capsys
):
    db = tmp_path / "cairn.db"
    store = Store(db)
    store.create_project("meetings")
    store.close()
    fake = FakeGoogle(RealClock(), keys)
    monkeypatch.setenv(CRED_ENV, fake.env(SA)[CRED_ENV])
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

    settings = json.dumps(config(projects=[]))
    assert cli("source-add", "--db", str(db), "--name", "meet", "--kind", "google_meet",
               "--config", settings) == 0  # fmt: skip
    assert cli("source-sync", "--db", str(db), "--name", "meet") == 0
    assert fake.requests == []  # off: nothing read, no token asked for
    assert cli("source-enable", "--db", str(db), "--name", "meet", "--project", "meetings") == 0

    store = Store(db)
    try:
        since = parsed(store.get_source("meet").enabled_at)
    finally:
        store.close()
    fake.meeting(1, since - 2 * HOUR, since - HOUR, ready=since - 30 * MINUTE)
    just_after = since + timedelta(milliseconds=2)
    fake.meeting(2, since - 30 * MINUTE, just_after, ready=just_after)
    while datetime.now(UTC) <= just_after + timedelta(milliseconds=1):
        pass  # the real clock, which the command line reads, has passed it
    assert cli("source-sync", "--db", str(db), "--name", "meet") == 0
    assert cli("source-list", "--db", str(db)) == 0
    assert len(fake.calls("token", every=True)) == 1

    monkeypatch.setenv(CRED_ENV, json.dumps(service_account(keys, private_key=broken_key())))
    assert cli("source-sync", "--db", str(db), "--name", "meet") == 1
    assert CRED_ENV in said[-1]

    store = Store(db)
    try:
        held = store.list_notes(store.get_project("meetings").id)
        assert [c.source_ref for c in held] == [ref(2)]
    finally:
        store.close()
    shown = "\n".join(said)
    assert not leaked(shown, fake) and CANARY not in shown and NOT_A_KEY not in shown
    assert not leaked(dump(db), fake)
