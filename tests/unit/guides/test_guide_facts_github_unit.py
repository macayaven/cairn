"""Sentences of src/cairn/guides/sources.md about the GitHub source, each pinned here so the
guide cannot go false unnoticed: the sentence is in the test's docstring and in the guide,
and the test asks the running code for it. GitHub is a stand-in (httpx.MockTransport);
nothing leaves the process, and every name, token and address is made up."""

import asyncio
import json
import re
import shlex
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest

from cairn.sources import KINDS, SourceError, run_source
from cairn.sources import github as g
from cairn.store import Store
from support.guide_source import guide_source
from support.sources.github_pull import (
    BASE,
    DAY,
    HOUR,
    NOW,
    REPO,
    SINCE,
    TOKEN,
    VAR,
    Mini,
    comment,
    failure,
    issue_entry,
    pull_detail,
    pull_entry,
    pulled,
    release_entry,
    review,
    searched,
    user,
)

RAW = guide_source("sources")
GUIDE = " ".join(RAW.split())
MINUTE = timedelta(minutes=1)


def said(sentence: str) -> None:
    """The guide says `sentence`, word for word (whitespace aside)."""
    assert " ".join(sentence.split()) in GUIDE, sentence


def test_the_guide_points_to_the_github_section_from_the_list_of_kinds():
    """sources.md: "It also reads [GitHub](#finished-work-from-github-read-by-the-server),
    where the pull requests merged, the issues closed and the releases published in one
    repository do.\""""
    said(
        "It also reads [GitHub](#finished-work-from-github-read-by-the-server), where the pull"
        " requests merged, the issues closed and the releases published in one repository do."
    )
    assert "### Finished work from GitHub, read by the server" in RAW
    assert "github" in KINDS and g.check_config({"token_env": VAR, "repo": REPO})["repo"] == REPO


def test_it_reads_one_repository_into_one_project_and_never_what_is_not_finished():
    """sources.md: "The operator can add a GitHub source that reads that work from one
    repository, so it arrives without anyone asking an agent." ... "A GitHub source reads
    one repository and writes each finished item as a note into one project: a pull request
    once it is merged, an issue once it is closed as completed or as not planned, and a
    release once it is published, a prerelease among them. It never reads an open pull
    request or issue, a pull request closed without merging, an issue closed as a duplicate,
    a draft release, or review comments on lines of code.\""""
    said(
        "The operator can add a GitHub source that reads that work from one repository, so it"
        " arrives without anyone asking an agent."
    )
    said(
        "A GitHub source reads one repository and writes each finished item as a note into"
        " one project: a pull request once it is merged, an issue once it is closed as completed"
        " or as not planned, and a release once it is published, a prerelease among them. It"
        " never reads an open pull request or issue, a pull request closed without merging, an"
        " issue closed as a duplicate, a draft release, or review comments on lines of code."
    )
    with pytest.raises(ValueError, match="at most one project"):
        g.check_config({"token_env": VAR, "repo": REPO, "projects": ["code", "other"]})
    with pytest.raises(ValueError, match="repo is owner/name"):
        g.check_config({"token_env": VAR, "repo": "quillworks/quill,quillworks/ledger"})
    mini = Mini()
    mini.routes[f"{BASE}/issues"] = (
        200,
        [
            pull_entry(1),
            pull_entry(2, pull_request={"merged_at": None}),  # closed without merging
            pull_entry(3, state="open", pull_request={"merged_at": None}),
            issue_entry(4),
            issue_entry(5, state_reason="not_planned"),
            issue_entry(6, state_reason="duplicate"),
            issue_entry(7, state="open", closed_at=None, state_reason=None),
        ],
    )
    mini.routes[f"{BASE}/releases"] = (
        200,
        [release_entry(1), release_entry(2, prerelease=True), release_entry(3, draft=True)],
    )
    for n in (1, 4, 5):
        mini.routes[f"{BASE}/issues/{n}/comments"] = (200, [])
    mini.routes[f"{BASE}/pulls/1"] = (200, pull_detail(1))
    mini.routes[f"{BASE}/pulls/1/reviews"] = (200, [])
    got = pulled(mini)
    assert [d.item for d in got.drafts] == [
        "pull:1",
        "issue:4",
        "issue:5",
        "release:7001",
        "release:7002",
    ]
    assert {d.project for d in got.drafts} == {"quill"}
    assert "Prerelease: yes" in got.drafts[-1].text
    assert not any(path.endswith("/pulls/1/comments") for path in mini.paths())  # review ones


def test_the_note_says_who_did_what_and_when_then_the_description_and_the_conversation():
    """sources.md: "Its note has a heading with the repository, the item's number and its
    title (for a release, its tag and name), a list of facts (who opened it, who merged it,
    when and into which branch; when an issue was closed and how; a release's tag, date and
    who published it), then the description as GitHub gives it. With `comments` on, which it
    is unless you turn it off, the conversation follows under a **Comments** heading in time
    order: each comment with its author and date, and a pull request's reviews that carry
    text, each with its verdict in words.\""""
    said(
        "Its note has a heading with the repository, the item's number and its title (for a"
        " release, its tag and name), a list of facts (who opened it, who merged it, when and"
        " into which branch; when an issue was closed and how; a release's tag, date and who"
        " published it), then the description as GitHub gives it. With `comments` on, which it"
        " is unless you turn it off, the conversation follows under a **Comments** heading in"
        " time order: each comment with its author and date, and a pull request's reviews that"
        " carry text, each with its verdict in words."
    )
    assert g.check_config({"token_env": VAR, "repo": REPO})["comments"] is True
    mini = Mini()
    later = {"created_at": "2099-06-01T05:00:00Z"}
    mini.a_pull(
        12,
        title="Back off between webhook retries",
        body="Retries now back off.\n\n```python\nwait = 2 ** attempt\n```",
        comments=[comment(1, "Does this cover refunds?"), comment(2, "Yes.", **later)],
        reviews=[review(3, "CHANGES_REQUESTED", "Split the migration.")],
    )
    (draft,) = pulled(mini).drafts
    text = draft.text
    assert text.startswith(f"# {REPO}#12: Back off between webhook retries\n\n- Opened by:")
    for line in ("- Merged by: brook-lane", "- Merged: 2099-06-02 00:00 UTC", "- Into: main"):
        assert line in text
    assert "Retries now back off.\n\n```python\nwait = 2 ** attempt\n```" in text
    assert text.index("```\n") < text.index("## Comments") < text.index("Does this cover")
    assert text.index("Does this cover") < text.index("Split the") < text.index("Yes.")
    assert "ada-park asked for changes on 2099-06-01 01:00 UTC:" in text
    assert "cy-moss wrote on 2099-06-01 00:00 UTC:" in text
    mini = Mini()
    mini.an_issue(4, comment(1, "Not asked for."), state_reason="not_planned")
    (quiet,) = pulled(mini, comments=False).drafts
    assert "- Closed as: not planned" in quiet.text and "Comments" not in quiet.text
    assert not any(path.endswith("/comments") for path in mini.paths())
    mini.routes[f"{BASE}/releases"] = (200, [release_entry(1)])
    mini.routes[f"{BASE}/issues"] = (200, [])
    (release,) = pulled(mini).drafts
    assert release.title == f"{REPO} v1.0.0: Batch 1"
    for line in ("- Tag: v1.0.0", "- Published: 2099-06-03 00:00 UTC", "- Published by: ada"):
        assert line in release.text


def test_people_are_named_by_login_and_each_item_has_a_page_of_its_own():
    """sources.md: "People are named by their GitHub login, a bot's followed by "(bot)" and an
    account since deleted as "a deleted account"; no email address is written. Each item has
    a page of its own, named by its title.\""""
    said(
        'People are named by their GitHub login, a bot\'s followed by "(bot)" and an account'
        ' since deleted as "a deleted account"; no email address is written. Each item has a'
        " page of its own, named by its title."
    )
    assert g.DELETED == "a deleted account"
    mini = Mini()
    mini.routes[f"{BASE}/issues"] = (
        200,
        [issue_entry(4, user=user("quill-ci[bot]", "Bot")), issue_entry(5, user=None)],
    )
    mini.routes[f"{BASE}/issues/4/comments"] = (200, [])
    mini.routes[f"{BASE}/issues/5/comments"] = (200, [])
    first, second = pulled(mini).drafts
    assert "- Opened by: quill-ci[bot] (bot)" in first.text
    assert "- Opened by: a deleted account" in second.text
    assert "example.test" not in first.text + second.text  # the email the API gives
    assert first.topic is None and second.topic is None and first.title != second.title


def test_an_item_is_read_once_it_has_settled_and_written_once():
    """sources.md: "An item is read once it has been finished for `settle_minutes` (30
    minutes unless set), and it is written once: an edit, a new comment, a reopening and a
    second close, or a release published again changes nothing in cairn afterwards. Unless
    `backfill_days` is set, only work finished after the source was switched on is read,
    nothing from before and nothing finished while it was off. An issue reopened before it
    was read is read once it is closed again and has settled.\""""
    said(
        "An item is read once it has been finished for `settle_minutes` (30 minutes unless"
        " set), and it is written once: an edit, a new comment, a reopening and a second close,"
        " or a release published again changes nothing in cairn afterwards. Unless"
        " `backfill_days` is set, only work finished after the source was switched on is read,"
        " nothing from before and nothing finished while it was off. An issue reopened before"
        " it was read is read once it is closed again and has settled."
    )
    assert g.check_config({"token_env": VAR, "repo": REPO})["settle_minutes"] == 30
    assert g.check_config({"token_env": VAR, "repo": REPO})["backfill_days"] == 0
    mini = Mini()
    fresh = issue_entry(4, finished=NOW - 10 * MINUTE)
    before = issue_entry(5, finished=SINCE - HOUR)
    mini.routes[f"{BASE}/issues"] = (200, [before, fresh])
    mini.routes[f"{BASE}/issues/4/comments"] = (200, [])
    first = pulled(mini)
    assert first.drafts == []  # one from before it was on, one still settling
    second = pulled(mini, first.cursor, NOW + 25 * MINUTE)
    assert [d.item for d in second.drafts] == ["issue:4"]
    edited = {**fresh, "body": "Edited.", "updated_at": "2099-06-10T13:00:00Z", "comments": 1}
    mini.routes[f"{BASE}/issues"] = (200, [edited])
    assert pulled(mini, second.cursor, NOW + 2 * HOUR).drafts == []
    assert [d.item for d in second.drafts] == ["issue:4"]  # and the runner writes an item once
    # (the I1 record: tests/acceptance/test_c11_github.py runs it through the runner)


# Starting with what finished lately (K2)


def listing_of(entries: list[dict]):
    """The issues listing over `entries`: closed ones updated at or after `since`, oldest
    update first, a hundred a page, each next page in a Link header."""

    def answer(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        page = int(params.pop("page", "1"))
        rows = sorted(
            (e for e in entries if e["updated_at"] >= params["since"]),
            key=lambda e: (e["updated_at"], e["number"]),
        )
        headers = {}
        if page * 100 < len(rows):
            following = (
                f"https://api.github.com{BASE}/issues?{urlencode({**params, 'page': page + 1})}"
            )
            headers["link"] = f'<{following}>; rel="next"'
        return httpx.Response(200, json=rows[(page - 1) * 100 : page * 100], headers=headers)

    return answer


def test_by_default_it_reads_nothing_from_before_and_backfill_days_reaches_back():
    """sources.md: "By default a source reads nothing finished before it was switched on, so
    that switching it on never pours an unknown amount of history into a project. With
    `backfill_days` (0 unless set, at most 365) it also reads the pull requests merged, the
    issues closed and the releases published in that many days before it is switched on,
    oldest first, each once.\""""
    said(
        "By default a source reads nothing finished before it was switched on, so that"
        " switching it on never pours an unknown amount of history into a project. With"
        " `backfill_days` (0 unless set, at most 365) it also reads the pull requests merged,"
        " the issues closed and the releases published in that many days before it is"
        " switched on, oldest first, each once."
    )
    assert g.check_config({"token_env": VAR, "repo": REPO, "backfill_days": 365})
    with pytest.raises(ValueError, match="backfill_days"):
        g.check_config({"token_env": VAR, "repo": REPO, "backfill_days": 366})
    mini = Mini()
    old = pull_entry(5, finished=SINCE - 40 * DAY)
    mini.routes[f"{BASE}/issues"] = (
        200,
        [old, pull_entry(3, finished=SINCE - 20 * DAY), issue_entry(4, finished=SINCE - 2 * DAY)],
    )
    mini.routes[f"{BASE}/releases"] = (200, [release_entry(1, finished=SINCE - 9 * DAY)])
    for n in (3, 5):
        mini.routes[f"{BASE}/pulls/{n}"] = (200, pull_detail(n))
    assert pulled(mini, comments=False).drafts == []
    first = pulled(mini, comments=False, backfill_days=30)
    assert [d.item for d in first.drafts] == ["pull:3", "release:7001", "issue:4"]
    assert pulled(mini, first.cursor, comments=False, backfill_days=30).drafts == []


def test_a_backfill_is_read_over_runs_25_at_a_time_and_600_items_take_24_runs():
    """sources.md: "It reads them over several runs, at most 25 items a run, so 600 items
    take 24 runs, and an item finished meanwhile takes its turn in the order they were
    finished.\""""
    said(
        "It reads them over several runs, at most 25 items a run, so 600 items take 24 runs,"
        " and an item finished meanwhile takes its turn in the order they were finished."
    )
    assert g.PER_PULL == 25
    entries = [issue_entry(n, finished=SINCE - 300 * DAY + n * 10 * HOUR) for n in range(1, 601)]
    mini = Mini()
    mini.routes[f"{BASE}/issues"] = listing_of(entries)
    now, cursor, runs, written = NOW, None, 0, []
    while runs < 30:
        got = pulled(mini, cursor, now, comments=False, backfill_days=365)
        if not got.drafts:
            break
        runs, cursor, now = runs + 1, got.cursor, now + HOUR
        written += [d.item for d in got.drafts]
        if runs == 1:  # closed meanwhile: it comes after the 600, as it closed after
            entries.append(issue_entry(601, finished=NOW + 30 * MINUTE))
    assert written == [f"issue:{n}" for n in range(1, 602)]
    assert runs == 25  # 24 for the 600, and the one closed meanwhile


def test_both_ways_of_finding_reach_back_and_a_first_check_past_search_says_so():
    """sources.md: "Both the list of what changed and the first daily check reach back that
    far; in a busy repository that first check can find more than the 1,000 results GitHub's
    search returns, and the report then says so, while the list still reads every item whose
    update time moved.\""""
    said(
        "Both the list of what changed and the first daily check reach back that far; in a"
        " busy repository that first check can find more than the 1,000 results GitHub's search"
        " returns, and the report then says so, while the list still reads every item whose"
        " update time moved."
    )
    assert g.MAX_LIST_PAGES * g.PAGE == 1_000
    mini = Mini()
    mini.routes["/search/issues"] = (
        200,
        searched(issue_entry(3, finished=SINCE - 20 * DAY), total=1_500),
    )
    mini.routes[f"{BASE}/issues"] = (200, [issue_entry(4, finished=SINCE - 10 * DAY)])
    got = pulled(mini, comments=False, backfill_days=30)
    assert [d.item for d in got.drafts] == ["issue:3", "issue:4"]
    assert [s.reason for s in got.skipped] == [g.SEARCH_PAST]
    floor = SINCE - 30 * DAY
    (listing,) = [r for r in mini.asked if r.url.path == f"{BASE}/issues"]
    assert listing.url.params["since"] == g._bound(floor)
    windows = [r.url.params["q"] for r in mini.asked if r.url.path == "/search/issues"]
    assert windows and all(q.endswith(g._qualifier(floor - g.OVERLAP)) for q in windows)


def test_switched_off_and_on_it_reads_from_the_new_switch_on_and_writes_nothing_twice(tmp_path):
    """sources.md: "Switched off and on again, it reads from that many days before the new
    switch-on, and what it wrote before is not written again.\""""
    said(
        "Switched off and on again, it reads from that many days before the new switch-on,"
        " and what it wrote before is not written again."
    )
    clock = SimpleNamespace(now=SINCE - DAY)
    store = Store(tmp_path / "cairn.db", clock=lambda: clock.now)
    try:
        store.create_project("quill")
        config = {"token_env": VAR, "repo": REPO, "projects": [], "comments": False}
        config["backfill_days"] = 10
        store.add_source("code", "github", config)
        clock.now = SINCE
        store.enable_source("code", projects=["quill"])
        entries = [issue_entry(1, finished=SINCE - 5 * DAY), issue_entry(2, finished=SINCE + DAY)]
        mini = Mini()
        mini.routes[f"{BASE}/issues"] = listing_of(entries)
        http = httpx.AsyncClient(transport=httpx.MockTransport(mini.handler))

        def run(at):
            return asyncio.run(
                run_source(store, "code", http=http, env={VAR: TOKEN}, clock=lambda: at)
            )

        assert run(SINCE + 2 * DAY).saved == 2
        clock.now = SINCE + 3 * DAY
        store.disable_source("code")
        entries += [
            issue_entry(3, finished=SINCE + 5 * DAY),
            issue_entry(4, finished=SINCE + 15 * DAY),
        ]
        clock.now = SINCE + 20 * DAY  # the new floor: SINCE + 10 days
        store.enable_source("code")
        assert run(SINCE + 21 * DAY).saved == 1  # the fourth only
        assert run(SINCE + 25 * DAY).saved == 0
        assert len(store.list_notes(store.get_project("quill").id)) == 3
    finally:
        store.close()


def test_a_linear_or_github_backfill_is_the_exception_to_nothing_from_while_it_was_off(
    tmp_path,
):
    """sources.md, "From then on": "A [Linear](#issues-from-linear-read-by-the-server) or
    [GitHub](#finished-work-from-github-read-by-the-server) source given `backfill_days` is
    the exception: it also reads what finished in that many days before it was switched on,
    the time it was off among them.\""""
    said(
        "A [Linear](#issues-from-linear-read-by-the-server) or"
        " [GitHub](#finished-work-from-github-read-by-the-server) source given `backfill_days`"
        " is the exception: it also reads what finished in that many days before it was"
        " switched on, the time it was off among them."
    )
    assert "### Issues from Linear, read by the server" in RAW
    assert "### Finished work from GitHub, read by the server" in RAW
    for kind in ("linear", "github"):
        assert "backfill_days" in KINDS[kind].settings
    # GitHub, asked: switched off for two days and on again, it reads what closed meanwhile.
    clock = SimpleNamespace(now=SINCE - DAY)
    store = Store(tmp_path / "cairn.db", clock=lambda: clock.now)
    try:
        store.create_project("quill")
        config = {"token_env": VAR, "repo": REPO, "projects": [], "comments": False}
        store.add_source("code", "github", {**config, "backfill_days": 7})
        clock.now = SINCE
        store.enable_source("code", projects=["quill"])
        entries = [issue_entry(1, finished=SINCE - 3 * DAY)]
        mini = Mini()
        mini.routes[f"{BASE}/issues"] = listing_of(entries)
        http = httpx.AsyncClient(transport=httpx.MockTransport(mini.handler))

        def run(at):
            return asyncio.run(
                run_source(store, "code", http=http, env={VAR: TOKEN}, clock=lambda: at)
            )

        assert run(SINCE + HOUR).saved == 1  # from before the switch-on
        clock.now = SINCE + DAY
        store.disable_source("code")
        entries.append(issue_entry(2, finished=SINCE + 2 * DAY))  # while it was off
        clock.now = SINCE + 3 * DAY
        store.enable_source("code")
        assert run(SINCE + 3 * DAY + HOUR).saved == 1
    finally:
        store.close()
    # Linear is asked the same through the runner in tests/unit/test_guide_facts_linear_unit.py,
    # test_switched_off_and_on_it_reads_from_the_new_switch_on_and_writes_nothing_twice: its
    # fourth issue finished while the source was off and is read after the switch-on.


def test_every_run_lists_what_moved_and_once_a_day_searches_for_what_finished():
    """sources.md: "Every run lists the repository's closed issues and pull requests whose
    update time moved, and its releases. Once a day it also asks GitHub's search for what was
    merged or closed since the last such check, because GitHub does not say that closing an
    issue moves its update time. A run writes at most 25 items; the rest wait for the next
    run, in the order they were finished.\""""
    said(
        "Every run lists the repository's closed issues and pull requests whose update time"
        " moved, and its releases. Once a day it also asks GitHub's search for what was merged"
        " or closed since the last such check, because GitHub does not say that closing an issue"
        " moves its update time. A run writes at most 25 items; the rest wait for the next run,"
        " in the order they were finished."
    )
    assert g.PER_PULL == 25 and g.CHECK_EVERY == DAY
    mini = Mini()
    found = [issue_entry(n, finished=SINCE + n * HOUR) for n in range(1, 31)]
    mini.routes["/search/issues"] = (200, searched(*found))
    for n in range(1, 31):
        mini.routes[f"{BASE}/issues/{n}/comments"] = (200, [])
    first = pulled(mini)
    listing = next(r for r in mini.asked if r.url.path == f"{BASE}/issues")
    assert dict(listing.url.params) == {
        "state": "closed",
        "sort": "updated",
        "direction": "asc",
        "since": listing.url.params["since"],
        "per_page": "100",
    }
    assert [d.item for d in first.drafts] == [f"issue:{n}" for n in range(1, 26)]
    second = pulled(mini, first.cursor, NOW + 10 * MINUTE)
    assert [d.item for d in second.drafts] == [f"issue:{n}" for n in range(26, 31)]
    asked = len([p for p in mini.paths() if p == "/search/issues"])
    pulled(mini, second.cursor, NOW + 20 * MINUTE)
    assert len([p for p in mini.paths() if p == "/search/issues"]) == asked  # not within a day
    assert f"{BASE}/releases" in mini.paths()


def test_long_items_are_cut_with_a_line_saying_so_and_signed_addresses_lose_their_query():
    """sources.md: "A description is cut after 1,000 paragraphs, list items, tables, quotes or
    code blocks (or 250,000 characters), and a comment after 200 (or 65,536 characters),
    between two of them where it can (a code block cut in its middle is closed), with a line
    saying so. At most 300 comments and 300 reviews of an item are read, and the note says
    how many comments were left out. An image or a file that GitHub signs for a few minutes
    (an address on a GitHub host whose query holds `jwt`, `token` or `X-Amz-Signature`) is
    written without its query.\""""
    said(
        "A description is cut after 1,000 paragraphs, list items, tables, quotes or code blocks"
        " (or 250,000 characters), and a comment after 200 (or 65,536 characters), between two"
        " of them where it can (a code block cut in its middle is closed), with a line saying"
        " so. At most 300 comments and 300 reviews of an item are read, and the note says how"
        " many comments were left out. An image or a file that GitHub signs for a few minutes"
        " (an address on a GitHub host whose query holds `jwt`, `token` or `X-Amz-Signature`) is"
        " written without its query."
    )
    assert (g.BODY_PASSAGES, g.BODY_CHARS) == (1_000, 250_000)
    assert (g.COMMENT_PASSAGES, g.COMMENT_CHARS) == (200, 65_536)
    assert g.MAX_TALK_PAGES * g.PAGE == 300
    assert {"paragraph", "bullet", "table", "quote", "code"} == g.PASSAGE_KINDS
    body = "\n\n".join(f"Paragraph {i}." for i in range(1, 1_201))
    image = "https://private-user-images.githubusercontent.com/1/2.png"
    long_comment = "\n\n".join(f"Point {i}." for i in range(1, 251))
    mini = Mini()
    mini.an_issue(
        4,
        comment(1, long_comment),
        comment(2, f"![shot]({image}?jwt=unitSigned)"),
        body=body,
        comments=1_000,
    )
    (draft,) = pulled(mini).drafts
    text = draft.text
    assert "Paragraph 1000." in text and "Paragraph 1001." not in text
    assert "Point 200." in text and "Point 201." not in text
    assert text.count("is left out: it is longer than a note holds.") == 2
    assert "998 more comments are left out." in text
    assert f"![shot]({image})" in text and "unitSigned" not in text


def test_the_operator_adds_it_off_with_a_fine_grained_token_named_in_the_environment(
    capsys, tmp_path
):
    """sources.md: "On GitHub, make a fine-grained personal access token limited to the one
    repository, with read-only access to Pull requests, Issues and Contents." ... "The
    operator puts the token in the server's environment, never in cairn (with Docker, a line
    in `deploy/cairn.env`), and adds the source, which starts off:" ... "Everything but
    `token_env` and `repo` is optional: `items` (`["pulls", "issues", "releases"]`, or one or
    two of them to read less), `comments` (true), `settle_minutes` (30), `lookback_days` (30:
    how many days it keeps its own record of what it wrote, which spares GitHub a second
    read; an item is written once either way), `backfill_days` (0: how many days before the
    switch-on it also reads) and `base_url` (`https://api.github.com`; for GitHub Enterprise
    Server, `https://HOST/api/v3`). For a source that names no project yet, the page proposes
    `github`.\""""
    said(
        "On GitHub, make a fine-grained personal access token limited to the one repository,"
        " with read-only access to Pull requests, Issues and Contents."
    )
    said(
        "The operator puts the token in the server's environment, never in cairn (with Docker,"
        " a line in `deploy/cairn.env`), and adds the source, which starts off:"
    )
    said(
        'Everything but `token_env` and `repo` is optional: `items` (`["pulls", "issues",'
        ' "releases"]`, or one or two of them to read less), `comments` (true),'
        " `settle_minutes` (30), `lookback_days` (30: how many days it keeps its own record of"
        " what it wrote, which spares GitHub a second read; an item is written once either"
        " way), `backfill_days` (0: how many days before the switch-on it also reads) and"
        " `base_url` (`https://api.github.com`; for GitHub Enterprise Server,"
        " `https://HOST/api/v3`). For a source that names no project yet, the page proposes"
        " `github`."
    )
    assert set(g.PERMISSIONS.values()) == {"Issues and Pull requests", "Pull requests", "Contents"}
    assert set(g.SETTINGS) == {
        "token_env", "repo", "projects", "items", "comments", "settle_minutes", "lookback_days",
        "backfill_days", "base_url",
    }  # fmt: skip
    defaults = g.check_config({"token_env": VAR, "repo": REPO})
    assert (defaults["items"], defaults["comments"]) == (["pulls", "issues", "releases"], True)
    assert (defaults["settle_minutes"], defaults["lookback_days"]) == (30, 30)
    assert defaults["backfill_days"] == 0
    assert defaults["base_url"] == "https://api.github.com"
    ghe = g.check_config(
        {"token_env": VAR, "repo": REPO, "base_url": "https://ghe.example.test/api/v3"}
    )
    assert ghe["base_url"] == "https://ghe.example.test/api/v3"
    assert g.check_config({"token_env": VAR, "repo": REPO, "items": ["issues"]})["items"] == [
        "issues"
    ]
    assert g.KIND.default_project == "github" and g.KIND.starts_off
    assert g.KEPT_PAST_LOOKBACK < DAY * 3  # the record is lookback_days, and a little more
    with pytest.raises(ValueError, match="looks like a token"):
        g.check_config({"token_env": TOKEN, "repo": REPO})
    command = re.search(r"python -m cairn\.server source-add [^\n]*--kind github[^\n]*", RAW)
    assert command, "the guide shows the command"
    argv = shlex.split(command.group(0))[3:]
    config = json.loads(argv[argv.index("--config") + 1])
    assert g.check_config(config)["repo"] == "quillworks/quill"
    db = tmp_path / "guide.db"
    Store(db).close()
    argv[argv.index("--db") + 1] = str(db)
    from cairn import server

    assert server.main(argv) == 0
    added = Store(db)
    try:
        assert added.list_sources()[0].enabled is False
    finally:
        added.close()
    assert "--project github" in capsys.readouterr().out
    with pytest.raises(SourceError, match="GITHUB_TOKEN"):
        g.open_source("code", g.check_config(config), http=None, env={})


def test_an_organisations_pending_approval_looks_like_a_404_and_the_reason_says_so():
    """sources.md: "An organisation can require that its owners approve such a token: until
    they do, GitHub answers 404 for the repository, as for one that does not exist, and the
    run fails with a reason that says the token may lack the permission or still wait for the
    organisation's approval.\""""
    said(
        "An organisation can require that its owners approve such a token: until they do,"
        " GitHub answers 404 for the repository, as for one that does not exist, and the run"
        " fails with a reason that says the token may lack the permission or still wait for the"
        " organisation's approval."
    )
    mini = Mini()
    mini.routes[f"{BASE}/issues"] = (404, {"message": "Not Found"})
    reason = failure(mini)
    assert "lacks the" in reason and "permission" in reason
    assert "waits for the organisation's approval" in reason


def test_when_github_says_no_the_run_fails_with_a_reason_except_for_a_rate_limit_or_search():
    """sources.md: "A refused token, or a 404 for the repository, fails the run, and the
    Sources page shows the reason with the variable and the repository named. A 403 fails it
    too and names what was asked for (the list of issues and pull requests, a pull request,
    its comments or reviews, or the releases), so you know which permission is missing. A
    message of GitHub's own is shown only when it holds nothing that looks like a credential.
    When GitHub asks the server to slow down (a rate limit), the run stops there without
    failing, its report says so, and the next run goes on from where it stopped. When the
    daily check's search is refused, the run does not fail: what the listing found is
    written, and the report says the search was refused.\""""
    said(
        "A refused token, or a 404 for the repository, fails the run, and the Sources page"
        " shows the reason with the variable and the repository named. A 403 fails it too and"
        " names what was asked for (the list of issues and pull requests, a pull request, its"
        " comments or reviews, or the releases), so you know which permission is missing. A"
        " message of GitHub's own is shown only when it holds nothing that looks like a"
        " credential. When GitHub asks the server to slow down (a rate limit), the run stops"
        " there without failing, its report says so, and the next run goes on from where it"
        " stopped. When the daily check's search is refused, the run does not fail: what the"
        " listing found is written, and the report says the search was refused."
    )
    for status in (401, 404):
        mini = Mini()
        mini.routes[f"{BASE}/issues"] = (status, {"message": f"Refused {TOKEN}"})
        reason = failure(mini)
        assert VAR in reason and REPO in reason and TOKEN not in reason
        assert "GitHub said" not in reason
    for path, what in (
        (f"{BASE}/issues", "the list of closed issues and pull requests"),
        (f"{BASE}/pulls/1", "pull request #1"),
        (f"{BASE}/issues/1/comments", "the comments of #1"),
        (f"{BASE}/pulls/1/reviews", "the reviews of pull request #1"),
        (f"{BASE}/releases", "the list of releases"),
    ):
        mini = Mini()
        mini.a_pull(1)
        mini.routes[path] = (403, {"message": "Resource not accessible by personal access token"})
        reason = failure(mini)
        assert what in reason and 'GitHub said: "Resource not accessible' in reason
    mini = Mini()
    mini.a_pull(1)
    mini.routes[f"{BASE}/issues"] = (403, {"message": "limit"}, {"x-ratelimit-remaining": "0"})
    got = pulled(mini)
    assert (got.drafts, got.skipped, got.cursor) == ([], [], None)
    assert got.stopped == g.RATE_LIMITED  # the report keeps it (SyncReport.said)
    assert "slow down" in g.RATE_LIMITED and "next run goes on" in g.RATE_LIMITED
    mini = Mini()
    mini.a_pull(1)
    mini.routes["/search/issues"] = (403, {"message": "Forbidden"})
    got = pulled(mini)
    assert [d.item for d in got.drafts] == ["pull:1"] and got.skipped == []
    assert got.stopped == g.SEARCH_REFUSED.format(status=403)
    assert "refused the daily check's search" in got.stopped
