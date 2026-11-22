"""`repair --db PATH`: the operator's remedy for what `stats --other-projects` still counts
once the database is at schema 32, a page a program wrote behind the store's back citing a
passage that is not of its own project's notes. It runs the upgrade's own repair
(cairn.store.erasure.hold_to_own_projects) over the whole database in one transaction, prints
the totals and each workspace's counts, names no page, project or note, and on a database
with no such page says "nothing to change" and writes nothing. Like `compact`, it refuses at
once while a compaction runs (exit 3), and a write held past the store's wait leaves the
database as it was (exit 1). Made-up notes."""

import io
import json
import sqlite3
from contextlib import closing
from typing import ClassVar

import pytest
from starlette.testclient import TestClient

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.server import create_app
from cairn.store import Store
from cairn.web import cli, cli_data
from support.store.earlier_page_writers import BASE, SLUG, legacy
from support.store.schema_step32 import table_rows
from support.store.store import rows

NEW = "Drift rings the bell at two."
STATES = ("same workspace, held", "another workspace, held", "no longer held")


def other_projects(db, capsys) -> dict[str, int]:
    assert cli.main(["stats", "--db", str(db), "--json", "--other-projects"]) == 0
    return json.loads(capsys.readouterr().out)["other_projects"]


def written_behind_the_stores_back(db, state: str):
    """east's page in one of the three states such a row can be in. (east's id, the cited
    passage's id, east's workspace.)"""
    with closing(Store(db)) as store:
        east, cited = legacy(store, "project" if state.startswith("same") else "workspace")
        if state == "no longer held":  # the note's own workspace forgets it: east is stuck
            (far,) = rows(
                db,
                "SELECT projects.account_id FROM notes JOIN projects"
                f" ON projects.id = notes.project_id WHERE notes.id = '{cited.source_id}'",
            )
            Memory(store.for_account(far[0]), lint_gate).forget("desk", cited.source_id)
        return east.id, cited.id, store._account_id


@pytest.mark.parametrize("state", STATES)
def test_repair_holds_the_page_to_its_own_projects_notes_and_the_writers_go_on(
    tmp_path, capsys, state
):
    db = tmp_path / "cairn.db"
    east, cited, one = written_behind_the_stores_back(db, state)
    assert other_projects(db, capsys) == {one: 1}
    with closing(Store(db)) as store:
        due = store.compaction_state().deleted_at  # a forget made it due in the third state
    assert (due is None) == (state != "no longer held")
    assert cli.main(["repair", "--db", str(db)]) == 0
    said = capsys.readouterr().out
    assert said.splitlines() == [
        f"repaired {db}: 1 page(s) rewritten with their own project's claims, 0 removed,"
        " in 1 workspace(s)",
        f"workspace {one}: 1 rewritten, 0 removed",
    ]
    assert not any(name in said for name in ("east", "desk", SLUG, cited))
    assert other_projects(db, capsys) == {}
    with closing(Store(db)) as store:
        page = store.get_page(east, SLUG)
        assert store.page_versions(east, SLUG) == [page.version]
        assert not [c for c in page.claims if cited in c.span_ids]
        key = store.create_key("t", projects=[east])
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            r = client.post(
                "/api/projects/east/notes",
                headers={"x-api-key": key},
                json={"text": f"# Drift sync\n\n{NEW}\n"},
            )
        assert (r.status_code, r.json()["status"]) == (201, "saved"), r.text
        assert store.compaction_state().deleted_at is not None  # as a forget marks it
        out = io.BytesIO()
        summary = store.export(out)
    with closing(Store(":memory:")) as fresh:
        assert fresh.import_export(io.BytesIO(out.getvalue())).pages == summary.pages


class Traced(Store):
    said: ClassVar[list[str]] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._observe(trace=Traced.said.append)


def test_on_a_database_with_no_such_page_it_says_so_and_writes_nothing(
    tmp_path, capsys, monkeypatch
):
    db = tmp_path / "cairn.db"
    with closing(Store(db)) as store:
        east = store.create_project("east")
        store.add_note(east.id, "# Minutes\n\nThe ledger opens at noon.\n")
        Memory(store, lint_gate).note("east", "# Far\n\nWren keeps the far log.\n")
    held = table_rows(db)
    Traced.said = []
    monkeypatch.setattr(cli_data, "Store", Traced)
    assert cli.main(["repair", "--db", str(db)]) == 0
    assert capsys.readouterr().out == (
        f"nothing to change in {db}: no page cites a passage that is not of its own"
        " project's notes\n"
    )
    asked = [s for s in Traced.said if "json_each(p.claims)" in s]
    writes = [
        s
        for s in Traced.said
        if s.split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP"}
    ]
    assert len(asked) == 1 and writes == [], Traced.said
    assert table_rows(db) == held


def test_it_refuses_at_once_while_a_compaction_runs(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    written_behind_the_stores_back(db, "another workspace, held")
    held = table_rows(db)
    with closing(Store(db)) as compacting, compacting.compacting():
        assert cli.main(["repair", "--db", str(db)]) == cli_data.ANOTHER_COMPACTION
    err = capsys.readouterr().err
    assert "nothing was changed, run repair again when it is done" in err
    assert table_rows(db) == held


class Hurried(Store):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._con.execute("PRAGMA busy_timeout = 50")


def test_a_write_held_past_the_stores_wait_leaves_the_database_as_it_was(
    tmp_path, capsys, monkeypatch
):
    db = tmp_path / "cairn.db"
    written_behind_the_stores_back(db, "another workspace, held")
    held = table_rows(db)
    monkeypatch.setattr(cli_data, "Store", Hurried)
    writer = sqlite3.connect(db, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        assert cli.main(["repair", "--db", str(db)]) == 1
        writer.execute("ROLLBACK")
    finally:
        writer.close()
    err = capsys.readouterr().err
    assert f"{db} was not repaired: the database is busy; nothing was changed" in err
    assert table_rows(db) == held


def test_while_it_runs_a_compact_says_another_compaction_is_running(tmp_path, capsys, monkeypatch):
    """It holds the lock compact holds (the run guide's word: `compact` exits 3 meanwhile)."""
    db = tmp_path / "cairn.db"
    written_behind_the_stores_back(db, "another workspace, held")
    meanwhile: list[int] = []

    class Meanwhile(Store):
        def repair_own_projects(self, each):
            meanwhile.append(cli.main(["compact", "--db", str(db)]))
            return super().repair_own_projects(each)

    monkeypatch.setattr(cli_data, "Store", Meanwhile)
    assert cli.main(["repair", "--db", str(db)]) == 0
    assert meanwhile == [cli_data.ANOTHER_COMPACTION]
    assert "another compaction is running" in capsys.readouterr().err
