"""`python -m cairn.server audit-verify --db PATH [--workspace ID]`: for each workspace, how
many events it checked and that the chain holds, or the first event whose value does not
follow from the one before (its number and time, never its content), and exit 1. It reads the
file read-only and writes nothing."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cairn.store import DEFAULT_ACCOUNT_ID, SCHEMA_VERSION, Store
from cairn.web import cli, cli_audit_verify
from support.store.schema_floor import v30_database
from support.store.store import connect, rows, statements, user_version


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def made(tmp_path) -> tuple[Path, str]:
    """A database with events in two workspaces: the default one's five, the other's own."""
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    try:
        other = store.create_account("Fable Works").id
        store.for_account(other).create_project("lamp")
        ops = store.create_project("ops")
        for n in range(4):
            store.record_note(ops.id, "unchanged", {"page": f"secret-page-{n}"})
    finally:
        store.close()
    return db, other


def verify(capsys, db: Path, *extra: str) -> tuple[int, list[str], str]:
    code = cli.main(["audit-verify", "--db", str(db), *extra])
    out, err = capsys.readouterr()
    return code, out.splitlines(), err


def counted(db: Path, account_id: str) -> int:
    return rows(db, "SELECT COUNT(*) FROM audit_events WHERE account_id = ?", account_id)[0][0]


def test_every_chain_holds_one_line_each_and_nothing_is_written(made, capsys):
    db, other = made
    before = statements(db)
    code, lines, err = verify(capsys, db)
    assert (code, err) == (0, "")
    assert lines == [
        f"workspace {DEFAULT_ACCOUNT_ID}: {counted(db, DEFAULT_ACCOUNT_ID)} events checked;"
        " the chain holds",
        f"workspace {other}: {counted(db, other)} event"
        f"{'' if counted(db, other) == 1 else 's'} checked; the chain holds",
    ]
    assert statements(db) == before
    assert sorted(p.name for p in db.parent.iterdir()) == ["cairn.db"]


def test_a_changed_action_is_named_by_number_and_time_and_exits_1(made, capsys):
    db, _ = made
    (n, at) = rows(
        db,
        "SELECT n, at FROM audit_events WHERE account_id = ? ORDER BY n LIMIT 1 OFFSET 2",
        DEFAULT_ACCOUNT_ID,
    )[0]
    with connect(db) as con:
        con.execute(
            "UPDATE audit_events SET action = 'project.deleted' WHERE account_id = ? AND n = ?",
            (DEFAULT_ACCOUNT_ID, n),
        )
        con.commit()
    code, lines, err = verify(capsys, db)
    assert (code, err) == (1, "")
    assert lines[0] == (
        f"workspace {DEFAULT_ACCOUNT_ID}: event {n} ({at}) does not follow from the one before;"
        " 2 events before it hold"
    )
    assert lines[1].endswith("the chain holds")
    said = "\n".join(lines)
    assert "project.deleted" not in said and "secret-page" not in said and "unchanged" not in said


def test_an_event_deleted_from_the_middle_names_the_next(made, capsys):
    db, _ = made
    numbered = rows(
        db, "SELECT n, at FROM audit_events WHERE account_id = ? ORDER BY n", DEFAULT_ACCOUNT_ID
    )
    with connect(db) as con:
        con.execute(
            "DELETE FROM audit_events WHERE account_id = ? AND n = ?",
            (DEFAULT_ACCOUNT_ID, numbered[2][0]),
        )
        con.commit()
    code, lines, _ = verify(capsys, db, "--workspace", DEFAULT_ACCOUNT_ID)
    n, at = numbered[3]
    assert code == 1
    assert lines == [
        f"workspace {DEFAULT_ACCOUNT_ID}: event {n} ({at}) does not follow from the one before;"
        " 2 events before it hold"
    ]


def test_one_workspace_by_its_id(made, capsys):
    db, other = made
    code, lines, _ = verify(capsys, db, "--workspace", other)
    assert code == 0
    assert [line.split(":")[0] for line in lines] == [f"workspace {other}"]


def test_an_unknown_workspace_is_one_line_and_exit_1(made, capsys):
    db, _ = made
    code, lines, err = verify(capsys, db, "--workspace", "acct_000000000000")
    assert (code, lines) == (1, [])
    assert err == f"no workspace acct_000000000000 in the database at {db}\n"


def test_an_older_database_is_refused_and_not_upgraded(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    v30_database(db)
    before = statements(db)
    code, lines, err = verify(capsys, db)
    assert (code, lines) == (1, [])
    assert (
        err == cli_audit_verify.OLDER_DATABASE.format(db=db, found=30, known=SCHEMA_VERSION) + "\n"
    )
    assert statements(db) == before and user_version(db) == 30


def test_no_database_at_the_path(tmp_path, capsys):
    code, lines, err = verify(capsys, tmp_path / "none.db")
    assert (code, lines, err) == (1, [], f"no database at {tmp_path / 'none.db'}\n")
    assert not (tmp_path / "none.db").exists()
