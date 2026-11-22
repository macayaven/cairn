"""A page that cites another project's passage, of its workspace or another, as only a
database an earlier build wrote held one before schema step 32 (or one a program wrote into
the tables): each writer that builds on the page's claims, a compile, the tidy and the
arranging model's save, leaves such a claim out, by id (compiler.own_claims, asking
Store.passages_of_other_projects), whatever the writer's reach. Nothing is refused, nothing
waits (report 8, must-fix 3: a key that may write only the receiving project got a
`rejected` note and a page that stayed put). The new version is held to its own project's
notes; the earlier versions stay, as after any write: a write is not an erasure. Made-up
notes."""

import json
import re
from contextlib import closing

import pytest
from starlette.testclient import TestClient

from cairn.arrange import arrange_page, tidy_page
from cairn.compiler import compile_note, own_claims
from cairn.gate import lint_gate
from cairn.llm import Reply
from cairn.memory import FiledOnce, Memory
from cairn.server import create_app
from cairn.store import StalePage, Store
from support.store.earlier_page_writers import BASE, SAID, SLUG, filed, legacy, passages
from support.store.earlier_page_writers import store as store
from support.store.rt1_forget_workspace_held import borrowed, citing, passage

NEW = "Drift rings the bell at two."


def cites(store: Store, project_id: str, span_id: str, version: int | None = None) -> bool:
    page = store.get_page(project_id, SLUG, version)
    return any(span_id in claim.span_ids for claim in page.claims)


@pytest.mark.parametrize("source", ["project", "workspace"])
@pytest.mark.parametrize("reach", ["east alone", "every project"])
def test_a_note_posted_over_rest_is_saved_and_the_other_projects_claim_left_out(
    store, source, reach
):
    """The reviewer's case exactly: the committed fixture, a key that may write only the
    receiving project, a note posted over REST. 201, saved, nothing waits, the new note's
    claim on the page and the other project's gone; the version before keeps it."""
    east, cited = legacy(store, source)
    before = store.get_page(east.id, SLUG)
    versions = store.page_versions(east.id, SLUG)
    key = store.create_key("t", projects=[east.id] if reach == "east alone" else None)
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        text = f"# Drift sync\n\n{NEW}\n"
        r = client.post("/api/projects/east/notes", headers={"x-api-key": key}, json={"text": text})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "saved", r.json()
    assert store.waiting_notes() == []
    page = store.get_page(east.id, SLUG)
    assert page.version == before.version + 1 and NEW in [c.text for c in page.claims]
    assert not cites(store, east.id, cited.id)
    assert [c.text for c in page.claims].count(SAID) == 1  # east's own passage says it too
    assert store.page_versions(east.id, SLUG) == [*versions, page.version]
    assert cites(store, east.id, cited.id, before.version)


@pytest.mark.parametrize("source", ["project", "workspace"])
def test_the_store_says_which_ids_of_a_stored_page_are_another_projects(store, source):
    """Report 9, must-fix 3: the store answers for the ids the stored page version cites, and
    for no id a caller names; the version the writer read, or nothing for one not stored."""
    east, cited = legacy(store, source)
    page = store.get_page(east.id, SLUG)
    view = store.narrow([east.id])
    assert view.passages_of_other_projects(east.id, SLUG, page.version) == frozenset({cited.id})
    assert view.get_spans([cited.id]) == {}  # still no text of it for this view
    assert view.passages_of_other_projects(east.id, SLUG, page.version + 1) == frozenset()
    assert view.passages_of_other_projects(east.id, "no-such-page", 1) == frozenset()
    out = store.create_project("out")
    assert store.narrow([out.id]).passages_of_other_projects(east.id, SLUG, 1) == frozenset()
    store.save_page(east.id, SLUG, page.title, page.body, page.claims[1:])  # a later version
    assert view.passages_of_other_projects(east.id, SLUG, page.version + 1) == frozenset()
    assert view.passages_of_other_projects(east.id, SLUG, page.version) == frozenset({cited.id})


def test_the_reviewers_probe_cannot_be_asked(tmp_path):
    """A clean store, a read-only view of one project, an id of another workspace it knows
    and an absent id: there is no argument to pass an id in, and its page's answer is
    empty whatever any other workspace holds."""
    with closing(Store(tmp_path / "probe.db")) as root:
        desk = root.create_project("desk")
        filed(root, desk.id, f"# Drift sync\n\n{SAID}\n")
        other = root.for_account(root.create_account("Elsewhere").id)
        far = other.create_project("far")
        known = passages(other, filed(other, far.id, "# Far\n\nWren keeps the far log.\n"))[0]
        reader = root.narrow([desk.id], access="read")
        with pytest.raises(TypeError):
            reader.passages_of_other_projects(desk.id, [known.id, "sp_" + "0" * 16])
        page = reader.get_page(desk.id, SLUG)
        assert reader.passages_of_other_projects(desk.id, SLUG, page.version) == frozenset()


def test_the_tidy_leaves_the_claim_out_through_a_view_of_the_project_alone(store):
    east, cited = legacy(store, "workspace", own=lambda claims: tuple(reversed(claims)))
    before = store.get_page(east.id, SLUG)
    done = tidy_page(store.narrow([east.id]), east.id, SLUG)
    assert (done.status, done.version) == ("arranged", before.version + 1), done
    assert not cites(store, east.id, cited.id) and cites(store, east.id, cited.id, before.version)


class Reverser:
    """A fake model: the claims in reverse, folding nothing."""

    name = "writer-reverser"

    def complete(self, messages) -> Reply:
        user = messages[-1]["content"]
        numbers = [int(n) for n in re.findall(r'<claim-[0-9a-f]+ n="(\d+)"', user)]
        answer = {"order": list(reversed(numbers)), "same": []}
        return Reply(text=json.dumps(answer), prompt_tokens=9, completion_tokens=9)


def test_the_arranging_models_save_leaves_the_claim_out_through_a_view_of_the_project_alone(
    store,
):
    east, cited = legacy(store)
    before = store.get_page(east.id, SLUG)
    done = arrange_page(store.narrow([east.id]), east.id, SLUG, Reverser())
    assert (done.status, done.version) == ("arranged", before.version + 1), done
    page = store.get_page(east.id, SLUG)
    assert not cites(store, east.id, cited.id)
    assert len(page.claims) == len(before.claims) - 1


# save_page refuses every claim that cites a passage the store holds of another project's
# note, the one the page carries too (report 8: the carried exception is gone)

REFUSED = r"^A page can cite only passages from its own project\.$"


@pytest.mark.parametrize("source", ["project", "workspace"])
def test_save_page_refuses_the_claim_the_page_carries_as_any_other(store, source):
    east, _ = legacy(store, source)
    page = store.get_page(east.id, SLUG)
    with pytest.raises(ValueError, match=REFUSED):  # the page as it stands, saved again
        store.save_page(east.id, SLUG, page.title, page.body, page.claims)
    with pytest.raises(ValueError, match=REFUSED):  # at the version it read
        store.save_page(
            east.id, SLUG, page.title, page.body, page.claims, expected_version=page.version
        )
    with pytest.raises(ValueError, match=REFUSED):  # into another page
        store.save_page(east.id, "other", "Other", "", [page.claims[0]])
    assert store.get_page(east.id, SLUG).version == page.version
    assert store.get_page(east.id, "other") is None


def test_the_refusal_comes_after_the_version_check(store):
    """A save drafted before the page moved on is StalePage, which a writer retries on the
    page as it stands, never the refusal."""
    east, _ = legacy(store)
    read = store.get_page(east.id, SLUG)
    filed(store, east.id, f"# Drift sync\n\n{NEW}\n")
    with pytest.raises(StalePage) as raised:
        store.save_page(
            east.id, SLUG, read.title, read.body, read.claims, expected_version=read.version
        )
    assert type(raised.value) is StalePage


def test_the_refusal_costs_one_statement_by_the_spans_key_and_reads_no_page(store):
    east, cited = legacy(store)
    page = store.get_page(east.id, SLUG)
    own = [claim for claim in page.claims if cited.id not in claim.span_ids]
    made: dict[str, list[str]] = {}
    for name, slug, claims in (("ordinary", "plain", own), ("refused", SLUG, page.claims)):
        statements: list[str] = []
        store._observe(trace=statements.append)
        try:
            store.save_page(east.id, slug, page.title, page.body, claims)
        except ValueError:
            pass
        finally:
            store._observe(trace=None)
        made[name] = [s for s in statements if not s.startswith("--")]
    for statements in made.values():
        asked = [s for s in statements if "JOIN notes ON notes.id = spans.note_id" in s]
        assert len(asked) == 1 and "page_versions" not in asked[0]
    assert not [s for s in made["refused"] if "SELECT claims FROM page_versions" in s]


def test_a_fold_left_out_whole_comes_back_when_its_note_is_filed_again(store):
    """Report 9, forget tester: a writer leaves out whole a claim that folds east's own
    passage with desk's. Filing an unrelated note, or the tidy, does not bring east's passage
    back onto the page, and neither does its note's text alone when the note had a source
    reference (report 10: that is another note); filing the same note again, with its
    original source reference and topic, does: the store answers with the stored note and
    compiles it again (compiler.own_claims says so)."""
    memory = Memory(store, lint_gate)
    desk, east = store.create_project("desk"), store.create_project("east")
    theirs = passage(store, filed(store, desk.id, "# Dispatch\n\nQuill sealed the crate.\n"))
    text, ref = "# Minutes\n\nThe ledger opens at noon.\n", "minutes-0412"
    local, created = store.add_note(east.id, text, source_ref=ref, topic="Minutes")
    assert created and compile_note(store, local, gate=lint_gate).status == "saved"
    ours = passage(store, local)
    borrowed(store, east.id, "minutes", citing(1, ours.text, ours, theirs))

    def on_page() -> bool:
        page = store.get_page(east.id, "minutes")
        return ours.id in {i for claim in page.claims for i in claim.span_ids}

    assert memory.note("east", "# Minutes\n\nThe bell rings at two.\n")["status"] == "saved"
    assert not on_page()
    tidy_page(store, east.id, "minutes")
    assert not on_page()
    alone = memory.note("east", text, topic="Minutes")  # no source reference: another note
    assert alone["note_id"] != local.id and alone["created"] and not on_page()
    with pytest.raises(FiledOnce):  # the same note, for another topic: filed once
        memory.note("east", text, source_ref=ref, topic="Ledger")
    again = memory.note("east", text, source_ref=ref, topic="Minutes")
    assert (again["note_id"], again["created"], again["status"]) == (local.id, False, "saved")
    assert on_page()
    said = " ".join(own_claims.__doc__.split())
    assert (
        "Filing another note, or a tidy, does not bring it back: filing the same note again,"
        " with its original source reference and topic, does, since the store answers with the"
        " stored note and compiles it again."
    ) in said
