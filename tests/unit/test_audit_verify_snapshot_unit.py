"""A healthy audit trail verifies while the server writes (second review, should-fix 12).
`audit-verify` reads the value of the last event pruned and then the events; a prune
another connection commits between those two reads must not change the verdict. The check
reads in one transaction from before the workspaces are listed until the last event is
read, so it sees the snapshot it began with. The other connection writes at that exact point
through a wrapped connection, not by timing. Made-up data; the clock is injected."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from cairn.store import DEFAULT_ACCOUNT_ID, Store
from cairn.store.audit import check_trail
from cairn.web import cli, cli_audit_verify
from cairn.web.cli_stats import _connect


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def server(tmp_path):
    """The serving store, keeping four events a workspace, its trail already pruned."""
    s = Store(tmp_path / "cairn.db", clock=Clock())
    s.audit_keep = 4
    ops = s.create_project("ops")
    for n in range(10):
        s.record_note(ops.id, "unchanged", {"page": f"p{n}"})
    yield s
    s.close()


class Interleaved:
    """A connection whose first read of the events lets the server write first: the server
    records events (each one prunes the oldest) and commits, between the verifier's read of
    the pruned value and its read of the events."""

    def __init__(self, con: sqlite3.Connection, write) -> None:
        self._con, self._write, self.wrote = con, write, False

    def execute(self, sql: str, *args):
        if not self.wrote and sql.lstrip().startswith("SELECT seq, n, at"):
            self.wrote = True
            self._write()
        return self._con.execute(sql, *args)

    def __getattr__(self, name: str):
        return getattr(self._con, name)


def more_events(server: Store):
    ops = server.list_projects()[0]
    return lambda: [server.record_note(ops.id, "unchanged", {"page": f"q{n}"}) for n in range(3)]


def test_a_prune_between_the_two_reads_does_not_break_a_healthy_chain(server, tmp_path):
    with closing(_connect(tmp_path / "cairn.db")) as before:
        ((kept, first),) = before.execute(
            "SELECT COUNT(*), MIN(n) FROM audit_events WHERE account_id = ?", (DEFAULT_ACCOUNT_ID,)
        ).fetchall()
    con = Interleaved(_connect(tmp_path / "cairn.db"), more_events(server))
    try:
        (found,) = check_trail(con, DEFAULT_ACCOUNT_ID)
    finally:
        con.close()
    assert con.wrote
    assert found.broken is None, found
    assert found.checked == kept  # the snapshot it began with
    with closing(_connect(tmp_path / "cairn.db")) as after:
        (now_first,) = after.execute(
            "SELECT MIN(n) FROM audit_events WHERE account_id = ?", (DEFAULT_ACCOUNT_ID,)
        ).fetchone()
    assert now_first > first  # the server did prune in between


def test_every_workspace_is_read_in_the_one_snapshot(server, tmp_path):
    """The neighbour: listing the workspaces is part of the same read."""
    con = Interleaved(_connect(tmp_path / "cairn.db"), more_events(server))
    try:
        found = check_trail(con)
    finally:
        con.close()
    assert con.wrote and all(trail.broken is None for trail in found)


def test_the_command_holds_while_the_server_prunes(server, tmp_path, monkeypatch, capsys):
    db = tmp_path / "cairn.db"
    seen: list[Interleaved] = []

    def interleaved(path):
        seen.append(Interleaved(_connect(path), more_events(server)))
        return seen[-1]

    monkeypatch.setattr(cli_audit_verify, "_connect", interleaved)
    assert cli.main(["audit-verify", "--db", str(db), "--workspace", DEFAULT_ACCOUNT_ID]) == 0
    assert seen[0].wrote
    assert "the chain holds" in capsys.readouterr().out


def test_the_check_leaves_no_transaction_open(server, tmp_path):
    con = _connect(tmp_path / "cairn.db")
    try:
        check_trail(con)
        assert not con.in_transaction
    finally:
        con.close()
