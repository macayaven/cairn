"""A project's Models choice holds at the moment of a call (M4, an outside review's S3): the
state is read again in the transaction that reserves each call, so no call is admitted after
a change to "never" commits, and an answer that arrives after it is not saved. Every model
is a FakeModel or a recording one; nothing leaves the process. Fixtures are synthetic."""

import json

import pytest

from cairn.arrange import KEPT, Price, arrange_page, tidy_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import Reply
from cairn.store import KeptFromModels, Limits, Store

TEXT = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
PRICE = Price(input_per_million=1_000_000, output_per_million=5_000_000)


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def page_of(view):
    project = view.create_project("alpha")
    note, _ = view.add_note(project.id, TEXT)
    assert compile_note(view, note, gate=lint_gate).status == "saved"
    return project, view.get_page(project.id, "drift-sync")


def reversed_order(page) -> str:
    return json.dumps({"order": list(range(len(page.claims), 0, -1)), "same": []})


class Recording:
    """A model that records every request it is sent and answers each with `answers`, in
    turn; `on_call`, when given, runs inside the call, before the answer."""

    name = "recording"

    def __init__(self, answers, on_call=None):
        self.requests, self._answers, self._on_call = [], list(answers), on_call

    def complete(self, messages):
        self.requests.append([dict(m) for m in messages])
        if self._on_call is not None:
            self._on_call()
        return Reply(text=self._answers.pop(0), prompt_tokens=300, completion_tokens=40)


class SwitchedAfterRead:
    """The review's wrapper: the real view, except that page_for_model returns the real
    result and then sets the project to "never"."""

    def __init__(self, view):
        self._view = view

    def __getattr__(self, name):
        return getattr(self._view, name)

    def page_for_model(self, project_id, slug):
        found = self._view.page_for_model(project_id, slug)
        self._view.set_project_models(project_id, "never")
        return found


@pytest.mark.parametrize("price", [None, PRICE])
def test_the_reviews_reproduction_no_call_after_the_switch(store, price):
    project, page = page_of(store)
    model = Recording([reversed_order(page)] * 2)
    done = arrange_page(SwitchedAfterRead(store), project.id, page.slug, model, price=price)
    assert model.requests == []
    assert (done.status, done.problem, done.version) == ("kept from models", KEPT, page.version)
    assert store.model_reservations() == []
    assert store.get_page(project.id, page.slug).version == page.version


def test_a_budget_in_force_refuses_the_call_in_the_reservations_own_transaction(store):
    project, page = page_of(store)
    view = store.limited(Limits(model_budget_micros=10**9))
    model = Recording([reversed_order(page)])
    done = arrange_page(SwitchedAfterRead(view), project.id, page.slug, model, price=PRICE)
    assert (done.status, model.requests) == ("kept from models", [])
    assert store.model_reservations() == []
    assert store.usage().model_micros == 0  # nothing reserved, nothing spent


def test_the_reservation_refuses_a_project_at_never_and_writes_nothing(store):
    project, _ = page_of(store)
    store.set_project_models(project.id, "never")
    with pytest.raises(KeptFromModels):
        store.reserve_model_use(5, project.id)
    assert store.model_reservations() == []
    assert store.usage().model_micros == 0
    held = store.reserve_model_use(5)  # no project named: the meter alone, as before
    assert held is not None


def test_a_second_try_is_not_admitted_after_the_switch(store):
    project, page = page_of(store)
    switch = lambda: store.set_project_models(project.id, "never")  # noqa: E731
    model = Recording(["not json", reversed_order(page)], on_call=switch)
    done = arrange_page(store, project.id, page.slug, model, price=PRICE)
    assert len(model.requests) == 1  # the first try ran; the retry is refused
    assert (done.status, done.problem) == ("kept from models", KEPT)
    assert done.draft.attempts == 1
    assert store.usage().model_tokens_in == 300  # the try that was sent is spent
    assert store.model_reservations() == []


def test_an_answer_that_arrives_after_the_switch_is_not_saved_and_its_cost_is(store):
    project, page = page_of(store)
    switch = lambda: store.set_project_models(project.id, "never")  # noqa: E731
    model = Recording([reversed_order(page)], on_call=switch)
    done = arrange_page(store, project.id, page.slug, model, price=PRICE)
    assert len(model.requests) == 1
    assert (done.status, done.problem, done.version) == ("kept from models", KEPT, page.version)
    assert (done.moved, done.merged) == (0, 0)
    after = store.get_page(project.id, page.slug)
    assert (after.version, after.claims) == (page.version, page.claims)
    usage = store.usage()
    assert (usage.model_tokens_in, usage.model_tokens_out) == (300, 40)
    assert usage.model_micros == PRICE.micros(300, 40)
    assert store.model_reservations() == []
    assert "page.arranged" not in [e.action for e in store.list_audit()]


def test_a_save_of_a_models_answer_is_refused_in_its_own_write(store):
    project, page = page_of(store)
    store.set_project_models(project.id, "never")
    with pytest.raises(KeptFromModels):
        store.save_page(
            project.id,
            page.slug,
            page.title,
            page.body,
            page.claims,
            expected_version=page.version,
            event="page.arranged",
            detail={"from_version": page.version, "model": "m", "moved": 0, "merged": 0},
            by_model=True,
        )
    assert store.get_page(project.id, page.slug).version == page.version


def test_a_tidy_still_saves_in_a_project_at_never(store):
    project = store.create_project("alpha")
    repeated = TEXT + "- At most 500 files go in one batch.\n"
    note, _ = store.add_note(project.id, repeated)
    compile_note(store, note, gate=lint_gate)
    store.set_project_models(project.id, "never")
    done = tidy_page(store, project.id, "drift-sync")
    assert done.status == "arranged"  # the repeat is folded, by cairn, with no model
