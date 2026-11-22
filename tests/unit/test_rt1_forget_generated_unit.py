"""RT1: generated pages, each forgotten through the public path and held to the plain loop
(test_rt1_harness.py says what is checked and why).

RT1_SEEDS=lo:hi picks the pages (default 0:200, about a minute). A failure names the seeds
and the kinds of fault; `uv run python tests/unit/test_rt1_harness.py --seeds=lo:hi
--shrink` shrinks the first of each kind to a small plan."""

import os

import pytest
from test_rt1_harness import CEILING, Run, generated, signature

from support.fuzz import chunk_seeds, fuzz_budget

pytestmark = pytest.mark.fuzz

_DEFAULT_HI = fuzz_budget(100, 500)
_BOUNDS = tuple(map(int, os.environ.get("RT1_SEEDS", f"0:{_DEFAULT_HI}").split(":")))
_CHUNKS = [
    range(_BOUNDS[0] + sl.start, _BOUNDS[0] + sl.stop)
    for sl in chunk_seeds(_BOUNDS[1] - _BOUNDS[0], 4)
]


@pytest.mark.parametrize("seed_range", _CHUNKS, ids=[f"{r.start}:{r.stop}" for r in _CHUNKS])
def test_every_generated_forget_keeps_what_the_plain_loop_keeps(seed_range: range):
    found: dict[str, list[int]] = {}
    seen = {"forgets": 0, "dropped": 0, "title_changed": 0, "top_moved": 0, "past16": 0}
    for seed in seed_range:
        run = Run(generated(seed)).run()
        for kind in signature(run):
            found.setdefault(kind, []).append(seed)
        for stat in run.stats:
            seen["forgets"] += 1
            seen["dropped"] += bool(stat.get("dropped"))
            seen["title_changed"] += bool(stat.get("title_changed"))
            seen["top_moved"] += bool(stat.get("top_moved"))
            seen["past16"] += stat.get("dropped", 0) > CEILING
        run.close()
    # the search reaches what it is for: pages where the plain loop drops claims, where the
    # title changes between its renders, and where that moves a claim between the top and
    # a section
    assert seen["dropped"], seen
    assert not found, {kind: seeds[:10] for kind, seeds in found.items()}
