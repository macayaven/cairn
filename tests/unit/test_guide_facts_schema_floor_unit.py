"""The sentences on upgrades and going back (run.md, deploy/README.md, infra/gcp/README.md) and
on the audit trail's chain (security.md), each pinned to what the code does: the sentence is in
the test, and the test shows it true over a real store, the real schema steps or the CLI."""

import sqlite3
from importlib.resources import files
from pathlib import Path

import pytest
from test_schema_floor_unit import moved_ahead
from test_store_unit import connect, rows

import cairn.store.schema as schema
from cairn.store import SCHEMA_VERSION, SchemaTooNew, Store
from cairn.store.audit import check_trail
from cairn.web import cli

REPO = Path(__file__).resolve().parents[2]


def flat(text: str) -> str:
    return " ".join(text.split())


def guide(name: str) -> str:
    return flat(files("cairn.guides").joinpath(f"{name}.md").read_text())


def readme(path: str) -> str:
    return flat((REPO / path).read_text(encoding="utf-8"))


# Going back: which upgrades a previous image undoes

RUN_KINDS = (
    "An update that only adds to the database (tables, columns with defaults, indexes) can be"
    " undone by going back to the image before it, which runs on the updated database and"
    " leaves alone what it does not know. An update that changes what an older image relies on"
    " cannot: the older image refuses the database with one line that names the schema version"
    " it needs and its own, so going back means going back to a backup taken before the"
    " upgrade. The release's notes say which kind each release is; 0.1.0 is of the second kind."
)
DEPLOY_KINDS = (
    "From schema 31 on, a database records the oldest schema version whose image runs on it."
    " An update whose schema steps only add (tables, columns with defaults, indexes) leaves"
    " that number, so the image before it still runs on the updated database: going back to it"
    " needs no restore. An update with a step that changes what an older image relies on raises"
    " the number to itself, the older image refuses the database with the line above, which"
    " names the version it needs, and going back means restoring a database from before the"
    " change. The release's notes say which kind each update is. Schema 31 (release 0.1.0) is"
    " of the second kind."
)
INFRA_KINDS = (
    "**Which upgrades a rollback undoes.** From schema 31 on, a database records the oldest"
    " schema version whose image runs on it. A newer image whose schema steps only add"
    " (tables, columns with defaults, indexes) leaves that number: the older image runs on the"
    " database it moved forward, so the updater's automatic rollback, or moving the tag back,"
    " works with nothing lost. A step that changes what an older image relies on raises the"
    " number to itself: the older image refuses the database and fails its health check, and"
    " the way back is the cold backup, below. The release's notes say which kind each update"
    " is, and the refusal names the version it needs and its own. Schema 31 (release 0.1.0) is"
    " of the second kind: no image before it runs on a database it updated."
)
INFRA_UPDATER = (
    "If the updater's rollback is not healthy either, its line in `journalctl -u cairn-update`"
    " says to look for this one."
)


def test_the_guides_say_which_upgrades_going_back_undoes():
    assert RUN_KINDS in guide("run")
    assert DEPLOY_KINDS in readme("deploy/README.md")
    assert INFRA_KINDS in readme("infra/gcp/README.md")
    assert INFRA_UPDATER in readme("infra/gcp/README.md")
    host = (REPO / "infra" / "gcp" / "files" / "host.sh").read_text(encoding="utf-8")
    assert "is not healthy either: if journalctl -u cairn shows SchemaTooNew" in host


def test_schema_31_is_of_the_second_kind_and_the_floor_is_31():
    assert SCHEMA_VERSION == schema._OLDEST_BUILD == 31
    assert schema._OLDEST_BUILD not in schema._ONLY_ADDS
    assert schema._floor_of(SCHEMA_VERSION) == 31


@pytest.mark.parametrize("only_adds", [True, False])
def test_an_older_image_runs_on_an_added_to_database_and_refuses_a_changed_one(
    tmp_path, monkeypatch, only_adds
):
    db = tmp_path / "cairn.db"
    Store(db).close()
    moved_ahead(db, monkeypatch, only_adds=only_adds)
    if only_adds:  # this build is the image before: it runs on the database
        store = Store(db)
        try:
            store.create_project("ops")
            assert store.health() == "ok"
        finally:
            store.close()
        assert rows(db, "SELECT what FROM later_things") == [("kept",)]
    else:  # it refuses, naming the version it needs and its own
        with pytest.raises(SchemaTooNew) as refused:
            Store(db)
        said = str(refused.value)
        assert f"version {SCHEMA_VERSION + 1} or later" in said
        assert f"knows up to {SCHEMA_VERSION}" in said


# The audit trail's chain

SECURITY_CHAIN = (
    "**The audit log shows when it was changed.** Each stored event carries a value that chains"
    " it to the event before it in its workspace, and the operator's command `audit-verify --db"
    " PATH` names the first event that no longer follows from the one before, by its number and"
    " time; pruning the oldest events keeps the chain checkable."
)
SECURITY_NOT_SHOWN = (
    "Someone who can write the database file can rewrite the whole chain, which is why the copy"
    " of each event on the server's output exists. An event's project and detail are outside"
    " the chain, because forgetting a note must be able to take a page's name out of them."
)


def test_security_the_chain_and_what_it_does_not_show(tmp_path, capsys):
    assert SECURITY_CHAIN in guide("security")
    assert SECURITY_NOT_SHOWN in guide("security")
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        store.audit_keep = 2  # pruning keeps the chain checkable
        ops = store.create_project("ops")
        for n in range(6):
            store.record_note(ops.id, "unchanged", {"page": f"p{n}"})
    finally:
        store.close()
    assert cli.main(["audit-verify", "--db", str(db)]) == 0
    capsys.readouterr()
    with connect(db) as con:  # project and detail are outside the chain
        con.execute("UPDATE audit_events SET project = NULL, detail = '{}'")
        con.commit()
        assert all(found.broken is None for found in check_trail(con))
    # Whoever can write the file can rewrite the whole chain: recompute every value.
    with connect(db) as con:
        con.execute("UPDATE audit_events SET action = 'note.saved'")
        previous = {}
        for seq, account, n, at, kind, actor, actor_id, action in con.execute(
            "SELECT seq, account_id, n, at, actor_kind, actor, actor_id, action"
            " FROM audit_events ORDER BY account_id, n"
        ).fetchall():
            start = previous.get(account) or _start(con, account)
            value = schema._v31_link(start, account, n, seq, at, kind, actor, actor_id, action)
            con.execute("UPDATE audit_events SET chain = ? WHERE seq = ?", (value, seq))
            previous[account] = value
        con.commit()
        assert all(found.broken is None for found in check_trail(con))


def _start(con: sqlite3.Connection, account: str) -> str:
    import json

    from cairn.store.base import AUDIT_PRUNED_ROW, AUDIT_START

    row = con.execute(
        "SELECT value FROM settings WHERE account_id = ? AND name = ?", (account, AUDIT_PRUNED_ROW)
    ).fetchone()
    return json.loads(row[0])["chain"] if row else AUDIT_START
