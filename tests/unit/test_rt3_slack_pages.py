"""File generated Slack messages through the message shortcut, as a person does, and hold
what the page shows to what Slack displayed (test_rt3_slack_model): the note read whole, each
claim rendered alone as the page view renders it (cairn.web.markdown.render, the `md`
filter), and the page's assembled body. Every fault is shrunk to its smallest message.

The checks, by severity:
- link: every link any view shows is Slack's own, to Slack's own address, with Slack's own
  words; or the note's source link. No image, ever.
- heading: one heading, the author's, under the passage splitter and CommonMark; none in a
  claim alone; in the body, the title and the author's heading only.
- code: each line Slack displayed as code is shown as code, byte for byte; each line the
  page shows as code is a line Slack displayed (as cairn's guide writes it: a link as
  `[words](address)`), less only indentation at its start.
- text: the characters a person reads in the claims are those Slack displayed, less only
  marks Markdown may take as structure or emphasis.
- source: the note's source link is a link on the page.
- refused: a message with text is filed, or refused for a stated reason.
- author: the author line and the page title show what Slack displayed, with no escape.
"""

import asyncio
import os
import random
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from urllib.parse import unquote

import pytest
import test_slack_shortcuts_unit as shortcuts
from markdown_it import MarkdownIt
from test_rt3_slack_model import NAMES, Piece, a_message, an_author, decode, display
from test_slack_commands_unit import ADA_SLACK, REPLY, Bench

from cairn.slack import text as slack_text
from cairn.spans import chunk
from cairn.web.markdown import render

READER = MarkdownIt("commonmark", {"html": False}).enable("table")  # headings and images on
WHEN = "2030-03-17 17:46:40 UTC"
SOURCE = "https://fable-works.slack.com/archives/C0LIBRARY1/p1900000000000100"
SOURCE_LINE = f"[Source in Slack]({SOURCE})"
SEVERITY = [
    "link", "image", "heading", "code", "source", "refused", "reply", "title", "text", "author"
]  # fmt: skip


# Filing, as a person does: the shortcut on a message, the note, its page


@dataclass
class Filed:
    raw: str
    author: str
    reply: str
    note: str | None = None
    claims: list[str] = field(default_factory=list)
    body: str | None = None
    title: str | None = None
    paths: list[tuple[str, ...]] = field(default_factory=list)


class Filer:
    """A fresh workspace for each message, in one folder."""

    def __init__(self, folder: Path, rich: bool = False) -> None:
        self.folder = folder
        self.rich = rich  # send Slack's rich text with the text, as Slack does

    def __call__(self, raw: str, author: str = "Cedar Quill") -> Filed:
        for leftover in self.folder.glob("cairn.db*"):
            leftover.unlink()
        bench = Bench(self.folder)
        try:
            bench.users[shortcuts.CEDAR] = shortcuts.cedar(
                real_name=author, profile={"real_name": author, "display_name": "Cedar"}
            )
            bench.link(ADA_SLACK, bench.ada, bench.library)
            message = {"type": "message", "user": shortcuts.CEDAR, "ts": "1900000000.000100"}
            sent = {**message, "text": raw}
            if self.rich and (blocks := rich_text(raw, author)) is not None:
                sent["blocks"] = blocks
            response = shortcuts.handle(bench, sent)
            asyncio.run(response.background())
            (reply,) = [body["text"] for where, body in bench.calls if where == REPLY]
            filed = Filed(raw, author, reply)
            notes = bench.notes()
            if not notes:
                return filed
            ((note_id, project, note, _),) = notes
            filed.note = note
            filed.paths = [span.heading_path for span in bench.owner.note_spans(note_id)]
            listed = bench.owner.list_pages(project)
            if listed:
                (one,) = listed
                page = bench.owner.get_page(project, one.slug)
                filed.claims = [claim.text for claim in page.claims]
                filed.body, filed.title = page.body, page.title
            return filed
        finally:
            bench.store.close()


def rich_text(raw: str, author: str) -> list | None:
    """Slack's rich text for `raw`, as the model reads it: a preformatted element for each
    code block (its lines less a first or last line of blanks), a code-styled text element
    for each code span. None where the model leaves any of it open."""
    shown = display(raw, {shortcuts.CEDAR: author})
    if shown.unsure:
        return None
    elements: list[dict] = []
    section: list[dict] = []
    for piece in shown.pieces:
        if piece.kind == "block":
            if section:
                elements.append({"type": "rich_text_section", "elements": section})
                section = []
            lines = piece.shown.split("\n")
            while lines and not lines[0].strip(" \t"):
                del lines[0]
            while lines and not lines[-1].strip(" \t"):
                del lines[-1]
            text = "\n".join(lines)
            elements.append(
                {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": text}]}
            )
        elif piece.kind == "code":
            section.append({"type": "text", "text": piece.shown, "style": {"code": True}})
        elif piece.kind == "link":
            section.append({"type": "link", "url": piece.target, "text": piece.shown})
        else:
            section.append({"type": "text", "text": piece.shown})
    if section:
        elements.append({"type": "rich_text_section", "elements": section})
    return [{"type": "rich_text", "block_id": "rt3", "elements": elements}]


# What a view shows


class Shown(HTMLParser):
    """An HTML view's links, images, code and text."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.images: list[str] = []
        self.code: list[tuple[bool, str]] = []  # (in a <pre>, text)
        self.text: list[str] = []
        self.tags: Counter[str] = Counter()
        self._open: list[str] = []
        self._link: list[str] | None = None
        self._code: list | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        self.tags[tag] += 1
        if tag == "a":
            self._link = [found.get("href") or "", ""]
        elif tag == "img":
            self.images.append(found.get("src") or "")
        elif tag == "code":
            self._code = ["pre" in self._open, ""]
        if tag not in ("br", "hr", "img", "input"):
            self._open.append(tag)

    def handle_endtag(self, tag):
        if tag == "a" and self._link is not None:
            self.links.append((self._link[0], self._link[1]))
            self._link = None
        elif tag == "code" and self._code is not None:
            self.code.append((self._code[0], self._code[1]))
            self._code = None
        if tag in self._open:
            del self._open[len(self._open) - 1 - self._open[::-1].index(tag) :]

    def handle_data(self, data):
        if self._link is not None:
            self._link[1] += data
        if self._code is not None:
            self._code[1] += data
        self.text.append(data)


def _headings_md(markdown: str) -> list[int]:
    tokens = READER.parse(markdown)
    return [t.map[0] for t in tokens if t.type == "heading_open" and t.map]


def _headings_split(markdown: str) -> list[int]:
    return [span.start_line - 1 for span in chunk("", markdown) if span.kind == "heading"]


# The checks. A fault is (class, kind, view, detail); its signature, (class, kind), is what
# a shrink keeps.

Fault = tuple[str, str, str, str]
names = dict(NAMES)  # the author's Slack id and name, as the message being checked has them


def _norm_href(href: str) -> str:
    return unquote(href)


def _code_texts(raw: str) -> list[str]:
    return [p.shown for p in display(raw, names).pieces if p.kind in ("code", "block")]


def _link_faults(raw: str, html: str, view: str) -> list[Fault]:
    """Links and images in one HTML view that Slack did not make."""
    shown = display(raw, names)
    allowed = {(_norm_href(SOURCE), "Source in Slack")}
    allowed |= {(_norm_href(t), label) for t, label in shown.links + shown.any_links}
    found, out = Shown(html), []
    pieces = [p for p in shown.pieces if p.kind in ("code", "block")]

    def where(words: str, href: str) -> str:
        for piece in pieces:
            if (words and words in piece.shown) or (href and _norm_href(href) in piece.shown):
                return f"from a Slack code {'block' if piece.kind == 'block' else 'span'}"
        return "not Slack's"

    for href, words in found.links:
        if (_norm_href(href), words) in allowed:
            continue
        out.append(("link", where(words, href), view, f"href={href!r} words={words!r}"))
    for src in found.images:
        out.append(("image", where("", src), view, src))
    return out


def _code_lines(html: str) -> list[list[str]]:
    blocks = []
    for in_pre, text in Shown(html).code:
        if in_pre:
            lines = text.split("\n")
            if lines and lines[-1] == "":
                lines = lines[:-1]
            blocks.append(lines)
    return blocks


def _inline_code(html: str) -> list[str]:
    return [text for in_pre, text in Shown(html).code if not in_pre]


def _contains(lines: list[str], part: list[str]) -> bool:
    return any(lines[at : at + len(part)] == part for at in range(len(lines) - len(part) + 1))


_LEAD = r"[ \t>*+\-0-9.)]*"  # indentation, a quote or a list marker a code line may lose
_DELETABLE = set("*_~-+.)0123456789>|:=#")  # structure and emphasis Markdown may take

# The limits the connect guide states (src/cairn/guides/connect.md), which the generated run
# does not count; each is pinned in test_rt3_slack_found as the guide says it.
LIMIT_PASSAGE = (  # finding 5
    "there it shows the backslashes and entities that write it as text, and not the blanks"
    " that end it"
)
LIMIT_HEADINGS = (  # finding 7, the page's own title and author heading
    "the backslash or entity that keeps it text shows there, and bold text shows its asterisks"
)
LIMIT_MENTION = "a mention of anyone else is their member ID"  # finding 8


def as_text(markdown_line: str) -> str:
    """What a line written as text shows when it is read as code instead (LIMIT_PASSAGE,
    LIMIT_HEADINGS): its backslash escapes and its three entities as the characters they
    write. Nothing else is undone: doubled asterisks, say, are not in the guide's limit."""
    line = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", markdown_line)
    return re.sub(r"&(amp|lt|gt);", lambda m: {"amp": "&", "lt": "<", "gt": ">"}[m[1]], line)


def _note_code(note_html: str) -> set[str]:
    """Every line the note read whole shows as code."""
    return {line for block in _code_lines(note_html) for line in block}


def _patterns(raw: str, squash: bool, relax: bool = False) -> list[str]:
    """For each line Slack displays, a pattern of what a page may show of it: a link as its
    words, as `[words](address)` (cairn's guide) or as "[link omitted]"; a code span with or
    without its backticks; what Slack's page leaves open, anything. `squash`: white space
    left out, and marks Markdown may take as structure or emphasis optional (the text
    check); else the line as a code line shows it (the code check), its lead (indentation,
    quote and list marks at its start) optional. `relax`: white space optional too."""
    lines: list[list[str]] = [[]]
    leading = [True]  # whether the line so far is lead alone
    wild: set[tuple[int, int]] = set()  # where a part is what Slack's page leaves open

    def literal(text: str, optional: set[str] = frozenset()) -> None:
        for char in text:
            if char == "\n":
                lines.append([])
                leading.append(True)
                continue
            lead = leading[-1] and char in " \t>*+-0123456789.)"
            leading[-1] = lead
            if squash and char.isspace():
                continue
            if char in optional or (not squash and lead) or (relax and char in " \t"):
                lines[-1].append(f"(?:{re.escape(char)})?")
            else:
                lines[-1].append(re.escape(char))

    for piece in display(raw, names).pieces:
        if "@" in piece.shown and piece.kind in ("text", "code", "block"):
            piece = Piece(piece.kind, piece.shown, sure=False)  # an email address is removed
        if piece.kind == "text" and piece.sure:
            literal(piece.shown, _DELETABLE if squash else set())
        elif piece.kind == "code" and piece.sure:
            leading[-1] = False
            lines[-1].append("(?:`)?")
            literal(piece.shown)
            lines[-1].append("(?:`)?")
        elif piece.kind == "block" and piece.sure and not squash:  # on lines of its own
            if lines[-1]:
                lines.append([])
                leading.append(True)
            leading[-1] = False
            literal(piece.shown)
            lines.append([])
            leading.append(True)
        elif piece.kind == "block" and piece.sure:
            lines[-1].append("(?:```)?")
            literal(piece.shown)
            lines[-1].append("(?:```)?")
        elif piece.kind == "link" and piece.sure:
            leading[-1] = False
            words = re.escape(re.sub(r"\s", "", piece.shown) if squash else piece.shown)
            omitted = r"\[linkomitted\]" if squash else r"\[link omitted\]"
            lines[-1].append(rf"(?:\[{words}\]\([^\n]*?\)|{words}|{omitted})")
        elif piece.kind in ("mention", "special") and piece.sure:
            literal(piece.shown)
        elif piece.kind == "mention" and piece.target:  # a stated limit (LIMIT_MENTION)
            literal("@" + piece.target)
        else:  # what Slack's page leaves open (it may show nothing: the lead goes on)
            for _ in range(piece.shown.count("\n") + 1):
                wild.add((len(lines) - 1, len(lines[-1])))
                lines[-1].append("[^\n]*?" if not squash else ".*?")
                lines.append([])
                leading.append(True)
            lines.pop()
            leading.pop()
    found = ["".join(parts) for parts in lines]
    if not squash:  # a block in what is left open may put the line's ends on lines of their own
        for row, at in sorted(wild):
            found += ["".join(lines[row][at:]), "".join(lines[row][: at + 1])]
    return found


def _code_faults(raw: str, views: dict[str, str]) -> list[Fault]:
    """Slack's code shown as code, byte for byte; and code on the page that Slack displayed."""
    shown = display(raw, names)
    patterns = [re.compile(_LEAD + p) for p in _patterns(raw, squash=False)]
    relaxed = [re.compile(_LEAD + p) for p in _patterns(raw, squash=False, relax=True)]
    slack_code = {line for block in shown.code_blocks for line in block}
    slack_code |= {  # a block Slack's page leaves open may be shown either way
        line
        for p in shown.pieces
        if p.kind == "block" and not p.sure
        for line in p.shown.split("\n")
    }
    out: list[Fault] = []
    in_note = _note_code(views["note"])
    rows = shown.text().split("\n")
    # Stated in the connect guide, and not counted: an email address is removed (any text
    # with an "@" may be read as one); in a message that could hold a table, code within a
    # line that holds a "|" is plain characters; and a message in which a line would open
    # or end a fence as displayed keeps the marks of prose where both read code.
    table = any(
        "|" in above and "-" in row and re.fullmatch(r"[ \t>]*[|:-][|: \t-]*", row)
        for above, row in pairwise(rows)
    )
    fenced = any(re.match(r"[ \t>*+\-0-9.)]*(```|~~~)", row) for row in rows)
    for view, html in views.items():
        blocks, inline = _code_lines(html), _inline_code(html)
        for block in shown.code_blocks:
            if not block or any(_contains(lines, block) for lines in blocks):
                continue
            if any("@" in line for line in block):
                continue  # the guide's redaction
            if len(block) == 1 and block[0] in inline:
                continue
            if all(any(line in shown_line for c in blocks for shown_line in c) for line in block):
                kind = "block split or joined"
            elif len(block) > 1 and " ".join(block) in " ".join(inline):
                kind = "block shown inline"
            elif all(any(line.rstrip(" \t") == s.rstrip(" \t") for c in blocks for s in c)
                     for line in block if line.strip()):  # fmt: skip
                kind = "trailing white space lost (Slack code)"
            else:
                kind = "block not code"
            out.append(("code", kind, view, f"Slack code block {block!r}"))
        for span in shown.inline_code:
            trimmed = span[1:-1] if span.startswith(" ") and span.endswith(" ") else span
            if span.strip() == "" or span in inline or trimmed in inline:
                continue
            if "@" in span or ("|" in span and table):
                continue  # the guide's redaction; its rule for a table's bars
            if any(span in line for lines in blocks for line in lines):
                continue
            out.append(("code", "span not code", view, f"Slack code span {span!r}"))
        for lines in blocks:
            for line in lines:
                if line in ("", SOURCE_LINE) or line in slack_code or "[email removed]" in line:
                    continue  # the source link is its own check; an email is removed (guide)
                if any(p.fullmatch(line) for p in patterns):
                    continue
                if (
                    view != "note"
                    and line not in in_note
                    and any(p.fullmatch(as_text(line).rstrip(" \t")) for p in relaxed)
                ):
                    continue  # read as code in its passage alone: LIMIT_PASSAGE
                prose = re.sub(r"\\(.)", r"\1", line).replace("**", "*")
                if fenced and any(p.fullmatch(as_text(prose)) for p in relaxed):
                    continue  # the guide's fence exception
                if any(p.fullmatch(line) for p in relaxed):
                    kind = "trailing white space lost (Slack text read as code)"
                elif any(p.fullmatch(re.sub(r"\\(.)", r"\1", line)) for p in patterns):
                    kind = "escape shown in code"
                elif any(p.fullmatch(line.replace("**", "*").replace("~~", "~")) for p in patterns):
                    kind = "marks of prose in code"
                else:
                    kind = "line not displayed"
                out.append(("code", kind, view, f"code line {line!r}"))
    return out


def _text_faults(raw: str, claims: list[str], in_note: set[str] = frozenset()) -> list[Fault]:
    """The characters the claims show against those Slack displayed; code a claim alone
    shows that the note read as text is taken as the guide's limit says it shows."""
    seen = []
    slack_code = {line for block in display(raw, names).code_blocks for line in block}
    fenced = any(
        re.match(r"[ \t>*+\-0-9.)]*(```|~~~)", row) for row in display(raw).text().split("\n")
    )
    for claim in claims:
        if claim.strip() == SOURCE_LINE:
            continue
        found = Shown(str(render(claim)))
        text = "".join(found.text)
        for in_pre, code in found.code:
            lines = code.rstrip("\n").split("\n")
            if in_pre and all(line in slack_code for line in lines if line.strip()):
                continue  # Slack's own code: shown as it is
            if in_pre and fenced:  # the guide's fence exception: the marks of prose stay
                text = text.replace(code, as_text(code).replace("**", "*"), 1)
            elif in_pre and not any(line in in_note for line in lines if line.strip()):
                text = text.replace(code, as_text(code), 1)  # LIMIT_PASSAGE
        seen.append(text)
    page = re.sub(r"\s", "", "".join(seen))
    for written in (SOURCE_LINE, "Source in Slack"):  # the source swallowed: its own class
        if written.replace(" ", "") in page:
            page = page.replace(written.replace(" ", ""), "", 1)
            break
    pattern = "".join(_patterns(raw, squash=True))
    if len(pattern) > 8000:
        return []
    if re.fullmatch(pattern, page, re.DOTALL):
        return []
    kind = "other"
    if re.fullmatch(pattern, re.sub(r"\\(.)", r"\1", page), re.DOTALL):
        kind = "escape shown"
    elif re.fullmatch(pattern, page.replace("~~", "~"), re.DOTALL):
        kind = "strike as tildes"
    elif re.fullmatch(pattern, page.replace("&amp;", "&").replace("&lt;", "<"), re.DOTALL):
        kind = "entity shown"
    elif re.fullmatch(pattern, page.replace("**", "*"), re.DOTALL):
        kind = "asterisks doubled"
    return [("text", kind, "claim", f"page reads {page[:160]!r}")]


def faults(filed: Filed) -> list[Fault]:
    """Every fault of a filed message."""
    raw = filed.raw
    names.clear()
    names[shortcuts.CEDAR] = filed.author
    shown = display(raw, names)
    out: list[Fault] = []
    if filed.note is None:
        if shown.text().strip() and not filed.reply.startswith("This message has no text"):
            out.append(("refused", "reason", "reply", filed.reply))
        return out
    author = " ".join(filed.author.split())
    note = filed.note
    # heading
    if _headings_split(note) != [0]:
        out.append(("heading", "second", "note-splitter", str(_headings_split(note))))
    if _headings_md(note) != [0]:
        out.append(("heading", "second", "note-commonmark", str(_headings_md(note))))
    if len(set(filed.paths)) != 1 or any(len(path) != 1 for path in filed.paths):
        out.append(("heading", "path", "passages", repr(sorted(set(filed.paths)))[:300]))
    for claim in filed.claims:
        if _headings_split(claim) or _headings_md(claim):
            out.append(("heading", "in a claim", "claim", repr(claim[:120])))
    if filed.body is not None:
        lines = filed.body.split("\n")
        own = {0} | {at for at, line in enumerate(lines) if line.startswith("## ") and WHEN in line}
        if len(own) != 2:
            out.append(("heading", "written", "body", repr(sorted(own))))
        for reader, found in (("splitter", _headings_split), ("commonmark", _headings_md)):
            if set(found(filed.body)) != own:
                out.append(("heading", "second", f"body-{reader}", repr(found(filed.body))))
    # link and image
    views = {"note": str(render(note)), "note-commonmark": READER.render(note)}
    views |= {f"claim {n}": str(render(claim)) for n, claim in enumerate(filed.claims)}
    if filed.body is not None:
        views["body"] = READER.render(filed.body)
    for view, html in views.items():
        out += [(c, k, v.split(" ")[0], d) for c, k, v, d in _link_faults(raw, html, view)]
    # the source link
    if not any(
        (href, words) == (SOURCE, "Source in Slack")
        for claim in filed.claims
        for href, words in Shown(str(render(claim))).links
    ):
        out.append(("source", "not a link", "claim", "the note's source link is not a link"))
    # code
    code_views = {"note": views["note"]}
    code_views["claim"] = "\n".join(str(render(claim)) for claim in filed.claims)
    if filed.body is not None:
        code_views["body"] = views["body"]
    out += _code_faults(raw, code_views)
    # text
    out += _text_faults(raw, filed.claims, _note_code(views["note"]))
    # author: as the note writes it, escapes and entities showing (LIMIT_HEADINGS)
    heading = chunk("", note)[0].heading_path[0]
    entities = re.search(r"&(amp|lt|gt);", author)  # whether Slack sends a name so: open
    if not entities and as_text(heading) != f"{author} · {WHEN}":
        out.append(("author", "not the name", "page-heading", repr(heading)))
    # title, in the body's heading and in the reply in Slack
    out += _title_faults(filed, shown)
    return out


def _title_line(shown) -> tuple[str, bool, str] | None:
    """The first line the message displays, as the guide says the title holds it: code
    within a line as plain characters between its backticks, a code block's lines on lines
    of their own and without fences, a link as its words, a mention of anyone but the author
    as their member ID. With whether the model is sure of it, and the marks a code block
    displayed on it as characters (which Slack's own formatting would read). None when no
    line displays anything."""
    lines: list[list] = [["", True, ""]]

    def add(text: str, sure: bool = True, marks: str = "") -> None:
        for at, part in enumerate(text.split("\n")):
            if at:
                lines.append(["", True, ""])
            lines[-1][0] += part
            lines[-1][1] = lines[-1][1] and sure
            lines[-1][2] += marks

    for piece in shown.pieces:
        if piece.kind == "block":
            rows = piece.shown.split("\n")
            if rows and not rows[0].strip(" \t") and len(rows) > 1:
                rows = rows[1:]
            if rows and not rows[-1].strip(" \t") and len(rows) > 1:
                rows = rows[:-1]
            lines.append(["", True, ""])
            for row in rows:  # a code line is displayed, a quotation's mark and all
                sure = piece.sure and "@" not in row
                lines[-1] = [row, sure, "".join(c for c in "*_~`" if c in row), True]
                lines.append(["", True, ""])
        elif piece.kind == "code":
            add(f"`{piece.shown}`", piece.sure)
        elif piece.kind == "mention" and not piece.sure and piece.target:
            add("@" + piece.target)  # LIMIT_MENTION
        elif piece.kind == "link":  # a link the note will not write is "[link omitted]"
            add(piece.shown, piece.sure and re.match(r"https?://", piece.target or "") is not None)
        elif piece.kind in ("mention", "special"):
            add(piece.shown, piece.sure)
        else:
            add(piece.shown, piece.sure and "@" not in piece.shown)  # an address is removed
    for text, sure, marks, *code in lines:
        if text.strip(" \t" if code else " \t>"):
            return text, sure, marks
    return None


def _squash(text: str) -> str:
    """For comparing what two readings display: no white space, no quotation mark at the
    start, no mark of emphasis (a heading shows bold as bold)."""
    return re.sub(r"[\s*_]", "", text.lstrip(" \t>"))


def _title_faults(filed: Filed, shown) -> list[Fault]:
    """The title is the first line Slack displayed, as text, cut to 80 characters: the
    body's heading of it holds no code, link or image and displays the start of that line,
    with no mark Slack did not display; the reply in Slack names it as Slack displayed it."""
    out: list[Fault] = []
    found = None if shown.breaks_unsure else _title_line(shown)
    if filed.body is None or found is None or not found[1]:
        return out
    line, _, marks = found
    want = _squash(line)
    h1 = Shown(READER.render(filed.body.split("\n", 1)[0]))
    if h1.links or h1.images or h1.code:
        out.append(("title", "markup in its heading", "body-h1", repr(filed.title)))
    displayed = "".join(h1.text).strip()
    got = _squash(displayed)
    if want and (not got or not want.startswith(got) or len(got) < min(len(want), 16)):
        out.append(("title", "not the first line displayed", "body-h1", repr(displayed)))
    if displayed.count("*") > line.count("*") or displayed.count("~") > line.count("~"):
        out.append(("title", "marks doubled", "body-h1", repr(displayed)))
    said = re.match(r"(?:Already filed|Filed): (.*)\nProject: ", filed.reply, re.DOTALL)
    if said is None:
        return out
    named = said.group(1)
    if "<" in named:
        out.append(("reply", "Slack markup", "reply", repr(named)))
    reads = decode(named)
    got = _squash(reads)
    if want and (not got or not want.startswith(got) or len(got) < min(len(want), 16)):
        out.append(("reply", "not the first line displayed", "reply", repr(reads)))
    if reads.count("*") > line.count("*"):
        out.append(("reply", "marks doubled", "reply", repr(reads)))
    styled = [
        m
        for m in marks
        if re.search(rf"{re.escape(m)}[^\s{re.escape(m)}][^\n]*?(?<!\s){re.escape(m)}", reads)
    ]
    if styled:  # Slack's mrkdwn styles these, and has no escape for them
        out.append(("reply", "code read as Slack formatting", "reply", repr(reads)))
    return out


def signatures(found: list[Fault]) -> set[tuple[str, str]]:
    return {(kind, sub) for kind, sub, _, _ in found}


# Shrinking


def shrink(filer: Filer, raw: str, signature: tuple[str, str], author: str = "Cedar Quill",
           budget: int = 400) -> str:  # fmt: skip
    """The smallest message (lines, then characters) that still has the fault `signature`."""
    tries = 0

    def keeps(text: str) -> bool:
        nonlocal tries
        tries += 1
        if not text.strip():
            return False
        return signature in signatures(faults(filer(text, author)))

    best = raw
    for unit in ("\n", None):
        size = max(1, len(best.split("\n")) // 2) if unit else max(1, len(best) // 2)
        while size >= 1 and tries < budget:
            parts = best.split("\n") if unit else list(best)
            changed = False
            at = 0
            while at < len(parts) and tries < budget:
                candidate = parts[:at] + parts[at + size :]
                text = ("\n" if unit else "").join(candidate)
                if candidate and keeps(text):
                    best, parts, changed = text, candidate, True
                else:
                    at += size
            if not changed:
                size //= 2
    return best


# The campaign

COUNT = int(os.environ.get("RT3_MESSAGES", "600"))
SEED = int(os.environ.get("RT3_SEED", "20261120"))


def campaign(filer: Filer, count: int, seed: int, keep: int = 3) -> dict:
    """`count` messages from `seed`: the faults by signature (up to `keep` messages each),
    how often each was seen and in which views, and what each conversion cost."""
    found: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    seen: Counter[tuple[str, str]] = Counter()
    views: dict[tuple[str, str], set[str]] = {}
    cost: list[tuple[int, int, float, str]] = []
    parse, split = slack_text._PARSER.parse, slack_text.chunk
    counted: Counter[str] = Counter()

    def counting_parse(*args, **kwargs):
        counted["parse"] += 1
        return parse(*args, **kwargs)

    def counting_chunk(*args, **kwargs):
        counted["chunk"] += 1
        return split(*args, **kwargs)

    for n in range(count):
        rng = random.Random(seed * 100003 + n)
        raw = a_message(rng)
        author = an_author(rng) if rng.random() < 0.1 else "Cedar Quill"
        counted.clear()
        slack_text._PARSER.parse, slack_text.chunk = counting_parse, counting_chunk
        start = time.perf_counter()
        try:
            filed = filer(raw, author)
        finally:
            slack_text._PARSER.parse, slack_text.chunk = parse, split
        spent = time.perf_counter() - start
        cost.append((counted["parse"] + counted["chunk"], len(raw), spent, raw))
        for signature in signatures(got := faults(filed)):
            seen[signature] += 1
            views.setdefault(signature, set()).update(
                v for c, k, v, _ in got if (c, k) == signature
            )
            detail = next(d for c, k, _, d in got if (c, k) == signature)
            if len(found.setdefault(signature, [])) < keep:
                found[signature].append((raw, author, detail))
    return {"found": found, "seen": seen, "views": views, "cost": cost, "count": count}


def report(filer: Filer, result: dict, shrink_budget: int = 300) -> list[str]:
    lines = [f"{result['count']} messages"]
    order = sorted(result["found"], key=lambda sig: (SEVERITY.index(sig[0]), sig))
    for signature in order:
        smallest = set()
        for raw, author, _ in result["found"][signature]:
            small = shrink(filer, raw, signature, author, shrink_budget)
            got = faults(filer(small, author))
            detail = next((d for c, k, _, d in got if (c, k) == signature), "?")
            smallest.add((small, author, detail))
        views = ",".join(sorted(result["views"][signature]))
        lines.append(f"{signature[0]}: {signature[1]} x{result['seen'][signature]} in {views}")
        for small, author, detail in sorted(smallest, key=lambda s: len(s[0])):
            who = "" if author == "Cedar Quill" else f" author={author!r}"
            lines.append(f"    {small!r}{who} :: {detail[:200]}")
    worst = max(result["cost"], key=lambda c: c[0])
    slowest = max(result["cost"], key=lambda c: c[2] / max(c[1], 1))
    lines.append(f"most readings: {worst[0]} for {worst[1]} characters: {worst[3][:100]!r}")
    lines.append(f"slowest per character: {slowest[2]:.3f}s for {slowest[1]} characters")
    return lines


@pytest.fixture
def filer(tmp_path):
    return Filer(tmp_path)


def test_rt3_generated_messages_show_what_slack_displayed(filer):
    """RT3_MESSAGES messages from RT3_SEED (600 from 20261120 by default), filed through the
    shortcut; each fault shrunk to its smallest message. The first run, on 0d4e71a8 (the
    candidate as the seventh report found it), rediscovered both of its Slack faults by
    itself: a Slack code block whose text the claim alone renders as a link while the note
    reads it as code ("link: from a Slack code block", seen in the claim and not the note,
    e.g. '```\\n```i\\n\\t```\\n[](h)```'), and white space lost at the end of a line both
    readers read as code ("code: trailing white space lost (Slack text read as code)",
    e.g. '___\\n\\tt ')."""
    result = campaign(filer, COUNT, SEED, keep=1)
    lines = report(filer, result, shrink_budget=150)
    print("\n".join(lines))
    assert not result["found"], "\n" + "\n".join(lines)
