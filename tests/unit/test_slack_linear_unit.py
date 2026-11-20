"""A filed Slack message costs work in proportion to its length (the third tester, finding
9), and the conversion runs on a worker thread, never on the server's event loop.

What is counted, not timed: every reader (the Markdown parser and the passage splitter) and
every compiled pattern of cairn.slack.text is wrapped, and the characters each call is run
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
    return count


@pytest.mark.parametrize("shape", SHAPES)
def test_what_the_readers_and_the_patterns_are_run_over_grows_with_the_message(counted, shape):
    spent = {}
    for size in (SMALL, LARGE):
        counted[0] = 0
        convert(message_of(shape, size))
        spent[size] = counted[0]
    assert spent[LARGE] <= 10 * spent[SMALL], spent


def best_of_three(work, size: int) -> float:
    times = []
    for _ in range(3):
        start = time.perf_counter()
        work(size)
        times.append(time.perf_counter() - start)
    return min(times)


def linear(work) -> tuple[float, float]:
    """The best of three tries at LARGE and at SMALL; three rounds of it, the first that
    shows a ratio within 24 kept, so one slow moment of the machine fails nothing."""
    for _ in range(3):
        small, large = best_of_three(work, SMALL), best_of_three(work, LARGE)
        if large <= 24 * max(small, 1e-4):
            break
    return small, large


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
