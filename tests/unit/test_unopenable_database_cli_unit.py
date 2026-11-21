"""Every command of `python -m cairn.server` answers a database path it cannot open with exit
1 and one line on stderr that names the path once and says what to do: a file that is not a
SQLite database, a database it may not read or write, a folder, a folder it may not create
the database in. Nothing on stdout, no traceback, nothing written or removed, and `serve`
never listens. The store raises CannotOpen where it opens the file, before it writes
anything; a database that is merely busy, or an error once it is open, is not one of these
and surfaces as before. The commands come from the parser (cli_commands.py)."""

import os
import sqlite3
import stat
from collections.abc import Callable
from pathlib import Path

import pytest
from cli_commands import ARGS, COMMANDS, CREATES, PLANS_YAML, READS_ONLY, VARIANT_IDS, VARIANTS

from cairn.store import DEFAULT_ACCOUNT_ID, CannotOpen, Store
from cairn.web import cli

AS_ROOT = os.geteuid() == 0
needs_modes = pytest.mark.skipif(
    AS_ROOT, reason="root reads and writes a file or folder whatever its mode says"
)
TEXT = b"meeting notes, not a database: the secret word is heliotrope\n" * 64


def made(tmp_path: Path) -> tuple[Path, Path]:
    """The folder the database goes in, and a separate writable one ({dir}) for the files a
    command reads or would make (archive.zip, plans.yaml, key.env, export.zip)."""
    data, work = tmp_path / "data", tmp_path / "work"
    data.mkdir()
    work.mkdir()
    (work / "archive.zip").write_bytes(b"not read: the database is refused first")
    (work / "plans.yaml").write_text(PLANS_YAML, encoding="utf-8")
    return data, work


def not_a_database(data: Path) -> tuple[Path, list[Path]]:
    db = data / "cairn.db"
    db.write_bytes(TEXT)
    return db, []


def unreadable(data: Path) -> tuple[Path, list[Path]]:
    db = data / "cairn.db"
    Store(db).close()
    os.chmod(db, 0)
    return db, [db]


def read_only_folder(data: Path) -> tuple[Path, list[Path]]:
    """A database cairn may read in a folder it may not write: a volume another user owns,
    with ordinary modes. SQLite cannot make its WAL files beside it."""
    db = data / "cairn.db"
    Store(db).close()
    os.chmod(data, 0o555)
    return db, [data]


def a_folder(data: Path) -> tuple[Path, list[Path]]:
    db = data / "cairn.db"
    db.mkdir()
    return db, []


def unwritable_folder(data: Path) -> tuple[Path, list[Path]]:
    os.chmod(data, 0o555)
    return data / "cairn.db", [data]


def under_unwritable_folder(data: Path) -> tuple[Path, list[Path]]:
    os.chmod(data, 0o555)
    return data / "sub" / "cairn.db", [data]


Case = Callable[[Path], tuple[Path, list[Path]]]


def tree(root: Path) -> dict[str, bytes | None]:
    """Every path under root, with a file's bytes (None for a folder)."""
    return {
        str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


@pytest.fixture
def listened(monkeypatch) -> list:
    """uvicorn.run as `serve` would call it, recorded and never run."""
    calls: list = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    return calls


def run(tmp_path, capsys, listened, case: Case, command: str, extra: list[str]) -> str:
    """Run the command on the case's path and check everything but the line, which it returns."""
    data, work = made(tmp_path)
    db, locked = case(data)
    modes = {path: stat.S_IMODE(path.lstat().st_mode) for path in locked}
    for path in locked:  # read the tree as it was, then lock it again
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    before = tree(tmp_path)
    for path, mode in modes.items():
        os.chmod(path, mode)
    argv = [command, "--db", str(db), *(arg.format(dir=work) for arg in extra)]
    try:
        code = cli.main(argv)
        after_modes = {path: stat.S_IMODE(path.lstat().st_mode) for path in locked}
    finally:
        for path in locked:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
    out, err = capsys.readouterr()
    assert code == 1, (argv, err)
    assert out == "", argv
    assert "Traceback" not in err
    (line,) = err.splitlines()  # exactly one line
    assert err == line + "\n"
    assert after_modes == modes, "a mode was changed"
    assert tree(tmp_path) == before, "a file was written, made or removed"
    assert listened == [], "serve started listening"
    assert "heliotrope" not in line and "meeting notes" not in line
    return line


# --- the line each case prints ---------------------------------------------------------------


def expected(case: Case, command: str, data: Path) -> str:
    db = data / "cairn.db"
    if case is not_a_database:
        said = cli.NOT_A_DATABASE_YET if command in CREATES else cli.NOT_A_DATABASE
        return said.format(path=db)
    if case in (unreadable, read_only_folder):
        return cli.MAY_NOT_OPEN.format(path=db)
    if case is a_folder:
        return cli.A_FOLDER.format(path=db)
    return cli.MAY_NOT_CREATE.format(folder=data)


EVERY_COMMAND = [not_a_database, pytest.param(unreadable, marks=needs_modes)]
EVERY_COMMAND.append(pytest.param(read_only_folder, marks=needs_modes))
CREATING_ONLY = [a_folder, pytest.param(unwritable_folder, marks=needs_modes)]
CREATING_ONLY.append(pytest.param(under_unwritable_folder, marks=needs_modes))


def test_the_commands_that_create_the_database_are_the_ones_cli_names():
    assert CREATES == ["import", "key-create", "serve"]
    assert sorted(cli.CREATES) == CREATES


@pytest.mark.parametrize("case", EVERY_COMMAND)
@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_refuses_a_file_it_cannot_open_in_one_line(
    tmp_path, capsys, listened, command, case
):
    if case is read_only_folder and command in READS_ONLY:
        reads_without_writing(tmp_path, capsys, command)  # Q1: it reads, below
        return
    line = run(tmp_path, capsys, listened, case, command, ARGS[command])
    assert line == expected(case, command, tmp_path / "data")
    assert line.count(str(tmp_path / "data" / "cairn.db")) == 1


@pytest.mark.parametrize("case", EVERY_COMMAND)
@pytest.mark.parametrize(("command", "extra"), VARIANTS, ids=VARIANT_IDS)
def test_the_options_that_print_notices_still_leave_one_line(
    tmp_path, capsys, listened, command, extra, case
):
    line = run(tmp_path, capsys, listened, case, command, extra)
    assert line == expected(case, command, tmp_path / "data")


@pytest.mark.parametrize("case", CREATING_ONLY)
@pytest.mark.parametrize("command", COMMANDS)
def test_a_folder_or_a_folder_it_may_not_write_is_one_line_too(
    tmp_path, capsys, listened, command, case
):
    """The commands that make the database say what is wrong with the path; the others
    still say there is no database there, as before."""
    line = run(tmp_path, capsys, listened, case, command, ARGS[command])
    data = tmp_path / "data"
    if command in CREATES:
        assert line == expected(case, command, data)
        named = str(data / "cairn.db") if case is a_folder else str(data)
        assert line.count(named) == 1
    else:
        db = data / ("sub/cairn.db" if case is under_unwritable_folder else "cairn.db")
        assert line == f"no database at {db}"


def reads_without_writing(tmp_path: Path, capsys, command: str) -> None:
    """A command that only reads (Q1) reads a database cairn may not write beside, with
    nothing written, made or removed and no mode changed: SQLite opens it immutable."""
    data, _ = made(tmp_path)
    db, _ = read_only_folder(data)
    os.chmod(data, 0o755)
    before = tree(tmp_path)
    os.chmod(data, 0o555)
    try:
        code = cli.main([command, "--db", str(db)])
        mode = stat.S_IMODE(data.lstat().st_mode)
    finally:
        os.chmod(data, 0o755)
    out, err = capsys.readouterr()
    assert (code, err, mode) == (0, "", 0o555) and out.strip()
    assert tree(tmp_path) == before, "a file was written, made or removed"


def test_the_lines_say_what_is_wrong_and_what_to_do():
    path = "/data/cairn.db"
    assert cli.NOT_A_DATABASE.format(path=path) == (
        "the file at /data/cairn.db is not a cairn database; --db names the database file"
    )
    assert cli.NOT_A_DATABASE_YET.format(path=path) == (
        "the file at /data/cairn.db is not a cairn database; --db names the database file,"
        " or a path where none exists yet"
    )
    assert cli.MAY_NOT_OPEN.format(path=path) == (
        "cairn may not read or write /data/cairn.db; run cairn as the file's owner, or let"
        " cairn's user own the file and its folder"
    )
    assert cli.A_FOLDER.format(path=path) == "/data/cairn.db is a folder; --db names a file"
    assert cli.MAY_NOT_CREATE.format(folder="/data") == (
        "cairn may not create the database under /data; run cairn as the folder's owner, or"
        " let cairn's user own the folder"
    )


# --- the store's refusal, and what stays as it was ------------------------------------------


@pytest.mark.parametrize(
    ("case", "not_a_db"),
    [
        (not_a_database, True),
        pytest.param(unreadable, False, marks=needs_modes),
        pytest.param(read_only_folder, False, marks=needs_modes),
        (a_folder, False),
        pytest.param(unwritable_folder, False, marks=needs_modes),
    ],
)
def test_the_store_raises_cannot_open_before_it_writes_anything(tmp_path, case, not_a_db):
    data, _ = made(tmp_path)
    db, locked = case(data)
    try:
        with pytest.raises(CannotOpen) as refused:
            Store(db)
    finally:
        for path in locked:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
    assert refused.value.path == db
    assert refused.value.not_a_database is not_a_db
    assert isinstance(refused.value, sqlite3.DatabaseError)  # as the store raised before
    assert isinstance(refused.value.__cause__, sqlite3.Error)
    assert "heliotrope" not in str(refused.value)


def fast_connect(monkeypatch) -> None:
    """Store's connections wait no time for a lock, so a busy database fails at once."""
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real(*a, **{**k, "timeout": 0}))


def held(db: Path) -> sqlite3.Connection:
    """Another process's connection, holding the database's exclusive lock."""
    con = sqlite3.connect(db, isolation_level=None)
    con.execute("PRAGMA locking_mode = EXCLUSIVE")
    con.execute("BEGIN EXCLUSIVE")
    return con


def test_a_busy_database_is_not_one_it_cannot_open(tmp_path, monkeypatch, capsys):
    db = tmp_path / "cairn.db"
    Store(db).close()
    fast_connect(monkeypatch)
    holder = held(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="database is locked") as busy:
            Store(db)
        assert not isinstance(busy.value, CannotOpen)
        argv = ["usage", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID]
        assert cli.main(argv) == 1  # SQLite's busy error, said in one line of its own
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert capsys.readouterr().err == cli.BUSY.format(db=db, command="usage") + "\n"


def cannot_open_error() -> sqlite3.OperationalError:
    """A real SQLITE_CANTOPEN from SQLite, as a failure would raise it."""
    try:
        sqlite3.connect("/nonexistent-cairn-test-folder/x.db")
    except sqlite3.OperationalError as error:
        assert error.sqlite_errorname == "SQLITE_CANTOPEN"
        return error
    raise AssertionError("SQLite opened a file under a folder that does not exist")


def test_an_error_once_the_database_is_open_surfaces_as_before(tmp_path, monkeypatch, capsys):
    db = tmp_path / "cairn.db"
    Store(db).close()

    def fails(self):
        raise cannot_open_error()

    monkeypatch.setattr(Store, "list_account_summaries", fails)
    with pytest.raises(sqlite3.OperationalError, match="unable to open database file") as raised:
        cli.main(["workspace-list", "--db", str(db)])
    assert not isinstance(raised.value, CannotOpen)
    assert capsys.readouterr() == ("", "")


def test_a_refusal_no_fact_on_disk_explains_is_raised_as_it_is(tmp_path, monkeypatch, capsys):
    """SQLite says it cannot open a file cairn may read and write in a folder it may write
    (out of file descriptors, say): no line would be true, so the error surfaces."""
    db = tmp_path / "cairn.db"
    Store(db).close()
    refusal = cannot_open_error()

    def refuses(*args, **kwargs):
        raise refusal

    monkeypatch.setattr(sqlite3, "connect", refuses)
    with pytest.raises(CannotOpen):
        cli.main(["workspace-list", "--db", str(db)])
    assert capsys.readouterr() == ("", "")


@needs_modes
def test_key_create_out_under_a_folder_it_may_not_write_keeps_its_line(tmp_path, capsys):
    db, locked = tmp_path / "cairn.db", tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o555)
    out = locked / "key.env"
    try:
        code = cli.main(["key-create", "--db", str(db), "--label", "ci", "--out", str(out)])
    finally:
        os.chmod(locked, 0o755)
    assert code == 1
    assert capsys.readouterr() == ("", f"cannot create {out}: Permission denied\n")
    assert not out.exists()


# --- what the docstring and the README say ------------------------------------------------


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_docstring_quotes_each_line_main_prints():
    doc = flat(cli.__doc__)
    for said in (
        cli.NOT_A_DATABASE.format(path="PATH"),
        cli.NOT_A_DATABASE_YET.format(path="PATH"),
        cli.MAY_NOT_OPEN.format(path="PATH"),
        cli.A_FOLDER.format(path="PATH"),
        cli.MAY_NOT_CREATE.format(folder="FOLDER"),
    ):
        assert f'"{said}"' in doc, said
    assert "cannot open as a cairn database (the store raises CannotOpen" in doc
    assert "stops every command with exit 1 and one line on stderr, nothing on stdout" in doc
    assert 'still answer a folder or a missing file with "no database at PATH"' in doc
    assert "Nothing is written, made or removed, and `serve` never starts listening" in doc
    assert "held past SQLite's wait (SQLITE_BUSY or SQLITE_LOCKED)" in doc
    assert "and one line (BUSY), nothing on stdout" in doc
    assert (
        "A refusal nothing on disk explains, or another error once it is open, surfaces as before."
    ) in doc


def test_the_readme_says_what_cairn_prints_when_it_may_not_write_the_directory():
    readme = flat((Path(__file__).resolve().parents[2] / "README.md").read_text("utf-8"))
    bullet = readme[readme.index("- **A host directory instead of a volume:**") :]
    bullet = bullet[: bullet.index(" - **")]
    assert '--user "$(id -u):$(id -g)"' in bullet
    assert (
        "If cairn may not write there, `serve` stops at start with one line that names the"
        " folder or the database file and says to run cairn as its owner."
    ) in bullet
    # Those are the lines MAY_NOT_CREATE and MAY_NOT_OPEN, checked above on such folders.
    assert "run cairn as the folder's owner" in cli.MAY_NOT_CREATE
    assert "run cairn as the file's owner" in cli.MAY_NOT_OPEN
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text("utf-8")
    assert 'CMD ["serve", "--db", "/data/cairn.db", ' in dockerfile
