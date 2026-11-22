"""Shared test helpers extracted from test_rt3_slack_spans_held."""

import random
from itertools import pairwise

from cairn.slack.text import note_from_message

RUNS = ["`", "``", "```", "```````"]

CODES = [*RUNS, "a", "x y", "a`b", "a``b", "a```b", "a```````b", "`a", "a`", "`a`", " ",
         "  ", "\t", " a", "a ", " a ", " `", "` ", "``a``", "[x](https://fable.test/x)",
         "*bold*", "<b>", "&amp;", "&", "|", "#", "# h", "\\`", "a\\", "\\", "_x_", "~x~",
         "~~~", "![i](https://fable.test/i.png)", "https://fable.test/x",
         "<https://fable.test/x>", "1.", "- x", "> q", "===", "---", "*", "**"]  # fmt: skip

BOUND = [code for code in CODES if "`" not in code and code.strip()]  # the text's spans

PROSE = [" ", " and ", "x", "|", " | ", "#", "=", ".", ",", "a\\", "\\", "[", "]", "(x)", "&",
         "<", "~", "!", " - ", " > "]  # fmt: skip

LEADS = ["- ", "1. ", "* ", "+ ", "> ", ">", "- > ", "> - "]

NOTHING = "<javascript:x|>"  # a reference cairn shows as nothing: a link it will not write


def enc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def sent_text(kind: str, value: str) -> str:
    """What the message's text holds for a segment."""
    if kind in ("named", "bound"):
        return "`" + enc(value) + "`"
    return value if kind == "nothing" else enc(value)


def build(lines: list[list[tuple[str, str]]], sections: bool = False, omit: bool = False) -> dict:
    """The message Slack sends: its text, and its rich text naming the "named" spans, a
    reference as a link element and a span the text bounds left out. All in one section, or
    (`sections`) each line its own, a line led by a quotation's mark a quotation of its own."""
    blocks: list[dict] = []
    for at, line in enumerate(lines):
        quoted = sections and line[:1] == [("lead", "> ")]
        if at and not sections:
            blocks[-1]["elements"].append({"type": "text", "text": "\n"})
        if at == 0 or sections:
            kind = "rich_text_quote" if quoted else "rich_text_section"
            blocks.append({"type": kind, "elements": []})
        for kind, value in line[1:] if quoted else line:
            if kind == "named":
                element = {"type": "text", "text": value, "style": {"code": True}}
            elif kind in ("prose", "lead"):
                element = {"type": "text", "text": value}
            elif kind == "nothing":
                element = {"type": "link", "url": "javascript:x", "text": ""}
            elif not omit:  # a span of a shape cairn does not read: code-styled, not text
                element = {"type": "emoji", "name": "x", "style": {"code": True}}
            else:
                continue
            blocks[-1]["elements"].append(element)
    text = "\n".join("".join(sent_text(k, v) for k, v in line) for line in lines)
    return {
        "type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text,
        "blocks": [{"type": "rich_text", "elements": blocks}],
    }  # fmt: skip


def filed(sent: dict):
    return note_from_message(
        sent, team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    )  # fmt: skip


def a_line(rng: random.Random) -> list[tuple[str, str]]:
    line = [("lead", rng.choice(LEADS))] if rng.random() < 0.3 else []
    for _ in range(rng.randint(1, 6)):
        pick = rng.random()
        if pick < 0.45:
            if rng.random() < 0.2:  # backticks Slack displayed as characters, right before it
                line.append(("prose", rng.choice(["`", "x`", "``", "` "])))
            line.append(("named", rng.choice(CODES)))
        elif pick < 0.65:
            line.append(("bound", rng.choice(BOUND)))
        elif pick < 0.9:
            line.append(("prose", rng.choice(PROSE)))
        else:
            line.append(("nothing", NOTHING))
    shown = [(k, v) for k, v in line if k not in ("lead", "nothing")]
    if shown and shown[0][0] == "prose" and shown[0][1].lstrip()[:1] in "->=|#":
        line.insert(1 if line[0][0] == "lead" else 0, ("prose", "x"))  # no mark starts it
    return line


def legal(lines) -> bool:
    """A message the oracle speaks for: every line displays something, and no two spans the
    text alone bounds touch (one span, as the guide says)."""
    for line in lines:
        if not "".join(v for k, v in line if k not in ("lead", "nothing")).strip():
            return False
        spans = [k for k, _ in line if k != "nothing"]
        if any(a == b == "bound" for a, b in pairwise(spans)):
            return False
    return True


def messages(count: int, seed: int):
    rng, made = random.Random(seed), 0
    while made < count:
        lines = [a_line(rng) for _ in range(rng.randint(1, 3))]
        if legal(lines):
            made += 1
            yield lines
