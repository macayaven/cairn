"""Unit tests for `serve --trusted-proxy`: who may say which client a request came from.

Each test runs `serve` with uvicorn stubbed. Inside the stub, while `serve` holds its store
open, the app is loaded the way uvicorn would with the options `serve` gave it
(`uvicorn.Config.load`, which adds uvicorn's own proxy-header middleware), and requests are
sent from a chosen peer address through httpx's ASGI transport. The per-client limit on agent
registration shows which address a request was counted under.
"""

import asyncio
from collections.abc import Callable

import httpx
import pytest
import uvicorn

from cairn import oauth, server
from cairn.store import Store

BASE = "https://cairn.example.test"
CLIENT = {"client_name": "proxy-agent", "redirect_uris": ["http://127.0.0.1:33418/callback"]}
CADDY = "172.30.0.3"  # a peer inside the trusted network
FULL = oauth.REGISTRATIONS  # registrations one address may make in the window


def serve(tmp_path, monkeypatch, *extra: str, then: Callable[[object, dict], object]) -> object:
    """Runs `serve` with these arguments; `then(app as uvicorn loads it, options)` runs where
    uvicorn would, and its answer is returned."""
    answers = []

    def run(app, **options):
        config = uvicorn.Config(app, log_config=None, **options)
        config.load()
        answers.append(then(config.loaded_app, options))

    monkeypatch.setattr(uvicorn, "run", run)
    argv = ["serve", "--db", str(tmp_path / "cairn.db"), "--public-url", BASE, *extra]
    assert server.main(argv) == 0
    return answers[0]


def statuses(app, sends: list[tuple[str, str | None]]) -> list[int]:
    """POST /register once per (peer, X-Forwarded-For or None), in order."""

    async def run() -> list[int]:
        out = []
        for peer, forwarded in sends:
            transport = httpx.ASGITransport(app=app, client=(peer, 40000))
            headers = {"x-forwarded-for": forwarded} if forwarded else {}
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                out.append(
                    (await client.post("/register", json=CLIENT, headers=headers)).status_code
                )
        return out

    return asyncio.run(run())


def test_without_the_option_no_peer_is_trusted_not_even_loopback(tmp_path, monkeypatch):
    def then(app, options):
        assert options["proxy_headers"] is False and options["forwarded_allow_ips"] == []
        sends = [("127.0.0.1", f"203.0.113.{n}") for n in range(FULL + 1)]  # each claims another
        return statuses(app, sends)[-2:]

    assert serve(tmp_path, monkeypatch, then=then) == [201, 429]  # one bucket: the peer's


def test_a_trusted_proxy_names_the_client_and_an_untrusted_peer_cannot(tmp_path, monkeypatch):
    def then(app, options):
        assert options["proxy_headers"] is True
        assert options["forwarded_allow_ips"] == ["172.30.0.0/24", "10.9.8.7"]
        # Two clients behind the trusted proxies: a bucket each.
        assert statuses(app, [(CADDY, "203.0.113.5")] * (FULL + 1))[-2:] == [201, 429]
        assert statuses(app, [(CADDY, "203.0.113.6"), ("10.9.8.7", "203.0.113.7")]) == [201, 201]
        # An untrusted peer's header moves nothing: it is counted as itself, whatever it says.
        claims = [("198.51.100.7", f"203.0.113.{100 + n}") for n in range(FULL + 1)]
        return statuses(app, claims)[-2:]

    trusted = ["--trusted-proxy", "172.30.0.0/24", "--trusted-proxy", "10.9.8.7"]
    assert serve(tmp_path, monkeypatch, *trusted, then=then) == [201, 429]


def test_the_session_cookie_stays_secure_whatever_a_trusted_proxy_says(tmp_path, monkeypatch):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    store.close()

    def then(app, options):
        async def run() -> str:
            transport = httpx.ASGITransport(app=app, client=(CADDY, 40000))
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                headers = {"x-forwarded-proto": "http", "x-forwarded-for": "203.0.113.5"}
                signed = await client.post(
                    "/login",
                    data={"key": key},
                    headers={**headers, "sec-fetch-site": "same-origin"},
                )
                return signed.headers["set-cookie"]

        return asyncio.run(run())

    cookie = serve(tmp_path, monkeypatch, "--trusted-proxy", "172.30.0.0/24", then=then)
    assert "; secure" in cookie.lower()  # it follows --public-url, not the forwarded scheme


@pytest.mark.parametrize("value", ["*", "caddy", "172.30.0.1/24", "172.30.0.0/33", ""])
def test_something_that_is_neither_an_address_nor_a_network_stops_serve(
    tmp_path, monkeypatch, capsys, value
):
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    argv = ["serve", "--db", str(tmp_path / "cairn.db"), "--trusted-proxy", value]
    assert server.main(argv) == 2
    assert "cannot trust that proxy" in capsys.readouterr().err


@pytest.mark.parametrize("trusted", [[], ["--trusted-proxy", "172.30.0.0/24"]])
def test_forwarded_allow_ips_in_the_environment_trusts_no_one(tmp_path, monkeypatch, trusted):
    """uvicorn alone would read FORWARDED_ALLOW_IPS; serve never lets it: a peer that is not
    named by --trusted-proxy is counted as itself whatever it claims, loopback included."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")

    def then(app, options):
        peer = "198.51.100.7" if trusted else "127.0.0.1"
        claims = [(peer, f"203.0.113.{n}") for n in range(FULL + 1)]  # each claims another
        return statuses(app, claims)[-2:]

    assert serve(tmp_path, monkeypatch, *trusted, then=then) == [201, 429]  # one bucket


def test_the_sign_in_cookie_stays_secure_whatever_a_trusted_proxy_says(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_ID", "gh-id-from-env")
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_SECRET", "gh-secret-from-env")

    def then(app, options):
        async def run() -> str:
            transport = httpx.ASGITransport(app=app, client=(CADDY, 40000))
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                headers = {"x-forwarded-proto": "http", "x-forwarded-for": "203.0.113.5"}
                started = await client.get("/auth/github", headers=headers)
                assert started.status_code == 302
                cookies = started.headers.get_list("set-cookie")
                return next(cookie for cookie in cookies if "cairn_signin=" in cookie)

        return asyncio.run(run())

    argv = ["--trusted-proxy", "172.30.0.0/24", "--sign-in", "github"]
    cookie = serve(tmp_path, monkeypatch, *argv, then=then)
    assert "; secure" in cookie.lower()  # it follows --public-url, not the forwarded scheme
