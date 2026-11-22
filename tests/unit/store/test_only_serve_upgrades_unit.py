"""Only `serve` brings a database to this schema, and only once it holds the server's lock
(report 10, the class "a command that can commit changes while opening its database"). Every
other command of the parser opens the store with upgrade=False: a database of an older
schema is refused while the store opens, under SQLite's own lock and with no probe before
it, with one line on stderr and exit 1, and the file is left byte for byte. The reviewer's
three cases: `repair` once a lock that held the whole file clears, `repair` while it is still
held, and `compact` (with `--if-due` too) beside a running compaction. Made-up notes."""

import fcntl
import gc
import hashlib
import os
import sqlite3
import threading
from contextlib import closing
from functools import partial
from pathlib import Path

import pytest

from cairn.store import SCHEMA_VERSION, SchemaTooOld, Store
from cairn.web import cli, cli_audit_verify, cli_data, cli_stats
from support.cli_commands import ARGS, COMMANDS, PLANS_YAML
from support.store.repair_says_what_committed import at_28_file, at_31
from support.store.schema_step32 import table_rows
from support.store.store import user_version

BUILDS = {"31": at_31, "28": at_28_file}
OTHERS = [command for command in COMMANDS if command != "serve"]
# The two that only read keep their own lines (each says to start the server once on it).
OWN_LINES = {"stats": cli_stats.OLDER_DATABASE, "audit-verify": cli_audit_verify.OLDER_DATABASE}


def older(tmp_path: Path, build: str) -> Path:
    db = BUILDS[build](tmp_path)
    # The file at 28 is built through connections a `with sqlite3.connect(...)` leaves open
    # (test_hosted_accounts_unit.v27_database): closed here, not by a collection during the
    # command, which would fold their log into the file while the test compares its bytes.
    gc.collect()
    (tmp_path / "archive.zip").write_bytes(b"not read: the database is refused first")
    (tmp_path / "plans.yaml").write_text(PLANS_YAML, encoding="utf-8")
    return db


def sha(db: Path) -> str:
    return hashlib.sha256(db.read_bytes()).hexdigest()


def the_line(command: str, db: Path, found: int) -> str:
    said = OWN_LINES.get(command, cli_data.OLDER_SCHEMA)
    return said.format(db=db, found=found, known=SCHEMA_VERSION, command=command)


@pytest.fixture
def listened(monkeypatch) -> list:
    """uvicorn.run as `serve` would call it, recorded and never run."""
    calls: list = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    return calls


def refused(tmp_path, capsys, listened, db: Path, command: str, extra: list[str]) -> None:
    found, before, files = user_version(db), sha(db), sorted(tmp_path.iterdir())
    argv = [command, "--db", str(db), *(arg.format(dir=tmp_path) for arg in extra)]
    assert cli.main(argv) == 1, argv
    out, err = capsys.readouterr()
    assert out == "", argv
    assert err == the_line(command, db, found) + "\n", argv
    assert "start the server once on it (serve --db)" in err
    assert sha(db) == before, "the database was written"
    assert sorted(tmp_path.iterdir()) == files, "a file was made or removed beside it"
    assert listened == [], "serve started listening"


@pytest.mark.parametrize("build", BUILDS)
@pytest.mark.parametrize("command", OTHERS)
def test_every_command_but_serve_refuses_an_older_database_and_leaves_it_byte_for_byte(
    tmp_path, capsys, listened, command, build
):
    refused(tmp_path, capsys, listened, older(tmp_path, build), command, ARGS[command])


def test_the_line_says_what_is_true():
    line = the_line("compact", Path("cairn.db"), 31)
    assert line == (
        f"the database at cairn.db has an older schema (version 31; this cairn's is"
        f" {SCHEMA_VERSION}): start the server once on it (serve --db) to bring it up to date,"
        " which holds every page to its own project's notes and logs how many pages it"
        " rewrote and removed; only serve upgrades it, and compact changed nothing"
    )


@pytest.mark.parametrize("build", BUILDS)
def test_serve_upgrades_an_older_database_once_it_holds_its_lock(
    tmp_path, monkeypatch, listened, build
):
    db = older(tmp_path, build)
    order: list = []
    take = cli.take_serve_lock

    def taking(path):
        order.append("lock")
        return take(path)

    class Opening(Store):
        def __init__(self, *args, **kwargs) -> None:
            order.append(("open", user_version(db)))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(cli, "take_serve_lock", taking)
    monkeypatch.setattr(cli, "Store", Opening)
    assert cli.main(["serve", "--db", str(db)]) == 0
    assert order == ["lock", ("open", int(build))]
    assert user_version(db) == SCHEMA_VERSION and len(listened) == 1


@pytest.mark.parametrize("build", BUILDS)
def test_a_server_that_cannot_take_the_lock_upgrades_nothing(tmp_path, capsys, listened, build):
    """The lock an older server holds on the same database: the same file (serve_lock_path,
    unchanged since the first public version)."""
    db = older(tmp_path, build)
    held = cli.take_serve_lock(db)
    assert held is not None
    try:
        found, before = user_version(db), sha(db)
        assert cli.main(["serve", "--db", str(db)]) == 2
    finally:
        held.release()
    assert capsys.readouterr().err == cli.ANOTHER_SERVER.format(db=db) + "\n"
    assert sha(db) == before and user_version(db) == found and listened == []


def holding(db: Path) -> sqlite3.Connection:
    """A second connection that holds the whole file, its readers' way in too, as the
    reviewer's did; a read of the version by anyone else is busy meanwhile."""
    con = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
    con.execute("PRAGMA locking_mode = EXCLUSIVE")
    con.execute("BEGIN EXCLUSIVE")
    con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    with closing(sqlite3.connect(db, timeout=0)) as reader, pytest.raises(sqlite3.OperationalError):
        reader.execute("PRAGMA user_version").fetchone()
    return con


def let_go(con: sqlite3.Connection) -> None:
    con.execute("ROLLBACK")
    con.close()


def impatient_reads(monkeypatch) -> None:
    """A read-only connection (one opened by URI) gives up at once on a held file, as the
    reviewer's read of the version did once SQLite's wait ran out; the store's own open
    still waits."""
    real = sqlite3.connect

    def connect(database, *args, **kwargs):
        if kwargs.get("uri") and "mode=ro" in str(database):
            kwargs["timeout"] = 0
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)


def test_repair_once_a_held_database_clears_refuses_it_and_keeps_its_pages(
    tmp_path, capsys, monkeypatch
):
    """The reviewer's first case: repair's own read of the version was busy and fell through
    to an open for writing, which waited for the lock to clear and upgraded (31 to 32, pages
    removed, `nothing to change`, exit 0). Now no read comes before the open, which waits for
    SQLite's lock and then refuses."""
    db = at_31(tmp_path)
    held = table_rows(db)
    holder = holding(db)
    impatient_reads(monkeypatch)
    released = threading.Timer(0.3, let_go, (holder,))
    released.start()
    try:
        assert cli.main(["repair", "--db", str(db)]) == 1
    finally:
        released.join()
    out, err = capsys.readouterr()
    assert out == "" and err == the_line("repair", db, 31) + "\n"
    assert user_version(db) == 31 and table_rows(db) == held


def test_repair_while_a_database_is_held_past_the_stores_wait_says_busy(
    tmp_path, capsys, monkeypatch
):
    db = at_31(tmp_path)
    held = table_rows(db)
    holder = holding(db)
    monkeypatch.setattr(sqlite3, "connect", partial(sqlite3.connect, timeout=0.05))
    try:
        assert cli.main(["repair", "--db", str(db)]) == 1
    finally:
        monkeypatch.undo()
        let_go(holder)
    out, err = capsys.readouterr()
    assert out == ""
    assert err == (
        f"{db} was not repaired: the database is busy; nothing was changed, run repair again\n"
    )
    assert user_version(db) == 31 and table_rows(db) == held


@pytest.mark.parametrize("extra", [[], ["--if-due"]], ids=["compact", "compact --if-due"])
def test_compact_beside_a_running_compaction_refuses_an_older_database(
    tmp_path, capsys, listened, extra
):
    """The reviewer's third case: each upgraded on open, then exited 3 with `nothing was
    changed`. Now the open refuses first, and its line is true."""
    db = older(tmp_path, "31")
    held = table_rows(db)
    lock = os.open(f"{os.path.realpath(db)}.compact.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        refused(tmp_path, capsys, listened, db, "compact", extra)
    finally:
        os.close(lock)
    assert user_version(db) == 31 and table_rows(db) == held


@pytest.mark.parametrize("build", BUILDS)
def test_the_store_refuses_when_asked_and_upgrades_by_default(tmp_path, build):
    db = older(tmp_path, build)
    found, before = user_version(db), sha(db)
    with pytest.raises(SchemaTooOld) as refusal:
        Store(db, upgrade=False)
    assert refusal.value.found == found
    assert str(refusal.value) == (
        f"the database has schema version {found}; this cairn's is {SCHEMA_VERSION}"
    )
    assert sha(db) == before
    with closing(Store(db)):  # the library's default, as before
        pass
    assert user_version(db) == SCHEMA_VERSION


@pytest.mark.parametrize("empty", [False, True], ids=["no file", "an empty file"])
def test_a_store_that_may_not_upgrade_still_makes_a_new_database(tmp_path, empty):
    db = tmp_path / "cairn.db"
    if empty:
        db.touch()
    with closing(Store(db, upgrade=False)) as store:
        assert store.list_projects() == []
    assert user_version(db) == SCHEMA_VERSION


def test_key_create_still_makes_the_database_where_there_is_none(tmp_path, capsys):
    db = tmp_path / "data" / "cairn.db"
    argv = ["key-create", "--db", str(db), "--label", "ci", "--out", str(tmp_path / "key.env")]
    assert cli.main(argv) == 0
    assert user_version(db) == SCHEMA_VERSION


@pytest.mark.parametrize("upgrade", [False, True], ids=["a command's open", "serve's open"])
def test_a_sqlite_file_that_is_not_cairns_is_refused_as_before_never_as_an_older_one(
    tmp_path, upgrade
):
    """A file at version 0 with a table reads as v0 (version 1); one without v0's tables is no
    cairn database, and the open that may not upgrade says so as the upgrade does, never that
    starting the server would bring it up to date."""
    db = tmp_path / "other.db"
    with closing(sqlite3.connect(db)) as con:
        con.execute("CREATE TABLE recipes (name TEXT)")
        con.execute("INSERT INTO recipes VALUES ('soup')")
        con.commit()
    before = sha(db)
    with pytest.raises(sqlite3.DatabaseError, match="not a cairn database"):
        Store(db, upgrade=upgrade)
    assert sha(db) == before
