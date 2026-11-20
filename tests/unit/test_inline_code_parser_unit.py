"""Inline code and open fences are what the Markdown parser says (second report, findings 2
and 8, and "B3 does not hold").

A block boundary is not only a blank line: a quote's line of ">" alone, a list item that
starts inside another, a heading, a table's cell. The parser (markdown-it-py, as the web view
renders) is asked, through spans.code_spans, given the passage as the note has it, its quote
and list markers included. Made-up notes."""

import pytest
from markdown_it import MarkdownIt

from cairn.arrange import repeat_groups, tidy_page
from cairn.compiler import render_body
from cairn.facts import normalize
from cairn.gate import lint_gate, text_reading
from cairn.memory import Memory
from cairn.spans import chunk, code_spans
from cairn.store import Claim, Store

QUOTED = "> Stray `\n>\n> Keep `{name}` here."
SPACED, SINGLE = "a  b.txt", "a b.txt"
# The same fault one step away: a block that ends with no blank line before the next.
NEIGHBOURS = {
    "quote": QUOTED,
    "nested quote": "> > Stray `\n> >\n> > Keep `{name}` here.",
    "list item in a list item": "- Stray `\n  - Keep `{name}` here.",
    "heading in a quote": "> # Stray `\n> Keep `{name}` here.",
    "item after item": "- Stray `\n- Keep `{name}` here.",
    "table cells": "| Stray ` | Keep `{name}` here |\n|---|---|",
}


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def code_contents(text: str) -> list[str]:
    md = MarkdownIt("commonmark").enable("table")
    return [
        child.content
        for token in md.parse(text)
        if token.type == "inline"
        for child in token.children or ()
        if child.type == "code_inline"
    ]


@pytest.mark.parametrize("shape", NEIGHBOURS, ids=list(NEIGHBOURS))
def test_markdown_reads_the_name_as_code_and_so_does_cairn(shape):
    spaced, single = (NEIGHBOURS[shape].format(name=n) for n in (SPACED, SINGLE))
    assert code_contents(spaced) == [SPACED]  # the parser's own reading, apart from cairn
    found = code_spans(spaced)
    assert found is not None and [spaced[a:b] for a, b in found] == [f"`{SPACED}`"]
    assert text_reading(spaced) != text_reading(single)
    assert normalize(spaced) != normalize(single)


@pytest.mark.parametrize("shape", NEIGHBOURS, ids=list(NEIGHBOURS))
def test_the_two_spellings_are_no_repeat(shape):
    texts = [NEIGHBOURS[shape].format(name=n) for n in (SPACED, SINGLE)]
    # the passage that holds the name (a list's items are passages of their own)
    spans = [next(s for s in chunk(f"n{n}", t) if "Keep" in s.text) for n, t in enumerate(texts)]
    claims = [Claim(id=f"cl_{n:016x}", text=s.text, span_ids=(s.id,)) for n, s in enumerate(spans)]
    assert repeat_groups(claims, {s.id: s for s in spans}) == []


def test_the_reviewers_two_quoted_notes_stay_two_claims_through_a_tidy(store):
    store.create_project("alpha")
    memory = Memory(store, lint_gate)
    first, second = QUOTED.format(name=SPACED), QUOTED.format(name=SINGLE)
    slug = memory.note("alpha", first, title="Files")["page"]
    memory.note("alpha", second, title="Files", topic="Files")
    project = store.get_project("alpha")
    tidied = tidy_page(store, project.id, slug)
    assert (tidied.status, tidied.merged) == ("unchanged", 0)
    page = store.get_page(project.id, slug)
    assert [claim.text for claim in page.claims] == [first, second]
    assert len({claim.span_ids for claim in page.claims}) == 2
    assert all(len(claim.span_ids) == 1 for claim in page.claims)


def test_a_backtick_markdown_leaves_as_text_reads_the_passage_exactly():
    """A run the parser pairs with nothing is a backtick a reader sees; a line the note puts
    before the passage may pair it (a quote's lazy line), so the passage is compared exactly."""
    assert code_spans("No `code  here.") is None
    assert text_reading("No `code  here.") != text_reading("No `code here.")
    assert text_reading("No \\`code  here.") == text_reading("No \\`code here.")  # escaped


def test_no_backtick_needs_no_parser():
    assert code_spans("Plain words, no code.") == []


@pytest.mark.parametrize(
    "text",
    ["Keep `a  b` here.\r\nAnd `c  d`.", "Keep `a\tb` and\r`c  d`."],
    ids=["crlf", "lone-cr"],
)
def test_carriage_returns_keep_the_places(text):
    found = code_spans(text)
    assert found is not None
    wanted = sorted(m for m in ("`a  b`", "`c  d`", "`a\tb`") if m in text)
    assert sorted(text[a:b] for a, b in found) == wanted


def body_paragraphs(body: str) -> list[str]:
    tokens = MarkdownIt("commonmark").parse(body)
    return [tokens[n + 1].content for n, t in enumerate(tokens) if t.type == "paragraph_open"]


@pytest.mark.parametrize(
    "closer",
    ["\u00a0~~~", "\u2003~~~", "\u00a0```", "~~~\u00a0x", "\t~~~"],
    ids=["nbsp", "em-space", "nbsp-backticks", "nbsp-after", "tab"],
)
def test_the_next_note_after_a_fence_markdown_leaves_open_is_prose(closer):
    """Finding 8: cairn's splitter takes a closer a no-break space precedes; Markdown does not.
    The body closes the fence, so the next note is a paragraph."""
    opener = "```" if "`" in closer else "~~~"
    first = chunk("n0", f"{opener}\nsample\n{closer}")
    second = chunk("n1", "After the fence.")
    claims = [
        Claim(id=f"cl_{n:016x}", text=s.text, span_ids=(s.id,))
        for n, s in enumerate([*first, *second])
    ]
    spans = {s.id: s for s in [*first, *second]}
    body = render_body("Files", claims, spans)
    assert "After the fence." in body_paragraphs(body)


@pytest.mark.parametrize(
    "text", ["a\rb\rc\r~~~\nx", "~~~\rx", "- a\r  ```\r  x"], ids=["lone-cr", "cr-opener", "item"]
)
def test_a_fence_after_lone_carriage_returns_is_closed(text):
    """The parser breaks a line at a lone CR; the closer is found on the parser's lines."""
    first, second = chunk("n0", text), chunk("n1", "After the fence.")
    claims = [
        Claim(id=f"cl_{n:016x}", text=s.text, span_ids=(s.id,))
        for n, s in enumerate([*first, *second])
    ]
    body = render_body("Files", claims, {s.id: s for s in [*first, *second]})
    assert "After the fence." in body_paragraphs(body)


def test_an_archive_written_before_the_parser_closed_fences_imports(tmp_path, monkeypatch):
    """A body cairn wrote before this round (compiler.render_body_before_review_three: the
    fence after a no-break space left open) imports, and takes the current rendering."""
    import io

    import cairn.compiler
    from cairn.compiler import compile_note

    note_text = "~~~\nsample\n\u00a0~~~\n\nAfter the fence."
    view = Store(tmp_path / "old.db")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                cairn.compiler, "render_body", cairn.compiler.render_body_before_review_three
            )
            project = view.create_project("alpha")
            note, _ = view.add_note(project.id, note_text, title="Files")
            assert compile_note(view, note).status == "saved"
        old = view.get_page(project.id, "files").body
        assert "After the fence." not in body_paragraphs(old)  # the old body, as stored
        archive = io.BytesIO()
        view.export(archive)
    finally:
        view.close()
    target = Store(tmp_path / "new.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        body = target.get_page(target.get_project("alpha").id, "files").body
        assert "After the fence." in body_paragraphs(body)
    finally:
        target.close()
