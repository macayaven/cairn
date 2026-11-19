"""H6c acceptance tests (frozen): what a workspace takes on disk is bounded by its plan.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/store/__init__.py and src/cairn/server.py.

Why: the storage cap counts the text of notes, and nothing bounded what is built from it.
Measured before this task: a workspace filled to a 400 kB cap with one-word list items on
one page took 52 MB of database (131 times its cap), 15 MB with ordinary prose, and one
20 kB note took 6.6 seconds to save. A whole-branch review rated it high: one workspace
could fill the server's disk and stall it.

Three bounds, and what they add up to
- A page holds at most `cairn.store.PAGE_CLAIMS_MAX` claims (2,000), on every install,
  plans or not. A note that would take its page past that is refused before anything of
  it is stored: REST answers 409 with an `error` that names the page and says to file the
  note under another topic; the `note` tool fails with the same sentence; the composer
  shows the form again (409) with it. The store refuses to save such a page whoever asks.
- A workspace under a storage cap holds at most one passage per
  `cairn.store.BYTES_PER_PASSAGE` (40) bytes of that cap. A passage is one paragraph, list
  item, quote or code block of a note: what becomes a claim. `view.passages()` is how many
  the workspace holds. A note that would take it past is refused like any cap, inside the
  write: `OverLimit` with the limit "passages"; REST answers 402 with `limit` "passages",
  `used` and `cap`. Ordinary notes run 80 to 300 bytes a passage and never meet it.
- The versions of pages a workspace keeps take at most `cairn.store.HISTORY_FACTOR` (4)
  times its storage cap, counting each version's body and claims. When a save would pass
  that, the oldest versions in the workspace go first, in the same write, never a page's
  latest, and version numbers never start again. `versions_per_page` still holds beside it.
- Together: whatever its notes look like, a workspace at its caps takes at most 30 times
  its storage cap in the database file, once compacted.
- Without a storage cap (a self-hosted install, or a plan that names none) only the page
  bound applies.

Who sees the totals
- GET /api/usage gains `passages`: `{"used": n, "cap": n or null}`, beside `limits`, which
  keeps its four entries. /usage shows it in words.
- The workspace's totals count every project, so they are for whoever reaches every project:
  an owner, a key an owner made, or a credential of the workspace that acts for no person.
  An admin or a member gets 403 from /usage, and an admin's key from /api/usage; the
  workspace page does not list Usage for them; and when a cap refuses their note the
  answer names the limit and holds no number and no plan (over REST, exactly `error` and
  `limit`).
"""

import base64
import hashlib
import re
import secrets
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.plans import Plan, Plans
from cairn.server import create_app
from cairn.store import Limits, OverLimit, Store

BASE = "https://cairn.example.test"
ISSUER = "https://id.acme.example/pool-1"
REDIRECT = "http://127.0.0.1:33418/callback"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
CAP = 60_000  # bytes of notes the plan allows
PLANS = Plans(
    {"small": Plan("Small", notes_per_month=10_000, storage_bytes=CAP, projects=5)},
    default="small",
)


def bullets(n: int, items: int = 375) -> str:
    """About 3 kB of one-word list items: the densest a note gets."""
    return "".join(f"- w{n}x{i}\n" for i in range(items))


def prose(n: int, lines: int = 36) -> str:
    """About 3 kB of ordinary lines, each a paragraph."""
    return "".join(
        f"Entry {n}-{i}: the retry count for job {i} moved from {i % 7} to {i % 5} today.\n\n"
        for i in range(lines)
    )


SHAPES = {
    "list items, all on one page": lambda n: "# Log\n\n" + bullets(n),
    "prose, all on one page": lambda n: "# Log\n\n" + prose(n),
    "list items, a page per note": lambda n: f"# Log {n}\n\n" + bullets(n),
    "prose, a page per note": lambda n: f"# Log {n}\n\n" + prose(n),
    "small dense notes, one page": lambda n: "# Log\n\n" + bullets(n, items=60),
}


def size_of(db: Path) -> int:
    return sum(p.stat().st_size for p in db.parent.iterdir() if p.name.startswith(db.name))


class World:
    def __init__(self, tmp_path, plans: Plans | None = PLANS) -> None:
        self.db = tmp_path / "cairn.db"
        self.store = Store(self.db)
        self.app = create_app(self.store, public_url=BASE, plans=plans)
        self.key = {"x-api-key": self.store.create_key("operator")}
        self.project = self.store.create_project("ops")

    def person(self, name: str) -> SimpleNamespace:
        email = f"{name.lower()}@acme.example"
        signed = self.store.sign_in(
            "sso", f"sub-{name}", email, name, issuer=ISSUER, join="default"
        )
        view = self.store.for_account(signed.account_id, signed.user.id)
        cookie = {"cookie": f"__Host-cairn_session={signed.token}"}
        return SimpleNamespace(id=signed.user.id, name=name, cookie=cookie, view=view)

    def agent(self, who: SimpleNamespace) -> dict:
        """An agent `who` approved, as the header it calls with."""
        client = self.store.register_client("laptop", [REDIRECT])
        verifier = secrets.token_urlsafe(32)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        code = who.view.create_code(client.id, REDIRECT, challenge)
        token = self.store.redeem_code(code, client.id).access_token
        return {"authorization": f"Bearer {token}"}


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.store.close()


def note(client, world, text: str, who: dict | None = None):
    return client.post("/api/projects/ops/notes", headers=who or world.key, json={"text": text})


# What it adds up to


@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_workspace_at_its_caps_fits_in_thirty_times_its_storage_cap(world, shape):
    world.store.compact()
    empty = size_of(world.db)
    statuses: list[int] = []
    with TestClient(world.app, base_url=BASE) as client:
        for n in range(60):  # 60 notes of 0.5 to 3 kB: three times what the cap would take
            statuses.append(note(client, world, SHAPES[shape](n)).status_code)
    saved = statuses.count(201)
    assert saved >= 3, statuses  # the bounds leave room to work in
    assert set(statuses) <= {201, 402, 409}, statuses  # and say so plainly when they refuse
    assert statuses[-1] != 201  # by the end something had refused

    # Every note that was taken is whole: stored, compiled and found.
    notes = world.store.list_notes(world.project.id)
    assert len(notes) == saved
    assert world.store.stored_bytes() <= CAP
    for n in (0, saved - 1):
        word = f"w{n}x0" if "list" in shape or "dense" in shape else f"{n}-0"
        assert world.store.search(word, limit=5), (shape, n)

    world.store.compact()
    taken = size_of(world.db) - empty
    assert taken <= 30 * CAP, f"{shape}: {taken:,} bytes for a cap of {CAP:,} ({taken / CAP:.0f}x)"
    con = sqlite3.connect(world.db)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


# The three bounds


def test_a_page_takes_two_thousand_claims_and_no_more(tmp_path):
    from cairn.store import PAGE_CLAIMS_MAX

    assert PAGE_CLAIMS_MAX == 2_000
    world = World(tmp_path, plans=None)  # a self-hosted install: this bound is for everyone
    try:
        with TestClient(world.app, base_url=BASE) as client:
            for n in range(5):  # 5 notes of 400 one-line claims fill the page exactly
                text = "# Ledger\n\n" + "".join(f"Line {n}-{i} holds.\n\n" for i in range(400))
                assert note(client, world, text).status_code == 201, n
            page = world.store.get_page(world.project.id, "ledger")
            assert len(page.claims) == PAGE_CLAIMS_MAX
            before = (len(world.store.list_notes(world.project.id)), page.version)

            full = note(client, world, "# Ledger\n\nOne more line, kingfisher.\n")
            assert full.status_code == 409
            said = full.json()["error"]
            assert "ledger" in said.lower() and "topic" in said and "kingfisher" not in said
            call = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "note",
                    "arguments": {"project": "ops", "text": "# Ledger\n\nAnother, kingfisher.\n"},
                },
            }
            answer = client.post("/mcp", headers={**MCP_HEADERS, **world.key}, json=call).json()
            assert answer["result"]["isError"] is True
            assert "topic" in answer["result"]["content"][0]["text"]

            page = world.store.get_page(world.project.id, "ledger")
            assert (len(world.store.list_notes(world.project.id)), page.version) == before
            assert world.store.search("kingfisher") == []
            # Another topic is the way on, as the answer says.
            other = note(client, world, "# Ledger, second book\n\nOne more line, kingfisher.\n")
            assert other.status_code == 201
    finally:
        world.store.close()


def test_passages_are_capped_by_the_storage_cap(tmp_path):
    from cairn.store import BYTES_PER_PASSAGE

    assert BYTES_PER_PASSAGE == 40
    store = Store(tmp_path / "cairn.db")
    try:
        project = store.create_project("ops")
        assert store.passages() == 0
        limited = store.limited(Limits(storage_bytes=4_000))  # 100 passages
        limited.add_note(project.id, "# A\n\n" + "".join(f"- a{i}\n" for i in range(60)))
        assert store.passages() == 60
        with pytest.raises(OverLimit) as over:
            limited.add_note(project.id, "# B\n\n" + "".join(f"- b{i}\n" for i in range(41)))
        assert (over.value.limit, over.value.used, over.value.cap) == ("passages", 60, 100)
        assert len(store.list_notes(project.id)) == 1 and store.passages() == 60
        limited.add_note(project.id, "# B\n\n" + "".join(f"- b{i}\n" for i in range(40)))
        assert store.passages() == 100
        # A heading and a blank line are not passages, and no cap means no count is refused.
        store.add_note(project.id, "# C\n\n" + "".join(f"- c{i}\n" for i in range(500)))
        assert store.passages() == 600
    finally:
        store.close()


def test_rest_names_the_passages_limit_and_usage_shows_it(world):
    with TestClient(world.app, base_url=BASE) as client:
        cap = CAP // 40
        assert note(client, world, "# Log\n\n" + bullets(0, items=cap - 10)).status_code == 201
        refused = note(client, world, "# Log 2\n\n" + bullets(1, items=11))
        assert refused.status_code == 402
        body = refused.json()
        assert (body["limit"], body["used"], body["cap"]) == ("passages", cap - 10, cap)
        assert body["resets_at"] is None and "passages" in body["error"]

        usage = client.get("/api/usage", headers=world.key).json()
        assert usage["passages"] == {"used": cap - 10, "cap": cap}
        assert set(usage["limits"]) == {
            "notes_per_month",
            "storage_bytes",
            "projects",
            "model_budget_micros",
        }


def test_old_versions_give_way_and_the_latest_stays(tmp_path):
    from cairn.compiler import compile_note
    from cairn.store import HISTORY_FACTOR

    assert HISTORY_FACTOR == 4
    store = Store(tmp_path / "cairn.db")
    try:
        project = store.create_project("ops")
        cap = 20_000
        view = store.limited(Limits(storage_bytes=cap))

        def add(text: str) -> None:
            note, _ = view.add_note(project.id, text)
            assert compile_note(view, note).status == "saved"

        def kept() -> int:
            total = 0
            for page in view.list_pages(project.id):
                for number in view.page_versions(project.id, page.slug):
                    version = view.get_page(project.id, page.slug, number)
                    total += len(version.body.encode())
                    total += sum(len(claim.text.encode()) for claim in version.claims)
            return total

        add("# Small\n\nA page that changes little.\n")
        add("# Small\n\nIt gets a second line.\n")
        assert view.page_versions(project.id, "small") == [1, 2]  # history is kept while it fits

        for n in range(18):  # one page that grows by about 1 kB a note
            add("# Log\n\n" + prose(n, lines=12))
        versions = view.page_versions(project.id, "log")
        assert versions[-1] == 18 and versions == sorted(versions)
        assert len(versions) < 18  # the oldest went
        assert (
            kept() <= HISTORY_FACTOR * cap + len(view.get_page(project.id, "log").body.encode()) * 2
        )
        assert view.get_page(project.id, "log").version == 18  # the latest always stays
        assert view.page_versions(project.id, "small")[-1] == 2  # every page keeps its latest
        add("# Log\n\nOne more.\n")
        assert view.page_versions(project.id, "log")[-1] == 19  # numbers never start again
    finally:
        store.close()


# Who sees the totals


def test_only_an_owner_sees_the_workspaces_totals(world):
    ada, brook, cy = (world.person(name) for name in ("Ada", "Brook", "Cy"))
    ada.view.set_role(brook.id, "admin")
    owners_key = {"x-api-key": ada.view.create_key("owner-script")}
    admins_key = {"x-api-key": brook.view.create_key("admin-script")}
    members_agent = world.agent(cy)
    with TestClient(world.app, base_url=BASE) as client:

        def get(path: str, headers: dict):
            return client.get(path, headers=headers, follow_redirects=False)

        assert get("/usage", ada.cookie).status_code == 200
        assert 'href="/usage"' in get("/workspace", ada.cookie).text
        assert get("/api/usage", world.key).status_code == 200  # the workspace's own credential
        assert get("/api/usage", owners_key).status_code == 200

        for who in (
            brook,
            cy,
        ):  # an admin reaches projects as a member does, so neither sees totals
            assert get("/usage", who.cookie).status_code == 403
            hub = get("/workspace", who.cookie)
            assert hub.status_code == 200 and 'href="/usage"' not in hub.text
        assert get("/api/usage", admins_key).status_code == 403

        # At a cap, they learn which limit and nothing of the workspace's numbers or plan.
        assert note(client, world, "# Fill\n\n" + bullets(0, items=CAP // 40)).status_code == 201
        refused = note(client, world, "# Mine\n\n- one more\n", admins_key)
        assert refused.status_code == 402
        body = refused.json()
        assert set(body) == {"error", "limit"} and body["limit"] == "passages"
        assert not any(ch.isdigit() for ch in body["error"]) and "Small" not in body["error"]

        call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "note",
                "arguments": {"project": "ops", "text": "# Mine\n\n- more\n"},
            },
        }
        answer = client.post("/mcp", headers={**MCP_HEADERS, **members_agent}, json=call).json()
        said = answer["result"]["content"][0]["text"]
        assert answer["result"]["isError"] is True and "passages" in said
        assert not any(ch.isdigit() for ch in said) and "Small" not in said

        typed = client.post(
            "/b/ops/notes",
            data={"text": "# Mine\n\n- one more\n"},
            headers={**cy.cookie, **SAME_ORIGIN},
            follow_redirects=False,
        )
        shown = re.sub(r"<[^>]+>", " ", typed.text)  # what is read, without tags or addresses
        assert typed.status_code == 402 and "Small" not in shown
        assert "1500" not in shown and "1,500" not in shown

        # An owner is told all of it.
        full = note(client, world, "# Mine\n\n- one more\n", owners_key).json()
        assert (full["limit"], full["cap"], full["plan"]) == ("passages", CAP // 40, "small")
