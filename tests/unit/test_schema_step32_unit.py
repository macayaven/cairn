"""Schema step 32 (report 8, must-fix 2): the upgrade holds every page to its own project's
notes, once, over the whole database (cairn.store.erasure.hold_to_own_projects), in a step of
its own that raises the floor.

A file at schema 31 is one this build's store made (step 32 adds and changes no table),
stamped 31 with its floor at 31. A file at schema 28 is built from history and given the
same rows. Each holds the pages of test_hold_to_own_projects_unit.two_workspaces: (a) a page
citing another project's note of its workspace, (b) another workspace's note, (c) both,
beside its own claims and alone, (d) the citation in an earlier version only. Opened by this
build, each is repaired, each workspace's export imports, and a scan of every table finds
each other project's words only where they were before any page cited them. Made-up notes."""

import io
import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient
from test_earlier_page_writers_unit import BASE, SLUG, legacy
from test_hold_to_own_projects_unit import WORDS, two_workspaces
from test_hosted_accounts_unit import v27_database
from test_rt1_forget_workspace_held import borrowed, citing, filed, passage, tables_holding
from test_store_unit import connect, rows, user_version

import cairn.store.schema as schema
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.server import create_app
from cairn.store import Claim, Store, erasure
from cairn.store.base import _index_name
from cairn.store.memory import _write_page_version

NAMES = ("desk", "east", "west", "north", "yard", "out")


def made(db: Path):
    """two_workspaces in a new file at this build's schema. (the second workspace's id, the
    passages kept by page, the tables each word was in before any page cited it.)"""
    with closing(Store(db)) as store:
        other, kept, before = two_workspaces(store)
        return other._account_id, kept, before


def stamped_31(db: Path) -> None:
    with connect(db) as con:
        con.execute("PRAGMA user_version = 31")
        con.execute("UPDATE oldest_build SET version = 31")
        con.commit()
    assert user_version(db) == 31 and floor(db) == [(31,)]


def at_28(db: Path, source: Path) -> None:
    """A file at schema 28 as cairn's own steps left it (built from history), holding the
    workspaces, projects, notes, passages and topic keys of `source`, and each of its page
    versions written as the store writes one, with its search rows and statistics: the
    tables they live in are the same at 28."""
    v27_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        schema._MIGRATIONS[schema._CREDENTIAL_TOKENS](con)
        con.execute("PRAGMA user_version = 28")
        con.commit()
    with connect(db) as con:
        con.execute("ATTACH DATABASE ? AS made", (str(source),))
        con.execute("BEGIN")
        for table in ("accounts", "projects", "notes", "spans", "page_keys"):
            named = [row[1] for row in con.execute(f"PRAGMA main.table_info({table})")]
            columns = ", ".join(n for n in named if n != "seq")  # an account's: made anew
            con.execute(
                f"INSERT OR IGNORE INTO main.{table} ({columns}) SELECT {columns} FROM made.{table}"
            )
        versions = con.execute(
            "SELECT projects.account_id, p.* FROM made.page_versions AS p"
            " JOIN made.projects ON projects.id = p.project_id ORDER BY 2, 3, 4"
        ).fetchall()
        for account, *row in versions:
            claims = [Claim(**claim) for claim in json.loads(row[5])]
            _write_page_version(con, account, _index_name(account), tuple(row), claims)
        con.commit()
    names = rows(db, "SELECT name FROM projects WHERE account_id != 'acct_aaaaaaaaaaaa'")
    assert sorted(names) == sorted((n,) for n in NAMES) and user_version(db) == 28


def floor(db: Path) -> list[tuple]:
    return rows(db, "SELECT version FROM oldest_build")


def repaired(db: Path, two: str, kept: dict, before: dict, caplog) -> None:
    """Open `db` with this build and check what step 32 did to the pages of two_workspaces."""
    with caplog.at_level(logging.WARNING, logger="cairn.store"):
        store = Store(db)
    with closing(store):
        other = store.for_account(two)
        one = store._account_id
        assert user_version(db) == schema.SCHEMA_VERSION == 32 and floor(db) == [(32,)]
        with store._read() as con:
            assert erasure.count_pages_citing_other_projects(con) == {}
        found = {}
        for view in (store, other):
            for project in view.list_projects():
                for page in view.list_pages(project.id):
                    claims = view.get_page(project.id, page.slug).claims
                    versions = len(view.page_versions(project.id, page.slug))
                    found[(project.name, page.slug)] = [[(c.text, c.span_ids) for c in claims]]
                    found[(project.name, page.slug)].append(versions)
        assert {k: v for k, v in found.items() if k[0] in NAMES} == {
            (name, slug): [[(p.text, (p.id,))], 1] for (name, slug), p in kept.items()
        }
        assert {word: tables_holding(store, word) for word in WORDS} == before
        repairs = [r.args for r in caplog.records if r.getMessage().startswith("repair:")]
        assert sorted(repairs) == sorted([(one, 5, 3, 2), (two, 1, 1, 0)])
        archives = []
        for view in (store, other):
            out = io.BytesIO()
            archives.append((view.export(out).pages, out.getvalue()))
    own = ([before["kqzvtxb"], []], [[], before["zmvqpr"]])  # each workspace's note alone
    for (pages, archive), words in zip(archives, own, strict=True):
        with closing(Store(":memory:")) as fresh:
            assert fresh.import_export(io.BytesIO(archive)).pages == pages
            assert [tables_holding(fresh, word) for word in WORDS] == words


def test_a_file_at_31_holding_such_pages_opens_with_each_held_to_its_own_projects_notes(
    tmp_path, caplog
):
    db = tmp_path / "cairn.db"
    two, kept, before = made(db)
    stamped_31(db)
    repaired(db, two, kept, before, caplog)


def test_a_file_at_28_goes_through_the_same(tmp_path, caplog):
    source, db = tmp_path / "made.db", tmp_path / "cairn.db"
    two, kept, before = made(source)
    at_28(db, source)
    repaired(db, two, kept, before, caplog)


def test_a_page_citing_a_note_its_workspace_never_saw_forgotten_is_held_too(tmp_path):
    """An earlier build saved east's page citing a note of another workspace, and that
    workspace then forgot the note, a forget that searches its own workspace alone: east's
    page cites a passage the store no longer holds, which is not of its own project's notes
    either. Opened by this build, the page is held to its own project's notes as any other:
    its export imports, and a note posted by a key that may write east alone is saved."""
    db = tmp_path / "cairn.db"
    with closing(Store(db)) as store:
        east, cited = legacy(store, "workspace")
        (far,) = rows(
            db,
            "SELECT projects.account_id FROM notes JOIN projects ON projects.id = notes.project_id"
            f" WHERE notes.id = '{cited.source_id}'",
        )
        there = store.for_account(far[0])
        assert Memory(there, lint_gate).forget("desk", cited.source_id)["elsewhere"] == 0
        assert any(cited.id in c.span_ids for c in store.get_page(east.id, SLUG).claims)
    stamped_31(db)
    with closing(Store(db)) as store:
        page = store.get_page(east.id, SLUG)
        assert store.page_versions(east.id, SLUG) == [page.version]
        assert not [c for c in page.claims if cited.id in c.span_ids]
        out = io.BytesIO()
        store.export(out)
        with closing(Store(":memory:")) as fresh:
            assert fresh.import_export(io.BytesIO(out.getvalue())).pages == 1
        key = store.create_key("t", projects=[east.id])
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            r = client.post(
                "/api/projects/east/notes",
                headers={"x-api-key": key},
                json={"text": "# Drift sync\n\nDrift rings the bell at two.\n"},
            )
        assert (r.status_code, r.json()["status"]) == (201, "saved"), r.text


def table_rows(db: Path) -> dict[str, list]:
    """Every row of every table but the floor's, as SQLite reads it."""
    with connect(db) as con:
        names = [n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {
            name: sorted(map(repr, con.execute(f'SELECT * FROM "{name}"')))
            for name in names
            if name != "oldest_build"
        }


def test_a_file_with_no_such_page_opens_with_one_search_and_no_other_write(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    with closing(Store(db)) as store:
        east, west = store.create_project("east"), store.create_project("west")
        filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n")
        filed(store, west.id, "# Far\n\nWren keeps the far log.\n")
    stamped_31(db)
    held = table_rows(db)
    said: list[str] = []
    step = schema._MIGRATIONS[32]

    def traced(con: sqlite3.Connection) -> None:
        con.set_trace_callback(said.append)
        try:
            step(con)
        finally:
            con.set_trace_callback(None)

    monkeypatch.setitem(schema._MIGRATIONS, 32, traced)
    Store(db).close()
    assert len(said) == 1 and said[0].startswith("SELECT"), said
    assert table_rows(db) == held
    assert user_version(db) == 32 and floor(db) == [(32,)]


# Bounded: the step holds one page and one workspace's counts at a time (as step 31 holds one
# event: test_schema_step31_bounded_unit). Counted in rows handed over by one cursor, and in
# the items the step's own containers hold, never in bytes or time.


class Cursor:
    def __init__(self, cursor: sqlite3.Cursor, most: list[int]) -> None:
        self._cursor, self._most, self._read = cursor, most, 0

    def _took(self, n: int) -> None:
        self._read += n
        self._most[0] = max(self._most[0], self._read)

    def fetchall(self) -> list[Any]:
        found = self._cursor.fetchall()
        self._took(len(found))
        return found

    def fetchone(self) -> Any:
        found = self._cursor.fetchone()
        self._took(found is not None)
        return found

    def fetchmany(self, size: int = 1) -> list[Any]:
        found = self._cursor.fetchmany(size)
        self._took(len(found))
        return found

    def __iter__(self):
        for row in self._cursor:
            self._took(1)
            yield row

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class Counting(sqlite3.Connection):
    """Every cursor counts the rows it hands over; `most` is the most any one handed."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.most = [0]

    def execute(self, sql: str, parameters: Any = (), /) -> Any:
        return Cursor(super().execute(sql, parameters), self.most)


def many(db: Path, workspaces: int) -> None:
    """`workspaces` workspaces, each with a page citing, beside its own claim, a note of
    another project of its workspace, stamped 31."""
    with closing(Store(db)) as store:
        for w in range(workspaces):
            view = store.for_account(store.create_account(f"Crew {w}").id)
            desk, east = view.create_project("desk"), view.create_project("east")
            d = passage(view, view.add_note(desk.id, f"# Dispatch\n\nCrate {w} ships.\n")[0])
            e = passage(view, filed(view, east.id, "# Minutes\n\nThe ledger opens at noon.\n"))
            borrowed(view, east.id, "minutes", citing(1, e.text, e), citing(2, d.text, d))
    stamped_31(db)


def step_32(db: Path) -> int:
    con = sqlite3.connect(db, factory=Counting, isolation_level=None)
    stamped = schema._STAMP.set(lambda: "2099-01-01T00:00:00.000000+00:00")
    try:
        con.execute("BEGIN")
        schema._MIGRATIONS[32](con)
        con.execute("COMMIT")
        return con.most[0]
    finally:
        schema._STAMP.reset(stamped)
        con.close()


@pytest.mark.parametrize("count", [3, 30])
def test_step_32_holds_one_page_at_a_time_whatever_the_number_of_pages_and_workspaces(
    tmp_path, count, caplog
):
    db = tmp_path / "cairn.db"
    many(db, count)
    with caplog.at_level(logging.WARNING, logger="cairn.store"):
        most = step_32(db)
    assert [r.args[1:] for r in caplog.records] == [(1, 1, 0)] * count
    assert rows(db, "SELECT COUNT(*) FROM page_versions") == [(count,)]
    assert most == MOST, most


# The most rows any one cursor of the step hands over, at 3 workspaces as at 30: one page's.
MOST = 1
