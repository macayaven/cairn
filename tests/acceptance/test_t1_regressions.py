"""T1 regression tests (frozen), from the GPT Sol 6.1 review of 2026-11-10.

Rules adopted on the design advisor's triage:
- A closing fence may be indented at most 3 spaces more than its opening fence (it may be
  indented less). An absolute limit would break fences inside list items.
- A line belongs to a list item when its indentation reaches the item's content column: the
  marker's indentation plus the marker width plus the spaces after it. A lazy continuation
  line (plain paragraph text right after item text) also belongs. After a blank line, only
  lines that reach the content column continue the item.
"""

from cairn.spans import chunk


def shape(text: str) -> list[tuple]:
    return [(s.kind, s.start_line, s.end_line, s.heading_path) for s in chunk("r", text)]


def test_overindented_closer_does_not_close_a_tilde_fence():
    assert shape("~~~\n    ~~~\n# hidden\n~~~\n") == [("code", 1, 4, ())]


def test_overindented_closer_does_not_close_a_backtick_fence():
    assert shape("```\n    ```\n# hidden\n```\n") == [("code", 1, 4, ())]


def test_blank_line_does_not_detach_a_nested_item():
    assert shape("- parent\n\n  - child\n- sibling\n") == [
        ("bullet", 1, 3, ()),
        ("bullet", 4, 4, ()),
    ]


def test_heading_short_of_the_content_column_leaves_the_item():
    assert shape("- parent\n # Outside\nbody\n") == [
        ("bullet", 1, 1, ()),
        ("heading", 2, 2, ("Outside",)),
        ("paragraph", 3, 3, ("Outside",)),
    ]


def test_fence_inside_a_list_item_stays_in_the_item():
    text = "- step:\n  ```\n  # not a heading\n  ```\n# Real\ntext\n"
    assert shape(text) == [
        ("bullet", 1, 4, ()),
        ("heading", 5, 5, ("Real",)),
        ("paragraph", 6, 6, ("Real",)),
    ]


def test_lazy_continuation_still_joins_the_item():
    assert shape("- item\ncontinued text\n- next\n") == [
        ("bullet", 1, 2, ()),
        ("bullet", 3, 3, ()),
    ]
