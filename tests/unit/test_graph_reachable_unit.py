"""A project's graph through the real application: GET /api/graph and GET /b/<project>/graph
are registered, behind the same admission as the project's pages over REST and on the web."""

import base64
import hashlib
import json
import logging
import secrets
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.server import create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
ISSUER = "https://id.example.test/pool-graph"
REDIRECT = "http://127.0.0.1:33418/callback"
ALPHA_NOTE = "# Quill runbook\n\nQuill retries 3 times before failing.\n"
BETA_NOTE = "# Lumen plan\n\nLumen waits 5 minutes between tries.\n"
GAMMA_NOTE = "# Mote list\n\nMote checks the queue hourly.\n"


def add(view, project, text: str, source_ref: str) -> None:
    note, _ = view.add_note(project.id, text, source_ref=source_ref)
    assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")


@pytest.fixture
def world(tmp_path):
    """Ada, the owner, with projects alpha and beta, a note compiled in each."""
    store = Store(tmp_path / "cairn.db")
    signed = store.sign_in(
        "sso", "sub-ada", "ada@example.test", "Ada", issuer=ISSUER, join="default"
    )
    ada = store.for_account(signed.account_id, signed.user.id)
    alpha = ada.create_project("alpha")
    beta = ada.create_project("beta")
    add(ada, alpha, ALPHA_NOTE, "desk:alpha-1")
    add(ada, beta, BETA_NOTE, "desk:beta-1")
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(
            store=store,
            client=client,
            ada=ada,
            alpha=alpha,
            beta=beta,
            cookie={"cookie": f"__Host-cairn_session={signed.token}"},
        )
    store.close()


def key(world, label: str, **reach) -> dict:
    return {"x-api-key": world.store.create_key(label, **reach)}


def agent(world) -> dict:
    """An agent Ada approved, as an access token."""
    client = world.store.register_client("laptop", [REDIRECT])
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    code = world.ada.create_code(client.id, REDIRECT, challenge)
    token = world.store.redeem_code(code, client.id).access_token
    return {"authorization": f"Bearer {token}"}


def access_records(caplog) -> list[tuple]:
    return [(r.action, r.project, r.status) for r in caplog.records if r.name == "cairn.access"]


def kinds(graph: dict) -> set[str]:
    return {node["kind"] for node in graph["nodes"]}


# A signed-in owner


def test_a_signed_in_owner_gets_the_page_and_a_key_of_theirs_the_json(world):
    r = world.client.get("/b/alpha/graph", headers=world.cookie)
    assert r.status_code == 200, r.text
    assert "<svg" in r.text and "Graph content" in r.text
    assert "desk:alpha-1" in r.text
    assert 'href="/b/alpha"' in r.text  # the graph page links back to the project's page

    owner_key = {"x-api-key": world.ada.create_key("ada-laptop")}
    r = world.client.get("/api/graph", params={"project": "alpha", "limit": "5"}, headers=owner_key)
    assert r.status_code == 200, r.text
    graph = r.json()
    assert graph["nodes"][0] == {"id": "project:" + world.alpha.id, "kind": "project",
                                 "label": "alpha"}  # fmt: skip
    assert {"page", "claim", "passage", "note"} <= kinds(graph)


def test_the_project_page_links_its_graph_where_its_other_views_are(world):
    r = world.client.get("/b/alpha", headers=world.cookie)
    assert r.status_code == 200
    meta = next(line for line in r.text.splitlines() if "compiled from" in line)
    assert '<a href="/b/alpha/graph">Graph</a>' in meta


# A key's reach


def test_a_key_limited_to_one_project_gets_its_graph_and_nothing_of_another(world):
    limited = key(world, "alpha-only", projects=[world.alpha.id])
    r = world.client.get("/api/graph", params={"project": "alpha"}, headers=limited)
    assert r.status_code == 200
    said = json.dumps(r.json())
    for of_beta in ("beta", "Lumen", "desk:beta-1", world.beta.id):
        assert of_beta not in said, of_beta

    hidden = world.client.get("/api/graph", params={"project": "beta"}, headers=limited)
    assert hidden.status_code == 404
    assert hidden.json() == {"error": "no project 'beta'"}

    # The same name asked before the project exists and once it exists out of reach: the
    # same status and the same body.
    before = world.client.get("/api/graph", params={"project": "gamma"}, headers=limited)
    gamma = world.ada.create_project("gamma")
    add(world.ada, gamma, GAMMA_NOTE, "desk:gamma-1")
    after = world.client.get("/api/graph", params={"project": "gamma"}, headers=limited)
    assert (before.status_code, before.json()) == (after.status_code, after.json())
    assert after.status_code == 404


def test_a_read_only_key_is_served(world):
    reader = key(world, "reader", access="read")
    r = world.client.get("/api/graph", params={"project": "alpha"}, headers=reader)
    assert r.status_code == 200
    assert r.json()["nodes"][0]["label"] == "alpha"


def test_a_revoked_key_is_refused(world):
    revoked = key(world, "gone")
    assert world.client.get("/api/graph?project=alpha", headers=revoked).status_code == 200
    assert world.store.revoke_key("gone")
    r = world.client.get("/api/graph?project=alpha", headers=revoked)
    assert r.status_code == 401
    assert "nodes" not in r.json()


def test_an_agents_token_is_refused_on_the_api_and_no_tool_draws_the_graph(world):
    bearer = agent(world)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"accept": "application/json, text/event-stream", **bearer}
    listed = world.client.post("/mcp", headers=headers, json=body)
    assert listed.status_code == 200  # the token is good on /mcp
    tools = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert not [name for name in tools if "graph" in name]
    r = world.client.get("/api/graph?project=alpha", headers=bearer)
    assert r.status_code == 401


# The query


def test_a_parameter_the_address_does_not_take_is_refused_naming_those_it_takes(world):
    desk = key(world, "desk")
    r = world.client.get("/api/graph?project=alpha&zorp=1", headers=desk)
    assert r.status_code == 400
    assert r.json() == {
        "error": "unknown query parameter 'zorp'; this address takes 'project' and 'limit'"
    }
    for limit in ("0", "21", "abc"):
        r = world.client.get(f"/api/graph?project=alpha&limit={limit}", headers=desk)
        assert r.status_code == 400, limit


def test_the_page_ignores_what_it_does_not_take_and_refuses_a_limit_out_of_range(world):
    r = world.client.get("/b/alpha/graph?zorp=1", headers=world.cookie)
    assert r.status_code == 200
    assert world.client.get("/b/alpha/graph?limit=21", headers=world.cookie).status_code == 400


# Signed out


def test_a_signed_out_browser_is_sent_to_sign_in_as_for_any_project_page(world):
    project = world.client.get("/b/alpha", follow_redirects=False)
    graph = world.client.get("/b/alpha/graph", follow_redirects=False)
    assert graph.status_code == project.status_code == 303
    assert project.headers["location"].startswith("/login")
    expected = project.headers["location"].replace("%2Fb%2Falpha", "%2Fb%2Falpha%2Fgraph")
    assert graph.headers["location"] == expected


# The access log


def test_each_request_is_one_access_record_named_graph(world, caplog):
    desk = key(world, "desk")
    limited = key(world, "alpha-only", projects=[world.alpha.id])
    caplog.set_level(logging.INFO, logger="cairn.access")
    world.client.get("/api/graph?project=alpha", headers=desk)
    world.client.get("/b/alpha/graph", headers=world.cookie)
    world.client.get("/api/graph?project=beta", headers=limited)
    world.client.get("/api/graph?project=alpha&limit=0", headers=desk)
    assert access_records(caplog) == [
        ("graph", "alpha", 200),
        ("graph", "alpha", 200),
        ("graph", None, 404),
        ("graph", None, 400),
    ]
