"""Unit tests for withheld notes in cairn.arrange: the inner TDD loop under
tests/acceptance/test_m2_withheld_notes.py. A page that cites a note written via a withheld
kind goes to no model; the worker tidies it instead.

Every model is a FakeModel; nothing leaves the process. Fixtures are synthetic."""

import asyncio
import json
import logging

import pytest

from cairn import arrange as arranging
from cairn.arrange import (
    STALE_TRIES,
    TIDY,
    WITHHELD,
    Arranger,
    Price,
    arrange_page,
    holds_withheld,
    tidy_page,
)
from cairn.compiler import GateResult, compile_note
from cairn.gate import lint_gate
from cairn.llm import FakeModel, Reply
from cairn.sources import KINDS, SourceKind
from cairn.store import Limits, StalePage, Store

FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
MAILED = "# Drift sync\n\n## Limits\n\n- Dee mailed the bucket keys.\n"
PRICE = Price(input_per_million=1_000_000, output_per_million=5_000_000)
HELD = frozenset({"mail-like"})


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def add(view, text, *, via=None, project="alpha"):
    found = view.get_project(project) or view.create_project(project)
    note, _ = view.add_note(found.id, text, via=via)
    assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")
    return found, note


def mixed(view):
    """The drift-sync page: two ordinary notes and one written via a withheld kind."""
    add(view, FIRST)
    add(view, SECOND)
    project, mailed = add(view, MAILED, via="mail-like")
    return project, view.get_page(project.id, "drift-sync"), mailed


def answer(page) -> Reply:
    numbers = list(range(len(page.claims), 0, -1))
    return Reply(text=json.dumps({"order": numbers, "same": []}), prompt_tokens=9)


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


def test_the_default_withholds_nothing_and_reads_no_note(store):
    project, page, _ = mixed(store)
    view = Counting(store)
    model = FakeModel([answer(page)])
    assert arrange_page(view, project.id, "drift-sync", model).status == "arranged"
    assert "get_note" not in view.calls and "list_notes" not in view.calls


def test_a_withheld_page_costs_nothing_and_says_one_fixed_sentence(store):
    project, page, _ = mixed(store)
    model = FakeModel([])
    result = arrange_page(store, project.id, "drift-sync", model, price=PRICE, withhold=HELD)
    assert (result.status, result.version, result.previous) == (
        "withheld",
        page.version,
        page.version,
    )
    assert (result.moved, result.merged, result.problem) == (0, 0, WITHHELD)
    assert result.draft.composer == model.name and result.draft.attempts == 0
    assert result.draft.prompt_tokens is None and model.requests == []
    assert store.model_reservations() == [] and store.usage().model_micros == 0
    assert store.get_page(project.id, "drift-sync") == page


def test_the_check_reads_only_the_notes_the_page_cites_each_once(store):
    for n in range(4):  # notes on other pages of the project, three of them withheld
        add(
            store,
            f"# Other {n}\n\n- Line {n} of another page.\n",
            via="mail-like" if n else None,
        )
    _, page, _ = mixed(store)
    view = Counting(store)
    assert holds_withheld(view, page, HELD)
    assert "list_notes" not in view.calls
    cited = {
        span.source_id
        for span in store.get_spans(i for c in page.claims for i in c.span_ids).values()
    }
    assert 1 <= view.calls.count("get_note") <= len(cited) == 3


def test_a_page_with_no_withheld_note_reads_each_cited_note_once(store):
    add(store, FIRST)
    project, _ = add(store, SECOND)
    page = store.get_page(project.id, "drift-sync")
    view = Counting(store)
    assert not holds_withheld(view, page, HELD)
    assert view.calls.count("get_note") == 2 and view.calls.count("get_spans") == 1


@pytest.mark.parametrize("when", ["large", "guarded", "no budget", "a model named cairn"])
def test_a_withheld_page_is_withheld_whatever_else_would_stop_it(store, when):
    project, page, _ = mixed(store)
    view, options, model = store, {}, FakeModel([])
    if when == "large":
        options["max_claims"] = 2
    elif when == "guarded":
        store.set_setting("guard.policy", "off")
        project, _ = add(store, "# Drift sync\n\n- Ask ada@example.org first.\n")
        store.set_setting("guard.policy", "secrets+personal")
        page = store.get_page(project.id, "drift-sync")
    elif when == "no budget":
        view = store.limited(Limits(model_budget_micros=0))
        options["price"] = PRICE
    else:
        model = FakeModel([], name="cairn")
    assert arrange_page(view, project.id, "drift-sync", model, **options).status != "withheld"
    result = arrange_page(view, project.id, "drift-sync", model, withhold=HELD, **options)
    assert (result.status, result.version, model.requests) == ("withheld", page.version, [])
    assert store.model_reservations() == []


def test_an_unknown_page_is_gone_before_anything_is_withheld(store):
    project, _, _ = mixed(store)
    result = arrange_page(store, project.id, "no-such-page", FakeModel([]), withhold=HELD)
    assert (result.status, result.version) == ("gone", None)


def test_a_note_the_view_cannot_read_withholds_its_page(store):
    """A span the view knows whose note it cannot read: the note's `via` is unknown, so
    the page is held back (it costs a tidy at most)."""
    add(store, FIRST)
    project, _ = add(store, SECOND)
    page = store.get_page(project.id, "drift-sync")

    class Blind(Counting):
        def __getattr__(self, name):
            if name == "get_note":
                return lambda note_id: None
            return super().__getattr__(name)

    assert holds_withheld(Blind(store), page, HELD)
    assert not holds_withheld(Blind(store), page, frozenset())


def test_a_forgotten_withheld_note_no_longer_counts(store):
    project, _, mailed = mixed(store)
    store.forget_note(mailed.id)
    page = store.get_page(project.id, "drift-sync")
    assert not holds_withheld(store, page, HELD)
    model = FakeModel([answer(page)])
    assert arrange_page(store, project.id, "drift-sync", model, withhold=HELD).status == "arranged"


def test_a_note_forgotten_between_the_page_and_its_spans_withholds_the_page(store):
    """Finding 9: arrange_page reads the page, then its spans, in two reads. A withheld note
    forgotten between them left spans the second read did not return, which counted for
    nothing, so the page that was read, the forgotten note's claims and the title it gave
    included, went to the model. A cited span the view does not return now withholds the
    page; the page's next version is judged again."""
    add(store, FIRST)
    secret = "# Drift sync\n\n## Limits\n\n- The acquisition price is 41 million.\n"
    project, mailed = add(store, secret, via="mail-like")
    titled = '"Dríft" SÝNC!'  # the topic of "Drift sync" (N1), in another spelling
    retitled, _ = store.add_note(project.id, MAILED, topic=titled, via="mail-like")
    assert compile_note(store, retitled, gate=lint_gate).status == "saved"
    page = store.get_page(project.id, "drift-sync")
    assert page.title == titled  # the last note to add claims named the page

    class Racing(Counting):
        """The real store; both mailed notes are forgotten right after the page is read."""

        def get_spans(self, span_ids):
            if not self.calls.count("get_spans_done"):
                self.calls.append("get_spans_done")
                store.forget_note(mailed.id)
                store.forget_note(retitled.id)
            return self._view.get_spans(span_ids)

    model = FakeModel([answer(page)])
    result = arrange_page(Racing(store), project.id, "drift-sync", model, withhold=HELD)
    assert (result.status, result.version) == ("withheld", page.version)
    assert model.requests == []
    after = store.get_page(project.id, "drift-sync")  # judged again at its next version
    assert after.version > page.version and after.title == "Drift sync"
    model = FakeModel([answer(after)])
    assert arrange_page(store, project.id, "drift-sync", model, withhold=HELD).status == "arranged"
    told = json.dumps(model.requests)
    assert "41 million" not in told and titled not in told


def test_a_cited_span_the_view_does_not_return_withholds_only_when_withholding(store):
    add(store, FIRST)
    project, _ = add(store, SECOND)
    page = store.get_page(project.id, "drift-sync")
    spans = store.get_spans(i for claim in page.claims for i in claim.span_ids)
    missing = dict(list(spans.items())[1:])
    assert holds_withheld(store, page, HELD, spans=missing)
    assert not holds_withheld(store, page, HELD, spans=spans)
    assert not holds_withheld(store, page, frozenset(), spans=missing)


def test_a_via_not_named_is_not_withheld(store):
    project, page, _ = mixed(store)
    model = FakeModel([answer(page)])
    result = arrange_page(store, project.id, "drift-sync", model, withhold={"another"})
    assert result.status == "arranged" and len(model.requests) == 1


# The worker


def worker(store, model, **options):
    options.setdefault("price", None)
    options.setdefault("view_for", store.for_account)
    return Arranger(model, **options)


def look(arranger, store, project_id, slug="drift-sync"):
    arranger.queue(store.account_id, project_id, slug)
    return asyncio.run(arranger.run_once())


def arrange_lines(caplog):
    return [r for r in caplog.records if getattr(r, "action", None) == "arrange"]


def short(view, slug_title, via):
    """A page of four claims, fewer than MIN_CLAIMS, one of them a repeat."""
    for word in ("a", "b"):
        project, _ = add(
            view, f"# {slug_title}\n\nAda keeps the keys.\n\n- Line {word}.\n", via=via
        )
    return project, view.get_page(project.id, slug_title.lower())


def test_a_withheld_page_too_small_to_send_is_still_tidied_and_never_sent(store):
    project, held = short(store, "Held", "mail-like")
    _, plain = short(store, "Plain", None)
    assert len(held.claims) == len(plain.claims) == 4
    model = FakeModel([])
    arranger = worker(store, model, withhold=HELD)
    done = look(arranger, store, project.id, "held") + look(arranger, store, project.id, "plain")
    assert [(d.slug, d.status, d.draft) for d in done] == [
        ("held", "withheld", None),
        ("plain", "small", None),
    ]
    assert (done[0].previous, done[0].version) == (held.version, held.version + 1)
    tidied = store.get_page(project.id, "held")
    assert len(tidied.claims) == 3 and tidied.metrics["arranged_by"] == TIDY
    assert store.get_page(project.id, "plain") == plain  # a small ordinary page: as before
    assert model.requests == [] and arranger.unusable == 0
    assert [d.status for d in look(arranger, store, project.id, "held")] == ["seen"]


def test_a_withheld_page_whose_tidy_the_gate_refuses_is_remembered_as_it_is(
    store, monkeypatch, caplog
):
    project, page, _ = mixed(store)
    refused = GateResult(passed=False, violations=["grounding.not_verbatim: claim 1"])
    monkeypatch.setattr(arranging, "verbatim_gate", lambda candidate, spans: refused)
    arranger = worker(store, FakeModel([]), withhold=HELD)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        done = look(arranger, store, project.id)
        assert [(d.status, d.previous, d.version) for d in done] == [
            ("withheld", page.version, page.version)
        ]
        assert [d.status for d in look(arranger, store, project.id)] == ["seen"]
    assert len(arrange_lines(caplog)) == 1
    assert store.get_page(project.id, "drift-sync") == page


def test_a_withheld_page_whose_tidy_comes_back_stale_is_queued_again_a_few_times(store):
    project, page, _ = mixed(store)

    class Moving:
        """A view on which every save finds the page moved on."""

        def __init__(self, view):
            self._view = view

        def __getattr__(self, name):
            return getattr(self._view, name)

        def save_page(self, *args, expected_version, **kwargs):
            raise StalePage(expected_version, expected_version + 1)

    arranger = worker(
        store, FakeModel([]), withhold=HELD, view_for=lambda a: Moving(store.for_account(a))
    )
    arranger.queue(store.account_id, project.id, "drift-sync")
    for tries in range(1, STALE_TRIES + 1):
        (done,) = asyncio.run(arranger.run_once())
        assert (done.status, done.previous) == ("withheld", page.version)
        assert len(arranger.pending()) == (0 if tries == STALE_TRIES else 1)
    assert store.get_page(project.id, "drift-sync") == page
    assert [d.status for d in look(arranger, store, project.id)] == ["withheld"]  # not remembered


def test_a_page_that_moves_between_the_check_and_the_tidy_is_looked_at_again(store, monkeypatch):
    project, page, _ = mixed(store)
    real = arranging.tidy_page

    def later(view, project_id, slug):
        add(store, "# Drift sync\n\n- Cy joined the sync later.\n")  # a new version meanwhile
        return real(view, project_id, slug)

    monkeypatch.setattr(arranging, "tidy_page", later)
    arranger = worker(store, FakeModel([]), withhold=HELD)
    (done,) = look(arranger, store, project.id)
    assert (done.status, done.previous) == ("withheld", page.version)
    assert len(arranger.pending()) == 1  # the version tidied was never judged: again
    monkeypatch.setattr(arranging, "tidy_page", real)
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["withheld"]
    assert [d.status for d in look(arranger, store, project.id)] == ["seen"]


def test_the_worker_reads_the_withheld_kinds_when_it_judges_a_page(store, monkeypatch):
    model = FakeModel([])
    arranger = worker(store, model)  # built before the kind is added
    stand_in = SourceKind(check=dict, open=dict, via="mail-like", withheld=True)
    monkeypatch.setitem(KINDS, "unit_stand_in", stand_in)
    project, _, _ = mixed(store)
    assert [d.status for d in look(arranger, store, project.id)] == ["withheld"]
    assert model.requests == []


def test_a_worker_that_withholds_nothing_sends_a_page_of_mail(store):
    for project in ("sent", "held"):
        add(store, FIRST, via="gmail", project=project)
        add(store, SECOND, via="gmail", project=project)
    sent, held = store.get_project("sent"), store.get_project("held")
    model = FakeModel([answer(store.get_page(sent.id, "drift-sync"))])
    sending = worker(store, model, withhold=())
    assert [d.status for d in look(sending, store, sent.id)] == ["arranged"]
    assert len(model.requests) == 1
    assert [d.status for d in look(worker(store, model), store, held.id)] == ["withheld"]
    assert len(model.requests) == 1  # the default withholds the mail


def test_a_workspace_with_no_budget_left_leaves_its_withheld_pages_queued(store):
    """The budget is read before the page, so a workspace out of budget costs one read a
    minute: its withheld pages wait, queued, as its other pages do."""
    project, page, _ = mixed(store)
    empty = store.limited(Limits(model_budget_micros=0))
    arranger = worker(store, FakeModel([]), price=PRICE, withhold=HELD, view_for=lambda a: empty)
    assert [d.status for d in look(arranger, store, project.id)] == ["no budget"]
    assert store.get_page(project.id, "drift-sync") == page
    assert [p.slug for p in arranger.pending()] == ["drift-sync"]


def test_without_a_model_nothing_is_withheld_and_every_page_is_tidied(store):
    add(store, FIRST, via="gmail")
    project, _ = add(store, SECOND, via="gmail")
    arranger = worker(store, None)
    assert [d.status for d in look(arranger, store, project.id)] == ["arranged"]
    assert store.get_page(project.id, "drift-sync").metrics["arranged_by"] == TIDY
    assert tidy_page(store, project.id, "drift-sync").status == "unchanged"
