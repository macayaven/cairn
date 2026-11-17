"""Telling work that grows with its input from work that grows with its square, with no
limit in seconds.

In P1's style (tests/unit/test_facts_lint_cost_unit.py): the same shape at n and at eight
times n, timed in this one process, best of five runs each with the garbage collector off,
and a bound on the ratio of the two times. Linear work gives about 8 and quadratic work
about 64; the bound is 24, well away from both. A busy machine slows both sizes, so it
moves the ratio little where it would break a limit in seconds.
"""

import gc
import time
from collections.abc import Callable

TIMES = 8
BOUND = 24


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


def assert_near_linear(make: Callable[[int], Callable[[], object]], n: int) -> None:
    """`make(n)` builds the input of size n and returns the work to time on it; that work
    must start from the same state each time it runs."""
    small, large = make(n), make(TIMES * n)
    small_s, large_s = best_of_five(small), best_of_five(large)
    assert large_s < BOUND * small_s, (
        f"{TIMES}x the input took {large_s / small_s:.1f}x the time "
        f"({small_s * 1000:.2f} ms, then {large_s * 1000:.2f} ms); the bound is {BOUND}x"
    )
