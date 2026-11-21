"""The operator can tell which committed schema an unsuccessful start left on disk."""

from contextlib import closing

import pytest
from test_repair_says_what_committed_unit import at_28_file, clean_31
from test_store_unit import connect, user_version

from cairn.store import Store, schema
from cairn.web import cli


def test_bad_model_settings_leave_the_old_database_and_lock_alone(tmp_path, monkeypatch, capsys):
    db = clean_31(tmp_path)
    before = db.read_bytes()
    monkeypatch.delenv("DEMO_MODEL", raising=False)
    locks = []
    take = cli.take_serve_lock

    def locking(path):
        locks.append(path)
        return take(path)

    monkeypatch.setattr(cli, "take_serve_lock", locking)
    assert cli.main(["serve", "--db", str(db), "--model-env", "DEMO"]) == 2
    assert "cannot use that model" in capsys.readouterr().err
    assert db.read_bytes() == before and locks == []


@pytest.mark.parametrize("start", ["new", "older", "current"])
def test_listener_failure_names_every_schema_change(tmp_path, monkeypatch, capsys, start):
    db = tmp_path / "db"
    if start == "older":
        db = clean_31(tmp_path)
    if start == "current":
        with closing(Store(db)):
            pass

    def refused(*args, **kwargs):
        raise SystemExit(3)

    monkeypatch.setattr(cli.uvicorn, "run", refused)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["serve", "--db", str(db)])
    assert stopped.value.code == 3
    err = capsys.readouterr().err
    assert user_version(db) == 32
    with connect(db) as con:
        assert con.execute("SELECT version FROM oldest_build").fetchone() == (32,)
    if start == "current":
        assert "committed schema" not in err
    else:
        source = 0 if start == "new" else 31
        assert f"committed schema {source} -> 32" in err and "floor 32" in err


def test_a_later_step_failing_names_only_the_steps_that_committed(tmp_path, monkeypatch, capsys):
    db = at_28_file(tmp_path)

    def broken(con):
        raise RuntimeError("step refused")

    monkeypatch.setitem(schema._MIGRATIONS, 30, broken)
    with pytest.raises(RuntimeError, match="step refused"):
        cli.main(["serve", "--db", str(db)])
    assert user_version(db) == 29
    err = capsys.readouterr().err
    assert "committed schema 28 -> 29" in err and "no recorded floor" in err
