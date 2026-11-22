"""Unit tests for cairn.facts (T2): one small test per fact kind and rule.

Spans are built by hand from fixture snippets, so these tests do not depend on chunk() (T1).
"""

import itertools
import json
from collections import Counter

import pytest

from cairn.facts import extract_facts, normalize
from cairn.spans import Span, SpanKind
from support.paths import TESTS_ROOT

TESTS = TESTS_ROOT

FRONT_MATTER = "---\ntitle: Quill and Lumen runbook\ndate: 2026-03-14\ntags: [runbook, cli]\n---"
PARAGRAPH = (
    "How one script drives two fictional CLIs headless. "
    "Checked on 2026-03-14 against Quill 2.4.1 and Lumen 0.9.0-beta.2."
)
TABLE = (
    "| Tool | Version | Headless |\n"
    "|---|---|---|\n"
    "| Quill | 2.4.1 | `quill run` |\n"
    "| Lumen | 0.9.0-beta.2 | `lumen exec` |"
)
INVOKE_BULLET = (
    '- **Invoke:** `quill run "<task>" --json --workdir ./scratch`.\n'
    "  - `--json` prints one event per line.\n"
    "  - `--out last.txt` writes the final message to a file."
)
CODE = (
    "```bash\n"
    "# quill\n"
    'quill run "<q>" --json --set cache.enabled=false\n'
    "# lumen\n"
    'lumen exec "<q>" --format jsonl --read-only\n'
    "```"
)
MIXED = (
    "- On 2026-03-14 run `quill` v2.4.1 with --json -q for 5 min; "
    "see ~/x.toml and https://example.com/a.\n"
    "  ```bash\n"
    "  quill run --json\n"
    "  ```"
)


def make_span(text: str, kind: SpanKind = "paragraph") -> Span:
    return Span(
        id="sp_unit",
        source_id="unit",
        kind=kind,
        heading_path=(),
        start_line=1,
        end_line=text.count("\n") + 1,
        text=text,
    )


def kv(text: str, kind: SpanKind = "paragraph") -> list[tuple[str, str]]:
    return [(f.kind, f.value) for f in extract_facts(make_span(text, kind))]


def kinds(text: str) -> set[str]:
    return {kind for kind, _ in kv(text)}


def test_code_span_yields_one_code_block_with_the_text_between_the_fences():
    span = make_span(CODE, "code")
    [fact] = extract_facts(span)
    assert fact.kind == "code_block"
    assert fact.value == "\n".join(CODE.split("\n")[1:-1])
    assert span.text[fact.start : fact.end] == fact.value
    assert (fact.span_id, fact.norm) == (span.id, normalize(fact.value))


def test_code_block_lines_are_normalised_and_skip_blank_and_comment_only_lines():
    code = "```js\n  // setup\nRUN   --Fast\n\n   # note\nnode  App.js  # trailing\n```"
    [fact] = extract_facts(make_span(code, "code"))
    assert fact.lines == ("run --fast", "node app.js # trailing")
    [fixture_block] = extract_facts(make_span(CODE, "code"))
    assert fixture_block.lines == (
        'quill run "<q>" --json --set cache.enabled=false',
        'lumen exec "<q>" --format jsonl --read-only',
    )


def test_unclosed_fence_runs_to_the_end_of_the_span():
    [fact] = extract_facts(make_span("```\ncode\n# not a heading\n\nmore code", "code"))
    assert (fact.value, fact.lines) == ("code\n# not a heading\n\nmore code", ("code", "more code"))


def test_closing_fence_needs_the_same_character_and_at_least_the_same_length():
    assert kv("```\nx\n````", "code") == [("code_block", "x")]
    assert kv("````\nx\n```\n````", "code") == [("code_block", "x\n```")]
    assert kv("```\nx\n~~~", "code") == [("code_block", "x\n~~~")]


def test_closing_fence_sits_at_the_same_quote_depth_as_the_opener():
    assert kv("```markdown\n> ```\n> quoted\n```", "code") == [("code_block", "> ```\n> quoted")]


def test_closing_fence_is_indented_at_most_three_more_than_the_opener():
    assert kv("```\n    ```\n# hidden\n```", "code") == [("code_block", "    ```\n# hidden")]
    assert kv("~~~\n    ~~~\nx\n~~~", "code") == [("code_block", "    ~~~\nx")]
    assert kv("- a:\n  ```\n  b --json\n     ```\n  for 5 min", "bullet") == [
        ("code_block", "  b --json"),
        ("number", "5 min"),
    ]


def test_empty_and_one_line_fences_have_an_empty_value():
    for text in ("```", "```bash", "```\n```", "~~~~\n~~~~"):
        [fact] = extract_facts(make_span(text, "code"))
        assert (fact.value, text[fact.start : fact.end]) == ("", ""), text


def test_fenced_block_inside_a_bullet_or_quote_is_one_atomic_code_block():
    bullet = "- Run it:\n  ```bash\n  quill run --json --max-seconds 300\n  ```\n  then read ~/out."
    assert kv(bullet, "bullet") == [
        ("code_block", "  quill run --json --max-seconds 300"),
        ("path", "~/out"),
    ]
    quote = "> Note:\n> ```\n> lumen exec --read-only\n> ```"
    assert kv(quote, "quote") == [("code_block", "> lumen exec --read-only")]


def test_inline_code_value_is_the_text_between_the_backticks():
    assert kv("## Quill — `quill run`", "heading") == [("inline_code", "quill run")]
    assert kv(INVOKE_BULLET, "bullet") == [
        ("inline_code", 'quill run "<task>" --json --workdir ./scratch'),
        ("inline_code", "--json"),
        ("inline_code", "--out last.txt"),
    ]
    [fact] = extract_facts(make_span("see `x`"))
    assert (fact.start, fact.end) == (5, 6)


def test_inline_code_runs_close_only_on_a_run_of_the_same_length_on_the_same_line():
    assert kv("use ``a `b` c`` here") == [("inline_code", "a `b` c")]
    assert kv("```x`` y```") == [("inline_code", "x`` y")]
    assert kv("a ` stray") == []
    assert kv("``unclosed`") == []
    assert kv("`split\nacross lines`") == []


def test_url_never_ends_in_sentence_punctuation():
    assert kv("Docs: https://example.com/quill-lumen.") == [
        ("url", "https://example.com/quill-lumen")
    ]
    assert kv("(see https://example.com/a?b=1), then [x](http://example.com/x).") == [
        ("url", "https://example.com/a?b=1"),
        ("url", "http://example.com/x"),
    ]
    assert kv("Seen <https://example.com/faq>? Or s3://bucket/key; ok!") == [
        ("url", "https://example.com/faq"),
        ("url", "s3://bucket/key"),
    ]


def test_path_has_a_root_and_never_ends_in_sentence_punctuation():
    assert kv("- **Config:** defaults live in ~/.config/quill/config.toml.", "bullet") == [
        ("path", "~/.config/quill/config.toml")
    ]
    assert kv("Run ./bin/up.sh or ../tools/x_y-z.py; keep ~/notes/ and /etc/lumen/a.json, ok") == [
        ("path", "./bin/up.sh"),
        ("path", "../tools/x_y-z.py"),
        ("path", "~/notes/"),
        ("path", "/etc/lumen/a.json"),
    ]


def test_prose_slashes_and_url_paths_are_not_paths():
    assert kv("and/or, TCP/IP, w/o, +/- x, a / b, cd / here, //") == []
    assert kv("see https://example.com/a/b") == [("url", "https://example.com/a/b")]


def test_iso_date():
    assert kv(FRONT_MATTER, "frontmatter") == [("date", "2026-03-14")]
    assert kv("on 2026-03-12 Lumen ignored it (2026-03-13).") == [
        ("date", "2026-03-12"),
        ("date", "2026-03-13"),
    ]
    assert "date" not in kinds("2026-3-14, 2026-03-145, 12026-03-14")


def test_version_is_three_part_with_optional_suffix_or_has_a_v_prefix():
    assert kv(PARAGRAPH) == [
        ("date", "2026-03-14"),
        ("version", "2.4.1"),
        ("version", "0.9.0-beta.2"),
    ]
    assert kv(TABLE, "table") == [
        ("version", "2.4.1"),
        ("inline_code", "quill run"),
        ("version", "0.9.0-beta.2"),
        ("inline_code", "lumen exec"),
    ]
    assert kv("pin v2 or v1.2 or v3.0.0-rc.1, then 1.2.3.4 and 1.0.0-rc-1.") == [
        ("version", "v2"),
        ("version", "v1.2"),
        ("version", "v3.0.0-rc.1"),
        ("version", "1.2.3.4"),
        ("version", "1.0.0-rc-1"),
    ]
    assert "version" not in kinds("costs $1.15 and 1.2 GB, dev2, 1.2.3a, 2.x")


def test_flag_is_a_double_or_single_dash_option():
    assert kv("- **Quiet:** add --quiet to drop progress lines.", "bullet") == [("flag", "--quiet")]
    assert kv("use -x, -rf or --format=jsonl and --no-network.") == [
        ("flag", "-x"),
        ("flag", "-rf"),
        ("flag", "--format"),
        ("flag", "--no-network"),
    ]


def test_hyphenated_words_dashes_and_markup_are_not_flags():
    for text in (
        "> Re-check versions after 30 days.",
        "## Copy-paste: read-only check",
        "- **Safety:** `--read-only` and `--no-network` give a read-only check.",
        "- **Cost:** a 40-file run cost $1.15 and peaked at 1.2 GB of RAM.",
        "pre- and post-run, x -- y, <!-- note -->, |---|:---:|",
    ):
        assert "flag" not in kinds(text), text
    assert kv("the `quill`-style and `--json`-only output") == [
        ("inline_code", "quill"),
        ("inline_code", "--json"),
    ]


def test_number_with_currency_separators_decimals_and_unit():
    cost = "- **Cost:** a 40-file run cost $1.15 and peaked at 1.2 GB of RAM."
    assert kv(cost, "bullet") == [
        ("number", "40-file"),
        ("number", "$1.15"),
        ("number", "1.2 GB"),
    ]
    assert kv("> Re-check versions after 30 days.", "quote") == [("number", "30 days")]
    assert kv("- **Settings:** /etc/lumen/settings.json, mode 0600.", "bullet") == [
        ("path", "/etc/lumen/settings.json"),
        ("number", "0600"),
    ]
    assert kv("Usage under 2,500 tokens. Docs: https://example.com/quill-lumen.") == [
        ("number", "2,500 tokens"),
        ("url", "https://example.com/quill-lumen"),
    ]
    assert [v for _, v in kv("5ms, 10 s, 3 sec, 2h, 50%, 1 TB, 7-KB, 1 day, 1 token, 3 files")] == [
        "5ms", "10 s", "3 sec", "2h", "50%", "1 TB", "7-KB", "1 day", "1 token", "3 files"
    ]  # fmt: skip
    assert [v for _, v in kv("€5 or £1,234,567.50, -7 and 3 minutes")] == [
        "€5", "£1,234,567.50", "7", "3"
    ]  # fmt: skip


def test_number_never_ends_in_punctuation_or_takes_part_of_a_word():
    assert kv("- **Timeout:** pass `--max-seconds 300`; the default is 5 min.", "bullet") == [
        ("inline_code", "--max-seconds 300"),
        ("number", "5 min"),
    ]
    assert kv("sha256, x86_64, 4x faster, 100k, 3rd, 1.5x, 2.x, 1a2b, 5 sessions") == [
        ("number", "5")
    ]


def test_ordered_list_markers_are_markup_not_numbers():
    assert kv("1. Install quill", "bullet") == []
    assert kv("2) Run it for 5 min\n   1. sub-step\n   10. then --quiet", "bullet") == [
        ("number", "5 min"),
        ("flag", "--quiet"),
    ]
    assert kv("> 3. quoted step, then 4. inline", "quote") == [("number", "4")]


def test_nothing_is_extracted_from_inside_inline_code():
    assert kv("open `https://example.com/x` now") == [("inline_code", "https://example.com/x")]


def test_every_kind_in_one_span_comes_out_sorted_by_offset():
    assert [kind for kind, _ in kv(MIXED, "bullet")] == [
        "date", "inline_code", "version", "flag", "flag", "number", "path", "url", "code_block"
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (FRONT_MATTER, "frontmatter"),
        (PARAGRAPH, "paragraph"),
        (TABLE, "table"),
        (INVOKE_BULLET, "bullet"),
        (CODE, "code"),
        (MIXED, "bullet"),
    ],
)
def test_offsets_norms_lines_order_and_claims_are_consistent(text: str, kind: SpanKind):
    span = make_span(text, kind)
    facts = extract_facts(span)
    assert facts == extract_facts(span)
    assert facts == sorted(facts, key=lambda f: (f.start, f.end))
    for fact, after in itertools.pairwise(facts):
        assert fact.end <= after.start  # no character is claimed twice
    for fact in facts:
        assert span.text[fact.start : fact.end] == fact.value
        assert (fact.span_id, fact.norm) == (span.id, normalize(fact.value))
        assert fact.kind == "code_block" or fact.lines == ()


def fixture_spans() -> list[Span]:
    """The fixture cut where tests/acceptance/test_t1_spans.py pins it, without chunk()."""
    lines = (TESTS / "fixtures" / "runbook_synthetic.md").read_text(encoding="utf-8").splitlines()
    blocks: dict[int, tuple[SpanKind, int]] = {
        1: ("frontmatter", 5),
        14: ("bullet", 16),
        30: ("table", 33),
        36: ("code", 41),
    }
    spans = []
    for n, line in enumerate(lines, start=1):
        if line.strip() and not any(s.start_line <= n <= s.end_line for s in spans):
            kind, end = blocks.get(n, ("paragraph", n))  # kind does not steer extraction
            text = "\n".join(lines[n - 1 : end])
            spans.append(
                make_span(text, kind).model_copy(update={"start_line": n, "end_line": end})
            )
    return spans


def test_fixture_spans_yield_exactly_the_gold_facts():
    lines = (TESTS / "fixtures" / "runbook_synthetic.md").read_text(encoding="utf-8").splitlines()
    gold = (TESTS / "gold" / "t2_facts_synthetic.jsonl").read_text(encoding="utf-8").splitlines()
    spans = fixture_spans()
    expected: dict[int, Counter] = {s.start_line: Counter() for s in spans}
    for row in map(json.loads, filter(str.strip, gold)):
        [n] = [i for i, line in enumerate(lines, start=1) if row["marker"] in line]
        [span] = [s for s in spans if s.start_line <= n <= s.end_line]
        expected[span.start_line][row["kind"], row["value"]] += 1
    actual = {s.start_line: Counter((f.kind, f.value) for f in extract_facts(s)) for s in spans}
    assert actual == expected


def test_normalize_collapses_whitespace_strips_and_casefolds():
    assert normalize("  Quill  RUN\n\t--JSON  ") == "quill run --json"


def test_normalize_removes_only_thousands_separators():
    assert normalize("$1,200.50 and 2,500 Tokens") == "$1200.50 and 2500 tokens"
    assert normalize("1,234,567") == "1234567"
    assert normalize("12,3456 or 1,2,3 or a, 123") == "12,3456 or 1,2,3 or a, 123"
