import os
import sqlite3

import pytest
from cli_commands import ARGS

from cairn.web import cli


@pytest.mark.parametrize("command", ["serve", "import", "key-create", "stats", "repair"])
def test_a_fifo_is_refused_before_sqlite_can_open_it(tmp_path, monkeypatch, capsys, command):
    db = tmp_path / "pipe"
    os.mkfifo(db)
    (tmp_path / "archive.zip").touch()
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("opened FIFO"))
    args = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in ARGS[command])]
    assert cli.main(args) == 1
    assert "regular file" in capsys.readouterr().err
    assert db.is_fifo() and not (tmp_path / "key.env").exists()
