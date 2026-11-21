"""A note the project holds, sent again for another topic (FiledOnce), names its page by the
Notes page's rule (Store.notes_part): only when that page's latest version cites the note.
A note that waits for its page is answered as `note` answers one (no page, WAITS); a note
refused a place on its topic's page (the gate refused it, or the page filled first) is on no
page (NO_PAGE). Made-up notes only."""

from datetime import UTC, datetime

import pytest

from cairn.candidate import GateResult
from cairn.compiler import provenance_gate
from cairn.memory import NO_PAGE, WAITS, FiledOnce, Memory
from cairn.store import Store

Y2099 = datetime(2099, 3, 2, 9, 0, tzinfo=UTC)
FIRST = "- Quill retries 3 times before failing.\n"
SECOND = "- Lumen keeps the relay.\n"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db", clock=lambda: Y2099)
    s.create_project("ops")
    yield s
    s.close()


def refusing_after(first: int):
    """A gate that passes its first `first` pages and refuses every one after."""
    calls = [0]

    def gate(page, spans):
        calls[0] += 1
        if calls[0] > first:
            return GateResult(passed=False, violations=["made.up: -: refused"], metrics={})
        return provenance_gate(page, spans)

    return gate


def sent_again(memory: Memory, text: str) -> FiledOnce:
    with pytest.raises(FiledOnce) as refused:
        memory.note("ops", text, topic="Backoff", source_ref="r")
    return refused.value


QUIET = {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"}


def statements(store: Store, call) -> int:
    """The statements `call` runs on the store's connections, transactions aside."""
    seen: list[str] = []
    store._observe(trace=seen.append, rows=None)
    try:
        call()
    finally:
        store._observe(trace=None, rows=None)
    return sum(1 for s in seen if s.split(None, 1)[0].upper() not in QUIET)


def test_a_note_its_page_cites_is_named_on_that_page(store):
    memory = Memory(store, provenance_gate)
    memory.note("ops", FIRST, topic="Retry policy", source_ref="r")
    said = sent_again(memory, FIRST)
    assert said.page == "retry-policy"
    assert "'retry-policy'" in str(said)


def test_a_note_its_topics_page_refused_names_no_page(store):
    memory = Memory(store, refusing_after(1))
    memory.note("ops", FIRST, topic="Retry policy")
    refused = memory.note("ops", SECOND, topic="Retry policy", source_ref="r")
    assert refused["status"] == "rejected"
    said = sent_again(memory, SECOND)
    assert said.page is None
    assert "retry-policy" not in str(said) and NO_PAGE in str(said)
    assert "Lumen" not in str(said) and "relay" not in str(said)


def test_a_waiting_note_names_no_page_and_says_it_waits(store):
    project = store.list_projects()[0]
    store.add_note(project.id, FIRST, topic="Retry policy", source_ref="r")  # not compiled
    said = sent_again(Memory(store, provenance_gate), FIRST)
    assert said.page is None
    assert "retry-policy" not in str(said) and WAITS in str(said)


def test_the_page_of_a_held_note_is_read_in_one_statement(store):
    memory = Memory(store, refusing_after(1))
    memory.note("ops", FIRST, topic="Retry policy", source_ref="a")
    memory.note("ops", SECOND, topic="Retry policy", source_ref="b")
    project = store.list_projects()[0]
    held = {n.text: n for n in store.list_notes(project.id)}
    waiting = store.add_note(project.id, "- Tessera waits.\n", topic="Queue")[0]
    store.notes_part(project.id, 1)  # a reader opened before the observers are attached
    for note in (held[FIRST], held[SECOND], waiting):
        assert statements(store, lambda n=note: memory._page_of(n)) == 1


def test_a_note_sent_again_for_another_topic_costs_seven_statements_whatever_its_page(store):
    """The whole refusal, from `note` to FiledOnce: seven statements for a note on its page,
    one its page refused and one that waits. Before the Notes page's rule it was seven, seven
    and eight (a waiting note's page was looked for among free addresses)."""
    memory = Memory(store, refusing_after(1))
    memory.note("ops", FIRST, topic="Retry policy", source_ref="a")
    memory.note("ops", SECOND, topic="Retry policy", source_ref="b")
    project = store.list_projects()[0]
    store.add_note(project.id, "- Tessera waits.\n", topic="Queue", source_ref="c")
    store.notes_part(project.id, 1)  # a reader opened before the observers are attached
    for text, ref in ((FIRST, "a"), (SECOND, "b"), ("- Tessera waits.\n", "c")):

        def resend(text=text, ref=ref):
            with pytest.raises(FiledOnce):
                memory.note("ops", text, topic="Backoff", source_ref=ref)

        assert statements(store, resend) == 7, ref


def test_slack_names_a_held_notes_page_by_the_same_rule(store):
    """A message filed already (cairn.slack.people.already) links the note's page only when
    that page cites it; a note its page refused, or one that waits, links its project."""
    from types import SimpleNamespace

    from cairn.slack import people

    memory = Memory(store, refusing_after(1))
    on_page = memory.note("ops", FIRST, topic="Retry policy")
    refused = memory.note("ops", SECOND, topic="Retry policy")
    project = store.list_projects()[0]
    waiting = store.add_note(project.id, "- Tessera waits.\n", topic="Queue")[0]
    context = SimpleNamespace(public_url="https://cairn.example.test")
    asker = SimpleNamespace(memory=memory)

    def said(note_id: str) -> str:
        return people.already(context, asker, note_id)["blocks"][0]["text"]["text"]

    assert "/b/ops/p/retry-policy|" in said(on_page["note_id"])
    for note_id, sentence in ((refused["note_id"], "No page holds it."), (waiting.id, "shortly")):
        text = said(note_id)
        assert "/p/" not in text and "<https://cairn.example.test/b/ops|ops>" in text
        assert sentence in text
