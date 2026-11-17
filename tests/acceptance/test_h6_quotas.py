"""H6 acceptance tests (frozen): plans, quotas and metering.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/plans.py, src/cairn/store/__init__.py and
src/cairn/server.py.

Metering (always on, hosted or not)
- `Store(path, clock=None)`: `clock` returns the current time as an aware datetime; the
  month a write is counted in is its UTC month, written "YYYY-MM".
- `view.usage(period=None) -> Usage` for the current month or the one named: `period`,
  `notes`, `note_bytes`, `model_tokens_in`, `model_tokens_out`, `model_micros`.
  `view.stored_bytes()` is the UTF-8 size of every note the workspace holds, whenever it
  was written. A note that is stored counts once, in the transaction that stores it; the
  same note again ("unchanged") counts nothing.
- `view.record_model_use(tokens_in, tokens_out, micros)` adds a model call to the current
  month. `micros` are millionths of a US dollar, an integer; none of the three may be
  negative (ValueError).
- Usage belongs to a workspace: another workspace never sees it, and `delete_account`
  removes it.

Limits (the store refuses inside the write's own transaction)
- `view.limited(Limits(notes_per_month=None, storage_bytes=None, projects=None,
  model_budget_micros=None))` is a view that is a drop-in for the one it came from and
  raises `cairn.store.OverLimit` (`limit`, `used`, `cap`, `resets_at`, an aware datetime
  or None) instead of writing:
  a new note when the month's notes have reached the cap ("notes_per_month") or when it
  would take the stored bytes over the cap ("storage_bytes"), a new project when the
  workspace already has as many as the cap ("projects"). `resets_at` is the first instant of
  the next UTC month for the monthly limit and None for the others. A cap of None is no
  cap. Writers in parallel cannot pass a cap between them. A note the workspace already
  holds is "unchanged", never over a limit.
- `view.model_budget_left()` is None without a budget, else the month's budget less what
  was recorded, never below 0.

Plans (`cairn.plans`)
- `Plan(label, notes_per_month=None, storage_bytes=None, projects=None,
  model_budget_micros=None, requests_per_minute=None)`; `Plans(table, default)` where
  `table` maps a plan's name to its Plan and `default` names one of them (ValueError
  otherwise). `cairn.plans.load(path) -> Plans` reads the same from a YAML file with the
  keys `default` and `plans`; an unknown key, a negative number or a missing default is a
  ValueError that names what is wrong.
- A workspace's plan is a name in the store: `Account.plan` (None until set) and
  `store.set_plan(account_id, name_or_None)` (KeyError for an unknown account). A
  workspace with no plan, or with a name the table does not have, is on the default.

The service
- `create_app(..., plans=None, clock=None)`. Without `plans` nothing is capped or rate
  limited: that is a self-hosted install. With it, every request acts through a view
  limited by its workspace's plan.
- Over a limit: REST answers 402 with JSON `error` (a sentence), `limit`, `used`, `cap`,
  `resets_at` (ISO 8601, or null) and `plan`; the `note` and `new_project` tools fail with a
  message that names
  the limit, the usage, the cap and when it resets; the web composer shows the form again
  (402) with that message and what was typed. Nothing is stored.
- `requests_per_minute`: when a workspace's keys, grants and sessions together have made
  that many requests in the last 60 seconds, the next one is 429 with `Retry-After` (whole
  seconds, at least 1) and stores nothing. Requests without a credential, /healthz and
  /static are not counted. The count lives in memory: nothing is written for it.
- GET /api/usage (API key or grant): `plan`, `label`, `period`, `resets_at` and `limits`,
  an object with one entry per limit: `{"used": n, "cap": n or null}` for
  "notes_per_month", "storage_bytes", "projects" and "model_budget_micros". Without `plans`:
  `plan` and `label` are null and every cap is null. GET /usage shows the same to a person.
  Reading either writes nothing.
- Command line: `plan-set --db PATH --account ID --plan NAME` (or `--default`),
  `usage --db PATH --account ID` (prints the month's usage as JSON) and
  `serve --plans FILE`.
"""

import json
import re
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.plans import Plan, Plans, load
from cairn.server import create_app
from cairn.store import Limits, OverLimit, Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "host": "127.0.0.1:8765"}
NOVEMBER = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
DECEMBER = datetime(2026, 12, 1, tzinfo=UTC)
DECEMBER_FIRST = "2026-12-01T00:00:00+00:00"
ACCENTED = "Größe: 5 µm\n"  # twelve characters, fifteen bytes
PLANS = Plans(
    {
        "free": Plan(
            "Free", notes_per_month=3, storage_bytes=400, projects=2, model_budget_micros=0
        ),
        "solo": Plan(
            "Solo",
            notes_per_month=1_000,
            storage_bytes=10_000_000,
            projects=50,
            model_budget_micros=2_000_000,
            requests_per_minute=5,
        ),
    },
    default="free",
)


class Clock:
    def __init__(self, now: datetime = NOVEMBER) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


def note(n: int) -> str:
    return f"# Fact {n}\n\nQuill keeps fact number {n}.\n"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    opened = Store(tmp_path / "cairn.db", clock=clock)
    yield opened
    opened.close()


# Metering


def test_every_stored_note_is_counted_once_in_its_month(store, clock):
    project = store.create_project("ops")
    assert store.usage() == store.usage("2026-11")
    empty = store.usage()
    assert (empty.period, empty.notes, empty.note_bytes) == ("2026-11", 0, 0)

    store.add_note(project.id, note(1))
    store.add_note(project.id, ACCENTED)
    _, created = store.add_note(project.id, note(1))
    assert created is False  # the same note again counts nothing
    used = store.usage()
    assert len(ACCENTED) == 12 and len(ACCENTED.encode()) == 15
    assert (used.notes, used.note_bytes) == (2, len(note(1).encode()) + 15)
    assert store.stored_bytes() == used.note_bytes

    clock.advance(days=30)  # 10 December
    store.add_note(project.id, note(2))
    december = store.usage()
    assert (december.period, december.notes) == ("2026-12", 1)
    assert store.usage("2026-11").notes == 2
    assert store.stored_bytes() == used.note_bytes + len(note(2).encode())


def test_model_use_is_metered_against_a_monthly_budget(store, clock):
    assert store.model_budget_left() is None  # no budget, no cap
    limited = store.limited(Limits(model_budget_micros=2_000_000))
    assert limited.model_budget_left() == 2_000_000

    limited.record_model_use(tokens_in=1_004, tokens_out=5_570, micros=1_250_000)
    used = store.usage()
    assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (
        1_004,
        5_570,
        1_250_000,
    )
    assert limited.model_budget_left() == 750_000
    limited.record_model_use(tokens_in=10, tokens_out=10, micros=900_000)
    assert limited.model_budget_left() == 0  # never below zero
    assert store.usage().model_micros == 2_150_000  # what was spent is still recorded

    for bad in ((-1, 0, 0), (0, -1, 0), (0, 0, -1)):
        with pytest.raises(ValueError):
            store.record_model_use(*bad)

    clock.advance(days=30)
    assert limited.model_budget_left() == 2_000_000  # a new month, a new budget


def test_usage_belongs_to_its_workspace(store, tmp_path):
    other = store.for_account(store.create_account("Brook").id)
    theirs = other.create_project("ops")
    other.add_note(theirs.id, note(1))
    other.record_model_use(5, 5, 987_654_321)
    assert store.usage().notes == 0 and store.stored_bytes() == 0
    assert store.usage().model_micros == 0
    assert (other.usage().notes, other.usage().model_micros) == (1, 987_654_321)

    assert "987654321" in dump(tmp_path / "cairn.db")
    store.delete_account(other.account_id)
    assert "987654321" not in dump(tmp_path / "cairn.db")  # its usage went with it


# Limits in the store


def test_a_limited_view_refuses_at_each_cap(store, clock):
    limited = store.limited(Limits(notes_per_month=2, storage_bytes=200, projects=1))
    project = limited.create_project("ops")
    with pytest.raises(OverLimit) as full:
        limited.create_project("more")
    assert (full.value.limit, full.value.used, full.value.cap) == ("projects", 1, 1)
    assert full.value.resets_at is None
    assert [b.name for b in store.list_projects()] == ["ops"]

    limited.add_note(project.id, note(1))
    limited.add_note(project.id, note(2))
    with pytest.raises(OverLimit) as third:
        limited.add_note(project.id, note(3))
    error = third.value
    assert (error.limit, error.used, error.cap) == ("notes_per_month", 2, 2)
    assert error.resets_at == DECEMBER
    assert len(store.list_notes(project.id)) == 2 and store.usage().notes == 2

    _, created = limited.add_note(project.id, note(1))  # already held: not a new note
    assert created is False

    clock.advance(days=30)  # the month's cap resets; storage does not
    with pytest.raises(OverLimit) as heavy:
        limited.add_note(project.id, "x" * 150)
    assert (heavy.value.limit, heavy.value.cap) == ("storage_bytes", 200)
    assert heavy.value.used == store.stored_bytes() and heavy.value.resets_at is None
    limited.add_note(project.id, "short\n")

    unlimited = store.limited(Limits())
    unlimited.add_note(project.id, "y" * 5_000)  # a cap of None is no cap


def test_parallel_writers_cannot_pass_a_cap_between_them(store):
    limited = store.limited(Limits(notes_per_month=10))
    project = limited.create_project("ops")
    outcomes: list[str] = []

    def write(n: int) -> None:
        try:
            limited.add_note(project.id, note(n))
            outcomes.append("stored")
        except OverLimit:
            outcomes.append("refused")

    threads = [threading.Thread(target=write, args=(n,)) for n in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("stored") == 10 and outcomes.count("refused") == 30
    assert store.usage().notes == 10 and len(store.list_notes(project.id)) == 10


# Plans


def test_plans_are_data_and_a_workspace_names_one(store, tmp_path):
    with pytest.raises(ValueError):
        Plans({"free": Plan("Free")}, default="solo")

    file = tmp_path / "plans.yaml"
    file.write_text(
        "default: free\n"
        "plans:\n"
        "  free:\n    label: Free\n    notes_per_month: 3\n    projects: 2\n"
        "  solo:\n    label: Solo\n    model_budget_micros: 2000000\n    requests_per_minute: 5\n"
    )
    loaded = load(file)
    assert loaded.default == "free" and set(loaded.table) == {"free", "solo"}
    assert loaded.table["free"] == Plan("Free", notes_per_month=3, projects=2)
    assert loaded.table["solo"].requests_per_minute == 5
    for broken in (
        "plans:\n  free:\n    label: Free\n",  # no default
        "default: free\nplans:\n  free:\n    label: Free\n    notes: 3\n",  # unknown key
        "default: free\nplans:\n  free:\n    label: Free\n    projects: -1\n",
        "default: gold\nplans:\n  free:\n    label: Free\n",
    ):
        file.write_text(broken)
        with pytest.raises(ValueError):
            load(file)

    brook = store.create_account("Brook")
    assert brook.plan is None
    store.set_plan(brook.id, "solo")
    assert store.get_account(brook.id).plan == "solo"
    store.set_plan(brook.id, None)
    assert store.get_account(brook.id).plan is None
    with pytest.raises(KeyError):
        store.set_plan("acct_000000000000", "solo")


# The service


def world(tmp_path, clock, **options) -> SimpleNamespace:
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    key = store.create_key("laptop")
    app = create_app(store, public_url=BASE, clock=clock, **options)
    return SimpleNamespace(store=store, db=db, key=key, app=app, headers={"x-api-key": key})


def tool(client, w, name: str, /, **arguments) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    r = client.post("/mcp", headers={**MCP_HEADERS, **w.headers}, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_at_the_cap_every_way_in_says_so_and_stores_nothing(tmp_path, clock):
    w = world(tmp_path, clock, plans=PLANS)
    with TestClient(w.app, base_url=BASE) as client:
        made = client.post("/api/projects", headers=w.headers, json={"name": "ops"})
        assert made.status_code == 201
        project = made.json()["id"]
        for n in (1, 2, 3):
            r = client.post(
                f"/api/projects/{project}/notes", headers=w.headers, json={"text": note(n)}
            )
            assert r.status_code == 201, r.text

        r = client.post(f"/api/projects/{project}/notes", headers=w.headers, json={"text": note(4)})
        assert r.status_code == 402
        body = r.json()
        assert (body["limit"], body["used"], body["cap"]) == ("notes_per_month", 3, 3)
        assert (body["resets_at"], body["plan"]) == (DECEMBER_FIRST, "free")
        assert "3" in body["error"] and "Free" in body["error"]

        result = tool(client, w, "note", project="ops", text=note(4))
        assert result["isError"] is True
        said = result["content"][0]["text"]
        assert "notes" in said and "3 of 3" in said and "2026-12-01" in said

        client.post("/login", data={"key": w.key}, headers=SAME_ORIGIN)
        typed = "A thought typed on a phone."
        page = client.post(
            "/b/ops/notes", data={"text": typed}, headers=SAME_ORIGIN, follow_redirects=False
        )
        assert page.status_code == 402 and 'role="alert"' in page.text
        assert typed in page.text and "3 of 3" in page.text

        client.post("/api/projects", headers=w.headers, json={"name": "second"})
        over = client.post("/api/projects", headers=w.headers, json={"name": "third"})
        assert over.status_code == 402 and over.json()["limit"] == "projects"
        assert over.json()["resets_at"] is None
        refused = tool(client, w, "new_project", name="third")
        assert refused["isError"] is True and "projects" in refused["content"][0]["text"]
        client.post("/logout", headers=SAME_ORIGIN)
        stored = dump(w.db)
        assert "fact number 4" not in stored and typed not in stored and "third" not in stored
        assert [b.name for b in w.store.list_projects()] == ["ops", "second"]
        assert len(w.store.list_notes(project)) == 3 and w.store.usage().notes == 3

        # The same note again is not a new note, so it is not refused.
        again = client.post(
            f"/api/projects/{project}/notes", headers=w.headers, json={"text": note(1)}
        )
        assert again.status_code in (200, 201) and again.json()["status"] == "unchanged"

        # On a bigger plan writing works again.
        clock.advance(minutes=2)  # so that plan's rate limit starts from a quiet minute
        w.store.set_plan(w.store.account_id, "solo")
        r = client.post(f"/api/projects/{project}/notes", headers=w.headers, json={"text": note(4)})
        assert r.status_code == 201
    w.store.close()


def test_a_workspace_can_see_its_usage(tmp_path, clock):
    w = world(tmp_path, clock, plans=PLANS)
    with TestClient(w.app, base_url=BASE) as client:
        project = client.post("/api/projects", headers=w.headers, json={"name": "ops"}).json()["id"]
        client.post(f"/api/projects/{project}/notes", headers=w.headers, json={"text": note(1)})
        w.store.record_model_use(100, 200, 30_000)

        before = dump(w.db)
        usage = client.get("/api/usage", headers=w.headers)
        assert usage.status_code == 200
        body = usage.json()
        assert (body["plan"], body["label"], body["period"]) == ("free", "Free", "2026-11")
        assert body["resets_at"] == DECEMBER_FIRST
        assert body["limits"] == {
            "notes_per_month": {"used": 1, "cap": 3},
            "storage_bytes": {"used": len(note(1).encode()), "cap": 400},
            "projects": {"used": 1, "cap": 2},
            "model_budget_micros": {"used": 30_000, "cap": 0},
        }
        assert dump(w.db) == before  # reading usage writes nothing
        assert client.get("/api/usage").status_code == 401

        client.post("/login", data={"key": w.key}, headers=SAME_ORIGIN)
        signed_in = dump(w.db)
        page = client.get("/usage")
        assert page.status_code == 200 and "Free" in page.text
        assert re.search(r"1\s+of\s+3", page.text) and "1 December 2026" in page.text
        assert dump(w.db) == signed_in
        client.post("/logout", headers=SAME_ORIGIN)
    w.store.close()


def test_a_self_hosted_install_has_no_caps(tmp_path, clock):
    w = world(tmp_path, clock)  # no plans
    with TestClient(w.app, base_url=BASE) as client:
        project = client.post("/api/projects", headers=w.headers, json={"name": "ops"}).json()["id"]
        for n in range(12):
            r = client.post(
                f"/api/projects/{project}/notes", headers=w.headers, json={"text": note(n)}
            )
            assert r.status_code == 201
        for _ in range(80):
            assert client.get("/api/projects", headers=w.headers).status_code == 200
        body = client.get("/api/usage", headers=w.headers).json()
        assert (body["plan"], body["label"]) == (None, None)
        assert body["limits"]["notes_per_month"] == {"used": 12, "cap": None}
        assert all(entry["cap"] is None for entry in body["limits"].values())
    w.store.close()


def test_a_workspace_that_asks_too_fast_is_told_to_wait(tmp_path, clock):
    w = world(tmp_path, clock, plans=PLANS)
    w.store.set_plan(w.store.account_id, "solo")  # five requests a minute
    quiet = w.store.for_account(w.store.create_account("Brook").id)
    quiet_key = quiet.create_key("brook")
    with TestClient(w.app, base_url=BASE) as client:
        for _ in range(5):
            assert client.get("/api/projects", headers=w.headers).status_code == 200
        before = dump(w.db)
        slow = client.get("/api/projects", headers=w.headers)
        assert slow.status_code == 429
        assert 1 <= int(slow.headers["retry-after"]) <= 60
        refused = client.post("/api/projects", headers=w.headers, json={"name": "late"})
        assert refused.status_code == 429
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        assert (
            client.post("/mcp", headers={**MCP_HEADERS, **w.headers}, json=body).status_code == 429
        )
        assert dump(w.db) == before  # nothing was stored, and counting wrote nothing

        # Another workspace, people without a credential and the health check are unaffected.
        assert client.get("/api/projects", headers={"x-api-key": quiet_key}).status_code == 200
        for _ in range(20):
            assert client.get("/healthz").status_code == 200
            assert client.get("/api/projects").status_code == 401

        clock.advance(seconds=61)
        assert client.get("/api/projects", headers=w.headers).status_code == 200
    w.store.close()


def test_the_operator_sets_plans_from_the_command_line(tmp_path, clock, capsys):
    from cairn.server import main

    db = tmp_path / "cairn.db"
    store = Store(db)
    brook = store.create_account("Brook")
    view = store.for_account(brook.id)
    view.add_note(view.create_project("ops").id, note(1))
    store.close()

    assert main(["plan-set", "--db", str(db), "--account", brook.id, "--plan", "solo"]) == 0
    capsys.readouterr()
    assert main(["usage", "--db", str(db), "--account", brook.id]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["plan"] == "solo" and printed["notes"] == 1
    assert printed["note_bytes"] == len(note(1).encode())

    assert main(["plan-set", "--db", str(db), "--account", brook.id, "--default"]) == 0
    reopened = Store(db)
    try:
        assert reopened.get_account(brook.id).plan is None
    finally:
        reopened.close()
    unknown = ["plan-set", "--db", str(db), "--account", "acct_000000000000", "--plan", "x"]
    assert main(unknown) == 1
