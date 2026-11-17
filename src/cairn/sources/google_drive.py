"""The Google Drive source: the documents put into one folder, read from the Drive API v3, a
page a document.

Contract (frozen by tests/acceptance/test_c7_google_drive.py; the API's shapes are recorded in
docs/google-drive-api.md and docs/google-auth.md, with what Google's pages do not state marked
as such and what cairn decided marked **[cairn]**). cairn never writes to Google. It signs in
through cairn.sources.google_auth, and calls the API and checks its config's names, address
and base_url through cairn.sources.google_api (its timestamps it reads itself: `moment` below
takes neither a lower-case "z" nor white space around a time).

The kind ("google_drive" in cairn.sources.KINDS)
- It is withheld (SourceKind.withheld): a page that cites one of its notes is not sent to a
  model unless the operator allows it (cairn.arrange), since Google's rules for its users'
  data restrict passing it on, and whether the operator's model may read documents is theirs
  to decide.
- It starts off: added off, read only once an owner or admin switches it on with a project.
  Its notes are written via "google-drive", with the label "google-drive". It proposes the
  project "documents" (DEFAULT_PROJECT). It asks for one scope, SCOPE (drive.readonly).
- The config: `credentials_env` (required: the name of the environment variable that holds
  the credential), `subject` (one email address: the person a service account acts for, by
  domain-wide delegation; left out, a service account acts as itself, and the folder is
  shared with its own address; refused with a refresh token, which `open` checks),
  `folder_id` (required: 10 to 100 letters, digits, "-" and "_"; an address is refused
  asking for the id), `settle_hours` (24, 1 to 720), `existing` (false), `projects` (at most
  one project name; none while it is off) and `base_url` (DEFAULT_BASE_URL unless given;
  https, or http on a loopback host; no user, password, query or fragment; a trailing "/"
  dropped). No other setting. A refusal names the setting at fault and never repeats a
  value; a value that looks like a credential is refused as such.
- `open` loads the credential (a SourceError naming the variable). Its `secrets` are the
  credential's, and the access token (and its Bearer header) once the pull has it.
- `describe` says which folder, read as whom (the `subject`, or "the account its
  credential belongs to"), at which address, and after how long a rest; never the
  variable. SWITCH_ON says what switching it on reads.

What is read, and when (**[cairn]**: Drive's change feed is not used, docs/google-drive-api.md
G; a pull lists the folder instead)
- Only the folder's direct children (no subfolder, no shortcut is followed) of three types:
  Google Docs (exported as text/markdown), and text/markdown and text/plain files (read
  with alt=media). Every other type is passed over without a word; so is a trashed file,
  which the listing's `q` leaves out.
- Each pull asks for one access token, then (1) files.get on the folder itself, with
  `fields=id,mimeType,trashed,capabilities/canListChildren` and `supportsAllDrives=true`;
  (2) files.list with `q='<folder_id>' in parents and trashed = false`, LIST_FIELDS,
  `pageSize=1000`, `supportsAllDrives=true` and `includeItemsFromAllDrives=true`, every
  page (at most MAX_PAGES, else a SourceError; **[cairn]** and, since Google chooses how
  many files a page holds, at most as many files of every type as full pages hold,
  MAX_PAGES x PAGE_SIZE: a million, else a SourceError); (3) the content of each due
  document.
- The stock: the first pull after a switch-on (`since`) takes stock of the folder: each
  document there last modified before `since` is "there" and never read unless it is
  modified after `since`, or `existing` is set (then none is). A document put into the
  folder later is due however old it is. Switched off and on again, stock is taken again;
  what was written or skipped stays so. **[cairn]** A document half written (see `parts`)
  and last modified before the new switch-on is not continued: its remaining parts are
  given up and it is taken stock of as any other (there, unless `existing`), since its text
  may now hold what was written while the source was off. With `existing` it is read again
  whole, so its page gets its first parts a second time, from one version. One modified
  after the switch-on goes on from its next part.
- A document is due once it has rested: not modified for `settle_hours`. Due documents are
  read by modifiedTime, then id. Each is read once: written or skipped, it is done, and a
  later edit, rename, move or deletion is not followed (decision 26: cairn keeps every note
  it is given, so a document read at each change would pile near-copies onto its page, and
  Google gives a reader no dependable sign that a Doc's content changed, notes G.4 and
  G.5). To have a new version read, a person takes the document out of the folder, leaves
  it out until a pull has run, and puts it back: a document a pull finds the folder no
  longer lists is forgotten, so it is due again. Out and back between two pulls, it is
  never seen to leave, and stays done.
- At most PER_PULL (25) contents are fetched by one pull; the rest wait for the next.
  A skip that needs no request does not count.

The cursor (opaque compact JSON this module owns, version 1; never a token, a key, an id, a
name, an address or any text). **[cairn]** It holds digests only: the first 16 hex digits
of the SHA-256 of a file's id.
- `on`: the switch-on its stock was taken for, in Unix microseconds.
- `there`: the documents listed at the stock-taking and not read.
- `done`: the documents written or skipped.
- `parts`: a document half written (a run stopped between two of its parts): how many of
  its parts are written. It goes on from the next when next read.
- Bound: each of the three keeps only documents the folder still lists, and a document
  read or skipped leaves `there`, so the cursor holds at most one digest for each listed
  document of the three types.
- Each draft carries `resume`: the cursor after it, so a run that stops there is picked up
  at the next draft. At its deadline or its BUDGET of requests (**[cairn]** 1,200: the
  folder, MAX_PAGES pages of the listing, 25 contents and their retries; a pull of one
  page costs 5 + 100 + 25 x 200 quota units, notes J), or when a content meets a rate limit
  that does not clear after 4 retries or the daily limit, a pull returns what it finished
  with such a cursor. One that runs out before it has listed the folder, or before it has
  written or skipped one document, raises a SourceError instead, so no cursor moves.

Skips (each a fixed sentence, without an id, that says the document is read again only once
it is taken out of the folder and put back; the Skip's ref is the file's id)
- NO_DOWNLOAD (capabilities.canDownload is false) and, for a text or Markdown file whose
  `size` is over MAX_BYTES, TOO_LARGE: both without a request.
- TOO_LARGE: content over MAX_BYTES (10 MiB) as it is read. GONE: 404 on the content.
  REFUSED: a 403 on the content that is neither a rate limit, the daily limit nor one of
  TOKEN_REASONS (those refuse the token, and fail the pull). EMPTY: no text once embedded
  images are left out. TOO_LONG: more than MAX_PARTS parts. UNREADABLE: any other failure
  while its content is made into drafts (one document never stops the source).

When Google says no (a message never holds a token, a body, a reason word or an address)
- The folder: 404 (on the folder or the listing) is NOT_FOUND; a file that is not a folder,
  NOT_A_FOLDER; one in the trash, TRASHED; canListChildren false (a folder with limited
  access, which Google lists as empty), LIMITED. Each names folder_id.
- 401, or a 403 that is not a rate limit, on the folder or the listing; 401, or a 403 with
  one of TOKEN_REASONS, on a content: "refused access", naming the scope, the delegation,
  the API, an administrator's policy and the sharing to check. Not retried.
- 429, or a 403 whose reason is a rate limit: waited out (google_api: 2^n seconds and up to
  one more, n from 0) at most 4 times. dailyLimitExceeded is not retried. Either, still on,
  fails the pull before a document is done (see the cursor above).
- A redirect (never followed), 5xx, another 4xx, a network error (by its class), a body
  not JSON, or an answer not of the documented shape: a SourceError.

What it writes, for each document, into the config's project
- `title`: the file's name on one line, without ".md" or ".txt" for a Markdown or text
  file, cut at a word to TITLE_MAX characters and TITLE_BYTES bytes as a heading is stored
  (**[cairn]**: a passage's headings may take 400 bytes); UNTITLED when nothing is left.
- `topic`: the title when its slug, with room for ", part NN", is within the compiler's 64
  characters; else the title cut at a word, ", " and 6 hex digits of the id's digest, so
  two long names never share a page. ", part N" from the second part.
- `text`: "# <title>", a blank line, the facts, a blank line, then the content: decoded as
  UTF-8 with replacement, one leading byte-order mark dropped, and every embedded `data:`
  image left out (its alt text kept). So that the search takes time in step with a line's
  length, an image whose alt text or label holds "[" or more than 999 characters, or whose
  `data:` address holds "(", is not looked for and stays as it came. The facts, one line
  each: "- Last modified: YYYY-MM-DD HH:MM UTC", "- Last modified by: <display name>" (left
  out when Google gives none or it holds "@", of any width, by cairn.sources.names's check:
  no email address is written), "- Open it in
  Google Drive: <link>" (webViewLink, https only, without its query or fragment).
- A document longer than 1,500 passages (as cairn.store.usage counts them) is cut into
  parts of at most PASSAGES, between two passages, before the first of a run of headings in
  a part's last third when there is one. Later parts are "# <title>, part N", a blank
  line, then their text; the facts are in the first only. At most MAX_PARTS parts. Each
  part also fits a page by its characters (K4: cairn.sources.fit, a page's bounds read when
  the drafts are made, the facts counted in the first): a part ends before the passage that
  would take it past them, and a passage longer than a part may hold is first cut into
  passages that fit (at a line's end, else at a space; a fence closed and opened again), so
  the parts hold the whole text. A part still past a page's bounds is cut at its end with
  NOTE_CUT. So a document of more than about 8,000,000 characters takes more than MAX_PARTS
  parts and is TOO_LONG.
- `source_ref`: "drive:<file id>", and "/N" from the second part. `label`: "google-drive".
"""

import asyncio
import json
import random
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

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
    TooLarge,
    check_base_url,
    digest,
    looks_like_a_credential,
)
from cairn.sources.google_auth import AuthorizedUser, Credential, access_token, load_credential
from cairn.sources.timing import limit
from cairn.spans import Span, chunk
from cairn.store.memory import FIELD_MAX, is_project_name
from cairn.store.usage import PASSAGE_KINDS, path_bytes

DEFAULT_BASE_URL = "https://www.googleapis.com/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SCOPES = [SCOPE]
SERVICE = "Google Drive"
VIA = LABEL = "google-drive"
ORIGIN = "drive:"
DEFAULT_PROJECT = "documents"
SETTINGS = (
    "credentials_env",
    "subject",
    "folder_id",
    "settle_hours",
    "existing",
    "projects",
    "base_url",
)
SETTLE_HOURS = (24, 1, 720)  # the default, the least, the most
API = "the Google Drive API (v3)"
FOLDER_ID = re.compile(r"[A-Za-z0-9_-]{10,100}")
DOC = "application/vnd.google-apps.document"
MARKDOWN, TEXT = "text/markdown", "text/plain"
FOLDER_TYPE = "application/vnd.google-apps.folder"
READ_TYPES = frozenset({DOC, MARKDOWN, TEXT})
MAX_BYTES = 10 * 1024 * 1024  # "10 MB": the most of one document's content cairn reads
PASSAGES = 1_500  # of one part, as cairn counts them (cairn.store.usage.passages_of)
MAX_PARTS = 20
SLUG_MAX = 64  # cairn.compiler.SLUG_MAX (the compiler imports the store, which imports this)
TITLE_MAX = FIELD_MAX["title"]
TOPIC_MAX = FIELD_MAX["topic"]
TITLE_BYTES = 160  # of the 400 a passage's headings may take (cairn.store.HEADINGS_MAX)
TAG = 6  # hex digits of the id's digest that tell long names apart
PART_ROOM = len("-part-nn")  # what ", part NN" adds to a slug
PART_CHARS = len(f", part {MAX_PARTS}")
UNTITLED = "Untitled document"
DATA_DEFINITION = re.compile(r"^ {0,3}\[([^\]\n]{1,999})\]:[ \t]*<?data:[^\n]*\n?", re.MULTILINE)
# Linear on any line: an alt text or a label holds no "[" and at most 999 characters, and a
# data address no "(", so a search from each "!" stops at the next "[" or "(" instead of
# scanning the rest of the line (a line of "![" repeated took time growing with its square).
DATA_IMAGE = re.compile(r"!\[([^\[\]\n]{0,999})\]\([ \t]*<?data:[^()\n]*\)")
REFERENCE_IMAGE = re.compile(r"!\[([^\[\]\n]{0,999})\](?:\[([^\[\]\n]{0,999})\])?")
CURSOR_VERSION = 1
DIGEST = re.compile(r"[0-9a-f]{16}")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
FOLDER_FIELDS = "id,mimeType,trashed,capabilities/canListChildren"
LIST_FIELDS = (
    "nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink,"
    "lastModifyingUser/displayName,capabilities/canDownload)"
)
PAGE_SIZE = 1_000  # files.list's most
MAX_PAGES = 1_000  # of the folder's listing in one pull: a million files
PER_PULL = 25  # documents whose content one pull fetches
BUDGET = 1_200  # requests of one pull: the folder, the listing's pages, 25 contents, retries
FILE_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
SIZE = re.compile(r"[0-9]{1,20}")
TIME = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})")
DAILY = "dailyLimitExceeded"
# A 403 on content with one of these refuses the token itself, not the one document.
TOKEN_REASONS = frozenset(
    {"authError", "domainPolicy", "insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}
)
FOLDER_GET, LIST, EXPORT, MEDIA = (
    "files.get on the folder",
    "files.list",
    "files.export",
    "files.get (alt=media)",
)
NOT_FOUND = (
    f"{SERVICE} found no folder by the id in folder_id: check the id, and that the folder is"
    " shared with the account that reads it"
)
NOT_A_FOLDER = (
    "folder_id names a file that is not a folder: give the id of a folder, the last part of"
    " its address after /folders/"
)
TRASHED = (
    "the folder folder_id names is in the trash: restore it from the trash, or give another"
    " folder's id"
)
LIMITED = (
    "the account that reads may not list the folder folder_id names (a folder with limited"
    " access, which Google lists as empty): share that folder itself with it"
)

AGAIN = "it is read again only once it is taken out of the folder and put back"
NO_DOWNLOAD = (
    "not downloadable: its owner, its shared drive or an administrator does not let the"
    f" account that reads download it; {AGAIN}"
)
TOO_LARGE = f"too large: it is over 10 MB, more than this server reads; {AGAIN}"
GONE = (
    "gone: Google Drive no longer gives it to the account that reads (deleted, or no longer"
    f" shared, since it was listed); {AGAIN}"
)
REFUSED = (
    "refused: Google Drive would not give its content to the account that reads (a"
    f" restriction on it, or a Doc too large to export); {AGAIN}"
)
EMPTY = f"empty: it holds no text once its embedded images are left out; {AGAIN}"
TOO_LONG = (
    f"too long: it would take more than {MAX_PARTS} notes of {PASSAGES:,} passages each, each"
    f" within what a page holds; {AGAIN}"
)
NOTE_CUT = "The rest of this note is left out: it is longer than a page holds."

UNREADABLE = (
    f"unreadable: this server failed on its content and could not make a note of it; {AGAIN}"
)

SWITCH_ON = (
    "Switching it on reads the documents put into the folder or changed there from then on,"
    " each once it has rested: nothing from before, and nothing from while it was off. With"
    " existing set, it reads the documents already there too."
)


# The config


def check_config(config: object) -> dict[str, Any]:
    """The config as stored, with the defaults filled in, or ValueError naming the setting at
    fault and never a value."""
    if not isinstance(config, Mapping):
        raise ValueError("a google_drive source's config is an object of settings")
    for setting in config:
        if setting in SETTINGS:
            continue
        if (
            isinstance(setting, str)
            and SETTING_NAME.fullmatch(setting)
            and not looks_like_a_credential(setting)
        ):
            raise ValueError(
                f"a google_drive source has no setting {setting!r}: it takes {', '.join(SETTINGS)}"
            )
        raise ValueError(
            "a google_drive source does not take one of the settings given: it takes"
            f" {', '.join(SETTINGS)}"
        )
    checked: dict[str, Any] = {"credentials_env": _check_variable(config.get("credentials_env"))}
    subject = config.get("subject")
    if subject is not None:
        if isinstance(subject, str) and looks_like_a_credential(subject):
            raise ValueError("subject holds what looks like a credential, not an email address")
        if not isinstance(subject, str) or len(subject) > 254 or not EMAIL.fullmatch(subject):
            raise ValueError(
                "subject is one email address: the person a service account acts for (left out,"
                " the service account acts as itself)"
            )
        checked["subject"] = subject
    checked["folder_id"] = _check_folder(config.get("folder_id"))
    default, least, most = SETTLE_HOURS
    hours = config.get("settle_hours", default)
    if isinstance(hours, bool) or not isinstance(hours, int) or not least <= hours <= most:
        raise ValueError(
            f"settle_hours is a whole number from {least} to {most}: how long a document must"
            " go unmodified before it is read"
        )
    checked["settle_hours"] = hours
    existing = config.get("existing", False)
    if not isinstance(existing, bool):
        raise ValueError(
            "existing is true or false: whether the documents already in the folder when the"
            " source is switched on are read too"
        )
    checked["existing"] = existing
    checked["projects"] = _check_projects(config)
    checked["base_url"] = check_base_url(config.get("base_url", DEFAULT_BASE_URL), API)
    return checked


def _check_variable(variable: object) -> str:
    if variable is None:
        raise ValueError(
            "a google_drive source needs credentials_env: the name of the environment variable"
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


def _check_folder(folder: object) -> str:
    if folder is None:
        raise ValueError(
            "a google_drive source needs folder_id: the id of the one folder it reads, the last"
            " part of the folder's address"
        )
    if isinstance(folder, str) and looks_like_a_credential(folder):
        raise ValueError("folder_id holds what looks like a credential, not a folder's id")
    if isinstance(folder, str) and ("/" in folder or ":" in folder or "?" in folder):
        raise ValueError(
            "folder_id is the folder's id, not its address: the last part of the address, after"
            " /folders/ and before any ?"
        )
    if not isinstance(folder, str) or not FOLDER_ID.fullmatch(folder):
        raise ValueError(
            "folder_id is the folder's id: 10 to 100 letters, digits, - and _, the last part of"
            " the folder's address"
        )
    return folder


def _check_projects(config: Mapping[str, Any]) -> list[str]:
    projects = config.get("projects", [])
    if not isinstance(projects, list | tuple):
        raise ValueError("projects is a list of at most one project's name")
    if len(projects) > 1:
        raise ValueError(
            "projects names at most one project: a google_drive source writes into one"
        )
    for project in projects:
        if isinstance(project, str) and looks_like_a_credential(project):
            raise ValueError("projects holds a value that looks like a credential")
        if not isinstance(project, str) or not is_project_name(project):
            raise ValueError("projects holds a project's name (not its id)")
    return list(projects)


def describe(config: Mapping[str, Any]) -> str:
    """What a source reads, for GET /sources: which folder, as whom, from where, and when a
    document is read. Never the variable or a credential."""
    whom = config.get("subject") or "the account its credential belongs to"
    hours = config.get("settle_hours", SETTLE_HOURS[0])
    said = (
        "Google Docs, Markdown and text files directly in the Google Drive folder"
        f" {config['folder_id']}, read as {whom}, at {config['base_url']}; each is read once"
        f" it has not changed for {hours} hour{'s' if hours != 1 else ''}"
    )
    if config.get("existing"):
        said += ", those already there when it was switched on included"
    return said


# Opening


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
) -> "DriveSource":
    """The source a stored config describes, with its credential read from `env`.
    SourceError names a variable or a setting, never a value."""
    credential = load_credential(config["credentials_env"], env)
    if isinstance(credential, AuthorizedUser) and config.get("subject") is not None:
        raise SourceError(
            f"source {name!r} reads with a refresh token, which reads as the person who"
            " authorised it: remove subject from its config"
        )
    return DriveSource(
        name, config, credential, http=http, clock=clock, sleep=sleep, deadline=deadline,
        since=since, jitter=jitter,
    )  # fmt: skip


KIND = SourceKind(
    check=check_config,
    open=open_source,
    via=VIA,
    starts_off=True,
    describe=describe,
    switch_on=SWITCH_ON,
    default_project=DEFAULT_PROJECT,
    withheld=True,
    ref_form=ORIGIN.removesuffix(":"),  # M3: what its notes' source_ref starts with
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


# The cursor, and what counts as new


def _micros(moment: datetime) -> int:
    return (moment.astimezone(UTC) - EPOCH) // timedelta(microseconds=1)


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class Cursor:
    """What a pull starts from (see the module docstring): the switch-on its stock was taken
    for, and the digests of the documents it knows."""

    on: datetime
    there: set[str] = field(default_factory=set)  # listed at the stock-taking, unchanged since
    done: set[str] = field(default_factory=set)  # written, or skipped for good
    parts: dict[str, int] = field(default_factory=dict)  # half written: the next part's index

    @classmethod
    def taken(
        cls, known: "Cursor | None", since: datetime, listed: list["Document"], *, existing: bool
    ) -> "Cursor":
        """The cursor for a source last switched on at `since`: `known` itself when its stock
        was taken for that switch-on; else stock taken now of what the folder lists: a
        document last modified before `since` is there (unless `existing`), what is done
        stays so, and a half-written one stays so only when modified after `since` (one
        modified before it gives up its remaining parts and is taken stock of as any other)."""
        if known is not None and known.on == since:
            return known
        done = set(known.done) if known else set()
        parts = dict(known.parts) if known else {}
        there = set()
        for document in listed:
            key = digest(document.id)
            if document.modified >= since or key in done:
                continue
            parts.pop(key, None)  # its text may hold what was written while the source was off
            if not existing:
                there.add(key)
        return cls(on=since, there=there, done=done, parts=parts)

    def kept(self, listed: set[str]) -> "Cursor":
        """What it knows of the documents the folder lists (by digest), and nothing else."""
        return Cursor(
            on=self.on,
            there=self.there & listed,
            done=self.done & listed,
            parts={key: index for key, index in self.parts.items() if key in listed},
        )

    def encode(self) -> str:
        return json.dumps(
            {
                "v": CURSOR_VERSION,
                "on": _micros(self.on),
                "there": sorted(self.there),
                "done": sorted(self.done),
                "parts": {key: self.parts[key] for key in sorted(self.parts)},
            },
            separators=(",", ":"),
        )

    @classmethod
    def decode(cls, text: str) -> "Cursor":
        broken = SourceError(
            "the stored cursor is not one this Google Drive source wrote: remove the source and"
            " add it again to start afresh"
        )
        try:
            state = json.loads(text)
        except (ValueError, RecursionError):
            raise broken from None
        if not isinstance(state, dict) or state.get("v") != CURSOR_VERSION:
            raise broken
        on, there, done, parts = (state.get(k) for k in ("on", "there", "done", "parts"))
        if not _count(on) or not isinstance(there, list) or not isinstance(done, list):
            raise broken
        if not isinstance(parts, dict):
            raise broken
        if not all(isinstance(k, str) and DIGEST.fullmatch(k) for k in [*there, *done, *parts]):
            raise broken
        if not all(_count(index) and index > 0 for index in parts.values()):
            raise broken
        return cls(
            on=EPOCH + timedelta(microseconds=on),
            there=set(there),
            done=set(done),
            parts=dict(parts),
        )


def due(
    cursor: Cursor, listed: list["Document"], *, since: datetime, now: datetime, settle: timedelta
) -> list["Document"]:
    """The documents to read, by `modifiedTime`, then by id: those not done, that were not
    there at the stock-taking or have been modified since the switch-on, once rested."""
    found = []
    for document in listed:
        key = digest(document.id)
        if key in cursor.done:
            continue
        if key in cursor.there and document.modified < since:
            continue
        if now - document.modified < settle:
            continue
        found.append(document)
    return sorted(found, key=lambda document: (document.modified, document.id))


# What a draft says


@dataclass(frozen=True)
class Document:
    """A file of a type cairn reads, as the folder's listing gave it."""

    id: str
    name: str
    mime: str
    modified: datetime
    size: int | None  # bytes; None when Google gives none (a Doc may have none)
    link: str | None  # webViewLink
    editor: str | None  # lastModifyingUser.displayName
    can_download: bool


def moment(value: object) -> datetime | None:
    """An RFC 3339 time as Drive gives it (0 to 9 fractional digits, Z or an offset), in
    UTC to the microsecond; None for anything else."""
    found = TIME.fullmatch(value) if isinstance(value, str) else None
    if found is None:
        return None
    whole, fraction, zone = found.groups()
    micros = (fraction or "").ljust(6, "0")[:6]
    try:  # a date that is not one, or a moment outside years 1 to 9999 once in UTC
        parsed = datetime.fromisoformat(f"{whole}.{micros}{'+00:00' if zone == 'Z' else zone}")
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _shape(what: str) -> SourceError:
    return SourceError(f"{SERVICE}'s answer to {what} is not the documented shape")


def _document(item: object) -> Document | None:
    """A listed file of a type cairn reads; None for any other type. A file of another type
    needs only an id and a type; one cairn reads needs its name and modifiedTime too, and the
    optional fields of the documented types."""
    if not isinstance(item, dict):
        raise _shape(LIST)
    file_id, mime = item.get("id"), item.get("mimeType")
    if not isinstance(file_id, str) or not FILE_ID.fullmatch(file_id) or not isinstance(mime, str):
        raise _shape(LIST)
    if mime not in READ_TYPES:
        return None
    name, modified, size = item.get("name"), moment(item.get("modifiedTime")), item.get("size")
    capabilities = item.get("capabilities", {})
    can_download = capabilities.get("canDownload", True) if isinstance(capabilities, dict) else None
    if (
        not isinstance(name, str)
        or modified is None
        or not (size is None or (isinstance(size, str) and SIZE.fullmatch(size)))
        or not isinstance(can_download, bool)
    ):
        raise _shape(LIST)
    user = item.get("lastModifyingUser")
    editor = user.get("displayName") if isinstance(user, dict) else None
    link = item.get("webViewLink")
    return Document(
        id=file_id,
        name=name,
        mime=mime,
        modified=modified,
        size=int(size) if size is not None else None,
        link=link if isinstance(link, str) else None,
        editor=editor if isinstance(editor, str) else None,
        can_download=can_download,
    )


def refusal(reply: Reply, what: str, variable: str) -> str:
    """A SourceError's message for an answer that is not 200, from its status and reasons."""
    status = reply.status
    if reply.limited:
        return (
            f"{SERVICE} still answered with a rate limit to {what} after {RETRIES} retries:"
            " the next run goes on"
        )
    if DAILY in reply.reasons:
        return (
            f"{SERVICE} answered {what} that the Cloud project's daily limit is reached, a cap"
            " set on its quota: raise or remove it in the Cloud console; the next run tries again"
        )
    if status == 404 and what in (FOLDER_GET, LIST):
        return NOT_FOUND
    if status in (401, 403):
        return (
            f"{SERVICE} refused access (HTTP {status}) to {what}: check that the credential in"
            f" {variable} carries the scope {SCOPE} (for a service account acting for a"
            " subject, in its domain-wide delegation), that the Google Drive API is enabled in"
            " its Cloud project, that no administrator's policy blocks it, and that the folder"
            " is shared with the account that reads it"
        )
    if 300 <= status < 400:
        return (
            f"{SERVICE} answered {what} with a redirect (HTTP {status}), which this server does not"
            " follow: check base_url"
        )
    if status >= 500:
        return (
            f"{SERVICE} answered HTTP {status} to {what}, a fault on its side: the next run"
            " tries again"
        )
    if 400 <= status < 500:
        return f"{SERVICE} refused the request to {what} (HTTP {status})"
    return f"{SERVICE} answered HTTP {status} to {what}"


def _one_line(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _cut(text: str, chars: int, heading_bytes: int | None = None) -> str:
    """`text` cut (at a word where it can be, never ending on a comma) to at most `chars`
    characters and, given, `heading_bytes` bytes as a heading is stored."""

    def fits(cut: str) -> bool:
        return len(cut) <= chars and (heading_bytes is None or path_bytes([cut]) <= heading_bytes)

    cut = text
    while cut and not fits(cut):
        shorter = cut[: chars + 1] if len(cut) > chars + 1 else cut
        shorter = shorter.rsplit(" ", 1)[0] if " " in shorter.strip() else shorter[:-1]
        cut = shorter.rstrip(" ,") or shorter
    return cut


def title_of(name: object, mime: str) -> str:
    """A document's title: its name on one line, without the extension of a Markdown or text
    file, cut to what a note's title and its heading may hold."""
    title = _one_line(name)
    if mime in (MARKDOWN, TEXT):
        for extension in (".md", ".txt"):
            if title.lower().endswith(extension) and len(title) > len(extension):
                title = title[: -len(extension)].rstrip()
                break
    return _cut(title, TITLE_MAX, TITLE_BYTES) or UNTITLED


def full_slug(text: str) -> str:
    """The slug cairn.compiler.slugify makes of `text`, before it is cut at SLUG_MAX."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def topic(title: str, file_id: str, number: int) -> str:
    """The page a part of a document goes on. The title itself when its slug, with room for
    ", part NN", is within the compiler's SLUG_MAX (and not empty); else the title cut short,
    ", " and TAG digits of the id's digest, so long names never share a page. Then ", part N"
    from the second part."""
    suffix = f", part {number}" if number > 1 else ""
    slug = full_slug(title)
    if slug and len(slug) + PART_ROOM <= SLUG_MAX and len(title) + PART_CHARS <= TOPIC_MAX:
        return title + suffix
    tag = digest(file_id)[:TAG]
    room = SLUG_MAX - 1 - TAG - PART_ROOM  # "-" and the tag, then "-part-NN"
    cut = title
    while cut and (len(full_slug(cut)) > room or len(cut) + 2 + TAG + PART_CHARS > TOPIC_MAX):
        shorter = cut.rsplit(" ", 1)[0] if " " in cut.strip() else cut[:-1]
        cut = shorter.rstrip(" ,") or shorter
    return f"{cut or UNTITLED}, {tag}{suffix}"


def clean(content: bytes) -> str:
    """The content as cairn writes it: UTF-8 with replacement, a leading byte-order mark
    dropped, and embedded `data:` images left out: a reference definition whose target is a
    `data:` address is dropped, and an image with such an address, inline or by a dropped
    reference, is replaced by its alt text."""
    text = content.decode("utf-8", errors="replace").removeprefix("﻿")
    dropped: set[str] = set()

    def definition(found: re.Match[str]) -> str:
        dropped.add(_label(found.group(1)))
        return ""

    text = DATA_DEFINITION.sub(definition, text)
    text = DATA_IMAGE.sub(lambda found: found.group(1), text)
    if dropped:

        def by_reference(found: re.Match[str]) -> str:
            alt, label = found.group(1), found.group(2)
            named = _label(label if label else alt)
            return alt if named in dropped else found.group(0)

        text = REFERENCE_IMAGE.sub(by_reference, text)
    return text


def _label(label: str) -> str:
    return " ".join(label.split()).casefold()


def _link(value: object) -> str | None:
    """webViewLink without its query and fragment (so no resource key is copied); None for
    anything but an https address without a user."""
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not host or "@" in parts.netloc:
        return None
    return f"https://{parts.netloc}{parts.path}"


def facts(document: Document) -> list[str]:
    """The lines under a document's heading: when it was last modified, by whom (a display
    name, never one holding an address), and where to open it."""
    said = [f"- Last modified: {document.modified.astimezone(UTC):%Y-%m-%d %H:%M} UTC"]
    editor = names.shown(document.editor)
    if editor:
        said.append(f"- Last modified by: {editor}")
    link = _link(document.link)
    if link:
        said.append(f"- Open it in Google Drive: {link}")
    return said


def split(text: str, first: int, room: int | None = None) -> list[str] | None:
    """The text cut into the bodies of its parts: the first holds at most `first` passages as
    cairn counts them, the others PASSAGES (or a page's claims, when fewer); each ends between
    two passages, just before the first of a run of headings when one falls in its last
    third. Each also holds at most a page's characters in its passages, the first `room` of
    them (K4: cairn.sources.fit, read now): a part ends before the passage that would take it
    past them, and a passage longer than that is first cut into passages that fit
    (fit.opened_up), nothing of it lost. None past MAX_PARTS."""
    passages, chars = min(PASSAGES, fit.claims()), fit.chars()
    most = chars if room is None else max(1, min(room, chars))
    text = fit.opened_up(text, most)  # every passage then fits any part, the first included
    lines = text.splitlines(keepends=True)
    # A blank first line, as under the heading and the facts: never front matter.
    spans = chunk("cap_000000000000", "\n" + text)
    groups: list[list[Span]] = []
    current: list[Span] = []
    count, budget, held = 0, first, 0
    for span in spans:
        passage = span.kind in PASSAGE_KINDS
        size = len(span.text) if passage else 0
        full = count >= budget
        if passage and count > 0 and (full or held + size > most):
            cut = _cut_point(current, budget) if full else len(current)
            if _held(current[cut:]) + size > chars:
                cut = len(current)  # what a heading would carry over would not fit
            groups.append(current[:cut])
            if len(groups) == MAX_PARTS:
                return None
            current = current[cut:]
            count = sum(s.kind in PASSAGE_KINDS for s in current)
            held = _held(current)
            budget, most = passages, chars
        current.append(span)
        count += passage
        held += size
    if current:
        groups.append(current)
    bodies = []
    for group in groups:
        last = lines[group[-1].end_line - 2]
        body = "".join(lines[group[0].start_line - 2 : group[-1].end_line - 1])
        bodies.append(body if last != last.splitlines()[0] else body + "\n")  # ends a line
    return bodies


def _held(spans: list[Span]) -> int:
    """The characters of the passages among `spans`, as a page counts them."""
    return sum(len(span.text) for span in spans if span.kind in PASSAGE_KINDS)


def _cut_point(spans: list[Span], budget: int) -> int:
    """Where a full part ends: before the last run of headings that starts in its last third,
    or after its last span."""
    passages, cut = 0, len(spans)
    third = budget - budget // 3
    for index, span in enumerate(spans):
        if span.kind == "heading":
            starts = index > 0 and spans[index - 1].kind != "heading"
            if starts and passages >= third:
                cut = index
        passages += span.kind in PASSAGE_KINDS
    return cut


def drafts(document: Document, text: str, project: str) -> list[NoteDraft] | None:
    """A document's drafts: the first with its heading and facts, later ones with their own
    heading; [] when the text holds nothing, None when it would take more than MAX_PARTS."""
    title = title_of(document.name, document.mime)
    head = facts(document)
    room = fit.chars() - sum(map(len, head))  # the facts are passages of the first part
    bodies = split(text, min(PASSAGES, fit.claims()) - len(head), room)
    if bodies is None:
        return None
    ref = ORIGIN + document.id
    made = []
    for index, body in enumerate(bodies):
        number = index + 1
        if number == 1:
            written = f"# {title}\n\n" + "\n".join(head) + "\n\n" + body
        else:
            written = f"# {title}, part {number}\n\n" + body
        written = fit.fitted(written, NOTE_CUT)
        made.append(
            NoteDraft(
                project=project,
                title=title,
                text=written,
                source_ref=ref + (f"/{number}" if number > 1 else ""),
                label=LABEL,
                topic=topic(title, document.id, number),
            )
        )
    return made


class DriveSource:
    """Reads the documents of one Drive folder (see the module docstring)."""

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
        self.folder = config["folder_id"]
        self.settle = timedelta(hours=config["settle_hours"])
        self.existing = config["existing"]
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
        return f"DriveSource({self.base_url!r}, name={self.name!r})"

    @property
    def secrets(self) -> tuple[str, ...]:
        """The credential's secrets and, once the pull has it, the access token."""
        token = (self._token, f"Bearer {self._token}") if self._token else ()
        return (*self._credential.secrets, *token)

    async def pull(self, cursor: str | None) -> Pulled:
        known = None if cursor is None else Cursor.decode(cursor)
        now = self._clock().astimezone(UTC)
        since = (self.since or (known.on if known else now)).astimezone(UTC)
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
            deadline=self.deadline, budget=BUDGET, jitter=self._jitter, limit=MAX_BYTES,
        )  # fmt: skip
        try:
            await self._folder(reader)
            listed = await self._listed(reader)
        except Spent as spent:
            raise SourceError(
                f"the folder could not be listed within one pull's bounds ({spent}), so the"
                " cursor cannot move: the next run tries again"
            ) from None
        state = Cursor.taken(known, since, listed, existing=self.existing)
        state = state.kept({digest(document.id) for document in listed})
        progress = _Progress(state)
        fetched = 0
        for document in due(state, listed, since=since, now=now, settle=self.settle):
            unread = _unread(document)
            if unread is not None:
                progress.skip(document, unread)
                continue
            if fetched == PER_PULL:
                break
            fetched += 1
            try:
                content = await self._content(reader, document)
            except (Spent, _Stopped) as stopped:
                if progress.drafts or progress.skipped:
                    break
                if isinstance(stopped, _Stopped):
                    raise SourceError(str(stopped)) from None
                raise SourceError(
                    f"no document could be read within one pull's bounds ({stopped}), so the"
                    " cursor cannot move: the next run tries again"
                ) from None
            if isinstance(content, str):
                progress.skip(document, content)
                continue
            try:
                text = clean(content)
                made = drafts(document, text, self.project) if text.strip() else []
            except SourceError:
                raise
            except Exception:  # one document cairn fails on must not stop the source for good
                progress.skip(document, UNREADABLE)
                continue
            if made is None:
                progress.skip(document, TOO_LONG)
            elif not made:
                progress.skip(document, EMPTY)
            else:
                progress.write(document, made)
        return Pulled(progress.drafts, progress.skipped, progress.state.encode())

    # Requests

    async def _folder(self, reader: Reader) -> None:
        """The folder itself, first: a SourceError naming folder_id when it is not one the
        account that reads may list."""
        reply = await reader.get(
            f"{self.base_url}/files/{self.folder}",
            {"fields": FOLDER_FIELDS, "supportsAllDrives": "true"},
            service=SERVICE,
            what=FOLDER_GET,
        )
        if reply.status != 200:
            raise SourceError(refusal(reply, FOLDER_GET, self.variable))
        body = reply.body
        if not isinstance(body, dict) or not isinstance(body.get("mimeType"), str):
            raise _shape(FOLDER_GET)
        trashed = body.get("trashed", False)
        capabilities = body.get("capabilities", {})
        can_list = (
            capabilities.get("canListChildren", True) if isinstance(capabilities, dict) else None
        )
        if not isinstance(trashed, bool) or not isinstance(can_list, bool):
            raise _shape(FOLDER_GET)
        if body["mimeType"] != FOLDER_TYPE:
            raise SourceError(NOT_A_FOLDER)
        if trashed:
            raise SourceError(TRASHED)
        if not can_list:
            raise SourceError(LIMITED)

    async def _listed(self, reader: Reader) -> list[Document]:
        """The folder's children of the types cairn reads, every page of them."""
        params = {
            "q": f"'{self.folder}' in parents and trashed = false",
            "fields": LIST_FIELDS,
            "pageSize": str(PAGE_SIZE),
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        found: list[Document] = []
        listed = 0  # files of every type: a page may hold more than asked
        token: str | None = None
        for _ in range(MAX_PAGES):
            asked = {**params, "pageToken": token} if token else params
            reply = await reader.get(f"{self.base_url}/files", asked, service=SERVICE, what=LIST)
            if reply.status != 200:
                raise SourceError(refusal(reply, LIST, self.variable))
            body = reply.body
            if not isinstance(body, dict):
                raise _shape(LIST)
            files, token = body.get("files", []), body.get("nextPageToken")
            if not isinstance(files, list) or not (token is None or isinstance(token, str)):
                raise _shape(LIST)
            listed += len(files)
            if listed > MAX_PAGES * PAGE_SIZE:
                raise SourceError(
                    f"{SERVICE} listed more than {MAX_PAGES * PAGE_SIZE:,} files of the folder in"
                    " one pull"
                )
            found += [document for item in files if (document := _document(item))]
            if not token:
                return found
        raise SourceError(
            f"{SERVICE} listed more than {MAX_PAGES:,} pages of the folder in one pull"
        )

    async def _content(self, reader: Reader, document: Document) -> bytes | str:
        """A document's bytes, or the reason it is skipped. _Stopped when the pull must stop
        (a rate limit still on, or the daily limit); SourceError when the token is refused or
        Google fails."""
        if document.mime == DOC:
            url, params, what = (
                f"{self.base_url}/files/{document.id}/export",
                {"mimeType": "text/markdown"},
                EXPORT,
            )
        else:
            url, params, what = (
                f"{self.base_url}/files/{document.id}",
                {"alt": "media", "supportsAllDrives": "true"},
                MEDIA,
            )
        try:
            reply = await reader.get(url, params, service=SERVICE, what=what, raw=True)
        except TooLarge:
            return TOO_LARGE
        if reply.status == 200:
            assert isinstance(reply.body, bytes)
            return reply.body
        if reply.status == 404:
            return GONE
        if reply.limited or DAILY in reply.reasons:
            raise _Stopped(refusal(reply, what, self.variable))
        if reply.status == 403 and not reply.reasons & TOKEN_REASONS:
            return REFUSED
        raise SourceError(refusal(reply, what, self.variable))


class _Stopped(Exception):
    """A rate limit or the daily limit on a document's content: the pull stops there."""


def _unread(document: Document) -> str | None:
    """The reason a document is skipped without a request, if it is."""
    if not document.can_download:
        return NO_DOWNLOAD
    if document.mime != DOC and document.size is not None and document.size > MAX_BYTES:
        return TOO_LARGE
    return None


@dataclass
class _Progress:
    """What a pull has done so far: the cursor it stands at, and its drafts and skips."""

    state: Cursor
    drafts: list[NoteDraft] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)

    def skip(self, document: Document, reason: str) -> None:
        key = digest(document.id)
        self.state.there.discard(key)
        self.state.parts.pop(key, None)
        self.state.done.add(key)
        self.skipped.append(Skip(document.id, reason))

    def write(self, document: Document, made: list[NoteDraft]) -> None:
        """The parts not yet written, each with the cursor that stands after it."""
        key = digest(document.id)
        self.state.there.discard(key)
        written = self.state.parts.get(key, 0)
        for number in range(written + 1, len(made) + 1):
            if number == len(made):
                self.state.parts.pop(key, None)
                self.state.done.add(key)
            else:
                self.state.parts[key] = number
            self.drafts.append(replace(made[number - 1], resume=self.state.encode()))
        if written >= len(made):  # its text shrank since it was half written
            self.state.parts.pop(key, None)
            self.state.done.add(key)
