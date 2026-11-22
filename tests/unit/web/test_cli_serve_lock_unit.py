"""Unit tests for the lock that keeps one `serve` per database (R1), beside what
tests/acceptance/test_r1_store_health.py pins with real processes: where the lock file is,
that it is removed by whoever holds it, that a file swapped under a taker is not trusted, a
link planted at its path, a lock file cairn may not make, and that `serve` refuses before it
opens anything. `serve` runs in this process with uvicorn.run recorded and never run."""

import fcntl
import os
import sqlite3
from pathlib import Path

import pytest

from cairn.store import Store
from cairn.web import cli

AS_ROOT = os.geteuid() == 0


@pytest.fixture
def listened(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    return calls


def serve(db: Path) -> int:
    return cli.main(["serve", "--db", str(db), "--port", "8765"])


def tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def test_the_lock_file_is_beside_the_database_and_named_after_it(tmp_path):
    db = tmp_path / "data" / "kb.db"
    assert cli.serve_lock_path(db) == Path(os.path.realpath(tmp_path)) / "data" / "kb.db.serve.lock"


def test_two_spellings_and_a_link_to_the_database_find_one_lock(tmp_path):
    (tmp_path / "data").mkdir()
    db = tmp_path / "data" / "kb.db"
    Store(db).close()
    link = tmp_path / "kb-link.db"
    link.symlink_to(db)
    spellings = [db, tmp_path / "data" / ".." / "data" / "kb.db", link]
    assert len({cli.serve_lock_path(path) for path in spellings}) == 1
    held = cli.take_serve_lock(db)
    try:
        assert all(cli.take_serve_lock(path) is None for path in spellings)
    finally:
        held.release()


def test_a_held_lock_refuses_a_second_taker_and_is_removed_when_let_go(tmp_path):
    db = tmp_path / "kb.db"
    held = cli.take_serve_lock(db)
    assert held is not None and held.path.exists()
    assert cli.take_serve_lock(db) is None
    held.release()
    assert not held.path.exists()
    again = cli.take_serve_lock(db)
    assert again is not None
    again.release()
    again.release()  # twice is harmless


def test_a_lock_file_left_behind_blocks_nothing(tmp_path, listened):
    db = tmp_path / "kb.db"
    cli.serve_lock_path(db).write_text("")  # a killed server's, not held by anyone
    assert serve(db) == 0
    assert len(listened) == 1
    assert tree(tmp_path) == ["kb.db"]  # removed when serve stopped (WAL files went too)


def test_a_file_swapped_under_the_taker_is_not_trusted(tmp_path, monkeypatch):
    """The server that held the lock removed its file between this taker's open and its
    lock, and another made a new one: the taker locks again, on the file at the path."""
    db = tmp_path / "kb.db"
    path = cli.serve_lock_path(db)
    real, calls = fcntl.flock, []

    def swapping(fd, operation):
        calls.append(fd)
        if len(calls) == 1:
            path.unlink()
            path.write_text("")
        return real(fd, operation)

    monkeypatch.setattr(cli.fcntl, "flock", swapping)
    held = cli.take_serve_lock(db)
    try:
        assert len(calls) == 2
        assert os.path.samestat(os.fstat(held._fd), os.lstat(path))
    finally:
        held.release()


def test_serve_refuses_beside_a_running_server_before_it_opens_anything(tmp_path, listened, capsys):
    db = tmp_path / "data" / "kb.db"
    db.parent.mkdir()
    held = cli.take_serve_lock(Path(os.path.realpath(tmp_path)) / "data" / "kb.db")
    try:
        assert serve(db) == 2
        out, err = capsys.readouterr()
        assert out == "" and err == cli.ANOTHER_SERVER.format(db=db) + "\n"
        assert listened == []
        assert not db.exists()  # the store was never opened: no database made
        assert tree(tmp_path) == ["data", "data/kb.db.serve.lock"]  # the holder's file
    finally:
        held.release()


def test_serve_refuses_a_link_planted_at_the_lock_path(tmp_path, listened, capsys):
    db = tmp_path / "kb.db"
    Store(db).close()
    target = tmp_path / "elsewhere"
    cli.serve_lock_path(db).symlink_to(target)
    assert serve(db) == 1
    out, err = capsys.readouterr()
    assert out == ""
    (line,) = err.splitlines()
    assert line.startswith(f"cannot take the lock that keeps one server on {db}: ")
    assert listened == [] and not target.exists()


@pytest.mark.skipif(AS_ROOT, reason="root writes a folder whatever its mode says")
def test_a_lock_file_cairn_may_not_make_is_the_line_for_the_folder(tmp_path, listened, capsys):
    """A database cairn may read in a folder it may not write: the line is the store's own
    (cli.MAY_NOT_OPEN), exit 1, nothing made, as before the lock."""
    data = tmp_path / "data"
    data.mkdir()
    db = data / "kb.db"
    Store(db).close()
    before = tree(tmp_path)
    os.chmod(data, 0o555)
    try:
        assert serve(db) == 1
    finally:
        os.chmod(data, 0o755)
    out, err = capsys.readouterr()
    assert out == "" and err == cli.MAY_NOT_OPEN.format(path=db) + "\n"
    assert listened == [] and tree(tmp_path) == before


def test_serve_lets_go_of_the_lock_when_the_store_will_not_open(tmp_path, listened, capsys):
    db = tmp_path / "kb.db"
    db.write_bytes(b"a text file, not a database\n" * 64)
    assert serve(db) == 1
    assert tree(tmp_path) == ["kb.db"]
    assert listened == []
    held = cli.take_serve_lock(db)
    assert held is not None
    held.release()


def test_a_database_in_memory_takes_no_lock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    held = cli.take_serve_lock(Path(":memory:"))
    assert held is not None
    held.release()
    assert tree(tmp_path) == []


def test_other_commands_take_no_lock(tmp_path, capsys):
    db = tmp_path / "kb.db"
    Store(db).close()
    held = cli.take_serve_lock(db)
    try:
        assert cli.main(["usage", "--db", str(db), "--account", "acct_default"]) == 0
        assert cli.main(["session-end", "--db", str(db), "--email", "ada@example.test"]) == 0
        assert cli.main(["compact", "--db", str(db)]) == 0
    finally:
        held.release()
    con = sqlite3.connect(db)
    con.close()
