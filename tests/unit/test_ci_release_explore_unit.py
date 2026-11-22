"""Stateful randomized invariant exploration of job `release`'s `Create the release` step.

Runs fast in-process against the exact inline `python3 -I` script extracted from ci-cd.yml
using ReleaseHub, verifying every safety & liveness invariant across:
- Full 96-state grammar + seeded two-release pairs (with another tag's release beside them)
- Concurrent user edits / publishes / deletions / tag moves at every request boundary
- Fault injection (create refused/lost-ack, cut upload, publish refused/lost-ack, lag, read errors)
- Up to 2 retries after faults/writes

Uses RT2_FULL=1 (default off on PRs, on in nightly.yml) to scale seeded exploration pairs.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import os
import random

import pytest

from support.github_release_hub import (
    DEFAULT_MARK,
    DEFAULT_RUN_ID,
    OTHER_COMMIT,
    ReleaseHub,
    make_release_record,
    run_release_in_process,
)

pytestmark = pytest.mark.ci_meta

PAIRS = 32 if os.environ.get("RT2_FULL") == "1" else 8
SEED = 20261120
WHEEL = "cairn_memory-{ver}-py3-none-any.whl"
SOURCE = "cairn_memory-{ver}.tar.gz"
SUMS = "SHA256SUMS"

MARKS = {
    "this run's mark": DEFAULT_MARK,
    "another run's mark": f"<!-- cairn-ci-cd run {DEFAULT_RUN_ID}0 -->",
    "no mark": "made by hand",
}
FILE_CASES = ("all-three", "none", "cut-wheel", "corrupt-source")
TAGS = [("v1.2.3", False, ""), ("v1.2.3-rc.1", True, "true")]


def expected_attached(tag: str) -> dict[str, bytes]:
    ver = tag.removeprefix("v").split("-rc.", 1)[0]
    w = WHEEL.format(ver=ver)
    s = SOURCE.format(ver=ver)
    artifacts = {w: b"a wheel", s: b"a source archive", "notes.md": b"the notes\n"}
    sums = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in artifacts.items()).encode()
    return {w: artifacts[w], s: artifacts[s], SUMS: sums}


def make_grammar_release(
    tag: str, rid: int, *, draft: bool, pre: bool, mark_kind: str, files_kind: str
) -> dict:
    ver = tag.removeprefix("v").split("-rc.", 1)[0]
    attached = expected_attached(tag)
    w, s = WHEEL.format(ver=ver), SOURCE.format(ver=ver)
    if files_kind == "none":
        asset_files = {}
        overrides = None
    elif files_kind == "cut-wheel":
        asset_files = attached
        overrides = {w: {"state": "starter", "size": 4, "_bytes": b"part"}}
    elif files_kind == "corrupt-source":
        asset_files = {**attached, s: b"OTHER SOURCE BYT"}
        overrides = None
    else:
        asset_files = attached
        overrides = None
    body = f"the notes\n\n{MARKS[mark_kind]}\n"
    return make_release_record(
        tag,
        pre,
        rid=rid,
        draft=draft,
        body=body,
        asset_files=asset_files,
        asset_overrides=overrides,
    )


def _grammar_states(tag: str) -> list[dict]:
    states = []
    rid = 70
    for draft in (True, False):
        for pre in (False, True):
            for mark_kind in MARKS:
                for files_kind in FILE_CASES:
                    states.append(
                        make_grammar_release(
                            tag,
                            rid,
                            draft=draft,
                            pre=pre,
                            mark_kind=mark_kind,
                            files_kind=files_kind,
                        )
                    )
                    rid += 1
    assert len(states) == 48
    return states


def _check_step_invariants(hub: ReleaseHub, res, tag: str, want_pre: bool) -> None:
    """Verify universal invariants held by every attempt regardless of outcome."""
    assert hub.deletes == [], f"forbidden delete call: {hub.deletes}"
    for w in hub.writes:
        assert w[:2] in (["release", "create"], ["api", "--method"])
    # Never leave two newly created releases from one attempt
    created = [w for w in hub.writes if w[:2] == ["release", "create"]]
    assert len(created) <= 1
    if res.returncode == 0:
        assert "is this run's and is published" in res.stdout
        assert len(hub.calls) >= 1
        assert hub.calls[-1][:2] == ["api", "-H"]
        ours = [r for r in hub.releases if r["tag_name"] == tag]
        assert len(ours) == 1
        rel = ours[0]
        assert rel["draft"] is False and rel["prerelease"] is want_pre
        assert rel["name"] in (None, "", tag)
        assert rel["body"] == f"the notes\n\n{DEFAULT_MARK}\n"
        assert {a["name"]: a["_bytes"] for a in rel["assets"]} == expected_attached(tag)
    else:
        # Either exactly one formatted ::error:: sentence or a direct gh non-zero exit output.
        errors = [ln for ln in res.stdout.splitlines() if ln.startswith("::error::")]
        assert len(errors) <= 1


@pytest.mark.parametrize(("tag", "want_pre", "candidate"), TAGS)
def test_release_step_holds_invariants_across_entire_96_state_grammar(
    tmp_path, tag, want_pre, candidate
):
    """Exhaustively explore all 48 states per tag (96 total) plus empty initial state."""
    for idx, state in enumerate([None, *_grammar_states(tag)]):
        releases = [] if state is None else [copy.deepcopy(state)]
        hub = ReleaseHub(tag=tag, releases=releases)
        run_dir = tmp_path / f"{'rc' if want_pre else 'rel'}-{idx}"
        res = run_release_in_process(hub, run_dir, tag=tag, candidate=candidate)
        _check_step_invariants(hub, res, tag, want_pre)
        # Published existing releases that do not match must never be mutated.
        if state is not None and not state["draft"]:
            matches = (
                state["prerelease"] is want_pre
                and state["body"] == f"the notes\n\n{DEFAULT_MARK}\n"
                and {a["name"]: (a["state"], a["_bytes"]) for a in state["assets"]}
                == {n: ("uploaded", b) for n, b in expected_attached(tag).items()}
            )
            assert (res.returncode == 0) is matches
            assert hub.writes == []


def test_seeded_pairs_with_concurrent_person_actions_and_faults_and_retries(tmp_path):
    """Run seeded grammar pairs + concurrent boundary actions + fault injection + 2 retries."""
    rng = random.Random(SEED)
    all_cases: list[tuple[str, bool, str, list[dict]]] = []
    for tag, want_pre, candidate in TAGS:
        states = _grammar_states(tag)
        rng.shuffle(states)
        other = make_release_record(
            "v0.9.0", False, rid=50, draft=False, body="notes\n", asset_files={}
        )
        for i in range(PAIRS):
            all_cases.append(
                (
                    tag,
                    want_pre,
                    candidate,
                    [copy.deepcopy(states[i]), copy.deepcopy(states[(i + 7) % len(states)])],
                )
            )
        # Single matching or partial states beside another release
        for base in itertools.islice(states, PAIRS):
            all_cases.append(
                (tag, want_pre, candidate, [copy.deepcopy(base), copy.deepcopy(other)])
            )

    person_actions = [
        lambda h: h.by_hand_mutate(lambda rs: [{**r, "draft": False} for r in rs]),
        lambda h: h.by_hand_mutate(lambda rs: []),
        lambda h: h.by_hand_mutate(lambda rs: [{**r, "name": "HandEdited"} for r in rs]),
        lambda h: h.by_hand_mutate(
            lambda rs: [{**r, "body": f"edited\n\n{DEFAULT_MARK}\n"} for r in rs]
        ),
    ]
    faults = [
        {"create_fault": "refused"},
        {"create_fault": "lost-ack"},
        {"create_fault": "cut-after-1"},
        {"publish_fault": "refused"},
        {"publish_fault": "lost-ack"},
        {"made_shown_from_look": 2},
        {"made_shown_from_look": 11},
        {"published_shown_from_look": 2},
        {"read_fails_at": {1: (502, "Bad Gateway")}},
    ]

    for idx, (tag, want_pre, candidate, initial) in enumerate(all_cases):
        action = rng.choice(person_actions)
        fault = rng.choice(faults)
        hub = ReleaseHub(tag=tag, releases=initial, **copy.deepcopy(fault))

        # Determine request count first or trigger person action on a boundary call
        fired = {"done": False}
        trigger_at = rng.randint(1, 5)

        def boundary_hook(
            h: ReleaseHub, _args: list[str], act=action, state=fired, target=trigger_at
        ) -> None:
            if not state["done"] and len(h.calls) >= target:
                state["done"] = True
                act(h)

        hub.after_call = boundary_hook
        work = tmp_path / f"case-{idx}"
        res = run_release_in_process(hub, work, tag=tag, candidate=candidate)
        _check_step_invariants(hub, res, tag, want_pre)

        # Retries with cleared faults preserve safety
        if res.returncode != 0:
            for retry_kw in (
                {
                    "after_call": None,
                    "create_fault": None,
                    "publish_fault": None,
                    "made_shown_from_look": 1,
                    "published_shown_from_look": 1,
                    "read_fails_at": {},
                },
                {"after_call": None},
            ):
                for k, v in retry_kw.items():
                    setattr(hub, k, v)
                res_r = run_release_in_process(hub, work, tag=tag, candidate=candidate)
                _check_step_invariants(hub, res_r, tag, want_pre)
                if res_r.returncode == 0:
                    break


@pytest.mark.parametrize(("tag", "want_pre", "candidate"), TAGS)
def test_concurrent_publish_or_tag_move_across_every_request_boundary(
    tmp_path, tag, want_pre, candidate
):
    """Replay concurrent publish / tag move at every request boundary of a fresh run."""
    baseline_hub = ReleaseHub(tag=tag)
    base_res = run_release_in_process(baseline_hub, tmp_path / "base", tag=tag, candidate=candidate)
    assert base_res.returncode == 0
    total_calls = len(baseline_hub.calls)

    for boundary_idx in range(1, total_calls + 1):
        # 1. Person publishes draft at boundary_idx -> never deleted; published state preserved
        hub_pub = ReleaseHub(tag=tag)

        def publish_at(h: ReleaseHub, _args: list[str], b=boundary_idx) -> None:
            if len(h.calls) == b:
                for r in h.releases:
                    r["draft"] = False

        hub_pub.before_call = publish_at
        res_pub = run_release_in_process(
            hub_pub, tmp_path / f"pub-{boundary_idx}", tag=tag, candidate=candidate
        )
        _check_step_invariants(hub_pub, res_pub, tag, want_pre)

        # 2. Tag moved at boundary_idx -> never succeeds when tag points elsewhere at final check
        hub_tag = ReleaseHub(tag=tag)

        def move_at(h: ReleaseHub, _args: list[str], b=boundary_idx) -> None:
            if len(h.calls) == b:
                h.tag_sha = OTHER_COMMIT

        hub_tag.before_call = move_at
        res_tag = run_release_in_process(
            hub_tag, tmp_path / f"tag-{boundary_idx}", tag=tag, candidate=candidate
        )
        assert res_tag.returncode != 0
        _check_step_invariants(hub_tag, res_tag, tag, want_pre)
