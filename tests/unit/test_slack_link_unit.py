"""Linking a Slack user to a person (cairn.slack.link): the name the page gives the Slack user,
what the page asks and never shows, and what a request that is not one gets. The inner loop
under tests/acceptance/test_k1b_slack_setup.py. Every name, id and value here is made up;
every call to Slack goes to an httpx.MockTransport."""

import html
import logging
from datetime import UTC, datetime
from urllib.parse import parse_qsl

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.slack import link
from cairn.store import Store
from cairn.web.slack import Slack

BASE = "https://cairn.example.test"
HOST = "cairn.example.test"
KEY = bytes(range(32))
ISSUER = "https://sso.example.test"
NOW = datetime(2031, 3, 4, 9, tzinfo=UTC)
SECRET = "unit-signing-" + "0f" * 8
TOKEN = "xoxb-" + "unit-" + "0123456789"
TEAM = "T0FABLE01"
BOT = "U0FABLEBOT"
GUS = "U0GUS0001"


class World:
    def __init__(self, tmp_path) -> None:
        self.store = Store(tmp_path / "cairn.db", clock=lambda: NOW)
        self.users: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict]] = []

        def slack(request: httpx.Request) -> httpx.Response:
            fields = dict(parse_qsl(request.content.decode()))
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            self.calls.append((request.url.path, token, fields))
            user = self.users.get(fields.get("user", ""))
            if user is None:
                return httpx.Response(200, json={"ok": False, "error": "user_not_found"})
            return httpx.Response(200, json={"ok": True, "user": user})

        sign_in = SignIn(
            providers=(GitHub("gh-id", "gh-secret"),),
            http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
            join="personal",
        )
        switch = Slack(
            key=KEY, http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(slack))
        )
        self.app = create_app(self.store, public_url=BASE, sign_in=sign_in, slack=switch)
        made = self.store.sign_in(
            "sso", "sub-gus", "gus@example.test", "Gus", issuer=ISSUER, join="personal"
        )
        self.gus = {"id": made.user.id, "account": made.account_id, "token": made.token}
        self.view = self.store.for_account(made.account_id, made.user.id)
        self.install = self.view.make_slack_install().id
        self.view.ready_slack_install(
            self.install, signing_secret=SECRET, bot_token=TOKEN, team_id=TEAM,
            team_domain="fable-works", bot_user_id=BOT,
        )  # fmt: skip
        self.client = TestClient(self.app, base_url=BASE)
        self.client.cookies.set("cairn_session", made.token, domain=HOST, path="/")

    def offer(self) -> str:
        return self.view.offer_slack_link(self.install, GUS)

    def close(self) -> None:
        self.client.close()
        self.store.close()


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.close()


@pytest.mark.parametrize(
    "user, name",
    [
        ({"real_name": "Gus Reed", "profile": {"display_name": "gus"}, "name": "g"}, "Gus Reed"),
        ({"real_name": "", "profile": {"display_name": "gus", "real_name": "G R"}}, "gus"),
        ({"profile": {"display_name": " ", "real_name": "Gus R"}}, "Gus R"),
        ({"name": "gus.reed"}, "gus.reed"),
        ({"real_name": 7, "profile": "not an object"}, GUS),
        ({}, GUS),
        ({"real_name": "x" * 200}, "x" * 80),
    ],
)
def test_a_slack_user_is_named_as_slack_names_them_or_by_id(user, name):
    assert link.slack_name(user, GUS) == name


def test_the_name_is_asked_of_slack_with_the_installs_bot_token(world):
    world.users[GUS] = {"id": GUS, "real_name": "Gus <b>Reed</b>"}
    page = world.client.get(f"{link.LINK_PATH}?t={world.offer()}")
    assert page.status_code == 200
    assert world.calls == [("/api/users.info", TOKEN, {"user": GUS})]
    assert "Gus &lt;b&gt;Reed&lt;/b&gt;" in page.text and "<b>Reed" not in page.text
    assert TOKEN not in page.text


def test_with_no_bot_token_slack_is_not_asked_and_the_id_is_shown(world):
    value = world.offer()
    world.store.for_account(world.gus["account"]).acting_as("system", None).revoke_slack_bot(
        world.install
    )
    page = world.client.get(f"{link.LINK_PATH}?t={value}")
    assert page.status_code == 200 and GUS in page.text and world.calls == []


def test_the_page_names_the_person_and_the_workspace_and_posts_back_to_itself(world):
    value = world.offer()
    page = world.client.get(f"{link.LINK_PATH}?t={value}")
    text = html.unescape(page.text)
    name = world.store.get_account(world.gus["account"]).name
    assert "Gus" in text and "gus@example.test" in text and name in text
    main = page.text[page.text.index("<main") :]
    form = main[main.index("<form") : main.index(">", main.index("<form"))]
    assert form == '<form method="post"'  # back to this very address
    assert value not in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("query", ["", "?t=", "?t=a&t=b", "?t=" + "x" * 300, "?value=x"])
def test_an_address_that_holds_no_one_value_is_no_page(world, query):
    world.offer()
    unknown = world.client.get("/integrations/nothing/here")
    for answer in (world.client.get(f"{link.LINK_PATH}{query}"),
                   world.client.post(f"{link.LINK_PATH}{query}")):  # fmt: skip
        assert answer.status_code == 404 and answer.content == unknown.content


def test_no_log_line_holds_the_value(world, caplog):
    caplog.set_level(logging.DEBUG)
    value = world.offer()
    world.client.get(f"{link.LINK_PATH}?t={value}")
    world.client.post(f"{link.LINK_PATH}?t={value}", follow_redirects=False)
    world.client.post(f"{link.LINK_PATH}?t={value}")
    for record in caplog.records:
        if record.name.startswith("httpx"):  # the test's own client, saying what it asked
            continue
        assert value not in record.getMessage() + repr(record.__dict__)
    access = [r for r in caplog.records if r.name == "cairn.access" and r.action == "slack_link"]
    assert [(r.status, r.install) for r in access] == [
        (200, world.install),
        (200, world.install),  # the yes shows the code
        (404, None),
    ]


def test_unlink_with_nothing_set_up_is_no_page(tmp_path):
    world = World(tmp_path)
    try:
        world.view.remove_slack_install(world.install)
        unknown = world.client.post("/integrations/nothing/here")
        answer = world.client.post(link.UNLINK_PATH, data={"slack_user": GUS})
        assert answer.status_code == 404 and answer.content == unknown.content
    finally:
        world.close()
