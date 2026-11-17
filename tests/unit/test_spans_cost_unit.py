"""G1: chunking a line of backticks costs time that grows with the line, not its square.

In P1's style (tests/unit/test_facts_lint_cost_unit.py): one input and the same shape eight
times larger, in this process, best of five runs each with the garbage collector off, and a
bound on the ratio of the two times. Linear work gives about 8 and quadratic work about 64;
the bound is 24. No wall-clock limit is asserted.

Measured before G1 (one line of backticks with one more backtick after it):
spans.chunk took 0.011 s for 8,000 backticks, 1.7 s for 64,000 and 44 s for 512,000. The
fence pattern took the whole run of backticks, found a backtick later on the line, gave one
back and tried again, each try reading the rest of the line.
"""

import gc
import time
from collections.abc import Callable

from cairn.spans import chunk


def best_of_five(work: Callable[[], object]) -> float:
    gc.collect()
    gc.disable()
    try:
        times = []
        for _ in range(5):
            started = time.perf_counter()
            work()
            times.append(time.perf_counter() - started)
        return min(times)
    finally:
        gc.enable()


def assert_near_linear(text_of: Callable[[int], str], n: int, bound: float = 24) -> None:
    small, large = text_of(n), text_of(8 * n)
    small_s = best_of_five(lambda: chunk("cost", small))
    large_s = best_of_five(lambda: chunk("cost", large))
    assert large_s < bound * small_s, (
        f"8x the input took {large_s / small_s:.1f}x the time "
        f"({small_s * 1000:.2f} ms, then {large_s * 1000:.2f} ms); the bound is {bound}x"
    )


LINES = 300  # lines of the shape in one text, so the small input takes a few milliseconds


def test_lines_of_backticks_with_one_more_after_them_are_near_linear():
    assert_near_linear(lambda n: ("`" * n + " x`\n\n") * LINES, 2_000)


def test_the_same_lines_inside_list_items_are_near_linear():
    """A list item asks the fence pattern about each of its lines, from its content column."""
    assert_near_linear(lambda n: ("- " + "`" * n + " x`\n  " + "`" * n + " y`\n\n") * LINES, 2_000)


def test_a_heading_with_a_long_run_of_blanks_is_near_linear():
    """Found while measuring G1: the heading pattern's lazy title, followed by an optional
    closing run and blanks, tried each length of title and read the blanks after it again,
    0.12 s for 2,000 blanks in one heading and some 30 s for 32,000. The same text is read
    whole, and a closing "#" run dropped, by a pass over the line."""
    assert_near_linear(lambda n: ("# a" + " " * n + "b\n\n") * LINES, 200)
    assert_near_linear(lambda n: ("## a " + " \t" * n + "x #\n\n") * LINES, 100)
