"""The lint gate's work grows with the page, not with the page squared.

A page may hold 2,000 claims (cairn.store.PAGE_CLAIMS_MAX), and the gate runs on the whole
page each time a note is saved onto it. Measured before this test: 0.09 s at 500 claims and
1.64 s at 2,000, eighteen times as long for four times the page, because every fact of
every cited note was searched for in the page's whole body, again for each repeat of the
same value, and past 4,096 distinct values each search compiled its pattern anew.
"""

import gc
import random
import re
import time
from collections.abc import Callable

from cairn.compiler import CandidatePage, compile_note, render_body
from cairn.gate import lint_gate
from cairn.lint import _occurs
from cairn.store import Store

LINES = 40


def note(n: int) -> str:
    return "# Log\n\n" + "".join(
        f"Entry {n}-{i}: the retry count for job {i} moved from {i % 7} to {i % 5} today.\n\n"
        for i in range(LINES)
    )


def gate_input(store: Store, project_id: str) -> tuple[int, Callable[[], None]]:
    """How many claims the page holds, and the gate's run on it (which must pass)."""
    page = store.get_page(project_id, "log")
    spans = {s.id: s for c in store.list_notes(project_id) for s in store.note_spans(c.id)}
    candidate = CandidatePage(
        slug=page.slug,
        title=page.title,
        body=render_body(page.title, page.claims, spans),
        claims=page.claims,
    )

    def gate() -> None:
        result = lint_gate(candidate, spans)
        assert result.passed, result.violations[:3]

    return len(page.claims), gate


def best_of_five_each(*works: Callable[[], None]) -> list[float]:
    """Each work's best time of five, the works taking turns with the garbage collector off,
    so that a busy machine slows each of them alike."""
    best = [float("inf")] * len(works)
    gc.collect()
    gc.disable()
    try:
        for _ in range(5):
            for i, work in enumerate(works):
                started = time.perf_counter()
                work()
                best[i] = min(best[i], time.perf_counter() - started)
    finally:
        gc.enable()
    return best


def test_four_times_the_page_takes_about_four_times_as_long(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        project = store.create_project("ops")
        gates = {}
        for n in range(50):
            stored, _ = store.add_note(project.id, note(n))
            assert compile_note(store, stored).status == "saved"
            if n + 1 in (12, 50):
                claims, gate = gate_input(store, project.id)
                gates[claims] = gate
        small, large = best_of_five_each(gates[480], gates[2_000])
        assert large < 8 * small, f"480 claims: {small:.3f} s, 2,000 claims: {large:.3f} s"
    finally:
        store.close()


def test_a_whole_token_is_found_exactly_as_the_pattern_finds_it():
    """`_occurs` decides most cases without a pattern; the pattern is the definition."""

    def by_pattern(norm: str, text: str) -> bool:
        if not norm:
            return True
        return bool(re.search(r"(?<![0-9a-z_])" + re.escape(norm) + r"(?![0-9a-z_])", text))

    rng = random.Random(20261111)
    alphabet = "ab1_ -./é\n"
    for _ in range(4_000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        norm = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 4)))
        assert _occurs(norm, text) == by_pattern(norm, text), (norm, text)
    # Many near misses before the one whole token, and none at all.
    crowded = "1" * 500 + " 1 " + "1" * 500
    assert _occurs("1", crowded) is True and by_pattern("1", crowded) is True
    assert _occurs("1", "1" * 1_000) is False and by_pattern("1", "1" * 1_000) is False
    assert _occurs("111", "111") is True  # the whole text is the token
    assert _occurs("1", "a1" * 500) is False and by_pattern("1", "a1" * 500) is False
