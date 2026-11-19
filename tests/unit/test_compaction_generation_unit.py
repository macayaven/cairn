"""A rewrite covers only the deletions recorded before it began (second review, should-fix
11). The command line's `compact` and the server are two processes on one file: when the
server deletes between the end of the command's rewrite and the moment it records it, that
deletion is in no rewrite, and it stays due. The rewrite marks covered only the record's
state it read before rewriting (its generation), in a statement that changes nothing when
the row has moved on. Two stores on one file stand for the two processes; the second one
deletes at that exact point through a patched call, not by timing. Made-up data; the clock
is pinned; nothing sleeps."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from guide_source import guide_source

from cairn.store import Store, base, portable

T0 = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)


@pytest.fixture
def now(monkeypatch):
    moment = [T0]
    monkeypatch.setattr(base, "_clock", lambda: moment[0])
    return moment


@pytest.fixture
def two(tmp_path, now):
    """The command line's store and the server's, on one file."""
    db = tmp_path / "cairn.db"
    command, server = Store(db), Store(db)
    yield command, server
    command.close()
    server.close()


def deleted(store) -> None:
    store.delete_account(store.create_account("Gone").id)


def deleting_before_the_record(monkeypatch, server, now) -> list[int]:
    """The server deletes once VACUUM has returned and before the command records it: the
    record is written in compact's one transaction after the rewrite."""
    calls: list[int] = []
    real = portable._transaction

    @contextmanager
    def late(con):
        if not calls:
            calls.append(1)
            now[0] = T0 + timedelta(minutes=1)  # the server's deletion comes later
            deleted(server)
        with real(con):
            yield

    monkeypatch.setattr(portable, "_transaction", late)
    return calls


def test_a_deletion_between_the_rewrite_and_its_record_stays_due(two, monkeypatch, now):
    command, server = two
    deleted(command)
    calls = deleting_before_the_record(monkeypatch, server, now)
    command.compact()
    assert calls == [1]
    state = server.compaction_state()
    assert state.deleted_at is not None  # the server's deletion is in no rewrite
    assert state.rewritten is False  # so a rewrite is due, not only the log
    assert state.compacted_at is not None  # the rewrite itself is recorded


def test_the_next_rewrite_covers_it(two, monkeypatch, now):
    command, server = two
    deleted(command)
    deleting_before_the_record(monkeypatch, server, now)
    command.compact()
    command.compact()  # the patched call deletes once only
    assert server.compaction_state().deleted_at is None


def test_with_no_deletion_in_between_the_rewrite_covers_the_record(two):
    """The neighbour: the ordinary case still clears what it covered."""
    command, server = two
    deleted(server)
    command.compact()
    state = server.compaction_state()
    assert state.deleted_at is None and state.rewritten is False


def test_a_deletion_recorded_during_the_wait_for_the_lock_is_covered(two, monkeypatch, now):
    """The other side of the generation: a deletion recorded before the rewrite's own read
    of the record is the rewrite's, as the record says, and nothing stays due."""
    command, server = two
    deleted(command)
    now[0] = T0 + timedelta(minutes=1)
    deleted(server)
    command.compact()
    assert server.compaction_state().deleted_at is None


class Killed(BaseException):
    """The process dies: nothing after it runs, nothing catches it."""


def test_a_compact_killed_before_its_record_rewrites_again_and_loses_nothing(two, monkeypatch):
    """The run guide's sentence: a `compact` killed after the rewrite and before its record
    leaves the deletion due; the next run rewrites again, and the record then goes."""
    command, server = two
    kept = server.create_account("Kept").id
    deleted(server)

    @contextmanager
    def killed(con):
        raise Killed
        yield  # pragma: no cover

    real = portable._transaction
    monkeypatch.setattr(portable, "_transaction", killed)
    with pytest.raises(Killed):
        command.compact()
    state = server.compaction_state()
    assert state.deleted_at is not None and state.rewritten is False  # still due: a rewrite
    monkeypatch.setattr(portable, "_transaction", real)
    command.compact()
    assert server.compaction_state().deleted_at is None
    assert kept in {a.id for a in server.list_accounts()}  # nothing lost


RUN_SAYS = (
    "A rewrite covers the deletions recorded before it began: one the server records once"
    " `compact` has begun to rewrite the file stays due, for a rewrite of its own. A"
    " `compact` killed after it rewrote the file and before it recorded the rewrite leaves"
    " the deletion due, so the next run rewrites the file again: that costs the time of one"
    " rewrite and loses nothing."
)


def test_the_run_guide_says_what_a_rewrite_covers_and_what_a_crash_costs():
    raw = guide_source("run")
    flat = " ".join(raw.split())
    assert flat.count(RUN_SAYS) == 1
    paragraph = next(p for p in raw.split("\n\n") if RUN_SAYS in " ".join(p.split()))
    assert paragraph.startswith("A compaction needs twice the database's size free")
