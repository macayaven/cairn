"""Unit tests for people and project rules over the server: REST, MCP and the web pages. The
inner TDD loop under tests/acceptance/test_t1_members.py, for what it leaves open."""

import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cairn.web.auth import may_manage_credentials

BASE = "http://127.0.0.1:8765"
ISSUER = "https://id.example.test/pool-1"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def env(tmp_path):
    """Ada owns the default workspace, Brook administers it, Cy is a member; each has a key
    of their own. Cy made "journal", private."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    people = {}
    for name in ("Ada", "Brook", "Cy"):
        signed = store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
        people[name.lower()] = store.for_account(signed.account_id, signed.user.id)
    people["ada"].set_role(people["brook"].user_id, "admin")
    journal = people["cy"].create_project("journal", everyone="none")
    keys = {name: people[name].create_key(f"{name}-key") for name in ("ada", "brook")}
    people["ada"].set_role(people["cy"].user_id, "admin")
    keys["cy"] = people["cy"].create_key("cy-key")
    people["ada"].set_role(people["cy"].user_id, "member")
    client = TestClient(create_app(store, public_url=BASE), base_url=BASE)
    with client:
        yield SimpleNamespace(
            client=client, store=store, db=db, keys=keys, journal=journal, **people
        )
    store.close()


def tool(env, key: str, name: str, /, **arguments) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    r = env.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


# Step 4: the server


def test_a_private_project_is_made_over_rest_and_mcp(env):
    made = tool(env, env.keys["cy"], "new_project", name="diary", private=True)
    diary = json.loads(made["content"][0]["text"])
    assert env.store.project_access(diary["id"]).everyone == "none"
    r = env.client.post(
        "/api/projects",
        headers={"x-api-key": env.keys["cy"]},
        json={"name": "log", "private": True},
    )
    assert r.status_code == 201
    assert env.store.project_access(r.json()["id"]).everyone == "none"
    names = [b["name"] for b in tool_projects(env, env.keys["brook"])]
    assert "diary" not in names and "log" not in names


def tool_projects(env, key: str) -> list[dict]:
    return env.client.get("/api/projects", headers={"x-api-key": key}).json()["projects"]


def test_private_must_be_true_or_false_over_rest(env):
    before = dump(env.db)
    for private in ("yes", 1, None):
        r = env.client.post(
            "/api/projects",
            headers={"x-api-key": env.keys["cy"]},
            json={"name": "log", "private": private},
        )
        assert r.status_code == 400, private
        assert "private" in r.json()["error"]
    assert dump(env.db) == before


def test_a_name_a_hidden_project_holds_is_refused_like_any_taken_name(env):
    """Brook does not reach "journal"; the answer is the one a visible name gets, with no
    id, and nothing is written."""
    env.ada.create_project("handbook")
    before = dump(env.db)
    api = {"x-api-key": env.keys["brook"]}
    for name in ("Journal", "handbook"):
        r = env.client.post("/api/projects", headers=api, json={"name": name})
        assert r.status_code == 409 and env.journal.id not in r.text
        refused = tool(env, env.keys["brook"], "new_project", name=name)
        assert refused["isError"] is True and env.journal.id not in json.dumps(refused)
    assert dump(env.db) == before


def test_whether_a_caller_manages_is_the_stores_rule(env):
    def manages(view) -> bool:
        return may_manage_credentials(SimpleNamespace(store=view))

    assert manages(env.store) and manages(env.ada) and manages(env.brook)
    assert not manages(env.cy)
    env.ada.remove_member(env.brook.user_id)
    assert not manages(env.brook)  # a person who left manages nothing
    assert not manages(env.store.narrow(None, "read"))


def test_a_signed_out_join_link_comes_back_after_signing_in(env):
    r = env.client.get("/join/some-token", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=%2Fjoin%2Fsome-token"
    posted = env.client.post("/join/some-token", headers=SAME_ORIGIN, follow_redirects=False)
    assert posted.headers["location"] == "/login"  # only a GET comes back


def signed_in(env, view) -> None:
    env.client.cookies.set("cairn_session", view.create_session())


def test_a_taken_name_gets_one_sentence_whether_its_project_is_hidden_or_not(env):
    """Names are unique per workspace, so a 409 tells that a name is taken; it tells nothing
    more, and the same sentence, for a project the caller sees and one they do not."""
    import html

    env.ada.create_project("handbook")  # Brook sees it; "journal" is private to Cy

    def sentence(name: str) -> str:
        return f"The project name {name!r} is not available: it already exists in this workspace."

    signed_in(env, env.brook)
    for name in ("Journal", "Handbook"):
        api = {"x-api-key": env.keys["brook"]}
        rest = env.client.post("/api/projects", headers=api, json={"name": name})
        assert (rest.status_code, rest.json()["error"]) == (409, sentence(name))
        mcp = tool(env, env.keys["brook"], "new_project", name=name)
        assert mcp["content"][0]["text"].endswith(sentence(name))  # after the MCP prefix
        form = env.client.post("/projects", data={"name": name}, headers=SAME_ORIGIN)
        assert form.status_code == 409 and sentence(name) in html.unescape(form.text)
        setup = env.client.post(
            "/start", data={"use": ["repo"], "name-repo": name}, headers=SAME_ORIGIN
        )
        assert setup.status_code == 409 and sentence(name) in html.unescape(setup.text), name
        assert "already exists, for" not in setup.text  # not the kind of the project that has it


def test_the_setup_refuses_a_name_a_hidden_project_holds_and_makes_nothing(env):
    signed_in(env, env.brook)
    before = dump(env.db)
    r = env.client.post(
        "/start",
        data={"use": ["repo"], "name-repo": "Journal"},
        headers=SAME_ORIGIN,
        follow_redirects=False,
    )
    assert r.status_code == 409 and 'role="alert"' in r.text
    assert "Journal" in r.text and env.journal.id not in r.text
    assert dump(env.db) == before


# Step 5: the web pages


def as_(env, view) -> dict[str, str]:
    return {"cookie": f"cairn_session={view.create_session()}"}


def send(env, who: dict[str, str], path: str, data: dict | None = None):
    headers = {**who, **SAME_ORIGIN}
    return env.client.post(path, data=data or {}, headers=headers, follow_redirects=False)


def test_a_member_sees_the_people_but_no_forms_and_no_invites(env):
    env.ada.invite("zed@example.test")
    page = env.client.get("/members", headers=as_(env, env.cy))
    assert page.status_code == 200
    assert all(name in page.text for name in ("Ada", "Brook", "Cy"))
    assert (
        "zed@example.test" not in page.text
        and "<form" not in page.text.split('<main id="main" tabindex="-1">')[1]
    )
    hub = env.client.get("/workspace", headers=as_(env, env.cy)).text
    assert 'href="/members"' in hub  # on the workspace page, which the navigation links


def test_an_invite_is_shown_once_and_can_be_revoked(env):
    ada = as_(env, env.ada)
    made = send(env, ada, "/members/invite", {"email": "zed@example.test", "role": "admin"})
    assert made.status_code == 200 and f"{BASE}/join/" in made.text
    (pending,) = env.store.list_invites()
    listed = env.client.get("/members", headers=ada).text
    assert "zed@example.test" in listed
    assert f'href="/members/invites/{pending.id}/revoke"' in listed
    assert send(env, as_(env, env.cy), f"/members/invites/{pending.id}/revoke").status_code == 403
    gone = send(env, ada, f"/members/invites/{pending.id}/revoke")
    assert gone.status_code == 303 and gone.headers["location"] == "/members"
    assert env.store.list_invites() == []
    assert send(env, ada, f"/members/invites/{pending.id}/revoke").status_code == 404


@pytest.mark.parametrize(
    "data",
    [{"email": "not-an-address", "role": "member"}, {"email": "z@example.test", "role": "boss"}],
)
def test_a_bad_invite_is_refused_and_writes_nothing(env, data):
    ada = as_(env, env.ada)
    before = dump(env.db)
    r = send(env, ada, "/members/invite", data)
    assert r.status_code == 400 and 'role="alert"' in r.text
    assert dump(env.db) == before


def test_roles_and_removal_answer_like_the_store(env):
    ada, brook = as_(env, env.ada), as_(env, env.brook)
    before = {m.user_id: m.role for m in env.store.list_members()}
    assert send(env, ada, f"/members/{env.cy.user_id}/role", {"role": "boss"}).status_code == 400
    assert send(env, ada, "/members/usr_000000000000/role", {"role": "admin"}).status_code == 404
    last = send(env, ada, f"/members/{env.ada.user_id}/role", {"role": "member"})
    assert last.status_code == 400 and "owner" in last.text  # the last owner stays
    lift = send(env, brook, f"/members/{env.cy.user_id}/role", {"role": "owner"})
    assert lift.status_code == 403  # only an owner makes an owner
    assert {m.user_id: m.role for m in env.store.list_members()} == before
    assert send(env, ada, "/members/usr_000000000000/remove").status_code == 404
    assert send(env, ada, f"/members/{env.ada.user_id}/remove").status_code == 400


def test_the_join_page_names_the_address_and_refuses_another_person(env):
    eve = env.store.sign_in(
        "sso", "sub-Eve", "eve@example.test", "Eve", issuer=ISSUER, join="personal"
    )
    token = env.ada.invite("zed@example.test").token
    eve_cookie = {"cookie": f"cairn_session={eve.token}"}
    before = dump(env.db)
    page = env.client.get(f"/join/{token}", headers=eve_cookie)
    assert page.status_code == 200 and "zed@example.test" in page.text
    main = page.text.split('<main id="main" tabindex="-1">', 1)[1].split("</main>", 1)[0]
    assert f'action="/join/{token}"' not in main  # a button that could only be refused
    said = (
        "You are signed in as <strong>eve@example.test</strong>, and this invitation is for"
        " <strong>zed@example.test</strong>."
    )
    assert said in main and '<form method="post" action="/logout">' in main
    refused = send(env, eve_cookie, f"/join/{token}")
    assert refused.status_code == 403 and "another email address" in refused.text
    assert dump(env.db) == before  # the invite is still open, and nothing was written
    assert env.client.get("/join/not-a-token", headers=eve_cookie).status_code == 404
    assert send(env, eve_cookie, "/join/not-a-token").status_code == 404


def test_the_person_an_invitation_is_for_gets_the_button_whatever_the_case(env):
    zed = env.store.sign_in(
        "sso", "sub-Zed", "Zed@Example.test", "Zed", issuer=ISSUER, join="personal"
    )
    token = env.ada.invite("zed@example.test").token
    page = env.client.get(f"/join/{token}", headers={"cookie": f"cairn_session={zed.token}"})
    main = page.text.split('<main id="main" tabindex="-1">', 1)[1].split("</main>", 1)[0]
    assert f'action="/join/{token}"' in main and "You are signed in as" not in main
    assert 'action="/logout"' not in main


def test_a_session_without_a_person_cannot_join(env):
    token = env.ada.invite("zed@example.test").token
    keyed = env.store.acting_as("key", "ops", None).create_session()
    r = send(env, {"cookie": f"cairn_session={keyed}"}, f"/join/{token}")
    assert r.status_code == 403
    assert env.store.find_invite(token) is not None


def test_listing_workspaces_writes_nothing_and_switching_keeps_the_old_session(env):
    eve = env.store.sign_in(
        "sso", "sub-Eve", "eve@example.test", "Eve", issuer=ISSUER, join="personal"
    )
    env.store.accept_invite(env.ada.invite("eve@example.test").token, eve.user.id)
    cookie = {"cookie": f"cairn_session={eve.token}"}
    before = dump(env.db)
    places = env.client.get("/workspaces", headers=cookie)
    assert places.status_code == 200 and dump(env.db) == before
    assert f"/workspaces/{env.store.account_id}/switch" in places.text
    moved = send(env, cookie, f"/workspaces/{env.store.account_id}/switch")
    assert moved.status_code == 303 and moved.headers["location"] == "/"
    assert env.store.resolve_session(eve.token).account_id == eve.account_id
    there = env.store.resolve_session(moved.cookies["cairn_session"])
    assert (there.account_id, there.user_id) == (env.store.account_id, eve.user.id)


def test_the_access_page_says_what_private_means(env):
    page = env.client.get("/b/journal/access", headers=as_(env, env.cy))
    assert page.status_code == 200
    assert "only the maker, the people named, and the workspace's owners" in page.text
    assert 'href="/b/journal/access"' in env.client.get("/b/journal", headers=as_(env, env.cy)).text


def test_the_project_page_links_its_rule_only_for_whoever_may_open_it(env):
    env.ada.create_project("shared")
    for who, opens in ((env.ada, True), (env.brook, False), (env.cy, False)):
        cookie = as_(env, who)
        status = env.client.get("/b/shared/access", headers=cookie).status_code
        assert status == (200 if opens else 403)
        linked = 'href="/b/shared/access"' in env.client.get("/b/shared", headers=cookie).text
        assert linked is opens, who.user_id


@pytest.mark.parametrize(
    "people", [["no-colon"], ["usr_000000000000:read"], ["{brook}:admin"], ["{brook}:none:x"]]
)
def test_a_bad_rule_is_refused_and_writes_nothing(env, people):
    people = [p.format(brook=env.brook.user_id) for p in people]
    cy = as_(env, env.cy)
    before = dump(env.db)
    r = send(env, cy, "/b/journal/access", {"everyone": "none", "people": people})
    assert r.status_code == 400
    assert dump(env.db) == before


def test_a_rule_posted_without_people_keeps_them(env):
    env.cy.set_project_access(env.journal.id, people={env.brook.user_id: "read"})
    cy = as_(env, env.cy)
    assert send(env, cy, "/b/journal/access", {"everyone": "read"}).status_code == 303
    rule = env.cy.project_access(env.journal.id)
    assert (rule.everyone, rule.people) == ("read", {env.brook.user_id: "read"})
    cleared = send(
        env, cy, "/b/journal/access", {"everyone": "read", "people": [f"{env.brook.user_id}:none"]}
    )
    assert cleared.status_code == 303 and env.cy.project_access(env.journal.id).people == {}


MANAGE_WORDS = "Read and write, and decide who reaches it"
ADMIN_SENTENCE = (
    "Deciding who reaches a project is not a workspace role: a workspace admin manages people,"
    " keys and sources, and reaches this project only as this rule lets them."
)


def test_the_access_page_offers_manage_in_the_scope_words_and_shows_who_holds_it(env):
    """The levels a person can be given are the Scope component's words (memory.py
    ACCESS_LABELS), and a third that says what it adds; the one held is selected."""
    env.cy.set_project_access(env.journal.id, people={env.brook.user_id: "manage"})
    page = env.client.get("/b/journal/access", headers=as_(env, env.cy)).text
    brook = env.brook.user_id
    assert f'<option value="{brook}:manage" selected>{MANAGE_WORDS}</option>' in page
    assert f'<option value="{brook}:write">Read and write</option>' in page
    assert f'<option value="{brook}:read">Read only</option>' in page
    assert ADMIN_SENTENCE in page.replace("&#39;", "'")
    assert " style=" not in page


def test_the_access_page_says_who_changes_the_rule(env):
    """Who may open the page and post it (Store.project_access): the owners, the maker and
    anyone at "manage"."""
    env.cy.set_project_access(env.journal.id, people={env.brook.user_id: "manage"})
    page = env.client.get("/b/journal/access", headers=as_(env, env.brook)).text
    words = " ".join(re.sub(r"<[^>]+>", " ", page).split())
    assert (
        "The workspace's owners, the project's maker and anyone given Read and write, and"
        " decide who reaches it change this rule."
    ) in words.replace("&#39;", "'")


def test_sharing_words_count_a_person_at_manage_as_named(env):
    """web/sharing.py: a person at "manage" is one of the people a private project names."""
    env.cy.set_project_access(env.journal.id, people={env.brook.user_id: "manage"})
    for who in (env.cy, env.brook):
        page = env.client.get("/b/journal", headers=as_(env, who)).text
        assert "Private: you and 1 other" in page, who.user_id
    page = env.client.get("/b/journal", headers=as_(env, env.ada)).text
    assert "Private: 2 people" in page


def test_a_list_with_addresses_of_no_member_names_them_and_says_to_invite_first(env):
    cy = as_(env, env.cy)
    typed = "brook@example.test zed@elsewhere.example.test;Zed@Elsewhere.example.test,<b>@x.test"
    r = send(env, cy, "/b/journal/share", {"emails": typed, "level": "read"})
    assert r.status_code == 200
    notice = r.text.split('<section class="notice"', 1)[1].split("</section>", 1)[0]
    assert "zed@elsewhere.example.test" in notice and "Zed@Elsewhere" not in notice  # once
    assert "&lt;b&gt;@x.test" in notice and "<b>@" not in r.text
    assert "invite" in notice.lower() and 'href="/members"' in notice
    assert env.cy.project_access(env.journal.id).people == {env.brook.user_id: "read"}


@pytest.mark.parametrize(
    "emails, level",
    [("", "read"), ("brook@example.test brook", "read"), ("brook@example.test", "admin")],
)
def test_a_bad_list_is_refused_on_the_page_with_what_was_typed(env, emails, level):
    cy = as_(env, env.cy)
    before = dump(env.db)
    r = send(env, cy, "/b/journal/share", {"emails": emails, "level": level})
    assert r.status_code == 400
    assert '<p class="alert" role="alert">' in r.text
    assert 'action="/b/journal/share"' in r.text  # the access page, to try again
    if emails:
        assert f">{emails}</textarea>" in r.text
    assert dump(env.db) == before


def test_sharing_over_the_web_answers_like_the_access_page(env):
    env.cy.set_project_access(env.journal.id, people={env.brook.user_id: "write"})
    data = {"emails": "ada@example.test", "level": "read"}
    assert send(env, as_(env, env.brook), "/b/journal/share", data).status_code == 403
    assert send(env, as_(env, env.cy), "/b/nowhere/share", data).status_code == 404
    env.cy.set_project_access(env.journal.id, people={})
    assert send(env, as_(env, env.brook), "/b/journal/share", data).status_code == 404


def test_a_private_project_is_made_from_the_home_page(env):
    made = send(env, as_(env, env.cy), "/projects", {"name": "notes", "private": "on"})
    assert made.status_code == 303
    project = env.cy.get_project("notes")
    assert env.cy.project_access(project.id).everyone == "none"
    assert 'name="private"' in env.client.get("/", headers=as_(env, env.cy)).text


def test_the_keys_page_names_who_made_each_key(env):
    page = env.client.get("/keys", headers=as_(env, env.ada)).text
    row = next(line for line in page.split("<tr>") if "brook-key" in line)
    assert "Brook" in row


def test_a_project_not_open_to_every_member_says_so_to_whoever_sees_it(env):
    env.cy.set_project_access(env.journal.id, people={env.brook.user_id: "read"})
    env.ada.create_project("handbook", everyone="read")
    env.ada.create_project("ops")
    said = {
        "cy": "Private: you and 1 other",
        "brook": "Private: you and 1 other",
        "ada": "Private: 2 people",  # an owner reaches it without being named
    }
    for who, words in said.items():
        cookie = as_(env, getattr(env, who))
        home = env.client.get("/", headers=cookie).text
        project = env.client.get("/b/journal", headers=cookie).text
        assert words in home and words in project, who
        assert "Members read only" in home and "Members read only" not in project
        ops = env.client.get("/b/ops", headers=cookie).text
        assert "Private" not in ops and "read only" not in ops  # open to every member
    assert "Members read only" in env.client.get("/b/handbook", headers=as_(env, env.cy)).text
    env.cy.set_project_access(env.journal.id, people={})
    assert "Private: you" in env.client.get("/b/journal", headers=as_(env, env.cy)).text
    assert "journal" not in env.client.get("/", headers=as_(env, env.brook)).text


def test_a_persons_own_list_of_workspaces_marks_their_name(env):
    page = env.client.get("/workspaces", headers=as_(env, env.cy)).text
    nav = page.split('aria-label="Main"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/workspaces" aria-current="page"' in nav
    assert nav.count('aria-current="page"') == 1


def row_of(page: str, name: str) -> str:
    body = page.split('aria-labelledby="people"', 1)[1].split("</tbody>", 1)[0]
    person = 'data-label="Name"><span class="actor"><span class="actor-kind">person</span>'
    named = f'<span class="actor-name">{name}</span>'
    return next(row for row in body.split("<tr>") if person in row and named in row)


def test_the_only_owners_row_offers_no_role_change_and_no_removal(env):
    ada = env.ada.user_id
    for viewer in (env.ada, env.brook):  # the owner herself, and an admin
        page = env.client.get("/members", headers=as_(env, viewer)).text
        own = row_of(page, "Ada")
        assert '<td data-label="Actions" class="act">The only owner</td>' in own
        assert f"/members/{ada}/role" not in page and f"/members/{ada}/remove" not in page
        assert f"/members/{env.cy.user_id}/role" in row_of(page, "Cy")
    env.ada.set_role(env.brook.user_id, "owner")
    page = env.client.get("/members", headers=as_(env, env.ada)).text
    assert "The only owner" not in page  # two owners: either may step down
    assert f"/members/{ada}/role" in row_of(page, "Ada")
