"""A filed Slack message costs work in proportion to its length (the third tester, finding
9), and the conversion runs on a worker thread, never on the server's event loop.

What is counted, not timed: every reader (the Markdown parser and the passage splitter),
every compiled pattern of cairn.slack.text and every search of its own (_Next, and the
digests of _Hashes where there are any) is wrapped, and the characters each call is run
over are added up (a search or a match: up to where it ends, or the end it was given when
it finds nothing); 40,000 characters of a shape may cost at most 10 times what 5,000 do (8
is linear, 64 quadratic). That cannot see a pattern that reads its input more than once
inside one call, so each pattern is also run alone over the input that would make it do so,
and timed: the best of three tries at 40,000 characters may take at most 24 times the best
of three at 5,000. The shapes are the tester's ("a" and "<" repeated) and these: each
character a pattern repeats, a quotation's marks before a code block, many code blocks on
one line, headings, underlines and table rows. Every message is made up."""

import asyncio
import re
import threading
import time

import pytest
import test_slack_shortcuts_unit as shortcuts
from test_slack_commands_unit import ADA_SLACK

from cairn.slack import text as slack_text
from cairn.slack.text import note_from_message

SMALL, LARGE = 5_000, 40_000
SHAPES = {
    "address": "a",  # the tester's: a run an address's first part may be
    "angle": "<",  # the tester's: "<" with no ">" after it
    "star": "*",
    "tilde": "~",
    "plus": "a+",
    "dots": "a.b",
    "underscores": "_a",
    "bracketed domain": "a@[",
    "quoted name": '"a',
    "ampersand": "&amp;",
    "backtick": "`",
    "backtick word": "`a",
    "angle word": "<a",
    "fences": "```",
    "blocks on one line": "```a```",
    "quoted block": "&gt; ",  # then a code block on the same line
    "headings": "# a\n",
    "underlines": "a\n===\n",
    "rules": "-\n",
    "table": "|a|\n|-|\n",
    "indented": "a\n    b\n",
    "brackets": "[",
    "mentions": "<@U0CEDAR01>",
    "hashes": "#",  # a title's closing run (_CLOSING)
    "list marks": "- ",  # what may stand before a fence of tildes (_FENCE_LEAD)
    "escaped brackets": "\\[",  # a title's escapes (_ATOM)
    "bold words": "*a* ",  # a title no cut keeps
    "open marks": "*a _b ~c ",  # marks that open and find no closer (_paired)
    "marks in words": "a*b_c~d ",  # marks that neither open nor close
}


def message_of(shape: str, size: int) -> str:
    unit = SHAPES[shape]
    text = (unit * (size // len(unit) + 1))[:size]
    return text + "x```a```" if shape == "quoted block" else text


def convert(text: str):
    message = {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text}
    return note_from_message(
        message, team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    )  # fmt: skip


class Counted:
    """A compiled pattern, or a reader, that adds up the characters each call is run over."""

    def __init__(self, wrapped, count: list[int]) -> None:
        self.wrapped, self.count = wrapped, count

    def _region(self, string, pos=0, endpos=None) -> None:
        self.count[0] += (len(string) if endpos is None else min(endpos, len(string))) - pos

    def __getattr__(self, name):
        found = getattr(self.wrapped, name)
        if name in ("match", "search"):  # read at least to where the match ends

            def first(string, pos=0, endpos=None):
                end = len(string) if endpos is None else min(endpos, len(string))
                got = found(string, pos, end)
                self.count[0] += (got.end() if got else end) - pos
                return got

            return first
        if name in ("fullmatch", "finditer", "findall"):

            def call(string, pos=0, endpos=None):
                self._region(string, pos, endpos)
                return found(string, pos) if endpos is None else found(string, pos, endpos)

            return call
        if name in ("sub", "subn", "split"):

            def replace(*args, **kwargs):
                self._region(args[1] if name != "split" else args[0])
                return found(*args, **kwargs)

            return replace
        if name == "parse":

            def parse(text, *args, **kwargs):
                self._region(text)
                return found(text, *args, **kwargs)

            return parse
        return found


@pytest.fixture
def counted(monkeypatch):
    count = [0]
    for name, value in list(vars(slack_text).items()):
        if isinstance(value, re.Pattern):
            monkeypatch.setattr(slack_text, name, Counted(value, count))
    monkeypatch.setattr(slack_text, "_PARSER", Counted(slack_text._PARSER, count))
    split = slack_text.chunk

    def chunk(source_id, text):
        count[0] += len(text)
        return split(source_id, text)

    monkeypatch.setattr(slack_text, "chunk", chunk)

    class Next(slack_text._Next):
        def __call__(self, at: int) -> int:
            was, found = (self.since, self.found), super().__call__(at)
            if (self.since, self.found) != was:  # it searched, from `at` to `found`
                count[0] += min(found + len(self.what), self.end) - at
            return found

    monkeypatch.setattr(slack_text, "_Next", Next)
    if hasattr(slack_text, "_Hashes"):

        class Hashes(slack_text._Hashes):
            def __init__(self, text: str) -> None:
                count[0] += len(text)
                super().__init__(text)

            def of(self, way: str) -> int:
                count[0] += len(way)
                return super().of(way)

            def holds(self, way: str, at: int, value: int) -> bool:
                count[0] += 1
                return super().holds(way, at, value)

        monkeypatch.setattr(slack_text, "_Hashes", Hashes)
    return count


@pytest.mark.parametrize("shape", SHAPES)
def test_what_the_readers_and_the_patterns_are_run_over_grows_with_the_message(counted, shape):
    spent = {}
    for size in (SMALL, LARGE):
        counted[0] = 0
        convert(message_of(shape, size))
        spent[size] = counted[0]
    assert spent[LARGE] <= 10 * spent[SMALL], spent


def rich(shape: str, size: int) -> dict:
    """A message of code blocks that its rich text names, as Slack sends one: the tester's
    second attack, finding 6 (each block was looked for in four ways, three of which read to
    the text's end)."""
    unit = {"same": "```x```", "distinct": "```{}```", "lines": "```\n{}\n```"}[shape]
    shown = [str(n) if "{" in unit else "x" for n in range(size // len(unit.format(0)))]
    text = "".join(unit.format(code) for code in shown)
    named = [
        {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": code}]}
        for code in shown
    ]
    blocks = [{"type": "rich_text", "elements": named}]
    return {"type": "message", "ts": "1900000000.000100", "text": text, "blocks": blocks}


@pytest.mark.parametrize("shape", ["same", "distinct", "lines"])
def test_code_blocks_rich_text_names_cost_what_the_message_is_long(counted, shape):
    spent = {}
    for size in (SMALL, LARGE):
        counted[0] = 0
        draft = note_from_message(
            rich(shape, size), team_id="T0FABLE01", team_domain="fable-works",
            conversation_id="C0LIBRARY1", author="Cedar Quill", names={},
        )  # fmt: skip
        spent[size] = counted[0]
    assert draft.text.count("```") >= 2 * (LARGE // 12), draft.text[:300]  # all of them code
    assert spent[LARGE] <= 10 * spent[SMALL], spent


@pytest.fixture
def compared(monkeypatch):
    """The characters _Hashes compares in full, one by one, wherever placing asks it to: its
    text counts what each `startswith` on it is run over."""
    count = [0]

    class Compared(str):
        def startswith(self, prefix, start=0, *args):
            count[0] += min(len(prefix), max(len(self) - start, 0))
            return super().startswith(prefix, start, *args)

    class Hashes(slack_text._Hashes):
        def __init__(self, text: str) -> None:
            super().__init__(text)
            self.text = Compared(self.text)

    monkeypatch.setattr(slack_text, "_Hashes", Hashes)
    return count


def placed(text: str, elements: list[dict]) -> dict:
    blocks = [{"type": "rich_text", "elements": [
        {"type": "rich_text_section", "elements": elements}
    ]}]  # fmt: skip
    return {"type": "message", "ts": "1900000000.000100", "text": text, "blocks": blocks}


def code(text: str) -> dict:
    return {"type": "text", "text": text, "style": {"code": True}}


def plain(text: str) -> dict:
    return {"type": "text", "text": text}


PLACED = {
    # The second reviewer's eighth report, must-fix 4: a named span of `size` backticks right
    # after a span the rich text leaves out, the text before it `size` characters long, and a
    # run of backticks where the span's way stands at each place that text could reach (each
    # place's full comparison succeeded, then the text before it was asked and did not stand).
    "after a span left out": lambda size: placed(
        "y" * size + "`k`" + "`" * (2 * size + 2),
        [plain("y" * size), code("`" * size), plain("`" * size)],
    ),
    "a run as long as the message": lambda size: placed(
        "`" * 3 * size, [code("`" * (3 * size - 2))]
    ),
    "the same text before each span": lambda size: placed(
        " `x`" * size, [element for _ in range(size) for element in (plain(" "), code("x"))]
    ),
    "a text before at every place": lambda size: placed(
        "````" * size, [element for _ in range(size) for element in (plain("`"), code("`"))]
    ),
}


@pytest.mark.parametrize("size", [32, 64, 128, 1024])
@pytest.mark.parametrize("shape", PLACED)
def test_placing_spans_compares_at_most_a_constant_times_the_message(compared, shape, size):
    """What placing compares in full grows with the message, not as its square (on the
    reviewer's shape, 3,570, 13,266, 51,090 and 3,161,106 characters for texts of 101, 197,
    389 and 3,077): a full comparison runs only where both digests agree, so where it
    succeeds or at a collision, within each span's own stretch of the text. The note's body,
    the message as displayed and the title are each placed once."""
    sent = PLACED[shape](size)
    note_from_message(
        sent, team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    )  # fmt: skip
    assert compared[0] <= 6 * len(sent["text"]), (len(sent["text"]), compared[0])


def best_of_three(work, size: int) -> float:
    times = []
    for _ in range(3):
        start = time.process_time()
        work(size)
        times.append(time.process_time() - start)
    return min(times)


def linear(work) -> tuple[float, float]:
    """The best of three tries at LARGE and at SMALL; three rounds of it, the first that
    shows a ratio within 24 kept, so one slow moment of the machine fails nothing."""
    for _ in range(3):
        small, large = best_of_three(work, SMALL), best_of_three(work, LARGE)
        if large <= 24 * max(small, 1e-4):
            break
    return small, large


@pytest.mark.perf
@pytest.mark.parametrize("shape", SHAPES)
def test_a_long_message_converts_in_time_linear_in_its_length(shape):
    small, large = linear(lambda size: convert(message_of(shape, size)))
    assert large <= 24 * max(small, 1e-4), (small, large)


# Each pattern alone, over the input that would make it read its input again from each start.
ALONE = {
    "an address": lambda n: slack_text._redact("a" * n),
    "an address after another": lambda n: slack_text._redact("a@b+" * (n // 4)),
    "a bracketed domain": lambda n: slack_text._redact("a@[" * (n // 3)),
    "a quoted name": lambda n: slack_text._redact('"' + "a" * n),
    "a quotation's marks": lambda n: slack_text._render("&gt; " * (n // 5) + "x```a```", {}),
    "angles": lambda n: slack_text._render("<" * n, {}),
    "backticks on one line": lambda n: slack_text._render("`a" * (n // 2), {}),
    "emphasis": lambda n: slack_text._prose("*a" * (n // 2)),
}


@pytest.mark.perf
@pytest.mark.parametrize("pattern", ALONE)
def test_each_pattern_reads_its_worst_input_once(pattern):
    small, large = linear(ALONE[pattern])
    assert large <= 24 * max(small, 1e-4), (small, large)


def test_a_redacted_address_next_to_another_is_still_removed():
    """The pattern that finds an address starts no match inside a run of the characters an
    address begins with; one that starts right where the last match ended is still found,
    as one substitution from left to right found it ("+brook" is an address's first part)."""
    assert slack_text._redact("ada@fable.test+brook@fable.test") == (
        "[email removed][email removed]"
    )
    assert slack_text._redact("x a.b@fable.test, c@d") == "x [email removed], [email removed]"
    assert slack_text._redact('"a b"@fable.test') == "[email removed]"


def test_the_conversion_runs_off_the_event_loop(monkeypatch, tmp_path):
    bench = shortcuts.Bench(tmp_path)
    try:
        bench.users[shortcuts.CEDAR] = shortcuts.cedar()
        bench.link(ADA_SLACK, bench.ada, bench.library)
        threads, real = [], shortcuts.shortcuts.note_from_message

        def watched(*args, **kwargs):
            try:
                asyncio.get_running_loop()
                threads.append("the event loop")
            except RuntimeError:
                threads.append(threading.current_thread().name)
            return real(*args, **kwargs)

        monkeypatch.setattr(shortcuts.shortcuts, "note_from_message", watched)
        shortcuts.handle(bench)
        assert len(threads) == 1 and threads != ["the event loop"], threads
        assert len(bench.notes()) == 1
    finally:
        bench.store.close()
