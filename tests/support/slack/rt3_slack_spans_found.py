"""Shared test helpers extracted from test_rt3_slack_spans_found."""

from markdown_it import MarkdownIt

from cairn.slack.text import note_from_message

READER = MarkdownIt("commonmark", {"html": False})


def code(text: str) -> dict:
    return {"type": "text", "text": text, "style": {"code": True}}


def prose(text: str) -> dict:
    return {"type": "text", "text": text}


def shown(text: str, elements: list[dict]) -> list[tuple[str, str]]:
    """What the page displays of the message's lines: ("code", ...) or ("text", ...)."""
    section = {"type": "rich_text_section", "elements": elements}
    sent = {
        "type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text,
        "blocks": [{"type": "rich_text", "elements": [section]}],
    }  # fmt: skip
    draft = note_from_message(
        sent, team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    )  # fmt: skip
    body = draft.text.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]
    out: list[tuple[str, str]] = []
    for token in READER.parse(body):
        for child in token.children or []:
            kind = "code" if child.type == "code_inline" else "text"
            if out and kind == out[-1][0] == "text":
                out[-1] = ("text", out[-1][1] + child.content)
            else:
                out.append((kind, child.content))
    return out
