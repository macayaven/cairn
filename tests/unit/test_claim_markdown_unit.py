"""A claim's own Markdown (the cross-reader's item 7, CORE5 item 8). A passage that is a
setext heading or a link definition is the note's own text, and it stays a claim. In the
page's body, the Markdown a REST or MCP reader gets, it changes how the body reads as one
document: a setext heading puts the claims after it under its heading, and a definition
renders as nothing. The web view does not render the body: it shows each claim by itself,
with its own citations, under the headings of its own passages. Made-up notes."""

import re

import pytest
from markdown_it import MarkdownIt
from starlette.testclient import TestClient

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store

BODY = MarkdownIt("commonmark", {"html": False})


def outline(body: str) -> list[tuple[str, str]]:
    """The body's top-level blocks as CommonMark reads it: ("h1", text), ("p", text)."""
    tokens = BODY.parse(body)
    out = []
    for n, token in enumerate(tokens):
        if token.level == 0 and token.type in ("heading_open", "paragraph_open"):
            kind = token.tag if token.type == "heading_open" else "p"
            out.append((kind, tokens[n + 1].content))
    return out


@pytest.fixture
def served(tmp_path):
    store = Store(tmp_path / "cairn.db")
    store.create_project("alpha")
    memory = Memory(store, lint_gate)
    for text in ("Status\n===", "[x]: /target", "Pending."):
        answer = memory.note("alpha", text, title="Ops", topic="Ops")
        assert answer["status"] == "saved", answer["violations"]
    yield store
    store.close()


def test_the_body_reads_as_one_document_and_the_web_view_shows_each_claim(served):
    page = served.get_page(served.get_project("alpha").id, "ops")
    assert [c.text for c in page.claims] == ["Status\n===", "[x]: /target", "Pending."]
    # The body, as a reader of the raw Markdown gets it: "Pending." sits under a second H1,
    # the note's own setext heading, and the definition shows nothing.
    assert outline(page.body) == [("h1", "Ops"), ("h1", "Status"), ("p", "Pending.")]

    base, same_origin = "http://127.0.0.1:8765", {"sec-fetch-site": "same-origin"}
    key = served.create_key("laptop")
    from cairn.server import create_app

    with TestClient(create_app(served, public_url=base), base_url=base) as client:
        assert client.post("/login", data={"key": key}, headers=same_origin).status_code < 400
        shown = client.get("/b/alpha/p/ops").text
    articles = re.findall(r'<article class="claim[^"]*" id="([^"]+)">(.*?)</article>', shown, re.S)
    assert [claim_id for claim_id, _ in articles] == [c.id for c in page.claims]
    texts = [
        re.search(r'<div class="claim-text md">(.*?)</div>', inside, re.S).group(1).strip()
        for _, inside in articles
    ]
    # Each claim by itself: the heading is the note's text, no heading of the page; the
    # definition renders as nothing; "Pending." is a claim of its own with its own source.
    assert texts == ["<p>Status\n===</p>", "", "<p>Pending.</p>"]
    assert [len(re.findall(r'title="Source \d+"', inside)) for _, inside in articles] == [1, 1, 1]
    assert re.findall(r'<h(\d) class="section">', shown) == []
