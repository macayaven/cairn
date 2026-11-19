"""Unit tests for the worker's pause (T4c): the inner TDD loop under
tests/acceptance/test_t4c_pause.py. Models are scripted in the process; fixtures are synthetic."""

import asyncio
import json
import logging
import math
import threading

import pytest

import cairn.arrange
from cairn.arrange import Arranged, Arranger
from cairn.compiler import DraftStats, compile_note
from cairn.gate import lint_gate
from cairn.llm import Reply
from cairn.store import Store


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def note_for(title: str, lines: int = 6) -> str:
    facts = "".join(f"- Fact {k} of {title}.\n" for k in range(lines))
    return f"# {title}\n\n{facts}"


def pages(view, titles, *, project="alpha", lines=6) -> str:
    """One page per title in `project` (slug: the title, lower case, dashes); the project's id."""
    found = view.get_project(project) or view.create_project(project)
    for title in titles:
        note, _ = view.add_note(found.id, note_for(title, lines))
        compile_note(view, note, gate=lint_gate)
    return found.id


def slug(title: str) -> str:
    return title.lower().replace(" ", "-")


class Scripted:
    """Stands in for arrange_page: each page ends with the status its slug is given."""

    def __init__(self, script: dict[str, str]) -> None:
        self.script = script
        self.asked: list[str] = []

    def __call__(self, view, project_id, page_slug, model, **options) -> Arranged:
        self.asked.append(page_slug)
        version = view.get_page(project_id, page_slug).version
        draft = DraftStats(composer=model.name, attempts=1, prompt_tokens=1, completion_tokens=1)
        return Arranged(
            status=self.script[page_slug], version=version, previous=version, draft=draft
        )


class Named:
    name = "scripted-model"

    def complete(self, messages):  # never reached: arrange_page is scripted
        raise AssertionError("no model call in these tests")


def worker(store, clock, **options) -> Arranger:
    options.setdefault("min_claims", 1)
    return Arranger(Named(), price=None, view_for=store.for_account, clock=clock, **options)


def run(arranger, limit=10):
    return asyncio.run(arranger.run_once(limit=limit))


# Counting


def test_the_fourth_unusable_answer_does_not_pause_and_the_fifth_does(store, monkeypatch):
    titles = [f"Page {n}" for n in range(1, 7)]
    project = pages(store, titles)
    monkeypatch.setattr(
        cairn.arrange, "arrange_page", Scripted(dict.fromkeys(map(slug, titles), "invalid"))
    )
    clock = Clock()
    arranger = worker(store, clock)
    for title in titles[:4]:
        arranger.queue(store.account_id, project, slug(title))
    assert [d.status for d in run(arranger)] == ["invalid"] * 4
    assert (arranger.unusable, arranger.paused()) == (4, False)
    arranger.queue(store.account_id, project, "page-5")
    arranger.queue(store.account_id, project, "page-6")
    assert [d.status for d in run(arranger, limit=1)] == ["invalid"]
    assert (arranger.unusable, arranger.paused()) == (5, True)
    assert run(arranger) == [] and [p.slug for p in arranger.pending()] == ["page-6"]


NEUTRAL_SCRIPTED = ["large", "guarded", "no budget", "stale", "invalid page"]


@pytest.mark.parametrize("outcome", [*NEUTRAL_SCRIPTED, "small", "seen", "gone"])
def test_an_outcome_where_no_answer_was_judged_leaves_the_count_as_it_is(
    store, monkeypatch, outcome
):
    titles = [f"Page {n}" for n in range(1, 7)]
    project = pages(store, titles)
    pages(store, ["Tiny"], lines=1)
    script = dict.fromkeys(map(slug, titles), "invalid")
    script["page-5"] = outcome if outcome in NEUTRAL_SCRIPTED else "invalid"
    scripted = Scripted(script)
    monkeypatch.setattr(cairn.arrange, "arrange_page", scripted)
    clock = Clock()
    arranger = worker(store, clock, min_claims=2)
    for title in titles[:4]:
        arranger.queue(store.account_id, project, slug(title))
    assert [d.status for d in run(arranger)] == ["invalid"] * 4

    neutral = {"small": "tiny", "seen": "page-1", "gone": "nothing"}.get(outcome, "page-5")
    arranger.queue(store.account_id, project, neutral)
    assert [d.status for d in run(arranger, limit=1)] == [outcome]
    assert (arranger.unusable, arranger.paused()) == (4, False)

    arranger.queue(store.account_id, project, "page-6")
    clock.now += 61  # past a "no budget" hold
    done = run(arranger)
    assert done[-1].slug == "page-6" and done[-1].status == "invalid"
    assert (arranger.unusable, arranger.paused()) == (5, True)


@pytest.mark.parametrize("outcome", ["arranged", "unchanged", "rejected"])
def test_an_answer_that_could_be_used_clears_the_count(store, monkeypatch, outcome):
    titles = [f"Page {n}" for n in range(1, 11)]
    project = pages(store, titles)
    script = dict.fromkeys(map(slug, titles), "invalid")
    script["page-5"] = outcome
    monkeypatch.setattr(cairn.arrange, "arrange_page", Scripted(script))
    arranger = worker(store, Clock())
    for title in titles[:9]:
        arranger.queue(store.account_id, project, slug(title))
    done = run(arranger)
    assert [d.status for d in done] == ["invalid"] * 4 + [outcome] + ["invalid"] * 4
    assert (arranger.unusable, arranger.paused()) == (4, False)
    arranger.queue(store.account_id, project, "page-10")
    run(arranger)
    assert (arranger.unusable, arranger.paused()) == (5, True)


def test_a_page_that_raises_counts_as_failed(store):
    titles = [f"Page {n}" for n in range(1, 7)]
    project = pages(store, titles)

    class Broken:
        name = "broken"

        def complete(self, messages):
            raise RuntimeError("not an LLMError")

    arranger = Arranger(Broken(), price=None, view_for=store.for_account, min_claims=1)
    for title in titles:
        arranger.queue(store.account_id, project, slug(title))
    assert [d.status for d in run(arranger)] == ["failed"] * 5
    assert arranger.paused() and len(arranger.pending()) == 1


# Several workspaces


class ByTitle:
    """Answers in prose for titles starting "A"; for a "B" title it first waits until the
    worker has paused (at most five seconds), moves the clock on, then answers with the
    claims in the order given, or in prose when `b_fails`."""

    name = "by-title"

    def __init__(self, clock: Clock, b_fails: bool = False) -> None:
        self.clock, self.b_fails = clock, b_fails
        self.titles: list[str] = []
        self.waited = False
        self.pause_applied = threading.Event()

    def complete(self, messages):
        user = messages[-1]["content"]
        title = user.split("Page title: ", 1)[1].split("\n", 1)[0]
        self.titles.append(title)
        if title.startswith("B") and not self.waited:
            assert self.pause_applied.wait(5), "the other workspace did not trigger a pause"
            self.waited = True
            self.clock.now += 100  # the pause began 100 s before this answer
        if title.startswith("A") or self.b_fails:
            return Reply(text="A better order, in words.")
        ids = [part.split('"', 1)[0] for part in user.split(' id="')[1:]]
        return Reply(text=json.dumps({"order": ids, "same": []}))


@pytest.mark.parametrize("b_fails", [False, True])
def test_a_pause_lets_the_pages_in_hand_finish_and_stops_every_page_not_started(
    store, caplog, b_fails, monkeypatch
):
    clock = Clock()
    model = ByTitle(clock, b_fails=b_fails)
    arranger = Arranger(
        model, price=None, view_for=store.for_account, min_claims=1, clock=clock, pause_after=2
    )
    count = arranger._count

    def observed(status):
        pause = count(status)
        if pause is not None:
            model.pause_applied.set()
        return pause

    monkeypatch.setattr(arranger, "_count", observed)
    queued = {}
    for name in ("A", "B"):
        view = store.for_account(store.create_account(name).id)
        titles = [f"{name}{n}" for n in range(1, 4 if name == "A" else 3)]
        project = pages(view, titles)
        for title in titles:
            arranger.queue(view.account_id, project, slug(title))
        queued[name] = view.account_id

    with caplog.at_level(logging.INFO, logger="cairn.access"):
        done = run(arranger)

    b_status = "invalid" if b_fails else "unchanged"
    assert [(d.slug, d.status) for d in done] == [
        ("a1", "invalid"),
        ("b1", b_status),
        ("a2", "invalid"),
    ]
    assert sorted(p.slug for p in arranger.pending()) == ["a3", "b2"]
    assert "A3" not in model.titles and "B2" not in model.titles
    assert [r.getMessage() for r in caplog.records].count(paused_line("by-title", 2, 600)) == 1
    assert sum("paused" in r.getMessage() for r in caplog.records) == 1
    # A usable answer in hand clears the count, but the pause stands; a failure in hand
    # counts, but neither moves the pause's end nor begins another.
    assert arranger.unusable == (3 if b_fails else 0)
    assert arranger.paused() and run(arranger) == []
    clock.now += 500  # 600 s after the pause began
    assert not arranger.paused()


# Settings, stop and the log


@pytest.mark.parametrize(
    "options",
    [
        {"pause_after": -1},
        {"pause_after": 1.5},
        {"pause_after": 5.0},
        {"pause_after": True},
        {"pause_seconds": 0},
        {"pause_seconds": -1.0},
        {"pause_seconds": math.nan},
        {"pause_seconds": math.inf},
    ],
)
def test_a_pause_setting_that_makes_no_sense_is_refused(store, options):
    with pytest.raises(ValueError):
        worker(store, Clock(), **options)


def test_the_defaults_pause_after_five_for_ten_minutes(store):
    arranger = worker(store, Clock())
    assert (arranger.pause_after, arranger.pause_seconds) == (5, 600.0)
    assert (cairn.arrange.PAUSE_AFTER, cairn.arrange.PAUSE_SECONDS) == (5, 600.0)


def test_a_pause_survives_stop_and_a_paused_pass_reads_nothing(store, monkeypatch):
    titles = [f"Page {n}" for n in range(1, 8)]
    project = pages(store, titles)
    scripted = Scripted(dict.fromkeys(map(slug, titles), "invalid"))
    monkeypatch.setattr(cairn.arrange, "arrange_page", scripted)
    looked = []

    def view_for(account_id):
        looked.append(account_id)
        return store.for_account(account_id)

    arranger = Arranger(Named(), price=None, view_for=view_for, min_claims=1, clock=Clock())
    for title in titles:
        arranger.queue(store.account_id, project, slug(title))
    assert len(run(arranger)) == 5
    assert arranger.stop(1.0) is True  # nothing in hand while paused
    before = (len(looked), len(scripted.asked), arranger.pending())
    assert run(arranger) == []
    assert (len(looked), len(scripted.asked), arranger.pending()) == before
    assert len(before[2]) == 2


def paused_line(model: str, count: int, seconds: float) -> str:
    return (
        f"arrange paused: {count} answers in a row from {model!r} could not be used;"
        f" no page goes to it for {seconds:g} seconds"
    )


def test_the_pause_line_names_the_model_the_count_and_the_seconds_and_no_page(
    store, monkeypatch, caplog
):
    titles = [f"Page {n}" for n in range(1, 4)]
    project = pages(store, titles)
    monkeypatch.setattr(
        cairn.arrange, "arrange_page", Scripted(dict.fromkeys(map(slug, titles), "failed"))
    )
    arranger = worker(store, Clock(), pause_after=3, pause_seconds=90.5)
    for title in titles:
        arranger.queue(store.account_id, project, slug(title))
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        run(arranger)
    (record,) = [r for r in caplog.records if "paused" in r.getMessage()]
    assert record.getMessage() == paused_line("scripted-model", 3, 90.5)
    assert record.levelno == logging.WARNING
    fields = {
        k: getattr(record, k)
        for k in ("actor_kind", "actor", "actor_id", "action", "project", "status")
    }
    assert fields == {
        "actor_kind": "system",
        "actor": None,
        "actor_id": None,
        "action": "arrange",
        "project": None,
        "status": 503,
    }
    assert "page-" not in record.getMessage() and project not in record.getMessage()


def test_after_the_pause_one_more_failure_pauses_again_with_the_count_so_far(
    store, monkeypatch, caplog
):
    titles = [f"Page {n}" for n in range(1, 5)]
    project = pages(store, titles)
    monkeypatch.setattr(
        cairn.arrange, "arrange_page", Scripted(dict.fromkeys(map(slug, titles), "invalid"))
    )
    clock = Clock()
    arranger = worker(store, clock, pause_after=2, pause_seconds=30.0)
    for title in titles:
        arranger.queue(store.account_id, project, slug(title))
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert len(run(arranger)) == 2
        clock.now += 29.999
        assert arranger.paused() and run(arranger) == []
        clock.now += 0.001
        assert not arranger.paused()
        assert len(run(arranger)) == 1
    lines = [r.getMessage() for r in caplog.records if "paused" in r.getMessage()]
    assert lines == [paused_line("scripted-model", 2, 30), paused_line("scripted-model", 3, 30)]
    assert arranger.paused() and len(arranger.pending()) == 1
