"""The line that follows the claims a forget asks the parser about is read once a render,
whatever the number of claims asked (eighth round, finding 2): its indentation and first
character are worked out once (spans.following) and each question is built from them, so
building the questions is linear in the body. Counted in calls: how often a forget reads
that line whole, at the reviewer's three sizes (test_reviewer8_forget_copies_unit.py counts
the characters copied)."""

from contextlib import closing

import pytest
from test_forget_siblings_unit import narrow, under_steps

from cairn import compiler, spans
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store, erasure


@pytest.mark.parametrize(("count", "width"), [(14, 400), (28, 800), (56, 1600)])
def test_a_forget_reads_the_following_line_once_a_render_whatever_its_siblings(
    monkeypatch, count, width
):
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        separator = under_steps(store, narrow(count), [" " * 8 + "x" * (width - 8)])
        read, whole, renders = [], [], []
        work, ask, render = spans.following, spans.asked_line, compiler.left_to_run

        def following(line):  # the line read whole: its length
            read.append(len(line))
            return work(line)

        def asked_line(line, block):  # whether a question was handed the line itself
            whole.append(not isinstance(line, spans.Following))
            return ask(line, block)

        def left_to_run(*args, **kwargs):
            renders.append(1)
            return render(*args, **kwargs)

        for module in (spans, erasure):
            monkeypatch.setattr(module, "following", following)
            monkeypatch.setattr(module, "asked_line", asked_line)
        monkeypatch.setattr(compiler, "left_to_run", left_to_run)
        Memory(store, lint_gate).forget("alpha", separator)
        assert whole and not any(whole)  # every question built from the line worked out
        assert read == [width] * len(read) and 0 < len(read) <= len(renders) == 2
