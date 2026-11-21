"""A refusal of an older database has the same single sentence at every command."""

import os
import tempfile
from pathlib import Path

import pytest
from cli_commands import ARGS, COMMANDS
from test_repair_says_what_committed_unit import clean_31

from cairn.web import cli, cli_data


@pytest.mark.parametrize("command", [c for c in COMMANDS if c != "serve"])
def test_publicly_readable_old_database_gets_one_refusal(command, capsys):
    with tempfile.TemporaryDirectory(dir="/tmp") as folder:
        root = Path(folder)
        db = clean_31(root)
        os.chmod(root, 0o755)
        os.chmod(db, 0o644)
        assert cli_data.readable(db)
        (root / "archive.zip").touch()
        argv = [command, "--db", str(db), *(s.format(dir=root) for s in ARGS[command])]
        assert cli.main(argv) == 1
        out, err = capsys.readouterr()
        assert out == "" and len(err.splitlines()) == 1
