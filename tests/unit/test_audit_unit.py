"""Unit tests for workspace settings and the audit log in the store: the inner TDD loop under
tests/acceptance/test_g1_guard.py."""

import itertools
import json
import sqlite3
from pathlib import Path

import pytest

import cairn.store.base
from cairn.memory import Memory
from cairn.store import GUARD_POLICIES, SETTINGS, AuditEvent, Claim, StalePage, Store

REDIRECT = "http://127.0.0.1:33418/callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
ISSUER = "https://id.example.test/pool-1"


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


def actions(view) -> list[str]:
    return [event.action for event in reversed(view.list_audit())]


def connect_agent(store, view, name="test-agent"):
    """An agent approved by `view` and connected: its grant id and token pair."""
    client = store.register_client(name, [REDIRECT])
    pair = store.redeem_code(view.create_code(client.id, REDIRECT, CHALLENGE), client.id)
    (grant,) = [g for g in view.list_grants() if g.client_id == client.id]
    return client, grant, pair


# Settings


def test_a_setting_reads_its_default_until_it_is_set(store):
    assert SETTINGS == {"guard.policy": GUARD_POLICIES}
    assert GUARD_POLICIES == ("off", "secrets", "secrets+personal")
    assert store.get_setting("guard.policy") is None
    assert store.get_setting("guard.policy", "secrets") == "secrets"
    store.set_setting("guard.policy", "off")
    assert store.get_setting("guard.policy", "secrets") == "off"


@pytest.mark.parametrize(
    "name, value",
    [("guard.policy", "everything"), ("guard.policy", ""), ("colour", "blue"), ("", "off")],
)
def test_an_unknown_setting_or_value_is_refused_and_nothing_is_written(store, db, name, value):
    before = dump(db)
    with pytest.raises(ValueError):
        store.set_setting(name, value)
    assert dump(db) == before


def test_reading_an_unknown_setting_is_a_value_error(store):
    with pytest.raises(ValueError):
        store.get_setting("colour")


def test_settings_are_per_workspace(store):
    brook = store.for_account(store.create_account("Brook").id)
    brook.set_setting("guard.policy", "secrets+personal")
    assert store.get_setting("guard.policy") is None
    assert brook.get_setting("guard.policy") == "secrets+personal"


def test_setting_the_same_value_again_writes_nothing(store, db):
    store.set_setting("guard.policy", "off")
    before = dump(db)
    store.set_setting("guard.policy", "off")
    assert dump(db) == before and actions(store) == ["setting.changed"]


@pytest.mark.parametrize(
    "narrowing", [{"projects": ("x",)}, {"access": "read"}, {"projects": ("x",), "access": "read"}]
)
def test_a_narrowed_view_can_neither_read_nor_change_settings(store, db, narrowing):
    store.set_setting("guard.policy", "off")
    view = store.narrow(**narrowing)
    before = dump(db)
    with pytest.raises(PermissionError):
        view.get_setting("guard.policy", "secrets")
    with pytest.raises(PermissionError):
        view.set_setting("guard.policy", "secrets")
    assert dump(db) == before


def test_only_who_manages_the_workspace_changes_a_setting(store, db):
    """Owner or admin, or a view that acts for no person (a key, the command line); a member
    is refused inside the write, and nothing is written."""
    people = [
        store.sign_in("github", str(n), f"p{n}@example.test", name, issuer=ISSUER, join="default")
        for n, name in enumerate(("Olive Owner", "Max Member"))
    ]
    owner, member = (store.for_account(p.account_id, p.user.id) for p in people)
    assert (owner.role(), member.role()) == ("owner", "member")
    before = dump(db)
    with pytest.raises(PermissionError):
        member.set_setting("guard.policy", "off")
    assert dump(db) == before
    owner.set_setting("guard.policy", "secrets+personal")
    store.for_account(people[0].account_id).set_setting("guard.policy", "secrets")
    assert member.get_setting("guard.policy") == "secrets"


def test_reading_a_setting_writes_nothing(store, db):
    before = dump(db)
    store.get_setting("guard.policy", "secrets")
    assert dump(db) == before


# The actor


def test_a_bare_view_acts_as_system_and_acting_as_returns_another_view(store):
    assert store.actor == ("system", None)
    key = store.acting_as("key", "laptop")
    assert key.actor == ("key", "laptop") and store.actor == ("system", None)
    assert key.account_id == store.account_id
    assert key.narrow(access="read").actor == ("key", "laptop")  # narrowing keeps the actor
    for kind in ("key", "token", "session", "system"):
        assert store.acting_as(kind, None).actor == (kind, None)


@pytest.mark.parametrize("kind", ["person", "", "KEY"])
def test_an_unknown_actor_kind_is_a_value_error(store, kind):
    with pytest.raises(ValueError):
        store.acting_as(kind, "x")


def test_a_view_records_what_it_writes_under_its_actor(store):
    store.acting_as("key", "laptop").create_project("ops")
    (event,) = store.list_audit()
    assert isinstance(event, AuditEvent)
    assert (event.actor_kind, event.actor, event.action, event.project, event.detail) == (
        "key",
        "laptop",
        "project.created",
        "ops",
        {"kind": "general"},  # U1: the kind the project was made with
    )
    assert event.at


def test_a_view_that_acts_for_a_person_names_them_in_the_detail(store):
    signed = store.sign_in(
        "sso", "subject-1", "ada@example.test", "Ada", issuer=ISSUER, join="personal"
    )
    ada = store.for_account(signed.account_id, user_id=signed.user.id).acting_as("session", "Ada")
    ada.create_key("ci")
    event, opened = ada.list_audit()  # A1: the sign-in's session is on record, under Ada
    assert (event.actor_kind, event.actor) == ("session", "Ada")
    assert event.detail == {"label": "ci", "user": signed.user.id}
    assert (opened.action, opened.actor_kind, opened.actor) == ("session.opened", "session", "Ada")
    assert opened.detail == {"how": "sso", "user": signed.user.id}


# What is recorded


def test_projects_keys_and_settings_are_recorded_with_small_details(store):
    project = store.create_project("ops")
    store.create_key("laptop")
    store.create_key("reader", projects=(project.id,), access="read")
    assert store.revoke_key("nobody") is False  # nothing revoked, nothing recorded
    assert store.revoke_key("laptop") is True
    store.set_setting("guard.policy", "secrets+personal")
    events = list(reversed(store.list_audit()))
    assert [(e.action, e.project, e.detail) for e in events] == [
        ("project.created", "ops", {"kind": "general"}),
        ("key.created", None, {"label": "laptop"}),
        ("key.created", None, {"label": "reader", "projects": [project.id], "access": "read"}),
        ("key.revoked", None, {"label": "laptop"}),
        ("setting.changed", None, {"name": "guard.policy"}),
    ]
    assert all(e.actor_kind == "system" and e.actor is None for e in events)


def test_a_saved_page_is_recorded_in_the_same_transaction_as_the_save(store, db):
    project = store.create_project("ops")
    claims = [Claim(id="cl_1", text="Quill retries.", span_ids=())]
    store.acting_as("token", "laptop-agent").save_page(project.id, "runbook", "Runbook", "", claims)
    event = store.list_audit()[0]
    assert (event.action, event.actor, event.project) == ("note.saved", "laptop-agent", "ops")
    assert event.detail == {"page": "runbook", "version": 1}
    before = dump(db)
    with pytest.raises(StalePage):
        store.save_page(project.id, "runbook", "Runbook", "", claims, expected_version=0)
    assert dump(db) == before  # a refused save records nothing either


@pytest.mark.parametrize("outcome", ["unchanged", "rejected", "refused"])
def test_a_note_outcome_is_recorded_into_its_project(store, outcome):
    project = store.create_project("ops")
    detail = {"rules": ["jwt"]} if outcome == "refused" else {"page": "runbook"}
    store.acting_as("key", "laptop").record_note(project.id, outcome, detail)
    event = store.list_audit()[0]
    assert (event.action, event.actor, event.project, event.detail) == (
        f"note.{outcome}",
        "laptop",
        "ops",
        detail,
    )


def test_record_note_refuses_another_outcome_an_unknown_project_and_a_read_only_view(store, db):
    project = store.create_project("ops")
    other = store.create_project("journal")
    before = dump(db)
    with pytest.raises(ValueError):
        store.record_note(project.id, "saved", {})  # a saved note is recorded by save_page
    with pytest.raises(KeyError):
        store.record_note("br_000000000000", "refused", {"rules": ["jwt"]})
    with pytest.raises(KeyError):
        store.narrow(projects=(project.id,)).record_note(other.id, "refused", {"rules": ["jwt"]})
    with pytest.raises(PermissionError, match="read-only"):
        store.narrow(access="read").record_note(project.id, "refused", {"rules": ["jwt"]})
    assert dump(db) == before


def test_agents_connecting_and_disconnecting_are_recorded_in_their_own_workspace(store):
    brook = store.for_account(store.create_account("Brook").id).acting_as("session", "Brook")
    client, grant, pair = connect_agent(store, brook, "brook-agent")
    assert store.revoke_token(pair.refresh_token, client.id) is True  # the agent itself
    _, second, _ = connect_agent(store, brook, "brook-agent")
    assert brook.revoke_grant(second.id) is True  # the person, from the web view
    assert store.list_audit() == []  # nothing lands in the workspace that ran the calls
    events = list(reversed(brook.list_audit()))
    assert [(e.action, e.actor_kind, e.actor) for e in events] == [
        ("agent.approved", "session", "Brook"),  # A1: the approval, as it is given
        ("agent.connected", "token", "brook-agent"),
        ("agent.disconnected", "token", "brook-agent"),
        ("agent.approved", "session", "Brook"),
        ("agent.connected", "token", "brook-agent"),
        ("agent.disconnected", "session", "Brook"),
    ]
    assert events[0].detail == {
        "agent": "brook-agent",
        "client": client.id,
        "redirect_uri": REDIRECT,
    }
    assert events[1].detail == {"grant": grant.id, "client": client.id}
    assert events[2].detail == {"grant": grant.id, "agent": "brook-agent"}
    assert events[5].detail == {"grant": second.id, "agent": "brook-agent"}


def test_an_agent_approved_by_a_person_names_them_and_a_narrow_grant_its_reach(store):
    signed = store.sign_in(
        "sso", "subject-1", "ada@example.test", "Ada", issuer=ISSUER, join="personal"
    )
    ada = store.for_account(signed.account_id, user_id=signed.user.id)
    project = ada.create_project("ops")
    client = store.register_client("reader-agent", [REDIRECT])
    code = ada.create_code(client.id, REDIRECT, CHALLENGE, projects=(project.id,), access="read")
    store.redeem_code(code, client.id)
    event = ada.list_audit()[0]
    assert event.action == "agent.connected"
    assert event.detail["user"] == signed.user.id
    assert (event.detail["projects"], event.detail["access"]) == ([project.id], "read")


def test_a_replayed_code_or_refresh_token_ends_the_grant_and_says_why(store):
    client = store.register_client("agent", [REDIRECT])
    code = store.create_code(client.id, REDIRECT, CHALLENGE)
    store.redeem_code(code, client.id)
    assert store.redeem_code(code, client.id) is None  # replayed: the grant ends
    pair = store.redeem_code(store.create_code(client.id, REDIRECT, CHALLENGE), client.id)
    store.rotate_refresh_token(pair.refresh_token, client.id)
    assert store.rotate_refresh_token(pair.refresh_token, client.id) is None  # replayed
    reasons = [e.detail.get("reason") for e in reversed(store.list_audit())]
    assert actions(store) == [
        "agent.approved",  # A1
        "agent.connected",
        "agent.disconnected",
        "agent.approved",
        "agent.connected",
        "agent.disconnected",
    ]
    assert reasons == [None, None, "code replayed", None, None, "refresh token replayed"]


# Reading the log


def test_the_log_is_newest_first_and_limited(store):
    for n in range(5):
        store.create_project(f"b{n}")
    assert [e.project for e in store.list_audit()] == ["b4", "b3", "b2", "b1", "b0"]
    assert [e.project for e in store.list_audit(limit=2)] == ["b4", "b3"]
    assert store.list_audit(limit=0) == [] and store.list_audit(limit=-1) == []


def test_a_workspace_keeps_its_newest_100000_events():
    assert cairn.store.base.AUDIT_KEEP == 100_000
    assert 1 < cairn.store.base.AUDIT_PRUNE_BATCH < cairn.store.base.AUDIT_KEEP


def test_the_oldest_events_are_pruned_in_batches_in_the_inserts_transaction(store, db, monkeypatch):
    """Anyone who may write can make events (a refused note is one), so the log is bounded:
    beyond AUDIT_KEEP events, an insert also deletes the oldest, a batch at a time, so most
    inserts delete nothing and none deletes more than a batch. Other workspaces keep theirs."""
    monkeypatch.setattr(cairn.store.base, "AUDIT_KEEP", 10)
    monkeypatch.setattr(cairn.store.base, "AUDIT_PRUNE_BATCH", 4)
    other = store.for_account(store.create_account("Brook").id)
    other.create_project("kept")
    ops = store.create_project("ops")
    sizes = []
    for n in range(40):
        store.record_note(ops.id, "unchanged", {"page": f"p{n}"})
        sizes.append(count_events(db, store.account_id))
    assert max(sizes) == 10  # never more than AUDIT_KEEP
    deleted = [before + 1 - after for before, after in itertools.pairwise(sizes)]
    assert max(deleted) <= 4 and deleted.count(0) > len(deleted) / 2
    newest = [e.detail["page"] for e in store.list_audit(1000)]
    assert newest == [f"p{n}" for n in range(39, 39 - len(newest), -1)]  # the newest, in order
    assert [e.action for e in other.list_audit()] == ["project.created"]


def count_events(db: Path, account_id: str) -> int:
    with sqlite3.connect(db) as con:
        (n,) = con.execute(
            "SELECT COUNT(*) FROM audit_events WHERE account_id = ?", (account_id,)
        ).fetchone()
    return n


def test_the_log_is_per_workspace_and_a_narrowed_view_reads_none_of_it(store):
    project = store.create_project("ops")
    brook = store.for_account(store.create_account("Brook").id)
    brook.create_project("private")
    assert [e.project for e in store.list_audit()] == ["ops"]
    assert [e.project for e in brook.list_audit()] == ["private"]
    assert store.narrow(projects=(project.id,)).list_audit() == []
    assert store.narrow(access="read").list_audit() == []


def test_reading_the_log_writes_nothing(store, db):
    store.create_project("ops")
    before = dump(db)
    store.list_audit()
    assert dump(db) == before


def test_deleting_a_workspace_deletes_its_log_and_settings(store, db):
    brook = store.for_account(store.create_account("Brook").id)
    brook.acting_as("key", "brook-laptop").create_project("zebrafinch")
    brook.set_setting("guard.policy", "off")
    store.create_project("ops")
    store.delete_account(brook.account_id)
    after = dump(db)
    assert "zebrafinch" not in after and "brook-laptop" not in after
    assert brook.account_id not in after
    assert [e.project for e in store.list_audit()] == ["ops"]


def test_details_are_stored_as_json_objects(store, db):
    store.create_key("laptop")
    con = sqlite3.connect(db)
    try:
        (detail,) = con.execute("SELECT detail FROM audit_events").fetchone()
    finally:
        con.close()
    assert json.loads(detail) == {"label": "laptop"}


# Sessions remember the key they were opened with


def test_a_session_opened_by_a_key_carries_its_label(store):
    keyed = store.acting_as("key", "laptop").create_session()
    assert store.resolve_session(keyed).label == "laptop"
    assert store.resolve_session(store.create_session()).label is None  # a bare view's
    # only a key's label: a session made by another session names no key
    assert store.resolve_session(store.acting_as("session", "Ada").create_session()).label is None


# Every stored note has an outcome


def test_a_note_whose_compile_raises_is_recorded_as_rejected_and_the_error_kept(store):
    """The note is committed before the compile; when the compile raises, its outcome is
    still recorded (note.rejected, with a fixed code and nothing of the error), and the
    error goes on to the caller."""

    def broken_gate(*args, **kwargs):
        raise RuntimeError("the gate fell over")

    ops = store.create_project("ops")
    with pytest.raises(RuntimeError, match="fell over"):
        Memory(store, broken_gate).note("ops", "# Retries\n\nQuill retries 3 times.\n")
    assert len(store.list_notes(ops.id)) == 1
    event = store.list_audit()[0]
    assert (event.action, event.project, event.detail) == (
        "note.rejected",
        "ops",
        {"violations": ["compile.error"]},
    )


# Who acted, without ambiguity: names repeat, ids do not


def test_two_keys_with_one_label_are_two_actors(store):
    (first, one_id), (second, two_id) = (
        store.identify_key(store.create_key("ci")) for _ in range(2)
    )
    assert one_id != two_id and one_id.startswith("key_") and two_id.startswith("key_")
    for principal, actor_id, name in ((first, one_id, "one"), (second, two_id, "two")):
        store.acting_as("key", principal.label, actor_id).create_project(name)
    one, two = (e for e in store.list_audit() if e.action == "project.created")
    assert (one.actor, two.actor) == ("ci", "ci")
    assert {one.actor_id, two.actor_id} == {one_id, two_id}
    assert store.identify_key(store.create_key("other"))[1] not in (one_id, two_id)


def test_a_key_keeps_its_id_and_a_revoked_one_is_never_reused(store):
    key = store.create_key("ci")
    kept = store.identify_key(key)[1]
    assert store.identify_key(key)[1] == kept
    store.revoke_key("ci")
    assert store.identify_key(store.create_key("ci"))[1] != kept


def test_two_agents_with_one_name_are_two_actors(store):
    view = store.acting_as("session", "Ada")
    _, first, one = connect_agent(store, view, "laptop-agent")
    _, second, two = connect_agent(store, view, "laptop-agent")
    assert store.identify_token(one.access_token)[1] == first.id
    assert store.identify_token(two.access_token)[1] == second.id
    connected = [e for e in reversed(store.list_audit()) if e.action == "agent.connected"]
    assert [(e.actor, e.actor_id) for e in connected] == [
        ("laptop-agent", first.id),
        ("laptop-agent", second.id),
    ]


def test_a_session_names_its_person_or_the_key_it_was_opened_with(store):
    signed = store.sign_in("sso", "s-1", "ada@example.test", "Ada", issuer=ISSUER, join="default")
    assert store.identify_session(signed.token)[1] == signed.user.id
    key, key_id = store.identify_key(store.create_key("laptop"))
    keyed = store.acting_as("key", key.label, key_id).create_session()
    assert store.identify_session(keyed) == (store.resolve_session(keyed), key_id)
    assert store.identify_session(store.create_session())[1] is None


def test_a_bare_view_is_system_with_no_id_and_the_detail_is_unchanged(store):
    assert store.actor_id is None
    store.create_project("ops")
    (event,) = store.list_audit()
    assert (event.actor_kind, event.actor, event.actor_id, event.detail) == (
        "system",
        None,
        None,
        {"kind": "general"},  # project.created's own detail, with no person added
    )


# An arrangement is recorded as itself (T4b)


def test_a_page_saved_as_an_arrangement_is_one_page_arranged_event(store):
    project = store.create_project("alpha")
    claims = [Claim(id="cl_a", text="A.", span_ids=("sp_a",))]
    store.save_page(project.id, "p", "P", "# P\n\nA.\n", claims)
    detail = {"model": "m", "moved": 1, "merged": 0, "from_version": 1}
    page = store.save_page(
        project.id, "p", "P", "# P\n\nA.\n", claims, expected_version=1,
        event="page.arranged", detail=detail,
    )  # fmt: skip
    assert page.version == 2
    assert actions(store)[-2:] == ["note.saved", "page.arranged"]  # oldest first
    event = store.list_audit(limit=1)[0]
    assert event.project == "alpha"
    assert event.detail == {**detail, "page": "p", "version": 2}


ARRANGED = {"model": "m", "moved": 1, "merged": 0, "from_version": 1}
NOT_A_DETAIL = {
    "the store's page": {**ARRANGED, "page": "other"},
    "the store's version": {**ARRANGED, "version": 99},
    "a field missing": {"model": "m", "moved": 1, "merged": 0},
    "another field": {**ARRANGED, "text": "Drift mirrors two buckets."},
    "a count that is text": {**ARRANGED, "moved": "1"},
    "a count that is a bool": {**ARRANGED, "merged": True},
    "a negative count": {**ARRANGED, "moved": -1},
    "a version that is a float": {**ARRANGED, "from_version": 1.0},
    "a model name too long": {**ARRANGED, "model": "m" * 81},
    "a model name with a line break": {**ARRANGED, "model": "m\nInjected: yes"},
    "an empty model name": {**ARRANGED, "model": ""},
    "a model name that is not text": {**ARRANGED, "model": ["m"]},
}


@pytest.mark.parametrize("name", list(NOT_A_DETAIL))
def test_an_arrangement_records_only_its_fixed_fields_and_types(store, name):
    project = store.create_project("alpha")
    claims = [Claim(id="cl_a", text="A.", span_ids=("sp_a",))]
    store.save_page(project.id, "p", "P", "b", claims)
    before = actions(store)
    with pytest.raises(ValueError):
        store.save_page(
            project.id, "p", "P", "b", claims, event="page.arranged", detail=NOT_A_DETAIL[name]
        )
    assert actions(store) == before and store.page_versions(project.id, "p") == [1]


def test_a_compiled_note_records_no_detail_of_its_callers(store):
    project = store.create_project("alpha")
    claims = [Claim(id="cl_a", text="A.", span_ids=("sp_a",))]
    with pytest.raises(ValueError):
        store.save_page(project.id, "p", "P", "b", claims, detail={"model": "m"})
    assert store.get_page(project.id, "p") is None
    store.save_page(project.id, "p", "P", "b", claims, detail={})
    store.save_page(project.id, "p", "P", "b", claims, detail=None)
    assert store.page_versions(project.id, "p") == [1, 2]


def test_a_page_save_records_only_the_page_events_and_a_stale_one_records_nothing(store):
    project = store.create_project("alpha")
    claims = [Claim(id="cl_a", text="A.", span_ids=("sp_a",))]
    before = actions(store)
    with pytest.raises(ValueError, match=r"page\.arranged"):
        store.save_page(project.id, "p", "P", "b", claims, event="key.created")
    with pytest.raises(StalePage):
        store.save_page(
            project.id, "p", "P", "b", claims, expected_version=3, event="page.arranged",
            detail=ARRANGED,
        )  # fmt: skip
    assert actions(store) == before and store.get_page(project.id, "p") is None


# What the note guard would refuse today (T4b)


def test_screen_answers_with_the_policy_in_force_now_and_writes_nothing(store, db):
    from cairn.guard import PatternGuard

    email = "Write to ada@example.org for access."
    assert store.screen(email) == []  # the default policy refuses secrets, not addresses
    store.set_setting("guard.policy", "secrets+personal")
    before = dump(db)
    found = store.screen(email)
    assert found and {finding.kind for finding in found} == {"personal"}
    assert store.screen("Nothing to see here.") == []
    assert store.with_guard(PatternGuard("off")).screen(email) == []  # the view's own guard
    assert store.narrow(projects=(), access="read").screen(email) == found  # it reads no project
    assert dump(db) == before
