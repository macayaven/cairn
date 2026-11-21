"""When two titles are the same topic (N1): the topic key, and the tag a page's address takes
when its slug is another topic's.

A note compiles into the page of its topic: its `topic`, else its title. Two titles are the
same topic when their keys are equal (`topic_key`). The rule, in the order it is applied:

- Letter case does not count (Unicode case folding: "RETRY" is "retry", Greek final sigma is
  sigma).
- An accent on a Latin or a Greek letter does not count ("Café" is "Cafe", "Résumé" is
  "resume", "Πολιτική" is "ΠΟΛΙΤΙΚΗ"), written composed or decomposed: Latin because a
  page's address has always dropped it, Greek because capitals are written without it.
- A combining mark on a letter of any other script is part of the letter and counts, written
  composed or decomposed: Cyrillic "й" is not "и", Japanese "が" is not "か", a Devanagari
  nukta or vowel sign and a Thai tone mark are part of the word ("की" is not "कि", "ข่าว"
  is not "ข้าว").
- Spaces, hyphens, underscores and slashes are one separator, however many of them stand
  together: "retry-policy", "retry_policy", "retry/policy" and "RETRY  POLICY" are
  "Retry policy".
- Sentence punctuation at the edges of a word does not count: . , ; : ! ? " ' ( ) [ ] { },
  the ellipsis, the en dash and the em dash ('"Retry" (policy)!' is "Retry policy", "日本語?"
  is "日本語", "Release 2.10." is "Release 2.10").
- Everything else counts: every letter and digit of every script, a symbol written into a
  word ("C++", "C#" and "C" are three topics; "R&D", "R D" and "RD" are three; "50%" is not
  "50"), and punctuation inside a word ("retry.policy" is not "retry policy", "Release
  2.10" is not "Release 2-10" or "Release 210"). Letters that look alike in two scripts are
  two letters (a Latin C is not the Cyrillic letter es, a Latin A not a Greek alpha). There is no
  cut: two titles that differ only in their last word are two topics, however long.
- A title with nothing left ("!!!", "???", the empty title) has the empty key: such titles
  are one topic.

What the rule leaves open, and so decides only by the steps above: ".NET" and "NET" are one
topic (the dot is at the word's edge), "e-mail" and "email" are two, full- and half-width
forms are two, Arabic with and without its vowel marks is two, Cyrillic yo and ie are two,
"ß" and "ss" are one (case folding), a symbol standing alone between spaces counts ("R & D"
is not "R D"), and CJK punctuation and curly quotes at a word's edge count.

A key is made once, from what was given, when a note is stored (cairn.store), and kept with
the note; a page's key is fixed when the page is made. This module is the rule as it is
today. The schema step that gave pages made before N1 their keys has its own copy of the
rule as it was then (cairn.store.schema), so a later change here never changes what that
step did; a change here changes no stored key either, only how later notes are read.
"""

import hashlib
import re
import unicodedata

# Spaces (any whitespace), hyphens, underscores and slashes: one separator, however many.
_SEPARATORS = re.compile(r"[\s\-_/]+")
# Sentence punctuation that does not count at the edges of a word.
EDGE_PUNCTUATION = ".,;:!?\"'()[]{}\u2026\u2013\u2014"
# The scripts whose accents do not count: a base letter whose Unicode name starts so.
_FOLDED_SCRIPTS = ("LATIN ", "GREEK ")
TAG_SIZE = 8  # hex characters of the tag a colliding topic's address ends with, at first


def topic_key(title: str) -> str:
    """The topic key of `title` (see the module): two titles are one topic when their keys
    are equal. The key is the title's words, case folded, accents on Latin and Greek letters
    dropped, edge punctuation stripped, joined by one space; "" when nothing is left."""
    text = unicodedata.normalize("NFD", unicodedata.normalize("NFD", title).casefold())
    kept: list[str] = []
    folds = False  # whether the letter the marks that follow sit on is Latin or Greek
    for char in text:
        if unicodedata.combining(char):
            if folds:
                continue  # an accent on a Latin or Greek letter
        else:
            folds = unicodedata.name(char, "").startswith(_FOLDED_SCRIPTS)
        kept.append(char)
    words = (word.strip(EDGE_PUNCTUATION) for word in _SEPARATORS.split("".join(kept)))
    return unicodedata.normalize("NFC", " ".join(word for word in words if word))


def topic_tag(key: str, size: int = TAG_SIZE) -> str:
    """The tag of a topic key: the first `size` lower-case hex characters of the SHA-256 of
    its UTF-8 bytes. The same key gives the same tag on every install."""
    return hashlib.sha256(key.encode()).hexdigest()[:size]


def one_line(title: str) -> str:
    """A title, topic or name written as one line of text, wherever it is put into Markdown or
    plain text read as lines: a page body's heading (compiler.heading_text), Slack, the
    export, the command line (review blocker 1). A title of one line is as it is; in any
    other, each line break (every one str.splitlines knows, as cairn.spans splits a note: CR,
    LF, the line and paragraph separators and the rest) with the blanks around it is one
    space, and blank lines are dropped. What is stored and keyed is never changed by it."""
    lines = title.splitlines()
    if lines == [title]:
        return title
    return " ".join(line.strip() for line in lines if line.strip())
