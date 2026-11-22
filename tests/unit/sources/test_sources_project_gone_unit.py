"""Finding 4: a source followed a project's NAME, so a deleted project could be taken over. Once
the project a source wrote into was deleted, any member who made a project of that name got the
source's notes. Deleting a project now takes its name out of every source that names it, in
the same write, and switches off a source left with no project; and a run writes only into
the projects its claim resolved, by id, so a name that comes to mean another project during a
run gets nothing.

A fake kind stands in for a mail source. Nothing leaves the process."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.store import Store
from cairn.store.access import PROJECT_PRIVATE

ISSUER = "https://id.example.test/pool-1"


class Feed:
    """A source whose pull answers the batches the test queued, each draft for the project
    the test names; `during` runs inside the pull."""

    def __init__(self) -> None:
        self.batches: list[list[tuple[str, str]]] = []
        self.during = None

    def check(self, config: dict) -> dict:
        return {"projects": list(config.get("projects", []))}

    def open(self, name: str, config: dict, **options) -> "Feed":
        return self

    async def pull(self, cursor):
        if self.during:
            self.during()
        batch = self.batches.pop(0) if self.batches else []
        drafts = [
            NoteDraft(project=project, title="Mail", text=f"# Mail\n\n{line}\n",
                      source_ref=f"m:{n}", label="mail")
            for n, (project, line) in enumerate(batch)
        ]  # fmt: skip
        return Pulled(drafts=drafts, cursor=f"c{len(self.batches)}")


@pytest.fixture
def world(tmp_path, monkeypatch):
    feed = Feed()
    kind = SourceKind(check=feed.check, open=feed.open, via="mailish", starts_off=True)
    monkeypatch.setitem(KINDS, "mailish", kind)
    store = Store(tmp_path / "cairn.db")

    def person(name: str):
        signed = store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
        view = store.for_account(signed.account_id, signed.user.id)
        return view.acting_as("session", name, signed.user.id)

    ada, eve = person("Ada"), person("Eve")  # the owner, a member
    yield SimpleNamespace(store=store, feed=feed, ada=ada, eve=eve)
    store.close()


def run(world):
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    return asyncio.run(run_source(world.store, "dana-mail", http=http, env={}))


def lines(world, project_id: str) -> list[str]:
    return [c.text.splitlines()[-1] for c in world.store.list_notes(project_id)]


def test_a_deleted_project_s_name_is_taken_out_of_its_source_which_goes_off(world):
    mail = world.ada.create_project("Mail", everyone="read")
    world.store.add_source("dana-mail", "mailish", {"projects": []})
    world.store.enable_source("dana-mail", ["mail"])  # the config keeps the caller's casing
    world.feed.batches = [[("mail", "First labelled mail.")]]
    assert run(world).saved == 1

    world.ada.delete_project(mail.id)
    gone = world.store.get_source("dana-mail")
    assert (gone.enabled, gone.config["projects"]) == (False, [])
    deleted, off = world.store.list_audit(limit=2)  # newest first
    assert deleted.action == "project.deleted"  # the deletion is the last event
    assert (off.action, off.actor) == ("source.disabled", "Ada")
    assert off.detail["source"] == "dana-mail" and "deleted" in off.detail["reason"]
    assert "mail" not in str(off.detail).lower().replace("dana-mail", "")
    (enabled,) = [e for e in world.store.list_audit(limit=20) if e.action == "source.enabled"]
    assert enabled.detail["projects"] == [f"{mail.id} (deleted)"]  # never the reused name

    taken = world.eve.create_project("mail", everyone="none")  # a member takes the name
    world.feed.batches = [[("mail", "Second labelled mail, for Dana.")]]
    assert run(world).off
    assert lines(world, taken.id) == []
    with pytest.raises(ValueError):  # nobody may switch it on without naming a project
        world.store.enable_source("dana-mail")


def test_a_source_that_keeps_another_project_stays_on_without_the_deleted_one(world):
    mail = world.ada.create_project("mail", everyone="read")
    world.ada.create_project("archive", everyone="read")
    world.store.add_source("dana-mail", "mailish", {"projects": []})
    world.store.enable_source("dana-mail", ["MAIL", "archive"])
    world.ada.delete_project(mail.id)
    kept = world.store.get_source("dana-mail")
    assert (kept.enabled, kept.config["projects"]) == (True, ["archive"])
    assert world.store.list_audit(limit=1)[0].action == "project.deleted"

    taken = world.eve.create_project("mail", everyone="none")
    world.feed.batches = [[("mail", "For the old mail project."), ("archive", "Kept mail.")]]
    report = run(world)
    assert (report.saved, report.reasons) == (1, {"project not allowed": 1})
    assert lines(world, taken.id) == []


def test_a_name_that_means_another_project_during_a_run_gets_nothing(world):
    """The runner looked a draft's project up by name at each draft, after asking whether it
    still held the claim. A project deleted and made again under its name in between got the
    draft. It now writes only into the projects its claim resolved, by id."""
    mail = world.ada.create_project("mail", everyone="read")
    world.store.add_source("dana-mail", "mailish", {"projects": []})
    world.store.enable_source("dana-mail", ["mail"])
    made = []

    class Racing:
        """The real store; right after the run is told it still holds its claim, the project
        goes and a member makes one of the same name."""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def acting_as(self, *args):
            return Racing(self._inner.acting_as(*args))

        def holds_source_claim(self, claim):
            held = self._inner.holds_source_claim(claim)
            if not made:
                world.ada.delete_project(mail.id)
                made.append(world.eve.create_project("mail", everyone="none"))
            return held

    claim = world.store.claim_source("dana-mail")
    assert claim.project_ids == {"mail": mail.id}  # resolved once, in the claim's write
    world.store.fail_source_run(claim, "a test claim")  # let it go again
    world.feed.batches = [[("mail", "Second labelled mail, for Dana.")]]
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    report = asyncio.run(run_source(Racing(world.store), "dana-mail", http=http, env={}))
    assert made and lines(world, made[0].id) == []
    assert report.saved == 0 and report.reasons == {"no such project": 1}


def test_a_project_made_private_by_one_who_does_not_decide_leaves_its_sources(world):
    """Rule (a) in two steps: only an owner sends a source into a project not every member
    reaches, but its maker could make a project every member reaches, have a source switched
    into it, then take `everyone` to none. The source went on writing a person's mail into
    a project that person no longer reached. Narrowing a project so, by anyone but an owner or
    the operator, now takes it out of every source that names it, as deleting it does."""
    signed = world.store.sign_in(
        "sso", "sub-Cy", "cy@example.test", "Cy", issuer=ISSUER, join="default"
    )
    world.store.set_role(signed.user.id, "admin")
    view = world.store.for_account(signed.account_id, signed.user.id)
    cy = view.acting_as("session", "Cy", signed.user.id)
    shared = world.eve.create_project("eve-shared", everyone="write")
    world.ada.create_project("archive", everyone="write")
    world.store.add_source("dana-mail", "mailish", {"projects": []})
    world.store.add_source("dana-notes", "mailish", {"projects": []})
    cy.enable_source("dana-mail", ["eve-shared"])
    cy.enable_source("dana-notes", ["EVE-SHARED", "archive"])

    world.eve.set_project_access(shared.id, everyone="none")
    gone = world.store.get_source("dana-mail")
    assert (gone.enabled, gone.config["projects"]) == (False, [])
    kept = world.store.get_source("dana-notes")
    assert (kept.enabled, kept.config["projects"]) == (True, ["archive"])
    changed, off = world.store.list_audit(limit=2)  # newest first
    assert changed.action == "project.access_changed"
    assert (off.action, off.detail["source"]) == ("source.disabled", "dana-mail")
    assert off.detail["reason"] == PROJECT_PRIVATE  # never the project's name
    world.feed.batches = [[("eve-shared", "Mail for Dana.")]]
    assert run(world).off
    assert lines(world, shared.id) == []


def test_a_project_an_owner_makes_private_keeps_its_sources(world):
    board = world.ada.create_project("board", everyone="read")
    world.store.add_source("dana-mail", "mailish", {"projects": []})
    world.store.enable_source("dana-mail", ["board"])
    world.ada.set_project_access(board.id, everyone="none")  # an owner decides where it writes
    kept = world.store.get_source("dana-mail")
    assert (kept.enabled, kept.config["projects"]) == (True, ["board"])
    world.store.set_project_access(board.id, everyone="read")
    world.store.set_project_access(board.id, everyone="none")  # and so does the operator
    assert world.store.get_source("dana-mail").config["projects"] == ["board"]
