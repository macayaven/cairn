"""Finding 15: with DEBUG logging on, a credential a provider echoes in a response header
reached the log. httpx's own request lines were held back, but httpcore, under it, traces
every response's headers at DEBUG ("httpcore.http11"). Every client that carries a
credential (a source's, the model's) and the server's run hold both loggers at WARNING.

The requests go through the real httpx and httpcore, over an in-memory network that echoes
the Authorization header's value in a response header. Nothing leaves the process."""

import asyncio
import json
import logging
import re

import httpcore
import httpx
import pytest

from cairn.llm import OpenAICompatible
from cairn.sources.runner import source_client
from cairn.web.cli import _no_client_request_lines

KEY = "sk-" + "echoed-" + "made-up-" + "7d1c0a9e"  # never a real secret
CHAT = json.dumps(
    {
        "choices": [{"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
).encode()


def _echo(sent: bytes) -> bytes:
    """An HTTP/1.1 answer that carries the request's Authorization value in a header."""
    found = re.search(rb"(?im)^authorization: *(.+?)\r$", sent)
    echoed = found.group(1) if found else b"-"
    head = b"HTTP/1.1 200 OK\r\nX-Echo: %s\r\nContent-Type: application/json\r\n" % echoed
    return head + b"Content-Length: %d\r\n\r\n" % len(CHAT) + CHAT


class EchoStream(httpcore.NetworkStream):
    def __init__(self) -> None:
        self.sent, self.out = b"", None

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.sent += buffer

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self.out is None:
            self.out = _echo(self.sent)
        chunk, self.out = self.out[:max_bytes], self.out[max_bytes:]
        return chunk

    def close(self) -> None:
        pass

    def get_extra_info(self, info: str) -> None:
        return None


class EchoBackend(httpcore.NetworkBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return EchoStream()


class AsyncEchoStream(httpcore.AsyncNetworkStream):
    def __init__(self) -> None:
        self.inner = EchoStream()

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.inner.write(buffer)

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self.inner.read(max_bytes)

    async def aclose(self) -> None:
        pass

    def get_extra_info(self, info: str) -> None:
        return None


class AsyncEchoBackend(httpcore.AsyncNetworkBackend):
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return AsyncEchoStream()

    async def sleep(self, seconds: float) -> None:
        pass


@pytest.fixture
def debug(caplog):
    """The root logger at DEBUG, and the client loggers as a fresh process has them (each
    hold is for good, so the levels are put back afterwards)."""
    names = ("httpx", "httpcore")
    kept = {name: logging.getLogger(name).level for name in names}
    for name in names:
        logging.getLogger(name).setLevel(logging.NOTSET)
    with caplog.at_level(logging.DEBUG):
        yield caplog
    for name, level in kept.items():
        logging.getLogger(name).setLevel(level)


def logged(caplog) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


def test_the_echo_reaches_the_log_through_a_client_that_holds_nothing(debug):
    """The test's own check: without a hold, httpcore writes the echoed header."""
    transport = httpx.HTTPTransport()
    transport._pool = httpcore.ConnectionPool(network_backend=EchoBackend())
    with httpx.Client(transport=transport) as client:
        answer = client.get("http://provider.test/", headers={"Authorization": KEY})
    assert answer.headers["x-echo"] == KEY
    assert KEY in logged(debug)


def test_a_source_s_client_keeps_an_echoed_credential_out_of_the_log(debug):
    transport = httpx.AsyncHTTPTransport()
    transport._pool = httpcore.AsyncConnectionPool(network_backend=AsyncEchoBackend())

    async def resolve(host: str, port: int) -> list[str]:
        return ["192.0.0.9"]

    async def call() -> httpx.Response:
        async with source_client(transport, resolve=resolve) as client:
            return await client.get(
                "http://provider.test/v1/notes?page=2", headers={"Authorization": f"Bearer {KEY}"}
            )

    answer = asyncio.run(call())
    assert answer.headers["x-echo"] == f"Bearer {KEY}"
    assert KEY not in logged(debug) and "page=2" not in logged(debug)


def test_the_model_s_client_keeps_an_echoed_key_out_of_the_log(debug):
    transport = httpx.HTTPTransport()
    transport._pool = httpcore.ConnectionPool(network_backend=EchoBackend())
    model = OpenAICompatible(
        "http://127.0.0.1:8400/v1", "local", api_key=KEY, transport=transport, retries=0
    )
    assert model.complete([{"role": "user", "content": "Arrange."}]).text == "{}"
    assert KEY not in logged(debug)


def test_while_the_server_runs_both_client_loggers_are_held_and_put_back(debug):
    with _no_client_request_lines():
        for name in ("httpx", "httpcore"):
            assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING, name
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level == logging.NOTSET
