"""Unit tests for the span chunker: the inner TDD loop under tests/acceptance/test_t1_spans.py."""

import re
from html import unescape

import pytest

from cairn import spans
from cairn.spans import chunk, code_read
from cairn.web import markdown


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


# Where the Markdown parser reads code in a text read alone (code_read): each stretch as the
# characters of the text it covers, written out here.


def code_in(text: str) -> list[str]:
    return [text[start:end] for start, end in code_read(text)]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Read `Step` by Step.", ["Step"]),  # a code span: its code, between its backticks
        ("a ``x ` y`` b `z`", ["x ` y", "z"]),
        ("Read `Step\nby Step` today.", ["Step\nby Step"]),  # across a line's end
        ("> Read `Step\n> by Step` today.", ["Step\n> by Step"]),  # in a quote: its marks too
        ("- Read `x`,\n      then `y`.", ["x", "y"]),  # a list item's lines
        ("- Read the guide.\n\tStep `by` x.", ["by"]),  # a tab the parser reads as spaces
        ("Read `x`  \n   then `y`  ", ["x", "y"]),  # blanks that end the lines
        ("# Read `Step` by # `Step` #", ["Step", "Step"]),  # a heading
        ("Read `x` by\n`Step` y\n===", ["x", "Step"]),  # and one underlined
        ("| a | `b \\| c` d | `e` |\n| --- | --- | --- |", ["b \\| c", "e"]),  # table cells
        ("| `a` | `a` |\n| --- | --- |\n| `a` | b |", ["a", "a", "a"]),
        ("See ![`Step\nby` x](p.png) and `y`.", ["Step\nby", "y"]),  # a picture's: a link's
        ("See [`a` b](https://x.dev) `c`.", ["a", "c"]),  # a link's words
        ("Escaped \\`Step\\` is not code.", []),
        ("Text <https://x.dev/`a`> b.", []),  # an address in angle brackets
        ("Run:\n\n```sh\nquill run\n```\nThen.", ["```sh\nquill run\n```"]),  # a fenced block
        ("> ~~~\n> quill run\n> ~~~", ["> ~~~\n> quill run\n> ~~~"]),
        ("Run:\n\n    quill run\n    --now\n\nThen.", ["    quill run\n    --now"]),  # indented
        ("- Read the guide.\n\n    Step by Step", []),  # indented under an item: its prose
        ("- Read the guide.\n\n        Step by Step", ["        Step by Step"]),  # its code
        ("Read\n    Step by Step", []),  # a paragraph's lines go on, however indented
        ("Plain prose with no mark of code at all.", []),
    ],
)
def test_the_parser_says_what_is_code_in_a_text_read_alone(text, code):
    assert code_in(text) == code


def test_a_code_span_the_parser_cannot_place_in_the_text_covers_its_lines(monkeypatch):
    """Where the parser's text of a block cannot be found in the text (it never happens with
    the blocks above), its code spans cover the lines they stand on: never less than the code."""
    placed = spans._placed
    monkeypatch.setattr(spans, "_placed", lambda *args: placed(*args)._replace(starts=None))
    assert code_in("First line.\nRead `x` here.\nLast line.") == ["Read `x` here."]


@pytest.mark.parametrize(
    "text",
    [
        "*Read `Step*` by* Step `x*` **y**",
        "_a `b_` c_ and *`d`* and ***`e`***",
        "*[`a*`](u)* ![*`b`*](p.png) <https://x.dev/*`c`*>",
        "**a `b\nc**` d** e `f`",
        "- *a* `b*`\n\n  > *`c`*\n\n      *d*",
        "| *a* | `b *\\| c*` |\n| --- | --- |",
    ],
)
def test_the_code_read_without_emphasis_is_the_web_views_code(text):
    """The code reader leaves emphasis unread (spans._CODE_READER): the code a claim's parser
    (spans.claim_markdown) reads in the same text, block by block and span by span, is the
    same."""

    def code(parser) -> list:
        found = []
        for token in parser.parse(text):
            if token.type in ("fence", "code_block"):
                found.append((token.type, token.map, token.content))
            for child in token.children or ():
                for inner in [child, *(child.children or ())]:
                    if inner.type == "code_inline":
                        found.append((inner.type, inner.markup, inner.content))
        return found

    assert code(spans._CODE_READER) == code(spans.claim_markdown())
    assert code(spans.claim_markdown())


# Whatever reads a claim reads it as the page that shows it does (spans.claim_markdown): the
# code the code reader finds is the code the page's renderer shows (cairn.web.markdown.render),
# in the shapes where a heading, an underline or a picture would change the reading.
AGREED = [
    "`Step by\n===\nStep` today.",  # a setext underline inside a code span
    "> # `Step\n> by Step` today.",  # a heading line in a quote
    "- # `Step\n  by Step` today.",  # and in a list item
    '![a [b](u)](v "`") Step\nby Step ` x',  # a picture whose title opens a code span
    "Notes\n===\n    Step by Step today.",  # indented after an underline
    "> # Notes\n>     Step by Step today.",  # indented after a heading line
    "# Read `Step` by Step",  # a heading line
    "Read `x` by\nStep y\n===\n`z`",  # a setext underline under a paragraph
    'See ![a `b` c](p.png "t `x` y") and `z`.',  # a picture: backticks in its words and title
    "| a | `b \\| c` d |\n| --- | --- |\n| `e` | f |",  # a table
    "<span>`x`</span> and <div>\n`y`\n</div>",  # raw HTML
    "<div>\n\n    z\n\n</div>",
    "See [a](<b `c` d>) and <https://x.dev/`y`> `z`.",  # addresses in angle brackets
    "Run:\n\n```sh\nquill `run`\n```\n\n    indented `x`",  # fenced and indented blocks
    "- Read the guide.\n\n    Step `by` Step",  # indented under a list item
    "*a `b*` c* _d `e_`",  # emphasis around code
]


def page_code(text: str) -> list[str]:
    """What the page shows as code in a claim, as written."""
    return [unescape(code) for code in re.findall(r"<code[^>]*>(.*?)</code>", render(text), re.S)]


def render(text: str) -> str:
    return str(markdown.render(text))


@pytest.mark.parametrize("text", AGREED)
def test_the_code_read_is_the_code_the_page_shows(text):
    tokens = []
    for token in spans._CODE_READER.parse(text):
        if token.type in ("fence", "code_block"):
            tokens.append(token.content)
        for child in token.children or ():
            for inner in [child, *(child.children or ())]:
                if inner.type == "code_inline":
                    tokens.append(inner.content)
    assert tokens == page_code(text)
    assert len(code_read(text)) == len(tokens)  # each where it stands


def test_the_page_and_the_code_reader_read_a_claim_with_the_same_rules():
    """One definition of how a claim is read (spans.claim_markdown, CLAIM_RULES_OFF), which
    both build on: the code reader leaves emphasis unread besides, and nothing else."""
    page, reader = markdown._PARSER.get_active_rules(), spans._CODE_READER.get_active_rules()
    assert page["block"] == reader["block"]
    assert [rule for rule in page["inline"] if rule != "emphasis"] == reader["inline"]
    assert set(spans.CLAIM_RULES_OFF) == {"image", "heading", "lheading"}
    assert not set(spans.CLAIM_RULES_OFF) & {*page["block"], *page["inline"]}


def test_a_text_longer_than_the_page_parses_is_code_whole():
    """The page shows a claim longer than MAX_RENDERED characters whole, as preformatted
    text, and parses none of it: none of it is prose."""
    long = "Step by Step. " * (spans.MAX_RENDERED // 14 + 1)
    assert len(long) > spans.MAX_RENDERED and render(long).startswith("<pre>")
    assert code_read(long) == [(0, len(long))]
    within = long[: spans.MAX_RENDERED]
    assert code_read(within) == [] and not render(within).startswith("<pre>")
    assert markdown.MAX_RENDERED == spans.MAX_RENDERED
