"""Unit tests for the sources runner: what tests/acceptance/test_c2_langfuse.py does not pin.
A fake kind stands in for Langfuse; time and waiting are injected; nothing sleeps."""

import asyncio
import json
import logging
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar
from urllib.parse import quote

import httpx
import pytest

import cairn.store.base
from cairn.compiler import GateResult
from cairn.gate import lint_gate
from cairn.guard import Finding
from cairn.memory import BadRequest, Memory
from cairn.plans import Plan, Plans
from cairn.sources import (
    KINDS,
    SOURCE_LOGGER,
    NoteDraft,
    Pulled,
    Skip,
    SourceError,
    SourceKind,
    SyncReport,
    run_source,
    sync_all,
    sync_forever,
)
from cairn.store import SOURCE_CLAIM_TTL, Store
from cairn.store.usage import Limits

NOTE = "# Retries\n\nQuill retries a failed charge 3 times.\n"


class Fake:
    """A source whose pull answers what the test put in `answers` (or raises it)."""

    answers: ClassVar[list] = []
    cursors: ClassVar[list] = []  # the cursor each pull started from
    deadlines: ClassVar[list] = []  # the deadline each source was opened with
    during_pull = None  # called inside pull, to change the world while a run is under way
    secrets = ("fake/secret+value=",)  # what the source authenticates with

    def __init__(self, name: str) -> None:
        self.name = name

    async def pull(self, cursor):
        Fake.cursors.append(cursor)
        if Fake.during_pull:
            Fake.during_pull()
        answer = Fake.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def check(config):
    if not isinstance(config.get("projects"), list):
        raise ValueError("projects")
    return dict(config)


def opened(name, config, *, http, env, clock=None, sleep=None, deadline=None, since=None):
    if "FAKE_KEY" not in env:
        raise SourceError("set FAKE_KEY in the environment")
    Fake.deadlines.append(deadline)
    return Fake(name)


@pytest.fixture(autouse=True)
def fake_kind(monkeypatch):
    monkeypatch.setitem(KINDS, "fake", SourceKind(check=check, open=opened, via="fake"))
    Fake.answers, Fake.during_pull, Fake.cursors, Fake.deadlines = [], None, [], []


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    s.create_project("api")
    s.add_source("feed", "fake", {"projects": ["api"]})
    yield s
    s.close()


def draft(project="api", text=NOTE, ref="https://example.test/1"):
    return NoteDraft(project=project, title="Retries", text=text, source_ref=ref, label="fake")


def run(store, name="feed", env=None):
    return asyncio.run(
        run_source(
            store, name, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"} if env is None else env
        )
    )


def test_importing_the_seam_does_not_import_the_memory_use_cases():
    code = "import sys, cairn.sources; print('cairn.memory' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_a_run_counts_every_outcome_with_the_reasons_and_uses_the_kinds_via(store):
    store.create_project("other")
    Fake.answers = [
        Pulled(
            drafts=[draft(), draft("other"), draft("ghost-project")],
            skipped=[Skip("s9", "marked skip")],
            cursor="c1",
        )
    ]
    report = run(store)
    assert (report.saved, report.skipped) == (1, 3)
    assert report.reasons == {"marked skip": 1, "project not allowed": 2}
    (note,) = store.list_notes(store.get_project("api").id)
    assert note.via == "fake"
    assert store.get_source("feed").last_report["reasons"] == report.reasons


def test_a_draft_with_a_field_past_its_bound_is_skipped_and_the_run_goes_on(store):
    long = draft(ref="https://example.test/" + "x" * 500)  # source_ref past FIELD_MAX
    Fake.answers = [Pulled(drafts=[long, draft()], cursor="c1")]
    report = run(store)
    assert (report.saved, report.reasons) == (1, {"a field past its bound": 1})
    assert store.get_source("feed").last_error is None


def test_a_draft_refused_for_what_it_is_is_skipped_and_the_cursor_moves_past_it(store):
    """Empty text or a field past FIELD_MAX: skipped by its reason's name, counted as
    `malformed` in the run's one audit event, and never proposed again."""
    long = draft(ref="https://example.test/" + "x" * 500)
    empty = draft(text="  \n", ref="https://example.test/empty")
    Fake.answers = [Pulled(drafts=[empty, long, draft()], cursor="c1")]
    report = run(store)
    assert (report.saved, report.skipped, report.malformed) == (1, 2, 2)
    assert report.reasons == {"empty text": 1, "a field past its bound": 1}
    source = store.get_source("feed")
    assert (source.last_error, source.cursor) == (None, "c1")
    assert source.last_report["malformed"] == 2
    synced = [e for e in store.list_audit() if e.action == "source.synced"]
    assert len(synced) == 1 and synced[0].detail["malformed"] == 2
    said = json.dumps(synced[0].detail)
    assert "example.test" not in said and "empty text" not in said  # counts only
    Fake.answers = [Pulled(drafts=[], cursor="c2")]
    assert run(store).malformed == 0  # the cursor moved past them: not proposed again


def test_a_bad_request_for_another_reason_still_fails_the_run(store, monkeypatch):
    def unknown(self, *args, **kwargs):
        raise BadRequest("something else")

    monkeypatch.setattr(Memory, "note", unknown)
    Fake.answers = [Pulled(drafts=[draft()], cursor="c1")]
    with pytest.raises(BadRequest):
        run(store)
    source = store.get_source("feed")
    assert (source.last_error, source.cursor) == ("the run failed: BadRequest", None)


def test_a_project_deleted_or_missing_is_skipped_not_created(store, monkeypatch):
    store.add_source("wide", "fake", {"projects": ["api"]})
    with store._write() as con:  # the project disappears after the source was added
        con.execute("DELETE FROM projects WHERE name = 'api'")
    Fake.answers = [Pulled(drafts=[draft()], cursor="c")]
    report = run(store, "wide")
    assert (report.saved, report.reasons) == (0, {"no such project": 1})
    assert store.list_projects() == []


# H5b: a forgotten note, and a route into a deleted project


def test_a_draft_its_project_has_forgotten_is_skipped_and_the_cursor_moves_past_it(store):
    api = store.get_project("api")
    note, _ = store.add_note(api.id, NOTE)
    store.forget_note(note.id)
    Fake.answers = [Pulled(drafts=[draft(), draft(text=NOTE + "\nAnd more.\n")], cursor="c1")]
    report = run(store)
    assert (report.saved, report.skipped, report.malformed) == (1, 1, 0)
    assert report.reasons == {"forgotten": 1}
    source = store.get_source("feed")
    assert (source.last_error, source.cursor) == (None, "c1")
    assert NOTE not in [c.text for c in store.list_notes(api.id)]


def test_a_route_into_a_deleted_project_is_unknown_and_the_others_go_on(store):
    store.create_project("other")
    store.add_source("wide", "fake", {"projects": ["api", "other"]})
    assert store.delete_project(store.get_project("api").id)
    assert store.get_source("wide").config["projects"] == ["other"]  # the name went with it
    Fake.answers = [Pulled(drafts=[draft(), draft("other")], cursor="c")]
    report = run(store, "wide")
    assert (report.saved, report.reasons) == (1, {"project not allowed": 1})
    assert [b.name for b in store.list_projects()] == ["other"]  # nothing is made again
    assert store.get_source("wide").cursor == "c"


def test_a_run_while_another_holds_the_source_does_nothing(store):
    held = store.claim_source("feed")
    report = run(store)
    assert report.busy and (report.saved, report.skipped) == (0, 0)
    assert store.get_source("feed").last_run_at is None
    store.finish_source_run(held, "c", {})


def test_a_run_overtaken_after_the_limit_stores_no_cursor_and_says_so(store, monkeypatch):
    now = {"at": cairn.store.base._clock()}
    monkeypatch.setattr(cairn.store.base, "_clock", lambda: now["at"])

    def overtaken():
        now["at"] += SOURCE_CLAIM_TTL
        faster = store.claim_source("feed")
        store.finish_source_run(faster, "faster", {})

    Fake.during_pull = overtaken
    Fake.answers = [Pulled(drafts=[draft()], cursor="slow")]
    report = run(store)
    assert report.superseded and report.saved == 0  # the claim is checked before each note
    assert store.get_source("feed").cursor == "faster"


def test_an_unexpected_failure_keeps_only_its_class_and_releases_the_claim(store):
    Fake.answers = [RuntimeError("the note text was: secret plans")]
    with pytest.raises(RuntimeError):
        run(store)
    failed = store.get_source("feed")
    assert "RuntimeError" in failed.last_error and "secret plans" not in failed.last_error
    assert failed.cursor is None
    Fake.answers = [Pulled(cursor="c1")]
    assert not run(store).busy  # the claim was released
    assert store.get_source("feed").last_error is None


def test_a_source_error_is_kept_word_for_word(store):
    with pytest.raises(SourceError):
        run(store, env={})
    assert store.get_source("feed").last_error == "set FAKE_KEY in the environment"


def test_an_unknown_source_is_a_key_error(store):
    with pytest.raises(KeyError):
        run(store, "nope")


def test_one_pass_of_the_background_sync_logs_a_failure_by_name_and_class(store, caplog):
    store.add_source("broken", "fake", {"projects": ["api"]})
    Fake.answers = [SourceError("body: sk-lf-secret"), Pulled(drafts=[draft()], cursor="c")]
    waits: list[float] = []
    ticks = iter([100.0, 103.0])

    async def wait(seconds):
        waits.append(seconds)
        raise asyncio.CancelledError  # the server shuts down during the wait

    async def forever():
        await sync_forever(
            store, 60, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"}, wait=wait,
            monotonic=lambda: next(ticks),
        )  # fmt: skip

    with (
        caplog.at_level(logging.WARNING, logger=SOURCE_LOGGER),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(forever())
    assert waits == [57.0]  # what is left of the interval
    (record,) = caplog.records
    assert "broken" in record.getMessage() and "SourceError" in record.getMessage()
    assert "sk-lf-secret" not in record.getMessage()
    assert store.list_notes(store.get_project("api").id)  # the other source still ran


def test_a_pass_longer_than_the_interval_starts_the_next_at_once(store):
    Fake.answers = [Pulled(cursor="c"), Pulled(cursor="c")]
    waits: list[float] = []
    ticks = iter([0.0, 90.0, 90.0, 95.0])

    async def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            sync_forever(
                store, 60, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"}, wait=wait,
                monotonic=lambda: next(ticks),
            )
        )  # fmt: skip
    assert waits == [0, 55.0]


def test_sync_all_lets_cancellation_through(store):
    Fake.answers = [asyncio.CancelledError()]

    async def everything():
        return await sync_all(store, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"})

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(everything())
    assert store.claim_source("feed") is not None  # the stopped run let go of it


def test_the_background_sync_refuses_an_interval_below_a_second(store):
    with pytest.raises(ValueError):
        asyncio.run(sync_forever(store, 0.5, http=httpx.AsyncClient(), env={}))


def test_a_rerun_after_the_limit_is_not_blocked_by_a_dead_claim(store, monkeypatch):
    store.claim_source("feed")  # a process killed while it held the claim
    assert run(store).busy
    later = cairn.store.base._clock() + SOURCE_CLAIM_TTL + timedelta(seconds=1)
    monkeypatch.setattr(cairn.store.base, "_clock", lambda: later)
    Fake.answers = [Pulled(cursor="c")]
    assert not run(store).busy and store.get_source("feed").cursor == "c"


class RefuseAll:
    def check(self, text):
        return [Finding(rule="test.everything", kind="secret", line=1)]


def test_the_background_sync_screens_with_the_guard_it_is_given(store):
    Fake.answers = [Pulled(drafts=[draft()], cursor="c")]

    async def wait(seconds):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            sync_forever(
                store, 60, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"}, guard=RefuseAll(),
                wait=wait,
            )
        )  # fmt: skip
    assert store.get_source("feed").last_report["refused"] == 1
    assert not store.list_notes(store.get_project("api").id)


def test_a_pass_that_breaks_is_logged_by_class_and_the_next_pass_still_runs(
    store, caplog, monkeypatch
):
    passes: list[int] = []

    def broken():
        passes.append(1)
        raise sqlite3.OperationalError("database is locked: SELECT secret")

    monkeypatch.setattr(store, "list_accounts", broken)
    waits: list[float] = []

    async def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise asyncio.CancelledError

    with (
        caplog.at_level(logging.WARNING, logger=SOURCE_LOGGER),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(
            sync_forever(store, 60, http=httpx.AsyncClient(), env={}, wait=wait,
                         monotonic=lambda: 0.0)
        )  # fmt: skip
    assert len(passes) == 2 and waits == [60, 60]
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2 and all("OperationalError" in m for m in messages)
    assert not any("SELECT" in m for m in messages)


def test_a_stopped_run_lets_the_note_it_is_writing_finish_first(store):
    """Shutdown cancels the background sync while a note is being written on a worker
    thread: the cancellation waits for that write, so the server does not close the store
    under it, and the run still lets go of the source."""
    entered, release = threading.Event(), threading.Event()

    def slow_gate(page, spans):
        entered.set()
        assert release.wait(10)
        return lint_gate(page, spans)

    Fake.answers = [Pulled(drafts=[draft()], cursor="c")]

    async def scenario():
        task = asyncio.create_task(
            run_source(store, "feed", http=httpx.AsyncClient(), env={"FAKE_KEY": "x"},
                       gate=slow_gate)
        )  # fmt: skip
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        for _ in range(5):
            await asyncio.sleep(0)  # yields only: the loop turns, no time passes
        held = not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return held

    assert asyncio.run(scenario())
    assert store.list_notes(store.get_project("api").id)  # the write in flight finished
    assert "stopped" in store.get_source("feed").last_error
    assert store.get_source("feed").cursor is None
    assert store.claim_source("feed") is not None


STORE_CALLS = ("claim_source", "get_project", "finish_source_run", "fail_source_run")
LISTING_CALLS = ("list_accounts", "for_account", "list_sources")


def test_the_runner_does_its_database_work_off_the_event_loop(store, monkeypatch):
    on_loop: list[str] = []
    loop_thread = threading.get_ident()

    def watched(name):
        original = getattr(Store, name)

        def call(self, *args, **kwargs):
            if threading.get_ident() == loop_thread:
                on_loop.append(name)
            return original(self, *args, **kwargs)

        return call

    for name in STORE_CALLS + LISTING_CALLS:
        monkeypatch.setattr(Store, name, watched(name))
    Fake.answers = [Pulled(drafts=[draft()], cursor="c"), SourceError("down")]
    asyncio.run(sync_all(store, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"}))
    asyncio.run(sync_all(store, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"}))
    assert on_loop == []
    assert store.get_source("feed").last_error == "down"


def test_a_failure_while_storing_the_result_still_releases_the_source(store, monkeypatch):
    def broken(self, *args):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(Store, "finish_source_run", broken)
    Fake.answers = [Pulled(cursor="c")]
    with pytest.raises(sqlite3.OperationalError):
        run(store)
    assert "OperationalError" in store.get_source("feed").last_error
    assert store.claim_source("feed") is not None  # released, not held until the limit


def test_a_run_stopped_while_claiming_does_not_keep_the_claim(store, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = Store.claim_source

    def slow_claim(self, name):
        entered.set()
        assert release.wait(10)
        return original(self, name)

    monkeypatch.setattr(Store, "claim_source", slow_claim)

    async def scenario():
        task = asyncio.create_task(
            run_source(store, "feed", http=httpx.AsyncClient(), env={"FAKE_KEY": "x"})
        )
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        for _ in range(5):
            await asyncio.sleep(0)  # yields only
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    monkeypatch.setattr(Store, "claim_source", original)
    assert store.claim_source("feed") is not None


LEAK = Fake.secrets[0]


def test_a_draft_or_a_reason_holding_a_sources_secret_is_neither_written_nor_stored(store):
    Fake.answers = [
        Pulled(
            drafts=[
                draft(text=NOTE + LEAK),
                draft(ref="https://example.test/" + quote(LEAK, safe="")),
                draft(),
            ],
            skipped=[Skip("s9", f"odd {LEAK}")],
            cursor="c",
        )
    ]
    report = run(store)
    assert report.saved == 1
    assert report.reasons == {"held the source's credentials": 3}
    assert LEAK not in json.dumps(store.get_source("feed").last_report)


def test_an_error_or_a_cursor_holding_a_sources_secret_is_not_stored(store):
    Fake.answers = [SourceError(f"bad {LEAK}")]
    with pytest.raises(SourceError):
        run(store)
    kept = store.get_source("feed").last_error
    assert LEAK not in kept and "credentials" in kept
    Fake.answers = [Pulled(cursor=f"c {LEAK}")]
    with pytest.raises(SourceError, match="cursor"):
        run(store)
    assert store.get_source("feed").cursor is None


def drafts_with_resume(count: int) -> list[NoteDraft]:
    """Drafts as a source that can stop after any of them proposes them: each carries the
    cursor that resumes right after it."""
    return [
        NoteDraft(
            project="api", title=f"Note {n}", text=f"# Note {n}\n\nQuill fact number {n}.\n",
            source_ref=f"https://example.test/{n}", label="fake", resume=f"after-{n}",
        )
        for n in range(count)
    ]  # fmt: skip


def after_notes(monkeypatch, count: int, then) -> None:
    """Run `then()` once `count` notes have been written."""
    original, written = Memory.note, []

    def note(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        written.append(result)
        if len(written) == count:
            then()
        return result

    monkeypatch.setattr(Memory, "note", note)


def test_a_run_overtaken_after_the_limit_writes_nothing_more(store, monkeypatch):
    Fake.answers = [Pulled(drafts=drafts_with_resume(3), cursor="all")]
    taken = []

    def take_over():  # the limit passes and another run takes the source
        later = cairn.store.base._clock() + SOURCE_CLAIM_TTL + timedelta(seconds=1)
        monkeypatch.setattr(cairn.store.base, "_clock", lambda: later)
        taken.append(store.claim_source("feed"))

    after_notes(monkeypatch, 1, take_over)
    report = run(store)
    assert taken[0] is not None and report.superseded and report.saved == 1
    assert len(store.list_notes(store.get_project("api").id)) == 1  # nothing after it
    info = store.get_source("feed")
    assert info.cursor is None and info.last_error is None  # the other run's to settle
    assert store.finish_source_run(taken[0], "theirs", {})  # its claim is intact


def test_a_run_past_its_deadline_stops_and_stores_where_it_got_to(store, monkeypatch):
    now = [datetime(2026, 11, 10, 12, 0, tzinfo=UTC)]
    Fake.answers = [Pulled(drafts=drafts_with_resume(3), cursor="all")]
    after_notes(monkeypatch, 2, lambda: now.__setitem__(0, now[0] + timedelta(minutes=11)))
    report = asyncio.run(
        run_source(store, "feed", http=httpx.AsyncClient(), env={"FAKE_KEY": "x"},
                   clock=lambda: now[0])
    )  # fmt: skip
    assert (report.saved, report.left) == (2, 1)
    assert store.get_source("feed").cursor == "after-1"  # right after the last one written
    assert store.get_source("feed").last_report["left"] == 1
    claimed = store.claim_source("feed")
    assert claimed is not None  # released
    store.finish_source_run(claimed, "after-1", {})
    monkeypatch.setattr(Memory, "note", original_note)
    Fake.answers, Fake.cursors = [Pulled(drafts=drafts_with_resume(3)[2:], cursor="all")], []
    assert run(store).saved == 1 and Fake.cursors == ["after-1"]


def test_a_run_past_its_deadline_without_a_resume_point_keeps_the_old_cursor(store):
    now = [datetime(2026, 11, 10, 12, 0, tzinfo=UTC)]

    def late():
        now[0] += timedelta(minutes=11)

    Fake.during_pull = late  # the pull itself took the whole time
    Fake.answers = [Pulled(drafts=[draft()], cursor="all")]
    report = asyncio.run(
        run_source(store, "feed", http=httpx.AsyncClient(), env={"FAKE_KEY": "x"},
                   clock=lambda: now[0])
    )  # fmt: skip
    assert (report.saved, report.left) == (0, 1)
    assert store.get_source("feed").cursor is None


class SignsIn(Fake):
    """A source that gets a token only during its pull, as a Google source gets its access
    token: its `secrets` hold the token from then on."""

    TOKEN = "late/" + "pull-token+value="
    failure: ClassVar[BaseException | None] = None

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.token: str | None = None

    @property
    def secrets(self):
        return ("opened/" + "secret+value=", *((self.token,) if self.token else ()))

    async def pull(self, cursor):
        self.token = SignsIn.TOKEN
        if SignsIn.failure is not None:
            raise SignsIn.failure
        return await super().pull(cursor)


@pytest.fixture
def signs_in(monkeypatch, store):
    def opened_late(name, config, **given):
        return SignsIn(name)

    monkeypatch.setitem(KINDS, "late", SourceKind(check=check, open=opened_late, via="fake"))
    store.add_source("signed", "late", {"projects": ["api"]})
    SignsIn.failure = None
    return SignsIn.TOKEN


def test_what_a_source_signs_in_with_during_its_pull_is_withheld_too(store, signs_in):
    Fake.answers = [
        Pulled(
            drafts=[draft(text=NOTE + signs_in), draft(ref="https://example.test/2")],
            skipped=[Skip("s1", f"odd {quote(signs_in, safe='')}")],
            cursor="c",
        )
    ]
    report = run(store, "signed")
    assert report.saved == 1 and report.reasons == {"held the source's credentials": 2}
    assert signs_in not in json.dumps(store.get_source("signed").last_report)
    Fake.answers = [Pulled(cursor=f"c {signs_in}")]
    with pytest.raises(SourceError, match="cursor"):
        run(store, "signed")
    assert store.get_source("signed").cursor == "c"


def test_a_failed_pulls_message_holding_what_it_signed_in_with_is_not_kept(store, signs_in):
    SignsIn.failure = SourceError(f"the server said {signs_in}")
    with pytest.raises(SourceError):
        run(store, "signed")
    kept = store.get_source("signed").last_error
    assert signs_in not in kept and "credentials" in kept


def test_a_resume_point_holding_a_credential_is_never_stored(store):
    times = iter([datetime(2026, 11, 10, 12, 0, tzinfo=UTC)] * 2)
    late = datetime(2026, 11, 10, 12, 11, tzinfo=UTC)
    leaky = NoteDraft(
        project="api", title="Leaky", text=NOTE, source_ref="https://example.test/l",
        label="fake", resume=f"after-{Fake.secrets[0]}",
    )  # fmt: skip
    Fake.answers = [Pulled(drafts=[leaky, *drafts_with_resume(1)], cursor="all")]
    with pytest.raises(SourceError, match="cursor"):
        asyncio.run(
            run_source(store, "feed", http=httpx.AsyncClient(), env={"FAKE_KEY": "x"},
                       clock=lambda: next(times, late))
        )  # fmt: skip
    info = store.get_source("feed")
    assert info.cursor is None and Fake.secrets[0] not in (info.last_error or "")


def test_a_pull_gets_half_the_runs_time_so_what_it_found_is_still_written(store):
    now = [datetime(2026, 11, 10, 12, 0, tzinfo=UTC)]

    def slow():  # the pull uses every moment it was given
        now[0] = Fake.deadlines[0]

    Fake.during_pull = slow
    Fake.answers = [Pulled(drafts=drafts_with_resume(2), cursor="all")]
    report = asyncio.run(
        run_source(store, "feed", http=httpx.AsyncClient(), env={"FAKE_KEY": "x"},
                   clock=lambda: now[0])
    )  # fmt: skip
    assert Fake.deadlines == [datetime(2026, 11, 10, 12, 5, tzinfo=UTC)]  # half of RUN_LIMIT
    assert (report.saved, report.left) == (2, 0)
    assert store.get_source("feed").cursor == "all"


class RefuseForbidden:
    def check(self, text):
        found = "Forbidden" in text
        return [Finding(rule="test.forbidden", kind="secret", line=1)] if found else []


def reject_all(page, spans):
    return GateResult(passed=False, violations=["test.rejected: it said SECRET-PLANS"])


def test_a_refused_or_rejected_draft_is_listed_by_its_link_and_codes_never_its_text(store):
    refused = draft(text="# Plans\n\nForbidden SECRET-PLANS.\n", ref="https://example.test/r")
    rejected = draft(text="# Plans\n\nQuill SECRET-PLANS.\n", ref="https://example.test/j")
    Fake.answers = [Pulled(drafts=[refused, rejected], cursor="c")]
    report = asyncio.run(
        run_source(store, "feed", http=httpx.AsyncClient(), env={"FAKE_KEY": "x"},
                   guard=RefuseForbidden(), gate=reject_all)
    )  # fmt: skip
    api = store.get_project("api").id  # each names its project: who sees the link reaches it
    listed = [
        {"link": "https://example.test/r", "status": "refused", "codes": ["test.forbidden"],
         "project": api},
        {"link": "https://example.test/j", "status": "rejected", "codes": ["test.rejected"],
         "project": api},
    ]  # fmt: skip
    assert (report.refused, report.rejected, report.declined) == (1, 1, listed)
    stored = store.get_source("feed").last_report
    assert stored["declined"] == listed and "SECRET-PLANS" not in json.dumps(stored)
    event = store.list_audit(limit=1)[0]
    assert event.action == "source.synced" and "declined" not in event.detail


original_note = Memory.note


# H6: a workspace at a cap


def test_a_draft_past_a_cap_waits_with_every_draft_after_it(store):
    Fake.answers = [Pulled(drafts=drafts_with_resume(3), cursor="all")]
    report = run(store.limited(Limits(notes_per_month=1)))
    assert (report.saved, report.capped, report.refused, report.left) == (1, 2, 0, 0)
    assert report.declined == []  # not declined for good
    info = store.get_source("feed")
    assert info.cursor == "after-0"  # the next pull starts at the first draft it could not write
    assert info.last_report["capped"] == 2

    Fake.answers = [Pulled(drafts=drafts_with_resume(3)[1:], cursor="all")]  # on a bigger plan
    report = run(store.limited(Limits(notes_per_month=10)))
    assert (report.saved, report.capped) == (2, 0)
    assert store.get_source("feed").cursor == "all"


def test_a_capped_draft_without_a_resume_point_keeps_the_cursor_the_run_began_from(store):
    texts = [NOTE, NOTE + "\nAnd a second fact.\n"]
    drafts = [draft(text=text, ref=f"https://example.test/{n}") for n, text in enumerate(texts)]
    Fake.answers = [Pulled(drafts=drafts, cursor="c2")]
    report = run(store.limited(Limits(notes_per_month=1)))
    assert (report.saved, report.capped) == (1, 1)
    assert store.get_source("feed").cursor is None  # never past what it could not write


def test_a_downgrade_during_a_source_pass_holds_its_next_draft(store, monkeypatch):
    plans = Plans(
        {"big": Plan("Big", notes_per_month=10), "small": Plan("Small", notes_per_month=1)},
        "big",
    )
    Fake.answers = [Pulled(drafts=drafts_with_resume(3), cursor="all")]
    after_notes(monkeypatch, 1, lambda: store.set_plan(store.account_id, "small"))
    (outcome,) = asyncio.run(
        sync_all(store, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"}, plans=plans)
    )
    assert (outcome.report.saved, outcome.report.capped) == (1, 2)
    assert outcome.report.hold["plan"] == "small"  # the plan the write was refused on
    assert len(store.list_notes(store.get_project("api").id)) == 1


def test_the_background_sync_holds_each_workspace_to_its_plan(store):
    plans = Plans({"none": Plan("None", notes_per_month=0), "open": Plan("Open")}, "none")
    Fake.answers = [Pulled(drafts=[draft()], cursor="c")]
    outcomes = asyncio.run(
        sync_all(store, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"}, plans=plans)
    )
    assert [(o.report.saved, o.report.capped) for o in outcomes] == [(0, 1)]
    store.set_plan(store.account_id, "open")
    Fake.answers = [Pulled(drafts=[draft()], cursor="c")]
    outcomes = asyncio.run(
        sync_all(store, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"}, plans=plans)
    )
    assert [o.report.saved for o in outcomes] == [1]


@pytest.mark.parametrize(("operator", "saved"), [(None, 0), ("open", 1)])
def test_the_background_sync_holds_a_workspace_to_the_plan_in_force(
    store, monkeypatch, operator, saved
):
    """The plan in force: the operator's, else the default."""
    plans = Plans({"none": Plan("None", notes_per_month=0), "open": Plan("Open")}, "none")
    asked = []
    resolve = Plans.in_force
    monkeypatch.setattr(Plans, "in_force", lambda self, *a: asked.append(a[1]) or resolve(self, *a))
    if operator:
        store.set_plan(store.account_id, operator)
    Fake.answers = [Pulled(drafts=[draft()], cursor="c")]
    outcomes = asyncio.run(
        sync_all(store, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"}, plans=plans)
    )
    assert [(o.report.saved, o.report.capped) for o in outcomes] == [(saved, 1 - saved)]
    assert asked == [store.account_id]  # the one function, not a copy of it


# A source held at a monthly cap

NOVEMBER = datetime(2026, 11, 10, 12, tzinfo=UTC)
DECEMBER = datetime(2026, 12, 1, tzinfo=UTC)
TINY = Plans({"tiny": Plan("Tiny", notes_per_month=1), "solo": Plan("Solo")}, "tiny")


class Moment:
    def __init__(self) -> None:
        self.now = NOVEMBER

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def held(tmp_path):
    """A workspace on the Tiny plan whose source has hit the month's note cap."""
    moment = Moment()
    s = Store(tmp_path / "held.db", clock=moment)
    s.create_project("api")
    s.add_source("feed", "fake", {"projects": ["api"]})
    Fake.answers = [Pulled(drafts=drafts_with_resume(3), cursor="all")]
    report = sync(s, moment)
    assert (report.saved, report.capped, report.held) == (1, 2, False)
    yield SimpleNamespace(store=s, moment=moment, report=report)
    s.close()


def sync(store, moment, plans=TINY):
    (outcome,) = asyncio.run(
        sync_all(store, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"}, clock=moment,
                 plans=plans)
    )  # fmt: skip
    return outcome.report


def test_a_source_at_the_months_cap_records_until_when_it_is_held(held):
    hold = held.store.get_source("feed").last_report["hold"]
    assert hold == {"until": DECEMBER.isoformat(), "plan": "tiny", "cap": 1}


def test_a_held_source_is_not_pulled_again_and_nothing_is_written(held):
    before = held.store.get_source("feed")
    held.moment.now = NOVEMBER + timedelta(days=5)
    report = sync(held.store, held.moment)
    assert report.held and report.saved == 0 and Fake.cursors == [None]  # one pull only
    assert held.store.get_source("feed") == before  # its record, the hold included, kept


def test_a_held_source_is_pulled_again_once_the_month_resets(held):
    held.moment.now = DECEMBER
    Fake.answers = [Pulled(drafts=drafts_with_resume(3)[1:], cursor="all")]
    report = sync(held.store, held.moment)
    assert not report.held and report.saved == 1 and Fake.cursors == [None, "after-0"]
    assert held.store.get_source("feed").last_report.get("hold") is not None  # capped again


@pytest.mark.parametrize("change", ["plan", "cap"])
def test_a_held_source_is_pulled_again_once_the_workspaces_plan_changes(held, change):
    if change == "plan":
        held.store.set_plan(held.store.account_id, "solo")
        plans = TINY
    else:  # the operator's file gives the same plan a bigger cap
        plans = Plans({"tiny": Plan("Tiny", notes_per_month=5)}, "tiny")
    Fake.answers = [Pulled(drafts=drafts_with_resume(3)[1:], cursor="all")]
    report = sync(held.store, held.moment, plans)
    assert not report.held and report.saved == 2
    info = held.store.get_source("feed")
    assert info.cursor == "all" and info.last_report.get("hold") is None


def test_a_storage_cap_holds_nothing(tmp_path):
    moment = Moment()
    s = Store(tmp_path / "s.db", clock=moment)
    try:
        s.create_project("api")
        s.add_source("feed", "fake", {"projects": ["api"]})
        plans = Plans({"small": Plan("Small", storage_bytes=20)}, "small")  # no note fits
        for _ in range(2):
            Fake.answers = [Pulled(drafts=drafts_with_resume(2), cursor="all")]
            report = sync(s, moment, plans)
            assert report.capped == 2 and not report.held
        assert s.get_source("feed").last_report.get("hold") is None
    finally:
        s.close()


# H6c: a full page


def test_a_draft_for_a_full_page_is_skipped_and_the_run_goes_on(store, monkeypatch):
    import cairn.memory

    monkeypatch.setattr(cairn.memory, "PAGE_CLAIMS_MAX", 2)
    api = store.get_project("api")
    Memory(store, lint_gate).note("api", "# Retries\n\nOne.\n\nTwo.\n")
    other = NoteDraft(
        project="api",
        title="Elsewhere",
        text="Three.\n",
        source_ref="https://example.test/2",
        label="fake",
    )
    Fake.answers = [Pulled(drafts=[draft(), other], cursor="c1")]
    report = run(store)
    assert (report.saved, report.skipped, report.malformed) == (1, 1, 0)
    assert report.reasons == {"page full": 1}
    source = store.get_source("feed")
    assert (source.last_error, source.cursor) == (None, "c1")
    assert len(store.list_notes(api.id)) == 2  # the full page's draft was not stored


def test_a_draft_under_headings_past_their_bound_is_skipped_as_malformed(store):
    long = draft(text="# " + "h" * 401 + "\n\n- one\n", ref="https://example.test/2")
    Fake.answers = [Pulled(drafts=[long, draft()], cursor="c1")]
    report = run(store)
    assert (report.saved, report.malformed) == (1, 1)
    assert report.reasons == {"headings past their bound": 1}
    assert store.get_source("feed").cursor == "c1"


def test_a_draft_of_mostly_headings_is_skipped_as_malformed(store):
    outline = draft(
        text="".join(f"## Part {i}\n\n" for i in range(10)) + "One line.\n",  # one passage
        ref="https://example.test/3",
    )
    Fake.answers = [Pulled(drafts=[outline, draft()], cursor="c1")]
    report = run(store)
    assert (report.saved, report.malformed) == (1, 1)
    assert report.reasons == {"headings past their bound": 1}
    assert store.get_source("feed").cursor == "c1"


# S2: a source that is off is not read; the kind's open gets `since`.


def test_the_background_sync_passes_over_a_source_that_is_off_without_logging(store, caplog):
    store.create_project("other")
    store.add_source("second", "fake", {"projects": ["other"]})
    store.disable_source("feed")
    Fake.answers = [Pulled(drafts=[draft("other")], cursor="c")]
    before = store.get_source("feed")

    async def wait(seconds):
        raise asyncio.CancelledError  # one pass, then the server shuts down

    with (
        caplog.at_level(logging.DEBUG, logger=SOURCE_LOGGER),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(
            sync_forever(store, 60, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"}, wait=wait)
        )
    assert caplog.records == []  # off is not a failure
    assert len(Fake.cursors) == 1 and Fake.answers == []  # only "second" was pulled
    assert store.get_source("feed") == before  # no claim, no run recorded
    assert store.list_notes(store.get_project("other").id)


def test_a_run_with_a_plan_says_off_before_it_looks_at_a_hold(store):
    store.disable_source("feed")
    report = asyncio.run(
        run_source(
            store, "feed", http=httpx.AsyncClient(), env={"FAKE_KEY": "x"},
            plan=("free", Plan("Free")),
        )
    )  # fmt: skip
    assert report == SyncReport(off=True) and Fake.cursors == []


def test_a_kinds_open_gets_since_as_an_aware_utc_moment(store, monkeypatch):
    seen = []

    def recording(name, config, **options):
        seen.append(options["since"])
        return opened(name, config, **options)

    monkeypatch.setitem(KINDS, "fake", SourceKind(check=check, open=recording, via="fake"))
    Fake.answers = [Pulled(cursor="c")]
    run(store)
    (since,) = seen
    assert since == datetime.fromisoformat(store.get_source("feed").enabled_at)
    assert since.tzinfo is not None and since.utcoffset() == timedelta(0)


def test_an_off_report_is_not_what_the_store_keeps():
    assert "off" not in SyncReport(off=True).counts()


# Finding 14: a credential the provider sends back, split or encoded


ECHO_KEY = "grn_" + "made-up+" + "Q7v2/Lk9pX4mT="  # never a real secret
ECHO_CONFIG = {"projects": ["api"], "key_env": "ECHO_KEY"}


def _b64(raw: bytes, *, url: bool = False, pad: bool = True) -> str:
    import base64

    said = (base64.urlsafe_b64encode if url else base64.b64encode)(raw).decode()
    return said if pad else said.rstrip("=")


def _wrapped(text: str, width: int = 76) -> str:
    return "\n".join(text[i : i + width] for i in range(0, len(text), width))


def _echoed() -> dict[str, str]:
    """The key as a provider might send it back inside a note's text."""
    key = ECHO_KEY.encode()
    return {
        "split across two lines": ECHO_KEY[:9] + "\n" + ECHO_KEY[9:],
        "spaced out": " ".join(ECHO_KEY),
        "base64 at offset 0": _b64(key),
        "base64 at offset 1": _b64(b"A" + key + b"!"),
        "base64 at offset 2": _b64(b"AB" + key),
        "base64 without padding": _b64(b"Bearer " + key, pad=False),
        "base64url": _b64(b"\xfb\xff" + key + b"\xff", url=True),
        "base64 wrapped at 76": _wrapped(_b64(b"Authorization: Bearer " + key * 3 + b"\xff")),
        "percent-encoded twice": quote(quote("Bearer " + ECHO_KEY, safe=""), safe=""),
        "percent-encoded three times": quote(quote(quote(ECHO_KEY, safe=""), safe=""), safe=""),
        "base64, then percent-encoded": quote(_b64(b"x:" + key), safe=""),
        "hex": key.hex(),
        "HEX": key.hex().upper(),
    }


@pytest.mark.parametrize("form", list(_echoed()))
def test_a_draft_holding_the_key_in_any_form_is_not_written(store, form):
    store.add_source("echo", "fake", ECHO_CONFIG)
    held = NOTE + "\nThe provider said: " + _echoed()[form] + "\n"
    Fake.answers = [Pulled(drafts=[draft(text=held)], cursor="c")]
    env = {"FAKE_KEY": "x", "ECHO_KEY": ECHO_KEY}
    report = asyncio.run(run_source(store, "echo", http=httpx.AsyncClient(), env=env))
    assert (report.saved, report.reasons) == (0, {"held the source's credentials": 1}), form
    assert store.list_notes(store.get_project("api").id) == []


def test_a_long_innocent_text_and_a_short_secret_s_encodings_are_not_held(store):
    """Nothing is held that should not be: a long text of prose, base64 and hex that holds
    no secret is written; a secret shorter than SECRET_ENCODED_MIN is looked for only as it
    is (and percent-decoded), so its base64 or hex, which short words match by chance, is
    not held."""
    import random

    from cairn.sources import SECRET_ENCODED_MIN

    short = "ab12cd"
    assert len(short) < SECRET_ENCODED_MIN <= len(ECHO_KEY)
    store.add_source("echo", "fake", {**ECHO_CONFIG, "short_env": "SHORT"})
    rng = random.Random(14)
    noise = bytes(rng.randrange(256) for _ in range(30_000))
    vocabulary = ["quill", "retries", "charge", "ledger", "batch"]
    words = " ".join(rng.choice(vocabulary) for _ in range(40_000))
    long_text = (
        f"# Retries\n\n{words}\n\n{_wrapped(_b64(noise))}\n\n{noise.hex()}\n\n"
        f"{_b64(short.encode())} {short.encode().hex()} {' '.join(short)}\n"
    )
    Fake.answers = [Pulled(drafts=[draft(text=long_text)], cursor="c")]
    env = {"FAKE_KEY": "x", "ECHO_KEY": ECHO_KEY, "SHORT": short}
    report = asyncio.run(run_source(store, "echo", http=httpx.AsyncClient(), env=env))
    assert (report.saved, report.skipped) == (1, 0)
    plain = draft(text=NOTE + short, ref="https://example.test/2")
    Fake.answers = [Pulled(drafts=[plain], cursor="d")]
    report = asyncio.run(run_source(store, "echo", http=httpx.AsyncClient(), env=env))
    assert report.reasons == {"held the source's credentials": 1}  # as it is: held
