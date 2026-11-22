"""Shared test helpers extracted from test_title_masking_unit."""

import sqlite3
from contextlib import closing

from cairn import relations
from cairn.spans import Span, code_read
from cairn.store.schema.shared import _CLAIM_INDEX

OPEN, CLOSE = "\ufdd0", "\ufdd1"


def passage(text: str) -> Span:
    end = 1 + text.count("\n")
    return Span(id="sp", source_id="n", kind="paragraph", heading_path=(), start_line=1,
                end_line=end, text=text)  # fmt: skip


def stretches_of(text: str) -> list[tuple[int, int]]:
    return [*relations.covered(passage(text)), *code_read(text)]


def words(texts: list[str]) -> list[list[tuple[int, int, str]]]:
    """Each text's words as the index has them: (start, end, word), in order. The words come
    from fts5vocab; where each stands, from highlight of a query of each of its words."""
    with closing(sqlite3.connect(":memory:")) as con:
        con.execute(_CLAIM_INDEX.format(index="t"))
        con.execute("CREATE VIRTUAL TABLE v USING fts5vocab (t, instance)")
        con.executemany("INSERT INTO t (rowid, text) VALUES (?, ?)", list(enumerate(texts, 1)))
        found = []
        for row, text in enumerate(texts, 1):
            terms = [term for (term,) in con.execute(
                "SELECT term FROM v WHERE doc = ? AND col = 'text' ORDER BY offset", (row,)
            )]  # fmt: skip
            if not terms:
                found.append([])
                continue
            asked = " OR ".join('"' + term.replace('"', '""') + '"' for term in set(terms))
            (marked,) = con.execute(
                "SELECT highlight(t, 5, ?, ?) FROM t WHERE rowid = ? AND t MATCH ?",
                (OPEN, CLOSE, row, "text : (" + asked + ")"),
            ).fetchone()
            spans = places(marked)
            assert len(spans) == len(terms), (text, terms, marked)
            found.append([(*at, term) for at, term in zip(spans, terms, strict=True)])
        return found


def places(marked: str) -> list[tuple[int, int]]:
    found, at, before = [], 0, 0
    while (start := marked.find(OPEN, at)) >= 0:
        end = marked.find(CLOSE, start)
        found.append((start - before, end - before - 1))
        at, before = end + 1, before + 2
    return found


def apart(start: int, end: int, stretches) -> bool:
    return all(end <= s or e <= start for s, e in stretches)


def standing(said: list[tuple[int, int, str]], title: str, stretches) -> list[tuple[int, int]]:
    """Where the title stands whole in prose, read here from the index's words of the text:
    its words side by side, from the first's start to the last's end, and no character of a
    stretch from there to there."""
    (asked,) = words([title])
    terms, size = [word for _, _, word in asked], len(asked)
    found = []
    for n in range(len(said) - size + 1):
        if [word for _, _, word in said[n : n + size]] == terms:
            place = (said[n][0], said[n + size - 1][1])
            if apart(*place, stretches):
                found.append(place)
    return found


STRETCHES = ["https://e.x/a", "/srv/x", "/_", "--force", "v1.2.3", "`x`", "` `", "`-`"]

BEFORE = {  # the word before the stretch, and what stands between them
    "letter": ("Alpha", ""), "letter-mark": ("Cafe\u0301", ""), "mark": ("Alpha", "-\u0301"),
    "digit": ("66", ""), "cjk": ("東京", ""), "underscore": ("Alpha", "_"),
    "hyphen": ("Alpha", "-"),
}  # fmt: skip

AFTER = {  # what stands between the stretch and the word after it, and the word
    "letter": ("", "Beta"), "letter-mark": ("", "e\u0301clair"), "mark": ("\u0301", "Beta"),
    "digit": ("", "77"), "cjk": ("", "大阪"), "underscore": ("_", "Beta"),
    "hyphen": ("-", "Beta"),
}  # fmt: skip


def flush_class() -> list[tuple[str, str]]:
    """(title, text): every stretch between the two words of a title of two, and between
    each pair of a title of three; and flush before and after a whole title."""
    cases = []
    for stretch in STRETCHES:
        for word, between in BEFORE.values():
            for then, after in AFTER.values():
                flush = f"{word}{between}{stretch}{then}{after}"
                cases.append((f"{word} {after}", f"Read {flush} today."))
                cases.append((f"{word} {after} Gamma", f"Read {flush} Gamma today."))
                cases.append((f"Delta {word} {after}", f"Read Delta {flush} today."))
            cases.append((f"Delta {word}", f"Read Delta {word}{between}{stretch} today."))
        for then, after in AFTER.values():
            cases.append((f"{after} Gamma", f"Read {stretch}{then}{after} Gamma today."))
    return cases
