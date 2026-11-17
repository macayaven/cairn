"""cairn.sources.fit (K4): a page's bounds as every kind of source reads them, measured as
cairn.memory measures a note before storing it, and the cuts and pieces kinds make with them.
Offline, over a store in a temporary file, with made-up notes."""

import pytest

import cairn.memory
import cairn.store.memory
from cairn.gate import lint_gate
from cairn.memory import Memory, PageFull
from cairn.sources import fit
from cairn.store import PAGE_CHARS_MAX, PAGE_CLAIMS_MAX, Store

WORD = "ledger "  # no fact in it


@pytest.fixture
def memory(tmp_path):
    store = Store(tmp_path / "cairn.db")
    store.create_project("ops")
    yield Memory(store, lint_gate)
    store.close()


def paragraph(length: int) -> str:
    return (WORD * (length // len(WORD) + 1))[: length - 1] + "x"


def test_the_bounds_are_the_store_s_read_when_asked(monkeypatch):
    assert (fit.claims(), fit.chars()) == (PAGE_CLAIMS_MAX, PAGE_CHARS_MAX)
    monkeypatch.setattr(cairn.store.memory, "PAGE_CHARS_MAX", 1_234)
    monkeypatch.setattr(cairn.store.memory, "PAGE_CLAIMS_MAX", 56)
    assert (fit.claims(), fit.chars()) == (56, 1_234)


def test_a_note_measured_at_the_bound_is_saved_and_one_character_over_is_refused(memory):
    body = paragraph(PAGE_CHARS_MAX)
    at = f"# Ledger\n\n{body}\n"
    assert fit.measure(at) == (1, PAGE_CHARS_MAX) and fit.fits(at)
    over = f"# Ledger two\n\n{body}y\n"
    assert fit.measure(over) == (1, PAGE_CHARS_MAX + 1) and not fit.fits(over)
    with pytest.raises(PageFull):
        memory.note("ops", over)
    assert memory.note("ops", at)["status"] == "saved"


def test_passages_are_measured_as_the_store_counts_claims(memory, monkeypatch):
    monkeypatch.setattr(cairn.store.memory, "PAGE_CLAIMS_MAX", 5)
    monkeypatch.setattr(cairn.memory, "PAGE_CLAIMS_MAX", 5)
    five = "# Items\n\n" + "\n".join(f"- item {k}" for k in "abcde") + "\n"
    six = "# Items two\n\n" + "\n".join(f"- item {k}" for k in "abcdef") + "\n"
    assert fit.measure(five)[0] == 5 and fit.fits(five) and not fit.fits(six)
    with pytest.raises(PageFull):
        memory.note("ops", six)
    assert memory.note("ops", five)["status"] == "saved"


def test_fitted_keeps_a_note_that_fits_and_cuts_one_that_does_not_with_its_line(monkeypatch):
    monkeypatch.setattr(cairn.store.memory, "PAGE_CHARS_MAX", 1_000)
    small = "# Note\n\nA line.\n"
    assert fit.fitted(small, "The rest is left out.") == small
    big = "# Note\n\n" + "\n\n".join(f"Paragraph {k:03d} {WORD * 10}" for k in range(40)) + "\n"
    made = fit.fitted(big, "The rest is left out.")
    assert made.startswith("# Note\n\nParagraph 000 ") and made.endswith("The rest is left out.\n")
    assert fit.fits(made) and fit.measure(made)[1] <= 1_000


def test_fitted_closes_a_fence_it_cuts_through(monkeypatch):
    monkeypatch.setattr(cairn.store.memory, "PAGE_CHARS_MAX", 500)
    code = "\n".join(f"line {k:04d} of the dump" for k in range(200))
    made = fit.fitted(f"# Dump\n\n```\n{code}\n```\n", "The rest is left out.")
    assert fit.fits(made) and made.endswith("```\n\nThe rest is left out.\n")


@pytest.mark.parametrize(
    "text",
    [
        "one long line " * 2_000,
        "\n".join(f"line {k:04d} of a long paragraph" for k in range(2_000)),
        "x" * 30_001,
    ],
    ids=["one_line", "many_lines", "no_space"],
)
def test_pieces_of_a_long_passage_each_within_the_bound_lose_nothing(text):
    made = fit.pieces(text, 1_000)
    assert len(made) > 1 and all(len(piece) <= 1_000 for piece in made)
    assert "".join(" ".join(made).split()) == "".join(text.split())


def test_pieces_of_a_fence_are_each_a_closed_fence():
    code = "\n".join(f"line {k:04d} of the dump" for k in range(2_000))
    made = fit.pieces(f"```python\n{code}\n```", 1_000)
    assert len(made) > 1
    for piece in made:
        assert len(piece) <= 1_000 and piece.startswith("```python\n") and piece.endswith("\n```")
        assert fit.measure(piece)[0] == 1
    inner = [line for piece in made for line in piece.splitlines()[1:-1]]
    assert inner == code.splitlines()


def test_opened_up_cuts_only_the_passages_longer_than_the_bound_into_passages_that_fit():
    long = " ".join(f"word{k:05d}" for k in range(500))  # 4,999 characters, one paragraph
    text = f"Short one.\n\n{long}\n\n- an item\n- another item\n\nShort two."
    made = fit.opened_up(text, 1_000)
    assert made.startswith("Short one.\n\n") and made.endswith("- another item\n\nShort two.")
    passages = [s.text for s in fit._spans(made) if s.kind in fit.PASSAGE_KINDS]
    assert all(len(p) <= 1_000 for p in passages) and len(passages) == 4 + 5
    assert made.split() == text.split()
    assert fit.opened_up(text, 10_000) == text


SHAPES = {
    "quote": "\n".join(f"> line {k:04d} of an earlier message quoted" for k in range(400)),
    "table": "| n | what |\n| --- | --- |\n"
    + "\n".join(f"| {k:04d} | entry {k} of the ledger |" for k in range(400)),
    "bullet": "- one item\n" + "\n".join(f"  line {k:04d} of the same item" for k in range(400)),
    "one_long_line": "- " + " ".join(f"word{k:05d}" for k in range(600)),
}


@pytest.mark.parametrize("shape", SHAPES, ids=list(SHAPES))
def test_opened_up_leaves_every_passage_within_the_bound_whatever_its_shape(shape):
    text = f"Before it.\n\n{SHAPES[shape]}\n\nAfter it."
    made = fit.opened_up(text, 1_000)
    passages = [s.text for s in fit._spans(made) if s.kind in fit.PASSAGE_KINDS]
    assert len(passages) > 3 and all(len(p) <= 1_000 for p in passages), shape
    assert made.startswith("Before it.\n\n") and made.endswith("\n\nAfter it.")
    assert made.split() == text.split()
