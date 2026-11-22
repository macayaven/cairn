"""Unit tests for Cache-Control on what machines are answered (H1, item 3; the acceptance test
is tests/acceptance/test_h1_hardening.py): Auth sets `no-store` on every answer on /api and
/mcp, replacing whatever a route said, and leaves every other address as its route made it."""

import asyncio

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from cairn.store import Store
from cairn.web import auth
from cairn.web.app import create_app

BASE = "http://127.0.0.1:8765"


def started(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """The headers of the response start `auth._no_store` passes on."""
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def both():
        wrapped = auth._no_store(send)
        await wrapped({"type": "http.response.start", "status": 200, "headers": headers})
        await wrapped({"type": "http.response.body", "body": b"x"})

    asyncio.run(both())
    assert sent[1] == {"type": "http.response.body", "body": b"x"}  # the body as it was
    return sent[0]["headers"]


def test_no_store_replaces_what_a_route_said_in_any_case():
    headers = [
        (b"content-type", b"application/json"),
        (b"Cache-Control", b"public, max-age=600"),
        (b"cache-control", b"immutable"),
    ]
    assert started(headers) == [
        (b"content-type", b"application/json"),
        (b"cache-control", b"no-store"),
    ]
    assert started([]) == [(b"cache-control", b"no-store")]


def kept(request: Request) -> Response:
    """A route that says its answer may be kept for ten minutes."""
    return Response(
        "{}", media_type="application/json", headers={"cache-control": "public, max-age=600"}
    )


def test_auth_sets_it_on_machine_addresses_only(tmp_path):
    """A route under /api that says it may be kept is still answered no-store; a file under
    /static/ keeps what its route says."""
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("ops")
    try:
        app = create_app(store, public_url=BASE)
        app.router.routes.insert(0, Route("/api/kept", kept))
        with TestClient(app, base_url=BASE) as client:
            answer = client.get("/api/kept", headers={"x-api-key": key})
            assert answer.status_code == 200
            assert answer.headers.get_list("cache-control") == ["no-store"]
            icon = client.get("/static/cairn.svg")
            assert icon.status_code == 200 and "no-store" not in icon.headers.get_list(
                "cache-control"
            )
    finally:
        store.close()
