"""System invariants: rules that must hold across every feature, checked on a real database.

Each milestone adds what it stores to `fill`; the checks themselves do not change. They
read the whole database, so a table a later task adds is covered the day it appears.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cairn.compiler import compile_note
from cairn.store import Store

MARKER = "zebrafinch"  # appears in everything `fill` writes, and in nothing else
ISSUER = "https://id.example.test/pool-1"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def fill(store: Store, account_id: str, tag: str) -> None:
    """Write one of everything a workspace can hold, each carrying MARKER and `tag`."""
    view = store.for_account(account_id)
    project = view.create_project(
        f"{MARKER}-{tag}-project", f"{MARKER} {tag} description", kind="repo"
    )
    note, _ = view.add_note(
        project.id,
        f"# {MARKER} {tag} note\n\nThe {MARKER} {tag} fact is 42.\n",
        label=f"{MARKER}-{tag}-label",
        source_ref=f"{MARKER}:{tag}:ref",
        via="system-test",
    )
    assert compile_note(view, note).status == "saved"
    assert view.search(MARKER)
    view.create_key(f"{MARKER}-{tag}-key")
    view.create_session()
    group = view.create_set(f"{MARKER}-{tag}-set", (project.id,))
    view.create_key(f"{MARKER}-{tag}-limited-key", projects=(group.id,), access="read")
    # A client registers itself before anyone approves it and belongs to no workspace, so
    # its name is not workspace data; the grant that approval makes is.
    client = store.register_client(f"agent-of-{tag}", ["http://127.0.0.1:33418/callback"])
    code = view.create_code(client.id, "http://127.0.0.1:33418/callback", "c" * 43)
    assert store.redeem_code(code, client.id) is not None
    # G1: a setting, and audit rows under an actor of the workspace (every write above made
    # one too, under "system", and the redeemed code one under the agent).
    view.set_setting("guard.policy", "secrets+personal")
    actor = view.acting_as("key", f"{MARKER}-{tag}-actor")
    actor.record_note(project.id, "refused", {"rules": ["jwt"]})
    actor.create_session()  # a session opened with that key carries its label
    # A source, with a cursor, the report of a run that worked and the error of one that
    # did not (a source keeps the env names of its keys, never the keys).
    source = f"{MARKER}-{tag}-source"
    view.add_source(
        source,
        "langfuse",
        {
            "base_url": f"https://{MARKER}-{tag}.example.test",
            "projects": [project.name],
            "public_key_env": "ZEBRA_PUBLIC_KEY",
            "secret_key_env": "ZEBRA_SECRET_KEY",
            "score_name": f"{MARKER}-{tag}-score",
        },
    )
    report = {"saved": 1, "skipped": 1, "reasons": {f"{MARKER} {tag} reason": 1}}
    assert view.finish_source_run(view.claim_source(source), f"{MARKER}-{tag}-cursor", report)
    assert view.fail_source_run(view.claim_source(source), f"{MARKER} {tag} failure")
    # T1: an open invitation; a person who joined by one (their own first workspace gone,
    # so this is their only one), named in a project's rule, who made a private project and a key.
    view.invite(f"{MARKER}.{tag}.invitee@example.test", role="member")
    email = f"{MARKER}.{tag}.{account_id}.member@example.test"
    member = store.sign_in(
        "sso", f"{MARKER}-{tag}-{account_id}", email, f"{MARKER} {tag} Member", issuer=ISSUER,
        join="personal",
    )  # fmt: skip
    store.accept_invite(view.invite(email, role="admin").token, member.user.id)
    assert store.delete_account(member.account_id) is True
    theirs = store.for_account(account_id, member.user.id)
    view.set_project_access(project.id, everyone="read", people={member.user.id: "write"})
    made = theirs.create_project(f"{MARKER}-{tag}-private", everyone="none")
    assert made.maker_id == member.user.id
    theirs.create_key(f"{MARKER}-{tag}-person-key")
    # A person removed again: what is left of them is the workspace's removal marker.
    gone = f"{MARKER}.{tag}.{account_id}.leaver@example.test"
    leaver = store.sign_in(
        "sso", f"{MARKER}-{tag}-{account_id}-leaver", gone, f"{MARKER} {tag} Leaver",
        issuer=ISSUER, join="personal",
    )  # fmt: skip
    store.accept_invite(view.invite(gone).token, leaver.user.id)
    assert store.delete_account(leaver.account_id) is True  # this is their only workspace now
    assert view.remove_member(leaver.user.id) is True
    assert store.get_user(leaver.user.id) is None  # forgotten, but for the marker
    assert view.list_audit()
    # H6: the month's usage (the note above counted itself), a model call, and the plan the
    # operator put the workspace on.
    view.record_model_use(1_004, 5_570, 2_850)
    store.set_plan(account_id, f"{MARKER}-{tag}-plan")
    assert view.usage().notes == 1 and view.stored_bytes() > 0
    # What a build that sold plans left (H7, R3): a subscription with the provider's
    # references, the message seen for it and an open checkout. Nothing reads them now;
    # deleting the workspace still erases them.
    now = datetime.now(UTC).isoformat()
    with store._write() as con:
        con.execute(
            "INSERT INTO subscriptions (account_id, provider, plan, status, since, customer_ref,"
            " subscription_ref) VALUES (?, ?, ?, 'active', ?, ?, ?)",
            (account_id, f"{MARKER}-{tag}-provider", f"{MARKER}-{tag}-bought", now,
             f"cus-{MARKER}-{tag}", f"sub-{MARKER}-{tag}"),
        )  # fmt: skip
        con.execute(
            "INSERT INTO billing_messages (provider, id, account_id, received_at)"
            " VALUES (?, ?, ?, ?)",
            (f"{MARKER}-{tag}-provider", f"evt-{MARKER}-{tag}-{account_id}", account_id, now),
        )
        con.execute(
            "INSERT INTO checkouts (account_id, plan, idempotency_key, url, opened_at, ends_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, f"{MARKER}-{tag}-bought", f"key-{MARKER}-{tag}",
             f"https://pay.example.test/{MARKER}-{tag}", now, now),
        )  # fmt: skip


def test_deleting_a_workspace_leaves_no_row_that_names_it(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        kept = store.create_account("Kept Works")
        gone = store.create_account(f"{MARKER} gone Works")
        person = store.sign_in(
            "sso",
            f"{MARKER}-gone-subject",
            f"{MARKER}.gone@example.test",
            f"{MARKER} gone Person",
            issuer=ISSUER,
            join="personal",
        )
        fill(store, kept.id, "kept")
        fill(store, gone.id, "gone")
        fill(store, person.account_id, "gone")

        before = dump(db)
        assert before.count(f"{MARKER}-gone") >= 6 and gone.id in before

        assert store.delete_account(gone.id) is True
        assert store.delete_account(person.account_id) is True

        after = dump(db)
        for needle in (
            gone.id,
            person.account_id,
            person.user.id,
            f"{MARKER}-gone",
            f"{MARKER} gone",
        ):
            assert needle not in after, needle
        assert f"{MARKER}.gone@example.test" not in after
        # The other workspace is whole.
        assert store.for_account(kept.id).search(MARKER)
        assert after.count(f"{MARKER}-kept") == before.count(f"{MARKER}-kept")
    finally:
        store.close()


def test_two_workspaces_filled_the_same_way_never_see_each_other(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        one, two = store.create_account("One"), store.create_account("Two")
        fill(store, one.id, "one")
        fill(store, two.id, "two")
        for mine, theirs, account in (("one", "two", one), ("two", "one", two)):
            view = store.for_account(account.id)
            seen = " ".join(
                [
                    *(b.name for b in view.list_projects()),
                    *(k.label for k in view.list_keys()),
                    *(g.client_name for g in view.list_grants()),
                    *(h.text for h in view.search(MARKER)),
                    *(repr(s) for s in view.list_sources()),
                ]
            )
            assert f"{MARKER}-{mine}" in seen and f"agent-of-{mine}" in seen
            assert f"-{theirs}" not in seen and f" {theirs} " not in seen
    finally:
        store.close()


def test_a_deleted_workspaces_text_leaves_the_files_and_compact_takes_the_rest(tmp_path):
    """The database zeroes what it deletes (secure_delete, and FTS5's own secure delete), and
    delete_account ends with a truncating checkpoint, so after it alone no note's text is in
    the main file, the -wal or the -shm. Short index keys (a page's slug, made from a title)
    can stay in index pages other workspaces still use; compact takes those too. Six
    workspaces of 200 notes, written in turns so their rows share pages and the b-trees
    rebalance, one deleted. Titles carry one word, the text under them another. The one place
    the title word may stay is the name of the OAuth client that registered itself (clients
    belong to no workspace, see the first check)."""
    db = tmp_path / "cairn.db"
    title, text, client = b"osprey", b"quillwort", b"agent-of-osprey"
    kept = [".example.test", "-score", "-cursor"]  # what fill() puts in the source's config
    words = ["heron", "kestrel", "plover", "linnet", "avocet", title.decode()]
    store = Store(db)
    try:
        views = [store.for_account(store.create_account(f"{w} Works").id) for w in words]
        for view, word in zip(views, words, strict=True):
            fill(store, view.account_id, word)
        projects = [view.create_project("core") for view in views]
        for n in range(200):
            for view, project, word in zip(views, projects, words, strict=True):
                inside = text.decode() if view is views[-1] else f"{word}text"
                body = f"The {inside} figure number {n} says retries were {n % 7}.\n" * 3
                note, _ = view.add_note(project.id, f"# {word} report {n}\n\n{body}")
                assert compile_note(view, note).status == "saved"

        def files() -> list[Path]:
            return [path for path in tmp_path.iterdir() if path.name.startswith(db.name)]

        before = b"".join(path.read_bytes().lower() for path in files())
        assert before.count(text) > 0 and before.count(title) > before.count(client)
        # what its source holds that no index keys: config, cursor, last report and error
        held = [f"{MARKER}-{title.decode()}{rest}".lower().encode() for rest in kept]
        assert all(before.count(word) > 0 for word in held)
        assert store.delete_account(views[-1].account_id) is True  # and nothing else
        for path in files():
            data = path.read_bytes().lower()
            assert text not in data, path.name
            assert not [word for word in held if word in data], path.name
        store.compact()
        for path in files():
            data = path.read_bytes().lower()
            assert text not in data and data.count(title) == data.count(client), path.name
        for view, word in zip(views[:-1], words[:-1], strict=True):  # the others are whole
            assert len(view.search(word, limit=50)) == 50
    finally:
        store.close()


def outside_the_audit_log(db: Path) -> str:
    """The rows of every table but the audit log, which names what was deleted (H5b)."""
    rows = dump(db).splitlines()
    return "\n".join(row for row in rows if not row.startswith('INSERT INTO "audit_events"'))


def test_a_deleted_project_and_a_forgotten_note_leave_no_row_that_names_them(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        account = store.create_account(f"{MARKER} erasing Works")
        fill(store, account.id, "erased")  # its project is in a set, a rule, a key and a source
        view = store.for_account(account.id)
        project = view.get_project(f"{MARKER}-erased-project")
        kept = view.create_project(f"{MARKER}-kept")
        notes = []
        for owner in ("Brook owns the rollback.", "Dee owns the release."):
            note, _ = view.add_note(kept.id, f"# Weekly\n\n- {owner}\n")
            assert compile_note(view, note).status == "saved"
            notes.append(note)
        stay, go = notes
        alone, _ = view.add_note(kept.id, f"# {MARKER} plover\n\nThe plover ledger shuts.\n")
        assert compile_note(view, alone).status == "saved"
        slugs = (f"{MARKER}-erased-note", f"{MARKER}-plover")  # pages that go: titles of notes
        con = sqlite3.connect(db)
        try:
            spans = [r[0] for r in con.execute("SELECT id FROM spans WHERE note_id=?", (go.id,))]
        finally:
            con.close()
        assert spans and project.id in outside_the_audit_log(db)
        assert all(slug in dump(db) for slug in slugs)

        assert view.delete_project(project.id) is True
        assert view.forget_note(go.id).rebuilt == ("weekly",)
        assert view.forget_note(alone.id).removed == (f"{MARKER}-plover",)

        after = outside_the_audit_log(db)
        for needle in (project.id, go.id):
            assert needle not in after, needle
        everything = dump(db)  # the audit log names the note, never its spans
        for needle in (*spans, *slugs):  # the audit log keeps its events, not their slugs
            assert needle not in everything, needle
        assert stay.id in after and kept.id in after  # what was not erased is whole
    finally:
        store.close()
