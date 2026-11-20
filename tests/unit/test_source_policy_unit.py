"""Every built-in source enforces the same transport and response bounds."""

import ast
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

import cairn.sources
from cairn.sources import KINDS, SourceError, connector, linear, run_source, source_client
from cairn.store import Store

NOW = datetime(2026, 11, 19, tzinfo=UTC)
BASE = "https://source.example.test/feed"
GOOGLE = {"credentials_env": "SOURCE_CRED"}
SETTINGS = {
    "connector": {"url": BASE, "secret_env": "SOURCE_KEY"},
    "github": {"token_env": "SOURCE_KEY", "repo": "paperwork/ledger"},
    "gmail": {**GOOGLE, "label": "To file"},
    "google_calendar": GOOGLE,
    "google_drive": {**GOOGLE, "folder_id": "1FolderAbCdEfGh"},
    "google_meet": GOOGLE,
    "granola": {"key_env": "SOURCE_KEY"},
    "langfuse": {"public_key_env": "SOURCE_ID", "secret_key_env": "SOURCE_KEY"},
    "linear": {"key_env": "SOURCE_KEY"},
}
ENV = {
    "SOURCE_KEY": "synthetic-source-key",
    "SOURCE_ID": "synthetic-source-id",
    "SOURCE_CRED": json.dumps(
        {
            "type": "authorized_user",
            "client_id": "unit.apps.example.test",
            "client_secret": "synthetic-client-secret",
            "refresh_token": "synthetic-refresh-token",
        }
    ),
}


def config_for(kind):
    return {
        "projects": ["ledger"],
        ("url" if kind == "connector" else "base_url"): BASE,
        **SETTINGS[kind],
    }


def test_policy_cases_cover_every_kind():
    assert KINDS.keys() == SETTINGS.keys()


@pytest.mark.parametrize("kind", SETTINGS)
def test_every_source_refuses_an_invalid_port(kind):
    config = config_for(kind)
    field = "url" if kind == "connector" else "base_url"
    config[field] = "https://metadata.example.test:invalid/feed"
    with pytest.raises(ValueError, match=rf"^{field} is not a URL$"):
        KINDS[kind].check(config)


@pytest.mark.parametrize("kind", SETTINGS)
def test_every_source_refuses_the_same_malformed_projects(kind):
    config = {**config_for(kind), "projects": {"ledger": True}}
    with pytest.raises(ValueError, match=r"^projects (is a list|lists the names)"):
        KINDS[kind].check(config)


class LongAnswer(httpx.AsyncByteStream):
    async def __aiter__(self):
        chunk = b" " * (1024 * 1024)
        for _ in range(12):
            yield chunk


@pytest.mark.parametrize("kind", SETTINGS)
@pytest.mark.parametrize("declared", [True, False])
def test_every_source_refuses_the_same_overlong_answer(kind, declared):
    def upstream(request):
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "synthetic-access"})
        if declared:
            return httpx.Response(200, headers={"content-length": "13000000"})
        return httpx.Response(200, stream=LongAnswer())

    with pytest.raises(SourceError, match=r"larger|too large"):
        pull_source(kind, upstream)


def pull_source(kind, upstream):
    async def pull():
        async with source_client(httpx.MockTransport(upstream)) as client:
            kind_spec = KINDS[kind]
            source = kind_spec.open(
                "ledger", kind_spec.check(config_for(kind)), http=client, env=ENV,
                clock=lambda: NOW, since=NOW - timedelta(days=1),
            )  # fmt: skip
            return await source.pull(None)

    return asyncio.run(pull())


def test_connector_ignores_the_sentence_in_an_error_declared_over_the_cap():
    def upstream(request):
        return httpx.Response(
            400, headers={"content-length": "100000"}, json={"error": "Try another filter."}
        )

    async def request():
        async with source_client(httpx.MockTransport(upstream)) as client:
            return await connector.ask(
                client, BASE, ENV["SOURCE_KEY"], {}, deadline=None, clock=lambda: NOW
            )

    reply = asyncio.run(request())
    assert reply.status == 400
    assert reply.error is None
    assert reply.data == b""


@pytest.mark.parametrize(
    ("kind", "status", "body", "marker"),
    [
        (
            "granola",
            403,
            {"code": "SUBSCRIPTION_INACTIVE"},
            "the workspace's subscription is not active",
        ),
        (
            "linear",
            400,
            {"errors": [{"message": "Try another filter."}]},
            "Try another filter.",
        ),
    ],
)
@pytest.mark.parametrize("oversized", [False, True])
def test_error_fields_are_used_only_within_the_declared_bound(
    kind, status, body, marker, oversized
):
    def upstream(request):
        headers = {"content-length": "100000"} if oversized else None
        return httpx.Response(status, headers=headers, json=body)

    with pytest.raises(SourceError) as failed:
        pull_source(kind, upstream)
    displayed = marker in str(failed.value)
    assert displayed is (not oversized)


def test_request_errors_do_not_quote_other_credential_shapes_from_the_provider():
    message = "Authorization: Bearer " + "made-up-response-value"
    assert linear.said_by_linear([{"message": message}], ()) == ""


def test_source_modules_define_the_shared_checks_once():
    source_dir = Path(cairn.sources.__file__).parent
    check_owners = {
        "check_projects": "config.py",
        "check_env_name": "config.py",
        "read_bounded": "http_policy.py",
        "request_failure": "http_policy.py",
        "safe_error_text": "http_policy.py",
        "check_transport": "url.py",
    }
    found = {name: [] for name in check_owners}
    for path in sorted(source_dir.glob("*.py")):
        syntax = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(syntax):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name not in {"_check_projects", "_bounded", "_check_token_env"}, (
                    path.name
                )
                if node.name in found:
                    found[node.name].append(path.name)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "aiter_bytes"
            ):
                assert path.name == "http_policy.py"
    assert found == {name: [owner] for name, owner in check_owners.items()}


def test_the_rule_for_an_environment_variables_name_is_written_once():
    """The first review's 11.2: config.py holds the check of a variable's name (key_env,
    token_env and their like); no source module writes the pattern again."""
    source_dir = Path(cairn.sources.__file__).parent
    written = [
        path.name
        for path in sorted(source_dir.glob("*.py"))
        if "[A-Z_][A-Z0-9_]{0,127}" in path.read_text(encoding="utf-8")
    ]
    assert written == ["config.py"]


def test_every_connector_checks_env_names_through_config():
    source_dir = Path(cairn.sources.__file__).parent
    connectors = (
        "connector.py",
        "github.py",
        "gmail.py",
        "google_calendar.py",
        "google_drive.py",
        "google_meet.py",
        "granola.py",
        "langfuse.py",
        "linear.py",
    )
    for name in connectors:
        syntax = ast.parse((source_dir / name).read_text(encoding="utf-8"))
        defs = {
            node.name
            for node in ast.walk(syntax)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "_check_variable" not in defs, name
        calls = [node for node in ast.walk(syntax) if isinstance(node, ast.Call)]
        fullmatch_env = [
            call
            for call in calls
            if isinstance(call.func, ast.Attribute)
            and call.func.attr == "fullmatch"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "ENV_NAME"
        ]
        assert not fullmatch_env, name
        called_check = any(
            isinstance(call.func, ast.Name) and call.func.id == "check_env_name" for call in calls
        )
        assert called_check, name


def test_connector_sync_keeps_its_report_and_request_counts():
    requested = []

    def upstream(request):
        requested.append((request.method, request.url.path))
        return httpx.Response(
            200, json={
                "items": [{"id": "one", "title": "Gauge", "text": "# Gauge\n\nPressure is 4 bar."}],
                "protocol": 1, "more": False, "cursor": "one",
            },
        )  # fmt: skip

    async def sync(store):
        async with source_client(httpx.MockTransport(upstream)) as client:
            return await run_source(store, "ledger", http=client, env=ENV, clock=lambda: NOW)

    statements = []
    store = Store(":memory:")
    try:
        store.create_project("ledger")
        store.add_source("ledger", "connector", config_for("connector"))
        store.enable_source("ledger", ["ledger"])

        # An in-memory store shares one connection. SQLite labels statements it
        # generates internally with a comment, and the engine's count can vary.
        def trace(statement):
            if not statement.startswith("--"):
                statements.append(None)

        store._con.set_trace_callback(trace)
        try:
            report = asyncio.run(sync(store))
        finally:
            store._con.set_trace_callback(None)
        assert len(statements) == 106
        assert (report.saved, report.skipped, report.unchanged) == (1, 0, 0)
        assert len(store.list_notes(store.get_project("ledger").id)) == 1
    finally:
        store.close()
    assert requested == [("POST", "/feed")]
