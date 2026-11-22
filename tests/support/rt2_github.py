"""A stand-in GitHub for job `release`'s step "Create the release", kept as a small state machine,
and the harness that runs the step's real shell against it, request by request.

Why another stand-in. Six reviews refused this step, each for a fault one step past the last fix:
a published release deleted by the CLI, a draft deleted, an edited draft published, and then
comparisons as stale as GitHub's listing. Each fault sat between two requests, inside one
command or across a re-run. So this model works at the level of HTTP requests, not commands, and
an outside schedule decides, before every request, whether a person acts and how the request
ends. The checker in test_rt2_explore.py enumerates those schedules.

GitHub, as modelled
- Releases with an id, a tag name, draft and pre-release flags, a title, notes, and files with
  bytes and an upload state (`uploaded`, or `starter` for an upload that was cut). Git tags,
  each naming a commit.
- The truth is a log of writes. A read by id (`GET /releases/{id}`) and an asset's bytes read
  the truth. The listing (`GET /releases?per_page=N`, one request a page) reads a view: the
  writes it shows are those made at least `lag[kind]` listing requests before it, separately for
  each kind of write (the step's creation, its uploads, its publication, a person's write). The
  re-run of a job comes later, and by default the listing has caught up by then
  (`persist_lag` keeps it behind across attempts). `bytag="listing"` makes the two lookups the
  CLI does by tag (`releases/tags/{tag}`, and the GraphQL `release(tagName:)`) read the view too.
- Publishing a draft whose git tag is gone makes the tag on the default branch's head, as GitHub
  does for a release whose tag does not exist (`target_commitish`).
- Switches for what was not measured: a second published release for one tag is refused (422)
  by default, a second draft accepted; a file whose upload was cut downloads as 404.

The GitHub CLI, as read at v2.102.0
- `gh api`: GET unless --method, or POST when fields are given; --paginate follows pages and
  runs --jq on each page; on a non-2xx it prints the error body to standard output without the
  filter and `gh: <message> (HTTP <status>)` on standard error, and exits 1
  (pkg/cmd/api/api.go, processResponse, 493 to 566).
- `gh release create --draft --verify-tag`: the GraphQL ref lookup, one POST, then one upload
  request per file, each sent again up to three times after a network error or a 5xx; a second
  send of a file that arrived gets 422 (name taken) and is final. Asked for a draft, it never
  publishes and never deletes (create/create.go 498 to 560, shared/upload.go 111 to 155).
- `gh release download <tag>`: two lookups at once, by tag for a published release and by the
  GraphQL `release(tagName:)` then by id for a draft; the first that answers without an error
  wins (shared/fetch.go 182 to 222); then one GET per file, refusing a file already on disk
  (download/download.go 160 to 250, 439 to 455).

Anything else the step asks of `gh` is answered with exit 99 and recorded, so a test fails
loudly where the model has a gap. Made-up repository, run, commits and files.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import CodeType
from urllib.parse import parse_qs, urlsplit

import yaml

from support.paths import REPO_ROOT

REPO_ROOT = REPO_ROOT
SYSTEM = "/usr/bin:/bin"
RUN_ID = "4242"
RUN_SHA = "a1" * 20
HEAD_SHA = "b2" * 20
OTHER_SHA = "c3" * 20
GH_REPO = "wren/ledger"
MARK = f"<!-- cairn-ci-cd run {RUN_ID} -->"
TAGS = {"release": "v1.2.3", "candidate": "v1.2.3-rc.1"}
VERSION = "1.2.3"
WHEEL = f"cairn_memory-{VERSION}-py3-none-any.whl"
SDIST = f"cairn_memory-{VERSION}.tar.gz"
SUMS = "SHA256SUMS"
KINDS = ("create", "upload", "publish", "person")


def notes_text(tag: str) -> str:
    return f"## {VERSION} - 2026-11-20\n\n- the notes of {tag}\n"


def body_text(tag: str) -> str:
    return f"{notes_text(tag)}\n{MARK}\n"


def run_files(build: int = 1) -> dict[str, bytes]:
    """The three files a run attested; `build` 2 is package re-run after the artifact expired."""
    files = {
        WHEEL: f"wheel bytes, build {build}\n".encode(),
        SDIST: f"source bytes, build {build}\n".encode(),
    }
    files[SUMS] = f"sums over build {build}\n".encode()
    return files


def step_run(workflow_text: str) -> str:
    jobs = yaml.safe_load(workflow_text)["jobs"]
    (step,) = [s for s in jobs["release"]["steps"] if s.get("name") == "Create the release"]
    return step["run"]


def current_run() -> str:
    return step_run((REPO_ROOT / ".github" / "workflows" / "ci-cd.yml").read_text())


def pinned_run(commit: str) -> str:
    """The step as it stood at COMMIT: its text, kept beside the tests
    (tests/fixtures/release_step_COMMIT.txt). It was read from this repository's history, which
    a checkout need not hold: GitHub's own run of this file had no such commit and failed
    there. Where the history is at hand, test_the_pinned_step_is_the_step_of_its_commit
    holds the file to it."""
    return (REPO_ROOT / "tests" / "fixtures" / f"release_step_{commit}.txt").read_text(
        encoding="utf-8"
    )


def step_in_history(commit: str) -> str | None:
    """The step as this repository's history has it at COMMIT, or None where the checkout
    does not hold that commit (a shallow one, an archive)."""
    shown = subprocess.run(  # noqa: UP022 (the keyword's name is not written here)
        ["git", "-C", str(REPO_ROOT), "show", f"{commit}:.github/workflows/ci-cd.yml"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return step_run(shown.stdout) if shown.returncode == 0 else None


# --- the outside schedule -------------------------------------------------------------------------


class Scheduler:
    """Decides every choice point of one execution. PLAN maps a point's index to the
    alternative taken; any other point takes alternative 0, the default (succeed, nobody acts,
    the published lookup answers first). Each point is recorded, with its alternatives, so an
    explorer can branch on it."""

    def __init__(self, plan: dict[int, int] | None = None, rng=None, rate: float = 0.0):
        self.plan = dict(plan or {})
        self.points: list[tuple[str, list[str]]] = []
        self.taken: list[int] = []
        self.rng = rng
        self.rate = rate

    def choose(self, label: str, options: list[str]) -> int:
        index = len(self.points)
        self.points.append((label, list(options)))
        if index in self.plan:
            pick = self.plan[index]
        elif self.rng is not None and len(options) > 1 and self.rng.random() < self.rate:
            pick = self.rng.randrange(1, len(options))
            self.plan[index] = pick
        else:
            pick = 0
        pick = min(pick, len(options) - 1)
        self.taken.append(pick)
        return pick


class Script(Scheduler):
    """A schedule written as words: each (point, option) pair is taken at the first choice point
    after the previous one whose label POINT matches (a regular expression, searched) and
    that offers an option containing
    OPTION; every other point takes the default. A third element N takes the N-th such point
    (from 1). For the tests that replay a found trace."""

    def __init__(self, steps: list[tuple]):
        super().__init__()
        self.steps = [(*s, 1) if len(s) == 2 else tuple(s) for s in steps]

    def choose(self, label: str, options: list[str]) -> int:
        index = len(self.points)
        self.points.append((label, list(options)))
        pick = 0
        if self.steps:
            point, option, nth = self.steps[0]
            hits = [i for i, o in enumerate(options) if option in o]
            if re.search(point, label) and hits:
                if nth > 1:
                    self.steps[0] = (point, option, nth - 1)
                else:
                    pick = hits[0]
                    self.steps.pop(0)
                    self.plan[index] = pick
        self.taken.append(pick)
        return pick


# --- the state machine ----------------------------------------------------------------------------


def new_state(tag: str) -> dict:
    return {"releases": {}, "tags": {tag: RUN_SHA}, "latest": None}


def apply(state: dict, op: tuple) -> None:
    """One write. A write to a release that is not there (in a view, one not shown yet) is
    skipped."""
    what = op[0]
    releases = state["releases"]
    if what == "create":
        _, rid, fields = op
        releases[rid] = copy.deepcopy(fields)
        return
    if what == "tag":
        _, tag, sha = op
        if sha is None:
            state["tags"].pop(tag, None)
        else:
            state["tags"][tag] = sha
        return
    if what == "latest":
        state["latest"] = op[1]
        return
    if what == "replace":  # a whole attempt replayed from the explorer's memory
        state.clear()
        state.update(copy.deepcopy(op[1]))
        return
    rid = op[1]
    release = releases.get(rid)
    if release is None:
        return
    if what == "set":
        release.update(copy.deepcopy(op[2]))
    elif what == "asset_add":
        release["assets"] = [a for a in release["assets"] if a["id"] != op[2]["id"]]
        release["assets"].append(copy.deepcopy(op[2]))
    elif what == "asset_del":
        release["assets"] = [a for a in release["assets"] if a["id"] != op[2]]
    elif what == "delete":
        del releases[rid]
    else:  # pragma: no cover - a write the model does not know
        raise AssertionError(f"unknown write {op!r}")


@dataclass
class Write:
    seq: int
    kind: str
    look: int  # listing requests issued before it
    op: tuple
    by: str  # "step" or "person"
    caught: bool = False


@dataclass
class Event:
    """One thing that happened: a request of the step, a person's act, a sleep, an exit."""

    seq: int
    attempt: int
    what: str  # request | person | sleep | exit | follow
    detail: str
    kind: str = ""  # listing | byid | bytag | graphql | asset | create | upload | patch | ref
    rid: int | None = None
    fate: str = "ok"
    took_effect: bool = False
    truth_before: dict | None = None
    truth_after: dict | None = None
    shown: object = None  # what a read answered
    others: tuple = ()  # a listing: the other tags' published stable releases it showed
    command: int = 0  # which gh call of the attempt


@dataclass
class Config:
    tag: str = TAGS["release"]
    lag: dict = field(default_factory=lambda: dict.fromkeys(KINDS, 0))
    persist_lag: bool = False
    bytag: str = "current"  # or "listing"
    unique_published: bool = True
    draft_dup_allowed: bool = True
    starter_download: str = "404"
    attempts: int = 3
    per_page: int | None = None  # None: the page size the step asks for
    others: int = 0  # releases of other tags, older than the run's
    follow_advice: bool = True
    rebuild_between: bool = False  # package re-run with new bytes before the 2nd attempt
    person_menu: str = "full"  # or "small", or "core"
    gh_limit: int = 120
    annotated: bool = False  # the run's tag is an annotated tag (an object naming the commit)
    existing: tuple = ()  # stable versions published (by earlier runs) before the step starts
    # stable versions a person may publish by hand at any gap (job `release` runs one at a time
    # across the repository, its concurrency group, so no other tag's job publishes meanwhile)
    by_hand: tuple = ()

    @property
    def candidate(self) -> bool:
        return "-rc." in self.tag


class Unmodelled(Exception):
    pass


class Hung(Exception):
    """An attempt that did not end within ATTEMPT_SECONDS: the step looped, or the model did."""


ATTEMPT_SECONDS = int(os.environ.get("RT2_ATTEMPT_SECONDS", 60))


def stable_key(tag: str):
    m = re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag)
    return tuple(int(x) for x in m.groups()) if m else None


def highest_stable(state: dict):
    """The id of the highest stable version among the published releases: where GitHub's Latest
    belongs, by the ordering job `aliases` uses."""
    best = None
    for rid, r in state["releases"].items():
        key = stable_key(r["tag_name"])
        if r["draft"] or r["prerelease"] or key is None:
            continue
        if best is None or key > best[0]:
            best = (key, rid)
    return None if best is None else best[1]


def published_elsewhere(rid: int, version: str) -> dict:
    return {
        "id": rid,
        "tag_name": version,
        "draft": False,
        "prerelease": False,
        "name": version,
        "body": f"the notes of {version}\n",
        "assets": [],
        "target_commitish": OTHER_SHA,
    }


class Hub:
    """GitHub and the gh CLI, as the step meets them, plus the outside actor."""

    def __init__(self, config: Config, scheduler: Scheduler):
        self.config = config
        self.s = scheduler
        self.tag = config.tag
        self.initial = new_state(self.tag)
        self.writes: list[Write] = []
        self.events: list[Event] = []
        self.listings = 0
        self.next_id = 88
        self.next_asset = 500
        self.attempt = 0
        self.command = 0
        self.unmodelled: list[str] = []
        self.files = run_files(1)
        self._truth = new_state(self.tag)
        for n in range(config.others):
            rid = 10 + n
            self._truth["releases"][rid] = {
                "id": rid,
                "tag_name": f"v0.{n}.0",
                "draft": False,
                "prerelease": False,
                "name": f"v0.{n}.0",
                "body": "older notes\n",
                "assets": [],
                "target_commitish": "main",
            }
        for n, version in enumerate(config.existing):
            rid = 40 + n
            self._truth["releases"][rid] = published_elsewhere(rid, version)
            self._truth["tags"][version] = OTHER_SHA
        self._truth["latest"] = highest_stable(self._truth)
        self.next_other = 60
        self.initial = copy.deepcopy(self._truth)

    # -- truth and views --

    @property
    def truth(self) -> dict:
        return self._truth

    def write(self, kind: str, op: tuple, by: str) -> None:
        w = Write(len(self.writes), kind, self.listings, op, by)
        self.writes.append(w)
        apply(self._truth, op)

    def view(self, at: int | None = None) -> dict:
        at = self.listings if at is None else at
        state = copy.deepcopy(self.initial)
        for w in self.writes:
            if w.caught or w.op[0] == "tag" or at - w.look >= self.config.lag[w.kind]:
                apply(state, w.op)
        return state

    def catch_up(self) -> None:
        for w in self.writes:
            w.caught = True

    def releases_for(self, state: dict, tag: str | None = None) -> list[dict]:
        tag = self.tag if tag is None else tag
        return [r for _, r in sorted(state["releases"].items()) if r["tag_name"] == tag]

    def snap(self) -> dict:
        return copy.deepcopy(self._truth)

    # -- events --

    def event(self, what: str, detail: str, **kw) -> Event:
        e = Event(len(self.events), self.attempt, what, detail, command=self.command, **kw)
        self.events.append(e)
        return e

    # -- the outside actor --

    def person_options(self) -> list[tuple[str, object]]:
        """What a person may do now, on the releases page or to the tag."""
        options: list[tuple[str, object]] = [("nobody acts", None)]
        # "small" leaves out the rarer acts; "core" is "small" with the acts on the git tag
        small = self.config.person_menu in ("small", "core")
        tag_acts = self.config.person_menu in ("full", "core")
        for r in self.releases_for(self._truth):
            rid = r["id"]
            name = f"release {rid}"
            options.append((f"edits the title of {name}", ("set", rid, {"name": "Spring"})))
            options.append(
                (
                    f"edits the notes of {name}, keeping the mark",
                    ("set", rid, {"body": (r["body"] or "").replace("the notes", "other notes")}),
                )
            )
            wheel = [a for a in r["assets"] if a["name"] == WHEEL]
            if wheel:
                aid = self.next_asset
                options.append(
                    (
                        f"replaces the wheel of {name}",
                        [
                            ("asset_del", rid, wheel[0]["id"]),
                            (
                                "asset_add",
                                rid,
                                {
                                    "id": aid,
                                    "name": WHEEL,
                                    # the same size: only the bytes differ
                                    "data": b"wheel bytes, build Z\n",
                                    "state": "uploaded",
                                },
                            ),
                        ],
                    )
                )
                if not small:
                    options.append(
                        (f"removes the wheel of {name}", ("asset_del", rid, wheel[0]["id"]))
                    )
            if r["draft"]:
                options.append((f"publishes {name}", ("set", rid, {"draft": False})))
            elif not small:
                options.append((f"turns {name} back into a draft", ("set", rid, {"draft": True})))
            options.append((f"deletes {name}", ("delete", rid)))
            if not small:
                options.append(
                    (
                        f"adds a file to {name}",
                        (
                            "asset_add",
                            rid,
                            {
                                "id": self.next_asset,
                                "name": "extra.txt",
                                "data": b"extra\n",
                                "state": "uploaded",
                            },
                        ),
                    )
                )
                options.append(
                    (
                        f"flips the pre-release flag of {name}",
                        ("set", rid, {"prerelease": not r["prerelease"]}),
                    )
                )
        options.append(("makes a second release for the tag, a draft", "second-draft"))
        for version in self.config.by_hand:
            if not any(r["tag_name"] == version for r in self._truth["releases"].values()):
                options.append((f"publishes {version} by hand", f"other:{version}"))
        if not small:
            options.append(("makes a second release for the tag, published", "second-public"))
        if tag_acts and self.tag in self._truth["tags"]:
            # GitHub turns a published release whose tag is deleted back into a draft
            # (assumed); a draft keeps its tag name, which is only a string
            options.append(
                (
                    "deletes the git tag",
                    [("tag", self.tag, None)]
                    + [
                        ("set", r["id"], {"draft": True})
                        for r in self.releases_for(self._truth)
                        if not r["draft"]
                    ],
                )
            )
            options.append(
                (
                    "moves the git tag to another commit (a forced push)",
                    ("tag", self.tag, OTHER_SHA),
                )
            )
        return options

    def person_acts(self, label: str) -> None:
        options = self.person_options()
        pick = self.s.choose(f"person, {label}", [name for name, _ in options])
        if pick == 0:
            return
        name, act = options[pick]
        before = self.snap()
        rid = None
        if isinstance(act, str) and act.startswith("other:"):
            # a release of another tag published by hand, made Latest when it is the highest
            # (a careful person; GitHub's own default for a release newly published is Latest)
            version = act.split(":", 1)[1]
            rid = self.next_other
            self.next_other += 1
            self.write("person", ("tag", version, OTHER_SHA), "person")
            self.write("person", ("create", rid, published_elsewhere(rid, version)), "person")
            if highest_stable(self._truth) == rid:
                self.write("person", ("latest", rid), "person")
        elif act in ("second-draft", "second-public"):
            rid = self.next_id
            self.next_id += 1
            fields = {
                "id": rid,
                "tag_name": self.tag,
                "draft": act == "second-draft",
                "prerelease": self.config.candidate,
                "name": "by hand",
                "body": "written by hand\n",
                "assets": [],
                "target_commitish": "main",
            }
            self.write("person", ("create", rid, fields), "person")
            if act == "second-public" and not fields["prerelease"]:
                self.write("person", ("latest", rid), "person")
        else:
            ops = act if isinstance(act, list) else [act]
            for op in ops:
                if op[0] == "asset_add":
                    self.next_asset += 1
                rid = op[1] if op[0] != "tag" else None
                kind = "person"
                self.write(kind, op, "person")
            r = self._truth["releases"].get(rid) if rid is not None else None
            was = before["releases"].get(rid) if rid is not None else None
            published = r is not None and was is not None and was["draft"] and not r["draft"]
            if published and not r["prerelease"]:  # by hand: make_latest's default, true
                self.write("person", ("latest", rid), "person")
        self.event(
            "person", f"a person {name}", rid=rid, truth_before=before, truth_after=self.snap()
        )

    # -- requests --

    def request(self, kind: str, detail: str, write: bool, rid=None, menu=None) -> str:
        """The gap before a request (a person may act), then how the request ends."""
        self.person_acts(f"before {detail}")
        if menu is None:
            menu = ["ok", "fails, not done", "done, reply lost"] if write else ["ok", "fails"]
        pick = self.s.choose(f"fate of {detail}", menu)
        return ["ok", "fail", "lost", "cut"][pick] if write else ["ok", "fail"][pick]

    # -- gh --

    def call(self, prog: str, cwd: str, args: list[str], env_tmp: str) -> tuple[int, str, str]:
        if prog == "sleep":
            self.event("sleep", f"sleep {' '.join(args)}")
            return 0, "", ""
        self.command += 1
        if self.command > self.config.gh_limit:
            self.unmodelled.append("the step called gh more often than the limit")
            return 97, "", "too many calls\n"
        try:
            if args[:1] == ["api"]:
                return self.api(args[1:])
            if args[:2] == ["release", "create"]:
                return self.create(cwd, args[2:])
            if args[:2] == ["release", "download"]:
                return self.download(args[2:])
            if args == ["--version"]:
                return 0, "gh version 2.102.0 (2026-11-01)\n", ""
            raise Unmodelled(f"gh {' '.join(args)}")
        except Unmodelled as gap:
            self.unmodelled.append(str(gap))
            return 99, "", f"the model does not know: {gap}\n"

    # gh api

    def api(self, args: list[str]) -> tuple[int, str, str]:
        method, path, jq, paginate, silent, fields = None, None, None, False, False, {}
        accept = None
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("--method", "-X"):
                method, i = args[i + 1], i + 2
            elif a in ("--jq", "-q"):
                jq, i = args[i + 1], i + 2
            elif a == "--paginate":
                paginate, i = True, i + 1
            elif a == "--silent":
                silent, i = True, i + 1
            elif a in ("-F", "--field", "-f", "--raw-field"):
                key, _, value = args[i + 1].partition("=")
                if a in ("-F", "--field"):
                    value = {"true": True, "false": False}.get(value, value)
                fields[key], i = value, i + 2
            elif a in ("-H", "--header"):
                key, _, value = args[i + 1].partition(":")
                if key.strip().lower() != "accept":
                    raise Unmodelled(f"gh api header {args[i + 1]}")
                accept, i = value.strip(), i + 2
            elif a == "--allow-escape-sequences":  # matters only on a terminal
                i += 1
            elif a.startswith("-"):
                raise Unmodelled(f"gh api option {a}")
            else:
                if path is not None:
                    raise Unmodelled(f"gh api with two paths: {path} {a}")
                path, i = a, i + 1
        method = (method or ("POST" if fields else "GET")).upper()
        parts = urlsplit(path or "")
        segments = parts.path.strip("/").split("/")
        if (
            method == "GET"
            and segments[:5] == ["repos", *GH_REPO.split("/"), "commits", "tags"]
            and len(segments) == 6
            and accept == "application/vnd.github.sha"
            and jq is None
        ):
            return self.tag_read(segments[5])
        if (
            method == "GET"
            and segments[:5] == ["repos", *GH_REPO.split("/"), "releases", "assets"]
            and len(segments) == 6
            and segments[5].isdigit()
            and accept == "application/octet-stream"
            and jq is None
        ):
            return self.asset(int(segments[5]))
        if accept is not None:
            raise Unmodelled(f"gh api {method} {path} with Accept {accept}")
        base = ["repos", *GH_REPO.split("/"), "releases"]
        if segments[:4] != base:
            raise Unmodelled(f"gh api {method} {path}")
        rest = segments[4:]
        if method == "GET" and rest == [] and paginate:
            per_page = int(parse_qs(parts.query).get("per_page", ["30"])[0])
            return self.listing(per_page, jq)
        if method == "GET" and len(rest) == 1 and rest[0].isdigit() and not paginate:
            return self.by_id(int(rest[0]), jq)
        if method == "PATCH" and len(rest) == 1 and rest[0].isdigit():
            return self.patch(int(rest[0]), fields, silent)
        raise Unmodelled(f"gh api {method} {path} {'--paginate' if paginate else ''}")

    def to_json(self, r: dict) -> dict:
        return {
            "id": r["id"],
            "tag_name": r["tag_name"],
            "name": r["name"],
            "body": r["body"],
            "draft": r["draft"],
            "prerelease": r["prerelease"],
            "target_commitish": r.get("target_commitish", "main"),
            "assets": [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "state": a["state"],
                    "size": a.get("size", len(a["data"])),
                    # a replacement is a new asset; an asset never changes in place here
                    "updated_at": f"2026-11-20T10:{a['id'] % 60:02d}:00Z",
                }
                for a in r["assets"]
            ],
        }

    def listing(self, per_page: int, jq: str | None) -> tuple[int, str, str]:
        per_page = self.config.per_page or per_page
        out, page = [], 0
        while True:
            fate = self.request("listing", f"GET releases page {page + 1}", write=False)
            before = self.snap()
            if fate == "fail":
                self.listings += 1
                self.event(
                    "request",
                    f"GET releases page {page + 1}: 502",
                    kind="listing",
                    fate=fate,
                    truth_before=before,
                )
                body = "".join(out) + '{"message":"Server Error"}'
                return 1, body, "gh: Server Error (HTTP 502)\n"
            view = self.view()
            self.listings += 1
            ordered = [self.to_json(r) for _, r in sorted(view["releases"].items(), reverse=True)]
            chunk = ordered[page * per_page : (page + 1) * per_page]
            shown = [r for r in chunk if r["tag_name"] == self.tag]
            others = tuple(
                r["tag_name"]
                for r in chunk
                if r["tag_name"] != self.tag and not r["draft"] and not r["prerelease"]
            )
            self.event(
                "request",
                f"GET releases page {page + 1}",
                kind="listing",
                truth_before=before,
                shown=shown,
                others=others,
            )
            out.append(jq_eval(jq, json.dumps(chunk)) if jq else json.dumps(chunk))
            if len(ordered) <= (page + 1) * per_page:
                break
            page += 1
        return 0, "".join(out), ""

    def by_id(self, rid: int, jq: str | None) -> tuple[int, str, str]:
        fate = self.request("byid", f"GET releases/{rid}", write=False, rid=rid)
        before = self.snap()
        if fate == "fail":
            self.event(
                "request",
                f"GET releases/{rid}: 502",
                kind="byid",
                rid=rid,
                fate=fate,
                truth_before=before,
            )
            return 1, '{"message":"Server Error"}', "gh: Server Error (HTTP 502)\n"
        r = self._truth["releases"].get(rid)
        if r is None:
            self.event(
                "request",
                f"GET releases/{rid}: 404",
                kind="byid",
                rid=rid,
                truth_before=before,
                shown=None,
            )
            return (1, '{"message":"Not Found","status":"404"}', "gh: Not Found (HTTP 404)\n")
        shown = self.to_json(r)
        self.event(
            "request", f"GET releases/{rid}", kind="byid", rid=rid, truth_before=before, shown=shown
        )
        return 0, (jq_eval(jq, json.dumps(shown)) if jq else json.dumps(shown)), ""

    def tag_read(self, tag: str) -> tuple[int, str, str]:
        """GET repos/R/commits/tags/T with Accept application/vnd.github.sha: the commit the tag
        names, as plain text with no newline. GitHub resolves the ref, so an annotated tag gives
        its commit, not the tag object; a tag that is gone gives 422."""
        fate = self.request("tagread", f"GET commits/tags/{tag}", write=False)
        if fate == "fail":
            self.event(
                "request",
                f"GET commits/tags/{tag}: 502",
                kind="tag",
                fate=fate,
                truth_before=self.snap(),
            )
            url = f"https://api.github.com/repos/{GH_REPO}/commits/tags/{tag}"
            return 1, "", f"gh: HTTP 502: Bad Gateway ({url})\n"
        sha = self._truth["tags"].get(tag)
        kind = "annotated" if self.config.annotated else "lightweight"
        self.event(
            "request",
            f"GET commits/tags/{tag} ({kind}): {sha or '422'}",
            kind="tag",
            fate=fate,
            shown=sha,
            truth_before=self.snap(),
        )
        if sha is None:
            body = (
                '{"message":"No commit found for SHA: tags/' + tag + '","documentation_url":'
                '"https://docs.github.com/rest/commits/commits#get-a-commit","status":"422"}'
            )
            return 1, body, f"gh: No commit found for SHA: tags/{tag} (HTTP 422)\n"
        return 0, sha, ""

    def asset(self, aid: int) -> tuple[int, str, str]:
        """GET repos/R/releases/assets/ID with Accept application/octet-stream: the file's bytes
        (GitHub redirects to the storage and gh follows), or 404 for an asset that is gone or
        whose upload never finished."""
        owner = next(
            (
                rid
                for rid, r in self._truth["releases"].items()
                for a in r["assets"]
                if a["id"] == aid
            ),
            None,
        )
        fate = self.request("asset", f"GET asset {aid}", write=False, rid=owner)
        owner = next(
            (
                rid
                for rid, r in self._truth["releases"].items()
                for a in r["assets"]
                if a["id"] == aid
            ),
            None,
        )
        live = (
            None
            if owner is None
            else next(a for a in self._truth["releases"][owner]["assets"] if a["id"] == aid)
        )
        ok = (
            fate == "ok"
            and live is not None
            and not (live["state"] == "starter" and self.config.starter_download == "404")
        )
        name = live["name"] if live is not None else f"asset {aid}"
        self.event(
            "request",
            f"GET asset {aid} ({name}) of {owner}" + ("" if ok else ": failed"),
            kind="asset",
            rid=owner,
            fate=fate,
            truth_before=self.snap(),
            shown=(name, live["data"] if ok else None),
        )
        if fate == "fail":
            return 1, "", "gh: HTTP 502: Bad Gateway\n"
        if not ok:
            body = '{"message":"Not Found","status":"404"}'
            return 1, body, "gh: Not Found (HTTP 404)\n"
        return 0, live["data"].decode(), ""

    def patch(self, rid: int, fields: dict, silent: bool) -> tuple[int, str, str]:
        fate = self.request("patch", f"PATCH releases/{rid} {fields}", write=True, rid=rid)
        before = self.snap()
        if fate == "fail":
            self.event(
                "request",
                f"PATCH releases/{rid}: 502, not done",
                kind="patch",
                rid=rid,
                fate=fate,
                truth_before=before,
                shown=fields,
            )
            return 1, "", "gh: Server Error (HTTP 502)\n"
        r = self._truth["releases"].get(rid)
        if r is None:
            self.event(
                "request",
                f"PATCH releases/{rid}: 404",
                kind="patch",
                rid=rid,
                truth_before=before,
                shown=fields,
            )
            return 1, "", "gh: Not Found (HTTP 404)\n"
        publishing = fields.get("draft") is False and r["draft"]
        others = self.releases_for(self._truth, r["tag_name"])
        if (
            publishing
            and self.config.unique_published
            and any(o["id"] != rid and not o["draft"] for o in others)
        ):
            self.event(
                "request",
                f"PATCH releases/{rid}: 422 tag taken",
                kind="patch",
                rid=rid,
                truth_before=before,
                shown=fields,
            )
            return 1, "", "gh: Validation Failed (HTTP 422)\n"
        change = {
            k: v
            for k, v in fields.items()
            if k in ("draft", "prerelease", "name", "body", "tag_name")
        }
        self.write("publish", ("set", rid, change), "step")
        if publishing and r["tag_name"] not in self._truth["tags"]:
            # GitHub makes a missing tag from target_commitish: a commit, or a branch's head
            target = r.get("target_commitish") or "main"
            sha = target if re.fullmatch(r"[0-9a-f]{40}", target) else HEAD_SHA
            self.write("publish", ("tag", r["tag_name"], sha), "step")
        # make_latest (REST docs): "true" makes it Latest, "false" leaves Latest where it is,
        # and left out it defaults to true for a release newly published
        latest = fields.get("make_latest", "true" if publishing else None)
        now = self._truth["releases"].get(rid)
        if latest not in (None, "true", True, "false", False):
            raise Unmodelled(f"make_latest={latest}")
        # drafts and pre-releases cannot be Latest
        if latest in ("true", True) and now is not None and not now["draft"] | now["prerelease"]:
            self.write("publish", ("latest", rid), "step")
        self.event(
            "request",
            f"PATCH releases/{rid} {fields}" + (": done, reply lost" if fate == "lost" else ""),
            kind="patch",
            rid=rid,
            fate=fate,
            took_effect=True,
            truth_before=before,
            truth_after=self.snap(),
            shown=fields,
        )
        if fate == "lost":
            return 1, "", "Patch: read tcp: connection reset by peer\n"
        return 0, "", ""

    # gh release create

    def create(self, cwd: str, args: list[str]) -> tuple[int, str, str]:
        tag, rest = args[0], args[1:]
        flags = {"--draft": False, "--verify-tag": False, "--prerelease": False}
        title = notes_file = target = None
        files = []
        i = 0
        while i < len(rest):
            a = rest[i]
            if a in flags:
                flags[a], i = True, i + 1
            elif a == "--title":
                title, i = rest[i + 1], i + 2
            elif a == "--notes-file":
                notes_file, i = rest[i + 1], i + 2
            elif a == "--target":  # gh sends it as target_commitish
                target, i = rest[i + 1], i + 2
            elif a.startswith("--latest="):
                i += 1  # make_latest on a draft: GitHub uses it only once it is published
            elif a.startswith("-"):
                raise Unmodelled(f"gh release create option {a}")
            else:
                files.append(rest[i])
                i += 1
        if not flags["--draft"]:
            raise Unmodelled("gh release create without --draft (it would publish and clean up)")
        if flags["--verify-tag"]:
            fate = self.request("ref", f"GraphQL ref refs/tags/{tag}", write=False)
            if fate == "fail":
                self.event("request", "GraphQL ref: 502", kind="ref", fate=fate)
                return 1, "", "HTTP 502: Server Error (https://api.github.com/graphql)\n"
            exists = tag in self._truth["tags"]
            self.event("request", f"GraphQL ref refs/tags/{tag}", kind="ref", shown=exists)
            if not exists:
                return 1, "", f"tag {tag} doesn't exist in the repo {GH_REPO}, aborting\n"
        body = Path(notes_file).read_bytes().decode() if notes_file else ""
        fate = self.request("create", f"POST releases (draft of {tag})", write=True)
        before = self.snap()
        if fate == "fail":
            self.event(
                "request",
                "POST releases: 502, not done",
                kind="create",
                fate=fate,
                truth_before=before,
            )
            return 1, "", "HTTP 502: Server Error (https://api.github.com/repos/.../releases)\n"
        if not self.config.draft_dup_allowed and self.releases_for(self._truth, tag):
            self.event("request", "POST releases: 422", kind="create", truth_before=before)
            return 1, "", "HTTP 422: Validation Failed\n"
        rid = self.next_id
        self.next_id += 1
        fields = {
            "id": rid,
            "tag_name": tag,
            "draft": True,
            "prerelease": flags["--prerelease"],
            "name": title or None,
            "body": body or None,
            "assets": [],
            "target_commitish": target or "main",
        }
        self.write("create", ("create", rid, fields), "step")
        self.event(
            "request",
            f"POST releases: draft {rid}" + (", reply lost" if fate == "lost" else ""),
            kind="create",
            rid=rid,
            fate=fate,
            took_effect=True,
            truth_before=before,
            truth_after=self.snap(),
        )
        if fate == "lost":
            return 1, "", "Post: read tcp: connection reset by peer\n"
        failed = None
        for name in files:
            data = (Path(cwd) / name).read_bytes()
            error = self.upload(rid, Path(name).name, data)
            failed = failed or error
        if failed:
            return 1, "", failed
        return 0, f"https://github.com/{GH_REPO}/releases/tag/untagged-{rid}\n", ""

    def upload(self, rid: int, name: str, data: bytes) -> str | None:
        for send in range(4):
            menu = ["ok", "fails, not done", "done, reply lost", "cut, left unfinished"]
            fate = self.request(
                "upload",
                f"upload {name} to {rid} (send {send + 1})",
                write=True,
                rid=rid,
                menu=menu,
            )
            before = self.snap()
            r = self._truth["releases"].get(rid)
            if r is None:
                self.event(
                    "request", f"upload {name}: 404", kind="upload", rid=rid, truth_before=before
                )
                return "HTTP 404: Not Found (uploads)\n"
            if fate == "fail":
                self.event(
                    "request",
                    f"upload {name}: 502, not done",
                    kind="upload",
                    rid=rid,
                    fate=fate,
                    truth_before=before,
                )
                continue
            if any(a["name"] == name for a in r["assets"]):
                self.event(
                    "request",
                    f"upload {name}: 422 name taken",
                    kind="upload",
                    rid=rid,
                    truth_before=before,
                )
                return "HTTP 422: Validation Failed (already_exists)\n"
            aid = self.next_asset
            self.next_asset += 1
            state = "starter" if fate == "cut" else "uploaded"
            stored = data[: len(data) // 2] if fate == "cut" else data
            self.write(
                "upload",
                (
                    "asset_add",
                    rid,
                    {"id": aid, "name": name, "data": stored, "state": state, "size": len(data)},
                ),
                "step",
            )
            self.event(
                "request",
                f"upload {name} to {rid}: {fate}",
                kind="upload",
                rid=rid,
                fate=fate,
                took_effect=True,
                truth_before=before,
                truth_after=self.snap(),
            )
            if fate == "ok":
                return None
            # lost or cut: a network error, sent again
        return "upload failed after four sends\n"

    # gh release download

    def lookup_state(self) -> dict:
        return self.view(self.listings) if self.config.bytag == "listing" else self._truth

    def download(self, args: list[str]) -> tuple[int, str, str]:
        tag, rest = args[0], args[1:]
        if rest[:1] != ["--dir"] or len(rest) != 2:
            raise Unmodelled(f"gh release download {' '.join(args)}")
        dest = Path(rest[1])
        # the two lookups, in either order
        found = {}
        fate = self.request("bytag", f"GET releases/tags/{tag}", write=False)
        state = self.lookup_state()
        pub = [r for r in self.releases_for(state, tag) if not r["draft"]]
        found["published"] = None if fate == "fail" or not pub else pub[-1]["id"]
        self.event(
            "request",
            f"GET releases/tags/{tag}" + (": 502" if fate == "fail" else ""),
            kind="bytag",
            fate=fate,
            rid=found["published"],
        )
        fate = self.request("graphql", f"GraphQL release(tagName: {tag})", write=False)
        state = self.lookup_state()
        candidates = self.releases_for(state, tag)
        public = [r for r in candidates if not r["draft"]]
        pick = (public or candidates or [None])[-1]
        draft_id = pick["id"] if pick is not None and pick["draft"] else None
        if fate == "fail":
            draft_id = None
        if draft_id is not None:
            fate2 = self.request("byid", f"GET releases/{draft_id} (draft lookup)", write=False)
            if fate2 == "fail" or draft_id not in self._truth["releases"]:
                draft_id = None
        self.event(
            "request",
            f"GraphQL release(tagName: {tag}) then by id",
            kind="graphql",
            fate=fate,
            rid=draft_id,
        )
        found["draft"] = draft_id
        both = [k for k in ("published", "draft") if found[k] is not None]
        if not both:
            return 1, "", "release not found\n"
        first = both[0]
        if len(both) == 2:
            first = both[self.s.choose("which lookup answers first", both)]
        rid = found[first]
        r = self._truth["releases"].get(rid)
        if r is None:
            return 1, "", "release not found\n"
        assets = copy.deepcopy(r["assets"])
        self.event(
            "request", f"download picks release {rid} ({first} lookup)", kind="pick", rid=rid
        )
        if not assets:
            return 1, "", "no assets to download\n"
        dest.mkdir(parents=True, exist_ok=True)
        error = None
        for a in assets:
            if (dest / a["name"]).exists():
                error = f"{dest / a['name']} already exists\n"
                continue
            fate = self.request(
                "asset", f"GET asset {a['id']} ({a['name']}) of {rid}", write=False, rid=rid
            )
            now = self._truth["releases"].get(rid)
            live = (
                None
                if now is None
                else next((x for x in now["assets"] if x["id"] == a["id"]), None)
            )
            ok = (
                fate == "ok"
                and live is not None
                and not (live["state"] == "starter" and self.config.starter_download == "404")
            )
            self.event(
                "request",
                f"GET asset {a['id']} ({a['name']}) of {rid}" + ("" if ok else ": failed"),
                kind="asset",
                rid=rid,
                fate=fate,
                truth_before=self.snap(),
                shown=(a["name"], live["data"] if ok else None),
            )
            if not ok:
                error = f"HTTP 404: Not Found ({a['name']})\n"
                continue
            (dest / a["name"]).write_bytes(live["data"])
        return (1, "", error) if error else (0, "", "")


_JQ_CACHE: dict[tuple[str, str], str] = {}


def jq_eval(program: str, data: str) -> str:
    """The step's own filter, run by jq (the CLI runs gojq; for these filters they agree)."""
    key = (program, data)
    if key not in _JQ_CACHE:
        done = subprocess.run(  # noqa: UP022 (the keyword's name is not written here)
            ["jq", "-r", program],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            raise Unmodelled(f"jq failed on the step's filter: {done.stderr.strip()}")
        _JQ_CACHE[key] = done.stdout
    return _JQ_CACHE[key]


# --- running the real shell -----------------------------------------------------------------------

CLIENT = r"""#!/bin/bash
d="$RT2_DIR"
printf '%s\0' "$(($# + 2))" "${0##*/}" "$PWD" "$@" > "$d/req"
IFS= read -r code < "$d/resp"
[ ! -s "$d/out" ] || cat "$d/out"
[ ! -s "$d/err" ] || cat "$d/err" >&2
exit "$code"
"""


# The same client as shell functions, put before the step's text so that a call to gh or sleep
# forks no process of its own (the explorer runs thousands of attempts on a loaded machine). The
# step's text is unchanged; test_the_two_clients_give_the_same_execution holds them equal.
PRELUDE = r"""__rt2_call() {
  local __c="" __o="" __e=""
  printf '%s\0' "$(($# + 1))" "$1" "$PWD" "${@:2}" > "$RT2_DIR/req"
  IFS= read -r __c < "$RT2_DIR/resp" || true
  if [ -s "$RT2_DIR/out" ]; then
    IFS= read -r -d '' __o < "$RT2_DIR/out" || true; printf '%s' "$__o"
  fi
  if [ -s "$RT2_DIR/err" ]; then
    IFS= read -r -d '' __e < "$RT2_DIR/err" || true; printf '%s' "$__e" >&2
  fi
  return "$__c"
}
gh() { __rt2_call gh "$@"; }
sleep() { __rt2_call sleep "$@"; }
"""


@dataclass
class Attempt:
    number: int
    code: int
    stdout: str
    stderr: str
    errors: list[str]


_PY_HEREDOC_RE = re.compile(r"\A\s*python3\s+-I\s+-\s+<<'PY'\n(.*)\nPY\s*\Z", re.DOTALL)
_COMPILED_RELEASE_CACHE: dict[str, CodeType] = {}


def _run_python_inprocess(py_source: str, hub: Hub, files: dict[str, bytes]) -> Attempt:
    """Execute inline `python3 -I` release step in-process against HUB with no subprocess fork."""
    import textwrap

    code_obj = _COMPILED_RELEASE_CACHE.get(py_source)
    if code_obj is None:
        code_obj = compile(textwrap.dedent(py_source), "<release-step>", "exec")
        _COMPILED_RELEASE_CACHE[py_source] = code_obj

    work = Path(tempfile.mkdtemp(prefix="rt2-"))
    try:
        temp = work / "temp"
        release = temp / "release"
        release.mkdir(parents=True)
        (release / "notes.md").write_text(notes_text(hub.tag))
        for name, data in files.items():
            (release / name).write_bytes(data)
        env = {
            "PATH": SYSTEM,
            "RUNNER_TEMP": str(temp),
            "GITHUB_REF_NAME": hub.tag,
            "GITHUB_RUN_ID": RUN_ID,
            "GITHUB_SHA": RUN_SHA,
            "CANDIDATE": "true" if hub.config.candidate else "",
            "GH_TOKEN": "a made-up token",
            "GH_REPO": GH_REPO,
            "LC_ALL": "C",
        }
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        saved_env = os.environ.copy()
        saved_cwd = os.getcwd()
        saved_stdout = sys.stdout
        saved_stderr = sys.stderr
        saved_subproc_run = subprocess.run
        saved_sleep = time.sleep
        exit_code = 0

        def fake_run(cmd, *args, **kwargs):
            if not isinstance(cmd, (list, tuple)) or not cmd or str(cmd[0]) != "gh":
                raise RuntimeError(f"unmodelled subprocess in inprocess runner: {cmd!r}")
            rc, out_str, err_str = hub.call(
                "gh", str(Path.cwd()), [str(x) for x in cmd[1:]], str(temp)
            )
            is_text = bool(
                kwargs.get("text")
                or kwargs.get("universal_newlines")
                or kwargs.get("encoding")
                or kwargs.get("errors")
            )
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=rc,
                stdout=out_str if is_text else out_str.encode("utf-8"),
                stderr=err_str if is_text else err_str.encode("utf-8"),
            )

        def fake_sleep(_secs: float) -> None:
            # `python3 -I` calls stdlib time.sleep natively (never PATH/sleep), so sleep does
            # not emit a hub event on the `path` runner either; skip wall-clock waiting here.
            return None

        try:
            os.environ.clear()
            os.environ.update(env)
            os.chdir(release)
            sys.stdout = out_buf
            sys.stderr = err_buf
            subprocess.run = fake_run
            time.sleep = fake_sleep
            try:
                exec(code_obj, {"__name__": "__main__"})
            except SystemExit as exc:
                if exc.code is None:
                    exit_code = 0
                elif isinstance(exc.code, int):
                    exit_code = exc.code
                else:
                    exit_code = 1
        finally:
            time.sleep = saved_sleep
            subprocess.run = saved_subproc_run
            sys.stdout = saved_stdout
            sys.stderr = saved_stderr
            os.chdir(saved_cwd)
            os.environ.clear()
            os.environ.update(saved_env)

        out = out_buf.getvalue()
        err = err_buf.getvalue()
        errors = [line for line in out.splitlines() if line.startswith("::error::")]
        return Attempt(hub.attempt, exit_code, out, err, errors)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_attempt(run: str, hub: Hub, files: dict[str, bytes], client: str = "inprocess") -> Attempt:
    """One attempt of the step on a new runner, its gh and sleep served by HUB.

    Executes the inline `python3 -I` release step in-process when `client` is `"inprocess"` or
    `"function"`, or falls back to `/bin/bash` + FIFOs for shell scripts / `client="path"`.
    """
    if client in ("inprocess", "function") and (m := _PY_HEREDOC_RE.fullmatch(run)):
        return _run_python_inprocess(m.group(1), hub, files)
    work = Path(tempfile.mkdtemp(prefix="rt2-"))
    try:
        temp = work / "temp"
        release = temp / "release"
        release.mkdir(parents=True)
        (release / "notes.md").write_text(notes_text(hub.tag))
        for name, data in files.items():
            (release / name).write_bytes(data)
        bin_ = work / "bin"
        bin_.mkdir()
        for prog in ("gh", "sleep"):
            (bin_ / prog).write_text(CLIENT)
            (bin_ / prog).chmod(0o755)
        os.mkfifo(work / "req")
        os.mkfifo(work / "resp")
        env = {
            "PATH": f"{bin_}:{SYSTEM}",
            "RUNNER_TEMP": str(temp),
            "GITHUB_REF_NAME": hub.tag,
            "GITHUB_RUN_ID": RUN_ID,
            "GITHUB_SHA": RUN_SHA,
            "CANDIDATE": "true" if hub.config.candidate else "",
            "GH_TOKEN": "a made-up token",
            "GH_REPO": GH_REPO,
            "RT2_DIR": str(work),
            "LC_ALL": "C",
        }
        problems: list[str] = []
        # Requests arrive on one stream: the count of fields, then the fields, each ended by
        # NUL. The harness holds a writer of its own open, so the stream never reaches an end
        # between two requests, and a client's open of it never waits; the end of the attempt
        # is a request too. (Reading each request to its end of file hung, rarely, on a loaded
        # machine: a read that never saw the end, with no writer left.)
        reader = os.open(work / "req", os.O_RDONLY | os.O_NONBLOCK)
        writer = os.open(work / "req", os.O_WRONLY)
        os.set_blocking(reader, True)
        pending = bytearray()

        def field() -> str:
            while b"\0" not in pending:
                chunk = os.read(reader, 65536)
                if not chunk:
                    raise EOFError("the request stream ended")
                pending.extend(chunk)
            end = pending.index(b"\0")
            text = bytes(pending[:end]).decode()
            del pending[: end + 1]
            return text

        def serve():
            while True:
                parts = [field() for _ in range(int(field()))]
                if parts[0] == "__end__":
                    return
                prog, cwd, *args = parts
                try:
                    code, out, err = hub.call(prog, cwd, args, str(temp))
                except Exception as error:  # the model failed: answer, and say so
                    problems.append(repr(error))
                    code, out, err = 98, "", f"model error {error!r}\n"
                (work / "out").write_text(out)
                (work / "err").write_text(err)
                try:
                    with open(work / "resp", "w") as f:
                        f.write(f"{code}\n")
                except OSError as error:  # the client was killed
                    problems.append(f"the answer was not read: {error!r}")

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        proc = subprocess.Popen(
            [
                "/bin/bash",
                "-e",
                "-c",
                (PRELUDE if client in ("inprocess", "function") else "") + run,
            ],
            cwd=release,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=ATTEMPT_SECONDS)
        except subprocess.TimeoutExpired as error:
            frame = sys._current_frames().get(server.ident)
            stack = "".join(traceback.format_stack(frame)) if frame else "(server gone)"
            last = "\n".join(f"A{e.attempt} {e.what}: {e.detail}" for e in hub.events[-8:])
            text = f"the attempt did not end; model at:\n{stack}\nlast events:\n{last}"
            raise Hung(text) from error
        finally:
            if proc.poll() is None:
                # the whole group: a subshell of the step keeps the output pipes open
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
            os.write(writer, b"1\0__end__\0")
            # a server waiting to hand an answer to a killed client gets a reader
            poke = os.open(work / "resp", os.O_RDONLY | os.O_NONBLOCK)
            server.join(timeout=10)
            os.close(poke)
            os.close(writer)
            os.close(reader)
        if problems:
            hub.unmodelled.extend(problems)
        errors = [line for line in out.splitlines() if line.startswith("::error::")]
        return Attempt(hub.attempt, proc.returncode, out, err, errors)
    finally:
        shutil.rmtree(work, ignore_errors=True)
