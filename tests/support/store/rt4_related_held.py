"""Shared test helpers extracted from test_rt4_related_held_unit."""

import re
import sqlite3
from contextlib import closing

from cairn import relations
from cairn.facts import cased, extract_facts
from cairn.store import Store


def phrase_in(text: str, phrase: str) -> bool:
    """The index's own matching of the phrase as written (FTS5 tokenizes both)."""
    with closing(sqlite3.connect(":memory:")) as con:
        con.execute("CREATE VIRTUAL TABLE t USING fts5 (text)")
        con.execute("INSERT INTO t (text) VALUES (?)", (text,))
        quoted = '"' + phrase.replace('"', '""') + '"'
        return bool(con.execute("SELECT 1 FROM t WHERE t MATCH ?", (quoted,)).fetchone())


def places_of(text: str, phrase: str) -> list[tuple[int, int]]:
    """Where the index's own matching finds the phrase in the text, by character: FTS5's
    highlight() of the quoted phrase, in a table of its own."""
    with closing(sqlite3.connect(":memory:")) as con:
        con.execute("CREATE VIRTUAL TABLE t USING fts5 (text)")
        con.execute("INSERT INTO t (text) VALUES (?)", (text,))
        quoted = '"' + phrase.replace('"', '""') + '"'
        row = con.execute(
            "SELECT highlight(t, 0, char(1), char(2)) FROM t WHERE t MATCH ?", (quoted,)
        ).fetchone()
    found, marked, skipped = [], row[0] if row else "", 0
    for match in re.finditer("\x01([^\x02]*)\x02", marked):
        start = match.start() - skipped
        found.append((start, start + len(match.group(1))))
        skipped += 2
    return found


NO_PROSE = ("url", "path", "flag", "version", "inline_code", "code_block")


def in_prose(span, phrase: str) -> bool:
    """The decided rule, worked out here: some place where the index matches the phrase in
    the passage shares no character with a fact of NO_PROSE."""
    stretches = [(f.start, f.end) for f in extract_facts(span) if f.kind in NO_PROSE]
    return any(
        all(end <= lo or start >= hi for lo, hi in stretches)
        for start, end in places_of(span.text, phrase)
    )


def relates(kind: str, text: str) -> bool:
    if kind not in ("url", "path", "flag", "version", "inline_code") or len(text) > 200:
        return False
    if not any(c.isalnum() for c in text):
        return False
    return kind != "inline_code" or len(text) >= 12 or not text.isalpha()


def oracle(store: Store, project_id: str) -> dict[str, list[tuple]]:
    """For each page, every reason the rule gives, as (other page, kind, term, term kind,
    pages, claim id there, claim id here), worked out here claim by claim."""
    pages = {h.slug: store.get_page(project_id, h.slug) for h in store.list_pages(project_id)}
    claims = [(slug, c) for slug, page in pages.items() for c in page.claims]
    spans = store.get_spans(c.span_ids[0] for _, c in claims if c.span_ids)
    stated: dict[tuple, dict[str, str]] = {}  # term -> page -> first claim (in page order)
    holds_words: dict[tuple, int] = {}
    for slug, claim in claims:
        span = spans.get(claim.span_ids[0]) if claim.span_ids else None
        assert span is not None and span.text == claim.text  # a claim is its passage's text
        for fact in extract_facts(span):
            term = (fact.kind, cased(fact.as_read))  # decided: a term keeps its case
            if fact.end - fact.start > 200 or not relates(*term):
                continue
            if phrase_in(claim.text, term[1]):
                stated.setdefault(term, {}).setdefault(slug, claim.id)
    for term in stated:
        holds_words[term] = sum(phrase_in(c.text, term[1]) for _, c in claims)
    given: dict[str, list[tuple]] = {slug: [] for slug in pages}
    for term, by_page in stated.items():
        if len(by_page) > relations.COMMON or holds_words[term] > relations.CLAIMS_COMMON:
            continue
        for slug, here in by_page.items():
            for other, there in by_page.items():
                if other != slug:
                    given[slug].append(
                        (other, "shares", term[1], term[0], len(by_page), there, here)
                    )
    for slug, page in pages.items():
        naming: dict[str, str] = {}
        count = 0  # claims of other pages that hold the title's words, in prose or not
        for other, claim in claims:
            if other != slug and phrase_in(claim.text, page.title):
                count += 1
                if in_prose(spans[claim.span_ids[0]], page.title):  # decided: prose only
                    naming.setdefault(other, claim.id)
        if len(naming) <= relations.COMMON and count <= relations.CLAIMS_COMMON:
            for other, there in naming.items():
                given[slug].append((other, "mentioned_by", page.title, "title", 2, there, None))
    return given
