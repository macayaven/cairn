"""The audit trail shows when it was changed: every event carries a value that chains it to
the event before it in its workspace, a SHA-256 over that event's value and this event's fields
that never change after it is written (its workspace, number, sequence, time, who by kind,
name and id, and action); `project` and `detail` are outside it, since erasure rewrites them
on purpose. Pruning keeps the value of the last event pruned in the workspace's settings, and
verification starts from it. Step 31 fills the value for the events already stored."""

import io
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_schema_floor_unit import v30_database
from test_store_unit import connect, rows

import cairn.store.schema as schema
from cairn.store import DEFAULT_ACCOUNT_ID, Store
from cairn.store.audit import AUDIT_START, TrailCheck, audit_link, check_trail
from cairn.store.base import AUDIT_PRUNED_ROW

SOURCE = {
    "base_url": "https://langfuse.example.test",
    "public_key_env": "LF_PUBLIC",
    "secret_key_env": "LF_SECRET",
}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db, clock=Clock())
    yield s
    s.close()


def checked(db: Path, account_id: str | None = None) -> list[TrailCheck]:
    with connect(db) as con:
        return check_trail(con, account_id)


def holds(db: Path, account_id: str) -> int:
    """How many events the workspace's trail holds, asserting that its chain holds."""
    (found,) = checked(db, account_id)
    assert found.broken is None, found
    return found.checked


EVENT = "SELECT seq, account_id, n, at, actor_kind, actor, actor_id, action, chain"


def test_each_event_chains_to_the_one_before_it_in_its_workspace(store, db):
    other = store.for_account(store.create_account("Fable Works").id)
    store.acting_as("key", "laptop", "key_1").create_project("ops")
    other.create_project("lamp")
    store.acting_as("session", "Ada", "usr_1").create_project("dev")
    for account in (DEFAULT_ACCOUNT_ID, other.account_id):
        events = rows(db, f"{EVENT} FROM audit_events WHERE account_id = ? ORDER BY n", account)
        previous = AUDIT_START
        for seq, account_id, n, at, kind, actor, actor_id, action, chain in events:
            assert chain == audit_link(
                previous, account_id, n, seq, at, kind, actor, actor_id, action
            )
            previous = chain
        assert events
        assert holds(db, account) == len(events)


def test_the_value_covers_the_fields_that_never_change_and_not_project_and_detail():
    fields = (AUDIT_START, "acct_default", 3, 7, "2026-11-02T09:00:00", "key", "laptop", "k1", "a")
    base = audit_link(*fields)
    assert len(base) == 64 and int(base, 16) >= 0
    for i in range(len(fields)):
        changed = list(fields)
        changed[i] = 99 if isinstance(fields[i], int) else f"{fields[i]}x"
        assert audit_link(*changed) != base, i
    assert audit_link(*fields[:6], None, *fields[7:]) != base  # no actor id is not ""


def test_a_changed_action_is_named_by_its_number_and_time(store, db):
    ops = store.create_project("ops")
    for n in range(5):
        store.record_note(ops.id, "unchanged", {"page": f"p{n}"})
    (n, at) = rows(
        db, "SELECT n, at FROM audit_events WHERE account_id = ? ORDER BY n", DEFAULT_ACCOUNT_ID
    )[3]
    with connect(db) as con:
        con.execute(
            "UPDATE audit_events SET action = 'note.saved' WHERE n = ? AND account_id = ?",
            (n, DEFAULT_ACCOUNT_ID),
        )
        con.commit()
    (found,) = checked(db, DEFAULT_ACCOUNT_ID)
    assert found.broken == (n, at)


def test_an_event_deleted_from_the_middle_names_the_next(store, db):
    ops = store.create_project("ops")
    for n in range(5):
        store.record_note(ops.id, "unchanged", {"page": f"p{n}"})
    numbered = rows(
        db, "SELECT n, at FROM audit_events WHERE account_id = ? ORDER BY n", DEFAULT_ACCOUNT_ID
    )
    with connect(db) as con:
        con.execute(
            "DELETE FROM audit_events WHERE account_id = ? AND n = ?",
            (DEFAULT_ACCOUNT_ID, numbered[2][0]),
        )
        con.commit()
    (found,) = checked(db, DEFAULT_ACCOUNT_ID)
    assert found.broken == numbered[3]


def test_the_oldest_event_deleted_by_hand_names_the_next(store, db):
    ops = store.create_project("ops")
    store.record_note(ops.id, "unchanged", {"page": "p"})
    numbered = rows(
        db, "SELECT n, at FROM audit_events WHERE account_id = ? ORDER BY n", DEFAULT_ACCOUNT_ID
    )
    with connect(db) as con:
        con.execute("DELETE FROM audit_events WHERE n = ?", (numbered[0][0],))
        con.commit()
    (found,) = checked(db, DEFAULT_ACCOUNT_ID)
    assert found.broken == numbered[1]


def test_rewriting_project_and_detail_keeps_the_chain(store, db):
    ops = store.create_project("ops")
    store.record_note(ops.id, "unchanged", {"page": "p"})
    with connect(db) as con:
        con.execute("UPDATE audit_events SET project = 'other', detail = '{}'")
        con.commit()
    assert holds(db, DEFAULT_ACCOUNT_ID) == 2


def test_a_pruned_trail_verifies_from_the_last_event_pruned(store, db):
    store.audit_keep = 4
    ops = store.create_project("ops")
    for n in range(20):
        store.record_note(ops.id, "unchanged", {"page": f"p{n}"})
    numbers = [
        n
        for (n,) in rows(
            db, "SELECT n FROM audit_events WHERE account_id = ? ORDER BY n", DEFAULT_ACCOUNT_ID
        )
    ]
    assert numbers[0] > 1 and numbers[-1] == 21
    ((value,),) = rows(
        db,
        "SELECT value FROM settings WHERE account_id = ? AND name = ?",
        DEFAULT_ACCOUNT_ID,
        AUDIT_PRUNED_ROW,
    )
    assert f'"n":{numbers[0] - 1}' in value.replace(" ", "")
    assert holds(db, DEFAULT_ACCOUNT_ID) == len(numbers)


def test_deleting_a_workspace_takes_its_events_and_the_others_still_verify(store, db):
    gone = store.for_account(store.create_account("Fable Works").id)
    gone.audit_keep = 2
    for n in range(5):
        gone.create_project(f"p{n}")
    assert store.delete_account(gone.account_id)
    assert rows(db, "SELECT COUNT(*) FROM settings WHERE account_id = ?", gone.account_id) == [(0,)]
    assert [c.account_id for c in checked(db)] == [DEFAULT_ACCOUNT_ID]
    assert holds(db, DEFAULT_ACCOUNT_ID) == rows(db, "SELECT COUNT(*) FROM audit_events")[0][0]


def test_a_workspace_with_no_events_holds(store, db):
    made = store.create_account("Fable Works")
    with connect(db) as con:
        con.execute("DELETE FROM audit_events WHERE account_id = ?", (made.id,))
        con.commit()
    assert holds(db, made.id) == 0


def test_an_unknown_workspace_is_a_key_error(store, db):
    with connect(db) as con, pytest.raises(KeyError):
        check_trail(con, "acct_000000000000")


# Every path that writes an event goes through the one writer


def test_events_of_an_import_erasure_a_source_run_and_the_system_are_chained(store, db, tmp_path):
    ops = store.create_project("ops")
    kept = store.add_note(ops.id, "# Relay\n\nThe relay runs at night.\n")
    forgotten, _ = store.add_note(ops.id, "# Lamp\n\nThe lamp is blue.\n")
    out = io.BytesIO()
    store.export(out)
    other_db = tmp_path / "other.db"
    other = Store(other_db, clock=Clock())
    try:
        other.create_project("before")  # the import's event follows one already there
        fresh = other.for_account(other.create_account("Fable Works").id)
        fresh.import_export(io.BytesIO(out.getvalue()))
        assert "workspace.imported" in [e.action for e in fresh.list_audit()]
    finally:
        other.close()
    assert holds(other_db, fresh.account_id) >= 1  # the import's event, chained
    assert holds(other_db, DEFAULT_ACCOUNT_ID) >= 1

    store.forget_note(forgotten.id)  # rewrites project and detail of older events
    assert holds(db, DEFAULT_ACCOUNT_ID) >= 1
    store.add_source("traces", "langfuse", {**SOURCE, "projects": ["ops"]})
    claim = store.claim_source("traces")
    assert store.finish_source_run(claim, "c1", {"saved": 1})
    assert "source.synced" in [e.action for e in store.list_audit()]
    store.acting_as("system", None).create_project("sys")  # the server's own event
    assert store.list_audit(1)[0].actor_kind == "system"
    assert holds(db, DEFAULT_ACCOUNT_ID) >= 1
    assert store.delete_project(ops.id)  # rewrites project and detail of its events
    assert f"{ops.id} (deleted)" in {e.project for e in store.list_audit(100)}
    assert holds(db, DEFAULT_ACCOUNT_ID) >= 1
    assert kept is not None


# What an event costs


def test_an_event_costs_two_statements_and_a_prune_two_more(store):
    """Before the chain: one read (the workspace's last and first number) and the insert; a
    prune, one delete. With it: the same read also takes the last event's value and the
    newest sequence (scalar subqueries in one statement), and the insert carries the value;
    a prune first records the value of the last event it removes (one INSERT ... SELECT into
    settings), then deletes. So an event costs two statements as before, and a prune two."""
    ops = store.create_project("ops")
    store.audit_keep = 1_000  # read once per connection: not counted below
    seen: list[str] = []
    store._observe(trace=seen.append)
    try:
        store.record_note(ops.id, "unchanged", {"page": "p"})
    finally:
        store._observe(trace=None)
    audit = [s for s in seen if "audit_events" in s or AUDIT_PRUNED_ROW in s]
    assert len(audit) == 2, audit
    store.audit_keep = 2
    seen.clear()
    store._observe(trace=seen.append)
    try:
        store.record_note(ops.id, "unchanged", {"page": "q"})
    finally:
        store._observe(trace=None)
    audit = [s for s in seen if "audit_events" in s or "settings" in s]
    assert len(audit) == 4, audit


# Step 31 fills the value for the events already stored


def v30_events(db: Path) -> list[tuple]:
    """A database at step 30 with events in two workspaces: the default one pruned before
    the step (its numbers start at 3), the other with three events of its own and two more."""
    v30_database(db)
    events = [
        (DEFAULT_ACCOUNT_ID, 3, "2026-01-01T00:00:01", "system", None, None, "project.created"),
        ("acct_aaaaaaaaaaaa", 4, "2026-01-01T00:00:02", "key", "laptop", "k1", "note.saved"),
        (DEFAULT_ACCOUNT_ID, 4, "2026-01-01T00:00:03", "session", "Ada", "u1", "note.saved"),
        ("acct_aaaaaaaaaaaa", 5, "2026-01-01T00:00:04", "token", "agent", "g1", "page.saved"),
    ]
    with connect(db) as con:
        con.execute("DELETE FROM audit_events WHERE account_id = ?", (DEFAULT_ACCOUNT_ID,))
        con.executemany(
            "INSERT INTO audit_events (account_id, n, at, actor_kind, actor, actor_id, action,"
            " project, detail) VALUES (?, ?, ?, ?, ?, ?, ?, 'ops', '{\"page\": \"p\"}')",
            events,
        )
        con.commit()
    return rows(
        db,
        "SELECT seq, account_id, n, at, actor_kind, actor, actor_id, action, project,"
        " detail FROM audit_events ORDER BY seq",
    )


def test_step_31_chains_the_events_already_stored_in_order(db):
    before = v30_events(db)
    Store(db).close()
    after = rows(
        db,
        "SELECT seq, account_id, n, at, actor_kind, actor, actor_id, action, project,"
        " detail, chain FROM audit_events ORDER BY seq",
    )
    assert [row[:-1] for row in after] == before  # every event kept as it was
    previous: dict[str, str] = {}
    for seq, account_id, n, at, kind, actor, actor_id, action, _, _, chain in sorted(
        after, key=lambda row: (row[1], row[2])
    ):
        value = audit_link(
            previous.get(account_id, AUDIT_START),
            account_id,
            n,
            seq,
            at,
            kind,
            actor,
            actor_id,
            action,
        )
        assert chain == value
        previous[account_id] = value
    found = {c.account_id: (c.checked, c.broken) for c in checked(db)}
    assert found == {"acct_aaaaaaaaaaaa": (5, None), DEFAULT_ACCOUNT_ID: (2, None)}


def test_step_31_has_its_own_copy_of_the_value_and_it_agrees():
    fields = (AUDIT_START, "acct_default", 3, 7, "2026-11-02T09:00:00", "key", None, "k1", "a")
    assert schema._v31_link is not audit_link
    assert schema._v31_link(*fields) == audit_link(*fields)


def test_the_logger_line_carries_the_value(store, caplog):
    import json
    import logging

    with caplog.at_level(logging.INFO, logger="cairn.audit"):
        store.create_project("ops")
    (record,) = [r for r in caplog.records if r.name == "cairn.audit"]
    ((chain,),) = rows(
        Path(store.database_file()), "SELECT chain FROM audit_events ORDER BY seq DESC LIMIT 1"
    )
    assert json.loads(record.getMessage())["chain"] == chain


def test_a_newer_writer_that_did_not_chain_is_named(store, db):
    """What an event written without the value looks like: an empty value is named."""
    store.create_project("ops")
    store.create_project("dev")
    with connect(db) as con:
        con.execute(
            "UPDATE audit_events SET chain = '' WHERE n = 2 AND account_id = ?",
            (DEFAULT_ACCOUNT_ID,),
        )
        con.commit()
    (found,) = checked(db, DEFAULT_ACCOUNT_ID)
    assert found.broken is not None and found.broken[0] == 2


def test_check_trail_writes_nothing(store, db):
    store.create_project("ops")
    with connect(db) as con:
        before = list(con.iterdump())
        check_trail(con)
        assert list(con.iterdump()) == before
    assert isinstance(sqlite3.sqlite_version, str)
