"""Q1 A in Memory (cairn.memory): what the MCP tools and the REST API answer for a project's
pages and a page's versions, a part at a time, and what a count costs."""

from datetime import UTC, datetime, timedelta

import pytest

from cairn.compiler import provenance_gate
from cairn.memory import PAGES_AT_ONCE, VERSIONS_AT_ONCE, BadRequest, Memory
from cairn.store import Claim, Store

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


def save(store: Store, project_id: str, slug: str, times: int = 1) -> None:
    claim = Claim(id=f"cl_{slug}", text=f"{slug}.", span_ids=())
    for _ in range(times):
        store.save_page(project_id, slug, slug.title(), "", [claim])


def test_the_bounds_are_what_the_contract_says():
    assert (PAGES_AT_ONCE, VERSIONS_AT_ONCE) == (100, 50)


def test_projects_counts_pages_without_listing_them(store, monkeypatch):
    north = store.create_project("north")
    store.create_project("empty")
    for slug in ("a", "b"):
        save(store, north.id, slug, times=2)
    monkeypatch.setattr(Store, "list_pages", lambda *a: pytest.fail("listed every page"))
    found = Memory(store, provenance_gate).projects()["projects"]
    assert [(b["name"], b["pages"]) for b in found] == [("empty", 0), ("north", 2)]


def test_a_part_of_the_pages_says_how_many_there_are_and_what_follows(store):
    project = store.create_project("north")
    for i in range(5):
        save(store, project.id, f"p{i}")
    memory = Memory(store, provenance_gate)
    whole = memory.pages("north")
    assert [p["slug"] for p in whole["pages"]] == ["p0", "p1", "p2", "p3", "p4"]
    assert list(whole) == ["project", "pages"]  # everything in it: today's answer, no new field
    first = memory.pages("north", limit=2)
    assert [p["slug"] for p in first["pages"]] == ["p0", "p1"]
    assert (first["pages_total"], first["pages_next"]) == (5, "p1")
    last = memory.pages("north", limit=2, after="p3")
    assert [p["slug"] for p in last["pages"]] == ["p4"]
    assert last["pages_total"] == 5 and "pages_next" not in last
    over = memory.overview("north", limit=2, after="p1")
    assert [p["slug"] for p in over["pages"]] == ["p2", "p3"] and over["notes"] == 0
    assert over["pages_next"] == "p3"


def test_a_part_of_the_versions_is_read_from_the_end(store):
    project = store.create_project("north")
    save(store, project.id, "a", times=7)
    memory = Memory(store, provenance_gate)
    assert memory.page("north", "a")["versions"] == [1, 2, 3, 4, 5, 6, 7]
    assert not [n for n in memory.page("north", "a") if n.startswith("versions_")]
    latest = memory.page("north", "a", limit=3)
    assert latest["versions"] == [5, 6, 7] and latest["version"] == 7
    assert (latest["versions_total"], latest["versions_next"]) == (7, 5)
    earlier = memory.page("north", "a", limit=3, before=5)
    assert earlier["versions"] == [2, 3, 4] and earlier["versions_next"] == 2
    first = memory.page("north", "a", limit=3, before=2)
    assert first["versions"] == [1] and "versions_next" not in first
    assert first["versions_total"] == 7


@pytest.mark.parametrize("limit", [0, -1])
def test_a_limit_below_one_is_refused(store, limit):
    project = store.create_project("north")
    save(store, project.id, "a")
    memory = Memory(store, provenance_gate)
    for ask in (
        lambda: memory.pages("north", limit=limit),
        lambda: memory.overview("north", limit=limit),
        lambda: memory.page("north", "a", limit=limit),
    ):
        with pytest.raises(BadRequest, match="limit"):
            ask()


def test_a_limit_past_the_bound_is_the_bound(store):
    project = store.create_project("north")
    save(store, project.id, "a", times=VERSIONS_AT_ONCE + 2)
    memory = Memory(store, provenance_gate)
    assert len(memory.page("north", "a", limit=10_000)["versions"]) == VERSIONS_AT_ONCE
    assert memory.page("north", "a", before=3)["versions"] == [1, 2]
