"""Where a span Slack's rich text names is placed in the message's text: where the rich text
gives the element right before it, that element agrees with the place, one step past the
cases of test_rt3_slack_spans_found. Every message is made up."""

from test_rt3_slack_spans_found import code, prose, shown


def test_text_before_a_span_is_read_with_its_entities():
    """The text element right before the span is held with "&" as an entity, as the message's
    text holds it: "a &`" then the code "`", whose backticks the first place would split."""
    assert shown("a &amp;````", [prose("a &`"), code("`")]) == [("text", "a &`"), ("code", "`")]


def test_a_later_place_is_not_taken_where_the_rich_text_is_silent_between():
    """The rich text leaves the span "x" out, so the text "y" before the first "a" stands
    earlier than its place: the first place is kept, not the later one that "y" stands
    right before (which would make "x``a" one span)."""
    sent = "y`x``a`y`a`"
    page = shown(sent, [prose("y"), code("a"), prose("y"), code("a")])
    assert page == [("text", "y"), ("code", "x"), ("text", "ay"), ("code", "a")]


def test_a_backtick_that_closes_a_span_is_not_taken_where_the_text_before_is_not_there():
    """The text "x " the rich text gives before the span of one backtick does not stand right
    before any place it could take (the span "k" it leaves out is between), so the rich text
    tells nothing there: one backtick before the run on its line is unpaired, the run's first
    closes that span, and the code starts one later."""
    page = shown("x `k````", [prose("x "), code("`")])
    assert page == [("text", "x "), ("code", "k"), ("text", "`")]
