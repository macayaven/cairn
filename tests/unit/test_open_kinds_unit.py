"""What the table of every command against every kind of file (test_open_table_unit.py)
cannot reach by laying a file and running a command once: the shapes the recognition holds a
file to are built from the steps themselves; a `serve` that stops after its start wrote says
what its steps committed and what else it wrote, whatever stopped it; and `import` publishes
without replacing a file that appeared meanwhile, and removes only what it made. Each line is
built from the code's own constants. Made-up notes."""

import gc
import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_repair_says_what_committed_unit import at_28_file, at_31
from test_store_unit import user_version

import cairn.store
import cairn.store.schema as schema
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import DEFAULT_ACCOUNT_ID, SCHEMA_VERSION, Store
from cairn.web import cli, cli_data


def test_the_shape_the_steps_make_at_this_version_is_this_schema():
    """_historical_shapes runs the steps in memory; at this version its tables and columns
    are what _SCHEMA makes."""
    shapes = schema._historical_shapes()
    objects, fields = shapes[SCHEMA_VERSION]
    assert {t: set(c) for t, c in fields.items()} == {
        table: set(columns) for table, columns in schema._columns().items()
    }
    assert {name for name, kind in objects.items() if kind == "table"} == set(fields)
    assert "subject" in shapes[28][1]["credential_tokens"]
    assert "oldest_build" not in shapes[30][0] and shapes[31][0]["oldest_build"] == "table"


def in_mode(db: Path, mode: str) -> None:
    """Put the file in `mode` (it is left in WAL by the builders) and close it."""
    gc.collect()
    with closing(sqlite3.connect(db)) as con:
        assert con.execute(f"PRAGMA journal_mode = {mode}").fetchone() == (mode,)


def committed(db: Path, before: int, after: int, floor: int | None) -> str:
    """serve's line for the schema its own steps committed before it stopped."""
    said = cli.NO_RECORDED_FLOOR if floor is None else cli.COMPATIBILITY_FLOOR.format(floor=floor)
    return cli.SERVE_COMMITTED.format(before=before, after=after, db=db, floor=said)


def wrote(db: Path, *writes: str) -> str:
    """serve's line for the other writes of its start, in order."""
    return cli.SERVE_CHANGED.format(db=db, writes=", ".join(writes))


def to_wal(mode: str) -> tuple[str, ...]:
    """The journal write serve's open makes on a file in `mode`: none when it is in WAL."""
    return () if mode == "wal" else (cli.JOURNAL_CHANGED.format(before=mode, after="wal"),)


@pytest.fixture
def listened(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    return calls


def refusing_app(*args, **kwargs):
    raise ValueError("that provider is not known")


@pytest.mark.parametrize("mode", ["wal", "delete"])
def test_a_serve_that_stops_after_upgrading_says_the_schema_and_floor_now(
    tmp_path, monkeypatch, capsys, listened, mode
):
    db = at_31(tmp_path)
    in_mode(db, mode)
    monkeypatch.setattr(cli, "create_app", refusing_app)
    assert cli.main(["serve", "--db", str(db)]) == 2
    assert user_version(db) == SCHEMA_VERSION
    lines = capsys.readouterr().err.splitlines()
    assert lines[0].endswith("that provider is not known")  # the refusal, as it was
    # a second line names the journal and settings writes, when there are any
    expected = [
        committed(db, 31, SCHEMA_VERSION, 32),
        *([wrote(db, *to_wal(mode))] * (mode != "wal")),
    ]
    assert lines[1:] == expected
    assert listened == []


def test_a_serve_that_stops_after_making_a_database_says_so(
    tmp_path, monkeypatch, capsys, listened
):
    db = tmp_path / "cairn.db"
    monkeypatch.setattr(cli, "create_app", refusing_app)
    assert cli.main(["serve", "--db", str(db)]) == 2
    lines = capsys.readouterr().err.splitlines()
    assert lines[-2:] == [
        committed(db, 0, SCHEMA_VERSION, SCHEMA_VERSION),
        wrote(db, cli.CREATED_THE_FILE, *to_wal("delete")),
    ]


def test_a_serve_whose_server_fails_after_an_upgrade_says_so_too(tmp_path, monkeypatch, capsys):
    db = at_31(tmp_path)

    def address_in_use(*args, **kwargs):
        raise SystemExit(1)  # as uvicorn does when it cannot bind

    monkeypatch.setattr(cli.uvicorn, "run", address_in_use)
    with pytest.raises(SystemExit):
        cli.main(["serve", "--db", str(db)])
    lines = capsys.readouterr().err.splitlines()
    assert lines[-2] == committed(db, 31, SCHEMA_VERSION, 32)
    assert lines[-1].startswith(wrote(db)) and lines[-1].endswith(cli.RECORDED_BOUND)


def test_a_serve_whose_upgrade_fails_part_way_says_what_the_steps_before_committed(
    tmp_path, monkeypatch, capsys, listened
):
    """Each step commits on its own: a 28 whose step 31 fails stays at 30, and the line says
    so before the failure goes on."""
    db = at_28_file(tmp_path)
    gc.collect()

    def failing(con):
        raise RuntimeError("step 31 failed")

    monkeypatch.setitem(schema._MIGRATIONS, 31, failing)
    with pytest.raises(RuntimeError, match="step 31 failed"):
        cli.main(["serve", "--db", str(db)])
    assert user_version(db) == 30
    err = capsys.readouterr().err
    # 29 and 30 only add: no floor is recorded before 31 (schema._OLDEST_BUILD)
    assert err.splitlines() == [committed(db, 28, 30, None)]
    assert listened == []


@pytest.mark.parametrize("mode", ["wal", "delete"])
def test_a_serve_whose_open_fails_after_the_upgrade_committed_says_so(
    tmp_path, monkeypatch, capsys, listened, mode
):
    """The upgrade committed, then the open's own last steps (its readers) failed."""
    db = at_31(tmp_path)
    in_mode(db, mode)

    def no_readers(con, journal):
        raise RuntimeError("no readers")

    monkeypatch.setattr(cairn.store, "_readers_for", no_readers)
    with pytest.raises(RuntimeError, match="no readers"):
        cli.main(["serve", "--db", str(db)])
    assert user_version(db) == SCHEMA_VERSION
    expected = [
        committed(db, 31, SCHEMA_VERSION, 32),
        *([wrote(db, *to_wal(mode))] * (mode != "wal")),
    ]
    assert capsys.readouterr().err.splitlines() == expected


def test_compact_leaves_a_database_in_wal_as_it_found_it(tmp_path):
    """The commands but serve no longer put a file in WAL: compact's rewrite must not take it
    out of WAL either."""
    db = tmp_path / "cairn.db"
    with closing(Store(db)) as store:
        store.create_project("east")
        Memory(store, lint_gate).note("east", "# Far\n\nWren keeps the far log.\n")
    assert cli.main(["compact", "--db", str(db)]) == 0
    with closing(sqlite3.connect(db)) as con:
        assert con.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_a_serve_that_upgraded_and_stopped_as_asked_says_nothing_of_it(tmp_path, capsys, listened):
    db = at_31(tmp_path)
    assert cli.main(["serve", "--db", str(db)]) == 0
    err = capsys.readouterr().err
    assert cli.SERVE_COMMITTED.split("{")[0] not in err
    assert cli.SERVE_CHANGED.split("{")[0] not in err


def archive(tmp_path: Path) -> Path:
    db, out = tmp_path / "source.db", tmp_path / "archive.zip"
    with closing(Store(db)) as store:
        store.create_project("east")
        Memory(store, lint_gate).note("east", "# Far\n\nWren keeps the far log.\n")
    argv = ["export", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--out", str(out)]
    assert cli.main(argv) == 0
    return out


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_import_never_replaces_a_file_that_appeared_while_it_ran(tmp_path, monkeypatch, capsys):
    """Another process makes the file after import found none. Import publishes with a link
    that fails, says so, and removes only its own staged file; the other file is left byte for
    byte."""
    source = archive(tmp_path)
    folder = tmp_path / "data"
    db = folder / "cairn.db"
    real = cli_data.os.link

    def meanwhile(src, dst):
        db.write_bytes(b"another program's file")
        return real(src, dst)

    monkeypatch.setattr(cli_data.os, "link", meanwhile)
    argv = ["import", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--in", str(source)]
    assert cli.main(argv) == 1
    err = capsys.readouterr().err
    there = cli_data.ALREADY_THERE.format(db=db)
    assert err.splitlines()[-1] == cli_data.NOTHING_IMPORTED.format(error=there)
    assert db.read_bytes() == b"another program's file"
    assert sorted(p.name for p in folder.iterdir()) == ["cairn.db"]


def test_import_where_the_filesystem_makes_no_links_publishes_nothing(
    tmp_path, monkeypatch, capsys
):
    source = archive(tmp_path)
    folder = tmp_path / "data"
    db = folder / "cairn.db"

    def no_links(src, dst):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(cli_data.os, "link", no_links)
    argv = ["import", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--in", str(source)]
    assert cli.main(argv) == 1
    err = capsys.readouterr().err
    why = cli_data.CANNOT_PUBLISH.format(db=db, reason="Operation not permitted")
    assert err.splitlines()[-1] == cli_data.NOTHING_IMPORTED.format(error=why)
    assert not folder.exists()  # the folder it made for the database went with its file


def test_import_publishes_one_file_where_there_was_none(tmp_path, capsys):
    source = archive(tmp_path)
    capsys.readouterr()  # the export's own lines
    db = tmp_path / "data" / "cairn.db"
    argv = ["import", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--in", str(source)]
    assert cli.main(argv) == 0
    assert sorted(p.name for p in db.parent.iterdir()) == ["cairn.db"]
    assert os.stat(db).st_nlink == 1 and (os.stat(db).st_mode & 0o777) == 0o600
    assert user_version(db) == SCHEMA_VERSION
    assert capsys.readouterr().out.startswith("imported ")
