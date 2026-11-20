"""A span whose text a source span holds takes that span's facts under its own id: the same
facts extract_facts gives it."""

import pytest

from cairn.facts import extract_facts
from cairn.lint import _Source
from cairn.spans import Span

TEXTS = [
    "Port 8080 on `orbit-7` since 2024-03-01, see https://example.test/a.",
    "Run `make  fern --fast` then set FERN_MODE=calm.",
    "```\nfern  start\n# note\n```",
    "Nothing of note here.",
]


def _span(span_id: str, text: str) -> Span:
    return Span(
        id=span_id,
        source_id="src",
        kind="paragraph",
        heading_path=(),
        start_line=1,
        end_line=1 + text.count("\n"),
        text=text,
    )


@pytest.mark.parametrize("text", TEXTS)
def test_a_span_repeating_a_source_text_has_the_facts_extract_facts_gives(text):
    source = _Source([_span("src#1", text)], [])
    other = _span("page#c1", text)
    assert source.facts_of(other) == extract_facts(other)
    assert all(f.span_id == "page#c1" for f in source.facts_of(other))


def test_a_span_with_a_new_text_is_extracted():
    source = _Source([_span("src#1", TEXTS[0])], [])
    other = _span("page#c2", "Version 3.2.1 of `kestrel`.")
    assert source.facts_of(other) == extract_facts(other)


def test_the_source_span_itself_gets_a_list_it_may_change():
    source = _Source([_span("src#1", TEXTS[0])], [])
    got = source.facts_of(source.spans[0])
    got.clear()
    assert source.facts["src#1"] == extract_facts(source.spans[0])
