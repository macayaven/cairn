"""The command line's data commands: export, import and compact (cairn.web.cli dispatches
here). They print paths, counts, the workspace's id, the names of the note fields an import
cut, the notice that imported projects are private, and refusals that name a file, an id, a
project's name, a rule or a limit, and failures that name the file and the system's reason or
the error's class; never note text, keys or tokens.

A private database (H1; make_folder, make_database, linked, readable): every folder a command
makes for its database (serve, key-create, import) is 0700, and the database file is made
0600, empty, before SQLite opens it (O_CREAT|O_EXCL|O_NOFOLLOW), so the -wal and -shm files
SQLite makes beside it are 0600 too, whatever the umask. cairn.web.cli.main checks every
command's --db before the command runs: a symbolic link is refused, exit 2, in one line
(LINKED_DB), with nothing made at either end of it; an existing database others on this
machine can read (its group or everyone else may read it, and may pass every folder above
it) is not changed and not refused, and the command says so first, once, in one line on
stderr (READABLE_DB: its mode and the chmod that makes it private). A file in a folder only
its owner may enter is quiet whatever its own mode: the container's /data is 0700."""

import argparse
import os
import stat
import sys
from pathlib import Path

from cairn.plans import Plans
from cairn.plans import load as load_plans
from cairn.store import FIELD_MAX, CannotOpen, CompactIncomplete, OverLimit, Pruned, Store

COMMANDS = ("export", "import", "compact")
# The --db help of a command that never creates the database (it exits 1 when there is none).
DB_EXISTS = "the SQLite file serve uses, which must exist"
NO_PLAN = "no plan is applied: this import caps nothing (--plans FILE holds it to one)"


def add_commands(commands: argparse._SubParsersAction) -> None:
    export = commands.add_parser(
        "export", help="write a workspace's data to a new 0600 zip", allow_abbrev=False
    )
    export.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
    export.add_argument("--account", required=True, help="the workspace's id (acct_...)")
    export.add_argument("--out", type=Path, required=True, help="a file that must not exist")
    restore = commands.add_parser(
        "import", help="restore an export into a workspace", allow_abbrev=False
    )
    restore.add_argument("--db", type=Path, required=True, help="SQLite file, created if missing")
    restore.add_argument("--account", required=True, help="the workspace's id (acct_...)")
    restore.add_argument("--in", type=Path, required=True, dest="source", help="the export")
    restore.add_argument(
        "--max-bytes",
        type=_bytes,
        default=None,
        metavar="N",
        help="read at most N bytes of the archive, all held in memory (256 MiB)",
    )
    restore.add_argument(
        "--plans",
        type=Path,
        default=None,
        metavar="FILE",
        help="hold the workspace to its plan in this plans file, as serve does",
    )
    compact = commands.add_parser(
        "compact",
        help="rewrite the database file so deleted data leaves the disk",
        allow_abbrev=False,
    )
    compact.add_argument("--db", type=Path, required=True, help=DB_EXISTS)


def run(args: argparse.Namespace) -> int:
    if args.command == "export":
        return _export(args.db, args.account, args.out)
    if args.command == "import":
        plans = None
        if args.plans is not None:
            try:
                plans = load_plans(args.plans)
            except OSError as error:
                print(f"cannot use those plans: cannot read {args.plans}: {error.strerror}",
                      file=sys.stderr)  # fmt: skip
                return 2
            except ValueError as error:
                print(f"cannot use those plans: {error}", file=sys.stderr)
                return 2
        return _import(args.db, args.account, args.source, args.max_bytes, plans)
    return _compact(args.db)


def _counts(summary) -> str:
    return f"{summary.projects} projects, {summary.notes} notes, {summary.pages} pages"


def _export(db: Path, account: str, out: Path) -> int:
    """The workspace is found first, then the file is made (O_EXCL: never overwrite, never
    follow a link; mode 0600, as the export holds every note); a failure removes it."""
    if not db.is_file():
        print(f"no database at {db}", file=sys.stderr)
        return 1
    store = Store(db)
    try:
        try:
            view = store.for_account(account)
        except KeyError:
            print(f"no workspace {account!r} in {db}", file=sys.stderr)
            return 1
        try:
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            print(f"{out} already exists: export never overwrites a file", file=sys.stderr)
            return 1
        except OSError as error:
            print(f"cannot create {out}: {error.strerror}", file=sys.stderr)
            return 1
        try:
            with os.fdopen(fd, "wb") as file:
                os.fchmod(file.fileno(), 0o600)  # exactly 0600, whatever the umask
                summary = view.export(file)
        except Exception as error:
            out.unlink(missing_ok=True)
            # the system's reason or the class: a message may name another path or a note
            reason = error.strerror if isinstance(error, OSError) else None
            print(f"cannot export to {out}: {reason or type(error).__name__}", file=sys.stderr)
            return 1
        except BaseException:  # stopped (Ctrl-C): the half-made file goes too
            out.unlink(missing_ok=True)
            raise
    finally:
        store.close()
    print(f"{out}: {_counts(summary)}")
    return 0


def _bytes(text: str) -> int:
    try:
        count = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("a whole number of bytes") from None
    if count < 1:
        raise argparse.ArgumentTypeError("1 or more bytes")
    return count


def _import(
    db: Path, account: str, source: Path, max_bytes: int | None = None, plans: Plans | None = None
) -> int:
    """With `plans`, the workspace's view is limited by its plan in force, read again inside
    the import's transaction (Plans.limits_of), as a request's is: over a cap, nothing is
    imported and the error names the limit. Without, NO_PLAN goes to stderr first, once the
    database is open (a newer one is refused in one line, cairn.web.cli.main) or when the
    archive cannot be read. A database that was not there is created, and removed again,
    with the directories made for it, when the import does not finish."""
    try:
        file = source.open("rb")
    except OSError as error:
        if plans is None:
            print(NO_PLAN, file=sys.stderr)
        print(f"cannot read {source}: {error.strerror}", file=sys.stderr)
        return 1
    with file:
        new = not db.exists()
        made = _missing_directories(db.parent) if new else []
        make_folder(db)
        imported = False
        try:
            make_database(db)  # 0600 before SQLite opens it (H1)
            store = Store(db)
            try:
                if plans is None:
                    print(NO_PLAN, file=sys.stderr)
                view = store.for_account(account)
                if plans is not None:
                    plan = plans.in_force(view, account)[1]
                    view = plans.limit(view, plan)
                summary = view.import_export(file, max_bytes)
                imported = True
            except KeyError:
                print(f"no workspace {account!r} in {db}", file=sys.stderr)
                return 1
            except (ValueError, OverLimit) as error:  # names files, ids, counts, rules, limits
                print(f"nothing was imported: {error}", file=sys.stderr)
                return 1
            finally:
                store.close()
        finally:
            if new and not imported:
                _remove_new(db, made)
    print(f"imported {_counts(summary)} into {account} in {db}")
    print(f"cut {summary.fields_cut} note fields to their bounds ({', '.join(FIELD_MAX)})")
    print(summary.notice)  # who reaches what it brought: the archive carries no access rules
    return 0


def make_folder(db: Path) -> None:
    """The database's folder, made with its parents when missing, each one 0700 (H1): what
    the database holds is its owner's only. A folder that was there is left as it is. A
    folder cairn may not make it under is CannotOpen, as the store's own refusal is
    (cairn.web.cli.main)."""
    for folder in reversed(_missing_directories(db.parent)):
        try:
            os.mkdir(folder, 0o700)  # the umask can only take bits away from 0700
        except FileExistsError:
            pass
        except PermissionError as error:
            raise CannotOpen(db) from error


# H1: what the command line says of a --db that is a symbolic link (exit 2), and of an
# existing database others on this machine can read (one line on stderr; the command goes
# on). Each names the path as given.
LINKED_DB = (
    "{db} is a symbolic link: --db names the database file itself, so a link cannot send"
    " the server to a file you did not mean"
)
READABLE_DB = (
    "others on this machine can read {db} (mode {mode:04o}): run chmod 600 {db} to make it"
    " yours only"
)
# The classes of user beside the owner, each as (its read bit, its search bit on a folder).
_OTHERS = ((stat.S_IRGRP, stat.S_IXGRP), (stat.S_IROTH, stat.S_IXOTH))


def make_database(db: Path) -> None:
    """The database file, made empty and exactly 0600 when there is none, before SQLite
    opens it, so the -wal and -shm files SQLite makes beside it take the same mode (SQLite
    gives them the database's). O_EXCL|O_NOFOLLOW: an existing file is left as it is, and a
    link at the path is never followed (OSError). A folder cairn may not write is
    CannotOpen, as the store's own refusal is."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(db, flags, 0o600)
    except FileExistsError:
        if os.path.islink(db):
            raise
        return
    except PermissionError as error:
        raise CannotOpen(db) from error
    try:
        os.fchmod(fd, 0o600)  # exactly 0600, whatever the umask took away
    finally:
        os.close(fd)


def linked(db: Path | None) -> str | None:
    """LINKED_DB when `db` is a symbolic link, else None."""
    return LINKED_DB.format(db=db) if db is not None and os.path.islink(db) else None


def readable_by_others(db: Path) -> int | None:
    """The mode of the file at `db` when someone other than its owner can read it: the file
    gives a class of users (its group, or everyone else) read, and every folder above it
    lets that class through. None for a file only its owner reads, a missing path or a
    folder."""
    try:
        info = os.stat(db)
        if not stat.S_ISREG(info.st_mode):
            return None
        folders = [os.stat(folder).st_mode for folder in Path(os.path.realpath(db)).parents]
    except OSError:
        return None
    for read, search in _OTHERS:
        if info.st_mode & read and all(folder & search for folder in folders):
            return stat.S_IMODE(info.st_mode)
    return None


def readable(db: Path | None) -> str | None:
    """READABLE_DB when others on this machine can read the database at `db`, else None."""
    found = None if db is None else readable_by_others(db)
    return None if found is None else READABLE_DB.format(db=db, mode=found)


def _missing_directories(directory: Path) -> list[Path]:
    """The directories mkdir(parents=True) would make for `directory`, deepest first."""
    missing = []
    while not directory.exists() and directory != directory.parent:
        missing.append(directory)
        directory = directory.parent
    return missing


def _remove_new(db: Path, directories: list[Path]) -> None:
    """Remove a database this run created, its SQLite side files, then the directories it
    made, deepest first; a directory something else has put a file in stays."""
    for path in (db, *(db.with_name(db.name + end) for end in ("-wal", "-shm", "-journal"))):
        path.unlink(missing_ok=True)
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            return


def _compact(db: Path) -> int:
    if not db.is_file():
        print(f"no database at {db}", file=sys.stderr)
        return 1
    before = db.stat().st_size
    store = Store(db)
    try:
        pruned = store.prune_by_record()  # Q1: before the file is rewritten
        store.compact()
    except CompactIncomplete as error:
        if pruned.removed:  # removed all the same: said, beside the failure
            print(_pruned(pruned), file=sys.stderr)
        print(f"{db} was not fully compacted: {error}", file=sys.stderr)
        return 1
    finally:
        store.close()
    print(_pruned(pruned))
    print(f"compacted {db}: {before} -> {db.stat().st_size} bytes")
    return 0


def _pruned(pruned: Pruned) -> str:
    """What `compact` says of the page versions it removed by the last serve's record (Q1;
    Store.prune_by_record): how many and by what rule, or why none without a record; then,
    on a line of their own, the workspaces whose plan that record does not name, which keep
    every version."""
    if not pruned.recorded:
        return (
            "removed no page version: no serve of this version has recorded how many to keep;"
            " run serve once, then compact"
        )
    if pruned.by_plans:
        rule = "each workspace keeps what its plan keeps"
    elif pruned.keep is None:
        rule = "each page keeps every version"
    else:
        rule = f"each page keeps its newest {pruned.keep}"
    said = f"removed {pruned.removed} older page versions: {rule}"
    if pruned.left_alone:
        said += (
            f"\nkept every version in {', '.join(pruned.left_alone)}: the plans the last serve"
            " ran with do not name their plan"
        )
    return said
