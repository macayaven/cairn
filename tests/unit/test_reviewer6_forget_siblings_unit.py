"""Compare a public forget with removing just the claim the rendered body names each time."""

import io
from contextlib import closing
from itertools import groupby

import pytest
from markdown_it import MarkdownIt

from cairn import compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import CLOSERS_MAX
from cairn.store import Store

FIRST = "## First\n\n"
STEPS = "## Steps\n\n"
INLINE = "The sundial uses `local time`."
END_FIRST = "  End of first."
TAIL = "        End of samples."
SEPARATOR = "Between."
CLOSED = "    ```text\n    Closed compass example.\n    ```"
PREFIX = [f"    ~~~\n    first {n}" for n in range(16)]
SEARCH = "sundial item first parent sample samples between compass"


def oracle(title, claims, spans):
    remaining = list(claims)
    dropped = []
    while True:
        _, place = compiler.left_to_run(title, remaining, spans)
        if place is None:
            return [claim.text for claim in remaining], dropped
        assert 1 <= place <= len(remaining)
        dropped.append(remaining.pop(place - 1).text)


def restore(source, target):
    archive = io.BytesIO()
    source.export(archive)
    archive.seek(0)
    target.import_export(archive)


def forget_renders(desk, note_id):
    # Round five groups consecutive whole-body parses by the examples still present.
    # Inserting a closer within one render leaves each example's identifying line alone.
    reads = []
    parse, render = MarkdownIt.parse, compiler.left_to_run

    def remember(body):
        reads.append(tuple(s for s in body.splitlines() if " sample " in s or " first " in s))

    def observed(reader, source, *args, **kwargs):
        if source.startswith("# Ops\n\n"):
            remember(source)
        return parse(reader, source, *args, **kwargs)

    def include_plain_body(title, claims, spans):
        before = len(reads)
        body, place = render(title, claims, spans)
        if len(reads) == before:
            # A final body with all fence marks gone is still rendered, without a parser call.
            remember(body)
        return body, place

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(MarkdownIt, "parse", observed)
        patch.setattr(compiler, "left_to_run", include_plain_body)
        assert desk.forget("alpha", note_id)["rebuilt"] == ["ops"]
    return sum(1 for _ in groupby(reads))


def exercise(notes, forget_at):
    with closing(Store(":memory:")) as store:
        project = store.create_project("alpha")
        desk = Memory(store, lint_gate)
        ids = []
        for note in notes:
            answer = desk.note("alpha", note, title="Ops", topic="Ops")
            assert (answer["status"], answer["page"]) == ("saved", "ops"), answer
            ids.append(answer["note_id"])
        before = store.get_page(project.id, "ops")
        with closing(Store(":memory:")) as original:
            restore(store, original)
            assert (
                original.get_page(original.get_project("alpha").id, "ops").claims == before.claims
            )
            for source in (store, original):
                assert [h.text for h in source.search("sundial")] == [
                    c.text for c in before.claims if c.text == INLINE
                ]

        forgotten = ids[forget_at]
        spans = store.get_spans(i for claim in before.claims for i in claim.span_ids)
        kept = [
            c for c in before.claims if all(spans[i].source_id != forgotten for i in c.span_ids)
        ]
        assert [c.text for c in before.claims if c not in kept] == [SEPARATOR]
        expected, dropped = oracle(before.title, kept, spans)

        renders = forget_renders(desk, forgotten)
        with closing(Store(":memory:")) as restored:
            restore(store, restored)
            pages = []
            for view in (desk, Memory(restored, lint_gate)):
                page = view.page("alpha", "ops")
                assert not page.get("body_not_shown")
                texts = [c["text"] for c in page["claims"]]
                in_body = [c.text for c in before.claims if f"\n{c.text}\n" in page["body"]]
                assert in_body == texts
                hits = view.recall(SEARCH, project="alpha", limit=50)["hits"]
                assert sorted(hit["text"] for hit in hits) == sorted(texts)
                assert view.store.get_note(forgotten) is None
                assert all(view.store.get_note(i) is not None for i in ids if i != forgotten)
                pages.append(texts)
            assert pages[0] == pages[1]
        return expected, dropped, pages[0], renders


def sibling_page(count, *, closed_at=None, prefix=PREFIX, tail=TAIL):
    siblings = [f"    -   item {n}\n        ~~~\n        sample {n}" for n in range(count)]
    examples = siblings.copy()
    if closed_at is not None:
        examples.insert(closed_at, CLOSED)
    notes = [
        INLINE,
        FIRST + "- item",
        FIRST + "\n\n".join([*prefix, END_FIRST]),
        STEPS + "- parent",
        STEPS + "\n\n".join(examples),
        STEPS + SEPARATOR,
        STEPS + tail,
    ]
    return siblings, exercise(notes, forget_at=5)


@pytest.fixture(scope="module", params=[36, 120], ids=["36-siblings", "120-siblings"])
def seven_notes(request):
    return sibling_page(request.param)


def test_seven_note_page_keeps_every_claim_except_the_separator_and_siblings(seven_notes):
    siblings, (expected, dropped, actual, renders) = seven_notes
    assert dropped == siblings[::-1]
    assert expected == [INLINE, "- item", *PREFIX, END_FIRST, "- parent", TAIL]
    assert actual == expected, f"{renders} renders"


def test_render_bound_is_independent_of_sibling_count(seven_notes):
    _, (_, _, _, renders) = seven_notes
    assert renders <= 2 * CLOSERS_MAX + 2


def test_with_one_closer_available_all_siblings_and_the_tail_stay():
    prefix = PREFIX[:-1]
    siblings, (expected, dropped, actual, _) = sibling_page(24, prefix=prefix)
    assert dropped == []
    assert expected == [INLINE, "- item", *prefix, END_FIRST, "- parent", *siblings, TAIL]
    assert actual == expected


def test_indented_tail_with_fence_marks_in_prose_stays():
    tail = "        End of samples with `short` labels, even ~~~ in prose."
    siblings, (expected, dropped, actual, _) = sibling_page(36, tail=tail)
    assert dropped == siblings[::-1]
    assert expected == [INLINE, "- item", *PREFIX, END_FIRST, "- parent", tail]
    assert actual == expected


@pytest.mark.parametrize("at", [0, 3], ids=["before-siblings", "between-siblings"])
def test_indented_example_with_its_own_closer_stays(at):
    siblings, (expected, dropped, actual, _) = sibling_page(36, closed_at=at)
    assert dropped == siblings[at:][::-1]
    assert expected == [
        INLINE,
        "- item",
        *PREFIX,
        END_FIRST,
        "- parent",
        *siblings[:at],
        CLOSED,
        TAIL,
    ]
    assert actual == expected


@pytest.mark.parametrize("extra", [16, 17], ids=["at-ceiling", "past-ceiling"])
def test_only_a_later_indented_fence_may_leave_past_the_ceiling(extra):
    examples = [f"    ~~~\n    first {n}" for n in range(16 + extra)]
    notes = ["- item", SEPARATOR, "\n\n".join([*examples, END_FIRST, CLOSED, TAIL])]
    expected, dropped, actual, _ = exercise(notes, forget_at=1)
    assert dropped == examples[16:]
    assert expected == ["- item", *examples[:16], END_FIRST, CLOSED, TAIL]
    allowed = [expected]
    if extra > CLOSERS_MAX:
        allowed.append([text for text in expected if text != CLOSED])
    assert actual in allowed
