"""The use cases the MCP tools, the REST API, the web UI and sources share, the errors they
raise, and the serializers of their answers. Nothing here knows about HTTP: the web package
turns a RequestError into a status. The note guard is enforced below this, in
`Store.add_note`, the one place a note is written, so no caller can store a note it
has not screened; `Memory.note` turns its refusal into Refused (422). A note refused for what it is
raises Malformed (a BadRequest, 400) with a `reason` that quotes nothing of it: EmptyText
for blank text, NoPassage for text with no passage to compile (headings, front matter and
blank lines only: review 7.6) unless the project holds that very note, stored before the
refusal (decision 173: it is that note, "unchanged"), PastBound for a field past
cairn.store.FIELD_MAX (naming the field and its bound), LongHeadings for headings past theirs,
ManyFacts (G1) for more facts than cairn.store.FACTS_MAX (giving the count and the bound).

The access log: each MCP tool call and REST request runs inside `Memory.access(action)`, as
does each page of the web view that shows memory (cairn.web.ui and the routes beside it),
which writes one INFO record on the logger "cairn.access" when it ends, with the attributes
`actor_kind`, `actor`, `actor_id` (the key's, grant's or person's id: names repeat),
`action`, `project` (the name of the project the call resolved, or None) and `status`. It never
holds a query, note text, a credential or what the caller typed as a project: only names and
ids the store resolved. Reads are recorded only here, unless the server audits reads
(`serve --audit-reads`, Store.reads_audited): then a read of READ_EVENTS that ends well is
also an event of the workspace (`access`), naming who, the kind of read, the project and
the page, claim or note read, never a query or any text. An export is always an event
(EXPORTED_EVENT). Writes are recorded here and in the workspace's audit log. Two records
come from outside a call, with the same attributes and fixed values (`record_access`): a
request to /api or /mcp refused before its route for want of a valid credential is ("none",
None, None, DENIED, None, 401), never with what it presented; an MCP call whose arguments fail the
tool's schema is its caller's, INVALID, no project, 400; a request over its workspace's rate
(cairn.web.auth) is its caller's, "rate_limited", no project, 429. A log handler that raises
does not change how a request ends.

Plans (H6): a Memory made with `plan=(name, Plan)` acts through a view limited by it
(Accounts.memory). A write past a cap raises OverQuota (402), never audited: its message is
one plain sentence ("... 3 of 3 notes this month on the Free plan, and can write again on
1 December 2026 (2026-12-01, UTC).") and `fields()` gives limit, used, cap, resets_at (ISO,
or None) and the plan's name. `usage()` is the workspace's plan and what it used of each
cap, Forbidden to a narrowed or read-only connection; a read.

The store's own trouble (R1): a write that failed because the disk is full or the file
cannot be written (cairn.store.CannotWrite) is Unavailable (503) with UNWRITABLE, and a
store found unusable (cairn.store.StoreUnusable: a rollback failed) is Unavailable with
OUT_OF_SERVICE, both turned so in `Memory.access`, the one place every MCP tool call and
REST request passes, and recorded there with 503. Each is one fixed sentence that names no
path and no SQL. Nothing is recorded in the audit log: the write's audit row went with it.

Filing (N1): `Memory.note` stores a note, then files it on the page of its topic key
(cairn.compiler.page_for; the store says which page that is, nothing here makes an
address). The same note sent again for another topic raises FiledOnce (409), naming its
page only when that page's latest version cites it (Store.note_page, the Notes page's rule),
else saying it waits (WAITS) or is on no page (NO_PAGE); nothing is stored. A note whose
page moved under it more often than the compiler retries is compiled once more; if it
loses again, nothing is recorded, the note stays
waiting, and the answer is `status` WAITING with the sentence WAITS (REST 202, an MCP
result that is not an error). `file_waiting(note_id)` files a waiting note as `note`
does and records its outcome, for the filer `serve` runs (cairn.web.filing).

Who wrote it (A1; frozen by tests/acceptance/test_a1_written_by.py). What recall, page and
evidence answer is text that someone wrote down; cairn does not judge whether it reads as an
instruction. It says who wrote each line and how it came in, beside what each answer had
before, which stays as it was (and what `note` answers does not change):
- A note's writer is its store's (`Store.note_writers`): (kind, name), the kind one of
  "person", "key", "agent", "source", "operator", or "unknown" for a note stored before
  writers were recorded (no name). The writer is the caller's, never anything the request
  says: a note's `via` is the caller's own word and is shown as given.
- `outside` (is_outside): a note a source's run wrote is outside text, its words written by
  whoever can write at that source; so is a Slack message someone filed (8.5: its
  `slack_notes` row, of how "filed", created the note), its words its author's; a note with
  no recorded writer is outside when its `via` is the `via` of a kind in
  cairn.sources.KINDS (read at each call); every other note is not, a note typed with the
  Slack command among them.
- Every hit of `recall` carries `outside` (true when any note its claim cites is outside)
  and `written_by`: one entry per note the claim cites, in the order the claim first cites
  each, at most WRITTEN_BY_MAX (3), each {"note": the note's id, "kind", "name", "via",
  "outside"}, and for a filed Slack message `filed_message` (SLACK_FILED: the writer filed
  it, the words are the message's author's); and `written_by_more`, the count of notes left
  out, only when there are more. A passage the store no longer holds, or one out of the
  caller's reach, names nothing.
- Every claim `page` answers carries `outside` and `written_by` (and `written_by_more`) by
  the same rule. The stored body is not rewritten.
- Every passage `evidence` answers carries `written_by` ({"kind", "name"} of its note's
  writer, and `filed_message` as above), its note's `via` and `outside`.
- When (8.1): every hit, claim and passage carries `written_at`, the `created_at` the store
  keeps of its note (ISO 8601); for a hit or a claim citing several notes, the newest; None
  when it cites none the caller reaches. It is read with the writers (Store.note_origins)
  or with the note itself, so no statement is added.
- No name holds an address: a person is named by audit.person_name, and a key, an agent and
  a source by the label or name they were given.

A read costs what it returns (Q1; frozen by tests/acceptance/test_q1_bounded_reads.py). Each
read here runs a fixed number of statements whatever a workspace holds, and a count reads no
page body, claims or note text: `projects` counts each project's pages in one statement
(Store.page_counts), `overview` counts notes without reading them (Store.count_notes),
and the sources of a claim or a page are read in one statement for every note they cite
(Store.get_notes). Lists that grow with use are given a part at a time:
- `pages` and `overview` give a project's pages by slug, at most PAGES_AT_ONCE (100) at a
  time, as their heads (Store.page_heads: no body read). `limit` asks for fewer (a whole
  number, 1 or more; more than the bound is the bound) and `after` is a slug: the part holds
  the pages whose slug sorts after it, so pages added between two asks neither repeat a page
  nor skip one. A part that leaves pages out has `pages_total`, and, while more follow,
  `pages_next`: the `after` of the next part, its last slug. `notes` still counts every
  note of the project.
- `page` gives a page's version numbers read from their end: the latest VERSIONS_AT_ONCE
  (50) first, oldest first within the part; `limit` takes fewer of them and `before` (a
  version's number) the ones numbered below it. A part that leaves versions out has
  `versions_total`, and, while earlier ones remain, `versions_next`: the `before` of the
  earlier part, its lowest number.
An answer that holds the whole list has none of these fields: it is the answer as it was.

What a search left out (Q2; tests/acceptance/test_q2_search_says_what_it_left_out.py).
`recall` answers, beside `hits`, `searched` (`searched`), always, so that no one infers
that a search was complete from a field that is not there. The store works out the facts
once (cairn.store.ranking.found); `searched` words them, and nothing else does: GET
/api/search and the `recall` tool answer this dict, and the search page shows its `said`.

Related pages (KG1; the rules are cairn.relations, the read cairn.store.relating).
`related(project, slug, limit=8)` answers {"project", "page", "title", "version", "related",
"searched"}: the pages of the project most related to the page, best first, at most `limit`
(a whole number of 1 to relations.RELATED_MAX, any other refused, never cut), each as
{"page", "title", "version", "why", "more"}. `why` holds at most relations.WHY_MAX reasons,
the weightiest first, and `more` counts the rest. A reason is {"kind": "shares", "term",
"term_kind", "pages", "here", "there"}: a term both pages state, as it is compared, its
kind, how many pages of the project hold it (exactly: a term whose pages are not all known
gives no reason), and the claim of this page and of the other that state it; or {"kind":
"mentioned_by", "term", "term_kind": "title", "there"}: a claim of the other page that holds
this page's title. This version does not list the pages a page mentions. Each claim is
{"claim_id", "text", "passage_ids", "outside", "written_by", "written_at"} (and
`written_by_more`), as a hit of `recall` carries them, its text whole as a hit's is. No
answer shows a weight. `searched` (`related_searched`), always: `complete`, and the three
things a read can leave out, each counted (cairn.store.relating.Left): `terms_looked_up` and
`terms_not_looked_up`, the page's terms past the bound on terms; `claims_read` and
`claims_not_read`, the claims of other pages past the bounds on what is read;
`pages_left_out`, the related pages past `limit`. Nothing of a relation is stored: the
answer is worked out from the pages as they are, in one read of the store, with who wrote
the notes its claims cite. A page that is not there is refused as `page` refuses it."""

import contextlib
import logging
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import IO, Any

from cairn.compiler import (
    CONTENT_KINDS,
    CompileResult,
    Gate,
    body_to_read,
    compile_note,
    lost_race,
    slugify,
)
from cairn.guard import Finding
from cairn.kinds import GENERAL, KINDS, kind_of
from cairn.plans import Plan, Plans, limit_sentence
from cairn.relations import RELATED_MAX, SHARES, Reason
from cairn.sources import KINDS as SOURCE_KINDS
from cairn.spans import Span, chunk
from cairn.store import (
    MODELS,
    PROJECT_NAME,
    CannotWrite,
    Claim,
    DescriptionTooLong,
    ExportSummary,
    FieldTooLong,
    FiledElsewhere,
    Forgotten,
    ForgottenNote,
    HeadingsTooLong,
    NameTaken,
    Note,
    NoteHead,
    NoteRefused,
    Page,
    PageTooLong,
    Project,
    Searched,
    Store,
    StoreUnusable,
    TooManyFacts,
    User,
    note_key,
    ranking,
    relating,
)
from cairn.store.access import NOT_THE_MAKER
from cairn.store.audit import UNKNOWN_WRITER
from cairn.store.memory import PAGE_CHARS_MAX, PAGE_CLAIMS_MAX, NoteOrigin, claim_chars
from cairn.store.models import shown_metrics
from cairn.store.reach import REACH_MAX, reach_too_wide
from cairn.store.usage import (
    BYTES_PER_PASSAGE,
    HISTORY_FACTOR,
    LIMITS,
    PASSAGE_KINDS,
    Limits,
    OverLimit,
    month_after,
)

MAX_HITS = 50  # the most hits `recall` gives: a `limit` of 1 to this, any other refused (Q3)
HITS_RANGE = f"'limit' is a whole number, 1 to {MAX_HITS}"  # what a search says of any other
RELATED_RANGE = f"'limit' is a whole number, 1 to {RELATED_MAX}"  # and `related`, of its own
PAGES_AT_ONCE = 100  # Q1: the most pages one answer of `pages` or `overview` lists
VERSIONS_AT_ONCE = 50  # Q1: the most version numbers one answer of `page` lists
WRITTEN_BY_MAX = 3  # the notes a recall hit names; `written_by_more` counts the rest (A1)
NEAREST_SHOWN = 3  # 8.6: the page names a page not found names
ACCESS_LOGGER = "cairn.access"
access_log = logging.getLogger(ACCESS_LOGGER)
# The note fields the guard checks besides the text, as a person reads their names.
CHECKED_FIELDS = {
    "title": "title",
    "topic": "topic",
    "label": "label",
    "source_ref": "source",
    "via": "via",
}
_VIOLATION_CODE = re.compile(r"[a-z_]+\.[a-z_]+")  # "provenance.missing_passages: ..." -> code
COMPILE_ERROR = "compile.error"  # the code note.rejected records when the compile raised


# Reads as audit events (`serve --audit-reads`; Store.reads_audited): each access-log action
# that reads what a workspace holds, and the event it is recorded as when it ends well. An
# export is not among them: it is always an event of its own (EXPORTED_EVENT).
READ_EVENTS = {
    "page": "read.page",
    "related": "read.page",  # the pages related to a page: a read of that page, by its slug
    "evidence": "read.evidence",
    "recall": "read.search",
    "graph": "read.graph",
    "ask_forget": "read.note",  # the page that shows a note before it is forgotten
    "notes": "read.notes",  # a part of a project's list of notes (UI and UX 8): no text
}
EXPORTED_EVENT = "workspace.exported"  # an export of the workspace's notes and pages


DENIED = "denied"  # the action of a request refused before its route: no valid credential
INVALID = "invalid"  # the action of an MCP call whose arguments fail the tool's schema


def record_access(
    actor_kind: str,
    actor: str | None,
    actor_id: str | None,
    action: str,
    project: str | None,
    status: int,
) -> None:
    """One record on the access log. A handler that fails is the log's problem, not the
    caller's: what logging raises is dropped, and the request ends as it would have. A
    request refused before anyone was identified has no caller and no project, and its line
    says so in words ("denied by an unknown caller: 401"), not with Python's None."""
    with contextlib.suppress(Exception):
        unknown = actor is None and actor_id is None and project is None
        form = "%s by an unknown caller: %s" if unknown else "%s by %s %r (%s), project %r: %s"
        said = (
            (action, status) if unknown else (action, actor_kind, actor, actor_id, project, status)
        )
        access_log.info(
            form,
            *said,
            extra={
                "actor_kind": actor_kind,
                "actor": actor,
                "actor_id": actor_id,
                "action": action,
                "project": project,
                "status": status,
            },
        )


class RequestError(Exception):
    """A request cairn refuses: REST answers with `status`, an MCP tool returns an error."""

    status = 400


class BadRequest(RequestError):
    status = 400


class Malformed(BadRequest):
    """A note refused for what it is, not for the workspace's state: nothing stored.
    `reason` names why in words that quote nothing of the note."""

    reason = "a malformed note"


class EmptyText(Malformed):
    """A note whose text is empty or blank."""

    reason = "empty text"


class NoPassage(Malformed):
    """A note of headings, front matter and blank lines only: nothing on it becomes a claim,
    so nothing of it is stored (review 7.6)."""

    reason = "no body text"


NO_PASSAGE = (
    "the note holds no body text, only headings or front matter: write the text under a heading"
)


class PastBound(Malformed):
    """A note's field is longer than its bound (cairn.store.FIELD_MAX)."""

    reason = "a field past its bound"


class LongHeadings(Malformed):
    """A passage of the note sits under headings past their bound (cairn.store.HEADINGS_MAX
    UTF-8 bytes in all), or its headings outnumber its passages by more than
    cairn.store.HEADINGS_OVER_PASSAGES: nothing stored. The message gives the numbers and
    the bound, never a heading."""

    reason = "headings past their bound"


class ManyFacts(Malformed):
    """The note holds more facts than cairn.store.FACTS_MAX (G1): nothing stored. The
    message gives the count and the bound, never a fact."""

    reason = "facts past their bound"


class Forbidden(RequestError):
    status = 403


class NotFound(RequestError):
    status = 404


# What a caller is told when the store cannot do its part: fixed sentences, no path, no SQL.
# A note is two writes (the note, then its page), so neither says that nothing was stored.
UNWRITABLE = (
    "The server could not write to its database: its disk is full or cannot be written."
    " Try again later."
)
OUT_OF_SERVICE = (
    "The server cannot use its database until it is restarted: a write failed and could"
    " not be undone."
)


class Unavailable(RequestError):
    """The store could not do its part: REST answers 503 and MCP a tool error, with one of
    the fixed sentences above (`unavailable`)."""

    status = 503


def unavailable(error: CannotWrite | StoreUnusable) -> Unavailable:
    """The refusal for the store's own trouble: UNWRITABLE for a write the disk refused,
    OUT_OF_SERVICE for a store that is unusable."""
    return Unavailable(UNWRITABLE if isinstance(error, CannotWrite) else OUT_OF_SERVICE)


class Conflict(RequestError):
    status = 409


class NoteForgotten(Conflict):
    """A note whose text the project has forgotten (Store.forget_note) was written to it
    again: nothing was stored. REST answers 409, MCP a tool error, the composer its form
    again, and a source sync skips it ("forgotten"). The message names the project, never
    the text."""

    def __init__(self, project: str) -> None:
        super().__init__(
            f"This note was forgotten in project {project!r}, and the same text is not stored there"
            " again: nothing was stored."
        )


FORGOTTEN = "forgotten"  # the reason a source sync skips a forgotten note by
PAGE_FULL = "page full"  # the reason a source sync skips a note for a full page by
NOT_TOTALS = (
    "Only the workspace's owners, and the keys and connections that reach the whole"
    " workspace for them, see what it has used of its plan"
)


FILED_ELSEWHERE = "filed under another topic"  # the reason a source sync skips FiledOnce by
WAITING = "waiting"  # the status of a note stored and not yet on its page (N1)
WAITS = (
    "The note is stored, and its page changed under it while it was filed, more often than a"
    " note is tried at once. It will be filed on its page by itself: do not send it again."
)


NO_PAGE = (
    "No page was made from this note: it is stored, but no page holds it, so there is no page"
    " to read."
)


# The key of Memory.note's answer, for a note the gate refused, that holds the page whose
# latest version cites the note (Store.note_page), or None: read by without_missing_page.
CITED_ON = "cited_on"


def without_missing_page(answer: dict[str, Any]) -> dict[str, Any]:
    """8.6: what `note` answers an agent (MCP and REST): a note that made no page (it is on
    none: `version` None, and not WAITING for its page) has `page` None and `message`
    NO_PAGE, so it names no page that does not open. A note the gate refused names its page
    by the Notes page's rule (CITED_ON, Store.note_page): only a page whose latest version
    cites it, so one refused on a topic that has a page is on none too (the second report's
    finding 8). Any other answer as it is, CITED_ON left out. The web composer and Slack
    read Memory.note's own answer."""
    shown = {key: value for key, value in answer.items() if key != CITED_ON}
    if CITED_ON in answer:
        slug = answer[CITED_ON]
        shown |= {"page": slug, "version": shown["version"] if slug else None}
    if shown["version"] is not None or shown["status"] == WAITING:
        return shown
    return {**shown, "page": None, "message": NO_PAGE}


class FiledOnce(Conflict):
    """A note the project holds already (the same text and source_ref) was sent again for
    another topic (N1): nothing was stored or recorded. REST answers 409, MCP a tool error,
    the composer its form again, and a source sync skips it (FILED_ELSEWHERE). The message
    names the project and, as `page`, the page the note is on by the Notes page's rule
    (Store.note_page): only a page whose latest version cites it. A note that waits for its
    page names none and says WAITS, as `note` answers one; a note on no page (its page was
    refused it) names none and says NO_PAGE. Never the note's text; it says only what its
    reader can do (8.2): an agent has no tool that forgets, so forgetting is a person's, on
    the web."""

    reason = FILED_ELSEWHERE

    def __init__(self, project: str, slug: str | None, waiting: bool = False) -> None:
        self.page = slug
        if slug is not None:
            where = (
                f"Nothing was stored: this note is already in project {project!r}, filed on"
                f" page {slug!r} under another topic. A note is filed once: to add to that"
                " page, write a new note with that page's title as its topic."
            )
        else:
            where = (
                f"Nothing was stored: this note is already in project {project!r}, under"
                f" another topic. {WAITS if waiting else NO_PAGE}"
            )
        super().__init__(
            f"{where} To move this one, a person who may forget notes in this project can"
            " forget it on the web; then send it again with its new topic, reworded (a text"
            " forgotten in a project is not stored there again), or send it to another"
            " project."
        )


class PageFull(Conflict):
    """A note would take its page past the claims a page holds (cairn.store
    PAGE_CLAIMS_MAX) or the characters its claims hold (PAGE_CHARS_MAX, G1), or holds more
    than that alone. REST answers 409, MCP a tool error, the composer its form again, and a
    source sync skips it (PAGE_FULL). The message says which bound was met and names the
    project and the page, never the note's text."""

    reason = PAGE_FULL

    @classmethod
    def full(cls, project: str, slug: str, held: int) -> "PageFull":
        return cls(
            f"Nothing was stored: page {slug!r} in project {project!r} holds {held:,} claims, and"
            f" this note would take it past the {PAGE_CLAIMS_MAX:,} a page holds. Give the"
            " note another topic: it goes to that topic's page."
        )

    @classmethod
    def alone(cls, passages: int) -> "PageFull":
        return cls(
            f"Nothing was stored: this note holds {passages:,} passages, and a page holds at"
            f" most {PAGE_CLAIMS_MAX:,} claims: split it into smaller notes, each with its own"
            " topic."
        )

    @classmethod
    def full_chars(cls, project: str, slug: str, held: int) -> "PageFull":
        if held > PAGE_CHARS_MAX:  # larger already when the bound came (G1)
            return cls(
                f"Nothing was stored: page {slug!r} in project {project!r} holds {held:,}"
                f" characters of claims, more than the {PAGE_CHARS_MAX:,} a page holds, so it"
                " stays as it is and takes no more notes. Give the note another topic: it goes"
                " to that topic's page."
            )
        return cls(
            f"Nothing was stored: page {slug!r} in project {project!r} holds {held:,} characters"
            f" of claims, and this note would take it past the {PAGE_CHARS_MAX:,} characters"
            " a page holds. Give the note another topic: it goes to that topic's page."
        )

    @classmethod
    def alone_chars(cls, chars: int) -> "PageFull":
        return cls(
            f"Nothing was stored: this note's passages hold {chars:,} characters, and a page"
            f" holds at most {PAGE_CHARS_MAX:,} characters of claims: split it into smaller"
            " notes, each with its own topic."
        )

    @classmethod
    def kept(cls, project: str, slug: str, chars: bool = False) -> "PageFull":
        if chars:  # G1: the characters, not the claims
            return cls(
                f"The note was kept, but page {slug!r} in project {project!r} came to hold the"
                f" {PAGE_CHARS_MAX:,} characters of claims a page holds meanwhile, and nothing"
                " was added to it. File what follows under another topic."
            )
        return cls(
            f"The note was kept, but page {slug!r} in project {project!r} came to hold the"
            f" {PAGE_CLAIMS_MAX:,} claims a page holds meanwhile, and nothing was added to it."
            " File what follows under another topic."
        )


# M4: what a refused change of a project's switch says. Neither repeats what was given.
NOT_MODELS = f"'models' is {MODELS[0]!r} or {MODELS[1]!r}"
NOT_A_DECIDER = (
    "Only the project's maker, the workspace's owners, anyone this project lets decide who"
    " reaches it, and a key that belongs to whoever runs the server can change whether a model"
    " is sent its pages."
)
NOT_AN_ERASER = (
    "Only the project's maker, the workspace's owners and a key that belongs to whoever runs the"
    " server, such as one made on its command line, can delete this project or forget its"
    " notes, and only from a connection that reaches every project to write."
)


class Refused(RequestError):
    """The note guard refused a note: nothing of it was stored. `findings` pairs each
    finding with the field it was found in (store.SCREENED_FIELDS: "text", "title", "topic",
    "label", "source_ref" or "via"). Its message names the rules and where they matched,
    never the text."""

    status = 422

    def __init__(self, findings: list[tuple[str, Finding]]) -> None:
        self.findings = findings
        kinds = {finding.kind for _, finding in findings}
        what = " or ".join(
            label
            for kind, label in (("secret", "a secret"), ("personal", "personal data"))
            if kind in kinds
        )
        where = ", ".join(f"{f.rule} {_where(field, f)}" for field, f in findings)
        super().__init__(
            f"the note was refused and nothing of it was stored: it holds what looks like "
            f"{what or 'something the guard refuses'} ({where}). Remove it and send the note again."
        )

    def listed(self) -> list[dict[str, Any]]:
        """The findings as answers show them: rule, kind and line, and the field when it is
        not the text."""
        return [
            {"rule": f.rule, "kind": f.kind, "line": f.line}
            | ({} if field == "text" else {"field": field})
            for field, f in self.findings
        ]


_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


# A refusal at a cap, said to a connection that may not read the workspace's usage: which
# limit, and nothing of the workspace's counts, caps, reset day or plan.
_WITHHELD = {
    "notes_per_month": (
        "Nothing was stored: this workspace has written all the notes its plan allows this month."
    ),
    "storage_bytes": "Nothing was stored: this note would take the workspace past its plan's"
    " storage for notes.",
    "projects": "No project was made: this workspace has all the projects its plan allows.",
    "passages": "Nothing was stored: this note would take the workspace past the passages"
    " (paragraphs, list items and other blocks) its plan's storage allows.",
}


class OverQuota(RequestError):
    """A write that would pass a cap of the workspace's plan (the store's OverLimit), as
    every way in reports it: REST 402 with `fields()`, an MCP tool error, the composer's
    form again. Nothing was stored. Its message is one sentence with the usage, the cap,
    the plan's label and, for the monthly cap, the day it resets; never the note's text.

    `whole` False (a connection that may not read the workspace's usage: narrowed to some
    projects, or read-only) tells only which limit: the sentence has no number and no plan,
    and `fields()` only the limit's name."""

    status = 402

    def __init__(
        self, over: OverLimit, plan: str | None, label: str | None, *, whole: bool = True
    ) -> None:
        self.limit, self.used, self.cap, self.resets_at = (
            over.limit,
            over.used,
            over.cap,
            over.resets_at,
        )
        self.plan = plan
        self.whole = whole
        if not whole:
            super().__init__(_WITHHELD[over.limit])
            return
        allows = f"the {label} plan" if label else "its plan"
        allows += f" allows {limit_sentence(over.limit, over.cap)}"  # the plan's own words
        if over.limit == "notes_per_month":
            day = over.resets_at
            assert day is not None  # the monthly cap always resets
            message = (
                f"Nothing was stored: this workspace has written {over.used} of {over.cap} notes"
                f" this month, and {allows}; it can write again on {day.day}"
                f" {_MONTHS[day.month - 1]} {day.year} ({day:%Y-%m-%d}, UTC)."
            )
        elif over.limit == "storage_bytes":
            message = (
                f"Nothing was stored: this workspace's notes take {over.used} of {over.cap}"
                f" bytes, {allows}, and this note would take it past that."
            )
        elif over.limit == "passages":
            message = (
                f"Nothing was stored: this workspace's notes hold {over.used} of {over.cap}"
                f" passages (paragraphs, list items and other blocks), {allows}, and this"
                " note would take it past that."
            )
        else:
            message = (
                f"No project was made: this workspace has {over.used} of {over.cap} projects,"
                f" and {allows}."
            )
        super().__init__(message)

    def fields(self) -> dict[str, Any]:
        """What a REST answer carries beside the message: the limit's name alone when the
        connection may not read the workspace's usage."""
        if not self.whole:
            return {"limit": self.limit}
        return {
            "limit": self.limit,
            "used": self.used,
            "cap": self.cap,
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
            "plan": self.plan,
        }


def _where(field: str, finding: Finding) -> str:
    if field == "text":
        return f"on line {finding.line}"
    return f"in the {CHECKED_FIELDS.get(field, field)}"


SCOPES = ("all", "selected")  # how a form names a reach: every project, or the ticked ones
ACCESS_LABELS = {"write": "Read and write", "read": "Read only"}


def choose_reach(
    scope: object, projects: Iterable[object], access: object
) -> tuple[tuple[str, ...] | None, str]:
    """The reach a person chose on a form, as the store takes it: `scope` "all" (or nothing)
    is every project, "selected" the ticked `projects`, at least one; `access` "write" (or
    nothing) or "read". BadRequest otherwise, and also for projects ticked without "selected":
    the form has no script, so that is a person who ticked projects and left All projects
    chosen, and neither reading is safe to guess. Whether each id is a project or set of the
    workspace is the store's to check; more than REACH_MAX of them is refused here, in the
    store's sentence (cairn.store.reach.reach_too_wide), so the consent page leaves the request
    open."""
    scope, access = scope or "all", access or "write"
    ticked = tuple(dict.fromkeys(ref for ref in projects if isinstance(ref, str) and ref))
    if scope not in SCOPES:
        raise BadRequest("Choose All projects or Selected projects.")
    if access not in ACCESS_LABELS:
        raise BadRequest("Choose Read and write or Read only.")
    if scope == "all" and ticked:
        raise BadRequest(
            "Projects are ticked but All projects is chosen: choose Selected projects."
        )
    if scope == "all":
        return None, str(access)
    if not ticked:
        raise BadRequest("Selected projects: tick at least one project or set.")
    if len(ticked) > REACH_MAX:  # the store refuses it too; here, before a consent is settled
        raise BadRequest(reach_too_wide("a connection").capitalize() + ".")
    return ticked, str(access)


def reach_label(projects: tuple[str, ...] | None, names: dict[str, str]) -> str:
    """A connection's reach by name, for people: "All projects", or the names of the projects and
    sets it names that still exist (`names`: id to name), or "No project" when none does."""
    if projects is None:
        return "All projects"
    return ", ".join(names[ref] for ref in projects if ref in names) or "No project"


def name_unavailable(name: str) -> str:
    """The one answer to a project name the workspace already uses, in every view (web form,
    REST, MCP, guided setup): the same sentence whether that project is one the caller sees
    or not, since names are unique per workspace. It names only the name asked for."""
    return f"The project name {name!r} is not available: it already exists in this workspace."


NAME_RULE = "use 1 to 64 letters, digits, '.', '_' or '-', starting with a letter or digit"
ID_PREFIX = 'a name cannot begin with "br_", because that is how ids begin'


def name_rule(name: str) -> str:
    """Why `name` cannot name a project (cairn.store.is_project_name refuses it): the rule it
    breaks. A name that meets PROJECT_NAME is refused only for the id prefix "br_" (any case),
    and is told that, never a rule it meets."""
    return ID_PREFIX if PROJECT_NAME.fullmatch(name) else NAME_RULE


def unknown_kind(kind: str) -> str:
    """Why a kind is refused, naming every kind there is."""
    ids = list(KINDS)
    return f"{kind!r} is not a project kind: use {', '.join(ids[:-1])} or {ids[-1]}"


UNKNOWN_SHOWN = 5  # X1: the most unknown names a refusal shows; it counts the rest
NAME_SHOWN = 64  # X1: the most characters of an unknown name a refusal shows


def _joined(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _shown(name: str) -> str:
    """`name` as a refusal shows it: through repr, so a line break or a control character
    is never written raw, and cut when what repr writes between its quotes would pass
    NAME_SHOWN characters, the cut said by "..." after the quotes."""
    if len(repr(name)) - 2 <= NAME_SHOWN:
        return repr(name)
    cut = ""
    for char in name:
        if len(repr(cut + char)) - 2 > NAME_SHOWN:
            break
        cut += char
    return repr(cut) + "..."


def not_taken(what: str, unknown: Iterable[str], who: str, takes: Sequence[str]) -> str:
    """X1: why a request is refused that names a `what` ("key", "query parameter",
    "argument") that `who` ("this address", a tool's name) does not take. One sentence: the
    unknown names sorted, the first UNKNOWN_SHOWN of them each cut to NAME_SHOWN characters
    (_shown) and how many more there are, then every name `who` takes in its own order, or
    that it takes none. Enough to find the mistake, and bounded whatever is sent, since the
    sentence goes back to the caller and onto logs. REST and MCP say it alike."""
    given = sorted(unknown)
    shown = [_shown(name) for name in given[:UNKNOWN_SHOWN]]
    if len(given) > UNKNOWN_SHOWN:
        shown.append(f"{len(given) - UNKNOWN_SHOWN} more")
    said = f"unknown {what}{'s' if len(given) > 1 else ''} {_joined(shown)}; "
    listed = [repr(name) for name in takes]
    return said + (f"{who} takes {_joined(listed)}" if takes else f"{who} takes no {what}s")


NOTHING_LEFT_OUT = "Every word of the query was searched, and every matching claim was ranked."
SHORTEN = "Shorten the query to the words that matter most."
NARROW = (  # more than one project searched
    "Search one project so that fewer claims match, or add a rarer word to rank the claims that"
    " hold it first."
)
RARER = "Add a rarer word to rank the claims that hold it first."  # one project searched
NO_WORDS = (  # 8.4: a query of nothing but spaces or punctuation
    "Nothing was searched: the query holds no words, only spaces or punctuation. Search with"
    " words of letters or digits."
)


def _cut(word: str) -> str:
    """A word left out as `searched` lists it: cut to NAME_SHOWN characters, the cut said by
    "..." (no word holds a ".")."""
    return word if len(word) <= NAME_SHOWN else word[:NAME_SHOWN] + "..."


def _many(n: int, one: str, more: str) -> str:
    return f"{n:,} {one if n == 1 else more}"


def searched(facts: Searched) -> dict[str, Any]:
    """Q2: what a search left out, as `recall` answers it under `searched`: the facts, each
    word left out cut to NAME_SHOWN characters (the store keeps the first WORDS_SHOWN, 5), and
    `said`, one or two sentences from those facts alone that say what was left out and what
    to do about it, or NOTHING_LEFT_OUT; NO_WORDS for a query that held no word to search
    (8.4: never "every word was searched"). The advice follows the search: searching one project
    is advised only to a search of more than one (NARROW), never to one that covered one
    project, named or the only one reached (RARER). The search page shows `said` as it is."""
    sentences = []
    if left := facts.words_left_out_count:
        shown = [_shown(word) for word in facts.words_left_out]
        if left > len(shown):
            shown.append(f"{left - len(shown):,} more")
        if facts.words_searched == ranking.WORDS_MAX:
            words = f"{_many(left, 'more was', 'more were')} left out: {_joined(shown)}."
            first = f"A search takes the first {ranking.WORDS_MAX} words of a query"
            sentences.append(f"{first}; {words}")
        else:
            first = facts.words_searched
            taken = "none was" if not first else f"the first {_many(first, 'was', 'were')}"
            sentences.append(
                f"A search takes the words of a query until they hold {ranking.PARTS_MAX} parts"
                f" between underscores; {taken} searched and {_many(left, 'was', 'were')} left"
                f" out: {_joined(shown)}."
            )
        sentences.append(SHORTEN)
    ranked = facts.claims_ranked
    if facts.ranked_by != "all":
        if facts.claims_matched is None:
            match = f"More than {ranked:,} claims match"
        elif not facts.claims_matched_exact:  # the count stopped at its bound (R6)
            match = f"More than {facts.claims_matched:,} claims match"
        else:
            match = f"{facts.claims_matched:,} claims match"
        if facts.ranked_by == "newest":
            sentences.append(f"{match}, so only the newest {ranked:,} were ranked.")
        else:
            sentences.append(
                f"{match}, so only the {ranked:,} holding the query's rarest words were ranked,"
                " the newest first among equals."
            )
        sentences.append(NARROW if facts.projects_searched > 1 else RARER)
    # R6: `claims_matched_exact` is there, false, only when the count stopped at its bound;
    # under it the answer is what it was.
    stopped = {} if facts.claims_matched_exact else {"claims_matched_exact": False}
    return {
        "complete": facts.complete,
        "words_searched": facts.words_searched,
        "words_left_out": [_cut(word) for word in facts.words_left_out],
        "words_left_out_count": facts.words_left_out_count,
        "claims_matched": facts.claims_matched,
        **stopped,
        "claims_ranked": facts.claims_ranked,
        "said": " ".join(sentences) or (NOTHING_LEFT_OUT if facts.words_searched else NO_WORDS),
    }


def _kind(project: Project) -> dict[str, str]:
    """What an agent is told a project is for: its kind, and the guidance on what to write
    there. A kind no longer shipped reads as "general"."""
    kind = kind_of(project.kind)
    return {"kind": kind.id, "guidance": kind.guidance}


def _models(project_id: str, kept: frozenset[str]) -> str:
    """How a project's representation says whether a model may be sent its pages (M4)."""
    return MODELS[1] if project_id in kept else MODELS[0]


def _codes(violations: Iterable[str]) -> list[str]:
    """The codes of a gate's violations ("provenance.missing_passages"), never their messages,
    which may quote the note."""
    codes = (_VIOLATION_CODE.match(v) for v in violations)
    return list(dict.fromkeys(code.group() for code in codes if code))


def _claim(claim: Claim) -> dict[str, Any]:
    return claim.shown()


# 8.5: what a `written_by` entry of a Slack message someone filed says beside its writer.
SLACK_FILED = (
    "A Slack message this writer filed: its words are its author's, whom the note's heading names."
)


def source_via() -> frozenset[str]:
    """The `via` of every kind of source (cairn.sources.KINDS), read now: a kind added at
    run time counts."""
    return frozenset(kind.via for kind in SOURCE_KINDS.values())


def is_outside(kind: str, via: str | None, *, filed_message: bool = False) -> bool:
    """Whether a note is outside text (A1): a source's run wrote it; or it is a Slack
    message someone filed (8.5: Store.note_origins' `filed_message`), whose words are its
    author's, as a source's are whoever wrote there; or no writer was recorded for it (it
    was stored before) and its `via` is a source kind's. A note typed with the Slack command
    is its writer's own."""
    if kind == "source" or filed_message:
        return True
    return kind == UNKNOWN_WRITER and via is not None and via in source_via()


def _written(note: str, origin: NoteOrigin) -> dict[str, Any]:
    """One entry of a hit's `written_by`."""
    kind, name, via = origin[:3]
    entry = {
        "note": note,
        "kind": kind,
        "name": name,
        "via": via,
        "outside": is_outside(kind, via, filed_message=origin.filed_message),
    }
    return entry | _filed(origin)


def _filed(origin: NoteOrigin) -> dict[str, str]:
    """8.5: what a writer of a Slack message they filed is said with: SLACK_FILED."""
    return {"filed_message": SLACK_FILED} if origin.filed_message else {}


def _writers(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """A claim's `written_by` (A1): its first WRITTEN_BY_MAX entries, and `written_by_more`,
    the count of the rest, only when there are more."""
    shown: dict[str, Any] = {"written_by": entries[:WRITTEN_BY_MAX]}
    if len(entries) > WRITTEN_BY_MAX:
        shown["written_by_more"] = len(entries) - WRITTEN_BY_MAX
    return shown


def _newest(times: Iterable[str]) -> str | None:
    """8.1: when the newest of a claim's notes was written (the store's ISO times sort as
    times), or None when none of its notes is held."""
    return max(times, default=None)


def written_entries(
    cited: Iterable[Iterable[str]], notes_of: Mapping[str, str], origins: Mapping[str, NoteOrigin]
) -> list[list[dict[str, Any]]]:
    """For each claim's passage ids, one `written_by` entry per note it cites, in the order it
    first cites each (A1), from what a read gave: `notes_of`, the note of each passage it
    holds, and `origins`, who wrote each note. A passage that is not in `notes_of`, or whose
    note is not in `origins`, names nothing. Every entry, none cut."""
    answers = []
    for span_ids in cited:
        notes = dict.fromkeys(notes_of[i] for i in span_ids if i in notes_of)
        answers.append([_written(note, origins[note]) for note in notes if note in origins])
    return answers


def _held_by(reason: Reason, stated: Mapping[Any, dict[str, Any]]) -> dict[str, Any]:
    """What a `shares` reason says beside its term: how many pages hold it, and this page's
    claim. Nothing for a mention."""
    if reason.kind != SHARES:
        return {}
    return {"pages": reason.pages, "here": stated[reason.here]}


def related_searched(left: relating.Left) -> dict[str, Any]:
    """What a read of related pages left out, as `related` answers it under `searched`:
    `complete`, then the counts of cairn.store.relating.Left under their own names."""
    return {"complete": left.complete, **left._asdict()}


def _source(span: Span, note: Note) -> dict[str, Any]:
    return {
        "id": span.id,
        "note_id": note.id,
        "note_title": note.title,
        "source_ref": note.source_ref,
        "heading_path": list(span.heading_path),
        "start_line": span.start_line,
        "end_line": span.end_line,
        "text": span.text,
    }


class Memory:
    """What the MCP tools, the REST API and the web UI share. A project is named by id or name;
    unknown projects, pages and claims raise NotFound, never an empty answer. The store
    screens every note before anything of it is stored (Store.add_note): under the
    workspace's policy, or the guard its view carries (`Store.with_guard`)."""

    def __init__(
        self,
        store: Store,
        gate: Gate,
        person: User | None = None,
        plan: tuple[str, Plan] | None = None,
        *,
        plans: Plans | None = None,
    ) -> None:
        self.store = store
        self.gate = gate
        self.person = person  # who signed in; None for a key, or a session opened with one
        # The workspace's plan, (name, Plan), when the server has plans; `store` is then
        # limited by it. None: a self-hosted install, where nothing is capped.
        self.plan = plan
        # The server's plans, when it has them: a refusal names the plan the write read
        # inside its transaction, which may be another than the one `plan` was admitted on.
        self.plans = plans
        self._touched: str | None = None  # the project the current call resolved, by name
        # What the current call read, as its audit event's detail (READ_EVENTS): a page's
        # slug, a claim's id, a note's id. None: nothing was read yet.
        self._read: dict[str, str] | None = None
        # Called with (project id, slug) after a note saves a page (T4b: the app queues the
        # page for its arranger). None: nothing is called. Its failure never fails a note.
        self.saved: Callable[[str, str], None] | None = None

    def quota_error(self, over: OverLimit) -> OverQuota:
        """The store's refusal as every way in reports it, with the plan's name and label:
        the plan in force the write read (`over.plan`, when a resolver gave its caps and the
        server's plans are known), else the one the call was admitted on."""
        name, plan = self.plan if self.plan else (None, None)
        if over.resolved and self.plans is not None:
            name, plan = self.plans.resolve(over.plan)
        if not self.sees_totals():  # as much as /api/usage would tell it, and no more
            return OverQuota(over, name, None, whole=False)
        return OverQuota(over, name, plan.label if plan else None)

    def sees_totals(self) -> bool:
        """Whether this connection may read the workspace's totals (/usage, /api/usage, a
        402 with numbers and the plan): the totals count every project, so they are for whoever
        reaches every project as the workspace does, an owner, a key an owner made, or a
        credential that acts for no person; never a connection narrowed to some projects or
        read-only, nor an admin or a member, whose reach is chosen project by project. A read."""
        try:
            self.store.stored_bytes()
        except PermissionError:  # narrowed to some projects, or read-only
            return False
        return self.store.user_id is None or self.store.role() == "owner"

    def usage(self) -> dict[str, Any]:
        """The workspace's plan and, for each limit, what is used of it this UTC month (or
        held, for storage and projects) and its cap (None: no cap). Forbidden for a connection
        that may not see the totals (sees_totals). A read."""
        if not self.sees_totals():
            raise Forbidden(NOT_TOTALS)
        try:
            used = self.store.usage()
            stored = self.store.stored_bytes()
        except PermissionError as refused:  # limited to chosen projects, or read-only
            raise Forbidden(str(refused)) from None
        name, plan = self.plan if self.plan else (None, None)
        caps = plan.limits() if plan else Limits()
        counts = {
            "notes_per_month": used.notes,
            "storage_bytes": stored,
            "projects": self.store.project_count(),
            "model_budget_micros": used.model_micros,
        }
        return {
            "plan": name,
            "label": plan.label if plan else None,
            "period": used.period,
            "resets_at": month_after(used.period).isoformat(),
            "limits": {
                limit: {"used": counts[limit], "cap": getattr(caps, limit)} for limit in LIMITS
            },
            # Beside the four limits, which the frozen H6 contract fixes: the passages the
            # storage cap allows (H6c), one per BYTES_PER_PASSAGE bytes of it.
            "passages": {
                "used": self.store.passages(),
                "cap": None
                if caps.storage_bytes is None
                else caps.storage_bytes // BYTES_PER_PASSAGE,
            },
        }

    def record_refusal(self, action: str, project: str | None, error: RequestError) -> None:
        """One record on the access log for a web form refused at a cap of the plan (the
        composer: "note" and its project; the /projects form: "new_project"; /start: "start"), as
        `access` records an MCP call or a REST request refused there. A web form that
        writes is recorded in the audit log, not here."""
        kind, actor = self.store.actor
        record_access(kind, actor, self.store.actor_id, action, project, error.status)

    @contextlib.contextmanager
    def access(self, action: str) -> Iterator[None]:
        """One call of an MCP tool or a REST route: when it ends, one INFO record on the
        access log, with who called (the store's actor), the action, the project it resolved
        and the status it ended with (a RequestError's, 500 for anything else). The store's
        own trouble goes on as Unavailable (503; the module's docstring).

        With reads audited (Store.reads_audited), a call of READ_EVENTS that ends well and
        read something is also one event of the workspace, written before the record: who,
        the kind of read, the project it resolved, and what `_read` says it read (a graph is
        one whatever it showed). If the event cannot be written, the call fails with it (an
        unrecorded read is not answered)."""
        self._touched, self._read, status = None, None, 200
        try:
            yield
            event = READ_EVENTS.get(action)
            if (
                event
                and self.store.reads_audited
                and (self._read is not None or event == "read.graph")
            ):
                self.store._record_read(event, self._touched, self._read or {})
        except RequestError as error:
            status = error.status
            raise
        except (CannotWrite, StoreUnusable) as error:
            status = Unavailable.status
            raise unavailable(error) from error
        except BaseException:
            status = 500
            raise
        finally:
            kind, actor = self.store.actor
            record_access(kind, actor, self.store.actor_id, action, self._touched, status)

    def project(self, ref: str) -> Project:
        project = self.store.get_project(ref) if ref else None
        if project is None:
            raise NotFound(f"no project {ref!r}")
        self._touched = project.name
        return project

    def find_page(self, project: Project, slug: str, version: int | None = None) -> Page:
        page = self.store.get_page(project.id, slug, version)
        if page is not None:
            self._read = {"page": page.slug}  # the audit event's detail (READ_EVENTS)
        if page is None:
            if version is not None and 0 < version < min(
                self.store.page_versions(project.id, slug), default=0
            ):  # an older version a plan's versions_per_page dropped
                cap = self.plan[1].versions_per_page if self.plan else None
                keeps = limit_sentence("versions_per_page", cap) if cap else "its latest versions"
                if self.plan and self.plan[1].storage_bytes is not None:  # the history budget
                    keeps += (
                        f", and older ones only while its pages' versions fit in"
                        f" {HISTORY_FACTOR} times its storage"
                    )
                raise NotFound(
                    f"version {version} of page {slug!r} in project {project.name!r} is no longer"
                    f" kept: this workspace keeps {keeps}"
                )
            which = "page" if version is None else f"version {version} of page"
            raise NotFound(f"no {which} {slug!r} in project {project.name!r}")
        return page

    def named_page(self, project: Project, slug: str, version: int | None = None) -> Page:
        """find_page, for an agent (8.6): a latest page that is not there is NotFound with
        the nearest of the project's page names (nearest_pages), or that it has none."""
        try:
            return self.find_page(project, slug, version)
        except NotFound as missing:
            if version is not None:
                raise
            raise NotFound(f"{missing}. {self.nearest_pages(project, slug)}") from None

    def nearest_pages(self, project: Project, name: str) -> str:
        """The sentence naming the NEAREST_SHOWN page names of `project` nearest to `name`,
        a slug or a title: those whose slug sorts at or after `name` made a slug, then the
        project's first ones; or that the project has no pages. Bounded: at most twice
        NEAREST_SHOWN page heads, in four statements (Store.page_heads, twice at most)."""
        key = slugify(name)  # never empty; slugs hold [a-z0-9-], all below "\x7f"
        below = key[:-1] + chr(ord(key[-1]) - 1) + "\x7f"  # the slugs after it are key's and on
        heads, total = self.store.page_heads(project.id, NEAREST_SHOWN, below)
        if not total:
            return f"Project {project.name!r} has no pages yet."
        if len(heads) < NEAREST_SHOWN:
            held = {head.slug for head in heads}
            first, _ = self.store.page_heads(project.id, NEAREST_SHOWN)
            heads += [head for head in first if head.slug not in held]
        # slugs only: a title is a note's words, and this sentence goes onto logs too
        shown = ", ".join(repr(head.slug) for head in heads[:NEAREST_SHOWN])
        return (
            "A page is named by its slug, as note, recall and overview give it; the nearest:"
            f" {shown}."
        )

    def sources(self, project: Project, span_ids: Iterable[str]) -> dict[str, tuple[Span, Note]]:
        """The known spans among `span_ids` whose note is in `project`, with that note:
        two reads, however many notes they cite (Q1)."""
        spans = self.store.get_spans(span_ids)
        notes = self.store.get_notes(span.source_id for span in spans.values())
        found = {}
        for span_id, span in spans.items():
            note = notes.get(span.source_id)
            if note is not None and note.project_id == project.id:
                found[span_id] = (span, note)
        return found

    def projects(self) -> dict[str, Any]:
        """Each project with its kind and the guidance for it, then the kinds a new project can
        have, so an agent can choose one. The pages are counted in one read (Q1)."""
        counts = self.store.page_counts()
        projects = self.store.list_projects()
        kept = self.store.kept_from_models(project.id for project in projects)  # one read (M4)
        return {
            "projects": [
                {
                    "id": project.id,
                    "name": project.name,
                    "description": project.description,
                    "pages": counts.get(project.id, 0),
                    **_kind(project),
                    "models": _models(project.id, kept),
                }
                for project in projects
            ],
            "kinds": [
                {"id": kind.id, "title": kind.title, "summary": kind.summary}
                for kind in KINDS.values()
            ],
        }

    def reach_names(self) -> dict[str, str]:
        """The name of every project and set this view sees, by id: how a reach is shown."""
        projects = {project.id: project.name for project in self.store.list_projects()}
        return projects | {
            project_set.id: project_set.name for project_set in self.store.list_sets()
        }

    def new_project(
        self,
        name: str,
        description: str = "",
        kind: str = GENERAL,
        *,
        private: bool | None = None,
    ) -> dict[str, Any]:
        """A project of `kind` (an id of cairn.kinds.KINDS), made by the caller's person. A
        private one is reached only by its maker, the people its rule names, the workspace's
        owners and credentials that act for no person. `private` None: the kind decides
        (cairn.kinds.Kind.private; a personal project is private, any other open to every
        member). A connection that may not create projects is refused before the kind or the
        name is looked at. Names are unique per workspace, so a name a project the caller does
        not reach already has is refused like any taken name (Conflict)."""
        everyone = None if private is None else "none" if private else "write"
        try:
            project = self.store.create_project(name, description, kind, everyone=everyone)
        except PermissionError as refused:  # a connection limited to chosen projects, or read-only
            raise Forbidden(str(refused)) from None
        except NameTaken:
            raise Conflict(name_unavailable(name)) from None
        except OverLimit as over:  # the plan's projects, all made
            raise self.quota_error(over) from None
        except DescriptionTooLong as long:
            raise BadRequest(str(long)) from None
        except ValueError:
            if kind not in KINDS:
                raise BadRequest(unknown_kind(kind)) from None
            raise BadRequest(f"{name!r} is not a valid project name: {name_rule(name)}") from None
        kept = self.store.kept_from_models([project.id])  # a private kind starts at "never"
        return {
            "id": project.id,
            "name": project.name,
            **_kind(project),
            "models": _models(project.id, kept),
        }

    def set_models(self, ref: str, models: object) -> dict[str, str]:
        """M4: whether a model the operator connects may be sent the project's pages,
        "allowed" or "never" (Store.set_project_models), and the state it is at now.
        BadRequest (NOT_MODELS) for another value, before the project is looked up; NotFound
        for a project unknown or out of reach; Forbidden for a caller who may not change who
        reaches it (NOT_A_DECIDER), or a connection limited to some projects or to reading
        (the store's own sentence). Never an MCP tool: an agent does not decide what a model
        may read."""
        if not isinstance(models, str) or models not in MODELS:
            raise BadRequest(NOT_MODELS)
        target = self.project(ref)
        try:
            now = self.store.set_project_models(target.id, models)
        except KeyError:
            raise NotFound(f"no project {ref!r}") from None
        except PermissionError as refused:
            said = str(refused)
            raise Forbidden(NOT_A_DECIDER if said == NOT_THE_MAKER else said) from None
        return {"project": target.name, "models": now}

    def note(
        self,
        project: str,
        text: str,
        *,
        title: str | None = None,
        topic: str | None = None,
        label: str | None = None,
        source_ref: str | None = None,
        via: str | None = None,
        within: Callable[..., None] | None = None,
    ) -> dict[str, Any]:
        """`within(con, note, created)`: run inside the store's write of the note, before it
        commits and before the note is compiled (Store.add_note's `within`): what a caller
        records of a note it brought is kept with the note or not at all, whatever the
        compile does afterwards, and what it raises stores nothing and goes on to the caller."""
        target = self.project(project)
        if not text.strip():
            raise EmptyText("the note's text is empty")
        if not any(span.kind in CONTENT_KINDS for span in chunk("", text)) and not (
            # Decision 173: one stored before the refusal is that note, and keeps its answer.
            # One read, on this path alone.
            self.store.holds_note(target.id, text, source_ref)
        ):
            raise NoPassage(NO_PASSAGE)  # nothing stored, counted or bound to its topic
        self._room_on_page(target, text, title, topic, source_ref)  # PageFull: nothing stored
        try:
            note, created = self.store.add_note(
                target.id,
                text,
                title=title,
                topic=topic,
                label=label,
                source_ref=source_ref,
                via=via,
                **({"within": within} if within is not None else {}),
            )
            result = self._file(note, record_errors=True)
        except FiledElsewhere as elsewhere:  # N1: the same note, for another topic
            raise FiledOnce(target.name, *self._page_of(elsewhere.held)) from None
        except PermissionError as refused:  # a read-only connection
            raise Forbidden(str(refused)) from None
        except NoteRefused as refused:  # recorded as note.refused; nothing else stored
            raise Refused(refused.findings) from None
        except OverLimit as over:  # nothing stored, nothing recorded: not a write
            raise self.quota_error(over) from None
        except PageTooLong as long:  # the page filled since _room_on_page looked: kept
            raise PageFull.kept(target.name, long.slug, long.chars is not None) from None
        except HeadingsTooLong as long:  # nothing stored: the bound, never a heading
            raise LongHeadings(str(long)) from None
        except TooManyFacts as many:  # nothing stored: the count and the bound (G1)
            raise ManyFacts(str(many)) from None
        except FieldTooLong as long:  # nothing stored: the field and its bound, never its text
            raise PastBound(str(long)) from None
        except ForgottenNote:  # the project forgot this text: nothing stored or recorded
            raise NoteForgotten(target.name) from None
        except KeyError:  # the project was deleted meanwhile: whatever was stored went with it
            raise NotFound(f"no project {target.name!r}") from None
        answer = {
            "note_id": note.id,
            "created": created,
            "page": result.slug,
            "status": result.status,
            "version": result.version,
            "violations": list(result.gate.violations) if result.gate else [],
        }
        if lost_race(result):  # N1: stored, and waiting for serve to file it
            answer |= {"status": WAITING, "message": WAITS}
        elif result.status == "rejected":  # its topic's page, if any, was refused it
            answer[CITED_ON] = self._page_of(note)[0]  # the Notes page's rule, as FiledOnce
        return answer

    def file_waiting(self, note_id: str) -> str | None:
        """File a stored note that waits for its page (N1; cairn.web.filing), as `note` files
        one, and say how it ended: "saved", "unchanged" or "rejected" (recorded, and it waits
        no more), "full" (its page holds all the claims a page holds: recorded as rejected,
        and it waits no more), WAITING (its page kept moving: nothing recorded, it waits), or
        None for a note that is no longer stored. Anything else it raises, recording nothing:
        the note waits."""
        note = self.store.get_note(note_id)
        if note is None:
            return None
        try:
            result = self._file(note, record_errors=False)
        except PageTooLong:
            return "full"
        return WAITING if lost_race(result) else result.status

    def _file(self, note: Note, *, record_errors: bool) -> CompileResult:
        """Compile a stored note into its page, and record how it ended (N1).

        A compile whose page kept moving (compiler.lost_race) is tried once more, whole; if
        it loses again nothing is recorded and the note waits. "unchanged" and "rejected" are
        recorded with the note (Store.record_note(filed=...)), which ends its wait in that
        write; "saved" ended it with the save. PageTooLong is recorded as rejected
        (compile.error, as before N1) and ends the wait, then goes on. Any other error is
        recorded as rejected only when `record_errors` (a request's note, as before N1), never
        ends the wait, and goes on."""
        project_id = note.project_id
        try:
            result = compile_note(self.store, note, self.gate)  # retries a moved page
            if lost_race(result):  # rule 10: the whole compile, once more
                result = compile_note(self.store, note, self.gate)
        except PageTooLong:
            with contextlib.suppress(Exception):
                detail = {"violations": [COMPILE_ERROR]}
                self.store.record_note(project_id, "rejected", detail, filed=note.id)
            raise
        except Exception:
            # The note is stored: its outcome is recorded all the same, with a fixed
            # code (never the error, which may quote the note), and the error goes on.
            if record_errors:
                with contextlib.suppress(Exception):
                    self.store.record_note(project_id, "rejected", {"violations": [COMPILE_ERROR]})
            raise
        if lost_race(result):  # nothing recorded: the note waits
            return result
        if result.status != "saved":  # a saved page was recorded with the save itself
            detail: dict[str, Any] = {"page": result.slug}
            if result.gate and result.gate.violations:
                detail["violations"] = _codes(result.gate.violations)
            self.store.record_note(project_id, result.status, detail, filed=note.id)
        elif self.saved is not None:
            with contextlib.suppress(Exception):  # the note is saved whatever this does
                self.saved(project_id, result.slug)
        return result

    def _page_of(self, held: Note) -> tuple[str | None, bool]:
        """(the page a stored note is on, whether it waits for its page), by the Notes page's
        rule (Store.note_page): a page only when its latest version cites the note, so a note
        refused a place on its topic's page, or waiting for it, names none. One statement."""
        waiting, slug = self.store.note_page(held.project_id, held.id)
        return slug, waiting

    def _room_on_page(
        self,
        target: Project,
        text: str,
        title: str | None,
        topic: str | None,
        source_ref: str | None,
    ) -> None:
        """PageFull when the note's passages (each a claim once compiled, its text the
        passage's) would take the page it compiles into past PAGE_CLAIMS_MAX claims or
        PAGE_CHARS_MAX characters of claims (G1), unless the project holds it already
        ("unchanged"). The page is the project's page of the note's topic key (N1:
        Store.topic_page of cairn.store.note_key); a key with no page has no claims yet.
        Reads only; the store's save_page refuses a longer page all the same (PageTooLong),
        when another note filled the page meanwhile."""
        found = [span for span in chunk("cap_000000000000", text) if span.kind in PASSAGE_KINDS]
        passages, chars = len(found), sum(len(span.text) for span in found)  # as passages_of
        if passages == 0:
            return
        slug = self.store.topic_page(target.id, note_key(text, title, topic))
        alone = passages > PAGE_CLAIMS_MAX or chars > PAGE_CHARS_MAX
        page = self.store.get_page(target.id, slug) if slug is not None and not alone else None
        held = len(page.claims) if page else 0
        held_chars = claim_chars(page.claims) if page else 0
        if held + passages <= PAGE_CLAIMS_MAX and held_chars + chars <= PAGE_CHARS_MAX:
            return
        if self.store.holds_note(target.id, text, source_ref):
            return
        if passages > PAGE_CLAIMS_MAX:
            raise PageFull.alone(passages)
        if chars > PAGE_CHARS_MAX:
            raise PageFull.alone_chars(chars)
        if held + passages > PAGE_CLAIMS_MAX:
            raise PageFull.full(target.name, slug or "", held)  # held > 0: the page exists
        raise PageFull.full_chars(target.name, slug or "", held_chars)

    def written_by(self, claims: Iterable[Iterable[str]]) -> list[list[dict[str, Any]]]:
        """For each claim's span ids, one `written_by` entry per note it cites, in the order
        it first cites each (module docstring, A1); every entry, none cut. Two reads, for
        all the claims at once."""
        return self._written_by(claims)[0]

    def _written_by(
        self, claims: Iterable[Iterable[str]]
    ) -> tuple[list[list[dict[str, Any]]], dict[str, Span], dict[str, NoteOrigin]]:
        """written_by's answer, the spans it read to make it, and the origin of each note
        they come from (when it was written: 8.1), read in the same statement."""
        cited = [list(span_ids) for span_ids in claims]
        spans = self.store.get_spans(i for span_ids in cited for i in span_ids)
        origins = self.store.note_origins(span.source_id for span in spans.values())
        notes_of = {span_id: span.source_id for span_id, span in spans.items()}
        return written_entries(cited, notes_of, origins), spans, origins

    def recall(self, query: str, project: str | None = None, limit: int = 8) -> dict[str, Any]:
        """The best hits for `query`, each with who wrote what its claim cites (A1), and what
        the search left out (Q2: `searched`). BadRequest: a `limit` that is not a whole
        number of 1 to MAX_HITS, refused rather than cut (Q3; decision 112)."""
        if type(limit) is not int or not 1 <= limit <= MAX_HITS:
            raise BadRequest(HITS_RANGE)
        project_ids = [self.project(project).id] if project else None
        found = self.store.find(query, project_ids, limit)
        self._read = {}  # a search: never its words (READ_EVENTS)
        hits = found.hits
        writers, _, origins = self._written_by(h.span_ids for h in hits)
        answers = []
        for hit, entries in zip(hits, writers, strict=True):
            answer = {
                "project": hit.project,
                "page": hit.slug,
                "title": hit.title,
                "claim_id": hit.claim_id,
                "text": hit.text,
                "passage_ids": list(hit.span_ids),
                "version": hit.version,
                "score": hit.score,
                "outside": any(entry["outside"] for entry in entries),
                **_writers(entries),
                "written_at": _newest(origins[entry["note"]].created_at for entry in entries),
            }
            answers.append(answer)
        return {"hits": answers, "searched": searched(found.searched)}

    def related(self, project: str, slug: str, limit: int = 8) -> dict[str, Any]:
        """The pages of the project most related to its page `slug`, each with why and the
        claims it rests on, and what the read left out (the module's docstring, Related
        pages). Every claim says who wrote what it cites (A1), from the store's one read
        (Store.related_pages), which `written_entries` words as `_written_by` does.
        BadRequest: a `limit` that is not a whole number of 1 to RELATED_MAX, refused rather
        than cut. NotFound: a project unknown or out of reach, or a page that is not there,
        with the nearest page names, as `page` says it."""
        if type(limit) is not int or not 1 <= limit <= RELATED_MAX:
            raise BadRequest(RELATED_RANGE)
        target = self.project(project)
        found = self.store.related_pages(target.id, slug, limit)
        if found is None:  # as find_page and named_page say it of a latest page
            raise NotFound(
                f"no page {slug!r} in project {target.name!r}. {self.nearest_pages(target, slug)}"
            )
        self._read = {"page": found.slug}  # the audit event's detail (READ_EVENTS): no term
        claims = dict.fromkeys(  # each claim given, once, whatever reasons it stands in
            claim
            for page in found.pages
            for reason in page.why
            for claim in (reason.here, reason.there)
            if claim is not None
        )
        writers = written_entries((c.span_ids for c in claims), found.notes_of, found.origins)
        stated = {
            claim: {
                "claim_id": claim.claim_id,
                "text": claim.text,
                "passage_ids": list(claim.span_ids),
                "outside": any(entry["outside"] for entry in entries),
                **_writers(entries),
                "written_at": _newest(found.origins[e["note"]].created_at for e in entries),
            }
            for claim, entries in zip(claims, writers, strict=True)
        }
        return {
            "project": target.name,
            "page": found.slug,
            "title": found.title,
            "version": found.version,
            "related": [
                {
                    "page": page.slug,
                    "title": page.title,
                    "version": page.version,
                    "why": [
                        {
                            "kind": reason.kind,
                            "term": reason.term,
                            "term_kind": reason.term_kind,
                            **_held_by(reason, stated),
                            "there": stated[reason.there],
                        }
                        for reason in page.why
                    ],
                    "more": page.more,
                }
                for page in found.pages
            ],
            "searched": related_searched(found.left),
        }

    def page(
        self,
        project: str,
        slug: str,
        version: int | None = None,
        *,
        limit: int | None = None,
        before: int | None = None,
    ) -> dict[str, Any]:
        """A page as stored, each claim marked `outside` when a note it cites is (A1), with a
        part of its version numbers read from their end (Q1: versions_of). Its body is
        rendered when read (compiler.body_to_read of its claims and the spans they cite, read
        once with the writers), so a version stored under an earlier rendering reads as now.
        A version an earlier build stored whose rendering would leave a code fence running
        from one claim into the next is answered with every claim, compiler.BODY_NOT_SHOWN
        as its body and `body_not_shown` true; no other answer has that field."""
        target = self.project(project)
        page = self.named_page(target, slug, version)
        writers, spans, origins = self._written_by(claim.span_ids for claim in page.claims)
        versions, total, _, following = self.versions_of(target, page.slug, limit, before)
        body, not_shown = body_to_read(page.title, page.claims, spans)
        answer = {
            "project": target.name,
            "slug": page.slug,
            "title": page.title,
            "version": page.version,
            "versions": versions,
            "body": body,
            "claims": [
                {
                    **_claim(claim),
                    "outside": any(entry["outside"] for entry in entries),
                    **_writers(entries),
                    "written_at": _newest(origins[entry["note"]].created_at for entry in entries),
                }
                for claim, entries in zip(page.claims, writers, strict=True)
            ],
            "metrics": shown_metrics(page.metrics),
        }
        if not_shown:
            answer["body_not_shown"] = True
        if len(versions) < total:  # Q1: the part leaves versions out
            answer["versions_total"] = total
            if following is not None:
                answer["versions_next"] = following
        return answer

    def versions_of(
        self, project: Project, slug: str, limit: int | None = None, before: int | None = None
    ) -> tuple[list[int], int, int | None, int | None]:
        """Q1: a part of the page's version numbers read from their end: at most `limit` (at
        most VERSIONS_AT_ONCE) of the latest below `before`, oldest first; how many versions
        it keeps in all; its latest version's number; and the `before` of the earlier part,
        None when none remains. BadRequest: a limit below 1, or a `before` that is not a
        whole number."""
        count = _part_size(limit, VERSIONS_AT_ONCE)
        if before is not None and type(before) is not int:
            raise BadRequest("'before' is a version's number, as versions_next gives it")
        newest, total, latest = self.store.version_numbers(project.id, slug, count + 1, before)
        versions = sorted(newest[:count])
        return versions, total, latest, versions[0] if len(newest) > count else None

    def evidence(
        self, project: str, slug: str, claim_id: str, version: int | None = None
    ) -> dict[str, Any]:
        """The source text behind a claim: each passage it cites, with its note (A1). E1: the
        claim as `version` of the page holds it, with the passages that version cites (None:
        the latest); the answer names the version. A version the page does not keep is
        refused as `page` refuses it (find_page); a claim that version does not hold is
        NotFound, whatever another version holds."""
        target = self.project(project)
        page = self.named_page(target, slug, version)
        claim = next((c for c in page.claims if c.id == claim_id), None)
        if claim is None:
            which = "page" if version is None else f"version {page.version} of page"
            raise NotFound(f"no claim {claim_id!r} on {which} {slug!r} in project {target.name!r}")
        self._read = {"page": page.slug, "claim": claim.id}
        sources = self.sources(target, claim.span_ids)
        origins = self.store.note_origins(note.id for _, note in sources.values())
        passages = []
        for span_id in claim.span_ids:
            if span_id not in sources:
                continue
            span, note = sources[span_id]
            unknown = NoteOrigin(UNKNOWN_WRITER, None, note.via, note.created_at, False)
            origin = origins.get(note.id, unknown)
            kind, name, via = origin[:3]
            passages.append(
                {
                    **_source(span, note),
                    # A1: who wrote its note; 8.5: and a Slack message's, who filed it
                    "written_by": {"kind": kind, "name": name} | _filed(origin),
                    "via": via,
                    "outside": is_outside(kind, via, filed_message=origin.filed_message),
                    "written_at": note.created_at,  # 8.1: the row read already holds it
                }
            )
        return {"claim": _claim(claim), "version": page.version, "passages": passages}

    def pages(
        self, project: str, *, limit: int | None = None, after: str | None = None
    ) -> dict[str, Any]:
        """The project's pages, a part at a time (Q1: pages_part)."""
        target = self.project(project)
        return {"project": target.name, **self.pages_part(target, limit, after)}

    def pages_part(
        self,
        project: Project,
        limit: int | None = None,
        after: str | None = None,
        most: int = PAGES_AT_ONCE,
    ) -> dict[str, Any]:
        """Q1: {"pages": at most `limit` (at most `most`) of the project's pages whose slug sorts
        after `after`, by slug, each summarised from its head}, with `pages_total` when the
        part leaves pages out and `pages_next` while more follow. BadRequest: a limit below
        1."""
        count = _part_size(limit, most)
        heads, total = self.store.page_heads(project.id, count + 1, after)
        part: dict[str, Any] = {"pages": [head.model_dump() for head in heads[:count]]}
        if len(part["pages"]) < total:
            part["pages_total"] = total
            if len(heads) > count:
                part["pages_next"] = part["pages"][-1]["slug"]
        return part

    def export(self, out: IO[bytes]) -> ExportSummary:
        """The workspace as a format-1 zip (Store.export) written to `out`. A connection
        limited to some projects or to reading, or a person who does not own the workspace,
        cannot take everything: Forbidden. An export taken is one event of the workspace,
        EXPORTED_EVENT, whoever took it and whether reads are audited or not: who and when."""
        try:
            summary = self.store.export(out)
        except PermissionError as refused:
            raise Forbidden(str(refused)) from None
        self.store._record_read(EXPORTED_EVENT, None, {})
        return summary

    # Erasure (H5b): a person's act, or a key's over REST; never an MCP tool.

    def may_erase(self, project: Project) -> bool:
        """Whether the caller may delete `project` and forget its notes (Store.may_erase): the
        web view links to those pages only then. A read."""
        try:
            return self.store.may_erase(project.id)
        except KeyError:
            return False

    def erasable(self, ref: str) -> Project:
        """The project named `ref` when the caller may delete it and forget its notes:
        NotFound when it is unknown or out of reach (checked first), Forbidden otherwise."""
        target = self.project(ref)
        if not self.may_erase(target):
            raise Forbidden(NOT_AN_ERASER)
        return target

    def note_in(self, project: Project, note_id: str) -> Note:
        """The note `note_id` of `project`; NotFound when it is unknown, out of reach or
        another project's."""
        note = self.store.get_note(note_id)
        if note is None or note.project_id != project.id:
            raise NotFound(f"no note {note_id!r} in project {project.name!r}")
        self._read = {"note": note.id}
        return note

    def delete_project(self, ref: str, confirm: object) -> dict[str, Any]:
        """Delete the project when `confirm` is its name exactly (Store.delete_project). NotFound
        for a project unknown or out of reach, then Forbidden for a caller who may not, then
        BadRequest for another `confirm`."""
        target = self.erasable(ref)
        if confirm != target.name:
            raise BadRequest(f"To delete project {target.name!r}, confirm with its name exactly.")
        try:
            deleted = self.store.delete_project(target.id)
        except PermissionError:  # the rule changed since the check above
            raise Forbidden(NOT_AN_ERASER) from None
        if not deleted:  # deleted meanwhile, or out of reach since
            raise NotFound(f"no project {target.name!r}")
        return {"deleted": True}

    def forget(self, ref: str, note_id: str) -> dict[str, Any]:
        """Forget the note `note_id` of the project `ref` (Store.forget_note): the pages
        rebuilt without it and those removed, by slug. NotFound for a project or note unknown
        or out of reach, or a note of another project; Forbidden for a caller who may not."""
        note = self.note_in(self.project(ref), note_id)
        try:
            forgotten = self.store.forget_note(note.id)
        except PermissionError:
            raise Forbidden(NOT_AN_ERASER) from None
        except KeyError:  # forgotten, or its project deleted, meanwhile
            raise NotFound(f"no note {note_id!r}") from None
        return {
            "note_id": forgotten.note_id,
            "rebuilt": list(forgotten.rebuilt),
            "removed": list(forgotten.removed),
        }

    def forget_preview(self, ref: str, note_id: str) -> tuple[Project, Note, Forgotten]:
        """What forgetting the note would change (Store.forget_preview), for a caller who may
        forget it: NotFound and Forbidden as for `forget`, writing nothing."""
        target = self.project(ref)
        note = self.note_in(target, note_id)
        if not self.may_erase(target):
            raise Forbidden(NOT_AN_ERASER)
        try:
            return target, note, self.store.forget_preview(note.id)
        except KeyError:
            raise NotFound(f"no note {note_id!r}") from None

    def notes_part(
        self, ref: str, limit: int, before: str | None = None
    ) -> tuple[Project, list[NoteHead], str | None]:
        """The project and a part of its notes, newest first (Store.notes_part: never their
        text), and where the next part starts. NotFound for a project unknown or out of reach.
        With reads audited it is one read.notes event of the project, with no detail."""
        target = self.project(ref)
        heads, after = self.store.notes_part(target.id, limit, before)
        self._read = {}  # the audit event's detail (READ_EVENTS): the project alone
        return target, heads, after

    def overview(
        self, project: str, *, limit: int | None = None, after: str | None = None
    ) -> dict[str, Any]:
        """The project's kind, how many notes it holds (counted, not read), and its pages a
        part at a time (Q1: pages_part)."""
        target = self.project(project)
        return {
            "project": target.name,
            **_kind(target),
            "notes": self.store.count_notes(target.id),
            **self.pages_part(target, limit, after),
        }


def _part_size(limit: int | None, most: int) -> int:
    """How many items a part of a list holds (Q1): `limit`, at most `most`; `most` when no
    limit is given. BadRequest: a limit that is not a whole number of 1 or more."""
    if limit is None:
        return most
    if type(limit) is not int or limit < 1:
        raise BadRequest("'limit' is a whole number, 1 or more")
    return min(limit, most)
