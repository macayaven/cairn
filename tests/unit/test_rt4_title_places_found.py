"""Faults of the title rule of related pages found one step past the fix (RT4, fourth round),
now fixed (the page's parser and the rule's reader of code are one definition,
cairn.spans.claim_markdown) and kept as regression tests.

A page shows a claim's text by cairn.web.markdown.render (page.html's `md`), whose parser
reads no heading, no setext heading and no picture. The reader of code the rule judged by
(cairn.spans.code_read) read all three, so it found code where the page shows prose and
prose where the page shows code. Each case was decided by hand against the page's HTML (the
title inside <code>, or not), and an independent oracle agrees with each
(test_rt4_title_places_held.py). Made-up names."""

import re

import pytest
import test_related_unit as api

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.web.markdown import render

env = api.env
TITLE = "Step by Step"
PLAIN = "Group {n} reads Step by Step today."
IN_CODE = re.compile(r"<code>[^<]*Step\W+by\W+(?:[^<]*\W)?Step[^<]*</code>")

# Claims the page shows with the title in code, which the reader of code reads as prose.
SHOWN_IN_CODE = [
    pytest.param("`Step by\n===\nStep` today.", id="setext-underline-inside-a-code-span"),
    pytest.param("> # `Step\n> by Step` today.", id="heading-in-a-quote"),
    pytest.param("- # `Step\n  by Step` today.", id="heading-in-a-list-item"),
    pytest.param('![a [b](u)](v "`") Step\nby Step ` x', id="picture-title-opens-a-code-span"),
]
# Claims the page shows with the title in prose, which the reader of code reads as code.
SHOWN_IN_PROSE = [
    pytest.param("Notes\n===\n    Step by Step today.", id="indented-after-a-setext-line"),
    pytest.param("> # Notes\n>     Step by Step today.", id="indented-after-a-heading-in-a-quote"),
]


def file(env, title: str, text: str) -> str:
    saved = api.note(env, title, text)
    assert saved["status"] == "saved", saved
    (claim,) = api.answer(env.client, env.key, "page", project="ops", slug=saved["page"])["claims"]
    assert claim["text"] == text.rstrip("\n")  # the note is one passage, the claim its text
    return saved["page"]


def mentions(env, focus: str) -> dict[str, tuple[bool, list[str]]]:
    """Each surface's answer: complete, and the pages that mention the title."""
    response = api.rest(env, focus, "?limit=20")
    assert response.status_code == 200
    answers = {
        "Memory": Memory(env.store, lint_gate).related("ops", focus, 20),
        "REST": response.json(),
        "MCP": api.answer(env.client, env.key, "related", project="ops", slug=focus, limit=20),
    }
    return {
        reader: (
            answer["searched"]["complete"],
            sorted(
                page["page"]
                for page in answer["related"]
                if any(why["kind"] == "mentioned_by" for why in page["why"])
            ),
        )
        for reader, answer in answers.items()
    }


def expect(env, focus: str, pages: list[str]) -> None:
    assert mentions(env, focus) == dict.fromkeys(("Memory", "REST", "MCP"), (True, sorted(pages)))


@pytest.mark.parametrize("claim", SHOWN_IN_CODE)
def test_a_title_the_page_shows_only_in_code_is_no_mention(env, claim):
    assert IN_CODE.search(str(render(claim))), render(claim)  # as page.html shows the claim
    focus = file(env, TITLE, "The field guide is on the shelf.")
    file(env, "Reader", claim)
    expect(env, focus, [])


@pytest.mark.parametrize("claim", SHOWN_IN_CODE[:1])
def test_five_pages_in_prose_beside_one_shown_in_code_are_five_mentions(env, claim):
    focus = file(env, TITLE, "The field guide is on the shelf.")
    plain = [file(env, f"Group {n}", PLAIN.format(n=n)) for n in range(5)]
    file(env, "Reader", claim)
    expect(env, focus, plain)  # five name it in prose; cairn counts six and gives none


@pytest.mark.parametrize("claim", SHOWN_IN_PROSE)
@pytest.mark.parametrize("extra", [0, 5], ids=["alone", "beside-five"])
def test_a_title_the_page_shows_in_prose_is_a_mention_and_counts(env, claim, extra):
    rendered = str(render(claim))
    assert "Step by Step today." in rendered and not IN_CODE.search(rendered), rendered
    focus = file(env, TITLE, "The field guide is on the shelf.")
    for n in range(extra):
        file(env, f"Group {n}", PLAIN.format(n=n))
    reader = file(env, "Reader", claim)
    # alone: one mention; beside five: six pages name it, more than COMMON, so none. Cairn
    # gives none alone, and five beside five, with the answer complete.
    expect(env, focus, [reader] if not extra else [])
