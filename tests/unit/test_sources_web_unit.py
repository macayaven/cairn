"""Unit tests for GET /sources: who may read it, what it shows, and the navigation. The inner
loop under tests/acceptance/test_c2_langfuse.py."""

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
CONFIG = {
    "base_url": "https://langfuse.example.test",
    "projects": ["ops"],
    "public_key_env": "LANGFUSE_PUBLIC_KEY",
    "secret_key_env": "LANGFUSE_SECRET_KEY",
}


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "cairn.db")
    store.create_project("ops")
    store.add_source("traces", "langfuse", CONFIG)
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, key=key)
    store.close()


def as_person(env, role: str) -> None:
    people = ("Olive Owner", "Max Member") if role == "member" else ("Olive Owner",)
    for n, name in enumerate(people):
        signed = env.store.sign_in(
            "github", str(n), f"p{n}@example.test", name, issuer="https://github.com",
            join="default",
        )  # fmt: skip
    env.client.cookies.set(session_cookie(), signed.token)


def test_only_who_manages_the_workspace_sees_its_sources(env):
    as_person(env, "member")  # R4: the page opens; with no account of their own, no source
    member = env.client.get("/sources")
    assert member.status_code == 200 and "langfuse.example.test" not in member.text
    assert "traces" not in member.text
    as_person(env, "owner")
    page = env.client.get("/sources")
    assert page.status_code == 200 and "langfuse.example.test" in page.text


def test_the_page_shows_the_last_run_and_its_error_and_the_navigation_links_it(env):
    env.client.post("/login", data={"key": env.key}, headers=SAME_ORIGIN)
    assert "Not run yet" in env.client.get("/sources").text
    claim = env.store.claim_source("traces")
    env.store.fail_source_run(claim, "Langfuse refused the credentials (HTTP 401)")
    page = env.client.get("/sources").text
    assert "Failed: Langfuse refused the credentials (HTTP 401)" in page
    assert 'href="/sources"' in env.client.get("/workspace").text
    claim = env.store.claim_source("traces")
    report = {"saved": 2, "unchanged": 0, "refused": 1, "rejected": 0, "skipped": 3}
    env.store.finish_source_run(claim, "c", report)
    assert (
        "2 saved, 0 unchanged, 1 refused, 0 rejected, 3 skipped" in env.client.get("/sources").text
    )


def test_the_page_says_why_items_were_skipped_in_the_words_the_command_line_prints(env):
    from cairn.web.cli import _counts

    env.client.post("/login", data={"key": env.key}, headers=SAME_ORIGIN)
    claim = env.store.claim_source("traces")
    reasons = {"no such project": 1, "marked skip": 2, "Unread": 1}  # the order is case's too
    report = {"saved": 1, "unchanged": 0, "refused": 0, "rejected": 0, "skipped": 4}
    env.store.finish_source_run(claim, "c", {**report, "reasons": reasons})
    counts = "1 saved, 0 unchanged, 0 refused, 0 rejected, 4 skipped"
    said = f"{counts} (1 Unread; 2 marked skip; 1 no such project)"
    assert _counts({**report, "reasons": reasons}) == said  # the command line's sentence
    assert said in env.client.get("/sources").text
    claim = env.store.claim_source("traces")
    env.store.finish_source_run(claim, "d", report)  # no reasons: the counts alone, as before
    page = env.client.get("/sources").text
    assert f"{counts}</dd>" in page


def test_the_page_lists_each_refused_or_rejected_draft_by_its_link_and_codes(env):
    env.client.post("/login", data={"key": env.key}, headers=SAME_ORIGIN)
    claim = env.store.claim_source("traces")
    declined = [
        {"link": "https://langfuse.example.test/project/p/traces/t1", "status": "refused",
         "codes": ["secret.aws_key"]},
        {"link": "javascript:alert(1)", "status": "rejected", "codes": ["provenance.x"]},
    ]  # fmt: skip
    report = {"saved": 0, "unchanged": 0, "refused": 1, "rejected": 1, "skipped": 0}
    env.store.finish_source_run(claim, "c", {**report, "declined": declined})
    page = env.client.get("/sources").text
    assert 'href="https://langfuse.example.test/project/p/traces/t1"' in page
    assert "refused: secret.aws_key" in page and "rejected: provenance.x" in page
    assert 'href="javascript' not in page  # a link that is not http(s) is shown as text


def test_the_page_says_a_source_is_held_at_the_plans_monthly_cap(env):
    env.client.post("/login", data={"key": env.key}, headers=SAME_ORIGIN)
    claim = env.store.claim_source("traces")
    hold = {"until": "2026-12-01T00:00:00+00:00", "plan": "tiny", "cap": 1}
    report = {"saved": 1, "unchanged": 0, "refused": 0, "rejected": 0, "skipped": 0}
    env.store.finish_source_run(claim, "c", {**report, "capped": 2, "hold": hold})
    page = env.client.get("/sources").text
    assert "2 left for the next run at the plan's cap" in page
    assert "Held at the plan's monthly cap" in page and "1 December 2026" in page


def test_the_app_refuses_a_background_sync_more_often_than_every_second(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        with pytest.raises(ValueError, match="every"):
            create_app(store, sync_every=0.5)
    finally:
        store.close()
