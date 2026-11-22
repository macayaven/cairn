"""The loop (cairn.web.slack.Keeper) asks Slack whether each bot token still works, removes an
install whose token Slack ended GONE_DAYS ago, and survives one bad row. Every call to Slack
goes to an httpx.MockTransport; the clock is the store's. Every name is made up."""

import asyncio
import logging
import threading
from contextlib import closing
from datetime import timedelta

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.store import Store
from cairn.store.hosted import Sealer
from cairn.store.slack import CHECK_EVERY, CHECKS_AT_ONCE, DUE_AT_ONCE, FORGET_AFTER, GONE_AFTER
from cairn.web import slack as web_slack
from cairn.web.slack import Keeper
from support.slack.slack_store import KEY, OTHER_TEAM, TOKEN, a_note, ready, workspaces, world  # noqa: F401
from support.store.store import rows

API = "https://slack.test/api"
BASE = "https://cairn.example.test"


class Slack:
    """auth.test as cairn calls it: every call recorded, every answer chosen here."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.answer: object = httpx.Response(200, json={"ok": True, "user_id": "U0BOT0001"})

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((str(request.url), request.headers["authorization"]))
        if isinstance(self.answer, BaseException):
            raise self.answer
        if self.answer == "hang":
            await asyncio.Event().wait()
        return self.answer


@pytest.fixture
def slack():
    return Slack()


def keeper(world, slack) -> Keeper:  # noqa: F811
    transport = httpx.MockTransport(slack)
    return Keeper(world.store, http=lambda: httpx.AsyncClient(transport=transport), api=API)


def state(world) -> tuple:  # noqa: F811
    found = world.view.get_slack_install()
    (dead,) = rows(world.db, "SELECT dead_checks FROM slack_installs WHERE id = ?", found.id)[0]
    return found.state, found.holds_bot_token, found.bot_gone_at, dead


def ended(code: str) -> httpx.Response:
    return httpx.Response(200, json={"ok": False, "error": code})


def test_each_install_that_holds_a_bot_token_is_asked_with_auth_test(world, slack):  # noqa: F811
    ready(world)
    world.clock.now += CHECK_EVERY
    assert asyncio.run(keeper(world, slack).check()) == 1
    assert slack.calls == [(f"{API}/auth.test", f"Bearer {TOKEN}")]
    assert asyncio.run(keeper(world, slack).check()) == 0  # not again within six hours
    world.clock.now += CHECK_EVERY
    asyncio.run(keeper(world, slack).check())
    assert len(slack.calls) == 2


def test_at_most_twenty_installs_in_one_pass(world, slack):  # noqa: F811
    workspaces(world, CHECKS_AT_ONCE + 2)
    world.clock.now += CHECK_EVERY
    assert asyncio.run(keeper(world, slack).check()) == 20
    assert asyncio.run(keeper(world, slack).check()) == 2


@pytest.mark.parametrize("code", ["invalid_auth", "account_inactive", "token_revoked"])
def test_two_ended_answers_six_hours_apart_switch_it_off_and_delete_the_token(
    world,  # noqa: F811
    slack,
    code,
):
    install = ready(world)
    slack.answer = ended(code)
    world.clock.now += CHECK_EVERY
    asyncio.run(keeper(world, slack).check())
    assert state(world) == ("ready", True, None, 1)
    world.clock.now += CHECK_EVERY - timedelta(seconds=1)
    asyncio.run(keeper(world, slack).check())  # too soon: not asked
    assert len(slack.calls) == 1
    world.clock.now += timedelta(seconds=1)
    asyncio.run(keeper(world, slack).check())
    found = world.view.get_slack_install()
    assert (found.state, found.holds_bot_token) == ("off", False)
    assert found.bot_gone_at is not None and found.needs_bot_token
    assert world.store.slack_inbound(install.id).signing_secret is not None  # kept


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(429, headers={"retry-after": "30"}),
        httpx.Response(500),
        httpx.Response(503),
        httpx.Response(200, text="<html>not json</html>"),
        ended("ratelimited"),
        ended("fatal_error"),
        httpx.ConnectError("no connection"),
        httpx.ReadTimeout("slow"),
        "hang",
    ],
)
def test_anything_else_changes_nothing_but_when_it_was_checked(
    world,  # noqa: F811
    slack,
    answer,
    monkeypatch,
):
    ready(world)
    monkeypatch.setattr(Keeper, "CHECK_WITHIN", 0)  # "hang": the time is up at once
    slack.answer = ended("invalid_auth")
    world.clock.now += CHECK_EVERY
    asyncio.run(keeper(world, slack).check())
    for _ in range(3):
        slack.answer = answer
        world.clock.now += CHECK_EVERY
        asyncio.run(keeper(world, slack).check())
        assert state(world) == ("ready", True, None, 1)
    (checked,) = rows(world.db, "SELECT checked_at FROM slack_installs")[0]
    assert checked == world.clock.now.isoformat(timespec="microseconds")
    assert len(slack.calls) == 4  # each asked once: a 429 is not waited out


def test_a_good_answer_in_between_starts_the_count_again(world, slack):  # noqa: F811
    ready(world)
    for answer in (ended("invalid_auth"), slack.answer, ended("invalid_auth")):
        slack.answer = answer
        world.clock.now += CHECK_EVERY
        asyncio.run(keeper(world, slack).check())
    assert state(world) == ("ready", True, None, 1)


def test_each_check_is_within_five_seconds_in_all():
    assert Keeper.CHECK_WITHIN == 5.0


def test_a_check_that_raises_is_logged_by_its_class_and_the_next_is_checked(
    world,  # noqa: F811
    slack,
    monkeypatch,
    caplog,
):
    workspaces(world, 2)
    world.clock.now += CHECK_EVERY
    real, seen = Store.slack_token_checked, []

    def first_fails(self, check, answer):
        seen.append(check.install_id)
        if len(seen) == 1:
            raise RuntimeError(f"database text {TOKEN}")
        return real(self, check, answer)

    monkeypatch.setattr(Store, "slack_token_checked", first_fails)
    with caplog.at_level(logging.WARNING, logger="cairn.slack"):
        assert asyncio.run(keeper(world, slack).check()) == 1
    assert len(seen) == 2
    assert [r.getMessage() for r in caplog.records] == [
        "slack: a check of a bot token failed: RuntimeError"
    ]
    assert TOKEN not in caplog.text


# Removal after GONE_DAYS, and forgetting


def test_the_loop_removes_an_install_slack_ended_three_days_ago_and_forgets_its_notes(
    world,  # noqa: F811
    slack,
):
    install = ready(world)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    slack.answer = ended("token_revoked")
    for _ in range(2):
        world.clock.now += CHECK_EVERY
        asyncio.run(keeper(world, slack).check())
    loop = keeper(world, slack)
    world.clock.now += GONE_AFTER
    loop.run_once()
    assert world.view.get_slack_install().state == "off"  # three days, not more
    world.clock.now += timedelta(seconds=1)
    loop.run_once()
    assert world.view.get_slack_install().state == "removed"
    world.clock.now += FORGET_AFTER
    assert loop.run_once() == (0, 1)
    assert world.view.get_note(note) is None


def test_a_note_that_cannot_be_forgotten_is_logged_and_skipped(
    world,  # noqa: F811
    monkeypatch,
    caplog,
):
    install = ready(world)
    notes = [a_note(world, f"# N{n}\n\nFact {n}.\n") for n in range(3)]
    for note in notes:
        world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    world.view.remove_slack_install(install.id)
    world.clock.now += FORGET_AFTER
    real = Store.forget_note

    def first_fails(self, note_id, *args, **kwargs):
        if note_id == notes[0]:
            raise sqlite_error()
        return real(self, note_id, *args, **kwargs)

    monkeypatch.setattr(Store, "forget_note", first_fails)
    with caplog.at_level(logging.WARNING, logger="cairn.slack"):
        assert Keeper(world.store).run_once() == (0, 2)
    assert [world.view.get_note(n) is None for n in notes] == [False, True, True]
    assert [r.getMessage() for r in caplog.records] == [
        "slack: a note was not forgotten: OperationalError"
    ]
    assert notes[0] not in caplog.text


def test_restart_with_slack_off_forgets_only_what_is_due(world, monkeypatch):  # noqa: F811
    from cairn.server import create_app

    install = ready(world)
    due, early = a_note(world), a_note(world, "The desk is by the north window.")
    world.view.record_slack_note(install.id, due, how="typed", brought_by=world.owner)
    world.view.remove_slack_install(install.id)
    world.clock.now += timedelta(days=1)
    ready(world, team=OTHER_TEAM)
    world.view.record_slack_note(install.id, early, how="typed", brought_by=world.owner)
    world.view.remove_slack_install(install.id)
    world.clock.now += FORGET_AFTER - timedelta(days=1)

    ran = threading.Event()
    run_once = Keeper.run_once

    def pass_at_start(self):
        try:
            return run_once(self)
        finally:
            ran.set()

    def no_http():
        pytest.fail("the local pass needs no HTTP client")

    monkeypatch.setattr(Keeper, "run_once", pass_at_start)
    with closing(Store(world.db, clock=world.clock)) as restarted:
        app = create_app(restarted, clock=world.clock, source_http=no_http)
        with TestClient(app):
            if app.state.slack_keeper is not None:
                assert ran.wait(5)
        view = restarted.for_account(world.account)
        missing = view.get_note(due) is None
        kept = view.get_note(early) is not None
        assert missing and kept


def test_failed_batch_retries_without_starving_the_next_note(world, monkeypatch):  # noqa: F811
    install = ready(world)
    first = {a_note(world, f"The quill is on desk {n}.") for n in range(DUE_AT_ONCE)}
    for note in first:
        world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    world.view.remove_slack_install(install.id)
    world.clock.now += timedelta(days=1)
    ready(world, team=OTHER_TEAM)
    later = a_note(world, "The quill is on the top shelf.")
    world.view.record_slack_note(install.id, later, how="typed", brought_by=world.owner)
    world.view.remove_slack_install(install.id)
    world.clock.now += FORGET_AFTER
    tried = []
    forget = Store.forget_note

    def fails_for_first_batch(self, note_id):
        tried.append(note_id)
        if note_id in first:
            raise sqlite_error()
        return forget(self, note_id)

    monkeypatch.setattr(Store, "forget_note", fails_for_first_batch)
    loop = Keeper(world.store)
    assert loop.run_once() == (0, 0)
    assert set(tried) == first
    assert loop.run_once() == (0, 1)
    assert world.view.get_note(later) is None
    tried.clear()
    assert loop.run_once() == (0, 0)
    assert set(tried) == first


@pytest.mark.parametrize("path", ["scheduled", "owner", "owner_after_another_removal"])
def test_reconnection_between_selection_and_erasure_cancels_that_forgetting(
    world,  # noqa: F811
    monkeypatch,
    path,
):
    install = ready(world)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    world.view.remove_slack_install(install.id)
    if path == "scheduled":
        world.clock.now += FORGET_AFTER
    forget = Store.forget_note
    reconnected = []

    def reconnect_before_forgetting(self, note_id):
        with closing(Store(world.db, clock=world.clock)) as other:
            other.slack_sealer = Sealer(KEY)
            view = other.for_account(world.account, world.owner)
            ready(world, view=view)
            if path == "owner_after_another_removal":
                # The wall clock has stayed at the first removal's time.
                view.remove_slack_install(install.id)
            reconnected.append(True)
        return forget(self, note_id)

    monkeypatch.setattr(Store, "forget_note", reconnect_before_forgetting)
    if path == "scheduled":
        assert Keeper(world.store).run_once() == (0, 0)
    else:
        assert world.view.forget_slack_notes_now(install.id) == 0
    assert reconnected == [True]
    assert world.view.get_note(note) is not None


def test_automatic_forgetting_checks_the_deadline_again_in_its_write(
    world,  # noqa: F811
    monkeypatch,
):
    install = ready(world)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    world.view.remove_slack_install(install.id)
    world.clock.now += FORGET_AFTER
    forget = Store.forget_note

    def clock_steps_back(self, note_id):
        world.clock.now -= timedelta(microseconds=1)
        return forget(self, note_id)

    monkeypatch.setattr(Store, "forget_note", clock_steps_back)
    assert Keeper(world.store).run_once() == (0, 0)
    assert world.view.get_note(note) is not None


def test_local_forgetting_runs_while_slack_has_not_answered(world, monkeypatch):  # noqa: F811
    from cairn.identity import GitHub
    from cairn.server import SignIn, create_app

    install = ready(world)
    due = a_note(world)
    world.view.record_slack_note(install.id, due, how="typed", brought_by=world.owner)
    world.view.remove_slack_install(install.id)
    ready(world, team=OTHER_TEAM)
    world.clock.now += FORGET_AFTER

    async def exercise():
        asked, forgotten = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        forget = Store.forget_note

        def record_forgetting(self, note_id):
            answer = forget(self, note_id)
            loop.call_soon_threadsafe(forgotten.set)
            return answer

        async def unanswered(request):
            asked.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(Store, "forget_note", record_forgetting)
        sign_in = SignIn(
            providers=(GitHub("gh-id", "gh-secret"),),
            http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(unanswered)),
            join="personal",
        )
        switch = web_slack.Slack(key=KEY, http=sign_in.http, api=API)
        app = create_app(world.store, public_url=BASE, sign_in=sign_in, clock=world.clock,
                         slack=switch)  # fmt: skip
        async with app.router.lifespan_context(app), asyncio.timeout(3):
            await asked.wait()
            await forgotten.wait()
            assert world.view.get_note(due) is None

    asyncio.run(exercise())


def sqlite_error() -> Exception:
    import sqlite3

    return sqlite3.OperationalError("database text: The tide turns.")


def test_the_polling_loop_checks_before_waiting(world, slack):  # noqa: F811
    ready(world)
    world.clock.now += CHECK_EVERY
    done = []

    async def stop(seconds):
        done.append(seconds)
        raise asyncio.CancelledError

    loop = keeper(world, slack)
    loop.wait = stop
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop.check_loop())
    assert len(slack.calls) == 1 and done == [loop.every]


def test_stopped_it_asks_slack_nothing(world, slack):  # noqa: F811
    ready(world)
    world.clock.now += CHECK_EVERY
    loop = keeper(world, slack)
    loop.stop()
    assert asyncio.run(loop.check()) == 0 and slack.calls == []


def test_without_a_client_it_asks_slack_nothing(world):  # noqa: F811
    ready(world)
    world.clock.now += CHECK_EVERY
    assert asyncio.run(Keeper(world.store).check()) == 0
    assert web_slack.Keeper(world.store).run_once() == (0, 0)


def test_the_app_hands_the_loop_the_switchs_client_and_api(world, slack):  # noqa: F811
    from cairn.identity import GitHub
    from cairn.server import SignIn, create_app
    from cairn.web.slack import Slack as Switch

    nowhere = httpx.MockTransport(lambda request: httpx.Response(500))
    sign_in = SignIn(
        providers=(GitHub("gh-id", "gh-secret"),),
        http=lambda: httpx.AsyncClient(transport=nowhere),
        join="personal",
    )
    transport = httpx.MockTransport(slack)
    app = create_app(
        world.store, public_url="https://cairn.example.test", sign_in=sign_in, clock=world.clock,
        slack=Switch(key=bytes(range(32)), http=lambda: httpx.AsyncClient(transport=transport),
                     api=API),
    )  # fmt: skip
    ready(world)
    world.clock.now += CHECK_EVERY
    assert asyncio.run(app.state.slack_keeper.check()) == 1
    assert slack.calls == [(f"{API}/auth.test", f"Bearer {TOKEN}")]


def test_stopped_while_slack_is_asked_nothing_is_written_after(world, slack):  # noqa: F811
    ready(world)
    world.clock.now += CHECK_EVERY
    loop = keeper(world, slack)
    slack.answer = ended("invalid_auth")
    real = slack.__call__

    async def stopping(request):
        loop.stop()  # the app is stopping while Slack answers
        return await real(request)

    loop._http = lambda: httpx.AsyncClient(transport=httpx.MockTransport(stopping))
    assert asyncio.run(loop.check()) == 0
    assert state(world) == ("ready", True, None, 0)


def test_a_check_that_fails_as_a_whole_is_logged_and_the_pass_still_runs(world, caplog):  # noqa: F811
    ready(world)
    world.clock.now += CHECK_EVERY

    def no_client():
        raise RuntimeError(f"no client for {TOKEN}")

    loop = Keeper(world.store, http=no_client, api=API)
    passes = []
    loop.run_once = lambda: passes.append(1)

    async def stop(seconds):
        raise asyncio.CancelledError

    loop.wait = stop
    with caplog.at_level(logging.WARNING, logger="cairn.slack"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(loop.check_loop())
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(loop.loop())
    assert passes == [1]
    assert [r.getMessage() for r in caplog.records] == [
        "slack: the checks of bot tokens failed: RuntimeError"
    ]
    assert TOKEN not in caplog.text
