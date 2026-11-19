"""Unit tests for where a source may send its credential (review, finding 3): one helper,
cairn.sources.url, checks every source's and connector's address. https, or http to this
machine as before; never a link-local address, a cloud's metadata name or address, a
multicast, unspecified or broadcast address. Private ranges and loopback stay allowed. A
name is resolved before each request through the source client, by a resolver handed in:
no test here touches DNS."""

import asyncio
import inspect
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

import cairn.sources
from cairn.sources import connector, github, google_api, google_meet, granola, langfuse, linear
from cairn.sources import url as u
from cairn.sources.runner import source_client

SOURCES_DIR = Path(cairn.sources.__file__).parent

# Each checker as the sources call it, and the name its messages start with.
CHECKERS = {
    "github": (github._check_base_url, "base_url"),
    "linear": (linear._check_base_url, "base_url"),
    "granola": (granola._check_base_url, "base_url"),
    "langfuse": (langfuse._check_base_url, "base_url"),
    "google": (lambda v: google_api.check_base_url(v, "the Gmail API"), "base_url"),
    "google_meet": (google_meet._check_base_url, "base_url"),
    "connector": (connector.check_url, "url"),
}

INSIDE = [
    "169.254.169.254",
    "169.254.0.1",
    "169.254.170.2",
    "[fe80::1]",
    "[fe80::1%25eth0]",
    "[febf::1]",
    "metadata.google.internal",
    "METADATA.Google.Internal",
    "metadata.google.internal.",
    "metadata",
    "metadata.goog",
    "instance-data",
    "instance-data.ec2.internal",
    "[fd00:ec2::254]",
    "100.100.100.200",
    "224.0.0.1",
    "239.255.255.250",
    "[ff02::1]",
    "0.0.0.0",
    "[::]",
    "255.255.255.255",
    "[::ffff:169.254.169.254]",
    "[64:ff9b::a9fe:a9fe]",
    "2852039166",  # 169.254.169.254 written as one number
    "0xa9fea9fe",
    "169.254.43518",
]

ALLOWED = [
    "10.0.0.5",
    "172.16.0.1",
    "192.168.1.4",
    "100.64.0.1",  # carrier-grade NAT, where a tailnet's machines live
    "[fd12:3456::1]",  # a unique local address other than a metadata service's
    "169.255.0.1",
    "fable.test",
    "metadata.fable.test",
    "api.github.com",
]


@pytest.mark.parametrize("host", INSIDE)
def test_an_address_inside_the_machines_network_plumbing_is_refused(host):
    assert u.inside(host.strip("[]").replace("%25", "%")) is True


@pytest.mark.parametrize("host", [*ALLOWED, "127.0.0.1", "localhost", "[::1]"])
def test_private_ranges_loopback_and_names_are_allowed(host):
    assert u.inside(host.strip("[]")) is False


@pytest.mark.parametrize("source", sorted(CHECKERS))
@pytest.mark.parametrize("host", INSIDE)
def test_every_source_refuses_an_inside_address_and_says_so_without_it(source, host):
    check, name = CHECKERS[source]
    with pytest.raises(ValueError) as refused:
        check(f"https://{host}/api")
    said = str(refused.value)
    assert said.startswith(f"{name} ") and "inside this machine's network" in said, said
    bare = host.strip("[]").split("%")[0].lower()
    assert f"//{host}" not in said and (bare == "metadata" or bare not in said.lower())


@pytest.mark.parametrize("source", sorted(CHECKERS))
@pytest.mark.parametrize("host", ALLOWED)
def test_every_source_takes_a_private_address_or_a_name_over_https(source, host):
    check, _ = CHECKERS[source]
    assert check(f"https://{host}/api") == f"https://{host}/api"


@pytest.mark.parametrize("source", sorted(CHECKERS))
def test_every_source_takes_http_on_this_machine_as_before(source):
    check, _ = CHECKERS[source]
    assert check("http://127.0.0.1:8811/api") == "http://127.0.0.1:8811/api"
    assert check("http://localhost:8811/api") == "http://localhost:8811/api"
    with pytest.raises(ValueError, match="https"):
        check("http://10.0.0.5/api")


def test_the_same_faults_keep_their_messages():
    with pytest.raises(ValueError, match=r"^base_url is https, or http on a loopback host$"):
        langfuse._check_base_url("http://10.0.0.5")
    with pytest.raises(ValueError, match=r"^base_url is https, or http on this machine$"):
        github._check_base_url("http://10.0.0.5")
    with pytest.raises(ValueError, match=r"^url is https, or http only to 127.0.0.1 or localhost"):
        connector.check_url("http://[::1]:8811/items")  # a connector's loopback is named
    assert github._check_base_url("http://[::1]:8811/api/v3") == "http://[::1]:8811/api/v3"
    with pytest.raises(ValueError, match=r"^base_url is https, or http on this machine$"):
        github._check_base_url("https://")
    with pytest.raises(ValueError, match=r"^url is not a URL: it names no host$"):
        connector.check_url("https:///items")
    with pytest.raises(ValueError, match=r"looks like it holds a token: the token comes from"):
        github._check_base_url(
            "https://ghe.fable.test/ghp_" + "Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk0Lm1234"
        )
    with pytest.raises(ValueError, match=r"named by public_key_env and secret_key_env$"):
        langfuse._check_base_url("https://lf.fable.test/sk-lf-" + "Zx9Qw3Er7Ty1Ui5Op2As")


def test_no_source_keeps_a_url_check_of_its_own():
    for path in sorted(SOURCES_DIR.glob("*.py")):
        if path.name == "url.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "LOOPBACK_HOSTS = " not in text, path.name
        assert 'scheme == "https"' not in text, path.name


# 6. A name that resolves inside is refused before a request goes out


class Resolver:
    """A resolver handed in: answers from a table, and remembers what it was asked."""

    def __init__(self, table: dict[str, list[str]] | None = None, fails: bool = False) -> None:
        self.table, self.fails, self.asked = table or {}, fails, []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.asked.append((host, port))
        if self.fails:
            raise OSError("no such name")
        return self.table.get(host, ["10.0.0.5"])


def recording():
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    return sent, httpx.MockTransport(handler)


SECRET = "Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk0Lm"


def get(client: httpx.AsyncClient, address: str) -> httpx.Response:
    async def run() -> httpx.Response:
        async with client:
            return await client.get(address, headers={"authorization": f"Bearer {SECRET}"})

    return asyncio.run(run())


@pytest.mark.parametrize(
    "answers",
    [
        ["169.254.169.254"],
        ["fe80::1"],
        ["10.0.0.5", "169.254.169.254"],  # any one of them
        ["::ffff:169.254.169.254"],
        ["224.0.0.1"],
        ["0.0.0.0"],
    ],
)
def test_a_name_that_resolves_inside_is_refused_and_nothing_is_sent(answers):
    sent, transport = recording()
    resolve = Resolver({"rebind.fable.test": answers})
    client = source_client(transport=transport, resolve=resolve)
    with pytest.raises(u.RefusedAddress) as refused:
        get(client, "https://rebind.fable.test/api")
    assert sent == [] and resolve.asked == [("rebind.fable.test", 443)]
    assert isinstance(refused.value, httpx.HTTPError)  # every source answers those already
    said = str(refused.value)
    assert "rebind" not in said and SECRET not in said and "169.254" not in said


@pytest.mark.parametrize(
    "answers", [["10.0.0.5"], ["192.168.1.4", "fd12:3456::1"], ["127.0.0.1"], ["::1"]]
)
def test_a_name_that_resolves_to_a_private_or_loopback_address_is_sent(answers):
    sent, transport = recording()
    resolve = Resolver({"ghe.fable.test": answers})
    response = get(
        source_client(transport=transport, resolve=resolve), "http://ghe.fable.test:8080/api"
    )
    assert response.status_code == 200 and len(sent) == 1
    assert resolve.asked == [("ghe.fable.test", 8080)]


@pytest.mark.parametrize("host", ["169.254.169.254", "[fe80::1]", "2852039166", "0xa9fea9fe"])
def test_an_inside_address_written_out_is_refused_without_asking(host):
    sent, transport = recording()
    resolve = Resolver()
    with pytest.raises(u.RefusedAddress):
        get(source_client(transport=transport, resolve=resolve), f"https://{host}/api")
    assert sent == []


def test_an_address_written_out_is_not_resolved():
    sent, transport = recording()
    resolve = Resolver()
    get(source_client(transport=transport, resolve=resolve), "https://10.0.0.5/api")
    assert resolve.asked == [] and len(sent) == 1


def test_a_name_that_does_not_resolve_is_a_connect_error_and_nothing_is_sent():
    sent, transport = recording()
    client = source_client(transport=transport, resolve=Resolver(fails=True))
    with pytest.raises(httpx.ConnectError) as failed:
        get(client, "https://gone.fable.test/api")
    assert sent == [] and "gone" not in str(failed.value)


def test_a_source_reading_through_the_client_says_it_cannot_reach_its_address():
    """A source answers the refusal as any failure to connect: by the error's kind."""
    sent, transport = recording()
    client = source_client(transport=transport, resolve=Resolver({"feed.fable.test": ["fe80::9"]}))

    async def ask() -> None:
        async with client:
            await connector.ask(
                client,
                "https://feed.fable.test/items",
                SECRET,
                {},
                deadline=None,
                clock=lambda: datetime(2026, 11, 19, tzinfo=UTC),
            )

    with pytest.raises(cairn.sources.SourceError) as failed:
        asyncio.run(ask())
    assert sent == [] and "RefusedAddress" in str(failed.value)
    assert SECRET not in str(failed.value)


def test_the_client_without_a_transport_checks_with_the_systems_resolver():
    client = source_client()
    (check,) = client.event_hooks["request"]
    assert check.resolve is u.system_resolve


def test_a_client_given_a_transport_alone_resolves_nothing():
    """Tests hand in a transport and, unless they give a resolver, no name is looked up."""
    _, transport = recording()
    assert source_client(transport=transport).event_hooks["request"] == []


def test_the_resolver_is_handed_in_and_never_looked_up_by_the_helper():
    assert "resolve" in inspect.signature(source_client).parameters


# The guide


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_security_guide_says_where_a_source_connects():
    """In the security guide: the sources guide is at its bound (70,000 bytes)."""
    from guide_source import guide_source

    sentence = (
        "- A source sends its credential only over `https`, or `http` on this machine, and"
        " never to a link-local address, a cloud's metadata service, or a multicast,"
        " unspecified or broadcast address, whether the address is written out or a name"
        " leads to it; a private address and this machine itself stay allowed, since a"
        " self-hoster's own services live there."
    )
    assert flat(sentence) in flat(guide_source("security"))
    assert connector.check_url("https://10.0.0.5/items") and github._check_base_url(
        "https://192.168.1.4/api/v3"
    )
    for refused in ("https://169.254.169.254/x", "https://metadata.google.internal/x"):
        with pytest.raises(ValueError, match="inside this machine's network"):
            connector.check_url(refused)
    with pytest.raises(u.RefusedAddress):
        get(
            source_client(transport=recording()[1], resolve=Resolver({"x.fable.test": ["::"]})),
            "https://x.fable.test/",
        )
