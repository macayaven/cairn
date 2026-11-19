"""The Sources page (R2, section 5): a person chooses what a source reads and where it writes,
with no command line and no script.

GET /sources says that nothing is read until someone chooses, lists the accounts the viewer
may use, each source in words, and a form that makes a new one; every form posts under
/integrations/. Every name here is made up; no request leaves the test.
"""

import html as html_text
import re
import sqlite3
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.sources import KINDS
from cairn.store import Store
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
ISSUER = "https://id.example.test/pool-page"
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
ADA_ENV, TEAM_ENV, BROOK_ENV = "PAGE_ADA_GOOGLE", "PAGE_TEAM_LINEAR", "PAGE_BROOK_GOOGLE"
ADA_ADDRESS = "ada.quill@example.test"  # the Google account Ada's credential reads as
NOTHING_READ = "Nothing is read until you choose what and where."


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def page_text(markup: str) -> str:
    return " ".join(html_text.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def section(markup: str, ident: str) -> str:
    """The inside of the element whose id is `ident`, up to its closing </section>."""
    found = re.search(r'<section\b[^>]*\bid="' + re.escape(ident) + r'"[^>]*>(.*?)</section>',
                      markup, re.S)  # fmt: skip
    assert found, f"no section {ident}"
    return found.group(1)


def main(markup: str) -> str:
    """The page's own part: what <main> holds, without the frame's sign-out form."""
    return markup.partition('<main id="main">')[2].partition("</main>")[0]


def form_to(markup: str, action: str) -> tuple[str, str]:
    found = re.search(
        r'(<form\b[^>]*\baction="' + re.escape(action) + r'(#[^"]*)?"[^>]*>)(.*?)</form>',
        markup,
        re.S,
    )
    assert found, f"no form posts to {action}"
    return found.group(1), found.group(3)


def person(store: Store, name: str, role: str | None = None) -> SimpleNamespace:
    signed = store.sign_in(
        "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER, join="default"
    )
    if role is not None:
        store.set_role(signed.user.id, role)
    return SimpleNamespace(
        id=signed.user.id,
        cookie={"cookie": f"{session_cookie()}={signed.token}"},
        view=store.for_account(signed.account_id, signed.user.id),
    )


@pytest.fixture
def world(tmp_path):
    """Ada (the owner) with her Gmail account, Brook (a member) with his, Cy (an admin), the
    workspace's Linear account, and one source made from Ada's account, off."""
    db = tmp_path / "cairn.db"
    store = Store(db, clock=lambda: NOW)
    ada, brook = person(store, "Ada"), person(store, "Brook")
    cy = person(store, "Cy", role="admin")
    store.create_project("decisions")
    store.create_project("shared", everyone="write")
    gmail = {"credentials_env": ADA_ENV, "subject": ADA_ADDRESS}
    store.add_credential("ada-mail", "gmail", gmail, owner="ada@example.test")
    brooks = {"credentials_env": BROOK_ENV, "subject": "brook.ng@example.test"}
    store.add_credential("brook-mail", "gmail", brooks, owner="brook@example.test")
    store.add_credential("team-linear", "linear", {"key_env": TEAM_ENV})
    ada.view.add_source_from("ada-labelled", "ada-mail", {"label": "to-team"}, {}, "decisions")
    refused = httpx.MockTransport(lambda request: httpx.Response(500))
    app = create_app(
        store, public_url=BASE, source_http=lambda: httpx.AsyncClient(transport=refused)
    )
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(store=store, db=db, ada=ada, brook=brook, cy=cy, client=client)
    store.close()


def get(world, who, path: str = "/sources") -> httpx.Response:
    return world.client.get(path, headers=who.cookie)


def post(world, who, path: str, data: dict | None = None) -> httpx.Response:
    return world.client.post(
        path, data=data or {}, headers={**SAME_ORIGIN, **who.cookie}, follow_redirects=False
    )


class Controls(HTMLParser):
    """Every input, select, textarea and button with what names it, and every script."""

    def __init__(self, markup: str) -> None:
        super().__init__(convert_charrefs=True)
        self.controls: list[dict] = []
        self.labels: dict[str, str] = {}  # for= -> text
        self.scripts = 0
        self.handlers: list[str] = []
        self._label: list[dict] = []  # open labels
        self._button: dict | None = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        given = dict(attrs)
        self.handlers += [name for name, _ in attrs if name.startswith("on")]
        if tag == "script":
            self.scripts += 1
        elif tag == "label":
            self._label.append({"for": given.get("for"), "text": ""})
        elif tag in ("input", "select", "textarea"):
            if given.get("type") == "hidden":
                return
            self.controls.append({"tag": tag, **given, "wrapped": bool(self._label)})
        elif tag == "button":
            self._button = {"tag": tag, **given, "text": ""}
            self.controls.append(self._button)

    def handle_endtag(self, tag):
        if tag == "label" and self._label:
            closed = self._label.pop()
            if closed["for"]:
                self.labels[closed["for"]] = closed["text"]
        elif tag == "button":
            self._button = None

    def handle_data(self, data):
        for label in self._label:
            label["text"] += data
        if self._button is not None:
            self._button["text"] += data

    def unnamed(self) -> list[dict]:
        return [
            control
            for control in self.controls
            if not (
                control.get("aria-label")
                or control.get("aria-labelledby")
                or control.get("wrapped")
                or (control.get("id") and self.labels.get(control["id"], "").strip())
                or (control["tag"] == "button" and control.get("text", "").strip())
            )
        ]


# The page, read only


def test_the_page_says_nothing_is_read_until_chosen_and_lists_accounts(world):
    page = get(world, world.ada)
    assert page.status_code == 200
    text = page_text(page.text)
    assert NOTHING_READ in text
    accounts = page_text(section(page.text, "accounts"))
    assert "Your accounts" in accounts
    # One row per account Ada may use: hers and the workspace's, never Brook's.
    assert "Gmail" in accounts and "Yours" in accounts and ADA_ADDRESS in accounts
    assert "1 source" in accounts
    assert "Linear" in accounts and "The workspace's" in accounts and "0 sources" in accounts
    assert "brook" not in accounts.partition("Other people")[0].lower()  # theirs: below
    # The source made from it, in words.
    first = "From your Gmail: messages labelled “to-team” → into decisions"
    assert first in text
    # With no account to use: what to do.
    for name in ("ada-mail", "team-linear", "brook-mail"):
        world.store.remove_credential(name)
    accounts = page_text(section(get(world, world.ada).text, "accounts"))
    assert "No account yet. Ask the person who runs this server to connect one." in accounts


def test_the_page_shows_no_variable_address_or_subject(world):
    world.store.enable_source("ada-labelled")
    world.store.disable_source("ada-labelled")
    page = get(world, world.ada).text
    for hidden in (ADA_ENV, TEAM_ENV, BROOK_ENV, "googleapis.com", "api.linear.app"):
        assert hidden not in page, hidden
    # The account's address is shown once: in its row under "Your accounts".
    assert page.count(ADA_ADDRESS) == 1 and ADA_ADDRESS in section(page, "accounts")
    assert "brook.ng@example.test" not in page
    for who in (world.cy, world.ada):  # nor to an admin, nor in a refusal
        shown = post(world, who, "/integrations/ada-labelled/on", {"project": "nowhere"}).text
        for hidden in (ADA_ENV, "googleapis.com"):
            assert hidden not in shown
    # A preview of a service that cannot be read, and a run that failed, say why without them.
    previewed = post(world, world.ada, "/integrations/ada-labelled/preview", {"days": "3"})
    assert previewed.status_code == 200 and 'id="preview-ada-labelled"' in previewed.text
    world.store.enable_source("ada-labelled")
    claim = world.store.claim_source("ada-labelled")
    failed = f"set {ADA_ENV} for {ADA_ADDRESS} at https://gmail.googleapis.com/gmail/v1"
    world.store.fail_source_run(claim, failed)
    for shown in (previewed.text, get(world, world.ada).text):
        for hidden in (ADA_ENV, "googleapis.com"):
            assert hidden not in shown, hidden
        assert shown.count(ADA_ADDRESS) == 1
    assert "Failed:" in page_text(section(get(world, world.ada).text, "source-ada-labelled"))


def test_the_new_forms_are_refused_from_another_site(world):
    before = dump(world.db)
    cross = {"sec-fetch-site": "cross-site", **world.ada.cookie}
    for path, data in (
        ("/integrations/new", {"credential": "ada-mail", "name": "x", "select_label": "y",
                               "project": "decisions"}),
        ("/integrations/ada-labelled/remove", {"confirm": "ada-labelled"}),
        ("/integrations/accounts/ada-mail/disconnect", {"confirm": "ada-mail"}),
    ):  # fmt: skip
        refused = world.client.post(path, data=data, headers=cross, follow_redirects=False)
        assert refused.status_code == 403, path
    assert dump(world.db) == before


def test_every_control_has_a_label_and_the_page_has_no_script(world):
    world.store.enable_source("ada-labelled")
    for who, path in (
        (world.ada, "/sources"),
        (world.ada, "/sources?account=ada-mail"),
        (world.ada, "/sources?account=team-linear"),
        (world.cy, "/sources"),
    ):
        page = get(world, who, path)
        assert page.status_code == 200, path
        found = Controls(page.text)
        assert found.controls, path
        assert found.unnamed() == [], (path, found.unnamed())
        assert found.scripts == 0 and found.handlers == [], path


def test_no_form_posts_under_sources(world):
    world.store.enable_source("ada-labelled")
    for path in ("/sources", "/sources?account=ada-mail"):
        page = main(get(world, world.ada, path).text)
        forms = re.findall(r"<form\b[^>]*>", page)
        assert forms
        for opening in forms:
            method = re.search(r'\bmethod="([^"]*)"', opening)
            action = re.search(r'\baction="([^"]*)"', opening).group(1)
            if method and method.group(1).lower() == "post":
                assert action.startswith("/integrations/") and "/sources" not in action, opening
            else:  # choosing an account is a GET of the page itself
                assert action.partition("#")[0] == "/sources", opening


# Choose something new


def test_a_source_is_made_from_the_page_and_starts_off(world):
    chosen = get(world, world.ada, "/sources?account=ada-mail")
    assert chosen.status_code == 200
    step = main(chosen.text)
    picked = re.search(r'<input\b[^>]*name="account"[^>]*value="ada-mail"[^>]*>', step)
    assert picked and "checked" in picked.group(0)
    assert "brook-mail" not in section(step, "new-source")  # not offered to make from
    opening, inside = form_to(step, "/integrations/new")
    assert 'method="post"' in opening
    legends = [page_text(legend) for legend in re.findall(r"<legend>(.*?)</legend>", inside, re.S)]
    assert legends[:2] == ["What it reads from your Gmail", "Where it writes"]
    label = re.search(r'<input\b[^>]*name="select_label"[^>]*>', inside).group(0)
    assert "value=" not in label  # nothing pre-filled
    assert "The Gmail label on the messages it should read" in page_text(inside)
    assert re.search(r"<button\b[^>]*>\s*Save, off\s*</button>", inside)
    assert re.search(r"<button\b[^>]*>\s*Save and switch on\s*</button>", inside)
    assert " ".join(KINDS["gmail"].switching_on().split()) in page_text(inside)

    before = len(world.store.list_audit(limit=100))
    made = post(
        world,
        world.ada,
        "/integrations/new",
        {"credential": "ada-mail", "name": "ada-weekly", "select_label": "weekly",
         "project": "decisions", "then": "off"},
    )  # fmt: skip
    assert made.status_code == 303
    assert made.headers["location"] == "/sources?saved=ada-weekly#source-ada-weekly-name"
    held = world.store.get_source("ada-weekly")
    assert held.enabled is False and held.config["label"] == "weekly"
    assert held.config["projects"] == ["decisions"]
    assert world.store.source_credentials()["ada-weekly"] == "ada-mail"
    assert len(world.store.list_audit(limit=100)) == before + 1
    # Back on the page: saved, off, and Preview is offered on it before switching it on.
    shown = get(world, world.ada, made.headers["location"])
    mine = section(shown.text, "source-ada-weekly")
    assert 'role="status"' in mine and "Saved, off." in page_text(mine)
    form_to(mine, "/integrations/ada-weekly/preview")

    on = post(
        world,
        world.ada,
        "/integrations/new",
        {"credential": "team-linear", "name": "team-issues", "select_teams": "ENG, OPS",
         "new_project": "issues", "then": "on"},
    )  # fmt: skip
    assert on.status_code == 303
    held = world.store.get_source("team-issues")
    assert held.enabled is True and held.config["teams"] == ["ENG", "OPS"]
    assert held.config["projects"] == ["issues"]


def test_the_new_form_previews_before_saving_and_stays_filled(world, monkeypatch):
    """R4, part 2: the form's own Preview reads what saving would bring, stores nothing, and
    shows the result in the region a stored preview uses, the form still filled, with Save,
    off and Save and switch on below it."""
    from cairn.web.rest import ACCOUNT_KEY_UNSET

    monkeypatch.delenv(ADA_ENV, raising=False)  # unset: it reads nothing and says so
    step = main(get(world, world.ada, "/sources?account=ada-mail").text)
    _, inside = form_to(step, "/integrations/new")
    assert re.search(r'<button\b[^>]*name="then"[^>]*value="preview"[^>]*>\s*Preview\s*</button>',
                     inside)  # fmt: skip
    typed = {"credential": "ada-mail", "name": "ada-weekly", "select_label": "weekly",
             "project": "decisions", "then": "preview"}  # fmt: skip
    before = dump(world.db)
    shown = post(world, world.ada, "/integrations/new", typed)
    assert shown.status_code == 200, shown.text
    assert dump(world.db) == before
    page = main(shown.text)
    region = re.search(r'(<section\b[^>]*\bid="preview-new-region"[^>]*>)(.*?)</section>', page,
                       re.S)  # fmt: skip
    assert region, "no result region"
    tag, result = region.groups()
    assert 'aria-live="polite"' in tag and 'aria-labelledby="preview-new"' in tag
    assert re.search(r'<h3\b[^>]*\bid="preview-new"[^>]*tabindex="-1"', result)
    assert ACCOUNT_KEY_UNSET in page_text(result) and "Nothing was stored." in page_text(result)
    assert ADA_ENV not in shown.text and ADA_ADDRESS not in result  # hers: in her row only
    _, filled = form_to(page, "/integrations/new")
    assert re.search(r'name="name" value="ada-weekly"', filled)
    assert re.search(r'name="select_label" value="weekly"', filled)
    assert re.search(r'value="decisions" checked', filled)
    after = filled.partition('id="preview-new-region"')[2]
    assert "Save, off" in after and "Save and switch on" in after  # below the result
    # A refusal is saving's, on top, the form still filled.
    refused = post(world, world.ada, "/integrations/new", {**typed, "select_label": "INBOX"})
    assert refused.status_code == 400 and 'role="alert"' in refused.text
    saving = post(world, world.ada, "/integrations/new", {**typed, "select_label": "INBOX",
                                                          "then": "off"})  # fmt: skip
    alert = re.compile(r'<p class="alert"[^>]*>(.*?)</p>', re.S)
    assert alert.search(refused.text).group(1) == alert.search(saving.text).group(1)
    assert dump(world.db) == before


def test_nothing_selected_comes_back_as_an_alert_naming_the_field(world):
    before = dump(world.db)
    for typed, field, said in (
        (
            {"credential": "ada-mail", "name": "ada-empty", "select_label": " ",
             "project": "decisions"},
            "select_label",
            "What it reads: name the one label you put on the messages it reads.",
        ),
        (
            {"credential": "team-linear", "name": "team-empty", "select_teams": "",
             "select_linear_project": "", "new_project": "issues-later"},
            "select_teams",
            "What it reads: name teams or one Linear project.",
        ),
    ):  # fmt: skip
        refused = post(world, world.ada, "/integrations/new", {**typed, "then": "off"})
        assert refused.status_code == 400, refused.text
        alert = re.search(r'<p\b[^>]*role="alert"[^>]*id="([^"]+)"[^>]*>(.*?)</p>', refused.text,
                          re.S)  # fmt: skip
        assert alert and said in page_text(alert.group(2)), refused.text
        control = re.search(r'<input\b[^>]*name="' + field + r'"[^>]*>', refused.text).group(0)
        assert f'aria-describedby="{alert.group(1)}' in control and 'aria-invalid="true"' in control
        _, inside = form_to(main(refused.text), "/integrations/new")
        assert re.search(r'<input\b[^>]*name="name"[^>]*value="' + typed["name"] + '"', inside)
        if "project" in typed:
            ticked = re.search(r'<input\b[^>]*name="project"[^>]*value="decisions"[^>]*>', inside)
            assert "checked" in ticked.group(0)
        if "new_project" in typed:
            assert re.search(r'name="new_project"[^>]*value="issues-later"', inside)
    assert dump(world.db) == before


# The four endings

SWITCH_OFF = "it reads nothing until switched on; it keeps its place, its record of items"
REMOVE = "it reads nothing more; it forgets which items it wrote"
DISCONNECT = "its sources are switched off and stay listed, off; their notes stay"
FORGET = "one note at a time, from the page that cites it, or the whole project"
CHANGE = "its next notes go where you choose; what it wrote before stays where it is"


def test_switch_on_off_change_and_remove_each_say_what_they_do_first(world):
    off = section(get(world, world.ada).text, "source-ada-labelled")
    _, switch_on = form_to(off, "/integrations/ada-labelled/on")
    assert "Switch on" in switch_on
    world.store.enable_source("ada-labelled")
    page = get(world, world.ada).text
    mine = section(page, "source-ada-labelled")
    # Said once, before the sources, in the section's introduction (the same for each).
    first = page_text(page.partition('id="source-ada-labelled"')[0])
    for action, said in (
        ("/integrations/ada-labelled/off", SWITCH_OFF),
        ("/integrations/ada-labelled/on", CHANGE),
        ("/integrations/ada-labelled/remove", REMOVE),
    ):
        opening, _ = form_to(mine, action)
        assert 'method="post"' in opening and said in first, action
    assert FORGET in first and DISCONNECT in page_text(section(page, "accounts"))

    before = dump(world.db)
    asked = post(world, world.ada, "/integrations/ada-labelled/remove")  # no confirm: it asks
    assert asked.status_code == 200 and REMOVE in page_text(asked.text)
    _, confirm = form_to(asked.text, "/integrations/ada-labelled/remove")
    assert re.search(r'<label for="confirm"', confirm) and 'name="confirm"' in confirm
    wrong = post(world, world.ada, "/integrations/ada-labelled/remove", {"confirm": "other"})
    assert wrong.status_code == 400 and 'role="alert"' in wrong.text
    assert dump(world.db) == before
    done = post(world, world.ada, "/integrations/ada-labelled/remove", {"confirm": "ada-labelled"})
    assert done.status_code == 303 and done.headers["location"] == "/sources"
    assert world.store.get_source("ada-labelled") is None
    assert post(world, world.ada, "/integrations/ada-labelled/remove").status_code == 404


def test_disconnect_asks_first_and_switches_its_sources_off(world):
    world.store.enable_source("ada-labelled")
    accounts = section(get(world, world.ada).text, "accounts")
    opening, inside = form_to(accounts, "/integrations/accounts/ada-mail/disconnect")
    assert 'method="post"' in opening and "ada-mail" in inside
    assert DISCONNECT in page_text(accounts)
    before = dump(world.db)
    asked = post(world, world.ada, "/integrations/accounts/ada-mail/disconnect")
    assert asked.status_code == 200 and DISCONNECT in page_text(asked.text)
    assert 'name="confirm"' in form_to(asked.text, "/integrations/accounts/ada-mail/disconnect")[1]
    wrong = post(
        world, world.ada, "/integrations/accounts/ada-mail/disconnect", {"confirm": "nope"}
    )
    assert wrong.status_code == 400 and 'role="alert"' in wrong.text
    assert dump(world.db) == before
    done = post(
        world, world.ada, "/integrations/accounts/ada-mail/disconnect", {"confirm": "ada-mail"}
    )
    assert done.status_code == 303 and done.headers["location"] == "/sources"
    assert "ada-mail" not in [info.name for info in world.store.list_credentials()]
    held = world.store.get_source("ada-labelled")
    assert held is not None and held.enabled is False  # listed, off
    shown = section(get(world, world.ada).text, "source-ada-labelled")
    assert "disconnected" in page_text(shown)
    assert 'action="/integrations/ada-labelled/on"' not in shown
    form_to(shown, "/integrations/ada-labelled/remove")


def test_only_the_owner_of_an_account_gets_its_buttons(world):
    world.store.enable_source("ada-labelled")
    seen = get(world, world.cy)  # an admin
    assert seen.status_code == 200
    theirs = section(seen.text, "source-ada-labelled")
    actions = re.findall(r'<form\b[^>]*\baction="([^"#]*)', theirs)
    assert actions == ["/integrations/ada-labelled/off"]
    accounts = section(seen.text, "accounts")
    assert "ada-mail" not in accounts and "disconnect" not in accounts.lower()
    assert "Linear" in accounts  # the workspace's account is Cy's to use
    remove = {"confirm": "ada-labelled"}
    assert post(world, world.cy, "/integrations/ada-labelled/remove", remove).status_code == 403
    moved = post(world, world.cy, "/integrations/ada-labelled/on", {"project": "shared"})
    assert moved.status_code == 400 and 'role="alert"' in moved.text  # the store's refusal
    assert world.store.get_source("ada-labelled").enabled is True
    ada = section(get(world, world.ada).text, "source-ada-labelled")
    actions = re.findall(r'<form\b[^>]*\baction="([^"#]*)', ada)
    assert set(actions) == {
        "/integrations/ada-labelled/off",
        "/integrations/ada-labelled/preview",
        "/integrations/ada-labelled/on",
        "/integrations/ada-labelled/remove",
    }
    # R4: Brook, a member with an account of his own, does not learn another's source exists.
    assert post(world, world.brook, "/integrations/ada-labelled/remove").status_code == 404


def test_a_members_page_holds_their_own_account_and_sources_only(world):
    """R4: Brook, a member, sees his account and his source, no one else's; his source's
    forms offer the projects he manages and a new private one; no disconnect; labelled
    controls and no script."""
    world.brook.view.create_project("brook-notes", everyone="none")
    world.brook.view.add_source_from(
        "brook-in", "brook-mail", {"label": "to-me"}, {}, "brook-notes"
    )
    for path in ("/sources", "/sources?account=brook-mail"):
        page = get(world, world.brook, path)
        assert page.status_code == 200, path
        found = Controls(page.text)
        assert found.unnamed() == [] and found.scripts == 0 and found.handlers == [], path
        inside = main(page.text)
        for hidden in ("ada-mail", "ada-labelled", "team-linear", "Linear", "decisions",
                       ADA_ADDRESS, BROOK_ENV, "Other people",
                       "Owners and admins see every source", "/disconnect"):  # fmt: skip
            assert hidden not in inside, (path, hidden)
    page = get(world, world.brook).text
    mine = section(page, "source-brook-in")
    _, switch_on = form_to(mine, "/integrations/brook-in/on")
    offered = re.findall(r'name="project" value="([^"]+)"', switch_on)
    assert offered == ["brook-notes"]  # not "shared", which he writes but does not manage
    assert 'name="new_project"' in switch_on
    made = post(world, world.brook, "/integrations/brook-in/on", {"new_project": "brook-more"})
    assert made.status_code == 303
    assert world.store.get_source("brook-in").config["projects"] == ["brook-more"]


def test_a_member_cannot_disconnect_their_own_account_on_the_page(world):
    """R4: the operator registered it; an owner disconnects it."""
    before = dump(world.db)
    path = "/integrations/accounts/brook-mail/disconnect"
    refused = post(world, world.brook, path, {"confirm": "brook-mail"})
    assert refused.status_code == 403
    assert "an owner of the workspace disconnects" in page_text(refused.text).lower()
    assert dump(world.db) == before


def test_remove_of_a_source_written_where_the_admin_does_not_reach_is_404(world):
    """The owner sends a workspace account's source into her private project: the admin
    sees it, but the store removes nothing for him, so the page answers as the API: 404."""
    ada = world.ada.view
    ada.add_source_from("ada-issues", "team-linear", {"teams": ["ENG"]}, {}, None,
                        new_project="ada-own")  # fmt: skip
    assert world.cy.view.get_source("ada-issues") is not None
    before = dump(world.db)
    removed = post(world, world.cy, "/integrations/ada-issues/remove", {"confirm": "ada-issues"})
    assert removed.status_code == 404 and dump(world.db) == before
    assert world.store.get_source("ada-issues") is not None


# Follow-ups: what the store allows, said once

OPERATOR_FEED = {"url": "https://feed.example.test/items", "secret_env": "PAGE_FEED_SECRET"}
REMOVED_THERE = "The person who runs this server removes it there."


def test_remove_is_offered_only_where_it_works(world):
    world.store.add_source("ops-feed", "connector", {**OPERATOR_FEED, "projects": ["shared"]})
    page = get(world, world.ada).text
    theirs = section(page, "source-ops-feed")
    assert 'action="/integrations/ops-feed/remove"' not in theirs
    assert REMOVED_THERE in page_text(theirs)
    form_to(theirs, "/integrations/ops-feed/on")  # its other buttons as before
    form_to(section(page, "source-ada-labelled"), "/integrations/ada-labelled/remove")
    before = dump(world.db)
    refused = post(world, world.ada, "/integrations/ops-feed/remove", {"confirm": "ops-feed"})
    assert refused.status_code == 403 and dump(world.db) == before


WORDS_BEFORE = 369  # <main> for one account and one source that is on, before point 2
WORDS_AT_MOST = 330


def words_in_main(markup: str) -> int:
    return len(page_text(main(markup)).split())


def test_each_sentence_that_is_the_same_everywhere_is_said_once(world, tmp_path):
    world.store.enable_source("ada-labelled")
    world.ada.view.add_source_from("ada-second", "ada-mail", {"label": "second"}, {}, "decisions")
    world.store.enable_source("ada-second")
    page = page_text(get(world, world.ada).text)
    for said in (SWITCH_OFF, CHANGE, REMOVE, DISCONNECT, FORGET, "It stores nothing"):
        assert page.count(said) == 1, said
    accounts = section(get(world, world.ada).text, "accounts")
    buttons = re.findall(r"<button\b[^>]*>.*?</button>", accounts, re.S)
    assert len(buttons) == 3  # hers, the workspace's, and (an owner) Brook's
    assert any("ada-mail" in button for button in buttons)
    assert any("team-linear" in button for button in buttons)

    # One account and one source: shorter than before, with every ending still said.
    store = Store(tmp_path / "one.db", clock=lambda: NOW)
    ada = person(store, "Ada")
    store.create_project("decisions")
    gmail = {"credentials_env": ADA_ENV, "subject": ADA_ADDRESS}
    store.add_credential("ada-mail", "gmail", gmail, owner="ada@example.test")
    ada.view.add_source_from("ada-labelled", "ada-mail", {"label": "to-team"}, {}, "decisions")
    store.enable_source("ada-labelled")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        shown = client.get("/sources", headers=ada.cookie).text
    store.close()
    assert words_in_main(shown) <= WORDS_AT_MOST < WORDS_BEFORE
    for said in (SWITCH_OFF, CHANGE, REMOVE, DISCONNECT, FORGET, NOTHING_READ):
        assert said in page_text(shown), said


DISCONNECTED_LINE = "Its account was disconnected: it reads nothing and cannot be switched on again"


def test_a_disconnected_source_says_so_and_a_stopped_one_says_why(world):
    from cairn.store.sources import OWNER_CANNOT_WRITE, OwnerCannotWrite

    world.store.enable_source("ada-labelled")
    world.store.remove_credential("ada-mail")
    page = get(world, world.ada).text
    gone = section(page, "source-ada-labelled")
    first = re.search(r'<p class="first-line">(.*?)</p>', gone, re.S).group(1)
    assert DISCONNECTED_LINE in page_text(first)
    actions = re.findall(r'<form\b[^>]*\baction="([^"#]*)', gone)
    assert actions == ["/integrations/ada-labelled/remove"]

    # The store switched it off with a reason: the reason is next to "Off".
    world.store.add_credential(
        "cy-mail", "gmail", {"credentials_env": "PAGE_CY", "subject": "cy.r@example.test"},
        owner="cy@example.test",
    )  # fmt: skip
    world.cy.view.create_project("cy-notes")
    world.cy.view.add_source_from("cy-labelled", "cy-mail", {"label": "x"}, {}, "cy-notes")
    world.store.enable_source("cy-labelled")
    world.store.remove_member(world.cy.id)
    with pytest.raises(OwnerCannotWrite):
        world.store.claim_source("cy-labelled")
    stopped = section(get(world, world.ada).text, "source-cy-labelled")
    state = re.search(r'<p class="state is-off">(.*?)</p>', stopped, re.S).group(1)
    assert page_text(state).startswith("Off:") and OWNER_CANNOT_WRITE in page_text(state)
    assert page_text(stopped).count(OWNER_CANNOT_WRITE) == 1


def more_choices(markup: str) -> tuple[str, str]:
    """The opening tag and the inside of the new-source form's "More choices"."""
    found = re.search(r"(<details\b[^>]*>)\s*<summary>More choices</summary>(.*?)</details>",
                      markup, re.S)  # fmt: skip
    assert found, "no More choices"
    return found.group(1), found.group(2)


def test_reading_options_are_more_choices_left_to_the_kind_unless_given(world):
    world.store.add_credential("team-code", "github", {"token_env": "PAGE_GH"})
    opening, inside = more_choices(get(world, world.ada, "/sources?account=team-linear").text)
    assert " open" not in opening
    settle = re.search(r'<input\b[^>]*name="option_settle_minutes"[^>]*>', inside).group(0)
    for attribute in ('type="number"', 'min="0"', 'max="1440"', 'placeholder="30"'):
        assert attribute in settle, attribute
    assert "value=" not in settle
    for option in ("lookback_days", "backfill_days"):
        assert f'name="option_{option}"' in inside
    comments = re.search(r'<input\b[^>]*name="option_comments"[^>]*>', inside).group(0)
    assert 'type="checkbox"' in comments and "checked" not in comments
    _, drive_like = more_choices(get(world, world.ada, "/sources?account=team-code").text)
    items = re.findall(r'<input\b[^>]*name="select_items"[^>]*>', drive_like)
    assert len(items) == 3 and all('type="checkbox"' in item for item in items)

    # Left empty, nothing is sent: the kind's defaults hold.
    plain = {"credential": "team-linear", "select_teams": "ENG", "project": "shared"}
    assert post(world, world.ada, "/integrations/new", {**plain, "name": "a"}).status_code == 303
    held = world.store.get_source("a").config
    assert (held["settle_minutes"], held["comments"]) == (30, True)
    given = {**plain, "name": "b", "option_settle_minutes": "5", "option_comments": "off"}
    given["select_teams"] = "OPS"  # a second source of one team is refused (R3)
    assert post(world, world.ada, "/integrations/new", given).status_code == 303
    held = world.store.get_source("b").config
    assert (held["settle_minutes"], held["comments"]) == (5, False)
    code = {"credential": "team-code", "name": "c", "select_repo": "quill/quill",
            "select_items": ["pulls", "releases"], "project": "shared"}  # fmt: skip
    assert post(world, world.ada, "/integrations/new", code).status_code == 303
    assert world.store.get_source("c").config["items"] == ["pulls", "releases"]

    # A refusal that names one opens it, points at that field and keeps what was typed.
    before = dump(world.db)
    wrong = {**plain, "name": "d", "option_settle_minutes": "5000"}
    refused = post(world, world.ada, "/integrations/new", wrong)
    assert refused.status_code == 400 and "settle_minutes is a whole number" in refused.text
    opening, inside = more_choices(refused.text)
    assert " open" in opening
    settle = re.search(r'<input\b[^>]*name="option_settle_minutes"[^>]*>', inside).group(0)
    assert 'value="5000"' in settle and 'aria-describedby="refused' in settle
    assert dump(world.db) == before


OTHERS = "Other people's accounts in this workspace"


def test_an_owner_sees_other_peoples_accounts_with_disconnect_only(world):
    accounts = section(get(world, world.ada).text, "accounts")
    assert OTHERS in page_text(accounts)
    others = accounts.partition(OTHERS)[2]
    assert "Brook" in page_text(others) and "Gmail" in page_text(others)
    assert "0 sources" in page_text(others)
    assert "brook.ng@example.test" not in accounts  # no subject but in its owner's own row
    actions = re.findall(r'<form\b[^>]*\baction="([^"#]*)', others)
    assert actions == ["/integrations/accounts/brook-mail/disconnect"]
    button = re.search(r"<button\b[^>]*>.*?</button>", others, re.S).group(0)
    assert "brook-mail" in button or "Brook" in button
    step = get(world, world.ada, "/sources?account=brook-mail").text  # nobody else makes one
    assert 'value="brook-mail"' not in main(step) and "/integrations/new" not in step
    assert OTHERS not in get(world, world.cy).text  # an admin
    done = post(
        world, world.ada, "/integrations/accounts/brook-mail/disconnect", {"confirm": "brook-mail"}
    )
    assert done.status_code == 303
    assert "brook-mail" not in [info.name for info in world.store.list_credentials()]


HELD_SAID = (
    "These carry what another of your sources reads too, so neither wrote them. Take one of"
    " the two labels off, or switch one source off, and the next run writes each once."
)
HELD_LINK = "https://tracker.example.test/items/held-7"


def ran_holding(world, name: str, held: list[dict], more: int = 0, expired: int = 0) -> None:
    """A run of `name` that held `held` (each {title, link, project}), as the runner keeps
    it (cairn.sources.runner.SyncReport): the lists only when there is one."""
    world.store.enable_source(name)
    claim = world.store.claim_source(name)
    report = {"saved": 0, "skipped": 0, "failed": 0}
    if held:
        report["held_items"], report["held_more"] = held, more
    if expired:
        report["held_expired"] = expired
    assert world.store.finish_source_run(claim, None, report)


PRIVATE_HOST = "lf.ops-private.example.test"


def test_skip_reasons_and_declined_links_hide_the_accounts_address(world):
    """A source made from an account whose base_url is a private host: what its last run
    skipped and declined is said without that host, as its error is."""
    lf = {"base_url": f"https://{PRIVATE_HOST}", "public_key_env": "PAGE_LF_P",
          "secret_key_env": "PAGE_LF_S"}  # fmt: skip
    world.store.add_credential("team-langfuse", "langfuse", lf)
    world.ada.view.add_source_from("scores", "team-langfuse", {"score_name": "ok"}, {}, "shared")
    world.store.enable_source("scores")
    claim = world.store.claim_source("scores")
    link = f"https://{PRIVATE_HOST}/project/p1/traces/t1"
    report = {
        "saved": 0, "skipped": 1, "refused": 1,
        "reasons": {f"{PRIVATE_HOST.upper()} answered 502": 1},
        "declined": [{"link": link, "status": "refused", "codes": ["C1"], "project": None}],
    }  # fmt: skip
    assert world.store.finish_source_run(claim, None, report)
    shown = section(get(world, world.ada).text, "source-scores")
    assert PRIVATE_HOST not in shown.casefold() and "ops-private" not in shown
    assert "answered 502" in page_text(shown) and "refused: C1" in page_text(shown)


def held_part(markup: str, name: str) -> str:
    """What the source's section says from its "Held" heading on."""
    shown = section(markup, f"source-{name}")
    assert "<h3>Held</h3>" in shown, name
    return shown.partition("<h3>Held</h3>")[2]


def test_a_source_with_held_items_lists_them_under_held_with_the_sentence_once(world):
    decisions = world.store.get_project("decisions").id
    ran_holding(
        world,
        "ada-labelled",
        [
            {"title": "Budget for the spring", "link": "gmail:held0001", "project": decisions},
            {"title": "Board minutes draft", "link": HELD_LINK, "project": decisions},
        ],
    )
    page = get(world, world.ada).text
    held = held_part(page, "ada-labelled")
    assert "Budget for the spring" in page_text(held) and "Board minutes draft" in page_text(held)
    assert page_text(page).count(HELD_SAID) == 1
    assert HELD_SAID in page_text(held)
    links = re.findall(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', held, re.S)
    assert links == [(HELD_LINK, "Board minutes draft")]  # a gmail: ref has no address
    assert "gmail:held0001" not in page
    found = Controls(page)
    assert found.unnamed() == [] and found.scripts == 0 and found.handlers == []


def test_held_more_and_the_ones_that_left_the_window_are_said(world):
    decisions = world.store.get_project("decisions").id
    one = [{"title": "Budget for the spring", "link": "gmail:held0001", "project": decisions}]
    ran_holding(world, "ada-labelled", one, more=3, expired=2)
    held = page_text(held_part(get(world, world.ada).text, "ada-labelled"))
    assert "and 3 more" in held
    assert "2 held items left the look-back window unwritten." in held


def test_a_viewer_who_does_not_reach_the_project_sees_none_of_the_titles(world):
    private = world.ada.view.create_project("ada-private", everyone="none")
    ran_holding(
        world,
        "ada-labelled",
        [{"title": "Salary review notes", "link": HELD_LINK, "project": private.id}],
    )
    assert "Salary review notes" in get(world, world.ada).text  # the owner reaches it
    page = get(world, world.cy).text  # an admin who does not
    assert "Salary review notes" not in page and HELD_LINK not in page
    assert "<h3>Held</h3>" not in section(page, "source-ada-labelled")


def test_a_source_with_no_held_items_shows_no_held_heading(world):
    ran_holding(world, "ada-labelled", [])
    page = get(world, world.ada).text
    assert "<h3>Held</h3>" not in page and HELD_SAID not in page_text(page)


def test_a_member_learns_only_that_a_name_is_taken_on_the_page(world):
    """R4, second reading: Ada's source "ada-labelled" is out of Brook's sight, so the page
    tells him the name is taken and nothing more; a preview takes no name."""
    from cairn.store.sources import NAME_TAKEN

    world.brook.view.create_project("brook-notes", everyone="none")
    typed = {"credential": "brook-mail", "select_label": "to-me", "project": "brook-notes"}
    before = dump(world.db)
    for name in ("ada-labelled", "ADA-LABELLED"):
        refused = post(
            world, world.brook, "/integrations/new", {**typed, "name": name, "then": "off"}
        )
        assert refused.status_code == 400, refused.text
        alert = re.search(r'<p\b[^>]*role="alert"[^>]*id="([^"]+)"[^>]*>(.*?)</p>', refused.text,
                          re.S)  # fmt: skip
        said = page_text(alert.group(2))
        assert NAME_TAKEN in said and "labelled" not in said.casefold(), said
        control = re.search(r'<input\b[^>]*name="name"[^>]*>', refused.text).group(0)
        assert f'aria-describedby="{alert.group(1)}' in control and 'aria-invalid="true"' in control
        shown = post(world, world.brook, "/integrations/new", {**typed, "name": name,
                                                               "then": "preview"})  # fmt: skip
        assert shown.status_code == 200 and NAME_TAKEN not in shown.text, shown.text
    assert dump(world.db) == before


def test_a_held_account_answers_409_on_the_page_naming_the_account(world):
    """R4, second reading: a preview holds the account it reads, so the page's 409 points at
    the account, not at the name, and keeps what was typed."""
    from cairn.sources import runner

    world.brook.view.create_project("brook-notes", everyone="none")
    typed = {"credential": "brook-mail", "name": "mine", "select_label": "to-me",
             "project": "brook-notes", "then": "preview"}  # fmt: skip
    held = (world.store.account_id, "account:brook-mail")
    runner._previewing.add(held)
    try:
        busy = post(world, world.brook, "/integrations/new", typed)
    finally:
        runner._previewing.discard(held)
    assert busy.status_code == 409, busy.text
    alert = re.search(r'<p\b[^>]*role="alert"[^>]*>(.*?)</p>', busy.text, re.S)
    assert page_text(alert.group(1)) == f"Refused. Which account: {runner.PREVIEW_BUSY}."
    control = re.search(r'<input\b[^>]*name="name"[^>]*>', busy.text).group(0)
    assert 'aria-invalid="true"' not in control and 'value="mine"' in control
    assert post(world, world.brook, "/integrations/new", typed).status_code == 200


def test_the_buttons_are_explained_only_when_there_is_a_source(world):
    """With no source there is no button to explain: the page is short until one exists."""
    world.ada.view.remove_source("ada-labelled")
    bare = main(get(world, world.ada).text)
    assert "What each button does" not in bare and "Switch off:" not in page_text(bare)
    assert "Choose something new" in bare  # the form is still there
    world.ada.view.add_source_from("again", "ada-mail", {"label": "to-team"}, {}, "decisions")
    assert "What each button does" in main(get(world, world.ada).text)
