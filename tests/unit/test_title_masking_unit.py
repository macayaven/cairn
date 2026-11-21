"""What is not prose is blanked out of a passage without moving where any of the index's
words begins or ends (relations.blanked, with the index's own reading of each character,
cairn.store.relating._kinds). Checked against FTS5 alone: the words of a text and the
characters each stands on are what an index of the claims' declaration reports (fts5vocab,
instance, and highlight), never a reading of the characters written here. Seeded, made-up
texts: facts flush before and after a title's words with no blank between, in NFC and NFD,
with combining marks, digits, CJK, an apostrophe, a hyphen, an underscore, and characters of
private use beside the one that blanks."""

import random
import sqlite3
import unicodedata
from contextlib import closing

from cairn import relations
from cairn.spans import Span, code_read
from cairn.store import relating
from cairn.store.ranking import quoted
from cairn.store.schema.shared import _CLAIM_INDEX

OPEN, CLOSE = "\ufdd0", "\ufdd1"
TITLES = ["Café", "Café Noir"]
PIECES = ["Café", "Cafe\u0301", "Noir", "Café Noir", "Cafe\u0301 Noir", " ", "  ", "\n"]
PIECES += ["/notes", "/srv/x", "--draft", "-v"]
PIECES += ["v1.2.3", "1.2.3", "https://e.x/a", "`", "``", "`x`", "'", "-", "_", "日本", "語"]
PIECES += ["42", "\u0301", "\u0301\u0301", "\u0303", "\ue000", "\ue001", "\uf8ff", ".", "/"]
PIECES += ["~", "@", "+", ":", "é", "e", "\u00ad", "\u200b", "\U0001d400", "x", "N\u0303"]


def passage(text: str) -> Span:
    end = 1 + text.count("\n")
    return Span(id="sp", source_id="n", kind="paragraph", heading_path=(), start_line=1,
                end_line=end, text=text)  # fmt: skip


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


def named(texts: list[str], title: str) -> list[list[tuple[int, int]]]:
    with closing(sqlite3.connect(":memory:")) as con:
        con.execute(_CLAIM_INDEX.format(index="t"))
        con.executemany("INSERT INTO t (rowid, text) VALUES (?, ?)", list(enumerate(texts, 1)))
        marked = dict(con.execute(
            "SELECT rowid, highlight(t, 5, ?, ?) FROM t WHERE t MATCH ?",
            (OPEN, CLOSE, quoted(title)),
        ).fetchall())  # fmt: skip
    return [places(marked.get(row, "")) for row in range(1, len(texts) + 1)]


def joined(stretches) -> list[tuple[int, int]]:
    """The stretches, those that overlap or touch made one."""
    out: list[tuple[int, int]] = []
    for start, end in sorted(stretches):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def apart(start: int, end: int, stretches) -> bool:
    return all(end <= s or e <= start for s, e in stretches)


def generated(seed: int, count: int) -> list[str]:
    rng, texts = random.Random(seed), []
    while len(texts) < count:
        text = "".join(rng.choice(PIECES) for _ in range(rng.randint(3, 14)))
        texts.append(text if rng.random() < 0.5 else unicodedata.normalize("NFD", text))
    return texts


def masked(text: str, stretches, title: str) -> str:
    asked = quoted(title)
    kind = relating._kinds(text + title + relations.fill_for(asked))
    return relations.blanked(text, stretches, asked, kind)


def test_blanking_moves_no_word_and_every_word_it_touches_is_another():
    """For the stretches the product blanks (the facts' and the page's code) and for
    stretches drawn anywhere: every word of the text stands where it stood; one with no
    character in a stretch is the same word, one with a character in a stretch is another,
    of the blanking character; and the only other word is one blanking character in a
    stretch that held no word, so that no phrase is read across it."""
    rng, cases = random.Random(7), []
    for text in generated(20261121, 500):
        own = [*relations.covered(passage(text)), *code_read(text)]
        drawn = []
        for _ in range(rng.randint(1, 3)):
            start = rng.randrange(len(text))
            drawn.append((start, rng.randint(start + 1, len(text))))
        for stretches in (own, drawn):
            cases.append((text, stretches, masked(text, stretches, TITLES[0])))
    before = words([text for text, _, _ in cases])
    after = words([blanked for _, _, blanked in cases])
    fill = relations.fill_for(quoted(TITLES[0]))
    added = 0
    for (text, stretches, blanked), was, now in zip(cases, before, after, strict=True):
        assert len(blanked) == len(text)
        now_at = {(start, end): word for start, end, word in now}
        for start, end, word in was:
            assert (start, end) in now_at, (text, stretches, blanked)
            if apart(start, end, stretches):
                assert now_at.pop((start, end)) == word, (text, stretches)
            else:
                assert fill in now_at.pop((start, end)), (text, stretches)
        for (start, end), word in now_at.items():  # a word given to a stretch that held none
            assert (word, end - start) == (fill, 1), (text, stretches, blanked)
            (held,) = [(s, e) for s, e in joined(stretches) if s <= start < e]
            assert all(apart(s, e, [held]) for s, e, _ in was), (text, stretches)
            added += 1
    assert len(cases) == 1000 and added > 0


def test_a_title_is_found_in_what_is_blanked_where_it_stands_whole_outside_every_stretch():
    """Where the index finds a title in the text blanked is where it finds it in the text,
    less every place that holds a character of a stretch or has one inside it: exactly the
    places that stand whole outside them."""
    texts = generated(20261122, 600)
    for title in TITLES:
        cases = []
        for text in texts:
            stretches = [*relations.covered(passage(text)), *code_read(text)]
            cases.append((text, stretches, masked(text, stretches, title)))
        found = named([text for text, _, _ in cases], title)
        kept = named([blanked for _, _, blanked in cases], title)
        hits = 0
        for (text, stretches, _), there, here in zip(cases, found, kept, strict=True):
            assert here == [p for p in there if apart(*p, stretches)], (title, text, stretches)
            hits += bool(here)
        assert hits >= 50, (title, hits)


def test_the_kinds_of_characters_are_the_index_own(monkeypatch):
    """A letter, a digit, a character of private use begin a word wherever they stand; a
    combining mark continues the word it follows and begins none; a slash, a blank, an
    apostrophe, a hyphen and an underscore separate. Asked of an index of the claims'
    declaration, once a process: characters already read are not asked again."""
    kind = relating._kinds("aZ7日\U0001d400\ue000\u0301\u0303/ '-_")
    assert [kind(c) for c in "aZ7日\U0001d400\ue000"] == [relations.TOKEN] * 6
    assert [kind(c) for c in "\u0301\u0303"] == [relations.MARK] * 2
    assert [kind(c) for c in "/ '-_"] == [relations.SEPARATOR] * 5
    opened = []
    made = relating._classifying
    monkeypatch.setattr(relating, "_classifying", lambda: opened.append(made()) or opened[-1])
    relating._kinds("aZ7/ ")
    assert opened == []


def test_blanking_reads_each_character_a_bounded_number_of_times():
    """However many combining marks a stretch holds, or the text between stretches, each
    character is read a bounded number of times: a stretch of marks alone between two
    backticks, which holds no word, and a stretch of one mark at every other character of
    marks after a letter."""
    asked = quoted(TITLES[0])
    for n in (1_000, 4_000):
        for text, stretches in (
            ("Quill `" + "\u0301" * n + "`", [(7, 7 + n)]),
            ("x" + "\u0301" * n, [(at, at + 1) for at in range(2, n, 2)]),
        ):
            kind = relating._kinds(text + asked + relations.fill_for(asked))
            read: list[str] = []

            def counted(char: str, read=read, kind=kind) -> int:
                read.append(char)
                return kind(char)

            relations.blanked(text, stretches, asked, counted)
            assert len(read) <= 4 * len(text), (n, len(read))


def test_any_characters_are_read_however_many_a_text_holds_of_those_kept_for_a_program(
    monkeypatch,
):
    """The characters Unicode keeps for a program's own use, all at once (a title can hold
    some, a passage the rest), beside the probe words' own letters: each is read as an
    index of the claims' declaration counts its words (fts5vocab), alone (a word, or none)
    and between two letters (one word, or two)."""
    monkeypatch.setattr(relating, "_KINDS", {})
    kept = [chr(code) for code in range(0xFDD0, 0xFDF0)] + ["\ufffe", "\uffff", "\U0010ffff"]
    kept += [*"qzxy", "\u0301", "/"]
    kind = relating._kinds(kept)
    with closing(sqlite3.connect(":memory:")) as con:
        con.execute(_CLAIM_INDEX.format(index="t"))
        given = [*kept, *("a" + char + "b" for char in kept)]
        con.executemany("INSERT INTO t (rowid, text) VALUES (?, ?)", list(enumerate(given, 1)))
        con.execute("CREATE VIRTUAL TABLE v USING fts5vocab (t, instance)")
        words = dict(con.execute("SELECT doc, count(*) FROM v GROUP BY doc").fetchall())
    reading = {(1, 1): relations.TOKEN, (0, 1): relations.MARK, (0, 2): relations.SEPARATOR}
    for n, char in enumerate(kept, 1):
        said = (words.get(n, 0), words.get(len(kept) + n, 0))
        assert kind(char) == reading[said], (hex(ord(char)), said)
    assert [kind(char) for char in kept[-6:]] == [relations.TOKEN] * 4 + [
        relations.MARK,
        relations.SEPARATOR,
    ]


def test_the_first_place_ends_after_as_many_words_as_the_title_holds():
    """Where the index's marks around overlapping matches begin, the first place ends after
    the title's number of words (relations.words_end): the end of that many of the words
    fts5vocab and highlight report, whatever overlaps it, with marks, NFD and CJK about."""
    rng, texts = random.Random(5), []
    pieces = ["Go", "go", "Go\u0301", " ", "\n", "-", "`", "日本", "\u0301", "x", "Go Go"]
    while len(texts) < 800:
        texts.append("".join(rng.choice(pieces) for _ in range(rng.randint(2, 12))))
    title = "Go Go"
    found = named(texts, title)
    checked = 0
    for text, there, said in zip(texts, found, words(texts), strict=True):
        if not there:
            continue
        kind = relating._kinds(text + title)
        start = there[0][0]
        (first,) = [n for n, (s, _, _) in enumerate(said) if s == start]
        assert relations.words_in(title, kind) == 2
        assert relations.words_end(text, start, 2, kind) == said[first + 1][1], text
        checked += 1
    assert checked >= 120, checked
