"""Shared test helpers extracted from test_index_page_cost_unit."""

from cairn.store import Store
from support.store.rt1_forget_workspace_held import borrowed, citing, filed, passage

SIZES = (0, 1500, 6000)


def workspace(others: int, beside: str = "pages") -> tuple[Store, str]:
    """A workspace whose project `desk` holds `others` pages of one claim each (or, `beside`
    "projects", that has `others` other projects, empty), and a page `drift` of its own,
    compiled from a note, its index merged into one segment. (the store, desk's id.)

    The merge is what keeps the count to the store's own work: FTS5's secure-delete looks a
    deleted row's terms up in each segment of the index, and how many segments there are at a
    given moment is FTS5's merging, a few dozen at most (6 to 20 here, a save costing 10 to
    24), never the number of pages."""
    store = Store(":memory:")
    desk = store.create_project("desk")
    own = passage(store, filed(store, desk.id, "# Drift\n\nDrift mirrors two buckets.\n"))
    for n in range(others):
        if beside == "projects":
            store.create_project(f"other-{n:05d}")
        else:
            borrowed(store, desk.id, f"other-{n:05d}", citing(n, own.text, own))
    with store._write() as con:
        con.execute(f"INSERT INTO {store._index}({store._index}) VALUES ('optimize')")
    return store, desk.id


def counted(store: Store, act) -> int:
    steps = [0]

    def tick() -> int:
        steps[0] += 1
        return 0

    store._observe(progress=(tick, 100))
    try:
        act()
    finally:
        store._observe(progress=None)
    return steps[0]
