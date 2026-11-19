"""Unit tests for the Linear source: what tests/acceptance/test_c10_linear.py does not pin.
HTTP goes through httpx.MockTransport; the clock and sleep are injected. Every key, name,
team and address here is made up."""

import asyncio
import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cairn.sources import KINDS, SourceError, run_source, source_client
from cairn.sources import linear as lin
from cairn.spans import chunk
from cairn.store import PAGE_CLAIMS_MAX, Store

VAR = "LINEAR_UNIT_KEY"
KEY = "lin-unit-" + "made-up-key-" + "4f2a9c1e"  # never a real key
NOW = datetime(2099, 6, 1, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=20)
HOUR, DAY = timedelta(hours=1), timedelta(days=1)
BASE = "https://linear.example.test/graphql"
PROJECT = "0b7c6a52-3f1e-4c2d-9a8b-5e6f7a8b9c0d"
TOKEN = "ghp_" + "UnitMadeUpTokenaB3dE5gH7jK9mN1pQ2rS4tU6"  # the shape of a credential


def settings(**given) -> dict:
    return {"key_env": VAR, "projects": ["linear"], "base_url": BASE, **given}


def refusal(config) -> str:
    with pytest.raises(ValueError) as refused:
        lin.check_config(config)
    return str(refused.value)


# The kind and its config, setting by setting


def test_the_kind_is_registered_starts_off_and_writes_with_its_own_via():
    kind = KINDS["linear"]
    assert kind is lin.KIND and kind.starts_off and kind.via == "linear"
    assert kind.check is lin.check_config and kind.open is lin.open_source
    assert kind.default_project == "linear" and kind.withheld is True


def test_the_defaults_are_filled_in_and_what_is_stored_checks_the_same():
    checked = lin.check_config({"key_env": VAR})
    assert checked == {
        "key_env": VAR,
        "projects": [],
        "base_url": "https://api.linear.app/graphql",
        "comments": True,
        "settle_minutes": 30,
        "lookback_days": 30,
        "backfill_days": 0,
    }
    assert lin.check_config(checked) == checked


def test_a_null_teams_or_project_is_left_out_and_a_given_one_kept_as_given():
    assert "teams" not in lin.check_config(settings(teams=None))
    assert "linear_project" not in lin.check_config(settings(linear_project=None))
    upper = PROJECT.upper()
    assert lin.check_config(settings(linear_project=upper))["linear_project"] == upper
    assert lin.check_config(settings(teams=["eng", "Ops_2"]))["teams"] == ["eng", "Ops_2"]


def test_base_url_loses_a_trailing_slash_and_keeps_its_path():
    assert lin.check_config(settings(base_url=BASE + "/"))["base_url"] == BASE
    assert lin.check_config(settings(base_url="http://[::1]:8123/graphql"))["base_url"] == (
        "http://[::1]:8123/graphql"
    )


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"key_env": "linear_key"}, "key_env names an environment variable"),
        ({"key_env": "A" * 129}, "key_env names an environment variable"),
        ({"projects": ["linear", "work"]}, "projects names at most one project"),
        ({"projects": [""]}, "projects holds a project's name"),
        ({"projects": "linear"}, "projects is a list"),
        ({"teams": []}, "teams is a list of 1 to 20 team keys"),
        ({"teams": [f"T{n}" for n in range(21)]}, "teams is a list of 1 to 20 team keys"),
        ({"teams": "ENG"}, "teams is a list of 1 to 20 team keys"),
        ({"teams": ["ENG-1"]}, "each of teams is a team's key"),
        ({"teams": ["ABCDEFGHIJK"]}, "each of teams is a team's key"),
        ({"teams": [True]}, "each of teams is a team's key"),
        ({"linear_project": "refund-revamp"}, "linear_project is one Linear project's id"),
        ({"linear_project": PROJECT + "0"}, "linear_project is one Linear project's id"),
        ({"comments": 1}, "comments is true or false"),
        ({"settle_minutes": True}, "settle_minutes is a whole number from 0 to 1440"),
        ({"settle_minutes": 2.5}, "settle_minutes is a whole number from 0 to 1440"),
        ({"lookback_days": None}, "lookback_days is a whole number from 1 to 365"),
        ({"backfill_days": -1}, "backfill_days is a whole number from 0 to 365"),
        ({"backfill_days": 366}, "backfill_days is a whole number from 0 to 365"),
        ({"backfill_days": False}, "backfill_days is a whole number from 0 to 365"),
        ({"base_url": "https://linear.example.test/graphql?x=1"}, "base_url has no query"),
        ({"base_url": "https://linear.example.test/graphql#top"}, "base_url has no query"),
        ({"base_url": "//linear.example.test/graphql"}, "base_url is https"),
        ({"base_url": "http://linear.example.test/graphql"}, "base_url is https"),
        ({"base_url": 7}, "base_url is the address"),
    ],
)
def test_each_refusal_names_its_setting_and_says_what_it_takes(given, said):
    assert said in refusal(settings(**given))


def test_both_teams_and_a_project_are_refused_naming_both():
    said = refusal(settings(teams=["ENG"], linear_project=PROJECT))
    assert "teams" in said and "linear_project" in said


def test_a_missing_key_env_and_an_unknown_setting_are_named():
    assert "needs key_env" in refusal({"projects": []})
    said = refusal(settings(colour="blue"))
    assert "'colour'" in said and "key_env" in said  # and it lists the settings it takes


@pytest.mark.parametrize(
    "config",
    [
        settings(key_env=TOKEN),
        settings(projects=[TOKEN]),
        settings(base_url=f"https://linear.example.test/{TOKEN}/graphql"),
        settings(base_url=f"https://{TOKEN}.example.test/graphql"),
        settings(base_url=f"https://c10:{TOKEN}@linear.example.test/graphql"),
        settings(teams=[TOKEN]),
        settings(linear_project=TOKEN),
        settings(**{TOKEN: True}),
        settings(api_key=TOKEN),
    ],
)
def test_a_credential_anywhere_in_the_config_is_refused_and_never_repeated(config):
    said = refusal(config)
    assert TOKEN not in said and TOKEN[4:20] not in said


def test_describe_says_what_is_read_without_a_credential_or_the_software_name():
    plain = lin.describe(lin.check_config(settings()))
    assert "every team the key can read" in plain and "with their comments" in plain
    assert "30 minutes" in plain and BASE in plain
    teams = lin.describe(lin.check_config(settings(teams=["ENG", "OPS"], comments=False)))
    assert "ENG, OPS" in teams and "without their comments" in teams
    project = lin.describe(lin.check_config(settings(linear_project=PROJECT, settle_minutes=0)))
    assert PROJECT in project and "as soon as it is listed" in project
    one = lin.describe(lin.check_config(settings(settle_minutes=1)))
    assert "1 minute " in one or one.endswith("1 minute")
    for said in (plain, teams, project, one, KINDS["linear"].switching_on("Knowledge Base")):
        assert "cairn" not in said.lower()
    assert "finished" in KINDS["linear"].switching_on()


def test_a_backfill_is_said_in_days_and_a_single_day_as_the_day():
    """K2: describe and the switch-on sentence say the backfill when it is not 0."""
    kind = KINDS["linear"]
    for days, words in ((30, "in the 30 days before"), (1, "in the day before")):
        config = lin.check_config(settings(backfill_days=days))
        for said in (lin.describe(config), kind.switching_on("Knowledge Base", config)):
            assert f"what finished {words} it is switched on" in said, said
            assert "cairn" not in said.lower()
    plain = lin.check_config(settings())
    assert kind.switching_on(config=plain) == lin.SWITCH_ON == kind.switching_on()
    assert lin.describe(plain) == lin.describe(
        {k: v for k, v in plain.items() if k != "backfill_days"}
    )


# A small Linear: the two operations over a list of issues, newest first, every request kept


def iid(n: int) -> str:
    return str(uuid.UUID(int=n, version=4))


def z(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def issue(n: int, finished: datetime, **given) -> dict:
    made = {
        "id": iid(n),
        "identifier": f"ENG-{n}",
        "title": f"Issue {n}",
        "description": None,
        "url": f"https://linear.example.test/unit/issue/ENG-{n}",
        "completedAt": z(finished),
        "canceledAt": None,
        "trashed": None,
        "state": {"name": "Done", "type": "completed"},
        "team": {"key": "ENG", "name": "Engineering"},
        "project": None,
        "assignee": None,
        "creator": None,
        "labels": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "comments": [],
    }
    made.update(given)
    return made


def comment(n: int, body: str, at: datetime, **given) -> dict:
    made = {
        "id": f"c-{n:05d}",
        "body": body,
        "createdAt": z(at),
        "user": {"name": "Ada Park", "displayName": "adapark"},
        "botActor": None,
        "externalUser": None,
        "parent": None,
    }
    made.update(given)
    return made


class Linear:
    """Answers FinishedIssues and IssueComments from `issues` (finish at or after the
    filter's T, newest first, pages of 25) and `answers` (forced answers, in order)."""

    def __init__(self, issues=(), clock=None) -> None:
        self.issues = list(issues)
        self.answers: list = []  # forced answers for the next requests, by operation
        self.requests: list[dict] = []
        self.clock = clock

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        for k, (op, answer) in enumerate(self.answers):
            if op == body["operationName"]:
                del self.answers[k]
                return answer() if callable(answer) else answer
        if body["operationName"] == lin.LISTING:
            return self.listing(body["variables"])
        return self.comments(body["variables"])

    def page(self, nodes, offset, size):
        chunk_ = nodes[offset : offset + size]
        more = offset + size < len(nodes)
        return {
            "nodes": chunk_,
            "pageInfo": {"hasNextPage": more, "endCursor": str(offset + size) if more else None},
        }

    def listing(self, variables):
        T = z(datetime.fromisoformat(variables["filter"]["or"][0]["completedAt"]["gte"]))

        def finished(i: dict) -> str:  # a broken time is listed, last
            return str(i["completedAt"] or i["canceledAt"])

        found = [
            {k: v for k, v in i.items() if k != "comments"}
            for i in self.issues
            if finished(i) >= T or not finished(i).startswith("2")
        ]
        found.sort(key=finished, reverse=True)
        offset = int(variables.get("after") or 0)
        return httpx.Response(200, json={"data": {"issues": self.page(found, offset, 25)}})

    def comments(self, variables):
        (found,) = [i for i in self.issues if i["id"] == variables["id"]]
        newest = sorted(found["comments"], key=lambda c: (c["createdAt"], c["id"]), reverse=True)
        offset = int(variables.get("after") or 0)
        connection = self.page(newest, offset, 50)
        return httpx.Response(200, json={"data": {"issue": {"comments": connection}}})

    def ops(self) -> list[str]:
        return [r["operationName"] for r in self.requests]


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def opened(stand_in: Linear, *, clock=None, deadline=None, since=SINCE, **given):
    config = lin.check_config({"key_env": VAR, "projects": ["linear"], "base_url": BASE, **given})
    http = httpx.AsyncClient(transport=httpx.MockTransport(stand_in))
    return lin.open_source(
        "tracker", config, http=http, env={VAR: KEY}, clock=clock or Clock(NOW),
        deadline=deadline, since=since,
    )  # fmt: skip


def pull(source, cursor=None):
    return asyncio.run(source.pull(cursor))


def graphql_error(status: int, message: str, **extensions) -> httpx.Response:
    error = {"message": message, "extensions": extensions}
    return httpx.Response(status, json={"errors": [error], "data": None})


# The note: cuts, bounds and who wrote what


def test_a_long_text_is_cut_at_the_last_paragraph_break_within_the_bound():
    text = "\n\n".join(["first paragraph", "second paragraph", "third paragraph"])
    shown, left, kept = lin.cut(text, 40, 100)
    assert shown == "first paragraph\n\nsecond paragraph" and kept == 2
    assert left == len(text) - len(shown)
    shown, _, kept = lin.cut(text, 1_000, 1)  # by spans: one passage only
    assert shown == "first paragraph" and kept == 1
    assert lin.cut(text, 1_000, 3) == (text, 0, 3)  # within both: whole


def test_a_text_with_no_paragraph_break_within_the_bound_is_cut_at_the_bound():
    text = "x" * 50 + "\n\n" + "y" * 10
    shown, left, _ = lin.cut(text, 20, 100)
    assert shown == "x" * 20 and left == len(text) - 20


def test_the_lines_this_module_writes_are_plain_and_state_their_number_plainly():
    long = "\n\n".join(f"Paragraph {k}: " + "lorem ipsum " * 30 for k in range(2_000))
    made = lin.parse_issue(issue(1, NOW - DAY, description=long))
    many = [
        lin.parse_comment(comment(k, "Short.", NOW - DAY + k * timedelta(seconds=1)))
        for k in range(2_345)
    ]
    text = lin.note_text(made, many)
    lines = [ln for ln in text.splitlines() if "cut here" in ln or "more comments" in ln]
    assert len(lines) == 2
    for line in lines:
        assert "_" not in line and "*" not in line
        assert not re.search(r"\d,\d", line)  # 2145, never 2,145
    assert "The issue has 2145 more comments, not written here." in lines[1]


def test_the_title_is_cut_at_a_word_with_an_ellipsis_and_the_heading_by_bytes():
    title = " ".join(["reconcile"] * 40)
    cut_title = lin.note_title("ENG-7", title)
    assert len(cut_title) <= 200 and cut_title.startswith("ENG-7: reconcile")
    assert cut_title.endswith("reconcile…")
    assert lin.note_title("ENG-7", "Short") == "ENG-7: Short"
    wide = lin.heading("ENG-8: " + "漢字 " * 100)  # three bytes a character
    assert len(wide.encode()) <= lin.HEADING_BYTES and wide.endswith("…")


def test_who_wrote_a_comment_when_a_name_is_missing_or_holds_an_address():
    def author(**given) -> str:
        return lin.parse_comment(comment(1, "Hi.", NOW, **given)).author

    assert author(user={"name": "Brook Lane", "displayName": "brook@example.test"}) == (
        "Brook Lane"
    )
    assert author(user={"name": "dee@example.test", "displayName": ""}) == (
        "Someone whose name is not shown"
    )
    assert author(user=None) == "Someone Linear did not record"
    assert author(user=None, botActor={"name": None, "subType": "slack"}) == "A bot (slack)"
    assert author(user=None, botActor={"name": "Relay", "subType": None}) == "Relay"
    outside = {"name": "Ola Berg", "displayName": None}
    assert author(user=None, externalUser=outside) == "Ola Berg from outside the workspace"
    nameless = {"name": "ola@example.test", "displayName": None}
    assert author(user=None, externalUser=nameless) == "Someone outside the workspace"


def test_a_reply_whose_root_is_gone_stands_by_its_time_and_still_reads_as_a_reply():
    root = lin.parse_comment(comment(1, "Root.", NOW))
    orphan = lin.parse_comment(comment(2, "Orphan.", NOW - HOUR, parent={"id": "c-gone"}))
    reply = lin.parse_comment(comment(3, "Reply.", NOW + HOUR, parent={"id": "c-00001"}))
    assert [c.id for c in lin.ordered([reply, root, orphan])] == ["c-00002", "c-00001", "c-00003"]
    text = lin.note_text(lin.parse_issue(issue(1, NOW)), lin.ordered([reply, root, orphan]))
    assert text.index("adapark replied") < text.index("Orphan.") < text.index("Root.")


def worst(n: int) -> dict:
    """An issue as long as Linear can make one: a description of 3,000 paragraphs and 1,000
    comments of many short paragraphs and headings each."""
    description = "\n\n".join(f"Paragraph {k}: " + "lorem ipsum " * 18 for k in range(3_000))
    body = "\n\n".join(f"## Part {k}\n\nPoint {k}." for k in range(300))
    finished = NOW - DAY
    return issue(
        n,
        finished,
        description=description,
        comments=[comment(k, body, finished - k * timedelta(minutes=1)) for k in range(1_000)],
    )


def test_the_longest_issue_fits_its_page_through_the_runner(tmp_path):
    made = lin.parse_issue(worst(1))
    raw = worst(1)["comments"]
    text = lin.note_text(made, lin.ordered([lin.parse_comment(c) for c in raw]))
    spans = chunk("cap_000000000000", text)
    assert len(spans) <= lin.NOTE_PASSAGES + 10 < PAGE_CLAIMS_MAX

    clock = Clock(SINCE - DAY)
    store = Store(tmp_path / "cairn.db", clock=clock)
    try:
        store.create_project("linear")
        store.add_source("tracker", "linear", {"key_env": VAR, "projects": [], "base_url": BASE})
        clock.now = SINCE  # the switch-on's moment
        store.enable_source("tracker", projects=["linear"])
        stand_in = Linear([worst(1)])
        report = asyncio.run(
            run_source(
                store, "tracker", http=source_client(httpx.MockTransport(stand_in)),
                env={VAR: KEY}, clock=Clock(NOW),
            )
        )  # fmt: skip
        assert (report.saved, report.rejected, report.malformed, report.skipped) == (1, 0, 0, 0)
        assert report.reasons == {}
    finally:
        store.close()


# What a pull reads


def test_issues_the_rescan_finds_past_a_pull_s_bound_leave_the_rescan_to_run_again():
    clock = Clock(NOW)
    late = [issue(n, SINCE + n * HOUR) for n in range(1, 31)]  # found only by a rescan
    stand_in = Linear([], clock)
    source = opened(stand_in, clock=clock)
    first = pull(source)  # nothing yet: the mark moves to NOW less the settle
    assert first.drafts == []
    stand_in.issues = late
    clock.now = NOW + 2 * DAY
    second = pull(opened(stand_in, clock=clock), first.cursor)
    assert len(second.drafts) == 25
    assert json.loads(second.cursor)["rescan"] == json.loads(first.cursor)["rescan"]
    clock.now += HOUR
    third = pull(opened(stand_in, clock=clock), second.cursor)
    assert [d.item for d in third.drafts] == [iid(n) for n in range(26, 31)]
    assert json.loads(third.cursor)["rescan"] == clock.now.isoformat()


def test_with_a_backfill_the_daily_rescan_reaches_back_to_the_floor_not_the_switch_on():
    """K2: the floor (the switch-on less backfill_days) is where a rescan stops, when it is
    later than lookback_days ago."""
    clock = Clock(NOW)
    since = NOW - 90 * DAY
    stand_in = Linear([], clock)
    first = pull(opened(stand_in, clock=clock, since=since, backfill_days=30, lookback_days=365))
    floor = since - 30 * DAY
    assert stand_in.requests[0]["variables"]["filter"]["or"][0]["completedAt"]["gte"] == (
        lin._stamp(floor - lin.OVERLAP)
    )
    clock.now = NOW + DAY
    stand_in.requests.clear()
    pull(
        opened(stand_in, clock=clock, since=since, backfill_days=30, lookback_days=365),
        first.cursor,
    )
    bounds = [r["variables"]["filter"]["or"][0]["completedAt"]["gte"] for r in stand_in.requests]
    assert bounds[-1] == lin._stamp(floor - lin.OVERLAP)


def test_a_config_stored_before_backfill_days_opens_and_reads_as_before():
    """A source added before K2 has no backfill_days in its stored config: it reads from its
    switch-on, as a backfill of 0 does."""
    config = lin.check_config(settings())
    del config["backfill_days"]
    stand_in = Linear([issue(1, SINCE - DAY), issue(2, SINCE + DAY)])
    http = httpx.AsyncClient(transport=httpx.MockTransport(stand_in))
    source = lin.open_source(
        "tracker", config, http=http, env={VAR: KEY}, clock=Clock(NOW), since=SINCE
    )
    assert [d.item for d in pull(source).drafts] == [iid(2)]
    assert lin.describe(config) == lin.describe(lin.check_config(settings()))
    assert KINDS["linear"].switching_on(config=config) == lin.SWITCH_ON


def test_a_scan_past_its_pages_names_backfill_days_among_what_to_lower(monkeypatch):
    monkeypatch.setattr(lin, "MAX_LIST_PAGES", 1)
    stand_in = Linear([issue(n, SINCE - n * HOUR) for n in range(1, 30)])
    with pytest.raises(SourceError) as failed:
        pull(opened(stand_in, backfill_days=10))
    assert "backfill_days" in str(failed.value) and "lookback_days" in str(failed.value)


def test_an_issue_it_fails_on_is_skipped_once_and_not_proposed_again(monkeypatch):
    stand_in = Linear([issue(1, NOW - DAY), issue(2, NOW - 2 * DAY)])
    real = lin.make_draft

    def failing(made, comments, project):
        if made.id == iid(2):
            raise RuntimeError("a rendering fault")
        return real(made, comments, project)

    monkeypatch.setattr(lin, "make_draft", failing)
    first = pull(opened(stand_in))
    assert [d.item for d in first.drafts] == [iid(1)]
    assert [(s.ref, s.reason) for s in first.skipped] == [(iid(2), lin.UNREADABLE)]
    assert "cairn" not in lin.UNREADABLE
    monkeypatch.setattr(lin, "make_draft", real)
    again = pull(opened(stand_in, clock=Clock(NOW + HOUR)), first.cursor)
    assert again.drafts == [] and again.skipped == []


def test_more_comments_than_it_reads_or_comments_too_large_leave_the_note_without_them(
    monkeypatch,
):
    many = issue(1, NOW - DAY, comments=[comment(k, "Hi.", NOW - DAY) for k in range(120)])
    monkeypatch.setattr(lin, "MAX_COMMENT_PAGES", 2)
    (draft,) = pull(opened(Linear([many]))).drafts
    assert "more than this server reads, so none of them is written" in draft.text
    assert "Hi." not in draft.text
    monkeypatch.undo()

    stand_in = Linear([issue(2, NOW - DAY)])
    stand_in.answers.append((lin.COMMENTS, httpx.Response(200, content=b" " * (9 << 20))))
    (draft,) = pull(opened(stand_in)).drafts
    assert lin.TOO_LARGE_COMMENTS in draft.text


def test_a_listing_past_the_response_bound_fails_naming_the_operation():
    stand_in = Linear([issue(1, NOW - DAY)])
    stand_in.answers.append((lin.LISTING, httpx.Response(200, content=b" " * (9 << 20))))
    with pytest.raises(SourceError) as failed:
        pull(opened(stand_in))
    assert lin.LISTING in str(failed.value) and "larger than this server reads" in str(failed.value)


def test_at_its_deadline_a_pull_returns_what_it_finished_and_raises_when_nothing():
    clock = Clock(NOW)
    stand_in = Linear([issue(1, NOW - 2 * DAY), issue(2, NOW - DAY)])

    def tick():
        clock.now += timedelta(minutes=3)
        return httpx.Response(200, json={"data": {"issue": {"comments": {
            "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}})  # fmt: skip

    stand_in.answers.append((lin.COMMENTS, tick))
    got = pull(opened(stand_in, clock=clock, deadline=NOW + timedelta(minutes=2)))
    assert [d.item for d in got.drafts] == [iid(1)]
    assert stand_in.ops() == [lin.LISTING, lin.COMMENTS]  # none started after the deadline
    rest = pull(opened(stand_in, clock=clock), got.cursor)
    assert [d.item for d in rest.drafts] == [iid(2)]
    with pytest.raises(SourceError) as late:
        pull(opened(Linear([issue(3, NOW - DAY)]), clock=clock, deadline=NOW))
    assert "deadline" in str(late.value)


# Shapes and refusals


@pytest.mark.parametrize(
    ("broken", "said"),
    [
        ({"id": "not-a-uuid"}, "an issue's id"),
        ({"identifier": "ENG 1"}, "an issue's identifier"),
        ({"url": "ftp://linear.example.test/x"}, "an issue's url"),
        ({"completedAt": "yesterday"}, "an issue's completedAt"),
        ({"trashed": "yes"}, "an issue's trashed"),
        ({"state": None}, "an issue's state"),
        ({"labels": {"nodes": None, "pageInfo": {"hasNextPage": False}}}, "an issue's labels"),
        ({"assignee": {"name": 7}}, "an issue's assignee"),
    ],
)
def test_an_issue_not_of_the_documented_shape_fails_the_pull_naming_the_part(broken, said):
    with pytest.raises(SourceError) as failed:
        pull(opened(Linear([issue(1, NOW - DAY, **broken)])))
    assert lin.LISTING in str(failed.value) and said in str(failed.value)


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        ({"issues": {"nodes": [], "pageInfo": {"hasNextPage": True}}}, "next page"),
        ({"issues": {"nodes": [], "pageInfo": None}}, "pageInfo"),
        ({"issues": []}, "its issues"),
    ],
)
def test_a_listing_page_not_of_the_documented_shape_fails_the_pull(answer, said):
    stand_in = Linear()
    stand_in.answers.append((lin.LISTING, httpx.Response(200, json={"data": answer})))
    with pytest.raises(SourceError) as failed:
        pull(opened(stand_in))
    assert said in str(failed.value) and "documented shape" in str(failed.value)


def test_a_comment_not_of_the_documented_shape_fails_the_pull():
    broken = issue(1, NOW - DAY, comments=[comment(1, "Hi.", NOW - DAY, createdAt=None)])
    with pytest.raises(SourceError) as failed:
        pull(opened(Linear([broken])))
    assert lin.COMMENTS in str(failed.value) and "a comment's id or time" in str(failed.value)


def test_linear_s_own_message_is_shown_only_on_one_line_and_without_a_credential_or_address():
    secrets = (KEY,)
    plain = [{"message": "Argument\nvalidation   failed " + "x" * 400}]
    shown = lin.said_by_linear(plain, secrets)
    assert shown.startswith(': Linear said "Argument validation failed x')
    assert len(shown) <= lin.MESSAGE_MAX + len(': Linear said ""')
    assert lin.said_by_linear([{"message": f"bad key {KEY}"}], secrets) == ""
    spaced = " ".join(KEY)  # spaced out, still the key
    assert lin.said_by_linear([{"message": f"bad key {spaced}"}], secrets) == ""
    assert lin.said_by_linear([{"message": "ask ada@example.test"}], secrets) == ""
    assert lin.said_by_linear([{"message": 7}, {"code": "X"}], secrets) == ""


def test_a_refusal_of_another_kind_names_the_operation_status_and_code_and_the_message():
    stand_in = Linear()
    stand_in.answers.append(
        (lin.LISTING, graphql_error(400, "Unknown argument on field", code="GRAPHQL_VALIDATION"))
    )
    with pytest.raises(SourceError) as failed:
        pull(opened(stand_in))
    said = str(failed.value)
    assert said == (
        'Linear refused FinishedIssues (HTTP 400) (GRAPHQL_VALIDATION): Linear said "Unknown'
        ' argument on field"'
    )
    stand_in.answers.append(
        (lin.LISTING, graphql_error(400, f"key {KEY} is wrong", code="GRAPHQL_VALIDATION"))
    )
    with pytest.raises(SourceError) as failed:
        pull(opened(stand_in))
    assert KEY not in str(failed.value) and "Linear said" not in str(failed.value)


def test_a_redirect_is_not_followed_and_a_refused_key_names_its_variable_only():
    stand_in = Linear()
    stand_in.answers.append(
        (lin.LISTING, httpx.Response(302, headers={"location": "https://elsewhere.example.test"}))
    )
    with pytest.raises(SourceError) as failed:
        pull(opened(stand_in))
    assert "redirect (HTTP 302)" in str(failed.value) and len(stand_in.requests) == 1
    stand_in.answers.append((lin.LISTING, graphql_error(200, KEY, type="Forbidden")))
    with pytest.raises(SourceError) as refused:
        pull(opened(stand_in))
    assert VAR in str(refused.value) and KEY not in str(refused.value)
    assert "cairn" not in str(refused.value).lower()


def test_a_rate_limit_is_said_in_a_sentence_without_the_key_and_the_pull_returns(caplog):
    caplog.set_level(logging.DEBUG)
    stand_in = Linear([issue(1, NOW - DAY)])
    stand_in.answers.append((lin.LISTING, graphql_error(429, KEY, code="RATELIMITED")))
    given = lin.Cursor(SINCE, None).encode().replace(",", ", ")  # returned as it was given
    got = pull(opened(stand_in), given)
    assert got.drafts == [] and got.cursor == given and stand_in.ops() == [lin.LISTING]
    assert got.skipped == [] and got.stopped == (
        "Linear asked source 'tracker' to slow down (rate limited) when it asked"
        " FinishedIssues: this pull stopped there and kept what it had finished, and the next"
        " run goes on."
    )
    assert KEY not in got.stopped and "cairn" not in got.stopped.lower()
    assert KEY not in caplog.text


def test_every_request_is_one_named_operation_sent_as_json_with_the_key_as_it_is():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return Linear([issue(1, NOW - DAY)])(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = lin.check_config({"key_env": VAR, "projects": ["linear"], "base_url": BASE})
    source = lin.open_source(
        "tracker", config, http=http, env={VAR: f"  {KEY}\n"}, clock=Clock(NOW), since=SINCE
    )
    assert source.secrets == (KEY,)
    pull(source)
    for request in seen:
        body = json.loads(request.content)
        assert set(body) == {"query", "variables", "operationName"}
        assert f"query {body['operationName']}(" in body["query"]
        assert request.headers["authorization"] == KEY
        assert request.headers["content-type"] == "application/json"
        assert "email" not in body["query"].lower()
