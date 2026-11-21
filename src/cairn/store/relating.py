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
  term's candidate is, with the first passage it cites; one statement says which of them
  are their passage's text (`_judged`), and each place the title stands in such a claim is
  judged by itself. The passage's text, with every stretch that is no prose made characters
  the title does not hold (relations.blanked: what the passage's facts read as an address, a
  path, a flag or a version, relations.covered, and what the Markdown parser reads as code
  in it as the page does, cairn.spans.code_read), is given to an index of the same
  declaration in a database of the read's own, in memory, and the title is asked of it again
  (`_in_prose`): the claim names the title where that index still finds it, whatever
  other place overlaps that one. A title more than relations.COMMON other pages name so is
  common and relates none; CLAIMS_COMMON counts the claims that hold the title's words,
  in prose or not. The title is given to the index as a term is, itself and whole; one
  longer than relations.TERM_MAX characters, which no title the product writes is, is not
  looked up, and relates none.

Row to project before any claim is read (as ranking.search): a match is kept only when
`search_claims`, by primary key, puts its row in the project, and the statements that list
matches read no column of a claim. A claim of another project is never read, whatever
matches, and nothing out of the project changes what an answer says: it can leave an answer
incomplete, which the answer says (below).

Bounded work, whoever asks, and nothing left out silently. What can be left out is counted
(`Left`, which every answer carries), and it is these three things and no other:
- The page's terms past the first TERMS_MAX, the shortest first
  (relations.looked_up_before), and those not looked up past ROWS_MAX rows of the index
  (below), the title then counting as one more: `terms_not_looked_up`. Every term of the
  page that relates pages by the rule is one or the other: looked up, or counted here.
  (The page's own claims and their first passages are read whole, as `page` reads them: a
  page holds at most cairn.store.PAGE_CLAIMS_MAX claims.)
- The claims of other pages listed for a term, or for the title, and not read:
  `claims_not_read`. The claims listed are read the terms with the fewest first, the title
  as one more of them, a term's claims all or none, until CLAIMS_MAX claims are read; and
  their passages, in that order, until BYTES_MAX bytes are (each is measured first from
  its row, by a handle on the stored value that reads none of it, as cairn.graph measures
  one: a passage that does not fit is neither read nor fetched). A passage counts once,
  however many of the claims listed cite it, for the terms and the title alike, and not at
  all when it is one of the page's own, which the read holds whole already: within a read
  its facts are read once (cairn.facts.extract_facts), and kept for that read alone, for
  its terms (relations.placed) and for whether it names the title in prose
  (relations.covered). A term one of whose claims was not read gives no reason at
  all: it is not known of every page whether it states the term, nor how many do. And so
  for the title. So every reason given says how many pages hold its term exactly, and no
  reason needs a mark of how far it was read.
- The related pages past the `limit` asked for: `pages_left_out`.
`complete` is true only when all three are nothing.

BYTES_MAX is set from a table of measurements (beside it): a read's time goes to reading the
facts of passages (cairn.facts.extract_facts, 97% of it), not to the index or to reading the
rows (1% each), at about half a microsecond to 0.7 of one a byte of text dense with flags,
paths and versions; 512 candidates of short passages take 0.03 s, so CLAIMS_MAX is not what costs.
Since one read of related pages runs at a time (Store.related_pages), a slow read delays
the next read of related pages, not page views. The page's own passages are not counted in
BYTES_MAX: they are read whole, as `page` reads them, and a page of 380,000 characters dense
with facts costs 0.35 s alone. Judging where a title stands in prose (`_mentioning`) reads
with the Markdown parser each passage that a claim listed for the title is the text of,
once however many claims are, when it holds a backtick, a tilde, a tab or four spaces in a
row and is no longer than cairn.spans.MAX_RENDERED characters: the bytes it parses are
bounded as the passages read are, and it parses them as the page that shows them does.
Measured on the same laptop on 2026-11-21 under a load of 4 to 5, the best of three runs,
64 such passages of 11,718 bytes each (749,952 bytes), judging in all (the parse, the
blanking and the database in memory) beside the page's own render of the same passages
(cairn.web.markdown.render): "![a `b` " over and over, the worst shape found, 2.55 s beside
2.43 s (4.16 s for the parse alone before the reading was the page's); tables of 300
columns of code spans 1.80 s beside 1.09 s; code spans with emphasis marks and links 0.64 s
beside 1.06 s; code spans alone 0.30 s beside 0.12 s; indented lines 0.13 s beside 0.05 s.
Judging a title costs about what rendering the passages it judges costs; a passage of
unclosed brackets and no backtick, which the page takes 5 s to render, it does not parse.

What is given is bounded too, and a claim says when it is not whole (`Stated`). A claim's
text is read to relations.TEXT_MAX characters and no further, by a handle on the value the
index stores (`_text`), so of a claim of any length no more is handed back than of a short
one; a longer one is given cut. Beside a claim cut that states a shared term goes the line
of its first passage that holds the term (relations.line_of), from the passage as this
read already holds it and from where the term's fact stands in it: nothing is read for it,
and the text is not searched a second time. Beside a claim cut that names the title goes
the line that names it: the line of its passage at the first place the index in memory
finds the title in prose (`_in_prose`), from the passage this read holds. A
claim cut that mentions the page as the index has it (its text is not its passage's,
character for character: saved by hand, or differing from it only by the blanks that end
its lines) has no place that is known to be prose: it has no line, and an answer gives its
first characters. A claim that holds a mark of the index or a null character names no page
(`_mentioning`).

Statements, T being the terms looked up, beside the four of the caller (the page's head, the
index's table, the notes of the passages the claims given cite, and who wrote them): 2 for
the project's span with the page's claims, and their passages; ceil(T / AT_ONCE) to list
the claims that hold each term's words; 1 for the title; 3 for the candidates, the title's
among them (their rows, the rows of their passages, the passages); 1 to say which of the
claims listed for the title are their passage's text (`_judged`); 2 for what is given (the
claims, the pages' titles and versions; each claim's text is then read by a handle, which
is no statement). A statement with nothing to ask is not run, nor one that lists claims past
ROWS_MAX rows. At most 2 + 4 + 1 + 3 + 1 + 2 = 13, and 17 with the caller's. Not on the
store: 3 in the database of the read's own in memory (`_in_prose`: its index made, given
each passage to judge once, asked the title once), whatever the claims, and none when no
claim listed for the title is its passage's text.
Rows: one for the span and two for each claim of the page; for each term at most
CLAIMS_COMMON + 1, and as many for the title; 3 for each candidate claim read, at most
CLAIMS_MAX, and one more for each of the title's, at most CLAIMS_COMMON; and what is given,
at most relations.RELATED_MAX pages of WHY_MAX reasons of two claims. (The caller's read of who
wrote the claims given is a row for each passage they cite: one for a claim as it is
compiled, and as many as a tidy folded into it, as a search reads them for a hit.)
tests/unit/test_relating_cost_unit.py counts statements, rows and SQLite's steps for pages of
10, 200 and 3,000 terms.
The rows of the index a read is handed are bounded, whatever the workspace holds. The index
is asked for each term once, and for the title once, over the project's span, its first row
to its last (`_SPAN`: two seeks a page of the project, once a read, in the statement that
reads the page's claims). A claim of another project outside the span is never walked,
however many there hold the page's words. One inside it that holds a term's words as the
term has them is handed over, and passed over by its project before anything of it is
read, as ranking.search does. A term's walk stops at its CLAIMS_COMMON + 1st claim of the
project, and every walk at the ROWS_MAX-th row handed over, the project's and others'
together, counted as SQLite hands them over (`_Visits`): the term being asked when the
count reaches ROWS_MAX, and every term after it, the title last, are not looked up. So
where other projects hold a page's terms in very many claims among the project's rows, an
answer can be incomplete, and says so (`terms_not_looked_up`); each reason it gives is
true, with the same count of pages and the same claims as with nothing left out, though it
can hold other true reasons than a complete answer would show first: a term not looked up
leaves room among the claims read (CLAIMS_MAX, BYTES_MAX) for another term's. Not
counted: a claim among the project's rows that holds every word of a term, but not side by
side as the term has them, is read by the index for its places and passed over inside the
index's own work, never handed over, so ROWS_MAX does not bound that work (measured beside
it). Which claims listed for the title are their passage's text is asked (`_judged`) by
each claim's own row, a claim of the project already read, with no match. The same test counts
the steps of a read whose project's pages were saved together, and each in turn with
another project's, at 120, 1,000 and 4,000 pages, beside 0, 8,000 and 64,000 claims of
that project that hold the page's words, outside the span and inside it; and beside 1,000
and 8,000 that hold every word and the title, saved before the project's pages, after
them or between them, on each path: the terms', the title's, and a mention given cut.

Limits. A term is found by its words as it is compared: `1,250x` is compared as `1250x`,
which the index reads as another word, so a claim that writes the separator states no term
the index finds. Such a term relates nothing, from either side.
"""

import contextlib
import json
import sqlite3
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from cairn import relations
from cairn.relations import MENTIONED_BY, SHARES, TITLE, Reason, Term
from cairn.spans import Span, code_read
from cairn.store import ranking, schema

TERMS_MAX = 256  # terms of a page that are looked up; it counts the rest
CLAIMS_MAX = 512  # claims of other pages read in all; it counts the rest
# BYTES_MAX, measured on one laptop (an Apple M5) on 2026-11-20, each passage read and counted
# once a read: this repository's documents filed as 32 pages, each asked at limit 20, and the
# worst read built (256 terms; 512 candidate claims of 2,000 bytes of flags, paths and
# versions each). Time is the middle of three runs, under a load of 10 to 13; the counts do
# not vary with the load, the times do.
#   bytes       complete  pairs  reasons lost      worst of the 32  worst page built
#   250,000     3 of 32   106    1,199 of 1,874    0.34 s           0.48 s
#   400,000     3 of 32   132      902             0.39 s           0.68 s
#   500,000    20 of 32   152      412             0.40 s           0.91 s
#   750,000    27 of 32   157      257             0.42 s           1.32 s
#   1,000,000  27 of 32   157      257             0.49 s           1.78 s
# The rule: the smallest value whose worst read is at most half a second on a quiet run and
# which keeps 24 of the 32 answers complete, else 500,000. 750,000 is the smallest that keeps
# 24. Its worst of the 32 is under half a second even under that load. The worst page built
# is the one time over it: no other page cites its passages or names its title, so its read
# is the same as before passages were counted once (the same claims read, the same answer),
# and that read, on the code before that change and under a load of 5.7, the quietest run
# measured, took 0.13, 0.21, 0.26, 0.37 and 0.51 s at the five values: 3.2 to 3.7 times less.
BYTES_MAX = 750_000  # bytes of those claims' passages, as stored, read in all
AT_ONCE = 64  # terms one statement asks the index for
# ROWS_MAX, measured on the same laptop on 2026-11-20 under a load of 5: the worst read built,
# a page of 256 terms in a project of 4,000 pages whose span holds 64,000 claims of another
# project, each with the words of 8 of the terms and the title (576,516 rows handed over with
# no bound); the index part of the read (`_listed`), the middle of three runs:
#   rows        index merged   as FTS5 left it
#   100,000     0.13 s         0.11 s
#   150,000     0.19 s         0.22 s
#   200,000     0.26 s         0.32 s
#   250,000     0.32 s         0.33 s
#   no bound    0.70 s         0.51 s
# about 1.3 microseconds and 19 of SQLite's steps a row handed over. 150,000 is the value whose
# index part is about a fifth of a second; the whole read then takes 0.20 s. Long claims cost
# less a row: 8,000 of 2,816 bytes, each every one of the 256 terms, 0.09 s at 150,000 rows
# (2,056,516 rows and 1.6 to 2.1 s with no bound). Not bounded by it:
# claims among the project's rows that hold the words of 8 of its terms apart, not side by
# side, which the index reads and passes over without handing them over: 64,000 of them take
# the index part from 0.006 s to 0.14 s, 256,000 to 0.55 s, and SQLite's steps do not see it.
ROWS_MAX = 150_000  # rows of the account's index a read is handed, for its terms and title
# The two marks the index in memory is asked to put around what it matched of a title in a
# passage's text (`_in_prose`): characters Unicode keeps for a program's own use and out of
# every text that is exchanged, so a claim holds neither unless someone put it there, and
# `_judged` judges no claim that does.
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
    terms_not_looked_up: int  # its terms past TERMS_MAX or past ROWS_MAX rows, the title as one
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
# The project's span in the account's index, its first row and its last, as one row with no
# claim: a page's rows are its claims, indexed together in one transaction
# (ranking.index_page), so each page is a range of rows whose first and last are two seeks
# of search_claims by its page. ?1 the account, ?2 the project.
_SPAN = (
    "SELECT NULL, NULL, min(lo), max(hi) FROM (SELECT"
    " (SELECT min(row) FROM search_claims WHERE account_id = ?1 AND project_id = ?2"
    " AND slug = heads.slug) AS lo,"
    " (SELECT max(row) FROM search_claims WHERE account_id = ?1 AND project_id = ?2"
    " AND slug = heads.slug) AS hi"
    " FROM page_heads AS heads WHERE heads.project_id = ?2)"
)


class _Visits:
    """The rows of the account's index one read is handed for its terms and its title,
    ROWS_MAX in all, counted as SQLite hands them over (`_listed`), by the part of a
    statement that asked for them: a term, or the title."""

    def __init__(self) -> None:
        self.total = 0
        self.by: dict[int, int] = {}

    def visit(self, part: int) -> int:
        self.total += 1
        self.by[part] = self.by.get(part, 0) + 1
        return 1

    def left(self) -> int:
        return max(0, ROWS_MAX - self.total)


_READ = threading.local()  # the count of the read under way on this thread (`_counting`)


def _visited(part: int) -> int:
    return _READ.visits.visit(part)


def _rows_left() -> int:
    return _READ.visits.left()


def _counting(con: sqlite3.Connection) -> _Visits:
    """A fresh count of the rows this read is handed, on `con`, which serves this read alone
    (Store.related_pages). SQLite calls the two functions the statements count by on the
    thread that runs the read, and they count on that thread's count, so they are the same
    two for every read: they are given to the connection at each read, and where SQLite
    refuses to give them again, as it does while a statement of the connection is left
    under way, the connection holds them already."""
    _READ.visits = visits = _Visits()
    with contextlib.suppress(sqlite3.OperationalError):  # refused: given already
        con.create_function("cairn_visited", 1, _visited)
        con.create_function("cairn_rows_left", 0, _rows_left)
    return visits


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
    own: Cited = {}
    span: tuple[int, int] | None = None  # the project's first row in the index and its last
    for row, span_ids, lo, hi in sorted(
        con.execute(
            _SPAN + f" UNION ALL SELECT c.row, {index}.span_ids, NULL, NULL"
            f" FROM search_claims AS c CROSS JOIN {index} ON {index}.rowid = c.row"
            " WHERE c.account_id = ?1 AND c.project_id = ?2 AND c.slug = ?3",
            (account_id, project_id, slug),
        ).fetchall(),
        key=lambda found: -1 if found[0] is None else found[0],
    ):
        if row is not None:
            own[row] = tuple(json.loads(span_ids))
        elif lo is not None:
            span = (lo, hi)
    visits = _counting(con)
    spans: dict[str, Span] = {}  # each passage read, whole: a line given is a line of one
    facts: dict[
        str, list[relations.Fact]
    ] = {}  # the facts of each, read once, kept for this read alone
    own_passages = _passages(con, project_id, [cited[0] for cited in own.values() if cited])
    held = _terms(own_passages, spans, facts)
    stating: dict[Term, set[int]] = {}  # each term of the page: its claims whose passage holds it
    for row, cited in own.items():
        for term in held.get(cited[0], ()) if cited else ():
            stating.setdefault(term, set()).add(row)
    known = sorted(stating, key=relations.looked_up_before)  # every term: looked up, or counted
    looked = known[:TERMS_MAX]
    listed = _listed(con, *scope, span, visits, [ranking.quoted(term.text) for term in looked])

    # Each term to settle: the page's own claim that states it, and the other pages' claims
    # that hold its words. Not a term more claims hold than make it common, nor one that no
    # claim of the page is found to state, nor one no other page holds a word of; nor one not
    # looked up, past ROWS_MAX rows.
    asked: dict[Term, tuple[int, Listed]] = {}
    for term, rows in zip(looked, listed, strict=True):
        if rows is None or len(rows) > relations.CLAIMS_COMMON:
            continue
        here = next((row for row, at in rows if at == slug and row in stating[term]), None)
        others = [(row, at) for row, at in rows if at != slug]
        if here is not None and others:
            asked[term] = (here, others)
    # The title is settled as a term is: the claims of other pages that hold its words are
    # read with the terms' claims, within the same bounds.
    named = _title(title)
    title_listed = _title_claims(con, *scope, span, visits, slug, named, len(looked))
    naming = title_listed or []
    candidates = _candidates(con, *scope, [*(others for _, others in asked.values()), naming])
    passages, unread = _within(  # a passage of the page's own is held whole already
        con,
        project_id,
        list(dict.fromkeys(c[0] for c in candidates.values() if c and c[0] not in spans)),
    )
    held |= _terms(passages, spans, facts)
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
    mentioning = _mentioning(con, index, title, named, naming, candidates, read, spans, facts)
    for at, (row, place) in mentioning.items():
        reasons.setdefault(at, []).append(Reason(MENTIONED_BY, title, TITLE, 2, None, row))
        if place is not None:
            places[row] = place

    given, pages_left_out = relations.best(reasons, limit)
    pages = _given(con, *scope, given, held, spans, places) if given else ()
    wanted = {row for _, others in asked.values() for row, _ in others} | {r for r, _ in naming}
    terms_looked_up = sum(rows is not None for rows in listed)
    left = Left(
        terms_looked_up=terms_looked_up,
        terms_not_looked_up=len(known) - terms_looked_up + (title_listed is None),
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
    span: tuple[int, int] | None,
    visits: _Visits,
    slug: str,
    named: str | None,
    part: int,
) -> Listed | None:
    """The claims of other pages whose text holds the title as a phrase (`named`: `_title`),
    by row: the index's own match, on the text column, asked last, as part `part`. None when
    it is not looked up past ROWS_MAX rows (`_listed`). No claim for a title that is not
    looked up for its length, nor for one more than relations.CLAIMS_COMMON such claims
    hold, which is common and relates none. One statement at most."""
    if named is None:
        return []
    (rows,) = _listed(con, account_id, index, project_id, span, visits, [named], part, but=slug)
    return None if rows is None else [] if len(rows) > relations.CLAIMS_COMMON else rows


def _mentioning(
    con: sqlite3.Connection,
    index: str,
    title: str,
    named: str | None,
    naming: Listed,
    candidates: Cited,
    read: set[int],
    spans: Mapping[str, Span],
    facts: Mapping[str, Sequence[relations.Fact]],
) -> dict[str, tuple[int, tuple[int, int] | None]]:
    """Each other page that names the title in the prose of a claim: its first claim that
    does, by row, and where in the claim's text: the first place where the title stands
    whole in prose.

    A claim that holds the title's words (`naming`) names the title only where one place the
    title stands in it lies outside every stretch of its passage that the facts read as an
    address, a path, a flag or a version (relations.covered, on the facts this read holds of
    the passage, read whole) and outside what the Markdown parser reads as code in the claim
    alone, as the page does (cairn.spans.code_read). Each place is judged by itself,
    whatever other place overlaps it: the index is given the claim's text with those
    stretches blanked out in place (relations.blanked) and asked the title again
    (`_in_prose`). Those are
    places of the passage because the claim's text is the passage's text; the statement
    says whether it is (`_judged`), character for character. Where it is not (a page saved
    by hand; a claim that differs from its passage only by the blanks that end its lines,
    which the gate accepts as its text), and where the claim cites no passage of the
    project, the passage says nothing of the claim: the claim mentions the page as the
    index has it, and its place is None. A claim that holds a mark of the index or a null
    character names no page: where the index matched in it cannot be told, so whether it
    names the title in its prose cannot be checked.

    As for a term: a title one of whose claims was not read gives no mention at all; nor
    does a title more than relations.COMMON other pages name by this rule. One statement,
    none for no claim read; and `_in_prose`'s, once for every claim that is its passage's
    text, each passage given once however many such claims are its text."""
    asked = [
        (row, cited[0] if (cited := candidates[row]) and cited[0] in spans else None)
        for row, _ in naming
        if row in read
    ]
    judged = _judged(con, index, asked) if named and asked else {}
    if any(row not in read for row, _ in naming):  # a claim not read: no mention at all
        return {}
    # each claim that is its passage's text: that passage, numbered once however many cite it
    passage = {row: candidates[row][0] for row, its_passage in judged.items() if its_passage}
    numbered = {span_id: n for n, span_id in enumerate(dict.fromkeys(passage.values()), 1)}
    texts = {}  # each of those passages, by its number, with what is not prose blanked out
    asked, said = named or "", title.replace("\x00", " ")  # as ranking.quoted asks it
    if numbered:  # how the index's tokenizer reads each character they and the title hold
        kind = _kinds(
            [relations.fill_for(asked), *said, *(c for s in numbered for c in spans[s].text)]
        )
    for span_id, n in numbered.items():
        span = spans[span_id]
        stretches = [*relations.covered(span, facts[span_id]), *code_read(span.text)]
        texts[n] = relations.blanked(span.text, stretches, asked, kind)
    places = _in_prose(named, texts) if named and texts else {}
    if places:  # the first place: as many words from where the index's marks begin as the title
        words, passages = relations.words_in(said, kind), dict(map(reversed, numbered.items()))
        places = {
            n: (start, relations.words_end(spans[passages[n]].text, start, words, kind))
            for n, (start, _) in places.items()
        }
    names: dict[str, tuple[int, tuple[int, int] | None]] = {}
    for row, at in naming:
        if at in names:
            continue
        if row not in judged:  # the index's word stands
            names[at] = (row, None)
        elif row in passage and (place := places.get(numbered[passage[row]])):
            names[at] = (row, place)
    return {} if len(names) > relations.COMMON else names


def _judged(
    con: sqlite3.Connection, index: str, asked: Sequence[tuple[int, str | None]]
) -> dict[int, bool]:
    """For each (claim's row, its first passage's id when this read holds it, else None) of
    `asked`: False for a claim whose text holds a mark or a null character (SQLite's reading
    of a text stops there), whose places cannot be told; True for a claim whose text is its
    passage's text, character for character; and nothing for any other claim. The
    statement decides all three and hands back no text, so a claim reworded from its
    passage, of any length, costs nothing. One statement."""
    return {
        row: bool(its_passage)
        for row, its_passage in con.execute(
            f"SELECT {index}.rowid, CASE WHEN instr({index}.text, ?2) + instr({index}.text, ?3)"
            f" + instr(CAST({index}.text AS BLOB), X'00') > 0 THEN 0"
            f" WHEN {index}.text = spans.text THEN 1 END"
            f" FROM json_each(?1) AS wanted"
            f" CROSS JOIN {index} ON {index}.rowid = json_extract(wanted.value, '$[0]')"
            f" LEFT JOIN spans ON spans.id = json_extract(wanted.value, '$[1]')",
            (json.dumps([list(pair) for pair in asked]), *MARKS),
        )
        if its_passage is not None
    }


def _scratch() -> sqlite3.Connection:
    """A database of the read's own, in memory, closed when `_in_prose` is done with it; each
    statement its own transaction."""
    return sqlite3.connect(":memory:", isolation_level=None)


# What the index's tokenizer reads each character as (relations.TOKEN, MARK, SEPARATOR; 0 for
# one not asked yet), by code point: one table for each SQLite library and declaration of the
# index, since the reading is theirs alone and neither changes while a process runs.
_KINDS: dict[tuple[str, str], bytearray] = {}
_PROBES = ("qzx", "qzy")  # two words of letters, around the character asked


def _kinds(chars: Iterable[str]) -> Callable[[str], int]:
    """What the index's tokenizer reads each character as, asked of the tokenizer itself and
    never of a table written here: an index of the claims' declaration, in a database of its
    own in memory (`_classifying`), is given each character of `chars` not asked before
    twice, at a text's start before a word (it starts a word there, or separates) and
    inside one (it goes on with the word, or ends it), and asked where it finds the two
    probe words: a TOKEN starts a word, a MARK goes on with one and starts none (a combining
    mark), a SEPARATOR does neither. Three statements there (made, given the characters,
    asked), for the characters this process has not asked about, and none when it has
    asked about them all; the answers are kept for the process (`_KINDS`)."""
    known = _KINDS.setdefault((sqlite3.sqlite_version, schema._CLAIM_INDEX), bytearray(0x110000))
    new = sorted({char for char in chars if not known[ord(char)]})
    if not new:
        return lambda char: known[ord(char)]
    opened, closed = [m for m in map(chr, range(0xFDD0, 0xFDF0)) if m not in new][:2]
    first, then = _PROBES
    given = [[2 * n + 1, char + then] for n, char in enumerate(new)]
    given += [[2 * n + 2, first + char + then] for n, char in enumerate(new)]
    with contextlib.closing(_classifying()) as classifying:
        classifying.execute(schema._CLAIM_INDEX.format(index="chars"))
        classifying.execute(
            "INSERT INTO chars (rowid, text) SELECT json_extract(value, '$[0]'),"
            " json_extract(value, '$[1]') FROM json_each(?1)",
            (json.dumps(given),),
        )
        marked = dict(
            classifying.execute(
                "SELECT rowid, highlight(chars, 5, ?2, ?3) FROM chars WHERE chars MATCH ?1",
                (f"{first} OR {then}", opened, closed),
            ).fetchall()
        )
    for n, char in enumerate(new):
        starts = marked.get(2 * n + 1) != f"{char}{opened}{then}{closed}"
        ends = marked.get(2 * n + 2) == f"{opened}{first}{closed}{char}{opened}{then}{closed}"
        known[ord(char)] = (
            relations.TOKEN if starts else relations.SEPARATOR if ends else relations.MARK
        )
    return lambda char: known[ord(char)]


def _classifying() -> sqlite3.Connection:
    """A database of its own, in memory, in which `_kinds` asks the index's tokenizer how it
    reads characters, closed when that is done; each statement its own transaction."""
    return sqlite3.connect(":memory:", isolation_level=None)


def _in_prose(named: str, texts: Mapping[int, str]) -> dict[int, tuple[int, int]]:
    """For each claim of `texts` (its row: its text with what is not prose blanked out,
    relations.blanked) in which the index's own tokenizer finds the title asked (`named`),
    the first place it does, in the claim's characters: an index of the same declaration as
    the account's, in a database of the read's own in memory, is given every text and asked
    the title as the account's index is. Three statements in that database, whatever the
    texts: it is made, given the texts, asked. Its marks around overlapping matches make
    one stretch, which starts where the first match does; each match of it stands whole in
    prose. Nothing of it is kept: the database is closed when this returns. A claim's text
    holds no mark of the index (`_judged`)."""
    with contextlib.closing(_scratch()) as scratch:
        scratch.execute(schema._CLAIM_INDEX.format(index="prose"))
        scratch.execute(
            "INSERT INTO prose (rowid, text) SELECT json_extract(value, '$[0]'),"
            " json_extract(value, '$[1]') FROM json_each(?1)",
            (json.dumps(list(texts.items())),),
        )
        return {
            row: places[0]
            for row, marks in scratch.execute(
                "SELECT rowid, highlight(prose, 5, ?2, ?3) FROM prose WHERE prose MATCH ?1",
                (named, *MARKS),
            ).fetchall()
            if (places := _places(marks))
        }


def _places(marks: str) -> list[tuple[int, int]]:
    """Where the index matched, in the characters of the text it marked, in order: MARKS[0]
    stands before each match and MARKS[1] after it, and the text itself holds neither
    (`_judged` gives `_in_prose` no other text). A first mark with no second after it ends the
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
    passages: Iterable[Span], spans: dict[str, Span], facts: dict[str, list[relations.Fact]]
) -> dict[str, dict[Term, tuple[int, int]]]:
    """Each passage read: the terms it holds and where each stands in it (relations.placed, on
    the passage whole). The passage is kept in `spans`, and its facts, read once, in `facts`."""
    held = {}
    for span in passages:
        spans[span.id] = span
        facts[span.id] = relations.extract_facts(span)
        held[span.id] = relations.placed(span, facts[span.id])
    return held


def _within(
    con: sqlite3.Connection, project_id: str, span_ids: Sequence[str]
) -> tuple[list[Span], set[str]]:
    """The candidates' passages, in the order given, until BYTES_MAX bytes are read, and the
    ids of those of the project left unread. Each is measured first from its row, by a
    handle on its stored text that reads none of it, so a passage that does not fit is
    neither read nor fetched: two statements, none for no passage. Each passage counts
    once, whichever claims cite it and whatever they are listed for."""
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
    span: tuple[int, int] | None,
    visits: _Visits,
    matches: Sequence[str],
    first: int = 0,
    but: str | None = None,
) -> list[Listed | None]:
    """For each match, relations.CLAIMS_COMMON + 1 claims at most of the project that hold it
    (of its pages other than `but`, when given), as (row, the page's slug), by row: every
    such claim when there are no more, the first by row else; or None, for a match not
    looked up. One statement for every AT_ONCE matches, none when ROWS_MAX rows are spent.

    The index is asked for each match once, over the project's span (its first row to its
    last), in order: the rows it hands over are counted (`visits`, the matches being parts
    `first` on), and each is kept only when search_claims puts it in the project, by
    primary key, before any column of it is read, as ranking.search does. A match's walk
    stops at its CLAIMS_COMMON + 1st claim of the project, and every walk at the
    ROWS_MAX-th row handed over, the project's and others' together: the match being asked
    when the count reaches ROWS_MAX, and every match after it, are not looked up. A project
    with no row in the index has nothing to ask."""
    if span is None:
        return [[] for _ in matches]
    found: list[Listed | None] = [None] * len(matches)
    for start in range(0, len(matches), AT_ONCE):
        if not visits.left():
            break
        part = matches[start : start + AT_ONCE]
        each = " UNION ALL ".join(
            f"SELECT * FROM (SELECT ?{7 + 2 * n} AS part, walked.row AS row, c.slug AS slug"
            f" FROM (SELECT {index}.rowid AS row FROM {index} WHERE {index} MATCH ?{8 + 2 * n}"
            f" AND {index}.rowid BETWEEN ?5 AND ?6 AND cairn_visited(?{7 + 2 * n})"
            " LIMIT cairn_rows_left()) AS walked"
            " CROSS JOIN search_claims AS c ON c.account_id = ?1 AND c.row = walked.row"
            " WHERE c.project_id = ?2 AND (?3 IS NULL OR c.slug <> ?3) LIMIT ?4)"
            for n in range(len(part))
        )
        parameters: list[object] = [account_id, project_id, but, relations.CLAIMS_COMMON + 1]
        parameters += span
        for n, match in enumerate(part):
            parameters += [first + start + n, match]
        handed = visits.total
        rows = con.execute(each, parameters).fetchall()  # read to its end: nothing left under way
        for n in range(len(part)):
            handed += visits.by.get(first + start + n, 0)
            if handed >= ROWS_MAX:
                break  # this match and those after it: not looked up
            found[start + n] = []
        for at_part, row, at in rows:
            if (listed := found[at_part - first]) is not None:
                listed.append((row, at))
    return [None if rows is None else sorted(rows) for rows in found]


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
