"""Shared test helpers extracted from test_slack_store_unit."""

from datetime import UTC, datetime

import pytest

from cairn.store import Store
from cairn.store.hosted import Sealer
from cairn.store.slack import (
    SlackInstall,
)

ISSUER = "https://sso.example.test"

NOW = datetime(2031, 3, 4, 9, tzinfo=UTC)

KEY = bytes(range(32))

SECRET = "slack-signing-" + "0f" * 8

TOKEN = "xoxb-" + "unit-" + "0123456789"

TEAM, OTHER_TEAM = "T0FABLE01", "T0QUILL01"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    store.slack_sealer = Sealer(KEY)
    owner = store.sign_in(
        "sso", "sub-ada", "ada@example.test", "Ada", issuer=ISSUER, join="personal"
    )
    view = store.for_account(owner.account_id, owner.user.id)
    member = store.sign_in(
        "sso", "sub-bob", "bob@example.test", "Bob", issuer=ISSUER, join="default"
    )
    invite = view.invite("bob@example.test")
    store.accept_invite(invite.token, member.user.id)
    project = view.create_project("library")

    class World:
        pass

    w = World()
    w.store, w.db, w.clock, w.view, w.project = store, db, clock, view, project
    w.owner, w.member = owner.user.id, member.user.id
    w.account = owner.account_id
    w.member_view = store.for_account(owner.account_id, member.user.id)
    yield w
    store.close()


def ready(w, team=TEAM, secret=SECRET, token=TOKEN, view=None) -> SlackInstall:
    view = view or w.view
    made = view.make_slack_install()
    return view.ready_slack_install(
        made.id, signing_secret=secret, bot_token=token, team_id=team, team_domain="fable",
        bot_user_id="U0BOT0001",
    )  # fmt: skip


def a_note(w, text="# Tides\n\nThe tide turns.\n") -> str:
    note, _ = w.view.add_note(w.project.id, text, title="Tides")
    return note.id


def selects(store: Store, call) -> int:
    """How many SELECT statements `call()` runs on the store's connection."""
    asked: list[str] = []
    store._observe(trace=asked.append)
    try:
        call()
    finally:
        store._observe(trace=None)
    return len([s for s in asked if s.lstrip().upper().startswith(("SELECT", "WITH"))])


def workspaces(w, n: int) -> list:
    """`n` more workspaces, each with its own install ready, for a team of its own."""
    made = []
    for i in range(n):
        signed = w.store.sign_in(
            "sso", f"sub-w{i}", f"w{i}@example.test", f"W{i}", issuer=ISSUER, join="personal"
        )
        view = w.store.for_account(signed.account_id, signed.user.id)
        made.append(ready(w, team=f"T0OTHER{i:02d}", view=view))
    return made
