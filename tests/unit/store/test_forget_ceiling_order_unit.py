"""Past the ceiling on its renders, a forget takes with the claim named the claims the body
puts after it that can open a fence (fourth report, must-fix 2); and, in every render, the
claims directly before it that the parser shows holding what follows (CORE9A:
test_forget_siblings_unit.py). No other claim leaves for a fence, however often it renders,
but past the render caps (test_forget_render_cap_unit.py) and at the end, which only a
reader that reads one text two ways reaches (the last test here).

A forget that brings more code fences together than a body closes (spans.CLOSERS_MAX) drops
the claim whose fence runs on and renders again, CLOSERS_MAX times; past that it took the
claim then named and every later claim of the stored list that held a backtick or a tilde.
The body does not follow the stored list: claims with no section are written first. So a
paragraph with inline code, filed last and written first, left a valid page with a note that
was never forgotten, and sixteen fences the body had closed left with a second group.

Each page here is filed through Memory.note, one note at a time, and is one every gate
saves; the forgotten note is a paragraph at the margin that kept indented text apart from
the list item above it. Renders are counted as calls of compiler.left_to_run. Made-up notes."""

import io
import itertools
import random

import pytest
from markdown_it import MarkdownIt

import cairn.compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import CLOSERS_MAX, FenceLeftOpen, open_fence_closer
from cairn.store import Store, erasure

STEPS = "## Steps\n\n"
INLINE = "Ada keeps `port 80` open."
FENCED = "~~~text\nBrook owns the rollback.\n~~~"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    store.create_project("alpha")
    yield store
    store.close()


@pytest.fixture
def memory(store):
    return Memory(store, lint_gate)


def filed(memory: Memory, text: str) -> str:
    answer = memory.note("alpha", text, title="Ops", topic="Ops")
    assert (answer["status"], answer["page"]) == ("saved", "ops"), answer
    return answer["note_id"]


def page(store: Store):
    return store.get_page(store.get_project("alpha").id, "ops")


def texts(store: Store) -> list[str]:
    return [claim.text for claim in page(store).claims]


def snippets(word: str, count: int = 40) -> list[str]:
    return [f"    ~~~\n    {word} {n}" for n in range(count)]


def renders(monkeypatch, run) -> int:
    """How many times `run` renders a body to ask whether a fence runs on in it."""
    calls, real = [], cairn.compiler.left_to_run
    with monkeypatch.context() as patch:
        patch.setattr(
            cairn.compiler, "left_to_run", lambda *a, **k: calls.append(1) or real(*a, **k)
        )
        run()
    return len(calls)


def restored(store: Store, tmp_path) -> list[str]:
    """The page's claims in a store the export was imported into: an import takes it."""
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        assert page(target).claims == page(store).claims
        return texts(target)
    finally:
        target.close()


def one_list(memory: Memory, last: str) -> tuple[str, list[str], str]:
    """A list item, a paragraph at the margin and forty indented snippets under one heading,
    then the note `last`: (the paragraph's note, the snippets, the last note)."""
    blocks = snippets("sample")
    filed(memory, STEPS + "- item")
    separator = filed(memory, STEPS + "Between.")
    filed(memory, STEPS + "\n\n".join(blocks))
    return separator, blocks, filed(memory, last)


# Rendered order, not stored order


def test_claims_the_body_writes_before_the_fence_stay_though_they_were_filed_last(
    store, memory, tmp_path
):
    """The reviewer's page: the paragraph with inline code has no section, so the body
    writes it above the heading the snippets are under. A closed fence beside it stays too:
    what the body puts before the fence that runs on was read by the parser and is whole."""
    separator, blocks, last = one_list(memory, f"{FENCED}\n\n{INLINE}")
    before = page(store)
    assert [claim.text for claim in before.claims[-2:]] == [FENCED, INLINE]
    assert before.body.index(INLINE) < before.body.index("## Steps")
    assert store.forget_note(separator).rebuilt == ("ops",)
    assert texts(store) == ["- item", *blocks[:CLOSERS_MAX], FENCED, INLINE]
    assert restored(store, tmp_path) == texts(store)
    assert store.get_note(last) is not None
    (event,) = [e for e in store.list_audit(100) if e.action == "note.forgotten"]
    assert event.detail["claims_left"] == len(blocks) - CLOSERS_MAX


SAFE_AFTER = {
    "inline code": "The log keeps `short` labels.",
    "tildes in prose": "  The log has ~ marks and `short` labels, even ~~~ in prose.",
    "three backticks in a line": "Quill wraps code in ``` marks.",
    "three backticks, then one more in the line": "```a`b is how Quill writes it.",
    "a fence in a quotation, which ends with it": "> ~~~\n> Quill keeps the log.",
    "an indented quotation": "    > ~~~\n    > Quill keeps the log.",
    "a fenced block at the margin": FENCED,
    "a fence at the margin that its claim leaves open": "~~~text\nBrook owns the rollback.",
    "a list item at the margin that holds a fence": "- ~~~\n  Brook owns the rollback.",
}


@pytest.mark.parametrize("name", list(SAFE_AFTER))
def test_a_claim_after_the_fence_that_can_open_none_there_stays(store, memory, tmp_path, name):
    """Filed last under the same heading, so the body writes it after every snippet: past
    the ceiling it stays. No line of it opens a fence that could run past it; or it starts
    at the margin, where no list item reaches it and it is read as it is alone, closed."""
    separator, blocks, _ = one_list(memory, STEPS + SAFE_AFTER[name])
    assert texts(store)[-1] == SAFE_AFTER[name]
    assert store.forget_note(separator).rebuilt == ("ops",)
    assert texts(store) == ["- item", *blocks[:CLOSERS_MAX], SAFE_AFTER[name]]
    assert restored(store, tmp_path) == texts(store)


def test_what_the_ceiling_still_takes_is_an_indented_claim_after_the_fence_that_can_open_one(
    store, memory, tmp_path
):
    """The cost that is left, exactly: a claim the body writes after the fence that runs
    on, that starts with indentation and has a line that opens a fence, leaves, though here
    its fence would close in the claim. Its note stays stored, and sending it again files
    it on the page."""
    indented = "    ~~~text\n    Brook owns the rollback.\n    ~~~"
    separator, blocks, last = one_list(memory, STEPS + indented)
    assert store.forget_note(separator).rebuilt == ("ops",)
    assert texts(store) == ["- item", *blocks[:CLOSERS_MAX]]
    assert restored(store, tmp_path) == texts(store)
    assert store.get_note(last) is not None
    again = memory.note("alpha", STEPS + indented, title="Ops", topic="Ops")
    assert (again["note_id"], again["created"], again["status"]) == (last, False, "saved")
    assert texts(store) == ["- item", *blocks[:CLOSERS_MAX], indented]


# How many renders


def test_a_forget_of_one_list_renders_eighteen_times_and_asks_once_of_each_line_after(
    store, memory, monkeypatch
):
    """What the ceiling adds to a forget's cost: in the one render past it, one parse of a
    line for each line with three backticks or tildes in a row, of the claims the body
    writes after the claim named. Here that claim is the thirty-third snippet: the seven
    after it, and the line of prose with three tildes."""
    separator, _, _ = one_list(memory, STEPS + SAFE_AFTER["tildes in prose"])
    asked, real = [], erasure._covering_fence
    monkeypatch.setattr(erasure, "_covering_fence", lambda *a: asked.append(a[0]) or real(*a))
    assert renders(monkeypatch, lambda: store.forget_note(separator)) == CLOSERS_MAX + 2
    assert asked == ["~~~"] * 7 + [SAFE_AFTER["tildes in prose"].lstrip()]


def test_two_lists_the_body_writes_against_their_stored_order_cost_eighteen_renders(
    store, memory, tmp_path, monkeypatch
):
    """The page the reviewer asked for: the snippets under the heading are stored before
    those with no section, and the body writes them after. The first removal past the
    ceiling went by the stored list, left the group under the heading whole, and a second
    one then took the sixteen fences of the first group that the body had closed: nineteen
    renders. By the body's order one removal takes both groups' open fences."""
    top, under = snippets("top"), snippets("under")
    filed(memory, STEPS + "- item under")
    filed(memory, "- item")
    separator = filed(memory, f"Between.\n\n{STEPS}Between too.")
    filed(memory, STEPS + "\n\n".join([*under, "  End under."]))
    filed(memory, "\n\n".join([*top, "  End."]))
    before = page(store)
    assert before.body.index(top[0]) < before.body.index(under[0])
    assert texts(store).index(under[0]) < texts(store).index(top[0])
    count = renders(monkeypatch, lambda: store.forget_note(separator))
    assert count == CLOSERS_MAX + 2
    assert texts(store) == ["- item under", "- item", "  End under.", *top[:CLOSERS_MAX], "  End."]
    assert restored(store, tmp_path) == texts(store)


@pytest.mark.parametrize("deep", [1, 2, 8, 9])
def test_fences_that_ended_with_their_list_items_leave_in_the_render_that_uncovers_them(
    store, memory, tmp_path, monkeypatch, deep
):
    """Under a first list item, `deep` items nested one in another with a fence in each that
    ends where its item ends (the next block is indented less), then the snippets, then
    indented words. Once the snippets left, the words follow the fence of the innermost
    item and that fence covers them, and then the fence of the item around it. That was a
    render for each such item. They leave with the claim named in the seventeenth render,
    since the parser, asked with the words after each, shows every one of them holding the
    words (erasure._before_it); no claim that is whole is taken for it. Eight of them is
    the most: the parser reads no fence in a list nested deeper, and then nothing runs on."""
    first = [f"    ~~~\n    first {n}" for n in range(CLOSERS_MAX + 1)]
    items = [f"{' ' * 4 * level}-   item {level}" for level in range(deep + 1)]
    ended = [f"{' ' * 4 * level}~~~\n{' ' * 4 * level}ended" for level in range(deep + 1, 1, -1)]
    blocks, words = snippets("sample", 20), f"{' ' * 4 * (deep + 1)}plain words"
    filed(memory, "- item")
    for text in (*items, *ended):
        filed(memory, STEPS + text)
    separator = filed(memory, f"Between.\n\n{STEPS}Between too.")
    filed(memory, "\n\n".join(first))
    filed(memory, STEPS + "\n\n".join([*blocks, words]))
    count = renders(monkeypatch, lambda: store.forget_note(separator))
    if deep <= 8:
        assert texts(store) == ["- item", *items, *first, words]
        # CORE9A: it was CLOSERS_MAX + 2 + deep, a render for each nested item
        assert count == CLOSERS_MAX + 2
    else:
        assert texts(store) == ["- item", *items, *ended, *first, *blocks, words]
        assert count == 1
    assert restored(store, tmp_path) == texts(store)


def test_a_reader_that_still_names_a_claim_after_the_pages_class_meets_the_end(
    store, memory, monkeypatch
):
    """The end, forced (the tenth round: a bound by a number, not by an argument). No page
    is known that renders so often, so one is made up: a body reader that names the claim
    before the last for as long as more than twelve are left, a paragraph with inline code
    in which the parser reads no fence (one text read two ways). Each render takes the
    claim named; the first past sixteen takes with it the one claim after it that can open
    a fence; the render after RENDERS_HARD every claim of the page that can open one (the
    indented example with its own closing line); and the next, which still names a claim,
    is the end: every claim with a backtick or a tilde leaves (inline code, a fenced block
    at the margin, which the plain loop keeps), and the forget renders once more to write
    the page and stops. RENDERS_HARD + 3 renders. CORE9A took every such claim past twice
    the ceiling, and ordinary notes came to that; the end comes only after the page's
    class, which leaves no claim that can open a fence (test_forget_render_cap_unit.py)."""
    indented = "    ~~~text\n    Brook owns the rollback.\n    ~~~"
    safe = [INLINE, FENCED, "- item", indented, "Ada keeps `port 22` shut."]
    coded = [f"Ada keeps `log {n}`." for n in range(50)]
    filed(memory, "\n\n".join(safe[:3]))
    filed(memory, indented)
    filed(memory, "\n\n".join([safe[4], *coded]))
    last = "    ~~~text\n    Quill owns the log.\n    ~~~"
    filed(memory, last)
    other = filed(memory, "Fin.")
    assert texts(store) == [*safe, *coded, last, "Fin."]
    real, taken = cairn.compiler.left_to_run, []

    def named_before_the_last(title, claims, spans):
        body, _ = real(title, claims, spans)
        taken.append(len(claims))
        return body, len(claims) - 1 if len(claims) > 12 else None

    with monkeypatch.context() as patch:
        patch.setattr(cairn.compiler, "left_to_run", named_before_the_last)
        store.forget_note(other)
    hard = erasure.RENDERS_HARD
    assert len(taken) == hard + 3
    # one claim a render, but the first past the ceiling (the claim after it too) and the
    # page's (the indented example), then the end
    steps = [before - after for before, after in itertools.pairwise(taken)]
    assert steps[:hard] == [1] * CLOSERS_MAX + [2] + [1] * (hard - CLOSERS_MAX - 1)
    assert steps[hard:] == [2, taken[hard + 1] - 1]
    assert texts(store) == ["- item"]


# Which claims can open a fence: the parser's answer, held to the parser's reading of bodies

READER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the package reads
BEFORE = (
    [],
    ["- item"],
    ["-   item"],
    ["-\titem"],
    ["1. item"],
    ["10. item"],
    ["- item", "  - deeper"],
    ["-   a", "    -   b", "        -   c"],
    ["> said"],
    ["- > said"],
)
AFTER = ("next", "  next", "    next", "            next", "\tnext", "- next")
PARTS = (
    ["", " ", "  ", "   ", "    ", "      ", "\t", " \t", "        "],  # indentation
    ["", "", "- ", "-   ", "-\t", "-\t  ", "1. ", "12) ", "> ", ">", ">\t", "* ", "> - ", "- > "],
    ["`", "``", "```", "````", "~", "~~", "~~~", "~~~~", "words", "| a | b |", "--- | ---"],
    ["", "", " info", " a`b", " | b", "``` tail", " ~~~", "\r~~~", "\u2028~~~"],  # after the run
)


def some_claim(rng: random.Random) -> str:
    return "\n".join(
        "".join(rng.choice(part) for part in PARTS) for _ in range(rng.choice((1, 1, 2, 3)))
    )


def runs_past(blocks: list[str], at: int) -> bool:
    """Whether the parser, reading the body `blocks` make, has a code fence that starts in
    block `at` cover the first line of the block after it."""
    starts, line = [], 0
    for block in blocks:
        starts.append(line)
        line += len(block.replace("\r\n", "\n").replace("\r", "\n").split("\n")) + 1
    return any(
        token.type == "fence"
        and starts[at] <= token.map[0] < starts[at + 1]
        and token.map[1] > starts[at + 1]
        for token in READER.parse("\n\n".join(blocks) + "\n")
    )


def test_a_claim_said_to_open_no_fence_opens_none_that_runs_past_it_in_any_body():
    """60,000 generated claims, each written as a body writes it (closed where alone it
    leaves a fence open), after a list item, nested ones, a quotation or nothing, and before
    a block at one of six indentations: whenever the Markdown parser reads a fence that
    starts in the claim and covers the next block, `_may_open_a_fence` said it could. Were
    it wrong once, a note's author could make a forget cost a render for that claim. Half
    the claims start at the margin: none of those runs past, with a fence in thousands;
    of the others, some hundreds do."""
    rng = random.Random(20261120)
    ran_past, said_no, fenced_at_the_margin = 0, 0, 0
    for n in range(60_000):
        claim = some_claim(rng)
        if n % 2:
            claim = claim.lstrip(" \t\r\n") or "~~~"
        try:
            closer = open_fence_closer(claim)
        except FenceLeftOpen:  # no line closes it: the import's check refuses the claim
            continue
        written = claim if closer is None else f"{claim}\n{closer}"  # as compiler._closed
        blocks = ["# Ops", *rng.choice(BEFORE), written, rng.choice(AFTER)]
        may = erasure._may_open_a_fence(claim)
        said_no += not may
        if n % 2:
            assert not may
            fenced_at_the_margin += any(t.type == "fence" for t in READER.parse(written))
        if runs_past(blocks, len(blocks) - 2):
            ran_past += 1
            assert may, (blocks, claim)
    # both answers are asked for, many times
    assert ran_past > 400 and said_no > 20_000 and fenced_at_the_margin > 6000


@pytest.mark.parametrize(
    ("text", "may"),
    [
        ("    ~~~\n    sample", True),  # indented text alone, a fence after a list item
        ("    ~~~text\n    closed\n    ~~~", True),  # the fence would close: still True
        ("\t```", True),
        (" - ~~~", True),
        ("\n~~~", True),  # a first line that is empty is not one at the margin
        ("~~~text\nclosed\n~~~", False),  # starts at the margin: as it is alone, closed
        ("~~~text\nleft open", False),  # the body writes its closer after it
        ("- ~~~", False),
        ("Ada keeps `port 80` open.", False),
        ("even ~~~ in prose", False),
        ("```a`b", False),  # a backtick after the run: the parser reads no fence
        ("> ~~~", False),  # ends with its quotation, at the blank line after the claim
        ("~~", False),
    ],
)
def test_which_claims_can_open_a_fence(text, may):
    assert erasure._may_open_a_fence(text) is may
