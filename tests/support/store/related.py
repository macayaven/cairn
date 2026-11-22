"""Shared test helpers extracted from test_related_unit."""

import json
import random
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.server import create_app
from cairn.store import Store
from cairn.web.mcp import build_mcp
from support.store.title_masking import flush_class

BASE = "http://127.0.0.1:8765"

MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}

WRITTEN = datetime(2099, 2, 3, 4, 5, tzinfo=UTC)

WHEN = "2099-02-03T04:05:00.000000+00:00"


def rpc(client, key: str, method: str, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    r = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def tool(client, key: str, name: str, /, **arguments) -> dict:
    return rpc(client, key, "tools/call", {"name": name, "arguments": arguments})["result"]


def answer(client, key: str, name: str, /, **arguments) -> dict:
    result = tool(client, key, name, **arguments)
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def refusal(client, key: str, name: str, /, **arguments) -> str:
    result = tool(client, key, name, **arguments)
    assert result["isError"] is True, result
    return result["content"][0]["text"]


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "cairn.db", clock=lambda: WRITTEN)
    ops = store.create_project("ops")
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, key=key, h={"x-api-key": key}, ops=ops)
    store.close()


def note(env, title: str, text: str, project: str = "ops") -> dict:
    return answer(env.client, env.key, "note", project=project, text=text, title=title, topic=title)


def rest(env, slug: str, query: str = "", project: str = "ops"):
    return env.client.get(f"/api/projects/{project}/pages/{slug}/related{query}", headers=env.h)


@pytest.fixture
def world(env):
    """Deploy states a flag that Retention states too, and Retention's claim names Deploy."""
    deploy = note(env, "Deploy", "Run the job with `--audit-keep` set to 7.\n")
    retention = note(env, "Retention", "`--audit-keep` says how many; see Deploy.\n")
    note(env, "Queue", "Brook drains the queue at noon.\n")
    return SimpleNamespace(deploy=deploy, retention=retention)


def tools() -> dict:
    import asyncio

    return {tool.name: tool for tool in asyncio.run(build_mcp().list_tools())}


def mentioned(env, focus: str) -> dict[str, tuple[bool, list[str]]]:
    """Whether each surface's answer is complete, and the pages it says mention `focus`."""
    response = rest(env, focus, "?limit=20")
    assert response.status_code == 200
    answers = {
        "Memory": Memory(env.store, lint_gate).related("ops", focus, 20),
        "REST": response.json(),
        "MCP": answer(env.client, env.key, "related", project="ops", slug=focus, limit=20),
    }
    return {
        surface: (
            found["searched"]["complete"],
            sorted(
                page["page"]
                for page in found["related"]
                if any(why["kind"] == "mentioned_by" for why in page["why"])
            ),
        )
        for surface, found in answers.items()
    }


FLUSH = [
    *random.Random(12).sample(flush_class(), 40),
    ("Cafe\u0301 Beta", "Read Cafe\u0301/_\u0301Beta today."),
]

NINE = [
    "projects",
    "new_project",
    "note",
    "recall",
    "page",
    "evidence",
    "overview",
    "graph",
    "related",
]

RELATED = (
    "To see what else memory holds on a page's subject, call `related` with the page: it gives"
    " the pages of its project that state one of its terms (a path, a flag, a URL, a version,"
    " code) or name its title, and every relation says why, with the claims it rests on."
)
