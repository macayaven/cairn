"""A workspace's data as a file it can take away (export), bring back (import), and the
operator's compaction that clears what a deletion left in the database file.

The export format, version 1 (frozen by tests/acceptance/test_h5_data.py), is a zip:

    export.json                    {"format": "cairn-export", "version": 1, "exported_at",
                                    "workspace", "projects": [{"id", "name", "description",
                                    "kind", "created_at", "notes", "pages",
                                    "models" (M4), "models_decided"
                                    (Privacy 4)}]}
    projects/<name>/notes.jsonl   one line per note, oldest first: id, title, topic, label,
                                   source_ref, via, created_at, file, sha256,
                                   topic_key, before_topics (N1), writer (A1)
    projects/<name>/notes/<NNNN>-<note id>.md   each note exactly as it was written, NNNN
                                   its place in notes.jsonl from 0001
    projects/<name>/pages.jsonl      one line per page version, in the order they were saved:
                                   slug, version, title, body, claims, metrics, created_at,
                                   topic_key (N1: the page's, on each of its versions, or
                                   null for a page with none); each claim is {"id", "text",
                                   "passage_ids"} (Claim.shown), and the metrics are under
                                   the names callers meet (models.SHOWN_METRICS);
                                   a page's versions run without a gap, from 1 or, when a
                                   plan's versions_per_page dropped the older ones, from
                                   the oldest kept
    projects/<name>/pages/<slug>.md  each page's latest body (a body is as stored, but a
                                   title with a line break is written on one line)

Entries are written in that order, project by project (by name), with a fixed timestamp and
mode, so two exports of the same data differ only in `exported_at`. Keys, sessions, grants,
project sets, settings, sources and the audit log are not exported: they name the operator's
environment or the people who held access, not what the workspace knows. Nor are the items
each source wrote (I1, source_items), which go with their source: an import records none.
Nor is anything of a Slack install (K1, cairn.store.slack: the install, its links, offers and
events seen, and which notes came through it): the notes themselves are exported as any
other, and an import records none of them as Slack's.
One setting is exported: each project's Models choice (M4, below).

Models (M4). Version 1 grew a `models` on each project of the manifest, "allowed" or
"never" (access.MODELS), as the project was in the export's read; a reader that does not
know it passes it by, as the format's readers pass by every key they do not ask for, so a
build from before this one imports such an archive and starts each project as a new one of
its kind. An import puts a project the archive says is at "never" at "never", in the write
that brings it; a project of a private kind (Personal notes) arrives at "never" whatever the
archive says, and any other at "allowed" when the archive says its people chose it
(Decided, below), or not decided. An archive written before
says it of no project: each project starts as a new one of its kind, and the notice says
the archive did not say it (IMPORTED_MODELS_UNSAID); otherwise the notice says how many
arrived at "never" (IMPORTED_MODELS). A value other than the two, or an archive that says it
of some projects and not of others, is refused (ValueError). An import never targets an
existing project (a name the workspace uses is refused), so it never moves one from "never".
The workspace.imported event's detail counts the projects that arrived at "never"
(`kept_from_models`).
Decided (Privacy 4). A project's people may also have chosen "allowed", and a project nobody
decided for is neither: version 1 grew a `models_decided` on each project, true when its
people chose (allowed or never), false when nobody did; `models` stays "allowed" for a project
not at "never", so a build from before reads the archive as it always did. An import puts a
project the archive says its people chose "allowed" for at "allowed"; one nobody decided for
arrives not decided, and so does every project not at "never" of an archive that does not say
`models_decided` (one written before): its notice says so (IMPORTED_ALLOWED_UNSAID). A value
other than true or false, or an archive that says it of some projects and not of others, is
refused (ValueError).

An archive to import is hostile input. It is never extracted to disk: entries are read into
memory, at most ARCHIVE_ENTRIES_MAX of them, each at most ENTRY_BYTES_MAX bytes, and the
archive file and all its entries at most ARCHIVE_BYTES_MAX, 256 MiB, unless the caller
passes a larger `max_bytes` (counted on the bytes actually read, whatever the archive
claims); only stored or deflated, not encrypted, and every name must be one of the
layout's, once. Every file must be named by the manifest or an index line, and every line's
file must be there with its sha256. Every claim must cite spans of its own project's notes,
and be the whole of each span it cites, under the verbatim gate's rule (gate.whole) or under
the rule of the code before CORE5 (store.before_core5), so a version a tidy folded then
imports unchanged; and it must leave no code fence open that no line closes.
A version has at least one claim; a claim's id is shaped as cairn makes one (CLAIM_ID) and
it cites each span once. A span is cited from one page of its project only, across every
version, and the claims of
one version hold at most CLAIM_TEXT_FACTOR (2) times the text of the distinct spans it
cites, in UTF-8 bytes as the archive holds them: cairn compiles a note into one page whose
claims are its spans' text, so an archive past either was built to store a note's text on
more pages, or more often, than cairn would. A version's citations (its claims' span lists,
added up) are at most CITATION_FACTOR (2) times its claims and the distinct spans it cites,
added up. An import takes what cairn writes (claims of whole passages, arranged and folded;
since T5d the library's model composer, cairn.llm_compiler, passes the gate with nothing
else); a page of claims that each select sentences of a span, which only a gate named in
the verbatim gate's place lets through, is refused by the rule that a claim says what a span
it cites says.
A project's description past DESCRIPTION_MAX is cut like a note's fields, and a time longer
than TIME_CHARS_MAX characters is refused: a valid ISO 8601 time can be any length, and it
is stored as written.
A page's versions have no bound of their own here: under a storage cap the history budget
(HISTORY_FACTOR times the cap, the latest of each page always kept) applies to an import as
to a save, and without one the versions are the operator's own disk.
Every note (all six screened fields) and every page version (title and body) passes the
workspace's note guard before anything is written; then everything is written in one
transaction.

Topics (N1). Version 1 grew a `topic_key` on each line, which an archive written before
may lack, and an import reads such a line; a claim without `passage_ids` it refuses. An
import restores each page's key, so an imported workspace files a note on the page the
exported one did, also for a page whose title no longer reads as its key (a page merged
before N1, or retitled by a forget). A key
is text of at most TOPIC_KEY_MAX characters, or null for a page; a page's versions all
carry the same; two pages of a project never share one. A note keeps the key it carries, the
key it was stored with, and its `before_topics` (true: stored before N1, so sent again it is
the same note whatever its topic; Store.add_note). A line without `before_topics` is true
when it has no `topic_key` either (written before N1), else false. Without keys (an older
archive), each page takes the key of its latest title (the first page by slug, when two
titles have one key), each note cited by a page takes that page's, and any other note the
key of its topic, else its title, and each note that no page's latest version cites
waits for its page after the import, as schema step 24 makes such notes of a database wait,
so that `serve` files it (cairn.web.filing). An archive written since N1 does not say which
notes were waiting in the workspace exported: an import compiles nothing, and such a note is
imported as stored and not filed.

Writers (A1). Each line names who wrote its note: `writer` is {"kind", "name"} (a kind of
cairn.store.audit.WRITER_KINDS), or null for a note stored before writers were recorded. An
export never holds a source's name (its config and its name are the operator's, like the
sources themselves), so a source's note is {"kind": "source", "name": null}. An import
restores each writer as its line gives it, a person's name cut as audit.person_name cuts
one, and a null as no writer, so such a note is read as before (outside text when its
`via` is a source kind's). A line with no `writer` at all comes from an archive written
before writers were exported: its note is written by whoever imports it, the view's writer
(the command line's import: "operator").
"""

import contextlib
import fcntl
import hashlib
import heapq
import io
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import threading
import zipfile
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import IO, Any

from pydantic import ValidationError

from cairn.kinds import KINDS
from cairn.spans import Span, chunk
from cairn.store.access import (
    _MODELS_CHOICES,
    MODELS,
    MODELS_PREFIX,
    _choose_models,
    _keep_from_models,
)
from cairn.store.audit import ACTOR_NAME_MAX, WRITER_KINDS, person_name
from cairn.store.base import (
    _PROJECT_COLUMNS,
    _PROJECT_FIELDS,
    COMPACT_DUE,
    COMPACTED_AT,
    DEFAULT_ACCOUNT_ID,
    REWRITTEN,
    StoreBase,
    _find_account,
    _iso,
    _require_account,
    _transaction,
    is_rewritten,
    store_log,
)
from cairn.store.memory import (
    _NOTE_COLUMNS,
    _PAGE_COLUMNS,
    DESCRIPTION_MAX,
    FIELD_MAX,
    PAGE_CLAIMS_MAX,
    HeadingsTooLong,
    TooManyFacts,
    _page,
    _project,
    _stored_note,
    _write_note,
    _write_page_version,
    check_facts,
    check_headings,
    cut_note_fields,
    is_project_name,
)
from cairn.store.models import (
    Claim,
    CompactionState,
    ExportSummary,
    Note,
    Page,
    Project,
    shown_metrics,
    stored_metrics,
)
from cairn.store.usage import charge_of
from cairn.topics import one_line, topic_key

FORMAT = "cairn-export"
VERSION = 1
# Bounds on an archive that is read. A note sent over the web is at most 4 MiB (the app's
# MAX_BODY); an index or a project's pages.jsonl holds many, so an entry may be larger.
ARCHIVE_ENTRIES_MAX = 200_000
ENTRY_BYTES_MAX = 256 * 1024 * 1024
ARCHIVE_BYTES_MAX = 256 * 1024 * 1024  # the default bound on the archive, and on all it reads
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)  # the earliest a zip can say: entries carry no clock
_NOTE_ID = r"cap_[0-9a-f]{12}"
_SLUG = r"[a-z0-9][a-z0-9-]{0,63}"
_LAYOUT = re.compile(
    r"export\.json"
    r"|projects/(?P<project>[^/]+)/(?:"
    r"(?P<index>notes\.jsonl|pages\.jsonl)"
    rf"|notes/(?P<note>[0-9]{{4,}}-{_NOTE_ID}\.md)"
    rf"|pages/(?P<slug>{_SLUG})\.md)"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_NOTE_LINE = ("id", "title", "topic", "label", "source_ref", "via", "created_at")
_OPTIONAL = ("topic", "label", "source_ref", "via")
_PAGE_LINE = ("slug", "version", "title", "body", "claims", "metrics", "created_at")
_MANIFEST_PROJECT = ("id", "name", "description", "kind", "created_at", "notes", "pages")
# The longest topic key an archive may carry (N1): a key is made from at most FIELD_MAX's 200
# characters, and case folding and decomposition can make a character several.
TOPIC_KEY_MAX = 4 * FIELD_MAX["topic"]
# For a person, an archive is every project: exporting and importing are an owner's calls, since
# a partial archive that looks whole would be worse than a refusal.
NOT_AN_EXPORTER = "only the workspace's owners export it or import into it"
# An archive carries no access rules: every project it brings is private (cairn.store.access).
IMPORTED_PRIVATE = (
    "Every imported project is private: only you and the workspace's owners reach it, since"
    " the archive carries no access rules."
)
IMPORTED_OWNERS_ONLY = (
    "Every imported project is private: only the workspace's owners reach it, since the"
    " archive carries no access rules."
)

# What an import says of the projects' Models choice (M4): how many arrived at "never", or,
# for an archive written before exports carried the choice, that it did not say.
IMPORTED_MODELS = "{kept} of the {projects} projects imported arrived at Models never."
IMPORTED_MODELS_UNSAID = (
    "The archive does not say which projects were kept from models, so each arrived at Models"
    " as a new project of its kind does: Personal notes at never, the others not decided."
)
# What an import says when the archive says which projects are at "never" and not which ones
# their people allowed (Privacy 4: one written before exports carried it).
IMPORTED_ALLOWED_UNSAID = (
    "The archive does not say which projects' people allowed a model, so every project not at"
    " never arrived at Models not decided."
)


class CompactIncomplete(sqlite3.OperationalError):
    """compact() could not finish: another connection to the file was writing, or reading
    from the write-ahead log. Nothing is lost; running it again finishes the job.
    `rewrote`: True when the file was rewritten and only the log could not be emptied, False
    when it was not rewritten (or only the log was tried), None when not said."""

    rewrote: bool | None = None


def _incomplete(message: str, rewrote: bool) -> CompactIncomplete:
    error = CompactIncomplete(message)
    error.rewrote = rewrote
    return error


class NoRoom(CompactIncomplete):
    """compact() did not start: the free space on the database's disk (`free`, bytes) is
    under what a rewrite needs (`needed`: twice the database's file and its write-ahead
    log, a second copy and as much again as a margin). Nothing was changed; the deletion
    stays due."""

    def __init__(self, free: int, needed: int) -> None:
        super().__init__(f"the database's disk has {free} bytes free, a rewrite needs {needed}")
        self.free = free
        self.needed = needed


COMPACT_LOCK = ".compact.lock"  # `<db>.compact.lock`, beside the database's file


class CompactionRunning(RuntimeError):
    """Another compaction of this database is running, in this process or another: this one
    did not start and changed nothing (PortableMethods.compacting). It never waits."""

    def __init__(self) -> None:
        super().__init__("another compaction is running on this database")


class PortableMethods(StoreBase):
    def export(self, out: IO[bytes]) -> ExportSummary:
        """Write the workspace's projects, notes and every page version to `out` (a binary file
        object) as a format-1 zip. Everything is read in one read transaction, so the export
        is one moment of the workspace; the zip is written after the lock is released.
        Exporting writes nothing to the database. A management call (cairn.store.reach): only
        a view that reaches every project with write access may export, and for a person only
        an owner (NOT_AN_EXPORTER): a partial archive that looks whole would be worse than a
        refusal. KeyError: the workspace was deleted. The summary carries no notice."""
        with self._read() as con:
            self._require_exporter(con)
            if self._reach(con) is not None:  # a later limit on this view (cairn.store.reach)
                raise PermissionError("only a view of the whole workspace exports it")
            account = _find_account(con, self._account_id)
            if account is None:
                raise KeyError(self._account_id)
            projects = [
                _project(row)
                for row in con.execute(
                    f"SELECT {_PROJECT_COLUMNS} FROM projects WHERE account_id = ? ORDER BY name",
                    (account.id,),
                )
            ]
            chosen = dict(  # M4, Privacy 4: each project's choice, in the same read
                con.execute(
                    _MODELS_CHOICES,
                    (account.id, json.dumps([p.id for p in projects]), MODELS_PREFIX),
                ).fetchall()
            )
            held = [
                _Held(
                    project,
                    [
                        _stored_note(row)
                        for row in con.execute(
                            f"SELECT {_NOTE_COLUMNS} FROM notes WHERE project_id = ? ORDER BY seq",
                            (project.id,),
                        )
                    ],
                    [
                        _page(row)
                        for row in con.execute(
                            f"SELECT {_PAGE_COLUMNS} FROM page_versions WHERE project_id = ?"
                            " ORDER BY rowid",  # the order they were saved in
                            (project.id,),
                        )
                    ],
                    dict(
                        con.execute(
                            "SELECT slug, topic_key FROM page_keys WHERE project_id = ?",
                            (project.id,),
                        ).fetchall()
                    ),
                    {
                        note_id: (kind, name)
                        for note_id, kind, name in con.execute(
                            "SELECT note_writers.note_id, note_writers.kind,"
                            " note_writers.name FROM note_writers JOIN notes"
                            " ON notes.id = note_writers.note_id"
                            " WHERE notes.project_id = ?",
                            (project.id,),
                        )
                    },
                )
                for project in projects
            ]
        summary = ExportSummary(
            projects=len(held),
            notes=sum(len(h.notes) for h in held),
            pages=sum(len(h.slugs()) for h in held),
        )
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "exported_at": self._stamp(),
            "workspace": account.name,
            "projects": [
                {
                    "id": h.project.id,
                    "name": h.project.name,
                    "description": h.project.description,
                    "kind": h.project.kind,
                    "created_at": h.project.created_at,
                    "notes": len(h.notes),
                    "pages": len(h.slugs()),
                    "models": chosen.get(h.project.id, MODELS[0]),
                    "models_decided": h.project.id in chosen,
                }
                for h in held
            ],
        }
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            _put(archive, "export.json", json.dumps(manifest, indent=2).encode())
            for h in held:
                for name, data in h.entries():
                    _put(archive, name, data)
        return summary

    def import_export(self, source: IO[bytes], max_bytes: int | None = None) -> ExportSummary:
        """Restore a format-1 zip (a seekable binary file object) into this workspace, all or
        nothing: its projects (new ids, their names, descriptions, kinds and creation times),
        every note with its own id, fields and time (so every claim still cites the same
        spans), and every page version in the order it was saved, indexed for search as
        save_page indexes it. Recorded as one workspace.imported event (counts only).
        `max_bytes` bounds the archive file and the bytes read from it (ARCHIVE_BYTES_MAX,
        256 MiB, when None); what is read is held in memory until the write ends.

        A note field longer than FIELD_MAX allows (an export written before the bounds) is cut
        to its bound, never the note's text; the summary's `fields_cut` counts them, and so
        does the event. ValueError, and nothing is written: an archive that breaks a bound or
        the layout (see the module), an unknown format or version, a note whose bytes do not
        match its sha256, a claim citing a span that is not of its project's notes, a note or
        page the note guard refuses under the workspace's policy (or the view's own guard;
        the message names the rules, never what matched), a project name the workspace
        already uses, or a note id the database already holds. A management call: only a view that
        reaches every project with write access may import, and for a person only an owner
        (NOT_AN_EXPORTER). KeyError: the workspace was deleted. Its notes are metered as
        written now, in this month; on a limited view (H6), OverLimit, with nothing
        imported, when its projects, its notes this month or their bytes would pass a cap.

        The archive carries no access rules: every project it brings is private (`everyone`
        "none"), made by the view's person (None for a view acting for no person, so only
        the owners reach it), and the summary's `notice` says so in one sentence. It carries
        each project's Models choice (M4; see the module): a project it says is at "never",
        and every project of a private kind, is put at "never" in this write, and the
        notice's second sentence says how many, or that an archive written before did not
        say it. The workspace.imported event's detail adds `kept_from_models`, that count."""
        entries = _entries(source, ARCHIVE_BYTES_MAX if max_bytes is None else max_bytes)
        plan = _plan(entries)  # a defect is refused as one: nothing of it is written
        # Each note's fields and each page version, as add_note screens a note: the
        # policy in force inside the write decides.
        found, fields_cut = [], 0
        for project in plan.projects:
            if len(project.description) > DESCRIPTION_MAX:  # cut like a note's fields, and counted
                project.description, fields_cut = (
                    project.description[:DESCRIPTION_MAX],
                    fields_cut + 1,
                )
            for place, (note, spans) in enumerate(project.notes):
                fields = {"text": note.text, "title": note.title}
                fields |= {name: getattr(note, name) for name in _OPTIONAL}
                # An export written before FIELD_MAX may hold a longer field: cut to its
                # bound, never the text, which is all a span (and so a claim) cites.
                fields, cut = cut_note_fields(fields)
                if cut:
                    kept = {name: fields[name] for name in FIELD_MAX}
                    project.notes[place] = (note.model_copy(update=kept), spans)
                    fields_cut += cut
                found += self._screen(fields)
            for page in project.pages:
                found += self._screen({"title": page.title, "text": page.body})
        index = self._index
        with self._write() as con:
            _require_account(con, self._account_id)
            self._require_exporter(con)
            if self._reach(con) is not None:
                raise PermissionError("only a view of the whole workspace imports into it")
            _refuse(self._in_force(con, found))
            names = [project.name for project in plan.projects]
            taken = con.execute(
                "SELECT name FROM projects WHERE account_id = ?"
                " AND name IN (SELECT value FROM json_each(?))",
                (self._account_id, json.dumps(names)),
            ).fetchall()
            if taken:
                raise ValueError(f"this workspace already has a project named {taken[0][0]!r}")
            ids = [note.id for project in plan.projects for note, _ in project.notes]
            held = con.execute(
                "SELECT id FROM notes WHERE id IN (SELECT value FROM json_each(?)) LIMIT 1",
                (json.dumps(ids),),
            ).fetchone()
            if held:
                raise ValueError(f"this database already holds the note {held[0]}")
            notes = [note for project in plan.projects for note, _ in project.notes]
            size = sum(len(note.text.encode()) for note in notes)
            at = self._instant()
            # A limited view's caps, for the whole archive at once: OverLimit, nothing imported.
            self._admit_projects(con, len(plan.projects))
            if notes:  # counted in the month of the import, as any other write
                passages = sum(
                    charge_of(spans) for project in plan.projects for _, spans in project.notes
                )
                self._admit_note(con, size, at, len(notes), passages)
                self._meter_note(con, size, at, len(notes))
            versions, kept = [], 0
            for planned in plan.projects:
                project_id = "br_" + secrets.token_hex(6)
                project = Project(
                    id=project_id,
                    name=planned.name,
                    description=planned.description,
                    created_at=planned.created_at,
                    kind=planned.kind,
                    maker_id=self._user_id,  # the importing person; None: owners alone
                )
                values = (*(getattr(project, f) for f in _PROJECT_FIELDS), self._account_id, "none")
                con.execute(
                    f"INSERT INTO projects ({_PROJECT_COLUMNS}, account_id, everyone)"
                    f" VALUES ({', '.join('?' * len(values))})",
                    values,
                )
                # M4: at "never" when the archive says so, and a private kind always
                if KINDS[planned.kind].private or planned.models == MODELS[1]:
                    _keep_from_models(con, self._account_id, project_id, _iso(at))
                    kept += 1
                elif planned.models == MODELS[0] and planned.decided:  # Privacy 4: chosen
                    _choose_models(con, self._account_id, project_id, MODELS[0], _iso(at))
                for note, spans in planned.notes:
                    # A1: the writer its line gives; a line that gives none, the importer's.
                    writer = planned.writers.get(note.id, self.writer)
                    _write_note(
                        con, note.model_copy(update={"project_id": project_id}), spans, writer
                    )
                con.executemany(  # N1: an older archive's notes no page cites wait to be filed
                    "INSERT INTO waiting (note_id, project_id) VALUES (?, ?)",
                    [(note_id, project_id) for note_id in sorted(planned.waiting)],
                )
                con.executemany(  # N1: each page's topic, as the archive carries it
                    "INSERT INTO page_keys (project_id, slug, topic_key) VALUES (?, ?, ?)",
                    [(project_id, slug, key) for slug, key in sorted(planned.keys.items())],
                )
                versions.append([(page, project_id) for page in planned.pages])
            # Every version in the order it was saved, across projects: the search index's
            # rows, and so the order of ties, follow the source's.
            for page, project_id in heapq.merge(*versions, key=lambda item: item[0].updated_at):
                row = (
                    project_id,
                    page.slug,
                    page.version,
                    page.title,
                    page.body,
                    json.dumps([claim.model_dump(mode="json") for claim in page.claims]),
                    json.dumps(page.metrics),
                    page.updated_at,
                )
                _write_page_version(con, self._account_id, index, row, page.claims)
            for project_versions in versions:  # a limited view's versions_per_page, as a save
                for project_id in dict.fromkeys(project_id for _, project_id in project_versions):
                    self._keep_versions(con, project_id, None)
            self._keep_history(con)  # the storage cap's budget
            summary = plan.summary().model_copy(update={"fields_cut": fields_cut})
            detail = {**summary.model_dump(exclude={"notice"}), "kept_from_models": kept}
            self._audit(con, "workspace.imported", detail=detail)
        notice = IMPORTED_OWNERS_ONLY if self._user_id is None else IMPORTED_PRIVATE
        if plan.models_said:
            said = IMPORTED_MODELS.format(kept=kept, projects=len(plan.projects))
            if not plan.decided_said:
                said = f"{said} {IMPORTED_ALLOWED_UNSAID}"
        else:
            said = IMPORTED_MODELS_UNSAID
        return summary.model_copy(update={"notice": f"{notice} {said}"})

    def _require_exporter(self, con: sqlite3.Connection) -> None:
        """PermissionError(NOT_AN_EXPORTER) for a person who does not own the workspace now;
        a view acting for no person passes (cairn.store.reach has refused a narrowed one)."""
        if self._user_id is not None and self._role_in(con) != "owner":
            raise PermissionError(NOT_AN_EXPORTER)

    @contextlib.contextmanager
    def compacting(self) -> Iterator[None]:
        """One compaction at a time for a database, across processes: held from the
        decision of what is due (read the record inside it) through the clearing of the
        record. An exclusive flock(2) on `<db>.compact.lock` (COMPACT_LOCK), an empty file
        beside the database's real path, made 0600 and kept; the kernel lets go of it when
        the descriptor closes, so a process that is killed leaves nothing held. Inside one
        process a lock of the store's says the same. Never waits: CompactionRunning when
        either is held, nothing changed. The thread that holds it may take it again
        (compact and empty_log take it themselves, inside their caller's decision). A
        database in memory is no file: the store's lock alone. The operator's call
        (PermissionError on any view). OSError when the lock file cannot be made or opened
        (O_NOFOLLOW: a link planted there is refused)."""
        if not self._owner:
            raise PermissionError("only the store that opened the database compacts it")
        state = self._state
        if state.compaction_holder == threading.get_ident():
            yield
            return
        if not state.compaction_guard.acquire(blocking=False):
            raise CompactionRunning
        fd = None
        try:
            path = self._compaction_lock_path()
            if path:
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise CompactionRunning from None
            state.compaction_holder = threading.get_ident()
            try:
                yield
            finally:
                state.compaction_holder = None
        finally:
            if fd is not None:
                os.close(fd)  # the flock goes with it
            state.compaction_guard.release()

    def _compaction_lock_path(self) -> str:
        """`<db>.compact.lock` beside the database's real path; "" for one in memory."""
        with self._locked() as con:
            rows = con.execute("PRAGMA database_list").fetchall()
        file = next((row[2] for row in rows if row[1] == "main"), "")
        return os.path.realpath(file) + COMPACT_LOCK if file else ""

    def compact(self) -> None:
        """Rewrite the database file without its free space (VACUUM), then copy the
        write-ahead log into it and truncate the log to nothing (PRAGMA
        wal_checkpoint(TRUNCATE)). It gives the space a deletion freed back to the disk, and
        removes what `delete_account` cannot: short index keys of a deleted workspace (a
        page's slug, a project's name) left in index pages other workspaces still use.

        The operator's call, not a workspace's: only the store that opened the database may
        run it (PermissionError on any view), and a narrowed view is refused as for any
        management call. It always rewrites, whatever the record says (the command line's
        `compact` prunes old versions first and gives their space back); the server's
        compactor calls `empty_log` instead when only the log is due.

        First it checks for room (NoRoom, nothing changed: the disk must have twice the
        database free). What a write of this process waits for: during the rewrite, the
        whole of it (the time to write the file again, which grows with the database; cairn
        sets no bound), then the attempt to empty the log, at most about 5 seconds (SQLite's
        busy wait on the writer, sqlite3's default) for another connection to let go, plus
        the copy of the log into the file. Reads do not wait: they have connections of their
        own (base.Readers). Another process writing to the file waits too.

        One compaction at a time on a database (compacting: CompactionRunning, nothing
        changed, when another runs, in this process or another). The record
        (compaction_state): the rewrite and the emptying of the log are kept apart. Under
        the lock and before VACUUM, the deletion record (store.compact_due) is read as it
        stands, its `updated_at`: a generation each deletion writes of its own
        (base.deletion_generation), what the rewrite covers. Once VACUUM has returned, under
        the same hold of the lock and in one transaction, compacted_at is set to when the
        rewrite began and the record is marked as covered by it (REWRITTEN with a mark
        of its own) only if it still holds what was read: a deletion another process (the
        server, beside the command line) records in between keeps it due, for a rewrite of
        its own. A process that dies after VACUUM and before that transaction leaves the
        record as it was, so the rewrite is done again: it costs the time of one rewrite and
        loses nothing. A rewrite that did not happen, whatever stopped it, records nothing
        (CompactIncomplete, `rewrote` False, when the file was busy). When the rewrite
        succeeded and another connection held the log (a replica service always does),
        CompactIncomplete with `rewrote` True: the deletion stays recorded, as rewritten, so
        what stays due is emptying the log (empty_log), never a second rewrite for it once
        it is recorded. Once the log is emptied the record goes, unless a deletion came
        meanwhile (_empty_log: by its generation)."""
        if not self._owner:
            raise PermissionError("only the store that opened the database compacts it")
        with self.compacting():
            self._compact_held()

    def _compact_held(self) -> None:
        self._room_for_rewrite()
        began = self._stamp()
        with self._holding() as con:
            generation = con.execute(  # the record as the rewrite finds it
                "SELECT updated_at FROM settings WHERE account_id = ? AND name = ?",
                (DEFAULT_ACCOUNT_ID, COMPACT_DUE),
            ).fetchone()
            try:
                con.execute("VACUUM")
            except sqlite3.OperationalError as error:
                raise _incomplete(f"the database could not be compacted: {error}", False) from None
            mark = None if generation is None else _rewrite_mark()
            with _transaction(con):  # the deletions recorded before the rewrite are its own
                _set_row(con, COMPACTED_AT, began)
                if mark is not None:  # unchanged, or a later deletion keeps it due
                    con.execute(
                        "UPDATE settings SET updated_at = ?"
                        " WHERE account_id = ? AND name = ? AND updated_at = ?",
                        (mark, DEFAULT_ACCOUNT_ID, COMPACT_DUE, generation[0]),
                    )
        self._empty_log(rewrote=True, mark=mark)

    def empty_log(self) -> None:
        """What a rewrite left due (compaction_state().rewritten): copy the write-ahead log
        into the file and truncate it, with no rewrite. A write of this process waits for it
        at most about 5 seconds (SQLite's busy wait) plus the copy of the log. The record
        goes once it is done, unless a deletion came meanwhile; CompactIncomplete (`rewrote`
        False) when another connection held the log, and the record stays. The operator's
        call, as compact (PermissionError on any view)."""
        if not self._owner:
            raise PermissionError("only the store that opened the database compacts it")
        with self.compacting():
            self._empty_log(rewrote=False, mark=None)

    @contextlib.contextmanager
    def _holding(self) -> Iterator[sqlite3.Connection]:
        """The writer, under the lock, marked as compacting while it is held (and only
        then: a compaction waiting for a stuck writer does not lengthen its bound,
        Store.health)."""
        with self._locked() as con:  # StoreUnusable, not an incomplete compaction
            self._state.compacting = True
            try:
                yield con
            finally:
                self._state.compacting = False

    def _empty_log(self, rewrote: bool, mark: str | None) -> None:
        """Truncate the write-ahead log; then the record goes if it still holds the rewritten
        generation this emptying covers: `mark`, the one compact just wrote, or for
        empty_log the one read under the lock before the truncation. One statement, which
        changes nothing once a deletion (or another process's rewrite after another
        deletion) has moved the record on: that one stays due.

        The log is then truncated once more, for the clearing's own change. When another
        connection holds it by then (busy), one line on the "cairn.store" logger says so;
        when that truncation fails (an OperationalError), one warning there with SQLite's
        error name, never its text. In both nothing is raised, what was done stays done, and
        the record stays as it is: cleared, or moved on. The log then
        holds only changes made after its truncation: the clearing, and anything written
        since, where a deletion recorded itself again and a rewrite marked itself, so what
        either left is still due."""
        with self._holding() as con:
            if mark is None:
                found = con.execute(
                    "SELECT updated_at FROM settings WHERE account_id = ? AND name = ?",
                    (DEFAULT_ACCOUNT_ID, COMPACT_DUE),
                ).fetchone()
                mark = found[0] if found and is_rewritten(found[0]) else None
            try:
                busy = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
            except sqlite3.OperationalError as error:
                raise _incomplete(
                    f"the database could not be compacted: {error}", rewrote
                ) from None
        if busy:
            raise _incomplete(
                "the write-ahead log could not be truncated: another connection is using"
                " the database; run compact again when it is done",
                rewrote,
            )
        if mark is not None:
            with self._write() as con:
                con.execute(
                    "DELETE FROM settings WHERE account_id = ? AND name = ? AND updated_at = ?",
                    (DEFAULT_ACCOUNT_ID, COMPACT_DUE, mark),
                )
        with self._locked() as con:
            try:
                busy = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
            except sqlite3.OperationalError as error:  # its code, never its text
                store_log.warning(
                    "compact: the write-ahead log keeps the changes made after it was emptied:"
                    " its last truncation failed (%s)",
                    getattr(error, "sqlite_errorname", None) or "no code",
                )
                return
        if busy:
            store_log.info(
                "compact: the write-ahead log keeps the changes made after it was emptied:"
                " another connection held it"
            )

    def _room_for_rewrite(self) -> None:
        """NoRoom unless the database's disk has twice the database free (its file and its
        write-ahead log): a rewrite writes a second copy, and the margin is as much again,
        the rule `stats` calls low disk. A database in memory has no disk to check."""
        with self._locked() as con:
            rows = con.execute("PRAGMA database_list").fetchall()
        file = next((row[2] for row in rows if row[1] == "main"), "")
        if not file:
            return
        wal = f"{file}-wal"
        size = os.path.getsize(file) + (os.path.getsize(wal) if os.path.exists(wal) else 0)
        free = shutil.disk_usage(os.path.dirname(file)).free  # through the module: tests swap it
        if free < 2 * size:
            raise NoRoom(free, 2 * size)

    def compaction_state(self) -> CompactionState:
        """Whether a workspace was deleted since the last compaction, whether a rewrite has
        covered every deletion recorded (only emptying the log is left), and when the last
        rewrite began: what `serve --compact-every` decides by. delete_account,
        delete_project and forget_note record a deletion in their own transaction: its time
        when none is recorded, and in every case the record's `updated_at` set to theirs, so
        a deletion after a rewrite is no longer covered by it. The record is two rows of the
        default workspace's settings, which no setting call reads or changes. A read."""
        with self._read() as con:
            found = {
                name: (value, updated)
                for name, value, updated in con.execute(
                    "SELECT name, value, updated_at FROM settings"
                    " WHERE account_id = ? AND name IN (?, ?)",
                    (DEFAULT_ACCOUNT_ID, COMPACT_DUE, COMPACTED_AT),
                )
            }
        due, done = found.get(COMPACT_DUE), found.get(COMPACTED_AT)
        return CompactionState(
            deleted_at=due[0] if due else None,
            compacted_at=done[0] if done else None,
            rewritten=bool(due) and is_rewritten(due[1]),
        )


def _rewrite_mark() -> str:
    """The record's `updated_at` once a rewrite covered the generation it read
    (base.REWRITTEN): a mark of this rewrite's own, new even when the record was already
    rewritten (the command line rewrites then too), so emptying the log after one rewrite
    never clears what a later rewrite left due."""
    return f"{REWRITTEN}:{secrets.token_hex(16)}"


def _refuse(findings: list[tuple[str, Any]]) -> None:
    """ValueError naming the guard's rules, never what they matched, when there are any."""
    if findings:
        rules = ", ".join(dict.fromkeys(finding.rule for _, finding in findings))
        raise ValueError(f"the note guard refuses what this export holds: {rules}")


def _set_row(con: sqlite3.Connection, name: str, value: str | None) -> None:
    if value is None:
        con.execute(
            "DELETE FROM settings WHERE account_id = ? AND name = ?", (DEFAULT_ACCOUNT_ID, name)
        )
        return
    con.execute(
        "INSERT INTO settings (account_id, name, value, updated_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (account_id, name)"
        " DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (DEFAULT_ACCOUNT_ID, name, value, value),
    )


# Export


@dataclass
class _Held:
    """One project's data as one read transaction found it."""

    project: Project
    notes: list[Note]
    pages: list[Page]  # every version, in the order saved
    keys: dict[str, str]  # each page's topic key, by slug (N1); a page with none is absent
    writers: dict[str, tuple[str, str | None]]  # each note's writer, by id (A1); none: absent

    def slugs(self) -> list[str]:
        return list(dict.fromkeys(page.slug for page in self.pages))

    def entries(self) -> Iterator[tuple[str, bytes]]:
        root = f"projects/{self.project.name}/"
        lines, notes = [], []
        for n, note in enumerate(self.notes, 1):
            data = note.text.encode()
            file = f"notes/{n:04d}-{note.id}.md"
            line = {name: getattr(note, name) for name in _NOTE_LINE}
            line |= {"file": file, "sha256": hashlib.sha256(data).hexdigest()}
            writer = self.writers.get(note.id)
            if writer is not None and writer[0] == "source":  # an export names no source
                writer = ("source", None)
            lines.append(
                {
                    **line,
                    "topic_key": note.topic_key,
                    "before_topics": note.before_topics,
                    "writer": None if writer is None else {"kind": writer[0], "name": writer[1]},
                }
            )
            notes.append((root + file, data))
        yield root + "notes.jsonl", _jsonl(lines)
        yield from notes
        body_of = self._bodies()
        yield (
            root + "pages.jsonl",
            _jsonl(
                {
                    "slug": page.slug,
                    "version": page.version,
                    "title": page.title,
                    "body": body_of(page),
                    "claims": [claim.shown() for claim in page.claims],
                    "metrics": shown_metrics(page.metrics),
                    "created_at": page.updated_at,
                    "topic_key": self.keys.get(page.slug),
                }
                for page in self.pages
            ),
        )
        latest = {page.slug: page for page in self.pages}
        for slug in sorted(latest):
            yield f"{root}pages/{slug}.md", body_of(latest[slug]).encode()

    def _bodies(self) -> Callable[[Page], str]:
        """Each page version's body as the export writes it: as stored, except that a page
        whose title holds a line break is rendered as it is read (Memory.page), so its title
        is one line of heading text (cairn.topics.one_line): a version stored before that rule
        wrote the title as given, and a line break in it would add lines to the file. The
        project's notes are split once, and only for such a page. The import takes either."""
        spans: dict[str, Span] = {}

        def body_of(page: Page) -> str:
            if one_line(page.title) == page.title:
                return page.body
            from cairn.compiler import render_body  # the compiler builds on the store

            if not spans:
                spans.update({s.id: s for note in self.notes for s in chunk(note.id, note.text)})
            return render_body(page.title, page.claims, spans)

        return body_of


def _jsonl(lines: Any) -> bytes:
    # ensure_ascii: no line holds a raw line separator, so a reader may split on any of them
    return "".join(json.dumps(line, ensure_ascii=True) + "\n" for line in lines).encode()


def _put(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, data)


# Import


@dataclass
class _PlannedProject:
    name: str
    description: str
    kind: str
    created_at: str
    notes: list[tuple[Note, list[Span]]] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)  # every version, in the order saved
    keys: dict[str, str] = field(default_factory=dict)  # each page's topic key, by slug (N1)
    waiting: set[str] = field(default_factory=set)  # notes left to file (an older archive's)
    # Each note's writer as its line gives it (A1): (kind, name), or None for none recorded;
    # a note whose line has no `writer` is absent, and its importer writes it.
    writers: dict[str, tuple[str, str | None] | None] = field(default_factory=dict)
    # Whether the archive says the project is at "never" (M4): "allowed" or "never", or None
    # for an archive written before exports carried the choice.
    models: str | None = None
    # Whether its people chose (Privacy 4): True or False, or None for an archive that does
    # not say (one written before).
    decided: bool | None = None


@dataclass
class _Plan:
    projects: list[_PlannedProject]
    models_said: bool = True  # whether the archive says each project's Models choice (M4)
    decided_said: bool = True  # whether it says which projects' people chose (Privacy 4)

    def summary(self) -> ExportSummary:
        return ExportSummary(
            projects=len(self.projects),
            notes=sum(len(b.notes) for b in self.projects),
            pages=sum(len({p.slug for p in b.pages}) for b in self.projects),
        )


# A page version's metrics in an archive (H6c): a flat map of at most METRICS_MAX names,
# each 1 to METRIC_NAME_MAX characters, to a whole or finite number or a text of at most
# METRIC_TEXT_MAX characters, as the gates and cairn.arrange write them.
METRICS_MAX = 32
METRIC_NAME_MAX = 40
METRIC_TEXT_MAX = 200
# The claims of one page version in an archive hold at most CLAIM_TEXT_FACTOR times the text
# of the distinct spans the version cites (UTF-8 bytes, each as the archive holds it). A claim
# cairn writes is its span's text, so its versions sit at 1 or below (a folded claim cites
# several spans); 2 leaves room for whitespace while one span cannot be repeated or padded
# into a page that takes more disk than its notes allow. With a span cited from one page
# only (_cited_once), a note's text is stored on pages at most this many times per version.
CLAIM_TEXT_FACTOR = 2
# A claim's id in an archive is shaped as cairn makes one: "cl_" and 16 lowercase hex digits
# (cairn.spans.claim_id, cairn.llm_compiler, the erasure's new ids).
CLAIM_ID = re.compile(r"cl_[0-9a-f]{16}")
# The citations of one page version in an archive (its claims' span lists, added up) are at
# most CITATION_FACTOR times its claims and the distinct spans it cites, added up. cairn's
# versions cite each span from one claim (a claim per passage, a fold citing its lines), so
# their citations are at most the spans; claims that each cite every span are refused.
CITATION_FACTOR = 2


def _metrics(metrics: dict[Any, Any], slug: str, root: str) -> None:
    """ValueError unless `metrics` is the short flat map METRICS_MAX describes; the message
    names the page, never a metric."""
    bad = len(metrics) > METRICS_MAX or not all(
        isinstance(name, str)
        and 1 <= len(name) <= METRIC_NAME_MAX
        and (
            (isinstance(value, int) and not isinstance(value, bool))
            or (isinstance(value, float) and math.isfinite(value))
            or (isinstance(value, str) and len(value) <= METRIC_TEXT_MAX)
        )
        for name, value in metrics.items()
    )
    if bad:
        raise ValueError(
            f"the metrics of {slug!r} in {root} are not a flat map of at most {METRICS_MAX}"
            " short names to numbers or short words"
        )


def _plan(entries: dict[str, bytes]) -> _Plan:
    """What an archive's entries (read within the bounds, names of the layout) hold, checked
    against the manifest, each other and their checksums; ValueError for anything else.
    Pure: no database."""
    if "export.json" not in entries:
        raise ValueError("not a cairn export: it has no export.json")
    manifest = _json(entries, "export.json")
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("not a cairn export: export.json does not name the format")
    if manifest.get("version") != VERSION:
        raise ValueError(f"this cairn reads export version {VERSION}, not another version")
    listed = manifest.get("projects")
    if not isinstance(listed, list) or not all(isinstance(b, dict) for b in listed):
        raise ValueError("export.json lists no projects")
    used: set[str] = {"export.json"}
    plan, seen_names, seen_ids = _Plan([]), set(), set()
    for entry in listed:
        project = _manifest_project(entry)
        if project.name.casefold() in seen_names:
            raise ValueError(f"export.json lists the project {project.name!r} twice")
        seen_names.add(project.name.casefold())
        root = f"projects/{project.name}/"
        chunks: dict[str, Span] = {}  # each span, by id: what claims cite, and bodies render
        for n, line in enumerate(_lines(entries, root + "notes.jsonl"), 1):
            note, chunked = _note(entries, root, n, line)
            if "writer" in line:  # A1: an archive written before writers has none
                project.writers[note.id] = _writer(line["writer"], n, root)
            try:  # an archive is refused for it, never cut: a page's headings come from them
                check_headings(chunked)
            except HeadingsTooLong as headings:  # its message has numbers, never a heading
                raise ValueError(
                    f"note {n} of {root}notes.jsonl has headings past their bound: {headings}"
                ) from None
            try:  # G1: refused the same way, and its count and bound said, never a fact
                check_facts(chunked)
            except TooManyFacts as many:
                raise ValueError(
                    f"note {n} of {root}notes.jsonl has facts past their bound: {many}"
                ) from None
            if note.id in seen_ids:
                raise ValueError(f"the note {note.id} is in this export twice")
            seen_ids.add(note.id)
            used.add(root + f"notes/{n:04d}-{note.id}.md")
            chunks.update((span.id, span) for span in chunked)
            project.notes.append((note, chunked))
        versions: dict[str, int] = {}  # each page's latest version so far
        given: dict[str, str] = {}  # each page's latest body as the archive holds it
        cited: dict[str, str] = {}  # each span cited so far, and the page that cites it
        keys: dict[str, str | None] = {}  # each page's topic key, as its lines carry it (N1)
        for line in _lines(entries, root + "pages.jsonl"):
            page = _page_version(root, line, versions, chunks)
            _cited_once(page, cited, root)
            _claim_text(page, chunks, root)
            _page_key(line, page, keys, root)
            versions[page.slug], given[page.slug] = page.version, page.body
            project.pages.append(_as_rendered(page, chunks, root))
        latest = {page.slug: page for page in project.pages}
        _file_by_topic(project, latest, keys, cited, root)
        for slug in latest:
            name = f"{root}pages/{slug}.md"
            if entries.get(name) != given[slug].encode():
                raise ValueError(f"{name} is missing or is not the page's latest version")
            used.add(name)
        used.update({root + "notes.jsonl", root + "pages.jsonl"})
        if (entry["notes"], entry["pages"]) != (len(project.notes), len(latest)):
            raise ValueError(
                f"export.json says the project {project.name!r} has {entry['notes']} notes and"
                f" {entry['pages']} pages; its files hold {len(project.notes)} and {len(latest)}"
            )
        plan.projects.append(project)
    if stray := sorted(set(entries) - used):
        raise ValueError(f"{stray[0]} is a file no line of the export names")
    said = {project.models is not None for project in plan.projects}
    if len(said) > 1:  # an archive cairn writes says it of every project, or of none
        raise ValueError("export.json says the models choice of some projects and not of others")
    plan.models_said = said != {False}
    decided = {project.decided is not None for project in plan.projects}
    if len(decided) > 1:
        raise ValueError("export.json says models_decided of some projects and not of others")
    plan.decided_said = decided != {False}
    return plan


def _entries(source: IO[bytes], max_bytes: int) -> dict[str, bytes]:
    """Every entry by name, read within the bounds, never to disk: `max_bytes` for the archive
    file and for all its entries read."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("an import reads at least one byte: max_bytes must be 1 or more")
    try:
        size = source.seek(0, io.SEEK_END)
        source.seek(0)
    except (OSError, ValueError, AttributeError):
        raise ValueError("an export is read from a file that can seek") from None
    if size > max_bytes:
        raise ValueError(f"the archive is larger than {max_bytes} bytes")
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, ValueError):
        raise ValueError("not a cairn export: it is not a zip archive") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > ARCHIVE_ENTRIES_MAX:
            raise ValueError(f"the archive has more than {ARCHIVE_ENTRIES_MAX} entries")
        names = [info.filename for info in infos]
        for name in names:
            match = _LAYOUT.fullmatch(name)
            if match is None or (
                match["project"] is not None and not is_project_name(match["project"])
            ):
                raise ValueError(f"{name!r} is not a name of the export layout")
        seen: set[str] = set()
        for name in names:
            if name in seen:
                raise ValueError(f"the archive holds {name} twice")
            seen.add(name)
        entries, total = {}, 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise ValueError(f"{info.filename} is encrypted")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError(f"{info.filename} uses a compression this cairn does not read")
            if info.file_size > ENTRY_BYTES_MAX:
                raise ValueError(f"{info.filename} is larger than {ENTRY_BYTES_MAX} bytes")
            try:
                with archive.open(info) as entry:
                    data = entry.read(ENTRY_BYTES_MAX + 1)
            except (zipfile.BadZipFile, OSError, EOFError, RuntimeError, zlib.error):
                raise ValueError(f"not a cairn export: {info.filename} cannot be read") from None
            if len(data) > ENTRY_BYTES_MAX:
                raise ValueError(f"{info.filename} is larger than {ENTRY_BYTES_MAX} bytes")
            total += len(data)
            if total > max_bytes:
                raise ValueError(f"the archive holds more than {max_bytes} bytes in all")
            entries[info.filename] = data
    return entries


def _json(entries: dict[str, bytes], name: str) -> Any:
    try:
        return json.loads(entries[name].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValueError(f"{name} is not valid JSON in UTF-8") from None


def _lines(entries: dict[str, bytes], name: str) -> list[dict[str, Any]]:
    if name not in entries:
        raise ValueError(f"{name} is missing")
    try:
        text = entries[name].decode("utf-8")
        lines = [json.loads(line) for line in text.split("\n") if line.strip()]
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValueError(f"{name} is not valid JSON lines in UTF-8") from None
    if not all(isinstance(line, dict) for line in lines):
        raise ValueError(f"{name} holds a line that is not a JSON object")
    return lines


def _text(value: Any, what: str, *, optional: bool = False) -> Any:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{what} is not text")
    return value


TIME_CHARS_MAX = 40  # cairn writes 32 ("2026-11-10T00:00:00.000000+00:00"); none needs more


def _time(value: Any, what: str) -> str:
    """The time as the archive wrote it. A valid ISO 8601 time can be any length (a fraction
    of a hundred thousand digits parses), and it is stored as written: so its length is
    bounded here."""
    _text(value, what)
    try:
        if len(value) > TIME_CHARS_MAX:
            raise ValueError
        datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{what} is not an ISO 8601 time") from None
    return value


def _count(value: Any, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{what} is not a count")
    return value


def _manifest_project(entry: dict[str, Any]) -> _PlannedProject:
    if missing := [key for key in _MANIFEST_PROJECT if key not in entry]:
        raise ValueError(f"export.json lists a project without {missing[0]!r}")
    name = _text(entry["name"], "a project's name")
    if not is_project_name(name):
        raise ValueError(f"{name!r} is not a valid project name")
    if entry["kind"] not in KINDS:
        raise ValueError(f"the project {name!r} has a kind this cairn does not know")
    _count(entry["notes"], f"the number of notes of {name!r}")
    _count(entry["pages"], f"the number of pages of {name!r}")
    models = entry.get("models")
    if "models" in entry and (not isinstance(models, str) or models not in MODELS):
        raise ValueError(
            f"the project {name!r} has a models choice other than {' or '.join(MODELS)}"
        )
    decided = entry.get("models_decided")
    if "models_decided" in entry and not isinstance(decided, bool):
        raise ValueError(f"the project {name!r} has a models_decided other than true or false")
    return _PlannedProject(
        name=name,
        description=_text(entry["description"], f"the description of {name!r}"),
        kind=entry["kind"],
        created_at=_time(entry["created_at"], f"the creation time of {name!r}"),
        models=models,
        decided=decided,
    )


def _note(
    entries: dict[str, bytes], root: str, n: int, line: dict[str, Any]
) -> tuple[Note, list[Span]]:
    """The note on line `n` of a project's notes.jsonl, checked against its file."""
    where = f"line {n} of {root}notes.jsonl"
    if missing := [key for key in (*_NOTE_LINE, "file", "sha256") if key not in line]:
        raise ValueError(f"{where} has no {missing[0]!r}")
    note_id = _text(line["id"], f"the id on {where}")
    if not re.fullmatch(_NOTE_ID, note_id):
        raise ValueError(f"the id on {where} is not a note id")
    file = f"notes/{n:04d}-{note_id}.md"
    if line["file"] != file:
        raise ValueError(f"the file on {where} is not {file}")
    if root + file not in entries:
        raise ValueError(f"{root}{file} is missing")
    data = entries[root + file]
    if not isinstance(line["sha256"], str) or not _SHA256.fullmatch(line["sha256"]):
        raise ValueError(f"the sha256 on {where} is not a SHA-256 digest")
    if hashlib.sha256(data).hexdigest() != line["sha256"]:
        raise ValueError(f"{root}{file} does not match its sha256")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{root}{file} is not UTF-8") from None
    note = Note(
        id=note_id,
        project_id="",  # the project's new id, given when it is written
        text=text,
        title=_text(line["title"], f"the title on {where}"),
        created_at=_time(line["created_at"], f"the time on {where}"),
        topic_key=(
            None
            if line.get("topic_key") is None
            else _topic_key(line["topic_key"], f"the topic key on {where}")
        ),
        before_topics=_before_topics(line, where),
        **{name: _text(line[name], f"the {name} on {where}", optional=True) for name in _OPTIONAL},
    )
    return note, chunk(note_id, text)


def _writer(value: Any, n: int, root: str) -> tuple[str, str | None] | None:
    """A note's writer as line `n` gives it (A1): None for null (none recorded), else
    (kind, name) from {"kind", "name"}, a kind of WRITER_KINDS and a name of text (at most
    ACTOR_NAME_MAX characters) or null; a person's name as audit.person_name cuts it."""
    where = f"the writer on line {n} of {root}notes.jsonl"
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"kind", "name"}:
        raise ValueError(f"{where} is not a writer")
    kind, name = value["kind"], value["name"]
    if kind not in WRITER_KINDS:
        raise ValueError(f"{where} is not a kind of writer")
    if name is not None and (not isinstance(name, str) or len(name) > ACTOR_NAME_MAX):
        raise ValueError(f"{where} has a name that is not a writer's")
    return kind, person_name(name) if kind == "person" and name is not None else name


def _before_topics(line: dict[str, Any], where: str) -> bool:
    """Whether a note was stored before N1 (see the module): as its line says, else true
    for a line written before N1 (it carries no topic key either)."""
    value = line.get("before_topics", "topic_key" not in line)
    if not isinstance(value, bool):
        raise ValueError(f"before_topics on {where} is not true or false")
    return value


def _topic_key(value: Any, what: str) -> str:
    """A topic key as an archive carries it: text of at most TOPIC_KEY_MAX characters."""
    if not isinstance(value, str) or len(value) > TOPIC_KEY_MAX:
        raise ValueError(f"{what} is not a topic key")
    return value


def _page_key(line: dict[str, Any], page: Page, keys: dict[str, str | None], root: str) -> None:
    """The topic key a page version's line carries (N1), into `keys` by slug: the same on
    every version of a page (ValueError otherwise). A line without one leaves `keys` as it
    is, so an archive written before keys has none."""
    if "topic_key" not in line:
        return
    value = line["topic_key"]
    key = None if value is None else _topic_key(value, f"the topic key of {page.slug!r} in {root}")
    if keys.setdefault(page.slug, key) != key:
        raise ValueError(f"the versions of {page.slug!r} in {root} carry two topic keys")


def _file_by_topic(
    project: _PlannedProject,
    latest: dict[str, Page],
    keys: dict[str, str | None],
    cited: dict[str, str],
    root: str,
) -> None:
    """Each page's topic key and each note's (N1; see the module), checked and kept on
    `project`: ValueError for two pages of one key. A note keeps the key it carries, also one
    that is not its page's (a workspace whose pages were saved by hand exports such notes,
    and its own export must import). Without keys (an archive written before), each page
    takes the key of its latest title, the first by slug when two have one, and each note
    cited by a page takes that page's, any other the key of its topic, else its title; and
    each note no page's latest version cites waits to be filed (`project.waiting`)."""
    if keys:
        missing = sorted(set(latest) - set(keys))
        if missing:
            raise ValueError(f"the page {missing[0]!r} in {root} carries no topic key")
        project.keys = {slug: key for slug, key in keys.items() if key is not None}
        if len(set(project.keys.values())) != len(project.keys):
            raise ValueError(f"two pages in {root} carry one topic key")
    else:
        on_pages = {i for page in latest.values() for c in page.claims for i in c.span_ids}
        project.waiting = {
            note.id
            for note, spans in project.notes
            if not any(span.id in on_pages for span in spans)
        }
        for slug in sorted(latest):
            project.keys.setdefault(slug, topic_key(latest[slug].title))
        project.keys = {
            slug: key
            for key, slug in {key: slug for slug, key in reversed(project.keys.items())}.items()
        }
    for place, (note, spans) in enumerate(project.notes):
        on = next((cited[span.id] for span in spans if span.id in cited), None)
        page_key = project.keys.get(on) if on is not None else None
        key = note.topic_key
        if key is None:
            key = page_key if page_key is not None else topic_key(note.topic or note.title)
        project.notes[place] = (note.model_copy(update={"topic_key": key}), spans)


def _cited_once(page: Page, cited: dict[str, str], root: str) -> None:
    """ValueError when a claim of `page` cites a span another page of the project cites
    (`cited`, each span cited so far to its page, which this version's spans join): cairn
    compiles a note into one page, so its passages are cited from that page only. The
    message names both pages, never a span or a claim."""
    for claim in page.claims:
        for span_id in claim.span_ids:
            other = cited.setdefault(span_id, page.slug)
            if other != page.slug:
                raise ValueError(
                    f"the page {page.slug!r} in {root} cites a passage the page {other!r}"
                    " cites: a passage is cited from one page only"
                )


class Refusal(Enum):
    """Each reason an import refuses the claims of a version for (version_faults): whom the
    rule is said of (one claim, by its place or not; the version; its claims together) and
    the rule, which holds counts and never a claim's text, its id or a heading. An import
    says it with the page, the version and the archive's project (`said`); a gate, of the
    page it judges (cairn.gate.import_violations)."""

    NO_CLAIMS = ("version", "has no claims")  # cairn removes a page that is left with none
    ID_SHAPE = ("claim", "has an id cairn does not make")
    CITED_TWICE = ("claim", "cites a passage twice")
    NOT_OF_PROJECT = ("a claim", "cites a passage that is not of its project's notes")
    NOT_WHOLE = ("claim", "does not say what a passage it cites says")
    FENCE = ("claim", "leaves a code fence open that no closing line closes")
    HEADINGS = ("claim", "folds passages under different headings")
    ID_TWICE = ("version", "holds two claims with one id")
    CITATIONS = (
        "version",
        "cites its passages {citations} times, more than {factor} times its {claims} claims"
        " and {cited} passages",
    )
    TEXT = ("claims", "hold more than twice the text of the passages they cite")

    def rule(self, claims: Sequence[Claim]) -> str:
        """The rule in words, with the counts of `claims` (the version's) where it has any."""
        return self.value[1].format(
            citations=sum(len(claim.span_ids) for claim in claims),
            factor=CITATION_FACTOR,
            claims=len(claims),
            cited=len({span_id for claim in claims for span_id in claim.span_ids}),
        )

    def said(self, slug: str, version: int, root: str, n: int, claims: Sequence[Claim]) -> str:
        """An import's refusal of version `version` of the page `slug` in `root` for claim `n`."""
        of = {
            "claim": f"claim {n} of {slug!r} version {version} in {root}",
            "a claim": f"a claim of {slug!r} in {root}",
            "version": f"version {version} of {slug!r} in {root}",
            "claims": f"the claims of {slug!r} version {version} in {root}",
        }
        return f"{of[self.value[0]]} {self.rule(claims)}"


def version_faults(
    claims: Sequence[Claim],
    spans: Mapping[str, Span],
    *,
    legacy: bool,
    reworded: bool = False,
    text_bound: bool = True,
) -> Iterator[tuple[int, Refusal]]:
    """Why an import refuses `claims` as one version of a page, citing `spans` (the spans of
    its project's notes, by id): each fault as (the place of the claim that fails, 1 for the
    first, 0 for a version with no claim; the Refusal), and nothing for a version an import
    takes. The one check of a
    version's claims, which every writer of a version asks. An import refuses the version
    with the first fault (Refusal.said). A forget, which is never refused, writes the
    version without the claims named here (store.erasure). Every gate refuses a candidate it
    names a claim of (cairn.gate.import_violations), so what a compile, a tidy or an
    arrangement saves, an import takes, and a rule added here reaches them all.

    `legacy`: whether a claim that is the whole of its spans by the comparison before CORE5
    (store.before_core5) passes, as a fold a tidy saved then. True for a version that
    exists, an archive's or the store's (an import, a forget); False for a gate, which
    judges a version to be made and takes today's comparison alone.

    `reworded`: True leaves out the one rule that a claim is the whole of every span it
    cites, and asks every other of every claim. For the gates whose contract takes a claim
    in other words than its passage (cairn.gate.importable); never an import or a forget.

    An import asks in two steps, in the order it always did: everything but the bound on
    text when it reads the version's line (_page_version, `text_bound` False), and that
    bound after its rule across pages (_claim_text, of the same code: _past_bounds).

    A claim fails alone when its id is not shaped as cairn makes one, it cites a span twice
    or one that is not in `spans`, it is not the whole of every span it cites, it leaves a
    code fence open that no line closes, or it folds spans under different heading paths.
    Of the claims that pass, one whose id an earlier one holds fails. The rest are a version
    past a bound when their citations are more than CITATION_FACTOR times the claims and the
    distinct spans, or their text more than CLAIM_TEXT_FACTOR times those spans' text (UTF-8
    bytes, a claim's white space counted). Then, from the first claim on, a claim fails when
    it takes the claims before it that do not fail past either bound: of a passage copied
    too often the first copies stay and the later ones fail, and a claim of a passage of its
    own stays. So the claims no fault names pass together."""
    from cairn.gate import fence_left_open  # the gate builds on the store
    from cairn.gate import whole as copied
    from cairn.store.before_core5 import whole_before_core5

    if not claims:
        yield 0, Refusal.NO_CLAIMS
    alone: list[tuple[int, Claim]] = []
    read: dict[str, str | None] = {}  # each span's compared text, read once for the version
    for n, claim in enumerate(claims, 1):
        if not CLAIM_ID.fullmatch(claim.id):
            yield n, Refusal.ID_SHAPE
        elif len(set(claim.span_ids)) != len(claim.span_ids):
            yield n, Refusal.CITED_TWICE
        elif not set(claim.span_ids) <= spans.keys():
            yield n, Refusal.NOT_OF_PROJECT
        elif not (
            reworded or copied(claim, spans, read) or (legacy and whole_before_core5(claim, spans))
        ):
            yield n, Refusal.NOT_WHOLE
        elif fence_left_open([claim]):
            yield n, Refusal.FENCE
        elif len({spans[span_id].heading_path for span_id in claim.span_ids}) > 1:
            yield n, Refusal.HEADINGS
        else:
            alone.append((n, claim))
    first: dict[str, int] = {}  # the place of each id's first claim: `evidence` finds that one
    for n, claim in alone:
        if first.setdefault(claim.id, n) != n:
            yield n, Refusal.ID_TWICE
    rest = [(n, claim) for n, claim in alone if first[claim.id] == n]
    yield from _past_bounds(rest, spans, text_bound)


def _past_bounds(
    placed: Sequence[tuple[int, Claim]], spans: Mapping[str, Span], text_bound: bool
) -> Iterator[tuple[int, Refusal]]:
    """The faults of version_faults for claims that as a whole are past a bound of a version
    (`placed`: each claim with its place; each cites spans of `spans` only): nothing when
    they are within the bound on citations and, with `text_bound`, the bound on text; else
    one fault for each claim that takes the claims before it that do not fail past either
    bound, each with the Refusal the whole version is refused for."""
    citations = sum(len(claim.span_ids) for _, claim in placed)
    cited = {span_id for _, claim in placed for span_id in claim.span_ids}
    text = sum(len(claim.text.encode()) for _, claim in placed)
    if citations > CITATION_FACTOR * (len(placed) + len(cited)):
        fault = Refusal.CITATIONS
    elif text_bound and text > CLAIM_TEXT_FACTOR * sum(
        len(spans[span_id].text.encode()) for span_id in cited
    ):
        fault = Refusal.TEXT
    else:
        return
    kept = citations = text = passages = 0  # of the claims so far that do not fail
    cited = set()
    for n, claim in placed:
        new = set(claim.span_ids) - cited
        more = (
            citations + len(claim.span_ids),
            text + len(claim.text.encode()),
            passages + sum(len(spans[span_id].text.encode()) for span_id in new),
        )
        if (
            more[0] > CITATION_FACTOR * (kept + 1 + len(cited) + len(new))
            or more[1] > CLAIM_TEXT_FACTOR * more[2]
        ):
            yield n, fault
        else:
            kept, (citations, text, passages) = kept + 1, more
            cited.update(new)


def _claim_text(page: Page, chunks: Mapping[str, Span], root: str) -> None:
    """ValueError when the claims of `page` hold more than CLAIM_TEXT_FACTOR times the text
    of the distinct spans they cite, in UTF-8 bytes as the archive holds each (a claim's
    whitespace counts): the bound on text of version_faults, asked once the import knows
    each span is cited from one page. The message names the page and version, never the
    text."""
    for n, fault in _past_bounds(list(enumerate(page.claims, 1)), chunks, True):
        raise ValueError(fault.said(page.slug, page.version, root, n, page.claims))


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def _as_rendered(page: Page, spans: Mapping[str, Span], root: str) -> Page:
    """The version with the body cairn writes of its claims and the spans they cite
    (cairn.compiler.render_body). The archive's must equal it, or a rendering cairn wrote
    before, whitespace aside: before fix H (previous_render_body), before review 7.2, with
    a claim's open code fence left open (render_body_with_fences_open), or before the second
    review (render_body_before_review_two: a title as given, deep headings at level 6, a
    fence closed at any indentation), or before the third (render_body_before_review_three:
    a fence closed where cairn's line rules, not the Markdown parser, left it open), or
    before the third report (render_body_before_review_four: each claim closed as it is
    alone, the body never read whole).
    ValueError otherwise,
    naming the page and version, never the text: an import never brings a body cairn would
    not have written, and the version is stored with the current rendering. A title longer
    than cairn writes (a note's topic, FIELD_MAX) is cut to it, and the body is rendered
    with the cut title."""
    from cairn.compiler import (  # the compiler builds on the store
        previous_render_body,
        render_body,
        render_body_before_review_four,
        render_body_before_review_three,
        render_body_before_review_two,
        render_body_with_fences_open,
    )

    body = render_body(page.title, page.claims, spans)
    # An archive written before holds an earlier rendering: accepted, stored as now.
    given = _collapsed(page.body)
    if given != _collapsed(body) and all(
        given != _collapsed(earlier(page.title, page.claims, spans))
        for earlier in (
            previous_render_body,
            render_body_with_fences_open,
            render_body_before_review_two,
            render_body_before_review_three,
            render_body_before_review_four,
        )
    ):
        raise ValueError(
            f"the body of {page.slug!r} version {page.version} in {root} is not what cairn"
            " writes of its claims"
        )
    title = page.title[: FIELD_MAX["topic"]]  # a page's title is its note's topic or title
    if title != page.title:
        body = render_body(title, page.claims, spans)
    return page.model_copy(update={"title": title, "body": body})


def _page_version(
    root: str, line: dict[str, Any], versions: dict[str, int], spans: Mapping[str, Span]
) -> Page:
    """One line of a project's pages.jsonl: the next version of its page (`versions` holds
    each page's latest so far), citing only `spans`, the spans of the project's own notes by
    id. A page cairn could have written (review blocker 2): each claim is the whole of every
    span it cites, by the verbatim gate's own comparison (cairn.gate.whole: the same text but
    for white space at the ends of lines and blank lines around it), so no citation is false
    and no code is re-indented; a claim that cites
    several spans (a fold) cites spans of one heading path, the one its section shows; and no
    two claims of the version share an id, which `evidence` finds a claim by. A claim whole
    by the gate's comparison before CORE5 is accepted too (store.before_core5): a tidy then
    could fold passages that differ only by white space, and its version is imported as it
    is, never rewritten; the next tidy or compile rebuilds the latest (compiler.rebuilt_folds).
    """
    where = f"a line of {root}pages.jsonl"
    if missing := [key for key in _PAGE_LINE if key not in line]:
        raise ValueError(f"{where} has no {missing[0]!r}")
    slug = _text(line["slug"], f"the slug on {where}")
    if not re.fullmatch(_SLUG, slug):
        raise ValueError(f"{slug!r} on {where} is not a page slug")
    version = line["version"]
    if slug in versions:
        expected = versions[slug] + 1
    else:  # a page's first line: version 1, or a later one when a plan dropped the older
        whole = isinstance(version, int) and not isinstance(version, bool) and version >= 1
        expected = version if whole else 1
    if version != expected or isinstance(version, bool):
        raise ValueError(f"the page {slug!r} in {root} should have version {expected} next")
    if not isinstance(line["claims"], list) or not isinstance(line["metrics"], dict):
        raise ValueError(f"the claims or metrics of {slug!r} in {root} are not well formed")
    _metrics(line["metrics"], slug, root)
    if len(line["claims"]) > PAGE_CLAIMS_MAX:
        raise ValueError(
            f"the page {slug!r} in {root} holds {len(line['claims']):,} claims: a page holds at"
            f" most {PAGE_CLAIMS_MAX:,}"
        )
    try:
        claims = tuple(
            Claim(id=claim["id"], text=claim["text"], span_ids=claim["passage_ids"])
            for claim in line["claims"]
        )
    except (TypeError, KeyError, ValidationError):  # neither message is shown: they quote the input
        raise ValueError(f"a claim of {slug!r} in {root} is not well formed") from None
    for n, fault in version_faults(claims, spans, legacy=True, text_bound=False):
        raise ValueError(fault.said(slug, expected, root, n, claims))
    return Page(
        project_id="",
        slug=slug,
        title=_text(line["title"], f"the title on {where}"),
        body=_text(line["body"], f"the body on {where}"),
        version=expected,
        claims=claims,
        metrics=stored_metrics(line["metrics"]),
        updated_at=_time(line["created_at"], f"the time on {where}"),
    )
