"""A truncated SQLite file receives one diagnosis and no upgrade advice."""

import pytest
from cli_commands import ARGS, COMMANDS

from cairn.store import Store
from cairn.web import cli


@pytest.mark.parametrize("command", COMMANDS)
def test_truncated_database_is_left_for_recovery(tmp_path, capsys, command):
    db = tmp_path / "db"
    Store(db).close()
    with db.open("r+b") as damaged:
        damaged.truncate(4096)
    (tmp_path / "archive.zip").touch()
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    argv = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in ARGS[command])]
    result = cli.main(argv)
    out, err = capsys.readouterr()
    assert result == 1 and out == "" and len(err.splitlines()) == 1
    assert str(db) in err and "damaged" in err
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before
