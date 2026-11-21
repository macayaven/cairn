"""The sentences on a page held to its own project's notes (report 8): privacy.md's One note
and A project, run.md's import, upgrade and stats, each pinned to what the code does over a
real store, the real schema step or the command line. Made-up notes."""

import io
import json
import logging
from contextlib import closing
from importlib.resources import files

import pytest
from test_rt1_forget_workspace_held import GONE, borrowed, citing, filed, passage
from test_store_unit import connect

import cairn.store.schema as schema
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store
from cairn.web import cli


def guide(name: str) -> str:
    return " ".join(files("cairn.guides").joinpath(f"{name}.md").read_text().split())


PRIVACY_NOTE = (
    "A page cites only its own project's notes: {{name}} refuses any other citation on every"
    " write, and the upgrade to this version rewrote every page that an earlier version had"
    " saved citing another project's note, keeping its own project's claims and none of its"
    " earlier versions. Should a page of another project of the workspace still cite the"
    " note, which only a program that wrote into the database directly could leave, the"
    " forget changes it in the same way; the person who forgets is told how many such pages"
    " it changes, before and after, and so is everyone who reads the project's audit log,"
    " never which."
)
PRIVACY_PROJECT = (
    "A page of another project cannot cite its notes (One note, below); should one still"
    " cite them, which only a program that wrote into the database directly could leave, it"
    " changes as it does when a note is forgotten."
)
RUN_IMPORT = (
    "An import refuses a file in which a page cites a note of another project, with one line"
    " that names the page. cairn writes no such citation, and the upgrade to schema 32"
    " rewrote every page an earlier version had saved with one, so only an export made"
    " before that upgrade, or from a database a program wrote into directly, holds one."
    " Start this version of cairn once on the database the file came from, which runs that"
    " upgrade, and export again. In a database already at schema 32, `stats"
    " --other-projects` (above) counts such pages; forgetting the note a page cites, or"
    " deleting that note's project, rewrites the page when both are in one workspace."
)
RUN_UPGRADE = (
    "The upgrade to schema 32 (release 0.1.0) holds every page to its own project's notes: a"
    " page that an earlier version saved citing a note of another project, in any of its"
    " versions, keeps one new version with its own project's claims alone, and its earlier"
    " versions go, as when that note is forgotten; a page left with no claim of its own goes."
    " The server's log says, for each workspace that had any, how many pages were rewritten"
    " and how many removed, and names none."
)
RUN_STATS = (
    "`--other-projects` adds one count, for each workspace that has any: the pages that cite"
    " a note of another project in any of their versions (`other_projects` with `--json`)."
    " It reads every page version, so it runs only when asked. cairn writes no such page, and"
    " the upgrade that brought the database to this version rewrote every one an earlier"
    " version left, so a number above zero means that a program wrote into the database's"
    " tables directly."
)
GONE_SAYS = "A page can cite only passages from its own project."


def test_the_guides_say_it():
    assert PRIVACY_NOTE in guide("privacy")
    assert PRIVACY_PROJECT in guide("privacy")
    for said in (RUN_IMPORT, RUN_UPGRADE, RUN_STATS):
        assert said in guide("run")
    for gone in ("keeps it until then", "a later note on that page keeps one it has"):
        assert gone not in guide("privacy") + guide("run")


def old_page(store: Store):
    """east's page citing desk's note beside its own claim, as an earlier version saved it."""
    desk, east = store.create_project("desk"), store.create_project("east")
    note = filed(store, desk.id, f"# Dispatch\n\n{GONE}\n")
    own = passage(store, filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n"))
    borrowed(
        store, east.id, "minutes", citing(1, own.text, own), citing(2, GONE, passage(store, note))
    )
    return desk, east, note


def test_every_write_refuses_and_a_forget_tells_its_maker_the_count(tmp_path):
    with closing(Store(tmp_path / "cairn.db")) as store:
        _, east, note = old_page(store)
        page = store.get_page(east.id, "minutes")
        with pytest.raises(ValueError, match=GONE_SAYS):
            store.save_page(east.id, "minutes", page.title, page.body, page.claims)
        memory = Memory(store, lint_gate)
        assert memory.forget_preview("desk", note.id)[2].elsewhere == 1
        assert memory.forget("desk", note.id)["elsewhere"] == 1
        (event,) = [e for e in store.list_audit() if e.action == "note.forgotten"]
        assert event.detail["elsewhere"] == 1 and "east" not in json.dumps(event.detail)


def test_the_upgrade_rewrites_and_logs_counts_and_the_import_then_takes_the_export(
    tmp_path, caplog
):
    db = tmp_path / "cairn.db"
    with closing(Store(db)) as store:
        _, east, _ = old_page(store)
        out = io.BytesIO()
        store.export(out)
    with closing(Store(":memory:")) as fresh, pytest.raises(ValueError, match="minutes"):
        fresh.import_export(io.BytesIO(out.getvalue()))
    with connect(db) as con:  # the database as release 0.1.0's upgrade finds it
        con.execute("PRAGMA user_version = 31")
        con.execute("UPDATE oldest_build SET version = 31")
        con.commit()
    assert schema.SCHEMA_VERSION == 32
    with caplog.at_level(logging.WARNING, logger="cairn.store"), closing(Store(db)) as store:
        page = store.get_page(east.id, "minutes")
        assert [c.text for c in page.claims] == ["The ledger opens at noon."]
        assert store.page_versions(east.id, "minutes") == [page.version]
        out = io.BytesIO()
        store.export(out)
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("repair:")]
    assert "1 rewritten" in line and "0 removed" in line and "minutes" not in line
    with closing(Store(":memory:")) as fresh:
        assert fresh.import_export(io.BytesIO(out.getvalue())).pages == 2  # and desk's


def test_stats_counts_only_when_asked(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    with closing(Store(db)) as store:
        old_page(store)
        one = store._account_id
    assert cli.main(["stats", "--db", str(db), "--json", "--other-projects"]) == 0
    assert json.loads(capsys.readouterr().out)["other_projects"] == {one: 1}
