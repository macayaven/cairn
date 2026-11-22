"""Unit tests for the disk bounds (H6c): passages capped by the storage cap, the page history's
byte budget, the claims a page holds, and the words each is said in. The inner loop under
tests/acceptance/test_h6c_disk.py."""

import io
import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import cairn.store.memory
from cairn.compiler import claim_id, compile_note, render_body
from cairn.gate import lint_gate
from cairn.memory import Forbidden, Memory, OverQuota, PageFull
from cairn.plans import Plan, Plans, limit_sentence, limit_sentences
from cairn.server import create_app
from cairn.store import (
    BYTES_PER_PASSAGE,
    HISTORY_FACTOR,
    PAGE_CLAIMS_MAX,
    Claim,
    Limits,
    OverLimit,
    PageTooLong,
    Store,
    note_key,
)
from cairn.store.usage import passages_of

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


def items(prefix: str, n: int) -> str:
    return f"# {prefix}\n\n" + "".join(f"- {prefix}{i}\n" for i in range(n))


# Passages


def test_a_passage_is_a_block_that_becomes_a_claim():
    text = "---\ntitle: T\n---\n# T\n\nOne.\n\n- a\n- b\n\n> q\n\n```\nx\n```\n\n| a |\n|---|\n"
    assert passages_of(text) == 6  # a paragraph, two items, a quote, a code block, a table


def test_the_look_before_screening_refuses_past_the_passages(store, monkeypatch):
    project = store.create_project("ops")
    limited = store.limited(Limits(storage_bytes=10 * BYTES_PER_PASSAGE))
    limited.add_note(project.id, items("a", 10))
    screened = []
    monkeypatch.setattr(Store, "_screen", lambda self, fields: screened.append(1) or [])
    with pytest.raises(OverLimit) as over:
        limited.add_note(project.id, items("b", 1))
    assert over.value.limit == "passages" and screened == []  # refused before screening
    limited.add_note(project.id, items("a", 10))  # a note it holds is "unchanged", not over


def test_an_import_past_the_passages_imports_nothing(store, tmp_path):
    source = Store(tmp_path / "source.db")
    project = source.create_project("ops")
    source.add_note(project.id, items("a", 30))
    archive = io.BytesIO()
    source.export(archive)
    source.close()
    target = store.for_account(store.create_account("Brook").id)
    tight = target.limited(Limits(storage_bytes=29 * BYTES_PER_PASSAGE))
    with pytest.raises(OverLimit) as over:
        tight.import_export(io.BytesIO(archive.getvalue()))
    assert (over.value.limit, over.value.used, over.value.cap) == ("passages", 0, 29)
    assert target.list_projects() == [] and target.passages() == 0
    target.limited(Limits(storage_bytes=30 * BYTES_PER_PASSAGE)).import_export(archive)
    assert target.passages() == 30


def test_the_passages_are_said_in_words():
    assert limit_sentence("passages", 1_500) == "1,500 passages of notes"
    assert limit_sentence("passages", 1) == "1 passage of notes"
    assert limit_sentence("passages", None) == "any number of passages"
    said = limit_sentences(Plan("Small", storage_bytes=60_000))
    assert said["passages"] == "1,500 passages of notes"
    assert list(said).index("passages") == list(said).index("storage_bytes") + 1
    assert limit_sentences(Plan("Free"))["passages"] == "any number of passages"


def test_a_refusal_at_the_passages_says_so_with_or_without_numbers():
    over = OverLimit("passages", 1_490, 1_500, None, "small", True)
    whole = OverQuota(over, "small", "Small")
    assert "1490 of 1500 passages" in str(whole) and "Small" in str(whole)
    assert whole.fields()["limit"] == "passages" and whole.fields()["resets_at"] is None
    told = OverQuota(over, "small", "Small", whole=False)
    assert "passages" in str(told) and not any(ch.isdigit() for ch in str(told))
    assert told.fields() == {"limit": "passages"}


def test_the_usage_page_shows_the_passages(tmp_path):
    store = Store(tmp_path / "web.db")
    try:
        key = store.create_key("laptop")
        plans = Plans({"small": Plan("Small", storage_bytes=60_000)}, default="small")
        app = create_app(store, public_url=BASE, plans=plans)
        project = store.create_project("ops")
        store.add_note(project.id, items("a", 12))
        with TestClient(app, base_url=BASE) as client:
            usage = client.get("/api/usage", headers={"x-api-key": key}).json()
            assert usage["passages"] == {"used": 12, "cap": 1_500}
            client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            page = client.get("/usage").text
            assert "Passages in notes" in page and "12 of 1,500" in page
            assert "1,500 passages of notes" in page  # what the plan allows, in its words
    finally:
        store.close()


# History


def ticking(tmp_path) -> Store:
    """A store whose clock moves a second at each read: versions in the order they are saved."""
    moments = itertools.count()
    start = datetime(2026, 11, 1, tzinfo=UTC)
    return Store(tmp_path / "ticks.db", clock=lambda: start + timedelta(seconds=next(moments)))


def save(view, project_id: str, slug: str, size: int) -> None:
    """A version of `size` bytes as the budget counts them: a body as the compiler renders a
    page without claims ("# <title>\n") and its claims, "[]"."""
    title = "T" * (size - 5)
    view.save_page(project_id, slug, title, f"# {title}\n", [])


def save_cited(view, project_id: str, slug: str, size: int) -> None:
    """A version of `size` bytes as the budget counts them, as cairn writes one for a page an
    export carries: one claim, the one passage of a short note in the project (an import
    refuses a version without claims)."""
    note, _ = view.add_note(project_id, "Retries.\n")
    (span,) = view.note_spans(note.id)
    claim = Claim(id=claim_id(span.id), text=span.text, span_ids=(span.id,))

    def stored(title: str) -> int:  # the body and claims as the budget counts them
        body = render_body(title, [claim], {span.id: span})
        return len(body.encode()) + len(json.dumps([claim.model_dump()]).encode())

    title = "T" * (size - stored("T") + 1)  # one byte a letter of the title
    held = view.history_bytes()
    view.save_page(project_id, slug, title, render_body(title, [claim], {span.id: span}), [claim])
    assert view.history_bytes() - held == size


def kept(view) -> dict[str, list[int]]:
    return {
        f"{project.name}/{page.slug}": view.page_versions(project.id, page.slug)
        for project in view.list_projects()
        for page in view.list_pages(project.id)
    }


def test_the_history_budget_is_four_times_the_storage_cap():
    assert HISTORY_FACTOR == 4


def test_a_save_past_the_budget_drops_the_workspaces_oldest_versions(tmp_path):
    store = ticking(tmp_path)
    try:
        ops, team = store.create_project("ops").id, store.create_project("team").id
        view = store.limited(Limits(storage_bytes=100))  # a budget of 400 bytes
        save(view, ops, "p", 150)
        save(view, team, "q", 150)
        assert store.history_bytes() == 300  # it fits: nothing goes
        save(view, team, "q", 150)  # 450: the oldest version in the workspace goes
        assert kept(store) == {"ops/p": [1], "team/q": [2]}  # p's v1 is its latest: it stays
        assert store.history_bytes() == 300
        save(view, ops, "p", 150)
        assert kept(store) == {"ops/p": [2], "team/q": [2]}
        save(view, ops, "r", 300)  # 600, and every version left is a page's latest
        assert kept(store) == {"ops/p": [2], "ops/r": [1], "team/q": [2]}
        assert store.history_bytes() == 600
        save(view, ops, "r", 10)  # r's v1 is no longer its latest: it goes first
        assert kept(store) == {"ops/p": [2], "ops/r": [2], "team/q": [2]}
        save(view, ops, "r", 10)
        assert kept(store)["ops/r"] == [2, 3] and store.history_bytes() == 320
    finally:
        store.close()


def test_without_a_storage_cap_every_version_stays(tmp_path):
    store = ticking(tmp_path)
    try:
        ops = store.create_project("ops").id
        for _ in range(5):
            save(store.limited(Limits(notes_per_month=5)), ops, "p", 1_000)
        assert kept(store) == {"ops/p": [1, 2, 3, 4, 5]}
    finally:
        store.close()


def test_an_import_past_the_budget_keeps_what_fits(tmp_path):
    source = ticking(tmp_path)
    try:
        ops = source.create_project("ops").id
        for _ in range(5):
            save_cited(source, ops, "p", 200)  # a cited version takes over 100
        archive = io.BytesIO()
        source.export(archive)
    finally:
        source.close()
    store = Store(tmp_path / "target.db")
    try:
        target = store.for_account(store.create_account("Brook").id)
        target.limited(Limits(storage_bytes=150)).import_export(io.BytesIO(archive.getvalue()))
        assert kept(target) == {"ops/p": [3, 4, 5]}  # 600 of a budget of 600: the oldest went
        assert target.history_bytes() == 600
    finally:
        store.close()


@pytest.mark.parametrize(
    "storage, versions, held",
    [
        (150, [198, 199, 200], 600),  # 600 of a budget of 600
        (40, [200], 200),  # past a budget of 160: the latest is never dropped
    ],
)
def test_an_archive_of_200_versions_of_a_page_imports_with_what_the_budget_keeps(
    tmp_path, storage, versions, held
):
    """An archive's versions per page have no bound of their own: under a storage cap the
    history budget keeps what fits, and the latest always."""
    source = ticking(tmp_path)
    try:
        ops = source.create_project("ops").id
        for _ in range(200):
            save_cited(source, ops, "p", 200)  # one note, cited by every version
        assert len(source.list_notes(ops)) == 1
        archive = io.BytesIO()
        source.export(archive)
    finally:
        source.close()
    store = Store(tmp_path / "target.db")
    try:
        target = store.for_account(store.create_account("Brook").id)
        target.limited(Limits(storage_bytes=storage)).import_export(io.BytesIO(archive.getvalue()))
        assert kept(target) == {"ops/p": versions}
        assert target.history_bytes() == held
    finally:
        store.close()


def test_a_version_the_history_budget_dropped_is_404_saying_why(tmp_path):
    store = ticking(tmp_path)
    try:
        key = store.create_key("laptop")
        ops = store.create_project("ops").id
        plans = Plans({"small": Plan("Small", storage_bytes=100)}, default="small")
        app = create_app(store, public_url=BASE, plans=plans)
        view = plans.limit(store, plans.in_force(store, store.account_id)[1])
        for _ in range(3):
            save(view, ops, "p", 150)  # 450 of a budget of 400: version 1 goes
        assert store.page_versions(ops, "p") == [2, 3]
        with TestClient(app, base_url=BASE) as client:
            gone = client.get("/api/projects/ops/pages/p?version=1", headers={"x-api-key": key})
        said = gone.json()["error"]
        assert gone.status_code == 404 and "no longer kept" in said
        assert "4 times its storage" in said
    finally:
        store.close()


# The claims a page holds


def lines(prefix: str, n: int, topic: str = "Ledger") -> str:
    return f"# {topic}\n\n" + "".join(f"Line {prefix}{i} holds.\n\n" for i in range(n))


def test_the_store_saves_no_page_past_its_claims(store):
    project = store.create_project("ops")
    claims = [Claim(id=f"c{i}", text=f"t{i}", span_ids=()) for i in range(PAGE_CLAIMS_MAX + 1)]
    with pytest.raises(PageTooLong) as long:
        store.save_page(project.id, "ledger", "Ledger", "# Ledger\n", claims)
    assert isinstance(long.value, ValueError) and "ledger" in str(long.value)
    assert store.get_page(project.id, "ledger") is None and store.history_bytes() == 0
    store.save_page(project.id, "ledger", "Ledger", "# Ledger\n", claims[:-1])  # exactly the bound


def test_a_note_past_its_pages_claims_is_refused_before_it_is_stored(store):
    project = store.create_project("ops")
    memory = Memory(store, lint_gate)
    assert memory.note("ops", lines("a", PAGE_CLAIMS_MAX - 1))["status"] == "saved"
    assert memory.note("ops", lines("b", 1))["status"] == "saved"  # exactly the bound
    held = len(store.list_notes(project.id))
    with pytest.raises(PageFull) as full:
        memory.note("ops", lines("c", 1) + "Kingfisher.\n")
    said = str(full.value)
    assert full.value.status == 409 and "ledger" in said and "topic" in said
    assert "Kingfisher" not in said and "Line" not in said  # nothing of the note
    assert len(store.list_notes(project.id)) == held  # nothing stored
    assert memory.note("ops", lines("b", 1))["status"] == "unchanged"  # a note it holds
    other = memory.note("ops", lines("c", 1), topic="Ledger, second book")
    assert other["status"] == "saved" and other["page"] == "ledger-second-book"


def test_a_note_longer_than_any_page_says_to_split_it(store):
    store.create_project("ops")
    with pytest.raises(PageFull) as full:
        Memory(store, lint_gate).note("ops", lines("a", PAGE_CLAIMS_MAX + 1))
    assert "split" in str(full.value) and full.value.status == 409


def test_a_page_filled_meanwhile_keeps_the_note_and_answers_409(store, monkeypatch):
    project = store.create_project("ops")
    memory = Memory(store, lint_gate)
    memory.note("ops", lines("a", PAGE_CLAIMS_MAX))
    monkeypatch.setattr(Memory, "_room_on_page", lambda self, *args: None)  # a race lost
    with pytest.raises(PageFull) as full:
        memory.note("ops", lines("b", 1))
    assert full.value.status == 409 and "kept" in str(full.value)
    assert len(store.list_notes(project.id)) == 2
    assert len(store.get_page(project.id, "ledger").claims) == PAGE_CLAIMS_MAX


@pytest.mark.parametrize(
    "text, title, topic",
    [
        ("# Relay\n\nOne.\n", None, None),
        ("---\ntitle: From the matter\n---\n# Heading\n\nOne.\n", None, None),
        ("# Heading\n\nOne.\n", "Given title", None),
        ("# Heading\n\nOne.\n", "Given title", "The topic, it decides"),
        ("Just a line, no heading.\n\nTwo.\n", None, None),
        ("```\n# not a heading\n```\n\n# Real one\n\nOne.\n", None, None),
        ("# " + "Long " * 30 + "\n\nOne.\n", None, None),
        ("# Heading\n\nOne.\n", "   ", ""),
    ],
)
def test_the_page_a_note_is_checked_against_is_the_page_it_compiles_into(store, text, title, topic):
    """N1: Memory._room_on_page reads the page of the note's key (Store.topic_page of
    note_key), which is the page the note compiles into."""
    project = store.create_project("ops")
    note, _ = store.add_note(project.id, text, title=title, topic=topic)
    slug = compile_note(store, note).slug
    assert store.topic_page(project.id, note_key(text, title, topic)) == slug


def test_an_import_with_a_page_past_its_claims_imports_nothing(store, tmp_path, monkeypatch):
    source = Store(tmp_path / "source.db")
    try:
        project = source.create_project("ops")
        note, _ = source.add_note(project.id, lines("a", PAGE_CLAIMS_MAX + 1))
        monkeypatch.setattr(cairn.store.memory, "PAGE_CLAIMS_MAX", PAGE_CLAIMS_MAX + 1)
        assert compile_note(source, note).status == "saved"  # as an older build could
        monkeypatch.undo()
        archive = io.BytesIO()
        source.export(archive)
    finally:
        source.close()
    target = store.for_account(store.create_account("Brook").id)
    with pytest.raises(ValueError, match="ledger") as refused:
        target.import_export(archive)
    assert f"{PAGE_CLAIMS_MAX:,}" in str(refused.value)
    assert target.list_projects() == []


def test_the_composer_answers_a_full_page_with_its_form_again(tmp_path, monkeypatch):
    import cairn.memory

    monkeypatch.setattr(cairn.memory, "PAGE_CLAIMS_MAX", 2)
    store = Store(tmp_path / "web.db")
    try:
        key = store.create_key("laptop")
        store.create_project("ops")
        app = create_app(store, public_url=BASE)
        with TestClient(app, base_url=BASE) as client:
            client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            first = {"text": "# Ledger\n\nOne.\n\nTwo.\n"}
            assert client.post("/b/ops/notes", data=first, headers=SAME_ORIGIN).status_code == 200
            typed = {"text": "# Ledger\n\nThree, kingfisher.\n"}
            full = client.post("/b/ops/notes", data=typed, headers=SAME_ORIGIN)
        assert full.status_code == 409 and "another topic" in full.text
        assert "kingfisher" in full.text  # what was typed stays in the form
    finally:
        store.close()


# Who sees the totals


def person(store, name: str):
    signed = store.sign_in(
        "sso",
        f"sub-{name}",
        f"{name.lower()}@acme.example",
        name,
        issuer="https://id.acme.example/pool-1",
        join="default",
    )
    return store.for_account(signed.account_id, signed.user.id)


def test_the_totals_are_for_whoever_reaches_every_project_as_an_owner(store):
    ada, brook, cy = (person(store, name) for name in ("Ada", "Brook", "Cy"))
    ada.set_role(brook.user_id, "admin")
    assert (
        Memory(store, lint_gate).sees_totals() and Memory(ada, lint_gate).sees_totals()
    )  # no person; an owner
    assert not Memory(ada.narrow(projects=()), lint_gate).sees_totals()  # an owner's narrowed key
    for view in (brook, cy):  # an admin, a member
        memory = Memory(view, lint_gate)
        assert not memory.sees_totals()
        with pytest.raises(Forbidden):
            memory.usage()
        assert view.stored_bytes() == 0 and view.usage().notes == 0  # the store still answers


def test_a_cap_tells_an_admin_or_member_the_limit_and_nothing_else(store):
    ada, brook = person(store, "Ada"), person(store, "Brook")
    ada.set_role(brook.user_id, "admin")
    over = OverLimit("passages", 1_490, 1_500, None, "small", True)
    plans = Plans({"small": Plan("Small", storage_bytes=60_000)}, default="small")
    told = Memory(brook, lint_gate, plans=plans, plan=("small", plans.table["small"])).quota_error(
        over
    )
    assert told.fields() == {"limit": "passages"} and "Small" not in str(told)
    whole = Memory(ada, lint_gate, plans=plans, plan=("small", plans.table["small"])).quota_error(
        over
    )
    assert whole.fields()["cap"] == 1_500 and "Small" in str(whole)
