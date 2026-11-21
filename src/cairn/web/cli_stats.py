"""`python -m cairn.server stats --db PATH [--json]` (Q1 C; cairn.web.cli dispatches here): the
install's numbers and each workspace's, read without writing anything (cairn.web.stats says
what they are).

It never opens the store, which would set the file's journal mode (and, by its default,
upgrade an older database): it opens the file with SQLite alone, read-only. With no "-wal"
file beside it no server is writing it, so it is opened immutable (no lock, no "-shm" or
"-wal" file made), which also reads a database on a volume this user may not write, such as
a mounted backup. With a "-wal" file (a server is running, or one stopped without its last
checkpoint) it is opened read-only, reading through the log as SQLite does; where SQLite
cannot, the command refuses. Nothing is written, made, removed, or changed in mode.

It refuses as the other commands that open an existing database do, with their lines (exit
1, one line on stderr, nothing on stdout): no database at the path or a folder there ("no
database at PATH"), a file that is not a database, one it may not read, and one of a newer
schema, and one at this schema's version that lacks one of its tables (CannotOpen,
SchemaTooNew and UnknownSchema, which cairn.web.cli.main turns into its lines). An older cairn schema
gets the common command-line advice to start the server once to upgrade it.

`--json` prints one object (cairn.web.stats.numbers); without it, one line per number of
the install, then one line per workspace, by its id. Counts, sizes and times only. The
install's record of compaction is one line of its own (`compaction:`, Privacy 1), in words an
operator can act on, with the command that finishes the job; it names no workspace.

`--other-projects` also counts, by workspace, the pages any version of which cites a passage
that no note of the page's own project holds, another project's or one no longer held
(cairn.store.erasure.count_pages_citing_other_projects: the search the
store's repair, hold_to_own_projects, makes, writing nothing): `other_projects` in the
object, one line without `--json`. It reads every page version, so it is asked for, never
paid on every call. cairn's writers make no such page; one there was written behind the
store's back, or saved through Store.save_page citing passages the store does not hold.
"""

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cairn.store import CannotOpen
from cairn.store.base import _iso
from cairn.store.erasure import count_pages_citing_other_projects
from cairn.store.numbers import part_rows
from cairn.store.schema import ForeignSchema, _refuse, current_version
from cairn.web.cli_data import DB_EXISTS, OLDER_SCHEMA
from cairn.web.stats import COUNTS, numbers

COMMAND = "stats"
OLDER_DATABASE = OLDER_SCHEMA.replace("{command}", COMMAND)
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
    stats.add_argument(
        "--other-projects",
        action="store_true",
        help="also count the pages that cite a note of another project (reads every page version)",
    )


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
            version = current_version(con)
        except ForeignSchema:
            raise
        except sqlite3.Error as error:
            _refuse(db, error)  # CannotOpen: not a database, or one it may not read
            print(CANNOT_READ.format(db=db, reason=type(error).__name__), file=sys.stderr)
            return 1
        now = _iso(datetime.now(UTC))
        try:
            found = numbers(lambda part: part_rows(con, part, now), str(db), version)
            if args.other_projects:
                found["other_projects"] = count_pages_citing_other_projects(con)
        except sqlite3.Error as error:
            _refuse(db, error)
            print(CANNOT_READ.format(db=db, reason=type(error).__name__), file=sys.stderr)
            return 1
    finally:
        con.close()
    print(json.dumps(found, indent=2) if args.json else _plain(found, db))
    return 0


def compaction_line(state: dict[str, Any], db: Path | str) -> str:
    """The install's record of compaction (stats.numbers' `compaction`) as one line: whether
    something was deleted since the last compaction finished and since when, whether only the
    write-ahead log is left to empty, when the last rewrite began, and the command that
    finishes the job. Times only: no workspace, no name."""
    began = f"the last rewrite began {state['compacted_at'] or 'never'}"
    deleted = state["deleted_at"]
    if deleted is None:
        return f"compaction: nothing deleted since the last compaction finished; {began}"
    if state["rewritten"]:
        return (
            f"compaction: the file was rewritten after what was deleted since {deleted}; only"
            f" its write-ahead log is left to empty; {began}; `compact --if-due --db {db}`"
            " empties it once nothing else holds the log, such as a replica"
        )
    return (
        f"compaction: what was deleted since {deleted} is still in the database file; {began};"
        f" `compact --db {db}` rewrites it (serve does it by itself after --compact-every"
        " hours)"
    )


def _plain(found: dict[str, Any], db: Path) -> str:
    lines = [
        f"schema {found['schema']}",
        f"database {found['db_bytes']} bytes, write-ahead log {found['wal_bytes']} bytes",
        f"free on its disk {found['free_bytes']} bytes"
        + (" (low: under twice the database's size)" if found["low_disk"] else ""),
        compaction_line(found["compaction"], db),
        _counts("install", found),
    ]
    if "other_projects" in found:
        by = found["other_projects"]
        each = "".join(f"; workspace {account} {count}" for account, count in by.items())
        lines.append(f"pages citing notes of other projects: {sum(by.values())}{each}")
    for account, mine in found["workspaces"].items():
        lines.append(_counts(f"workspace {account}", mine))
    return "\n".join(lines)


def _counts(who: str, mine: dict[str, Any]) -> str:
    said = ", ".join(f"{name.replace('_', ' ')} {mine[name]}" for name in COUNTS)
    said += f", newest note {mine['newest_note'] or 'none'}"
    for kind, tally in mine["sources"].items():
        said += f", {kind} sources {tally['on']} on {tally['failed']} failed"
    return f"{who}: {said}"
