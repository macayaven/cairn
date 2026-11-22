"""Unit tests for K1: the kinds built side by side say a pull's sentence the same way. A
Linear or GitHub pull that stops for a rate limit, and a GitHub daily check whose search was
refused, say so in Pulled.stopped, as the connector does: the run's report keeps it
(SyncReport.said), the Sources page and source-sync's counts show it, and it is no skipped
item, so it writes no audit event of its own. So does a releases listing longer than one
pull reads (K2). A search that found more than search returns stays a skip:
tests/acceptance/test_c11_github.py (the test of a check past what search returns) reads it
among the pull's skips. Linear and GitHub are stand-ins; nothing leaves the process.
Fixtures are synthetic."""

import asyncio
import logging
from datetime import timedelta

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.sources import github as g
from cairn.sources import linear as lin
from cairn.sources import run_source, source_client
from cairn.store import Store
from cairn.web import cli
from support.cookie_names import session_cookie
from support.sources.github_pull import NOW as G_NOW
from support.sources.github_pull import REPO, Mini, issue_entry, make_world, release_entry, searched
from support.sources.github_pull import SINCE as SINCE_G
from support.sources.github_pull import TOKEN as G_TOKEN
from support.sources.github_pull import VAR as G_VAR
from support.sources.github_pull import pulled as g_pulled
from support.sources.linear import BASE as L_BASE
from support.sources.linear import KEY, SINCE, VAR, Clock, Linear, graphql_error, iid, issue

DAY, HOUR = timedelta(days=1), timedelta(hours=1)
L_NOW = SINCE + 20 * DAY
WEB = "http://127.0.0.1:8765"


def synced(store) -> list:
    return [e for e in store.list_audit() if e.action == "source.synced"]


def page_of(store) -> str:
    """The Sources page as an owner of the workspace sees it."""
    owner = store.sign_in(
        "sso", "o", "o@example.test", "Olive", issuer="https://id.example.test", join="default"
    )
    with TestClient(create_app(store, public_url=WEB), base_url=WEB) as client:
        client.cookies.set(session_cookie(), owner.token)
        return client.get("/sources").text


def shown(page: str, sentence: str) -> bool:
    return sentence.replace("'", "&#39;") in page or sentence in page


# Linear


@pytest.fixture
def linear(tmp_path):
    clock = Clock(SINCE - DAY)
    store = Store(tmp_path / "cairn.db", clock=clock)
    store.create_project("linear")
    store.add_source("tracker", "linear", {"key_env": VAR, "projects": [], "base_url": L_BASE})
    clock.now = SINCE
    store.enable_source("tracker", projects=["linear"])
    yield store
    store.close()


def run_linear(store, stand_in: Linear, at=L_NOW):
    http = source_client(httpx.MockTransport(stand_in))
    return asyncio.run(run_source(store, "tracker", http=http, env={VAR: KEY}, clock=Clock(at)))


def test_a_linear_listing_rate_limited_says_so_in_the_report_and_on_the_sources_page(
    linear, caplog
):
    caplog.set_level(logging.DEBUG)
    stand_in = Linear([issue(1, L_NOW - DAY)])
    stand_in.answers.append((lin.LISTING, graphql_error(429, "slow down", code="RATELIMITED")))
    report = run_linear(linear, stand_in)
    sentence = lin.RATE_LIMITED.format(name="tracker", op=lin.LISTING)
    assert report.said == sentence and sentence.endswith(".")
    assert (report.saved, report.skipped, report.reasons) == (0, 0, {})
    info = linear.get_source("tracker")
    assert info.last_error is None and info.last_report["said"] == sentence
    assert sentence in cli._counts(info.last_report)
    assert synced(linear) == []  # every count is zero: no event for an item that is not one
    assert not [r for r in caplog.records if r.name.startswith("cairn.sources")]  # said once
    assert shown(page_of(linear), sentence)


def test_a_linear_pull_rate_limited_halfway_keeps_what_it_finished_and_says_why(linear):
    stand_in = Linear([issue(1, L_NOW - 2 * DAY), issue(2, L_NOW - DAY)])
    stand_in.answers.append((lin.COMMENTS, graphql_error(400, "slow down", code="RATELIMITED")))
    stand_in.answers.insert(0, (lin.COMMENTS, stand_in.comments({"id": iid(1)})))
    report = run_linear(linear, stand_in)
    assert report.saved == 1 and report.skipped == 0 and report.reasons == {}
    assert report.said == lin.RATE_LIMITED.format(name="tracker", op=lin.COMMENTS)
    (event,) = synced(linear)
    assert event.detail["saved"] == 1 and event.detail["skipped"] == 0


# GitHub


def run_github(world, at):
    return asyncio.run(
        run_source(world.store, "code", http=world.http, env={G_VAR: G_TOKEN}, clock=lambda: at)
    )


def limited(request: httpx.Request) -> httpx.Response:
    return httpx.Response(429, json={"message": "slow down"}, headers={"retry-after": "60"})


def test_a_github_pull_rate_limited_says_so_and_skips_nothing(tmp_path):
    mini = Mini()
    mini.a_pull(3)
    world = make_world(tmp_path, mini)
    try:
        assert run_github(world, G_NOW).saved == 1
        events = len(synced(world.store))
        mini.routes[f"/repos/{REPO}/issues"] = limited
        report = run_github(world, G_NOW + HOUR)
        assert report.said == g.RATE_LIMITED and g.RATE_LIMITED.endswith(".")
        assert (report.saved, report.skipped, report.reasons) == (0, 0, {})
        info = world.store.get_source("code")
        assert info.last_error is None and g.RATE_LIMITED in cli._counts(info.last_report)
        assert len(synced(world.store)) == events  # no event for an item that is not one
        assert shown(page_of(world.store), g.RATE_LIMITED)
    finally:
        world.store.close()


def test_a_github_pull_rate_limited_halfway_says_so_once_beside_what_it_wrote(tmp_path):
    mini = Mini()
    mini.routes[f"/repos/{REPO}/issues"] = (
        200,
        [issue_entry(3), issue_entry(4, finished=SINCE_G + 2 * DAY)],
    )
    mini.routes[f"/repos/{REPO}/issues/3/comments"] = (200, [])
    mini.routes[f"/repos/{REPO}/issues/4/comments"] = limited
    world = make_world(tmp_path, mini)
    try:
        report = run_github(world, G_NOW)
        assert (report.saved, report.skipped, report.reasons) == (1, 0, {})
        assert report.said == g.RATE_LIMITED
    finally:
        world.store.close()


def test_a_refused_search_is_said_in_stopped_and_is_no_skip():
    mini = Mini()
    mini.a_pull(3)
    mini.routes["/search/issues"] = (422, {"message": "Validation Failed"})
    got = g_pulled(mini)
    assert [d.item for d in got.drafts] == ["pull:3"] and got.skipped == []
    assert got.stopped == g.SEARCH_REFUSED.format(status=422)
    assert got.stopped.endswith(".") and "daily check's search" in got.stopped


def test_a_refused_search_and_a_rate_limit_are_said_in_one_sentence():
    """The seam carries one sentence (Pulled.stopped is one string), so a pull with two
    things to say says both in it, in the order they happened."""
    mini = Mini()
    mini.a_pull(3)
    mini.routes["/search/issues"] = (403, {"message": "Forbidden"})
    mini.routes[f"/repos/{REPO}/releases"] = limited
    got = g_pulled(mini)
    refused = g.SEARCH_REFUSED.format(status=403)
    assert got.stopped == f"{refused} {g.RATE_LIMITED}" and got.skipped == []
    assert got.drafts == []


def test_a_refused_search_then_a_rate_limit_halfway_says_both_and_keeps_what_it_wrote():
    mini = Mini()
    mini.routes[f"/repos/{REPO}/issues"] = (
        200,
        [issue_entry(3), issue_entry(4, finished=SINCE_G + 2 * DAY)],
    )
    mini.routes[f"/repos/{REPO}/issues/3/comments"] = (200, [])
    mini.routes[f"/repos/{REPO}/issues/4/comments"] = limited
    mini.routes["/search/issues"] = (403, {"message": "Forbidden"})
    got = g_pulled(mini)
    assert [d.item for d in got.drafts] == ["issue:3"] and got.skipped == []
    assert got.stopped == f"{g.SEARCH_REFUSED.format(status=403)} {g.RATE_LIMITED}"


def test_more_releases_than_a_pull_reads_is_said_in_stopped_and_is_no_skip():
    """K2: RELEASES_PAST travels as the rate limit and the refused search do. The frozen
    tests/acceptance/test_c11_github.py does not read it among the skips."""
    mini = Mini()
    pages: list[int] = []

    def releases(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        pages.append(page)
        rows = [release_entry(100 * page + k, finished=SINCE_G + DAY) for k in range(100)]
        following = f"https://api.github.com/repos/{REPO}/releases?per_page=100&page={page + 1}"
        return httpx.Response(200, json=rows, headers={"link": f'<{following}>; rel="next"'})

    mini.routes[f"/repos/{REPO}/releases"] = releases
    got = g_pulled(mini)
    assert len(pages) == g.MAX_LIST_PAGES
    assert got.skipped == [] and got.stopped == g.RELEASES_PAST
    assert g.RELEASES_PAST.endswith(".") and "this server reads in one pull" in g.RELEASES_PAST
    assert len(got.drafts) == g.PER_PULL


def test_a_search_past_its_results_stays_a_skip_as_the_acceptance_test_reads_it():
    mini = Mini()
    mini.routes["/search/issues"] = (200, searched(issue_entry(3), total=1_500))
    mini.routes[f"/repos/{REPO}/issues/3/comments"] = (200, [])
    got = g_pulled(mini)
    assert [s.reason for s in got.skipped] == [g.SEARCH_PAST] and got.stopped is None
