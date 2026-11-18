"""K1 acceptance tests: Slack, both ways. Part 2, set-up and linking.

An owner sets a workspace's Slack app up on the Sources page, and each Slack user confirms
once, signed in to cairn, that they are a person of the workspace. Part 1
(test_k1_slack.py) is what this stands on; part 3 (test_k1c_slack_use.py) is what people then
do in Slack. The decisions are in docs/decisions.md; the Slack facts are in
docs/slack-api.md.

Every name here is made up; every call cairn would make to Slack goes to an
httpx.MockTransport.

The switch
- Without `--slack` none of the addresses below exists (404 as an address that is not a
  page) and the Sources page says nothing about Slack.

Set-up (Workspace, then Sources), for an owner or an admin only
- A member who is neither sees nothing of it there, and each address below refuses them as
  any management address does.
- "Set up Slack" (POST /integrations/slack/setup) makes the workspace's install, pending.
  The page then shows: a link that opens Slack's page for creating an app with this
  install's manifest filled in, and the same manifest as text to copy (both built by
  cairn.slack.manifest from this server's public URL and this install's id); what each of
  the four permissions is for, `im:history` said plainly: the direct messages people send
  the bot, and nothing among people; and two fields, for the signing secret and the bot
  token.
- The two secrets (POST /integrations/slack/secrets): cairn asks Slack whose token it is
  (`auth.test`). A token Slack does not accept, a Slack that does not answer, or a team that
  another workspace of this server already has an install for, is refused with one sentence,
  and nothing is kept. Otherwise the team is recorded, both secrets are sealed and the
  install is ready. Neither secret is ever shown again, on any page, in any log line, in the
  audit log or in an error: the fields are password fields that are never filled back in.
- The page says what state the install is in and what is left to do: ready (have Slack
  verify the events address), on, off, and off because Slack revoked the bot's token (then
  the token is asked for again, and the signing secret is not).
- Off and on (POST /integrations/slack/off, /on): off refuses commands and actions and
  deletes nothing; on again needs nothing pasted unless the token was revoked.
- Remove (POST /integrations/slack/remove), after the workspace's name is typed as a check:
  what part 1 says of removal. The page then says how many notes came through Slack, the
  day they will be forgotten, and offers an owner "Forget them now"
  (POST /integrations/slack/forget), which an admin is not offered and is refused.
- Each of these is one record on the audit log and one on the access log (`slack_setup`),
  naming who, the install and the outcome, never a secret.

Linking a Slack user to a person
- GET /integrations/slack/link?t=<the one-time value> is for a signed-in person. For a member
  of the install's workspace with a value that is good, it shows which Slack user (by the
  name Slack gives, or the id when Slack does not answer) would be linked to which person,
  and asks for a yes; it changes nothing by being opened, however often.
- The yes (POST /integrations/slack/link) links them and uses the value up: a second yes
  with it, a value older than 10 minutes, a value of another workspace, and a person who is
  not a member each get the page every address that is not a page gets, and nothing changes.
- A person who is not signed in is sent to sign in and comes back to the same address.
- The Sources page shows a person their own link, with "Unlink"
  (POST /integrations/slack/unlink); nobody is shown anybody else's Slack user.
- Each is one record on the access log (`slack_link`).

Every page here keeps the rules of every page: one `<h1>`, no script, no `style` attribute,
every field labelled, forms posted from another site refused.
"""

import hashlib
import hmac
import html
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.slack import link, setup
from cairn.slack.manifest import build_manifest, manifest_link
from cairn.store import Store
from cairn.web.slack import Slack

BASE = "https://cairn.example.test"
HOST = "cairn.example.test"
KEY = bytes(range(32))
ISSUER = "https://sso.example.test"
# Made-up values in the shapes Slack gives, built here so no file holds one whole.
SECRET = "8f" + "e1c0ffee" * 3 + "a1b2c3"  # Fable Works' signing secret
TOKEN = "xoxb-" + "1111-2222-" + "FableBotToken0123"
NEW_TOKEN = "xoxb-" + "1111-2222-" + "FableBotToken9876"  # after Slack revoked the first
QUILL_SECRET = "41" + "beadfeed" * 3 + "d4e5f6"
QUILL_TOKEN = "xoxb-" + "3333-4444-" + "QuillBotToken4567"
TWIN_TOKEN = "xoxb-" + "5555-6666-" + "TwinBotToken01234"  # another app in Fable's own team
USER_TOKEN = "xoxp-" + "7777-8888-" + "AdaUserToken01234"  # a person's token, not a bot's
TEAM, QUILL_TEAM = "T0FABLE01", "T0QUILL01"
DOMAINS = {TEAM: "fable-works", QUILL_TEAM: "quill-yard"}
BOT = "U0FABLEBOT"
BROOK_SLACK, CLEO_SLACK = "U0BROOK01", "U0CLEO001"
START = datetime(2031, 3, 4, 9, 0, tzinfo=UTC)
NO_PAGE = "There is no page at this address."
SLACK_TABLES = ("slack_installs", "slack_people", "slack_link_offers", "slack_notes")
POSTED = (  # every address here a form is posted to
    setup.SETUP_PATH, setup.SECRETS_PATH, setup.OFF_PATH, setup.ON_PATH, setup.REMOVE_PATH,
    setup.FORGET_PATH, link.LINK_PATH, link.UNLINK_PATH,
)  # fmt: skip


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class SlackApi:
    """Slack's Web API as these tests need it (docs/slack-api.md): auth.test names a token's
    team, its address and its bot; users.info names a user. `down`: Slack does not answer."""

    def __init__(self) -> None:
        self.tokens = {
            TOKEN: TEAM, NEW_TOKEN: TEAM, TWIN_TOKEN: TEAM, USER_TOKEN: TEAM,
            QUILL_TOKEN: QUILL_TEAM,
        }  # fmt: skip
        self.names = {BROOK_SLACK: "Brook Field", CLEO_SLACK: "Cleo Marsh"}
        self.down = False
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        fields = dict(parse_qsl(request.content.decode()))
        self.calls.append((method, token, fields))
        if self.down:
            raise httpx.ConnectError("Slack is not answering")
        team = self.tokens.get(token)
        if team is None:
            return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
        if method == "auth.test":
            answer = {
                "ok": True, "url": f"https://{DOMAINS[team]}.slack.com/", "team": "Fable",
                "user": "bot", "team_id": team, "user_id": BOT,
            }  # fmt: skip
            if not token.startswith("xoxp-"):
                answer["bot_id"] = "B0FABLE01"
            return httpx.Response(200, json=answer)
        if method == "users.info" and fields.get("user") in self.names:
            name = self.names[fields["user"]]
            user = {"id": fields["user"], "team_id": team, "real_name": name,
                    "profile": {"real_name": name, "display_name": name}}  # fmt: skip
            return httpx.Response(200, json={"ok": True, "user": user})
        return httpx.Response(200, json={"ok": False, "error": "user_not_found"})


class World:
    """A server with the switch on (or off) on a database with two workspaces: Fable Works,
    whose owner is Ada, with Brook an admin and Cleo a member; and Dara's own."""

    def __init__(self, tmp_path: Path, slack: bool = True) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.db = tmp_path / "cairn.db"
        self.clock = Clock()
        self.slack = SlackApi()
        self.store = Store(self.db, clock=self.clock)
        sign_in = SignIn(
            providers=(GitHub("gh-id", "gh-secret"),),
            http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
            join="personal",
        )
        switch = Slack(
            key=KEY, http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.slack))
        )
        self.app = create_app(
            self.store, public_url=BASE, sign_in=sign_in, clock=self.clock,
            slack=switch if slack else None,
        )  # fmt: skip
        self.clients: list[TestClient] = []
        self.ada = self.made("Ada")
        self.workspace = self.ada["account"]
        self.brook = self.joins("Brook", "admin")
        self.cleo = self.joins("Cleo", "member")
        self.dara = self.made("Dara")
        self.project = self.view(self.ada).create_project("library")

    def made(self, name: str) -> dict:
        signed = self.store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="personal",
        )  # fmt: skip
        return {"id": signed.user.id, "account": signed.account_id, "token": signed.token}

    def joins(self, name: str, role: str) -> dict:
        signed = self.store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
        invite = self.view(self.ada).invite(f"{name.lower()}@example.test", role)
        self.store.accept_invite(invite.token, signed.user.id)
        token = self.store.for_account(self.workspace, signed.user.id).create_session()
        return {"id": signed.user.id, "account": self.workspace, "token": token}

    def view(self, person: dict) -> Store:
        return self.store.for_account(person["account"], person["id"])

    def system(self) -> Store:
        return self.store.for_account(self.workspace).acting_as("system", None)

    def browser(self, person: dict | None) -> TestClient:
        client = TestClient(self.app, base_url=BASE)
        if person is not None:
            client.cookies.set("cairn_session", person["token"], domain=HOST, path="/")
        self.clients.append(client)
        return client

    def install(self) -> str:
        return self.view(self.ada).get_slack_install().id

    def readied(self, person: dict | None = None) -> str:
        """Fable Works' install, set up and ready, by the browser."""
        browser = self.browser(person or self.ada)
        said(browser, browser.post(setup.SETUP_PATH, follow_redirects=False))
        said(browser, secrets(browser))
        return self.install()

    def slack_rows(self) -> dict:
        con = sqlite3.connect(self.db)
        try:
            return {
                t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in SLACK_TABLES
            }
        finally:
            con.close()

    def audit(self) -> list:
        return [e for e in self.view(self.ada).list_audit(500) if e.action.startswith("slack.")]

    def close(self) -> None:
        for client in self.clients:
            client.close()
        self.store.close()


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.close()


def secrets(browser: TestClient, secret: str | None = SECRET, token: str | None = TOKEN):
    form = {
        **({"signing_secret": secret} if secret is not None else {}),
        **({"bot_token": token} if token is not None else {}),
    }
    return browser.post(setup.SECRETS_PATH, data=form, follow_redirects=False)


def said(browser: TestClient, response: httpx.Response) -> str:
    """The Sources page the browser is sent to after a form, as a person reads it."""
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/sources#slack"
    page = browser.get("/sources")
    assert page.status_code == 200
    return html.unescape(page.text)


def block(page: str) -> str:
    """The Slack block of a Sources page; "" when there is none."""
    start = page.find('id="slack"')
    if start < 0:
        return ""
    return page[start : page.index("</section>", start)]


def shown(browser: TestClient) -> str:
    """The Slack block of the browser's Sources page, as a person reads it."""
    return block(html.unescape(browser.get("/sources").text))


class Rules(HTMLParser):
    """What every page keeps: one <h1>, no script, no style attribute, every field labelled."""

    def __init__(self) -> None:
        super().__init__()
        self.h1 = 0
        self.scripts = 0
        self.styled = 0
        self.fields: list[dict] = []
        self.labels: set[str] = set()

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        self.h1 += tag == "h1"
        self.scripts += tag == "script"
        self.styled += "style" in found
        if tag == "label" and found.get("for"):
            self.labels.add(found["for"])
        if tag in ("input", "select", "textarea") and found.get("type") not in (
            "hidden", "submit",
        ):  # fmt: skip
            self.fields.append(found)


def keeps_the_rules(text: str) -> None:
    rules = Rules()
    rules.feed(text)
    assert rules.h1 == 1 and rules.scripts == 0 and rules.styled == 0
    for found in rules.fields:
        assert found.get("id") in rules.labels or found.get("aria-label"), found


def unknown(browser: TestClient) -> httpx.Response:
    """What an address that is not a page answers this browser."""
    return browser.get("/integrations/nothing/here")


def logged(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "cairn.access"]


def everything_said(caplog, world: World, *pages: str) -> str:
    """Every log line, every audit record and the given pages, as one text."""
    lines = [r.getMessage() + repr(r.__dict__) for r in caplog.records]
    audit = [repr(e) for e in world.view(world.ada).list_audit(500)]
    return "\n".join([*lines, *audit, *pages])


def readied_elsewhere(world: World, token: str, team: str, domain: str) -> str:
    """Dara's workspace's install, made ready for `team` through the store."""
    view = world.view(world.dara)
    made = view.make_slack_install()
    view.ready_slack_install(
        made.id, signing_secret=QUILL_SECRET, bot_token=token, team_id=team,
        team_domain=domain, bot_user_id=BOT,
    )  # fmt: skip
    return made.id


def typed_note(world: World, install: str, text: str) -> str:
    view = world.view(world.ada)
    note = view.add_note(world.project.id, text, title=text.capitalize())[0]
    view.record_slack_note(install, note.id, how="typed", brought_by=world.ada["id"])
    return note.id


# The switch


# The sources' own addresses, /integrations/<source>/off, /on and /remove, have these shapes:
# with the switch off they are a source named "slack"'s, and say there is none.
SOURCE_SHAPED = (setup.OFF_PATH, setup.ON_PATH, setup.REMOVE_PATH)


@pytest.fixture
def off(tmp_path):
    made = World(tmp_path, slack=False)
    yield made
    made.close()


def test_off_none_of_the_addresses_exists_and_the_sources_page_says_nothing_of_slack(off):
    ada = off.browser(off.ada)
    nowhere = ada.post("/integrations/nothing/here")
    assert nowhere.status_code == 404 and NO_PAGE in html.unescape(nowhere.text)
    for path in POSTED:
        answer = ada.post(path, data={"confirm": "x"})
        assert answer.status_code == 404, path
        if path not in SOURCE_SHAPED:
            assert answer.content == nowhere.content, path
    got = ada.get(f"{link.LINK_PATH}?t=anything")
    assert got.status_code == 404 and got.content == unknown(ada).content
    assert "slack" not in ada.get("/sources").text.lower()


@pytest.mark.xfail(
    strict=True,
    reason="cairn.web.ui serves /integrations/<source>/off, /on and /remove: with the switch off"
    " these answer that there is no source named slack",
)
def test_off_the_three_addresses_shaped_as_a_sources_are_no_page_either(off):
    ada = off.browser(off.ada)
    nowhere = ada.post("/integrations/nothing/here")
    for path in SOURCE_SHAPED:
        assert ada.post(path, data={"confirm": "x"}).content == nowhere.content, path


# Set-up, for an owner or an admin only


def test_a_member_sees_nothing_of_it_on_the_sources_page(world):
    world.readied()
    page = world.browser(world.cleo).get("/sources")
    assert page.status_code == 200 and block(page.text) == ""
    assert "slack" not in page.text.lower()


def test_each_address_refuses_a_member_as_any_management_address_does(world):
    install = world.readied()
    typed_note(world, install, "kept")
    cleo = world.browser(world.cleo)
    management = cleo.post("/members/invite", data={"email": "eve@example.test", "role": "member"})
    assert management.status_code == 403 and "Error 403" in management.text
    asked = len(world.slack.calls)
    before = world.slack_rows()
    name = world.store.get_account(world.workspace).name
    for path, form in (
        (setup.SETUP_PATH, {}),
        (setup.SECRETS_PATH, {"signing_secret": SECRET, "bot_token": TOKEN}),
        (setup.OFF_PATH, {}),(setup.ON_PATH, {}), (setup.REMOVE_PATH, {"confirm": name}),
        (setup.FORGET_PATH, {}),
    ):  # fmt: skip
        answer = cleo.post(path, data=form, follow_redirects=False)
        assert answer.status_code == 403, path
        assert "<h1" in answer.text and "Error 403" in answer.text
    assert world.slack_rows() == before and len(world.slack.calls) == asked


@pytest.mark.parametrize("who", ["ada", "brook"])
def test_set_up_slack_makes_the_install_pending_and_shows_how_to_make_the_app(world, who):
    browser = world.browser(getattr(world, who))
    page = block(said(browser, browser.post(setup.SETUP_PATH, follow_redirects=False)))
    install = world.view(world.ada).get_slack_install()
    assert install.state == "pending"
    manifest = build_manifest(public_url=BASE, install=install.id)
    raw = browser.get("/sources").text
    assert f'href="{html.escape(manifest_link(manifest))}"' in raw
    assert "<pre" in raw and json.dumps(manifest, indent=2) in page
    for scope, words in setup.PERMISSIONS.items():
        assert scope in page and words in page
    assert "the direct messages people send the bot" in setup.PERMISSIONS["im:history"]
    assert "nothing among people" in setup.PERMISSIONS["im:history"]
    for name in ("signing_secret", "bot_token"):
        at = raw.index(f'name="{name}"')
        field = raw[raw.rindex("<input", 0, at) : raw.index(">", at)]
        assert 'type="password"' in field and "value=" not in field


def test_set_up_twice_keeps_one_install(world):
    ada = world.browser(world.ada)
    said(ada, ada.post(setup.SETUP_PATH, follow_redirects=False))
    first = world.install()
    said(ada, ada.post(setup.SETUP_PATH, follow_redirects=False))
    assert world.install() == first


def test_the_two_secrets_are_checked_with_slack_and_kept_sealed(world):
    ada = world.browser(world.ada)
    said(ada, ada.post(setup.SETUP_PATH, follow_redirects=False))
    page = said(ada, secrets(ada))
    install = world.view(world.ada).get_slack_install()
    assert (install.state, install.team_id, install.team_domain) == ("ready", TEAM, "fable-works")
    assert install.bot_user_id == BOT and install.holds_secrets
    assert ("auth.test", TOKEN, {}) in world.slack.calls
    assert world.store.slack_inbound(install.id).signing_secret == SECRET
    assert world.view(world.ada).slack_bot_token(install.id) == TOKEN
    assert setup.SAID["ready"] in page
    held = world.db.read_bytes()
    assert SECRET.encode() not in held and TOKEN.encode() not in held


@pytest.mark.parametrize("case", ["not_accepted", "unreachable", "taken", "not_a_bot"])
def test_a_refused_attempt_is_one_sentence_and_keeps_nothing(world, case):
    if case == "taken":  # Dara's workspace holds Fable's Slack team already
        readied_elsewhere(world, TWIN_TOKEN, TEAM, "fable-works")
    ada = world.browser(world.ada)
    said(ada, ada.post(setup.SETUP_PATH, follow_redirects=False))
    before = world.slack_rows()
    world.slack.down = case == "unreachable"
    token = {"not_accepted": "xoxb-0000-0000-NotAToken0000", "not_a_bot": USER_TOKEN}.get(
        case, TOKEN
    )
    page = block(said(ada, secrets(ada, token=token)))
    sentence = setup.REFUSALS[case]
    assert sentence in page and sentence.count(". ") == 0 and sentence.endswith(".")
    assert world.slack_rows() == before
    assert world.view(world.ada).get_slack_install().state == "pending"
    assert token not in page and SECRET not in page


def test_a_team_whose_install_another_workspace_removed_is_not_taken(world):
    quill = readied_elsewhere(world, TWIN_TOKEN, TEAM, "fable-works")
    world.view(world.dara).remove_slack_install(quill)
    world.readied()
    assert world.view(world.ada).get_slack_install().state == "ready"


def test_neither_secret_is_ever_shown_logged_audited_or_said_in_an_error(world, caplog):
    caplog.set_level(logging.DEBUG)
    ada = world.browser(world.ada)
    pages = [said(ada, ada.post(setup.SETUP_PATH, follow_redirects=False)), said(ada, secrets(ada))]
    world.slack.down = True
    pages.append(said(ada, secrets(ada)))  # refused: Slack does not answer
    world.slack.down = False
    pages.append(said(ada, secrets(ada, secret="", token=TOKEN)))  # refused: one is blank
    pages += [ada.get("/sources").text, world.browser(world.brook).get("/sources").text]
    text = everything_said(caplog, world, *pages)
    assert SECRET not in text and TOKEN not in text and "xoxb-" not in text


def test_a_blank_secret_is_refused_and_slack_is_not_asked(world):
    ada = world.browser(world.ada)
    said(ada, ada.post(setup.SETUP_PATH, follow_redirects=False))
    page = block(said(ada, secrets(ada, secret="  ", token=TOKEN)))
    assert setup.REFUSALS["blank"] in page and world.slack.calls == []
    assert world.view(world.ada).get_slack_install().state == "pending"


def test_the_secrets_with_nothing_set_up_are_refused(world):
    ada = world.browser(world.ada)
    page = block(said(ada, secrets(ada)))
    assert setup.REFUSALS["none"] in page
    assert world.view(world.ada).get_slack_install() is None and world.slack.calls == []


# What state the install is in, and what is left to do


def test_ready_says_to_have_slack_verify_the_events_address(world):
    install = world.readied()
    page = shown(world.browser(world.ada))
    assert setup.STATES["ready"] in page
    assert f"{BASE}/integrations/slack/{install}/events" in page


def test_on_off_and_off_because_revoked_say_their_state(world):
    install = world.readied()
    ada = world.browser(world.ada)
    world.store.slack_install_verified(install)
    assert setup.STATES["on"] in shown(ada)
    said(ada, ada.post(setup.OFF_PATH, follow_redirects=False))
    page = shown(ada)
    assert setup.STATES["off"] in page and 'name="bot_token"' not in page
    world.system().revoke_slack_bot(install)
    page = shown(ada)
    assert setup.STATES["revoked"] in page
    assert 'name="bot_token"' in page and 'name="signing_secret"' not in page


def test_a_revoked_token_is_asked_for_again_and_the_signing_secret_is_not(world):
    install = world.readied()
    world.system().revoke_slack_bot(install)
    ada = world.browser(world.ada)
    page = said(ada, secrets(ada, secret=None, token=NEW_TOKEN))
    assert setup.SAID["ready"] in page
    found = world.view(world.ada).get_slack_install()
    assert found.state == "ready" and found.holds_secrets
    assert world.view(world.ada).slack_bot_token(install) == NEW_TOKEN
    assert world.store.slack_inbound(install).signing_secret == SECRET


# Off and on


def command_answer(world: World, install: str) -> int:
    """The status Slack gets for a signed `/cairn help` to the install's commands address."""
    body = urlencode(
        {"team_id": TEAM, "user_id": CLEO_SLACK, "command": "/cairn", "text": "help",
         "response_url": "https://hooks.slack.com/commands/T0FABLE01/1/x"}
    ).encode()  # fmt: skip
    at = str(int(world.clock().timestamp()))
    digest = hmac.new(SECRET.encode(), b"v0:" + at.encode() + b":" + body, hashlib.sha256)
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": at, "X-Slack-Signature": "v0=" + digest.hexdigest(),
    }  # fmt: skip
    client = world.browser(None)
    return client.post(f"/integrations/slack/{install}/commands", content=body, headers=headers)


def test_off_refuses_commands_and_deletes_nothing_and_on_needs_nothing_pasted(world):
    install = world.readied()
    view = world.view(world.ada)
    view.link_slack_user(install, CLEO_SLACK, world.cleo["id"])
    typed_note(world, install, "kept")
    assert command_answer(world, install).status_code == 200
    ada = world.browser(world.ada)
    assert setup.SAID["off"] in said(ada, ada.post(setup.OFF_PATH, follow_redirects=False))
    rows = world.slack_rows()
    assert view.get_slack_install().state == "off" and view.get_slack_install().holds_secrets
    assert command_answer(world, install).status_code == 404
    assert rows["slack_people"] and rows["slack_notes"]
    page = said(ada, ada.post(setup.ON_PATH, follow_redirects=False))
    assert setup.SAID["on"] in page and view.get_slack_install().state == "on"
    assert command_answer(world, install).status_code == 200
    after = world.slack_rows()
    assert (after["slack_people"], after["slack_notes"]) == (
        rows["slack_people"],
        rows["slack_notes"],
    )


def test_on_after_slack_revoked_the_token_asks_for_it_and_changes_nothing(world):
    install = world.readied()
    world.system().revoke_slack_bot(install)
    ada = world.browser(world.ada)
    before = world.slack_rows()
    page = block(said(ada, ada.post(setup.ON_PATH, follow_redirects=False)))
    assert setup.REFUSALS["needs_token"] in page and world.slack_rows() == before


# Remove, and forgetting


def test_remove_needs_the_workspaces_name_typed(world):
    install = world.readied()
    ada = world.browser(world.ada)
    before = world.slack_rows()
    refused = ada.post(setup.REMOVE_PATH, data={"confirm": "not its name"}, follow_redirects=False)
    assert setup.REFUSALS["confirm"] in block(said(ada, refused))
    assert world.slack_rows() == before and world.install() == install


def test_remove_says_how_many_notes_and_the_day_and_offers_only_the_owner_forget_now(world):
    install = world.readied()
    kept = [typed_note(world, install, "first"), typed_note(world, install, "second")]
    world.view(world.ada).link_slack_user(install, CLEO_SLACK, world.cleo["id"])
    ada, brook = world.browser(world.ada), world.browser(world.brook)
    name = world.store.get_account(world.workspace).name
    page = block(said(ada, ada.post(setup.REMOVE_PATH, data={"confirm": name},
                                    follow_redirects=False)))  # fmt: skip
    found = world.view(world.ada).get_slack_install()
    assert found.state == "removed" and not found.holds_secrets
    assert world.slack_rows()["slack_people"] == []
    assert "2 notes came through Slack" in page and "14 March 2031" in page
    assert "Forget them now" in page
    seen = shown(brook)
    assert "2 notes came through Slack" in seen and "Forget them now" not in seen
    refused = brook.post(setup.FORGET_PATH, follow_redirects=False)
    assert refused.status_code == 403 and len(world.slack_rows()["slack_notes"]) == 2
    page = block(said(ada, ada.post(setup.FORGET_PATH, follow_redirects=False)))
    assert world.slack_rows()["slack_notes"] == [] and "Forget them now" not in page
    for note in kept:
        assert world.view(world.ada).get_note(note) is None


def test_an_admin_removes_too(world):
    world.readied()
    brook = world.browser(world.brook)
    name = world.store.get_account(world.workspace).name
    said(brook, brook.post(setup.REMOVE_PATH, data={"confirm": name}, follow_redirects=False))
    assert world.view(world.ada).get_slack_install().state == "removed"


# The audit log and the access log


def test_each_is_one_audit_record_and_one_access_record_naming_who_the_install_and_the_outcome(
    world, caplog
):
    caplog.set_level(logging.INFO, logger="cairn.access")
    ada = world.browser(world.ada)
    name = world.store.get_account(world.workspace).name
    steps = [
        (lambda: ada.post(setup.SETUP_PATH, follow_redirects=False), "slack.install_made"),
        (lambda: secrets(ada), "slack.install_ready"),
        (lambda: ada.post(setup.OFF_PATH, follow_redirects=False), "slack.install_switched"),
        (lambda: ada.post(setup.ON_PATH, follow_redirects=False), "slack.install_switched"),
        (lambda: ada.post(setup.REMOVE_PATH, data={"confirm": name}, follow_redirects=False),
         "slack.install_removed"),
    ]  # fmt: skip
    for step, action in steps:
        audit_before, records_before = len(world.audit()), len(logged(caplog))
        assert step().status_code == 303
        added = world.audit()[: len(world.audit()) - audit_before]
        assert [(e.action, e.actor_id) for e in added] == [(action, world.ada["id"])]
        assert added[0].detail["install"] == world.install()
        records = logged(caplog)[records_before:]
        assert [r.action for r in records] == ["slack_setup"]
        assert (records[0].actor_id, records[0].install) == (world.ada["id"], world.install())
        assert records[0].status == 303 and records[0].outcome
    records_before = len(logged(caplog))
    world.browser(world.cleo).post(setup.OFF_PATH)
    (refused,) = logged(caplog)[records_before:]
    assert (refused.action, refused.status, refused.actor_id) == (
        "slack_setup",
        403,
        world.cleo["id"],
    )


# Linking a Slack user to a person


def offered(world: World, slack_user: str = CLEO_SLACK) -> tuple[str, str]:
    """A ready install, and a one-time value made as the other part's `/cairn link` makes it."""
    install = world.readied()
    return install, world.system().offer_slack_link(install, slack_user)


def test_the_link_page_names_both_sides_asks_for_a_yes_and_changes_nothing(world):
    install, value = offered(world)
    cleo = world.browser(world.cleo)
    before = world.slack_rows()
    for _ in range(2):
        page = cleo.get(f"{link.LINK_PATH}?t={value}")
        assert page.status_code == 200
        text = html.unescape(page.text)
        assert "Cleo Marsh" in text and "cleo@example.test" in text
        assert "<form" in page.text and 'method="post"' in page.text
        assert value not in page.text
        keeps_the_rules(page.text)
    assert world.slack_rows() == before
    assert world.view(world.ada).slack_person(install, CLEO_SLACK) is None


def test_the_link_page_names_the_slack_user_by_id_when_slack_does_not_answer(world):
    _install, value = offered(world)
    world.slack.down = True
    page = world.browser(world.cleo).get(f"{link.LINK_PATH}?t={value}")
    assert page.status_code == 200 and CLEO_SLACK in page.text and "Cleo Marsh" not in page.text


def test_the_yes_links_them_and_uses_the_value_up(world, caplog):
    caplog.set_level(logging.INFO, logger="cairn.access")
    install, value = offered(world)
    cleo = world.browser(world.cleo)
    page = said(cleo, cleo.post(f"{link.LINK_PATH}?t={value}", follow_redirects=False))
    assert link.SAID["linked"] in page
    assert world.view(world.ada).slack_person(install, CLEO_SLACK).user_id == world.cleo["id"]
    before = world.slack_rows()
    again = cleo.post(f"{link.LINK_PATH}?t={value}")
    assert again.status_code == 404 and again.content == unknown(cleo).content
    assert world.slack_rows() == before
    assert cleo.get(f"{link.LINK_PATH}?t={value}").content == unknown(cleo).content
    assert [r.action for r in logged(caplog)].count("slack_link") == 3
    assert value not in everything_said(caplog, world)


def test_a_value_older_than_10_minutes_is_no_page_and_changes_nothing(world):
    _install, value = offered(world)
    world.clock.now += timedelta(minutes=10, seconds=1)
    cleo = world.browser(world.cleo)
    before = world.slack_rows()
    for answer in (cleo.get(f"{link.LINK_PATH}?t={value}"),
                   cleo.post(f"{link.LINK_PATH}?t={value}")):  # fmt: skip
        assert answer.status_code == 404 and answer.content == unknown(cleo).content
    assert world.slack_rows() == before


def test_a_value_of_another_workspace_and_a_person_not_a_member_get_no_page(world):
    _install, value = offered(world)
    quill = readied_elsewhere(world, QUILL_TOKEN, QUILL_TEAM, "quill-yard")
    theirs = world.view(world.dara).offer_slack_link(quill, "U0QUILL01")
    cleo, dara = world.browser(world.cleo), world.browser(world.dara)
    before = world.slack_rows()
    for browser, given in ((cleo, theirs), (dara, value)):
        for answer in (browser.get(f"{link.LINK_PATH}?t={given}"),
                       browser.post(f"{link.LINK_PATH}?t={given}")):  # fmt: skip
            assert answer.status_code == 404 and answer.content == unknown(browser).content
    for answer in (cleo.get(link.LINK_PATH), cleo.post(link.LINK_PATH),
                   cleo.get(f"{link.LINK_PATH}?t=")):  # fmt: skip
        assert answer.status_code == 404 and answer.content == unknown(cleo).content
    assert world.slack_rows() == before


@pytest.mark.xfail(
    strict=True,
    reason="cairn.web.auth._refusal carries `next` only for /new, /join/ and the consent page",
)
def test_a_person_not_signed_in_comes_back_to_the_same_address(world):
    _install, value = offered(world)
    address = f"{link.LINK_PATH}?t={value}"
    away = world.browser(None).get(address, follow_redirects=False)
    assert away.status_code == 303
    assert away.headers["location"] == f"/login?next={quote(address, safe='')}"


def test_a_person_not_signed_in_is_sent_to_sign_in_and_nothing_changes(world):
    _install, value = offered(world)
    before = world.slack_rows()
    away = world.browser(None).get(f"{link.LINK_PATH}?t={value}", follow_redirects=False)
    assert away.status_code == 303 and away.headers["location"].startswith("/login")
    assert world.slack_rows() == before


@pytest.mark.xfail(
    strict=True,
    reason="cairn.store.slack has no read of a person's own links (from person to Slack user)",
)
def test_the_sources_page_shows_a_person_their_own_link_with_unlink(world):
    _install, value = offered(world)
    cleo = world.browser(world.cleo)
    said(cleo, cleo.post(f"{link.LINK_PATH}?t={value}", follow_redirects=False))
    page = shown(cleo)
    assert CLEO_SLACK in page and f'action="{link.UNLINK_PATH}"' in page


def test_unlink_undoes_ones_own_link_only(world, caplog):
    caplog.set_level(logging.INFO, logger="cairn.access")
    install, value = offered(world)
    world.view(world.ada).link_slack_user(install, BROOK_SLACK, world.brook["id"])
    cleo = world.browser(world.cleo)
    said(cleo, cleo.post(f"{link.LINK_PATH}?t={value}", follow_redirects=False))
    before = world.slack_rows()
    for slack_user in (BROOK_SLACK, "U0NOBODY1", "not an id"):
        theirs = cleo.post(link.UNLINK_PATH, data={"slack_user": slack_user})
        assert theirs.status_code == 404 and theirs.content == unknown(cleo).content
    assert world.slack_rows() == before
    page = said(cleo, cleo.post(link.UNLINK_PATH, data={"slack_user": CLEO_SLACK},
                                follow_redirects=False))  # fmt: skip
    assert link.SAID["unlinked"] in page
    assert world.view(world.ada).slack_person(install, CLEO_SLACK) is None
    assert world.view(world.ada).slack_person(install, BROOK_SLACK).user_id == world.brook["id"]
    assert [r.action for r in logged(caplog)].count("slack_link") == 5


def test_nobody_is_shown_anybody_elses_slack_user(world):
    install = world.readied()
    view = world.view(world.ada)
    view.link_slack_user(install, BROOK_SLACK, world.brook["id"])
    view.link_slack_user(install, CLEO_SLACK, world.cleo["id"])
    for person, others in (
        (world.ada, (BROOK_SLACK, CLEO_SLACK, "Brook Field", "Cleo Marsh")),
        (world.brook, (CLEO_SLACK, "Cleo Marsh")),
        (world.cleo, (BROOK_SLACK, "Brook Field")),
    ):
        page = world.browser(person).get("/sources").text
        for other in others:
            assert other not in page


# Every page keeps the rules of every page


def test_every_page_here_keeps_the_rules_and_forms_from_another_site_are_refused(world):
    ada = world.browser(world.ada)
    said(ada, ada.post(setup.SETUP_PATH, follow_redirects=False))
    keeps_the_rules(ada.get("/sources").text)
    said(ada, secrets(ada))
    keeps_the_rules(ada.get("/sources").text)
    world.system().revoke_slack_bot(world.install())
    keeps_the_rules(ada.get("/sources").text)
    name = world.store.get_account(world.workspace).name
    said(ada, ada.post(setup.REMOVE_PATH, data={"confirm": name}, follow_redirects=False))
    keeps_the_rules(ada.get("/sources").text)
    before = world.slack_rows()
    for path in POSTED:
        for headers in ({"origin": "https://elsewhere.example.test"},
                        {"sec-fetch-site": "cross-site"}):  # fmt: skip
            answer = ada.post(path, data={"confirm": name}, headers=headers)
            assert answer.status_code == 403, path
    assert world.slack_rows() == before
