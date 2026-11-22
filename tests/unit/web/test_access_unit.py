"""Unit tests for who reaches a project: a person's reach, applied to every store method that
names a project. The inner TDD loop under tests/acceptance/test_t1_members.py."""

import io
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairn.store import Claim, NameTaken, Store

ISSUER = "https://id.example.test/pool-1"
REDIRECT = "http://127.0.0.1:33418/callback"
SOURCE = {
    "base_url": "https://langfuse.example.test",
    "public_key_env": "LANGFUSE_PUBLIC_KEY",
    "secret_key_env": "LANGFUSE_SECRET_KEY",
}


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


@pytest.fixture
def team(store):
    """Ada owns the default workspace, Brook administers it, Cy is a member. Cy made
    "journal", private; "handbook" is open to everyone."""
    views = {}
    for name in ("Ada", "Brook", "Cy"):
        signed = store.sign_in(
            "sso",
            f"sub-{name}",
            f"{name.lower()}@example.test",
            name,
            issuer=ISSUER,
            join="default",
        )
        views[name.lower()] = store.for_account(signed.account_id, signed.user.id)
    views["ada"].set_role(views["brook"].user_id, "admin")
    handbook = views["ada"].create_project("handbook")
    journal = views["cy"].create_project("journal", everyone="none")
    return SimpleNamespace(store=store, handbook=handbook, journal=journal, **views)


def test_a_private_projects_name_is_taken_but_nothing_else_is_told(team, db):
    before = dump(db)
    with pytest.raises(NameTaken) as taken:
        team.brook.create_project("Journal")
    assert team.journal.id not in str(taken.value)
    with pytest.raises(NameTaken) as again:
        team.brook.create_projects([("journal", "general")])  # same kind: not handed back
    assert not hasattr(again.value, "project")
    assert dump(db) == before
    assert [b.name for b in team.cy.create_projects([("journal", "general")])] == ["journal"]


def test_a_view_never_sheds_or_swaps_its_person(team, db):
    """Only the root store binds an identity: a view gets itself back for its own workspace
    and a PermissionError for another workspace or another person."""
    hidden, _ = team.cy.add_note(team.journal.id, "# Diary\n\nA private line.\n")
    other = team.store.create_account("Elsewhere").id
    before = dump(db)
    for view in (team.brook, team.cy, team.store.narrow(None, "read")):
        assert view.for_account(view.account_id) is view
        with pytest.raises(PermissionError):
            view.for_account(other)
    for view in (team.brook, team.cy):
        with pytest.raises(PermissionError):  # another person: no impersonation
            view.for_account(view.account_id, team.ada.user_id)
    shed = team.brook.for_account(team.store.account_id)  # the admin, as before
    assert shed.get_note(hidden.id) is None  # the private project stays out of reach
    with pytest.raises(PermissionError):
        archive_of(shed)  # exporting stays an owner's
    with pytest.raises(PermissionError):
        team.cy.for_account(team.store.account_id).set_role(team.cy.user_id, "owner")
    assert dump(db) == before


def test_a_view_kept_across_a_removal_writes_nothing(team, db):
    """A request admitted just before its person was removed: every write that view could
    make is refused inside its own transaction, and nothing is written."""
    dee = team.store.sign_in("sso", "sub-Dee", "dee@example.test", "Dee", issuer=ISSUER,
                             join="personal")  # fmt: skip
    invitation = team.ada.invite("dee@example.test", role="admin")
    team.store.accept_invite(invitation.token, dee.user.id)
    kept = team.store.for_account(team.store.account_id, dee.user.id)  # admitted as an admin
    client = team.store.register_client("dee-agent", [REDIRECT])
    team.ada.remove_member(dee.user.id)
    before = dump(db)
    writes = {
        "create_project": lambda: kept.create_project("dees"),
        "create_projects": lambda: kept.create_projects([("dees", "general")]),
        "create_session": lambda: kept.create_session(),
        "create_code": lambda: kept.create_code(client.id, REDIRECT, "c" * 43),
        "create_key": lambda: kept.create_key("dee-ci"),
        "create_set": lambda: kept.create_set("dees"),
        "set_setting": lambda: kept.set_setting("guard.policy", "off"),
        "invite": lambda: kept.invite("eve@example.test"),
        "set_project_access": lambda: kept.set_project_access(team.handbook.id, everyone="read"),
    }
    for name, write in writes.items():
        with pytest.raises(PermissionError, match="no longer a member"):
            write()
        assert dump(db) == before, name


def test_owners_reach_every_project_and_admins_what_the_rules_give(team):
    assert [b.name for b in team.ada.list_projects()] == ["handbook", "journal"]
    assert [b.name for b in team.brook.list_projects()] == ["handbook"]
    assert team.store.list_projects() == team.ada.list_projects()  # no person: every project


def test_a_read_level_refuses_every_write_into_the_project(team):
    team.cy.set_project_access(team.journal.id, people={team.brook.user_id: "read"})
    claim = Claim(id="cl_1", text="A line.", span_ids=())
    for write in (
        lambda: team.brook.add_note(team.journal.id, "# Note\n\nA line.\n"),
        lambda: team.brook.save_page(team.journal.id, "note", "Note", "", (claim,)),
        lambda: team.brook.record_note(team.journal.id, "unchanged", {}),
    ):
        with pytest.raises(PermissionError, match="read-only"):
            write()
    assert team.brook.list_notes(team.journal.id) == []


def test_a_person_who_left_reaches_nothing(team):
    team.ada.remove_member(team.brook.user_id)
    assert team.brook.list_projects() == [] and team.brook.search("anything") == []
    for project_id in (team.handbook.id, "br_000000000000"):  # the same answer for any id
        with pytest.raises(PermissionError, match="no longer a member"):
            team.brook.add_note(project_id, "# Late\n\nA line.\n")


def test_a_rule_can_name_only_members_and_changing_nothing_writes_nothing(team, db):
    with pytest.raises(ValueError):
        team.cy.set_project_access(team.journal.id, everyone="all")
    with pytest.raises(ValueError):
        team.cy.set_project_access(team.journal.id, people={team.ada.user_id: "admin"})
    before = dump(db)
    team.cy.set_project_access(team.journal.id, everyone="none", people={})
    assert dump(db) == before


def test_manage_is_write_wherever_the_store_reads_a_persons_level(team):
    """A person at "manage" (T2) reaches the project in every read and writes in every write a
    person at "write" does: the projects list, search, a note, a page, a recorded outcome, and
    a source that writes there."""
    brook, journal = team.brook, team.journal
    team.cy.add_note(journal.id, "# Diary\n\nThe Quill review is on Thursday.\n")
    team.cy.set_project_access(journal.id, people={brook.user_id: "manage"})
    assert [b.name for b in brook.list_projects()] == ["handbook", "journal"]
    assert brook.get_project("journal").id == journal.id
    assert [c.title for c in brook.list_notes(journal.id)] == ["Diary"]
    claim = Claim(id="cl_1", text="A line.", span_ids=())
    brook.add_note(journal.id, "# Note\n\nA line.\n")
    brook.save_page(journal.id, "note", "Note", "", (claim,))
    brook.record_note(journal.id, "unchanged", {})
    assert brook.search("line") and {hit.project_id for hit in brook.search("line")} == {journal.id}
    brook.add_source("diary", "langfuse", {**SOURCE, "projects": ["journal"]})
    assert [s.name for s in brook.list_sources()] == ["diary"]
    assert brook.project_sharing(journal.id).people == {team.cy.user_id, brook.user_id}


def test_a_source_is_refused_for_a_project_its_adder_only_reads(team):
    """The level a source asks for is "write", and "manage" holds it (above); "read" does not."""
    team.cy.set_project_access(team.journal.id, people={team.brook.user_id: "read"})
    with pytest.raises(PermissionError, match="read-only"):
        team.brook.add_source("diary", "langfuse", {**SOURCE, "projects": ["journal"]})


def test_manage_lets_a_person_read_and_set_the_rule_and_nothing_more(team, db):
    """project_access and set_project_access open to a person at "manage" on that project, not on
    another project, and not to a person at "write"; deleting and forgetting stay closed."""
    brook, journal = team.brook, team.journal
    noted, _ = team.cy.add_note(journal.id, "# Diary\n\nA private line.\n")
    team.cy.set_project_access(journal.id, people={brook.user_id: "write"})
    with pytest.raises(PermissionError):
        brook.project_access(journal.id)
    team.cy.set_project_access(journal.id, people={brook.user_id: "manage"})
    assert brook.project_access(journal.id).people == {brook.user_id: "manage"}
    rule = brook.set_project_access(journal.id, everyone="read")
    assert (rule.everyone, rule.people) == ("read", {brook.user_id: "manage"})
    with pytest.raises(PermissionError):
        brook.set_project_access(team.handbook.id, everyone="read")  # not his to decide
    before = dump(db)
    assert brook.may_erase(journal.id) is False
    with pytest.raises(PermissionError):
        brook.forget_note(noted.id)
    with pytest.raises(PermissionError):
        brook.delete_project(journal.id)
    assert dump(db) == before


def test_everyone_never_gets_manage_and_a_bad_persons_level_names_the_three(team, db):
    before = dump(db)
    with pytest.raises(ValueError):
        team.cy.set_project_access(team.journal.id, everyone="manage")
    with pytest.raises(ValueError, match="'manage', 'write' or 'read'"):
        team.cy.set_project_access(team.journal.id, people={team.brook.user_id: "admin"})
    assert dump(db) == before


def test_a_narrowed_view_of_a_person_at_manage_reads_the_rule_and_changes_nothing(team, db):
    """project_access is a read limited to the reach, as for the maker; set_project_access is
    management, refused on any narrowed view (cairn.store.reach)."""
    team.cy.set_project_access(team.journal.id, people={team.brook.user_id: "manage"})
    before = dump(db)
    for narrowed in (team.brook.narrow([team.journal.id]), team.brook.narrow(None, "read")):
        assert narrowed.project_access(team.journal.id).people == {team.brook.user_id: "manage"}
        with pytest.raises(PermissionError):
            narrowed.set_project_access(team.journal.id, everyone="read")
    with pytest.raises(KeyError):
        team.brook.narrow([team.handbook.id]).project_access(team.journal.id)
    assert dump(db) == before


def test_sharing_with_a_list_records_ids_and_levels_and_never_an_address(team):
    """share_project writes one project.access_changed whose detail is set_project_access's (what
    everyone gets and the people by id), so no address typed, a member's or not, is kept."""
    shared = team.cy.share_project(
        team.journal.id, "manage", ["brook@example.test", "zed@elsewhere.example.test"]
    )
    assert shared.granted == (team.brook.user_id,)
    assert shared.unknown == ("zed@elsewhere.example.test",)
    event = team.store.list_audit()[0]
    assert (event.action, event.project) == ("project.access_changed", "journal")
    assert event.detail == {
        "everyone": "none",
        "people": {team.brook.user_id: "manage"},
        "user": team.cy.user_id,
    }
    assert "example.test" not in str(event.detail)


def test_an_address_repeated_in_another_case_is_reported_once_as_first_typed(team, db):
    before = dump(db)
    typed = [
        "Zed@Elsewhere.Example.Test",
        " zed@elsewhere.example.test",
        "ZED@ELSEWHERE.EXAMPLE.TEST",
    ]
    shared = team.cy.share_project(team.journal.id, "read", typed)
    assert (shared.granted, shared.unknown) == ((), ("Zed@Elsewhere.Example.Test",))
    assert dump(db) == before


def test_a_list_is_a_collection_of_addresses_and_a_bad_one_is_named_by_position(team, db):
    before = dump(db)
    for emails in ("brook@example.test", [None], [42], ("brook@example.test", b"x@y.test")):
        with pytest.raises(ValueError):
            team.cy.share_project(team.journal.id, "read", emails)
    with pytest.raises(ValueError, match="entry 2 ") as refused:
        team.cy.share_project(team.journal.id, "read", ["brook@example.test", "brook example"])
    assert "brook" not in str(refused.value)
    assert dump(db) == before


def test_someone_who_left_the_workspace_is_unknown_to_a_list(team, db):
    team.ada.remove_member(team.brook.user_id)
    before = dump(db)
    shared = team.cy.share_project(team.journal.id, "write", ["brook@example.test"])
    assert (shared.granted, shared.unknown) == ((), ("brook@example.test",))
    assert dump(db) == before


def test_whoever_reaches_a_project_reads_how_far_it_is_shared(team, db):
    """project_sharing: what any member gets and the people the rule names (its maker and
    `people`), for anyone who reaches the project; unknown, like the project, to anyone else."""
    team.cy.set_project_access(team.journal.id, people={team.brook.user_id: "read"})
    before = dump(db)
    for view in (team.ada, team.brook, team.cy):
        shared = view.project_sharing(team.journal.id)
        assert shared.everyone == "none"
        assert shared.people == frozenset({team.cy.user_id, team.brook.user_id})
    open_to_all = team.cy.project_sharing(team.handbook.id)
    assert open_to_all.everyone == "write" and open_to_all.people == {team.ada.user_id}
    assert dump(db) == before  # reading it writes nothing
    team.cy.set_project_access(team.journal.id, people={})
    with pytest.raises(KeyError):
        team.brook.project_sharing(team.journal.id)  # out of Brook's reach now
    with pytest.raises(KeyError):
        team.store.narrow(projects=(team.handbook.id,)).project_sharing(team.journal.id)
    assert team.store.project_sharing(team.journal.id).people == {team.cy.user_id}


def test_sets_show_and_keep_only_what_a_person_reaches(team):
    both = team.store.create_set("both", (team.handbook.id, team.journal.id))
    assert team.brook.get_set("both").project_ids == (team.handbook.id,)
    assert [s.project_ids for s in team.brook.list_sets()] == [(team.handbook.id,)]
    with pytest.raises(ValueError):
        team.brook.create_set("mine", (team.journal.id,))  # unknown, for Brook
    with pytest.raises(ValueError):
        team.brook.update_set(both.id, (team.journal.id,))
    team.brook.update_set(both.id, ())  # Brook empties what Brook sees of it
    assert team.store.get_set("both").project_ids == (team.journal.id,)  # the rest stays


def test_keys_grants_and_codes_name_only_projects_their_maker_reaches(team):
    team.ada.create_key("ada-journal", projects=(team.journal.id,), access="read")
    assert [k.projects for k in team.brook.list_keys()] == [()]
    assert [k.projects for k in team.ada.list_keys()] == [(team.journal.id,)]
    client = team.store.register_client("brook-agent", [REDIRECT])
    with pytest.raises(ValueError):
        team.brook.create_code(client.id, REDIRECT, "c" * 43, projects=(team.journal.id,))
    code = team.ada.create_code(client.id, REDIRECT, "c" * 43, projects=(team.journal.id,))
    team.store.redeem_code(code, client.id)
    assert [g.projects for g in team.ada.list_grants()] == [(team.journal.id,)]


def test_a_source_that_names_a_project_out_of_reach_is_shown_without_it(team):
    """Every manager sees every source (findings 2 and 3), and where it writes only as far
    as they reach; a run or a removal still finds only a source they reach all of."""
    team.ada.add_source("diary", "langfuse", {**SOURCE, "projects": ["journal"]})
    team.ada.add_source("help", "langfuse", {**SOURCE, "projects": ["handbook"]})
    assert [s.name for s in team.brook.list_sources()] == ["diary", "help"]
    diary = team.brook.get_source("diary")
    assert (diary.config["projects"], diary.unreached) == ([], True)
    assert "journal" not in diary.model_dump_json()
    assert team.brook.remove_source("diary") is False
    with pytest.raises(KeyError):
        team.brook.claim_source("diary")
    with pytest.raises(ValueError, match="no project named"):
        team.brook.add_source("mine", "langfuse", {**SOURCE, "projects": ["journal"]})
    assert [s.name for s in team.ada.list_sources()] == ["diary", "help"]


def test_the_audit_log_leaves_out_what_a_person_does_not_reach(team):
    """Brook administers the workspace and reads its log, but is not in the rule of Cy's
    private journal: its events are left out for him (their slugs come from note titles),
    and its id is left out of every event's reach. Owners see everything."""
    claim = Claim(id="cl_1", text="A line.", span_ids=())
    team.ada.save_page(team.handbook.id, "open", "Open", "", (claim,))
    team.ada.create_key("both", projects=(team.handbook.id, team.journal.id), access="read")
    team.cy.save_page(team.journal.id, "salary-review", "Salary review", "", (claim,))

    def events(view) -> list[tuple[str, str | None]]:
        return [(e.action, e.project) for e in view.list_audit()]

    assert ("note.saved", "journal") in events(team.ada)
    assert ("project.created", "journal") in events(team.ada)
    seen = events(team.brook)
    assert all(project != "journal" for _, project in seen)
    assert ("note.saved", "handbook") in seen  # what he reaches is all there
    (key,) = [e for e in team.brook.list_audit() if e.action == "key.created"]
    assert key.detail["projects"] == [team.handbook.id]
    (whole,) = [e for e in team.ada.list_audit() if e.action == "key.created"]
    assert whole.detail["projects"] == [team.handbook.id, team.journal.id]
    assert "salary" not in str(team.brook.list_audit(100_000))
    (newest,) = team.brook.list_audit(1)  # the newest event of all is the journal's
    assert newest.action == "key.created"  # the limit counts only what he may see


def test_an_event_about_the_whole_workspaces_content_is_for_owners_only(team, tmp_path):
    """workspace.imported names no project but counts what came into projects private to the
    importer: only owners and views acting for no person see it, even an admin who reaches
    every project does not."""
    other = Store(tmp_path / "other.db")
    try:
        other.create_project("elsewhere")
        archive = archive_of(other)
    finally:
        other.close()
    team.ada.import_export(io.BytesIO(archive))

    def imported(view) -> bool:
        return "workspace.imported" in [e.action for e in view.list_audit()]

    assert imported(team.ada) and imported(team.store)
    assert not imported(team.brook) and not imported(team.cy)
    for project in team.ada.list_projects():  # Brook now reaches every project
        team.ada.set_project_access(project.id, people={team.brook.user_id: "read"})
    assert team.brook.list_projects() == team.ada.list_projects()
    assert not imported(team.brook)


def test_a_source_out_of_reach_cannot_be_claimed_or_run(team, db):
    """An admin who does not reach a private project cannot take a source configured for it,
    and so never reads its config (the hidden project's name): it is unknown there."""
    team.ada.add_source("diary", "langfuse", {**SOURCE, "projects": ["journal"]})
    theirs = team.ada.claim_source("diary")  # a claim Brook should not be able to use
    before = dump(db)
    with pytest.raises(KeyError):
        team.brook.claim_source("diary")
    assert team.brook.holds_source_claim(theirs) is False
    assert team.brook.finish_source_run(theirs, "c", {"saved": 1}) is False
    assert team.brook.fail_source_run(theirs, "boom") is False
    assert team.brook.remove_source("diary") is False
    assert dump(db) == before
    assert team.ada.holds_source_claim(theirs) is True


def test_a_declined_drafts_link_is_shown_only_to_who_reaches_its_project(team):
    team.ada.add_source("help", "langfuse", {**SOURCE, "projects": ["handbook"]})
    claim = team.store.claim_source("help")
    declined = [  # a run before the config, a rename or a rule changed: any project at all
        {"link": "https://lf.test/t/open", "status": "refused", "codes": ["a"],
         "project": team.handbook.id},
        {"link": "https://lf.test/t/private", "status": "rejected", "codes": ["b"],
         "project": team.journal.id},
        {"link": "https://lf.test/t/unknown", "status": "refused", "codes": ["c"]},
    ]  # fmt: skip
    team.store.finish_source_run(claim, "c", {"refused": 2, "rejected": 1, "declined": declined})
    assert team.brook.get_source("help").last_report["declined"] == declined[:1]
    assert [s.last_report["declined"] for s in team.brook.list_sources()] == [declined[:1]]
    assert team.ada.get_source("help").last_report["declined"] == declined  # every project
    assert team.brook.get_source("help").last_report["refused"] == 2  # the counts stay


def test_the_audit_log_shows_an_admin_every_live_source_and_no_removed_one(team):
    """Every owner and admin sees every source (list_sources), whatever it writes into, in
    the log as well (findings 2, 3 and 16); a removed source's events, refiled, are an
    owner's only."""
    team.ada.add_source("diary", "langfuse", {**SOURCE, "projects": ["journal"]})
    team.ada.add_source("help", "langfuse", {**SOURCE, "projects": ["handbook"]})
    team.store.remove_source("help")  # the command line's: source-add made it
    team.ada.add_source("gone", "langfuse", {**SOURCE, "projects": ["journal"]})
    team.store.remove_source("gone")  # no longer shown to anyone: left out for a person too

    def sources(view) -> list[tuple[str, str]]:
        return [(e.action, e.detail["source"]) for e in view.list_audit() if "source" in e.detail]

    names = {name for _, name in sources(team.ada)}
    assert {name.split(" (removed ")[0] for name in names} == {"diary", "help", "gone"}
    assert "diary" in names and not {"help", "gone"} & names  # refiled when removed
    assert sources(team.brook) == [("source.added", "diary")]  # removed ones: not shown
    team.ada.add_source("help", "langfuse", {**SOURCE, "projects": ["handbook"]})
    # A later source of that name starts with none of the old one's events.
    assert sources(team.brook) == [("source.added", "help"), ("source.added", "diary")]


# Decision B: an archive holds every project, so for a person it is an owner's to take or bring


def archive_of(view) -> bytes:
    out = io.BytesIO()
    view.export(out)
    return out.getvalue()


def test_only_an_owner_exports_or_imports_for_a_person(team, db):
    team.ada.add_note(team.handbook.id, "# Open\n\nA line.\n")
    whole = archive_of(team.ada)  # an owner reaches every project, private ones included
    assert set(zipfile.ZipFile(io.BytesIO(whole)).namelist()) >= {"projects/journal/notes.jsonl"}
    before = dump(db)
    for person in (team.brook, team.cy):  # an admin, a member
        with pytest.raises(PermissionError, match="owner"):
            archive_of(person)
        with pytest.raises(PermissionError, match="owner"):
            person.import_export(io.BytesIO(whole))
    assert dump(db) == before
    archive_of(team.store)  # a view acting for no person still may
    archive_of(team.store.narrow(None, "write"))  # as a workspace key of whole reach does

    def as_key(maker):  # the view a key of whole reach to write gets (web.auth.Accounts)
        found = team.store.resolve_key(maker.create_key(f"whole-{maker.role()}"))
        return team.store.for_account(found.account_id, user_id=found.user_id).narrow(
            found.projects, found.access
        )

    with pytest.raises(PermissionError, match="owner"):  # a key acts for whoever made it
        archive_of(as_key(team.brook))
    archive_of(as_key(team.ada))


def test_manage_on_every_project_neither_exports_nor_imports_and_no_archive_holds_it(
    team, db, tmp_path
):
    """Export and import stay the owners' (decision 49): a person at "manage" on every
    project is refused both, and an archive carries no rule, so a project with a "manage"
    entry comes back private with no people."""
    cy = team.cy.user_id
    team.ada.set_project_access(team.handbook.id, people={cy: "manage"})
    team.ada.set_project_access(team.journal.id, people={team.brook.user_id: "manage"})
    team.ada.add_note(team.handbook.id, "# Open\n\nA line.\n")
    whole = archive_of(team.ada)
    before = dump(db)
    for person in (team.brook, team.cy):
        with pytest.raises(PermissionError, match="owner"):
            archive_of(person)
        with pytest.raises(PermissionError, match="owner"):
            person.import_export(io.BytesIO(whole))
    assert dump(db) == before
    held = zipfile.ZipFile(io.BytesIO(whole))
    for name in held.namelist():
        text = held.read(name)
        assert b"manage" not in text and cy.encode() not in text, name
        assert team.brook.user_id.encode() not in text, name
    other = Store(tmp_path / "other.db")
    try:
        bare = other.for_account(other.create_account("Bare").id)
        bare.import_export(io.BytesIO(whole))
        for project in bare.list_projects():
            rule = bare.project_access(project.id)
            assert (rule.everyone, rule.people) == ("none", {}), project.name
    finally:
        other.close()


def test_an_imported_project_is_private_and_its_importer_makes_it(team, tmp_path):
    team.ada.add_note(team.handbook.id, "# Open\n\nA line.\n")
    whole = archive_of(team.store)
    other = Store(tmp_path / "other.db")
    try:
        eve = other.sign_in("sso", "e", "eve@example.test", "Eve", issuer=ISSUER, join="personal")
        mine = other.for_account(eve.account_id, eve.user.id)
        done = mine.import_export(io.BytesIO(whole))
        assert done.projects == 2 and "private" in done.notice
        for project in mine.list_projects():
            rule = mine.project_access(project.id)
            assert (project.maker_id, rule.everyone, rule.people) == (eve.user.id, "none", {})
    finally:
        other.close()
    third = Store(tmp_path / "third.db")
    try:  # a view acting for no person: no maker, so only the owners reach them
        bare = third.for_account(third.create_account("Bare").id)
        assert "owners" in bare.import_export(io.BytesIO(whole)).notice
        assert {b.maker_id for b in bare.list_projects()} == {None}
        assert {bare.project_access(b.id).everyone for b in bare.list_projects()} == {"none"}
    finally:
        third.close()
    assert archive_of(team.ada) and team.ada.export(io.BytesIO()).notice is None


def approve(team, view, name: str) -> str:
    client = team.store.register_client(name, [REDIRECT])
    team.store.redeem_code(view.create_code(client.id, REDIRECT, "c" * 43), client.id)
    return next(g.id for g in team.store.list_grants() if g.client_name == name)


def test_a_member_sees_and_ends_only_the_agents_they_approved(team):
    theirs, mine = approve(team, team.ada, "ada-laptop"), approve(team, team.cy, "cy-laptop")
    assert [g.id for g in team.cy.list_grants()] == [mine]
    assert [g.id for g in team.brook.list_grants()] == [theirs, mine]  # an admin: all
    assert team.cy.revoke_grant(theirs) is False  # as unknown
    assert [g.id for g in team.ada.list_grants()] == [theirs, mine]
    assert team.cy.revoke_grant(mine) is True
    assert team.brook.revoke_grant(theirs) is True


def test_a_key_acts_for_the_person_who_made_it_as_they_are_now(team):
    key = team.brook.create_key("brook-ci")
    operator = team.store.create_key("nightly")
    assert team.store.resolve_key(key).user_id == team.brook.user_id
    assert team.store.resolve_key(operator).user_id is None
    assert [(k.label, k.maker_id) for k in team.ada.list_keys()] == [
        ("brook-ci", team.brook.user_id),
        ("nightly", None),
    ]
    team.ada.set_role(team.brook.user_id, "member")
    as_key = team.store.for_account(team.store.account_id, team.store.resolve_key(key).user_id)
    assert as_key.manages() is False  # it acts for Brook as Brook is now
