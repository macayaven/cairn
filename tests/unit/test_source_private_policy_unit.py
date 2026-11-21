"""The source client authorizes the address that it hands to HTTPX."""

import asyncio
import re
from datetime import UTC, datetime

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.server import create_app, main
from cairn.sources import SourceError, run_source, source_client
from cairn.store import Store

SETTING = "CAIRN_SOURCE_PRIVATE_ADDRESSES"
NOW = datetime(2026, 11, 19, tzinfo=UTC)
PRIVATE_REFUSAL = (
    "To read a source on a non-global address, set CAIRN_SOURCE_PRIVATE_ADDRESSES=allow."
)


@pytest.mark.parametrize("value", ["10.0.0.5", "100.64.0.2", "127.0.0.1", "fd12::1", "::1"])
@pytest.mark.parametrize("literal", [True, False])
def test_non_global_addresses_need_the_operator_setting(monkeypatch, value, literal):
    def stand_in(request):
        return httpx.Response(200)

    async def resolve(host, port):
        return [value]

    host = f"[{value}]" if ":" in value else value
    url = f"https://{host if literal else 'source.example.test'}/feed"

    async def get():
        async with source_client(httpx.MockTransport(stand_in), resolve=resolve) as client:
            return (await client.get(url)).status_code

    monkeypatch.delenv(SETTING, raising=False)
    with pytest.raises(httpx.ConnectError, match=re.escape(PRIVATE_REFUSAL)):
        asyncio.run(get())
    monkeypatch.setenv(SETTING, "allow")
    assert asyncio.run(get()) == 200


def test_the_real_transport_receives_only_the_first_checked_ip(monkeypatch):
    monkeypatch.delenv(SETTING, raising=False)
    public_ip = "192.0.0.9"
    answers = iter([[public_ip], ["169.254.169.254"]])
    dialed = []

    async def resolve(host, port):
        return next(answers)

    async def trace(event, info):
        if event == "connection.connect_tcp.started":
            dialed.append((info["host"], info["port"]))
            # Stop just before the socket opens. HTTPX supplies the actual dial target.
            raise httpx.ConnectError("test stopped the connection before dialing")

    async def request():
        async with source_client(resolve=resolve) as client:
            await client.get("https://source.example.test:8443/path", extensions={"trace": trace})

    with pytest.raises(httpx.ConnectError, match="test stopped the connection"):
        asyncio.run(request())
    assert dialed == [(public_ip, 8443)]
    assert next(answers) == ["169.254.169.254"]


def test_the_pin_keeps_host_tls_name_path_and_port_and_refuses_a_redirect():
    seen = []

    async def resolve(host, port):
        return ["192.0.0.9"]

    def upstream(request):
        seen.append(
            (
                request.url.host, request.url.port, request.url.raw_path,
                request.headers["Host"], request.extensions.get("sni_hostname"),
            )
        )  # fmt: skip
        return httpx.Response(307, headers={"location": "https://another.example.test/"})

    async def request():
        async with source_client(httpx.MockTransport(upstream), resolve=resolve) as client:
            return await client.get("https://source.example.test:8443/feed?q=1")

    response = asyncio.run(request())
    assert response.status_code == 307
    assert response.request.url.host == "source.example.test"
    assert seen == [
        ("192.0.0.9", 8443, b"/feed?q=1", "source.example.test:8443", "source.example.test")
    ]


def test_different_tls_names_on_the_same_ip_use_different_connection_pools(monkeypatch):
    pools = []

    async def resolve(host, port):
        return ["192.0.0.9"]

    async def stand_in(transport, request):
        pools.append(transport)
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", stand_in)

    async def request():
        async with source_client(resolve=resolve) as client:
            for host in ("one", "two", "one"):
                assert (await client.get(f"https://{host}.example.test/")).status_code == 200

    asyncio.run(request())
    assert pools[0] is pools[2]
    assert pools[0] is not pools[1]


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("serve", ["--no-tidy"]),
        ("source-add", ["--name", "ledger", "--kind", "connector", "--config", "{}"]),
        ("source-sync", ["--name", "ledger"]),
        ("source-preview", ["--name", "ledger"]),
        ("credential-add", ["--name", "ledger", "--kind", "connector", "--config", "{}"]),
    ],
)
def test_bad_setting_stops_the_command_before_opening_the_store(
    monkeypatch, tmp_path, capsys, command, extra
):
    def opened(*args, **kwargs):
        pytest.fail("opened the store with an invalid source setting")

    from cairn.web import cli

    monkeypatch.setattr(cli, "_open_store", opened)
    monkeypatch.setenv(SETTING, "synthetic-invalid-setting")
    db = tmp_path / "cairn.db"
    assert main([command, "--db", str(db), *extra]) == 2
    said = capsys.readouterr()
    assert not db.exists()
    assert said.err == f"{SETTING} is allow or refuse\n"


def test_a_stored_private_source_stays_enabled_and_shows_the_failed_run(monkeypatch, tmp_path):
    monkeypatch.delenv(SETTING, raising=False)
    db = tmp_path / "cairn.db"
    before = Store(db, clock=lambda: NOW)
    before.create_project("ledger")
    before.add_source(
        "ledger", "connector",
        {"url": "https://10.0.0.5/feed", "projects": ["ledger"], "secret_env": "FEED_SECRET"},
    )  # fmt: skip
    before.enable_source("ledger")
    before.close()

    def stand_in(request):
        pytest.fail("the private source was contacted")

    async def sync(store):
        async with source_client(httpx.MockTransport(stand_in)) as client:
            await run_source(
                store, "ledger", http=client, env={"FEED_SECRET": "synthetic-feed-secret"},
                clock=lambda: NOW,
            )  # fmt: skip

    store = Store(db, clock=lambda: NOW)
    try:
        with pytest.raises(SourceError, match=re.escape(PRIVATE_REFUSAL)):
            asyncio.run(sync(store))
        source = store.get_source("ledger")
        assert source.enabled and source.last_error == PRIVATE_REFUSAL
        key = store.create_key("reader")
        base = "http://127.0.0.1:8765"
        with TestClient(create_app(store, public_url=base), base_url=base) as client:
            client.post("/login", data={"key": key}, headers={"sec-fetch-site": "same-origin"})
            page = client.get("/sources")
        assert page.status_code == 200
        assert PRIVATE_REFUSAL in page.text
    finally:
        store.close()


def test_google_fetch_keeps_an_address_refusal_in_its_request_failure_contract(monkeypatch):
    from cairn.sources.google_api import Unreachable, fetch

    monkeypatch.setenv(SETTING, "refuse")

    def stand_in(request):
        pytest.fail("the private token endpoint was contacted")

    async def request():
        async with source_client(httpx.MockTransport(stand_in)) as client:
            return await fetch(client, "POST", "http://127.0.0.1:8765/token")

    with pytest.raises(Unreachable, match=re.escape(PRIVATE_REFUSAL)):
        asyncio.run(request())


def test_allow_keeps_plain_http_on_the_local_machine(monkeypatch):
    monkeypatch.setenv(SETTING, "allow")

    async def resolve(host, port):
        return ["192.0.0.9"]

    def stand_in(request):
        pytest.fail("a loopback HTTP source was handed to a non-loopback address")

    async def request():
        async with source_client(httpx.MockTransport(stand_in), resolve=resolve) as client:
            await client.get("http://localhost:8765/feed")

    with pytest.raises(httpx.ConnectError, match="plain HTTP source"):
        asyncio.run(request())
