"""A page an earlier build saved citing another project's passage takes new notes as any
page does (ninth round, item 2): save_page refuses a passage of another project only when
the page's latest version does not cite it already. A compile, the tidy and the arranging
model's save carry such a citation over as the page stands, until its note is forgotten or
its project deleted, which both rewrite the page (cairn.store.erasure); a new citation of
another project's passage is refused as before. Made-up notes."""

import json
import re

import pytest
from starlette.testclient import TestClient

from cairn.arrange import arrange_page, tidy_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import Reply
from cairn.server import create_app
from cairn.store import Claim, Store
from earlier_build import earlier_page

BASE = "https://cairn.example.test"
SLUG = "drift-sync"
SAID = "Drift mirrors two buckets."
FIRST = (
    f"# Drift sync\n\n{SAID}\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift keeps a log.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
REFUSED = r"^A page can cite only passages from its own project\.$"


def filed(view: Store, project_id: str, text: str):
    note, created = view.add_note(project_id, text)
    assert created and compile_note(view, note, gate=lint_gate).status == "saved"
    return note


def passages(view: Store, note):
    return [span for span in view.note_spans(note.id) if span.kind == "paragraph"]


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def legacy(store: Store):
    """`east`'s page, as an earlier build saved it: its own claims and, first, one that cites
    `desk`'s note in the words of its own first claim. The note's other passage is cited by
    nothing. (desk, east, the cited passage, the other passage.)"""
    desk, east = store.create_project("desk"), store.create_project("east")
    cited, other = passages(
        store, filed(store, desk.id, f"# Dispatch\n\n{SAID}\n\nThe crate ships at dawn.\n")
    )
    for text in (FIRST, SECOND):
        filed(store, east.id, text)
    page = store.get_page(east.id, SLUG)
    borrowed = Claim(id="cl_" + "9" * 16, text=SAID, span_ids=(cited.id,))
    earlier_page(store, east.id, SLUG, page.title, page.body, [borrowed, *page.claims])
    return desk, east, cited, other


def cites(store: Store, project_id: str, span_id: str) -> bool:
    page = store.get_page(project_id, SLUG)
    return any(span_id in claim.span_ids for claim in page.claims)


def test_a_note_compiled_into_the_page_is_saved_over_rest_and_never_waits(store):
    _, east, cited, _ = legacy(store)
    before = store.get_page(east.id, SLUG)
    key = store.create_key("t")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        text = "# Drift sync\n\nDrift rings the bell at two.\n"
        r = client.post("/api/projects/east/notes", headers={"x-api-key": key}, json={"text": text})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "saved"
    assert store.waiting_notes() == []
    page = store.get_page(east.id, SLUG)
    assert page.version == before.version + 1 and cites(store, east.id, cited.id)
    assert "Drift rings the bell at two." in page.body


def test_a_new_citation_of_another_projects_passage_is_refused_as_before(store):
    _, east, cited, other = legacy(store)
    page = store.get_page(east.id, SLUG)
    fresh = Claim(id="cl_" + "8" * 16, text=other.text, span_ids=(other.id,))
    with pytest.raises(ValueError, match=REFUSED):  # a passage the page does not cite
        store.save_page(east.id, SLUG, page.title, page.body, [*page.claims, fresh])
    with pytest.raises(ValueError, match=REFUSED):  # the cited one, into another page
        store.save_page(east.id, "other", "Other", "", [page.claims[0]])
    own = [claim for claim in page.claims if cited.id not in claim.span_ids]
    store.save_page(east.id, SLUG, page.title, page.body, own)  # the page lets it go
    with pytest.raises(ValueError, match=REFUSED):  # an earlier version cited it: not carried
        store.save_page(east.id, SLUG, page.title, page.body, page.claims)
    assert store.get_page(east.id, "other") is None
    assert not cites(store, east.id, cited.id)


def test_the_tidy_saves_the_page_as_a_compile_does(store):
    _, east, cited, _ = legacy(store)
    before = store.get_page(east.id, SLUG)
    done = tidy_page(store, east.id, SLUG)
    assert (done.status, done.version) == ("arranged", before.version + 1)
    assert cites(store, east.id, cited.id)


class Reverser:
    """A fake model: the claims in reverse, folding nothing."""

    name = "carried-reverser"

    def complete(self, messages) -> Reply:
        user = messages[-1]["content"]
        numbers = [int(n) for n in re.findall(r'<claim-[0-9a-f]+ n="(\d+)"', user)]
        answer = {"order": list(reversed(numbers)), "same": []}
        return Reply(text=json.dumps(answer), prompt_tokens=9, completion_tokens=9)


def test_the_arranging_models_save_saves_the_page_as_a_compile_does(store):
    _, east, cited, _ = legacy(store)
    before = store.get_page(east.id, SLUG)
    done = arrange_page(store, east.id, SLUG, Reverser())
    assert (done.status, done.version) == ("arranged", before.version + 1), done
    assert cites(store, east.id, cited.id)


def test_the_latest_version_is_read_only_where_a_passage_of_another_project_is_cited(store):
    """The refusal costs an ordinary save one statement, by the spans' primary key; the page's
    latest version is read for it only on the path where another project's passage is cited."""
    _, east, cited, _ = legacy(store)
    page = store.get_page(east.id, SLUG)
    own = [claim for claim in page.claims if cited.id not in claim.span_ids]
    made: dict[str, list[str]] = {}
    for name, slug, claims in (("ordinary", "plain", own), ("carried", SLUG, page.claims)):
        statements: list[str] = []
        store._observe(trace=statements.append)
        try:
            store.save_page(east.id, slug, page.title, page.body, claims)
        finally:
            store._observe(trace=None)
        made[name] = [s for s in statements if not s.startswith("--")]
    for statements in made.values():
        assert len([s for s in statements if "JOIN notes ON notes.id = spans.note_id" in s]) == 1
    carried = [s for s in made["carried"] if "SELECT claims FROM page_versions" in s]
    assert (len(carried), [s for s in made["ordinary"] if s in carried]) == (1, [])
