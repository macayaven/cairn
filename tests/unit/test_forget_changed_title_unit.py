"""Claims leave a page together only when the body the forget will write without them is
the body it asked about (sixth report, must-fix 1; CORE9A, seventh round).

A forget titles the page again in every render, with the topic of the oldest note the
claims left still cite, and a claim under a heading that says the title is written with no
heading. So taking one claim can change the title, and with it where the body writes other
claims: a heading comes back and stands between a fence and the tail it would have covered.
The claims before the one named were chosen on the body under the old title
(erasure._before_it), and so were the later claims a forget takes past its ceiling
(erasure._may_open_a_fence); on the first render of a page whose notes spell one topic two
ways, a claim left that the plain loop keeps.

Now what would leave with the claim named is checked against each body the plain loop
would render on the way: the claims leave in the order it would name them, and each must be
named under the title the question was asked under (its text: another note that spells it
the same changes nothing) with the blocks up to the one that follows them as asked. Where
the title changes at a step, the claims before that step leave together and the next render
starts from there (decision 1 of the eighth round, RT1's finding: holding to the note and not
to its spelling cost a render for each claim of a run stored in reverse).

The oldest note is filed after its neighbours here as two writers would interleave: its
gate files the others, and its own save, found stale, is made again. The plain loop beside
each forget titles the page again in every render, as the forget does. Made-up notes."""

from contextlib import closing

import pytest
from test_guide_facts_slack_unit import says

from cairn import compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import CLOSERS_MAX
from cairn.store import Store

BEFORE = "    - ~~~\n      Brook keeps the almanac."  # the claim before the one named
NAMED = "    - ~~~\n      Quill keeps the compass."
TAIL = "      Tail."
EXAMPLE = "      ```text\n      Ada keeps the gate.\n      ```"  # closed where it stands
CHECKS = "## What the checks on pages catch"  # the section of the security guide
TITLED = (  # the guide's sentence that the tests of both its halves run
    "A forget titles the page by the oldest note still on it, and a heading that says the title"
    " is not written, so taking claims can change where the page writes the others: of the"
    " claims above, only as many leave at once as would each be at fault in turn with the"
    " page's title unchanged, another note that spells it the same way changing nothing, and"
    " the forget reads the page again from the first claim whose going would change it."
)


@pytest.fixture
def desk():
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        yield Memory(store, lint_gate)


def filed(desk: Memory, text: str, topic: str = "OPS", *, first=None) -> str:
    """The note's id, filed on the page `ops`. With `first`, a function that files other
    notes, those are filed while this one is at its gate: this note is the older, and its
    claims stand after theirs."""
    waiting = [first] if first else []

    def gate(candidate, spans):
        if waiting:
            waiting.pop()()
        return lint_gate(candidate, spans)

    answer = Memory(desk.store, gate).note("alpha", text, title="Field notes", topic=topic)
    assert (answer["status"], answer["page"]) == ("saved", "ops"), answer
    return answer["note_id"]


def a_list_that_spends_the_closers(desk: Memory, more: int = 0, topic: str = "OPS") -> str:
    """A list whose fences run on CLOSERS_MAX + `more` times once the paragraph at the
    margin is forgotten: the body closes sixteen, and each of the others is named in its
    turn. The paragraph's note."""
    filed(desk, "- first", topic)
    separator = filed(desk, "Between.", topic)
    running = [f"    ~~~\n    first {n}" for n in range(CLOSERS_MAX + more)]
    filed(desk, "\n\n".join([*running, "  End of first."]), topic)
    return separator


def page(store: Store):
    return store.get_page(store.get_project("alpha").id, "ops")


def plain_loop(store: Store, forgotten: str) -> tuple[list[tuple[str, str]], str, tuple]:
    """The loop with no ceiling and no shortcut, the page titled again in every render:
    (each title with the claim it named under it, the last title, the claims left)."""
    notes = store.list_notes(store.get_project("alpha").id)  # the oldest stored first
    claims = page(store).claims
    spans = store.get_spans(i for claim in claims for i in claim.span_ids)
    left = [c for c in claims if all(spans[i].source_id != forgotten for i in c.span_ids)]
    named = []
    while True:
        cited = {spans[i].source_id for claim in left for i in claim.span_ids}
        oldest = next(note for note in notes if note.id in cited)
        title = oldest.topic or oldest.title
        place = compiler.left_to_run(title, left, spans)[1]
        if not place:
            return named, title, tuple(left)
        named.append((title, left.pop(place - 1).text))


def forgotten(desk: Memory, note_id: str) -> list[str]:
    """The title of each render of the forget."""
    titles, render = [], compiler.left_to_run
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(compiler, "left_to_run", lambda t, *a: titles.append(t) or render(t, *a))
        assert desk.forget("alpha", note_id)["rebuilt"] == ["ops"]
    return titles


def found(desk: Memory, word: str) -> list[str]:
    return [hit["text"] for hit in desk.recall(word, project="alpha")["hits"]]


def test_the_claim_before_stays_when_taking_the_one_named_changes_the_title(desk):
    """The reviewer's page in other words: seven notes, 23 claims, the topic spelled `Ops`
    by the oldest note and `OPS` by the others. Under `Ops` the tail, whose heading says
    Ops, is written with no heading, after the claim named. That claim is the oldest
    note's only one: without it the page is titled OPS, the heading `## Ops` is written
    before the tail, and the claim before, whose fence would have covered the tail, covers
    nothing. It stays, and it is found."""

    def others():
        nonlocal separator
        separator = a_list_that_spends_the_closers(desk)
        filed(desk, "- parent")
        filed(desk, BEFORE)

    separator = None
    filed(desk, NAMED, "Ops", first=others)
    filed(desk, f"## Ops\n\n{TAIL}")
    assert len(page(desk.store).claims) == 23
    named, title, claims = plain_loop(desk.store, separator)
    assert (named, title, len(claims)) == ([("Ops", NAMED)], "OPS", 21)
    assert forgotten(desk, separator) == ["Ops", "OPS"]
    after = page(desk.store)
    assert (after.title, after.claims) == (title, claims)
    assert BEFORE in [claim.text for claim in claims] and found(desk, "almanac") == [BEFORE]
    assert "\n## Ops\n\n      Tail.\n" in after.body


def test_the_claims_before_leave_in_the_next_render_when_the_new_title_changes_nothing_else(desk):
    """One step past: the tail has no heading, so the body under OPS is the body under Ops
    but for its first line. The claim named still leaves alone, since the title is not the
    one the question was asked under; the next render names the claim before it and asks
    again, and the five before leave together. Three renders, and the plain loop's claims."""
    before = [f"    - ~~~\n      Brook keeps almanac {n}." for n in range(5)]

    def others():
        nonlocal separator
        separator = a_list_that_spends_the_closers(desk)
        filed(desk, "- parent")
        filed(desk, "\n\n".join(before))

    separator = None
    filed(desk, NAMED, "Ops", first=others)
    filed(desk, TAIL)
    named, title, claims = plain_loop(desk.store, separator)
    assert named == [("Ops", NAMED), *(("OPS", text) for text in reversed(before))]
    assert forgotten(desk, separator) == ["Ops", "OPS", "OPS"]
    after = page(desk.store)
    assert (after.title, after.claims) == (title, claims)


def test_a_title_that_would_come_back_does_not_let_the_claim_before_leave(desk):
    """One step past again: the plain loop passes through every body between. The claim
    named is the oldest note's (`Ops`), the claim before it the second oldest's (`ops`),
    and every other note says `Ops`. Without both, the page is titled Ops again, as when
    the question was asked. But without the claim named alone it is titled ops: the
    heading `## Ops` stands before the tail, and the claim before covers nothing. So the
    title is compared after each claim that would leave, never once after all of them."""

    def others():
        nonlocal separator
        separator = a_list_that_spends_the_closers(desk, topic="Ops")
        filed(desk, "- parent", "Ops")

    separator = None
    filed(desk, NAMED, "Ops", first=lambda: filed(desk, BEFORE, "ops", first=others))
    filed(desk, f"## Ops\n\n{TAIL}", "Ops")
    named, title, claims = plain_loop(desk.store, separator)
    assert (named, title, len(claims)) == ([("Ops", NAMED)], "ops", 21)
    assert forgotten(desk, separator) == ["Ops", "ops"]
    after = page(desk.store)
    assert (after.title, after.claims) == (title, claims)
    assert found(desk, "almanac") == [BEFORE]


SIX = [f"    - ~~~\n      Brook keeps almanac {n}." for n in range(6)]  # the last is named first


@pytest.mark.parametrize(("oldest", "titles"), [(5, "ABB"), (2, "ABB"), (0, "AB")])
def test_the_nearest_claims_that_leave_the_body_as_asked_go_together(desk, oldest, titles):
    """One step past: six siblings, one of them the oldest note's only claim, and no heading
    that says the title. Not all or none: of the claims before the one named, the nearest
    leave with it for as far as the plain loop names them under the old title, the oldest
    note's claim included, since the plain loop names it while it still gives the title;
    the rest leave under the new title. Two renders or three (three or four while the rule
    held to the note after the whole batch), and the plain loop's claims, wherever in the
    run the claim stands."""

    def others():
        nonlocal separator
        separator = a_list_that_spends_the_closers(desk)
        filed(desk, "- parent")
        if oldest:
            filed(desk, "\n\n".join(SIX[:oldest]))

    separator = None
    filed(desk, SIX[oldest], "Ops", first=others)
    if oldest < 5:
        filed(desk, "\n\n".join(SIX[oldest + 1 :]))
    filed(desk, TAIL)
    named, title, claims = plain_loop(desk.store, separator)
    assert [text for _, text in named] == SIX[::-1] and title == "OPS"
    assert forgotten(desk, separator) == [{"A": "Ops", "B": "OPS"}[t] for t in titles]
    after = page(desk.store)
    assert (after.title, after.claims) == (title, claims)


def test_a_run_that_is_all_of_the_oldest_note_leaves_in_one_render(desk):
    """And the input that makes all-or-none cost a render for each claim: forty siblings in
    one note, the oldest. Taking them all changes the title, but the plain loop names each
    of them while the note still holds one, under the old title: so all forty leave in the
    first render (thirty-nine, and the fortieth alone, while the rule held to the note after
    the whole batch), and a second render under the new title finds nothing."""
    siblings = [f"    - ~~~\n      Brook keeps almanac {n}." for n in range(40)]

    def others():
        nonlocal separator
        separator = a_list_that_spends_the_closers(desk)
        filed(desk, "- parent")

    separator = None
    filed(desk, "\n\n".join(siblings), "Ops", first=others)
    filed(desk, TAIL)
    named, title, claims = plain_loop(desk.store, separator)
    assert len(named) == 40 and title == "OPS"
    assert forgotten(desk, separator) == ["Ops", "OPS"]
    after = page(desk.store)
    assert (after.title, after.claims) == (title, claims)


def test_a_run_of_notes_each_older_than_the_one_before_it_leaves_in_one_render(desk):
    """What comparing the note, and not the spelling, cost (RT1). Eighteen siblings, each a
    note of its own, the nearest the tail the oldest and so on back; every note spells the
    topic alike, so the title never changes and neither does the body but for the claims
    that leave. Each claim named was the last of the note that gave the title then, so it
    left alone: nineteen renders. The title's text is compared now, so the eighteen leave
    in the first render and a second finds nothing, as before that rule. The claims left
    are the plain loop's. And one note that holds a whole run, spelled like the rest, takes
    two renders again (it took three)."""
    siblings = [f"    - ~~~\n      Brook keeps almanac {n}." for n in range(CLOSERS_MAX + 2)]

    def others():
        nonlocal separator
        separator = a_list_that_spends_the_closers(desk)
        filed(desk, "- parent")

    def before(place: int) -> None:  # the sibling there, older than those written before it
        if place < 0:
            others()
        else:
            filed(desk, siblings[place], first=lambda: before(place - 1))

    separator = None
    before(len(siblings) - 1)
    filed(desk, TAIL)
    named, title, claims = plain_loop(desk.store, separator)
    assert [text for _, text in named] == siblings[::-1] and title == "OPS"
    assert forgotten(desk, separator) == ["OPS"] * 2
    after = page(desk.store)
    assert (after.title, after.claims) == (title, claims)


def test_a_run_in_the_oldest_note_spelled_like_the_rest_takes_two_renders(desk):
    siblings = [f"    - ~~~\n      Brook keeps almanac {n}." for n in range(40)]

    def others():
        nonlocal separator
        separator = a_list_that_spends_the_closers(desk)
        filed(desk, "- parent")

    separator = None
    filed(desk, "\n\n".join(siblings), first=others)
    filed(desk, TAIL)
    named, title, claims = plain_loop(desk.store, separator)
    assert len(named) == 40 and title == "OPS"
    assert forgotten(desk, separator) == ["OPS"] * 2
    after = page(desk.store)
    assert (after.title, after.claims) == (title, claims)


def test_a_title_that_changes_inside_the_run_and_comes_back_at_its_end_stops_the_run_there(
    desk,
):
    """Six siblings. The claim named is the oldest note's (`Ops`), the one before it the
    second oldest's (`Ops`), the one before that the third oldest's (`ops`), and the notes
    of the rest of the page say `Ops`; the tail stands under `## Ops`. Without the six the
    page is titled Ops again, as when the question was asked. The plain loop names the two
    nearest under Ops; without them the page is titled ops, `## Ops` is written before the
    tail, and nothing more is at fault. So the forget compares the title at each step: the
    two leave in the first render, and the second, titled ops, finds nothing."""

    def others():
        nonlocal separator
        separator = a_list_that_spends_the_closers(desk, topic="Ops")
        filed(desk, "- parent", "Ops")
        filed(desk, "\n\n".join(SIX[:3]), "Ops")

    def third():
        filed(desk, SIX[3], "ops", first=others)

    separator = None
    filed(desk, SIX[5], "Ops", first=lambda: filed(desk, SIX[4], "Ops", first=third))
    filed(desk, f"## Ops\n\n{TAIL}", "Ops")
    named, title, claims = plain_loop(desk.store, separator)
    assert (named, title) == ([("Ops", SIX[5]), ("Ops", SIX[4])], "ops")
    assert forgotten(desk, separator) == ["Ops", "ops"]
    after = page(desk.store)
    assert (after.title, after.claims) == (title, claims)
    assert set(SIX[:4]) <= {claim.text for claim in claims}
    says("security", CHECKS, TITLED)


def test_past_the_ceiling_a_later_claim_stays_when_taking_the_one_named_changes_the_title(desk):
    """The same condition for the class a forget takes past its ceiling. Sixteen fences
    are named one by one; the seventeenth is the oldest note's claim, whose fence covers
    an indented example with its own closing line. Under the old title the example is a
    later claim that can open a fence, and it left. Without the claim named the page is
    titled OPS, the example stands under `## Ops` and is whole: it stays, as in the plain
    loop."""

    def others():
        nonlocal separator
        separator = a_list_that_spends_the_closers(desk, CLOSERS_MAX)
        filed(desk, "- parent")
        filed(desk, BEFORE)

    separator = None
    filed(desk, NAMED, "Ops", first=others)
    filed(desk, f"## Ops\n\n{EXAMPLE}\n\n{TAIL}")
    named, title, claims = plain_loop(desk.store, separator)
    assert named[CLOSERS_MAX:] == [("Ops", NAMED)] and title == "OPS"
    assert forgotten(desk, separator) == ["Ops"] * (CLOSERS_MAX + 1) + ["OPS"]
    after = page(desk.store)
    assert (after.title, after.claims) == (title, claims)
    assert {BEFORE, EXAMPLE, TAIL} <= {claim.text for claim in claims}
    says("security", CHECKS, TITLED)


def test_past_the_ceiling_only_as_many_later_claims_leave_as_keep_the_title(desk):
    """And with the claim named another note's: the oldest note holds three indented
    examples after the list, each closed where it stands. Past the ceiling they are later
    claims that can open a fence; all three gone, another note would give the title. So
    the two nearest leave and the third stays, with the title; the next render finds
    nothing. The plain loop keeps all three: the two are the declared class."""
    examples = [f"    ```text\n    Ada keeps gate {n}.\n    ```" for n in range(3)]

    def others():
        nonlocal separator
        separator = a_list_that_spends_the_closers(desk, CLOSERS_MAX + 1)

    separator = None
    filed(desk, "\n\n".join(examples), "Ops", first=others)
    named, title, claims = plain_loop(desk.store, separator)
    assert len(named) == CLOSERS_MAX + 1 and title == "Ops"
    assert set(examples) <= {claim.text for claim in claims}
    assert forgotten(desk, separator) == ["Ops"] * (CLOSERS_MAX + 2)
    after = page(desk.store)
    assert after.title == "Ops"
    assert [c.text for c in claims if c not in after.claims] == examples[:2]
    says("security", CHECKS, TITLED)


def test_a_new_title_can_uncover_a_fence_so_no_number_of_renders_is_promised(desk):
    """Nineteen renders, on ordinary notes (the reviewer's second page in other words;
    eighteen was the most seen until then). Sixteen fences are named one by one and the
    seventeenth claim is the oldest note's. Without it the page is titled OPS, and a tail
    whose heading says OPS, written under that heading until then, is written with none,
    after a claim whose fence covers it: an eighteenth claim to name. What ends the loop
    is only that a render which names a claim takes it."""

    def others():
        nonlocal separator
        separator = a_list_that_spends_the_closers(desk, CLOSERS_MAX)
        filed(desk, "- earlier parent")
        filed(desk, BEFORE)
        filed(desk, "## OPS\n\n      Earlier tail.")
        filed(desk, "- later parent")

    separator = None
    filed(desk, NAMED, "Ops", first=others)
    filed(desk, TAIL, "Ops")
    named, title, claims = plain_loop(desk.store, separator)
    assert named[CLOSERS_MAX:] == [("Ops", NAMED), ("OPS", BEFORE)]
    titles = forgotten(desk, separator)
    assert titles == ["Ops"] * (CLOSERS_MAX + 1) + ["OPS"] * 2 and len(titles) == 19
    after = page(desk.store)
    assert (after.title, after.claims) == (title, claims)
    says(
        "security",
        CHECKS,
        "No number of readings is promised: a forget ends because each reading takes at least"
        " the claim at fault, and a title that changes can uncover another.",
    )
