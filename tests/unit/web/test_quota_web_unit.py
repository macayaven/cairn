"""Unit tests for plans in the web service (H6): what tests/acceptance/test_h6_quotas.py
leaves open about the 402 answers, who reads usage, and the rate limit."""

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn.plans import Plan, Plans
from cairn.server import create_app, main
from cairn.store import Store
from cairn.web.ratelimit import RateLimiter
from support.cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
NOVEMBER = datetime(2026, 11, 10, 12, tzinfo=UTC)
PLANS = Plans(
    {
        "tiny": Plan(
            "Tiny", notes_per_month=1, storage_bytes=80, projects=1, requests_per_minute=40
        ),
        "open": Plan("Open"),
    },
    default="tiny",
)
SECRET_ISH = "the private thought nobody else should read"


class Clock:
    def __init__(self) -> None:
        self.now = NOVEMBER

    def __call__(self) -> datetime:
        return self.now


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    key = store.create_key("laptop")
    project = store.create_project("ops")
    app = create_app(store, public_url=BASE, plans=PLANS, clock=clock)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(
            client=client, store=store, db=db, key=key, project=project, clock=clock,
            headers={"x-api-key": key},
        )  # fmt: skip
    store.close()


def rest_note(env, text: str):
    return env.client.post(
        f"/api/projects/{env.project.id}/notes", headers=env.headers, json={"text": text}
    )


def access_records(caplog) -> list[tuple]:
    return [
        (r.actor_kind, r.actor, r.actor_id, r.action, r.project, r.status)
        for r in caplog.records
        if r.name == "cairn.access"
    ]


def sign_in_with_key(env) -> None:
    r = env.client.post(
        "/login", data={"key": env.key}, headers=SAME_ORIGIN, follow_redirects=False
    )
    assert r.status_code == 303


# 402


def test_a_refusal_answers_its_numbers_and_holds_no_note_text(env, caplog):
    assert rest_note(env, "# First\n\nOne.\n").status_code == 201
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        r = rest_note(env, f"# Second\n\n{SECRET_ISH}.\n")
    assert r.status_code == 402
    body = r.json()
    assert set(body) == {"error", "limit", "used", "cap", "resets_at", "plan"}
    assert body["error"].endswith(".") and "Tiny" in body["error"]
    assert "1 December 2026" in body["error"] and "1 of 1" in body["error"]
    assert SECRET_ISH not in r.text and SECRET_ISH not in dump(env.db)
    assert access_records(caplog)[-1][3:] == ("note", "ops", 402)
    assert SECRET_ISH not in repr([sorted(r.__dict__.items()) for r in caplog.records])
    assert all(event.action != "note.refused" for event in env.store.list_audit())


def test_a_note_too_big_for_the_storage_left_says_so(env):
    r = rest_note(env, "x" * 81)
    assert r.status_code == 402
    body = r.json()
    assert (body["limit"], body["used"], body["cap"], body["resets_at"]) == (
        "storage_bytes",
        0,
        80,
        None,
    )
    assert "bytes" in body["error"] and "0 of 80" in body["error"]


def test_a_project_over_the_cap_says_so_on_the_home_page_with_what_was_typed(env):
    sign_in_with_key(env)
    r = env.client.post(
        "/projects", data={"name": "second", "description": "typed"}, headers=SAME_ORIGIN
    )
    assert r.status_code == 402 and 'role="alert"' in r.text
    assert 'value="second"' in r.text and "1 of 1" in r.text
    assert [b.name for b in env.store.list_projects()] == ["ops"]


def test_the_guided_setup_over_the_cap_makes_nothing(env):
    sign_in_with_key(env)
    before = dump(env.db)
    r = env.client.post(
        "/start",
        data={"use": ["repo", "team"], "name-repo": "code", "name-team": "runbooks"},
        headers=SAME_ORIGIN,
        follow_redirects=False,
    )
    assert r.status_code == 402 and 'role="alert"' in r.text and "projects" in r.text
    assert dump(env.db) == before


def test_the_quick_composer_at_the_cap_keeps_what_was_typed(env):
    assert rest_note(env, "# First\n\nOne.\n").status_code == 201
    sign_in_with_key(env)
    r = env.client.post(
        "/notes", data={"project": "ops", "text": "typed on a phone"}, headers=SAME_ORIGIN
    )
    assert r.status_code == 402 and 'role="alert"' in r.text
    assert "typed on a phone" in r.text and "1 of 1" in r.text


def test_a_refusal_in_a_web_form_is_one_line_on_the_access_log(env, caplog):
    assert rest_note(env, "# First\n\nOne.\n").status_code == 201
    sign_in_with_key(env)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        for path, data in (
            ("/notes", {"project": "ops", "text": f"# Picked\n\n{SECRET_ISH}.\n"}),
            ("/b/ops/notes", {"text": f"# Composed\n\n{SECRET_ISH}.\n"}),
            ("/projects", {"name": "second"}),
            ("/start", {"use": ["repo"], "name-repo": "code"}),
        ):
            assert env.client.post(path, data=data, headers=SAME_ORIGIN).status_code == 402
    records = access_records(caplog)
    assert [record[3:] for record in records] == [
        ("note", "ops", 402),
        ("note", "ops", 402),
        ("new_project", None, 402),
        ("start", None, 402),
    ]
    assert all(record[:2] == ("session", "laptop") for record in records)
    assert SECRET_ISH not in repr([sorted(r.__dict__.items()) for r in caplog.records])


def test_a_web_form_that_writes_adds_no_access_line(env, caplog):
    env.store.set_plan(env.store.account_id, "open")
    sign_in_with_key(env)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        r = env.client.post("/notes", data={"project": "ops", "text": "# A\n\nB.\n"},
                            headers=SAME_ORIGIN, follow_redirects=False)  # fmt: skip
    assert r.status_code == 303 and access_records(caplog) == []  # as before H6


# Who reads usage


@pytest.mark.parametrize("reach", ["projects", "read"])
def test_a_connection_that_reaches_less_than_the_workspace_reads_no_usage(env, reach):
    narrowed = (
        env.store.create_key("narrow", projects=(env.project.id,))
        if reach == "projects"
        else env.store.create_key("reader", access="read")
    )
    assert env.client.get("/api/usage", headers={"x-api-key": narrowed}).status_code == 403
    assert env.client.get("/api/usage", headers=env.headers).status_code == 200


def test_an_owner_sees_the_usage_page_and_a_member_does_not(env):
    """H6c: the totals count every project, so a member, who reaches projects one by one, gets
    403 (it was 200 before H6c)."""
    signed = [
        env.store.sign_in(
            "github", str(n), f"person{n}@example.test", name, issuer="https://github.com",
            join="default",
        )
        for n, name in enumerate(("Olive Owner", "Max Member"))
    ]  # fmt: skip
    owner, member = signed
    assert env.store.for_account(member.account_id, member.user.id).role() == "member"
    env.client.cookies.set(session_cookie(), owner.token)
    page = env.client.get("/usage")
    assert page.status_code == 200 and "Tiny" in page.text
    env.client.cookies.set(session_cookie(), member.token)
    assert env.client.get("/usage").status_code == 403


def test_the_usage_page_says_amounts_in_words(env):
    sign_in_with_key(env)
    page = env.client.get("/usage").text
    rows = re.sub(r"<[^>]+>", " ", page.split("<tbody>", 1)[1].split("</tbody>", 1)[0])
    rows = " ".join(rows.split())
    assert "Notes written this month 0 of 1" in rows
    assert "Notes stored 0 bytes of 80 bytes" in rows
    assert "Projects 1 of 1" in rows
    assert "Model use this month $0.00, no cap" in rows
    assert "millionths" not in page and "Bytes of notes" not in page


# 429


def test_a_request_over_the_rate_is_one_access_record_and_says_when_to_come_back(env, caplog):
    for _ in range(40):
        assert env.client.get("/api/projects", headers=env.headers).status_code == 200
    env.clock.now += timedelta(seconds=15)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        r = env.client.get("/api/projects", headers=env.headers)
    assert r.status_code == 429 and r.headers["retry-after"] == "45"
    assert "error" in r.json()
    (record,) = access_records(caplog)
    assert record[0:2] == ("key", "laptop") and record[3:] == ("rate_limited", None, 429)


def test_a_web_page_over_the_rate_is_told_to_wait_too(env):
    sign_in_with_key(env)
    for _ in range(40):
        assert env.client.get("/usage").status_code == 200
    r = env.client.get("/")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_a_plan_without_a_rate_is_never_told_to_wait(env):
    env.store.set_plan(env.store.account_id, "open")
    for _ in range(60):
        assert env.client.get("/api/projects", headers=env.headers).status_code == 200


# The limiter


def test_the_limiter_counts_whole_seconds_in_a_sliding_minute():
    clock = Clock()
    limiter = RateLimiter(clock)
    assert [limiter.take("a", 3) for _ in range(2)] == [0, 0]
    clock.now += timedelta(seconds=10)
    assert limiter.take("a", 3) == 0
    clock.now += timedelta(seconds=10)
    assert limiter.take("a", 3) == 40  # the first two leave the window at 60 s
    assert limiter.take("b", 3) == 0  # each workspace has its own count
    clock.now += timedelta(seconds=40)
    assert limiter.take("a", 3) == 0


def test_a_connection_narrowed_to_some_projects_learns_no_numbers_from_a_402(env):
    """It is refused /api/usage, so its 402 says which limit and nothing of the workspace's
    counts, cap, reset day or plan."""
    narrow = env.store.create_key("one-project", projects=[env.project.id])
    headers = {"x-api-key": narrow}
    assert env.client.get("/api/usage", headers=headers).status_code == 403
    assert rest_note(env, "# First\n\nOne.\n").status_code == 201  # the whole-workspace key
    r = env.client.post(
        f"/api/projects/{env.project.id}/notes", headers=headers, json={"text": "# Two\n\nTwo.\n"}
    )
    assert r.status_code == 402
    body = r.json()
    assert set(body) == {"error", "limit"} and body["limit"] == "notes_per_month"
    assert not any(ch.isdigit() for ch in body["error"]) and "Tiny" not in body["error"]
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "note", "arguments": {"project": "ops", "text": "# Two\n\nTwo.\n"}},
    }
    result = env.client.post("/mcp", headers={**MCP_HEADERS, **headers}, json=call).json()
    said = result["result"]["content"][0]["text"]
    assert result["result"]["isError"] is True and "notes" in said
    assert not any(ch.isdigit() for ch in said) and "Tiny" not in said


def test_a_downgrade_after_a_request_was_admitted_holds_its_write(env, monkeypatch):
    """The request is admitted on the open plan; the operator moves the workspace to the
    tiny one before the request writes: the write reads the plan in force and is refused,
    and the 402 names the plan it was refused on."""
    env.store.set_plan(env.store.account_id, "open")
    assert rest_note(env, "# First\n\nOne.\n").status_code == 201
    admitted = Plans.in_force

    def admit_then_downgrade(self, store, account_id):
        plan = admitted(self, store, account_id)
        env.store.set_plan(account_id, "tiny")  # after admission, before the write
        return plan

    monkeypatch.setattr(Plans, "in_force", admit_then_downgrade)
    r = rest_note(env, "# Second\n\nTwo.\n")
    assert r.status_code == 402
    body = r.json()
    assert (body["limit"], body["used"], body["cap"], body["plan"]) == (
        "notes_per_month",
        1,
        1,
        "tiny",
    )
    assert "Tiny" in body["error"]
    assert len(env.store.list_notes(env.project.id)) == 1


def test_an_upgrade_after_a_request_was_admitted_lets_its_write_through(env, monkeypatch):
    """Admitted on the tiny plan at its cap, upgraded before the write: the caps are the
    plan in force's alone, so the write goes ahead (no 402 naming one plan with another's
    cap)."""
    assert rest_note(env, "# First\n\nOne.\n").status_code == 201  # tiny: 1 of 1
    admitted = Plans.in_force

    def admit_then_upgrade(self, store, account_id):
        plan = admitted(self, store, account_id)
        env.store.set_plan(account_id, "open")  # after admission, before the write
        return plan

    monkeypatch.setattr(Plans, "in_force", admit_then_upgrade)
    assert rest_note(env, "# Second\n\nTwo.\n").status_code == 201


def test_the_limiters_memory_is_bounded():
    clock = Clock()
    limiter = RateLimiter(clock, tracked=5)
    for second in range(200):  # a request every second: at most a minute of entries
        clock.now = NOVEMBER + timedelta(seconds=second)
        limiter.take("busy", 1_000)
    assert limiter.entries("busy") <= 60
    for n in range(20):
        limiter.take(f"acct_{n}", 1)
    assert limiter.tracked() <= 5


def at(clock: Clock, second: int) -> None:
    clock.now = NOVEMBER + timedelta(seconds=second)


def test_a_full_limiter_forgets_a_quiet_workspace_before_a_live_count():
    """Y filled its window at 0 and was refused at 55, so it is the most recently seen,
    though its window is empty by 61; X counted at 50 is less recent but live. A new
    workspace at 61 drops Y, not X: X's count is not forgotten."""
    clock = Clock()
    limiter = RateLimiter(clock, tracked=2)
    at(clock, 0)
    for _ in range(3):
        assert limiter.take("Y", 3) == 0
    at(clock, 50)
    for _ in range(3):
        assert limiter.take("X", 3) == 0
    at(clock, 55)
    assert limiter.take("Y", 3) > 0  # refused: seen now, counted at 0
    at(clock, 61)
    assert limiter.take("Z", 3) == 0
    assert limiter.tracked() == 2 and limiter.entries("Y") == 0
    assert limiter.take("X", 3) > 0  # its three at 50 still count


def test_a_full_limiter_of_live_counts_still_lets_a_new_workspace_in():
    clock = Clock()
    limiter = RateLimiter(clock, tracked=2)
    for second, name in enumerate(("A", "B")):
        at(clock, second)
        assert limiter.take(name, 1) == 0
    at(clock, 2)
    assert limiter.take("C", 1) == 0  # never fails closed: the least recently seen goes
    assert limiter.tracked() == 2 and limiter.entries("A") == 0


def test_a_clock_that_steps_back_does_not_grow_the_limiter():
    clock = Clock()
    limiter = RateLimiter(clock)
    for second in range(120):
        clock.now = NOVEMBER - timedelta(seconds=second)  # ever earlier
        limiter.take("a", 10_000)
    assert limiter.entries("a") <= 60


# The command line


def test_serve_with_a_plans_file_caps_the_workspaces(tmp_path, monkeypatch):
    plans = tmp_path / "plans.yaml"
    plans.write_text("default: one\nplans:\n  one: {label: One, notes_per_month: 1}\n")
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("laptop")
    project = store.create_project("ops").id
    store.close()
    seen = []

    def run(app, **options):
        with TestClient(app, base_url=BASE) as client:
            for n in range(2):
                seen.append(
                    client.post(
                        f"/api/projects/{project}/notes",
                        headers={"x-api-key": key},
                        json={"text": f"# N{n}\n\nOne.\n"},
                    ).status_code
                )

    monkeypatch.setattr(uvicorn, "run", run)
    assert main(["serve", "--db", str(db), "--plans", str(plans)]) == 0
    assert seen == [201, 402]


@pytest.mark.parametrize("content", [None, "plans: {}\n", "default: x\nplans:\n  y: {label: Y}\n"])
def test_serve_with_a_bad_plans_file_says_why_and_exits_2(tmp_path, capsys, content):
    plans = tmp_path / "plans.yaml"
    if content is not None:
        plans.write_text(content)
    db = tmp_path / "cairn.db"
    assert main(["serve", "--db", str(db), "--plans", str(plans)]) == 2
    assert "cannot use those plans" in capsys.readouterr().err
    assert not db.exists()  # nothing opened before the plans were read


def test_plan_commands_refuse_a_missing_database_and_a_bad_name(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    assert main(["plan-set", "--db", str(db), "--account", "acct_x", "--plan", "solo"]) == 1
    assert main(["usage", "--db", str(db), "--account", "acct_x"]) == 1
    assert not db.exists()
    store = Store(db)
    account = store.account_id
    store.close()
    assert main(["plan-set", "--db", str(db), "--account", account, "--plan", " solo"]) == 1
    assert main(["usage", "--db", str(db), "--account", "acct_000000000000"]) == 1
    capsys.readouterr()
    assert main(["usage", "--db", str(db), "--account", account]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert set(printed) == {
        "account", "plan", "period", "notes", "note_bytes", "model_tokens_in",
        "model_tokens_out", "model_micros", "stored_bytes", "passages", "passages_cap",
        "reservations",
    }  # fmt: skip
    assert printed["plan"] is None and printed["notes"] == 0
    assert printed["passages"] == 0 and printed["passages_cap"] is None  # no plans: no cap


def test_usage_prints_the_passages_and_the_cap_of_the_plan_in_force(tmp_path, capsys):
    plans = tmp_path / "plans.yaml"
    plans.write_text(
        "default: one\nplans:\n  one: {label: One, notes_per_month: 10, storage_bytes: 4000}\n"
        "  two: {label: Two, notes_per_month: 10, storage_bytes: 8000}\n  open: {label: Open}\n"
    )
    db = tmp_path / "cairn.db"
    store = Store(db)
    account = store.account_id
    store.add_note(store.create_project("ops").id, "- one\n- two\n- three\n")
    store.close()

    def usage(*more: str) -> dict:
        assert main(["usage", "--db", str(db), "--account", account, *more]) == 0
        return json.loads(capsys.readouterr().out)

    assert (usage("--plans", str(plans))["passages"], usage()["passages"]) == (3, 3)
    assert usage("--plans", str(plans))["passages_cap"] == 100  # 4,000 bytes, one per 40
    assert main(["plan-set", "--db", str(db), "--account", account, "--plan", "two"]) == 0
    capsys.readouterr()
    assert usage("--plans", str(plans))["passages_cap"] == 200
    assert main(["plan-set", "--db", str(db), "--account", account, "--plan", "open"]) == 0
    capsys.readouterr()
    assert usage("--plans", str(plans))["passages_cap"] is None  # no storage cap, no cap
    assert usage()["passages_cap"] is None


def test_usage_refuses_plans_it_cannot_read(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    store = Store(db)
    account = store.account_id
    store.close()
    missing = tmp_path / "none.yaml"
    assert main(["usage", "--db", str(db), "--account", account, "--plans", str(missing)]) == 2
    assert "cannot use those plans" in capsys.readouterr().err


# workspace-list (H10): what tests/acceptance/test_h10_workspace_list.py leaves open


def workspace_list(db: Path, capsys) -> list[dict]:
    capsys.readouterr()
    assert main(["workspace-list", "--db", str(db)]) == 0
    return json.loads(capsys.readouterr().out)


def test_workspace_list_is_in_the_top_help_and_its_help_says_what_each_field_is(capsys):
    with pytest.raises(SystemExit) as shown:
        main(["--help"])
    assert shown.value.code == 0
    top = capsys.readouterr().out
    assert re.search(r"^\s+workspace-list\s+\S", top, re.MULTILINE)  # listed, with a line
    with pytest.raises(SystemExit) as shown:
        main(["workspace-list", "--help"])
    assert shown.value.code == 0
    said = " ".join(capsys.readouterr().out.split())
    for sentence in (
        "account: the workspace's id, which plan-set and usage take.",
        "name: the workspace's name.",
        "created_at: when it was made (UTC).",
        "members: how many people belong to it.",
        "plan: the plan it is held to now, the one plan-set recorded; null: the server's default.",
        "It reads no plans file and changes nothing.",
    ):
        assert sentence in said, sentence


def test_workspace_list_refuses_an_older_database_as_every_command_but_serve_does(tmp_path, capsys):
    """In one line on stderr, exit 1, the file as it was: only `serve` upgrades
    (test_only_serve_upgrades_unit.py)."""
    from support.store.store import empty_v0_database

    db = tmp_path / "cairn.db"
    empty_v0_database(db)
    before = dump(db)
    assert main(["workspace-list", "--db", str(db)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err.count("\n") == 1 and "start the server once on it" in err
    assert "workspace-list changed nothing" in err and dump(db) == before


def test_workspace_list_refuses_a_newer_database_as_usage_does(tmp_path, capsys):
    """In one line on stderr, exit 1 (every command: test_newer_database_cli_unit.py)."""
    from cairn.store import SCHEMA_VERSION

    db = tmp_path / "cairn.db"
    Store(db).close()
    con = sqlite3.connect(db)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    # a step this cairn may not run on: the floor goes up with it
    con.execute("UPDATE oldest_build SET version = ?", (SCHEMA_VERSION + 1,))
    con.commit()
    con.close()
    before = dump(db)
    for argv in (["usage", "--account", "acct_default"], ["workspace-list"]):
        assert main([argv[0], "--db", str(db), *argv[1:]]) == 1, argv
        out, err = capsys.readouterr()
        assert out == "" and err.count("\n") == 1 and err.startswith("SchemaTooNew: "), argv
        assert dump(db) == before, argv


def test_workspace_list_is_json_for_one_workspace_and_for_any_name(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    Store(db).close()
    (only,) = workspace_list(db, capsys)  # a list even with only the default workspace
    assert only["account"] == "acct_default" and only["members"] == 0
    store = Store(db)
    named = 'Zoë\'s "quoted" \\ wörkspace ☃'
    made = store.create_account(named)
    store.close()
    rows = workspace_list(db, capsys)
    assert [row["name"] for row in rows if row["account"] == made.id] == [named]


def test_workspace_list_prints_a_plan_no_plans_file_names(tmp_path, capsys):
    """The command reads no plans file: the name recorded is the name printed, whether or
    not the running server's plans still list it."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    made = store.create_account("Old customer")
    store.close()
    argv = ["plan-set", "--db", str(db), "--account", made.id, "--plan", "legacy-2024"]
    assert main(argv) == 0
    rows = {row["account"]: row for row in workspace_list(db, capsys)}
    assert rows[made.id]["plan"] == "legacy-2024" and "subscription" not in rows[made.id]
