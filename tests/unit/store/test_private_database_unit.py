"""Unit tests for a private database on the command line (H1, item 1; the acceptance test is
tests/acceptance/test_h1_hardening.py): the folders a command makes are 0700, the file is
made 0600 before SQLite opens it and never through a link, a --db that is a link is refused
before anything, and an existing database others can read is named, once, and left as it is.
Every name is made up."""

import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

from cairn.store import CannotOpen, Store
from cairn.web import cli, cli_data

AS_ROOT = os.geteuid() == 0


def mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


@pytest.fixture
def wide_umask():
    """No umask at all: the mode a command asks for is all that decides."""
    old = os.umask(0)
    try:
        yield
    finally:
        os.umask(old)


@pytest.fixture
def open_folder():
    """A folder under /tmp, mode 0755: others may enter it and every folder above it."""
    folder = Path(tempfile.mkdtemp(prefix="cairn-h1-", dir="/tmp"))
    os.chmod(folder, 0o755)
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_every_folder_made_for_the_database_is_0700(tmp_path, wide_umask):
    (tmp_path / "kept").mkdir(mode=0o755)
    db = tmp_path / "kept" / "a" / "b" / "cairn.db"
    cli_data.make_folder(db)
    assert mode(tmp_path / "kept") == 0o755  # a folder that was there is not touched
    assert mode(tmp_path / "kept" / "a") == 0o700 and mode(db.parent) == 0o700
    cli_data.make_folder(db)  # twice is harmless


def test_the_database_file_is_made_0600_whatever_the_umask(tmp_path, wide_umask):
    db = tmp_path / "cairn.db"
    cli_data.make_database(db)
    assert db.is_file() and db.stat().st_size == 0 and mode(db) == 0o600
    store = Store(db)  # SQLite takes an empty file as a new database
    try:
        store.create_project("ops", "")
        assert mode(Path(f"{db}-wal")) == 0o600 and mode(Path(f"{db}-shm")) == 0o600
    finally:
        store.close()


def test_an_existing_database_is_left_as_it_is(tmp_path):
    db = tmp_path / "cairn.db"
    Store(db).close()
    os.chmod(db, 0o644)
    before = db.read_bytes()
    cli_data.make_database(db)
    assert mode(db) == 0o644 and db.read_bytes() == before


def test_the_file_is_never_made_through_a_link(tmp_path):
    target, link = tmp_path / "elsewhere.db", tmp_path / "link.db"
    link.symlink_to(target)
    with pytest.raises(OSError):
        cli_data.make_database(link)
    assert not target.exists()


@pytest.mark.skipif(AS_ROOT, reason="root writes a folder whatever its mode says")
def test_a_folder_cairn_may_not_write_is_cannot_open(tmp_path):
    folder = tmp_path / "shut"
    folder.mkdir()
    os.chmod(folder, 0o500)
    try:
        with pytest.raises(CannotOpen):
            cli_data.make_database(folder / "cairn.db")
    finally:
        os.chmod(folder, 0o700)


def test_a_link_is_named_and_anything_else_is_not(tmp_path):
    db, link = tmp_path / "cairn.db", tmp_path / "link.db"
    Store(db).close()
    link.symlink_to(db)
    assert cli_data.linked(db) is None
    assert cli_data.linked(tmp_path / "absent.db") is None
    said = cli_data.linked(link)
    assert said == cli_data.LINKED_DB.format(db=link)
    assert "\n" not in said


def test_who_else_can_read_the_file(open_folder, tmp_path):
    db = open_folder / "cairn.db"
    Store(db).close()
    for bits, others in ((0o644, True), (0o640, True), (0o604, True), (0o600, False)):
        os.chmod(db, bits)
        assert (cli_data.readable_by_others(db) == bits) is others, oct(bits)
    os.chmod(db, 0o644)
    os.chmod(open_folder, 0o700)  # the folder lets nobody else through: nobody else reads
    assert cli_data.readable_by_others(db) is None
    os.chmod(open_folder, 0o750)  # members of the folder's group pass, and the file lets them
    assert cli_data.readable_by_others(db) == 0o644
    os.chmod(db, 0o604)  # ... but only others may read it, and others cannot pass
    assert cli_data.readable_by_others(db) is None
    assert cli_data.readable_by_others(tmp_path / "absent.db") is None
    assert cli_data.readable_by_others(open_folder) is None  # a folder is not a database


def test_the_warning_is_one_line_naming_the_mode_and_the_fix(open_folder):
    db = open_folder / "cairn.db"
    Store(db).close()
    os.chmod(db, 0o644)
    said = cli_data.readable(db)
    assert said == cli_data.READABLE_DB.format(db=db, mode=0o644)
    assert "\n" not in said and "0644" in said and f"chmod 600 {db}" in said
    os.chmod(db, 0o600)
    assert cli_data.readable(db) is None


def test_main_refuses_a_link_before_the_command_runs(tmp_path, capsys, monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run", lambda args: ran.append(args) or 0)
    link = tmp_path / "link.db"
    link.symlink_to(tmp_path / "elsewhere.db")
    assert cli.main(["key-revoke", "--db", str(link), "--label", "laptop"]) == 2
    assert ran == []
    out, err = capsys.readouterr()
    assert out == "" and err == cli_data.LINKED_DB.format(db=link) + "\n"
