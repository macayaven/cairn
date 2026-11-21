"""The store's one repair (report 8, must-fix 2): every page any version of which cites a
passage the store holds of a note of another project, of its workspace or another, is
rewritten as a forget rewrites a page, held to its own project's notes
(cairn.store.erasure.hold_to_own_projects); the operator's count of such pages reads the same
search and writes nothing (count_pages_citing_other_projects, `stats --other-projects`).

The pages are written as a database an earlier build holds them (tests/earlier_build.py). The
other projects' notes are filed and never compiled, so their words belong in the tables that
hold notes and passages alone: the oracle is a scan of every table. Made-up notes."""

import io
import json
import logging
from contextlib import closing

from test_rt1_forget_workspace_held import (
    borrowed,
    citing,
    filed,
    forgotten_rows,
    passage,
    tables_holding,
)

from cairn.store import Store, erasure
from cairn.web import cli

GONE = "Quill sealed the kqzvtxb crate."  # desk's, workspace one
FAR = "Wren logs the zmvqpr gate."  # yard's, workspace two
WORDS = ("kqzvtxb", "zmvqpr")
NONE = ("workspaces", "rewritten", "removed")


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


def repair(store: Store, account_id: str | None):
    with store._write() as con:
        return erasure.hold_to_own_projects(con, account_id, store._stamp())


def counted(store: Store) -> dict[str, int]:
    with store._read() as con:
        return erasure.count_pages_citing_other_projects(con)


def pages(store: Store, other: Store) -> dict[tuple[str, str], list]:
    """Every page of both workspaces: its claims' (text, passages) and how many versions."""
    found = {}
    for view in (store, other):
        for project in view.list_projects():
            for page in view.list_pages(project.id):
                claims = [(c.text, c.span_ids) for c in view.get_page(project.id, page.slug).claims]
                versions = len(view.page_versions(project.id, page.slug))
                found[(project.name, page.slug)] = [claims, versions]
    return found


def exported(view: Store) -> bytes:
    out = io.BytesIO()
    view.export(out)
    return out.getvalue()


def test_each_page_is_held_to_its_own_projects_notes_one_workspace_at_a_time(caplog):
    with closing(Store(":memory:")) as store:
        other, kept, before = two_workspaces(store)
        one, two = store._account_id, other._account_id
        assert counted(store) == {one: 5, two: 1}

        with caplog.at_level(logging.WARNING, logger="cairn.store"):
            assert repair(store, two) == {"workspaces": 1, "rewritten": 1, "removed": 0}
            assert counted(store) == {one: 5}
            assert repair(store, None) == {"workspaces": 1, "rewritten": 3, "removed": 2}

        assert counted(store) == {}
        assert pages(store, other) == {
            (name, slug): [[(p.text, (p.id,))], 1] for (name, slug), p in kept.items()
        }
        with store._read() as con:
            keys = con.execute("SELECT slug FROM page_keys ORDER BY slug").fetchall()
        assert keys == [("far",), ("minutes",), ("north",), ("outer",)]
        assert {word: tables_holding(store, word) for word in WORDS} == before
        assert forgotten_rows(store) == [(name, "") for name in ("east", "north", "out", "west")]
        assert [(r.args, r.levelname) for r in caplog.records] == [
            ((two, 1, 1, 0), "WARNING"),
            ((one, 5, 3, 2), "WARNING"),
        ]
        said = " ".join(r.getMessage() for r in caplog.records)
        assert not [w for w in ("minutes", "copy", "east", "out", *WORDS) if w in said]
        archives = [exported(store), exported(other)]
    own = ([before["kqzvtxb"], []], [[], before["zmvqpr"]])  # each workspace's note alone
    assert own == ([["notes", "spans"], []], [[], ["notes", "spans"]])
    for archive, held, words in zip(archives, (3, 1), own, strict=True):
        with closing(Store(":memory:")) as fresh:
            assert fresh.import_export(io.BytesIO(archive)).pages == held
            assert [tables_holding(fresh, word) for word in WORDS] == words


def test_on_a_database_with_no_such_page_it_makes_one_search_and_writes_nothing():
    with closing(Store(":memory:")) as store:
        east = store.create_project("east")
        filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n")
        said: list[str] = []
        with store._write() as con:
            changes = con.total_changes
            con.set_trace_callback(said.append)
            try:
                found = erasure.hold_to_own_projects(con, None, store._stamp())
            finally:
                con.set_trace_callback(None)
            assert (found, con.total_changes) == (dict.fromkeys(NONE, 0), changes)
        assert len(said) == 1 and said[0].startswith("SELECT")


def test_stats_counts_such_pages_by_workspace_only_when_asked(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    with closing(Store(db)) as store:
        two_workspaces(store)
        one = store._account_id
    held = db.read_bytes()
    assert cli.main(["stats", "--db", str(db), "--json"]) == 0
    assert "other_projects" not in json.loads(capsys.readouterr().out)
    assert cli.main(["stats", "--db", str(db), "--json", "--other-projects"]) == 0
    by = json.loads(capsys.readouterr().out)["other_projects"]
    assert sorted(by.values()) == [1, 5] and by[one] == 5
    assert cli.main(["stats", "--db", str(db), "--other-projects"]) == 0
    line = [x for x in capsys.readouterr().out.splitlines() if x.startswith("pages citing")]
    first, second = sorted(by)  # by workspace id
    each = f"workspace {first} {by[first]}; workspace {second} {by[second]}"
    assert line == [f"pages citing notes of other projects: 6; {each}"]
    assert db.read_bytes() == held
