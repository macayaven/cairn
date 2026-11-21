"""The index is given a passage's prose alone, each run of it between two stretches that are
no prose a row of its own (relations.prose_runs, with the index's own reading of each
character, cairn.store.relating._kinds), so no phrase is read across a stretch and every word
of a run is the word it was. Checked against FTS5 alone: the words of a text and the
characters each stands on are what an index of the claims' declaration reports (fts5vocab,
instance, and highlight), never a reading of the characters written here. Seeded, made-up
texts: facts flush before and after a title's words with no blank between, in NFC and NFD,
with combining marks, digits, CJK, an apostrophe, a hyphen, an underscore, and characters of
private use beside the fill."""

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
PIECES += ["/notes", "/srv/x", "--draft", "-v", "/_", "/_\u0301"]
PIECES += ["v1.2.3", "1.2.3", "https://e.x/a", "`", "``", "`x`", "'", "-", "_", "日本", "語"]
PIECES += ["42", "\u0301", "\u0301\u0301", "\u0303", "\ue000", "\ue001", "\uf8ff", ".", "/"]
PIECES += ["~", "@", "+", ":", "é", "e", "\u00ad", "\u200b", "\U0001d400", "x", "N\u0303"]


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
    for start, end in sorted(s for s in stretches if s[0] < s[1]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


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


def generated(seed: int, count: int) -> list[str]:
    rng, texts = random.Random(seed), []
    while len(texts) < count:
        text = "".join(rng.choice(PIECES) for _ in range(rng.randint(3, 14)))
        texts.append(text if rng.random() < 0.5 else unicodedata.normalize("NFD", text))
    return texts


def runs(text: str, stretches, asked: str) -> list[tuple[int, str]]:
    kind = relating._kinds(text + asked + relations.fill_for(asked))
    return relations.prose_runs(text, stretches, asked, kind)


def test_every_word_of_a_run_is_the_word_it_was_and_no_run_holds_both_sides_of_a_stretch():
    """For the stretches the product reads (the facts' and the page's code) and for
    stretches drawn anywhere: each run is the text between two stretches, in order, and
    none is empty; every word of it is the word of the text that stands at the same place,
    the fill aside (asked here so that no text holds it, beside the characters of private
    use the texts do hold); a word that holds the fill holds it at the run's edge, once
    for each edge it stands at, where the text's word there goes on into a stretch; and
    every word of the text that lies apart from every stretch is a word of a run, where it
    stood."""
    asked = quoted(TITLES[1]) + "\ue000\ue001"
    fill = relations.fill_for(asked)
    rng, cases = random.Random(7), []
    for text in generated(20261121, 500):
        assert fill not in text
        drawn = []
        for _ in range(rng.randint(1, 3)):
            start = rng.randrange(len(text))
            drawn.append((start, rng.randint(start + 1, len(text))))
        for stretches in (stretches_of(text), drawn):
            cases.append((text, stretches, runs(text, stretches, asked)))
    before = words([text for text, _, _ in cases])
    after = iter(words([run for _, _, found in cases for _, run in found]))
    filled = 0
    for (text, stretches, found), was in zip(cases, before, strict=True):
        was_at = {(start, end): word for start, end, word in was}
        kept = {(s, e) for s, e, _ in was if apart(s, e, stretches)}
        edges = [0, *(edge for stretch in joined(stretches) for edge in stretch), len(text)]
        gaps = [(a, b) for a, b in zip(edges[::2], edges[1::2], strict=True) if a < b]
        own = []
        for at, run in found:
            left, right = run[0] == fill, run[-1] == fill
            start, end = at + left, at + len(run) - right
            assert run[left : len(run) - right] == text[start:end], (text, stretches, run)
            own.append((start, end))
            for start, end, word in next(after):
                place = (at + start, at + end)
                if fill in word:
                    filled += 1
                    edges = (start == 0 and left) + (end == len(run) and right)
                    assert word.count(fill) == edges, (text, run)
                    assert any(
                        s < place[1] and place[0] < e and not apart(s, e, stretches)
                        for s, e in was_at
                    ), (text, stretches, run)
                else:
                    assert was_at.get(place) == word, (text, stretches, run, word)
                    kept.discard(place)
        assert own == gaps, (text, stretches, found)
        assert not kept, (text, stretches, found, kept)
    assert len(cases) == 1000 and filled > 50, filled


def test_a_title_is_found_in_the_runs_where_it_stands_whole_outside_every_stretch():
    """Where the index finds a title in a text's runs, each place in a run moved by where
    the run starts, is where the title stands whole in prose: its words side by side as the
    index reads the text, no character of a stretch from the first to the last."""
    texts = generated(20261122, 600)
    said = words(texts)
    for title in TITLES:
        cases = [(text, stretches_of(text)) for text in texts]
        given = [
            (n, at, run)
            for n, (text, s) in enumerate(cases)
            for at, run in runs(text, s, quoted(title))
        ]
        found = named([run for _, _, run in given], title)
        here: dict[int, list[tuple[int, int]]] = {}
        for (n, at, _), there in zip(given, found, strict=True):
            here.setdefault(n, []).extend((at + s, at + e) for s, e in there)
        hits = 0
        for n, (text, stretches) in enumerate(cases):
            expected = standing(said[n], title, stretches)
            assert sorted(here.get(n, [])) == expected, (title, text, stretches)
            hits += bool(expected)
        assert hits >= 50, (title, hits)


# The class the reviewer's case is one of (`/_`, a path that holds no word): each kind of
# stretch flush between two words of a title, and flush before and after a whole one, with
# each kind of character on its sides. Where the facts read no stretch the text is prose.
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


def test_a_title_flush_against_every_kind_of_stretch_is_found_where_it_stands_whole():
    """The product's own path (relating._first_places: the runs, the index in memory, the
    first place and its end) against the reading here, for every case of the class, in NFC
    and NFD: the first place where the title stands whole in prose, or none."""
    cases = flush_class() + [
        (unicodedata.normalize("NFD", t), unicodedata.normalize("NFD", x)) for t, x in flush_class()
    ]
    by_title: dict[str, list[str]] = {}
    for title, text in cases:
        by_title.setdefault(title, []).append(text)
    said = dict(zip([text for _, text in cases], words([text for _, text in cases]), strict=False))
    verdicts = {True: 0, False: 0}
    for title, texts in by_title.items():
        given = {n: (text, stretches_of(text)) for n, text in enumerate(texts, 1)}
        found = relating._first_places(quoted(title), title, given)
        for n, (text, stretches) in given.items():
            expected = standing(said[text], title, stretches)[:1]
            assert ([found[n]] if n in found else []) == expected, (title, text, stretches)
            verdicts[bool(expected)] += 1
    assert len(cases) == 2 * (8 * 49 * 3 + 8 * 7 * 2), len(cases)
    assert verdicts[True] >= 300 and verdicts[False] >= 2000, verdicts
    reviewers = ("Cafe\u0301 Beta", "Read Cafe\u0301/_\u0301Beta today.")  # the reviewer's shape
    assert reviewers in cases and stretches_of(reviewers[1]) == [(10, 12)]


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


def test_a_read_asks_the_tokenizer_three_statements_once_and_none_after(tmp_path, monkeypatch):
    """A read of related pages that judges where a title stands asks the tokenizer how it
    reads the characters of the passages it judges and of the title, in a database of its
    own in memory: made, given the characters, asked. A second read of the same asks none."""
    from test_relating_cost_unit import mentioned

    store, project_id = mentioned(tmp_path, "Then she opens the Quill ledger `at dawn`.")
    monkeypatch.setattr(relating, "_KINDS", {})
    asked, made = [], relating._classifying

    def classifying() -> sqlite3.Connection:
        con = made()
        con.set_trace_callback(
            lambda said: None if said.lstrip().startswith("--") else asked.append(said)
        )
        return con

    monkeypatch.setattr(relating, "_classifying", classifying)
    for statements in (["CREATE", "INSERT", "SELECT"], []):
        asked.clear()
        found = store.related_pages(project_id, "quill-ledger", relations.RELATED_MAX)
        assert [reason.kind for reason in found.pages[0].why] == [relations.MENTIONED_BY]
        assert [said.split()[0] for said in asked] == statements
    store.close()


def test_the_runs_read_each_character_once():
    """However many combining marks a stretch holds, or the text between stretches, each
    character is read once, and the fill once: a stretch of marks alone between two
    backticks, which holds no word, and a stretch of one mark at every other character of
    marks after a letter, a run of one character between each two."""
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

            relations.prose_runs(text, stretches, asked, counted)
            assert len(read) == len(text) + 1, (n, len(read))


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
