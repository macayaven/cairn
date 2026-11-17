"""N1: the page a note compiles into (cairn.compiler.page_for), and two notes racing for it."""

from datetime import UTC, datetime

import pytest

from cairn.compiler import (
    SAVE_TRIES,
    SLUG_MAX,
    STALE_PAGE,
    compile_note,
    lost_race,
    page_for,
    slugify,
)
from cairn.store import Claim, Store
from cairn.topics import topic_key, topic_tag

Y2099 = datetime(2099, 3, 2, 9, 0, tzinfo=UTC)
LONG = "Incident review for the payments ledger reconciliation service in region"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db", clock=lambda: Y2099)
    yield s
    s.close()


@pytest.fixture
def project(store):
    return store.create_project("ops")


def made(store, project_id, slug, key, save=Store.save_page):
    """A page at `slug` that is topic `key`'s, as the compiler saves one."""
    claims = [Claim(id=f"cl_{len(slug)}", text="Quill retries.", span_ids=())]
    save(store, project_id, slug, key, "", claims, topic=key)


def address(store, project, title):
    return page_for(store, project.id, topic_key(title), title)


def test_a_free_slug_is_the_title_s_slug(store, project):
    for title in ("Retry policy", "Café menu", "C++", f"{LONG} east"):
        assert address(store, project, title) == slugify(title)


def test_a_topic_whose_slug_is_another_s_takes_it_with_its_tag(store, project):
    made(store, project.id, "c", "c++")
    assert address(store, project, "C#") == f"c-{topic_tag('c#')}"
    assert address(store, project, "c++") == "c"  # the key's own page, whatever it is called


def test_a_longer_tag_is_tried_when_a_shorter_one_s_slug_is_taken(store, project):
    made(store, project.id, "c", "c++")
    for size in (8, 12, 16):
        slug = f"c-{topic_tag('c#', size)}"
        assert address(store, project, "C#") == slug
        made(store, project.id, slug, f"occupant {size}")
    assert address(store, project, "C#") == f"c-{topic_tag('c#', 16)}-2"


def test_a_title_slugify_keeps_nothing_of_takes_untitled_with_its_tag(store, project):
    assert address(store, project, "日本語") == f"untitled-{topic_tag('日本語')}"
    assert address(store, project, "!!!") == "untitled"  # the empty key: plain, when free
    made(store, project.id, "untitled", "untitled")  # a note titled "Untitled"
    assert address(store, project, "!!!") == f"untitled-{topic_tag('')}"


def test_a_tagged_slug_is_cut_at_a_hyphen_to_stay_within_the_bound(store, project):
    made(store, project.id, slugify(f"{LONG} east"), topic_key(f"{LONG} east"))
    slug = address(store, project, f"{LONG} west")
    tag = topic_tag(topic_key(f"{LONG} west"))
    assert len(slug) <= SLUG_MAX and slug.endswith(f"-{tag}")
    base = slug.removesuffix(f"-{tag}")
    assert slugify(f"{LONG} west").startswith(base) and not base.endswith("-")
    assert slugify(f"{LONG} west")[len(base)] == "-"  # cut between two words


def test_the_addresses_are_looked_up_in_one_read(store, project, monkeypatch):
    made(store, project.id, "c", "c++")
    reads = []
    original = Store.taken_slugs
    monkeypatch.setattr(
        Store, "taken_slugs", lambda s, b, slugs: reads.append(slugs) or original(s, b, slugs)
    )
    address(store, project, "C#")
    assert len(reads) == 1 and reads[0][:2] == ["c", f"c-{topic_tag('c#')}"]


def test_a_save_that_lost_its_slug_to_another_topic_chooses_again(store, project, monkeypatch):
    """Another writer makes `c` C++'s between this compile's choice and its save: the save
    is refused as stale, and the next try files the note on a page of its own."""
    note, _ = store.add_note(project.id, "- C# keeps generics.\n", topic="C#")
    original = Store.save_page
    calls = []

    def save_page(s, project_id, slug, *args, **kwargs):
        if not calls:
            made(store, project_id, slug, "c++", original)  # the other topic gets there first
        calls.append(slug)
        return original(s, project_id, slug, *args, **kwargs)

    monkeypatch.setattr(Store, "save_page", save_page)
    result = compile_note(store, note)
    assert result.status == "saved" and result.slug == f"c-{topic_tag('c#')}"
    assert calls == ["c", result.slug]
    assert store.topic_page(project.id, "c#") == result.slug and store.waiting_notes() == []


def test_a_compile_that_keeps_losing_says_so(store, project, monkeypatch):
    first, _ = store.add_note(project.id, "- Quill keeps the relay.\n", topic="Relay")
    assert compile_note(store, first).status == "saved"
    note, _ = store.add_note(project.id, "- Quill keeps losing.\n", topic="Relay")
    original = Store.save_page

    def moved(s, project_id, slug, *args, **kwargs):
        latest = store.get_page(project_id, slug)  # someone saves it again first, every time
        original(store, project_id, slug, latest.title, latest.body, list(latest.claims))
        return original(s, project_id, slug, *args, **kwargs)

    monkeypatch.setattr(Store, "save_page", moved)
    result = compile_note(store, note)
    assert lost_race(result) and result.gate.violations[0].startswith(STALE_PAGE)
    assert str(SAVE_TRIES) in result.gate.violations[0]
    assert [n.note_id for n in store.waiting_notes()] == [note.id]
