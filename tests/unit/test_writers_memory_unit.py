"""A1, part A in what memory answers and shows: recall, page and evidence name who wrote each
note a claim cites and whether it is outside text; the page says so in words; the MCP
instructions say once what recalled text is."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from cairn.gate import lint_gate
from cairn.memory import WRITTEN_BY_MAX, Memory, is_outside, source_via
from cairn.sources import KINDS
from cairn.store import Store
from cairn.store.models import Claim
from cairn.web.mcp import TREAT_AS_INFORMATION, instructions
from cairn.web.ui import writer_words

CLOCK = datetime(2099, 2, 3, 4, 5, tzinfo=UTC)
A_SOURCE_VIA = sorted(kind.via for kind in KINDS.values())[0]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db", clock=lambda: CLOCK)
    yield s
    s.close()


def note(view, project_id: str, text: str, via: str | None = None) -> str:
    note, created = view.add_note(project_id, f"# Note\n\n{text}\n", via=via)
    assert created
    return note.id


def paragraph(store: Store, note_id: str) -> str:
    """The id of the note's first paragraph span."""
    return next(s.id for s in store.note_spans(note_id) if s.kind == "paragraph")


def one_claim(
    store: Store, project_id: str, span_ids: list[str], text: str = "Shared words."
) -> None:
    claim = Claim(id="cl_000000000001", text=text, span_ids=tuple(span_ids))
    store.save_page(project_id, "shared", "Shared", f"# Shared\n\n{text}\n", [claim], {})


# Outside text


def test_a_sources_note_is_outside_and_an_unrecorded_one_by_its_via():
    assert source_via() == frozenset(kind.via for kind in KINDS.values())
    assert is_outside("source", None) and is_outside("source", "mcp")
    assert is_outside("unknown", A_SOURCE_VIA)
    assert not is_outside("unknown", "mcp") and not is_outside("unknown", None)
    for kind in ("person", "key", "agent", "operator"):  # `via` is the caller's word
        assert not is_outside(kind, A_SOURCE_VIA), kind


def test_a_source_kind_added_at_run_time_counts(monkeypatch):
    monkeypatch.setitem(KINDS, "a1_unit_feed", replace(KINDS["github"], via="x1"))
    assert is_outside("unknown", "x1")


# What recall, page and evidence answer


def test_a_hit_names_each_note_once_in_the_order_its_claim_first_cites_it(store):
    project = store.create_project("ops")
    key = store.acting_as("key", "laptop", "key_000000000001")
    mail = store.writing_as("source", "team-mail")
    first = note(key, project.id, "Shared words from the key.", via="api")
    second = note(mail, project.id, "Shared words from the mail.", via=A_SOURCE_VIA)
    spans = [paragraph(store, second), paragraph(store, first), paragraph(store, second)]
    one_claim(store, project.id, spans)

    (hit,) = Memory(store, lint_gate).recall("shared words")["hits"]
    assert hit["written_by"] == [
        {"note": second, "kind": "source", "name": "team-mail", "via": A_SOURCE_VIA,
         "outside": True},
        {"note": first, "kind": "key", "name": "laptop", "via": "api", "outside": False},
    ]  # fmt: skip
    assert hit["outside"] is True and "written_by_more" not in hit


def test_a_hit_names_at_most_three_notes_and_counts_the_rest(store):
    project = store.create_project("ops")
    notes = [note(store, project.id, f"Shared words, take {n}.") for n in range(WRITTEN_BY_MAX + 2)]
    one_claim(store, project.id, [paragraph(store, n) for n in notes])

    (hit,) = Memory(store, lint_gate).recall("shared words")["hits"]
    assert [entry["note"] for entry in hit["written_by"]] == notes[:WRITTEN_BY_MAX]
    assert {entry["kind"] for entry in hit["written_by"]} == {"operator"}
    assert hit["written_by_more"] == 2 and hit["outside"] is False


def test_a_span_the_store_does_not_hold_names_nothing(store):
    project = store.create_project("ops")
    kept = note(store, project.id, "Shared words kept.")
    one_claim(store, project.id, ["sp_000000000000", paragraph(store, kept)])

    hits = Memory(store, lint_gate).recall("shared words")["hits"]
    assert [entry["note"] for hit in hits for entry in hit["written_by"]] == [kept]


def test_a_page_marks_each_claim_and_a_trace_names_each_spans_writer(store, tmp_path):
    project = store.create_project("ops")
    person = store.acting_as("session", "Cy <cy@example.test>")
    mine = note(person, project.id, "Shared words from Cy.", via="web")
    old = note(store, project.id, "Shared words from before.", via=A_SOURCE_VIA)
    con = sqlite3.connect(tmp_path / "cairn.db")  # stored before writers were recorded
    con.execute("DELETE FROM note_writers WHERE note_id = ?", (old,))
    con.commit()
    con.close()
    one_claim(store, project.id, [paragraph(store, mine)], "Shared words from Cy.")
    claim = Claim(id="cl_000000000002", text="From before.", span_ids=(paragraph(store, old),))
    page = store.get_page(project.id, "shared")
    store.save_page(project.id, "shared", "Shared", page.body, [*page.claims, claim], {})

    memory = Memory(store, lint_gate)
    answer = memory.page("ops", "shared")
    assert [(c["text"], c["outside"]) for c in answer["claims"]] == [
        ("Shared words from Cy.", False),
        ("From before.", True),  # no writer, and a source kind's `via`
    ]
    (span,) = memory.evidence("ops", "shared", "cl_000000000001")["passages"]
    assert (span["written_by"], span["via"], span["outside"]) == (
        {"kind": "person", "name": "Cy"},
        "web",
        False,
    )
    (span,) = memory.evidence("ops", "shared", "cl_000000000002")["passages"]
    assert (span["written_by"], span["via"], span["outside"]) == (
        {"kind": "unknown", "name": None},
        A_SOURCE_VIA,
        True,
    )


# The page's words


@pytest.mark.parametrize(
    ("kind", "name", "said"),
    [
        ("person", "Ada", "written by Ada"),
        ("person", "", "written by a person"),
        ("key", "laptop", "written by the key laptop"),
        ("agent", "probe", "written by the agent probe"),
        ("source", "team-mail", "from the source team-mail"),
        ("source", None, "from a source"),
        ("unknown", None, "writer not recorded"),
    ],
)
def test_each_writer_is_said_with_its_kind_as_a_word(kind, name, said):
    assert writer_words(kind, name) == said


def test_the_operators_words_are_its_own():
    said = writer_words("operator", None)
    for other in ("writer not recorded", "outside text", "from a source", "None", "cairn"):
        assert other not in said


# What agents are told


@pytest.mark.parametrize("product", ["cairn", "Field Notes"])
def test_the_instructions_end_with_the_paragraph_on_recalled_text(product):
    paragraphs = [p.strip() for p in instructions(product).split("\n\n")]
    assert paragraphs[-1] == TREAT_AS_INFORMATION
    assert "cairn" not in TREAT_AS_INFORMATION.lower()


# Who the web writes as: from how the caller was admitted


def test_each_caller_writes_as_itself_and_makes_with_the_key_it_came_with(store):
    from cairn.store import DEFAULT_ACCOUNT_ID
    from cairn.store.models import Principal
    from cairn.web.auth import _writing

    def principal(kind: str, label: str | None = None, user_id: str | None = None):
        return Principal(account_id=DEFAULT_ACCOUNT_ID, kind=kind, label=label, user_id=user_id)

    person = SimpleNamespace(name="Cy <cy@example.test>")
    cases = [  # principal, person, actor id, key id: writer, made through
        (principal("key", "laptop"), None, "key_1", None, ("key", "laptop"), "key_1"),
        (principal("token", "probe"), None, "grant_1", None, ("agent", "probe"), None),
        (principal("session", "desk"), None, "key_2", None, ("key", "desk"), "key_2"),
        (principal("session", "desk", "usr_1"), person, "usr_1", "key_3", ("key", "desk"), "key_3"),
        (principal("session", None, "usr_1"), person, "usr_1", None, ("person", "Cy"), None),
    ]  # fmt: skip
    for who, someone, actor_id, key_id, writer, made_through in cases:
        view = _writing(store, who, someone, actor_id, key_id)
        assert (view.writer, view.made_through) == (writer, made_through), who
