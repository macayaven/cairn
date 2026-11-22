"""Unit tests for sources in the store: what tests/acceptance/test_c2_langfuse.py does not
pin. Configs, who sees them, and the claim a run takes on a source."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import cairn.store.base
from cairn.store import SOURCE_CLAIM_TTL, Store

CONFIG = {
    "base_url": "https://langfuse.example.test",
    "projects": ["payments-api"],
    "public_key_env": "LANGFUSE_PUBLIC_KEY",
    "secret_key_env": "LANGFUSE_SECRET_KEY",
}
ISSUER = "https://id.example.test"
REPORT = {"saved": 1, "unchanged": 0, "refused": 0, "rejected": 0, "skipped": 2, "malformed": 1}


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    s.create_project("payments-api")
    yield s
    s.close()


@pytest.fixture
def clock(monkeypatch):
    """The store's clock, moved by hand."""
    now = {"at": datetime(2026, 11, 10, 12, 0, tzinfo=UTC)}
    monkeypatch.setattr(cairn.store.base, "_clock", lambda: now["at"])
    return now


def test_a_source_is_stored_with_its_checked_config_and_found_by_name(store):
    made = store.add_source("traces", "langfuse", {**CONFIG, "base_url": CONFIG["base_url"] + "/"})
    assert made.config == CONFIG and made.last_report is None and made.last_error is None
    assert store.get_source("TRACES") == made
    store.add_source("alpha", "langfuse", CONFIG)
    assert [s.name for s in store.list_sources()] == ["alpha", "traces"]


@pytest.mark.parametrize("name", ["", "has space", "x" * 65, "-dash-first", None])
def test_a_source_name_follows_the_project_name_rules(store, name):
    with pytest.raises(ValueError):
        store.add_source(name, "langfuse", CONFIG)


def test_a_source_may_only_name_projects_the_workspace_has(store, db):
    before = dump(db)
    with pytest.raises(ValueError, match="no project"):
        store.add_source("traces", "langfuse", {**CONFIG, "projects": ["payments-api", "ghost"]})
    with pytest.raises(ValueError):
        store.add_source("TRACES", "langfuse", {**CONFIG, "projects": "payments-api"})
    assert dump(db) == before


def test_adding_and_removing_a_source_is_audited_and_reading_writes_nothing(store, db):
    store.add_source("traces", "langfuse", CONFIG)
    before = dump(db)
    store.list_sources(), store.get_source("traces"), store.get_source("nope")
    assert dump(db) == before
    assert store.remove_source("TRACES") is True
    removed, added = store.list_audit()[:2]
    gone = removed.detail["source"]  # refiled: no later "traces" starts with these
    assert gone.startswith("traces (removed ")
    assert removed.action == "source.removed" and added.action == "source.added"
    assert removed.detail == {"source": gone, "name": "traces"}
    assert added.detail == {"source": gone, "kind": "langfuse"}


def test_narrowed_views_and_members_see_no_source_and_manage_none(store):
    store.add_source("traces", "langfuse", CONFIG)
    project = store.get_project("payments-api")
    for view in (store.narrow(projects=(project.id,)), store.narrow(access="read")):
        assert view.list_sources() == [] and view.get_source("traces") is None
        with pytest.raises(PermissionError):
            view.add_source("more", "langfuse", CONFIG)
        with pytest.raises(PermissionError):
            view.claim_source("traces")
    owner = store.sign_in("sso", "ada", "ada@example.test", "Ada", issuer=ISSUER, join="default")
    member = store.sign_in("sso", "bo", "bo@example.test", "Bo", issuer=ISSUER, join="default")
    as_owner = store.for_account(owner.account_id, owner.user.id)
    as_member = store.for_account(member.account_id, member.user.id)
    assert [s.name for s in as_owner.list_sources()] == ["traces"]
    assert as_member.list_sources() == [] and as_member.get_source("traces") is None
    for write in (
        lambda: as_member.add_source("more", "langfuse", CONFIG),
        lambda: as_member.remove_source("traces"),
        lambda: as_member.claim_source("traces"),
    ):
        with pytest.raises(PermissionError):
            write()
    assert store.get_source("traces") is not None


def test_a_claim_is_taken_once_and_a_finished_run_stores_its_cursor_and_report(store, clock):
    store.add_source("traces", "langfuse", CONFIG)
    with pytest.raises(KeyError):
        store.claim_source("nope")
    claim = store.claim_source("traces")
    assert (claim.cursor, claim.kind, claim.config) == (None, "langfuse", CONFIG)
    assert store.claim_source("traces") is None  # held
    clock["at"] += timedelta(seconds=5)
    assert store.finish_source_run(claim, "cursor-1", REPORT) is True
    after = store.get_source("traces")
    assert (after.cursor, after.last_report, after.last_error) == ("cursor-1", REPORT, None)
    assert after.last_run_at == clock["at"].isoformat(timespec="microseconds")
    event = store.list_audit(limit=1)[0]
    assert (event.action, event.detail) == ("source.synced", {"source": "traces", **REPORT})
    again = store.claim_source("traces")
    assert again.cursor == "cursor-1"


def test_a_run_that_did_nothing_records_no_event(store):
    store.add_source("traces", "langfuse", CONFIG)
    claim = store.claim_source("traces")
    events = len(store.list_audit())
    zero = dict.fromkeys(REPORT, 0)
    assert store.finish_source_run(claim, "c", zero) is True
    assert len(store.list_audit()) == events and store.get_source("traces").last_report == zero


def test_a_failed_run_keeps_the_cursor_and_says_why_until_one_works(store):
    store.add_source("traces", "langfuse", CONFIG)
    store.finish_source_run(store.claim_source("traces"), "c1", REPORT)
    assert store.fail_source_run(store.claim_source("traces"), "Langfuse said no") is True
    failed = store.get_source("traces")
    assert (failed.cursor, failed.last_error, failed.last_report) == (
        "c1",
        "Langfuse said no",
        REPORT,
    )
    store.finish_source_run(store.claim_source("traces"), "c2", REPORT)
    assert store.get_source("traces").last_error is None


def test_two_runs_at_once_on_one_file_one_takes_the_claim(db, store):
    store.add_source("traces", "langfuse", CONFIG)
    other = Store(db)  # another process: the command line next to the server
    try:
        first, second = store.claim_source("traces"), other.claim_source("traces")
        assert first is not None and second is None
        assert store.finish_source_run(first, "c1", REPORT) is True
        assert other.claim_source("traces").cursor == "c1"
    finally:
        other.close()


def test_a_claim_held_by_a_killed_run_can_be_taken_after_the_limit(store, clock):
    store.add_source("traces", "langfuse", CONFIG)
    assert store.claim_source("traces") is not None  # its process dies holding it
    clock["at"] += SOURCE_CLAIM_TTL - timedelta(seconds=1)
    assert store.claim_source("traces") is None
    clock["at"] += timedelta(seconds=1)
    assert store.claim_source("traces") is not None


def test_a_slow_run_overtaken_after_the_limit_stores_nothing(store, clock, db):
    store.add_source("traces", "langfuse", CONFIG)
    slow = store.claim_source("traces")
    clock["at"] += SOURCE_CLAIM_TTL
    fast = store.claim_source("traces")
    assert fast is not None
    assert store.finish_source_run(fast, "fast", REPORT) is True
    before = dump(db)
    assert store.finish_source_run(slow, "slow", REPORT) is False  # superseded
    assert store.fail_source_run(slow, "late") is False
    assert dump(db) == before and store.get_source("traces").cursor == "fast"


def test_a_removed_source_ends_its_run_quietly(store):
    store.add_source("traces", "langfuse", CONFIG)
    claim = store.claim_source("traces")
    store.remove_source("traces")
    assert store.finish_source_run(claim, "c", REPORT) is False
    store.add_source("traces", "langfuse", CONFIG)  # a new source of the same name
    assert store.finish_source_run(claim, "c", REPORT) is False
    assert store.get_source("traces").cursor is None


def test_deleting_a_workspace_deletes_its_sources(store, db):
    other = store.for_account(store.create_account("Brook").id)
    other.create_project("payments-api")
    other.add_source("brook-traces", "langfuse", CONFIG)
    store.delete_account(other.account_id)
    assert "brook-traces" not in dump(db)
