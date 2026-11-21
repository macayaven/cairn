"""Code within a line, one step past the fix of the second reviewer's seventh report (must-fix
3) and past the placing of named spans after it (_before, _known_code, _ticks, a blank that
ends a span written as a character reference). What held on release/0.1.0 at 08747d92.

The oracle is not cairn's code. A message is built from segments, each with what Slack
displayed: a span the rich text names (a code-styled text element; the text holds it between
backticks, its "&", "<" and ">" as entities), a span only the text's backticks bound (the
rich text gives a code-styled element of a shape cairn does not read for it; the display
model, test_rt3_slack_model, reads it as code), prose Slack displayed as typed (backticks
too, right before a named span), a list's or a quotation's mark at the start of a line (a
page may take it as structure), and a link cairn shows as nothing. The rich text is one
section, or a section a line with a quotation's own; a line feed is its own text element.
The note is read by the Markdown parser the page uses, whole and passage by passage
(cairn.spans.chunk), and held to those segments: every character, each span its own code
span; a span right after another may be its plain characters instead (the connect guide
says so). Two spans only the text's backticks bound, side by side, are one span (the guide
says so too): not generated.

The class the first attack left out (a named span's backticks and code stand earlier in the
text than the span does) is generated: 103 of the 1,200. Those of it whose first place is a
backtick of a span the rich text does not read (on_unread, told from the segments) may still
differ, and are pinned in test_rt3_slack_spans_found2: 11 do. Not checked here: the reply as
blocks.filed sends it (its title is Draft.shown, which is checked). Every message is made
up."""

import random
import time
from itertools import pairwise

import pytest
from markdown_it import MarkdownIt
from test_rt3_slack_model import decode, display

from cairn.slack.text import note_from_message
from cairn.spans import chunk

READER = MarkdownIt("commonmark", {"html": False}).enable("table")
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


def first_places(lines) -> list[tuple[int, int, set[int]]]:
    """For each span the rich text names: its place in the text, the first place after the
    span named before where its backticks and code stand, and the places of the backticks
    of the spans the rich text does not read (told from the segments, not from cairn)."""
    text = "\n".join("".join(sent_text(k, v) for k, v in line) for line in lines)
    out, at, after, unread = [], 0, 0, set()
    for line in lines:
        for kind, value in line:
            way = sent_text(kind, value)
            if kind == "bound":
                unread |= {at, at + len(way) - 1}
            if kind == "named":
                out.append((at, text.find(way, after), unread))
                after = at + len(way)
            at += len(way)
        at += 1
    return out


def anchored(lines) -> bool:
    """Whether each span the rich text names is the first place, after the one before, that
    its backticks and code stand in the text (the class the first attack left out)."""
    return all(own == first for own, first, _ in first_places(lines))


def on_unread(lines) -> bool:
    """Whether a named span's backticks and code first stand on a backtick of a span the rich
    text does not read, its own place later: the class pinned in test_rt3_slack_spans_found2
    (where it shows a page unlike Slack's), and left out of the generated run."""
    return any(own != first and first in unread for own, first, unread in first_places(lines))


def expected(lines) -> list[list[tuple[str, str]]]:
    """Each line as Slack displayed it: ("c", code) a span, ("e", code) a span right after
    another (its code, or its plain characters), ("t", text), ("l", a mark at the start)."""
    rows = []
    for line in lines:
        row, last = [], False
        for kind, value in line:
            if kind in ("named", "bound"):
                row.append(("e" if last else "c", value))
                last = True
            elif kind != "nothing":
                row.append(("l" if kind == "lead" else "t", decode(enc(value))))
                last = False
        rows.append(row)
    return rows


def reading(markdown: str) -> tuple[list[list[tuple[str, str]]], list[str]]:
    """What the parser displays of `markdown`, line by line, and the markup it reads."""
    rows: list[list[tuple[str, str]]] = [[]]
    markup = []
    for token in READER.parse(markdown):
        if token.type in ("heading_open", "fence", "code_block", "table_open", "html_block"):
            markup.append(token.type)
        if token.type != "inline":
            continue
        rows += [[]] if rows[-1] else []
        for child in token.children or []:
            if child.type == "text":
                rows[-1].append(("t", child.content))
            elif child.type == "code_inline":
                rows[-1].append(("c", child.content))
            elif child.type in ("softbreak", "hardbreak"):
                rows.append([])
            elif child.type in ("link_open", "image", "em_open", "strong_open", "html_inline"):
                markup.append(child.type)
    return rows, markup


def differs(want: list[tuple[str, str]], got: list[tuple[str, str]]) -> str | None:
    """Why `got` does not display the line `want`, or None. A paragraph drops the blanks at
    a line's ends; a mark at the start may be read as structure and not shown."""
    shown = "".join(chars for _, chars in got)
    style, token = [], []
    for at, (kind, chars) in enumerate(got):
        style += [kind] * len(chars)
        token += [at] * len(chars)
    for keep_marks in (True, False):
        row = [(("t" if k == "l" else k), c) for k, c in want if keep_marks or k != "l"]
        whole = "".join(c for _, c in row)
        if whole.strip(" \t") != shown.strip(" \t"):
            continue
        place = len(shown) - len(shown.lstrip(" \t")) - (len(whole) - len(whole.lstrip(" \t")))
        for kind, chars in row:
            where = [i for i in range(place, place + len(chars)) if 0 <= i < len(style)]
            place += len(chars)
            if not chars:
                continue
            own = (
                len(where) == len(chars)
                and {style[i] for i in where} == {"c"}
                and len({token[i] for i in where}) == 1
                and got[token[where[0]]][1] == chars
            )
            if kind == "c" and not own:
                return f"span {chars!r} is not its own code span"
            if kind == "e" and not (own or {style[i] for i in where} <= {"t"}):
                return f"span {chars!r} is neither its own code nor its characters"
            if kind == "t" and "c" in {style[i] for i in where}:
                return f"text {chars!r} shown as code"
        return None
    return f"shows {shown!r} for {''.join(c for _, c in want)!r}"


def faults(lines, sections: bool = False, omit: bool = False) -> list[str]:
    """Every way the note and each passage of it alone differ from what Slack displayed."""
    draft = filed(build(lines, sections, omit))
    want, found = expected(lines), []
    rows, markup = reading(draft.text)
    if markup != ["heading_open", "link_open"]:  # the author's heading, the source link
        found.append(f"note reads {markup}")
    rows = rows[1:-1]  # less the author's heading and the source link
    if len(rows) != len(want):
        found.append(f"note has {len(rows)} lines for {len(want)}")
    found += [f"note: {why}" for w, g in zip(want, rows, strict=False) if (why := differs(w, g))]
    for passage in chunk("", draft.text)[1:-1]:
        if passage.kind == "code":
            found.append(f"a passage of code: {passage.text!r}")
        rows, markup = reading(passage.text)
        found += [f"passage reads {markup}"] if markup else []
        first = passage.start_line - 3  # the message starts on the note's third line
        for at, got in enumerate(rows):
            if 0 <= first + at < len(want) and (why := differs(want[first + at], got)):
                found.append(f"passage: {why}")
    return found + title_faults(lines, draft)


def title_faults(lines, draft) -> list[str]:
    """The title is the first line Slack displayed, as text, code within a line between
    single backticks (the guide), cut to 80 characters: its heading holds no code or link,
    and the reply's title is what that heading displays, a start of the line."""
    line = "".join(
        f"`{v}`" if k in ("named", "bound") else v for k, v in lines[0] if k != "nothing"
    )
    whole, shown = " ".join(line.split()), " ".join(draft.shown.split())
    found = []
    if not whole.startswith(shown) or len(shown) < min(len(whole), 16):
        found.append(f"the reply names {shown!r} for {whole!r}")
    heading = READER.parse("# " + draft.title)[1].children or []
    if {child.type for child in heading} - {"text"} or len(draft.title) > 80:
        found.append(f"the title {draft.title!r} is not text of 80 characters")
    if " ".join("".join(child.content for child in heading).split()) != shown:
        found.append("the page's heading and the reply differ")
    return found


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


def test_generated_mixed_spans_show_what_slack_displayed():
    """1,200 messages: spans the rich text names and spans only the text's backticks bound,
    in every order and in chains, runs of 1, 2, 3 and 7 backticks in different spans of one
    message, spans of backticks or blanks alone, a span at a line's start after a list's or
    a quotation's mark, a span right after another with a reference that shows nothing or
    blanks between. None differs, in the note or in a passage alone; no fence, heading,
    link, emphasis or table is read; the title holds."""
    seen, orders, runs, placed, pinned = {}, set(), set(), 0, 0
    for at, lines in enumerate(messages(1200, 20261121)):
        for line in lines:
            spans = [k for k, _ in line if k in ("named", "bound", "prose", "lead")]
            for a, b, c in zip(spans, [*spans[1:], ""], [*spans[2:], "", ""], strict=False):
                orders.add((a, b, c) if c in ("named", "bound") else (a, b))
            runs |= {v for k, v in line if k == "named" and v in RUNS}
        placed += not anchored(lines)
        if found := faults(lines, sections=at % 2 == 1):
            if on_unread(lines):
                pinned += 1  # the class test_rt3_slack_spans_found2 pins
            else:
                seen.setdefault(found[0][:40], (lines, found[0]))
    assert {("named", "bound", "named"), ("bound", "named", "bound")} <= orders
    assert {("named", "named", "named"), ("bound", "named"), ("named", "bound")} <= orders
    assert runs == set(RUNS)
    assert placed >= 50, placed  # the class the first attack left out is in the run
    assert pinned <= 11, pinned  # 11 of 1,200 on 08747d92: none may join them
    assert not seen, "\n".join(f"{lines!r}: {why}" for lines, why in seen.values())


def test_the_oracle_reads_as_the_display_model_where_the_model_is_sure():
    """Where the model of Slack's text is sure (no span touches a backtick of another) and
    the text holds no three backticks (a span "``a``" is sent "```a```", which the text alone
    reads as a block), it displays as code what the oracle says Slack did."""
    sure = 0
    for lines in messages(800, 7):
        text = build(lines)["text"]
        shown = display(text)
        literal = any(k == "prose" and "`" in v for line in lines for k, v in line)
        if shown.unsure or "```" in text or literal:  # the model reads the text alone
            continue
        sure += 1
        spans = [v for line in lines for k, v in line if k in ("named", "bound")]
        assert shown.inline_code == spans, text
    assert sure > 60


@pytest.mark.parametrize(
    "line",
    [
        [("named", "``")],  # a delimiter of three alone on its line
        [("named", "```````")],
        [("named", "```"), ("named", "``")],  # at the end of the message, chained
        [("lead", "- "), ("named", "```")],  # after a list's mark
        [("lead", "> "), ("named", "```````"), ("named", "~~~")],  # after a quotation's mark
        [("lead", "1. "), ("named", "``"), ("prose", " "), ("named", "```")],
    ],
)
def test_a_long_delimiter_at_a_lines_start_is_never_a_fence(line):
    """Its closing backticks stand on its line, so neither reader takes it for a fence (a
    fence's information holds no backtick), in the note or a passage alone."""
    assert faults([[("prose", "x")], line]) == []
    assert faults([line, [("prose", "x")]]) == []


@pytest.mark.parametrize(
    "code",
    ["[x](https://fable.test/x)", "*bold*", "<b>", "&amp;", "# h", "#", "\\`", "a\\",
     "![i](https://fable.test/i.png)", "<https://fable.test/x>", "_x_", "~~~", "`a`"],
)  # fmt: skip
@pytest.mark.parametrize("first", ["named", "bound"])
def test_a_span_written_as_its_characters_shows_them_literally(code, first):
    """The plain-characters branch: what Slack showed literally is no link, emphasis,
    markup or heading on the page, and its characters are all shown."""
    assert faults([[(first, "a"), ("named", code), ("prose", " tail")]]) == []
    assert faults([[("prose", "x"), (first, "a"), ("named", code)]]) == []


@pytest.mark.parametrize("between", [[("nothing", NOTHING)], [("prose", " ")], [("prose", "  ")]])
@pytest.mark.parametrize("pair", [("``", "`"), ("a", "```````"), ("` ", " `")])
def test_a_span_after_a_reference_that_shows_nothing_or_after_blanks(between, pair):
    """With a reference that shows nothing between them, two spans are side by side and the
    second shows its characters; with blanks, each is its own span."""
    line = [("named", pair[0]), *between, ("named", pair[1])]
    assert faults([line]) == []
    if between[0][0] == "prose":
        rows, _ = reading(filed(build([line])).text)
        assert [chars for kind, chars in rows[1] if kind == "c"] == list(pair)


@pytest.mark.parametrize("pad", range(66, 82, 3))
@pytest.mark.parametrize("pair", [("```````", "\\`"), ("&", "<b>"), ("``", "*"), ("a`b", "#")])
@pytest.mark.parametrize("touch", [True, False])
def test_the_title_cut_splits_no_delimiter_or_escape(pad, pair, touch):
    line = [("prose", "w" * pad), ("named", pair[0])]
    line += ([] if touch else [("prose", " ")]) + [("named", pair[1]), ("prose", " tail")]
    assert faults([line, [("prose", "second")]]) == []


SHAPES = {
    "a different run in each span": lambda n: [
        [x for k in range(n) for x in (("named", "a" + "`" * (k + 1) + "b"), ("prose", " "))]
    ],
    "touching named spans": lambda n: [[("named", "a" + "`" * (k % 7 + 1)) for k in range(n)]],
    "touching spans of escapes": lambda n: [[("named", "&<>*_[]()!#~\\`|")] * n],
    "touching spans of backticks": lambda n: [[("named", "`" * (k % 7 + 1)) for k in range(n)]],
    "chains of three on lines": lambda n: [
        [("named", "x"), ("named", "``"), ("named", "*")] for _ in range(n // 3)
    ],
    "a head of backticks before each span": lambda n: [
        [x for k in range(n) for x in (("prose", "x" + "`" * (k % 3 + 1)), ("named", "`"))]
    ],
    "an unread span before each, by parity": lambda n: [
        [x for k in range(n) for x in (("prose", " "), ("bound", "k"), ("named", "`"))]
    ],
}


def size_of(sent: dict) -> int:
    """A message's length: its text and the characters of its rich text together."""
    sections = sent["blocks"][0]["elements"]
    rich = sum(len(e.get("text", "")) for s in sections for e in s["elements"])
    return len(sent["text"]) + rich


@pytest.mark.parametrize("shape", SHAPES)
def test_the_note_is_at_most_a_constant_times_the_message(shape):
    ratios = []
    for spans in (16, 64, 256):
        sent = build(SHAPES[shape](spans))
        ratios.append(len(filed(sent).text) / size_of(sent))
    assert max(ratios) <= 3, ratios
    assert ratios[2] <= ratios[0] + 0.05, ratios  # it does not grow with the message


def best_of_three(sent: dict) -> float:
    times = []
    for _ in range(3):
        start = time.perf_counter()
        filed(sent)
        times.append(time.perf_counter() - start)
    return min(times)


@pytest.mark.parametrize("shape", SHAPES)
def test_filing_takes_time_linear_in_the_message(shape):
    """Eight times the spans (four times where each span's run grows with the message) may
    take at most three times as much longer as the message is (24 times for eight times the
    characters, the bound of test_slack_linear_unit); three rounds, the first within it kept."""
    spans = (64, 256) if shape == "a different run in each span" else (256, 2048)
    small, large = build(SHAPES[shape](spans[0])), build(SHAPES[shape](spans[1]))
    scale = size_of(large) / size_of(small)
    for _ in range(3):
        fast, slow = best_of_three(small), best_of_three(large)
        if slow <= 3 * scale * max(fast, 1e-4):
            break
    assert slow <= 3 * scale * max(fast, 1e-4), (fast, slow, scale)


def test_the_found_messages_are_left_out_by_the_segments_alone():
    """The anchoring the found file pins is told apart from the segments, not from cairn."""
    assert not anchored([[("bound", "k"), ("named", "`")]])
    assert not anchored([[("prose", "x"), ("bound", "x y"), ("named", "x y")]])
    assert anchored([[("named", "k"), ("named", "`")]])


LINK = {"type": "link", "url": "https://fable.test/x", "text": "x"}
BOLD = {"type": "text", "text": "b", "style": {"bold": True}}


def text_of(text: str) -> dict:
    return {"type": "text", "text": text}


def code_of(text: str) -> dict:
    return {"type": "text", "text": text, "style": {"code": True}}


def page_of(text: str, *sections: list[dict], kind: str = "rich_text_section") -> list:
    """What the page displays of the message's body: ("c", code) and ("t", text) pieces."""
    blocks = [{"type": kind, "elements": elements} for elements in sections]
    sent = {
        "type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text,
        "blocks": [{"type": "rich_text", "elements": blocks}],
    }  # fmt: skip
    body = filed(sent).text.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]
    rows, markup = reading(body)
    assert not [m for m in markup if m not in ("link_open", "strong_open")], markup
    return [piece for row in rows for piece in row if piece[1]]


@pytest.mark.parametrize(
    ("text", "elements", "page"),
    [
        ("a &lt;````", [text_of("a <`"), code_of("`")], [("t", "a <`"), ("c", "`")]),
        ("a &gt;````", [text_of("a >`"), code_of("`")], [("t", "a >`"), ("c", "`")]),
        ("&amp;amp;````", [text_of("&amp;`"), code_of("`")], [("t", "&amp;`"), ("c", "`")]),
        ("a\n````", [text_of("a\n`"), code_of("`")], [("t", "a"), ("t", "`"), ("c", "`")]),
        ("`````", [text_of("``"), code_of("`")], [("t", "``"), ("c", "`")]),
        ("`x`x`", [text_of("`x"), code_of("x")], [("t", "`x"), ("c", "x")]),
        ("y`y``y`", [text_of("y`y`"), code_of("y")], [("t", "y`y`"), ("c", "y")]),
        ("x````x````", [text_of("x`"), code_of("`"), text_of("x`"), code_of("`")],
         [("t", "x`"), ("c", "`"), ("t", "x`"), ("c", "`")]),
        ("<https://fable.test/x|x>````", [LINK, code_of("`"), text_of("`")],
         [("t", "x"), ("c", "`"), ("t", "`")]),
        ("`*b*```", [text_of("`"), BOLD, code_of("`")], [("t", "`"), ("t", "b"), ("c", "`")]),
        ("*b*````", [BOLD, code_of("`"), text_of("`")], [("t", "b"), ("c", "`"), ("t", "`")]),
        ("``a````", [text_of("`"), code_of("a"), code_of("`")],
         [("t", "`"), ("c", "a"), ("t", "`")]),
    ],
)  # fmt: skip
def test_the_text_before_a_span_places_it(text, elements, page):
    """The text before a span holding an entity, a line feed, backticks alone, or what stands
    before an earlier place too; a link or bold text before a span in a run of backticks."""
    assert page_of(text, elements) == page


@pytest.mark.parametrize(
    ("text", "sections", "kind", "page"),
    [  # the parity rule where the rich text tells nothing: across lines, in a quotation
        ("a`\n```", [[text_of("a`")], [code_of("`")]], "rich_text_section",
         [("t", "a`"), ("c", "`")]),
        ("```y```\n```", [[], [code_of("`")]], "rich_text_section", None),
        ("&gt; ```", [[code_of("`")]], "rich_text_quote", [("c", "`")]),
        ("`k`\n`a````", [[], [code_of("a"), code_of("`")]], "rich_text_section",
         [("c", "k"), ("c", "a"), ("t", "`")]),
    ],
)  # fmt: skip
def test_the_parity_rule_reads_each_line_alone(text, sections, kind, page):
    blocks = sections
    if text.startswith("```y"):  # a code block first, then a span on the next line
        pre = {"type": "rich_text_preformatted", "elements": [text_of("y")]}
        sent = {
            "type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text,
            "blocks": [{"type": "rich_text", "elements": [
                pre, {"type": "rich_text_section", "elements": [code_of("`")]}]}],
        }  # fmt: skip
        rows, markup = reading(filed(sent).text)
        assert [p for row in rows[1:-1] for p in row] == [("c", "`")]
        assert markup.count("fence") == 1
        return
    assert page_of(text, *blocks, kind=kind) == page


@pytest.mark.parametrize(
    ("code", "shows"), [(" ", " "), ("\t", "\t"), ("x ", "x "), ("  ", "  "), ("x\t", "x\t")]
)
def test_a_blank_that_ends_a_span_written_as_characters_is_kept(code, shows):
    """At the end of the message, of a line, in a passage alone, and in a table's cell; the
    title has no character reference (its code is between backticks there)."""
    sent = "`a``" + code + "`"
    assert page_of(sent, [code_of("a"), code_of(code)]) == [("c", "a"), ("t", shows)]
    lines = [[("named", "a"), ("named", code)], [("prose", "b")]]
    assert faults(lines) == []
    assert faults(lines, sections=True) == []
    table = [[("prose", "h | "), ("named", "a"), ("named", code)], [("prose", "--|--")]]
    draft = filed(build(table))
    assert "&#" not in draft.title and "&#" not in draft.shown
    tokens = READER.parse(draft.text)
    assert "th_open" in [t.type for t in tokens]  # a table: the guide's stated limit
    cells = ["".join(c.content for c in t.children or []) for t in tokens if t.type == "inline"]
    assert "a" + shows in cells, cells


def test_a_blank_kept_at_the_title_cut_is_no_character_reference():
    for pad in range(70, 80):
        lines = [[("prose", "w" * pad), ("named", "a"), ("named", "  "), ("prose", " tail")]]
        assert faults(lines) == []
        assert "&#" not in filed(build(lines)).shown
