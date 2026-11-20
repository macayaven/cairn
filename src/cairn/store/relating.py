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
  The passage decides. A term the index has no word of, or does not find in the claim that
  writes it (below, Limits), is stated by no claim.
- For each term of the page, the claims of the whole project that hold its words are listed,
  by row, one more than relations.CLAIMS_COMMON at most: that one more says the term is
  common, and it relates none. Otherwise the list is every claim that could state the term,
  the page's own among them, so what follows is asked the same way of the page asked about
  and of every other, and two pages are related by a term from either side or from neither.
  The page states the term by the first of its own claims in the list whose passage holds
  it; with none, the term relates nothing.
- A mention: a claim of another page whose text holds this page's title as a phrase. There
  the index's match is the verdict (so a title of one word is found inside `--audit-keep`
  too, as a search finds it). The index's `title` column, which every row of a page holds,
  is never matched, and a page's own claims do not mention it. The claims of other pages
  that hold the title are listed the same way: one more than CLAIMS_COMMON, or more than
  relations.COMMON pages among them, and the title is common and relates none. A title of
  more words than a search takes (ranking.split) is not looked up, and relates none.

Row to project before any claim is read (as ranking.search): a match is kept only when
`search_claims`, by primary key, puts its row in the project, and the statements that list
matches read no column of a claim. A claim of another project is never read, whatever
matches, and nothing out of the project changes an answer.

Bounded work, whoever asks, and nothing left out silently. What can be left out is counted
(`Left`, which every answer carries), and it is these three things and no other:
- The page's terms past the first TERMS_MAX, the shortest first
  (relations.looked_up_before): `terms_not_looked_up`. (The page's own claims and their
  first passages are read whole, as `page` reads them: a page holds at most
  cairn.store.PAGE_CLAIMS_MAX claims.)
- The claims of other pages listed for a term and not read: `claims_not_read`. The claims
  listed are read the terms with the fewest first, a term's claims all or none, until
  CLAIMS_MAX claims are read; and their passages, in that order, until BYTES_MAX bytes are
  (each is measured first from its row, by a handle on the stored value that reads none of
  it, as cairn.graph measures one: a passage that does not fit is neither read nor
  fetched). A term is settled when each page listed for it is known to hold it or not: one
  of its claims states it, or all of them were read. A term that is not settled gives no
  reason at all, unless more than COMMON pages are already known to hold it, which settles
  it as common. So every reason given says how many pages hold its term exactly, and no
  reason needs a mark of how far it was read.
- The related pages past the `limit` asked for: `pages_left_out`.
`complete` is true only when all three are nothing.

Statements, T being the terms looked up, beside the four of the caller (the page's head, the
index's table, the notes of the passages the claims given cite, and who wrote them): 2 for
the page's claims and their passages; ceil(T / AT_ONCE) to list the claims that hold each
term's words; 3 for the candidates (their rows, the rows of their passages, the passages); 1
for the title; 2 for what is given (the claims, the pages' titles and versions). A statement
with nothing to ask is not run. At most 2 + 4 + 3 + 1 + 2 = 12, and 16 with the caller's.
Rows: two for each claim of the page; for each term at most CLAIMS_COMMON + 1, and as many
for the title; 3 for each candidate claim read, at most CLAIMS_MAX; and what is given, at
most relations.RELATED_MAX pages of WHY_MAX reasons of two claims.
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
from cairn.relations import MENTIONED_BY, SHARES, TITLE, Reason, Term
from cairn.spans import Span
from cairn.store import ranking

TERMS_MAX = 256  # terms of a page that are looked up; it counts the rest
CLAIMS_MAX = 512  # claims of other pages read in all; it counts the rest
BYTES_MAX = 1_000_000  # bytes of those claims' passages, as stored, read in all
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
    """What a read left out (the module's bounds): counts, and nothing else."""

    terms_looked_up: int  # terms of the page looked up
    terms_not_looked_up: int  # its terms past TERMS_MAX
    claims_read: int  # claims of other pages read for those terms
    claims_not_read: int  # such claims not read: past CLAIMS_MAX claims or BYTES_MAX bytes
    pages_left_out: int  # related pages past the limit

    @property
    def complete(self) -> bool:
        return not (self.terms_not_looked_up or self.claims_not_read or self.pages_left_out)


class Related(NamedTuple):
    """The pages related to one page, best first, and what the read left out. `notes_of` is
    the note of each passage a claim given cites, and `origins` who wrote those notes: the
    store's read adds both, in its section, for the passages the view reaches (a passage it
    does not reach, or that the store no longer holds, names nothing, as for `page`)."""

    slug: str
    title: str
    version: int
    pages: tuple[RelatedPage, ...]
    left: Left
    notes_of: dict[str, str]
    origins: dict[str, Any]


Cited = dict[int, tuple[str, ...]]  # a claim's row in the index: the passages it cites
Listed = list[tuple[int, str]]  # claims that hold some words, by row: (row, its page's slug)
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
    `title`, read on `con` (the module's docstring). `notes_of` and `origins` are left empty."""
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
    stating: dict[Term, set[int]] = {}  # each term of the page: its claims whose passage holds it
    for row, cited in own.items():
        for term in held.get(cited[0], ()) if cited else ():
            stating.setdefault(term, set()).add(row)
    matches = {term: ranking.phrase(ranking.words(term.text)) for term in stating}
    known = sorted((term for term in stating if matches[term]), key=relations.looked_up_before)
    looked = known[:TERMS_MAX]
    listed = _listed(con, *scope, [matches[term] for term in looked])

    # Each term to settle: the page's own claim that states it, and the other pages' claims
    # that hold its words. Not a term more claims hold than make it common, nor one that no
    # claim of the page is found to state, nor one no other page holds a word of.
    asked: dict[Term, tuple[int, Listed]] = {}
    for term, rows in zip(looked, listed, strict=True):
        if len(rows) > relations.CLAIMS_COMMON:
            continue
        here = next((row for row, at in rows if at == slug and row in stating[term]), None)
        others = [(row, at) for row, at in rows if at != slug]
        if here is not None and others:
            asked[term] = (here, others)
    candidates = _candidates(con, *scope, [others for _, others in asked.values()])
    passages, unread = _within(
        con, project_id, list(dict.fromkeys(c[0] for c in candidates.values() if c))
    )
    held |= _terms(passages)
    read = {row for row, cited in candidates.items() if not (cited and cited[0] in unread)}

    reasons: dict[str, list[Reason]] = {}
    for term, (here, others) in asked.items():
        states: dict[str, int] = {}  # each page that holds the term: its first claim that states it
        unsettled: set[str] = set()  # pages with a claim that was not read
        for row, at in others:
            if row not in read:
                unsettled.add(at)
            elif at not in states and candidates[row] and term in held.get(candidates[row][0], ()):
                states[at] = row
        if len(states) >= relations.COMMON:  # with this page, more than COMMON hold it
            continue
        if unsettled - states.keys():  # a page that may hold it or not: the term gives no reason
            continue
        shared = Reason(SHARES, term.text, term.kind, 1 + len(states), here, None)
        for at, row in states.items():
            reasons.setdefault(at, []).append(shared._replace(there=row))

    for at, row in _mentioning(con, *scope, slug, title).items():
        reasons.setdefault(at, []).append(Reason(MENTIONED_BY, title, TITLE, 2, None, row))

    given, pages_left_out = relations.best(reasons, limit)
    pages = _given(con, *scope, given) if given else ()
    wanted = {row for _, others in asked.values() for row, _ in others}
    left = Left(
        terms_looked_up=len(looked),
        terms_not_looked_up=len(known) - len(looked),
        claims_read=len(wanted & read),
        claims_not_read=len(wanted - read),
        pages_left_out=pages_left_out,
    )
    return Related(slug, title, version, pages, left, {}, {})


def _mentioning(
    con: sqlite3.Connection, account_id: str, index: str, project_id: str, slug: str, title: str
) -> dict[str, int]:
    """Each other page a claim of which holds the title as a phrase: its first such claim, by
    row. None for a title of no word, or of more words than a search takes; and none for a
    title that is common: more than relations.CLAIMS_COMMON claims of other pages hold it, or
    more than relations.COMMON other pages. One statement at most."""
    counted, more_words = ranking.split(title)
    match = None if more_words else ranking.phrase(counted)
    if match is None:
        return {}
    (rows,) = _listed(con, account_id, index, project_id, [match], but=slug)
    naming: dict[str, int] = {}
    for row, at in rows:
        naming.setdefault(at, row)
    if len(rows) > relations.CLAIMS_COMMON or len(naming) > relations.COMMON:
        return {}
    return naming


def _given(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    given: Sequence[tuple[str, Sequence[Reason], int]],
) -> tuple[RelatedPage, ...]:
    """The pages given, each with its title, its version and the claims of its reasons, read
    by row: two statements."""
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
    return tuple(
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
    """The candidates' passages, in the order given, until BYTES_MAX bytes are read, and the
    ids of those of the project left unread. Each is measured first from its row, by a
    handle on its stored text that reads none of it, so a passage that does not fit is
    neither read nor fetched: two statements, none for no passage."""
    if not span_ids:
        return [], set()
    sizes = {}
    for span_id, row in con.execute(
        f"SELECT spans.id, spans.rowid {_PASSAGES}", (json.dumps(span_ids), project_id)
    ).fetchall():
        with con.blobopen("spans", "text", row, readonly=True) as stored:
            sizes[span_id] = len(stored)
    taken, room = [], BYTES_MAX
    for span_id in span_ids:
        if span_id in sizes and sizes[span_id] <= room:
            taken.append(span_id)
            room -= sizes[span_id]
    return _passages(con, project_id, taken), set(sizes) - set(taken)


def _listed(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    matches: Sequence[str],
    but: str | None = None,
) -> list[Listed]:
    """For each match, the first relations.CLAIMS_COMMON + 1 claims of the project that hold
    it (of its pages other than `but`, when given), as (row, the page's slug), by row: one
    statement for every AT_ONCE matches. A row is kept only when search_claims puts it in the
    project, and no column of a claim is read."""
    found: list[Listed] = [[] for _ in matches]
    for start in range(0, len(matches), AT_ONCE):
        part = matches[start : start + AT_ONCE]
        each = " UNION ALL ".join(
            f"SELECT * FROM (SELECT ?{5 + 2 * n} AS term, {index}.rowid AS row, c.slug AS slug"
            f" FROM {index} CROSS JOIN search_claims AS c ON c.account_id = ?1"
            f" AND c.row = {index}.rowid"
            f" WHERE {index} MATCH ?{6 + 2 * n} AND c.project_id = ?2"
            f" AND (?3 IS NULL OR c.slug <> ?3) ORDER BY {index}.rowid LIMIT ?4)"
            for n in range(len(part))
        )
        parameters: list[object] = [account_id, project_id, but, relations.CLAIMS_COMMON + 1]
        for n, match in enumerate(part):
            parameters += [start + n, match]
        for term, row, at in con.execute(each, parameters):
            found[term].append((row, at))
    return [sorted(rows) for rows in found]


def _candidates(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    listed: Sequence[Listed],
) -> Cited:
    """The claims listed for the terms, read (the passages each cites, never its text): the
    terms with the fewest claims first, a term's claims all or none, until CLAIMS_MAX claims
    are read. One statement, none for no claim."""
    rows: dict[int, None] = {}
    for others in sorted(listed, key=len):  # stable: among equals, as they were looked up
        more = dict.fromkeys(row for row, _ in others if row not in rows)
        if len(rows) + len(more) > CLAIMS_MAX:
            break
        rows |= more
    if not rows:
        return {}
    return {
        row: tuple(json.loads(span_ids))
        for row, span_ids in con.execute(
            f"SELECT c.row, {index}.span_ids " + _BY_ROW.format(index=index),
            (json.dumps(list(rows)), account_id, project_id),
        )
    }
