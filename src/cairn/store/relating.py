"""Related pages, read: the pages of a project related to one of them, each relation with the
claims it rests on (the rules are cairn.relations). Nothing of a relation is stored: `related`
reads the rows the pages are made of, on the connection of its caller's one read
(Store.related_pages), so every statement sees the project at one moment.

What it reads, all of one project, the caller's, which its caller found in reach in the same
read:
- A page's claims are its rows in the account's search index: its latest version's claims,
  replaced in the transaction that saves a version (ranking.index_page). The terms of a claim
  are those of the first passage it cites, when that is a passage of the project
  (`relations.terms_of`: cairn.facts.extract_facts on the passage, whole, never on the claim's
  text); a claim whose first passage is not, or that cites none, states none. A claim is the
  whole of each passage it cites, so the first stands for the others.
- A claim states a term when the index finds the term's words, side by side, in the claim's
  text, and its passage holds the term. The words are those of the term as it is compared
  (`ranking.phrase` of `ranking.words`; the first of them for a term of more words than a
  search takes), so they are the same whichever page asks. The index only proposes a claim:
  the flag `--audit-keep` and the words "audit keep" in a sentence are the same words to it.
  The passage decides. A page holds a term when one of its claims states it, and that is
  asked the same way of the page asked about and of every other, so two pages are related by
  a term from either side or from neither.
- A mention: a claim of another page whose text holds this page's title as a phrase. There
  the index's match is the verdict (so a title of one word is found inside `--audit-keep`
  too, as a search finds it). The index's `title` column, which every row of a page holds,
  is never matched, and a page's own claims do not mention it.

Row to project before any claim is read (as ranking.search): a match is kept only when
`search_claims`, by primary key, puts its row in the project, and the statements that list
matches read no column of a claim. A claim of another project is never read, whatever
matches, and nothing out of the project changes an answer.

Bounded work, whoever asks, and nothing left out silently (`Left`, which every answer
carries):
- The page's own claims and their first passages are read whole, as `page` reads them: a
  page holds at most cairn.store.PAGE_CLAIMS_MAX claims.
- At most TERMS_MAX of its terms are looked up, the shortest first
  (relations.looked_up_before). The others are counted (`terms_left_out`), with any term
  whose first word alone is more than a search takes.
- For each term, the first HERE_MAX of the page's claims that hold it by their passage are
  asked of the index, and the first LISTED_MAX claims of the project's other pages that hold
  its words are listed, by row. The claims listed are then read, the terms with the fewest
  first, a term's claims all or none, until CLAIMS_MAX claims are read; and their passages,
  in that order, until CHARS_MAX characters are (each is measured in SQL first: one that
  does not fit is not fetched). A term whose pages are not all known is counted
  (`terms_cut`): more claims hold its words than were listed, a claim listed or its passage
  was not read, or none of the page's first HERE_MAX claims is found and it has more. Not so
  when more than relations.COMMON pages are already known to hold it, which settles it. What
  was seen of a term that is cut still relates pages, and a reason's `pages` is how many
  pages hold the term as far as the read saw.
- The first LISTED_MAX claims that hold the title are listed; when more hold it, or the
  title has more words than a search takes (ranking.split) and is not looked up,
  `title_cut` says so.
- The related pages past the `limit` asked for are counted (`pages_left_out`).
`complete` is true only when all of these are nothing.

Statements, T being the terms looked up, beside the three of the caller (the page's head, the
index's table, and who wrote the notes of the claims given): 2 for the page's claims and
their passages; ceil(T / AT_ONCE) to ask the index of the page's own claims and at most as
many to list the other pages'; 3 for the candidates (their rows, the lengths of their
passages, the passages); 1 for the title; 3 for what is given (the claims, the pages' titles
and versions, the notes of the passages the claims cite). A statement with nothing to ask is
not run. At most 2 + 4 + 4 + 3 + 1 + 3 = 17, and 20 with the caller's. Rows: two for each
claim of the page; for each term 1 and at most LISTED_MAX + 1, and LISTED_MAX + 1 for the
title; 3 for each candidate claim read, at most CLAIMS_MAX; and what is given, at most
relations.RELATED_MAX pages of WHY_MAX reasons of two claims.
tests/unit/test_relating_cost_unit.py counts statements, rows and SQLite's steps for pages of
10, 200 and 3,000 terms. As for a search (ranking), the matches walked to list a term's
claims are the account's: a match out of the project is walked and passed over.

Limits. A term is found by its words as it is compared: `1,250x` is compared as `1250x`,
which the index reads as another word, so a claim that writes the separator states no term
the index finds; and so for a letter that is compared as other letters (the sharp s). Such
a term relates nothing, from either side.
"""

import json
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple

from cairn import relations
from cairn.relations import COMMON, MENTIONED_BY, SHARES, TITLE, Reason, Term
from cairn.spans import Span
from cairn.store import ranking

TERMS_MAX = 256  # terms of a page that are looked up; it counts the rest
HERE_MAX = 16  # the page's claims that state a term by their passage and are asked of the index
LISTED_MAX = 64  # claims of other pages listed for one term, and for the title
CLAIMS_MAX = 512  # candidate claims read in all
CHARS_MAX = 1_000_000  # characters of the candidates' passages read in all
AT_ONCE = 64  # terms one statement asks the index for


class Stated(NamedTuple):
    """A claim of a page's latest version, as the index holds it."""

    claim_id: str
    text: str
    span_ids: tuple[str, ...]


class RelatedPage(NamedTuple):
    slug: str
    title: str
    version: int
    why: tuple[Reason, ...]  # at most relations.WHY_MAX, the weightiest first
    more: int  # its further reasons, not given


class Left(NamedTuple):
    """What a read left out (the module's bounds). Counts and flags only."""

    terms: int  # terms of the page looked up
    terms_left_out: int  # its other terms: past TERMS_MAX, or of no word a search keeps
    terms_cut: int  # terms looked up whose pages are not all known
    title_cut: bool  # more claims hold the title than were listed, or it was not looked up
    claims_read: int  # candidate claims read
    pages_left_out: int  # related pages past the limit

    @property
    def complete(self) -> bool:
        return not (self.terms_left_out or self.terms_cut or self.title_cut or self.pages_left_out)


class Related(NamedTuple):
    """The pages related to one page, best first, and what the read left out. `notes_of` is
    the note of each passage a claim given cites (a passage the project no longer holds is
    not in it), and `origins` who wrote those notes, which the store's read adds."""

    slug: str
    title: str
    version: int
    pages: tuple[RelatedPage, ...]
    left: Left
    notes_of: dict[str, str]
    origins: dict[str, Any]


Cited = dict[int, tuple[str, ...]]  # a claim's row in the index: the passages it cites
# Each statement below reads the rows it was asked for and no other: CROSS JOIN keeps SQLite
# to the order written (the ids asked for, then each row by its primary key), where its
# planner would otherwise walk every claim or passage of the project to find them.
# A passage of the project, by its id among those asked for.
_PASSAGES = (
    "FROM json_each(?1) AS wanted CROSS JOIN spans ON spans.id = wanted.value"
    " CROSS JOIN notes ON notes.id = spans.note_id WHERE notes.project_id = ?2"
)
# A claim by its row, kept only when search_claims puts the row in the project.
_BY_ROW = (
    "FROM json_each(?1) AS wanted CROSS JOIN search_claims AS c ON c.account_id = ?2"
    " AND c.row = wanted.value CROSS JOIN {index} ON {index}.rowid = c.row"
    " WHERE c.project_id = ?3"
)


def related(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    slug: str,
    title: str,
    version: int,
    limit: int,
) -> Related:
    """The pages related to the page `slug` of the project, which holds it at `version` under
    `title`, read on `con` (the module's docstring). `origins` is left empty."""
    scope = (account_id, index, project_id)
    own: Cited = {
        row: tuple(json.loads(span_ids))
        for row, span_ids in con.execute(
            f"SELECT c.row, {index}.span_ids FROM search_claims AS c"
            f" CROSS JOIN {index} ON {index}.rowid = c.row"
            " WHERE c.account_id = ? AND c.project_id = ? AND c.slug = ? ORDER BY c.row",
            (account_id, project_id, slug),
        )
    }
    held = _terms(_passages(con, project_id, [cited[0] for cited in own.values() if cited]))
    stating: dict[Term, list[int]] = {}  # each term of the page: its claims whose passage holds it
    for row, cited in own.items():
        for term in held.get(cited[0], ()) if cited else ():
            stating.setdefault(term, []).append(row)
    matches = {term: ranking.phrase(ranking.words(term.text)) for term in stating}
    known = sorted((term for term in stating if matches[term]), key=relations.looked_up_before)
    looked = known[:TERMS_MAX]
    asked = [(matches[term], stating[term][:HERE_MAX]) for term in looked]
    here = dict(zip(looked, _here(con, index, asked), strict=True))
    # a term none of whose first claims the index finds, when the page has more that state it
    cut = sum(1 for term in looked if here[term] is None and len(stating[term]) > HERE_MAX)
    stated = [term for term in looked if here[term] is not None]
    listed = dict(
        zip(stated, _listed(con, *scope, slug, [matches[term] for term in stated]), strict=True)
    )
    candidates, read = _candidates(con, *scope, listed)
    passages, unread = _within(
        con, project_id, list(dict.fromkeys(c[0] for c in candidates.values() if c))
    )
    held |= _terms(passages)

    reasons: dict[str, list[Reason]] = {}
    for term in stated:
        rows = listed[term]
        holds: dict[str, int | None] = {}  # each page listed: its first claim that states it
        unknown = len(rows) > LISTED_MAX  # whether a page may hold it that is not known to
        for row, at in rows[:LISTED_MAX]:
            if holds.get(at) is not None:
                continue
            cited = candidates.get(row) if term in read else None
            first = cited[0] if cited else None
            holds[at] = row if first is not None and term in held.get(first, ()) else None
        holding = [at for at, row in holds.items() if row is not None]
        if len(holding) >= COMMON:  # with this page, more than COMMON hold it
            continue
        for row, at in rows[:LISTED_MAX]:  # a page not known to hold it, a claim of it unread
            cited = candidates.get(row)
            if holds[at] is None and (term not in read or (cited and cited[0] in unread)):
                unknown = True
        cut += unknown
        for at in holding:
            reasons.setdefault(at, []).append(
                Reason(SHARES, term.text, term.kind, 1 + len(holding), here[term], holds[at])
            )

    counted, more_words = ranking.split(title)
    mentions = [] if more_words else _mentions(con, *scope, slug, ranking.phrase(counted))
    mentioning: dict[str, int] = {}  # each page that mentions this one: its first such claim
    for row, at in mentions[:LISTED_MAX]:
        mentioning.setdefault(at, row)
    for at, row in mentioning.items():
        reasons.setdefault(at, []).append(Reason(MENTIONED_BY, title, TITLE, 2, None, row))

    given, pages_left_out = relations.best(reasons, limit)
    pages, notes_of = _given(con, *scope, given) if given else ((), {})
    left = Left(
        terms=len(looked),
        terms_left_out=len(stating) - len(looked),
        terms_cut=cut,
        title_cut=bool(more_words) or len(mentions) > LISTED_MAX,
        claims_read=len(candidates),
        pages_left_out=pages_left_out,
    )
    return Related(slug, title, version, pages, left, notes_of, {})


def _given(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    given: Sequence[tuple[str, Sequence[Reason], int]],
) -> tuple[tuple[RelatedPage, ...], dict[str, str]]:
    """The pages given, each with its title, its version and the claims of its reasons, read
    by row, and the note of each passage those claims cite: three statements."""
    rows = {r for _, why, _ in given for reason in why for r in (reason.here, reason.there)}
    shown = {
        row: Stated(claim_id, text, tuple(json.loads(span_ids)))
        for row, claim_id, text, span_ids in con.execute(
            f"SELECT c.row, {index}.claim_id, {index}.text, {index}.span_ids "
            + _BY_ROW.format(index=index),
            (json.dumps(sorted(row for row in rows if row is not None)), account_id, project_id),
        )
    }
    heads = {
        at: (title, version)
        for at, title, version in con.execute(
            "SELECT slug, title, version FROM page_heads WHERE project_id = ?1"
            " AND slug IN (SELECT value FROM json_each(?2))",
            (project_id, json.dumps([at for at, _, _ in given])),
        )
    }
    pages = tuple(
        RelatedPage(
            at,
            *heads[at],
            tuple(
                reason._replace(
                    here=None if reason.here is None else shown[reason.here],
                    there=shown[reason.there],
                )
                for reason in why
            ),
            more,
        )
        for at, why, more in given
    )
    cited = sorted({span_id for claim in shown.values() for span_id in claim.span_ids})
    notes_of = dict(
        con.execute(
            f"SELECT spans.id, spans.note_id {_PASSAGES}", (json.dumps(cited), project_id)
        ).fetchall()
    )
    return pages, notes_of


def _passages(con: sqlite3.Connection, project_id: str, span_ids: Iterable[str]) -> list[Span]:
    """The passages of the project among `span_ids`, whole: one statement, none for no id."""
    asked = list(dict.fromkeys(span_ids))
    if not asked:
        return []
    wanted = json.dumps(asked)
    return [
        Span(
            id=span_id,
            source_id=note_id,
            kind=kind,
            heading_path=tuple(json.loads(heading_path)),
            start_line=start_line,
            end_line=end_line,
            text=text,
        )
        for span_id, note_id, kind, heading_path, start_line, end_line, text in con.execute(
            "SELECT spans.id, spans.note_id, spans.kind, spans.heading_path, spans.start_line,"
            f" spans.end_line, spans.text {_PASSAGES}",
            (wanted, project_id),
        )
    ]


def _terms(passages: Iterable[Span]) -> dict[str, frozenset[Term]]:
    """Each passage read: the terms it holds (relations.terms_of, on the passage whole)."""
    return {span.id: frozenset(relations.terms_of(span)) for span in passages}


def _within(
    con: sqlite3.Connection, project_id: str, span_ids: Sequence[str]
) -> tuple[list[Span], set[str]]:
    """The candidates' passages, in the order given, until CHARS_MAX characters are read, and
    the ids of those of the project left unread. Their lengths are measured first, in SQL,
    so a passage that is not read is not fetched: two statements, none for no passage."""
    if not span_ids:
        return [], set()
    lengths = dict(
        con.execute(
            f"SELECT spans.id, length(spans.text) {_PASSAGES}", (json.dumps(span_ids), project_id)
        ).fetchall()
    )
    taken, room = [], CHARS_MAX
    for span_id in span_ids:
        if span_id in lengths and lengths[span_id] <= room:
            taken.append(span_id)
            room -= lengths[span_id]
    return _passages(con, project_id, taken), set(lengths) - set(taken)


def _here(
    con: sqlite3.Connection, index: str, asked: Sequence[tuple[str | None, Sequence[int]]]
) -> list[int | None]:
    """For each (a term's match, the page's claims whose passage holds the term), the first of
    those claims the index finds the term's words in, or None: one statement for every
    AT_ONCE terms, each reading one row at most and no column of a claim."""
    found: list[int | None] = [None] * len(asked)
    for start in range(0, len(asked), AT_ONCE):
        part = asked[start : start + AT_ONCE]
        each = " UNION ALL ".join(
            f"SELECT * FROM (SELECT ?{1 + 3 * n} AS term, {index}.rowid AS row"
            f" FROM json_each(?{3 + 3 * n}) AS own CROSS JOIN {index} ON {index}.rowid = own.value"
            f" WHERE {index} MATCH ?{2 + 3 * n} ORDER BY {index}.rowid LIMIT 1)"
            for n in range(len(part))
        )
        parameters: list[object] = []
        for n, (match, rows) in enumerate(part):
            parameters += [start + n, match, json.dumps(list(rows))]
        for term, row in con.execute(each, parameters):
            found[term] = row
    return found


def _listed(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    slug: str,
    matches: Sequence[str | None],
) -> list[list[tuple[int, str]]]:
    """For each match, the first LISTED_MAX + 1 claims of the project's other pages that hold
    it, as (row, the page's slug), by row: one statement for every AT_ONCE matches. A row is
    kept only when search_claims puts it in the project, and no column of a claim is read."""
    found: list[list[tuple[int, str]]] = [[] for _ in matches]
    for start in range(0, len(matches), AT_ONCE):
        part = matches[start : start + AT_ONCE]
        each = " UNION ALL ".join(
            f"SELECT * FROM (SELECT ?{5 + 2 * n} AS term, {index}.rowid AS row, c.slug AS slug"
            f" FROM {index} CROSS JOIN search_claims AS c ON c.account_id = ?1"
            f" AND c.row = {index}.rowid"
            f" WHERE {index} MATCH ?{6 + 2 * n} AND c.project_id = ?2 AND c.slug <> ?3"
            f" ORDER BY {index}.rowid LIMIT ?4)"
            for n in range(len(part))
        )
        parameters: list[object] = [account_id, project_id, slug, LISTED_MAX + 1]
        for n, match in enumerate(part):
            parameters += [start + n, match]
        for term, row, at in con.execute(each, parameters):
            found[term].append((row, at))
    return [sorted(rows) for rows in found]


def _mentions(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    slug: str,
    match: str | None,
) -> list[tuple[int, str]]:
    """The first LISTED_MAX + 1 claims of the project's other pages whose text holds the title,
    as (row, the page's slug); none for a title of no word. One statement."""
    if match is None:
        return []
    return _listed(con, account_id, index, project_id, slug, [match])[0]


def _candidates(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    listed: dict[Term, list[tuple[int, str]]],
) -> tuple[Cited, set[Term]]:
    """The claims listed for the terms, read (the passages each cites, never its text): the
    terms with the fewest claims first, a term's claims all or none, until CLAIMS_MAX claims
    are read; and the terms whose claims were read. One statement."""
    rows: dict[int, None] = {}
    read: set[Term] = set()
    for term in sorted(listed, key=lambda term: len(listed[term])):  # stable: as looked up
        more = dict.fromkeys(row for row, _ in listed[term][:LISTED_MAX] if row not in rows)
        if len(rows) + len(more) > CLAIMS_MAX:
            break
        rows |= more
        read.add(term)
    if not rows:
        return {}, read
    claims: Cited = {
        row: tuple(json.loads(span_ids))
        for row, span_ids in con.execute(
            f"SELECT c.row, {index}.span_ids " + _BY_ROW.format(index=index),
            (json.dumps(list(rows)), account_id, project_id),
        )
    }
    return claims, read
