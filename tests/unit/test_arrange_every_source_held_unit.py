"""Unit tests: what a source brought goes to no model unless someone chose that (review finding
Privacy 4). Every kind of source the server reads is withheld by default; the operator's two
settings still send them; a third makes projects opt in. Fixtures are synthetic; the model is
a fake that records what it is sent."""

import asyncio
import json
import re

import pytest

from cairn import sources
from cairn.arrange import Arranger, arrange_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import Reply
from cairn.sources import KINDS
from cairn.store import Store

# Six claims a page, the first line twice: worth a call (min_claims).
FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
SLUG = "drift-sync"


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


class Recorder:
    """A fake model: records each request and answers with the claims in the order given."""

    name = "held-recorder"

    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    def complete(self, messages) -> Reply:
        self.requests.append([dict(message) for message in messages])
        user = messages[-1]["content"]
        numbers = [int(n) for n in re.findall(r'<claim-[0-9a-f]+ n="(\d+)"', user)]
        same = json.loads(re.search(r"by number: (\[.*\])\n", user).group(1))
        folded = {n for group in same for n in group[1:]}
        order = [n for n in reversed(numbers) if n not in folded]
        return Reply(
            text=json.dumps({"order": order, "same": same}), prompt_tokens=9, completion_tokens=9
        )


def page(view, project: str, *, via: str | None = None) -> str:
    """A six-claim page in `project` (made here), its notes written with `via`. Its id."""
    made = view.create_project(project)
    for text in (FIRST, SECOND):
        note, _ = view.add_note(made.id, text, via=via)
        assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")
    return made.id


def sent_projects(store, model, project_ids) -> set[str]:
    """The projects whose page the worker sent to `model`, by the page each request quoted."""
    titles = {pid: store.get_page(pid, SLUG) for pid in project_ids}
    assert all(titles.values())
    arranger = Arranger(model, price=None, view_for=store.for_account)
    for project_id in project_ids:
        arranger.queue(store.account_id, project_id, SLUG)
    done = asyncio.run(arranger.run_once(limit=len(project_ids)))
    return {d.project_id for d in done if d.status == "arranged"}


# 1. Every kind of source the server reads is withheld by default


def test_only_the_page_a_person_typed_is_sent_whatever_kind_the_others_came_from(store):
    kinds = sorted(KINDS)
    assert {"granola", "linear", "github", "langfuse"} <= set(kinds)
    by_kind = {name: page(store, f"from-{name}", via=KINDS[name].via) for name in kinds}
    typed = page(store, "typed")  # a note a person typed: no via
    model = Recorder()

    sent = sent_projects(store, model, [*by_kind.values(), typed])

    assert sent == {typed}
    assert len(model.requests) == 1


def test_every_kind_the_server_reads_is_withheld():
    assert {name for name, kind in KINDS.items() if not kind.withheld} == set()
    assert sources.withheld_via() == {kind.via for kind in KINDS.values()}


@pytest.mark.parametrize("name", ["granola", "linear", "github", "langfuse"])
def test_arrange_page_withholds_each_kind_once_sent_by_default(store, name):
    project_id = page(store, name, via=KINDS[name].via)
    model = Recorder()
    said = arrange_page(store, project_id, SLUG, model, withhold=sources.withheld_via())
    assert said.status == "withheld" and model.requests == []
