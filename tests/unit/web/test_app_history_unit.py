"""Q1 B in the app: create_app(store, keep_versions=N) bounds every page's history when the
app has no plans (cairn.web.app); None, the default, keeps every version."""

from datetime import UTC, datetime, timedelta

import pytest

from cairn.plans import Plan, Plans
from cairn.server import create_app
from cairn.store import Store

START = datetime(2099, 6, 1, 9, 0, tzinfo=UTC)


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


def saves(store: Store, times: int) -> list[int]:
    project = store.get_project("north") or store.create_project("north")
    for i in range(times):
        store.save_page(project.id, "a", "A", f"Body {i}.", [])
    return store.page_versions(project.id, "a")


def test_keep_versions_bounds_every_save(store):
    create_app(store, keep_versions=2)
    assert saves(store, 4) == [3, 4]


def test_by_default_every_version_is_kept(store):
    create_app(store, keep_versions=2)
    create_app(store)  # the last app built says
    assert saves(store, 4) == [1, 2, 3, 4]


@pytest.mark.parametrize("bad", [0, -3, 1.5, "all", True])
def test_keep_versions_is_a_whole_number_of_one_or_more(store, bad):
    with pytest.raises(ValueError, match="1 or more"):
        create_app(store, keep_versions=bad)


def test_with_plans_keep_versions_is_refused(store):
    plans = Plans({"base": Plan(label="Base")}, "base")
    with pytest.raises(ValueError, match="plans"):
        create_app(store, plans=plans, keep_versions=5)
    create_app(store, plans=plans)  # the plan's own versions_per_page rules
