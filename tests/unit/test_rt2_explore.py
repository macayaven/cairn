"""A model checker for job `release`'s step "Create the release": the step's real shell, run
against test_rt2_github's stand-in GitHub under every outside schedule up to a bound, and
held to six invariants after every execution.

An execution is up to three attempts of the step (the first, then the job re-run from the top,
twice), on the same GitHub. Before every request of every attempt a person may act; every
request may succeed, fail before it took effect, or take effect and lose its reply (an upload may
also be cut, leaving a file unfinished); the CLI's two lookups by tag may answer in either order;
the listing is behind by a number of looks given for each kind of write. Between attempts the
person reads the error line and does what it says (or, as a deviation, only re-runs). A schedule
is the set of choice points that leave the default; the explorer runs every schedule of up to K
such deviations, breadth first, so the first execution that breaks an invariant is a shortest
one, for each lag; and a seeded random walk goes past K.

Invariants (the task's numbering)
1. The step never deletes a release or a file and never edits anything but one release's draft
   flag (and, for a candidate, make_latest=false), and only a release that carries its mark; it
   never makes a git tag.
2. When the step publishes a release, each difference from the run's tag, mark, flag, title,
   notes and files was written after the step's last read by id of that release before the
   publication (the documented race); any difference older than that read is a violation. The
   label says which read missed it: a listing that did not show it yet (the known fault of the
   sixth round), a read by id that does not cover it, or none.
3. The step exits zero only if the release it read last by id was, at that read, public, the
   run's (mark, flag, tag, git tag at the run's commit), with the title rule, the notes and
   exactly the run's files, and the only release for its tag.
4. With nothing wrong but lag within the ten looks, every attempt succeeds.
5. Every non-zero exit prints one `::error::` line (or none, when a gh command failed by
   itself, the README's carve-out, counted apart); the line is true of GitHub's state when the
   step stops, and its advice, followed, deletes no good public release.
6. A re-run never makes things worse: no violation of 1 or 2 in any attempt, no second release
   made beside a good one (judged from the re-run's first listing), and every attempt ends.
L. When the step exits zero, the release it made public is on the run's commit, and GitHub's
   Latest is the highest stable version (a stated limit excepted: a release published by hand,
   or one the listing shows only after the job's last look).

Running. By default (no variable set) this file runs a bounded exploration, every schedule of
one deviation for a release tag with the listing current, two attempts and the person's core
menu (about half a minute), and the lag-alone runs of a release tag. The full exploration (the
three kinds of tag, every lag, the whole menu, three attempts, the switches, random walks past
the bound and the rediscovery of the sixth round's fault; about forty minutes) runs with
RT2_FULL=1:

    RT2_FULL=1 uv run pytest -o addopts="" -q -p no:cacheprovider tests/unit/test_rt2_explore.py

and a chosen part of it prints each exploration's violations as it ends:

    uv run python tests/unit/test_rt2_explore.py release,candidate,backport current,"one behind"
"""

from __future__ import annotations

import collections
import copy
import hashlib
import os
import random
import re
import time
from dataclasses import dataclass, field

import pytest
from test_rt2_github import (
    KINDS,
    MARK,
    RUN_SHA,
    TAGS,
    Config,
    Event,
    Hub,
    Hung,
    Scheduler,
    Write,
    body_text,
    current_run,
    highest_stable,
    pinned_run,
    run_attempt,
    run_files,
    stable_key,
)

PRE_FIX = "27aff19f"  # the step the sixth review refused, read from history


def lags(**given: int) -> dict:
    lag = dict.fromkeys(KINDS, 0)
    lag.update(given)
    return lag


LAGS = {
    "current": lags(),
    "one behind": lags(create=1, upload=1, publish=1, person=1),
    "person one behind": lags(person=1),
    "person nine behind": lags(person=9),
    "own writes nine behind": lags(create=9, upload=9, publish=9),
    "all nine behind": lags(create=9, upload=9, publish=9, person=9),
    "uploads behind publication": lags(upload=9),
    "beyond the bound": lags(create=10, upload=10, publish=10, person=10),
}


# --- executions -----------------------------------------------------------------------------------


@dataclass
class Execution:
    config: Config
    plan: dict
    hub: Hub
    attempts: list
    follows: list = field(default_factory=list)
    stopped: bool = False  # the person, as a line said, re-runs no more


_MEMO: dict = {}


def _key(run: str, hub: Hub) -> tuple:
    c = hub.config
    return (
        hashlib.sha256(run.encode()).hexdigest(),
        repr(sorted(hub.truth["releases"].items())),
        repr(sorted(hub.truth["tags"].items())),
        hub.truth.get("latest"),
        tuple(sorted(hub.files.items())),
        hub.next_id,
        hub.next_asset,
        c.tag,
        c.bytag,
        c.unique_published,
        c.draft_dup_allowed,
        c.starter_download,
        c.per_page,
        repr(sorted(c.lag.items())),
        c.existing,
        c.by_hand,
        hub.next_other,
        c.annotated,
    )


def attempt(run: str, hub: Hub, budget_spent: bool):
    """One attempt; replayed from memory when no deviation is left for it and the listing has
    caught up (the attempt is then a function of the state it starts from)."""
    s = hub.s
    memo_ok = (
        budget_spent
        and not hub.config.persist_lag
        and s.rng is None
        and all(w.caught for w in hub.writes)  # the listing shows what the truth holds
    )
    key = _key(run, hub) if memo_ok else None
    if key is not None and key in _MEMO:
        done, events, end, listings, next_id, next_asset, next_other, unmodelled = _MEMO[key]
        offset = len(hub.events)
        for e in events:
            e2 = copy.deepcopy(e)
            e2.seq += offset
            e2.attempt = hub.attempt
            hub.events.append(e2)
        hub.writes.append(
            Write(len(hub.writes), "person", hub.listings, ("replace", end), "step", caught=True)
        )
        hub._truth = copy.deepcopy(end)
        hub.listings += listings
        hub.next_id, hub.next_asset, hub.next_other = next_id, next_asset, next_other
        hub.unmodelled.extend(unmodelled)
        return copy.deepcopy(done)
    start_events, start_listings, start_unmodelled = (
        len(hub.events),
        hub.listings,
        len(hub.unmodelled),
    )
    done = run_attempt(run, hub, hub.files)
    if key is not None:
        events = copy.deepcopy(hub.events[start_events:])
        for e in events:
            e.seq -= start_events
        _MEMO[key] = (
            copy.deepcopy(done),
            events,
            copy.deepcopy(hub.truth),
            hub.listings - start_listings,
            hub.next_id,
            hub.next_asset,
            hub.next_other,
            list(hub.unmodelled[start_unmodelled:]),
        )
    return done


def execute(
    run: str,
    config: Config,
    plan: dict | None = None,
    k: int | None = None,
    rng=None,
    rate: float = 0.0,
    scheduler: Scheduler | None = None,
) -> Execution:
    s = scheduler or Scheduler(plan, rng, rate)
    hub = Hub(config, s)
    ex = Execution(config, dict(plan or {}), hub, [])
    for n in range(1, config.attempts + 1):
        hub.attempt = n
        hub.command = 0
        if n > 1:
            follow(hub, ex)
            if ex.stopped:
                break
            # the re-run starts later: by then the listing shows every write, the person's too
            if not config.persist_lag:
                hub.catch_up()
            # job `package` re-run after the five days: only a failed run is re-run
            if config.rebuild_between and n == 2 and ex.attempts[-1].code != 0:
                hub.files = run_files(2)
                hub.event("follow", "job `package` re-run after the five days: new bytes")
        spent = k is not None and len(s.plan) >= k and (not s.plan or max(s.plan) < len(s.points))
        done = attempt(run, hub, spent)
        done.number = n
        done.files = dict(hub.files)
        hub.event(
            "exit",
            f"attempt {n} exits {done.code}" + (f": {done.errors[0][9:]}" if done.errors else ""),
            truth_before=hub.snap(),
        )
        ex.attempts.append(done)
    ex.plan = dict(s.plan)
    return ex


# --- what the state says --------------------------------------------------------------------------


def faults(state: dict, rid, hub: Hub, files: dict) -> list[str]:
    """What keeps release RID in STATE from being the run's whole public release."""
    r = state["releases"].get(rid) if rid is not None else None
    if r is None:
        return ["missing"]
    out = []
    if r["draft"]:
        out.append("draft")
    if r["tag_name"] != hub.tag:
        out.append("tag")
    if state["tags"].get(hub.tag) != RUN_SHA:
        out.append("git tag")
    if MARK not in (r["body"] or ""):
        out.append("mark")
    if r["prerelease"] != hub.config.candidate:
        out.append("flag")
    if r["name"] not in (None, "", hub.tag):
        out.append("title")
    if (r["body"] or "") != body_text(hub.tag):
        out.append("notes")
    names = sorted(a["name"] for a in r["assets"])
    if names != sorted(files):
        out.append("names")
    held = {a["name"]: a for a in r["assets"]}
    if any(
        n not in held or held[n]["data"] != d or held[n]["state"] != "uploaded"
        for n, d in files.items()
    ):
        out.append("bytes")
    return out


COMPONENT = {
    "tag": lambda r, s: r["tag_name"],
    "git tag": lambda r, s: None,  # read from the state's tags below
    "mark": lambda r, s: MARK in (r["body"] or ""),
    "flag": lambda r, s: r["prerelease"],
    "title": lambda r, s: r["name"],
    "notes": lambda r, s: r["body"],
    "names": lambda r, s: sorted(a["name"] for a in r["assets"]),
    "bytes": lambda r, s: sorted((a["name"], a["data"], a["state"]) for a in r["assets"]),
}


def value(state, rid, comp, tag):
    if state is None:
        return "?"
    if comp == "git tag":
        return state["tags"].get(tag)
    r = state["releases"].get(rid)
    return None if r is None else COMPONENT[comp](r, state)


def last_change(events: list[Event], upto: int, rid, comp: str, tag: str) -> int:
    """The seq of the last event before UPTO that changed COMP of RID; -1 if none did."""
    for e in reversed(events[:upto]):
        if e.truth_before is None or e.truth_after is None:
            continue
        if value(e.truth_before, rid, comp, tag) != value(e.truth_after, rid, comp, tag):
            return e.seq
    return -1


def file_value(state, rid, name):
    r = state["releases"].get(rid) if state else None
    a = None if r is None else next((x for x in r["assets"] if x["name"] == name), None)
    return None if a is None else (a["data"], a["state"])


def file_change(events: list[Event], upto: int, rid, name: str) -> int:
    for e in reversed(events[:upto]):
        if e.truth_before is None or e.truth_after is None:
            continue
        if file_value(e.truth_before, rid, name) != file_value(e.truth_after, rid, name):
            return e.seq
    return -1


def file_reads(events: list[Event], rid, name: str) -> list[int]:
    """When the step's downloads read NAME of release RID and got it."""
    return [
        e.seq
        for e in events
        if e.kind == "asset"
        and e.rid == rid
        and e.shown
        and e.shown[0] == name
        and e.shown[1] is not None
    ]


def wrong_files(state, rid, files: dict) -> list[str]:
    return [n for n, d in files.items() if file_value(state, rid, n) != (d, "uploaded")]


def focus(events: list[Event]):
    rid = None
    for e in events:
        if e.what != "request":
            continue
        if e.kind in ("byid", "patch", "pick", "create") and e.rid is not None:
            rid = e.rid
        elif e.kind == "listing" and e.fate == "ok" and e.shown and len(e.shown) == 1:
            rid = e.shown[0]["id"]
    return rid


# --- the person who reads the line ----------------------------------------------------------------


# Each line the step can stop with, and where it says it read what it states: the listing, a read
# by id, or the tag. Matched in this order; the first that matches names the line.
LINES = (
    ("tag-unread", r"the tag \S+ could not be read as a commit of the repository"),
    ("tag-moved", r"the tag \S+ names commit (?P<sha>\S+), not this run's commit"),
    ("unlisted", r"the repository's releases could not be listed"),
    ("crowded", r"GitHub's listing shows (?P<n>\d+) releases for \S+, and this job finishes"),
    ("unseen", r"GitHub's listing, at this job's last look, does not list the release for"),
    (
        "behind",
        r"GitHub's listing shows the release for \S+ without this run's mark, pre-release flag, "
        r"title or notes, while read by its id it is (?P<state>a draft|public) and has all four",
    ),
    ("elsewhere", r"showed for \S+ is, read by its id, a release of another tag"),
    ("gone", r"which GitHub's listing showed, was not found when read by its id"),
    ("unreadable", r"could not be read by its id, as the lines above say"),
    (
        "refuse",
        r"read by its id, a GitHub release for \S+ exists and is not one this run can finish: "
        r"(?P<reason>.*); look at it on",
    ),
    (
        "unfinished",
        r"read by its id, the GitHub release for \S+ is this run's draft and does not hold "
        r"exactly the files this run attested: (?P<reason>.*); this job neither",
    ),
    (
        "unread",
        r"the files of the GitHub release for \S+, (?P<state>a draft|public) as read by its id, "
        r"could not be downloaded",
    ),
    (
        "differs",
        r"read by its id, the GitHub release for \S+, (?P<state>a draft|public), carries run \d+'s "
        r"mark and differs from what that run wrote in its (?P<what>title and notes|title|notes);",
    ),
    (
        "changed",
        r"the GitHub release for \S+, (?P<state>a draft|public), changed between this job's two "
        r"reads",
    ),
    ("drafted", r"read by its id after this job published it, is a draft again"),
    (
        "lost-latest",
        r"read by its id, is this run's and public, .* GitHub's listing after the publication "
        r"shows (?P<higher>\S+) published, a higher stable version",
    ),
)
BY_ID = {
    "refuse",
    "unfinished",
    "unread",
    "differs",
    "changed",
    "drafted",
    "behind",
    "elsewhere",
    "lost-latest",
}


def classify(line: str) -> tuple[str, dict]:
    for kind, pattern in LINES:
        m = re.search(pattern, line)
        if m:
            parts = dict(m.groupdict())
            if "what" in parts:
                parts["what"] = parts["what"].split(" and ")
            return kind, parts
    return "unknown", {}


def follow(hub: Hub, ex: Execution) -> None:
    last = ex.attempts[-1]
    pick = hub.s.choose(
        f"after attempt {last.number}, the person",
        ["does what the line says, then re-runs", "only re-runs"],
    )
    if pick == 1 or not last.errors or not hub.config.follow_advice:
        ex.follows.append("re-runs")
        hub.event("follow", "the person re-runs the job")
        return
    kind, parts = classify(last.errors[0])
    rid = focus([e for e in hub.events if e.attempt == last.number])
    truth = hub.truth
    r = truth["releases"].get(rid) if rid is not None else None
    did = "re-runs"
    before = hub.snap()
    if kind in ("tag-unread", "tag-moved") and truth["tags"].get(hub.tag) != RUN_SHA:
        # the version is withdrawn, or the tag is not the run's: publish nothing, delete the
        # run's release if there is one (and nobody should publish it), re-run no more
        mine = [x["id"] for x in hub.releases_for(truth) if MARK in (x["body"] or "")]
        for d in mine:
            hub.write("person", ("delete", d), "person")
        ex.stopped = True
        did = f"deletes {mine or 'nothing'} and re-runs no more, as the line says"
    elif kind == "unfinished" and r is not None and r["draft"]:
        hub.write("person", ("delete", rid), "person")
        did = f"deletes release {rid}, as the line says, and re-runs"
    elif kind == "differs" and r is not None:
        if r["draft"]:
            hub.write("person", ("delete", rid), "person")
            did = f"deletes draft {rid}, as the line says, and re-runs"
        else:
            hub.write(
                "person", ("set", rid, {"name": hub.tag, "body": body_text(hub.tag)}), "person"
            )
            did = f"puts back the title and notes of {rid}, as the line says, and re-runs"
    elif kind == "refuse" or (kind == "crowded" and len(hub.releases_for(truth)) > 1):
        # the releases page first: delete those nobody should publish, keeping a public release
        # of this run that holds its files
        doomed = [
            x["id"] for x in hub.releases_for(truth) if faults(truth, x["id"], hub, last.files)
        ]
        for d in doomed:
            hub.write("person", ("delete", d), "person")
        did = (
            (f"deletes {doomed} (keeping any whole public release), as the line says, and re-runs")
            if doomed
            else "finds nothing to delete and re-runs"
        )
    elif kind == "lost-latest":
        higher = [
            x["id"]
            for x in truth["releases"].values()
            if x["tag_name"] == parts["higher"] and not x["draft"]
        ]
        if higher:
            hub.write("person", ("latest", higher[0]), "person")
            did = f"sets {parts['higher']} as the latest release, as the line says, and re-runs"
    elif (
        kind == "unread"
        and r is not None
        and r["draft"]
        and (
            any(a["state"] != "uploaded" for a in r["assets"])
            or sorted(a["name"] for a in r["assets"]) != sorted(last.files)
        )
    ):
        hub.write("person", ("delete", rid), "person")
        did = f"sees a file of draft {rid} missing or unfinished, deletes it, and re-runs"
    ex.follows.append(did)
    hub.event("follow", f"the person {did}", truth_before=before, truth_after=hub.snap())


# --- the invariants -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    invariant: str
    label: str

    def __str__(self):
        return f"{self.invariant}: {self.label}"


def check(ex: Execution) -> tuple[list[Violation], list[str]]:
    """The violations of one execution, and notes (documented races, bare exits)."""
    hub, events = ex.hub, ex.hub.events
    out: list[Violation] = []
    notes: list[str] = []
    tag = hub.tag
    if hub.unmodelled:
        out.append(
            Violation("model", f"the step asked what the model does not know: {hub.unmodelled[0]}")
        )
    files_of = {a.number: a.files for a in ex.attempts}
    for e in events:
        if e.what != "request" or not e.took_effect:
            continue
        files = files_of.get(e.attempt, hub.files)
        # 1. writes
        if e.kind == "patch":
            allowed = [{"draft": False, "make_latest": "false"}]
            if not hub.config.candidate:
                allowed.append({"draft": False, "make_latest": "true"})
            if e.shown not in allowed:
                out.append(Violation("1", f"the publication sent {e.shown}"))
            latest_after_patch(e, events, hub, out, notes)
            before = e.truth_before["releases"].get(e.rid)
            if before is not None and MARK not in (before["body"] or ""):
                out.append(
                    Violation("1", "the step wrote to a release that does not carry its mark")
                )
            if e.truth_after["tags"].get(tag) != e.truth_before["tags"].get(tag):
                if e.truth_after["tags"].get(tag) == RUN_SHA:
                    notes.append(
                        "1 (stated, not measured): the tag deleted after the last tag read is "
                        "made again by the publication, on its target, the run's commit"
                    )
                else:
                    out.append(
                        Violation(
                            "1",
                            "the publication made the git tag on a commit not the run's",
                        )
                    )
            # 2. what it published
            was = e.truth_before["releases"].get(e.rid)
            if was is None or not was["draft"]:
                continue
            for comp in faults(e.truth_before, e.rid, hub, files):
                if comp in ("draft", "missing"):
                    continue
                t = last_change(events, e.seq, e.rid, comp, tag)
                same = [x for x in events[: e.seq] if x.attempt == e.attempt]
                byid = [
                    x.seq
                    for x in same
                    if x.kind == "byid"
                    and x.rid == e.rid
                    and x.fate == "ok"
                    and x.shown is not None
                ]
                looks = [x.seq for x in same if x.kind == "listing" and x.fate == "ok"]
                last_byid = byid[-1] if byid else None
                if comp == "bytes":
                    for name in wrong_files(e.truth_before, e.rid, files):
                        t = file_change(events, e.seq, e.rid, name)
                        reads = file_reads(same, e.rid, name)
                        if not reads:
                            out.append(
                                Violation(
                                    "2", "published a file it never downloaded from this release"
                                )
                            )
                        elif t < reads[-1]:
                            out.append(Violation("2", "published a file its own download read"))
                        elif last_byid is not None and t < last_byid:
                            notes.append(
                                "2 (stated race, wider than the read by id): a file "
                                "replaced after the download and before the read by id is "
                                "published; that read shows names only"
                            )
                        else:
                            notes.append("2 (stated race): a file replaced after the last read")
                elif comp == "git tag":
                    tagreads = [x.seq for x in same if x.kind == "tag" and x.fate == "ok"]
                    if not tagreads or t < tagreads[-1]:
                        out.append(
                            Violation(
                                "2",
                                "published a draft whose git tag was gone or moved before "
                                "its last read of the tag",
                            )
                        )
                    elif e.truth_before["tags"].get(tag) is None:
                        notes.append(
                            "2 (stated race): the tag deleted after the last tag read, before "
                            "the publication"
                        )
                    else:
                        notes.append(
                            "2 (README says not published): the tag moved after the last tag "
                            "read, before the publication, is published on the moved tag"
                        )
                else:
                    if last_byid is not None and t < last_byid:
                        out.append(Violation("2", f"published a {comp} its read by id had shown"))
                    elif any(x > t for x in looks):
                        out.append(
                            Violation(
                                "2",
                                f"published an edit of its {comp} made before "
                                f"its comparison, which read a listing that "
                                f"did not show it yet",
                            )
                        )
                    else:
                        notes.append(f"2 (stated race): its {comp} changed after the last read")
    # 3, 5 per attempt
    for a in ex.attempts:
        mine = [e for e in events if e.attempt == a.number]
        reqs = [e for e in mine if e.what == "request"]
        files = a.files
        if a.code == 0:
            byid = [e for e in reqs if e.kind == "byid" and e.fate == "ok" and e.shown]
            last = byid[-1] if byid else (reqs[-1] if reqs else None)
            if last is None:
                out.append(Violation("3", "exit 0 without a request"))
            else:
                state = last.truth_before
                rels = hub.releases_for(state)
                rid = last.rid if byid else (rels[0]["id"] if rels else None)
                bad = faults(state, rid, hub, files)
                how = "its last read by id" if byid else "its last request (no read by id)"
                tagreads = [e for e in reqs if e.kind == "tag" and e.fate == "ok"]
                end_state = next(e for e in mine if e.what == "exit").truth_before
                if "git tag" in bad:
                    bad.remove("git tag")
                if not tagreads or tagreads[-1].shown != RUN_SHA:
                    out.append(
                        Violation("3", "exit 0 although its last tag read was not the run's commit")
                    )
                elif end_state["tags"].get(tag) != RUN_SHA:
                    notes.append("3 (race): the tag went or moved after the last tag read")
                latest_at_exit(a, ex.attempts, mine, end_state, rid, hub, out, notes)
                for comp in bad:
                    if comp == "bytes":
                        kinds = set()
                        for name in wrong_files(state, rid, files):
                            t = file_change(events, last.seq, rid, name)
                            reads = file_reads(reqs, rid, name)
                            if reads and byid and reads[-1] < t:
                                kinds.add(
                                    "exit 0 on a file replaced after the download and "
                                    "before the last read by id, which shows names only"
                                )
                            elif not reads:
                                kinds.add(
                                    "exit 0 on files it did not download from the "
                                    "release it read last by id"
                                )
                            else:
                                kinds.add("")
                        for label in kinds - {""}:
                            out.append(Violation("3", label))
                        if "" not in kinds:
                            continue
                    out.append(
                        Violation(
                            "3",
                            f"exit 0 although, at {how}, the release's {comp} was not the run's",
                        )
                    )
                # one release for the tag, as GitHub held it when the step last listed: a release
                # made after that listing is a race no listing can close
                looks = [e for e in reqs if e.kind == "listing" and e.fate == "ok"]
                if looks:
                    held = hub.releases_for(looks[-1].truth_before)
                    if len(held) != 1 and len(looks[-1].shown or []) > 1:
                        out.append(
                            Violation("3", "exit 0 although its last look listed two releases")
                        )
                    elif len(held) != 1:
                        # decision 6: missing from the listing, a stated limit
                        notes.append(
                            f"3 (stated limit): exit 0 with {len(held)} releases for the tag "
                            f"when it last listed them"
                        )
                    elif len(rels) != 1:
                        notes.append("3 (race): a second release made after the last listing")
        else:
            if not a.errors:
                notes.append("5 (carve-out): a gh command failed by itself, no ::error:: line")
            elif len(a.errors) > 1:
                out.append(Violation("5", "more than one ::error:: line"))
            else:
                out.extend(claim(a, mine, hub, files, notes))
        # 6. a re-run that makes things worse
        # judged from the re-run's first listing: a person who deletes the run's release after
        # the tag read and before that listing leaves the step nothing it could have seen
        start = next((e for e in mine if e.kind == "listing" and e.truth_before is not None), None)
        end = next((e for e in mine if e.what == "exit"), None)
        if start is not None and end is not None and a.number > 1:
            whole = [
                r["id"]
                for r in hub.releases_for(start.truth_before)
                if not faults(start.truth_before, r["id"], hub, files)
            ]
            made = [e for e in mine if e.kind == "create" and e.took_effect]
            if whole and made:
                out.append(
                    Violation(
                        "6", "a re-run made a second release beside the run's whole public one"
                    )
                )
        for e in mine:
            before = [x for x in mine if x.seq < e.seq and x.kind == "listing" and x.fate == "ok"]
            if (
                e.kind == "create"
                and e.took_effect
                and before
                and hub.releases_for(before[-1].truth_before)
            ):
                held = before[-1].truth_before["releases"]
                by = (
                    "an earlier attempt"
                    if any(
                        x.kind == "create"
                        and x.took_effect
                        and x.attempt < a.number
                        and x.rid in held
                        for x in events
                    )
                    else "a person"
                )
                # decision 6: one a person makes while the job runs, or the draft of an earlier
                # attempt just before a re-run, missing from the listing: a stated limit
                if before[-1].shown:
                    out.append(Violation("6", "made a second release its look had listed"))
                notes.append(
                    f"6 (stated limit): the step made a second release for the tag; its "
                    f"look did not show the one {by} made"
                )
        if any("more often than the limit" in u for u in hub.unmodelled):
            out.append(Violation("6", "an attempt did not end"))
    return sorted(set(out), key=str), notes


def claim(a, mine: list[Event], hub: Hub, files: dict, notes: list) -> list[Violation]:
    """Invariant 5 for one error line: true of what it says it read (the read by id, the
    listing, the tag), and advice that, followed on the state the step left, deletes no good
    public release."""
    line = a.errors[0]
    kind, parts = classify(line)
    end = next(e for e in mine if e.what == "exit")
    rid = focus(mine)
    byids = [e for e in mine if e.kind == "byid"]
    reads = [e for e in byids if e.fate == "ok" and e.shown]
    looks = [e for e in mine if e.kind == "listing" and e.fate == "ok"]
    out = []

    def false(why):
        out.append(Violation("5", f"the {kind} line is not true of what it read: {why}"))

    # where the line says it read: by id (its last read), else the state the step left
    state = end.truth_before
    if kind in BY_ID:
        if not reads and kind != "elsewhere":
            false("no read by id answered")
            return out
        if reads:
            state, rid = reads[-1].truth_before, reads[-1].rid
    r = state["releases"].get(rid) if rid is not None else None
    bad = faults(state, rid, hub, files)
    # the advice is followed on the state the step left: whole as this attempt's files say, or
    # as the run's first build's (job `package` re-run after five days builds other bytes)
    left = end.truth_before
    r_left = left["releases"].get(rid) if rid is not None else None
    whole_public = r_left is not None and (
        not faults(left, rid, hub, files) or not faults(left, rid, hub, run_files(1))
    )

    def reasons(reason):
        if reason.startswith("its text does not carry") and r is not None and "mark" not in bad:
            false("the release carries the mark")
        m = re.match(r"its pre-release flag is (\w+)", reason)
        if m and r is not None and str(r["prerelease"]).lower() != m[1]:
            false("the flag is otherwise")
        m = re.match(r"its files are \[(.*)\], not", reason)
        if m and r is not None and "names" not in bad:
            false("the release holds exactly the run's file names")
        m = re.match(r"(\S+) is not the file this run attested", reason)
        if m and r is not None and "bytes" not in bad:
            false(f"{m[1]} is the file this run attested")

    if kind == "unknown":
        out.append(Violation("5", f"a line the checker does not know: {line[:80]}"))
    elif kind == "unfinished":
        if r is None:
            false("the release is not there")
        elif not r["draft"]:
            false("the release is public")
        elif not ({"names", "bytes"} & set(bad)):
            false("the draft holds exactly the run's files")
        reasons(parts["reason"])
        if whole_public:
            out.append(
                Violation(
                    "5",
                    "the unfinished line tells a person to delete the run's whole public release",
                )
            )
    elif kind == "differs":
        if r is None:
            false("the release is not there")
        else:
            if (parts["state"] == "a draft") != r["draft"]:
                false(
                    f"it says {parts['state']}, the release is "
                    f"{'a draft' if r['draft'] else 'public'}"
                )
            for what in parts["what"]:
                if what not in bad:
                    false(f"its {what} is the run's")
    elif kind == "refuse":
        reasons(parts["reason"])
        if whole_public and files != run_files(1) and not faults(left, rid, hub, run_files(1)):
            notes.append(
                "5 (stated: 'One case does not recover in its own run: a `release` recorded as "
                "failed although it made the release, re-run after the five days, compares the "
                "release with files `package` built again and stops unless they are the same "
                "bytes')"
            )
        elif whole_public:
            out.append(
                Violation(
                    "5",
                    "the refusal advises deleting, 'if nobody should publish "
                    "it', the run's whole public release",
                )
            )
    elif kind == "crowded":
        if not looks or len(looks[-1].shown or []) != int(parts["n"]):
            false(f"its last look did not list {parts['n']} releases")
    elif kind == "behind":
        row = next((x for x in (looks[-1].shown if looks else None) or [] if x["id"] == rid), None)
        if row is None:
            false("the last look listed no row for the release")
        elif (
            MARK in (row["body"] or "")
            and row["prerelease"] == hub.config.candidate
            and row["name"] in (None, "", hub.tag)
            and (row["body"] or "") == body_text(hub.tag)
        ):
            false("the listing's row shows the run's mark, flag, title and notes")
        if r is None or set(bad) & {"mark", "flag", "title", "notes"}:
            false("read by its id the release is not as the run wrote it")
        elif (parts["state"] == "a draft") != r["draft"]:
            false(f"it says {parts['state']}")
    elif kind == "drafted":
        if r is None or not r["draft"]:
            false("read by its id the release is not a draft")
    elif kind == "elsewhere":
        last = byids[-1] if byids else None
        held = last.truth_before["releases"].get(last.rid) if last else None
        if held is None or held["tag_name"] == hub.tag:
            false("read by its id the release is of this tag, or not there")
    elif kind == "lost-latest":
        if r is None or [b for b in bad if b != "git tag"]:
            false("read by its id the release is not this run's whole public one")
        if not looks or parts["higher"] not in looks[-1].others:
            false(f"its last look did not list {parts['higher']}")
        elif stable_key(parts["higher"]) <= stable_key(hub.tag):
            false(f"{parts['higher']} is not higher")
    elif kind == "unread":
        if r is not None and (parts["state"] == "a draft") != r["draft"]:
            false(f"it says {parts['state']}")
    elif kind == "gone":
        last = byids[-1] if byids else None
        if last is None or last.rid in (last.truth_before or {}).get("releases", {}):
            false("the release was there when read by its id")
    elif kind == "tag-unread":
        tags = [e for e in mine if e.kind == "tag"]
        if tags and tags[-1].fate == "ok" and tags[-1].shown is not None:
            false("the tag was read as a commit")
    elif kind == "tag-moved":
        tags = [e for e in mine if e.kind == "tag" and e.fate == "ok"]
        if not tags or tags[-1].shown != parts["sha"] or parts["sha"] == RUN_SHA:
            false(f"the tag's last read showed {tags[-1].shown if tags else None}")
    elif kind == "changed":
        if len(reads) < 2:
            false("there were not two reads by id")
        else:
            a1, a2 = (compared(x.truth_before, x.rid) for x in reads[-2:])
            if a1 == a2:
                false("the release was the same at both reads")
            if r is not None and (parts["state"] == "a draft") != r["draft"]:
                false(f"it says {parts['state']}")
    return out


def compared(state, rid):
    """What the step's two reads by id compare: files (ids, names, sizes), title, notes, flags."""
    r = state["releases"].get(rid)
    if r is None:
        return None
    files = sorted(
        (a["id"], a["name"], a["state"], a.get("size", len(a["data"]))) for a in r["assets"]
    )
    return (r["tag_name"], r["name"], r["draft"], r["prerelease"], r["body"], files)


def published_at(events, rid) -> int:
    """The seq of the event that published release RID (another run's, or a person's act)."""
    for e in events:
        if e.truth_before is None or e.truth_after is None:
            continue
        was = e.truth_before["releases"].get(rid)
        now = e.truth_after["releases"].get(rid)
        if now is not None and not now["draft"] and (was is None or was["draft"]):
            return e.seq
    return -1


def latest_cause(e_seq, events, attempt, state, rid) -> tuple[str, str]:
    """Why release RID was made Latest at E_SEQ although a higher one was published: a higher
    one published before the attempt's last look and missing from it (stated), or after it."""
    higher = highest_stable(state)
    when = published_at(events, higher)
    looks = [
        x.seq
        for x in events[:e_seq]
        if x.attempt == attempt and x.kind == "listing" and x.fate == "ok"
    ]
    last_look = looks[-1] if looks else -1
    if last_look >= 0 and state["releases"][higher]["tag_name"] in events[last_look].others:
        return "listed", "its last look listed the higher version"
    if when < last_look:
        return "stated", "a higher version published before the last look was missing from it"
    return "new", "a higher version published after the job's last look, before its publication"


def same_version(state, a, b) -> bool:
    """Releases A and B are of one version (two releases for a tag tie for the highest)."""
    ra, rb = state["releases"].get(a), state["releases"].get(b)
    return ra is not None and rb is not None and ra["tag_name"] == rb["tag_name"]


HAND = (
    "L (stated: 'A release published by hand, or one GitHub's listing shows only after the "
    "job's last look, can still leave GitHub's latest on a lower version')"
)


def latest_after_patch(e, events, hub, out, notes):
    """GitHub's Latest is the highest stable version, as the publication leaves it."""
    after = e.truth_after
    want = highest_stable(after)
    if (
        after["latest"] == want
        or hub.config.candidate
        or same_version(after, after["latest"], want)
    ):
        return
    if after["latest"] == e.rid and e.truth_before["latest"] != e.rid:
        how, why = latest_cause(e.seq, events, e.attempt, after, e.rid)
        if how == "listed":
            out.append(Violation("L", f"the publication said latest although {why}"))
        else:
            notes.append(f"{HAND}: {why}")
    elif want == e.rid:
        out.append(Violation("L", "the publication left Latest on a lower version"))


def latest_at_exit(a, attempts, mine, state, rid, hub, out, notes):
    want = highest_stable(state)
    if state["latest"] == want or same_version(state, state["latest"], want):
        return
    held = state["releases"].get(state["latest"])
    if held is None or held["draft"]:
        # GitHub's choice of Latest after its Latest is deleted or made a draft is not modelled
        notes.append("L (not modelled): Latest's release was deleted or made a draft")
        return
    if rid is None or state["latest"] != rid:
        if want == rid:
            out.append(Violation("L", "exit 0 with its release the highest and not Latest"))
        else:
            notes.append("L (another's act): Latest is not the highest at exit 0")
        return
    made = [
        e
        for e in hub.events
        if e.kind == "patch"
        and e.took_effect
        and e.rid == rid
        and e.truth_after["latest"] == rid
        and e.truth_before["latest"] != rid
    ]
    if not made:
        notes.append("L (another's act): a person made the run's release Latest")
        return
    mine_now = state["releases"][rid]
    if (
        want is None
        or mine_now["draft"]
        or mine_now["prerelease"]
        or stable_key(state["releases"][want]["tag_name"]) <= stable_key(hub.tag)
    ):
        notes.append("L (race): the run's release was changed after its last read")
        return
    p, higher = made[-1], state["releases"][want]["tag_name"]
    if p.attempt == a.number:
        after = [x for x in mine if x.kind == "listing" and x.fate == "ok" and x.seq > p.seq]
        if after and higher in after[-1].others:
            out.append(
                Violation("L", "exit 0 although its look after the publication listed a higher")
            )
        else:
            notes.append(f"{HAND}: missing from the look after the publication")
        return
    stopped = [
        x
        for x in attempts
        if p.attempt <= x.number < a.number
        and x.errors
        and classify(x.errors[0])[0] == "lost-latest"
    ]
    if stopped:
        # a stated limit (README, the lost-latest row): the remedy is to set Latest by hand
        notes.append(
            "L (stated: 'a re-run of the job after this stop sends no publication and succeeds "
            "without looking at GitHub's latest again, so re-running does not repair it')"
        )
    else:
        notes.append(f"{HAND}: an earlier attempt's publication")


# --- exploring ------------------------------------------------------------------------------------


@dataclass
class Found:
    violation: Violation
    deviations: int
    execution: Execution


def reached(errors: list[str]) -> str:
    """Which of the step's stops an attempt reached, its numbers and file lists left out."""
    if not errors:
        return "a gh command failed by itself"
    kind, detail = classify(errors[0])
    text = kind + (
        f" ({detail['state']}: {' and '.join(detail['what'])})" if "what" in detail else ""
    )
    if "reason" in detail:
        text += ": " + re.sub(r"\d+", "N", re.sub(r"\[[^]]*\]", "[...]", detail["reason"]))
    return text


@dataclass
class Report:
    executions: int = 0
    found: dict = field(default_factory=dict)
    notes: collections.Counter = field(default_factory=collections.Counter)
    seconds: float = 0.0
    lines: collections.Counter = field(default_factory=collections.Counter)

    def add(self, ex: Execution, deviations: int):
        self.executions += 1
        violations, notes = check(ex)
        self.notes.update(notes)
        for a in ex.attempts:
            if a.code != 0:
                self.lines[reached(a.errors)] += 1
        for v in violations:
            if v not in self.found or self.found[v].deviations > deviations:
                self.found[v] = Found(v, deviations, ex)

    def merge(self, other: Report):
        self.executions += other.executions
        self.notes.update(other.notes)
        self.lines.update(other.lines)
        self.seconds += other.seconds
        for v, f in other.found.items():
            if v not in self.found or self.found[v].deviations > f.deviations:
                self.found[v] = f


def guarded(report: Report, what: str, make) -> Execution | None:
    """One execution; one that does not end is made again once (a slow machine), and one that
    hangs twice is kept as a note, with its schedule, instead of ending the search."""
    for _ in range(2):
        try:
            return make()
        except Hung as error:
            last = str(error).splitlines()[-1]
            report.notes[f"an attempt did not end once: {what}; last event {last}"] += 1
    report.notes[f"6? an attempt did not end, twice: {what}; last event {last}"] += 1
    return None


def explore(run: str, config: Config, k: int, limit: int | None = None) -> Report:
    """Every schedule of at most K deviations, breadth first (so each violation's first
    execution is a shortest one)."""
    report, t0 = Report(), time.time()
    queue = collections.deque([{}])
    while queue and (limit is None or report.executions < limit):
        plan = queue.popleft()
        ex = guarded(report, f"plan {plan}", lambda plan=plan: execute(run, config, plan, k))
        if ex is None:
            continue
        report.add(ex, len(plan))
        if len(plan) < k:
            after = max(plan) + 1 if plan else 0
            for j in range(after, len(ex.hub.s.points)):
                for alt in range(1, len(ex.hub.s.points[j][1])):
                    queue.append({**plan, j: alt})
    report.seconds = time.time() - t0
    return report


def wander(run: str, config: Config, walks: int, seed: int, rate: float) -> Report:
    """Seeded random schedules, past the bound: each choice point deviates with RATE, and each
    walk draws its own lag for each kind of write, from current to past the ten looks."""
    report, t0 = Report(), time.time()
    rng = random.Random(seed)
    for _ in range(walks):
        lag = {kind: rng.choice([0, 0, 1, 2, 5, 9, 10, 14]) for kind in KINDS}
        c = copy.deepcopy(config)
        c.lag = lag
        c.persist_lag = config.persist_lag or rng.random() < 0.2
        seed = rng.random()
        ex = guarded(
            report,
            f"walk seed {seed}, lag {lag}",
            lambda c=c, seed=seed: execute(run, c, None, None, rng=random.Random(seed), rate=rate),
        )
        if ex is None:
            continue
        report.add(ex, len(ex.hub.s.plan))
    report.seconds = time.time() - t0
    return report


def render(ex: Execution) -> list[str]:
    """The trace, request by request, with the outside actions and the deviations."""
    lines = []
    for e in ex.hub.events:
        if e.what == "sleep":
            continue
        text = f"A{e.attempt} {e.what}: {e.detail}"
        own = body_text(ex.hub.tag)
        if e.kind == "listing" and e.fate == "ok":
            shown = [
                f"{r['id']} {'draft' if r['draft'] else 'public'}"
                f"{'' if r['body'] == own else ' (notes edited)'}"
                f"{'' if r['name'] in (None, '', ex.hub.tag) else ' (title edited)'}"
                f" files={len(r['assets'])}"
                for r in (e.shown or [])
            ]
            text += f" -> shows {shown or 'nothing'}"
        if e.kind == "byid" and e.shown:
            r = e.shown
            text += (
                f" -> {'draft' if r['draft'] else 'public'}, title {r['name']!r}, "
                f"notes {'as written' if r['body'] == own else 'edited'}"
                f", files {[a['name'] for a in r['assets']]}"
            )
        lines.append(text)
    taken = [
        (ex.hub.s.points[j][0], ex.hub.s.points[j][1][alt])
        for j, alt in sorted(ex.plan.items())
        if j < len(ex.hub.s.points)
    ]
    lines.append(f"deviations: {taken}; lag {ex.config.lag}; tag {ex.config.tag}")
    return lines


def summary(report: Report) -> str:
    out = [f"{report.executions} executions in {report.seconds:.0f} s"]
    for v, f in sorted(report.found.items(), key=lambda kv: (kv[1].deviations, str(kv[0]))):
        out.append(f"== {v} (shortest: {f.deviations} deviations)")
        out.extend("   " + line for line in render(f.execution))
    for note, n in sorted(report.notes.items()):
        out.append(f"-- note x{n}: {note}")
    for line, n in sorted(report.lines.items()):
        out.append(f"-- stop reached x{n}: {line}")
    return "\n".join(out)


FULL = os.environ.get("RT2_FULL") == "1"
full_only = pytest.mark.skipif(not FULL, reason="the full exploration runs with RT2_FULL=1")


def budget(default: int) -> int:
    return int(os.environ.get("RT2_BUDGET", default))


# --- the tests ------------------------------------------------------------------------------------


@full_only
def test_the_checker_finds_the_sixth_round_fault_by_itself_in_the_step_it_was_found_in():
    """The step at 27aff19f, explored with one deviation and a listing one look behind a
    person's write: the search, not a scripted case, must reach both outcomes report-27aff19f
    describes: an edited draft published, and exit 0 on an already-edited public release."""
    run = pinned_run(PRE_FIX)
    report = Report()
    for tag in TAGS.values():
        report.merge(explore(run, Config(tag=tag, lag=LAGS["person one behind"]), k=1))
    labels = {str(v) for v in report.found}
    print(summary(report))
    stale_publish = [
        x
        for x in labels
        if x.startswith("2: published an edit of its") and "listing that did not show it yet" in x
    ]
    stale_success = [x for x in labels if x.startswith("3: exit 0 although, at its last request")]
    assert stale_publish, labels
    assert stale_success, labels


WITHIN = [name for name in LAGS if name != "beyond the bound"]

# The three kinds of tag the step publishes, each with the stable versions GitHub already holds
# and the versions another tag's run may publish while the step runs (higher and lower).
SCENARIOS = {
    "release": dict(tag=TAGS["release"], existing=("v1.2.2",), by_hand=("v1.3.0", "v1.2.1")),
    "candidate": dict(tag=TAGS["candidate"], existing=("v1.2.2",), by_hand=("v1.3.0",)),
    # v1.10.0 above v1.2.3 only when versions are compared as numbers
    "backport": dict(tag=TAGS["release"], existing=("v1.10.0",), by_hand=("v1.10.1", "v1.2.2")),
}


@pytest.mark.parametrize("scenario", SCENARIOS if FULL else ["release"])
@pytest.mark.parametrize("lag", WITHIN)
def test_lag_alone_within_the_ten_looks_never_stops_a_run(scenario, lag):
    """Invariant 4: nothing wrong but a listing behind each kind of write by up to nine looks
    (the tenth look shows it): the first attempt succeeds, and so does each re-run."""
    ex = execute(current_run(), Config(lag=LAGS[lag], **SCENARIOS[scenario]), {}, 0)
    assert [a.code for a in ex.attempts] == [0, 0, 0], "\n".join(render(ex))
    assert not check(ex)[0]


@full_only
def test_lag_alone_drawn_at_random_within_the_ten_looks_never_stops_a_run():
    rng = random.Random(20261120)
    for _ in range(budget(24)):
        lag = {kind: rng.randrange(0, 10) for kind in KINDS}
        scenario = rng.choice(list(SCENARIOS))
        ex = execute(current_run(), Config(lag=lag, **SCENARIOS[scenario]), {}, 0)
        assert [a.code for a in ex.attempts] == [0, 0, 0], "\n".join(render(ex))


def test_every_one_deviation_schedule_of_a_release_tag_keeps_the_invariants():
    """The bounded exploration the default run makes: every schedule of one deviation for a
    release tag (v1.2.2 published before it, v1.3.0 and v1.2.1 a person may publish by hand),
    the listing current, two attempts, the person's core menu (the small one and the acts on
    the git tag)."""
    config = Config(lag=LAGS["current"], person_menu="core", attempts=2, **SCENARIOS["release"])
    report = explore(current_run(), config, k=1)
    assert report.executions > 150, summary(report)
    assert not report.found, summary(report)


@full_only
@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("lag", list(LAGS))
def test_every_schedule_of_one_deviation_keeps_the_invariants(scenario, lag):
    """Every request of three attempts may fail, lose its reply or be cut; before every request
    a person may do any one thing the menu holds, and another run may publish its version;
    between attempts the person may skip the line's advice. One of these at a time."""
    config = Config(lag=LAGS[lag], **SCENARIOS[scenario])
    report = explore(current_run(), config, k=budget(1))
    assert not report.found, summary(report)


SWITCHES = {
    "the listing stays behind across re-runs": dict(persist_lag=True, lag=LAGS["one behind"]),
    "package re-run after five days": dict(rebuild_between=True),
    "two published releases for a tag allowed": dict(
        unique_published=False, lag=LAGS["person one behind"]
    ),
    "an annotated tag": dict(annotated=True),
}


@full_only
@pytest.mark.parametrize("switch", list(SWITCHES))
def test_one_deviation_under_what_was_not_measured(switch):
    config = Config(person_menu="small", **SCENARIOS["release"], **SWITCHES[switch])
    report = explore(current_run(), config, k=budget(1))
    assert not report.found, summary(report)


@full_only
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_random_schedules_past_the_bound(scenario):
    config = Config(**SCENARIOS[scenario])
    report = wander(current_run(), config, walks=budget(60), seed=7, rate=0.03)
    assert not report.found, summary(report)


def main() -> None:  # pragma: no cover - the full run, outside pytest
    """python test_rt2_explore.py SCENARIO[,SCENARIO] LAG[,LAG] [K]: each exploration's
    violations as soon as it ends, then the whole summary."""
    import sys

    scenarios = sys.argv[1].split(",") if len(sys.argv) > 1 else list(SCENARIOS)
    lag_names = sys.argv[2].split(",") if len(sys.argv) > 2 else ["current"]
    k = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    run, total = current_run(), Report()
    for scenario in scenarios:
        for name in lag_names:
            if scenario.startswith("switch:"):
                switch = SWITCHES[scenario[7:]]
                config = Config(person_menu="small", **SCENARIOS["release"], **switch)
            else:
                config = Config(lag=LAGS[name], **SCENARIOS[scenario])
            r = explore(run, config, k=k)
            print(
                f"{scenario} {name}: {r.executions} executions, {len(r.found)} violations, "
                f"{r.seconds:.0f} s",
                flush=True,
            )
            print("".join(f"   {v}\n" for v in sorted(map(str, r.found))), end="", flush=True)
            total.merge(r)
    print(summary(total))


if __name__ == "__main__":
    main()
