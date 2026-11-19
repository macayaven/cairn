"""`python -m cairn.server stats --db PATH [--json]` (Q1 C; cairn.web.cli dispatches here): the
install's numbers and each workspace's, read without writing anything (cairn.web.stats says
what they are).

It never opens the store, which would upgrade an older database and set the file's journal
mode: it opens the file with SQLite alone, read-only. With no "-wal" file beside it no server
is writing it, so it is opened immutable (no lock, no "-shm" or "-wal" file made), which
also reads a database on a volume this user may not write, such as a mounted backup. With a
"-wal" file (a server is running, or one stopped without its last checkpoint) it is opened
read-only, reading through the log as SQLite does; where SQLite cannot, the command refuses.
Nothing is written, made, removed, or changed in mode.

It refuses as the other commands that open an existing database do, with their lines (exit
1, one line on stderr, nothing on stdout): no database at the path or a folder there ("no
database at PATH"), a file that is not a database, one it may not read, and one of a newer
schema, and one at this schema's version that lacks one of its tables (CannotOpen,
SchemaTooNew and UnknownSchema, which cairn.web.cli.main turns into its lines). One of
an older schema is refused too, on purpose (OLDER_DATABASE): the other commands upgrade it,
and a command that only reads must not; starting the server once upgrades it.

`--json` prints one object (cairn.web.stats.numbers); without it, one line per number of
the install, then one line per workspace, by its id. Counts, sizes and times only.
"""

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cairn.store import SCHEMA_VERSION, CannotOpen
from cairn.store.base import _iso
from cairn.store.numbers import part_rows
from cairn.store.schema import _check_tables, _refuse, _schema_version
from cairn.web.cli_data import DB_EXISTS
from cairn.web.stats import COUNTS, numbers

COMMAND = "stats"
OLDER_DATABASE = (
    "the database at {db} has an older schema (version {found}; this cairn's is {known}): start"
    " the server once on it (serve --db) to bring it up to date, then run stats again; stats"
    " only reads, so it does not upgrade it"
)
CANNOT_READ = "cannot read the database at {db}: {reason}"


def add_command(commands: Any) -> None:
    stats = commands.add_parser(
        COMMAND,
        help="print the install's counts, sizes and free disk space, and each workspace's"
        " counts; it only reads",
        allow_abbrev=False,
    )
    stats.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
    stats.add_argument("--json", action="store_true", help="print one JSON object")


def _connect(db: Path) -> sqlite3.Connection:
    """The database read-only: immutable when no "-wal" file is beside it (the module's
    docstring). CannotOpen when SQLite refuses the file."""
    wal = db.with_name(db.name + "-wal")
    query = "mode=ro" if wal.exists() else "mode=ro&immutable=1"
    uri = f"{db.resolve().as_uri()}?{query}"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error as error:
        _refuse(db, error)
        raise


def run(args: argparse.Namespace) -> int:
    db: Path = args.db
    if not db.is_file():
        print(f"no database at {db}", file=sys.stderr)
        return 1
    try:
        con = _connect(db)
    except CannotOpen:  # cairn.web.cli.main says it as for every command
        raise
    except sqlite3.Error as error:  # not one of SQLite's refusals to open the file
        print(CANNOT_READ.format(db=db, reason=type(error).__name__), file=sys.stderr)
        return 1
    try:
        try:
            version = _schema_version(con)  # SchemaTooNew: a newer cairn wrote it
            if version >= SCHEMA_VERSION:  # a newer one runs here when its floor allows
                _check_tables(con)  # UnknownSchema: this version did not make it
        except sqlite3.Error as error:
            _refuse(db, error)  # CannotOpen: not a database, or one it may not read
            print(CANNOT_READ.format(db=db, reason=type(error).__name__), file=sys.stderr)
            return 1
        if version < SCHEMA_VERSION:
            print(
                OLDER_DATABASE.format(db=db, found=version, known=SCHEMA_VERSION),
                file=sys.stderr,
            )
            return 1
        now = _iso(datetime.now(UTC))
        try:
            found = numbers(lambda part: part_rows(con, part, now), str(db), version)
        except sqlite3.Error as error:
            _refuse(db, error)
            print(CANNOT_READ.format(db=db, reason=type(error).__name__), file=sys.stderr)
            return 1
    finally:
        con.close()
    print(json.dumps(found, indent=2) if args.json else _plain(found))
    return 0


def _plain(found: dict[str, Any]) -> str:
    lines = [
        f"schema {found['schema']}",
        f"database {found['db_bytes']} bytes, write-ahead log {found['wal_bytes']} bytes",
        f"free on its disk {found['free_bytes']} bytes"
        + (" (low: under twice the database's size)" if found["low_disk"] else ""),
        _counts("install", found),
    ]
    for account, mine in found["workspaces"].items():
        lines.append(_counts(f"workspace {account}", mine))
    return "\n".join(lines)


def _counts(who: str, mine: dict[str, Any]) -> str:
    said = ", ".join(f"{name.replace('_', ' ')} {mine[name]}" for name in COUNTS)
    said += f", newest note {mine['newest_note'] or 'none'}"
    for kind, tally in mine["sources"].items():
        said += f", {kind} sources {tally['on']} on {tally['failed']} failed"
    return f"{who}: {said}"
