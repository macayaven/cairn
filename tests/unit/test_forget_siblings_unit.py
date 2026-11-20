"""A forget that uncovers a run of fences takes the run in one render, and only the run
(fifth report, must-fix 1; CORE9A).

A fence with no closing line ends where its list item ends. Sibling items with such a fence
each end at the next sibling, and the last at a paragraph at the margin. Forget that
paragraph's note and the indented tail after it belongs to the last sibling's item: its
fence covers the tail. With the body's closers spent the sibling is named and leaves; then
the tail belongs to the sibling before. A render for each sibling, backwards, and past twice
the ceiling a forget took every claim with a backtick or a tilde, the paragraph with inline
code that the body writes first among them.

Now the claims before the one named are asked of the parser in the same render
(erasure._before_it, spans.fences_reaching): each is written with the tail's first line
after it, and those whose fence takes the line in leave with the claim named, for as long as
they follow one another. The smallest inputs are here; the generated ones are in
test_generated_forget_fences_unit.py, beside the plain loop.

Each page is filed through Memory.note, one note at a time. Renders are calls of
compiler.left_to_run; questions are calls of spans.fences_reaching. Made-up notes."""

import io
from contextlib import closing

import pytest
from markdown_it import MarkdownIt
from test_guide_facts_slack_unit import says

import cairn.spans
from cairn import compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import CLOSERS_MAX
from cairn.store import Store, erasure

FIRST, STEPS = "## First\n\n", "## Steps\n\n"
INLINE = "Ada keeps `port 80` open."
SPENT = [f"    ~~~\n    first {n}" for n in range(CLOSERS_MAX)]  # each runs into the next
END = "  End of first."
WIDE = "    -   ~~~\n        wide"  # its text starts at column eight
CLOSED = "      ```text\n      Brook owns the rollback.\n      ```"
FENCED = "~~~text\nQuill owns the log.\n~~~"  # a fenced block as it is commonly written
CHECKS = "## What the checks on pages catch"  # the section of the security guide


def narrow(count: int, word: str = "sample") -> list[str]:
    """Sibling items whose text starts at column six, a fence in each that no line closes."""
    return [f"    - ~~~\n      {word} {n}" for n in range(count)]


@pytest.fixture
def store():
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        yield store


def filed(store: Store, text: str) -> str:
    answer = Memory(store, lint_gate).note("alpha", text, title="Ops", topic="Ops")
    assert (answer["status"], answer["page"]) == ("saved", "ops"), answer
    return answer["note_id"]


def page(store: Store):
    return store.get_page(store.get_project("alpha").id, "ops")


def texts(store: Store) -> list[str]:
    return [claim.text for claim in page(store).claims]


def under_steps(store: Store, before: list[str], after: list[str], spent=SPENT) -> str:
    """The page: a paragraph with inline code; under "First" a list that spends a closer
    for each of `spent`; under "Steps" a list item, `before`, a paragraph at the margin and
    `after`. The note of the paragraph at the margin."""
    filed(store, INLINE)
    filed(store, FIRST + "- item")
    filed(store, FIRST + "\n\n".join([*spent, END]))
    filed(store, STEPS + "- parent")
    filed(store, STEPS + "\n\n".join(before))
    separator = filed(store, STEPS + "Between.")
    filed(store, STEPS + "\n\n".join(after))
    return separator


class Counted:
    """What a forget costs: its renders, its parses of a whole body, its questions."""

    def __init__(self, store: Store, note_id: str) -> None:
        self.renders = self.parses = self.questions = 0
        parse, render, ask = MarkdownIt.parse, compiler.left_to_run, erasure.fences_reaching

        def parsed(reader, source, *args, **kwargs):
            self.parses += source.startswith("# Ops\n\n")
            return parse(reader, source, *args, **kwargs)

        def rendered(*args):
            self.renders += 1
            return render(*args)

        def asked(*args):
            self.questions += 1
            return ask(*args)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(MarkdownIt, "parse", parsed)
            patch.setattr(compiler, "left_to_run", rendered)
            patch.setattr(erasure, "fences_reaching", asked)
            assert store.forget_note(note_id).rebuilt == ("ops",)


def plain_loop(store: Store, note_id: str) -> list[str]:
    """What the page's claims are once the note's own are gone and the loop with no
    ceiling has run: render, drop the one claim named, render again."""
    gone = {span.id for span in store.note_spans(note_id)}
    left = [claim for claim in page(store).claims if not gone & set(claim.span_ids)]
    spans = store.get_spans(i for claim in left for i in claim.span_ids)
    while place := compiler.left_to_run("Ops", left, spans)[1]:
        del left[place - 1]
    return [claim.text for claim in left]


def restored(store: Store) -> list[str]:
    archive = io.BytesIO()
    store.export(archive)
    with closing(Store(":memory:")) as target:
        target.import_export(io.BytesIO(archive.getvalue()))
        return texts(target)


@pytest.mark.parametrize("count", [1, 17, 40, 300])
def test_the_siblings_leave_in_one_render_whatever_their_number(store, count):
    """The reviewer's page. It was a render for each sibling up to thirty-two, then every
    claim with a backtick or a tilde. Two renders now, and one question in the first: the
    siblings leave, and the paragraph with inline code and the sixteen fences the body
    closed under the other heading stay."""
    siblings, tail = narrow(count), "        End of samples."
    separator = under_steps(store, siblings, [tail])
    cost = Counted(store, separator)
    assert texts(store) == [INLINE, "- item", *SPENT, END, "- parent", tail]
    assert (cost.renders, cost.questions) == (2, count > 1)
    assert cost.parses == 2 * (CLOSERS_MAX + 1) + cost.questions
    assert restored(store) == texts(store)
    (event,) = [e for e in store.list_audit(100) if e.action == "note.forgotten"]
    assert event.detail["claims_left"] == count


STOPS = {
    "its list item would not hold the tail": WIDE,
    "its fence is closed": "    ```text\n    Brook owns the rollback.\n    ```",
}


@pytest.mark.parametrize("why", list(STOPS))
def test_a_claim_before_the_run_stays_and_so_do_those_before_it(store, why):
    """One step past: the fences end alike, and what a claim's fence holds is the parser's
    to say. Before the three siblings that leave stands a wide sibling, whose text starts
    at column eight where the tail starts at six, or an indented example with its own
    closing line. With the three gone the tail is not in its fence, the plain loop stops
    there, and so does the forget: two questions, since the first, of all six claims, had
    an answer that did not hold before the ones that did."""
    before = [*narrow(2, "early"), STOPS[why], *narrow(3)]
    separator = under_steps(store, before, ["      tail at six"])
    expected = plain_loop(store, separator)
    assert expected == [INLINE, "- item", *SPENT, END, "- parent", *before[:3], "      tail at six"]
    cost = Counted(store, separator)
    assert texts(store) == restored(store) == expected
    assert (cost.renders, cost.questions) == (2, 2)
    says(
        "security",
        CHECKS,
        "Claims that would each leave such a fence open in turn, as sibling list items do whose"
        " fences have no closing line, leave together; a claim before them stays when its fence"
        " is closed or its list item would not hold what follows, and so does every claim"
        " before that one.",
    )


def test_a_tail_that_every_item_holds_takes_the_wide_sibling_too(store):
    before = [*narrow(2, "early"), WIDE, *narrow(3)]
    separator = under_steps(store, before, ["        tail at eight"])
    expected = plain_loop(store, separator)
    assert expected == [INLINE, "- item", *SPENT, END, "- parent", "        tail at eight"]
    cost = Counted(store, separator)
    assert texts(store) == restored(store) == expected
    assert (cost.renders, cost.questions) == (2, 1)


def test_with_a_closer_to_spare_the_fence_is_closed_and_nothing_is_asked(store):
    """Fifteen closers spent: the last sibling's fence gets the sixteenth, no claim is
    named, and the parser is asked nothing about the siblings."""
    separator = under_steps(store, narrow(24), ["        End of samples."], spent=SPENT[:-1])
    cost = Counted(store, separator)
    assert texts(store) == [
        INLINE,
        "- item",
        *SPENT[:-1],
        END,
        "- parent",
        *narrow(24),
        "        End of samples.",
    ]
    assert (cost.renders, cost.questions) == (1, 0)


def test_a_claim_named_because_no_line_closes_it_takes_no_claim_before_it(store, monkeypatch):
    """The claims before are asked only when the body wrote all its closers: with one to
    spare, the next render would close the fence of the claim before, not name it. No page
    is known where a closer does not close, so the parser's answer is stood in for, for
    the one fence written with four tildes: its closer goes to the margin. The plain loop
    names that sibling, then closes the fence of the sibling before it, and every other
    claim stays. The forget does the same and asks nothing."""
    siblings = [*narrow(4), "    - ~~~~\n      Quill owns the log."]
    separator = under_steps(store, siblings, ["        End of samples."], spent=[])
    closer = cairn.spans._closer
    monkeypatch.setattr(
        cairn.spans, "_closer", lambda opener, run: run if run == "~~~~" else closer(opener, run)
    )
    expected = plain_loop(store, separator)
    assert expected == [INLINE, "- item", END, "- parent", *siblings[:4], "        End of samples."]
    cost = Counted(store, separator)
    assert texts(store) == expected
    assert (cost.renders, cost.questions) == (2, 0)
    assert page(store).body.count("\n      ~~~\n\n        End of samples.\n") == 1  # closed


@pytest.mark.parametrize("steps", [CLOSERS_MAX, CLOSERS_MAX + 1])
def test_sixteen_times_and_then_the_later_claims_that_can_open_a_fence_leave(store, steps):
    """A list whose fences run on sixteen times more than the body closes, or seventeen;
    after them an indented example with its own closing line, a fenced block at the margin
    and a paragraph with inline code. Sixteen: each is named in its turn and nothing else
    leaves. Seventeen: the last of them leaves with the indented example, which was whole.
    The block at the margin and the paragraph stay both times."""
    running = [f"    ~~~\n    running {n}" for n in range(CLOSERS_MAX + steps)]
    example = "    ```text\n    Brook owns the rollback.\n    ```"
    filed(store, "- item")
    separator = filed(store, "Between.")
    filed(store, "\n\n".join([*running, END, example, FENCED, INLINE]))
    whole = ["- item", *running[:CLOSERS_MAX], END, example, FENCED, INLINE]
    assert plain_loop(store, separator) == whole
    cost = Counted(store, separator)
    past = steps > CLOSERS_MAX
    assert texts(store) == restored(store) == [t for t in whole if not (past and t == example)]
    assert cost.renders == CLOSERS_MAX + 1 + past
    says(
        "security",
        CHECKS,
        "A forget does that sixteen times; if a fence still runs on then, the claim that opens"
        " it leaves with every claim written after it on the page that begins with indentation"
        " and has a line that opens a fence, even one whose fence would have closed inside the"
        " claim.",
        "No other claim leaves for a fence: a paragraph with inline code stays, and so does a"
        " fenced block that starts at the margin, wherever the page writes them.",
    )


# What a question costs: never more than twice the body (sixth report, must-fix 4), and a
# long line after the claims no longer narrows what leaves (RT1, the tester's finding)


def questions(store: Store, note_id: str, monkeypatch):
    """The forget, watched: for each render (its body, the claim it named or None, the
    texts given to the parser for the questions asked after it)."""
    seen, render, ask, parse = [], compiler.left_to_run, erasure.fences_reaching, MarkdownIt.parse
    asking = []

    def rendered(title, claims, spans):
        body, place = render(title, claims, spans)
        seen.append((body, claims[place - 1].text if place else None, []))
        return body, place

    def asked(*args):
        asking.append(1)
        try:
            return ask(*args)
        finally:
            asking.pop()

    def parsed(reader, source, *args, **kwargs):
        if asking:
            seen[-1][2].append(source)
        return parse(reader, source, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(compiler, "left_to_run", rendered)
        patch.setattr(erasure, "fences_reaching", asked)
        patch.setattr(MarkdownIt, "parse", parsed)
        assert store.forget_note(note_id).rebuilt == ("ops",)
    return seen


LONG = "        " + "tail " * 150 + "end"  # a tail of one line of 761 characters


def test_a_long_line_is_asked_cut_and_every_sibling_leaves_in_the_first_render(store, monkeypatch):
    """Thirty siblings before that tail. Written whole once after each sibling asked, the
    line made a question of 23,459 characters for a body of 2,121, and the bound of twice
    the body then let three leave a render: nine renders. Whether a fence takes the line in
    depends on its indentation and on whether it holds anything else, never on its length
    (spans.fences_reaching), so a line longer than the claim it follows is written cut to
    those: the eight spaces and `t`. One question, of all twenty-nine, well within twice
    the body; the thirty leave in the first render and a second finds nothing. The claims
    left are the plain loop's."""
    siblings = narrow(30)
    separator = under_steps(store, siblings, [LONG])
    expected = plain_loop(store, separator)
    seen = questions(store, separator, monkeypatch)
    assert texts(store) == restored(store) == expected
    assert [named for _, named, _ in seen] == [siblings[-1], None]
    body, _, sources = seen[0]
    assert len(body) == 2121 and len(sources) == 1
    prefix = body.index(f"{siblings[-1]}\n\n")  # the body up to the claim named
    assert len(sources[0]) == prefix + 29 * len("        t\n\n") <= 2 * len(body)
    assert sources[0].count("\n\n        t\n\n") == 29 and LONG not in sources[0]


@pytest.mark.parametrize("count", [100, 1000])
def test_a_long_line_after_many_claims_costs_two_renders(store, monkeypatch, count):
    """A hundred siblings before that line left four or five at a render: twenty-two
    renders. A thousand before a line of 40,000 characters left one or two: 335 renders,
    a minute inside the write. Two renders now, one question in the first, and the claims
    left are the plain loop's, which renders once for each sibling and once more (run
    here for the hundred; for the thousand it is the minute)."""
    tail = LONG if count == 100 else "        " + "tail " * 8000
    siblings = narrow(count)
    separator = under_steps(store, siblings, [tail])
    expected = [INLINE, "- item", *SPENT, END, "- parent", tail]
    if count == 100:
        assert plain_loop(store, separator) == expected
    seen = questions(store, separator, monkeypatch)
    assert texts(store) == expected
    assert [len(sources) for _, _, sources in seen] == [1, 0]
    assert all(len(source) <= 2 * len(body) for body, _, sources in seen for source in sources)
    says(
        "security",
        CHECKS,
        "How long the line after such claims is does not change how many leave together.",
    )


def test_where_not_one_claim_fits_nothing_is_asked_and_the_claim_named_leaves_alone(
    store, monkeypatch
):
    """The bound stays for what a cut line cannot make small. No claim's text makes it
    reach here (a cut line is as deep as the claim reaches and one character more), so the
    case is made: the line is written whole and longer than twice the body. Nothing is
    given to the parser for it, each sibling is named in its turn, and the claims left
    are the plain loop's."""
    siblings = narrow(3)
    separator = under_steps(store, siblings, ["        End of samples."])
    expected = plain_loop(store, separator)
    monkeypatch.setattr(erasure, "asked_line", lambda line, block: "x" * 9000)
    seen = questions(store, separator, monkeypatch)
    assert texts(store) == expected
    assert [named for _, named, _ in seen] == [*reversed(siblings), None]
    assert all(len(body) < 4500 and not sources for body, _, sources in seen)


# What the declared class costs besides itself: the two differences from the plain loop
# that are left, on the smallest pages found for them. Both need the forget to be past its
# ceiling (seventeen renders that each name a claim) at a place like these.

RUNNING = [f"    ~~~\n    running {n}" for n in range(2 * CLOSERS_MAX + 1)]


def past_the_ceiling(store: Store, steps: list[str]) -> str:
    """A list with no section whose fences run on thirty-two times once the paragraph at
    the margin is forgotten: sixteen closed, sixteen named one by one. Then `steps` under a
    heading, where the next fault is the seventeenth. The paragraph's note."""
    filed(store, "- item")
    separator = filed(store, "Between.")
    filed(store, "\n\n".join(RUNNING))
    filed(store, STEPS + "- parent")
    filed(store, STEPS + "\n\n".join(steps))
    return separator


def test_past_the_ceiling_a_claim_before_the_fault_leaves_when_the_class_uncovers_the_tail(store):
    """A difference from the plain loop that is NOT of the declared class, kept here as it
    is so that it is seen (CORE9A's report gives it to the next review).

    Under "Steps": a wide sibling, a narrow one, an indented example with its own closing
    line, a tail at column eight. The narrow sibling's fence covers the example and is the
    seventeenth fault. The plain loop drops it and stops: the example is not in the wide
    sibling's item, and stands between that item and the tail. The forget is past its
    ceiling, so the example leaves as a claim after the fault that can open a fence (the
    declared class). Then nothing stands between the wide sibling and the tail, its fence
    covers the tail, and it leaves as a claim at fault does: in the body the forget writes
    it is one. It stands before the fault, so it is not of the class.

    Not removed in this round. Past its ceiling the forget takes a later claim that can
    open a fence without asking whether it is at fault where it would stand: that is the
    declared class, and this page is what the class costs one claim further."""
    tail = "        tail at eight"
    separator = past_the_ceiling(store, [WIDE, *narrow(1), CLOSED, tail])
    kept = ["- item", *RUNNING[:CLOSERS_MAX], RUNNING[-1], "- parent"]
    assert plain_loop(store, separator) == [*kept, WIDE, CLOSED, tail]
    cost = Counted(store, separator)
    assert texts(store) == restored(store) == [*kept, tail]
    assert cost.renders == CLOSERS_MAX + 2
    assert erasure._may_open_a_fence(CLOSED) and erasure._may_open_a_fence(WIDE)
    says(
        "security",
        CHECKS,
        "A claim written before those, whose fence covers what follows once they have left,"
        " leaves then too.",
    )


def test_past_the_ceiling_a_claim_stays_that_the_plain_loop_drops_for_a_claim_of_the_class(
    store,
):
    """The other way round, and the forget keeps more: three narrow siblings, the indented
    example, a tail at column two. The plain loop drops all three, each for covering the
    example, which it keeps. The forget takes the example with the first of them (the
    seventeenth fault, past the ceiling), and the two before then cover nothing."""
    siblings = narrow(3)
    separator = past_the_ceiling(store, [*siblings, CLOSED, "  tail at two"])
    kept = ["- item", *RUNNING[:CLOSERS_MAX], RUNNING[-1], "- parent"]
    assert plain_loop(store, separator) == [*kept, CLOSED, "  tail at two"]
    Counted(store, separator)
    assert texts(store) == restored(store) == [*kept, *siblings[:2], "  tail at two"]
