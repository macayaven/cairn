"""Unit tests for the navigation and the workspace page (web/workspace.py), beyond the
frozen P1 contract: a session opened with a key, and which navigation an error page
shows."""

import re

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cairn.web.workspace import PARTS

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


def nav(html: str) -> list[tuple[str, str]]:
    """The links of the main navigation, as (text, href)."""
    block = html.split('aria-label="Main"', 1)[1].split("</nav>", 1)[0]
    found = re.findall(r'href="([^"]+)"[^>]*>([^<]*)<', block)
    return [(text.strip(), href) for href, text in found]


def listed(html: str) -> list[str]:
    block = html.split('id="parts"', 1)[1].split("</ul>", 1)[0]
    return re.findall(r'<a href="([^"]+)"', block)


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        yield client, key
    store.close()


def test_a_key_session_gets_the_four_links_and_every_part(env):
    client, key = env
    client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
    home = client.get("/").text
    assert nav(home) == [
        ("Projects", "/"),
        ("Search", "/search"),
        ("New note", "/new"),
        ("Workspace", "/workspace"),
    ]
    assert 'action="/logout"' in home.split("</nav>", 1)[0]
    hub = client.get("/workspace")
    assert hub.status_code == 200 and "<h1>default</h1>" in hub.text
    shown = listed(hub.text)
    assert shown == [p.href for p in PARTS]
    for href in shown:
        assert client.get(href).status_code == 200, href


def test_a_signed_out_error_page_shows_the_visitors_navigation(env):
    client, _ = env
    page = client.get("/no-such-page", follow_redirects=False)
    assert page.status_code == 404
    assert nav(page.text) == [("Guides", "/docs"), ("Sign in", "/login")]
    assert 'action="/logout"' not in page.text


def test_a_signed_in_error_page_keeps_the_apps_navigation(env):
    client, key = env
    client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
    page = client.get("/b/no-such-project")
    assert page.status_code == 404
    assert ("Workspace", "/workspace") in nav(page.text) and 'action="/logout"' in page.text


def current(html: str) -> list[str]:
    """The hrefs the main navigation marks as the current section."""
    block = html.split('aria-label="Main"', 1)[1].split("</nav>", 1)[0]
    return re.findall(r'href="([^"]+)" aria-current="page"', block)


CRUMB = '<p class="meta"><a href="/workspace">Workspace</a> /</p>'


def test_the_navigation_marks_where_the_reader_is(env):
    client, key = env
    client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
    client.post("/projects", data={"name": "ops"}, headers=SAME_ORIGIN)
    where = {
        "/": "/",
        "/b/ops": "/",
        "/search": "/search",
        "/search?q=retry": "/search",
        "/new": "/new",
        "/b/ops/new": "/new",
        "/workspace": "/workspace",
        "/start": "/workspace",  # the step before Setup (/start/connect)
        **{part.href: "/workspace" for part in PARTS},
    }
    for path, section in where.items():
        page = client.get(path)
        assert page.status_code == 200, path
        assert current(page.text) == [section], path
    assert current(client.get("/workspaces").text) == []  # a key session names no person


def test_a_search_hit_reads_as_its_claim_does_on_the_page(env):
    client, key = env
    client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
    client.post("/projects", data={"name": "ops"}, headers=SAME_ORIGIN)
    note = "# Retry\n\n```bash\nquill run --max-retries 3\n```\n"
    saved = client.post("/b/ops/notes", data={"text": note}, headers=SAME_ORIGIN)
    assert saved.status_code in (200, 303)
    page = client.get("/search?q=quill").text
    hit = page.split('<li class="hit">', 1)[1].split("</li>", 1)[0]
    assert "```" not in hit and "<pre" in hit and "<mark>quill</mark>" in hit
    assert "quill run --max-retries 3" in re.sub(r"</?mark>", "", hit)  # the matched word marked
    assert "score" not in hit


def test_every_page_the_workspace_lists_leads_back_to_it(env):
    client, key = env
    client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
    for part in PARTS:
        assert CRUMB in client.get(part.href).text, part.href
    assert CRUMB in client.get("/start").text
    assert CRUMB not in client.get("/workspace").text
    assert CRUMB not in client.get("/").text


SLACK = "xoxb-" + "123456789012-" + "123456789012-" + "abcdefghijklmnopqrstuvwx"


def test_a_refusal_says_the_guards_rules_in_words_and_keeps_their_ids(env):
    client, key = env
    client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
    client.post("/projects", data={"name": "ops"}, headers=SAME_ORIGIN)
    typed = f"# Tokens\n\nslack: {SLACK}\n"
    refused = client.post("/b/ops/notes", data={"text": typed}, headers=SAME_ORIGIN)
    assert refused.status_code == 422
    alert = refused.text.split('role="alert"', 1)[1].split("</div>", 1)[0]
    assert '<span data-rule="slack_token">a Slack token</span> on line 3' in alert
    assert "slack_token" not in re.sub(r"<[^>]+>", " ", alert)  # the id is not read
    audit = client.get("/audit").text
    assert '<span data-rule="slack_token">a Slack token</span>' in audit
    api = client.post(
        "/api/projects/ops/notes", headers={"x-api-key": key}, json={"text": typed}
    ).json()
    assert api["findings"][0]["rule"] == "slack_token" and "slack_token" in api["error"]


def test_a_key_session_chooses_the_guard_under_the_list_of_parts(env):
    client, key = env
    client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
    page = client.get("/workspace").text
    assert page.index('id="parts"') < page.index('id="guard"')
    assert 'action="/workspace/guard"' in page  # a workspace key manages the workspace
    done = client.post("/workspace/guard", data={"policy": "off"}, headers=SAME_ORIGIN)
    assert done.status_code == 200 and "Nothing is checked" in done.text  # back on /workspace
