"""A title mention requires one whole occurrence in a claim's prose."""

import pytest

import support.store.related as api

world = api.env
TITLE = "Step by Step"
OVERLAP = "Read `Step` by Step by Step today."


def file_note(world, title, text):
    saved = api.note(world, title, text)
    assert saved["status"] == "saved", saved
    return saved["page"]


def assert_mentions(world, focus, title, expected):
    response = api.rest(world, focus, "?limit=20")
    assert response.status_code == 200
    answers = {
        "REST": response.json(),
        "MCP": api.answer(world.client, world.key, "related", project="ops", slug=focus, limit=20),
    }
    seen = {}
    for reader, answer in answers.items():
        assert answer["searched"]["complete"], reader
        seen[reader] = sorted(
            (page["page"], why["there"]["text"])
            for page in answer["related"]
            for why in page["why"]
            if (why["kind"], why["term"]) == ("mentioned_by", title)
        )
    assert seen == {reader: sorted(expected) for reader in answers}


@pytest.mark.parametrize("extra", [0, 4, 5], ids=["overlap-alone", "five-pages", "six-pages"])
def test_an_overlapping_prose_occurrence_counts_towards_the_five_page_rule(world, extra):
    focus = file_note(world, TITLE, "The field guide is on the shelf.")
    pages = [(file_note(world, "Reader", OVERLAP), OVERLAP)]
    for n in range(extra):
        text = f"Group {n} reads Step by Step today."
        pages.append((file_note(world, f"Group {n}", text), text))
    assert_mentions(world, focus, TITLE, pages if len(pages) <= 5 else [])


@pytest.mark.parametrize(
    ("title", "claim", "names_it"),
    [
        pytest.param(TITLE, "Read `Step` by Step by Step by `Step` today.", True, id="middle-only"),
        pytest.param("Echo Echo", "Say `Echo` Echo Echo aloud.", True, id="repeated-word-title"),
        pytest.param("Echo", "Say `Echo` then Echo.", True, id="one-word-title"),
        pytest.param(TITLE, "Read `Step` by Step today.", False, id="starts-in-code"),
        pytest.param(TITLE, "Read Step by `Step` today.", False, id="ends-in-code"),
        pytest.param(TITLE, "Read `Step by Step` today.", False, id="inline-code"),
        pytest.param(TITLE, "~~~text\nStep by Step\n~~~", False, id="fenced-code"),
        pytest.param(TITLE, "    Step by Step", False, id="indented-code"),
        pytest.param(TITLE, "- Read the guide.\n\n    Step by Step", True, id="list-item-prose"),
        pytest.param(TITLE, "Open /Step/by/Step today.", False, id="passage-path"),
    ],
)
def test_each_occurrence_is_judged_in_its_own_markdown_context(world, title, claim, names_it):
    focus = file_note(world, title, "The field guide is on the shelf.")
    other = file_note(world, "Reader", claim)
    assert_mentions(world, focus, title, [(other, claim)] if names_it else [])
