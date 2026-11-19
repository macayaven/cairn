"""Compaction checks for room first (review 6.2). Before it rewrites the file, compact reads
the free space on the database's disk; under twice the database's size (its file and its
write-ahead log: a second copy, and as much again as a margin) it does not start: it raises
NoRoom (a CompactIncomplete) with the two numbers, changes nothing and the deletion stays
due. The compactor logs one line with both numbers. The free-space reading is injected."""

import logging
import os
from collections import namedtuple
from datetime import UTC, datetime

import pytest

from cairn.store import CompactIncomplete, NoRoom, Store, portable
from cairn.web.compactor import Compactor

Usage = namedtuple("Usage", "total used free")
T0 = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    s.delete_account(s.create_account("Gone").id)
    yield s
    s.close()


def size_of(db) -> int:
    wal = f"{db}-wal"
    return os.path.getsize(db) + (os.path.getsize(wal) if os.path.exists(wal) else 0)


def free(monkeypatch, amount_for) -> list[str]:
    """The disk's free space, as `amount_for(size)` says; the folders asked about."""
    asked = []

    def usage(folder):
        asked.append(str(folder))
        return Usage(1, 1, amount_for())

    monkeypatch.setattr(portable.shutil, "disk_usage", usage)
    return asked


def statements(store) -> list[str]:
    seen: list[str] = []
    store._observe(trace=seen.append)
    return seen


def test_without_room_for_two_copies_compact_does_not_start_and_stays_due(store, db, monkeypatch):
    before = store.compaction_state()
    asked = free(monkeypatch, lambda: 2 * size_of(db) - 1)
    seen = statements(store)
    with pytest.raises(NoRoom) as raised:
        store.compact()
    assert isinstance(raised.value, CompactIncomplete)
    needed = raised.value.needed
    assert raised.value.free == needed - 1
    assert needed == 2 * size_of(db)
    assert str(needed) in str(raised.value) and str(needed - 1) in str(raised.value)
    assert not any(s.strip().upper().startswith(("VACUUM", "INSERT", "DELETE")) for s in seen)
    assert store.compaction_state() == before  # still due, nothing recorded
    assert asked == [str(db.parent)]


def test_with_room_it_compacts(store, db, monkeypatch):
    free(monkeypatch, lambda: 2 * size_of(db))
    store.compact()
    assert store.compaction_state().deleted_at is None


def test_the_compactor_logs_one_line_with_both_numbers_and_tries_again(
    store, db, monkeypatch, caplog
):
    amounts = [0]
    free(monkeypatch, lambda: amounts[0])
    compactor = Compactor(store, hours=24, clock=lambda: T0)
    assert compactor.due()
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert compactor.run_once() is False
    (line,) = [r for r in caplog.records if r.getMessage().startswith("compact")]
    needed = 2 * size_of(db)
    assert line.getMessage() == (
        f"compact not started: the database's disk has 0 bytes free, a rewrite needs {needed}"
    )
    assert line.status == 507 and line.action == "compact"
    assert not line.getMessage().startswith("compact not finished: ")  # not the alert's line
    assert compactor.due()  # stays due: the next poll checks again
    amounts[0] = needed
    assert compactor.run_once() is True
