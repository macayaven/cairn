"""A Google account connected in the browser (step two of the sources contract): its custody.

Schema step 28 adds `credential_tokens`: per connected account its workspace, credential name,
kind, owner, address, granted scopes, the refresh token sealed with AES-GCM, and when it was
added and last replaced. The settings row of step one stays the registry. Every name, address
and token here is made up; every clock is in 2099.
"""

import base64
import io
import json
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_open_checkouts_unit import v26_database
from test_store_unit import before_step_30, rows, statements, user_version

import cairn.store.schema
from cairn.sources import holds_secret
from cairn.store import SCHEMA_VERSION, SchemaTooNew, Store, UnknownSchema
from cairn.store.hosted import (
    GONE,
    HOSTED_ENV,
    NOT_HERE,
    RECONNECT,
    UNOPENED,
    GoogleCustody,
    Sealer,
    TokenUnusable,
    associated,
)
from cairn.store.sources import HOSTED_DISCONNECT, HOSTED_LEFT

ISSUER = "https://id.example.test/pool-g2"
NOW = datetime(2099, 11, 17, 9, tzinfo=UTC)
KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
TOKEN = "1//g2-refresh-token-ada-0123456789abcdef"
SECRET = "g2-client-secret-0123456789"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
COLUMNS = [
    "account_id",
    "credential",
    "kind",
    "owner",
    "address",
    "subject",
    "scopes",
    "sealed",
    "added_at",
    "refreshed_at",
    "ended_at",
]


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def custody(key: bytes = KEY) -> GoogleCustody:
    return GoogleCustody(Sealer(key), client_id="g2-client.apps.example.test", client_secret=SECRET)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def world(tmp_path):
    db = tmp_path / "cairn.db"
    clock = Clock()
    store = Store(db, clock=clock)
    store.google_custody = custody()
    people = {}
    for name, role in (("Olive", "owner"), ("Ada", "admin"), ("Brook", None)):
        signed = store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
        if role:
            store.set_role(signed.user.id, role)
        people[name] = SimpleNamespace(
            id=signed.user.id, view=store.for_account(signed.account_id, signed.user.id)
        )
    store.create_project("ledger")
    yield SimpleNamespace(store=store, db=db, clock=clock, **people)
    store.close()


# The schema step


def v27_database(db) -> dict:
    v26_database(db)
    with sqlite3.connect(db) as con:
        cairn.store.schema._MIGRATIONS[cairn.store.schema._CHECKOUTS](con)
        con.execute(f"PRAGMA user_version = {cairn.store.schema._CHECKOUTS}")
    assert user_version(db) == 27
    return {"statements": statements(db)}


def test_step_28_is_one_step_a_database_at_28_has_both_tables_or_does_not_open(
    tmp_path, monkeypatch
):
    assert "google_revocations" in cairn.store.schema._add_credential_tokens.__doc__
    assert "one step" in cairn.store.schema._add_credential_tokens.__doc__.lower()
    fresh = tmp_path / "fresh.db"
    Store(fresh).close()  # made at 28 at once
    made = {name for (name,) in rows(fresh, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"credential_tokens", "google_revocations"} <= made
    halfway = tmp_path / "halfway.db"
    v27_database(halfway)
    monkeypatch.setattr(cairn.store.schema, "_V28_GOOGLE_REVOCATIONS", "CREATE TABLE (")
    with pytest.raises(sqlite3.Error):  # the step's second table fails: nothing of it stays
        Store(halfway)
    monkeypatch.undo()
    assert user_version(halfway) == 27
    assert rows(halfway, "SELECT name FROM sqlite_master WHERE name = 'credential_tokens'") == []
    con = sqlite3.connect(fresh)
    with con:
        con.execute("DROP TABLE google_revocations")
    con.close()
    with pytest.raises(UnknownSchema):  # a 28 without it is refused before anything is read
        Store(fresh)


def test_a_database_at_28_without_a_column_the_code_needs_does_not_open(tmp_path):
    db = tmp_path / "cairn.db"
    Store(db).close()
    con = sqlite3.connect(db)
    with con:
        con.execute("ALTER TABLE credential_tokens DROP COLUMN subject")
    con.close()
    before = dump(db)
    with pytest.raises(UnknownSchema, match=r"credential_tokens.*subject"):
        Store(db)
    assert dump(db) == before


def test_step_28_adds_the_empty_token_table_and_changes_nothing_else(tmp_path):
    db = tmp_path / "cairn.db"
    v27 = v27_database(db)
    assert cairn.store.schema._CREDENTIAL_TOKENS == 28 and SCHEMA_VERSION >= 28
    Store(db).close()
    assert user_version(db) == SCHEMA_VERSION
    assert [row[1] for row in rows(db, "PRAGMA table_info(credential_tokens)")] == COLUMNS
    assert rows(db, "SELECT COUNT(*) FROM credential_tokens") == [(0,)]
    waiting = [row[1] for row in rows(db, "PRAGMA table_info(google_revocations)")]
    assert waiting[:4] == ["id", "subject", "address", "sealed"]  # no workspace, no person
    assert rows(db, "SELECT COUNT(*) FROM google_revocations") == [(0,)]
    # Steps 29 and 30 run too: Slack's tables, and each session's last use (step 30 rebuilds
    # sessions with `used_at` last, NULL on every session there was).
    rebuilt = cairn.store.schema._V30_SESSIONS + ";"
    after = [line for line in before_step_30(statements(db)) if line != rebuilt]
    before = {line for line in v27["statements"] if not line.startswith("CREATE TABLE sessions")}
    added = [line for line in after if line not in before]
    assert added and all(
        "credential_tokens" in line or "google_revocations" in line or "slack_" in line
        for line in added
    ), added
    assert before <= set(after)
    assert rebuilt in statements(db)


def test_a_database_migrated_from_version_27_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v27_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)


def test_a_failed_step_28_leaves_the_version_27_database_as_it_was(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    v27 = v27_database(db)
    version = cairn.store.schema._CREDENTIAL_TOKENS
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v27["statements"] and user_version(db) == 27


def test_a_database_of_a_newer_schema_is_refused_and_left_as_it_was(tmp_path):
    db = tmp_path / "cairn.db"
    Store(db).close()
    with sqlite3.connect(db) as con:
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        # a step this cairn may not run on: the floor goes up with it
        con.execute("UPDATE oldest_build SET version = ?", (SCHEMA_VERSION + 1,))
    before = dump(db)
    with pytest.raises(SchemaTooNew):
        Store(db)
    assert dump(db) == before


# Sealing


def test_a_sealed_value_opens_with_its_key_and_its_associated_data_only():
    sealer = Sealer(KEY)
    tied = associated("acct_1", "gmail.ada.0123456789", "user_1")
    sealed = sealer.seal(TOKEN, tied)
    assert sealer.open(sealed, tied) == TOKEN
    assert TOKEN not in sealed and not holds_secret(sealed, (TOKEN,))
    assert Sealer(OTHER_KEY).open(sealed, tied) is None
    for other in (
        associated("acct_2", "gmail.ada.0123456789", "user_1"),
        associated("acct_1", "gmail.bob.0123456789", "user_1"),
        associated("acct_1", "gmail.ada.0123456789", "user_2"),
    ):
        assert sealer.open(sealed, other) is None
    assert sealer.open("not-a-sealed-value", tied) is None
    changed = bytearray(base64.urlsafe_b64decode(sealed))
    changed[-1] ^= 1  # Change the tag itself, not only padding bits in its Base64 spelling.
    assert sealer.open(base64.urlsafe_b64encode(changed).decode("ascii"), tied) is None


def test_each_seal_takes_a_fresh_nonce():
    sealer = Sealer(KEY)
    tied = associated("acct_1", "x", None)
    assert sealer.seal(TOKEN, tied) != sealer.seal(TOKEN, tied)


def test_the_associated_data_cannot_be_shifted_between_its_parts():
    assert associated("a", "b.c", "d") != associated("a.b", "c", "d")
    assert associated("acct", "Gmail.Ada", "u") == associated("acct", "gmail.ada", "u")


@pytest.mark.parametrize("key", [b"", bytes(16), bytes(31), bytes(33)])
def test_a_key_that_is_not_32_bytes_is_refused(key):
    with pytest.raises(ValueError, match="32 bytes"):
        Sealer(key)


def test_no_repr_shows_the_key_or_the_secret():
    shown = repr(custody()) + repr(Sealer(KEY))
    assert SECRET not in shown and KEY.hex() not in shown
    assert base64.b64encode(KEY).decode() not in shown


# The store: connecting, the row, and what opens


def connect(
    world, kind="gmail", address="ada.lovelace@gmail.example.test", token=TOKEN, subject=None
):
    return world.Ada.view.connect_google(
        kind, address, [GMAIL_SCOPE, "openid", "email"], token,
        subject=subject or sub(address),
    )  # fmt: skip


def sub(address: str) -> str:
    """Google's id of the account an address names, in these tests."""
    return "1000" + str(sum(map(ord, address.casefold())))


def test_connecting_writes_the_registry_row_and_one_sealed_row_and_nothing_else(world):
    before = {t: rows(world.db, f"SELECT * FROM {t}") for t in ("notes", "sources", "source_items")}
    connected = connect(world)
    assert connected.reconnected is False
    name = connected.name
    assert name.startswith("gmail.adalovelace.") and len(name) <= 64
    after = {t: rows(world.db, f"SELECT * FROM {t}") for t in ("notes", "sources", "source_items")}
    assert after == before
    (row,) = rows(world.db, "SELECT * FROM credential_tokens")
    _, credential, kind, owner, address, subject, scopes, _, added, refreshed, ended = row
    assert ended is None
    assert (credential, kind, owner) == (name, "gmail", world.Ada.id)
    assert address == "ada.lovelace@gmail.example.test"
    assert subject == sub(address)
    assert scopes == f"{GMAIL_SCOPE} openid email"
    assert added == refreshed == NOW.isoformat(timespec="microseconds")
    assert not holds_secret(dump(world.db), (TOKEN,))
    (info,) = world.Ada.view.list_credentials()
    assert (info.name, info.kind, info.owner, info.sources) == (name, "gmail", world.Ada.id, 0)
    (registry,) = rows(world.db, "SELECT value FROM settings WHERE name = ?", f"credential:{name}")
    held = json.loads(registry[0])
    assert held["hosted"] is True and held["settings"]["credentials_env"] == HOSTED_ENV
    assert "subject" not in held["settings"]
    assert world.store.google_token(name) == TOKEN
    events = [e.action for e in world.store.list_audit(10)]
    assert "source.credential_added" in events
    assert TOKEN not in json.dumps([e.model_dump() for e in world.store.list_audit(10)])


def test_the_name_comes_from_kind_owner_and_address_and_cannot_inject(world):
    one = connect(world, address="ADA.Lovelace@Gmail.Example.Test").name
    assert one == connect(world, address="ada.lovelace@gmail.example.test").name  # one account
    other_kind = connect(world, kind="google_drive").name
    assert other_kind != one and other_kind.startswith("google_drive.")
    odd = connect(world, address="<script>';DROP--\"@example.test").name
    assert all(c.isalnum() or c in "._-" for c in odd)
    olive = world.Olive.view.connect_google(
        "gmail",
        "ada.lovelace@gmail.example.test",
        [GMAIL_SCOPE],
        TOKEN,
        subject=sub("ada.lovelace@gmail.example.test"),
    ).name
    assert olive != one  # another person's connection of the same address is another account


def test_a_sealed_token_copied_to_another_row_does_not_open(world):
    first = connect(world).name
    second = connect(world, kind="google_drive").name
    con = sqlite3.connect(world.db)
    with con:
        (sealed,) = con.execute(
            "SELECT sealed FROM credential_tokens WHERE credential = ?", (first,)
        ).fetchone()
        moved = "UPDATE credential_tokens SET sealed = ? WHERE credential = ?"
        con.execute(moved, (sealed, second))
    con.close()
    assert world.store.google_token(first) == TOKEN
    with pytest.raises(TokenUnusable) as refused:
        world.store.google_token(second)
    assert str(refused.value) == UNOPENED


def test_a_token_does_not_open_under_another_key(world):
    name = connect(world).name
    world.store.google_custody = custody(OTHER_KEY)
    with pytest.raises(TokenUnusable) as refused:
        world.store.google_token(name)
    assert str(refused.value) == UNOPENED
    assert "sealing key" in UNOPENED and "reconnect" in UNOPENED


def test_three_failures_have_three_sentences():
    assert len({RECONNECT, UNOPENED, GONE}) == 3
    assert "Google" in RECONNECT and "sealing key" in UNOPENED and "no longer connected" in GONE


def test_an_account_whose_row_is_gone_says_so(world):
    name = connect(world).name
    con = sqlite3.connect(world.db)
    with con:
        con.execute("DELETE FROM credential_tokens WHERE credential = ?", (name,))
    con.close()
    with pytest.raises(TokenUnusable) as refused:
        world.store.google_token(name)
    assert str(refused.value) == GONE


def test_without_the_key_nothing_is_read_or_written(world):
    world.store.google_custody = None
    view = world.store.for_account(world.Ada.view.account_id, world.Ada.id)
    before = dump(world.db)
    with pytest.raises(TokenUnusable) as refused:
        view.connect_google(
            "gmail",
            "ada@gmail.example.test",
            [GMAIL_SCOPE],
            TOKEN,
            subject=sub("ada@gmail.example.test"),
        )
    assert str(refused.value) == NOT_HERE
    assert dump(world.db) == before


def test_a_member_connects_their_own_and_sees_no_one_elses(world):
    connect(world)  # Ada's
    brook = world.Brook.view
    mine = brook.connect_google(
        "gmail",
        "brook@gmail.example.test",
        [GMAIL_SCOPE],
        TOKEN,
        subject=sub("brook@gmail.example.test"),
    )
    assert [one.name for one in brook.google_accounts_to_manage()] == [mine.name]
    assert [one.name for one in brook.list_credentials()] == [mine.name]
    with pytest.raises(KeyError):  # another person's: as if it did not exist
        brook.disconnect_google(connect(world, "google_drive").name)
    dropped = world.Olive.view.disconnect_google(mine.name)  # an owner disconnects it
    assert dropped.name == mine.name


def test_a_preview_reads_only_the_persons_own_account(world):
    adas = connect(world).name
    assert json.loads(world.Ada.view.hosted_document("", account=adas))["refresh_token"] == TOKEN
    for other in (world.Brook.view, world.Olive.view):  # a member's or an owner's view
        with pytest.raises(TokenUnusable) as refused:
            other.hosted_document("", account=adas)
        assert str(refused.value) == GONE


def test_the_cap_per_person_is_said_of_their_own_accounts(world, monkeypatch):
    from cairn.store.hosted import FULL, MINE_FULL

    monkeypatch.setattr(cairn.store.hosted, "CONNECTED_PER_PERSON", 1)
    brook = world.Brook.view
    brook.connect_google(
        "gmail",
        "brook@gmail.example.test",
        [GMAIL_SCOPE],
        TOKEN,
        subject=sub("brook@gmail.example.test"),
    )
    assert brook.google_room() == "mine_full"
    with pytest.raises(ValueError) as refused:
        brook.connect_google(
            "google_drive",
            "brook@gmail.example.test",
            [GMAIL_SCOPE],
            TOKEN,
            subject=sub("brook@gmail.example.test"),
        )
    assert str(refused.value) == MINE_FULL
    assert world.Ada.view.google_room() is None
    monkeypatch.setattr(cairn.store.hosted, "CREDENTIALS_MAX", 1)
    assert world.Ada.view.google_room() == "full"
    assert FULL == "no more accounts can be connected here now"


def test_another_kind_is_refused(world):
    with pytest.raises(ValueError):
        world.Ada.view.connect_google(
            "github", "ada@example.test", ["repo"], TOKEN, subject=sub("ada@example.test")
        )


def test_a_person_sees_their_own_connected_accounts_only(world):
    mine = connect(world).name
    world.Olive.view.connect_google(
        "gmail",
        "olive@gmail.example.test",
        [GMAIL_SCOPE],
        TOKEN,
        subject=sub("olive@gmail.example.test"),
    )
    (shown,) = world.Ada.view.google_accounts()
    assert (shown.name, shown.kind) == (mine, "gmail")
    assert shown.address == "ada.lovelace@gmail.example.test"
    assert TOKEN not in repr(shown)
    with pytest.raises(KeyError):
        world.Olive.view.google_token(mine)  # another person's token is never handed out


# Reconnect, disconnect, removal, deletion, export


def test_reconnecting_as_the_same_account_replaces_the_token_and_keeps_the_rest(world):
    name = connect(world).name
    later = datetime(2099, 11, 18, tzinfo=UTC)
    world.clock.now = later
    again = world.Ada.view.connect_google(
        "gmail", "ada.lovelace@gmail.example.test", [GMAIL_SCOPE], "1//g2-new-token-0123456789",
        expect=name, subject=sub("ada.lovelace@gmail.example.test"),
    )  # fmt: skip
    assert (again.name, again.reconnected) == (name, True)
    assert world.store.google_token(name) == "1//g2-new-token-0123456789"
    ((added, refreshed),) = rows(world.db, "SELECT added_at, refreshed_at FROM credential_tokens")
    assert added == NOW.isoformat(timespec="microseconds")
    assert refreshed == later.isoformat(timespec="microseconds")


def test_a_google_account_is_known_by_googles_id_not_by_its_address(world):
    first = connect(world, address="ada@gmail.example.test", subject="1000ada")
    assert world.store.google_account_held("1000ada")
    assert not world.store.google_account_held("1000someone")
    # its primary address changed at Google: the same account, reconnected, the name kept
    again = connect(world, address="ada.l@example.test", subject="1000ada", token="1//g2-b-012345")
    assert (again.name, again.reconnected) == (first.name, True)
    (account,) = world.Ada.view.google_accounts()
    assert account.address == "ada.l@example.test"  # the address is what people read
    assert world.store.google_token(first.name) == "1//g2-b-012345"
    # the same address, another Google account (the address passed on): not a reconnect
    with pytest.raises(PermissionError, match="another"):
        world.Ada.view.connect_google(
            "gmail", "ada.l@example.test", [GMAIL_SCOPE], TOKEN, subject="1000other",
            expect=first.name,
        )  # fmt: skip
    with pytest.raises(ValueError):  # Google named no account id
        world.Ada.view.connect_google(
            "google_drive", "ada.l@example.test", [GMAIL_SCOPE], TOKEN, subject=""
        )


def test_reconnecting_as_another_google_account_is_refused_and_changes_nothing(world):
    name = connect(world).name
    before = dump(world.db)
    with pytest.raises(PermissionError, match="another"):
        world.Ada.view.connect_google(
            "gmail", "someone.else@gmail.example.test", [GMAIL_SCOPE], "1//g2-other-0123456789",
            expect=name, subject=sub("someone.else@gmail.example.test"),
        )  # fmt: skip
    assert dump(world.db) == before


def test_the_generic_disconnect_refuses_a_connected_google_account(world):
    name = connect(world).name
    before = dump(world.db)
    with pytest.raises(PermissionError) as refused:
        world.Ada.view.remove_credential(name)
    assert str(refused.value) == HOSTED_DISCONNECT
    assert dump(world.db) == before


def test_with_the_switch_off_the_generic_disconnect_deletes_it_and_its_row(world):
    """The Disconnect under Google is gone with the switch: the person (or the command line)
    can still remove the account; nothing can take the access away at Google from here."""
    name = connect(world).name
    world.store.google_custody = None
    view = world.store.for_account(world.Ada.view.account_id, world.Ada.id)
    assert view.remove_credential(name) == []
    assert rows(world.db, "SELECT COUNT(*) FROM credential_tokens") == [(0,)]
    assert view.list_credentials() == []
    assert "cairn" not in HOSTED_DISCONNECT.lower()  # B3: the product's name is not in it


def test_with_the_switch_off_the_command_line_says_googles_permission_stays(world, capsys):
    from cairn.web.cli import main

    name = connect(world).name
    assert world.Ada.view.credential_hosted(name) is True
    assert world.Ada.view.credential_hosted("no-such-account") is False
    code = main(["credential-remove", "--db", str(world.db), "--name", name])
    out = capsys.readouterr().out
    assert code == 0 and HOSTED_LEFT in out and "third-party apps" in out
    assert rows(world.db, "SELECT COUNT(*) FROM credential_tokens") == [(0,)]


def test_a_dropped_accounts_token_waits_sealed_until_its_revocation_is_settled(world):
    name = connect(world).name
    dropped = world.Ada.view.disconnect_google(name)
    assert dropped.pending is not None
    assert rows(world.db, "SELECT COUNT(*) FROM credential_tokens") == [(0,)]
    ((waiting, subject, address, sealed),) = rows(
        world.db, "SELECT id, subject, address, sealed FROM google_revocations"
    )
    assert waiting == dropped.pending and address == "ada.lovelace@gmail.example.test"
    assert subject == dropped.subject == sub(address)
    assert not holds_secret(dump(world.db), (TOKEN,)) and TOKEN not in sealed
    world.store.release_google_revocation(waiting, dropped.claim)  # its send did not settle it
    assert world.store.pending_google_revocations() == [(waiting, subject, TOKEN)]
    other = Store(world.db)  # another key: it waits, unopened
    other.google_custody = custody(bytes(32))
    assert other.pending_google_revocations() == [(waiting, subject, None)]
    other.close()
    claim = world.store.claim_google_revocation(waiting)
    world.store.settle_google_revocation(waiting, claim)
    assert rows(world.db, "SELECT COUNT(*) FROM google_revocations") == [(0,)]


def test_a_refused_revocation_backs_off_and_stops_after_its_tries(world):
    hosted = cairn.store.hosted
    assert hosted.REVOKE_TRIES == 20 and hosted.BACKOFF_MAX == 86_400
    dropped = world.Ada.view.disconnect_google(connect(world).name)
    revocation, claim, now = dropped.pending, dropped.claim, NOW
    for tries in range(1, hosted.REVOKE_TRIES + 1):
        changed, stopped = world.store.release_google_revocation(revocation, claim, "refused")
        assert changed == (tries == 1)  # said once per change of state, not each try
        assert stopped == (tries == hosted.REVOKE_TRIES)
        assert world.store.pending_google_revocations() == []  # not due yet
        wait = min(60 * 2**tries, hosted.BACKOFF_MAX)
        world.clock.now = now + timedelta(seconds=wait - 1)
        assert world.store.pending_google_revocations() == []
        now = world.clock.now = now + timedelta(seconds=wait)
        if stopped:
            break
        assert [one[0] for one in world.store.pending_google_revocations()] == [revocation]
        claim = world.store.claim_google_revocation(revocation)
    world.clock.now = now + timedelta(days=365)  # stopped: kept, never sent again
    assert world.store.pending_google_revocations() == []
    assert rows(world.db, "SELECT COUNT(*) FROM google_revocations") == [(1,)]


def test_an_unanswered_revocation_is_tried_each_pass_and_said_once(world):
    dropped = world.Ada.view.disconnect_google(connect(world).name)
    revocation, claim = dropped.pending, dropped.claim
    said = []
    for left in ("unreachable", "unreachable", "kept", "kept", "unreachable"):
        said.append(world.store.release_google_revocation(revocation, claim, left)[0])
        assert [one[0] for one in world.store.pending_google_revocations()] == [revocation]
        claim = world.store.claim_google_revocation(revocation)
    assert said == [True, False, True, False, True]


def test_a_waiting_row_is_sent_only_by_whoever_claimed_it(world):
    name = connect(world).name
    dropped = world.Ada.view.disconnect_google(name)
    assert dropped.claim is not None  # the disconnect sends it itself, no one else at once
    assert world.store.pending_google_revocations() == []
    assert world.store.claim_google_revocation(dropped.pending) is None
    world.store.settle_google_revocation(dropped.pending, "not-its-claim")
    world.store.release_google_revocation(dropped.pending, "not-its-claim")
    assert world.store.claim_google_revocation(dropped.pending) is None  # still the disconnect's
    world.store.release_google_revocation(dropped.pending, dropped.claim)
    first = world.store.claim_google_revocation(dropped.pending)  # one statement takes it
    assert first is not None and world.store.claim_google_revocation(dropped.pending) is None
    world.clock.now = NOW + timedelta(seconds=cairn.store.hosted.CLAIM_STALE + 1)
    second = world.store.claim_google_revocation(dropped.pending)  # its claimant never came back
    assert second not in (None, first)
    world.store.settle_google_revocation(dropped.pending, first)  # a stale claim settles nothing
    assert rows(world.db, "SELECT COUNT(*) FROM google_revocations") == [(1,)]
    world.store.settle_google_revocation(dropped.pending, second)
    assert rows(world.db, "SELECT COUNT(*) FROM google_revocations") == [(0,)]


def test_a_deleted_workspaces_tokens_wait_with_no_trace_of_it(world):
    zed = world.store.sign_in(
        "sso", "sub-Zed", "zed@example.test", "Zed", issuer=ISSUER, join="personal"
    )
    account_id = zed.account_id
    own = world.store.for_account(account_id, zed.user.id)
    own.connect_google(
        "gmail",
        "zed@gmail.example.test",
        [GMAIL_SCOPE],
        TOKEN,
        subject=sub("zed@gmail.example.test"),
    )
    assert own.delete_account(account_id) is True
    ((waiting, subject, address, sealed),) = rows(
        world.db, "SELECT id, subject, address, sealed FROM google_revocations"
    )
    assert account_id not in waiting + subject + address + sealed
    assert [token for _, _, token in world.store.pending_google_revocations()] == [TOKEN]


def test_without_the_key_nothing_waits(world):
    name = connect(world).name
    world.store.google_custody = None
    view = world.store.for_account(world.Ada.view.account_id, world.Ada.id)
    view.remove_credential(name)
    assert rows(world.db, "SELECT COUNT(*) FROM google_revocations") == [(0,)]


def test_disconnecting_deletes_the_row_and_the_registry(world):
    name = connect(world).name
    dropped = world.Ada.view.disconnect_google(name)
    assert (dropped.name, dropped.address, dropped.token, dropped.switched) == (
        name, "ada.lovelace@gmail.example.test", TOKEN, []
    )  # fmt: skip
    assert TOKEN not in repr(dropped)
    assert rows(world.db, "SELECT COUNT(*) FROM credential_tokens") == [(0,)]
    assert not world.store.google_account_held(sub("ada.lovelace@gmail.example.test"))
    assert world.Ada.view.list_credentials() == []


def test_deleting_the_workspace_deletes_its_rows(world):
    connect(world)
    zed = world.store.sign_in(
        "sso", "sub-Zed", "zed@example.test", "Zed", issuer=ISSUER, join="personal"
    )
    own = world.store.for_account(zed.account_id, zed.user.id)
    own.connect_google(
        "gmail",
        "zed@gmail.example.test",
        [GMAIL_SCOPE],
        TOKEN,
        subject=sub("zed@gmail.example.test"),
    )
    assert rows(world.db, "SELECT COUNT(*) FROM credential_tokens") == [(2,)]
    assert own.delete_account(zed.account_id) is True
    assert rows(world.db, "SELECT account_id FROM credential_tokens") == [
        (world.Ada.view.account_id,)
    ]


def test_an_export_leaves_the_table_out(world):
    connect(world)
    out = io.BytesIO()
    world.store.for_account(world.Ada.view.account_id).export(out)
    with zipfile.ZipFile(out) as archive:
        everything = b"".join(archive.read(n) for n in archive.namelist())
    assert b"credential_tokens" not in everything
    assert b"ada.lovelace" not in everything
    assert not holds_secret(everything.decode("utf-8", "replace"), (TOKEN,))
