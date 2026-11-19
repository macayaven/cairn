"""Unit tests: what a source brought goes to no model unless someone chose that (review finding
Privacy 4). Every kind of source the server reads is withheld by default; the operator's two
settings still send them; a third makes projects opt in. Fixtures are synthetic; the model is
a fake that records what it is sent."""

import asyncio
import json
import re

import pytest

from cairn import sources
from cairn.arrange import UNDECIDED, Arranger, arrange_page
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


# 2. What lifts the hold is unchanged


def test_send_sends_every_kind_s_page_but_not_slack_s_and_never_a_kept_project_s(store):
    by_kind = {name: page(store, f"from-{name}", via=KINDS[name].via) for name in sorted(KINDS)}
    slack = page(store, "from-slack", via="slack")
    kept = page(store, "kept", via=KINDS["granola"].via)
    store.set_project_models(kept, "never")
    model = Recorder()
    arranger = Arranger(model, price=None, view_for=store.for_account, withhold=())
    for project_id in (*by_kind.values(), slack, kept):
        arranger.queue(store.account_id, project_id, SLUG)
    done = {d.project_id: d.status for d in asyncio.run(arranger.run_once(limit=20))}
    assert {pid for pid, status in done.items() if status == "arranged"} == set(by_kind.values())
    assert done[slack] == "withheld" and done[kept] == "kept from models"
    assert len(model.requests) == len(by_kind)


# 3. Projects opt in


class Chosen:
    """A view whose page_for_model also says whether the project's people chose "allowed"
    (the third item the store is asked to add): True for the ids in `chosen`."""

    def __init__(self, view, chosen: set[str]) -> None:
        self._view, self._chosen = view, chosen

    def __getattr__(self, name):
        return getattr(self._view, name)

    def page_for_model(self, project_id, slug):
        found = self._view.page_for_model(project_id, slug)
        return None if found is None else (*found[:2], project_id in self._chosen)


def test_opt_in_sends_only_a_project_whose_people_chose_allowed(store):
    chosen = page(store, "chosen")
    undecided = page(store, "undecided")
    model = Recorder()

    def view_for(account_id):
        return Chosen(store.for_account(account_id), {chosen})

    arranger = Arranger(model, price=None, view_for=view_for, opt_in=True)
    for project_id in (chosen, undecided):
        arranger.queue(store.account_id, project_id, SLUG)
    done = {d.project_id: d for d in asyncio.run(arranger.run_once())}
    assert done[chosen].status == "arranged"
    assert done[undecided].status == "undecided" and done[undecided].draft is None
    assert len(model.requests) == 1
    assert store.get_page(undecided, SLUG).metrics["arranged_by"] == "cairn"  # tidied instead


def test_opt_in_with_a_store_that_cannot_say_who_chose_sends_nothing(store):
    project_id = page(store, "plain")
    model = Recorder()
    said = arrange_page(store, project_id, SLUG, model, opt_in=True)
    assert said.status == "undecided" and model.requests == []
    assert said.problem == UNDECIDED and said.version == said.previous
    # The store at this commit records "allowed" as no row: a project switched to never and
    # back is still undecided to the arranger (the store change the report asks for).
    store.set_project_models(project_id, "never")
    store.set_project_models(project_id, "allowed")
    assert arrange_page(store, project_id, SLUG, model, opt_in=True).status == "undecided"
    assert model.requests == []


def test_opt_in_never_sends_a_kept_project_and_still_holds_the_sources(store):
    kept = page(store, "kept")
    store.set_project_models(kept, "never")
    mailed = page(store, "mailed", via="gmail")
    view = Chosen(store, {kept, mailed})
    model = Recorder()
    assert arrange_page(view, kept, SLUG, model, opt_in=True).status == "kept from models"
    held = arrange_page(view, mailed, SLUG, model, opt_in=True, withhold=sources.withheld_via())
    assert held.status == "withheld" and model.requests == []


def test_without_opt_in_an_undecided_project_is_sent_as_today(store):
    project_id = page(store, "plain")
    model = Recorder()
    assert arrange_page(store, project_id, SLUG, model).status == "arranged"
    assert len(model.requests) == 1


def test_opt_in_tidies_a_page_a_model_ordered_without_changing_it(store):
    project_id = page(store, "ordered")
    assert arrange_page(store, project_id, SLUG, Recorder()).status == "arranged"
    before = store.get_page(project_id, SLUG)
    model = Recorder()
    arranger = Arranger(model, price=None, view_for=store.for_account, opt_in=True)
    arranger.queue(store.account_id, project_id, SLUG)
    (done,) = asyncio.run(arranger.run_once())
    assert (done.status, done.previous, done.version) == (
        "undecided",
        before.version,
        before.version,
    )
    assert store.get_page(project_id, SLUG) == before and model.requests == []


def test_an_undecided_page_is_one_access_line_with_no_slug_or_text(store, caplog):
    project_id = page(store, "undecided")
    arranger = Arranger(Recorder(), price=None, view_for=store.for_account, opt_in=True)
    arranger.queue(store.account_id, project_id, SLUG)
    with caplog.at_level("INFO", logger="cairn.access"):
        asyncio.run(arranger.run_once())
    (line,) = [r.getMessage() for r in caplog.records if getattr(r, "action", None) == "arrange"]
    assert line.startswith("arrange undecided in project ") and project_id in line
    assert SLUG not in line and "Drift" not in line and "buckets" not in line


# 6. With no model connected nothing changes


def test_without_a_model_every_kind_s_page_is_tidied_whatever_opt_in_says(store):
    by_kind = {name: page(store, f"from-{name}", via=KINDS[name].via) for name in sorted(KINDS)}
    undecided = page(store, "typed")
    arranger = Arranger(None, price=None, view_for=store.for_account, opt_in=True)
    for project_id in (*by_kind.values(), undecided):
        arranger.queue(store.account_id, project_id, SLUG)
    done = asyncio.run(arranger.run_once(limit=20))
    assert {d.status for d in done} == {"arranged"}
    assert len(done) == len(by_kind) + 1


# 7. Cost: deciding the hold for a page stays the number of statements it is today

QUIET = {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"}


def statements(store, call) -> int:
    seen: list[str] = []

    def traced(statement: str) -> None:
        text = statement.lstrip()
        if text and not text.startswith("--") and text.split(None, 1)[0].upper() not in QUIET:
            seen.append(text)

    store._con.set_trace_callback(traced)
    try:
        call()
    finally:
        store._con.set_trace_callback(None)
    return len(seen)


def test_a_granola_page_costs_what_a_gmail_page_costs_to_hold(store):
    mailed = page(store, "mailed", via="gmail")
    granola = page(store, "granola", via=KINDS["granola"].via)
    held = sources.withheld_via()

    def judge(project_id, **more):
        def call():
            said = arrange_page(store, project_id, SLUG, Recorder(), withhold=held, **more)
            assert said.status == "withheld"

        return call

    gmail_cost = statements(store, judge(mailed))
    assert statements(store, judge(granola)) == gmail_cost
    assert statements(store, judge(granola, opt_in=False)) == gmail_cost
    chosen = Chosen(store, {granola})
    assert (
        statements(
            store,
            lambda: arrange_page(chosen, granola, SLUG, Recorder(), opt_in=True, withhold=held),
        )
        == gmail_cost
    )
