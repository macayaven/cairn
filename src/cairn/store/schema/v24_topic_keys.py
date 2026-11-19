"""Schema step 24: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import re
import sqlite3
import unicodedata

from cairn.store.schema.v02_accounts import _V2_NOTES_BY_TEXT
from cairn.store.schema.v18_heading_charge import _V18_PASSAGES

# N1: a note keeps the topic key it was stored with (cairn.topics), made once from its topic,
# else its title, as given, never again from a cut or shown form. `before_topics` is 1 for
# every note step 24 found, the notes stored before notes were filed by topic, and 0 for each
# note stored since: sent again (the same project, text and source_ref), a note marked so is the
# same note whatever topic or title it comes with, as it was before (Store.add_note).
_V24_NOTES = """CREATE TABLE notes (
    seq INTEGER PRIMARY KEY,  -- insertion order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    title TEXT NOT NULL,
    topic TEXT,
    label TEXT,
    source_ref TEXT,
    via TEXT,
    created_at TEXT NOT NULL,
    topic_key TEXT NOT NULL,  -- the topic it is filed under (cairn.topics.topic_key)
    before_topics INTEGER NOT NULL DEFAULT 0 CHECK (before_topics IN (0, 1))  -- 1: stored
        -- before N1 (schema step 24): sent again, it is the same note whatever its topic
)"""
# Topics (N1). Each page's topic key, fixed when the page is made: a note goes to the page of
# its key (cairn.compiler), and no two pages of a project have one key. A page whose title's
# key another page held when it got its row has none, and no note goes to it. Rows go with
# their page (forget_note removes a page left with no claim), their project and their
# workspace; an export carries each page's key and an import restores it.
_V24_PAGE_KEYS = """CREATE TABLE page_keys (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    PRIMARY KEY (project_id, slug),
    UNIQUE (project_id, topic_key)
) WITHOUT ROWID"""
# The notes stored and not yet on their page (N1): a row is written with the note and
# removed with its outcome (its page saved, or note.unchanged or note.rejected recorded), in
# the same transaction. `serve` files them (cairn.web.filing), counting in `tries` each try
# that ended with no outcome. Rows go with their note, project and workspace; never exported.
_V24_WAITING = """CREATE TABLE waiting (
    note_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID"""


# The topic key as N1 defined it (cairn.topics.topic_key on 2026-11-14), copied here so that
# what _add_topic_keys did never changes when the rule does: a step is history.
_V24_SEPARATORS = re.compile(r"[\s\-_/]+")
_V24_EDGE = ".,;:!?\"'()[]{}\u2026\u2013\u2014"
_V24_FOLDED = ("LATIN ", "GREEK ")


def _v24_topic_key(title: str) -> str:
    """N1's topic key, as it was when _add_topic_keys was written."""
    text = unicodedata.normalize("NFD", unicodedata.normalize("NFD", title).casefold())
    kept: list[str] = []
    folds = False
    for char in text:
        if unicodedata.combining(char):
            if folds:
                continue
        else:
            folds = unicodedata.name(char, "").startswith(_V24_FOLDED)
        kept.append(char)
    words = (word.strip(_V24_EDGE) for word in _V24_SEPARATORS.split("".join(kept)))
    return unicodedata.normalize("NFC", " ".join(word for word in words if word))


_V24_NOTE_COLUMNS = (
    "seq, id, project_id, text, text_sha256, title, topic, label, source_ref, via, created_at"
)
_V24_SPAN_TRIGGERS = ("passage_added", "passage_removed")  # the triggers that name notes


def _add_topic_keys(con: sqlite3.Connection) -> None:
    """Schema _SOURCE_ITEMS to _TOPIC_KEYS (N1): each page and each note gets a topic key,
    and the notes no page cites wait for their page.

    Pages: each page keeps its slug and takes the key of its latest version's title, by the
    rule as it was when this step was written (_v24_topic_key). A page that merged topics
    before (C++ and C# notes filed on `c`) stays merged under the key of its latest title.
    When two pages' latest titles have one key (only pages saved outside the compiler can),
    the first by slug takes it and the other has none.

    Notes: a note filed on a page (a span of it cited by the page's latest version) takes
    that page's key, so it is the same note on the page it is on, whatever its own title
    says; any other note takes the key of its topic, else its title (stored cut to 80
    characters before this step). Every note this step finds is marked `before_topics`:
    sent again, it is the same note whatever topic or title it comes with, as it was before
    this step, so a resend that was answered "unchanged" still is. Notes are rebuilt (as
    in _add_vouched_email) so that a migrated schema reads exactly like a fresh one, every
    row kept in its order; the two triggers on spans that name notes are dropped first
    and made again after, as they were.

    Waiting: every note that no page's latest version cites waits for its page, so that
    `serve` files it (cairn.web.filing): a note stored and never compiled gets its page; one
    the gate rejected, or refused for a full page, is compiled once more, and its outcome,
    recorded, ends its wait. A note cited only by a page's older versions waits too: it is
    on no page today."""
    con.create_function("cairn_v24_topic_key", 1, _v24_topic_key, deterministic=True)
    con.execute(_V24_PAGE_KEYS)
    con.execute(
        "INSERT OR IGNORE INTO page_keys (project_id, slug, topic_key)"
        " SELECT project_id, slug, cairn_v24_topic_key(title) FROM page_versions AS p"
        " WHERE version = (SELECT MAX(version) FROM page_versions"
        "  WHERE project_id = p.project_id AND slug = p.slug)"
        " ORDER BY project_id, slug"
    )
    con.execute(
        "CREATE TABLE filed_v24 (note_id TEXT PRIMARY KEY, topic_key TEXT NOT NULL) WITHOUT ROWID"
    )
    con.execute(
        "INSERT OR IGNORE INTO filed_v24 (note_id, topic_key)"
        " SELECT spans.note_id, page_keys.topic_key FROM page_versions AS p"
        " JOIN page_keys ON page_keys.project_id = p.project_id AND page_keys.slug = p.slug,"
        " json_each(p.claims) AS claim, json_each(claim.value, '$.span_ids') AS cited"
        " JOIN spans ON spans.id = cited.value"
        " WHERE p.version = (SELECT MAX(version) FROM page_versions"
        "  WHERE project_id = p.project_id AND slug = p.slug)"
    )
    for trigger in _V24_SPAN_TRIGGERS:
        con.execute(f"DROP TRIGGER {trigger}")
    con.execute("ALTER TABLE notes RENAME TO notes_v23")
    con.execute(_V24_NOTES)
    columns = _V24_NOTE_COLUMNS
    con.execute(
        f"INSERT INTO notes ({columns}, topic_key, before_topics)"
        f" SELECT {', '.join(f'c.{name.strip()}' for name in columns.split(','))},"
        " COALESCE(filed_v24.topic_key, cairn_v24_topic_key(COALESCE(NULLIF(c.topic, ''),"
        " c.title))), 1 FROM notes_v23 AS c"
        " LEFT JOIN filed_v24 ON filed_v24.note_id = c.id ORDER BY c.seq"
    )
    con.execute("DROP TABLE notes_v23")  # and with it its index
    con.execute("DROP TABLE filed_v24")
    con.execute(_V2_NOTES_BY_TEXT)
    for statement in _V18_PASSAGES:
        con.execute(statement)
    con.execute(_V24_WAITING)
    con.execute(
        "INSERT INTO waiting (note_id, project_id) SELECT id, project_id FROM notes"
        " WHERE id NOT IN (SELECT spans.note_id FROM page_versions AS p,"
        " json_each(p.claims) AS claim, json_each(claim.value, '$.span_ids') AS cited"
        " JOIN spans ON spans.id = cited.value"
        " WHERE p.version = (SELECT MAX(version) FROM page_versions"
        "  WHERE project_id = p.project_id AND slug = p.slug))"
    )
