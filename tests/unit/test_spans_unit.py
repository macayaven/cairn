"""Unit tests for the span chunker: the inner TDD loop under tests/acceptance/test_t1_spans.py."""

import pytest

from cairn.spans import chunk


def layout(text: str) -> list[tuple[str, int, int]]:
    return [(s.kind, s.start_line, s.end_line) for s in chunk("doc", text)]


def test_blank_text_has_no_spans():
    assert chunk("doc", "") == []
    assert chunk("doc", "\n   \n\t\n") == []


def test_a_line_of_prose_is_a_paragraph_span_with_a_pinned_id():
    [span] = chunk("doc", "hello world\n")
    assert (span.kind, span.start_line, span.end_line) == ("paragraph", 1, 1)
    assert (span.text, span.source_id, span.heading_path) == ("hello world", "doc", ())
    # sha256 over json.dumps([source_id, heading_path, text, occurrence]); pinned so that a
    # change to the id scheme, which would orphan stored citations, is always deliberate.
    assert span.id == "sp_72300c7a54d9dfb2"


def test_consecutive_lines_form_one_paragraph_and_blank_lines_separate_spans():
    text = "one\r\ntwo\r\n\r\n  \r\nthree\r\n"
    assert layout(text) == [("paragraph", 1, 2), ("paragraph", 5, 5)]
    assert chunk("doc", text)[0].text == "one\ntwo"


@pytest.mark.parametrize(
    ("line", "title"),
    [
        ("# Title", "Title"),
        ("###### Six", "Six"),
        ("##   Padded   ", "Padded"),
        ("## Closed ##", "Closed"),
        ("## C#", "C#"),
        ("   # Indented", "Indented"),
    ],
)
def test_atx_heading_span_path_is_its_own_title(line, title):
    [span] = chunk("doc", line)
    assert (span.kind, span.heading_path) == ("heading", (title,))


@pytest.mark.parametrize("line", ["#NoSpace", "####### Seven", "    # indented", "\t# tabbed"])
def test_lines_that_are_not_headings(line):
    assert layout(line) == [("paragraph", 1, 1)]


def test_heading_interrupts_a_paragraph_and_scopes_what_follows():
    spans = chunk("doc", "intro\n# A\nbody\n## B\nmore\n")
    assert [(s.kind, s.heading_path) for s in spans] == [
        ("paragraph", ()),
        ("heading", ("A",)),
        ("paragraph", ("A",)),
        ("heading", ("A", "B")),
        ("paragraph", ("A", "B")),
    ]


def test_front_matter_is_one_atomic_span_closed_by_the_next_dashes():
    text = "---\ntitle: x\n\n# a yaml comment\n---\nbody\n"
    assert layout(text) == [("frontmatter", 1, 5), ("paragraph", 6, 6)]
    assert chunk("doc", text)[0].heading_path == ()


def test_unclosed_or_later_dashes_are_not_front_matter():
    assert layout("---\ntitle: x\n") == [("paragraph", 1, 2)]
    assert layout("intro\n\n---\na: 1\n---\n") == [("paragraph", 1, 1), ("paragraph", 3, 5)]


def test_fence_is_one_atomic_code_span_that_keeps_its_blank_lines():
    text = "intro\n~~~\n# not a heading\n\n- not a bullet\n~~~\nafter\n"
    assert layout(text) == [("paragraph", 1, 1), ("code", 2, 6), ("paragraph", 7, 7)]


def test_fence_closes_on_the_same_char_at_least_as_long_with_no_info_string():
    text = "````md\n```\n~~~~\n```` not a closer\n`````\nafter\n"
    assert layout(text) == [("code", 1, 5), ("paragraph", 6, 6)]


def test_fence_opens_with_at_most_three_spaces_of_indentation():
    assert layout("   ```\n   code\n   ```\n") == [("code", 1, 3)]
    assert layout("    ```\n    code\n") == [("paragraph", 1, 2)]


def test_a_closing_fence_is_indented_at_most_three_spaces_more_than_its_opener():
    # Four spaces deeper is fence content; three deeper, or any shallower, closes the fence.
    assert layout("~~~\n    ~~~\n~~~\nafter\n") == [("code", 1, 3), ("paragraph", 4, 4)]
    assert layout(" ```\n    ```\nafter\n") == [("code", 1, 2), ("paragraph", 3, 3)]
    assert layout("   ```\n```\nafter\n") == [("code", 1, 2), ("paragraph", 3, 3)]
    # The limit is relative to the opener, not absolute, so fences in items work at any depth.
    assert layout("- a\n  ```\n      ```\n  ```\n- b\n") == [("bullet", 1, 4), ("bullet", 5, 5)]
    nested = "- a\n  - b\n    ```\n    x\n    ```\n- c\n"
    assert layout(nested) == [("bullet", 1, 5), ("bullet", 6, 6)]


def test_unclosed_fence_runs_to_the_last_non_blank_line():
    assert layout("```\ncode\n\nmore\n\n\n") == [("code", 1, 4)]


def test_backticks_after_a_backtick_run_make_inline_code_not_a_fence():
    assert layout("```inline``` code\nnext line\n") == [("paragraph", 1, 2)]


def test_consecutive_table_lines_form_one_span():
    text = "intro\n| a | b |\n|---|---|\n| 1 | 2 |\nafter\n"
    assert layout(text) == [("paragraph", 1, 1), ("table", 2, 4), ("paragraph", 5, 5)]


def test_consecutive_quote_lines_form_one_span():
    text = "intro\n> a\n>\n> b\n\n> c\n"
    assert layout(text) == [("paragraph", 1, 1), ("quote", 2, 4), ("quote", 6, 6)]


@pytest.mark.parametrize("marker", ["-", "*", "+", "1.", "12)"])
def test_each_top_level_list_item_is_its_own_bullet_span(marker):
    assert layout(f"{marker} one\n{marker} two\n") == [("bullet", 1, 1), ("bullet", 2, 2)]


def test_nested_items_and_continuation_lines_stay_with_their_item():
    text = "- parent\n  - child\n    grandchild text\n  indented\nlazy line\n- sibling\n"
    assert layout(text) == [("bullet", 1, 5), ("bullet", 6, 6)]


def test_items_at_equal_indentation_are_siblings():
    assert layout("  - a\n  - b\n") == [("bullet", 1, 1), ("bullet", 2, 2)]


def test_a_marker_indented_four_spaces_does_not_start_an_item():
    assert layout("    - deep\n") == [("paragraph", 1, 1)]


def test_lines_that_reach_the_content_column_continue_the_item_across_blank_lines():
    text = "- a\n\n  more a\n\n\n  - nested\n- b\n"
    assert layout(text) == [("bullet", 1, 6), ("bullet", 7, 7)]
    # The content column counts every space after the marker: 3 + 2 = 5 here.
    assert layout("10.  a\n\n     in\n") == [("bullet", 1, 3)]
    assert layout("10.  a\n\n    out\n") == [("bullet", 1, 1), ("paragraph", 3, 3)]


def test_a_line_short_of_the_content_column_ends_the_item_unless_it_is_lazy():
    assert layout("1. a\n  - b\n") == [("bullet", 1, 1), ("bullet", 2, 2)]
    assert layout("- a\n # H\n") == [("bullet", 1, 1), ("heading", 2, 2)]
    assert layout("- a\n lazy\n") == [("bullet", 1, 2)]


def test_a_shallow_line_after_a_blank_or_another_block_ends_an_item():
    text = "- a\n\n after blank\n- b\n# H\n- c\n> q\n- d\n| t |\n- e\n```\ncode\n```\n"
    assert layout(text) == [
        ("bullet", 1, 1),
        ("paragraph", 3, 3),
        ("bullet", 4, 4),
        ("heading", 5, 5),
        ("bullet", 6, 6),
        ("quote", 7, 7),
        ("bullet", 8, 8),
        ("table", 9, 9),
        ("bullet", 10, 10),
        ("code", 11, 13),
    ]


def test_a_fence_inside_an_item_is_never_split_by_its_blank_lines():
    text = "1. Run:\n   ```sh\n   a\n\n   # b\n   ```\n2. Next\n"
    assert layout(text) == [("bullet", 1, 6), ("bullet", 7, 7)]


def test_a_fence_that_opens_on_a_marker_line_closes_in_its_item():
    # Its closer must not be read as a new opener that runs on through later blocks.
    assert layout("- ```\n  # x\n  ```\n# H\n") == [("bullet", 1, 3), ("heading", 4, 4)]
    nested = "- a\n  - ```\n    x\n    ```\n- b\n"
    assert layout(nested) == [("bullet", 1, 4), ("bullet", 5, 5)]
    # The closer limit counts from the fence's own column, after the marker.
    assert layout("10. ```\n    x\n    ```\n# H\n") == [("bullet", 1, 3), ("heading", 4, 4)]


def test_identical_text_gets_distinct_ids_per_section_and_occurrence():
    same = [s for s in chunk("doc", "# A\nsame\n\nsame\n# B\nsame\n") if s.text == "same"]
    assert [s.heading_path for s in same] == [("A",), ("A",), ("B",)]
    assert len({s.id for s in same}) == 3
