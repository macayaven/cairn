"""Job `release`'s publication always says whether the release is GitHub's latest: a candidate
never is, and a stable version is only when it is the highest stable version among the
repository's published releases (as the job's last look before the publication listed them)
and itself, ordered by the same function job `aliases` orders versions with.

The fault (the independent tester's finding outside the six, decision 4): the publication sent
`draft=false` alone, and GitHub's `make_latest` "Defaults to `true` for newly published
releases" (GitHub's REST reference, "Update a release"), so a stable backport, v1.2.3 tagged
while v1.3.0 is released, became the repository's Latest release while job `aliases` pointed the
image's `latest` at 1.3.0.

The two jobs check out nothing and cannot share a file, so each holds the function
`stable_versions`, word for word, and a test here holds them to that.

gh is test_ci_release_retry_unit's stand-in: its listing runs the step's own --jq expression
over every release it holds, other tags' too."""

import json
import re

import pytest
from ci_shell import SYSTEM, run_step
from test_ci_release_retry_unit import (
    CREATE,
    README,
    TAG_READ,
    attached,
    held,
    made_earlier,
    run,
    the_error,
    writes,
)
from test_ci_workflow_unit import JOBS, REPO

(ALIAS,) = [s for s in JOBS["aliases"]["steps"] if s.get("name", "").startswith("Point latest")]
PUBLICATION = "gh api --method PATCH repos/owner/repo/releases/{id} --silent -F draft=false"


def function(run: str, name: str) -> str:
    """The text of shell function NAME in RUN, from its first line to its closing brace, each
    line stripped of its indent."""
    found = re.search(rf"^( *){name}\(\) \{{\n.*?^\1\}}$", run, re.M | re.S)
    assert found, name
    return "\n".join(line.strip() for line in found[0].splitlines())


def other(tag: str, rid: int, draft: bool = False, prerelease: bool = False) -> dict:
    """A release of another tag, as a person or an earlier version's run left it."""
    return {
        **made_earlier(tag, prerelease, draft=draft, body=f"the notes of {tag}\n"),
        "id": rid,
        "assets": [],
    }


def published_with(calls: list[str]) -> str:
    (line,) = [line for line in writes(calls) if line.startswith("gh api --method PATCH")]
    return line


# --- what the publication says ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("others", "latest"),
    [
        ([], "true"),
        ([other("v1.3.0", 60)], "false"),
        ([other("v1.2.2", 60), other("v1.1.9", 61)], "true"),
        ([other("v1.2.10", 60)], "false"),
        ([other("v1.10.0", 60), other("v1.9.9", 61)], "false"),
        ([other("v2.0.0-rc.1", 60, prerelease=True)], "true"),
        ([other("v1.3.0", 60, draft=True)], "true"),
        ([other("nightly", 60), other("v1.3", 61)], "true"),
    ],
    ids=[
        "the first release ever",
        "a backport below a released higher version",
        "above every released version",
        "numbers, not text: 1.2.10 is above 1.2.3",
        "numbers, not text: 1.10.0 is above 1.9.9",
        "a candidate's release is not a stable version",
        "a draft is not published",
        "tags that are not versions",
    ],
)
@pytest.mark.parametrize("existing", [None, "draft"], ids=["made now", "left by an attempt"])
def test_a_stable_release_says_whether_it_is_github_s_latest(tmp_path, others, latest, existing):
    ours = [made_earlier("v1.2.3", False, draft=True)] if existing else []
    done, calls = run(tmp_path, "v1.2.3", "", [*others, *ours], attached("v1.2.3"))
    assert done.returncode == 0, done.stdout + done.stderr
    rid = 77 if existing else 88
    assert published_with(calls) == PUBLICATION.format(id=rid) + f" -f make_latest={latest}"
    assert [r for r in held(tmp_path) if r["id"] in (60, 61)] == others


@pytest.mark.parametrize("others", [[], [other("v0.9.0", 60)]], ids=["alone", "above another"])
def test_a_candidate_is_never_the_latest(tmp_path, others):
    done, calls = run(tmp_path, "v1.2.3-rc.1", "true", others)
    assert done.returncode == 0, done.stdout + done.stderr
    assert published_with(calls) == PUBLICATION.format(id=88) + " -f make_latest=false"


# --- one ordering for both jobs -------------------------------------------------------------------


def test_release_and_aliases_order_versions_with_one_function():
    ours = function(CREATE["run"], "stable_versions")
    assert ours == function(ALIAS["run"], "stable_versions")
    assert "stable_versions" in ALIAS["run"].split("stable_versions() {", 1)[1]  # and uses it


def latest_of(released: list[str], tag: str) -> str:
    """The step's own two functions, run on RELEASED (the published tags the last look listed)
    for TAG."""
    run = CREATE["run"]
    script = "\n".join(
        [
            function(run, "stable_versions"),
            function(run, "is_latest"),
            'released="$RELEASED"',
            "is_latest",
        ]
    )
    env = {"PATH": SYSTEM, "GITHUB_REF_NAME": tag, "RELEASED": "\n".join(released)}
    done = run_step(script, env)
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.mark.parametrize(
    ("released", "tag", "latest"),
    [
        ([], "v1.2.3", "true"),
        (["v1.2.3"], "v1.2.3", "true"),
        (["v1.2.3", "v1.2.2"], "v1.2.3", "true"),
        (["v1.3.0"], "v1.2.3", "false"),
        (["v0.10.0"], "v0.9.12", "false"),
        (["v0.9.12"], "v0.10.0", "true"),
        (["v01.3.0", "v1.3", "nightly"], "v1.2.3", "true"),
    ],
    ids=[
        "the first release ever",
        "the version equal to the highest",
        "above the rest, equal to itself",
        "a backport",
        "numbers, not text",
        "numbers, not text, the other way",
        "what is not a version is not compared",
    ],
)
def test_the_step_s_comparison_takes_the_highest_of_the_listed_and_itself(released, tag, latest):
    assert latest_of(released, tag) == latest + "\n"


def test_the_aliases_job_orders_with_it_as_before(tmp_path):
    """The function, run alone as `aliases` runs it: the stable versions, lowest first."""
    tags = "v0.10.0 v0.9.12 nightly v0.9.2 v0.2 v1.0.0-rc.1"
    ordered = f"printf '%s\\n' {tags} | stable_versions"
    script = function(ALIAS["run"], "stable_versions") + "\n" + ordered
    done = run_step(script, {"PATH": SYSTEM})
    assert done.returncode == 0, done.stderr
    assert done.stdout == "0.9.2\n0.9.12\n0.10.0\n"


# --- one release job at a time, and Latest checked after the publication -----------------------
#
# The tester's finding of round rl9: v1.2.2 is Latest; this run (v1.2.3) looks, makes its draft
# and reads it; another tag's run publishes v1.3.0, which becomes Latest; this run publishes with
# make_latest=true from its earlier look, and Latest moves down to v1.2.3, the job ending well.


def test_one_release_job_runs_at_a_time_in_the_repository_and_none_waiting_is_replaced():
    """Another tag's `release` waits for this one, so no run of this workflow publishes between
    this job's last look and its publication; `queue: max` keeps a job that waits from being
    replaced by a newer one, as a waiting `aliases` is (GitHub's default for a group)."""
    release, aliases = JOBS["release"]["concurrency"], JOBS["aliases"]["concurrency"]
    assert release == {"group": "cairn-release", "cancel-in-progress": False, "queue": "max"}
    assert release["group"] != aliases["group"]


HIGHER = other("v1.3.0", 60)
LATEST_LOST = (
    "::error::the GitHub release for v1.2.3, read by its id, is this run's and public, with the"
    " title and the notes this run wrote and exactly the files this run attested, and this"
    " job's publication asked GitHub to make it the repository's latest release; GitHub's"
    " listing after the publication shows v1.3.0 published, a higher stable version, so"
    " GitHub's latest release may now be v1.2.3, below it: on the repository's releases page"
    " set v1.3.0 as the latest release, then re-run this job, which finds v1.2.3 published and"
    " changes nothing, so that job aliases runs"
)


@pytest.mark.parametrize("existing", [None, "draft"], ids=["made now", "left by an attempt"])
def test_a_higher_version_listed_after_a_publication_that_said_latest_stops_the_job(
    tmp_path, existing
):
    """A person publishes v1.3.0 by hand after the job's last look and before its publication:
    the publication says latest, from that look; the look after it lists v1.3.0, and the job
    stops with a line of its own, after every other check, and edits no other release."""
    ours = [made_earlier("v1.2.3", False, draft=True)] if existing else []
    lower = other("v1.2.2", 61)
    done, calls = run(
        tmp_path,
        "v1.2.3",
        "",
        [lower, *ours],
        attached("v1.2.3"),
        ADDED_BEFORE_PUBLICATION=json.dumps(HIGHER),
    )
    assert done.returncode == 1, done.stdout + done.stderr
    rid = 77 if existing else 88
    assert published_with(calls) == PUBLICATION.format(id=rid) + " -f make_latest=true"
    assert the_error(done) == LATEST_LOST
    assert len(writes(calls)) == (1 if existing else 2)
    releases = {r["id"]: r for r in held(tmp_path)}
    assert releases[60] == HIGHER and releases[61] == lower and not releases[rid]["draft"]
    assert calls[-1] == TAG_READ.format(tag="v1.2.3")  # its checks all made first


@pytest.mark.parametrize(
    ("tag", "candidate", "others"),
    [
        ("v1.2.3", "", [HIGHER]),
        ("v1.2.3-rc.1", "true", []),
    ],
    ids=["a backport, said not latest", "a candidate, never latest"],
)
def test_a_publication_that_did_not_say_latest_is_not_checked(tmp_path, tag, candidate, others):
    done, calls = run(
        tmp_path,
        tag,
        candidate,
        others,
        attached(tag),
        ADDED_BEFORE_PUBLICATION=json.dumps(other("v1.4.0", 62)),
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert published_with(calls).endswith(" -f make_latest=false")


def test_a_re_run_that_finds_its_release_published_sends_and_checks_nothing_of_latest(tmp_path):
    """The attempt that published it is the one that could check; a re-run writes nothing."""
    done, calls = run(
        tmp_path, "v1.2.3", "", [HIGHER, made_earlier("v1.2.3", False)], attached("v1.2.3")
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert writes(calls) == []


# --- the documents --------------------------------------------------------------------------------


def test_the_readme_and_the_comment_say_what_the_publication_sends():
    readme = " ".join(README.split())
    assert "which sets `draft` to false and nothing else" not in readme
    assert (
        "The job's one write to a release that exists is the publication, which sets `draft` to"
        " false and says whether the release is GitHub's latest, and nothing else: a candidate"
        " never is, and a stable version is only when it is the highest stable version among"
        " the published releases the job's last look listed and itself, compared as job"
        " `aliases` compares versions, so a backport below a released version does not take"
        " GitHub's latest from it."
    ) in readme
    comment = " ".join(CREATE["run"].replace("#", "").split())
    assert "it sets draft to false and nothing else" not in comment
    assert '"Defaults to `true` for newly published releases"' in comment


def header_of(job: str) -> str:
    """The comment above JOB in the workflow's text, its lines joined."""
    text = (REPO / ".github" / "workflows" / "ci-cd.yml").read_text()
    above = text.split(f"\n  {job}:\n")[0].rsplit("\n\n", 1)[1]
    return " ".join(line.strip().removeprefix("#").strip() for line in above.splitlines())


def test_the_readme_and_the_comments_say_one_release_job_at_a_time_and_the_check_after():
    readme = " ".join(README.split())
    assert (
        "One `release` job runs at a time across the repository's runs (its concurrency group,"
        " in which up to 100 wait and none waiting is replaced by a newer one; that GitHub takes"
        " this for a job is read in its documentation, not measured), so no other tag's"
        " `release` publishes between the job's last look and its publication. The look after"
        " the publication checks what it said: when it lists a higher stable version and the"
        " publication said the release is GitHub's latest, the job stops with a line of its own"
        " and edits no other release (the table has the line). A release published by hand, or"
        " one GitHub's listing shows only after the job's last look, can still leave GitHub's"
        " latest on a lower version, and the job cannot see it."
    ) in readme
    assert (
        "| `the GitHub release for …, read by its id, is this run's and public, with the title"
        " and the notes this run wrote and exactly the files this run attested, and this job's"
        " publication asked GitHub to make it the repository's latest release` |"
    ) in readme
    header = header_of("release")
    for said in (
        "One such job runs at a time across all runs (its concurrency group, of its own):"
        " another tag's `release` waits until this one ends,",
        "`queue: max` lets up to 100 wait: a job waiting is not replaced by a newer one, as"
        " GitHub does by default and as a waiting `aliases` is, which would leave its tag"
        " without a release.",
        "A re-run of this job waits as a first run does.",
        "a candidate and its release tagged close together run one after the other, either"
        " first, and a candidate published after its release is a pre-release, never the"
        " latest.",
        "A run re-run later keeps the workflow of its own commit, and a commit from before this"
        " group has none.",
        "That GitHub takes `queue` in a job's concurrency is read in its workflow syntax"
        " reference, not measured.",
    ):
        assert said in header, said
    comment = " ".join(CREATE["run"].replace("#", "").split())
    assert (
        "The look after the publication checks it: when it lists a higher stable version and"
        " the publication said latest, the job stops with a line of its own and edits no other"
        " release"
    ) in comment
