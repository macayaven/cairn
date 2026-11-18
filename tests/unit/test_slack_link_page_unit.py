"""The two pages of a link in the browser (cairn.slack.link): the page the
one-time address opens, which says what cannot be chosen first and shows no code, and the
yes, which ties the offer to the signed-in person and shows them the code once. Every name,
id and value is made up; every call to Slack goes to an httpx.MockTransport; the code is
chosen by the test through the store's seam (cairn.store.slack._new_code)."""

import html
import logging
import re

import pytest
from test_slack_link_unit import GUS, KEY, SECRET, TEAM, TOKEN, World  # noqa: F401

import cairn.store.slack as store_slack
from cairn.slack import link

CODE = "KM7Q-R2XZ"
NAME = "Gus Reed"
WARNING = "Give this code to no one: whoever types it in Slack acts as you here."


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(store_slack, "_new_code", lambda: CODE.replace("-", ""))
    made = World(tmp_path)
    made.users[GUS] = {"id": GUS, "real_name": NAME}
    yield made
    made.close()


def main(page) -> str:
    """What the page's main says, as read."""
    text = page.text[page.text.index("<main") : page.text.index("</main>")]
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def offers(world) -> list[tuple]:
    return world.store._con.execute(
        "SELECT slack_user_id, user_id, code_digest IS NOT NULL FROM slack_link_offers"
    ).fetchall()


# The page the address opens


def test_the_page_leads_with_the_slack_user_id_and_team_then_the_name_they_set(world):
    page = world.client.get(f"{link.LINK_PATH}?t={world.offer()}")
    said = main(page)
    assert page.status_code == 200
    assert said.index(GUS) < said.index(NAME) and said.index("fable-works.slack.com") < said.index(
        NAME
    )
    assert "a name this Slack user set themself" in said


def test_the_page_says_to_go_on_only_if_you_typed_the_command_and_to_close_it_if_sent(world):
    said = main(world.client.get(f"{link.LINK_PATH}?t={world.offer()}"))
    assert "Go on only if you typed the command in Slack yourself a moment ago." in said
    assert "If someone sent you this address, close this page." in said


def test_the_page_shows_no_code_and_opening_it_changes_nothing(world):
    value = world.offer()
    before = offers(world)
    for _ in range(3):
        page = world.client.get(f"{link.LINK_PATH}?t={value}")
        assert page.status_code == 200
        assert CODE not in page.text and CODE.replace("-", "") not in page.text
        assert value not in page.text
    assert offers(world) == before == [(GUS, None, 0)]


# The yes


def yes(world, value):
    return world.client.post(f"{link.LINK_PATH}?t={value}", follow_redirects=False)


def test_the_yes_shows_the_code_once_privately_and_links_nothing(world):
    value = world.offer()
    page = yes(world, value)
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    said = main(page)
    assert CODE in said and value not in page.text
    assert offers(world) == [(GUS, world.gus["id"], 1)]
    assert world.view.slack_person(world.install, GUS) is None


def test_the_code_page_says_what_to_type_by_when_and_to_give_the_code_to_no_one(world):
    said = main(yes(world, world.offer()))
    assert f"/cairn link {CODE}" in said
    assert "Type it in Slack by" in said
    assert WARNING in said
    page = yes(world, world.offer())  # a second offer, so the page again
    assert page.text.count("<h1>") == 1 and "<script" not in page.text
    assert "style=" not in page.text


def test_the_code_page_says_the_code_goes_with_the_command_not_in_a_message(world):
    """A message to the bot from someone not linked starts a new offer, which ends this one."""
    assert link.AS_THE_COMMAND in main(yes(world, world.offer()))
    assert link.AS_THE_COMMAND == (
        "Type it as the command above: sent to the bot as a message, it starts over."
    )


def test_the_code_page_names_the_installs_own_command(world):
    world.view.remove_slack_install(world.install)
    world.view.make_slack_install("/notes")
    world.view.ready_slack_install(
        world.install, signing_secret=SECRET, bot_token=TOKEN, team_id=TEAM,
        team_domain="fable-works", bot_user_id="U0FABLEBOT",
    )  # fmt: skip
    said = main(yes(world, world.offer()))
    assert f"/notes link {CODE}" in said and "/cairn" not in said


def test_after_the_yes_the_address_is_no_page_and_changes_nothing(world):
    value = world.offer()
    yes(world, value)
    before = offers(world)
    unknown = world.client.get("/integrations/nothing/here")
    for answer in (world.client.get(f"{link.LINK_PATH}?t={value}"), yes(world, value)):
        assert answer.status_code == 404 and answer.content == unknown.content
        assert CODE not in answer.text
    assert offers(world) == before


def test_neither_the_value_nor_the_code_is_in_a_log_line_or_a_cookie(world, caplog):
    caplog.set_level(logging.DEBUG)
    value = world.offer()
    world.client.get(f"{link.LINK_PATH}?t={value}")
    shown = yes(world, value)
    yes(world, value)
    secrets = (value, CODE, CODE.replace("-", ""))
    for record in caplog.records:
        if record.name.startswith("httpx"):  # the test's own client, saying what it asked
            continue
        for secret in secrets:
            assert secret not in record.getMessage() + repr(record.__dict__)
    for secret in secrets:
        assert secret not in str(shown.headers.get_list("set-cookie"))
        assert secret not in str(dict(world.client.cookies))
    access = [r for r in caplog.records if r.name == "cairn.access" and r.action == "slack_link"]
    assert [(r.status, r.outcome) for r in access] == [
        (200, "asked"),
        (200, "code_shown"),
        (404, "no_offer"),
    ]


@pytest.mark.parametrize(
    "headers", [{"origin": "https://elsewhere.example.test"}, {"sec-fetch-site": "cross-site"}]
)
def test_a_yes_from_another_site_is_refused_and_ties_nothing(world, headers):
    value = world.offer()
    answer = world.client.post(f"{link.LINK_PATH}?t={value}", headers=headers)
    assert answer.status_code == 403 and CODE not in answer.text
    assert offers(world) == [(GUS, None, 0)]
