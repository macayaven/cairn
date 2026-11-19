"""G1b acceptance tests (frozen): the workspace page says what the note guard refuses,
and the people who manage the workspace can change it there.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstring of src/cairn/server.py.

Why: G1 made the guard's policy a setting of the workspace, and nothing in the product
changes it. A person cannot see what their workspace refuses, and a team that wants
personal data kept out has no way to say so.

- GET /workspace has a section with the id "guard" for everyone in the workspace: what the
  guard refuses now, in words.
- For an owner or an admin the section holds a form posting to /workspace/guard with one
  choice named `policy` per value of `cairn.store.GUARD_POLICIES`, each with words a person
  can read, the workspace's current one chosen. A member sees the words and no form.
- POST /workspace/guard with one of those values changes the setting, recorded as one
  setting.changed event under the person, and redirects to /workspace. Choosing the value
  it already has changes and records nothing. A member gets 403. Another value, or none,
  is 400. Nothing changes on a refusal.
- The choice holds at once, for every door a note comes through.
- When the server was given a guard of its own (`create_app(guard=...)`), a workspace's
  choice would do nothing: the section says the operator of the server decides, there is
  no form, and the POST answers 409.
"""

import re
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.guard import PatternGuard
from cairn.server import create_app
from cairn.store import GUARD_POLICIES, Store

BASE = "https://cairn.example.test"
ISSUER = "https://id.acme.example/pool-1"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
TOKEN = "ghp_" + "Zq7xR2" * 6  # shaped like a GitHub token; it is not one
WITH_ADDRESS = "# Follow-up\n\nWrite to dana@acme.example about the renewal.\n"
WITH_TOKEN = f"# Deploy\n\nThe job runs with {TOKEN} today.\n"


class World:
    """The default workspace: Ada owns it, Brook is an admin, Cy is a member."""

    def __init__(self, tmp_path, **options) -> None:
        self.store = Store(tmp_path / "cairn.db")
        self.app = create_app(self.store, public_url=BASE, **options)
        self.ada, self.brook, self.cy = (self.person(name) for name in ("Ada", "Brook", "Cy"))
        self.ada.view.set_role(self.brook.id, "admin")
        self.project = self.ada.view.create_project("ops")

    def person(self, name: str) -> SimpleNamespace:
        email = f"{name.lower()}@acme.example"
        signed = self.store.sign_in(
            "sso", f"sub-{name}", email, name, issuer=ISSUER, join="default"
        )
        return SimpleNamespace(
            id=signed.user.id,
            name=name,
            cookie={"cookie": f"__Host-cairn_session={signed.token}"},
            view=self.store.for_account(signed.account_id, signed.user.id),
        )

    def policy(self) -> str:
        return self.store.get_setting("guard.policy", "secrets")

    def notes(self) -> int:
        return len(self.ada.view.list_notes(self.project.id))


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.store.close()


@pytest.fixture
def client(world):
    with TestClient(world.app, base_url=BASE) as opened:
        yield opened


def section(client, who) -> str:
    page = client.get("/workspace", headers=who.cookie, follow_redirects=False)
    assert page.status_code == 200
    assert page.text.count('id="guard"') == 1
    return page.text.split('id="guard"', 1)[1].split("</section>", 1)[0]


def words(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.split(">", 1)[1]).split())


def chosen(html: str) -> list[str]:
    """The values of the `policy` choices that are marked as the current one."""
    fields = re.findall(r"<(?:input|option)\b[^>]*>", html)
    marked = [f for f in fields if re.search(r"\b(checked|selected)\b", f)]
    return [re.search(r'value="([^"]*)"', f).group(1) for f in marked]


def choose(client, who, **data):
    headers = {**who.cookie, **SAME_ORIGIN}
    return client.post("/workspace/guard", data=data, headers=headers, follow_redirects=False)


def compose(client, who, text: str):
    headers = {**who.cookie, **SAME_ORIGIN}
    return client.post("/b/ops/notes", data={"text": text}, headers=headers, follow_redirects=False)


def test_everyone_reads_what_is_refused_and_managers_get_the_choice(world, client):
    for who in (world.ada, world.brook):
        html = section(client, who)
        assert 'action="/workspace/guard"' in html and 'name="policy"' in html
        for policy in GUARD_POLICIES:
            assert f'value="{policy}"' in html, policy
        assert chosen(html) == ["secrets"]
    for_cy = section(client, world.cy)
    assert "<form" not in for_cy and 'name="policy"' not in for_cy
    assert len(words(for_cy)) > 20  # a sentence, not a code
    assert not re.search(
        r"\bsecrets\+personal\b|\bguard\.policy\b", words(for_cy)
    )  # words, not ids


def test_a_manager_changes_it_and_it_holds_at_once(world, client):
    before = words(section(client, world.cy))
    assert compose(client, world.cy, WITH_ADDRESS).status_code == 303  # allowed by default
    assert world.notes() == 1

    done = choose(client, world.brook, policy="secrets+personal")
    assert (done.status_code, done.headers["location"]) == (303, "/workspace")
    assert world.policy() == "secrets+personal"
    assert chosen(section(client, world.ada)) == ["secrets+personal"]
    assert words(section(client, world.cy)) != before

    refused = compose(client, world.cy, WITH_ADDRESS.replace("renewal", "contract"))
    assert refused.status_code == 422  # the form comes back with their text, and nothing is kept
    assert world.notes() == 1

    events = [e for e in world.ada.view.list_audit() if e.action == "setting.changed"]
    assert [(e.actor_kind, e.actor, e.detail["name"]) for e in events] == [
        ("session", "Brook", "guard.policy")
    ]
    assert "personal" not in str(events[0].detail)  # which setting, as G1 records it; not its value
    assert choose(client, world.brook, policy="secrets+personal").status_code == 303  # no change
    assert len([e for e in world.ada.view.list_audit() if e.action == "setting.changed"]) == 1

    assert compose(client, world.ada, WITH_TOKEN).status_code == 422
    assert choose(client, world.ada, policy="off").status_code == 303
    assert compose(client, world.ada, WITH_TOKEN).status_code == 303  # they chose to check nothing
    assert world.notes() == 2


def test_a_member_and_a_wrong_value_change_nothing(world, client):
    assert choose(client, world.cy, policy="off").status_code == 403
    assert choose(client, world.ada, policy="everything").status_code == 400
    assert choose(client, world.ada).status_code == 400
    assert choose(client, world.ada, policy="").status_code == 400
    assert world.policy() == "secrets"
    assert [e for e in world.ada.view.list_audit() if e.action == "setting.changed"] == []
    signed_out = client.post(
        "/workspace/guard", data={"policy": "off"}, headers=SAME_ORIGIN, follow_redirects=False
    )
    assert signed_out.status_code in (303, 401) and world.policy() == "secrets"


def test_a_server_with_its_own_guard_leaves_no_choice(tmp_path):
    world = World(tmp_path, guard=PatternGuard("secrets"))
    try:
        with TestClient(world.app, base_url=BASE) as client:
            for who in (world.ada, world.cy):
                html = section(client, who)
                assert "<form" not in html and "operator" in words(html).lower()
            assert choose(client, world.ada, policy="off").status_code == 409
            assert world.policy() == "secrets"
            assert compose(client, world.ada, WITH_TOKEN).status_code == 422
    finally:
        world.store.close()
