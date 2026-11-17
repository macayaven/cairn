"""Unit tests for the sources on the command line and `serve --sync-every`. Langfuse is an
httpx.MockTransport behind the command line's client factory; nothing leaves the process."""

import json
import logging
import sys
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn import server
from cairn.sources import SOURCE_LOGGER, source_client
from cairn.store import Store
from cairn.web import cli

PUBLIC, SECRET = "pk-lf-cli-public", "sk-lf-cli-secret-do-not-leak"
ENV = {"LANGFUSE_PUBLIC_KEY": PUBLIC, "LANGFUSE_SECRET_KEY": SECRET}
CONFIG = {
    "base_url": "https://langfuse.example.test",
    "projects": ["payments-api"],
    "public_key_env": "LANGFUSE_PUBLIC_KEY",
    "secret_key_env": "LANGFUSE_SECRET_KEY",
}


def ago(minutes: float) -> str:
    """A time that many minutes before now: the command line runs on the real clock."""
    moment = datetime.now(UTC) - timedelta(minutes=minutes)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def langfuse(request: httpx.Request) -> httpx.Response:
    """One marked trace, every page at once."""
    if request.url.path.endswith("/scores"):
        score = {
            "id": "s1",
            "projectId": "proj",
            "name": "cairn",
            "dataType": "CATEGORICAL",
            "value": "payments-api",
            "timestamp": ago(60),
            "comment": "The refund rule.",
            "subject": {"kind": "trace", "id": "t1"},
        }
        return httpx.Response(200, json={"data": [score], "meta": {"limit": 100}})
    root = {
        "id": "o1",
        "traceId": "t1",
        "startTime": ago(61),
        "isRootObservation": True,
        "input": "Refund order 4411",
        "output": "Quill refunds take 5 days.",
        "traceName": "refund-flow",
    }
    return httpx.Response(200, json={"data": [root], "meta": {}})


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "cairn.db"
    store = Store(path)
    store.create_project("payments-api")
    store.close()
    monkeypatch.setattr(
        cli, "_source_http", lambda: httpx.AsyncClient(transport=httpx.MockTransport(langfuse))
    )
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    return path


PRINTED: list[str] = []  # everything every command printed, checked for keys


@pytest.fixture(autouse=True)
def no_key_printed():
    PRINTED.clear()
    yield
    assert PRINTED and not any(PUBLIC in s or SECRET in s for s in PRINTED)


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = server.main(list(argv))
    printed = capsys.readouterr()
    PRINTED.extend((printed.out, printed.err))
    return code, printed.out, printed.err


def test_add_list_sync_and_remove_a_source(db, capsys):
    added = run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
                "--config", json.dumps(CONFIG))  # fmt: skip
    assert added[0] == 0 and "traces" in added[1]
    code, out, _ = run(capsys, "source-list", "--db", str(db))
    assert code == 0 and "traces" in out and "langfuse.example.test" in out and "never" in out
    code, out, _ = run(capsys, "source-sync", "--db", str(db), "--name", "traces")
    assert code == 0 and "1 saved" in out
    code, out, _ = run(capsys, "source-sync", "--db", str(db), "--all")
    assert code == 0 and "traces: 0 saved" in out
    code, out, _ = run(capsys, "source-list", "--db", str(db))
    assert "0 saved" in out
    assert run(capsys, "source-remove", "--db", str(db), "--name", "traces")[0] == 0
    assert run(capsys, "source-remove", "--db", str(db), "--name", "traces")[0] == 1
    store = Store(db)
    try:
        hits = store.search("refunds days")
        assert hits and hits[0].project == "payments-api" and store.list_sources() == []
    finally:
        store.close()


def test_a_refused_config_or_an_unknown_workspace_exits_1_naming_no_value(db, capsys):
    leaked = {**CONFIG, "secret_key_env": SECRET}
    code, out, err = run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind",
                         "langfuse", "--config", json.dumps(leaked))  # fmt: skip
    assert code == 1 and SECRET not in out + err and "environment variable" in err
    code, _, err = run(capsys, "source-list", "--db", str(db), "--workspace", "acct_nope")
    assert code == 1 and "no workspace" in err
    with pytest.raises(SystemExit):
        server.main(["source-add", "--db", str(db), "--name", "x", "--kind", "langfuse",
                     "--config", "{not json"])  # fmt: skip


@pytest.mark.parametrize(
    "placed",
    [
        {"base_url": f"https://reader:{SECRET}@langfuse.example.test"},
        {"base_url": f"https://langfuse.example.test/?key={SECRET}"},
        {"projects": [SECRET]},
        {"public_key_env": SECRET},
        {"secret_key_env": SECRET},
        {"score_name": SECRET},
    ],
    ids=lambda placed: next(iter(placed)),
)
def test_a_secret_in_any_setting_is_refused_and_never_repeated(db, capsys, placed):
    code, out, err = run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind",
                         "langfuse", "--config", json.dumps({**CONFIG, **placed}))  # fmt: skip
    assert code == 1 and err.startswith("cannot add source 'traces': ")
    assert SECRET not in out + err


@pytest.mark.parametrize(
    "base_url",
    [
        f"https://langfuse.example.test/{SECRET}",
        f"https://langfuse.example.test/proxy/{SECRET.upper()}/",
        f"https://{SECRET}.example.test",
        f"https://langfuse.example.test/{PUBLIC}",
        "https://langfuse.example.test/AKIAIOSFODNN7EXAMPLE",
    ],
    ids=["path", "deeper path, any case", "host", "public key", "another kind of key"],
)
def test_a_credential_in_base_url_is_refused_naming_the_setting_and_never_listed(
    db, capsys, base_url
):
    """The setting is named, the value never repeated, and nothing is stored for
    `source-list` to print."""
    config = {**CONFIG, "base_url": base_url}
    code, out, err = run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind",
                         "langfuse", "--config", json.dumps(config))  # fmt: skip
    assert code == 1 and err.startswith("cannot add source 'traces': base_url ")
    printed = (out + err).casefold()
    assert SECRET.casefold() not in printed and PUBLIC not in printed
    assert "akiaiosfodnn7example" not in printed
    assert run(capsys, "source-list", "--db", str(db))[:2] == (0, "")


def test_a_base_url_that_only_resembles_a_key_is_kept(db, capsys):
    """A host label or path segment is held to the rule as a whole: "disk-lf-1" and a bare
    "sk-lf" are not keys."""
    config = {**CONFIG, "base_url": "https://disk-lf-1.example.test/sk-lf/x"}
    code, _, err = run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind",
                       "langfuse", "--config", json.dumps(config))  # fmt: skip
    assert code == 0, err


def test_a_refusal_may_repeat_a_project_name_the_workspace_lacks(db, capsys):
    config = {**CONFIG, "projects": ["ghost"]}
    code, _, err = run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind",
                       "langfuse", "--config", json.dumps(config))  # fmt: skip
    assert code == 1 and "no project named 'ghost'" in err


def test_a_failed_sync_exits_1_with_its_reason_and_no_key(db, capsys, monkeypatch):
    run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
        "--config", json.dumps(CONFIG))  # fmt: skip
    monkeypatch.delenv("LANGFUSE_SECRET_KEY")
    code, out, err = run(capsys, "source-sync", "--db", str(db), "--name", "traces")
    assert code == 1 and "LANGFUSE_SECRET_KEY" in err and PUBLIC not in out + err
    code, _, err = run(capsys, "source-sync", "--db", str(db), "--name", "ghost")
    assert code == 1 and "no source named 'ghost'" in err


def test_a_key_error_inside_a_run_is_a_failed_run_not_a_missing_source(db, capsys, monkeypatch):
    run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
        "--config", json.dumps(CONFIG))  # fmt: skip

    async def broken(*args, **kwargs):
        raise KeyError("payments-api")

    monkeypatch.setattr(cli, "run_source", broken)
    code, _, err = run(capsys, "source-sync", "--db", str(db), "--name", "traces")
    assert code == 1 and "KeyError" in err and "no source named" not in err


def test_a_sync_while_another_holds_the_source_says_so(db, capsys):
    run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
        "--config", json.dumps(CONFIG))  # fmt: skip
    store = Store(db)
    try:
        store.claim_source("traces")  # the server's background sync, say
        code, out, _ = run(capsys, "source-sync", "--db", str(db), "--name", "traces")
        assert code == 0 and "another run" in out
    finally:
        store.close()


def test_serve_without_sync_every_syncs_nothing_and_with_it_runs_a_pass_and_stops(
    db, capsys, monkeypatch
):
    run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
        "--config", json.dumps(CONFIG))  # fmt: skip
    failed = threading.Event()

    class Seen(logging.Handler):
        def emit(self, record):
            failed.set()

    def quiet(app, **options):  # serve without --sync-every: start and stop, no pass
        with TestClient(app) as client:
            assert client.get("/healthz").status_code == 200
        assert not failed.is_set()

    def syncing(app, **options):
        with TestClient(app) as client:
            assert failed.wait(10)  # the first pass runs at start-up
            assert client.get("/healthz").status_code == 200  # and requests are served

    logging.getLogger(SOURCE_LOGGER).addHandler(seen := Seen())
    try:
        monkeypatch.delenv("LANGFUSE_SECRET_KEY")  # a pass fails before any request
        monkeypatch.setattr(uvicorn, "run", quiet)
        assert server.main(["serve", "--db", str(db)]) == 0
        monkeypatch.setattr(uvicorn, "run", syncing)
        assert server.main(["serve", "--db", str(db), "--sync-every", "3600"]) == 0
    finally:
        logging.getLogger(SOURCE_LOGGER).removeHandler(seen)
    store = Store(db)
    try:
        # Shutdown stopped the background task; the source is not left claimed.
        assert store.claim_source("traces") is not None
        assert "LANGFUSE_SECRET_KEY" in store.get_source("traces").last_error
    finally:
        store.close()
    with pytest.raises(SystemExit):
        server.main(["serve", "--db", str(db), "--sync-every", "0"])


def test_serve_keeps_the_http_clients_request_lines_out_of_its_output(db, capsys, monkeypatch):
    """httpx logs each request's URL, query string included, at INFO; a source's queries
    (and a sign-in provider's) must not reach the server's output."""
    client_log, root = logging.getLogger("httpx"), logging.getLogger()
    levels = client_log.level, root.level
    seen = []
    monkeypatch.setattr(
        uvicorn, "run", lambda app, **options: seen.append(client_log.isEnabledFor(logging.INFO))
    )
    client_log.setLevel(logging.NOTSET)
    root.setLevel(logging.INFO)  # as the MCP SDK may leave it
    try:
        assert run(capsys, "serve", "--db", str(db))[0] == 0
        assert seen == [False]
        assert client_log.level == logging.NOTSET  # put back afterwards
    finally:
        client_log.setLevel(levels[0])
        root.setLevel(levels[1])


def test_source_sync_keeps_the_http_clients_request_lines_out_of_its_output(
    db, capsys, monkeypatch
):
    """httpx logs each request's URL with its query string at INFO. Whoever makes a
    source's client gets it quiet, the command line included, whatever the root logger."""
    run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
        "--config", json.dumps(CONFIG))  # fmt: skip
    monkeypatch.setattr(
        cli, "_source_http", lambda: source_client(transport=httpx.MockTransport(langfuse))
    )
    client_log, root = logging.getLogger("httpx"), logging.getLogger()
    levels = client_log.level, root.level
    handler = logging.StreamHandler(sys.stderr)
    client_log.setLevel(logging.NOTSET)
    root.setLevel(logging.INFO)  # as a host application may leave it
    root.addHandler(handler)
    try:
        code, out, err = run(capsys, "source-sync", "--db", str(db), "--name", "traces")
    finally:
        root.removeHandler(handler)
        client_log.setLevel(levels[0])
        root.setLevel(levels[1])
    assert code == 0 and "1 saved" in out
    assert "HTTP Request" not in err and "?" not in out + err


def test_a_report_says_what_was_left_for_the_next_run_and_never_lists_declined_drafts():
    report = {"saved": 1, "left": 4, "declined": [{"link": "https://x.test/t", "codes": []}]}
    said = cli._counts(report)
    PRINTED.append(said)  # what `source-list` and `source-sync` print of a report
    assert said == "1 saved, 0 unchanged, 0 refused, 0 rejected, 0 skipped; 4 left for the next run"
    assert "x.test" not in said


def plans_file(tmp_path, notes: int):
    path = tmp_path / "plans.yaml"
    path.write_text(f"default: tiny\nplans:\n  tiny: {{label: Tiny, notes_per_month: {notes}}}\n")
    return path


@pytest.mark.parametrize("which", [["--name", "traces"], ["--all"]])
def test_source_sync_with_plans_holds_the_workspace_to_its_plan(db, capsys, tmp_path, which):
    added = run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
                "--config", json.dumps(CONFIG))  # fmt: skip
    assert added[0] == 0
    plans = str(plans_file(tmp_path, notes=0))
    code, out, err = run(capsys, "source-sync", "--db", str(db), *which, "--plans", plans)
    assert code == 0 and "0 saved" in out and "1 left for the next run at the plan's cap" in out
    assert "no plan" not in err
    code, out, err = run(capsys, "source-sync", "--db", str(db), *which)
    assert code == 0 and "1 saved" in out
    assert err.count("\n") == 1 and "no plan is applied" in err  # one line, and it says so


@pytest.mark.parametrize("which", [["--name", "traces"], ["--all"]])
def test_source_sync_holds_a_workspace_to_the_plan_the_operator_set(db, capsys, tmp_path, which):
    run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
        "--config", json.dumps(CONFIG))  # fmt: skip
    plans = tmp_path / "plans.yaml"
    plans.write_text(
        "default: tiny\nplans:\n  tiny: {label: Tiny, notes_per_month: 0}\n  solo: {label: Solo}\n"
    )
    store = Store(db)
    try:
        store.set_plan(store.account_id, "solo")
    finally:
        store.close()
    code, out, _ = run(capsys, "source-sync", "--db", str(db), *which, "--plans", str(plans))
    assert code == 0 and "1 saved" in out


def test_source_sync_says_a_source_held_at_the_plans_monthly_cap_was_not_read(db, capsys, tmp_path):
    run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
        "--config", json.dumps(CONFIG))  # fmt: skip
    plans = str(plans_file(tmp_path, notes=0))
    argv = ["source-sync", "--db", str(db), "--name", "traces", "--plans", plans]
    code, out, _ = run(capsys, *argv)
    assert code == 0 and "held at the plan's monthly cap until" in out
    code, out, _ = run(capsys, *argv)
    assert code == 0 and "so not pulled" in out
    code, out, _ = run(capsys, "source-list", "--db", str(db))
    assert "held at the plan's monthly cap until" in out


def test_source_sync_with_a_bad_plans_file_exits_2_and_syncs_nothing(db, capsys, tmp_path):
    run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
        "--config", json.dumps(CONFIG))  # fmt: skip
    missing = str(tmp_path / "none.yaml")
    argv = ["source-sync", "--db", str(db), "--name", "traces", "--plans", missing]
    code, _, err = run(capsys, *argv)
    assert code == 2 and "cannot use those plans" in err
    store = Store(db)
    try:
        assert store.get_source("traces").last_run_at is None
    finally:
        store.close()


def test_sync_all_exits_1_when_one_source_fails_and_names_its_workspace(db, capsys):
    store = Store(db)
    other = store.create_account("Other").id
    store.for_account(other).create_project("payments-api")
    store.close()
    run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
        "--config", json.dumps(CONFIG))  # fmt: skip
    broken = {**CONFIG, "secret_key_env": "NOT_SET_ANYWHERE"}
    run(capsys, "source-add", "--db", str(db), "--workspace", other, "--name", "broken",
        "--kind", "langfuse", "--config", json.dumps(broken))  # fmt: skip
    code, out, err = run(capsys, "source-sync", "--db", str(db), "--all")
    assert code == 1
    assert "traces: 1 saved" in out and out.rstrip().endswith("(workspace acct_default)")
    failed = [line for line in err.splitlines() if line.startswith("broken: failed: ")]
    assert len(failed) == 1 and "NOT_SET_ANYWHERE" in failed[0]
    assert failed[0].endswith(f"(workspace {other})") and "broken" not in out


def test_sync_all_with_a_workspace_exits_2_and_syncs_nothing(db, capsys):
    run(capsys, "source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
        "--config", json.dumps(CONFIG))  # fmt: skip
    argv = ["source-sync", "--db", str(db), "--all", "--workspace", "acct_default"]
    code, out, err = run(capsys, *argv)
    assert (code, out) == (2, "")
    assert err == "--all syncs every workspace: leave out --workspace\n"
    store = Store(db)
    try:
        assert store.get_source("traces").last_run_at is None
    finally:
        store.close()


@pytest.mark.parametrize(
    "argv",
    [
        ["source-add", "--name", "traces", "--kind", "langfuse", "--config", json.dumps(CONFIG)],
        ["source-list"],
        ["source-sync", "--name", "traces"],
        ["source-sync", "--all"],
        ["source-remove", "--name", "traces"],
    ],
    ids=lambda argv: " ".join(argv[:2]),
)
def test_every_source_command_refuses_a_missing_database_and_creates_none(tmp_path, capsys, argv):
    missing = tmp_path / "cairn.db"  # its directory exists: a careless open would make it
    code, out, err = run(capsys, argv[0], "--db", str(missing), *argv[1:])
    assert (code, out) == (1, "") and f"no database at {missing}" in err
    assert not missing.exists()


def test_a_report_says_how_many_drafts_wait_for_the_plans_cap():
    said = cli._counts({"saved": 1, "capped": 2})
    PRINTED.append(said)
    assert said.endswith("; 2 left for the next run at the plan's cap")
    said = cli._counts({"saved": 1, "left": 1, "capped": 2})
    assert said.endswith("; 1 left for the next run; 2 left for the next run at the plan's cap")


def test_source_add_help_lists_the_settings_of_every_kind(capsys):
    """The sources guide sends an operator to this command for the settings: they are here."""
    from cairn.sources import KINDS
    from cairn.sources.github import SETTINGS as GITHUB_SETTINGS
    from cairn.sources.gmail import SETTINGS as GMAIL_SETTINGS
    from cairn.sources.google_calendar import SETTINGS as CALENDAR_SETTINGS
    from cairn.sources.google_drive import SETTINGS as DRIVE_SETTINGS
    from cairn.sources.google_meet import SETTINGS as MEET_SETTINGS
    from cairn.sources.granola import SETTINGS
    from cairn.sources.langfuse import CONFIG_KEYS
    from cairn.sources.linear import SETTINGS as LINEAR_SETTINGS

    with pytest.raises(SystemExit) as stopped:
        server.main(["source-add", "--help"])
    assert stopped.value.code == 0
    printed = capsys.readouterr()
    PRINTED.extend((printed.out, printed.err))  # the file's own check: no key in any output
    said = " ".join(printed.out.split())
    # a new kind lists its settings here too
    assert set(KINDS) == {
        "langfuse",
        "granola",
        "google_meet",
        "google_drive",
        "google_calendar",
        "gmail",
        "linear",
        "github",
        "connector",
    }
    langfuse, granola = said.index(" langfuse: {"), said.index(" granola: {")
    meet = said.index(" google_meet: {")
    drive = said.index(" google_drive: {")
    calendar = said.index(" google_calendar: {")
    gmail = said.index(" gmail: {")
    github = said.index(" github: {")
    for setting in CONFIG_KEYS:
        assert f'"{setting}"' in said[langfuse:granola], setting
    for setting in SETTINGS:
        assert f'"{setting}"' in said[granola:meet], setting
    for setting in MEET_SETTINGS:
        assert f'"{setting}"' in said[meet:drive], setting
    for setting in DRIVE_SETTINGS:
        assert f'"{setting}"' in said[drive:calendar], setting
    for setting in CALENDAR_SETTINGS:
        assert f'"{setting}"' in said[calendar:gmail], setting
    for setting in GMAIL_SETTINGS:
        assert f'"{setting}"' in said[gmail:], setting
    for setting in GITHUB_SETTINGS:
        assert f'"{setting}"' in said[github:], setting
    assert "optional" in said  # score_name, and granola's settings past key_env
    assert "starts off" in said[granola:meet]  # added off, switched on with source-enable
    assert "starts off" in said[meet:drive]
    assert "service account" in said[meet:drive]  # subject is for one, and refused without one
    assert "starts off" in said[drive:calendar] and "service account" in said[drive:calendar]
    assert "starts off" in said[calendar:gmail]
    assert "service account" in said[calendar:gmail]  # a calendar shared with its address
    assert "starts off" in said[gmail:]
    # a service account needs subject, a refresh token refuses it
    assert "service account must name subject" in said[gmail:]
    assert "refresh token leaves it out" in said[gmail:]
    assert "a label a person made" in said[gmail:]  # never INBOX or a category
    linear = said.index(" linear: {")
    for setting in LINEAR_SETTINGS:
        assert f'"{setting}"' in said[linear:], setting
    assert "starts off" in said[linear:] and "teams or linear_project, at most one" in said[linear:]
    assert "starts off" in said[github:] and "fine-grained token" in said[github:]
