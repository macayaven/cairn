"""A deletion costs one rewrite, not two (left.md, part 5): `compact --if-due` does only what the
record of compaction (Store.compaction_state) says is still due. No deletion recorded: nothing,
said in one line. The file rewritten since the last deletion, only its write-ahead log left:
the log alone (Store.empty_log). Otherwise the whole compaction, as `compact` without the flag,
which stays as it is. Every clock is in 2099."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from cairn.store import Store, portable
from cairn.store.base import COMPACT_DUE, COMPACTED_AT, DEFAULT_ACCOUNT_ID, REWRITTEN
from cairn.web import cli
from support.guide_source import guide_source

DELETED = "2099-03-04T05:06:07.000000+00:00"
REWROTE = "2099-03-04T06:00:00.000000+00:00"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2099, 3, 4, 7, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "cairn.db"
    Store(path, clock=Clock()).close()
    return path


def record(db, *, due: str | None, rewritten: bool = False) -> None:
    """The install's record of compaction, as the store writes it (cairn.store.base)."""
    con = sqlite3.connect(db)
    with con:
        con.execute(
            "DELETE FROM settings WHERE account_id = ? AND name IN (?, ?)",
            (DEFAULT_ACCOUNT_ID, COMPACT_DUE, COMPACTED_AT),
        )
        insert = "INSERT INTO settings (account_id, name, value, updated_at) VALUES (?, ?, ?, ?)"
        if due is not None:
            con.execute(
                insert, (DEFAULT_ACCOUNT_ID, COMPACT_DUE, due, REWRITTEN if rewritten else due)
            )
            con.execute(insert, (DEFAULT_ACCOUNT_ID, COMPACTED_AT, REWROTE, REWROTE))
    con.close()


def state(db):
    store = Store(db, clock=Clock())
    try:
        return store.compaction_state()
    finally:
        store.close()


@pytest.fixture
def calls(monkeypatch):
    """What the command asked of the store: `compact`, `empty_log` and `prune_by_record`, each
    still done."""
    asked: list[str] = []
    for name in ("compact", "empty_log", "prune_by_record"):
        real = getattr(Store, name)

        def watched(self, *args, _name=name, _real=real, **kwargs):
            asked.append(_name)
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(Store, name, watched)
    return asked


def test_with_no_deletion_recorded_it_does_nothing_and_says_so(db, calls, capsys):
    record(db, due=None)
    before = db.read_bytes()
    assert cli.main(["compact", "--if-due", "--db", str(db)]) == 0
    printed = capsys.readouterr()
    assert printed.out == (
        f"nothing to compact in {db}: no deletion is recorded since the last compaction finished\n"
    )
    assert printed.err == "" and calls == [] and db.read_bytes() == before


def test_when_only_the_log_is_due_it_empties_the_log_alone(db, calls, capsys):
    record(db, due=DELETED, rewritten=True)
    assert state(db).rewritten is True
    assert cli.main(["compact", "--if-due", "--db", str(db)]) == 0
    printed = capsys.readouterr()
    assert printed.out == (
        f"emptied the write-ahead log of {db}: the file was rewritten after the last deletion,"
        " so only the log was due\n"
    )
    assert calls == ["empty_log"]
    assert state(db).deleted_at is None  # the record is gone: the compaction is done


def test_a_log_held_by_another_connection_is_not_finished_as_compact_says_it(
    db, monkeypatch, capsys
):
    record(db, due=DELETED, rewritten=True)

    def held(self):
        raise portable.CompactIncomplete("another connection is reading the log")

    monkeypatch.setattr(Store, "empty_log", held)
    assert cli.main(["compact", "--if-due", "--db", str(db)]) == 1
    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err == f"{db} was not fully compacted: another connection is reading the log\n"
    assert state(db).rewritten is True  # the record stays


def test_a_deletion_not_yet_rewritten_is_compacted_as_compact_does(db, calls, capsys):
    record(db, due=DELETED)
    assert cli.main(["compact", "--if-due", "--db", str(db)]) == 0
    flagged = capsys.readouterr().out.splitlines()
    assert calls[:2] == ["prune_by_record", "compact"] and "empty_log" not in calls
    assert state(db).deleted_at is None
    assert flagged[-1].startswith(f"compacted {db}: ")
    record(db, due=DELETED)
    calls.clear()
    assert cli.main(["compact", "--db", str(db)]) == 0
    plain = capsys.readouterr().out.splitlines()
    assert [x.split(":")[0] for x in plain] == [x.split(":")[0] for x in flagged]
    assert calls[:2] == ["prune_by_record", "compact"]


def test_without_the_flag_compact_always_rewrites(db, calls, capsys):
    record(db, due=None)
    assert cli.main(["compact", "--db", str(db)]) == 0
    assert calls[:2] == ["prune_by_record", "compact"]
    record(db, due=DELETED, rewritten=True)
    calls.clear()
    assert cli.main(["compact", "--db", str(db)]) == 0
    assert "compact" in calls and "empty_log" not in calls


def test_a_missing_database_is_refused_with_the_flag_too(tmp_path, capsys):
    missing = tmp_path / "missing.db"
    assert cli.main(["compact", "--if-due", "--db", str(missing)]) == 1
    assert "no database" in capsys.readouterr().err and not missing.exists()


# The guides


def flat(text: str) -> str:
    return " ".join(text.split())


RUN_SAYS = (
    "With `--if-due`, `compact` does only what a recorded deletion still needs, in one line:"
    " nothing when no deletion is recorded since the last compaction finished; when the file"
    " was rewritten after the last deletion and only its write-ahead log is left (the line"
    " `serve` logs says `rewritten; the write-ahead log was not emptied`), the log alone,"
    " without a second rewrite; and otherwise the whole compaction. It exits 0 when it is done"
    " and 1 when it is not, as `compact` does."
)


def test_run_md_says_what_if_due_does():
    raw = guide_source("run")
    assert flat(RUN_SAYS) in flat(raw) and flat(raw).count(flat(RUN_SAYS)) == 1
    paragraph = next(p for p in raw.split("\n\n") if flat(RUN_SAYS) in flat(p))
    assert paragraph.startswith("A compaction needs twice the database's size free")
