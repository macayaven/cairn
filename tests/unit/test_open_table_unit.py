"""Every command of the parser against every kind of file `--db` can name (report 11, the
class "a command that commits changes while opening its database, then reports only what
happened after the open", closed by enumeration).

A row is a command of the parser's table with the smallest arguments that make it open its
database (cli_commands.ARGS), and three more ways in: `compact --if-due`, and `serve` with a
model whose settings are missing, which must fail before the lock and the open. A column is
a kind of file (KINDS). Each cell runs the command once, in-process, on a fresh copy, and
asserts three things:

- the bytes and the mode of the file and of every file beside it, after against before:
  equal, unless the cell is named in MAY_WRITE (with its reason) or the command's own job is
  a write (OWN_WRITE) on a database of this schema and it succeeded;
- the exit code (EXIT, by kind, and THIS_SCHEMA_EXIT for the commands on a database they
  run on);
- that the words on stdout and stderr are true of what the bytes show (TRUE_ONLY): no
  "changed nothing" beside changed bytes, a failure after a change names it, and no advice
  to start a server on a file that is not cairn's database of an older schema or empty.

`serve` is driven to the point after its open and no further: uvicorn.run is recorded, never
run. Each phrase a cell looks for is the code's own words: a part of one of its constants
(test_every_phrase_is_the_codes_own), or the line its own function makes for that file.
Made-up notes only.
"""

import fcntl
import gc
import hashlib
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import threading
from contextlib import ExitStack, closing
from functools import partial
from pathlib import Path

import pytest
from cli_commands import ARGS, COMMANDS, PLANS_YAML
from test_repair_says_what_committed_unit import at_28_file, at_31
from test_schema_step32_unit import made

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import DEFAULT_ACCOUNT_ID, SCHEMA_VERSION, Store
from cairn.store.base import hard_linked
from cairn.web import cli, cli_data, cli_stats

# The rows: every command of the parser that opens a database, and three more ways in.
ROWS: dict[str, tuple[str, list[str]]] = {command: (command, ARGS[command]) for command in COMMANDS}
ROWS["compact --if-due"] = ("compact", ["--if-due"])
ROWS["serve, model unset"] = ("serve", ["--model-env", "CAIRN_TABLE_UNSET"])
ROWS["key-create --project"] = ("key-create", [*ARGS["key-create"], "--project", "nope"])

# The columns: what `--db` names.
OTHER_VERSIONS = (0, 1, 28, 31, SCHEMA_VERSION, SCHEMA_VERSION + 1)
KINDS = [
    "none",  # no file
    "empty",  # a file of no bytes
    "not sqlite",  # bytes SQLite does not read as a database
    *(f"other {version}" for version in OTHER_VERSIONS),  # another program's, at that version
    "cairn 28",
    "cairn 31",
    "cairn 28, replicated",  # cairn 28 holding a replica's two tables (Litestream's), one row
    "cairn wal",  # this schema, as serve leaves it
    "cairn, replicated",  # this schema holding a replica's two tables, one row
    "cairn rollback",  # this schema, its journal mode put back to a rollback journal
    "newer runs",  # a newer schema whose floor is at or below this build's
    "newer refused",  # a newer schema whose floor is above it
    "busy held",  # this schema, a lock held past every wait
    "busy clears",  # this schema, a lock released after 0.3 s
    "readable",  # this schema, a file others on the machine may read
    "folder",  # a directory
    "second name",  # this schema, with a second hard link beside it
    "empty, compacting",  # an empty file while a compaction holds its lock
]
# Kinds whose file is cairn's database of this schema, which a command runs on.
THIS_SCHEMA = (
    "cairn wal",
    "cairn, replicated",
    "cairn rollback",
    "newer runs",
    "busy clears",
    "readable",
)
OTHERS = tuple(f"other {version}" for version in OTHER_VERSIONS)
OLDER = ("cairn 28", "cairn 31", "cairn 28, replicated")
EMPTY = ("empty", "empty, compacting")
SERVES = ("serve", "serve, model unset")
# The rows whose command makes a database where there is none.
MAKES = ("serve", "key-create", "import")

# The cells allowed to change bytes beyond OWN_WRITE, each with its reason. Every other cell
# is byte for byte.
MAY_WRITE: dict[tuple[str, str], str] = {
    ("serve", "none"): "serve makes a new database",
    ("serve", "empty"): "serve makes a new database in an empty file",
    ("serve", "cairn 28"): "serve upgrades a cairn database of an older schema",
    ("serve", "cairn 31"): "serve upgrades a cairn database of an older schema",
    ("serve", "cairn 28, replicated"): (
        "serve upgrades a cairn database of an older schema; the replica's tables stay as they were"
    ),
    ("key-create", "none"): "key-create publishes the database it staged with the key in it",
    ("serve", "empty, compacting"): "serve makes a new database in an empty file",
    ("import", "none"): "import publishes a database of its own making",
}
# The rows whose own job writes the database, on a database of this schema, when they succeed;
# serve's start records what it keeps of each page's history, and puts a rollback-journal
# file in WAL (what the WAL mode forces).
OWN_WRITE: dict[str, str] = {
    "serve": "records the history bound it keeps (Store.record_history_bound); WAL",
    "key-create": "stores the new key",
    "source-add": "stores the source",
    "credential-add": "stores the credential",
    "import": "imports the archive",
    "compact": "rewrites the file",
    "compact --if-due": "rewrites the file when due",
    "plan-set": "sets the plan",
    "arrange": "files and tidies pages",
    "repair": "rewrites each page that cites another project's notes",
    "export": "records the export in the workspace's audit log",
}
# The cells where only SQLite's side files may differ, each with its reason: the database's own
# bytes are compared all the same.
SIDE_FILES_ONLY: dict[tuple[str, str], str] = {
    **{
        (row, "busy clears"): (
            "the holder's connection made the -wal and -shm files; a command that neither makes"
            " nor upgrades the database does not fold a log it found at its open on its last"
            " close (the store's SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE), so they stay beside the file"
        )
        for row in ROWS
    },
    **{
        (row, "busy clears"): (
            "it reads through a read-only connection, which may not remove the write-ahead log"
            " and its index that the holder left beside the file"
        )
        for row in ("stats", "audit-verify")
    },
}
SIDE_FILES = {"cairn.db-wal", "cairn.db-shm"}
# Files a command writes on purpose, beside the database, when it succeeds.
OUTPUTS = {"key-create": {"key.env"}, "export": {"export.zip"}}

NOTHING_CHANGED = (
    "changed nothing",
    "nothing was changed",
    "nothing to change",
    "nothing was imported",
)
START_A_SERVER = "start the server once on it"
UNINITIALIZED = "is uninitialized: start serve to initialize it"
# Phrases that are true of one family of kinds only.
ONLY_ON: dict[str, tuple[str, ...]] = {
    START_A_SERVER: OLDER,
    "older schema": OLDER,
    "is not a cairn database": ("not sqlite", *OTHERS),
    UNINITIALIZED: EMPTY,
    "needs a cairn of schema version": ("newer refused",),
    "no database at": ("none", "folder"),  # true of a folder too: there is no database there
    "is a folder": ("folder",),
    "is busy": ("busy held",),
    "cannot read the database at": ("busy held",),
    "has multiple hard links": ("second name",),
}
# Where each phrase is the code's own: the constant it is a part of. "no database at" and
# UnknownSchema's sentence are written where they are said; cli's description quotes both.
PHRASES: dict[str, str] = {
    START_A_SERVER: cli_data.OLDER_SCHEMA,
    "older schema": cli_data.OLDER_SCHEMA,
    "is not a cairn database": cli.NOT_A_DATABASE,
    UNINITIALIZED: cli.UNINITIALIZED,
    "needs a cairn of schema version": cli.__doc__,
    "no database at": cli.__doc__,
    "is a folder": cli.A_FOLDER,
    "is busy": cli.BUSY,
    "cannot read the database at": cli_stats.CANNOT_READ,
    "was not made by this version of cairn": cli.__doc__,
}


# What a refusal on each kind says, one of these, so that no refusal is silent.
SAYS: dict[str, tuple[str, ...]] = {
    "none": ("no database at",),
    **{kind: (UNINITIALIZED,) for kind in EMPTY},
    "not sqlite": ("is not a cairn database",),
    **{kind: ("is not a cairn database",) for kind in OTHERS},
    f"other {SCHEMA_VERSION}": ("was not made by this version of cairn",),  # UnknownSchema
    **{kind: ("older schema",) for kind in OLDER},
    "newer refused": ("needs a cairn of schema version",),
    "busy held": ("is busy",),
    "folder": ("no database at", "is a folder"),
}


# A row whose refusal on a kind is its own, before the kind's line would come.
OWN_SAYS = {
    ("key-create --project", "none"): (cli.NO_SUCH_PROJECT.format(name="nope"),),
    # stats and audit-verify read with SQLite alone and say any SQLite error in their own line
    # (cli_stats.CANNOT_READ, which names the error's class), a busy one included
    **{(row, "busy held"): ("cannot read the database at",) for row in ("stats", "audit-verify")},
}


def expected_exit(row: str, kind: str) -> int:
    """The exit code of the cell."""
    command = ROWS[row][0]
    if kind == "second name":
        return 2  # every command refuses a database with a second name (DatabaseLinks)
    if row == "serve, model unset":
        return 2  # the model's settings are read before the lock and the open
    if row == "key-create --project":
        return 1  # no project nope: refused before a database is made where there is none
    if kind in THIS_SCHEMA:
        return THIS_SCHEMA_EXIT[row]
    if kind == "none":
        return 0 if command in MAKES else 1
    if kind in EMPTY:
        return 0 if row == "serve" else 1  # only serve initialises an existing empty file
    if kind in OLDER:
        return 0 if row == "serve" else 1
    return 1  # not sqlite, another program's, newer refused, busy held, a folder


# What each command exits on a database of this schema with the smallest arguments: 0 when
# it did its job, 1 when what it names is not there.
THIS_SCHEMA_EXIT: dict[str, int] = {
    "arrange": 0,
    "audit-verify": 0,
    "compact": 0,
    "compact --if-due": 0,
    "credential-add": 0,
    "credential-list": 0,
    "credential-remove": 1,  # no credential named acct
    "export": 0,
    "import": 1,  # the archive's pages are there already: nothing was imported
    "key-create": 0,
    "key-create --project": 1,  # no project named nope: no key was made
    "key-list": 0,
    "key-revoke": 1,  # no key labelled ci
    "plan-set": 0,
    "repair": 0,
    "reseal": 2,  # no key to reseal with in the environment
    "serve": 0,
    "serve, model unset": 2,
    "session-end": 0,
    "source-add": 1,  # a langfuse source needs base_url
    "source-disable": 1,  # no source named traces
    "source-enable": 1,
    "source-list": 0,
    "source-preview": 1,
    "source-remove": 1,
    "source-sync": 1,
    "stats": 0,
    "usage": 0,
    "waiting": 0,
    "workspace-list": 0,
}

FOREIGN = "CREATE TABLE recipes (name TEXT, steps TEXT)"


def other_program(db: Path, version: int) -> None:
    with closing(sqlite3.connect(db)) as con:
        con.execute(FOREIGN)
        con.execute("INSERT INTO recipes VALUES ('bread', 'knead')")
        con.execute(f"PRAGMA user_version = {version}")
        con.commit()


def this_schema(db: Path) -> None:
    made(db)


def newer(db: Path, floor: int) -> None:
    made(db)
    with closing(sqlite3.connect(db)) as con:
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        con.execute("UPDATE oldest_build SET version = ?", (floor,))
        con.commit()


def rollback(db: Path) -> None:
    made(db)
    with closing(sqlite3.connect(db)) as con:
        con.execute("PRAGMA journal_mode = DELETE")


# A replica's own tables, as Litestream makes them in the database it replicates.
REPLICA = (
    "CREATE TABLE _litestream_seq (id INTEGER PRIMARY KEY, seq INTEGER)",
    "INSERT INTO _litestream_seq (id, seq) VALUES (1, 7)",
    "CREATE TABLE _litestream_lock (id INTEGER)",
)


def replicated(build):
    def at(db: Path) -> None:
        build(db)
        gc.collect()
        with closing(sqlite3.connect(db)) as con:
            for statement in REPLICA:
                con.execute(statement)
            con.commit()

    return at


def older(build):
    def at(db: Path) -> None:
        built = build(db.parent)
        assert built == db
        gc.collect()  # connections the builders leave open are closed before the copy
        for leftover in db.parent.glob("made.db*"):
            leftover.unlink()

    return at


BUILDERS = {
    "not sqlite": lambda db: db.write_bytes(b"recipes: bread, knead, bake\n" * 8),
    **{f"other {v}": partial(other_program, version=v) for v in OTHER_VERSIONS},
    "cairn 28": older(at_28_file),
    "cairn 31": older(at_31),
    "cairn 28, replicated": replicated(older(at_28_file)),
    "cairn wal": this_schema,
    "cairn, replicated": replicated(this_schema),
    "cairn rollback": rollback,
    "newer runs": partial(newer, floor=SCHEMA_VERSION),
    "newer refused": partial(newer, floor=SCHEMA_VERSION + 1),
}
for kind in ("busy held", "busy clears", "readable", "second name"):
    BUILDERS[kind] = this_schema


@pytest.fixture(scope="module")
def templates(tmp_path_factory) -> dict[str, bytes]:
    """Each kind's file, made once, as bytes; and the archive an import reads."""
    made_files: dict[str, bytes] = {}
    for kind, build in BUILDERS.items():
        folder = tmp_path_factory.mktemp("kind")
        db = folder / "cairn.db"
        build(db)
        gc.collect()
        assert sorted(p.name for p in folder.iterdir()) == ["cairn.db"], kind
        made_files[kind] = db.read_bytes()
    folder = tmp_path_factory.mktemp("archive")
    db = folder / "cairn.db"
    with closing(Store(db)) as store:  # one workspace, one project, one note
        store.create_project("east")
        Memory(store, lint_gate).note("east", "# Far\n\nWren keeps the far log.\n")
    out = folder / "archive.zip"
    assert (
        cli.main(["export", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--out", str(out)])
        == 0
    )
    made_files["archive"] = out.read_bytes()
    return made_files


def snapshot(folder: Path) -> dict[str, object]:
    """Every file under `folder`: its bytes' digest and its mode; a folder as such."""
    found: dict[str, object] = {}
    for path in sorted(folder.rglob("*")):
        name = str(path.relative_to(folder))
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            found[name] = ("folder", stat.S_IMODE(info.st_mode))
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            found[name] = (digest, stat.S_IMODE(info.st_mode))
    return found


def holding(db: Path) -> sqlite3.Connection:
    """A second connection that holds the whole file: anyone else's read is busy meanwhile."""
    con = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
    con.execute("PRAGMA locking_mode = EXCLUSIVE")
    con.execute("BEGIN EXCLUSIVE")
    con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return con


def let_go(con: sqlite3.Connection) -> None:
    con.execute("ROLLBACK")
    con.close()


@pytest.fixture
def listened(monkeypatch) -> list:
    """uvicorn.run as `serve` would call it, recorded and never run."""
    calls: list = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    return calls


def run_cell(row: str, kind: str, folder: Path, templates, monkeypatch, capsys):
    """Lay the kind's file in `folder`, run the row's command on it, and return the exit
    code (or the exception's name), stdout, stderr, and the folder before and after."""
    command, extra = ROWS[row]
    db = folder / "cairn.db"
    if kind in templates:
        db.write_bytes(templates[kind])
        os.chmod(db, 0o644 if kind == "readable" else 0o600)
    elif kind in ("empty", "empty, compacting"):
        db.touch(mode=0o600)
    elif kind == "folder":
        db.mkdir(mode=0o700)
    (folder / "archive.zip").write_bytes(templates["archive"])
    (folder / "plans.yaml").write_text(PLANS_YAML, encoding="utf-8")
    with ExitStack() as stack:
        if kind == "second name":
            os.link(db, folder / "first.db")
        if kind == "empty, compacting":
            lock = os.open(f"{os.path.realpath(db)}.compact.lock", os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stack.callback(os.close, lock)
        before = snapshot(folder)  # the holder below comes and goes: not the command's
        holder = holding(db) if kind in ("busy held", "busy clears") else None
        argv = [command, "--db", str(db), *(arg.format(dir=folder) for arg in extra)]
        released = impatient = None
        if kind == "busy clears":
            released = threading.Timer(0.3, let_go, (holder,))
            released.start()
        elif kind == "busy held":
            impatient = pytest.MonkeyPatch()
            impatient.setattr(sqlite3, "connect", partial(sqlite3.connect, timeout=0.05))
        try:
            code: int | str = cli.main(argv)
        except BaseException as error:
            code = f"raised {type(error).__name__}: {error}"
        finally:
            if released is not None:
                released.join()
            elif holder is not None:
                impatient.undo()
                let_go(holder)
        gc.collect()
        after = snapshot(folder)  # before the compaction's lock is let go
    out, err = capsys.readouterr()
    return code, out, err, before, after


def changed(row: str, code, before: dict, after: dict) -> set[str]:
    """The files whose bytes or mode differ, or that came or went; a command's own output
    file, when it succeeded, is not counted."""
    differ = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    if code == 0:
        differ -= OUTPUTS.get(row, set())
    return differ


def may_write(row: str, kind: str, code) -> bool:
    if (row, kind) in MAY_WRITE:
        return True
    return kind in THIS_SCHEMA and code == 0 and row in OWN_WRITE


def false_words(row: str, kind: str, code, said: str, differ: set[str]) -> list[str]:
    """What the cell's words say that its bytes do not show."""
    wrong = []
    lowered = said.lower()
    if differ:
        wrong += [
            f"says {phrase!r} beside changed bytes"
            for phrase in NOTHING_CHANGED
            if phrase in lowered
        ]
        if code != 0 and "now at schema" not in said and "made a new database" not in said:
            wrong.append("failed after changing bytes and does not say what changed")
    for phrase, kinds in ONLY_ON.items():
        if phrase in lowered and kind not in kinds:
            wrong.append(f"says {phrase!r} of a file of kind {kind!r}")
    return wrong


# CAIRN_TABLE_OBSERVE=1 prints each cell's answer instead of asserting (read at import: the
# tests run with a cleared environment).
OBSERVE = bool(os.environ.get("CAIRN_TABLE_OBSERVE"))
CELLS = [(row, kind) for row in ROWS for kind in KINDS]


def table_size() -> tuple[int, int]:
    return len(ROWS), len(KINDS)


@pytest.mark.parametrize(("row", "kind"), CELLS, ids=[f"{r} | {k}" for r, k in CELLS])
def test_the_cell(row, kind, tmp_path, templates, monkeypatch, capsys, listened):
    if kind == "readable":
        folder = Path(tempfile.mkdtemp(prefix="cairn-table-", dir="/tmp"))
        os.chmod(folder, 0o755)
    else:
        folder = tmp_path
    try:
        code, out, err, before, after = run_cell(row, kind, folder, templates, monkeypatch, capsys)
    finally:
        if kind == "readable":
            shutil.rmtree(folder, ignore_errors=True)
    differ = changed(row, code, before, after)
    said = out + err
    if OBSERVE:
        first = err.strip().splitlines()[:2]
        print(f"OBSERVED|{row}|{kind}|{code}|{sorted(differ)}|{first}", file=sys.__stdout__)
        return
    assert code == expected_exit(row, kind), (code, err)
    # the side files a cell may leave changed are not the command's: its words are held to the rest
    differ -= SIDE_FILES if (row, kind) in SIDE_FILES_ONLY else set()
    if not may_write(row, kind, code):
        assert differ == set(), f"bytes changed: {sorted(differ)}; said: {said!r}"
    assert false_words(row, kind, code, said, differ) == [], said
    says = OWN_SAYS.get((row, kind), SAYS.get(kind))
    if kind == "second name":
        says = (hard_linked(folder / "cairn.db"),)  # the store's own line for the file
    if code != 0 and row != "serve, model unset" and says is not None:
        assert any(phrase in said for phrase in says), said
    if ROWS[row][0] == "serve" and code != 0:
        assert listened == [], "serve started listening"


def test_the_table_has_every_command_and_every_kind():
    assert sorted({command for command, _ in ROWS.values()}) == COMMANDS
    assert len(CELLS) == len(ROWS) * len(KINDS)
    assert set(MAY_WRITE) <= set(CELLS) and set(SIDE_FILES_ONLY) <= set(CELLS)
    assert set(THIS_SCHEMA_EXIT) == set(ROWS) or OBSERVE
    assert all(MAY_WRITE.values()) and all(SIDE_FILES_ONLY.values())  # each with its reason


def test_every_phrase_is_the_codes_own(tmp_path):
    """Each phrase a cell looks for is a part of the code's own words, never of what a run
    printed: a constant it is part of, or the line cairn's own function makes."""
    for phrase in {*ONLY_ON, *(p for says in (*SAYS.values(), *OWN_SAYS.values()) for p in says)}:
        if phrase == "has multiple hard links":
            db = tmp_path / "cairn.db"
            db.touch()
            os.link(db, tmp_path / "first.db")
            assert phrase in hard_linked(db)
        elif phrase in PHRASES:
            assert phrase in " ".join(PHRASES[phrase].split()), phrase
        else:
            assert phrase == cli.NO_SUCH_PROJECT.format(name="nope"), phrase


def test_the_run_guide_says_what_the_table_holds():
    """run.md's sentences on what each command does with an older, a foreign or a linked file,
    each held to the table's cells (test_guide_facts_own_project_unit pins RUN_COMMANDS)."""
    from test_guide_facts_own_project_unit import RUN_COMMANDS, guide

    run = guide("run")
    # "only `serve` does": the one row that may change a database of an older schema
    assert {row for row, kind in MAY_WRITE if kind in OLDER} == {"serve"}
    assert "only `serve` does" in RUN_COMMANDS
    # "A file that is not cairn's is refused ... and is left unchanged."
    assert not [cell for cell in MAY_WRITE if cell[1] in OTHERS]
    assert all(expected_exit(row, kind) != 0 for row in ROWS for kind in OTHERS)
    assert "A file that is not cairn's is refused with" in run
    # "more may be, such as the two tables the replica service (Litestream) keeps in it"
    assert expected_exit("serve", "cairn 28, replicated") == 0
    assert ("serve", "cairn 28, replicated") in MAY_WRITE
    assert "the two tables the replica service (Litestream) keeps in it" in run
    # "A database with multiple hard links is refused"
    assert {expected_exit(row, "second name") for row in ROWS} == {2}
    assert "A database with multiple hard links is refused" in run
