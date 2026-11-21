"""A page that a forget removes leaves its name free, and its version numbers with it
(fourth report, "History after a forget").

Nothing here is new behaviour: tests/unit/test_forget_unit.py holds that a page removed and
made again is at version 1. This runs the sentence offered for the privacy guide: a later
note can make a page of the same name, which starts again at version 1, so an address kept
for a version of the page that went shows the new page's version of that number, or
nothing. Made-up notes."""

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store


def test_an_address_of_a_removed_pages_version_shows_the_page_made_later_under_its_name(
    tmp_path,
):
    store = Store(tmp_path / "cairn.db")
    try:
        project, memory = store.create_project("alpha"), Memory(store, lint_gate)
        first = memory.note("alpha", "Ada keeps port 80 open.", title="Net", topic="Net")
        second = memory.note("alpha", "Brook owns the rollback.", title="Net", topic="Net")
        assert (first["page"], second["page"]) == ("net", "net")
        assert store.page_versions(project.id, "net") == [1, 2]
        for answer in (first, second):
            store.forget_note(answer["note_id"])
        assert store.page_versions(project.id, "net") == []  # the page went with its notes
        assert store.get_page(project.id, "net", 2) is None
        later = memory.note("alpha", "Quill keeps the log.", title="Net", topic="Net")
        assert later["page"] == "net" and store.page_versions(project.id, "net") == [1]
        page = store.get_page(project.id, "net", 1)  # the address of the old first version
        assert [claim.text for claim in page.claims] == ["Quill keeps the log."]
        assert "Ada" not in page.body
        assert store.get_page(project.id, "net", 2) is None  # and of the old second: nothing
    finally:
        store.close()
