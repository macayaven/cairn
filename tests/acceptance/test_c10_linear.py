"""C10 acceptance tests (frozen): the Linear source, finished issues read from Linear's GraphQL API.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/sources/linear.py and
src/cairn/sources/__init__.py. The API's request and response shapes are recorded in
docs/linear-api.md; what Linear's documentation does not state is marked there as such, and
the connector depends on none of it: the stand-in below behaves either way where it matters
(trashed issues listed or not; pages newest first, shuffled, or with an issue repeated on two
pages; an error under `extensions.code` or `extensions.type`; a rate limit as 429 or as 400).

The kind
- `KINDS["linear"]` (cairn.sources.linear). It starts off. Its notes are written with the
  via "linear". It is not withheld from a model (`withheld` is False: it reads the
  workspace's own tracker). The project it proposes (`default_project`) is "linear"; `describe`
  and `switching_on()` each say in a sentence what it reads.
- The config is a JSON object. `check` returns it with the defaults filled in, or raises
  ValueError naming the setting at fault, never a value that could be a credential:
  - `key_env` (required): the name of the environment variable that holds a personal API
    key. A value that looks like a credential is refused.
  - `projects`: a list of at most one project name; may be empty while the source is off.
  - `teams` (optional): 1 to 20 team keys, each 1 to 10 letters, digits or "_" (the prefix
    of an identifier: ENG in ENG-123). Without it, every team the key can read.
  - `linear_project` (optional): one Linear project's id, a UUID. At most one of `teams` and
    `linear_project`.
  - `comments` (default true): also write each issue's comments.
  - `settle_minutes` (default 30, 0 to 1440) and `lookback_days` (default 30, 1 to 365).
  - `base_url`: "https://api.linear.app/graphql" unless given; https, or http on this
    machine; no user or password. A credential inside it is refused and never repeated.
  - No other setting.
- `open(name, config, *, http, env, clock, sleep, deadline, since)` reads the key from `env`
  (a missing or empty variable is a SourceError naming the variable). Every request is a
  POST of JSON to `base_url` and nowhere else, with the header `Authorization: <key>`
  exactly: a personal key takes no "Bearer". The key is among the source's `secrets`.

What it reads
- Issues whose workflow state's type is "completed" or "canceled", finished at or after
  `since` (the source's last switch-on): `completedAt` for a completed issue, `canceledAt`
  for a canceled one, checked by the connector whatever bounds it asked the listing for.
  Never one of type "duplicate" (though Linear sets its `canceledAt`), "started", "backlog"
  or any other type; never a trashed one (`trashed: true`), whether the listing returns it
  or not. Archived issues are read: the listing asks with `includeArchived: true`.
- Each issue once, by its `id`, for as long as the source exists: once it is written, a
  later edit, a new comment, a reopening, a second finish or a move to another team (a new
  `identifier` and `url`) writes nothing, across runs and across switching the source off
  and on. Two things make this so. The kind's own digests, kept for `lookback_days` after
  the finish they were written for, whatever `since` is (see the cursor), keep a pull from
  proposing again what it wrote lately. And every draft names its item (`NoteDraft.item`
  is the issue's `id`, task I1), so the runner does not write an item its source wrote
  before: an issue finished again after its digest is gone is proposed, and the run counts
  it `unchanged` and writes nothing. Where the runner keeps that record is not pinned here.
- Only once settled: finished at least `settle_minutes` ago by the injected clock. A
  settling issue waits, and is read with what it holds when it has settled.
- With `teams` or `linear_project`, only what that filter selects. A team the key cannot see is
  not an error: the run writes what the listing returns.
- With `comments`, every comment the issue has when it is read, roots and replies.

How it finds them
- Every pull asks one listing, operation `FinishedIssues` (the request's `operationName`,
  and a `query FinishedIssues` operation in its text): `issues(first: 25, after: $after,
  includeArchived: true, orderBy: updatedAt, filter: $filter)`, with those arguments written
  so, and no variables but `filter` and `after` (null or absent on a scan's first page).
  The filter is `{"state": {"type": {"in": ["completed", "canceled"]}}, "or":
  [{"completedAt": {"gte": T}}, {"canceledAt": {"gte": T}}]}` (lists in any order), with the
  team or project condition beside them when one is set, in any form the API documents
  (`and`, `or`, `eq`, `in`, on the team's `key` or the project's `id`). T is an ISO 8601
  instant with its zone, a fixed overlap (more than nothing, at most a day) before the
  cursor's mark; on the first pull the mark is `since`. A settling issue holds the mark
  back. Every page is followed while `pageInfo.hasNextPage` is true, `after` being the last
  page's `endCursor`; a page cursor is good only within the pull that got it.
- At most once a day (by the injected clock, recorded in the cursor) a pull also asks with
  T the later of `since` and `lookback_days` ago (or up to a day before that), which finds
  an issue the first listing could not see.
- The listing's order across pages and its tie-break are not stated, and pages shift while
  issues change, so a pull collects its candidates and writes them in its own order: oldest
  finish first, then by `id`. Shuffled pages, or an issue listed on two pages, give the same
  drafts, none twice.
- At most 25 issues a pull; the rest wait for the next pull, in the same order, none lost.
- Comments: operation `IssueComments`, `issue(id: $id) { comments(first: 50, after: $after)
  { ... } }`, every page, then ordered by the connector oldest first (`createdAt`, then
  `id`), each reply after the root it answers (and after that root's earlier replies). The
  listing never asks for comments (one query may cost at most 10,000 points), and no request
  asks for `email`.
- The cursor is opaque JSON the connector owns: the mark, when it last rescanned, and short
  digests of the ids it wrote, kept only while a rescan could list them, so it stays
  bounded (1,000 issues over 400 days). Never an id, an identifier, a title or a key.

The stand-in's answers (docs/linear-api.md, sections D, E, G and I). A listed issue has
`id`, `identifier`, `number`, `title`, `description`, `url`, `createdAt`, `updatedAt`,
`completedAt`, `canceledAt`, `archivedAt`, `trashed` (true or null), `state { id name type }`,
`team { id key name }`, `project { id name }` (null without one), `assignee` and `creator`
(`{ id name displayName }`, or null) and `labels { nodes { id name } pageInfo }`. A comment
has `id`, `body`, `createdAt`, `updatedAt`, `editedAt`, `user` (a person, or null),
`botActor { id type subType name userDisplayName }`, `externalUser { id name displayName }`
(each null when not set), `parent { id }` and `parentId` (null for a root). A connection has
`nodes`, `edges { node cursor }` and `pageInfo { hasNextPage hasPreviousPage startCursor
endCursor }`, newest first. Only the fields a query's text names come back, as GraphQL does.
Times are UTC with milliseconds and "Z". `issue(id:)` for an id it does not know fails.

What it writes, for each issue, into the config's project
- `title`: "<identifier>: <title>" as they are when read ("ENG-123: Fix the retry loop").
  `source_ref`: the issue's `url`.
- No `topic` (None): each issue has a page of its own, named by the note's title. (A page a
  project and month would fill: a page holds 2,000 claims.) A title past a note title's 200
  characters is cut, the identifier never: the title starts with "<identifier>: " whole and
  what follows is the start of the issue's title.
- `item`: the issue's `id`, on every draft.
- `text`: a first heading with the title; a list of facts, each a line naming the fact and
  its value: Team (its name), Project (only when there is one), State (the workflow state's
  name), Finished (the date, YYYY-MM-DD), Assignee and Creator (when there are any) by
  display name, Labels by name; then the description as Linear gives it (Markdown),
  unchanged; then, with `comments` and when there are any, a heading "Comments" and each
  comment with who wrote it, its date and its body, in the order above, a reply marked as a
  reply (it reads differently from a root comment).
- People: `displayName`, else `name`. A name that holds an email address, or no name at all,
  counts as no name (cairn.sources.names), so no address is written anywhere. A bot's comment
  (`botActor`) is credited to the bot's `name` with its service (`subType`); one made
  through an integration for an outside person (`externalUser`) to that person's name,
  marked as outside (it reads differently from a member's); one with nobody recorded is
  still written.
- An issue with no description and no comments is written: its facts are the note.
- Bounds: a description or a comment longer than a note's bound is cut, with a line after it
  saying so (3,000 paragraphs, more than a page holds, keep fewer than 2,000). An issue with
  more comments than a stated number (from 120 to 999) keeps the first, in the order above,
  and says after them how many were left out.

When Linear says no
- HTTP 401 or 403, or a GraphQL error whose `extensions.type` or `extensions.code` says
  authentication or forbidden ("authentication error", "AUTHENTICATION_ERROR", "forbidden",
  "FORBIDDEN", in any case), on any status: the pull fails with a SourceError naming the
  variable, never the key.
- A rate limit, HTTP 429 or a GraphQL error RATELIMITED / ratelimited (code or type) on any
  status, 400 included: the pull stops at once: no request after it, and no wait through
  `sleep` (Retry-After means only "not before the next run"). It returns the issues it had
  finished, with a cursor that goes on from there, so the run does not fail; when it had
  finished none it may return nothing or fail, and either way the cursor does not move. The
  next run goes on, and nothing is written twice.
- A response with both `data` and `errors` is a failure of that request: nothing from it is
  written, and the cursor does not move past it.
- 5xx, a timeout, a body that is not JSON or not the expected shape: the pull fails with a
  SourceError, so the run fails and no cursor moves; the next run writes what it did not.
- A failure's reason names the operation (FinishedIssues or IssueComments). Linear's
  message may be shown only when it holds no credential: no key reaches a reason, a log
  line, the database or the audit log.

Together with the framework: off when added; switched on with a project, `run_source` writes
each settled finished issue once via "linear", a page an issue; running again
writes nothing; the audit log's "source.synced" counts them.
"""

import asyncio
import hashlib
import itertools
import json
import logging
import random
import re
import sqlite3
import uuid
from base64 import b64encode, urlsafe_b64decode, urlsafe_b64encode
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from cairn.sources import (
    KINDS,
    SourceError,
    holds_secret,
    run_source,
    source_client,
    withheld_via,
)
from cairn.store import Store

KEY_ENV = "LINEAR_C10_KEY"
KEY = "c10" + "-linear-" + "made-up-key-" + "7d1e0f3a9c2b4e6f"  # never a real key
WRONG_KEY = "c10" + "-linear-" + "wrong-key-" + "1b2d3f4a"
ENV = {KEY_ENV: KEY}
TOKEN = "ghp_" + "C10aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zAaB3d"  # the shape of a credential
CANARY = "c10-" + "body-" + "canary-5e2a"  # in every error body the stand-in sends
BASE_URL = "https://linear.example.test/graphql"
DEFAULT_BASE_URL = "https://api.linear.app/graphql"
MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
NOW = datetime(2099, 4, 2, 12, 0, tzinfo=UTC)
SINCE = NOW - 30 * DAY  # when the source was switched on: 2099-03-03 12:00
LISTING, COMMENTS = "FinishedIssues", "IssueComments"


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def uid(kind: str, n: int) -> str:
    """A made-up UUID, the same for the same kind and number."""
    digest = hashlib.sha256(f"c10:{kind}:{n}".encode()).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4))


def z(moment: datetime) -> str:
    """A time as the API writes one: UTC, milliseconds, "Z"."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def instant(value: object) -> datetime | None:
    """An ISO 8601 instant with its zone, or None (a duration, a date, anything else)."""
    if not isinstance(value, str) or "P" in value.upper().lstrip("+-")[:1]:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else None


def tag(n: int) -> str:
    """A word made of letters only, different for each n (no digits to confuse a count)."""
    letters = ""
    for _ in range(4):
        n, rest = divmod(n, 26)
        letters = "abcdefghijklmnopqrstuvwxyz"[rest] + letters
    return letters


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


# The workspace at the stand-in


@dataclass(frozen=True)
class Person:
    """A Linear user as the API gives one without its email, which nobody asks for."""

    id: str
    name: str | None
    display: str | None


ADA = Person(uid("user", 1), "Ada Park", "adapark")
BROOK = Person(uid("user", 2), "Brook Lane", "brook@example.test")  # an address as a nick
CY = Person(uid("user", 3), "Cy Moss", "cymoss")
DEE = Person(uid("user", 4), "dee@example.test", "")  # nothing but an address
TEAMS = {"ENG": "Engineering", "OPS": "Operations", "SEC": "Security"}
TEAM_IDS = {key: uid("team", n) for n, key in enumerate(TEAMS)}
PROJECT_IDS = {"refunds": uid("project", 1), "ledger": uid("project", 2)}
STATES = {  # a state's name, as each team names it, and its type
    "Triage": "triage",
    "Backlog": "backlog",
    "Todo": "unstarted",
    "In Progress": "started",
    "Shipped": "completed",
    "Won't fix": "canceled",
    "Duplicate": "duplicate",
}


@dataclass
class Comment:
    id: str
    body: str
    created: datetime
    user: Person | None = None
    bot: dict | None = None  # ActorBot
    external: dict | None = None  # ExternalUser
    parent: str | None = None  # the root comment's id


@dataclass
class Issue:
    id: str
    n: int  # the test's name for it
    number: int
    team: str
    title: str
    state: str
    created: datetime
    updated: datetime
    completed: datetime | None = None
    canceled: datetime | None = None
    archived: datetime | None = None
    trashed: bool = False
    description: str | None = None
    project: str | None = None  # a key of PROJECT_IDS
    assignee: Person | None = None
    creator: Person | None = None
    labels: list[str] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    visible: bool = True  # False: the key cannot see it yet
    urls: list[str] = field(default_factory=list)  # every url it has had

    @property
    def identifier(self) -> str:
        return f"{self.team}-{self.number}"

    @property
    def url(self) -> str:
        return f"https://linear.example.test/c10/issue/{self.identifier}"

    @property
    def finished(self) -> datetime | None:
        return self.completed or self.canceled


@dataclass
class Rule:
    """Answer `answer` to the next `times` requests of operation `op` (None: all of them),
    after letting `skip` of them through."""

    op: str
    answer: object
    skip: int = 0
    times: int | None = 1


def refusal(status: int, ext: dict | None = None, headers: dict | None = None, text=None) -> dict:
    return {"status": status, "ext": ext, "headers": headers or {}, "text": text}


_MADE: list["FakeLinear"] = []


class FakeLinear:
    """Linear's GraphQL endpoint for the two operations the contract names, as an httpx mock
    over a small in-memory workspace (docs/linear-api.md). It reads each request's
    `operationName`, `query` and `variables` without a GraphQL library: it checks the query's
    text for what the contract says it must and must not ask (the listing's arguments, no
    `comments` in the listing, no `email` anywhere), applies the filter in `variables` as the
    API documents it (`and`, `or`, the state's type, the team's key, the project's id, the two
    finish times, with `eq`, `neq`, `in`, `nin`, `gt`, `gte`, `lt`, `lte`, `null`), pages by
    `first` and `after` with cursors bound to the filter and to the pull, and returns only the
    fields the query names. It records every request, and every way a request broke the
    contract in `violations` (each test then fails). It can be told to list trashed issues,
    shuffle what it lists, repeat an issue on the next page, hide a team from the key, and
    answer an error of any status and body, a timeout, or a partial answer."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.base_url = BASE_URL
        self.tick = timedelta(0)  # the clock moves this much with every request
        self.issues: dict[str, Issue] = {}
        self.project_names = {"refunds": "Refund revamp", "ledger": "Ledger audit"}
        self.hidden_teams: set[str] = set()  # teams the key cannot see
        self.lists_trashed = False  # issues(includeArchived: true) lists trashed issues
        self.shuffle = False  # list in an order of its own, not newest first
        self.repeat = False  # each page after the first starts with the last of the one before
        self.requests: list[SimpleNamespace] = []
        self.violations: list[str] = []
        self.slept: list[float] = []
        self.rules: list[Rule] = []
        self.gen = 0
        _MADE.append(self)

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def new_pull(self) -> None:
        self.gen += 1

    # The workspace, changed as Linear changes it

    def issue(self, n: int, title: str = "Fix the retry loop", **given) -> Issue:
        state = given.pop("state", "Shipped")
        finished = given.pop("finished", SINCE + DAY)
        created = given.pop("created", finished - 3 * DAY)
        given.setdefault("creator", CY)
        issue = Issue(
            id=uid("issue", n),
            n=n,
            number=given.pop("number", n),
            team=given.pop("team", "ENG"),
            title=title,
            state="Todo",
            created=created,
            updated=created,
            **given,
        )
        self.issues[issue.id] = issue
        issue.urls.append(issue.url)
        if STATES[state] in ("completed", "canceled", "duplicate"):
            self.finish(issue, finished, state)
        else:
            issue.state, issue.updated = state, finished
        return issue

    def finish(self, issue: Issue, at: datetime, state: str = "Shipped") -> None:
        kind = STATES[state]
        issue.state = state
        issue.completed = at if kind == "completed" else None
        issue.canceled = at if kind in ("canceled", "duplicate") else None
        issue.updated = at

    def reopen(self, issue: Issue, at: datetime) -> None:
        issue.state, issue.completed, issue.canceled, issue.updated = "In Progress", None, None, at

    def move(self, issue: Issue, team: str, number: int, at: datetime) -> None:
        issue.team, issue.number, issue.updated = team, number, at
        issue.urls.append(issue.url)

    def trash(self, issue: Issue, at: datetime) -> None:
        issue.trashed, issue.archived, issue.updated = True, at, at

    def comment(self, issue: Issue, n: int, body: str, at: datetime, **given) -> Comment:
        parent = given.pop("parent", None)
        if "bot" not in given and "external" not in given:
            given.setdefault("user", ADA)
        made = Comment(uid("comment", n), body, at, parent=parent.id if parent else None, **given)
        issue.comments.append(made)
        issue.updated = max(issue.updated, at)
        return made

    def fail(self, op: str, answer: object, *, skip: int = 0, times: int | None = 1) -> None:
        self.rules.append(Rule(op, answer, skip, times))

    # What the tests ask about the requests

    def of_pull(self, gen: int | None = None) -> list[SimpleNamespace]:
        gen = self.gen if gen is None else gen
        return [r for r in self.requests if r.gen == gen]

    def listings(self, gen: int | None = None) -> list[SimpleNamespace]:
        return [r for r in self.of_pull(gen) if r.op == LISTING]

    def comment_reads(self, gen: int | None = None) -> list[SimpleNamespace]:
        return [r for r in self.of_pull(gen) if r.op == COMMENTS]

    def number_of(self, source_ref: str) -> int:
        (found,) = [i.n for i in self.issues.values() if source_ref in i.urls]
        return found

    def numbers(self, pulled) -> list[int]:
        """The issues `pulled` proposed, by number; every draft names its issue's id."""
        found = []
        for draft in pulled.drafts:
            (issue,) = [i for i in self.issues.values() if draft.source_ref in i.urls]
            assert draft.item == issue.id, (issue.n, draft.item)
            found.append(issue.n)
        return found

    # The API

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.clock.now += self.tick
        record = SimpleNamespace(
            gen=self.gen,
            at=self.clock(),
            url=str(request.url),
            method=request.method,
            auth=request.headers.get("authorization"),
            op=None,
            query="",
            variables={},
            filter=None,
            after=None,
            T=None,
            issue=None,
            status=None,
            count=None,
            more=None,
            end=None,
        )
        self.requests.append(record)
        response = self._answer(request, record)
        record.status = response.status_code
        return response

    def _violation(self, record, why: str) -> None:
        self.violations.append(f"{record.op or 'a request'}: {why}")

    def _invalid(self, record, why: str) -> httpx.Response:
        self._violation(record, why)
        error = {"message": why, "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"}}
        return httpx.Response(400, json={"errors": [error]})

    def _refused(self, request: httpx.Request, record, answer: dict) -> httpx.Response:
        if answer["text"] is not None:
            return httpx.Response(
                answer["status"], text=f"{answer['text']} {CANARY}", headers=answer["headers"]
            )
        said = request.headers.get("authorization") or ""
        error = {
            "message": f"{CANARY}: refused {said} ({b64encode(said.encode()).decode()})",
            "path": ["issues" if record.op == LISTING else "issue"],
        }
        if answer["ext"] is not None:
            error["extensions"] = answer["ext"]
        body = {"errors": [error], "data": None}
        return httpx.Response(answer["status"], json=body, headers=answer["headers"])

    def _answer(self, request: httpx.Request, record) -> httpx.Response:
        if request.method != "POST" or record.url != self.base_url:
            self._violation(record, f"{request.method} to another address than base_url")
            return httpx.Response(404, text=CANARY)
        if record.auth == WRONG_KEY:  # a key Linear does not know
            return self._refused(request, record, refusal(401, {"code": "AUTHENTICATION_ERROR"}))
        if record.auth != KEY:
            self._violation(record, "the Authorization header is not the key as it is")
            return self._refused(request, record, refusal(401, {"code": "AUTHENTICATION_ERROR"}))
        if not request.headers.get("content-type", "").startswith("application/json"):
            return self._invalid(record, "the body is not sent as JSON")
        try:
            body = json.loads(request.content)
        except ValueError:
            return self._invalid(record, "the body is not JSON")
        if not isinstance(body, dict) or not isinstance(body.get("query"), str):
            return self._invalid(record, "the body has no query")
        record.query = query = body["query"]
        record.variables = variables = body.get("variables") or {}
        record.op = op = body.get("operationName")
        if not isinstance(variables, dict):
            return self._invalid(record, "variables is not an object")
        if "email" in query.lower():
            self._violation(record, "the query asks for email")
        if op not in (LISTING, COMMENTS) or not re.search(rf"\bquery\s+{op}\b", query):
            return self._invalid(record, f"operationName {op!r} is not an operation it defines")
        partial = False
        for rule in self.rules:
            if rule.op != op or rule.times == 0:
                continue
            if rule.skip:
                rule.skip -= 1
                break
            if rule.times is not None:
                rule.times -= 1
            if rule.answer == "partial":
                partial = True
                break
            return self._forced(request, record, rule.answer)
        if op == LISTING:
            return self._listing(record, query, variables, partial)
        return self._comments(record, query, variables, partial)

    def _forced(self, request: httpx.Request, record, answer: object) -> httpx.Response:
        if answer == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if answer == "not json":
            return httpx.Response(200, text=f"<html>{CANARY}</html>")
        if answer == "wrong shape":
            field_name = "issues" if record.op == LISTING else "issue"
            return httpx.Response(200, json={"data": {field_name: {"nodes": CANARY}}})
        if answer == "no data":
            return httpx.Response(200, json={"data": None})
        return self._refused(request, record, answer)  # type: ignore[arg-type]

    # Reading a query

    def _cursor(self, scope: str, offset: int) -> str:
        state = json.dumps({"g": self.gen, "o": offset, "s": scope}, sort_keys=True)
        return urlsafe_b64encode(state.encode()).decode()

    def _offset(self, after: object, scope: str) -> int | None:
        if after is None:
            return 0
        try:
            state = json.loads(urlsafe_b64decode(str(after).encode()))
        except ValueError:
            return None
        if not isinstance(state, dict) or state.get("g") != self.gen or state.get("s") != scope:
            return None  # from another pull, or for another filter or issue
        return state["o"]

    def _connection(self, nodes: list, scope: str, start: int, more: bool) -> dict:
        cursors = [self._cursor(scope, start + k + 1) for k in range(len(nodes))]
        return {
            "nodes": nodes,
            "edges": [{"node": n, "cursor": c} for n, c in zip(nodes, cursors, strict=True)],
            "pageInfo": {
                "hasNextPage": more,
                "hasPreviousPage": start > 0,
                "startCursor": cursors[0] if cursors else None,
                "endCursor": cursors[-1] if cursors else None,
            },
        }

    def _answered(self, record, query: str, data: dict, partial: bool) -> httpx.Response:
        words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query))
        body: dict = {"data": _selected(data, words)}
        if partial:
            body["errors"] = [
                {
                    "message": f"{CANARY}: one field could not be read",
                    "path": [*data, "nodes", 1],
                    "extensions": {"code": "INTERNAL_ERROR", "type": "internal error"},
                }
            ]
        return httpx.Response(200, json=body)

    # FinishedIssues

    def _listing(self, record, query: str, variables: dict, partial: bool) -> httpx.Response:
        args = _arguments(query, "issues")
        if args is None:
            return self._invalid(record, "the listing does not ask issues(...)")
        for name, wanted in (
            ("first", "25"),
            ("after", "$after"),
            ("includeArchived", "true"),
            ("orderBy", "updatedAt"),
            ("filter", "$filter"),
        ):
            if _argument(args, name) != wanted:
                self._violation(record, f"issues() is not given {name}: {wanted}")
        if re.search(r"\bcomments\b", query):
            self._violation(record, "the listing asks for comments")
        if not set(variables) <= {"filter", "after"}:
            self._violation(record, f"the listing's variables are {sorted(variables)}")
        record.filter, record.after = variables.get("filter"), variables.get("after")
        if not isinstance(record.filter, dict):
            return self._invalid(record, "the filter is not an object")
        times = _gte_times(record.filter)
        record.T = min(times) if times else None
        scope = "issues:" + json.dumps(record.filter, sort_keys=True)
        offset = self._offset(record.after, scope)
        if offset is None:
            return self._invalid(record, "a page cursor from another pull or another filter")
        archived = _argument(args, "includeArchived") == "true"
        found = [
            issue
            for issue in self.issues.values()
            if issue.visible
            and issue.team not in self.hidden_teams
            and (not issue.trashed or self.lists_trashed)
            and (issue.archived is None or archived)
            and self._matches(issue, record.filter, record)
        ]
        found.sort(key=lambda issue: (issue.updated, issue.id), reverse=True)
        if self.shuffle:
            found.sort(key=lambda issue: hashlib.sha256(f"order:{issue.id}".encode()).digest())
        start = offset - 1 if self.repeat and offset > 0 else offset
        page = found[start : start + 25]
        if self.shuffle:
            random.Random(start).shuffle(page)
        more = start + len(page) < len(found)
        if partial:
            page = page[: len(page) // 2]
        connection = self._connection([self._node(i) for i in page], scope, start, more)
        record.count, record.more = len(page), more
        record.end = connection["pageInfo"]["endCursor"]
        return self._answered(record, query, {"issues": connection}, partial)

    def _node(self, issue: Issue) -> dict:
        project = issue.project
        return {
            "id": issue.id,
            "identifier": issue.identifier,
            "number": issue.number,
            "title": issue.title,
            "description": issue.description,
            "url": issue.url,
            "createdAt": z(issue.created),
            "updatedAt": z(issue.updated),
            "completedAt": z(issue.completed) if issue.completed else None,
            "canceledAt": z(issue.canceled) if issue.canceled else None,
            "archivedAt": z(issue.archived) if issue.archived else None,
            "trashed": True if issue.trashed else None,
            "state": self._state(issue),
            "team": {"id": TEAM_IDS[issue.team], "key": issue.team, "name": TEAMS[issue.team]},
            "project": {"id": PROJECT_IDS[project], "name": self.project_names[project]}
            if project
            else None,
            "assignee": _person(issue.assignee),
            "creator": _person(issue.creator),
            "labels": {
                "nodes": [{"id": uid(f"label-{n}", 0), "name": n} for n in issue.labels],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        }

    def _state(self, issue: Issue) -> dict:
        state_id = uid(
            "state", list(STATES).index(issue.state) + 10 * list(TEAMS).index(issue.team)
        )
        return {"id": state_id, "name": issue.state, "type": STATES[issue.state]}

    def _matches(self, issue: Issue, condition: object, record) -> bool:
        """Whether `issue` passes an IssueFilter, as the API documents one."""
        if not isinstance(condition, dict):
            self._violation(record, "a filter condition that is not an object")
            return False
        for name, test in condition.items():
            if name in ("and", "or"):
                if not isinstance(test, list):
                    self._violation(record, f"{name} is not a list")
                    return False
                results = [self._matches(issue, part, record) for part in test]
                passed = all(results) if name == "and" else any(results)
            elif name in ("state", "team", "project"):
                passed = self._object_matches(self._object(issue, name), test, record)
            elif name in ("completedAt", "canceledAt", "createdAt", "updatedAt", "archivedAt"):
                value = {
                    "completedAt": issue.completed,
                    "canceledAt": issue.canceled,
                    "createdAt": issue.created,
                    "updatedAt": issue.updated,
                    "archivedAt": issue.archived,
                }[name]
                passed = self._compare(value, test, record, dates=True)
            else:
                self._violation(record, f"a filter on {name}, which the stand-in does not model")
                return False
            if not passed:
                return False
        return True

    def _object(self, issue: Issue, name: str) -> dict | None:
        if name == "state":
            return self._state(issue)
        if name == "team":
            return {"id": TEAM_IDS[issue.team], "key": issue.team, "name": TEAMS[issue.team]}
        if issue.project is None:
            return None
        return {"id": PROJECT_IDS[issue.project], "name": self.project_names[issue.project]}

    def _object_matches(self, found: dict | None, test: object, record) -> bool:
        if not isinstance(test, dict):
            self._violation(record, "an object's filter that is not an object")
            return False
        for name, comparator in test.items():
            if name == "null":
                if (found is None) != bool(comparator):
                    return False
                continue
            if name not in ("id", "key", "name", "type"):
                self._violation(record, f"a filter on {name}, which the stand-in does not model")
                return False
            if found is None or name not in found:
                return False
            if not self._compare(found[name], comparator, record, dates=False):
                return False
        return True

    def _compare(self, value: object, comparator: object, record, *, dates: bool) -> bool:
        if not isinstance(comparator, dict):
            self._violation(record, "a comparator that is not an object")
            return False

        def operand(given: object) -> object:
            if not dates:
                return given
            moment = instant(given)
            if moment is None:
                self._violation(record, f"{given!r} is not an instant with its zone")
            return moment

        for op, given in comparator.items():
            if op == "null":
                passed = (value is None) == bool(given)
            elif op in ("in", "nin"):
                if not isinstance(given, list):
                    self._violation(record, f"{op} is not a list")
                    return False
                values = [operand(g) for g in given]
                passed = value is None or value not in values
                if op == "in":
                    passed = value is not None and value in values
            elif op in ("eq", "neq", "gt", "gte", "lt", "lte"):
                other = operand(given)
                if value is None or other is None:
                    passed = False
                elif op == "eq":
                    passed = value == other
                elif op == "neq":
                    passed = value != other
                else:
                    passed = {
                        "gt": value > other,  # type: ignore[operator]
                        "gte": value >= other,  # type: ignore[operator]
                        "lt": value < other,  # type: ignore[operator]
                        "lte": value <= other,  # type: ignore[operator]
                    }[op]
            else:
                self._violation(record, f"the comparator {op}, which the stand-in does not model")
                return False
            if not passed:
                return False
        return True

    # IssueComments

    def _comments(self, record, query: str, variables: dict, partial: bool) -> httpx.Response:
        if re.search(r"\bissues\s*\(", query):
            self._violation(record, "IssueComments also lists issues")
        on_issue, on_comments = _arguments(query, "issue"), _arguments(query, "comments")
        if on_issue is None or on_comments is None:
            return self._invalid(record, "IssueComments does not ask issue(id:) { comments() }")
        if _argument(on_comments, "first") != "50":
            self._violation(record, "comments() is not given first: 50")
        issue_id = _value(_argument(on_issue, "id"), variables)
        after = _value(_argument(on_comments, "after"), variables)
        record.issue, record.after = issue_id, after
        issue = self.issues.get(str(issue_id))
        if issue is None or not issue.visible or issue.team in self.hidden_teams:
            error = {"message": "Entity not found: Issue", "extensions": {"code": "INVALID_INPUT"}}
            return httpx.Response(200, json={"errors": [error], "data": None})
        scope = f"comments:{issue.id}"
        offset = self._offset(after, scope)
        if offset is None:
            return self._invalid(record, "a page cursor from another pull or another issue")
        ordered = sorted(issue.comments, key=lambda c: (c.created, c.id), reverse=True)
        page = ordered[offset : offset + 50]
        more = offset + len(page) < len(ordered)
        if partial:
            page = page[: len(page) // 2]
        nodes = [_comment_node(c) for c in page]
        connection = self._connection(nodes, scope, offset, more)
        record.count, record.more = len(page), more
        record.end = connection["pageInfo"]["endCursor"]
        data = {"issue": {"id": issue.id, "comments": connection}}
        return self._answered(record, query, data, partial)


def _person(person: Person | None) -> dict | None:
    if person is None:
        return None
    return {"id": person.id, "name": person.name, "displayName": person.display}


def _comment_node(comment: Comment) -> dict:
    return {
        "id": comment.id,
        "body": comment.body,
        "createdAt": z(comment.created),
        "updatedAt": z(comment.created),
        "editedAt": None,
        "user": _person(comment.user),
        "botActor": comment.bot,
        "externalUser": comment.external,
        "parent": {"id": comment.parent} if comment.parent else None,
        "parentId": comment.parent,
    }


def _selected(value: object, words: set[str]) -> object:
    """What GraphQL returns of `value` to a query naming `words`: only the fields named."""
    if isinstance(value, dict):
        return {k: _selected(v, words) for k, v in value.items() if k in words}
    if isinstance(value, list):
        return [_selected(v, words) for v in value]
    return value


def _arguments(query: str, name: str) -> str | None:
    """The text inside the parentheses after field `name` in the query, or None."""
    found = re.search(rf"\b{name}\s*\(", query)
    if found is None:
        return None
    depth = 1
    for at in range(found.end(), len(query)):
        depth += {"(": 1, ")": -1}.get(query[at], 0)
        if depth == 0:
            return query[found.end() : at]
    return None


def _argument(arguments: str, name: str) -> str | None:
    """An argument's value as written: a $variable, a "string", or a word."""
    found = re.search(rf"\b{name}\s*:\s*(\$\w+|\"[^\"]*\"|[A-Za-z0-9_]+)", arguments)
    return found.group(1) if found else None


def _value(written: str | None, variables: dict) -> object:
    if written is None:
        return None
    if written.startswith("$"):
        return variables.get(written[1:])
    return written.strip('"')


def _gte_times(condition: object) -> list[datetime]:
    """Every `gte` instant on completedAt or canceledAt anywhere in a filter."""
    found = []
    if isinstance(condition, dict):
        for name, test in condition.items():
            if name in ("completedAt", "canceledAt") and isinstance(test, dict):
                moment = instant(test.get("gte"))
                if moment is not None:
                    found.append(moment)
            else:
                found += _gte_times(test)
    elif isinstance(condition, list):
        for part in condition:
            found += _gte_times(part)
    return found


def normal(value: object) -> object:
    """A filter with its lists in one order, to compare two filters that mean the same."""
    if isinstance(value, dict):
        return {k: normal(v) for k, v in value.items()}
    if isinstance(value, list):
        return sorted((normal(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


# Fixtures and helpers


@pytest.fixture(autouse=True)
def no_request_broke_the_contract():
    _MADE.clear()
    yield
    for stand_in in _MADE:
        assert stand_in.violations == [], stand_in.violations


@pytest.fixture
def kind():
    return KINDS["linear"]


@pytest.fixture
def fake():
    return FakeLinear(Clock(NOW))


def config(**settings) -> dict:
    return {"key_env": KEY_ENV, "projects": ["linear"], "base_url": BASE_URL, **settings}


def opened(kind, fake, *, since=SINCE, deadline=None, env=ENV, **settings):
    checked = kind.check(config(**settings))
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return kind.open(
        "linear", checked, http=http, env=env, clock=fake.clock, sleep=fake.sleep,
        deadline=deadline, since=since,
    )  # fmt: skip


def pull(fake, source, cursor=None):
    fake.new_pull()
    return asyncio.run(source.pull(cursor))


def reader(kind, fake, **settings):
    """Each pull through a source opened afresh, as the runner opens one for every run: what
    a pull knows of the last one is in the cursor it is given."""

    def read(cursor=None):
        return pull(fake, opened(kind, fake, **settings), cursor)

    return read


def scans(requests: list) -> list[list]:
    """The listing requests of one pull, grouped by scan: a scan starts with no `after`."""
    grouped: list[list] = []
    for request in requests:
        if request.after is None or not grouped:
            grouped.append([])
        grouped[-1].append(request)
    return grouped


def rescanned(fake, horizon: datetime) -> bool:
    """Whether this pull asked from the oldest moment a rescan reaches (or up to a day before)."""
    return any(r.T is not None and horizon - DAY <= r.T <= horizon for r in fake.listings())


def lines_with(text: str, *words: str) -> list[str]:
    return [line for line in text.splitlines() if all(w.lower() in line.lower() for w in words)]


# The kind and its config


def test_the_kind_is_linear_starts_off_is_not_withheld_and_proposes_a_project(kind):
    assert kind.starts_off is True and kind.via == "linear"
    assert kind.withheld is True and "linear" in withheld_via()  # Privacy 4: every kind
    assert kind.default_project == "linear"
    checked = kind.check({"key_env": KEY_ENV, "projects": []})
    assert kind.describe is not None
    said = kind.describe(checked)
    assert isinstance(said, str) and said.strip() and KEY not in said
    on = kind.switching_on()
    assert isinstance(on, str) and on.strip()


def test_check_fills_the_defaults_and_keeps_what_is_given(kind):
    checked = kind.check({"key_env": KEY_ENV, "projects": []})
    assert (checked["key_env"], checked["projects"]) == (KEY_ENV, [])
    assert checked["base_url"] == DEFAULT_BASE_URL
    assert checked["comments"] is True
    assert (checked["settle_minutes"], checked["lookback_days"]) == (30, 30)
    assert checked.get("teams") is None and checked.get("linear_project") is None
    assert kind.check(checked) == checked  # what is stored is checked again on switch-on

    given = {
        "key_env": KEY_ENV,
        "projects": ["linear"],
        "base_url": BASE_URL,
        "teams": ["ENG", "OPS"],
        "comments": False,
        "settle_minutes": 0,
        "lookback_days": 365,
    }
    kept = kind.check(given)
    for setting, value in given.items():
        assert kept[setting] == value, setting
    assert kind.check(kept) == kept
    with_project = kind.check(config(linear_project=PROJECT_IDS["refunds"]))
    assert with_project["linear_project"].lower() == PROJECT_IDS["refunds"].lower()
    for accepted in (
        {"teams": [f"T{n:02d}" for n in range(20)]},
        {"teams": ["ABCDEFGHIJ", "A_1", "Q"]},
        {"settle_minutes": 1440, "lookback_days": 1},
        {"base_url": "http://127.0.0.1:8123/graphql"},
        {"base_url": "http://localhost:8123/graphql"},
    ):
        assert kind.check(config(**accepted))["projects"] == ["linear"], accepted


@pytest.mark.parametrize(
    ("setting", "settings"),
    [
        ("key_env", {"key_env": None}),
        ("key_env", {"key_env": 7}),
        ("key_env", {"key_env": ""}),
        ("projects", {"projects": ["linear", "elsewhere"]}),
        ("projects", {"projects": "linear"}),
        ("teams", {"teams": []}),
        ("teams", {"teams": "ENG"}),
        ("teams", {"teams": [f"T{n:02d}" for n in range(21)]}),
        ("teams", {"teams": ["ENG-1"]}),
        ("teams", {"teams": ["ABCDEFGHIJK"]}),
        ("teams", {"teams": [""]}),
        ("teams", {"teams": [7]}),
        ("linear_project", {"linear_project": "refund-revamp"}),
        ("linear_project", {"linear_project": 7}),
        ("comments", {"comments": "yes"}),
        ("settle_minutes", {"settle_minutes": -1}),
        ("settle_minutes", {"settle_minutes": 1441}),
        ("settle_minutes", {"settle_minutes": "30"}),
        ("lookback_days", {"lookback_days": 0}),
        ("lookback_days", {"lookback_days": 366}),
        ("base_url", {"base_url": "http://linear.example.test/graphql"}),
        ("base_url", {"base_url": "ftp://linear.example.test/graphql"}),
        ("base_url", {"base_url": "https://ada:hunter2-c10@linear.example.test/graphql"}),
        ("color", {"color": "blue"}),
    ],
)
def test_check_refuses_a_bad_setting_by_its_name(kind, setting, settings):
    given = config(**settings)
    if settings.get("key_env", "") is None:
        del given["key_env"]
    with pytest.raises(ValueError) as refused:
        kind.check(given)
    assert setting in str(refused.value)
    assert "hunter2-c10" not in str(refused.value)


def test_check_refuses_both_teams_and_a_project_and_a_config_that_is_not_an_object(kind):
    with pytest.raises(ValueError) as refused:
        kind.check(config(teams=["ENG"], linear_project=PROJECT_IDS["refunds"]))
    assert "teams" in str(refused.value) or "project" in str(refused.value)
    for odd in (None, [], "key_env"):
        with pytest.raises(ValueError):
            kind.check(odd)


def test_a_credential_in_the_config_is_refused_and_never_repeated(kind, tmp_path):
    for setting, leaked in (
        ("key_env", config(key_env=TOKEN)),
        ("base_url", config(base_url=f"https://linear.example.test/{TOKEN}/graphql")),
        ("base_url", config(base_url=f"https://c10:{TOKEN}@linear.example.test/graphql")),
        (None, config(api_key=TOKEN)),
    ):
        with pytest.raises(ValueError) as refused:
            kind.check(leaked)
        said = str(refused.value)
        assert TOKEN not in said and TOKEN.lower() not in said.lower()
        if setting:
            assert setting in said

    store = Store(tmp_path / "cairn.db", clock=Clock(NOW))
    try:
        store.create_project("linear")
        with pytest.raises(ValueError):
            store.add_source("linear", "linear", config(key_env=TOKEN))
        assert store.list_sources() == []
    finally:
        store.close()
    assert TOKEN not in dump(tmp_path / "cairn.db")


def test_the_key_comes_from_the_environment_and_is_sent_as_it_is_only_to_base_url(kind, fake):
    for env in ({}, {KEY_ENV: ""}, {"ANOTHER_VARIABLE": KEY}):
        with pytest.raises(SourceError) as missing:
            opened(kind, fake, env=env)
        assert KEY_ENV in str(missing.value) and KEY not in str(missing.value)

    fake.issue(1, finished=SINCE + DAY)
    source = opened(kind, fake)
    assert KEY in source.secrets
    assert fake.numbers(pull(fake, source)) == [1]
    assert {r.op for r in fake.requests} == {LISTING, COMMENTS}
    for request in fake.requests:
        assert (request.method, request.url, request.auth) == ("POST", BASE_URL, KEY)

    fake.base_url = DEFAULT_BASE_URL
    default = kind.check({"key_env": KEY_ENV, "projects": ["linear"]})
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    plain = kind.open(
        "linear", default, http=http, env=ENV, clock=fake.clock, sleep=fake.sleep,
        deadline=None, since=SINCE,
    )  # fmt: skip
    fake.requests.clear()
    assert fake.numbers(pull(fake, plain)) == [1]
    assert fake.requests and all(r.url == DEFAULT_BASE_URL for r in fake.requests)


# What it reads


@pytest.mark.parametrize("lists_trashed", [False, True], ids=["trashed-hidden", "trashed-listed"])
def test_only_issues_completed_or_cancelled_since_it_was_switched_on_are_read(
    kind, fake, lists_trashed
):
    fake.lists_trashed = lists_trashed
    fake.issue(1, "Completed after switch-on", finished=SINCE + DAY)
    fake.issue(2, "Cancelled after switch-on", state="Won't fix", finished=SINCE + 2 * DAY)
    fake.issue(3, "Completed a minute before", finished=SINCE - MINUTE)
    old = fake.issue(4, "Completed long before, edited since", finished=SINCE - 5 * DAY)
    old.updated = NOW - 2 * HOUR
    fake.issue(5, "Being worked on", state="In Progress", finished=SINCE + DAY)
    fake.issue(6, "In the backlog", state="Backlog", finished=SINCE + DAY)
    fake.issue(7, "Marked a duplicate", state="Duplicate", finished=SINCE + 3 * DAY)
    trashed = fake.issue(8, "Trashed once finished", finished=SINCE + 4 * DAY)
    fake.trash(trashed, SINCE + 5 * DAY)
    archived = fake.issue(9, "Archived since", finished=SINCE + 5 * DAY)
    archived.archived = SINCE + 6 * DAY
    fake.issue(10, "Finished as it was switched on", finished=SINCE)
    fake.issue(11, "Waiting in triage", state="Triage", finished=SINCE + DAY)
    fake.issue(12, "To do", state="Todo", finished=SINCE + DAY)

    read = reader(kind, fake)
    first = read()
    assert fake.numbers(first) == [10, 1, 2, 9]
    assert {d.project for d in first.drafts} == {"linear"}
    fake.clock.now = NOW + DAY
    assert read(first.cursor).drafts == []


def test_the_first_listing_asks_exactly_what_the_contract_says(kind, fake):
    fake.issue(1, finished=SINCE + DAY)
    pull(fake, opened(kind, fake))
    first = fake.requests[0]
    assert first.op == LISTING
    variables = {k: v for k, v in first.variables.items() if v is not None}
    assert set(variables) == {"filter"}
    sent = first.filter["or"][0]
    t = sent.get("completedAt", sent.get("canceledAt", {})).get("gte")
    expected = {
        "state": {"type": {"in": ["completed", "canceled"]}},
        "or": [{"completedAt": {"gte": t}}, {"canceledAt": {"gte": t}}],
    }
    assert normal(variables["filter"]) == normal(expected)
    assert SINCE - DAY <= instant(t) <= SINCE  # the first pull: a fixed overlap before since
    assert "comments" not in first.query
    for request in fake.listings():
        assert normal(request.filter)["state"] == normal(expected)["state"]


@pytest.mark.parametrize("which", ["teams", "project", "neither"])
def test_teams_or_a_project_narrow_the_filter_and_what_is_written(kind, fake, which):
    fake.hidden_teams = {"SEC"}  # the key cannot see it: not an error
    fake.issue(1, "Engineering, refunds", team="ENG", project="refunds", finished=SINCE + DAY)
    fake.issue(2, "Engineering, no project", team="ENG", finished=SINCE + 2 * DAY)
    fake.issue(3, "Operations, refunds", team="OPS", project="refunds", finished=SINCE + 3 * DAY)
    fake.issue(4, "Operations, ledger", team="OPS", project="ledger", finished=SINCE + 4 * DAY)
    fake.issue(5, "Security, refunds", team="SEC", project="refunds", finished=SINCE + 5 * DAY)
    settings = {
        "teams": {"teams": ["ENG", "SEC"]},
        "project": {"linear_project": PROJECT_IDS["refunds"]},
        "neither": {},
    }[which]
    got = pull(fake, opened(kind, fake, **settings))
    assert fake.numbers(got) == {"teams": [1, 2], "project": [1, 3], "neither": [1, 2, 3, 4]}[which]
    assert fake.listings()
    for request in fake.listings():
        sent = json.dumps(request.filter)
        if which == "teams":
            assert '"ENG"' in sent and '"SEC"' in sent and '"OPS"' not in sent
        elif which == "project":
            assert PROJECT_IDS["refunds"].lower() in sent.lower()
            assert PROJECT_IDS["ledger"].lower() not in sent.lower()
        else:
            assert '"team"' not in sent and '"project"' not in sent


def test_an_issue_is_read_once_settled_with_the_comment_added_while_it_settled(kind, fake):
    fake.issue(1, "Settled", finished=NOW - 2 * HOUR)
    fresh = fake.issue(2, "Just closed", finished=NOW - MINUTE)
    read = reader(kind, fake)
    first = read()
    assert fake.numbers(first) == [1]
    fake.clock.now = NOW + 10 * MINUTE
    fake.comment(fresh, 1, "The comment that closes it.", fake.clock.now, user=CY)
    second = read(first.cursor)
    assert second.drafts == []
    assert max(r.T for r in fake.listings()) <= fresh.finished  # the mark has not passed it
    fake.clock.now = NOW + 31 * MINUTE
    third = read(second.cursor)
    assert fake.numbers(third) == [2]
    assert "The comment that closes it." in third.drafts[0].text


def test_a_settling_issue_holds_the_mark_and_every_listing_overlaps_the_last(kind, fake):
    fake.issue(1, "Settled long ago", finished=NOW - DAY)
    waiting = fake.issue(2, "A long settle", finished=NOW - 40 * MINUTE)
    settle = 10 * HOUR
    read = reader(kind, fake, settle_minutes=600)
    cursor, written, before = None, [], None
    for hour in range(16):
        fake.clock.now = NOW + hour * HOUR
        got = read(cursor)
        cursor = got.cursor
        written += [(hour, n) for n in fake.numbers(got)]
        latest = max(r.T for r in fake.listings())
        if hour <= 10:  # until it is written, every listing reaches it
            assert latest <= waiting.finished, hour
        if before is not None and hour >= 12:  # nothing waits: the mark follows the clock
            assert fake.clock.now - settle - 2 * DAY <= latest < before - settle, hour
        before = fake.clock.now
    assert written == [(0, 1), (10, 2)]  # settled at 9:20 after NOW: read at 10


def test_an_issue_is_read_once_by_id_whatever_happens_to_it_later(kind, fake):
    first_issue = fake.issue(1, "Fix the retry loop", finished=NOW - 2 * HOUR)
    read = reader(kind, fake)
    state = SimpleNamespace(cursor=None, written=Counter())

    def at(moment: datetime) -> list[int]:
        fake.clock.now = moment
        got = read(state.cursor)
        state.cursor = got.cursor
        state.written.update(fake.numbers(got))
        return fake.numbers(got)

    assert at(NOW) == [1]
    first_issue.title, first_issue.description = "Fix the retry loop (edited)", "Edited later."
    first_issue.updated = NOW + HOUR
    fake.comment(first_issue, 1, "A comment after it was written.", NOW + HOUR)
    assert at(NOW + 2 * HOUR) == []
    fake.reopen(first_issue, NOW + 3 * HOUR)
    assert at(NOW + 4 * HOUR) == []
    fake.finish(first_issue, NOW + 5 * HOUR)  # a second finish
    assert at(NOW + 6 * HOUR) == [] and at(NOW + 7 * HOUR) == []
    fake.move(first_issue, "OPS", 9, NOW + 8 * HOUR)  # a new identifier and url
    fake.finish(first_issue, NOW + 8 * HOUR, "Won't fix")  # and a third finish
    for day in range(4):  # through the daily rescans
        assert at(NOW + 9 * HOUR + day * DAY) == [], day
    fake.issue(2, "Something new", finished=fake.clock.now - HOUR)
    assert at(fake.clock.now + HOUR) == [2]
    assert state.written == Counter({1: 1, 2: 1})


def test_an_issue_the_key_sees_late_is_found_by_the_daily_rescan(kind, fake):
    since = NOW - 10 * DAY
    fake.issue(1, "Kickoff", finished=NOW - 2 * DAY)
    late = fake.issue(2, "Seen late", finished=NOW - 5 * DAY)
    late.visible = False  # its team is shared with the key two hours from now
    read = reader(kind, fake, since=since)
    cursor, written, rescans = None, {}, []
    for hour in range(61):
        fake.clock.now = NOW + hour * HOUR
        if hour == 2:
            late.visible = True
        got = read(cursor)
        cursor = got.cursor
        if hour > 0 and rescanned(fake, since):
            rescans.append(hour)
        for n in fake.numbers(got):
            written.setdefault(n, []).append(hour)
    assert written[1] == [0]
    found_by = min(hour for hour in rescans if hour >= 2)
    assert written[2] == [found_by]  # by the first rescan after it appeared, not before
    assert found_by - 2 <= 26
    assert len(rescans) >= 2 and all(b - a >= 24 for a, b in itertools.pairwise(rescans))


@pytest.mark.parametrize(
    ("since", "lookback_days"),
    [
        (NOW - 10 * DAY, 30),  # since is the later
        (NOW - 100 * DAY, 30),  # lookback_days ago is the later
    ],
)
def test_the_rescan_looks_back_lookback_days_and_never_before_since(
    kind, fake, since, lookback_days
):
    read = reader(kind, fake, since=since, lookback_days=lookback_days)
    cursor, rescans = None, 0
    for hour in range(30):
        fake.clock.now = NOW + hour * HOUR
        cursor = read(cursor).cursor
        horizon = max(since, fake.clock.now - lookback_days * DAY)
        for request in fake.listings():
            if horizon - DAY <= request.T <= horizon:
                rescans += hour > 0
            elif hour == 0:
                assert since - DAY <= request.T <= since  # the first pull: since
            else:
                assert fake.clock.now - 2 * DAY <= request.T, hour
    assert rescans >= 1


def pulled_in_modes(kind, count: int, *modes: str) -> dict:
    """The drafts of the first pulls over `count` finished issues, for each way of listing."""
    seen = {}
    for mode in modes:
        stand_in = FakeLinear(Clock(NOW))
        stand_in.shuffle = "shuffled" in mode
        stand_in.repeat = "repeated" in mode
        for n in range(1, count + 1):
            stand_in.issue(n, f"Standup {n}", finished=SINCE + n * HOUR)
        read, cursor, pulls = reader(kind, stand_in), None, []
        for step in range(4):
            stand_in.clock.now = NOW + step * HOUR
            got = read(cursor)
            cursor = got.cursor
            for scan in scans(stand_in.listings()):
                assert [r.status for r in scan] == [200] * len(scan), mode
                assert scan[-1].more is False, mode  # every page followed
                for earlier, later in itertools.pairwise(scan):
                    assert earlier.more is True and later.after == earlier.end, mode
            if mode == "documented" and step == 0:
                assert [r.count for r in scans(stand_in.listings())[0]] == [
                    min(25, count - k) for k in range(0, count, 25)
                ]
            pulls.append([(d.source_ref, d.title, d.item, d.text, d.project) for d in got.drafts])
            assert stand_in.numbers(got) == list(
                range(1 + 25 * step, min(count, 25 * step + 25) + 1)
            )
        seen[mode] = pulls
    return seen


def test_shuffled_pages_and_an_issue_on_two_pages_give_the_same_drafts_none_twice(kind):
    seen = pulled_in_modes(kind, 40, "documented", "shuffled", "repeated", "shuffled repeated")
    assert seen["documented"] == seen["shuffled"] == seen["repeated"]
    assert seen["documented"] == seen["shuffled repeated"]
    refs = [draft[0] for one_pull in seen["documented"] for draft in one_pull]
    assert len(refs) == len(set(refs)) == 40


def test_sixty_finished_issues_take_three_pulls_of_at_most_25_in_finish_order(kind):
    seen = pulled_in_modes(kind, 60, "documented")
    assert [len(one_pull) for one_pull in seen["documented"]] == [25, 25, 10, 0]


# What it writes


DESCRIPTION = (
    "## Repro\n\n1. Queue a refund of 12.50.\n2. Watch it retry *forever*.\n\n"
    "```\nretry(backoff=0)\n```\n\nSee also [the runbook](https://wiki.example.test/retries).\n"
)
NAMES = ("Ada Park", "adapark", "Cy Moss", "cymoss", "Dee Quinn", "deequinn", "Brook Lane")


def rich_issue(fake) -> tuple[Issue, list[Comment]]:
    issue = fake.issue(
        123,
        "Fix the retry loop",
        team="ENG",
        project="refunds",
        finished=datetime(2099, 3, 14, 9, 30, tzinfo=UTC),
        assignee=ADA,
        creator=CY,
        labels=["bug", "payments"],
        description=DESCRIPTION,
    )
    day12 = datetime(2099, 3, 12, tzinfo=UTC)
    day13 = datetime(2099, 3, 13, tzinfo=UTC)
    c1 = fake.comment(issue, 1, "Found it: the backoff is zero.", day12 + 10 * HOUR, user=ADA)
    c2 = fake.comment(issue, 2, "Can we cap the retries at five?", day12 + 11 * HOUR, user=CY)
    c3 = fake.comment(issue, 3, "Patching the backoff now.", day12 + 12 * HOUR, parent=c1)
    c4 = fake.comment(issue, 4, "Five it is, then.", day13 + 8 * HOUR, user=CY, parent=c2)
    bot = {
        "id": uid("bot", 1),
        "type": "integration",
        "subType": "github",
        "name": "Sentinel",
        "userDisplayName": None,
    }
    c5 = fake.comment(issue, 5, "Pull request merged into main.", day13 + 9 * HOUR, bot=bot)
    outside = {"id": uid("external", 1), "name": "Dee Quinn", "displayName": "deequinn"}
    c6 = fake.comment(
        issue, 6, "The customer confirms the fix.", day13 + 10 * HOUR, external=outside
    )
    c7 = fake.comment(issue, 7, "Imported note with nobody recorded.", day13 + 11 * HOUR, user=None)
    c8 = fake.comment(issue, 8, "Closing this out.", day13 + 12 * HOUR, user=BROOK)
    ties = [
        fake.comment(issue, 9, "First of a tie.", day13 + 13 * HOUR, user=ADA),
        fake.comment(issue, 10, "Second of a tie.", day13 + 13 * HOUR, user=ADA),
    ]
    ties.sort(key=lambda c: c.id)
    return issue, [c1, c3, c2, c4, c5, c6, c7, c8, *ties]


def frame(text: str, start: int, body: str) -> str:
    """How a comment is written, without its words: the text from the end of the comment
    before it to the end of its body, with the body, every name and every digit taken out."""
    block = text[start : text.index(body) + len(body)].replace(body, "{body}")
    for name in sorted(NAMES, key=len, reverse=True):
        block = block.replace(name, "{who}")
    return re.sub(r"\d", "", block)


def test_the_note_says_what_the_issue_was_and_what_was_said_in_order(kind, fake):
    issue, order = rich_issue(fake)
    (draft,) = pull(fake, opened(kind, fake)).drafts
    assert draft.title == "ENG-123: Fix the retry loop"
    assert draft.source_ref == issue.url
    assert draft.topic is None  # a page of its own
    assert draft.item == issue.id
    assert draft.project == "linear"

    text = draft.text
    assert text.startswith("# ") and "Fix the retry loop" in text.splitlines()[0]
    for fact, value in (
        ("team", "Engineering"),
        ("project", "Refund revamp"),
        ("state", "Shipped"),
        ("finished", "2099-03-14"),
        ("assignee", "adapark"),
        ("creator", "cymoss"),
        ("label", "bug"),
        ("label", "payments"),
    ):
        assert lines_with(text, fact, value), (fact, value)
    assert DESCRIPTION.strip() in text
    described = text.index(DESCRIPTION.strip())
    assert text.index(lines_with(text, "creator", "cymoss")[0]) < described
    (heading,) = [line for line in text.splitlines() if line.startswith("#") and "Comments" in line]
    assert described < text.index(heading)

    places = [text.index(c.body) for c in order]
    assert places == sorted(places) and places[0] > text.index(heading)
    ends = [p + len(c.body) for p, c in zip(places, order, strict=True)]
    frames = {c.id: frame(text, ends[k - 1], c.body) for k, c in enumerate(order) if k}
    _, c3, c2, c4, _, c6, c7, _, _, tie2 = order
    roots = {frames[c2.id], frames[tie2.id]}  # members' root comments, each after another
    assert frames[c3.id] not in roots and frames[c4.id] not in roots  # a reply reads as one
    assert frames[c6.id] not in roots  # an outside person reads as one
    bot_said = text[ends[3] : ends[4]]
    assert "Sentinel" in bot_said and "github" in bot_said
    assert c7.body in text
    for value in (draft.title, draft.text):
        assert "@" not in value  # no address, even where a name held one


def test_a_note_without_a_project_and_the_day_it_finished_in_utc(kind, fake):
    first = fake.issue(
        7,
        "Rotate the deploy keys",
        team="OPS",
        state="Won't fix",
        finished=datetime(2099, 3, 31, 23, 59, 59, 999000, tzinfo=UTC),
        description="Keys rotated by hand instead.",
        assignee=DEE,  # a name that is an address: not written
        creator=BROOK,
    )
    second = fake.issue(
        8, "Renew the certificates", team="OPS", finished=datetime(2099, 4, 1, tzinfo=UTC)
    )
    cancelled, renewed = pull(fake, opened(kind, fake)).drafts
    assert cancelled.title == "OPS-7: Rotate the deploy keys"
    assert (cancelled.topic, renewed.topic) == (None, None)
    assert (cancelled.item, renewed.item) == (first.id, second.id)
    assert lines_with(cancelled.text, "state", "Won't fix")
    assert lines_with(cancelled.text, "finished", "2099-03-31")
    assert lines_with(cancelled.text, "team", "Operations")
    assert not lines_with(cancelled.text, "project")
    for draft in (cancelled, renewed):
        for value in (draft.title, draft.text):
            assert "@" not in value  # no address, even where a name held one


LONG_TITLE = " ".join(f"step {tag(k)} of the quarterly reconciliation" for k in range(12))


def test_a_title_past_200_characters_is_cut_and_the_identifier_never(kind, fake):
    assert len(LONG_TITLE) > 400
    issue = fake.issue(3, LONG_TITLE, finished=SINCE + DAY)
    (draft,) = pull(fake, opened(kind, fake)).drafts
    assert draft.title.startswith("ENG-3: ") and len(draft.title) <= 200
    kept = draft.title.removeprefix("ENG-3: ").rstrip("\u2026. ")
    assert len(kept) > 100 and LONG_TITLE.startswith(kept)  # the start of the issue's title
    assert (draft.topic, draft.item) == (None, issue.id)


def test_an_issue_with_nothing_but_facts_is_written(kind, fake):
    fake.issue(5, "Bump the version", creator=None, finished=SINCE + DAY)
    (draft,) = pull(fake, opened(kind, fake)).drafts
    assert draft.title == "ENG-5: Bump the version"
    assert draft.text.startswith("# ")
    assert lines_with(draft.text, "team", "Engineering")
    assert lines_with(draft.text, "state", "Shipped")
    assert lines_with(draft.text, "finished", (SINCE + DAY).strftime("%Y-%m-%d"))
    assert not lines_with(draft.text, "assignee") and not lines_with(draft.text, "creator")


def test_120_comments_are_read_in_three_requests_and_written_oldest_first(kind, fake):
    issue = fake.issue(1, "A long thread", finished=SINCE + 5 * DAY)
    bodies = [f"Remark {tag(k)}." for k in range(120)]
    for k, body in enumerate(bodies):
        fake.comment(issue, k, body, SINCE + k * MINUTE, user=[ADA, CY][k % 2])
    (draft,) = pull(fake, opened(kind, fake)).drafts
    reads = fake.comment_reads()
    assert len(reads) == 3 and {r.issue for r in reads} == {issue.id}
    places = [draft.text.index(body) for body in bodies]
    assert places == sorted(places)

    fake.issues.clear()
    issue = fake.issue(2, "Comments off", finished=SINCE + 5 * DAY)
    fake.comment(issue, 500, "Not to be read.", SINCE + DAY)
    (draft,) = pull(fake, opened(kind, fake, comments=False)).drafts
    assert fake.comment_reads() == []
    assert "Not to be read." not in draft.text


def test_an_issue_with_more_comments_than_the_bound_keeps_the_first_and_says_how_many_left(
    kind, fake
):
    issue = fake.issue(1, "A very long thread", finished=SINCE + 5 * DAY)
    total = 1_000
    bodies = [f"Remark {tag(k)} on the ledger." for k in range(total)]
    for k, body in enumerate(bodies):
        fake.comment(issue, k, body, SINCE + k * MINUTE, user=ADA)
    (draft,) = pull(fake, opened(kind, fake)).drafts
    kept = [k for k, body in enumerate(bodies) if body in draft.text]
    assert 120 <= len(kept) < total
    assert kept == list(range(len(kept)))  # the first, in order
    places = [draft.text.index(bodies[k]) for k in kept]
    assert places == sorted(places)
    after = draft.text[places[-1] + len(bodies[kept[-1]]) :]
    assert re.search(rf"(?<![\d,]){total - len(kept)}(?![\d,])", after)  # how many left out


def paragraphs(word: str, count: int) -> list[str]:
    return [f"{word} {tag(k)}: " + " ".join(["lorem ipsum"] * 18) + "." for k in range(count)]


def cut_after(text: str, said: list[str]) -> tuple[int, str]:
    """How many of `said` the text keeps (they must be the first), and the first line with
    words after the last of them."""
    kept = [k for k, p in enumerate(said) if p.split(":")[0] + ":" in text]
    assert kept == list(range(len(kept)))
    last = text.index(said[kept[-1]].split(":")[0] + ":")
    rest = [line.strip() for line in text[last:].splitlines()[1:] if line.strip()]
    if rest and any(p.startswith(rest[0]) for p in said):  # cut inside the next paragraph
        rest = rest[1:]
    return len(kept), rest[0] if rest else ""


def test_a_description_longer_than_a_note_is_cut_with_a_line_saying_so(kind, fake):
    said = paragraphs("Paragraph", 3_000)
    fake.issue(1, "A huge description", description="\n\n".join(said), finished=SINCE + DAY)
    (draft,) = pull(fake, opened(kind, fake, comments=False)).drafts
    kept, next_line = cut_after(draft.text, said)
    assert 1 <= kept < 2_000
    assert next_line and not any(next_line in p for p in said)


def test_a_comment_longer_than_a_note_is_cut_with_a_line_saying_so(kind, fake):
    issue = fake.issue(1, "A huge comment", description="Short.", finished=SINCE + DAY)
    said = paragraphs("Section", 3_000)
    fake.comment(issue, 1, "\n\n".join(said), SINCE + HOUR, user=ADA)
    fake.comment(issue, 2, "After the long one.", SINCE + 2 * HOUR, user=CY)
    (draft,) = pull(fake, opened(kind, fake)).drafts
    kept, next_line = cut_after(draft.text, said)
    assert 1 <= kept < 2_000
    assert next_line and not any(next_line in p for p in said)
    assert "After the long one." not in next_line and "cymoss" not in next_line
    assert "After the long one." in draft.text


# Together with the framework


@pytest.fixture
def world(tmp_path, fake):
    clock = Clock(SINCE - DAY)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    for name in ("linear", "elsewhere"):
        store.create_project(name)
    http = source_client(httpx.MockTransport(fake.handler))
    yield SimpleNamespace(store=store, db=db, clock=clock, http=http)
    store.close()


def switched_on(world, **settings) -> None:
    world.store.add_source("linear", "linear", config(projects=[], **settings))
    world.clock.now = SINCE
    world.store.enable_source("linear", projects=["linear"])


def run(world, fake, at: datetime):
    fake.clock.now = at
    fake.new_pull()
    return asyncio.run(
        run_source(
            world.store, "linear", http=world.http, env=ENV, clock=fake.clock, sleep=fake.sleep
        )
    )


def notes(world, project: str = "linear") -> list:
    return world.store.list_notes(world.store.get_project(project).id)


def written(world, fake) -> list[int]:
    return [fake.number_of(c.source_ref) for c in notes(world)]


def pages_of(world, note) -> set[str]:
    """The pages whose claims cite the note's spans: where its note went."""
    spans = {span.id for span in world.store.note_spans(note.id)}
    project = world.store.get_project("linear").id
    return {
        page.slug
        for page in world.store.list_pages(project)
        if any(spans & set(claim.span_ids) for claim in page.claims)
    }


def nothing_holds_the_key(world, caplog, *raised: BaseException) -> None:
    stored = dump(world.db)
    assert not holds_secret(stored, (KEY,))
    for event in world.store.list_audit(limit=200):
        assert not holds_secret(event.detail, (KEY,))
    assert not holds_secret(caplog.text, (KEY,))
    for error in raised:
        assert not holds_secret(str(error), (KEY,))


def test_through_the_runner_it_is_off_until_switched_on_then_writes_each_issue_once(
    world, fake, caplog
):
    caplog.set_level(logging.DEBUG)
    fake.issue(1, "Before it was on", finished=SINCE - HOUR)
    first = fake.issue(2, "Fix the retry loop", project="refunds", finished=NOW - 2 * DAY)
    fake.issue(3, "Rotate the deploy keys", team="OPS", finished=SINCE + 2 * DAY)
    fake.issue(4, "Still settling", finished=NOW - 5 * MINUTE)

    added = world.store.add_source("linear", "linear", config(projects=[]))
    assert added.enabled is False
    assert run(world, fake, NOW).off is True and fake.requests == []

    world.clock.now = SINCE
    world.store.enable_source("linear", projects=["linear"])
    report = run(world, fake, NOW)
    assert (report.saved, report.unchanged, report.refused, report.rejected) == (2, 0, 0, 0)
    assert written(world, fake) == [3, 2]  # oldest finish first
    assert {c.via for c in notes(world)} == {"linear"}
    assert notes(world, "elsewhere") == []
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind) == ("source.synced", "system")
    assert (event.detail["source"], event.detail["saved"]) == ("linear", 2)

    again = run(world, fake, NOW + 10 * MINUTE)
    assert (again.saved, again.unchanged) == (0, 0)
    later = run(world, fake, NOW + HOUR)  # the fourth has settled
    assert (later.saved, later.unchanged) == (1, 0) and written(world, fake) == [3, 2, 4]

    # Changed, switched off and on, reopened, moved and finished again: nothing more
    first.title, first.description = "Fix the retry loop for good", "Rewritten."
    fake.comment(first, 1, "Reopening: it came back.", NOW + 2 * HOUR, user=CY)
    world.clock.now = NOW + 2 * HOUR
    world.store.disable_source("linear")
    assert run(world, fake, NOW + 3 * HOUR).off is True
    fake.reopen(first, NOW + 3 * HOUR)
    world.clock.now = NOW + 4 * HOUR
    world.store.enable_source("linear")
    fake.move(first, "OPS", 12, NOW + 5 * HOUR)
    fake.finish(first, NOW + 5 * HOUR)
    for at in (NOW + 6 * HOUR, NOW + 2 * DAY, NOW + 3 * DAY):
        report = run(world, fake, at)
        assert (report.saved, report.unchanged) == (0, 0), at
    assert written(world, fake) == [3, 2, 4]
    nothing_holds_the_key(world, caplog)


def test_through_the_runner_each_issue_has_a_page_of_its_own_however_long_its_title(world, fake):
    switched_on(world)
    march = datetime(2099, 3, 20, tzinfo=UTC)
    fake.issue(1, "Close March's books", project="ledger", finished=march)
    fake.issue(2, "Reconcile the refunds", project="ledger", finished=march + 5 * DAY)
    fake.issue(3, LONG_TITLE, project="ledger", finished=march + 7 * DAY)
    report = run(world, fake, NOW)
    assert (report.saved, report.malformed) == (3, 0) and written(world, fake) == [1, 2, 3]
    one, two, three = notes(world)
    assert (one.title, two.title) == ("ENG-1: Close March's books", "ENG-2: Reconcile the refunds")
    assert (one.topic, two.topic, three.topic) == (None, None, None)
    assert three.title.startswith("ENG-3: ")  # the identifier whole at the start
    pages = [pages_of(world, note) for note in (one, two, three)]
    assert [len(page) for page in pages] == [1, 1, 1]  # one project, one month: three pages
    assert len(set().union(*pages)) == 3
    for note in (one, two, three):
        assert lines_with(note.text, "project", "Ledger audit")  # the project is a fact


def test_through_the_runner_an_issue_finished_again_after_its_digest_is_gone_is_not_written_again(
    world, fake
):
    switched_on(world, lookback_days=7)
    issue = fake.issue(1, "Fix the retry loop", finished=NOW - 2 * HOUR)
    report = run(world, fake, NOW)
    assert (report.saved, report.unchanged) == (1, 0)
    (note,) = notes(world)
    fake.reopen(issue, NOW + HOUR)
    for day in range(1, 12):  # well past lookback_days: the kind's digest is gone
        report = run(world, fake, NOW + day * DAY)
        assert (report.saved, report.unchanged) == (0, 0), day
    # Changed, moved to another team (a new identifier and url) and finished again
    issue.title, issue.description = "Fix the retry loop, again", "It came back in the spring."
    fake.comment(issue, 1, "Reopened: the backoff is zero again.", NOW + 11 * DAY, user=CY)
    fake.move(issue, "OPS", 9, NOW + 12 * DAY)
    fake.finish(issue, NOW + 12 * DAY)
    report = run(world, fake, NOW + 12 * DAY + HOUR)
    # proposed again by the kind, and not written: the source wrote this item before
    assert (report.saved, report.unchanged, report.refused, report.rejected) == (0, 1, 0, 0)
    assert [stored.id for stored in notes(world)] == [note.id]
    later = run(world, fake, NOW + 13 * DAY + HOUR)
    assert later.saved == 0 and [stored.id for stored in notes(world)] == [note.id]


@pytest.fixture
def running(world, fake):
    """A source switched on that has written issue 1, and three more issues waiting."""
    switched_on(world)
    fake.issue(1, "Kickoff", finished=SINCE + DAY)
    assert run(world, fake, NOW).saved == 1
    for n in (2, 3, 4):
        made = fake.issue(n, f"Follow-up {n}", finished=NOW + n * MINUTE)
        for k in range(4):
            fake.comment(made, 10 * n + k, f"Note {tag(10 * n + k)} on {n}.", NOW + n * MINUTE)
    return world.store.get_source("linear")


def go_on(world, fake, at: datetime) -> None:
    """The next run, with Linear answering again, writes what was left, and nothing twice."""
    fake.rules.clear()
    run(world, fake, at)
    assert written(world, fake) == [1, 2, 3, 4]
    assert run(world, fake, at + HOUR).saved == 0
    for note in notes(world)[1:]:
        n = fake.number_of(note.source_ref)
        assert all(f"Note {tag(10 * n + k)} on {n}." in note.text for k in range(4))


AUTH_REFUSED = {
    "401": refusal(401, text="Unauthorized"),
    "403": refusal(403, {"code": "FORBIDDEN"}),
    "code": refusal(200, {"code": "AUTHENTICATION_ERROR"}),
    "type": refusal(400, {"type": "authentication error"}),
    "forbidden type": refusal(200, {"type": "forbidden"}),
    "any case": refusal(200, {"code": "Authentication_Error"}),
}


@pytest.mark.parametrize("answer", AUTH_REFUSED.values(), ids=AUTH_REFUSED.keys())
@pytest.mark.parametrize("op", [LISTING, COMMENTS])
def test_a_refused_key_fails_the_run_naming_the_variable_and_never_the_key(
    world, fake, running, caplog, op, answer
):
    caplog.set_level(logging.DEBUG)
    fake.fail(op, answer, times=None)
    with pytest.raises(SourceError) as failed:
        run(world, fake, NOW + HOUR)
    source = world.store.get_source("linear")
    assert KEY_ENV in str(failed.value) and KEY_ENV in source.last_error
    assert source.cursor == running.cursor and written(world, fake) == [1]
    nothing_holds_the_key(world, caplog, failed.value)
    go_on(world, fake, NOW + 2 * HOUR)


def test_a_key_linear_does_not_know_fails_the_pull_naming_the_variable(kind, fake):
    fake.issue(1, finished=SINCE + DAY)
    with pytest.raises(SourceError) as failed:
        pull(fake, opened(kind, fake, env={KEY_ENV: WRONG_KEY}))
    said = str(failed.value)
    assert KEY_ENV in said and WRONG_KEY not in said and not holds_secret(said, (WRONG_KEY,))


RATE_LIMITED = {
    "429": refusal(429, {"code": "RATELIMITED"}, {"retry-after": "120"}),
    "429 bare": refusal(429, headers={"retry-after": "120"}, text="Too many requests"),
    "400 code": refusal(400, {"code": "RATELIMITED"}),
    "200 type": refusal(200, {"type": "ratelimited"}),
}


@pytest.mark.parametrize("answer", RATE_LIMITED.values(), ids=RATE_LIMITED.keys())
def test_a_rate_limit_while_reading_comments_stops_the_pull_and_keeps_what_was_finished(
    world, fake, running, caplog, answer
):
    caplog.set_level(logging.DEBUG)
    fake.fail(COMMENTS, answer, skip=2)  # the comments of the third issue
    report = run(world, fake, NOW + HOUR)  # the run does not fail
    assert report.saved == 2 and written(world, fake) == [1, 2, 3]
    assert fake.of_pull()[-1].status == answer["status"]  # nothing asked after it
    assert fake.slept == []
    assert world.store.get_source("linear").last_error is None
    nothing_holds_the_key(world, caplog)
    go_on(world, fake, NOW + 2 * HOUR)
    assert fake.slept == []


@pytest.mark.parametrize("answer", RATE_LIMITED.values(), ids=RATE_LIMITED.keys())
def test_a_rate_limited_listing_writes_nothing_and_moves_no_cursor(
    world, fake, running, caplog, answer
):
    caplog.set_level(logging.DEBUG)
    fake.fail(LISTING, answer)
    try:
        run(world, fake, NOW + HOUR)
    except SourceError as failed:  # when nothing was finished, it may fail
        assert LISTING in str(failed) and LISTING in world.store.get_source("linear").last_error
        nothing_holds_the_key(world, caplog, failed)
    assert len(fake.of_pull()) == 1 and fake.slept == []
    assert world.store.get_source("linear").cursor == running.cursor
    assert written(world, fake) == [1]
    nothing_holds_the_key(world, caplog)
    go_on(world, fake, NOW + 2 * HOUR)


def test_a_partial_listing_is_a_failure_and_nothing_of_it_is_written(world, fake, running):
    fake.fail(LISTING, "partial")
    try:
        run(world, fake, NOW + HOUR)
    except SourceError as failed:
        assert LISTING in str(failed) and not holds_secret(str(failed), (KEY,))
    assert written(world, fake) == [1]
    assert world.store.get_source("linear").cursor == running.cursor
    go_on(world, fake, NOW + 2 * HOUR)


def test_partial_comments_are_a_failure_and_that_issue_is_written_whole_later(world, fake, running):
    fake.fail(COMMENTS, "partial", skip=1)  # the comments of the second issue
    try:
        run(world, fake, NOW + HOUR)
    except SourceError as failed:
        assert COMMENTS in str(failed) and not holds_secret(str(failed), (KEY,))
    assert 3 not in written(world, fake)
    go_on(world, fake, NOW + 2 * HOUR)  # issue 3 then holds all four of its comments


FAILURES = {
    "500": refusal(500, {"code": "INTERNAL_ERROR"}),
    "503 html": refusal(503, text="<html>Service unavailable</html>"),
    "timeout": "timeout",
    "not json": "not json",
    "wrong shape": "wrong shape",
    "no data": "no data",
}


@pytest.mark.parametrize("answer", FAILURES.values(), ids=FAILURES.keys())
@pytest.mark.parametrize("op", [LISTING, COMMENTS])
def test_a_server_error_a_timeout_or_a_malformed_answer_fails_the_run_and_moves_no_cursor(
    world, fake, running, caplog, op, answer
):
    caplog.set_level(logging.DEBUG)
    fake.fail(op, answer, skip=1 if op == COMMENTS else 0)
    with pytest.raises(SourceError) as failed:
        run(world, fake, NOW + HOUR)
    source = world.store.get_source("linear")
    assert op in str(failed.value) and op in source.last_error
    assert source.cursor == running.cursor and written(world, fake) == [1]
    nothing_holds_the_key(world, caplog, failed.value)
    go_on(world, fake, NOW + 2 * HOUR)
    assert world.store.get_source("linear").last_error is None


# The cursor


def test_the_cursor_holds_no_id_identifier_title_or_key_and_stays_bounded(kind, fake):
    read = reader(kind, fake, since=NOW, lookback_days=7, comments=False)
    spacing = 400 * DAY / 1_000
    cursor, counted, lengths, added = None, Counter(), {}, 0
    for day in range(1, 402):
        fake.clock.now = NOW + day * DAY
        while added < 1_000 and NOW + spacing * (added + 1) <= fake.clock.now - HOUR:
            added += 1
            fake.issue(added, f"Quill review {tag(added)}", finished=NOW + spacing * added)
        got = read(cursor)
        counted.update(fake.numbers(got))
        cursor = got.cursor
        json.loads(cursor)  # opaque, but JSON
        shown = cursor.lower()
        assert KEY.lower() not in shown
        for issue in fake.issues.values():
            assert issue.id.lower() not in shown and issue.id.replace("-", "") not in shown
            assert issue.identifier.lower() not in shown and issue.title not in cursor
        if day == 60:
            lengths["two months"] = len(cursor)
    assert added == 1_000 and counted == Counter({n: 1 for n in range(1, 1_001)})
    assert len(cursor) <= 1.5 * lengths["two months"] + 200  # it did not grow with the issues
    assert fake.clock.now - max(r.T for r in fake.listings()) < 2 * DAY
