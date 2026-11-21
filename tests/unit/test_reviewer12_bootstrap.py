"""Creating the first key publishes a complete database or leaves the path free."""

from cairn.store import Store
from cairn.web import cli, cli_data


def test_refused_key_bootstrap_does_not_leave_a_database(tmp_path, monkeypatch):
    def refuse(self, label):
        raise ValueError("cannot mint this key")

    monkeypatch.setattr(Store, "create_key", refuse)
    db = tmp_path / "new" / "db"
    out = db.parent / "key.env"
    assert cli.main(["key-create", "--db", str(db), "--out", str(out), "--label", "demo"]) == 1
    assert not db.parent.exists()


def test_key_bootstrap_does_not_mint_into_a_concurrent_creators_database(tmp_path, monkeypatch):
    db = tmp_path / "db"
    out = tmp_path / "key.env"
    make_folder = cli_data.make_folder

    def another(path):
        result = make_folder(path)
        if not db.exists():
            Store(db).close()
        return result

    monkeypatch.setattr(cli_data, "make_folder", another)
    assert cli.main(["key-create", "--db", str(db), "--out", str(out), "--label", "demo"]) == 1
    assert not out.exists()
    store = Store(db)
    try:
        assert store.list_keys() == []
    finally:
        store.close()
