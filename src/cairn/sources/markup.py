"""HTML to text: the one conversion every source uses for HTML it reads.

`html_to_text(html: str) -> str` turns HTML into plain text a note can hold. It is pure (no
network, no clock), uses only the standard library (html.parser), and never raises on
malformed HTML: it does what it can. Its rules (the shared specification's, numbered as
there; the example pairs are in the acceptance tests that use it):

1. Comments, and the whole content of `script`, `style`, `head`, `title` and `template`
   elements, are dropped.
2. `br` is a line break. Each of `p`, `div`, `section`, `article`, `header`, `footer`, `h1` to
   `h6`, `ul`, `ol`, `table`, `blockquote`, `pre`, `hr` begins and ends a paragraph (a blank
   line between it and what is around it). `li` and `tr` each begin a new line.
3. A list item's line begins with "- " (in `ul` and in `ol` alike; nesting is not shown).
4. The cells of a table row (`td`, `th`) are on one line, separated by " | ".
5. A heading's text is a paragraph of its own, with no "#" in front: the text must not start
   sections in the note it is written into.
6. A link `<a href="URL">words</a>` is written "words (URL)". When the words are empty, or
   equal the URL (ignoring a trailing "/" and an "http://" or "https://" in front), it is
   written as the URL alone. A `mailto:` or `tel:` link is written as its words alone, and a
   link whose `href` is empty, "#..." or "javascript:..." as its words alone.
7. An image is written as its `alt` text when it has one, and dropped otherwise. No address of
   an image is written.
8. Every other tag is removed and its text kept. Character references are decoded (`&amp;`,
   `&#39;`, `&nbsp;` as an ordinary space).
9. White space: inside a line, a run of spaces and tabs is one space, and a line has none at
   either end; inside `pre`, line breaks are kept (spaces are still collapsed); more than one
   blank line in a row is one; the result has no blank line at its start or end. Text with no
   tag at all comes back with its own line breaks kept and the same white-space rules applied.

How the rules are read where they leave a choice
- Outside `pre`, a line break in the HTML's own text is white space, as a browser shows it:
  only the elements above break lines. Consecutive `br` add up ("a<br><br>b" leaves a blank
  line); paragraph breaks do not (two blocks in a row leave one blank line).
- "Nesting is not shown": a list inside a list item does not open a paragraph of its own, so
  its items are lines of the one list, each beginning "- ". An item with no text writes no
  line.
- Input holds a tag when it has "<" followed by a letter, "/", "!" or "?"; anything else
  ("a < b") is text with no tag.
- A `head` left open ends where `body` begins. An `a` inside an `a` closes the first; one
  never closed is closed at the end. A stray end tag of a block still breaks the paragraph,
  and any other stray end tag is ignored.
- The words of a link are compared to its URL with white space collapsed; a URL is written
  with the white space at its ends removed. A `mailto:` or `tel:` link with no words writes
  nothing, so an address in `href` is never written.
"""

import re
from collections import Counter
from html import unescape
from html.parser import HTMLParser

HIDDEN = frozenset({"script", "style", "head", "title", "template"})
BLOCKS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "table",
        "blockquote",
        "pre",
        "hr",
    }
)
LISTS = frozenset({"ul", "ol"})
CELLS = frozenset({"td", "th"})
WORDS_ONLY = ("mailto:", "tel:", "#", "javascript:")
HAS_TAG = re.compile(r"<[A-Za-z/!?]")
SPACES = re.compile(r"[ \t]+")
HTML_SPACE = re.compile(r"[\r\n\f\v]")
SCHEME = re.compile(r"https?://", re.IGNORECASE)


def html_to_text(html: str) -> str:
    """`html` as plain text, by the rules in the module's docstring."""
    if not HAS_TAG.search(html):
        return _tidy(unescape(html).replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    reader = _Reader()
    reader.feed(html)
    reader.close()
    return reader.text()


def _tidy(lines: list[str]) -> str:
    """Each line's spaces and tabs collapsed and its ends stripped, runs of blank lines made
    one, and no blank line at either end."""
    kept: list[str] = []
    for line in lines:
        clean = SPACES.sub(" ", line.replace("\xa0", " ")).strip()
        if clean or (kept and kept[-1]):
            kept.append(clean)
    while kept and not kept[-1]:
        kept.pop()
    return "\n".join(kept)


def _bare(url: str) -> str:
    """A URL as rule 6 compares it: without "http://" or "https://" and a trailing "/"."""
    bare = SCHEME.sub("", url, count=1) if SCHEME.match(url) else url
    return bare.removesuffix("/")


# What the reader writes, before it is made into lines: text, a break of so many lines
# (paragraph breaks are the larger of two, line breaks from `br` add up), and a dash.


class _Break:
    __slots__ = ("adds", "lines")

    def __init__(self, lines: int, adds: bool) -> None:
        self.lines = lines
        self.adds = adds


_DASH = object()  # the next line of text is a list item's
_UNDASH = object()  # an item, or its list, ended before any text came


class _Link:
    """An open `a`: its href and where its words begin among the written pieces."""

    __slots__ = ("at", "href")

    def __init__(self, href: str, at: int) -> None:
        self.href = href
        self.at = at


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pieces: list[object] = []  # str, _Break, _DASH or _UNDASH
        self.hidden: list[str] = []  # the hidden elements open, innermost last
        self.open_hidden: Counter[str] = Counter()  # the same, counted by name
        self.lists = 0  # lists open
        self.pre = 0  # pre elements open
        self.link: _Link | None = None
        self.cells = 0  # cells written in the row so far
        self.pre_opened = False  # a newline right after <pre> is not the text's

    # What the parser calls

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body" and self.open_hidden["head"]:
            self._unhide("head")
        if tag in HIDDEN:
            self.hidden.append(tag)
            self.open_hidden[tag] += 1
            return
        if self.hidden:
            return
        if tag == "br":
            self._break(1, adds=True)
        elif tag in LISTS:
            if not self.lists:
                self._break(2)
            self.lists += 1
        elif tag in BLOCKS:
            self._break(2)
            if tag == "pre":
                self.pre += 1
                self.pre_opened = True
            if tag == "table":
                self.cells = 0
        elif tag == "li":
            self._break(1)
            self.pieces.append(_DASH)
        elif tag == "tr":
            self._break(1)
            self.cells = 0
        elif tag in CELLS:
            if self.cells:
                self.pieces.append(" | ")
            self.cells += 1
        elif tag == "a":
            self._close_link()
            href = dict(attrs).get("href") or ""
            self.link = _Link(href.strip(), len(self.pieces))
        elif tag == "img":
            alt = dict(attrs).get("alt") or ""
            if alt.strip():
                self.pieces.append(HTML_SPACE.sub(" ", alt))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in ("br", "img", "hr"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.open_hidden[tag]:
            self._unhide(tag)
            return
        if self.hidden:
            return
        if tag == "li":
            self.pieces.append(_UNDASH)
        elif tag in LISTS:
            self.lists = max(self.lists - 1, 0)
            self.pieces.append(_UNDASH)
            if not self.lists:
                self._break(2)
        elif tag in BLOCKS:
            self._break(2)
            if tag == "pre":
                self.pre = max(self.pre - 1, 0)
        elif tag == "a":
            self._close_link()

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        if not self.pre:
            self.pieces.append(HTML_SPACE.sub(" ", data))
            return
        text = data.replace("\r\n", "\n").replace("\r", "\n")
        if self.pre_opened:
            text = text.removeprefix("\n")
            self.pre_opened = False
        for index, line in enumerate(text.split("\n")):
            if index:
                self._break(1, adds=True)
            if line:
                self.pieces.append(line)

    def close(self) -> None:
        super().close()
        self._close_link()

    def _unhide(self, tag: str) -> None:
        """Close the open hidden element `tag` and every hidden element inside it."""
        while self.hidden:
            closed = self.hidden.pop()
            self.open_hidden[closed] -= 1
            if closed == tag:
                return

    # Links

    def _close_link(self) -> None:
        link, self.link = self.link, None
        if link is None:
            return
        words = " ".join("".join(p for p in self.pieces[link.at :] if isinstance(p, str)).split())
        href = link.href
        if not href or href.lower().startswith(WORDS_ONLY):
            return
        if not words or _bare(words) == _bare(href):
            self.pieces[link.at :] = [p for p in self.pieces[link.at :] if not isinstance(p, str)]
            self.pieces.append(href)
        else:
            self.pieces.append(f" ({href})")

    # Lines

    def _break(self, lines: int, adds: bool = False) -> None:
        self.pieces.append(_Break(lines, adds))

    def text(self) -> str:
        """The pieces as lines, by rule 9."""
        lines: list[str] = []
        line: list[str] = []
        waiting = 0  # line breaks before the next text
        dash = False
        for piece in self.pieces:
            if isinstance(piece, _Break):
                waiting = waiting + piece.lines if piece.adds else max(waiting, piece.lines)
                continue
            if piece is _DASH or piece is _UNDASH:
                dash = piece is _DASH
                continue
            assert isinstance(piece, str)
            if not piece.strip():
                if line and not waiting:
                    line.append(piece)
                continue
            if waiting:
                if line:
                    lines.append("".join(line))
                lines.extend([""] * (waiting - 1))
                line, waiting = [], 0
            if dash and not "".join(line).strip():
                line = ["- "]
                dash = False
            line.append(piece)
        if line:
            lines.append("".join(line))
        return _tidy(lines)
