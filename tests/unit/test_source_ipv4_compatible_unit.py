"""An IPv4 address written the deprecated "IPv4-compatible" way is read as that address.

RFC 4291 (2.5.5.1) deprecates `::a.b.c.d`, the address after 96 zero bits, and no current
system tunnels it; Python still calls `::169.254.169.254` globally reachable, so a source
written or resolved that way passed the check a metadata address must never pass (the first
outside reviewer's note on the fifth round). Its layout is one, so it is read, as the
well-known translation prefix is: the last 32 bits. `::` and `::1` are not such addresses:
the unspecified one is refused as before and loopback stays this machine.

Runtime resolution uses a stand-in; every name is made up.
"""

from ipaddress import IPv6Address

import pytest
from test_source_translated_layouts_unit import fetch

from cairn import identity
from cairn.sources import connector
from cairn.sources import url as u


@pytest.mark.parametrize(
    ("address", "by_default", "allowed"),
    [
        ("::169.254.169.254", u.RefusedAddress, u.RefusedAddress),  # metadata, link-local
        ("::a9fe:a9fe", u.RefusedAddress, u.RefusedAddress),  # the same, in hexadecimal
        ("::224.0.0.1", u.RefusedAddress, u.RefusedAddress),  # multicast
        ("::255.255.255.255", u.RefusedAddress, u.RefusedAddress),  # broadcast
        ("::10.0.0.5", u.PrivateAddress, None),
        ("::127.0.0.1", u.PrivateAddress, None),  # not loopback: it is on no interface
        ("::93.184.216.34", None, None),
    ],
)
@pytest.mark.parametrize("route", ["written", "resolved", "second-answer"])
def test_an_ipv4_compatible_address_is_read_as_the_address_it_holds(
    monkeypatch, address, by_default, allowed, route
):
    where = f"https://[{address}]/api" if route == "written" else "https://t.fable.test/api"
    answers = [address] if route == "resolved" else ["93.184.216.34", address]
    for setting, outcome in (("refuse", by_default), ("allow", allowed)):
        monkeypatch.setenv(u.PRIVATE_ADDRESSES_SETTING, setting)
        sent, get = fetch(where, answers)
        if outcome is None:
            assert get().status_code == 200 and len(sent) == 1
        else:
            with pytest.raises(u.RefusedAddress) as refused:
                get()
            assert type(refused.value) is outcome and sent == []


def test_a_metadata_address_written_that_way_is_refused_where_an_address_is_stored():
    assert u.inside("::169.254.169.254") is True
    with pytest.raises(ValueError, match="inside this machine's network"):
        connector.check_url("https://[::169.254.169.254]/items")
    assert identity._https_or_loopback("http://[::127.0.0.1]/v1") is False


def test_loopback_and_the_unspecified_address_are_what_they_were():
    assert u._loopback(IPv6Address("::1")) and not u._address_inside(IPv6Address("::1"))
    assert u._address_inside(IPv6Address("::")) and not u._loopback(IPv6Address("::"))
    assert not u._loopback(IPv6Address("::127.0.0.1"))
    assert u._policy_address(IPv6Address("::1")) == IPv6Address("::1")
