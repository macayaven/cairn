"""The slash command's words (cairn.slack.commands), each handler called as cairn.slack.dispatch
calls it, on a real store, with every call to Slack sent to an httpx.MockTransport. The
helpers here (Bench) are shared with the unit tests of direct messages and the shortcut.
Every name is made up."""

import asyncio
import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
import pytest

from cairn.compiler import provenance_gate
from cairn.memory import Memory
from cairn.slack import blocks, commands, dispatch, people
from cairn.slack.dispatch import Context
from cairn.slack.verify import envelope
from cairn.store import Claim, Store
from cairn.store.hosted import Sealer

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


def test_every_word_has_its_handler_and_none_is_left_unregistered():
    assert {"", "find", "note", "project", "link", "unlink", "help"} <= set(dispatch.COMMANDS)
    for word in ("", "find", "note", "project", "link", "unlink", "help"):
        assert dispatch.COMMANDS[word].__module__ == commands.__name__


def test_help_none_and_blank_are_the_help_of_the_installs_command(bench):
    help_ = blocks.help_text("/cairn")
    for text in ("help", "", "  ", "HELP me"):
        answer = ask(bench, text)
        assert said(answer) == said(help_)
    assert bench.calls == []


def test_an_unlinked_user_gets_the_one_time_address_and_nothing_else(bench):
    for text in ("find jars", "note Row 4.", "project", "project library", "link", "unlink"):
        answer = said(ask(bench, text))
        assert f"{BASE}/integrations/slack/link?t=" in answer
    offered = bench.store._con.execute("SELECT COUNT(*) FROM slack_link_offers").fetchone()
    assert offered == (6,) and bench.notes() == []


def test_a_link_to_someone_no_longer_a_member_is_no_link(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.context = dataclasses.replace(bench.context, memory_for=lambda user_id: None)
    assert "link?t=" in said(ask(bench, "find jars"))


def test_find_runs_the_persons_search_for_five_and_links_each_claim(bench, monkeypatch):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    for n in range(7):
        claim = Claim(id=f"cl_jar{n:04d}", text=f"Jar {n} holds quince.", span_ids=())
        bench.owner.save_page(bench.library.id, f"jar-{n}", f"Jar {n}", "", [claim])
    asked = []
    real = Memory.recall

    def spying(self, query, project=None, limit=8):
        asked.append((query, limit))
        return real(self, query, project, limit)

    monkeypatch.setattr(Memory, "recall", spying)
    answer = said(ask(bench, "find   quince jars "))
    assert asked == [("quince jars", 5)]
    assert answer.count(f"<{BASE}/b/library/p/jar-") == 5


def test_find_with_no_words_is_help_and_nothing_found_is_said(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    assert said(ask(bench, "find")) == said(blocks.help_text("/cairn"))
    assert said(ask(bench, "find zucchini")) == said(blocks.nothing_found("zucchini"))


def test_note_writes_to_the_slack_project_and_records_it_typed_before_answering(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    answer = said(ask(bench, "note Row 4 holds\n\nthe quince jars."))
    ((note_id, project_id, text, via),) = bench.notes()
    assert (project_id, text, via) == (bench.library.id, "Row 4 holds\n\nthe quince jars.", "slack")
    recorded = bench.store._con.execute(
        "SELECT install_id, how, conversation_id, message_time, brought_by FROM slack_notes"
        " WHERE note_id = ?",
        (note_id,),
    ).fetchall()
    assert recorded == [(bench.install.id, "typed", None, None, bench.ada)]
    assert answer.startswith("Filed: ") and f"{BASE}/b/library/p/row-4-holds" in answer


def test_a_note_without_a_project_is_refused_naming_the_command(bench):
    bench.link(ADA_SLACK, bench.ada)
    answer = said(ask(bench, "note Row 4."))
    assert answer == said(blocks.refused(people.NO_PROJECT.format(command="/cairn")))
    assert bench.notes() == []


def test_project_lists_the_projects_the_person_writes_to(bench):
    bench.owner.create_project("cellar", everyone="none")
    bench.owner.create_project("reading-room", everyone="read")
    bench.link(BROOK_SLACK, bench.brook)
    answer = said(ask(bench, "project", BROOK_SLACK))
    assert answer == said(blocks.project_is(None, writable=["library"]))
    bench.link(ADA_SLACK, bench.ada)
    everything = ["cellar", "library", "reading-room"]
    assert said(ask(bench, "project")) == said(blocks.project_is(None, writable=everything))


def test_the_projects_a_person_writes_to_are_the_stores_own_answer(bench, monkeypatch):
    memory = bench.memory_for(bench.brook)
    asked = []

    def answered(self):
        asked.append(self)
        return [bench.library]

    def private(*args, **kwargs):
        raise AssertionError("people.writable reads none of the store's private members")

    monkeypatch.setattr(Store, "writable_projects", answered)
    monkeypatch.setattr(Store, "_level", private)
    assert people.writable(memory) == [bench.library] and asked == [memory.store]


def test_project_name_chooses_one_by_name_in_any_case_and_refuses_the_rest_alike(bench):
    bench.owner.create_project("cellar", everyone="none")
    bench.owner.create_project("reading-room", everyone="read")
    bench.link(BROOK_SLACK, bench.brook)
    chosen = said(ask(bench, "project LIBRARY", BROOK_SLACK))
    assert chosen == said(blocks.project_is("library", writable=["library"]))
    assert bench.owner.slack_person(bench.install.id, BROOK_SLACK).project_id == bench.library.id
    refusal = said(blocks.refused(people.NOT_WRITABLE.format(command="/cairn")))
    for name in ("cellar", "reading-room", "attic"):
        assert said(ask(bench, f"project {name}", BROOK_SLACK)) == refusal
    assert bench.owner.slack_person(bench.install.id, BROOK_SLACK).project_id == bench.library.id


def test_a_project_no_longer_reached_is_shown_as_none_chosen(bench):
    cellar = bench.owner.create_project("cellar", everyone="none")
    bench.link(BROOK_SLACK, bench.brook, cellar)
    assert said(ask(bench, "project", BROOK_SLACK)) == said(
        blocks.project_is(None, writable=["library"])
    )


def test_link_says_who_and_unlink_undoes_it_and_offers_the_address(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    assert said(ask(bench, "link")) == said(blocks.linked("Ada"))
    answer = said(ask(bench, "unlink"))
    assert bench.owner.slack_person(bench.install.id, ADA_SLACK) is None
    assert f"{BASE}/integrations/slack/link?t=" in answer


def test_the_command_is_the_installs(bench):
    install = dataclasses.replace(bench.install, command="/shelf")
    bench.context = dataclasses.replace(bench.context, install=install)
    assert said(ask(bench, "help")) == said(blocks.help_text("/shelf"))


def test_who_asks_shows_ids_in_a_repr_and_nothing_of_their_memory(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    asker = asyncio.run(people.asker(bench.context, ADA_SLACK))
    shown = repr(asker)
    assert ADA_SLACK in shown and "Memory" not in shown and "store" not in shown.lower()


def test_a_typed_text_the_project_held_already_is_not_recorded_as_come_through_slack(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.owner.add_note(bench.library.id, "Row 4 holds the quince jars.", via="web")
    assert said(ask(bench, "note Row 4 holds the quince jars.")).startswith("Filed: ")
    assert len(bench.notes()) == 1
    assert bench.store._con.execute("SELECT COUNT(*) FROM slack_notes").fetchone() == (0,)
