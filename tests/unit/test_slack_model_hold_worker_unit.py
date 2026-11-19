"""A note that came through Slack (`via` "slack") is held back from the model that arranges
pages, as a note of a withheld kind is (cairn.sources.HELD_VIA, cairn.arrange). The model is a
FakeModel; nothing leaves the process. Every name is made up."""

import asyncio
import json

import pytest

from cairn.arrange import Arranger
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import FakeModel, Reply
from cairn.store import Store

FIRST = (
    "# Quince rows\n\nRow 4 holds the quince jars.\n\n## Shelves\n\n"
    "- The top shelf is for labels.\n- The low shelf is for lids.\n"
)
SECOND = (
    "# Quince rows\n\nRow 4 holds the quince jars.\n\n## Shelves\n\n"
    "- Brook moved the twine.\n\n## Counts\n\n- Ada counts the jars on Fridays.\n"
)
PERMALINK = "https://fable-works.slack.com/archives/C0LIBRARY1/p1900000000000100"


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def page_of(view, project: str, *, via=None, source_ref=None):
    """A page of six claims from two notes, both written via `via`."""
    found = view.get_project(project) or view.create_project(project)
    for text in (FIRST, SECOND):
        note, _ = view.add_note(found.id, text, via=via, source_ref=source_ref)
        assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")
    return found, view.get_page(found.id, "quince-rows")


def answer(page) -> Reply:
    numbers = list(range(len(page.claims), 0, -1))
    return Reply(text=json.dumps({"order": numbers, "same": []}), prompt_tokens=9)


def look(arranger, store, *projects):
    for project in projects:
        arranger.queue(store.account_id, project.id, "quince-rows")
    return {d.project_id: d.status for d in asyncio.run(arranger.run_once())}


def test_a_slack_page_is_not_sent_and_an_ordinary_page_is(store):
    control, plain = page_of(store, "plain")
    slack, filed = page_of(store, "filed", via="slack", source_ref=PERMALINK)
    model = FakeModel([answer(plain), answer(filed)])  # enough for both: none is short
    arranger = Arranger(model, price=None, view_for=store.for_account)
    done = look(arranger, store, control, slack)
    assert done == {control.id: "arranged", slack.id: "withheld"}
    assert len(model.requests) == 1
