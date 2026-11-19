"""What the pages and the privacy guide promise about erased text is what holds beside a
continuous backup too. cairn truncates the write-ahead log after a deletion; beside a replica
process that reads that log for good (the shipped backup), the truncation does not happen, and
the text erased from the database stays in the log file until later writes reach it or the
operator compacts with the replica stopped (tests/unit/test_compaction_unit.py shows it). So
no page says "at once" of the file without saying this."""

import sqlite3
from pathlib import Path

import pytest

from cairn.store import Store

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "cairn" / "templates"
PRIVACY = (ROOT / "src" / "cairn" / "guides" / "privacy.md").read_text(encoding="utf-8")
BESIDE_A_BACKUP = (
    "Where this install keeps a continuous backup, the old text can stay in the database's log"
    " file until the operator compacts it, and in backup copies until they expire."
)


def flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize(
    ("page", "lead"),
    [
        ("forget.html", "What forgetting removes is overwritten in the database file."),
        ("project_delete.html", "What it deletes is overwritten in the database file."),
        ("account.html", "What it deletes is overwritten in the database file."),
    ],
)
def test_a_page_that_erases_says_what_can_stay_beside_a_backup(page, lead):
    text = flat((TEMPLATES / page).read_text(encoding="utf-8"))
    assert f"{lead} {BESIDE_A_BACKUP}" in text
    assert "overwritten in the database file at once" not in text


def test_the_privacy_guide_says_the_shipped_backup_is_the_usual_case():
    guide = flat(PRIVACY)
    assert (
        "With the shipped continuous backup this is the usual case: its replica process reads"
        " that log for good, so what was deleted can stay in the log file on the server's disk"
        " until later writes reach it, or until the operator compacts with the replica stopped."
    ) in guide
    assert "overwritten in the database file at once, not only marked as free" not in guide
    assert "In both cases the text is overwritten in the database file at once" not in guide
    assert guide.count("the write-ahead log, below, is the exception") == 2


def test_the_text_of_a_forgotten_note_stays_in_the_log_while_a_reader_holds_it(tmp_path):
    """The fact the sentences state, on a real file: a reader that holds the log, as a
    replica process does, and the erased text is still in `cairn.db-wal` afterwards."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        project = store.create_project("ops")
        note, _ = store.add_note(project.id, "# Heron\n\nCANARY-heron-restarts-the-queue.\n")
        store._con.execute("PRAGMA busy_timeout = 0")
        reader = sqlite3.connect(db)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM notes").fetchone()
            store.forget_note(note.id)
            assert store.get_note(note.id) is None
            assert b"CANARY-heron-restarts-the-queue" in Path(f"{db}-wal").read_bytes()
        finally:
            reader.close()
    finally:
        store.close()
