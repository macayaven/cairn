"""Two passages are a repeat only when the verbatim gate would call them the same (review 7.3).

`repeat_groups` (the list a model is sent, the plan tidy_page folds by, the rule a model's
`same` lists are held to) reads a claim the way verbatim_gate reads a passage: code, tables
and prose that holds structure line by line, plain prose as text. Made-up notes."""

import itertools
import json

import pytest

from cairn.arrange import parse_arrangement, repeat_groups, tidy_page
from cairn.compiler import CandidatePage, render_body
from cairn.gate import lint_gate, verbatim_gate
from cairn.memory import Memory
from cairn.spans import chunk
from cairn.store import Claim, Store

NESTED = "```python\nif ok:\n    run()\n    cleanup()\n```"
FLAT = "```python\nif ok:\n    run()\ncleanup()\n```"
PROSE = "First prose note."


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def claims_of(*texts: str) -> tuple[list[Claim], dict]:
    spans = [chunk(f"n{n}", text)[0] for n, text in enumerate(texts)]
    claims = [Claim(id=f"cl_{n:016x}", text=s.text, span_ids=(s.id,)) for n, s in enumerate(spans)]
    return claims, {s.id: s for s in spans}


# The reviewer's probe, on the repository's fixtures


def test_code_that_differs_in_indentation_is_not_folded_and_the_real_repeat_is(store):
    store.create_project("alpha")
    memory = Memory(store, lint_gate)
    slug = memory.note("alpha", f"{NESTED}\n\n{PROSE}", title="CodePage")["page"]
    memory.note("alpha", f"{FLAT}\n\n{PROSE}", title="CodePage", topic="CodePage")
    project = store.get_project("alpha")
    tidied = tidy_page(store, project.id, slug)
    assert (tidied.status, tidied.merged, tidied.problem) == ("arranged", 1, None)
    page = store.get_page(project.id, slug)
    assert [claim.text for claim in page.claims] == [NESTED, PROSE, FLAT]


def test_two_code_blocks_that_differ_in_indentation_are_no_repeat():
    claims, _ = claims_of(NESTED, FLAT)
    assert repeat_groups(claims, {}) == []


def test_a_models_same_list_of_them_is_refused_as_other_text():
    claims, _ = claims_of(NESTED, FLAT)
    answer = json.dumps({"order": [1], "same": [[1, 2]]})
    with pytest.raises(ValueError, match="do not have the same text"):
        parse_arrangement(answer, claims, sections={c.id: () for c in claims})


# Equal keys are exactly the folds the gate accepts

PAIRS = {
    "prose wrapped elsewhere": ("Drift mirrors two\nbuckets.", "Drift mirrors two buckets."),
    "prose with other words": ("Drift mirrors two buckets.", "Drift mirrors three buckets."),
    "prose with a no-break space": (
        "Drift mirrors two buckets.",
        "Drift mirrors\u00a0two buckets.",
    ),
    "code with spaces at a line's end": ("```\nrun()   \n```", "```\nrun()\n```"),
    "code indented otherwise": (NESTED, FLAT),
    "code with its lines joined": ("```\na b\n```", "```\na\nb\n```"),
    "a table the same": ("| a | b |\n|---|---|", "| a | b |\n|---|---|"),
    "a table spaced otherwise": ("| a | b |\n|---|---|", "| a |  b |\n|---|---|"),
    "structured prose with a hard break": ("Alpha.  \nBeta.", "Alpha.\nBeta."),
    "structured prose the same": ("Alpha.  \nBeta.", "Alpha.  \nBeta."),
    "a list item rewrapped": ("- Install Docker\n  and uv", "- Install Docker and uv"),
    "a list item and the same text unmarked": ("- Install Docker", "Install Docker"),
    "a quote and its text": ("> Keep it off.", "Keep it off."),
}


@pytest.mark.parametrize("name", list(PAIRS))
def test_two_claims_repeat_exactly_when_the_gate_accepts_their_fold(name):
    first, second = PAIRS[name]
    claims, spans = claims_of(first, second)
    folded = (
        claims[0].model_copy(update={"span_ids": (*claims[0].span_ids, *claims[1].span_ids)}),
    )
    page = CandidatePage(slug="p", title="P", body=render_body("P", folded, spans), claims=folded)
    accepted = verbatim_gate(page, spans).passed
    assert (repeat_groups(claims, {}) == [[1, 2]]) == accepted, name


def test_the_rule_is_symmetric_and_each_pair_is_judged_both_ways():
    texts = list(dict.fromkeys(itertools.chain.from_iterable(PAIRS.values())))
    for first, second in itertools.permutations(texts, 2):
        claims, spans = claims_of(first, second)
        folded = (
            claims[0].model_copy(update={"span_ids": (*claims[0].span_ids, *claims[1].span_ids)}),
        )
        body = render_body("P", folded, spans)
        page = CandidatePage(slug="p", title="P", body=body, claims=folded)
        assert (repeat_groups(claims, {}) == [[1, 2]]) == verbatim_gate(page, spans).passed, (
            first,
            second,
        )


def test_repeats_under_other_headings_are_still_apart():
    claims, _ = claims_of(PROSE, PROSE)
    sections = {claims[0].id: ("A",), claims[1].id: ("B",)}
    assert repeat_groups(claims, sections) == []
    assert repeat_groups(claims, {c.id: ("A",) for c in claims}) == [[1, 2]]


def test_text_that_holds_no_passage_repeats_only_itself():
    claims = [
        Claim(id="cl_1", text="# One heading", span_ids=("sp_1",)),
        Claim(id="cl_2", text="# Another heading", span_ids=("sp_2",)),
        Claim(id="cl_3", text="# One heading", span_ids=("sp_3",)),
    ]
    assert repeat_groups(claims, {}) == [[1, 3]]
