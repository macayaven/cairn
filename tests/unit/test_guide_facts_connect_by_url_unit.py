"""Sentences of connect.md and patterns.md about connecting an agent by its URL alone, each
pinned here against the running app so the guides cannot go false unnoticed. An agent
connects by URL alone only where the server runs its own OAuth, which takes `--public-url`;
the README's install has none, and there an agent connects with an API key. The app runs in
process over a real store in a temporary file, as `create_app` is given what `serve` is;
nothing leaves the process."""

from pathlib import Path

import pytest
from guide_source import guide_source
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store

METADATA = "/.well-known/oauth-authorization-server"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
LOCAL = "http://127.0.0.1:8765"  # the README's install: no --public-url
HOSTED = "https://cairn.example.test"


def flat(text: str) -> str:
    return " ".join(text.split())


def says(guide: str, *sentences: str) -> None:
    whole = flat(guide_source(guide))
    for sentence in sentences:
        assert sentence in whole, sentence


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def oauth_answers(client) -> list[int]:
    return [
        client.get(METADATA).status_code,
        client.get("/authorize", follow_redirects=False).status_code,
        client.post("/register", json={}).status_code,
    ]


def test_on_a_local_install_an_agent_connects_with_a_key_and_not_by_url_alone(store):
    """connect.md: "On a local install, started without `--public-url`, sign-in through the
    browser is off: an agent connects with an API key, and [Headless agents and scripts]
    (#headless-agents-and-scripts) says how to make one.\""""
    says(
        "connect",
        "On a local install, started without `--public-url`, sign-in through the browser is"
        " off: an agent connects with an API key, and [Headless agents and scripts]"
        "(#headless-agents-and-scripts) says how to make one.",
        "A key is sent as `x-api-key: <key>` or `Authorization: Bearer <key>`.",
    )
    key = store.create_key("laptop")
    with TestClient(create_app(store), base_url=LOCAL) as client:
        assert oauth_answers(client) == [404, 404, 404]
        assert client.post("/mcp", headers=MCP_HEADERS, json=LIST).status_code == 401
        for sent in ({"x-api-key": key}, {"authorization": f"Bearer {key}"}):
            listed = client.post("/mcp", headers={**MCP_HEADERS, **sent}, json=LIST)
            assert listed.status_code == 200 and listed.json()["result"]["tools"]


@pytest.mark.parametrize("public_url", [HOSTED, LOCAL])
def test_with_a_public_url_an_agent_connects_by_url_alone(store, public_url):
    """connect.md: "On an install started with `--public-url`, cairn is its own OAuth 2.1
    server with dynamic client registration and PKCE, so a client that supports remote MCP
    with OAuth connects with that URL alone: a browser opens once and the person signs in and
    approves." And, "If it does not connect": "Sign-in through the browser is on only on an
    install started with `--public-url`, which must be https except on localhost.\""""
    says(
        "connect",
        "On an install started with `--public-url`, cairn is its own OAuth 2.1 server with"
        " dynamic client registration and PKCE, so a client that supports remote MCP with"
        " OAuth connects with that URL alone: a browser opens once and the person signs in"
        " and approves; a connection left unused for 30 days asks again.",
        "Sign-in through the browser is on only on an install started with `--public-url`,"
        " which must be https except on localhost.",
    )
    with TestClient(create_app(store, public_url=public_url), base_url=public_url) as client:
        metadata = client.get(METADATA)
        assert metadata.status_code == 200 and metadata.json()["issuer"] == public_url
        assert "S256" in metadata.json()["code_challenge_methods_supported"]
        assert metadata.json()["registration_endpoint"] == f"{public_url}/register"
        unsigned = client.post("/mcp", headers=MCP_HEADERS, json=LIST)
        assert unsigned.status_code == 401
        assert "resource_metadata" in unsigned.headers["www-authenticate"]


def test_where_no_browser_opens_the_guide_says_to_use_a_key(store):
    """connect.md, "If it does not connect": "If the browser never opens on an install
    started with `--public-url`, sign in by hand ... On an install started without it no
    browser opens: connect with an API key.\""""
    says(
        "connect",
        "If the browser never opens on an install started with `--public-url`, sign in by hand:",
        "On an install started without it no browser opens: connect with an API key.",
    )
    with TestClient(create_app(store), base_url=LOCAL) as client:
        assert client.get(METADATA).status_code == 404


def test_patterns_connects_by_url_only_where_sign_in_is_on():
    """patterns.md, "Many agents, one project": by URL alone with `--public-url`, a key on a
    local install."""
    whole = flat(guide_source("patterns"))
    assert "Each agent connects by URL" not in whole
    says(
        "patterns",
        "One project holds everything, and every agent reaches it.",
        "On an install started with `--public-url`, an agent that has a browser at hand"
        " connects by URL alone:",
        "On a local install, started without `--public-url`, sign-in through the browser is"
        " off: give each agent a key instead, as in step 3 and [the connect guide]"
        "(connect.md#headless-agents-and-scripts) shows.",
    )


def test_the_readmes_list_of_guides_says_by_url_only_where_sign_in_is_on():
    """README.md, "Guides": the connect guide connects "by URL on an install with
    `--public-url`, or with a key"."""
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    (row,) = [line for line in readme.splitlines() if line.startswith("| [Connect an agent]")]
    assert "by URL on an install with `--public-url`, or with a key" in row
