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
from cairn.store.hosted import Sealer

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


def test_removed_slack_secrets_and_links_stay_in_the_log_while_a_reader_holds_it(tmp_path):
    guide = flat(PRIVACY)
    assert (
        "Where this install keeps a continuous backup, the deleted secrets, still sealed, and"
        " links can stay in the database's log file until later writes reach them or the"
        " operator compacts with the replica stopped, and in backup copies until they expire."
    ) in guide
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        store.slack_sealer = Sealer(bytes(range(32)))
        ada = store.sign_in(
            "sso", "sub-ada", "ada@example.test", "Ada", issuer="https://sso.example.test",
            join="personal",
        )  # fmt: skip
        owner = store.for_account(ada.account_id, ada.user.id)
        install = owner.make_slack_install()
        owner.ready_slack_install(
            install.id,
            signing_secret="8f" + "e1c0ffee" * 3,
            bot_token="xoxb-" + "1111-2222-" + "FableBotToken0123",
            team_id="T0FABLE01",
            team_domain="fable-works",
            bot_user_id="U0FABLEBOT",
        )
        owner.link_slack_user(install.id, "U0CANARYWAL01", ada.user.id)
        sealed = store._con.execute(
            "SELECT signing_secret FROM slack_installs WHERE id = ?", (install.id,)
        ).fetchone()[0]
        assert isinstance(sealed, str) and len(sealed) >= 28
        sealed_bytes = sealed.encode("ascii")
        store._con.execute("PRAGMA busy_timeout = 0")
        reader = sqlite3.connect(db)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM slack_installs").fetchone()
            owner.remove_slack_install(install.id)
            assert owner.own_slack_links() == []
            wal = Path(f"{db}-wal").read_bytes()
            assert b"U0CANARYWAL01" in wal and sealed_bytes in wal
        finally:
            reader.close()
        store.compact()
        assert b"U0CANARYWAL01" not in db.read_bytes()
        assert sealed_bytes not in db.read_bytes()
        wal_after = Path(f"{db}-wal")
        if wal_after.exists():
            after = wal_after.read_bytes()
            assert b"U0CANARYWAL01" not in after and sealed_bytes not in after
    finally:
        store.close()


def _slack_install(store):
    """A store with one Slack installation set up and one person linked; (the owner's view,
    the installation's id, the sealed signing secret and bot token as the file holds them)."""
    store.slack_sealer = Sealer(bytes(range(32)))
    ada = store.sign_in(
        "sso", "sub-ada", "ada@example.test", "Ada", issuer="https://sso.example.test",
        join="personal",
    )  # fmt: skip
    owner = store.for_account(ada.account_id, ada.user.id)
    install = owner.make_slack_install()
    owner.ready_slack_install(
        install.id,
        signing_secret="8f" + "e1c0ffee" * 3,
        bot_token="xoxb-" + "1111-2222-" + "FableBotToken0123",
        team_id="T0FABLE01",
        team_domain="fable-works",
        bot_user_id="U0FABLEBOT",
    )
    owner.link_slack_user(install.id, "U0CANARYWAL01", ada.user.id)
    signing, token = store._con.execute(
        "SELECT signing_secret, bot_token FROM slack_installs WHERE id = ?", (install.id,)
    ).fetchone()
    return owner, install.id, signing.encode("ascii"), token.encode("ascii")


def _log(db) -> bytes:
    wal = Path(f"{db}-wal")
    return wal.read_bytes() if wal.exists() else b""


def test_removing_slack_empties_the_log_as_any_deletion_does_and_marks_a_compaction_due(tmp_path):
    """With no other connection on the file, what Slack's removal deleted is not left in the
    write-ahead log (it was: removal did not truncate the log as forgetting a note does), and
    the deletion is recorded, so `compact --if-due` finishes it where a replica holds the
    log."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        owner, install_id, signing, token = _slack_install(store)
        assert store.compaction_state().deleted_at is None
        assert signing in _log(db) and b"U0CANARYWAL01" in _log(db)  # still in the log: set-up
        owner.remove_slack_install(install_id)
        log = _log(db)
        held = [
            name
            for name, what in (
                ("signing secret", signing),
                ("bot token", token),
                ("link", b"U0CANARYWAL01"),
            )
            if what in log
        ]
        assert held == []
        assert store.compaction_state().deleted_at is not None
    finally:
        store.close()


def test_a_revoked_bot_token_is_not_left_in_the_log(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        owner, install_id, _, token = _slack_install(store)
        owner.revoke_slack_bot(install_id)
        assert (token in _log(db)) is False
        assert store.compaction_state().deleted_at is not None
    finally:
        store.close()
