"""Plain http reaches this machine and nothing else (fifth round, the case past finding 6).

A source may be written `http://localhost:…`. Whether the request then stays on this machine
is judged on the address the connection is pinned to, each one the name resolved to: an IPv4
or IPv6 loopback address, or the IPv4-mapped form of one (::ffff:127.0.0.1, which the kernel
delivers to 127.0.0.1). A loopback address as a translator would rewrite it (64:ff9b::7f00:1,
::ffff:0:7f00:1) is an address on the network, where the translator is: it was taken for
loopback, so with private addresses allowed the request left the machine in clear.
Runtime resolution uses a stand-in; every name is made up.
"""

import asyncio

import httpx
import pytest

from cairn import identity, llm
from cairn.sources import url as u
from cairn.sources.runner import source_client
from support.sources.source_url import CHECKERS, SECRET, Resolver, recording

TRANSLATED = ["64:ff9b::7f00:1", "::ffff:0:7f00:1"]  # 127.0.0.1 behind each one-layout prefix
PLAIN = "The plain HTTP source did not resolve to loopback."


def ask(answers: list[str], address: str = "http://localhost:8811/api"):
    sent, transport = recording()
    resolve = Resolver({"localhost": answers, "ghe.fable.test": answers})

    async def run() -> httpx.Response:
        async with source_client(transport=transport, resolve=resolve) as client:
            return await client.get(address, headers={"authorization": f"Bearer {SECRET}"})

    return sent, resolve, lambda: asyncio.run(run())


@pytest.mark.parametrize("answer", TRANSLATED)
@pytest.mark.parametrize("beside", [[], ["127.0.0.1"]], ids=["alone", "beside-loopback"])
def test_a_translated_loopback_address_is_not_this_machine(monkeypatch, answer, beside):
    monkeypatch.setenv(u.PRIVATE_ADDRESSES_SETTING, "allow")
    sent, _, get = ask([*beside, answer])
    with pytest.raises(u.RefusedAddress) as refused:
        get()
    assert sent == [] and str(refused.value) == PLAIN
    assert type(refused.value) is u.RefusedAddress  # no setting lifts it


@pytest.mark.parametrize("answer", TRANSLATED)
def test_without_the_setting_localhost_is_refused_before_it_is_resolved(monkeypatch, answer):
    """As before: localhost is a private address by its name."""
    monkeypatch.delenv(u.PRIVATE_ADDRESSES_SETTING, raising=False)
    sent, resolve, get = ask([answer])
    with pytest.raises(u.PrivateAddress):
        get()
    assert sent == [] and resolve.asked == []


@pytest.mark.parametrize(
    "answers",
    [["127.0.0.1"], ["::1"], ["127.8.9.10"], ["127.0.0.1", "::1"], ["::ffff:127.0.0.1"]],
    ids=["v4", "v6", "another-of-127", "both", "v4-mapped"],
)
def test_localhost_on_a_loopback_address_is_read_over_plain_http_as_before(monkeypatch, answers):
    """The IPv4-mapped form is loopback: a connection to it is one to 127.0.0.1."""
    monkeypatch.setenv(u.PRIVATE_ADDRESSES_SETTING, "allow")
    sent, resolve, get = ask(answers)
    assert get().status_code == 200 and resolve.asked == [("localhost", 8811)]
    assert sent[0].url.host == answers[0] and sent[0].headers["host"] == "localhost:8811"


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]"])
def test_a_loopback_address_written_out_is_read_over_plain_http_as_before(monkeypatch, host):
    monkeypatch.setenv(u.PRIVATE_ADDRESSES_SETTING, "allow")
    sent, resolve, get = ask([], f"http://{host}:8811/api")
    assert get().status_code == 200 and resolve.asked == [] and len(sent) == 1


@pytest.mark.parametrize("source", sorted(CHECKERS))
def test_plain_http_to_another_name_is_refused_where_the_address_is_stored(source):
    """A name that is not localhost may well resolve to 127.0.0.1; plain http is not for it.
    Every source's address is refused when it is stored, before a name is ever resolved."""
    check, name = CHECKERS[source]
    for written in ("http://ghe.fable.test:8811/api", "http://[::ffff:127.0.0.1]:8811/api"):
        with pytest.raises(ValueError, match=rf"^{name} is https, or http "):
            check(written)
    for answer in TRANSLATED:
        with pytest.raises(ValueError, match=rf"^{name} is https, or http "):
            check(f"http://[{answer}]:8811/api")


@pytest.mark.parametrize(
    "host", ["[64:ff9b::7f00:1]", "[::ffff:0:7f00:1]", "[::ffff:127.0.0.1]", "ghe.fable.test"]
)
def test_sign_in_and_model_endpoints_take_plain_http_by_the_written_host_alone(host):
    """Their rule is another: nothing is resolved and no connection is pinned; plain http is
    for a host written localhost, 127.0.0.1 or [::1], so no other form of loopback passes."""
    assert identity._https_or_loopback(f"http://{host}:8400/v1") is False
    with pytest.raises(llm.LLMConfigError, match=r"^FABLE_BASE_URL must be https, or http on this"):
        llm._anthropic_from_env("quill-1", {"FABLE_BASE_URL": f"http://{host}:8400"}, "FABLE", {})
    for written in ("localhost", "127.0.0.1", "[::1]"):
        assert identity._https_or_loopback(f"http://{written}:8400/v1") is True
