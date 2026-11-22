"""Unit tests for the arranger's one check reading a note's `source_ref` beside its `via` (M3:
tests/acceptance/test_m3_notes_that_name_a_source.py): a page that cites a note whose
`source_ref` has one of cairn.sources.SOURCE_REF_FORMS goes to no model wherever notes are
withheld at all, and the check reads nothing more than M2's did.

Every model is a FakeModel; nothing leaves the process. Fixtures are synthetic."""

import asyncio
import json

import pytest

from cairn import sources
from cairn.arrange import WITHHELD, Arranger, arrange_page, holds_withheld
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import FakeModel, Reply
from cairn.store import Store

FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
HELD = frozenset({"mail-like"})  # some `via` withheld: notes are withheld at all


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def add(view, text, *, via=None, source_ref=None, project="alpha"):
    found = view.get_project(project) or view.create_project(project)
    note, _ = view.add_note(found.id, text, via=via, source_ref=source_ref)
    assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")
    return found


def page_of(view, source_ref, *, via="mcp", second_ref=None):
    """The drift-sync page: FIRST written with `source_ref`, SECOND with `second_ref`."""
    add(view, FIRST, via=via, source_ref=source_ref)
    project = add(view, SECOND, via="api", source_ref=second_ref)
    return project, view.get_page(project.id, "drift-sync")


def answer(page) -> Reply:
    """The claims reversed, the repeated first line (claims 1 and 4) folded into claim 1."""
    numbers = [n for n in range(len(page.claims), 0, -1) if n != 4]
    return Reply(text=json.dumps({"order": numbers, "same": [[1, 4]]}), prompt_tokens=9)


class Counting:
    """A view that records the name of every method called on it."""

    def __init__(self, view):
        self._view = view
        self.calls: list[str] = []

    def __getattr__(self, name):
        found = getattr(self._view, name)
        if callable(found):
            self.calls.append(name)
        return found


@pytest.mark.parametrize("ref", ["mail:t-1", "meeting:m-1", "calendar:week/2099-11-09", "doc:f-1"])
def test_a_page_citing_a_note_of_a_form_is_withheld_where_notes_are_withheld(store, ref):
    project, page = page_of(store, ref)
    model = FakeModel([])
    for withhold in (HELD, sources.withheld_via()):
        result = arrange_page(store, project.id, "drift-sync", model, withhold=withhold)
        assert (result.status, result.problem) == ("withheld", WITHHELD)
    assert model.requests == [] and store.get_page(project.id, "drift-sync") == page


def test_a_caller_that_withholds_nothing_withholds_nothing(store):
    project, page = page_of(store, "todo:list/1")
    view = Counting(store)
    model = FakeModel([answer(page)])
    assert arrange_page(view, project.id, "drift-sync", model).status == "arranged"
    assert "get_note" not in view.calls  # as before: nothing more is read


def test_the_check_reads_each_cited_note_once_whichever_question_answers(store):
    """The same reads as M2's check: the spans once, each distinct cited note once, for a page
    withheld by a source_ref and for one that is not withheld at all."""
    _, page = page_of(store, "session:s-1", second_ref="mail:t-1")
    view = Counting(store)
    assert holds_withheld(view, page, HELD)
    assert view.calls.count("get_spans") == 1 and view.calls.count("get_note") == 2
    assert set(view.calls) == {"get_spans", "get_note"}

    other = add(store, FIRST, via="mcp", source_ref="commit:c-1", project="beta")
    add(store, SECOND, via="api", source_ref=None, project="beta")
    plain = store.get_page(other.id, "drift-sync")
    view = Counting(store)
    assert not holds_withheld(view, plain, HELD)
    assert view.calls.count("get_spans") == 1 and view.calls.count("get_note") == 2
    assert set(view.calls) == {"get_spans", "get_note"}


def test_a_granola_note_is_withheld_and_the_same_ref_over_mcp_is_withheld(store):
    """Privacy 4: Granola's notes go to no model by default, by the kind; the very same
    source_ref written over MCP is a note that names a meeting."""
    granola = sources.KINDS["granola"].via
    project, page = page_of(store, "meeting:granola/n-1", via=granola)
    model = FakeModel([answer(page)])
    result = arrange_page(store, project.id, "drift-sync", model, withhold=sources.withheld_via())
    assert result.status == "withheld" and model.requests == []

    other = add(store, FIRST, via="mcp", source_ref="meeting:granola/n-1", project="beta")
    add(store, SECOND, via="api", project="beta")
    result = arrange_page(
        store, other.id, "drift-sync", FakeModel([]), withhold=sources.withheld_via()
    )
    assert result.status == "withheld"


def test_the_worker_withholds_a_page_of_such_notes_by_default(store):
    project, _ = page_of(store, "mail:t-1")
    held = FakeModel([])
    arranger = Arranger(held, price=None, view_for=store.for_account)
    arranger.queue(store.account_id, project.id, "drift-sync")
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["withheld"]
    assert held.requests == []


def test_a_worker_given_send_sends_a_page_of_such_notes(store):
    project, page = page_of(store, "mail:t-1")
    sent = FakeModel([answer(page)])
    arranger = Arranger(sent, price=None, view_for=store.for_account, withhold=())
    arranger.queue(store.account_id, project.id, "drift-sync")
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["arranged"]
    assert len(sent.requests) == 1
