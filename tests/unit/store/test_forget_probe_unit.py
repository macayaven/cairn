"""Measure the characters actually passed to CommonMark during a forget."""

from contextlib import closing

import pytest
from markdown_it import MarkdownIt

from cairn import compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store, erasure
from support.store.forget_cost import PER_RENDER
from support.store.forget_siblings import SPENT, narrow, page, under_steps
from support.store.reviewer7_forget_title import oracle


def observe_forget(store, separator, monkeypatch):
    """Pair each rendered body's size with every parser input size charged to it."""
    renders = []
    inputs = []
    render, parse = compiler.left_to_run, MarkdownIt.parse

    def rendered(title, claims, spans):
        nonlocal inputs
        # Charge any earlier work to the first body. Start each later count on entry
        # so it includes building that body and asking questions after it returns.
        if renders:
            inputs = []
        body, place = render(title, claims, spans)
        renders.append((len(body), inputs))
        return body, place

    def parsed(reader, source, *args, **kwargs):
        inputs.append(len(source))
        return parse(reader, source, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(compiler, "left_to_run", rendered)
        patch.setattr(MarkdownIt, "parse", parsed)
        assert Memory(store, lint_gate).forget("alpha", separator)["rebuilt"] == ["ops"]
    return renders


def check_forget(store, separator, siblings, monkeypatch):
    steps, title, body, claims = oracle(store, separator)
    assert [text for _, text in steps] == siblings[::-1]

    renders = observe_forget(store, separator, monkeypatch)
    after = page(store)
    assert (after.title, after.body, after.claims) == (title, body, claims)
    assert 0 < len(renders) <= len(steps) + 1  # the oracle also renders the survivors
    for body_size, inputs in renders:
        assert max(inputs, default=0) <= 2 * body_size
    total = sum(sum(inputs) for _, inputs in renders)
    largest_body = max(size for size, _ in renders)
    assert 0 < total <= PER_RENDER * len(renders) * largest_body
    return len(renders)


@pytest.fixture
def store():
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        yield store


@pytest.mark.parametrize(
    ("count", "width"),
    [
        pytest.param(28, 794, id="2139-character-body"),
        pytest.param(28, 1367, id="1367-character-tail"),
        pytest.param(28, 1368, id="1368-character-tail"),
        pytest.param(28, 12, id="short-tail"),
        pytest.param(1, 794, id="one-sibling"),
    ],
)
def test_sibling_runs_meet_the_same_render_and_input_budgets_at_each_width(
    store, monkeypatch, count, width
):
    siblings = narrow(count)
    tail = "        " + "é" * (width - 8)
    spent = [SPENT[0] + " in the field notebook for winter 2026.", *SPENT[1:]]
    separator = under_steps(store, siblings, [tail], spent=spent)
    # One render can find the run and a second can verify the survivors at any width.
    assert check_forget(store, separator, siblings, monkeypatch) <= 2


def test_a_proposed_line_larger_than_the_body_budget_keeps_the_oracle_result(store, monkeypatch):
    siblings = narrow(3)
    tail = "        End of samples."
    separator = under_steps(store, siblings, [tail])
    ask = erasure._before_it

    def oversized_line(body, blocks, at, line, *args, **kwargs):
        # A real body already contains its next line. Extend it only at the question
        # boundary so the proposed line alone exceeds the entire render's budget.
        long_line = line.ljust(8008, "x")
        assert len(long_line) > 2 * len(body)
        return ask(body, blocks, at, long_line, *args, **kwargs)

    monkeypatch.setattr(erasure, "_before_it", oversized_line)
    check_forget(store, separator, siblings, monkeypatch)
