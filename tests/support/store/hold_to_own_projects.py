"""Shared test helpers extracted from test_hold_to_own_projects_unit."""

from cairn.store import Store
from support.store.rt1_forget_workspace_held import (
    borrowed,
    citing,
    filed,
    passage,
    tables_holding,
)
from support.store.rt1_own_project_held import SRC

GONE = "Quill sealed the kqzvtxb crate."  # desk's, workspace one

FAR = "Wren logs the zmvqpr gate."  # yard's, workspace two

WORDS = ("kqzvtxb", "zmvqpr")


def two_workspaces(store: Store):
    """Workspace one: desk's note (GONE), never compiled; east's `minutes` cites it beside its
    own claim (a) and `copy` alone; west's `far` in an earlier version only (d); north's
    `north` cites it and yard's note of workspace two beside its own claim, `both` the two
    alone (c). Workspace two: yard's note (FAR), never compiled; out's `outer` cites desk's
    note beside its own claim (b). (the second workspace's view, the passages kept by page,
    the tables each word was in before any page cited it.)"""
    desk, east, west, north = (store.create_project(n) for n in ("desk", "east", "west", "north"))
    other = store.for_account(store.create_account("Fieldwork").id)
    yard, out = other.create_project("yard"), other.create_project("out")
    d = passage(store, store.add_note(desk.id, f"# Dispatch\n\n{GONE}\n")[0])
    f = passage(other, other.add_note(yard.id, f"# Yard\n\n{FAR}\n")[0])
    e = passage(store, filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n"))
    w = passage(store, filed(store, west.id, "# Far\n\nWren keeps the far log.\n"))
    n = passage(store, filed(store, north.id, "# North\n\nThe north gate shuts.\n"))
    o = passage(other, filed(other, out.id, "# Outer\n\nThe outer door sticks.\n"))
    before = {word: tables_holding(store, word) for word in WORDS}
    borrowed(store, east.id, "minutes", citing(1, e.text, e), citing(2, GONE, d))
    borrowed(store, east.id, "copy", citing(3, GONE, d))
    borrowed(store, west.id, "far", citing(4, GONE, d), citing(5, w.text, w))
    borrowed(store, west.id, "far", citing(5, w.text, w))
    borrowed(store, north.id, "north", citing(6, n.text, n), citing(7, GONE, d), citing(8, FAR, f))
    borrowed(store, north.id, "both", citing(9, GONE, d), citing(10, FAR, f))
    borrowed(other, out.id, "outer", citing(11, o.text, o), citing(12, GONE, d))
    kept = {
        ("east", "minutes"): e,
        ("west", "far"): w,
        ("north", "north"): n,
        ("out", "outer"): o,
    }
    return other, kept, before


def spread(clean: int, hit: int) -> Store:
    """Each of `hit` pages in a project of its own of one workspace, citing a note of another
    of its projects, and `clean` pages in one project of a second workspace."""
    store = Store(":memory:")
    other = store.for_account(store.create_account("Other").id)
    desk = store.create_project("desk")
    src = passage(store, filed(store, desk.id, f"# Src\n\n{SRC}\n"))
    for n in range(hit):
        own_project = store.create_project(f"p{n:04d}")
        own = passage(store, filed(store, own_project.id, "# Own\n\nThe ledger opens.\n"))
        borrowed(store, own_project.id, "hit", citing(1, own.text, own), citing(2, SRC, src))
    far = other.create_project("far")
    own = passage(other, filed(other, far.id, "# Own\n\nThe ledger opens.\n"))
    for n in range(clean):
        borrowed(other, far.id, f"clean-{n:05d}", citing(1, own.text, own))
    return store
