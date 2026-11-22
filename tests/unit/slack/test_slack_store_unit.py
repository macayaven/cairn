"""What cairn keeps of a Slack install (cairn.store.slack): schema step 29, its tables and the
methods the set-up, linking and the handlers build on. The inner loop under
tests/acceptance/test_k1_slack.py. Every name, id and secret here is made up."""

import sqlite3
from datetime import timedelta

import pytest

import cairn.store.schema
from cairn.store import SCHEMA_VERSION, Store, reach
from cairn.store.slack import (
    FORGET_AFTER,
    INSTALL_ID,
    OFFER_TTL,
    SEEN_FOR,
    NoSlackKey,
    SlackInbound,
)
from support.slack.slack_store import (
    ISSUER,
    NOW,
    OTHER_TEAM,
    SECRET,
    TEAM,
    TOKEN,
    a_note,
    ready,
    selects,
    workspaces,
)
from support.slack.slack_store import world as world
from support.store.hosted_accounts import v27_database
from support.store.store import rows, statements, user_version

TABLES = ("slack_installs", "slack_people", "slack_link_offers", "slack_events", "slack_notes")


# Schema step 29


def test_step_29_adds_the_five_empty_tables_and_changes_nothing_else(tmp_path):
    db = tmp_path / "cairn.db"
    v27_database(db)
    with sqlite3.connect(db) as con:
        cairn.store.schema._MIGRATIONS[28](con)
        con.execute("PRAGMA user_version = 28")
    before = set(statements(db))
    assert cairn.store.schema._SLACK == 29 <= SCHEMA_VERSION
    with sqlite3.connect(db) as con:  # step 29 alone, whatever steps come after it
        cairn.store.schema._MIGRATIONS[29](con)
        con.execute("PRAGMA user_version = 29")
    assert user_version(db) == 29
    added = [line for line in statements(db) if line not in before]
    assert added and all("slack_" in line for line in added), added
    assert before <= set(statements(db))
    for table in TABLES:
        assert rows(db, f"SELECT COUNT(*) FROM {table}") == [(0,)]


def test_a_fresh_database_has_the_tables_and_one_without_one_does_not_open(tmp_path):
    db = tmp_path / "cairn.db"
    Store(db).close()
    made = {name for (name,) in rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(TABLES) <= made
    con = sqlite3.connect(db)
    with con:
        con.execute("DROP TABLE slack_notes")
    con.close()
    with pytest.raises(cairn.store.UnknownSchema, match="slack_notes"):
        Store(db)


# Installs


def test_an_owner_or_admin_makes_one_pending_install_and_a_member_none(world):
    with pytest.raises(PermissionError):
        world.member_view.make_slack_install()
    made = world.view.make_slack_install()
    assert INSTALL_ID.fullmatch(made.id) and made.state == "pending"
    assert made.account_id == world.account and made.made_by == world.owner
    assert made.command == "/cairn" and not made.holds_secrets
    assert world.view.make_slack_install() == made  # the same, made again
    assert world.view.get_slack_install() == made


def test_ready_seals_both_secrets_bound_to_the_install_and_the_workspace(world):
    install = ready(world)
    assert install.state == "ready" and install.team_id == TEAM and install.holds_secrets
    assert install.team_domain == "fable" and install.bot_user_id == "U0BOT0001"
    sealed = rows(world.db, "SELECT signing_secret, bot_token FROM slack_installs")[0]
    assert SECRET not in "".join(sealed) and TOKEN not in "".join(sealed)
    found = world.store.slack_inbound(install.id)
    assert isinstance(found, SlackInbound) and found.signing_secret == SECRET
    assert SECRET not in repr(found) and TOKEN not in repr(install)
    assert world.view.slack_bot_token(install.id) == TOKEN
    # swapped between the two columns, neither opens
    con = sqlite3.connect(world.db)
    with con:
        con.execute(
            "UPDATE slack_installs SET signing_secret = bot_token, bot_token = signing_secret"
        )
    con.close()
    assert world.store.slack_inbound(install.id).signing_secret is None
    assert world.view.slack_bot_token(install.id) is None


def test_ready_refuses_a_member_blank_values_and_a_removed_install(world):
    made = world.view.make_slack_install()
    with pytest.raises(PermissionError):
        world.member_view.ready_slack_install(
            made.id, signing_secret=SECRET, bot_token=TOKEN, team_id=TEAM,
            team_domain="fable", bot_user_id="U0BOT0001",
        )  # fmt: skip
    with pytest.raises(ValueError):
        world.view.ready_slack_install(
            made.id, signing_secret=" ", bot_token=TOKEN, team_id=TEAM,
            team_domain="fable", bot_user_id="U0BOT0001",
        )  # fmt: skip
    with pytest.raises(KeyError):
        world.view.ready_slack_install(
            "sl_000000000000", signing_secret=SECRET, bot_token=TOKEN, team_id=TEAM,
            team_domain="fable", bot_user_id="U0BOT0001",
        )  # fmt: skip
    assert world.view.get_slack_install().state == "pending"


def test_without_the_key_nothing_is_sealed_or_opened(world):
    made = world.view.make_slack_install()
    world.store.slack_sealer = None
    view = world.store.for_account(world.account, world.owner)
    with pytest.raises(NoSlackKey):
        view.ready_slack_install(
            made.id, signing_secret=SECRET, bot_token=TOKEN, team_id=TEAM,
            team_domain="fable", bot_user_id="U0BOT0001",
        )  # fmt: skip
    assert NoSlackKey.__doc__


def test_on_off_and_on_again(world):
    install = ready(world)
    assert world.store.slack_install_verified(install.id).state == "on"
    assert world.view.switch_slack_install(install.id, on=False).state == "off"
    assert world.view.switch_slack_install(install.id, on=True).state == "on"
    with pytest.raises(PermissionError):
        world.member_view.switch_slack_install(install.id, on=False)
    pending = world.store.for_account(world.account)
    assert pending.get_slack_install().state == "on"


def test_another_signing_secret_pasted_while_on_is_ready_until_slack_proves_it(world):
    """Nothing proves a signing secret but a request Slack signed with it: an install that
    is on and is given another one is ready again; given the one it holds, it stays on."""
    install = ready(world)
    world.store.slack_install_verified(install.id)
    same = ready(world, token=TOKEN + "1")  # a new bot token, the signing secret it holds
    assert same.state == "on" and world.view.slack_bot_token(install.id) == TOKEN + "1"
    other = ready(world, secret=SECRET + "ab")
    assert other.state == "ready"
    assert world.store.slack_inbound(install.id).signing_secret == SECRET + "ab"
    assert world.store.slack_install_verified(install.id).state == "on"


def test_a_pending_install_is_not_switched_on_and_the_challenge_does_not_turn_it_on(world):
    made = world.view.make_slack_install()
    with pytest.raises(ValueError):
        world.view.switch_slack_install(made.id, on=True)
    assert world.store.slack_install_verified(made.id) is None
    assert world.view.get_slack_install().state == "pending"


def test_the_inbound_lookup_is_one_statement(world):
    install = ready(world)
    asked: list[str] = []
    world.store._observe(trace=asked.append)
    try:
        world.store.slack_inbound(install.id)
    finally:
        world.store._observe(trace=None)
    assert len([s for s in asked if s.lstrip().upper().startswith("SELECT")]) == 1
    assert world.store.slack_inbound("sl_000000000000") is None
    assert world.store.slack_inbound("not an id") is None


# Removing it (D10) and revoking the bot


def test_removing_deletes_secrets_links_and_offers_and_makes_its_notes_due_in_10_days(world):
    install = ready(world)
    world.view.link_slack_user(install.id, "U0ADA0001", world.owner)
    world.view.offer_slack_link(install.id, "U0BOB0001")
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    removed = world.view.remove_slack_install(install.id)
    assert removed.state == "removed" and not removed.holds_secrets
    assert rows(world.db, "SELECT signing_secret, bot_token FROM slack_installs") == [(None, None)]
    for table in ("slack_people", "slack_link_offers"):
        assert rows(world.db, f"SELECT COUNT(*) FROM {table}") == [(0,)]
    due = world.view.slack_notes_to_forget()
    assert due.notes == 1 and due.at == NOW + FORGET_AFTER == NOW + timedelta(days=10)
    world.clock.now += timedelta(days=3)
    assert world.view.remove_slack_install(install.id).state == "removed"  # again: no change
    assert world.view.slack_notes_to_forget().at == NOW + FORGET_AFTER
    assert world.store.slack_notes_due() == []
    world.clock.now = NOW + FORGET_AFTER
    assert world.store.slack_notes_due() == [(world.account, note)]


def test_revoking_the_bot_switches_off_and_deletes_the_bot_token_only(world):
    install = ready(world)
    world.view.link_slack_user(install.id, "U0ADA0001", world.owner)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    found = world.store.for_account(world.account).revoke_slack_bot(install.id)
    assert found.state == "off" and not found.holds_secrets and not found.holds_bot_token
    assert found.needs_bot_token
    assert world.store.slack_inbound(install.id).signing_secret == SECRET  # still verified
    assert world.view.slack_bot_token(install.id) is None
    assert world.view.slack_person(install.id, "U0ADA0001") is not None
    assert world.view.slack_notes_to_forget() is None
    with pytest.raises(ValueError):  # nothing to switch on with
        world.view.switch_slack_install(install.id, on=True)
    again = ready(world, token=TOKEN + "new")  # a new bot token puts it back
    assert again.state == "ready" and again.holds_bot_token and not again.needs_bot_token


def test_an_install_switched_off_keeps_its_token_and_is_told_from_a_revoked_one(world):
    install = ready(world)
    off = world.view.switch_slack_install(install.id, on=False)
    assert off.state == "off" and off.holds_secrets and off.holds_bot_token
    assert not off.needs_bot_token
    assert world.view.switch_slack_install(install.id, on=True).state == "on"
    assert not world.view.get_slack_install().needs_bot_token
    removed = world.view.remove_slack_install(install.id)
    assert not removed.holds_bot_token and not removed.needs_bot_token  # set up again instead
    assert world.view.revoke_slack_bot(install.id).state == "removed"  # changes nothing


def test_set_up_again_reuses_the_id_and_the_same_team_cancels_the_forgetting(world):
    install = ready(world)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    world.view.remove_slack_install(install.id)
    again = world.view.make_slack_install()
    assert again.id == install.id and again.state == "pending"
    assert world.view.slack_notes_to_forget().notes == 1  # pending cancels nothing
    ready(world)
    assert world.view.slack_notes_to_forget() is None


def test_another_team_made_ready_leaves_the_old_notes_due(world):
    install = ready(world)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    world.view.remove_slack_install(install.id)
    ready(world, team=OTHER_TEAM)
    assert world.view.slack_notes_to_forget().notes == 1
    later = a_note(world, "# Kettles\n\nThe kettle sings.\n")
    world.view.record_slack_note(install.id, later, how="typed", brought_by=world.owner)
    world.clock.now += FORGET_AFTER
    assert world.store.slack_notes_due() == [(world.account, note)]


def test_forget_them_now_forgets_every_due_note_of_the_install(world):
    install = ready(world)
    notes = [a_note(world, f"# N{n}\n\nFact {n}.\n") for n in range(3)]
    for note in notes:
        world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    with pytest.raises(ValueError):  # not removed: nothing is due
        world.view.forget_slack_notes_now(install.id)
    world.view.remove_slack_install(install.id)
    with pytest.raises(PermissionError):
        world.member_view.forget_slack_notes_now(install.id)
    assert world.view.forget_slack_notes_now(install.id) == 3
    assert [world.view.get_note(n) for n in notes] == [None] * 3
    assert world.view.slack_notes_to_forget() is None


# Links, offers, projects, notes


def test_link_unlink_and_a_project(world):
    install = ready(world)
    linked = world.view.link_slack_user(install.id, "U0BOB0001", world.member)
    assert linked.user_id == world.member and linked.project_id is None
    with pytest.raises(KeyError):  # not a member of this workspace
        world.view.link_slack_user(install.id, "U0BOB0001", "usr_000000000000")
    world.view.set_slack_project(install.id, "U0BOB0001", world.project.id)
    assert world.view.slack_person(install.id, "U0BOB0001").project_id == world.project.id
    with pytest.raises(KeyError):
        world.view.set_slack_project(install.id, "U0BOB0001", "br_000000000000")
    world.view.set_slack_project(install.id, "U0BOB0001", None)
    assert world.view.slack_person(install.id, "U0BOB0001").project_id is None
    assert world.view.unlink_slack_user(install.id, "U0BOB0001") is True
    assert world.view.unlink_slack_user(install.id, "U0BOB0001") is False
    assert world.view.slack_person(install.id, "U0BOB0001") is None


def test_an_offer_is_a_digest_used_once_by_a_person_of_the_workspace(world, tmp_path):
    install = ready(world)
    value = world.view.offer_slack_link(install.id, "U0BOB0001")
    assert value not in "\n".join(statements(world.db))
    offer = world.member_view.slack_offer(value)
    assert offer.slack_user_id == "U0BOB0001" and offer.install_id == install.id
    assert offer.expires_at == NOW + OFFER_TTL == NOW + timedelta(minutes=10)
    stranger = world.store.sign_in(
        "sso", "sub-cy", "cy@example.test", "Cy", issuer=ISSUER, join="personal"
    )
    theirs = world.store.for_account(stranger.account_id, stranger.user.id)
    assert theirs.slack_offer(value) is None  # another workspace's person sees nothing
    with pytest.raises(KeyError):
        theirs.claim_slack_offer(value)
    with pytest.raises(PermissionError):  # a view acting for no person says yes for nobody
        world.store.for_account(world.account).claim_slack_offer(value)
    code = world.member_view.claim_slack_offer(value)
    taken = world.store.for_account(world.account).take_slack_offer(install.id, "U0BOB0001", code)
    assert taken.user_id == world.member
    with pytest.raises(KeyError):
        world.member_view.claim_slack_offer(value)


def test_a_note_is_recorded_once_per_message_and_found_by_it(world):
    install = ready(world)
    first, second = a_note(world), a_note(world, "# Kettles\n\nThe kettle sings.\n")
    kept = world.view.record_slack_note(
        install.id, first, how="filed", brought_by=world.owner,
        conversation_id="C0LIB0001", message_time="1900000000.000100",
    )  # fmt: skip
    assert kept == first
    again = world.view.record_slack_note(
        install.id, second, how="filed", brought_by=world.owner,
        conversation_id="C0LIB0001", message_time="1900000000.000100",
    )  # fmt: skip
    assert again == first  # the note the message already is
    assert world.view.slack_note_for(install.id, "C0LIB0001", "1900000000.000100") == first
    with pytest.raises(ValueError):
        world.view.record_slack_note(install.id, second, how="filed", brought_by=world.owner)
    with pytest.raises(ValueError):
        world.view.record_slack_note(install.id, second, how="carried", brought_by=world.owner)
    with pytest.raises(KeyError):
        world.view.record_slack_note(install.id, "cap_000000000000", how="typed",
                                     brought_by=world.owner)  # fmt: skip


def test_events_are_seen_once_and_swept_after_a_day(world):
    install = ready(world)
    assert world.store.slack_event_seen(install.id, "Ev01") is True
    assert world.store.slack_event_seen(install.id, "Ev01") is False
    world.clock.now += SEEN_FOR
    assert world.store.sweep_slack() == 1
    assert world.store.slack_event_seen(install.id, "Ev01") is True


def test_a_forgotten_event_id_is_seen_again_as_the_first_time(world):
    install = ready(world)
    assert world.store.slack_event_seen(install.id, "Ev01") is True
    world.store.forget_slack_event(install.id, "Ev01")
    assert world.store.slack_event_seen(install.id, "Ev01") is True
    assert world.store.slack_event_seen(install.id, "Ev01") is False
    world.store.forget_slack_event(install.id, "Ev0NEVER")  # one never seen: nothing
    world.store.forget_slack_event("sl_ffffffffffff", "Ev01")  # another install's: nothing
    assert world.store.slack_event_seen(install.id, "Ev01") is False


def test_forgetting_an_event_id_is_the_servers_alone(world):
    install = ready(world)
    world.store.slack_event_seen(install.id, "Ev01")
    with pytest.raises(PermissionError):
        world.view.forget_slack_event(install.id, "Ev01")
    assert world.store.slack_event_seen(install.id, "Ev01") is False


def test_the_sweep_takes_offers_past_their_time_and_is_part_of_sweep_expired(world):
    install = ready(world)
    world.view.offer_slack_link(install.id, "U0BOB0001")
    world.store.slack_event_seen(install.id, "Ev01")
    world.clock.now += timedelta(minutes=11)
    assert world.store.sweep_slack() == 1  # the offer; the event is younger than a day
    world.clock.now += SEEN_FOR
    assert world.store.sweep_expired() >= 1
    assert rows(world.db, "SELECT COUNT(*) FROM slack_events") == [(0,)]


# What the set-up, the Sources page and the handlers read


def test_a_persons_own_links_are_theirs_alone(world):
    install = ready(world)
    world.view.link_slack_user(install.id, "U0BOB0001", world.member)
    world.view.link_slack_user(install.id, "U0BOB0002", world.member)
    world.view.link_slack_user(install.id, "U0ADA0001", world.owner)
    mine = world.member_view.own_slack_links()
    assert [(p.install_id, p.slack_user_id, p.user_id) for p in mine] == [
        (install.id, "U0BOB0001", world.member),
        (install.id, "U0BOB0002", world.member),
    ]
    assert [p.slack_user_id for p in world.view.own_slack_links()] == ["U0ADA0001"]
    assert world.store.for_account(world.account).own_slack_links() == []  # no person
    world.view.remove_slack_install(install.id)
    assert world.member_view.own_slack_links() == []


def test_a_persons_own_links_are_one_read_whatever_the_number_of_workspaces(world):
    install = ready(world)
    world.view.link_slack_user(install.id, "U0BOB0001", world.member)
    workspaces(world, 1)
    few = selects(world.store, world.member_view.own_slack_links)
    workspaces(world, 6)
    assert few == selects(world.store, world.member_view.own_slack_links) == 1


def test_whether_another_workspace_holds_a_team_is_one_read(world):
    mine = ready(world)
    others = workspaces(world, 1)
    other = others[0]
    held = world.store.slack_team_held
    assert held(other.team_id, world.account) is True
    assert held(TEAM, world.account) is False  # its own install is not another's
    assert held(TEAM, other.account_id) is True
    assert held("T0NOBODY1", world.account) is False
    world.store.for_account(other.account_id).remove_slack_install(other.id)
    assert held(other.team_id, world.account) is False  # a removed install holds nothing
    assert mine.team_id == TEAM
    few = selects(world.store, lambda: held(TEAM, other.account_id))
    workspaces(world, 6)
    assert few == selects(world.store, lambda: held(TEAM, other.account_id)) == 1


def test_the_projects_a_person_may_write_to(world):
    store = world.view
    reading = store.create_project("archive")
    store.set_project_access(reading.id, everyone="read")
    hidden = store.create_project("diary")
    store.set_project_access(hidden.id, everyone="none")
    assert [p.name for p in world.view.writable_projects()] == ["archive", "diary", "library"]
    assert [p.name for p in world.member_view.writable_projects()] == ["library"]
    assert world.member_view.narrow(access="read").writable_projects() == []
    narrowed = world.store.for_account(world.account).narrow(projects=(reading.id,))
    assert [p.name for p in narrowed.writable_projects()] == ["archive"]


def test_the_projects_a_person_may_write_to_cost_the_same_with_2_or_8_workspaces(world):
    for name in ("archive", "diary"):
        world.view.create_project(name)
    workspaces(world, 1)
    few = selects(world.store, world.member_view.writable_projects)
    workspaces(world, 6)
    for name in ("ledger", "maps", "notes"):
        world.view.create_project(name)
    assert selects(world.store, world.member_view.writable_projects) == few


# Classification


def test_every_slack_method_is_classified_and_a_narrowed_view_manages_none(world):
    names = [
        name
        for name in dir(Store)
        if "slack" in name and not name.startswith("_") and callable(getattr(Store, name))
    ]
    assert names
    for name in names:
        assert name in reach.MANAGEMENT, name
    narrowed = world.store.for_account(world.account).narrow(access="read")
    for name in names:
        with pytest.raises(PermissionError):
            getattr(narrowed, name)("anything")


def test_the_cross_workspace_ones_refuse_a_view_acting_for_a_person(world):
    install = ready(world)
    for call in (
        lambda v: v.slack_inbound(install.id),
        lambda v: v.slack_event_seen(install.id, "Ev01"),
        lambda v: v.slack_notes_due(),
        lambda v: v.sweep_slack(),
        lambda v: v.slack_install_verified(install.id),
        lambda v: v.slack_team_held(TEAM, world.account),
    ):
        with pytest.raises(PermissionError):
            call(world.view)
