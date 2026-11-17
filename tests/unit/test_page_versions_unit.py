"""Unit tests for the plan's bound on page versions (H6 fix 1b): a limited view keeps the
last `versions_per_page` versions of each page and drops the older ones in the save's own
transaction; the words for each limit come from one function in cairn.plans."""

import io
import zipfile
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.compiler import claim_id, render_body
from cairn.plans import Plan, Plans, limit_sentence, limit_sentences, load
from cairn.server import create_app
from cairn.store import Claim, Limits, Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


def save(view, project_id: str, n: int, slug: str = "retry-policy"):
    # A body as cairn renders a page without claims (H6c: an import refuses any other).
    return view.save_page(project_id, slug, f"Version {n}", f"# Version {n}\n", [])


def save_cited(view, project_id: str, n: int, slug: str = "retry-policy"):
    """A version as cairn writes one, for a page an export carries: one claim, the one
    passage of a note in the project (an import refuses a version without claims)."""
    note, _ = view.add_note(project_id, "Retries twice, then stops.\n")
    (span,) = view.note_spans(note.id)
    claim = Claim(id=claim_id(span.id), text=span.text, span_ids=(span.id,))
    title = f"Version {n}"
    body = render_body(title, [claim], {span.id: span})
    return view.save_page(project_id, slug, title, body, [claim])


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


# The words


def test_each_limit_is_said_in_words():
    plan = Plan(
        "Team",
        notes_per_month=5000,
        storage_bytes=500_000_000,
        projects=40,
        versions_per_page=50,
        model_budget_micros=2_000_000,
        requests_per_minute=120,
    )
    assert limit_sentences(plan) == {
        "notes_per_month": "5,000 notes a month",
        "storage_bytes": "500 MB of notes",
        "passages": "12,500,000 passages of notes",
        "projects": "40 projects",
        "versions_per_page": "the last 50 versions of each page",
        "model_budget_micros": "$2.00 of model use a month",
        "requests_per_minute": "120 requests a minute",
    }


@pytest.mark.parametrize(
    "limit, cap, said",
    [
        ("notes_per_month", 1, "1 note a month"),
        ("storage_bytes", 80, "80 bytes of notes"),
        ("storage_bytes", 1, "1 byte of notes"),
        ("storage_bytes", 1_500_000, "1.5 MB of notes"),
        ("storage_bytes", 2_000_000_000, "2 GB of notes"),
        ("storage_bytes", 25_000, "25 kB of notes"),
        ("projects", 1, "1 project"),
        ("versions_per_page", 1, "the latest version of each page"),
        ("model_budget_micros", 0, "no model use"),
        ("model_budget_micros", 1_250_000, "$1.25 of model use a month"),
        ("notes_per_month", None, "any number of notes a month"),
        ("storage_bytes", None, "notes of any size in all"),
        ("projects", None, "any number of projects"),
        ("versions_per_page", None, "every version of each page"),
        ("model_budget_micros", None, "model use without a monthly cap"),
        ("requests_per_minute", None, "requests without a rate limit"),
    ],
)
def test_one_limit_in_words(limit, cap, said):
    assert limit_sentence(limit, cap) == said


def test_a_plan_keeps_at_least_the_latest_version():
    with pytest.raises(ValueError, match="versions_per_page"):
        Plan("Free", versions_per_page=0)
    with pytest.raises(ValueError, match="versions_per_page"):
        Limits(versions_per_page=0)
    assert Plan("Free", versions_per_page=3).limits() == Limits(versions_per_page=3)


def test_a_plans_file_takes_versions_per_page(tmp_path):
    file = tmp_path / "plans.yaml"
    file.write_text("default: free\nplans:\n  free:\n    label: Free\n    versions_per_page: 5\n")
    assert load(file).table["free"].versions_per_page == 5


def test_limiting_twice_keeps_the_fewer_versions():
    assert Limits(versions_per_page=5).tighter(Limits(versions_per_page=3)).versions_per_page == 3


# The store


def test_a_limited_view_keeps_the_last_versions_and_numbers_never_restart(store):
    project = store.create_project("ops")
    limited = store.limited(Limits(versions_per_page=2))
    for n in range(1, 6):
        page = save(limited, project.id, n)
    assert page.version == 5
    assert store.page_versions(project.id, "retry-policy") == [4, 5]
    assert store.get_page(project.id, "retry-policy", 3) is None
    assert store.get_page(project.id, "retry-policy").body == "# Version 5\n"
    assert save(limited, project.id, 6).version == 6  # after the dropped ones, never 3
    assert store.page_versions(project.id, "retry-policy") == [5, 6]


def test_only_that_pages_versions_are_dropped(store):
    project = store.create_project("ops")
    for n in range(1, 4):
        save(store, project.id, n, slug="other")  # an unlimited view keeps every version
    assert store.page_versions(project.id, "other") == [1, 2, 3]
    limited = store.limited(Limits(versions_per_page=1))
    save(limited, project.id, 1)
    save(limited, project.id, 2)
    assert store.page_versions(project.id, "retry-policy") == [2]
    assert store.page_versions(project.id, "other") == [1, 2, 3]


def test_the_export_carries_what_exists(store):
    project = store.create_project("ops")
    limited = store.limited(Limits(versions_per_page=2))
    for n in range(1, 5):
        save(limited, project.id, n)
    out = io.BytesIO()
    store.export(out)
    with zipfile.ZipFile(io.BytesIO(out.getvalue())) as archive:
        lines = archive.read("projects/ops/pages.jsonl").decode().splitlines()
    assert len(lines) == 2


def test_an_export_of_the_kept_versions_imports_with_their_numbers(store, tmp_path):
    project = store.create_project("ops")
    limited = store.limited(Limits(versions_per_page=2))
    for n in range(1, 6):
        save_cited(limited, project.id, n)
    archive = io.BytesIO()
    store.export(archive)
    copy = Store(tmp_path / "copy.db")  # notes keep their ids: another database
    try:
        target = copy.for_account(copy.create_account("Brook").id)
        target.import_export(io.BytesIO(archive.getvalue()))
        (imported,) = target.list_projects()
        assert target.page_versions(imported.id, "retry-policy") == [4, 5]
        assert save(target, imported.id, 6).version == 6
    finally:
        copy.close()


def test_an_import_through_a_limited_view_keeps_the_last_versions(store, tmp_path):
    source = Store(tmp_path / "source.db")
    project = source.create_project("ops")
    for n in range(1, 5):
        save_cited(source, project.id, n)
    archive = io.BytesIO()
    source.export(archive)
    source.close()
    target = store.for_account(store.create_account("Brook").id)
    target.limited(Limits(versions_per_page=2)).import_export(io.BytesIO(archive.getvalue()))
    (imported,) = target.list_projects()
    assert target.page_versions(imported.id, "retry-policy") == [3, 4]


def test_a_view_held_to_its_plans_keeps_the_versions_of_the_plan_in_force(store, tmp_path):
    """Through Plans.limit, as requests, syncs and import --plans hold a view: the bound is
    the plan in force at each save and each import, read inside it."""
    plans = Plans({"wide": Plan("Wide"), "two": Plan("Two", versions_per_page=2)}, "wide")
    project = store.create_project("ops")
    view = plans.limit(store, plans.in_force(store, store.account_id)[1])
    for n in range(1, 4):
        save(view, project.id, n)
    assert store.page_versions(project.id, "retry-policy") == [1, 2, 3]  # every one kept
    store.set_plan(store.account_id, "two")  # a downgrade after the view was made
    save(view, project.id, 4)
    assert store.page_versions(project.id, "retry-policy") == [3, 4]
    other = store.create_account("Brook").id
    store.set_plan(other, "two")
    target = store.for_account(other)
    held = plans.limit(target, plans.in_force(target, other)[1])
    source = Store(tmp_path / "source.db")
    old = source.create_project("ops")
    for n in range(1, 5):
        save_cited(source, old.id, n)
    full = io.BytesIO()
    source.export(full)
    source.close()
    held.import_export(io.BytesIO(full.getvalue()))
    (imported,) = target.list_projects()
    assert target.page_versions(imported.id, "retry-policy") == [3, 4]


# The service

PLANS = Plans({"tiny": Plan("Tiny", versions_per_page=2, projects=1)}, default="tiny")


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("laptop")
    project = store.create_project("ops")
    app = create_app(store, public_url=BASE, plans=PLANS)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, key=key, project=project)
    store.close()


def test_a_dropped_version_is_404_with_a_sentence_saying_why(env):
    limited = env.store.limited(PLANS.table["tiny"].limits())
    for n in range(1, 4):
        save(limited, env.project.id, n)
    headers = {"x-api-key": env.key}
    gone = env.client.get("/api/projects/ops/pages/retry-policy?version=1", headers=headers)
    assert gone.status_code == 404
    said = gone.json()["error"]
    assert "no longer kept" in said and "the last 2 versions of each page" in said
    never = env.client.get("/api/projects/ops/pages/retry-policy?version=9", headers=headers)
    assert never.status_code == 404 and "no longer kept" not in never.json()["error"]
    env.client.post("/login", data={"key": env.key}, headers=SAME_ORIGIN)
    page = env.client.get("/b/ops/p/retry-policy?version=1")
    assert page.status_code == 404 and "no longer kept" in page.text


def test_the_usage_page_says_the_plan_in_words(env):
    env.client.post("/login", data={"key": env.key}, headers=SAME_ORIGIN)
    page = env.client.get("/usage")
    assert page.status_code == 200
    assert "the last 2 versions of each page" in page.text and "1 project" in page.text


def test_a_402_says_the_cap_in_the_plans_words(env):
    over = env.client.post("/api/projects", headers={"x-api-key": env.key}, json={"name": "two"})
    assert over.status_code == 402
    assert "the Tiny plan allows 1 project" in over.json()["error"]
