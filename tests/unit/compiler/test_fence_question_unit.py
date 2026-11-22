"""The one question still asked of the Markdown parser (CORE5): does a claim's text leave a
code fence open, so the body must close it before the next claim?

It is asked without a private character: a known line is written after the claim's text and
a blank line, and the fence is open when a fence token the claim's text opened covers that
line in the parser's line map. A closer is checked the same way. Made-up notes."""

import pytest
from markdown_it import MarkdownIt

import cairn.compiler
from cairn.compiler import compile_note, render_body
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import FenceLeftOpen, chunk, open_fence_closer
from cairn.store import Store

READERS = {
    "raw HTML off, as the web view reads": MarkdownIt("commonmark", {"html": False}),
    "raw HTML on": MarkdownIt("commonmark", {"html": True}),
}
# The cross-reader's input: a rule-delimited block (not front matter) that holds a closed
# tilde fence around the old probe character, then a backtick fence it never closes.
READER = "---\n~~~\n\n~~~\n```\nx\n---"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def top_paragraphs(body: str, reader: MarkdownIt) -> list[str]:
    tokens = reader.parse(body)
    return [
        tokens[n + 1].content
        for n, token in enumerate(tokens)
        if token.type == "paragraph_open" and token.level == 0
    ]


@pytest.mark.parametrize("reader", list(READERS))
def test_the_claim_after_a_fence_its_note_leaves_open_is_prose(store, reader):
    store.create_project("alpha")
    memory = Memory(store, lint_gate)
    slug = memory.note("alpha", READER, title="Ops", topic="Ops")["page"]
    answer = memory.note("alpha", "After.", title="Ops", topic="Ops")
    assert answer["status"] == "saved", answer["violations"]
    page = store.get_page(store.get_project("alpha").id, slug)
    assert [c.text for c in page.claims] == [READER, "After."]
    assert top_paragraphs(page.body, READERS[reader])[-1] == "After."


CLOSERS = {
    "no fence": ("Ada keeps the log.", None),
    "a closed fence": ("```\nx\n```", None),
    "an open backtick fence": ("```\nx", "```"),
    "an open tilde fence": ("~~~~\nx", "~~~~"),
    "the probe character in closed code, then an open fence": (READER, "```"),
    "a fence open in a list item": ("- a\n  ```\n  x", "  ```"),
    "a fence a quote holds": ("> ```\n> x", None),
    "a backtick run that is inline code": ("```a```", None),
    "a closer a tab puts past Markdown's limit": ("```\nx\n\t```", "```"),
}


@pytest.mark.parametrize("name", list(CLOSERS))
def test_the_closer_is_the_one_the_parser_needs(name):
    text, closer = CLOSERS[name]
    assert open_fence_closer(text) == closer


@pytest.mark.parametrize("name", list(CLOSERS))
def test_after_the_closer_the_next_claim_is_prose_for_every_reader(name):
    text, _ = CLOSERS[name]
    spans = {s.id: s for s in [*chunk("n1", text), *chunk("n2", "After.")]}
    claims = [
        cairn.compiler.Claim(id=f"cl_{n:016x}", text=s.text, span_ids=(s.id,))
        for n, s in enumerate(spans.values())
    ]
    body = render_body("T", claims, spans)
    for reader in READERS.values():
        assert top_paragraphs(body, reader)[-1] == "After."


def test_a_claim_no_closer_closes_is_refused_with_a_reason_of_its_own(store, monkeypatch):
    """No text found so far leaves a fence the parser reads open after its own closer at the
    opener's column or at the margin (searched over generated texts in the evaluation of
    this change); the parser's answer is stood in for here, to show what the compiler does
    with one: it refuses the note and saves nothing, naming the claim, never its text."""

    def unclosable(text: str) -> str | None:
        if "Never closed" in text:
            raise FenceLeftOpen
        return None

    import cairn.gate

    monkeypatch.setattr(cairn.compiler, "open_fence_closer", unclosable)
    monkeypatch.setattr(cairn.gate, "open_fence_closer", unclosable)
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, "Fine.\n\n```\nNever closed", title="Ops")
    result = compile_note(store, note, gate=lint_gate)
    assert result.status == "rejected"
    (violation,) = result.gate.violations
    assert violation.startswith("compile.fence_left_open: cl_")
    assert "Never" not in violation
    assert store.get_page(project.id, "ops") is None


def test_every_gate_refuses_a_page_that_holds_such_a_claim(monkeypatch):
    """The same stand-in: a tidy, an arrangement or a compile is gated, and every gate names
    the claim, never its text, so no path saves a page whose next claim would read as code."""
    import cairn.gate
    from cairn.compiler import CandidatePage, provenance_gate
    from cairn.gate import strict_lint_gate, verbatim_gate

    def unclosable(text: str) -> str | None:
        if "Never closed" in text:
            raise FenceLeftOpen
        return None

    spans = {s.id: s for s in [*chunk("n1", "```\nNever closed"), *chunk("n2", "After.")]}
    claims = tuple(
        cairn.compiler.Claim(id=f"cl_{n:016x}", text=s.text, span_ids=(s.id,))
        for n, s in enumerate(spans.values())
    )
    page = CandidatePage(slug="t", title="T", body=render_body("T", claims, spans), claims=claims)
    monkeypatch.setattr(cairn.gate, "open_fence_closer", unclosable)
    for gate in (provenance_gate, lint_gate, strict_lint_gate, verbatim_gate):
        verdict = gate(page, spans)
        assert not verdict.passed, gate.__name__
        assert [v for v in verdict.violations if v.startswith("compile.fence_left_open")] == [
            f"compile.fence_left_open: {claims[0].id}: the passage leaves a code fence open"
            " that no closing line after it closes, so the claims after it would read as code"
        ], gate.__name__
        assert not any("Never" in v for v in verdict.violations)


def test_an_import_refuses_a_version_that_holds_such_a_claim(store, tmp_path, monkeypatch):
    import io

    import cairn.gate

    store.create_project("alpha")
    memory = Memory(store, lint_gate)
    assert memory.note("alpha", "```\nNever closed\n```", title="Ops", topic="Ops")["status"] == (
        "saved"
    )
    archive = io.BytesIO()
    store.export(archive)
    monkeypatch.setattr(
        cairn.gate,
        "open_fence_closer",
        lambda text: (_ for _ in ()).throw(FenceLeftOpen) if "Never" in text else None,
    )
    target = Store(tmp_path / "imported.db")
    try:
        with pytest.raises(ValueError) as refused:
            target.import_export(io.BytesIO(archive.getvalue()))
        assert "'ops' version 1" in str(refused.value)
        assert "Never" not in str(refused.value)
    finally:
        target.close()
