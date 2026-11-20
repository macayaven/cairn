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

import re
import subprocess

import pytest
from ci_shell import SYSTEM
from test_ci_release_retry_unit import (
    CREATE,
    README,
    attached,
    held,
    made_earlier,
    run,
    writes,
)
from test_ci_workflow_unit import JOBS

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
            'released="$(cat)"',
            "is_latest",
        ]
    )
    done = subprocess.run(
        ["/bin/bash", "-e", "-c", script],
        input="".join(f"{name}\n" for name in released),
        env={"PATH": SYSTEM, "GITHUB_REF_NAME": tag},
        capture_output=True,
        text=True,
        check=True,
    )
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
    script = function(ALIAS["run"], "stable_versions") + "\nstable_versions"
    done = subprocess.run(
        ["/bin/bash", "-e", "-c", script],
        input="v0.10.0\nv0.9.12\nnightly\nv0.9.2\nv0.2\nv1.0.0-rc.1\n",
        env={"PATH": SYSTEM},
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout == "0.9.2\n0.9.12\n0.10.0\n"


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
