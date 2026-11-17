"""Unit tests for a source's items (I1): what tests/acceptance/test_i1_source_items.py does
not pin. NoteDraft's item, the store's two calls on the table, and the runner's order of
checks."""

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import ClassVar

import httpx
import pytest

from cairn.gate import lint_gate
from cairn.sources import HELD_CREDENTIALS, KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.store import Store
from cairn.store.sources import ITEM_BATCH


class Items:
    """A source whose pulls answer what the test queued."""

    answers: ClassVar[list[Pulled]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    async def pull(self, cursor):
        return Items.answers.pop(0)


def opened(name, config, *, http, env, clock=None, sleep=None, deadline=None, since=None):
    return Items(name)


@pytest.fixture(autouse=True)
def items_kind(monkeypatch):
    monkeypatch.setitem(KINDS, "items", SourceKind(check=dict, open=opened, via="items"))
    Items.answers = []


def draft(**fields) -> NoteDraft:
    return NoteDraft(
        project="alpha", title="Entry", text="# Entry\n\nA line.\n", source_ref="ref", label="l",
        **fields,
    )  # fmt: skip


# NoteDraft.item


def test_a_draft_names_no_item_unless_its_kind_gives_one():
    assert draft().item is None
    assert draft(item="x").item == "x" and draft(item="y" * 200).item == "y" * 200


@pytest.mark.parametrize("bad", ["", "z" * 201, 7, b"bytes"])
def test_an_item_is_one_to_200_characters_or_the_draft_is_not_made(bad):
    with pytest.raises(ValueError, match="item"):
        draft(item=bad)


# The store: written_source_items and record_source_item


AT = datetime(2099, 5, 2, 8, 30, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db", clock=lambda: AT)
    s.create_project("alpha")
    s.add_source("books", "items", {"projects": ["alpha"]})
    yield s
    s.close()


def table(store) -> list[tuple]:
    return store._con.execute("SELECT * FROM source_items ORDER BY source, item").fetchall()


def test_an_item_is_recorded_once_as_a_digest_with_the_time_it_was_written(store):
    claim = store.claim_source("books")
    assert store.record_source_item(claim, "row-1") is True
    assert store.record_source_item(claim, "row-1") is True  # already there: still held
    digest = hashlib.sha256(b"row-1").hexdigest()[:32]
    seq = store._con.execute("SELECT seq FROM sources WHERE name = 'books'").fetchone()[0]
    assert table(store) == [(seq, digest, "2099-05-02T08:30:00.000000+00:00")]
    assert store.written_source_items(claim, ["row-1", "row-2", "row-1"]) == {"row-1"}


def test_a_claim_no_longer_held_records_nothing(store):
    claim = store.claim_source("books")
    store.disable_source("books")  # lets go of the claim
    assert store.record_source_item(claim, "row-1") is False
    store.enable_source("books")
    assert store.record_source_item(claim, "row-1") is False  # another run's now, or none
    assert table(store) == []
    store.remove_source("books")
    assert store.record_source_item(claim, "row-1") is False
    assert store.written_source_items(claim, ["row-1"]) == set()


def test_the_items_of_one_source_are_not_anothers(store):
    store.add_source("ledger", "items", {"projects": ["alpha"]})
    books, ledger = store.claim_source("books"), store.claim_source("ledger")
    store.record_source_item(books, "row-1")
    assert store.written_source_items(ledger, ["row-1"]) == set()
    assert store.written_source_items(books, ["row-1"]) == {"row-1"}


def test_the_read_is_one_section_of_statements_of_at_most_item_batch_digests(store):
    claim = store.claim_source("books")
    for n in range(0, 1200, 7):
        store.record_source_item(claim, f"row-{n}")
    seen: list[str] = []
    store._con.set_trace_callback(seen.append)
    try:
        found = store.written_source_items(claim, [f"row-{n}" for n in range(1200)])
    finally:
        store._con.set_trace_callback(None)
    assert found == {f"row-{n}" for n in range(0, 1200, 7)}
    selects = [s for s in seen if "source_items" in s]
    assert len(selects) == -(-1200 // ITEM_BATCH) == 3
    assert store.written_source_items(claim, []) == set()


def test_removing_a_source_removes_its_items_and_a_new_one_under_its_number_has_none(store):
    claim = store.claim_source("books")
    store.record_source_item(claim, "row-1")
    store.finish_source_run(claim, None, {})
    assert store.remove_source("books") is True
    assert table(store) == []
    store.add_source("books", "items", {"projects": ["alpha"]})  # may take the same seq
    again = store.claim_source("books")
    assert store.written_source_items(again, ["row-1"]) == set()


def test_deleting_a_workspace_removes_its_sources_items_and_no_others(store):
    store.record_source_item(store.claim_source("books"), "row-1")
    kept = table(store)
    other = store.for_account(store.create_account("Fen").id)
    other.create_project("alpha")
    other.add_source("books", "items", {"projects": ["alpha"]})
    other.record_source_item(other.claim_source("books"), "row-1")
    assert len(table(store)) == 2
    assert store.delete_account(other.account_id) is True
    assert table(store) == kept


def test_a_narrowed_view_neither_reads_nor_records_items(store):
    claim = store.claim_source("books")
    for view in (store.narrow(access="read"), store.narrow(projects=("nothing",))):
        with pytest.raises(PermissionError):
            view.written_source_items(claim, ["row-1"])
        with pytest.raises(PermissionError):
            view.record_source_item(claim, "row-1")
    assert table(store) == []


# The runner


def note(n: int, *, project: str = "alpha", text: str | None = None, item: str | None = None):
    return NoteDraft(
        project=project,
        title=f"Row {n}",
        text=text if text is not None else f"# Row {n}\n\nRow {n} holds {n * 3} crates.\n",
        source_ref=f"https://items.example.test/{n}",
        label="items",
        topic=f"row-{n}",
        item=item if item is not None else f"row-{n}",
    )


def run(store, drafts, **kwargs):
    Items.answers.append(Pulled(drafts=list(drafts), cursor="c"))
    return asyncio.run(
        run_source(store, "books", http=httpx.AsyncClient(), env={}, clock=lambda: AT, **kwargs)
    )


def test_a_written_item_counts_as_unchanged_whatever_project_its_draft_names_now(store):
    store.create_project("beta")
    assert run(store, [note(1)]).saved == 1
    report = run(store, [note(1, project="beta"), note(1, project="nowhere")])
    assert (report.unchanged, report.skipped, report.saved) == (2, 0, 0)


def test_an_item_holding_the_sources_credential_is_skipped_and_not_recorded(store):
    store.add_source("keyed", "items", {"projects": ["alpha"], "token_env": "ITEMS_TOKEN"})
    Items.answers.append(Pulled(drafts=[note(1, item="row-1 sk-items-0123456789")], cursor="c"))
    report = asyncio.run(
        run_source(store, "keyed", http=httpx.AsyncClient(),
                   env={"ITEMS_TOKEN": "sk-items-0123456789"}, clock=lambda: AT)
    )  # fmt: skip
    assert (report.saved, report.reasons) == (0, {HELD_CREDENTIALS: 1})
    assert table(store) == []


def test_a_draft_whose_text_its_project_forgot_is_not_recorded(store):
    alpha = store.get_project("alpha")
    stored, _ = store.add_note(alpha.id, note(1).text)
    store.forget_note(stored.id)
    report = run(store, [note(1)])
    assert (report.saved, report.reasons) == (0, {"forgotten": 1})
    assert table(store) == []


def test_a_note_whose_record_finds_the_claim_gone_ends_the_run_as_superseded(store):
    taken = []

    def gate_that_lets_the_claim_go(page, spans):
        if not taken:
            store.disable_source("books")  # lets go of the claim, as another run taking it
            taken.append(True)
        return lint_gate(page, spans)

    report = run(store, [note(1), note(2)], gate=gate_that_lets_the_claim_go)
    assert report.superseded and (report.saved, report.unchanged) == (1, 0)
    assert table(store) == [] and len(store.list_notes(store.get_project("alpha").id)) == 1
    assert store.get_source("books").cursor is None  # nothing stored by the superseded run


def test_a_pull_without_items_reads_nothing_of_the_table(store, monkeypatch):
    def unread(*args, **kwargs):
        raise AssertionError("the table was read")

    monkeypatch.setattr(Store, "written_source_items", unread)
    monkeypatch.setattr(Store, "record_source_item", unread)
    plain = NoteDraft(project="alpha", title="Row", text="# Row\n\nA row.\n", source_ref="r",
                      label="items")  # fmt: skip
    assert run(store, [plain, plain]).saved == 1
