"""K2 acceptance tests: a Linear or a GitHub source can start with what finished lately.

Written before the implementation; frozen when it is merged.

A Linear source (cairn.sources.linear) and a GitHub source (cairn.sources.github) read what
is finished from the moment they are switched on. That stays the default: a source does not
pour an unknown amount of history into a project unless someone asks it to. This is how one
asks.

The setting
- `backfill_days`, a setting of both kinds: a whole number from 0 to 365, 0 unless given
  (`check` fills it in). Anything else (a negative number, more than 365, a string, a
  fraction, true or false, null) is refused with a ValueError that names `backfill_days`.
- With 0 a source does what it did before K2: tests/acceptance/test_c10_linear.py and
  tests/acceptance/test_c11_github.py pass as they are.

What it reads
- With `backfill_days` N, a source that is switched on also reads what finished in the N
  days before the switch-on. Call the switch-on less N days the floor: nothing that finished
  before the floor is written. What it reads before the switch-on is what the kind reads
  after it, only earlier: for Linear, an issue completed or cancelled; for GitHub, a pull
  request merged, an issue closed as completed or not planned, a release published. Each is
  written once, once it has settled (`settle_minutes`), as a page of its own, oldest finish
  first.
- It is a backlog, read over several pulls, at most 25 items a pull, nothing skipped to
  catch up: 60 items take three pulls (25, 25, 10), and what finishes meanwhile takes its
  place in finish order. The cursor stays bounded: once the backlog is written and
  `lookback_days` have passed, it holds nothing of it.
- An item written once is never written again: each draft names its item and the runner
  writes an item once for as long as the source exists. An issue finished 80 days before
  the switch-on, read with a backfill of 90 days and `lookback_days` at its default (30),
  is written once however many runs follow, though the kind's own record of it is let go.
- Off and on again: the floor moves with the new switch-on. What was written before is not
  written again; what finished while the source was off, and is not before the new floor,
  is read; what finished while it was off and before the new floor is not.

GitHub's two ways of finding
- The issues listing's first `since` and the daily check's first window both start at the
  floor (each at most a day before it), so an item is found whether or not its close moved
  its update time. Releases are taken from the floor too.
- A first window that long can find more than search returns (1,000 results): the pull
  then says so among its skips, as it does for any window, and the listing still reads
  everything whose update time moved, so every item is written once, in finish order.

What a person is told
- `describe(config)` (the Sources page's "Reads" line) and the switch-on sentence
  (`SourceKind.switching_on(product, config)`, said beside an off source) say the backfill
  when it is not 0: "what finished in the 30 days before it is switched on". With 0 they
  say what they said before K2, and `switching_on()` without a config is the kind's own
  sentence as before. The Sources page shows both.
- `source-add --help` lists `backfill_days` among each kind's settings.

The stand-ins are the frozen files' own: `FakeLinear` from test_c10_linear.py and
`FakeGitHub` from test_c11_github.py, imported as modules (the acceptance directory is on
the test path, as for test_h3b_oauth_strict.py). Their checks (a request that breaks the
contract) are run after every test here too. Nothing leaves the process; everything is
made up, and every clock is in 2099.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import test_c10_linear as c10
import test_c11_github as c11
from starlette.testclient import TestClient

from cairn.server import create_app, main
from cairn.sources import KINDS, run_source, source_client
from cairn.store import Store
from cookie_names import session_cookie

MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
WEB = "http://127.0.0.1:8765"
PHRASE = "what finished in the 30 days before it is switched on"


# The stand-ins, checked after every test


@pytest.fixture(autouse=True)
def every_request_kept_the_contract():
    c10._MADE.clear()
    made: list[c11.FakeGitHub] = []
    yield made
    for stand_in in c10._MADE:
        assert stand_in.violations == [], stand_in.violations
    for stand_in in made:
        assert stand_in.problems() == []


@pytest.fixture
def lfake():
    return c10.FakeLinear(c10.Clock(c10.NOW))


@pytest.fixture
def gfake(every_request_kept_the_contract):
    fake = c11.FakeGitHub(c11.Clock(c11.NOW))
    every_request_kept_the_contract.append(fake)
    return fake


LINEAR, GITHUB = KINDS["linear"], KINDS["github"]
L_ON = c10.SINCE  # the Linear source's switch-on: 2099-03-03 12:00, thirty days before NOW
G_ON = c11.SINCE  # the GitHub source's: 2099-03-01 03:00, nine days before NOW


# Linear, through its pull


def four_issues(fake) -> None:
    """Issues finished 40, 20 and 2 days before the switch-on and 1 day after it."""
    fake.issue(1, "Forty days before", finished=L_ON - 40 * DAY)
    fake.issue(2, "Twenty days before", team="OPS", finished=L_ON - 20 * DAY)
    fake.issue(3, "Two days before", finished=L_ON - 2 * DAY, state="Won't fix")
    fake.issue(4, "A day after", finished=L_ON + DAY)


def first_bound(fake) -> datetime:
    """The bound the first listing of the last pull asked from."""
    return fake.listings()[0].T


def test_linear_by_default_reads_nothing_finished_before_the_switch_on(lfake):
    four_issues(lfake)
    assert LINEAR.check(c10.config())["backfill_days"] == 0
    got = c10.pull(lfake, c10.opened(LINEAR, lfake, since=L_ON))
    assert lfake.numbers(got) == [4]
    assert L_ON - DAY <= first_bound(lfake) <= L_ON


def test_linear_with_a_backfill_reads_what_finished_since_the_floor_oldest_first(lfake):
    four_issues(lfake)
    got = c10.pull(lfake, c10.opened(LINEAR, lfake, since=L_ON, backfill_days=30))
    assert lfake.numbers(got) == [2, 3, 4]  # never the one finished forty days before
    floor = L_ON - 30 * DAY
    assert floor - DAY <= first_bound(lfake) <= floor
    assert all(draft.topic is None for draft in got.drafts)  # a page of its own each
    again = c10.pull(lfake, c10.opened(LINEAR, lfake, since=L_ON, backfill_days=30), got.cursor)
    assert again.drafts == []


def test_linear_reads_sixty_backfilled_issues_in_three_pulls_of_at_most_25(lfake):
    floor = L_ON - 30 * DAY
    for n in range(1, 61):
        lfake.issue(n, f"Backlog {c10.tag(n)}", finished=floor + n * 7 * HOUR)
    lfake.issue(61, "Finished after the switch-on", finished=L_ON + DAY)
    read = c10.reader(LINEAR, lfake, since=L_ON, backfill_days=30, comments=False, settle_minutes=0)
    cursor, sizes, written = None, [], []
    for k in range(5):
        lfake.clock.now = c10.NOW + k * 10 * MINUTE
        if k == 1:  # finished while the backlog is read: it takes its place in finish order
            lfake.issue(62, "Finished meanwhile", finished=c10.NOW + 5 * MINUTE)
        got = read(cursor)
        cursor = got.cursor
        sizes.append(len(got.drafts))
        written += lfake.numbers(got)
    assert sizes == [25, 25, 12, 0, 0]
    assert written == list(range(1, 63))


def test_linear_a_backfilled_issue_still_settling_waits(lfake):
    on = c10.NOW - 5 * MINUTE  # switched on just now
    lfake.issue(1, "Settled", finished=on - 3 * DAY)
    lfake.issue(2, "Finished just before the switch-on", finished=c10.NOW - 10 * MINUTE)
    read = c10.reader(LINEAR, lfake, since=on, backfill_days=30)
    first = read()
    assert lfake.numbers(first) == [1]
    lfake.clock.now = c10.NOW + 25 * MINUTE
    second = read(first.cursor)
    assert lfake.numbers(second) == [2]


def test_linear_the_cursor_holds_nothing_of_the_backlog_once_lookback_days_have_passed():
    stand_in, twin = c10.FakeLinear(c10.Clock(c10.NOW)), c10.FakeLinear(c10.Clock(c10.NOW))
    for n in range(1, 301):
        stand_in.issue(n, f"Old work {c10.tag(n)}", finished=L_ON - 364 * DAY + n * DAY)
    read = c10.reader(LINEAR, stand_in, since=L_ON, backfill_days=365, comments=False)
    alone = c10.reader(LINEAR, twin, since=L_ON, comments=False)
    cursor = empty = None
    counted: list[int] = []
    for k in range(60):
        stand_in.clock.now = twin.clock.now = c10.NOW + k * 12 * HOUR
        got = read(cursor)
        cursor = got.cursor
        counted += stand_in.numbers(got)
        empty = alone(empty).cursor
        for issue in stand_in.issues.values():
            assert issue.id.lower() not in cursor.lower() and issue.title not in cursor
    assert counted == list(range(1, 301))
    assert len(cursor) <= len(empty) + 40  # no more than a source with nothing to read


# Linear, through the runner


def linear_world(tmp_path, fake, **settings) -> SimpleNamespace:
    """A workspace with a project "linear" and a Linear source added off, then switched on at
    L_ON with `settings`."""
    clock = c10.Clock(L_ON - DAY)
    store = Store(tmp_path / "cairn.db", clock=clock)
    store.create_project("linear")
    store.add_source("linear", "linear", c10.config(projects=[], **settings))
    clock.now = L_ON
    store.enable_source("linear", projects=["linear"])
    http = source_client(httpx.MockTransport(fake.handler))
    return SimpleNamespace(store=store, clock=clock, http=http)


def linear_run(world, fake, at: datetime):
    fake.clock.now = at
    fake.new_pull()
    return asyncio.run(
        run_source(world.store, "linear", http=world.http, env=c10.ENV, clock=fake.clock)
    )


def linear_written(world, fake) -> list[int]:
    project = world.store.get_project("linear").id
    return [fake.number_of(c.source_ref) for c in world.store.list_notes(project)]


@pytest.mark.parametrize("backfill", [0, 30])
def test_linear_through_the_runner_writes_what_finished_since_the_floor_once(
    tmp_path, lfake, backfill
):
    four_issues(lfake)
    world = linear_world(tmp_path, lfake, backfill_days=backfill)
    try:
        report = linear_run(world, lfake, c10.NOW)
        expected = [2, 3, 4] if backfill else [4]
        assert report.saved == len(expected)
        assert linear_written(world, lfake) == expected  # oldest finish first
        for hours in (1, 30, 300):
            assert linear_run(world, lfake, c10.NOW + hours * HOUR).saved == 0
        assert linear_written(world, lfake) == expected
    finally:
        world.store.close()


def test_linear_an_issue_finished_80_days_before_is_written_once_across_many_runs(tmp_path, lfake):
    lfake.issue(1, "Eighty days before", finished=L_ON - 80 * DAY)
    lfake.issue(2, "Ten days before", finished=L_ON - 10 * DAY)
    world = linear_world(tmp_path, lfake, backfill_days=90)
    assert world.store.get_source("linear").config["lookback_days"] == 30
    try:
        saved = 0
        for half_days in range(120):  # two runs a day for sixty days
            saved += linear_run(world, lfake, L_ON + HOUR + half_days * 12 * HOUR).saved
        assert saved == 2 and linear_written(world, lfake) == [1, 2]
    finally:
        world.store.close()


def test_linear_switched_off_and_on_again_moves_the_floor(tmp_path, lfake):
    lfake.issue(1, "Ten days before the first switch-on", finished=L_ON - 10 * DAY)
    lfake.issue(2, "A day after it", finished=L_ON + DAY)
    world = linear_world(tmp_path, lfake, backfill_days=30)
    store = world.store
    try:
        assert linear_run(world, lfake, L_ON + 2 * DAY).saved == 2

        world.clock.now = L_ON + 3 * DAY  # off for two days: the new floor covers 1 and 2
        store.disable_source("linear")
        lfake.issue(3, "Finished while it was off", finished=L_ON + 4 * DAY)
        world.clock.now = L_ON + 5 * DAY
        store.enable_source("linear")
        assert linear_run(world, lfake, L_ON + 6 * DAY).saved == 1
        assert linear_written(world, lfake) == [1, 2, 3]

        world.clock.now = L_ON + 7 * DAY  # off for 43 days: the new floor is L_ON + 20 days
        store.disable_source("linear")
        lfake.issue(4, "Off, before the new floor", finished=L_ON + 10 * DAY)
        lfake.issue(5, "Off, after the new floor", finished=L_ON + 40 * DAY)
        world.clock.now = L_ON + 50 * DAY
        store.enable_source("linear")
        lfake.issue(6, "On again", finished=L_ON + 51 * DAY)
        assert linear_run(world, lfake, L_ON + 53 * DAY).saved == 2
        for days in (54, 60, 90):
            assert linear_run(world, lfake, L_ON + days * DAY).saved == 0
        assert linear_written(world, lfake) == [1, 2, 3, 5, 6]
    finally:
        store.close()


# GitHub, through its pull


def finished_items(fake, what: str, created: datetime, base: int = 0) -> list[str]:
    """Four items of one kind, finished 40, 20 and 2 days before the switch-on and 1 day
    after it, numbered from `base` + 1; their links, in that order. Issues and pull requests
    are opened at `created`."""
    links = []
    for k, at in enumerate((-40 * DAY, -20 * DAY, -2 * DAY, DAY), start=1):
        n = base + k
        if what == "pulls":
            fake.pull_request(n, f"Change {n}", created=created, merged=G_ON + at)
            links.append(c11.pr_ref(n))
        elif what == "issues":
            reason = "not_planned" if k == 3 else "completed"
            fake.issue(n, f"Report {n}", created=created, closed=G_ON + at, reason=reason)
            links.append(c11.issue_ref(n))
        else:
            fake.release(n, f"v{n}.0.0", f"Release {n}", published=G_ON + at)
            links.append(c11.release_ref(f"v{n}.0.0"))
    return links


def g_reader(fake, **settings):
    return c11.reader(GITHUB, fake, since=G_ON, comments=False, **settings)


def g_read_all(fake, read, pulls: int = 3) -> list[str]:
    cursor, written = None, []
    for k in range(pulls):
        fake.clock.now = c11.NOW + k * 10 * MINUTE
        got = read(cursor)
        cursor = got.cursor
        written += c11.refs(got)
    return written


WHAT = ["pulls", "issues", "releases"]
MOVES = pytest.mark.parametrize(
    "moves", [True, False], ids=["close-moves-updated", "close-keeps-updated"]
)


@MOVES
@pytest.mark.parametrize("what", WHAT)
def test_github_by_default_reads_nothing_finished_before_the_switch_on(gfake, what, moves):
    gfake.moves = moves
    links = finished_items(gfake, what, created=G_ON - 60 * DAY)
    assert GITHUB.check(c11.config())["backfill_days"] == 0
    assert g_read_all(gfake, g_reader(gfake)) == links[3:]


@MOVES
@pytest.mark.parametrize("what", WHAT)
def test_github_with_a_backfill_reads_what_finished_since_the_floor_oldest_first(
    gfake, what, moves
):
    gfake.moves = (
        moves  # opened long before: when a close keeps updated_at, only the check finds them
    )
    links = finished_items(gfake, what, created=G_ON - 60 * DAY)
    read = g_reader(gfake, backfill_days=30)
    gfake.clock.now = c11.NOW
    first = read()
    assert c11.refs(first) == links[1:]  # never the one finished forty days before
    floor = G_ON - 30 * DAY
    (listing, *_) = gfake.asked("issues")
    assert floor - DAY <= c11.parsed(listing.params["since"]) <= floor
    windows = c11.searches(gfake)
    assert set(windows) == {c11.PR_QUERY, c11.ISSUE_QUERY}
    assert all(floor - DAY <= bound <= floor for b in windows.values() for bound in b)
    assert gfake.asked("releases")
    gfake.clock.now = c11.NOW + 10 * MINUTE
    assert read(first.cursor).drafts == []


@MOVES
def test_github_reads_sixty_backfilled_items_in_three_pulls_of_at_most_25(gfake, moves):
    gfake.moves, gfake.page_cap = moves, 16
    floor = G_ON - 30 * DAY
    opened = floor - 10 * DAY
    expected = []
    for i in range(60):
        at = floor + (i + 1) * 2 * HOUR
        if i % 6 in (0, 3, 5):
            gfake.pull_request(100 + i, f"Change {i}", created=opened, merged=at)
            expected.append(c11.pr_ref(100 + i))
        elif i % 6 in (1, 4):
            gfake.issue(100 + i, f"Report {i}", created=opened, closed=at)
            expected.append(c11.issue_ref(100 + i))
        else:
            gfake.release(i, f"v3.{i}.0", f"Batch {i}", published=at)
            expected.append(c11.release_ref(f"v3.{i}.0"))
    gfake.issue(200, "Closed after the switch-on", created=opened, closed=G_ON + DAY)
    read = g_reader(gfake, backfill_days=30, settle_minutes=0)
    cursor, sizes, written = None, [], []
    for k in range(5):
        gfake.clock.now = c11.NOW + k * 10 * MINUTE
        if k == 1:  # merged while the backlog is read: it takes its place in finish order
            gfake.pull_request(201, "Merged meanwhile", created=opened, merged=c11.NOW + MINUTE)
        got = read(cursor)
        cursor = got.cursor
        sizes.append(len(got.drafts))
        written += c11.refs(got)
    assert sizes == [25, 25, 12, 0, 0]
    assert written == [*expected, c11.issue_ref(200), c11.pr_ref(201)]


def test_github_a_backfilled_item_still_settling_waits(gfake):
    on = c11.NOW - 5 * MINUTE  # switched on just now
    gfake.issue(1, "Settled", closed=on - 3 * DAY)
    gfake.issue(2, "Closed just before the switch-on", closed=c11.NOW - 10 * MINUTE)
    read = c11.reader(GITHUB, gfake, since=on, comments=False, backfill_days=30)
    first = read()
    assert c11.refs(first) == [c11.issue_ref(1)]
    gfake.clock.now = c11.NOW + 25 * MINUTE
    assert c11.refs(read(first.cursor)) == [c11.issue_ref(2)]


def test_github_a_first_window_past_what_search_returns_says_so_and_the_listing_reads_the_rest(
    gfake,
):
    for n in range(1, 1_031):
        gfake.issue(n, f"Report {n}", created=G_ON - 300 * DAY, closed=G_ON - 200 * DAY + n * HOUR)
    read = g_reader(gfake, backfill_days=365, items=["issues"])
    cursor, written, reasons = None, [], []
    for k in range(45):
        gfake.clock.now = c11.NOW + k * 10 * MINUTE
        got = read(cursor)
        cursor = got.cursor
        written += c11.refs(got)
        reasons += [skip.reason for skip in got.skipped]
    assert written == [c11.issue_ref(n) for n in range(1, 1_031)]  # each once, in order
    assert any("search" in reason.lower() for reason in reasons), reasons  # it says so


def test_github_the_cursor_holds_nothing_of_the_backlog_once_lookback_days_have_passed(
    every_request_kept_the_contract,
):
    stand_in, twin = c11.FakeGitHub(c11.Clock(c11.NOW)), c11.FakeGitHub(c11.Clock(c11.NOW))
    every_request_kept_the_contract += [stand_in, twin]
    for n in range(1, 201):
        stand_in.issue(n, f"Old report {n}", created=G_ON - 365 * DAY, closed=G_ON - n * DAY)
    read = c11.reader(GITHUB, stand_in, since=G_ON, backfill_days=365, comments=False)
    alone = c11.reader(GITHUB, twin, since=G_ON, comments=False)
    cursor = empty = None
    written: list[str] = []
    for k in range(100):
        stand_in.clock.now = twin.clock.now = c11.NOW + k * 12 * HOUR
        got = read(cursor)
        cursor = got.cursor
        written += c11.refs(got)
        empty = alone(empty).cursor
        assert "Old report" not in cursor
    assert written == [c11.issue_ref(n) for n in range(200, 0, -1)]  # oldest finish first
    assert len(cursor) <= len(empty) + 40  # no more than a source with nothing to read


# GitHub, through the runner


def github_world(tmp_path, fake, **settings) -> SimpleNamespace:
    """A workspace with a project "quill" and a GitHub source added off, then switched on at
    G_ON with `settings`."""
    world = c11.make_world(tmp_path, fake)
    world.store.add_source("github", "github", c11.config(projects=[], **settings))
    world.clock.now = G_ON
    world.store.enable_source("github", projects=["quill"])
    return world


def github_run(world, fake, at: datetime):
    fake.clock.now = at
    fake.new_pull()
    return asyncio.run(
        run_source(world.store, "github", http=world.http, env=c11.ENV, clock=fake.clock)
    )


def github_written(world) -> list[str]:
    return [c.source_ref for c in c11.notes(world)]


@MOVES
@pytest.mark.parametrize("backfill", [0, 30])
def test_github_through_the_runner_writes_what_finished_since_the_floor_once(
    tmp_path, gfake, backfill, moves
):
    gfake.moves = moves
    links = [
        link
        for base, what in enumerate(WHAT)
        for link in finished_items(gfake, what, G_ON - 60 * DAY, base=10 * base)
    ]
    world = github_world(tmp_path, gfake, backfill_days=backfill)
    try:
        report = github_run(world, gfake, c11.NOW)
        # Oldest finish first; on a tie of finish, pull requests, issues, releases.
        times = (1, 2, 3) if backfill else (3,)
        expected = [links[4 * what + t] for t in times for what in range(3)]
        assert report.saved == len(expected)
        assert github_written(world) == expected
        for hours in (1, 30, 300):
            assert github_run(world, gfake, c11.NOW + hours * HOUR).saved == 0
        assert github_written(world) == expected
    finally:
        world.store.close()


@MOVES
def test_github_an_issue_closed_80_days_before_is_written_once_across_many_runs(
    tmp_path, gfake, moves
):
    gfake.moves = moves
    old = gfake.issue(1, "Closed eighty days before", created=G_ON - 99 * DAY)
    gfake.close(old, G_ON - 80 * DAY)
    gfake.pull_request(2, "Merged ten days before", created=G_ON - 99 * DAY, merged=G_ON - 10 * DAY)
    world = github_world(tmp_path, gfake, backfill_days=90)
    assert world.store.get_source("github").config["lookback_days"] == 30
    try:
        saved = 0
        for half_days in range(120):  # two runs a day for sixty days
            at = G_ON + HOUR + half_days * 12 * HOUR
            if half_days == 80:  # forty days on, long after its digest was let go
                gfake.comment(old, at - HOUR, "cy-moss", "Still relevant.")
            saved += github_run(world, gfake, at).saved
        assert saved == 2
        assert sorted(github_written(world)) == [c11.issue_ref(1), c11.pr_ref(2)]
    finally:
        world.store.close()


@MOVES
def test_github_switched_off_and_on_again_moves_the_floor(tmp_path, gfake, moves):
    gfake.moves = moves
    opened = G_ON - 60 * DAY

    def merged(n: int, at: datetime) -> str:
        gfake.pull_request(n, f"Change {n}", created=opened, merged=at)
        return c11.pr_ref(n)

    one, two = merged(1, G_ON - 10 * DAY), merged(2, G_ON + DAY)
    gfake.release(1, "v1.0.0", "Ten days before", published=G_ON - 10 * DAY)
    world = github_world(tmp_path, gfake, backfill_days=30)
    store = world.store
    try:
        assert github_run(world, gfake, G_ON + 2 * DAY).saved == 3

        world.clock.now = G_ON + 3 * DAY  # off for two days: the new floor covers all three
        store.disable_source("github")
        three = merged(3, G_ON + 4 * DAY)
        world.clock.now = G_ON + 5 * DAY
        store.enable_source("github")
        assert github_run(world, gfake, G_ON + 6 * DAY).saved == 1

        world.clock.now = G_ON + 7 * DAY  # off for 43 days: the new floor is G_ON + 20 days
        store.disable_source("github")
        merged(4, G_ON + 10 * DAY)
        gfake.release(2, "v2.0.0", "Off, before the new floor", published=G_ON + 10 * DAY)
        five = merged(5, G_ON + 40 * DAY)
        world.clock.now = G_ON + 50 * DAY
        store.enable_source("github")
        six = merged(6, G_ON + 51 * DAY)
        assert github_run(world, gfake, G_ON + 53 * DAY).saved == 2
        for days in (54, 60, 90):
            assert github_run(world, gfake, G_ON + days * DAY).saved == 0
        assert github_written(world) == [one, c11.release_ref("v1.0.0"), two, three, five, six]
    finally:
        store.close()


# The setting


@pytest.mark.parametrize("kind", ["linear", "github"])
def test_backfill_days_is_a_whole_number_from_0_to_365(kind):
    config = c10.config if kind == "linear" else c11.config
    for given in (0, 1, 30, 365):
        checked = KINDS[kind].check(config(backfill_days=given))
        assert checked["backfill_days"] == given
        assert KINDS[kind].check(checked) == checked
    for wrong in (-1, 366, "30", 1.5, True, None, [30]):
        with pytest.raises(ValueError) as refused:
            KINDS[kind].check(config(backfill_days=wrong))
        assert "backfill_days" in str(refused.value), wrong


# What a person is told


@pytest.mark.parametrize("kind", ["linear", "github"])
def test_describe_and_the_switch_on_sentence_say_the_backfill_when_it_is_not_0(kind):
    config = c10.config if kind == "linear" else c11.config
    plain = KINDS[kind].check(config())
    back = KINDS[kind].check(config(backfill_days=30))
    assert "days before" not in KINDS[kind].describe(plain)
    assert PHRASE in KINDS[kind].describe(back)
    own = KINDS[kind].switching_on()
    assert KINDS[kind].switching_on(config=plain) == own
    said = KINDS[kind].switching_on(config=back)
    assert said != own and PHRASE in said
    assert "nothing finished before" not in said.lower()  # no longer true
    one_day = KINDS[kind].switching_on(config=KINDS[kind].check(config(backfill_days=1)))
    assert "the day before it is switched on" in one_day


def page_of(store) -> str:
    """The Sources page as an owner of the workspace sees it."""
    owner = store.sign_in(
        "sso", "o", "o@example.test", "Olive", issuer="https://id.example.test", join="default"
    )
    with TestClient(create_app(store, public_url=WEB), base_url=WEB) as client:
        client.cookies.set(session_cookie(), owner.token)
        return " ".join(client.get("/sources").text.split())


def test_the_sources_page_says_the_backfill_of_an_off_source_and_of_an_on_one(tmp_path):
    store = Store(tmp_path / "cairn.db", clock=c10.Clock(L_ON))
    try:
        store.create_project("linear")
        store.add_source("tracker", "linear", c10.config(projects=[], backfill_days=30))
        store.add_source("code", "github", c11.config(projects=[], backfill_days=30))
        page = page_of(store).replace("&#39;", "'")
        for kind, name in (("linear", "tracker"), ("github", "code")):
            info = store.get_source(name)
            assert KINDS[kind].switching_on(config=info.config) in page, kind
        store.enable_source("tracker", projects=["linear"])
        page = page_of(store).replace("&#39;", "'")
        assert LINEAR.describe(store.get_source("tracker").config) in page
    finally:
        store.close()


def test_source_add_help_lists_backfill_days_for_both_kinds(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "10000")  # one line: no break after a hyphen
    with pytest.raises(SystemExit):
        main(["source-add", "--help"])
    said = " ".join(capsys.readouterr().out.split())
    for kind in ("linear", "github"):
        settings = " ".join(KINDS[kind].settings.split())
        assert '"backfill_days": 0' in settings and f"{kind}: {settings}" in said, kind


def test_the_frozen_files_do_not_take_backfill_days_as_their_unknown_setting():
    """Each frozen file refuses an unknown setting by a name of its own ("color"), so adding
    backfill_days changes none of what they pin."""
    for module in (c10, c11):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "backfill_days" not in source
        assert '("color", {"color": "blue"})' in source
