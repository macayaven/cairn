"""Shared test helpers extracted from test_assembled_fence_unit."""

import io

import pytest
from markdown_it import MarkdownIt

import cairn.compiler
import cairn.spans
from cairn.compiler import (
    render_body_before_review_four,
)
from cairn.spans import claim_id
from cairn.store import Claim, Store

READERS = {
    "raw HTML off, as the web view reads": MarkdownIt("commonmark", {"html": False}),
    "raw HTML on": MarkdownIt("commonmark", {"html": True}),
}

AT_THE_MARGIN = "# Ops\n\n- item\n\n    ~~~\n    sample\n~~~\n\n  After.\n"  # a closer that is none


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


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


def exported(store: Store) -> bytes:
    archive = io.BytesIO()
    store.export(archive)
    return archive.getvalue()


def imported(store: Store, tmp_path) -> Store:
    target = Store(tmp_path / "imported.db")
    target.import_export(io.BytesIO(exported(store)))
    return target


def no_closer(monkeypatch) -> None:
    """The parser's answer stood in for (no page is known whose fence the closer does not
    hold in): the closer is written at the margin, where it ends the list and opens a fence
    at the top, so the claim after it is code still."""
    assert not prose(AT_THE_MARGIN, "After.")
    monkeypatch.setattr(cairn.spans, "_closer", lambda opener, run: run)


def triples(count: int) -> list[str]:
    """`count` times the reviewer's three notes, each with words of its own."""
    return [
        text
        for n in range(count)
        for text in (f"- item {n}", f"    ~~~\n    sample {n}", f"  After {n}.")
    ]


def saved_by_23c24229(store: Store, texts: list[str]):
    """The page `ops` of project `alpha` as 23c24229 saved these notes: its gate took a body
    with any number of fences that run from one claim into the next, today's refuses the
    seventeenth, so the version is built at the store's level with that build's renderer."""
    project = store.get_project("alpha")
    notes = [store.add_note(project.id, text, title="Ops", topic="Ops")[0] for text in texts]
    spans = {s.id: s for note in notes for s in store.note_spans(note.id)}
    claims = [Claim(id=claim_id(i), text=s.text, span_ids=(i,)) for i, s in spans.items()]
    body = render_body_before_review_four("Ops", claims, spans)
    return store.save_page(project.id, "ops", "Ops", body, claims), notes


def parses(monkeypatch, run) -> int:
    """How many times `run` has the package's parser parse."""
    calls, parse = [], cairn.spans._PARSER.parse
    with monkeypatch.context() as patch:
        patch.setattr(
            cairn.spans._PARSER, "parse", lambda *a, **k: calls.append(1) or parse(*a, **k)
        )
        run()
    return len(calls)
