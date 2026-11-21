"""An import owns its staging files. Another writer owns what it puts at the destination."""

from contextlib import closing

import pytest

from cairn.store import DEFAULT_ACCOUNT_ID, Store
from cairn.web import cli, cli_data


@pytest.mark.parametrize("valid", [True, False], ids=["publish", "refuse"])
def test_import_keeps_a_concurrent_creators_files(tmp_path, monkeypatch, capsys, valid):
    db = tmp_path / "db"
    archive = tmp_path / "in.zip"
    with closing(Store(":memory:")) as source, archive.open("wb") as out:
        source.create_project("orchard")
        source.export(out)
    if not valid:
        archive.write_bytes(b"bad")
    real = cli_data.make_folder
    side = db.with_name("db-journal")

    def create_first(path):
        made = real(path)
        db.write_bytes(b"first")
        side.write_bytes(b"kept")
        return made

    monkeypatch.setattr(cli_data, "make_folder", create_first)
    result = cli.main(
        ["import", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--in", str(archive)]
    )
    assert db.is_file() and db.read_bytes() == b"first"
    assert side.read_bytes() == b"kept"
    assert result == 1
    out, err = capsys.readouterr()
    assert out == "" and ("already exists" in err or "nothing was imported" in err)
    assert "workspace.imported" not in err
    assert set(tmp_path.iterdir()) == {archive, db, side}


def test_import_does_not_publish_over_a_leftover_sqlite_log(tmp_path, capsys):
    archive = tmp_path / "in.zip"
    with closing(Store(":memory:")) as source, archive.open("wb") as out:
        source.export(out)
    db = tmp_path / "db"
    log = tmp_path / "db-wal"
    log.write_bytes(b"kept")
    assert (
        cli.main(["import", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--in", str(archive)])
        == 1
    )
    assert not db.exists() and log.read_bytes() == b"kept"
    assert "db-wal" in capsys.readouterr().err
