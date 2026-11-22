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
from collections import Counter
from contextlib import closing

from cairn.store import Store, erasure
from cairn.web import cli
from support.store.hold_to_own_projects import WORDS, spread, two_workspaces
from support.store.rt1_forget_workspace_held import (
    filed,
    forgotten_rows,
    tables_holding,
)
from support.store.rt1_own_project_held import clean_and_hit

NONE = ("workspaces", "rewritten", "removed")


def repair(store: Store, account_id: str | None):
    """The repair, its counts said as schema step 32 says them: once its transaction commits."""
    said: list[tuple] = []
    with store._write() as con:
        done = erasure.hold_to_own_projects(
            con, account_id, store._stamp(), lambda *counts: said.append(counts)
        )
    for counts in said:
        erasure._log_repaired(*counts)
    return done


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


def search_work(clean: int, hit: int, build=clean_and_hit) -> tuple[int, int]:
    """The repair's searches alone, those that read page versions to find the next page to
    repair (erasure._NEXT_PROJECT, _NEXT_PAGE), on `hit` pages that cite another project's
    note among `clean` pages that cite none (test_rt1_own_project_held.clean_and_hit, all in
    one project, or `spread`): SQLite's work in units of 100 virtual-machine steps, counted
    not timed, and how many searches they were."""
    with closing(build(clean, hit)) as store:
        steps, at, asked = Counter(), [""], [0]

        def tick() -> int:
            steps[at[0]] += 1
            return 0

        def trace(statement: str) -> None:
            at[0] = statement
            asked[0] += "ORDER BY p.slug LIMIT 1" in statement

        with store._write() as con:
            con.set_trace_callback(trace)
            con.set_progress_handler(tick, 100)
            try:
                erasure.hold_to_own_projects(con, None, store._stamp())
            finally:
                con.set_progress_handler(None, 0)
                con.set_trace_callback(None)
        searched = sum(
            n for statement, n in steps.items() if "ORDER BY p.slug LIMIT 1" in statement
        )
        return searched, asked[0]


def test_the_repair_searches_the_page_versions_once_however_many_pages_it_repairs():
    """The tester's 40 pages among 1,500 clean ones (report: 40 full searches before): the
    searches of the 40 pages among them cost the clean pages' one search and the 40 pages',
    one search for the project and then one from each page repaired, by the keys."""
    alone, once = search_work(1500, 0)
    pages, asked = search_work(0, 40)
    both, asked_both = search_work(1500, 40)
    assert once == 1 and asked == asked_both == 42
    assert both - pages <= alone + alone // 10, (alone, pages, both)


def test_pages_spread_over_many_projects_cost_no_more_than_their_number():
    """The projects are walked by the index from the last one searched, never from the
    workspace's first again: 160 pages in 160 projects cost about four times 40, and a
    workspace of clean pages beside them its one search."""
    alone, _ = search_work(1500, 0, spread)
    forty, _ = search_work(0, 40, spread)
    many, asked = search_work(0, 160, spread)
    both, _ = search_work(1500, 160, spread)
    assert asked == 2 * 160 + 1  # each project's search, and one after its page
    assert many <= 5 * forty, (forty, many)
    assert both - many <= alone + alone // 10, (alone, many, both)
