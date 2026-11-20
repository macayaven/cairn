"""Measure the characters actually passed to CommonMark during a forget."""

from contextlib import closing
from dataclasses import dataclass, field
from itertools import pairwise

import pytest
from markdown_it import MarkdownIt
from test_forget_siblings_unit import SPENT, narrow, plain_loop, texts, under_steps

from cairn import compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store, erasure


@dataclass
class Render:
    body: str
    siblings: list[str]
    named: str | None
    inputs: list[str] = field(default_factory=list)


def observe_forget(store, separator, siblings, monkeypatch):
    renders = []
    render, parse = compiler.left_to_run, MarkdownIt.parse
    rendering = False

    def rendered(title, claims, spans):
        nonlocal rendering
        rendering = True
        try:
            body, place = render(title, claims, spans)
        finally:
            rendering = False
        live = [c.text for c in claims if c.text in siblings]
        renders.append(Render(body, live, claims[place - 1].text if place else None))
        return body, place

    def parsed(reader, source, *args, **kwargs):
        if renders and not rendering:
            renders[-1].inputs.append(source)
        return parse(reader, source, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(compiler, "left_to_run", rendered)
        patch.setattr(MarkdownIt, "parse", parsed)
        assert Memory(store, lint_gate).forget("alpha", separator)["rebuilt"] == ["ops"]
    return renders


@pytest.fixture
def store():
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        yield store


@pytest.mark.parametrize(
    ("count", "width", "fits_first"),
    [
        pytest.param(28, 794, 3, id="2139-character-body"),
        pytest.param(28, 1367, 3, id="third-neighbour-fits-exactly"),
        pytest.param(28, 1368, 2, id="third-neighbour-one-character-over"),
        pytest.param(28, 12, 27, id="all-neighbours-fit"),
        pytest.param(1, 794, 0, id="named-sibling-alone"),
    ],
)
def test_each_render_asks_as_many_of_the_nearest_siblings_as_fit(
    store, monkeypatch, count, width, fits_first
):
    siblings = narrow(count)
    tail = "        " + "é" * (width - 8)
    spent = [SPENT[0] + " in the field notebook for winter 2026.", *SPENT[1:]]
    separator = under_steps(store, siblings, [tail], spent=spent)
    expected = plain_loop(store, separator)
    renders = observe_forget(store, separator, siblings, monkeypatch)
    assert texts(store) == expected
    assert renders[-1].named is None and not renders[-1].siblings

    for index, (now, following) in enumerate(pairwise(renders)):
        assert now.named == now.siblings[-1]
        budget = 2 * len(now.body)
        # Up to the named claim the original prefix and unaltered siblings occur once.
        # Each extra question inserts the tail's first line and one blank line.
        prefix_size = now.body.index(now.named + "\n\n")
        earlier = now.siblings[:-1]
        fits = min(len(earlier), (budget - prefix_size) // (len(tail) + 2))
        asked = earlier[len(earlier) - fits :]
        if index == 0:
            assert fits == fits_first
            if width == 794 and count == 28:
                assert len(now.body) == 2139
                assert prefix_size + len(earlier) * (len(tail) + 2) == 22809
            if width == 1367:
                assert prefix_size + fits * (len(tail) + 2) == budget
        assert all(len(source) <= budget for source in now.inputs), (
            len(now.body),
            max(map(len, now.inputs)),
        )
        # Identify which sibling each inserted line actually followed in a parser input.
        questions = [
            [s for s in earlier if s + "\n\n" + tail + "\n" in source] for source in now.inputs
        ]
        if fits:
            assert asked in questions
        assert following.siblings == earlier[: len(earlier) - fits]


def test_no_question_and_only_the_named_claim_leaves_when_one_probe_cannot_fit(store, monkeypatch):
    siblings = narrow(3)
    tail = "        End of samples."
    separator = under_steps(store, siblings, [tail])
    expected = plain_loop(store, separator)
    ask = erasure._before_it
    long_line = "        " + "x" * 8000

    def oversized_line(body, blocks, at, line, *args, **kwargs):
        # In a real body its own next line already fits once. Supply a longer proposed
        # line at the boundary that receives it separately to exercise the zero-fit rule.
        assert line == tail
        assert len(long_line) > 2 * len(body)
        return ask(body, blocks, at, long_line, *args, **kwargs)

    monkeypatch.setattr(erasure, "_before_it", oversized_line)
    renders = observe_forget(store, separator, siblings, monkeypatch)
    assert texts(store) == expected
    assert all(long_line not in src for now in renders for src in now.inputs)
    assert [now.siblings for now in renders] == [siblings[:n] for n in range(3, -1, -1)]
