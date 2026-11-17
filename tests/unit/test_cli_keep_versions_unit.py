"""Q1 B on the command line (cairn.web.cli, cairn.web.cli_data): `serve --keep-versions`,
what serve says and records at start, and what `compact` removes by that record. `serve`
runs in this process with uvicorn.run recorded and never run."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cairn.store import Store
from cairn.web import cli

START = datetime(2099, 7, 1, 9, 0, tzinfo=UTC)
PLANS = "default: base\nplans:\n  base:\n    label: Base\n    versions_per_page: 2\n"


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def built(monkeypatch) -> list:
    """The keep_versions each serve built its app with, and uvicorn.run never run."""
    seen: list = []
    real = cli.create_app

    def create_app(store, **options):
        seen.append(options.get("keep_versions", "left out"))
        return real(store, **options)

    monkeypatch.setattr(cli, "create_app", create_app)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: None)
    return seen


def history(db: Path, pages: dict[str, int]) -> None:
    store = Store(db, clock=Clock())
    try:
        project = store.create_project("north")
        for slug, times in pages.items():
            for i in range(times):
                store.save_page(project.id, slug, slug.title(), f"Body {i}.", [])
    finally:
        store.close()


def versions(db: Path, slug: str) -> list[int]:
    store = Store(db, clock=Clock())
    try:
        return store.page_versions(store.get_project("north").id, slug)
    finally:
        store.close()


def serve(db: Path, *argv: str) -> int:
    return cli.main(["serve", "--db", str(db), "--no-tidy", *argv])


@pytest.mark.parametrize(("value", "parsed"), [("1", 1), ("50", 50), ("all", cli.ALL_VERSIONS)])
def test_keep_versions_parses_a_whole_number_or_all(value, parsed):
    args = cli._parser().parse_args(["serve", "--db", "x.db", "--keep-versions", value])
    assert args.keep_versions == parsed
    assert cli._parser().parse_args(["serve", "--db", "x.db"]).keep_versions is None


@pytest.mark.parametrize("bad", ["0", "-1", "1.5", "every", "", " 3", "1e3"])
def test_keep_versions_refuses_anything_else(capsys, bad):
    with pytest.raises(SystemExit) as stopped:
        cli._parser().parse_args(["serve", "--db", "x.db", "--keep-versions", bad])
    assert stopped.value.code == 2
    assert "--keep-versions" in capsys.readouterr().err


@pytest.mark.parametrize(("argv", "kept"), [((), 50), (("--keep-versions", "7"), 7),
                                            (("--keep-versions", "all"), None)])  # fmt: skip
def test_serve_builds_its_app_with_the_bound(tmp_path, built, argv, kept):
    assert serve(tmp_path / "cairn.db", *argv) == 0
    assert built == [kept]


def test_with_plans_serve_passes_no_bound(tmp_path, built):
    plans = tmp_path / "plans.yaml"
    plans.write_text(PLANS, encoding="utf-8")
    assert serve(tmp_path / "cairn.db", "--plans", str(plans)) == 0
    assert built == [None]


@pytest.mark.parametrize("value", ["3", "all"])
def test_with_plans_keep_versions_exits_2_in_one_line_before_anything(
    tmp_path, built, capsys, value
):
    plans = tmp_path / "plans.yaml"
    plans.write_text(PLANS, encoding="utf-8")
    db = tmp_path / "data" / "cairn.db"
    assert serve(db, "--plans", str(plans), "--keep-versions", value) == 2
    said = capsys.readouterr()
    assert said.out == "" and len(said.err.strip().splitlines()) == 1
    assert "--keep-versions" in said.err and "--plans" in said.err
    assert built == [] and not db.parent.exists()  # nothing made


def test_serve_says_how_many_pages_hold_more_than_the_bound(tmp_path, built, capsys):
    db = tmp_path / "cairn.db"
    history(db, {"a": 5, "b": 4, "c": 3})
    capsys.readouterr()
    assert serve(db, "--keep-versions", "3") == 0
    (line,) = [x for x in capsys.readouterr().err.splitlines() if "--keep-versions all" in x]
    assert line.startswith("2 pages hold more than 3 versions")
    assert versions(db, "a") == [1, 2, 3, 4, 5]  # nothing removed at start
    assert serve(db, "--keep-versions", "4") == 0
    (line,) = [x for x in capsys.readouterr().err.splitlines() if "--keep-versions all" in x]
    assert line.startswith("1 page holds more than 4 versions")
    for quiet in (("--keep-versions", "5"), ("--keep-versions", "all")):
        assert serve(db, *quiet) == 0
        assert "keep-versions" not in capsys.readouterr().err


def compact(db: Path, capsys) -> tuple[int, str, str]:
    capsys.readouterr()
    code = cli.main(["compact", "--db", str(db)])
    said = capsys.readouterr()
    return code, said.out, said.err


def test_compact_with_no_record_removes_nothing_and_says_to_run_serve(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    history(db, {"a": 5})
    code, out, err = compact(db, capsys)
    assert code == 0 and versions(db, "a") == [1, 2, 3, 4, 5]
    lines = out.splitlines()
    assert len(lines) == 2 and "serve" in lines[0] and lines[1].startswith("compacted ")
    assert err == ""


def test_compact_removes_by_what_the_last_serve_recorded(tmp_path, built, capsys):
    db = tmp_path / "cairn.db"
    history(db, {"a": 5, "b": 2})
    assert serve(db, "--keep-versions", "2") == 0
    code, out, _ = compact(db, capsys)
    assert code == 0 and versions(db, "a") == [4, 5] and versions(db, "b") == [1, 2]
    assert out.splitlines()[0] == "removed 3 older page versions: each page keeps its newest 2"
    assert serve(db, "--keep-versions", "all") == 0
    _, out, _ = compact(db, capsys)
    assert out.splitlines()[0] == "removed 0 older page versions: each page keeps every version"


def test_compact_names_the_workspaces_whose_plan_the_record_does_not_name(tmp_path, built, capsys):
    db = tmp_path / "cairn.db"
    history(db, {"a": 5})
    store = Store(db, clock=Clock())
    try:
        other = store.create_account("Other")
        store.set_plan(other.id, "retired")
    finally:
        store.close()
    plans = tmp_path / "plans.yaml"
    plans.write_text(PLANS, encoding="utf-8")
    assert serve(db, "--plans", str(plans)) == 0
    code, out, _ = compact(db, capsys)
    assert code == 0 and versions(db, "a") == [4, 5]
    first, second, last = out.splitlines()
    assert first == "removed 3 older page versions: each workspace keeps what its plan keeps"
    assert other.id in second and "every version" in second and "plan" in second
    assert last.startswith("compacted ")


def test_the_record_is_not_a_setting(tmp_path, built):
    db = tmp_path / "cairn.db"
    assert serve(db, "--keep-versions", "4") == 0
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT account_id, name FROM settings").fetchall()
    assert rows == [("acct_default", "store.keep_versions")]
    store = Store(db)
    try:
        with pytest.raises(ValueError):
            store.get_setting("store.keep_versions")
    finally:
        store.close()
