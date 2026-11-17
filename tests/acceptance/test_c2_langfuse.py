"""C2 acceptance tests (frozen): the first built-in source, Langfuse traces.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/sources/__init__.py,
src/cairn/sources/langfuse.py and src/cairn/store/__init__.py.

The idea
- Someone, or an evaluator in Langfuse, marks a trace as worth remembering by giving it a
  CATEGORICAL score (named "cairn" unless configured) whose value is the name of the project
  it belongs in, or "skip". The score's comment says why. cairn reads those scores, fetches
  each trace's root observation, and writes a note made of the reason and the trace's own
  input and output, word for word, with a link back to the trace.

The seam (`cairn.sources`)
- A source has a `name` and `async pull(cursor) -> Pulled`. `Pulled` has `drafts` (a list
  of NoteDraft: `project`, `title`, `text`, `source_ref`, `label`), `skipped` (a list of Skip:
  `ref`, `reason`) and `cursor`, an opaque string to hand to the next pull. `SourceError`
  is raised when the source cannot be read; its message never contains a credential.

`cairn.sources.langfuse.LangfuseSource(base_url, public_key, secret_key, *, http, ...)`
- Reads only `GET /api/public/v3/scores` (name, dataType=CATEGORICAL, fromTimestamp,
  fields=details,subject, limit at most 100, cursor) and `GET /api/public/v2/observations`
  (traceId, the field groups core, basic, io and trace_context, fromStartTime and
  toStartTime, cursor), with HTTP Basic auth: the public key as user, the secret key as
  password. Options: `score_name="cairn"`, `max_chars=20_000` per input or output,
  `lookback` (how far back the first pull looks, 7 days), `trace_window` (how long before
  its score a trace may have started, 30 days; the search ends one day after the score),
  `grace` (how long to wait for a trace's observations, 1 hour), `overlap` (how far behind
  the newest handled score a pull looks again, 1 hour), `clock` and `sleep` (injected, so
  tests control time).
- Scores are handled oldest first, and by id where timestamps are equal, whatever order the
  server returns them in, across pages. A pull's cursor makes the next pull start where
  this one ended: nothing is handled twice, and nothing is lost when several scores share
  a timestamp. A score that reaches Langfuse late, stamped up to `overlap` before the
  newest one already handled, is still picked up by the next pull.
- A score is read once. Editing or deleting it in Langfuse afterwards changes nothing in
  cairn; what was noted is removed in cairn.
- A score on a trace or on an observation names that trace. Its root observation
  (`isRootObservation`) supplies the title (the trace's name, else the observation's), the
  input and the output. Other subjects are skipped ("unsupported subject"); the value
  "skip" is skipped ("marked skip").
- The note: "# <title>", a line naming the trace and its start time, "## Why it was kept"
  with the score's comment word for word (or "No reason was given."), then "## Input" and
  "## Output". A value that parses as a JSON object or array is shown indented in a
  ```json block; anything else is shown as it is; a missing value is "(none)". A value
  longer than `max_chars` is cut there and followed by a line saying how much was left out.
  `source_ref` is "<base_url>/project/<projectId>/traces/<traceId>"; `label` is "langfuse".
- A scored trace with no root observation yet is neither drafted nor skipped while the
  score is younger than `grace`: the cursor stays before it, so the next pull tries again.
  Older than that, it is skipped ("no root observation").
- 429 is retried after its Retry-After (through `sleep`), at most 5 times per request; then
  SourceError. 401 or 403: SourceError about credentials. 404 from the observations
  endpoint: SourceError saying the server must be Langfuse v4 or later. A failed pull
  returns nothing, so the caller's cursor does not move.

Wiring
- A workspace keeps its sources in the store: `add_source(name, kind, config)` ->
  SourceInfo (`name`, `kind`, `config`, `cursor`, `last_run_at`, `last_report`),
  `list_sources()`, `get_source(name)`, `remove_source(name)`. For "langfuse" the config
  holds `base_url` (https, or http on a loopback host), `projects` (the names it may write
  to; at least one), `public_key_env` and `secret_key_env` (names of environment variables)
  and optionally `score_name`. A config never holds a credential: a value that looks like
  one is a ValueError. Because a config names environment variables of the server, only
  the operator adds a source, on the command line: the web view and the MCP tools show
  sources and never create or change them. Deleting a workspace deletes its sources.
- `cairn.sources.run_source(view, name, *, http, env, gate=None, guard=None, clock=None,
  sleep=None) -> SyncReport` (`saved`, `unchanged`, `refused`, `rejected`, `skipped`): it
  pulls from the stored cursor, writes each draft through the same path as the `note` tool
  (the guard, the compiler and the gate) with via "langfuse", and then stores the new
  cursor and the report. A draft for a project the source may not write to, or that does not
  exist, is skipped. Running it again writes nothing new, and a run that died halfway can
  simply be run again: what it had already written comes back "unchanged". The audit log
  gets one "source.synced" event with the counts. A run that fails raises, moves no
  cursor and leaves its message, without credentials, in the source's `last_error`; the
  next run that works clears it.
- `cairn.sources.sync_all(store, *, http, env, ...) -> list[SyncOutcome]` (`account_id`,
  `source`, `report` or None, `error` or None) runs every source of every workspace once.
  One that fails does not stop the others. `serve --sync-every SECONDS` does this in the
  background on that interval; without the option nothing is synced by the server.
- GET /sources (web session) lists the workspace's sources with their last run. No route
  under a path that contains "/sources" accepts anything but GET.
- Command line: `source-add`, `source-list`, `source-sync` and `source-remove`.
"""

import asyncio
import base64
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.gate import lint_gate
from cairn.server import create_app
from cairn.sources import NoteDraft, SourceError, run_source, sync_all
from cairn.sources.langfuse import LangfuseSource
from cairn.store import Store

LF = "https://langfuse.example.test"
PUBLIC, SECRET = "pk-lf-test-public", "sk-lf-test-secret-do-not-leak"
PROJECT = "cm0proj000001"
NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
ENV = {"LANGFUSE_PUBLIC_KEY": PUBLIC, "LANGFUSE_SECRET_KEY": SECRET}
CONFIG = {
    "base_url": LF,
    "projects": ["payments-api", "story-bible"],
    "public_key_env": "LANGFUSE_PUBLIC_KEY",
    "secret_key_env": "LANGFUSE_SECRET_KEY",
}


def stamp(minutes_ago: float) -> str:
    moment = NOW - timedelta(minutes=minutes_ago)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


class FakeLangfuse:
    """The two read endpoints as Langfuse's OpenAPI file describes them (v4), as an httpx
    mock. Scores come back newest first and two to a page, observations one to a page and
    only with the field groups that were asked for, to make a client do the work."""

    def __init__(self) -> None:
        self.scores: list[dict] = []
        self.observations: list[dict] = []
        self.requests: list[tuple[str, dict]] = []
        self.rate_limited = 0  # answer this many requests with 429 first
        self.status_for: dict[str, int] = {}  # path -> forced status
        self.empty_first_page = False  # observations: one empty page that still has a cursor

    def score(self, score_id: str, value: str, trace: str, minutes_ago: float, **extra) -> dict:
        subject = extra.pop(
            "subject", {"kind": "observation", "id": f"obs-{trace}", "traceId": trace}
        )
        score = {
            "id": score_id,
            "projectId": PROJECT,
            "name": extra.pop("name", "cairn"),
            "dataType": extra.pop("data_type", "CATEGORICAL"),
            "value": value,
            "source": "EVAL",
            "timestamp": stamp(minutes_ago),
            "environment": "production",
            "createdAt": stamp(minutes_ago),
            "updatedAt": stamp(minutes_ago),
            "comment": extra.pop("comment", f"Kept because of {score_id}."),
            "configId": None,
            "metadata": None,
            "subject": subject,
        }
        assert not extra
        self.scores.append(score)
        return score

    def trace(self, trace: str, name: str, given, answer, minutes_ago: float = 30) -> None:
        def text(value):
            return value if isinstance(value, str) or value is None else json.dumps(value)

        root = {
            "id": f"obs-{trace}",
            "traceId": trace,
            "startTime": stamp(minutes_ago),
            "endTime": stamp(minutes_ago - 0.1),
            "projectId": PROJECT,
            "parentObservationId": None,
            "type": "SPAN",
            "name": "handle-request",
            "level": "DEFAULT",
            "environment": "production",
            "isRootObservation": True,
            "input": text(given),
            "output": text(answer),
            "metadata": {},
            "traceName": name,
            "tags": ["prod"],
        }
        child = {
            **root,
            "id": f"child-{trace}",
            "parentObservationId": root["id"],
            "type": "GENERATION",
            "name": "llm-call",
            "isRootObservation": False,
            "startTime": stamp(minutes_ago - 0.01),
            "input": "the child's input must not be used",
            "output": "the child's output must not be used",
        }
        self.observations += [child, root]

    @staticmethod
    def _page(items: list, query: dict, size: int) -> httpx.Response:
        start = (
            int(base64.urlsafe_b64decode(query["cursor"][0]).decode()) if "cursor" in query else 0
        )
        meta: dict = {"limit": int(query.get("limit", ["50"])[0])}
        if start + size < len(items):
            meta["cursor"] = base64.urlsafe_b64encode(str(start + size).encode()).decode()
        return httpx.Response(200, json={"data": items[start : start + size], "meta": meta})

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, query = request.url.path, parse_qs(request.url.query.decode())
        self.requests.append((path, {k: v[0] for k, v in query.items()}))
        expected = "Basic " + base64.b64encode(f"{PUBLIC}:{SECRET}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx.Response(401, json={"message": "Invalid credentials"})
        if path in self.status_for:
            return httpx.Response(self.status_for[path], json={"message": "refused"})
        if self.rate_limited > 0:
            self.rate_limited -= 1
            return httpx.Response(429, headers={"retry-after": "2"}, json={"message": "slow down"})
        if path == "/api/public/v3/scores":
            if int(query.get("limit", ["50"])[0]) > 100:
                return httpx.Response(400, json={"message": "limit"})
            wanted = [
                s
                for s in self.scores
                if s["name"] in query.get("name", [s["name"]])[0].split(",")
                and s["dataType"] in query.get("dataType", [s["dataType"]])[0].split(",")
                and moment(s["timestamp"]) >= moment(query["fromTimestamp"][0])
            ]
            wanted.sort(key=lambda s: s["timestamp"], reverse=True)
            if "subject" not in query.get("fields", [""])[0]:
                wanted = [{k: v for k, v in s.items() if k != "subject"} for s in wanted]
            if "details" not in query.get("fields", [""])[0]:
                wanted = [{k: v for k, v in s.items() if k != "comment"} for s in wanted]
            return self._page(wanted, query, 2)
        if path == "/api/public/v2/observations":
            if query.get("parseIoAsJson") == ["true"] or "traceId" not in query:
                return httpx.Response(400, json={"message": "bad request"})
            if self.empty_first_page and "cursor" not in query:
                cursor = base64.urlsafe_b64encode(b"0").decode()
                return httpx.Response(200, json={"data": [], "meta": {"cursor": cursor}})
            if "fromStartTime" not in query or "toStartTime" not in query:
                return httpx.Response(400, json={"message": "unbounded"})
            start, end = moment(query["fromStartTime"][0]), moment(query["toStartTime"][0])
            wanted = [
                o
                for o in self.observations
                if o["traceId"] == query["traceId"][0] and start <= moment(o["startTime"]) < end
            ]
            wanted.sort(key=lambda o: o["startTime"], reverse=True)
            groups = set(query.get("fields", ["core,basic"])[0].split(","))
            hidden = {
                "io": ("input", "output"),
                "basic": ("isRootObservation", "parentObservationId", "level"),
                "trace_context": ("traceName", "tags"),
            }
            dropped = {
                name for group, names in hidden.items() if group not in groups for name in names
            }
            wanted = [{k: v for k, v in o.items() if k not in dropped} for o in wanted]
            return self._page(wanted, query, 1)
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture
def fake():
    return FakeLangfuse()


def source_for(fake: FakeLangfuse, **options) -> SimpleNamespace:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    source = LangfuseSource(
        LF, PUBLIC, SECRET, http=http, clock=lambda: NOW, sleep=sleep, **options
    )
    return SimpleNamespace(source=source, slept=slept, http=http)


def pull(source, cursor=None):
    return asyncio.run(source.pull(cursor))


# Reading Langfuse


def test_marked_traces_become_drafts_with_the_reason_and_a_link_back(fake):
    fake.trace(
        "t-pay", "refund-flow", {"message": "Refund order 4411"}, {"reply": "Refunds take 5 days."}
    )
    fake.trace(  # it ran twenty days before somebody marked it
        "t-story",
        "story-maker",
        "Write page 3 about Mira.",
        "Mira found the red scarf.",
        minutes_ago=20 * 24 * 60,
    )
    fake.trace("t-skip", "noise", "hello", "hi")
    fake.score("s1", "payments-api", "t-pay", 50, comment="Shows the refund rule: 5 days.")
    fake.score("s2", "skip", "t-skip", 40)
    fake.score("s3", "story-bible", "t-story", 30, subject={"kind": "trace", "id": "t-story"})
    fake.score("s4", "payments-api", "t-pay", 20, subject={"kind": "session", "id": "sess-1"})
    fake.score("other", "payments-api", "t-pay", 10, name="helpfulness")
    fake.score("numeric", "0.5", "t-pay", 5, data_type="NUMERIC")

    got = pull(source_for(fake).source)
    assert [d.project for d in got.drafts] == ["payments-api", "story-bible"]  # oldest first
    pay, story = got.drafts
    assert isinstance(pay, NoteDraft) and (pay.title, pay.label) == ("refund-flow", "langfuse")
    assert pay.source_ref == f"{LF}/project/{PROJECT}/traces/t-pay"
    assert pay.text.startswith("# refund-flow\n")
    assert "## Why it was kept\n\nShows the refund rule: 5 days.\n" in pay.text
    assert '## Input\n\n```json\n{\n  "message": "Refund order 4411"\n}\n```\n' in pay.text
    assert '## Output\n\n```json\n{\n  "reply": "Refunds take 5 days."\n}\n```\n' in pay.text
    assert "t-pay" in pay.text and "the child's" not in pay.text
    assert "## Input\n\nWrite page 3 about Mira.\n" in story.text
    assert "## Output\n\nMira found the red scarf.\n" in story.text
    assert {(s.ref, s.reason) for s in got.skipped} == {
        ("s2", "marked skip"),
        ("s4", "unsupported subject"),
    }

    paths = {path for path, _ in fake.requests}
    assert paths == {"/api/public/v3/scores", "/api/public/v2/observations"}
    scores_query = next(q for p, q in fake.requests if p.endswith("/scores"))
    assert scores_query["name"] == "cairn" and scores_query["dataType"] == "CATEGORICAL"
    assert set(scores_query["fields"].split(",")) >= {"details", "subject"}
    assert int(scores_query["limit"]) <= 100
    first_look = datetime.fromisoformat(scores_query["fromTimestamp"].replace("Z", "+00:00"))
    assert first_look == NOW - timedelta(days=7)
    obs_query = next(q for p, q in fake.requests if p.endswith("/observations"))
    assert {"core", "basic", "io", "trace_context"} <= set(obs_query["fields"].split(","))
    assert moment(obs_query["fromStartTime"]) < moment(obs_query["toStartTime"])
    assert "parseIoAsJson" not in obs_query


def test_a_pull_resumes_where_the_last_one_ended(fake):
    for n in range(5):
        fake.trace(f"t{n}", f"trace-{n}", f"in {n}", f"out {n}")
        fake.score(f"s{n}", "payments-api", f"t{n}", minutes_ago=50 - n)
    fake.scores[3]["timestamp"] = fake.scores[2]["timestamp"]  # two scores at the same instant
    fake.empty_first_page = True  # an empty page that still has a cursor is not the end

    source = source_for(fake).source
    first = pull(source)
    assert [d.title for d in first.drafts] == [f"trace-{n}" for n in range(5)]
    assert first.cursor

    again = pull(source, first.cursor)
    assert again.drafts == [] and again.skipped == []

    fake.trace("t5", "trace-5", "in 5", "out 5")
    fake.score("s5", "payments-api", "t5", minutes_ago=1)
    later = pull(source, again.cursor)
    assert [d.title for d in later.drafts] == ["trace-5"]
    assert pull(source, later.cursor).drafts == []

    # A score that reaches Langfuse late, stamped before ones already handled, is not lost.
    fake.trace("t-late", "trace-late", "in late", "out late")
    fake.score("s-late", "payments-api", "t-late", minutes_ago=30)
    caught = pull(source, later.cursor)
    assert [d.title for d in caught.drafts] == ["trace-late"]
    assert pull(source, caught.cursor).drafts == []

    custom = pull(source_for(fake, score_name="remember").source)
    assert custom.drafts == []  # another score name sees none of these


def test_a_trace_that_has_not_arrived_yet_is_tried_again(fake):
    fake.trace("t-old", "old-trace", "in", "out")
    fake.score("s-old", "payments-api", "t-old", minutes_ago=50)
    fake.score("s-late", "payments-api", "t-late", minutes_ago=10)  # its observations lag
    fake.score("s-newer", "payments-api", "t-newer", minutes_ago=5)
    fake.trace("t-newer", "newer-trace", "in", "out")

    source = source_for(fake).source
    first = pull(source)
    assert [d.title for d in first.drafts] == ["old-trace"]  # it stops at the one that lags
    assert first.skipped == []

    fake.trace("t-late", "late-trace", "in", "out")
    second = pull(source, first.cursor)
    assert [d.title for d in second.drafts] == ["late-trace", "newer-trace"]

    # A scored trace that never shows up is given up on once it is older than the grace.
    fake.score("s-ghost", "payments-api", "t-ghost", minutes_ago=90)
    gone = pull(source_for(fake).source)
    assert ("s-ghost", "no root observation") in {(s.ref, s.reason) for s in gone.skipped}
    assert len(gone.drafts) == 3


def test_limits_and_refusals_do_not_lose_or_duplicate_anything(fake):
    fake.trace("t1", "trace-1", "in", "out")
    fake.score("s1", "payments-api", "t1", minutes_ago=20)

    fake.rate_limited = 2
    patient = source_for(fake)
    assert [d.title for d in pull(patient.source).drafts] == ["trace-1"]
    assert patient.slept == [2, 2]

    fake.rate_limited = 50
    stuck = source_for(fake)
    with pytest.raises(SourceError):
        pull(stuck.source)
    assert len(stuck.slept) == 5
    fake.rate_limited = 0

    wrong = LangfuseSource(
        LF,
        PUBLIC,
        "sk-lf-wrong",
        http=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
    )
    with pytest.raises(SourceError) as refused:
        pull(wrong)
    assert "credential" in str(refused.value).lower() and "sk-lf-wrong" not in str(refused.value)

    fake.status_for["/api/public/v2/observations"] = 404
    with pytest.raises(SourceError) as old:
        pull(source_for(fake).source)
    assert "v4" in str(old.value)


def test_long_and_odd_values_are_bounded_and_kept_word_for_word(fake):
    long_text = "quill " * 10_000  # 60,000 characters
    fake.trace("t-long", "long-trace", long_text, None)
    fake.trace("t-list", "list-trace", [{"role": "user", "content": "Hi"}], "not {json")
    fake.score("s1", "payments-api", "t-long", 30, comment=None)
    fake.score("s2", "payments-api", "t-list", 20)

    long, listed = pull(source_for(fake, max_chars=1_000).source).drafts
    assert "## Why it was kept\n\nNo reason was given.\n" in long.text
    assert long_text[:1_000] in long.text and long_text[:1_001] not in long.text
    assert "59,000 more characters" in long.text
    assert "## Output\n\n(none)\n" in long.text
    assert len(long.text) < 2_000
    assert '```json\n[\n  {\n    "role": "user",\n    "content": "Hi"\n  }\n]\n```' in listed.text
    assert "## Output\n\nnot {json\n" in listed.text


# Wiring it to a workspace


@pytest.fixture
def env(tmp_path, fake):
    db = tmp_path / "cairn.db"
    store = Store(db)
    for name in ("payments-api", "story-bible", "private"):
        store.create_project(name)
    key = store.create_key("owner")
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    yield SimpleNamespace(store=store, db=db, key=key, http=http)
    store.close()


def sync(env, **options):
    async def sleep(seconds: float) -> None:
        return None

    return asyncio.run(
        run_source(
            env.store, "traces", http=env.http, env=ENV, clock=lambda: NOW, sleep=sleep, **options
        )
    )


def test_a_sync_writes_notes_like_the_note_tool_and_only_once(env, fake):
    fake.trace(
        "t-pay", "refund-flow", {"message": "Refund order 4411"}, {"reply": "Refunds take 5 days."}
    )
    fake.trace(
        "t-key",
        "leaky-trace",
        "My token is " + "ghp_" + "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zAaB3d",
        "ok",
    )
    fake.trace("t-private", "private-trace", "in", "out")
    fake.trace("t-nowhere", "nowhere-trace", "in", "out")
    fake.score("s1", "payments-api", "t-pay", 50, comment="Shows the refund rule: 5 days.")
    fake.score("s2", "payments-api", "t-key", 40)
    fake.score("s3", "private", "t-private", 30)  # a project this source may not write to
    fake.score("s4", "no-such-project", "t-nowhere", 20)

    info = env.store.add_source("traces", "langfuse", CONFIG)
    assert (info.name, info.kind, info.cursor, info.last_run_at) == (
        "traces",
        "langfuse",
        None,
        None,
    )

    report = sync(env)
    assert (report.saved, report.unchanged, report.refused, report.rejected) == (1, 0, 1, 0)
    assert report.skipped == 2

    project = env.store.get_project("payments-api")
    (note,) = env.store.list_notes(project.id)
    assert (note.via, note.label) == ("langfuse", "langfuse")
    assert note.source_ref == f"{LF}/project/{PROJECT}/traces/t-pay"
    hits = env.store.search("refunds days")
    assert hits and hits[0].project == "payments-api"
    assert env.store.list_notes(env.store.get_project("private").id) == []
    stored = dump(env.db)
    assert "ghp_" not in stored and SECRET not in stored and PUBLIC not in stored

    after = env.store.get_source("traces")
    assert after.cursor and after.last_run_at
    assert after.last_report["saved"] == 1 and after.last_report["refused"] == 1
    event = env.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind) == ("source.synced", "system")
    assert event.detail["source"] == "traces" and event.detail["saved"] == 1

    again = sync(env)
    assert (again.saved, again.unchanged, again.refused, again.skipped) == (0, 0, 0, 0)
    assert len(env.store.list_notes(project.id)) == 1

    fake.rate_limited = 50  # a failed run changes nothing and says so
    before = env.store.get_source("traces")
    assert before.last_error is None
    with pytest.raises(SourceError):
        sync(env)
    failed = env.store.get_source("traces")
    assert failed.cursor == before.cursor and failed.last_error
    assert SECRET not in failed.last_error and PUBLIC not in failed.last_error
    fake.rate_limited = 0
    sync(env)
    assert env.store.get_source("traces").last_error is None


def test_a_run_that_dies_halfway_can_be_run_again(env, fake):
    for n in (1, 2):
        fake.trace(f"t{n}", f"trace-{n}", f"Quill question {n}", f"Quill answer {n}")
        fake.score(f"s{n}", "payments-api", f"t{n}", minutes_ago=50 - n)
    env.store.add_source("traces", "langfuse", CONFIG)
    seen = []

    def dies_on_the_second(page, spans):
        seen.append(page)
        if len(seen) == 2:
            raise RuntimeError("the process died here")
        return lint_gate(page, spans)

    with pytest.raises(RuntimeError):
        sync(env, gate=dies_on_the_second)
    assert env.store.get_source("traces").cursor is None

    report = sync(env)
    assert (report.saved, report.unchanged, report.refused, report.rejected) == (1, 1, 0, 0)
    project = env.store.get_project("payments-api")
    assert len(env.store.list_notes(project.id)) == 2
    assert {hit.title for hit in env.store.search("Quill answer")} == {"trace-1", "trace-2"}
    assert sync(env).saved == 0


def test_one_pass_over_every_workspace_survives_a_source_that_fails(env, fake):
    fake.trace("t-pay", "refund-flow", "in", "Refunds take 5 days.")
    fake.score("s1", "payments-api", "t-pay", 50)
    env.store.add_source("traces", "langfuse", CONFIG)
    brook = env.store.for_account(env.store.create_account("Brook").id)
    brook.create_project("payments-api")
    brook.add_source(
        "brook-traces",
        "langfuse",
        {**CONFIG, "projects": ["payments-api"], "secret_key_env": "BROOK_SECRET_KEY"},
    )

    async def sleep(seconds: float) -> None:
        return None

    outcomes = asyncio.run(
        sync_all(env.store, http=env.http, env=ENV, clock=lambda: NOW, sleep=sleep)
    )
    by_source = {o.source: o for o in outcomes}
    assert set(by_source) == {"traces", "brook-traces"}
    worked, failed = by_source["traces"], by_source["brook-traces"]
    assert worked.error is None and worked.report.saved == 1
    assert worked.account_id == env.store.account_id
    assert failed.report is None and "BROOK_SECRET_KEY" in failed.error
    assert failed.account_id == brook.account_id
    assert brook.get_source("brook-traces").last_error and brook.search("refunds") == []


def test_a_source_config_never_holds_a_credential(env):
    for leaked in (
        {**CONFIG, "secret_key_env": SECRET},
        {**CONFIG, "public_key": PUBLIC},
        {**CONFIG, "base_url": f"https://{PUBLIC}:{SECRET}@langfuse.example.test"},
    ):
        with pytest.raises(ValueError):
            env.store.add_source("traces", "langfuse", leaked)
    for invalid in (
        {**CONFIG, "base_url": "http://langfuse.example.test"},
        {**CONFIG, "projects": []},
        {k: v for k, v in CONFIG.items() if k != "secret_key_env"},
    ):
        with pytest.raises(ValueError):
            env.store.add_source("traces", "langfuse", invalid)
    with pytest.raises(ValueError):
        env.store.add_source("traces", "carrier-pigeon", {})
    assert env.store.list_sources() == []

    env.store.add_source("traces", "langfuse", CONFIG)
    with pytest.raises(ValueError):
        env.store.add_source("traces", "langfuse", CONFIG)  # the name is taken

    async def run_without_keys():
        return await run_source(env.store, "traces", http=env.http, env={})

    with pytest.raises(SourceError) as missing:
        asyncio.run(run_without_keys())
    assert "LANGFUSE_PUBLIC_KEY" in str(missing.value)
    assert env.store.remove_source("traces") is True and env.store.remove_source("traces") is False


def test_sources_belong_to_a_workspace_and_show_in_the_web_view(env, fake):
    fake.trace("t-pay", "refund-flow", "in", "out")
    fake.score("s1", "payments-api", "t-pay", 50)
    env.store.add_source("traces", "langfuse", CONFIG)
    sync(env)
    other = env.store.for_account(env.store.create_account("Brook").id)
    assert other.list_sources() == [] and other.get_source("traces") is None
    other.create_project("payments-api")
    other.add_source("brook-traces", "langfuse", {**CONFIG, "projects": ["payments-api"]})
    assert env.store.get_source("brook-traces") is None
    assert [s.name for s in env.store.list_sources()] == ["traces"]

    app = create_app(env.store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        assert client.get("/sources", follow_redirects=False).status_code == 303
        client.post("/login", data={"key": env.key}, headers=SAME_ORIGIN)
        before = dump(env.db)
        page = client.get("/sources")
        assert page.status_code == 200
        for needle in ("traces", "langfuse", "payments-api", "langfuse.example.test"):
            assert needle in page.text
        assert "brook-traces" not in page.text
        assert SECRET not in page.text and PUBLIC not in page.text
        assert dump(env.db) == before  # reading the page wrote nothing
        added = client.post(
            "/sources", data={"name": "more", "kind": "langfuse"}, headers=SAME_ORIGIN
        )
        assert added.status_code in (404, 405)  # only the operator adds a source
        assert [s.name for s in env.store.list_sources()] == ["traces"]

    def routes(found, prefix=""):
        for route in found:
            path = prefix + getattr(route, "path", "")
            if hasattr(route, "routes"):
                yield from routes(route.routes, path)
            else:
                yield path, set(getattr(route, "methods", None) or ())

    about_sources = [(path, methods) for path, methods in routes(app.routes) if "/sources" in path]
    assert about_sources and all(methods <= {"GET", "HEAD"} for _, methods in about_sources)

    env.store.delete_account(other.account_id)
    assert "brook-traces" not in dump(env.db)
