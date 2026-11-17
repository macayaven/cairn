"""Unit tests for the GitHub source's pull: what tests/acceptance/test_c11_github.py does not
pin (shapes, GitHub's own message, the report's sentences, the daily check refused for days,
a rate limit and long texts through the runner). HTTP goes through httpx.MockTransport; time
is injected. Every token, name and address here is made up."""

import asyncio
import inspect
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

import cairn.memory
import cairn.store.memory
from cairn.sources import SourceError, fit, run_source
from cairn.sources import github as g
from cairn.store import PAGE_CHARS_MAX, Store

VAR = "GITHUB_UNIT_TOKEN"
REPO = "quillworks/quill"
TOKEN = "github" + "_pat_" + "11UNITMADEUP0" + "_" + "a1b2c3" * 6  # never a real token
NOW = datetime(2099, 6, 10, 12, 0, tzinfo=UTC)
SINCE = datetime(2099, 6, 1, tzinfo=UTC)
DAY, HOUR = timedelta(days=1), timedelta(hours=1)
BASE = f"/repos/{REPO}"


def z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def user(login: str | None = "ada-park", kind: str = "User") -> dict | None:
    if login is None:
        return None
    return {"login": login, "type": kind, "name": "Ada Park", "email": "ada@example.test"}


def issue_entry(n: int, *, finished: datetime = SINCE + DAY, **given) -> dict:
    entry = {
        "number": n,
        "html_url": f"https://github.com/{REPO}/issues/{n}",
        "state": "closed",
        "state_reason": "completed",
        "title": f"Refunds stall {n}",
        "body": f"Item {n}.",
        "user": user(),
        "labels": [{"name": "bug"}],
        "comments": 0,
        "created_at": z(SINCE - 9 * DAY),
        "updated_at": z(finished),
        "closed_at": z(finished),
    }
    return {**entry, **given}


def pull_entry(n: int, *, finished: datetime = SINCE + DAY, **given) -> dict:
    given.setdefault("html_url", f"https://github.com/{REPO}/pull/{n}")
    given.setdefault("pull_request", {"merged_at": z(finished)})
    return issue_entry(n, finished=finished, state_reason=None, **given)


def pull_detail(n: int, **given) -> dict:
    return {"number": n, "base": {"ref": "main"}, "merged_by": user("brook-lane"), **given}


def release_entry(k: int, *, finished: datetime = SINCE + 2 * DAY, **given) -> dict:
    entry = {
        "id": 7_000 + k,
        "html_url": f"https://github.com/{REPO}/releases/tag/v{k}.0.0",
        "tag_name": f"v{k}.0.0",
        "name": f"Batch {k}",
        "body": "Retries back off.",
        "draft": False,
        "prerelease": False,
        "author": user(),
        "published_at": z(finished),
    }
    return {**entry, **given}


def searched(*items: dict, total: int | None = None, incomplete: bool = False) -> dict:
    count = len(items) if total is None else total
    return {"total_count": count, "incomplete_results": incomplete, "items": list(items)}


class Mini:
    """GitHub's answers by path: (status, JSON, headers), or a callable of the request."""

    def __init__(self) -> None:
        self.routes: dict = {
            f"{BASE}/issues": (200, []),
            "/search/issues": (200, searched()),
            f"{BASE}/releases": (200, []),
        }
        self.asked: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        route = self.routes.get(request.url.path, (404, {"message": "Not Found"}))
        if callable(route):
            return route(request)
        status, body, *rest = route
        headers = rest[0] if rest else {}
        if isinstance(body, bytes):
            return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.asked]

    def an_issue(self, n: int, *talk: dict, **given) -> None:
        given.setdefault("comments", len(talk))
        self.routes[f"{BASE}/issues"] = (200, [issue_entry(n, **given)])
        self.routes[f"{BASE}/issues/{n}/comments"] = (200, list(talk))

    def a_pull(self, n: int, comments=(), reviews=(), **given) -> None:
        entry = pull_entry(n, comments=len(comments), **given)
        self.routes[f"{BASE}/issues"] = (200, [entry])
        self.routes[f"{BASE}/pulls/{n}"] = (200, pull_detail(n))
        self.routes[f"{BASE}/issues/{n}/comments"] = (200, list(comments))
        self.routes[f"{BASE}/pulls/{n}/reviews"] = (200, list(reviews))


def source(mini: Mini, now: datetime = NOW, **given):
    http = httpx.AsyncClient(transport=httpx.MockTransport(mini.handler))
    config = g.check_config({"token_env": VAR, "repo": REPO, "projects": ["quill"], **given})
    return g.open_source(
        "code", config, http=http, env={VAR: TOKEN}, clock=lambda: now, since=SINCE
    )


def pulled(mini: Mini, cursor: str | None = None, now: datetime = NOW, **given):
    return asyncio.run(source(mini, now, **given).pull(cursor))


def failure(mini: Mini, **given) -> str:
    with pytest.raises(SourceError) as failed:
        pulled(mini, **given)
    return str(failed.value)


def comment(n: int, body: object = "A comment.", **given) -> dict:
    return {"id": n, "body": body, "user": user("cy-moss"), "created_at": z(SINCE), **given}


def review(n: int, state: object = "APPROVED", body: object = "Fine.", **given) -> dict:
    at = z(SINCE + HOUR)
    return {"id": n, "body": body, "state": state, "user": user(), "submitted_at": at, **given}


# Shapes: an answer not of the documented shape is the "malformed" failure, never another


ODD = [None, 7, "odd", [], {}, True]
LISTING_FIELDS = ["number", "html_url", "state", "updated_at", "closed_at", "state_reason",
                  "pull_request", "title", "body", "user", "labels", "comments"]  # fmt: skip
RELEASE_FIELDS = ["id", "html_url", "tag_name", "draft", "published_at", "name", "body",
                  "author", "prerelease"]  # fmt: skip


def outcome(mini: Mini) -> str:
    """ "malformed", "drafted", "skipped" or "left": what a pull made of what it was given;
    anything else (an exception that names nothing) fails the test."""
    try:
        got = pulled(mini)
    except SourceError as error:
        assert "malformed" in str(error) or "not JSON" in str(error), str(error)
        assert TOKEN not in str(error)
        return "malformed"
    if got.drafts:
        return "drafted"
    return "skipped" if got.skipped else "left"


@pytest.mark.parametrize("field", LISTING_FIELDS)
@pytest.mark.parametrize("pull", [False, True], ids=["issue", "pull"])
def test_every_field_of_the_listing_given_an_odd_value_is_written_skipped_or_malformed(field, pull):
    for odd in ODD:
        mini = Mini()
        if pull:
            mini.a_pull(3)
        else:
            mini.an_issue(3)
        mini.routes[f"{BASE}/issues"][1][0][field] = odd
        assert outcome(mini) in ("malformed", "drafted", "skipped", "left"), (field, odd)


@pytest.mark.parametrize("field", ["number", "base", "merged_by"])
def test_a_pull_requests_details_of_the_wrong_shape_are_malformed(field):
    for odd in [7, "odd", [], True] + ([None] if field != "merged_by" else []):
        mini = Mini()
        mini.a_pull(3)
        mini.routes[f"{BASE}/pulls/3"][1][field] = odd
        assert outcome(mini) == "malformed", (field, odd)


@pytest.mark.parametrize("field", ["created_at", "body", "user", "minimized", "id"])
def test_a_comment_of_any_shape_is_written_left_out_or_malformed(field):
    for odd in ODD:
        mini = Mini()
        mini.an_issue(3, comment(1, **{field: odd}))
        assert outcome(mini) in ("malformed", "drafted"), (field, odd)
    mini = Mini()
    mini.an_issue(3)
    mini.routes[f"{BASE}/issues/3/comments"] = (200, {"items": []})
    assert outcome(mini) == "malformed"


@pytest.mark.parametrize("field", ["body", "state", "submitted_at", "user"])
def test_a_review_of_any_shape_is_written_left_out_or_malformed(field):
    for odd in ODD:
        mini = Mini()
        mini.a_pull(3, reviews=[review(1, **{field: odd})])
        assert outcome(mini) in ("malformed", "drafted"), (field, odd)


@pytest.mark.parametrize("field", RELEASE_FIELDS)
def test_every_field_of_a_release_given_an_odd_value_is_written_left_out_or_malformed(field):
    for odd in ODD:
        mini = Mini()
        entry = release_entry(1)
        entry[field] = odd
        mini.routes[f"{BASE}/releases"] = (200, [entry])
        assert outcome(mini) in ("malformed", "drafted", "left"), (field, odd)


def test_answers_that_are_not_the_documented_shape_fail_the_pull_by_name():
    for path, body in (
        (f"{BASE}/issues", {"items": []}),
        (f"{BASE}/issues", ["an entry that is not an object"]),
        (f"{BASE}/releases", {"releases": []}),
        ("/search/issues", []),
        ("/search/issues", {"total_count": "1", "incomplete_results": False, "items": []}),
        ("/search/issues", {"total_count": 1, "incomplete_results": None, "items": []}),
        ("/search/issues", {"total_count": 1, "incomplete_results": False, "items": [7]}),
    ):
        mini = Mini()
        mini.routes[path] = (200, body)
        said = failure(mini)
        assert "malformed" in said and REPO in said, (path, body)
    mini = Mini()
    mini.routes[f"{BASE}/issues"] = (200, b"<html>not JSON</html>")
    assert "is not JSON" in failure(mini)


def test_an_answer_past_the_bound_fails_the_pull_without_reading_it_all(monkeypatch):
    monkeypatch.setattr(g, "MAX_RESPONSE_BYTES", 1_000)
    mini = Mini()
    mini.routes[f"{BASE}/issues"] = (200, [issue_entry(n) for n in range(1, 40)])
    assert "larger than this server reads" in failure(mini)


# GitHub's own message: shown only when it may be


@pytest.mark.parametrize(
    ("message", "shown"),
    [
        ("Validation Failed", True),
        ("Must have admin rights to Repository.", True),
        (f"Bad credentials ({TOKEN})", False),
        ("Bad credentials (" + "gh" + "p_" + "Unit" + "Ab12" * 6 + ")", False),  # a token's shape
        (f"Bad credentials (Bearer {TOKEN.upper()[:8]})", True),  # too short to be one
        ("Two\nlines", False),
        ("x" * 301, False),
        (7, False),
    ],
)
def test_githubs_message_is_shown_only_when_it_holds_no_credential(message, shown):
    said = g.shown_message(message, (TOKEN, f"Bearer {TOKEN}"))
    assert (said is not None) == shown
    mini = Mini()
    mini.routes[f"{BASE}/issues"] = (422, {"message": message})
    refused = failure(mini)
    assert ("GitHub said" in refused) == shown and TOKEN not in refused


def test_githubs_message_is_withheld_when_it_holds_the_token_encoded():
    import base64

    encoded = base64.b64encode(f"x:{TOKEN}".encode()).decode()
    assert g.shown_message(f"Refused {encoded}", (TOKEN, f"Bearer {TOKEN}")) is None
    assert g.shown_message(f"Refused {TOKEN.encode().hex()}", (TOKEN,)) is None


# What a refusal says


@pytest.mark.parametrize(
    ("path", "status", "words"),
    [
        (f"{BASE}/issues", 401, [VAR, REPO, "401"]),
        (f"{BASE}/issues", 404, [VAR, REPO, "permission", "approval"]),
        (f"{BASE}/releases", 404, [VAR, REPO, "Contents", "approval"]),
        (f"{BASE}/issues", 403, [VAR, "closed issues", "Issues and Pull requests"]),
        (f"{BASE}/pulls/3", 403, [VAR, "pull request #3", "Pull requests"]),
        (f"{BASE}/issues/3/comments", 403, ["comments of #3"]),
        (f"{BASE}/pulls/3/reviews", 403, ["reviews of pull request #3"]),
        (f"{BASE}/releases", 403, ["releases", "Contents"]),
        (f"{BASE}/issues", 301, ["redirect", "new owner/name"]),
        (f"{BASE}/pulls/3", 502, ["502", "the next run tries again"]),
    ],
)
def test_a_refusal_says_what_was_asked_and_what_to_do(path, status, words):
    mini = Mini()
    mini.a_pull(3, reviews=[review(1)])
    mini.routes[path] = (status, {"message": "Refused"}, {"x-ratelimit-remaining": "4000"})
    said = failure(mini)
    for word in words:
        assert word in said, (word, said)
    assert "rate limit" not in said and "cairn" not in said.lower()


def test_an_items_own_address_that_moved_skips_it_and_a_list_that_moved_fails():
    mini = Mini()
    mini.a_pull(3)
    mini.routes[f"{BASE}/pulls/3"] = (301, {"message": "Moved Permanently"})
    got = pulled(mini)
    assert got.drafts == [] and [s.reason for s in got.skipped] == [g.MOVED]
    mini = Mini()
    mini.an_issue(3)
    mini.routes[f"{BASE}/issues/3/comments"] = (404, {"message": "Not Found"})
    assert [s.reason for s in pulled(mini).skipped] == [g.GONE]


def test_a_network_error_names_its_class_and_nothing_else():
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused, with {TOKEN}", request=request)

    mini = Mini()
    mini.routes[f"{BASE}/issues"] = broken
    said = failure(mini)
    assert said.endswith("ConnectError") and TOKEN not in said


# The report's sentences


def test_the_sentences_a_pull_may_add_to_its_report_are_plain_and_name_no_product():
    sentences = [g.GONE, g.MOVED, g.UNREADABLE, g.RATE_LIMITED, g.SEARCH_PAST, g.RELEASES_PAST]
    sentences.append(g.SEARCH_REFUSED.format(status=403))
    sentences += [*g.CUT_LINES.values(), g.MORE_COMMENTS, g.MORE_REVIEWS, g.DELETED, g.SWITCH_ON]
    for sentence in sentences:
        assert "cairn" not in sentence.lower() and not set("_*") & set(sentence), sentence
    assert "slow down" in g.RATE_LIMITED and "next run" in g.RATE_LIMITED
    assert "daily check" in g.SEARCH_REFUSED and "search" in g.SEARCH_PAST


def test_no_string_this_module_writes_names_the_software():
    """B3: under a brand nothing a page, a run's reason or an error shows names the software."""
    text = inspect.getsource(g)
    quoted = [
        line
        for line in text.splitlines()
        if "cairn" in line.lower()
        and not line.lstrip().startswith(("from cairn", "import cairn", "#"))
        and '"' in line
    ]
    assert all("cairn." in line or "(cairn" in line for line in quoted), quoted


def test_a_refused_search_says_so_once_and_the_listings_finds_are_written():
    mini = Mini()
    mini.a_pull(3)
    mini.routes["/search/issues"] = (422, {"message": "Validation Failed"})
    got = pulled(mini)
    assert [d.item for d in got.drafts] == ["pull:3"] and got.skipped == []
    assert got.stopped == g.SEARCH_REFUSED.format(status=422)
    searches = [p for p in mini.paths() if p == "/search/issues"]
    assert len(searches) == 1  # the second query is not asked once the first was refused


def test_a_search_past_its_results_says_so():
    for given in (
        searched(issue_entry(3), total=1_500),
        searched(issue_entry(3), incomplete=True),
    ):
        mini = Mini()
        mini.routes["/search/issues"] = (200, given)
        mini.routes[f"{BASE}/issues/3/comments"] = (200, [])
        got = pulled(mini)
        assert [d.item for d in got.drafts] == ["issue:3"]
        assert g.SEARCH_PAST in [s.reason for s in got.skipped]


def test_with_a_backfill_the_listing_and_the_first_check_ask_from_the_floor():
    """K2: the floor is the switch-on less backfill_days; the listing's first since and the
    first check's window are OVERLAP before it, written as each writes a moment."""
    mini = Mini()
    pulled(mini, backfill_days=30)
    floor = SINCE - 30 * DAY
    (listing,) = [r for r in mini.asked if r.url.path == f"{BASE}/issues"]
    assert listing.url.params["since"] == g._bound(floor)
    windows = [r.url.params["q"] for r in mini.asked if r.url.path == "/search/issues"]
    assert len(windows) == 2
    assert all(q.endswith(f">={g._qualifier(floor - g.OVERLAP)}") for q in windows), windows


def test_a_config_stored_before_backfill_days_opens_and_reads_as_before():
    """A source added before K2 has no backfill_days in its stored config: it reads from its
    switch-on, as a backfill of 0 does."""
    mini = Mini()
    mini.routes[f"{BASE}/issues"] = (
        200,
        [issue_entry(3, finished=SINCE - DAY), issue_entry(4, finished=SINCE + DAY)],
    )
    config = g.check_config({"token_env": VAR, "repo": REPO, "projects": ["quill"]})
    config["comments"] = False
    del config["backfill_days"]
    http = httpx.AsyncClient(transport=httpx.MockTransport(mini.handler))
    opened = g.open_source(
        "code", config, http=http, env={VAR: TOKEN}, clock=lambda: NOW, since=SINCE
    )
    got = asyncio.run(opened.pull(None))
    assert [d.item for d in got.drafts] == ["issue:4"]
    (listing,) = [r for r in mini.asked if r.url.path == f"{BASE}/issues"]
    assert listing.url.params["since"] == g._bound(SINCE)
    assert g.KIND.switching_on(config=config) == g.SWITCH_ON


def test_with_a_backfill_a_release_is_taken_from_the_floor():
    mini = Mini()
    mini.routes[f"{BASE}/releases"] = (
        200,
        [release_entry(1, finished=SINCE - 40 * DAY), release_entry(2, finished=SINCE - 9 * DAY)],
    )
    assert pulled(mini).drafts == []
    got = pulled(mini, backfill_days=30)
    assert [d.item for d in got.drafts] == ["release:7002"]


def test_a_search_refused_for_days_reaches_back_to_since_when_it_is_allowed_again():
    """lookback_days 1: the daily check is refused three days running, then allowed; it
    reaches back to `since`, and the issue closed while it was refused is written."""
    mini = Mini()
    hidden = issue_entry(3, finished=SINCE + 2 * HOUR, updated_at=z(SINCE - 9 * DAY))
    mini.routes[f"{BASE}/issues/3/comments"] = (200, [])
    cursor, bounds = None, []

    def search(request: httpx.Request) -> httpx.Response:
        bounds.append(request.url.params["q"].split(">=")[1])
        if len(bounds) <= 3:
            return httpx.Response(403, json={"message": "Forbidden"})
        return httpx.Response(200, json=searched(hidden))

    mini.routes["/search/issues"] = search
    written = []
    for day in range(4):
        got = pulled(mini, cursor, NOW + day * DAY + HOUR, lookback_days=1)
        cursor = got.cursor
        written += [d.item for d in got.drafts]
        if day < 3:
            assert got.drafts == [] and "refused" in got.stopped
    assert written == ["issue:3"]
    assert set(bounds) == {"2099-05-31T23:00:00+00:00"}  # since, overlapped, every time
    assert len(bounds) == 5  # refused three times on the first query; then both queries
    assert json.loads(cursor)["checked"] is not None


def test_a_check_not_finished_keeps_every_digest_it_may_need():
    mini = Mini()
    settling = issue_entry(4, finished=NOW - 5 * timedelta(minutes=1))
    old = issue_entry(3, finished=SINCE + HOUR)
    mini.routes["/search/issues"] = (200, searched(old, settling))
    mini.routes[f"{BASE}/issues/3/comments"] = (200, [])
    mini.routes[f"{BASE}/issues/4/comments"] = (200, [])
    first = pulled(mini, lookback_days=1)
    assert [d.item for d in first.drafts] == ["issue:3"]
    later = NOW + 5 * DAY  # past lookback_days: the digest would be let go
    still = issue_entry(5, finished=later - 5 * timedelta(minutes=1))  # the check stays open
    mini.routes["/search/issues"] = (200, searched(old, still))
    again = pulled(mini, first.cursor, later, lookback_days=1)
    assert again.drafts == [] and json.loads(again.cursor)["pending"] is not None
    mini.routes["/search/issues"] = (200, searched(old))
    last = pulled(mini, again.cursor, later + HOUR, lookback_days=1)
    assert last.drafts == []  # the pending check found issue 3 again: still known


# Through the runner


def make_world(tmp_path, mini: Mini, **settings):
    clock = SimpleNamespace(now=SINCE - DAY)
    store = Store(tmp_path / "cairn.db", clock=lambda: clock.now)
    store.create_project("quill")
    config = {"token_env": VAR, "repo": REPO, "projects": ["quill"], **settings}
    store.add_source("code", "github", config)
    clock.now = SINCE
    store.enable_source("code")
    http = httpx.AsyncClient(transport=httpx.MockTransport(mini.handler))
    return SimpleNamespace(store=store, http=http)


def run(world, at: datetime):
    return asyncio.run(
        run_source(world.store, "code", http=world.http, env={VAR: TOKEN}, clock=lambda: at)
    )


def limited(request: httpx.Request) -> httpx.Response:
    return httpx.Response(429, json={"message": "slow down"}, headers={"retry-after": "60"})


def test_a_rate_limit_with_nothing_finished_is_no_failed_run_and_moves_no_cursor(tmp_path):
    mini = Mini()
    mini.a_pull(3)
    world = make_world(tmp_path, mini)
    try:
        assert run(world, NOW).saved == 1
        before = world.store.get_source("code").cursor
        mini.routes[f"{BASE}/issues"] = limited
        report = run(world, NOW + HOUR)
        after = world.store.get_source("code")
        assert (report.saved, after.cursor, after.last_error) == (0, before, None)
        assert report.reasons == {} and report.said == g.RATE_LIMITED
    finally:
        world.store.close()


def test_a_rate_limit_halfway_keeps_what_was_written_and_says_so(tmp_path):
    mini = Mini()
    mini.routes[f"{BASE}/issues"] = (
        200,
        [issue_entry(3), issue_entry(4, finished=SINCE + 2 * DAY)],
    )
    mini.routes[f"{BASE}/issues/3/comments"] = (200, [])
    mini.routes[f"{BASE}/issues/4/comments"] = limited
    world = make_world(tmp_path, mini)
    try:
        report = run(world, NOW)
        assert report.saved == 1 and report.reasons == {} and report.said == g.RATE_LIMITED
        assert world.store.get_source("code").last_error is None
        mini.routes[f"{BASE}/issues/4/comments"] = (200, [])
        assert run(world, NOW + HOUR).saved == 1
    finally:
        world.store.close()


def test_a_body_and_comments_of_any_length_are_written_or_skipped_never_a_failed_run(
    tmp_path, monkeypatch
):
    fence = "```\n" + "\n".join(f"line {i:06d} of the dump" for i in range(40_000)) + "\n```"
    long_one = "word " * 30_000  # one paragraph of 150,000 characters
    mini = Mini()
    mini.a_pull(3, body=fence, comments=[comment(n, long_one) for n in range(1, 41)])
    # K4: the note is cut to fit a page by both of its bounds (cairn.sources.fit), so it is
    # saved, where under G1 alone it was skipped as a full page.
    (tmp_path / "bound").mkdir()
    world = make_world(tmp_path / "bound", mini)
    try:
        report = run(world, NOW)
        assert (report.saved, report.skipped) == (1, 0), report
        (held,) = world.store.list_notes(world.store.get_project("quill").id)
        assert fit.measure(held.text)[1] <= PAGE_CHARS_MAX < len(fence) + 40 * len(long_one)
        assert "line 000000 of the dump" in held.text and g.CUT_LINES["pull"] in held.text
        assert "more comments are left out." in held.text
    finally:
        world.store.close()
    # A page's bound lowered moves the cut with it.
    monkeypatch.setattr(cairn.store.memory, "PAGE_CHARS_MAX", 60_000)
    monkeypatch.setattr(cairn.memory, "PAGE_CHARS_MAX", 60_000)
    (tmp_path / "lowered").mkdir()
    world = make_world(tmp_path / "lowered", mini)
    try:
        report = run(world, NOW)
        assert (report.saved, report.skipped) == (1, 0), report
        (held,) = world.store.list_notes(world.store.get_project("quill").id)
        assert fit.measure(held.text)[1] <= 60_000 and g.CUT_LINES["pull"] in held.text
    finally:
        world.store.close()


# People and the conversation


def test_a_person_is_their_login_a_bot_says_so_and_a_deleted_account_is_said_one_way():
    assert g.person(user("ada-park")) == "ada-park"
    assert g.person(user("ada_park_emu")) == "ada_park_emu"  # a managed user's login
    assert g.person(user("quill-ci[bot]", "Bot")) == "quill-ci[bot] (bot)"
    for gone in (None, user("ghost"), user("Ghost"), {"login": None}, {"login": " "}, 7):
        assert g.person(gone) == g.DELETED
    assert g.person(user("ada@example.test")) == g.DELETED  # never an address


def test_the_conversation_says_each_verdict_in_words_and_leaves_out_what_has_no_text():
    mini = Mini()
    mini.a_pull(
        3,
        comments=[
            comment(1, "First."),
            comment(2, None),
            comment(3, "Hidden.", minimized={"reason": "spam"}),
            comment(4, "Last."),
        ],
        reviews=[
            review(5, "CHANGES_REQUESTED", "Split it."),
            review(6, "DISMISSED", "Was wrong."),
            review(7, "COMMENTED", "A thought."),
            review(8, "PENDING", "Not sent."),
            review(9, "APPROVED", ""),
        ],
    )
    (draft,) = pulled(mini).drafts
    text = draft.text
    assert "ada-park asked for changes on 2099-06-01 01:00 UTC:\n\nSplit it." in text
    assert "ada-park reviewed (a review since dismissed) on" in text
    assert "ada-park reviewed on 2099-06-01 01:00 UTC:\n\nA thought." in text
    for absent in ("Not sent.", "Hidden.", "PENDING", "APPROVED", "approved"):
        assert absent not in text
    assert text.index("First.") < text.index("Last.") < text.index("Split it.")
    assert "left out" not in text  # one without text, and one hidden, are not left out


def test_what_is_left_out_of_a_conversation_is_said_in_plain_text():
    talk = g.Talk(
        said=[g.comment_said(comment(n, f"Comment {n}."), n) for n in range(5)],
        comments_read=5,
        comments_total=1_234,
        comments_more=True,
        reviews_more=True,
    )
    text = g.note_text("t", ["Opened by: x"], None, "issue", g.GITHUB_DOMAINS, talk)
    assert "1,229 more comments are left out.\n\nMore reviews are left out." in text
    talk = g.Talk(said=[], comments_read=100, comments_total=100, comments_more=True)
    text = g.note_text("t", [], "", "issue", g.GITHUB_DOMAINS, talk)
    assert text.endswith("## Comments\n\nMore comments are left out.\n")
    assert g.note_text("t", [], None, "issue", g.GITHUB_DOMAINS, g.Talk([], 0, 0)) == "# t\n"
