"""C11 acceptance tests (frozen): the GitHub source, the finished work of one repository.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/sources/github.py and
src/cairn/sources/__init__.py. The API's request and response shapes are recorded in
docs/github-api.md (API version 2022-11-28). Where those notes say [not stated] or CONFLICT,
the connector depends on neither reading, and the stand-in below behaves either way.

The kind
- `KINDS["github"]` (cairn.sources.github). It starts off. Its notes are written with the
  via "github". It is not withheld from a model. It says what a source reads
  (`describe`, naming the repository) and what switching it on reads; the project it proposes
  for a source that names none is "github", whatever the repository (as `source-add` says).
- The config is a JSON object. `check` returns it with the defaults filled in, or raises
  ValueError naming the setting at fault, never a value that could be a credential:
  - `token_env` (required): the name of the environment variable that holds the token. A
    value that looks like a credential (GitHub's token prefixes among them) is refused.
  - `repo` (required): `owner/name`, each part as GitHub allows it; no URL, no ".git".
  - `projects`: at most one project name; may be empty while the source is off.
  - `items`: a non-empty subset of ["pulls", "issues", "releases"]; all three unless given.
  - `comments` (default true): also an item's conversation and a pull request's reviews.
  - `settle_minutes` (default 30, 0 to 1440), `lookback_days` (default 30, 1 to 365).
  - `base_url`: "https://api.github.com" unless given; https, or http on this machine; no
    user or password, nothing that looks like a credential.
  - No other setting.
- The token is read from the environment when the source is opened (a missing or empty
  variable: SourceError naming it), sent only as `Authorization: Bearer <token>` and only to
  `base_url`, and is among the source's `secrets`. Every request carries `Accept:
  application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28` and a User-Agent of the
  connector's own (not the HTTP library's default). No request goes to another host, even
  when a `Link` header points there.

What it reads
- A pull request once it is merged (`merged_at` at or after `since`, the last switch-on); one
  closed without merging, never. An issue once closed as completed, as not planned, or with
  no reason (older issues), `closed_at` at or after `since`; one closed as a duplicate,
  never. An item of the issues listing that has a `pull_request` key is a pull request. A
  release once published (`draft` false, `published_at` at or after `since`); a prerelease
  is written and says so.
- Each item once, for as long as the source exists: after it was written, an edit, a
  comment, a reopening and a second close, or a release published again, changes nothing in
  cairn, across runs and across switching the source off and on. Each draft names its item
  (`NoteDraft.item`: "pull:<number>", "issue:<number>", "release:<id>") and the runner never
  writes an item its source wrote before (I1). The kind's own digests stay bounded by
  `lookback_days` (they spare a pull fetching again what it wrote lately), so an item closed
  again long after it was written may be proposed again: it is not written again. An item
  that finished at or after `since` is written however long after it finished it is found,
  whether or not its close moved `updated_at`.
- Only once settled: finished at least `settle_minutes` ago by the injected clock. An issue
  reopened before it was read is written once it is closed again and settled.

How it finds them
- Every pull lists `GET /repos/{owner}/{repo}/issues` with exactly `state=closed`,
  `sort=updated`, `direction=asc`, `since` (YYYY-MM-DDTHH:MM:SSZ, a fixed overlap before
  the cursor's mark; on the first pull at most a day before `since`) and `per_page=100`,
  then follows each `Link` rel="next" as given (it may point at /repositories/{id}/...) to
  the last page. The same for `GET .../releases?per_page=100`. A merged pull request costs
  one `GET .../pulls/{number}` (`merged_by`, the base branch), whatever the pages repeat.
- The daily check: a pull also searches `GET /search/issues` with the qualifiers
  `repo:OWNER/NAME is:pr is:merged merged:>=T` and `repo:OWNER/NAME is:issue is:closed
  closed:>=T`, nothing else (a date without a time may be read in any time zone: the
  stand-in reads it in the latest). T reaches back to the last check whose finds were all
  written, less a day (the first check: to `since`), however long ago that is
  (`lookback_days` bounds the kind's digests and nothing else); the test allows a day's
  margin below it. A check whose finds were not all written (past the pull's 25, or some
  still settling) is made again by the next pull with the same T; a check whose finds were
  all written is not made again within a day by the injected clock. So an item whose close
  or merge did not move its `updated_at` is written within about a day of the next run, and
  nothing found is lost. When search has more than it returns (`total_count` above what its
  pages gave, or `incomplete_results`), the check writes what it was given, is done once
  that is written (not made again with the same T, its window not split), and the pull's
  skips say the daily check found more than search returns. A refused search (403, 422)
  does not fail the run: the listing's results are written and the run's report says the
  search was refused.
- A pull's items are written in the connector's order: oldest finish first, then pull
  requests, issues, releases, then by number or id; pages shuffled or repeating an item give
  the same drafts. At most 25 items a pull; the rest wait for the next, none lost, none
  reordered, none twice. `items` decides what is asked for and what is written.
- The cursor is JSON: never an item's number, id, title or the token, and bounded as items
  pass. At the pull's deadline it returns what it finished; every draft's `resume` (when
  set) goes on without loss or repeat.

The note (`title`, `item`, `source_ref` = the item's `html_url`; no `topic`)
- Title `owner/name#123: The title`, or `owner/name v1.2.3: Name` (`owner/name v1.2.3` without
  a name), at most 200 characters, cut at the end: `owner/name#123` is never cut. No topic:
  each item has a page of its own, named by its title, so two pull requests merged in one
  month are two pages.
- Text: a heading with the title, the facts (pull request: Opened by, Merged by, Merged,
  Into, Labels; issue: Opened by, Closed, Closed as ("completed", "not planned"), Labels;
  release: Tag, Published, Published by, and "Prerelease"), the body as GitHub gives it
  (null, empty or absent: still written), then with `comments` a "Comments" heading and the
  conversation in order, each with its author and date, and a pull request's reviews that
  carry a body, with their verdict in words. Review comments on lines are not asked for.
- People by login; a bot's login followed by "(bot)"; a deleted account (null, or GitHub's
  "ghost" user) written the same way either way and never as "ghost". No email address.
- On a GitHub host, an address whose query holds `jwt`, `token` or `X-Amz-Signature` is kept
  without its query, in a body and in a comment; other addresses are untouched.
- Bounds: a body or a comment past a note's bound is cut with a line saying so, and more
  comments than a stated number keep the first and say how many were left out; such a note
  is still written through the runner. A line the connector writes is plain text (no
  emphasis around a number). 250 comments are read in three requests, in order.

When GitHub says no (a message never holds the token; GitHub's own message only if it
passes the credential check)
- 401: the run fails naming the variable and the repository. 404 on the issues or the
  releases listing: the same, and it names the permission and the organisation's approval.
- A rate limit (403 or 429 with `x-ratelimit-remaining: 0` or with `retry-after`): the pull
  stops at once (no request after it, no wait) and returns what it finished; the next pull
  goes on, nothing lost or doubled. A 403 without either header is a refusal: the run fails
  naming what it asked for (the listing, a pull request, comments, reviews, releases).
- 5xx, a timeout, a body not JSON or not the documented shape: the run fails, the cursor
  does not move, nothing is written; the next run writes the rest once.
- 410 on an item's own addresses: that item is skipped once with a reason, the others are
  written.
- No token in any reason, log line, cursor or stored field.
"""

import asyncio
import hashlib
import itertools
import json
import logging
import random
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit

import httpx
import pytest

from cairn.server import main
from cairn.sources import KINDS, SourceError, run_source, withheld_via
from cairn.store import Store

OWNER, NAME = "quillworks", "quill"
REPO = f"{OWNER}/{NAME}"
REPO_ID = 70011
TOKEN_ENV = "GITHUB_C11_TOKEN"
TOKEN = "github" + "_pat_" + "11C11MADEUP0" + "_" + "f3a9c7e" + "Qz" * 9 + "x"  # never real
WRONG_TOKEN = "github" + "_pat_" + "11C11WRONG00" + "_" + "b2d4" + "Wy" * 10
ENV = {TOKEN_ENV: TOKEN}
CANARY = "c11-" + "body-" + "canary-5e2b"  # in every error message the stand-in sends
API, WEB = "https://api.github.com", "https://github.com"
GHE_API, GHE_WEB = "https://ghe.example.test/api/v3", "https://ghe.example.test"
API_VERSION = "2022-11-28"
ACCEPT = "application/vnd.github+json"
NOW = datetime(2099, 3, 10, 12, 0, tzinfo=UTC)
SINCE = datetime(2099, 3, 1, 3, 0, tzinfo=UTC)  # switched on early in a UTC day
MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
RELEASE_BASE = 7_100_000_000  # release ids: never in a cursor
PEOPLE = {  # login: (name, type); each also has an email address the API gives
    "ada-park": ("Ada Park", "User"),
    "brook-lane": ("Brook Lane", "User"),
    "cy-moss": ("Cy Moss", "User"),
    "dee-quinn": ("Dee Quinn", "User"),
    "quill-ci[bot]": (None, "Bot"),
}
SINCE_FORMAT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def z(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parsed(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def search_moment(value: str) -> datetime | None:
    """A search qualifier's date as the stand-in reads it. A date alone is read in the latest
    time zone there is (UTC-12), since the notes do not say which; a time needs its zone."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        day = datetime.fromisoformat(value)
        return day.replace(tzinfo=timezone(timedelta(hours=-12))).astimezone(UTC)
    found = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(Z|[+-]\d{2}:\d{2})", value)
    if not found:
        return None
    zone = "+00:00" if found.group(2) == "Z" else found.group(2)
    return datetime.fromisoformat(found.group(1) + zone).astimezone(UTC)


def pr_ref(n: int, web: str = WEB, repo: str = REPO) -> str:
    return f"{web}/{repo}/pull/{n}"


def issue_ref(n: int, web: str = WEB, repo: str = REPO) -> str:
    return f"{web}/{repo}/issues/{n}"


def release_ref(tag: str, web: str = WEB, repo: str = REPO) -> str:
    return f"{web}/{repo}/releases/tag/{tag}"


def refs(pulled) -> list[str]:
    return [draft.source_ref for draft in pulled.drafts]


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def lines_with(text: str, label: str, without: str | None = None) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if label in line and (without is None or without not in line)
    ]


def fact(text: str, label: str, without: str | None = None) -> str:
    """The first line holding `label` (and not `without`): where a fact is said."""
    found = lines_with(text, label, without)
    assert found, (label, text[:2000])
    return found[0]


@dataclass
class Comment:
    id: int
    author: str | None
    body: str | None
    at: datetime


@dataclass
class Review:
    id: int
    author: str | None
    body: str
    state: str
    at: datetime


@dataclass
class Item:
    """An issue or a pull request at the stand-in."""

    number: int
    pull: bool
    title: str
    body: str | None
    author: str | None  # a login; None: the account was deleted
    created: datetime
    updated: datetime
    state: str = "open"
    reason: str | None = None
    closed: datetime | None = None
    merged: datetime | None = None
    merged_by: str | None = None
    base: str = "main"
    labels: list = field(default_factory=list)  # names; ("text", name) is sent as a string
    comments: list = field(default_factory=list)
    reviews: list = field(default_factory=list)
    gone_on: set = field(default_factory=set)  # its own addresses that answer 410
    body_absent: bool = False


@dataclass
class Release:
    id: int
    tag: str
    name: str | None
    body: str | None
    author: str | None
    created: datetime
    published: datetime | None
    draft: bool = False
    prerelease: bool = False


class FakeGitHub:
    """The REST API (version 2022-11-28) of one repository as docs/github-api.md describes
    it, as an httpx mock: the issues listing (state, sort, direction, since), search with the
    qualifiers the connector uses and its 1,000-result ceiling, a pull request, an item's
    comments, a pull request's reviews, the releases listing; paging by per_page and page
    with `Link` headers whose pages are under /repositories/{id}/. Every request is kept with
    its pull, its parameters, its headers and its status, and `problems` lists every one that
    broke the rules (a header missing or wrong, another host, an address the connector has
    no business asking). Where the notes leave it open it behaves either way: a close or a
    merge moves `updated_at` or not (`moves`), `since` and `>=` inclusive or not
    (`exclusive`), pages in order or shuffled with an item repeated (`shuffle`), fewer items
    a page than asked (`page_cap`), a deleted account as null or as the ghost user
    (`ghost`), an unmerged pull request's `merged_at` null or absent (`unmerged_absent`), a
    search refused (`search_refused`), a next page on another host (`foreign_next`)."""

    def __init__(self, clock: Clock, *, api: str = API, web: str = WEB, repo: str = REPO):
        self.clock = clock
        self.api, self.web, self.repo = api, web, repo
        self.owner, self.name = repo.split("/")
        self.items: dict[int, Item] = {}
        self.releases: dict[int, Release] = {}
        self.requests: list[SimpleNamespace] = []
        self.slept: list[float] = []
        self.gen = 0
        self.tick = timedelta(0)  # the clock moves this much with every request
        self.moves = True
        self.exclusive = False
        self.shuffle = False
        self.page_cap: int | None = None
        self.ghost = False
        self.unmerged_absent = False
        self.search_refused: int | None = None
        self.incomplete = False  # search times out: every sixth result left out, and it says so
        self.foreign_next = False
        self.faults: list[tuple[str, int | None, tuple]] = []  # one-shot
        self.always: dict[str, tuple] = {}  # endpoint ("*": every one) -> fault
        self._ids = itertools.count(5_000_001)

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def new_pull(self) -> None:
        self.gen += 1

    # Building the repository

    def pull_request(self, n: int, title: str = "Retry the refund webhook", **given) -> Item:
        return self._item(n, True, title, **given)

    def issue(self, n: int, title: str = "Refunds stall on retry", **given) -> Item:
        return self._item(n, False, title, **given)

    def _item(self, n, pull, title, *, merged=None, closed=None, reason="completed", **given):
        created = given.pop("created", SINCE - 20 * DAY)
        merged_by = given.pop("merged_by", "brook-lane")
        updated = given.pop("updated", None)
        given.setdefault("body", f"Item {n}: the retry queue keeps its order.")
        given.setdefault("author", "ada-park")
        item = Item(number=n, pull=pull, title=title, created=created, updated=created, **given)
        item.updated = created + 10 * MINUTE
        self.items[n] = item
        if merged is not None:
            self.merge(item, merged, merged_by)
        elif closed is not None:
            self.close(item, closed, reason)
        if updated is not None:
            item.updated = updated
        return item

    def release(self, k: int, tag: str, name: str | None = None, **given) -> Release:
        published = given.pop("published", None)
        given.setdefault("body", f"Release {tag}: retries back off.")
        given.setdefault("author", "ada-park")
        created = given.pop("created", (published or NOW) - DAY)
        made = Release(id=RELEASE_BASE + k, tag=tag, name=name, created=created,
                       published=published, **given)  # fmt: skip
        if published is None:
            made.draft = True
        self.releases[made.id] = made
        return made

    def merge(self, item: Item, at: datetime, by: str | None = "brook-lane") -> None:
        item.state, item.closed, item.merged, item.merged_by = "closed", at, at, by
        self._touch(item, at)

    def close(self, item: Item, at: datetime, reason: str | None = "completed") -> None:
        item.state, item.closed = "closed", at
        item.reason = None if item.pull else reason
        self._touch(item, at)

    def reopen(self, item: Item, at: datetime) -> None:
        item.state, item.closed, item.reason = "open", None, "reopened"
        self._touch(item, at)

    def edit(self, item: Item, at: datetime, body: str) -> None:
        item.body, item.updated = body, at

    def comment(self, item: Item, at: datetime, author: str | None, body: str | None) -> Comment:
        made = Comment(next(self._ids), author, body, at)
        item.comments.append(made)
        item.updated = max(item.updated, at)
        return made

    def review(self, item: Item, at: datetime, author: str | None, state: str, body: str):
        item.reviews.append(Review(next(self._ids), author, body, state, at))

    def _touch(self, item: Item, at: datetime) -> None:
        if self.moves:
            item.updated = at

    # What the tests ask

    def of_pull(self, gen: int | None = None) -> list[SimpleNamespace]:
        gen = self.gen if gen is None else gen
        return [r for r in self.requests if r.gen == gen]

    def asked(self, endpoint: str, gen: int | None = None) -> list[SimpleNamespace]:
        return [r for r in self.of_pull(gen) if r.endpoint == endpoint]

    def problems(self) -> list[str]:
        found = []
        for r in self.requests:
            if r.endpoint in ("foreign", "other", "review comments"):
                found.append(f"{r.endpoint}: {r.url}")
            if r.accept != ACCEPT or r.version != API_VERSION:
                found.append(f"headers: {r.accept!r} {r.version!r}")
            if not r.agent or r.agent.lower().startswith("python-httpx"):
                found.append(f"user-agent: {r.agent!r}")
            if r.auth not in (f"Bearer {TOKEN}", f"Bearer {WRONG_TOKEN}"):
                found.append("authorization")
        return found

    # The API

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.clock.now += self.tick
        url = request.url
        record = SimpleNamespace(
            gen=self.gen, at=self.clock(), url=str(url), host=url.host, path=url.path,
            params=dict(url.params), endpoint=None, number=None, status=None, next=None,
            count=None, auth=request.headers.get("authorization"),
            accept=request.headers.get("accept"),
            version=request.headers.get("x-github-api-version"),
            agent=request.headers.get("user-agent"),
        )  # fmt: skip
        self.requests.append(record)
        response = self._answer(request, record)
        record.status = response.status_code
        return response

    def _rate(self, **extra) -> dict:
        reset = str(int((self.clock() + HOUR).timestamp()))
        return {
            "x-ratelimit-limit": "5000",
            "x-ratelimit-remaining": "4321",
            "x-ratelimit-used": "679",
            "x-ratelimit-reset": reset,
            "x-ratelimit-resource": "core",
            **extra,
        }

    def _error(self, request, status: int, message: str, **headers) -> httpx.Response:
        echoed = request.headers.get("authorization")
        body = {
            "message": f"{message} ({CANARY}: {echoed})",
            "documentation_url": "https://docs.github.com/rest",
            "status": str(status),
        }
        return httpx.Response(status, json=body, headers=self._rate(**headers))

    def _fault(self, request, fault: tuple) -> httpx.Response:
        what = fault[0]
        if what == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if what == "not json":
            return httpx.Response(200, text=f"<html>{CANARY}</html>", headers=self._rate())
        if what == "wrong shape":
            return httpx.Response(200, json={"items": CANARY}, headers=self._rate())
        if what == "limit":
            _, status, how = fault
            headers = (
                {"x-ratelimit-remaining": "0"} if how == "remaining" else {"retry-after": "60"}
            )
            return self._error(request, status, "API rate limit exceeded", **headers)
        if what == "permission":
            return self._error(request, 403, "Resource not accessible by personal access token")
        return self._error(request, fault[1], "Refused")

    def _which(self, rest: str) -> tuple[str, int | None]:
        if rest == "/search/issues":
            return "search", None
        sub = None
        found = re.fullmatch(r"/repos/([^/]+)/([^/]+)(/.*)", rest)
        if found and f"{found.group(1)}/{found.group(2)}".lower() == self.repo.lower():
            sub = found.group(3)
        found = re.fullmatch(rf"/repositories/{REPO_ID}(/.*)", rest)
        if found:
            sub = found.group(1)
        if sub is None:
            return "other", None
        for pattern, endpoint in (
            (r"/issues", "issues"),
            (r"/issues/(\d+)", "issue"),
            (r"/issues/(\d+)/comments", "comments"),
            (r"/pulls/(\d+)", "pull"),
            (r"/pulls/(\d+)/reviews", "reviews"),
            (r"/pulls/(\d+)/comments", "review comments"),
            (r"/releases", "releases"),
        ):
            matched = re.fullmatch(pattern, sub)
            if matched:
                return endpoint, int(matched.group(1)) if matched.groups() else None
        return "other", None

    def _answer(self, request, record) -> httpx.Response:
        url, api = request.url, urlsplit(self.api)
        prefix = api.path.rstrip("/")
        same = (url.scheme, url.host, url.port) == (api.scheme, api.hostname, api.port)
        if not same or not url.path.startswith(prefix + "/"):
            record.endpoint = "foreign"
            return self._error(request, 404, "Not Found")
        record.endpoint, record.number = self._which(url.path[len(prefix) :])
        if record.auth != f"Bearer {TOKEN}":
            return self._error(request, 401, "Bad credentials")
        fault = self.always.get(record.endpoint) or self.always.get("*")
        for index, (endpoint, number, one) in enumerate(self.faults):
            if endpoint == record.endpoint and number in (None, record.number):
                del self.faults[index]
                fault = one
                break
        if fault:
            return self._fault(request, fault)
        endpoint, n = record.endpoint, record.number
        item = self.items.get(n) if n is not None else None
        if item is not None and endpoint in item.gone_on:
            return self._error(request, 410, "Gone")
        if endpoint == "issues":
            return self._issues(request, record)
        if endpoint == "search":
            return self._search(request, record)
        if endpoint == "releases":
            rows = sorted(
                self.releases.values(), key=lambda r: (r.created, r.id), reverse=True
            )  # the order is not stated: newest commit first
            chunk, headers = self._page(record, rows, f"/repositories/{REPO_ID}/releases")
            if chunk is None:
                return self._error(request, 422, "Validation Failed")
            return self._ok([self._release(r) for r in chunk], headers)
        if endpoint == "issue" and item is not None:
            return self._ok(self._issue(item))
        if endpoint == "pull" and item is not None and item.pull:
            return self._ok(self._pull(item))
        if endpoint == "comments" and item is not None:
            rows = sorted(item.comments, key=lambda c: c.id)  # ascending id, as documented
            path = f"/repositories/{REPO_ID}/issues/{n}/comments"
            chunk, headers = self._page(record, rows, path, mixable=False)
            if chunk is None:
                return self._error(request, 422, "Validation Failed")
            return self._ok([self._comment(item, c) for c in chunk], headers)
        if endpoint == "reviews" and item is not None and item.pull:
            rows = sorted(item.reviews, key=lambda r: (r.at, r.id))  # chronological
            path = f"/repositories/{REPO_ID}/pulls/{n}/reviews"
            chunk, headers = self._page(record, rows, path, mixable=False)
            if chunk is None:
                return self._error(request, 422, "Validation Failed")
            return self._ok([self._review(item, r) for r in chunk], headers)
        if endpoint == "review comments" and item is not None:
            return self._ok([])
        return self._error(request, 404, "Not Found")

    def _ok(self, body, headers: dict | None = None, resource: str = "core") -> httpx.Response:
        rate = self._rate(**({"x-ratelimit-resource": resource} if resource != "core" else {}))
        return httpx.Response(200, json=body, headers={**rate, **(headers or {})})

    def _page(self, record, rows: list, path: str, mixable: bool = True, ceiling: int = 0):
        """(the page's rows, the Link header), or (None, None) for parameters it refuses."""
        params = record.params
        try:
            size, page = int(params.get("per_page", "30")), int(params.get("page", "1"))
        except ValueError:
            return None, None
        if size < 1 or page < 1:
            return None, None
        size = min(size, 100, self.page_cap or 100)  # past 100, fewer without a word
        rows = list(rows)
        if mixable and self.shuffle and len(rows) > 1:
            rows.sort(key=lambda row: hashlib.sha256(f"o{row}".encode()).hexdigest())
            if len(rows) > size:
                rows.insert(size, rows[0])  # the first item again, at the top of page 2
        if ceiling:
            rows = rows[:ceiling]
        start = (page - 1) * size
        if ceiling and start >= ceiling:
            return None, None
        chunk = rows[start : start + size]
        if mixable and self.shuffle:
            random.Random(page).shuffle(chunk)
        more = start + size < len(rows)
        last = max(1, -(-len(rows) // size))
        kept = {k: v for k, v in params.items() if k != "page"}
        origin = "https://elsewhere.example.test" if self.foreign_next else None

        def at(number: int, foreign: bool = False) -> str:
            base = self.api
            if foreign and origin:
                base = origin + urlsplit(self.api).path
            return f"{base}{path}?{urlencode({**kept, 'page': str(number)})}"

        links = []
        if more:
            links += [f'<{at(page + 1, True)}>; rel="next"', f'<{at(last)}>; rel="last"']
        if page > 1:
            links += [f'<{at(page - 1)}>; rel="prev"', f'<{at(1)}>; rel="first"']
        record.next = at(page + 1, True) if more else None
        record.count = len(chunk)
        return chunk, ({"link": ", ".join(links)} if links else {})

    def _issues(self, request, record) -> httpx.Response:
        p = record.params
        state, sort = p.get("state", "open"), p.get("sort", "created")
        direction, since = p.get("direction", "desc"), p.get("since")
        if state not in ("open", "closed", "all") or sort not in ("created", "updated"):
            return self._error(request, 422, "Validation Failed")
        if direction not in ("asc", "desc"):
            return self._error(request, 422, "Validation Failed")
        if since is not None and not SINCE_FORMAT.fullmatch(since):
            return self._error(request, 422, "Validation Failed")
        rows = [i for i in self.items.values() if state == "all" or i.state == state]
        if since is not None:
            bound = parsed(since)
            rows = [
                i for i in rows if (i.updated > bound if self.exclusive else i.updated >= bound)
            ]
        rows.sort(
            key=lambda i: (i.updated if sort == "updated" else i.created, i.number),
            reverse=direction == "desc",
        )
        path = f"/repositories/{REPO_ID}/issues"
        chunk, headers = self._page(record, [i.number for i in rows], path)
        if chunk is None:
            return self._error(request, 422, "Validation Failed")
        return self._ok([self._issue(self.items[n]) for n in chunk], headers)

    def _search(self, request, record) -> httpx.Response:
        if self.search_refused == 403:
            return self._error(request, 403, "Resource not accessible by personal access token")
        if self.search_refused == 422:
            return self._error(request, 422, "Validation Failed")
        terms = record.params.get("q", "").split()
        rows = list(self.items.values())
        seen_repo = False
        for term in terms:
            dated = re.fullmatch(r"(merged|closed|created|updated):(>=|<=|>|<)(.+)", term)
            if term.lower() == f"repo:{self.repo}".lower():
                seen_repo = True
            elif term in ("is:pr", "type:pr", "is:pull-request"):
                rows = [i for i in rows if i.pull]
            elif term in ("is:issue", "type:issue"):
                rows = [i for i in rows if not i.pull]
            elif term in ("is:closed", "state:closed"):
                rows = [i for i in rows if i.state == "closed"]
            elif term in ("is:open", "state:open"):
                rows = [i for i in rows if i.state == "open"]
            elif term == "is:merged":
                rows = [i for i in rows if i.merged is not None]
            elif term == "is:unmerged":
                rows = [i for i in rows if i.pull and i.merged is None]
            elif dated and search_moment(dated.group(3)) is not None:
                which, op, bound = dated.group(1), dated.group(2), search_moment(dated.group(3))
                if self.exclusive and op == ">=":
                    op = ">"  # CONFLICT: "merged after May 2014"
                compare = {
                    ">=": lambda a, b=bound: a >= b,
                    ">": lambda a, b=bound: a > b,
                    "<=": lambda a, b=bound: a <= b,
                    "<": lambda a, b=bound: a < b,
                }[op]
                stamp = {"merged": "merged", "closed": "closed", "created": "created"}
                rows = [
                    i
                    for i in rows
                    if (when := getattr(i, stamp.get(which, "updated"))) is not None
                    and compare(when)
                ]
            else:
                return self._error(request, 422, "Validation Failed")
        if not seen_repo:
            return self._error(request, 422, "Validation Failed")
        rows.sort(key=lambda i: i.number, reverse=True)
        found = [i.number for i in rows]
        given = [n for n in found if not (self.incomplete and n % 6 == 0)]
        chunk, headers = self._page(record, given, "/search/issues", ceiling=1000)
        if chunk is None:
            return self._error(request, 422, "Validation Failed")
        body = {
            "total_count": len(found),
            "incomplete_results": self.incomplete,
            "items": [{**self._issue(self.items[n]), "score": 1.0} for n in chunk],
        }
        return self._ok(body, headers, resource="search")

    # Shapes

    def _user(self, login: str | None) -> dict | None:
        if login is None and not self.ghost:
            return None
        login = login or "ghost"
        name, kind = PEOPLE.get(login, (None, "User"))
        number = int(hashlib.sha256(login.encode()).hexdigest()[:6], 16)
        return {
            "login": login,
            "id": 10137 if login == "ghost" else number,
            "node_id": f"U_c11{number}",
            "avatar_url": f"https://avatars.githubusercontent.com/u/{number}",
            "url": f"{self.api}/users/{login}",
            "html_url": f"{self.web}/{login}",
            "type": kind,
            "site_admin": False,
            "name": name,
            "email": None if login == "ghost" else f"{login.split('[')[0]}@example.test",
            "user_view_type": "public",
        }

    def _label(self, label) -> object:
        if isinstance(label, tuple):
            return label[1]
        number = int(hashlib.sha256(label.encode()).hexdigest()[:6], 16)
        return {
            "id": number,
            "node_id": f"L_c11{number}",
            "url": f"{self.api}/repos/{self.repo}/labels/{label}",
            "name": label,
            "description": None,
            "color": "d73a4a",
            "default": False,
        }

    def _issue(self, item: Item) -> dict:
        n = item.number
        url = f"{self.api}/repos/{self.repo}/issues/{n}"
        body = {
            "id": 880_000_000 + n,
            "node_id": f"I_c11{n}",
            "url": url,
            "repository_url": f"{self.api}/repos/{self.repo}",
            "labels_url": url + "/labels{/name}",
            "comments_url": url + "/comments",
            "events_url": url + "/events",
            "html_url": pr_ref(n, self.web, self.repo)
            if item.pull
            else issue_ref(n, self.web, self.repo),
            "number": n,
            "state": item.state,
            "state_reason": None if item.pull else item.reason,
            "title": item.title,
            "body": item.body,
            "user": self._user(item.author),
            "labels": [self._label(label) for label in item.labels],
            "assignee": None,
            "assignees": [],
            "milestone": None,
            "locked": False,
            "active_lock_reason": None,
            "comments": len(item.comments),
            "created_at": z(item.created),
            "updated_at": z(item.updated),
            "closed_at": z(item.closed) if item.closed else None,
            "closed_by": self._user(item.author) if item.closed else None,
            "author_association": "MEMBER",
        }
        if item.body_absent:
            del body["body"]
        if item.pull:
            pull = f"{self.api}/repos/{self.repo}/pulls/{n}"
            body["pull_request"] = {
                "url": pull,
                "html_url": pr_ref(n, self.web, self.repo),
                "diff_url": pr_ref(n, self.web, self.repo) + ".diff",
                "patch_url": pr_ref(n, self.web, self.repo) + ".patch",
                "merged_at": z(item.merged) if item.merged else None,
            }
            if item.merged is None and self.unmerged_absent:
                del body["pull_request"]["merged_at"]
        return body

    def _pull(self, item: Item) -> dict:
        n = item.number
        return {
            "id": 550_000_000 + n,
            "node_id": f"PR_c11{n}",
            "url": f"{self.api}/repos/{self.repo}/pulls/{n}",
            "html_url": pr_ref(n, self.web, self.repo),
            "number": n,
            "state": item.state,
            "locked": False,
            "title": item.title,
            "body": item.body,
            "user": self._user(item.author),
            "labels": [self._label(label) for label in item.labels if isinstance(label, str)],
            "milestone": None,
            "created_at": z(item.created),
            "updated_at": z(item.updated),
            "closed_at": z(item.closed) if item.closed else None,
            "merged_at": z(item.merged) if item.merged else None,
            "merge_commit_sha": "c11" + "0" * 37,
            "draft": False,
            "head": {"ref": f"change-{n}", "label": f"{self.owner}:change-{n}", "sha": "1" * 40},
            "base": {"ref": item.base, "label": f"{self.owner}:{item.base}", "sha": "2" * 40},
            "author_association": "MEMBER",
            "auto_merge": None,
            "merged": item.merged is not None,
            "mergeable": None,
            "merged_by": self._user(item.merged_by) if item.merged else None,
            "comments": len(item.comments),
            "review_comments": 0,
            "maintainer_can_modify": False,
            "commits": 1,
            "additions": 12,
            "deletions": 3,
            "changed_files": 2,
        }

    def _comment(self, item: Item, comment: Comment) -> dict:
        return {
            "id": comment.id,
            "node_id": f"IC_c11{comment.id}",
            "url": f"{self.api}/repos/{self.repo}/issues/comments/{comment.id}",
            "html_url": f"{self._html(item)}#issuecomment-{comment.id}",
            "issue_url": f"{self.api}/repos/{self.repo}/issues/{item.number}",
            "body": comment.body,
            "user": self._user(comment.author),
            "created_at": z(comment.at),
            "updated_at": z(comment.at),
            "author_association": "MEMBER",
        }

    def _review(self, item: Item, review: Review) -> dict:
        return {
            "id": review.id,
            "node_id": f"PRR_c11{review.id}",
            "user": self._user(review.author),
            "body": review.body,
            "state": review.state,
            "html_url": f"{self._html(item)}#pullrequestreview-{review.id}",
            "pull_request_url": f"{self.api}/repos/{self.repo}/pulls/{item.number}",
            "commit_id": "3" * 40,
            "submitted_at": z(review.at),
            "author_association": "MEMBER",
            "_links": {"html": {"href": f"{self._html(item)}#pullrequestreview-{review.id}"}},
        }

    def _release(self, release: Release) -> dict:
        url = f"{self.api}/repos/{self.repo}/releases/{release.id}"
        return {
            "url": url,
            "html_url": release_ref(release.tag, self.web, self.repo),
            "assets_url": url + "/assets",
            "upload_url": f"https://uploads.github.com/repos/{self.repo}/releases/{release.id}/assets",
            "tarball_url": f"{self.api}/repos/{self.repo}/tarball/{release.tag}",
            "zipball_url": f"{self.api}/repos/{self.repo}/zipball/{release.tag}",
            "id": release.id,
            "node_id": f"RE_c11{release.id}",
            "tag_name": release.tag,
            "target_commitish": "main",
            "name": release.name,
            "body": release.body,
            "draft": release.draft,
            "prerelease": release.prerelease,
            "immutable": False,
            "created_at": z(release.created),
            "published_at": z(release.published) if release.published else None,
            "updated_at": z(release.published or release.created),
            "author": self._user(release.author),
            "assets": [],
        }

    def _html(self, item: Item) -> str:
        if item.pull:
            return pr_ref(item.number, self.web, self.repo)
        return issue_ref(item.number, self.web, self.repo)

    # Changing a release

    def unpublish(self, release: Release) -> None:
        release.draft, release.published = True, None

    def publish(self, release: Release, at: datetime) -> None:
        release.draft, release.published = False, at


@pytest.fixture
def kind():
    return KINDS["github"]


@pytest.fixture
def stand_ins():
    made: list[FakeGitHub] = []

    def make(**given) -> FakeGitHub:
        fake = FakeGitHub(Clock(NOW), **given)
        made.append(fake)
        return fake

    yield make
    for fake in made:  # every request of every test: the headers, the host, the addresses
        assert fake.problems() == []


@pytest.fixture
def fake(stand_ins):
    return stand_ins()


def config(**settings) -> dict:
    return {"token_env": TOKEN_ENV, "repo": REPO, "projects": ["quill"], **settings}


def opened(kind, fake, *, since=SINCE, deadline=None, env=ENV, **settings):
    settings.setdefault("repo", fake.repo)
    if fake.api != API:
        settings.setdefault("base_url", fake.api)
    checked = kind.check(config(**settings))
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return kind.open(
        "github", checked, http=http, env=env, clock=fake.clock, sleep=fake.sleep,
        deadline=deadline, since=since,
    )  # fmt: skip


def pull(fake, source, cursor=None):
    fake.new_pull()
    return asyncio.run(source.pull(cursor))


def reader(kind, fake, **settings):
    """Each pull through a source opened afresh, as the runner opens one for every run."""

    def read(cursor=None):
        return pull(fake, opened(kind, fake, **settings), cursor)

    return read


def searches(fake, gen: int | None = None) -> dict[frozenset, list[datetime]]:
    """The searches of a pull: each query's qualifiers without the date, and the date's bound
    as the stand-in read it, once per page asked."""
    found: dict[frozenset, list[datetime]] = {}
    for request in fake.asked("search", gen):
        terms = request.params["q"].split()
        dated = [t for t in terms if re.match(r"(merged|closed):>=", t)]
        assert len(dated) == 1, terms
        bound = search_moment(dated[0].split(">=", 1)[1])
        assert bound is not None, dated
        key = frozenset([t.lower() for t in terms if t not in dated] + [dated[0].split(":")[0]])
        found.setdefault(key, []).append(bound)
    return found


PR_QUERY = frozenset([f"repo:{REPO}".lower(), "is:pr", "is:merged", "merged"])
ISSUE_QUERY = frozenset([f"repo:{REPO}".lower(), "is:issue", "is:closed", "closed"])


def window(since: datetime, previous: datetime | None) -> datetime:
    """How far a check must reach back: to the last check whose finds were all written
    (`previous`) less a day, or to `since` for the first; however long ago that is. (Never
    asked to reach before `since`: a window from earlier is allowed by the margin.)"""
    return since if previous is None else max(previous - DAY, since)


def assert_windows(found: dict, expected: datetime) -> None:
    """Every bound of a pull's searches reaches back to `expected`, and not more than a day
    further."""
    for bounds in found.values():
        for bound in bounds:
            assert expected - DAY <= bound <= expected, (bound, expected)


# The kind and its config


def test_the_kind_starts_off_writes_into_the_github_via_and_is_not_withheld(kind):
    assert kind.starts_off is True and kind.via == "github"
    assert kind.withheld is True and "github" in withheld_via()  # Privacy 4: every kind
    assert kind.describe is not None
    said = kind.describe(kind.check(config()))
    assert REPO in said and TOKEN not in said and TOKEN_ENV not in said
    assert kind.switching_on()


@pytest.mark.parametrize("repo", [REPO, "Quill-Works/Ledger.js"])
def test_source_add_proposes_the_project_github_whatever_the_repository(tmp_path, capsys, repo):
    db = tmp_path / "cairn.db"
    Store(db).close()
    settings = json.dumps({"token_env": TOKEN_ENV, "repo": repo, "projects": []})
    argv = ["source-add", "--db", str(db), "--name", "code", "--kind", "github"]
    assert main([*argv, "--config", settings]) == 0
    said = capsys.readouterr()
    assert said.out.strip().endswith("source-enable --name code --project github")
    assert TOKEN not in said.out + said.err


def test_check_fills_in_the_defaults_and_keeps_what_is_given(kind):
    checked = kind.check({"token_env": TOKEN_ENV, "repo": REPO, "projects": []})
    assert (checked["token_env"], checked["repo"], checked["projects"]) == (TOKEN_ENV, REPO, [])
    assert sorted(checked["items"]) == ["issues", "pulls", "releases"]
    assert checked["comments"] is True
    assert (checked["settle_minutes"], checked["lookback_days"]) == (30, 30)
    assert checked["base_url"] == API
    assert kind.check(checked) == checked  # what is stored is checked again on switch-on

    given = config(
        items=["releases", "pulls"], comments=False, settle_minutes=0, lookback_days=365,
        base_url=GHE_API,
    )  # fmt: skip
    kept = kind.check(given)
    for setting, value in given.items():
        if setting == "items":
            assert sorted(kept[setting]) == sorted(value)
        else:
            assert kept[setting] == value, setting
    assert kind.check(kept) == kept
    for accepted in (
        {"repo": "Quill-Works/quill.js"},
        {"repo": "q/q"},
        {"repo": "quillworks/ledger_v2-core"},
        {"items": ["issues"]},
        {"settle_minutes": 1440, "lookback_days": 1},
        {"base_url": "http://127.0.0.1:8123/api/v3"},
        {"base_url": "http://localhost:8123"},
    ):
        kept = kind.check(config(**accepted))
        assert kept["projects"] == ["quill"], accepted
        if "repo" in accepted:
            assert kept["repo"].lower() == accepted["repo"].lower()


@pytest.mark.parametrize(
    ("setting", "settings"),
    [
        ("token_env", {"token_env": None}),
        ("token_env", {"token_env": 7}),
        ("token_env", {"token_env": ""}),
        ("repo", {"repo": None}),
        ("repo", {"repo": 7}),
        ("repo", {"repo": "quillworks"}),
        ("repo", {"repo": "https://github.com/quillworks/quill"}),
        ("repo", {"repo": "github.com/quillworks/quill"}),
        ("repo", {"repo": "quillworks/quill.git"}),
        ("repo", {"repo": "quillworks/quill/issues"}),
        ("repo", {"repo": "quill works/quill"}),
        ("repo", {"repo": "-quillworks/quill"}),
        ("repo", {"repo": "quillworks/"}),
        ("repo", {"repo": "q" * 40 + "/quill"}),
        ("repo", {"repo": "quillworks/" + "q" * 101}),
        ("projects", {"projects": ["quill", "other"]}),
        ("projects", {"projects": "quill"}),
        ("items", {"items": []}),
        ("items", {"items": ["pulls", "commits"]}),
        ("items", {"items": "pulls"}),
        ("comments", {"comments": "yes"}),
        ("settle_minutes", {"settle_minutes": -1}),
        ("settle_minutes", {"settle_minutes": 1441}),
        ("settle_minutes", {"settle_minutes": "30"}),
        ("settle_minutes", {"settle_minutes": True}),
        ("lookback_days", {"lookback_days": 0}),
        ("lookback_days", {"lookback_days": 366}),
        ("lookback_days", {"lookback_days": 1.5}),
        ("base_url", {"base_url": "http://ghe.example.test/api/v3"}),
        ("base_url", {"base_url": "ftp://ghe.example.test/api/v3"}),
        ("base_url", {"base_url": "https://ada:hunter2-c11@ghe.example.test/api/v3"}),
        ("base_url", {"base_url": "not an address"}),
        ("color", {"color": "blue"}),
    ],
)
def test_check_refuses_a_bad_setting_by_its_name(kind, setting, settings):
    given = config(**settings)
    for name, value in settings.items():
        if value is None:
            del given[name]
    with pytest.raises(ValueError) as refused:
        kind.check(given)
    assert setting in str(refused.value)
    assert "hunter2-c11" not in str(refused.value)


def test_a_config_that_is_not_an_object_is_refused(kind):
    for odd in (None, [], "token_env", 7):
        with pytest.raises(ValueError):
            kind.check(odd)


def test_a_token_in_the_config_is_refused_and_never_repeated(kind, tmp_path):
    tail = "c11MadeUp" + "A1b2C3d4E5f6G7h8I9j0K1l2M3"
    tokens = [prefix + tail for prefix in ("gh" + "p_", "github" + "_pat_", "gh" + "o_")]
    tokens += [prefix + tail for prefix in ("gh" + "u_", "gh" + "r_")] + [TOKEN]
    for token in tokens:
        for setting, leaked in (
            ("token_env", config(token_env=token)),
            ("base_url", config(base_url=f"https://ghe.example.test/{token}/api/v3")),
            ("base_url", config(base_url=f"https://{token}@ghe.example.test/api/v3")),
            ("base_url", config(base_url=f"https://ghe.example.test/api/v3?access_token={token}")),
        ):
            with pytest.raises(ValueError) as refused:
                kind.check(leaked)
            said = str(refused.value)
            assert setting in said and token not in said, (setting, token[:4])
        with pytest.raises(ValueError) as refused:
            kind.check(config(api_token=token))
        assert token not in str(refused.value)

    store = Store(tmp_path / "cairn.db")
    try:
        store.create_project("quill")
        with pytest.raises(ValueError):
            store.add_source("github", "github", config(token_env=TOKEN))
        assert store.list_sources() == []
    finally:
        store.close()
    assert TOKEN not in dump(tmp_path / "cairn.db")


def test_the_token_comes_from_the_environment_and_goes_only_to_base_url_as_a_bearer(kind, fake):
    for env in ({}, {TOKEN_ENV: ""}, {"ANOTHER_VARIABLE": TOKEN}):
        with pytest.raises(SourceError) as missing:
            opened(kind, fake, env=env)
        assert TOKEN_ENV in str(missing.value) and TOKEN not in str(missing.value)

    pr = fake.pull_request(1, merged=SINCE + DAY)
    fake.comment(pr, SINCE + HOUR, "cy-moss", "Looks fine.")
    fake.review(pr, SINCE + 2 * HOUR, "dee-quinn", "APPROVED", "Ship it.")
    fake.issue(2, closed=SINCE + DAY + HOUR)
    fake.release(1, "v1.0.0", published=SINCE + 2 * DAY)
    source = opened(kind, fake)
    assert TOKEN in source.secrets
    got = pull(fake, source)
    assert refs(got) == [pr_ref(1), issue_ref(2), release_ref("v1.0.0")]
    asked = {r.endpoint for r in fake.requests}
    assert {"issues", "pull", "comments", "reviews", "releases"} <= asked
    for request in fake.requests:
        assert request.auth == f"Bearer {TOKEN}"
        assert (request.accept, request.version) == (ACCEPT, API_VERSION)
        assert request.agent and not request.agent.lower().startswith("python-httpx")
        assert TOKEN not in request.url and TOKEN.lower() not in request.url.lower()
        assert request.url.startswith(API + "/")


def test_an_enterprise_server_base_url_is_used_for_every_request(kind, stand_ins):
    fake = stand_ins(api=GHE_API, web=GHE_WEB)
    fake.page_cap = 2
    for n in range(1, 4):
        fake.pull_request(n, merged=SINCE + n * HOUR)
    fake.issue(4, closed=SINCE + 5 * HOUR)
    fake.release(1, "v1.0.0", published=SINCE + 6 * HOUR)
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [pr_ref(n, GHE_WEB) for n in (1, 2, 3)] + [
        issue_ref(4, GHE_WEB),
        release_ref("v1.0.0", GHE_WEB),
    ]
    assert len(fake.asked("issues")) == 2  # every page, at the Enterprise Server
    assert all(r.url.startswith(GHE_API + "/") for r in fake.requests)


def test_a_next_page_on_another_host_is_never_asked(kind, fake):
    fake.foreign_next = True
    fake.page_cap = 2
    for n in range(1, 6):
        fake.issue(n, closed=SINCE + n * HOUR)
    try:
        pull(fake, opened(kind, fake))
    except SourceError as error:
        assert TOKEN not in str(error)
    assert fake.asked("issues")
    assert [r.url for r in fake.requests if r.host != "api.github.com"] == []


# What it reads


@pytest.mark.parametrize("moves", [True, False], ids=["close-moves-updated", "close-keeps-updated"])
@pytest.mark.parametrize("exclusive", [False, True], ids=["inclusive", "exclusive"])
def test_only_what_finished_since_it_was_switched_on_in_the_connectors_order(
    kind, fake, moves, exclusive
):
    fake.moves, fake.exclusive, fake.unmerged_absent = moves, exclusive, exclusive
    fake.pull_request(1, merged=SINCE + DAY)
    fake.pull_request(2, "Closed without merging", closed=SINCE + DAY)
    fake.pull_request(3, "Still open")
    fake.pull_request(4, "Merged before it was on", merged=SINCE - HOUR)
    fake.pull_request(5, "Merged as it was switched on", merged=SINCE)
    fake.issue(6, closed=SINCE + 2 * DAY, reason="completed")
    fake.issue(7, closed=SINCE + 2 * DAY + HOUR, reason="not_planned")
    fake.issue(8, "A duplicate", closed=SINCE + 2 * DAY, reason="duplicate")
    fake.issue(9, "Still open")
    fake.issue(10, "Closed before close reasons", closed=SINCE + 3 * DAY, reason=None)
    fake.issue(11, "Closed before it was on", closed=SINCE - DAY)
    tie = SINCE + 4 * DAY
    fake.release(3, "v1.1.0", "Tied", published=tie)
    fake.issue(13, "Tied issue", closed=tie)
    fake.pull_request(14, "Tied, second by number", merged=tie)
    fake.pull_request(12, "Tied, first by number", merged=tie)
    fake.release(1, "v1.0.0", published=SINCE + DAY)
    fake.release(2, "v1.2.0-draft")  # a draft: never published
    fake.release(4, "v0.9.0", published=SINCE - DAY)
    fake.release(5, "v1.2.0-rc.1", "Release candidate", published=SINCE + 5 * DAY, prerelease=True)

    read = reader(kind, fake)
    cursor, written = None, []
    for hour in range(27):  # the daily check comes within the first day, wherever it starts
        fake.clock.now = NOW + hour * HOUR
        got = read(cursor)
        cursor = got.cursor
        written += refs(got)
    assert written == [
        pr_ref(5),
        pr_ref(1),
        release_ref("v1.0.0"),
        issue_ref(6),
        issue_ref(7),
        issue_ref(10),
        pr_ref(12),
        pr_ref(14),
        issue_ref(13),
        release_ref("v1.1.0"),
        release_ref("v1.2.0-rc.1"),
    ]
    details = Counter(r.number for r in fake.requests if r.endpoint == "pull")
    assert details == Counter({1: 1, 5: 1, 12: 1, 14: 1})  # merged ones only, each once


def test_the_listing_asks_for_closed_items_by_update_time_and_follows_every_page(kind, fake):
    fake.page_cap = 3
    for n in range(1, 9):
        fake.issue(n, closed=SINCE + n * HOUR)
    for k in range(1, 6):
        fake.release(k, f"v1.{k}.0", published=SINCE + (10 + k) * HOUR)
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [issue_ref(n) for n in range(1, 9)] + [
        release_ref(f"v1.{k}.0") for k in range(1, 6)
    ]
    for endpoint, pages, first in (
        (
            "issues",
            3,
            {"state": "closed", "sort": "updated", "direction": "asc", "per_page": "100"},
        ),
        ("releases", 2, {"per_page": "100"}),
    ):
        asked = fake.asked(endpoint)
        assert len(asked) == pages, endpoint
        opening = dict(asked[0].params)
        since = opening.pop("since", None)
        assert opening == first, endpoint
        if endpoint == "issues":
            assert since is not None and SINCE_FORMAT.fullmatch(since)
            assert SINCE - DAY <= parsed(since) <= SINCE  # the first pull: since, overlapped
        for earlier, later in itertools.pairwise(asked):  # each next page as its Link gave it
            given = urlsplit(earlier.next)
            assert later.path == given.path and later.host == given.hostname
            assert later.params == dict(httpx.URL(earlier.next).params)
        assert asked[-1].next is None


@pytest.mark.parametrize("exclusive", [False, True], ids=["inclusive", "exclusive"])
def test_what_a_close_does_not_bring_to_the_listing_the_daily_check_finds(kind, fake, exclusive):
    fake.moves, fake.exclusive = False, exclusive
    merged = fake.pull_request(41, created=SINCE - 20 * DAY)
    closed = fake.issue(42, created=SINCE - 15 * DAY)
    early = fake.issue(43, created=SINCE - 15 * DAY)
    fake.close(early, SINCE + 2 * HOUR)  # early in the UTC day it was switched on
    read = reader(kind, fake)
    cursor, written, checks = None, {}, []
    for hour in range(60):
        fake.clock.now = NOW + hour * HOUR
        if hour == 2:
            fake.merge(merged, fake.clock.now)
        if hour == 5:
            fake.close(closed, fake.clock.now, "not_planned")
        got = read(cursor)
        cursor = got.cursor
        found = searches(fake)
        if found:
            assert set(found) == {PR_QUERY, ISSUE_QUERY}, found
            previous = NOW + checks[-1] * HOUR if checks else None  # each check's finds written
            assert_windows(found, window(SINCE, previous))
            checks.append(hour)
        for ref in refs(got):
            written.setdefault(ref, []).append(hour)
    assert set(written) == {issue_ref(43), pr_ref(41), issue_ref(42)}
    assert all(len(hours) == 1 for hours in written.values()), written
    assert written[issue_ref(43)][0] <= 25
    assert 3 <= written[pr_ref(41)][0] <= 2 + 26
    assert 6 <= written[issue_ref(42)][0] <= 5 + 26
    assert len(checks) >= 2 and all(b - a >= 24 for a, b in itertools.pairwise(checks))


@pytest.mark.parametrize(
    ("since", "lookback_days"),
    [
        (NOW - 10 * DAY, 30),
        (NOW - 100 * DAY, 30),  # the first check reaches back past lookback_days, to since
    ],
)
def test_the_daily_check_reaches_back_to_the_last_one_or_to_since(kind, fake, since, lookback_days):
    read = reader(kind, fake, since=since, lookback_days=lookback_days)
    cursor, checks = None, []
    for hour in range(60):
        fake.clock.now = NOW + hour * HOUR
        cursor = read(cursor).cursor
        found = searches(fake)
        if found:
            assert set(found) == {PR_QUERY, ISSUE_QUERY}, found
            previous = checks[-1] if checks else None  # nothing found: each check finished
            assert_windows(found, window(since, previous))
            checks.append(fake.clock.now)
    assert len(checks) >= 2
    assert all(later - earlier >= DAY for earlier, later in itertools.pairwise(checks))


@pytest.mark.parametrize("refusal", [403, 422])
def test_a_refused_search_does_not_fail_the_run_and_the_report_says_so(world, fake, refusal):
    start(world)
    fake.pull_request(1, merged=SINCE + DAY)  # the listing finds it
    fake.moves = False
    hidden = fake.issue(2, created=SINCE - 20 * DAY)
    fake.close(hidden, SINCE + DAY)  # only a search finds it
    fake.search_refused = refusal
    report = run(world, fake, NOW)
    assert report.saved == 1
    assert [c.source_ref for c in notes(world)] == [pr_ref(1)]
    said = repr(report).lower()
    assert "search" in said or "daily check" in said
    assert fake.asked("search")

    fake.search_refused = None
    later = run(world, fake, NOW + DAY + HOUR)
    assert later.saved == 1
    assert sorted(c.source_ref for c in notes(world)) == [issue_ref(2), pr_ref(1)]


def test_shuffled_pages_with_an_item_repeated_give_the_same_drafts(kind, stand_ins):
    def stocked(shuffle: bool) -> FakeGitHub:
        made = stand_ins()
        made.page_cap, made.shuffle = 4, shuffle
        for n in range(1, 11):
            if n % 2:
                made.pull_request(n, f"Change {n}", merged=SINCE + n * 3 * HOUR)
            else:
                made.issue(n, f"Report {n}", closed=SINCE + n * 3 * HOUR)
        for k in range(1, 7):
            made.release(k, f"v2.{k}.0", f"Batch {k}", published=SINCE + k * 5 * HOUR)
        return made

    seen = {}
    for shuffle in (False, True):
        made = stocked(shuffle)
        got = pull(made, opened(kind, made))
        assert len(refs(got)) == len(set(refs(got))) == 16, shuffle
        seen[shuffle] = [(d.source_ref, d.title, d.topic, d.text, d.project) for d in got.drafts]
        details = Counter(r.number for r in made.requests if r.endpoint == "pull")
        assert details == Counter({n: 1 for n in range(1, 11, 2)})
        if shuffle:
            assert len(made.asked("issues")) >= 3 and len(made.asked("releases")) >= 2
    assert seen[False] == seen[True]


@pytest.mark.parametrize("moves", [True, False], ids=["close-moves-updated", "close-keeps-updated"])
def test_sixty_finished_items_take_three_pulls_of_at_most_25_in_finish_order(kind, fake, moves):
    fake.moves, fake.page_cap = moves, 16
    expected = []
    for i in range(60):
        at = SINCE + (i + 1) * 2 * HOUR
        if i % 6 in (0, 3, 5):
            fake.pull_request(100 + i, f"Change {i}", merged=at)
            expected.append(pr_ref(100 + i))
        elif i % 6 in (1, 4):
            fake.issue(100 + i, f"Report {i}", closed=at)
            expected.append(issue_ref(100 + i))
        else:
            fake.release(i, f"v3.{i}.0", f"Batch {i}", published=at)
            expected.append(release_ref(f"v3.{i}.0"))
    read = reader(kind, fake, comments=False)
    cursor, sizes, written, windows_asked = None, [], [], []
    for k in range(5):
        fake.clock.now = NOW + k * 10 * MINUTE
        got = read(cursor)
        cursor = got.cursor
        sizes.append(len(got.drafts))
        written += refs(got)
        windows_asked.append({query: sorted(set(b)) for query, b in searches(fake).items()})
    assert sizes == [25, 25, 10, 0, 0]
    assert written == expected
    # The check found forty: cut by the cap, it is made again with the same T until all its
    # finds are written; then it is not made again within the day.
    assert set(windows_asked[0]) == {PR_QUERY, ISSUE_QUERY}
    assert windows_asked[0] == windows_asked[1] == windows_asked[2]
    assert windows_asked[3] == windows_asked[4] == {}


@pytest.mark.parametrize("how", ["ceiling", "incomplete"])
def test_a_check_past_what_search_returns_writes_what_it_was_given_and_says_so(kind, fake, how):
    fake.moves = False  # only the check finds them
    count = 1_030 if how == "ceiling" else 60
    for n in range(1, count + 1):
        made = fake.issue(n, f"Report {n}", created=SINCE - 5 * DAY)
        fake.close(made, SINCE + n * MINUTE)
    fake.incomplete = how == "incomplete"
    given = set(range(31, count + 1)) if how == "ceiling" else {n for n in range(1, 61) if n % 6}
    read = reader(kind, fake, items=["issues"], comments=False)
    cursor, written, reasons, asked = None, [], [], []
    for k in range(len(given) // 25 + 4):  # every ten minutes: the check is not due again
        fake.clock.now = NOW + k * 10 * MINUTE
        got = read(cursor)
        cursor = got.cursor
        written += refs(got)
        reasons += [skip.reason for skip in got.skipped]
        asked.append({query: sorted(set(b)) for query, b in searches(fake).items()})
    assert written == [issue_ref(n) for n in sorted(given)]  # what search gave, once, in order
    assert any("search" in reason.lower() for reason in reasons), reasons  # it says so
    made = [windows for windows in asked if windows]
    assert len(made) == -(-len(given) // 25)  # made again only until its finds were written
    assert all(windows == made[0] for windows in made)  # with the same T, never split
    assert asked[-1] == {}  # then done: not made again within the day

    last = max(k for k, windows in enumerate(asked) if windows)
    fake.clock.now = NOW + last * 10 * MINUTE + DAY + HOUR
    got = read(cursor)
    later = searches(fake)
    assert later and got.drafts == []  # the next check reaches back to the last one only
    assert all(bound > made[0][ISSUE_QUERY][0] for bound in later[ISSUE_QUERY])


SUBSETS = [
    ["pulls"],
    ["issues"],
    ["releases"],
    ["pulls", "issues"],
    ["pulls", "releases"],
    ["issues", "releases"],
    ["pulls", "issues", "releases"],
]


@pytest.mark.parametrize("items", SUBSETS, ids=["+".join(s) for s in SUBSETS])
def test_items_decides_what_is_asked_for_and_what_is_written(kind, fake, items):
    pr = fake.pull_request(1, merged=SINCE + DAY)
    fake.comment(pr, SINCE + HOUR, "cy-moss", "On the pull request.")
    fake.review(pr, SINCE + 2 * HOUR, "dee-quinn", "APPROVED", "Fine by me.")
    issue = fake.issue(2, closed=SINCE + DAY + HOUR)
    fake.comment(issue, SINCE + HOUR, "cy-moss", "On the issue.")
    fake.release(1, "v1.0.0", published=SINCE + 2 * DAY)
    read = reader(kind, fake, items=items)
    cursor, written = None, []
    for hour in range(27):
        fake.clock.now = NOW + hour * HOUR
        got = read(cursor)
        cursor = got.cursor
        written += refs(got)
    wanted = {"pulls": pr_ref(1), "issues": issue_ref(2), "releases": release_ref("v1.0.0")}
    assert written == [wanted[k] for k in ("pulls", "issues", "releases") if k in items]

    asked = Counter(r.endpoint for r in fake.requests)
    assert ("issues" in asked) == ("pulls" in items or "issues" in items)
    assert ("releases" in asked) == ("releases" in items)
    assert ("pull" in asked) == ("pulls" in items)
    if "pulls" not in items:
        assert "reviews" not in asked
    numbers = {1} if "pulls" in items else set()
    numbers |= {2} if "issues" in items else set()
    assert {r.number for r in fake.requests if r.endpoint == "comments"} == numbers
    queries = set()
    for gen in range(1, fake.gen + 1):
        queries |= set(searches(fake, gen))
    expected = {PR_QUERY} if "pulls" in items else set()
    expected |= {ISSUE_QUERY} if "issues" in items else set()
    assert queries == expected


def test_an_item_is_read_only_once_it_has_settled(kind, fake):
    fake.pull_request(4, "Merged two hours ago", merged=NOW - 2 * HOUR)
    pr = fake.pull_request(1, merged=NOW - 10 * MINUTE)
    fake.issue(2, closed=NOW - 20 * MINUTE)
    fake.release(1, "v1.0.0", published=NOW - 5 * MINUTE)
    read = reader(kind, fake)
    cursor, written = None, {}
    for minutes in (0, 5, 12, 22, 27, 40, 90):
        fake.clock.now = NOW + minutes * MINUTE
        got = read(cursor)
        cursor = got.cursor
        for ref in refs(got):
            written.setdefault(ref, []).append(minutes)
        if pr_ref(1) not in written:  # until it is written, every listing still reaches it
            for request in fake.asked("issues"):
                assert parsed(request.params["since"]) <= pr.updated
    assert written == {
        pr_ref(4): [0],
        issue_ref(2): [12],
        pr_ref(1): [22],
        release_ref("v1.0.0"): [27],
    }

    fresh = FakeGitHub(Clock(NOW))
    fresh.pull_request(1, merged=NOW - MINUTE)
    got = pull(fresh, opened(kind, fresh, settle_minutes=0))
    assert refs(got) == [pr_ref(1)]
    assert fresh.problems() == []


@pytest.mark.parametrize("moves", [True, False], ids=["close-moves-updated", "close-keeps-updated"])
def test_an_item_is_written_once_whatever_happens_to_it_afterwards(kind, fake, moves):
    fake.moves = moves
    pr = fake.pull_request(1, merged=NOW - 2 * DAY)
    issue = fake.issue(2, closed=NOW - DAY)
    release = fake.release(1, "v1.0.0", published=NOW - DAY)
    read = reader(kind, fake)
    written = Counter()
    got = read()
    cursor = got.cursor
    written.update(refs(got))
    for hour in range(1, 30):  # whenever the first daily check comes
        fake.clock.now = NOW + hour * HOUR
        got = read(cursor)
        cursor = got.cursor
        written.update(refs(got))
    assert written == Counter({pr_ref(1): 1, issue_ref(2): 1, release_ref("v1.0.0"): 1})

    later = fake.clock.now
    fake.edit(pr, later + HOUR, "Edited after it was written.")
    fake.comment(pr, later + HOUR, "cy-moss", "A comment after it was written.")
    fake.reopen(issue, later + 2 * HOUR)
    fake.close(issue, later + 3 * HOUR, "not_planned")
    fake.unpublish(release)
    fake.publish(release, later + 3 * HOUR)
    for step in range(1, 41):  # ten days, well inside lookback_days
        fake.clock.now = later + step * 6 * HOUR
        got = read(cursor)
        cursor = got.cursor
        assert got.drafts == [], step
    fake.issue(3, closed=fake.clock.now - HOUR)
    fake.clock.now += 30 * HOUR
    got = read(cursor)
    for _ in range(30):
        cursor = got.cursor
        written.update(refs(got))
        fake.clock.now += HOUR
        got = read(cursor)
    written.update(refs(got))
    assert written == Counter(
        {pr_ref(1): 1, issue_ref(2): 1, release_ref("v1.0.0"): 1, issue_ref(3): 1}
    )


@pytest.mark.parametrize("moves", [True, False], ids=["close-moves-updated", "close-keeps-updated"])
def test_an_issue_reopened_before_it_was_read_is_written_once_closed_again(kind, fake, moves):
    fake.moves = moves
    issue = fake.issue(7, created=NOW - 5 * DAY)
    read = reader(kind, fake)
    cursor, written = None, []
    for step in range(0, 60 * 6):  # every ten minutes for sixty hours
        fake.clock.now = NOW + step * 10 * MINUTE
        if step == 1:
            fake.close(issue, fake.clock.now)
        if step == 2:
            fake.reopen(issue, fake.clock.now)  # twenty minutes later, before it settled
        if step == 18:
            fake.close(issue, fake.clock.now, "not_planned")  # three hours on
        got = read(cursor)
        cursor = got.cursor
        written += [(step, draft) for draft in got.drafts]
    assert len(written) == 1
    step, draft = written[0]
    assert step >= 18 + 3  # settled after the second close
    if moves:
        assert step == 18 + 3
    assert "not planned" in fact(draft.text, "Closed as")


# What it writes


def notes_world(fake) -> None:
    """A month and a bit of a repository, for the tests of what a note says."""
    pr = fake.pull_request(
        12,
        "Back off between webhook retries",
        created=datetime(2099, 3, 2, 9, 0, tzinfo=UTC),
        author="ada-park",
        base="release/2099",
        labels=["payments", "retry-queue"],
        body=PR_BODY,
    )
    fake.comment(pr, datetime(2099, 3, 2, 10, 0, tzinfo=UTC), "cy-moss", "Does this cover refunds?")
    fake.comment(pr, datetime(2099, 3, 3, 8, 15, tzinfo=UTC), "ada-park", "Yes, the same queue.")
    fake.review(pr, datetime(2099, 3, 3, 9, 0, tzinfo=UTC), "brook-lane", "APPROVED", "Right.")
    fake.review(
        pr,
        datetime(2099, 3, 3, 11, 0, tzinfo=UTC),
        "cy-moss",
        "CHANGES_REQUESTED",
        "Please split the migration.",
    )
    fake.review(pr, datetime(2099, 3, 4, 9, 0, tzinfo=UTC), "dee-quinn", "APPROVED", "")
    fake.merge(pr, datetime(2099, 3, 4, 16, 30, tzinfo=UTC), "brook-lane")
    fake.pull_request(13, "Count retries", merged=datetime(2099, 2, 27, 8, 0, tzinfo=UTC))
    fake.issue(
        20,
        "Refunds stall after the third retry",
        created=datetime(2099, 2, 25, 9, 0, tzinfo=UTC),
        labels=["bug", ("text", "refunds")],
        body=None,
        author="dee-quinn",
    )
    fake.close(fake.items[20], datetime(2099, 3, 5, 10, 0, tzinfo=UTC), "completed")
    fake.issue(
        21, "Retry in the browser", closed=datetime(2099, 3, 5, 11, 0, tzinfo=UTC),
        reason="not_planned", body_absent=True,
    )  # fmt: skip
    fake.issue(22, "Old style close", closed=datetime(2099, 3, 5, 12, 0, tzinfo=UTC), reason=None)
    fake.release(
        1, "v2.1.0", "Faster retries", published=datetime(2099, 3, 6, 9, 0, tzinfo=UTC),
        body=RELEASE_BODY,
    )  # fmt: skip
    fake.release(
        2, "v2.2.0-rc.1", published=datetime(2099, 3, 7, 9, 0, tzinfo=UTC), prerelease=True,
        author="quill-ci[bot]", body="",
    )  # fmt: skip


PR_BODY = (
    "Retries now back off.\n\n- First try after 1 s\n- Then 2 s, then 4 s\n\n"
    "```python\nwait = 2 ** attempt\n```\n"
)
RELEASE_BODY = "## What changed\n\n- Retries back off (#12).\n"
NOTES_SINCE = datetime(2099, 2, 20, tzinfo=UTC)


def written_notes(kind, fake, **settings) -> dict:
    got = pull(fake, opened(kind, fake, since=NOTES_SINCE, **settings))
    by_ref = {draft.source_ref: draft for draft in got.drafts}
    assert len(by_ref) == len(got.drafts)
    for draft in got.drafts:
        assert draft.topic is None  # a page an item, named by its title
        for value in (draft.title, draft.text):
            assert "@example.test" not in value and "example.test" not in value
    return by_ref


def heading(text: str) -> str:
    first = next(line for line in text.splitlines() if line.strip())
    assert re.match(r"#{1,6} ", first), first
    return first


def windows(text: str, bodies: list[str]) -> list[str]:
    """For each body, in order, the text from the end of the body before it to the start of
    the body after it: where its author, date and verdict are said."""
    at = [text.index(body) for body in bodies]
    assert at == sorted(at), "in order"
    ends = [a + len(b) for a, b in zip(at, bodies, strict=True)]
    starts = [*at[1:], len(text)]
    begins = [text.index("Comments"), *ends[:-1]]
    return [text[begin:end] for begin, end in zip(begins, starts, strict=True)]


def test_a_pull_request_note_says_who_merged_what_into_what_with_its_conversation(kind, fake):
    notes_world(fake)
    notes = written_notes(kind, fake)
    note = notes[pr_ref(12)]
    assert note.title == f"{REPO}#12: Back off between webhook retries"
    assert note.project == "quill"
    assert (note.item, notes[pr_ref(13)].item) == ("pull:12", "pull:13")
    text = note.text
    assert note.title in heading(text)
    assert "ada-park" in fact(text, "Opened by")
    assert "brook-lane" in fact(text, "Merged by")
    assert "2099-03-04" in fact(text, "Merged", without="Merged by")
    assert "release/2099" in fact(text, "Into")
    labels = fact(text, "Labels")
    assert "payments" in labels and "retry-queue" in labels
    assert PR_BODY.strip() in text
    assert text.index("Opened by") < text.index(PR_BODY.strip())
    assert any(re.fullmatch(r"#{1,6} Comments", line) for line in text.splitlines())
    assert text.index(PR_BODY.strip()) < text.index("Comments")

    comments = ["Does this cover refunds?", "Yes, the same queue."]
    reviews = ["Right.", "Please split the migration."]
    for body in comments + reviews:
        assert body in text
    assert text.index(comments[0]) < text.index(comments[1])
    assert text.index(reviews[0]) < text.index(reviews[1])
    order = sorted(comments + reviews, key=text.index)
    said = dict(zip(order, windows(text, order), strict=True))
    for body, author, day in (
        (comments[0], "cy-moss", "2099-03-02"),
        (comments[1], "ada-park", "2099-03-03"),
        (reviews[0], "brook-lane", "2099-03-03"),
        (reviews[1], "cy-moss", "2099-03-03"),
    ):
        assert author in said[body] and day in said[body], body
    assert "approved" in said[reviews[0]].lower()
    assert "changes" in said[reviews[1]].lower()
    assert "APPROVED" not in text and "CHANGES_REQUESTED" not in text  # in words
    assert "dee-quinn" not in text  # a review without a body is not written
    assert Counter(r.number for r in fake.requests if r.endpoint == "pull") == Counter(
        {12: 1, 13: 1}
    )


def test_an_issue_note_says_how_it_was_closed_and_a_missing_body_still_writes_it(kind, fake):
    notes_world(fake)
    notes = written_notes(kind, fake)
    completed, planned, plain = (notes[issue_ref(n)] for n in (20, 21, 22))
    assert completed.title == f"{REPO}#20: Refunds stall after the third retry"
    assert (completed.item, planned.item, plain.item) == ("issue:20", "issue:21", "issue:22")
    text = completed.text
    assert completed.title in heading(text)
    assert "dee-quinn" in fact(text, "Opened by")
    assert "2099-03-05" in fact(text, "Closed", without="Closed as")
    assert "completed" in fact(text, "Closed as")
    labels = fact(text, "Labels")
    assert "bug" in labels and "refunds" in labels  # a label as an object, and as a string
    assert "not planned" in fact(planned.text, "Closed as")
    assert "not_planned" not in planned.text
    assert plain.title == f"{REPO}#22: Old style close"
    assert "2099-03-05" in fact(plain.text, "Closed", without="Closed as")


def test_a_release_note_says_its_tag_who_published_it_and_whether_it_is_a_prerelease(kind, fake):
    notes_world(fake)
    notes = written_notes(kind, fake)
    named, candidate = notes[release_ref("v2.1.0")], notes[release_ref("v2.2.0-rc.1")]
    assert named.title == f"{REPO} v2.1.0: Faster retries"
    assert candidate.title == f"{REPO} v2.2.0-rc.1"
    assert named.item == f"release:{RELEASE_BASE + 1}"  # by its id, not its tag
    assert candidate.item == f"release:{RELEASE_BASE + 2}"
    text = named.text
    assert named.title in heading(text)
    assert "v2.1.0" in fact(text, "Tag")
    assert "2099-03-06" in fact(text, "Published", without="Published by")
    assert "ada-park" in fact(text, "Published by")
    assert RELEASE_BODY.strip() in text
    assert "Prerelease" not in text and "Prerelease" in candidate.text
    assert "quill-ci[bot] (bot)" in fact(candidate.text, "Published by")


def test_a_bot_is_marked_and_a_deleted_account_is_written_the_same_either_way(kind, stand_ins):
    texts = []
    for ghost in (False, True):
        made = stand_ins()
        made.ghost = ghost
        pr = made.pull_request(5, "From the bot", author="quill-ci[bot]")
        made.comment(pr, SINCE + HOUR, None, "Written by an account since deleted.")
        made.comment(pr, SINCE + 2 * HOUR, "quill-ci[bot]", "Checks passed.")
        made.review(pr, SINCE + 3 * HOUR, None, "APPROVED", "Approved before leaving.")
        made.merge(pr, SINCE + DAY, None)
        gone = made.issue(6, "Opened by someone gone", author=None)
        made.close(gone, SINCE + 2 * DAY)
        made.release(1, "v1.0.0", "By someone gone", published=SINCE + 3 * DAY, author=None)
        got = pull(made, opened(kind, made))
        assert refs(got) == [pr_ref(5), issue_ref(6), release_ref("v1.0.0")]
        texts.append([draft.text for draft in got.drafts])
        bot = got.drafts[0].text
        assert "quill-ci[bot] (bot)" in fact(bot, "Opened by")
        assert "Written by an account since deleted." in bot
        for draft in got.drafts:
            assert "ghost" not in draft.text.lower()
    assert texts[0] == texts[1]


def test_a_signed_address_loses_its_query_and_an_ordinary_one_is_kept(kind, fake):
    jwt = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "c11" + "Payload" + "." + "c11Sig"
    token = "c11" + "AttachmentToken" + "77"
    signature = "c11" + "deadbeef" * 4
    image = "https://private-user-images.githubusercontent.com/90001/300001-c11.png"
    upload = "https://github.com/user-attachments/files/4401/report.pdf"
    asset = "https://objects.githubusercontent.com/github-production-release-asset/1/2"
    signed = {
        image: f"{image}?jwt={jwt}",
        upload: f"{upload}?token={token}",
        asset: f"{asset}?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature={signature}&x=1",
    }
    ordinary = [
        "https://github.com/quillworks/quill/issues?q=is%3Aopen+label%3Abug",
        "https://docs.example.org/retries?section=2",
    ]
    body = (
        f"Before and after:\n\n![before]({signed[image]})\n\n[the report]({signed[upload]})\n\n"
        f"Asset: {signed[asset]}\n\nSee {ordinary[0]} and {ordinary[1]}.\n"
    )
    pr = fake.pull_request(3, "With images", body=body)
    fake.comment(pr, SINCE + HOUR, "cy-moss", f"Mine: ![after]({signed[image]}) and {ordinary[1]}")
    fake.merge(pr, SINCE + DAY)
    fake.release(
        1, "v1.0.0", "Signed asset", published=SINCE + 2 * DAY, body=f"Get {signed[asset]}"
    )
    got = pull(fake, opened(kind, fake))
    assert refs(got) == [pr_ref(3), release_ref("v1.0.0")]
    note, release = (draft.text for draft in got.drafts)
    for text in (note, release):
        for secret in (jwt, token, signature, "jwt=", "X-Amz-Signature"):
            assert secret not in text
    for bare in signed:
        assert bare in note
    assert note.count(image) == 2  # in the body and in the comment
    for kept in ordinary:
        assert kept in note
    assert asset in release


def test_two_hundred_and_fifty_comments_are_read_in_three_requests_in_order(kind, fake):
    issue = fake.issue(9, "A long discussion")
    authors = ["ada-park", "brook-lane", "cy-moss"]
    for i in range(250):
        fake.comment(issue, SINCE + i * MINUTE, authors[i % 3], f"Comment {i:03d} on the queue.")
    fake.close(issue, SINCE + DAY)
    got = pull(fake, opened(kind, fake))
    (note,) = got.drafts
    at = [note.text.index(f"Comment {i:03d} on the queue.") for i in range(250)]
    assert at == sorted(at)
    asked = [r for r in fake.requests if r.endpoint == "comments"]
    assert len(asked) == 3 and all(r.number == 9 for r in asked)
    assert asked[0].params.get("per_page") == "100"

    quiet = FakeGitHub(Clock(NOW))
    quiet_pr = quiet.pull_request(1, merged=SINCE + DAY)
    quiet.comment(quiet_pr, SINCE + HOUR, "cy-moss", "Not to be read.")
    quiet.review(quiet_pr, SINCE + HOUR, "dee-quinn", "APPROVED", "Nor this.")
    quiet_issue = quiet.issue(2, closed=SINCE + DAY + HOUR)
    quiet.comment(quiet_issue, SINCE + HOUR, "cy-moss", "Nor this one.")
    got = pull(quiet, opened(kind, quiet, comments=False))
    assert refs(got) == [pr_ref(1), issue_ref(2)]
    assert not {r.endpoint for r in quiet.requests} & {"comments", "reviews"}
    assert not any("Not to be read." in d.text or "Nor this" in d.text for d in got.drafts)
    assert quiet.problems() == []


# Through the runner


def make_world(tmp_path: Path, fake: FakeGitHub, switched_on: datetime = SINCE):
    clock = Clock(switched_on - DAY)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    for name in ("quill", "other"):
        store.create_project(name)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return SimpleNamespace(store=store, db=db, clock=clock, http=http, switched_on=switched_on)


@pytest.fixture
def world(tmp_path, fake):
    made = make_world(tmp_path, fake)
    yield made
    made.store.close()


def start(world, **settings) -> None:
    world.store.add_source("github", "github", config(**settings))
    world.clock.now = world.switched_on
    world.store.enable_source("github")


def run(world, fake, at: datetime, env=ENV, saved=None):
    fake.clock.now = at
    fake.new_pull()
    return asyncio.run(
        run_source(
            world.store, "github", http=world.http, env=env, clock=fake.clock, sleep=fake.sleep,
            saved=saved,
        )
    )  # fmt: skip


def notes(world, project: str = "quill") -> list:
    return world.store.list_notes(world.store.get_project(project).id)


def no_token_kept(world, caplog) -> None:
    stored = dump(world.db)
    for secret in (TOKEN, WRONG_TOKEN):
        assert secret not in stored and secret.lower() not in stored.lower()
        assert secret not in caplog.text
    for event in world.store.list_audit(limit=200):
        assert TOKEN not in json.dumps(event.detail)


def test_through_the_runner_it_is_off_until_switched_on_and_writes_each_item_once(
    world, fake, caplog
):
    caplog.set_level(logging.DEBUG)
    fake.pull_request(1, "Before it was on", merged=SINCE - HOUR)
    pr = fake.pull_request(2, merged=SINCE + HOUR)
    issue = fake.issue(3, closed=SINCE + DAY)
    fake.release(1, "v1.0.0", "First", published=SINCE + 2 * DAY)

    added = world.store.add_source("github", "github", config(projects=[]))
    assert added.enabled is False
    assert run(world, fake, NOW).off is True and fake.requests == []

    world.clock.now = world.switched_on
    world.store.enable_source("github", projects=["quill"])
    report = run(world, fake, NOW)
    assert (report.saved, report.unchanged, report.refused, report.rejected) == (3, 0, 0, 0)
    held = notes(world)
    assert sorted(c.source_ref for c in held) == [issue_ref(3), pr_ref(2), release_ref("v1.0.0")]
    assert {c.via for c in held} == {"github"}
    assert notes(world, "other") == []
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind) == ("source.synced", "system")
    assert (event.detail["source"], event.detail["saved"]) == ("github", 3)

    fake.edit(pr, NOW + HOUR, "Edited after it was written.")
    fake.reopen(issue, NOW + HOUR)
    fake.close(issue, NOW + 2 * HOUR, "not_planned")
    for hours in (3, 30):
        again = run(world, fake, NOW + hours * HOUR)
        assert (again.saved, again.unchanged) == (0, 0), hours

    world.clock.now = NOW + 31 * HOUR
    world.store.disable_source("github")
    fake.reopen(issue, NOW + 32 * HOUR)
    fake.close(issue, NOW + 33 * HOUR)  # closed again while it was off
    fake.pull_request(5, "Merged while it was off", merged=NOW + 33 * HOUR)
    assert run(world, fake, NOW + 34 * HOUR).off is True
    world.clock.now = NOW + 35 * HOUR
    world.store.enable_source("github")
    fake.reopen(issue, NOW + 36 * HOUR)
    fake.close(issue, NOW + 37 * HOUR, "completed")  # and again once it was on
    fake.pull_request(6, "Merged once it was on again", merged=NOW + 36 * HOUR)
    for hours, saved in ((40, 1), (64, 0), (90, 0)):
        assert run(world, fake, NOW + hours * HOUR).saved == saved, hours
    assert sorted(c.source_ref for c in notes(world)) == [
        issue_ref(3),
        pr_ref(2),
        pr_ref(6),
        release_ref("v1.0.0"),
    ]
    no_token_kept(world, caplog)


@pytest.mark.parametrize("moves", [True, False], ids=["close-moves-updated", "close-keeps-updated"])
def test_an_item_closed_again_long_after_it_was_written_is_not_written_again(world, fake, moves):
    fake.moves = moves
    start(world, lookback_days=1)
    issue = fake.issue(3, created=SINCE - 5 * DAY)
    fake.close(issue, SINCE + DAY)
    pr = fake.pull_request(4, merged=SINCE + DAY)
    began = SINCE + DAY + HOUR  # its first run, an hour after they finished
    assert run(world, fake, began).saved == 2
    for day in range(1, 41):  # forty days of runs, two a day: its digest is long gone
        assert run(world, fake, began + day * DAY).saved == 0
        assert run(world, fake, began + day * DAY + 12 * HOUR).saved == 0

    later = began + 41 * DAY
    fake.reopen(issue, later)
    fake.close(issue, later + HOUR, "not_planned")
    fake.edit(pr, later + HOUR, "Edited forty days after it was written.")
    fake.comment(pr, later + 2 * HOUR, "cy-moss", "And commented on.")
    saved, unchanged = 0, 0
    for hour in range(3, 40):
        report = run(world, fake, later + hour * HOUR)
        saved, unchanged = saved + report.saved, unchanged + report.unchanged
    assert saved == 0
    assert unchanged >= 1  # the kind proposed it again: the runner did not write it
    held = Counter(c.source_ref for c in notes(world))
    assert held == Counter({issue_ref(3): 1, pr_ref(4): 1})


@pytest.mark.parametrize("moves", [True, False], ids=["close-moves-updated", "close-keeps-updated"])
def test_what_finished_since_switch_on_is_written_however_late_it_is_found(world, fake, moves):
    fake.moves = moves
    start(world, lookback_days=1)
    for n in (2, 4):
        fake.issue(n, created=SINCE - 5 * DAY)
    fake.pull_request(3, created=SINCE - 5 * DAY)
    fake.close(fake.items[2], SINCE + HOUR)
    fake.merge(fake.items[3], SINCE + 2 * HOUR)
    fake.release(1, "v1.0.0", "Early", published=SINCE + 3 * HOUR)
    late = SINCE + 6 * DAY  # the first run, six times lookback_days after they finished
    report = run(world, fake, late)
    assert report.saved == 3
    assert sorted(c.source_ref for c in notes(world)) == [
        issue_ref(2),
        pr_ref(3),
        release_ref("v1.0.0"),
    ]
    assert run(world, fake, late + DAY + HOUR).saved == 0

    fake.close(fake.items[4], late + 3 * DAY)  # closed while the source could not run
    assert run(world, fake, late + 12 * DAY).saved == 1  # eleven days stopped
    assert issue_ref(4) in {c.source_ref for c in notes(world)}


REFUSALS = [
    ("401", "*", ("status", 401), [TOKEN_ENV, REPO]),
    ("wrong token", None, None, [TOKEN_ENV, REPO]),
    ("404 issues", "issues", ("status", 404), [TOKEN_ENV, REPO, "permission", "approv"]),
    ("404 releases", "releases", ("status", 404), [TOKEN_ENV, REPO, "permission", "approv"]),
    ("403 issues", "issues", ("permission",), ["list|issue"]),
    ("403 pull", "pull", ("permission",), ["pull request"]),
    ("403 comments", "comments", ("permission",), ["comment"]),
    ("403 reviews", "reviews", ("permission",), ["review"]),
    ("403 releases", "releases", ("permission",), ["release"]),
    ("500 issues", "issues", ("status", 500), []),
    ("502 pull", "pull", ("status", 502), []),
    ("503 comments", "comments", ("status", 503), []),
    ("timeout issues", "issues", ("timeout",), []),
    ("timeout pull", "pull", ("timeout",), []),
    ("not json issues", "issues", ("not json",), []),
    ("not json releases", "releases", ("not json",), []),
    ("wrong shape issues", "issues", ("wrong shape",), []),
    ("wrong shape pull", "pull", ("wrong shape",), []),
    ("wrong shape reviews", "reviews", ("wrong shape",), []),
]


@pytest.mark.parametrize(
    ("case", "endpoint", "fault", "words"), REFUSALS, ids=[r[0] for r in REFUSALS]
)
def test_a_refusal_fails_the_run_moves_no_cursor_and_holds_no_token(
    world, fake, caplog, case, endpoint, fault, words
):
    caplog.set_level(logging.DEBUG)
    start(world)
    fake.pull_request(1, merged=SINCE + DAY)
    assert run(world, fake, NOW).saved == 1
    before = world.store.get_source("github")

    pr = fake.pull_request(2, "Merged later", merged=NOW + HOUR)
    fake.comment(pr, NOW + 10 * MINUTE, "cy-moss", "A comment.")
    fake.review(pr, NOW + 20 * MINUTE, "dee-quinn", "APPROVED", "A review.")
    fake.release(1, "v1.0.0", "Later", published=NOW + HOUR)
    env = {TOKEN_ENV: WRONG_TOKEN} if case == "wrong token" else ENV
    if fault is not None:
        fake.always[endpoint] = fault
    with pytest.raises(SourceError) as failed:
        run(world, fake, NOW + 3 * HOUR, env=env)
    said = str(failed.value)
    for word in words:
        assert any(w in said.lower() for w in word.lower().split("|")), (word, said)
    if fault == ("permission",):  # a 403 without either header is a refusal, not a limit
        assert "rate limit" not in said.lower() and "rate-limit" not in said.lower(), said
    for secret in (TOKEN, WRONG_TOKEN, CANARY):
        assert secret not in said
    after = world.store.get_source("github")
    assert after.cursor == before.cursor and after.last_error
    assert TOKEN not in after.last_error and CANARY not in after.last_error
    assert [c.source_ref for c in notes(world)] == [pr_ref(1)]

    fake.always.clear()
    report = run(world, fake, NOW + 4 * HOUR)
    assert report.saved == 2 and world.store.get_source("github").last_error is None
    assert sorted(c.source_ref for c in notes(world)) == [
        pr_ref(1),
        pr_ref(2),
        release_ref("v1.0.0"),
    ]
    assert run(world, fake, NOW + 5 * HOUR).saved == 0
    no_token_kept(world, caplog)


@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.parametrize("how", ["remaining", "retry"], ids=["remaining-zero", "retry-after"])
def test_a_rate_limit_on_the_listing_stops_the_pull_and_the_next_goes_on(kind, fake, status, how):
    fake.pull_request(1, merged=SINCE + DAY)
    fake.issue(2, closed=SINCE + DAY + HOUR)
    fake.release(1, "v1.0.0", published=SINCE + 2 * DAY)
    fake.faults.append(("issues", None, ("limit", status, how)))
    read = reader(kind, fake)
    try:
        got = read()
        cursor, first = got.cursor, refs(got)
    except SourceError as error:
        cursor, first = None, []
        assert TOKEN not in str(error) and CANARY not in str(error)
    asked = fake.of_pull()
    limited = [i for i, r in enumerate(asked) if r.status == status]
    assert limited == [len(asked) - 1]  # nothing asked after it
    assert fake.slept == []
    assert pr_ref(1) not in first and issue_ref(2) not in first

    rest, later = [], cursor
    for hour in range(27):
        fake.clock.now = NOW + 5 * MINUTE + hour * HOUR
        got = read(later)
        later = got.cursor
        rest += refs(got)
    assert sorted(first + rest) == sorted([pr_ref(1), issue_ref(2), release_ref("v1.0.0")])


def test_a_rate_limit_halfway_keeps_what_was_finished_and_the_next_pull_does_the_rest(kind, fake):
    for n in (1, 2, 3):
        pr = fake.pull_request(n, f"Change {n}", merged=SINCE + n * DAY)
        fake.comment(pr, SINCE + n * DAY - HOUR, "cy-moss", f"Comment on {n}.")
        fake.review(pr, SINCE + n * DAY - HOUR, "dee-quinn", "APPROVED", f"Review of {n}.")
    fake.faults.append(("comments", 3, ("limit", 429, "retry")))
    read = reader(kind, fake)
    try:
        got = read()
        cursor, first = got.cursor, refs(got)
    except SourceError:
        cursor, first = None, []
    asked = fake.of_pull()
    limited = next(i for i, r in enumerate(asked) if r.status == 429)
    assert limited == len(asked) - 1 and fake.slept == []

    def finished(n: int) -> bool:
        done = {r.endpoint for r in asked[:limited] if r.number == n and r.status == 200}
        return {"pull", "comments", "reviews"} <= done

    ready = len(list(itertools.takewhile(finished, (1, 2, 3))))
    full = [pr_ref(1), pr_ref(2), pr_ref(3)]
    assert first == full[: len(first)] and len(first) >= ready and pr_ref(3) not in first
    fake.clock.now += 5 * MINUTE
    rest = read(cursor)
    assert refs(rest) == full[len(first) :]


@pytest.mark.parametrize(
    ("what", "where"),
    [("pull request", "pull"), ("pull request", "comments"), ("issue", "comments")],
)
def test_an_item_gone_from_github_is_skipped_once_and_the_others_are_written(
    kind, fake, what, where
):
    fake.pull_request(1, merged=SINCE + DAY)
    if what == "issue":
        gone = fake.issue(2, "Deleted since", closed=SINCE + DAY + HOUR)
    else:
        gone = fake.pull_request(2, "Deleted since", merged=SINCE + DAY + HOUR)
    gone.gone_on = {where}
    fake.issue(3, closed=SINCE + 2 * DAY)
    read = reader(kind, fake)
    got = read()
    assert refs(got) == [pr_ref(1), issue_ref(3)]
    (skip,) = got.skipped
    assert skip.reason and TOKEN not in skip.reason and CANARY not in skip.reason
    fake.clock.now += 2 * HOUR
    again = read(got.cursor)
    assert again.drafts == [] and again.skipped == []


def test_bounds_keep_a_long_body_many_comments_and_a_long_title_and_each_item_has_a_page(
    world, fake
):
    start(world)
    paragraphs = [f"Paragraph {i:04d} of the long description." for i in range(1, 4001)]
    fake.pull_request(
        1, "A long description", body="\n\n".join(paragraphs),
        merged=datetime(2099, 3, 2, tzinfo=UTC),
    )  # fmt: skip
    busy = fake.issue(2, "A busy thread", created=datetime(2099, 1, 2, tzinfo=UTC))
    for i in range(1, 3001):
        fake.comment(busy, busy.created + i * MINUTE, "cy-moss", f"Comment {i:04d} here.")
    fake.close(busy, datetime(2099, 3, 2, 6, tzinfo=UTC))
    huge = fake.issue(3, "One long comment", created=datetime(2099, 1, 2, tzinfo=UTC))
    fake.comment(huge, huge.created + HOUR, "ada-park", "\n\n".join(paragraphs))
    fake.comment(huge, huge.created + 2 * HOUR, "brook-lane", "A short one after it.")
    fake.close(huge, datetime(2099, 3, 2, 12, tzinfo=UTC))
    fake.pull_request(4, "Long title " + "word " * 60, merged=datetime(2099, 3, 3, tzinfo=UTC))
    fake.pull_request(5, "Long title as well", merged=datetime(2099, 3, 4, tzinfo=UTC))
    pages: list[tuple[str, str]] = []
    report = run(world, fake, NOW, saved=lambda project, slug: pages.append((project, slug)))
    assert (report.saved, report.skipped, report.malformed) == (5, 0, 0), report
    assert len(pages) == 5 and len(set(pages)) == 5  # a page an item: one month, five pages
    held = {c.source_ref: c for c in notes(world)}
    assert all(note.topic is None for note in held.values())

    long_body = held[pr_ref(1)].text
    assert "Paragraph 0001 " in long_body and "Paragraph 4000 " not in long_body
    kept = [i for i in range(1, 4001) if f"Paragraph {i:04d} " in long_body]
    assert kept == list(range(1, len(kept) + 1))
    tail = long_body[long_body.index(f"Paragraph {kept[-1]:04d} ") :].splitlines()[1:]
    said = [line for line in tail if line.strip()]
    assert said  # a line saying it was cut
    assert not set("_*") & set(said[0])  # in plain text

    thread = held[issue_ref(2)].text
    shown = [i for i in range(1, 3001) if f"Comment {i:04d} here." in thread]
    assert shown == list(range(1, len(shown) + 1)) and 0 < len(shown) < 3000
    left = 3000 - len(shown)
    counted = [line for line in thread.splitlines() if f"{left:,}" in line or str(left) in line]
    assert counted and not set("_*") & set(counted[-1])  # how many were left out, plainly

    one = held[issue_ref(3)].text
    assert "Paragraph 0001 " in one and "Paragraph 4000 " not in one
    titled = heading(held[pr_ref(4)].text)
    assert titled.startswith(f"# {REPO}#4: Long title") and len(titled) <= len("# ") + 200


# The cursor


def leaves(value) -> list:
    if isinstance(value, dict):
        return [*value.keys(), *(leaf for v in value.values() for leaf in leaves(v))]
    if isinstance(value, list):
        return [leaf for v in value for leaf in leaves(v)]
    return [value]


def test_the_cursor_holds_no_number_id_title_or_token_and_stays_bounded(kind, fake):
    read = reader(kind, fake, since=NOW, comments=False)
    spacing = 399 * DAY / 1000
    numbers: set[int] = set()
    titles: list[str] = []
    cursor, written, expected, checks, lengths, added = None, Counter(), Counter(), [], {}, 0
    for step in range(1, 801):  # a pull every twelve hours for 400 days
        fake.clock.now = NOW + step * 12 * HOUR
        while added < 1000 and NOW + spacing * (added + 1) <= fake.clock.now - HOUR:
            added += 1
            done = NOW + spacing * added
            if added % 10 < 5:
                fake.pull_request(300_000 + added, f"Ledger change {added}", merged=done)
                numbers.add(300_000 + added)
                expected[pr_ref(300_000 + added)] += 1
            elif added % 10 < 9:
                fake.issue(300_000 + added, f"Ledger report {added}", closed=done)
                numbers.add(300_000 + added)
                expected[issue_ref(300_000 + added)] += 1
            else:
                fake.release(added, f"v{added}.0.0", f"Ledger release {added}", published=done)
                numbers.add(RELEASE_BASE + added)
                expected[release_ref(f"v{added}.0.0")] += 1
            titles.append(f"Ledger change {added}")
        got = read(cursor)
        written.update(refs(got))
        cursor = got.cursor
        assert TOKEN not in cursor and TOKEN.lower() not in cursor.lower()
        assert "Ledger " not in cursor
        for leaf in leaves(json.loads(cursor)):  # opaque, but JSON
            if isinstance(leaf, int) and not isinstance(leaf, bool):
                assert leaf not in numbers
            if isinstance(leaf, str):
                assert not set(re.split(r"[^0-9A-Za-z]+", leaf)) & {str(n) for n in numbers}
        found = searches(fake)
        if found:  # every check's finds are written in its pull here
            previous = checks[-1] if checks else None
            assert_windows(found, window(NOW, previous))
            checks.append(fake.clock.now)
        if step == 120:
            lengths["two months"] = len(cursor)
    assert added == 1000 and written == expected
    assert len(cursor) <= 1.5 * lengths["two months"] + 200  # it did not grow with the items
    listed = fake.asked("issues")
    assert listed and fake.clock.now - parsed(listed[-1].params["since"]) < 2 * DAY
    assert len(checks) >= 250  # about once a day: pulls come every twelve hours
    assert all(later - earlier >= DAY for earlier, later in itertools.pairwise(checks))


def test_a_pull_cut_by_its_deadline_and_every_resume_point_go_on_without_loss_or_repeat(kind, fake):
    for n in range(1, 7):
        pr = fake.pull_request(n, f"Change {n}", merged=SINCE + n * DAY)
        fake.comment(pr, SINCE + n * DAY - HOUR, "cy-moss", f"Comment on {n}.")
        fake.review(pr, SINCE + n * DAY - HOUR, "dee-quinn", "APPROVED", f"Review of {n}.")
    whole = pull(fake, opened(kind, fake))
    full = refs(whole)
    assert full == [pr_ref(n) for n in range(1, 7)]
    for index, draft in enumerate(whole.drafts):  # a run that stops right after this draft
        if draft.resume is None:
            continue
        fake.clock.now += HOUR
        rest = pull(fake, opened(kind, fake), draft.resume)
        assert refs(rest) == full[index + 1 :], index

    fake.clock.now += HOUR
    fake.tick = MINUTE  # every request takes a minute
    hurried = opened(kind, fake, deadline=fake.clock.now + 20 * MINUTE)
    cut = pull(fake, hurried)
    assert 0 < len(cut.drafts) < len(full)
    assert refs(cut) == full[: len(cut.drafts)]
    fake.tick = timedelta(0)
    fake.clock.now += HOUR
    rest = pull(fake, opened(kind, fake), cut.cursor)
    assert refs(rest) == full[len(cut.drafts) :]
