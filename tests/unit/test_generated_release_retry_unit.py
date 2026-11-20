"""Job `release`'s step "Create the release", run against every state of a small grammar of
what GitHub may hold for the tag, with a person acting between any two of the step's calls
(third report, must-fix 2: a retry deleted its draft by id after a person had published it).

The grammar. The tag is a release's (`v1.2.3`) or a candidate's (`v1.2.3-rc.1`). GitHub holds
for it nothing, or one release: a draft or published; its pre-release flag the tag's or the
other; its text carrying this run's mark, another run's (an id that begins with this run's) or
none; its files all three as attested, none, the source archive cut off, or the source
archive of other bytes. Every such release is enumerated, 96 of them, and a seeded generator
draws pairs of them and a release of another tag beside one. A person does nothing, publishes
the tag's drafts (GitHub keeps a release's id when it is published) or deletes the tag's
releases, between two of the step's calls to gh: after its k-th and before its next, for
every such k. What a person did before the step's first call is another state of the grammar
(the last test here shows it for both acts).

GitHub is a stand-in `gh` that keeps the releases as data and answers the step's calls from
them: the listing through the step's own --jq expression, run by jq; a download; and any
write, which it records with what the write's target was at that moment. `gh release create`
is one call there: the real one makes a draft, uploads and publishes in separate requests
(its help says so), and what it does to its own draft when one of them fails is gh's and is
not modelled.

What must hold, whatever the state, the person and the moment:
- the step's only write is `gh release create`, issued only when its own listing showed no
  release for the tag: it never deletes, uploads to or edits a release that exists;
- a release that is published at any moment is there at the end with the same id, text, flag
  and files, unless the person deleted it;
- a step that succeeds leaves exactly one release for the tag: published, with the tag's
  pre-release flag and this run's mark, holding byte for byte the files this run attested;
- a step that stops has written nothing, and says why in exactly one `::error::` line of one
  sentence, unless a call to gh failed by itself.

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
PEOPLE = ("publish", "delete")

GH = r"""
import json, subprocess, sys
from pathlib import Path

here, args = Path(sys.argv[1]), sys.argv[2:]
state = json.loads((here / "state.json").read_text())
of_tag = [r for r in state["releases"] if r["tag_name"] == state["tag"]]
state["calls"] += 1
if state["calls"] == state["after"] + 1:  # the person, between two of the step's calls
    state["acted"] = True
    for release in of_tag:
        if state["person"] == "publish":
            release["draft"] = False
        elif state["person"] == "delete":
            state["releases"].remove(release)
            state["removed"].append(release["id"])
for release in state["releases"]:
    if not release["draft"]:
        state["public"].setdefault(str(release["id"]), release)


def answer(code=0, out=""):
    if code:
        state["failed"] = " ".join(args[:3])
    (here / "state.json").write_text(json.dumps(state))
    sys.stdout.write(out)
    raise SystemExit(code)


def write(target, create=False):
    state["writes"].append({
        "call": " ".join(args[:4]),
        "create": create,
        "on a published release": target is not None and not target["draft"],
        "listing before it": state["listed"][-1] if state["listed"] else None,
    })


def named(tag):
    return next((r for r in state["releases"] if r["tag_name"] == tag), None)


WRITES = ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input")
if args[0] == "api" and not any(flag in args for flag in WRITES):
    listed = json.dumps([{k: v for k, v in r.items() if k != "files"} for r in state["releases"]])
    done = subprocess.run(
        ["jq", "-r", args[args.index("--jq") + 1]], input=listed, capture_output=True, text=True
    )
    state["listed"].append(done.stdout)
    answer(done.returncode, done.stdout)
if args[0] == "api":
    path = next(a for a in args if a.startswith("repos/"))
    target = next((r for r in state["releases"] if path.endswith(f"/releases/{r['id']}")), None)
    write(target)
    if target is None:
        answer(1)  # HTTP 404
    if "DELETE" in args:
        state["releases"].remove(target)
    answer()
if args[:2] == ["release", "download"]:
    target = named(args[2])
    if target is None:
        answer(1)
    out = Path(args[args.index("--dir") + 1])
    out.mkdir(parents=True, exist_ok=True)
    for name, data in target["files"].items():
        (out / name).write_bytes(bytes.fromhex(data))
    answer()
if args[:2] == ["release", "create"]:
    write(None, create=True)
    notes = args.index("--notes-file") + 1
    names = [a for i, a in enumerate(args) if i > 2 and i != notes and Path(a).is_file()]
    files = {name: Path(name).read_bytes().hex() for name in names}
    state["releases"].append({
        "id": 900 + state["calls"],
        "tag_name": args[2],
        "draft": False,
        "prerelease": "--prerelease" in args,
        "body": Path(args[notes]).read_text(),
        "assets": [{"name": name, "size": len(data) // 2} for name, data in files.items()],
        "files": files,
    })
    answer()
if args[0] == "release" and args[1] in ("upload", "edit", "delete", "delete-asset"):
    target = named(args[2])
    write(target)
    if target is not None and args[1] == "delete":
        state["releases"].remove(target)
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


def run(gh: Path, where: Path, tag: str, releases: list[dict], person: str, after: int):
    work = where / "release"
    work.mkdir(parents=True)
    for name, data in FILES.items():
        (work / name).write_bytes(data)
    (work / "notes.md").write_text("the notes\n")
    state = {
        "tag": tag, "releases": releases, "calls": 0, "person": person, "after": after,
        "acted": False, "removed": [], "writes": [], "listed": [], "failed": None,
        "public": {str(r["id"]): r for r in releases if not r["draft"]},
    }  # fmt: skip
    (where / "state.json").write_text(json.dumps(state))
    done = run_step(
        STEP["run"],
        env={
            "PATH": f"{gh}:{SYSTEM}",
            "STATE": str(where),
            "RUNNER_TEMP": str(where),
            "GITHUB_REF_NAME": tag,
            "GITHUB_RUN_ID": RUN_ID,
            "CANDIDATE": "true" if "-rc." in tag else "",
            "GH_REPO": "owner/repo",
        },
    )
    return done, json.loads((where / "state.json").read_text())


def faults(done, state: dict, tag: str) -> list[str]:
    found = []
    for write in state["writes"]:
        if not write["create"]:
            what = "HARM: on a published release" if write["on a published release"] else "issued"
            found.append(f"{what}: gh {write['call']}")
        elif write["listing before it"] != "":
            found.append("made a release though its listing showed one for the tag")
    there = {str(r["id"]): r for r in state["releases"]}
    for rid, public in state["public"].items():
        if rid not in there and int(rid) not in state["removed"]:
            found.append(f"HARM: published release {rid} is gone")
        elif rid in there and there[rid] != public:
            found.append(f"HARM: published release {rid} was changed")
    if done.returncode == 0:
        ours = [r for r in state["releases"] if r["tag_name"] == tag]
        whole = {name: data.hex() for name, data in FILES.items()}
        if not (
            len(ours) == 1
            and not ours[0]["draft"]
            and ours[0]["prerelease"] == ("-rc." in tag)
            and MARKS["this run's mark"] in ours[0]["body"]
            and ours[0]["files"] == whole
        ):
            found.append("succeeded without exactly one published release of this run's files")
        return found
    if state["writes"]:
        found.append("stopped after it had written")
    errors = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    if state["failed"] is None and (len(errors) != 1 or ". " in errors[0]):
        found.append(f"stopped with {len(errors)} ::error:: lines of one sentence")
    return found


def everything_found(gh: Path, tmp_path: Path, state_name: str, tag: str) -> dict:
    """The faults of every run of the step from this state: with nobody, and with each person
    acting after the step's k-th call and before its next, for every k it has."""
    done, state = run(gh, tmp_path / "nobody", tag, built(state_name, tag), "nobody", -1)
    found = {"nobody": faults(done, state, tag)}
    for person in PEOPLE:
        for after in range(1, state["calls"]):
            done, acted = run(
                gh, tmp_path / f"{person}-{after}", tag, built(state_name, tag), person, after
            )
            assert acted["acted"], (state_name, person, after)
            found[f"{person} after call {after}"] = faults(done, acted, tag)
    return {when: what for when, what in found.items() if what}


@pytest.mark.parametrize("kind", TAGS)
@pytest.mark.parametrize("state_name", STATES)
def test_the_release_step_harms_no_release_whatever_a_person_does_between_its_calls(
    gh, tmp_path, state_name, kind
):
    found = everything_found(gh, tmp_path, state_name, TAGS[kind])
    assert not found, json.dumps(found, indent=1)


def test_the_grammar_holds_the_reviewer_s_input_and_the_person_does_what_is_said(gh, tmp_path):
    """The reviewer's input is in the grammar: this run's draft of a release, any files, and a
    person who publishes it after the step's second call. And the stand-in's person is a
    person, whose act before the step's first call is another state of the grammar: publishing
    a draft gives the step the published release, id kept, and deleting gives it nothing."""
    assert len(variants()) == 96 // len(TAGS) and len(STATES) == 1 + 48 + 2 * PAIRS
    draft = "draft, its flag, this run's mark, all files"
    published = "published, its flag, this run's mark, all files"
    assert draft in STATES and published in STATES
    tag = TAGS["release"]
    by_hand, after = run(gh, tmp_path / "a", tag, built(draft, tag), "publish", 0)
    as_made, as_it_was = run(gh, tmp_path / "b", tag, built(published, tag), "nobody", -1)
    assert (by_hand.returncode, by_hand.stdout) == (as_made.returncode, as_made.stdout)
    assert after["releases"] == as_it_was["releases"] and after["releases"][0]["id"] == 7
    gone, _ = run(gh, tmp_path / "c", tag, built(draft, tag), "delete", 0)
    none, _ = run(gh, tmp_path / "d", tag, [], "nobody", -1)
    assert (gone.returncode, gone.stdout) == (none.returncode, none.stdout)
