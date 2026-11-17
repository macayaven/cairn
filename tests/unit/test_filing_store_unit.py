"""N1 in the store: a note's topic key, made once and kept; the page of each key; the notes
waiting for their page (cairn.store.memory, schema step 24)."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cairn.store import (
    TITLE_MAX,
    Claim,
    FiledElsewhere,
    StalePage,
    Store,
    WaitingNote,
    note_key,
)
from cairn.topics import topic_key

Y2099 = datetime(2099, 3, 2, 9, 0, tzinfo=UTC)
STEM = "word " * 30  # 150 characters
TEXT = "- Quill retries three times.\n"


def dump(db: Path) -> list[str]:
    con = sqlite3.connect(db)
    try:
        return list(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db, clock=lambda: Y2099)
    yield s
    s.close()


@pytest.fixture
def project(store):
    return store.create_project("ops")


def page(store, project_id, slug, title, **options):
    claims = [Claim(id=f"cl_{slug}", text="Quill retries.", span_ids=())]
    return store.save_page(project_id, slug, title, "", claims, **options)


# The key: made once, from what was given, and kept


def test_a_key_is_made_once_from_the_whole_title_and_kept_through_a_reopened_store(db):
    store = Store(db, clock=lambda: Y2099)
    project = store.create_project("ops")
    first, _ = store.add_note(project.id, TEXT, title=STEM + "alpha", source_ref="r")
    assert len(first.title) <= TITLE_MAX and first.topic_key == topic_key(STEM + "alpha")
    store.close()
    store = Store(db, clock=lambda: Y2099)  # what was kept, not what the stored title says
    try:
        (held,) = store.list_notes(project.id)
        assert held.topic_key == topic_key(STEM + "alpha") != topic_key(held.title)
        assert store.add_note(project.id, TEXT, title=STEM + "alpha", source_ref="r") == (
            held,
            False,
        )
        before = dump(db)
        with pytest.raises(FiledElsewhere):  # alike in its first 150 characters: another topic
            store.add_note(project.id, TEXT, title=STEM + "beta", source_ref="r")
        assert dump(db) == before
    finally:
        store.close()


def test_a_key_is_the_topic_s_else_the_title_s_else_the_text_s_first_line(store, project):
    with_topic, _ = store.add_note(project.id, "- one\n", title="Title", topic="Retry policy")
    with_title, _ = store.add_note(project.id, "- two\n", title="RETRY-policy.")
    from_text, _ = store.add_note(project.id, "# Retry policy!\n\n- three\n")
    assert with_topic.topic_key == with_title.topic_key == from_text.topic_key == "retry policy"
    assert note_key("# Retry policy!\n\n- three\n") == "retry policy"
    assert note_key("- x\n", title=STEM + "alpha") == topic_key(STEM + "alpha")


def test_a_note_sent_again_with_its_key_is_the_same_note_whatever_its_label(store, project, db):
    first, _ = store.add_note(project.id, TEXT, topic="Retry policy", source_ref="r", label="a")
    before = dump(db)
    again = store.add_note(project.id, TEXT, topic="retry-policy", source_ref="r", label="b")
    assert again == (first, False) and dump(db) == before


def test_a_note_sent_again_for_another_topic_is_refused_naming_the_note_it_is(store, project, db):
    first, _ = store.add_note(project.id, TEXT, topic="Retry policy", source_ref="r")
    before = dump(db)
    with pytest.raises(FiledElsewhere) as refused:
        store.add_note(project.id, TEXT, topic="Backoff", source_ref="r")
    assert refused.value.held == first and "Quill" not in str(refused.value)
    assert dump(db) == before
    assert store.add_note(project.id, TEXT, topic="Backoff", source_ref="other")[1] is True


# A page's key


def test_a_page_takes_the_key_it_is_saved_for_and_keeps_it(store, project):
    page(store, project.id, "c", "C++", topic="c++")
    assert store.topic_page(project.id, "c++") == "c"
    page(store, project.id, "c", "Another title", topic="c++")  # retitled: the key stays
    assert store.topic_page(project.id, "c++") == "c"
    assert store.topic_page(project.id, "another title") is None


def test_a_save_for_another_topic_than_its_page_s_is_stale_and_writes_nothing(store, project, db):
    page(store, project.id, "c", "C++", topic="c++")
    before = dump(db)
    with pytest.raises(StalePage):
        page(store, project.id, "c", "C#", topic="c#", expected_version=1)
    with pytest.raises(StalePage):  # the key has a page: another slug may not take it
        page(store, project.id, "c-2", "C++", topic="c++")
    assert dump(db) == before


def test_a_save_made_outside_the_compiler_keys_a_new_page_by_its_title_when_free(store, project):
    page(store, project.id, "retry-policy", "Retry policy")
    assert store.topic_page(project.id, "retry policy") == "retry-policy"
    page(store, project.id, "other", "RETRY POLICY")  # the key is held: saved, with no key
    assert store.topic_page(project.id, "retry policy") == "retry-policy"
    page(store, project.id, "retry-policy", "Anything")  # a page with a key takes any title
    assert {p.slug for p in store.list_pages(project.id)} == {"retry-policy", "other"}


def test_the_slugs_a_new_page_may_not_take_are_its_project_s_pages_and_keys(store, project):
    page(store, project.id, "c", "C++", topic="c++")
    page(store, project.id, "loose", "RETRY POLICY")
    other = store.create_project("other")
    page(store, other.id, "free", "Free")
    assert store.taken_slugs(project.id, ["c", "loose", "free", "c", "new"]) == {"c", "loose"}
    narrowed = store.narrow(projects=(other.id,))
    assert narrowed.taken_slugs(project.id, ["c"]) == set()  # out of reach: no page
    assert narrowed.topic_page(project.id, "c++") is None


# Waiting


def test_a_stored_note_waits_until_its_page_is_saved(store, project):
    note, _ = store.add_note(project.id, TEXT, topic="Retry policy")
    assert store.waiting_notes() == [
        WaitingNote(
            note_id=note.id,
            account_id=store.account_id,
            workspace="default",
            project_id=project.id,
            project="ops",
            stored_at=note.created_at,
            tries=0,
        )
    ]
    page(store, project.id, "retry-policy", "Retry policy", topic="retry policy", filed=note.id)
    assert store.waiting_notes() == []


def test_a_recorded_outcome_ends_the_wait_in_its_own_write(store, project):
    note, _ = store.add_note(project.id, TEXT, topic="Retry policy")
    other, _ = store.add_note(project.id, "- Another note.\n", topic="Retry policy")
    store.record_note(project.id, "unchanged", {"page": "retry-policy"}, filed=note.id)
    assert [n.note_id for n in store.waiting_notes()] == [other.id]
    assert store.list_audit()[0].action == "note.unchanged"


def test_a_try_with_no_outcome_is_counted_on_the_note(store, project):
    note, _ = store.add_note(project.id, TEXT, topic="Retry policy")
    assert [store.tried_waiting(note.id) for _ in range(2)] == [1, 2]
    assert store.waiting_notes()[0].tries == 2
    store.record_note(project.id, "rejected", {"violations": []}, filed=note.id)
    assert store.tried_waiting(note.id) == 0  # it waits no more
    with pytest.raises(KeyError):
        store.tried_waiting("cap_000000000000")
    with pytest.raises(PermissionError):
        store.narrow(access="read").tried_waiting(note.id)


def test_a_duplicate_or_a_refused_note_adds_no_wait(store, project):
    note, _ = store.add_note(project.id, TEXT, topic="Retry policy")
    page(store, project.id, "retry-policy", "Retry policy", topic="retry policy", filed=note.id)
    store.add_note(project.id, TEXT, topic="Retry policy")
    with pytest.raises(FiledElsewhere):
        store.add_note(project.id, TEXT, topic="Backoff")
    assert store.waiting_notes() == []


def test_the_waiting_list_names_no_note_s_text_or_title(store, project):
    store.add_note(project.id, "- Zyqvorth keeps it.\n", title="Secret plans")
    (note,) = store.waiting_notes()
    assert "Zyqvorth" not in repr(note) and "Secret" not in repr(note)


# Erasure leaves no topic or wait behind


def rows(db: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_forgetting_a_note_ends_its_wait_and_a_removed_page_s_key(store, project, db):
    waiting, _ = store.add_note(project.id, TEXT, topic="Retry policy")
    filed, _ = store.add_note(project.id, "- Backoff doubles.\n", topic="Backoff")
    (span,) = store.note_spans(filed.id)
    claims = [Claim(id="cl_backoff", text="Backoff doubles.", span_ids=(span.id,))]
    store.save_page(project.id, "backoff", "Backoff", "", claims, topic="backoff", filed=filed.id)
    store.forget_note(waiting.id)
    store.forget_note(filed.id)  # its page had nothing else: removed
    assert rows(db, "SELECT * FROM waiting") == rows(db, "SELECT * FROM page_keys") == []


def test_deleting_a_project_or_a_workspace_leaves_no_topic_or_wait(store, db):
    for account in (store, store.for_account(store.create_account("Other").id)):
        project = account.create_project("ops")
        account.add_note(project.id, TEXT, topic="Retry policy")
        page(account, project.id, "retry-policy", "Retry policy", topic="retry policy")
    assert len(rows(db, "SELECT * FROM waiting")) == len(rows(db, "SELECT * FROM page_keys")) == 2
    store.delete_project(store.list_projects()[0].id)
    store.delete_account(store.list_accounts()[1].id)
    assert rows(db, "SELECT * FROM waiting") == rows(db, "SELECT * FROM page_keys") == []
