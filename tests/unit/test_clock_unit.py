"""The store's clock: `Store(path, clock=...)` is the one source of the time for every
timestamp the store writes and every expiry it checks (H6 makes months and rate windows
testable through it)."""

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import cairn.store
from cairn.store import Store

LATER = datetime(2031, 1, 2, tzinfo=UTC)  # a year no real clock in this suite reads
CALLBACK = "http://127.0.0.1:33418/callback"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_every_timestamp_the_store_writes_comes_from_its_clock(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock(LATER))  # the default account too
    try:
        brook = store.for_account(store.create_account("Brook").id)
        project = brook.create_project("ops")
        brook.add_note(project.id, "# Retry\n\nQuill retries.\n")
        brook.save_page(project.id, "retry", "Retry", "", ())
        brook.create_key("laptop")
        brook.create_session()
        brook.create_set("all", (project.id,))
        brook.set_setting("guard.policy", "off")
        config = {
            "base_url": "https://langfuse.example.test",
            "projects": ["ops"],
            "public_key_env": "LANGFUSE_PUBLIC_KEY",
            "secret_key_env": "LANGFUSE_SECRET_KEY",
        }
        brook.add_source("traces", "langfuse", config)
        claim = brook.claim_source("traces")
        assert brook.finish_source_run(claim, "c1", {"saved": 1})
        assert brook.fail_source_run(brook.claim_source("traces"), "boom")
        client = store.register_client("agent", [CALLBACK])
        code = brook.create_code(client.id, CALLBACK, "c" * 43)
        tokens = store.redeem_code(code, client.id)
        assert tokens is not None
        assert store.rotate_refresh_token(tokens.refresh_token, client.id) is not None
        store.sign_in(
            "sso", "subject-1", "ada@example.test", "Ada", issuer="https://id.example.test",
            join="personal",
        )  # fmt: skip
        brook.invite("bo@example.test")
        store.delete_account(store.create_account("Gone").id)  # a compaction falls due
        store.compact()
    finally:
        store.close()
    stamps = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", dump(db))
    assert stamps and all(stamp.startswith("2031-") for stamp in stamps), set(stamps)


def test_expiry_is_judged_by_the_stores_clock(tmp_path):
    clock = Clock(LATER)
    store = Store(tmp_path / "cairn.db", clock=clock)
    try:
        token = store.create_session(ttl_seconds=60)
        assert store.check_session(token)
        clock.now = LATER + timedelta(seconds=60)
        assert not store.check_session(token)
    finally:
        store.close()


def test_without_a_clock_the_store_reads_the_modules_at_each_call(tmp_path, monkeypatch):
    store = Store(tmp_path / "cairn.db")
    try:
        monkeypatch.setattr(cairn.store.base, "_clock", lambda: LATER)  # after the store opened
        assert store.create_project("ops").created_at.startswith("2031-01-02")
    finally:
        store.close()


def test_a_clock_without_a_time_zone_is_refused(tmp_path):
    db = tmp_path / "cairn.db"
    with pytest.raises(ValueError, match="time zone"):
        Store(db, clock=lambda: datetime(2031, 1, 2))  # it makes the default account
    Store(db).close()  # nothing was half written: the file opens as a fresh one
