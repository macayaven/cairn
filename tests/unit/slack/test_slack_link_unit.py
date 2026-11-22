"""Linking a Slack user to a person (cairn.slack.link): the name the page gives the Slack user,
what the page asks and never shows, and what a request that is not one gets. The inner loop
under tests/acceptance/test_k1b_slack_setup.py. Every name, id and value here is made up;
every call to Slack goes to an httpx.MockTransport."""

import html
import logging

import pytest

from cairn.slack import link
from support.slack.slack_link import GUS, TOKEN, World


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
