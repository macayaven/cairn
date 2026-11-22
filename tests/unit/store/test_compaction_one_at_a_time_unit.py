"""One compaction at a time for a database, across processes (the cross-read of OPS4,
findings 1 to 3). Two compactions that overlapped could leave a rewrite in the write-ahead
log while the record said nothing was due; a run that met a deletion recorded while it ran
said it had done all that was due; and an error of the last truncation of the log was
swallowed. Two stores on one file stand for two processes; each step is placed by a patched
call or by threads that meet at events, never by timing. The clock is pinned."""

import contextlib
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

import cairn.store as store_module
from cairn.store import CompactIncomplete, Store, base
from support.paths import REPO_ROOT

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
# Absent before the lock existed: then nothing is refused and the overlap runs as it did.
RUNNING = getattr(store_module, "CompactionRunning", ())


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "_clock", lambda: T0)
    return tmp_path / "cairn.db"


@pytest.fixture
def two(db):
    """The server's store and the command line's, on one file."""
    a, b = Store(db), Store(db)
    for store in (a, b):  # a held log is refused at once, not after 5 s of retries
        store._con.execute("PRAGMA busy_timeout = 0")
    yield a, b
    a.close()
    b.close()


def deleted(store) -> None:
    store.delete_account(store.create_account("Gone").id)


def wal_size(db) -> int:
    path = f"{db}-wal"
    return os.path.getsize(path) if os.path.exists(path) else 0


def pinned(db) -> sqlite3.Connection:
    """As Litestream: a connection that keeps reading the write-ahead log."""
    reader = sqlite3.connect(db)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM settings").fetchone()
    return reader


class _Pausing:
    """The writer as compact uses it, with `step` run once just before its VACUUM."""

    def __init__(self, con, step):
        self._con, self._step = con, step

    def execute(self, sql, *args):
        if sql == "VACUUM" and self._step is not None:
            step, self._step = self._step, None
            step()
        return self._con.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._con, name)


def pause_before_vacuum(store, monkeypatch, step) -> None:
    real = store._holding

    @contextmanager
    def holding():
        with real() as con:
            yield _Pausing(con, step)

    monkeypatch.setattr(store, "_holding", holding)


def before_the_first_write(store, monkeypatch, step) -> None:
    """`step` once, when `store` next opens its writer: in compact, between its successful
    truncation of the log and its clearing of the record."""
    real, ran = store._write, []

    @contextmanager
    def first(*args, **kwargs):
        if not ran:
            ran.append(1)
            step()
        with real(*args, **kwargs) as con:
            yield con

    monkeypatch.setattr(store, "_write", first)


def test_two_overlapping_compactions_leave_no_rewrite_in_the_log_the_record_forgets(
    two, db, monkeypatch
):
    """The cross-read's finding 1: the command reads the deletion and pauses before its
    VACUUM; the server rewrites, records, empties the log; a reader pins the log; the command
    rewrites again (its record matches nothing) and cannot empty the log; the server clears
    its own record. Then the log holds a rewrite while the record says nothing is due.
    One compaction at a time: the server's is refused while the command's runs."""
    server, command = two
    deleted(server)
    read, go = threading.Event(), threading.Event()
    readers: list[sqlite3.Connection] = []
    outcome: list[BaseException | None] = []

    def wait_for_the_server():
        read.set()
        assert go.wait(30)

    pause_before_vacuum(command, monkeypatch, wait_for_the_server)

    def the_command():
        try:
            command.compact()
            outcome.append(None)
        except BaseException as error:  # the command's own outcome, kept for the test
            outcome.append(error)
        finally:
            read.set()

    thread = threading.Thread(target=the_command)
    thread.start()
    try:
        assert read.wait(30)

        def the_command_rewrites_again():
            readers.append(pinned(db))
            go.set()
            thread.join(30)

        before_the_first_write(server, monkeypatch, the_command_rewrites_again)
        with contextlib.suppress(*([RUNNING] if RUNNING else [])):
            server.compact()
        go.set()
        thread.join(30)
        assert not thread.is_alive()
        state = server.compaction_state()
        assert wal_size(db) == 0 or state.deleted_at is not None, (
            "a rewrite is in the write-ahead log and the record says nothing is due"
        )
    finally:
        go.set()
        thread.join(30)
        for reader in readers:
            reader.rollback()
            reader.close()


def held_by_another(db):
    """Another process's compaction, running: a store of its own holding the lock."""
    other = Store(db)
    stack = contextlib.ExitStack()
    stack.callback(other.close)
    stack.enter_context(other.compacting())
    return stack


def test_a_compaction_while_another_runs_is_refused_and_changes_nothing(two, db):
    server, command = two
    deleted(server)
    before = server.compaction_state()
    with held_by_another(db):
        for call in (command.compact, command.empty_log, server.compact):
            with pytest.raises(store_module.CompactionRunning):
                call()
    assert server.compaction_state() == before
    command.compact()  # let go: it runs
    assert server.compaction_state().deleted_at is None


def test_the_lock_is_a_file_beside_the_database_and_a_killed_holder_leaves_it_free(db):
    """flock(2): the kernel lets go of it with the descriptor, so a process killed while it
    compacts leaves nothing held."""
    import signal
    import subprocess
    import sys

    Store(db).close()
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from cairn.store import Store\n"
            "s = Store(sys.argv[1])\n"
            "with s.compacting():\n"
            "    print('held', flush=True); sys.stdin.read()\n",
            str(db),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "held"
        assert os.path.exists(f"{os.path.realpath(db)}.compact.lock")
        store = Store(db)
        try:
            with pytest.raises(store_module.CompactionRunning):
                store.empty_log()
            child.send_signal(signal.SIGKILL)
            child.wait(30)
            store.empty_log()  # free again
        finally:
            store.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(30)
        child.stdout.close()
        child.stdin.close()


def access_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "cairn.access"]


def test_the_servers_compactor_skips_a_round_while_another_compaction_runs(db, caplog):
    from cairn.web.compactor import Compactor

    store = Store(db)
    try:
        deleted(store)
        compactor = Compactor(store, hours=24, clock=lambda: T0)
        with held_by_another(db), caplog.at_level(logging.INFO, logger="cairn.access"):
            assert compactor.run_once() is False
        (line,) = access_lines(caplog)
        assert line.startswith(
            "compact skipped: another compaction is running on this database in "
        )
        assert store.compaction_state().deleted_at is not None  # nothing changed
        assert compactor.due() is True  # the next poll tries again: no interval to wait
    finally:
        store.close()


@pytest.mark.parametrize("if_due", [True, False])
def test_the_command_says_another_compaction_runs_and_exits_3(db, capsys, if_due):
    from cairn.web import cli

    store = Store(db)
    try:
        deleted(store)
    finally:
        store.close()
    with held_by_another(db):
        code = cli.main(["compact", *(["--if-due"] if if_due else []), "--db", str(db)])
    out = capsys.readouterr()
    assert code == 3
    assert out.err.strip().endswith(
        "was not compacted: another compaction is running on this database; nothing was"
        " changed, run compact again when it is done"
    )
    assert out.out == ""


def a_deletion_during(store, monkeypatch, name, other) -> None:
    """A workspace deleted by `other` (the server) once `store`'s `name` has begun."""
    real = getattr(store, name)

    def with_a_deletion():
        deleted(other)
        return real()

    monkeypatch.setattr(store, name, with_a_deletion)


def rewritten_log_held(store, db) -> None:
    deleted(store)
    reader = pinned(db)
    try:
        with pytest.raises(CompactIncomplete):
            store.compact()
    finally:
        reader.rollback()
        reader.close()
    assert store.compaction_state().rewritten is True


STILL_DUE = "a deletion recorded during the run still asks for a rewrite"


def test_a_log_only_run_of_the_compactor_that_meets_a_deletion_says_a_rewrite_is_due(
    two, db, monkeypatch, caplog
):
    from cairn.web.compactor import Compactor

    server, other = two
    rewritten_log_held(server, db)
    a_deletion_during(server, monkeypatch, "empty_log", other)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert Compactor(server, hours=24, clock=lambda: T0).run_once() is True
    (line,) = access_lines(caplog)
    assert line.startswith(
        f"compact done (no rewrite: only the write-ahead log was due; {STILL_DUE}) in "
    )
    state = server.compaction_state()
    assert state.deleted_at is not None and state.rewritten is False


def test_a_rewrite_of_the_compactor_that_meets_a_deletion_says_a_rewrite_is_due(
    two, db, monkeypatch, caplog
):
    from cairn.web.compactor import Compactor

    server, other = two
    deleted(server)
    from cairn.store import portable

    real, ran = portable._transaction, []

    @contextmanager
    def after_the_vacuum(con):  # a deletion the rewrite did not cover
        if not ran:
            ran.append(1)
            deleted(other)
        with real(con):
            yield

    monkeypatch.setattr(portable, "_transaction", after_the_vacuum)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert Compactor(server, hours=24, clock=lambda: T0).run_once() is True
    (line,) = access_lines(caplog)
    assert line.startswith(f"compact done ({STILL_DUE}) in ")


def test_a_log_only_command_that_meets_a_deletion_says_a_rewrite_is_due(db, monkeypatch, capsys):
    """It did what was due at its start (exit 0) and says what is due now."""
    from cairn.web import cli

    store = Store(db)
    try:
        rewritten_log_held(store, db)
    finally:
        store.close()
    other = Store(db)
    real = Store.empty_log

    def with_a_deletion(self):
        deleted(other)
        return real(self)

    monkeypatch.setattr(Store, "empty_log", with_a_deletion)
    try:
        capsys.readouterr()
        assert cli.main(["compact", "--if-due", "--db", str(db)]) == 0
    finally:
        other.close()
    out = capsys.readouterr().out
    assert "emptied the write-ahead log of" in out
    assert f"{STILL_DUE}: run compact --if-due again" in out
    state_store = Store(db)
    try:
        state = state_store.compaction_state()
    finally:
        state_store.close()
    assert state.deleted_at is not None and state.rewritten is False


class _FailingCheckpoint:
    """The writer, whose second truncation of the log fails as an I/O fault would."""

    def __init__(self, con, calls):
        self._con, self._calls = con, calls

    def execute(self, sql, *args):
        if sql == "PRAGMA wal_checkpoint(TRUNCATE)":
            self._calls.append(sql)
            if len(self._calls) == 2:
                error = sqlite3.OperationalError("disk I/O error at /made/up/place")
                error.sqlite_errorname = "SQLITE_IOERR"
                raise error
        return self._con.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._con, name)


def test_an_error_of_the_last_truncation_is_logged_by_its_code_and_the_work_stays_done(
    db, monkeypatch, caplog
):
    store = Store(db)
    try:
        deleted(store)
        calls: list[str] = []
        real = store._locked

        @contextmanager
        def locked(*args, **kwargs):
            with real(*args, **kwargs) as con:
                yield _FailingCheckpoint(con, calls)

        monkeypatch.setattr(store, "_locked", locked)
        with caplog.at_level(logging.INFO, logger="cairn.store"):
            store.compact()  # nothing raised: the rewrite and the first truncation are done
        assert len(calls) == 2
        said = [r for r in caplog.records if r.name == "cairn.store"]
        assert [(r.levelno, r.getMessage()) for r in said] == [
            (
                logging.WARNING,
                "compact: the write-ahead log keeps the changes made after it was emptied:"
                " its last truncation failed (SQLITE_IOERR)",
            )
        ]
        assert store.compaction_state().deleted_at is None  # the record stays cleared
    finally:
        store.close()


def _one_line(text: str) -> str:
    return " ".join(text.split())


RUN_LOCK_SAYS = [
    "`compact` is run from the same image as the server, inside its running container, as"
    " every `compact` in these instructions is.",
    "One compaction runs at a time on a database: `compact` and the server's own compactor"
    " each hold `cairn.db.compact.lock`, an empty file beside the database, from reading what"
    " is due until they are done.",
    "A `compact` that finds another compaction running changes nothing, says so and exits 3.",
    "A run that ends with a deletion recorded while it ran, which it did not rewrite for, says"
    " so in one more line and still exits 0: run `compact --if-due` again.",
    "`compact skipped: another compaction is running on this database in N ms`: the command"
    " line's `compact` was running; nothing changed, and it tries again at the next poll.",
]


def test_the_run_guide_says_one_compaction_at_a_time_and_runs_it(tmp_path, capsys):
    """The sentences, once each, then what they say: the lock's file beside the database,
    and a `compact` refused while the server's compactor holds it (exit 3, nothing
    changed)."""
    from cairn.web import cli
    from support.guide_source import guide_source

    said = _one_line(guide_source("run"))
    for sentence in RUN_LOCK_SAYS:
        assert said.count(sentence) == 1, sentence
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        deleted(store)
        before = store.compaction_state()
        with store.compacting():
            assert (tmp_path / "cairn.db.compact.lock").is_file()
            assert cli.main(["compact", "--db", str(db)]) == 3
        assert store.compaction_state() == before  # nothing changed
    finally:
        store.close()
    assert "another compaction is running" in capsys.readouterr().err


def _compact_commands(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if "cairn.server compact" in line or "cairn compact" in line
    ]


def test_every_compact_in_the_instructions_runs_in_the_servers_container():
    """What the run guide's first sentence above says of the instructions: `compact` runs by
    `exec` in the running server's container, so from its image, in the run guide, the
    Compose guide and the host's script; the Google Cloud guide runs it only through that
    script."""

    from support.guide_source import guide_source

    root = REPO_ROOT
    places = {
        "run.md": (guide_source("run"), "docker exec cairn python -m cairn.server compact "),
        "deploy/README.md": (
            (root / "deploy" / "README.md").read_text(encoding="utf-8"),
            "docker compose exec cairn python -m cairn.server compact ",
        ),
        "infra/gcp/files/host.sh": (
            (root / "infra" / "gcp" / "files" / "host.sh").read_text(encoding="utf-8"),
            "docker exec cairn python -m cairn.server compact ",
        ),
    }
    for where, (text, how) in places.items():
        commands = _compact_commands(text)
        assert commands, where
        assert all(c.startswith(how) for c in commands), (where, commands)
    gcp = (root / "infra" / "gcp" / "README.md").read_text(encoding="utf-8")
    assert _compact_commands(gcp) == []
    assert "sudo bash /etc/cairn/host.sh compact" in gcp


def test_a_rewrite_that_did_not_happen_does_not_say_a_deletion_came(db, monkeypatch, caplog):
    """The record still asks for a rewrite because this run did not do it, not because a
    deletion came while it ran: its line says only that it did not finish."""
    from cairn.store.portable import _incomplete
    from cairn.web.compactor import Compactor

    store = Store(db)
    try:
        deleted(store)

        def busy():
            raise _incomplete("the database could not be compacted: busy", False)

        monkeypatch.setattr(store, "compact", busy)
        with caplog.at_level(logging.INFO, logger="cairn.access"):
            assert Compactor(store, hours=24, clock=lambda: T0).run_once() is False
        (line,) = access_lines(caplog)
        assert line.startswith("compact not finished: another connection held the file in ")
        assert STILL_DUE not in line
    finally:
        store.close()


RUN_NOTHING_DUE_SAYS = (
    "`compact skipped: nothing was due once another compaction ended in N ms`: another"
    " compaction finished between this one's decision and its start; nothing was left to do,"
    " and the file is not rewritten again."
)
RUN_FAILED_SAYS = (
    "`compact failed: <error type> in N ms`: the run stopped on an error, named by its type"
    " and never its text; it does not wait for the interval, and tries again at the next poll."
)


def test_the_compactor_rewrites_nothing_when_another_compaction_did_it_since_its_decision(
    two, db, caplog
):
    """The command line's `compact` finishes between the server's decision (due) and its
    run: read again once the lock is held, nothing is due, and the file is not rewritten.
    The run guide's sentence for that line, once, is what this shows."""
    from cairn.web.compactor import Compactor
    from support.guide_source import guide_source

    assert _one_line(guide_source("run")).count(RUN_NOTHING_DUE_SAYS) == 1
    server, command = two
    deleted(server)
    compactor = Compactor(server, hours=24, clock=lambda: T0)
    assert compactor.due() is True
    command.compact()  # done, and recorded: nothing is due
    vacuums: list[str] = []
    server._observe(trace=lambda s: vacuums.append(s) if s.strip().upper() == "VACUUM" else None)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert compactor.run_once() is True
    assert vacuums == []
    (line,) = access_lines(caplog)
    assert line.startswith("compact skipped: nothing was due once another compaction ended in ")


RUN_STILL_DUE_SAYS = (
    "A line whose parentheses end with `a deletion recorded during the run still asks for a"
    " rewrite`: what it names was done, and a workspace deleted while it ran still needs a"
    " rewrite of its own, after the interval."
)


def after_the_vacuum_a_deletion(monkeypatch, other) -> list[int]:
    from cairn.store import portable

    real, ran = portable._transaction, []

    @contextmanager
    def late(con):
        if not ran:
            ran.append(1)
            deleted(other)
        with real(con):
            yield

    monkeypatch.setattr(portable, "_transaction", late)
    return ran


def test_the_run_guide_says_a_deletion_during_a_run_is_rewritten_after_the_interval(
    two, db, monkeypatch, caplog
):
    from cairn.web.compactor import Compactor
    from support.guide_source import guide_source

    assert _one_line(guide_source("run")).count(RUN_STILL_DUE_SAYS) == 1
    server, other = two
    deleted(server)
    ran = after_the_vacuum_a_deletion(monkeypatch, other)
    ticks = [0.0]
    compactor = Compactor(server, hours=24, clock=lambda: T0, ticks=lambda: ticks[0])
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert compactor.run_once() is True
    assert ran == [1]
    (line,) = access_lines(caplog)
    assert f"({STILL_DUE})" in line  # what it names was done: the rewrite and the log
    assert compactor.due() is False  # still due, after the interval
    ticks[0] += 24 * 3600.0
    assert compactor.due() is True


def test_a_full_command_that_meets_a_deletion_says_a_rewrite_is_due(db, monkeypatch, capsys):
    from cairn.web import cli

    store = Store(db)
    try:
        deleted(store)
    finally:
        store.close()
    other = Store(db)
    try:
        ran = after_the_vacuum_a_deletion(monkeypatch, other)
        capsys.readouterr()
        assert cli.main(["compact", "--if-due", "--db", str(db)]) == 0
        assert ran == [1]
    finally:
        other.close()
    out = capsys.readouterr().out
    assert f"compacted {db}" in out
    assert f"{STILL_DUE}: run compact --if-due again" in out


def test_the_run_guide_says_a_failed_run_is_named_by_type_and_tried_again_at_the_next_poll(
    two, db, monkeypatch, caplog
):
    """The sentence, once, then what it says: a run that stops on an error writes the
    error's type and not its text, the next decision is due at once (no interval is waited),
    and the next run does the work."""
    from cairn.web.compactor import Compactor
    from support.guide_source import guide_source

    assert _one_line(guide_source("run")).count(RUN_FAILED_SAYS) == 1
    server, _ = two
    deleted(server)
    compactor = Compactor(server, hours=24, clock=lambda: T0, ticks=lambda: 0.0)
    assert compactor.due() is True
    real = server.compact

    def broken() -> None:
        raise RuntimeError("the text of a row that must not be logged")

    monkeypatch.setattr(server, "compact", broken)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert compactor.run_once() is False
    (line,) = access_lines(caplog)
    assert line.startswith("compact failed: RuntimeError in ")
    assert "must not be logged" not in line
    assert compactor.due() is True  # on the same tick: the next poll, not the interval
    monkeypatch.setattr(server, "compact", real)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert compactor.run_once() is True
    (line,) = access_lines(caplog)
    assert line.startswith("compact done in ")
    assert compactor.due() is False
