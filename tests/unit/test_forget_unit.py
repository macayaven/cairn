"""Unit tests for deleting one project and forgetting one note (H5b): what
tests/acceptance/test_h5b_forget.py does not pin. Fixtures are synthetic; nothing sleeps."""

import asyncio
import io
import json
import sqlite3
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

from cairn.arrange import Arranger, arrange_page
from cairn.compiler import claim_id, compile_note, render_body
from cairn.llm import FakeModel, Reply
from cairn.memory import Memory, NotFound
from cairn.spans import chunk
from cairn.store import (
    DEFAULT_ACCOUNT_ID,
    CitesForgotten,
    Claim,
    ForgottenNote,
    Limits,
    StalePage,
    Store,
)
from cairn.store.erasure import kept_claims

WEEKLY = "# Weekly\n\n- Brook owns the rollback.\n- Quill ships on Thursday.\n"
ECHO = "# Weekly\n\n- Brook owns the rollback.\n- Lumen keeps its own rule.\n"
LATER = "# Weekly\n\n- Dee starts in March.\n"


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    opened = Store(db)
    yield opened
    opened.close()


def add(view: Store, project_id: str, text: str, **fields):
    note, created = view.add_note(project_id, text, **fields)
    assert created and compile_note(view, note).status == "saved"
    return note


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def in_files(db: Path, word: str) -> bool:
    return any(
        word.encode() in path.read_bytes().lower()
        for path in db.parent.iterdir()
        if path.name.startswith(db.name)
    )


def fold(store: Store, project_id: str, slug: str) -> None:
    """Save the page with its claims of identical text folded into the first, as an
    arrangement does: one claim citing every span of that text."""
    page = store.get_page(project_id, slug)
    folded: dict[str, Claim] = {}
    for claim in page.claims:
        first = folded.get(claim.text)
        if first is None:
            folded[claim.text] = claim
        else:
            ids = (*first.span_ids, *claim.span_ids)
            folded[claim.text] = first.model_copy(update={"span_ids": ids})
    claims = list(folded.values())
    spans = store.get_spans(i for c in claims for i in c.span_ids)
    body = render_body(page.title, claims, spans)
    store.save_page(project_id, slug, page.title, body, claims, expected_version=page.version)


# A claim that cites several notes


def test_a_folded_claim_keeps_the_note_left_and_goes_with_both(store):
    project = store.create_project("team")
    weekly, echo, later = (add(store, project.id, t) for t in (WEEKLY, ECHO, LATER))
    fold(store, project.id, "weekly")
    (shared,) = [c for c in store.get_page(project.id, "weekly").claims if "rollback" in c.text]
    assert len(shared.span_ids) == 2

    assert store.forget_note(weekly.id).rebuilt == ("weekly",)
    page = store.get_page(project.id, "weekly")
    (kept,) = [c for c in page.claims if "rollback" in c.text]
    echo_spans = {s.id for s in store.note_spans(echo.id)}
    assert kept.text == shared.text and set(kept.span_ids) <= echo_spans
    # Its id was made from the forgotten note's span (a digest of its text): it takes one
    # made from the span it keeps.
    assert shared.id == claim_id(shared.span_ids[0]) and kept.id == claim_id(kept.span_ids[0])
    assert "Thursday" not in page.body and "rollback" in page.body

    store.forget_note(echo.id)
    page = store.get_page(project.id, "weekly")
    assert [c.text for c in page.claims] == ["- Dee starts in March."]
    spans = store.get_spans(page.claims[0].span_ids)
    assert page.body == render_body(page.title, page.claims, spans)
    assert store.search("rollback") == [] and store.forget_preview(later.id).removed == ("weekly",)


def test_a_claim_that_joins_sentences_of_a_forgotten_note_goes_with_it(store):
    project = store.create_project("team")
    first = add(store, project.id, "# Plan\n\nQuill ships first.\n")
    second = add(store, project.id, "# Plan\n\nLumen ships second.\n")
    page = store.get_page(project.id, "plan")
    a, b = page.claims
    joined = Claim(id="cl_joined", text=f"{a.text} {b.text}", span_ids=(*a.span_ids, *b.span_ids))
    spans = store.get_spans(joined.span_ids)
    store.save_page(
        project.id, "plan", "Plan", render_body("Plan", [joined, b], spans), [joined, b]
    )
    store.forget_note(first.id)
    page = store.get_page(project.id, "plan")
    assert [c.text for c in page.claims] == ["Lumen ships second."]  # the joined claim went
    assert "Quill" not in page.body and store.get_note(second.id) is not None


def test_kept_claims_keep_order_ids_and_text():
    span = chunk("n", "Lumen  keeps\nits rule.")[0]  # a passage, which the gate reads by kind
    claims = [
        Claim(id="cl_1", text="Gone.", span_ids=("sp_a",)),
        Claim(id="cl_2", text="Lumen keeps its rule.", span_ids=("sp_a", "sp_b")),
        Claim(id="cl_3", text="Other.", span_ids=("sp_c",)),
        Claim(id="cl_a", text="Lumen keeps its rule.", span_ids=("sp_a", "sp_b")),
        Claim(id="cl_a2", text="Lumen keeps its rule.", span_ids=("sp_a2", "sp_b")),
    ]
    gone = frozenset({"sp_a", "sp_a2"})
    kept = kept_claims(claims, gone, {"sp_b": span})
    assert [(c.id, c.span_ids) for c in kept[:2]] == [("cl_2", ("sp_b",)), ("cl_3", ("sp_c",))]
    # An id made from a forgotten span is made again from the first span kept, or, when a
    # claim already has that one, from the spans kept: never from the forgotten span.
    assert kept[2].id == "cl_b" and kept[2].span_ids == ("sp_b",)
    assert kept[3].id not in {"cl_a2", "cl_b", "cl_2", "cl_3"} and kept[3].id.startswith("cl_")


# What of a forgotten note can outlive it on a page that stays

SAME = "Fire Bob? Q3 plan for the northern and the southern regions, final"  # one slug
TITLES = (
    f"{SAME} saltmarsh",  # another topic (N1): its last word counts
    SAME.lower().replace("?", "").replace(",", ""),  # SAME's topic, in another spelling
    "Fire-Bob: Q3 Plan for the Northern and the Southern Regions - Final",  # and another
)


def test_a_rebuilt_page_takes_its_title_from_the_notes_left(store, db):
    project = store.create_project("team")
    notes = [
        store.add_note(project.id, f"# {title}\n\n- Point {n} of the plan.\n")[0]
        for n, title in enumerate(TITLES)
    ]
    first, second, third = notes
    for note in (second, third):
        assert compile_note(store, note).status == "saved"
    slug = "fire-bob-q3-plan-for-the-northern-and-the-southern-regions-final"
    # The first note joins the page as a page merged before N1 holds it (its slug alone filed
    # it then), and its latest version has the first note's title.
    page = store.get_page(project.id, slug)
    added = [s for s in store.note_spans(first.id) if s.kind == "bullet"]
    claims = [
        *page.claims,
        *(Claim(id=claim_id(s.id), text=s.text, span_ids=(s.id,)) for s in added),
    ]
    spans = store.get_spans({i for claim in claims for i in claim.span_ids})
    store.save_page(project.id, slug, TITLES[0], render_body(TITLES[0], claims, spans), claims)
    assert store.get_page(project.id, slug).title == TITLES[0]
    assert store.search("saltmarsh")  # the title is searched

    assert store.forget_note(first.id).rebuilt == (slug,)
    page = store.get_page(project.id, slug)
    assert page.title == TITLES[1]  # the oldest note left, as a new page takes its first's
    assert page.body.startswith(f"# {TITLES[1]}\n") and "saltmarsh" not in page.body
    assert store.search("saltmarsh") == []
    assert TITLES[0] not in dump(db) and "saltmarsh" not in dump(db).lower()
    assert not in_files(db, "saltmarsh")  # nor in the index's own pages, or the log

    store.forget_note(second.id)
    assert store.get_page(project.id, slug).title == TITLES[2]


# Racing writers


class Interleaved:
    """A view that runs `before_save` once, just before its first save_page: what another
    request does while a compile or an arrangement is between its read and its save."""

    def __init__(self, view: Store, before_save) -> None:
        self._view, self._before = view, before_save

    def save_page(self, *args, **kwargs):
        if self._before is not None:
            run, self._before = self._before, None
            run()
        return self._view.save_page(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._view, name)


def test_a_compile_of_a_note_forgotten_before_it_ran_saves_nothing(store):
    project = store.create_project("team")
    add(store, project.id, LATER)
    note, _ = store.add_note(project.id, WEEKLY)
    store.forget_note(note.id)
    result = compile_note(store, note)
    assert (result.status, result.version) == ("unchanged", 1)  # nothing of it to compile
    assert "rollback" not in store.get_page(project.id, "weekly").body


def test_a_compile_that_read_the_page_before_a_forget_builds_on_the_new_version(store):
    project = store.create_project("team")
    weekly, _ = add(store, project.id, WEEKLY), add(store, project.id, ECHO)
    note, _ = store.add_note(project.id, LATER)
    view = Interleaved(store, lambda: store.forget_note(weekly.id))
    result = compile_note(view, note)  # read version 2; the forget made version 3
    page = store.get_page(project.id, "weekly")
    assert (result.status, result.version, page.version) == ("saved", 4, 4)
    assert store.page_versions(project.id, "weekly") == [3, 4]
    assert "Thursday" not in page.body and "Lumen" in page.body and "March" in page.body


def test_a_compile_whose_own_note_is_forgotten_meanwhile_saves_nothing(store):
    project = store.create_project("team")
    add(store, project.id, LATER)
    note, _ = store.add_note(project.id, WEEKLY)
    view = Interleaved(store, lambda: store.forget_note(note.id))
    result = compile_note(view, note)  # CitesForgotten, then nothing left to compile
    assert (result.status, result.version) == ("unchanged", 1)
    page = store.get_page(project.id, "weekly")
    assert page.version == 1 and "rollback" not in page.body


def test_a_page_removed_and_made_again_meanwhile_never_gets_a_forgotten_note_back(store):
    project = store.create_project("team")
    weekly = add(store, project.id, WEEKLY)
    note, _ = store.add_note(project.id, ECHO)

    def meanwhile():
        assert store.forget_note(weekly.id).removed == ("weekly",)
        add(store, project.id, LATER)  # the page again, at version 1

    result = compile_note(Interleaved(store, meanwhile), note)
    page = store.get_page(project.id, "weekly")
    assert (result.status, page.version) == ("saved", 2)
    assert "Thursday" not in page.body and "Lumen" in page.body and "March" in page.body


def test_a_save_into_a_project_that_forgot_a_note_refuses_its_spans(store):
    project = store.create_project("team")
    weekly = add(store, project.id, WEEKLY)
    old = store.get_page(project.id, "weekly")
    store.forget_note(weekly.id)
    with pytest.raises(CitesForgotten) as refused:
        store.save_page(project.id, "weekly", old.title, old.body, old.claims, expected_version=0)
    assert isinstance(refused.value, StalePage) and store.get_page(project.id, "weekly") is None


def test_an_arrangement_read_before_a_forget_is_stale_and_a_gone_page_is_dropped(store):
    project = store.create_project("team")
    weekly, _ = add(store, project.id, WEEKLY), add(store, project.id, ECHO)
    order = [c.id for c in store.get_page(project.id, "weekly").claims][::-1]
    model = FakeModel([Reply(text=json.dumps({"order": order, "same": []}))])
    view = Interleaved(store, lambda: store.forget_note(weekly.id))
    result = arrange_page(view, project.id, "weekly", model)
    assert result.status == "stale" and "Thursday" not in store.get_page(project.id, "weekly").body

    arranger = Arranger(FakeModel([]), price=None, view_for=store.for_account, min_claims=1)
    arranger.queue(store.account_id, project.id, "weekly")
    assert store.delete_project(project.id)
    (done,) = asyncio.run(arranger.run_once())
    assert done.status == "gone" and arranger.pending() == []


def test_a_project_deleted_while_a_note_is_written_is_not_found(store):
    project = store.create_project("team")

    class Deleting(Interleaved):
        def get_page(self, *args, **kwargs):  # the compile's first read
            if self._before is not None:
                run, self._before = self._before, None
                run()
            return self._view.get_page(*args, **kwargs)

    memory = Memory(Deleting(store, lambda: store.delete_project(project.id)), compile_gate())
    with pytest.raises(NotFound):
        memory.note("team", WEEKLY)
    assert store.list_projects() == [] and store.get_project("team") is None


def compile_gate():
    from cairn.compiler import provenance_gate

    return provenance_gate


# Plans


def test_forgetting_needs_no_room_under_any_cap_and_versions_per_page_holds(store):
    project = store.create_project("team")
    weekly, echo = add(store, project.id, WEEKLY), add(store, project.id, ECHO)
    full = store.limited(
        Limits(notes_per_month=0, storage_bytes=0, projects=0, versions_per_page=1)
    )
    assert full.forget_note(weekly.id).rebuilt == ("weekly",)
    assert store.page_versions(project.id, "weekly") == [3]
    two = store.limited(Limits(versions_per_page=2))
    add(two, project.id, LATER)
    assert store.page_versions(project.id, "weekly") == [3, 4]  # numbers go on, never restart
    assert two.forget_note(echo.id).rebuilt == ("weekly",)
    assert store.page_versions(project.id, "weekly") == [5]


# A forgotten note stays out


def test_the_same_text_is_refused_through_memory_and_another_projects_digest_is_its_own(store):
    team = store.create_project("team")
    store.create_project("other")
    weekly = add(store, team.id, WEEKLY, source_ref="mail:1")
    add(store, team.id, ECHO)
    store.forget_note(weekly.id)
    with pytest.raises(ForgottenNote):
        store.add_note(team.id, WEEKLY)
    memory = Memory(store, compile_gate())
    with pytest.raises(Exception) as refused:
        memory.note("team", WEEKLY, source_ref="mail:2")
    assert refused.value.status == 409 and "forgotten" in str(refused.value)
    assert "rollback" not in str(refused.value)
    assert memory.note("other", WEEKLY)["status"] == "saved"
    assert store.list_audit(3)[0].action == "note.saved"  # no event for the refusal


def test_an_old_export_cannot_bring_a_forgotten_note_back_into_its_project(store):
    team = store.create_project("team")
    weekly = add(store, team.id, WEEKLY)
    archive = io.BytesIO()
    store.export(archive)
    store.forget_note(weekly.id)
    with pytest.raises(ValueError, match="already has a project named 'team'"):
        store.import_export(io.BytesIO(archive.getvalue()))
    assert store.get_note(weekly.id) is None


# Nothing left behind


def test_deleting_a_project_takes_it_out_of_every_reach_and_report(store, db):
    team, other = store.create_project("team"), store.create_project("other")
    group = store.create_set("work", (team.id, other.id))
    store.create_key("both", projects=(team.id, other.id))
    store.create_key("one", projects=(team.id,), access="read")
    store.create_key("by-set", projects=(group.id,))
    client = store.register_client("agent", ["http://127.0.0.1:1/cb"])
    code = store.create_code(client.id, "http://127.0.0.1:1/cb", "c" * 43, projects=(team.id,))
    assert store.redeem_code(code, client.id) is not None
    store.create_code(client.id, "http://127.0.0.1:1/cb", "d" * 43, projects=(team.id, other.id))
    store.add_source("feed", "langfuse", _langfuse(["team", "other"]))
    claim = store.claim_source("feed")
    declined = [
        {"link": "l1", "status": "refused", "codes": ["jwt"], "project": team.id},
        {"link": "l2", "status": "refused", "codes": ["jwt"], "project": other.id},
    ]
    store.finish_source_run(claim, "c1", {"saved": 0, "declined": declined})

    assert store.delete_project(team.id)
    assert team.id not in "\n".join(
        line for line in dump(db).splitlines() if not line.startswith('INSERT INTO "audit_events"')
    )
    keys = {k.label: k.projects for k in store.list_keys()}
    assert keys == {"both": (other.id,), "one": (), "by-set": (group.id,)}
    assert store.get_set("work").project_ids == (other.id,)
    (grant,) = store.list_grants()
    assert grant.projects == ()
    assert [d["link"] for d in store.get_source("feed").last_report["declined"]] == ["l2"]
    assert store.narrow(keys["one"]).list_projects() == []  # nothing, not everything
    # A source names its projects by name: the deleted one's name goes too (finding 4), so
    # a project made later under it gets nothing; one left with another project stays on.
    feed = store.get_source("feed")
    assert (feed.config["projects"], feed.enabled) == (["other"], True)


def _langfuse(projects: list[str]) -> dict:
    return {
        "base_url": "https://langfuse.example.test",
        "public_key_env": "LF_PUBLIC",
        "secret_key_env": "LF_SECRET",
        "score_name": "keep",
        "projects": projects,
    }


def test_the_digests_go_with_their_project_and_their_workspace_and_are_never_exported(store, db):
    team = store.create_project("team")
    store.forget_note(add(store, team.id, WEEKLY).id)
    archive = io.BytesIO()
    store.export(archive)
    assert b"forgotten" not in archive.getvalue()
    assert 'INSERT INTO "forgotten"' in dump(db)
    store.delete_project(team.id)
    assert 'INSERT INTO "forgotten"' not in dump(db)
    view = store.for_account(store.create_account("Other").id)
    project = view.create_project("team")
    view.forget_note(add(view, project.id, WEEKLY).id)
    assert store.delete_account(view.account_id)
    assert 'INSERT INTO "forgotten"' not in dump(db)


def test_a_forgotten_word_leaves_the_files_among_many_notes(store, db):
    project = store.create_project("team")
    words = [f"glossword{n:03d}x" for n in range(300)]
    notes = [
        add(store, project.id, f"# Topic {n % 30}\n\nThe {word} figure is {n}.\n")
        for n, word in enumerate(words)
    ]
    for n in (0, 7, 150, 299):
        store.forget_note(notes[n].id)
        assert not in_files(db, words[n]), words[n]
    assert in_files(db, words[1]) and len(store.search(words[1])) == 1
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_both_mark_a_compaction_due(store):
    team = store.create_project("team")
    store.forget_note(add(store, team.id, WEEKLY).id)
    first = store.compaction_state().deleted_at
    assert first is not None
    store.compact()
    assert store.compaction_state().deleted_at is None
    store.delete_project(team.id)
    assert store.compaction_state().deleted_at is not None


# Who may, asked before anything is offered


def test_may_erase_and_the_preview_write_nothing(store, db):
    team = store.create_project("team")
    weekly = add(store, team.id, WEEKLY)
    before = dump(db)
    assert store.may_erase(team.id) is True
    assert store.narrow([team.id]).may_erase(team.id) is False
    assert store.narrow(None, "read").may_erase(team.id) is False
    with pytest.raises(KeyError):
        store.narrow(["br_000000000000"]).may_erase(team.id)
    preview = store.forget_preview(weekly.id)
    assert (preview.rebuilt, preview.removed) == ((), ("weekly",))
    assert dump(db) == before


# What it costs


def counted(store: Store, work: Callable[[], object]) -> tuple[object, int, int]:
    """What `work` returned, the rows it changed (SQLite's total_changes) and the steps of
    SQLite's virtual machine it took, in thousands (a progress handler): work, not time."""
    con, steps = store._con, [0]

    def step() -> int:
        steps[0] += 1
        return 0  # go on

    before = con.total_changes
    con.set_progress_handler(step, 1_000)
    try:
        result = work()
    finally:
        con.set_progress_handler(None, 0)
    return result, con.total_changes - before, steps[0]


def test_both_cost_what_they_remove_on_a_project_of_2000_notes(tmp_path):
    """Neither rewrites the database: forgetting reads one project's page versions and writes
    one page per page that cited the note; deleting costs the project's own rows. Counted, not
    timed, on a project of 2,000 notes and one of 240, each in topics of 20 notes."""
    rows, steps = {}, {}
    for n in (240, 2_000):
        store = Store(tmp_path / f"{n}.db")
        try:
            big, small = store.create_project("big"), store.create_project("small")
            notes = [
                add(store, big.id, f"# Topic {k % (n // 20)}\n\nFact {k} holds for item {k * 7}.\n")
                for k in range(n)
            ]
            add(store, small.id, WEEKLY)
            forgotten, rows[n, "forget"], steps[n, "forget"] = counted(
                store, partial(store.forget_note, notes[0].id)
            )
            assert forgotten.rebuilt == ("topic-0",)
            deleted, rows[n, "delete"], steps[n, "delete"] = counted(
                store, partial(store.delete_project, big.id)
            )
            assert deleted is True
            assert [b.name for b in store.list_projects()] == ["small"]
        finally:
            store.close()
    # Forgetting writes the one page that cited the note: the same rows on either project.
    assert rows[2_000, "forget"] == rows[240, "forget"], rows
    # Deleting changes the project's own rows: about eight times as many for 8.3 times the notes.
    assert rows[2_000, "delete"] < 10 * rows[240, "delete"], rows
    # Neither works more than in step with the project (24: well away from 8 and from 64).
    assert steps[2_000, "forget"] < 24 * steps[240, "forget"], steps
    assert steps[2_000, "delete"] < 24 * steps[240, "delete"], steps


def test_the_default_workspace_can_delete_a_project_through_any_view(store):
    view = store.for_account(DEFAULT_ACCOUNT_ID)
    assert view.delete_project(view.create_project("x").id) is True
