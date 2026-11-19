"""`serve --audit-reads` and `serve --audit-keep` (cairn.web.cli), and the "cairn.audit" logger
on serve's stderr. `serve` runs in this process with uvicorn.run recorded and never run."""

import json
import logging

import pytest
from starlette.testclient import TestClient

from cairn.store import Store
from cairn.store.audit import AUDIT_LOGGER
from cairn.web import cli

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def built(monkeypatch) -> list:
    """The audit options each serve built its app with, and uvicorn.run never run."""
    seen: list = []
    real = cli.create_app

    def create_app(store, **options):
        seen.append((options.get("audit_reads", "left out"), options.get("audit_keep", "left out")))
        return real(store, **options)

    monkeypatch.setattr(cli, "create_app", create_app)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: None)
    return seen


def serve(db, *argv: str) -> int:
    return cli.main(["serve", "--db", str(db), "--no-tidy", *argv])


@pytest.mark.parametrize(("value", "parsed"), [("1", 1), ("250000", 250_000), ("all", "all")])
def test_audit_keep_parses_a_whole_number_or_all(value, parsed):
    args = cli._parser().parse_args(["serve", "--db", "x.db", "--audit-keep", value])
    assert args.audit_keep == parsed
    plain = cli._parser().parse_args(["serve", "--db", "x.db"])
    assert plain.audit_keep is None and plain.audit_reads is False


@pytest.mark.parametrize("bad", ["0", "-1", "1.5", "every", "", "1e5"])
def test_audit_keep_refuses_anything_else(capsys, bad):
    with pytest.raises(SystemExit) as stopped:
        cli._parser().parse_args(["serve", "--db", "x.db", "--audit-keep", bad])
    assert stopped.value.code == 2
    assert "--audit-keep" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "options"),
    [
        ((), (False, 100_000)),
        (("--audit-reads",), (True, 100_000)),
        (("--audit-keep", "5000"), (False, 5_000)),
        (("--audit-keep", "all", "--audit-reads"), (True, None)),
    ],
)
def test_serve_builds_its_app_with_the_audit_options(tmp_path, built, argv, options):
    assert serve(tmp_path / "cairn.db", *argv) == 0
    assert built == [options]


def test_the_help_says_a_read_then_costs_a_write(capsys):
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["serve", "--help"])
    said = " ".join(capsys.readouterr().out.split())
    assert "--audit-reads" in said and "one more write" in said
    assert "--audit-keep" in said and "100000" in said.replace(",", "")


def test_serve_writes_each_stored_event_as_one_line_on_its_own_logger(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("ci-runner")
    store.close()
    audit = logging.getLogger(AUDIT_LOGGER)
    before = (list(audit.handlers), audit.propagate, audit.level)

    def run(app, *, host, port, access_log, **proxy):
        with TestClient(app, base_url=BASE) as client:
            r = client.post("/api/projects", headers={"x-api-key": key}, json={"name": "ops"})
            assert r.status_code == 201

    monkeypatch.setattr(cli.uvicorn, "run", run)
    assert serve(db) == 0
    lines = [x for x in capsys.readouterr().err.splitlines() if x.startswith("cairn.audit: ")]
    (line,) = lines
    event = json.loads(line.removeprefix("cairn.audit: "))
    assert (event["action"], event["actor"], event["project"]) == (
        "project.created",
        "ci-runner",
        "ops",
    )
    assert (list(audit.handlers), audit.propagate, audit.level) == before
