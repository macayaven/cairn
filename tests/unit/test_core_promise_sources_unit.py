"""A source's item with no body is skipped, and counted as a refused item is (review 7.6):
the runner's own `malformed`, by the reason "no body text", the cursor moved past it and
nothing stored. The fake kind of the runner's unit tests stands in for a source."""

from test_sources_runner_unit import Fake, draft, fake_kind, run, store  # noqa: F401

from cairn.sources import Pulled


def test_an_item_of_a_title_only_is_skipped_as_malformed_and_nothing_is_stored(store):  # noqa: F811
    title_only = draft(text="# Quill outage\n", ref="https://example.test/9")
    Fake.answers = [Pulled(drafts=[title_only, draft()], cursor="c1")]
    report = run(store)
    assert (report.saved, report.skipped, report.malformed) == (1, 1, 1)
    assert report.reasons == {"no body text": 1}
    assert store.get_source("feed").cursor == "c1"
    (note,) = store.list_notes(store.get_project("api").id)
    assert note.source_ref == "https://example.test/1"
