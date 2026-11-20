"""How many events a workspace keeps, with two processes on one file (the third report's
table, "Audit retention"). The number is recorded in the file by the server that sets it and
read once by each connection: a process that has read it keeps pruning by the number it read
when another changes it; one that has not read it yet reads the new one. Two stores on one
file stand for two processes."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cairn.store import Store

NOW = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
RUN_SAYS = (
    "A process reads the `--audit-keep` number once: one that is running when another `serve` is started"
    " with a different number keeps pruning by the number it read until it exits, so"
    " change it by stopping `serve` and starting it again, with no other `serve` on the same"
    " database."
)


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


def opened(db) -> Store:
    return Store(db, clock=lambda: NOW)


def count_events(db: Path, account_id: str) -> int:
    with sqlite3.connect(db) as con:
        (n,) = con.execute(
            "SELECT COUNT(*) FROM audit_events WHERE account_id = ?", (account_id,)
        ).fetchone()
    return n


def write(store, project_id: str, first: int, last: int) -> None:
    for n in range(first, last):
        store.record_note(project_id, "unchanged", {"page": f"p{n}"})


def test_a_process_that_read_the_number_prunes_by_it_after_another_changes_it(db):
    first, late = opened(db), opened(db)  # `late` is open and has read nothing yet
    try:
        first.audit_keep = 5
        ops = first.create_project("ops")
        write(first, ops.id, 0, 20)  # `first` has read 5, and prunes by it
        assert count_events(db, first.account_id) <= 5
        second = opened(db)  # another serve, started with `--audit-keep all`
        try:
            second.audit_keep = None
            assert late.audit_keep is None  # read now, for the first time: the new number
            write(first, ops.id, 20, 60)  # the process that read 5 still prunes by 5
            assert count_events(db, first.account_id) <= 5
            write(second, ops.id, 60, 80)  # and the one that set `all` prunes nothing
            assert count_events(db, first.account_id) >= 20
        finally:
            second.close()
        again = opened(db)  # the first server, stopped and started again with nothing set
        try:
            assert again.audit_keep is None
        finally:
            again.close()
    finally:
        first.close()
        late.close()


def test_the_run_guide_says_a_running_process_keeps_the_number_it_read():
    from guide_source import guide_source

    said = " ".join(guide_source("run").split())
    assert said.count(RUN_SAYS) == 1
