"""T1 acceptance tests (frozen): members, roles, and who reaches which project.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/store/__init__.py and src/cairn/server.py.

One rule under everything here: a credential a person made acts for that person. A web
session, an agent the person approved and an API key the person made all reach what the
person reaches at the moment of the request, and nothing once the person has left. A key
made without a person (on the command line, or in a session opened with a key) belongs to
the workspace, as before.

People
- Roles: "owner", "admin", "member". Owners and admins invite and remove people and manage
  keys and sets. Only an owner makes or unmakes an owner. A workspace always has an owner:
  removing or demoting the last one is a ValueError. A view that acts for a member raises
  PermissionError on any of this; a view that acts for no person may do all of it.
- `view.invite(email, role="member", ttl_seconds=604_800) -> Invite` (`id`, `email`,
  `role`, `expires_at`, and `token`, given this once: the database keeps a digest).
  `view.list_invites()` (pending ones, no tokens), `view.revoke_invite(id) -> bool`.
- `store.find_invite(token) -> InviteInfo | None` (`account_id`, `workspace`, `email`,
  `role`) for one that is still open. `store.accept_invite(token, user_id) -> Member` makes
  the user a member with the invite's role, once: KeyError for a token that is unknown,
  used, revoked or expired; PermissionError when the user's email is not the invite's
  (ignoring case). Nothing changes on either error. Someone who is already a member keeps
  their role. Inviting an address again replaces its open invite.
- `view.set_role(user_id, role)`, `view.remove_member(user_id) -> bool`. Removal ends, in
  the same transaction, that person's sessions in this workspace, the agents they approved
  and the keys they made. What they wrote stays. `store.list_workspaces(user_id)` lists a
  person's workspaces in joining order, each with their role.

Who reaches a project
- A project has a maker (`Project.maker_id`: the user whose view created it, else None) and a
  rule, `view.project_access(project) -> ProjectAccess`: `everyone` is what any member gets
  ("write", "read" or "none"), `people` maps user ids to "read" or "write".
  `create_project(name, description="", kind="general", *, everyone="write")`; the
  `new_project` tool takes `private` (false by default), which means everyone = "none".
  `view.set_project_access(project, *, everyone=None, people=None)` replaces what is given;
  only the project's maker and the workspace's owners may call it (PermissionError), and a
  person named in `people` must be a member (ValueError).
- A person reaches a project with the most that any of these gives: owners write everywhere;
  the maker writes; `people`; `everyone`. A project a person does not reach behaves, for
  them, exactly like one that does not exist: in every store method, every tool, the REST
  API, the web view and search. With "read", every write is refused (PermissionError in
  the store, 403 over REST, a tool error that says "read-only").
- `for_account(account_id, user_id)` is already that person's view. A connection's own
  limits (S1) narrow it further; they never widen it.
- What a search returns, and every score in it, depends only on projects the caller reaches.

The web view
- GET /members lists the people and their roles. Owners and admins also get the invite
  form and, per person, a role form and a remove button. POST /members/invite (`email`,
  `role`) shows the link to send, `<public_url>/join/<token>`, once. POST
  /members/<user id>/role (`role`), POST /members/<user id>/remove, POST
  /members/invites/<id>/revoke. A member gets 403 on each POST.
- GET /join/<token>: signed out, 303 to /login with the way back; signed in, the
  workspace's name and a form that posts to the same address. POST /join/<token> joins,
  starts a session in that workspace and redirects to /. The wrong person gets 403 with
  the reason; a dead link is 404. The GET writes nothing.
- GET /workspaces lists the person's workspaces; POST /workspaces/<account id>/switch
  starts a session there (404 for a workspace they are not in).
- GET /b/<project>/access shows the rule; POST replaces it (`everyone`, and `people`
  as repeated "user id:level" values). 403 unless the maker or an owner.
- The audit log (G1) records member.invited, member.joined, member.removed, role.changed,
  invite.revoked and project.access_changed, each with who did it: a person in the web view
  is `actor_kind` "session" with their name as `actor`; an agent they approved is "token"
  with the agent's name; a key they made is "key" with its label.
"""

import base64
import hashlib
import json
import re
import secrets
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app
from cairn.store import Store
from cookie_names import session_cookie

BASE = "https://cairn.example.test"
ISSUER = "https://id.acme.example/pool-1"
REDIRECT = "http://127.0.0.1:33418/callback"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
SECRET_PLAN = "# Launch plan\n\nProject Heron ships on 2027-03-01.\n"
PUBLIC_NOTE = "# Handbook\n\nReleases go out on Tuesdays.\n"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


class World:
    """One shared workspace (the default one) and the people in it, made through the store.
    Ada signs in first and owns it; everyone after her arrives as a member."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "cairn.db"
        self.store = Store(self.db)
        self.app = create_app(self.store, public_url=BASE)
        self.account = self.store.account_id
        self.people: dict[str, SimpleNamespace] = {}

    def person(self, name: str, *, join: str = "default") -> SimpleNamespace:
        email = f"{name.lower()}@acme.example"
        signed = self.store.sign_in("sso", f"sub-{name}", email, name, issuer=ISSUER, join=join)
        who = SimpleNamespace(
            id=signed.user.id,
            name=name,
            email=email,
            token=signed.token,
            cookie={"cookie": f"{session_cookie('https')}={signed.token}"},
            account=signed.account_id,
            view=self.store.for_account(signed.account_id, signed.user.id),
        )
        self.people[name] = who
        return who

    def agent(self, who: SimpleNamespace, name: str = "laptop") -> str:
        """An agent `who` approved, as an access token."""
        client = self.store.register_client(name, [REDIRECT])
        verifier = secrets.token_urlsafe(32)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        code = who.view.create_code(client.id, REDIRECT, challenge)
        return self.store.redeem_code(code, client.id).access_token

    def close(self) -> None:
        self.store.close()


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.close()


@pytest.fixture
def team(world):
    """Ada owns the workspace, Brook is an admin, Cy and Dee are members."""
    ada, brook, cy, dee = (world.person(name) for name in ("Ada", "Brook", "Cy", "Dee"))
    ada.view.set_role(brook.id, "admin")
    return SimpleNamespace(world=world, ada=ada, brook=brook, cy=cy, dee=dee)


def tool(client, credential: dict, name: str, /, **arguments) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    r = client.post("/mcp", headers={**MCP_HEADERS, **credential}, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def call(client, credential: dict, name: str, /, **arguments) -> dict:
    result = tool(client, credential, name, **arguments)
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def bearer(token: str) -> dict:
    return {"authorization": f"Bearer {token}"}


def kind_of(action, *arguments) -> str:
    """How a call ends: "ok", or the name of what it raised."""
    try:
        action(*arguments)
    except Exception as error:
        return type(error).__name__
    return "ok"


# People


def test_an_invite_brings_one_person_in_once(world):
    ada = world.person("Ada")
    outsider = world.person("Brook", join="personal")  # has a workspace of their own
    stranger = world.person("Mallory", join="personal")
    assert [m.role for m in ada.view.list_members()] == ["owner"]

    invite = ada.view.invite("BROOK@acme.example", "admin")
    assert invite.token and invite.role == "admin" and invite.expires_at
    stored = dump(world.db)
    assert invite.token not in stored and "brook@acme.example" in stored.lower()
    (pending,) = ada.view.list_invites()
    assert (pending.id, pending.email.lower()) == (invite.id, "brook@acme.example")
    assert not hasattr(pending, "token")
    found = world.store.find_invite(invite.token)
    assert (found.account_id, found.role) == (world.account, "admin")

    with pytest.raises(PermissionError):
        world.store.accept_invite(invite.token, stranger.id)  # another person's invite
    assert len(ada.view.list_members()) == 1

    joined = world.store.accept_invite(invite.token, outsider.id)
    assert (joined.user_id, joined.role) == (outsider.id, "admin")
    assert [m.name for m in ada.view.list_members()] == ["Ada", "Brook"]
    assert ada.view.list_invites() == [] and world.store.find_invite(invite.token) is None
    with pytest.raises(KeyError):
        world.store.accept_invite(invite.token, outsider.id)  # it worked once
    places = world.store.list_workspaces(outsider.id)
    assert [(p.account_id, p.role) for p in places] == [
        (outsider.account, "owner"),
        (world.account, "admin"),
    ]

    expired = ada.view.invite("nobody@acme.example", ttl_seconds=0)
    replaced = ada.view.invite("mallory@acme.example")
    revoked = ada.view.invite("mallory@acme.example")  # replaces the address's open invite
    assert world.store.find_invite(replaced.token) is None
    assert world.store.find_invite(revoked.token) is not None
    assert ada.view.revoke_invite(revoked.id) is True
    assert ada.view.revoke_invite(revoked.id) is False
    for dead in (expired.token, revoked.token, "no-such-token"):
        assert world.store.find_invite(dead) is None
        with pytest.raises(KeyError):
            world.store.accept_invite(dead, stranger.id)
    assert len(ada.view.list_members()) == 2


def test_roles_decide_who_manages_people(team):
    ada, brook, cy, dee = team.ada, team.brook, team.cy, team.dee
    for refused in (
        lambda: cy.view.invite("eve@acme.example"),
        lambda: cy.view.set_role(dee.id, "admin"),
        lambda: cy.view.remove_member(dee.id),
        lambda: cy.view.list_invites(),
        lambda: brook.view.invite("eve@acme.example", "owner"),  # an admin cannot make owners
        lambda: brook.view.set_role(cy.id, "owner"),
        lambda: brook.view.set_role(ada.id, "member"),  # nor unmake one
        lambda: brook.view.remove_member(ada.id),
    ):
        with pytest.raises(PermissionError):
            refused()

    brook.view.invite("eve@acme.example", "admin")
    brook.view.set_role(cy.id, "admin")
    assert brook.view.remove_member(dee.id) is True
    assert brook.view.remove_member(dee.id) is False
    roles = {m.name: m.role for m in ada.view.list_members()}
    assert roles == {"Ada": "owner", "Brook": "admin", "Cy": "admin"}

    for last_owner in (
        lambda: ada.view.set_role(ada.id, "admin"),
        lambda: ada.view.remove_member(ada.id),
    ):
        with pytest.raises(ValueError):
            last_owner()
    ada.view.set_role(brook.id, "owner")
    ada.view.set_role(ada.id, "member")  # fine now: Brook owns it too
    assert {m.name: m.role for m in brook.view.list_members()}["Ada"] == "member"

    # A view that acts for no person is the operator's and may do all of it.
    world = team.world
    world.store.set_role(ada.id, "owner")
    assert world.store.remove_member(cy.id) is True


def test_leaving_ends_everything_the_person_held(team):
    world, ada, cy = team.world, team.ada, team.cy
    project = ada.view.create_project("handbook")
    ada.view.add_note(project.id, PUBLIC_NOTE)
    token = world.agent(cy)
    with pytest.raises(PermissionError):
        cy.view.create_key("cy-script")  # a member does not make keys
    ada.view.set_role(cy.id, "admin")
    key = cy.view.create_key("cy-script")
    operator_key = world.store.create_key("nightly")  # made by no person
    cy.view.add_note(project.id, "# Handbook\n\nCy wrote this line.\n")

    with TestClient(world.app, base_url=BASE) as client:
        assert client.get("/", headers=cy.cookie).status_code == 200
        assert call(client, bearer(token), "projects")["projects"]
        assert client.get("/api/projects", headers={"x-api-key": key}).status_code == 200

        assert ada.view.remove_member(cy.id) is True

        gone = client.get("/", headers=cy.cookie, follow_redirects=False)
        assert gone.status_code == 303 and gone.headers["location"].startswith("/login")
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        dead = client.post("/mcp", headers={**MCP_HEADERS, **bearer(token)}, json=body)
        assert dead.status_code == 401
        assert client.get("/api/projects", headers={"x-api-key": key}).status_code == 401
        # The workspace's own key and what Cy wrote are untouched.
        assert client.get("/api/projects", headers={"x-api-key": operator_key}).status_code == 200
    assert len(ada.view.list_notes(project.id)) == 2
    assert world.store.resolve_token(token) is None and world.store.resolve_key(key) is None
    assert [g.client_name for g in world.store.list_grants()] == []


# Who reaches a project


@pytest.fixture
def projects(team):
    """handbook: everyone writes. policies: everyone reads, Brook writes. launch: Cy's
    private project, shared with Dee to read."""
    ada, brook, cy, dee = team.ada, team.brook, team.cy, team.dee
    handbook = ada.view.create_project("handbook")
    policies = ada.view.create_project("policies", everyone="read")
    ada.view.set_project_access(policies.id, people={brook.id: "write"})
    launch = cy.view.create_project("launch", everyone="none")
    cy.view.set_project_access(launch.id, people={dee.id: "read"})
    ada.view.add_note(handbook.id, PUBLIC_NOTE)
    ada.view.add_note(policies.id, "# Leave\n\nEveryone gets 25 days of leave.\n")
    cy.view.add_note(launch.id, SECRET_PLAN)
    for view, project in ((ada.view, handbook), (ada.view, policies), (cy.view, launch)):
        for note in view.list_notes(project.id):
            assert compile_note(view, note).status == "saved"
    return SimpleNamespace(team=team, handbook=handbook, policies=policies, launch=launch)


def test_a_projects_rule_decides_what_each_person_reaches(projects):
    team = projects.team
    ada, brook, cy, dee = team.ada, team.brook, team.cy, team.dee
    assert projects.launch.maker_id == cy.id and projects.handbook.maker_id == ada.id
    rule = cy.view.project_access(projects.launch.id)
    assert (rule.everyone, rule.people) == ("none", {dee.id: "read"})

    def names(who) -> list[str]:
        return [b.name for b in who.view.list_projects()]

    assert names(ada) == ["handbook", "launch", "policies"]  # owners reach everything
    assert names(brook) == ["handbook", "policies"]  # an admin is a member here
    assert names(cy) == ["handbook", "launch", "policies"]
    assert names(dee) == ["handbook", "launch", "policies"]

    # Out of reach is exactly unknown.
    hidden, nowhere = projects.launch.id, "brn_000000000000"
    assert brook.view.get_project("launch") is None and brook.view.get_project(hidden) is None
    for attempt in (
        lambda b: brook.view.add_note(b, "# Note\n\nA line.\n"),
        lambda b: brook.view.list_notes(b),
        lambda b: brook.view.list_pages(b),
        lambda b: brook.view.get_page(b, "launch-plan"),
        lambda b: brook.view.page_versions(b, "launch-plan"),
        lambda b: brook.view.project_access(b),
        lambda b: brook.view.search("heron", project_ids=[b]),
    ):
        assert kind_of(attempt, hidden) == kind_of(attempt, nowhere)
    assert brook.view.search("heron") == [] and dee.view.search("heron")
    theirs = cy.view.list_notes(hidden)[0]
    assert brook.view.get_note(theirs.id) is None and brook.view.note_spans(theirs.id) == []

    # Read is read.
    for reader, project in (
        (dee, projects.launch),
        (cy, projects.policies),
        (dee, projects.policies),
    ):
        assert reader.view.list_notes(project.id)
        with pytest.raises(PermissionError):
            reader.view.add_note(project.id, "# Edit\n\nNot allowed.\n")
    brook.view.add_note(projects.policies.id, "# Leave\n\nCarry over at most 5 days.\n")

    # Only the maker and owners change a rule, and only for members.
    for refused in (
        lambda: dee.view.set_project_access(projects.launch.id, everyone="write"),
        lambda: brook.view.set_project_access(projects.policies.id, everyone="write"),
    ):
        with pytest.raises(PermissionError):
            refused()
    with pytest.raises(KeyError):
        brook.view.set_project_access(projects.launch.id, everyone="write")  # it does not exist
    with pytest.raises(ValueError):
        cy.view.set_project_access(projects.launch.id, people={"usr_000000000000": "read"})
    ada.view.set_project_access(projects.launch.id, people={})  # an owner may; Dee is out
    assert names(dee) == ["handbook", "policies"]
    assert cy.view.project_access(projects.launch.id).everyone == "none"  # left as it was


def test_every_way_in_shows_a_person_only_what_they_reach(projects):
    team = projects.team
    world, brook, cy, dee = team.world, team.brook, team.cy, team.dee
    brook_agent, dee_agent = world.agent(brook, "brook-laptop"), world.agent(dee, "dee-laptop")
    with TestClient(world.app, base_url=BASE) as client:
        # The web view.
        home = client.get("/", headers=brook.cookie).text
        assert "handbook" in home and "launch" not in home
        assert client.get("/b/launch", headers=brook.cookie).status_code == 404
        assert client.get("/b/launch", headers=dee.cookie).status_code == 200
        assert "Heron" not in client.get("/search?q=heron", headers=brook.cookie).text
        assert "Heron" in client.get("/search?q=heron", headers=dee.cookie).text
        typed = {"text": "# Launch plan\n\nDee adds a line.\n"}
        refused = client.post("/b/launch/notes", data=typed, headers={**dee.cookie, **SAME_ORIGIN})
        assert refused.status_code == 403

        # An agent acts for the person who approved it.
        listed = [b["name"] for b in call(client, bearer(brook_agent), "projects")["projects"]]
        assert listed == ["handbook", "policies"]
        missing = tool(client, bearer(brook_agent), "recall", query="heron", project="launch")
        unknown = tool(client, bearer(brook_agent), "recall", query="heron", project="no-such")
        assert missing["isError"] is True and unknown["isError"] is True
        same = missing["content"][0]["text"].replace("launch", "X")
        assert same == unknown["content"][0]["text"].replace("no-such", "X")
        assert call(client, bearer(brook_agent), "recall", query="heron")["hits"] == []
        assert call(client, bearer(dee_agent), "recall", query="heron")["hits"]
        read_only = tool(client, bearer(dee_agent), "note", project="launch", text="# X\n\nNo.\n")
        assert read_only["isError"] is True and "read-only" in read_only["content"][0]["text"]

        private = call(client, bearer(dee_agent), "new_project", name="dee-notes", private=True)
        assert (
            world.store.for_account(world.account).project_access(private["id"]).everyone == "none"
        )
        assert "dee-notes" not in [b.name for b in brook.view.list_projects()]

        # When the person's reach shrinks, so does everything they hold.
        cy.view.set_project_access(projects.launch.id, people={})
        assert client.get("/b/launch", headers=dee.cookie).status_code == 404
        assert call(client, bearer(dee_agent), "recall", query="heron")["hits"] == []
    assert len(cy.view.list_notes(projects.launch.id)) == 1  # nothing was written


def test_a_key_reaches_no_more_than_the_person_who_made_it(projects):
    team = projects.team
    world, ada, brook = team.world, team.ada, team.brook
    admins = brook.view.create_key("brook-ci")  # all projects, as far as Brook reaches
    owners = ada.view.create_key("ada-ci")
    with pytest.raises(ValueError):
        brook.view.create_key("sneaky", projects=(projects.launch.id,))  # not a project, for Brook
    with TestClient(world.app, base_url=BASE) as client:

        def names(key: str) -> list[str]:
            got = client.get("/api/projects", headers={"x-api-key": key})
            return sorted(b["name"] for b in got.json()["projects"])

        assert names(admins) == ["handbook", "policies"]
        assert names(owners) == ["handbook", "launch", "policies"]
        hidden = client.get(
            f"/api/projects/{projects.launch.id}/pages", headers={"x-api-key": admins}
        )
        assert hidden.status_code == 404
        ada.view.set_project_access(projects.launch.id, people={brook.id: "read"})
        assert names(admins) == ["handbook", "launch", "policies"]  # it follows its maker
        note = {"text": "# Launch plan\n\nA line from CI.\n"}
        denied = client.post(
            f"/api/projects/{projects.launch.id}/notes", headers={"x-api-key": admins}, json=note
        )
        assert denied.status_code == 403


def test_a_search_never_depends_on_what_the_caller_cannot_reach(tmp_path):
    def hits_for_brook(private_text: str | None):
        world = World(tmp_path / ("with" if private_text else "without"))
        try:
            ada, brook = world.person("Ada"), world.person("Brook")
            handbook = ada.view.create_project("handbook")
            ada.view.add_note(handbook.id, "# Handbook\n\nHeron is the bird on our logo.\n")
            ada.view.add_note(handbook.id, "# Travel\n\nBook trains through the portal.\n")
            for note in ada.view.list_notes(handbook.id):
                compile_note(ada.view, note)
            if private_text:
                secret = ada.view.create_project("secret", everyone="none")
                for n in range(6):
                    ada.view.add_note(secret.id, f"# Plan {n}\n\n{private_text} Step {n}.\n")
                for note in ada.view.list_notes(secret.id):
                    compile_note(ada.view, note)
            return [(h.project, h.slug, h.claim_id, h.score) for h in brook.view.search("heron")]
        finally:
            world.close()

    (tmp_path / "with").mkdir()
    (tmp_path / "without").mkdir()
    alone = hits_for_brook(None)
    beside = hits_for_brook("Project Heron ships in March. Heron needs heron budget.")
    assert alone and [h[:2] for h in alone] == [h[:2] for h in beside]
    assert [h[3] for h in alone] == [h[3] for h in beside]  # the same scores, to the last digit


# The web view


def test_people_are_managed_in_the_web_view(world):
    ada = world.person("Ada")
    cy = world.person("Cy")
    newcomer = world.person("Eve", join="personal")
    with TestClient(world.app, base_url=BASE) as client:

        def post(who, path: str, **data):
            headers = {**who.cookie, **SAME_ORIGIN}
            return client.post(path, data=data, headers=headers, follow_redirects=False)

        page = client.get("/members", headers=ada.cookie)
        assert page.status_code == 200 and "Ada" in page.text and "Cy" in page.text
        assert 'action="/members/invite"' in page.text
        plain = client.get("/members", headers=cy.cookie)
        assert plain.status_code == 200 and 'action="/members/invite"' not in plain.text
        assert (
            post(cy, "/members/invite", email="eve@acme.example", role="member").status_code == 403
        )
        assert post(cy, f"/members/{ada.id}/remove").status_code == 403

        made = post(ada, "/members/invite", email="eve@acme.example", role="member")
        assert made.status_code == 200
        link = re.search(re.escape(BASE) + r"/join/([A-Za-z0-9_-]{20,})", made.text)
        assert link, "the link to send is shown once"
        token = link.group(1)
        assert token not in client.get("/members", headers=ada.cookie).text
        assert "eve@acme.example" in client.get("/members", headers=ada.cookie).text

        client.cookies.clear()
        signed_out = client.get(f"/join/{token}", follow_redirects=False)
        assert signed_out.status_code == 303 and "/login" in signed_out.headers["location"]
        assert f"join/{token}" in signed_out.headers["location"].replace("%2F", "/")
        assert post(cy, f"/join/{token}").status_code == 403  # not Cy's invite
        before = dump(world.db)
        shown = client.get(f"/join/{token}", headers=newcomer.cookie)
        assert shown.status_code == 200 and f'action="/join/{token}"' in shown.text
        assert dump(world.db) == before  # looking at an invite writes nothing
        joined = post(newcomer, f"/join/{token}")
        assert joined.status_code == 303 and joined.headers["location"] == "/"
        session = joined.cookies[session_cookie("https")]
        client.cookies.clear()
        there = world.store.resolve_session(session)
        assert (there.account_id, there.user_id) == (world.account, newcomer.id)
        assert client.get(f"/join/{token}", headers=newcomer.cookie).status_code == 404

        # Eve is in two workspaces now and moves between them.
        eve_here = {"cookie": f"{session_cookie('https')}={session}"}
        places = client.get("/workspaces", headers=eve_here)
        assert places.status_code == 200 and f"/workspaces/{newcomer.account}/switch" in places.text
        back = client.post(
            f"/workspaces/{newcomer.account}/switch",
            headers={**eve_here, **SAME_ORIGIN},
            follow_redirects=False,
        )
        assert back.status_code == 303
        home = world.store.resolve_session(back.cookies[session_cookie("https")])
        assert home.account_id == newcomer.account
        client.cookies.clear()
        elsewhere = client.post(
            "/workspaces/acct_000000000000/switch",
            headers={**eve_here, **SAME_ORIGIN},
            follow_redirects=False,
        )
        assert elsewhere.status_code == 404

        assert post(ada, f"/members/{cy.id}/role", role="admin").status_code == 303
        assert post(ada, f"/members/{newcomer.id}/remove").status_code == 303
        assert client.get("/", headers=eve_here, follow_redirects=False).status_code == 303
        roles = {m.name: m.role for m in ada.view.list_members()}
        assert roles == {"Ada": "owner", "Cy": "admin"}

    audit = world.store.for_account(world.account).list_audit()
    actions = [event.action for event in audit]
    for expected in ("member.invited", "member.joined", "role.changed", "member.removed"):
        assert expected in actions, expected
    invited = next(event for event in audit if event.action == "member.invited")
    assert (invited.actor_kind, invited.actor) == ("session", "Ada")
    assert token not in json.dumps(invited.detail)


def test_a_projects_rule_is_set_in_the_web_view(projects):
    team = projects.team
    world, ada, brook, cy, dee = team.world, team.ada, team.brook, team.cy, team.dee
    with TestClient(world.app, base_url=BASE) as client:
        page = client.get("/b/launch/access", headers=cy.cookie)
        assert page.status_code == 200 and "Dee" in page.text
        assert 'action="/b/launch/access"' in page.text
        assert client.get("/b/launch/access", headers=brook.cookie).status_code == 404
        looking = client.get(
            "/b/launch/access", headers=dee.cookie
        )  # reaches it, may not change it
        assert looking.status_code == 403

        def post(who, **data):
            headers = {**who.cookie, **SAME_ORIGIN}
            return client.post(
                "/b/launch/access", data=data, headers=headers, follow_redirects=False
            )

        assert post(dee, everyone="write").status_code == 403
        changed = post(cy, everyone="read", people=[f"{brook.id}:write"])
        assert changed.status_code == 303
        rule = cy.view.project_access(projects.launch.id)
        assert (rule.everyone, rule.people) == ("read", {brook.id: "write"})
        assert post(cy, everyone="everything").status_code == 400
        assert post(ada, everyone="none").status_code == 303  # an owner may
    audit = world.store.for_account(world.account).list_audit()
    changes = [e for e in audit if e.action == "project.access_changed"]
    assert [e.actor for e in changes[:2]] == ["Ada", "Cy"] and changes[0].project == "launch"


def test_a_workspaces_people_data_goes_with_it(world, tmp_path):
    ada = world.person("Ada", join="personal")
    project = ada.view.create_project("journal", everyone="none")
    ada.view.set_project_access(project.id, people={})
    invite = ada.view.invite("zed-unique-address@acme.example")
    assert "zed-unique-address" in dump(world.db)
    world.store.delete_account(ada.account)
    stored = dump(world.db)
    assert "zed-unique-address" not in stored and "journal" not in stored
    assert world.store.find_invite(invite.token) is None
    assert world.store.get_user(ada.id) is None  # she had no other workspace (the H4 rule)
