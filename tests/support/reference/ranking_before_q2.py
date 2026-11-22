# Reference copy for the Q2 differential test (tests/unit/test_search_differential_unit.py).
# Below these three lines: src/cairn/store/ranking.py byte for byte as of commit b6b0eaa,
# before Q2 made search say what it left out. Nothing imports it but that test. Do not edit.
"""Search: claims matched in the account's FTS5 index and ranked with BM25 over the projects
the caller reaches, the same code for every view.

FTS5's own bm25() weighs a term by statistics over its whole table, every project of the
account. Here they come from the reach alone, so a search's hits and scores depend only on
the projects the caller reaches:

- N and the average claim length come from `search_projects`, the claim and token counts of
  each project, which save_page keeps in its own transaction (index_page);
- a word's document frequency is a count of the reachable claims it matches;
- a word's frequency in a claim, and the claim's length, are those FTS5 itself would use:
  the instances `highlight()` marks, and the token count FTS5 stored for the row;
- the formula and constants are FTS5's: k1 = 1.2, b = 0.75, IDF = ln((N - n + 0.5) /
  (n + 0.5)) floored at 1e-6, summed over the query's words (a word given twice counts
  twice). Scores are positive, higher is better, as the store has always returned them.

Matching walks each word's entries in the account's index, and keeps a match only when
`search_claims` (row to project, by primary key) puts it in reach: a claim out of reach is
never read. Only the SCORED_MAX most recently indexed matches are scored; N and the
document frequencies still count every reachable claim, so a scored claim's score is the
same whether or not the cap applied. Python rounds where SQLite's C may fuse a multiply and
an add, so a score can differ from bm25()'s in its last bits, never in order.
"""

import json
import math
import re
import sqlite3
import unicodedata
from collections.abc import Collection, Iterator, Sequence
from itertools import chain

from cairn.store.models import Claim, SearchHit

K1 = 1.2
B = 0.75
IDF_FLOOR = 1e-6
WORDS_MAX = 32  # words of a query that count; any after them are ignored
SCORED_MAX = 1000  # matches scored per query: the most recently indexed reachable ones
_TITLE, _TEXT = 4, 5  # the index's searched columns (schema._CLAIM_INDEX)


def words(query: str) -> list[str]:
    """The words of a query: its first WORDS_MAX runs of word characters. Each becomes one
    quoted FTS5 phrase, so no user text is ever read as query syntax."""
    return re.findall(r"\w+", query)[:WORDS_MAX]


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
) -> list[SearchHit]:
    """The best `limit` claims of the projects in `reach` (and in `project_ids`, when given) for
    `query_words`, best first, ties in index order. `reach` is also the corpus every
    statistic counts."""
    shown = set(reach) if project_ids is None else set(reach) & set(project_ids)
    if not shown:
        return []
    in_reach = json.dumps(sorted(reach))
    n_claims, n_tokens = con.execute(
        "SELECT coalesce(sum(claims), 0), coalesce(sum(tokens), 0) FROM search_projects"
        " WHERE project_id IN (SELECT value FROM json_each(?))",
        (in_reach,),
    ).fetchone()
    if not n_claims:
        return []
    matched = _JOINED.format(index=index)
    candidates = con.execute(
        f"SELECT {index}.rowid, c.tokens, c.project_id, projects.name, {index}.slug,"
        f" {index}.title, {index}.claim_id, {index}.text, {index}.span_ids"
        f" {matched} JOIN projects ON projects.id = c.project_id"
        f" WHERE {index} MATCH ? AND c.project_id IN (SELECT value FROM json_each(?))"
        f" ORDER BY {index}.rowid DESC LIMIT ?",
        (account_id, _phrases(query_words), json.dumps(sorted(shown)), SCORED_MAX),
    ).fetchall()
    if not candidates:
        return []

    idf = {}
    for word in dict.fromkeys(query_words):
        (found,) = con.execute(
            f"SELECT count(*) {matched}"
            f" WHERE {index} MATCH ? AND c.project_id IN (SELECT value FROM json_each(?))",
            (account_id, _phrases([word]), in_reach),
        ).fetchone()
        value = math.log((n_claims - found + 0.5) / (found + 0.5))
        idf[word] = IDF_FLOOR if value <= 0.0 else value

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
    return [
        SearchHit(
            project_id=project_id,
            project=project,
            slug=slug,
            title=title,
            claim_id=claim_id,
            text=text,
            span_ids=tuple(json.loads(span_ids)),
            score=-negative,
        )
        for negative, _, (project_id, project, slug, title, claim_id, text, span_ids) in scored[
            :limit
        ]
    ]


# A match in the account's index, with its row's project and length, read by primary key.
_JOINED = "FROM {index} JOIN search_claims AS c ON c.account_id = ? AND c.row = {index}.rowid"


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
