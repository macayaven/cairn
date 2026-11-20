"""The independent tester's model of GitHub (test_rt2_github), taught the requests job
`release` has sent since the tester's checker was written, and its invariants restated where a
decision changed them. The tester's three files are not edited: this module subclasses their
`Hub` and wraps their `check`, and test_release_rt2_model_unit runs their findings and their
exploration through it.

The requests taught, each read in GitHub's REST description and in the CLI's source at v2.102.0:
- the tag, `gh api -H 'Accept: application/vnd.github.sha' repos/R/commits/tags/<tag>`: GitHub's
  "Get a commit", which "Returns the commit's SHA-1 hash"; the commit the tag names now (an
  annotated tag's too), or, for a tag that is gone, a 422 (which status GitHub gives is not
  measured; the step treats any failure alike). Before it a person may act, and it may fail.
- a release's file by its asset id, `gh api --allow-escape-sequences -H 'Accept:
  application/octet-stream' repos/R/releases/assets/<id>`: "Get a release asset", the bytes
  the asset holds now, or a 404 for an asset that is not there or whose upload was cut (the
  model's own switch `starter_download`, as its `gh release download` does). Recorded as the
  model's own download events (`kind="asset"`, the release that holds the asset, the name and
  the bytes), so the checker's reads of a file see it.
- `gh release create --target <commit>`: the CLI sends it as `target_commitish` (create.go, 459
  to 461). The model's publication still makes a missing tag on the default branch's head: what
  GitHub does with a stored target then is not measured, so the model is left as the tester
  wrote it, and a tag deleted between the step's last read of it and the publication shows as
  the tester's invariant 1.

Restated (the coordinator's decisions; the tester will restate them in its own files):
- invariant 1 allowed a stable publication to send `draft=false` alone. Decision 4: it also
  sends `make_latest`, true exactly when the version is the highest stable one among the
  repository's published releases and itself. A publication that sends the right value is
  not a violation; the wrong value is.
- invariant 5 knew only the lines of the step it was written for. The new lines (the tag that
  cannot be read as a commit, the tag that names another commit, the release that changed
  between two reads by id) are judged here: each must be true of what the step read.
- invariant 6 ("a re-run made a second release beside the run's whole public one") takes the
  state before an attempt's first request, which was its first listing. The step now reads the
  tag first (decision 1), so a person who deletes the whole public release between that read
  and the listing made the re-run's new release count as one beside it, though GitHub then
  held none. The state is taken where the tester took it: before the attempt's first listing.
"""

from __future__ import annotations

import re

import test_rt2_explore as explore
from test_rt2_github import GH_REPO, RUN_SHA, Hub

STABLE = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
TAG_LINE = re.compile(r"the tag (\S+) could not be read as a commit of the repository")
MOVED_LINE = re.compile(r"the tag (\S+) names commit ([0-9a-f]+), not this run's commit")
CHANGED_LINE = re.compile(r"changed between this job's two reads of it by its id")
BESIDE = "a re-run made a second release beside the run's whole public one"
THEIR_CHECK = explore.check


class Hub2(Hub):
    """The tester's GitHub, answering the three requests it did not know."""

    def call(self, prog, cwd, args, env_tmp):
        if prog == "gh" and args[:2] == ["release", "create"] and "--target" in args:
            at = args.index("--target")
            target = args[at + 1] if at + 1 < len(args) else ""
            if target != RUN_SHA:
                self.command += 1
                self.unmodelled.append(f"gh release create --target {target}")
                return 99, "", "the model does not know this target\n"
            args = args[:at] + args[at + 2 :]
        if prog == "gh" and args[:1] == ["api"]:
            rest = args[1:]
            accept = None
            if "-H" in rest:
                at = rest.index("-H")
                accept = rest[at + 1]
                rest = rest[:at] + rest[at + 2 :]
            raw = "--allow-escape-sequences" in rest
            rest = [a for a in rest if a != "--allow-escape-sequences"]
            if accept is not None:
                self.command += 1
                if len(rest) != 1:
                    self.unmodelled.append(f"gh api {' '.join(args[1:])}")
                    return 99, "", "the model does not know\n"
                path = rest[0]
                tag_path = f"repos/{GH_REPO}/commits/tags/{self.tag}"
                if accept == "Accept: application/vnd.github.sha" and path == tag_path:
                    return self.tag_read()
                asset = re.fullmatch(rf"repos/{GH_REPO}/releases/assets/([0-9]+)", path)
                if accept == "Accept: application/octet-stream" and asset and raw:
                    return self.asset(int(asset[1]))
                self.unmodelled.append(f"gh api {' '.join(args[1:])}")
                return 99, "", "the model does not know\n"
        return super().call(prog, cwd, args, env_tmp)

    def tag_read(self):
        fate = self.request("tag", f"GET commits/tags/{self.tag}", write=False)
        at = self._truth["tags"].get(self.tag)
        if fate == "fail":
            self.event(
                "request",
                f"GET commits/tags/{self.tag}: 502",
                kind="tag",
                fate=fate,
                truth_before=self.snap(),
            )
            return 1, '{"message":"Server Error"}', "gh: Server Error (HTTP 502)\n"
        self.event(
            "request",
            f"GET commits/tags/{self.tag}" + ("" if at else ": 422"),
            kind="tag",
            truth_before=self.snap(),
            shown=at,
        )
        if at is None:
            return (
                1,
                '{"message":"No commit found for SHA: tags/' + self.tag + '"}',
                f"gh: No commit found for SHA: tags/{self.tag} (HTTP 422)\n",
            )
        return 0, at, ""

    def asset(self, aid: int):
        holder = next(
            (
                r
                for r in self._truth["releases"].values()
                if any(a["id"] == aid for a in r["assets"])
            ),
            None,
        )
        rid = None if holder is None else holder["id"]
        fate = self.request("asset", f"GET asset {aid} by its id", write=False, rid=rid)
        holder = next(
            (
                r
                for r in self._truth["releases"].values()
                if any(a["id"] == aid for a in r["assets"])
            ),
            None,
        )
        live = None if holder is None else next(a for a in holder["assets"] if a["id"] == aid)
        ok = (
            fate == "ok"
            and live is not None
            and not (live["state"] == "starter" and self.config.starter_download == "404")
        )
        self.event(
            "request",
            f"GET asset {aid} ({live['name'] if live else '?'}) by its id"
            + ("" if ok else ": failed"),
            kind="asset",
            rid=None if holder is None else holder["id"],
            fate=fate,
            truth_before=self.snap(),
            shown=(live["name"] if live else None, live["data"] if ok else None),
        )
        if not ok:
            return 1, '{"message":"Not Found"}', "gh: Not Found (HTTP 404)\n"
        return 0, live["data"].decode(), ""


def to_json(self, r: dict) -> dict:
    """The tester's own answer, with each asset's update time: an upload's id stands for it
    (a file put again is a new asset, with a new id and a new time)."""
    out = Hub.to_json(self, r)
    for a in out["assets"]:
        a["updated_at"] = f"2026-11-20T12:00:{a['id'] % 60:02d}Z"
    return out


Hub2.to_json = to_json


def expected_latest(state: dict, tag: str) -> str:
    """Decision 4, from GitHub's state: true when TAG is the highest stable version among the
    published releases and itself."""
    released = [
        r["tag_name"]
        for r in state["releases"].values()
        if not r["draft"] and not r["prerelease"] and STABLE.match(r["tag_name"])
    ]

    def key(name):
        return tuple(int(x) for x in name[1:].split("."))

    return "true" if max([*released, tag], key=key) == tag else "false"


def check2(ex):
    """The tester's check, with invariants 1 and 5 restated for the decisions above."""
    violations, notes = THEIR_CHECK(ex)
    hub = ex.hub
    kept = []
    for v in violations:
        if v.invariant == "1" and v.label.startswith("the publication sent "):
            continue  # judged below, with make_latest
        if v.invariant == "5" and v.label.startswith("a line the checker does not know: "):
            continue  # judged below
        if v.invariant == "6" and v.label == BESIDE:
            continue  # judged below, from the attempt's first listing
        kept.append(v)
    for a in ex.attempts:
        mine = [e for e in hub.events if e.attempt == a.number]
        start = next((e for e in mine if e.what == "request" and e.kind == "listing"), None)
        if a.number < 2 or start is None:
            continue
        whole = [
            r["id"]
            for r in hub.releases_for(start.truth_before)
            if not explore.faults(start.truth_before, r["id"], hub, a.files)
        ]
        if whole and any(e.kind == "create" and e.took_effect for e in mine):
            kept.append(explore.Violation("6", BESIDE))
    for e in hub.events:
        if e.what != "request" or e.kind != "patch" or not e.took_effect:
            continue
        sent = dict(e.shown)
        if hub.config.candidate:
            ok = sent == {"draft": False, "make_latest": "false"}
        else:
            ok = sent == {"draft": False, "make_latest": expected_latest(e.truth_before, hub.tag)}
        if not ok:
            kept.append(explore.Violation("1", f"the publication sent {sent}"))
    for a in ex.attempts:
        if a.code == 0 or not a.errors:
            continue
        line = a.errors[0]
        if explore.classify(line)[0] != "unknown":
            continue
        mine = [e for e in hub.events if e.attempt == a.number and e.what == "request"]
        tags = [e for e in mine if e.kind == "tag"]
        if TAG_LINE.search(line):
            last = tags[-1] if tags else None
            if last is None or (last.fate == "ok" and last.shown == RUN_SHA):
                kept.append(explore.Violation("5", "the tag line, on a tag that was read"))
        elif m := MOVED_LINE.search(line):
            last = tags[-1] if tags else None
            if last is None or last.shown != m[2] or m[2] == RUN_SHA:
                kept.append(explore.Violation("5", "the moved line names another commit"))
        elif CHANGED_LINE.search(line):
            reads = [e for e in mine if e.kind == "byid" and e.fate == "ok" and e.shown]
            if len(reads) < 2 or same_release(reads[-2].shown, reads[-1].shown):
                kept.append(explore.Violation("5", "the changed line, on two reads that agree"))
        else:
            kept.append(explore.Violation("5", f"a line the checker does not know: {line[:80]}"))
    return sorted(set(kept), key=str), notes


def same_release(a: dict, b: dict) -> bool:
    keys = ("tag_name", "draft", "prerelease", "name", "body", "assets")
    return all(a.get(k) == b.get(k) for k in keys)


def patch_in(monkeypatch, *modules) -> None:
    """Hub2 and check2 in the tester's modules for one test, and the explorer's memory of
    attempts emptied (it is keyed by the step's text and GitHub's state, not by the model)."""
    import test_rt2_findings as findings

    for module in (explore, findings, *modules):
        if hasattr(module, "Hub"):
            monkeypatch.setattr(module, "Hub", Hub2)
        if hasattr(module, "check"):
            monkeypatch.setattr(module, "check", check2)
    monkeypatch.setattr(explore, "_MEMO", {})
