"""Unit tests for `arrange` and `serve --model-env` on the command line (T4b). The model is an
httpx.MockTransport behind the command line's model options; nothing leaves the process."""

import asyncio
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

import httpx
import pytest
import uvicorn

import cairn.arrange
import cairn.plans
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import FakeModel, Reply
from cairn.plans import Plan, Plans
from cairn.store import Store
from cairn.web import cli, cli_arrange

KEY = "sk-arrange-cli-do-not-leak"
ENV = {"CAIRN_T_BASE_URL": "http://127.0.0.1:9/v1", "CAIRN_T_API_KEY": KEY, "CAIRN_T_MODEL": "m"}
FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "cairn.db"
    store = Store(path)
    project = store.create_project("alpha")
    for text in (FIRST, SECOND, "# Short\n\nOne line.\n"):
        note, _ = store.add_note(project.id, text)
        compile_note(store, note, gate=lint_gate)
    store.close()
    return path


@pytest.fixture
def model(monkeypatch):
    """The endpoint: answers with the page's claim ids reversed, and records each request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        user = json.loads(request.content)["messages"][1]["content"]
        ids = [part.split('"', 1)[0] for part in user.split(' id="')[1:]]
        answer = {"order": ids[::-1], "same": []}
        message = {"role": "assistant", "content": json.dumps(answer)}
        usage = {"prompt_tokens": 300, "completion_tokens": 40}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": usage})

    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli_arrange, "_model_options", {"transport": httpx.MockTransport(handler)})
    return seen


def test_arrange_makes_one_pass_and_prints_a_line_a_page(db, model, capsys):
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_T"]
    assert cli.main([*argv, "--model-price", "1,5"]) == 0
    out, err = capsys.readouterr()
    lines = sorted(out.splitlines())
    assert lines == [
        "alpha/drift-sync: arranged, version 2 -> 3, tokens 300/40",
        "alpha/short: small, version 1",
    ]
    assert len(model) == 1 and model[0].headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in out + err and "Drift" not in out + err
    store = Store(db)
    try:
        used = store.usage()
        assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (300, 40, 500)
    finally:
        store.close()
    assert cli.main(argv) == 0  # a pass of its own: the reversed page is reversed back
    assert "alpha/drift-sync: arranged, version 3 -> 4, tokens 300/40" in capsys.readouterr().out


def test_arrange_without_a_price_spends_no_budget_and_still_records_tokens(
    db, model, tmp_path, capsys
):
    plans = tmp_path / "plans.yaml"
    plans.write_text("default: some\nplans:\n  some: {label: Some, model_budget_micros: 1000}\n")
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_T"]
    assert cli.main([*argv, "--plans", str(plans)]) == 0
    assert "alpha/drift-sync: arranged, version 2 -> 3, tokens 300/40" in capsys.readouterr().out
    store = Store(db)
    try:
        used = store.usage()
        assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (300, 40, 0)
        view = cli_arrange.worker_views(store, cairn.plans.load(plans))("acct_default")
        assert view.model_budget_left() == 1000 and store.model_reservations() == []
    finally:
        store.close()


def test_arrange_without_a_model_exits_2_and_asks_nothing(db, model, capsys):
    with pytest.raises(SystemExit) as refused:
        cli.main(["arrange", "--db", str(db), "--account", "acct_default"])
    assert refused.value.code == 2 and model == []
    assert "--model-env" in capsys.readouterr().err


def test_a_page_never_read_is_printed_without_a_version(db, model, monkeypatch, capsys):
    """ "<project>/<slug>: <status>" alone: a workspace gone, or arranging that raised."""
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_T"]
    views = cli_arrange.worker_views

    def gone(store, plans):
        def view_for(account_id):
            raise KeyError(account_id)

        return view_for

    monkeypatch.setattr(cli_arrange, "worker_views", gone)
    assert cli.main(argv) == 0
    assert sorted(capsys.readouterr().out.splitlines()) == ["alpha/drift-sync: gone",
                                                             "alpha/short: gone"]  # fmt: skip

    def broken(store, plans):
        def view_for(account_id):
            raise RuntimeError("the view broke")

        return view_for

    monkeypatch.setattr(cli_arrange, "worker_views", broken)
    assert cli.main(argv) == 1
    out, err = capsys.readouterr()
    assert sorted(out.splitlines()) == ["alpha/drift-sync: failed", "alpha/short: failed"]
    assert "arrange stopped" not in err and "the view broke" not in out + err
    monkeypatch.setattr(cli_arrange, "worker_views", views)
    assert model == []


def test_a_workspace_with_no_budget_left_prints_its_pages_without_a_version(
    db, model, tmp_path, capsys
):
    plans = tmp_path / "plans.yaml"
    plans.write_text(PLANS)  # the default plan: no model budget
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_T"]
    assert cli.main([*argv, "--plans", str(plans)]) == 0
    assert sorted(capsys.readouterr().out.splitlines()) == [
        "alpha/drift-sync: no budget",
        "alpha/short: no budget",
    ]
    assert model == []


def test_a_page_whose_call_failed_exits_1_and_says_failed(db, monkeypatch, capsys):
    """The model answers 500 to every try: the page is "failed", with the version read and
    the tokens the model never reported, and the command exits 1. It is not the pause: one
    failure in a row is not five."""
    asked: list[httpx.Request] = []

    def down(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(500, json={"error": "down"})

    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    options = {"transport": httpx.MockTransport(down), "sleep": lambda seconds: None}
    monkeypatch.setattr(cli_arrange, "_model_options", options)
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_T"]
    assert cli.main(argv) == 1
    out, err = capsys.readouterr()
    assert sorted(out.splitlines()) == [
        "alpha/drift-sync: failed, version 2, tokens not reported",
        "alpha/short: small, version 1",
    ]
    assert asked and "arrange stopped" not in err and KEY not in out + err


def test_arrange_takes_one_project_and_refuses_what_it_cannot_find(db, model, capsys):
    argv = ["arrange", "--db", str(db), "--model-env", "CAIRN_T"]
    assert cli.main([*argv, "--account", "acct_default", "--project", "beta"]) == 1
    assert "no project 'beta'" in capsys.readouterr().err
    assert cli.main([*argv, "--account", "acct_nobody"]) == 1
    assert "no workspace 'acct_nobody'" in capsys.readouterr().err
    assert cli.main(["arrange", "--db", str(db.with_name("none.db")), "--account", "a",
                     "--model-env", "CAIRN_T"]) == 1  # fmt: skip
    assert model == []


def test_a_model_that_is_not_configured_exits_2_naming_the_setting(db, monkeypatch, capsys):
    monkeypatch.delenv("CAIRN_U_BASE_URL", raising=False)
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_U"]
    assert cli.main(argv) == 2
    assert "CAIRN_U_BASE_URL" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["1", "a,b", "-1,2", "1,2,3", "nan,1"])
def test_a_price_is_two_numbers_of_dollars_per_million_tokens(bad):
    with pytest.raises(SystemExit):
        cli.main(["arrange", "--db", "x", "--account", "a", "--model-env", "P",
                  "--model-price", bad])  # fmt: skip
    price = cli_arrange.price("0.15,0.6")
    assert (price.input_per_million, price.output_per_million) == (150_000, 600_000)


def test_arranging_has_its_own_deadline_of_five_minutes_unless_told(db, model, monkeypatch):
    """Measured on a local 27B model when answers were in claim ids: 0.78 s a claim, so the
    largest page (300 claims) took four minutes. A call cut off at its deadline is paid at its
    reservation and saves nothing, so the default has to cover the largest page cairn sends."""
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    serve = ["serve", "--db", str(db), "--model-env", "CAIRN_T"]
    assert cli.main(serve) == 0
    assert cli.main([*serve, "--model-deadline", "5"]) == 0
    assert [app.state.arranger.model.deadline for app in apps] == [300.0, 5.0]
    for bad in ("0", "-1", "soon", "nan", "inf"):
        with pytest.raises(SystemExit):
            cli.main([*serve, "--model-deadline", bad])
    built = []
    monkeypatch.setattr(
        cairn.arrange, "Arranger", lambda model, **options: built.append(model) or _Empty()
    )
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_T"]
    assert cli.main([*argv, "--model-deadline", "7.5"]) == 0
    assert [m.deadline for m in built] == [7.5]


class _Empty:
    unusable = 0

    def queue(self, *item):
        pass

    async def run_once(self, limit=10):
        return []

    def paused(self):
        return False


def test_arrange_says_it_stopped_when_the_pause_begins_on_its_last_page(
    tmp_path, monkeypatch, capsys
):
    """The pass ran into the pause even when no page was left: the model's answers could not
    be used, five in a row, so the command says so and exits 1."""
    path = tmp_path / "cairn.db"
    store = Store(path)
    project = store.create_project("alpha")
    for n in range(5):
        lines = "".join(f"- Row {k} of page {n} stays put.\n" for k in range(7))
        note, _ = store.add_note(project.id, f"# Page {n}\n\n{lines}")
        compile_note(store, note, gate=lint_gate)
    store.close()

    def prose(request: httpx.Request) -> httpx.Response:
        message = {"role": "assistant", "content": "A better order, in words."}
        usage = {"prompt_tokens": 10, "completion_tokens": 5}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": usage})

    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli_arrange, "_model_options", {"transport": httpx.MockTransport(prose)})
    argv = ["arrange", "--db", str(path), "--account", "acct_default", "--model-env", "CAIRN_T"]
    assert cli.main(argv) == 1
    out, err = capsys.readouterr()
    assert sorted(out.splitlines()) == [
        f"alpha/page-{n}: invalid, version 1, tokens 20/10" for n in range(5)
    ]
    assert err.splitlines() == [
        "arrange stopped: 5 answers in a row from 'm' could not be used,"
        " so 0 pages were not asked about"
    ]
    assert KEY not in out + err and "Row" not in out + err


def test_serve_refuses_a_model_reached_over_plain_http_off_this_machine(db, monkeypatch, capsys):
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    for name, value in {**ENV, "CAIRN_T_BASE_URL": "http://10.0.0.5:8400/v1"}.items():
        monkeypatch.setenv(name, value)
    assert cli.main(["serve", "--db", str(db), "--model-env", "CAIRN_T"]) == 2
    said = capsys.readouterr().err
    assert "CAIRN_T_BASE_URL" in said and "https" in said and "localhost" in said
    assert "10.0.0.5" not in said and KEY not in said and apps == []


def test_serve_runs_the_models_worker_only_with_model_env(db, model, monkeypatch, capsys):
    """With --model-env the model's worker runs in place of the tidy one, --no-tidy or not;
    without it, --model-price still exits 2 whether the tidy is on or off."""
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    for off in ([], ["--no-tidy"]):
        assert cli.main(["serve", "--db", str(db), *off, "--model-price", "1,5"]) == 2
        assert "--model-price needs --model-env" in capsys.readouterr().err
    assert cli.main(["serve", "--db", str(db), "--model-env", "CAIRN_U"]) == 2
    assert "CAIRN_U_BASE_URL" in capsys.readouterr().err
    assert cli.main(["serve", "--db", str(db)]) == 0
    assert cli.main(["serve", "--db", str(db), "--model-env", "CAIRN_T"]) == 0
    assert cli.main(["serve", "--db", str(db), "--no-tidy", "--model-env", "CAIRN_T"]) == 0
    assert apps[0].state.arranger.model is None  # the default: the worker without a model
    for app in apps[1:]:
        arranger = app.state.arranger
        assert arranger.every and arranger.model.name == "m" and arranger.price is None
    assert KEY not in capsys.readouterr().err


def test_the_worker_acts_through_a_view_limited_by_the_workspace_plan(db):
    plans = Plans(default="tiny", table={"tiny": Plan(label="Tiny", model_budget_micros=7)})
    store = Store(db)
    try:
        view = cli_arrange.worker_views(store, plans)("acct_default")
        assert view.model_budget_left() == 7
        assert cli_arrange.worker_views(store, None)("acct_default").model_budget_left() is None
        with pytest.raises(KeyError):
            cli_arrange.worker_views(store, plans)("acct_gone")
    finally:
        store.close()


NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)


@pytest.mark.parametrize(("operator", "budget"), [(None, 7), ("solo", 900)])
def test_the_worker_spends_the_budget_of_the_plan_in_force(db, monkeypatch, operator, budget):
    """A workspace the operator put on a plan gets its model budget; any other the default's."""
    plans = Plans(
        default="tiny",
        table={"tiny": Plan("Tiny", model_budget_micros=7),
               "solo": Plan("Solo", model_budget_micros=900)},
    )  # fmt: skip
    asked = []
    resolve = Plans.in_force
    monkeypatch.setattr(Plans, "in_force", lambda self, *a: asked.append(a) or resolve(self, *a))
    store = Store(db, clock=lambda: NOW)
    try:
        if operator:
            store.set_plan("acct_default", operator)
        view = cli_arrange.worker_views(store, plans)("acct_default")
        assert view.model_budget_left() == budget
        assert [a[1] for a in asked] == ["acct_default"]  # the one function, not a copy
    finally:
        store.close()


def test_a_workspace_downgraded_after_its_page_was_queued_gets_no_budget_and_no_call(db):
    """The reservation reads the plan in force inside its own transaction: a workspace moved
    to a plan of no model budget after its view was resolved on a plan with one is refused
    there, and the model is never asked."""
    plans = Plans(
        default="free",
        table={"free": Plan("Free", model_budget_micros=0),
               "solo": Plan("Solo", model_budget_micros=10**9)},
    )  # fmt: skip
    store = Store(db)
    try:
        store.set_plan("acct_default", "solo")
        project = store.get_project("alpha")
        views = cli_arrange.worker_views(store, plans)

        def view_for(account_id: str) -> Store:
            view = views(account_id)  # resolved on Solo
            store.set_plan(account_id, "free")  # then downgraded, before the reservation
            return view

        model = FakeModel([])
        price = cairn.arrange.Price(input_per_million=1_000_000, output_per_million=5_000_000)
        arranger = cairn.arrange.Arranger(model, price=price, view_for=view_for, min_claims=1)
        arranger.queue("acct_default", project.id, "drift-sync")
        done = asyncio.run(arranger.run_once())
        assert [d.status for d in done] == ["no budget"] and model.requests == []
        assert store.usage().model_micros == 0 and store.model_reservations() == []
    finally:
        store.close()


def test_an_arrangement_saved_through_the_worker_view_keeps_the_plans_versions(db):
    plans = Plans(default="two", table={"two": Plan("Two", versions_per_page=2)})
    store = Store(db)
    try:
        project = store.get_project("alpha")
        assert store.page_versions(project.id, "drift-sync") == [1, 2]
        page = store.get_page(project.id, "drift-sync")
        answer = {"order": [claim.id for claim in page.claims][::-1], "same": []}
        model = FakeModel([Reply(text=json.dumps(answer), prompt_tokens=9, completion_tokens=3)])
        view = cli_arrange.worker_views(store, plans)("acct_default")
        done = cairn.arrange.arrange_page(view, project.id, "drift-sync", model)
        assert (done.status, done.version) == ("arranged", 3)
        assert store.page_versions(project.id, "drift-sync") == [2, 3]  # the oldest dropped
    finally:
        store.close()


def test_a_tidy_saved_through_the_worker_view_counts_toward_the_plans_versions(db):
    """What the run guide and deploy/README.md say: under a plan that keeps the last versions
    of each page, a tidy is a version like any other, and the oldest goes."""
    plans = Plans(default="two", table={"two": Plan("Two", versions_per_page=2)})
    store = Store(db)
    try:
        project = store.get_project("alpha")
        assert store.page_versions(project.id, "drift-sync") == [1, 2]
        view = cli_arrange.worker_views(store, plans)("acct_default")
        done = cairn.arrange.tidy_page(view, project.id, "drift-sync")
        assert (done.status, done.version) == ("arranged", 3)
        assert store.page_versions(project.id, "drift-sync") == [2, 3]  # the oldest dropped
    finally:
        store.close()


PLANS = "default: tiny\nplans:\n  tiny: {label: Tiny, model_budget_micros: 0}\n"


def test_arrange_needs_the_plans_when_a_workspace_has_a_plan(db, model, tmp_path, capsys):
    store = Store(db)
    other = store.create_account("Other").id
    store.set_plan(other, "solo")  # another workspace's plan is enough
    store.close()
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_T"]
    assert cli.main(argv) == 2
    assert "--plans" in capsys.readouterr().err and model == []
    plans = tmp_path / "plans.yaml"
    plans.write_text(PLANS)
    assert cli.main([*argv, "--plans", str(plans)]) == 0
    assert "alpha/drift-sync: no budget" in capsys.readouterr().out
    assert model == []  # the default plan in force: no model budget


def test_a_subscription_an_older_build_left_does_not_ask_for_plans(db, model, capsys):
    """Nothing reads the billing tables: a row there puts no workspace on a plan."""
    Store(db).close()
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO subscriptions (account_id, provider, plan, status, since)"
        " VALUES ('acct_default', 'till', 'solo', 'active', ?)",
        (NOW.isoformat(),),
    )
    con.commit()
    con.close()
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_T"]
    assert cli.main(argv) == 0
    assert "--plans" not in capsys.readouterr().err


def test_arrange_refuses_a_plans_file_it_cannot_use(db, model, tmp_path, capsys):
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_T"]
    assert cli.main([*argv, "--plans", str(tmp_path / "none.yaml")]) == 2
    assert "cannot use those plans" in capsys.readouterr().err and model == []


def test_usage_lists_a_reservation_never_settled(db, capsys):
    store = Store(db)
    held = store.reserve_model_use(42)
    store.close()
    assert cli.main(["usage", "--db", str(db), "--account", "acct_default"]) == 0
    printed = json.loads(capsys.readouterr().out)
    (listed,) = printed["reservations"]
    assert (listed["id"], listed["micros"], printed["model_micros"]) == (held, 42, 42)
    assert set(listed) == {"id", "period", "micros", "reserved_at"}


def test_the_models_extra_request_fields_come_from_the_environment_too(
    db, model, monkeypatch, capsys
):
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_T"]
    monkeypatch.setenv(
        "CAIRN_T_REQUEST_EXTRA", '{"chat_template_kwargs": {"enable_thinking": false}}'
    )
    assert cli.main(argv) == 0
    body = json.loads(model[0].content)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["max_tokens"] > 0  # the answer bound is still cairn's

    monkeypatch.setenv("CAIRN_T_REQUEST_EXTRA", '{"max_tokens": 100000, "k": "sk-not-shown"}')
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert "CAIRN_T_REQUEST_EXTRA" in err and "max_tokens" in err and "sk-not-shown" not in err
    assert len(model) == 1  # nothing was sent with the refused setting


# The model in Anthropic's own format (T4d): <PREFIX>_PROVIDER=anthropic

CLAUDE_KEY = "sk-ant-arrange-cli-do-not-leak"
CLAUDE_ENV = {
    "CAIRN_A_PROVIDER": "anthropic",
    "CAIRN_A_API_KEY": CLAUDE_KEY,
    "CAIRN_A_MODEL": "claude-haiku-5-5",
}


@pytest.fixture
def claude(monkeypatch):
    """Anthropic's API, as a MockTransport: answers in its Messages format with the page's
    claim ids reversed, and records each request. No base URL is set, so the client aims at
    Anthropic's own address, which the transport stands in for."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        user = json.loads(request.content)["messages"][0]["content"]
        ids = [part.split('"', 1)[0] for part in user.split(' id="')[1:]]
        answer = json.dumps({"order": ids[::-1], "same": []})
        thinking = {"type": "thinking", "thinking": "", "signature": "s"}
        return httpx.Response(
            200,
            json={
                "type": "message",
                "content": [thinking, {"type": "text", "text": answer}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 300, "output_tokens": 40},
            },
        )

    for name, value in CLAUDE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("CAIRN_A_BASE_URL", raising=False)
    monkeypatch.setattr(cli_arrange, "_model_options", {"transport": httpx.MockTransport(handler)})
    return seen


def test_arrange_builds_the_anthropic_client_when_the_provider_says_so(db, claude, capsys):
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_A"]
    assert cli.main([*argv, "--model-price", "1,5"]) == 0
    out, err = capsys.readouterr()
    assert sorted(out.splitlines()) == [
        "alpha/drift-sync: arranged, version 2 -> 3, tokens 300/40",
        "alpha/short: small, version 1",
    ]
    (request,) = claude
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == CLAUDE_KEY and "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["thinking"] == {"type": "disabled"} and "temperature" not in body
    assert body["system"] and body["max_tokens"] == cairn.arrange.answer_bound(6)
    assert CLAUDE_KEY not in out + err


def test_serve_builds_the_anthropic_client_when_the_provider_says_so(db, claude, monkeypatch):
    from cairn.llm import AnthropicMessages

    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    assert cli.main(["serve", "--db", str(db), "--model-env", "CAIRN_A"]) == 0
    model = apps[0].state.arranger.model
    assert isinstance(model, AnthropicMessages) and model.name == "claude-haiku-5-5"
    assert model.deadline == cli_arrange.MODEL_DEADLINE


@pytest.mark.parametrize("command", ["serve", "arrange"])
def test_a_provider_cairn_does_not_know_exits_2_naming_the_variable(
    db, claude, monkeypatch, capsys, command
):
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: None)
    monkeypatch.setenv("CAIRN_A_PROVIDER", "claudius-sk-not-shown")
    argv = [command, "--db", str(db), "--model-env", "CAIRN_A"]
    if command == "arrange":
        argv += ["--account", "acct_default"]
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert "CAIRN_A_PROVIDER" in err and "anthropic" in err and "openai" in err
    assert "claudius" not in err and CLAUDE_KEY not in err and claude == []


def test_anthropics_api_without_a_key_exits_2_before_any_call(db, claude, monkeypatch, capsys):
    monkeypatch.delenv("CAIRN_A_API_KEY")
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_A"]
    assert cli.main(argv) == 2
    assert "CAIRN_A_API_KEY" in capsys.readouterr().err and claude == []


def test_the_model_env_help_and_the_docstring_name_the_provider_variable():
    commands = cli._parser()._subparsers._group_actions[0].choices
    for command in ("serve", "arrange"):
        said = " ".join(commands[command]._option_string_actions["--model-env"].help.split())
        assert "PREFIX_PROVIDER" in said and "anthropic" in said
    doc = " ".join(cli_arrange.__doc__.split())
    assert "<PREFIX>_PROVIDER" in doc and '"anthropic"' in doc


# --tidy: cairn arranges pages itself, with no model (T4f)


def test_arrange_tidy_makes_one_pass_with_no_model_and_prints_a_line_a_page(db, model, capsys):
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--tidy"]
    assert cli.main(argv) == 0
    out, err = capsys.readouterr()
    assert sorted(out.splitlines()) == [
        "alpha/drift-sync: arranged, version 2 -> 3",
        "alpha/short: unchanged, version 1",
    ]
    assert err == "" and model == [] and "Drift" not in out
    assert cli.main(argv) == 0  # tidy already: nothing more to do
    assert sorted(capsys.readouterr().out.splitlines()) == [
        "alpha/drift-sync: unchanged, version 3",
        "alpha/short: unchanged, version 1",
    ]
    store = Store(db)
    try:
        assert store.get_page(store.get_project("alpha").id, "drift-sync").metrics[
            "arranged_by"
        ] == (cairn.arrange.TIDY)
        used = store.usage()
        assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (0, 0, 0)
    finally:
        store.close()


def test_arrange_tidy_needs_the_plans_where_a_workspace_has_one(db, model, tmp_path, capsys):
    """A tidy spends nothing, but a plan also limits what a workspace keeps (the versions of a
    page), so the pass is held to the plans like the one with a model; without them it stops
    before a page is read."""
    store = Store(db)
    store.set_plan(store.create_account("Other").id, "solo")
    store.close()
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--tidy"]
    assert cli.main(argv) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.strip() == cli_arrange.NEEDS_PLANS
    store = Store(db)
    assert store.get_page(store.get_project("alpha").id, "drift-sync").version == 2  # untouched
    store.close()
    plans = tmp_path / "plans.yaml"
    plans.write_text(PLANS)  # no model budget: a tidy needs none
    assert cli.main([*argv, "--plans", str(plans)]) == 0
    assert "alpha/drift-sync: arranged, version 2 -> 3" in capsys.readouterr().out
    assert model == []


@pytest.mark.parametrize(
    ("given", "option"),
    [
        (["--model-price", "1,5"], "--model-price"),
        (["--model-deadline", "5"], "--model-deadline"),
    ],
)
def test_arrange_tidy_refuses_a_model_option_in_one_line(db, model, capsys, given, option):
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--tidy", *given]
    assert cli.main(argv) == 2
    assert capsys.readouterr().err == f"--tidy uses no model, so it takes no {option}\n"
    store = Store(db)
    try:
        assert store.page_versions(store.get_project("alpha").id, "drift-sync") == [1, 2]
    finally:
        store.close()


def test_arrange_takes_exactly_one_of_a_model_and_tidy(db, model, capsys):
    argv = ["arrange", "--db", str(db), "--account", "acct_default"]
    with pytest.raises(SystemExit) as refused:
        cli.main([*argv, "--tidy", "--model-env", "CAIRN_T"])
    assert refused.value.code == 2 and model == []
    assert "not allowed with argument" in capsys.readouterr().err
    with pytest.raises(SystemExit) as refused:
        cli.main(argv)
    assert refused.value.code == 2
    assert "one of the arguments --model-env --tidy is required" in capsys.readouterr().err


def test_serve_tidy_runs_the_worker_with_no_model(db, monkeypatch, capsys):
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    assert cli.main(["serve", "--db", str(db), "--tidy"]) == 0
    arranger = apps[0].state.arranger
    assert arranger.model is None and arranger.price is None and arranger.every
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("given", "option"),
    [
        (["--model-env", "CAIRN_T"], "--model-env"),
        (["--model-price", "1,5"], "--model-price"),
        (["--model-deadline", "300"], "--model-deadline"),
    ],
)
def test_serve_tidy_refuses_a_model_option_in_one_line_and_starts_nothing(
    tmp_path, model, monkeypatch, capsys, given, option
):
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    db = tmp_path / "new.db"
    assert cli.main(["serve", "--db", str(db), "--tidy", *given]) == 2
    assert capsys.readouterr().err == f"--tidy uses no model, so it takes no {option}\n"
    assert not db.exists() and model == []


# serve tidies by default (decision 76); --no-tidy turns it off


def test_serve_tidies_by_default_and_no_tidy_turns_it_off(db, model, monkeypatch, capsys):
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    assert cli_arrange.TIDY_BY_DEFAULT is True
    for argv in ([], ["--tidy"], ["--no-tidy"]):
        assert cli.main(["serve", "--db", str(db), *argv]) == 0
    default, tidy, off = (app.state.arranger for app in apps)
    for arranger in (default, tidy):
        assert arranger.model is None and arranger.price is None and arranger.every
    assert off is None
    assert capsys.readouterr().err == "" and model == []


def test_the_default_is_one_constant(db, monkeypatch):
    """Reversing decision 76 is TIDY_BY_DEFAULT = False: serve is then as it was before it,
    with no worker unless --tidy asks for one."""
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    monkeypatch.setattr(cli_arrange, "TIDY_BY_DEFAULT", False)
    for argv in ([], ["--tidy"], ["--no-tidy"]):
        assert cli.main(["serve", "--db", str(db), *argv]) == 0
    default, tidy, off = (app.state.arranger for app in apps)
    assert default is None and off is None and tidy.model is None and tidy.every


def test_serve_refuses_tidy_with_no_tidy(db, monkeypatch, capsys):
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    with pytest.raises(SystemExit) as refused:
        cli.main(["serve", "--db", str(db), "--tidy", "--no-tidy"])
    assert refused.value.code == 2
    assert "argument --no-tidy: not allowed with argument --tidy" in capsys.readouterr().err


def test_arrange_takes_no_no_tidy(db, capsys):
    """arrange is unchanged: exactly one of --model-env and --tidy, and nothing to turn off."""
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--tidy", "--no-tidy"]
    with pytest.raises(SystemExit) as refused:
        cli.main(argv)
    assert refused.value.code == 2
    assert "unrecognized arguments: --no-tidy" in capsys.readouterr().err


def test_model_deadline_without_a_model_is_accepted_and_ignored(db, model, monkeypatch, capsys):
    """As before decision 76: --model-deadline without --model-env starts serve and changes
    nothing; the worker is the one the tidy switch says (the tidy, or none)."""
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    assert cli.main(["serve", "--db", str(db), "--model-deadline", "5"]) == 0
    assert cli.main(["serve", "--db", str(db), "--no-tidy", "--model-deadline", "5"]) == 0
    assert apps[0].state.arranger.model is None and apps[1].state.arranger is None
    assert capsys.readouterr().err == "" and model == []


SERVE_PROBE = """
import sys
import uvicorn
uvicorn.run = lambda *args, **options: None
from cairn.web import cli
code = cli.main(["serve", "--db", sys.argv[1], *sys.argv[2:]])
watched = ("cairn.arrange", "cairn.llm", "cairn.llm_compiler")
print(code, sorted(m for m in sys.modules if m in watched))
"""


def test_serve_no_tidy_loads_no_arranging_code(tmp_path):
    """--no-tidy is serve as it was before decision 76: no worker and no arranging code; the
    default loads the worker (cairn.arrange, which imports cairn.llm), and no model compiler."""
    db = str(tmp_path / "cairn.db")
    said = {}
    for option in ("--no-tidy", None):
        argv = [sys.executable, "-c", SERVE_PROBE, db, *([option] if option else [])]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        said[option] = out.stdout.strip()
    assert said["--no-tidy"] == "0 []"
    assert said[None] == "0 ['cairn.arrange', 'cairn.llm']"
