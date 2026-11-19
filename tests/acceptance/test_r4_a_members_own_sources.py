"""R4 acceptance tests: a member manages the sources of their own account.

The operator registers an account for a member (`credential-add --for <their address>`). That
member then reaches the Sources page and sees their own accounts and the sources made from
them, and nothing else: no other person's account or source, not the workspace's accounts, not
a source added on the command line, not their existence or their count. From their own account
they make a source into a project they may manage (one they made, one whose rule gives them
"manage", or a new private one), switch it on and off, change where it writes under the same
rule, preview it and remove it. They cannot disconnect the account (an owner does), cannot
touch another person's source, and cannot use the workspace's accounts. A member with no
account of their own sees the page with its one sentence and "No account yet. Ask the person
who runs this server to connect one.", and no form.

The same holds over the API with a key that acts for that member: GET /api/credentials, GET and
POST /api/integrations, /on, /off, DELETE and /preview answer their own only; anything else is
answered as not found, in the words an owner gets for a name that does not exist.

Every name here is made up; every request goes to an httpx.MockTransport.
"""

import asyncio
import html as html_text
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.sources import run_source
from cairn.store import Store
from cairn.store.sources import NOT_A_SOURCE_MANAGER
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
ISSUER = "https://id.example.test/pool-r4"
SECRET_ENV = "R4_FEED_SECRET"
SECRET = "r4" + "-made-up-" + "feed-" + "secret-91ad"  # never a real secret
BROOK_URL = "https://brook-feed.example.test/cairn/feed"
DESK_URL = "https://brook-desk.example.test/cairn/feed"
ADA_URL = "https://ada-feed.example.test/cairn/feed"
TEAM_URL = "https://team-feed.example.test/cairn/feed"
OPERATOR_URL = "https://minutes.example.test/cairn/feed"
NOW = datetime.now(UTC).replace(microsecond=0)  # the web preview reads the real time
DAY, MINUTE = timedelta(days=1), timedelta(minutes=1)
NOTHING_READ = "Nothing is read until you choose what and where."
NO_ACCOUNT = "No account yet. Ask the person who runs this server to connect one."
# What Brook must never learn of: other people's and the workspace's accounts and sources,
# the command line's source, the projects he does not reach, and every address.
NOT_BROOKS = (
    "ada-feed", "team-feed", "ada-board", "team-ledger", "minutes", "board",
    ADA_URL, TEAM_URL, OPERATOR_URL, "ada-feed.example.test", "team-feed.example.test",
    "minutes.example.test", "Other people", "Ada's",
)  # fmt: skip


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def z(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def entry(n: int, at: datetime) -> dict:
    return {
        "id": f"BRK-{n}",
        "text": f"# Brook entry {n}\n\nEntry {n} records the stand-up of day {n}.\n",
        "title": f"Brook entry {n}",
        "at": z(at),
    }


class Feed:
    """Connectors of protocol 1 behind httpx.MockTransport: the bearer checked, the items at or
    after `since`, and every request kept with its host."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.requests: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.host)
        if request.headers.get("authorization") != f"Bearer {SECRET}":
            return httpx.Response(401, json={"error": "the secret was refused"})
        since = datetime.fromisoformat(json.loads(request.content)["since"].replace("Z", "+00:00"))
        given = [
            i for i in self.items if datetime.fromisoformat(i["at"].replace("Z", "+00:00")) >= since
        ]
        return httpx.Response(
            200, json={"protocol": 1, "items": given, "cursor": "after:all", "more": False}
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


def person(store: Store, name: str, role: str | None = None) -> SimpleNamespace:
    signed = store.sign_in(
        "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER, join="default"
    )
    if role is not None:
        store.set_role(signed.user.id, role)
    view = store.for_account(signed.account_id, signed.user.id)
    return SimpleNamespace(
        id=signed.user.id,
        cookie={"cookie": f"{session_cookie()}={signed.token}"},
        view=view.acting_as("session", name, signed.user.id),
    )


def member_key(store: Store, who: SimpleNamespace, label: str) -> str:
    """A key that acts for a member: made while they were an admin, then they are a member."""
    store.set_role(who.id, "admin")
    key = who.view.create_key(label)
    store.set_role(who.id, "member")
    return key


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Ada (the owner), Brook (a member with two accounts the operator registered for him), Dee
    (a member with none), Cy (an admin); Ada's own account and the workspace's, each with a
    source; a source added on the command line. Projects: "ledger" (every member writes),
    "board" (Ada's, private), "brook-notes" (Brook made it), "stand-ups" (its rule gives Brook
    "manage")."""
    monkeypatch.setenv(SECRET_ENV, SECRET)
    clock = Clock(NOW)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    ada, brook, dee = person(store, "Ada"), person(store, "Brook"), person(store, "Dee")
    cy = person(store, "Cy", role="admin")
    store.create_project("ledger")
    store.create_project("board", everyone="none")
    brook.view.create_project("brook-notes", everyone="none")
    ups = store.create_project("stand-ups", everyone="read")
    store.set_project_access(ups.id, people={brook.id: "manage"})
    for name, url, owner in (
        ("brook-feed", BROOK_URL, "brook@example.test"),
        ("brook-desk", DESK_URL, "brook@example.test"),
        ("ada-feed", ADA_URL, "ada@example.test"),
        ("team-feed", TEAM_URL, None),
    ):
        store.add_credential(name, "connector", {"url": url, "secret_env": SECRET_ENV}, owner=owner)
    ada.view.add_source_from("ada-board", "ada-feed", {}, {}, "board")
    store.add_source_from("team-ledger", "team-feed", {}, {}, "ledger")
    store.add_source(
        "minutes", "connector", {"url": OPERATOR_URL, "secret_env": SECRET_ENV, "projects": []}
    )
    feed = Feed()
    keys = {
        "ada": ada.view.create_key("ada-desk"),
        "brook": member_key(store, brook, "brook-desk"),
        "dee": member_key(store, dee, "dee-desk"),
    }
    app = create_app(store, public_url=BASE, source_http=feed.client)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(
            store=store, db=db, clock=clock, feed=feed, client=client, keys=keys,
            ada=ada, brook=brook, dee=dee, cy=cy,
        )  # fmt: skip
    store.close()


def get(world, who, path: str = "/sources") -> httpx.Response:
    return world.client.get(path, headers=who.cookie)


def post(world, who, path: str, data: dict | None = None) -> httpx.Response:
    return world.client.post(
        path, data=data or {}, headers={**SAME_ORIGIN, **who.cookie}, follow_redirects=False
    )


def call(world, who: str, method: str, path: str, **kwargs) -> httpx.Response:
    return world.client.request(method, path, headers={"x-api-key": world.keys[who]}, **kwargs)


def main(markup: str) -> str:
    return markup.partition('<main id="main" tabindex="-1">')[2].partition("</main>")[0]


def page_text(markup: str) -> str:
    return " ".join(html_text.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def actions(markup: str) -> list[str]:
    return re.findall(r'<form\b[^>]*\baction="([^"#]*)', main(markup))


def make_brooks(world, name: str = "brook-in", project: str = "brook-notes") -> None:
    made = post(
        world, world.brook, "/integrations/new",
        {"credential": "brook-feed", "name": name, "project": project, "then": "off"},
    )  # fmt: skip
    assert made.status_code == 303, made.text


# The page


def test_a_member_sees_their_own_accounts_and_sources_and_nothing_else(world):
    make_brooks(world)
    page = get(world, world.brook)
    assert page.status_code == 200
    text = page_text(main(page.text))
    assert NOTHING_READ in text
    assert "brook-feed" in page.text and "brook-desk" in page.text and "brook-in" in text
    assert "From your connector" in text
    for hidden in (*NOT_BROOKS, BROOK_URL, DESK_URL, SECRET_ENV):
        assert hidden not in page.text, hidden
    # No count of anything but his own: two accounts, one source.
    assert len(re.findall(r'<section class="integration"', page.text)) == 1
    assert 'id="account-' in page.text and page.text.count('id="account-') == 2
    # His source's buttons, his accounts in the form; never a disconnect.
    for action in ("/integrations/brook-in/on", "/integrations/brook-in/preview",
                   "/integrations/brook-in/remove"):  # fmt: skip
        assert action in actions(page.text), action
    assert not [a for a in actions(page.text) if "/disconnect" in a]
    hub = get(world, world.brook, "/workspace")
    assert 'href="/sources"' in hub.text


def test_a_member_makes_switches_moves_previews_and_removes_their_own_source(world):
    # Made into the project he made: off, whatever the kind.
    make_brooks(world)
    made = world.store.get_source("brook-in")
    assert (made.enabled, made.config["projects"]) == (False, ["brook-notes"])
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor) == ("source.added", "Brook")

    # A preview of it, on the page.
    world.feed.items = [entry(n, NOW - DAY + n * MINUTE) for n in (1, 2)]
    before = dump(world.db)
    shown = post(world, world.brook, "/integrations/brook-in/preview", {"days": "3"})
    assert shown.status_code == 200, shown.text
    region = re.search(r'id="preview-brook-in-region"[^>]*>(.*?)</section>', shown.text, re.S)
    assert region and "Brook entry 1" in region.group(1) and "Brook entry 2" in region.group(1)
    assert dump(world.db) == before
    assert world.feed.requests == ["brook-feed.example.test"]

    # Switched on, it runs into his project.
    on = post(world, world.brook, "/integrations/brook-in/on", {"project": "brook-notes"})
    assert on.status_code == 303 and world.store.get_source("brook-in").enabled is True
    world.feed.items = [entry(3, NOW + MINUTE)]
    world.clock.now = NOW + 2 * MINUTE
    ran = asyncio.run(_run(world, "brook-in"))
    assert ran.saved == 1
    assert world.store.count_notes(world.store.get_project("brook-notes").id) == 1

    # Moved to a project whose rule gives him "manage"; not where he only writes.
    moved = post(world, world.brook, "/integrations/brook-in/on", {"project": "stand-ups"})
    assert moved.status_code == 303
    assert world.store.get_source("brook-in").config["projects"] == ["stand-ups"]
    before = dump(world.db)
    for data, status in (({"project": "ledger"}, 400), ({"project": "board"}, 400)):
        refused = post(world, world.brook, "/integrations/brook-in/on", data)
        assert refused.status_code == status, (data, refused.text)
        assert 'role="alert"' in refused.text
    assert dump(world.db) == before

    # Off, then removed, asked first.
    off = post(world, world.brook, "/integrations/brook-in/off")
    assert off.status_code == 303 and world.store.get_source("brook-in").enabled is False
    asked = post(world, world.brook, "/integrations/brook-in/remove")
    assert asked.status_code == 200 and "brook-in" in asked.text
    gone = post(world, world.brook, "/integrations/brook-in/remove", {"confirm": "brook-in"})
    assert gone.status_code == 303 and world.store.get_source("brook-in") is None
    assert world.store.count_notes(world.store.get_project("brook-notes").id) == 1  # notes stay

    # A new private project of his own, from his other account, and switched on at once.
    fresh = post(
        world, world.brook, "/integrations/new",
        {"credential": "brook-desk", "name": "desk-in", "new_project": "brook-desk-notes",
         "then": "on"},
    )  # fmt: skip
    assert fresh.status_code == 303, fresh.text
    source = world.store.get_source("desk-in")
    assert (source.enabled, source.config["projects"]) == (True, ["brook-desk-notes"])
    assert world.brook.view.get_project("brook-desk-notes") is not None
    assert world.dee.view.get_project("brook-desk-notes") is None  # private


def test_a_member_cannot_disconnect_or_touch_anothers_source_or_the_workspaces(world):
    make_brooks(world)
    world.store.enable_source("team-ledger")
    before = dump(world.db)
    refusals = [
        ("/integrations/accounts/brook-feed/disconnect", {"confirm": "brook-feed"}, 403),
        ("/integrations/accounts/ada-feed/disconnect", {"confirm": "ada-feed"}, 404),
        ("/integrations/accounts/team-feed/disconnect", {"confirm": "team-feed"}, 404),
        ("/integrations/new", {"credential": "team-feed", "name": "x", "project": "brook-notes"},
         404),
        ("/integrations/new", {"credential": "ada-feed", "name": "x", "project": "brook-notes"},
         404),
    ]  # fmt: skip
    for name in ("ada-board", "team-ledger", "minutes"):
        refusals += [
            (f"/integrations/{name}/on", {"project": "brook-notes"}, 404),
            (f"/integrations/{name}/off", {}, 404),
            (f"/integrations/{name}/preview", {}, 404),
            (f"/integrations/{name}/remove", {"confirm": name}, 404),
        ]
    for path, data, status in refusals:
        answer = post(world, world.brook, path, data)
        assert answer.status_code == status, (path, answer.status_code)
        for hidden in (ADA_URL, TEAM_URL, OPERATOR_URL):
            assert hidden not in answer.text
        assert dump(world.db) == before, path
    assert world.feed.requests == []
    assert world.store.get_source("team-ledger").enabled is True
    # Not found, in the words of a name that does not exist.
    ghost = page_text(main(post(world, world.ada, "/integrations/ghost/off").text))
    hidden = page_text(main(post(world, world.brook, "/integrations/ada-board/off").text))
    assert hidden == ghost.replace("ghost", "ada-board")


def test_a_member_with_no_account_sees_one_sentence_and_no_form(world):
    page = get(world, world.dee)
    assert page.status_code == 200
    inside = main(page.text)
    assert "<form" not in inside
    text = page_text(inside)
    assert NOTHING_READ in text and NO_ACCOUNT in text
    for hidden in (*NOT_BROOKS, "brook-feed", "brook-desk", "Choose something new"):
        assert hidden not in page.text, hidden
    # No source and no switch for a source that is not theirs, as before.
    before = dump(world.db)
    for path, data, status in (
        ("/integrations/minutes/on", {"project": "ledger"}, 403),
        ("/integrations/team-ledger/off", {}, 403),
        ("/integrations/ada-board/preview", {}, 403),
        ("/integrations/new", {"credential": "team-feed", "name": "x", "project": "ledger"}, 404),
    ):
        assert post(world, world.dee, path, data).status_code == status, path
    assert dump(world.db) == before
    with pytest.raises(PermissionError) as refused:
        world.dee.view.disable_source("team-ledger")
    assert str(refused.value) == NOT_A_SOURCE_MANAGER
    assert world.dee.view.list_sources() == [] and world.dee.view.list_credentials() == []


# The API


def test_the_same_over_the_api_with_a_key_that_acts_for_the_member(world):
    credentials = call(world, "brook", "GET", "/api/credentials")
    assert credentials.status_code == 200
    assert [c["name"] for c in credentials.json()["credentials"]] == ["brook-desk", "brook-feed"]
    body = {
        "name": "brook-in", "credential": "brook-feed", "select": {}, "options": {},
        "project": "brook-notes", "new_project": None,
    }  # fmt: skip
    made = call(world, "brook", "POST", "/api/integrations", json=body)
    assert made.status_code == 201, made.text
    assert (made.json()["enabled"], made.json()["credential"]) == (False, "brook-feed")
    listed = call(world, "brook", "GET", "/api/integrations")
    assert [s["name"] for s in listed.json()["sources"]] == ["brook-in"]
    for hidden in (*NOT_BROOKS, BROOK_URL, SECRET_ENV):
        assert hidden not in listed.text and hidden not in credentials.text, hidden

    world.feed.items = [entry(1, NOW - DAY)]
    shown = call(world, "brook", "POST", "/api/integrations/brook-in/preview", json={"days": 2})
    assert shown.status_code == 200 and shown.json()["titles"] == ["Brook entry 1"], shown.text
    on = call(world, "brook", "POST", "/api/integrations/brook-in/on",
              json={"projects": ["stand-ups"]})  # fmt: skip
    assert on.status_code == 200 and on.json()["projects"] == ["stand-ups"], on.text
    assert call(world, "brook", "POST", "/api/integrations/brook-in/off").json()["enabled"] is False
    nope = call(world, "brook", "POST", "/api/integrations/brook-in/on",
                json={"projects": ["ledger"]})  # fmt: skip
    assert nope.status_code == 403  # he writes "ledger" but does not manage it

    # Anything else: not found, in the words an owner gets for a name that does not exist.
    before = dump(world.db)
    world.feed.requests.clear()
    for name in ("ada-board", "team-ledger", "minutes"):
        asked = [
            ("POST", f"/api/integrations/{name}/on", {"json": {"projects": ["brook-notes"]}}),
            ("POST", f"/api/integrations/{name}/off", {}),
            ("POST", f"/api/integrations/{name}/preview", {"json": {}}),
            ("DELETE", f"/api/integrations/{name}", {"params": {"confirm": name}}),
        ]
        for method, path, kwargs in asked:
            hidden = call(world, "brook", method, path, **kwargs)
            ghost = call(world, "ada", method, path.replace(name, "ghost"),
                         **json.loads(json.dumps(kwargs).replace(name, "ghost")))  # fmt: skip
            assert ghost.status_code == 404, (path, ghost.text)
            assert hidden.status_code == 404, (path, hidden.text)
            assert hidden.json() == {"error": ghost.json()["error"].replace("ghost", name)}
    for credential in ("ada-feed", "team-feed"):
        hidden = call(world, "brook", "POST", "/api/integrations",
                      json={**body, "name": "x", "credential": credential})  # fmt: skip
        ghost = call(world, "ada", "POST", "/api/integrations",
                     json={**body, "name": "x", "credential": "ghost"})  # fmt: skip
        assert hidden.status_code == ghost.status_code == 404
        assert hidden.json() == {"error": ghost.json()["error"].replace("ghost", credential)}
        hidden = call(world, "brook", "DELETE", f"/api/credentials/{credential}",
                      params={"confirm": credential})  # fmt: skip
        assert hidden.status_code == 404
    refused = call(world, "brook", "DELETE", "/api/credentials/brook-feed",
                   params={"confirm": "brook-feed"})  # fmt: skip
    assert refused.status_code == 403
    assert dump(world.db) == before and world.feed.requests == []

    gone = call(world, "brook", "DELETE", "/api/integrations/brook-in",
                params={"confirm": "brook-in"})  # fmt: skip
    assert gone.status_code == 200 and world.store.get_source("brook-in") is None

    # A member with no account: no source and none of the workspace's.
    assert call(world, "dee", "GET", "/api/integrations").json() == {"sources": []}
    assert call(world, "dee", "GET", "/api/credentials").json() == {"credentials": []}
    refused = call(world, "dee", "POST", "/api/integrations/minutes/off")
    assert (refused.status_code, refused.json()) == (403, {"error": NOT_A_SOURCE_MANAGER})


def test_a_member_previews_a_new_source_before_saving_it(world):
    """The new-source form's Preview, for a member from his own account into a new private
    project of his: what saving would bring, nothing stored, the project not made."""
    world.feed.items = [entry(n, NOW - DAY + n * MINUTE) for n in (1, 2)]
    typed = {"credential": "brook-desk", "name": "desk-in", "new_project": "brook-later",
             "days": "3", "then": "preview"}  # fmt: skip
    before = dump(world.db)
    shown = post(world, world.brook, "/integrations/new", typed)
    assert shown.status_code == 200, shown.text
    region = re.search(r'id="preview-new-region"[^>]*>(.*?)</section>', shown.text, re.S)
    assert region and "Brook entry 1" in region.group(1) and "Brook entry 2" in region.group(1)
    assert "2 new in the last 3 days" in page_text(region.group(1))
    assert dump(world.db) == before and world.feed.requests == ["brook-desk.example.test"]
    assert world.store.get_project("brook-later") is None
    refused = post(world, world.brook, "/integrations/new", {**typed, "credential": "team-feed"})
    assert refused.status_code == 404 and dump(world.db) == before


async def _run(world, name: str):
    async with world.feed.client() as http:
        return await run_source(world.store, name, http=http, env={SECRET_ENV: SECRET},
                                clock=world.clock)  # fmt: skip
