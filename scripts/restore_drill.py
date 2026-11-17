"""Restore drill for cairn: python3 scripts/restore_drill.py (--replica URL | --db-file PATH)

Checks that the backup restores to a database that looks like cairn's. With --replica it
restores the latest Litestream replica into a temporary directory; with --db-file it checks a
SQLite file you already have, which exercises everything except the restore. Either way it then
opens the file read-only and reports: PRAGMA integrity_check; that cairn's five core tables are
there with the columns each has had since schema 2, and that the schema version is 2 or more; a
row count for every table that exists; and the newest note's timestamp. That is all it
checks: it does not compare the whole schema with any one version of cairn, it accepts a version
newer than the cairn you run, and it does not start cairn on the file. The server starting on
the restored database is the last step of a real recovery and the only proof that it works. The
file is never written, and --db-file is safe on a database a running cairn has open. Standard
library only, Python 3.10 or later. Exit code 0 when every check passes, 1 when one fails, 2 on
bad arguments.

    python3 scripts/restore_drill.py --replica gs://BUCKET/cairn
    python3 scripts/restore_drill.py --replica gs://BUCKET/cairn --docker
    python3 scripts/restore_drill.py --db-file runs/dev/cairn.db --min-notes 1

The restore runs `litestream restore -o TMP/cairn.db URL` (Litestream 0.5 syntax) with
--litestream BINARY, or in a container with --docker [IMAGE] (the pinned image by default).
With neither flag it uses `litestream` from PATH, or the container when there is none. It
never passes -if-replica-exists: in a drill, a missing replica is a failure.

Credentials are whatever Litestream itself reads and are never printed. The container gets
them two ways: the variables in PASS_ENV are passed by name, so their values never appear on
a command line, and a Google credentials file (GOOGLE_APPLICATION_CREDENTIALS, or the one
`gcloud auth application-default login` writes) is mounted read-only. The container runs as
the caller's user with at most 1 GiB of memory, one CPU and 256 processes, and is removed at
the end along with the temporary directory, pass or fail.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

# The version and index digest that deploy/ and infra/gcp pin.
LITESTREAM_IMAGE = (
    "litestream/litestream:0.5.18"
    "@sha256:513bff62a88fc0c86c73d27333c41c54c89fe890a7c32a551600adff9309b11b"
)
LIMITS = ("--memory", "1g", "--cpus", "1", "--pids-limit", "256")
RESTORE_TIMEOUT = 1800  # seconds
SQLITE_MAGIC = b"SQLite format 3\x00"
PASS_ENV = (
    "LITESTREAM_ACCESS_KEY_ID",
    "LITESTREAM_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "LITESTREAM_AZURE_ACCOUNT_KEY",
)
IN_CONTAINER = "/restore"
CREDENTIALS_IN_CONTAINER = "/gcp-credentials.json"


class Failed(Exception):
    pass


# What every database cairn has written since it had workspaces holds, whatever its version:
# its five core tables, each with the columns it has had since schema 2. Later versions add
# tables and columns; none removes one of these.
CAIRN_TABLES = {
    "accounts": {"id", "name", "created_at"},
    "projects": {"id", "account_id", "name", "created_at"},
    "notes": {"id", "project_id", "text", "text_sha256", "title", "created_at"},
    "spans": {"id", "note_id", "kind", "heading_path", "start_line", "end_line", "text"},
    "page_versions": {"project_id", "slug", "version", "title", "body", "claims", "created_at"},
}
FIRST_SCHEMA_VERSION = 2  # the first numbered migration; an older or foreign file reads 0 or 1


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise Failed(message)


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def google_credentials() -> Path | None:
    """The credentials file Google's client libraries would pick up on this machine."""
    explicit = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if explicit:
        return Path(explicit)
    config = os.environ.get("CLOUDSDK_CONFIG") or str(Path.home() / ".config" / "gcloud")
    default = Path(config) / "application_default_credentials.json"
    return default if default.is_file() else None


class Drill:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.tmp = Path(tempfile.mkdtemp(prefix="cairn-drill-"))
        self.container = f"cairn-drill-{os.getpid()}"
        self.used_docker = False
        self.how = ""  # what ran the restore, for the report
        self.db = Path(args.db_file) if args.db_file else self.tmp / "cairn.db"
        self.con: sqlite3.Connection | None = None
        self.tables: list[str] = []

    # -- the restore --------------------------------------------------------------------------

    def restore_command(self) -> list[str]:
        url = self.args.replica
        binary = self.args.litestream or (None if self.args.docker else shutil.which("litestream"))
        if binary:
            expect(shutil.which(binary) is not None, f"no litestream binary at {binary!r}")
            self.how = binary
            return [binary, "restore", "-o", str(self.db), url]
        expect(shutil.which("docker") is not None, "neither litestream nor docker is on PATH")
        self.used_docker = True
        command = ["docker", "run", "--rm", "--name", self.container, *LIMITS]
        command += ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"]
        command += ["-v", f"{self.tmp}:{IN_CONTAINER}"]
        for name in PASS_ENV:
            if name in os.environ:
                command += ["-e", name]  # by name: docker reads the value from our environment
        credentials = google_credentials()
        if credentials is not None:
            expect(credentials.is_file(), f"no credentials file at {credentials}")
            command += ["-v", f"{credentials.resolve()}:{CREDENTIALS_IN_CONTAINER}:ro"]
            command += ["-e", f"GOOGLE_APPLICATION_CREDENTIALS={CREDENTIALS_IN_CONTAINER}"]
        image = self.args.docker or LITESTREAM_IMAGE
        self.how = f"docker run {image}"
        return [*command, image, "restore", "-o", f"{IN_CONTAINER}/{self.db.name}", url]

    def restores_the_latest_replica(self) -> str:
        command = self.restore_command()
        how = self.how
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=RESTORE_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            raise Failed(f"{how} did not finish in {RESTORE_TIMEOUT} s") from None
        tail = (result.stdout + result.stderr).strip()[-800:]
        expect(result.returncode == 0, f"{how} restore exited {result.returncode}: {tail}")
        expect(self.db.is_file(), f"{how} restore exited 0 but wrote no database: {tail}")
        source = self.args.replica.split("?", 1)[0]
        return f"{how} restored {source} ({self.db.stat().st_size} bytes)"

    def has_the_file(self) -> str:
        expect(self.db.is_file(), f"{self.db} is not a file")
        return f"{self.db} ({self.db.stat().st_size} bytes), no restore"

    # -- the checks ---------------------------------------------------------------------------

    def query(self, sql: str) -> list[tuple]:
        assert self.con is not None
        try:
            return self.con.execute(sql).fetchall()
        except sqlite3.Error as error:
            raise Failed(f"{sql!r} failed: {error}") from None

    def opens_read_only(self) -> str:
        with self.db.open("rb") as handle:
            header = handle.read(len(SQLITE_MAGIC))
        expect(header == SQLITE_MAGIC, "not a SQLite database (empty file or wrong header)")
        # A WAL-mode database that is in use has -wal and -shm files beside it, and SQLite
        # must read them. One that has neither is at rest: immutable=1 then reads the file
        # alone, so SQLite does not create those two files next to it.
        in_use = any(Path(f"{self.db}{suffix}").exists() for suffix in ("-wal", "-shm"))
        options = "mode=ro" if in_use else "mode=ro&immutable=1"
        try:
            self.con = sqlite3.connect(f"{self.db.resolve().as_uri()}?{options}", uri=True)
        except sqlite3.Error as error:
            raise Failed(f"cannot open the file: {error}") from None
        self.query("PRAGMA query_only = ON")  # belt and braces on top of mode=ro
        mode = self.query("PRAGMA journal_mode")[0][0]
        return f"{options} with SQLite {sqlite3.sqlite_version}, journal mode {mode}"

    def passes_integrity_check(self) -> str:
        rows = self.query("PRAGMA integrity_check")
        problems = "; ".join(str(row[0]) for row in rows[:5])
        expect(rows == [("ok",)], f"integrity_check reported: {problems}")
        return "PRAGMA integrity_check says ok"

    def has_a_schema(self) -> str:
        version = self.query("PRAGMA user_version")[0][0]
        found = self.query("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name")
        virtual = [name for name, sql in found if (sql or "").upper().startswith("CREATE VIRTUAL")]
        self.tables = [
            name
            for name, _ in found
            if not name.startswith("sqlite_")
            and not any(name.startswith(f"{table}_") for table in virtual)
        ]
        missing = sorted(set(CAIRN_TABLES) - set(self.tables))
        expect(not missing, f"not a cairn database: no table named {', '.join(missing)}")
        for table, needed in CAIRN_TABLES.items():
            held = {row[1] for row in self.query(f"PRAGMA table_info({quote(table)})")}
            lacking = ", ".join(sorted(needed - held))
            expect(not lacking, f"not a cairn database: table {table} has no column {lacking}")
        expect(
            isinstance(version, int) and version >= FIRST_SCHEMA_VERSION,
            f"schema version (user_version) is {version}; cairn's start at {FIRST_SCHEMA_VERSION}",
        )
        return f"schema version (user_version) {version}, cairn's tables among {len(self.tables)}"

    def counts_rows(self) -> str:
        counts = [
            f"{table}={self.query(f'SELECT count(*) FROM {quote(table)}')[0][0]}"
            for table in self.tables
        ]
        return ", ".join(counts)

    def finds_the_newest_note(self) -> str:
        if "notes" not in self.tables:
            expect(self.args.min_notes == 0, "there is no notes table")
            return "no notes table in this file"
        count, newest = self.query("SELECT count(*), max(created_at) FROM notes")[0]
        expect(
            count >= self.args.min_notes,
            f"{count} notes, fewer than --min-notes {self.args.min_notes}",
        )
        return f"{count} notes, newest at {newest}" if count else "0 notes"

    def cleanup(self) -> str:
        if self.con is not None:
            self.con.close()
        if self.used_docker:
            subprocess.run(["docker", "rm", "-f", self.container], capture_output=True, check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)
        expect(not self.tmp.exists(), f"{self.tmp} could not be removed")
        return "temporary directory removed"


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="restore_drill.py",
        description="Restore the latest Litestream replica of a cairn database and check it.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--replica", metavar="URL", help="replica URL, e.g. gs://BUCKET/cairn")
    source.add_argument("--db-file", metavar="PATH", help="check this SQLite file, no restore")
    tool = parser.add_mutually_exclusive_group()
    tool.add_argument("--litestream", metavar="BINARY", help="litestream binary to restore with")
    tool.add_argument(
        "--docker",
        metavar="IMAGE",
        nargs="?",
        const=LITESTREAM_IMAGE,
        help=f"restore with `docker run IMAGE` (default image: {LITESTREAM_IMAGE})",
    )
    parser.add_argument(
        "--min-notes",
        metavar="N",
        type=int,
        default=0,
        help="fail when the database holds fewer notes than N (default 0)",
    )
    args = parser.parse_args(argv)
    if args.db_file and (args.litestream or args.docker):
        parser.error("--litestream and --docker only apply with --replica")
    if args.min_notes < 0:
        parser.error("--min-notes can't be negative")
    return args


def main(argv: list[str]) -> int:
    args = parse(argv[1:])
    drill = Drill(args)
    checks: list[Callable[[], str]] = [
        drill.restores_the_latest_replica if args.replica else drill.has_the_file,
        drill.opens_read_only,
        drill.passes_integrity_check,
        drill.has_a_schema,
        drill.counts_rows,
        drill.finds_the_newest_note,
    ]
    passed = 0
    try:
        for check in checks:
            started = time.monotonic()
            try:
                detail = check()
            except Failed as failure:
                print(f"FAIL {check.__name__}: {failure}")
                break
            print(f"PASS {check.__name__} ({time.monotonic() - started:.1f}s): {detail}")
            passed += 1
    finally:
        try:
            print(f"PASS cleanup: {drill.cleanup()}")
        except Failed as failure:
            print(f"FAIL cleanup: {failure}")
            passed = -1
    print(f"{max(passed, 0)}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
