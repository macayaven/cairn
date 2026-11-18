"""The browser's side of a Slack install (cairn.slack.setup): what Slack's answer to auth.test
must hold, how the other workspaces' installs are asked about, how an outcome is said once,
and the block on the Sources page. The inner loop under
tests/acceptance/test_k1b_slack_setup.py. Every name, id and secret here is made up; every
call to Slack goes to an httpx.MockTransport."""

import html
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.slack import setup
from cairn.store import Store
from cairn.store.hosted import Sealer
from cairn.web.slack import Slack
from cairn.web.templating import templates

BASE = "https://cairn.example.test"
HOST = "cairn.example.test"
KEY = bytes(range(32))
ISSUER = "https://sso.example.test"
NOW = datetime(2031, 3, 4, 9, tzinfo=UTC)
SECRET = "unit-signing-" + "0f" * 8
TOKEN = "xoxb-" + "unit-" + "0123456789"
TEAM = "T0FABLE01"
BOT = "U0FABLEBOT"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def answered(**fields) -> dict:
    """auth.test's answer for a bot token of Fable Works, with `fields` changed."""
    answer = {
        "ok": True, "url": "https://fable-works.slack.com/", "team": "Fable", "user": "bot",
        "team_id": TEAM, "user_id": BOT, "bot_id": "B0FABLE01",
    }  # fmt: skip
    return {k: v for k, v in {**answer, **fields}.items() if v is not None}


class World:
    def __init__(self, tmp_path, answer: dict | None = None) -> None:
        self.clock = Clock()
        self.db = tmp_path / "cairn.db"
        self.store = Store(self.db, clock=self.clock)
        self.answer = answer or answered()
        self.calls: list[str] = []

        def slack(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            return httpx.Response(200, json=self.answer)

        sign_in = SignIn(
            providers=(GitHub("gh-id", "gh-secret"),),
            http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
            join="personal",
        )
        switch = Slack(
            key=KEY, http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(slack))
        )
        self.app = create_app(
            self.store, public_url=BASE, sign_in=sign_in, clock=self.clock, slack=switch
        )
        self.ada = self.signed("Ada", "personal")
        self.bob = self.signed("Bob", "personal")
        self.clients: list[TestClient] = []

    def signed(self, name: str, join: str) -> dict:
        made = self.store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER, join=join
        )
        return {"id": made.user.id, "account": made.account_id, "token": made.token}

    def view(self, person: dict) -> Store:
        return self.store.for_account(person["account"], person["id"])

    def browser(self, person: dict) -> TestClient:
        client = TestClient(self.app, base_url=BASE)
        client.cookies.set("cairn_session", person["token"], domain=HOST, path="/")
        self.clients.append(client)
        return client

    def close(self) -> None:
        for client in self.clients:
            client.close()
        self.store.close()


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.close()


def page_after(browser: TestClient, response: httpx.Response) -> str:
    assert response.status_code == 303 and response.headers["location"] == "/sources#slack"
    return html.unescape(browser.get("/sources").text)


# What Slack's answer to auth.test must hold


@pytest.mark.parametrize(
    "url, domain",
    [
        ("https://fable-works.slack.com/", "fable-works"),
        ("https://fable-works.slack.com", "fable-works"),
        ("https://fable.slack-gov.com/", "fable"),
        ("https://fable-works.example.test/", None),  # not Slack's
        ("http://fable-works.slack.com/", None),  # not https
        ("https://slack.com/", None),  # no workspace in it
        ("https://a.b.slack.com/", None),  # more than one label before Slack's own
        ("https://fable works.slack.com/", None),
        (None, None),
        (42, None),
    ],
)
def test_the_workspaces_domain_is_the_first_label_of_slacks_address_for_it(url, domain):
    assert setup.team_domain(url) == domain


def test_an_answer_names_the_team_its_domain_and_the_bot():
    found = setup.identity(answered(enterprise_id="E0FABLE01"))
    assert found == setup.Identity(TEAM, "fable-works", BOT, "E0FABLE01")
    assert setup.identity(answered()).enterprise_id is None


@pytest.mark.parametrize(
    "fields",
    [
        {"bot_id": None},  # a person's token: no bot
        {"team_id": None},
        {"team_id": "T 0"},
        {"user_id": None},
        {"url": "https://elsewhere.example.test/"},
        {"enterprise_id": "E 0"},
    ],
)
def test_an_answer_that_lacks_any_of_it_names_nothing(fields):
    assert setup.identity(answered(**fields)) is None


@pytest.mark.parametrize(
    "fields, said",
    [({"bot_id": None}, "not_a_bot"), ({"url": "https://elsewhere.example.test/"}, "unclear")],
)
def test_such_an_answer_is_refused_and_nothing_is_kept(tmp_path, fields, said):
    world = World(tmp_path, answered(**fields))
    try:
        ada = world.browser(world.ada)
        ada.post(setup.SETUP_PATH, follow_redirects=False)
        sent = ada.post(
            setup.SECRETS_PATH, data={"signing_secret": SECRET, "bot_token": TOKEN},
            follow_redirects=False,
        )  # fmt: skip
        assert setup.REFUSALS[said] in page_after(ada, sent)
        found = world.view(world.ada).get_slack_install()
        assert found.state == "pending" and not found.holds_secrets
    finally:
        world.close()


@pytest.mark.parametrize("value", ["has a space", "x" * 513, "tab\there"])
def test_a_secret_not_of_slacks_shape_is_refused_before_slack_is_asked(world, value):
    ada = world.browser(world.ada)
    ada.post(setup.SETUP_PATH, follow_redirects=False)
    for form in ({"signing_secret": value, "bot_token": TOKEN},
                 {"signing_secret": SECRET, "bot_token": value}):  # fmt: skip
        sent = ada.post(setup.SECRETS_PATH, data=form, follow_redirects=False)
        page = page_after(ada, sent)
        assert setup.REFUSALS["blank"] in page and value not in page
    assert world.calls == []


# The other workspaces' installs, and the kept signing secret


def ready(view: Store, team: str = TEAM) -> str:
    made = view.make_slack_install()
    view.ready_slack_install(
        made.id, signing_secret=SECRET, bot_token=TOKEN, team_id=team,
        team_domain="fable-works", bot_user_id=BOT,
    )  # fmt: skip
    return made.id


def test_a_team_is_held_only_by_another_workspaces_install_that_is_not_removed(world):
    office = setup.office_of(world.store)
    ada, bob = world.view(world.ada), world.view(world.bob)
    assert not office.team_held(TEAM, world.ada["account"])
    bob.make_slack_install()
    assert not office.team_held(TEAM, world.ada["account"])  # pending: no team yet
    install = ready(bob)
    assert office.team_held(TEAM, world.ada["account"])
    assert not office.team_held(TEAM, world.bob["account"])  # its own
    assert not office.team_held("T0OTHER01", world.ada["account"])
    bob.switch_slack_install(install, on=False)
    assert office.team_held(TEAM, world.ada["account"])  # off is still held
    bob.remove_slack_install(install)
    assert not office.team_held(TEAM, world.ada["account"])
    ready(ada)
    assert office.team_held(TEAM, world.bob["account"])


def test_the_kept_signing_secret_is_given_only_for_its_own_workspace(world):
    office = setup.office_of(world.store)
    install = ready(world.view(world.ada))
    assert office.kept_secret(install, world.ada["account"]) == SECRET
    assert office.kept_secret(install, world.bob["account"]) is None
    assert office.kept_secret("sl_000000000000", world.ada["account"]) is None
    assert SECRET not in repr(office)


def test_a_revoked_token_whose_kept_secret_does_not_open_asks_for_both(world, monkeypatch):
    install = ready(world.view(world.ada))
    world.store.for_account(world.ada["account"]).acting_as("system", None).revoke_slack_bot(
        install
    )
    world.store.slack_sealer = Sealer(bytes(range(1, 33)))  # the key changed since
    ada = world.browser(world.ada)
    sent = ada.post(setup.SECRETS_PATH, data={"bot_token": TOKEN}, follow_redirects=False)
    assert setup.REFUSALS["blank"] in page_after(ada, sent)
    assert world.calls == []


# An outcome, said once to its person


def test_an_outcome_is_said_once_and_only_to_its_person(world):
    ada = world.browser(world.ada)
    made = ada.post(setup.SETUP_PATH, follow_redirects=False)
    cookie = [v for v in made.headers.get_list("set-cookie") if v.startswith(setup.SAID_COOKIE)]
    assert len(cookie) == 1
    for flag in ("path=/sources", "httponly", "samesite=lax", "secure"):
        assert flag in cookie[0].lower()
    sealed = cookie[0].split(";", 1)[0].split("=", 1)[1]
    first = ada.get("/sources")
    assert setup.SAID["made"] in html.unescape(first.text)
    assert first.headers["cache-control"] == "no-store"
    assert setup.SAID["made"] not in html.unescape(ada.get("/sources").text)
    bob = world.browser(world.bob)
    bob.cookies.set(setup.SAID_COOKIE, sealed, domain=HOST, path="/sources")
    assert setup.SAID["made"] not in html.unescape(bob.get("/sources").text)
    assert setup.SAID["made"] not in html.unescape(ada.get("/sources?said=made").text)


@pytest.mark.parametrize("which", ["as_written", "expired", "workspace", "garbage", "purpose"])
def test_an_outcome_that_is_not_this_persons_now_is_not_said_and_is_cleared(world, which):
    ada = world.browser(world.ada)
    ada.post(setup.SETUP_PATH, follow_redirects=False)
    ada.get("/sources")  # the set-up's own outcome, said and cleared
    outcome = {
        "said": "made", "person": world.ada["id"],
        "workspace": "another" if which == "workspace" else world.ada["account"],
        "exp": (NOW + timedelta(seconds=-1 if which == "expired" else 60)).timestamp(),
    }  # fmt: skip
    purpose = b"another purpose" if which == "purpose" else setup.OUTCOME
    sealed = Sealer(KEY).seal(json.dumps(outcome), purpose)
    if which == "garbage":
        sealed = "not-a-sealed-value"
    ada.cookies.set(setup.SAID_COOKIE, sealed, domain=HOST, path="/sources")
    page = ada.get("/sources")
    assert (setup.SAID["made"] in html.unescape(page.text)) == (which == "as_written")
    cleared = [v for v in page.headers.get_list("set-cookie") if v.startswith(setup.SAID_COOKIE)]
    assert len(cleared) == 1 and "max-age=0" in cleared[0].lower()


# The block


def test_switched_off_the_include_adds_not_one_byte_to_the_page():
    assert templates.get_template("_slack.html").render() == ""
    around = templates.from_string('a\n{% include "_slack.html" %}\nb')
    assert around.render() == "a\nb"


def test_set_up_again_after_a_removal_shows_the_same_install_pending(world):
    ada = world.browser(world.ada)
    install = ready(world.view(world.ada))
    world.view(world.ada).remove_slack_install(install)
    page = page_after(ada, ada.post(setup.SETUP_PATH, follow_redirects=False))
    found = world.view(world.ada).get_slack_install()
    assert (found.id, found.state) == (install, "pending")
    assert f"/integrations/slack/{install}/events" in page


def test_the_block_says_no_slack_word_for_its_events_page_and_quotes_no_secret(world):
    ada = world.browser(world.ada)
    ada.post(setup.SETUP_PATH, follow_redirects=False)
    template = (setup.TEMPLATES / "_slack.html").read_text()
    words = " ".join([*setup.SAID.values(), *setup.REFUSALS.values(), *setup.STATES.values(),
                      *setup.PERMISSIONS.values(), template])  # fmt: skip
    assert "subscription" not in words.lower()


def test_an_access_record_holds_who_the_install_and_the_outcome_only(world, caplog):
    caplog.set_level(logging.INFO, logger="cairn.access")
    ada = world.browser(world.ada)
    ada.post(setup.SETUP_PATH, follow_redirects=False)
    ada.post(
        setup.SECRETS_PATH, data={"signing_secret": SECRET, "bot_token": TOKEN},
        follow_redirects=False,
    )  # fmt: skip
    records = [r for r in caplog.records if r.name == "cairn.access"]
    assert [(r.action, r.outcome) for r in records if r.action == "slack_setup"] == [
        ("slack_setup", "made"),
        ("slack_setup", "ready"),
    ]
    for record in records:
        said = record.getMessage() + repr(record.__dict__)
        assert SECRET not in said and TOKEN not in said
