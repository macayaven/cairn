"""T1 acceptance tests (frozen): section-aware span chunker.

Written by the orchestrator before implementation. Implementers must not edit this file,
the fixture, or the gold files to make tests pass.
"""

import itertools
import re
from pathlib import Path

import pytest

from cairn.spans import Span, chunk

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runbook_synthetic.md"
TEXT = FIXTURE.read_text(encoding="utf-8")
LINES = TEXT.splitlines()
H1 = "Quill and Lumen runbook"
QUILL = "Quill — `quill run`"
LUMEN = "Lumen — `lumen exec`"
COPY = "Copy-paste: read-only check"


def line_of(marker: str, lines: list[str] = LINES) -> int:
    hits = [i + 1 for i, line in enumerate(lines) if marker in line]
    assert len(hits) == 1, f"marker {marker!r} must match exactly one line, got {hits}"
    return hits[0]


def span_at(spans: list[Span], line_no: int) -> Span:
    covering = [s for s in spans if s.start_line <= line_no <= s.end_line]
    assert len(covering) == 1, f"line {line_no} is covered by {len(covering)} spans"
    return covering[0]


@pytest.fixture(scope="module")
def spans() -> list[Span]:
    return chunk("runbook", TEXT)


def test_every_nonblank_line_is_in_exactly_one_span(spans):
    for n, line in enumerate(LINES, start=1):
        covering = [s for s in spans if s.start_line <= n <= s.end_line]
        if line.strip():
            assert len(covering) == 1, (n, line, covering)


def test_spans_are_ordered_disjoint_and_trimmed(spans):
    for a, b in itertools.pairwise(spans):
        assert a.end_line < b.start_line
    for s in spans:
        assert s.start_line <= s.end_line
        assert LINES[s.start_line - 1].strip()
        assert LINES[s.end_line - 1].strip()


def test_span_text_is_the_exact_source_lines(spans):
    for s in spans:
        assert s.text == "\n".join(LINES[s.start_line - 1 : s.end_line])
        assert s.source_id == "runbook"


def test_kinds(spans):
    front = span_at(spans, 1)
    assert (front.kind, front.end_line) == ("frontmatter", 5)
    assert span_at(spans, line_of(f"# {H1}")).kind == "heading"
    assert span_at(spans, line_of("How one script drives")).kind == "paragraph"
    assert span_at(spans, line_of("> Re-check versions")).kind == "quote"
    assert span_at(spans, line_of("**Timeout:**")).kind == "bullet"
    assert span_at(spans, line_of("| Tool | Version")).kind == "table"
    assert span_at(spans, line_of("```bash")).kind == "code"
    assert span_at(spans, line_of("Two checks per day")).kind == "paragraph"


def test_fenced_block_is_one_atomic_code_span(spans):
    open_line = line_of("```bash")
    close_line = next(
        n for n in range(open_line + 1, len(LINES) + 1) if LINES[n - 1].strip() == "```"
    )
    code = span_at(spans, open_line)
    assert (code.kind, code.start_line, code.end_line) == ("code", open_line, close_line)
    assert span_at(spans, line_of("# quill")) == code
    assert span_at(spans, line_of("# lumen")) == code
    assert all(s.kind != "heading" for s in spans if open_line <= s.start_line <= close_line)


def test_heading_paths_follow_sections(spans):
    assert span_at(spans, 1).heading_path == ()
    assert span_at(spans, line_of("How one script drives")).heading_path == (H1,)
    cache_off = span_at(spans, line_of("--set cache.enabled=false --set cache.write"))
    assert cache_off.heading_path == (H1, QUILL)
    assert span_at(spans, line_of("Lumen has no cache store")).heading_path == (H1, LUMEN)
    assert span_at(spans, line_of("| Tool | Version")).heading_path == (H1, LUMEN)
    assert span_at(spans, line_of("```bash")).heading_path == (H1, COPY)
    assert span_at(spans, line_of("Two checks per day")).heading_path == (H1, COPY)


def test_heading_span_path_ends_with_its_own_text(spans):
    assert span_at(spans, line_of(f"# {H1}")).heading_path == (H1,)
    lumen = span_at(spans, line_of(f"## {LUMEN}"))
    assert (lumen.kind, lumen.heading_path) == ("heading", (H1, LUMEN))
    assert span_at(spans, line_of("## Notes")).heading_path == (H1, "Notes")


def test_nested_items_stay_with_their_parent_bullet(spans):
    parent = span_at(spans, line_of('**Invoke:** `quill run "<task>"'))
    assert parent.kind == "bullet"
    assert span_at(spans, line_of("prints one event per line")) == parent
    assert span_at(spans, line_of("writes the final message")) == parent
    assert span_at(spans, line_of("**Timeout:**")) != parent


def test_heading_levels_pop_correctly():
    text = "# A\n## B\ntext b\n### C\ntext c\n## D\ntext d\n# E\ntext e\n"
    by_text = {s.text: s for s in chunk("levels", text)}
    assert by_text["text b"].heading_path == ("A", "B")
    assert by_text["text c"].heading_path == ("A", "B", "C")
    assert by_text["text d"].heading_path == ("A", "D")
    assert by_text["text e"].heading_path == ("E",)


def test_unclosed_fence_runs_to_end_and_hides_headings():
    text = "# Top\n```\ncode\n# not a heading\n\nmore code\n"
    spans = chunk("unclosed", text)
    code = [s for s in spans if s.kind == "code"]
    assert len(code) == 1
    assert (code[0].start_line, code[0].end_line) == (2, 6)
    assert [s.kind for s in spans] == ["heading", "code"]


def test_ids_have_the_documented_format_and_are_unique(spans):
    ids = [s.id for s in spans]
    assert all(re.fullmatch(r"sp_[0-9a-f]{16}", i) for i in ids)
    assert len(set(ids)) == len(ids)


def test_identical_text_in_one_section_gets_distinct_ids(spans):
    dups = [s for s in spans if s.text == "- **Duplicate:** keep this line."]
    assert len(dups) == 2
    assert dups[0].id != dups[1].id


def test_ids_are_stable_across_runs(spans):
    assert [s.id for s in chunk("runbook", TEXT)] == [s.id for s in spans]


def test_ids_survive_edits_elsewhere_including_line_shifts(spans):
    edited_lines = list(LINES)
    i = line_of("Lumen has no cache store") - 1
    edited_lines[i] = edited_lines[i].replace("no cache store", "no cache store at all")
    j = line_of("How one script drives")  # insert after this paragraph; later lines shift by 2
    edited_lines[j:j] = ["", "An inserted paragraph that shifts every later line."]
    edited = chunk("runbook", "\n".join(edited_lines) + "\n")
    edited_by_id = {s.id: s for s in edited}

    quill_spans = [s for s in spans if s.heading_path[:2] == (H1, QUILL)]
    assert quill_spans
    for s in quill_spans:
        twin = edited_by_id.get(s.id)
        assert twin is not None, f"id changed for unchanged span {s.text!r}"
        assert twin.text == s.text
        assert twin.start_line == s.start_line + 2

    old_lumen = span_at(spans, line_of("Lumen has no cache store"))
    assert old_lumen.id not in edited_by_id


def test_same_text_in_another_source_gets_another_id():
    a = chunk("one", "# T\nsame text\n")
    b = chunk("two", "# T\nsame text\n")
    assert [s.text for s in a] == [s.text for s in b]
    assert {s.id for s in a}.isdisjoint({s.id for s in b})
