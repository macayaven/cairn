"""Forget a separator on a valid page whose stored and rendered orders differ."""

from contextlib import closing
from itertools import groupby

import pytest
from markdown_it import MarkdownIt

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store
from support.compiler.assembled_fence import imported

SECTION = "## Samples\n\n"
INLINE = "The sundial uses `local time`."
BLOCK = "~~~text\nNorth marker.\n~~~"
TAIL = "  The ledger has ~ marks and `short` labels, even ~~~ in prose."


@pytest.fixture
def desk():
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        yield Memory(store, lint_gate)


def file_note(desk, text):
    answer = desk.note("alpha", text, title="Ops", topic="Ops")
    assert (answer["status"], answer["page"]) == ("saved", "ops"), answer
    return answer["note_id"]


def snippets(group, count=40):
    return [f"    ~~~\n    {group} sample {n}" for n in range(count)]


def page(store):
    return store.get_page(store.get_project("alpha").id, "ops")


def check_forget(desk, tmp_path, separator, expected, independent, query):
    assert desk.forget("alpha", separator)["rebuilt"] == ["ops"]
    with closing(imported(desk.store, tmp_path)) as restored:
        assert page(restored).claims == page(desk.store).claims == expected
        for store in (desk.store, restored):
            assert store.get_note(separator) is None
            assert expected[-1].text in {hit.text for hit in store.search(query)}
            assert store.get_note(independent) is not None


def test_last_filed_inline_code_on_the_full_separator_page_survives(desk, tmp_path):
    blocks = [f"    ~~~\n    sample {n}" for n in range(1997)]
    file_note(desk, SECTION + "- item")
    separator = file_note(desk, SECTION + "Between.")
    file_note(desk, SECTION + "\n\n".join(blocks))
    independent = file_note(desk, INLINE)
    before = page(desk.store)
    assert [c.text for c in before.claims] == ["- item", "Between.", *blocks, INLINE]
    assert before.body.index(INLINE) < before.body.index(SECTION.strip())
    expected = (before.claims[0], *before.claims[2:18], before.claims[-1])
    check_forget(desk, tmp_path, separator, expected, independent, "sundial")


@pytest.mark.parametrize(
    ("where", "count"),
    [("before-section", 40), ("after-openers", 40), ("after-openers", 16)],
)
def test_innocent_claims_survive_in_search_and_after_restore(desk, tmp_path, where, count):
    blocks = snippets("section", count)
    file_note(desk, SECTION + "- item")
    separator = file_note(desk, SECTION + "Between.")
    file_note(desk, SECTION + "\n\n".join([*blocks, "  End of samples."]))
    if where == "before-section":
        # Filed last. One safe claim even contains a real, closed fence.
        independent = file_note(desk, BLOCK + "\n\n" + INLINE)
        protected, query = INLINE, "sundial"
    else:
        independent = file_note(desk, SECTION + TAIL)
        protected, query = TAIL, "ledger"
    before = page(desk.store)
    assert before.claims[-1].text == protected
    assert (before.body.index(protected) < before.body.index(blocks[0])) == (
        where == "before-section"
    )
    removed = {"Between.", *blocks[16:]}
    expected = tuple(c for c in before.claims if c.text not in removed)
    assert protected in {c.text for c in expected}
    check_forget(desk, tmp_path, separator, expected, independent, query)


def test_two_groups_in_reverse_order_share_the_eighteen_render_budget(desk, tmp_path, monkeypatch):
    top, section = snippets("top"), snippets("section")
    # The inline code keeps the final body visible to the parser even if every sample goes.
    file_note(desk, SECTION + "- section item with a `label`")
    file_note(desk, "- top item")
    separator = file_note(desk, "Top separator.\n\n" + SECTION + "Section separator.")
    file_note(desk, SECTION + "\n\n".join([*section, "  End of section."]))
    file_note(desk, "\n\n".join([*top, "  End of top."]))
    before = page(desk.store)
    stored = [c.text for c in before.claims]
    assert stored.index(section[0]) < stored.index(top[0])
    assert before.body.index(top[0]) < before.body.index(section[0])
    removed = {"Top separator.", "Section separator.", *top[16:], *section}
    expected = tuple(c for c in before.claims if c.text not in removed)
    # Observe the inputs to CommonMark. Inserting a closer leaves the set of samples alone.
    # Removing another claim starts a new rendering. Standalone passage parses cost no
    # whole-page rendering. Eighteen renderings can each parse a body at most 17 times.
    reads = []
    parse = MarkdownIt.parse

    def observed(parser, source, *args, **kwargs):
        if source.startswith("# Ops\n\n"):
            reads.append(tuple(line for line in source.splitlines() if " sample " in line))
        return parse(parser, source, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(MarkdownIt, "parse", observed)
        assert desk.forget("alpha", separator)["rebuilt"] == ["ops"]
    renders = sum(1 for _ in groupby(reads))
    assert renders <= 18
    assert len(reads) <= 18 * 17
    with closing(imported(desk.store, tmp_path)) as restored:
        assert page(desk.store).claims == page(restored).claims == expected
