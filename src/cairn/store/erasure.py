"""Erasure of one project or one note (H5b): `delete_project`, `forget_note`, and
`may_erase`, the question the web view asks before it offers either.

Who may: an owner of the workspace, the person who made the project, or a view acting for no
person (the command line, the workspace's own key). Anyone else who reaches the project gets
PermissionError, and so does any connection limited to some projects or to reading
(StoreBase._limited_connection), whoever approved it. Out of reach is checked first, inside
the write, and is exactly unknown.

Both run in one write transaction and cost what they remove, never the whole database: the
rows are found by their project (and a forgotten note's pages by the workspace's page
versions, whatever their project, and so are a deleted project's), the project's search
rows by `search_claims`, by primary key. What they delete is zeroed
(`PRAGMA secure_delete`, FTS5's own secure-delete), and each ends with a truncating
checkpoint, as delete_account does, so the text leaves the files at once. Each marks a
compaction due (cairn.store.portable.compaction_state): short index keys, such as a page's
slug or a project's name, can stay in index pages until `compact()`.

Forgetting keeps a SHA-256 digest of the note's text in `forgotten`, so the same text is
never stored in that project again (add_note raises ForgottenNote), and a page save that
cites a span the store no longer holds, in such a workspace, is CitesForgotten (save_page):
a compile or arrangement that read the page before the note was forgotten never brings it
back. The digests go with their project and their workspace and are never exported. A
forget or a delete that changes or removes a page of another project leaves that project
one row of `forgotten` that is no digest of any text (PASSAGES_REMOVED): it records only
that passages a page of the project cited went with another project, so a page drafted
before never brings them back. It goes with its project like the others, is never
exported, and refuses no note, since no text has that digest.

The audit log keeps that each happened, who did it and when, and nothing that names what
is gone. delete_project moves every event of the project, project.deleted included, from the
project's name to audit.DELETED_PROJECT (its id and a word, with a space no project name can
hold), which list_audit shows to owners and to views acting for no person only, and a later
project of the same name starts with none of them; so does a source event that named the
project among the projects a source writes into (its `projects`). delete_project also takes
the project's name out of every source that names it, in the same write, and switches off a
source left with no project (cairn.store.sources, "A project that stops existing"). Both
take a removed page's slug (made from a note's title) out of the events that name it:
delete_project from every event of the project, forget_note from the events of each page it
removes; the events themselves stay.
`page` is the one value of a project's events made from a note (NOTE_DERIVED).
"""

import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence

from cairn.spans import (
    _PARSER,
    _PARSER_LINES,
    CLOSERS_MAX,
    Span,
    _covering_fence,
    asked_line,
    claim_id,
    fences_reaching,
    following,
)
from cairn.store import ranking
from cairn.store.access import MODELS_PREFIX
from cairn.store.audit import DELETED_PROJECT
from cairn.store.base import (
    COMPACT_DUE,
    DEFAULT_ACCOUNT_ID,
    RECORD_DELETION,
    StoreBase,
    _has_table,
    _index_name,
    deletion_generation,
    store_log,
)
from cairn.store.before_core5 import whole_before_core5
from cairn.store.memory import _SPAN_COLUMNS, _span, _write_page_version
from cairn.store.models import Claim, Forgotten
from cairn.store.portable import Refusal, version_faults

NOT_AN_ERASER = (
    "only the project's maker and the workspace's owners can delete a project or forget its notes"
)
LIMITED_ERASER = (
    "this connection is limited to chosen projects or to reading: deleting a project or"
    " forgetting a note takes a person, or a key of the whole workspace"
)
# The renders that each name a claim a forget makes of a page before every claim of the
# named one's section that the body leaves open, and every claim after it that can open a
# fence, leave with it whatever the title does (_plan's second stated class).
# Why a cap: notes stored in reverse that spell the title two ways in turn, under a heading
# that says one spelling, cost a render a note in the plain rule and in the rule without
# it (a thousand: 1,001 renders, 74 s; with the cap at 64, 66 renders and 17 s inside the
# write). Why 24: outside that family (test_forget_title_line_unit.py's pages are of it, 24
# renders that name a claim, exact at 24) a forget rendered a page at most 19 times on the
# pages tried: the generated pages, seeds 0 to 299, where the plain rule rendered up to 37
# times, the reviewers' and the tester's, and test_forget_changed_title_unit.py's;
# runbook-style notes filed one after another, 2. So within it a forget is exact (the
# plain rule, but for the class stated past CLOSERS_MAX), and that worst case renders
# RENDERS_MAX + 2 times, at any size (tests/unit/test_forget_render_cap_unit.py,
# test_forget_cost_unit.py).
RENDERS_MAX = 24
# The renders that each name a claim before the next takes every claim of the page that can
# open a fence (_plan's third stated class), after which no claim can own a fence that runs
# on; a render after that which still names a claim is the end. Why a second cap: each
# class closed one chain and the same chain was rebuilt one step further out (the run, then
# the section, then a pair a section: 300 pairs, 301 renders, 109 s inside the write), so
# the bound is a number, whatever the page: RENDERS_HARD + 3 renders. Why 32: room for the
# section's class to end the chains it ends (RENDERS_MAX + 2 renders), as it did at 32.
RENDERS_HARD = 32
# The keys of an audit event's detail made from a note: a page's slug comes from its title.
# The other keys a project's events carry are counts, kinds, codes, rule ids, a model's name,
# person ids and the project's own name and id (see the store contract's Audit log).
NOTE_DERIVED = ("page",)
# The row of `forgotten` a forget or a delete leaves each other project whose page it changed
# or removed (_rewrite): no digest of any text (a note's is 64 hex characters), so it refuses
# no note. It records only that passages a page of that project cited went with another
# project, so a save drafted before is CitesForgotten (memory._cites_forgotten) and never
# brings them back, whatever becomes of that other project. It goes with its project like the
# digests, and is never exported.
PASSAGES_REMOVED = ""
# The tables whose rows name projects in a reach (a JSON array of project and set ids, or NULL
# for every project), and the column that holds it.
_REACHES = (
    ("api_keys", "projects"),
    ("oauth_codes", "projects"),
    ("oauth_grants", "projects"),
    ("project_sets", "project_ids"),
)


def kept_claims(
    claims: Sequence[Claim], gone: frozenset[str], spans: Mapping[str, Span]
) -> tuple[Claim, ...]:
    """A page's claims once the spans `gone` are forgotten, in their order, with their ids.
    A claim that cites none of them is kept. One that cites some of them
    and other spans too keeps citing those others when its text is the whole of every one of
    them that the store still holds (`spans`, by id), by the verbatim gate's own equality
    (cairn.gate.whole, review 7.5): a claim folded from passages that say the same is kept,
    while one whose text merely occurs inside a remaining passage ("Enable X." inside "Never
    enable X. in prod.") or that joined the forgotten note's words goes. One that cites only
    forgotten spans goes.
    One folded by an earlier build from passages that differ by white space inside a line
    (store.before_core5.whole_before_core5) keeps citing the others in the same way.
    A kept claim that is the whole of every span it cites takes the text of the first of them
    the store holds, byte for byte (third report, must-fix 1): a fold's text is one note's
    spelling, and the white space that equality leaves out must not outlive that note, in
    this forget or one made before this rule. Any other kept claim keeps its text.
    A kept claim whose id was made from a forgotten span (spans.claim_id: a span id is a
    digest of its text) takes the id of the first span it keeps, or, when another kept claim
    holds that one, an id made from its place and the spans it keeps, drawn again until no
    kept claim holds it (fourth report, must-fix 5; as compiler.rebuilt_folds draws one): a
    page may hold any id of cairn's shape, so no claim loses its id, or its place on the
    page, to this one. A claim that cites no span at all (a page saved past the gates may
    hold one) is kept like any that cites no forgotten span, for the import's check to
    judge (_plan), and has no first span: its new id is drawn from its place at once."""
    from cairn.gate import whole  # the gate builds on the store

    kept = []
    for claim in claims:
        left = tuple(span_id for span_id in claim.span_ids if span_id not in gone)
        cut = len(left) < len(claim.span_ids)
        survivor = claim.model_copy(update={"span_ids": left}) if cut else claim
        # A fold an earlier build made of passages that differ by white space inside a line
        # is the whole of what it still cites by that build's rule, which an import accepts
        # and a tidy rebuilds: it outlives the note as the first passage left, like any fold.
        if whole(survivor, spans) or (cut and whole_before_core5(survivor, spans)):
            first = next(span_id for span_id in left if span_id in spans)
            kept.append(survivor.model_copy(update={"text": spans[first].text}))
        elif not cut:
            kept.append(claim)
    made_from_gone = {claim_id(span_id) for span_id in gone}
    taken = {claim.id for claim in kept if claim.id not in made_from_gone}
    for place, claim in enumerate(kept):
        if claim.id in made_from_gone:
            new, tries = claim_id(claim.span_ids[0]) if claim.span_ids else None, 0
            while new is None or new in taken:
                key = json.dumps([place, claim.span_ids, tries]).encode()
                new, tries = "cl_" + hashlib.sha256(key).hexdigest()[:16], tries + 1
            taken.add(new)
            kept[place] = claim.model_copy(update={"id": new})
    return tuple(kept)


class ErasureMethods(StoreBase):
    def may_erase(self, project_id: str) -> bool:
        """Whether this view may delete the project and forget its notes (see the module).
        KeyError for a project unknown or out of reach. A read."""
        with self._read() as con:
            _, maker_id, _ = self._reached(con, project_id)
            return self._erasure_refusal(con, maker_id) is None

    def delete_project(self, project_id: str) -> bool:
        """Remove the project and everything it holds: its notes with their spans, its pages
        with every version, its rows in the search index and statistics, its access rule and
        its forgotten digests. Every set and every key, code and grant whose reach names it
        loses it (one that named it alone then reaches nothing). A page of another project of
        the workspace that cites a span of its notes, in any version, changes as forget_note
        changes a page that cites the note (_plan, _rewrite), in the same transaction: only a
        program that wrote into the database directly, or an older build's writer still open
        across the upgrade, leaves one (save_page refuses a new one, and the upgrade to schema
        32 rewrote every one an earlier build left). The workspace's
        stored bytes go down by its notes' size; the month's count of notes written does not.
        Recorded as project.deleted (its id and name, how many
        notes and pages, and how many pages of other projects changed as `elsewhere` when it is
        not zero: counted, never named, as the log's line for each). False for a project
        unknown or out of reach; PermissionError for anyone else who may not (see the
        module)."""
        index = self._index
        with self._write() as con:
            try:
                name, maker_id, _ = self._reached(con, project_id)
            except KeyError:
                return False
            self._refuse_erasure(con, maker_id)
            gone = _passages(con, "notes.project_id", project_id)
            plan = _plan(con, self._account_id, gone, deleted=project_id)
            _rewrite(con, self._account_id, index, plan, self._stamp(), project_id)
            notes, size = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(text AS BLOB))), 0) FROM notes"
                " WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            (pages,) = con.execute(
                "SELECT COUNT(DISTINCT slug) FROM page_versions WHERE project_id = ?", (project_id,)
            ).fetchone()
            if _has_table(con, index):
                con.execute(
                    f"DELETE FROM {index} WHERE rowid IN"
                    " (SELECT row FROM search_claims WHERE account_id = ? AND project_id = ?)",
                    (self._account_id, project_id),
                )
            con.execute(
                "DELETE FROM search_claims WHERE account_id = ? AND project_id = ?",
                (self._account_id, project_id),
            )
            for statement in (
                "DELETE FROM search_projects WHERE project_id = ?",
                "DELETE FROM page_heads WHERE project_id = ?",  # Q1: first, so its trigger
                "DELETE FROM page_versions WHERE project_id = ?",  # finds no head to move
                "DELETE FROM page_keys WHERE project_id = ?",  # N1: each page's topic
                "DELETE FROM waiting WHERE project_id = ?",  # N1: its notes not yet filed
                "DELETE FROM note_writers WHERE note_id IN"  # A1: who wrote its notes
                " (SELECT id FROM notes WHERE project_id = ?)",
                "DELETE FROM slack_notes WHERE note_id IN"  # K1: which came through Slack
                " (SELECT id FROM notes WHERE project_id = ?)",
                "UPDATE slack_people SET project_id = NULL WHERE project_id = ?",  # K1
                "DELETE FROM spans WHERE note_id IN (SELECT id FROM notes WHERE project_id = ?)",
                "DELETE FROM notes WHERE project_id = ?",
                "DELETE FROM project_people WHERE project_id = ?",
                "DELETE FROM forgotten WHERE project_id = ?",
                "DELETE FROM projects WHERE id = ?",
            ):
                con.execute(statement, (project_id,))
            con.execute(  # M4: whether its pages went to a model
                "DELETE FROM settings WHERE account_id = ? AND name = ?",
                (self._account_id, MODELS_PREFIX + project_id),
            )
            for table, column in _REACHES:
                con.execute(
                    f"UPDATE {table} SET {column} = (SELECT json_group_array(value)"
                    f" FROM json_each({table}.{column}) WHERE value != ?1)"
                    f" WHERE account_id = ?2 AND {column} IS NOT NULL AND EXISTS"
                    f" (SELECT 1 FROM json_each({table}.{column}) WHERE value = ?1)",
                    (project_id, self._account_id),
                )
            self._forget_declined(con, project_id)
            self._unstore(con, size)
            moved = DELETED_PROJECT.format(project_id)
            self._forget_destination(con, name)
            _unname(con, self._account_id, name, moved, None)
            _unname_sources(con, self._account_id, name, moved)
            detail = {"id": project_id, "name": name, "notes": notes, "pages": pages}
            if plan:
                detail["elsewhere"] = len(plan)  # pages of other projects: counted, not named
            self._audit(con, "project.deleted", project=moved, detail=detail)
            _compaction_due(con, self._stamp())
        self._truncate_log(f"project {project_id}")
        _log_left(plan, project_id, "delete")  # once it is committed
        return True

    def forget_note(self, note_id: str) -> Forgotten:
        """Remove one note with its spans (see the module). A page of the workspace that cited
        it, in any of its versions and whatever its project, loses every version and
        continues as one new version, numbered one past its latest: the latest version's
        claims without the note (kept_claims), held to its own project's notes (_plan), its
        body cairn.compiler.render_body of them, its metrics the provenance counts ("claims",
        "spans_cited"), indexed for search in place of the old; a page left with no claim
        is removed, with its search rows. That version is written here, past every gate, so
        it is held to the import's own check of a version (portable.version_faults): a claim
        an import would refuse leaves it as the note's own claims do, never the forget, and
        one line on the store's log counts those claims by page and version (for a page they
        leave empty, the count alone: a removed page's slug is made from a note's title; for
        a page of another project, the count alone too), and the forget's audit event counts
        them over all its pages (`claims_left`, when there are any). `claims_left` is that
        count and no other: the claims the import's check took from the versions the forget
        wrote, those that left for a fence that ran on among them (_plan). The claims
        kept_claims does not keep, which cited the note alone or were not the whole of the
        passages they still cite, are the forget itself and are not in it. The project keeps
        the note's digest, so the same text is not stored there again. The workspace's
        stored bytes go down by the note's size; the month's count of notes written does
        not. Recorded as note.forgotten (the note's id, its project's id, how many pages
        changed, how many of them were of other projects as `elsewhere` and `claims_left`,
        each when it is not zero); nothing of its text, title or source_ref. The answer, the
        preview, the log and the event count the pages of other projects and name none of
        them or their projects: the one who forgets may not reach them. save_page refuses a
        new citation of another project's passage, and the upgrade to schema 32 and the
        `repair` command (repair_own_projects) rewrite every such page they find; one a program
        wrote into the tables since, or an older build's writer still open across the upgrade,
        is found here too, when the note is forgotten. KeyError
        for a note unknown or out of reach; PermissionError for anyone
        else who may not (see the module). Needs no room under any cap of a plan."""
        index = self._index
        with self._write() as con:
            project_id, text_sha256, size = self._note_in_reach(con, note_id)
            name, maker_id, _ = self._reached(con, project_id)
            self._refuse_erasure(con, maker_id)
            plan = _plan(con, self._account_id, _passages(con, "spans.note_id", note_id))
            con.execute("DELETE FROM spans WHERE note_id = ?", (note_id,))
            con.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            con.execute("DELETE FROM waiting WHERE note_id = ?", (note_id,))  # N1
            con.execute("DELETE FROM note_writers WHERE note_id = ?", (note_id,))  # A1
            con.execute("DELETE FROM slack_notes WHERE note_id = ?", (note_id,))  # K1
            _rewrite(con, self._account_id, index, plan, self._stamp(), project_id)
            con.execute(
                "INSERT INTO forgotten (project_id, text_sha256) VALUES (?, ?)"
                " ON CONFLICT DO NOTHING",
                (project_id, text_sha256),
            )
            self._unstore(con, size)
            detail = {"note": note_id, "project": project_id, "pages": len(plan)}
            if elsewhere := sum(at != project_id for at, *_ in plan):
                detail["elsewhere"] = elsewhere  # pages of other projects: counted, not named
            left = sum(refused for *_, refused, _ in plan)
            if left:  # claims an import would refuse, which left their pages with the note
                detail["claims_left"] = left
            self._audit(con, "note.forgotten", project=name, detail=detail)
            _compaction_due(con, self._stamp())
        self._truncate_log(f"note {note_id}")
        _log_left(plan, project_id)  # once it is committed
        return _outcome(note_id, project_id, plan)

    def forget_preview(self, note_id: str) -> Forgotten:
        """What forget_note would do now, by the same rule, writing nothing: the pages of the
        note's project it would rebuild and those it would remove, for anyone who reaches the
        note, and how many pages of other projects it would change, only for a view that may
        forget it (_erasure_refusal, as Memory.forget_preview asks may_erase first): anyone
        else is told 0 (report 8), since the count says something of projects they may not
        reach before any erasure. KeyError for a note unknown or out of reach. A read."""
        with self._read() as con:
            project_id, _, _ = self._note_in_reach(con, note_id)
            gone = _passages(con, "spans.note_id", note_id)
            found = _outcome(note_id, project_id, _plan(con, self._account_id, gone))
            _, maker_id, _ = self._reached(con, project_id)
            if self._erasure_refusal(con, maker_id) is not None:
                found = found.model_copy(update={"elsewhere": 0})
            return found

    def repair_own_projects(self, each: Callable[[str, int, int], None]) -> dict[str, int]:
        """The `repair` command's write (the operator's): hold_to_own_projects over the whole
        database, as the upgrade to schema 32 ran it, in one write transaction like a
        forget's, with `each` called for each workspace that had any (its id, the pages
        rewritten, the pages removed) before the commit. When a page changed, a compaction is
        marked due and the write-ahead log truncated after the commit, as a forget does: the
        versions it deleted held another project's text. The server's readers see each page as
        it was before or as it is after, never between; its writers wait for it as for a
        forget, and one that drafted on such a page before it is StalePage and reads the page
        again. On a database with no such page it makes one search and writes nothing.
        PermissionError on a view: only the store that opened the database repairs it."""
        if not self._owner:
            raise PermissionError("only the store that opened the database repairs it")
        with self._write() as con:
            done = hold_to_own_projects(con, None, self._stamp(), each)
            if done["rewritten"] or done["removed"]:
                _compaction_due(con, self._stamp())
        if done["rewritten"] or done["removed"]:
            self._truncate_log("pages held to their own projects' notes")
        return done

    def _note_in_reach(self, con: sqlite3.Connection, note_id: str) -> tuple[str, str, int]:
        """(project id, text digest, UTF-8 size) of a note of a project in the view's reach;
        KeyError otherwise, as for a note that does not exist."""
        row = con.execute(
            "SELECT notes.project_id, notes.text_sha256,"
            " LENGTH(CAST(notes.text AS BLOB)) FROM notes"
            " JOIN projects ON projects.id = notes.project_id"
            " WHERE notes.id = ? AND projects.account_id = ?",
            (note_id, self._account_id),
        ).fetchone()
        reach = self._reach(con)
        if row is None or (reach is not None and row[0] not in reach):
            raise KeyError(note_id)
        return row

    # Who may, inside the caller's section

    def _erasure_refusal(self, con: sqlite3.Connection, maker_id: str | None) -> str | None:
        """Why this view may not erase in a project made by `maker_id`, or None when it may:
        a limited connection never may; a view acting for no person, the maker or an owner
        may."""
        if self._limited_connection():
            return LIMITED_ERASER
        if self._user_id is None or self._user_id == maker_id:
            return None
        return None if self._role_in(con) == "owner" else NOT_AN_ERASER

    def _refuse_erasure(self, con: sqlite3.Connection, maker_id: str | None) -> None:
        if refusal := self._erasure_refusal(con, maker_id):
            raise PermissionError(refusal)

    # What goes with what is removed

    def _unstore(self, con: sqlite3.Connection, size: int) -> None:
        """The workspace's stored bytes, down by `size`: a running total (cairn.store.usage)."""
        con.execute(
            "UPDATE account_quota SET stored_bytes = MAX(0, stored_bytes - ?) WHERE account_id = ?",
            (size, self._account_id),
        )

    def _forget_declined(self, con: sqlite3.Connection, project_id: str) -> None:
        """Drop from each source's last report the declined drafts that name the project: a
        report keeps only ids of projects that exist. Its counts stay: they are the run's."""
        rows = con.execute(
            "SELECT name, last_report FROM sources WHERE account_id = ? AND instr(last_report, ?)",
            (self._account_id, project_id),
        ).fetchall()
        for name, last_report in rows:
            report = json.loads(last_report)
            declined = report.get("declined")
            if not isinstance(declined, list):
                continue
            report["declined"] = [
                item
                for item in declined
                if not (isinstance(item, dict) and item.get("project") == project_id)
            ]
            con.execute(
                "UPDATE sources SET last_report = ? WHERE account_id = ? AND name = ?",
                (json.dumps(report), self._account_id, name),
            )


# A page of a forgotten note's workspace that cites it in any version, whatever its project:
# (its project's id and name, its slug, its latest version, its title, the claims it keeps
# (kept_claims, less those an import would refuse), the spans of its project's notes still
# held that its latest version cites, how many claims an import would refuse, the body of the
# claims it keeps as compiler.render_body writes it).
_Planned = tuple[str, str, str, int, str, tuple[Claim, ...], dict[str, Span], int, str]


def _plan(
    con: sqlite3.Connection, account_id: str, gone: frozenset[str], deleted: str | None = None
) -> list[_Planned]:
    """What removing the spans `gone` (a note's, or every note's of the project `deleted`,
    whose own pages go whole and are not planned) does to the pages of the workspace
    (`account_id`), read on `con`: each page of any project of it that cites one of the
    spans in any version, by project and slug, with what its latest version keeps: the
    claims kept_claims keeps,
    less every claim the import's check of a version names (portable.version_faults: the
    claims it does not name pass together, so it is run once, on one render of the body,
    which is the body the forget writes). Each page is held to its own project's notes, as
    an import of that project holds it: in a page of another project, a claim that also
    cites a note of that project stays as that note's text, and one that cites a passage
    of a project not its own leaves (Refusal.NOT_OF_PROJECT), as in the note's project.

    But for a code fence that only the claims before it open and the body does not close
    (more than spans.CLOSERS_MAX of them, Refusal.FENCE_RUNS_ON). The claim that opens it
    leaves, the first in the body's order, and the rest are rendered and asked again, since
    without it the body is another. That alone is the plain rule, and it costs a render for
    each claim that leaves. So that it costs that only where the body changes after each
    claim beyond the claim itself, as when notes stored in reverse spell the title
    differently in turn and a heading says one spelling (the plain rule costs as much
    there), two kinds of claim leave in the same render as the claim named (`_with_it`):

    - In every render, the claims the body writes directly before it that the plain rule
      would name next, one a render: those whose fence, ended until now by the end of its
      list item, would cover the block that follows once the claim named has left. The
      parser is asked of each, on the body's own text (`_before_it`), so this takes no
      claim the plain rule keeps: not one whose fence is closed where it stands, nor one
      whose item would not hold that block, nor any claim before such a one (fifth report,
      must-fix 1). A question is never more than twice the body (sixth report, must-fix 4),
      and the first line of the block that follows is written in it cut to what the answer
      depends on, its indentation and first character (spans.asked_line), so a long line
      does not narrow what is asked (RT1).
    - From the render after CLOSERS_MAX renders that each named a claim, the claims the
      body writes after it that can open a fence there (`_may_open_a_fence`; fourth
      report, must-fix 2). This is wider than the plain rule: it takes a claim that starts
      with indentation and has a line that opens a fence, though the fence would close in
      the claim. What stays: a claim after it that can open none, such as one with inline
      code, and one that starts at the margin, a fenced block as it is commonly written,
      since it reads there as it does alone. And it changes what follows the claims before:
      one of them whose fence covers the block that then follows leaves as the first kind
      does, though the plain rule kept it behind a claim that left here; and one the plain
      rule dropped for covering such a claim stays.
    - From the render after RENDERS_MAX renders that each named a claim, in place of the
      first kind, every claim of its section (the claims under the same headings, from
      the heading the body writes before it) whose fence the body left open
      (`_left_open`): a fence that starts in the claim and covers the line after its
      last, with no line the body wrote after it. All of them at once, wherever in the
      section they stand, not asked of what follows them, whatever the title does at each
      step (the eighth round; the ninth: the run of them directly before the claim named
      stopped at a list item at the margin, and a chain of siblings each uncovered by the
      title the one before gave cost a render a sibling past the cap). This is wider than
      the plain rule: such a claim leaves though the plain rule would never name it, its
      list item not holding what follows it, or nothing its fence would cover ever coming
      to follow it. What stays: a claim of another section, one whose fence is closed, by
      its own line or by the body, and one that holds none. And, in place of the second
      kind, every claim after it that can open a fence, whatever the title does: past the
      cap a forget is no longer exact, and a title that changes at every step must not
      hold those claims to one a render.
    - At the render after RENDERS_HARD renders that each named a claim, whatever it names,
      in place of the others: every claim of the page that can open a fence
      (`_may_open_a_fence`), wherever the body writes it (the tenth round: each class had
      closed one chain, and the same chain was rebuilt one step further out, last with each
      pair in a section of its own). A claim of the page whose fence the body left open is
      one of them, or one at the margin that what the claim named leaves open runs into,
      whose own fence closes in its text: it cannot open a fence and stays, as such a claim
      does at every level. With them leave the claims the next render would name for
      another fault (version_faults without a fence: the claims it does not name pass
      together). No claim left can then own a fence that runs on, by what
      `_may_open_a_fence` is tested to mean, so the next render names none.
    - The end, by construction. Should a render after that still name a claim (the parser
      would have read one text two ways), every claim that holds a backtick or a tilde
      leaves with it, and those the next render would name for another fault; a page of the
      rest has no fence, and one more render writes it. The end is there so that a forget's
      renders are bounded by a number and not by an argument: at most RENDERS_HARD + 3,
      whatever the page.

    The kinds are chosen on the body just rendered, and the page is titled again by the
    claims left: a claim under a heading that says the title is written with no heading, so
    another title can be another body. The first two leave in the order the plain rule would
    name them, nearest first. The second leaves only while the title's text is this
    render's, within the cap (the eighth round: loosened as the first is, it took a claim
    the plain rule keeps on two generated pages and saved no render). The first leaves only
    as far as the plain rule would name each in a body written as this render's below its
    title line (`_with_it`; sixth report, must-fix 1): the title after each claim that
    leaves, and the first heading of each claim, are read once for all of them (`_Titles`);
    a new title changes more than its line only where the first heading of a claim still on
    the page says it or this render's title, and where it does at a step the claims before
    that step leave and the next render starts from there (RT1: holding to the note that
    gave the title cost a render for each claim of a run stored in reverse, and holding to
    its spelling a render a claim where the spelling alternates).

    What it costs. Until a forget has named CLOSERS_MAX claims, each render is one the plain
    rule makes too and takes at least the claim it names, so a forget renders no more often
    than the plain rule; past that the second kind can change the body, and up to
    RENDERS_HARD what ends the loop is that a render which names a claim takes it; past it,
    the number does. Where the title changes at
    every step and a first heading says one of its spellings, so that the body differs at
    each step, a forget takes one claim a render as the plain rule does, and the third kind
    ends that at RENDERS_MAX + 2 renders (a hundred, three hundred or a thousand notes: 26
    renders, and 0.5, 1.5 and 4.6 s, where the plain rule renders 101, 301 and 1,001 times;
    and where each sibling named uncovers the next across a list item at the margin, 900
    claims: 26 renders and 7 s, where the run before the claim named took 301 and 58 s).
    A new title can still uncover a fence the old one kept apart (nineteen renders, and
    nineteen in the plain rule: test_forget_changed_title_unit.py), and past RENDERS_MAX
    too: where each claim named is the oldest note's, and its going titles the page with a
    spelling that one heading alone says, over a tail that then joins the section of the
    next claim to name, each such claim in a section of its own, nothing else of its
    section is left open and nothing after it can open a fence, and a forget renders once a
    claim, as the plain rule does, up to RENDERS_HARD; the fourth kind ends that at
    RENDERS_HARD + 2 renders (thirty-four such claims: 34 renders and 1.3 s, against 35;
    three hundred: 34 renders and 12 s, where it took 301 renders and 109 s). So a forget
    renders at most RENDERS_HARD + 3 times, whatever the page (seconds on a loaded
    machine). A render parses the body whole at most CLOSERS_MAX + 2 times, each claim
    alone, and puts a few questions of twice the body at most. On the pages tried a forget
    rendered at most once more than the plain rule, and that only past CLOSERS_MAX, and
    handed the parser at most 25 characters for each character of its body in each render
    (tests/unit/test_forget_cost_unit.py): counts from those pages, not bounds.
    It reads the workspace's page versions in one search, whatever the number of its
    projects, never the rest of the database."""

    pages = con.execute(
        "SELECT DISTINCT p.project_id, projects.name, p.slug FROM page_versions AS p"
        " JOIN projects ON projects.id = p.project_id"
        " WHERE projects.account_id = ? AND p.project_id IS NOT ? AND EXISTS"
        " (SELECT 1 FROM json_each(p.claims) AS claim,"
        "  json_each(claim.value, '$.span_ids') AS cited"
        "  WHERE cited.value IN (SELECT value FROM json_each(?)))"
        " ORDER BY p.project_id, p.slug",
        (account_id, deleted, json.dumps(sorted(gone))),
    ).fetchall()
    return [_plan_page(con, project_id, name, slug, gone) for project_id, name, slug in pages]


def _plan_page(
    con: sqlite3.Connection, project_id: str, name: str, slug: str, gone: frozenset[str]
) -> _Planned:
    """One page of `_plan`, by the same rule, whoever asks: the page `slug` of the project
    `project_id` (named `name`) once the spans `gone` leave it, held to its own project's
    notes. hold_to_own_projects asks it of a page whose other projects' passages go."""
    from cairn.compiler import left_to_run  # the compiler builds on the store

    version, title, claims_json = con.execute(
        "SELECT version, title, claims FROM page_versions"
        " WHERE project_id = ? AND slug = ? ORDER BY version DESC LIMIT 1",
        (project_id, slug),
    ).fetchone()
    claims = [Claim(**claim) for claim in json.loads(claims_json)]
    cited = [i for claim in claims for i in claim.span_ids if i not in gone]
    spans = _held_spans(con, project_id, cited)
    kept, refused, body, renders = kept_claims(claims, gone, spans), 0, "", 0
    titles = _Titles(con, kept, spans, slug)  # the title the page has as claims leave
    while kept:  # until the claims left are a version an import takes: once, but for a
        # fence that the claims before it open, where each time the first such claim leaves
        title = titles.of(kept)
        body, run_on = left_to_run(title, kept, spans)
        if renders > RENDERS_HARD + 1:  # the end: no claim left holds a backtick or a tilde
            break
        faults = list(  # each claim is asked alone once: the claims do not change
            version_faults(kept, spans, legacy=True, run_on=run_on, ask_alone=not renders)
        )
        named = {place for place, _ in faults if place}
        if not named:
            break
        renders += 1
        if renders > RENDERS_HARD:  # the page's class; a render after it, the end
            takes = _fenced if renders > RENDERS_HARD + 1 else _may_open_a_fence
            named |= {place for place, claim in enumerate(kept, 1) if takes(claim.text)}
        elif faults == [(run_on, Refusal.FENCE_RUNS_ON)]:
            named |= _with_it(title, kept, spans, run_on, body, renders, titles)
        kept = tuple(claim for place, claim in enumerate(kept, 1) if place not in named)
        refused += len(named)
        if renders > RENDERS_HARD:  # what the next render would name but for a fence, now
            more = {
                place
                for place, _ in version_faults(
                    kept, spans, legacy=True, run_on=None, ask_alone=False
                )
                if place
            }
            kept = tuple(claim for place, claim in enumerate(kept, 1) if place not in more)
            refused += len(more)
    return (project_id, name, slug, version, title, kept, spans, refused, body)


def _with_it(
    title: str,
    kept: Sequence[Claim],
    spans: Mapping[str, Span],
    run_on: int,
    body: str,
    renders: int,
    titles: "_Titles",
) -> set[int]:
    """The places (1 for the first) of the claims of `kept` that leave with the claim at
    `run_on`, whose fence `body`, the body titled `title` as just rendered, leaves to run on
    (_plan). The order is the body's (compiler._blocks: the claims with no section first),
    never the stored list's.

    `renders`, the renders of the page that named a claim, this one counted. Once past
    CLOSERS_MAX: the claims the body writes after that one that can open a fence
    (`_may_open_a_fence`), the nearest first, as far as the title's text stays this
    render's with each gone (`_Titles.steady` with `text`, the eighth round).

    Past RENDERS_MAX: every claim of that one's section whose fence the body left open
    (`_left_open`), all at once and not asked of what follows them, and every claim after
    it that can open a fence, whatever the title does at any step.

    Else, in every render: the claims the body writes directly before that one that the
    plain loop would name next, one a render, the nearest first (`_before_it`), once all
    the claims after it of the first kind leave: each is asked of the parser on this body
    without the claims before it in that order, followed by the block that follows the
    claim named once it and the first kind have left.

    They leave in that order, as the plain loop would name them one after another, and
    each must be named in the body this one is after the claims before it in the order
    have left (sixth report, must-fix 1; RT1). The page is titled again by the claims left,
    and a claim under a heading that says the title is written with no heading: with
    another title the body can write its claims elsewhere, and a heading may stand where a
    fence was to reach. Where every claim keeps its section it writes them where it did:
    the claims before the one named stand where they stood, the block that follows them is
    the one asked about (claims next to one another with no heading between them have one
    section, so the headings written after them are the same whichever of them is last),
    and the closers before them are the same; the title line alone may differ, and it is
    one heading to the parser whatever it says. Every claim keeps its section when the
    title's text is the same (another note that spells it the same is no change), and when
    no claim left has a first heading that says this render's title or the new one
    (`_Titles.steady`, which compares with this render's title at each step, so a title
    that changes and comes back is a change at the step where it changed). That is read
    once for all the claims, and the block that follows is laid out once. Where a step
    changes a section, the claims before that step leave and the next render starts from
    there; the claim named leaves alone where its own going does. The claims before leave
    only as far as the sections hold, the claims after only as far as the title's text,
    and the claims before are asked about only when all the claims after left, so that what
    follows them is the body's."""
    from cairn.compiler import _blocks, heading_text, section_headings  # it builds on the store

    def laid_out(claims: Sequence[Claim]) -> list[tuple[str, Claim | None]]:
        return _blocks(title, claims, spans, str, section_headings, heading_text)

    past, capped = renders > CLOSERS_MAX, renders > RENDERS_MAX
    blocks, named = laid_out(kept), kept[run_on - 1]
    at = next(n for n, (_, claim) in enumerate(blocks) if claim is named)
    later = [
        c for text, c in blocks[at + 1 :] if (past or capped) and c and _may_open_a_fence(text)
    ]
    if capped:  # all of them, and its section's claims left open before it, whatever the title
        return _places(kept, [*later, *(blocks[n][1] for n in _left_open(body, blocks, at))])
    took = later[: max(titles.steady(kept, [named, *later], title, text=True) - 1, 0)]
    leave = {id(claim) for claim in took}
    nearest = []  # the claims written directly before the one named, the nearest first
    for _, claim in reversed(blocks[:at]):
        if claim is None:
            break
        nearest.append(claim)
    if len(took) == len(later) and nearest:
        steady = titles.steady(kept, [named, *took, *nearest], title)
        without = [claim for claim in kept if claim is not named and id(claim) not in leave]
        written = laid_out(without)  # the same blocks before the one named, then what follows
        if at < len(written):
            line = _PARSER_LINES.split(written[at][0])[0]
            asked = _before_it(body, blocks, at, line, most=steady - len(took))
            leave |= {id(blocks[n][1]) for n in asked}
    return _places(kept, [claim for claim in kept if id(claim) in leave])


def _places(kept: Sequence[Claim], claims: Iterable[Claim | None]) -> set[int]:
    """The places (1 for the first) of `claims` among `kept`."""
    place = {id(claim): n for n, claim in enumerate(kept, 1)}
    return {place[id(claim)] for claim in claims if claim is not None}


class _Titles:
    """The title a forget gives its page as claims leave it: the `topic or title` of the
    oldest note (in the order notes were stored) that one of the claims left cites, which
    is what a page made by that note alone is titled (cairn.compiler.compile_note), else
    the page's slug. Read once for all the page's claims, in one query, never once a render
    or a step. And each claim's first heading, as the body takes it (compiler._blocks: the
    heading path of the first span it cites that is held), which is all of a claim that a
    title is compared with (compiler.section_headings)."""

    def __init__(
        self,
        con: sqlite3.Connection,
        claims: Sequence[Claim],
        spans: Mapping[str, Span],
        slug: str,
    ) -> None:
        self._head = {  # each claim's first heading, None for a claim with none
            id(claim): next((spans[i].heading_path for i in claim.span_ids if i in spans), ())[:1]
            or None
            for claim in claims
        }
        cited = [i for claim in claims for i in claim.span_ids]
        rows = con.execute(
            "SELECT spans.id, notes.seq, COALESCE(NULLIF(notes.topic, ''), notes.title)"
            " FROM spans JOIN notes ON notes.id = spans.note_id"
            " WHERE spans.id IN (SELECT value FROM json_each(?))",
            (json.dumps(list(dict.fromkeys(cited))),),
        ).fetchall()
        noted = {span_id: (seq, named) for span_id, seq, named in rows}
        self._slug = slug
        self._oldest = {  # each claim's oldest note: (its place in the stored order, its title)
            id(claim): min((noted[i] for i in claim.span_ids if i in noted), default=None)
            for claim in claims
        }

    def _text(self, oldest: tuple[int, str | None] | None) -> str:
        return (oldest[1] or self._slug) if oldest else self._slug

    def of(self, claims: Iterable[Claim]) -> str:
        """The title of the page when `claims` are the ones left on it."""
        return self._text(min(filter(None, (self._oldest[id(c)] for c in claims)), default=None))

    def steady(
        self, claims: Sequence[Claim], leaving: Sequence[Claim], title: str, *, text: bool = False
    ) -> int:
        """How many of `leaving` (claims of `claims`), taken in their order, can leave one
        after another with the page written the same below its title line as when titled
        `title`: the largest m such that, for every j up to m, `claims` without the first j
        of `leaving` are titled `title`, or by a text that no first heading of theirs says
        and `title` is not said by one either. A claim's section is its heading path less a
        first heading that says the title, compared as stored (compiler.section_headings);
        where neither title is such a heading every claim keeps its section, so the blocks
        are the same and in the same order, and the bodies differ in the title line alone,
        one heading to the parser whatever it says. The same text from another note is no
        change. With `text`, the largest m such that they are titled `title` for every j up
        to m, whatever the headings say (the class a forget takes after the claim named).
        One sort and one count, then a step for each."""
        place = {id(claim): n for n, claim in enumerate(claims)}
        ranked = sorted((o[0], n) for n, c in enumerate(claims) if (o := self._oldest[id(c)]))
        heads = Counter(self._head[id(claim)] for claim in claims)  # of the claims not gone
        gone: set[int] = set()
        first = 0  # the oldest of `ranked` not gone
        for count, claim in enumerate(leaving):
            gone.add(place[id(claim)])
            heads[self._head[id(claim)]] -= 1
            while first < len(ranked) and ranked[first][1] in gone:
                first += 1
            oldest = self._oldest[id(claims[ranked[first][1]])] if first < len(ranked) else None
            now = self._text(oldest)
            if now != title and (text or heads[(now,)] or heads[(title,)]):
                return count
        return len(leaving)


def _before_it(
    body: str, blocks: Sequence[tuple[str, Claim | None]], at: int, line: str, *, most: int
) -> set[int]:
    """Which blocks directly before the block at `at`, the one `body` leaves to run on, leave
    with it: those the plain loop would name one after another, a render each, the nearest
    `most` of them at most (_with_it: those it would name with the page written the same
    below its title). `blocks` are the body's (compiler._blocks, each claim's text as
    given), `line` the first line of the block that will follow them.

    Why any would: a fence with no closing line ends where its list item ends. The claim
    before the one named may hold such a fence, ended only because the claim named began
    another item; with that claim gone the item holds the next block, the fence covers it,
    and no closer is left for it. Then the same of the claim before that.

    Asked of the parser, on the body's own text (spans.fences_reaching): the claims are
    written as the body has them, each followed by `line`, and a claim leaves when a fence
    that starts in it takes that line in, and so does every claim between it and the one
    named. That is the question the next render would ask of the nearest of them, and the
    render after of the next: a claim whose fence is closed where it stands, or whose item
    would not hold the line, is not taken, and nothing before it is. An answer is the
    parser's own only when every claim before it in the same question holds the line too.
    So the question is asked of all of them first; when one does not hold the line, of the
    last ones that all said they did (a guess, asked again without the others); then of
    half of those still open, until the answers leave none: about log2 of their number of
    parses at most, four on the pages tried, one or two on nearly all.

    A question is the body up to the claim named, with `line` and a blank line once more
    for each claim asked, `line` as spans.asked_line writes it after that claim: whole when
    it is no longer than the claim's longest line, else cut to its indentation and first
    character, which is all the answer depends on (spans.fences_reaching). It is never more
    than twice the body (sixth report, must-fix 4), and how long `line` is no longer narrows
    what is asked (RT1): every claim fits when what is written after each is no longer than
    the claim, which holds but where a claim's widest line is mostly tabs. Past that bound
    only as many of the nearest are asked as fit, and none when not one does. Its
    indentation and first character are worked out once here (spans.following) and every
    question is written from them, so building the questions costs what they write, never
    the line's length once a claim (eighth round, finding 2).

    Only claims are asked that hold a backtick or a tilde and after which the body wrote
    nothing: a claim with a closer is closed. And only when the body wrote all its
    CLOSERS_MAX closers: with one to spare the next render would close the fence, not name
    its claim (the claim named was then one that no line closes). Nothing is asked when
    `body` is not these blocks in order."""
    found = _open_run(body, blocks, at)
    if found is None:
        return set()
    starts, run = found
    texts = [text for text, _ in blocks[: at + 1]]
    said = following(line)  # its indentation and first character, once for every question
    room, left = 0, 2 * len(body) - starts[at]  # the claims a question may ask, the nearest
    while room < min(at - run, most):  # first, and what twice the body leaves for them
        left -= len(asked_line(said, texts[at - room - 1])) + 2
        if left < 0:
            break
        room += 1
    low, high = 0, room  # the last `low` leave; the last `high + 1` not all
    ask = first = high
    while low < high:
        taken = fences_reaching(body[: starts[at - ask]], texts[at - ask : at], said)
        if all(taken):
            low = ask
        else:
            high = ask - taken.index(False) - 1
        guess = next((n for n, yes in enumerate(reversed(taken)) if not yes), ask)
        ask = min(max(guess, low + 1), high) if ask == first else (low + high + 1) // 2
    return set(range(at - low, at))


def _written(
    body: str, blocks: Sequence[tuple[str, Claim | None]], at: int
) -> tuple[list[int], list[str]] | None:
    """Where each block up to the one at `at`, the one `body` leaves to run on, starts in
    `body`, and what the body wrote after each block before it (the lines that close its
    fences). None when `body` is not these blocks in order. No parse: whether the body
    wrote all its closers is `_spent`'s question, asked once there is something to ask."""
    texts = [text for text, _ in blocks[: at + 1]]
    starts = _placed(body, texts)
    if starts is None:
        return None
    return starts, [body[starts[n] + len(texts[n]) : starts[n + 1] - 2] for n in range(at)]


def _spent(blocks: Sequence[tuple[str, Claim | None]], wrote: Sequence[str]) -> bool:
    """Whether the body wrote all its CLOSERS_MAX closers, `wrote` as `_written` gives it:
    with one to spare the next render would close a fence, not name its claim (the claim
    named was then one that no line closes). A parse for each block the body wrote lines
    after, so it is asked last, when there is a claim to ask about."""
    from cairn.compiler import _closed  # the compiler builds on the store

    closers = sum(  # each line the body wrote, less the one a claim alone is closed by
        lines.count("\n") - (_closed(text) != text)
        for (text, _), lines in zip(blocks, wrote, strict=False)
        if lines
    )
    return closers == CLOSERS_MAX


def _open_run(
    body: str, blocks: Sequence[tuple[str, Claim | None]], at: int
) -> tuple[list[int], int] | None:
    """The claims directly before the block at `at`, the one `body` leaves to run on, that a
    fence of theirs may have reached it: (where each block up to it starts in `body`, the
    first of those claims). They follow one another with no heading and no other claim
    between, each holds a backtick or a tilde, and the body wrote nothing after any of them
    (a claim with a closer is closed). None when there is none, as `_written`, or when the
    body did not write all its closers (`_spent`, asked only when there is such a claim)."""
    found = _written(body, blocks, at)
    if found is None:
        return None
    starts, wrote = found
    run = at  # the first of the claims
    while run and blocks[run - 1][1] and not wrote[run - 1] and _fenced(blocks[run - 1][0]):
        run -= 1
    return None if run == at or not _spent(blocks, wrote) else (starts, run)


def _left_open(body: str, blocks: Sequence[tuple[str, Claim | None]], at: int) -> set[int]:
    """Which blocks before the block at `at`, the one `body` leaves to run on, leave with it
    past RENDERS_MAX (_plan's third kind, the second stated class): every claim of its
    section, from the heading before it (or the title), that holds a code fence the body
    left open: one that starts in the claim and covers the line after its last (a fence
    with no closing line, ended by the end of its list item), and after which the body
    wrote nothing. Wherever in the section it stands: a list item at the margin between it
    and the claim named does not keep it (the ninth round). Not a claim whose fence closes
    in its own text, nor one the body wrote a closer after, nor one with no fence. Not asked
    of what follows them: one parse of the body as it is, whatever the title does next;
    none as `_written` and `_spent`. After the claim named the section is not asked: a
    claim there whose fence has no closing line can open a fence, and leaves by
    `_may_open_a_fence`; one at the margin that this parse reads open is so only because
    what the claim named leaves open runs on into it, and its own fence closes in its
    text."""
    found = _written(body, blocks, at)
    if found is None:
        return set()
    starts, wrote = found
    section = at  # the first block of the claim named's section
    while section and blocks[section - 1][1] is not None:
        section -= 1
    asked = [n for n in range(section, at) if not wrote[n] and _fenced(blocks[n][0])]
    if not asked or not _spent(blocks, wrote):
        return set()
    stops = {  # each fence of the body, by the line it starts on: the line after its last
        token.map[0]: token.map[1]
        for token in _PARSER.parse(body)
        if token.type == "fence" and token.map
    }
    out, line, at_char = set(), 0, 0
    for n in asked:  # in the body's order: the lines before each are counted once
        line += len(_PARSER_LINES.findall(body, at_char, starts[n]))
        at_char = starts[n]
        end = line + len(_PARSER_LINES.split(blocks[n][0]))
        if any(stops.get(first, -1) > end for first in range(line, end)):
            out.add(n)
    return out


def _fenced(text: str) -> bool:
    return "`" in text or "~" in text  # no other text holds a fence (spans.fences_kept_in)


def _placed(body: str, texts: Sequence[str]) -> list[int] | None:
    """Where each of `texts`, the first blocks of `body` in order, starts in it; None when
    `body` is not those blocks, each followed by a blank line. Between a block's text and
    that blank line stand the lines the body wrote to close its fences, none of them empty."""
    starts, at = [], 0
    for text in texts:
        if not text or not body.startswith(text, at):
            return None
        starts.append(at)
        end = body.find("\n\n", at + len(text))
        at = len(body) if end < 0 else end + 2
    return starts


def _may_open_a_fence(text: str) -> bool:
    """Whether a claim of this text can open a code fence that runs past it, somewhere a
    body writes it. The Markdown parser's answers, never a reading of its lines here.

    What stands before a claim reaches into it in one way: a list item holds what is
    indented under it, across the blank line. So a claim whose first line starts at the
    margin is read in every body as it is alone, where the body writes it closed
    (compiler._closed; one that no line closes, the import's check has refused by then):
    False. Of any other, each line is asked alone and without its indentation (its tabs
    first read to the next stop of four, as the parser reads them), the question
    spans.open_fence_closer asks: whether a fence that starts there covers a line written
    after a blank one. A line can lose indentation to a list item and nothing else, and one
    that opens no fence with none of its indentation opens none with part of it; a fence in
    a quotation ends with the quotation, at the blank line after the claim at the latest,
    and is not counted. One parse for a line, and a line without three backticks or three
    tildes in a row is not parsed. So a claim this is False of opens no fence that covers
    the block after it (inline code is not a fence), and one it is True of may open none
    where it stands, or one that closes in the claim."""
    if text[:1] not in ("", " ", "\t", "\n", "\r"):
        return False
    alone = (line.expandtabs(4).lstrip(" ") for line in _PARSER_LINES.split(text))
    return any(
        _covering_fence(line, 1, [line]) is not None
        for line in alone
        if "```" in line or "~~~" in line
    )


def _outcome(note_id: str, project_id: str, plan: list[_Planned]) -> Forgotten:
    """What the one who forgets is told: the pages of the note's project by slug, and those
    of other projects by their number alone (they may not reach them)."""
    own = [(slug, kept) for at, _, slug, _, _, kept, *_ in plan if at == project_id]
    rebuilt = tuple(slug for slug, kept in own if kept)
    removed = tuple(slug for slug, kept in own if not kept)
    elsewhere = len(plan) - len(own)
    return Forgotten(note_id=note_id, rebuilt=rebuilt, removed=removed, elsewhere=elsewhere)


def _passages(con: sqlite3.Connection, column: str, value: str) -> frozenset[str]:
    """The ids of the spans going: a note's (`spans.note_id`) or all of a project's notes'
    (`notes.project_id`), by an index."""
    rows = con.execute(
        f"SELECT spans.id FROM spans JOIN notes ON notes.id = spans.note_id WHERE {column} = ?",
        (value,),
    )
    return frozenset(span_id for (span_id,) in rows)


def _rewrite(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    plan: list[_Planned],
    stamp: str,
    leaving: str | None,
) -> None:
    """Write what `_plan` planned, in the caller's transaction (forget_note, delete_project):
    each page that keeps a claim continues as one new version, numbered one past its latest,
    every earlier version deleted; a page left with no claim is removed with its topic key and
    search rows, and its slug leaves the events of its project. Each project other than
    `leaving` (the one the notes are leaving: a forgotten note's, or the deleted one) whose
    page this changes or removes gets one row PASSAGES_REMOVED, once, so a save drafted
    before stays stale whatever becomes of `leaving` and its digests."""
    for at, _, slug, version, title, kept, _, _, body in plan:  # at: the page's project
        if kept:
            cited = {i for claim in kept for i in claim.span_ids}
            metrics = {"claims": len(kept), "spans_cited": len(cited)}
            page = (
                at,
                slug,
                version + 1,
                title,
                body,
                json.dumps([claim.model_dump(mode="json") for claim in kept]),
                json.dumps(metrics),
                stamp,
            )
            _write_page_version(con, account_id, index, page, kept)
            con.execute(
                "DELETE FROM page_versions WHERE project_id = ? AND slug = ? AND version <= ?",
                (at, slug, version),
            )
        else:
            for table in ("page_versions", "page_keys"):  # N1: and its topic
                con.execute(
                    f"DELETE FROM {table} WHERE project_id = ? AND slug = ?",
                    (at, slug),
                )
            if _has_table(con, index):  # its rows and its project's counts
                ranking.index_page(con, account_id, index, at, slug, title, ())
    for at in dict.fromkeys(at for at, *_ in plan if at != leaving):
        con.execute(
            "INSERT INTO forgotten (project_id, text_sha256) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (at, PASSAGES_REMOVED),
        )
    removed: dict[str, list[str]] = {}  # by the name of each page's project
    for _, named, slug, _, _, kept, *_ in plan:
        if not kept:
            removed.setdefault(named, []).append(slug)
    for named, slugs in removed.items():
        _unname(con, account_id, named, named, slugs)


# A passage a page version cites (`cited.value`; `p`: the page version) that no note of the
# page's own project holds, what an import refuses: another project's, whatever workspace that
# project is in, or one the store no longer holds (a forget searches its own workspace alone,
# so a page of another that cited the note keeps the id). hold_to_own_projects and
# count_pages_citing_other_projects ask it, so the repair and the operator's count are one
# search.
_NOT_OF_ITS_PROJECT = (
    "NOT EXISTS (SELECT 1 FROM spans JOIN notes ON notes.id = spans.note_id"
    " WHERE spans.id = cited.value AND notes.project_id = p.project_id)"
)
_CITES_OUTSIDE_ITS_PROJECT = (
    "EXISTS (SELECT 1 FROM json_each(p.claims) AS claim,"
    f" json_each(claim.value, '$.span_ids') AS cited WHERE {_NOT_OF_ITS_PROJECT})"
)
# hold_to_own_projects' one pass. The next project of the scope (?1: a workspace, or NULL for
# every one) after (?2, ?3), in the order of the unique index on (account_id, name), with the
# slug of its first page, in its versions' primary key order, any version of which cites
# outside its project; NULL for a project that has none, which the outer query passes over.
# The projects after (?2, ?3) are those of ?2 after ?3 and then those of the workspaces after
# ?2, each a seek on the index, merged in order (a single row value would seek on account_id
# alone and walk the workspace's projects from its first on every search); the subquery is
# asked of each project as the merge hands it on, and each LIMIT -1 keeps a query a
# co-routine, so each project's versions are searched once, up to that page.
_NEXT_PROJECT = (
    "SELECT account_id, name, id, slug FROM (SELECT account_id, name, id,"
    " (SELECT p.slug FROM page_versions AS p WHERE p.project_id = later.id"
    f" AND {_CITES_OUTSIDE_ITS_PROJECT} ORDER BY p.slug LIMIT 1) AS slug"
    " FROM (SELECT account_id, name, id FROM projects"
    " WHERE account_id = ?2 AND name > ?3 AND (?1 IS NULL OR account_id = ?1)"
    " UNION ALL SELECT account_id, name, id FROM projects"
    " WHERE account_id > ?2 AND (?1 IS NULL OR account_id = ?1)"
    " ORDER BY 1, 2 LIMIT -1) AS later LIMIT -1) WHERE slug IS NOT NULL LIMIT 1"
)
# The next such page of the project ?1 after the slug ?2: the search goes on from there, by
# the primary key, so the project's versions are read once in all.
_NEXT_PAGE = (
    "SELECT p.slug FROM page_versions AS p WHERE p.project_id = ?1 AND p.slug > ?2"
    f" AND {_CITES_OUTSIDE_ITS_PROJECT} ORDER BY p.slug LIMIT 1"
)


def hold_to_own_projects(
    con: sqlite3.Connection,
    account_id: str | None,
    stamp: str,
    each: Callable[[str, int, int], None] | None = None,
) -> dict[str, int]:
    """Hold every page to its own project's notes, on `con`, in the caller's transaction:
    each page any version of which cites a passage that no note of the page's own project
    holds (one of a note of another project of its workspace, or of another workspace's, or
    one the store no longer holds: what an import refuses) is rewritten as a forget rewrites
    a page (_plan_page, _rewrite), with those passages going from that page alone and
    nothing else: one new version, numbered one past its latest, of the claims its own
    project's notes hold up, every earlier version deleted (they held the other project's
    text), its search rows rewritten, its project given the row PASSAGES_REMOVED; a page
    left with no claim is removed. The other project's notes are not touched. Scope: the
    workspace `account_id`, or the whole database (None: schema step 32 runs it so, once).
    An earlier build's own writers could leave such a page (its compile saved what it had
    gated, and its save asked only whether the cited passages existed anywhere), as could a
    program writing the tables; a database whose pages all cite their own project's notes
    has none, and this makes one search and writes nothing. A passage no longer held is one
    whose note another workspace forgot after the page cited it (a forget searches its own
    workspace alone).

    The pages are found one at a time, by workspace, project name and slug, in one pass over
    the page versions however many pages it repairs: each search goes on from the last page
    found, by the projects' unique index and the versions' primary key (_NEXT_PROJECT,
    _NEXT_PAGE), so each version is read by the search once, and it holds one page and one
    workspace's counts whatever the database holds (schema step 32, its caller, holds
    besides one log line for each workspace that had any until its transaction commits); on
    a database that holds no such page it makes one search and writes nothing. Each page it
    removes also has its name taken out of the audit events that named it (_unname), one
    pass over the workspace's audit events for each page removed, so that part grows with
    the events (64 pages removed: 5,579 units of 100 SQLite steps beside 512 other events,
    20,324 beside 2,048, as the second reviewer measured). It writes no audit event: inside
    the upgrade no audit writer of this build may run (a step is history). It says nothing
    itself: `each`, when given, is called, in the transaction, with each workspace that had
    any (its id and its counts rewritten and removed), and the caller says them once the
    transaction has committed (schema step 32 through the upgrade's runner, one line of the
    store's log each, _log_repaired; the `repair` command on stdout). Returns how many
    `workspaces` had any, and how many pages were `rewritten` and `removed`."""
    said = _each(each or (lambda *counts: None))
    done: Counter = Counter()
    tally: Counter = Counter()  # the pages of the workspace `at`, the one being repaired
    at, after = None, ("", "")
    while found := con.execute(_NEXT_PROJECT, (account_id, *after)).fetchone():
        account, name, project_id, slug = found
        after = (account, name)
        if account != at:
            said(at, tally)
            done["workspaces"] += 1
            at, tally = account, Counter()
        while slug is not None:
            gone = frozenset(  # the passages its versions cite that are not of its project's
                span_id
                for (span_id,) in con.execute(
                    "SELECT DISTINCT cited.value FROM page_versions AS p,"
                    " json_each(p.claims) AS claim, json_each(claim.value, '$.span_ids') AS cited"
                    f" WHERE p.project_id = ? AND p.slug = ? AND {_NOT_OF_ITS_PROJECT}",
                    (project_id, slug),
                )
            )
            planned = _plan_page(con, project_id, name, slug, gone)
            _rewrite(con, account, _index_name(account), [planned], stamp, None)
            what = "rewritten" if planned[5] else "removed"
            tally[what] += 1
            done[what] += 1
            (slug,) = con.execute(_NEXT_PAGE, (project_id, slug)).fetchone() or (None,)
    said(at, tally)
    return {what: done[what] for what in ("workspaces", "rewritten", "removed")}


def _each(each: Callable[[str, int, int], None]) -> Callable[[str | None, Counter], None]:
    """hold_to_own_projects' `each`, called with a workspace's tally (None: none yet)."""

    def said(account_id: str | None, tally: Counter) -> None:
        if account_id is not None:
            each(account_id, tally["rewritten"], tally["removed"])

    return said


def _log_repaired(account_id: str, rewritten: int, removed: int) -> None:
    """The store's line for a workspace hold_to_own_projects repaired, said once the
    transaction that repaired it has committed (schema step 32)."""
    store_log.warning(
        "repair: workspace %s held %d page(s) citing notes of other projects: %d rewritten"
        " with its own notes' claims, %d removed",
        account_id,
        rewritten + removed,
        rewritten,
        removed,
    )


def count_pages_citing_other_projects(con: sqlite3.Connection) -> dict[str, int]:
    """By workspace id, how many pages any version of which cites a passage that no note of
    the page's own project holds (another project's, or one no longer held):
    hold_to_own_projects' search, counted, writing nothing (the `stats --other-projects`
    command). One statement, over every page version."""
    return dict(
        con.execute(
            "SELECT account_id, COUNT(*) FROM (SELECT DISTINCT projects.account_id,"
            " p.project_id, p.slug FROM page_versions AS p"
            f" JOIN projects ON projects.id = p.project_id WHERE {_CITES_OUTSIDE_ITS_PROJECT})"
            " GROUP BY account_id ORDER BY account_id"
        ).fetchall()
    )


def _log_left(plan: list[_Planned], project_id: str, what: str = "forget") -> None:
    """One line on the store's log for each page the claims an import would refuse left: by
    slug and version in `project_id`, the count alone for a page of another project (the one
    who removes may not reach it) or one removed (its slug is made from a note's title). The
    count is each line's first argument, `what` ("forget" or "delete") part of its text."""
    for at, _, slug, version, _, kept, _, refused, _ in plan:
        if refused and at != project_id:  # a page the one who forgets may not reach
            store_log.warning(
                what + ": %d claim(s) that an import would refuse left a page of another project",
                refused,
            )
        elif refused and kept:
            store_log.warning(
                what + ": %d claim(s) that an import would refuse left page %r at its version %d",
                refused,
                slug,
                version + 1,
            )
        elif refused:
            store_log.warning(
                what + ": %d claim(s) that an import would refuse left a page, which was"
                " removed with them",
                refused,
            )


def _held_spans(
    con: sqlite3.Connection, project_id: str, span_ids: Sequence[str]
) -> dict[str, Span]:
    """The spans among `span_ids` the store still holds of the project's own notes, by id:
    the spans an import of the project reads its pages against."""
    rows = con.execute(
        f"SELECT {_SPAN_COLUMNS} FROM spans WHERE id IN (SELECT value FROM json_each(?))"
        " AND note_id IN (SELECT id FROM notes WHERE project_id = ?)",
        (json.dumps(list(dict.fromkeys(span_ids))), project_id),
    ).fetchall()
    return {row[0]: _span(row) for row in rows}


def _unname(
    con: sqlite3.Connection, account_id: str, name: str, project: str, slugs: list[str] | None
) -> None:
    """The audit events of the project named `name`, moved to `project`, without the keys of
    NOTE_DERIVED: every event of the project (`slugs` None), or those of the pages `slugs`."""
    paths = ", ".join(f"'$.{key}'" for key in NOTE_DERIVED)
    which = (
        ""
        if slugs is None
        else " AND json_extract(detail, '$.page') IN (SELECT value FROM json_each(?))"
    )
    con.execute(
        f"UPDATE audit_events SET project = ?, detail = json_remove(detail, {paths})"
        f" WHERE account_id = ? AND project = ?{which}",
        (project, account_id, name, *(() if slugs is None else (json.dumps(slugs),))),
    )


def _unname_sources(con: sqlite3.Connection, account_id: str, name: str, project: str) -> None:
    """In the `projects` of every source event, the project named `name` (ignoring case) moved
    to `project` (DELETED_PROJECT): a project made later under that name shows none of them."""
    con.execute(
        "UPDATE audit_events SET detail = json_set(detail, '$.projects', json((SELECT"
        " json_group_array(CASE WHEN value = ?1 COLLATE NOCASE THEN ?2 ELSE value END)"
        " FROM json_each(audit_events.detail, '$.projects'))))"
        " WHERE account_id = ?3 AND action LIKE 'source.%' AND EXISTS (SELECT 1 FROM"
        " json_each(audit_events.detail, '$.projects') WHERE value = ?1 COLLATE NOCASE)",
        (name, project, account_id),
    )


def _compaction_due(con: sqlite3.Connection, now: str) -> None:
    """Mark a compaction due (cairn.store.portable.compaction_state): its time unless one
    already is, and in every case a generation of this deletion's own as the record's
    `updated_at` (base.deletion_generation), so a rewrite that ran or began before it is not
    taken to cover it. What was removed can leave short index keys behind until a rewrite
    runs."""
    con.execute(RECORD_DELETION, (DEFAULT_ACCOUNT_ID, COMPACT_DUE, now, deletion_generation()))
