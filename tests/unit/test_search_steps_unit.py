"""R6: a search's work does not grow with the claims that match it.

Decisions 38, 118 and 124 bound the work of one search, whoever asks. The work is counted in
SQLite's virtual machine steps (its progress handler), never timed: a search of one word, and
one of two, over a corpus where they match N claims, then 3N, N above every bound of
cairn.store.ranking. The steps at 3N stay within a small constant factor of those at N, for
the whole search and for each of its statements.

Both corpora have their index merged into one segment, as FTS5's own merging tends to: a
claim read by row seeks it in each segment of the index that holds the word, so the steps
of reading a claim follow the index's segments, which FTS5 keeps few, and not the matches.
Here the matches alone differ.
"""

from collections.abc import Callable

import pytest

from cairn.store import Claim, Store, ranking

PERIOD = 50  # steps between two calls of the progress handler
FACTOR = 1.1  # what 3N may cost against N: the same work, give or take the index's pages


def corpus(folder, claims: int, text: str) -> Store:
    """`claims` claims in one project, each `text` and a word of its own, on pages of at most
    2,000 claims, the index then merged into one segment."""
    store = Store(folder / "cairn.db")
    project = store.create_project("api").id
    for first in range(0, claims, 2_000):
        store.save_page(
            project,
            f"p{first}",
            "P",
            "",
            [
                Claim(id=f"cl_{n}", text=f"{text} mark{n}.", span_ids=())
                for n in range(first, min(claims, first + 2_000))
            ],
        )
    merge(store)
    return store


def merge(store: Store) -> None:
    """The account's index merged into one segment (FTS5's 'optimize')."""
    store._con.execute(f"INSERT INTO {store._index} ({store._index}) VALUES ('optimize')")
    store._con.commit()


def steps(store: Store, query: str) -> tuple[int, dict[str, int], object]:
    """The steps of one Store.find: in all, and by statement (its first 40 characters, the
    steps of statements that begin alike added up), with what it says it searched."""
    seen = {"steps": 0}
    marks: list[tuple[int, str]] = []

    def counted() -> int:
        seen["steps"] += PERIOD
        return 0

    def traced(statement: str) -> None:
        if not statement.lstrip().startswith("--"):  # not FTS5's own statements
            marks.append((seen["steps"], statement.lstrip()[:40]))

    store._observe(progress=(counted, PERIOD))
    store._observe(trace=traced)
    try:
        found = store.find(query, limit=10)
    finally:
        store._observe(progress=None)
        store._observe(trace=None)
    by: dict[str, int] = {}
    for n, (start, statement) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else seen["steps"]
        by[statement] = by.get(statement, 0) + end - start
    return seen["steps"], by, found.searched


N = 9_000  # matches of the smaller corpus


@pytest.fixture(scope="module")
def measured(tmp_path_factory) -> Callable[[str, str], tuple]:
    built: dict[tuple[int, str], Store] = {}

    def measure(text: str, query: str) -> tuple:
        out = []
        for claims in (N, 3 * N):
            if (claims, text) not in built:
                folder = tmp_path_factory.mktemp(f"c{claims}")
                built[claims, text] = corpus(folder, claims, text)
            out.append(steps(built[claims, text], query))
        return tuple(out)

    yield measure
    for store in built.values():
        store.close()


def test_n_is_above_every_bound_of_the_module():
    bounds = (ranking.SCORED_WORK, ranking.SCORED_MAX, ranking.scored_max(1), ranking.COUNTED_MAX)
    assert max(bounds) < N


@pytest.mark.parametrize(
    ("text", "query"),
    [("Quill marks the row", "quill"), ("Quill relay marks the row", "quill relay")],
    ids=["one word", "two words"],
)
def test_a_search_over_3n_matches_costs_what_one_over_n_costs(measured, text, query):
    (small, small_by, _), (large, large_by, _) = measured(text, query)
    assert large <= FACTOR * small, (small, large, small_by, large_by)
    for statement, cost in large_by.items():  # each statement, the count first among them
        assert cost <= FACTOR * small_by[statement] + 10 * PERIOD, (statement, small_by, large_by)
