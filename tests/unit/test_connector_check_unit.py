"""Unit tests for connector-check (cairn.sources.connector_check, C9): each rule of the
checker on hand-made answers, the order of its lines, what it says of a rule it could not
check, and its options. The connector is a function behind httpx.MockTransport; the clock is
injected. The frozen tests/acceptance/test_c9_connector.py runs the command itself against
real sockets. Fixtures are synthetic."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cairn.server import main
from cairn.sources import connector as c
from cairn.sources import connector_check as check
from cairn.sources import timing
from wait_helpers import wait_for_event

SECRET = "check-" + "made-up-" + "secret-4d2a91"
URL = "https://feed.example.test/items"
SINCE = datetime(2099, 5, 1, tzinfo=UTC)


def item(n: int, **fields) -> dict:
    return {"id": f"V-{n}", "text": f"# Valve {n}\n\nValve {n} is shut.\n", **fields}


class Feed:
    """A faithful connector of `size` items, `page` an answer; `change` may alter an answer
    (the request's body, the number of the asking with a null cursor, the answer)."""

    def __init__(self, size: int = 5, page: int = 2) -> None:
        self.items = [item(n) for n in range(1, size + 1)]
        self.page = page
        self.firsts = 0
        self.change = None
        self.wrong = (401, {"error": "no"})
        self.requests: list[dict] = []
        self.bearers: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.bearers.append(request.headers["authorization"])
        if request.headers["authorization"] != f"Bearer {SECRET}":
            status, said = self.wrong
            return httpx.Response(status, json=said)
        offset = 0 if body["cursor"] is None else int(body["cursor"].removeprefix("after:"))
        self.firsts += offset == 0
        chunk = self.items[offset : offset + min(self.page, body["limit"])]
        nxt = offset + len(chunk)
        answer = {
            "protocol": 1,
            "items": chunk,
            "cursor": f"after:{nxt}",
            "more": nxt < len(self.items),
        }
        if self.change:
            answer = self.change(body, self.firsts, answer)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)


def checked(feed: Feed, since=SINCE, limit=3, moments=None) -> list[check.Verdict]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(feed.handle))
    clock = (lambda: SINCE) if moments is None else moments
    return asyncio.run(check.converse(http, URL, SECRET, since=since, limit=limit, clock=clock))


def by_rule(verdicts) -> dict[str, check.Verdict]:
    return {v.rule: v for v in verdicts}


def failed(verdicts) -> list[str]:
    return [v.rule for v in verdicts if not v.ok]


def test_a_faithful_connector_passes_every_rule_in_the_fixed_order():
    feed = Feed()
    verdicts = checked(feed)
    assert [v.rule for v in verdicts] == list(check.RULES) and failed(verdicts) == []
    assert [r["cursor"] for r in feed.requests] == [None, None, "after:2", "after:4", None]
    assert all(r["limit"] == 3 and r["since"] == "2099-05-01T00:00:00Z" for r in feed.requests)
    assert feed.bearers[:4] == [f"Bearer {SECRET}"] * 4
    assert SECRET not in feed.bearers[4] and feed.bearers[4].startswith("Bearer ")
    lines = [check.line(v) for v in verdicts]
    assert all(line.startswith("ok ") and "FAILED" not in line for line in lines)
    assert "followed 3 answers" in lines[check.RULES.index(c.MOVES_ON)]


def test_the_lines_name_their_rule_and_never_an_item_s_text_or_the_secret():
    feed = Feed()
    feed.change = lambda body, firsts, answer: {**answer, "items": [item(1, title="t" * 300)]}
    lines = [check.line(v) for v in checked(feed)]
    assert len(lines) == len(check.RULES)
    for line, rule in zip(lines, check.RULES, strict=True):
        assert check.TITLES[rule] in line and "\n" not in line
    assert not any("Valve" in line or SECRET in line for line in lines)
    assert "'V-1'" in lines[check.RULES.index(c.BOUNDS)]


@pytest.mark.parametrize(
    ("size", "change", "rule"),
    [
        (5, lambda b, f, a: {**a, "protocol": 2} if b["cursor"] else a, c.SHAPE),  # the walk's
        (2, lambda b, f, a: {**a, "items": [*a["items"], a["items"][0]]}, c.UNIQUE),
        (2, lambda b, f, a: {**a, "items": [item(9, source_ref="r" * 501)]}, c.BOUNDS),
        (2, lambda b, f, a: {**a, "items": [item(9, at="2099-04-30T23:59:59Z")]}, c.SINCE),
        (2, lambda b, f, a: {**a, "cursor": "after:" + "0" * 2_000}, c.CURSOR),
        (2, lambda b, f, a: {**a, "cursor": f"after:2;{SECRET}"}, c.CURSOR),
    ],
)
def test_each_rule_of_an_answer_fails_alone(size, change, rule):
    feed = Feed(size=size)
    feed.change = change
    verdicts = checked(feed)
    assert failed(verdicts) == [rule], [check.line(v) for v in verdicts]
    assert SECRET not in " ".join(check.line(v) for v in verdicts)


def test_another_order_or_an_item_gone_on_the_second_asking_fails_the_same_request_twice():
    for change in (
        lambda b, f, a: {**a, "items": a["items"][::-1]} if f == 2 and b["cursor"] is None else a,
        lambda b, f, a: {**a, "items": a["items"][1:]} if f == 2 and b["cursor"] is None else a,
        lambda b, f, a: {**a, "cursor": "after:2;x"} if f == 2 and b["cursor"] is None else a,
    ):
        feed = Feed()
        feed.change = change
        assert failed(checked(feed)) == [check.REPEAT]


def test_a_last_page_that_grew_between_the_askings_is_not_a_fault():
    feed = Feed(size=2, page=5)
    feed.change = lambda b, f, a: (
        {**a, "items": [*a["items"], item(3)], "cursor": "after:3"} if f == 2 else a
    )
    assert failed(checked(feed)) == []


def test_a_more_answer_that_does_not_move_on_fails_and_the_walk_stops():
    feed = Feed()
    feed.change = lambda b, f, a: (
        {**a, "items": [], "cursor": b["cursor"], "more": True} if b["cursor"] else a
    )
    verdicts = checked(feed)
    assert failed(verdicts) == [c.MOVES_ON]
    assert [r["cursor"] for r in feed.requests] == [None, None, "after:2", None]
    assert "answer 3" in check.line(by_rule(verdicts)[c.MOVES_ON])  # the walk's first


def test_a_backlog_longer_than_a_run_asks_for_passes_and_says_how_far_it_went():
    feed = Feed(size=200, page=2)
    verdicts = checked(feed)
    assert failed(verdicts) == []
    walked = [r for r in feed.requests if r["cursor"]]
    assert len(walked) == check.FOLLOWED - 1 == 19
    said = check.line(by_rule(verdicts)[c.MOVES_ON])
    assert "followed 20 answers" in said and "had more" in said


@pytest.mark.parametrize(
    ("wrong", "ok"),
    [
        ((401, {}), True),
        ((403, {"error": "no"}), True),
        ((401, {"items": [item(1)]}), False),
        ((200, {"protocol": 1, "items": [], "cursor": None, "more": False}), False),
        ((404, {}), False),
        ((500, {}), False),
    ],
)
def test_a_wrong_secret_must_be_refused_with_401_or_403_and_no_item(wrong, ok):
    feed = Feed()
    feed.wrong = wrong
    verdicts = checked(feed)
    assert failed(verdicts) == ([] if ok else [check.WRONG_SECRET])


def test_an_answer_past_the_deadline_fails_the_deadline_rule_alone(monkeypatch):
    # Give the immediate answers generous headroom. The stalled one must be cancelled.
    monkeypatch.setattr(check, "ANSWER_SECONDS", 1.0)
    monkeypatch.setattr(timing, "FLOOR", 1.0)
    feed = Feed()

    async def slow(request):  # the walk's second answer never comes in time
        if json.loads(request.content)["cursor"] == "after:4":
            await wait_for_event(asyncio.Event(), "the checker did not cancel its request")
        return feed.handle(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    verdicts = asyncio.run(
        check.converse(http, URL, SECRET, since=SINCE, limit=3, clock=lambda: SINCE)
    )
    assert failed(verdicts) == [check.DEADLINE]
    assert "answer 4" in check.line(by_rule(verdicts)[check.DEADLINE])
    assert "stopped" in check.line(by_rule(verdicts)[c.MOVES_ON])


def test_rules_that_no_answer_let_it_check_fail_as_not_checked():
    feed = Feed()
    feed.change = lambda b, f, a: httpx.Response(500, json={"error": "down"})
    verdicts = checked(feed)
    said = {v.rule: check.line(v) for v in verdicts}
    assert not by_rule(verdicts)[c.SHAPE].ok and "HTTP 500" in said[c.SHAPE]
    for rule in (c.UNIQUE, c.BOUNDS, c.SINCE, check.REPEAT, c.MOVES_ON, c.CURSOR):
        assert said[rule].startswith("FAILED") and "not checked" in said[rule], said[rule]
    assert by_rule(verdicts)[check.WRONG_SECRET].ok


def test_the_made_up_secret_is_new_each_time_and_never_holds_the_real_one():
    one, two = check.made_up(SECRET), check.made_up(SECRET)
    assert one != two and SECRET not in one and one not in SECRET


# The command's options


def call(argv) -> int:
    try:
        return main(argv)
    except SystemExit as stopped:
        return stopped.code if isinstance(stopped.code, int) else 1


@pytest.mark.parametrize(
    "extra",
    [
        ["--since", "2099-05-01"],
        ["--since", "2099-05-01T00:00:00"],
        ["--limit", "0"],
        ["--limit", "201"],
        ["--limit", "2.5"],
        ["--secret-env", "a variable"],
    ],
)
def test_a_bad_option_exits_2(extra, monkeypatch, capsys):
    monkeypatch.setenv("FEED_SECRET", SECRET)
    argv = ["connector-check", "--url", URL, "--secret-env", "FEED_SECRET", *extra]
    assert call(argv) == 2
    assert SECRET not in capsys.readouterr().err


def test_an_unset_secret_names_its_variable_and_sends_nothing(monkeypatch, capsys):
    monkeypatch.delenv("FEED_SECRET", raising=False)
    sent = []
    monkeypatch.setattr(check, "source_client", lambda: sent.append(1))
    assert call(["connector-check", "--url", URL, "--secret-env", "FEED_SECRET"]) == 2
    assert "FEED_SECRET" in capsys.readouterr().err and sent == []


def test_the_default_since_is_thirty_days_before_now():
    now = datetime(2099, 5, 31, 12, tzinfo=UTC)
    assert check.default_since(lambda: now) == now - timedelta(days=30)


def test_it_prints_the_lines_on_stdout_and_a_summary_on_stderr(monkeypatch, capsys):
    feed = Feed()
    feed.items[0]["title"] = "t" * 201
    monkeypatch.setenv("FEED_SECRET", SECRET)
    monkeypatch.setattr(
        check,
        "source_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(feed.handle)),
    )
    argv = ["connector-check", "--url", URL, "--secret-env", "FEED_SECRET", "--limit", "3"]
    assert call(argv) == 1
    printed = capsys.readouterr()
    assert len(printed.out.splitlines()) == len(check.RULES)
    assert "1 of 9 rules failed" in printed.err and SECRET not in printed.out + printed.err
