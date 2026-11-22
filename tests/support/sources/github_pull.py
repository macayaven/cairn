"""Shared test helpers extracted from test_github_pull_unit."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from cairn.sources import SourceError
from cairn.sources import github as g
from cairn.store import Store

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
