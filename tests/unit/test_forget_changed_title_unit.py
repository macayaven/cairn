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

Now what would leave with the claim named is checked against what the forget will really
write: the note that gives the claims left their title, and their blocks up to the one that
follows the claims taken. Of the claims that would leave, the nearest to the claim named
do, as many as leave the body the one that was asked about; where not one does, the claim
named leaves alone. The next render asks again, of the body as it then is.

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
    " claims above, only as many leave at once as leave that note on the page, the claim at"
    " fault alone where it is that note's last, and the forget reads the page again as it then"
    " is."
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
    heading `## Ops` stands before the tail, and the claim before covers nothing. So what
    must stay the same is the note that gives the title, which does not come back once it
    has left, and not the title's spelling."""

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


@pytest.mark.parametrize(("oldest", "titles"), [(5, "ABB"), (2, "AABB"), (0, "AAB")])
def test_the_nearest_claims_that_leave_the_body_as_asked_go_together(desk, oldest, titles):
    """One step past: six siblings, one of them the oldest note's only claim, and no heading
    that says the title. Not all or none: of the claims before the one named, the nearest
    leave with it for as far as the title stays (those nearer the tail than the oldest
    note's claim); that claim is then named and leaves alone, since the title goes with it;
    the rest leave under the new title. Three renders or four, and the plain loop's claims,
    wherever in the run the claim stands."""

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


def test_a_run_that_is_all_of_the_oldest_note_leaves_but_for_its_last_claim(desk):
    """And the input that makes all-or-none cost a render for each claim: forty siblings in
    one note, the oldest. Taking them all changes the title, so thirty-nine leave with the
    title as it is, the fortieth is named and leaves alone, and a third render under the
    new title finds nothing."""
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
    assert forgotten(desk, separator) == ["Ops", "Ops", "OPS"]
    after = page(desk.store)
    assert (after.title, after.claims) == (title, claims)


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
