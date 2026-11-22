"""P1: fact extraction and the lint gate cost time that grows with the input, not its square.

Each test times one input and the same shape eight (or sixteen) times larger, in this process,
best of five runs each, with the garbage collector off, and bounds the ratio. Linear work gives
about the size ratio and quadratic work its square: for eight times the input, 8 against 64,
and the bound is 24, well away from both. No wall-clock limit is asserted, so a slow or busy
runner slows both runs alike and the ratio holds; best of five drops the runs a neighbour
interrupted. The small input is sized to take several milliseconds, so the timer's resolution
and fixed costs do not decide the ratio.

Measured before P1 (one passage of numbers): extract_facts 0.27 s for 4,000 facts and 3.2 s
for 16,000; compile and lint 8.0 s for 16,000 and 31.3 s for 32,000.
"""

import gc
import importlib.util
import sys
import time
from collections.abc import Callable

import pytest

from cairn import lint
from cairn.facts import extract_facts, normalize
from cairn.lint import ClaimInput, PageInput
from cairn.spans import Span, chunk
from support.paths import FIXTURES_ROOT, REFERENCE_ROOT

ARABIC = "".join(map(chr, range(0x660, 0x66A)))  # Arabic-Indic digits: words, not tokens
SYMBOLS = [chr(c) for c in range(0x2010, 0x2E00) if not chr(c).isalnum() and not chr(c).isspace()]


def arabic(number: int) -> str:
    return "".join(ARABIC[int(c)] for c in str(number))


def span_of(text: str, kind: str = "paragraph") -> Span:
    return Span(
        id="sp_cost",
        source_id="cost",
        kind=kind,
        heading_path=(),
        start_line=1,
        end_line=1 + text.count("\n"),
        text=text,
    )


def best_of_five(work: Callable[[], object]) -> float:
    gc.collect()
    gc.disable()
    try:
        times = []
        for _ in range(5):
            started = time.process_time()
            work()
            times.append(time.process_time() - started)
        return min(times)
    finally:
        gc.enable()


def assert_near_linear(
    make: Callable[[int], Callable[[], object]], n: int, times: int = 8, bound: float = 24
) -> None:
    """`make(n)` builds the input of size n and returns the work to time on it."""
    small, large = make(n), make(n * times)
    small_s, large_s = best_of_five(small), best_of_five(large)
    assert large_s < bound * small_s, (
        f"{times}x the input took {large_s / small_s:.1f}x the time "
        f"({small_s * 1000:.1f} ms, then {large_s * 1000:.1f} ms); the bound is {bound}x"
    )


def extraction(text_of: Callable[[int], str]) -> Callable[[int], Callable[[], object]]:
    def make(n: int) -> Callable[[], object]:
        span = span_of(text_of(n))
        return lambda: extract_facts(span)

    return make


def numbers(n: int) -> str:
    return " ".join(str(1000 + i) for i in range(n))


@pytest.mark.perf
def test_extracting_one_passage_of_numbers_is_near_linear():
    """Each match was checked against every range claimed before it."""
    assert_near_linear(extraction(numbers), 400)


def joined_pieces(n: int) -> str:
    """n numbers a comma joins to what is around them, of each shape G2 does not read."""
    shapes = ("b_{},{:03d}", "{},{:03d}x", "{}234,{:03d}", "/a{},{:03d}/x", "v1.{},{:03d}")
    return " ".join(shapes[i % 5].format(i % 900 + 1, i % 1000) for i in range(n))


@pytest.mark.perf
def test_extracting_numbers_a_comma_joins_is_near_linear():
    """G2 finds the commas normalize() takes out and skips the facts that start or end at one:
    many short runs of each shape, one long run of groups after a letter, and one long head of
    digits. Each small input takes several milliseconds."""
    assert_near_linear(extraction(joined_pieces), 2_000)
    assert_near_linear(extraction(lambda n: "x1" + ",000" * n + "x 2,500"), 2_000)
    assert_near_linear(extraction(lambda n: "x1" + "2" * n + ",000"), 100_000)


@pytest.mark.perf
def test_extracting_one_passage_of_inline_code_is_near_linear():
    assert_near_linear(extraction(lambda n: "`a` " * n), 400)


@pytest.mark.perf
def test_inline_code_that_keeps_its_white_space_is_near_linear():
    """Each fact with white space inside its code reads where the code is (review blocker 3):
    found once a passage, and looked up, never copied, for each fact."""
    assert_near_linear(extraction(lambda n: "`a  b` " * n), 400)
    assert_near_linear(lambda n: lambda text="`a  b` " * n: normalize(text), 4_000)


@pytest.mark.perf
def test_a_long_run_of_scheme_characters_is_near_linear():
    """The url pattern was tried at every word start of "a.a.a...", and each try scanned to the
    run's end looking for "://": 1.3 s for 32,000 characters, four times as long as 8,000."""
    assert_near_linear(extraction(lambda n: "a." * n), 2_000)
    assert_near_linear(extraction(lambda n: "a-" * n + "://."), 2_000)  # "://", no url after it


@pytest.mark.perf
def test_backtick_runs_of_many_lengths_on_one_line_are_near_linear():
    """An inline-code opener with no closing run of its length on the line scanned to the end of
    the line; a line with runs of 1, 2, 3, ... backticks paid that once per length, its length
    to the power 1.5. Sixteen times the input: 16 linear, 64 at power 1.5, bound 40."""

    def runs(n: int) -> str:
        return "a".join("`" * r for r in range(1, int((2 * n) ** 0.5)))

    assert_near_linear(extraction(runs), 4_000, times=16, bound=40)


# Lint: whether a value occurs in a text as a whole token
#
# The old check read the text with str.find once per value, in C: cheap per character, so a
# timing ratio sees it only on large inputs. These tests count the work instead, which does not
# depend on the machine: the characters of text handed to lint._occurs (a full read for one
# value) or to lint._Needles.found_in (a full read for all its values), against the characters
# of the input. An automaton's read costs amortised constant time a character plus a step per
# value it reports (the _Needles docstring says why). Before building one, _Needles asks
# _occurs while those reads stay within _READS_IN_C reads of the characters it has seen, so
# the bound is that many reads of the input, plus the automaton's.

CONTENT = ("paragraph", "bullet", "code", "table", "quote")


def gate_like(text: str) -> tuple[list[PageInput], list[Span]]:
    """A cairn page as the lint gate sees one: a claim per content span, verbatim, in order."""
    spans = chunk("cap", "# Dense\n\n" + text + "\n")
    content = [s for s in spans if s.kind in CONTENT]
    claims = [ClaimInput(id=f"c{i}", text=s.text, span_ids=[s.id]) for i, s in enumerate(content)]
    body = "# Dense\n\n" + "\n\n".join(s.text for s in content) + "\n"
    return [PageInput(slug="dense", text=body, claims=claims)], spans


def lint_work(monkeypatch, pages, spans, entities=()) -> tuple[int, int]:
    """(work, input characters) of one lint_pages run."""
    work = 0
    occurs = lint._occurs
    needles = getattr(lint, "_Needles", None)
    found_in = needles.found_in if needles else None

    def counted_occurs(norm, text):
        nonlocal work
        work += len(text)
        return occurs(norm, text)

    def counted_found_in(self, text, found=None):
        nonlocal work
        work += len(text)
        return found_in(self, text, found)

    monkeypatch.setattr(lint, "_occurs", counted_occurs)
    if needles:
        monkeypatch.setattr(needles, "found_in", counted_found_in)
    lint.lint_pages(pages, spans, entities)
    monkeypatch.undo()
    return work, sum(len(p.text) for p in pages) + sum(len(s.text) for s in spans)


SHAPES = {
    "numbers": numbers,
    "passages": lambda n: "\n\n".join(
        " ".join(str(10_000 + q * n + i) for i in range(n // 16)) for q in range(16)
    ),
    "versions": lambda n: " ".join(f"{i % 9}.{i // 9 % 9}.{i // 81}" for i in range(n)),
    "dates": lambda n: " ".join(
        f"{2000 + i // 336}-{1 + i // 28 % 12:02d}-{1 + i % 28:02d}" for i in range(n)
    ),
    "same_first_word": lambda n: " ".join(
        f"--a-{i} `a b{i}` a.{i}.0 /a/b/c{i}" for i in range(n // 4)
    ),
    "urls": lambda n: " ".join(f"https://h.example/docs/p{i}.html" for i in range(n)),
    "code_lines": lambda n: (
        "```\n" + "\n".join(f"run --step {i} --dry" for i in range(n)) + "\n```"
    ),
    "arabic_digits": lambda n: " ".join(arabic(1000 + i) for i in range(n)),
    # Values that differ only in symbols around one shared word.
    "symbols_around_one_word": lambda n: " ".join(
        f"`{SYMBOLS[i % 54]}{SYMBOLS[i // 54 % 54]}x`" for i in range(n)
    ),
    "symbol_before_a_common_word": lambda n: " ".join(
        f"`{SYMBOLS[i % len(SYMBOLS)]}a` a" for i in range(n)
    ),
    "symbols_alone": lambda n: symbols_alone(n),
}


def symbols_alone(n: int) -> str:
    """Inline code of symbols only: values with no token or other word character."""
    return " ".join(
        f"`{SYMBOLS[i % 54]}{SYMBOLS[i // 54 % 54]}{SYMBOLS[i // 2916]}`" for i in range(n)
    )


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_lint_gate_reads_each_text_a_bounded_number_of_times(monkeypatch, shape):
    """Before P1 every value of the page and its sources was looked for in the whole text: the
    work was values times characters."""
    pages, spans = gate_like(SHAPES[shape](3_000))
    work, size = lint_work(monkeypatch, pages, spans)
    bound = (lint._READS_IN_C + 2) * size
    assert work <= bound, f"{work:,} units of work for {size:,} characters of input"


def test_placement_reads_each_source_span_a_bounded_number_of_times(monkeypatch):
    """Each flag, path and long inline code of an entity page was looked for in every source
    span: values times spans."""
    n = 1_500
    source = "# Alpha\n\n" + "\n\n".join(f"Run it with --opt-{i:05d} today." for i in range(n))
    page = "---\ntitle: Beta\n---\n" + "\n\n".join(f"Use --opt-{i:05d} here." for i in range(n))
    entities = [lint.Entity(name="Alpha"), lint.Entity(name="Beta")]
    pages = [PageInput(slug="beta", text=page)]
    work, size = lint_work(monkeypatch, pages, chunk("src", source), entities)
    bound = (lint._READS_IN_C + 2) * size
    assert work <= bound, f"{work:,} units of work for {size:,} characters of input"


@pytest.mark.perf
def test_linting_a_dense_page_is_near_linear():
    def make(n: int) -> Callable[[], object]:
        pages, spans = gate_like(numbers(n))
        return lambda: lint.lint_pages(pages, spans)

    assert_near_linear(make, 1_000)


@pytest.mark.perf
def test_linting_a_page_of_symbol_values_is_near_linear():
    """Values with no token character: before P1, and still in the run-anchored matcher of
    910e549, each was handed to `_occurs`, a read of the whole text per value."""

    def make(n: int) -> Callable[[], object]:
        pages, spans = gate_like(symbols_alone(n))
        return lambda: lint.lint_pages(pages, spans)

    assert_near_linear(make, 1_000)


@pytest.mark.perf
def test_many_values_around_one_word_met_at_many_places_are_near_linear():
    """Values "-a", "a--", "--a-", ... share their one run "a", at different offsets and
    lengths, and "a" occurs all over the text: each place was checked for each value, or each
    shape of value. k values per side give k * k values in about k ** 3 characters. The run-
    anchored matcher this replaced took 109x the time for 16x the input."""

    def make(n: int) -> Callable[[], object]:
        k = round(n ** (1 / 3))
        codes = " ".join(f"`{'-' * i}a{'-' * j}`" for i in range(k) for j in range(k))
        pages, spans = gate_like(codes + " a" * n)
        return lambda: lint.lint_pages(pages, spans)

    assert_near_linear(make, 1_000, times=16, bound=40)


@pytest.mark.perf
def test_one_long_value_whose_word_recurs_through_the_text_is_near_linear():
    """A value "a-a-a-...-b" anchored on a word the text repeats was compared at each place
    for as long as the text followed it: places times the value's length. The run-anchored
    matcher this replaced took 94x the time for 16x the input."""

    def make(n: int) -> Callable[[], object]:
        value = "a-" * (n // 4) + "b"
        pages, spans = gate_like(f"`{value}` " + "a-" * n)
        return lambda: lint.lint_pages(pages, spans)

    assert_near_linear(make, 2_000, times=16, bound=40)


@pytest.mark.perf
def test_finding_each_claim_in_the_body_is_near_linear():
    """Each claim was looked for with `in`, reading the body from its start: claims times body.
    The old `[text in body for text in texts]` took 48x the time for 8x the claims."""

    def make(n: int) -> Callable[[], object]:
        texts = [f"Claim {i} says the retry count moved today." for i in range(n)]
        body = "# Page\n\n" + "\n\n## Part\n\n".join(texts)
        return lambda: lint._in_body(texts, body)

    assert_near_linear(make, 8_000)


@pytest.mark.perf
def test_one_claim_citing_many_spans_is_near_linear():
    """Grounding looked for each fact of a claim in each span it cites: one claim of n numbers
    citing n short spans was n * n reads (1.6 s for n = 4,000 before P1, 5.3 s at 2e1cc2c)."""

    def make(n: int) -> Callable[[], object]:
        source = "# S\n\n" + "\n\n".join(f"Value {10_000 + i} here." for i in range(n)) + "\n"
        spans = chunk("src", source)
        text = " ".join(str(10_000 + i) for i in range(n))
        cited = [s.id for s in spans if s.kind == "paragraph"]
        claim = ClaimInput(id="c0", text=text, span_ids=cited)
        pages = [PageInput(slug="p", text=f"# P\n\n{text}\n", claims=[claim])]
        return lambda: lint.lint_pages(pages, spans)

    assert_near_linear(make, 500)


@pytest.mark.perf
def test_many_entities_over_many_spans_is_near_linear():
    """Each span asked every entity whether each segment of its heading path names it, though
    the spans share their segments: entities times spans. Eight times the entities and the
    spans took 29x the time before P1."""

    def make(n: int) -> Callable[[], object]:
        names = [f"Node{i}" for i in range(n // 4)]
        source = "# Node0\n\n## Logs\n\n" + "\n\n".join(f"Node0 entry {i}." for i in range(n))
        spans = chunk("src", source)
        content = [s for s in spans if s.kind in CONTENT]
        claims = [
            ClaimInput(id=f"c{i}", text=s.text, span_ids=[s.id]) for i, s in enumerate(content)
        ]
        body = "\n\n".join(s.text for s in content)
        pages = [PageInput(slug="node1", text=f"# Node1\n\n{body}\n", claims=claims)]
        entities = [lint.Entity(name=name) for name in names]
        return lambda: lint.lint_pages(pages, spans, entities)

    assert_near_linear(make, 400)


@pytest.mark.perf
def test_a_line_of_backticks_with_one_more_after_it_is_near_linear():
    """The fence pattern tried every shorter backtick run, and each try read the rest of the
    line looking for a backtick. (cairn.spans has the same pattern; it is outside P1.)"""
    assert_near_linear(extraction(lambda n: "`" * n + " x`"), 4_000)


# Small inputs: no slower than before P1
#
# Linear work can still cost more than the old quadratic work on the small pages most notes
# make. These tests time the reference copies of 0856d6b (tests/unit/reference) against the
# current modules in this process and bound the ratio. The two take turns, eleven rounds of
# five runs each with the garbage collector off, and the best round of each is compared, so a
# busy runner slows both alike and a burst of load lands on a round of either, which the other
# rounds outweigh. At 2e1cc2c the fixture page linted in 1.8x the time of 0856d6b: an automaton
# was built for every set of values, however small.


def _reference(name: str):
    path = REFERENCE_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"p1_speed_reference_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _small_pages() -> dict[str, str]:
    fixtures = FIXTURES_ROOT
    return {
        "fixtures": "\n\n".join(p.read_text() for p in sorted(fixtures.glob("*.md"))),
        "short_passages": "\n\n".join(f"Item {1000 + i} here." for i in range(250)),
    }


@pytest.mark.perf
@pytest.mark.parametrize("name", sorted(_small_pages()))
def test_a_small_page_lints_about_as_fast_as_before_p1(name):
    old_facts, old_lint = _reference("facts_before_p1"), _reference("lint_before_p1")
    old_lint.Fact, old_lint.extract_facts, old_lint.normalize = (
        old_facts.Fact,
        old_facts.extract_facts,
        old_facts.normalize,
    )
    spans = chunk("cap", "# Small\n\n" + _small_pages()[name] + "\n")
    content = [s for s in spans if s.kind in CONTENT]
    body = "# Small\n\n" + "\n\n".join(s.text for s in content) + "\n"
    work = {}
    for label, module in (("before", old_lint), ("now", lint)):
        claims = [
            module.ClaimInput(id=f"c{i}", text=s.text, span_ids=[s.id])
            for i, s in enumerate(content)
        ]
        pages = [module.PageInput(slug="small", text=body, claims=claims)]
        work[label] = lambda m=module, p=pages: m.lint_pages(p, spans)
    times = {"before": float("inf"), "now": float("inf")}
    gc.collect()
    gc.disable()
    try:
        for _ in range(11):
            for label, run in work.items():
                started = time.process_time()
                for _ in range(5):
                    run()
                times[label] = min(times[label], time.process_time() - started)
    finally:
        gc.enable()
    ratio = times["now"] / times["before"]
    assert ratio <= 1.3, f"{ratio:.2f}x the time of 0856d6b ({times})"
