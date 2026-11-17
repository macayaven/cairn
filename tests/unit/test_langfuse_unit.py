"""Unit tests for the Langfuse trace source: what tests/acceptance/test_c2_langfuse.py does
not pin. HTTP goes through httpx.MockTransport; time and sleep are injected."""

import asyncio
import base64
import bisect
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from functools import partial

import httpx
import pytest
from linear_cost import assert_near_linear
from markdown_it import MarkdownIt

from cairn.guard import Finding
from cairn.sources import KINDS, SourceError, langfuse
from cairn.sources.langfuse import (
    LAG,
    MAX_RESPONSE_BYTES,
    MAX_RETRY_AFTER,
    MAX_SCORE_PAGES,
    MAX_TRACE_PAGES,
    LangfuseSource,
    _encode,
    check_config,
    open_source,
    shown,
)
from cairn.spans import chunk

LF = "https://langfuse.example.test"
NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
CONFIG = {
    "base_url": LF,
    "projects": ["payments-api"],
    "public_key_env": "LANGFUSE_PUBLIC_KEY",
    "secret_key_env": "LANGFUSE_SECRET_KEY",
}


def stamp(minutes_ago: float) -> str:
    return (
        (NOW - timedelta(minutes=minutes_ago))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def score(score_id="s1", value="payments-api", trace="t1", minutes_ago=20.0, **extra):
    return {
        "id": score_id,
        "projectId": "proj",
        "name": "cairn",
        "dataType": "CATEGORICAL",
        "value": value,
        "timestamp": stamp(minutes_ago),
        "comment": extra.pop("comment", "why"),
        "subject": extra.pop("subject", {"kind": "trace", "id": trace}),
        **extra,
    }


def root(trace="t1", given="in", answer="out", **extra):
    return {
        "id": f"obs-{trace}",
        "traceId": trace,
        "startTime": stamp(30),
        "projectId": "proj",
        "parentObservationId": None,
        "type": "SPAN",
        "name": "handle",
        "isRootObservation": True,
        "input": given,
        "output": answer,
        "traceName": f"name-{trace}",
        **extra,
    }


def at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def in_bounds(item: dict, params) -> bool:
    """fromTimestamp inclusive, toTimestamp exclusive, as the API documents them."""
    try:
        moment = at(item["timestamp"])
        moment < NOW  # noqa: B015 - raises for a time without a zone
    except (TypeError, ValueError, AttributeError):
        return True  # a malformed score is sent as it is, for the client to refuse
    low, high = params.get("fromTimestamp"), params.get("toTimestamp")
    return (low is None or moment >= at(low)) and (high is None or moment < at(high))


def serve(scores=(), observations=(), seen=None):
    """A minimal server: every score in the asked time bounds on one page, every
    observation on one page."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("/scores"):
            data = [s for s in scores if in_bounds(s, request.url.params)]
            return httpx.Response(200, json={"data": data, "meta": {"limit": 100}})
        trace = request.url.params["traceId"]
        found = [o for o in observations if o["traceId"] == trace]
        return httpx.Response(200, json={"data": found, "meta": {}})

    return handler


def source(handler, **options) -> tuple[LangfuseSource, list[float]]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    clock = options.pop("clock", lambda: NOW)
    made = LangfuseSource(LF, "pk-lf-1", "sk-lf-1", http=http, clock=clock, sleep=sleep, **options)
    return made, slept


def pull(made, cursor=None):
    return asyncio.run(made.pull(cursor))


# The config


def test_a_valid_config_is_kept_as_stored_and_its_url_trimmed():
    checked = check_config({**CONFIG, "base_url": LF + "/", "score_name": "remember"})
    assert checked == {**CONFIG, "score_name": "remember"}
    assert check_config({**CONFIG, "base_url": "http://127.0.0.1:3000"})["base_url"] == (
        "http://127.0.0.1:3000"
    )
    assert check_config({**CONFIG, "projects": ["a", "A", "b"]})["projects"] == ["a", "b"]


@pytest.mark.parametrize(
    "change",
    [
        {"base_url": "ftp://langfuse.example.test"},
        {"base_url": "http://10.0.0.1"},
        {"base_url": "https://"},
        {"base_url": f"{LF}?project=1"},
        {"base_url": f"{LF}#x"},
        {"base_url": "https://user@langfuse.example.test"},
        {"projects": "payments-api"},
        {"projects": ["br_000000000000"]},
        {"projects": ["not a name"]},
        {"public_key_env": "lower-case-dash"},
        {"public_key_env": ""},
        {"score_name": "a,b"},
        {"score_name": " "},
        {"score_name": "x" * 201},
        {"score_name": "line\nbreak"},
        {"projects": ["sk-lf-0123"]},
        {"extra": "value"},
    ],
)
def test_a_config_that_cannot_work_is_refused(change):
    with pytest.raises(ValueError):
        check_config({**CONFIG, **change})


def test_a_key_in_base_url_is_refused_by_one_rule_wherever_a_source_is_made():
    """The config check (the only way a source is added: Store.add_source, through
    KINDS["langfuse"].check) and opening a stored source both go through _check_base_url."""
    url = f"{LF}/project/sk-lf-0b1c2d3e"
    with pytest.raises(ValueError, match=r"^base_url looks like it holds a credential") as checked:
        check_config({**CONFIG, "base_url": url})
    with pytest.raises(ValueError) as opened:
        LangfuseSource(url, "pk-lf-1", "sk-lf-1", http=httpx.AsyncClient())
    assert str(opened.value) == str(checked.value) and "0b1c2d3e" not in str(checked.value)


def test_a_refusal_never_repeats_a_value_that_could_be_a_credential():
    secret = "sk-lf-0b1c2d3e-aaaa-bbbb-cccc-000000000000"
    for leaked in ({**CONFIG, secret: "x"}, {**CONFIG, "secret_key_env": secret}):
        with pytest.raises(ValueError) as refused:
            check_config(leaked)
        assert secret not in str(refused.value)


def test_opening_a_source_reads_its_keys_from_the_environment_only():
    env = {"LANGFUSE_PUBLIC_KEY": "pk-lf-1", "LANGFUSE_SECRET_KEY": "  "}
    with pytest.raises(SourceError) as missing:
        open_source("traces", CONFIG, http=httpx.AsyncClient(), env=env)
    assert "LANGFUSE_SECRET_KEY" in str(missing.value) and "pk-lf-1" not in str(missing.value)
    opened = open_source(
        "traces",
        {**CONFIG, "score_name": "remember"},
        http=httpx.AsyncClient(),
        env={**env, "LANGFUSE_SECRET_KEY": "sk-lf-1"},
    )
    assert (opened.name, opened.score_name) == ("traces", "remember")
    assert KINDS["langfuse"].open is open_source and KINDS["langfuse"].via == "langfuse"


@pytest.mark.parametrize("status", [401, 403])
def test_refused_credentials_name_the_two_variables_and_no_key(status):
    names = {"public_key_env": "TRACES_PUBLIC", "secret_key_env": "TRACES_SECRET"}
    env = {"TRACES_PUBLIC": "pk-lf-refused-9", "TRACES_SECRET": "sk-lf-refused-9"}
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status)))
    source = open_source("traces", {**CONFIG, **names}, http=http, env=env, clock=lambda: NOW)
    with pytest.raises(SourceError) as refused:
        asyncio.run(source.pull(None))
    said = str(refused.value)
    assert said == (
        f"Langfuse refused the credentials (HTTP {status}): check that TRACES_PUBLIC holds the"
        " Langfuse public key and TRACES_SECRET the Langfuse secret key"
    )
    for secret in (*env.values(), "refused-9", *source.secrets):
        assert secret not in said


@pytest.mark.parametrize(
    "options",
    [
        {"overlap": timedelta(minutes=30), "grace": timedelta(hours=1)},
        {"max_chars": 0},
        {"lookback": timedelta(0)},
        {"trace_window": timedelta(seconds=-1)},
    ],
)
def test_options_that_could_lose_a_waiting_score_are_refused(options):
    with pytest.raises(ValueError):
        LangfuseSource(LF, "pk", "sk", http=httpx.AsyncClient(), **options)


# Reading


def test_requests_carry_basic_auth_never_follow_a_redirect_and_name_what_was_asked():
    seen: list[httpx.Request] = []
    made, _ = source(serve([score()], [root()], seen))
    assert [d.title for d in pull(made).drafts] == ["name-t1"]
    assert all(r.headers["authorization"].startswith("Basic ") for r in seen)

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://elsewhere.example.test/"})

    moved, _ = source(redirect)
    with pytest.raises(SourceError, match="redirect"):
        pull(moved)


def test_a_response_larger_than_the_bound_is_not_parsed():
    big = b'{"data": [], "meta": {}, "pad": "' + b"x" * MAX_RESPONSE_BYTES + b'"}'

    def huge(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big)

    made, _ = source(huge)
    with pytest.raises(SourceError, match="larger"):
        pull(made)


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"data": {}, "meta": {}}', b'{"data": [], "meta": {"cursor": 5}}'],
)
def test_an_answer_that_is_not_the_documented_shape_fails_loudly(body):
    made, _ = source(lambda request: httpx.Response(200, content=body))
    with pytest.raises(SourceError):
        pull(made)


def test_a_retry_after_is_capped_and_a_missing_one_waits_a_second():
    answers = iter(
        [
            httpx.Response(429, headers={"retry-after": "86400"}),
            httpx.Response(429),
            httpx.Response(429, headers={"retry-after": "soon"}),
        ]
    )

    def limited(request: httpx.Request) -> httpx.Response:
        return next(answers, None) or httpx.Response(200, json={"data": [], "meta": {}})

    made, slept = source(limited)
    assert pull(made).drafts == []
    assert slept == [MAX_RETRY_AFTER, 1.0, 1.0]


def test_a_network_error_is_a_source_error_without_the_url_or_keys():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    made, _ = source(down)
    with pytest.raises(SourceError) as failed:
        pull(made)
    assert "ConnectError" in str(failed.value) and "sk-lf-1" not in str(failed.value)


def test_scores_without_the_subject_group_fail_loudly_and_a_null_subject_is_skipped():
    bare = {k: v for k, v in score().items() if k != "subject"}
    made, _ = source(serve([bare]))
    with pytest.raises(SourceError, match="subject"):
        pull(made)
    made, _ = source(serve([score(subject=None)]))
    assert [(s.ref, s.reason) for s in pull(made).skipped] == [("s1", "unsupported subject")]


@pytest.mark.parametrize("stamped", ["2026-11-10T11:00:00", "yesterday", None])
def test_a_score_whose_time_cannot_be_placed_fails_loudly(stamped):
    made, _ = source(serve([{**score(), "timestamp": stamped}]))
    with pytest.raises(SourceError, match="time"):
        pull(made)


def test_a_score_another_name_or_type_the_server_sent_anyway_is_left_alone():
    made, _ = source(serve([score(name="helpfulness"), score("s2", dataType="NUMERIC")], [root()]))
    got = pull(made)
    assert got.drafts == [] and got.skipped == []


def test_an_answer_that_never_ends_fails_loudly_rather_than_never_moving():
    def endless(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "meta": {"limit": 100, "cursor": "more"}})

    made, _ = source(endless)
    with pytest.raises(SourceError, match="one pull's bounds"):
        pull(made)


def test_a_root_beyond_the_page_bound_is_skipped_with_its_own_reason():
    count = {"pages": 0}

    def endless(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/scores"):
            return httpx.Response(200, json={"data": [score()], "meta": {"limit": 100}})
        count["pages"] += 1
        child = {**root(), "id": "child", "isRootObservation": False}
        return httpx.Response(200, json={"data": [child], "meta": {"cursor": "more"}})

    made, _ = source(endless)
    got = pull(made)
    assert [(s.ref, s.reason) for s in got.skipped] == [("s1", "too many observations")]
    assert count["pages"] == MAX_TRACE_PAGES


def test_observations_without_the_basic_or_io_group_fail_loudly():
    no_flag = {k: v for k, v in root().items() if k != "isRootObservation"}
    no_io = {k: v for k, v in root().items() if k not in ("input", "output")}
    for broken in (no_flag, no_io):
        made, _ = source(serve([score()], [broken]))
        with pytest.raises(SourceError):
            pull(made)


def test_a_cursor_that_is_not_this_sources_fails_loudly():
    made, _ = source(serve())
    for cursor in ("garbage", json.dumps({"v": 99}), json.dumps([1, 2])):
        with pytest.raises(SourceError, match="cursor"):
            pull(made, cursor)


def test_the_cursor_keeps_only_what_the_overlap_needs():
    scores = [score(f"s{n}", minutes_ago=600 - n * 60, trace="t1") for n in range(10)]
    made, _ = source(serve(scores, [root()]))
    cursor = json.loads(pull(made).cursor)
    newest = NOW - timedelta(minutes=600 - 9 * 60)
    assert datetime.fromisoformat(cursor["from"]) == newest - timedelta(hours=1)
    # fromTimestamp is inclusive: s8, stamped exactly at the new start, comes back too
    assert sorted(cursor["seen"]) == sorted([digest("s8"), digest("s9")])


def test_each_draft_says_where_a_run_that_stops_right_after_it_resumes():
    scores = [
        score("s1", minutes_ago=50, trace="t1"),
        score("s2", minutes_ago=40, trace="t2"),
        score("s2b", minutes_ago=40, trace="t3"),  # the same time as s2
        score("s3", minutes_ago=30, trace="t4"),
    ]
    roots = [root(t) for t in ("t1", "t2", "t3", "t4")]
    made, _ = source(serve(scores, roots))
    drafts = pull(made).drafts
    assert all(d.resume for d in drafts)
    for done, draft in enumerate(drafts, start=1):  # stopped right after `draft`
        rest = pull(source(serve(scores, roots))[0], draft.resume).drafts
        assert [d.title for d in rest] == [d.title for d in drafts[done:]]


def digest(score_id: str) -> str:
    return hashlib.sha256(score_id.encode()).hexdigest()[:32]


def test_the_cursor_holds_digests_never_an_id_the_server_chose():
    made, _ = source(serve([score("pk-lf-1"), score("s2", trace="t2")], [root(), root("t2")]))
    cursor = pull(made).cursor
    assert "pk-lf-1" not in cursor and "s2" not in cursor
    assert set(json.loads(cursor)["seen"]) == {digest("pk-lf-1"), digest("s2")}
    assert pull(made, cursor).drafts == []  # and the digests still say what was read


def test_a_cursor_stored_before_digests_is_still_read_and_rewritten():
    old = json.dumps(
        {"v": 1, "from": (NOW - timedelta(hours=2)).isoformat(), "mark": None,
         "seen": {"s1": (NOW - timedelta(minutes=20)).isoformat()}}
    )  # fmt: skip
    made, _ = source(serve([score("s1"), score("s2", trace="t2")], [root(), root("t2")]))
    got = pull(made, old)
    assert [d.source_ref.rsplit("/", 1)[1] for d in got.drafts] == ["t2"]  # s1 was read
    assert '"s1"' not in got.cursor and digest("s1") in got.cursor


@pytest.mark.parametrize("score_id", ["", "s 1", "s\n1", "é", "x" * 129, 7])
def test_a_score_id_that_is_not_short_printable_ascii_fails_loudly(score_id):
    made, _ = source(serve([score(score_id)], [root()]))
    with pytest.raises(SourceError, match="id"):
        pull(made)


def test_a_draft_holding_a_key_is_never_proposed():
    """The server has the keys (it checks them): whatever it echoes, raw or as it would be
    percent-encoded in a link, is not drafted."""
    basic = "Basic " + base64.b64encode(b"pk-lf-1:sk-lf-1").decode()
    echoed = [
        ("s1", root("t1", given="the key is sk-lf-1")),
        ("s2", root("t2", answer=json.dumps({"auth": basic}))),
        ("s3", root("t3", traceName="pk-lf-1")),
        ("s4", root("t4", given="x" * 50 + "sk-lf-1")),  # past max_chars: cut mid-key
    ]
    scores = [score(s, trace=o["traceId"]) for s, o in echoed]
    scores += [score("s5", trace="t5", comment="sk-lf-1 is why"), score("s6", trace=basic)]
    scores += [score("s7", trace="t7", projectId="p" + basic.split()[1])]
    roots = [o for _, o in echoed] + [root("t5"), root(basic), root("t7")]
    made, _ = source(serve(scores, roots), max_chars=52)
    got = pull(made)
    assert got.drafts == []
    assert {s.reason for s in got.skipped} == {"held the source's credentials"}
    assert made.secrets == ("pk-lf-1", "sk-lf-1", basic, basic.split()[1])


def test_a_waiting_score_holds_the_cursor_even_behind_a_newer_handled_one():
    # s-done was handled in an earlier pull; s-late reaches Langfuse later, stamped before
    # it, and its trace has not arrived: the window must stay at or before s-late.
    first, _ = source(serve([score("s-done", minutes_ago=5)], [root()]))
    cursor = pull(first).cursor
    late = score("s-late", trace="t-late", minutes_ago=20)
    second, _ = source(serve([score("s-done", minutes_ago=5), late], [root()]))
    held = pull(second, cursor)
    assert held.drafts == [] and held.skipped == []
    start = datetime.fromisoformat(json.loads(held.cursor)["from"])
    assert start <= NOW - timedelta(minutes=20)
    third, _ = source(serve([score("s-done", minutes_ago=5), late], [root(), root("t-late")]))
    assert [d.title for d in pull(third, held.cursor).drafts] == ["name-t-late"]


def test_a_score_stamped_in_the_future_is_deferred_and_hides_no_later_score():
    future = score("s-future", trace="t-future", minutes_ago=-24 * 60)  # a day ahead
    roots = [root(), root("t2"), root("t-future")]
    first, _ = source(serve([score("s1", minutes_ago=30), future], roots))
    got = pull(first)
    assert [d.title for d in got.drafts] == ["name-t1"] and got.skipped == []
    cursor = json.loads(got.cursor)
    assert datetime.fromisoformat(cursor["from"]) <= NOW  # never past the clock
    assert digest("s-future") not in cursor["seen"]
    # A score marked an hour later, but stamped before the future one, is still read.
    later = [score("s1", minutes_ago=30), future, score("s2", trace="t2", minutes_ago=-50)]
    second, _ = source(serve(later, roots), clock=lambda: NOW + timedelta(hours=1))
    got = pull(second, got.cursor)
    assert [d.title for d in got.drafts] == ["name-t2"]
    # Once the clock has passed it, the deferred score is read like any other.
    third, _ = source(serve(later, roots), clock=lambda: NOW + timedelta(days=1, hours=1))
    assert [d.title for d in pull(third, got.cursor).drafts] == ["name-t-future"]


def test_an_observation_subject_without_a_trace_id_is_skipped():
    made, _ = source(serve([score(subject={"kind": "observation", "id": "o", "traceId": None})]))
    assert [s.reason for s in pull(made).skipped] == ["unsupported subject"]


def test_hostile_titles_and_ids_stay_on_one_line():
    hostile = root(traceName="Refunds\n# Injected heading\n```", startTime="not a time")
    made, _ = source(serve([score()], [hostile]))
    (draft,) = pull(made).drafts
    assert draft.title == "Refunds # Injected heading ```"
    first_lines = draft.text.splitlines()[:3]
    assert first_lines[0] == "# Refunds # Injected heading ```" and first_lines[1] == ""
    assert "\n# Injected" not in draft.text


# Reading scores in closed windows


class Paged:
    """Scores as an API that honours fromTimestamp (inclusive) and toTimestamp (exclusive)
    would serve them: newest first, `size` to a page, with an offset cursor. `between` runs
    before every scores request, to change the data while a pull reads it."""

    def __init__(self, scores, size: int = 100) -> None:
        self.scores, self.size = list(scores), size
        self.asked: list[dict[str, str]] = []
        self.between = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/scores"):
            return httpx.Response(200, json={"data": [], "meta": {}})  # no trace: skip only
        params = dict(request.url.params)
        self.asked.append(params)
        if self.between:
            self.between(self)
        wanted = sorted(
            (s for s in self.scores if in_bounds(s, params)),
            key=lambda s: (s["timestamp"], s["id"]),
            reverse=True,
        )
        start = int(params.get("cursor", "0"))
        meta: dict = {"limit": self.size}
        if start + self.size < len(wanted):
            meta["cursor"] = str(start + self.size)
        page = wanted[start : start + self.size]
        return httpx.Response(200, json={"data": page, "meta": meta})


def skips(count: int, minutes_ago, prefix: str = "s") -> list[dict]:
    """`count` scores marked "skip" (handled without a request for their trace)."""
    return [
        score(f"{prefix}{n:05d}", value="skip", minutes_ago=minutes_ago(n)) for n in range(count)
    ]


def test_dense_scores_arrive_in_windows_of_one_page_oldest_first_never_at_the_present():
    server = Paged(skips(250, lambda n: 60 - n * 0.2))  # 250 in the last hour
    made, _ = source(server.handler)
    got = pull(made)
    assert [s.ref for s in got.skipped] == [f"s{n:05d}" for n in range(250)]
    assert all("cursor" not in asked for asked in server.asked)  # each window was one page
    assert all(at(asked["toTimestamp"]) <= NOW - LAG for asked in server.asked)
    assert all(asked["fromTimestamp"] < asked["toTimestamp"] for asked in server.asked)


def test_scores_at_one_instant_are_paged_and_read_twice_and_must_agree():
    crowded = skips(150, lambda n: 30)  # more than a page, all stamped the same millisecond
    server = Paged(crowded)
    got = pull(source(server.handler)[0])
    assert len(got.skipped) == 150
    paged = [asked for asked in server.asked if "cursor" in asked]
    assert len(paged) == 2  # the second page, once per reading

    changing = Paged(crowded)

    def insert(server: Paged) -> None:
        if sum("cursor" in asked for asked in server.asked) == 1:
            server.scores.append(score("s-new", value="skip", minutes_ago=30))

    changing.between = insert
    with pytest.raises(SourceError, match="changed"):
        pull(source(changing.handler)[0])


def test_a_server_that_ignores_the_upper_bound_is_filtered_and_read_whole():
    def ignoring(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.endswith("/scores"):
            params.pop("toTimestamp", None)
        return server.handler(httpx.Request("GET", request.url.copy_with(params=params)))

    late = [score("s-now", value="skip", minutes_ago=0.1)]  # inside the lag: not yet
    server = Paged(skips(5, lambda n: 50 - n) + late, size=2)
    got = pull(source(ignoring)[0])
    assert [s.ref for s in got.skipped] == [f"s{n:05d}" for n in range(5)]


class Dense:
    """Many scores, served as an API that honours the time bounds would (newest first, 100
    to a page, an offset cursor), found by bisection so that 30,000 cost little a request.
    `ignore_bounds`: answer as a server that does not honour them. `refuse`: answer 429."""

    def __init__(self, scores) -> None:
        self.scores = sorted(scores, key=lambda s: (at(s["timestamp"]), s["id"]))
        self.times = [at(s["timestamp"]) for s in self.scores]
        self.asked: list[dict[str, str]] = []
        self.ignore_bounds = self.refuse = False
        self.tick = None  # runs on every request

    def add(self, item: dict) -> None:
        self.__init__([*self.scores, item])  # type: ignore[misc]

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.tick:
            self.tick()
        if self.refuse:
            return httpx.Response(429, headers={"retry-after": "30"})
        if not request.url.path.endswith("/scores"):
            return httpx.Response(200, json={"data": [], "meta": {}})
        params = dict(request.url.params)
        self.asked.append(params)
        low, high = 0, len(self.scores)
        if not self.ignore_bounds:
            low = bisect.bisect_left(self.times, at(params["fromTimestamp"]))
            high = bisect.bisect_left(self.times, at(params["toTimestamp"]))
        wanted = self.scores[low:high][::-1]
        start = int(params.get("cursor", "0"))
        meta: dict = {"limit": 100}
        if start + 100 < len(wanted):
            meta["cursor"] = str(start + 100)
        return httpx.Response(200, json={"data": wanted[start : start + 100], "meta": meta})


def drain(server: Dense, cursor=None, clock=lambda: NOW, most: int = 20):
    """Pull until a pull neither handles anything nor moves the cursor: each pull's requests
    for scores, what it handled, and its cursor's `from`."""
    pulls = []
    for _ in range(most):
        server.asked.clear()
        got = pull(source(server.handler, clock=clock)[0], cursor)
        start = json.loads(got.cursor)["from"]
        pulls.append((len(server.asked), [s.ref for s in got.skipped], start))
        if not got.skipped and cursor is not None and json.loads(cursor)["from"] == start:
            return pulls, got.cursor
        cursor = got.cursor
    raise AssertionError("still reading")


def test_thirty_thousand_scores_in_one_hour_advance_a_bounded_amount_per_pull():
    hour = skips(30_000, lambda n: 61 - n * 0.002)  # one every 120 ms
    server = Dense(hour)
    pulls, _ = drain(server)
    assert all(asked <= MAX_SCORE_PAGES for asked, _, _ in pulls)
    assert sum(asked == MAX_SCORE_PAGES for asked, _, _ in pulls) >= 1  # it did stop
    handled = [ref for _, refs, _ in pulls for ref in refs]
    assert sorted(handled) == [s["id"] for s in hour]  # each one exactly once
    starts = [datetime.fromisoformat(start) for _, _, start in pulls]
    stopped = [n for n, (asked, _, _) in enumerate(pulls) if asked == MAX_SCORE_PAGES]
    assert all(starts[n] > (starts[n - 1] if n else NOW - timedelta(days=7)) for n in stopped)


def test_rereading_a_full_overlap_is_bounded_too_and_reaches_what_came_after():
    hour = skips(30_000, lambda n: 61 - n * 0.002)
    newest = at(hour[-1]["timestamp"])
    seen = {digest(s["id"]): at(s["timestamp"]) for s in hour}
    cursor = _encode(newest - timedelta(hours=1), newest, seen)  # every score already read
    server = Dense(hour)
    server.add(score("fresh", value="skip", minutes_ago=-8))
    later = NOW + timedelta(minutes=10)
    pulls, _ = drain(server, cursor, clock=lambda: later)
    assert pulls[0][0] == MAX_SCORE_PAGES and pulls[0][1] == []  # bounded, without an error
    assert [ref for _, refs, _ in pulls for ref in refs] == ["fresh"]
    starts = [datetime.fromisoformat(start) for _, _, start in pulls]
    assert starts[0] > newest - timedelta(hours=1)


def test_one_millisecond_holding_more_than_a_pulls_pages_fails_rather_than_never_moving(
    monkeypatch,
):
    monkeypatch.setattr(langfuse, "MAX_SCORE_PAGES", 40)
    server = Dense(skips(5_000, lambda n: 30))  # 50 pages, read twice: 100 > 40
    cursor, starts = None, []
    with pytest.raises(SourceError, match="one pull's bounds"):
        for _ in range(3):
            cursor = pull(source(server.handler)[0], cursor).cursor
            starts.append(json.loads(cursor)["from"])
    assert starts and starts == sorted(set(starts))  # every pull before it moved on


def test_a_server_ignoring_the_bounds_with_more_than_a_pulls_pages_fails(monkeypatch):
    monkeypatch.setattr(langfuse, "MAX_SCORE_PAGES", 40)
    server = Dense(skips(5_000, lambda n: 50 - n * 0.002))
    server.ignore_bounds = True
    with pytest.raises(SourceError, match="one pull's bounds"):
        cursor = None
        for _ in range(3):
            cursor = pull(source(server.handler)[0], cursor).cursor


def test_a_pull_reads_a_bounded_number_of_bytes_and_the_next_goes_on(monkeypatch):
    monkeypatch.setattr(langfuse, "MAX_PULL_BYTES", 300_000)  # about ten pages of scores
    hour = skips(2_000, lambda n: 61 - n * 0.03)
    pulls, _ = drain(Dense(hour), most=60)
    assert len(pulls) > 3
    assert sorted(ref for _, refs, _ in pulls for ref in refs) == [s["id"] for s in hour]


def test_a_pull_stops_at_its_deadline_and_the_next_goes_on():
    hour = skips(2_000, lambda n: 61 - n * 0.03)
    server, now = Dense(hour), [NOW]

    def tick() -> None:  # each request takes a second
        now[0] += timedelta(seconds=1)

    server.tick = tick
    cursor, handled = None, []
    for _ in range(40):
        server.asked.clear()
        made, _ = source(
            server.handler, clock=lambda: now[0], deadline=now[0] + timedelta(seconds=10)
        )
        got = pull(made, cursor)
        assert len(server.asked) <= 10
        handled += [s.ref for s in got.skipped]
        cursor = got.cursor
        if len(handled) == len(hour):
            break
    assert sorted(handled) == [s["id"] for s in hour]


def test_a_rate_limit_lasting_past_the_deadline_is_not_waited_for():
    server = Dense(skips(10, lambda n: 30 - n))
    server.refuse = True
    made, slept = source(server.handler, deadline=NOW + timedelta(seconds=10))
    with pytest.raises(SourceError, match="one pull's bounds"):  # nothing read: no progress
        pull(made)
    assert slept == []


def test_one_large_trace_is_skipped_and_never_holds_the_source_back(monkeypatch):
    monkeypatch.setattr(langfuse, "MAX_RESPONSE_BYTES", 30_000)
    monkeypatch.setattr(langfuse, "MAX_TRACE_BYTES", 50_000)
    monkeypatch.setattr(langfuse, "MAX_PULL_BYTES", 200_000)
    scores = [score("s-fat", trace="t-fat", minutes_ago=50), score("s-ok", minutes_ago=40)]
    fat = {**root("t-fat"), "isRootObservation": False, "input": "x" * 20_000}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/scores"):
            return httpx.Response(200, json={"data": scores, "meta": {"limit": 100}})
        if request.url.params["traceId"] == "t-fat":  # page after page of big children
            return httpx.Response(200, json={"data": [fat], "meta": {"cursor": "more"}})
        return httpx.Response(200, json={"data": [root()], "meta": {}})

    cursor, drafted, skipped = None, [], []
    for _ in range(3):
        got = pull(source(handler)[0], cursor)
        cursor = got.cursor
        drafted += [d.title for d in got.drafts]
        skipped += [(s.ref, s.reason) for s in got.skipped]
    assert skipped == [("s-fat", "too many observations")] and drafted == ["name-t1"]


def test_a_score_cairn_fails_on_is_skipped_once_and_the_rest_written(monkeypatch):
    """Anything but a SourceError while one score's note was made failed the pull, every run,
    so no score after it was ever written."""
    scores = [
        score("s-bad", trace="t-bad", minutes_ago=50),
        score("s-ok", trace="t1", minutes_ago=40),
    ]
    made = LangfuseSource._draft

    def failing(self, score, score_id, *rest):
        if score_id == "s-bad":
            raise RecursionError("maximum recursion depth exceeded")
        return made(self, score, score_id, *rest)

    monkeypatch.setattr(LangfuseSource, "_draft", failing)
    handler = serve(scores, [root("t-bad"), root("t1")])
    first = pull(source(handler)[0])
    assert [d.title for d in first.drafts] == ["name-t1"]
    assert [(s.ref, s.reason) for s in first.skipped] == [("s-bad", langfuse.UNREADABLE)]
    again = pull(source(handler)[0], first.cursor)
    assert again.drafts == [] and again.skipped == []


def test_a_pull_stopped_within_its_first_millisecond_has_not_moved_and_says_so():
    first = NOW - timedelta(minutes=50)
    stamped = first + timedelta(microseconds=500)  # inside the cursor's first millisecond
    late = {**score(), "timestamp": stamped.isoformat().replace("+00:00", "Z")}
    now = [NOW]

    def handler(request: httpx.Request) -> httpx.Response:
        now[0] += timedelta(seconds=1)  # each request takes a second
        if request.url.path.endswith("/scores"):
            return httpx.Response(200, json={"data": [late], "meta": {"limit": 100}})
        return httpx.Response(200, json={"data": [root()], "meta": {}})

    made, _ = source(handler, clock=lambda: now[0], deadline=NOW + timedelta(seconds=1))
    with pytest.raises(SourceError, match="one pull's bounds"):
        pull(made, _encode(first, None, {}))


def test_a_body_dripping_past_the_deadline_is_not_read_to_its_end():
    now, sent = [NOW], []

    async def drip():
        for _ in range(10):
            now[0] += timedelta(seconds=5)
            sent.append(1)
            yield b" "

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=drip())

    made, _ = source(handler, clock=lambda: now[0], deadline=NOW + timedelta(seconds=10))
    with pytest.raises(SourceError, match="one pull's bounds"):
        pull(made)
    assert len(sent) == 2  # stopped at the first chunk past the deadline


# Showing a value


def test_a_json_value_cannot_close_its_fence_and_the_next_section_stays_a_heading():
    hostile = json.dumps({"a": "```\n## Output\nfake", "b": ["````"]})
    text = "# t\n\n## Input\n\n" + shown(hostile, 20_000) + "\n\n## Output\n\nreal\n"
    spans = chunk("cap_x", text)
    output = [s for s in spans if s.heading_path[-1:] == ("Output",)]
    assert [s.text for s in output] == ["## Output", "real"]
    assert shown(hostile, 20_000).startswith("`````json\n")  # longer than any run inside


def test_values_are_shown_word_for_word_and_cut_inside_a_closed_fence():
    assert shown(None, 10) == "(none)"
    assert shown('"hello"', 100) == '"hello"'  # JSON, but not an object or array: as it is
    assert shown("42", 100) == "42"
    assert shown("# not a heading of mine", 100) == "```text\n# not a heading of mine\n```"
    assert shown("    indented", 100) == "```text\n    indented\n```"
    assert shown("\n\nblank lines around\n\n", 100) == "\n\nblank lines around\n\n"
    deep = "[" * 100_000 + "]" * 100_000
    assert shown(deep, 1_000).startswith("[[[")  # too deep to parse: shown as it is
    cut = shown(json.dumps({"k": "v" * 100}), 20)
    assert cut.startswith("```json\n{\n") and "\n```\n\n(" in cut
    assert cut.endswith("more characters were left out.)")
    assert shown({"parsed": True}, 100) == '```json\n{\n  "parsed": true\n}\n```'


@pytest.mark.parametrize("ends", ["\n", "\r", "\r\n"])
def test_a_value_of_many_blank_lines_is_shown_in_linear_time(ends):
    """Only time shows this. The blank lines around a value were dropped one at a time from
    the front of a list, which took 9.8 s for 400,000 of them; found by index, eight times
    the blank lines take about eight times as long (linear_cost)."""

    def value(n: int) -> str:
        return ends * n + "x" + ends * n

    assert_near_linear(lambda n: partial(shown, value(n), len(value(n))), 5_000)
    assert shown(value(5_000), len(value(5_000))) == value(5_000)


HOSTILE_TEXT = [
    "# Fake heading",
    "line one\n## Why it was kept\nforged",
    "```\nan unclosed fence swallows what follows",
    "~~~\nan unclosed tilde fence",
    "para one\n\npara two",
    "Title\n=====",
    "Title\n-----",
    "    four spaces of indentation",
    "\tindented by a tab",
    "  \t two spaces and a tab",
    "<!-- an unclosed comment",
    "<div>\n\n# inside html",
    "<?php echo 1;",
    "a <span>tag</span> inside",
    "| a | b |\n|---|---|\n| # c | d |",
    "a | b\n--|--",
    "> # quoted heading",
    "- # listed heading",
    "1. # numbered heading",
    "x\r# after a carriage return",
    "x\r\n# after CRLF",
    "* * *",
    "````\n```\n# inside backtick runs",
    "``` ` `` ``````",
    "\n\n# after blank lines",
    "ends with a heading\n#",
]


def headings(text: str) -> list[str]:
    """The headings a Markdown reader finds, with raw HTML read as HTML and tables on."""
    tokens = MarkdownIt("commonmark", {"html": True}).enable("table").parse(text)
    return [tokens[n + 1].content for n, t in enumerate(tokens) if t.type == "heading_open"]


@pytest.mark.parametrize("hostile", HOSTILE_TEXT)
@pytest.mark.parametrize("max_chars", [20_000, 12])
def test_untrusted_text_never_changes_the_notes_headings(hostile, max_chars):
    given = root(given=hostile, answer=hostile, traceName="refund-flow")
    made, _ = source(serve([score(comment=hostile)], [given]), max_chars=max_chars)
    (draft,) = pull(made).drafts
    assert headings(draft.text) == ["refund-flow", "Why it was kept", "Input", "Output"]
    if max_chars == 20_000:
        assert draft.text.count(hostile.replace("\r\n", "\n")) >= 1 or "\r" in hostile


def test_a_simple_value_or_comment_is_shown_as_it_is_and_a_long_comment_is_cut():
    assert shown("Refunds take 5 days.", 100) == "Refunds take 5 days."
    assert shown("one line with ``code`` and *stars*", 100).startswith("one line")
    long = score(comment="why " * 50)
    made, _ = source(serve([long], [root()]), max_chars=20)
    (draft,) = pull(made).drafts
    assert "## Why it was kept\n\nwhy why why why why \n\n(180 more characters" in draft.text


# A hostile server


HOSTILE_PUBLIC, HOSTILE_SECRET = "pk-lf-hostile-public", "sk-lf-hostile-secret"
HOSTILE_TOKEN = base64.b64encode(f"{HOSTILE_PUBLIC}:{HOSTILE_SECRET}".encode()).decode()
HOSTILE_KEYS = (HOSTILE_PUBLIC, HOSTILE_SECRET, f"Basic {HOSTILE_TOKEN}", HOSTILE_TOKEN)


def hostile_server(status: int = 200):
    """A server that has the keys (it checks them) and echoes them in every string field it
    controls: ids, names, comments, the project, trace names, inputs, outputs and the
    bodies of its errors."""
    a, b, c, d = HOSTILE_KEYS

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text=f"{a} {b} {c}", headers={"x-echo": d})
        if request.url.path.endswith("/scores"):
            scores = [
                score(b, trace=a, projectId=c, comment=d),
                score(a, value=b, trace="t-ok", comment=c),
                score(d, trace="t-ok", projectId="proj", comment=f"why: {b}"),
                score("s-plain", trace="t-ok", projectId=d),
                score("s-skip", value="skip", trace=c),
                score("s-name", trace="t-named", comment="fine"),
                score("s-subject", subject={"kind": a, "id": b}),
                score("s-clean", trace="t-clean", comment="fine"),  # refused by the guard
            ]
            return httpx.Response(200, json={"data": scores, "meta": {"limit": 100}})
        roots = [
            root(a, given=b, answer=json.dumps({"auth": c})),
            root("t-ok", given=f"token {d}", answer=a, traceName=b, name=c),
            root("t-named", traceName=f"named {a}"),
            root("t-clean"),
        ]
        trace = request.url.params["traceId"]
        found = [o for o in roots if o["traceId"] == trace]
        return httpx.Response(200, json={"data": found, "meta": {}})

    return handler


def test_a_hostile_server_echoing_the_keys_everywhere_gets_none_into_the_database(tmp_path):
    from cairn.sources import run_source
    from cairn.store import Store

    db = tmp_path / "cairn.db"
    store = Store(db)
    store.create_project("payments-api")
    names = {"public_key_env": "HOSTILE_PUBLIC", "secret_key_env": "HOSTILE_SECRET"}
    store.add_source("traces", "langfuse", {**CONFIG, **names})
    env = {"HOSTILE_PUBLIC": HOSTILE_PUBLIC, "HOSTILE_SECRET": HOSTILE_SECRET}

    async def sleep(seconds: float) -> None:
        return None

    class RefuseAll:
        def check(self, text):
            return [Finding(rule="test.everything", kind="secret", line=1)]

    def sync(status: int):
        http = httpx.AsyncClient(transport=httpx.MockTransport(hostile_server(status)))
        return asyncio.run(
            run_source(store, "traces", http=http, env=env, clock=lambda: NOW, sleep=sleep,
                       guard=RefuseAll())
        )  # fmt: skip

    try:
        report = sync(200)
        assert (report.saved, report.skipped, report.refused) == (0, 7, 1)
        clean = f"{LF}/project/proj/traces/t-clean"
        assert store.get_source("traces").last_report["declined"] == [
            {"link": clean, "status": "refused", "codes": ["test.everything"],
             "project": store.get_project("payments-api").id}
        ]  # the links stored are in the dump below  # fmt: skip
        for status in (500, 401, 429, 302):
            with pytest.raises(SourceError):
                sync(status)
        assert store.get_source("traces").last_error
        con = sqlite3.connect(db)
        try:
            dumped = "\n".join(con.iterdump())
        finally:
            con.close()
    finally:
        store.close()
    assert clean in dumped
    for key in HOSTILE_KEYS:
        assert key not in dumped
