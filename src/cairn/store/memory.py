"""Projects, notes and their spans, pages, and search."""

import hashlib
import json
import re
import secrets
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, NamedTuple

import yaml

from cairn import facts as _facts
from cairn.kinds import GENERAL, KINDS
from cairn.spans import Span, chunk
from cairn.store import ranking
from cairn.store.access import LEVELS, MODELS_PREFIX, KeptFromModels, _keep_from_models
from cairn.store.audit import UNKNOWN_WRITER
from cairn.store.base import (
    _PROJECT_COLUMNS,
    _PROJECT_FIELDS,
    StoreBase,
    _has_table,
    _iso,
    _require_account,
)
from cairn.store.models import (
    Claim,
    Found,
    Note,
    NoteHead,
    Page,
    PageHead,
    Project,
    SearchHit,
    WaitingNote,
)
from cairn.store.schema import _create_index, _secure_index
from cairn.store.usage import PASSAGE_KINDS, charge_of, path_bytes
from cairn.topics import topic_key

# A note's page by the Notes page's rule (notes_part, note_page), read in one statement: the
# slug of its topic's page when the note waits no more and that page's latest version cites a
# passage of it (a claim whose span_ids hold one of its spans), else NULL. _PAGE_OF_NOTE joins
# the tables it reads to `notes`.
_PAGE_OF_NOTE = (
    " LEFT JOIN waiting ON waiting.note_id = notes.id"
    " LEFT JOIN page_keys ON page_keys.project_id = notes.project_id"
    "  AND page_keys.topic_key = notes.topic_key"
)
_CITED_PAGE = (
    "CASE WHEN waiting.note_id IS NULL AND EXISTS (SELECT 1 FROM page_heads"
    "  JOIN page_versions ON page_versions.project_id = page_heads.project_id"
    "   AND page_versions.slug = page_heads.slug"
    "   AND page_versions.version = page_heads.version,"
    "  json_each(page_versions.claims) AS claim,"
    "  json_each(claim.value, '$.span_ids') AS cited"
    "  JOIN spans ON spans.id = cited.value"
    "  WHERE page_heads.project_id = notes.project_id"
    "   AND page_heads.slug = page_keys.slug AND spans.note_id = notes.id)"
    "  THEN page_keys.slug END"
)


class NoteOrigin(NamedTuple):
    """Who wrote a note and how it came (MemoryStore.note_origins)."""

    kind: str
    name: str | None
    via: str | None
    created_at: str
    filed_message: bool  # a Slack message someone filed, which created the note


PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
TITLE_MAX = 80
# The events a page save records, each with the only detail fields its caller may give and
# their kinds ("count": an int of 0 or more, never a bool; "name": text of 1 to NAME_MAX
# printable characters): a compiled note ("note.saved", none) or a model's arrangement of
# the claims the page already holds, or a tidy with no model ("page.arranged",
# cairn.arrange). The store adds "page" and "version" itself.
PAGE_DETAIL: dict[str, dict[str, str]] = {
    "note.saved": {},
    "page.arranged": {
        "from_version": "count",
        "model": "name",
        "moved": "count",
        "merged": "count",
    },
}
PAGE_EVENTS = tuple(PAGE_DETAIL)
NAME_MAX = 80
# Rows are scoped by resolving their project through the bound account and the view's reach,
# bound as _scope gives them: the account id, then the reach twice (StoreBase._reach).
_IN_REACH = "(? IS NULL OR {} IN (SELECT value FROM json_each(?)))"
_OWN_PROJECT = (
    "project_id IN (SELECT id FROM projects WHERE account_id = ? AND "
    + _IN_REACH.format("id")
    + ")"
)
_OWN_SPAN = (
    "EXISTS (SELECT 1 FROM notes JOIN projects ON projects.id = notes.project_id"
    " WHERE notes.id = spans.note_id AND projects.account_id = ? AND "
    + _IN_REACH.format("projects.id")
    + ")"
)


def _page_detail(event: str, detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """`detail` when it has exactly `event`'s fields, each of its kind; ValueError otherwise,
    naming the field and never its value."""
    if event not in PAGE_DETAIL:
        raise ValueError(f"a page save records one of {PAGE_EVENTS}, not {event!r}")
    fields, given = PAGE_DETAIL[event], dict(detail or {})
    if set(given) != set(fields):
        raise ValueError(f"{event} records exactly the fields {sorted(fields)}")
    for name, kind in fields.items():
        value = given[name]
        if kind == "count":
            ok = type(value) is int and value >= 0
        else:
            ok = isinstance(value, str) and 0 < len(value) <= NAME_MAX and value.isprintable()
        if not ok:
            raise ValueError(f"{event} {name} must be a {kind}")
    return given


def is_project_name(name: str) -> bool:
    """Whether `name` can name a project (taken or not): PROJECT_NAME, and never the id prefix
    "br_" in any case. create_project checks the same."""
    return bool(PROJECT_NAME.fullmatch(name)) and not name.casefold().startswith("br_")


class StalePage(Exception):
    """A page save named a version that is no longer the page's latest; nothing was written."""

    def __init__(self, expected: int, found: int) -> None:
        self.expected, self.found = expected, found
        super().__init__(f"the page is at version {found}, not {expected}")


class CitesForgotten(StalePage):
    """A page save into a project that has forgotten a note cited a span the store no longer
    holds: it was built from a note forgotten while it was being compiled or arranged
    (store/erasure.py). Nothing was written; a writer that reads the page again finds the
    note gone. A StalePage, so every writer that retries a moved page retries this too."""

    def __init__(self, expected: int, found: int) -> None:
        super().__init__(expected, found)
        self.args = ("a claim cites a note that was forgotten meanwhile: read the page again",)


class FiledElsewhere(ValueError):
    """N1: a note the project holds already (the same text and source_ref) was sent again for
    another topic: nothing was stored or recorded. `held` is the note as stored, with the
    key it is filed under. The message names no text."""

    def __init__(self, held: Note) -> None:
        self.held = held
        super().__init__("this note is stored already, under another topic: a note is filed once")


class ForgottenNote(ValueError):
    """A note whose text was forgotten in this project (forget_note) was written to it
    again, under any source_ref: nothing was stored or recorded. The message names no text."""

    def __init__(self) -> None:
        super().__init__(
            "this note was forgotten in this project: the same text is not stored there again"
        )


class NoteRefused(Exception):
    """The note guard found something in a note: nothing of it was stored, and the
    refusal was recorded (note.refused). `findings`: (field, Finding) pairs, the field one
    of SCREENED_FIELDS. Neither the message nor the findings hold the text."""

    def __init__(self, findings: list[tuple[str, Any]]) -> None:
        self.findings = findings
        rules = ", ".join(dict.fromkeys(finding.rule for _, finding in findings))
        super().__init__(f"the note guard refused the note: {rules}")


# The most characters each of a note's fields beside its text may have (add_note refuses
# more, FieldTooLong; import_export cuts more to the bound): with them, what a note holds
# beside its text is
# bounded, and the storage cap (cairn.store.usage), which counts the text, bounds the rest.
FIELD_MAX = {"title": 200, "topic": 200, "label": 80, "source_ref": 500, "via": 40}
# The most claims a page holds, on every install (H6c): save_page refuses more (PageTooLong)
# and import_export refuses an archive with a longer page; cairn.memory refuses a note that
# would take its page past it before storing the note. A page's every save rewrites its
# body, claims and search rows, so its size bounds the cost of each note added to it.
PAGE_CLAIMS_MAX = 2_000
# The most characters a page's claims hold in all, on every install (G1), beside its claims:
# save_page refuses a note's save past it (PageTooLong) and cairn.memory refuses a note that
# would take its page past it before storing the note. Every save lints and indexes the whole
# page again: a page fed notes of dense facts cost 13 to 20 s a save by its 40th note.
# Measured on an M-series laptop with other work running (load 11 to 21), one note's save
# (compile, lint and index) onto a page of 72 KB notes of 1,500 numbers a passage, the
# costliest shape measured: 1.8 to 2.4 s at 360,000 characters, 2.2 to 2.6 s at 432,000,
# 2.8 to 4.9 s at 510,000. 400,000 is the bound. A page already larger when the bound came
# stays as it is: forgetting a note rebuilds it and arranging saves it, neither adding a
# claim, and no note's save is taken.
PAGE_CHARS_MAX = 400_000


def claim_chars(claims: Iterable[Claim]) -> int:
    """The characters of these claims' texts, in all: what PAGE_CHARS_MAX bounds."""
    return sum(len(claim.text) for claim in claims)


class PageTooLong(ValueError):
    """save_page was given more than PAGE_CLAIMS_MAX claims for page `slug`, or (`chars` not
    None) a note's save whose claims hold `chars` characters, past PAGE_CHARS_MAX: nothing
    was written. The message names the page and the bound met, never a claim."""

    def __init__(self, slug: str, claims: int, chars: int | None = None) -> None:
        self.slug, self.claims, self.chars = slug, claims, chars
        if chars is None:
            super().__init__(
                f"page {slug!r} would hold {claims:,} claims: a page holds at most"
                f" {PAGE_CLAIMS_MAX:,}"
            )
        else:
            super().__init__(
                f"page {slug!r} would hold {chars:,} characters of claims: a page holds at"
                f" most {PAGE_CHARS_MAX:,}"
            )


DESCRIPTION_MAX = 500  # characters of a project's description: a sentence or two on what it is for


class DescriptionTooLong(ValueError):
    """A project's description is longer than DESCRIPTION_MAX characters; nothing was made."""

    def __init__(self, length: int) -> None:
        self.length = length
        super().__init__(
            f"a project's description has {length} characters:"
            f" it may have at most {DESCRIPTION_MAX}"
        )


class FieldTooLong(ValueError):
    """A note's `field` (one of FIELD_MAX) is longer than its `bound` in characters, and
    nothing of the note was stored."""

    def __init__(self, field: str, length: int, bound: int) -> None:
        self.field, self.length, self.bound = field, length, bound
        super().__init__(f"the note's {field} has {length} characters: it may have at most {bound}")


# The most UTF-8 bytes the headings above a span (its heading path, every heading from the
# note's first level down to its own section; a heading's own path holds it too) may take in
# all, on every install (H6c). A heading is stored with every span under it, and a passage
# costs about 380 bytes of rows whatever its length (its span, claim and search rows), so
# its headings may cost about as much again and no more. A note past it is refused, never
# cut: a page's headings come from this path, and a cut one is a heading nobody wrote.
HEADINGS_MAX = 400
# A note's heading spans may outnumber its passages by at most HEADINGS_OVER_PASSAGES. A
# heading span is no passage, so its rows are paid for by its path's charge and the note's
# bytes alone; a note of short headings under a long one made rows past what both pay for.
# An outline of a few headings with nothing under them still goes in.
HEADINGS_OVER_PASSAGES = 8


class HeadingsTooLong(FieldTooLong):
    """A span of the note sits under headings of `length` UTF-8 bytes in all, past
    HEADINGS_MAX, and nothing of the note was stored. Its `field` is "headings"; the message
    names the bound, never a heading."""

    def __init__(self, length: int) -> None:
        self.field, self.length, self.bound = "headings", length, HEADINGS_MAX
        ValueError.__init__(
            self,
            f"a passage of the note sits under headings of {length} bytes in all: the"
            f" headings above a passage may take at most {HEADINGS_MAX} bytes",
        )


class TooManyHeadings(HeadingsTooLong):
    """The note's heading spans (`length`) outnumber its passages by more than
    HEADINGS_OVER_PASSAGES (`bound`: its passages plus that), and nothing of the note was
    stored. A HeadingsTooLong, so whatever refuses one refuses this; the message gives the
    numbers, never a heading."""

    def __init__(self, headings: int, passages: int) -> None:
        bound = passages + HEADINGS_OVER_PASSAGES
        self.field, self.length, self.bound = "headings", headings, bound
        ValueError.__init__(
            self,
            f"the note has {headings} headings over {passages} passages: its headings may"
            f" outnumber its passages by at most {HEADINGS_OVER_PASSAGES}",
        )


def check_headings(spans: Sequence[Span]) -> None:
    """HeadingsTooLong for the first span whose heading path passes HEADINGS_MAX bytes, each
    heading counted as it is stored (cairn.store.usage.path_bytes: JSON without its quotes,
    which is its UTF-8 bytes for ordinary text and more for what JSON escapes, a quote or
    backslash 2, a control character 6); then TooManyHeadings when the note's heading spans
    outnumber its passages by more than HEADINGS_OVER_PASSAGES."""
    for span in spans:
        if (size := path_bytes(span.heading_path)) > HEADINGS_MAX:
            raise HeadingsTooLong(size)
    headings = sum(span.kind == "heading" for span in spans)
    passages = sum(span.kind in PASSAGE_KINDS for span in spans)
    if headings > passages + HEADINGS_OVER_PASSAGES:
        raise TooManyHeadings(headings, passages)


# The most facts (cairn.facts: numbers, dates, versions, paths, links, flags, inline code and
# code blocks) a note may hold, on every install (G1). Passages are bounded by the claims a
# page holds; what each holds was not, and a compile's time and memory grow with its facts: a
# 4 MiB note of numbers (some 600,000 facts) took 17 to 31 s and 1.6 to 2.2 GB to compile.
# Measured on an M-series laptop with other work running (load 3 to 7), one note a process:
# links, the costliest shape, stored and compiled in 1.90 s at 30,000 facts (+106 MB) and
# 2.22 s at 35,000 (best of five), 2.84 s at 40,000 (best of three); paths, numbers, dates
# and versions cost less. 30,000 is the bound.
FACTS_MAX = 30_000


class TooManyFacts(ValueError):
    """The note holds `facts` facts, past FACTS_MAX, and nothing of it was stored. The message
    gives the count and the bound, never a fact."""

    def __init__(self, facts: int) -> None:
        self.facts, self.bound = facts, FACTS_MAX
        super().__init__(
            f"the note holds {facts:,} facts (numbers, dates, versions, paths, links, flags"
            f" and code): a note may hold at most {FACTS_MAX:,}; split it into smaller notes"
        )


def count_facts(spans: Iterable[Span]) -> int:
    """How many facts cairn.facts.extract_facts finds in these spans, in all: the same
    patterns, in the same order, claiming characters the same way, in one pass each over a
    span's text, with no Fact built and none kept. (facts.py is frozen; tests/unit/
    test_fact_bound_unit.py holds the two equal.)"""
    found = 0
    for span in spans:
        text = span.text
        claimed = _facts._Claimed(len(text))
        for start, end, _, _ in _facts._fenced(text):
            claimed.add(start, end)
            found += 1
        for marker in _facts._ORDERED_MARKER.finditer(text):
            claimed.add(*marker.span())
        text = _facts._emphasis(text)  # an underscore of emphasis by a fact reads as "*"
        for kind, pattern in _facts._PATTERNS:
            for match in _facts._FINDERS.get(kind, pattern.finditer)(text):
                if not claimed.overlaps(*match.span()):
                    claimed.add(*match.span())
                    found += 1
    return found


def check_facts(spans: Sequence[Span]) -> None:
    """TooManyFacts when the note's spans hold more than FACTS_MAX facts (count_facts)."""
    if (found := count_facts(spans)) > FACTS_MAX:
        raise TooManyFacts(found)


def check_note_fields(fields: Mapping[str, str | None]) -> None:
    """FieldTooLong for the first of a note's fields, in FIELD_MAX's order, that passes its
    bound; None and missing fields pass."""
    for field, bound in FIELD_MAX.items():
        value = fields.get(field)
        if value is not None and len(value) > bound:
            raise FieldTooLong(field, len(value), bound)


def cut_note_fields(fields: Mapping[str, str | None]) -> tuple[dict[str, str | None], int]:
    """The fields with each of FIELD_MAX's that passes its bound cut to it (its first
    `bound` characters), and how many were cut: how import_export takes an export written
    before the bounds. Any other field, the text among them, is kept whole."""
    kept, cut = dict(fields), 0
    for field, bound in FIELD_MAX.items():
        value = kept.get(field)
        if value is not None and len(value) > bound:
            kept[field], cut = value[:bound], cut + 1
    return kept, cut


class NameTaken(ValueError):
    """A project was asked for under a name a project of the workspace already has (ignoring
    case), and nothing was made. Names are unique per workspace, so this is said also when
    that project is one the view does not reach; nothing else of it is told."""

    def __init__(self, name: str, message: str | None = None) -> None:
        self.name = name
        super().__init__(message or f"project name already used: {name!r}")


class ProjectTaken(NameTaken):
    """`create_projects` was asked for a name that a project of another kind, one the view
    reaches, already has, and made nothing. `project`: that project."""

    def __init__(self, project: Project, kind: str) -> None:
        self.project = project
        message = f"project name already used, by a project of another kind: {project.name!r}"
        super().__init__(project.name, message)


def _check_new_project(name: str, kind: str) -> None:
    """ValueError for a kind not in cairn.kinds.KINDS (checked first) or an invalid name."""
    if kind not in KINDS:
        raise ValueError(f"unknown project kind: {kind!r}")
    if not PROJECT_NAME.fullmatch(name):
        raise ValueError(f"invalid project name: {name!r}")
    if name.casefold().startswith("br_"):  # a name must never read as another project's id
        raise ValueError(f"project names may not start with 'br_', the id prefix: {name!r}")


def kind_everyone(kind: str) -> str:
    """What any member gets of a new project of `kind` when its maker does not say: "none"
    (private) for a kind that is private (cairn.kinds.Kind.private), else "write"."""
    return "none" if KINDS[kind].private else "write"


class MemoryMethods(StoreBase):
    # Projects
    def create_project(
        self,
        name: str,
        description: str = "",
        kind: str = GENERAL,
        *,
        everyone: str | None = None,
    ) -> Project:
        """A project made by the view's person (its maker; None for a view acting for no
        person), whose rule gives any member `everyone` ("write", "read" or "none": private;
        None: the kind's default, kind_everyone). ValueError for an invalid name, a kind
        that is not in cairn.kinds.KINDS or another `everyone`; NameTaken (a ValueError) for
        a name this account already uses; DescriptionTooLong (a ValueError) past
        DESCRIPTION_MAX characters, which no cap would otherwise count."""
        _check_new_project(name, kind)
        if len(description) > DESCRIPTION_MAX:
            raise DescriptionTooLong(len(description))
        if everyone is None:
            everyone = kind_everyone(kind)
        if everyone not in LEVELS:
            raise ValueError(f"everyone is one of {', '.join(LEVELS)}, not {everyone!r}")
        with self._write() as con:
            _require_account(con, self._account_id)
            if self._project_named(con, name) is not None:
                raise NameTaken(name)
            self._admit_projects(con, 1)  # OverLimit
            return self._insert_project(con, name, description, kind, everyone)

    def create_projects(self, wanted: Sequence[tuple[str, str]]) -> list[Project]:
        """Each (name, kind) in `wanted` as a project, in one transaction: all of them or none.
        A project that already has the name (ignoring case) and that kind is kept as it is and
        returned in its place, so the same request can be made twice, or by two at once.
        A new one gets its kind's default rule (kind_everyone).
        ValueError, with nothing written: a kind not in KINDS or an invalid name (both checked
        before the transaction), one name twice, ignoring case, (ProjectTaken) a name a project
        of another kind already has, or (NameTaken) a name a project out of the view's reach
        has, which is never handed back. On a limited view, OverLimit when the projects it
        would newly make pass the cap, and nothing is made."""
        seen: set[str] = set()
        for name, kind in wanted:
            _check_new_project(name, kind)
            if name.casefold() in seen:
                raise ValueError(f"one project name asked for twice: {name!r}")
            seen.add(name.casefold())
        with self._write() as con:
            _require_account(con, self._account_id)
            reach = self._reach(con)
            found = []
            for name, kind in wanted:
                existing = self._project_named(con, name)
                if existing is not None and reach is not None and existing.id not in reach:
                    raise NameTaken(name)
                if existing is not None and existing.kind != kind:
                    raise ProjectTaken(existing, kind)
                found.append(existing)
            self._admit_projects(con, found.count(None))
            return [
                self._insert_project(con, name, "", kind, kind_everyone(kind))
                if existing is None
                else existing
                for existing, (name, kind) in zip(found, wanted, strict=True)
            ]

    def _project_named(self, con: sqlite3.Connection, name: str) -> Project | None:
        """The account's project with this name, ignoring case, inside a write, whatever the
        view's reach: names are unique per account. A caller hands it back only when it is in
        reach (create_projects); a write that creates projects is MANAGEMENT, so only a person's
        reach can leave it out."""
        row = con.execute(
            f"SELECT {_PROJECT_COLUMNS} FROM projects WHERE account_id = ? AND name = ?",
            (self._account_id, name),
        ).fetchone()
        return _project(row) if row else None

    def _insert_project(
        self,
        con: sqlite3.Connection,
        name: str,
        description: str,
        kind: str,
        everyone: str = "write",
    ) -> Project:
        project = Project(
            id="br_" + secrets.token_hex(6),
            name=name,
            description=description,
            created_at=self._stamp(),
            kind=kind,
            maker_id=self._user_id,
        )
        con.execute(
            f"INSERT INTO projects ({_PROJECT_COLUMNS}, account_id, everyone)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*(getattr(project, field) for field in _PROJECT_FIELDS), self._account_id, everyone),
        )
        detail = {"kind": kind} | ({} if everyone == "write" else {"everyone": everyone})
        self._audit(
            con, "project.created", project=project.name, detail=detail, at=project.created_at
        )
        if KINDS[kind].private:  # M4: a private kind's pages start kept from every model
            _keep_from_models(con, self._account_id, project.id, project.created_at)
        return project

    def list_projects(self) -> list[Project]:
        """Sorted by name, case-insensitively."""
        with self._read() as con:
            rows = con.execute(
                f"SELECT {_PROJECT_COLUMNS} FROM projects"
                f" WHERE account_id = ? AND {_IN_REACH.format('id')} ORDER BY name",
                self._scope(con),
            ).fetchall()
        return [_project(row) for row in rows]

    def writable_projects(self) -> list[Project]:
        """The projects in reach the view's person may write to, sorted as list_projects: in
        one section, the same few statements whatever the workspace or the server holds."""
        with self._read() as con:
            rows = con.execute(
                f"SELECT {_PROJECT_COLUMNS} FROM projects"
                f" WHERE account_id = ? AND {_IN_REACH.format('id')} ORDER BY name",
                self._scope(con),
            ).fetchall()
            writable = self._writable(con)
        projects = [_project(row) for row in rows]
        return [p for p in projects if writable is None or p.id in writable]

    def get_project(self, ref: str) -> Project | None:
        """An id match wins over a (case-insensitive) name match."""
        with self._read() as con:
            row = con.execute(
                f"SELECT {_PROJECT_COLUMNS} FROM projects"
                f" WHERE account_id = ? AND {_IN_REACH.format('id')} AND (id = ? OR name = ?)"
                " ORDER BY id = ? DESC LIMIT 1",
                (*self._scope(con), ref, ref, ref),
            ).fetchone()
        return _project(row) if row else None

    # Notes and their spans
    def add_note(
        self,
        project_id: str,
        text: str,
        *,
        title: str | None = None,
        topic: str | None = None,
        label: str | None = None,
        source_ref: str | None = None,
        via: str | None = None,
        within: Callable[[sqlite3.Connection, Note, bool], None] | None = None,
    ) -> tuple[Note, bool]:
        """Store a note and its spans; returns (note, created). KeyError: unknown project.
        FieldTooLong (a ValueError): a field longer than FIELD_MAX allows; nothing is stored
        or recorded.

        `within(con, note, created)`: run inside the write, once the note is stored (or the
        one the project held already is found), on its connection, before the commit; what a
        caller writes there is kept with the note or not at all, and whatever it raises rolls
        the whole write back and goes on to the caller. It opens no read or write of its own.

        The note guard is enforced here, the one place a note is written: every field
        is screened under the workspace's policy (or the view's own guard, `with_guard`),
        and a finding raises NoteRefused after recording note.refused, with nothing of
        the note stored. The matching runs before the write transaction, under the
        widest policy; inside it, the workspace's policy is read and decides which findings
        count (POLICY_KINDS), so the policy that decides is the one in force when the note
        is written, and the write lock is never held while patterns run.

        ForgottenNote (a ValueError): the project has forgotten a note with this very text,
        whatever its source_ref; checked in the write, after the project, before the guard's
        findings count, and nothing is stored or recorded.

        N1: the note's topic key (cairn.topics.topic_key) is made here, once, from its topic,
        else its title as given (`title`, else the one its text gives, up to FIELD_MAX's 200
        characters, never the 80 the stored title is cut to: note_key), and is stored with
        it. A note the project holds already (the same text and source_ref, found in the write
        by the index notes_by_text) is the same note when its key is the same: it is
        returned, created False, whatever its title, label or via. With another key it
        is FiledElsewhere (a ValueError), and nothing is stored or recorded: a note is filed
        once. A new note is written with a row saying it waits for its page
        (`waiting_notes`), in the same transaction."""
        fields = {"text": text, "title": title, "topic": topic, "label": label}
        fields |= {"source_ref": source_ref, "via": via}
        check_note_fields(fields)  # FieldTooLong, before anything is screened or written
        spans = chunk("cap_000000000000", text)
        check_headings(spans)  # HeadingsTooLong, the same way
        check_facts(spans)  # TooManyFacts, the same way (G1)
        key = topic_key(_topic_of(title, topic, spans))  # N1: made once, from what was given
        # OverLimit, cheaply, when a cap already refuses it (the write checks again).
        self._look_before_screening(project_id, text, source_ref)
        found = self._screen(fields)
        with self._write() as con:
            self._require_project(con, project_id)
            if _forgotten(con, project_id, hashlib.sha256(text.encode()).hexdigest()):
                raise ForgottenNote
            found = self._in_force(con, found)
            if not found:
                note, created = self._insert_note(con, project_id, fields, self._instant(), key)
                if within is not None:
                    within(con, note, created)
                return note, created
            (name,) = con.execute(
                "SELECT name FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            rules = list(dict.fromkeys(finding.rule for _, finding in found))
            self._audit(con, "note.refused", project=name, detail={"rules": rules})
        raise NoteRefused(found)  # after the commit, which keeps the refusal's record

    def _insert_note(
        self,
        con: sqlite3.Connection,
        project_id: str,
        fields: dict[str, str | None],
        at: datetime,
        key: str,
    ) -> tuple[Note, bool]:
        """The note and its spans, on `con`, stored at `at` and metered in that instant's
        month, filed under the topic key `key` and waiting for its page. An identical note
        already stored (same text and source_ref) is returned instead, with created False,
        counting nothing, when its key is `key` or it was stored before N1 (`before_topics`:
        then it is the same note whatever its topic, as it was); FiledElsewhere when it is
        another."""
        text, source_ref = fields["text"] or "", fields["source_ref"]
        encoded = text.encode()
        text_sha256 = hashlib.sha256(encoded).hexdigest()
        duplicate = con.execute(
            f"SELECT {_NOTE_COLUMNS} FROM notes"
            " WHERE project_id = ? AND text_sha256 = ? AND source_ref IS ?"
            " ORDER BY seq LIMIT 1",
            (project_id, text_sha256, source_ref),
        ).fetchone()
        if duplicate:
            held = _stored_note(duplicate)
            if held.topic_key != key and not held.before_topics:
                raise FiledElsewhere(held)
            return held, False
        note_id = "cap_" + secrets.token_hex(6)
        spans = chunk(note_id, text)
        self._admit_note(con, len(encoded), at, 1, charge_of(spans))  # OverLimit
        self._meter_note(con, len(encoded), at)
        note = Note(
            id=note_id,
            project_id=project_id,
            text=text,
            title=_title(fields["title"], spans),
            topic=fields["topic"],
            label=fields["label"],
            source_ref=source_ref,
            via=fields["via"],
            created_at=_iso(at),
            topic_key=key,
        )
        _write_note(con, note, spans, self.writer)  # A1
        con.execute(
            "INSERT INTO waiting (note_id, project_id) VALUES (?, ?)", (note_id, project_id)
        )
        return note, True

    def holds_note(self, project_id: str, text: str, source_ref: str | None = None) -> bool:
        """Whether the project holds a note of this very text and source_ref, the one
        add_note would return as it is ("unchanged"). False for a project out of reach.
        A read."""
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self._read() as con:
            row = con.execute(
                "SELECT 1 FROM notes WHERE project_id = ? AND text_sha256 = ?"
                f" AND source_ref IS ? AND {_OWN_PROJECT} LIMIT 1",
                (project_id, digest, source_ref, *self._scope(con)),
            ).fetchone()
        return row is not None

    def get_note(self, note_id: str) -> Note | None:
        with self._read() as con:
            row = con.execute(
                f"SELECT {_NOTE_COLUMNS} FROM notes WHERE id = ? AND {_OWN_PROJECT}",
                (note_id, *self._scope(con)),
            ).fetchone()
        return _stored_note(row) if row else None

    def note_writers(
        self, note_ids: Iterable[str]
    ) -> dict[str, tuple[str, str | None, str | None]]:
        """Who wrote each of these notes the view reaches (A1), by note id: (the writer's kind,
        its name, the note's via). A note stored before writers were recorded has the
        kind audit.UNKNOWN_WRITER and no name. Unknown ids, and notes out of reach or of
        another workspace, are left out. A read."""
        return {note_id: origin[:3] for note_id, origin in self.note_origins(note_ids).items()}

    def note_origins(self, note_ids: Iterable[str]) -> dict[str, NoteOrigin]:
        """note_writers' answer and, in the same one statement, when each note was written
        (its `created_at`, as stored) and whether it is a Slack message someone filed (a
        `slack_notes` row of how "filed" that created the note; a tie to a note the project
        held already is not). A read."""
        wanted = json.dumps(list(dict.fromkeys(note_ids)))
        with self._read() as con:
            rows = con.execute(
                "SELECT notes.id, note_writers.kind, note_writers.name, notes.via,"
                " notes.created_at,"
                " COALESCE(slack_notes.how = 'filed' AND slack_notes.owns = 1, 0)"
                " FROM notes LEFT JOIN note_writers ON note_writers.note_id = notes.id"
                " LEFT JOIN slack_notes ON slack_notes.note_id = notes.id"
                f" WHERE notes.id IN (SELECT value FROM json_each(?)) AND {_OWN_PROJECT}",
                (wanted, *self._scope(con)),
            ).fetchall()
        return {
            note_id: NoteOrigin(kind or UNKNOWN_WRITER, name, via, created_at, bool(filed))
            for note_id, kind, name, via, created_at, filed in rows
        }

    def list_notes(self, project_id: str) -> list[Note]:
        """Oldest first."""
        with self._read() as con:
            rows = con.execute(
                f"SELECT {_NOTE_COLUMNS} FROM notes"
                f" WHERE project_id = ? AND {_OWN_PROJECT} ORDER BY seq",
                (project_id, *self._scope(con)),
            ).fetchall()
        return [_stored_note(row) for row in rows]

    def notes_part(
        self, project_id: str, limit: int, before: str | None = None
    ) -> tuple[list[NoteHead], str | None]:
        """At most `limit` of the project's notes stored before the note `before` (from the
        newest when None), newest first, each as a NoteHead (never its text); and where the
        next part starts: the last note's id when the part is full, else None (a full last
        part's next is empty). Scoped as list_notes: nothing for a project out of reach, and
        a `before` that is no note of the project starts nothing. One statement, which reads
        at most `limit` rows whatever the project holds.

        A note's page is its topic's page only when that page's latest version cites a
        passage of the note (a claim whose span_ids hold one of its spans): a note refused a
        place (a page filled before it saved, or rejected) waits no more and is on no page,
        whatever its topic. For each row the statement reads the claims of one page version
        at most, bounded as a page is (PAGE_CLAIMS_MAX)."""
        with self._read() as con:
            rows = con.execute(
                "SELECT notes.id, notes.title, notes.created_at, notes.source_ref, notes.via,"
                " COALESCE(note_writers.kind, ?), note_writers.name,"
                f" waiting.note_id IS NOT NULL, {_CITED_PAGE}"
                " FROM notes LEFT JOIN note_writers ON note_writers.note_id = notes.id"
                f"{_PAGE_OF_NOTE} WHERE notes.project_id = ? AND notes.{_OWN_PROJECT}"
                " AND (? IS NULL OR notes.seq <"
                "  (SELECT seq FROM notes AS cut WHERE cut.id = ? AND cut.project_id = ?))"
                " ORDER BY notes.seq DESC LIMIT ?",
                (
                    UNKNOWN_WRITER,
                    project_id,
                    *self._scope(con),
                    before,
                    before,
                    project_id,
                    max(0, limit),
                ),
            ).fetchall()
        heads = [
            NoteHead(
                **dict(zip(NoteHead.model_fields, (*row[:7], bool(row[7]), row[8]), strict=True))
            )
            for row in rows
        ]
        full = limit > 0 and len(heads) == limit
        return heads, heads[-1].id if full else None

    def note_page(self, project_id: str, note_id: str) -> tuple[bool, str | None]:
        """Where a note of the project stands, by notes_part's rule: whether it waits for its
        page, and its page's slug when that page's latest version cites a passage of it (it
        waits no more), else None. (False, None) for a note the project does not hold or a
        project out of reach. One statement, which reads the claims of one page version at
        most."""
        with self._read() as con:
            row = con.execute(
                f"SELECT waiting.note_id IS NOT NULL, {_CITED_PAGE} FROM notes{_PAGE_OF_NOTE}"
                f" WHERE notes.id = ? AND notes.project_id = ? AND notes.{_OWN_PROJECT}",
                (note_id, project_id, *self._scope(con)),
            ).fetchone()
        return (bool(row[0]), row[1]) if row else (False, None)

    def note_spans(self, note_id: str) -> list[Span]:
        """The note's spans in document order: exactly chunk(note.id, note.text)."""
        with self._read() as con:
            rows = con.execute(
                f"SELECT {_SPAN_COLUMNS} FROM spans"
                f" WHERE note_id = ? AND {_OWN_SPAN} ORDER BY ordinal",
                (note_id, *self._scope(con)),
            ).fetchall()
        return [_span(row) for row in rows]

    def get_spans(self, span_ids: Iterable[str]) -> dict[str, Span]:
        """The known spans among `span_ids`, by id; unknown ids are left out.

        The result follows the order in which ids were first requested."""
        wanted = list(dict.fromkeys(span_ids))
        with self._read() as con:
            rows = con.execute(
                f"SELECT {_SPAN_COLUMNS} FROM spans"
                f" WHERE id IN (SELECT value FROM json_each(?)) AND {_OWN_SPAN}",
                (json.dumps(wanted), *self._scope(con)),
            ).fetchall()
        found = {row[0]: _span(row) for row in rows}
        return {span_id: found[span_id] for span_id in wanted if span_id in found}

    def spans_within(self, span_ids: Iterable[str], most: int) -> dict[str, Span]:
        """As get_spans, leaving out as well every span whose text or headings (as stored)
        are longer than `most` characters: their text is compared in SQL and never
        returned. One statement, narrowed as get_spans is (_OWN_SPAN)."""
        wanted = list(dict.fromkeys(span_ids))
        with self._read() as con:
            rows = con.execute(
                f"SELECT {_SPAN_COLUMNS} FROM spans"
                " WHERE id IN (SELECT value FROM json_each(?))"
                " AND length(text) <= ? AND length(heading_path) <= ?"
                f" AND {_OWN_SPAN}",
                (json.dumps(wanted), most, most, *self._scope(con)),
            ).fetchall()
        found = {row[0]: _span(row) for row in rows}
        return {span_id: found[span_id] for span_id in wanted if span_id in found}

    # Pages
    def save_page(
        self,
        project_id: str,
        slug: str,
        title: str,
        body: str,
        claims: Sequence[Claim],
        metrics: Mapping[str, Any] | None = None,
        *,
        expected_version: int | None = None,
        event: str = "note.saved",
        detail: Mapping[str, Any] | None = None,
        topic: str | None = None,
        filed: str | None = None,
        by_model: bool = False,
    ) -> Page:
        """Write the next version of a page and re-index it for search. KeyError: unknown project.
        The save is recorded in the same transaction as `event`, one of PAGE_EVENTS: note.saved
        (a compiled note, the default) or page.arranged (cairn.arrange). Its detail is
        `detail`, exactly the fields PAGE_DETAIL gives the event, each of its kind, with
        "page" and "version" set by the store. ValueError: another event, or any other
        detail, and nothing is written.

        With `expected_version` (0: the page does not exist yet) the save happens only when the
        page's latest version is that one, checked in the write transaction; otherwise it
        raises StalePage and writes nothing, search index and audit log included.

        In a project that has forgotten a note, a save whose claims cite a span the store no
        longer holds raises CitesForgotten (a StalePage) and writes nothing: a page read or
        drafted before the note was forgotten never brings its text back.

        Topics (N1): `topic` is the topic key the save is for (the compiler's). When the slug
        is another topic's, or the key has another page of the project, the save raises
        StalePage and writes nothing, so a compile that chose its page on what it read
        chooses again. A page made by a save takes `topic` as its key, or, for a save made
        outside the compiler (no `topic`), the key of `title` when no other page of the
        project holds that key; a page that has a key keeps it, whatever later saves call it.
        `filed`: the id of the note this save files, whose waiting row is removed in the
        same transaction.

        `by_model` (M4): the save holds what a model answered (cairn.arrange). Then, when the
        project is at "never", it raises KeptFromModels and writes nothing, the state read in
        the statement that reads the page's latest version, so it costs no statement of its
        own; an answer that arrives after a change to "never" is never saved."""
        given = _page_detail(event, detail)
        claims = tuple(claims)
        if len(claims) > PAGE_CLAIMS_MAX:
            raise PageTooLong(slug, len(claims))
        if event == "note.saved" and (chars := claim_chars(claims)) > PAGE_CHARS_MAX:
            raise PageTooLong(slug, len(claims), chars)  # G1: a note's save, never another
        claims_json = json.dumps([claim.model_dump(mode="json") for claim in claims])
        metrics_json = json.dumps(dict(metrics or {}))
        index = self._index
        with self._write() as con:
            self._require_project(con, project_id)
            if by_model:  # the latest version, and whether the project is at "never"
                latest, kept = con.execute(
                    "SELECT COALESCE(MAX(version), 0), EXISTS (SELECT 1 FROM settings"
                    "  WHERE account_id = ? AND name = ? AND value = 'never')"
                    " FROM page_versions WHERE project_id = ? AND slug = ?",
                    (self._account_id, MODELS_PREFIX + project_id, project_id, slug),
                ).fetchone()
                if kept:
                    raise KeptFromModels(project_id)
            else:
                (latest,) = con.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM page_versions"
                    " WHERE project_id = ? AND slug = ?",
                    (project_id, slug),
                ).fetchone()
            if expected_version is not None and latest != expected_version:
                raise StalePage(expected_version, latest)
            if not _keyed(con, project_id, slug, title, topic):
                raise StalePage(latest if expected_version is None else expected_version, latest)
            if _cites_forgotten(con, project_id, claims):
                raise CitesForgotten(
                    latest if expected_version is None else expected_version, latest
                )
            version = latest + 1
            row = (project_id, slug, version, title, body, claims_json, metrics_json, self._stamp())
            _write_page_version(con, self._account_id, index, row, claims)
            self._keep_versions(con, project_id, slug)  # a plan's bound
            self._keep_history(con)  # the storage cap's
            (name,) = con.execute(
                "SELECT name FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            recorded = {**given, "page": slug, "version": version}
            self._audit(con, event, project=name, detail=recorded, at=row[-1])
            if filed is not None:
                con.execute(
                    "DELETE FROM waiting WHERE note_id = ? AND project_id = ?", (filed, project_id)
                )
        return _page(row)

    def get_page(self, project_id: str, slug: str, version: int | None = None) -> Page | None:
        """The latest version, or the given one."""
        with self._read() as con:
            row = con.execute(
                f"SELECT {_PAGE_COLUMNS} FROM page_versions"
                " WHERE project_id = ? AND slug = ? AND (? IS NULL OR version = ?)"
                f" AND {_OWN_PROJECT}"
                " ORDER BY version DESC LIMIT 1",
                (project_id, slug, version, version, *self._scope(con)),
            ).fetchone()
        return _page(row) if row else None

    def page_claims(
        self, project_id: str, slug: str, most: int
    ) -> tuple[int, tuple[Claim, ...] | None, dict[str, Any] | None] | None:
        """The page's latest version number, its claims and its metrics, never its body:
        claims or metrics are None when their stored text is longer than `most` characters,
        compared in SQL and not returned. None when there is no such page in reach. One
        statement, narrowed as get_page is (_OWN_PROJECT)."""
        with self._read() as con:
            row = con.execute(
                "SELECT version,"
                " CASE WHEN length(claims) <= ? THEN claims END,"
                " CASE WHEN length(metrics) <= ? THEN metrics END"
                " FROM page_versions WHERE project_id = ? AND slug = ?"
                f" AND {_OWN_PROJECT} ORDER BY version DESC LIMIT 1",
                (most, most, project_id, slug, *self._scope(con)),
            ).fetchone()
        if row is None:
            return None
        version, claims, metrics = row
        return (
            version,
            None if claims is None else tuple(Claim(**c) for c in json.loads(claims)),
            None if metrics is None else json.loads(metrics),
        )

    def page_for_model(self, project_id: str, slug: str) -> tuple[Page, bool, bool] | None:
        """The page's latest version, whether its project is at "never" (M4: its pages go to
        no model) and whether its people chose "allowed" themselves (Privacy 4), in the one
        statement that reads the page, so the arranger learns both at no cost of its own;
        None when there is no such page in reach."""
        with self._read() as con:
            row = con.execute(
                f"SELECT {_PAGE_COLUMNS}, (SELECT value FROM settings"
                "  WHERE settings.account_id = ? AND settings.name = ? || page_versions.project_id)"
                " FROM page_versions WHERE project_id = ? AND slug = ?"
                f" AND {_OWN_PROJECT} ORDER BY version DESC LIMIT 1",
                (self._account_id, MODELS_PREFIX, project_id, slug, *self._scope(con)),
            ).fetchone()
        if row is None:
            return None
        return _page(row[:-1]), row[-1] == "never", row[-1] == "allowed"

    def list_pages(self, project_id: str) -> list[Page]:
        """Latest versions, sorted by slug."""
        with self._read() as con:
            rows = con.execute(
                f"SELECT {_PAGE_COLUMNS} FROM page_versions AS p"
                f" WHERE project_id = ? AND {_OWN_PROJECT} AND version ="
                " (SELECT MAX(version) FROM page_versions"
                "  WHERE project_id = p.project_id AND slug = p.slug)"
                " ORDER BY slug",
                (project_id, *self._scope(con)),
            ).fetchall()
        return [_page(row) for row in rows]

    def page_versions(self, project_id: str, slug: str) -> list[int]:
        with self._read() as con:
            rows = con.execute(
                "SELECT version FROM page_versions"
                f" WHERE project_id = ? AND slug = ? AND {_OWN_PROJECT} ORDER BY version",
                (project_id, slug, *self._scope(con)),
            ).fetchall()
        return [version for (version,) in rows]

    # Reads that cost what they return (Q1): a count reads no body, claims or text, and a
    # list is read a part at a time. Each is one read of a fixed number of statements.
    def page_counts(self) -> dict[str, int]:
        """How many pages each project in reach has, by project id; a project with none is left
        out. One statement, over the page heads' key only (schema step 26)."""
        with self._read() as con:
            rows = con.execute(
                f"SELECT project_id, COUNT(*) FROM page_heads WHERE {_OWN_PROJECT}"
                " GROUP BY project_id",
                self._scope(con),
            ).fetchall()
        return dict(rows)

    def count_notes(self, project_id: str) -> int:
        """How many notes the project holds (0 for a project out of reach), reading no text."""
        with self._read() as con:
            (count,) = con.execute(
                f"SELECT COUNT(*) FROM notes WHERE project_id = ? AND {_OWN_PROJECT}",
                (project_id, *self._scope(con)),
            ).fetchone()
        return count

    def page_heads(
        self, project_id: str, limit: int, after: str | None = None
    ) -> tuple[list[PageHead], int]:
        """At most `limit` of the project's pages whose slug sorts after `after` (every page
        when None), by slug, each as its latest version's head (PageHead: no body, no
        claims); and how many pages the project has in all. Nothing for a project out of reach.
        A page added between two asks sorts where its slug does, so asking again after the
        last slug given neither repeats a page nor skips one. Two statements, both on the page
        heads (schema step 26), which no version's body is read for."""
        with self._read() as con:
            scope = self._scope(con)
            rows = con.execute(
                "SELECT slug, title, version, claims, updated_at FROM page_heads"
                f" WHERE project_id = ? AND {_OWN_PROJECT} AND (? IS NULL OR slug > ?)"
                " ORDER BY slug LIMIT ?",
                (project_id, *scope, after, after, max(0, limit)),
            ).fetchall()
            (total,) = con.execute(
                f"SELECT COUNT(*) FROM page_heads WHERE project_id = ? AND {_OWN_PROJECT}",
                (project_id, *scope),
            ).fetchone()
        heads = [PageHead(**dict(zip(PageHead.model_fields, row, strict=True))) for row in rows]
        return heads, total

    def first_page_heads(self, project_id: str, limit: int) -> list[PageHead]:
        """At most `limit` of the project's page heads, by slug: page_heads' first part and
        nothing else, so no count of the project's pages is made. Nothing for a project out
        of reach. One statement on the page heads, narrowed as page_heads is."""
        with self._read() as con:
            rows = con.execute(
                "SELECT slug, title, version, claims, updated_at FROM page_heads"
                f" WHERE project_id = ? AND {_OWN_PROJECT} ORDER BY slug LIMIT ?",
                (project_id, *self._scope(con), max(0, limit)),
            ).fetchall()
        return [PageHead(**dict(zip(PageHead.model_fields, row, strict=True))) for row in rows]

    def version_numbers(
        self, project_id: str, slug: str, limit: int, before: int | None = None
    ) -> tuple[list[int], int, int | None]:
        """A part of the page's version numbers read from its end: at most `limit` of those
        below `before` (all when None), newest first; how many versions it keeps in all; and
        its latest version's number (None, with 0 kept, for a page unknown or out of reach)."""
        with self._read() as con:
            scope = self._scope(con)
            rows = con.execute(
                "SELECT version FROM page_versions WHERE project_id = ? AND slug = ?"
                f" AND {_OWN_PROJECT} AND (? IS NULL OR version < ?)"
                " ORDER BY version DESC LIMIT ?",
                (project_id, slug, *scope, before, before, max(0, limit)),
            ).fetchall()
            total, latest = con.execute(
                "SELECT COUNT(*), MAX(version) FROM page_versions WHERE project_id = ? AND slug = ?"
                f" AND {_OWN_PROJECT}",
                (project_id, slug, *scope),
            ).fetchone()
        return [version for (version,) in rows], total, latest

    def get_notes(self, note_ids: Iterable[str]) -> dict[str, Note]:
        """The known notes among `note_ids` in reach, by id, in the order the ids were
        first asked; unknown ids and notes out of reach are left out. One statement."""
        wanted = list(dict.fromkeys(note_ids))
        with self._read() as con:
            rows = con.execute(
                f"SELECT {_NOTE_COLUMNS} FROM notes"
                f" WHERE id IN (SELECT value FROM json_each(?)) AND {_OWN_PROJECT}",
                (json.dumps(wanted), *self._scope(con)),
            ).fetchall()
        found = {note.id: note for note in map(_stored_note, rows)}
        return {note_id: found[note_id] for note_id in wanted if note_id in found}

    def note_sources(
        self, note_ids: Iterable[str], most: int
    ) -> dict[str, tuple[str, str, str | None, str | None]]:
        """Where each of these notes in reach came from, never its text: (its project id, its
        title, its source_ref, its via) by note id. A note whose source_ref is longer than
        `most` characters is left out, compared in SQL. One statement, narrowed as get_notes
        is (_OWN_PROJECT)."""
        wanted = list(dict.fromkeys(note_ids))
        with self._read() as con:
            rows = con.execute(
                "SELECT id, project_id, title, source_ref, via FROM notes"
                " WHERE id IN (SELECT value FROM json_each(?))"
                f" AND length(coalesce(source_ref, '')) <= ? AND {_OWN_PROJECT}",
                (json.dumps(wanted), most, *self._scope(con)),
            ).fetchall()
        return {row[0]: tuple(row[1:]) for row in rows}

    # Topics and the notes waiting for their page (N1)
    def topic_page(self, project_id: str, key: str) -> str | None:
        """The slug of the project's page of the topic key `key` (cairn.topics), or None when
        no page of the project has it. None for a project out of reach. A read."""
        with self._read() as con:
            row = con.execute(
                "SELECT slug FROM page_keys WHERE project_id = ? AND topic_key = ?"
                f" AND {_OWN_PROJECT}",
                (project_id, key, *self._scope(con)),
            ).fetchone()
        return row[0] if row else None

    def taken_slugs(self, project_id: str, slugs: Sequence[str]) -> set[str]:
        """Which of `slugs` a page of the project has, or a topic of it holds: the addresses a
        new page may not take. None of them for a project unknown or out of reach, which has no
        page (a save into it is refused). A read."""
        wanted = json.dumps(list(dict.fromkeys(slugs)))
        with self._read() as con:
            scope = self._scope(con)
            rows = con.execute(
                "SELECT value FROM json_each(?) WHERE value IN ("
                f"SELECT slug FROM page_keys WHERE project_id = ? AND {_OWN_PROJECT} UNION ALL"
                f" SELECT slug FROM page_versions WHERE project_id = ? AND {_OWN_PROJECT})",
                (wanted, project_id, *scope, project_id, *scope),
            ).fetchall()
        return {slug for (slug,) in rows}

    def tried_waiting(self, note_id: str) -> int:
        """Count one more try of a waiting note that ended with no outcome (cairn.web.filing),
        and return how many it has had; 0 when the note waits no more. A write into its
        project: KeyError for a note unknown or out of reach, and a read-only view refuses it."""
        with self._write() as con:
            row = con.execute("SELECT project_id FROM notes WHERE id = ?", (note_id,))
            found = row.fetchone()
            if found is None:
                raise KeyError(note_id)
            self._require_project(con, found[0])
            con.execute("UPDATE waiting SET tries = tries + 1 WHERE note_id = ?", (note_id,))
            tries = con.execute("SELECT tries FROM waiting WHERE note_id = ?", (note_id,))
            counted = tries.fetchone()
        return counted[0] if counted else 0

    def waiting_notes(self) -> list[WaitingNote]:
        """Every note of every workspace that is stored and not yet on its page, in the order
        they were stored: its id, its workspace and project (ids and names), when it was
        stored, and how many tries ended with no outcome. Never its text or title. Not
        scoped: the operator's, like list_accounts. A read."""
        with self._read() as con:
            rows = con.execute(
                "SELECT waiting.note_id, accounts.id, accounts.name, projects.id, projects.name,"
                " notes.created_at, waiting.tries FROM waiting"
                " JOIN notes ON notes.id = waiting.note_id"
                " JOIN projects ON projects.id = notes.project_id"
                " JOIN accounts ON accounts.id = projects.account_id"
                " ORDER BY notes.seq"
            ).fetchall()
        fields = WaitingNote.model_fields
        return [WaitingNote(**dict(zip(fields, row, strict=True))) for row in rows]

    # The reach, inside the caller's section
    def _scope(self, con: sqlite3.Connection) -> tuple[str, str | None, str | None]:
        """The parameters of _OWN_PROJECT, _OWN_SPAN and _IN_REACH after an account id: the
        account, then the reach (a JSON array, or NULL for every project) twice."""
        reach = self._reach(con)
        ids = None if reach is None else json.dumps(sorted(reach))
        return self._account_id, ids, ids

    def _require_project(self, con: sqlite3.Connection, project_id: str) -> None:
        """KeyError unless the project belongs to the account and is in this view's reach,
        checked on `con`, inside the caller's transaction."""
        reach = self._reach(con)
        owned = con.execute(
            "SELECT 1 FROM projects WHERE id = ? AND account_id = ?", (project_id, self._account_id)
        )
        if not owned.fetchone() or (reach is not None and project_id not in reach):
            raise KeyError(project_id)

    # Search
    def search(
        self, query: str, project_ids: Sequence[str] | None = None, limit: int = 10
    ) -> list[SearchHit]:
        """Best first: BM25 over the projects this view reaches (cairn.store.ranking), matched
        in the account's own index: the hits of `find`."""
        return self.find(query, project_ids, limit).hits

    def find(self, query: str, project_ids: Sequence[str] | None = None, limit: int = 10) -> Found:
        """The hits of a search, best first, and what it left out (Q2: cairn.store.ranking).
        No words, no projects (an empty `project_ids`) or a limit below 1: no hits, before the
        database is read. Two statements of its own, the index's table and, on a view of
        every project, the account's projects; a narrowed view resolves its reach instead."""
        counted, left_out = ranking.split(query)
        if not counted or limit < 1 or (project_ids is not None and not project_ids):
            return ranking.found(counted, left_out)
        with self._read() as con:
            if not _has_table(con, self._index):  # the account has not saved a page yet
                return ranking.found(counted, left_out)
            reach = self._reach(con)
            if reach is None:  # every project: the same search, over all of them
                reach = frozenset(
                    project_id
                    for (project_id,) in con.execute(
                        "SELECT id FROM projects WHERE account_id = ?", (self._account_id,)
                    )
                )
            ranked = ranking.search(
                con, self._account_id, self._index, reach, counted, project_ids, limit
            )
            return ranking.found(counted, left_out, ranked)


_NOTE_FIELDS = tuple(Note.model_fields)
_NOTE_COLUMNS = ", ".join(_NOTE_FIELDS)
_SPAN_COLUMNS = "id, note_id, kind, heading_path, start_line, end_line, text"
_PAGE_COLUMNS = "project_id, slug, version, title, body, claims, metrics, created_at"


def _write_note(
    con: sqlite3.Connection,
    note: Note,
    spans: Sequence[Span],
    writer: tuple[str, str | None] | None,
) -> None:
    """A note and its spans (exactly chunk(note.id, note.text)), on `con`: what
    add_note and an import (cairn.store.portable) both write; and its writer (A1), (kind,
    name), or None for a note whose writer is not known (an import's, stored before)."""
    text_sha256 = hashlib.sha256(note.text.encode()).hexdigest()
    if note.topic_key is None:  # made outside the store: the key of its topic or title
        note = note.model_copy(update={"topic_key": stored_key(note)})
    con.execute(
        f"INSERT INTO notes ({_NOTE_COLUMNS}, text_sha256)"
        f" VALUES ({', '.join('?' * (len(_NOTE_FIELDS) + 1))})",
        (*(getattr(note, f) for f in _NOTE_FIELDS), text_sha256),
    )
    con.executemany(
        f"INSERT INTO spans ({_SPAN_COLUMNS}, ordinal) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(*_span_row(span), ordinal) for ordinal, span in enumerate(spans)],
    )
    if writer is not None:
        con.execute(
            "INSERT INTO note_writers (note_id, kind, name) VALUES (?, ?, ?)",
            (note.id, *writer),
        )


def _keyed(
    con: sqlite3.Connection, project_id: str, slug: str, title: str, key: str | None
) -> bool:
    """Whether a save of page `slug` may go on (save_page, inside its write), giving a page
    that has no key yet the one it is saved for: `key`, the compiler's, or without one the
    key of `title`. A page with a key keeps it: a save for another key may not go on. A
    page with none takes `key`, unless another page of the project holds it: then a save for
    `key` may not go on, and a save outside the compiler goes on leaving the page keyless."""
    owner = con.execute(
        "SELECT topic_key FROM page_keys WHERE project_id = ? AND slug = ?", (project_id, slug)
    ).fetchone()
    if owner is not None:
        return key is None or owner[0] == key
    wanted = topic_key(title) if key is None else key
    if con.execute(
        "SELECT 1 FROM page_keys WHERE project_id = ? AND topic_key = ?", (project_id, wanted)
    ).fetchone():
        return key is None
    con.execute(
        "INSERT INTO page_keys (project_id, slug, topic_key) VALUES (?, ?, ?)",
        (project_id, slug, wanted),
    )
    return True


def _forgotten(con: sqlite3.Connection, project_id: str, text_sha256: str) -> bool:
    """Whether the project has forgotten a note with this text (its SHA-256, hex)."""
    found = con.execute(
        "SELECT 1 FROM forgotten WHERE project_id = ? AND text_sha256 = ?",
        (project_id, text_sha256),
    )
    return found.fetchone() is not None


def _cites_forgotten(con: sqlite3.Connection, project_id: str, claims: Sequence[Claim]) -> bool:
    """Whether, in a project that has forgotten a note, a claim cites a span the store does not
    hold: one of a forgotten note. Only such a project is asked, so a project that never forgot
    anything costs one lookup, and a save that cites spans of its own making (the tests' and
    an operator's) is never refused there."""
    if not con.execute(
        "SELECT 1 FROM forgotten WHERE project_id = ? LIMIT 1", (project_id,)
    ).fetchone():
        return False
    cited = json.dumps(list(dict.fromkeys(i for claim in claims for i in claim.span_ids)))
    (missing,) = con.execute(
        "SELECT COUNT(*) FROM json_each(?) WHERE value NOT IN (SELECT id FROM spans"
        " WHERE id IN (SELECT value FROM json_each(?)))",
        (cited, cited),
    ).fetchone()
    return missing > 0


def _write_page_version(
    con: sqlite3.Connection, account_id: str, index: str, row: tuple, claims: Sequence[Claim]
) -> None:
    """One page version (a row of _PAGE_COLUMNS) and its claims in the account's search
    index and statistics, on `con`: what save_page and an import both write, so an imported
    page is ranked exactly as the saves that made it."""
    con.execute(f"INSERT INTO page_versions ({_PAGE_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
    if not _has_table(con, index):  # the account's first page
        _create_index(con, index)
    else:
        _secure_index(con, index)
    project_id, slug, _, title = row[:4]
    ranking.index_page(con, account_id, index, project_id, slug, title, claims)


def _project(row: tuple) -> Project:
    return Project(**dict(zip(_PROJECT_FIELDS, row, strict=True)))


def _stored_note(row: tuple) -> Note:
    return Note(**dict(zip(_NOTE_FIELDS, row, strict=True)))


def _span(row: tuple) -> Span:
    span_id, note_id, kind, heading_path, start_line, end_line, text = row
    return Span(
        id=span_id,
        source_id=note_id,
        kind=kind,
        heading_path=tuple(json.loads(heading_path)),
        start_line=start_line,
        end_line=end_line,
        text=text,
    )


def _span_row(span: Span) -> tuple:
    """A span as values for _SPAN_COLUMNS; the inverse of _span."""
    # As UTF-8, not \u escapes: what is stored is what HEADINGS_MAX counted (check_headings).
    heading_path = json.dumps(span.heading_path, ensure_ascii=False)
    return (
        span.id,
        span.source_id,
        span.kind,
        heading_path,
        span.start_line,
        span.end_line,
        span.text,
    )


def _page(row: tuple) -> Page:
    project_id, slug, version, title, body, claims, metrics, created_at = row
    return Page(
        project_id=project_id,
        slug=slug,
        title=title,
        body=body,
        version=version,
        claims=tuple(Claim(**claim) for claim in json.loads(claims)),
        metrics=json.loads(metrics),
        updated_at=created_at,
    )


def note_title(given: str | None, text: str) -> str:
    """The title add_note gives a note of `text` with `given` as its title (_title)."""
    return _title(given, chunk("cap_000000000000", text))


def note_key(text: str, title: str | None = None, topic: str | None = None) -> str:
    """The topic key add_note gives a note of `text` with this title and topic (N1): the
    key of its topic, else of its title as given, else of the title its text gives, each
    up to FIELD_MAX's 200 characters, never the 80 a stored title keeps."""
    return topic_key(_topic_of(title, topic, chunk("cap_000000000000", text)))


def stored_key(note: Note) -> str:
    """The topic key a note is filed under: the one it was stored with, else (a note
    made outside the store) the key of its topic, else its title."""
    if note.topic_key is not None:
        return note.topic_key
    return topic_key(note.topic or note.title)


def _topic_of(title: str | None, topic: str | None, spans: Sequence[Span]) -> str:
    """What a note's topic key is made of: its topic, else its title as given, else the one
    its text gives, cut at FIELD_MAX's 200 characters."""
    return topic or _title(title, spans, FIELD_MAX["title"])


_RULE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")  # a thematic break


def _title(given: str | None, spans: Sequence[Span], cut: int = TITLE_MAX) -> str:
    """The given title, else the front matter `title`, else the first ATX heading's text, else
    the first non-blank line outside the front matter that is not a rule (a thematic break:
    three or more of one of `-`, `*`, `_`, alone on the line with spaces between, such as the
    `---` opening a block read as content); stripped and cut to `cut` characters (TITLE_MAX,
    80, for the title a note is stored with).

    Headings come from the spans, so a "# comment" inside a code fence is not a heading. A
    blank candidate counts as missing; when every candidate is missing the title is "".
    """
    candidates = (
        given,
        _front_matter_title(spans),
        next((s.heading_path[-1] for s in spans if s.kind == "heading"), None),
        next(
            (
                line
                for s in spans
                if s.kind != "frontmatter"
                for line in s.text.splitlines()
                if line.strip() and not _RULE.match(line)
            ),
            None,
        ),
    )
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()[:cut].rstrip()
    return ""


def _front_matter_title(spans: Sequence[Span]) -> str | None:
    """The `title` of YAML front matter, read as text (no type coercion, so "2.10" stays
    "2.10"); None when it is missing, not a string, or the YAML does not parse."""
    if not spans or spans[0].kind != "frontmatter":
        return None
    yaml_text = "\n".join(spans[0].text.splitlines()[1:-1])  # between the "---" fences
    try:
        meta = yaml.load(yaml_text, Loader=yaml.BaseLoader)  # nosec B506 # BaseLoader: strings only
    except yaml.YAMLError:
        return None
    title = meta.get("title") if isinstance(meta, dict) else None
    return title if isinstance(title, str) else None
