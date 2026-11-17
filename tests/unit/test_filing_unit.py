"""N1 above the store: a note sent again for another topic (FiledOnce), a note that keeps
losing its page (WAITING, REST 202), Memory.file_waiting, the filer `serve` runs
(cairn.web.filing) and the `waiting` command."""

import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

from cairn import server
from cairn.compiler import compile_note, provenance_gate
from cairn.gate import lint_gate
from cairn.memory import FILED_ELSEWHERE, WAITING, WAITS, FiledOnce, Memory
from cairn.server import create_app
from cairn.store import PAGE_CLAIMS_MAX, Claim, Store, WaitingNote
from cairn.web import filing
from cairn.web.filing import TRIES_MAX, Filer, in_turns

Y2099 = datetime(2099, 3, 2, 9, 0, tzinfo=UTC)
BASE = "http://127.0.0.1:8765"
TEXT = "- Quill retries 3 times before failing.\n"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db, clock=lambda: Y2099)
    yield s
    s.close()


@pytest.fixture
def project(store):
    return store.create_project("ops")


@pytest.fixture
def memory(store, project):
    return Memory(store, provenance_gate)


def moving(monkeypatch, store, times):
    """Store.save_page wrapped: before each of its first `times` calls, the page is saved
    again first, so the call finds it moved."""
    original, left = Store.save_page, [times]

    def save_page(s, project_id, slug, *args, **kwargs):
        latest = s.get_page(project_id, slug)
        if left[0] and latest is not None:
            left[0] -= 1
            original(s, project_id, slug, latest.title, latest.body, list(latest.claims))
        return original(s, project_id, slug, *args, **kwargs)

    monkeypatch.setattr(Store, "save_page", save_page)
    return left


# FiledOnce


def test_the_same_note_for_another_topic_is_refused_naming_its_page(memory, store, db):
    first = memory.note("ops", TEXT, topic="Retry policy", source_ref="r")
    with pytest.raises(FiledOnce) as refused:
        memory.note("ops", TEXT, topic="Backoff", source_ref="r")
    said = str(refused.value)
    assert refused.value.status == 409 and refused.value.reason == FILED_ELSEWHERE
    assert first["page"] in said and "filed once" in said and "forget" in said
    assert "Quill" not in said and "retries" not in said
    assert [c.id for c in store.list_notes(store.list_projects()[0].id)] == [first["note_id"]]


def test_a_waiting_note_sent_for_another_topic_names_the_page_it_will_be_filed_on(memory, store):
    project = store.list_projects()[0]
    store.add_note(project.id, TEXT, topic="Retry policy", source_ref="r")  # not compiled
    with pytest.raises(FiledOnce, match="'retry-policy'"):
        memory.note("ops", TEXT, topic="Backoff", source_ref="r")
    assert store.list_pages(project.id) == []


# A note that keeps losing its page


def seed(memory):
    return memory.note("ops", "- Quill keeps the relay.\n", topic="Relay")


def test_a_note_that_loses_its_page_once_is_compiled_once_more(memory, store, monkeypatch):
    seed(memory)
    from cairn.compiler import SAVE_TRIES

    left = moving(monkeypatch, store, SAVE_TRIES)
    answer = memory.note("ops", TEXT, topic="Relay")
    assert left == [0] and answer["status"] == "saved" and "message" not in answer
    assert store.waiting_notes() == []


def test_a_note_that_keeps_losing_is_answered_waiting_and_waits(memory, store, monkeypatch):
    seed(memory)
    moving(monkeypatch, store, 10**6)
    answer = memory.note("ops", TEXT, topic="Relay")
    assert answer["status"] == WAITING and answer["message"] == WAITS
    assert answer["page"] == "relay" and answer["violations"][0].startswith("compile.stale_page")
    assert [n.note_id for n in store.waiting_notes()] == [answer["note_id"]]
    assert "note.rejected" not in [event.action for event in store.list_audit()]


def test_rest_answers_a_waiting_note_202_and_mcp_a_result(store, project, monkeypatch):
    key = store.create_key("t")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        h = {"x-api-key": key}
        client.post("/api/projects/ops/notes", headers=h, json={"text": "- A.\n", "topic": "Relay"})
        moving(monkeypatch, store, 10**6)
        r = client.post("/api/projects/ops/notes", headers=h, json={"text": TEXT, "topic": "Relay"})
        assert r.status_code == 202 and r.json()["status"] == WAITING, r.text
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
        body["params"] = {"name": "note", "arguments": {"project": "ops", "text": "- B.\n"}}
        body["params"]["arguments"]["topic"] = "relay"
        headers = {**h, "accept": "application/json, text/event-stream"}
        result = client.post("/mcp", headers=headers, json=body).json()["result"]
        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"])["status"] == WAITING


# Memory.file_waiting


def test_a_waiting_note_is_filed_and_waits_no_more(memory, store, project):
    note, _ = store.add_note(project.id, TEXT, topic="Relay")
    assert memory.file_waiting(note.id) == "saved"
    assert memory.file_waiting(note.id) == "unchanged"  # recorded: it waited no more
    assert store.waiting_notes() == []
    assert memory.file_waiting("cap_000000000000") is None


def test_a_waiting_note_its_gate_rejects_or_its_full_page_refuses_waits_no_more(store, project):
    bogus = Claim(id="cl_00000000000a", text="A claim.", span_ids=("sp_00000000000a",))
    store.save_page(project.id, "bogus", "Bogus", "# Bogus\n\n- A claim.\n", [bogus])
    rejected, _ = store.add_note(project.id, TEXT, topic="Bogus")
    full = "".join(f"- Item {n}.\n" for n in range(PAGE_CLAIMS_MAX))
    first, _ = store.add_note(project.id, full, topic="Full")
    assert compile_note(store, first).status == "saved"
    refused, _ = store.add_note(project.id, "- One more.\n", topic="Full")
    memory = Memory(store, lint_gate)
    assert memory.file_waiting(rejected.id) == "rejected"
    assert memory.file_waiting(refused.id) == "full"
    assert store.waiting_notes() == []


def test_a_waiting_note_whose_filing_raises_records_nothing_and_waits(memory, store, monkeypatch):
    project = store.list_projects()[0]
    note, _ = store.add_note(project.id, TEXT, topic="Relay")

    def broken(*args, **kwargs):
        raise RuntimeError("the disk refused it")

    monkeypatch.setattr(Store, "save_page", broken)
    with pytest.raises(RuntimeError):
        memory.file_waiting(note.id)
    assert [n.note_id for n in store.waiting_notes()] == [note.id]
    assert [event.action for event in store.list_audit()] == ["project.created"]


# The filer


def note(account: str, n: int, tries: int = 0) -> WaitingNote:
    return WaitingNote(
        note_id=f"cap_{account}{n:011d}"[:16],
        account_id=account,
        workspace=account,
        project_id="br_000000000000",
        project="ops",
        stored_at="2099-03-02T09:00:00.000000+00:00",
        tries=tries,
    )


def test_workspaces_take_turns_a_few_notes_each():
    notes = [note("a", n) for n in range(6)] + [note("b", n) for n in range(2)]
    notes.append(note("c", 0))
    order = [(n.account_id, int(n.note_id[-2:])) for n in in_turns(notes, per_turn=4)]
    assert order == [
        *(("a", n) for n in range(4)),
        ("b", 0),
        ("b", 1),
        ("c", 0),
        ("a", 4),
        ("a", 5),
    ]


class FakeAccounts:
    def __init__(self, notes, outcome="saved"):
        self.notes, self.outcome, self.filed, self.tried = notes, outcome, [], []
        self.during = None  # run while a note is in hand, if set

    def waiting_notes(self):
        return list(self.notes)

    def file_waiting(self, account_id, note_id):
        self.filed.append(note_id)
        if self.during is not None:
            self.during()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def tried_waiting(self, account_id, note_id):
        self.tried.append(note_id)
        return 1


def test_a_filing_that_raises_is_logged_by_its_type_counted_and_not_tried_again(caplog):
    caplog.set_level(logging.DEBUG, filing.FILING_LOGGER)
    waiting = note("a", 1)
    accounts = FakeAccounts([waiting], RuntimeError(f"the disk refused {TEXT!r}"))
    filer = Filer(accounts, 3600)
    assert filer.file_one(waiting) == "failed"
    assert accounts.tried == [waiting.note_id]
    (record,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
    said = record.getMessage()
    assert waiting.note_id in said and "RuntimeError" in said and "Quill" not in said
    assert record.exc_info is None
    asyncio.run(filer._pass([waiting]))  # not again before the next start
    assert accounts.filed == [waiting.note_id]


def test_a_note_that_lost_its_page_again_has_a_try_counted(caplog):
    waiting = note("a", 1)
    accounts = FakeAccounts([waiting], WAITING)
    assert Filer(accounts, 3600).file_one(waiting) == WAITING
    assert accounts.tried == [waiting.note_id]


def ends_at_the_first_wait(monkeypatch):
    """The filer's loop ends where it would first wait for its next look: after the start's
    pass, with no clock involved."""

    async def cancelled(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(filing.asyncio, "sleep", cancelled)


def test_a_note_with_every_try_spent_is_left_and_said_once_at_start(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, filing.FILING_LOGGER)
    spent, fresh = note("a", 1, tries=TRIES_MAX), note("a", 2)
    accounts = FakeAccounts([spent, fresh])
    filer = Filer(accounts, 3600)
    ends_at_the_first_wait(monkeypatch)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(filer.loop())  # the start's pass, then its first wait ends the loop
    assert accounts.filed == [fresh.note_id]
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warned) == 1 and spent.note_id in warned[0]


class Looks(FakeAccounts):
    """FakeAccounts whose waiting notes are, at each look in turn, the next of `looks`: a
    list of notes, or an exception the read raises."""

    def __init__(self, looks):
        super().__init__([])
        self.looks = list(looks)

    def waiting_notes(self):
        answer = self.looks.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return list(answer)


def looks_then_ends(monkeypatch, looks: int):
    """The filer's loop waits `looks` times at once (no clock), then ends at the next wait."""
    left = [looks]

    async def wait(seconds):
        if not left[0]:
            raise asyncio.CancelledError
        left[0] -= 1

    monkeypatch.setattr(filing.asyncio, "sleep", wait)


def test_a_note_stored_during_uptime_is_filed_at_the_look_after_the_one_that_saw_it(
    monkeypatch,
):
    late = note("a", 1)
    accounts = Looks([[], [late], [late]])
    looks_then_ends(monkeypatch, 2)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(Filer(accounts, 3600).loop())
    assert accounts.filed == [late.note_id]  # not at the first look: a request's, then


def test_a_look_whose_read_raises_is_logged_and_the_next_one_reads(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, filing.FILING_LOGGER)
    stored = note("a", 1)
    accounts = Looks([RuntimeError(f"the disk refused {TEXT!r}"), [stored]])
    looks_then_ends(monkeypatch, 1)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(Filer(accounts, 3600).loop())
    assert accounts.filed == [stored.note_id]  # the start's look, in place of the failed one
    (said,) = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert "RuntimeError" in said and "Quill" not in said


def test_stop_waits_for_the_note_in_hand_and_takes_no_other():
    """stop() sets the filer stopped, then takes the lock a note in hand holds, so it
    returns only once that note is done. Seen from inside the note's filing: the filer is
    stopped there (stop's first step), the lock stop waits on is held, and the note is
    filed to its end all the same."""
    first, second = note("a", 1), note("a", 2)
    accounts = FakeAccounts([first, second])
    filer = Filer(accounts, 3600)
    seen = []

    def stop_begins():
        filer._stopped.set()
        seen.append(filer._in_hand.locked())

    accounts.during = stop_begins
    assert filer.file_one(first) == "saved" and seen == [True]
    accounts.during = None
    filer.stop()
    assert filer.file_one(second) is None and accounts.filed == [first.note_id]


def test_only_an_app_given_file_every_files_anything(store):
    assert create_app(store).state.filer is None
    assert create_app(store, file_every=60).state.filer.every == 60
    for bad in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            create_app(store, file_every=bad)


# The waiting command


def test_waiting_lists_each_note_with_its_workspace_project_time_and_tries(db, capsys):
    store = Store(db, clock=lambda: Y2099)
    project = store.create_project("ops")
    one, _ = store.add_note(project.id, "- Zyqvorth keeps it.\n", title="Secret plans")
    other = store.for_account(store.create_account("Dee").id)
    two, _ = other.add_note(other.create_project("notes").id, TEXT, topic="Relay")
    store.tried_waiting(one.id)
    store.close()
    assert server.main(["waiting", "--db", str(db)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in lines] == [one.id, two.id]
    assert "'default'" in lines[0] and "'ops'" in lines[0] and "tries 1" in lines[0]
    assert "2099-03-02" in lines[0] and "'Dee'" in lines[1]
    assert not any(word in "".join(lines) for word in ("Zyqvorth", "Secret", "Quill"))
    assert server.main(["waiting", "--db", str(db), "--workspace", "Dee", "--json"]) == 0
    (listed,) = json.loads(capsys.readouterr().out)
    assert listed["note_id"] == two.id and listed["tries"] == 0
    assert server.main(["waiting", "--db", str(db), "--workspace", "Nobody"]) == 1


def test_waiting_says_so_when_nothing_waits(db, capsys):
    Store(db).close()
    assert server.main(["waiting", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "cap_" not in out and "No note is waiting" in out
    assert server.main(["waiting", "--db", str(db), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
