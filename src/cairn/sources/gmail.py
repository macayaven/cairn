"""The Gmail source: the messages of one mailbox that a person marks with one Gmail label, each
read once, a page a conversation a month.

Contract (frozen by tests/acceptance/test_c6_gmail.py; the API's shapes are recorded in
docs/gmail-api.md and docs/google-auth.md, with what Google's pages do not state marked as such
and what cairn decided marked **[cairn]**). cairn never writes to Google. It signs in through
cairn.sources.google_auth, calls the API and checks its config through
cairn.sources.google_api, and turns HTML into text with cairn.sources.markup.html_to_text.

Why only what a person marks: a mailbox holds other people's words, most of it noise, and what
was copied cannot be taken back. So there is no setting that reads a whole mailbox: a person
puts one label of their own on a message, and that message is read once.

The kind ("gmail" in cairn.sources.KINDS)
- It is withheld (SourceKind.withheld): a page that cites one of its notes is not sent to a
  model unless the operator allows it (cairn.arrange), since Google's rules for its users'
  data restrict passing it on, and whether the operator's model may read mail is theirs
  to decide.
- It starts off: added off, read only once an owner or admin switches it on with a project, and
  then only messages that arrived at or after that switch-on (`since`). Its notes are written
  via "gmail", with the label "gmail". It proposes the project "mail" (DEFAULT_PROJECT). It asks
  for one scope, SCOPE (gmail.readonly), which reads; it never asks for one that can send,
  change or delete.
- The config: `credentials_env` (required: the name of the environment variable that holds
  the credential), `subject` (one email address: the person whose mailbox a service account
  reads, by domain-wide delegation), `label` (required: the label's name as Gmail's API gives
  it, `users.labels.list`'s `name`; 1 to LABEL_MAX characters, no control character; Gmail's
  own label names, GMAIL_OWN and anything beginning "CATEGORY_", are refused whatever their
  case, so the source can never be pointed at a whole mailbox), `lookback_days` (30, 1 to
  365: how long after a message arrived its label is still noticed), `projects` (at most one
  project name; none while it is off) and `base_url` (DEFAULT_BASE_URL unless given; https, or
  http on a loopback host). No other setting. A refusal names the setting at fault and never
  repeats a value; a value that looks like a credential is refused as such.
- `open` loads the credential (a SourceError naming the variable). A service account has no
  mailbox of its own and none can be shared with it, so it needs `subject`; a refresh token
  reads the mailbox of the person who authorised it, so `subject` is refused with one. Its
  `secrets` are the credential's, and the access token (and its Bearer header) once the pull
  has it. Nothing is sent before the first pull.
- `describe` says whose mailbox, which label, at which address and for how long a label is
  noticed; never the variable. SWITCH_ON says what switching it on reads.

What a pull reads
- One access token, then `users.labels.list`, then every page of `users.messages.list`, then
  `users.messages.get` with `format=full` for each listed message not yet handled, at most
  READS (50) a pull: the rest wait for the next. The mailbox in the path is the subject's
  address for a service account and "me" for a refresh token (**[cairn]**: the notes do not
  state that "me" names the impersonated person; the address is the documented form).
- The label: an exact match on `name`; else a match ignoring case when exactly one label
  matches; else NOT_FOUND, or AMBIGUOUS when several match. A label whose `type` is not
  "user" is NOT_YOURS. Resolved again on every pull, so a label deleted and made again under
  the same name is found by its new id.
- The listing: `labelIds` the label's id, `q` "after:<seconds>" (`after`: a day before the
  later of `since` and the look-back's line, now minus `lookback_days`; the day covers what
  the notes do not state, whether `after:` is inclusive and that it goes by `internalDate`),
  `maxResults` PAGE (500), no `includeSpamTrash` (so Spam and Trash are left out), the same
  beside every `pageToken`, at most MAX_PAGES pages (else a SourceError). **[cairn]** Google
  chooses how many ids a page holds, so more ids than full pages hold (MAX_PAGES x PAGE:
  50,000) fail the pull with a SourceError too. An id listed twice is read once. **[cairn]**
  Nothing depends on the listing's order, nor on `q` narrowing a listing
  by `labelIds` (not stated either): each message's own `internalDate` decides, so a listing
  that returns every labelled message, however old, gives the same drafts over more pulls.
  The ids are read in the reverse of the order listed (the guide says newest first, so the
  oldest are read first when that holds). **[cairn]** A listed id of other than 1 to 256
  letters, digits, "_" and "-" fails the pull before it goes into a path (the notes do not
  state the form; Gmail's are hex): "." and ".." pass quote() unchanged, and a URL resolves
  them to another endpoint.
- A message read is written when it arrived (`internalDate`) at or after `since` and at or
  after now minus `lookback_days`, and its `labelIds` do not hold DRAFT. Otherwise it is passed
  over for good, without a word: nothing went wrong. So a message that arrived before the
  switch-on is never written, even when it is labelled later, and one labelled more than
  `lookback_days` after it arrived is not written either: nothing in Gmail says when a label
  was put on (notes, headline 1), so when it arrived is the only line that can be drawn and
  kept. The unit is a message: a reply that arrives later in a labelled conversation carries
  the label only when someone puts it on that reply.
- Each message once: a message written, skipped or passed over is handled, and taking its
  label off, trashing it or labelling it again changes nothing (messages do not change, notes
  I). A pull's messages are written by `internalDate`, then by id.

The cursor (opaque compact JSON this module owns, version 1; never a token, a key, a message
id, a subject, a name or an address). It holds digests only (google_api.digest: the first 16
hex digits of the SHA-256 of the message's id), each with a time in whole Unix seconds: the
message's `internalDate`, or, for a message gone when read, the pull's clock.
- `done`: the messages handled. `parts`: a message half written (a run stopped between two of
  its parts), with how many of its parts are written; it goes on from the next when next read.
- Bound: a digest is kept while its time is at most PRUNE_MARGIN (a minute) before the
  listing's `after`, or while the listing still lists it, so the cursor holds the messages of
  the look-back and a day, however many weeks it runs (and, should Google ignore `q`, the
  labelled messages it lists, which are never fetched twice).
- Each draft carries `resume`: the cursor after it, holding too every message the pull passed
  over or skipped, so a run that stops there is picked up at the next draft and reports no
  skip twice.
- The bounds of a pull: its `deadline`, BUDGET requests (**[cairn]** (1 + MAX_PAGES + READS) x
  5: the labels, the pages and the reads, each with its retries; READS full reads cost 3,000 of
  the 6,000 quota units a person's mailbox has a minute, notes K), and READS reads. A rate
  limit still on after 4 retries, or the daily limit, on a read stops the pull too. A pull that
  stops so returns what it handled, with the cursor that goes on from there; one that stops
  before it has handled any message, or before the listing is complete, raises a SourceError,
  so no cursor moves.

Skips (each a fixed sentence without an id; the Skip's ref is the message id): GONE (a 404 on
a message listed a moment ago), NO_TEXT, TOO_LARGE (Gmail's answer past MESSAGE_BYTES, 8 MB),
TOO_LONG (more than MAX_PARTS parts), TANGLED (a part tree deeper than MAX_DEPTH, 100, or of
more than MAX_MIME_PARTS parts, 1,000, or nested deeper than its JSON can be parsed) and
UNREADABLE (any other failure while one message is made into drafts: one message never
stops the source).

When Google says no (a message never holds a token, a body, an id or an address)
- 401, or a 403 that is not a rate limit or the daily limit: access refused, naming the scope,
  the delegation, the API and an administrator's policy to check. Not retried.
- 429, or a 403 whose reason is rateLimitExceeded or userRateLimitExceeded: waited out
  (google_api: 2^n seconds and up to one more, n from 0) at most 4 times. dailyLimitExceeded
  is not retried. Either, still on, fails the labels or the listing, and stops the reads.
- 404 on the labels or the listing: no mailbox to read (check subject). A redirect (never
  followed), 5xx, another 4xx, a network error (by its class), a body not JSON, or an answer
  not of the documented shape: a SourceError. An error's body that is not JSON is read by its
  status alone. A failed pull returns nothing.

What it writes, for each message, into the config's project
- `title`: the Subject header with its RFC 2047 encoded words decoded (an encoded word that
  cannot be decoded is left as it came; only a header's first HEADER_MAX characters, 16,384,
  are read, here and for a person's name), on one line, cut at a word to TITLE_MAX characters
  and TITLE_BYTES bytes as a heading is stored; NO_SUBJECT when it is missing or empty.
- `topic`: the subject without its reply and forward prefixes (PREFIXES: Re, Fwd, Fw, AW, WG,
  each with a colon, repeated or mixed, any case; NO_SUBJECT when nothing is left), cut at a
  word so its slug leaves room for the month and ", part NN" in the compiler's 64, then a
  comma and the UTC month it arrived in: "Budget review, 2026-11". So the labelled messages of
  one conversation in one month share a page, and a subject that comes back every week starts
  a page a month. **[cairn]** A subject whose slug keeps nothing (no ASCII letter or digit)
  gets TAG hex digits of its own digest before the month, so it does not share a page with
  every other such subject of the month.
- `text`: "# <title>", a blank line, the facts, a blank line, then the body; nothing after it.
  The facts, one list item each, left out when there is nothing to say: "- Arrived:
  YYYY-MM-DD HH:MM UTC" (internalDate), "- From: ", "- To: ", "- Cc: " (each person by
  display name, decoded; one with no name, or whose name holds "@", as the domain of their
  address; MAX_SHOWN at most, then "and N more"), "- Attachments: " (the filename of each part
  that has one, MAX_SHOWN at most, a name holding "@" counted, not shown). Attachments are
  never fetched. "Holds "@"" is cairn.sources.names's check, so an at sign of another width
  (U+FF20, U+FE6B) counts too. No email address is in the title's facts. An address in the
  subject or the body is the sender's text: the workspace's note guard decides on it as on
  any note.
- The body: the part tree walked depth first, to MAX_DEPTH levels (else TANGLED); the first
  `text/plain` part that is not an attachment (no filename) and holds words; else the first
  such `text/html` part, through html_to_text; else the message is NO_TEXT. Data is base64url,
  with or without padding, decoded as UTF-8 with replacement; a part whose data is held apart
  (an `attachmentId`) is not read. **[cairn]** Line ends become "\\n", and blank lines at
  either end go; nothing else of the text changes.
- A body longer than a note may be is cut into parts of at most PASSAGES (1,500) passages as
  cairn.store.usage counts them (the facts among the first part's), between two passages. A
  later part is "# <title>, part N", a blank line, then its text; its topic ends ", part N".
  Each part also fits a page by its characters (K4: cairn.sources.fit, a page's bounds read
  when the drafts are made, the facts counted in the first): a part ends before the passage
  that would take it past them, and a passage longer than a page holds is first cut into
  passages that fit (at a line's end, else at a space; a fence closed and opened again), so
  the parts hold the whole body. A part still past a page's bounds (facts no message has) is
  cut at its end with NOTE_CUT.
- `source_ref`: "gmail:<message id>", and "/N" from the second part. `label`: "gmail".
"""

import asyncio
import binascii
import json
import random
import re
import unicodedata
from base64 import urlsafe_b64decode
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.utils import getaddresses
from typing import Any
from urllib.parse import quote

import httpx

from cairn.sources import (
    Clock,
    Held,
    NoteDraft,
    Pulled,
    Skip,
    SourceError,
    SourceKind,
    fit,
    names,
)
from cairn.sources.config import check_env_name, check_projects
from cairn.sources.google_api import (
    EMAIL,
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
from cairn.sources.google_auth import (
    AuthorizedUser,
    Credential,
    ServiceAccount,
    access_token,
    load_credential,
)
from cairn.sources.markup import html_to_text
from cairn.sources.timing import limit
from cairn.spans import Span, chunk
from cairn.store.memory import FIELD_MAX
from cairn.store.usage import PASSAGE_KINDS, path_bytes

DEFAULT_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = [SCOPE]
SERVICE = "Gmail"
API = "the Gmail API (v1)"
KIND_NAME = "gmail"
VIA = LABEL = "gmail"
ORIGIN = "gmail:"
DEFAULT_PROJECT = "mail"
SETTINGS = ("credentials_env", "subject", "label", "lookback_days", "projects", "base_url")
LOOKBACK_DAYS = (30, 1, 365)  # the default, the least, the most
LABEL_MAX = 200
GMAIL_OWN = frozenset(
    {"INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT", "CHAT"}
)
CATEGORY = "CATEGORY_"
LABELS, LIST, GET = "labels.list", "messages.list", "messages.get"
PAGE = 500  # messages.list's most
MAX_PAGES = 100  # of one pull's listing: 50,000 labelled messages
READS = 50  # messages.get of one pull, in full
# R3: a source with others of its account on looks at a message's labels (format=metadata)
# before it reads it in full. LOOKS: looks at messages it does not know yet, each pull:
# the NEWEST newest of them, then the rest of LOOKS taken in turn from where the last pull's
# turn stopped (the cursor's `sweep`), so new mail is looked at in the first pull after it
# and every other message it does not know within a bounded number of pulls, however many
# are held; RECHECKS: looks at messages it holds, each pull, those looked at longest ago
# first; HELD_KEPT: messages held in the cursor (beyond it, one more held message is not
# kept and is looked at again as an unknown one, in its turn).
LOOKS = 200
NEWEST = 50
RECHECKS = 100
HELD_KEPT = 500
BUDGET = (1 + MAX_PAGES + READS + LOOKS + RECHECKS) * (RETRIES + 1)
MARGIN = timedelta(days=1)  # the listing's `after:` is this much before the line
PRUNE_MARGIN = 60  # seconds a digest is kept before the listing's `after`
MESSAGE_BYTES = 8 * 1024 * 1024  # the most of one message's answer cairn reads
PASSAGES = 1_500  # of one part, as cairn counts them (cairn.store.usage.passages_of)
MAX_PARTS = 20
MAX_SHOWN = 10  # people, or attachments, named in one fact
# A message's part tree is walked no deeper than MAX_DEPTH and over no more than
# MAX_MIME_PARTS parts. A mail client nests two or three levels for each message forwarded
# as an attachment, so 100 levels is some thirty forwards inside one another; and Gmail
# takes a message of at most 25 MB, so 1,000 parts would be a thousand attachments of 25 KB.
MAX_DEPTH = 100
MAX_MIME_PARTS = 1_000
SLUG_MAX = 64  # cairn.compiler.SLUG_MAX (the compiler imports the store, which imports this)
MONTH_ROOM = len("-yyyy-mm")
PART_ROOM = len("-part-nn")
TOPIC_TEXT_MAX = FIELD_MAX["topic"] - len(", ffffff, 2026-11, part 20")
TITLE_MAX = 150  # characters, inside the store's 200
HEADER_MAX = 16_384  # characters of a header that are decoded: no real subject or name is near
TITLE_BYTES = 200  # as a heading is stored, of the 400 a passage's headings may take
TAG = 6  # hex digits of a subject's digest, for a subject whose slug keeps nothing
NO_SUBJECT = "(no subject)"
CURSOR_VERSION = 1
DIGEST = re.compile(r"[0-9a-f]{16}")
MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,256}")  # never "." or "..", which a URL resolves
LABEL_ID = re.compile(r"\S{1,256}")
INTERNAL_DATE = re.compile(r"[0-9]{1,15}")
LATEST_MS = 253_402_300_799_999  # 9999-12-31T23:59:59.999Z
BASE64URL = re.compile(r"[A-Za-z0-9_-]*={0,2}")
PREFIXES = re.compile(r"^\s*(?:(?:re|fwd|fw|aw|wg)\s*:\s*)+", re.IGNORECASE)
DAILY = "dailyLimitExceeded"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

NOT_FOUND = (
    "Gmail has no label named as the label setting says in the mailbox read: make that label"
    " in Gmail, or set label to its name exactly as Gmail shows it (a nested label with its"
    " parents in front, parent/child)"
)
AMBIGUOUS = (
    "Gmail has more than one label whose name differs from the label setting only in case: set"
    " label to the name of one of them exactly as Gmail shows it"
)
NOT_YOURS = (
    "the label setting names one of Gmail's own labels, which this server never reads: make a label"
    " of your own in Gmail, put it on the messages to read, and name it in label"
)

AGAIN = "it is not read again"
GONE = (
    "gone: Gmail no longer had it when this server came to read it (deleted since it was listed);"
    f" {AGAIN}"
)
NO_TEXT = (
    "no text to read: it has no text part of its own, only attachments, which this server does not"
    f" read; {AGAIN}"
)
TOO_LARGE = f"too large: Gmail's answer for it is over 8 MB, more than this server reads; {AGAIN}"
TOO_LONG = (
    f"too long: its text would take more than {MAX_PARTS} notes of {PASSAGES:,} passages each,"
    f" each within what a page holds; {AGAIN}"
)
NOTE_CUT = "The rest of this note is left out: it is longer than a page holds."
TANGLED = (
    f"too tangled: its parts are nested more than {MAX_DEPTH} deep or number more than"
    f" {MAX_MIME_PARTS:,}, which no mail this server reads reaches; {AGAIN}"
)
UNREADABLE = (
    f"unreadable: this server failed on what Gmail gave for it and could not make a note of it;"
    f" {AGAIN}"
)

SWITCH_ON = (
    "Switching it on reads the messages that arrive from then on and carry the label, each"
    " once: nothing that arrived before, even when it is labelled later."
)


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
    checked: dict[str, Any] = {
        "credentials_env": check_env_name(
            config,
            "credentials_env",
            kind=KIND_NAME,
            holds="the Google credential",
            secret="credential",
            looks_like=looks_like_a_credential,
        )
    }
    subject = config.get("subject")
    if subject is not None:
        if isinstance(subject, str) and looks_like_a_credential(subject):
            raise ValueError("subject holds what looks like a credential, not an email address")
        if not isinstance(subject, str) or len(subject) > 254 or not EMAIL.fullmatch(subject):
            raise ValueError(
                "subject is one email address: the person whose mailbox a service account reads"
            )
        checked["subject"] = subject
    checked["label"] = _check_label(config.get("label"))
    default, least, most = LOOKBACK_DAYS
    days = config.get("lookback_days", default)
    if isinstance(days, bool) or not isinstance(days, int) or not least <= days <= most:
        raise ValueError(
            f"lookback_days is a whole number from {least} to {most}: how many days after a"
            " message arrived its label is still noticed"
        )
    checked["lookback_days"] = days
    checked["projects"] = check_projects(
        config.get("projects", []), kind="gmail", looks_like=looks_like_a_credential
    )
    checked["base_url"] = check_base_url(config.get("base_url", DEFAULT_BASE_URL), API)
    return checked


def _check_label(label: object) -> str:
    if label is None:
        raise ValueError(
            f"a {KIND_NAME} source needs label: the name of the Gmail label, of the person's"
            " own, that marks a message for this server"
        )
    if isinstance(label, str) and looks_like_a_credential(label):
        raise ValueError("label holds what looks like a credential, not a label's name")
    if (
        not isinstance(label, str)
        or not 1 <= len(label) <= LABEL_MAX
        or any(unicodedata.category(ch) == "Cc" for ch in label)
    ):
        raise ValueError(
            f"label is the name of one Gmail label, as Gmail shows it (1 to {LABEL_MAX}"
            " characters, no control character; a nested label with its parents in front,"
            " parent/child)"
        )
    if label.upper() in GMAIL_OWN or label.upper().startswith(CATEGORY):
        raise ValueError(
            "label names one of Gmail's own labels, which would read a whole mailbox: make a"
            " label of your own in Gmail and name it here"
        )
    return label


def describe(config: Mapping[str, Any]) -> str:
    """What a source reads, for GET /sources: whose mailbox, which label, from where, and for
    how long a label is noticed. Never the variable or a credential."""
    whose = config.get("subject") or "the person who authorised its refresh token"
    days = config.get("lookback_days", LOOKBACK_DAYS[0])
    return (
        f"Messages in the mailbox of {whose} that carry the Gmail label “{config['label']}”, at"
        f" {config['base_url']}; each is read once, when it is labelled within {days}"
        f" day{'s' if days != 1 else ''} of arriving"
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
    jitter: Callable[[], float] = random.random,
) -> "GmailSource":
    """The source a stored config describes, with its credential read from `env`.
    SourceError names a variable or a setting, never a value."""
    credential = load_credential(config["credentials_env"], env)
    subject = config.get("subject")
    if isinstance(credential, AuthorizedUser) and subject is not None:
        raise SourceError(
            f"source {name!r} reads with a refresh token, which reads the mailbox of the person"
            " who authorised it: remove subject from its config"
        )
    if isinstance(credential, ServiceAccount) and subject is None:
        raise SourceError(
            f"source {name!r} reads with a service account, which has no mailbox of its own and"
            " cannot be given one: set subject to the person whose mail it reads (domain-wide"
            " delegation)"
        )
    return GmailSource(
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


def mailbox(subject: str | None) -> str:
    """The `userId` of every path: the subject's address, percent-encoded, or "me"."""
    return quote(subject, safe="") if subject else "me"


def after(since: datetime, now: datetime, lookback_days: int) -> int:
    """The listing's `after:`, in seconds: a day before the later of `since` and the line of
    the look-back."""
    line = max(since, now - timedelta(days=lookback_days))
    return int((line - MARGIN).timestamp() // 1)


def listing(label_id: str, after_: int) -> dict[str, str]:
    """messages.list's parameters, beside which each page's token goes."""
    return {"labelIds": label_id, "q": f"after:{after_}", "maxResults": str(PAGE)}


def _utc_now() -> datetime:
    return datetime.now(UTC)


# The cursor


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class Cursor:
    """What a pull starts from (see the module docstring): the digests of the messages it
    handled, and of those half written with how many parts are, each with its time."""

    done: dict[str, int] = field(default_factory=dict)
    parts: dict[str, list[int]] = field(default_factory=dict)  # digest: [time, parts written]
    # R3: digest: [time it arrived, when its labels were last looked at]; at most HELD_KEPT
    held: dict[str, list[int]] = field(default_factory=dict)
    sweep: str | None = None  # R3: the digest of the last message looked at in turn

    def pruned(self, after_: int, listed: set[str]) -> "Cursor":
        """What a listing from `after_` may still return, and what it listed (digests). A held
        message no longer listed (its label gone, or out of the window) is let go."""
        least = after_ - PRUNE_MARGIN
        return Cursor(
            done={d: t for d, t in self.done.items() if t >= least or d in listed},
            parts={d: p for d, p in self.parts.items() if p[0] >= least or d in listed},
            held={d: h for d, h in self.held.items() if d in listed},
            sweep=self.sweep,
        )

    def encode(self) -> str:
        state: dict[str, Any] = {
            "v": CURSOR_VERSION,
            "done": {d: self.done[d] for d in sorted(self.done)},
            "parts": {d: self.parts[d] for d in sorted(self.parts)},
        }
        if self.held:  # kept only when there is one: a cursor before R3 reads the same
            state["holds"] = {d: self.held[d] for d in sorted(self.held)}
        if self.sweep is not None:
            state["sweep"] = self.sweep
        return json.dumps(state, separators=(",", ":"))

    @classmethod
    def decode(cls, text: str) -> "Cursor":
        broken = SourceError(
            "the stored cursor is not one this Gmail source wrote: remove the source and add it"
            " again to start afresh"
        )
        try:
            state = json.loads(text)
        except (ValueError, RecursionError):
            raise broken from None
        if not isinstance(state, dict) or state.get("v") != CURSOR_VERSION:
            raise broken
        done, parts = state.get("done"), state.get("parts")
        if not isinstance(done, dict) or not isinstance(parts, dict):
            raise broken
        if not all(DIGEST.fullmatch(d) for d in [*done, *parts]):
            raise broken
        if not all(_count(t) for t in done.values()):
            raise broken
        if not all(
            isinstance(p, list) and len(p) == 2 and _count(p[0]) and _count(p[1]) and p[1] > 0
            for p in parts.values()
        ):
            raise broken
        # "holds": digests. A cursor written while the held map was keyed by message id
        # ("held") is read with that map dropped: those messages are looked at again.
        held = state.get("holds", {})
        if not isinstance(held, dict) or len(held) > HELD_KEPT:
            raise broken
        if not all(
            isinstance(d, str) and DIGEST.fullmatch(d) and isinstance(h, list)
            and len(h) == 2 and _count(h[0]) and _count(h[1])
            for d, h in held.items()
        ):  # fmt: skip
            raise broken
        sweep = state.get("sweep")
        if sweep is not None and not (isinstance(sweep, str) and DIGEST.fullmatch(sweep)):
            raise broken
        return cls(
            done=dict(done),
            parts={d: list(p) for d, p in parts.items()},
            held={d: list(h) for d, h in held.items()},
            sweep=sweep,
        )


# A message


@dataclass(frozen=True)
class Mail:
    """A message as messages.get gave it: its id, when it arrived (internalDate, in UTC), its
    label ids, and its payload, the part tree."""

    id: str
    at: datetime
    labels: tuple[str, ...]
    payload: Mapping[str, Any] = field(repr=False)

    @property
    def seconds(self) -> int:
        return int(self.at.timestamp() // 1)


def _shape(what: str, why: str = "") -> SourceError:
    said = f" ({why})" if why else ""
    return SourceError(f"{SERVICE}'s answer to {what} is not the documented shape{said}")


def read_message(message_id: str, body: bytes) -> Mail:
    """The message in a 200's body; SourceError when it is not JSON or not the documented
    shape, never quoting it; _Tangled when it is nested deeper than JSON is parsed."""
    try:
        item = json.loads(body)
    except RecursionError:  # nested deeper than the parser goes: a part tree, for one
        raise _Tangled from None
    except ValueError:
        raise SourceError(f"{SERVICE}'s answer to {GET} is not JSON") from None
    if not isinstance(item, dict):
        raise _shape(GET)
    stamp = item.get("internalDate")
    if isinstance(stamp, int) and not isinstance(stamp, bool):
        stamp = str(stamp)
    if not isinstance(stamp, str) or not INTERNAL_DATE.fullmatch(stamp):
        raise _shape(GET, "no usable internalDate")
    if int(stamp) > LATEST_MS:
        raise _shape(GET, "no usable internalDate")
    labels = item.get("labelIds", [])
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        raise _shape(GET, "labelIds is not a list of ids")
    payload = item.get("payload")
    if not isinstance(payload, dict):
        raise _shape(GET, "no payload")
    at = EPOCH + timedelta(milliseconds=int(stamp))
    return Mail(message_id, at, tuple(labels), payload)


class _Tangled(Exception):
    """A message nested past MAX_DEPTH, or of more than MAX_MIME_PARTS parts."""


def _parts(part: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """The part tree, depth first (each part before its children), walked with a stack of
    its own: _Tangled past MAX_DEPTH levels or MAX_MIME_PARTS parts."""
    stack: list[tuple[Mapping[str, Any], int]] = [(part, 1)]
    walked = 0
    while stack:
        current, depth = stack.pop()
        walked += 1
        if depth > MAX_DEPTH or walked > MAX_MIME_PARTS:
            raise _Tangled
        yield current
        children = current.get("parts")
        if isinstance(children, list):
            stack += [(c, depth + 1) for c in reversed(children) if isinstance(c, dict)]


def _filename(part: Mapping[str, Any]) -> str:
    name = part.get("filename")
    return name if isinstance(name, str) else ""


def _data(part: Mapping[str, Any]) -> str | None:
    """A part's text, base64url with or without padding decoded as UTF-8 with replacement;
    None when its body holds no data."""
    body = part.get("body")
    data = body.get("data") if isinstance(body, dict) else None
    if data is None:
        return None
    if not isinstance(data, str) or not BASE64URL.fullmatch(data) or len(data.rstrip("=")) % 4 == 1:
        raise _shape(GET, "a part's data is not base64url")
    try:
        raw = urlsafe_b64decode(data.rstrip("=") + "=" * (-len(data.rstrip("=")) % 4))
    except (binascii.Error, ValueError):
        raise _shape(GET, "a part's data is not base64url") from None
    return raw.decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    return re.sub(r"\r\n?", "\n", text).strip("\n")


def body_of(payload: Mapping[str, Any]) -> str | None:
    """The message's text: its first text/plain part that is not an attachment and holds
    words, else its first such text/html part as text; None when it has neither."""
    found: dict[str, str] = {}
    for part in _parts(payload):
        mime = part.get("mimeType")
        mime = mime.lower() if isinstance(mime, str) else ""
        if mime not in ("text/plain", "text/html") or mime in found or _filename(part):
            continue
        text = _data(part)
        if text is None:
            continue
        text = _clean(text) if mime == "text/plain" else html_to_text(text)
        if text.strip():
            found[mime] = text
        if "text/plain" in found:
            break
    return found.get("text/plain") or found.get("text/html")


def _headers(payload: Mapping[str, Any]) -> dict[str, str]:
    """The top part's headers by name in lower case, the first of each."""
    found: dict[str, str] = {}
    given = payload.get("headers")
    for header in given if isinstance(given, list) else ():
        if not isinstance(header, dict):
            continue
        name, value = header.get("name"), header.get("value")
        if isinstance(name, str) and isinstance(value, str):
            found.setdefault(name.lower(), value)
    return found


def _one_line(value: str) -> str:
    return " ".join("".join(" " if unicodedata.category(c) == "Cc" else c for c in value).split())


def _decoded(value: str) -> str:
    """A header's RFC 2047 encoded words decoded; the value as it came when they cannot be.
    Only its first HEADER_MAX characters are read, up to the last white space among them so
    no encoded word is cut: the standard library's decoding takes time growing with the
    square of a header's length."""
    if len(value) > HEADER_MAX:
        value = value[:HEADER_MAX]
        value = value.rsplit(maxsplit=1)[0] if len(value.split(maxsplit=1)) > 1 else value
    if "=?" not in value:
        return value
    try:
        return str(make_header(decode_header(value)))
    except (HeaderParseError, LookupError, UnicodeError, ValueError):
        return value


def _cut(text: str, too_long: Callable[[str], bool], most: int) -> str:
    """`text` cut at a word (or a character, when it is one word) until it is not too long,
    never ending on a space or a comma. `most` is the most characters it may keep: the text
    is first cut to one more than that, so each word taken off measures a bounded text and
    the work does not grow with the text's length."""
    text = text[: most + 1]
    while text and too_long(text):
        shorter = text.rsplit(" ", 1)[0] if " " in text.strip() else text[:-1]
        text = shorter.rstrip(" ,") or shorter[:-1]
    return text


def subject_of(header: str | None) -> str:
    """The Subject header decoded, on one line; "" when there is none."""
    return _one_line(_decoded(header)) if header else ""


def title_of(header: str | None) -> str:
    """A message's title: its subject, cut to what a title and its heading may hold."""
    title = _cut(
        subject_of(header),
        lambda t: len(t) > TITLE_MAX or path_bytes([t]) > TITLE_BYTES,
        TITLE_MAX,
    )
    return title or NO_SUBJECT


def _full_slug(text: str) -> str:
    """The slug cairn.compiler.slugify makes of `text`, before it is cut at SLUG_MAX."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def topic(subject: str, at: datetime) -> str:
    """The page a message goes on: its subject without reply and forward prefixes, cut so the
    slug keeps the month and a part's number, and the UTC month it arrived in."""
    bare = PREFIXES.sub("", _one_line(subject)).strip() or NO_SUBJECT
    month = at.astimezone(UTC).strftime("%Y-%m")
    tag = ""
    if not _full_slug(bare):
        tag = f", {digest(bare)[:TAG]}"
    room = SLUG_MAX - MONTH_ROOM - PART_ROOM - (TAG + 1 if tag else 0)
    cut = _cut(bare, lambda t: len(_full_slug(t)) > room or len(t) > TOPIC_TEXT_MAX, TOPIC_TEXT_MAX)
    return f"{cut or NO_SUBJECT}{tag}, {month}"


def _shown(named: list[str], hidden: int) -> str | None:
    """At most MAX_SHOWN names, then how many more."""
    more = hidden + max(len(named) - MAX_SHOWN, 0)
    if not named:
        return f"{more} without a name" if more else None
    return ", ".join(named[:MAX_SHOWN]) + (f" and {more} more" if more else "")


def people(header: str | None) -> str | None:
    """The people of an address header by display name; one with no name, or whose name
    holds "@", as the domain of their address. Never an address."""
    if not header:
        return None
    named, hidden = [], 0
    for name, address in getaddresses([header]):
        shown = names.shown(_decoded(name)) if name else None
        if shown is None:
            shown = names.shown(address.rpartition("@")[2] if "@" in address else "")
        if shown:
            named.append(shown)
        elif address:
            hidden += 1
    return _shown(named, hidden)


def attachments(payload: Mapping[str, Any]) -> str | None:
    """The names of the parts that have one; a name holding "@" is counted, not shown."""
    named, hidden = [], 0
    for part in _parts(payload):
        name = _one_line(_filename(part))
        if name and not names.holds_an_address(name):
            named.append(name)
        elif name:
            hidden += 1
    return _shown(named, hidden)


def facts(mail: Mail) -> list[str]:
    """The lines under a message's heading: when it arrived, who wrote to whom, and the names
    of its attachments."""
    headers = _headers(mail.payload)
    said = [f"- Arrived: {mail.at:%Y-%m-%d %H:%M} UTC"]
    for fact, header in (("From", "from"), ("To", "to"), ("Cc", "cc")):
        named = people(headers.get(header))
        if named:
            said.append(f"- {fact}: {named}")
    files = attachments(mail.payload)
    if files:
        said.append(f"- Attachments: {files}")
    return said


def split(text: str, first: int, room: int | None = None) -> list[str] | None:
    """The text cut into the bodies of its parts, each ending between two passages as cairn
    counts them: the first holds at most `first`, the others PASSAGES (or a page's claims,
    when fewer). Each also holds at most a page's characters in its passages, the first
    `room` of them (K4: cairn.sources.fit, read now); a passage longer than a page holds is
    cut into passages that fit first (fit.opened_up), nothing of it lost. None past
    MAX_PARTS."""
    passages, chars = min(PASSAGES, fit.claims()), fit.chars()
    text = fit.opened_up(text, chars)
    lines = text.splitlines(keepends=True)
    # A blank first line, as under the heading and the facts: never front matter.
    spans = chunk("cap_000000000000", "\n" + text)
    groups: list[list[Span]] = [[]]
    count, budget = 0, first
    held, most = 0, chars if room is None else room
    for span in spans:
        passage = span.kind in PASSAGE_KINDS
        size = len(span.text) if passage else 0
        over = held + size > most and (count > 0 or len(groups) == 1)
        if passage and (count >= budget or over):
            if len(groups) == MAX_PARTS:
                return None
            groups.append([])
            count, budget, held, most = 0, passages, 0, chars
        groups[-1].append(span)
        count += passage
        held += size
    bodies = []
    for index, group in enumerate(groups):
        start = 0 if index == 0 else group[0].start_line - 2
        end = len(lines) if index == len(groups) - 1 else groups[index + 1][0].start_line - 2
        bodies.append("".join(lines[start:end]).strip("\n"))
    return bodies


def drafts(mail: Mail, project: str) -> list[NoteDraft] | str:
    """A message's drafts, the first with its heading and facts, later ones with their own
    heading; or the reason it has none (NO_TEXT, TOO_LONG, TANGLED)."""
    try:
        body = body_of(mail.payload)
        head = facts(mail)
    except _Tangled:
        return TANGLED
    if body is None:
        return NO_TEXT
    room = fit.chars() - sum(map(len, head))  # the facts are passages of the first part
    bodies = split(body, min(PASSAGES, fit.claims()) - len(head), room)
    if bodies is None:
        return TOO_LONG
    subject = _headers(mail.payload).get("subject")
    title = title_of(subject)
    page = topic(subject_of(subject), mail.at)
    made = []
    for index, text in enumerate(bodies):
        number = index + 1
        if number == 1:
            written = f"# {title}\n\n" + "\n".join(head) + f"\n\n{text}\n"
        else:
            written = f"# {title}, part {number}\n\n{text}\n"
        written = fit.fitted(written, NOTE_CUT)
        suffix = f"/{number}" if number > 1 else ""
        made.append(
            NoteDraft(
                project=project,
                title=title,
                text=written,
                source_ref=f"{ORIGIN}{mail.id}{suffix}",
                label=LABEL,
                topic=page + (f", part {number}" if number > 1 else ""),
            )
        )
    return made


# Wording of a refusal


def refusal(reply: Reply, what: str, variable: str) -> str:
    """A SourceError's message for an answer that is not 200, from its status and reasons
    alone."""
    status = reply.status
    if reply.limited:
        return (
            f"{SERVICE} still answered with a rate limit to {what} after {RETRIES} retries:"
            " the next run goes on"
        )
    if DAILY in reply.reasons:
        return (
            f"{SERVICE} answered {what} that the Cloud project's daily limit is reached ({DAILY}),"
            " a cap set on its quota: it is not retried; raise or remove the cap in the Cloud"
            " console, and the next run goes on"
        )
    if status in (401, 403):
        return (
            f"{SERVICE} refused access (HTTP {status}) to {what}: check that the credential in"
            f" {variable} carries the scope {SCOPE} (for a service account, in its domain-wide"
            " delegation), that the Gmail API is enabled in its Cloud project, and that no"
            " administrator's policy keeps apps from Gmail"
        )
    if status == 404:
        return (
            f"{SERVICE} found no mailbox to read (HTTP 404 to {what}): check subject, the person"
            " whose mail is read"
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


# The source


class _Stopped(Exception):
    """A read that must end the pull's reading: a rate limit still on, the daily limit, or
    the pull's bounds. Its message is a SourceError's, for a pull that handled nothing."""


@dataclass
class _Progress:
    """What a pull has done so far: the cursor it stands at, and its drafts and skips."""

    state: Cursor
    drafts: list[NoteDraft] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    handled: int = 0
    held: list[Held] = field(default_factory=list)
    expired: int = 0  # held messages found past the look-back's line, left unwritten

    def passed(self, key: str, seconds: int) -> None:
        self.state.done[key] = seconds
        self.state.parts.pop(key, None)
        self.handled += 1

    def skip(self, message_id: str, seconds: int, reason: str) -> None:
        self.passed(digest(message_id), seconds)
        self.skipped.append(Skip(message_id, reason))

    def write(self, mail: Mail, made: list[NoteDraft]) -> None:
        """The parts not yet written, each with the cursor that stands after it."""
        key = digest(mail.id)
        written = self.state.parts.get(key, [0, 0])[1]
        for number in range(written + 1, len(made) + 1):
            if number == len(made):
                self.state.parts.pop(key, None)
                self.state.done[key] = mail.seconds
            else:
                self.state.parts[key] = [mail.seconds, number]
            self.drafts.append(replace(made[number - 1], resume=self.state.encode()))
        if written >= len(made):  # never more parts written than it has: done
            self.passed(key, mail.seconds)


class GmailSource:
    """Reads the labelled messages of one mailbox (see the module docstring)."""

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
        self.label = config["label"]
        self.lookback_days = config["lookback_days"]
        self.project = config["projects"][0] if config["projects"] else ""
        self._credential = credential
        self._token: str | None = None
        self._http = http
        self._clock = clock or _utc_now
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter
        self.deadline = deadline
        self.since = since
        self._users = f"{self.base_url}/users/{mailbox(self.subject)}"
        # R3: the selectors of the other sources of this account that are on (the runner
        # sets them): a message carrying one is held, not counted as done.
        self.holding: frozenset[str] = frozenset()
        self._names: dict[str, str] = {}  # a user label's id: its name, case-folded

    def __repr__(self) -> str:
        return f"GmailSource({self.base_url!r}, name={self.name!r})"

    @property
    def secrets(self) -> tuple[str, ...]:
        """The credential's secrets and, once the pull has it, the access token."""
        token = (self._token, f"Bearer {self._token}") if self._token else ()
        return (*self._credential.secrets, *token)

    async def pull(self, cursor: str | None) -> Pulled:
        start = Cursor() if cursor is None else Cursor.decode(cursor)
        now = self._clock().astimezone(UTC)
        since = (self.since or now).astimezone(UTC)
        line = max(since, now - timedelta(days=self.lookback_days))
        after_ = after(since, now, self.lookback_days)
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
            deadline=self.deadline, budget=BUDGET, jitter=self._jitter, limit=MESSAGE_BYTES,
        )  # fmt: skip
        try:
            label_id = await self._label(reader)
            listed = await self._listed(reader, listing(label_id, after_))
        except Spent as spent:
            raise SourceError(
                f"the labelled messages could not be listed within one pull's bounds ({spent}),"
                " so the cursor cannot move: the next run tries again"
            ) from None

        progress = _Progress(
            Cursor(
                dict(start.done),
                {k: list(v) for k, v in start.parts.items()},
                {m: list(h) for m, h in start.held.items()} if self.holding else {},
                start.sweep if self.holding else None,
            )
        )
        seconds_now = int(now.timestamp() // 1)
        held_now = progress.state.held
        due: list[Mail] = []
        candidates = [m for m in reversed(listed) if digest(m) not in start.done]
        if self.holding:  # R3: the ones it does not hold, oldest first, then the held ones
            fresh = [m for m in candidates if digest(m) not in held_now]
            again = sorted(
                (m for m in candidates if digest(m) in held_now),
                key=lambda m: held_now[digest(m)][1],
            )
            chosen, sweep = self._chosen(fresh, start.sweep)
            progress.state = replace(progress.state, sweep=sweep)
            asked = [m for m in fresh if m in chosen] + again[:RECHECKS]  # oldest first
            looked = await self._looked(reader, asked, progress, line)
            candidates = [m for m in asked if m in looked]
        for message_id in candidates[:READS]:
            key = digest(message_id)
            try:
                got = await self._message(reader, message_id)
            except _Stopped as stopped:
                if progress.handled or due:
                    break
                raise SourceError(str(stopped)) from None
            was_held = held_now.pop(key, None) is not None  # read in full: not held now
            if isinstance(got, str):
                progress.skip(message_id, seconds_now, got)
            elif "DRAFT" in got.labels or got.at < line:
                progress.expired += was_held and got.at < line
                progress.passed(key, got.seconds)
            else:
                due.append(got)

        written: list[tuple[Mail, list[NoteDraft]]] = []
        for mail in due:
            try:
                made = drafts(mail, self.project)
            except SourceError:
                raise
            except Exception:  # one message cairn fails on must not stop the source for good
                made = UNREADABLE
            if isinstance(made, str):
                progress.skip(mail.id, mail.seconds, made)
            else:
                carried = tuple(sorted({f"label:{self._names[x]}" for x in mail.labels
                                        if x in self._names}))  # fmt: skip
                if self.holding.intersection(carried):  # labelled between the look and the read
                    if digest(mail.id) in held_now or len(held_now) < HELD_KEPT:
                        held_now[digest(mail.id)] = [mail.seconds, seconds_now]
                    link = f"{ORIGIN}{mail.id}"
                    progress.held.append(Held(made[0].title, link, self.project, carried))
                    continue
                written.append((mail, [replace(d, selectors=carried) for d in made]))
        for mail, made in sorted(written, key=lambda pair: (pair[0].at, pair[0].id)):
            progress.write(mail, made)
        digests = {digest(m) for m in listed}
        progress.expired += sum(  # held, no longer listed, and before the line: out of the window
            1 for d, h in held_now.items() if d not in digests and h[0] < line.timestamp()
        )
        following = progress.state.pruned(after_, digests)
        return Pulled(
            progress.drafts, progress.skipped, following.encode(), held=progress.held,
            held_expired=progress.expired,
        )  # fmt: skip

    @staticmethod
    def _chosen(fresh: list[str], sweep: str | None) -> tuple[set[str], str | None]:
        """R3: which of `fresh` (the messages not known, oldest first) a pull looks at: the
        NEWEST newest, then the rest of LOOKS in turn, after the one `sweep` names (from the
        oldest when it is gone), wrapping round; and the digest of the last so taken."""
        newest = fresh[-NEWEST:] if NEWEST else []
        rest = fresh[: len(fresh) - len(newest)]
        keys = [digest(m) for m in rest]
        begin = keys.index(sweep) + 1 if sweep in keys else 0
        turn = (rest[begin:] + rest[:begin])[: max(LOOKS - len(newest), 0)]
        return {*newest, *turn}, digest(turn[-1]) if turn else sweep

    async def _looked(
        self, reader: Reader, ids: list[str], progress: "_Progress", line: datetime
    ) -> set[str]:
        """R3: look at the labels of each of `ids` (format=metadata) and keep the ones to
        read in full: those that carry no selector of `holding`. One that does is held
        (progress.held, and in the cursor while there is room, HELD_KEPT); one that arrived
        before `line`, or a draft, is passed; one gone is skipped. A look that must stop the
        pull's reading ends the looking: what is left waits for the next pull."""
        held_now = progress.state.held
        seconds_now = int(self._clock().timestamp() // 1)
        kept: set[str] = set()
        for message_id in ids:
            try:
                got = await self._look(reader, message_id)
            except _Stopped:
                break
            if isinstance(got, str):
                held_now.pop(digest(message_id), None)
                progress.skip(message_id, seconds_now, got)
                continue
            labels, seconds, subject = got
            carried = tuple(sorted({f"label:{self._names[x]}" for x in labels
                                    if x in self._names}))  # fmt: skip
            at = seconds if seconds is not None else seconds_now
            if "DRAFT" in labels or (seconds is not None and seconds < line.timestamp()):
                was_held = held_now.pop(digest(message_id), None) is not None
                progress.expired += was_held and "DRAFT" not in labels
                progress.passed(digest(message_id), at)
            elif self.holding.intersection(carried):
                if digest(message_id) in held_now or len(held_now) < HELD_KEPT:
                    held_now[digest(message_id)] = [at, seconds_now]
                title = title_of(subject)
                link = f"{ORIGIN}{message_id}"
                progress.held.append(Held(title, link, self.project, carried))
            else:
                kept.add(message_id)
        return kept

    async def _look(
        self, reader: Reader, message_id: str
    ) -> tuple[list[str], int | None, str | None] | str:
        """A message's label ids, when it arrived (None when Gmail does not say) and its
        Subject header, from messages.get with format=metadata (R3); or the reason it is
        skipped. _Stopped when the pull must stop reading; SourceError as _message."""
        url = f"{self._users}/messages/{quote(message_id, safe='')}"
        asked = {"format": "metadata", "metadataHeaders": "Subject"}
        try:
            reply = await reader.get(url, asked, service=SERVICE, what=GET)
        except Spent as spent:
            raise _Stopped(str(spent)) from None
        if reply.status == 404:
            return GONE
        if reply.status != 200:
            if reply.limited or DAILY in reply.reasons:
                raise _Stopped(refusal(reply, GET, self.variable))
            raise SourceError(refusal(reply, GET, self.variable))
        body = reply.body
        labels = body.get("labelIds", []) if isinstance(body, dict) else None
        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            raise _shape(GET, "labelIds is not a list of ids")
        stamp = body.get("internalDate")
        if isinstance(stamp, int) and not isinstance(stamp, bool):
            stamp = str(stamp)
        seconds = (
            int(stamp) // 1000
            if isinstance(stamp, str) and INTERNAL_DATE.fullmatch(stamp)
            and int(stamp) <= LATEST_MS
            else None
        )  # fmt: skip
        payload = body.get("payload")
        headers = _headers(payload) if isinstance(payload, dict) else {}
        return labels, seconds, headers.get("subject")

    # Requests

    async def _label(self, reader: Reader) -> str:
        """The id of the label the config names (see the module docstring)."""
        reply = await reader.get(f"{self._users}/labels", {}, service=SERVICE, what=LABELS)
        if reply.status != 200:
            raise SourceError(refusal(reply, LABELS, self.variable))
        body = reply.body
        labels = body.get("labels", []) if isinstance(body, dict) else None
        if not isinstance(labels, list):
            raise _shape(LABELS)
        for item in labels:
            if not (
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("id"), str)
                and LABEL_ID.fullmatch(item["id"])
            ):
                raise _shape(LABELS, "a label has no usable id or name")
        self._names = {x["id"]: x["name"].casefold() for x in labels if x.get("type") == "user"}
        found = [x for x in labels if x["name"] == self.label]
        if not found:
            folded = self.label.casefold()
            found = [x for x in labels if x["name"].casefold() == folded]
        if not found:
            raise SourceError(NOT_FOUND)
        if len(found) > 1:
            raise SourceError(AMBIGUOUS)
        if found[0].get("type") != "user":
            raise SourceError(NOT_YOURS)
        return found[0]["id"]

    async def _listed(self, reader: Reader, params: dict[str, str]) -> list[str]:
        """The ids of every page of the listing, each once, in the order listed."""
        ids: dict[str, None] = {}
        token: str | None = None
        url = f"{self._users}/messages"
        for _ in range(MAX_PAGES):
            asked = {**params, "pageToken": token} if token else params
            reply = await reader.get(url, asked, service=SERVICE, what=LIST)
            if reply.status != 200:
                raise SourceError(refusal(reply, LIST, self.variable))
            body = reply.body
            if not isinstance(body, dict):
                raise _shape(LIST)
            page, token = body.get("messages", []), body.get("nextPageToken")
            if not isinstance(page, list) or not (token is None or isinstance(token, str)):
                raise _shape(LIST)
            for item in page:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise _shape(LIST, "a message has no usable id")
                if not MESSAGE_ID.fullmatch(item["id"]):
                    raise _shape(LIST, "a message has no usable id")
                ids.setdefault(item["id"])
            if len(ids) > MAX_PAGES * PAGE:  # a page may hold more than asked
                raise SourceError(
                    f"{SERVICE} listed more than {MAX_PAGES * PAGE:,} messages in one pull"
                )
            if not token:
                return list(ids)
        raise SourceError(f"{SERVICE} listed more than {MAX_PAGES} pages of messages in one pull")

    async def _message(self, reader: Reader, message_id: str) -> Mail | str:
        """The message, or the reason it is skipped. _Stopped when the pull must stop reading;
        SourceError when access is refused or Google fails."""
        url = f"{self._users}/messages/{quote(message_id, safe='')}"
        try:
            reply = await reader.get(url, {"format": "full"}, service=SERVICE, what=GET, raw=True)
        except TooLarge:
            return TOO_LARGE
        except Spent as spent:
            raise _Stopped(
                f"no message could be read within one pull's bounds ({spent}), so the cursor"
                " cannot move: the next run tries again"
            ) from None
        if reply.status == 200:
            assert isinstance(reply.body, bytes)
            try:
                return read_message(message_id, reply.body)
            except _Tangled:
                return TANGLED
        if reply.status == 404:
            return GONE
        if reply.limited or DAILY in reply.reasons:
            raise _Stopped(refusal(reply, GET, self.variable))
        raise SourceError(refusal(reply, GET, self.variable))
