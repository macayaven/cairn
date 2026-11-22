"""Shared test helpers extracted from test_forget_render_cap_unit."""

from support.rt1_harness import (
    Note,
    Plan,
)
from support.store.forget_cost import older
from support.store.rt1_fix_regressions import SPENT

CAP = 24  # erasure.RENDERS_MAX, as the guide says it

HARD = 32  # erasure.RENDERS_HARD, as the guide says it

WIDE = "    -   ~~~\n        wide sibling"  # its item would not hold a line indented six

EXAMPLE = "    ~~~\n    an example\n    ~~~"  # closed where it stands, after the tail


def cascade(count: int, *, wide: bool = False, example: str = "", spellings=("Ops", "OPS")) -> Plan:
    """`count` siblings stored in reverse, spelling the topic Ops and OPS in turn, an aside
    under `## Ops`, and a tail indented six: the plain loop renders once for each sibling
    (test_forget_cost_unit.alternating), and each sibling it names is the oldest note left,
    so the title's text changes with each. With `wide`, a sibling before them all whose
    item would not hold the tail: the plain loop keeps it. With `example`, an indented
    example after the tail, closed where it stands: the plain loop keeps it. The first of
    `spellings` is the innermost sibling's (the last the plain loop names)."""
    inner = [*older(), *([Note("wide", WIDE, "Ops")] if wide else [])]
    for n in range(count):
        inner = [Note(f"s{n}", f"    - ~~~\n      sample {n}", spellings[n % 2], inner=inner)]
    return Plan(
        [
            *inner,
            Note("sep", "Between.", "Ops"),
            Note("tail", "      End.", "Ops"),
            Note("aside", "## Ops\n\nAn aside.", "Ops"),
            *([Note("example", example, "Ops")] if example else []),
        ],
        [],
    )


WORD = "field notes"


def spelling(k: int) -> str:
    """A spelling of WORD for each k, all of one slug: bit i of k + 1 uppercases letter i."""
    letters = [i for i, ch in enumerate(WORD) if ch.isalpha()]
    return "".join(
        ch.upper() if i in letters and (k + 1) >> letters.index(i) & 1 else ch
        for i, ch in enumerate(WORD)
    )


INLINE = "Text with `inline` code."  # neither can open a code fence

AT_THE_MARGIN = "```\nat the margin\n```"


def sectioned(count: int) -> Plan:
    """`uncovering`, each pair in a section of its own: the list item and the sibling under
    `## Group k`, the tail under `## spelling(k)` then `### Group k`, so that it joins the
    sibling's section when the page is titled spelling(k). After the closed fences, with
    no heading, a paragraph with inline code and a fenced block at the margin."""
    inner = [Note("item", "- item", spelling(0)), Note("spent", SPENT, spelling(0))]
    inner += [Note("inline", INLINE, spelling(0)), Note("margin", AT_THE_MARGIN, spelling(0))]
    inner.append(Note("p1", "## Group 1\n\n- parent 1", spelling(0)))
    for k in range(1, count + 1):
        group = f"## Group {k}"
        sibling = Note(f"y{k}", f"{group}\n\n    - ~~~\n      sample {k}", spelling(k), inner=inner)
        tail = Note(f"t{k}", f"## {spelling(k)}\n\n#{group}\n\n      tail {k}", spelling(0))
        after = [Note(f"p{k + 1}", f"## Group {k + 1}\n\n- parent {k + 1}", spelling(0))]
        inner = [sibling, tail, *(after * (k < count))]
    return Plan([Note("intro", "Intro.", spelling(0)), *inner], [])
