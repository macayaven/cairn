"""Switching a source on and off (S2): what tests/acceptance/test_s2_source_switch.py leaves
open. A project made while switching on exists only when the switch worked; a source that
names no project is shown as such; the web view answers a refusal on the page it was made on."""

import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.sources import KINDS, SourceKind
from cairn.store import Store
from cairn.store.usage import Limits
from support.cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
ISSUER = "https://id.example.test/pool-1"


def one_project(config: object) -> dict:
    """A kind that starts off and writes into at most one project."""
    if not isinstance(config, dict) or not isinstance(config.get("projects", []), list):
        raise ValueError("projects lists the names of projects")
    if len(config.get("projects", [])) > 1:
        raise ValueError("this source writes into exactly one project")
    return {"projects": list(config.get("projects", []))}


def never_opened(name, config, **options):
    raise AssertionError("a source was read")


def dump(db) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    kind = SourceKind(check=one_project, open=never_opened, via="minutes", starts_off=True)
    monkeypatch.setitem(KINDS, "minutes", kind)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=lambda: datetime(2026, 11, 13, 9, tzinfo=UTC))
    store.create_project("meetings")
    store.add_source("standup", "minutes", {"projects": []})
    yield SimpleNamespace(store=store, db=db)
    store.close()


# A project made while switching on


def test_a_new_project_is_not_made_when_the_switch_is_refused(world):
    before = dump(world.db)
    with pytest.raises(ValueError, match="exactly one project"):  # the kind refuses two
        world.store.enable_source("standup", ["meetings"], new_project="board")
    with pytest.raises(PermissionError):  # a connection limited to projects manages nothing
        world.store.narrow([world.store.get_project("meetings").id]).enable_source(
            "standup", new_project="board"
        )
    with pytest.raises(KeyError):
        world.store.enable_source("nope", new_project="board")
    assert dump(world.db) == before and world.store.get_project("board") is None


def test_a_new_project_past_the_plans_cap_on_projects_makes_nothing(world):
    from cairn.store.usage import OverLimit

    capped = world.store.limited(Limits(projects=1))
    before = dump(world.db)
    with pytest.raises(OverLimit):
        capped.enable_source("standup", new_project="board")
    assert dump(world.db) == before


def test_a_new_project_is_made_and_switched_to_in_one_write(world):
    on = world.store.enable_source("standup", new_project="board")
    assert (on.enabled, on.config["projects"]) == (True, ["board"])
    project = world.store.get_project("board")
    assert (project.kind, world.store.project_access(project.id).everyone) == ("general", "none")
    made, enabled = world.store.list_audit(limit=2)[::-1]
    assert (made.action, made.project) == ("project.created", "board")
    assert (enabled.action, enabled.detail["projects"]) == ("source.enabled", ["board"])


def test_a_new_project_with_a_project_ticked_writes_into_both_when_the_kind_allows(world):
    multi = SourceKind(
        check=lambda config: {"projects": list(config.get("projects", []))},
        open=never_opened,
        via="multi",
    )
    KINDS["multi"] = multi
    try:
        world.store.add_source("feed", "multi", {"projects": ["meetings"]})
        on = world.store.enable_source("feed", ["meetings"], new_project="board")
        assert on.config["projects"] == ["meetings", "board"]
    finally:
        del KINDS["multi"]


def test_a_new_project_already_inserted_is_undone_when_a_later_check_refuses(world):
    multi = SourceKind(
        check=lambda config: {"projects": list(config.get("projects", []))},
        open=never_opened,
        via="multi",
    )
    KINDS["multi"] = multi
    try:
        world.store.add_source("feed", "multi", {"projects": ["meetings"]})
        before = dump(world.db)
        with pytest.raises(
            ValueError, match="no project named 'ghost'"
        ):  # checked after the insert
            world.store.enable_source("feed", ["ghost"], new_project="board")
        assert dump(world.db) == before and world.store.get_project("board") is None
    finally:
        del KINDS["multi"]


def test_switching_off_during_a_run_stops_it_writing_and_storing(world, monkeypatch):
    import asyncio

    import httpx

    from cairn.sources import NoteDraft, Pulled, SyncReport, run_source

    class Pulling:
        name = "standup"

        async def pull(self, cursor):
            world.store.disable_source("standup")  # someone switches it off meanwhile
            text = "# Standup\n\nAda moved the release to Friday.\n"
            draft = NoteDraft("meetings", "Standup", text, "meeting:1", "meeting")
            return Pulled(drafts=[draft], cursor="c1")

    kind = SourceKind(
        check=one_project, open=lambda *a, **k: Pulling(), via="minutes", starts_off=True
    )
    monkeypatch.setitem(KINDS, "minutes", kind)
    world.store.enable_source("standup", ["meetings"])

    def run():
        return asyncio.run(run_source(world.store, "standup", http=httpx.AsyncClient(), env={}))

    report = run()
    assert report.superseded and report.saved == 0
    off = world.store.get_source("standup")
    assert (off.enabled, off.cursor, off.last_run_at) == (False, None, None)
    assert world.store.list_notes(world.store.get_project("meetings").id) == []
    assert world.store.list_audit(limit=1)[0].action == "source.disabled"
    assert run() == SyncReport(off=True)
    world.store.enable_source("standup")
    assert not run().busy  # the claim the stopped run held went with the switch


def test_changing_where_it_writes_during_a_run_stops_it_writing_and_storing(world, monkeypatch):
    """Finding 8: a run under way when the source is moved to other projects wrote its
    remaining drafts into the old project and stored the cursor past them. A change of projects
    lets go of the run as switching off does; the next run starts from the last cursor."""
    import asyncio

    import httpx

    from cairn.sources import NoteDraft, Pulled, run_source

    world.store.create_project("planning")
    cursors: list[str | None] = []

    class Pulling:
        name = "standup"

        def __init__(self, config):
            self.project = config["projects"][0]

        async def pull(self, cursor):
            cursors.append(cursor)
            text = "# Standup\n\n{} moved the release to Friday.\n"
            drafts = [
                NoteDraft(self.project, "Standup", text.format(who), f"meeting:{who}", "meeting")
                for who in ("Ada", "Brook", "Cy")
            ]
            return Pulled(drafts=drafts, cursor=f"after-{len(cursors)}")

    kind = SourceKind(
        check=one_project,
        open=lambda name, config, **k: Pulling(config),
        via="minutes",
        starts_off=True,
    )
    monkeypatch.setitem(KINDS, "minutes", kind)
    world.store.enable_source("standup", ["meetings"])
    moved: list[str] = []

    def saved(project_id: str, slug: str) -> None:
        if not moved:  # after the first note, someone chooses another project
            moved.extend(world.store.enable_source("standup", ["planning"]).config["projects"])

    def run(**more):
        return asyncio.run(
            run_source(world.store, "standup", http=httpx.AsyncClient(), env={}, **more)
        )

    report = run(saved=saved)
    assert moved == ["planning"]
    assert report.superseded and report.saved == 1  # the one being written when it moved
    meetings = world.store.get_project("meetings").id
    assert len(world.store.list_notes(meetings)) == 1
    stored = world.store.get_source("standup")
    assert stored.cursor is None and stored.last_run_at is None  # nothing stored
    again = run()
    assert not again.busy and again.saved == 3  # it starts again from where it was
    assert cursors == [None, None]
    assert len(world.store.list_notes(world.store.get_project("planning").id)) == 3
    assert len(world.store.list_notes(meetings)) == 1


# Findings 2 and 3, rule (a): where an admin may send a source


def team(world):
    """Ada owns the workspace, Cy and Dee administer it, Dana is a member."""

    def person(name: str, role: str | None = None):
        signed = world.store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
        if role:
            world.store.set_role(signed.user.id, role)
        view = world.store.for_account(signed.account_id, signed.user.id)
        return SimpleNamespace(
            id=signed.user.id,
            view=view.acting_as("session", name, signed.user.id),
            cookie={"cookie": f"{session_cookie()}={signed.token}", **SAME_ORIGIN},
        )

    return SimpleNamespace(
        ada=person("Ada"), cy=person("Cy", "admin"), dee=person("Dee", "admin"), dana=person("Dana")
    )


def test_an_admin_sends_a_source_only_where_every_member_reaches(world):
    from cairn.store.sources import OWNER_DECIDES

    people = team(world)
    world.store.create_project("board", everyone="none")
    world.store.set_project_access(
        world.store.get_project("board").id, people={people.cy.id: "write"}
    )
    people.cy.view.create_project("cy-inbox", everyone="none")  # Cy's own, private
    before = dump(world.db)
    refusals = [
        lambda: people.cy.view.enable_source("standup", new_project="cy-quiet"),
        lambda: people.cy.view.enable_source("standup", new_project="board"),  # taken: same answer
        lambda: people.cy.view.enable_source("standup", ["board"]),  # Cy writes, Dana does not
        lambda: people.cy.view.enable_source("standup", ["cy-inbox"]),
    ]
    for call in refusals:
        with pytest.raises(PermissionError) as refused:
            call()
        assert str(refused.value) == OWNER_DECIDES
        assert dump(world.db) == before
    assert "owner" in OWNER_DECIDES and "every member" in OWNER_DECIDES

    on = people.cy.view.enable_source("standup", ["meetings"])  # every member writes there
    assert (on.enabled, on.config["projects"]) == (True, ["meetings"])
    people.ada.view.enable_source("standup", [], new_project="ada-board")  # an owner
    people.ada.view.enable_source("standup", ["board"])
    world.store.enable_source("standup", ["cy-inbox"])  # the operator: as before
    assert world.store.get_source("standup").config["projects"] == ["cy-inbox"]


def test_switching_on_with_its_own_projects_follows_the_same_rule(world):
    from cairn.store.sources import OWNER_DECIDES

    people = team(world)
    people.cy.view.create_project("cy-inbox", everyone="none")
    world.store.add_source("retro", "minutes", {"projects": ["cy-inbox"]})
    world.store.add_source("weekly", "minutes", {"projects": ["meetings"]})
    before = dump(world.db)
    with pytest.raises(PermissionError) as refused:
        people.cy.view.enable_source("retro")
    assert str(refused.value) == OWNER_DECIDES and dump(world.db) == before
    assert people.cy.view.enable_source("weekly").enabled
    assert people.ada.view.enable_source("retro").enabled


def test_the_sources_page_offers_an_admin_only_projects_every_member_reaches(world):
    people = team(world)
    people.cy.view.create_project("cy-inbox", everyone="none")
    world.store.create_project("handbook", everyone="read")
    app = create_app(world.store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        admin = client.get("/sources", headers=people.cy.cookie).text
        assert 'name="new_project"' not in admin
        assert 'value="meetings"' in admin and 'value="handbook"' in admin
        assert 'value="cy-inbox"' not in admin
        owner = client.get("/sources", headers=people.ada.cookie).text
        assert 'name="new_project"' in owner and 'value="cy-inbox"' in owner
        before = dump(world.db)
        for data in ({"new_project": "cy-quiet"}, {"project": "cy-inbox"}):
            answer = client.post(
                "/integrations/standup/on", data=data, headers=people.cy.cookie,
                follow_redirects=False,
            )  # fmt: skip
            assert answer.status_code == 400 and "owner" in answer.text
            assert 'role="alert"' in answer.text and dump(world.db) == before
        assert world.store.get_project("cy-quiet") is None


# Findings 2 and 3, rule (b): every manager sees every source and can stop it


def test_every_manager_sees_every_source_and_where_it_writes_only_as_far_as_they_reach(world):
    people = team(world)
    world.store.add_source("dana-mail", "minutes", {"projects": []})
    people.ada.view.enable_source("dana-mail", [], new_project="ada-board")  # owners only
    for view in (people.cy.view, people.dee.view):
        (shown,) = [s for s in view.list_sources() if s.name == "dana-mail"]
        assert (shown.kind, shown.enabled, shown.config["projects"]) == ("minutes", True, [])
        assert shown.unreached is True
        assert "ada-board" not in shown.model_dump_json()
        assert view.get_source("DANA-MAIL") == shown
    full = people.ada.view.get_source("dana-mail")
    assert (full.config["projects"], full.unreached) == (["ada-board"], False)
    assert world.store.get_source("dana-mail").config["projects"] == ["ada-board"]  # operator
    assert (
        people.dana.view.list_sources() == [] and people.dana.view.get_source("dana-mail") is None
    )
    narrowed = world.store.narrow([world.store.get_project("meetings").id])
    assert narrowed.list_sources() == [] and narrowed.get_source("dana-mail") is None
    assert world.store.narrow(None, "read").list_sources() == []


def test_any_manager_switches_off_a_source_whatever_it_writes_into(world):
    from cairn.store.sources import OWNER_DECIDES

    people = team(world)
    world.store.add_source("dana-mail", "minutes", {"projects": []})
    people.ada.view.enable_source("dana-mail", [], new_project="ada-board")
    off = people.cy.view.disable_source("dana-mail")
    assert (off.enabled, off.config["projects"], off.unreached) == (False, [], True)
    assert world.store.get_source("dana-mail").enabled is False
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor) == ("source.disabled", "Cy")
    before = dump(world.db)
    with pytest.raises(PermissionError) as refused:  # on again, into a project Cy does not reach
        people.cy.view.enable_source("dana-mail")
    assert str(refused.value) == OWNER_DECIDES and "ada-board" not in str(refused.value)
    assert dump(world.db) == before
    with pytest.raises(PermissionError):
        people.dana.view.disable_source("dana-mail")  # a member: as before
    # Nor may Cy move it out of the owner's project into one he reads: only an owner does.
    for call in (
        lambda: people.cy.view.enable_source("dana-mail", ["meetings"]),
        lambda: people.dee.view.enable_source("dana-mail", ["meetings"]),
    ):
        with pytest.raises(PermissionError) as refused:
            call()
        assert str(refused.value) == OWNER_DECIDES
    assert dump(world.db) == before
    moved = people.ada.view.enable_source("dana-mail", ["meetings"])
    assert moved.config["projects"] == ["meetings"]


def test_the_sources_page_shows_every_source_and_hides_projects_out_of_reach(world):
    people = team(world)
    world.store.add_source("dana-mail", "minutes", {"projects": []})
    people.ada.view.enable_source("dana-mail", [], new_project="ada-board")
    app = create_app(world.store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        page = client.get("/sources", headers=people.dee.cookie)
        assert page.status_code == 200
        assert 'id="source-dana-mail"' in page.text and "ada-board" not in page.text
        assert "a project you do not reach" in page.text
        assert 'action="/integrations/dana-mail/off"' in page.text
        assert 'action="/integrations/dana-mail/on"' not in page.text  # an owner's choice
        stopped = client.post(
            "/integrations/dana-mail/off", headers=people.dee.cookie, follow_redirects=False
        )
        assert stopped.status_code == 303 and not world.store.get_source("dana-mail").enabled
        owner = client.get("/sources", headers=people.ada.cookie).text
        assert "ada-board" in owner and "a project you do not reach" not in owner


def test_an_invalid_new_project_name_is_refused_before_anything_is_read(world):
    before = dump(world.db)
    for name in ("not a name!", "br_000000000000", ""):
        with pytest.raises(ValueError):
            world.store.enable_source("standup", new_project=name)
    assert dump(world.db) == before


# The web view


@pytest.fixture
def web(world):
    signed = world.store.sign_in(
        "sso", "sub-ada", "ada@example.test", "Ada", issuer=ISSUER, join="default"
    )
    cookie = {"cookie": f"{session_cookie()}={signed.token}", **SAME_ORIGIN}
    app = create_app(world.store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, cookie=cookie)


def test_a_refusal_is_said_on_the_sources_page_with_its_forms(world, web):
    answer = web.client.post(
        "/integrations/standup/on",
        data={"project": "meetings", "new_project": "board"},
        headers=web.cookie,
        follow_redirects=False,
    )
    assert answer.status_code == 400
    assert "exactly one project" in answer.text and 'role="alert"' in answer.text
    assert 'action="/integrations/standup/on"' in answer.text  # the forms are still there
    assert world.store.get_project("board") is None


def test_an_empty_project_field_is_no_project(world, web):
    answer = web.client.post(
        "/integrations/standup/on",
        data={"project": "", "new_project": "  "},
        headers=web.cookie,
        follow_redirects=False,
    )
    assert answer.status_code == 400
    assert "name one" in answer.text  # NO_PROJECT_NAMED, not "no project named ''"


def test_a_source_naming_no_project_says_so_and_offers_every_project_it_could_write(world, web):
    page = web.client.get("/sources", headers=web.cookie).text
    assert "No project yet" in page
    assert 'name="project" value="meetings"' in page


def test_a_project_the_admin_may_only_read_is_refused_on_the_page(world, web):
    world.store.create_project("handbook", everyone="read")
    member = world.store.sign_in(
        "sso", "sub-cy", "cy@example.test", "Cy", issuer=ISSUER, join="default"
    )
    world.store.set_role(member.user.id, "admin")
    cy = {"cookie": f"{session_cookie()}={member.token}", **SAME_ORIGIN}
    before = dump(world.db)
    answer = web.client.post(
        "/integrations/standup/on", data={"project": "handbook"}, headers=cy, follow_redirects=False
    )
    assert answer.status_code == 400 and "read-only" in answer.text
    assert dump(world.db) == before
