"""cairn.sources.markup.html_to_text below the acceptance level: each of its nine rules on
inputs the shared example pairs do not hold (nested lists, a table inside a paragraph, links of
every kind, entities at the edges, unclosed and stray tags, text that is not HTML at all), and
its time on long and hostile input, which grows with the input and not with its square.

The example pairs themselves are checked by tests/acceptance/test_c5_google_calendar.py."""

from functools import partial

import pytest
from linear_cost import best_of_five

from cairn.sources.markup import html_to_text

# Rule 1: comments and the content of script, style, head, title and template


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("<p>Kept</p><!-- a comment -->", "Kept"),
        ("<head><title>Page</title><style>b{}</style></head><body><p>Body</p></body>", "Body"),
        ("<template><p>Not shown</p></template><p>Shown</p>", "Shown"),
        ("<script>if (a < b) { x = '</p>' }</script>After", "After"),
        ("<style><!-- p {} --></style>Left", "Left"),
        ("Before<SCRIPT>gone()</SCRIPT>after", "Beforeafter"),
        ("<title>Only a title</title>", ""),
        ("<!-- one --><!-- two -->", ""),
        ("<!DOCTYPE html><html><body>Text</body></html>", "Text"),
        ("<?xml version='1.0'?><p>Text</p>", "Text"),
        ("<![CDATA[ hidden ]]>Shown", "Shown"),
    ],
)
def test_comments_and_the_content_of_hidden_elements_are_dropped(html, text):
    assert html_to_text(html) == text


def test_a_head_left_open_ends_where_the_body_begins():
    assert html_to_text("<head><title>T</title><body><p>Body</p>") == "Body"


# Rule 2: br, blocks, li and tr


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("One<br>Two", "One\nTwo"),
        ("One<br><br>Two", "One\n\nTwo"),
        ("One<br><br><br><br>Two", "One\n\nTwo"),
        ("<br>Starts<br>", "Starts"),
        ("A<hr>B", "A\n\nB"),
        ("<section>A</section><article>B</article>", "A\n\nB"),
        ("<header>A</header><footer>B</footer>", "A\n\nB"),
        ("<blockquote>Quoted</blockquote>Said", "Quoted\n\nSaid"),
        ("Text <p>inside</p> more", "Text\n\ninside\n\nmore"),
        ("<div><div><p>Deep</p></div></div>", "Deep"),
        ("<p></p><p>  </p><p>Only</p>", "Only"),
        ("<span>a</span><span>b</span>", "ab"),
        ("<b>bold</b> <i>italic</i>", "bold italic"),
    ],
)
def test_breaks_blocks_and_lines(html, text):
    assert html_to_text(html) == text


# Rule 3: lists


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("<ol><li>One</li><li>Two</li></ol>", "- One\n- Two"),
        ("<ul><li>a<ul><li>b</li><li>c</li></ul></li><li>d</li></ul>", "- a\n- b\n- c\n- d"),
        ("<ul><li>a<ol><li>b<ul><li>c</li></ul></li></ol></li></ul>", "- a\n- b\n- c"),
        ("<ul><li></li><li>  </li><li>Kept</li></ul>", "- Kept"),
        ("<ul><li>No closing<li>tags here</ul>", "- No closing\n- tags here"),
        ("<ul><li><p>In a paragraph</p></li></ul>", "- In a paragraph"),
        ("Before<ul><li>x</li></ul>After", "Before\n\n- x\n\nAfter"),
        ("<li>A stray item</li>", "- A stray item"),
    ],
)
def test_list_items_begin_with_a_dash_and_nesting_is_not_shown(html, text):
    assert html_to_text(html) == text


# Rule 4: tables


@pytest.mark.parametrize(
    ("html", "text"),
    [
        (
            "<table><thead><tr><th>A</th><th>B</th></tr></thead>"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody></table>",
            "A | B\n1 | 2",
        ),
        (
            "<p>Before <table><tr><td>x</td><td>y</td></tr></table> after</p>",
            "Before\n\nx | y\n\nafter",
        ),
        ("<table><tr><td>Only</td></tr></table>", "Only"),
        ("<table><tr><td>a<td>b<tr><td>c<td>d</table>", "a | b\nc | d"),
        ("<td>loose</td><td>cells</td>", "loose | cells"),
    ],
)
def test_the_cells_of_a_row_are_one_line(html, text):
    assert html_to_text(html) == text


# Rule 5: headings


def test_a_heading_is_a_paragraph_without_a_hash():
    assert html_to_text("<h1>Title</h1>Text<h6>Small</h6>") == "Title\n\nText\n\nSmall"
    assert "#" not in html_to_text("<h3>Agenda</h3><p>Items</p>")


# Rule 6: links


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ('<a href="https://a.example.test/x">Words</a>', "Words (https://a.example.test/x)"),
        ('<a href="https://a.example.test/x"></a>', "https://a.example.test/x"),
        ('<a href="https://a.example.test/x">  </a>', "https://a.example.test/x"),
        ('<a href="https://a.example.test/x/">a.example.test/x</a>', "https://a.example.test/x/"),
        ('<a href="http://a.example.test/">https://a.example.test</a>', "http://a.example.test/"),
        (
            '<a href="https://a.example.test/X">a.example.test/x</a>',
            "a.example.test/x (https://a.example.test/X)",
        ),
        ('<a href="mailto:ada@example.test">ada@example.test</a>', "ada@example.test"),
        ('<a href="MAILTO:ada@example.test">Ada</a>', "Ada"),
        ('<a href="mailto:ada@example.test"></a>', ""),
        ('<a href="tel:+15550100">Call</a>', "Call"),
        ('<a href="">Empty</a>', "Empty"),
        ('<a href="#top">Top</a>', "Top"),
        ('<a href="javascript:void(0)">Run</a>', "Run"),
        ('<a name="anchor">Named</a>', "Named"),
        ('<a href=" https://a.example.test/y ">Spaced</a>', "Spaced (https://a.example.test/y)"),
        (
            '<a href="https://a.example.test/?a=1&amp;b=2">Q</a>',
            "Q (https://a.example.test/?a=1&b=2)",
        ),
        (
            '<a href="https://a.example.test/z"><b>Bold</b> words</a>',
            "Bold words (https://a.example.test/z)",
        ),
        (
            '<a href="https://a.example.test/i"><img src="i.png" alt="Logo"></a>',
            "Logo (https://a.example.test/i)",
        ),
        (
            '<a href="https://a.example.test/n">Never closed',
            "Never closed (https://a.example.test/n)",
        ),
        ("Stray</a> end", "Stray end"),
        (
            '<a href="https://a.example.test/1">One <a href="https://a.example.test/2">Two</a>',
            "One (https://a.example.test/1)Two (https://a.example.test/2)",
        ),
    ],
)
def test_links_are_written_by_their_kind(html, text):
    assert html_to_text(html) == text


# Rule 7: images


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ('<img src="https://img.example.test/a.png" alt="A chart">', "A chart"),
        ('<img src="https://img.example.test/a.png">', ""),
        ('<img src="https://img.example.test/a.png" alt="">', ""),
        ('Before <img alt="  spaced   out "/> after', "Before spaced out after"),
    ],
)
def test_an_image_is_its_alt_text_and_never_its_address(html, text):
    said = html_to_text(html)
    assert said == text
    assert "img.example.test" not in said


# Rule 8: other tags and character references


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("&amp;", "&"),
        ("&lt;b&gt; is not a tag", "<b> is not a tag"),
        ("&nbsp;&nbsp;edges&nbsp;", "edges"),
        ("<p>&#39;quoted&#39; &quot;twice&quot;</p>", "'quoted' \"twice\""),
        ("<p>&#x263A; &copy;</p>", "☺ ©"),
        ("<p>&amp</p>", "&"),
        ("<p>&unknown; entity</p>", "&unknown; entity"),
        ("<p>tail &amp;</p>", "tail &"),
        ("<custom-tag>Kept</custom-tag> <font color=red>too</font>", "Kept too"),
    ],
)
def test_other_tags_are_removed_and_character_references_decoded(html, text):
    assert html_to_text(html) == text


# Rule 9: white space


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("<p>  many   spaces\tand\ttabs  </p>", "many spaces and tabs"),
        ("<p>A line\nbroken in the source</p>", "A line broken in the source"),
        ("<pre>keep\n  these\n\nlines</pre>", "keep\nthese\n\nlines"),
        ("<pre>\nfirst</pre>", "first"),
        ("<pre>a\n\n\n\nb</pre>", "a\n\nb"),
        ("<p>a</p>\n\n\n<p>b</p>", "a\n\nb"),
        ("\n\n<p>edges</p>\n\n", "edges"),
    ],
)
def test_white_space_is_collapsed_except_line_breaks_in_pre(html, text):
    assert html_to_text(html) == text


@pytest.mark.parametrize(
    ("plain", "text"),
    [
        ("", ""),
        ("   ", ""),
        ("One line", "One line"),
        ("  Two\tlines  \n  here  ", "Two lines\nhere"),
        ("Para one\n\n\n\nPara two", "Para one\n\nPara two"),
        ("\n\nEdges\n\n", "Edges"),
        ("Windows\r\nline\r\nends", "Windows\nline\nends"),
        ("a < b and c > d", "a < b and c > d"),
        ("Fish &amp; chips\nno tags", "Fish & chips\nno tags"),
        ("3<4 and 5>2", "3<4 and 5>2"),
        ("Mail ada@example.test & co", "Mail ada@example.test & co"),
    ],
)
def test_text_with_no_tag_keeps_its_own_line_breaks(plain, text):
    assert html_to_text(plain) == text


# Malformed input: it does what it can and never raises


@pytest.mark.parametrize(
    "html",
    [
        "<",
        "<<<<",
        "<p",
        "<p class=",
        '<a href="https://a.example.test/',
        "<!--",
        "<!-- never closed <p>text",
        "</>",
        "</p></div></ul></li></a></table>",
        "<p>\x00\x01control</p>",
        "<p>\udcff lone surrogate</p>",
        "<" + "div" * 100 + ">",
        "<img alt>",
        "&#xFFFFFFFF; &#0; &#;",
    ],
)
def test_malformed_html_never_raises(html):
    assert isinstance(html_to_text(html), str)


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("<p>Unclosed <b>bold<p>Next", "Unclosed bold\n\nNext"),
        ("</p>Stray<div>", "Stray"),
        ("<p>Open at the end", "Open at the end"),
        ("<b><i>misnested</b></i> fine", "misnested fine"),
    ],
)
def test_unclosed_and_stray_tags_keep_the_text(html, text):
    assert html_to_text(html) == text


def test_a_tag_cut_off_at_the_end_does_not_cost_the_text_before_it():
    # Python's parser drops "<p" at the end in some releases and keeps it in others.
    assert html_to_text("Text then <p").startswith("Text then")


def test_it_knows_nothing_of_calendars():
    from pathlib import Path

    import cairn.sources.markup as markup

    text = Path(markup.__file__).read_text().lower()
    for word in ("calendar", "event", "gmail", "google"):
        assert word not in text, word


# Time: it grows with the input


def seconds(html: str) -> float:
    return best_of_five(partial(html_to_text, html))


SHAPES = {
    "paragraphs": lambda n: "<p>Some words &amp; more</p>" * n,
    "a long list": lambda n: "<ul>" + "<li>An item <b>bold</b></li>" * n + "</ul>",
    "deep nesting": lambda n: "<div>" * n + "x" + "</div>" * n,
    "unclosed tags": lambda n: "<b>word " * n,
    "runs of <": lambda n: "<" * n + "p>x",
    "lone < among text": lambda n: "<p>" + "a < b " * n + "</p>",
    "links": lambda n: '<a href="https://a.example.test/p">p</a> ' * n,
    "an unclosed comment": lambda n: "<!--" + "x " * n,
    "white space": lambda n: "<p>" + " \t\n" * n + "x</p>",
    "a table": lambda n: "<table>" + "<tr><td>a</td><td>b</td></tr>" * n + "</table>",
    "plain text": lambda n: "line of text\n" * n,
}


@pytest.mark.perf
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_four_times_the_input_takes_about_four_times_as_long(shape):
    make = SHAPES[shape]
    small, large = seconds(make(5_000)), seconds(make(20_000))
    assert large < 10 * small, f"{shape}: {small:.4f} s, then {large:.4f} s"  # no seconds
