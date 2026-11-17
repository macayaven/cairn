"""Sentences of src/cairn/guides/sources.md about the Linear source, each pinned here so the
guide cannot go false unnoticed: the sentence is in the test's docstring and in the guide,
and the test asks the running code for it, or, for what Linear itself says, the notes in
docs/linear-api.md. Linear is a stand-in; nothing leaves the process."""

import asyncio
import json
import shlex
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from guide_source import guide_source
from test_linear_unit import (
    BASE,
    KEY,
    NOW,
    SINCE,
    VAR,
    Clock,
    Linear,
    comment,
    graphql_error,
    iid,
    issue,
    opened,
    pull,
)

from cairn.server import main
from cairn.sources import KINDS, SourceError, run_source, source_client
from cairn.sources import linear as lin
from cairn.store import Store

RAW = guide_source("sources")
GUIDE = " ".join(RAW.split())
NOTES = " ".join((Path(__file__).parents[2] / "docs" / "linear-api.md").read_text().split())
DAY, HOUR = timedelta(days=1), timedelta(hours=1)


def said(sentence: str) -> None:
    """The guide says `sentence`, word for word (whitespace aside)."""
    assert " ".join(sentence.split()) in GUIDE, sentence


@pytest.fixture
def world(tmp_path):
    """A workspace with a project "linear" and a Linear source switched on at SINCE."""
    clock = Clock(SINCE - DAY)
    store = Store(tmp_path / "cairn.db", clock=clock)
    store.create_project("linear")
    store.add_source("tracker", "linear", {"key_env": VAR, "projects": [], "base_url": BASE})
    clock.now = SINCE
    store.enable_source("tracker", projects=["linear"])
    yield store
    store.close()


def run(store, stand_in: Linear, at=NOW):
    http = source_client(httpx.MockTransport(stand_in))
    return asyncio.run(run_source(store, "tracker", http=http, env={VAR: KEY}, clock=Clock(at)))


def test_the_guide_lists_linear_among_the_sources_the_server_reads():
    """sources.md: "It also reads [Linear](#issues-from-linear-read-by-the-server), where
    issues become notes once they are finished.\""""
    said(
        "It also reads [Linear](#issues-from-linear-read-by-the-server), where issues become"
        " notes once they are finished."
    )
    assert "### Issues from Linear, read by the server" in RAW
    assert KINDS["linear"] is lin.KIND


# sources.md, "Issues from Linear, read by the server"

HEADING = "### Issues from Linear, read by the server"


def test_the_guide_says_when_to_use_it_or_the_to_dos_recipe(world):
    """sources.md: "When a team tracks its work in Linear, the operator can add a Linear
    source, and each issue arrives as a note once it is finished, without anyone asking an
    agent. Use the recipe above for a list Linear does not hold, or when you want an agent to
    write what was done in its own words.\""""
    said(
        "When a team tracks its work in Linear, the operator can add a Linear source, and each"
        " issue arrives as a note once it is finished, without anyone asking an agent. Use the"
        " recipe above for a list Linear does not hold, or when you want an agent to write what"
        " was done in its own words."
    )
    assert RAW.index("## To-dos") < RAW.index(HEADING) < RAW.index("## Check a recipe")
    stand_in = Linear([issue(1, NOW - DAY), issue(2, NOW + DAY)])  # one finished, one not
    assert run(world, stand_in).saved == 1  # the server's own run, no agent
    (note,) = world.list_notes(world.get_project("linear").id)
    assert note.text.startswith("# ENG-1: Issue 1")


def test_it_starts_off_and_an_owner_or_admin_switches_it_on_choosing_its_project(tmp_path):
    """sources.md: "Then an owner or an admin switches it on under **Workspace**, then
    **Sources**, and chooses its project ([Sources the server reads](#sources-the-server-reads))."
    """
    said(
        "Then an owner or an admin switches it on under **Workspace**, then **Sources**, and"
        " chooses its project ([Sources the server reads](#sources-the-server-reads))."
    )
    assert KINDS["linear"].starts_off is True
    store = Store(tmp_path / "cairn.db")
    try:
        store.create_project("linear")
        store.add_source("tracker", "linear", {"key_env": VAR, "projects": []})
        assert store.get_source("tracker").enabled is False
        store.enable_source("tracker", projects=["linear"])
        on = store.get_source("tracker")
        assert on.enabled is True and on.config["projects"] == ["linear"]
    finally:
        store.close()


def test_it_reads_what_the_key_reads_or_the_teams_or_project_named_into_one_project():
    """sources.md: "A Linear source reads the issues one Linear API key can read, or those of
    the teams or the one Linear project the operator names, and writes each issue into one
    project once it is completed or cancelled.\""""
    said(
        "A Linear source reads the issues one Linear API key can read, or those of the teams"
        " or the one Linear project the operator names, and writes each issue into one project"
        " once it is completed or cancelled."
    )
    project = iid(900)
    for given, wanted in (
        ({}, None),
        ({"teams": ["ENG", "OPS"]}, {"team": {"key": {"in": ["ENG", "OPS"]}}}),
        ({"linear_project": project}, {"project": {"id": {"eq": project}}}),
    ):
        stand_in = Linear()
        pull(opened(stand_in, **given))
        sent = stand_in.requests[0]["variables"]["filter"]
        assert sent["state"] == {"type": {"in": ["completed", "canceled"]}}
        extra = {k: v for k, v in sent.items() if k not in ("state", "or")}
        assert extra == (wanted or {})
    with pytest.raises(ValueError):
        lin.check_config({"key_env": VAR, "projects": ["a", "b"]})  # one project
    cancelled = issue(1, NOW - DAY, completedAt=None, canceledAt="2099-05-31T09:00:00.000Z")
    cancelled["state"] = {"name": "Won't do", "type": "canceled"}
    stand_in = Linear([cancelled, issue(2, NOW - 2 * DAY)])
    assert [d.item for d in pull(opened(stand_in)).drafts] == [iid(2), iid(1)]


def test_the_note_holds_the_facts_the_description_and_the_comments_in_order():
    """sources.md: "Its note has a heading with the issue's identifier and title, then its
    team, its Linear project when it has one, its status, the day it was finished, who it
    is assigned to and who created it, by name, and its labels, then the description as
    Linear holds it, then its comments, oldest first, each reply after the comment it
    answers, with who wrote each one and when.\""""
    said(
        "Its note has a heading with the issue's identifier and title, then its team, its"
        " Linear project when it has one, its status, the day it was finished, who it is"
        " assigned to and who created it, by name, and its labels, then the description as"
        " Linear holds it, then its comments, oldest first, each reply after the comment it"
        " answers, with who wrote each one and when."
    )
    root = comment(1, "Found it.", NOW - 3 * DAY)
    later = comment(2, "A second thought.", NOW - 2 * DAY)
    reply = comment(3, "Fixed now.", NOW - DAY - HOUR, parent={"id": "c-00001"})
    made = issue(
        123,
        NOW - DAY,
        title="Fix the retry loop",
        description="The *backoff* was zero.",
        project={"id": iid(5), "name": "Refunds"},
        assignee={"name": "Ada Park", "displayName": "adapark"},
        creator={"name": "Cy Moss", "displayName": None},
        labels={"nodes": [{"name": "bug"}], "pageInfo": {"hasNextPage": False}},
        comments=[later, reply, root],
    )
    (draft,) = pull(opened(Linear([made]))).drafts
    lines = draft.text.splitlines()
    assert lines[0] == "# ENG-123: Fix the retry loop"
    facts = [line for line in lines if line.startswith("- ")]
    assert facts == [
        "- Team: Engineering",
        "- Project: Refunds",
        "- State: Done",
        f"- Finished: {NOW - DAY:%Y-%m-%d}",
        "- Assignee: adapark",
        "- Creator: Cy Moss",
        "- Labels: bug",
    ]
    text = draft.text
    order = ["The *backoff* was zero.", "## Comments", "Found it.", "Fixed now.", "A second"]
    assert [text.index(part) for part in order] == sorted(text.index(part) for part in order)
    assert f"adapark replied on {NOW - DAY - HOUR:%Y-%m-%d %H:%M} UTC:" in text


def test_each_issue_is_a_page_of_its_own_named_by_its_identifier_and_title(world):
    """sources.md: "Each issue gets a page of its own, named by its identifier and title, so
    a team that finishes a hundred issues a month adds about a hundred pages a month.\""""
    said(
        "Each issue gets a page of its own, named by its identifier and title, so a team that"
        " finishes a hundred issues a month adds about a hundred pages a month."
    )
    same = {"project": {"id": iid(5), "name": "Refunds"}}
    stand_in = Linear([issue(n, NOW - n * HOUR - DAY, **same) for n in (1, 2, 3)])
    assert run(world, stand_in).saved == 3
    project = world.get_project("linear").id
    slugs = sorted(page.slug for page in world.list_pages(project))
    assert slugs == ["eng-1-issue-1", "eng-2-issue-2", "eng-3-issue-3"]
    assert {c.topic for c in world.list_notes(project)} == {None}


def test_names_are_written_and_never_an_address():
    """sources.md: "{{name}} writes people's names, never their email addresses, and a name
    that holds an address counts as no name.\""""
    said(
        "cairn writes people's names, never their email addresses, and a name that holds an"
        " address counts as no name."
    )
    assert "email" not in lin.LISTING_QUERY.lower() + lin.COMMENTS_QUERY.lower()
    made = issue(
        1,
        NOW - DAY,
        assignee={"name": "ada@example.test", "displayName": None},
        creator={"name": "Brook Lane", "displayName": "brook@example.test"},
        comments=[comment(1, "Hi.", NOW - DAY, user={"name": "c@example.test", "displayName": ""})],
    )
    (draft,) = pull(opened(Linear([made]))).drafts
    assert "@" not in draft.text and "- Creator: Brook Lane" in draft.text
    assert "Assignee" not in draft.text and "Someone whose name is not shown" in draft.text


def test_a_long_description_or_comment_is_cut_and_the_first_200_comments_are_kept():
    """sources.md: "A description or a comment too long for one page is cut, with a line
    saying so, and a note keeps an issue's first 200 comments and says how many more there
    are.\""""
    said(
        "A description or a comment too long for one page is cut, with a line saying so, and"
        " a note keeps an issue's first 200 comments and says how many more there are."
    )
    assert lin.COMMENTS_KEPT == 200
    long = "\n\n".join(f"Part {k}: " + "lorem ipsum " * 40 for k in range(1_000))
    many = [
        comment(k, f"Remark {k:04d}.", NOW - 2 * DAY + k * timedelta(seconds=1)) for k in range(250)
    ]
    many[0]["body"] = long
    made = issue(1, NOW - DAY, description=long, comments=many)
    (draft,) = pull(opened(Linear([made]))).drafts
    assert "The description is cut here:" in draft.text
    assert "This comment is cut here:" in draft.text
    assert "Remark 0199." in draft.text and "Remark 0200." not in draft.text
    assert "The issue has 50 more comments, not written here." in draft.text


def test_it_proposes_the_project_linear():
    """sources.md: "When it is switched on, the page proposes the project `linear`.\""""
    said("When it is switched on, the page proposes the project `linear`.")
    assert KINDS["linear"].default_project == "linear"


def test_an_issue_is_read_once_settled_and_only_once(world):
    """sources.md: "An issue is read once it has been completed or cancelled for
    `settle_minutes` (30 unless set), so that the comment that closes it is in, and only
    once: an edit, a new comment, reopening it, finishing it again or moving it to another
    team afterwards writes nothing more in {{name}}.\""""
    said(
        "An issue is read once it has been completed or cancelled for `settle_minutes` (30"
        " unless set), so that the comment that closes it is in, and only once: an edit, a new"
        " comment, reopening it, finishing it again or moving it to another team afterwards"
        " writes nothing more in cairn."
    )
    assert lin.check_config({"key_env": VAR})["settle_minutes"] == 30
    made = issue(1, NOW - 29 * timedelta(minutes=1))
    stand_in = Linear([made])
    assert run(world, stand_in).saved == 0  # finished 29 minutes ago: it waits
    made["comments"].append(comment(1, "Closing it.", NOW))
    assert run(world, stand_in, NOW + 2 * timedelta(minutes=1)).saved == 1
    made.update(title="Edited", identifier="OPS-9", url="https://linear.example.test/OPS-9")
    made["completedAt"] = lin._stamp(NOW + HOUR)  # finished again, in another team
    made["comments"].append(comment(2, "Reopened and done again.", NOW + HOUR))
    for later in (NOW + 2 * HOUR, NOW + 40 * DAY):  # past the digest's lookback too
        report = run(world, stand_in, later)
        assert report.saved == 0
    project = world.get_project("linear").id
    (note,) = world.list_notes(project)
    assert "Closing it." in note.text and "Edited" not in note.text


def test_a_duplicate_an_open_issue_and_one_in_the_trash_are_never_read():
    """sources.md: "An issue marked as a duplicate, one still open and one in Linear's trash
    are never read. Unless `backfill_days` is set, only issues finished after the source was
    switched on are read, nothing from before. Archived issues are read too, since Linear
    archives finished issues by itself.\""""
    said(
        "An issue marked as a duplicate, one still open and one in Linear's trash are never"
        " read. Unless `backfill_days` is set, only issues finished after the source was"
        " switched on are read, nothing from before. Archived issues are read too, since"
        " Linear archives finished issues by itself."
    )
    assert lin.check_config({"key_env": VAR})["backfill_days"] == 0
    duplicate = issue(1, NOW - DAY, completedAt=None, canceledAt=lin._stamp(NOW - DAY))
    duplicate["state"] = {"name": "Duplicate", "type": "duplicate"}
    still_open = issue(2, NOW - DAY, state={"name": "In Progress", "type": "started"})
    trashed = issue(3, NOW - DAY, trashed=True)
    before = issue(4, SINCE - HOUR)
    kept = issue(5, NOW - DAY)
    stand_in = Linear([duplicate, still_open, trashed, before, kept])
    assert [d.item for d in pull(opened(stand_in)).drafts] == [iid(5)]
    assert "includeArchived: true" in lin.LISTING_QUERY
    assert (
        "A closed issue is auto-archived once it has stayed completed, canceled or auto-closed,"
        " with no activity, for the whole auto-archive period (paraphrased)."
        " [doc: DELETE-ARCHIVE]" in NOTES
    )


def test_by_default_it_reads_nothing_from_before_and_backfill_days_reaches_back():
    """sources.md: "By default a source reads nothing finished before it was switched on, so
    that switching it on never pours an unknown amount of history into a project. With
    `backfill_days` (0 unless set, at most 365) it also reads the issues finished in that
    many days before it is switched on, oldest first, each once.\""""
    said(
        "By default a source reads nothing finished before it was switched on, so that"
        " switching it on never pours an unknown amount of history into a project. With"
        " `backfill_days` (0 unless set, at most 365) it also reads the issues finished in"
        " that many days before it is switched on, oldest first, each once."
    )
    own = RAW.split(HEADING)[1].split("\n### ")[0]  # the section's own text
    assert "**Starting with what finished lately.**" in own
    assert lin.check_config({"key_env": VAR})["backfill_days"] == 0
    assert lin.check_config({"key_env": VAR, "backfill_days": 365})["backfill_days"] == 365
    with pytest.raises(ValueError, match="backfill_days"):
        lin.check_config({"key_env": VAR, "backfill_days": 366})
    issues = [issue(n, SINCE - n * DAY) for n in (40, 20, 2)]
    assert pull(opened(Linear(issues))).drafts == []
    first = pull(opened(Linear(issues), backfill_days=30))
    assert [d.item for d in first.drafts] == [iid(20), iid(2)]  # oldest first
    assert pull(opened(Linear(issues), backfill_days=30), first.cursor).drafts == []


def test_a_backfill_is_read_over_runs_25_at_a_time_and_600_issues_take_24_runs():
    """sources.md: "It reads them over several runs, at most 25 issues a run, so 600 issues
    take 24 runs, and an issue finished meanwhile takes its turn in the order they were
    finished.\""""
    said(
        "It reads them over several runs, at most 25 issues a run, so 600 issues take 24 runs,"
        " and an issue finished meanwhile takes its turn in the order they were finished."
    )
    assert lin.PER_PULL == 25
    clock = Clock(NOW)
    stand_in = Linear([issue(n, SINCE - 300 * DAY + n * 10 * HOUR) for n in range(1, 601)])
    cursor, runs, written = None, 0, []
    while runs < 30:
        got = pull(opened(stand_in, clock=clock, backfill_days=365, comments=False), cursor)
        if not got.drafts:
            break
        runs, cursor = runs + 1, got.cursor
        written += [d.item for d in got.drafts]
        clock.now += HOUR
        if runs == 1:  # finished meanwhile: it comes after the 600, as it finished after
            stand_in.issues.append(issue(601, NOW + HOUR / 2))
    assert written == [iid(n) for n in range(1, 602)]
    assert runs == 25  # 24 for the 600, and the one finished meanwhile


def test_switched_off_and_on_it_reads_from_the_new_switch_on_and_writes_nothing_twice(tmp_path):
    """sources.md: "Switched off and on again, it reads from that many days before the new
    switch-on, and what it wrote before is not written again.\""""
    said(
        "Switched off and on again, it reads from that many days before the new switch-on,"
        " and what it wrote before is not written again."
    )
    clock = Clock(SINCE - DAY)
    store = Store(tmp_path / "cairn.db", clock=clock)
    try:
        store.create_project("linear")
        settings = {"key_env": VAR, "projects": [], "base_url": BASE, "backfill_days": 10}
        store.add_source("tracker", "linear", settings)
        clock.now = SINCE
        store.enable_source("tracker", projects=["linear"])
        stand_in = Linear([issue(1, SINCE - 5 * DAY), issue(2, SINCE + DAY)])
        assert run(store, stand_in, SINCE + 2 * DAY).saved == 2
        clock.now = SINCE + 3 * DAY
        store.disable_source("tracker")
        stand_in.issues += [issue(3, SINCE + 5 * DAY), issue(4, SINCE + 15 * DAY)]
        clock.now = SINCE + 20 * DAY  # the new floor: SINCE + 10 days
        store.enable_source("tracker")
        assert run(store, stand_in, SINCE + 21 * DAY).saved == 1  # the fourth only
        assert run(store, stand_in, SINCE + 25 * DAY).saved == 0
        project = store.get_project("linear").id
        assert len(store.list_notes(project)) == 3
    finally:
        store.close()


def test_setting_it_up_with_a_read_only_key_limited_to_teams():
    """sources.md: "In Linear, a person makes a personal API key under Settings, Account,
    Security & Access. Give it only the Read permission and limit it to the teams the source
    should read: the key reads what its owner can see in those teams, private teams the owner
    belongs to included." Linear's statements, recorded in our words in docs/linear-api.md
    (K.1, K.2)."""
    said(
        "In Linear, a person makes a personal API key under Settings, Account, Security &"
        " Access. Give it only the Read permission and limit it to the teams the source should"
        " read: the key reads what its owner can see in those teams, private teams the owner"
        " belongs to included."
    )
    assert (
        "Where (paraphrased): Admins, and Members who are allowed to, make personal API keys"
        " under _Settings > Account > Security & Access_. Each key either gets all the data its"
        " user can reach or is held to chosen permissions (_Read_, _Write_, _Admin_, _Create"
        " issues_, _Create comments_), and a key can also be limited to chosen teams of the"
        " workspace." in NOTES
    )
    assert (
        "The API can read private issue data through the personal API key of a user who has"
        " access to private teams (paraphrased). [doc: PRIVATE-TEAMS]" in NOTES
    )


def test_the_command_adds_a_source_that_starts_off(tmp_path, capsys):
    """sources.md: "The operator puts the key in the server's environment, never in {{name}}
    (with Docker, a line in `deploy/cairn.env`), and adds the source, which starts off:" and
    the command after it."""
    said(
        "The operator puts the key in the server's environment, never in cairn (with Docker, a"
        " line in `deploy/cairn.env`), and adds the source, which starts off:"
    )
    (command,) = [
        line
        for line in RAW.splitlines()
        if line.startswith("python -m cairn.server source-add") and "--kind linear" in line
    ]
    args = shlex.split(command)[3:]
    db = tmp_path / "cairn.db"
    Store(db).close()
    args[args.index("--db") + 1] = str(db)
    assert main(args) == 0
    store = Store(db)
    try:
        (source,) = store.list_sources()
        assert (source.kind, source.enabled) == ("linear", False)
        assert source.config["key_env"] == "LINEAR_API_KEY" and source.config["teams"] == ["ENG"]
    finally:
        store.close()
    capsys.readouterr()


def test_every_setting_but_key_env_is_optional_with_its_default():
    """sources.md: "Everything but `key_env` is optional: `teams` (up to 20 team keys, the
    letters before the number in an identifier, `ENG` in `ENG-123`) or `linear_project` (one
    Linear project's id), not both, and without either every team the key can read; `comments`
    (true); `settle_minutes` (30); `lookback_days` (30: how far back the daily look for
    issues that turned up late goes); `backfill_days` (0: how many days before the switch-on
    it also reads) and `base_url`.\""""
    said(
        "Everything but `key_env` is optional: `teams` (up to 20 team keys, the letters before"
        " the number in an identifier, `ENG` in `ENG-123`) or `linear_project` (one Linear"
        " project's id), not both, and without either every team the key can read;"
        " `comments` (true); `settle_minutes` (30); `lookback_days` (30: how far back the daily"
        " look for issues that turned up late goes); `backfill_days` (0: how many days before"
        " the switch-on it also reads) and `base_url`."
    )
    checked = lin.check_config({"key_env": VAR})
    assert (
        checked["comments"],
        checked["settle_minutes"],
        checked["lookback_days"],
        checked["backfill_days"],
    ) == (True, 30, 30, 0)
    assert set(lin.SETTINGS) == {*checked, "teams", "linear_project"}
    lin.check_config({"key_env": VAR, "teams": [f"T{n}" for n in range(20)]})
    with pytest.raises(ValueError):
        lin.check_config({"key_env": VAR, "teams": [f"T{n}" for n in range(21)]})
    with pytest.raises(ValueError):
        lin.check_config({"key_env": VAR, "teams": ["ENG"], "linear_project": iid(1)})
    clock = Clock(NOW)
    stand_in = Linear()
    first = pull(opened(stand_in, clock=clock, since=NOW - 90 * DAY, lookback_days=30))
    clock.now = NOW + DAY
    stand_in.requests.clear()
    pull(opened(stand_in, clock=clock, since=NOW - 90 * DAY, lookback_days=30), first.cursor)
    rescan = stand_in.requests[-1]["variables"]["filter"]["or"][0]["completedAt"]["gte"]
    assert rescan == lin._stamp(NOW + DAY - 30 * DAY - lin.OVERLAP)


def test_a_team_the_key_cannot_read_is_not_an_error():
    """sources.md: "A team the key cannot read is not an error: the source reads what Linear
    lists, so when a team's issues do not arrive, check the key's teams.\""""
    said(
        "A team the key cannot read is not an error: the source reads what Linear lists, so"
        " when a team's issues do not arrive, check the key's teams."
    )
    got = pull(opened(Linear([]), teams=["SEC"]))  # Linear lists nothing for it
    assert got.drafts == [] and json.loads(got.cursor)["v"] == lin.CURSOR_VERSION


def test_a_refused_key_fails_the_run_naming_the_variable(world):
    """sources.md: "When Linear refuses the key, the run fails, and the Sources page names the
    variable to check, never the key.\""""
    said(
        "When Linear refuses the key, the run fails, and the Sources page names the variable"
        " to check, never the key."
    )
    stand_in = Linear([issue(1, NOW - DAY)])
    stand_in.answers.append(
        (lin.LISTING, graphql_error(401, f"bad {KEY}", code="AUTHENTICATION_ERROR"))
    )
    with pytest.raises(SourceError):
        run(world, stand_in)
    error = world.get_source("tracker").last_error
    assert VAR in error and KEY not in error


def test_a_rate_limit_stops_the_run_without_failing_it_and_says_so_under_its_counts(world):
    """sources.md: "When Linear asks the server to slow down, the run stops where it is, keeps
    what it finished and does not fail; the Sources page says so under the source's counts,
    and the next run goes on.\""""
    said(
        "When Linear asks the server to slow down, the run stops where it is, keeps what it"
        " finished and does not fail; the Sources page says so under the source's counts, and"
        " the next run goes on."
    )
    stand_in = Linear([issue(1, NOW - 2 * DAY), issue(2, NOW - DAY)])
    stand_in.answers.append((lin.COMMENTS, graphql_error(400, "slow down", code="RATELIMITED")))
    stand_in.answers.insert(0, (lin.COMMENTS, stand_in.comments({"id": iid(1)})))
    report = run(world, stand_in)
    assert report.saved == 1 and world.get_source("tracker").last_error is None
    assert "to slow down (rate limited)" in world.get_source("tracker").last_report["said"]
    assert run(world, stand_in, NOW + HOUR).saved == 1  # the next run goes on


def test_every_key_of_one_user_shares_the_hourly_allowance():
    """sources.md: "All the keys of one Linear user share that user's hourly allowance of
    2,500 requests, so another tool using the same person's keys can use it up." Linear's
    statement, recorded in our words in docs/linear-api.md (C)."""
    said(
        "All the keys of one Linear user share that user's hourly allowance of 2,500 requests,"
        " so another tool using the same person's keys can use it up."
    )
    assert "| API key | 2,500 | User | 1 hour |" in NOTES
    assert (
        "Requests count against the authenticated user, so every request by one user draws on"
        " one quota, whichever of that user's API keys it uses (paraphrased). [doc: RATE-LIMITS]"
        in NOTES
    )
