"""Database open refusals, lock safety, schema shape validation, and cold recovery contracts."""

import fcntl
import gc
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import pytest

from cairn.store import DEFAULT_ACCOUNT_ID, SCHEMA_VERSION, SchemaTooOld, Store
from cairn.web import cli, cli_data
from support.cli_commands import ARGS, COMMANDS, CREATES, PLANS_YAML, READS_ONLY, VARIANTS
from support.store.repair_says_what_committed import at_28_file, clean_31

# --- test_reviewer12_corruption.py ---


@pytest.mark.parametrize("command", COMMANDS)
def test_truncated_database_is_left_for_recovery(tmp_path, capsys, command):
    db = tmp_path / "db"
    Store(db).close()
    with db.open("r+b") as damaged:
        damaged.truncate(4096)
    (tmp_path / "archive.zip").touch()
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    argv = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in ARGS[command])]
    result = cli.main(argv)
    out, err = capsys.readouterr()
    assert result == 1 and out == "" and len(err.splitlines()) == 1
    assert str(db) in err and "damaged" in err
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before


# --- test_reviewer12_destinations.py ---


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm", ".serve.lock", ".compact.lock"])
@pytest.mark.parametrize("command", ["export", "key-create"])
def test_an_output_file_cannot_be_a_database_companion(tmp_path, capsys, command, suffix):
    db = tmp_path / "db"
    with closing(Store(db)):
        pass
    out = tmp_path / ("db" + suffix)
    extra = ["--label", "demo"] if command == "key-create" else ["--account", DEFAULT_ACCOUNT_ID]
    before = db.read_bytes()
    assert cli.main([command, "--db", str(db), "--out", str(out), *extra]) == 1
    said, err = capsys.readouterr()
    assert not out.exists() and db.read_bytes() == before
    assert said == "" and "reserved" in err


# --- test_reviewer12_file_types.py ---


@pytest.mark.parametrize("command", ["serve", "import", "key-create", "stats", "repair"])
def test_a_fifo_is_refused_before_sqlite_can_open_it(tmp_path, monkeypatch, capsys, command):
    db = tmp_path / "pipe"
    os.mkfifo(db)
    (tmp_path / "archive.zip").touch()
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("opened FIFO"))
    args = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in ARGS[command])]
    assert cli.main(args) == 1
    assert "regular file" in capsys.readouterr().err
    assert db.is_fifo() and not (tmp_path / "key.env").exists()


# --- test_reviewer12_header.py ---


@pytest.mark.parametrize("statement", ["VACUUM", "CREATE TABLE receipt(n); DROP TABLE receipt"])
@pytest.mark.parametrize("command", ["serve", "key-list", "stats", "audit-verify"])
def test_allocated_sqlite_file_is_never_an_initialization_target(
    tmp_path, capsys, monkeypatch, command, statement
):
    db = tmp_path / "db"
    with closing(sqlite3.connect(db)) as other:
        other.executescript(statement)
        assert other.execute("PRAGMA page_count").fetchone()[0] > 0
        assert not other.execute("SELECT name FROM sqlite_schema").fetchall()
    before = db.read_bytes()
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: None)
    result = cli.main([command, "--db", str(db)])
    out, err = capsys.readouterr()
    assert db.read_bytes() == before
    assert result == 1 and out == ""
    assert len(err.splitlines()) == 1 and "not a cairn database" in err


# --- test_reviewer12_identifiers.py ---


@pytest.mark.parametrize("command", ["serve", "key-list", "stats", "audit-verify"])
@pytest.mark.parametrize(
    "metadata",
    [
        ["PRAGMA application_id=7"],
        ["CREATE TABLE oldest_build (n)", "PRAGMA user_version=33"],
        [
            "CREATE TABLE oldest_build (one, version)",
            "INSERT INTO oldest_build VALUES (1, 'x')",
            "PRAGMA user_version=33",
        ],
    ],
)
def test_another_application_or_an_invalid_floor_is_a_clean_refusal(
    tmp_path, capsys, monkeypatch, command, metadata
):
    db = tmp_path / "db"
    with closing(sqlite3.connect(db)) as con:
        for sql in metadata:
            con.execute(sql)
        con.commit()
    before = db.read_bytes()
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("server started"))
    assert cli.main([command, "--db", str(db)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and len(err.splitlines()) == 1 and "cairn database" in err
    assert "serve --db" not in err and db.read_bytes() == before


# --- test_reviewer12_journal.py ---


@pytest.mark.parametrize("argv", [["repair"], ["compact"], ["compact", "--if-due"]])
def test_busy_maintenance_preserves_the_rollback_journal_mode(tmp_path, capsys, argv):
    db = tmp_path / "db"
    Store(db).close()
    with closing(sqlite3.connect(db)) as con:
        assert con.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    lock = tmp_path / "db.compact.lock"
    with lock.open("wb") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        before = db.read_bytes()
        assert cli.main([*argv, "--db", str(db)]) == 3
        assert "nothing was changed" in capsys.readouterr().err
        assert db.read_bytes() == before


@pytest.mark.parametrize("command", [c for c in COMMANDS if c != "serve"])
def test_just_opening_a_current_database_does_not_write(tmp_path, monkeypatch, command):
    db = tmp_path / "db"
    Store(db).close()
    with closing(sqlite3.connect(db)) as con:
        con.execute("PRAGMA journal_mode=DELETE")
    (tmp_path / "archive.zip").touch()
    (tmp_path / "plans.yaml").write_text(PLANS_YAML)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    args = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in ARGS[command])]
    if command in READS_ONLY:
        assert cli.main(args) == 0
    else:

        class Opened(Exception):
            pass

        actual = Store.__init__

        def stop_at_open(store, *args, **kwargs):
            actual(store, *args, **kwargs)
            store.close()
            raise Opened

        monkeypatch.setattr(Store, "__init__", stop_at_open)
        with pytest.raises(Opened):
            cli.main(args)
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_an_older_database_left_in_the_wal_is_refused_without_checkpointing(tmp_path, capsys):
    db = tmp_path / "db"
    Store(db).close()
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("PRAGMA user_version=31")
con.execute("UPDATE oldest_build SET version=31")
con.commit()
os._exit(0)
""",
            os.fspath(db),
        ],
        check=True,
    )
    wal = tmp_path / "db-wal"
    before = db.read_bytes(), wal.read_bytes()
    assert cli.main(["repair", "--db", str(db)]) == 1
    assert "version 31" in capsys.readouterr().err
    assert wal.exists() and (db.read_bytes(), wal.read_bytes()) == before


# --- test_reviewer12_links.py ---


@pytest.mark.parametrize("command", COMMANDS)
def test_a_second_name_cannot_open_the_database(tmp_path, capsys, monkeypatch, command):
    db, alias = tmp_path / "original", tmp_path / "alias"
    with closing(Store(db)):
        pass
    lock = cli.take_serve_lock(db)
    assert lock is not None
    try:
        alias.hardlink_to(db)
        (tmp_path / "archive.zip").touch()
        before = {p: p.read_bytes() for p in tmp_path.iterdir()}
        monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("second server"))
        args = [command, "--db", str(alias), *(s.format(dir=tmp_path) for s in ARGS[command])]
        assert cli.main(args) == 2
        out, err = capsys.readouterr()
        assert out == "" and "hard link" in err and str(alias) in err
        assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before
        with pytest.raises(ValueError, match="hard link"):
            Store(alias)
    finally:
        lock.release()


# --- test_reviewer12_open.py ---

ADMIN = [(c, ARGS[c]) for c in COMMANDS if c != "serve"] + VARIANTS + [("compact", ["--if-due"])]


@pytest.mark.parametrize(("command", "extra"), ADMIN)
def test_existing_empty_file_is_refused_without_writing(tmp_path, capsys, command, extra):
    db = tmp_path / "empty"
    db.touch()
    (tmp_path / "archive.zip").write_bytes(b"bad")
    (tmp_path / "plans.yaml").write_text(PLANS_YAML)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    argv = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in extra)]
    result = cli.main(argv)
    out, err = capsys.readouterr()
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert (result, out) == (1, "")
    assert len(err.splitlines()) == 1 and "uninitialized" in err and str(db) in err


@pytest.mark.parametrize("command", [c for c in COMMANDS if c not in CREATES])
def test_a_database_removed_just_before_open_is_not_recreated(
    tmp_path, capsys, monkeypatch, command
):
    db = tmp_path / "db"
    Store(db).close()
    is_file = Path.is_file

    def removed(path):
        answer = is_file(path)
        if path == db and answer:
            db.unlink()
        return answer

    monkeypatch.setattr(Path, "is_file", removed)
    argv = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in ARGS[command])]
    assert cli.main(argv) == 1
    out, err = capsys.readouterr()
    assert not db.exists() and out == "" and err == f"no database at {db}\n"


# --- test_reviewer12_readers.py ---


@pytest.mark.parametrize("command", ["stats", "audit-verify"])
def test_readers_use_the_common_refusal(tmp_path, capsys, command):
    db = clean_31(tmp_path)
    assert cli.main([command, "--db", str(db)]) == 1
    assert (
        capsys.readouterr().err
        == cli_data.OLDER_SCHEMA.format(db=db, found=31, known=SCHEMA_VERSION, command=command)
        + "\n"
    )


# --- test_reviewer12_recovery.py ---


@pytest.mark.parametrize(
    "command", ["serve", "repair", "compact", "key-list", "stats", "audit-verify"]
)
def test_recovery_is_left_to_the_database_owner(tmp_path, capsys, command, monkeypatch):
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("server started"))
    db = tmp_path / "db"
    Store(db).close()
    with closing(sqlite3.connect(db)) as con:
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("CREATE TABLE scratch (v BLOB)")
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("PRAGMA cache_size=2")
con.execute("BEGIN IMMEDIATE")
con.execute("PRAGMA user_version=31")
con.executemany("INSERT INTO scratch VALUES (zeroblob(4000))", [()] * 80)
os._exit(0)
""",
            str(db),
        ],
        check=True,
    )
    journal = tmp_path / "db-journal"
    before = db.read_bytes(), journal.read_bytes()
    assert before[1][:8] != bytes(8)
    assert cli.main([command, "--db", str(db)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "rollback journal" in err and "start the server" not in err
    assert journal.exists() and (db.read_bytes(), journal.read_bytes()) == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("empty", [False, True])
def test_serve_leaves_an_unattached_companion_alone(tmp_path, capsys, monkeypatch, suffix, empty):
    db = tmp_path / "db"
    if empty:
        db.touch()
    companion = tmp_path / ("db" + suffix)
    companion.write_bytes(b"previous")
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("server started"))
    assert cli.main(["serve", "--db", str(db)]) == 1
    assert str(companion) in capsys.readouterr().err
    assert db.exists() == empty and (not empty or db.read_bytes() == b"")
    assert companion.read_bytes() == b"previous"


# --- test_reviewer12_refusal.py ---


@pytest.mark.parametrize("command", [c for c in COMMANDS if c != "serve"])
def test_publicly_readable_old_database_gets_one_refusal(command, capsys):
    with tempfile.TemporaryDirectory(dir="/tmp") as folder:
        root = Path(folder)
        db = clean_31(root)
        os.chmod(root, 0o755)
        os.chmod(db, 0o644)
        assert cli_data.readable(db)
        (root / "archive.zip").touch()
        argv = [command, "--db", str(db), *(s.format(dir=root) for s in ARGS[command])]
        assert cli.main(argv) == 1
        out, err = capsys.readouterr()
        assert out == "" and len(err.splitlines()) == 1


# --- test_reviewer12_schema.py ---


@pytest.fixture
def no_listener(monkeypatch):
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("server started"))


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("version", [0, 28, -1])
def test_foreign_database_is_never_advised_onto_serve(
    tmp_path, capsys, command, version, no_listener
):
    db = tmp_path / "db"
    with closing(sqlite3.connect(db)) as con:
        con.execute("CREATE TABLE receipts (n INTEGER)")
        con.execute(f"PRAGMA user_version = {version}")
    (tmp_path / "archive.zip").touch()
    argv = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in ARGS[command])]
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    assert cli.main(argv) == 1
    out, err = capsys.readouterr()
    assert out == "" and len(err.splitlines()) == 1
    assert "cairn database" in err and "start the server" not in err
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE sessions DROP COLUMN expires_at",
        "CREATE TABLE slack_notes (n INTEGER)",
    ],
)
def test_serve_refuses_a_damaged_older_shape_before_the_first_step(
    tmp_path, capsys, change, no_listener
):
    db = at_28_file(tmp_path)
    gc.collect()  # close the historical fixture's connections before comparing disk bytes
    with closing(sqlite3.connect(db)) as con:
        con.execute(change)
    before = db.read_bytes()
    assert cli.main(["serve", "--db", str(db)]) == 1
    assert "cairn database" in capsys.readouterr().err
    assert db.read_bytes() == before


# --- test_reviewer12_scope.py ---


def test_a_read_only_view_has_no_database_opener():
    with closing(Store(":memory:")) as owner:
        reader = owner.narrow(None, "read")
        assert not hasattr(reader, "open_existing")


# --- test_reviewer12_version.py ---


@pytest.mark.parametrize("newer", [True, False], ids=["current", "older"])
def test_the_first_version_read_is_already_inside_the_transaction(tmp_path, monkeypatch, newer):
    db = clean_31(tmp_path)
    if newer:
        Store(db).close()
    reads = []
    connect = sqlite3.connect

    def traced(path, *args, **kwargs):
        con = connect(path, *args, **kwargs)
        if str(path) != ":memory:":

            def trace(statement):
                if statement in ("PRAGMA user_version", "PRAGMA application_id"):
                    reads.append((statement, con.in_transaction))

            con.set_trace_callback(trace)
        return con

    monkeypatch.setattr(sqlite3, "connect", traced)
    if newer:
        with closing(Store(db, upgrade=False, create=False)):
            pass
    else:
        with pytest.raises(SchemaTooOld):
            Store(db, upgrade=False, create=False)
    assert reads == [("PRAGMA user_version", True), ("PRAGMA application_id", True)]
