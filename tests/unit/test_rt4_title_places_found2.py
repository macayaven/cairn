"""A fault of the title rule of related pages found one step past the shared parser (RT4,
fifth round), pinned as failing (xfail, strict) so the file passes as committed.

The facts (cairn.facts.extract_facts) read the text between two backticks of one line as
inline code even where Markdown pairs neither: a backtick in a link's title, in a link's
address written in angle brackets, in an autolink, or escaped. The page shows the title
between them as prose (cairn.web.markdown.render), and the contract, the tool's description
and the connect guide say code is what the page renders, naming only escaped backticks as
the facts' exception; the rule's module (cairn.relations) counts every inline code the
facts read. So such a claim names nothing, and beside five pages that name the title the
answer gives five, complete, where six name it. Decided by hand against the page's HTML.
Made-up names."""

import re

import pytest
import test_related_unit as api

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.web.markdown import render

env = api.env
TITLE = "Step by Step"
PLAIN = "Group {n} reads Step by Step today."
SHOWN_AS_PROSE = re.compile(r"</a> Step by Step `x</p>")
FACTS_PAIR = (
    "the facts read inline code across backticks Markdown does not pair; no sentence says so"
)

PAIRED_BY_THE_FACTS = [
    pytest.param('[d](u "`") Step by Step `x', id="backtick-in-a-links-title"),
    pytest.param("[d](<`>) Step by Step `x", id="backtick-in-a-links-address"),
    pytest.param("<https://e.x/`> Step by Step `x", id="backtick-in-an-autolink"),
]


def file(env, title: str, text: str) -> str:
    saved = api.note(env, title, text)
    assert saved["status"] == "saved", saved
    (claim,) = api.answer(env.client, env.key, "page", project="ops", slug=saved["page"])["claims"]
    assert claim["text"] == text
    return saved["page"]


def mentions(env, focus: str) -> dict[str, tuple[bool, list[str]]]:
    response = api.rest(env, focus, "?limit=20")
    assert response.status_code == 200
    answers = {
        "Memory": Memory(env.store, lint_gate).related("ops", focus, 20),
        "REST": response.json(),
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


@pytest.mark.parametrize("claim", PAIRED_BY_THE_FACTS)
@pytest.mark.parametrize("extra", [0, 5], ids=["alone", "beside-five"])
def test_a_title_the_page_shows_as_prose_between_unpaired_backticks_names_the_page(
    env, claim, extra
):
    assert SHOWN_AS_PROSE.search(str(render(claim))), render(claim)  # prose, no <code>
    focus = file(env, TITLE, "The field guide is on the shelf.")
    for n in range(extra):
        file(env, f"Group {n}", PLAIN.format(n=n))
    reader = file(env, "Reader", claim)
    # alone: a mention; beside five: six pages name it, more than COMMON, so none. Cairn
    # gives none alone, and five beside five, with the answer complete.
    assert mentions(env, focus) == dict.fromkeys(
        ("Memory", "REST"), (True, [reader] if not extra else [])
    )
