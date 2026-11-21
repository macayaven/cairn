"""The sentences on a page held to its own project's notes (report 8): privacy.md's One note
and A project, run.md's import, upgrade and stats, each pinned to what the code does over a
real store, the real schema step or the command line. Made-up notes."""

import io
import json
import logging
from contextlib import closing
from importlib.resources import files

import pytest
from test_earlier_page_writers_unit import legacy
from test_rt1_forget_workspace_held import GONE, borrowed, citing, filed, passage
from test_store_unit import connect, rows

import cairn.store.schema as schema
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Claim, Store
from cairn.web import cli


def guide(name: str) -> str:
    return " ".join(files("cairn.guides").joinpath(f"{name}.md").read_text().split())


PRIVACY_NOTE = (
    "A page cites only its own project's notes: {{name}}'s own writers (a note's compile, the"
    " tidy and a model's arrangement) never save a citation of another project's note, a"
    " page's save refuses any passage the database holds of another project's note, and an"
    " import refuses a file that holds one; a passage the database does not hold is no note's,"
    " and only a program that calls the store's save itself can cite one. And the upgrade to"
    " this version rewrote every page that an earlier version had"
    " saved citing another project's note, keeping its own project's claims and none of its"
    " earlier versions. Should a page of another project of the workspace still cite the"
    " note, which only a program that wrote into the database directly, or an older version's"
    " writer still open across the upgrade, could leave, the forget changes it in the same way;"
    " the person who forgets is told how many such pages it changes, before and after, and so"
    " is everyone who reads the project's audit log, never which."
)
PRIVACY_PROJECT = (
    "A page of another project cannot cite its notes (One note, below); should one still"
    " cite them, which only a program that wrote into the database directly, or an older"
    " version's writer still open across the upgrade, could leave, it changes as it does when a"
    " note is forgotten."
)
RUN_IMPORT = (
    "An import refuses a file in which a page cites a note of another project, with one line"
    " that names the page. cairn writes no such citation, and the upgrade to schema 32"
    " rewrote every page an earlier version had saved with one, so only an export made"
    " before that upgrade, or from a database written into directly by a program or by an"
    " older version's writer still open across the upgrade, holds one."
    " Start this version of cairn once on the database the file came from, which runs that"
    " upgrade, and export again. In a database already at schema 32, `stats"
    " --other-projects` (above) counts such pages, and `repair` (above) rewrites them as the"
    " upgrade did: run it, then export again."
)
RUN_UPGRADE = (
    "The upgrade to schema 32 (release 0.1.0) holds every page to its own project's notes: a"
    " page that an earlier version saved citing a note of another project, in any of its"
    " versions, even one that note's workspace has forgotten since, keeps one new version with"
    " its own project's claims alone, and its earlier versions go, as when that note is"
    " forgotten; a page left with no claim of its own goes."
    " Once the upgrade has committed, the server's log says, for each workspace that had any,"
    " how many pages were rewritten and how many removed, and names no page; when a page"
    " changed, a compaction is marked due, as a forget marks one. A database whose pages all"
    " cite only their own project's notes passes the upgrade with no page rewritten or removed,"
    " and the log says nothing of it. To learn beforehand what it would change, start this"
    " version once on a copy of the database and read that copy's log: `stats"
    " --other-projects` counts such pages only on a database already at this version, and"
    " refuses an older one."
)
RUN_COMMANDS = (  # its list: test_every_command_that_opens_the_database_is_named
    "`serve` runs the upgrade once it holds the lock that keeps one server on the database,"
    " which an older version's server holds too, and only `serve` does; every other command"
    " refuses a database of an older schema with one line on stderr that says to start the"
    " server once on it, exits 1 and changes nothing. An older version's other commands take"
    " no such lock: stop them before the upgrade, as `docker stop cairn` does for those run in"
    " its container, since one still open across the upgrade can write into the upgraded"
    " database."
)
RUN_ADDRESSES = (
    "An upgrade keeps the current address of every page that remains. A link pinned to a"
    " removed version can return 404. A claim link on the current page keeps working when the"
    " claim keeps its id."
)
RUN_STATS = (
    "`--other-projects` adds one count, for each workspace that has any: the pages that cite,"
    " in any of their versions, a passage that is not of their own project's notes, such as one"
    " of another project's note, even a note its workspace has forgotten since (`other_projects`"
    " with `--json`)."
    " It reads every page version, so it runs only when asked. cairn writes no such page, and"
    " the upgrade that brought the database to this version rewrote every one an earlier"
    " version left, so a number above zero means that a program other than cairn made the page"
    " (by writing into the database's tables, or by calling cairn's own code with passages the"
    " database does not hold) or an older version's writer still open across the upgrade did."
)
RUN_REPAIR = (  # its behaviour: test_repair_command_unit.py
    "To clear them, run `docker exec cairn python -m cairn.server repair --db /data/cairn.db`;"
    " it rewrites those pages as the upgrade did, in one transaction, and prints how many pages"
    " it rewrote and how many it removed, in all and for each workspace by its id, naming no page,"
    " or `nothing to change` when there are none. It runs beside a running server, whose"
    " readers see each page as it was before or as it is after and whose writes wait for it as"
    " for a forget; as a forget does, it marks a compaction due. Like `compact`, it changes"
    " nothing and exits 3 while a compaction runs, and it changes nothing and exits 1 when"
    " another write holds the database for too long: run it again. It holds"
    " `cairn.db.compact.lock` as `compact` does, so while it runs a `compact` says that another"
    " compaction is running and exits 3, and the server's own compactor logs `compact skipped:"
    " another compaction is running on this database` and tries again at the next poll."
    " On a database of an older schema it changes nothing and exits 1, as `stats` does: start"
    " the server once on it, whose upgrade holds every page to its own project's notes and"
    " logs how many pages it rewrote and removed."
)
GONE_SAYS = "A page can cite only passages from its own project."


def test_the_guides_say_it():
    assert PRIVACY_NOTE in guide("privacy")
    assert PRIVACY_PROJECT in guide("privacy")
    for said in (RUN_IMPORT, RUN_UPGRADE, RUN_STATS, RUN_REPAIR, RUN_ADDRESSES, RUN_COMMANDS):
        assert said in guide("run")
    for gone in (
        "keeps it until then",
        "a later note on that page keeps one it has",
        "refuses any other citation on every write",
        "An upgrade keeps every page's address",
        "and to the claims that remain on it keep working",
        "which only a program that wrote into the database directly could leave",
        "a database a program wrote into directly, holds one",
    ):
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


def test_stats_counts_a_page_saved_through_the_store_citing_a_passage_it_never_held(
    tmp_path, capsys
):
    db = tmp_path / "cairn.db"
    with closing(Store(db)) as store:
        east = store.create_project("east")
        made_up = Claim(id="cl_" + "1" * 16, text="A line.", span_ids=("sp_" + "0" * 16,))
        assert store.save_page(east.id, "made", "Made", "", [made_up]).version == 1
        one = store._account_id
    assert cli.main(["stats", "--db", str(db), "--json", "--other-projects"]) == 0
    assert json.loads(capsys.readouterr().out)["other_projects"] == {one: 1}


def test_stats_counts_a_page_citing_a_note_its_workspace_has_forgotten_since(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    with closing(Store(db)) as store:
        _, cited = legacy(store, "workspace")
        (far,) = rows(
            db,
            "SELECT projects.account_id FROM notes JOIN projects ON projects.id = notes.project_id"
            f" WHERE notes.id = '{cited.source_id}'",
        )
        Memory(store.for_account(far[0]), lint_gate).forget("desk", cited.source_id)
        one = store._account_id
    assert cli.main(["stats", "--db", str(db), "--json", "--other-projects"]) == 0
    assert json.loads(capsys.readouterr().out)["other_projects"] == {one: 1}


def test_every_command_that_opens_the_database_is_named():
    """RUN_COMMANDS against the parser: `serve` is the one command it says upgrades, and every
    other command that takes --db is one test_only_serve_upgrades_unit.py runs against a file
    at 31 and at 28 (its ARGS, read from the parser), each refused with its line and the file
    left byte for byte; a new command fails there until it has its arguments."""
    import argparse
    import re

    from cli_commands import ARGS

    from cairn.web import cli_audit_verify, cli_data, cli_stats

    sub = next(a for a in cli._parser()._actions if isinstance(a, argparse._SubParsersAction))
    takes_db = {n for n, p in sub.choices.items() if "db" in {a.dest for a in p._actions}}
    assert set(ARGS) == takes_db and "serve" in takes_db
    said = RUN_COMMANDS.split(";")[0]
    assert set(re.findall(r"`([a-z-]+)`", said)) == {"serve"}
    assert "only `serve` does; every other command refuses a database of an older schema" in (
        RUN_COMMANDS
    )
    for line in (cli_data.OLDER_SCHEMA, cli_stats.OLDER_DATABASE, cli_audit_verify.OLDER_DATABASE):
        assert "start the server once on it (serve --db)" in line
