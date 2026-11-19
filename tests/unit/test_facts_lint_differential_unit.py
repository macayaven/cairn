"""P1: facts.py and lint.py return exactly what they returned before they were made near-linear.

tests/unit/reference/ holds both modules byte for byte as they were at commit 0856d6b (under a
three-line header). Each case runs the reference and the current module on the same input and
asserts equality of everything they return: every fact with its kind, value, norm, offsets and
lines, in order; every lint finding, metric and mode; the same error with the same message.
Objects of the two copies are different classes, so their dumps are compared.

The reference lint is bound to the reference facts, so the old pipeline runs whole. When a case
differs, the reference is right by definition.

Inputs: every string literal of the existing tests that feed these modules, the fixtures and the
gold file; the reviewers' dense shapes and the hostile shapes found while profiling, at sizes
the old code finishes quickly; seeded generated passages and pages (the same every run).
"""

import ast
import collections
import importlib.util
import itertools
import operator
import random
import re
import sys
from pathlib import Path

import pytest

from cairn import facts as new_facts
from cairn import lint as new_lint
from cairn.spans import Span, chunk

TESTS = Path(__file__).resolve().parents[1]
REFERENCE = Path(__file__).resolve().parent / "reference"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"p1_reference_{name}", REFERENCE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


old_facts = _load("facts_before_p1")
old_lint = _load("lint_before_p1")
# The reference lint imported cairn.facts by name; point those names at the reference facts.
old_lint.Fact, old_lint.extract_facts, old_lint.normalize = (
    old_facts.Fact,
    old_facts.extract_facts,
    old_facts.normalize,
)

KINDS = ("paragraph", "bullet", "code", "table", "quote", "heading", "frontmatter")
COUNT = {"facts": 0, "lint": 0}  # cases compared, printed by the last test


def span_of(text: str, kind: str = "paragraph", sid: str = "sp_x") -> Span:
    return Span(
        id=sid,
        source_id="src",
        kind=kind,
        heading_path=(),
        start_line=1,
        end_line=1 + text.count("\n"),
        text=text,
    )


# G1 item 5: the one exception, by shape. An underscore run (one to three) opens emphasis at
# the start of a text or after a space, ( [ { " ' * or a backtick, before a character that is
# not a space; it closes emphasis after a character that is not a space, at the end or before a
# space, . , ; : ! ? ) ] } " ' * or a backtick. Where such a run touches a number, a version or
# a date (as the reference reads the text with every such run as "*"), next to its first or
# last character or after its last character and a period, and the fact's other side is
# another such run or a plain edge (before it: the start, a space or what an opener may follow;
# after it: not a word character, / ~ @ +, or a separator and a digit), the current facts.py
# reads the
# run as "*", the same emphasis. On a text holding such a run, and only there, the two copies
# may differ, and only as the reference differs between that text and the same text with
# those runs as "*" (offsets kept, values as written). Any other difference is a failure.
OPENS_AFTER = "([{\"'*`"  # besides a space and the start of the text
CLOSES_BEFORE = ".,;:!?)]}\"'*`"  # besides a space and the end of the text
INSIDE_AFTER = re.compile(r"[\w/~@+]|[,.]\d")  # a fact before one of these is inside a token
OLD_PATTERNS = dict(old_facts._PATTERNS)

# Each shape: an example, what the reference reads in it, what facts.py reads now, and why.
SHAPES = {
    "opens before a number": (
        "_2,700 more comments are left out._",
        "number 700: no number may start after a word character, and _ is one, so the"
        " reference starts after the comma; lint then finds 700 in no whole token of 2700",
        "number 2,700",
        "the underscore is emphasis, as * is; *2,700 reads 2,700",
    ),
    "closes after a number": (
        "_about 1,250_ jobs ran.",
        "number 1: 1,250 may not end before a word character, so the reference backs off to 1",
        "number 1,250",
        "the underscore closes the emphasis; 1,250* reads 1,250",
    ),
    "closes after a number and its period": (
        "_They left out 2,700._",
        "number 2: the reference may not end 2,700 before a period and a word character",
        "number 2,700",
        "the period ends the sentence and the underscore the emphasis, as in 2,700.*",
    ),
    "opens before a date": (
        "_2099-03-02_ is the day.",
        "number 03: the date may not start after _, so a number inside it is read",
        "date 2099-03-02",
        "the date is the whole emphasised token, as in *2099-03-02*",
    ),
    "closes after a date": (
        "the day is _2099-03-02_",
        "number 03, as above",
        "date 2099-03-02",
        "as above",
    ),
    "closes after a date and its period": (
        "_It shipped on 2099-03-02._",
        "date 2099-03-02 already, when nothing else touches it",
        "date 2099-03-02",
        "as above; the run is read as * so the text is normalized as with *",
    ),
    "opens before a version": (
        "_v1.2.3_ shipped.",
        "nothing: a version may not start or end next to a word character",
        "version v1.2.3",
        "as above, *v1.2.3* reads v1.2.3",
    ),
    "closes after a version": (
        "the release _1.2.3-rc1_",
        "nothing, as above",
        "version 1.2.3-rc1",
        "as above, *1.2.3-rc1* reads 1.2.3-rc1",
    ),
    "closes after a version and its period": (
        "_It is 2.10.1._",
        "nothing",
        "version 2.10.1",
        "as above",
    ),
}
# The same runs inside inline code or a code block, where nothing but the code is a fact:
# the code's value is as written, and its norm and lines read the run as *, as the page and
# the span that hold them are normalized: normalize() cannot tell code from prose, and lint
# finds the code because both sides are read alike.
DIFFERED: collections.Counter[str] = collections.Counter()  # inputs that differed, by shape


def emphasis_runs(text: str) -> dict[tuple[int, int], str]:
    """The underscore runs facts.py now reads as "*" in `text`, each with its shape."""
    if "_" not in text:
        return {}
    opens, closes = {}, {}
    for run in re.finditer("_+", text):
        a, b = run.span()
        before = text[a - 1] if a else " "
        after = text[b] if b < len(text) else " "
        if b - a > 3:
            continue
        if (before.isspace() or before in OPENS_AFTER) and b < len(text) and not after.isspace():
            opens[b] = a
        if a and not before.isspace() and (after.isspace() or after in CLOSES_BEFORE):
            closes[a] = b
    read = star(text, [(a, b) for b, a in opens.items()] + list(closes.items()))
    runs = {}
    for kind in ("date", "version", "number"):
        for m in OLD_PATTERNS[kind].finditer(read):
            a, b = m.span()
            stop = b + 1 if text[b : b + 1] == "." and b + 1 in closes else b
            left, right = a in opens, stop in closes
            if not (left or right):
                continue
            if not left and a and not (text[a - 1].isspace() or text[a - 1] in OPENS_AFTER):
                continue
            if not right and INSIDE_AFTER.match(text, b):
                continue
            if left:  # named after the first kind that keeps it: date, version, number
                runs.setdefault((opens[a], a), f"opens before a {kind}")
            if right:
                period = " and its period" if stop > b else ""
                runs.setdefault((stop, closes[stop]), f"closes after a {kind}{period}")
    return runs


def star(text: str, runs) -> str:
    out = list(text)
    for a, b in runs:
        out[a:b] = "*" * (b - a)
    return "".join(out)


def as_starred(text: str) -> str:
    """`text` with the runs facts.py reads as "*" written as "*"; asserts their shapes."""
    runs = emphasis_runs(text)
    assert set(runs.values()) <= SHAPES.keys(), (sorted(set(runs.values())), text[:300])
    return star(text, runs)


def starred_facts(span: Span) -> list:
    """The reference's facts of the span with its runs written as "*", valued as written."""
    runs = emphasis_runs(span.text)
    if not runs:
        return old_facts.extract_facts(span)
    read = old_facts.extract_facts(span.model_copy(update={"text": as_starred(span.text)}))
    return [f.model_copy(update={"value": span.text[f.start : f.end]}) for f in read]


def starred_normalize(value: str) -> str:
    return code_normalize(as_starred(value))


# G2: the second exception, by shape. normalize() takes out a comma that has a digit before it
# and exactly three digits after it, so it joins build_1,250 into build_1250 and 1,250x into
# 1250x. The reference read pieces of such a joined run: 250 of build_1,250 (no number may start
# after a word character, so it started after the comma), 1 of 1,250x (no number may end before
# one, so it backed off to 1), 1234 and 567 of 1234,567, the path /logs/batch_10 of
# /logs/batch_10,000/out.txt, the version v1.2 of v1.2,000. normalize() joins each piece to a
# digit, and lint finds it in no whole token. Now no fact is read that starts or ends at such a
# comma when the digit across it is 0-9, a character of lint's tokens; a piece beside a digit of
# another script is found by lint and reads as before.
#
# The oracle is the reference itself with that one rule added to each of its patterns
# (ORACLE): its facts, of the text with G1's runs as "*" and valued as written, must equal the
# current ones on every input, and normalize() must equal the reference's. Where the oracle
# and the plain reference differ, each difference is named by a shape of PIECE_SHAPES; a
# difference of no listed shape fails, and so does any other difference from the oracle.
ASCII_DIGITS = "0123456789"


def joined_edges(text: str) -> tuple[set[int], set[int]]:
    """(starts, ends): where no fact may start (right after a comma normalize() takes out, a
    0-9 digit before it) and where none may end (at such a comma, a 0-9 digit after it).
    Written apart from facts.py, from the reference's own separator."""
    starts, ends = set(), set()
    for m in old_facts._THOUSANDS_SEPARATOR.finditer(text):
        comma = m.start()
        if text[comma - 1] in ASCII_DIGITS:
            starts.add(comma + 1)
        if text[comma + 1] in ASCII_DIGITS:
            ends.add(comma)
    return starts, ends


class WholeRuns:
    """A reference pattern less the matches that joined_edges rules out."""

    def __init__(self, pattern: re.Pattern[str]) -> None:
        self.pattern, self.groupindex = pattern, pattern.groupindex

    def finditer(self, text: str):
        starts, ends = joined_edges(text)
        found = self.pattern.finditer(text)
        return (m for m in found if m.start() not in starts and m.end() not in ends)


def _load_as(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, REFERENCE / f"{file}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ORACLE = _load_as("g2_oracle_facts", "facts_before_p1")
ORACLE._PATTERNS = tuple((kind, WholeRuns(pattern)) for kind, pattern in ORACLE._PATTERNS)

# Each shape: an example, what the reference reads in it, what facts.py reads now, and why.
PIECE_SHAPES = {
    "a letter or underscore before the run": (
        "The failing job is build_1,250 on the main branch.",
        [("number", "250")],
        [],
        "normalize() reads build_1250, one name, which gives nothing written so; the reference's"
        " 250 is in no whole token of it",
    ),
    "the run goes on past where a number may end": (
        "The new index gives a 1,250x speedup on the large table.",
        [("number", "1")],
        [],
        "1,250x is one token, as 1250x is, which gives nothing; the reference backed off to 1,"
        " which is in no whole token of 1250x",
    ),
    "a head of four or more digits": (
        "The measured value was 1234,567 in the second run.",
        [("number", "1234"), ("number", "567")],
        [],
        "normalize() reads 1234567; neither piece is a whole token of it",
    ),
    "a decimal before the group": (
        "The ratio was 1.5,250 in the second run.",
        [("number", "1.5"), ("number", "250")],
        [],
        "normalize() reads 1.5250; neither piece is a whole token of it",
    ),
    "a version before the group": (
        "The range is v1.2,000 items wide in the config.",
        [("version", "v1.2"), ("number", "000")],
        [],
        "normalize() reads v1.2000; neither piece is a whole token of it",
    ),
    "a version after the comma": (
        "The id is 1,234.5.6 in the config.",
        [("number", "1"), ("version", "234.5.6")],
        [],
        "normalize() reads 1234.5.6; neither piece is a whole token of it",
    ),
    "a rooted path that holds the run": (
        "The log is at /logs/batch_10,000/out.txt on the host.",
        [("path", "/logs/batch_10"), ("number", "000")],
        [],
        "normalize() reads /logs/batch_10000/out.txt; neither piece is a whole token of it. Read"
        " whole through the comma, the path would be found too, but that changes exactly the"
        " same inputs as reading nothing, so the one rule holds",
    ),
    "a flag that ends at the comma": (
        "Run it with --run-x628,506 on the host.",
        [("flag", "--run-x628"), ("number", "506")],
        [],
        "normalize() reads --run-x628506; neither piece is a whole token of it",
    ),
    "a date that ends at the comma": (
        "The day 2099-03-14,250 is in the config.",
        [("date", "2099-03-14")],
        [("number", "2099"), ("number", "03"), ("number", "14,250")],
        "normalize() reads 2099-03-14250; the date is no whole token of it, and with the date"
        " not read, the numbers it held are read, each a whole token",
    ),
    "a fact a refused piece leaves free": (
        "The file is /srv/1.2.3/a1,250 on the host.",
        [("path", "/srv/1.2.3/a1"), ("number", "250")],
        [("version", "1.2.3")],
        "the path /srv/1.2.3/a1 is in no whole token of /srv/1.2.3/a1250; not read, it leaves"
        " 1.2.3 free, read as a version, a whole token",
    ),
}
DIFFERED_G2: collections.Counter[str] = collections.Counter()  # inputs that differed, by shape


def _number_shape(value: str) -> str:
    """The shape of a number the reference read that ends at a joined comma."""
    if "." in value:
        return "a decimal before the group"
    if len(value.lstrip("$€£").split(",")[0]) > 3:
        return "a head of four or more digits"
    return "the run goes on past where a number may end"


def piece_shape(text: str, fact: dict, ends_at: dict[int, dict]) -> str:
    """The shape of a fact the reference read and the oracle does not."""
    kind, start, value = fact["kind"], fact["start"], fact["value"]
    if fact["end"] in joined_edges(text)[1]:  # the run's head piece: by its kind
        named = {
            "path": "a rooted path that holds the run",
            "flag": "a flag that ends at the comma",
        }
        named |= {"date": "a date that ends at the comma", "version": "a version before the group"}
        return named.get(kind) or (_number_shape(value) if kind == "number" else "unlisted")
    if kind == "version":
        return "a version after the comma"
    if kind != "number":
        return "unlisted"
    if start - 1 in ends_at:  # the same run's head piece names it
        return piece_shape(text, ends_at[start - 1], ends_at)
    first, separators = (
        start - 1,
        {m.start() for m in old_facts._THOUSANDS_SEPARATOR.finditer(text)},
    )
    while first > 0 and (text[first - 1].isdecimal() or first - 1 in separators):
        first -= 1
    before = text[first - 1] if first else ""
    if before == "_" or (before.isalnum() and not before.isdecimal()):
        return "a letter or underscore before the run"
    if len(text[first:start].split(",")[0]) > 3:
        return "a head of four or more digits"
    if before == ".":
        return "a decimal before the group"
    return "unlisted"


# Review blocker 3: the third rule, added to the oracle. White space inside inline code is the
# code's own, as the gate reads prose: normalize() keeps it, and an inline_code fact or a code
# line keeps it in its norm, so `a  b.txt` and `a b.txt` are two facts. Everything else is
# collapsed as the reference collapses it. Inline code is found here the slow way, apart from
# cairn.spans.code_spans, with the backticks of every fence line (the reference's `_FENCE`)
# set aside: a run of backticks not escaped by a backslash opens, and the first later run of
# exactly as many before a blank line closes it.
DIFFERED_CODE = collections.Counter()  # inputs whose reading the rule changed, by what


def code_places(text: str) -> set[int]:
    """The places of every character of inline code in `text`, its backticks included."""
    return _paired_slowly(old_facts._FENCE.sub(lambda m: m[0].replace("`", "~"), text))


def _paired_slowly(text: str) -> set[int]:
    inside: set[int] = set()
    n = 0
    while n < len(text):
        if text[n] != "`":
            n += 1
            continue
        end = n
        while end < len(text) and text[end] == "`":
            end += 1
        slashes = 0
        while n - slashes - 1 >= 0 and text[n - slashes - 1] == "\\":
            slashes += 1
        opener = n + 1 if slashes % 2 else n
        size, close, found = end - opener, end, None
        while size and close < len(text):
            if text[close] == "\n":
                after = text.find("\n", close + 1)
                if after != -1 and not text[close + 1 : after].strip(" \t\r"):
                    break  # a blank line ends the block
            if text[close] != "`":
                close += 1
                continue
            stop = close
            while stop < len(text) and text[stop] == "`":
                stop += 1
            if stop - close == size:
                found = stop
                break
            close = stop
        if found is None:
            n = end
            continue
        inside.update(range(opener, found))
        n = found
    return inside


def code_read(text: str, start: int, end: int, places: set[int]) -> str:
    """text[start:end] as the reference normalizes it, with the third rule: white space at a
    place of `places` kept as it is."""
    pieces: list[tuple[bool, str]] = []  # (outside white space, text)
    n = start
    while n < end:
        if n not in places and text[n].isspace():
            while n < end and n not in places and text[n].isspace():
                n += 1
            pieces.append((True, " "))
            continue
        pieces.append((False, text[n]))
        n += 1
    while pieces and pieces[0][0]:
        pieces.pop(0)
    while pieces and pieces[-1][0]:
        pieces.pop()
    read = "".join(piece for _, piece in pieces).casefold()
    return old_facts._THOUSANDS_SEPARATOR.sub("", read)


def code_normalize(read: str) -> str:
    """The oracle's normalize() of a text read with G1's runs as "*"."""
    old = old_facts.normalize(read)
    if "`" not in read:
        return old
    kept = code_read(read, 0, len(read), code_places(read))
    if kept != old:
        DIFFERED_CODE["normalize"] += 1
    return kept


def oracle_facts(span: Span, read: str, code: bool = True) -> list[dict]:
    """The oracle's facts of the span read as `read` (G1's runs as "*"), valued as written;
    with `code`, inline code's norms read by the third rule."""
    facts = [f.model_dump() for f in ORACLE.extract_facts(span.model_copy(update={"text": read}))]
    places = code_places(read) if code and "`" in read else set()
    for fact in facts:
        fact["value"] = span.text[fact["start"] : fact["end"]]
        if not places:
            continue
        kept = code_read(read, fact["start"], fact["end"], places)
        if kept != fact["norm"]:
            DIFFERED_CODE[f"{fact['kind']} norm"] += 1
        fact["norm"] = kept
        if fact["kind"] == "code_block":
            lines = tuple(code_lines(read, fact["start"], fact["end"], places))
            if lines != fact["lines"]:
                DIFFERED_CODE["code lines"] += 1
            fact["lines"] = lines
    return facts


def code_lines(read: str, start: int, end: int, places: set[int]):
    """The reference's code lines of read[start:end], each read by the third rule."""
    at = start
    for line in read[start:end].splitlines(keepends=True):
        bare = line.rstrip("\r\n\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
        if bare.strip() and not bare.strip().startswith(("#", "//")):
            yield code_read(read, at, at + len(bare), places)
        at += len(line)


def g2_shapes(read: str, reference: list[dict], oracle: list[dict]) -> set[str]:
    """The shapes of the differences between the reference's facts and the oracle's."""
    key = operator.itemgetter("kind", "start", "end")
    kept = {key(f) for f in oracle}
    ends_at = {f["end"]: f for f in reference}
    shapes = {piece_shape(read, f, ends_at) for f in reference if key(f) not in kept}
    if {key(f) for f in reference} >= kept:
        return shapes
    return shapes | {"a fact a refused piece leaves free"}


def expected_facts(span: Span) -> list:
    """What the reference lint is given: the oracle's facts, as Fact objects of the reference."""
    return [old_facts.Fact(**f) for f in oracle_facts(span, as_starred(span.text))]


# The reference lint reads facts through the oracle, with G1's runs as "*"; every other check
# of the lint (a claim's text in the page, titles, entities) reads the inputs as given.
old_lint.extract_facts, old_lint.normalize = expected_facts, starred_normalize

# The invariant: every fact facts.py reads occurs in its own span by lint's own matching, so a
# claim that is the verbatim text of its passage never fails grounding or retention for a fact
# of its own. Measured on 37008da, before G2, every failure was a piece of a run normalize()
# joins across a comma; no other shape failed, so nothing is excluded from it.
INVARIANT_EXCLUDED: dict[str, str] = {}


def every_fact_occurs(span: Span) -> None:
    body = new_facts.normalize(span.text)
    for fact in new_facts.extract_facts(span):
        for norm in fact.lines if fact.kind == "code_block" else (fact.norm,):
            assert new_lint._occurs(norm, body), (fact.kind, fact.value, span.text[:300])


def same_facts(span: Span) -> None:
    COUNT["facts"] += 1
    new = [f.model_dump() for f in new_facts.extract_facts(span)]
    runs = emphasis_runs(span.text)
    read = as_starred(span.text) if runs else span.text
    # The reference reads G1's runs as "*", its values as written.
    reference = [f.model_dump() for f in starred_facts(span)]
    expected = oracle_facts(span, read)
    assert new == expected, span.text[:300]
    assert new_facts.normalize(span.text) == code_normalize(read)
    every_fact_occurs(span)
    if (pieces := oracle_facts(span, read, code=False)) != reference:
        shapes = g2_shapes(read, reference, pieces)
        assert shapes and shapes <= PIECE_SHAPES.keys(), (sorted(shapes), span.text[:300])
        DIFFERED_G2.update(shapes)
    if runs:
        plain = [f.model_dump() for f in old_facts.extract_facts(span)]
        if reference != plain or new_facts.normalize(span.text) != old_facts.normalize(span.text):
            DIFFERED.update(set(runs.values()))


def same_text(text: str) -> None:
    """Every span of `text`, as chunked, and the whole text as one span of each kind."""
    for span in chunk("src", text):
        same_facts(span)
    for kind in ("paragraph", "code"):
        same_facts(span_of(text, kind))


def _report(module, pages, spans, entities, unassigned):
    try:
        report = module.lint_pages(
            [module.PageInput(**p) for p in pages],
            spans,
            [module.Entity(**e) for e in entities],
            unassigned,
        )
    except module.LintInputError as error:
        return ("LintInputError", str(error))
    return report.model_dump()


def same_lint(pages, spans, entities=(), unassigned=()) -> None:
    COUNT["lint"] += 1
    new = _report(new_lint, pages, spans, entities, unassigned)
    # The reference lint reads facts and normalizes through starred_facts and starred_normalize
    # (G1 item 5, below); with no run in a text that is the reference facts itself.
    old = _report(old_lint, pages, spans, entities, unassigned)
    assert new == old


# Inputs the existing tests feed to these modules


FEEDERS = [
    "acceptance/test_t2_facts.py",
    "acceptance/test_t3_lint.py",
    "acceptance/test_t3_multisource.py",
    "acceptance/test_t5_grounding.py",
    "acceptance/test_t5b_blocks.py",
    "acceptance/test_t5c_structure.py",
    "acceptance/test_w3_gate.py",
    "acceptance/test_t4_coverage_guard.py",
    "unit/test_facts_unit.py",
    "unit/test_lint_unit.py",
    "unit/test_fact_matching_invariant.py",
    "unit/test_lint_speed_unit.py",
    "unit/test_gate_unit.py",
]
FIXTURES = sorted((TESTS / "fixtures").glob("*.md"))
ENTITIES = [{"name": "Quill", "aliases": ["quill"]}, {"name": "Lumen"}, {"name": "Tessera"}]


def literals() -> list[str]:
    out = []
    for name in FEEDERS:
        path = TESTS / name
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append(node.value)
    return sorted(set(out))


@pytest.fixture(params=["as_shipped", "automaton_only"])
def matcher(request, monkeypatch):
    """lint._Needles answers small inputs with `_occurs` and larger ones with its automaton;
    "automaton_only" gives it no budget for `_occurs`, so the automaton answers every case."""
    if request.param == "automaton_only":
        monkeypatch.setattr(new_lint, "_READS_IN_C", 0)
    return request.param


def test_the_literals_of_the_existing_tests_give_the_same_facts():
    for text in literals():
        same_text(text)


def test_the_fixtures_and_the_gold_file_give_the_same_facts_and_reports(matcher):
    gold = (TESTS / "gold" / "t2_facts_synthetic.jsonl").read_text(encoding="utf-8")
    same_text(gold)
    for line in filter(str.strip, gold.splitlines()):
        same_text(line)
    entities = ENTITIES + [{"name": p.stem.split("_")[0]} for p in FIXTURES]
    for path in FIXTURES:
        text = path.read_text(encoding="utf-8")
        same_text(text)
        spans = chunk(path.stem, text)
        content = [s for s in spans if s.kind in new_lint.CONTENT_KINDS]
        claims = [
            {"id": f"c{i}", "text": s.text, "span_ids": [s.id]} for i, s in enumerate(content)
        ]
        body = "\n\n".join(s.text for s in content)
        for ents in ((), entities):
            same_lint([{"slug": "copy", "text": text}], spans, ents)
            same_lint([{"slug": "page", "text": body, "claims": claims}], spans, ents)
            same_lint(
                [{"slug": "half", "text": body[: len(body) // 2], "claims": claims[::2]}],
                spans,
                ents,
                [s.id for s in content[1::4]],
            )


def test_the_cli_entry_reads_the_fixtures_the_same(tmp_path):
    yaml_path = TESTS / "fixtures" / "entities.yaml"
    for path in FIXTURES:
        pages = tmp_path / path.stem
        pages.mkdir()
        text = path.read_text(encoding="utf-8")
        content = [s for s in chunk(path.stem, text) if s.kind in new_lint.CONTENT_KINDS]
        (pages / "copy.md").write_text(text, encoding="utf-8")
        (pages / "page.md").write_text("\n\n".join(s.text for s in content), encoding="utf-8")
        claims = [
            {"id": f"c{i}", "text": s.text, "passage_ids": [s.id]} for i, s in enumerate(content)
        ]
        (pages / "page.claims.json").write_text(
            __import__("json").dumps({"claims": claims}), encoding="utf-8"
        )
        for entities in (None, yaml_path):
            COUNT["lint"] += 1
            old = old_lint.lint(path, pages, entities).model_dump()
            assert new_lint.lint(path, pages, entities).model_dump() == old


# The reviewers' dense shapes and the hostile shapes found while profiling, kept small


ARABIC, FULLWIDTH, DEVANAGARI = (
    "".join(map(chr, range(c, c + 10))) for c in (0x660, 0xFF10, 0x966)
)
SYMBOLS = [chr(c) for c in range(0x2010, 0x2E00) if not chr(c).isalnum() and not chr(c).isspace()]
SYMBOLS[:3] = "-/."  # ASCII symbols too: "-x", "/x", ".x"


def arabic(number: int) -> str:
    return "".join(ARABIC[int(c)] for c in str(number))


def dense(n: int, base: int = 1000) -> str:
    return " ".join(str(base + i) for i in range(n))


HOSTILE = {
    "numbers": lambda n: dense(n),
    "items": lambda n: "\n\n".join(f"Item {1000 + i} here." for i in range(n)),
    "dots": lambda n: "a." * n,
    "dashes": lambda n: "a-" * n,
    "url_tail": lambda n: "a." * n + "://.",
    "urls": lambda n: " ".join(f"https://h.example/{i}/p.{i}" for i in range(n)),
    "url_runs": lambda n: "x://y " + "a.b-c+d" * n + "://z." + " q.r://" * n,
    "backtick_runs": lambda n: "a".join("`" * r for r in range(1, n // 8 + 2)),
    "backtick_pairs": lambda n: "`a ` `` b `` " * n,
    "ticks_one": lambda n: "`a " * n,
    "fence_line": lambda n: "`" * n + " x`\n```\ncode\n```",
    "fences": lambda n: "\n".join(["```", "x", "~~~", "> ```", "  ```"] * n),
    "commas": lambda n: "1," + ",".join(["000"] * n) + "x",
    "versions": lambda n: " ".join(f"{i % 7}.{i % 5}.{i % 3}" for i in range(n)),
    "dates": lambda n: " ".join(
        f"20{i % 30:02d}-{1 + i % 12:02d}-{1 + i % 28:02d}" for i in range(n)
    ),
    "flags": lambda n: " ".join(f"--opt-{i} -{chr(97 + i % 26)}" for i in range(n)),
    "paths": lambda n: " ".join(f"/srv/a{i % 9}/b{i}.txt ./c/{i} ~/d" for i in range(n)),
    "arabic": lambda n: " ".join(arabic(1000 + i) for i in range(n)),
    "arabic_commas": lambda n: " ".join(f"x{arabic(1)},{arabic(1000 + i)[1:]}" for i in range(n)),
    "same_word": lambda n: " ".join(f"--a-{i} `a {i}` a{i} a-{i}-b" for i in range(n)),
    "permutations": lambda n: "\n".join(
        f"`{' '.join(random.Random(i).sample('abcdef', 6))}`" for i in range(n)
    ),
    "ordered": lambda n: "\n".join(f"{i}. step {i}" for i in range(n)),
    # Values of one length around one word at one offset, found, near-missed and repeated.
    "symbols_around_x": lambda n: " ".join(
        f"`{SYMBOLS[i % 7]}{SYMBOLS[i // 7 % 7]}x` {SYMBOLS[i // 7 % 7]}x{SYMBOLS[i % 7]}"
        f" a{SYMBOLS[i % 7]}x `{SYMBOLS[i % 3]}{SYMBOLS[i % 7]}x`"
        for i in range(n)
    ),
    "symbol_before_a": lambda n: " ".join(
        f"`{SYMBOLS[i % 40]}a` a {SYMBOLS[i % 9]}a" for i in range(n)
    ),
    # Values that hold one word at many offsets and lengths, met whole, inside longer tokens,
    # and as longer values' suffixes; and one long periodic value against periodic text.
    "offsets_around_a": lambda n: " ".join(
        f"`{'-' * (i % 5)}a{'-' * (i // 5 % 5)}` -{'-' * (i % 3)}a-- xa- -ax {'-' * i}a"
        for i in range(n)
    ),
    "long_periodic": lambda n: f"`{'a-' * (n // 2)}b` " + "a-" * n + "b a-a-b",
}


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_the_dense_and_hostile_shapes_give_the_same_facts_and_reports(shape, matcher):
    for n in (0, 1, 2, 3, 7, 40, 200):
        text = HOSTILE[shape](n)
        same_text(text)
        doc = "# Dense\n\n" + text + "\n"
        spans = chunk("cap", doc)
        content = [s for s in spans if s.kind in new_lint.CONTENT_KINDS]
        claims = [
            {"id": f"c{i}", "text": s.text, "span_ids": [s.id]} for i, s in enumerate(content)
        ]
        body = "# Dense\n\n" + "\n\n".join(s.text for s in content) + "\n"
        same_lint([{"slug": "dense", "text": body, "claims": claims}], spans)
        same_lint([{"slug": "plain", "text": text}], spans, [{"name": "Dense"}])


# Seeded generated inputs

DIGITS = ["0123456789", ARABIC, FULLWIDTH, DEVANAGARI]
WORDS = ["the", "a", "run", "job", "retry", "deploy", "Quill", "lumen", "tessera", "node"]
WORDS += ["x", "y", "z", "on", "at", "é", "ß"]
WORDS += [chr(0x130), chr(0xFB01)]  # capital I with a dot, the fi ligature: casefold grows them
UNITS = ["", " ", "-"]
UNIT_WORDS = ["days", "day", "tokens", "files", "min", "sec", "ms", "KB", "GB", "s", "h", "%"]
PUNCT = [*".,;:!?)(-—/\\'\"<>[]{}|*_~#@+=&", "...", " - ", "…", chr(0x60C), chr(0x66B)]


def piece(rng: random.Random) -> str:
    """One token-ish piece: a fact of some kind, a near miss, or prose."""
    digits = rng.choice(DIGITS) if rng.random() < 0.15 else DIGITS[0]

    def num(lo: int = 1, hi: int = 6) -> str:
        return "".join(rng.choice(digits) for _ in range(rng.randint(lo, hi)))

    kind = rng.randrange(22)
    if kind == 0:
        return num()
    if kind == 1:
        decimals = rng.choice(["", "." + num(1, 2)])
        return f"{rng.choice(['', '$', '€', '£'])}{num(1, 3)},{num(3, 3)}{decimals}"
    if kind == 2:
        return num() + rng.choice(UNITS) + rng.choice(UNIT_WORDS)
    if kind == 3:
        time = rng.choice(["", "T10:00", "T10:00:00Z", "T10:05:30.250+01:00", "t1"])
        return f"{num(4, 4)}-{num(2, 2)}-{num(2, 2)}{time}"
    if kind == 4:
        core = ".".join(num(1, 2) for _ in range(rng.randint(1, 4)))
        return rng.choice(["", "v", "V"]) + core + rng.choice(["", "-rc1", "-beta.2", "-", "-a.b-"])
    if kind == 5:
        return (
            rng.choice(["-", "--", "---"]) + rng.choice(WORDS) + rng.choice(["", "-x", "_y", "=1"])
        )
    if kind == 6:
        root = rng.choice(["/", "~/", "./", "../", "//", ".../"])
        return root + "/".join(rng.choice([*WORDS, num()]) for _ in range(rng.randint(1, 4)))
    if kind == 7:
        scheme = rng.choice(["https", "http", "s3", "git+ssh", "a.b-c", "1x"])
        path = "/".join(rng.choice([*WORDS, num()]) for _ in range(rng.randint(0, 3)))
        tail = rng.choice(["", "?q=1", "#f", ".", ")"])
        return f"{scheme}://{rng.choice(['h.example', 'x', ''])}/{path}{tail}"
    if kind == 8:
        return f"{rng.choice(WORDS)}@{rng.choice(WORDS)}.example"
    if kind == 9:
        ticks = "`" * rng.randint(1, 3)
        inner = " ".join(piece(rng) for _ in range(rng.randint(0, 3))) or rng.choice(WORDS)
        close = ticks if rng.random() < 0.8 else "`" * rng.randint(1, 4)
        return f"{ticks}{inner}{close}"
    if kind == 10:
        return rng.choice(PUNCT)
    if kind == 11:
        return "".join(rng.choice("`.-/:~_1aé ") for _ in range(rng.randint(1, 8)))
    if kind == 12:  # two pieces touching
        return piece(rng) + piece(rng)
    if kind == 13:
        return num() + rng.choice(["x", "k", "_", "é", ".", ".5", "a1"])
    return rng.choice(WORDS)


def line(rng: random.Random) -> str:
    seps = [" "] * 6 + ["", ", ", "; ", ". ", " (", ") ", "/", "\t"]
    parts = [piece(rng) for _ in range(rng.randint(0, 12))]
    return "".join(p + rng.choice(seps) for p in parts).rstrip(" ")


def block(rng: random.Random) -> str:
    kind = rng.randrange(10)
    if kind == 0:
        return f"{'#' * rng.randint(1, 3)} {line(rng)}"
    if kind == 1:
        return "\n".join(f"{rng.choice(['-', '*', '1.', '2)'])} {line(rng)}" for _ in range(3))
    if kind == 2:
        fence = rng.choice(["```", "~~~", "````", "> ```", "  ```"])
        body = "\n".join(line(rng) for _ in range(rng.randint(0, 3)))
        close = rng.choice([fence.strip("> "), fence, "", "```x", "   ```"])
        return f"{fence}{rng.choice(['', 'sh', 'a`b'])}\n{body}\n{close}".rstrip("\n")
    if kind == 3:
        return "\n".join(f"| {line(rng)} | {piece(rng)} |" for _ in range(rng.randint(1, 3)))
    if kind == 4:
        return "\n".join(f"> {line(rng)}" for _ in range(rng.randint(1, 3)))
    if kind == 5:
        return "\n".join(
            f"# {c}" if c.startswith("#") else c for c in [line(rng), "// " + line(rng)]
        )
    return "\n".join(line(rng) for _ in range(rng.randint(1, 3)))


def document(rng: random.Random) -> str:
    return "\n\n".join(block(rng) for _ in range(rng.randint(0, 6)))


def test_generated_passages_give_the_same_facts():
    rng = random.Random(20261114)
    for _ in range(2500):
        same_text(document(rng))
    for _ in range(2500):  # character soup, from empty to a few dozen characters
        alphabet = rng.choice(
            ["`.-/:~ 1a\n", "`~> \n x", "1,.$% kmGB-", "aé .,-/:_" + ARABIC[:3], "://a.b-"]
        )
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        same_facts(span_of(text, rng.choice(KINDS)))


def emphasised(rng: random.Random) -> str:
    """A line where underscores open and close emphasis, sit inside words, or both."""
    pieces = []
    for _ in range(rng.randint(1, 8)):
        inner = piece(rng)
        marks = "_" * rng.choice([1, 1, 1, 2, 3, 4])
        choice = rng.randrange(9)
        if choice == 0:
            pieces.append(f"{marks}{inner}{marks}")
        elif choice == 1:
            pieces.append(f"{marks}{inner} {rng.choice(WORDS)}{marks}")
        elif choice == 2:
            pieces.append(f"{marks}{rng.choice(WORDS)} {inner}{rng.choice(['', '.', ','])}{marks}")
        elif choice == 3:
            pieces.append(f"{rng.choice(WORDS)}_{inner}")  # inside a word: max_retries_3
        elif choice == 4:
            pieces.append(f"{inner}_{rng.choice(WORDS)}")
        elif choice == 5:
            pieces.append(f"{rng.choice(PUNCT)}{marks}{inner}{marks}{rng.choice(PUNCT)}")
        elif choice == 6:
            pieces.append(f"`{marks}{inner}{marks}`")
        else:
            pieces.append(inner)
    return rng.choice([" ", "", ". ", "\n"]).join(pieces)


def test_underscores_around_and_inside_facts_differ_only_by_shape(matcher):
    """Lines dense with underscores: each differs from the reference only where SHAPES says,
    and every fact the current copy reads is found whole in its own span (lint._occurs), unless
    the reference read that same fact and could not find it either."""
    rng = random.Random(20261119)
    for _ in range(4000):
        text = "\n\n".join(emphasised(rng) for _ in range(rng.randint(1, 3)))
        same_text(text)
        for span in chunk("src", text):
            body = new_facts.normalize(span.text)
            old_body = old_facts.normalize(span.text)
            lost = {
                (f.kind, f.start, f.end)
                for f in old_facts.extract_facts(span)
                for n in (f.lines if f.kind == "code_block" else (f.norm,))
                if not new_lint._occurs(n, old_body)
            }
            for fact in new_facts.extract_facts(span):
                for n in fact.lines if fact.kind == "code_block" else (fact.norm,):
                    found = new_lint._occurs(n, body) or (fact.kind, fact.start, fact.end) in lost
                    assert found, (fact.kind, fact.value, span.text)
        spans = chunk("cap", "# Emphasis\n\n" + text + "\n")
        content = [s for s in spans if s.kind in new_lint.CONTENT_KINDS]
        claims = [
            {"id": f"c{i}", "text": s.text, "span_ids": [s.id]} for i, s in enumerate(content)
        ]
        body = "# Emphasis\n\n" + "\n\n".join(s.text for s in content) + "\n"
        same_lint([{"slug": "emphasis", "text": body, "claims": claims}], spans)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_each_shape_is_read_as_the_table_says(shape):
    example, _, now, _ = SHAPES[shape]
    runs = emphasis_runs(example)
    assert shape in runs.values(), (example, runs)
    kind, value = now.split(" ", 1)
    read = [(f.kind, f.value) for s in chunk("s", example) for f in new_facts.extract_facts(s)]
    assert (kind, value) in read
    assert all(r == (kind, value) or r[0] != kind for r in read)  # read whole, not in parts


IDENTIFIERS = [  # underscores inside words open and close nothing: read exactly as before
    "max_retries_3",
    "v1_2",
    "notes_2026-03-14.md",
    "file_2.txt",
    "x_1.2.3",
    "run_2026-03-14_a ran",
    "Build v2.4.1-rc1_final shipped",
    "release_2,048_final",  # build_1,250 was here; G2 reads it as PIECE_SHAPES says
    "__init__ and _private_2",
    "snake_case_10 and a_ 3_b",
    "/srv/v2_ and /srv/2_ end paths",
    "_--json_ _/srv/app_ _https://h.example/x_",
]


@pytest.mark.parametrize("text", IDENTIFIERS)
def test_an_underscore_inside_a_word_is_no_shape(text):
    assert emphasis_runs(text) == {}
    span = span_of(text)
    assert [f.model_dump() for f in new_facts.extract_facts(span)] == [
        f.model_dump() for f in old_facts.extract_facts(span)
    ]
    assert new_facts.normalize(text) == old_facts.normalize(text)


@pytest.mark.parametrize("shape", sorted(PIECE_SHAPES))
def test_each_g2_shape_is_read_as_the_table_says(shape):
    example, before, now, _ = PIECE_SHAPES[shape]
    (span,) = chunk("s", example)
    reference = [f.model_dump() for f in old_facts.extract_facts(span)]
    current = [f.model_dump() for f in new_facts.extract_facts(span)]
    assert [(f["kind"], f["value"]) for f in reference] == before
    assert [(f["kind"], f["value"]) for f in current] == now
    assert shape in g2_shapes(example, reference, current)
    assert new_facts.normalize(example) == old_facts.normalize(example)


def test_a_piece_beside_a_digit_of_another_script_reads_as_before():
    """Lint's tokens are [0-9a-z_]: a piece beside an Arabic-Indic digit is found, so it stays."""
    for text in (f"x{ARABIC[1]},{ARABIC[0] * 3} ok", f"x{ARABIC[1]},000 ok", f"1,{ARABIC[0] * 3}x"):
        (span,) = chunk("s", text)
        assert [f.model_dump() for f in new_facts.extract_facts(span)] == [
            f.model_dump() for f in old_facts.extract_facts(span)
        ]
        every_fact_occurs(span)


# Names and sentences that were not failing read as they did (G2): each with what the reference
# and the current copy read, equal, and normalize() equal. The first seven are the lines of the
# first G2 reproduction that were saved before it; the last eleven those of the second.
NAMES = {
    "The run processed 1,250 rows on the main branch.": [("number", "1,250")],
    "The failing job is build_1250 on the main branch.": [],
    "The failing job is build-1,250 on the main branch.": [("number", "1,250")],
    "Deploy tag is release_2,048_final for the service.": [],
    "The failing job is `build_1,250` on the main branch.": [("inline_code", "build_1,250")],
    "The threshold key is limit_0.75 in the config.": [],
    "The snapshot is backup_2099-03-02 on the bucket.": [("number", "03"), ("number", "02")],
    "max_retries_3 is set.": [],
    "v1_2 is a tag.": [],
    "notes_2026-03-14.md is a file.": [("number", "03")],
    "file_2.txt and x_1.2.3 are names.": [],
    "Build v2.4.1-rc1_final shipped.": [("version", "v2.4.1")],
    "build_1,2500 is not grouped.": [("number", "2500")],  # normalize() keeps this comma
    "a_1,25 is not grouped.": [("number", "25")],  # nor this one
    "See https://h.example/a_1,250 now.": [("url", "https://h.example/a_1,250")],
    "The queue held _1,250_ jobs at noon.": [("number", "1,250")],  # G1's reading, kept
    "The deploy finished at 2026-03-14T10:00:00Z without errors.": [
        ("date", "2026-03-14T10:00:00Z")
    ],
    "The deploy finished at 2026-03-14T10:00:00+01:00 without errors.": [
        ("date", "2026-03-14T10:00:00+01:00")
    ],
    "The snapshot tag is v2026-03-14 on the bucket.": [("version", "v2026-03-14")],
    "The new index gives a 1250x speedup on the large table.": [],
    "The measured value was 3,14 in the second run.": [("number", "3"), ("number", "14")],
    "Coverage rose to 97.5% after the change.": [("number", "97.5%")],
    "The job starts at 10:30 every morning.": [("number", "10"), ("number", "30")],
    "It failed on the 3rd attempt of 5.": [("number", "5")],
    "The plan costs $1,250 a month for 3 seats.": [("number", "$1,250"), ("number", "3")],
    "The commit is 0b153ea on the main branch.": [],
    "The host answers at 10.0.0.12 on port 8443.": [
        ("version", "10.0.0.12"),
        ("number", "8443"),
    ],
}


@pytest.mark.parametrize("text", sorted(NAMES))
def test_a_name_that_is_not_a_number_reads_as_before(text):
    (span,) = chunk("s", text)
    new = [(f.kind, f.value) for f in new_facts.extract_facts(span)]
    assert new == NAMES[text]
    every_fact_occurs(span)
    if emphasis_runs(text):  # G1's reading, pinned above; G2 takes nothing from it
        read = as_starred(text)
        assert oracle_facts(span, read) == [f.model_dump() for f in starred_facts(span)]
        return
    assert [f.model_dump() for f in new_facts.extract_facts(span)] == [
        f.model_dump() for f in old_facts.extract_facts(span)
    ]
    assert new_facts.normalize(text) == old_facts.normalize(text)


def test_the_known_limit_in_the_lint_docstring_no_longer_holds():
    """lint.py says the date in 2026-03-14T10:00:00Z or v2026-03-14 is extracted but never
    occurs as a whole token. The date with its time is read whole and found; v2026-03-14 is
    read as one version, not a date, and found. Checked here, by lint's own matching."""
    for text, read in (
        ("At 2026-03-14T10:00:00Z it ran.", [("date", "2026-03-14T10:00:00Z")]),
        ("Tag v2026-03-14 is kept.", [("version", "v2026-03-14")]),
    ):
        (span,) = chunk("s", text)
        assert [(f.kind, f.value) for f in new_facts.extract_facts(span)] == read
        assert [(f.kind, f.value) for f in old_facts.extract_facts(span)] == read
        every_fact_occurs(span)


def named(rng: random.Random) -> str:
    """A line of numbers that a comma may join to what is around them: glued to a letter or an
    underscore or not, grouped or not, a long head, a decimal, a version, a date or a flag
    before, a letter after, in code, a path, a url or emphasis, beside real numbers."""
    pieces = []
    for _ in range(rng.randint(1, 6)):
        digits = rng.choice(DIGITS) if rng.random() < 0.1 else DIGITS[0]
        head = "".join(rng.choice(digits) for _ in range(rng.randint(1, 5)))
        groups = "".join(
            rng.choice([",", ",", ".", ""]) + "".join(rng.choice(digits) for _ in range(3))
            for _ in range(rng.randint(0, 3))
        )
        tail = rng.choice(["", "", "", "x", "_final", ".", ".5", "/out.txt", " ms", "ms", ","])
        tail = rng.choice([tail, tail, "rows", ".5.6", "-rc1"])
        word = rng.choice([*WORDS, "build", "logs/batch", "/srv/run", "é", "", "v1", "1.5"])
        word = rng.choice([word, word, "--run-x", "2099-03-14", "/srv/1.2.3/a", "$"])
        joint = rng.choice(["_", "_", "", "-", "__", " ", "/", ".", ","])
        name = f"{word}{joint}{head}{groups}{tail}"
        wrap = rng.randrange(8)
        if wrap == 0:
            name = f"`{name}`"
        elif wrap == 1:
            name = f"https://h.example/{name}"
        elif wrap == 2:
            name = f"_{name}_"
        elif wrap == 3:
            name = f"{name} after {rng.randint(1, 9)},{rng.randint(100, 999)} retries"
        pieces.append(name)
    return rng.choice([" ", ". ", "\n", " | "]).join(pieces)


def test_numbers_a_comma_joins_differ_only_by_shape(matcher):
    """Lines dense with numbers a comma joins to what is around them: each differs from the
    reference only where PIECE_SHAPES says (and SHAPES, for G1's runs), and every fact the
    current copy reads occurs in its own span (same_facts checks both)."""
    rng = random.Random(20261120)
    for _ in range(3000):
        text = "\n\n".join(named(rng) for _ in range(rng.randint(1, 3)))
        same_text(text)
        spans = chunk("cap", "# Names\n\n" + text + "\n")
        content = [s for s in spans if s.kind in new_lint.CONTENT_KINDS]
        claims = [
            {"id": f"c{i}", "text": s.text, "span_ids": [s.id]} for i, s in enumerate(content)
        ]
        body = "# Names\n\n" + "\n\n".join(s.text for s in content) + "\n"
        same_lint([{"slug": "names", "text": body, "claims": claims}], spans)


def test_every_short_string_over_the_characters_urls_and_code_turn_on_gives_the_same_facts():
    """All strings of up to five characters over a: . / ` 1 and a space, and up to four over
    a richer set, so every way a url, an inline code span or a fence can start and end
    within a few characters is compared."""
    for alphabet, longest in (("a:./`1 ", 5), ("a-:/`~\n>.", 4)):
        for size in range(longest + 1):
            for chars in itertools.product(alphabet, repeat=size):
                same_facts(span_of("".join(chars)))


def test_the_one_read_matcher_answers_what_occurs_answers_for_each_value(matcher):
    """lint._Needles against lint._occurs (unchanged, the definition), and lint._in_body
    against `in`, on texts and values over small alphabets, so that values overlap, repeat,
    touch, sit inside longer tokens and share runs."""
    rng = random.Random(20261117)
    alphabets = ["ab1_ -./é\n", "a1 .-", "x" + ARABIC[:2] + " ,-", "ab c", "1.2-3 ", "é٠ -"]
    alphabets += ["-> .=\n", ".- ,A", "a-->", "- " + ARABIC[:2], "=-=- a"]  # symbols only
    for _ in range(6_000):
        alphabet = rng.choice(alphabets)
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        norms = {"".join(rng.choice(alphabet) for _ in range(rng.randint(0, 7))) for _ in range(8)}
        for _ in range(3):  # values taken from the text itself, cut at random places
            at = rng.randint(0, len(text))
            norms.add(text[at : at + rng.randint(0, 9)])
        expected = {n for n in norms if new_lint._occurs(n, text)}
        needles = new_lint._Needles(norms)
        assert needles.found_in(text) == expected, (sorted(norms), text)
        other = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        expected = {n for n in norms if new_lint._occurs(n, other)}  # the same needles again
        assert needles.found_in(other) == expected, (sorted(norms), other)
        texts = sorted(norms, key=lambda _: rng.random())
        assert new_lint._in_body(texts, text) == [t in text for t in texts], (texts, text)


def mutate(rng: random.Random, text: str) -> str:
    """A claim that keeps, drops, changes or adds facts."""
    choice = rng.randrange(6)
    if choice == 0 or not text:
        return text
    if choice == 1:
        return text[: rng.randint(0, len(text))]
    if choice == 2:
        return text.replace(rng.choice("0123456789"), rng.choice("0123456789"))
    if choice == 3:
        return text + " " + piece(rng)
    if choice == 4:
        return " ".join(text.split()[::2])
    return piece(rng) + " " + text


def scenario(rng: random.Random):
    sources = [document(rng) for _ in range(rng.randint(1, 3))]
    names = ["Quill", "Lumen", "Tessera", "Nimbus"]
    spans = []
    for i, text in enumerate(sources):
        heads = (
            f"# {rng.choice(names)}\n\n{text}\n\n## {rng.choice(names)} notes\n\n{document(rng)}"
        )
        spans += chunk(f"src{i}", heads if rng.random() < 0.6 else text)
    content = [s for s in spans if s.kind in new_lint.CONTENT_KINDS]
    pages = []
    for p in range(rng.randint(1, 3)):
        title = rng.choice([*names, "Topic", "quill-2", "LUMEN!"])
        claims, texts = [], []
        for c in range(rng.randint(0, 6)):
            cited = rng.sample(content, min(len(content), rng.randint(0, 2))) if content else []
            ids = [s.id for s in cited]
            if rng.random() < 0.15:
                ids.append("sp_unknown")
            if rng.random() < 0.15 and ids:
                ids.append(ids[0])
            pool = cited or content or [span_of(document(rng))]
            text = mutate(rng, rng.choice(pool).text)
            claims.append({"id": f"c{c}", "text": text, "span_ids": ids})
            if rng.random() < 0.85:
                texts.append(text)
        omitted = []
        for s in rng.sample(content, min(len(content), 2)):
            for f in new_facts.extract_facts(s)[:2]:
                omitted.append(
                    {"fact_id": new_lint.fact_id(s.id, f.start), "span_id": s.id, "reason": "r"}
                )
        front = rng.choice(["", f"---\ntitle: {title}\n---\n", f"---\naliases: [{title}]\n---\n"])
        body = front + f"# {title}\n\n" + "\n\n".join(texts + [document(rng)] * rng.randint(0, 1))
        page = {"slug": f"p{p}", "text": body}
        if rng.random() < 0.7:
            page |= {"claims": claims, "omitted_facts": omitted}
        pages.append(page)
    entities = [
        {"name": n, "aliases": rng.sample(["q", n.lower(), "Topic", "notes"], rng.randint(0, 2))}
        for n in rng.sample(names, rng.randint(0, 4))
    ]
    unassigned = [s.id for s in content if rng.random() < 0.2]
    return pages, spans, entities, unassigned


def test_generated_pages_checked_against_sources_give_the_same_reports(matcher):
    rng = random.Random(20261115)
    for _ in range(1200):
        pages, spans, entities, unassigned = scenario(rng)
        for span in spans:  # the invariant on the generated sources too
            every_fact_occurs(span)
        same_lint(pages, spans, entities, unassigned)


def test_many_small_sources_and_few_large_ones_give_the_same_reports(matcher):
    rng = random.Random(20261116)
    for _ in range(40):
        small = [chunk(f"s{i}", line(rng)) for i in range(rng.randint(20, 60))]
        spans = [s for group in small for s in group]
        large = chunk("big", "\n\n".join(document(rng) for _ in range(30)))
        for source in (spans, large, spans + large):
            content = [s for s in source if s.kind in new_lint.CONTENT_KINDS]
            claims = [
                {"id": f"c{i}", "text": s.text, "span_ids": [s.id, rng.choice(content).id]}
                for i, s in enumerate(content)
                if rng.random() < 0.8
            ]
            body = (
                "\n\n".join(c["text"] for c in claims)
                + "\n\n"
                + "\n\n".join(c["text"] for c in claims[:5])
            )
            same_lint([{"slug": "t", "text": body, "claims": claims}], source)
            same_lint([{"slug": "t", "text": body}], source, [{"name": "Quill"}, {"name": "Lumen"}])


def test_claims_citing_several_spans_give_the_same_reports(matcher):
    """Values from several cited spans, and values made of the end of one cited span and the
    start of the next, which are in none of them."""
    rng = random.Random(20261118)
    for _ in range(300):
        spans = chunk("src", "\n\n".join(document(rng) for _ in range(rng.randint(2, 8))))
        content = [s for s in spans if s.kind in new_lint.CONTENT_KINDS]
        if not content:
            continue
        claims = []
        for c in range(rng.randint(1, 4)):
            cited = rng.sample(content, min(len(content), rng.randint(2, 6)))
            parts = [rng.choice(s.text.split() or ["x"]) for s in cited]
            for a, b in itertools.pairwise(cited):
                parts.append(f"`{(a.text.split() or ['x'])[-1]} {(b.text.split() or ['x'])[0]}`")
                parts.append(f"{(a.text.split() or ['1'])[-1]}{(b.text.split() or ['2'])[0]}")
            text = " ".join(rng.sample(parts, len(parts)))
            claims.append({"id": f"c{c}", "text": text, "span_ids": [s.id for s in cited]})
        body = "\n\n".join(c["text"] for c in claims)
        same_lint([{"slug": "t", "text": body, "claims": claims}], spans)


def test_zz_the_count_of_cases_compared():
    """Runs last in this file; the count goes in the P1 report."""
    print(f"\nP1 differential cases: {COUNT['facts']} fact extractions, {COUNT['lint']} lint runs")
    print(f"G1 inputs that differed, by shape: {dict(sorted(DIFFERED.items()))}")
    print(f"G2 inputs that differed, by shape: {dict(sorted(DIFFERED_G2.items()))}")
    print(f"Code white space readings that differed: {dict(sorted(DIFFERED_CODE.items()))}")
    assert COUNT["facts"] > 5_000 or COUNT["facts"] == 0  # 0 when run alone with -k
