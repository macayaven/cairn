"""Fix H, points 12 and 13: a page version stored under the previous rendering is served in
the current one, rendered when read from its claims, by REST, by MCP and on the web page,
and the three agree; reading it adds no statement."""

import html
import json
import re

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note, previous_render_body, render_body
from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
PRE = re.compile(r'<details class="body">.*?<pre>(.*?)</pre>', re.S)

DEPRECATED = "## Deprecated — DO NOT USE\n\nNever run legacy-reset.sh on production hosts.\n"
VERIFY = "Always run verify-backup.sh before release.\n"
PORTS = (
    "## Production\n\n### Ports\n\nPort 8080 serves TLS.\n\n"
    "## Staging\n\n### Ports\n\nPort 9090 serves staging.\n"
)


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    project = store.create_project("alpha-ops")
    for text, fields in (
        (DEPRECATED, {"title": "Deploy"}),
        (VERIFY, {"title": "Verify", "topic": "Deploy"}),
        (PORTS, {"title": "Ports guide"}),
    ):
        note, _ = store.add_note(project.id, text, **fields)
        assert compile_note(store, note).status == "saved"
    for slug in ("deploy", "ports-guide"):  # a next version, stored as before fix H
        page = store.get_page(project.id, slug)
        spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
        old = previous_render_body(page.title, page.claims, spans)
        assert old != render_body(page.title, page.claims, spans)
        store.save_page(project.id, slug, page.title, old, page.claims)
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        assert client.post("/login", data={"key": key}, headers=SAME_ORIGIN).status_code < 400
        yield store, project, client, key
    store.close()


def mcp_page(client, key, slug, version=None) -> dict:
    arguments = {"project": "alpha-ops", "slug": slug}
    if version is not None:
        arguments["version"] = version
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "page", "arguments": arguments},
    }
    answer = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=call).json()
    assert answer["result"]["isError"] is False, answer
    return json.loads(answer["result"]["content"][0]["text"])


@pytest.mark.parametrize("slug", ["deploy", "ports-guide"])
def test_rest_mcp_and_the_web_serve_the_current_rendering_of_an_old_version(env, slug):
    store, project, client, key = env
    page = store.get_page(project.id, slug)
    spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
    assert page.body == previous_render_body(page.title, page.claims, spans)  # as stored
    current = render_body(page.title, page.claims, spans)
    path = f"/api/projects/alpha-ops/pages/{slug}"
    rest = client.get(path, headers={"x-api-key": key}).json()
    assert rest["version"] == page.version and rest["body"] == current
    assert mcp_page(client, key, slug)["body"] == current
    shown = PRE.search(client.get(f"/b/alpha-ops/p/{slug}").text)
    assert shown and html.unescape(shown.group(1)) == current
    for version in store.page_versions(project.id, slug):  # every version, old or not
        kept = store.get_page(project.id, slug, version=version)
        want = render_body(kept.title, kept.claims, spans)
        got = client.get(path, params={"version": version}, headers={"x-api-key": key}).json()
        assert got["body"] == want
        assert mcp_page(client, key, slug, version)["body"] == want


def test_reading_a_page_reads_its_passages_once(env, monkeypatch):
    _, _, client, key = env
    calls = []
    real = Store.get_spans

    def counted(self, span_ids):
        calls.append(1)
        return real(self, span_ids)

    monkeypatch.setattr(Store, "get_spans", counted)
    assert client.get("/api/projects/alpha-ops/pages/deploy", headers={"x-api-key": key}).is_success
    assert len(calls) == 1
