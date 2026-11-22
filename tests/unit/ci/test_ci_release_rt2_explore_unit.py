"""Exploration suite for the release step using support.rt2_explore."""

from __future__ import annotations

import random

import pytest

from support.rt2_explore import (
    FULL,
    LAGS,
    PRE_FIX,
    Report,
    budget,
    check,
    execute,
    explore,
    full_only,
    render,
    summary,
    wander,
)
from support.rt2_github import (
    KINDS,
    TAGS,
    Config,
    current_run,
    pinned_run,
)

pytestmark = [pytest.mark.ci_meta, pytest.mark.fuzz]


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
