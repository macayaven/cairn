"""One long run of backticks among short Slack code spans."""

import pytest
from test_design_system_unit import tree

from cairn.slack.text import note_from_message
from cairn.web.markdown import render


@pytest.fixture(params=[16, 32], ids=["16-short-spans", "32-short-spans"])
def message_draft(request):
    count = request.param
    spans = [f"id{n:03}" for n in range(count)]
    spans.insert(count // 2, "row" + "`" * count + "id")
    lead = "Field log.\n\n"
    elements = [{"type": "text", "text": lead}]
    for n, value in enumerate(spans):
        if n:
            elements.append({"type": "text", "text": " "})
        elements.append({"type": "text", "text": value, "style": {"code": True}})
    elements.append({"type": "text", "text": "."})
    section = {"type": "rich_text_section", "elements": elements}
    message = {
        "type": "message",
        "ts": "1900000000.000100",
        "text": lead + " ".join(f"`{value}`" for value in spans) + ".",
        "blocks": [{"type": "rich_text", "elements": [section]}],
    }
    draft = note_from_message(
        message,
        team_id="T0FABLE01",
        team_domain="fable",
        conversation_id="CLIB001",
        author="Cedar",
        names={},
    )
    return message["text"], spans, draft.text


def test_draft_size_is_linear_in_the_slack_text(message_draft):
    fallback, _, draft = message_draft
    # Allow room for the author heading, source link and quoting the long span itself.
    assert len(draft) <= 3 * len(fallback), (len(fallback), len(draft))


def test_every_inline_span_displays_its_own_text_in_order(message_draft):
    _, spans, draft = message_draft
    shown = tree(str(render(draft)))
    assert [node.text() for node in shown.all("code")] == spans
