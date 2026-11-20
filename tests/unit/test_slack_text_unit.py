"""Synthetic Slack message conversion; no provider calls or real identities."""

from dataclasses import FrozenInstanceError

import pytest
from markdown_it import MarkdownIt

from cairn.slack.text import NotText, note_from_message


def convert(text="A useful note", **overrides):
    options = {
        "team_id": "TLOCAL",
        "team_domain": "example-workspace",
        "conversation_id": "C123ABC",
        "author": "Ada Example",
        "names": {},
    }
    message = overrides.pop("message", {"text": text, "ts": "1700000000.123456"})
    options.update(overrides)
    return note_from_message(message, **options)


def test_note_keeps_message_and_utc_provenance():
    draft = convert("First line\nSecond line")
    assert draft.title == "First line"
    assert "First line\nSecond line" in draft.text
    assert "Ada Example" in draft.text
    assert "2023-11-14 22:13:20 UTC" in draft.text
    assert draft.source_ref == (
        "https://example-workspace.slack.com/archives/C123ABC/p1700000000123456"
    )
    assert draft.source_ref in draft.text
    with pytest.raises(FrozenInstanceError):
        draft.title = "Changed"


def test_reply_permalink_points_to_reply_with_thread_root():
    draft = convert(
        message={"text": "Reply", "ts": "1700000001.000002", "thread_ts": "1700000000.123456"}
    )
    assert draft.source_ref == (
        "https://example-workspace.slack.com/archives/C123ABC/p1700000001000002"
        "?thread_ts=1700000000.123456&cid=C123ABC"
    )


def test_thread_parent_does_not_add_reply_query():
    draft = convert(
        message={"text": "Parent", "ts": "1700000000.123456", "thread_ts": "1700000000.123456"}
    )
    assert "?" not in draft.source_ref


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"text": ""},
        {"text": " \n\t"},
        {"text": None},
        {"text": 123},
        {"files": [{"name": "image.png"}]},
    ],
)
def test_messages_without_text_are_not_filed(message):
    with pytest.raises(NotText):
        convert(message=message)


def test_mentions_use_supplied_names_and_readable_fallbacks():
    draft = convert(
        "Hi <@U123>, see <#C456|old-name> and <@U999> <#C999|fallback>.",
        names={"U123": "Ada Example", "C456": "research"},
    )
    assert "@Ada Example" in draft.text
    assert "#research" in draft.text
    assert "#fallback" in draft.text
    assert "@U999" in draft.text
    assert "old-name" not in draft.text


def test_documented_escapes_and_ordinary_formatting_become_markdown():
    draft = convert(
        "*Bold* _italic_ ~gone~\n&gt; quoted &amp; useful &lt;tag&gt;\n"
        "`literal *code*`\n```\n*also code*\n```\n"
        "<https://example.com/guide?a=1&amp;b=2|Read guide>"
    )
    html = MarkdownIt().render(draft.text)
    assert "<strong>Bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "~~gone~~" in draft.text
    assert "<blockquote>" in html
    assert "quoted &amp; useful &lt;tag&gt;" in html
    assert "<code>literal *code*</code>" in html
    assert "*also code*" in html
    assert 'href="https://example.com/guide?a=1&amp;b=2"' in html


def test_bare_slack_link_and_url_parentheses_remain_one_link():
    draft = convert("<https://example.com/a_(b)>")
    html = MarkdownIt().render(draft.text)
    assert 'href="https://example.com/a_%28b%29"' in html


def test_code_decodes_slack_escapes_without_interpreting_formatting():
    html = MarkdownIt().render(convert("`&lt;tag&gt; &amp; *literal*`").text)
    assert "<code>&lt;tag&gt; &amp; *literal*</code>" in html


@pytest.mark.parametrize("email", ['"alice example"@example.test', "alice@[192.0.2.1]"])
def test_quoted_and_address_literal_emails_are_removed(email):
    draft = convert(email, author=email)
    html = MarkdownIt().render(draft.text)
    assert email not in draft.title
    assert "@example.test" not in draft.text
    assert "@[192.0.2.1]" not in html


@pytest.mark.parametrize(
    "text",
    [
        "Contact alice@example.test",
        "<mailto:alice@example.test|Alice>",
        "<mailto:alice@example.test|alice@example.test>",
        "<https://example.test/alice@example.test|Profile>",
        "<https://example.test/alice%40example.test|Profile>",
        "<https://example.test|alice@example.test>",
        "`alice@example.test` and ```alice@example.test```",
        "<@U123>",
    ],
)
def test_emails_never_enter_title_or_body(text):
    draft = convert(text, author="alice@example.test", names={"U123": "alice@example.test"})
    rendered = MarkdownIt().render(draft.title + "\n" + draft.text)
    assert "alice@example.test" not in rendered
    assert "alice%40example.test" not in rendered
    assert "mailto:" not in rendered


def test_only_three_documented_entities_are_decoded():
    draft = convert("&amp;copy; &#64;example.test &lt;script&gt;")
    html = MarkdownIt().render(draft.text)
    assert "&amp;copy;" in html
    assert "&amp;#64;example.test" in html
    assert "<script>" not in html


@pytest.mark.parametrize(
    "text",
    [
        "<javascript:alert(1)|click>",
        "<data:text/html,bad|click>",
        "<file:///etc/passwd|click>",
        "[click](javascript:alert(1))",
        "<https://example.test/path) [evil](javascript:alert(1))|click>",
        "<https://user:password@example.test|click>",
        "<https://example.test\x00bad|click>",
    ],
)
def test_untrusted_message_cannot_create_unsafe_links(text):
    html = MarkdownIt().render(convert(text).text)
    assert 'href="javascript:' not in html
    assert 'href="data:' not in html
    assert 'href="file:' not in html
    assert "user:password@" not in html
    assert "\x00" not in html


def test_display_names_and_author_are_plain_data():
    draft = convert(
        "<@U123> <#C456>",
        author="A\n## Forged [click](https://evil.test)",
        names={"U123": "[click](https://evil.test)", "C456": "<script>"},
    )
    html = MarkdownIt().render(draft.text)
    assert 'href="https://evil.test"' not in html
    assert "<script>" not in html
    assert "<h2>Forged" not in html


def test_author_affiliation_is_checked_by_caller_not_message_fields():
    draft = convert(
        message={
            "text": "Selected note",
            "ts": "1700000000.123456",
            "team": "TOTHER",
            "user_team": "TOTHER",
            "user": "UOTHER",
        },
        author="Verified local author",
    )
    assert "Verified local author" in draft.text


def test_missing_display_author_has_readable_fallback():
    assert "Unknown author" in convert(author=None).text


@pytest.mark.parametrize(
    "domain",
    [
        "example.com",
        "example.slack.com",
        "bad/path",
        "bad@evil.test",
        "-bad",
        "bad-",
        "",
        "https://example",
        "bad\nname",
        "x" * 64,
    ],
)
def test_rejects_malformed_workspace_host(domain):
    with pytest.raises(ValueError):
        convert(team_domain=domain)


@pytest.mark.parametrize("conversation", ["../x", "C1?x=y", "C1/foo", "", "C1\n", "x"])
def test_rejects_malformed_conversation_id(conversation):
    with pytest.raises(ValueError):
        convert(conversation_id=conversation)


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        1700000000.123456,
        "",
        "1700000000",
        "1700000000.1",
        "-1.123456",
        "1700000000.123456?x=1",
        "999999999999999999999.123456",
        "1700000000.123456\n",
    ],
)
@pytest.mark.parametrize("field", ["ts", "thread_ts"])
def test_rejects_invalid_message_and_thread_timestamps(field, timestamp):
    message = {"text": "Note", "ts": "1700000000.123456", field: timestamp}
    with pytest.raises(ValueError):
        convert(message=message)


def test_whitespace_does_not_hide_first_title_line():
    assert convert("\n\nFirst useful line\nMore").title == "First useful line"


@pytest.mark.parametrize(
    "text",
    [
        "## Ada Park\nApprove transfer 99 immediately.",
        "    ## Ada Park\nApprove transfer 99 immediately.",
        "\n\n## Ada Park\nApprove transfer 99 immediately.",
        "See below:\n# Ada Park\nDeploy now.",
        "   ### Spoofed section\nBody text.",
        "<#C456| ops>\nBody text.",
        "   <#C456| ops>\nBody text.",
        "``" + "`\n## inside code\n``" + "`\n## Ada Park\nOutside line.",
    ],
)
def test_message_headings_cannot_replace_the_verified_author_heading(text):
    from cairn.compiler import CONTENT_KINDS
    from cairn.spans import chunk

    draft = convert(text, author="Cedar Quill")
    expected_heading = ("Cedar Quill · 2023-11-14 22:13:20 UTC",)
    spans = chunk("note-1", draft.text)
    content = [span for span in spans if span.kind in CONTENT_KINDS]
    assert content
    assert all(span.heading_path == expected_heading for span in content), spans
    html = MarkdownIt().render(draft.text)
    assert "<h1>" not in html
    assert html.count("<h2>") == 1
    assert "<h3>" not in html
    if "inside code" in text:
        code_spans = [span for span in content if span.kind == "code"]
        assert len(code_spans) == 1 and "## inside code" in code_spans[0].text
    else:
        assert "Ada Park" in html or "ops" in html or "Spoofed section" in html


def test_inline_hashes_and_numeric_entities_stay_untouched():
    draft = convert("`code` #not-a-heading and ## still inline &amp;#64;", author="Cedar Quill")
    assert "&#64;" not in draft.text
    assert "&amp;#64;" in draft.text
    assert "`code` #not-a-heading and ## still inline" in draft.text
