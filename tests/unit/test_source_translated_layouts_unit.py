"""An address of the local-use translation range is never sent to (fifth round, finding 6).

RFC 8215 gives 64:ff9b:1::/48 to a network's own translators, and RFC 6052 (2.2) lets each
put the IPv4 address after a prefix of 32, 40, 48, 56, 64 or 96 bits, at a different place for
each. Which one a network uses is not in the address, so no reading of it is the right one:
the last fix read the last 32 bits throughout, which took a private address written the /48
way for 0.0.0.0 and let a metadata address written the /64 way through once private addresses
were allowed. The range is refused whole, whatever CAIRN_SOURCE_PRIVATE_ADDRESSES says. The
well-known prefix 64:ff9b::/96 has one layout and is read as before.

Each address below is written out by hand from the table of 2.2 and checked against it here.
Runtime resolution uses a stand-in; every name is made up.
"""

import asyncio
from ipaddress import IPv4Address, IPv6Address, IPv6Network

import httpx
import pytest
from test_source_url_unit import SECRET, Resolver, recording

from cairn import identity
from cairn.sources import connector
from cairn.sources import url as u
from cairn.sources.runner import source_client

LOCAL_USE = IPv6Network("64:ff9b:1::/48")
METADATA, PRIVATE, PUBLIC = "169.254.169.254", "10.0.0.5", "93.184.216.34"
# The prefix length, the IPv4 address embedded after it, and the address as written.
ROWS = [
    (48, METADATA, "64:ff9b:1:a9fe:a9:fe00::"),
    (48, PRIVATE, "64:ff9b:1:a00:0:500::"),
    (48, PUBLIC, "64:ff9b:1:5db8:d8:2200::"),
    (56, METADATA, "64:ff9b:1:12a9:fe:a9fe::"),
    (56, PRIVATE, "64:ff9b:1:120a:0:5::"),
    (56, PUBLIC, "64:ff9b:1:125d:b8:d822::"),
    (64, METADATA, "64:ff9b:1:12:a9:fea9:fe00:0"),
    (64, PRIVATE, "64:ff9b:1:12:a:0:500:0"),
    (64, PUBLIC, "64:ff9b:1:12:5d:b8d8:2200:0"),
    (96, METADATA, "64:ff9b:1:12:0:34:a9fe:a9fe"),
    (96, PRIVATE, "64:ff9b:1:12:0:34:a00:5"),
    (96, PUBLIC, "64:ff9b:1:12:0:34:5db8:d822"),
    # A prefix of 32 or 40 bits is shorter than the range, so the range's own bits are the
    # first octets of what it embeds: 0.1 and 1. Neither can spell a metadata address.
    (32, "0.1.169.254", "64:ff9b:1:a9fe::"),
    (40, "1.254.169.254", "64:ff9b:1:fea9:fe::"),
]
NAMED = [pytest.param(*row, id=f"{row[0]}-{row[1]}") for row in ROWS]
# Where 2.2 puts the address's bits for each prefix length (bits 64 to 71 are never its).
PLACES = {
    32: [(32, 64)],
    40: [(40, 64), (72, 80)],
    48: [(48, 64), (72, 88)],
    56: [(56, 64), (72, 96)],
    64: [(72, 104)],
    96: [(96, 128)],
}


def embedded(address: str, length: int) -> IPv4Address:
    bits = f"{int(IPv6Address(address)):0128b}"
    return IPv4Address(int("".join(bits[start:end] for start, end in PLACES[length]), 2))


def fetch(address: str, answers: list[str] | None = None, scheme: str = "https"):
    sent, transport = recording()
    resolve = Resolver({"t.fable.test": answers or []})

    async def run() -> httpx.Response:
        async with source_client(transport=transport, resolve=resolve) as client:
            return await client.get(address, headers={"authorization": f"Bearer {SECRET}"})

    return sent, lambda: asyncio.run(run())


@pytest.mark.parametrize(("length", "holds", "address"), NAMED)
def test_each_row_embeds_what_it_says_in_the_local_use_range(length, holds, address):
    assert IPv6Address(address) in LOCAL_USE
    assert embedded(address, length) == IPv4Address(holds)
    assert f"{int(IPv6Address(address)):0128b}"[64:72] == "0" * 8


@pytest.mark.parametrize(("length", "holds", "address"), NAMED)
@pytest.mark.parametrize("setting", [None, "refuse", "allow"])
@pytest.mark.parametrize("route", ["written", "resolved", "second-answer"])
def test_a_local_use_address_is_refused_whatever_the_setting(
    monkeypatch, length, holds, address, setting, route
):
    """A metadata address, a private one and a public one, under every layout: none is sent,
    and the refusal is the one no setting lifts, so it never says to allow private addresses."""
    if setting is None:
        monkeypatch.delenv(u.PRIVATE_ADDRESSES_SETTING, raising=False)
    else:
        monkeypatch.setenv(u.PRIVATE_ADDRESSES_SETTING, setting)
    where = f"https://[{address}]/api" if route == "written" else "https://t.fable.test/api"
    answers = [address] if route == "resolved" else ["192.0.0.9", address]
    sent, get = fetch(where, answers)
    with pytest.raises(u.RefusedAddress) as refused:
        get()
    assert sent == [] and type(refused.value) is u.RefusedAddress
    said = str(refused.value)
    assert said == u.REFUSED and "the range kept for a network's own address translators" in said
    assert address not in said and holds not in said and SECRET not in said


@pytest.mark.parametrize(("length", "holds", "address"), NAMED)
def test_a_local_use_address_is_refused_where_an_address_is_stored(length, holds, address):
    assert u.inside(address) is True
    with pytest.raises(ValueError, match="inside this machine's network") as refused:
        connector.check_url(f"https://[{address}]/items")
    assert address not in str(refused.value)
    assert identity._https_or_loopback(f"https://[{address}]/v1") is False


def test_the_edges_of_the_range():
    assert u.inside("64:ff9b:1::") and u.inside("64:ff9b:1:ffff:ffff:ffff:ffff:ffff")
    assert not u.inside("64:ff9b:2::") and not u.inside("64:ff9b:0:ffff:ffff:ffff:ffff:ffff")


@pytest.mark.parametrize(
    ("address", "by_default", "allowed"),
    [
        ("64:ff9b::a9fe:a9fe", u.RefusedAddress, u.RefusedAddress),  # 169.254.169.254
        ("64:ff9b::", u.RefusedAddress, u.RefusedAddress),  # 0.0.0.0
        ("64:ff9b::a00:5", u.PrivateAddress, None),  # 10.0.0.5
        ("64:ff9b::5db8:d822", None, None),  # 93.184.216.34
    ],
)
def test_the_well_known_prefix_is_read_as_before(monkeypatch, address, by_default, allowed):
    """Its layout is one: 96 bits, then the address. So it is read: a metadata address in it
    is never sent to, a private one needs the setting, a public one needs nothing."""
    for setting, outcome in (("refuse", by_default), ("allow", allowed)):
        monkeypatch.setenv(u.PRIVATE_ADDRESSES_SETTING, setting)
        sent, get = fetch("https://t.fable.test/api", [address])
        if outcome is None:
            assert get().status_code == 200 and len(sent) == 1
        else:
            with pytest.raises(u.RefusedAddress) as refused:
                get()
            assert type(refused.value) is outcome and sent == []


def test_plain_http_to_this_machine_is_not_sent_to_a_local_use_address(monkeypatch):
    """localhost answered with 127.0.0.1 written the /96 way was taken for loopback."""
    monkeypatch.setenv(u.PRIVATE_ADDRESSES_SETTING, "allow")
    sent, transport = recording()
    resolve = Resolver({"localhost": ["64:ff9b:1::7f00:1"]})

    async def run() -> httpx.Response:
        async with source_client(transport=transport, resolve=resolve) as client:
            return await client.get("http://localhost:8811/api")

    with pytest.raises(u.RefusedAddress):
        asyncio.run(run())
    assert sent == []


def test_what_the_refusal_leaves_reachable(monkeypatch):
    """The limit, as the guide can say it: a server on an IPv6-only network cannot read an
    IPv4-only source through a translator of the local-use range, even with private
    addresses allowed. A public IPv4 address through the well-known prefix, a native IPv6
    address and an IPv4 address reached directly are read as before."""
    monkeypatch.setenv(u.PRIVATE_ADDRESSES_SETTING, "allow")
    sent, get = fetch("https://t.fable.test/api", ["64:ff9b:1::a00:5"])  # 10.0.0.5, the /96 way
    with pytest.raises(u.RefusedAddress):
        get()
    assert sent == []
    monkeypatch.delenv(u.PRIVATE_ADDRESSES_SETTING)
    for answer in ("64:ff9b::5db8:d822", "2a00:1234:5678::9", "192.0.0.9"):
        sent, get = fetch("https://t.fable.test/api", [answer])
        assert get().status_code == 200 and sent[0].url.host == answer
