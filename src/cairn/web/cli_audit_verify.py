"""`python -m cairn.server audit-verify --db PATH [--workspace ID]` (cairn.web.cli dispatches
here): whether each workspace's audit trail still chains (cairn.store.audit.check_trail).

It opens the file as `stats` does (cairn.web.cli_stats): read-only, with SQLite alone, never
through the store, so that nothing it opens can write the very values it is asked to check.
It refuses as `stats` does, with the same lines, and an older schema with its own
OLDER_DATABASE. Nothing is written, made, removed, or changed in mode. It runs beside a
serving cairn: the check is one read transaction (check_trail), so a prune the server commits
meanwhile does not change the verdict.

For each workspace, in creation order (or the one --workspace names), one line: how many
events it checked and that the chain holds, or the first event whose value does not follow
from the one before, by its number and time, never its content. Exit 0 when every chain
holds, 1 when one does not or when --workspace names no workspace of the database.
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

from cairn.store import SCHEMA_VERSION, CannotOpen
from cairn.store.audit import check_trail
from cairn.store.schema import (
    ForeignSchema,
    Uninitialized,
    _check_source,
    _check_tables,
    _refuse,
    _schema_version,
)
from cairn.web.cli_data import DB_EXISTS
from cairn.web.cli_stats import CANNOT_READ, _connect

COMMAND = "audit-verify"
OLDER_DATABASE = (
    "the database at {db} has an older schema (version {found}; this cairn's is {known}): start"
    " the server once on it (serve --db) to bring it up to date, then run audit-verify again;"
    " it only reads, so it does not upgrade it"
)
NO_WORKSPACE = "no workspace {workspace} in the database at {db}"
HOLDS = "workspace {workspace}: {checked} {events} checked; the chain holds"
BROKEN = (
    "workspace {workspace}: event {n} ({at}) does not follow from the one before;"
    " {before} {events} before it hold"
)


def add_command(commands: Any) -> None:
    verify = commands.add_parser(
        COMMAND,
        help="check that each workspace's audit trail still chains, each event to the one"
        " before it; it only reads",
        allow_abbrev=False,
    )
    verify.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
    verify.add_argument("--workspace", help="check this workspace only, by its id")


def _events(count: int) -> str:
    return "event" if count == 1 else "events"


def run(args: argparse.Namespace) -> int:
    db: Path = args.db
    if not db.is_file():
        print(f"no database at {db}", file=sys.stderr)
        return 1
    try:
        con = _connect(db)
    except CannotOpen:  # cairn.web.cli.main says it as for every command
        raise
    except sqlite3.Error as error:
        print(CANNOT_READ.format(db=db, reason=type(error).__name__), file=sys.stderr)
        return 1
    try:
        try:
            version = _schema_version(con)  # SchemaTooNew: a newer cairn it may not read
            if version >= SCHEMA_VERSION:
                _check_tables(con)  # UnknownSchema: this version did not make it
            if version == 0:
                raise Uninitialized
            if version < SCHEMA_VERSION:
                _check_source(con, version)
            if version < SCHEMA_VERSION:
                print(
                    OLDER_DATABASE.format(db=db, found=version, known=SCHEMA_VERSION),
                    file=sys.stderr,
                )
                return 1
            try:
                found = check_trail(con, args.workspace)
            except KeyError:
                print(NO_WORKSPACE.format(workspace=args.workspace, db=db), file=sys.stderr)
                return 1
        except ForeignSchema:
            raise
        except sqlite3.Error as error:
            _refuse(db, error)  # CannotOpen: not a database, or one it may not read
            print(CANNOT_READ.format(db=db, reason=type(error).__name__), file=sys.stderr)
            return 1
    finally:
        con.close()
    for trail in found:
        if trail.broken is None:
            print(
                HOLDS.format(
                    workspace=trail.account_id,
                    checked=trail.checked,
                    events=_events(trail.checked),
                )
            )
        else:
            n, at = trail.broken
            before = trail.checked - 1
            print(
                BROKEN.format(
                    workspace=trail.account_id, n=n, at=at, before=before, events=_events(before)
                )
            )
    return 1 if any(trail.broken is not None for trail in found) else 0
