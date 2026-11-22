"""Unit tests for the connector kind (cairn.sources.connector, C9): what the frozen
tests/acceptance/test_c9_connector.py does not see. Every bound of the config and of an item,
each rule of an answer on a hand-made answer, the transport rule's cases, the pull's
sentences, and what a refusal says. The connector is a stand-in behind httpx.MockTransport;
clocks are injected and far ahead; nothing sleeps. Fixtures are synthetic."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cairn.sources import ITEM_MAX, KINDS, SourceError, withheld_via
from cairn.sources import connector as c
from cairn.store.memory import FIELD_MAX

SECRET = "unit-" + "made-up-" + "connector-secret-0b7e"
ENV = {"FEED_SECRET": SECRET}
URL = "https://feed.example.test/items"
SINCE = datetime(2099, 3, 1, 9, 0, tzinfo=UTC)
TOKEN = "ghp_" + "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zAaB3d"  # shaped like a credential


def config(**settings) -> dict:
    given = {"url": URL, "secret_env": "FEED_SECRET", "projects": ["gauges"], **settings}
    return {k: v for k, v in given.items() if v is not None}


def item(n: int, **fields) -> dict:
    given = {"id": f"G-{n}", "text": f"# Gauge {n}\n\nGauge {n} reads {n} bar.\n"}
    given.update(fields)
    return {k: v for k, v in given.items() if v is not None}


def answer(items=(), cursor="after:1", more=False, **extra) -> dict:
    return {"protocol": 1, "items": list(items), "cursor": cursor, "more": more, **extra}


# The bounds are a note's and the item's own


def test_the_bounds_are_a_note_s_own_and_an_item_s():
    assert c.ID_MAX == ITEM_MAX == 200
    assert (
        FIELD_MAX["title"],
        FIELD_MAX["topic"],
        FIELD_MAX["source_ref"],
    ) == (c.TITLE_MAX, c.TOPIC_MAX, c.SOURCE_REF_MAX)
    assert c.CURSOR_MAX == 2_000 and c.ANSWER_MAX_BYTES == 4 * 1024 * 1024
    assert c.ERROR_SENTENCE_MAX == 200 and c.LIMIT == (50, 1, 200)
    assert 1 <= c.ANSWERS_PER_RUN < 1_000


def test_the_kind_is_registered_withheld_and_proposes_the_source_s_own_name():
    kind = KINDS["connector"]
    assert kind is c.KIND and kind.via == "connector" == c.VIA
    assert kind.withheld and "connector" in withheld_via()
    assert kind.starts_off and kind.own_project and kind.proposed_project("erp-feed") == "erp-feed"
    assert "connector-check" in kind.settings and '"secret_env"' in kind.settings
    assert "cairn" not in (kind.switching_on() or "").lower()
    assert "cairn" not in kind.describe(c.check_config(config())).lower()


# The config


@pytest.mark.parametrize(
    "url",
    [
        "https://feed.example.test/items",
        "https://feed.example.test:8443/a/b?team=north",
        "http://127.0.0.1:8811/feed",
        "http://localhost/",
        "https://feed.example.test",
    ],
)
def test_a_url_by_the_transport_rule_is_kept_exactly(url):
    assert c.check_config(config(url=url))["url"] == url


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://feed.example.test/items", "http"),
        ("http://192.168.1.4/items", "http"),
        ("http://[::1]:8811/items", "http"),
        ("https://ada:pw-77@feed.example.test/items", "user"),
        ("https://ada@feed.example.test/items", "user"),
        ("https://feed.example.test/items#top", "fragment"),
        ("https:///items", "url"),
        ("https://feed.example.test:99999/items", "url"),
        ("https://FEED.example.test/items", "as it is sent"),
        ("https://feed.example.test/a b", "as it is sent"),
        ("https://feed.example.test/" + "a" * 2_100, "url"),
        ("ws://feed.example.test/items", "https"),
        ("/items", "https"),
    ],
)
def test_a_url_the_transport_rule_refuses_names_url_and_never_its_userinfo(url, why):
    with pytest.raises(ValueError) as refused:
        c.check_url(url)
    said = str(refused.value)
    assert said.startswith("url ") and why in said and "pw-77" not in said and "ada" not in said


@pytest.mark.parametrize(
    "url",
    [
        f"https://feed.example.test/items?token={TOKEN}",
        f"https://feed.example.test/items?a=1&k={TOKEN}",
        f"https://feed.example.test/{TOKEN}/items",
        f"https://{TOKEN.lower()}.example.test/items",
    ],
)
def test_a_credential_anywhere_in_the_url_is_refused_and_never_repeated(url):
    with pytest.raises(ValueError) as refused:
        c.check_config(config(url=url))
    said = str(refused.value)
    assert "url" in said and TOKEN not in said and TOKEN[4:].lower() not in said.lower()


@pytest.mark.parametrize(
    ("settings", "key"),
    [
        ({"secret_env": "feed_secret"}, "secret_env"),
        ({"secret_env": "FEED SECRET"}, "secret_env"),
        ({"secret_env": "A" * 129}, "secret_env"),
        ({"secret_env": ["FEED_SECRET"]}, "secret_env"),
        ({"projects": ["gauges", "valves"]}, "projects"),
        ({"projects": [7]}, "projects"),
        ({"projects": ["br_123"]}, "projects"),
        ({"projects": [TOKEN]}, "projects"),
        ({"limit": -1}, "limit"),
        ({"limit": None, "url": None}, "url"),
    ],
)
def test_each_bad_setting_is_named(settings, key):
    with pytest.raises(ValueError) as refused:
        c.check_config(config(**settings))
    assert key in str(refused.value) and TOKEN not in str(refused.value)


def test_a_setting_cairn_does_not_take_is_named_only_when_its_name_is_plain():
    with pytest.raises(ValueError, match="colour"):
        c.check_config(config(colour="blue"))
    for odd in (TOKEN, "Colour Scheme", "x" * 41):
        with pytest.raises(ValueError) as refused:
            c.check_config(config(**{odd: 1}))
        assert odd not in str(refused.value)


def test_check_fills_in_the_defaults_and_is_stable():
    checked = c.check_config({"url": URL, "secret_env": "FEED_SECRET"})
    assert checked == {"url": URL, "secret_env": "FEED_SECRET", "projects": [], "limit": 50}
    assert c.check_config(checked) == checked
    assert c.check_config(config(limit=1, projects=("gauges",)))["projects"] == ["gauges"]


# Items, one at a time


@pytest.mark.parametrize(
    ("raw", "rule"),
    [
        ("an item", c.NOT_AN_OBJECT),
        ({"text": "x"}, c.BAD_ID),
        ({"id": "", "text": "x"}, c.BAD_ID),
        ({"id": "x" * 201, "text": "x"}, c.BAD_ID),
        ({"id": 7, "text": "x"}, c.BAD_ID),
        ({"id": "G-1"}, c.BAD_TEXT),
        ({"id": "G-1", "text": "  \n"}, c.BAD_TEXT),
        ({"id": "G-1", "text": ["x"]}, c.BAD_TEXT),
        ({"id": "G-1", "text": "x", "title": "t" * 201}, c.bad_field("title")),
        ({"id": "G-1", "text": "x", "title": 7}, c.bad_field("title")),
        ({"id": "G-1", "text": "x", "topic": "t" * 201}, c.bad_field("topic")),
        ({"id": "G-1", "text": "x", "source_ref": "r" * 501}, c.bad_field("source_ref")),
        ({"id": "G-1", "text": "x", "at": "2099-03-01"}, c.BAD_AT),
        ({"id": "G-1", "text": "x", "at": "2099-03-01T09:00:00"}, c.BAD_AT),
        ({"id": "G-1", "text": "x", "at": "2099-03-01 09:00:00Z"}, c.BAD_AT),
        ({"id": "G-1", "text": "x", "at": "20990301T090000Z"}, c.BAD_AT),
        ({"id": "G-1", "text": "x", "at": "2099-02-30T09:00:00Z"}, c.BAD_AT),
        ({"id": "G-1", "text": "x", "at": 4102444800}, c.BAD_AT),
    ],
)
def test_an_item_that_breaks_the_protocol_says_which_rule_in_fixed_words(raw, rule):
    assert c.read_item(raw) == rule
    assert "G-1" not in rule and "cairn" not in rule.lower()


def test_an_item_at_its_bounds_is_read_and_null_is_absent():
    raw = item(
        1, id="i" * 200, title="t" * 200, topic="p" * 200, source_ref="r" * 500,
        at="2099-03-01T10:00:00.5+01:00", colour="blue",
    )  # fmt: skip
    read = c.read_item(raw)
    assert isinstance(read, c.Item) and read.id == "i" * 200 and read.source_ref == "r" * 500
    assert read.at == datetime(2099, 3, 1, 9, 0, 0, 500_000, tzinfo=UTC)
    bare = c.read_item(item(2, title=None, topic=None) | {"title": None, "at": None})
    assert (bare.title, bare.topic, bare.source_ref, bare.at) == (None, None, None, None)
    assert c.read_item(item(3, at="2099-03-01t09:00:00z")).at == SINCE


# Answers, as a whole


def examined(body, sent=None, limit=50):
    return c.examine(body, sent=sent, limit=limit, since=SINCE, secrets=(SECRET,))


def rules(body, **options) -> list[str]:
    return [finding.rule for finding in examined(body, **options).findings]


def test_a_faithful_answer_has_no_finding():
    got = examined(answer([item(1), item(2, at="2099-03-01T09:00:00Z")], "after:2", True))
    assert got.findings == [] and got.cursor == "after:2" and got.more is True
    assert [entry.id for entry in got.items] == ["G-1", "G-2"]


@pytest.mark.parametrize(
    "body",
    [
        [answer()],
        "an answer",
        None,
        answer() | {"protocol": 2},
        answer() | {"protocol": "1"},
        answer() | {"protocol": True},
        {k: v for k, v in answer().items() if k != "protocol"},
        answer() | {"items": None},
        answer() | {"items": {"id": "G-1"}},
        answer() | {"cursor": 7},
        answer() | {"more": "false"},
        answer() | {"more": None},
        {k: v for k, v in answer().items() if k != "more"},
        answer([item(n) for n in range(3)]),  # three for a limit of two
    ],
)
def test_an_answer_that_breaks_the_protocol_as_a_whole_fails_its_shape(body):
    assert rules(body, limit=2) == [c.SHAPE]
    said = examined(body, limit=2).findings[0].said
    assert "cairn" not in said.lower()


def test_another_version_says_version():
    for body in (answer() | {"protocol": 2}, {"items": [], "cursor": None, "more": False}):
        assert "version" in examined(body).findings[0].said


def test_the_cursor_s_size_and_secret_are_its_own_rule():
    assert rules(answer(cursor="k" * 2_000)) == []
    assert rules(answer(cursor="k" * 2_001)) == [c.CURSOR]
    for held in (f"after:1;{SECRET}", f"after:1;Bearer {SECRET}"):
        got = examined(answer(cursor=held))
        assert [f.rule for f in got.findings] == [c.CURSOR] and SECRET not in got.findings[0].said


def test_more_must_move_on_by_an_item_or_a_new_cursor():
    assert rules(answer([], "after:1", True), sent="after:1") == [c.MOVES_ON]
    assert rules(answer([], None, True), sent=None) == [c.MOVES_ON]
    assert rules(answer([], "after:2", True), sent="after:1") == []
    assert rules(answer([item(1)], "after:1", True), sent="after:1") == []
    assert rules(answer([], "after:1", False), sent="after:1") == []  # the end, not stuck


def test_ids_twice_bounds_and_since_are_found_by_item_and_named_by_id():
    body = answer(
        [
            item(1),
            item(1, text="again"),
            item(2, title="t" * 201),
            item(3, at="2099-03-01T08:59:59Z"),
            item(4, at="2099-03-01T10:00:00+01:00"),  # 09:00 UTC: the moment itself
        ]
    )
    got = examined(body)
    assert [f.rule for f in got.findings] == [c.UNIQUE, c.BOUNDS, c.SINCE]
    assert [("G-1" in f.said, "G-2" in f.said, "G-3" in f.said) for f in got.findings] == [
        (True, False, False),
        (False, True, False),
        (False, False, True),
    ]
    assert all("Gauge" not in f.said for f in got.findings)  # never an item's text
    assert got.items[2] == c.bad_field("title")


def test_an_id_shown_is_one_line_and_short_and_never_the_secret():
    assert c.shown_id("G-1", ()) == "'G-1'"
    long = c.shown_id("x\n" * 150, ())
    assert "\n" not in long and len(long) <= 70
    assert SECRET not in c.shown_id(f"G-{SECRET}", (SECRET,))


# The transport rule and what the connector is sent


class Stand:
    """A connector behind MockTransport: it keeps each request and answers `answers` in turn,
    each (status, body, headers)."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []
        self.http = httpx.AsyncClient(
            transport=httpx.MockTransport(self.handle), follow_redirects=True
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) != URL:
            return httpx.Response(200, json=answer([item(99)]))
        status, body, headers = self.answers.pop(0)
        if isinstance(body, bytes):
            return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)


def opened(stand: Stand, clock=None, deadline=None, **settings):
    checked = c.check_config(config(**settings))
    now = clock or (lambda: SINCE + timedelta(hours=1))
    return c.open_source(
        "feed", checked, http=stand.http, env=ENV, clock=now, deadline=deadline, since=SINCE
    )


def pulled(stand: Stand, cursor=None, **options):
    return asyncio.run(opened(stand, **options).pull(cursor))


def test_the_request_is_one_post_with_the_bearer_and_json_both_ways():
    stand = Stand((200, answer([item(1)], "after:1"), {}))
    got = pulled(stand, limit=7)
    (request,) = stand.requests
    assert request.method == "POST" and str(request.url) == URL
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["accept"] == "application/json"
    sent = json.loads(request.content)
    assert sent == {"protocol": 1, "since": "2099-03-01T09:00:00Z", "cursor": None, "limit": 7}
    assert [d.item for d in got.drafts] == ["G-1"] and got.stopped is None


def test_a_redirect_is_not_followed_and_says_so():
    stand = Stand((308, b"", {"location": "https://elsewhere.example.test/items"}))
    with pytest.raises(SourceError, match="redirect"):
        pulled(stand)
    assert len(stand.requests) == 1


def test_the_answer_must_say_it_is_json():
    stand = Stand((200, json.dumps(answer()).encode(), {"content-type": "text/plain"}))
    with pytest.raises(SourceError, match="not JSON"):
        pulled(stand)


def test_an_answer_over_4_mib_is_refused_before_it_is_read_whole():
    big = b'{"protocol": 1, "items": [], "pad": "' + b"x" * (4 * 1024 * 1024) + b'"}'
    stand = Stand((200, big, {"content-type": "application/json"}))
    with pytest.raises(SourceError, match="4 MiB"):
        pulled(stand)


def test_a_missing_secret_names_the_variable():
    for env in ({}, {"FEED_SECRET": " "}):
        with pytest.raises(SourceError, match="FEED_SECRET"):
            c.open_source("feed", c.check_config(config()), http=None, env=env, since=SINCE)
    source = c.open_source("feed", c.check_config(config()), http=None, env=ENV, since=SINCE)
    assert source.secrets == (SECRET, f"Bearer {SECRET}")
    assert SECRET not in repr(source)


# What a refusal says


@pytest.mark.parametrize(
    ("status", "words"),
    [
        (401, "refused the secret in FEED_SECRET (HTTP 401)"),
        (403, "refused the secret in FEED_SECRET (HTTP 403)"),
        (404, "HTTP 404"),
        (500, "HTTP 500, a fault on its side"),
        (429, "slow down"),
    ],
)
def test_a_refusal_says_what_happened_and_the_connector_s_sentence(status, words):
    stand = Stand((status, {"error": "the feed is closed for audit"}, {"retry-after": "30"}))
    with pytest.raises(SourceError) as failed:
        pulled(stand)
    said = str(failed.value)
    assert words in said and "the feed is closed for audit" in said
    assert "cairn" not in said.lower() and len(stand.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"error": "x" * 201},
        {"error": 7},
        {"error": f"key {SECRET}"},
        {"error": f"key {TOKEN}"},
        {"message": "the feed is closed"},
        b"<html>the feed is closed</html>",
        b'{"error": "' + b"x" * (64 * 1024) + b'"}',
    ],
)
def test_an_error_s_sentence_is_shown_only_when_short_a_string_and_clean(body):
    stand = Stand((500, body, {}))
    with pytest.raises(SourceError) as failed:
        pulled(stand)
    said = str(failed.value)
    assert "closed" not in said and SECRET not in said and TOKEN not in said and "xxx" not in said


def test_an_error_s_sentence_is_put_on_one_line():
    stand = Stand((500, {"error": "the feed\nis closed"}, {}))
    with pytest.raises(SourceError, match="the feed is closed"):
        pulled(stand)


# The pull: where it stops, and what it says


def test_a_429_after_an_answer_stops_the_run_with_what_it_had_and_says_so():
    stand = Stand(
        (200, answer([item(1)], "after:1", True), {}),
        (429, {"error": "too fast"}, {"retry-after": "30"}),
    )
    got = pulled(stand)
    assert [d.item for d in got.drafts] == ["G-1"] and "slow down" in got.stopped
    assert c.resumed(got.cursor, SINCE) == "after:1"


def test_a_429_on_the_run_s_first_request_fails_the_run_and_asks_once():
    stand = Stand((429, {}, {"retry-after": "30"}))
    with pytest.raises(SourceError, match=r"slow down.*first request"):
        pulled(stand)
    assert len(stand.requests) == 1


@pytest.mark.parametrize(
    "given",
    [
        [],  # an empty answer that moved its cursor
        [item(1, title="t" * (c.TITLE_MAX + 1))],  # only skipped
        [item(1, at="2099-02-28T09:00:00Z")],  # only before since
    ],
)
def test_a_429_after_any_answer_keeps_its_progress_without_a_draft(given):
    stand = Stand((200, answer(given, "after:1", True), {}), (429, {}, {}))
    got = pulled(stand)
    assert got.drafts == [] and got.stopped == c.SLOWED
    assert c.resumed(got.cursor, SINCE) == "after:1"
    assert len(stand.requests) == 2


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def store(tmp_path):
    from cairn.store import Store

    clock = Clock(SINCE - timedelta(days=1))
    s = Store(tmp_path / "cairn.db", clock=clock)
    s.create_project("gauges")
    s.add_source("feed", "connector", {"url": URL, "secret_env": "FEED_SECRET"})
    clock.now = SINCE
    s.enable_source("feed", projects=["gauges"])
    clock.now = SINCE + timedelta(hours=1)
    s.clock = clock
    yield s
    s.close()


def synced(store, stand: Stand):
    from cairn.sources import run_source

    return asyncio.run(run_source(store, "feed", http=stand.http, env=ENV, clock=store.clock))


@pytest.mark.parametrize(
    "first",
    [
        [item(1, title="t" * (c.TITLE_MAX + 1))],  # only skipped
        [item(1, at="2099-02-28T09:00:00Z")],  # only before since
        [item(1)],  # a draft
    ],
)
def test_a_run_slowed_by_a_429_ends_worked_and_the_next_goes_on_writing_nothing_twice(store, first):
    slowed = Stand((200, answer(first, "after:1", True), {}), (429, {}, {}))
    report = synced(store, slowed)
    assert store.get_source("feed").last_error is None and report.said == c.SLOWED
    written = report.saved
    assert c.resumed(store.get_source("feed").cursor, SINCE) == "after:1"
    again = Stand((200, answer([item(1), item(2)], "after:2"), {}))  # item 1 sent again
    report = synced(store, again)
    assert json.loads(again.requests[0].content)["cursor"] == "after:1"
    assert report.saved == (1 if written else 2) and report.unchanged == written
    texts = [n.text for n in store.list_notes(store.get_project("gauges").id)]
    assert sorted(texts) == sorted({item(1)["text"], item(2)["text"]})


def test_a_run_asks_at_most_its_bound_of_answers_and_says_so():
    stand = Stand(*[(200, answer([item(n)], f"after:{n}", True), {}) for n in range(1, 60)])
    got = pulled(stand)
    assert len(stand.requests) == c.ANSWERS_PER_RUN == len(got.drafts)
    assert str(c.ANSWERS_PER_RUN) in got.stopped and "next run" in got.stopped
    assert c.resumed(got.cursor, SINCE) == f"after:{c.ANSWERS_PER_RUN}"


def test_a_run_holds_at_most_its_bound_of_bytes(monkeypatch):
    monkeypatch.setattr(c, "PULL_BYTES", 300)
    stand = Stand(*[(200, answer([item(n)], f"after:{n}", True), {}) for n in range(1, 9)])
    got = pulled(stand)
    assert 1 < len(stand.requests) < 8 and "next run" in got.stopped


def test_a_deadline_after_an_answer_returns_it_and_before_any_fails():
    moments = iter([SINCE + timedelta(minutes=m) for m in (0, 0, 9, 9, 9, 9)])
    stand = Stand(*[(200, answer([item(n)], f"after:{n}", True), {}) for n in range(1, 5)])
    got = pulled(stand, clock=lambda: next(moments), deadline=SINCE + timedelta(minutes=5))
    assert [d.item for d in got.drafts] == ["G-1"] and "time" in got.stopped
    with pytest.raises(SourceError, match="time"):
        pulled(Stand(), deadline=SINCE)


def test_each_draft_resumes_before_its_answer_and_the_last_after_it():
    stand = Stand(
        (200, answer([item(1), item(2)], "after:2", True), {}),
        (200, answer([item(3), {"id": "G-4"}], "after:4", False), {}),
    )
    got = pulled(stand)
    resumes = [c.resumed(d.resume, SINCE) for d in got.drafts]
    # G-4 has no text: skipped, not a draft, so G-3 is its answer's last draft
    assert resumes == [None, "after:2", "after:4"]
    assert [s.reason for s in got.skipped] == [c.BAD_TEXT]
    assert c.resumed(got.cursor, SINCE) == "after:4"


def test_a_draft_says_its_note_and_names_its_item():
    stand = Stand(
        (200, answer([item(1, title="Gauge 1", topic="Gauges", source_ref="g:1"), item(2)]), {})
    )
    one, two = pulled(stand).drafts
    assert (one.project, one.title, one.topic, one.source_ref, one.label, one.item) == (
        "gauges", "Gauge 1", "Gauges", "g:1", "connector", "G-1",
    )  # fmt: skip
    assert (two.title, two.topic, two.source_ref) == ("", None, "feed:G-2")


def test_items_before_since_are_left_out_and_not_counted():
    stand = Stand((200, answer([item(1, at="2099-03-01T08:00:00Z"), item(2)]), {}))
    got = pulled(stand)
    assert [d.item for d in got.drafts] == ["G-2"] and got.skipped == []


def test_the_stored_cursor_is_the_connector_s_under_the_switch_on_it_was_made_in():
    stored = c.stored(SINCE, "after:3")
    assert c.resumed(stored, SINCE) == "after:3"
    assert c.resumed(stored, SINCE + timedelta(days=1)) is None  # switched on again
    assert c.resumed(None, SINCE) is None
    for broken in ("after:3", "[]", '{"v": 2}', '{"v": 1, "since": 7, "cursor": null}'):
        with pytest.raises(SourceError, match="stored cursor"):
            c.resumed(broken, SINCE)
    stand = Stand((200, answer([], "after:9"), {}))
    pulled(stand, cursor=c.stored(SINCE - timedelta(days=1), "after:3"))
    assert json.loads(stand.requests[0].content)["cursor"] is None
