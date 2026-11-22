"""Exercise both source policies through the client, with a local resolver and transport."""

import asyncio
from ipaddress import IPv4Address

import pytest

from cairn.sources import url
from cairn.sources.runner import source_client
from support.sources.source_url import Resolver, recording

HOST = "translation.example.test"
# RFC 6052 section 2.2, in prefix-length order. The u octet is zero. For /32 and /40,
# overlap with 64:ff9b:1::/48 fixes the first two or one IPv4 octets respectively.
# All six use stand-in metadata addresses. Some suffixes have nonzero bits beyond the
# embedded address. The /48 and /56 controls exercise those same layouts with zero suffixes.
LAYOUTS = [
    pytest.param("0.1.2.33", "64:ff9b:1:221::c000:209", id="32"),
    pytest.param("1.2.3.33", "64:ff9b:1:203:21::c000:209", id="40"),
    pytest.param("192.0.2.33", "64:ff9b:1:c000:2:2100:c000:209", id="48"),
    pytest.param("192.0.2.33", "64:ff9b:1:c000:2:2100::", id="48-zero"),
    pytest.param("192.0.2.33", "64:ff9b:1:abc0:0:221:c000:209", id="56"),
    pytest.param("192.0.2.33", "64:ff9b:1:abc0:0:221::", id="56-zero"),
    pytest.param("192.0.2.33", "64:ff9b:1:abcd:c0:2:2100:0", id="64"),
    pytest.param("192.0.2.33", "64:ff9b:1:abcd:0:17:c000:221", id="96"),
]


def fetch(transport, address, answers):
    async def run():
        async with source_client(transport, resolve=Resolver({HOST: answers})) as client:
            return await client.get(address)

    return asyncio.run(run())


@pytest.mark.parametrize(
    "address",
    ["64:ff9b:1:5db8:d8:2200::", "64:ff9b:1::93.184.216.34"],
    ids=["zero-tail", "public-tail"],
)
def test_default_refuses_unknown_translation_layout(monkeypatch, address):
    monkeypatch.delenv(url.PRIVATE_ADDRESSES_SETTING, raising=False)
    sent, transport = recording()
    with pytest.raises(url.RefusedAddress):
        fetch(transport, f"https://{HOST}/", [address])
    assert not sent


@pytest.mark.parametrize(("metadata", "address"), LAYOUTS)
@pytest.mark.parametrize("mode", ["default", "allow"])
@pytest.mark.parametrize("route", ["literal", "dns"])
def test_protected_destination_never_reaches_the_transport(
    monkeypatch, metadata, address, mode, route
):
    if mode == "default":
        monkeypatch.delenv(url.PRIVATE_ADDRESSES_SETTING, raising=False)
    else:
        monkeypatch.setenv(url.PRIVATE_ADDRESSES_SETTING, "allow")
    monkeypatch.setattr(url, "METADATA_ADDRESSES", url.METADATA_ADDRESSES | {IPv4Address(metadata)})
    sent, transport = recording()
    endpoint = f"https://[{address}]/" if route == "literal" else f"https://{HOST}/"
    # A permitted first answer must not hide a protected second answer.
    with pytest.raises(url.RefusedAddress):
        fetch(transport, endpoint, ["192.0.0.9", address])
    assert not sent


def test_known_public_translation_still_works(monkeypatch):
    monkeypatch.delenv(url.PRIVATE_ADDRESSES_SETTING, raising=False)
    sent, transport = recording()
    response = fetch(transport, f"https://{HOST}/", ["64:ff9b::93.184.216.34"])
    assert response.is_success and len(sent) == 1


def test_configured_metadata_stays_blocked_with_private_access(monkeypatch):
    monkeypatch.setenv(url.PRIVATE_ADDRESSES_SETTING, "allow")
    sent, transport = recording()
    with pytest.raises(url.RefusedAddress):
        fetch(transport, f"https://{HOST}/", ["64:ff9b:1::169.254.169.254"])
    assert not sent
