"""Unit tests for a project kept from every model in cairn.arrange (M4): the inner TDD loop
under tests/acceptance/test_m4_a_project_no_model_reads.py. A page of a project at "never" goes
to no model, whatever its notes say and whatever the operator sends; the worker tidies it.

Every model is a FakeModel; nothing leaves the process. Fixtures are synthetic."""

import asyncio
import json
import logging

import pytest

from cairn import arrange as arranging
from cairn.arrange import KEPT, TIDY, WITHHELD, Arranger, Price, arrange_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.guard import PatternGuard
from cairn.llm import FakeModel, Reply
from cairn.store import Limits, Store

FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
PRICE = Price(input_per_million=1_000_000, output_per_million=5_000_000)


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def add(view, text, *, via=None, project="alpha"):
    found = view.get_project(project) or view.create_project(project)
    note, _ = view.add_note(found.id, text, via=via)
    assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")
    return found


def kept(view, project="alpha"):
    """The drift-sync page of `project`, six claims, the project at "never"."""
    add(view, FIRST, project=project)
    found = add(view, SECOND, project=project)
    view.set_project_models(found.id, "never")
    return found, view.get_page(found.id, "drift-sync")


def answer(page) -> Reply:
    return Reply(text=json.dumps({"order": list(range(len(page.claims), 0, -1)), "same": []}))


def test_a_page_of_a_project_at_never_is_kept_whatever_withhold_says(store):
    project, page = kept(store)
    model = FakeModel([])
    for withhold in ((), frozenset({"gmail"})):
        result = arrange_page(
            store, project.id, "drift-sync", model, price=PRICE, withhold=withhold
        )
        assert (result.status, result.version, result.previous) == (
            "kept from models",
            page.version,
            page.version,
        )
        assert (result.moved, result.merged, result.problem) == (0, 0, KEPT)
        assert result.draft.attempts == 0 and result.draft.prompt_tokens is None
    assert KEPT != WITHHELD and model.requests == []
    assert store.model_reservations() == [] and store.usage().model_micros == 0
    assert store.get_page(project.id, "drift-sync") == page


def test_the_switch_is_judged_before_anything_else(store, monkeypatch):
    project, _page = kept(store)
    add(store, "# Drift sync\n\n- Dee mailed the keys.\n", via="gmail")  # withheld too
    cases = {
        "a model named cairn": dict(model=FakeModel([], name=TIDY)),
        "large": dict(max_claims=1),
        "guarded": dict(view=store.with_guard(PatternGuard("secrets+personal"))),
        "no budget": dict(view=store.limited(Limits(model_budget_micros=0)), price=PRICE),
    }
    for case, given in cases.items():
        view = given.pop("view", store)
        model = given.pop("model", FakeModel([]))
        result = arrange_page(view, project.id, "drift-sync", model, withhold={"gmail"}, **given)
        assert result.status == "kept from models", case


def test_an_unknown_page_is_gone_before_it_is_kept(store):
    project, _ = kept(store)
    assert arrange_page(store, project.id, "nowhere", FakeModel([])).status == "gone"


def test_a_project_switched_back_is_sent_again(store):
    project, page = kept(store)
    store.set_project_models(project.id, "allowed")
    model = FakeModel([answer(page)])
    assert arrange_page(store, project.id, "drift-sync", model).status == "arranged"


def test_the_project_beside_it_is_sent(store):
    kept(store)
    other = add(store, FIRST, project="beta")
    add(store, SECOND, project="beta")
    page = store.get_page(other.id, "drift-sync")
    model = FakeModel([answer(page)])
    assert arrange_page(store, other.id, "drift-sync", model).status == "arranged"


# The worker


def worker(store, model, **options):
    options.setdefault("price", None)
    options.setdefault("view_for", store.for_account)
    return Arranger(model, **options)


def look(arranger, store, project_id, slug="drift-sync"):
    arranger.queue(store.account_id, project_id, slug)
    return asyncio.run(arranger.run_once())


@pytest.mark.parametrize("withhold", [None, ()], ids=["default", "send"])
def test_the_worker_tidies_a_kept_page_and_remembers_it(store, withhold):
    project, page = kept(store)
    model = FakeModel([])
    arranger = worker(store, model, withhold=withhold)
    (done,) = look(arranger, store, project.id)
    after = store.get_page(project.id, "drift-sync")
    assert (done.status, done.previous, done.version, done.draft) == (
        "kept from models",
        page.version,
        after.version,
        None,
    )
    assert after.metrics["arranged_by"] == TIDY and len(after.claims) == 5
    assert model.requests == [] and arranger.unusable == 0
    assert [d.status for d in look(arranger, store, project.id)] == ["seen"]


@pytest.mark.parametrize("withhold", [None, ()], ids=["default", "send"])
def test_a_page_a_model_arranged_before_stays_as_it_is_through_the_worker(store, withhold):
    """'Pages already arranged stay as they are': the worker's tidy of a kept page leaves a
    model's order and its folds alone."""
    add(store, FIRST)
    project = add(store, SECOND)
    page = store.get_page(project.id, "drift-sync")
    assert arrange_page(store, project.id, "drift-sync", FakeModel([answer(page)])).status == (
        "arranged"
    )
    before = store.get_page(project.id, "drift-sync")
    assert [c.id for c in before.claims] != [c.id for c in page.claims]  # a real reorder
    store.set_project_models(project.id, "never")
    model = FakeModel([])
    arranger = worker(store, model, withhold=withhold)
    (done,) = look(arranger, store, project.id)
    after = store.get_page(project.id, "drift-sync")
    assert (done.status, done.previous, done.version) == (
        "kept from models",
        before.version,
        before.version,
    )
    assert model.requests == []
    assert (after.version, after.claims, after.metrics) == (
        before.version,
        before.claims,
        before.metrics,
    )
    assert [d.status for d in look(arranger, store, project.id)] == ["seen"]
    # A note written later makes a version no model ordered: that one the worker tidies.
    add(store, "# Drift sync\n\n## Limits\n\n- At most 9 buckets sync at once.\n")
    (done,) = look(arranger, store, project.id)
    assert done.status == "kept from models" and model.requests == []
    assert store.get_page(project.id, "drift-sync").metrics["arranged_by"] == TIDY


def test_a_small_page_of_a_kept_project_is_tidied(store):
    project = add(store, "# Held\n\nAda keeps the keys.\n\n- Line a.\n")
    add(store, "# Held\n\nAda keeps the keys.\n\n- Line b.\n")
    store.set_project_models(project.id, "never")
    arranger = worker(store, FakeModel([]), withhold=())
    (done,) = look(arranger, store, project.id, "held")
    assert (done.status, done.draft) == ("kept from models", None)
    assert store.get_page(project.id, "held").metrics["arranged_by"] == TIDY


def test_a_page_switched_between_the_worker_s_two_reads_is_kept(store, monkeypatch):
    add(store, FIRST)
    project = add(store, SECOND)
    real = arranging.arrange_page

    def switched(view, project_id, slug, model, **options):
        store.set_project_models(project_id, "never")  # after the worker read the page
        return real(view, project_id, slug, model, **options)

    monkeypatch.setattr(arranging, "arrange_page", switched)
    model = FakeModel([])
    (done,) = look(worker(store, model), store, project.id)
    assert done.status == "kept from models" and model.requests == []


def test_a_model_s_order_switched_between_the_worker_s_two_reads_stays(store, monkeypatch):
    add(store, FIRST)
    project = add(store, SECOND)
    page = store.get_page(project.id, "drift-sync")
    assert arrange_page(store, project.id, "drift-sync", FakeModel([answer(page)])).status == (
        "arranged"
    )
    before = store.get_page(project.id, "drift-sync")
    real = arranging.arrange_page

    def switched(view, project_id, slug, model, **options):
        store.set_project_models(project_id, "never")  # after the worker read the page
        return real(view, project_id, slug, model, **options)

    monkeypatch.setattr(arranging, "arrange_page", switched)
    arranger = worker(store, FakeModel([]))
    (done,) = look(arranger, store, project.id)
    assert (done.status, done.previous, done.version) == (
        "kept from models",
        before.version,
        before.version,
    )
    assert store.get_page(project.id, "drift-sync") == before
    assert [d.status for d in look(arranger, store, project.id)] == ["seen"]


def test_a_kept_page_leaves_the_count_towards_a_pause_as_it_is(store):
    project, _ = kept(store)
    other = add(
        store, "# Plain\n\n" + "".join(f"- Crate {n} sits low.\n" for n in range(7)), project="beta"
    )
    prose = Reply(text="In words.", prompt_tokens=3, completion_tokens=2)
    arranger = worker(store, FakeModel([prose] * 4), pause_after=2, clock=lambda: 50.0)
    statuses = [
        d.status
        for d in look(arranger, store, other.id, "plain") + look(arranger, store, project.id)
    ]
    assert statuses == ["invalid", "kept from models"] and arranger.unusable == 1
    assert not arranger.paused()


def test_a_workspace_with_no_budget_left_leaves_its_kept_pages_queued(store):
    project, page = kept(store)
    empty = store.limited(Limits(model_budget_micros=0))
    arranger = worker(store, FakeModel([]), price=PRICE, view_for=lambda a: empty)
    assert [d.status for d in look(arranger, store, project.id)] == ["no budget"]
    assert store.get_page(project.id, "drift-sync") == page
    assert [p.slug for p in arranger.pending()] == ["drift-sync"]


def test_without_a_model_a_kept_page_is_tidied_as_any_other(store):
    project, _ = kept(store)
    assert [d.status for d in look(worker(store, None), store, project.id)] == ["arranged"]


def test_a_kept_page_is_one_line_on_the_access_log(store, caplog):
    project, page = kept(store)
    arranger = worker(store, FakeModel([]))
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        look(arranger, store, project.id)
        look(arranger, store, project.id)  # seen: no second line
    (line,) = [r for r in caplog.records if getattr(r, "action", None) == "arrange"]
    said = line.getMessage()
    assert said.startswith("arrange kept from models in project ") and project.id in said
    assert "alpha" not in said and page.title not in said and "tokens" not in said
