"""Search: claims matched in the account's FTS5 index and ranked with BM25 over the projects
the caller reaches, the same code for every view.

FTS5's own bm25() weighs a term by statistics over its whole table, every project of the
account. Here they come from the reach alone, so a search's hits and scores depend only on
the projects the caller reaches:

- N and the average claim length come from `search_projects`, the claim and token counts of
  each project, which save_page keeps in its own transaction (index_page);
- a word's document frequency is a count of the reachable claims it matches, which stops
  one match past COUNTED_MAX (below);
- a word's frequency in a claim, and the claim's length, are those FTS5 itself would use:
  the instances `highlight()` marks, and the token count FTS5 stored for the row;
- the formula and constants are FTS5's: k1 = 1.2, b = 0.75, IDF = ln((N - n + 0.5) /
  (n + 0.5)) floored at 1e-6, summed over the query's words (a word given twice counts
  twice). Scores are positive, higher is better, as the store has always returned them.

Matching walks each word's entries in the account's index, and keeps a match only when
`search_claims` (row to project, by primary key) puts it in reach: a claim out of reach is
never read. Python rounds where SQLite's C may fuse a multiply and an add, so a score can
differ from bm25()'s in its last bits, never in order.

Bounded work, whoever asks (decision 38), and nothing left out silently (Q2):

- The words that count are the query's first WORDS_MAX, and fewer when their parts (FTS5
  splits a word at "_") would pass PARTS_MAX: the words stop before the first one that would
  take them past it (`split`). The others are left out, and the answer names them.
- The budget is work, and a short query spends what a long one already did (Q3): a search
  scores as many matching claims as SCORED_WORK allows, a claim scored costing one unit per
  distinct part of the words that count, and never fewer than SCORED_MAX (`scored_max`,
  worked out when the search runs): max(SCORED_MAX, SCORED_WORK // p) for p parts. Time
  follows claims scored times parts, and SCORED_WORK = 8,000 is what a query of eight parts
  already cost at SCORED_MAX before Q3, so a query of one part scores 8,000 claims, two
  4,000, three 2,666, four 2,000, and eight parts or more 1,000, as before. Measured on an
  Apple M5 (2026-11-15), Store.find over 32,000 claims of 15 to 25 words that all match:
  eight words at 1,000 claims took 0.37 s before Q3, and at their bounds one word took
  0.19 s, two 0.32 s, three 0.35 s, four 0.31 s and eight 0.36 s. No search costs more time
  than one of eight parts did, and none scores fewer claims than before. Words are matched
  with OR and none is dropped, so a question of several words matches most of a workspace:
  the work goes to the short queries, whose claims weigh alike, not to scoring claims that
  hold only a long query's common words.
- The search asks for one claim more than it may score, so it knows whether the bound
  applied without counting anything else. When it did, the claims scored are those likeliest
  to score highest, by statistics of the reach alone: each word of the query that a claim
  holds weighs its IDF times how often the query gives it (the most a word can add to a BM25
  score is in proportion to that), and the claims whose words weigh most are scored, the
  newest first among equals. A query of one matching word gives every match the same weight,
  so the newest are scored, as before Q2. (When the document frequencies of the query's
  words add up to the bound or less, every match is scored, and the claims are read newest
  first as before Q2, without weighing them.) N and the document frequencies still count
  every reachable claim, up to COUNTED_MAX, so a scored claim's score is the same whether
  or not the scoring bound applied (above COUNTED_MAX, see the next point).
- A word's count reads at most COUNTED_MAX + 1 of its matches in reach (R6), so no
  statement grows with the claims the caller reaches. COUNTED_MAX = 8,000 is the most
  claims any search scores (scored_max(1)), and a count is never stopped below what the
  search scores, so a stopped count changes neither the path (every match, the newest, the
  likeliest) nor which claims are scored by the newest. A word held by more claims weighs
  as one held by COUNTED_MAX + 1: its true IDF is at most that, and it is still less than
  the IDF of any word counted exactly, so the words keep their order; it is as common as a
  word needs to be for so small a weight. The likeliest walk reads each word's matches as
  far as its count did, the newest first, so an older claim that holds only words counted
  to the bound is not weighed by them. Under the bound the hits, their order and their
  scores are what they were.
- What was left out is worked out once (`found`, a cairn.store.models.Searched): the words
  left out, the claims scored, and how many matched in the projects searched, known without
  another statement when every match was scored, or when one word matched and the projects
  searched are the whole reach (its document frequency is that count: when that count
  stopped at its bound, more than COUNTED_MAX match, and the answer says so); else not
  counted.

A search runs one statement for the reach's counts, one per distinct word that counts (its
document frequency: one row, from at most C + 1 matches read, C = COUNTED_MAX), one for the
claims to score (none when no word matches; at most S + 1 rows, S = scored_max(p) for the p
distinct parts of those words; on the likeliest path it reads at most C + 1 matches of each
word to weigh them), and one per distinct part (at most S rows each, each claim read by its
row). The claims and their parts so cost S x (p + 1) + 1 rows: 16,001 for one part, 12,001
for two, 10,001 for four, 9,001 for eight, and the most, 65,001, for 64. With Store.find's
two, and the reach's counts and the 32 document frequencies, 100 statements and 65,035 rows
at most, plus one row per project of the account, as before Q3; and the matches walked to
return them, at most 32 x (C + 1) = 256,032 for the counts and as many for the likeliest
walk, however many claims the caller reaches match (R6). tests/unit/test_search_cost_unit.py
counts the statements and rows over queries of 1, 2, 4, 8 and 64 parts, and their steps in
SQLite over three times the matches; tests/unit/test_search_steps_unit.py measures one and two words
over 9,000 and 27,000 matching claims. Two things still grow: a claim read by row is
sought in each segment of the account's index that holds the word (FTS5 merges segments,
so they stay few), and a match out of the caller's reach is walked and passed over, not
counted, so a caller who reaches few of a word's matches still walks the account's: the
index has no order by project, and giving it one is a schema change.
"""

import json
import math
import re
import sqlite3
import unicodedata
from collections.abc import Collection, Iterator, Sequence
from itertools import chain
from typing import NamedTuple

from cairn.store.models import Claim, Found, Searched, SearchHit

K1 = 1.2
B = 0.75
IDF_FLOOR = 1e-6
WORDS_MAX = 32  # words of a query that count; any after them are left out, and named
PARTS_MAX = 64  # distinct parts ("_" splits a word) the words that count may hold
SCORED_MAX = 1000  # matches a query of PARTS_MAX parts scores, and the fewest any query scores
SCORED_WORK = 8_000  # claims scored x parts a search may spend: 8 parts at SCORED_MAX
# The matches a word's count reads before it stops: the most claims any search scores
# (scored_max(1)), so a count past it never changes how the claims to score are chosen, nor
# the "N claims match" a search of one word can say. A word held by more is common: it
# weighs as a word held by COUNTED_MAX + 1 claims, less than any word counted exactly.
COUNTED_MAX = 8_000
WORDS_SHOWN = 5  # the words left out that an answer names; it counts the rest
WEIGHT_SCALE = 1_000_000  # a word's weight is its IDF in millionths: whole numbers add exactly
_TITLE, _TEXT = 4, 5  # the index's searched columns (schema._CLAIM_INDEX)


def split(query: str) -> tuple[list[str], list[str]]:
    """(the words that count, the words left out), each in the query's order. The words of a
    query are its runs of word characters; those that count are the first WORDS_MAX, and
    fewer when their distinct parts would pass PARTS_MAX. Each word that counts becomes one
    quoted FTS5 phrase, so no user text is ever read as query syntax."""
    found = re.findall(r"\w+", query)
    counted: list[str] = []
    parts: set[str] = set()
    for word in found[:WORDS_MAX]:
        more = parts | {part for part in word.split("_") if part}
        if len(more) > PARTS_MAX:
            break
        parts = more
        counted.append(word)
    return counted, found[len(counted) :]


def words(query: str) -> list[str]:
    """The words of a query that count (split)."""
    return split(query)[0]


def scored_max(parts: int) -> int:
    """How many matching claims a search scores when its words hold `parts` distinct parts:
    what SCORED_WORK allows, a claim scored costing one unit of work per part, and never
    fewer than SCORED_MAX. Read when the search runs."""
    return max(SCORED_MAX, SCORED_WORK // max(parts, 1))


def _parts(query_words: Sequence[str]) -> set[str]:
    """The distinct parts of the words (FTS5 splits a word at "_"): one statement each in
    _frequencies."""
    return {part for word in query_words for part in word.split("_") if part}


class Ranked(NamedTuple):
    """What `search` found: the hits, how many claims matched in the projects searched (None:
    more than were scored, not counted), how many were scored, how those were chosen, and how
    many projects were searched."""

    hits: list[SearchHit]
    matched: int | None
    scored: int
    by: str  # "all", "newest" or "likeliest" (Searched.ranked_by)
    projects: int = 0
    exact: bool = True  # False: more than `matched` match; the count stopped there


NOTHING = Ranked([], 0, 0, "all")


def found(counted: Sequence[str], left_out: Sequence[str], ranked: Ranked = NOTHING) -> Found:
    """The answer of a search and the facts of what it left out: the one place they are
    worked out (cairn.memory.searched words them)."""
    return Found(
        hits=ranked.hits,
        searched=Searched(
            words_searched=len(counted) if _parts(counted) else 0,
            words_left_out=tuple(left_out[:WORDS_SHOWN]),
            words_left_out_count=len(left_out),
            claims_matched=ranked.matched,
            claims_matched_exact=ranked.exact,
            claims_ranked=ranked.scored,
            ranked_by=ranked.by,
            projects_searched=ranked.projects,
        ),
    )


# Statistics


def index_page(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    project_id: str,
    slug: str,
    title: str,
    claims: Sequence[Claim],
) -> None:
    """Replace a page's rows in the account's index, and its project's counts with them, in
    the caller's transaction."""
    claims_before, tokens_before = con.execute(
        "SELECT count(*), coalesce(sum(tokens), 0) FROM search_claims"
        " WHERE account_id = ? AND project_id = ? AND slug = ?",
        (account_id, project_id, slug),
    ).fetchone()
    con.execute(
        "DELETE FROM search_claims WHERE account_id = ? AND project_id = ? AND slug = ?",
        (account_id, project_id, slug),
    )
    con.execute(f"DELETE FROM {index} WHERE project_id = ? AND slug = ?", (project_id, slug))
    rows = []
    for claim in claims:
        row = con.execute(
            f"INSERT INTO {index} (project_id, slug, claim_id, span_ids, title, text)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, slug, claim.id, json.dumps(claim.span_ids), title, claim.text),
        ).lastrowid
        rows.append((account_id, row, project_id, slug, _row_tokens(con, index, row)))
    con.executemany(
        "INSERT INTO search_claims (account_id, row, project_id, slug, tokens)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    con.execute(
        "INSERT INTO search_projects (project_id, account_id, claims, tokens) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (project_id) DO UPDATE"
        " SET claims = claims + excluded.claims, tokens = tokens + excluded.tokens",
        (
            project_id,
            account_id,
            len(rows) - claims_before,
            sum(row[-1] for row in rows) - tokens_before,
        ),
    )


def count_index(con: sqlite3.Connection, account_id: str, index: str) -> None:
    """The statistics of an index written before they were kept (schema step 5)."""
    rows = con.execute(
        f"SELECT i.rowid, i.project_id, i.slug, d.sz FROM {index} AS i"
        f" JOIN {index}_docsize AS d ON d.id = i.rowid ORDER BY i.rowid"
    ).fetchall()
    con.executemany(
        "INSERT INTO search_claims (account_id, row, project_id, slug, tokens)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (account_id, row, project, slug, sum(_varints(size)))
            for row, project, slug, size in rows
        ],
    )
    con.execute(
        "INSERT INTO search_projects (project_id, account_id, claims, tokens)"
        " SELECT project_id, account_id, count(*), sum(tokens) FROM search_claims"
        " WHERE account_id = ? GROUP BY project_id",
        (account_id,),
    )


def _row_tokens(con: sqlite3.Connection, index: str, row: int) -> int:
    """The tokens FTS5 counted in a row, over every column: what bm25() reads as its
    length. FTS5 keeps them in its docsize table, one varint per column."""
    (size,) = con.execute(f"SELECT sz FROM {index}_docsize WHERE id = ?", (row,)).fetchone()
    return sum(_varints(size))


def _varints(blob: bytes) -> Iterator[int]:
    """SQLite's varints: seven bits a byte, high bit set on all but the last byte."""
    value = 0
    for byte in blob:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            yield value
            value = 0


# Search


def search(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    reach: Collection[str],
    query_words: Sequence[str],
    project_ids: Sequence[str] | None,
    limit: int,
) -> Ranked:
    """The best `limit` claims of the projects in `reach` (and in `project_ids`, when given) for
    `query_words`, best first, ties in index order, with how many matched and were scored.
    `reach` is also the corpus every statistic counts."""
    shown = set(reach) if project_ids is None else set(reach) & set(project_ids)
    if not shown:
        return NOTHING
    in_reach = json.dumps(sorted(reach))
    n_claims, n_tokens = con.execute(
        "SELECT coalesce(sum(claims), 0), coalesce(sum(tokens), 0) FROM search_projects"
        " WHERE project_id IN (SELECT value FROM json_each(?))",
        (in_reach,),
    ).fetchone()
    if not n_claims:
        return NOTHING
    matched = _JOINED.format(index=index)
    most = scored_max(len(_parts(query_words)))  # what the work allows this query
    counted_max = max(COUNTED_MAX, most)  # a count past `most` never changes the path taken

    idf, df = {}, {}
    for word in dict.fromkeys(query_words):
        (found,) = con.execute(  # stops one match past its bound: then it is not exact
            f"SELECT count(*) FROM (SELECT 1 {matched}"
            f" WHERE {index} MATCH ? AND c.project_id IN (SELECT value FROM json_each(?))"
            " LIMIT ?)",
            (account_id, _phrases([word]), in_reach, counted_max + 1),
        ).fetchone()
        df[word] = found
        value = math.log((n_claims - found + 0.5) / (found + 0.5))
        idf[word] = IDF_FLOOR if value <= 0.0 else value

    held = [word for word, found in df.items() if found]  # a word no claim in reach holds
    if not held:  # matches none in the projects searched either
        return NOTHING
    # One word: every match holds it, so the newest are the likeliest. Words whose matches add
    # up to `most` or fewer: every match is scored, whatever the order they are read in.
    if len(held) == 1 or sum(df[word] for word in held) <= most:
        candidates = _newest(con, account_id, index, held, shown, most)
    else:
        weights = {word: round(idf[word] * query_words.count(word) * WEIGHT_SCALE) for word in held}
        candidates = _likeliest(con, account_id, index, weights, shown, most, counted_max)
    capped = len(candidates) > most  # one row more than the bound was asked for
    candidates = candidates[:most]
    if not candidates:
        return NOTHING
    exact = True
    if not capped:
        matched_count, by = len(candidates), "all"
    elif len(held) == 1:  # its document frequency counts the reach: the matches' only when
        # the projects searched are the whole reach
        matched_count, by = (df[held[0]] if shown == set(reach) else None), "newest"
        if matched_count is not None and matched_count > counted_max:  # the count stopped
            matched_count, exact = counted_max, False
    else:
        matched_count, by = None, "likeliest"

    texts = {row[0]: (row[5], row[7]) for row in candidates}
    freq = _frequencies(con, index, query_words, texts)
    avgdl = n_tokens / n_claims
    scored = []
    for row, tokens, *hit in candidates:
        score = 0.0
        for word in query_words:
            f = float(freq.get((word, row), 0))
            d = float(tokens)
            score += idf[word] * ((f * (K1 + 1.0)) / (f + K1 * (1 - B + B * d / avgdl)))
        scored.append((-score, row, hit))
    scored.sort(key=lambda entry: entry[:2])
    hits = [
        SearchHit(
            project_id=project_id,
            project=project,
            slug=slug,
            title=title,
            claim_id=claim_id,
            text=text,
            span_ids=tuple(json.loads(span_ids)),
            version=version,
            score=-negative,
        )
        for negative, _, (project_id, project, slug, title, claim_id, text, span_ids, version) in (
            scored[:limit]
        )
    ]
    return Ranked(hits, matched_count, len(candidates), by, len(shown), exact)


# A match in the account's index, with its row's project and length, read by primary key.
_JOINED = "FROM {index} JOIN search_claims AS c ON c.account_id = ? AND c.row = {index}.rowid"
# What a candidate is read as: its row, length, project id and name, page, title, claim, text,
# passages, and the version of its page the claim was read on. A page's rows in the index are
# its latest version's claims (index_page, in the transaction that saves the version), and
# page_heads holds that version's number, so the one statement that reads a claim reads it,
# by primary key for each claim returned (a join instead let the planner read page_heads in
# an order whose steps grew with the pages: tests/unit/test_search_steps_unit.py).
_CANDIDATE = (
    "{index}.rowid, c.tokens, c.project_id, projects.name, {index}.slug, {index}.title,"
    " {index}.claim_id, {index}.text, {index}.span_ids,"
    " (SELECT version FROM page_heads WHERE project_id = c.project_id AND slug = c.slug)"
)


def _newest(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    query_words: Sequence[str],
    shown: Collection[str],
    most: int,
) -> list[tuple]:
    """The `most` + 1 most recently indexed claims of `shown` that hold a word: one
    statement, which reads the claims it returns and no other."""
    return con.execute(
        f"SELECT {_CANDIDATE.format(index=index)}"
        f" {_JOINED.format(index=index)} JOIN projects ON projects.id = c.project_id"
        f" WHERE {index} MATCH ? AND c.project_id IN (SELECT value FROM json_each(?))"
        f" ORDER BY {index}.rowid DESC LIMIT ?",
        (account_id, _phrases(query_words), json.dumps(sorted(shown)), most + 1),
    ).fetchall()


def _likeliest(
    con: sqlite3.Connection,
    account_id: str,
    index: str,
    weights: dict[str, int],
    shown: Collection[str],
    most: int,
    counted_max: int,
) -> list[tuple]:
    """The `most` + 1 claims of `shown` whose words weigh most (each word a claim holds
    adds its weight), the newest first among equals: one statement, which walks each word's
    matches by row alone, the newest `counted_max` + 1 of them at most (the matches its count
    read), and reads only the claims it returns."""
    held = " UNION ALL ".join(
        f"SELECT * FROM (SELECT {index}.rowid AS row, ?{5 + 2 * n} AS weight"
        f" FROM {index} JOIN search_claims AS c ON c.account_id = ?1 AND c.row = {index}.rowid"
        f" WHERE {index} MATCH ?{6 + 2 * n} AND c.project_id IN (SELECT value FROM json_each(?2))"
        f" ORDER BY {index}.rowid DESC LIMIT ?4)"
        for n in range(len(weights))
    )
    parameters: list[object] = [account_id, json.dumps(sorted(shown)), most + 1, counted_max + 1]
    for word, weight in weights.items():
        parameters += [weight, _phrases([word])]
    return con.execute(
        f"WITH held AS ({held}),"
        " likely AS (SELECT row, sum(weight) AS weight FROM held GROUP BY row"
        " ORDER BY weight DESC, row DESC LIMIT ?3)"
        f" SELECT {_CANDIDATE.format(index=index)} FROM likely"
        f" JOIN {index} ON {index}.rowid = likely.row"
        " JOIN search_claims AS c ON c.account_id = ?1 AND c.row = likely.row"
        " JOIN projects ON projects.id = c.project_id"
        " ORDER BY likely.weight DESC, likely.row DESC",
        parameters,
    ).fetchall()


def _phrases(query_words: Sequence[str]) -> str:
    return " OR ".join(f'"{word}"' for word in query_words)


def _frequencies(
    con: sqlite3.Connection,
    index: str,
    query_words: Sequence[str],
    texts: dict[int, tuple[str, str]],
) -> dict[tuple[str, int], int]:
    """(word, row): how many times FTS5 finds the word's phrase in the row's title and text.

    `highlight()` marks each token that matches a one-token phrase, so each part of a word
    (a word with "_" in it is a phrase of several tokens, as FTS5 splits it) is marked on
    its own, one statement per part over the rows given. A phrase instance is then a run of
    marked tokens, one per part in order, with no other token between them; instances may
    overlap, and each counts, as in FTS5."""
    open_, close = _markers(chain.from_iterable(texts.values()))
    parts = {word: [part for part in word.split("_") if part] for word in query_words}
    marked: dict[str, dict[tuple[int, int], list[tuple[int, int]]]] = {}
    rows = json.dumps(sorted(texts))
    for part in dict.fromkeys(chain.from_iterable(parts.values())):
        found: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for row, title, text in con.execute(
            f"SELECT rowid, highlight({index}, {_TITLE}, ?1, ?2), highlight({index}, {_TEXT},"
            f" ?1, ?2) FROM {index} WHERE {index} MATCH ?3"
            " AND rowid IN (SELECT value FROM json_each(?4))",
            (open_, close, _phrases([part]), rows),
        ):
            found[row, 0] = _marked(title, open_, close)
            found[row, 1] = _marked(text, open_, close)
        marked[part] = found

    freq: dict[tuple[str, int], int] = {}
    for word in dict.fromkeys(query_words):
        if not parts[word]:
            continue  # only "_": a phrase of no token, which matches nothing
        for row, columns in texts.items():
            count = sum(
                _instances(parts[word], marked, (row, column), columns[column]) for column in (0, 1)
            )
            if count:
                freq[word, row] = count
    return freq


def _instances(
    parts: list[str],
    marked: dict[str, dict[tuple[int, int], list[tuple[int, int]]]],
    key: tuple[int, int],
    text: str,
) -> int:
    tokens: dict[tuple[int, int], set[str]] = {}
    for part in set(parts):
        for span in marked[part].get(key, ()):
            tokens.setdefault(span, set()).add(part)
    if len(parts) == 1:
        return len(tokens)
    order = sorted(tokens)
    return sum(
        all(parts[j] in tokens[order[start + j]] for j in range(len(parts)))
        and all(
            _adjacent(text, order[start + j - 1], order[start + j]) for j in range(1, len(parts))
        )
        for start in range(len(order) - len(parts) + 1)
    )


def _adjacent(text: str, before: tuple[int, int], after: tuple[int, int]) -> bool:
    """No token between two marked tokens: nothing in the gap is a letter, digit, mark or
    private-use character, the characters FTS5's unicode61 tokenizer keeps."""
    return not any(
        unicodedata.category(char)[0] in "LNM" or unicodedata.category(char) == "Co"
        for char in text[before[1] : after[0]]
    )


def _marked(highlighted: str, open_: str, close: str) -> list[tuple[int, int]]:
    """The (start, end) of each marked token, as offsets into the text without markers."""
    spans, position, start = [], 0, 0
    for char in highlighted:
        if char == open_:
            start = position
        elif char == close:
            spans.append((start, position))
        else:
            position += 1
    return spans


def _markers(texts: Iterator[str]) -> tuple[str, str]:
    """Two characters that occur in none of the texts: control characters first."""
    used = set(chain.from_iterable(texts))
    free = (
        chr(code) for code in chain(range(1, 32), range(0xE000, 0xF900)) if chr(code) not in used
    )
    return next(free), next(free)
