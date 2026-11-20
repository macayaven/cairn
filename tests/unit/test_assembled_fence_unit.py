"""A code fence that opens only where the claims are put together is closed where its claim
ends (third report, must-fix 4).

A claim's blocks depend on what the body writes before it: after a list item, a passage that
alone is indented text may open a fence inside the item, and the claim after it reads as
code. The body is parsed whole, and a fence that runs from one claim into the next gets a
closer after the claim it starts in. Each body here is read by the Markdown parser, as the
package reads it and with raw HTML on. Made-up notes."""

import pytest
from markdown_it import MarkdownIt

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store

READERS = {
    "raw HTML off, as the web view reads": MarkdownIt("commonmark", {"html": False}),
    "raw HTML on": MarkdownIt("commonmark", {"html": True}),
}
REVIEWER = ["- item", "    ~~~\n    sample", "  After."]  # the third report's three notes
AS_23C24229_WROTE_IT = "# Ops\n\n- item\n\n    ~~~\n    sample\n\n  After.\n"
CLOSED = "# Ops\n\n- item\n\n    ~~~\n    sample\n    ~~~\n\n  After.\n"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


@pytest.fixture
def memory(store):
    store.create_project("alpha")
    return Memory(store, lint_gate)


def prose(body: str, text: str) -> bool:
    """Whether every reader shows `text` as a paragraph's whole text, and none shows it in a
    fence or an indented code block."""
    for reader in READERS.values():
        tokens = reader.parse(body)
        paragraphs = [
            tokens[n + 1].content
            for n, token in enumerate(tokens)
            if token.type == "paragraph_open"
        ]
        code = [token.content for token in tokens if token.type in ("fence", "code_block")]
        if text not in paragraphs or any(text in block for block in code):
            return False
    return True


def filed(memory: Memory, store: Store, texts: list[str]):
    answers = [memory.note("alpha", text, title="Ops", topic="Ops") for text in texts]
    assert [answer["status"] for answer in answers] == ["saved"] * len(texts)
    return store.get_page(store.get_project("alpha").id, answers[0]["page"])


def test_the_reviewers_third_note_is_a_paragraph_of_the_body_and_in_no_code(memory, store):
    assert not prose(AS_23C24229_WROTE_IT, "After.")  # the fault, as the parser reads it
    page = filed(memory, store, REVIEWER)
    assert [claim.text for claim in page.claims] == REVIEWER  # no claim's text is changed
    assert page.body == CLOSED
    assert prose(page.body, "After.")
    assert memory.page("alpha", page.slug)["body"] == CLOSED  # as the API gives it
