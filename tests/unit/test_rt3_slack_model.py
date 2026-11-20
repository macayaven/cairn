"""An independent model of what Slack displays for a message's `text`, and a seeded grammar
of Slack text to generate messages from. Written from Slack's own page on message text
(docs.slack.dev/messaging/formatting-message-text, read 2026-11-20), not from cairn's
converter; the tests at the bottom hold the model to that page's own examples.

What the page says, and so what the model holds as certain:
- `&`, `<` and `>` are control characters; only `&amp;`, `&lt;` and `&gt;` are decoded for
  display, everywhere.
- Inline code is text between backticks; "Text within inline code blocks will not use any
  other formatting", so a `*`, a `<url|label>` or a `[x](y)` inside it is shown as written.
- A code block is text between three backticks before and after it; its own example puts
  text on the same line as the backticks: "```This is a code block\\nAnd it's multi-line```".
- `<url>` and `<url|label>` are links to `url`, showing `label` (else the url); `<@U..>` is
  a user shown by their name, `<#C..|name>` a conversation by its name, `<!here>` and the
  other special mentions are shown as their words, `<!date^..|fallback>` as a date.
- `*bold*`, `_italic_`, `~strike~`; `>` at the start of a line quotes it; there is no list
  syntax, so `- item` and `1. item` are text.

What the page does not say is marked unsure (`Piece.sure` False) and a check may accept
either reading of it: a run of backticks with no closer, an inline code span across a line
break, a code block's empty first or last line, whether emphasis needs word boundaries, the
date a `<!date>` shows, a raw `<` without its `>`, and line breaks other than LF and CR LF.
"""

import random
import re
from dataclasses import dataclass, field

# Line boundaries Python's str.splitlines knows besides LF; Slack's page names only "\n".
OTHER_BREAKS = "\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"


def decode(text: str) -> str:
    """The three entities Slack decodes for display, read left to right (one pass)."""
    return re.sub(r"&(lt|gt|amp);", lambda m: {"lt": "<", "gt": ">", "amp": "&"}[m[1]], text)


@dataclass(frozen=True)
class Piece:
    """A stretch of a message as Slack displays it."""

    kind: str  # "text", "code", "block", "link", "mention", "special"
    shown: str  # the characters Slack displays
    target: str | None = None  # a link's address as the message writes it, decoded
    sure: bool = True  # False where Slack's page leaves the reading open


@dataclass
class Display:
    raw: str
    pieces: list[Piece] = field(default_factory=list)
    breaks_unsure: bool = False  # the message holds a line break Slack's page does not name

    @property
    def code_blocks(self) -> list[list[str]]:
        """Each code block's displayed lines; an empty first or last line is left out (the
        page does not say whether Slack shows it)."""
        blocks = []
        for piece in self.pieces:
            if piece.kind == "block" and piece.sure:
                lines = piece.shown.split("\n")
                if lines and lines[0] == "":
                    lines = lines[1:]
                if lines and lines[-1] == "":
                    lines = lines[:-1]
                blocks.append(lines)
        return blocks

    @property
    def inline_code(self) -> list[str]:
        return [p.shown for p in self.pieces if p.kind == "code" and p.sure]

    @property
    def links(self) -> list[tuple[str, str]]:
        """(address, label) of each link Slack itself made, outside code."""
        return [(p.target, p.shown) for p in self.pieces if p.kind == "link"]

    @property
    def any_links(self) -> list[tuple[str, str]]:
        """(address, label) of each `<url>` and `<url|label>` the message writes, code
        included: the page does not say whether Slack links one inside a code block."""
        found = []
        for inner in re.findall(r"<([^>\n]+)>", self.raw):
            if inner[:1] not in "@#!":
                target, bar, label = inner.partition("|")
                found.append((decode(target), decode(label if bar else target)))
        return found

    @property
    def unsure(self) -> bool:
        return self.breaks_unsure or any(not p.sure for p in self.pieces)

    def guide_lines(self, names: dict[str, str]) -> list[str]:
        """Each line as cairn's guide says a line read as code holds it: what Slack
        displayed, a link as `[its words](its address)`, a mention as `@` and the name,
        emphasis marks as written (Slack shows them as style, not characters)."""
        out = []
        for piece in self.pieces:
            if piece.kind == "link":
                out.append(f"[{piece.shown}]({piece.target})")
            else:
                out.append(piece.shown)
        return "".join(out).split("\n")

    def text(self) -> str:
        """Every character Slack displays, in order, emphasis marks included (a reader of
        the page may see them removed as style; see the pages harness)."""
        return "".join(p.shown for p in self.pieces)


_SPECIALS = {"here": "@here", "everyone": "@everyone"}


def _angle(inner: str, names: dict[str, str]) -> Piece:
    """What Slack displays for `<inner>`."""
    if inner.startswith("@"):
        user, _, label = inner[1:].partition("|")
        if user in names:
            return Piece("mention", "@" + names[user])
        return Piece("mention", "@" + (label or user), sure=False)  # Slack shows their name
    if inner.startswith("#"):
        conversation, _, label = inner[1:].partition("|")
        return Piece("mention", "#" + decode(label or conversation), sure=label != "")
    if inner.startswith("!"):
        word, _, label = inner[1:].partition("|")
        if word in _SPECIALS:
            return Piece("special", _SPECIALS[word], sure=False)
        return Piece("special", decode(label) or word, sure=False)  # a group, a date
    target, bar, label = inner.partition("|")
    return Piece(
        "link",
        decode(label if bar else target),
        target=decode(target),
        sure=bool(label if bar else target),
    )


def display(raw: str, names: dict[str, str] | None = None) -> Display:
    """What Slack displays for `raw`, the `text` of a message, as pieces in order."""
    names = names or {}
    shown = Display(raw)
    text = raw.replace("\r\n", "\n")
    if any(mark in text for mark in OTHER_BREAKS):
        shown.breaks_unsure = True
        text = re.sub(f"[{OTHER_BREAKS}]", "\n", text)
    pieces = shown.pieces
    i, run = 0, []

    def flush() -> None:
        if run:
            pieces.append(Piece("text", decode("".join(run))))
            run.clear()

    def lone(at: int, end: int) -> bool:
        """Whether code from `at` to `end` (its delimiters included) stands clear of other
        backticks: next to one, the page does not say how Slack pairs them."""
        return (at == 0 or text[at - 1] != "`") and (end >= len(text) or text[end] != "`")

    while i < len(text):
        char = text[i]
        if text.startswith("```", i):
            end = text.find("```", i + 3)
            flush()
            if end >= 0:
                pieces.append(Piece("block", decode(text[i + 3 : end]), sure=lone(i, end + 3)))
                i = end + 3
            else:  # no closer: the page does not say; read as literal backticks
                pieces.append(Piece("text", "```", sure=False))
                i += 3
            continue
        if char == "`":
            end, line_end = text.find("`", i + 1), text.find("\n", i + 1)
            flush()
            if end > i + 1 and (line_end < 0 or end < line_end):
                pieces.append(Piece("code", decode(text[i + 1 : end]), sure=lone(i, end + 1)))
                i = end + 1
            else:  # a lone backtick, or "``": the page does not say
                pieces.append(Piece("text", "`", sure=False))
                i += 1
            continue
        if char == "<":
            end, line_end = text.find(">", i + 1), text.find("\n", i + 1)
            flush()
            if end > i + 1 and (line_end < 0 or end < line_end):
                pieces.append(_angle(text[i + 1 : end], names))
                i = end + 1
            else:  # a control character without its pair
                pieces.append(Piece("text", "<", sure=False))
                i += 1
            continue
        run.append(char)
        i += 1
    flush()
    return shown


# The grammar. Each message is lines; a line is a lead (indentation, a quote, a list marker
# that Slack shows as text) and pieces. Code blocks open and close anywhere.

WORDS = ["Row 4.", "port 8811", "the ledger", "blue", "north", "approve", "x", "Ada"]
HOSTS = [
    "https://docs.fable.test/manual",
    "https://fable.test/a(b)",
    "http://ledger.test/x?y=1&z=2",
]
ACTIVE = [  # Markdown that Slack shows as plain characters
    "[manual](https://evil.test/m)",
    "![pic](https://evil.test/p.png)",
    "[ref]: https://evil.test/r",
    "[ref]",
    "<https://evil.test/auto>",
    "*not bold*",
    "**two**",
    "_under_",
    "~strike~",
    "# Heading",
    "Title",
    "\\",
    "\\*",
    "&#106;avascript:x",
    "| a | b |",
    "<b>raw</b>",
    "javascript:alert(1)",
]
LEADS = ["", "", "", " ", "  ", "   ", "    ", "     ", "\t", " \t", "\t\t", "&gt; ", "&gt;",
         "- ", "* ", "+ ", "1. ", "1) ", "2. ", "-", "1.", "- &gt; ", "&gt; - ", "  - ",
         "    - ", "#", "## ", "&gt; ## "]  # fmt: skip
UNDERS = ["===", "---", "-", "=", "- - -", "***", "___", "  ---", "    ---", "&gt; ---",
          "|---|---|", "|-|", "~~~", "```", "````", "~~~~", "\t```", "   ~~~", "&lt;div&gt;",
          "&lt;!-- x --&gt;", "[ref]: https://evil.test/r", "1986. A year", ""]  # fmt: skip
INVISIBLE = ["\u200b", "\ufeff", "\u202e", "\u2066", "\u200d", "\xa0", "\u2028", "\u2029",
             "\x85", "\r", "\r\n", "\v", "\f", "\x1c", "\t", " "]  # fmt: skip
NAMES = {"U0CEDAR01": "Cedar Quill"}


def _entity(rng: random.Random) -> str:
    return rng.choice(["&lt;", "&gt;", "&amp;", "&amp;lt;", "&amp;amp;", "&lt;&gt;", "&amp;gt;"])


def _label(rng: random.Random) -> str:
    return rng.choice([*WORDS, *ACTIVE, "a`b", "~~~", "```", "|", "a|b", "*x*", "&lt;y&gt;",
                       "[a](b)", "line\u2028two", "`", "x](https://evil.test/l) [y",
                       "\\"])  # fmt: skip


def _link(rng: random.Random) -> str:
    target = rng.choice([*HOSTS, "mailto:shelf", "javascript:alert(1)", "/relative",
                         "https://fable.test/`x`", "https://fable.test/a)b", "data:text/html,x",
                         "https://fable.test/&lt;x&gt;"])  # fmt: skip
    return rng.choice([f"<{target}>", f"<{target}|{_label(rng)}>", f"<{target}|>"])


def _code_text(rng: random.Random, multiline: bool) -> str:
    choices = [*WORDS, *ACTIVE, _link(rng), "&lt;x&gt;", "~~~", "````", "~~", "  lead", "trail \t",
               "\t", " ", "a\tb\t", "<@U0CEDAR01>", "*raw*", "# not heading", "---"]  # fmt: skip
    parts = [rng.choice(choices) for _ in range(rng.randint(1, 3))]
    if not multiline:
        return " ".join(parts).replace("`", "'") or "x"
    lines = []
    for _ in range(rng.randint(1, 4)):
        lines.append(
            rng.choice(["", "", rng.choice(LEADS[:9])])
            + " ".join(rng.choice(choices) for _ in range(rng.randint(0, 2)))
            + rng.choice(["", "", " ", "\t", "  \t"])
        )
    text = "\n".join(lines).replace("```", "~~~")
    return text


def _block(rng: random.Random) -> str:
    content = _code_text(rng, True)
    opening = rng.choice(["```", "```\n", "```\n", "```\n\n"])
    closing = rng.choice(["```", "\n```", "\n```", "\n\n```", ""])  # "" leaves it unclosed
    return opening + content + closing


def _piece(rng: random.Random) -> str:
    kind = rng.random()
    if kind < 0.25:
        return rng.choice(WORDS)
    if kind < 0.40:
        return rng.choice(ACTIVE)
    if kind < 0.52:
        return _link(rng)
    if kind < 0.60:
        return "`" + _code_text(rng, False) + "`"
    if kind < 0.66:
        return rng.choice(["`", "``", "```", "````"])
    if kind < 0.72:
        return _entity(rng)
    if kind < 0.78:
        return rng.choice(["<@U0CEDAR01>", "<@U0BIRCH01>", "<#C0SHELF1|shelf>", "<!here>",
                           "<!subteam^S0TEAM|@shelvers>",
                           "<!date^1900000000^{date}|17 Mar>"])  # fmt: skip
    if kind < 0.86:
        return rng.choice(INVISIBLE)
    if kind < 0.94:
        return _block(rng)
    return rng.choice(["*" + rng.choice(WORDS) + "*", "_" + rng.choice(WORDS) + "_",
                       "~" + rng.choice(WORDS) + "~"])  # fmt: skip


def a_line(rng: random.Random) -> str:
    if rng.random() < 0.18:
        return rng.choice(UNDERS)
    lead = rng.choice(LEADS)
    body = "".join(rng.choice(["", " ", " ", "\t"]) + _piece(rng) for _ in range(rng.randint(0, 4)))
    return lead + body + rng.choice(["", "", "", " ", "\t", "  ", " \t"])


def a_message(rng: random.Random) -> str:
    """A message of Slack text: a few lines, white space at either end at times."""
    lines = [a_line(rng) for _ in range(rng.randint(1, 7))]
    for _ in range(rng.randint(0, 2)):
        lines.insert(rng.randrange(len(lines) + 1), "")
    text = rng.choice(["\n", "\n", "\n", "\r\n", "\u2028"]).join(lines)
    text = rng.choice(["", "", "\n", " ", "\t"]) + text + rng.choice(["", "", "\n", "\t", " "])
    return text if text.strip() else "Row 4."


def an_author(rng: random.Random) -> str:
    return rng.choice(["Cedar Quill"] * 6 + [
        "Cedar [Quill](https://evil.test/n)", "Cedar\n## Mallory", "Ana & Bo", "snake_case_name",
        "Cedar · 2031-01-01 00:00:00 UTC", "Cedar\u2028## Mallory", "*Cedar*", "Cedar #",
        "Cedar \\", "&lt;Cedar&gt;", "Cedar `code`", "Cedar\u202eLLiuQ",
    ])  # fmt: skip


# The model held to Slack's page


def test_inline_code_uses_no_other_formatting():
    shown = display("This is a sentence with some `inline *code*` in it.")
    assert [p.kind for p in shown.pieces] == ["text", "code", "text"]
    assert shown.inline_code == ["inline *code*"]


def test_a_code_block_may_start_on_the_line_of_its_backticks():
    shown = display("```This is a code block\nAnd it's multi-line```")
    assert shown.code_blocks == [["This is a code block", "And it's multi-line"]]
    assert not shown.unsure


def test_the_three_entities_are_decoded_and_nothing_else():
    assert display("&lt;b&gt; &amp;amp; &#106;").text() == "<b> &amp; &#106;"


def test_links_show_their_label_or_their_address():
    shown = display("<https://fable.test/a|the ledger> and <https://fable.test/b>")
    assert shown.links == [("https://fable.test/a", "the ledger"), ("https://fable.test/b",) * 2]
    assert shown.text() == "the ledger and https://fable.test/b"


def test_mentions_show_names_and_unknown_ones_are_unsure():
    shown = display("<@U0CEDAR01> <@U0BIRCH01> <#C0SHELF1|shelf> <!here>", NAMES)
    assert shown.text() == "@Cedar Quill @U0BIRCH01 #shelf @here"
    assert [p.sure for p in shown.pieces if p.kind != "text"] == [True, False, True, False]


def test_what_the_page_leaves_open_is_unsure():
    for text in ("a ` b", "a ``` b", "a\u2028b", "a < b", "``"):
        assert display(text).unsure, text
    assert not display("a `b` c\r\nd").unsure


def test_the_guide_lines_write_links_as_markdown():
    shown = display("x <https://fable.test/a|the ledger>\n```code &lt;y&gt;```")
    assert shown.guide_lines({}) == ["x [the ledger](https://fable.test/a)", "code <y>"]


def test_the_grammar_is_seeded():
    one = [a_message(random.Random(seed)) for seed in range(50)]
    assert one == [a_message(random.Random(seed)) for seed in range(50)]
    assert len(set(one)) > 45
