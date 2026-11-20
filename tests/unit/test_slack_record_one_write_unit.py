"""A note Slack creates and the row that says so are one write (cairn.slack.people.write,
cairn.store.slack): a store fault between them leaves no note and releases the filed
message's claim; a message that is another note's at the moment of the write stores nothing;
and a Slack write costs the statements pinned here. Every name is made up."""

import sqlite3

import pytest
from test_slack_commands_unit import ADA_SLACK, Bench, ask, said
from test_slack_shortcuts_unit import CEDAR, CONVERSATION, cedar, handle, replied

from cairn.slack import people
from cairn.store import Store
from cairn.store.base import _iso
from cairn.store.slack import CLAIMED, FORGET_AFTER

TIME = "1900000000.000100"
LINK = "https://fable-works.slack.com/archives/C0LIBRARY1/p1900000000000100"


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    made.users[CEDAR] = cedar()
    yield made
    made.store.close()


def rows(bench: Bench) -> list[tuple]:
    return bench.store._con.execute(
        "SELECT note_id, how, conversation_id, message_time FROM slack_notes ORDER BY id"
    ).fetchall()


def claim(bench: Bench):
    return bench.view.claim_slack_message(bench.install.id, CONVERSATION, TIME, brought_by=None)


def asker(bench: Bench) -> people.Asker:
    person = bench.view.slack_person(bench.install.id, ADA_SLACK)
    return people.Asker(ADA_SLACK, person, bench.memory_for(bench.ada))


# 6. One transaction: a fault between the note and its record keeps neither


def test_a_fault_at_the_record_leaves_no_note_and_releases_the_claim(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    con = bench.store._con
    con.execute(  # a store fault at the record's write, after the note was written
        "CREATE TEMP TRIGGER fault BEFORE UPDATE OF note_id ON slack_notes"
        " BEGIN SELECT RAISE(ABORT, 'a fault'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        handle(bench)
    assert bench.notes() == [] and rows(bench) == []  # no note, and the claim released
    con.execute("DROP TRIGGER fault")
    assert replied(bench, handle(bench)).startswith("Filed: ")  # it can be filed again
    ((note_id, *_),) = bench.notes()
    assert rows(bench) == [(note_id, "filed", CONVERSATION, TIME)]


def test_a_fault_after_the_record_inside_the_write_leaves_no_note_and_is_not_said_stored(
    bench, monkeypatch
):
    """The fault comes after everything the caller runs inside the write, before the commit:
    nothing is stored, so the person is not told the note is stored."""
    bench.link(ADA_SLACK, bench.ada, bench.library)
    real = Store.add_note

    def faulty(self, *args, within=None, **kwargs):
        def then_fails(con, note, created):
            within(con, note, created)
            raise sqlite3.OperationalError("a fault after the record")

        return real(self, *args, within=then_fails if within else None, **kwargs)

    monkeypatch.setattr(Store, "add_note", faulty)
    with pytest.raises(sqlite3.OperationalError):
        handle(bench)
    assert bench.notes() == [] and rows(bench) == []
    monkeypatch.setattr(Store, "add_note", real)
    assert replied(bench, handle(bench)).startswith("Filed: ")
    assert len(bench.notes()) == 1


def test_a_typed_note_whose_record_faults_is_not_stored(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.store._con.execute(
        "CREATE TEMP TRIGGER fault BEFORE INSERT ON slack_notes"
        " BEGIN SELECT RAISE(ABORT, 'a fault'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        people.write(bench.context, asker(bench), "Row 4 holds the quince jars.")
    assert bench.notes() == [] and rows(bench) == []


# 7. A message that is another note's at the moment of the write stores nothing: the test is
# in test_slack_forgetting_filing_unit (it replaced the one that recorded a second note).


# 8. What stays as it was


def test_a_typed_text_the_project_held_already_stores_and_records_nothing(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    held, _ = bench.owner.add_note(bench.library.id, "Row 4 holds the quince jars.")
    assert said(ask(bench, "note Row 4 holds the quince jars.")).startswith("Filed: ")
    assert [note_id for note_id, *_ in bench.notes()] == [held.id] and rows(bench) == []


def test_a_typed_note_is_recorded_in_the_write_that_stores_it(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    assert said(ask(bench, "note Row 4 holds the quince jars.")).startswith("Filed: ")
    ((note_id, *_),) = bench.notes()
    assert rows(bench) == [(note_id, "typed", None, None)]


def test_an_install_removed_between_the_claim_and_the_write_gives_the_note_its_day(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    assert claim(bench) == (CLAIMED, None)
    ada = asker(bench)  # linked when the filing began
    bench.owner.remove_slack_install(bench.install.id)
    removed_at = bench.clock.now
    people.write(
        bench.context, ada, "Row 4.", title="Row", source_ref=LINK,
        message=(CONVERSATION, TIME),
    )  # fmt: skip
    ((note_id, *_),) = bench.notes()
    forget_at = bench.store._con.execute(
        "SELECT forget_at FROM slack_notes WHERE note_id = ?", (note_id,)
    ).fetchone()[0]
    assert forget_at == _iso(removed_at + FORGET_AFTER)


# 9. What a Slack write costs, pinned


def counted(bench: Bench, call) -> tuple[int, int]:
    """(every statement, the write transactions) `call()` runs on the store's connection."""
    asked: list[str] = []
    bench.store._observe(trace=asked.append)
    try:
        call()
    finally:
        bench.store._observe(trace=None)
    return len(asked), asked.count("BEGIN IMMEDIATE")


def test_a_filed_note_and_its_record_cost_the_statements_pinned(bench):
    """The note and its record use the same write. The first reply adds one scoped read to
    the former 108 statements: BEGIN, person scope, citation lookup and COMMIT. There are
    still two write transactions."""
    bench.link(ADA_SLACK, bench.ada, bench.library)
    claim(bench)
    ada = asker(bench)
    cost = counted(
        bench,
        lambda: people.write(
            bench.context,
            ada,
            "Row 4.",
            title="Row",
            source_ref=LINK,
            message=(CONVERSATION, TIME),
        ),
    )
    assert cost == (WRITE_STATEMENTS, 2)


WRITE_STATEMENTS = 112


# 9. The store's `within` is general: nothing of Slack


def test_add_note_runs_within_inside_its_write_and_a_raise_keeps_nothing(bench):
    seen = []

    def within(con, note, created):
        seen.append((con.in_transaction, note.id, created))

    note, created = bench.owner.add_note(bench.library.id, "Row 7 holds figs.", within=within)
    held, again = bench.owner.add_note(bench.library.id, "Row 7 holds figs.", within=within)
    assert created and not again and held.id == note.id
    assert seen == [(True, note.id, True), (True, note.id, False)]

    def fails(con, note, created):
        raise RuntimeError("the caller's own fault")

    with pytest.raises(RuntimeError):
        bench.owner.add_note(bench.library.id, "Row 8 holds plums.", within=fails)
    texts = [text for _, _, text, _ in bench.notes()]
    assert texts == ["Row 7 holds figs."]
    waiting = bench.store._con.execute("SELECT COUNT(*) FROM waiting").fetchone()
    assert waiting == (1,)  # only the first note's
    other, made = bench.owner.add_note(bench.library.id, "Row 9 holds dates.")  # omitted
    assert made and other.text == "Row 9 holds dates."
