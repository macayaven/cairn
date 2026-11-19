"""A page's title is one line of heading text, whatever its note's topic or title holds
(review blocker 1).

A topic given with a line break put a paragraph in the body that no claim holds: "# " and the
topic were written as they were. The rule lives where the body is written
(compiler.render_body): a title is read as one line, every line break and the blanks around it
one space. A body is rendered when read, so a title stored with a line break, a page version
stored with the prose, and an archive written before all read as the rule says. Made-up notes.
"""

import html
import io
import json
import re

import pytest
from markdown_it import MarkdownIt
from starlette.testclient import TestClient

from cairn.compiler import compile_note, heading_text, render_body
from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
PRE = re.compile(r'<details class="body">.*?<pre>(.*?)</pre>', re.S)
PENDING = "The inspection is pending."
TOPIC = "Inspection\n\nThe inspection passed."
BODY = f"# Inspection The inspection passed.\n\n{PENDING}\n"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def mcp_page(client, key, slug, version=None) -> dict:
    arguments = {"project": "alpha", "slug": slug}
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


def paragraphs(body: str) -> list[str]:
    tokens = MarkdownIt("commonmark").parse(body)
    return [tokens[n + 1].content for n, t in enumerate(tokens) if t.type == "paragraph_open"]


# The reviewer's note, over REST and MCP


def test_rest_and_mcp_return_no_paragraph_the_topic_held(store):
    key = store.create_key("laptop")
    store.create_project("alpha")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        assert client.post("/login", data={"key": key}, headers=SAME_ORIGIN).status_code < 400
        sent = client.post(
            "/api/projects/alpha/notes",
            headers={"x-api-key": key},
            json={"text": PENDING, "topic": TOPIC},
        ).json()
        assert sent["status"] == "saved", sent
        slug = sent["page"]
        rest = client.get(f"/api/projects/alpha/pages/{slug}", headers={"x-api-key": key}).json()
        mcp = mcp_page(client, key, slug)
        web = PRE.search(client.get(f"/b/alpha/p/{slug}").text)
    for body in (rest["body"], mcp["body"], html.unescape(web.group(1))):
        assert body == BODY
        assert paragraphs(body) == [PENDING]  # the one claim, and nothing else


@pytest.mark.parametrize(
    "fields",
    [
        {"topic": "Inspection\r\rThe inspection passed."},
        {"topic": "Inspection\u2028The inspection passed."},
        {"topic": "Inspection\r\n \nThe inspection passed.\n"},
        {"title": "Inspection\n\nThe inspection passed."},  # a title, when no topic is given
    ],
    ids=["cr", "line-separator", "crlf-and-blanks", "title"],
)
def test_every_line_break_reads_as_one_space(store, fields):
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, PENDING, **fields)
    result = compile_note(store, note)
    assert result.status == "saved"
    assert result.candidate.body == BODY
    assert store.get_page(project.id, result.slug).body == BODY


def test_heading_text_leaves_a_title_of_one_line_as_it_is():
    for title in ("Inspection", "  Two  spaces  ", "", "Tabs\tstay"):
        assert heading_text(title) == title
    assert heading_text("A\n") == "A"


# What an install holds already


def test_a_stored_version_with_the_prose_reads_as_the_rule_says(store):
    key = store.create_key("laptop")
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, PENDING, topic="Inspection")
    slug = compile_note(store, note).slug
    page = store.get_page(project.id, slug)
    old = f"# {TOPIC}\n\n{PENDING}\n"  # as render_body wrote it of such a title before
    store.save_page(project.id, slug, TOPIC, old, page.claims)
    assert store.get_page(project.id, slug).body == old  # stored so
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        for version in (None, 2):
            params = {} if version is None else {"version": version}
            rest = client.get(
                f"/api/projects/alpha/pages/{slug}", params=params, headers={"x-api-key": key}
            ).json()
            assert rest["body"] == BODY and rest["title"] == TOPIC
            assert mcp_page(client, key, slug, version)["body"] == BODY


def test_an_archive_written_before_imports_with_the_title_on_one_line(store, tmp_path):
    source = Store(tmp_path / "before.db")
    try:
        project = source.create_project("alpha")
        note, _ = source.add_note(project.id, PENDING, topic=TOPIC)
        result = compile_note(source, note)
        page = source.get_page(project.id, result.slug)
        old = f"# {TOPIC}\n\n{PENDING}\n"  # the body an install wrote before the rule
        source.save_page(project.id, result.slug, TOPIC, old, page.claims)
        archive = io.BytesIO()
        source.export(archive)
    finally:
        source.close()
    archive.seek(0)
    store.import_export(archive)
    project = store.get_project("alpha")
    for version in store.page_versions(project.id, result.slug):
        assert store.get_page(project.id, result.slug, version=version).body == BODY


def test_render_body_writes_one_line_for_the_title():
    from cairn.spans import chunk
    from cairn.store import Claim

    (span,) = chunk("n1", PENDING)
    claims = [Claim(id="cl_1", text=span.text, span_ids=(span.id,))]
    assert render_body(TOPIC, claims, {span.id: span}) == BODY
