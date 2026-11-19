"""Unit tests for cairn.gate.verbatim_gate (T5, T5d): a claim is a passage, whole.

The inner TDD loop under tests/acceptance/test_t5d_a_claim_is_a_passage_whole.py. Candidates
are built by hand from Span objects or chunk() output, so no store is needed.
"""

from pathlib import Path

import pytest

from cairn import gate
from cairn.compiler import CandidatePage, extractive, render_body
from cairn.gate import _not_verbatim, _refusals, _structure, strict_lint_gate, verbatim_gate
from cairn.spans import Span, chunk
from cairn.store import Claim

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


# The structure detector: which prose is taken line for line rather than read as words

STRUCTURAL = {
    "indented four columns": "    dangerous()",
    "indented with a tab": "\tdangerous()",
    "a table row": "| a | b |",
    "a table delimiter row": "|---|:--:|",
    "a delimiter row without outer pipes": "--- | ---",
    "a rule of dashes": "---",
    "a rule of spaced stars": "* * *",
    "a rule of underscores": "___",
    "a heading underline of equals": "===",
    "a heading underline of two dashes": "--",
    "an underline with a space after it": "=== ",
    "two dashes with a space after them": "-- ",
    "a hard break of two spaces": "Alpha.  ",
    "a hard break of a backslash": "Alpha.\\",
    "an html tag": "<details>",
    "a closing html tag": "</details>",
    "an html comment": "<!-- hidden -->",
    "a link-reference definition": "[d]: https://example.test/docs",
    "a footnote definition": "[^1]: A note.",
    "a code fence": "```bash",
    "a quoted table row": "> | a | b |",
    "a quoted indented line": ">     dangerous()",
    "an item holding a rule": "- ***",
    "a list marker alone": "*",
    "a list number alone": "1.",
}
PLAIN = ("Plain one.", "Plain two.")


@pytest.mark.parametrize("line", STRUCTURAL.values(), ids=STRUCTURAL)
@pytest.mark.parametrize("where", [1, 2, 3], ids=["first", "middle", "last"])
def test_each_kind_of_structure_is_found_on_any_line(line, where):
    lines = [*PLAIN]
    lines.insert(where - 1, line)
    assert _structure(lines) == where


NEAR_MISSES = {
    "a sentence that holds a pipe": ["Use a | b here."],
    "a sentence that holds a dash": ["Pass -- to stop - then go."],
    "a sentence that holds a backslash": ["Escape \\* with a backslash."],
    "a line indented three columns": ["   three()"],
    "two spaces inside a line": ["Alpha.  Beta."],
    "one trailing space": ["Alpha. "],
    "a less-than sign": ["a < b holds."],
    "a reference link used, not defined": ["See [the docs][d] now."],
    "a line that opens with brackets": ["[Draft] Ship it: soon."],
    "an item's continuation": ["- Install it", "  and run it."],
    "a loose list's child": ["- Install it.", "", "    - Then run it."],
    "a numbered item's continuation": ["10. Ship it", "    when it is ready."],
    "bold dashes in a sentence": ["Keep **--delete** off."],
    "a quote continued": ["> Keep it off until", "> the dry run is clean."],
}


@pytest.mark.parametrize("lines", NEAR_MISSES.values(), ids=NEAR_MISSES)
def test_near_misses_stay_plain(lines):
    assert _structure(lines) is None


# The gate


def span(span_id: str, text: str, kind: str = "paragraph", path=("T",)) -> Span:
    return Span(
        id=span_id,
        source_id="cap_000000000000",
        kind=kind,
        heading_path=path,
        start_line=1,
        end_line=text.count("\n") + 1,
        text=text,
    )


PROSE = span(
    "sp_p", "Drift mirrors two buckets. Deleting the mirror is safe when\nthe run is clean."
)
AGAIN = span(
    "sp_a", "Drift mirrors two buckets. Deleting the mirror is safe when the run is clean."
)
LONGER = span("sp_l", f"{AGAIN.text} Ask the owner first.")
BULLET = span("sp_b", "- **Run:** sync it.\n  - Nested detail here.", "bullet")
NUMBERED = span("sp_n", "1. First step here.", "bullet")
QUOTE = span("sp_q", "> Keep it off until\n> the dry run is clean.", "quote")
BLANK_QUOTE = span("sp_bq", ">\n> Hello.", "quote")
DASHED = span("sp_d", "Pass the flag - then sync.")
CODE = span("sp_c", "```bash\n# sync both\ndrift sync  --src ./a\n```", "code")
TABLE = span("sp_t", "| Tool | Version |\n|---|---|\n| Drift | 1.8.2 |", "table")
OPEN_FENCE = span("sp_f", "- See this.\n  ```bash", "bullet")
HARD_BREAK = span("sp_hb", "Alpha.  \nBeta.")
RULED = span("sp_ru", "Before.\n---\nAfter.")
WRAPPED_YEAR = span("sp_v", "- It shipped in\n  2020. Then it grew.", "bullet")
ALL = {
    s.id: s
    for s in (
        *(PROSE, AGAIN, LONGER, BULLET, NUMBERED, QUOTE, BLANK_QUOTE, DASHED, CODE, TABLE),
        *(OPEN_FENCE, HARD_BREAK, RULED, WRAPPED_YEAR),
    )
}


def page(*claims: tuple[str, tuple[Span, ...]]) -> CandidatePage:
    made = tuple(
        Claim(id=f"cl_{n:016x}", text=text, span_ids=tuple(s.id for s in cited))
        for n, (text, cited) in enumerate(claims)
    )
    return CandidatePage(slug="t", title="T", body=render_body("T", made, ALL), claims=made)


def refusals(*claims) -> list[str]:
    result = verbatim_gate(page(*claims), ALL)
    return [v for v in result.violations if v.startswith("grounding.not_verbatim: ")]


ACCEPTED = {
    "a paragraph as it is": (PROSE.text, (PROSE,)),
    "a paragraph wrapped elsewhere": (
        "Drift mirrors two buckets.\nDeleting the mirror is safe when the run is clean.",
        (PROSE,),
    ),
    "a paragraph with its white space doubled, tabbed and CRLF": (
        "Drift  mirrors\ttwo buckets. Deleting the mirror\r\nis safe when the run is clean.",
        (PROSE,),
    ),
    "a paragraph with white space at its two ends": (f"\n  {PROSE.text} \n\n", (PROSE,)),
    "a fold of two passages wrapped apart": (AGAIN.text, (PROSE, AGAIN)),
    "a bullet with its markers": (BULLET.text, (BULLET,)),
    "a bullet's continuation wrapped elsewhere": (
        "- **Run:**\n  sync it.\n  - Nested detail\n    here.",
        (BULLET,),
    ),
    "a numbered item": (NUMBERED.text, (NUMBERED,)),
    "a number wrapped to a line's start, as it is": (WRAPPED_YEAR.text, (WRAPPED_YEAR,)),
    "a number that wrapped to a line's start, unwrapped": (
        "- It shipped in 2020. Then it grew.",
        (WRAPPED_YEAR,),
    ),
    "a quote with its markers": (QUOTE.text, (QUOTE,)),
    "a quote with a blank line": (BLANK_QUOTE.text, (BLANK_QUOTE,)),
    "a dash inside a line, wrapped before another word": (
        "Pass the flag -\nthen sync.",
        (DASHED,),
    ),
    "a code block": (CODE.text, (CODE,)),
    "a code block, white space at the ends of its lines aside": (
        "\n".join(f"{line} \t" for line in CODE.text.split("\n")),
        (CODE,),
    ),
    "a code block with blank lines around it": (f"\n\n{CODE.text}\n\n", (CODE,)),
    "a table": (TABLE.text, (TABLE,)),
    "a hard line break, whole": (HARD_BREAK.text, (HARD_BREAK,)),
    "a rule, whole, with blank lines around it": (f"\n{RULED.text}\n", (RULED,)),
}


@pytest.mark.parametrize(("text", "cited"), ACCEPTED.values(), ids=ACCEPTED)
def test_a_claim_that_is_each_passage_it_cites_whole_passes(text, cited):
    assert refusals((text, cited)) == []


REFUSED = {
    "one sentence": ("Drift mirrors two buckets.", (PROSE,)),
    "the last sentence": ("Deleting the mirror is safe when the run is clean.", (PROSE,)),
    "sentences reordered": (
        "Deleting the mirror is safe when the run is clean. Drift mirrors two buckets.",
        (PROSE,),
    ),
    "a sentence repeated": ("Drift mirrors two buckets. Drift mirrors two buckets.", (PROSE,)),
    "a sentence added": (f"{PROSE.text} Ask the owner first.", (PROSE,)),
    "a fold where one passage says more": (AGAIN.text, (AGAIN, LONGER)),
    "two passages joined": (f"{PROSE.text}\n\n{QUOTE.text}", (PROSE, QUOTE)),
    "a passage and a block joined": (f"{PROSE.text}\n\n{CODE.text}", (PROSE, CODE)),
    "two blocks joined": (f"{CODE.text}\n\n{TABLE.text}", (CODE, TABLE)),
    "a passage cited beside one it is not": (PROSE.text, (PROSE, QUOTE)),
    "a word changed": (PROSE.text.replace("two", "three"), (PROSE,)),
    "case changed": (PROSE.text.lower(), (PROSE,)),
    "a full stop dropped": (PROSE.text.removesuffix("."), (PROSE,)),
    "a homoglyph": (PROSE.text.replace("buckets", "bucket" + chr(0x0455)), (PROSE,)),
    "a bidirectional override": (chr(0x202E) + PROSE.text, (PROSE,)),
    "a zero-width joiner": (PROSE.text.replace("two ", "two" + chr(0x200D) + " "), (PROSE,)),
    "a no-break space": (PROSE.text.replace("two ", "two" + chr(0x00A0)), (PROSE,)),
    "a line separator": (PROSE.text.replace("two ", "two" + chr(0x2028)), (PROSE,)),
    "a list marker dropped": ("**Run:** sync it.\n  - Nested detail here.", (BULLET,)),
    "a nested marker dropped": ("- **Run:** sync it.\n  Nested detail here.", (BULLET,)),
    "a nested item joined to its parent's line": (
        "- **Run:** sync it. - Nested detail here.",
        (BULLET,),
    ),
    "a list marker added": (f"- {PROSE.text}", (PROSE,)),
    "a list marker changed": ("* **Run:** sync it.\n  - Nested detail here.", (BULLET,)),
    "a line broken before a dash, which reads as a marker": (
        "Pass the flag\n- then sync.",
        (DASHED,),
    ),
    "a list number dropped": ("First step here.", (NUMBERED,)),
    "a list number changed": ("2. First step here.", (NUMBERED,)),
    "a quote's markers dropped": ("Keep it off until\nthe dry run is clean.", (QUOTE,)),
    "a quote rewrapped, its second marker inside a line": (
        "> Keep it off until > the dry run is clean.",
        (QUOTE,),
    ),
    "a quote rewrapped, its marker moved": (
        "> Keep it off\n> until the dry run is clean.",
        (QUOTE,),
    ),
    "a quote's blank line dropped": ("> Hello.", (BLANK_QUOTE,)),
    "a quote's blank line put before a line without its marker": (">\nHello.", (BLANK_QUOTE,)),
    "a quote marker alone": (">", (BLANK_QUOTE,)),
    "a hard line break added": (
        "Drift mirrors two buckets.  \nDeleting the mirror is safe when the run is clean.",
        (PROSE,),
    ),
    "a hard line break at the end": (f"{PROSE.text}  ", (PROSE,)),
    "a rule added": (
        "Drift mirrors two buckets.\n---\nDeleting the mirror is safe when the run is clean.",
        (PROSE,),
    ),
    "indentation that makes code": (f"    {PROSE.text}", (PROSE,)),
    "a code line alone": ("drift sync  --src ./a", (CODE,)),
    "spacing changed inside a code line": (CODE.text.replace("  ", " "), (CODE,)),
    "a code block indented": ("\n".join(f"  {line}" for line in CODE.text.split("\n")), (CODE,)),
    "a closing fence left out": (CODE.text.removesuffix("\n```"), (CODE,)),
    "a table row alone": ("| Drift | 1.8.2 |", (TABLE,)),
    "a hard line break removed": ("Alpha. Beta.", (HARD_BREAK,)),
    "a hard line break's spaces changed": ("Alpha.   \nBeta.", (HARD_BREAK,)),
    "a rule flattened": ("Before. --- After.", (RULED,)),
    "a sentence beside a rule": ("After.", (RULED,)),
    "a claim that cites nothing": (PROSE.text, ()),
}


@pytest.mark.parametrize(("text", "cited"), REFUSED.values(), ids=REFUSED)
def test_anything_but_each_passage_whole_is_refused(text, cited):
    (violation,) = refusals((text, cited))
    assert violation.startswith("grounding.not_verbatim: cl_0000000000000000: ")


def test_a_claim_that_cites_only_ids_the_gate_was_not_given_is_refused_without_them():
    for cited in ((), ("sp_made up by a model",)):
        for text in (PROSE.text, "", " \n "):
            claim = Claim(id="cl_0000000000000000", text=text, span_ids=cited)
            violation = _not_verbatim(claim, ALL)
            assert violation is not None and "no passage" in violation
            assert "made up" not in violation


def test_an_id_the_gate_was_not_given_is_passed_over_as_before():
    # The strict gate reports it (provenance.unknown_passage); this rule judges the others.
    claim = Claim(id="cl_0000000000000000", text=PROSE.text, span_ids=("sp_x", PROSE.id))
    assert _not_verbatim(claim, ALL) is None
    assert _refusals((claim,), ALL) == []


# What a refusal says


def test_a_refusal_tells_the_model_what_to_do_and_names_the_passage_never_text():
    (violation,) = refusals(("Drift mirrors two buckets.", (PROSE,)))
    said = violation.removeprefix("grounding.not_verbatim: cl_0000000000000000: ")
    assert said.startswith("a claim must be the whole of each passage it cites")
    assert PROSE.id in said
    assert "Drift" not in said and "buckets" not in said


def test_a_fold_is_refused_by_the_first_passage_it_is_not():
    (violation,) = refusals((AGAIN.text, (PROSE, AGAIN, LONGER, QUOTE)))
    assert LONGER.id in violation and QUOTE.id not in violation


def test_structure_added_to_a_passage_is_named_by_its_line():
    (violation,) = refusals(
        (
            "Drift mirrors two buckets.\n\nDeleting the mirror is safe when  \nthe run is clean.",
            (PROSE,),
        )
    )
    assert "line 3" in violation and PROSE.id in violation and "Deleting" not in violation


def test_a_fence_the_passage_leaves_open_passes_since_the_body_closes_it():
    assert refusals((OPEN_FENCE.text, (OPEN_FENCE,))) == []  # review 7.2


def test_one_violation_per_claim():
    claims = [("Made up.\nAlso made up.", (PROSE,)), (PROSE.text, (PROSE,)), ("x", (CODE,))]
    found = refusals(*claims)
    assert [v.split(": ")[1] for v in found] == ["cl_0000000000000000", "cl_0000000000000002"]


def test_the_gate_adds_to_the_strict_gate_and_keeps_its_metrics():
    candidate = page(("Drift mirrors three buckets.", (PROSE,)))
    strict, verbatim = strict_lint_gate(candidate, ALL), verbatim_gate(candidate, ALL)
    assert verbatim.metrics == strict.metrics
    assert verbatim.violations[: len(strict.violations)] == strict.violations
    assert not verbatim.passed


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*_synthetic.md")), ids=lambda p: p.name)
def test_the_extractive_draft_of_every_fixture_passes_as_the_strict_gate_does(path):
    spans = [
        s
        for s in chunk("cap_000000000000", path.read_text(encoding="utf-8"))
        if s.kind not in ("heading", "frontmatter")
    ]
    claims = extractive("T", spans).claims
    by_id = {s.id: s for s in spans}
    candidate = CandidatePage(
        slug="t", title="T", body=render_body("T", claims, by_id), claims=claims
    )
    result = verbatim_gate(candidate, by_id)
    assert result.passed, result.violations
    assert result == strict_lint_gate(candidate, by_id)


# What the gate says of itself


def test_the_docstring_says_the_rule_and_what_it_cannot_do():
    module = " ".join(gate.__doc__.split())
    function = " ".join(verbatim_gate.__doc__.split())
    for doc in (module, function):
        assert "a claim is one passage, whole" in doc
        assert "it keeps a passage whole; it does not keep two passages together" in doc
    for gone in ("sentences(", "ABBREVIATIONS", "whole sentences", "neighbouring sentence"):
        assert gone not in module and gone not in function, gone


def test_the_sentence_machinery_is_gone():
    for name in ("sentences", "_paired", "_ends_sentence", "_Prose", "ABBREVIATIONS"):
        assert not hasattr(gate, name), name


# Cost: linear in the text of the claims and the passages they cite. The same shape at n and at
# 8n, the work counted (every character handed to the gate's readers), no clock: linear work
# grows about 8 times, work that reads a passage once per claim about 64 times.

READERS = ("_text", "_rows", "_structure")
TIMES = 8


def work(monkeypatch, claims, spans, judge=_refusals) -> tuple[int, list[str]]:
    """(characters the gate's readers took in, the refusals) for one call of `judge`."""
    read = 0

    def counted(original):
        def reader(lines, *args, **kwargs):
            nonlocal read
            lines = list(lines)
            read += sum(len(line) + 1 for line in lines)
            return original(lines, *args, **kwargs)

        return reader

    for name in READERS:
        monkeypatch.setattr(gate, name, counted(getattr(gate, name)))
    refused = judge(claims, spans)
    monkeypatch.undo()
    return read, refused


def assert_linear(monkeypatch, make, n: int, refused: bool) -> None:
    (small, first), (large, second) = (work(monkeypatch, *make(m)) for m in (n, TIMES * n))
    assert bool(first) == bool(second) == refused, (first[:1], second[:1])
    assert small > 0 and large <= (TIMES + 1) * small, (
        f"{TIMES}x the input read {large / small:.1f}x"
    )


def test_a_claim_that_cites_many_passages_that_say_it_is_linear(monkeypatch):
    def make(n: int):
        text = "Drift mirrors two buckets. Deleting the mirror is safe."
        spans = {f"sp_{k:016x}": span(f"sp_{k:016x}", text) for k in range(n)}
        return (Claim(id="cl_0000000000000000", text=text, span_ids=tuple(spans)),), spans

    assert_linear(monkeypatch, make, 500, refused=False)


def test_many_claims_that_cite_one_long_passage_read_it_once(monkeypatch):
    def make(n: int):
        words = [f"Word{k}." for k in range(n)]
        long = span("sp_0000000000000000", " ".join(words))
        claims = tuple(
            Claim(id=f"cl_{k:016x}", text=word, span_ids=(long.id,)) for k, word in enumerate(words)
        )
        return claims, {long.id: long}

    assert_linear(monkeypatch, make, 500, refused=True)


def test_a_long_claim_of_many_marked_lines_is_linear(monkeypatch):
    def make(n: int):
        text = "\n".join(f"- Item {k} here.\n  > quoted {k}" for k in range(n))
        long = span("sp_0000000000000000", text, "bullet")
        return (Claim(id="cl_0000000000000000", text=text, span_ids=(long.id,)),), {long.id: long}

    assert_linear(monkeypatch, make, 500, refused=False)


def test_long_blocks_are_linear(monkeypatch):
    def make(n: int):
        code = span("sp_c", "```\n" + "\n".join(f"line {k}" for k in range(n)) + "\n```", "code")
        hard = span("sp_h", "\n".join(f"Line {k}.  " for k in range(n)) + "\nEnd.")
        claims = (
            Claim(id="cl_0000000000000000", text=code.text, span_ids=(code.id,)),
            Claim(id="cl_0000000000000001", text=hard.text, span_ids=(hard.id,)),
        )
        return claims, {code.id: code, hard.id: hard}

    assert_linear(monkeypatch, make, 500, refused=False)


def test_the_count_tells_a_passage_read_once_per_claim(monkeypatch):
    """The counter itself: judging each claim with its own readings, as a gate without the
    shared readings would, reads the long passage once per claim, and the ratio shows it."""

    def per_claim(claims, spans):
        return [v for c in claims if (v := _not_verbatim(c, spans))]

    def make(n: int):
        long = span("sp_0000000000000000", " ".join(f"Word{k}." for k in range(n)))
        claims = tuple(
            Claim(id=f"cl_{k:016x}", text=f"Word{k}.", span_ids=(long.id,)) for k in range(n)
        )
        return claims, {long.id: long}

    (small, _), (large, _) = (work(monkeypatch, *make(m), per_claim) for m in (500, TIMES * 500))
    assert large > 4 * TIMES * small
