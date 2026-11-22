"""Shared test helpers extracted from test_search_steps_unit."""

from cairn.store import Store

PERIOD = 50  # steps between two calls of the progress handler

FACTOR = 1.1  # what 3N may cost against N: the same work, give or take the index's pages


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
