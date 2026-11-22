"""Fix H and the checks: a body that now holds a claim's parent headings, deeper levels and a
parent written again is still accepted by the gates, the fake-model arranger and the tidy.
And the reach of "renders as before": a note whose first heading is not the page's title."""

import json

import pytest

from cairn.arrange import arrange_page, tidy_page
from cairn.compiler import CandidatePage, compile_note
from cairn.gate import lint_gate, verbatim_gate
from cairn.llm import FakeModel, Reply
from cairn.store import Store

NESTED = (
    "# Rollout\n\n## Release `v2.4.1` from /srv/app/releases\n\n### Port 8443\n\n"
    "- Open 8443 before the switch.\n\n### Hosts\n\n- Drain kestrel-01 first.\n\n"
    "## Release `v2.4.1` from /srv/app/releases\n\n- Tag it once.\n"
)
LOOSE = "Always keep the old release for a day.\n"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def rollout(store):
    project = store.create_project("alpha-ops")
    note, _ = store.add_note(project.id, NESTED)
    assert compile_note(store, note, gate=lint_gate).status == "saved"
    note, _ = store.add_note(project.id, LOOSE, title="Keep", topic="Rollout")
    assert compile_note(store, note, gate=lint_gate).status == "saved"
    return project, store.get_page(project.id, "rollout")


def test_the_gates_accept_a_body_with_parents_written_again(store):
    _, page = rollout(store)
    assert page.body.count("## Release `v2.4.1` from /srv/app/releases") == 2
    assert "### Port 8443" in page.body and "### Hosts" in page.body
    spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
    candidate = CandidatePage(slug=page.slug, title=page.title, body=page.body, claims=page.claims)
    assert verbatim_gate(candidate, spans).violations == []
    assert lint_gate(candidate, spans).violations == []


def test_the_fake_model_arranger_and_the_tidy_accept_it(store):
    project, page = rollout(store)
    order = [claim.id for claim in page.claims][::-1]
    model = FakeModel([Reply(text=json.dumps({"order": order, "same": []}))])
    assert arrange_page(store, project.id, page.slug, model).status == "arranged"
    assert tidy_page(store, project.id, page.slug).status in ("arranged", "unchanged")


def test_a_one_note_page_whose_first_heading_is_not_its_title_shows_that_heading(store):
    project = store.create_project("alpha-ops")
    note, _ = store.add_note(project.id, "# Notes\n\n## Install\n\nGet it.\n", topic="Setup")
    assert compile_note(store, note).status == "saved"
    page = store.get_page(project.id, "setup")
    assert page.body == "# Setup\n\n## Notes\n\n### Install\n\nGet it.\n"
