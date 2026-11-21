"""Q1 B in the store: a page's history bound without plans (cairn.store.history). The server
says how many versions each page keeps (`bound_history`), records what it runs with
(`record_history_bound`), and a compaction removes by that record alone (`prune_by_record`).
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from cairn.store import Limits, Pruned, Store

START = datetime(2099, 5, 1, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db", clock=Clock())
    yield s
    s.close()


def save(store: Store, project_id: str, slug: str, times: int) -> None:
    for i in range(times):
        store.save_page(project_id, slug, slug.title(), f"Body {i}.", [])


def statements(store: Store, call):
    seen: list[str] = []

    def traced(statement: str) -> None:
        if not statement.lstrip().startswith("--"):
            seen.append(statement.lstrip().split(None, 1)[0].upper())

    store._observe(trace=traced)
    try:
        result = call()
    finally:
        store._observe(trace=None)
    return result, [s for s in seen if s not in ("BEGIN", "COMMIT")]


def test_a_save_past_the_bound_removes_the_oldest_versions(store):
    project = store.create_project("north")
    store.bound_history(3)
    save(store, project.id, "a", 5)
    assert store.page_versions(project.id, "a") == [3, 4, 5]
    view = store.for_account(store._account_id)  # every view of the connection: one bound
    save(view, project.id, "a", 1)
    assert store.page_versions(project.id, "a") == [4, 5, 6]


def test_no_bound_keeps_every_version_and_reading_removes_none(store):
    project = store.create_project("north")
    save(store, project.id, "a", 5)
    store.bound_history(2)
    store.get_page(project.id, "a")
    store.page_versions(project.id, "a")
    assert store.page_versions(project.id, "a") == [1, 2, 3, 4, 5]  # until it is saved
    store.bound_history(None)
    save(store, project.id, "a", 1)
    assert store.page_versions(project.id, "a") == [1, 2, 3, 4, 5, 6]


def test_a_plan_rules_over_the_servers_bound(store):
    project = store.create_project("north")
    store.bound_history(2)
    planned = store.limited(resolve=lambda name: Limits())  # a plan with no bound
    save(planned, project.id, "a", 4)
    assert store.page_versions(project.id, "a") == [1, 2, 3, 4]
    capped = store.limited(Limits(versions_per_page=3))  # a cap, no plan: the lower holds
    save(capped, project.id, "b", 4)
    assert store.page_versions(project.id, "b") == [3, 4]


@pytest.mark.parametrize("bad", [0, -1, 2.5, "3", True])
def test_the_bound_is_a_whole_number_of_one_or_more(store, bad):
    with pytest.raises(ValueError, match="1 or more"):
        store.bound_history(bad)
    with pytest.raises(ValueError, match="1 or more"):
        store.record_history_bound(bad)


def test_pages_over_counts_pages_past_a_bound_in_one_statement(store):
    north, south = store.create_project("north"), store.create_project("south")
    save(store, north.id, "a", 4)
    save(store, north.id, "b", 3)
    save(store, south.id, "c", 5)
    count, ran = statements(store, lambda: store.pages_over(3))
    assert count == 2 and ran == ["SELECT"]
    assert store.pages_over(5) == 0


def test_with_no_record_a_prune_removes_nothing_and_says_so(store):
    project = store.create_project("north")
    save(store, project.id, "a", 4)
    assert store.prune_by_record() == Pruned(recorded=False, removed=0)
    assert store.page_versions(project.id, "a") == [1, 2, 3, 4]


def test_a_prune_keeps_what_the_last_record_says(store):
    north, south = store.create_project("north"), store.create_project("south")
    save(store, north.id, "a", 6)
    save(store, south.id, "b", 2)
    store.record_history_bound(None)
    assert store.prune_by_record() == Pruned(recorded=True, removed=0, keep=None)
    store.record_history_bound(4)
    store.record_history_bound(2)  # the last record rules
    assert store.prune_by_record() == Pruned(recorded=True, removed=4, keep=2)
    assert store.page_versions(north.id, "a") == [5, 6]
    assert store.page_versions(south.id, "b") == [1, 2]
    store.bound_history(None)  # what the running server keeps is not the record
    assert store.prune_by_record().removed == 0


def test_with_plans_each_workspace_keeps_its_plans_versions(store):
    mine = store.create_project("north")
    save(store, mine.id, "a", 5)
    tight = store.create_account("Tight")
    theirs = store.for_account(tight.id)
    beacon = theirs.create_project("beacon")
    save(theirs, beacon.id, "lamp", 6)
    store.set_plan(tight.id, "tight")
    unknown = store.create_account("Elsewhere")
    gone = store.for_account(unknown.id)
    far = gone.create_project("far")
    save(gone, far.id, "x", 6)
    store.set_plan(unknown.id, "retired")
    store.record_history_bound(plans={"roomy": None, "tight": 2}, default="roomy")
    pruned = store.prune_by_record()
    assert pruned == Pruned(recorded=True, removed=4, left_alone=(unknown.id,), by_plans=True)
    assert store.page_versions(mine.id, "a") == [1, 2, 3, 4, 5]  # the default: every one
    assert theirs.page_versions(beacon.id, "lamp") == [5, 6]
    assert gone.page_versions(far.id, "x") == [1, 2, 3, 4, 5, 6]  # a plan it does not name


def test_a_record_with_plans_names_its_default_among_them(store):
    with pytest.raises(ValueError, match="default"):
        store.record_history_bound(plans={"roomy": None}, default="other")
    with pytest.raises(ValueError, match="1 or more"):
        store.record_history_bound(plans={"roomy": 0}, default="roomy")


def test_a_prune_that_fails_removes_nothing(store):
    project = store.create_project("north")
    save(store, project.id, "a", 5)
    save(store, project.id, "b", 5)
    store.record_history_bound(2)
    state = {"pruned": False, "running": ""}

    def progress() -> int:  # the COMMIT after a removal is interrupted: rolled back
        return 1 if state["pruned"] and state["running"] == "COMMIT" else 0

    def traced(statement: str) -> None:
        text = statement.lstrip()
        if text.startswith("--"):
            return
        state["running"] = text.split(None, 1)[0].upper()
        if text.upper().startswith("DELETE FROM PAGE_VERSIONS"):
            state["pruned"] = True

    store._con.set_trace_callback(traced)
    store._con.set_progress_handler(progress, 1)
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.prune_by_record()
    finally:
        store._con.set_progress_handler(None, 1)
        store._con.set_trace_callback(None)
    assert store.page_versions(project.id, "a") == [1, 2, 3, 4, 5]
    assert store.page_versions(project.id, "b") == [1, 2, 3, 4, 5]


def test_the_records_and_bounds_are_the_operators(store):
    person = store.sign_in(
        "sso", "sub-p", "p@example.test", "Pia", issuer="https://id.example.test/h",
        join="personal", ttl_seconds=86_400,
    )  # fmt: skip
    hers = store.for_account(person.account_id, person.user.id)
    for call in (
        lambda: hers.bound_history(3),
        lambda: hers.record_history_bound(3),
        lambda: hers.pages_over(3),
        hers.prune_by_record,
    ):
        with pytest.raises(PermissionError):
            call()
