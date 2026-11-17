"""T4f acceptance tests (frozen): cairn tidies a page with no model.

Written by the orchestrator before implementation. Implementers must not edit this file.

Why: measured on 2026-11-13, an answer that only repeats the order it was given saved the same
page as a model's answers did on every large page. What made those pages better was cairn's
own work: a heading's claims kept together, and the claims that repeat folded. Neither needs
a model, a key or a budget.

Contract:
- `tidy_page(view, project_id, slug) -> Arranged` saves the page's next version with each
  heading's claims together (the tree of headings as `tree_order` has it, the claims under one
  heading in the order they had) and each group of repeats (`repeat_groups`) folded into its
  first claim, which then cites every source. It asks no model: nothing is reserved, spent or
  counted, whatever the workspace's model budget. The words are the page's own, the gate
  decides as for any arrangement, and the version before stays readable.
- It is recorded as an arrangement by cairn itself: "page.arranged" in the audit log and
  `arranged_by` in the version's metrics, both naming TIDY, never a claim's words.
- A page already tidy is "unchanged" and nothing is saved. A page that is not there is "gone".
- On a page with repeats the result is the page `arrange_page` saves for a model that keeps
  the order it was given.
- `Arranger(None, price=None, view_for=...)` is the worker without a model: it tidies every
  queued page, the small ones and those of a workspace with no model budget too.
"""

import asyncio
import json
import re

import pytest
from starlette.testclient import TestClient

from cairn.arrange import TIDY, Arranger, arrange_page, tidy_page
from cairn.compiler import compile_note, render_body
from cairn.gate import lint_gate
from cairn.llm import Reply
from cairn.server import create_app
from cairn.store import Limits, Store

BASE = "http://127.0.0.1:8765"
FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
THIRD = (  # a line of FIRST again under its own heading, and the same words under another
    "# Drift sync\n\n## Flags\n\n- `--delete` removes extra files.\n\n"
    "## Limits\n\n- `--dry-run` lists the changes first.\n"
)
# Three notes, no line twice, the second heading in between the first heading's two claims.
FLAGS_ONE = "# Drift sync\n\n## Flags\n\n- `--dry-run` lists the changes first.\n"
LIMITS = "# Drift sync\n\n## Limits\n\n- At most 500 files go in one batch.\n"
FLAGS_TWO = "# Drift sync\n\n## Flags\n\n- `--retries 3` helps on flaky links.\n"
TINY_ONE = "# Tiny\n\nIt runs nightly.\n"
TINY_TWO = "# Tiny\n\nIt runs nightly.\n\n- It keeps a log.\n"


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def page_of(view, *notes, project="alpha", slug="drift-sync"):
    found = view.get_project(project) or view.create_project(project)
    for text in notes:
        note, _ = view.add_note(found.id, text)
        assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")
    return found, view.get_page(found.id, slug)


class KeepsTheOrder:
    """A model that keeps the order it was given and folds the groups the request lists."""

    name = "keeps-the-order"

    def __init__(self) -> None:
        self.requests = 0

    def complete(self, messages) -> Reply:
        self.requests += 1
        user = "\n".join(m["content"] for m in messages if m["role"] != "system")
        tag = re.search(r'<(claim-[0-9a-f]+) n="1" id="cl_[0-9a-f]{16}"', user).group(1)
        head = user[: user.index(f"<{tag} ")]
        (listed,) = re.findall(rf"^[^\n]*{tag}[^\n]*: (\[.*\])$", head, re.MULTILINE)
        groups = json.loads(listed)
        folded = {n for group in groups for n in group[1:]}
        count = len(re.findall(rf'<{tag} n="\d+" id="cl_[0-9a-f]{{16}}"', user))
        order = [n for n in range(1, count + 1) if n not in folded]
        return Reply(text=json.dumps({"order": order, "same": groups}))


# One page


def test_a_page_is_tidied_with_no_model_and_the_words_stay_its_own(store):
    project, before = page_of(store, FIRST, SECOND, THIRD)
    intro, dry, delete, again, retries, limit, delete_again, dry_under_limits = before.claims
    assert intro.text == again.text and delete.text == delete_again.text
    assert dry.text == dry_under_limits.text  # the same words, under another heading

    result = tidy_page(store, project.id, "drift-sync")
    assert (result.status, result.previous, result.version) == (
        "arranged",
        before.version,
        before.version + 1,
    )
    assert result.merged == 2 and result.problem is None
    assert (result.draft.composer, result.draft.attempts) == (TIDY, 0)
    assert (result.draft.prompt_tokens, result.draft.completion_tokens) == (None, None)

    after = store.get_page(project.id, "drift-sync")
    # Each heading's claims together, in the order they had; the two repeats folded.
    assert [claim.id for claim in after.claims] == [
        intro.id,
        dry.id,
        delete.id,
        retries.id,
        limit.id,
        dry_under_limits.id,
    ]
    assert after.claims[0].span_ids == (*intro.span_ids, *again.span_ids)  # both sources
    assert after.claims[2].span_ids == (*delete.span_ids, *delete_again.span_ids)
    assert {claim.text for claim in after.claims} <= {claim.text for claim in before.claims}
    spans = store.get_spans([s for claim in after.claims for s in claim.span_ids])
    assert after.body == render_body("Drift sync", after.claims, spans)
    assert after.metrics["arranged_by"] == TIDY
    assert isinstance(TIDY, str) and TIDY.strip() and TIDY.isprintable()

    assert store.get_page(project.id, "drift-sync", before.version).claims == before.claims
    hits = store.search("retries flaky")
    assert hits and hits[0].claim_id == retries.id

    event = store.list_audit(limit=1)[0]
    assert (event.action, event.project) == ("page.arranged", "alpha")
    assert event.detail["page"] == "drift-sync" and event.detail["model"] == TIDY
    assert event.detail["merged"] == 2
    assert "Drift" not in json.dumps(event.detail)

    used = store.usage()
    assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (0, 0, 0)


def test_a_heading_split_by_another_is_brought_together_when_nothing_repeats(store):
    project, before = page_of(store, FLAGS_ONE, LIMITS, FLAGS_TWO)
    dry, limit, retries = before.claims
    result = tidy_page(store, project.id, "drift-sync")
    assert (result.status, result.merged) == ("arranged", 0) and result.moved >= 1
    after = store.get_page(project.id, "drift-sync")
    assert [claim.id for claim in after.claims] == [dry.id, retries.id, limit.id]
    assert [claim.text for claim in after.claims] == [dry.text, retries.text, limit.text]


def test_a_tidy_page_is_left_as_it_is(store):
    project, _ = page_of(store, FIRST, SECOND, THIRD)
    assert tidy_page(store, project.id, "drift-sync").status == "arranged"
    tidy = store.get_page(project.id, "drift-sync")
    events = len(store.list_audit(limit=50))

    again = tidy_page(store, project.id, "drift-sync")
    assert (again.status, again.version, again.moved, again.merged) == (
        "unchanged",
        tidy.version,
        0,
        0,
    )
    assert store.get_page(project.id, "drift-sync") == tidy
    assert len(store.list_audit(limit=50)) == events  # nothing was recorded

    assert tidy_page(store, project.id, "no-such-page").status == "gone"


def test_it_saves_the_page_a_model_that_keeps_the_order_would(store):
    project, _ = page_of(store, FIRST, SECOND, THIRD)
    tidy_page(store, project.id, "drift-sync")
    tidied = store.get_page(project.id, "drift-sync")

    other = store.for_account(store.create_account("Other").id)
    there, _ = page_of(other, FIRST, SECOND, THIRD)
    model = KeepsTheOrder()
    assert arrange_page(other, there.id, "drift-sync", model).status == "arranged"
    asked = other.get_page(there.id, "drift-sync")
    assert model.requests == 1
    assert [claim.text for claim in tidied.claims] == [claim.text for claim in asked.claims]
    assert tidied.body == asked.body


def test_it_needs_no_model_budget(store):
    ada = store.for_account(store.create_account("Ada").id)
    project, before = page_of(ada, FIRST, SECOND)
    spent = ada.limited(Limits(model_budget_micros=0))
    assert spent.model_budget_left() == 0
    result = tidy_page(spent, project.id, "drift-sync")
    assert (result.status, result.merged) == ("arranged", 1)
    assert len(ada.get_page(project.id, "drift-sync").claims) == len(before.claims) - 1
    assert ada.usage().model_micros == 0


# The worker without a model


def test_the_worker_without_a_model_tidies_the_pages_notes_touch(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    arranger = Arranger(None, price=None, view_for=store.for_account)
    app = create_app(store, public_url=BASE, arranger=arranger)
    headers = {"x-api-key": key}
    with TestClient(app, base_url=BASE) as client:
        project = client.post("/api/projects", headers=headers, json={"name": "alpha"}).json()["id"]
        for text in (FIRST, SECOND, TINY_ONE, TINY_TWO, "# Other page\n\nOne line.\n"):
            made = client.post(
                f"/api/projects/{project}/notes", headers=headers, json={"text": text}
            )
            assert made.status_code == 201
    assert {p.slug for p in arranger.pending()} == {"drift-sync", "tiny", "other-page"}

    done = asyncio.run(arranger.run_once())
    assert {(d.slug, d.status) for d in done} == {
        ("drift-sync", "arranged"),
        ("tiny", "arranged"),  # three claims: no page is too small to tidy
        ("other-page", "unchanged"),
    }
    assert len(store.get_page(project, "drift-sync").claims) == 5
    assert [claim.text for claim in store.get_page(project, "tiny").claims] == [
        "It runs nightly.",
        "- It keeps a log.",
    ]
    assert store.usage().model_micros == 0 and store.usage().model_tokens_in == 0
    assert arranger.pending() == [] and asyncio.run(arranger.run_once()) == []
    store.close()


def test_the_worker_without_a_model_tidies_a_workspace_with_no_budget(store):
    ada = store.for_account(store.create_account("Ada").id)
    project, _ = page_of(ada, FIRST, SECOND)

    def view_for(account_id):
        return store.for_account(account_id).limited(Limits(model_budget_micros=0))

    arranger = Arranger(None, price=None, view_for=view_for)
    arranger.queue(ada.account_id, project.id, "drift-sync")
    done = asyncio.run(arranger.run_once())
    assert [(d.slug, d.status) for d in done] == [("drift-sync", "arranged")]
    assert arranger.pending() == []
