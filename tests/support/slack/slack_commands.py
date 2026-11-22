"""Shared test helpers extracted from test_slack_commands_unit."""

import asyncio
import importlib
import json
import pkgutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
import pytest

import cairn.slack
from cairn.compiler import provenance_gate
from cairn.memory import Memory
from cairn.slack import dispatch, people
from cairn.slack.dispatch import Context
from cairn.slack.verify import envelope
from cairn.store import Store
from cairn.store.hosted import Sealer

# A handler is heard once its module has been imported. The product imports every module of
# cairn.slack when it makes Slack's addresses (cairn.web.slack); a bench hands envelopes to the
# dispatcher itself, so it imports them here, the same way. Without this a test file hears its
# handlers only if another file that imports them ran first in the same process.
for _module in pkgutil.iter_modules(cairn.slack.__path__):
    importlib.import_module(f"{cairn.slack.__name__}.{_module.name}")

BASE = "https://cairn.example.test"

KEY = bytes(range(32))

TEAM = "T0FABLE01"

ADA_SLACK, BROOK_SLACK, BOT = "U0ADA0001", "U0BROOK01", "U0FABLEBOT"

REPLY = "https://hooks.slack.com/actions/T0FABLE01/1/fable"

TOKEN = "xoxb-" + "1111-2222-" + "FableBotToken0123"

START = datetime(2031, 3, 4, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class Bench:
    """A workspace, Fable Works (Ada owns it, Brook is a member), its install ready, and what
    a handler is handed (`context`), with every call to Slack recorded in `calls`."""

    def __init__(self, tmp_path: Path) -> None:
        self.clock = Clock()
        self.store = Store(tmp_path / "cairn.db", clock=self.clock)
        self.store.slack_sealer = Sealer(KEY)
        ada = self.store.sign_in(
            "sso", "sub-ada", "ada@example.test", "Ada", issuer="https://sso.example.test",
            join="personal",
        )  # fmt: skip
        self.account, self.ada = ada.account_id, ada.user.id
        self.owner = self.store.for_account(self.account, self.ada)
        brook = self.store.sign_in(
            "sso", "sub-brook", "brook@example.test", "Brook", issuer="https://sso.example.test",
            join="default",
        )  # fmt: skip
        self.store.accept_invite(self.owner.invite("brook@example.test").token, brook.user.id)
        self.brook = brook.user.id
        self.library = self.owner.create_project("library")
        made = self.owner.make_slack_install()
        self.install = self.owner.ready_slack_install(
            made.id, signing_secret="8f" + "e1c0ffee" * 3, bot_token=TOKEN, team_id=TEAM,
            team_domain="fable-works", bot_user_id=BOT,
        )  # fmt: skip
        self.view = self.store.for_account(self.account).acting_as("system", None)
        self.calls: list[tuple[str, dict]] = []
        self.answers: dict[str, object] = {}
        self.users: dict[str, dict] = {}
        self.context = Context(
            install=self.install, view=self.view, memory_for=self.memory_for,
            http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.slack)),
            public_url=BASE, now=self.clock,
        )  # fmt: skip
        people.AUTHORS.clear()

    def memory_for(self, user_id: str) -> Memory | None:
        if user_id not in (self.ada, self.brook):
            return None
        view = self.store.for_account(self.account, user_id)
        return Memory(view.acting_as("session", "Someone"), provenance_gate, view.get_user(user_id))

    def slack(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://slack.com/api/"):
            where = url.rsplit("/", 1)[1]
            if request.headers.get("content-type", "").startswith("application/json"):
                body = json.loads(request.content)
            else:
                body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        else:
            where, body = url, json.loads(request.content)
        self.calls.append((where, body))
        answer = self.answers.get(where)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        if where == "users.info":
            found = self.users.get(body["user"])
            if found is None:
                return httpx.Response(200, json={"ok": False, "error": "user_not_found"})
            return httpx.Response(200, json={"ok": True, "user": found})
        return httpx.Response(200, json={"ok": True})

    def link(self, slack_user: str, user_id: str, project=None) -> None:
        self.owner.link_slack_user(self.install.id, slack_user, user_id)
        if project is not None:
            self.owner.set_slack_project(self.install.id, slack_user, project.id)

    def envelope(self, kind: str, body: bytes):
        return envelope(kind, self.install, body, {})

    def notes(self) -> list[tuple]:
        return self.store._con.execute(
            "SELECT id, project_id, text, via FROM notes ORDER BY created_at"
        ).fetchall()


def command_body(text: str, user: str = ADA_SLACK) -> bytes:
    return urlencode(
        {
            "team_id": TEAM, "team_domain": "fable-works", "channel_id": "C0LIBRARY1",
            "user_id": user, "command": "/cairn", "text": text,
            "response_url": "https://hooks.slack.com/commands/T0FABLE01/1/fable",
            "trigger_id": "13345.0042.fable",
        }
    ).encode()  # fmt: skip


def said(message: dict) -> str:
    return "\n".join(
        [message.get("text", "")]
        + [block["text"]["text"] for block in message.get("blocks", []) if "text" in block]
    )


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    yield made
    made.store.close()


def ask(bench: Bench, text: str, user: str = ADA_SLACK) -> dict:
    """The answer to `/cairn <text>`, as dispatch gives it: JSON, marked for the asker."""
    found = bench.envelope("commands", command_body(text, user))
    response = asyncio.run(dispatch.dispatch(found, bench.context))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.background is None  # a command's answer is all in the response
    answer = json.loads(response.body)
    assert answer["response_type"] == "ephemeral"
    return answer
