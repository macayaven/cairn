"""Extraction and lint matching must agree: every fact is found in its own span.

The lint matcher (cairn.lint._occurs) treats [0-9a-z_] as word characters around a fact. A
fact the extractor finds but the matcher cannot find in the very span it came from would
make the extractive baseline fail its own gate.
"""

import pytest

from cairn.facts import extract_facts, normalize
from cairn.lint import _occurs
from cairn.spans import chunk
from support.paths import FIXTURES_ROOT

FIXTURE = FIXTURES_ROOT / "runbook_synthetic.md"
TEXTS = [
    FIXTURE.read_text(encoding="utf-8"),
    "Started 2026-03-14T10:00:00Z.\n",
    "Started 2026-03-14T10:00:00+01:00 and ended 2026-03-14T10:05:30.250Z.\n",
    "See notes_2026-03-14.md for details.\n",
    "Build v2.4.1-rc1_final shipped; run_2026-03-14_a ran 2026-03-14.\n",
    "Released 2026-03-14 to all users.\n",
]


@pytest.mark.parametrize("text", TEXTS)
def test_every_fact_is_found_in_its_own_span(text):
    for span in chunk("s", text):
        body = normalize(span.text)
        for fact in extract_facts(span):
            for needle in fact.lines if fact.kind == "code_block" else (fact.norm,):
                assert _occurs(needle, body), (fact.kind, fact.value, span.text)


def test_an_iso_timestamp_is_one_date_fact():
    (span,) = chunk("s", "Started 2026-03-14T10:00:00Z.\n")
    assert [(f.kind, f.value) for f in extract_facts(span)] == [("date", "2026-03-14T10:00:00Z")]


def test_a_date_inside_a_filename_is_not_a_date_fact():
    (span,) = chunk("s", "See notes_2026-03-14.md for details.\n")
    assert [f for f in extract_facts(span) if f.kind == "date"] == []
