"""Where the rule for a project's name is stated before a name is refused, it is the whole rule:
a name beginning with "br_" is refused because ids begin that way."""

from pathlib import Path

from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "cairn" / "templates"


def test_the_new_project_form_says_a_name_cannot_begin_like_an_id():
    hint = (TEMPLATES / "new_project.html").read_text(encoding="utf-8")
    assert "br_" in hint.split('id="project-name-hint"', 1)[1].split("</p>", 1)[0]


def test_the_new_project_tool_says_it_too_and_the_refusal_agrees(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    headers = {"x-api-key": key, "accept": "application/json, text/event-stream"}
    try:
        with TestClient(create_app(store), base_url="http://127.0.0.1:8765") as client:
            listed = client.post(
                "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            )
            tools = {tool["name"]: tool for tool in listed.json()["result"]["tools"]}
            assert "br_" in tools["new_project"]["description"]
            refused = client.post("/api/projects", headers=headers, json={"name": "br_payments"})
            assert refused.status_code == 400 and "br_" in refused.json()["error"]
    finally:
        store.close()
