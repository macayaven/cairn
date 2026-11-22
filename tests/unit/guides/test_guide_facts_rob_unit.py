"""Sentences on health, compaction and free disk (review 6.1, 6.2, 6.6) in the run guide,
deploy/README.md and infra/gcp/README.md, each held to the code that makes it true. Made-up
data; the clock and the free-space reading are injected; nothing sleeps."""

import logging
import re
import sqlite3
import threading
from collections import namedtuple
from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

import cairn.store as store_module
from cairn.store import COMPACT_STUCK_AFTER, STUCK_AFTER, Store, portable
from cairn.web.app import create_app
from cairn.web.compactor import POLL_SECONDS, Compactor
from support.paths import REPO_ROOT

REPO = REPO_ROOT
RUN = (REPO / "src" / "cairn" / "guides" / "run.md").read_text(encoding="utf-8")
DEPLOY = (REPO / "deploy" / "README.md").read_text(encoding="utf-8")
INFRA = (REPO / "infra" / "gcp" / "README.md").read_text(encoding="utf-8")
MONITORING = (REPO / "infra" / "gcp" / "monitoring.tf").read_text(encoding="utf-8")
T0 = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
Usage = namedtuple("Usage", "total used free")


def one_line(text: str) -> str:
    return " ".join(text.split())


class Ticks:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def lines_of(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("compact")]


def as_pattern(quoted: str) -> re.Pattern[str]:
    """A quoted line with N for a number."""
    return re.compile(
        "".join(r"\d+" if p == "N" else re.escape(p) for p in re.split(r"(\bN\b)", quoted))
    )


@pytest.fixture
def stuck_status(tmp_path, monkeypatch):
    """What /healthz answers with one write holding the writer: before and after the bound,
    and once the writer is obtained again."""
    monkeypatch.setattr(store_module, "HEALTH_WAIT", 0.0)
    ticks = Ticks()
    store = Store(tmp_path / "cairn.db", ticks=ticks)
    seen = []
    taken, release = threading.Event(), threading.Event()

    def hold() -> None:
        with store._lock:
            taken.set()
            release.wait()

    try:
        with TestClient(create_app(store)) as client:
            holder = threading.Thread(target=hold)
            holder.start()
            try:
                assert taken.wait(10)
                seen.append(client.get("/healthz"))
                ticks.now += STUCK_AFTER + 1
                seen.append(client.get("/healthz"))
            finally:
                release.set()
                holder.join()
            seen.append(client.get("/healthz"))
    finally:
        store.close()
    return [(r.status_code, r.json()) for r in seen]


# The health check


def test_the_deploy_guide_says_what_503_stuck_means_and_what_to_do(stuck_status):
    """Once one write has held the database for more than a minute (an hour while a
    compaction runs), it answers 503 with `{"status":"stuck"}`: ... Restart it ...: the
    write under way is lost, nothing committed is. It answers 200 again as soon as that
    write lets go."""
    said = one_line(DEPLOY)
    assert (
        "Once one write has held the database for more than a minute (an hour while a"
        ' compaction runs), it answers 503 with `{"status":"stuck"}`' in said
    )
    assert "reads may still work, but no write can be made" in said
    assert (
        "`docker compose restart cairn`: the write under way is lost, nothing committed is" in said
    )
    assert "It answers 200 again as soon as that write lets go." in said
    assert (STUCK_AFTER, COMPACT_STUCK_AFTER) == (60.0, 3600.0)
    assert stuck_status == [(200, {"status": "ok"}), (503, {"status": "stuck"}),
                            (200, {"status": "ok"})]  # fmt: skip


def test_the_hosted_guide_names_stuck_among_the_503s(stuck_status):
    said = one_line(INFRA)
    assert (
        "one write has held the database for more than a minute (an hour while a compaction"
        ' runs) and it answers `{"status":"stuck"}`' in said
    )
    assert "the write under way is lost, nothing committed is" in said
    assert stuck_status[1] == (503, {"status": "stuck"})


def test_the_run_guide_says_what_503_means(stuck_status):
    said = one_line(RUN)
    assert (
        '`/healthz` answers 503 with `{"status":"stuck"}` once one write has held the'
        " database for more than a minute (an hour while a compaction runs)" in said
    )
    assert "Restart the server: the write under way is lost, nothing committed is." in said
    assert stuck_status[1] == (503, {"status": "stuck"})


# Compaction: room, and the lines an operator sees


def test_the_guides_state_the_rule_for_room(tmp_path, monkeypatch, caplog):
    rule = "twice the database's size free on its disk"
    for guide in (RUN, DEPLOY, INFRA):
        assert rule in one_line(guide)
    quoted = "compact not started: the database's disk has N bytes free, a rewrite needs N"
    for guide in (RUN, DEPLOY):
        assert f"`{quoted}`" in one_line(guide)
    store = Store(tmp_path / "cairn.db")
    try:
        store.delete_account(store.create_account("Gone").id)
        monkeypatch.setattr(portable.shutil, "disk_usage", lambda folder: Usage(1, 1, 7))
        with caplog.at_level(logging.INFO, logger="cairn.access"):
            Compactor(store, hours=24, clock=lambda: T0).run_once()
        (line,) = lines_of(caplog)
        assert as_pattern(quoted).fullmatch(line)
        assert store.compaction_state().deleted_at is not None  # stays due
    finally:
        store.close()
    assert POLL_SECONDS == 600.0  # "at the next poll, 10 minutes later"
    assert "it tries again at the next poll, 10 minutes later" in one_line(RUN)


def test_the_guides_quote_the_lines_of_a_rewrite_whose_log_was_held(tmp_path, caplog):
    quoted = [
        "compact done in N ms",
        "compact not finished: another connection held the file (rewritten; the write-ahead"
        " log was not emptied) in N ms",
        "compact not finished: another connection held the file (no rewrite: only the"
        " write-ahead log was due) in N ms",
        "compact done (no rewrite: only the write-ahead log was due) in N ms",
    ]
    for guide in (RUN, DEPLOY):
        said = one_line(guide)
        for line in quoted:
            assert f"`{line}`" in said, line
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        with caplog.at_level(logging.INFO, logger="cairn.access"):
            store.delete_account(store.create_account("Gone").id)
            Compactor(store, hours=24, clock=lambda: T0).run_once()
            store.delete_account(store.create_account("Gone too").id)
            reader = sqlite3.connect(db)
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM settings").fetchone()
            store._con.execute("PRAGMA busy_timeout = 50")
            try:
                Compactor(store, hours=24, clock=lambda: T0).run_once()
                Compactor(store, hours=24, clock=lambda: T0).run_once()  # as after a restart
            finally:
                reader.close()
                store._con.execute("PRAGMA busy_timeout = 5000")
            Compactor(store, hours=24, clock=lambda: T0).run_once()
    finally:
        store.close()
    seen = lines_of(caplog)
    assert len(seen) == 4
    for line, said in zip(quoted, seen, strict=True):
        assert as_pattern(line).fullmatch(said), said
    assert "neither the next try nor a restart rewrites the file again" in one_line(DEPLOY)
    assert "a deletion after it starts a new rewrite" in one_line(DEPLOY)


def test_the_compaction_runbook_tells_a_rewrite_from_a_try_at_the_log_alone():
    said = one_line(INFRA)
    assert (
        "A line that says `(rewritten; the write-ahead log was not emptied)` is a compaction"
        " that rewrote the whole database file under the store's lock" in said
    )
    assert (
        "A line that says `(no rewrite: only the write-ahead log was due)` rewrote nothing:"
        " it held writes for at most about 5 seconds" in said
    )
    assert "`store.compacted_at` is when the last rewrite began" in said
    assert "each one rewrote the whole database" not in MONITORING
    assert "the first after a deletion rewrote the whole database" in MONITORING


def test_the_compact_command_without_room_says_both_numbers_and_exits_1(
    tmp_path, monkeypatch, capsys
):
    """`compact` checks first; without the room it does not rewrite the file (the old
    versions it removed stay removed), says how many bytes are free and how many a rewrite
    needs, and exits 1."""
    from cairn.web import cli

    said = one_line(RUN)
    assert (
        "`compact` checks first; without the room it does not rewrite the file (the old"
        " versions it removed stay removed), says how many bytes are free and how many a"
        " rewrite needs, and exits 1." in said
    )
    db = tmp_path / "cairn.db"
    store = Store(db)
    store.delete_account(store.create_account("Gone").id)
    store.close()
    monkeypatch.setattr(portable.shutil, "disk_usage", lambda folder: Usage(1, 1, 7))
    capsys.readouterr()
    assert cli.main(["compact", "--db", str(db)]) == 1
    err = capsys.readouterr().err
    assert re.search(r"the database's disk has 7 bytes free, a rewrite needs \d+", err)
    store = Store(db)
    try:
        assert store.compaction_state().deleted_at is not None
    finally:
        store.close()
