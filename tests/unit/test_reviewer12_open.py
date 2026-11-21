"""Drive database opens from the parser, including commands with a late failure."""

from pathlib import Path

import pytest
from cli_commands import ARGS, COMMANDS, CREATES, PLANS_YAML, VARIANTS

from cairn.store import Store
from cairn.web import cli

ADMIN = [(c, ARGS[c]) for c in COMMANDS if c != "serve"] + VARIANTS + [("compact", ["--if-due"])]


@pytest.mark.parametrize(("command", "extra"), ADMIN)
def test_existing_empty_file_is_refused_without_writing(tmp_path, capsys, command, extra):
    db = tmp_path / "empty"
    db.touch()
    (tmp_path / "archive.zip").write_bytes(b"bad")
    (tmp_path / "plans.yaml").write_text(PLANS_YAML)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    argv = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in extra)]
    result = cli.main(argv)
    out, err = capsys.readouterr()
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert (result, out) == (1, "")
    assert len(err.splitlines()) == 1 and "uninitialized" in err and str(db) in err


@pytest.mark.parametrize("command", [c for c in COMMANDS if c not in CREATES])
def test_a_database_removed_just_before_open_is_not_recreated(
    tmp_path, capsys, monkeypatch, command
):
    db = tmp_path / "db"
    Store(db).close()
    is_file = Path.is_file

    def removed(path):
        answer = is_file(path)
        if path == db and answer:
            db.unlink()
        return answer

    monkeypatch.setattr(Path, "is_file", removed)
    argv = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in ARGS[command])]
    assert cli.main(argv) == 1
    out, err = capsys.readouterr()
    assert not db.exists() and out == "" and err == f"no database at {db}\n"
