"""Different names of one inode would split both advisory locks and SQLite journals."""

from contextlib import closing

import pytest
from cli_commands import ARGS, COMMANDS

from cairn.store import Store
from cairn.web import cli


@pytest.mark.parametrize("command", COMMANDS)
def test_a_second_name_cannot_open_the_database(tmp_path, capsys, monkeypatch, command):
    db, alias = tmp_path / "original", tmp_path / "alias"
    with closing(Store(db)):
        pass
    lock = cli.take_serve_lock(db)
    assert lock is not None
    try:
        alias.hardlink_to(db)
        (tmp_path / "archive.zip").touch()
        before = {p: p.read_bytes() for p in tmp_path.iterdir()}
        monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("second server"))
        args = [command, "--db", str(alias), *(s.format(dir=tmp_path) for s in ARGS[command])]
        assert cli.main(args) == 2
        out, err = capsys.readouterr()
        assert out == "" and "hard link" in err and str(alias) in err
        assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before
        with pytest.raises(ValueError, match="hard link"):
            Store(alias)
    finally:
        lock.release()
