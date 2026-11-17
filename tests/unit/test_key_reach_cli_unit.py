"""Unit tests for a narrow key on the command line (H1, item 2; the acceptance test is
tests/acceptance/test_h1_hardening.py): key-create's --project and --read-only, the refusal of
a project the workspace does not have, and key-list's lines, which say what the Keys page says
of each key. Every name is made up."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cairn.web import cli
from cairn.web.components import scope_of


def create(db: Path, out: Path, *extra: str, label: str = "reader") -> int:
    return cli.main(["key-create", "--db", str(db), "--label", label, "--out", str(out), *extra])


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "cairn.db"
    store = Store(path)
    for name in ("alpha", "beta"):
        store.create_project(name, "")
    store.create_set("both", tuple(b.id for b in store.list_projects()))
    store.close()
    return path


def keys(db: Path) -> list:
    store = Store(db)
    try:
        return store.list_keys()
    finally:
        store.close()


def test_a_project_named_twice_is_reached_once(db, tmp_path):
    assert create(db, tmp_path / "k.env", "--project", "alpha", "--project", "alpha") == 0
    (info,) = keys(db)
    assert len(info.projects) == 1 and info.access == "write"


def test_read_only_alone_reaches_every_project_to_read(db, tmp_path):
    assert create(db, tmp_path / "k.env", "--read-only") == 0
    (info,) = keys(db)
    assert (info.projects, info.access) == (None, "read")


def test_a_set_is_not_a_project(db, tmp_path, capsys):
    """--project names projects; a set of projects is chosen on the Keys page."""
    assert create(db, tmp_path / "k.env", "--project", "both") == 1
    assert capsys.readouterr().err == cli.NO_SUCH_PROJECT.format(name="both") + "\n"
    assert keys(db) == [] and not (tmp_path / "k.env").exists()


def test_the_refusal_leaves_a_key_file_that_was_there_alone(db, tmp_path, capsys):
    out = tmp_path / "k.env"
    out.write_text("kept\n")
    assert create(db, out, "--project", "nowhere") == 1
    assert out.read_text() == "kept\n"  # never overwritten, never removed
    assert keys(db) == []


def test_key_list_says_what_the_keys_page_says(db, tmp_path, capsys):
    """Each line is the page's scope component in words: the mode and what it reaches, row
    by row, then what is out of reach, then who made it and when."""
    assert create(db, tmp_path / "a.env", label="all") == 0
    assert create(db, tmp_path / "b.env", "--project", "beta", label="one") == 0
    capsys.readouterr()
    assert cli.main(["key-list", "--db", str(db)]) == 0
    lines = capsys.readouterr().out.splitlines()
    store = Store(db)
    try:
        names = {b.id: b.name for b in store.list_projects()}
        names |= {s.id: s.name for s in store.list_sets()}
        for line, info in zip(lines, store.list_keys(), strict=True):
            found = scope_of(
                info.projects,
                info.access,
                names,
                store.list_sets(),
                store.list_projects(),
                "nothing in this workspace",
            )
            rows = "; ".join(f"{row.mode}: {row.what}" for row in found.rows)
            assert line == (
                f"{info.label}: {rows}; No access: {found.beyond}; made by the workspace on"
                f" {info.created_at}"
            )
    finally:
        store.close()


def test_what_the_help_says_a_key_can_do_with_and_without_options(db, tmp_path, capsys):
    """KEY_CREATE_HELP: with no option a key reads and writes every project, exports the
    workspace and opens a web session; limited either way it exports nothing and opens no
    web session."""
    made = {}
    for label, extra in (("full", []), ("ro", ["--read-only"]), ("one", ["--project", "beta"])):
        out = tmp_path / f"{label}.env"
        assert create(db, out, *extra, label=label) == 0
        made[label] = out.read_text().strip().partition("=")[2]
    store = Store(db)
    base = "http://127.0.0.1:8765"
    try:
        with TestClient(create_app(store, public_url=base), base_url=base) as client:
            for label, can in (("full", True), ("ro", False), ("one", False)):
                key = {"x-api-key": made[label]}
                exported = client.get("/api/account/export", headers=key).status_code
                session = client.post(
                    "/login",
                    data={"key": made[label]},
                    headers={"sec-fetch-site": "same-origin"},
                    follow_redirects=False,
                ).status_code
                assert (exported == 200, session == 303) == (can, can), label
            full = {"x-api-key": made["full"]}
            assert (
                client.post(
                    "/api/projects/alpha/notes", headers=full, json={"text": "# n\n\nx 1\n"}
                ).status_code
                == 201
            )
            assert len(client.get("/api/projects", headers=full).json()["projects"]) == 2
    finally:
        store.close()
    assert cli.KEY_DEFAULT in cli.KEY_CREATE_HELP and cli.KEY_DEFAULT in cli.KEY_LIST_HELP


def test_key_list_with_no_key_and_with_no_database(db, tmp_path, capsys):
    assert cli.main(["key-list", "--db", str(db)]) == 0
    assert capsys.readouterr() == ("", "")
    missing = tmp_path / "missing.db"
    assert cli.main(["key-list", "--db", str(missing)]) == 1
    assert capsys.readouterr().err == f"no database at {missing}\n"
    assert not missing.exists()
