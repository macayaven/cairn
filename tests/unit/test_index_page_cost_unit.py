"""What saving a page costs SQLite does not grow with the workspace: ranking.index_page takes
the page's rows out of the full-text index by their rowids, which search_claims holds by
page (search_claims_by_page), never by the index's own columns, which FTS5 does not index and
so reads every row, every claim's text with it, to find. Counted in units of 100 of SQLite's
virtual-machine steps, never in time: one page saved, and one note compiled onto a page, in
a workspace of 0, 1,500 and 6,000 other pages, and of 0, 1,500 and 6,000 other projects (report
10, finding 5: the lookup of a stored version's passages asked its project's reach by
enumerating the workspace's projects; the check of a forgotten note still asks each of them,
which save_page's description states). And the guard on which that rests: the index
and search_claims hold the same rows, each kind of write the store makes keeping them so.
Made-up notes."""

import io
from contextlib import closing

import pytest
from test_hold_to_own_projects_unit import spread
from test_rt1_forget_workspace_held import GONE, borrowed, citing, filed, passage
from test_store_unit import v4_database

from cairn.arrange import tidy_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store, erasure
from cairn.store.base import AUDIT_KEEP
from cairn.store.usage import Limits
from cairn.web.cli import KEEP_VERSIONS

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


# How the statements that ask each of the workspace's projects read: _OWN_PROJECT, and the
# check of a forgotten note (memory._cites_forgotten), as SQLite expands them.
ASKS_EACH_PROJECT = "IN (SELECT id FROM projects WHERE account_id = "


def counted_apart(store: Store, act) -> tuple[int, int]:
    """As counted, in two: (the units of the statements that ask each of the workspace's
    projects, the units of every other)."""
    steps = {True: 0, False: 0}
    asking = [False]

    def trace(sql: str) -> None:
        asking[0] = ASKS_EACH_PROJECT in sql

    def tick() -> int:
        steps[asking[0]] += 1
        return 0

    store._observe(trace=trace, progress=(tick, 100))
    try:
        act()
    finally:
        store._observe(trace=None, progress=None)
    return steps[True], steps[False]


def save_one(others: int, beside: str = "pages", count=counted):
    store, desk = workspace(others, beside)
    with closing(store):
        page = store.get_page(desk, "drift")
        return count(
            store, lambda: store.save_page(desk, "drift", page.title, page.body, page.claims)
        )


def compile_one(others: int, beside: str = "pages", count=counted):
    store, desk = workspace(others, beside)
    with closing(store):
        note, _ = store.add_note(desk, "# Drift\n\nDrift keeps a log.\n")
        outcome = []
        work = count(store, lambda: outcome.append(compile_note(store, note, gate=lint_gate)))
        assert outcome[0].status == "saved", outcome
        return work


@pytest.mark.parametrize("act", [save_one, compile_one], ids=["save_page", "compile_note"])
def test_a_save_costs_the_same_in_a_workspace_of_0_1500_and_6000_other_pages(act):
    work = [act(size) for size in SIZES]  # 7, 7, 8 and 9, 9, 10; 7, 547, 2,168 before the fix
    assert max(work) - min(work) <= 3, dict(zip(SIZES, work, strict=True))


def flat_but_for_asking_each_project(work: list[tuple[int, int]], asks: int) -> None:
    """Beside 0, 1,500 and 6,000 other projects: every statement but those that ask each of
    the workspace's projects costs the same, and those, `asks` of them, grow by about one
    unit (100 steps) for each ten projects each, as save_page's description states."""
    said = dict(zip(SIZES, work, strict=True))
    rest = [other for _, other in work]
    assert max(rest) - min(rest) <= 3, said
    for size, (asking, _) in said.items():
        assert asking <= asks * (size // 10 + 1), said


def test_a_save_beside_other_projects_grows_by_the_check_of_a_forgotten_note_alone():
    # (asking, the rest): (0, 7), (150, 7), (600, 7); 7, 157, 607 in all, before as after
    flat_but_for_asking_each_project([save_one(s, "projects", counted_apart) for s in SIZES], 1)


def test_a_compile_beside_other_projects_grows_by_the_reads_that_ask_each_project():
    # (asking, the rest): (0, 9), (390, 9), (1,560, 9): the forgotten check, the page's read
    # and its topic key's (_OWN_PROJECT); before the lookup's fix the rest was 9, 129, 489
    work = [compile_one(s, "projects", counted_apart) for s in SIZES]
    flat_but_for_asking_each_project(work, 3)


def look_up(others: int) -> int:
    """The stored-version lookup of compiler.own_claims, beside `others` other projects."""
    store, desk = workspace(others, "projects")
    with closing(store):
        version = store.get_page(desk, "drift").version
        return counted(store, lambda: store.passages_of_other_projects(desk, "drift", version))


def test_the_lookup_of_a_stored_versions_passages_asks_its_project_alone():
    work = [look_up(size) for size in SIZES]  # 0, 120, 480 before the fix
    assert max(work) - min(work) <= 1, dict(zip(SIZES, work, strict=True))


def served(others: int, capped: bool, beside: str = "pages", count=counted):
    """save_one under what `serve` runs with by default (report 9, must-fix 5): each page's
    newest KEEP_VERSIONS versions kept and the audit log's newest AUDIT_KEEP events, and,
    `capped`, a storage cap the workspace is well within (_keep_history reads its size)."""
    store, desk = workspace(others, beside)
    with closing(store):
        store.bound_history(KEEP_VERSIONS)
        store.audit_keep = AUDIT_KEEP
        view = store.limited(Limits(storage_bytes=10**9)) if capped else store
        page = view.get_page(desk, "drift")
        return count(
            store, lambda: view.save_page(desk, "drift", page.title, page.body, page.claims)
        )


@pytest.mark.parametrize("capped", [False, True], ids=["serve's defaults", "storage cap"])
def test_a_save_costs_the_same_under_the_servers_defaults(capped):
    work = [served(size, capped) for size in SIZES]  # 7, 82, 308 before the fix
    assert max(work) - min(work) <= 3, dict(zip(SIZES, work, strict=True))


@pytest.mark.parametrize("capped", [False, True], ids=["serve's defaults", "storage cap"])
def test_under_the_servers_defaults_a_save_beside_other_projects_grows_by_that_check(capped):
    work = [served(size, capped, "projects", counted_apart) for size in SIZES]
    flat_but_for_asking_each_project(work, 1)  # 7, 157, 607 in all, as the reviewer counted


# The guard: every row of the workspace's index has its search_claims row, with the page it
# is of, and the reverse, after each kind of write the store makes.


def rows_agree(store: Store) -> None:
    with store._read() as con:
        index = dict(
            (row, (project, slug))
            for row, project, slug in con.execute(
                f"SELECT rowid, project_id, slug FROM {store._index}"
            )
        )
        claims = dict(
            (row, (project, slug))
            for row, project, slug in con.execute(
                "SELECT row, project_id, slug FROM search_claims WHERE account_id = ?",
                (store._account_id,),
            )
        )
    assert index == claims


def test_the_index_and_search_claims_hold_the_same_rows_after_every_kind_of_write():
    with closing(Store(":memory:")) as store:
        memory = Memory(store, lint_gate)
        desk, east = store.create_project("desk"), store.create_project("east")
        note = filed(store, desk.id, f"# Dispatch\n\n{GONE}\n\nThe crate ships at dawn.\n")
        rows_agree(store)  # a compile
        memory.note("desk", "# Dispatch\n\nThe crate ships at noon.\n")
        rows_agree(store)  # a compile onto a page
        page = store.get_page(desk.id, "dispatch")
        store.save_page(desk.id, "dispatch", page.title, page.body, tuple(reversed(page.claims)))
        rows_agree(store)  # a save
        assert tidy_page(store, desk.id, "dispatch").status in ("arranged", "unchanged")
        rows_agree(store)  # the tidy
        own = passage(store, filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n"))
        borrowed(
            store,
            east.id,
            "minutes",
            citing(1, own.text, own),
            citing(2, GONE, passage(store, note)),
        )
        rows_agree(store)  # a page written as an earlier build wrote it
        with store._write() as con:
            erasure.hold_to_own_projects(con, None, store._stamp())
        rows_agree(store)  # the repair
        memory.forget("desk", note.id)
        rows_agree(store)  # a forget
        out = io.BytesIO()
        store.export(out)
        with closing(Store(":memory:")) as fresh:
            fresh.import_export(io.BytesIO(out.getvalue()))
            rows_agree(fresh)  # an import
        rows_agree(store)
        assert store.delete_project(east.id)
        rows_agree(store)  # a project deleted


def test_the_rows_agree_after_the_repair_of_pages_spread_over_projects():
    with closing(spread(30, 20)) as store:
        with store._write() as con:
            erasure.hold_to_own_projects(con, None, store._stamp())
        rows_agree(store)


def test_the_rows_agree_in_every_workspace_of_a_database_upgraded_from_before_step_5(tmp_path):
    """Schema step 5 made search_claims and counted every row an older index held
    (ranking.count_index): each workspace's index and its search_claims rows agree after the
    upgrade, and after a page of the upgraded database is saved again."""
    db = tmp_path / "v4.db"
    v4_database(db)
    with closing(Store(db)) as store:
        with store._read() as con:
            accounts = [a for (a,) in con.execute("SELECT id FROM accounts ORDER BY seq")]
        held = 0
        for account in accounts:
            view = store.for_account(account)
            with view._read() as con:
                made = con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (view._index,))
                if made.fetchone() is None:  # no page yet: no index, and no row to count
                    assert not con.execute(
                        "SELECT 1 FROM search_claims WHERE account_id = ?", (account,)
                    ).fetchone()
                    continue
                held += con.execute(f"SELECT count(*) FROM {view._index}").fetchone()[0]
            rows_agree(view)
        assert held, "the database holds no index row to count"
        with store._read() as con:
            account, project, slug = con.execute(
                "SELECT account_id, project_id, slug FROM search_claims LIMIT 1"
            ).fetchone()
        view = store.for_account(account)
        page = view.get_page(project, slug)
        view.save_page(project, slug, page.title, page.body, page.claims)
        rows_agree(view)
