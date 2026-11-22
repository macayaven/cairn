"""Unit tests for metering (H6): what a workspace has stored and used, counted in the
transaction of the write it belongs to, under tests/acceptance/test_h6_quotas.py."""

import io
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cairn.store import Limits, Store
from cairn.store.base import NOT_A_MEMBER

NOVEMBER = datetime(2026, 11, 10, 12, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = NOVEMBER) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def recount(db: Path, account_id: str) -> int:
    """The UTF-8 size of every note the workspace holds, counted from the notes themselves."""
    con = sqlite3.connect(db)
    try:
        texts = con.execute(
            "SELECT text FROM notes WHERE project_id IN"
            " (SELECT id FROM projects WHERE account_id = ?)",
            (account_id,),
        ).fetchall()
    finally:
        con.close()
    return sum(len(text.encode()) for (text,) in texts)


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(db, clock):
    s = Store(db, clock=clock)
    yield s
    s.close()


def test_a_note_written_at_the_last_instant_of_a_month_counts_in_that_month(store, clock):
    project = store.create_project("ops")
    clock.now = datetime(2026, 11, 30, 23, 59, 59, 999_999, tzinfo=UTC)
    store.add_note(project.id, "last\n")
    clock.now = datetime(2026, 12, 1, tzinfo=UTC)
    store.add_note(project.id, "first\n")
    assert store.usage("2026-11").notes == 1 and store.usage("2026-12").notes == 1


def test_the_month_is_the_utc_month_whatever_the_clocks_zone(store, clock):
    project = store.create_project("ops")
    madrid = timezone_plus(1)
    clock.now = datetime(2026, 12, 1, 0, 30, tzinfo=madrid)  # 23:30 on 30 November, UTC
    store.add_note(project.id, "late\n")
    assert store.usage("2026-11").notes == 1 and store.usage("2026-12").notes == 0


def timezone_plus(hours: int):
    from datetime import timezone

    return timezone(timedelta(hours=hours))


@pytest.mark.parametrize("period", ["2026-13", "2026-1", "nov", "2026-11-01", ""])
def test_a_period_is_a_month_written_yyyy_mm(store, period):
    with pytest.raises(ValueError):
        store.usage(period)


def test_a_month_with_no_use_reads_as_zero(store):
    used = store.usage("1999-01")
    assert (used.period, used.notes, used.note_bytes, used.model_micros) == ("1999-01", 0, 0, 0)


def test_stored_bytes_is_a_running_total_that_equals_a_recount(store, db):
    other = store.for_account(store.create_account("Brook").id)
    mine, theirs = store.create_project("ops"), other.create_project("ops")
    for text in ("Größe: 5 µm\n", "plain\n", "日本語のメモ\n", "Größe: 5 µm\n"):  # one twice
        store.add_note(mine.id, text)
        other.add_note(theirs.id, text * 2)
    store.add_note(mine.id, "plain\n", source_ref="elsewhere")  # same text, another note
    assert store.stored_bytes() == recount(db, store.account_id)
    assert other.stored_bytes() == recount(db, other.account_id)
    gone = store.for_account(store.create_account("Gone").id)
    gone.add_note(gone.create_project("ops").id, "bye\n")
    store.delete_account(gone.account_id)
    assert store.stored_bytes() == recount(db, store.account_id)
    assert other.stored_bytes() == recount(db, other.account_id)


def test_an_import_is_metered_in_the_month_it_is_made(store, db, clock, tmp_path):
    source = Store(tmp_path / "source.db", clock=clock)  # an export moves between installs
    try:
        project = source.create_project("ops")
        for text in ("Größe: 5 µm\n", "plain\n"):
            source.add_note(project.id, text)
        archive = io.BytesIO()
        source.export(archive)
    finally:
        source.close()
    clock.now = NOVEMBER + timedelta(days=40)  # imported in December, written in November
    target = store.for_account(store.create_account("Target").id)
    target.import_export(io.BytesIO(archive.getvalue()))
    assert target.stored_bytes() == recount(db, target.account_id) > 0
    december = target.usage()
    assert (december.period, december.notes) == ("2026-12", 2)
    assert december.note_bytes == target.stored_bytes()
    assert target.usage("2026-11").notes == 0


def test_a_refused_note_counts_nothing(store):
    project = store.create_project("ops")
    with pytest.raises(Exception, match="guard"):
        store.add_note(project.id, "token ghp_" + "Zq7xR2" * 6 + "\n")
    assert store.usage().notes == 0 and store.stored_bytes() == 0


def test_reading_usage_writes_nothing(store, db):
    store.add_note(store.create_project("ops").id, "x\n")
    store.record_model_use(1, 2, 3)
    before = dump(db)
    store.usage()
    store.usage("2026-10")
    store.stored_bytes()
    assert dump(db) == before


@pytest.mark.parametrize("bad", [(1.5, 0, 0), (0, "2", 0), (0, 0, True), (0, 0, None)])
def test_model_use_is_whole_numbers(store, db, bad):
    before = dump(db)
    with pytest.raises(ValueError):
        store.record_model_use(*bad)
    assert dump(db) == before


def test_model_use_and_notes_share_the_months_row(store, clock):
    store.record_model_use(10, 20, 30)
    store.add_note(store.create_project("ops").id, "x\n")
    store.record_model_use(1, 2, 3)
    used = store.usage()
    assert (used.notes, used.model_tokens_in, used.model_tokens_out, used.model_micros) == (
        1,
        11,
        22,
        33,
    )


def test_a_view_of_a_deleted_workspace_records_no_model_use(store, db):
    gone = store.for_account(store.create_account("Gone").id)
    store.delete_account(gone.account_id)
    before = dump(db)
    with pytest.raises(KeyError):
        gone.record_model_use(1, 1, 1)
    assert dump(db) == before


def test_a_workspaces_plan_is_a_name_in_the_store(store, db):
    brook = store.create_account("Brook")
    assert store.get_account(brook.id).plan is None
    store.set_plan(brook.id, "solo")
    assert store.get_account(brook.id).plan == "solo"
    assert [a.plan for a in store.list_accounts()] == [None, "solo"]
    assert store.for_account(brook.id).get_account(brook.id).plan == "solo"
    for bad in ("", " ", "x" * 65, 3):
        with pytest.raises(ValueError):
            store.set_plan(brook.id, bad)
    store.set_plan(brook.id, None)
    assert store.get_account(brook.id).plan is None


def test_a_narrowed_view_reads_no_usage(store):
    project = store.create_project("ops")
    for view in (store.narrow(projects=(project.id,)), store.narrow(access="read")):
        with pytest.raises(PermissionError):
            view.usage()
        with pytest.raises(PermissionError):
            view.stored_bytes()


def test_a_narrowed_view_still_meters_its_notes_for_the_workspace(store):
    project = store.create_project("ops")
    store.narrow(projects=(project.id,)).add_note(project.id, "x\n")
    store.narrow(projects=(project.id,)).record_model_use(1, 1, 5)
    assert (store.usage().notes, store.usage().model_micros) == (1, 5)


def test_deleting_a_workspace_deletes_its_usage_and_plan(store, db):
    gone = store.create_account("Gone")
    view = store.for_account(gone.id)
    view.add_note(view.create_project("ops").id, "x\n")
    view.record_model_use(1, 1, 1)
    store.set_plan(gone.id, "solo")
    store.delete_account(gone.id)
    con = sqlite3.connect(db)
    try:
        for table in ("usage", "account_quota"):
            left = con.execute(f"SELECT COUNT(*) FROM {table} WHERE account_id = ?", (gone.id,))
            assert left.fetchone() == (0,), table
    finally:
        con.close()


def people(store: Store):
    """An owner and a member of the default workspace, each as their own view."""
    views = []
    for n, name in enumerate(("Olive Owner", "Max Member")):
        signed = store.sign_in(
            "github", str(n), f"person{n}@example.test", name, issuer="https://github.com",
            join="default",
        )  # fmt: skip
        views.append(store.for_account(signed.account_id, signed.user.id))
    return views


def test_only_a_view_acting_for_no_person_sets_a_plan(store, db):
    owner, member = people(store)
    before = dump(db)
    for person in (owner, member):
        with pytest.raises(PermissionError):
            person.set_plan(store.account_id, "solo")
    assert dump(db) == before
    store.set_plan(store.account_id, "solo")  # the operator's
    assert store.get_account(store.account_id).plan == "solo"


def test_any_member_reads_the_workspaces_usage_whole(store):
    owner, member = people(store)
    owner.create_project("private", everyone="none")
    secret = owner.create_project("ledger", everyone="none")
    owner.add_note(secret.id, "Größe: 5 µm\n")
    assert member.get_project("ledger") is None  # out of the member's reach
    assert member.usage().notes == 1 and member.stored_bytes() == owner.stored_bytes() > 0
    assert member.project_count() == owner.project_count() == 2


def test_a_removed_member_meters_nothing(store, db):
    """Metering and the caps ride in the store's own write transaction, after its
    membership check: a person removed meanwhile writes nothing, the meter included."""
    owner, member = people(store)
    limited = member.limited(Limits(notes_per_month=5))
    project = owner.create_project("ops")
    owner.remove_member(member.user_id)
    before = dump(db)
    with pytest.raises(PermissionError, match=NOT_A_MEMBER):
        limited.add_note(project.id, "# Fact\n\nQuill keeps one fact.\n")
    with pytest.raises(PermissionError, match=NOT_A_MEMBER):
        limited.create_project("more")
    assert dump(db) == before


def test_a_removed_member_at_the_cap_is_told_they_are_not_a_member(store):
    """The look at the counters before screening is a read, before the write's membership
    check: it must not answer a removed person with the workspace's counts."""
    owner, member = people(store)
    project = owner.create_project("ops")
    limited = member.limited(Limits(notes_per_month=0))
    owner.remove_member(member.user_id)
    with pytest.raises(PermissionError, match=NOT_A_MEMBER):
        limited.add_note(project.id, "# Fact\n\nQuill keeps one fact.\n")
