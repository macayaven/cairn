"""Job `release`'s step "Create the release", run against every state of a small grammar of
what GitHub may hold for the tag, with a person acting before any one request the step's
commands send, a request that fails with or without having been done, and the re-runs after.

Third report, must-fix 2: a retry deleted its draft by id after a person had published it.
Fourth report, must-fix 1: `gh release create`, given files, made a draft, published it and
deleted it by id when the publication's reply was an error, though GitHub had published it.
The failure was inside one command, between its requests, where the test of the last round
never looked: it counted commands. This one counts requests.

The grammar. The tag is a release's (`v1.2.3`) or a candidate's (`v1.2.3-rc.1`). GitHub holds
for it nothing, or one release: a draft or published; its pre-release flag the tag's or the
other; its text carrying this run's mark, another run's (an id that begins with this run's) or
none; its files all three as attested, none, the source archive cut off, or the source
archive of other bytes. Every such release is enumerated, 96 of them, and a seeded generator
draws pairs of them and a release of another tag beside one. A person does nothing, or, just
before the n-th request of the first attempt, for every n it has but the first: publishes the
tag's drafts (GitHub keeps a release's id when it is published), deletes the tag's releases,
replaces a file, attaches a file, or changes the text. What a person did before the first
request is another state of the grammar (the last test here shows it). A fault strikes one
request of the first attempt: the creation refused, or done and its reply lost; the upload
cut after the first file; the publication refused, or done and its reply lost; the listing
after a write still showing what was there before it. Each fault is run alone and with a
person publishing before each request up to the one it strikes, where the attempt ends. An
attempt that stops after a person acted, a fault struck or the step wrote is re-run, twice at
most, with nobody acting and no fault, as a person re-runs a failed job; one that only read
and stopped would read the same again.

GitHub and gh are a stand-in `gh` that keeps the releases as data. The listing goes through
the step's own --jq expression, run by jq. `gh release download` gives a release's files, a
draft's too (shared/fetch.go, 182 to 270: `FetchRelease` "finds a published repository release
by its tagName, or a draft release by its pending tag name"), and refuses to write over a
file that is there (download/download.go, 304 and 439 to 447). `gh release create` is modelled
on gh's source, v2.102.0, pkg/cmd/release/create/create.go: `if hasAssets && !opts.Draft {`
... `draftWhileUploading = true`, `params["draft"] = true` (lines 500 to 512);
`cleanupDraftRelease := func(err error) error { if !draftWhileUploading { return err }` and
then `deleteRelease(httpClient, baseRepo.RepoHost(),
safeurl.NewImmutableSafeURL(newRelease.APIURL))` (533 to 541), called when
`shared.ConcurrentUpload` or `publishRelease` returns an error (550 to 560), and not when
`createRelease` does (514 to 531). The upload deletes a file only when its `ExistingURL` is
set (shared/upload.go, 141 to 146), which a creation's files never have (62 to 70), and no
release ever. So: with files and no --draft it makes a draft, uploads,
publishes, and on a failed upload or publication deletes the release by its id, published or
not; with --draft it makes the draft, uploads, and deletes nothing. `gh api --method PATCH`
on a release's id with `draft=false` publishes that release, as `publishRelease` does
(create/http.go, 182 to 206); any other write is performed and recorded.

What must hold, whatever the state, the person, the fault and the moment:
- the step's writes are the creation, only when its own listing showed no release for the
  tag and once an attempt, and the publication, only of the one release its last listing
  showed as this run's draft with the tag's flag and its files' names, and after a download
  later than that listing gave exactly the files this run attested: nothing else, so no
  delete, no upload onto a release that exists and no other edit;
- a release that is published at any moment is there at the end with the same id, text, flag
  and files, unless the person deleted or changed it (or published the draft `gh release
  create` had just made before that command's own upload, which then reaches it);
- an attempt that succeeds saw, in its last listing, one release for the tag, published, with
  the tag's flag, this run's mark and the files' names, and got from the download after it
  exactly the files this run attested; that is what GitHub holds at the end, unless the person
  acted after that listing, and then the step wrote nothing after the person;
- the releases the step made never leave two for the tag;
- an attempt that stops says why in exactly one `::error::` line of one sentence, unless a
  call to gh failed by itself.

Seeded, so every run checks the same cases. Made-up repository, run and files."""

import itertools
import json
import random
import sys
from pathlib import Path

import pytest
from ci_shell import SYSTEM, run_step
from test_ci_workflow_unit import JOBS

(STEP,) = [s for s in JOBS["release"]["steps"] if s.get("name") == "Create the release"]
SEED, PAIRS = 20261120, 8
RUN_ID = "4242"
MARKS = {
    "this run's mark": f"<!-- cairn-ci-cd run {RUN_ID} -->",
    "another run's mark": f"<!-- cairn-ci-cd run {RUN_ID}0 -->",
    "no mark": "made by hand",
}
TAGS = {"release": "v1.2.3", "candidate": "v1.2.3-rc.1"}
WHEEL, SOURCE, SUMS = (
    "cairn_memory-1.2.3-py3-none-any.whl",
    "cairn_memory-1.2.3.tar.gz",
    "SHA256SUMS",
)
HELD = ("all files", "no file", "a file cut off", "a file of other bytes")
PEOPLE = ("publish", "delete", "replace a file", "attach a file", "change the text")
FAULTS = (
    ("made", "refused"),
    ("made", "lost"),
    ("upload", "cut"),
    ("publication", "refused"),
    ("publication", "lost"),
    ("listing", "stale"),
)

GH = r"""
import copy, json, subprocess, sys
from pathlib import Path

here, args = Path(sys.argv[1]), sys.argv[2:]
state = json.loads((here / "state.json").read_text())
now = state["attempts"][-1]


def by_id(rid):
    return next((r for r in state["releases"] if r["id"] == rid), None)


def named(tag):
    return next((r for r in state["releases"] if r["tag_name"] == tag), None)


def attach(release, name, data):
    # A file put on a release: by a person, or by the upload of the command that made it.
    release["files"][name] = data
    release["assets"] = [{"name": n, "size": len(d) // 2} for n, d in release["files"].items()]
    if not release["draft"]:
        state["public"][str(release["id"])] = copy.deepcopy(release)


def person():
    now["acted"] = now["requests"]
    for release in [r for r in state["releases"] if r["tag_name"] == state["tag"]]:
        if now["person"] == "publish":
            release["draft"] = False
            continue
        if now["person"] == "delete":
            state["releases"].remove(release)
            state["removed"].append(release["id"])
            continue
        if now["person"] == "replace a file" and state["source"] in release["files"]:
            attach(release, state["source"], b"put there by hand".hex())
        elif now["person"] == "attach a file":
            attach(release, "by-hand.txt", b"put there by hand".hex())
        elif now["person"] == "change the text":
            release["body"] = "edited by hand\n"
            if not release["draft"]:  # theirs to change, as the files above
                state["public"][str(release["id"])] = copy.deepcopy(release)


def public():
    for release in state["releases"]:
        if not release["draft"]:
            state["public"].setdefault(str(release["id"]), copy.deepcopy(release))


def request(kind):
    # One request to GitHub: the person may act just before it, and the fault may strike it.
    now["requests"] += 1
    now["log"].append(kind)
    if now["requests"] == now["before"]:
        person()
    public()
    fault = now["fault"]
    if fault and fault[0] == kind and not now["struck"]:
        if kind != "listing" or now["stale"] is not None:
            now["struck"] = True
            return fault[1]
    return None


def answer(code=0, out=""):
    public()
    if code:
        now["failed"] = " ".join(args[:3])
    (here / "state.json").write_text(json.dumps(state))
    sys.stdout.write(out)
    raise SystemExit(code)


def wrote(kind, target=None, fields=()):
    state["writes"].append({
        "attempt": len(state["attempts"]),
        "kind": kind,
        "call": " ".join(args[:4]),
        "fields": list(fields),
        "at": now["requests"],
        "target": copy.deepcopy(target),
        "listed": now["listed"][-1] if now["listed"] else None,
        "given": now["given"][-1] if now["given"] else None,
    })
    now["stale"] = copy.deepcopy(state["releases"])


if args[0] == "api" and "--jq" in args:
    how = request("listing")
    shown = now["stale"] if how == "stale" else state["releases"]
    listed = json.dumps([{k: v for k, v in r.items() if k != "files"} for r in shown])
    done = subprocess.run(
        ["jq", "-r", args[args.index("--jq") + 1]], input=listed, capture_output=True, text=True
    )
    now["listed"].append({"at": now["requests"], "out": done.stdout})
    answer(done.returncode, done.stdout)
if args[0] == "api":
    method = next((args[i + 1] for i, a in enumerate(args) if a in ("-X", "--method")), "POST")
    flags = ("-f", "-F", "--field", "--raw-field")
    fields = [[a, args[i + 1]] for i, a in enumerate(args) if a in flags]
    path = next(a for a in args if a.startswith("repos/"))
    rid = next((r["id"] for r in state["releases"] if path.endswith(f"/releases/{r['id']}")), 0)
    publishes = (
        method == "PATCH"
        and ["-F", "draft=false"] in fields
        and all(f in (["-F", "draft=false"], ["-f", "make_latest=false"]) for f in fields)
    )
    wrote("publish" if publishes else "other", by_id(rid), fields)
    how = request("publication" if publishes else "other")
    target = by_id(rid)
    if how == "refused" or target is None:
        answer(1)  # HTTP 502, or 404
    if method.upper() == "DELETE":
        state["releases"].remove(target)
    elif publishes:
        target["draft"] = False
    if how == "lost":
        answer(1)  # done, and the reply never came
    answer()
if args[:2] == ["release", "download"]:
    request("download")
    target = named(args[2])
    out = Path(args[args.index("--dir") + 1])
    if target is None or any((out / name).exists() for name in target["files"]):
        answer(1)  # no such release, or a file of that name is there and gh keeps it
    out.mkdir(parents=True, exist_ok=True)
    for name, data in target["files"].items():
        (out / name).write_bytes(bytes.fromhex(data))
    now["given"].append({"at": now["requests"], "id": target["id"], "files": target["files"]})
    answer()
if args[:2] == ["release", "create"]:
    notes = args.index("--notes-file") + 1
    names = [a for i, a in enumerate(args) if i > 2 and i != notes and Path(a).is_file()]
    asked = "--draft" in args or "-d" in args
    while_uploading = bool(names) and not asked  # create.go, 500 to 512
    wrote("create")
    if while_uploading:
        request("exists")  # publishedReleaseExists, 503
        if any(r["tag_name"] == args[2] and not r["draft"] for r in state["releases"]):
            answer(1)
    how = request("made")
    if how == "refused":
        answer(1)
    made = state["next"]
    state["next"] += 1
    state["releases"].append({
        "id": made,
        "tag_name": args[2],
        "draft": asked or while_uploading,
        "prerelease": "--prerelease" in args,
        "body": Path(args[notes]).read_text(),
        "assets": [],
        "files": {},
    })
    if how == "lost":
        answer(1)  # 529 to 531: gh holds no release, so it cleans up nothing

    def cleanup():  # cleanupDraftRelease, 533 to 541
        if while_uploading:
            request("cleanup")
            if by_id(made) is not None:  # DELETE of the release's own address: its id
                state["releases"].remove(by_id(made))
        answer(1)

    if names:
        how = request("upload")
        if by_id(made) is None:
            cleanup()  # HTTP 404
        for n, name in enumerate(names):
            data = Path(name).read_bytes()
            if how == "cut" and n == 1:
                attach(by_id(made), name, data[:5].hex())
                cleanup()
            attach(by_id(made), name, data.hex())
        if while_uploading:
            how = request("publication")
            if how == "refused" or by_id(made) is None:
                cleanup()
            by_id(made)["draft"] = False
            public()
            if how == "lost":
                cleanup()
    answer()
if args[0] == "release" and args[1] in ("upload", "edit", "delete", "delete-asset"):
    target = named(args[2])
    wrote("other", target)
    request("other")
    if named(args[2]) is not None and args[1] == "delete":
        state["releases"].remove(named(args[2]))
    answer()
answer(2)
"""


def attested(rng: random.Random) -> dict[str, bytes]:
    return {name: f"{name} {rng.randrange(10**6)}".encode() for name in (WHEEL, SOURCE, SUMS)}


def release(rid, tag, files, draft, flag, mark, held) -> dict:
    """A release as GET repos/OWNER/REPO/releases lists one, and the bytes of its files."""
    has = {
        "all files": files,
        "no file": {},
        "a file cut off": {**files, SOURCE: files[SOURCE][:5]},
        "a file of other bytes": {**files, SOURCE: b"other " + files[SOURCE]},
    }[held]
    return {
        "id": rid,
        "tag_name": tag,
        "draft": draft,
        "prerelease": ("-rc." in tag) == flag,
        "body": f"the notes\n\n{MARKS[mark]}\n",
        "assets": [{"name": name, "size": len(data)} for name, data in has.items()],
        "files": {name: data.hex() for name, data in has.items()},
    }


def variants() -> list[tuple]:
    """(draft, whether the flag is the tag's, mark, files) of every release of the grammar."""
    return list(itertools.product((True, False), (True, False), MARKS, HELD))


def said(variant: tuple) -> str:
    draft, flag, mark, held = variant
    return (
        f"{'draft' if draft else 'published'}, {'its' if flag else 'the other'} flag, {mark},"
        f" {held}"
    )


def states() -> dict[str, list[tuple]]:
    """Every state by name: a list of (id, whether it is this tag's, variant)."""
    found = {"nothing": []}
    for variant in variants():
        found[said(variant)] = [(7, True, variant)]
    rng = random.Random(SEED)
    for first in rng.sample(variants(), PAIRS):
        second = rng.choice(variants())
        found[f"two: {said(first)}; {said(second)}"] = [(7, True, first), (8, True, second)]
        found[f"another tag's beside: {said(first)}"] = [(7, True, first), (9, False, second)]
    return found


STATES = states()
FILES = attested(random.Random(SEED))
WHOLE = {name: data.hex() for name, data in FILES.items()}
DRAFT = "draft, its flag, this run's mark, all files"
PUBLISHED = "published, its flag, this run's mark, all files"


def built(state_name: str, tag: str) -> list[dict]:
    return [
        release(rid, tag if own else "v9.9.9", FILES, *variant)
        for rid, own, variant in STATES[state_name]
    ]


@pytest.fixture(scope="module")
def gh(tmp_path_factory) -> Path:
    """The directory that holds the stand-in, written once: it reads where its state is from
    the environment of the step that calls it."""
    bin_ = tmp_path_factory.mktemp("gh")
    (bin_ / "gh.py").write_text(GH)
    (bin_ / "gh").write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -IS "{bin_}/gh.py" "$STATE" "$@"\n'
    )
    (bin_ / "gh").chmod(0o755)
    return bin_


def attempt(gh: Path, where: Path, state: dict, person="nobody", before=-1, fault=None):
    """One attempt of the step on what GitHub holds in STATE, on a runner of its own, as a
    re-run of a job gets: the files `package` built and nothing else."""
    state["attempts"].append({
        "person": person, "before": before, "fault": fault, "struck": False, "acted": None,
        "requests": 0, "log": [], "listed": [], "given": [], "stale": None, "failed": None,
    })  # fmt: skip
    runner = where / f"attempt-{len(state['attempts'])}"
    (runner / "release").mkdir(parents=True)
    for name, data in FILES.items():
        (runner / "release" / name).write_bytes(data)
    (runner / "release" / "notes.md").write_text("the notes\n")
    (where / "state.json").write_text(json.dumps(state))
    tag = state["tag"]
    done = run_step(
        STEP["run"],
        env={
            "PATH": f"{gh}:{SYSTEM}",
            "STATE": str(where),
            "RUNNER_TEMP": str(runner),
            "GITHUB_REF_NAME": tag,
            "GITHUB_RUN_ID": RUN_ID,
            "CANDIDATE": "true" if "-rc." in tag else "",
            "GH_REPO": "owner/repo",
        },
    )
    return done, json.loads((where / "state.json").read_text())


def history(gh: Path, where: Path, tag: str, releases: list[dict], **first):
    """The first attempt, with the person and the fault given, and then the re-runs of a job
    that stopped, twice at most, with nobody and no fault, for as long as the attempt before
    left GitHub or the step another way than it found them. Each attempt's result with what
    GitHub held after it."""
    where.mkdir(parents=True)
    state = {
        "tag": tag, "source": SOURCE, "releases": releases, "next": 900, "attempts": [],
        "writes": [], "removed": [],
        "public": {str(r["id"]): r for r in releases if not r["draft"]},
    }  # fmt: skip
    runs = []
    for n in range(3):
        done, state = attempt(gh, where, state, **(first if n == 0 else {}))
        runs.append((done, state))
        now = state["attempts"][-1]
        wrote = any(w["attempt"] == n + 1 for w in state["writes"])
        if done.returncode == 0 or not (wrote or now["struck"] or now["acted"] is not None):
            break
    return runs


def ours(state: dict) -> list[dict]:
    return [r for r in state["releases"] if r["tag_name"] == state["tag"]]


def errors(done) -> list[str]:
    return [line for line in done.stdout.splitlines() if line.startswith("::error::")]


def faults(runs: list) -> list[str]:
    state = runs[-1][1]
    tag = state["tag"]
    flag = "true" if "-rc." in tag else "false"
    names = " ".join(sorted(WHOLE))

    def listed_as(draft: str, rid) -> str:
        return f"{rid}\t{draft}\t{flag}\ttrue\t{names}\n"

    found = []
    made = set()
    for write in state["writes"]:
        listed, given, target = write["listed"], write["given"], write["target"]
        if write["kind"] == "create":
            if listed is None or listed["out"] != "":
                found.append("made a release though its listing did not show none for the tag")
            if write["attempt"] in made:
                found.append("made a release twice in one attempt")
            made.add(write["attempt"])
        elif write["kind"] == "publish":
            if target is None or listed is None or listed["out"] != listed_as("true", target["id"]):
                found.append("published what its last listing did not show as this run's draft")
            elif given is None or given["at"] < listed["at"] or given["id"] != target["id"]:
                found.append("published a draft it had not downloaded since it listed it")
            elif given["files"] != WHOLE:
                found.append("HARM: published a draft whose files were not the ones attested")
        else:
            public = target is not None and not target["draft"]
            found.append(
                f"{'HARM: on a published release' if public else 'issued'}: gh {write['call']}"
            )
    there = {str(r["id"]): r for r in state["releases"]}
    for rid, public in state["public"].items():
        if rid not in there and int(rid) not in state["removed"]:
            found.append(f"HARM: published release {rid} is gone")
        elif rid in there and there[rid] != public:
            found.append(f"HARM: published release {rid} was changed")
    if len([r for r in ours(state) if r["id"] >= 900]) > 1:
        found.append("left two releases it made for the tag")
    for n, (done, _) in enumerate(runs):
        now = state["attempts"][n]
        if done.returncode != 0:
            said_ = errors(done)
            if now["failed"] is None and (len(said_) != 1 or ". " in said_[0]):
                found.append(f"stopped with {len(said_)} ::error:: lines of one sentence")
            continue
        listed = now["listed"][-1]
        rid = listed["out"].split("\t")[0]
        given = now["given"][-1] if now["given"] else None
        if listed["out"] != listed_as("false", rid):
            found.append("succeeded though its last listing did not show this run's release")
        elif given is None or given["at"] < listed["at"] or given["files"] != WHOLE:
            found.append("succeeded without the attested files from a download after its listing")
        elif now["acted"] is not None and now["acted"] > listed["at"]:
            if any(w["attempt"] == n + 1 and w["at"] >= now["acted"] - 1 for w in state["writes"]):
                found.append("wrote after the person had changed what it last listed")
        elif [
            (r["id"], r["draft"], r["prerelease"], MARKS["this run's mark"] in r["body"])
            for r in ours(state)
        ] != [(int(rid), False, "-rc." in tag, True)] or ours(state)[0]["files"] != WHOLE:
            found.append("succeeded without exactly one published release of this run's files")
    return found


def everything_found(gh: Path, tmp_path: Path, state_name: str, tag: str) -> dict:
    """The faults of every history from this state: with nobody and no fault; with each person
    acting before the first attempt's n-th request, for every n it has but the first; with
    each fault that has a request to strike; and with each such fault and a person publishing
    before each request up to the one it strikes."""

    def run(name: str, **first):
        runs = history(gh, tmp_path / name.replace(" ", "-"), tag, built(state_name, tag), **first)
        if what := faults(runs):
            found[name] = what
        return runs[-1][1]["attempts"][0]

    found = {}
    log = run("nobody")["log"]
    moments = range(2, len(log) + 1)
    for person in PEOPLE:
        for before in moments:
            first = run(f"{person} before request {before}", person=person, before=before)
            assert first["acted"] == before, (state_name, person, before)
    for fault in FAULTS:
        if fault[0] not in (log[1:] if fault[0] == "listing" else log):
            continue  # (a second listing always follows a write)
        name = f"{fault[0]} {fault[1]}"
        assert run(name, fault=list(fault))["struck"], (state_name, name)
        for before in range(2, log.index(fault[0], 1) + 2):
            run(f"{name}, published before request {before}", fault=list(fault), person="publish",
                before=before)  # fmt: skip
    return found


@pytest.mark.parametrize("kind", TAGS)
@pytest.mark.parametrize("state_name", STATES)
def test_the_release_step_harms_no_release_whatever_happens_before_any_of_its_requests(
    gh, tmp_path, state_name, kind
):
    found = everything_found(gh, tmp_path, state_name, TAGS[kind])
    assert not found, json.dumps(found, indent=1)


# --- the fourth report's regression, and what each fault leaves ----------------------------------

LEFT = (
    "::error::the GitHub release for {tag} is a draft that an earlier attempt of run "
    + RUN_ID
    + " left unfinished, which this job neither deletes nor publishes; delete it on the"
    " repository's releases page and re-run this job, which makes the release again"
)


def kinds(state: dict, n: int) -> list[str]:
    return [w["kind"] for w in state["writes"] if w["attempt"] == n]


@pytest.mark.parametrize("kind", TAGS)
@pytest.mark.parametrize("state_name", ["nothing", DRAFT])
def test_a_publication_done_whose_reply_is_lost_keeps_the_release_and_the_re_run_succeeds(
    gh, tmp_path, state_name, kind
):
    """The reviewer's regression: GitHub publishes the release and the reply is an error. The
    release stays, with its id and its files, and the re-run finds it, compares and goes on."""
    tag = TAGS[kind]
    runs = history(gh, tmp_path / "h", tag, built(state_name, tag), fault=["publication", "lost"])
    (first, after), (retry, state) = runs
    assert first.returncode != 0 and after["attempts"][0]["struck"]
    (public,) = ours(after)
    assert not public["draft"] and public["files"] == WHOLE
    assert public["prerelease"] == ("-rc." in tag) and MARKS["this run's mark"] in public["body"]
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert kinds(state, 2) == [] and ours(state) == [public]
    assert faults(runs) == []


@pytest.mark.parametrize("kind", TAGS)
@pytest.mark.parametrize(
    ("fault", "holds"),
    [
        (["upload", "cut"], {WHEEL: WHOLE[WHEEL], SOURCE: WHOLE[SOURCE][:10]}),
        (["made", "lost"], {}),
    ],
    ids=["an upload cut", "made and the reply lost"],
)
def test_a_draft_whose_files_are_not_whole_is_never_published_and_stops_every_re_run(
    gh, tmp_path, kind, fault, holds
):
    tag = TAGS[kind]
    runs = history(gh, tmp_path / "h", tag, [], fault=fault)
    assert [done.returncode for done, _ in runs] == [1, 1]
    assert errors(runs[1][0]) == [LEFT.format(tag=tag)]
    state = runs[-1][1]
    (draft,) = ours(state)
    assert draft["draft"] and draft["files"] == holds
    assert [w["kind"] for w in state["writes"]] == ["create"]
    assert faults(runs) == []


@pytest.mark.parametrize("kind", TAGS)
@pytest.mark.parametrize(
    ("state_name", "fault", "written"),
    [
        ("nothing", ["made", "refused"], ["create", "create", "publish"]),
        ("nothing", ["publication", "refused"], ["create", "publish", "publish"]),
        (DRAFT, ["publication", "refused"], ["publish", "publish"]),
    ],
)
def test_a_write_github_refused_is_made_by_the_re_run(
    gh, tmp_path, kind, state_name, fault, written
):
    tag = TAGS[kind]
    runs = history(gh, tmp_path / "h", tag, built(state_name, tag), fault=fault)
    assert [done.returncode for done, _ in runs] == [1, 0], runs[-1][0].stdout
    state = runs[-1][1]
    assert [w["kind"] for w in state["writes"]] == written
    assert faults(runs) == []


STALE = (
    "::error::GitHub does not list the release for {tag} as this job has just left it; re-run"
    " this job, which starts from what is listed then"
)


@pytest.mark.parametrize("kind", TAGS)
@pytest.mark.parametrize(
    ("state_name", "first", "then"),
    [("nothing", ["create"], ["publish"]), (DRAFT, ["publish"], [])],
)
def test_a_listing_that_does_not_show_the_step_s_own_write_stops_it_before_it_writes_again(
    gh, tmp_path, kind, state_name, first, then
):
    """One step past: were the release it had just made not listed, a step that made one
    whenever it saw none would make a second."""
    tag = TAGS[kind]
    runs = history(gh, tmp_path / "h", tag, built(state_name, tag), fault=["listing", "stale"])
    (stopped, _), (retry, state) = runs
    assert stopped.returncode == 1 and errors(stopped) == [STALE.format(tag=tag)]
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert (kinds(state, 1), kinds(state, 2)) == (first, then)
    assert faults(runs) == []


@pytest.mark.parametrize("kind", TAGS)
@pytest.mark.parametrize(
    ("person", "why"),
    [
        ("replace a file", f"{SOURCE} is not the file this run attested"),
        ("attach a file", "its files are [SHA256SUMS by-hand.txt cairn_memory-1.2.3"),
    ],
)
def test_a_draft_changed_between_the_comparison_and_the_publication_is_seen_after_it(
    gh, tmp_path, kind, person, why
):
    """The limit: no request compares and publishes at once. A person who changes this run's
    draft after the step compared its files has that draft published; the step looks again
    after it publishes, stops on the difference and deletes nothing."""
    tag = TAGS[kind]
    base = history(gh, tmp_path / "base", tag, built(DRAFT, tag))
    before = base[0][1]["attempts"][0]["log"].index("publication") + 1
    runs = history(gh, tmp_path / "h", tag, built(DRAFT, tag), person=person, before=before)
    assert [done.returncode for done, _ in runs] == [1, 1]
    for done, _ in runs:
        (error,) = errors(done)
        assert "exists and is not one this run can finish: " + why in error
    state = runs[-1][1]
    (public,) = ours(state)
    assert public["id"] == 7 and not public["draft"] and public["files"] != WHOLE
    assert [w["kind"] for w in state["writes"]] == ["publish"]
    assert faults(runs) == []


@pytest.mark.parametrize("kind", TAGS)
def test_the_publication_changes_nothing_on_a_draft_a_person_published_and_fails_on_one_deleted(
    gh, tmp_path, kind
):
    """Why the step's one write to a release that exists is safe, whatever a person did to the
    draft after the step compared it and before the request arrived."""
    tag = TAGS[kind]
    base = history(gh, tmp_path / "base", tag, built(DRAFT, tag))
    before = base[0][1]["attempts"][0]["log"].index("publication") + 1
    held = history(gh, tmp_path / "p", tag, built(DRAFT, tag), person="publish", before=before)
    ((done, state),) = held
    assert done.returncode == 0, done.stdout + done.stderr
    assert kinds(state, 1) == ["publish"] and faults(held) == []
    assert ours(state) == [{**built(DRAFT, tag)[0], "draft": False}]
    runs = history(gh, tmp_path / "d", tag, built(DRAFT, tag), person="delete", before=before)
    (first, after), (retry, state) = runs
    assert first.returncode != 0 and errors(first) == [] and ours(after) == []
    assert after["attempts"][0]["failed"] == "api --method PATCH"
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert kinds(state, 2) == ["create", "publish"] and faults(runs) == []


@pytest.mark.parametrize("kind", TAGS)
def test_a_draft_a_person_publishes_while_its_files_upload_is_never_taken_back(gh, tmp_path, kind):
    """The fourth report's fault one step on: the upload fails after a person published the
    draft the command had just made. gh, asked for a draft, cleans up nothing, and its upload
    removes no file: the release stays public with the files that arrived, and the re-run
    says it cannot finish it and writes nothing."""
    tag = TAGS[kind]
    base = history(gh, tmp_path / "base", tag, [])
    before = base[0][1]["attempts"][0]["log"].index("upload") + 1
    runs = history(
        gh, tmp_path / "h", tag, [], fault=["upload", "cut"], person="publish", before=before
    )
    (first, after), (retry, state) = runs
    assert first.returncode != 0 and after["attempts"][0]["struck"]
    (public,) = ours(after)
    assert (public["id"], public["draft"]) == (900, False)
    assert public["files"] == {WHEEL: WHOLE[WHEEL], SOURCE: WHOLE[SOURCE][:10]}
    (error,) = errors(retry)
    assert retry.returncode == 1 and "exists and is not one this run can finish: its files" in error
    assert ours(state) == [public] and [w["kind"] for w in state["writes"]] == ["create"]
    assert faults(runs) == []


def test_the_grammar_holds_the_reviewers_inputs_and_the_person_does_what_is_said(gh, tmp_path):
    """Both reports' inputs are in the grammar: this run's draft, any files, and a person who
    publishes it before any request; no release, and the publication's reply lost. And the
    stand-in's person is a person, whose act before the step's first request is another state
    of the grammar: publishing a draft gives the step the published release, id kept, and
    deleting gives it nothing."""
    assert len(variants()) == 96 // len(TAGS) and len(STATES) == 1 + 48 + 2 * PAIRS
    assert DRAFT in STATES and PUBLISHED in STATES and ("publication", "lost") in FAULTS
    tag = TAGS["release"]

    def one(name, state_name, **first):
        state = {
            "tag": tag, "source": SOURCE, "releases": built(state_name, tag), "next": 900,
            "attempts": [], "writes": [], "removed": [], "public": {},
        }  # fmt: skip
        (tmp_path / name).mkdir()
        done, after = attempt(gh, tmp_path / name, state, **first)
        return (done.returncode, done.stdout), after["releases"]

    by_hand, after = one("a", DRAFT, person="publish", before=1)
    as_made, as_it_was = one("b", PUBLISHED)
    assert by_hand == as_made and by_hand[0] == 0
    assert after == as_it_was and after[0]["id"] == 7
    assert one("c", DRAFT, person="delete", before=1) == one("d", "nothing")
