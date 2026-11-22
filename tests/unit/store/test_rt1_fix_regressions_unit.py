"""RT1: what af207894 (the fix of the sixth report's must-fix 1 and 4) does to ordinary
sibling runs. Each test states what the plain loop keeps, or a bound the release before the
fix met, so it fails while the regression stands. Made-up notes.

The fix lets claims leave with the one named only (a) as many of the nearest as fit in a
question of twice the body, and (b) while the note that gives the title stays the note that
gives it. Both make a run of siblings that left in one reading leave a few at a time, or one
at a time:

- (a) a tail whose first line is long: three to five siblings a reading;
- (b) siblings each in its own note, their saves completing in the reverse of their stores
  (writers at the same time; built here as the reviewer built it, by filing notes inside
  another note's gate): each named sibling is the oldest note's last claim, so it leaves
  alone, though every title on the way is the same word and no heading moves.

The cost is the plain loop's: a render, sixteen whole-body parses and a parse of each fenced
claim, for every one to three siblings, inside the write. And past the sixteenth reading the
declared class leaves: an indented example with its own closing line, after the tail, that
the plain loop keeps and the release before the fix kept, leaves the page, search and the
export, though its note stays stored."""

import sys
from contextlib import closing

import pytest

from cairn.spans import CLOSERS_MAX
from cairn.store import Store
from support.rt1_harness import Note, Plan, Run, Watch, expected
from support.store.forget_siblings import CLOSED, narrow, plain_loop, texts, under_steps
from support.store.rt1_fix_regressions import SPENT, chain

LONG_TAIL = "        " + "tail " * 150 + "end"  # the fix's own test's line: 761 characters
# A forget's work, at most: CLOSERS_MAX + 3 renders of CLOSERS_MAX + 1 whole-body parses,
# each fenced claim parsed alone, questions of twice the body. 307 bodies' worth on the
# generated pages; the release before the fix spent 25 to 613 on every page below.
WORK_MAX = 400


@pytest.fixture
def store():
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        yield store


def test_the_fixs_own_long_line_page_with_an_example_after_the_tail_keeps_the_example(store):
    """test_forget_siblings_unit.py::test_a_long_line_after_a_hundred_claims_takes_twenty_
    two_renders, with that file's own CLOSED example filed after the tail. Twenty-one
    readings: past the sixteenth the example is of the declared class and leaves. The plain
    loop keeps it; before the fix the hundred siblings left in one reading and it stayed.
    Eighty siblings are enough (eighteen readings)."""
    separator = under_steps(store, narrow(100), [LONG_TAIL, CLOSED])
    expected_texts = plain_loop(store, separator)
    assert CLOSED in expected_texts
    assert store.forget_note(separator).rebuilt == ("ops",)
    assert texts(store) == expected_texts


def forgotten(plan: Plan, name: str):
    """(the claims the plain loop keeps, the forget's claims, the watch, the body before)."""
    run = Run(plan, check_restore=False)
    try:
        assert all(status == "saved" for status in run.status.values()), run.status
        want = expected(run.store, run.pid, run.ids[name])["ops"]
        body = run.store.get_page(run.pid, "ops").body
        watch = Watch()
        with watch.on():
            run.store.forget_note(run.ids[name])
        have = [claim.text for claim in run.store.get_page(run.pid, "ops").claims]
        return [claim.text for claim in want.kept], have, watch, body
    finally:
        run.close()


def test_siblings_stored_in_reverse_leave_together_and_the_example_after_the_tail_stays():
    """The page of test_forget_changed_title_unit.py::test_a_run_of_notes_each_older_than_
    the_one_before_it_takes_a_render_for_each (01ae715b: eighteen siblings, nineteen
    renders), but the oldest two siblings share one note, and an example closed where it
    stands follows the tail. Every title is "Ops". The forget takes sixteen one a reading
    (each is the oldest note's last claim), names the second of the shared note at the
    seventeenth, and since that note still gives the title, the declared class leaves with
    it: the example. The plain loop keeps it; the release before the fix took the eighteen
    in one reading and kept it. The smallest found: fifteen and two, or sixteen and one,
    keep it. With every sibling its own note, as in that test, the example stays."""
    older = [
        Note("item", "- item", "Ops"),
        Note("spent", SPENT, "Ops"),
        Note("parent", "- parent", "Ops"),
    ]
    pair = Note(
        "pair", "\n\n".join(f"    - ~~~\n      early {n}" for n in range(2)), "Ops", inner=older
    )
    plan = Plan(
        [
            *chain(16, [pair]),
            Note("sep", "Between.", "Ops"),
            Note("tail", "        End of samples.", "Ops"),
            Note("example", CLOSED, "Ops"),
        ],
        [],
    )
    want, have, watch, _ = forgotten(plan, "sep")
    assert CLOSED in want
    assert have == want
    assert len(watch.renders) <= CLOSERS_MAX + 2, len(watch.renders)


@pytest.mark.parametrize("count", [100])
def test_siblings_stored_in_reverse_cost_a_render_each(count):
    """No long line, one title: a hundred siblings stored in reverse take 101 readings and
    1,171 bodies' worth of parser input (3,236 characters: 3.8 million); three hundred, 301
    and 3,228 bodies (9.8 s against 0.12 s before the fix). The plain loop's cost, which
    the batch was made to avoid ("so that a note's author cannot make a forget cost
    that", store.erasure._plan)."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20_000))
    older = [
        Note("item", "- item", "Ops"),
        Note("spent", SPENT, "Ops"),
        Note("parent", "- parent", "Ops"),
    ]
    plan = Plan(
        [
            *chain(count, older),
            Note("sep", "Between.", "Ops"),
            Note("tail", "        End of samples.", "Ops"),
        ],
        [],
    )
    want, have, watch, body = forgotten(plan, "sep")
    assert have == want  # the survivors are right: the cost is the fault
    assert len(watch.renders) <= CLOSERS_MAX + 3, len(watch.renders)
    assert sum(watch.inputs) <= WORK_MAX * len(body), sum(watch.inputs) / len(body)


def test_a_long_line_after_two_hundred_siblings_costs_sixty_eight_readings(store):
    """Two hundred siblings in one note, a tail whose first line is 8,000 characters: 68
    readings and 1,042 bodies' worth of parser input (1.9 s against 0.06 s before the fix).
    A thousand siblings and a 40,000-character line: 335 readings, 350 million characters,
    64 s inside the write (0.76 s before the fix)."""
    from cairn.gate import lint_gate
    from cairn.memory import Memory

    memory = Memory(store, lint_gate)

    def note(text: str) -> str:
        answer = memory.note("alpha", text, title="Ops", topic="Ops")
        assert answer["status"] == "saved", answer
        return answer["note_id"]

    note("- item")
    note(SPENT)
    note("- parent")
    note("\n\n".join(f"    - ~~~\n      sample {n}" for n in range(200)))
    separator = note("Between.")
    note("        " + "tail " * 1600)
    body = store.get_page(store.get_project("alpha").id, "ops").body
    watch = Watch()
    with watch.on():
        memory.forget("alpha", separator)
    assert len(watch.renders) <= CLOSERS_MAX + 3, len(watch.renders)
    assert sum(watch.inputs) <= WORK_MAX * len(body), sum(watch.inputs) / len(body)
