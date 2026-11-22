"""Q2, Q3: the most one search can cost, in statements and rows, counted (never timed).

A search (Store.find, Store.search) runs, on a view of every project:
- 1 statement to see that the account has an index, and 1 for the ids of its projects (a
  narrowed view's reach is resolved by its own statements instead);
- 1 for the claim and token counts of the reach;
- 1 per distinct word that counts, at most WORDS_MAX (32): its document frequency, a count
  that reads at most C + 1 of the word's matches (R6: C = ranking.COUNTED_MAX, 8,000) and
  returns one row;
- 1 for the claims to score, none when no word matches: at most S + 1 rows, the one more
  saying whether the bound applied; when several words take the likeliest path, it walks
  at most C + 1 matches of each word, the newest, to weigh them;
- 1 per distinct part of the words that count (FTS5 splits a word at "_"), at most PARTS_MAX
  (64): the instances of the part in the claims scored, at most S rows each.
S, the claims a query of p distinct parts scores (Q3, ranking.scored_max), is what a fixed
amount of work allows, the work being claims scored times parts: SCORED_WORK // p, SCORED_WORK
= 8,000 (what 8 parts cost at 1,000 claims), never fewer than SCORED_MAX (1,000). So S x p is
8,000 at most up to 8 parts and 1,000 x p from 8 on, the claims to score and their parts cost
S x (p + 1) + 1 rows, at most 65,001 (64 parts), and a search at most 2 + 1 + 32 + 1 + 64 =
100 statements and 1 + P + 1 + 32 + 65,001 = 65,035 + P rows, P the number of the account's
projects, as before Q3. At their bounds, with one project: one part (8,000 claims) 6
statements and 16,005 rows; two parts (4,000) 7 and 12,005; four (2,000) 10 and 10,006; eight
(1,000) 16 and 9,008; 64 (1,000) 100 and 65,036.
Whether the bound applied changes neither count, and nothing in the query raises them: a 33rd
word, a 65th part and a word given a thousand times add none. The counts are those the store's
connection reports to its trace callback and hands to its row factory, as in
tests/acceptance/test_q1_bounded_reads.py.
One statement more, once (SETTINGS): a search runs on one of the store's read-only
connections (cairn.store.base.Readers), and the first time a connection uses a workspace's
index, FTS5 loads the index's settings into it with one statement of its own, `SELECT k, v
FROM '<index>_config'`; that connection's later searches do not run it. Each test below opens
a new store, so its first search pays it, and the counts above are those plus SETTINGS; a
second search on the same store (the cap's, the steps') runs exactly the counts above.
R6: the matches a statement walks to return its rows are bounded too, at most 32 x (C + 1) =
256,032 for the counts and as many for the likeliest walk. The rows above cannot see them,
so they are counted in SQLite's steps: for a query of each size, a corpus three times as
large, every claim matching, costs the same steps (tests/unit/test_search_steps_unit.py
measures one and two words over 9,000 and 27,000 claims).
"""

import contextlib
from types import SimpleNamespace

import pytest

from cairn.memory import searched
from cairn.store import Claim, Store, ranking
from support.store.search_steps import FACTOR, PERIOD, merge, steps

FIXED = 3  # the index's table, the account's projects, the reach's counts
SETTINGS = 1  # FTS5 loading the index's settings into a reader that has not used it yet


def settings_loaded(statements: list[str]) -> int:
    """How many of `statements` are FTS5 loading an index's settings (SETTINGS)."""
    return sum(1 for s in statements if s.startswith("SELECT k, v FROM 'main'.'claim_search_"))


QUIET = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}
WORST_ROWS = 65_035 + 1  # the module docstring's rows, P = 1 project


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def cap(monkeypatch, scored: int) -> None:
    """Every query scores `scored` claims: the fewest any query scores, and an amount of work
    that allows no more (ranking.scored_max)."""
    monkeypatch.setattr(ranking, "SCORED_MAX", scored)
    monkeypatch.setattr(ranking, "SCORED_WORK", scored)


@contextlib.contextmanager
def watched(store: Store):
    seen = SimpleNamespace(statements=[], rows=0)
    before = store._state.observers.rows

    def traced(statement: str) -> None:
        text = statement.lstrip()
        if text and not text.startswith("--") and text.split()[0].upper() not in QUIET:
            seen.statements.append(text)

    def rows(cursor, row):
        seen.rows += 1
        return row if before is None else before(cursor, row)

    store._observe(trace=traced)
    store._observe(rows=rows)
    try:
        yield seen
    finally:
        store._observe(trace=None)
        store._observe(rows=before)


def worst(n: int) -> list[str]:
    """`n` words of two parts each, a part shared by no other word: 2n distinct parts."""
    return [f"a{i}_b{i}" for i in range(n)]


def fill(store: Store, claims: int, words: list[str]) -> None:
    """`claims` claims in one project that each hold every word (its parts side by side),
    on pages of at most 2,000 claims."""
    project = store.create_project("api").id
    text = " ".join(word.replace("_", " ") for word in words)
    for first in range(0, claims, 2_000):
        store.save_page(
            project,
            f"p{first}",
            "P",
            "",
            [
                Claim(id=f"cl_{n}", text=text, span_ids=())
                for n in range(first, min(claims, first + 2_000))
            ],
        )


def bound(words: int, parts: int, scored: int | None = None, projects: int = 1) -> tuple[int, int]:
    """The statements and rows of a search of `words` distinct words holding `parts`
    distinct parts that scores `scored` claims (ranking.scored_max(parts) when not given),
    every one holding every part."""
    scored = ranking.scored_max(parts) if scored is None else scored
    statements = FIXED + words + 1 + parts
    rows = 1 + projects + 1 + words + (scored + 1) + parts * scored
    return statements, rows


def test_the_stated_constants():
    assert (ranking.WORDS_MAX, ranking.PARTS_MAX, ranking.SCORED_MAX) == (32, 64, 1000)
    assert ranking.SCORED_WORK == 8 * ranking.SCORED_MAX == 8_000
    statements, rows = bound(ranking.WORDS_MAX, ranking.PARTS_MAX)
    assert (statements, rows - 1) == (100, 65_035)  # the module docstring's numbers, P = 1
    # The work for each number of parts, and the floor (ranking.scored_max)
    by_parts = {parts: ranking.scored_max(parts) for parts in range(ranking.PARTS_MAX + 1)}
    assert [by_parts[p] for p in (1, 2, 3, 4, 8, 64)] == [8_000, 4_000, 2_666, 2_000, 1_000, 1_000]
    assert min(by_parts.values()) == ranking.SCORED_MAX
    for parts, scored in by_parts.items():
        assert scored * parts <= max(ranking.SCORED_WORK, ranking.SCORED_MAX * parts)
        assert bound(ranking.WORDS_MAX, parts)[1] <= WORST_ROWS


# Q3: a query of each size, at the largest corpus each can score, under the real budget.
# (query, distinct words, distinct parts)
SIZES = [
    (["a0"], 1, 1),
    (["a0_b0"], 1, 2),
    (worst(2), 2, 4),
    (worst(4), 4, 8),
    (worst(ranking.WORDS_MAX), ranking.WORDS_MAX, ranking.PARTS_MAX),
]
# (statements, rows) at each size's bound, one project: the module docstring's numbers
AT_BOUND = {1: (6, 16_005), 2: (7, 12_005), 4: (10, 10_006), 8: (16, 9_008), 64: (100, 65_036)}


@pytest.mark.parametrize(
    ("words", "distinct", "parts"), SIZES, ids=[f"{parts} parts" for *_, parts in SIZES]
)
def test_a_query_of_each_size_scores_its_bound_and_costs_what_the_bound_says(
    store, words, distinct, parts
):
    scored = ranking.scored_max(parts)
    fill(store, scored + 1, words)  # one more than it scores: the bound applies
    with watched(store) as seen:
        found = store.find(" ".join(words), limit=50)
    assert not found.searched.complete and found.searched.claims_ranked == scored
    assert settings_loaded(seen.statements) == SETTINGS  # the one statement more, and why
    assert (len(seen.statements) - SETTINGS, seen.rows) == bound(distinct, parts)
    assert bound(distinct, parts) == AT_BOUND[parts]
    assert len(seen.statements) <= 100 + SETTINGS and seen.rows <= WORST_ROWS
    if parts == 1:  # the answer says so with the new number (cairn.memory.searched)
        said = searched(found.searched)["said"]
        # R6: the word's count stops at its bound, 8,000, so the total is not exact
        assert said.startswith("More than 8,000 claims match, so only the newest 8,000 were")
        assert (found.searched.claims_matched, found.searched.claims_matched_exact) == (
            8_000,
            False,
        )


def test_the_worst_search_costs_what_the_bound_says_and_the_cap_costs_nothing(store, monkeypatch):
    cap(monkeypatch, 4)  # small, so every part is in every claim
    words = worst(ranking.WORDS_MAX)
    fill(store, 10, words)
    with watched(store) as seen:
        found = store.find(" ".join(words), limit=50)
    assert not found.searched.complete and found.searched.claims_ranked == 4
    assert settings_loaded(seen.statements) == SETTINGS
    assert (len(seen.statements) - SETTINGS, seen.rows) == bound(32, 64, 4)
    # Under the cap, the same query runs the same statements (the second search: the reader
    # holds the index's settings, so it runs the bound's statements and no more).
    cap(monkeypatch, 10)
    with watched(store) as uncapped:
        assert store.find(" ".join(words), limit=50).searched.complete
    assert settings_loaded(uncapped.statements) == 0
    assert len(uncapped.statements) == len(seen.statements) - SETTINGS == bound(32, 64, 4)[0]


@pytest.mark.parametrize(
    "extra",
    [
        ["c0"],  # a 33rd word
        ["c0_c1_c2"] * 3,
        ["a0_b0"] * 1000,  # a word given a thousand times after the 32
        ["_".join(f"d{n}" for n in range(1000))],  # one word of 1,000 parts
    ],
)
def test_nothing_after_the_bounds_raises_the_cost(store, monkeypatch, extra):
    cap(monkeypatch, 4)
    words = worst(ranking.WORDS_MAX)
    fill(store, 10, words)
    with watched(store) as seen:
        found = store.find(" ".join(words + extra), limit=50)
    assert found.searched.words_left_out_count == len(extra)
    assert settings_loaded(seen.statements) == SETTINGS
    assert (len(seen.statements) - SETTINGS, seen.rows) == bound(32, 64, 4)


def test_the_parts_bound_comes_before_the_words_bound(store, monkeypatch):
    """Words of three parts: the first 21 hold 63 parts, the 22nd would make 66."""
    cap(monkeypatch, 4)
    words = [f"a{i}_b{i}_c{i}" for i in range(ranking.WORDS_MAX)]
    fill(store, 10, words)
    with watched(store) as seen:
        found = store.find(" ".join(words), limit=50)
    assert (found.searched.words_searched, found.searched.words_left_out_count) == (21, 11)
    assert settings_loaded(seen.statements) == SETTINGS
    assert (len(seen.statements) - SETTINGS, seen.rows) == bound(21, 63, 4)


def test_a_query_that_matches_nothing_costs_a_statement_per_word_and_asks_for_no_claim(store):
    fill(store, 10, ["quill"])
    words = [f"none{n}" for n in range(ranking.WORDS_MAX)]
    with watched(store) as seen:
        found = store.find(" ".join(words))
    assert found.hits == [] and found.searched.complete
    assert settings_loaded(seen.statements) == SETTINGS
    assert len(seen.statements) == FIXED + ranking.WORDS_MAX + SETTINGS  # the counts, no more
    assert all(s.lstrip().upper().startswith("SELECT") for s in seen.statements)
    assert not [s for s in seen.statements if "highlight(" in s or ".text" in s]


# R6: the matches the statements walk stop at their bounds, for a query of each size


@pytest.mark.parametrize(
    ("words", "distinct", "parts"), SIZES, ids=[f"{parts} parts" for *_, parts in SIZES]
)
def test_three_times_the_matches_cost_a_query_of_each_size_the_same_steps(
    tmp_path, monkeypatch, words, distinct, parts
):
    """Every query scores 4 claims and every count stops at 6 (C), over 20 claims that all
    match, then 60: the statements and rows are those the bound says, and the steps the same."""
    cap(monkeypatch, 4)
    monkeypatch.setattr(ranking, "COUNTED_MAX", 6)
    measured = []
    for claims in (20, 60):
        store = Store(tmp_path / f"{claims}.db")
        try:
            fill(store, claims, words)
            merge(store)
            with watched(store) as seen:
                store.find(" ".join(words), limit=50)
            assert settings_loaded(seen.statements) == SETTINGS
            assert (len(seen.statements) - SETTINGS, seen.rows) == bound(distinct, parts, 4)
            measured.append(steps(store, " ".join(words)))
        finally:
            store.close()
    (small, small_by, _), (large, large_by, facts) = measured
    assert (facts.claims_matched, facts.claims_ranked) == ((6, 4) if distinct == 1 else (None, 4))
    assert large <= FACTOR * small + 10 * PERIOD, (small_by, large_by)
