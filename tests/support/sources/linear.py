"""Shared test helpers extracted from test_linear_unit."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from cairn.sources import linear as lin

VAR = "LINEAR_UNIT_KEY"

KEY = "lin-unit-" + "made-up-key-" + "4f2a9c1e"  # never a real key

NOW = datetime(2099, 6, 1, 12, 0, tzinfo=UTC)

SINCE = NOW - timedelta(days=20)

BASE = "https://linear.example.test/graphql"


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
