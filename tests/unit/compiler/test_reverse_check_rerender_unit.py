"""The reverse check is equality with a re-render (second report, finding 1).

Every body a gate sees is written by compiler.render_body from the page's claims: the
extractive compile, a model's compile (the model drafts claims, never a body), a tidy and
the arranger. So the check asks one question: is the body what render_body writes of these
claims and these passages? No line of it is read on its own. Made-up notes."""

import pytest

from cairn.compiler import CONTENT_KINDS, CandidatePage, extractive, provenance_gate, render_body
from cairn.gate import lint_gate, strict_lint_gate, unclaimed_line, verbatim_gate
from cairn.memory import Memory
from cairn.spans import chunk
from cairn.store import Store

GATES = (provenance_gate, lint_gate, strict_lint_gate, verbatim_gate)
REPEATS = "Alpha.\n\nBeta.\nGamma.\n\nAlpha.\nBeta."


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def page_of(title: str, text: str) -> tuple[CandidatePage, dict]:
    spans = {s.id: s for s in chunk("cap_000000000001", text)}
    claims = extractive(title, [s for s in spans.values() if s.kind in CONTENT_KINDS]).claims
    body = render_body(title, claims, spans)
    return CandidatePage(slug="p", title=title, body=body, claims=claims), spans


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.__name__)
def test_a_paragraph_repeated_inside_a_later_one_passes_every_gate(gate):
    page, spans = page_of("Ops", REPEATS)
    assert len(page.claims) == 3
    assert unclaimed_line(page, spans) is None
    assert gate(page, spans).passed, gate(page, spans).violations


def test_the_reviewers_note_saves_a_page_of_three_claims(store):
    for name, gate in (("ops", provenance_gate), ("dev", lint_gate)):
        project = store.create_project(name)
        answer = Memory(store, gate).note(name, REPEATS, topic="Ops")
        page = store.get_page(project.id, answer["page"])
        assert page is not None, answer
        assert [c.text for c in page.claims] == ["Alpha.", "Beta.\nGamma.", "Alpha.\nBeta."]


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.__name__)
@pytest.mark.parametrize("fence", ["~~~", "```", "   ~~~~", "``` sh"])
def test_a_stray_fence_after_a_plain_claim_is_refused(gate, fence):
    page, spans = page_of("Ops", "First words.\n\nSecond words.")
    body = page.body.replace("First words.\n", f"First words.\n{fence}\n", 1)
    assert body != page.body
    stray = page.model_copy(update={"body": body})
    assert unclaimed_line(stray, spans) == 4
    assert not gate(stray, spans).passed


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.__name__)
def test_a_closer_the_renderer_writes_after_an_open_fence_passes(gate):
    page, spans = page_of("Ops", "~~~\nsample\n\nAfter.")
    assert page.body == "# Ops\n\n~~~\nsample\n\nAfter.\n~~~\n"  # one passage, closed
    page, spans = page_of("Ops", "Before.\n\n```sh\nmake")
    assert page.body.endswith("```sh\nmake\n```\n")
    assert gate(page, spans).passed, gate(page, spans).violations


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.__name__)
@pytest.mark.parametrize(
    "change",
    [
        ("## B\n", "### B\n"),  # the right heading at the wrong depth
        ("## A\n\nIn A.\n\n## B\n", "## A\n\n## B\n\nIn A.\n\n"),  # a heading moved
        ("## B\n\nIn B.\n", "In B.\n\n## B\n"),  # a heading after its claim
        ("In A.\n", "In A.\n\n## A\n"),  # a heading of the path written twice
    ],
    ids=["depth", "moved", "after", "twice"],
)
def test_a_heading_in_the_wrong_place_or_depth_is_refused(gate, change):
    page, spans = page_of("Ops", "## A\n\nIn A.\n\n## B\n\nIn B.")
    old, new = change
    assert old in page.body
    moved = page.model_copy(update={"body": page.body.replace(old, new, 1)})
    assert unclaimed_line(moved, spans) is not None
    assert not gate(moved, spans).passed


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.__name__)
def test_claims_in_another_order_than_the_page_says_are_refused(gate):
    page, spans = page_of("Ops", "One.\n\nTwo.")
    swapped = page.model_copy(update={"body": "# Ops\n\nTwo.\n\nOne.\n"})
    assert unclaimed_line(swapped, spans) == 3
    assert not gate(swapped, spans).passed


def test_a_body_with_white_space_the_renderer_does_not_write_is_refused():
    page, spans = page_of("Ops", "One.\n\nTwo.")
    for body in (page.body.rstrip("\n"), page.body + "\n", page.body.replace("\n\n", "\n\n\n")):
        assert unclaimed_line(page.model_copy(update={"body": body}), spans) is not None
