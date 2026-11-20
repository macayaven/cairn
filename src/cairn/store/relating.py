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

What is given is bounded too, and a claim says when it is not whole (`Stated`). A claim's
text is read to relations.TEXT_MAX characters and no further, by a handle on the value the
index stores (`_text`), so of a claim of any length no more is handed back than of a short
one; a longer one is given cut. Beside a claim cut that states a shared term goes the line
of its first passage that holds the term (relations.line_of), from the passage as this
read already holds it and from where the term's fact stands in it: nothing is read for it,
and the text is not searched a second time. Beside a claim cut that mentions the page
goes the line of its own text that names the title (`_named`): the index is asked where it
matched, answers by its own marks around the match, and hands back that place with the
part of the text a line can come from, 3 * LINE_MAX + 1 characters at most. Nothing here
looks for the title. To mark the match the index reads the claim whole itself, inside its
statement: one claim for each page that mentions the page and is given, so relations.COMMON
claims at most, each as long as a page's claims may be (cairn.store.PAGE_CHARS_MAX
characters).

Statements, T being the terms looked up, beside the four of the caller (the page's head, the
index's table, the notes of the passages the claims given cite, and who wrote them): 2 for
the page's claims and their passages; ceil(T / AT_ONCE) to list the claims that hold each
term's words; 3 for the candidates (their rows, the rows of their passages, the passages); 1
for the title; 2 for what is given (the claims, the pages' titles and versions; each claim's
text is then read by a handle, which is no statement), and 1 more to ask the index where a
claim given cut names the title. A statement with nothing to ask is not run. At most 2 + 4
+ 3 + 1 + 3 = 13, and 17 with the caller's.
Rows: two for each claim of the page; for each term at most CLAIMS_COMMON + 1, and as many
for the title; 3 for each candidate claim read, at most CLAIMS_MAX; and what is given, at
most relations.RELATED_MAX pages of WHY_MAX reasons of two claims. (The caller's read of who
wrote the claims given is a row for each passage they cite: one for a claim as it is
compiled, and as many as a tidy folded into it, as a search reads them for a hit.)
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
from collections.abc import Iterable, Mapping, Sequence
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
# text (`_named`): characters Unicode keeps for a program's own use and out of every text
# that is exchanged, so a claim holds neither unless someone put it there.
MARKS = ("\ufdd0", "\ufdd1")


class Stated(NamedTuple):
    """A claim of a page's latest version, as the index holds it and as a reason gives it:
    its text whole up to relations.TEXT_MAX characters, else its first TEXT_MAX and `cut`.
    For a claim cut that states a shared term, `line` is the line of its first passage that
    holds the term, where the term's fact stands (relations.line_of); for one that mentions
    the page, the line of its own text that names the title, where the index matched it
    (`_named`). `line_cut` says that line is given in part. A claim that is not cut has no
    line. An answer gives a claim cut by its line and leaves its first characters out; it
    gives them only when there is no line (cairn.memory)."""

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
    texts: dict[str, str] = {}  # each passage read, whole: a claim cut is given a line of it
    held = _terms(_passages(con, project_id, [cited[0] for cited in own.values() if cited]), texts)
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
    held |= _terms(passages, texts)
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

    named = _title(title)
    for at, row in _mentioning(con, *scope, slug, named).items():
        reasons.setdefault(at, []).append(Reason(MENTIONED_BY, title, TITLE, 2, None, row))

    given, pages_left_out = relations.best(reasons, limit)
    pages = _given(con, *scope, given, held, texts, named) if given else ()
    wanted = {row for _, others in asked.values() for row, _ in others}
    left = Left(
        terms_looked_up=len(looked),
        terms_not_looked_up=len(known) - len(looked),
        claims_read=len(wanted & read),
        claims_not_read=len(wanted - read),
        pages_left_out=pages_left_out,
    )
    return Related(slug, title, version, pages, left, {}, {})


def _title(title: str) -> str | None:
    """What the index is asked for a page's title: its words side by side in a claim's text.
    None for a title of no word, or of more words than a search takes (ranking.split)."""
    counted, more_words = ranking.split(title)
    return None if more_words else ranking.phrase(counted)


def _mentioning(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    slug: str,
    named: str | None,
) -> dict[str, int]:
    """Each other page a claim of which holds the title as a phrase (`named`: `_title`): its
    first such claim, by row. None for a title that is not looked up; and none for a title
    that is common: more than relations.CLAIMS_COMMON claims of other pages hold it, or more
    than relations.COMMON other pages. One statement at most."""
    if named is None:
        return {}
    (rows,) = _listed(con, account_id, index, project_id, [named], but=slug)
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
    held: Mapping[str, Mapping[Term, tuple[int, int]]],
    texts: Mapping[str, str],
    named: str | None,
) -> tuple[RelatedPage, ...]:
    """The pages given, each with its title, its version and the claims of its reasons, read
    by row: two statements, and one more when a claim that mentions the page is cut. A
    claim's text is read to relations.TEXT_MAX characters and no further (`_text`). A claim
    cut that states a term is given the line of its passage that holds it, from `held`,
    where each term stands in each passage read, and `texts`; one that mentions the page,
    the line of its own text that names the title (`_named`, asked what `named` matched)."""
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

    mentions = {
        reason.there
        for _, why, _ in given
        for reason in why
        if reason.kind == MENTIONED_BY and shown[reason.there].cut
    }
    naming = _named(con, index, named, sorted(mentions)) if named and mentions else {}

    def side(reason: Reason, row: int | None) -> Stated | None:
        stated = None if row is None else shown[row]
        if stated is None or not stated.cut:
            return stated
        if reason.kind == SHARES:
            passage = stated.span_ids[0]  # the passage the claim states the term by: its first
            place = held[passage][Term(reason.term_kind, reason.term)]
            line, line_cut = relations.line_of(texts[passage], *place)
        elif row in naming:
            line, line_cut = naming[row]
        else:  # the index could not say where: `_named`
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


def _named(
    con: sqlite3.Connection, index: str, named: str, rows: Sequence[int]
) -> dict[int, tuple[str, bool]]:
    """For each of these claims, which mention the page and are given cut: the line of the
    claim's text that names the title, and whether that line is cut (relations.line_of).

    Where the title is named is the index's own answer, and nothing here looks for it: the
    index is asked again for `named`, the match that listed the claim, restricted to the
    text column as it was, on these rows alone, and its highlight() gives each claim's text
    with MARKS around what the match held. The place of the first pair of marks is the place
    of the first match, in characters; it is found in the statement, which hands back that
    place and no more of the claim than line_of reads: LINE_MAX characters before the match
    and after it, of the text as the index stores it. So a claim of any length is answered
    with 3 * LINE_MAX + 1 characters at most. The index itself reads the claim whole, and
    marks it once a row: the inner SELECT is kept apart by its LIMIT, so highlight() is not
    run again for each use of its marks (tests/unit/test_relating_cost_unit.py reads the
    statement's plan).

    A claim whose own text holds one of the MARKS has no line here: the first mark would not
    say where the match is. Nor has one that holds a null character, where SQLite's reading
    of a text stops: its place and the part handed back would be wrong (its bytes are asked
    for the null, which the text's own functions do not see). Such a claim stays as it was
    read, cut and saying so. One statement."""
    before = relations.LINE_MAX
    found = {}
    for row, start, until, part, own in con.execute(
        "SELECT placed.row, placed.start - max(1, placed.start - ?3),"
        " placed.until - max(1, placed.start - ?3),"
        " substr(held.text, max(1, placed.start - ?3),"
        " placed.until + ?3 - max(1, placed.start - ?3)),"
        " instr(held.text, ?4) + instr(held.text, ?5) + instr(CAST(held.text AS BLOB), X'00')"
        " FROM (SELECT row, instr(marked, ?4) AS start,"
        " min(instr(marked, ?5) - 1, instr(marked, ?4) + ?3 + 1) AS until"
        f" FROM (SELECT {index}.rowid AS row, highlight({index}, 5, ?4, ?5) AS marked"
        f" FROM json_each(?1) AS wanted CROSS JOIN {index} ON {index}.rowid = wanted.value"
        f" WHERE {index} MATCH ?2 LIMIT -1)) AS placed"
        f" CROSS JOIN {index} AS held ON held.rowid = placed.row",
        (json.dumps(list(rows)), named, before, *MARKS),
    ):
        if not own:
            found[row] = relations.line_of(part, start, until)
    return found


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
    passages: Iterable[Span], texts: dict[str, str]
) -> dict[str, dict[Term, tuple[int, int]]]:
    """Each passage read: the terms it holds and where each stands in it (relations.placed, on
    the passage whole). Its text is kept in `texts`."""
    held = {}
    for span in passages:
        texts[span.id] = span.text
        held[span.id] = relations.placed(span)
    return held


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
