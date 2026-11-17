"""Unit tests for limited views (H6): caps a view refuses inside the write's own transaction,
under tests/acceptance/test_h6_quotas.py."""

import io
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cairn.store import Store
from cairn.store.usage import Limits, OverLimit

NOVEMBER = datetime(2026, 11, 10, 12, tzinfo=UTC)


def clock() -> datetime:
    return NOVEMBER


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def note(n: int) -> str:
    return f"# Fact {n}\n\nQuill keeps fact number {n}.\n"


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db, clock=clock)
    yield s
    s.close()


@pytest.mark.parametrize("bad", [-1, 1.5, True, "3"])
def test_a_cap_is_a_whole_number_or_none(bad):
    with pytest.raises(ValueError):
        Limits(notes_per_month=bad)


def test_over_a_limit_is_not_a_value_error():
    assert not issubclass(OverLimit, ValueError)  # Memory turns a ValueError into a 400


def test_a_refusal_writes_nothing_not_even_an_audit_event(store, db):
    limited = store.limited(Limits(notes_per_month=0, projects=0))
    project = store.create_project("ops")
    before = dump(db)
    with pytest.raises(OverLimit):
        limited.add_note(project.id, note(1))
    with pytest.raises(OverLimit):
        limited.create_project("more")
    assert dump(db) == before


def test_the_storage_cap_may_be_reached_but_not_passed(store):
    project = store.create_project("ops")
    limited = store.limited(Limits(storage_bytes=40))  # one passage of 40 bytes
    limited.add_note(project.id, "x" * 40)  # exactly the cap
    with pytest.raises(OverLimit) as over:
        limited.add_note(project.id, "y")
    assert (over.value.limit, over.value.used, over.value.cap) == ("storage_bytes", 40, 40)


def test_limits_only_tighten(store):
    loose = store.limited(Limits(notes_per_month=5, projects=1))
    both = loose.limited(Limits(notes_per_month=1, storage_bytes=1_000))
    project = store.create_project("ops")
    both.add_note(project.id, note(1))
    with pytest.raises(OverLimit) as over:
        both.add_note(project.id, note(2))
    assert over.value.cap == 1
    with pytest.raises(OverLimit) as projects:
        both.create_project("more")  # the first view's project cap still holds
    assert projects.value.limit == "projects"
    loose.add_note(project.id, note(2))  # the looser view is as it was


def test_a_cap_is_the_workspaces_whatever_the_views_reach(store):
    ops, other = store.create_project("ops"), store.create_project("other")
    store.add_note(other.id, note(1))
    narrowed = store.narrow(projects=(ops.id,)).limited(Limits(notes_per_month=1))
    with pytest.raises(OverLimit) as over:
        narrowed.add_note(ops.id, note(2))
    assert over.value.used == 1
    limited_then_narrowed = store.limited(Limits(notes_per_month=1)).narrow(projects=(ops.id,))
    with pytest.raises(OverLimit):
        limited_then_narrowed.add_note(ops.id, note(2))
    with pytest.raises(KeyError):  # the narrowing still holds on a limited view
        limited_then_narrowed.add_note(other.id, note(3))


def test_a_limited_view_is_refused_out_of_reach_before_its_caps(store):
    ops, other = store.create_project("ops"), store.create_project("other")
    view = store.narrow(projects=(ops.id,)).limited(Limits(notes_per_month=0))
    with pytest.raises(KeyError):
        view.add_note(other.id, note(1))
    with pytest.raises(PermissionError):
        store.narrow(access="read").limited(Limits(notes_per_month=0)).add_note(ops.id, "x")


def test_the_guided_setup_counts_only_the_projects_it_would_make(store, db):
    store.create_project("ops")
    limited = store.limited(Limits(projects=2))
    made = limited.create_projects([("ops", "general"), ("runbooks", "team")])
    assert [b.name for b in made] == ["ops", "runbooks"]
    assert limited.create_projects([("ops", "general")])  # nothing new: never over the cap
    before = dump(db)
    with pytest.raises(OverLimit) as over:
        store.limited(Limits(projects=3)).create_projects([("a", "general"), ("b", "general")])
    assert (over.value.limit, over.value.used, over.value.cap) == ("projects", 2, 3)
    assert dump(db) == before  # all or nothing


def test_a_name_already_taken_is_said_before_the_cap(store):
    store.create_project("ops")
    with pytest.raises(ValueError, match="already"):
        store.limited(Limits(projects=1)).create_project("ops")


def test_writers_on_two_connections_cannot_pass_a_cap_between_them(db, store):
    other = Store(db, clock=clock)
    try:
        project = store.create_project("ops")
        outcomes: list[str] = []

        def write(view: Store, n: int) -> None:
            try:
                view.add_note(project.id, note(n))
                outcomes.append("stored")
            except OverLimit:
                outcomes.append("refused")

        views = [s.limited(Limits(notes_per_month=7)) for s in (store, other)]
        threads = [threading.Thread(target=write, args=(views[n % 2], n)) for n in range(30)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert outcomes.count("stored") == 7 and store.usage().notes == 7
    finally:
        other.close()


def test_model_budget_left_never_goes_below_zero_and_is_none_without_a_budget(store):
    assert store.limited(Limits(notes_per_month=3)).model_budget_left() is None
    nothing = store.limited(Limits(model_budget_micros=0))
    assert nothing.model_budget_left() == 0
    nothing.record_model_use(1, 1, 50)  # recorded past the budget
    assert nothing.model_budget_left() == 0 and store.usage().model_micros == 50
    tighter = store.limited(Limits(model_budget_micros=500)).limited(
        Limits(model_budget_micros=100)
    )
    assert tighter.model_budget_left() == 50


def test_reading_the_budget_writes_nothing(store, db):
    limited = store.limited(Limits(model_budget_micros=10))
    limited.record_model_use(1, 1, 1)
    before = dump(db)
    assert limited.model_budget_left() == 9
    assert dump(db) == before


def archive_of(tmp_path: Path, projects: int, notes: int) -> bytes:
    """An export of another install: `projects` projects, `notes` notes in each."""
    source = Store(tmp_path / "source.db", clock=clock)
    try:
        for b in range(projects):
            project = source.create_project(f"project-{b}")
            for n in range(notes):
                source.add_note(project.id, note(10 * b + n))
        out = io.BytesIO()
        source.export(out)
        return out.getvalue()
    finally:
        source.close()


@pytest.mark.parametrize(
    ("limits", "limit"),
    [
        (Limits(notes_per_month=3), "notes_per_month"),
        (Limits(storage_bytes=100), "storage_bytes"),
        (Limits(projects=1), "projects"),
    ],
)
def test_an_import_past_a_cap_imports_nothing_and_names_the_limit(
    store, db, tmp_path, limits, limit
):
    archive = archive_of(tmp_path, projects=2, notes=2)
    before = dump(db)
    with pytest.raises(OverLimit) as refused:
        store.limited(limits).import_export(io.BytesIO(archive))
    assert refused.value.limit == limit
    assert dump(db) == before


def test_an_import_that_fits_the_caps_exactly_is_imported(store, tmp_path):
    archive = archive_of(tmp_path, projects=2, notes=2)
    limited = store.limited(Limits(notes_per_month=4, projects=2))
    assert limited.import_export(io.BytesIO(archive)).notes == 4


def test_a_limited_view_keeps_its_limits_when_it_rebinds(store):
    """for_account on a limited view returns itself for its own workspace and refuses any
    other, so no view sheds its caps by binding again (StoreBase._bound)."""
    a = store.create_account("Quill").id
    other = store.create_account("Brook").id
    limited = store.for_account(a).limited(Limits(notes_per_month=1))
    again = limited.for_account(a)
    project = again.create_project("ops")
    again.add_note(project.id, note(1))
    with pytest.raises(OverLimit):
        again.add_note(project.id, note(2))
    with pytest.raises(PermissionError):
        limited.for_account(other)


def test_the_contract_says_what_the_storage_cap_bounds_on_disk():
    import cairn.store

    contract = " ".join(cairn.store.__doc__.split())
    said = contract.split("What the storage cap bounds", 1)[1].split("- Billing", 1)[0]
    assert "note text" in said and "FIELD_MAX" in said and "versions_per_page" in said
    assert "3,600 bytes" in said  # 900 characters of fields, 4 bytes each at most
    # H6c: the bounds beside it, and what is still not bounded
    assert "BYTES_PER_PASSAGE" in said and "HISTORY_FACTOR" in said and "PAGE_CLAIMS_MAX" in said
    assert "heading" in said


# The plan in force, read inside each write (H6 fix 2)

TABLE = {
    "big": Limits(notes_per_month=10, projects=5),
    "small": Limits(notes_per_month=1, projects=1),
}


def by_name(asked: list):
    def resolve(name):
        asked.append(name)
        return TABLE.get(name, Limits())

    return resolve


def test_a_downgrade_between_admission_and_the_write_is_seen_by_the_write(store):
    store.set_plan(store.account_id, "big")
    asked: list = []
    view = store.limited(resolve=by_name(asked))  # admitted on the big plan
    project = view.create_project("ops")
    view.add_note(project.id, note(1))
    store.set_plan(store.account_id, "small")  # the operator downgrades meanwhile
    with pytest.raises(OverLimit) as over:
        view.add_note(project.id, note(2))
    assert (over.value.limit, over.value.used, over.value.cap) == ("notes_per_month", 1, 1)
    assert over.value.plan == "small"  # the plan in force when the write was refused
    with pytest.raises(OverLimit):
        view.create_project("more")
    assert asked[-1] == "small" and "big" in asked
    store.set_plan(store.account_id, "big")  # and an upgrade is seen as soon
    view.add_note(project.id, note(2))


def test_the_resolver_is_asked_inside_the_write(store):
    """Inside the write's transaction, with the name Store.plan_in_force gives: None when
    nothing is recorded (the server's default)."""
    seen = []

    def resolve(name):
        seen.append((name, store._con.in_transaction))
        return Limits()

    view = store.limited(resolve=resolve)
    project = view.create_project("ops")
    view.add_note(project.id, note(1))
    assert seen == [(None, True)] * 3  # the project; the note's look before screening; its write


def test_static_limits_and_a_resolver_together_keep_the_tighter(store):
    store.set_plan(store.account_id, "big")
    view = store.limited(Limits(notes_per_month=0), resolve=by_name([]))
    project = store.create_project("ops")
    with pytest.raises(OverLimit):
        view.add_note(project.id, note(1))


def test_a_resolver_is_a_callable_and_binds_the_view(store):
    with pytest.raises(TypeError):
        store.limited(resolve="big")
    other = store.create_account("Brook").id
    with pytest.raises(PermissionError):
        store.limited(resolve=by_name([])).for_account(other)


# A cheap look at the counters before the guard screens a note (H6 fix 4)


@pytest.fixture
def screened(monkeypatch):
    calls: list = []
    original = Store._screen

    def screen(self, fields):
        calls.append(fields["text"])
        return original(self, fields)

    monkeypatch.setattr(Store, "_screen", screen)
    return calls


@pytest.mark.parametrize(
    "limits, first", [(Limits(notes_per_month=1), note(1)), (Limits(storage_bytes=40), "x" * 40)]
)
def test_a_workspace_at_its_cap_refuses_a_new_note_before_screening_it(
    store, screened, limits, first
):
    project = store.create_project("ops")
    limited = store.limited(limits)
    limited.add_note(project.id, first)
    screened.clear()
    with pytest.raises(OverLimit):
        limited.add_note(project.id, note(2))
    assert screened == []  # the guard never ran


def test_the_same_note_again_at_the_cap_is_still_unchanged(store, screened):
    project = store.create_project("ops")
    limited = store.limited(Limits(notes_per_month=1))
    limited.add_note(project.id, note(1), source_ref="r:1")
    _, created = limited.add_note(project.id, note(1), source_ref="r:1")
    assert created is False


def test_the_check_inside_the_write_stays_the_authority(store, monkeypatch):
    """The look before screening passes; another writer fills the cap while the guard
    screens; the write's own check refuses."""
    project = store.create_project("ops")
    limited = store.limited(Limits(notes_per_month=1))
    original = Store._screen

    def screen_while_another_writes(self, fields):
        if fields["text"] == note(2):
            store.add_note(project.id, note(9))  # an unlimited writer, meanwhile
        return original(self, fields)

    monkeypatch.setattr(Store, "_screen", screen_while_another_writes)
    with pytest.raises(OverLimit):
        limited.add_note(project.id, note(2))
    assert [c.text for c in store.list_notes(project.id)] == [note(9)]


def test_a_foreign_project_at_the_cap_is_still_unknown(store):
    project = store.create_project("ops")
    limited = store.limited(Limits(notes_per_month=0))
    other = store.for_account(store.create_account("Brook").id)
    theirs = other.create_project("theirs")
    with pytest.raises(KeyError):
        limited.add_note(theirs.id, note(1))
    with pytest.raises(OverLimit):
        limited.add_note(project.id, note(1))
