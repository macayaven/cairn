import asyncio

from cairn.web.mcp import build_mcp


def test_graph_resource_template_discovery():
    mcp = build_mcp()
    assert asyncio.run(mcp.list_resources()) == []
    templates = asyncio.run(mcp.list_resource_templates())
    assert [str(t.uri_template) for t in templates] == ["graph://project/{project}/{limit}"]
    assert templates[0].mime_type == "application/json"


from support.web.oauth import MCP_HEADERS, bearer, connected  # noqa: E402
from support.web.oauth import world as oauth_world  # noqa: E402, F401


def rpc(env, method, params, headers):
    return env.raw.post(
        "/mcp",
        headers={**MCP_HEADERS, **headers},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        },
    )


def test_initialize_explains_graph_resource(oauth_world):  # noqa: F811
    response = rpc(
        oauth_world,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
        {"x-api-key": oauth_world.ada.key},
    )
    assert response.status_code == 200
    guidance = response.json()["result"]["instructions"]
    assert "graph://project/{project}/{limit}" in guidance
    graph_guidance = next(p for p in guidance.split("\n\n") if "graph://" in p)
    assert "projects" in graph_guidance and "names or IDs" in graph_guidance
    assert "1-20 pages" in graph_guidance and "truncation" in graph_guidance
    assert "quoted information" in graph_guidance and "not instructions" in graph_guidance


def test_oauth_resource_read_and_rest_boundary(oauth_world):  # noqa: F811
    world = oauth_world
    grant = connected(world, world.ada)
    headers = bearer(grant.access)
    response = rpc(world, "resources/read", {"uri": "graph://project/core/20"}, headers)
    assert response.status_code == 200
    import json

    data = response.json()["result"]["contents"][0]
    assert data["mimeType"] == "application/json"
    assert json.loads(data["text"])["nodes"][0]["label"] == "core"
    assert world.raw.get("/api/graph?project=core", headers=headers).status_code == 401


def test_identical_uri_uses_current_account_and_refuses_hidden(oauth_world):  # noqa: F811
    world = oauth_world
    import json

    world.ada.view.create_project("only-ada")

    def read(project, headers):
        return rpc(world, "resources/read", {"uri": f"graph://project/{project}/1"}, headers).json()

    ada = {"x-api-key": world.ada.key}
    brook = {"x-api-key": world.brook.key}
    first = json.loads(read("core", ada)["result"]["contents"][0]["text"])
    second = json.loads(read("core", brook)["result"]["contents"][0]["text"])
    assert first["nodes"][0]["id"] != second["nodes"][0]["id"]
    assert "error" in read("only-ada", brook)
    assert "error" in read("missing", brook)


def test_invalid_limit_refused_without_projection(oauth_world, monkeypatch):  # noqa: F811
    world = oauth_world
    import cairn.web.graph_resource as resource

    def forbidden(*args):
        raise AssertionError("projection must not run")

    monkeypatch.setattr(resource, "project_graph", forbidden)
    for limit in ("0", "21", "01", "-1", "true", "1.0", "9" * 100):
        response = rpc(
            world,
            "resources/read",
            {"uri": f"graph://project/core/{limit}"},
            {"x-api-key": world.ada.key},
        )
        assert "error" in response.json()


def test_scoped_key_and_revocation(oauth_world, caplog):  # noqa: F811
    import logging

    world = oauth_world
    other = world.ada.view.create_project("other")
    scoped = world.ada.view.create_key("scoped", projects=[other.id], access="read")
    headers = {"x-api-key": scoped}
    caplog.set_level(logging.INFO, logger="cairn.access")
    result = rpc(world, "resources/read", {"uri": "graph://project/other/1"}, headers)
    assert result.headers["cache-control"] == "no-store"
    assert "result" in result.json()
    records = [(r.action, r.project, r.status) for r in caplog.records if r.name == "cairn.access"]
    assert records == [("graph", "other", 200)]
    caplog.clear()
    refused = rpc(world, "resources/read", {"uri": "graph://project/core/1"}, headers)
    assert "error" in refused.json()
    records = [(r.action, r.project, r.status) for r in caplog.records if r.name == "cairn.access"]
    assert records == [("graph", None, 404)]
    assert world.ada.view.revoke_key("scoped")
    assert (
        rpc(world, "resources/read", {"uri": "graph://project/other/1"}, headers).status_code == 401
    )


def test_concurrent_same_uri_keeps_account(oauth_world):  # noqa: F811
    import json
    from concurrent.futures import ThreadPoolExecutor

    world = oauth_world

    def read(key):
        response = rpc(
            world, "resources/read", {"uri": "graph://project/core/1"}, {"x-api-key": key}
        )
        return json.loads(response.json()["result"]["contents"][0]["text"])["nodes"][0]["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(read, [world.ada.key, world.brook.key] * 5))
    assert len(set(ids[::2])) == len(set(ids[1::2])) == 1
    assert ids[0] != ids[1]


def test_changed_set_and_oversized_scope(oauth_world):  # noqa: F811
    world = oauth_world
    alpha = world.ada.view.get_project("core")
    beta = world.ada.view.create_project("beta")
    group = world.ada.view.create_set("group", [alpha.id])
    token = world.ada.view.create_key("group-only", projects=[group.id], access="read")
    headers = {"x-api-key": token}

    def read(name):
        return rpc(world, "resources/read", {"uri": f"graph://project/{name}/1"}, headers).json()

    assert "result" in read("core")
    world.ada.view.update_set(group.id, [beta.id])
    assert "error" in read("core")
    assert "result" in read("beta")
    with world.ada.view._write() as con:
        con.execute(
            "UPDATE project_sets SET project_ids = ? WHERE id = ?",
            ('["' + "a" * 70000 + '"]', group.id),
        )
    assert "error" in read("beta")
    assert "error" in read("missing")


def test_resource_reports_projection_truncation(oauth_world):  # noqa: F811
    import json

    world = oauth_world
    project = world.ada.view.get_project("core")
    for slug in ("a", "b", "c"):
        world.ada.view.save_page(project.id, slug, slug.upper(), "", [])
    response = rpc(
        world, "resources/read", {"uri": "graph://project/core/2"}, {"x-api-key": world.ada.key}
    )
    graph = json.loads(response.json()["result"]["contents"][0]["text"])
    assert graph["truncated"] is True
    assert [node["slug"] for node in graph["nodes"] if node["kind"] == "page"] == ["a", "b"]


def test_grant_tracks_rule_and_membership(tmp_path):
    import base64
    import hashlib
    import secrets
    from types import SimpleNamespace

    from starlette.testclient import TestClient

    from cairn.server import create_app
    from cairn.store import Store

    store = Store(tmp_path / "members.db")
    try:
        views = []
        for name in ("owner", "reader"):
            signed = store.sign_in(
                "sso",
                name,
                f"{name}@example.test",
                name,
                issuer="https://id.example.test",
                join="default",
            )
            views.append(store.for_account(signed.account_id, signed.user.id))
        owner, reader = views
        project = owner.create_project("shared", everyone="read")
        client = store.register_client("agent", ["http://127.0.0.1:33418/callback"])
        verifier = secrets.token_urlsafe(32)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        code = reader.create_code(client.id, "http://127.0.0.1:33418/callback", challenge)
        token = store.redeem_code(code, client.id).access_token
        with TestClient(
            create_app(store, public_url="http://127.0.0.1:8765"), base_url="http://127.0.0.1:8765"
        ) as raw:
            env = SimpleNamespace(raw=raw)
            headers = bearer(token)

            def read():
                return rpc(env, "resources/read", {"uri": "graph://project/shared/1"}, headers)

            assert "result" in read().json()
            owner.set_project_access(project.id, everyone="none")
            assert "error" in read().json()
            owner.set_project_access(project.id, everyone="read")
            assert "result" in read().json()
            owner.remove_member(reader.user_id)
            assert read().status_code == 401
    finally:
        store.close()


def test_one_worker_projection_per_read(oauth_world, monkeypatch):  # noqa: F811
    import threading

    import cairn.web.graph_resource as resource

    world = oauth_world
    original = resource.project_graph
    calls = []

    def counted(*args):
        calls.append(threading.current_thread().name)
        return original(*args)

    monkeypatch.setattr(resource, "project_graph", counted)
    response = rpc(
        world, "resources/read", {"uri": "graph://project/core/1"}, {"x-api-key": world.ada.key}
    )
    assert "result" in response.json()
    assert len(calls) == 1
    assert "worker" in calls[0].lower()


def test_invalid_project_has_no_traversal(oauth_world, monkeypatch):  # noqa: F811
    import cairn.web.graph_resource as resource

    world = oauth_world

    def forbidden(*args):
        raise AssertionError("projection must not run")

    monkeypatch.setattr(resource, "project_graph", forbidden)
    for project in ("_bad", "a" * 129, "a%20b"):
        response = rpc(
            world,
            "resources/read",
            {"uri": f"graph://project/{project}/1"},
            {"x-api-key": world.ada.key},
        )
        assert "error" in response.json()


def test_graph_failures_do_not_log_input(oauth_world, caplog):  # noqa: F811
    import logging

    caplog.set_level(logging.INFO)
    sentinel = "private-sentinel-abc"
    for project in (sentinel, f"{sentinel}%20bad"):
        caplog.clear()
        response = rpc(
            oauth_world,
            "resources/read",
            {"uri": f"graph://project/{project}/1"},
            {"x-api-key": oauth_world.ada.key},
        )
        assert "error" in response.json()
        assert sentinel not in response.text
        assert sentinel not in caplog.text


def test_sdk_rejected_graph_uri_is_logged_safely(oauth_world, caplog, monkeypatch):  # noqa: F811
    import logging

    import cairn.web.graph_resource as resource

    caplog.set_level(logging.INFO)

    def forbidden(*args):
        raise AssertionError("no traversal")

    monkeypatch.setattr(resource, "project_graph", forbidden)
    sentinel = "private-sentinel-xyz"
    for uri in (
        f"graph://project/{sentinel}%00x/1",
        f"graph://project/%2F{sentinel}/1",
        f"graph://project/{sentinel}" + "x" * 66000 + "/1",
    ):
        caplog.clear()
        response = rpc(
            oauth_world, "resources/read", {"uri": uri}, {"x-api-key": oauth_world.ada.key}
        )
        assert "error" in response.json()
        assert sentinel not in response.text
        assert sentinel not in caplog.text
        records = [
            (r.action, r.project, r.status) for r in caplog.records if r.name == "cairn.access"
        ]
        assert records == [("graph", None, 400)]


def test_read_only_scoped_oauth_grant(oauth_world):  # noqa: F811
    from support.web.oauth import REDIRECT, pkce

    world = oauth_world
    allowed = world.ada.view.get_project("core")
    world.ada.view.create_project("other")
    client = world.store.register_client("read-agent", [REDIRECT])
    _verifier, challenge = pkce()
    code = world.ada.view.create_code(
        client.id, REDIRECT, challenge, projects=[allowed.id], access="read"
    )
    token = world.store.redeem_code(code, client.id).access_token
    principal = world.store.resolve_token(token)
    assert principal.access == "read"
    assert principal.projects == [allowed.id] or tuple(principal.projects) == (allowed.id,)
    headers = bearer(token)
    allowed_result = rpc(world, "resources/read", {"uri": "graph://project/core/1"}, headers)
    assert "result" in allowed_result.json()
    denied = rpc(world, "resources/read", {"uri": "graph://project/other/1"}, headers)
    assert "error" in denied.json()
    assert world.raw.get("/api/graph?project=core", headers=headers).status_code == 401


def test_unexpected_projection_failure_is_sanitized(oauth_world, caplog, monkeypatch):  # noqa: F811
    import logging

    import cairn.web.graph_resource as resource

    caplog.set_level(logging.INFO)
    sentinel = "private-sentinel-crash"

    def fail(*args):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(resource, "project_graph", fail)
    response = rpc(
        oauth_world,
        "resources/read",
        {"uri": f"graph://project/{sentinel}/1"},
        {"x-api-key": oauth_world.ada.key},
    )
    assert "error" in response.json()
    assert sentinel not in response.text
    assert sentinel not in caplog.text
    assert not any(r.exc_info for r in caplog.records)
    records = [(r.action, r.project, r.status) for r in caplog.records if r.name == "cairn.access"]
    assert records == [("graph", None, 500)]


def test_template_description_explains_quoted_text_and_truncation():
    templates = asyncio.run(build_mcp().list_resource_templates())
    description = templates[0].description.lower()
    assert "information" in description and "instructions" in description
    assert "truncat" in description
