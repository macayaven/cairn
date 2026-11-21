"""T2 acceptance tests (frozen): a project shared with a list of people, at three levels.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/store/__init__.py (the store's contract, Who
reaches a project) and src/cairn/server.py (the web view's /b/<project>/access).

A third level
- A person's level on a project is "read", "write" or "manage". "manage" is everything "write"
  is, and one thing more: the person decides who reaches that project. The stored word is
  "manage", not "admin": "admin" is a workspace role (a workspace admin manages people, keys
  and sources, and reaches projects as a member does).
- `view.set_project_access(project_id, *, everyone=None, people=None)` accepts "manage" as a
  person's level. `everyone` stays "write", "read" or "none": everyone="manage" is a
  ValueError, and so is a person's level that is not one of the three. Nothing is written.
- Who may read a project's rule (`project_access`) and change it (`set_project_access`): its
  maker, the workspace's owners, a view acting for no person, and anyone whose own level on
  that project is "manage". Everyone else as before: PermissionError, and KeyError for a project
  out of reach. A workspace admin who is not the maker and has no "manage" level may not.
- A person at "manage" may give and take any level, "manage" included, their own included.
  The maker and the owners reach the project whatever the list says: an entry cannot limit
  them.
- "manage" does not delete the project and does not forget a note: both stay with the maker
  and the owners (decision 59).
- A person reaches a project with the most any of these gives: owner, maker, their own entry,
  `everyone`. `project_sharing` counts a person at "manage" as someone who reaches it.

Several people at once
- `view.share_project(project_id, level, emails) -> Shared`: `level` is "read", "write" or
  "manage", `emails` a sequence of addresses. `Shared.granted` is a tuple of the user ids
  now at that level, in the order given, each once; `Shared.unknown` a tuple of the
  addresses, as given and stripped, that belong to no member of this workspace. It sets that
  level for every address that is a member's (compared ignoring case and outer spaces) and
  leaves every other person's entry and `everyone` as they were. The maker's and an owner's
  address are granted and write no entry. Unknown addresses change nothing and are not
  invited.
- The same callers as `set_project_access`. One project.access_changed audit event for a call
  that changed something, none for a call that changed nothing. ValueError for a level not
  one of the three, an empty list, more than 200 addresses, or an entry that is not one
  address (blank, no "@", a space inside); the message never repeats an address. `unknown`
  only repeats what the caller typed.

The web view
- GET /b/<project>/access: each person's select offers "Read only", "Read and write" and a
  third level whose words say it adds deciding who reaches the project (never "Admin" alone);
  one sentence says how that differs from a workspace admin. Someone at "manage" gets the
  page and may post it; someone at "write" or "read" gets 403.
- POST /b/<project>/access is unchanged in shape and accepts "manage" as a level.
- "Add several people": a form on that page with a textarea `emails` (addresses separated by
  commas, semicolons, spaces or new lines) and a select `level`, posting to
  /b/<project>/share. Every address a member's: 303 back to the access page. Some not: 200,
  the access page with a notice listing those addresses as typed (escaped) and saying they
  must be invited to the workspace first; the members among them were still given the
  level. 400 for an empty or unusable list or a bad level; 403 and 404 as the access page.
- A project's page links "Who reaches it" for someone at "manage", as for its maker.

What stays
- A credential acts for the person who made it, and management is refused on any connection
  limited to some projects or to reading: a narrowed view is refused `set_project_access` and
  `share_project`. The REST API and the MCP tools have no call that changes who reaches a
  project.
- Export and import carry no access rules (decision 49).
- The rows a database holds in project_people before this step are all there after it, each at
  its level (SQLite cannot change a CHECK in place, so the table is rebuilt).
"""

import base64
import hashlib
import html
import io
import re
import secrets
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app
from cairn.store import DEFAULT_ACCOUNT_ID, Store
from cookie_names import session_cookie

BASE = "https://cairn.example.test"
ISSUER = "https://id.example.test/pool-1"
REDIRECT = "http://127.0.0.1:33418/callback"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
SECRET_PLAN = "# Launch plan\n\nProject Heron ships on 2027-03-01.\n"
HANDBOOK = "# Handbook\n\nReleases go out on Tuesdays.\n"
NOWHERE = "brn_000000000000"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def address(name: str) -> str:
    return f"{name.lower()}@example.test"


class World:
    """One shared workspace (the default one) and the people in it, made through the store.
    Ada signs in first and owns it; everyone after her arrives as a member."""

    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "cairn.db"
        self.store = Store(self.db)
        self.app = create_app(self.store, public_url=BASE)
        self.account = self.store.account_id
        self.operator = self.store.for_account(self.account)  # acts for no person

    def person(self, name: str, *, join: str = "default") -> SimpleNamespace:
        signed = self.store.sign_in(
            "sso", f"sub-{name}", address(name), name, issuer=ISSUER, join=join
        )
        return SimpleNamespace(
            id=signed.user.id,
            name=name,
            email=address(name),
            cookie={"cookie": f"{session_cookie('https')}={signed.token}"},
            account=signed.account_id,
            view=self.store.for_account(signed.account_id, signed.user.id),
        )

    def agent(self, who: SimpleNamespace, name: str = "laptop") -> str:
        """An agent `who` approved, as an access token."""
        client = self.store.register_client(name, [REDIRECT])
        verifier = secrets.token_urlsafe(32)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        code = who.view.create_code(client.id, REDIRECT, challenge)
        return self.store.redeem_code(code, client.id).access_token

    def changes(self) -> list:
        """The project.access_changed events of the workspace, newest first."""
        events = self.operator.list_audit(limit=500)
        return [event for event in events if event.action == "project.access_changed"]

    def close(self) -> None:
        self.store.close()


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.close()


@pytest.fixture
def team(world):
    """Ada owns the workspace, Brook is an admin, Cy, Dee, Eli and Fay are members. Ada made
    handbook (every member writes); Cy made launch, private, with one compiled note."""
    ada, brook, cy, dee, eli, fay = (
        world.person(name) for name in ("Ada", "Brook", "Cy", "Dee", "Eli", "Fay")
    )
    ada.view.set_role(brook.id, "admin")
    handbook = ada.view.create_project("handbook")
    launch = cy.view.create_project("launch", everyone="none")
    ada.view.add_note(handbook.id, HANDBOOK)
    cy.view.add_note(launch.id, SECRET_PLAN)
    for view, project in ((ada.view, handbook), (cy.view, launch)):
        for note in view.list_notes(project.id):
            assert compile_note(view, note).status == "saved"
    return SimpleNamespace(
        world=world,
        ada=ada,
        brook=brook,
        cy=cy,
        dee=dee,
        eli=eli,
        fay=fay,
        handbook=handbook,
        launch=launch,
    )


def names(who) -> list[str]:
    return sorted(project.name for project in who.view.list_projects())


def kind_of(action, *arguments) -> str:
    """How a call ends: "ok", or the name of what it raised."""
    try:
        action(*arguments)
    except Exception as error:
        return type(error).__name__
    return "ok"


def tool_names(client, credential: dict) -> list[str]:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    r = client.post("/mcp", headers={**MCP_HEADERS, **credential}, json=body)
    assert r.status_code == 200, r.text
    return [tool["name"] for tool in r.json()["result"]["tools"]]


def text_of(page: str) -> str:
    """A page's words, without tags, entities read, spaces collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def options_for(page: str, user_id: str) -> dict[str, str]:
    """The options of a person's select on the access page: level -> its words."""
    found = re.findall(
        r'<option\b[^>]*\bvalue="' + re.escape(user_id) + r':([a-z]+)"[^>]*>(.*?)</option>',
        page,
        flags=re.DOTALL,
    )
    return {level: " ".join(html.unescape(words).split()) for level, words in found}


# A third level


def test_a_person_at_manage_decides_who_reaches_the_project(team):
    world, ada, cy, dee, eli, fay = team.world, team.ada, team.cy, team.dee, team.eli, team.fay
    launch = team.launch.id
    with pytest.raises(PermissionError):
        dee.view.set_project_access(team.handbook.id, everyone="read")  # she writes it, no more
    before = len(world.changes())

    cy.view.set_project_access(launch, people={dee.id: "manage"})
    rule = dee.view.project_access(launch)
    assert (rule.maker_id, rule.everyone, rule.people) == (cy.id, "none", {dee.id: "manage"})

    dee.view.set_project_access(launch, people={dee.id: "manage", eli.id: "read"})
    dee.view.set_project_access(launch, people={dee.id: "manage", eli.id: "read", fay.id: "manage"})
    assert fay.view.project_access(launch).people[fay.id] == "manage"
    assert "launch" in names(eli)
    with pytest.raises(PermissionError):
        eli.view.project_access(launch)  # reads it, does not decide who does

    def members_named(rule) -> dict[str, str]:
        return {k: v for k, v in rule.people.items() if k in (dee.id, eli.id, fay.id)}

    # An entry cannot limit the maker or an owner (whether it is kept is not this test's).
    lowered = {dee.id: "manage", eli.id: "write", fay.id: "manage", cy.id: "read", ada.id: "read"}
    dee.view.set_project_access(launch, people=lowered)
    cy.view.add_note(launch, "# Launch plan\n\nCy still writes here.\n")
    ada.view.add_note(launch, "# Launch plan\n\nAda still writes here.\n")
    assert ada.view.project_access(launch).everyone == "none"
    assert members_named(cy.view.project_access(launch)) == {
        dee.id: "manage",
        eli.id: "write",
        fay.id: "manage",
    }

    # Her own entry too: once it is gone, the private project is out of her reach.
    left = {eli.id: "write", fay.id: "manage", cy.id: "read", ada.id: "read"}
    dee.view.set_project_access(launch, people=left)
    assert "launch" not in names(dee)
    with pytest.raises(KeyError):
        dee.view.project_access(launch)
    with pytest.raises(KeyError):
        dee.view.set_project_access(launch, people={dee.id: "manage"})
    assert members_named(fay.view.project_access(launch)) == {eli.id: "write", fay.id: "manage"}

    # A person at manage decides what everyone gets, too.
    fay.view.set_project_access(launch, everyone="read")
    assert dee.view.list_notes(launch)
    with pytest.raises(PermissionError):
        dee.view.project_access(launch)  # she reads it now, and no more

    changes = world.changes()
    assert len(changes) == before + 6
    assert all(event.project == "launch" for event in changes[:6])
    assert [event.detail.get("user") for event in changes[:6]] == [fay.id] + [dee.id] * 4 + [cy.id]


def test_manage_includes_write(team):
    cy, dee, eli = team.cy, team.dee, team.eli
    launch = team.launch.id
    cy.view.set_project_access(launch, people={dee.id: "manage", eli.id: "read"})
    note, created = dee.view.add_note(launch, "# Press\n\nThe Heron press day is Monday.\n")
    assert created and compile_note(dee.view, note).status == "saved"
    assert "launch" in names(dee)
    found = dee.view.search("heron")
    assert found and {hit.project_id for hit in found} == {launch}
    assert eli.view.search("heron")
    with pytest.raises(PermissionError):
        eli.view.add_note(launch, "# Press\n\nEli may not write this.\n")
    shared = eli.view.project_sharing(launch)
    assert {cy.id, dee.id} <= set(shared.people)  # the maker, and a person at manage


def test_manage_does_not_delete_the_project_nor_forget_a_note(team):
    world, ada, cy, dee = team.world, team.ada, team.cy, team.dee
    launch = team.launch.id
    cy.view.set_project_access(launch, people={dee.id: "manage"})
    note = cy.view.list_notes(launch)[0]
    before = dump(world.db)
    assert dee.view.may_erase(launch) is False
    with pytest.raises(PermissionError):
        dee.view.delete_project(launch)
    with pytest.raises(PermissionError):
        dee.view.forget_note(note.id)
    assert dump(world.db) == before

    assert cy.view.may_erase(launch) is True and ada.view.may_erase(launch) is True
    cy.view.forget_note(note.id)
    assert ada.view.delete_project(launch) is True


def test_a_workspace_admin_decides_only_where_he_manages(team):
    world, brook, cy, eli = team.world, team.brook, team.cy, team.eli
    launch, handbook = team.launch.id, team.handbook.id
    before = dump(world.db)
    for attempt in (
        lambda b: brook.view.project_access(b),
        lambda b: brook.view.set_project_access(b, everyone="read"),
        lambda b: brook.view.share_project(b, "read", [address("Eli")]),
    ):
        assert kind_of(attempt, launch) == kind_of(attempt, NOWHERE) == "KeyError"
        assert kind_of(attempt, handbook) == "PermissionError"  # he reaches it, as a member
    assert "launch" not in names(brook) and brook.view.search("heron") == []
    assert dump(world.db) == before

    cy.view.set_project_access(launch, people={brook.id: "manage"})
    assert brook.view.project_access(launch).people == {brook.id: "manage"}
    brook.view.set_project_access(launch, people={brook.id: "manage", eli.id: "read"})
    assert brook.view.search("heron") and "launch" in names(eli)
    with pytest.raises(PermissionError):
        brook.view.set_project_access(handbook, everyone="read")  # still not his to decide


def test_manage_is_a_persons_level_only(team):
    world, cy, dee = team.world, team.cy, team.dee
    launch = team.launch.id
    before = dump(world.db)
    for view in (cy.view, world.operator):
        for refused in (
            lambda v: v.set_project_access(launch, everyone="manage"),
            lambda v: v.set_project_access(launch, people={dee.id: "admin"}),
            lambda v: v.set_project_access(launch, people={dee.id: "everything"}),
            lambda v: v.set_project_access(launch, people={dee.id: "none"}),
            lambda v: v.set_project_access(launch, everyone="read", people={dee.id: "owner"}),
        ):
            with pytest.raises(ValueError):
                refused(view)
    assert dump(world.db) == before
    assert world.operator.set_project_access(launch, people={dee.id: "manage"}).people == {
        dee.id: "manage"
    }


# Several people at once


def test_sharing_with_a_list_sets_one_level_for_the_members_in_it(team):
    world, ada, brook, cy, dee, eli, fay = (
        team.world,
        team.ada,
        team.brook,
        team.cy,
        team.dee,
        team.eli,
        team.fay,
    )
    launch = team.launch.id
    mallory = world.person("Mallory", join="personal")  # a person, but not of this workspace
    cy.view.set_project_access(launch, everyone="read", people={fay.id: "manage"})
    before = len(world.changes())

    typed = [
        "  DEE@Example.Test ",
        "eli@example.test",
        " Stranger@Elsewhere.Example.Test",
        "cy@example.test",  # the maker
        "dee@example.test",  # again
        "ADA@example.test ",  # an owner
        "MALLORY@Example.test",
    ]
    shared = cy.view.share_project(launch, "write", typed)
    assert shared.granted == (dee.id, eli.id, cy.id, ada.id)
    assert isinstance(shared.unknown, tuple)
    assert sorted(shared.unknown) == ["MALLORY@Example.test", "Stranger@Elsewhere.Example.Test"]
    rule = cy.view.project_access(launch)
    assert (rule.everyone, rule.people) == (
        "read",
        {fay.id: "manage", dee.id: "write", eli.id: "write"},
    )
    assert len(world.changes()) == before + 1
    assert world.changes()[0].detail.get("user") == cy.id
    assert "stranger" not in dump(world.db).lower()  # a stranger leaves no trace
    assert ada.view.list_invites() == [] and "launch" not in names(mallory)

    unchanged = dump(world.db)
    again = cy.view.share_project(launch, "write", typed)
    assert (again.granted, sorted(again.unknown)) == (shared.granted, sorted(shared.unknown))
    assert len(world.changes()) == before + 1 and dump(world.db) == unchanged

    # It sets the level, down as well as up; a person at manage and the operator may too.
    assert cy.view.share_project(launch, "read", ["eli@example.test"]).granted == (eli.id,)
    assert fay.view.share_project(launch, "manage", ["Dee@example.test"]).granted == (dee.id,)
    assert world.changes()[0].detail.get("user") == fay.id
    assert dee.view.share_project(launch, "read", [address("Brook")]).granted == (brook.id,)
    world.operator.share_project(launch, "manage", [address("Brook")])
    assert ada.view.share_project(launch, "write", [address("Eli")]).granted == (eli.id,)
    assert cy.view.project_access(launch).people == {
        fay.id: "manage",
        dee.id: "manage",
        eli.id: "write",
        brook.id: "manage",
    }
    assert len(world.changes()) == before + 6


def test_sharing_refuses_a_bad_level_or_list_and_writes_nothing(team):
    world, brook, cy, dee, eli, fay = team.world, team.brook, team.cy, team.dee, team.eli, team.fay
    launch = team.launch.id
    cy.view.set_project_access(launch, people={dee.id: "write", eli.id: "read"})
    before = dump(world.db)

    for level in ("admin", "none", "owner", ""):
        with pytest.raises(ValueError):
            cy.view.share_project(launch, level, [address("Fay")])
    with pytest.raises(ValueError):
        cy.view.share_project(launch, "read", [])
    with pytest.raises(ValueError):
        cy.view.share_project(launch, "read", [f"person{n}@example.test" for n in range(201)])
    for entry in (
        "",
        "   ",
        "no-at-sign.example.test",
        "fay @example.test",
        "a@example.test b@example.test",
    ):
        with pytest.raises(ValueError) as refused:
            cy.view.share_project(launch, "read", ["eli@example.test", entry])
        message = str(refused.value)
        assert "example.test" not in message and "no-at-sign" not in message
        assert not entry.strip() or entry.strip() not in message
    assert dump(world.db) == before

    # Two hundred is the most, and addresses that are nobody's change nothing.
    many = [f"person{n}@example.test" for n in range(200)]
    shared = cy.view.share_project(launch, "read", many)
    assert shared.granted == () and sorted(shared.unknown) == sorted(many)
    assert dump(world.db) == before

    for view, refusal in (
        (dee.view, PermissionError),  # write is not manage
        (eli.view, PermissionError),
        (brook.view, KeyError),  # out of his reach
    ):
        with pytest.raises(refusal):
            view.share_project(launch, "write", [address("Fay")])
    with pytest.raises(KeyError):
        cy.view.share_project(NOWHERE, "write", [address("Fay")])
    assert dump(world.db) == before
    assert "launch" not in names(fay)


# The web view


def test_the_access_page_offers_three_levels_and_takes_a_list(team):
    world, brook, cy, dee, eli, fay = team.world, team.brook, team.cy, team.dee, team.eli, team.fay
    launch = team.launch.id
    cy.view.set_project_access(launch, people={dee.id: "manage", fay.id: "write", eli.id: "read"})
    with TestClient(world.app, base_url=BASE) as client:

        def post(who, path: str, **data):
            headers = {**who.cookie, **SAME_ORIGIN}
            return client.post(path, data=data, headers=headers, follow_redirects=False)

        page = client.get("/b/launch/access", headers=cy.cookie)
        assert page.status_code == 200
        offered = options_for(page.text, dee.id)
        assert {"read", "write", "manage"} <= set(offered)
        assert (offered["read"], offered["write"]) == ("Read only", "Read and write")
        assert offered["manage"] not in ("Read only", "Read and write")
        assert "who reaches" in offered["manage"].lower()
        assert offered["manage"].strip().lower() not in ("admin", "administrator")
        sentences = re.split(r"(?<=[.!?])\s+", text_of(page.text))
        about_admins = [s for s in sentences if "admin" in s.lower()]
        assert any("workspace" in s.lower() for s in about_admins)

        # The "Add several people" form.
        assert "Add several people" in page.text
        form = re.search(
            r'<form\b[^>]*action="/b/launch/share"[^>]*>(.*?)</form>', page.text, flags=re.DOTALL
        )
        assert form, "a form posts to /b/launch/share"
        assert re.search(r'<textarea\b[^>]*name="emails"', form.group(1))
        assert re.search(r'<select\b[^>]*name="level"', form.group(1))
        levels = set(re.findall(r'<option\b[^>]*value="([a-z]+)"', form.group(1)))
        assert {"read", "write", "manage"} <= levels

        # Dee, at manage, has the page and posts it; write and read do not.
        assert client.get("/b/launch/access", headers=dee.cookie).status_code == 200
        assert client.get("/b/launch/access", headers=fay.cookie).status_code == 403
        assert client.get("/b/launch/access", headers=eli.cookie).status_code == 403
        assert client.get("/b/launch/access", headers=brook.cookie).status_code == 404
        people = [f"{dee.id}:manage", f"{fay.id}:manage", f"{eli.id}:read"]
        moved = post(dee, "/b/launch/access", everyone="read", people=people)
        assert moved.status_code == 303
        assert (cy.view.project_access(launch).everyone, cy.view.project_access(launch).people) == (
            "read",
            {dee.id: "manage", fay.id: "manage", eli.id: "read"},
        )
        assert post(eli, "/b/launch/access", everyone="write").status_code == 403
        assert world.changes()[0].actor_kind == "session" and world.changes()[0].actor == "Dee"

        # A list of members: back to the access page.
        typed = "eli@example.test, FAY@example.test;ADA@example.test\n\n  cy@example.test  "
        listed = post(cy, "/b/launch/share", emails=typed, level="write")
        assert listed.status_code == 303
        assert listed.headers["location"].endswith("/b/launch/access")
        assert cy.view.project_access(launch).people == {
            dee.id: "manage",
            fay.id: "write",
            eli.id: "write",
        }
        assert (world.changes()[0].actor_kind, world.changes()[0].actor) == ("session", "Cy")

        # A stranger among them: the page again, naming the address as typed, escaped.
        mixed = post(
            dee,
            "/b/launch/share",
            emails="eli@example.test\nzed<i>@elsewhere.example.test",
            level="manage",
        )
        assert mixed.status_code == 200
        assert "zed&lt;i&gt;@elsewhere.example.test" in mixed.text and "zed<i>" not in mixed.text
        assert "invite" in text_of(mixed.text).lower()
        assert cy.view.project_access(launch).people[eli.id] == "manage"

        # Refusals change nothing.
        rule = cy.view.project_access(launch)
        many = ", ".join(f"person{n}@example.test" for n in range(201))
        for emails, level in (
            ("", "read"),
            (" \n ,; ", "read"),
            ("eli@example.test nobody", "read"),
            (many, "read"),
            ("eli@example.test", "admin"),
            ("eli@example.test", ""),
        ):
            assert post(cy, "/b/launch/share", emails=emails, level=level).status_code == 400
        assert (
            post(fay, "/b/launch/share", emails=address("Fay"), level="manage").status_code == 403
        )
        assert (  # Brook reads it now (everyone reads), and no more
            post(brook, "/b/launch/share", emails=address("Brook"), level="read").status_code == 403
        )
        assert post(cy, "/b/nowhere/share", emails=address("Eli"), level="read").status_code == 404
        assert cy.view.project_access(launch) == rule


def test_the_project_page_links_who_reaches_it_for_a_person_at_manage(team):
    world, cy, dee, fay = team.world, team.cy, team.dee, team.fay
    cy.view.set_project_access(team.launch.id, people={dee.id: "manage", fay.id: "write"})
    with TestClient(world.app, base_url=BASE) as client:
        assert 'href="/b/launch/access"' in client.get("/b/launch", headers=cy.cookie).text
        assert 'href="/b/launch/access"' in client.get("/b/launch", headers=dee.cookie).text
        writer = client.get("/b/launch", headers=fay.cookie)
        assert writer.status_code == 200 and 'href="/b/launch/access"' not in writer.text


# What stays


def test_a_credential_of_a_person_at_manage_does_not_manage_access(team):
    world, ada, cy, dee = team.world, team.ada, team.cy, team.dee
    launch = team.launch.id
    cy.view.set_project_access(launch, people={dee.id: "manage"})
    ada.view.set_role(dee.id, "admin")  # so she may make a key
    key = dee.view.create_key("dee-script")
    agent = world.agent(dee, "dee-laptop")
    rule = cy.view.project_access(launch)

    # Narrowed to some projects or to reading, a view of hers manages nothing.
    for narrowed in (dee.view.narrow([launch]), dee.view.narrow(None, "read")):
        with pytest.raises(PermissionError):
            narrowed.set_project_access(launch, people={dee.id: "manage", cy.id: "read"})
        with pytest.raises(PermissionError):
            narrowed.share_project(launch, "read", [address("Eli")])

    with TestClient(world.app, base_url=BASE) as client:
        for credential in ({"x-api-key": key}, {"authorization": f"Bearer {agent}"}):
            listed = tool_names(client, credential)
            assert "recall" in listed
            assert not [n for n in listed if re.search(r"access|share|people|member", n)]
        for path in (f"/api/projects/{launch}/access", f"/api/projects/{launch}/share"):
            for method in ("POST", "PUT", "PATCH"):
                body = {"people": {dee.id: "manage"}, "level": "read", "emails": [address("Eli")]}
                answer = client.request(method, path, headers={"x-api-key": key}, json=body)
                assert answer.status_code in (404, 405), (method, path)
        gone = client.delete(f"/api/projects/{launch}?confirm=launch", headers={"x-api-key": key})
        assert gone.status_code == 403  # manage does not delete, over REST either
    assert cy.view.project_access(launch) == rule
    assert "launch" in names(cy)


def test_an_export_carries_no_access_rule(team, tmp_path):
    ada, cy, dee = team.ada, team.cy, team.dee
    cy.view.set_project_access(team.launch.id, people={dee.id: "manage"})
    out = io.BytesIO()
    ada.view.export(out)
    archive = zipfile.ZipFile(io.BytesIO(out.getvalue()))
    for name in archive.namelist():
        assert dee.id.encode() not in archive.read(name), name
    other = Store(tmp_path / "elsewhere.db")
    try:
        target = other.for_account(other.create_account("Elsewhere").id)
        target.import_export(io.BytesIO(out.getvalue()))
        brought = target.get_project("launch")
        rule = target.project_access(brought.id)
        assert (rule.everyone, rule.people) == ("none", {})
    finally:
        other.close()


# Schema


def test_a_database_from_before_keeps_every_persons_level(tmp_path):
    db = tmp_path / "old.db"
    ids = {name: f"usr_{name * 6}" for name in ("c1", "d1", "e1")}
    cy, dee, eli = ids["c1"], ids["d1"], ids["e1"]
    launch, atlas = "brn_0000000000a1", "brn_0000000000a2"
    at = "2026-11-01T09:00:00+00:00"
    con = sqlite3.connect(db)
    try:
        con.executescript(SCHEMA_BEFORE)
        con.execute(
            "INSERT INTO accounts (seq, id, name, created_at) VALUES (1, ?, 'default', ?)",
            (DEFAULT_ACCOUNT_ID, at),
        )
        for name, user_id in (("Cy", cy), ("Dee", dee), ("Eli", eli)):
            con.execute(
                "INSERT INTO users (id, email, name, created_at) VALUES (?, ?, ?, ?)",
                (user_id, address(name), name, at),
            )
            con.execute(
                "INSERT INTO memberships (account_id, user_id, role, created_at)"
                " VALUES (?, ?, ?, ?)",
                (DEFAULT_ACCOUNT_ID, user_id, "owner" if name == "Cy" else "member", at),
            )
        for project_id, name in ((launch, "launch"), (atlas, "atlas")):
            con.execute(
                "INSERT INTO projects (id, account_id, name, description, created_at, kind,"
                " maker_id, everyone) VALUES (?, ?, ?, '', ?, 'general', ?, 'none')",
                (project_id, DEFAULT_ACCOUNT_ID, name, at, cy),
            )
        con.executemany(
            "INSERT INTO project_people (project_id, user_id, access) VALUES (?, ?, ?)",
            [(launch, dee, "read"), (launch, eli, "write"), (atlas, dee, "write")],
        )
        con.execute(f"PRAGMA user_version = {VERSION_BEFORE}")
        con.commit()
    finally:
        con.close()

    store = Store(db)
    try:
        maker = store.for_account(DEFAULT_ACCOUNT_ID, cy)
        assert maker.project_access(launch).people == {dee: "read", eli: "write"}
        assert maker.project_access(atlas).people == {dee: "write"}
        reader = store.for_account(DEFAULT_ACCOUNT_ID, dee)
        with pytest.raises(PermissionError):
            reader.add_note(launch, "# Atlas\n\nDee reads launch only.\n")
        reader.add_note(atlas, "# Atlas\n\nDee writes here.\n")
        maker.set_project_access(launch, people={dee: "manage", eli: "write"})
    finally:
        store.close()
    store = Store(db)
    try:
        manager = store.for_account(DEFAULT_ACCOUNT_ID, dee)
        assert manager.project_access(launch).people == {dee: "manage", eli: "write"}
        assert store.for_account(DEFAULT_ACCOUNT_ID, cy).project_access(atlas).people == {
            dee: "write"
        }
    finally:
        store.close()


# The schema as cairn writes it on 2026-11-13 (version 19), taken from a fresh database's
# sqlite_master with comments dropped and long lines wrapped. Every step after 19 runs on it,
# whichever number this one takes: the test asserts on the rows, never on a version.
VERSION_BEFORE = 19
SCHEMA_BEFORE = """
CREATE TABLE accounts (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    maker_id TEXT,
    everyone TEXT NOT NULL CHECK (everyone IN ('write', 'read', 'none')),
    UNIQUE (account_id, name)
);
CREATE TABLE api_keys (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    digest TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    projects TEXT,
    access TEXT NOT NULL CHECK (access IN ('write', 'read')),
    user_id TEXT
);
CREATE INDEX api_keys_by_label ON api_keys (account_id, label);
CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    user_id TEXT,
    label TEXT,
    key_id TEXT
);
CREATE TABLE notes (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    title TEXT NOT NULL,
    topic TEXT,
    label TEXT,
    source_ref TEXT,
    via TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX notes_by_text ON notes (project_id, text_sha256);
CREATE TABLE spans (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (note_id, ordinal)
);
CREATE TABLE page_versions (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    claims TEXT NOT NULL,
    metrics TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, slug, version)
);
CREATE TABLE oauth_clients (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,
    created_at TEXT NOT NULL,
    granted_at TEXT
);
CREATE TABLE oauth_grants (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    user_id TEXT,
    projects TEXT,
    access TEXT NOT NULL CHECK (access IN ('write', 'read')),
    audience TEXT
);
CREATE INDEX oauth_grants_by_account ON oauth_grants (account_id);
CREATE TABLE oauth_codes (
    digest TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INTEGER NOT NULL,
    code_challenge TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    grant_id TEXT,
    user_id TEXT,
    projects TEXT,
    access TEXT NOT NULL CHECK (access IN ('write', 'read')),
    audience TEXT
);
CREATE TABLE oauth_tokens (
    digest TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh', 'rotated')),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX oauth_tokens_by_grant ON oauth_tokens (grant_id);
CREATE TABLE users (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE identities (
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    issuer TEXT NOT NULL,
    PRIMARY KEY (provider, subject)
);
CREATE INDEX identities_by_user ON identities (user_id);
CREATE TABLE memberships (
    seq INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    created_at TEXT NOT NULL,
    UNIQUE (account_id, user_id)
);
CREATE INDEX memberships_by_user ON memberships (user_id);
CREATE TABLE project_sets (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    project_ids TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (account_id, name)
);
CREATE TABLE search_claims (
    account_id TEXT NOT NULL,
    row INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    PRIMARY KEY (account_id, row)
) WITHOUT ROWID;
CREATE INDEX search_claims_by_page ON search_claims (account_id, project_id, slug);
CREATE TABLE search_projects (
    project_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    claims INTEGER NOT NULL,
    tokens INTEGER NOT NULL
);
CREATE TABLE settings (
    account_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, name)
) WITHOUT ROWID;
CREATE TABLE audit_events (
    seq INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    at TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('key', 'token', 'session', 'system')),
    actor TEXT,
    actor_id TEXT,
    action TEXT NOT NULL,
    project TEXT,
    detail TEXT NOT NULL
);
CREATE UNIQUE INDEX audit_events_by_number ON audit_events (account_id, n);
CREATE TABLE sources (
    seq INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    kind TEXT NOT NULL,
    config TEXT NOT NULL,
    cursor TEXT,
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    last_report TEXT,
    last_error TEXT,
    claim TEXT,
    claimed_at TEXT,
    UNIQUE (account_id, name)
);
CREATE TABLE project_people (
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    access TEXT NOT NULL CHECK (access IN ('write', 'read')),
    PRIMARY KEY (project_id, user_id)
) WITHOUT ROWID;
CREATE INDEX project_people_by_user ON project_people (user_id);
CREATE TABLE invites (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    digest TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE (account_id, email)
);
CREATE TABLE removals (
    digest TEXT PRIMARY KEY,
    account_id TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE usage (
    account_id TEXT NOT NULL,
    period TEXT NOT NULL,
    notes INTEGER NOT NULL DEFAULT 0,
    note_bytes INTEGER NOT NULL DEFAULT 0,
    model_tokens_in INTEGER NOT NULL DEFAULT 0,
    model_tokens_out INTEGER NOT NULL DEFAULT 0,
    model_micros INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, period)
) WITHOUT ROWID;
CREATE TABLE account_quota (
    account_id TEXT PRIMARY KEY,
    plan TEXT,
    stored_bytes INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE TABLE subscriptions (
    account_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    plan TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'past_due', 'canceled')),
    period_end TEXT,
    since TEXT NOT NULL,
    customer_ref TEXT,
    subscription_ref TEXT,
    started TEXT,
    past_due_since TEXT
) WITHOUT ROWID;
CREATE TABLE billing_messages (
    provider TEXT NOT NULL,
    id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (provider, id)
) WITHOUT ROWID;
CREATE INDEX billing_messages_by_account ON billing_messages (account_id);
CREATE TABLE model_reservations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    period TEXT NOT NULL,
    micros INTEGER NOT NULL,
    reserved_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX model_reservations_by_account ON model_reservations (account_id);
CREATE TABLE forgotten (
    project_id TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    PRIMARY KEY (project_id, text_sha256)
) WITHOUT ROWID;
CREATE TABLE account_disk (
    account_id TEXT PRIMARY KEY,
    passages INTEGER NOT NULL DEFAULT 0,
    history_bytes INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE TRIGGER passage_added AFTER INSERT ON spans WHEN ((new.kind IN ('paragraph',
    'bullet', 'code', 'table', 'quote')) + MAX(0, LENGTH(CAST(new.heading_path AS BLOB)) - 4
    * json_array_length(new.heading_path)) / 40) > 0
BEGIN
    INSERT INTO account_disk (account_id, passages)
    SELECT projects.account_id, ((new.kind IN ('paragraph', 'bullet', 'code', 'table',
        'quote')) + MAX(0, LENGTH(CAST(new.heading_path AS BLOB)) - 4 *
        json_array_length(new.heading_path)) / 40)
    FROM notes JOIN projects ON projects.id = notes.project_id
    WHERE notes.id = new.note_id
    ON CONFLICT (account_id) DO UPDATE SET passages = passages + excluded.passages;
END;
CREATE TRIGGER passage_removed AFTER DELETE ON spans
WHEN ((old.kind IN ('paragraph', 'bullet', 'code', 'table', 'quote')) + MAX(0,
    LENGTH(CAST(old.heading_path AS BLOB)) - 4 * json_array_length(old.heading_path)) / 40)
    > 0
BEGIN
    UPDATE account_disk SET passages = MAX(0, passages - ((old.kind IN ('paragraph',
        'bullet', 'code', 'table', 'quote')) + MAX(0, LENGTH(CAST(old.heading_path AS BLOB))
        - 4 * json_array_length(old.heading_path)) / 40))
    WHERE account_id = (SELECT projects.account_id FROM notes
    JOIN projects ON projects.id = notes.project_id WHERE notes.id = old.note_id);
END;
CREATE TRIGGER version_added AFTER INSERT ON page_versions
BEGIN
    INSERT INTO account_disk (account_id, history_bytes)
    SELECT account_id, LENGTH(CAST(new.body AS BLOB)) + LENGTH(CAST(new.claims AS BLOB))
        FROM projects WHERE id = new.project_id
    ON CONFLICT (account_id) DO UPDATE SET
    history_bytes = history_bytes + excluded.history_bytes;
END;
CREATE TRIGGER version_removed AFTER DELETE ON page_versions
BEGIN
    UPDATE account_disk SET history_bytes = MAX(0, history_bytes - (LENGTH(CAST(old.body AS
        BLOB)) + LENGTH(CAST(old.claims AS BLOB))))
    WHERE account_id = (SELECT account_id FROM projects WHERE id = old.project_id);
END;
"""
