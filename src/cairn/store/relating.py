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
  text, and its passage holds the term. The index is given the term itself, as it is
  compared, as one quoted phrase (`ranking.quoted`): its tokenizer alone reads the words,
  as it read the claims', and nothing here splits them, so there is one reading and it is
  the same whichever page asks. The index only proposes a claim: the flag `--audit-keep`
  and the words "audit keep" in a sentence are the same words to it, and so are `-L` and
  `-l`. The passage decides, exactly: by the term's kind and its text as written, capitals
  included. A term the index has no word of, or does not find in the claim that writes it
  (below, Limits), is stated by no claim.
- For each term of the page, the claims of the whole project that hold its words are listed,
  by row, one more than relations.CLAIMS_COMMON at most: that one more says the term is
  common, and it relates none. Otherwise the list is every claim that could state the term,
  the page's own among them, so what follows is asked the same way of the page asked about
  and of every other, and two pages are related by a term from either side or from neither.
  The page states the term by the first of its own claims in the list whose passage holds
  it; with none, the term relates nothing.
- A mention: a claim of another page that names this page's title in its prose
  (`_mentioning`; the rule is relations.MENTIONED_BY). The index lists the claims whose
  text holds the title as a phrase, as it lists a term's: its `title` column, which every
  row of a page holds, is never matched, a page's own claims do not mention it, and one
  claim more than CLAIMS_COMMON says the title is common and relates none. The index
  only proposes here too: it reads `--audit-keep` as the words audit and keep, and
  `deploy/compose.yaml` as deploy, compose and yaml. So each claim listed is read as a
  term's candidate is, with the first passage it cites, and the index is asked for every
  place it matched the title in the claim's text (`_marked`); the claim names the title
  when one of those places lies outside every stretch of the passage that is code, a path,
  a flag, a version or an address (relations.named_at). A title more than relations.COMMON
  other pages name is common and relates none. The title is given to the index as a term
  is, itself and whole; one longer than relations.TERM_MAX characters, which no title the
  product writes is, is not looked up, and relates none.

Row to project before any claim is read (as ranking.search): a match is kept only when
`search_claims`, by primary key, puts its row in the project, and the statements that list
matches read no column of a claim. A claim of another project is never read, whatever
matches, and nothing out of the project changes an answer.

Bounded work, whoever asks, and nothing left out silently. What can be left out is counted
(`Left`, which every answer carries), and it is these three things and no other:
- The page's terms past the first TERMS_MAX, the shortest first
  (relations.looked_up_before): `terms_not_looked_up`. Every term of the page that relates
  pages by the rule is one or the other: looked up, or counted here. (The page's own
  claims and their first passages are read whole, as `page` reads them: a page holds at
  most cairn.store.PAGE_CLAIMS_MAX claims.)
- The claims of other pages listed for a term, or for the title, and not read:
  `claims_not_read`. The claims listed are read the terms with the fewest first, the title
  as one more of them, a term's claims all or none, until CLAIMS_MAX claims are read; and
  their passages, in that order, until BYTES_MAX bytes are (each is measured first from
  its row, by a handle on the stored value that reads none of it, as cairn.graph measures
  one: a passage that does not fit is neither read nor fetched). The passage of a claim
  listed for the title counts twice: its bytes are handed back once more, as the claim's
  text with the index's marks. A term one of whose claims was not read gives no reason at
  all: it is not known of every page whether it states the term, nor how many do. And so
  for the title. So every reason given says how many pages hold its term exactly, and no
  reason needs a mark of how far it was read.
- The related pages past the `limit` asked for: `pages_left_out`.
`complete` is true only when all three are nothing.

What is given is bounded too, and a claim says when it is not whole (`Stated`). A claim's
text is read to relations.TEXT_MAX characters and no further, by a handle on the value the
index stores (`_text`), so of a claim of any length no more is handed back than of a short
one; a longer one is given cut. Beside a claim cut that states a shared term goes the line
of its first passage that holds the term (relations.line_of), from the passage as this
read already holds it and from where the term's fact stands in it: nothing is read for it,
and the text is not searched a second time. Beside a claim cut that names the title goes
the line that names it: the line of its passage at the first place that counts
(`_mentioning`), from the passage this read holds; nothing here looks for the title. A
claim cut that mentions the page as the index has it (its text is not its passage's, or
holds a mark of the index or a null character) has no place that is known to be prose: it
has no line, and an answer gives its first characters.

Statements, T being the terms looked up, beside the four of the caller (the page's head, the
index's table, the notes of the passages the claims given cite, and who wrote them): 2 for
the page's claims and their passages; ceil(T / AT_ONCE) to list the claims that hold each
term's words; 1 for the title; 3 for the candidates, the title's among them (their rows,
the rows of their passages, the passages); 1 to ask the index where it matched the title in
the claims listed for it; 2 for what is given (the claims, the pages' titles and versions;
each claim's text is then read by a handle, which is no statement). A statement with
nothing to ask is not run. At most 2 + 4 + 1 + 3 + 1 + 2 = 13, and 17 with the caller's.
Rows: two for each claim of the page; for each term at most CLAIMS_COMMON + 1, and as many
for the title; 3 for each candidate claim read, at most CLAIMS_MAX, and one more for each
of the title's, at most CLAIMS_COMMON; and what is given, at
most relations.RELATED_MAX pages of WHY_MAX reasons of two claims. (The caller's read of who
wrote the claims given is a row for each passage they cite: one for a claim as it is
compiled, and as many as a tidy folded into it, as a search reads them for a hit.)
tests/unit/test_relating_cost_unit.py counts statements, rows and SQLite's steps for pages of
10, 200 and 3,000 terms. As for a search (ranking), the matches walked to list a term's
claims are the account's: a match out of the project is walked and passed over.

Limits. A term is found by its words as it is compared: `1,250x` is compared as `1250x`,
which the index reads as another word, so a claim that writes the separator states no term
the index finds. Such a term relates nothing, from either side.
"""

import json
import sqlite3
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from cairn import relations
from cairn.relations import MENTIONED_BY, SHARES, TITLE, Reason, Term
from cairn.spans import Span
from cairn.store import ranking

TERMS_MAX = 256  # terms of a page that are looked up; it counts the rest
CLAIMS_MAX = 512  # claims of other pages read in all; it counts the rest
BYTES_MAX = 1_000_000  # bytes of those claims' passages, as stored, read in all
AT_ONCE = 64  # terms one statement asks the index for
# The two marks the index is asked to put around what it matched of a title in a claim's
# text (`_marked`): characters Unicode keeps for a program's own use and out of every text
# that is exchanged, so a claim holds neither unless someone put it there.
MARKS = ("\ufdd0", "\ufdd1")


class Stated(NamedTuple):
    """A claim of a page's latest version, as the index holds it and as a reason gives it:
    its text whole up to relations.TEXT_MAX characters, else its first TEXT_MAX and `cut`.
    For a claim cut that states a shared term, `line` is the line of its first passage that
    holds the term, where the term's fact stands (relations.line_of); for one that mentions
    the page, the line of its passage at the place that names the title in prose
    (`_mentioning`). `line_cut` says that line is given in part. A claim that is not cut has
    no line, nor has one that mentions the page as the index has it. An answer gives a claim
    cut by its line and leaves its first characters out; it gives them only when there is
    no line (cairn.memory)."""

    claim_id: str
    text: str
    span_ids: tuple[str, ...]
    cut: bool = False
    line: str | None = None
    line_cut: bool = False


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
    spans: dict[str, Span] = {}  # each passage read, whole: a line given is a line of one
    held = _terms(_passages(con, project_id, [cited[0] for cited in own.values() if cited]), spans)
    stating: dict[Term, set[int]] = {}  # each term of the page: its claims whose passage holds it
    for row, cited in own.items():
        for term in held.get(cited[0], ()) if cited else ():
            stating.setdefault(term, set()).add(row)
    known = sorted(stating, key=relations.looked_up_before)  # every term: looked up, or counted
    looked = known[:TERMS_MAX]
    listed = _listed(con, *scope, [ranking.quoted(term.text) for term in looked])

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
    # The title is settled as a term is: the claims of other pages that hold its words are
    # read with the terms' claims, within the same bounds.
    named = _title(title)
    naming = _title_claims(con, *scope, slug, named)
    candidates = _candidates(con, *scope, [*(others for _, others in asked.values()), naming])
    marked = {candidates[row][0] for row, _ in naming if candidates.get(row)}
    passages, unread = _within(
        con, project_id, list(dict.fromkeys(c[0] for c in candidates.values() if c)), marked
    )
    held |= _terms(passages, spans)
    read = {row for row, cited in candidates.items() if not (cited and cited[0] in unread)}

    reasons: dict[str, list[Reason]] = {}
    for term, (here, others) in asked.items():
        if any(row not in read for row, _ in others):  # a claim not read: no reason at all
            continue
        states: dict[str, int] = {}  # each page that holds the term: its first claim that states it
        for row, at in others:
            if at not in states and candidates[row] and term in held.get(candidates[row][0], ()):
                states[at] = row
        if len(states) >= relations.COMMON:  # with this page, more than COMMON hold it
            continue
        shared = Reason(SHARES, term.text, term.kind, 1 + len(states), here, None)
        for at, row in states.items():
            reasons.setdefault(at, []).append(shared._replace(there=row))

    places: dict[int, tuple[int, int]] = {}  # where a claim names the title, when it is known
    for at, (row, place) in _mentioning(con, index, named, naming, candidates, read, spans).items():
        reasons.setdefault(at, []).append(Reason(MENTIONED_BY, title, TITLE, 2, None, row))
        if place is not None:
            places[row] = place

    given, pages_left_out = relations.best(reasons, limit)
    pages = _given(con, *scope, given, held, spans, places) if given else ()
    wanted = {row for _, others in asked.values() for row, _ in others} | {r for r, _ in naming}
    left = Left(
        terms_looked_up=len(looked),
        terms_not_looked_up=len(known) - len(looked),
        claims_read=len(wanted & read),
        claims_not_read=len(wanted - read),
        pages_left_out=pages_left_out,
    )
    return Related(slug, title, version, pages, left, {}, {})


def _title(title: str) -> str | None:
    """What the index is asked for a page's title: the title itself, as one phrase in a
    claim's text (ranking.quoted). None for a title longer than relations.TERM_MAX
    characters, which is not looked up: a phrase asked of the index is no longer than a
    term's, and no title the product writes is."""
    return None if len(title) > relations.TERM_MAX else ranking.quoted(title)


def _title_claims(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    slug: str,
    named: str | None,
) -> Listed:
    """The claims of other pages whose text holds the title as a phrase (`named`: `_title`),
    by row: the index's own match, on the text column. None for a title that is not looked
    up, and none for one more than relations.CLAIMS_COMMON such claims hold, which is common
    and relates none. One statement at most."""
    if named is None:
        return []
    (rows,) = _listed(con, account_id, index, project_id, [named], but=slug)
    return [] if len(rows) > relations.CLAIMS_COMMON else rows


def _mentioning(
    con: sqlite3.Connection,
    index: str,
    named: str | None,
    naming: Listed,
    candidates: Cited,
    read: set[int],
    spans: Mapping[str, Span],
) -> dict[str, tuple[int, tuple[int, int] | None]]:
    """Each other page that names the title in the prose of a claim: its first claim that
    does, by row, and where in the claim's text (relations.named_at).

    A claim that holds the title's words (`naming`) names the title only when a place where
    the index matched them lies outside every stretch of its passage that is code, a path, a
    flag, a version or an address (relations.covered, on the passage read whole). The places
    are the index's (`_marked`), and they are places of the passage because the claim's
    text is the passage's text; the statement says whether it is. Where it is not (a page
    saved by hand), where the claim cites no passage of the project, and where the claim
    holds a mark of the index or a null character, the places of the one say nothing of the
    other: the claim mentions the page as the index has it, and its place is None.

    As for a term: a title one of whose claims was not read gives no mention at all; nor
    does a title more than relations.COMMON other pages name. One statement, none for no
    claim read."""
    asked = [
        (row, candidates[row][0])
        for row, _ in naming
        if row in read and candidates[row] and candidates[row][0] in spans
    ]
    marks = _marked(con, index, named, asked) if named and asked else {}
    if any(row not in read for row, _ in naming):  # a claim not read: no mention at all
        return {}
    names: dict[str, tuple[int, tuple[int, int] | None]] = {}
    for row, at in naming:
        if at in names:
            continue
        if row not in marks:  # the index's word stands
            names[at] = (row, None)
        elif place := relations.named_at(
            _places(marks[row]), relations.covered(spans[candidates[row][0]])
        ):
            names[at] = (row, place)
    return {} if len(names) > relations.COMMON else names


def _marked(
    con: sqlite3.Connection, index: str, named: str, asked: Sequence[tuple[int, str]]
) -> dict[int, str]:
    """For each (claim's row, its first passage's id) of `asked`, a passage this read holds:
    the claim's text with MARKS around every place the index matched `named` in it, the
    match that listed the claim, on the text column (FTS5's highlight()). Only for a claim
    whose text is its passage's text, character for character, and holds neither mark nor a
    null character (SQLite's reading of a text stops there): the statement decides both
    without handing a text back, so a claim reworded from its passage, of any length, costs
    nothing. What is handed back is then the passage's bytes once more, with two marks a
    match; `_within` has counted them. One statement."""
    return {
        row: marks
        for row, marks in con.execute(
            f"SELECT {index}.rowid, CASE WHEN {index}.text = spans.text"
            f" AND instr({index}.text, ?3) + instr({index}.text, ?4)"
            f" + instr(CAST({index}.text AS BLOB), X'00') = 0"
            f" THEN highlight({index}, 5, ?3, ?4) END"
            f" FROM json_each(?1) AS wanted"
            f" CROSS JOIN {index} ON {index}.rowid = json_extract(wanted.value, '$[0]')"
            f" CROSS JOIN spans ON spans.id = json_extract(wanted.value, '$[1]')"
            f" WHERE {index} MATCH ?2",
            (json.dumps([list(pair) for pair in asked]), named, *MARKS),
        )
        if marks is not None
    }


def _places(marks: str) -> list[tuple[int, int]]:
    """Where the index matched, in the characters of the text it marked, in order: MARKS[0]
    stands before each match and MARKS[1] after it, and the text itself holds neither
    (`_marked` asks for no other text). A first mark with no second after it ends the
    reading: it says no place."""
    opened, closed = MARKS
    places, at, before = [], 0, 0  # `before`: the marks that stand before this place
    while (start := marks.find(opened, at)) >= 0 and (end := marks.find(closed, start + 1)) > 0:
        places.append((start - before, end - before - 1))
        at, before = end + 1, before + 2
    return places


def _given(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    given: Sequence[tuple[str, Sequence[Reason], int]],
    held: Mapping[str, Mapping[Term, tuple[int, int]]],
    spans: Mapping[str, Span],
    places: Mapping[int, tuple[int, int]],
) -> tuple[RelatedPage, ...]:
    """The pages given, each with its title, its version and the claims of its reasons, read
    by row: two statements. A claim's text is read to relations.TEXT_MAX characters and no
    further (`_text`). A claim cut that states a term is given the line of its passage that
    holds it, from `held`, where each term stands in each passage read, and `spans`; one
    that names the title, the line of its passage at the place that counts (`places`:
    `_mentioning`). A claim cut that mentions the page as the index has it has no place
    known to be prose, and no line."""
    rows = {r for _, why, _ in given for reason in why for r in (reason.here, reason.there)}
    shown = {}
    for row, claim_id, span_ids in con.execute(
        f"SELECT c.row, {index}.claim_id, {index}.span_ids " + _BY_ROW.format(index=index),
        (json.dumps(sorted(row for row in rows if row is not None)), account_id, project_id),
    ).fetchall():
        text, cut = _text(con, index, row)
        shown[row] = Stated(claim_id, text, tuple(json.loads(span_ids)), cut)
    heads = {
        at: (title, version)
        for at, title, version in con.execute(
            "SELECT slug, title, version FROM page_heads WHERE project_id = ?1"
            " AND slug IN (SELECT value FROM json_each(?2))",
            (project_id, json.dumps([at for at, _, _ in given])),
        )
    }

    def side(reason: Reason, row: int | None) -> Stated | None:
        stated = None if row is None else shown[row]
        if stated is None or not stated.cut:
            return stated
        if reason.kind == SHARES:
            passage = stated.span_ids[0]  # the passage the claim states the term by: its first
            place = held[passage][Term(reason.term_kind, reason.term)]
            line, line_cut = relations.line_of(spans[passage].text, *place)
        elif row in places:  # the claim is its passage's text: the place is a place of both
            line, line_cut = relations.line_of(spans[stated.span_ids[0]].text, *places[row])
        else:  # it mentions the page as the index has it: no place is known to be prose
            return stated
        return stated._replace(line=line, line_cut=line_cut)

    return tuple(
        RelatedPage(
            at,
            *heads[at],
            tuple(
                reason._replace(here=side(reason, reason.here), there=side(reason, reason.there))
                for reason in why
            ),
            more,
        )
        for at, why, more in given
    )


def _text(con: sqlite3.Connection, index: str, row: int) -> tuple[str, bool]:
    """A claim's text to relations.TEXT_MAX characters, and whether it holds more. The index
    keeps a row's text in its own table of what it was given; a handle on the stored value
    reads the bytes TEXT_MAX + 1 characters can take, 4 each at most, and no more of a claim
    however long it is. The caller has put the row in the project (`_BY_ROW`)."""
    with con.blobopen(f"{index}_content", "c5", row, readonly=True) as stored:
        head = stored.read(4 * (relations.TEXT_MAX + 1))
    text = head.decode("utf-8", errors="ignore")  # a last character read in part is left out
    return text[: relations.TEXT_MAX], len(text) > relations.TEXT_MAX


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


def _terms(
    passages: Iterable[Span], spans: dict[str, Span]
) -> dict[str, dict[Term, tuple[int, int]]]:
    """Each passage read: the terms it holds and where each stands in it (relations.placed, on
    the passage whole). The passage is kept in `spans`."""
    held = {}
    for span in passages:
        spans[span.id] = span
        held[span.id] = relations.placed(span)
    return held


def _within(
    con: sqlite3.Connection, project_id: str, span_ids: Sequence[str], twice: Collection[str]
) -> tuple[list[Span], set[str]]:
    """The candidates' passages, in the order given, until BYTES_MAX bytes are read, and the
    ids of those of the project left unread. Each is measured first from its row, by a
    handle on its stored text that reads none of it, so a passage that does not fit is
    neither read nor fetched: two statements, none for no passage. A passage of `twice`,
    whose claim may name the title, counts twice: its bytes come back once more as the
    claim's text with the index's marks (`_marked`)."""
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
        cost = sizes.get(span_id, 0) * (2 if span_id in twice else 1)
        if span_id in sizes and cost <= room:
            taken.append(span_id)
            room -= cost
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
