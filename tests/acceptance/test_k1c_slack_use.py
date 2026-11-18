"""K1 acceptance tests: Slack, both ways. Part 3, what people do in Slack.

A person files a Slack message as a note, writes a note, and asks the bot to find something.
Every action is the linked person's, with their reach, and every answer is for them alone.
Part 1 (test_k1_slack.py) is what this stands on; part 2 (test_k1b_slack_setup.py) is how an
install is set up and a Slack user linked. The decisions are in docs/decisions.md; the Slack
facts are in docs/slack-api.md.

Every name here is made up. Every request Slack would send is built and signed in this file;
every call cairn would make to Slack goes to an httpx.MockTransport that records it.

A Slack user who is not linked
- Whatever they ask, with the slash command, the message shortcut or a direct message, they
  are answered privately with a one-time address to link themselves (10 minutes, one use)
  and nothing else happens. `help` alone is answered without it.

The slash command (`/cairn`, or the name the owner gave it)
- `find <words>`: the claims that match, as cairn's search finds them for that person: at
  most 5, each with its page's title linking to the claim on its page, its project and the
  page's version. A project out of the person's reach contributes nothing, not even a count.
  Nothing found is said. The answer is in the HTTP response, marked for the asker alone.
- `note <text>`: a note in the person's Slack project, `via` "slack", recorded as having
  come through the install (typed). The answer links its page.
- `project` says which project that is and which the person can write to; `project <name>`
  changes it to one of those, and refuses any other without saying whether it exists.
- `link`, `unlink`, `help`; no word, or a word that is none of these, is answered as `help`.

A direct message to the bot
- Plain words are a search, answered in that conversation (`chat.postMessage`). `note:` and
  text writes a note. `help` lists what there is.
- Only a person's own new message is acted on: the bot's own messages, another app's, an
  edit, a deletion and any other subtype are acknowledged and nothing is done, so the bot
  never answers itself.

The message shortcut ("File in cairn")
- The message becomes a note in the person's Slack project: its text as Markdown
  (cairn.slack.text), a heading naming who wrote it and when, `source_ref` the message's own
  link, `via` "slack", recorded as filed with its conversation and time.
- Only a message whose author Slack shows to be of the install's team is filed (D7): cairn
  asks Slack who the author is (`users.info`), within 1.5 seconds in all. An author of
  another team, a stranger, a message with no person as its author, an answer that does not
  come in time, an error from Slack, and a rate limit are each refused, privately, with the
  reason; nothing is filed.
- A message is filed once (D9): the second time the answer links the note it already is.
- A message with no text is refused.

Whatever the way
- A write is durable before Slack is answered (D8): when the answer leaves, the note is in
  the store. The private reply that follows a shortcut or a direct message is sent after;
  when sending it fails, the note is there all the same and nothing is retried.
- A note written from Slack takes its turn among the server's compiles (`serve --compiles`),
  as every other note does: while every turn is held it waits, and it is written once one is
  free. It waits at most a second, inside Slack's three: past that nothing is stored and the
  person is told, privately, that nothing was stored and to try again.
- Every refusal is said privately and specifically, never silence: no project chosen; the
  project out of reach or gone; the note guard refused it, with the rule's name as cairn
  says it; the workspace over its cap; the page full; a text that is not a note.
- No answer is ever marked for the whole conversation, and cairn calls nothing at Slack but
  `users.info`, `chat.postMessage` and the reply address a signed request gave: the record
  of calls holds no other.
- Nothing of a message, a search or a note's text is in a log line.
- What a request costs is counted in statements, never time: a search, a typed note and a
  filed message each run the same number of statements in a workspace of 8 pages as in one
  of 48, apart from what cairn's own search and note already cost.
"""

import asyncio
import contextlib
import json
import logging
import re
import threading
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
import pytest
import test_k1_slack as k1
from starlette.testclient import TestClient

from cairn.compiler import provenance_gate
from cairn.identity import GitHub
from cairn.memory import Memory
from cairn.plans import Plan, Plans
from cairn.server import SignIn, create_app
from cairn.slack import people
from cairn.slack.client import SlackClient
from cairn.store import Claim, Store
from cairn.web.slack import Slack

BASE, TEAM, OTHER_TEAM, BOT = k1.BASE, k1.TEAM, k1.OTHER_TEAM, k1.BOT
ADA_SLACK, BROOK_SLACK, CONVERSATION = k1.ADA_SLACK, k1.BROOK_SLACK, k1.CONVERSATION
CEDAR_SLACK, DUNE_SLACK, HELPER_BOT = "U0CEDAR01", "U0DUNE001", "U0HELPER1"
DIRECT = "D0ADA0001"  # Ada's direct conversation with the bot
API = "https://slack.com/api/"
REPLY = "https://hooks.slack.com/actions/T0FABLE01/1/fable"
MESSAGE_TIME = "1900000000.000100"
LINK = f"{BASE}/integrations/slack/link?t="
# Ada's secret-looking value, made here so no file holds it whole.
LOOKS_SECRET = "AKIA" + "FABLEWORKS" + "012345"


def user(slack_id: str, name: str, team: str = TEAM, **marks) -> dict:
    """users.info's `user` (docs/slack-api.md: team_id and is_stranger name the author's team)."""
    return {
        "id": slack_id, "team_id": team, "real_name": name, "is_bot": False,
        "is_stranger": False, "deleted": False,
        "profile": {"real_name": name, "display_name": name.split()[0]}, **marks,
    }  # fmt: skip


class Slack_:
    """Slack, as far as cairn calls it: every request recorded, every answer chosen here."""

    def __init__(self, world: "World") -> None:
        self.world = world
        self.calls: list[tuple[str, dict]] = []
        self.users = {
            CEDAR_SLACK: user(CEDAR_SLACK, "Cedar Quill"),
            DUNE_SLACK: user(DUNE_SLACK, "Dune Far", team=OTHER_TEAM, is_stranger=True),
            HELPER_BOT: user(HELPER_BOT, "Helper", is_bot=True),
        }
        self.answers: dict[str, object] = {}  # what a method or the reply address answers
        self.notes_when_answered: list[int] = []  # notes in the store at each reply or post

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(API):
            where = url.removeprefix(API)
            if request.headers.get("content-type", "").startswith("application/json"):
                body = json.loads(request.content)
            else:
                body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        else:
            where, body = url, json.loads(request.content)
        self.calls.append((where, body))
        if where != "users.info":
            self.notes_when_answered.append(self.world.notes_held())
        answer = self.answers.get(where)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        if where == "users.info":
            found = self.users.get(body.get("user"))
            if found is None:
                return httpx.Response(200, json={"ok": False, "error": "user_not_found"})
            return httpx.Response(200, json={"ok": True, "user": found})
        if where == "chat.postMessage":
            return httpx.Response(200, json={"ok": True, "channel": body["channel"]})
        return httpx.Response(200, text="ok")

    def asked(self, where: str) -> list[dict]:
        return [body for at, body in self.calls if at == where]


class World:
    """A server, switch on, on a database with one workspace, Fable Works, owned by Ada, with
    Brook as a member, an install that is on, and Slack's answers recorded."""

    def __init__(self, tmp_path: Path, plans: Plans | None = None) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.db = tmp_path / "cairn.db"
        self.clock = k1.Clock()
        self.store = Store(self.db, clock=self.clock)
        self.slack = Slack_(self)
        sign_in = SignIn(
            providers=(GitHub("gh-id", "gh-secret"),), http=k1.no_http(), join="personal"
        )
        transport = httpx.MockTransport(self.slack)
        self.app = create_app(
            self.store, public_url=BASE, sign_in=sign_in, clock=self.clock, plans=plans,
            slack=Slack(key=k1.KEY, http=lambda: httpx.AsyncClient(transport=transport)),
        )  # fmt: skip
        self.client = TestClient(self.app, base_url=BASE)
        made = self.store.sign_in(
            "sso", "sub-ada", "ada@example.test", "Ada", issuer=k1.ISSUER, join="personal"
        )
        self.account = made.account_id
        self.ada = k1.Person(
            made.user.id, made.account_id, made.token,
            self.store.for_account(made.account_id, made.user.id),
        )  # fmt: skip
        brook = self.store.sign_in(
            "sso", "sub-brook", "brook@example.test", "Brook", issuer=k1.ISSUER, join="default"
        )
        self.store.accept_invite(self.ada.view.invite("brook@example.test").token, brook.user.id)
        self.brook = k1.Person(
            brook.user.id, self.account, "",
            self.store.for_account(self.account, brook.user.id),
        )  # fmt: skip
        self.library = self.ada.view.create_project("library")
        self.inbox = self.ada.view.create_project("inbox")
        made_install = self.ada.view.make_slack_install()
        self.install = self.ada.view.ready_slack_install(
            made_install.id, signing_secret=k1.SECRET, bot_token=k1.TOKEN, team_id=TEAM,
            team_domain="fable-works", bot_user_id=BOT,
        )  # fmt: skip
        people.AUTHORS.clear()

    def link(self, person: k1.Person, slack_user: str, project=None) -> None:
        self.ada.view.link_slack_user(self.install.id, slack_user, person.id)
        if project is not None:
            self.ada.view.set_slack_project(self.install.id, slack_user, project.id)

    def post(self, kind: str, body: bytes):
        headers = k1.signed(k1.SECRET, body, self.clock.stamp())
        content_type = (
            "application/json" if kind == "events" else "application/x-www-form-urlencoded"
        )
        return self.client.post(
            f"/integrations/slack/{self.install.id}/{kind}",
            content=body,
            headers={"content-type": content_type, **headers},
        )

    def command(self, text: str, user: str = ADA_SLACK):
        return self.post("commands", k1.command(user=user, text=text))

    def shortcut(self, message: dict | None = None, user: str = ADA_SLACK):
        return self.post("actions", shortcut(message, user=user))

    def direct(self, text: str, event_id: str, **fields):
        event = {"channel": DIRECT, "channel_type": "im", "user": ADA_SLACK, "text": text,
                 "ts": "1900000001.000200", **fields}  # fmt: skip
        return self.post("events", k1.event("message", event_id, **event))

    def notes_held(self) -> int:
        return k1.rows(self.db, "SELECT COUNT(*) FROM notes")[0][0]

    def notes(self) -> list[tuple]:
        return k1.rows(
            self.db,
            "SELECT notes.id, projects.name, notes.title, notes.text, notes.source_ref, notes.via"
            " FROM notes JOIN projects ON projects.id = notes.project_id ORDER BY notes.created_at",
        )

    def through_slack(self) -> list[tuple]:
        return k1.rows(
            self.db,
            "SELECT note_id, install_id, how, conversation_id, message_time, brought_by"
            " FROM slack_notes ORDER BY brought_at, note_id",
        )

    def pages(self, project, total: int, word: str = "marmalade", start: int = 0) -> None:
        for n in range(start, start + total):
            claim = Claim(id=f"cl_fable{n:04d}", text=f"The {word} shelf {n} is full.", span_ids=())
            self.ada.view.save_page(project.id, f"shelf-{n}", f"Shelf {n}", "", [claim])


def shortcut(message: dict | None = None, user: str = ADA_SLACK, team: str = TEAM) -> bytes:
    """A message shortcut's payload (docs/slack-api.md, Commands, shortcuts and replies)."""
    if message is None:
        message = {"type": "message", "user": CEDAR_SLACK, "ts": MESSAGE_TIME,
                   "text": "The *marmalade* shelf moves to row 4."}  # fmt: skip
    payload = {
        "type": "message_action", "callback_id": "file_in_cairn",
        "team": {"id": team, "domain": "fable-works"}, "user": {"id": user},
        "channel": {"id": CONVERSATION}, "message": message, "response_url": REPLY,
        "trigger_id": "13345.0044.fable",
    }  # fmt: skip
    return urlencode({"payload": json.dumps(payload)}).encode()


def said(answer: dict) -> str:
    """Everything a message shows: its fallback and its blocks' text."""
    texts = [answer.get("text", "")]
    texts += [block["text"]["text"] for block in answer.get("blocks", []) if "text" in block]
    return "\n".join(texts)


def private(response: httpx.Response) -> dict:
    """A slash command's answer: JSON in the HTTP response, marked for the asker alone."""
    assert response.status_code == 200
    answer = response.json()
    assert answer["response_type"] == "ephemeral"
    return answer


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.store.close()


# A Slack user who is not linked


def offers(world: World) -> list[tuple]:
    return k1.rows(world.db, "SELECT slack_user_id, expires_at, used_at FROM slack_link_offers")


def test_an_unlinked_user_is_answered_only_with_a_one_time_address_whatever_they_ask(world):
    before = world.notes()
    for text in ("find marmalade", "note The shelf moves.", "project", "project library",
                 "unlink", "link"):  # fmt: skip
        assert LINK in said(private(world.command(text)))
    assert world.shortcut().status_code == 200
    assert world.direct("marmalade", "Ev0LINK0001").status_code == 200
    assert world.direct("note: The shelf moves.", "Ev0LINK0002").status_code == 200
    replies = [said(body) for body in world.slack.asked(REPLY)]
    posts = world.slack.asked("chat.postMessage")
    assert len(replies) == 1 and LINK in replies[0]
    assert [post["channel"] for post in posts] == [DIRECT, DIRECT]
    assert all(LINK in said(post) for post in posts)
    assert world.slack.asked("users.info") == []
    assert world.notes() == before and world.through_slack() == []
    assert k1.rows(world.db, "SELECT COUNT(*) FROM slack_people") == [(0,)]
    made = offers(world)
    assert len(made) == 9 and {row[0] for row in made} == {ADA_SLACK}
    in_ten = (k1.START + timedelta(minutes=10)).isoformat()
    assert all(row[1][:19] == in_ten[:19] and row[2] is None for row in made)


def test_the_address_works_once_and_links_who_opens_it(world):
    answer = said(private(world.command("find marmalade")))
    value = re.search(r"link\?t=([A-Za-z0-9_-]+)", answer).group(1)
    world.ada.view.take_slack_offer(value)
    assert world.ada.view.slack_person(world.install.id, ADA_SLACK).user_id == world.ada.id
    with pytest.raises(KeyError):
        world.brook.view.take_slack_offer(value)


def test_help_alone_is_answered_without_it(world):
    before = k1.dump(world.db)
    for text in ("help", "HELP"):
        answer = said(private(world.command(text)))
        assert LINK not in answer and "/cairn find" in answer
    assert world.direct("help", "Ev0HELP0001").status_code == 200
    (post,) = world.slack.asked("chat.postMessage")
    assert LINK not in said(post) and "/cairn find" in said(post)
    after = k1.dump(world.db)
    # the event's id is recorded by the address (part 1); nothing else is written
    assert [line for line in after.splitlines() if line not in before.splitlines()] == [
        line for line in after.splitlines() if line.startswith('INSERT INTO "slack_events"')
    ]


def test_a_person_no_longer_a_member_is_treated_as_not_linked(world):
    world.link(world.brook, BROOK_SLACK, world.library)
    world.ada.view.remove_member(world.brook.id)
    assert LINK in said(private(world.command("find marmalade", user=BROOK_SLACK)))


# The slash command


def test_find_answers_at_most_5_claims_each_linked_with_its_project_and_version(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    world.pages(world.library, 7)
    answer = private(world.command("find marmalade"))
    shown = said(answer)
    links = re.findall(rf"<{re.escape(BASE)}/b/library/p/shelf-\d+#cl_fable\d+\|Shelf \d+>", shown)
    assert len(set(links)) == 5
    assert "Project: library" in shown and "Version: 1" in shown
    assert answer["response_type"] == "ephemeral"


def test_find_is_the_persons_reach_and_a_project_out_of_it_gives_nothing_not_even_a_count(
    world,
):
    cellar = world.ada.view.create_project("cellar", everyone="none")
    world.pages(cellar, 3, word="quince", start=50)
    world.pages(world.library, 1, word="quince", start=60)
    world.link(world.brook, BROOK_SLACK, world.inbox)
    brook = said(private(world.command("find quince", user=BROOK_SLACK)))
    assert "/b/library/p/shelf-60" in brook
    assert "cellar" not in brook and "shelf-5" not in brook
    assert not re.search(r"\b[34]\b", brook.replace("shelf-60", ""))
    world.link(world.ada, ADA_SLACK, world.inbox)
    assert "cellar" in said(private(world.command("find quince")))


def test_nothing_found_is_said(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    world.pages(world.library, 2)
    shown = said(private(world.command("find zucchini")))
    assert "Nothing found" in shown and "zucchini" in shown


def test_note_is_written_to_the_slack_project_via_slack_recorded_as_typed_and_linked(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    answer = said(private(world.command("note The marmalade shelf moves to row 4.")))
    ((note_id, project, _, text, _, via),) = world.notes()
    assert (project, text, via) == ("inbox", "The marmalade shelf moves to row 4.", "slack")
    assert world.through_slack() == [(note_id, world.install.id, "typed", None, None, world.ada.id)]
    (slug,) = [row[0] for row in k1.rows(world.db, "SELECT DISTINCT slug FROM page_versions")]
    assert f"<{BASE}/b/inbox/p/{slug}|" in answer and "Project: inbox" in answer


def test_project_says_which_and_where_the_person_can_write(world):
    world.ada.view.create_project("cellar", everyone="none")
    world.ada.view.create_project("reading-room", everyone="read")
    world.link(world.brook, BROOK_SLACK)
    shown = said(private(world.command("project", user=BROOK_SLACK)))
    assert "No project chosen" in shown
    assert "inbox" in shown and "library" in shown
    assert "cellar" not in shown and "reading-room" not in shown
    world.link(world.brook, BROOK_SLACK, world.library)
    assert "Slack project: library" in said(private(world.command("project", user=BROOK_SLACK)))


def test_project_name_changes_it_to_one_the_person_writes_to_and_refuses_any_other_alike(world):
    world.ada.view.create_project("cellar", everyone="none")
    world.ada.view.create_project("reading-room", everyone="read")
    world.link(world.brook, BROOK_SLACK, world.library)
    shown = said(private(world.command("project inbox", user=BROOK_SLACK)))
    assert "Slack project: inbox" in shown
    person = world.ada.view.slack_person(world.install.id, BROOK_SLACK)
    assert person.project_id == world.inbox.id
    refusals = [
        said(private(world.command(f"project {name}", user=BROOK_SLACK)))
        for name in ("cellar", "reading-room", "attic")
    ]
    assert len(set(r.replace(name, "") for r, name in zip(
        refusals, ("cellar", "reading-room", "attic"), strict=True))) == 1  # fmt: skip
    assert "Cannot do that" in refusals[0]
    person = world.ada.view.slack_person(world.install.id, BROOK_SLACK)
    assert person.project_id == world.inbox.id


def test_link_unlink_and_help(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    assert "Linked to Ada" in said(private(world.command("link")))
    assert offers(world) == []
    unlinked = said(private(world.command("unlink")))
    assert world.ada.view.slack_person(world.install.id, ADA_SLACK) is None
    assert LINK in unlinked
    assert "/cairn note" in said(private(world.command("help")))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "fable",
        "shelve marmalade",
    ],
)
def test_no_word_or_a_word_that_is_none_of_these_is_answered_as_help(world, text):
    world.link(world.ada, ADA_SLACK, world.inbox)
    help_ = said(private(world.command("help")))
    assert said(private(world.command(text))) == help_


def test_the_command_is_named_as_the_owner_named_it(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    assert world.install.command == "/cairn"
    assert "`/cairn find" in said(private(world.command("help")))


# A direct message to the bot


def test_plain_words_in_a_direct_message_are_a_search_answered_in_that_conversation(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    world.pages(world.library, 2)
    response = world.direct("marmalade", "Ev0FIND0001")
    assert response.status_code == 200 and response.content == b""
    (post,) = world.slack.asked("chat.postMessage")
    assert post["channel"] == DIRECT and f"{BASE}/b/library/p/shelf-" in said(post)
    assert world.notes() == []


def test_note_colon_in_a_direct_message_writes_a_note_and_help_lists_what_there_is(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    assert (
        world.direct("note: The quince crate is in the cellar.", "Ev0NOTE0001").status_code == 200
    )
    ((_, project, _, text, _, via),) = world.notes()
    assert (project, text, via) == ("inbox", "The quince crate is in the cellar.", "slack")
    assert world.through_slack()[0][2] == "typed"
    assert world.direct("help", "Ev0HELP0002").status_code == 200
    filed, helped = world.slack.asked("chat.postMessage")
    assert "Filed" in said(filed) and f"{BASE}/b/inbox/p/" in said(filed)
    assert "note: &lt;text&gt;" in said(helped)


@pytest.mark.parametrize(
    "fields",
    [
        {"user": BOT, "bot_id": "B0FABLEBOT"},
        {"user": BOT},
        {"bot_id": "B0HELPER01", "subtype": "bot_message"},
        {"bot_id": "B0HELPER01"},
        {"subtype": "message_changed"},
        {"subtype": "message_deleted"},
        {"subtype": "channel_join"},
        {"channel_type": "channel"},
    ],
)
def test_only_a_persons_own_new_message_is_acted_on(world, fields):
    world.link(world.ada, ADA_SLACK, world.inbox)
    before = k1.dump(world.db)
    response = world.direct("note: The quince crate is in the cellar.", "Ev0SKIP0001", **fields)
    assert response.status_code == 200 and response.content == b""
    assert world.slack.calls == [] and world.notes() == []
    after = k1.dump(world.db)
    assert after.replace(before, "").count('INSERT INTO "slack_notes"') == 0


# The message shortcut ("File in cairn")


def test_the_message_becomes_a_note_in_the_persons_slack_project(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    response = world.shortcut()
    assert response.status_code == 200 and response.content == b""
    ((note_id, project, _, text, source_ref, via),) = world.notes()
    link = f"https://fable-works.slack.com/archives/{CONVERSATION}/p1900000000000100"
    assert (project, source_ref, via) == ("inbox", link, "slack")
    assert text.startswith("## Cedar Quill · 2030-03-17 17:46:40 UTC\n\n")
    assert "The **marmalade** shelf moves to row 4." in text
    assert world.through_slack() == [
        (note_id, world.install.id, "filed", CONVERSATION, MESSAGE_TIME, world.ada.id)
    ]
    (reply,) = world.slack.asked(REPLY)
    assert reply["response_type"] == "ephemeral" and "Filed" in said(reply)
    assert f"{BASE}/b/inbox/p/" in said(reply)


def test_cairn_asks_slack_who_the_author_is_once_within_a_second_and_a_half(world, monkeypatch):
    asked = []
    real = SlackClient.user

    async def spying(self, token, user_id, *, within=None):
        asked.append((user_id, within))
        return await real(self, token, user_id, within=within)

    monkeypatch.setattr(SlackClient, "user", spying)
    world.link(world.ada, ADA_SLACK, world.inbox)
    world.shortcut()
    assert asked == [(CEDAR_SLACK, 1.5)]
    (info,) = world.slack.asked("users.info")
    assert info == {"user": CEDAR_SLACK}


def refused_filing(world: World, message: dict | None = None) -> str:
    response = world.shortcut(message)
    assert response.status_code == 200 and response.content == b""
    (reply,) = world.slack.asked(REPLY)
    assert reply["response_type"] == "ephemeral"
    assert world.notes() == [] and world.through_slack() == []
    return said(reply)


@pytest.mark.parametrize(
    "author, answer, reason",
    [
        (DUNE_SLACK, None, "not of this Slack workspace"),
        (CEDAR_SLACK, {"team_id": OTHER_TEAM}, "not of this Slack workspace"),
        (CEDAR_SLACK, {"is_stranger": True}, "not of this Slack workspace"),
        (HELPER_BOT, None, "no person"),
        (CEDAR_SLACK, {"deleted": True}, "no person"),
        ("U0NOBODY1", None, "user_not_found"),
    ],
)
def test_an_author_slack_does_not_show_to_be_of_the_team_is_refused(world, author, answer, reason):
    world.link(world.ada, ADA_SLACK, world.inbox)
    if answer:
        world.slack.users[author] = {**world.slack.users[author], **answer}
    message = {"type": "message", "user": author, "ts": MESSAGE_TIME, "text": "Row 4."}
    assert reason in refused_filing(world, message)


@pytest.mark.parametrize(
    "message",
    [
        {"type": "message", "ts": MESSAGE_TIME, "text": "Row 4.", "bot_id": "B0HELPER01"},
        {
            "type": "message",
            "ts": MESSAGE_TIME,
            "text": "Row 4.",
            "subtype": "bot_message",
            "bot_id": "B0HELPER01",
            "username": "Helper",
        },
        {"type": "message", "ts": MESSAGE_TIME, "text": "Row 4."},
    ],
)
def test_a_message_with_no_person_as_its_author_is_refused_without_asking(world, message):
    world.link(world.ada, ADA_SLACK, world.inbox)
    assert "no person" in refused_filing(world, message)
    assert world.slack.asked("users.info") == []


@pytest.mark.parametrize(
    "failure, reason",
    [
        (TimeoutError(), "in time"),
        (httpx.Response(429, headers={"retry-after": "30"}), "limit"),
        (httpx.Response(200, json={"ok": False, "error": "fatal_error"}), "fatal_error"),
        (httpx.Response(500), "http_500"),
        (httpx.ConnectError("unreachable"), "http_error"),
    ],
)
def test_no_answer_in_time_an_error_or_a_rate_limit_is_refused_with_the_reason(
    world, failure, reason
):
    world.link(world.ada, ADA_SLACK, world.inbox)
    world.slack.answers["users.info"] = failure
    assert reason in refused_filing(world)
    assert len(world.slack.asked("users.info")) == 1  # nothing is retried


def test_a_message_is_filed_once_and_the_second_time_links_the_note_it_already_is(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    world.link(world.brook, BROOK_SLACK, world.library)
    world.shortcut()
    world.shortcut(user=BROOK_SLACK)
    assert len(world.notes()) == 1 and len(world.through_slack()) == 1
    first, again = world.slack.asked(REPLY)
    assert said(again).startswith("Already filed") and "Project: inbox" in said(again)
    page = re.search(rf"<({re.escape(BASE)}/b/inbox/p/[^|>]+)\|", said(first)).group(1)
    assert page in said(again)
    assert len(world.slack.asked("users.info")) == 1


def test_already_filed_in_a_project_out_of_the_persons_reach_names_nothing_of_it(world):
    cellar = world.ada.view.create_project("cellar", everyone="none")
    world.link(world.ada, ADA_SLACK, cellar)
    world.link(world.brook, BROOK_SLACK, world.library)
    world.shortcut()
    world.shortcut(user=BROOK_SLACK)
    _, again = world.slack.asked(REPLY)
    assert "cellar" not in said(again) and "marmalade" not in said(again)
    assert len(world.notes()) == 1


def test_a_second_filing_of_the_same_authors_message_asks_slack_nothing(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    world.shortcut()
    other = {"type": "message", "user": CEDAR_SLACK, "ts": "1900000050.000300", "text": "Row 5."}
    world.shortcut(other)
    assert len(world.notes()) == 2 and len(world.slack.asked("users.info")) == 1


@pytest.mark.parametrize("text", ["", "   ", None])
def test_a_message_with_no_text_is_refused(world, text):
    world.link(world.ada, ADA_SLACK, world.inbox)
    message = {"type": "message", "user": CEDAR_SLACK, "ts": MESSAGE_TIME}
    if text is not None:
        message["text"] = text
    assert "no text" in refused_filing(world, message)


# Whatever the way


def test_a_write_is_durable_before_slack_is_answered_and_the_reply_follows(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    world.command("note The marmalade shelf moves to row 4.")
    assert world.notes_held() == 1  # the answer has left: the note is stored
    world.shortcut()
    world.direct("note: The quince crate is in the cellar.", "Ev0DURA0001")
    assert world.slack.notes_when_answered == [2, 3]


class Held:
    """Every one of the app's turns of compiles, held from outside its event loop until
    `let_go`: what a server busy compiling other notes looks like to Slack's note."""

    def __init__(self, world: World) -> None:
        self.turns = world.app.state.compiles
        self.release = threading.Event()
        taken = threading.Event()

        async def holding() -> None:
            async with contextlib.AsyncExitStack() as stack:
                for _ in range(self.turns.n):
                    await stack.enter_async_context(self.turns.turn())
                taken.set()
                await asyncio.to_thread(self.release.wait)

        self.holder = threading.Thread(target=lambda: asyncio.run(holding()))
        self.holder.start()
        assert taken.wait(10) and self.turns.held == self.turns.n

    def let_go(self) -> None:
        self.release.set()
        self.holder.join(10)


def looked(done, what: str) -> None:
    """Wait, a bounded time, for `done()`: a look every few milliseconds, never a sleep that
    decides the outcome."""
    pause = threading.Event()
    for _ in range(2000):
        if done():
            return
        pause.wait(0.005)
    raise AssertionError(f"waited 10 s for {what}")


def test_a_note_from_slack_waits_for_the_servers_turn_and_is_written_once_one_is_free(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    held = Held(world)
    answers = []
    sending = threading.Thread(
        target=lambda: answers.append(world.command("note The marmalade shelf moves to row 4."))
    )
    sending.start()
    try:
        looked(lambda: held.turns.waiting == 1, "the note waiting for a turn")
        assert world.notes_held() == 0 and answers == []
    finally:
        held.let_go()
        sending.join(10)
    assert said(private(answers[0])).startswith("Filed: ") and world.notes_held() == 1
    assert (held.turns.held, held.turns.waiting) == (0, 0)


def test_a_note_from_slack_that_finds_no_turn_in_time_is_not_stored_and_is_said(world, monkeypatch):
    world.link(world.ada, ADA_SLACK, world.inbox)
    monkeypatch.setattr(people, "TURN_WITHIN", 0)  # past the time at once, without sleeping
    held = Held(world)
    try:
        told = said(private(world.command("note The marmalade shelf moves to row 4.")))
        assert people.BUSY in told and world.notes_held() == 0 and world.through_slack() == []
        assert (held.turns.held, held.turns.waiting) == (held.turns.n, 0)
    finally:
        held.let_go()
    assert said(private(world.command("note The marmalade shelf moves to row 4.")))
    assert world.notes_held() == 1


@pytest.mark.parametrize("way", ["typed", "filed"])
def test_a_note_that_is_stored_and_then_not_filed_is_still_recorded_as_from_slack(
    world, monkeypatch, caplog, way
):
    """D10 forgets what came through an install by its record. A note is stored before it is
    compiled, so the record is written when it is stored: a compile that then fails leaves a
    note that removal still finds, and the person is told it is stored."""
    world.link(world.ada, ADA_SLACK, world.inbox)

    def refuses(*args, **kwargs):
        raise RuntimeError("the disk refused The marmalade shelf")

    monkeypatch.setattr("cairn.memory.compile_note", refuses)
    caplog.set_level(logging.DEBUG)
    if way == "typed":
        told = said(private(world.command("note The marmalade shelf moves to row 4.")))
        expected = ("typed", None, None)
    else:
        assert world.shortcut().status_code == 200
        (reply,) = world.slack.asked(REPLY)
        told = said(reply)
        expected = ("filed", CONVERSATION, MESSAGE_TIME)
    ((note_id, *_),) = world.notes()
    assert world.through_slack() == [(note_id, world.install.id, *expected, world.ada.id)]
    assert "stored" in told and "marmalade" not in told
    assert "marmalade" not in caplog.text and "RuntimeError" in caplog.text


@pytest.mark.parametrize("where", [REPLY, "chat.postMessage"])
def test_when_the_reply_fails_the_note_is_there_and_nothing_is_retried(world, where, caplog):
    world.link(world.ada, ADA_SLACK, world.inbox)
    world.slack.answers[where] = httpx.Response(429, headers={"retry-after": "1"})
    if where == REPLY:
        response = world.shortcut()
    else:
        response = world.direct("note: The quince crate is in the cellar.", "Ev0FAIL0001")
    assert response.status_code == 200
    assert len(world.notes()) == 1 and len(world.through_slack()) == 1
    assert len(world.slack.asked(where)) == 1


def every_way(world: World, note: str) -> list[str]:
    """`note` written by the slash command, a direct message and the shortcut: each answer."""
    answers = [said(private(world.command(f"note {note}")))]
    world.direct(f"note: {note}", "Ev0WAYS0001")
    message = {"type": "message", "user": CEDAR_SLACK, "ts": MESSAGE_TIME, "text": note}
    world.shortcut(message)
    answers += [said(body) for body in world.slack.asked("chat.postMessage")]
    answers += [said(body) for body in world.slack.asked(REPLY)]
    assert len(answers) == 3
    return answers


def test_no_project_chosen_is_refused_privately_every_way(world):
    world.link(world.ada, ADA_SLACK)
    for answer in every_way(world, "The marmalade shelf moves."):
        assert "No project is chosen" in answer and "/cairn project" in answer
    assert world.notes() == [] and world.slack.asked("users.info") == []


def test_a_project_out_of_reach_read_only_or_gone_is_refused(world):
    cellar = world.ada.view.create_project("cellar", everyone="none")
    reading = world.ada.view.create_project("reading-room", everyone="read")
    attic = world.ada.view.create_project("attic")
    for project, reason in ((cellar, "out of your reach"), (reading, "read-only"),
                            (attic, "No project is chosen")):  # fmt: skip
        world.link(world.brook, BROOK_SLACK, project)
        if project is attic:
            world.ada.view.delete_project(attic.id)
        answer = said(private(world.command("note The shelf moves.", user=BROOK_SLACK)))
        assert reason in answer and "cellar" not in answer
    assert world.notes() == []


def test_the_note_guard_refusal_names_the_rule_as_cairn_says_it(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    for answer in every_way(world, f"The cellar key is {LOOKS_SECRET} today."):
        assert "aws_access_key" in answer and "nothing of it was stored" in answer
        assert LOOKS_SECRET not in answer
    assert world.notes() == []


def test_the_workspace_over_its_cap_is_refused(tmp_path):
    world = World(tmp_path, plans=Plans({"free": Plan("Free", notes_per_month=1)}, "free"))
    try:
        world.link(world.ada, ADA_SLACK, world.inbox)
        private(world.command("note The marmalade shelf moves."))
        answer = said(private(world.command("note The quince crate moves.")))
        assert "Nothing was stored" in answer and "notes" in answer
        assert len(world.notes()) == 1
        shown = said(private(world.command("project library")))  # on a view the plan limits
        assert "Slack project: library" in shown and "inbox" in shown
    finally:
        world.store.close()


def test_a_full_page_is_refused(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    many = "\n\n".join(f"Shelf {n} holds jars." for n in range(2_001))
    answer = said(private(world.command(f"note {many}")))
    assert "Nothing was stored" in answer and "2,000" in answer
    assert world.notes() == []


@pytest.mark.parametrize("text", ["note", "note    ", "note # " + "h" * 500 + "\n\nRow 4."])
def test_a_text_that_is_not_a_note_is_refused(world, text):
    world.link(world.ada, ADA_SLACK, world.inbox)
    answer = said(private(world.command(text)))
    assert answer.startswith("Cannot do that") and "not a note" in answer
    assert world.notes() == []


def test_no_answer_is_for_the_whole_conversation_and_slack_is_called_for_three_things(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    world.pages(world.library, 2)
    answers = [world.command(text).json() for text in
               ("find marmalade", "note Row 4.", "project", "help", "link", "")]  # fmt: skip
    world.shortcut()
    world.direct("marmalade", "Ev0THRE0001")
    world.direct("note: Row 5.", "Ev0THRE0002")
    sent = answers + [body for _, body in world.slack.calls]
    assert "in_channel" not in json.dumps(sent)
    assert all(answer["response_type"] == "ephemeral" for answer in answers)
    assert {where for where, _ in world.slack.calls} == {"users.info", "chat.postMessage", REPLY}


def test_nothing_of_a_message_a_search_or_a_note_is_in_a_log_line(world, caplog):
    caplog.set_level(logging.DEBUG)
    world.link(world.ada, ADA_SLACK, world.inbox)
    world.pages(world.library, 2)
    world.command("find marmalade")
    world.command("note The quince crate is under the stairs.")
    world.command(f"note The cellar key is {LOOKS_SECRET} today.")
    world.direct("persimmon", "Ev0LOGS0001")
    world.direct("note: The fig tree leans east.", "Ev0LOGS0002")
    world.shortcut()
    world.slack.answers[REPLY] = httpx.Response(500)
    world.shortcut({"type": "message", "user": CEDAR_SLACK, "ts": "1900000060.000100",
                    "text": "The walnut barrel leaks."})  # fmt: skip
    prompt = said(private(world.command("find hazelnut", user=BROOK_SLACK)))
    value = re.search(r"link\?t=([A-Za-z0-9_-]+)", prompt).group(1)

    def logged(records) -> str:
        return "\n".join(f"{r.getMessage()} {sorted(vars(r).items())}" for r in records)

    assert caplog.records
    for word in ("marmalade", "quince", LOOKS_SECRET, "persimmon", "fig tree", "walnut",
                 "Cedar", "hazelnut", value, k1.TOKEN):  # fmt: skip
        assert word not in logged(caplog.records)
    # httpx's own INFO line names each address it posts to; no line of cairn's names a reply's
    assert REPLY not in logged(r for r in caplog.records if r.name != "httpx")


def counted(world: World, act) -> int:
    asked: list[str] = []
    world.store._con.set_trace_callback(asked.append)
    try:
        act()
    finally:
        world.store._con.set_trace_callback(None)
    return len(asked)


def test_a_request_costs_the_same_statements_with_8_pages_as_with_48(world):
    world.link(world.ada, ADA_SLACK, world.inbox)
    memory = Memory(
        world.store.for_account(world.account, world.ada.id).acting_as("session", "Ada"),
        provenance_gate,
    )
    costs = []
    for total, start in ((8, 0), (40, 8)):
        world.pages(world.library, total, start=start)
        n = start
        find = counted(world, lambda: world.command("find marmalade"))
        search = counted(world, lambda: memory.recall("marmalade", limit=5))
        typed = counted(world, lambda n=n: world.command(f"note Row {n} holds pears."))
        note = counted(world, lambda n=n: memory.note("inbox", f"Row {n} holds plums."))
        message = {"type": "message", "user": CEDAR_SLACK, "ts": f"19000001{n:02d}.000100",
                   "text": f"Row {n} holds figs."}  # fmt: skip
        people.AUTHORS.clear()  # Slack is asked, and the bot's token read, each time
        filed = counted(world, lambda message=message: world.shortcut(message))
        again = counted(world, lambda n=n: memory.note("inbox", f"Row {n} holds dates."))
        costs.append((find - search, typed - note, filed - again))
    assert costs[0] == costs[1]
