"""C6 acceptance tests (frozen): the Gmail source, which reads only the messages a person labels.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/sources/gmail.py, src/cairn/sources/markup.py
and src/cairn/sources/__init__.py. Gmail's request and response shapes are recorded in
docs/gmail-api.md; signing in to Google is cairn.sources.google_auth (C4), not tested again
here. Where the notes say Gmail's pages are silent or disagree, the connector depends on
neither reading: the stand-in below behaves both ways, and the tests run under both.

Why: a mailbox holds other people's words, most of it noise, and what was copied cannot be
taken back. So this source reads only what a person marks: they put one Gmail label on a
message, and cairn reads that message once. There is no "all mail" setting.

The kind
- `KINDS["gmail"]` (cairn.sources.gmail). It starts off. Via and label "gmail". It asks for
  one scope, https://www.googleapis.com/auth/gmail.readonly (never one that can send, change or
  delete). `default_project` is "mail". `describe(config)` says in one sentence whose mail it
  reads and under which label, never a credential or a variable's value. `switch_on` says that
  it reads the messages that arrive from then on and carry the label.
- `check(config)` returns the config with its defaults filled in, or raises ValueError naming
  the setting at fault, never a value that could be a credential:
  - `credentials_env` (required): the name of the variable that holds the Google credential.
  - `subject` (optional here): one email address, the person whose mailbox is read.
  - `label` (required): the name of the Gmail label that marks a message for cairn: the
    `name` Gmail's API gives the label (`users.labels.list`). Google's pages do not state how
    a nested label is named there; expect its parents in front, "parent/child". 1 to 200
    characters, no control character. Gmail's own label names are refused whatever their case:
    INBOX, SENT, DRAFT, SPAM, TRASH, UNREAD, STARRED, IMPORTANT, CHAT, and anything beginning
    CATEGORY_.
  - `lookback_days`: 30 unless given, a whole number from 1 to 365: how long after a message
    arrived its label is still noticed.
  - `projects`: at most one project; may be empty while the source is off.
  - `base_url`: "https://gmail.googleapis.com/gmail/v1" unless given; https, or http on this
    machine.
  - No other setting.
- Opened: a service account needs `subject` (SourceError naming `subject`); a refresh-token
  credential must not have one.

What it reads
- Messages of that one mailbox that carry the label, arrived (`internalDate`) at or after
  `since`, and labelled within `lookback_days` of arriving. A message that arrived before
  `since` is never written, even when it is labelled afterwards; one that arrived more than
  `lookback_days` before the pull that finds it is not written either, even when the listing's
  day of margin returns it.
- Each message once. Taking the label off, or trashing the message, after it was read changes
  nothing in cairn. The unit is a message, not a thread: a reply that arrives later in a
  labelled conversation is not read until it carries the label itself.
- Not read, silently: a message in Spam or Trash, and a draft (`labelIds` holds DRAFT).
- Every pull resolves the label's name to its id with `users.labels.list`: an exact match on
  `name`; else a match ignoring case when exactly one label matches; otherwise SourceError
  naming the `label` setting and saying to make the label in Gmail. A name that resolves to a
  label whose `type` is not "user": SourceError naming `label`.
- It lists `users.messages.list` with `labelIds` that id, `q` "after:<epoch seconds>" a day
  before the later of `since` and now minus `lookback_days`, `maxResults` 500, no
  `includeSpamTrash`, every page with the same parameters beside `pageToken`, and does not
  rely on the order of results. `userId` is "me", or the subject's address. For each listed
  id it has not handled it calls `users.messages.get` with `format=full`, at most 50 a pull;
  the rest is for the next pull. Google's pages do not state that `q` narrows a listing by
  `labelIds`, nor that `after:` goes by `internalDate`: the connector does not depend on
  either. It draws both lines itself from each message's `internalDate`, so a listing that
  returns more than asked (every labelled message, however old) gives the same drafts; it
  only takes more pulls. A pull's drafts come oldest first: by `internalDate`, then by id.
- The cursor is JSON the connector owns: short digests of the messages it handled (written,
  or passed over for good), each kept only while a listing could still return it. It holds no
  token, key, secret, message id, subject, name or address, and stays bounded. A message whose
  `get` was rate limited, or left for the next pull, is not handled.
- A pull that reaches its `deadline` returns what it finished; every draft carries a `resume`
  from which the next pull goes on with no draft written twice and none skipped.

What it writes, one draft a message (or a part of one), into the config's project
- `title`: the Subject header, RFC 2047 encoded words decoded, or "(no subject)" when it is
  missing or empty.
- `topic`: the subject without its reply and forward prefixes (Re:, RE:, Fwd:, FW:, Fw:, AW:,
  WG:, repeated or mixed, any case; "(no subject)" when nothing is left), a comma and the UTC
  month it arrived in: "Budget review, 2026-11". The subject's part is shortened so that the
  page's slug keeps the month and a part's number: the same subject in two months is on two
  pages.
- `text`: Markdown: a first line "# <title>"; the facts (when it arrived in UTC as
  "YYYY-MM-DD HH:MM"; from, to and cc by display name, a correspondent with no name as the
  domain of their address (so is one whose name is itself an address), more than ten in to
  or cc counted as "and N more"; the names of its attachments, which are not read); then the
  body: the first text/plain part that is not an attachment, as it is, else the first
  text/html part through `cairn.sources.markup.html_to_text`, the part tree walked to any
  depth, data base64url with or without padding, decoded as UTF-8 with replacement; nothing
  comes after the body. No email address is in the facts. A message with no text part is
  skipped once ("no text to read"). A skip's `ref` holds the message id.
- A long body is split into parts, each a draft (topic "..., part 2"): at most 1,500 passages
  a part, as cairn counts them (a paragraph, a list item, a quoted block), cut between two.
- `source_ref`: "gmail:<message id>" ("/2", "/3" for later parts). `label`: "gmail".

`html_to_text(html) -> str`: one function for every source's HTML; the examples below are its
contract, word for word (its rules are in cairn.sources.markup's docstring; the same pairs are
frozen with the Calendar source, tests/acceptance/test_c5_google_calendar.py).

When Google says no
- 401, and 403 for a reason that is not a rate limit: SourceError saying access was refused and
  what to check (the word "scope"), without a token or the body, not retried.
- 404 for a message listed a moment ago: skipped ("gone"), once.
- 429, and 403 `rateLimitExceeded` or `userRateLimitExceeded`: retried at most 4 times, the
  n-th wait (through the injected `sleep`) 2^n seconds plus up to one second, n from 0 or 1.
  When the limit outlasts the retries, and for `dailyLimitExceeded` (not retried): a pull that
  has read messages stops there and returns them with a cursor that goes on from there; one
  that has read none raises SourceError.
- A 5xx (not retried), a timeout, a body not of the documented shape: SourceError without the
  body. An error body that does not parse is treated by its status alone. A failed pull
  returns nothing.

Together with the framework: a Gmail source is off when added and reads nothing; switched on
with a project, `run_source` writes the labelled messages that arrived since, one conversation's
messages of a month on one page; again, nothing new; a message labelled later is written by the
run after. The note guard decides on a body as on any note. The database, the audit log and
the command line's output hold no private key, token, client secret or refresh token.
"""

import asyncio
import copy
import hashlib
import json
import logging
import re
import sqlite3
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, unquote

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from cairn.server import main
from cairn.sources import HELD_CREDENTIALS, KINDS, SourceError, holds_secret, markup, run_source
from cairn.store import Store

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
GMAIL = "https://gmail.googleapis.com/gmail/v1"
CRED_ENV = "GOOGLE_C6_CREDENTIALS"
SA, USER = "service_account", "authorized_user"
SUBJECT = "ada@example.test"
CLIENT_EMAIL = "cairn-mail@c6-project.iam.example.test"
KEY_ID = "c6" + "7d1e4b2a" * 4  # made up
SA_CLIENT_ID = "1" + "06" * 10
USER_CLIENT_ID = "c6-" + "desktop-client" + ".apps.example.test"
CLIENT_SECRET = "GOCSPX-" + "c6" + "MadeUp" + "Secret8e4b"  # never a real secret
REFRESH_TOKEN = "1//" + "0c6" + "MadeUpRefresh" + "Token2c9d"
NOT_A_KEY = "c6" + "NotAKey"
CANARY = "c6-" + "body-" + "canary-71fd"  # in every error body the stand-in sends
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=1)  # when the source was switched on
MS, MINUTE, HOUR, DAY = (
    timedelta(milliseconds=1),
    timedelta(minutes=1),
    timedelta(hours=1),
    timedelta(days=1),
)
LABEL = "cairn"  # the label's name, as the person made it in Gmail
LABEL_ID = "c6lbl" + "q7w"  # its id: opaque (the notes state no format for a user label's id)
SYSTEM = ("INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT", "CHAT")
LIST_KEYS = ("labels", "messages", "payload", "internalDate", "nextPageToken")
UNSET = object()

# The examples of html_to_text's contract, exactly: every source's test holds the same pairs.
HTML_PAIRS = [
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
    """base64url, with or without padding."""
    if re.search(r"[^A-Za-z0-9_\-=]", segment):
        raise ValueError(segment)
    return urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def enc(data: str | bytes, padded: bool = True) -> str:
    raw = data.encode() if isinstance(data, str) else data
    text = urlsafe_b64encode(raw).decode("ascii")
    return text if padded else text.rstrip("=")


def mid(n: int) -> str:
    """A message id: opaque, and with letters no hex digest holds."""
    return f"c6m{n:05d}x"


def tid(n: int) -> str:
    return f"c6t{n:05d}y"


def ref(n: int, part: int = 1) -> str:
    return f"gmail:{mid(n)}" + ("" if part == 1 else f"/{part}")


def refs(pulled) -> list[str]:
    return [draft.source_ref for draft in pulled.drafts]


def ms(moment: datetime) -> int:
    return round(moment.timestamp() * 1000)


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def leaf(mime: str, text: str | bytes | None = None, *, filename: str = "", padded=True) -> dict:
    """A MIME part with its body inline (or none)."""
    body: dict = {"size": 0}
    if text is not None:
        raw = text.encode() if isinstance(text, str) else text
        body = {"size": len(raw), "data": enc(raw, padded)}
    return {"mimeType": mime, "filename": filename, "headers": [], "body": body}


def attached(mime: str, filename: str, n: int = 0) -> dict:
    """An attachment held apart: an `attachmentId`, no data (attachments are not read)."""
    return {
        "mimeType": mime,
        "filename": filename,
        "headers": [{"name": "Content-Disposition", "value": f'attachment; filename="{filename}"'}],
        "body": {"size": 48_213, "attachmentId": f"c6att{n:03d}" + "ANGjdJ" * 6},
    }


def multi(mime: str, *parts: dict) -> dict:
    return {
        "mimeType": mime,
        "filename": "",
        "headers": [],
        "body": {"size": 0},
        "parts": list(parts),
    }


def plain_and_html(text: str, padded=True) -> dict:
    rich = '<div dir="ltr">' + "".join(f"<p>{escape(p)}</p>" for p in text.split("\n\n")) + "</div>"
    return multi(
        "multipart/alternative", leaf("text/plain", text, padded=padded), leaf("text/html", rich)
    )


@dataclass
class Msg:
    n: int
    at: datetime  # internalDate: when Google accepted it
    subject: str | None
    sender: str
    to: str | None
    cc: str | None
    tree: dict | None  # the payload without its headers; None: built from `body` when read
    body: str
    thread: str
    labels: set[str] = field(default_factory=set)  # label ids, besides the source's label
    label_at: datetime | None = None  # when the source's label is put on; None: never
    unlabel_at: datetime | None = None  # when it is taken off again
    gone: bool = False  # still listed, but reading it answers 404 (deleted in between)
    deleted: bool = False  # neither listed nor read
    echo: bool = False  # its body also says the access token (a bad server)

    @property
    def id(self) -> str:
        return mid(self.n)


class FakeGoogle:
    """Google's token endpoint and the Gmail API v1, as docs/google-auth.md and docs/gmail-api.md
    describe them, as one httpx mock. The token endpoint checks a service account's JWT or a
    refresh token's form and issues a token each time; Gmail accepts only the latest one, for
    `userId` "me" or the address the token was issued for. Gmail holds labels (system and user,
    nested names) and messages (id, threadId, labelIds, internalDate, headers, a MIME part tree
    with base64url bodies, attachments by filename and attachmentId), and implements
    labels.list, messages.list (labelIds, q with after: in seconds read as inclusive or as
    exclusive, maxResults, pageToken, resultSizeEstimate, ids only, Spam and Trash left out
    unless asked for) and messages.get. It can ignore q altogether, list in another order, omit
    an empty list, repeat an id on the next page, write header names in lower case, and answer
    401, 403 with each reason, 404, 429, 5xx, a timeout, an error body that is not JSON or a
    body of another shape.
    Labels and messages follow the clock. Every request is kept."""

    def __init__(self, clock, keys) -> None:
        self.clock = clock
        self.keys = keys
        self.base = GMAIL
        self.tick = timedelta(0)  # the clock moves this much with every request
        self.requests: list[SimpleNamespace] = []
        self.slept: list[float] = []
        self.issued: list[str] = []
        self.valid: tuple[str, datetime, str | None] | None = None
        self.gen = 0
        self.messages: dict[str, Msg] = {}
        self.labels: list[dict] = [
            *({"id": name, "name": name, "type": "system"} for name in SYSTEM if name != "CHAT"),
            {"id": "CATEGORY_PERSONAL", "name": "CATEGORY_PERSONAL", "type": "system"},
            {"id": "CATEGORY_UPDATES", "name": "CATEGORY_UPDATES", "type": "system"},
            {"id": LABEL_ID, "name": LABEL, "type": "user"},
            {"id": "c6lbl" + "r2d", "name": "Clients", "type": "user"},
            {"id": "c6lbl" + "s5k", "name": "Clients/Acme", "type": "user"},
        ]
        self.inclusive = True  # how `after:` is read: the notes do not say
        self.ignore_q = False  # list every message with the label, however old
        self.shuffle = False
        self.omit_empty = False
        self.repeat = False  # the last id of a page comes again on the next one
        self.lower_headers = False
        self.forced: list[tuple[str, object]] = []  # (endpoint, how): once each
        self.always: dict[str, object] = {}
        self.fail_reads_after: tuple[int, object] | None = None  # (reads that work, then how)
        self.users = {SUBJECT}

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def new_pull(self) -> None:
        self.gen += 1

    def env(self, cred: str = SA, **changes) -> dict[str, str]:
        doc = service_account(self.keys, **changes) if cred == SA else authorized_user(**changes)
        return {CRED_ENV: json.dumps(doc)}

    def message(
        self,
        n: int,
        at: datetime,
        *,
        subject: str | None = UNSET,  # type: ignore[assignment]
        body: str | None = None,
        sender: str = "Ada Lovelace <ada@example.test>",
        to: str | None = "Brook Lane <brook@example.test>",
        cc: str | None = None,
        tree: dict | None = None,
        thread: str | None = None,
        labelled: bool | datetime = True,
        labels: tuple[str, ...] = ("INBOX",),
    ) -> Msg:
        """A message that arrived at `at`. `labelled`: True (the label is on it from the start),
        False (never), or the moment it is put on."""
        if labelled is True:
            label_at = at
        elif labelled is False:
            label_at = None
        else:
            label_at = labelled
        message = Msg(
            n=n,
            at=at,
            subject=f"Item {n}: the plan" if subject is UNSET else subject,
            sender=sender,
            to=to,
            cc=cc,
            tree=tree,
            body=body if body is not None else f"Item {n} is agreed; Brook sends the figures.",
            thread=thread or tid(n),
            labels=set(labels),
            label_at=label_at,
        )
        self.messages[message.id] = message
        return message

    def label(self, message: Msg, at: datetime | None = None) -> None:
        message.label_at = at or self.clock()
        message.unlabel_at = None

    # What the tests ask about the requests

    def calls(self, endpoint: str | None = None, *, every: bool = False) -> list[SimpleNamespace]:
        return [
            r
            for r in self.requests
            if (every or r.gen == self.gen) and (endpoint is None or r.endpoint == endpoint)
        ]

    def read_ids(self, *, every: bool = False) -> list[str]:
        """The ids of the messages Gmail answered a `get` for with the message (200)."""
        return [r.message for r in self.calls("get", every=every) if r.status == 200]

    # The mailbox

    def label_ids(self, m: Msg) -> set[str]:
        now = self.clock()
        ids = set(m.labels)
        if (
            m.label_at is not None
            and m.label_at <= now
            and (m.unlabel_at is None or m.unlabel_at > now)
        ):
            ids.add(LABEL_ID)
        return ids

    def _exists(self, m: Msg) -> bool:
        return m.at <= self.clock() and not m.deleted

    def _payload(self, m: Msg, auth: str | None) -> dict:
        if m.tree is not None:
            tree = copy.deepcopy(m.tree)
        else:
            text = m.body
            if m.echo:
                text += f"\n\nThe server says {(auth or '').removeprefix('Bearer ')}."
            tree = plain_and_html(text)

        def ids(part: dict, prefix: str) -> None:
            for index, child in enumerate(part.get("parts") or []):
                child["partId"] = f"{prefix}.{index}" if prefix else str(index)
                ids(child, child["partId"])

        tree["partId"] = ""
        ids(tree, "")
        headers = [
            ("Date", m.at.strftime("%a, %d %b %Y %H:%M:%S +0000")),
            ("From", m.sender),
        ]
        if m.to is not None:
            headers.append(("To", m.to))
        if m.cc is not None:
            headers.append(("Cc", m.cc))
        if m.subject is not None:
            headers.append(("Subject", m.subject))
        headers += [
            ("Message-ID", f"<c6.{m.n}@mail.example.test>"),
            ("MIME-Version", "1.0"),
        ]
        name = str.lower if self.lower_headers else str
        tree["headers"] = [{"name": name(k), "value": v} for k, v in headers] + tree["headers"]
        return tree

    def _resource(self, m: Msg, auth: str | None) -> dict:
        return {
            "id": m.id,
            "threadId": m.thread,
            "labelIds": sorted(self.label_ids(m)),
            "snippet": m.body[:60],
            "historyId": str(40_000 + m.n),
            "internalDate": str(ms(m.at)),
            "payload": self._payload(m, auth),
            "sizeEstimate": 2_000 + len(m.body),
        }

    # Routing

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.tick:
            self.clock.now += self.tick
        url = request.url
        where = f"{url.scheme}://{url.netloc.decode()}{url.path}"
        endpoint, user, parts = self._endpoint(url, where)
        record = SimpleNamespace(
            at=self.clock(),
            gen=self.gen,
            method=request.method,
            url=str(url),
            where=where,
            endpoint=endpoint,
            user=user,
            params=dict(url.params),
            label_ids=url.params.get_list("labelIds"),
            query=list(url.params.multi_items()),
            auth=request.headers.get("authorization"),
            content_type=request.headers.get("content-type", ""),
            form=None,
            claims=None,
            problem=None,
            status=None,
            bound=None,
            message=None,
            count=None,
        )
        self.requests.append(record)
        response = self._answer(request, record, parts)
        record.status = response.status_code
        return response

    def _endpoint(self, url: httpx.URL, where: str) -> tuple[str, str | None, list[str]]:
        if url.path == "/token" and (where == TOKEN_URL or url.host in ("127.0.0.1", "localhost")):
            return "token", None, []
        if not where.startswith(self.base + "/users/"):
            return "other", None, []
        rest = [unquote(p) for p in where[len(self.base) + len("/users/") :].split("/")]
        user, parts = rest[0], rest[1:]
        shapes = {
            ("labels",): "labels",
            ("messages",): "list",
            ("messages", "*"): "get",
            ("messages", "*", "attachments", "*"): "attachment",
        }
        shape = tuple("*" if i % 2 else p for i, p in enumerate(parts))
        return shapes.get(shape, "other"), user, parts

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
            return self._error(404, "notFound", "global")
        if request.method != "GET" or request.content:
            record.problem = "every Gmail request here is a GET with an empty body"
            return self._error(400, "badRequest", "global")
        if not (
            self.valid is not None
            and record.auth == f"Bearer {self.valid[0]}"
            and self.clock() < self.valid[1]
        ):
            return self._error(401, "authError", "global")
        if record.user != "me" and not (self.valid[2] and record.user == self.valid[2]):
            record.problem = f"userId is me or the address the token is for, not {record.user}"
            return self._error(400, "invalidArgument", "global")
        if record.endpoint == "get":
            record.message = parts[1]
        how = self._forced_for(record.endpoint)
        if how is None and record.endpoint == "get" and self.fail_reads_after is not None:
            works, failing = self.fail_reads_after
            if len([r for r in self.calls("get") if r.status == 200]) >= works:
                how = failing
        if how is not None:
            return self._failure(request, how)
        if record.endpoint == "labels":
            return httpx.Response(200, json={"labels": [self._label(x) for x in self.labels]})
        if record.endpoint == "list":
            return self._list(record)
        if record.endpoint == "attachment":
            return httpx.Response(200, json={"size": 9, "data": enc(CANARY)})
        found = self.messages.get(parts[1])
        if found is None or not self._exists(found) or found.gone:
            return self._error(404, "notFound", "global")
        if record.params.get("format", "full") != "full":
            record.problem = "a message is read with format=full"
        return httpx.Response(200, json=self._resource(found, record.auth))

    @staticmethod
    def _label(x: dict) -> dict:
        shown = {
            "id": x["id"],
            "name": x["name"],
            "type": x["type"],
            "messageListVisibility": "show",
            "labelListVisibility": "labelShow",
        }
        if x["type"] == "system" and x["id"] in ("SPAM", "TRASH", "DRAFT"):
            shown.pop("messageListVisibility")
        return shown

    # messages.list

    def _page_token(self, offset: int, scope: list) -> str:
        state = json.dumps({"g": self.gen, "o": offset, "s": scope})
        return urlsafe_b64encode(state.encode()).decode()

    def _offset(self, token: str | None, scope: list) -> int | None:
        if token is None:
            return 0
        try:
            state = json.loads(urlsafe_b64decode(token.encode()))
        except ValueError:
            return None
        if state.get("g") != self.gen or state.get("s") != scope:
            return None  # from another pull, or the other parameters changed
        return state["o"]

    def _list(self, record) -> httpx.Response:
        params = record.params
        q = params.get("q")
        lower_ms = None
        if q is not None:
            match = re.fullmatch(r"\s*after:(\d+)\s*", q)
            if match is None:
                record.problem = f"q is after:<seconds>, not {q!r}"
                return self._error(400, "invalidArgument", "global")
            record.bound = datetime.fromtimestamp(int(match.group(1)), UTC)
            seconds = int(match.group(1))
            lower_ms = seconds * 1000 if self.inclusive else (seconds + 1) * 1000
            if self.ignore_q:
                lower_ms = None
        try:
            size = int(params.get("maxResults", "100"))
        except ValueError:
            return self._error(400, "invalidArgument", "global")
        if not 1 <= size <= 500:
            return self._error(400, "invalidArgument", "global")
        spam_trash = params.get("includeSpamTrash", "false").lower() == "true"
        found = []
        for m in self.messages.values():
            ids = self.label_ids(m)
            if not self._exists(m) or not set(record.label_ids) <= ids:
                continue
            if not spam_trash and ids & {"SPAM", "TRASH"}:
                continue
            if lower_ms is not None and ms(m.at) < lower_ms:
                continue
            found.append(m)
        found.sort(key=lambda m: (m.at, m.id), reverse=True)  # the guide: newest first
        if self.shuffle:
            found.sort(key=lambda m: hashlib.sha256(f"{self.gen}:{m.id}".encode()).hexdigest())
        scope = sorted(
            (k, v) for k, v in record.query if k != "pageToken"
        )  # every parameter but the page token
        scope = [list(item) for item in scope]
        offset = self._offset(params.get("pageToken"), scope)
        if offset is None:
            record.problem = "a page token is used with the parameters it was given for"
            return self._error(400, "invalidArgument", "global")
        start = offset - 1 if self.repeat and offset else offset
        page = found[start : offset + size]
        record.count = len(page)
        body: dict = {"resultSizeEstimate": len(found) + 7}  # an estimate, and not a good one
        if page or not self.omit_empty:
            body["messages"] = [{"id": m.id, "threadId": m.thread} for m in page]
        if offset + size < len(found):
            body["nextPageToken"] = self._page_token(offset + size, scope)
        return httpx.Response(200, json=body)

    # The token endpoint

    def _token(self, request: httpx.Request, record) -> httpx.Response:
        how = self._forced_for("token")
        if how is not None:
            return self._failure(request, how)
        try:
            text = request.content.decode("ascii")
            record.form = dict(parse_qsl(text, keep_blank_values=True, strict_parsing=True))
        except (UnicodeDecodeError, ValueError):
            record.form = {}
        problem, subject = self._token_problem(request, record)
        if problem:
            record.problem = problem
            body = {"error": "invalid_request", "error_description": f"{CANARY}: {problem}"}
            return httpx.Response(400, json=body)
        token = "ya29." + f"c6{len(self.issued) + 1:04d}" + "MadeUp" + "AccessToken"
        self.issued.append(token)
        self.valid = (token, self.clock() + HOUR, subject)
        body = {"access_token": token, "expires_in": 3599, "scope": SCOPE, "token_type": "Bearer"}
        return httpx.Response(200, json=body)

    def _token_problem(self, request: httpx.Request, record) -> tuple[str | None, str | None]:
        if request.method != "POST" or request.url.query:
            return "a token request is a POST without a query string", None
        if not record.content_type.startswith("application/x-www-form-urlencoded"):
            return "a token request is a form", None
        form = record.form
        if form.get("grant_type") == JWT_GRANT:
            if set(form) != {"grant_type", "assertion"}:
                return "a JWT grant has grant_type and assertion", None
            segments = form["assertion"].split(".")
            if len(segments) != 3:
                return "the assertion is not header.claims.signature", None
            try:
                claims = json.loads(unb64(segments[1]))
                signature = unb64(segments[2])
            except ValueError:
                return "a segment of the assertion is not base64url JSON", None
            record.claims = claims
            signed = f"{segments[0]}.{segments[1]}".encode("ascii")
            try:
                self.keys.public.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
            except InvalidSignature:
                return "the signature does not verify", None
            for claim, wanted in (("iss", CLIENT_EMAIL), ("scope", SCOPE), ("aud", TOKEN_URL)):
                if claims.get(claim) != wanted:
                    return f"{claim} is {wanted}", None
            if claims.get("sub") not in self.users:
                return "sub is a person of the domain", None
            return None, claims["sub"]
        if form.get("grant_type") == "refresh_token":
            if record.where != TOKEN_URL:
                return "a refresh token goes to Google's token endpoint", None
            given = (form.get("client_id"), form.get("client_secret"), form.get("refresh_token"))
            if given != (USER_CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN) or len(form) != 4:
                return "a refresh has its four fields, as issued", None
            return None, None
        return "grant_type", None

    # Answers

    def _error(self, status: int, reason: str, domain: str) -> httpx.Response:
        """The error shape of the notes (G-ERRORS), as valid JSON."""
        item = {"domain": domain, "reason": reason, "message": f"{CANARY}: {reason}"}
        body = {"error": {"errors": [item], "code": status, "message": f"{CANARY}: {reason}"}}
        return httpx.Response(status, json=body)

    def _failure(self, request: httpx.Request, how: object) -> httpx.Response:
        if how == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if how == "not json":
            return httpx.Response(200, text=f"<html>{CANARY}</html>")
        if how == "array":
            return httpx.Response(200, json=[CANARY])
        if how == "wrong shape":
            return httpx.Response(200, json=dict.fromkeys(LIST_KEYS, CANARY))
        if how == "no access_token":
            return httpx.Response(200, json={"token_type": "Bearer", "note": CANARY})
        status, _, reason = str(how).partition(" ")
        code = int(status)
        if reason == "not json":  # Google's own samples are not valid JSON as printed
            return httpx.Response(code, text='{"error": {"message": "' + CANARY + '",}}')
        if reason == "no body":
            return httpx.Response(code)
        if reason:
            usage = reason in ("rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded")
            return self._error(code, reason, "usageLimits" if usage else "global")
        default = {401: "authError", 403: "domainPolicy", 404: "notFound", 429: "rateLimitExceeded"}
        return self._error(code, default.get(code, "backendError"), "global")


def service_account(keys, **changes) -> dict:
    doc = {
        "type": "service_account",
        "project_id": "c6-project",
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


def broken_key() -> str:
    label = "PRIVATE" + " KEY"  # built from parts, so no scanner takes this for a key
    return f"-----BEGIN {label}-----\n" + NOT_A_KEY * 8 + f"\n-----END {label}-----\n"


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
    return KINDS["gmail"]


@pytest.fixture
def fake(kind, keys):
    return FakeGoogle(Clock(NOW), keys)


def leaked(text: str | None, fake: FakeGoogle) -> list[str]:
    """The secrets `text` holds, as written or percent-decoded."""
    if not text:
        return []
    secrets = [*fake.keys.body, CLIENT_SECRET, REFRESH_TOKEN, *fake.issued]
    return [s for s in secrets if s in text or s in unquote(text)]


def config(cred: str = SA, **settings) -> dict:
    given: dict = {"credentials_env": CRED_ENV, "label": LABEL, "projects": ["mail"]}
    if cred == SA:
        given["subject"] = SUBJECT
    given.update(settings)
    return {key: value for key, value in given.items() if value is not None}


def opened(kind, fake, *, cred=SA, env=None, since=SINCE, deadline=None, **settings):
    checked = kind.check(config(cred, **settings))
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return kind.open(
        "mail", checked, http=http, env=fake.env(cred) if env is None else env,
        clock=fake.clock, sleep=fake.sleep, deadline=deadline, since=since,
    )  # fmt: skip


def pull(fake, source, cursor=None):
    fake.new_pull()
    try:
        got = asyncio.run(source.pull(cursor))
    except SourceError as error:
        problems = [r.problem for r in fake.calls() if r.problem]
        if problems:
            raise AssertionError(f"the stand-in refused a request: {problems[-1]}") from error
        raise
    problems = [r.problem for r in fake.calls() if r.problem]
    assert not problems, problems
    return got


def reader(kind, fake, **settings):
    """Each pull through a source opened afresh, as the runner opens one for every run."""

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


def oldest_first(fake, pulled) -> None:
    """A pull's drafts come by internalDate, then id (parts of one message together)."""
    keys = []
    for draft in pulled.drafts:
        m = fake.messages[draft.source_ref.removeprefix("gmail:").split("/")[0]]
        keys.append((m.at, m.id))
    assert keys == sorted(keys), [d.source_ref for d in pulled.drafts]


def only(pulled):
    (draft,) = pulled.drafts
    return draft


def facts_of(draft, body: str) -> str:
    """The draft's text before its body: the heading and the facts."""
    assert body in draft.text, (body, draft.text)
    return draft.text.split(body, 1)[0]


# The kind and its config


def test_the_kind_starts_off_proposes_mail_and_its_config_has_these_defaults(kind):
    assert kind.starts_off is True and kind.via == "gmail"
    assert kind.default_project == "mail"
    said = kind.switching_on()
    assert isinstance(said, str) and said == kind.switch_on and "label" in said.lower()

    checked = kind.check({"credentials_env": CRED_ENV, "label": LABEL, "projects": []})
    assert (checked["credentials_env"], checked["label"], checked["projects"]) == (
        CRED_ENV,
        LABEL,
        [],
    )
    assert (checked["base_url"], checked["lookback_days"]) == (GMAIL, 30)
    assert checked.get("subject") is None
    assert kind.check(checked) == checked  # what is stored is checked again on switch-on

    given = {
        "credentials_env": CRED_ENV,
        "subject": SUBJECT,
        "label": "Clients/Acme",
        "lookback_days": 365,
        "projects": ["mail"],
        "base_url": "https://gmail.example.test/gmail/v1",
    }
    kept = kind.check(given)
    for setting, value in given.items():
        assert kept[setting] == value, setting
    assert kind.check(kept) == kept
    for accepted in (
        {"lookback_days": 1},
        {"label": "x"},
        {"label": "L" * 200},
        {"label": "Inbox zero"},
        {"label": "Categories"},
        {"label": "Ünïcode label · 2026"},
        {"base_url": "http://127.0.0.1:8786/gmail/v1"},
        {"base_url": "http://localhost:8786/gmail/v1"},
    ):
        assert kind.check(config(**accepted))["projects"] == ["mail"], accepted


def test_describe_says_whose_mail_and_which_label_in_one_sentence(kind):
    said = kind.describe(kind.check(config(label="Clients/Acme")))
    assert isinstance(said, str) and "\n" not in said.strip()
    assert "Clients/Acme" in said and SUBJECT in said
    theirs = kind.describe(kind.check(config(USER)))
    assert LABEL in theirs and "\n" not in theirs.strip()
    for value in (CLIENT_SECRET, REFRESH_TOKEN, CLIENT_EMAIL):
        assert value not in said and value not in theirs


@pytest.mark.parametrize(
    ("setting", "settings"),
    [
        ("credentials_env", {"credentials_env": None}),
        ("credentials_env", {"credentials_env": 7}),
        ("credentials_env", {"credentials_env": ""}),
        ("subject", {"subject": "ada"}),
        ("subject", {"subject": "ada@example.test, brook@example.test"}),
        ("subject", {"subject": ["ada@example.test"]}),
        ("label", {"label": None}),
        ("label", {"label": ""}),
        ("label", {"label": "L" * 201}),
        ("label", {"label": "cairn\x07"}),
        ("label", {"label": "cairn\nnext"}),
        ("label", {"label": ["cairn"]}),
        ("label", {"label": 7}),
        ("lookback_days", {"lookback_days": 0}),
        ("lookback_days", {"lookback_days": 366}),
        ("lookback_days", {"lookback_days": "30"}),
        ("lookback_days", {"lookback_days": True}),
        ("lookback_days", {"lookback_days": 7.5}),
        ("projects", {"projects": ["mail", "planning"]}),
        ("projects", {"projects": "mail"}),
        ("base_url", {"base_url": "http://gmail.example.test/gmail/v1"}),
        ("base_url", {"base_url": "ftp://gmail.example.test/gmail/v1"}),
        ("base_url", {"base_url": 7}),
        ("query", {"query": "in:anywhere"}),
        ("all_mail", {"all_mail": True}),
        ("scope", {"scope": "https://mail.google.com/"}),
    ],
)
def test_check_refuses_a_bad_setting_by_its_name(kind, setting, settings):
    given = config(**settings)  # a setting given as None is left out
    with pytest.raises(ValueError) as refused:
        kind.check(given)
    assert setting in str(refused.value)


@pytest.mark.parametrize(
    "name",
    [
        *SYSTEM,
        *(name.lower() for name in SYSTEM),
        "Inbox",
        "Spam",
        "CATEGORY_PERSONAL",
        "category_social",
        "Category_Promotions",
        "CATEGORY_FORUMS",
        "CATEGORY_",
    ],
)
def test_check_refuses_gmails_own_labels_whatever_their_case(kind, name):
    """The source can never be pointed at a whole mailbox."""
    with pytest.raises(ValueError) as refused:
        kind.check(config(label=name))
    assert "label" in str(refused.value)


def test_check_refuses_a_config_that_is_not_an_object(kind):
    for odd in (None, [], "label"):
        with pytest.raises(ValueError):
            kind.check(odd)


def test_a_credential_pasted_into_the_config_is_refused_and_never_repeated(kind, fake, tmp_path):
    document = json.dumps(service_account(fake.keys))
    token = "ya29." + "c6" + "PastedAccessToken" + "0a1b2c3d"
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
        store.create_project("mail")
        with pytest.raises(ValueError):
            store.add_source("mail", "gmail", config(credentials_env=document))
        assert store.list_sources() == []
    finally:
        store.close()
    assert not leaked(dump(tmp_path / "cairn.db"), fake)


# Opening and signing in


def test_subject_is_required_with_a_service_account_and_refused_with_a_refresh_token(kind, fake):
    for cred, settings in ((SA, {"subject": None}), (USER, {"subject": SUBJECT})):
        checked = kind.check(config(cred, **settings))
        http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
        with pytest.raises(SourceError) as refused:
            kind.open(
                "mail", checked, http=http, env=fake.env(cred), clock=fake.clock,
                sleep=fake.sleep, deadline=None, since=SINCE,
            )  # fmt: skip
        said = str(refused.value)
        assert "subject" in said, cred
        assert not leaked(said, fake) and CLIENT_EMAIL not in said
    assert fake.requests == []


@pytest.mark.parametrize("cred", [SA, USER])
def test_one_token_a_pull_asked_for_the_read_only_scope_and_kept_out_of_everything(
    kind, fake, cred, caplog
):
    caplog.set_level(logging.DEBUG)
    for n in (1, 2, 3):
        fake.message(n, SINCE + n * HOUR)
    source = opened(kind, fake, cred=cred)
    kept = tuple(source.secrets)
    if cred == SA:
        assert holds_secret(fake.keys.pem, kept)
    else:
        assert holds_secret(f"said {CLIENT_SECRET} once", kept)
        assert holds_secret(f"said {REFRESH_TOKEN} once", kept)
    got = pull(fake, source)
    assert refs(got) == [ref(1), ref(2), ref(3)]

    (asked,) = fake.calls("token")
    assert (asked.method, asked.url, asked.problem) == ("POST", TOKEN_URL, None)
    if cred == SA:
        assert asked.claims["scope"] == SCOPE and asked.claims["sub"] == SUBJECT
    else:
        assert set(asked.form) == {"client_id", "client_secret", "grant_type", "refresh_token"}
    (token,) = fake.issued
    assert holds_secret(f"the token {token} again", tuple(source.secrets))
    gmail = [r for r in fake.calls() if r.endpoint != "token"]
    assert gmail and {r.auth for r in gmail} == {f"Bearer {token}"}
    assert all(r.url.startswith(GMAIL + "/users/") and r.method == "GET" for r in gmail)
    assert {r.endpoint for r in gmail} == {"labels", "list", "get"}  # no thread, history, ...
    for request in fake.requests:
        assert not leaked(request.url, fake)
        assert not {"access_token", "key", "token"} & set(request.params)
    assert not leaked(caplog.text, fake) and not leaked(got.cursor, fake)
    for draft in got.drafts:
        assert not leaked(json.dumps(vars(draft)), fake)

    fake.clock.now += HOUR
    pull(fake, opened(kind, fake, cred=cred), got.cursor)
    assert len(fake.calls("token")) == 1 and len(fake.issued) == 2  # the next pull: its own


def test_gmail_is_read_at_the_base_url_given_on_this_machine(kind, fake):
    fake.base = "http://127.0.0.1:8786/gmail/v1"
    fake.message(1, SINCE + HOUR)
    got = pull(fake, opened(kind, fake, base_url=fake.base))
    assert refs(got) == [ref(1)]
    gmail = [r for r in fake.calls() if r.endpoint != "token"]
    assert gmail and all(r.url.startswith(fake.base + "/") for r in gmail)
    assert not any(r.endpoint == "other" for r in fake.requests)


# Resolving the label


def listed_with(fake) -> set[str]:
    (label_ids,) = {tuple(r.label_ids) for r in fake.calls("list")}
    return set(label_ids)


def test_the_label_is_found_by_its_exact_name_on_every_pull(kind, fake):
    fake.labels.append({"id": "c6lbl" + "t8m", "name": "Cairn", "type": "user"})  # a case-twin
    fake.message(1, SINCE + HOUR)
    read = reader(kind, fake)
    first = read()
    assert refs(first) == [ref(1)]
    assert len(fake.calls("labels")) == 1 and listed_with(fake) == {LABEL_ID}

    # The label is deleted and made again: a new id, the same name. The next pull finds it.
    renewed = "c6lbl" + "u3n"
    for x in fake.labels:
        if x["id"] == LABEL_ID:
            x["id"] = renewed
    fake.message(2, SINCE + 2 * HOUR, labels=("INBOX", renewed), labelled=False)
    fake.clock.now += HOUR
    second = read(first.cursor)
    assert refs(second) == [ref(2)]
    assert len(fake.calls("labels")) == 1 and listed_with(fake) == {renewed}


def test_a_label_is_found_ignoring_case_when_only_one_matches(kind, fake):
    fake.message(1, SINCE + HOUR)
    got = pull(fake, opened(kind, fake, label="CAIRN"))
    assert refs(got) == [ref(1)] and listed_with(fake) == {LABEL_ID}


@pytest.mark.parametrize("case", ["two differ only in case", "none"])
def test_a_label_not_found_or_ambiguous_fails_naming_the_setting(kind, fake, case):
    if case == "two differ only in case":
        fake.labels.append({"id": "c6lbl" + "t8m", "name": "Cairn", "type": "user"})
        name = "CAIRN"
    else:
        name = "Receipts"
    fake.message(1, SINCE + HOUR)
    said = str(failed_pull(fake, opened(kind, fake, label=name)))
    assert "label" in said and "gmail" in said.lower()
    assert CANARY not in said and not leaked(said, fake)
    assert fake.calls("list") == [] and fake.calls("get") == []


def test_a_nested_label_is_named_with_its_parents(kind, fake):
    nested = "c6lbl" + "s5k"
    fake.message(1, SINCE + HOUR, labels=("INBOX", nested), labelled=False)
    fake.message(2, SINCE + 2 * HOUR, labels=("INBOX", "c6lbl" + "r2d"), labelled=False)
    got = pull(fake, opened(kind, fake, label="Clients/Acme"))
    assert refs(got) == [ref(1)] and listed_with(fake) == {nested}


def test_a_name_that_resolves_to_one_of_gmails_own_labels_fails_naming_the_setting(kind, fake):
    """The notes say Gmail has reserved labels besides those `check` refuses (none is named:
    this one is made up)."""
    fake.labels.append({"id": "SCHEDULED", "name": "SCHEDULED", "type": "system"})
    fake.message(1, SINCE + HOUR, labels=("INBOX", "SCHEDULED"), labelled=False)
    for name in ("SCHEDULED", "Scheduled"):
        said = str(failed_pull(fake, opened(kind, fake, label=name)))
        assert "label" in said and CANARY not in said
        assert fake.calls("list") == [] and fake.calls("get") == []


# What it reads


@pytest.mark.parametrize("after", ["inclusive", "exclusive"])
@pytest.mark.parametrize("empty", ["[]", "absent"])
def test_the_first_pull_writes_what_arrived_since_and_a_label_put_on_later(
    kind, fake, after, empty
):
    fake.inclusive = after == "inclusive"
    fake.omit_empty = empty == "absent"
    fake.message(1, SINCE - 12 * HOUR, labelled=SINCE - 6 * HOUR)  # before switch-on, both
    two = fake.message(2, SINCE - 12 * HOUR, labelled=False)  # arrived before, labelled after
    three = fake.message(3, SINCE - 3 * DAY, labelled=False)
    fake.message(4, SINCE)  # right as it was switched on
    fake.message(5, SINCE - MS)
    fake.message(6, SINCE + 2 * HOUR)
    seven = fake.message(7, SINCE + HOUR, labelled=False)  # labelled after the first pull
    fake.message(8, SINCE + 3 * HOUR, labels=("TRASH",))
    fake.message(9, SINCE + 4 * HOUR, labels=("SPAM",))
    fake.message(10, SINCE + 5 * HOUR, labels=("DRAFT",))
    fake.message(11, SINCE + 6 * HOUR, labelled=False)  # never labelled
    read = reader(kind, fake)

    first = read()
    assert refs(first) == [ref(4), ref(6)]
    assert {draft.project for draft in first.drafts} == {"mail"}
    assert {draft.label for draft in first.drafts} == {"gmail"}
    silent = {mid(n) for n in (8, 9, 10, 11)}
    assert not any(skip.ref in silent or mid(10) in skip.reason for skip in first.skipped)
    (bound,) = {r.bound for r in fake.calls("list")}  # every page: the same query
    assert abs(bound - (SINCE - DAY)) <= HOUR  # a day before `since`, the later of the two

    fake.clock.now = NOW + HOUR
    for message in (two, three, seven):
        fake.label(message)
    second = read(first.cursor)
    assert refs(second) == [ref(7)]
    assert not any(skip.ref in silent for skip in second.skipped)

    fake.clock.now = NOW + 2 * HOUR
    third = read(second.cursor)
    assert third.drafts == [] and third.skipped == []
    assert fake.calls("get") == []  # every message it listed it had handled


def test_a_message_labelled_after_lookback_days_is_not_written(kind, fake):
    late = fake.message(1, SINCE + HOUR, labelled=False)
    fake.message(2, SINCE + 2 * HOUR, labelled=False)
    read = reader(kind, fake, lookback_days=30)
    first = read()
    assert first.drafts == []

    fake.clock.now = SINCE + 2 * HOUR + 29 * DAY  # the second: labelled 29 days after it came
    fake.label(fake.messages[mid(2)])
    second = read(first.cursor)
    assert refs(second) == [ref(2)]
    (bound,) = {r.bound for r in fake.calls("list")}
    assert abs(bound - (SINCE - DAY)) <= HOUR  # since is still the later of the two

    fake.clock.now = SINCE + HOUR + 33 * DAY  # the first: labelled 33 days after it came
    fake.label(late)
    third = read(second.cursor)
    assert third.drafts == []
    (bound,) = {r.bound for r in fake.calls("list")}
    assert abs(bound - (fake.clock.now - 31 * DAY)) <= HOUR  # now the look-back is the later

    # Labelled 18 hours after its 30 days: still inside the listing's day of margin, which
    # covers only how after: is read, so it is not written either.
    margin = fake.message(3, fake.clock.now - 30 * DAY - 18 * HOUR, labelled=False)
    fake.clock.now += MINUTE
    fake.label(margin)
    fourth = read(third.cursor)
    assert fourth.drafts == []


@pytest.mark.parametrize("q", ["honoured", "ignored"])
def test_a_listing_that_ignores_q_gives_the_same_drafts_over_more_pulls(kind, fake, q):
    """Google's pages do not say that q narrows a listing by labelIds: the connector draws the
    lines of `since` and the look-back itself, from each message's internalDate."""
    fake.ignore_q = q == "ignored"
    fake.clock.now = SINCE + 40 * DAY
    for n in range(1, 31):  # arrived before `since`
        fake.message(n, SINCE - n * DAY - n * MINUTE)
    for n in range(31, 61):  # after `since`, but more than 30 days before the pull
        fake.message(n, SINCE + HOUR + (n - 31) * 6 * HOUR)
    for n in range(61, 101):  # inside the look-back
        fake.message(n, fake.clock.now - 29 * DAY + (n - 61) * 12 * HOUR)
    read = reader(kind, fake, lookback_days=30)
    written: list[str] = []
    cursor = None
    for _ in range(8):
        got = read(cursor)
        written += refs(got)
        cursor = got.cursor
        if not fake.calls("get"):
            break
        fake.clock.now += MINUTE
    else:
        raise AssertionError("the pulls never stopped asking for messages")
    assert sorted(written) == [ref(n) for n in range(61, 101)]  # each once
    assert Counter(fake.read_ids(every=True)).most_common(1)[0][1] == 1  # none asked for twice


def test_a_reply_arriving_later_in_a_labelled_conversation_waits_for_its_own_label(kind, fake):
    thread = tid(1)
    fake.message(1, SINCE + HOUR, subject="Budget review", thread=thread)
    fake.message(2, SINCE + 2 * HOUR, subject="Re: Budget review", thread=thread)
    reply = fake.message(3, NOW + HOUR, subject="Re: Budget review", thread=thread, labelled=False)
    read = reader(kind, fake)
    first = read()
    assert refs(first) == [ref(1), ref(2)]
    assert {d.topic for d in first.drafts} == {"Budget review, 2026-11"}  # one page

    fake.clock.now = NOW + 2 * HOUR
    second = read(first.cursor)
    assert second.drafts == []
    fake.clock.now = NOW + 3 * HOUR
    fake.label(reply)
    third = read(second.cursor)
    assert refs(third) == [ref(3)] and only(third).topic == "Budget review, 2026-11"
    assert not any(r.endpoint == "other" for r in fake.requests)


def test_the_requests_ask_for_the_label_a_bound_in_seconds_and_full_messages(kind, fake):
    for n in range(1, 5):
        fake.message(n, SINCE + n * HOUR)
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(n) for n in range(1, 5)]
    (listed,) = fake.calls("list")
    assert listed.label_ids == [LABEL_ID]
    assert re.fullmatch(r"\s*after:\d+\s*", listed.params["q"])
    assert listed.params["maxResults"] == "500"
    assert "includeSpamTrash" not in listed.params
    reads = fake.calls("get")
    assert sorted(r.message for r in reads) == [mid(n) for n in range(1, 5)]
    assert all(r.params.get("format") == "full" for r in reads)
    assert fake.calls("attachment", every=True) == []


@pytest.mark.parametrize("after", ["inclusive", "exclusive"])
def test_a_message_that_arrived_right_at_the_bound_is_found_whichever_way_after_reads(
    kind, fake, after
):
    """The day before the bound covers what the notes do not say: whether after: is
    inclusive. A message that arrived in the first second after `since` is written."""
    fake.inclusive = after == "inclusive"
    fake.message(1, SINCE)
    fake.message(2, SINCE + timedelta(milliseconds=999))
    fake.message(3, SINCE + timedelta(seconds=1))
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(1), ref(2), ref(3)]


@pytest.mark.parametrize("repeat", [False, True], ids=["pages apart", "an id on two pages"])
def test_1200_listed_ids_come_over_three_pages_asked_with_the_same_parameters(kind, fake, repeat):
    fake.repeat = repeat  # the notes do not say whether an id can come on two pages
    for n in range(1, 1201):
        fake.message(n, SINCE + n * MINUTE)
    read = reader(kind, fake)
    first = read()
    pages = fake.calls("list")
    counts = [500, 501, 201] if repeat else [500, 500, 200]
    assert len(pages) == 3 and [p.count for p in pages] == counts
    asked = [sorted((k, v) for k, v in p.query if k != "pageToken") for p in pages]
    assert asked[0] == asked[1] == asked[2]
    assert "pageToken" not in pages[0].params and all("pageToken" in p.params for p in pages[1:])
    assert 0 < len(first.drafts) <= 50 and len(fake.calls("get")) <= 50
    oldest_first(fake, first)

    written, cursor = Counter(refs(first)), first.cursor
    for _ in range(30):
        if sum(written.values()) == 1200:
            break
        fake.clock.now += 10 * MINUTE
        got = read(cursor)
        assert len(fake.calls("get")) <= 50
        written.update(refs(got))
        cursor = got.cursor
    assert written == Counter({ref(n): 1 for n in range(1, 1201)})


@pytest.mark.parametrize("order", ["newest first", "shuffled"])
def test_drafts_come_oldest_first_whatever_order_gmail_lists_them_in(kind, fake, order):
    fake.shuffle = order == "shuffled"
    moments = {n: SINCE + (n * 5 % 7) * HOUR for n in range(1, 41)}  # some share an internalDate
    for n, at in moments.items():
        fake.message(n, at)
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(n) for n in sorted(moments, key=lambda n: (moments[n], mid(n)))]


def test_120_new_messages_are_written_over_three_pulls_of_at_most_50_reads(kind, fake):
    for n in range(1, 121):
        fake.message(n, SINCE + n * MINUTE)
    read = reader(kind, fake)
    written, cursor, sizes = Counter(), None, []
    for _ in range(3):
        fake.clock.now += MINUTE
        got = read(cursor)
        assert len(fake.calls("get")) <= 50
        assert sorted(fake.read_ids()) == sorted(
            d.source_ref.removeprefix("gmail:") for d in got.drafts
        )
        oldest_first(fake, got)
        written.update(refs(got))
        sizes.append(len(got.drafts))
        cursor = got.cursor
    assert written == Counter({ref(n): 1 for n in range(1, 121)}), sizes
    fake.clock.now += MINUTE
    assert read(cursor).drafts == [] and fake.calls("get") == []


# The draft


@pytest.mark.parametrize(
    ("subject", "title"),
    [
        ("Budget review", "Budget review"),
        ("=?UTF-8?B?" + enc("Revisión del presupuesto") + "?=", "Revisión del presupuesto"),
        ("=?ISO-8859-1?Q?Caf=E9_plans?=", "Café plans"),
        ("Re: =?UTF-8?Q?Caf=C3=A9?= meeting", "Re: Café meeting"),
        ("=?UTF-8?Q?Caf=C3=A9?= =?UTF-8?Q?_plans?=", "Café plans"),  # two words, one space
        ("Café plans", "Café plans"),  # the notes do not say whether Gmail decodes first
        (None, "(no subject)"),
        ("", "(no subject)"),
    ],
    ids=["plain", "base64", "latin-1", "mixed", "adjacent", "decoded", "none", "empty"],
)
def test_the_title_is_the_subject_decoded(kind, fake, subject, title):
    fake.message(1, SINCE + HOUR, subject=subject)
    draft = only(pull(fake, opened(kind, fake)))
    assert draft.title == title
    assert draft.text.splitlines()[0] == f"# {title}"
    assert (draft.source_ref, draft.label, draft.project) == (ref(1), "gmail", "mail")


@pytest.mark.parametrize(
    ("subject", "topic"),
    [
        ("Budget review", "Budget review"),
        ("Re: Budget review", "Budget review"),
        ("RE: Budget review", "Budget review"),
        ("Fwd: Budget review", "Budget review"),
        ("FW: Budget review", "Budget review"),
        ("Fw: Budget review", "Budget review"),
        ("AW: Budget review", "Budget review"),
        ("WG: Budget review", "Budget review"),
        ("re: fwd: RE: Budget review", "Budget review"),
        ("Fwd: AW: wg: fW: Budget review", "Budget review"),
        ("FWD: rE: Budget review", "Budget review"),
        ("Re: Re:", "(no subject)"),
        (None, "(no subject)"),
    ],
)
def test_the_topic_drops_reply_and_forward_prefixes_and_names_the_month(kind, fake, subject, topic):
    fake.message(1, SINCE + HOUR, subject=subject)
    draft = only(pull(fake, opened(kind, fake)))
    assert draft.topic == f"{topic}, 2026-11"
    assert draft.title == (subject or "(no subject)")  # the title keeps them


def test_the_month_is_the_utc_month_it_arrived_in(kind, fake):
    since = datetime(2026, 10, 30, tzinfo=UTC)
    fake.clock.now = datetime(2026, 11, 2, tzinfo=UTC)
    fake.message(1, datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC), subject="Month end")
    fake.message(2, datetime(2026, 11, 1, tzinfo=UTC), subject="Re: Month end")
    got = pull(fake, opened(kind, fake, since=since))
    assert [d.topic for d in got.drafts] == ["Month end, 2026-10", "Month end, 2026-11"]


@pytest.mark.parametrize("headers", ["Title-Case", "lower-case"])
def test_the_facts_name_the_people_and_attachments_never_an_address(kind, fake, headers):
    fake.lower_headers = headers == "lower-case"  # the notes do not say how names are cased
    body = "Friday works; I will bring the figures."
    tree = multi(
        "multipart/mixed",
        plain_and_html(body),
        attached("application/pdf", "Budget 2027.pdf", 1),
        attached("image/png", "chart.png", 2),
    )
    fake.message(
        1,
        datetime(2026, 11, 12, 14, 7, 41, tzinfo=UTC),
        subject="Budget review",
        sender='"Lovelace, Ada" <ada@example.test>',
        to='Brook Lane <brook@example.test>, cy@example.test, "dee@example.test" '
        "<dee@example.test>",
        cc="Eve Stone <eve@partner.example.test>",
        tree=tree,
    )
    draft = only(pull(fake, opened(kind, fake)))
    facts = facts_of(draft, body)
    assert "2026-11-12 14:07" in facts
    for name in ("Lovelace, Ada", "Brook Lane", "Eve Stone", "Budget 2027.pdf", "chart.png"):
        assert name in facts, name
    assert "example.test" in facts  # Cy and Dee, whom the header gives no name
    assert "@" not in facts and "ada@" not in draft.text and "dee@" not in draft.text
    assert fake.calls("attachment", every=True) == []  # attachments are not read


def test_more_than_ten_recipients_are_counted(kind, fake):
    people = [f"Person {chr(ord('A') + k)}{chr(ord('a') + k)}" for k in range(24)]
    to = ", ".join(f"{p} <p{k}@example.test>" for k, p in enumerate(people))
    body = "Notes for everyone."
    fake.message(1, SINCE + HOUR, to=to, body=body)
    facts = facts_of(only(pull(fake, opened(kind, fake))), body)
    assert sum(p in facts for p in people) == 10
    assert "and 14 more" in facts and "@" not in facts


PLAIN = "First paragraph of the reply.\n\nSecond paragraph, with a list:\n\n- one\n- two"


def bodies() -> dict[str, tuple[dict, str]]:
    """Each part tree, and the text its draft holds. The notes show no tree for a message with
    both a text and an HTML part, so several are tried."""
    rich = HTML_PAIRS[4]
    note = leaf("text/plain", "ATTACHED NOTES, not the body", filename="notes.txt")
    return {
        "a single text part": (leaf("text/plain", PLAIN), PLAIN),
        "text and HTML": (plain_and_html(PLAIN), PLAIN),
        "HTML only": (leaf("text/html", rich[0]), rich[1]),
        "nested": (
            multi(
                "multipart/related",
                multi(
                    "multipart/mixed",
                    note,
                    multi(
                        "multipart/alternative",
                        leaf("text/plain", PLAIN),
                        leaf("text/html", "<p>HTML twin</p>"),
                    ),
                    attached("application/pdf", "minutes.pdf"),
                ),
                attached("image/png", "logo.png", 3),
            ),
            PLAIN,
        ),
        "nested, HTML only": (
            multi(
                "multipart/mixed",
                note,
                multi(
                    "multipart/related", leaf("text/html", rich[0]), attached("image/png", "a.png")
                ),
            ),
            rich[1],
        ),
    }


@pytest.mark.parametrize("case", list(bodies()))
def test_the_body_is_the_first_text_part_that_is_not_an_attachment(kind, fake, case):
    tree, text = bodies()[case]
    fake.message(1, SINCE + HOUR, tree=tree)
    draft = only(pull(fake, opened(kind, fake)))
    assert text in draft.text
    assert "ATTACHED NOTES" not in draft.text and "HTML twin" not in draft.text


@pytest.mark.parametrize("padded", [True, False], ids=["padded", "unpadded"])
def test_body_data_is_base64url_with_or_without_padding_and_bad_bytes_are_replaced(
    kind, fake, padded
):
    text = "Ratio ~>>? and ~~? hold; see the plans.\n"
    assert ("-" in enc(text) or "_" in enc(text)) and enc(text).endswith("=")
    fake.message(1, SINCE + HOUR, tree=leaf("text/plain", text, padded=padded))
    fake.message(
        2, SINCE + 2 * HOUR, tree=leaf("text/plain", b"Caf\xe9 au lait at ten.", padded=padded)
    )
    first, second = pull(fake, opened(kind, fake)).drafts
    assert text.strip() in first.text
    assert "Caf� au lait at ten." in second.text


def test_a_message_with_no_text_part_is_skipped_once(kind, fake):
    fake.message(1, SINCE + HOUR, tree=multi("multipart/mixed", attached("image/png", "scan.png")))
    fake.message(2, SINCE + 2 * HOUR)
    read = reader(kind, fake)
    first = read()
    assert refs(first) == [ref(2)]
    (skip,) = first.skipped
    assert mid(1) in skip.ref
    assert "no text to read" in skip.reason.lower()
    fake.clock.now += HOUR
    again = read(first.cursor)
    assert again.drafts == [] and again.skipped == [] and fake.calls("get") == []


@pytest.mark.parametrize(("html", "text"), HTML_PAIRS)
def test_html_to_text_turns_each_example_into_its_text(html, text):
    assert markup.html_to_text(html) == text


def test_an_html_only_body_is_the_same_conversion_through_a_draft(kind, fake):
    for n, (html, _) in enumerate(HTML_PAIRS, start=1):
        fake.message(n, SINCE + n * MINUTE, tree=leaf("text/html", html))
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(n) for n in range(1, len(HTML_PAIRS) + 1)]
    for draft, (_, text) in zip(got.drafts, HTML_PAIRS, strict=True):
        assert text in draft.text, text
        assert draft.text.rstrip().endswith(text)


def paragraphs(count: int) -> str:
    return "\n\n".join(
        f"Paragraph {k:04d} says what step {k} of the plan is." for k in range(count)
    )


def test_a_hard_wrapped_body_of_3200_lines_in_400_paragraphs_is_one_part(kind, fake):
    body = "\n\n".join(
        "\n".join(f"Line {p:03d}.{k} of a paragraph wrapped at a fixed width" for k in range(8))
        for p in range(400)
    )
    fake.message(1, SINCE + HOUR, body=body, subject="Wrapped")
    draft = only(pull(fake, opened(kind, fake)))
    assert draft.source_ref == ref(1) and draft.topic == "Wrapped, 2026-11"


def test_a_body_of_1600_list_items_is_two_parts_cut_between_items(kind, fake):
    items = [f"- Item {k:04d}: check the figures for line {k}" for k in range(1600)]
    fake.message(1, SINCE + HOUR, body="\n".join(items), subject="Checklist")
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(1), ref(1, 2)]
    assert [d.topic for d in got.drafts] == ["Checklist, 2026-11", "Checklist, 2026-11, part 2"]
    held = Counter(line for d in got.drafts for line in d.text.splitlines() if line in items)
    assert held == Counter(items)  # each item whole, and in one part only


# Read once


def test_a_second_pull_writes_nothing_and_reads_no_message_again(kind, fake):
    for n in range(1, 6):
        fake.message(n, SINCE + n * HOUR)
    fake.message(6, SINCE - 2 * HOUR)  # before switch-on, inside the listing's margin
    fake.message(7, SINCE + 7 * HOUR, labels=("DRAFT",))
    read = reader(kind, fake)
    first = read()
    assert refs(first) == [ref(n) for n in range(1, 6)]
    fake.clock.now += HOUR
    second = read(first.cursor)
    assert second.drafts == [] and second.skipped == []
    assert fake.calls("list") and fake.calls("get") == []


def test_a_message_unlabelled_or_trashed_after_it_was_read_stays_as_it_was(kind, fake):
    one = fake.message(1, SINCE + HOUR)
    two = fake.message(2, SINCE + 2 * HOUR)
    read = reader(kind, fake)
    first = read()
    assert refs(first) == [ref(1), ref(2)]
    fake.clock.now = NOW + HOUR
    one.unlabel_at = fake.clock.now
    two.labels.add("TRASH")
    second = read(first.cursor)
    assert second.drafts == [] and second.skipped == []
    fake.clock.now = NOW + 2 * HOUR
    fake.label(one)  # labelled again: it was read once, and that stands
    two.labels.discard("TRASH")
    third = read(second.cursor)
    assert third.drafts == [] and fake.calls("get") == []


@pytest.mark.parametrize("after", ["inclusive", "exclusive"])
def test_the_cursor_holds_no_secret_id_subject_or_name_and_stays_bounded_over_weeks(
    kind, fake, after
):
    fake.inclusive = after == "inclusive"
    weeks = 9
    for n in range(1, weeks * 7 * 6 + 1):  # a message every four hours
        at = NOW + n * 4 * HOUR - 90 * MINUTE
        labelled = at + 2 * DAY if n % 5 == 0 else at  # some are labelled two days later
        message = fake.message(
            n,
            at,
            subject=f"Weekly report {n % 3}",
            sender="Brook Lane <brook@example.test>",
            labelled=labelled,
            labels=("DRAFT",) if n % 11 == 0 else ("INBOX",),
        )
        message.thread = tid(n % 4)
    read = reader(kind, fake, cred=USER, since=NOW, lookback_days=7)
    cursor, written, lengths = None, Counter(), {}
    for step in range(1, weeks * 7 * 4 + 1):  # a pull every six hours
        fake.clock.now = NOW + step * 6 * HOUR
        got = read(cursor)
        cursor = got.cursor
        written.update(refs(got))
        assert isinstance(json.loads(cursor), dict | list)  # opaque, but JSON
        assert not leaked(cursor, fake) and "@" not in cursor
        for words in ("Weekly report", "Brook", "Lane", "c6m"):  # subjects, names, message ids
            assert words not in cursor, words
        if step == 6 * 7 * 4:
            lengths["six weeks"] = len(cursor)
    last = fake.clock.now
    mail = [m for m in fake.messages.values() if "DRAFT" not in m.labels]
    must = {ref(m.n) for m in mail if m.label_at <= last - 6 * HOUR}  # a pull came after it
    may = {ref(m.n) for m in mail if m.label_at <= last}
    assert len(must) > 300 and must <= set(written) <= may
    assert max(written.values()) == 1  # none twice
    assert len(cursor) <= 1.25 * lengths["six weeks"] + 200  # it did not grow with the mail


# When Google says no


@pytest.mark.parametrize(
    ("endpoint", "how"),
    [
        ("labels", 401),
        ("labels", "403 domainPolicy"),
        ("list", 401),
        ("list", "403 domainPolicy"),
        ("list", "403 no body"),
        ("get", 401),
        ("get", "403 domainPolicy"),
        ("get", "403 not json"),  # treated by its status alone: refused, not a rate limit
    ],
)
def test_access_refused_by_gmail_says_what_to_check_and_is_not_retried(kind, fake, endpoint, how):
    fake.message(1, SINCE + HOUR)
    fake.always[endpoint] = how
    said = str(failed_pull(fake, opened(kind, fake)))
    assert "scope" in said.lower()
    assert CANARY not in said and not leaked(said, fake)
    assert fake.slept == [] and len(fake.calls(endpoint)) == 1


def test_a_message_gone_between_the_list_and_the_read_is_skipped_once(kind, fake):
    fake.message(1, SINCE + HOUR).gone = True  # still listed, but reading it answers 404
    fake.message(2, SINCE + 2 * HOUR)
    read = reader(kind, fake)
    first = read()
    assert refs(first) == [ref(2)]
    (skip,) = first.skipped
    assert mid(1) in skip.ref and "gone" in skip.reason.lower()
    fake.clock.now += HOUR
    again = read(first.cursor)
    assert again.drafts == [] and again.skipped == []


RATE_LIMITS = [
    "429",
    "429 no body",
    "429 not json",
    "403 rateLimitExceeded",
    "403 userRateLimitExceeded",
]


@pytest.mark.parametrize("how", RATE_LIMITS)
def test_rate_limits_are_retried_with_the_documented_backoff_through_sleep(kind, fake, how):
    fake.message(1, SINCE + HOUR)
    fake.forced = [("get", how), ("get", how)]
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(1)]
    assert len(fake.slept) == 2
    backed_off(fake.slept)

    for endpoint in ("labels", "list"):
        fake.slept.clear()
        fake.always = {endpoint: how}
        said = str(failed_pull(fake, opened(kind, fake)))
        assert 1 <= len(fake.slept) <= 4
        backed_off(fake.slept)
        assert len(fake.calls(endpoint)) == len(fake.slept) + 1
        assert CANARY not in said and not leaked(said, fake)


def waited(slept: list[float], daily: bool) -> None:
    if daily:
        assert slept == []  # the day's limit is not retried
    else:
        assert 1 <= len(slept) <= 4
        backed_off(slept)


@pytest.mark.parametrize("how", [*RATE_LIMITS, "403 dailyLimitExceeded"])
def test_a_limit_at_the_first_read_fails_and_after_twenty_reads_returns_them(kind, fake, how):
    for n in range(1, 31):
        fake.message(n, SINCE + n * MINUTE)
    daily = "dailyLimitExceeded" in how
    fake.fail_reads_after = (0, how)
    said = str(failed_pull(fake, opened(kind, fake)))
    assert CANARY not in said and not leaked(said, fake)
    waited(fake.slept, daily)

    fake.slept.clear()
    fake.fail_reads_after = (20, how)
    read = reader(kind, fake)
    first = read()
    assert len(first.drafts) == 20
    assert sorted(d.source_ref.removeprefix("gmail:") for d in first.drafts) == sorted(
        fake.read_ids()
    )
    oldest_first(fake, first)
    waited(fake.slept, daily)  # it stopped there: what it did not reach is for the next pull

    fake.fail_reads_after = None
    fake.clock.now += HOUR
    second = read(first.cursor)
    both = Counter(refs(first) + refs(second))
    assert both == Counter({ref(n): 1 for n in range(1, 31)})


@pytest.mark.parametrize(
    ("endpoint", "how"),
    [
        ("labels", 500),
        ("list", 503),
        ("get", 500),
        ("get", "502 not json"),
        ("labels", "timeout"),
        ("list", "timeout"),
        ("get", "timeout"),
        ("labels", "not json"),
        ("labels", "wrong shape"),
        ("list", "array"),
        ("list", "wrong shape"),
        ("get", "array"),
        ("get", "wrong shape"),
        ("token", 500),
        ("token", "timeout"),
        ("token", "no access_token"),
    ],
)
def test_a_server_error_a_timeout_or_an_odd_body_fails_the_pull(kind, fake, endpoint, how):
    fake.message(1, SINCE + HOUR)
    fake.message(2, SINCE + 2 * HOUR)
    fake.always[endpoint] = how
    said = str(failed_pull(fake, opened(kind, fake)))
    assert said.strip() and CANARY not in said and not leaked(said, fake)


# The deadline


def test_a_pull_cut_by_its_deadline_and_every_resume_point_go_on_without_loss_or_repeat(kind, fake):
    for n in range(1, 5):
        fake.message(n, SINCE + n * HOUR)
    fake.message(5, SINCE + 5 * HOUR, body=paragraphs(3_200), subject="Long plan")
    fake.message(6, SINCE + 6 * HOUR)

    whole = pull(fake, opened(kind, fake))
    full = refs(whole)
    assert full == [ref(1), ref(2), ref(3), ref(4), ref(5), ref(5, 2), ref(5, 3), ref(6)]
    assert all(draft.resume for draft in whole.drafts)
    for index, draft in enumerate(whole.drafts):  # a run that stops right after this draft
        fake.clock.now += HOUR
        rest = pull(fake, opened(kind, fake), draft.resume)
        assert refs(rest) == full[index + 1 :], index

    fake.clock.now += HOUR
    fake.tick = MINUTE  # every request takes a minute
    hurried = opened(kind, fake, deadline=fake.clock.now + 5 * MINUTE)
    cut = pull(fake, hurried)
    assert 0 < len(cut.drafts) < len(full)
    assert all(draft.resume for draft in cut.drafts)
    oldest_first(fake, cut)
    fake.tick = timedelta(0)
    fake.clock.now += HOUR
    rest = pull(fake, opened(kind, fake), cut.cursor)
    oldest_first(fake, rest)
    assert Counter(refs(cut) + refs(rest)) == Counter(full)


# Together with the framework


@pytest.fixture
def world(tmp_path, fake):
    clock = Clock(SINCE - DAY)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    for name in ("mail", "planning"):
        store.create_project(name)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    yield SimpleNamespace(store=store, db=db, clock=clock, http=http)
    store.close()


def run(world, fake, at: datetime, cred: str = SA):
    fake.clock.now = at
    fake.new_pull()
    return asyncio.run(
        run_source(
            world.store, "mail", http=world.http, env=fake.env(cred), clock=fake.clock,
            sleep=fake.sleep,
        )
    )  # fmt: skip


def switched_on(world, cred: str = SA, **settings) -> None:
    world.store.add_source("mail", "gmail", config(cred, **settings))
    world.clock.now = SINCE
    world.store.enable_source("mail")


def notes(world, project: str = "mail") -> list:
    return world.store.list_notes(world.store.get_project(project).id)


def pages(world, project: str = "mail") -> list:
    return world.store.list_pages(world.store.get_project(project).id)


def test_through_the_runner_a_source_is_off_until_switched_on_then_writes_each_message_once(
    world, fake
):
    fake.message(1, SINCE - 3 * HOUR, subject="Budget review")  # before switch-on
    fake.message(2, SINCE + HOUR, subject="Budget review", thread=tid(2))
    fake.message(3, SINCE + 2 * HOUR, subject="Re: Budget review", thread=tid(2))
    fake.message(4, SINCE + 3 * HOUR, subject="Hiring plan")
    late = fake.message(5, SINCE + 30 * MINUTE, subject="Office move", labelled=False)

    added = world.store.add_source("mail", "gmail", config(projects=[]))
    assert added.enabled is False
    assert run(world, fake, NOW).off is True and fake.requests == []

    world.clock.now = SINCE
    world.store.enable_source("mail", projects=["mail"])
    report = run(world, fake, NOW)
    assert (report.saved, report.unchanged, report.refused, report.rejected) == (3, 0, 0, 0)
    held = notes(world)
    assert sorted(c.source_ref for c in held) == [ref(2), ref(3), ref(4)]
    assert {(c.via, c.label) for c in held} == {("gmail", "gmail")}
    assert len(pages(world)) == 2  # the conversation's two messages share a page
    assert notes(world, "planning") == []
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind) == ("source.synced", "system")
    assert (event.detail["source"], event.detail["saved"]) == ("mail", 3)

    again = run(world, fake, NOW + 5 * MINUTE)
    assert (again.saved, again.unchanged) == (0, 0)
    fake.label(late, NOW + 6 * MINUTE)
    after = run(world, fake, NOW + 10 * MINUTE)
    assert after.saved == 1 and ref(5) in {c.source_ref for c in notes(world)}
    assert len(pages(world)) == 3

    assert not leaked(dump(world.db), fake)
    for event in world.store.list_audit(limit=100):
        assert not leaked(json.dumps(event.detail), fake)


LONG_SUBJECT = (
    "Quarterly planning for the platform team: budget, hiring, roadmap and the offsite in Rome."
)


def test_a_body_of_3200_paragraphs_is_three_parts_each_stored_and_none_fills_a_page(world, fake):
    assert len(LONG_SUBJECT) == 90
    fake.message(1, SINCE + HOUR, subject=LONG_SUBJECT, body=paragraphs(3_200))
    switched_on(world)
    report = run(world, fake, NOW)
    assert (report.saved, report.skipped, report.malformed) == (3, 0, 0), report.reasons
    held = notes(world)
    assert sorted(c.source_ref for c in held) == [ref(1), ref(1, 2), ref(1, 3)]
    made = pages(world)
    assert len(made) == 3  # the month and the part number are never what is cut
    assert all(0 < len(page.claims) <= 1_500 for page in made), [len(p.claims) for p in made]
    said = "\n".join(c.text for c in held)
    for k in range(3_200):
        assert f"Paragraph {k:04d} says" in said, k


def test_the_same_long_subject_in_two_months_is_on_two_pages(world, fake):
    fake.message(1, datetime(2026, 11, 20, 9, tzinfo=UTC), subject=LONG_SUBJECT)
    fake.message(2, datetime(2026, 12, 4, 9, tzinfo=UTC), subject="Re: " + LONG_SUBJECT)
    fake.message(3, datetime(2026, 12, 9, 9, tzinfo=UTC), subject="RE: " + LONG_SUBJECT)
    switched_on(world)
    assert run(world, fake, datetime(2026, 12, 10, tzinfo=UTC)).saved == 3
    made = pages(world)
    assert len(made) == 2
    assert sorted(len(page.claims) > 0 for page in made) == [True, True]


@pytest.mark.parametrize("policy", ["secrets", "secrets+personal"])
def test_through_the_runner_the_guard_decides_on_a_reply_as_on_any_note(world, fake, policy):
    reply = (
        "Friday works for me; I will bring the figures.\n\n"
        "On Thu, 12 Nov 2026 at 09:30, Ada Lovelace <ada@example.test> wrote:\n"
        "> Can we move the budget review to Friday?\n\n"
        "-- \nBrook Lane\nHead of Finance\nbrook@example.test\n+44 20 7946 0958\n"
    )
    fake.message(1, SINCE + HOUR, subject="Budget review")
    fake.message(2, SINCE + 2 * HOUR, subject="Re: Budget review", body=reply)
    fake.message(3, SINCE + 3 * HOUR, subject="Hiring plan")
    switched_on(world)
    if policy != "secrets":
        world.store.set_setting("guard.policy", policy)
    report = run(world, fake, NOW)
    held = {c.source_ref: c for c in notes(world)}
    if policy == "secrets":  # a new workspace's policy
        assert report.saved == 3 and report.refused == 0
        assert "ada@example.test" in held[ref(2)].text  # the body as it is, not rewritten
        assert "+44 20 7946 0958" in held[ref(2)].text
    else:
        assert (report.saved, report.refused) == (2, 1)
        assert sorted(held) == [ref(1), ref(3)]
        (declined,) = report.declined
        assert declined["link"] == ref(2) and "email_address" in declined["codes"]
        assert run(world, fake, NOW + HOUR).refused == 0  # final for that message


@pytest.mark.parametrize("cred", [SA, USER])
def test_through_the_runner_a_secret_gmail_sends_back_is_never_stored(world, fake, cred):
    fake.message(1, SINCE + HOUR).echo = True
    fake.message(2, SINCE + 2 * HOUR)
    switched_on(world, cred)
    report = run(world, fake, NOW, cred=cred)
    assert report.saved == 1 and [c.source_ref for c in notes(world)] == [ref(2)]
    assert report.reasons.get(HELD_CREDENTIALS) == 1
    assert not leaked(dump(world.db), fake)


def test_through_the_runner_a_failed_run_moves_no_cursor_and_the_next_writes_the_rest(world, fake):
    switched_on(world)
    fake.message(1, SINCE + HOUR)
    assert run(world, fake, NOW).saved == 1
    before = world.store.get_source("mail")

    fake.message(2, NOW + HOUR)
    fake.message(3, NOW + 2 * HOUR)
    fake.forced = [("get", 500)]  # the first of the two new messages cannot be read
    with pytest.raises(SourceError):
        run(world, fake, NOW + 3 * HOUR)
    failed = world.store.get_source("mail")
    assert failed.cursor == before.cursor and failed.last_error
    assert CANARY not in failed.last_error and not leaked(failed.last_error, fake)
    assert len(notes(world)) == 1

    report = run(world, fake, NOW + 4 * HOUR)
    assert report.saved == 2 and world.store.get_source("mail").last_error is None
    assert sorted(c.source_ref for c in notes(world)) == [ref(1), ref(2), ref(3)]
    assert run(world, fake, NOW + 5 * HOUR).saved == 0


def test_the_command_line_adds_switches_on_and_syncs_without_showing_a_secret(
    tmp_path, kind, keys, monkeypatch, capsys
):
    db = tmp_path / "cairn.db"
    store = Store(db)
    store.create_project("mail")
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
    assert cli("source-add", "--db", str(db), "--name", "mail", "--kind", "gmail",
               "--config", settings) == 0  # fmt: skip
    assert cli("source-sync", "--db", str(db), "--name", "mail") == 0
    assert fake.requests == []  # off: nothing read, no token asked for
    assert cli("source-enable", "--db", str(db), "--name", "mail", "--project", "mail") == 0

    store = Store(db)
    try:
        since = parsed(store.get_source("mail").enabled_at)
    finally:
        store.close()
    fake.message(1, since - HOUR)
    just_after = since + timedelta(milliseconds=2)
    fake.message(2, just_after)
    while datetime.now(UTC) <= just_after + timedelta(milliseconds=1):
        pass  # the real clock, which the command line reads, has passed it
    assert cli("source-sync", "--db", str(db), "--name", "mail") == 0
    assert cli("source-list", "--db", str(db)) == 0
    assert len(fake.calls("token", every=True)) == 1

    monkeypatch.setenv(CRED_ENV, json.dumps(service_account(keys, private_key=broken_key())))
    assert cli("source-sync", "--db", str(db), "--name", "mail") == 1
    assert CRED_ENV in said[-1]

    store = Store(db)
    try:
        held = store.list_notes(store.get_project("mail").id)
        assert [c.source_ref for c in held] == [ref(2)]
    finally:
        store.close()
    shown = "\n".join(said)
    assert not leaked(shown, fake) and CANARY not in shown and NOT_A_KEY not in shown
    assert not leaked(dump(db), fake)
