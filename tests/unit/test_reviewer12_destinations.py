"""An output cannot occupy a filename SQLite or the command locks will use."""

from contextlib import closing

import pytest

from cairn.store import DEFAULT_ACCOUNT_ID, Store
from cairn.web import cli


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm", ".serve.lock", ".compact.lock"])
@pytest.mark.parametrize("command", ["export", "key-create"])
def test_an_output_file_cannot_be_a_database_companion(tmp_path, capsys, command, suffix):
    db = tmp_path / "db"
    with closing(Store(db)):
        pass
    out = tmp_path / ("db" + suffix)
    extra = ["--label", "demo"] if command == "key-create" else ["--account", DEFAULT_ACCOUNT_ID]
    before = db.read_bytes()
    assert cli.main([command, "--db", str(db), "--out", str(out), *extra]) == 1
    said, err = capsys.readouterr()
    assert not out.exists() and db.read_bytes() == before
    assert said == "" and "reserved" in err
