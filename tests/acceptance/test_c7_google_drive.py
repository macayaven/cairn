"""C7 acceptance tests (frozen): the Google Drive source, the documents of one folder.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/sources/google_drive.py and
src/cairn/sources/__init__.py. Google's request and response shapes are recorded in
docs/google-drive-api.md (and, for signing in, docs/google-auth.md, whose module
cairn.sources.google_auth is tested with C4 and not again here). Where those notes say
Google's pages are silent or disagree, the connector depends on neither reading: the stand-in
below behaves both ways, and the tests run under both where it matters.

Why it reads each document once
- cairn keeps every note it is given, so reading a document again each time it changes would
  pile near-copies onto its page, and Google gives a reader no dependable sign that a Doc's
  content changed. So a document is read once, when it has stopped changing for a while, and
  later edits are not followed (decision 26).

The kind
- `KINDS["google_drive"]` (cairn.sources.google_drive). It starts off. Via and label
  "google-drive". It asks for one scope, https://www.googleapis.com/auth/drive.readonly. Its
  `default_project` is "documents". `describe(config)` says in one sentence which folder it
  reads (its id) and as whom (the subject, when there is one), never a credential or a
  variable's value. `switch_on` says what switching on reads: the folder's documents made or
  changed from then on, each once it has rested, or every document in the folder when
  `existing` is set.
- `check(config)` returns the config with its defaults filled in, or raises ValueError naming
  the setting at fault, never a value that could be a credential:
  - `credentials_env` (required): the name of the variable that holds the Google credential
    (as for Meet). A credential given there instead of a name is refused.
  - `subject` (optional): one email address, the person a service account acts for. Without
    it a service account acts as itself (a folder shared with its own address).
  - `folder_id` (required): the folder's id as it appears in its address, 10 to 100 letters,
    digits, "-" and "_". A whole address pasted there is refused, naming `folder_id`.
  - `settle_hours`: 24 unless given; a whole number from 1 to 720.
  - `existing`: false unless given; true or false.
  - `projects`: a list of at most one project name; may be empty while it is off.
  - `base_url`: "https://www.googleapis.com/drive/v3" unless given; https, or http on this
    machine.
  - No other setting.
- Opened with a refresh token (an "authorized_user" credential), a config naming `subject` is
  a SourceError naming `subject`. A service account signs in for the subject when there is
  one, else as itself (no `sub` claim).

What it reads
- The files directly in the folder (not its subfolders; a shortcut is not followed), not
  trashed, of these types: a Google Doc (application/vnd.google-apps.document), exported as
  text/markdown; a file of type text/markdown or text/plain, downloaded with `alt=media`.
  Every other type is passed over silently.
- What counts as new. The first pull after a switch-on takes stock: a document the folder
  lists then, last modified before `since`, is marked as already there and not read (unless
  `existing` is set, when it is read). From then on a document is read when it was modified
  at or after `since`, or when it appears in the folder and was not there at that stock-taking,
  whatever its `modifiedTime`. Whether a move changes `modifiedTime` is not stated by
  Google's pages; the stock-taking rule does not depend on it. A document put into the folder
  between the switch-on and the first pull counts as already there. Switching off and on again
  takes stock again: what arrived or changed while the source was off is not read.
- Each document once, when its `modifiedTime` is at least `settle_hours` old by the clock. One
  still being edited is left for a later pull. After its draft is written, an edit, a rename, a
  move or a deletion at Google changes nothing in cairn. Putting a document back into the
  folder after taking it out is choosing it again: a document taken out (or trashed) and later
  put back (or restored) is read again once it has rested. Unchanged, the store finds the note
  it already holds ("unchanged"); edited meanwhile, a second note is stored for it. That is how
  a person has cairn read a newer version of a document.
- A document the reader may not download (`capabilities.canDownload` false) is skipped once,
  with a reason in words. A file that is not a Google Doc and is larger than 10 MB by its
  `size` is skipped once ("too large"). Neither is asked for its content. A Google Doc's `size`
  (where Google gives one) is its stored size, not its export's: a Doc is always exported,
  whatever its `size`, and the export decides.

How it finds them
- Every pull first asks for the folder itself: `files.get` on `folder_id` with `fields`
  selecting `id`, `mimeType`, `trashed` and `capabilities/canListChildren`, and
  `supportsAllDrives=true`; it is the pull's first request to Drive. A 404 there, a `mimeType`
  that is not a folder's, a trashed folder, or `canListChildren` false (a folder with limited
  access, which Google lists as empty): SourceError that names the `folder_id` setting and says
  what is wrong and what to do (share the folder with the account that reads it; give a
  folder's id; restore it from the trash), without the body. Nothing is listed then.
- Then every pull lists the folder with `files.list`: `q` exactly
  "'<folder id>' in parents and trashed = false"; `fields` selecting `nextPageToken` and, of
  each file, `id`, `name`, `mimeType`, `modifiedTime`, `size`, `webViewLink`,
  `lastModifyingUser/displayName` and `capabilities/canDownload`; `pageSize` 1000;
  `supportsAllDrives=true` and `includeItemsFromAllDrives=true`; every page with the same
  parameters beside `pageToken`. It relies neither on the order of results nor on full pages.
  A text file's download also says `supportsAllDrives=true` (the notes: `files.get` finds a
  shared drive's file only so), so a folder in a shared drive is read the same way.
- A pull's documents are written by `modifiedTime`, then by id. At most 25 documents' content
  is fetched in one pull; the rest comes with the next pull, none lost and none twice.
- The cursor is opaque JSON the connector owns. It holds no token, key, secret, file id, file
  name, person's name or text, and it keeps what it knows only of documents the folder still
  lists, so it shrinks when they leave.
- At the `deadline` a pull returns what it finished; every draft carries a `resume` cursor
  from which the next pull goes on with no draft written twice and none skipped.

What it writes, one draft a document (more when it is long), into the config's project
- `title`: the file's name; for a .md or .txt file, without that extension. `topic`: when the
  title fits (its slug, with room kept for ", part NN", is within the compiler's 64
  characters), the title exactly, and ", part N" from the second part. When it does not fit:
  the title cut short (at a word where it can be), ", " and the first 6 hex digits of a digest
  of the file's id (never the id or a part of it), then ", part N" from the second part. So
  one document's parts stay together, two documents with long names never share a page
  whatever they are called and whenever they changed, and nothing of the id can be read from
  the topic. Two documents with the same short name share a page, as any two notes with one
  topic do.
- `text`: Markdown. A heading with the title, then the facts: when it was last modified, in
  UTC (YYYY-MM-DD HH:MM); who last modified it, by display name, when Google gives one; a link
  to open it (`webViewLink` without its query string and fragment, so no resource key is
  copied). No email address among the facts. Then the content as exported or downloaded,
  decoded as UTF-8 with replacement (a leading byte-order mark dropped), unchanged but for
  embedded images: a reference definition whose target is a `data:` address is dropped, and
  an inline image with a `data:` address is replaced by its alt text.
- A long document is split into parts, each its own draft: a part holds at most 1,500
  passages as cairn counts them, ends between two passages, and ends just before a heading
  where one falls in its last third. Through `run_source` every part is stored and no page
  holds more than 1,500 claims. A document that would take more than 20 parts is skipped once
  ("too long"); one whose content is empty or white space is skipped once ("empty").
- `source_ref`: "drive:<file id>", and "/2", "/3" for later parts. `label`: "google-drive".

When Google says no
- On the listing: 401, or 403 for a reason that is not a rate limit: SourceError saying access
  was refused and what to check (the words "scope" and "shared" among it), not retried. 404:
  SourceError naming `folder_id`.
- On a document's content: 404 is skipped once ("gone"). A 401 is a SourceError. A 403 for a
  rate limit is retried (below). A 403 that refuses the whole token is a SourceError, as on the
  listing: `domainPolicy` (the domain does not allow the app), and a reason that says the token
  lacks the scope (`insufficientPermissions`, or `ACCESS_TOKEN_SCOPE_INSUFFICIENT` in the newer
  shape). Every other 403 on content (`fileNotDownloadable`, `fileNotExportable`,
  `download_restricted_for_revision`, `cannotDownloadFile`, `exportSizeLimitExceeded`,
  `appNotAuthorizedToFile`, `teamDriveMembershipRequired`, and any reason the connector does not
  know) skips that one document, once, with a reason holding nothing of the response, and the
  pull goes on.
- 429, and 403 with the reason `rateLimitExceeded` or `userRateLimitExceeded`: retried at most
  4 times, the n-th wait (through the injected `sleep`) 2^n seconds plus up to one second, n
  from 0 or 1. `dailyLimitExceeded` is not retried. When the limit holds: a pull that has read
  documents returns them with a cursor that goes on from there; one that has read none raises
  SourceError.
- A 5xx, a timeout, a listing not of the documented shape: SourceError. No message holds a
  response body or a secret. A failed pull returns nothing, so no cursor moves.
- Errors come in the older `error.errors[]` shape; the stand-in also answers in the newer one
  (`error.status`, `error.details[]`), with the same reason strings, and the connector reads
  the reason from either.

Together with the framework: a Drive source is off when added and reads nothing; switched on
with a project, `run_source` writes the documents changed since that have rested, one page a
document; again, nothing new; a document that rests later is written by the run after. The
database, the audit log and the command line's output hold no private key, token, client
secret or refresh token.
"""

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import unicodedata
from base64 import urlsafe_b64decode, urlsafe_b64encode
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
from cairn.store import Store

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
DRIVE = "https://www.googleapis.com/drive/v3"
CRED_ENV = "GOOGLE_C7_CREDENTIALS"
SA, USER = "service_account", "authorized_user"
SUBJECT = "ada@example.test"
CLIENT_EMAIL = "cairn-reader@c7-project.iam.example.test"
KEY_ID = "c7" + "5e1d0b9a" * 4  # made up
SA_CLIENT_ID = "1" + "07" * 10
USER_CLIENT_ID = "c7-" + "desktop-client" + ".apps.example.test"
CLIENT_SECRET = "GOCSPX-" + "c7" + "MadeUp" + "Secret4c1f"  # never a real secret
REFRESH_TOKEN = "1//" + "0c7" + "MadeUpRefresh" + "Token9b2e"
NOT_A_KEY = "c7" + "NotAKey"
CANARY = "c7-" + "body-" + "canary-5a9c"  # in every error body the stand-in sends
FOLDER = "1C7Folder" + "AbCdEfGhIjKlMnOp"
OTHER = "1C7Other" + "QrStUvWxYzAbCdEf"
SHARED = "0C7Shared" + "GhIjKlMnOpQrStUv"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=3)  # when the source was switched on
MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
LONG_AGO = datetime(2023, 4, 5, 6, 7, 8, tzinfo=UTC)
DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
SLIDES = "application/vnd.google-apps.presentation"
FOLDER_TYPE = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
MD, TXT, PDF, PNG = "text/markdown", "text/plain", "application/pdf", "image/png"
Q = re.compile(r"'([A-Za-z0-9_-]+)' in parents and trashed = false")
NEEDED = (
    "nextPageToken",
    "files/id",
    "files/name",
    "files/mimeType",
    "files/modifiedTime",
    "files/size",
    "files/webViewLink",
    "files/lastModifyingUser/displayName",
    "files/capabilities/canDownload",
)
STATUS = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    429: "RESOURCE_EXHAUSTED",
}
DEFAULT_REASON = {
    400: "badRequest",
    401: "authError",
    403: "insufficientPermissions",
    404: "notFound",
    429: "rateLimitExceeded",
}
TAGGED = re.compile(r"(?P<cut>.+), (?P<tag>[0-9a-f]{6})(?:, part (?P<part>\d+))?")
PEOPLE = {"Ada Park": "ada@example.test", "Brook Lane": "brook@example.test"}


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
    if re.search(r"[^A-Za-z0-9_\-=]", segment):
        raise ValueError(segment)
    return urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def long_names() -> list[str]:
    """Two names of 90 characters that differ only in their last word."""
    stem = "Quarterly planning notes for the payments platform and the ledger, review"
    filler = "z" * (90 - len(stem) - len(" Alpha") - 1)
    names = [f"{stem} {filler} {word}" for word in ("Alpha", "Bravo")]
    assert all(len(name) == 90 for name in names), names
    assert names[0].rsplit(" ", 1)[0] == names[1].rsplit(" ", 1)[0]
    return names


def raw_slug(text: str) -> str:
    """A slug as the compiler makes one (cairn.compiler.slugify), before it is cut at 64."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def fid(n: int) -> str:
    """A file id: Drive's alphabet, with capitals, so no hex digest can hold one."""
    return f"1C7Doc{n:05d}" + "XyZwVuTsRqPoNmLk"


def ref(n: int, part: int = 1) -> str:
    return f"drive:{fid(n)}" + (f"/{part}" if part > 1 else "")


def refs(pulled) -> list[str]:
    return [draft.source_ref for draft in pulled.drafts]


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def paragraphs(count: int, word: str = "Paragraph", start: int = 0) -> list[str]:
    return [f"{word} {k:05d} of the ledger design." for k in range(start, start + count)]


def held(text: str, word: str = "Paragraph") -> list[int]:
    return [int(n) for n in re.findall(rf"^{word} (\d{{5}}) of the ledger design\.$", text, re.M)]


@dataclass
class File:
    id: str
    name: str
    mime: str
    parents: list[str]
    modified: datetime
    content: bytes = b""
    size: int | None = None  # None: the content's length (a Doc: none, unless docs_sized)
    trashed: bool = False
    can_download: bool = True
    editor: str | None = "Ada Park"  # display name of the last modifier; None: not given
    gone: bool = False  # still listed, but its content answers 404 (deleted in between)
    refused: str | None = None  # its content answers 403 with this reason
    echo: str | None = None  # its content also holds a secret: "token", "refresh", "secret"
    target: str | None = None  # a shortcut's target


def service_account(keys, **changes) -> dict:
    doc = {
        "type": "service_account",
        "project_id": "c7-project",
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


def selection(fields: str | None) -> dict | None:
    """The `fields` parameter as a tree (docs/google-drive-api.md, D.4): a comma list, "a/b"
    nests, "a(b,c)" sub-selects, "*" is everything. None: no `fields` given. ValueError when it
    does not parse."""
    if fields is None:
        return None
    tree: dict = {}
    text = fields.replace(" ", "")
    at = 0

    def parse(into: dict, closing: str | None) -> None:
        nonlocal at
        while at < len(text):
            match = re.match(r"[A-Za-z_*][A-Za-z0-9_*]*(?:/[A-Za-z_*][A-Za-z0-9_*]*)*", text[at:])
            if not match:
                raise ValueError(fields)
            at += match.end()
            node = into
            for key in match.group().split("/"):
                node = node.setdefault(key, {}) if node is not True else node
                if node is True:
                    break
            if at < len(text) and text[at] == "(":
                at += 1
                parse(node, ")")
            elif node == {}:
                *path, last = match.group().split("/")
                parent = into
                for key in path:
                    parent = parent[key]
                parent[last] = True
            if at < len(text) and text[at] == ",":
                at += 1
                continue
            if closing and at < len(text) and text[at] == closing:
                at += 1
                return
            if at < len(text):
                raise ValueError(fields)
        if closing:
            raise ValueError(fields)

    parse(tree, None)
    return tree


def selects(tree: dict | None, path: str) -> bool:
    node: object = tree
    for key in path.split("/"):
        if node is True:
            return True
        if not isinstance(node, dict):
            return False
        if "*" in node:
            return True
        if key not in node:
            return False
        node = node[key]
    return node is True or isinstance(node, dict)


def pick(value: dict, tree: object) -> dict:
    if tree is True or (isinstance(tree, dict) and "*" in tree):
        return value
    picked = {}
    for key, sub in tree.items():  # type: ignore[union-attr]
        if key in value:
            inner = value[key]
            picked[key] = pick(inner, sub) if isinstance(inner, dict) else inner
    return picked


class FakeGoogle:
    """Google's token endpoint and the Drive API v3, as docs/google-auth.md and
    docs/google-drive-api.md describe them, as one httpx mock. The token endpoint checks a
    service account's JWT or a refresh token's form and issues a new access token each time;
    Drive accepts only the latest one. Drive holds folders (one of them in a shared drive,
    whose children are listed only with both all-drives flags) and files that can be edited,
    renamed, moved and trashed between pulls; it answers `files.list` for the one `q` form the
    connector uses (any other `q` is a 400), returns only the `fields` asked for (the default
    fields when none are), pages with tokens that die with the pull and must come with the
    same parameters, and can send short pages, shuffle, omit an empty `files`, write times
    with 0, 3 or 9 fractional digits and "Z" or "+00:00", give Docs a `size` or not, and
    answer 401, 403 (each reason, in either error shape), 404, 429, 5xx, a timeout or an odd
    body. `files.export` serves Docs only, `files.get?alt=media` everything else. Every
    request is kept."""

    def __init__(self, clock, keys) -> None:
        self.clock = clock
        self.keys = keys
        self.base = DRIVE
        self.tick = timedelta(0)
        self.files: dict[str, File] = {}
        self.folders = {FOLDER: None, OTHER: None, SHARED: "0C7Drive"}  # id: shared drive
        self.unshared: set[str] = set()  # folders the account cannot see: 404
        self.limited: set[str] = set()  # limited access: canListChildren false, lists empty
        self.binned: set[str] = set()  # folders in the trash
        self.users = {SUBJECT}
        self.requests: list[SimpleNamespace] = []
        self.slept: list[float] = []
        self.issued: list[str] = []
        self.valid: tuple[str, datetime] | None = None
        self.gen = 0
        self.shape = "errors"  # or "details": the newer error shape
        self.short_pages = False
        self.shuffle = False
        self.omit_empty = False
        self.digits = 3
        self.zone = "Z"
        self.docs_sized = False
        self.forced: list[tuple[str, object]] = []
        self.always: dict[str, object] = {}
        self.content_limit: tuple[int, object] | None = None  # after n contents, answer how
        self.refusal: tuple[int, str] | None = None
        self.scope = SCOPE

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def new_pull(self) -> None:
        self.gen += 1

    def env(self, cred: str = SA, **changes) -> dict[str, str]:
        doc = service_account(self.keys, **changes) if cred == SA else authorized_user(**changes)
        return {CRED_ENV: json.dumps(doc)}

    def doc(self, n: int, modified: datetime, text: str | None = None, **settings) -> File:
        """A Google Doc in FOLDER (by default) whose Markdown export is `text`."""
        settings.setdefault("parents", [FOLDER])
        settings.setdefault("name", f"Design note {n:05d}")
        settings.setdefault("mime", DOC)
        if text is None:
            text = f"# Note {n}\n\nDecision {n:05d}: the ledger moves on Friday.\n"
        content = text.encode() if isinstance(text, str) else text
        made = File(id=fid(n), modified=modified, content=content, **settings)
        self.files[made.id] = made
        return made

    def edit(self, n: int, at: datetime, text: str | None = None) -> None:
        found = self.files[fid(n)]
        found.modified = at
        found.content = (text or f"Edited at {at:%H:%M}: the ledger moves on Monday.\n").encode()

    def calls(self, endpoint: str | None = None, *, every: bool = False) -> list[SimpleNamespace]:
        return [
            r
            for r in self.requests
            if (every or r.gen == self.gen) and (endpoint is None or r.endpoint == endpoint)
        ]

    def content_calls(self, *, every: bool = False) -> list[SimpleNamespace]:
        return [r for r in self.calls(every=every) if r.endpoint in ("export", "media")]

    def ts(self, moment: datetime) -> str:
        moment = moment.astimezone(UTC)
        base = moment.strftime("%Y-%m-%dT%H:%M:%S")
        if self.digits:
            base += "." + f"{moment.microsecond:06d}000"[: self.digits]
        return base + ("Z" if self.zone == "Z" else "+00:00")

    # Routing

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.tick:
            self.clock.now += self.tick
        url = request.url
        where = f"{url.scheme}://{url.netloc.decode()}{url.path}"
        params = dict(url.params)
        endpoint, file_id = self._endpoint(url, where, params)
        record = SimpleNamespace(
            at=self.clock(),
            gen=self.gen,
            method=request.method,
            url=str(url),
            where=where,
            endpoint=endpoint,
            params=params,
            multi=[key for key, _ in url.params.multi_items()],
            file=file_id,
            auth=request.headers.get("authorization"),
            content_type=request.headers.get("content-type", ""),
            form=None,
            header=None,
            claims=None,
            problem=None,
            status=None,
            folder=None,
            count=None,
        )
        self.requests.append(record)
        response = self._answer(request, record)
        record.status = response.status_code
        return response

    def _endpoint(self, url: httpx.URL, where: str, params: dict) -> tuple[str, str | None]:
        if url.path == "/token" and (where == TOKEN_URL or url.host in ("127.0.0.1", "localhost")):
            return "token", None
        if not where.startswith(self.base + "/files"):
            return "other", None
        rest = where[len(self.base) + len("/files") :]
        if rest == "":
            return "list", None
        parts = rest.split("/")[1:]
        if len(parts) == 1:
            return ("media" if params.get("alt") == "media" else "get"), parts[0]
        if len(parts) == 2 and parts[1] == "export":
            return "export", parts[0]
        return "other", None

    def _forced_for(self, endpoint: str) -> object:
        for index, (which, how) in enumerate(self.forced):
            if which == endpoint or (which == "content" and endpoint in ("export", "media")):
                del self.forced[index]
                return how
        if endpoint in ("export", "media") and "content" in self.always:
            return self.always["content"]
        return self.always.get(endpoint)

    def _answer(self, request: httpx.Request, record) -> httpx.Response:
        if record.endpoint == "token":
            return self._token(request, record)
        if record.endpoint == "other":
            return httpx.Response(404, text=CANARY)
        if request.method != "GET":
            return self._error(400, "badRequest")
        if not (
            self.valid is not None
            and record.auth == f"Bearer {self.valid[0]}"
            and self.clock() < self.valid[1]
        ):
            return self._error(401, "authError")
        how = self._forced_for(record.endpoint)
        if how is None and record.endpoint in ("export", "media") and self.content_limit:
            after, limited = self.content_limit
            served = [r for r in self.content_calls() if r.status == 200]
            if len(served) >= after:
                how = limited
        if how is not None:
            return self._failure(request, how)
        if record.endpoint == "list":
            return self._list(record)
        if record.endpoint == "get":
            return self._get(record)
        found = self.files.get(record.file or "")
        if found is None or found.trashed:
            return self._error(404, "notFound")
        shared = any(self.folders.get(p) for p in found.parents)
        if found.gone or (
            record.endpoint == "media"
            and shared
            and record.params.get("supportsAllDrives") != "true"
        ):
            return self._error(404, "notFound")
        if not found.can_download:
            return self._error(403, "cannotDownloadFile")
        if found.refused:
            return self._error(403, found.refused)
        if record.endpoint == "export":
            if found.mime != DOC:
                return self._error(403, "fileNotExportable")
            if record.params.get("mimeType") not in (MD, TXT):
                return self._error(400, "badRequest")
        elif found.mime in (DOC, SHEET, SLIDES, FOLDER_TYPE, SHORTCUT):
            return self._error(403, "fileNotDownloadable")
        body = found.content
        if found.echo == "token":
            body += f"\nThe server says {(record.auth or '').removeprefix('Bearer ')}\n".encode()
        elif found.echo == "refresh":
            body += f"\nThe server says {REFRESH_TOKEN}\n".encode()
        elif found.echo == "secret":
            body += f"\nThe server says {CLIENT_SECRET}\n".encode()
        kind = (
            record.params.get("mimeType", found.mime) if record.endpoint == "export" else found.mime
        )
        return httpx.Response(200, content=body, headers={"content-type": kind})

    # The token endpoint (as C4's stand-in)

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
        if problem:
            record.problem = problem
            return httpx.Response(
                400, json={"error": "invalid_request", "error_description": CANARY}
            )
        if self.refusal is not None:
            status, error = self.refusal
            return httpx.Response(status, json={"error": error, "error_description": CANARY})
        token = "ya29." + f"c7{len(self.issued) + 1:04d}" + "MadeUp" + "AccessToken"
        self.issued.append(token)
        self.valid = (token, self.clock() + HOUR)
        body = {"access_token": token, "expires_in": 3599, "scope": SCOPE, "token_type": "Bearer"}
        return httpx.Response(200, json=body)

    def _token_problem(self, request: httpx.Request, record) -> str | None:
        if request.method != "POST" or request.url.query:
            return "a token request is a POST without a query string"
        if not record.content_type.startswith("application/x-www-form-urlencoded"):
            return "a token request is a form"
        form = record.form
        if form.get("grant_type") == JWT_GRANT:
            if set(form) != {"grant_type", "assertion"}:
                return "a JWT grant has grant_type and assertion"
            return self._jwt_problem(form["assertion"], record)
        if form.get("grant_type") == "refresh_token":
            if set(form) != {"client_id", "client_secret", "grant_type", "refresh_token"}:
                return "a refresh has its four fields"
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
        signed = f"{segments[0]}.{segments[1]}".encode("ascii")
        try:
            self.keys.public.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            return "the signature does not verify with the service account's key"
        for claim, wanted in (("iss", CLIENT_EMAIL), ("scope", self.scope), ("aud", TOKEN_URL)):
            if claims.get(claim) != wanted:
                return f"{claim} is {wanted}"
        if "sub" in claims and claims["sub"] not in self.users:
            return "sub"
        return None

    # Answers

    def _error(self, code: int, reason: str | None = None) -> httpx.Response:
        reason = reason or DEFAULT_REASON.get(code, "backendError")
        if self.shape == "details":
            detail = {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": reason,
                "domain": "googleapis.com",
                "metadata": {"service": "drive.googleapis.com", "consumer": CANARY},
            }
            error = {
                "code": code,
                "message": f"{CANARY}: {reason}",
                "status": STATUS.get(code, "INTERNAL"),
                "details": [detail],
            }
        else:
            domain = "usageLimits" if "Limit" in reason else "global"
            item = {"domain": domain, "reason": reason, "message": f"{CANARY} {reason}"}
            error = {"errors": [item], "code": code, "message": f"{CANARY} {reason}"}
        return httpx.Response(code, json={"error": error})

    def _failure(self, request: httpx.Request, how: object) -> httpx.Response:
        if how == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if how == "not json":
            return httpx.Response(200, text=f"<html>{CANARY}</html>")
        if how == "array":
            return httpx.Response(200, json=[CANARY])
        if how == "wrong shape":
            return httpx.Response(200, json={"files": CANARY, "kind": "drive#fileList"})
        if how == "file without id":
            item = {"name": CANARY, "mimeType": DOC, "modifiedTime": self.ts(SINCE + HOUR)}
            return httpx.Response(200, json={"files": [item]})
        if how == "file without modifiedTime":
            item = {"id": fid(999), "name": CANARY, "mimeType": DOC}
            return httpx.Response(200, json={"files": [item]})
        if how == "429, no body":
            return httpx.Response(429)
        if isinstance(how, tuple):
            return self._error(*how)
        return self._error(int(how))  # type: ignore[call-overload]

    def _file(self, f: File) -> dict:
        body: dict = {
            "kind": "drive#file",
            "id": f.id,
            "name": f.name,
            "mimeType": f.mime,
            "parents": list(f.parents),
            "trashed": f.trashed,
            "createdTime": self.ts(min(f.modified, LONG_AGO)),
            "modifiedTime": self.ts(f.modified),
            "webViewLink": (
                f"https://docs.google.com/document/d/{f.id}/edit?usp=drivesdk"
                "&resourcekey=0-C7ResourceKey#heading=h.c7"
                if f.mime == DOC
                else f"https://drive.google.com/file/d/{f.id}/view?usp=drivesdk"
                "&resourcekey=0-C7ResourceKey"
            ),
            "capabilities": {"canDownload": f.can_download, "canEdit": False},
        }
        if f.size is not None:
            body["size"] = str(f.size)
        elif f.mime not in (DOC, FOLDER_TYPE, SHORTCUT) or (f.mime == DOC and self.docs_sized):
            body["size"] = str(len(f.content))
        if f.editor is not None:
            body["lastModifyingUser"] = {
                "kind": "drive#user",
                "displayName": f.editor,
                "emailAddress": PEOPLE.get(f.editor, "someone@example.test"),
                "me": False,
                "permissionId": "07" + hashlib.sha256(f.editor.encode()).hexdigest()[:8],
            }
        if f.target is not None:
            body["shortcutDetails"] = {"targetId": f.target, "targetMimeType": DOC}
        return body

    def _get(self, record) -> httpx.Response:
        """files.get without alt=media: a folder's or a file's metadata, as `fields` asks."""
        try:
            tree = selection(record.params.get("fields"))
        except ValueError:
            return self._error(400, "badRequest")
        all_drives = record.params.get("supportsAllDrives") == "true"
        if record.file in self.folders:
            if record.file in self.unshared or (self.folders[record.file] and not all_drives):
                return self._error(404, "notFound")
            body = {
                "kind": "drive#file",
                "id": record.file,
                "name": "Shared documents",
                "mimeType": FOLDER_TYPE,
                "trashed": record.file in self.binned,
                "capabilities": {
                    "canListChildren": record.file not in self.limited,
                    "canDownload": True,
                },
            }
        else:
            found = self.files.get(record.file or "")
            shared = found is not None and any(self.folders.get(p) for p in found.parents)
            if found is None or (shared and not all_drives):
                return self._error(404, "notFound")
            body = self._file(found)
            body["capabilities"]["canListChildren"] = False
        default = {"kind": True, "id": True, "name": True, "mimeType": True}
        return httpx.Response(200, json=pick(body, default if tree is None else tree))

    def _page_token(self, offset: int, params: dict) -> str:
        same = {k: v for k, v in params.items() if k != "pageToken"}
        state = json.dumps({"g": self.gen, "o": offset, "p": same}, sort_keys=True)
        return urlsafe_b64encode(state.encode()).decode()

    def _list(self, record) -> httpx.Response:
        params = record.params
        if len(record.multi) != len(set(record.multi)):
            return self._error(400, "badRequest")
        match = Q.fullmatch(params.get("q", ""))
        if match is None:
            return self._error(400, "badRequest")
        folder = record.folder = match.group(1)
        if folder not in self.folders or folder in self.unshared:
            return self._error(404, "notFound")
        try:
            tree = selection(params.get("fields"))
            size = int(params.get("pageSize", "100"))
        except ValueError:
            return self._error(400, "badRequest")
        if size < 1:
            return self._error(400, "badRequest")
        size = min(size, 1000)
        token = params.get("pageToken")
        offset = 0
        if token is not None:
            try:
                state = json.loads(urlsafe_b64decode(token.encode()))
            except ValueError:
                return self._error(400, "badRequest")
            same = {k: v for k, v in params.items() if k != "pageToken"}
            if state.get("g") != self.gen or state.get("p") != same:
                return self._error(400, "badRequest")
            offset = state["o"]
        flags = params.get("supportsAllDrives") == "true" and (
            params.get("includeItemsFromAllDrives") == "true"
        )
        children = [
            f
            for f in self.files.values()
            if folder in f.parents
            and not f.trashed
            and f.modified <= self.clock()
            and (self.folders[folder] is None or flags)
            and folder not in self.limited | self.binned
        ]
        children.sort(key=lambda f: f.id, reverse=True)
        if self.shuffle:
            children.sort(key=lambda f: hashlib.sha256(f"{self.gen}:{f.id}".encode()).hexdigest())
        if self.short_pages:
            size = min(size, 7) if token else 0
        page = children[offset : offset + size]
        record.count = len(page)
        default = {"kind": True, "id": True, "name": True, "mimeType": True}
        chosen = default if tree is None else True if "*" in tree else tree.get("files")
        items = [pick(self._file(f), chosen) for f in page] if chosen is not None else []
        body: dict = {"kind": "drive#fileList", "incompleteSearch": False}
        if items or not self.omit_empty:
            body["files"] = items
        if offset + size < len(children):
            body["nextPageToken"] = self._page_token(offset + size, params)
        if tree is not None:
            body = {k: v for k, v in body.items() if k in tree or "*" in tree}
        return httpx.Response(200, json=body)


LONG_NAMES = long_names()


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
    return KINDS["google_drive"]


@pytest.fixture
def fake(kind, keys):
    return FakeGoogle(Clock(NOW), keys)


def leaked(text: str | None, fake: FakeGoogle) -> list[str]:
    if not text:
        return []
    secrets = [*fake.keys.body, CLIENT_SECRET, REFRESH_TOKEN, *fake.issued]
    return [s for s in secrets if s in text or s in unquote(text)]


def config(cred: str = USER, **settings) -> dict:
    given: dict = {"credentials_env": CRED_ENV, "folder_id": FOLDER, "projects": ["documents"]}
    given.update(settings)
    return {key: value for key, value in given.items() if value is not None}


def opened(kind, fake, *, cred=USER, env=None, since=SINCE, deadline=None, **settings):
    checked = kind.check(config(cred, **settings))
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return kind.open(
        "drive", checked, http=http, env=fake.env(cred) if env is None else env,
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


def is_sorted(fake, pulled) -> bool:
    keys = [
        (fake.files[d.source_ref.split(":")[1].split("/")[0]].modified, d.source_ref)
        for d in pulled.drafts
    ]
    return keys == sorted(keys)


# The kind and its config


def test_the_kind_starts_off_and_its_config_has_these_defaults(kind):
    assert kind.starts_off is True and kind.via == "google-drive"
    assert kind.default_project == "documents"
    checked = kind.check({"credentials_env": CRED_ENV, "folder_id": FOLDER, "projects": []})
    assert (checked["credentials_env"], checked["folder_id"], checked["projects"]) == (
        CRED_ENV,
        FOLDER,
        [],
    )
    assert (checked["base_url"], checked["settle_hours"], checked["existing"]) == (
        DRIVE,
        24,
        False,
    )
    assert checked.get("subject") is None
    assert kind.check(checked) == checked

    given = {
        "credentials_env": CRED_ENV,
        "subject": SUBJECT,
        "folder_id": "a" * 100,
        "settle_hours": 720,
        "existing": True,
        "projects": ["documents"],
        "base_url": "https://drive.example.test/drive/v3",
    }
    kept = kind.check(given)
    for setting, value in given.items():
        assert kept[setting] == value, setting
    assert kind.check(kept) == kept
    for accepted in (
        {"settle_hours": 1},
        {"folder_id": "0Ab-_9xYz1"},
        {"existing": False},
        {"base_url": "http://127.0.0.1:8787/drive/v3"},
        {"base_url": "http://localhost:8787/drive/v3"},
    ):
        assert kind.check(config(**accepted))["projects"] == ["documents"], accepted


def test_describe_names_the_folder_and_whom_and_switch_on_says_what_it_reads(kind, fake):
    with_subject = kind.describe(kind.check(config(SA, subject=SUBJECT)))
    alone = kind.describe(kind.check(config(SA)))
    for said in (with_subject, alone):
        assert isinstance(said, str) and said.strip()
        assert FOLDER in said and not leaked(said, fake)
    assert SUBJECT in with_subject
    assert isinstance(kind.switch_on, str) and kind.switch_on.strip()
    assert "existing" in kind.switch_on
    assert kind.switching_on() == kind.switch_on


@pytest.mark.parametrize(
    ("setting", "settings"),
    [
        ("credentials_env", {"credentials_env": None}),
        ("credentials_env", {"credentials_env": 7}),
        ("credentials_env", {"credentials_env": ""}),
        ("subject", {"subject": "ada"}),
        ("subject", {"subject": "ada@example.test, brook@example.test"}),
        ("subject", {"subject": ["ada@example.test"]}),
        ("folder_id", {"folder_id": None}),
        ("folder_id", {"folder_id": "short"}),
        ("folder_id", {"folder_id": "a" * 101}),
        ("folder_id", {"folder_id": "1C7Folder AbCdEf"}),
        ("folder_id", {"folder_id": "1C7Folder.AbCdEf"}),
        ("folder_id", {"folder_id": 1234567890123}),
        ("settle_hours", {"settle_hours": 0}),
        ("settle_hours", {"settle_hours": 721}),
        ("settle_hours", {"settle_hours": "24"}),
        ("settle_hours", {"settle_hours": True}),
        ("settle_hours", {"settle_hours": 2.5}),
        ("existing", {"existing": "yes"}),
        ("existing", {"existing": 1}),
        ("projects", {"projects": ["documents", "planning"]}),
        ("projects", {"projects": "documents"}),
        ("base_url", {"base_url": "http://drive.example.test/drive/v3"}),
        ("base_url", {"base_url": "ftp://drive.example.test/drive/v3"}),
        ("base_url", {"base_url": 7}),
        ("color", {"color": "blue"}),
        ("scope", {"scope": SCOPE}),
        ("folders", {"folders": [FOLDER]}),
    ],
)
def test_check_refuses_a_bad_setting_by_its_name(kind, setting, settings):
    given = config(**settings)
    with pytest.raises(ValueError) as refused:
        kind.check(given)
    assert setting in str(refused.value)


@pytest.mark.parametrize(
    "address",
    [
        f"https://drive.google.com/drive/folders/{FOLDER}",
        f"https://drive.google.com/drive/u/0/folders/{FOLDER}?usp=sharing",
        f"drive.google.com/drive/folders/{FOLDER}",
    ],
)
def test_a_folder_address_instead_of_its_id_is_refused_asking_for_the_id(kind, address):
    with pytest.raises(ValueError) as refused:
        kind.check(config(folder_id=address))
    said = str(refused.value)
    assert "folder_id" in said and " id" in said.lower().replace("folder_id", "")


def test_check_refuses_a_config_that_is_not_an_object(kind):
    for odd in (None, [], "credentials_env"):
        with pytest.raises(ValueError):
            kind.check(odd)


def test_a_credential_pasted_into_the_config_is_refused_and_never_repeated(kind, fake, tmp_path):
    document = json.dumps(service_account(fake.keys))
    token = "ya29." + "c7" + "PastedAccessToken" + "0a1b2c3d"
    for setting, value in (
        ("credentials_env", document),
        ("credentials_env", fake.keys.pem),
        ("credentials_env", token),
        ("credentials_env", REFRESH_TOKEN),
        ("credentials_env", CLIENT_SECRET),
        ("subject", token),
        ("folder_id", token),
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
        store.create_project("documents")
        with pytest.raises(ValueError):
            store.add_source("drive", "google_drive", config(credentials_env=document))
        assert store.list_sources() == []
    finally:
        store.close()
    assert not leaked(dump(tmp_path / "cairn.db"), fake)


# Signing in


def test_a_refresh_token_signs_in_with_its_four_fields_and_reads_the_folder(kind, fake):
    fake.doc(1, SINCE + HOUR)
    fake.doc(2, SINCE + 2 * HOUR, name="Runbook.md", mime=MD, text="# Runbook\n\nStep one.\n")
    got = pull(fake, opened(kind, fake, cred=USER))
    assert refs(got) == [ref(1), ref(2)]
    (asked,) = fake.calls("token")
    assert (asked.method, asked.url) == ("POST", TOKEN_URL)
    assert asked.form == {
        "client_id": USER_CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    }
    drive = [r for r in fake.calls() if r.endpoint != "token"]
    assert drive and {r.auth for r in drive} == {f"Bearer {fake.issued[0]}"}
    assert all(r.url.startswith(DRIVE + "/files") for r in drive)


def test_drive_is_read_at_the_base_url_given_on_this_machine(kind, fake):
    fake.base = "http://127.0.0.1:8787/drive/v3"
    fake.doc(1, SINCE + HOUR)
    fake.doc(2, SINCE + 2 * HOUR, name="Spec.md", mime=MD, text="# Spec\n")
    got = pull(fake, opened(kind, fake, base_url=fake.base))
    assert refs(got) == [ref(1), ref(2)]
    drive = [r for r in fake.calls() if r.endpoint != "token"]
    assert drive and all(r.url.startswith(fake.base + "/files") for r in drive)
    assert not any(r.endpoint == "other" for r in fake.requests)


def test_a_service_account_with_a_subject_asks_for_that_persons_token(kind, fake):
    fake.doc(1, SINCE + HOUR)
    got = pull(fake, opened(kind, fake, cred=SA, subject=SUBJECT))
    assert refs(got) == [ref(1)]
    (asked,) = fake.calls("token")
    assert asked.problem is None and asked.form["grant_type"] == JWT_GRANT
    assert asked.claims["sub"] == SUBJECT and asked.claims["scope"] == SCOPE
    assert asked.claims["iss"] == CLIENT_EMAIL


def test_a_service_account_without_a_subject_acts_as_itself(kind, fake):
    fake.doc(1, SINCE + HOUR)
    got = pull(fake, opened(kind, fake, cred=SA))
    assert refs(got) == [ref(1)]
    (asked,) = fake.calls("token")
    assert asked.problem is None and asked.form["grant_type"] == JWT_GRANT
    assert "sub" not in asked.claims and asked.claims["scope"] == SCOPE


def test_a_refresh_token_with_a_subject_is_refused_when_opened(kind, fake):
    checked = kind.check(config(USER, subject=SUBJECT))
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    with pytest.raises(SourceError) as refused:
        kind.open(
            "drive", checked, http=http, env=fake.env(USER), clock=fake.clock,
            sleep=fake.sleep, deadline=None, since=SINCE,
        )  # fmt: skip
    said = str(refused.value)
    assert "subject" in said and not leaked(said, fake)
    assert fake.requests == []


@pytest.mark.parametrize("cred", [SA, USER])
def test_the_secrets_are_the_sources_and_never_in_a_url_a_log_or_the_cursor(
    kind, fake, cred, caplog
):
    caplog.set_level(logging.DEBUG)
    fake.doc(1, SINCE + HOUR)
    fake.doc(2, SINCE + 2 * HOUR, name="Ideas.txt", mime=TXT, text="Ideas for the ledger.\n")
    source = opened(kind, fake, cred=cred)
    kept = tuple(source.secrets)
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
        assert not {"access_token", "key", "token", "oauth_token"} & set(request.params)
    assert not leaked(caplog.text, fake)
    assert not leaked(got.cursor, fake)


# What counts as new


@pytest.mark.parametrize(
    ("digits", "zone"),
    [(3, "Z"), (0, "Z"), (9, "Z"), (3, "+00:00")],
    ids=["ms", "s", "ns", "offset"],
)
def test_the_first_pull_takes_stock_and_writes_what_changed_since_once_it_has_rested(
    kind, fake, digits, zone
):
    fake.digits, fake.zone = digits, zone
    fake.doc(1, SINCE - 2 * HOUR)  # there before the switch-on: not read
    fake.doc(2, SINCE + HOUR)  # changed since, rested
    fake.doc(3, NOW - 2 * HOUR)  # changed since, still settling
    fake.doc(4, SINCE)  # changed right at the switch-on
    fake.doc(5, NOW - DAY)  # rested just now
    read = reader(kind, fake)

    first = read()
    assert refs(first) == [ref(4), ref(2), ref(5)]
    assert first.skipped == [] and {d.project for d in first.drafts} == {"documents"}
    fake.clock.now = NOW + 12 * HOUR
    second = read(first.cursor)
    assert second.drafts == [] and second.skipped == []
    fake.clock.now = NOW + 22 * HOUR
    third = read(second.cursor)
    assert refs(third) == [ref(3)]
    fake.clock.now = NOW + 30 * HOUR
    assert read(third.cursor).drafts == []
    read_content = {r.file for r in fake.content_calls(every=True)}
    assert fid(1) not in read_content


def test_a_document_still_being_edited_is_written_once_it_rests(kind, fake):
    fake.doc(1, SINCE + HOUR)
    read = reader(kind, fake, settle_hours=6)
    cursor, written = None, []
    for step in range(12):  # a pull every two hours; edits until the fourth
        fake.clock.now = NOW + step * 2 * HOUR
        if step <= 4:
            fake.edit(1, fake.clock.now - 30 * MINUTE, f"Version {step} of the plan.\n")
        got = read(cursor)
        cursor = got.cursor
        written += [(step, d.source_ref, d.text) for d in got.drafts]
    ((step, found, text),) = written
    assert (step, found) == (7, ref(1))  # the last edit at step 4, six hours of rest
    assert "Version 4 of the plan." in text


def test_with_existing_the_documents_already_there_are_written_too(kind, fake):
    fake.doc(1, LONG_AGO)
    fake.doc(2, SINCE - HOUR)
    fake.doc(3, SINCE + HOUR)
    fake.doc(4, NOW - HOUR)  # still settling
    read = reader(kind, fake, existing=True)
    first = read()
    assert refs(first) == [ref(1), ref(2), ref(3)]
    fake.clock.now = NOW + DAY
    assert refs(read(first.cursor)) == [ref(4)]


def test_an_old_document_moved_into_the_folder_is_written_by_the_next_pull(kind, fake):
    """Whether a move changes `modifiedTime` is not stated in the notes, so the stand-in keeps it
    as it was: a document the folder did not list at the stock-taking is new to it, however
    old."""
    fake.doc(1, SINCE + HOUR)
    old = fake.doc(2, LONG_AGO, parents=[OTHER])
    read = reader(kind, fake)
    first = read()
    assert refs(first) == [ref(1)]
    old.parents = [FOLDER]  # moved in: nothing else about it changes
    fake.clock.now = NOW + HOUR
    second = read(first.cursor)
    assert refs(second) == [ref(2)]
    fake.clock.now = NOW + 2 * HOUR
    assert read(second.cursor).drafts == []


def test_a_document_there_at_stock_taking_and_edited_later_is_written_once_it_rests(kind, fake):
    fake.doc(1, SINCE - DAY)
    fake.doc(2, LONG_AGO)
    read = reader(kind, fake)
    first = read()
    assert first.drafts == []
    fake.edit(1, NOW + HOUR, "The settlement window is now two days.\n")
    fake.clock.now = NOW + 2 * HOUR
    second = read(first.cursor)
    assert second.drafts == []
    fake.clock.now = NOW + 26 * HOUR
    third = read(second.cursor)
    assert refs(third) == [ref(1)]
    assert "The settlement window is now two days." in third.drafts[0].text
    fake.clock.now = NOW + 30 * HOUR
    assert read(third.cursor).drafts == []


def test_switching_off_and_on_takes_stock_again(kind, fake):
    fake.doc(1, SINCE - DAY)  # there at the first stock-taking
    fake.doc(2, SINCE + HOUR)
    first = pull(fake, opened(kind, fake, since=SINCE))
    assert refs(first) == [ref(2)]

    # Off from NOW to `again`: what arrives or changes meanwhile is not read.
    fake.doc(3, NOW + HOUR)  # arrives while off
    fake.edit(2, NOW + 2 * HOUR)  # written already, edited while off
    fake.edit(1, NOW + 3 * HOUR)  # there before, edited while off
    fake.doc(4, LONG_AGO, parents=[OTHER])
    fake.files[fid(4)].parents = [FOLDER]  # moved in while off
    again = NOW + 2 * DAY
    fake.clock.now = again + DAY
    second = pull(fake, opened(kind, fake, since=again), first.cursor)
    assert second.drafts == [] and second.skipped == []

    fake.doc(5, again + 2 * HOUR)  # arrives after
    fake.doc(6, LONG_AGO, parents=[OTHER]).parents = [FOLDER]  # moved in after
    fake.edit(1, again + 3 * HOUR, "Edited after it was switched on again.\n")
    fake.clock.now = again + 2 * DAY
    third = pull(fake, opened(kind, fake, since=again), second.cursor)
    assert sorted(refs(third)) == sorted([ref(1), ref(5), ref(6)])
    fake.clock.now = again + 3 * DAY
    assert pull(fake, opened(kind, fake, since=again), third.cursor).drafts == []


# What it passes over


def test_other_types_subfolders_shortcuts_and_trashed_files_are_passed_over_silently(kind, fake):
    fake.doc(1, SINCE + HOUR)
    fake.doc(2, SINCE + HOUR, name="Notes.md", mime=MD, text="# Notes\n\nPlain notes.\n")
    fake.doc(3, SINCE + HOUR, name="Budget", mime=SHEET)
    fake.doc(4, SINCE + HOUR, name="Pitch", mime=SLIDES)
    fake.doc(5, SINCE + HOUR, name="Contract.pdf", mime=PDF, text=b"%PDF-1.7 c7")
    fake.doc(6, SINCE + HOUR, name="Diagram.png", mime=PNG, text=b"\x89PNG c7")
    fake.doc(7, SINCE + HOUR, name="Archive", mime=FOLDER_TYPE, text="")
    fake.folders[fid(7)] = None
    fake.doc(8, SINCE + HOUR, parents=[fid(7)])  # a Doc in the subfolder
    fake.doc(9, SINCE + HOUR, parents=[OTHER])  # a Doc elsewhere ...
    fake.doc(10, SINCE + HOUR, name="Shortcut to 9", mime=SHORTCUT, text="", target=fid(9))
    fake.doc(11, SINCE + HOUR).trashed = True
    fake.doc(12, SINCE + HOUR, name="Data.csv", mime="text/csv", text="a,b\n1,2\n")

    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(1), ref(2)] and got.skipped == []
    assert {r.file for r in fake.content_calls()} == {fid(1), fid(2)}
    assert {r.folder for r in fake.calls("list")} == {FOLDER}
    assert not [r for r in fake.calls() if r.file in (fid(8), fid(9))]  # nor the shortcut's


@pytest.mark.parametrize("docs_sized", [False, True], ids=["docs-without-size", "docs-sized"])
def test_a_document_that_may_not_be_downloaded_or_is_over_10_mb_is_skipped_once_unread(
    kind, fake, docs_sized
):
    fake.docs_sized = docs_sized
    fake.doc(1, SINCE + HOUR).can_download = False
    fake.doc(2, SINCE + HOUR, name="Log.txt", mime=TXT, text="big\n", size=12_000_000)
    fake.doc(3, SINCE + HOUR, name="Small.txt", mime=TXT, text="Just under.\n", size=9_000_000)
    fake.doc(4, SINCE + HOUR, name="Locked.md", mime=MD, text="# Locked\n", can_download=False)
    fake.doc(5, SINCE + 2 * HOUR)
    big = fake.doc(6, SINCE + 3 * HOUR, "A Doc whose stored size is large.\n", size=12_000_000)
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(3), ref(5), ref(6)]  # a Doc is exported whatever its size
    assert "A Doc whose stored size is large." in got.drafts[-1].text and big.mime == DOC
    reasons = {skip.ref: skip.reason for skip in got.skipped}
    assert set(reasons) == {fid(1), fid(2), fid(4)}
    assert all(reason.strip() for reason in reasons.values())
    assert "too large" in reasons[fid(2)].lower()
    assert {r.file for r in fake.content_calls()} == {fid(3), fid(5), fid(6)}
    fake.clock.now += HOUR
    again = read(got.cursor)
    assert again.drafts == [] and again.skipped == []


# The requests


def test_every_listing_asks_for_the_folders_children_with_the_documented_parameters(kind, fake):
    fake.doc(1, SINCE + HOUR)
    fake.doc(2, SINCE + 2 * HOUR, name="Plan.md", mime=MD, text="# Plan\n\nShip it.\n")
    fake.doc(3, SINCE + 3 * HOUR, name="Todo.txt", mime=TXT, text="Write the guide.\n")
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(1), ref(2), ref(3)]
    (listing,) = fake.calls("list")
    params = listing.params
    assert params["q"] == f"'{FOLDER}' in parents and trashed = false"
    assert params["pageSize"] == "1000"
    assert params["supportsAllDrives"] == "true"
    assert params["includeItemsFromAllDrives"] == "true"
    tree = selection(params.get("fields"))
    for path in NEEDED:
        assert selects(tree, path), path
    (export,) = fake.calls("export")
    assert export.file == fid(1) and export.params["mimeType"] == MD
    media = fake.calls("media")
    assert {r.file for r in media} == {fid(2), fid(3)}
    assert all(r.params["alt"] == "media" for r in media)
    assert all(r.method == "GET" for r in fake.calls() if r.endpoint != "token")


def test_2300_files_over_three_pages_a_short_page_and_any_order_give_the_same_drafts(kind, keys):
    def stocked(mode: str) -> FakeGoogle:
        stand_in = FakeGoogle(Clock(NOW), keys)
        stand_in.short_pages = stand_in.omit_empty = mode == "short pages"
        stand_in.shuffle = mode == "shuffled"
        stand_in.digits = {"documented": 3, "short pages": 9, "shuffled": 0}[mode]
        for n in range(2_300):
            if n % 230 == 7:
                stand_in.doc(n, SINCE + n * MINUTE)
            else:
                stand_in.doc(n, SINCE + n * MINUTE, name=f"Sheet {n}", mime=SHEET)
        return stand_in

    seen = {}
    for mode in ("documented", "short pages", "shuffled"):
        stand_in = stocked(mode)
        got = pull(stand_in, opened(kind, stand_in))
        assert refs(got) == [ref(n) for n in range(2_300) if n % 230 == 7], mode
        seen[mode] = [(d.source_ref, d.title, d.topic, d.text, d.project) for d in got.drafts]
        lists = stand_in.calls("list")
        assert all(r.status == 200 for r in lists), mode
        assert "pageToken" not in lists[0].params
        assert all("pageToken" in r.params for r in lists[1:])
        same = [{k: v for k, v in r.params.items() if k != "pageToken"} for r in lists]
        assert all(s == same[0] for s in same), mode
        if mode == "documented":
            assert [r.count for r in lists] == [1000, 1000, 300]
    assert seen["documented"] == seen["short pages"] == seen["shuffled"]


def test_a_folder_in_a_shared_drive_is_read_the_same_way(kind, fake):
    fake.doc(1, SINCE + HOUR, parents=[SHARED])
    fake.doc(2, SINCE + 2 * HOUR, parents=[SHARED], name="Spec.md", mime=MD, text="# Spec\n")
    got = pull(fake, opened(kind, fake, folder_id=SHARED))
    assert refs(got) == [ref(1), ref(2)] and got.skipped == []


@pytest.mark.parametrize("existing", [False, True], ids=["changed-since", "existing"])
def test_sixty_documents_are_written_over_three_pulls_of_at_most_25(kind, fake, existing):
    start = LONG_AGO if existing else SINCE + HOUR
    for n in range(60):
        fake.doc(n, start + (n * 7 % 60) * MINUTE)
    read = reader(kind, fake, existing=existing)
    cursor, counts, written = None, [], []
    for step in range(4):
        fake.clock.now = NOW + step * HOUR
        got = read(cursor)
        cursor = got.cursor
        assert len(fake.content_calls()) <= 25
        assert is_sorted(fake, got)
        counts.append(len(got.drafts))
        written += refs(got)
    assert counts == [25, 25, 10, 0]
    assert sorted(written) == sorted(ref(n) for n in range(60))
    assert len(set(written)) == 60


# The draft


def test_the_draft_says_what_the_document_is_and_holds_its_content_unchanged(kind, fake):
    content = (
        "# Ledger v2\n\n"
        "The ledger moves to *daily* settlement.\n\n"
        "- Owner: payments (write to ops@example.test)\n"
        "- Status: agreed\n\n"
        "| Step | When |\n|---|---|\n| Freeze | Monday |\n\n"
        "> Quoted from the review.\n"
    )
    when = datetime(2026, 11, 12, 9, 30, 41, tzinfo=UTC)
    fake.doc(1, when, content, name="Design review: ledger v2", editor="Brook Lane")
    fake.doc(2, when + HOUR, "Nobody signed in.\n", name="Unsigned", editor=None)
    got = pull(fake, opened(kind, fake, since=when - HOUR))
    assert refs(got) == [ref(1), ref(2)]
    draft, unsigned = got.drafts

    assert draft.title == draft.topic == "Design review: ledger v2"
    assert (draft.label, draft.project, draft.source_ref) == ("google-drive", "documents", ref(1))
    text = draft.text
    assert re.fullmatch(r"#{1,6} " + re.escape(draft.title), text.splitlines()[0])
    assert content.strip() in text
    facts = text[: text.index("# Ledger v2")]
    assert "2026-11-12 09:30" in facts and "Brook Lane" in facts
    assert f"https://docs.google.com/document/d/{fid(1)}/edit" in facts
    assert "resourcekey" not in text.lower() and "usp=" not in text and "#heading" not in text
    assert "@" not in facts and "example.test" not in facts

    assert unsigned.title == unsigned.topic == "Unsigned"
    assert "Nobody signed in." in unsigned.text
    assert "Ada Park" not in unsigned.text and "@" not in unsigned.text


@pytest.mark.parametrize(
    ("name", "mime", "title"),
    [
        ("Runbook.md", MD, "Runbook"),
        ("Ideas.txt", TXT, "Ideas"),
        ("Changelog", TXT, "Changelog"),
        ("release.notes.md", MD, "release.notes"),
        ("Plan v2", DOC, "Plan v2"),
    ],
)
def test_a_md_or_txt_file_is_titled_without_its_extension(kind, fake, name, mime, title):
    fake.doc(1, SINCE + HOUR, "Some text.\n", name=name, mime=mime)
    (draft,) = pull(fake, opened(kind, fake)).drafts
    assert draft.title == draft.topic == title
    assert re.fullmatch(r"#{1,6} " + re.escape(title), draft.text.splitlines()[0])


def test_bytes_are_decoded_as_utf8_with_replacement_and_a_bom_dropped(kind, fake):
    fake.doc(1, SINCE + HOUR, b"\xef\xbb\xbfCaf\xc3\xa9 opens at nine.\n", name="a.md", mime=MD)
    fake.doc(2, SINCE + 2 * HOUR, b"caf\xe9 au lait, \xff\xfe twice.\n", name="b.txt", mime=TXT)
    fake.doc(3, SINCE + 3 * HOUR, "﻿The Doc's own text.\n")
    first, second, third = pull(fake, opened(kind, fake)).drafts
    assert "Café opens at nine." in first.text and "﻿" not in first.text
    assert "caf� au lait," in second.text and "twice." in second.text
    assert "The Doc's own text." in third.text and "﻿" not in third.text


def test_embedded_data_images_are_dropped_and_their_alt_text_kept(kind, fake):
    blob = urlsafe_b64encode(hashlib.sha256(b"c7").digest() * 32_768).decode()  # 1.4 MB
    blob = (blob * 2)[:2_000_000]
    content = (
        "# Launch plan\n\n"
        "The opening paragraph.\n\n"
        "![][image1]\n\n"
        f"The chart: ![Revenue by quarter](data:image/png;base64,{blob}) and the rest.\n\n"
        "The closing paragraph.\n\n"
        f"[image1]: <data:image/png;base64,{blob}>\n"
    )
    fake.doc(1, SINCE + HOUR, content, name="Launch plan")
    (draft,) = pull(fake, opened(kind, fake)).drafts
    text = draft.text
    assert "data:" not in text and blob[:64] not in text
    assert len(text) < 10_000
    assert "Revenue by quarter" in text
    for kept in ("The opening paragraph.", "The closing paragraph.", "and the rest."):
        assert kept in text


def test_a_document_of_3200_paragraphs_is_written_in_three_parts_cut_before_a_heading(kind, fake):
    lines = ["## Overview", ""]
    for k, said in enumerate(paragraphs(3_200)):
        if k == 200:
            lines += ["## Background", ""]  # early in a part: no cut here
        if k == 1_300:
            lines += ["## Settlement", ""]  # in the first part's last third
        lines += [said, ""]
    fake.doc(1, SINCE + HOUR, "\n".join(lines), name="Ledger design")
    got = pull(fake, opened(kind, fake))
    parts = got.drafts
    assert refs(got) == [ref(1), ref(1, 2), ref(1, 3)]
    assert [d.topic for d in parts] == [
        "Ledger design",
        "Ledger design, part 2",
        "Ledger design, part 3",
    ]
    assert all((d.label, d.project) == ("google-drive", "documents") for d in parts)
    numbers = [held(d.text) for d in parts]
    assert [k for part in numbers for k in part] == list(
        range(3_200)
    )  # every paragraph once, in order
    assert numbers[0] == list(range(1_300))  # the first part ends just before the heading
    second = parts[1].text
    assert second.index("## Settlement") < second.index("Paragraph 01300 ")
    assert "## Settlement" not in parts[0].text and "## Background" in parts[0].text
    assert all(len(n) <= 1_500 for n in numbers)


def test_a_long_name_is_cut_and_tagged_from_its_id_and_a_name_that_fits_is_the_topic(kind, fake):
    for n, name in enumerate(LONG_NAMES, 2):  # changed at the same second
        fake.doc(n, SINCE + HOUR, "\n\n".join(paragraphs(1_700)), name=name)
    fits = "Settlement design notes for the ledger migration and v22"  # a slug of 56
    assert len(raw_slug(fits)) == 56
    fake.doc(4, SINCE + 2 * HOUR, "\n\n".join(paragraphs(1_700)), name=fits)
    over = "Settlement design notes for the ledger migration and the v2 rollout plan"
    fake.doc(5, SINCE + 3 * HOUR, name=over)
    fake.doc(6, SINCE + 4 * HOUR, name="Plan")
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(2), ref(2, 2), ref(3), ref(3, 2), ref(4), ref(4, 2), ref(5), ref(6)]
    by_ref = {d.source_ref: d for d in got.drafts}

    tags = {}
    for n, name in ((2, LONG_NAMES[0]), (3, LONG_NAMES[1]), (5, over)):
        parts = [by_ref[r] for r in (ref(n), ref(n, 2)) if r in by_ref]
        for number, draft in enumerate(parts, 1):
            assert draft.title == name
            shape = TAGGED.fullmatch(draft.topic)
            assert shape is not None, draft.topic
            assert name.startswith(shape["cut"]) and shape["cut"] != name
            assert shape["part"] == (str(number) if number > 1 else None), draft.topic
            assert draft.topic.endswith(f", part {number}" if number > 1 else shape["tag"])
            assert len(raw_slug(draft.topic)) <= 64, draft.topic
            assert shape["tag"] not in fid(n) and fid(n) not in draft.topic
            tags.setdefault(n, set()).add(shape["tag"])
    assert all(len(found) == 1 for found in tags.values())  # one tag for a document's parts
    assert len({found.pop() for found in tags.values()}) == 3
    assert [by_ref[ref(4)].topic, by_ref[ref(4, 2)].topic] == [fits, f"{fits}, part 2"]
    assert by_ref[ref(6)].topic == "Plan"


def test_a_document_too_long_or_empty_is_skipped_once(kind, fake):
    fake.doc(1, SINCE + HOUR, "\n\n".join(f"Line {k}." for k in range(31_000)), name="Huge")
    fake.doc(2, SINCE + 2 * HOUR, "  \n\n\t\n", name="Blank")
    fake.doc(3, SINCE + 3 * HOUR, b"", name="Nothing.md", mime=MD)
    fake.doc(4, SINCE + 4 * HOUR)
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(4)]
    reasons = {skip.ref: skip.reason.lower() for skip in got.skipped}
    assert set(reasons) == {fid(1), fid(2), fid(3)}
    assert "too long" in reasons[fid(1)]
    assert "empty" in reasons[fid(2)] and "empty" in reasons[fid(3)]
    fake.clock.now += HOUR
    again = read(got.cursor)
    assert again.drafts == [] and again.skipped == []


# Read once, and the cursor


def test_a_document_is_read_once_whatever_happens_to_it_later(kind, fake):
    for n in range(1, 6):
        fake.doc(n, SINCE + n * HOUR)
    read = reader(kind, fake)
    first = read()
    assert refs(first) == [ref(n) for n in range(1, 6)]
    fake.edit(1, NOW + HOUR, "A later edit.\n")
    fake.files[fid(2)].name = "Renamed"
    fake.files[fid(2)].modified = NOW + HOUR
    fake.files[fid(3)].parents = [OTHER]
    fake.files[fid(4)].trashed = True
    fake.files[fid(5)].parents = [FOLDER, OTHER]
    fake.clock.now = NOW + 3 * DAY
    again = read(first.cursor)
    assert again.drafts == [] and again.skipped == []
    fake.clock.now = NOW + 4 * DAY
    assert read(again.cursor).drafts == []


def test_the_cursor_holds_no_secret_id_name_or_text_and_shrinks_when_documents_leave(kind, fake):
    for n in range(200):
        fake.doc(
            n, SINCE + n * MINUTE, f"Body {n:05d} by ops@example.test.\n", name=f"Memo {n:05d}"
        )
    for n in range(200, 260):
        fake.doc(n, SINCE - DAY, name=f"Older memo {n:05d}")  # there at the stock-taking
    read = reader(kind, fake, cred=SA, subject=SUBJECT)
    cursor, written = None, []
    for step in range(10):
        fake.clock.now = NOW + step * HOUR
        got = read(cursor)
        cursor = got.cursor
        written += refs(got)
        assert isinstance(json.loads(cursor), dict | list)
        assert not leaked(cursor, fake) and "@" not in cursor
        for n in range(260):
            assert fid(n) not in cursor and f"emo {n:05d}" not in cursor
        for words in ("Ada Park", "Body ", "Memo", "ledger"):
            assert words not in cursor
    assert sorted(written) == sorted(ref(n) for n in range(200))
    full = len(cursor)

    for n in range(30, 260):
        fake.files[fid(n)].parents = [OTHER]  # most of the folder moves out
    fake.clock.now = NOW + DAY
    got = read(cursor)
    assert got.drafts == []
    assert len(got.cursor) < full / 3
    for n in range(30):
        fake.edit(n, NOW + DAY - 2 * DAY)  # still known: an edit long settled is not read
    fake.clock.now = NOW + 2 * DAY
    assert read(got.cursor).drafts == []


# When Google says no


@pytest.mark.parametrize("shape", ["errors", "details"])
@pytest.mark.parametrize(
    "how",
    [401, (403, "insufficientPermissions"), (403, "domainPolicy"), (403, "forbidden"), 403],
)
def test_access_refused_on_the_listing_says_what_to_check_and_is_not_retried(
    kind, fake, how, shape
):
    fake.shape = shape
    fake.doc(1, SINCE + HOUR)
    fake.always["list"] = how
    said = str(failed_pull(fake, opened(kind, fake)))
    assert "scope" in said.lower() and "shar" in said.lower()
    assert CANARY not in said and not leaked(said, fake)
    assert fake.slept == [] and len(fake.calls("list")) == 1


@pytest.mark.parametrize("shape", ["errors", "details"])
def test_a_folder_not_found_names_folder_id(kind, fake, shape):
    fake.shape = shape
    missing = "1C7Missing" + "FolderZzYyXxWw"
    said = str(failed_pull(fake, opened(kind, fake, folder_id=missing)))
    assert "folder_id" in said and CANARY not in said and not leaked(said, fake)
    fake.always["list"] = 404
    said = str(failed_pull(fake, opened(kind, fake)))
    assert "folder_id" in said and CANARY not in said
    assert fake.slept == []


def test_every_pull_first_asks_for_the_folder_itself(kind, fake):
    fake.doc(1, SINCE + HOUR)
    read = reader(kind, fake)
    cursor, written = None, []
    for step in range(2):
        fake.clock.now = NOW + step * HOUR
        got = read(cursor)
        cursor = got.cursor
        written += refs(got)
        drive = [r for r in fake.calls() if r.endpoint != "token"]
        first = drive[0]
        assert (first.endpoint, first.file) == ("get", FOLDER)
        assert first.params.get("supportsAllDrives") == "true"
        tree = selection(first.params.get("fields"))
        for path in ("id", "mimeType", "trashed", "capabilities/canListChildren"):
            assert selects(tree, path), path
        assert fake.calls("list")
    assert written == [ref(1)]


@pytest.mark.parametrize("case", ["unknown", "not shared", "not a folder", "trashed", "limited"])
def test_a_folder_the_account_cannot_use_is_an_error_naming_folder_id(kind, fake, case):
    fake.doc(1, SINCE + HOUR)
    folder = FOLDER
    if case == "unknown":
        folder = "1C7Missing" + "FolderZzYyXxWw"
    elif case == "not shared":
        fake.unshared.add(FOLDER)
    elif case == "not a folder":
        folder = fid(1)
    elif case == "trashed":
        fake.binned.add(FOLDER)
    else:
        fake.limited.add(FOLDER)  # Google would list it as empty
    said = str(failed_pull(fake, opened(kind, fake, folder_id=folder)))
    assert "folder_id" in said and CANARY not in said and not leaked(said, fake)
    if case in ("unknown", "not shared", "limited"):
        assert "shar" in said.lower()
    if case == "trashed":
        assert "trash" in said.lower()
    assert not fake.calls("list") and not fake.content_calls()
    assert fake.slept == []


@pytest.mark.parametrize("shape", ["errors", "details"])
@pytest.mark.parametrize(
    "how",
    [(429, "rateLimitExceeded"), (403, "rateLimitExceeded"), (403, "userRateLimitExceeded"), 429],
)
def test_rate_limits_are_retried_with_the_documented_backoff_through_sleep(kind, fake, how, shape):
    fake.shape = shape
    fake.doc(1, SINCE + HOUR)
    fake.forced = [("list", how), ("list", how), ("content", how)]
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [ref(1)]
    assert len(fake.slept) == 3
    backed_off(fake.slept[:2])

    fake.slept.clear()
    fake.always["list"] = how
    said = str(failed_pull(fake, opened(kind, fake)))
    assert 1 <= len(fake.slept) <= 4
    backed_off(fake.slept)
    assert len(fake.calls("list")) == len(fake.slept) + 1
    assert CANARY not in said and not leaked(said, fake)


def test_429_without_a_body_is_a_rate_limit_too(kind, fake):
    fake.doc(1, SINCE + HOUR)
    fake.forced = [("list", "429, no body")]
    assert refs(pull(fake, opened(kind, fake))) == [ref(1)]
    assert len(fake.slept) == 1


@pytest.mark.parametrize("shape", ["errors", "details"])
def test_the_daily_limit_is_not_retried(kind, fake, shape):
    fake.shape = shape
    fake.doc(1, SINCE + HOUR)
    fake.always["list"] = (403, "dailyLimitExceeded")
    said = str(failed_pull(fake, opened(kind, fake)))
    assert fake.slept == [] and len(fake.calls("list")) == 1
    assert CANARY not in said and not leaked(said, fake)


@pytest.mark.parametrize("shape", ["errors", "details"])
def test_a_document_gone_or_refused_on_its_content_is_skipped_once_and_the_pull_goes_on(
    kind, fake, shape
):
    fake.shape = shape
    fake.doc(1, SINCE + HOUR).gone = True
    fake.doc(2, SINCE + 2 * HOUR).refused = "exportSizeLimitExceeded"
    fake.doc(3, SINCE + 3 * HOUR, name="Locked.txt", mime=TXT).refused = "cannotDownloadFile"
    fake.doc(4, SINCE + 4 * HOUR).refused = "forbidden"
    fake.doc(5, SINCE + 5 * HOUR)
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(5)]
    reasons = {skip.ref: skip.reason for skip in got.skipped}
    assert set(reasons) == {fid(1), fid(2), fid(3), fid(4)}
    assert "gone" in reasons[fid(1)].lower()
    for reason in reasons.values():
        assert reason.strip() and CANARY not in reason
        assert "exportSizeLimitExceeded" not in reason and "cannotDownloadFile" not in reason
    assert fake.slept == []
    fake.clock.now += HOUR
    again = read(got.cursor)
    assert again.drafts == [] and again.skipped == []


SKIPPED_403 = [
    "fileNotDownloadable",
    "fileNotExportable",
    "download_restricted_for_revision",
    "cannotDownloadFile",
    "exportSizeLimitExceeded",
    "appNotAuthorizedToFile",
    "teamDriveMembershipRequired",
    "c7ReasonNobodyDocuments",
]


@pytest.mark.parametrize("shape", ["errors", "details"])
@pytest.mark.parametrize("reason", SKIPPED_403)
def test_a_403_on_one_documents_content_skips_that_document_once(kind, fake, reason, shape):
    fake.shape = shape
    fake.doc(1, SINCE + HOUR)
    fake.doc(2, SINCE + 2 * HOUR, name="Two.md", mime=MD, text="# Two\n").refused = reason
    fake.doc(3, SINCE + 3 * HOUR).refused = reason
    fake.doc(4, SINCE + 4 * HOUR)
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(1), ref(4)]
    assert {skip.ref for skip in got.skipped} == {fid(2), fid(3)}
    for skip in got.skipped:
        assert skip.reason.strip() and CANARY not in skip.reason and reason not in skip.reason
    assert fake.slept == []
    fake.clock.now += HOUR
    again = read(got.cursor)
    assert again.drafts == [] and again.skipped == []


@pytest.mark.parametrize(
    ("shape", "how"),
    [
        ("errors", (403, "domainPolicy")),
        ("details", (403, "domainPolicy")),
        ("errors", (403, "insufficientPermissions")),
        ("details", (403, "insufficientPermissions")),
        ("details", (403, "ACCESS_TOKEN_SCOPE_INSUFFICIENT")),
        ("errors", 401),
        ("details", 401),
    ],
)
def test_a_refusal_of_the_whole_token_on_content_fails_the_pull(kind, fake, shape, how):
    fake.shape = shape
    fake.doc(1, SINCE + HOUR)
    fake.doc(2, SINCE + 2 * HOUR)
    fake.forced = [("content", how)]
    said = str(failed_pull(fake, opened(kind, fake)))
    assert "scope" in said.lower()
    assert CANARY not in said and not leaked(said, fake)
    assert fake.slept == [] and len(fake.content_calls()) == 1


def test_a_rate_limit_at_the_first_document_fails_the_pull(kind, fake):
    for n in range(1, 4):
        fake.doc(n, SINCE + n * HOUR)
    fake.always["content"] = (429, "rateLimitExceeded")
    said = str(failed_pull(fake, opened(kind, fake)))
    assert CANARY not in said and not leaked(said, fake)
    assert 1 <= len(fake.slept) <= 4


@pytest.mark.parametrize(
    "how",
    [(429, "rateLimitExceeded"), (403, "userRateLimitExceeded"), (403, "dailyLimitExceeded")],
)
@pytest.mark.parametrize("shape", ["errors", "details"])
def test_a_rate_limit_after_ten_documents_returns_them_and_the_next_pull_writes_the_rest(
    kind, fake, how, shape
):
    fake.shape = shape
    for n in range(1, 16):
        fake.doc(n, SINCE + n * HOUR)
    fake.content_limit = (10, how)
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [ref(n) for n in range(1, 11)] and got.skipped == []
    if how[1] == "dailyLimitExceeded":
        assert fake.slept == []
    else:
        assert 1 <= len(fake.slept) <= 4
    fake.content_limit = None
    fake.clock.now += HOUR
    rest = read(got.cursor)
    assert refs(rest) == [ref(n) for n in range(11, 16)]
    fake.clock.now += HOUR
    assert read(rest.cursor).drafts == []


@pytest.mark.parametrize(
    ("endpoint", "how"),
    [
        ("list", 500),
        ("list", 503),
        ("list", "timeout"),
        ("list", "not json"),
        ("list", "array"),
        ("list", "wrong shape"),
        ("list", "file without id"),
        ("list", "file without modifiedTime"),
        ("content", 500),
        ("content", "timeout"),
        ("token", 500),
        ("token", "timeout"),
    ],
)
def test_a_server_error_a_timeout_or_an_odd_listing_fails_the_pull(kind, fake, endpoint, how):
    fake.doc(1, SINCE + HOUR)
    fake.doc(2, SINCE + 2 * HOUR)
    fake.always[endpoint] = how
    said = str(failed_pull(fake, opened(kind, fake)))
    assert said.strip() and CANARY not in said and not leaked(said, fake)


# The deadline


def test_a_pull_cut_by_its_deadline_and_every_resume_point_go_on_without_loss_or_repeat(kind, fake):
    for n in range(1, 6):
        fake.doc(n, SINCE + n * HOUR)
    fake.doc(6, SINCE + 6 * HOUR, "\n\n".join(paragraphs(1_600)), name="Long one")
    fake.doc(7, SINCE + 7 * HOUR, name="After.md", mime=MD, text="# After\n\nThe end.\n")
    fake.doc(8, SINCE - DAY)  # there at the stock-taking: never read, resumed or not
    whole = pull(fake, opened(kind, fake))
    full = refs(whole)
    assert full == [ref(1), ref(2), ref(3), ref(4), ref(5), ref(6), ref(6, 2), ref(7)]
    assert all(draft.resume for draft in whole.drafts)
    for draft in whole.drafts:  # a resume is a cursor: nothing in it a cursor may not hold
        assert not leaked(draft.resume, fake) and "@" not in draft.resume
        for n in range(1, 9):
            assert fid(n) not in draft.resume and fake.files[fid(n)].name not in draft.resume
    for index, draft in enumerate(whole.drafts):
        fake.clock.now += HOUR
        rest = pull(fake, opened(kind, fake), draft.resume)
        assert refs(rest) == full[index + 1 :], index

    fake.clock.now += HOUR
    fake.tick = MINUTE  # every request takes a minute
    hurried = opened(kind, fake, deadline=fake.clock.now + 5 * MINUTE)
    cut = pull(fake, hurried)
    assert 0 < len(cut.drafts) < len(full)
    assert refs(cut) == full[: len(cut.drafts)]
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
    for name in ("documents", "planning"):
        store.create_project(name)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    yield SimpleNamespace(store=store, db=db, clock=clock, http=http)
    store.close()


def run(world, fake, at: datetime, cred: str = USER):
    fake.clock.now = at
    fake.new_pull()
    return asyncio.run(
        run_source(
            world.store, "drive", http=world.http, env=fake.env(cred), clock=fake.clock,
            sleep=fake.sleep,
        )
    )  # fmt: skip


def notes(world, project: str = "documents") -> list:
    return world.store.list_notes(world.store.get_project(project).id)


def pages(world, project: str = "documents") -> list:
    return world.store.list_pages(world.store.get_project(project).id)


def test_through_the_runner_a_source_is_off_until_switched_on_then_writes_each_document_once(
    world, fake
):
    fake.doc(1, SINCE - 2 * HOUR)
    fake.doc(2, SINCE + HOUR)
    fake.doc(3, SINCE + 2 * HOUR, name="Spec.md", mime=MD, text="# Spec\n\nThe spec.\n")
    fake.doc(4, NOW - 2 * HOUR)
    fake.doc(5, SINCE + 3 * HOUR, name="Budget", mime=SHEET)

    added = world.store.add_source("drive", "google_drive", config(projects=[]))
    assert added.enabled is False
    assert run(world, fake, NOW).off is True and fake.requests == []

    world.clock.now = SINCE
    world.store.enable_source("drive", projects=["documents"])
    report = run(world, fake, NOW)
    assert (report.saved, report.unchanged, report.refused, report.rejected) == (2, 0, 0, 0)
    assert report.skipped == 0
    held_now = notes(world)
    assert sorted(c.source_ref for c in held_now) == [ref(2), ref(3)]
    assert {(c.via, c.label) for c in held_now} == {("google-drive", "google-drive")}
    assert len(pages(world)) == 2 and notes(world, "planning") == []
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.detail["source"], event.detail["saved"]) == (
        "source.synced",
        "drive",
        2,
    )

    again = run(world, fake, NOW + 5 * MINUTE)
    assert (again.saved, again.unchanged, again.skipped) == (0, 0, 0)
    after = run(world, fake, NOW + DAY)  # the fourth has rested
    assert after.saved == 1 and len(pages(world)) == 3
    assert not leaked(dump(world.db), fake)
    for event in world.store.list_audit(limit=100):
        assert not leaked(json.dumps(event.detail), fake)


def test_through_the_runner_the_stock_taken_is_kept_and_taken_again_on_switch_on(world, fake):
    fake.doc(1, SINCE - DAY)
    fake.doc(2, LONG_AGO, parents=[OTHER])
    world.store.add_source("drive", "google_drive", config())
    world.clock.now = SINCE
    world.store.enable_source("drive")
    assert run(world, fake, NOW).saved == 0  # nothing new: but the stock is taken
    fake.files[fid(2)].parents = [FOLDER]  # an old document moved in
    assert run(world, fake, NOW + HOUR).saved == 1
    assert [c.source_ref for c in notes(world)] == [ref(2)]

    world.clock.now = NOW + 2 * HOUR
    world.store.disable_source("drive")
    fake.doc(3, NOW + 3 * HOUR)  # arrives while it is off
    fake.edit(1, NOW + 4 * HOUR)  # changed while it is off
    assert run(world, fake, NOW + DAY).off is True
    world.clock.now = NOW + 2 * DAY
    world.store.enable_source("drive")
    assert run(world, fake, NOW + 3 * DAY).saved == 0
    fake.doc(4, NOW + 2 * DAY + HOUR)  # arrives after
    assert run(world, fake, NOW + 4 * DAY).saved == 1
    assert sorted(c.source_ref for c in notes(world)) == [ref(2), ref(4)]


def test_through_the_runner_a_document_put_back_into_the_folder_is_chosen_again(world, fake):
    fake.doc(1, SINCE + HOUR, "The first version.\n")
    fake.doc(2, SINCE + 2 * HOUR, "The other one.\n")
    world.store.add_source("drive", "google_drive", config())
    world.clock.now = SINCE
    world.store.enable_source("drive")
    assert run(world, fake, NOW).saved == 2

    fake.files[fid(1)].parents = [OTHER]  # taken out ...
    assert run(world, fake, NOW + HOUR).saved == 0
    fake.files[fid(1)].parents = [FOLDER]  # ... and put back unchanged: the same note
    back = run(world, fake, NOW + 2 * HOUR)
    assert (back.saved, back.unchanged) == (0, 1)
    assert [c.source_ref for c in notes(world)].count(ref(1)) == 1

    fake.files[fid(1)].trashed = True  # trashed ...
    assert run(world, fake, NOW + 3 * HOUR).saved == 0
    fake.edit(1, NOW + 4 * HOUR, "The second version.\n")
    fake.files[fid(1)].trashed = False  # ... edited, and restored: read again once rested
    assert run(world, fake, NOW + 5 * HOUR).saved == 0
    again = run(world, fake, NOW + 4 * HOUR + DAY)
    assert again.saved == 1
    texts = [c.text for c in notes(world) if c.source_ref == ref(1)]
    assert len(texts) == 2 and any("The second version." in text for text in texts)
    assert run(world, fake, NOW + 2 * DAY).saved == 0


def test_through_the_runner_long_documents_fill_no_page_past_1500_claims_or_another_page(
    world, fake
):
    lines = []
    for k, said in enumerate(paragraphs(3_200)):
        if k == 1_300:
            lines += ["## Settlement", ""]
        lines += [said, ""]
    fake.doc(1, SINCE + HOUR, "\n".join(lines), name="Ledger design")
    for n, name in enumerate(LONG_NAMES, 2):  # changed at the same second
        fake.doc(n, SINCE + 2 * HOUR, "\n\n".join(paragraphs(1_700, word=f"Doc{n}")), name=name)
    world.store.add_source("drive", "google_drive", config())
    world.clock.now = SINCE
    world.store.enable_source("drive")

    report = run(world, fake, NOW)
    assert report.saved == 7 and report.skipped == 0 and report.malformed == 0
    stored = notes(world)
    assert sorted(c.source_ref for c in stored) == sorted(
        [ref(1), ref(1, 2), ref(1, 3), ref(2), ref(2, 2), ref(3), ref(3, 2)]
    )
    shown = pages(world)
    assert len(shown) == 7  # a page a part: no part of one document on another's page
    for page in shown:
        assert 0 < len(page.claims) <= 1_500, page.slug
    for n in (2, 3):
        held_by_pages = [p for p in shown if any(f"Doc{n} 00000 " in c.text for c in p.claims)]
        assert len(held_by_pages) == 1


@pytest.mark.parametrize(
    ("cred", "echoes"), [(SA, ("token",)), (USER, ("token", "refresh", "secret"))]
)
def test_through_the_runner_a_secret_google_sends_back_is_never_stored(world, fake, cred, echoes):
    for n, echo in enumerate(echoes, start=1):
        fake.doc(n, SINCE + n * HOUR).echo = echo
    fake.doc(9, SINCE + 9 * HOUR)
    world.store.add_source("drive", "google_drive", config(cred))
    world.clock.now = SINCE
    world.store.enable_source("drive")
    report = run(world, fake, NOW, cred=cred)
    assert report.saved == 1 and [c.source_ref for c in notes(world)] == [ref(9)]
    assert report.reasons.get(HELD_CREDENTIALS) == len(echoes)
    assert not leaked(dump(world.db), fake)


def test_through_the_runner_a_failed_run_moves_no_cursor_and_the_next_writes_the_rest(world, fake):
    world.store.add_source("drive", "google_drive", config())
    world.clock.now = SINCE
    world.store.enable_source("drive")
    fake.doc(1, SINCE + HOUR)
    assert run(world, fake, NOW).saved == 1
    before = world.store.get_source("drive")

    fake.doc(2, NOW + HOUR)
    fake.doc(3, NOW + 2 * HOUR)
    fake.forced = [("content", 500)]
    with pytest.raises(SourceError):
        run(world, fake, NOW + 2 * DAY)
    failed = world.store.get_source("drive")
    assert failed.cursor == before.cursor and failed.last_error
    assert CANARY not in failed.last_error and not leaked(failed.last_error, fake)
    assert len(notes(world)) == 1

    report = run(world, fake, NOW + 2 * DAY + HOUR)
    assert report.saved == 2 and world.store.get_source("drive").last_error is None
    assert sorted(c.source_ref for c in notes(world)) == [ref(1), ref(2), ref(3)]
    assert run(world, fake, NOW + 3 * DAY).saved == 0


def test_the_command_line_adds_switches_on_and_syncs_without_showing_a_secret(
    tmp_path, kind, keys, monkeypatch, capsys
):
    db = tmp_path / "cairn.db"
    store = Store(db)
    store.create_project("documents")
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

    fake.doc(1, LONG_AGO, "# Handbook\n\nWelcome aboard.\n", name="Handbook")
    settings = json.dumps(config(SA, projects=[], subject=SUBJECT, existing=True))
    assert cli("source-add", "--db", str(db), "--name", "drive", "--kind", "google_drive",
               "--config", settings) == 0  # fmt: skip
    assert cli("source-sync", "--db", str(db), "--name", "drive") == 0
    assert fake.requests == []  # off: nothing read, no token asked for
    assert cli("source-enable", "--db", str(db), "--name", "drive", "--project", "documents") == 0
    assert cli("source-sync", "--db", str(db), "--name", "drive") == 0
    assert cli("source-list", "--db", str(db)) == 0
    assert len(fake.calls("token", every=True)) == 1

    monkeypatch.setenv(CRED_ENV, json.dumps(service_account(keys, private_key=broken_key())))
    assert cli("source-sync", "--db", str(db), "--name", "drive") == 1
    assert CRED_ENV in said[-1]

    store = Store(db)
    try:
        stored = store.list_notes(store.get_project("documents").id)
        assert [c.source_ref for c in stored] == [ref(1)]
    finally:
        store.close()
    shown = "\n".join(said)
    assert not leaked(shown, fake) and CANARY not in shown and NOT_A_KEY not in shown
    assert not leaked(dump(db), fake)
