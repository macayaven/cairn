"""Fence line ranges in the saved Markdown end before another claim's lines."""

from contextlib import closing

import pytest
from markdown_it import MarkdownIt

from cairn.compiler import render_body
from cairn.gate import lint_gate
from cairn.memory import Memory, RequestError
from cairn.store import Store


def assert_rendered(store, page):
    spans = store.get_spans(i for claim in page.claims for i in claim.span_ids)
    assert page.body == render_body(page.title, page.claims, spans)


def paragraphs_of(page):
    # Whole sequences distinguish a claim beginning with tildes from an extra closer.
    # CommonMark's end line is exclusive, including inside nested lists.
    tokens = MarkdownIt("commonmark").parse(page.body)
    lines = page.body.splitlines()
    content = [line.strip() for line in lines]
    owners = {}
    start = 0
    for claim in page.claims:
        source = [line.strip() for line in claim.text.splitlines()]
        first = next(
            (i for i in range(start, len(lines)) if content[i : i + len(source)] == source), None
        )
        assert first is not None, claim.text
        start = first + len(source)
        owners.update(dict.fromkeys(range(first, start), claim.id))
    for token in tokens:
        if token.type == "fence":
            first, end = token.map
            fenced = {owners[i] for i in range(first, end) if i in owners}
            assert len(fenced) <= 1, (token.map, lines[first:end])
    return [
        tokens[i + 1].content.strip() for i, t in enumerate(tokens) if t.type == "paragraph_open"
    ]


@pytest.mark.parametrize(
    ("item", "middle", "last"),
    [
        ("- item", "    ~~~\n    sample", "  After."),
        ("- item", "    ```\n    sample", "  After."),
        ("1. item", "     ~~~~\n     sample", "   After."),
        ("- item", "    ~~~\n    sample", "  Still prose.\n\nAfter."),
        ("- item", "    ~~~\n    sample", "  ~~~\n  thistle\n  ~~~\n\nAfter."),
        ("- item", "~~~\nsample", "After."),
    ],
    ids=[
        "list-tilde",
        "list-backtick",
        "ordered-list",
        "partial-overlap",
        "next-fence",
        "standalone-fence",
    ],
)
def test_fence_ends_with_its_claim(item, middle, last):
    with closing(Store(":memory:")) as store:
        project = store.create_project("alder")
        memory = Memory(store, lint_gate)
        accepted = []
        for index, text in enumerate((item, middle, last)):
            before = store.get_page(project.id, "ledger")
            history = store.page_versions(project.id, "ledger")
            try:
                filed = memory.note("alder", text, topic="Ledger")
            except RequestError as refusal:
                assert refusal.status in {400, 409, 422}
                filed = {"status": "rejected", "violations": [str(refusal)]}
            page = store.get_page(project.id, "ledger")
            if filed["status"] == "rejected":
                # The middle note or its successor can expose the unsafe boundary.
                assert index > 0 and len(accepted) == index
                assert filed["violations"] and page == before
                assert store.page_versions(project.id, "ledger") == history
                continue
            assert filed["status"] == "saved", filed["violations"]
            accepted.append(store.note_spans(filed["note_id"]))
            assert [(c.text, c.span_ids) for c in page.claims] == [
                (s.text, (s.id,)) for passages in accepted for s in passages
            ]
            assert_rendered(store, page)
            paragraphs = paragraphs_of(page)
            if index == 2:
                assert "After." in paragraphs


def test_plain_page_keeps_its_bytes():
    with closing(Store(":memory:")) as store:
        project = store.create_project("alder")
        memory = Memory(store, lint_gate)
        for text in ("- item", "    ordinary\n    sample", "  After."):
            assert memory.note("alder", text, topic="Ledger")["status"] == "saved"
        page = store.get_page(project.id, "ledger")
        assert_rendered(store, page)
        assert page.body.encode() == b"# Ledger\n\n- item\n\n    ordinary\n    sample\n\n  After.\n"
