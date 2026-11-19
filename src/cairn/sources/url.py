"""Where a source may send its credential: the one check of every source's and connector's
address (review, finding 3).

- `check_transport(name, scheme, host, loopback=..., http_said=...)`: https, or http to a
  loopback host (`loopback`: 127.0.0.1, localhost and ::1 unless a caller names fewer), to a
  host; and never a host `inside` this machine's network plumbing. ValueError starting with `name`,
  never repeating the address.
- `check_base_url(value, ...)`: a source's `base_url` as stored (https, or http on a
  loopback host; no user or password, query or fragment; no piece that looks like a
  credential), the trailing "/" dropped; ValueError naming base_url, never repeating the
  value. Each source says what its address is of and where its credential comes from.
- `inside(host)`: a link-local address (IPv4 169.254.0.0/16, IPv6 fe80::/10), a cloud's
  metadata name or address (METADATA_NAMES, METADATA_ADDRESSES), a multicast, unspecified
  or broadcast address; also when written as an IPv4-mapped or NAT64 IPv6 address, or as
  IPv4 in one number, in hex or with fewer than four parts. Private ranges and loopback are
  not inside: a self-hoster's own services live there.
- `Checked(resolve)`: the source client's request hook. Before each request, the host is
  refused when it is inside; a name is resolved with `resolve(host, port)` and refused when
  any address it resolves to is inside (RefusedAddress, an httpx.ConnectError, so each source
  answers it as any failure to connect, by the error's kind). A name that does not resolve is
  an httpx.ConnectError. Neither message names the host. Every request a source makes
  carries its credential, so every one is checked. The name is resolved again when the
  request connects: a name that changes its answer between the two (DNS rebinding) is not
  caught here.
- `system_resolve(host, port)`: the system's resolver, used only by a client made without
  a transport (cairn.sources.runner.source_client); tests hand in their own.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import unquote, urlsplit

import httpx

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# A cloud's metadata service, by name: Google's, Amazon's, and the short names that reach them.
METADATA_NAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
# By address, where it is not link-local already: Amazon's over IPv6, Alibaba's.
METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(a) for a in ("169.254.169.254", "fd00:ec2::254", "100.100.100.200")
)
_BROADCAST = ipaddress.IPv4Address("255.255.255.255")
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
_NUMERIC = frozenset("0123456789abcdefx.")  # IPv4 written in a form inet_aton reads
INSIDE = (
    "is an address inside this machine's network plumbing (a link-local address, a cloud's"
    " metadata service, a multicast, unspecified or broadcast address), where a credential"
    " is never sent"
)

Resolve = Callable[[str, int], Awaitable[Iterable[str]]]


def _address_inside(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif address in _NAT64:
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return (
        address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address == _BROADCAST
        or address in METADATA_ADDRESSES
    )


def _literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """`host` as an IP address when it is written as one, in any form a resolver would read
    as one without asking (2852039166, 0xa9fea9fe, 169.254.43518 included); else None."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if host and set(host) <= _NUMERIC and host[0].isdigit():
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def _bare(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host.split("%", 1)[0].rstrip(".")  # an IPv6 zone, and a name's final dot


def inside(host: str) -> bool:
    """Whether `host` (a name or an address, as a URL's host gives it) is one a credential is
    never sent to. Never asks a resolver."""
    host = _bare(host)
    if host in METADATA_NAMES:
        return True
    address = _literal(host)
    return address is not None and _address_inside(address)


def check_transport(
    name: str,
    scheme: str,
    host: str | None,
    *,
    loopback: frozenset[str] = LOOPBACK_HOSTS,
    http_said: str = "on this machine",
    no_host: str | None = None,
) -> None:
    """ValueError starting with `name` when `scheme` and `host` are not https, or http to a
    host of `loopback` ("{name} is https, or http {http_said}"), when there is no host (said
    as `no_host`, else as the first), or when `host` is inside."""
    said = f"{name} is https, or http {http_said}"
    if not (scheme == "https" or (scheme == "http" and host in loopback)):
        raise ValueError(said)
    if not host:
        raise ValueError(no_host or said)
    if inside(host):
        raise ValueError(f"{name} {INSIDE}")


def check_base_url(
    value: object,
    *,
    what: str,
    comes_from: str,
    named_by: str,
    holds: str = "a credential",
    http_said: str = "on this machine",
    looks_like: Callable[[str], bool],
) -> str:
    """`base_url` as stored, or ValueError naming it and never repeating the value. `what`
    is what it is the address of; `comes_from` where the credential comes from ("the key
    comes from the environment"), `named_by` the setting that names it; `looks_like` says
    whether a piece of the address looks like a credential."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError(f"base_url is the address of {what}")
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
    except ValueError:
        raise ValueError("base_url is not a URL") from None
    if "@" in parts.netloc:
        raise ValueError(f"base_url must not hold a user or password: {comes_from}")
    scheme = parts.scheme.lower()
    check_transport("base_url", scheme, host, http_said=http_said)
    if parts.query or parts.fragment or "?" in value or "#" in value:
        raise ValueError("base_url has no query or fragment")
    # The address as a whole, and each host label and path segment on its own: a credential
    # the URL carries is caught where it sits, not only at the start of the string.
    assert host is not None  # check_transport refused an address without one
    pieces = [value, *host.split("."), *(unquote(s) for s in parts.path.split("/"))]
    if any(looks_like(piece) for piece in pieces if piece):
        raise ValueError(f"base_url looks like it holds {holds}: {comes_from}, named by {named_by}")
    return f"{scheme}://{parts.netloc}{parts.path.rstrip('/')}"


# Before each request


class RefusedAddress(httpx.ConnectError):
    """A source's request to an address inside this machine's network plumbing, refused
    before it was sent."""


REFUSED = f"the source's address {INSIDE}"
UNRESOLVED = "the source's address could not be resolved"


async def system_resolve(host: str, port: int) -> list[str]:
    """The addresses the system resolves `host` to (an address written out is given back
    without a lookup)."""
    loop = asyncio.get_running_loop()
    found = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in found]


class Checked:
    """The source client's request hook: refuses a request to an inside address, or to a
    name that resolves to one (see the module docstring)."""

    def __init__(self, resolve: Resolve) -> None:
        self.resolve = resolve

    async def __call__(self, request: httpx.Request) -> None:
        host = request.url.host
        if inside(host):
            raise RefusedAddress(REFUSED, request=request)
        if _literal(_bare(host)) is not None:
            return
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        try:
            addresses = list(await self.resolve(host, port))
        except OSError:
            raise httpx.ConnectError(UNRESOLVED, request=request) from None
        if any(inside(address) for address in addresses):
            raise RefusedAddress(REFUSED, request=request)
