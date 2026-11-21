"""A claim's own Markdown (the cross-reader's item 7, CORE5 item 8). A passage that is a
setext heading or a link definition is the note's own text, and it stays a claim. In the
page's body, the Markdown a REST or MCP reader gets, it changes how the body reads as one
document: a setext heading puts the claims after it under its heading, and a definition
renders as nothing. The web view does not render the body: it shows each claim by itself,
with its own citations, under the headings of its own passages.

Below that, the other limits of a body that is the notes' own Markdown, each shown by parsing
the assembled body and each with what does not happen: one note's link definition makes a
link of a bracketed word in another's claim, and of two notes that define one label the
first is the address of every use; a claim that begins with indentation, after a
claim that ends in a list, is read as part of that list's last item; and for a reader with
raw HTML on, an HTML block a note leaves open holds the claims after it. Every claim's text
is still exact in the claims list, and the web view shows each claim by itself
(cairn.web.markdown.render, the template filter `md`). Made-up notes."""

import re

import pytest
from markdown_it import MarkdownIt
from starlette.testclient import TestClient

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store
from cairn.web.markdown import render

BODY = MarkdownIt("commonmark", {"html": False})
HTML_ON = MarkdownIt("commonmark", {"html": True})


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


# One note's Markdown in the body of a page of several notes


@pytest.fixture
def filed(tmp_path):
    """The page of notes filed one by one under one topic, each saved."""
    store = Store(tmp_path / "cairn.db")
    store.create_project("alpha")
    memory = Memory(store, lint_gate)

    def page(texts: list[str]):
        for text in texts:
            answer = memory.note("alpha", text, title="Ops", topic="Ops")
            assert answer["status"] == "saved", answer["violations"]
        page = store.get_page(store.get_project("alpha").id, "ops")
        assert [claim.text for claim in page.claims] == texts  # every claim's text, exact
        return page

    yield page
    store.close()


def read(body: str, reader: MarkdownIt = BODY) -> list[tuple]:
    """What a reader of the body is shown, block by block: (kind, depth, text) and, for a
    paragraph, the addresses its links go to."""
    tokens = reader.parse(body)
    shown = []
    for n, token in enumerate(tokens):
        if token.type in ("heading_open", "paragraph_open"):
            inline = tokens[n + 1]
            links = [c.attrs["href"] for c in inline.children or [] if c.type == "link_open"]
            shown.append((token.tag, token.level, inline.content, *links))
        elif token.type in ("code_block", "fence", "html_block"):
            shown.append((token.type, token.level, token.content))
    return shown


DEFINITION, USE = "[quill]: https://example.invalid/", "See [quill] now."


@pytest.mark.parametrize(
    "texts",
    [["- item", "    " + DEFINITION, USE], [DEFINITION, USE], [USE, DEFINITION]],
    ids=["indented after a list", "at the margin, before", "at the margin, after"],
)
def test_a_link_definition_in_one_claim_makes_a_link_of_a_word_in_another(filed, texts):
    page = filed(texts)
    for reader in (BODY, HTML_ON):
        shown = read(page.body, reader)
        # the definition shows nothing, and the other note's word is a link to its address
        assert ("p", 0, USE, "https://example.invalid/") in shown
        assert not any("example.invalid" in block[2] for block in shown)
    # what does not happen: each claim by itself, as the web view shows it, has no link
    assert render(USE) == "<p>See [quill] now.</p>\n"
    assert render(DEFINITION) == ""
    assert render("    " + DEFINITION) == f"<pre><code>{DEFINITION}\n</code></pre>\n"
    assert all("<a " not in render(claim.text) for claim in page.claims)


def test_when_two_notes_define_one_label_every_use_in_the_body_takes_the_first(filed):
    """Muse's third reading, finding 3. A link definition holds for the whole document, and
    of two for one label CommonMark takes the first: the second note's word, which in its
    own text points at the second address, is a link to the first in the body."""
    first = "[quill]: https://first.example.invalid/\nSee [quill] one."
    second = "[quill]: https://second.example.invalid/\nSee [quill] two."
    page = filed([first, second])
    for reader in (BODY, HTML_ON):
        shown = read(page.body, reader)
        assert ("p", 0, "See [quill] one.", "https://first.example.invalid/") in shown
        assert ("p", 0, "See [quill] two.", "https://first.example.invalid/") in shown
        assert not any("second.example.invalid" in str(block[3:]) for block in shown)
    # what does not happen: the claims list holds each note's text, and each claim by itself,
    # as the web view shows it, links to the address its own note gives
    assert [claim.text for claim in page.claims] == [first, second]
    for alone, address in ((first, "first"), (second, "second")):
        (link,) = re.findall(r'<a href="([^"]*)"', render(alone))
        assert link == f"https://{address}.example.invalid/"


@pytest.mark.parametrize(
    ("middle", "in_the_body", "alone"),
    [
        ("    ## Quill heading", ("h2", 2, "Quill heading"), "## Quill heading"),
        ("    Quill text.", ("p", 2, "Quill text."), "Quill text."),
    ],
    ids=["a heading", "a paragraph"],
)
def test_a_claim_that_begins_indented_after_a_list_is_read_as_part_of_its_last_item(
    filed, middle, in_the_body, alone
):
    page = filed(["- item", middle, "After."])
    for reader in (BODY, HTML_ON):
        shown = read(page.body, reader)
        assert shown == [("h1", 0, "Ops"), ("p", 2, "item"), in_the_body, ("p", 0, "After.")]
    # what does not happen: alone, as the web view shows it, the claim is indented code
    assert render(middle) == f"<pre><code>{alone}\n</code></pre>\n"


def test_such_a_claim_is_never_a_fence_that_runs_past_itself(filed):
    page = filed(["- item", "    ~~~\n    sample", "  After."])
    for reader in (BODY, HTML_ON):
        shown = read(page.body, reader)
        assert shown[-2:] == [("fence", 2, "sample\n"), ("p", 2, "After.")]
    assert render("    ~~~\n    sample") == "<pre><code>~~~\nsample\n</code></pre>\n"


@pytest.mark.parametrize(
    "opener",
    ["<!-- kept", "<script>", "<pre>", "<?php", "<!DOCTYPE quill", "<![CDATA[ kept"],
    ids=["comment", "script", "pre", "processing", "declaration", "cdata"],
)
def test_an_html_block_a_note_leaves_open_holds_the_next_claim_for_a_reader_with_html_on(
    filed, opener
):
    """The package's parser has raw HTML off: for it the note is a paragraph and so is the
    claim after it, and no check of the body sees more. A reader that takes raw HTML reads
    every such block to the end of the body, the claims after it inside."""
    page = filed([opener, "After."])
    assert read(page.body) == [("h1", 0, "Ops"), ("p", 0, opener), ("p", 0, "After.")]
    held = ("html_block", 0, f"{opener}\n\nAfter.\n")
    assert read(page.body, HTML_ON) == [("h1", 0, "Ops"), held]
    # what does not happen: the web view shows each claim by itself, raw HTML as text
    assert render("After.") == "<p>After.</p>\n"
    assert "<script>" not in render(opener) and "<pre>" not in render(opener)
