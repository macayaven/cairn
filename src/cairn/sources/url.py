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
  or broadcast address; also when written as an IPv4-mapped IPv6 address, as one a translator
  rewrites from a prefix with one layout (64:ff9b::/96, ::ffff:0:0:0/96: the last 32 bits),
  or as IPv4 in one number, in hex or with fewer than four parts. Every address of the
  local-use translation range 64:ff9b:1::/48 is inside, whatever it embeds: each network
  lays that range out its own way (RFC 6052, 2.2: after 32, 40, 48, 56, 64 or 96 bits), so
  no reading of such an address is the right one. Private ranges and loopback are not
  inside: a self-hoster's own services live there.
- `CheckedTransport`: resolve every request through the source client and send it only
  to an address that passed the checks. The HTTP Host and TLS name stay the source's name.
  A non-global address needs the operator's CAIRN_SOURCE_PRIVATE_ADDRESSES=allow.
- `system_resolve(host, port)`: the system resolver. Tests hand in their own.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable, Mapping
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx

URL_MAX = 2_048
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
_NAT64_LOCAL = ipaddress.IPv6Network("64:ff9b:1::/48")
_IPV4_TRANSLATED = ipaddress.IPv6Network("::ffff:0:0:0/96")
_NUMERIC = frozenset("0123456789abcdefx.")  # IPv4 written in a form inet_aton reads
INSIDE = (
    "is an address inside this machine's network plumbing (a link-local address, a cloud's"
    " metadata service, a multicast, unspecified or broadcast address, or one of the range"
    " kept for a network's own address translators), where a credential is never sent"
)

PRIVATE_ADDRESSES_SETTING = "CAIRN_SOURCE_PRIVATE_ADDRESSES"
PRIVATE_REFUSAL = (
    f"To read a source on a non-global address, set {PRIVATE_ADDRESSES_SETTING}=allow."
)
Resolve = Callable[[str, int], Awaitable[Iterable[str]]]
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def private_addresses_allowed(environ: Mapping[str, str]) -> bool:
    value = environ.get(PRIVATE_ADDRESSES_SETTING, "refuse")
    if value not in ("allow", "refuse"):
        raise ValueError(f"{PRIVATE_ADDRESSES_SETTING} is allow or refuse")
    return value == "allow"


def _policy_address(address: IPAddress) -> IPAddress:
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif address in _NAT64 or address in _IPV4_TRANSLATED:  # one layout: the last 32 bits
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return address


def _is_global(address: IPAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and (
        address in _NAT64_LOCAL or address in _IPV4_TRANSLATED
    ):
        return False
    return _policy_address(address).is_global


def _address_inside(address: IPAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address in _NAT64_LOCAL:
        return True  # its layout is the network's own: what it embeds cannot be read
    address = _policy_address(address)
    return (
        address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address == _BROADCAST
        or address in METADATA_ADDRESSES
    )


def _literal(host: str) -> IPAddress | None:
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
    full_endpoint: bool = False,
    loopback: frozenset[str] = LOOPBACK_HOSTS,
) -> str:
    """Check a source address without repeating it in a refusal. A base has no query
    and loses its trailing slash. A connector's full endpoint may have a query and
    is stored exactly as the HTTP client sends it."""
    name = "url" if full_endpoint else "base_url"
    if not isinstance(value, str) or (full_endpoint and not value) or len(value) > URL_MAX:
        if full_endpoint:
            raise ValueError(f"url is the connector's address, at most {URL_MAX:,} characters")
        raise ValueError(f"base_url is the address of {what}")
    try:
        parts = urlsplit(value if full_endpoint else value.strip())
        host, _ = parts.hostname, parts.port
    except ValueError:
        raise ValueError(f"{name} is not a URL") from None
    if "@" in parts.netloc:
        where = f", named by {named_by}" if full_endpoint else ""
        raise ValueError(f"{name} must not hold a user or password: {comes_from}{where}")
    scheme = parts.scheme.lower()
    check_transport(
        name, scheme, host, loopback=loopback, http_said=http_said,
        no_host="url is not a URL: it names no host" if full_endpoint else None,
    )  # fmt: skip
    if full_endpoint:
        if parts.fragment or "#" in value:
            raise ValueError("url has no fragment")
    elif parts.query or parts.fragment or "?" in value or "#" in value:
        raise ValueError("base_url has no query or fragment")

    # Catch credential shapes in the whole URL and in its decoded parts individually.
    assert host is not None  # check_transport refused an address without one
    pieces = [value, *host.split("."), *(unquote(s) for s in parts.path.split("/"))]
    if full_endpoint:
        pieces.extend(piece for pair in parse_qsl(parts.query) for piece in pair)
    if any(looks_like(piece) for piece in pieces if piece):
        raise ValueError(f"{name} looks like it holds {holds}: {comes_from}, named by {named_by}")

    if not full_endpoint:
        return f"{scheme}://{parts.netloc}{parts.path.rstrip('/')}"
    try:
        sent = str(httpx.URL(value))
    except (httpx.InvalidURL, ValueError):
        raise ValueError("url is not a URL") from None
    if sent != value:
        raise ValueError(
            "url is not written as it is sent: write it as the HTTP client sends it (the host"
            " in lower case, anything else percent-encoded)"
        )
    return value


# Before each request


class RefusedAddress(httpx.ConnectError):
    """A source's request to an address inside this machine's network plumbing, refused
    before it was sent."""


class PrivateAddress(RefusedAddress):
    """The operator has not allowed sources to reach non-global addresses."""


REFUSED = f"the source's address {INSIDE}"
UNRESOLVED = "the source's address could not be resolved"


async def system_resolve(host: str, port: int) -> list[str]:
    """The addresses the system resolves `host` to (an address written out is given back
    without a lookup)."""
    loop = asyncio.get_running_loop()
    found = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in found]


class CheckedTransport(httpx.AsyncBaseTransport):
    """Pin requests to checked IPs with the documented HTTPX sni_hostname extension.

    Pools are separate by original host. HTTPX pools by the dial target, so sharing
    one pool could reuse a connection authenticated for a different TLS name.
    """

    def __init__(
        self,
        resolve: Resolve | None,
        *,
        allow_private: bool,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.resolve = resolve
        self._allow_private = allow_private
        self._injected = transport
        self._pools: dict[str, httpx.AsyncHTTPTransport] = {}

    async def __aenter__(self) -> "CheckedTransport":
        if self._injected is not None:
            await self._injected.__aenter__()
        return self

    async def aclose(self) -> None:
        if self._injected is not None:
            await self._injected.aclose()
        for pool in self._pools.values():
            await pool.aclose()
        self._pools.clear()

    async def _addresses(self, request: httpx.Request) -> list[IPAddress] | None:
        host = request.url.raw_host.decode("ascii")
        bare = _bare(host)
        literal = _literal(bare)
        if inside(bare):
            raise RefusedAddress(REFUSED, request=request)
        if bare == "localhost" and not self._allow_private:
            raise PrivateAddress(PRIVATE_REFUSAL, request=request)
        if literal is not None:
            addresses = [literal]
        elif self.resolve is None:  # A MockTransport cannot open a connection.
            return None
        else:
            port = request.url.port or (443 if request.url.scheme == "https" else 80)
            try:
                answers = list(await self.resolve(host, port))
                addresses = list(dict.fromkeys(ipaddress.ip_address(_bare(ip)) for ip in answers))
            except (OSError, ValueError):
                raise httpx.ConnectError(UNRESOLVED, request=request) from None
            if not addresses:
                raise httpx.ConnectError(UNRESOLVED, request=request)
        if any(_address_inside(address) for address in addresses):
            raise RefusedAddress(REFUSED, request=request)
        if not self._allow_private and any(not _is_global(address) for address in addresses):
            raise PrivateAddress(PRIVATE_REFUSAL, request=request)
        if (
            request.url.scheme == "http"
            and bare in LOOPBACK_HOSTS
            and any(not _policy_address(address).is_loopback for address in addresses)
        ):
            raise RefusedAddress(
                "The plain HTTP source did not resolve to loopback.", request=request
            )
        return addresses

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        addresses = await self._addresses(request)
        host = request.url.raw_host.decode("ascii")
        transport = self._injected
        if transport is None:
            if host not in self._pools:
                self._pools[host] = httpx.AsyncHTTPTransport()
            transport = self._pools[host]
        if addresses is None:
            return await transport.handle_async_request(request)
        for index, address in enumerate(addresses):
            target = httpx.Request(
                request.method,
                request.url.copy_with(host=str(address)),
                headers=request.headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": host},
            )
            try:
                return await transport.handle_async_request(target)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                # Alternate addresses came from the same checked resolution. Retry only
                # a failed connection, before any request bytes can have been sent.
                if index == len(addresses) - 1:
                    raise
        raise AssertionError("a checked address is required")
