"""T2 acceptance tests (frozen): deterministic fact extractor.

Written by the orchestrator before implementation. Gold labels in
tests/gold/t2_facts_synthetic.jsonl are model-drafted, pending maintainer review.
Implementers must not edit this file, the fixture, or the gold files to make tests pass.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from cairn.facts import Fact, extract_facts, normalize
from cairn.spans import Span, chunk

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "fixtures" / "runbook_synthetic.md").read_text(encoding="utf-8")
LINES = TEXT.splitlines()
GOLD = [
    json.loads(row)
    for row in (ROOT / "gold" / "t2_facts_synthetic.jsonl").read_text(encoding="utf-8").splitlines()
    if row.strip()
]


def line_of(marker: str) -> int:
    hits = [i + 1 for i, line in enumerate(LINES) if marker in line]
    assert len(hits) == 1, f"marker {marker!r} must match exactly one line, got {hits}"
    return hits[0]


def span_at(spans: list[Span], line_no: int) -> Span:
    covering = [s for s in spans if s.start_line <= line_no <= s.end_line]
    assert len(covering) == 1, f"line {line_no} is covered by {len(covering)} spans"
    return covering[0]


@pytest.fixture(scope="module")
def spans() -> list[Span]:
    return chunk("runbook", TEXT)


@pytest.fixture(scope="module")
def facts(spans) -> dict[str, list[Fact]]:
    return {s.id: extract_facts(s) for s in spans}


def kv(spans, facts, marker: str) -> list[tuple[str, str]]:
    return [(f.kind, f.value) for f in facts[span_at(spans, line_of(marker)).id]]


def test_gold_recall_and_precision(spans, facts):
    gold_by_span: dict[str, Counter] = {}
    for g in GOLD:
        sid = span_at(spans, line_of(g["marker"])).id
        gold_by_span.setdefault(sid, Counter())[(g["kind"], g["value"])] += 1
    missed, extra, tp = [], [], 0
    for s in spans:
        pred = Counter((f.kind, f.value) for f in facts[s.id])
        gold = gold_by_span.get(s.id, Counter())
        tp += sum((pred & gold).values())
        missed += list((gold - pred).elements())
        extra += list((pred - gold).elements())
    recall = tp / (tp + len(missed))
    precision = tp / (tp + len(extra)) if tp + len(extra) else 0.0
    assert recall >= 0.95, f"recall {recall:.3f}; missed: {missed}"
    assert precision >= 0.90, f"precision {precision:.3f}; unexpected: {extra}"


def test_offsets_ids_and_norms_are_consistent(spans, facts):
    for s in spans:
        for f in facts[s.id]:
            assert s.text[f.start : f.end] == f.value
            assert f.span_id == s.id
            assert f.norm == normalize(f.value)
            if f.kind != "code_block":
                assert f.lines == ()


def test_sorted_and_deterministic(spans, facts):
    for s in spans:
        fs = facts[s.id]
        assert fs == sorted(fs, key=lambda f: (f.start, f.end))
        assert extract_facts(s) == fs


def test_code_block_is_one_atomic_fact_with_scorable_lines(spans, facts):
    code = span_at(spans, line_of("```bash"))
    fs = facts[code.id]
    assert [f.kind for f in fs] == ["code_block"]
    block = fs[0]
    assert block.value == "\n".join(LINES[code.start_line : code.end_line - 1])
    assert block.lines == (
        'quill run "<q>" --json --set cache.enabled=false',
        'lumen exec "<q>" --format jsonl --read-only',
    )


def test_inline_code_is_atomic(spans, facts):
    assert kv(spans, facts, "**Cache off:**") == [
        ("inline_code", "--set cache.enabled=false --set cache.write=false")
    ]
    timeout = kv(spans, facts, "**Timeout:**")
    assert ("inline_code", "--max-seconds 300") in timeout
    assert ("number", "300") not in timeout
    assert ("flag", "--max-seconds") not in timeout


def test_hyphenated_words_are_not_flags(spans, facts):
    for marker in ("**Safety:**", "## Copy-paste", "> Re-check", "**Cost:**"):
        assert all(k != "flag" for k, _ in kv(spans, facts, marker)), marker


def test_prose_flag_path_number_and_url(spans, facts):
    assert ("flag", "--quiet") in kv(spans, facts, "**Quiet:**")
    assert ("path", "~/.config/quill/config.toml") in kv(spans, facts, "**Config:**")
    assert ("path", "/etc/lumen/settings.json") in kv(spans, facts, "**Settings:**")
    para = kv(spans, facts, "Two checks per day")
    assert ("url", "https://example.com/quill-lumen") in para
    assert ("number", "2,500 tokens") in para


def test_dates_and_versions_are_not_numbers(spans, facts):
    assert kv(spans, facts, "How one script drives") == [
        ("date", "2026-03-14"),
        ("version", "2.4.1"),
        ("version", "0.9.0-beta.2"),
    ]


def test_normalize():
    assert normalize("  2,500\n  Tokens ") == "2500 tokens"
    assert normalize("--Set  Cache.Enabled=false") == "--set cache.enabled=false"
    assert normalize("1,234,567") == "1234567"
    assert normalize("a, 123") == "a, 123"
