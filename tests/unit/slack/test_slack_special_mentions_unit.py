"""Slack's special forms in a filed message (the tester's second attack, finding 1).

Each is written as what Slack displays, from Slack's page on message text alone
(docs.slack.dev/messaging/formatting-message-text): `<!here>`, `<!channel>` and
`<!everyone>` as "@here", "@channel" and "@everyone"; a user group's mention and a date as
the fallback text the message gives after "|", and without one as "[user group]" or
"[date]". None is a link. Every name and message is made up."""

import pytest

from cairn.slack.text import note_from_message


def filed(text: str):
    message = {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text}
    return note_from_message(
        message, team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    )  # fmt: skip


def body_of(text: str) -> str:
    """The note's lines between its heading and its source link."""
    return filed(text).text.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]


@pytest.mark.parametrize(
    ("text", "body", "shown"),
    [
        ("Standup <!here> at ten", "Standup @here at ten", "Standup @here at ten"),
        ("<!channel> doors close", "@channel doors close", "@channel doors close"),
        ("<!everyone> lunch is in", "@everyone lunch is in", "@everyone lunch is in"),
        ("<!here|here> soon", "@here soon", "@here soon"),  # the form older messages send
        ("Ask <!subteam^S0DESIGN1|@designers> today", "Ask @designers today", None),
        ("Ask <!subteam^S0DESIGN1> today", "Ask \\[user group\\] today", "Ask [user group] today"),
        ("Due <!date^1900000000^{date_short}|Mar 17, 2030> sharp", "Due Mar 17, 2030 sharp", None),
        (
            "Due <!date^1900000000^{date}^https://fable.test/cal|Mar 17> sharp",
            "Due Mar 17 sharp",
            None,
        ),
        ("Due <!date^1900000000^{date_short}> sharp", "Due \\[date\\] sharp", "Due [date] sharp"),
    ],
)
def test_a_special_form_is_written_as_slack_displays_it(text, body, shown):
    draft = filed(text)
    assert body_of(text) == body
    assert draft.title == body
    assert draft.shown == (shown or body)


@pytest.mark.parametrize(
    "text",
    [
        "<!here>",
        "<!channel|channel>",
        "<!subteam^S0DESIGN1|@designers>",
        "<!date^1900000000^{date}^https://fable.test/cal|Mar 17>",
    ],
)
def test_no_special_form_is_a_link_or_left_out(text):
    body = body_of(f"See {text} now")
    assert "](" not in body and "link omitted" not in body, body


def test_a_special_form_in_a_line_read_as_code_is_what_slack_displayed():
    """Where both readers read a line as indented code, it holds what Slack displayed."""
    lines = body_of("Notes:\n\n    <!here> run `make`\n    Due <!subteam^S0DESIGN1>").split("\n")
    assert lines[-2:] == ["    @here run `make`", "    Due [user group]"], lines
