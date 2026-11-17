"""Sentences of the guides about G1's bounds (tests/acceptance/test_g1_gate_bounds.py), each
pinned here so the guides cannot go false unnoticed: the facts a note holds and the characters
of claims a page holds (security and privacy guides), and `serve --compiles` (run guide). Each
test finds its sentences in the guide as a person reads it (guide_source), then asks cairn."""

import pytest
import uvicorn
from guide_source import guide_source

import cairn.store.memory
from cairn import server
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.memory import ManyFacts, Memory, PageFull
from cairn.store import FACTS_MAX, PAGE_CHARS_MAX, PAGE_CLAIMS_MAX, Store
from cairn.web import cli
from cairn.web.turns import COMPILES


def flat(text: str) -> str:
    return " ".join(text.split())


def says(guide: str, *sentences: str) -> None:
    """Each sentence is in the guide, as a person reads it."""
    text = flat(guide_source(guide))
    for sentence in sentences:
        assert flat(sentence) in text, sentence


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    s.create_project("ops")
    yield s
    s.close()


def test_a_note_holds_at_most_30000_facts(store):
    says(
        "security",
        "On every install a note holds at most 30,000 facts: numbers, dates, versions, paths,"
        " links, flags and pieces of code. A note with more is refused before anything of it"
        " is stored, and the answer gives the count and the bound, never the note's text.",
    )
    assert FACTS_MAX == 30_000
    numbers = " ".join(str(10_000 + i) for i in range(FACTS_MAX))
    links = " ".join(f"https://h.example/{i}" for i in range(3))
    flags = "--dry-run /srv/app/run.sh v1.2.3 2099-03-02 `run --fast`"
    memory = Memory(store, lint_gate)
    with pytest.raises(ManyFacts) as many:
        memory.note("ops", f"# Kinds\n\n{numbers}\n\n{links} {flags}\n")
    said = str(many.value)
    assert "30,008 facts" in said and "30,000" in said and "10000" not in said
    assert store.list_notes(store.get_project("ops").id) == []
    assert memory.note("ops", f"# Kinds\n\n{numbers}\n")["status"] == "saved"  # at the bound


def test_a_page_holds_at_most_400000_characters_of_claims(store, monkeypatch):
    says("privacy", "A page holds at most 2,000 claims and 400,000 characters of claims.")
    says(
        "security",
        "On every install a page holds at most 2,000 claims",
        "A page also holds at most 400,000 characters of claims, since every save lints and"
        " indexes the whole page again; a note that would take its page past that is refused"
        " the same way, and the answer says which bound it met.",
        "A page that already held more when this bound came stays as it is: it can still be"
        " read, and notes can still be forgotten from it, but it takes no more notes, and the"
        " answer says so.",
    )
    assert (PAGE_CLAIMS_MAX, PAGE_CHARS_MAX) == (2_000, 400_000)
    memory = Memory(store, lint_gate)
    words = " ".join(["the ledger holds every entry once"] * 3_000)  # 101,999 characters
    for n in range(3):
        memory.note("ops", f"# Ledger\n\nRun {n}: {words}\n")
    with pytest.raises(PageFull) as full:
        memory.note("ops", f"# Ledger\n\nRun 3: {words}\n")
    said = str(full.value)
    assert full.value.status == 409 and "400,000 characters" in said and "claims a" not in said
    # A page larger already: it stays, is read, a note is forgotten from it, and takes no more.
    project = store.get_project("ops")
    monkeypatch.setattr(cairn.store.memory, "PAGE_CHARS_MAX", 10 * PAGE_CHARS_MAX)
    note, _ = store.add_note(project.id, f"# Ledger\n\nRun 9: {words}\n")
    assert compile_note(store, note, lint_gate).status == "saved"
    monkeypatch.undo()
    with pytest.raises(PageFull) as full:
        memory.note("ops", "# Ledger\n\nOne more line.\n")
    assert "stays as it is" in str(full.value) and "takes no more notes" in str(full.value)
    assert store.get_page(project.id, "ledger") is not None
    assert list(store.forget_note(note.id).rebuilt) == ["ledger"]


def test_serve_compiles_two_notes_at_a_time_unless_compiles_says(tmp_path, monkeypatch):
    says(
        "run",
        "`serve` compiles at most two notes at a time. A note that arrives while two are"
        " compiling waits its turn, in the order notes came, and is answered when its own"
        " compile is done, as before.",
        "Reads, `/healthz`, sign-in and everything else that is not a note's compile never wait"
        " behind them. Notes from sources, and the waiting notes `serve` files by itself, take"
        " their turns too.",
        "`--compiles N` sets how many at a time, 1 or more",
        "N must be a whole number or `serve` stops at start.",
    )
    assert COMPILES == 2
    made = []
    real = cli.create_app

    def create(*args, **kwargs):
        app = real(*args, **kwargs)
        made.append((app.state.compiles.n, app.state.filer._compiles is app.state.compiles))
        return app

    monkeypatch.setattr(cli, "create_app", create)
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: None)
    db = str(tmp_path / "cairn.db")
    assert server.main(["serve", "--db", db, "--no-tidy"]) == 0
    assert server.main(["serve", "--db", db, "--no-tidy", "--compiles", "1"]) == 0
    assert made == [(2, True), (1, True)]  # the filer of `serve` takes the same turns
    for wrong in ("0", "2.5"):
        with pytest.raises(SystemExit):
            server.main(["serve", "--db", db, "--compiles", wrong])
