"""Erasure of one project or one note (H5b): `delete_project`, `forget_note`, and
`may_erase`, the question the web view asks before it offers either.

Who may: an owner of the workspace, the person who made the project, or a view acting for no
person (the command line, the workspace's own key). Anyone else who reaches the project gets
PermissionError, and so does any connection limited to some projects or to reading
(StoreBase._limited_connection), whoever approved it. Out of reach is checked first, inside
the write, and is exactly unknown.

Both run in one write transaction and cost what they remove, never the whole database: the
rows are found by their project (and a forgotten note's pages by the project's page versions),
the project's search rows by `search_claims`, by primary key. What they delete is zeroed
(`PRAGMA secure_delete`, FTS5's own secure-delete), and each ends with a truncating
checkpoint, as delete_account does, so the text leaves the files at once. Each marks a
compaction due (cairn.store.portable.compaction_state): short index keys, such as a page's
slug or a project's name, can stay in index pages until `compact()`.

Forgetting keeps a SHA-256 digest of the note's text in `forgotten`, so the same text is
never stored in that project again (add_note raises ForgottenNote), and a page save that
cites a span the store no longer holds, in such a project, is CitesForgotten (save_page):
a compile or arrangement that read the page before the note was forgotten never brings it
back. The digests go with their project and their workspace and are never exported.

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
from collections.abc import Mapping, Sequence

from cairn.spans import (
    _PARSER_LINES,
    CLOSERS_MAX,
    Span,
    _covering_fence,
    claim_id,
    fences_reaching,
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
# The keys of an audit event's detail made from a note: a page's slug comes from its title.
# The other keys a project's events carry are counts, kinds, codes, rule ids, a model's name,
# person ids and the project's own name and id (see the store contract's Audit log).
NOTE_DERIVED = ("page",)
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
        loses it (one that named it alone then reaches nothing). The workspace's stored bytes
        go down by its notes' size; the month's count of notes written does not. Recorded as
        project.deleted (its id and name, how many notes and pages). False for a project unknown
        or out of reach; PermissionError for anyone else who may not (see the module)."""
        index = self._index
        with self._write() as con:
            try:
                name, maker_id, _ = self._reached(con, project_id)
            except KeyError:
                return False
            self._refuse_erasure(con, maker_id)
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
            gone = DELETED_PROJECT.format(project_id)
            self._forget_destination(con, name)
            _unname(con, self._account_id, name, gone, None)
            _unname_sources(con, self._account_id, name, gone)
            detail = {"id": project_id, "name": name, "notes": notes, "pages": pages}
            self._audit(con, "project.deleted", project=gone, detail=detail)
            _compaction_due(con, self._stamp())
        self._truncate_log(f"project {project_id}")
        return True

    def forget_note(self, note_id: str) -> Forgotten:
        """Remove one note with its spans (see the module). A page that cited it, in any of
        its versions, loses every version and continues as one new version, numbered one
        past its latest: the latest version's claims without the note (kept_claims), its
        body cairn.compiler.render_body of them, its metrics the provenance counts ("claims",
        "spans_cited"), indexed for search in place of the old; a page left with no claim
        is removed, with its search rows. That version is written here, past every gate, so
        it is held to the import's own check of a version (portable.version_faults): a claim
        an import would refuse leaves it as the note's own claims do, never the forget, and
        one line on the store's log counts those claims by page and version (for a page they
        leave empty, the count alone: a removed page's slug is made from a note's title),
        and the forget's audit event counts them over all its pages (`claims_left`, when
        there are any). `claims_left` is that count and no other: the claims the import's
        check took from the versions the forget wrote, those that left for a fence that ran
        on among them (_plan). The claims kept_claims does not keep, which cited the note
        alone or were not the whole of the passages they still cite, are the forget itself
        and are not in it. The project keeps the note's digest, so the same text is not
        stored there again. The workspace's stored bytes go down by the note's size; the
        month's count of notes written does not. Recorded as note.forgotten (the note's id,
        its project's id, how many pages changed, and `claims_left` when it is not zero);
        nothing of its text, title or source_ref. KeyError for a note unknown or out of
        reach; PermissionError for anyone else who may not (see the module). Needs no room
        under any cap of a plan."""
        index = self._index
        with self._write() as con:
            project_id, text_sha256, size = self._note_in_reach(con, note_id)
            name, maker_id, _ = self._reached(con, project_id)
            self._refuse_erasure(con, maker_id)
            plan = _plan(con, project_id, note_id)
            con.execute("DELETE FROM spans WHERE note_id = ?", (note_id,))
            con.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            con.execute("DELETE FROM waiting WHERE note_id = ?", (note_id,))  # N1
            con.execute("DELETE FROM note_writers WHERE note_id = ?", (note_id,))  # A1
            con.execute("DELETE FROM slack_notes WHERE note_id = ?", (note_id,))  # K1
            for slug, version, title, kept, _, _, body in plan:
                if kept:
                    cited = {i for claim in kept for i in claim.span_ids}
                    metrics = {"claims": len(kept), "spans_cited": len(cited)}
                    page = (
                        project_id,
                        slug,
                        version + 1,
                        title,
                        body,
                        json.dumps([claim.model_dump(mode="json") for claim in kept]),
                        json.dumps(metrics),
                        self._stamp(),
                    )
                    _write_page_version(con, self._account_id, index, page, kept)
                    con.execute(
                        "DELETE FROM page_versions"
                        " WHERE project_id = ? AND slug = ? AND version <= ?",
                        (project_id, slug, version),
                    )
                else:
                    for table in ("page_versions", "page_keys"):  # N1: and its topic
                        con.execute(
                            f"DELETE FROM {table} WHERE project_id = ? AND slug = ?",
                            (project_id, slug),
                        )
                    if _has_table(con, index):  # its rows and its project's counts
                        ranking.index_page(
                            con, self._account_id, index, project_id, slug, title, ()
                        )
            con.execute(
                "INSERT INTO forgotten (project_id, text_sha256) VALUES (?, ?)"
                " ON CONFLICT DO NOTHING",
                (project_id, text_sha256),
            )
            self._unstore(con, size)
            removed = [slug for slug, _, _, kept, *_ in plan if not kept]
            if removed:
                _unname(con, self._account_id, name, name, removed)
            detail = {"note": note_id, "project": project_id, "pages": len(plan)}
            left = sum(refused for _, _, _, _, _, refused, _ in plan)
            if left:  # claims an import would refuse, which left their pages with the note
                detail["claims_left"] = left
            self._audit(con, "note.forgotten", project=name, detail=detail)
            _compaction_due(con, self._stamp())
        self._truncate_log(f"note {note_id}")
        for slug, version, _, kept, _, refused, _ in plan:  # once the forget is committed
            if refused and kept:
                store_log.warning(
                    "forget: %d claim(s) that an import would refuse left page %r at its"
                    " version %d",
                    refused,
                    slug,
                    version + 1,
                )
            elif refused:
                store_log.warning(
                    "forget: %d claim(s) that an import would refuse left a page, which was"
                    " removed with them",
                    refused,
                )
        return _outcome(note_id, plan)

    def forget_preview(self, note_id: str) -> Forgotten:
        """What forget_note would do now, by the same rule, writing nothing: the pages it
        would rebuild and those it would remove. For anyone who reaches the note (whether
        they may forget it is may_erase's question); KeyError for a note unknown or out of
        reach. A read."""
        with self._read() as con:
            project_id, _, _ = self._note_in_reach(con, note_id)
            return _outcome(note_id, _plan(con, project_id, note_id))

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


# A page of a forgotten note's project that cites it in any version: (slug, its latest version,
# its title, the claims it keeps (kept_claims, less those an import would refuse), the spans
# of the project still held that its latest version cites, how many claims an import would
# refuse, the body of the claims it keeps as compiler.render_body writes it).
_Planned = tuple[str, int, str, tuple[Claim, ...], dict[str, Span], int, str]


def _plan(con: sqlite3.Connection, project_id: str, note_id: str) -> list[_Planned]:
    """What forgetting the note does to its project's pages, read on `con`: each page that cites
    one of its spans in any version, by slug, with what its latest version keeps: the claims
    kept_claims keeps, less every claim the import's check of a version names
    (portable.version_faults: the claims it does not name pass together, so it is run once,
    on one render of the body, which is the body the forget writes).

    But for a code fence that only the claims before it open and the body does not close
    (more than spans.CLOSERS_MAX of them, Refusal.FENCE_RUNS_ON). The claim that opens it
    leaves, the first in the body's order, and the rest are rendered and asked again, since
    without it the body is another. That alone is the plain rule, and it costs a render for
    each claim that leaves. So that a note's author cannot make a forget cost that, two
    kinds of claim leave in the same render as the claim named (`_with_it`):

    - In every render, the claims the body writes directly before it that the plain rule
      would name next, one a render: those whose fence, ended until now by the end of its
      list item, would cover the block that follows once the claim named has left. The
      parser is asked of each, on the body's own text (`_before_it`), so this takes no
      claim the plain rule keeps: not one whose fence is closed where it stands, nor one
      whose item would not hold that block, nor any claim before such a one (fifth report,
      must-fix 1).
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

    After that render no claim that can open a fence is written after the claim named, and
    those before it that would have been named are gone: a forget rendered CLOSERS_MAX + 2
    times at most on every page tried, fences being all that was at fault
    (tests/unit/test_generated_forget_fences_unit.py). That number is seen, not proven.
    What holds by construction: a render that names a claim takes that claim, so the loop
    ends; and no render takes more than the two kinds above, however many came before it.
    It reads the project's page versions, never the rest of the database."""
    from cairn.compiler import left_to_run  # the compiler builds on the store

    gone = frozenset(
        span_id for (span_id,) in con.execute("SELECT id FROM spans WHERE note_id = ?", (note_id,))
    )
    slugs = [
        slug
        for (slug,) in con.execute(
            "SELECT DISTINCT slug FROM page_versions AS p WHERE project_id = ? AND EXISTS"
            " (SELECT 1 FROM json_each(p.claims) AS claim,"
            "  json_each(claim.value, '$.span_ids') AS cited"
            "  WHERE cited.value IN (SELECT value FROM json_each(?)))"
            " ORDER BY slug",
            (project_id, json.dumps(sorted(gone))),
        )
    ]
    planned = []
    for slug in slugs:
        version, title, claims_json = con.execute(
            "SELECT version, title, claims FROM page_versions"
            " WHERE project_id = ? AND slug = ? ORDER BY version DESC LIMIT 1",
            (project_id, slug),
        ).fetchone()
        claims = [Claim(**claim) for claim in json.loads(claims_json)]
        cited = [i for claim in claims for i in claim.span_ids if i not in gone]
        spans = _held_spans(con, project_id, cited)
        kept, refused, body, renders = kept_claims(claims, gone, spans), 0, "", 0
        while kept:  # until the claims left are a version an import takes: once, but for a
            # fence that the claims before it open, where each time the first such claim leaves
            # the title the page had may be the forgotten note's own
            title = _first_title(con, [i for claim in kept for i in claim.span_ids]) or slug
            body, run_on = left_to_run(title, kept, spans)
            faults = list(  # each claim is asked alone once: the claims do not change
                version_faults(kept, spans, legacy=True, run_on=run_on, ask_alone=not renders)
            )
            named = {place for place, _ in faults if place}
            if not named:
                break
            renders += 1
            if faults == [(run_on, Refusal.FENCE_RUNS_ON)]:
                named |= _with_it(title, kept, spans, run_on, body, renders > CLOSERS_MAX)
            kept = tuple(claim for place, claim in enumerate(kept, 1) if place not in named)
            refused += len(named)
        planned.append((slug, version, title, kept, spans, refused, body))
    return planned


def _with_it(
    title: str,
    kept: Sequence[Claim],
    spans: Mapping[str, Span],
    run_on: int,
    body: str,
    past: bool,
) -> set[int]:
    """The places (1 for the first) of the claims of `kept` that leave with the claim at
    `run_on`, whose fence `body`, the body titled `title` as just rendered, leaves to run on
    (_plan). The order is the body's (compiler._blocks: the claims with no section first),
    never the stored list's.

    `past`, once a forget is past its ceiling of renders: the claims the body writes after
    that one that can open a fence (`_may_open_a_fence`).

    In every render: the claims the body writes directly before that one that hold in a
    fence the block which follows them once it and those have left (`_before_it`)."""
    from cairn.compiler import _blocks, heading_text, section_headings  # it builds on the store

    blocks = _blocks(title, kept, spans, str, section_headings, heading_text)
    at = next(n for n, (_, claim) in enumerate(blocks) if claim is kept[run_on - 1])
    after = range(at + 1, len(blocks))
    leave = {n for n in after if past and blocks[n][1] and _may_open_a_fence(blocks[n][0])}
    follows = next((n for n in after if n not in leave), None)
    if follows is not None:
        leave |= _before_it(body, blocks, at, _PARSER_LINES.split(blocks[follows][0])[0])
    place = {id(claim): n for n, claim in enumerate(kept, 1)}
    return {place[id(blocks[n][1])] for n in leave}


def _before_it(
    body: str, blocks: Sequence[tuple[str, Claim | None]], at: int, line: str
) -> set[int]:
    """Which blocks directly before the block at `at`, the one `body` leaves to run on, leave
    with it: those the plain loop would name one after another, a render each. `blocks` are
    the body's (compiler._blocks, each claim's text as given), `line` the first line of the
    block that will follow them.

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

    Only claims are asked that hold a backtick or a tilde and after which the body wrote
    nothing: a claim with a closer is closed. And only when the body wrote all its
    CLOSERS_MAX closers: with one to spare the next render would close the fence, not name
    its claim (the claim named was then one that no line closes). Nothing is asked when
    `body` is not these blocks in order."""
    from cairn.compiler import _closed  # the compiler builds on the store

    texts = [text for text, _ in blocks[: at + 1]]
    starts = _placed(body, texts)
    if starts is None:
        return set()
    wrote = [body[starts[n] + len(texts[n]) : starts[n + 1] - 2] for n in range(at)]
    run = at  # the first of the claims asked
    while run and blocks[run - 1][1] and not wrote[run - 1] and _fenced(texts[run - 1]):
        run -= 1
    if run == at:
        return set()
    closers = sum(  # each line the body wrote, less the one a claim alone is closed by
        lines.count("\n") - (_closed(text) != text)
        for text, lines in zip(texts, wrote, strict=False)
        if lines
    )
    if closers != CLOSERS_MAX:
        return set()
    low, high = 0, at - run  # the last `low` leave; the last `high + 1` do not all leave
    ask = high
    while low < high:
        taken = fences_reaching(body[: starts[at - ask]], texts[at - ask : at], line)
        if all(taken):
            low = ask
        else:
            high = ask - taken.index(False) - 1
        guess = next((n for n, yes in enumerate(reversed(taken)) if not yes), ask)
        ask = min(max(guess, low + 1), high) if ask == at - run else (low + high + 1) // 2
    return set(range(at - low, at))


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


def _first_title(con: sqlite3.Connection, span_ids: Sequence[str]) -> str | None:
    """The `topic or title` of the oldest note (in the order notes were stored) that one of
    `span_ids` comes from: what a page made by that note alone is titled
    (cairn.compiler.compile_note). None when the store holds none of them."""
    row = con.execute(
        "SELECT COALESCE(NULLIF(notes.topic, ''), notes.title) FROM spans"
        " JOIN notes ON notes.id = spans.note_id"
        " WHERE spans.id IN (SELECT value FROM json_each(?))"
        " ORDER BY notes.seq LIMIT 1",
        (json.dumps(list(dict.fromkeys(span_ids))),),
    ).fetchone()
    return None if row is None else row[0]


def _outcome(note_id: str, plan: list[_Planned]) -> Forgotten:
    rebuilt = tuple(slug for slug, _, _, kept, *_ in plan if kept)
    removed = tuple(slug for slug, _, _, kept, *_ in plan if not kept)
    return Forgotten(note_id=note_id, rebuilt=rebuilt, removed=removed)


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
