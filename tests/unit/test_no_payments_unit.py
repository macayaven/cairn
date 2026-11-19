"""Unit tests for cairn without payments: nothing sells a plan. `serve` has no option for it,
no address about it exists, and the addresses that did answer as any address that never
existed answers, for a browser with a session, one without, and an API key."""

import dataclasses
import importlib.util
import inspect
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient
from test_schema_floor_unit import v30_database

import cairn.store
import cairn.store.schema
from cairn.identity import GitHub
from cairn.plans import Plan, Plans, load
from cairn.server import SignIn, create_app
from cairn.store import AccountSummary, OverLimit, Store
from cairn.web import cli

BASE = "http://127.0.0.1:8765"
ROOT = Path(__file__).resolve().parents[2]
PLANS = Plans({"free": Plan("Free", notes_per_month=3), "solo": Plan("Solo")}, "free")
GONE = ("/billing", "/billing/checkout", "/billing/portal", "/billing/webhook", "/pricing")
CONTROL = "/no-such-page"  # an address that never existed
GONE_API = ("/api/billing", "/api/checkout", "/api/subscription", "/api/pricing")
CONTROL_API = "/api/no-such-thing"


def _serve_error(tmp_path, monkeypatch, capsys, *options: str) -> tuple[int, str]:
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    argv = ["serve", "--db", str(tmp_path / "cairn.db"), *options]
    with pytest.raises(SystemExit) as stopped:
        cli.main(argv)
    return stopped.value.code, capsys.readouterr().err


@pytest.mark.parametrize("option", ["--billing", "--on-sale"])
def test_serve_refuses_the_selling_options_as_any_option_it_does_not_know(
    tmp_path, monkeypatch, capsys, option
):
    code, err = _serve_error(tmp_path, monkeypatch, capsys, option, "solo")
    unknown_code, unknown = _serve_error(tmp_path, monkeypatch, capsys, "--no-such-option", "solo")
    assert (code, unknown_code) == (2, 2)
    assert err == unknown.replace("--no-such-option", option)
    assert "unrecognized arguments" in err


def test_serve_help_says_nothing_of_selling(capsys):
    with pytest.raises(SystemExit):
        cli.main(["serve", "--help"])
    shown = capsys.readouterr().out.lower()
    for word in ("--billing", "--on-sale", "billing", "pricing", "subscription", "checkout"):
        assert word not in shown, word


def test_no_payment_module_is_left():
    # A checkout that had the package may keep its ignored __pycache__ directory, which
    # imports as an empty namespace: what must be gone is every source file.
    assert not any((ROOT / "src/cairn/billing").rglob("*.py"))
    for name in ("cairn.web.billing", "cairn.web.cli_billing"):
        assert importlib.util.find_spec(name) is None, name
    for name in ("billing.html", "pricing.html"):
        assert not (ROOT / "src/cairn/templates" / name).exists(), name


def test_create_app_has_no_billing():
    assert "billing" not in inspect.signature(create_app).parameters


def test_no_variable_of_the_payment_provider_is_read_anywhere():
    for path in (ROOT / "src").rglob("*"):
        if path.is_file() and path.suffix in (".py", ".html", ".md", ".txt", ".yaml"):
            assert "CAIRN_STRIPE_" not in path.read_text(encoding="utf-8"), path


def _sign_in() -> SignIn:
    offline = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    return SignIn(providers=(GitHub("id", "secret"),), http=offline, join="personal")


@pytest.fixture(params=["key login", "sign-in", "sign-in behind the wall"])
def install(request, tmp_path):
    """An install with plans, in each way a browser reaches it: (app, session, key, walled)."""
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    settings: dict = {"public_url": BASE, "plans": PLANS}
    if request.param != "key login":
        settings["sign_in"] = _sign_in()
    if request.param == "sign-in behind the wall":
        settings["wall"] = True
    session = store.create_session()
    yield create_app(store, **settings), session, key, request.param.endswith("wall")
    store.close()


def _answers(client: TestClient, method: str, path: str, query: str = "", **kw) -> tuple:
    """What an address answers, with the address itself taken out of the body."""
    url = f"{path}?{query}" if query else path
    answer = client.request(method, url, follow_redirects=False, **kw)
    body = answer.text.replace(url, "<address>").replace(path, "<address>")
    location = answer.headers.get("location", "").replace(path, "<address>")
    return answer.status_code, answer.headers.get("content-type"), location, body


def _as(client: TestClient, session: str | None) -> TestClient:
    """`client` as a browser with that session, or with none."""
    client.cookies.clear()
    if session:
        client.cookies.set("cairn_session", session)
    return client


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("query", ["", "checkout=done"])
@pytest.mark.parametrize("site", ["same-origin", "cross-site"])
def test_the_selling_addresses_answer_as_an_address_that_never_existed(
    install, method, query, site
):
    app, session, key, _ = install
    headers = {"sec-fetch-site": site}
    with TestClient(app, base_url=BASE) as client:
        for who in (None, session):  # a browser without a session, then with one
            _as(client, who)
            for path in GONE:
                assert _answers(client, method, path, query, headers=headers) == _answers(
                    client, method, CONTROL, query, headers=headers
                ), (who, path)
        _as(client, None)
        sent = {"x-api-key": key, **headers}
        for path in GONE:
            assert _answers(client, method, path, query, headers=sent) == _answers(
                client, method, CONTROL, query, headers=sent
            ), path
        for path in GONE_API:
            assert _answers(client, method, path, query, headers=sent) == _answers(
                client, method, CONTROL_API, query, headers=sent
            ), path


def test_an_address_that_never_existed_answers_as_pinned(install):
    """What the addresses above answer, pinned: a page that says there is none (404) for a
    browser with or without a session and for a key, except behind the wall, where a browser
    without a session, or with only a key, is sent to sign in (303 to /login; 401 for a
    form). A key on /api gets the router's 404."""
    app, session, key, walled = install
    same = {"sec-fetch-site": "same-origin"}
    with TestClient(app, base_url=BASE) as client:
        got = _as(client, None).get(CONTROL, follow_redirects=False)
        posted = client.post(CONTROL, headers=same, follow_redirects=False)
        if walled:
            assert (got.status_code, got.headers["location"]) == (303, "/login")
            assert posted.status_code == 401
        else:
            assert got.status_code == 404 and "There is no page at this address." in got.text
            assert posted.status_code == 404
        _as(client, session)
        for method in ("GET", "POST"):
            got = client.request(method, CONTROL, headers=same, follow_redirects=False)
            assert got.status_code == 404 and "There is no page at this address." in got.text
        _as(client, None)
        got = client.get(CONTROL, headers={"x-api-key": key}, follow_redirects=False)
        assert got.status_code == (303 if walled else 404)  # a key is no browser's session
        api = client.get(CONTROL_API, headers={"x-api-key": key})
        assert api.status_code == 404


def test_no_page_links_to_a_selling_address(install):
    app, session, _, _ = install
    with TestClient(app, base_url=BASE) as client:
        client.cookies.set("cairn_session", session)
        for path in ("/", "/workspace", "/account", "/usage", "/docs", "/docs/privacy"):
            page = client.get(path).text
            for address in ('href="/billing', 'href="/pricing', 'action="/billing'):
                assert address not in page, (path, address)


# The store: the plan is the operator's, and what billing wrote is kept, never read

MARKER = "legacy-billing-row"
FAR = "2999-01-01T00:00:00.000000+00:00"
PAST = "2001-01-01T00:00:00.000000+00:00"


def test_the_landing_page_keeps_its_two_ways_to_run_cairn_with_no_price(tmp_path):
    store = Store(tmp_path / "cairn.db")
    app = create_app(store, public_url=BASE, plans=PLANS, sign_in=_sign_in())
    with TestClient(app, base_url=BASE) as client:
        home = client.get("/").text
    store.close()
    ways = home.split("<h2>Two ways to run it</h2>", 1)[1].split("</section>", 1)[0]
    assert "<h3>Here</h3>" in ways and "<h3>On your own machine</h3>" in ways
    assert 'href="/login"' in ways and 'href="/docs/run"' in ways
    for word in ("/pricing", "/billing", "$", "price", "Pricing"):
        assert word not in home, word


def test_the_usage_page_says_the_plan_and_who_sets_it(install):
    """The account page once sent an owner to Billing to buy or manage a plan. Now the usage
    page says the plan, what it allows, and that whoever runs the server sets it."""
    app, session, _, _ = install
    with TestClient(app, base_url=BASE) as client:
        page = _as(client, session).get("/usage")
    assert page.status_code == 200
    said = " ".join(page.text.split())
    assert "This workspace is on the Free plan." in said
    assert "Whoever runs this server sets a workspace's plan." in said
    assert "3 notes a month" in said


BILLED = "acct_aaaaaaaaaaaa"  # a workspace of the database _billed makes


def _billed(db: Path) -> str:
    """A database at schema step 30 holding what a build that sold plans left for one of its
    workspaces (_billing_rows); its id. Opening it runs step 31, which drops those tables."""
    v30_database(db)
    _billing_rows(db, BILLED)
    return BILLED


def _billing_rows(db: Path, account_id: str, plan: str = "solo") -> None:
    """What a build that sold plans left in a database: an active subscription to `plan` paid
    far ahead, a provider's message seen, and an open checkout, written as that build did."""
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO subscriptions (account_id, provider, plan, status, period_end, since,"
            " customer_ref, subscription_ref, started, past_due_since)"
            " VALUES (?, 'till', ?, 'active', ?, ?, ?, ?, NULL, NULL)",
            (account_id, plan, FAR, PAST, f"cus-{MARKER}", f"sub-{MARKER}"),
        )
        con.execute(
            "INSERT INTO billing_messages (provider, id, account_id, received_at)"
            " VALUES ('till', ?, ?, ?)",
            (f"evt-{MARKER}-{account_id}", account_id, PAST),
        )
        con.execute(
            "INSERT INTO checkouts (account_id, plan, idempotency_key, url, opened_at, ends_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, plan, f"key-{account_id}", f"https://pay.test/{MARKER}", PAST, FAR),
        )
        con.commit()
    finally:
        con.close()


def _count(db: Path, table: str, account_id: str) -> int:
    con = sqlite3.connect(db)
    try:
        query = f"SELECT COUNT(*) FROM {table} WHERE account_id = ?"
        return con.execute(query, (account_id,)).fetchone()[0]
    finally:
        con.close()


@pytest.mark.parametrize("operator", [None, "team"])
def test_a_workspaces_plan_is_the_operators_else_the_default_whatever_billing_left(
    tmp_path, operator
):
    db = tmp_path / "cairn.db"
    made = SimpleNamespace(id=_billed(db))
    store = Store(db)
    try:
        store.set_plan(made.id, operator)
        plans = Plans(
            {"free": Plan("Free", notes_per_month=1), "solo": Plan("Solo"), "team": Plan("Team")},
            "free",
        )
        assert store.plan_in_force(made.id) == operator
        assert plans.in_force(store, made.id)[0] == (operator or "free")
        (summary,) = [s for s in store.list_account_summaries() if s.id == made.id]
        assert summary.plan == operator
        # A write is held to that plan too, not to the subscription's.
        view = plans.limit(store.for_account(made.id), plans.in_force(store, made.id)[1])
        project = view.create_project("billing-ops")
        view.add_note(project.id, "# One\n\nThe first note.\n")
        if operator is None:
            with pytest.raises(OverLimit):
                view.add_note(project.id, "# Two\n\nPast the cap.\n")
        else:
            view.add_note(project.id, "# Two\n\nTeam has no cap.\n")
    finally:
        store.close()


def test_the_store_has_no_billing_and_the_schema_keeps_its_steps():
    for name in (
        "apply_billing_event", "note_unusable_billing", "subscriptions_outside",
        "subscription", "subscription_in_force", "billing_customer", "open_checkout",
        "checkout_given", "drop_checkout",
    ):  # fmt: skip
        assert not hasattr(Store, name), name
    for name in (
        "BillingEvent", "BillingKind", "Subscription", "OpenCheckout", "ACTIVE_GRACE",
        "PAST_DUE_GRACE", "PROVIDER_NAME_MAX", "in_force_until",
    ):  # fmt: skip
        assert not hasattr(cairn.store, name), name
    assert not (ROOT / "src/cairn/store/billing.py").exists()
    assert "subscription" not in AccountSummary.model_fields
    assert cairn.store.SCHEMA_VERSION == 31
    for step in (12, cairn.store.schema._SUBSCRIPTION_TIMES, cairn.store.schema._CHECKOUTS):
        assert step in cairn.store.schema._MIGRATIONS, step  # history: 31 drops what they made
    assert cairn.store.schema._OLDEST_BUILD in cairn.store.schema._MIGRATIONS


def test_a_database_with_billing_rows_opens_without_them_and_its_workspace_deletes(tmp_path):
    db = tmp_path / "cairn.db"
    made = _billed(db)
    tables = ("subscriptions", "billing_messages", "checkouts")
    assert [_count(db, t, made) for t in tables] == [1, 1, 1]
    store = Store(db)  # step 31 drops the three tables with their rows
    try:
        with sqlite3.connect(db) as con:
            held = {n for (n,) in con.execute("SELECT name FROM sqlite_master")}
        assert not held & set(tables)
        assert store.delete_account(made)
    finally:
        store.close()


def test_an_export_leaves_what_billing_wrote_out(tmp_path):
    db = tmp_path / "cairn.db"
    made = SimpleNamespace(id=_billed(db))
    store = Store(db)
    try:
        out = io.BytesIO()
        store.for_account(made.id).export(out)
        with zipfile.ZipFile(io.BytesIO(out.getvalue())) as archive:
            held = b"".join(archive.read(name) for name in archive.namelist())
        assert MARKER.encode() not in held
        assert b"subscription" not in held and b"checkout" not in held
    finally:
        store.close()


def test_workspace_list_prints_no_subscription(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    _billed(db)
    assert cli.main(["workspace-list", "--db", str(db)]) == 0
    printed = capsys.readouterr().out
    rows = json.loads(printed)
    assert all(set(row) == {"account", "name", "created_at", "members", "plan"} for row in rows)
    assert MARKER not in printed
    with pytest.raises(SystemExit):
        cli.main(["workspace-list", "--help"])
    assert "subscription" not in capsys.readouterr().out


# The plans file: `price` is a key like any other it does not know


@pytest.mark.parametrize("value", ['"$9 a month"', "9", '""'])
def test_a_plans_file_with_a_price_is_refused_as_any_unknown_key_is(tmp_path, value):
    def refusal(key: str) -> str:
        file = tmp_path / f"{key}.yaml"
        file.write_text(f"default: free\nplans:\n  free:\n    label: Free\n    {key}: {value}\n")
        with pytest.raises(ValueError) as refused:
            load(file)
        return str(refused.value)

    assert refusal("price") == refusal("colour").replace("colour", "price")


def test_a_plan_has_no_price():
    assert "price" not in {field.name for field in dataclasses.fields(Plan)}
    with pytest.raises(TypeError):
        Plan("Solo", price="$9 a month")  # type: ignore[call-arg]


def test_no_example_plans_file_shows_a_price():
    examples = [ROOT / "deploy" / "plans.example.yaml", *ROOT.glob("docs/**/*.y*ml")]
    for path in examples:  # --model-price is the model's, not a plan's
        assert re.search(r"(?<!model-)price", path.read_text()) is None, path


# Docs and the hosted VM's files

# The record of what was decided keeps the names of the two removed documents; nothing else
# in the repository points at them.
HELD = ("docs/decisions.md", "docs/spec-v1.md")
PAYMENT_DOCS = ("docs/pricing.md", "docs/stripe-api.md")


def _tracked(*patterns: str) -> list[Path]:
    """The repository's own files under `patterns`: not what a run leaves beside them
    (__pycache__, .terraform and other dot directories, private/) and no binary file."""

    def ours(path: Path) -> bool:
        parts = path.relative_to(ROOT).parts
        left = any(p.startswith(".") or p in ("__pycache__", "private") for p in parts[:-1])
        return not left and path.suffix not in (".pyc", ".png", ".ico", ".woff2")

    paths = {p for pattern in patterns for p in ROOT.glob(pattern) if p.is_file()}
    return sorted(p for p in paths if ours(p))


def test_the_payment_docs_are_gone_and_nothing_points_at_them():
    assert not any((ROOT / doc).exists() for doc in PAYMENT_DOCS)
    for path in _tracked("src/**/*", "docs/**/*", "infra/**/*", "tests/**/*.py", "deploy/*"):
        if str(path.relative_to(ROOT)) in HELD or path == Path(__file__):
            continue
        text = path.read_text(errors="replace")
        for doc in ("pricing.md", "stripe-api.md"):
            assert doc not in text, (path, doc)


def test_the_hosted_vm_names_no_payment_provider_and_no_option_that_sold():
    for path in _tracked("infra/gcp/**/*"):
        text = path.read_text(errors="replace")
        for word in ("CAIRN_STRIPE_", "--on-sale", "/pricing", "/billing"):
            assert word not in text, (path, word)
        # gcloud's --billing-account links the cloud project to the bill Google sends
        assert re.search(r"--billing(?![-\w])", text) is None, path
        assert re.search(r"stripe", text, re.I) is None, path
