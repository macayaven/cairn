"""A tool call that fails leaves what the caller sent out of the server's log. The MCP SDK
logs each failed tool call with the error's text ("Tool 'note' failed: ... no project
'<name>'"), and that text quotes what the caller sent: a project's name, a page's slug. The
access log already records which tool ran, for whom and with which status, so the SDK's
logger is held at WARNING by the server, as the HTTP clients' loggers are: only an
unexpected exception in a tool is still written by it.

The app runs in process, as the README's install starts it (no public URL), over a real
store in a temporary file; every logger is read at DEBUG."""

import logging

import pytest
from starlette.testclient import TestClient

from cairn.memory import ACCESS_LOGGER
from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
# A project name that occurs nowhere by chance: no fixture, no code, no message of the SDK.
CANARY = "vexillum-" + "quorra-" + "5e81c0"


def said(record: logging.LogRecord) -> str:
    """Everything a record could print: its message, its arguments and its exception."""
    try:
        message = record.getMessage()
    except Exception:  # a record whose arguments do not fit its format still holds them
        message = f"{record.msg} {record.args!r}"
    return f"{message} {record.exc_text or ''} {record.exc_info!r}"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("note", {"project": CANARY, "text": "# Rollback\n\n- Brook owns it.\n"}),
        ("overview", {"project": CANARY}),
        ("page", {"project": CANARY, "slug": "rollback"}),
    ],
)
def test_a_failed_tool_call_leaves_what_the_caller_sent_in_no_log_record(
    store, caplog, tool, arguments
):
    key = store.create_key("laptop")
    caplog.set_level(logging.DEBUG)
    with TestClient(create_app(store), base_url=BASE) as client:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        answer = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    result = answer.json()["result"]
    assert answer.status_code == 200 and result["isError"] is True
    assert CANARY in result["content"][0]["text"]  # the caller is told; the log is not
    assert [r for r in caplog.records if CANARY in said(r)] == []
    access = [(r.action, r.status) for r in caplog.records if r.name == ACCESS_LOGGER]
    assert access == [(tool, 404)]
