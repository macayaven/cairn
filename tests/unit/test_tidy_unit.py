"""Unit tests for cairn.arrange.tidy_page and the worker without a model: the inner TDD loop
under tests/acceptance/test_t4f_tidy.py. No model is called; fixtures are synthetic."""

import asyncio
import logging
from pathlib import Path

import pytest

import cairn.arrange
from cairn.arrange import MAX_CLAIMS, TIDY, Arranger, Price, tidy_page, tree_order
from cairn.compiler import compile_note, render_body
from cairn.gate import lint_gate
from cairn.memory import ACCESS_LOGGER
from cairn.store import Claim, Limits, Store
from cairn.web import templating

PRICE = Price(input_per_million=1_000_000, output_per_million=5_000_000)


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def page_of(view, *notes, project="alpha", slug="tiny"):
    found = view.get_project(project) or view.create_project(project)
    for text in notes:
        note, _ = view.add_note(found.id, text)
        assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")
    return found, view.get_page(found.id, slug)


def arranged_events(view) -> list:
    return [event for event in view.list_audit(limit=200) if event.action == "page.arranged"]


# One page


def test_three_claims_that_repeat_fold_into_one_that_cites_all_three(store):
    project, before = page_of(
        store,
        "# Tiny\n\nIt runs nightly.\n",
        "# Tiny\n\nIt runs nightly.\n\n- It keeps a log.\n",
        "# Tiny\n\nIt runs nightly.\n\n- It rotates weekly.\n",
    )
    first, log, rotates = (before.claims[0], before.claims[2], before.claims[4])
    repeats = [before.claims[0], before.claims[1], before.claims[3]]
    assert {claim.text for claim in repeats} == {"It runs nightly."}
    result = tidy_page(store, project.id, "tiny")
    assert (result.status, result.merged) == ("arranged", 2)
    after = store.get_page(project.id, "tiny")
    assert [claim.id for claim in after.claims] == [first.id, log.id, rotates.id]
    assert after.claims[0].span_ids == tuple(s for claim in repeats for s in claim.span_ids)


def test_claims_that_differ_only_in_whitespace_fold(store):
    project, before = page_of(
        store,
        "# Tiny\n\nIt runs  nightly.\n",
        "# Tiny\n\nIt runs nightly.\n\n- It keeps a log.\n",
    )
    spaced, plain, log = before.claims
    assert spaced.text != plain.text and spaced.text.split() == plain.text.split()
    result = tidy_page(store, project.id, "tiny")
    assert (result.status, result.merged) == ("arranged", 1)
    after = store.get_page(project.id, "tiny")
    assert [claim.id for claim in after.claims] == [spaced.id, log.id]
    assert after.claims[0].text == spaced.text  # the first claim's words, as stored
    assert after.claims[0].span_ids == (*spaced.span_ids, *plain.span_ids)


NESTED_ONE = "# Deploy\n\nIt ships on Fridays.\n\n## Build\n\n- The image is built first.\n"
NESTED_TWO = (
    "# Deploy\n\n## Checks\n\n- Smoke tests run last.\n\n"
    "## Build\n\n### Cache\n\n- The cache is warmed.\n"
)
NESTED_THREE = "# Deploy\n\n## Build\n\n- Builds take four minutes.\n"


def test_a_page_with_nested_headings_keeps_each_heading_together(store):
    project, before = page_of(store, NESTED_ONE, NESTED_TWO, NESTED_THREE, slug="deploy")
    intro, image, smoke, cache, minutes = before.claims
    result = tidy_page(store, project.id, "deploy")
    assert (result.status, result.merged) == ("arranged", 0)
    after = store.get_page(project.id, "deploy")
    # Under Build its own claims, then its sub-heading Cache; Checks after Build, whose
    # first claim came first.
    assert [claim.id for claim in after.claims] == [
        intro.id,
        image.id,
        minutes.id,
        cache.id,
        smoke.id,
    ]
    spans = store.get_spans([s for claim in after.claims for s in claim.span_ids])
    assert after.body == render_body("Deploy", after.claims, spans)
    headings = [line for line in after.body.splitlines() if line.startswith("#")]
    assert len(headings) == len(set(headings)) == 4  # the title, Build, Cache and Checks
    assert tidy_page(store, project.id, "deploy").status == "unchanged"


def test_tree_order_of_a_tree_kept_order_is_that_order(store):
    """A tidy page is "unchanged": the step keeps a page it already sorted as it is."""
    _, before = page_of(store, NESTED_ONE, NESTED_TWO, NESTED_THREE, slug="deploy")
    spans = store.get_spans([s for claim in before.claims for s in claim.span_ids])
    sections = {claim.id: spans[claim.span_ids[0]].heading_path for claim in before.claims}
    once = tree_order([claim.id for claim in before.claims], sections)
    assert tree_order(once, sections) == once


def test_a_page_over_the_claim_bound_is_still_tidied(store):
    lines = "".join(f"- Item {n} is listed.\n" for n in range(MAX_CLAIMS + 1))
    again = "# Big\n\n- Item 0 is listed.\n"
    project, before = page_of(store, f"# Big\n\n{lines}", again, slug="big")
    assert len(before.claims) == MAX_CLAIMS + 2
    result = tidy_page(store, project.id, "big")
    assert (result.status, result.merged) == ("arranged", 1)
    assert len(store.get_page(project.id, "big").claims) == MAX_CLAIMS + 1


def test_a_page_that_names_one_claim_twice_is_an_invalid_page(store):
    project, before = page_of(store, "# Tiny\n\nIt runs nightly.\n\n- It keeps a log.\n")
    store.save_page(
        project.id, "tiny", before.title, before.body, (*before.claims, before.claims[0])
    )
    result = tidy_page(store, project.id, "tiny")
    assert (result.status, result.version) == ("invalid page", 2)
    assert result.problem == f"the page names claim {before.claims[0].id} more than once"
    assert store.page_versions(project.id, "tiny") == [1, 2]


def test_a_tidy_the_gate_refuses_is_rejected_and_nothing_is_recorded(store):
    project = store.create_project("alpha")
    claims = [
        Claim(id="cl_0", text="Made up.", span_ids=("sp_none",)),
        Claim(id="cl_1", text="Other.", span_ids=("sp_none",)),
        Claim(id="cl_2", text="Made up.", span_ids=("sp_none",)),
    ]
    store.save_page(project.id, "p", "P", "# P\n", claims)
    events = len(store.list_audit(limit=200))
    result = tidy_page(store, project.id, "p")
    assert (result.status, result.version, result.merged) == ("rejected", 1, 0)
    assert result.problem.startswith("the gate refused it: ")
    assert "Made up" not in result.problem
    assert store.page_versions(project.id, "p") == [1]
    assert store.get_page(project.id, "p").claims == tuple(claims)
    assert len(store.list_audit(limit=200)) == events


class Moving:
    """A view whose page moves (another note on it) between the read and the save."""

    def __init__(self, view, other, project_id):
        self._view, self._other, self._project_id = view, other, project_id

    def get_spans(self, ids):
        found = self._view.get_spans(ids)
        note, _ = self._other.add_note(self._project_id, "# Tiny\n\n- It is moved.\n")
        compile_note(self._other, note, gate=lint_gate)
        return found

    def __getattr__(self, name):
        return getattr(self._view, name)


def test_a_page_that_moved_under_it_is_stale_and_nothing_is_saved(store, tmp_path):
    project, before = page_of(
        store, "# Tiny\n\nIt runs nightly.\n", "# Tiny\n\nIt runs nightly.\n\n- It keeps a log.\n"
    )
    other = Store(tmp_path / "cairn.db")
    try:
        result = tidy_page(Moving(store, other, project.id), project.id, "tiny")
    finally:
        other.close()
    assert (result.status, result.previous, result.version) == (
        "stale",
        before.version,
        before.version + 1,
    )
    after = store.get_page(project.id, "tiny")
    assert after.version == before.version + 1 and len(after.claims) == len(before.claims) + 1
    assert arranged_events(store) == []


def test_a_tidy_is_never_screened_reserved_or_charged(store, monkeypatch):
    project, _ = page_of(
        store, "# Tiny\n\nIt runs nightly.\n", "# Tiny\n\nIt runs nightly.\n\n- It keeps a log.\n"
    )
    for name in ("screen", "reserve_model_use", "settle_model_use", "model_budget_left"):
        monkeypatch.setattr(Store, name, lambda *a, **k: pytest.fail("read the money or guard"))
    result = tidy_page(store, project.id, "tiny")
    assert result.status == "arranged"
    assert (result.draft.composer, result.draft.attempts, result.draft.usage_complete) == (
        TIDY,
        0,
        True,
    )


# The worker without a model


def test_a_price_without_a_model_is_refused(store):
    with pytest.raises(ValueError, match="price"):
        Arranger(None, price=PRICE, view_for=store.for_account)


def test_the_worker_without_a_model_never_says_small_no_budget_or_pauses(store, caplog):
    project, _ = page_of(
        store, "# Tiny\n\nIt runs nightly.\n", "# Tiny\n\nIt runs nightly.\n\n- It keeps a log.\n"
    )
    limited = store.limited(Limits(model_budget_micros=0))
    arranger = Arranger(None, price=None, view_for=lambda _: limited, pause_after=1)
    arranger.queue(store.account_id, project.id, "tiny")
    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        done = asyncio.run(arranger.run_once())
    assert [(d.status, d.draft) for d in done] == [("arranged", None)]
    (line,) = [r.getMessage() for r in caplog.records if r.name == ACCESS_LOGGER]
    assert line == f"arrange arranged in project {project.id}: version 2 to 3"

    def raising(account_id):
        raise RuntimeError("a page that raises")

    failing = Arranger(None, price=None, view_for=raising, pause_after=1)
    for n in range(6):
        failing.queue(store.account_id, project.id, f"page-{n}")
    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        caplog.clear()
        done = asyncio.run(failing.run_once())
    assert [d.status for d in done] == ["failed"] * 6
    assert not failing.paused() and failing.unusable == 0
    assert not any("paused" in r.getMessage() for r in caplog.records)


def test_the_worker_without_a_model_tidies_a_version_once_and_retries_a_stale_one(
    store, monkeypatch
):
    project, _ = page_of(
        store, "# Tiny\n\nIt runs nightly.\n", "# Tiny\n\nIt runs nightly.\n\n- It keeps a log.\n"
    )
    arranger = Arranger(None, price=None, view_for=store.for_account)
    arranger.queue(store.account_id, project.id, "tiny")
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["arranged"]
    arranger.queue(store.account_id, project.id, "tiny")
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["seen"]

    stale = cairn.arrange.Arranged(
        status="stale", version=4, previous=3, draft=cairn.arrange.DraftStats(composer=TIDY)
    )
    monkeypatch.setattr(cairn.arrange, "tidy_page", lambda view, project_id, slug: stale)
    again = Arranger(None, price=None, view_for=store.for_account)
    again.queue(store.account_id, project.id, "tiny")
    statuses = []
    for _ in range(4):
        statuses += [d.status for d in asyncio.run(again.run_once())]
    assert statuses == ["stale", "stale", "stale"] and again.pending() == []


def test_the_web_view_says_cairn_tidied_a_version(tmp_path):
    from starlette.testclient import TestClient

    from cairn.server import create_app

    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    project, _ = page_of(
        store, "# Tiny\n\nIt runs nightly.\n", "# Tiny\n\nIt runs nightly.\n\n- It keeps a log.\n"
    )
    assert tidy_page(store, project.id, "tiny").status == "arranged"
    app = create_app(store, public_url="http://127.0.0.1:8765")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.post("/login", data={"key": key}, headers={"sec-fetch-site": "same-origin"})
        page = client.get("/b/alpha/p/tiny").text
    store.close()
    said = " ".join(page.split())
    assert (
        "Arranged by cairn: each heading's claims were put together and repeated claims"
        " folded, with no model; every word is the sources' own." in said
    )
    assert "the model chose the order" not in said


def test_the_page_template_names_tidy_by_its_recorded_name():
    """The template cannot import cairn.arrange (no model code without an arranger), so it
    compares with the software's name, the template global `software`, which TIDY is (B3:
    no template spells the name)."""
    template = Path(cairn.arrange.__file__).parent / "templates" / "page.html"
    assert 'page.metrics.get("arranged_by") == software %}' in template.read_text()
    assert templating.templates.globals["software"] == TIDY


def test_the_module_docstring_says_what_a_tidy_skips_and_names():
    """Behaviour: the tests above (over MAX_CLAIMS, never screened or charged, a price with no
    model) and tests/acceptance/test_t4f_tidy.py (arranged_by and the audit event)."""
    doc = " ".join(cairn.arrange.__doc__.split())
    assert 'both naming TIDY ("cairn")' in doc and TIDY == "cairn"
    assert "no size bound (a page of more than MAX_CLAIMS claims is tidied too)" in doc
    assert "no guard screening, no reservation, no settlement and no budget read" in doc
    assert "A price with no model is a ValueError." in doc
    assert "the worker without a model, below" in doc


def test_a_model_cannot_carry_the_name_cairn_records_its_own_tidying_under(store):
    """`arranged_by` and the audit log name who arranged a version; a model called TIDY would
    read as cairn's own tidy, so it is never asked."""
    from cairn.arrange import arrange_page
    from cairn.llm import FakeModel

    project = store.create_project("names")
    for text in ("# Drift sync\n\nDrift mirrors two buckets.\n\n- It runs nightly.\n",):
        note, _ = store.add_note(project.id, text)
        compile_note(store, note, gate=lint_gate)
    before = store.get_page(project.id, "drift-sync")
    model = FakeModel(['{"order": [2, 1], "same": []}'], name=TIDY)
    result = arrange_page(store, project.id, "drift-sync", model)
    assert result.status == "failed" and TIDY in result.problem
    assert model.requests == [] and store.get_page(project.id, "drift-sync") == before


def test_a_page_the_tidy_leaves_as_it_is_writes_no_log_line(store, caplog):
    """With tidying on by default every saved note queues its page. A page already tidy is
    read and left alone; a log line for each would double the access log for nothing."""
    project, _ = page_of(store, "# Tiny\n\nIt runs nightly.\n\n- It keeps a log.\n")
    arranger = Arranger(None, price=None, view_for=lambda _: store)
    arranger.queue(store.account_id, project.id, "tiny")
    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        done = asyncio.run(arranger.run_once())
    assert [d.status for d in done] == ["unchanged"]
    assert [r.getMessage() for r in caplog.records if r.name == ACCESS_LOGGER] == []
