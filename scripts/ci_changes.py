"""Says whether a CI run needs the image jobs, from the paths its change touches.

Job `changes` in .github/workflows/ci-cd.yml runs this first. It writes `image=false` to
$GITHUB_OUTPUT only when the changed paths are known and every one of them is on
NO_IMAGE_WORK, the paths no image job reads; then `image`, `stack` and `host` are skipped,
and with them `publish`, `verify` and `deploy`. In every other case it writes `image=true`
and the whole gate runs, as before this script: a path not on the list, an empty list, a
manual run, a tag, a first or forced push, a lookup that fails, anything unexpected. Being
wrong costs runner minutes, never a skipped check. `test` and `infra` do not depend on it.

How the changed paths are found (the checkout has the whole history, fetch-depth 0):
- pull_request: GITHUB_SHA is the merge commit GitHub made of the pull request's head into
  its base. Its parent that is not the head (github.event.pull_request.head.sha) is the
  base it was merged into; the paths are those that differ between that parent and the
  merge commit, which is everything the pull request changes once merged. A commit that is
  not a merge of the head with one other parent is unknown.
- push to main: not what this push changed, but what differs between the head of the
  newest run on main that published an image and GITHUB_SHA. That run is found with
  GitHub's REST API (last_published): the newest runs of this workflow on main for a push,
  newest first; the first one whose job PUBLISH_JOB concluded `success`, if that run
  concluded `success` too. A run that skipped its image jobs or failed before it published
  is passed over, so a change whose image was never published (its run failed, it was
  cancelled while pending) is still in the diff, and the next push deploys it. Unknown:
  the newest run that published did not end green (its deploy was refused, failed or was
  cancelled: the VM may run an image no green run describes), an earlier run that has not
  finished, no run that published among the RUNS_LOOKED_AT newest, a head that is not an
  ancestor of GITHUB_SHA, a push that created the ref, deleted it or was forced, and any
  lookup that fails or answers in a shape its documentation does not give.
- push to another branch: from the event's `before` to `after`, which must be GITHUB_SHA;
  unknown when `before` is not a full SHA, is all zeros or is not in the history, or the
  push created, deleted or forced the ref. (The workflow runs on pushes to main and tags
  only.)
- a tag, a manual run or any other event: unknown.
`git diff --name-only -z --no-renames` lists both the old and the new path of a rename and
prints every name as it is, spaces and non-ASCII included.

When the paths are not known, the verdict line says why, from WHY_NOT_KNOWN: each branch
that ends in "not known" raises NotKnown with its case, and the line is built from it alone.
The reason is printed on one line, its control characters escaped, the token hidden, and it
is cut at REASON_BOUND characters. Each changed path is printed the same way on a line of its
own, after its mark, with its own backslashes doubled, cut at PATH_BOUND. Nothing is printed
before image= is written, and say() prints every line without ever raising.

Standard library only; it runs on the runner's own python3, before anything is installed.
To add a path to the list, first read every step of `image`, `stack`, `host`, `publish`,
`verify` and `deploy`, the Dockerfile, .dockerignore and every script those steps run, and
make sure none of them reads it. tests/unit/test_ci_changes_unit.py checks the steps and the
scripts for the names on the list.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping

# Paths no image-related job reads. An entry ending in "/" is a top-level directory and
# everything under it; any other entry is one top-level file. Not here, because those jobs
# read them: README.md, pyproject.toml, uv.lock, src/ (the image itself); Dockerfile and
# .dockerignore; scripts/ (smoke test, rehearsals, deploy); deploy/ (the stack rehearsal runs
# the compose files and commands from deploy/README.md); infra/ (the host rehearsal renders
# infra/gcp/cloud-init.yaml.tftpl and runs commands from infra/gcp/README.md); .github/.
NO_IMAGE_WORK = (
    "docs/",
    "tests/",
    "evals/",
    "examples/",
    ".github/ISSUE_TEMPLATE/",
    ".github/pull_request_template.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
    ".gitignore",
)

# The display name of job `publish` in ci-cd.yml: the API names a job by it. A test pins the
# two together. Were they to drift apart, no run would count as published and the whole gate
# would run on every push to main: minutes, not a skipped check.
PUBLISH_JOB = "publish to GHCR"
# How many of the newest runs on main are read: one page of the list, and at most one job
# listing for each. Every run passed over between the newest and the one that published is
# a push to main that changed only paths on NO_IMAGE_WORK, or a run that failed or was
# cancelled; 30 of them in a row means the gate runs once more and publishes, which resets
# the count. 31 requests at most, against the job token's 1,000 an hour.
RUNS_LOOKED_AT = 30
MAIN = "refs/heads/main"

Git = Callable[..., str]
Api = Callable[[str, dict], object]  # GET path?query -> the JSON it answered; raises on failure
_SHA = re.compile(r"[0-9a-f]{40}")
_ZEROS = "0" * 40
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

NOT_KNOWN = "the changed paths are not known"
# Why the changed paths are not known, one sentence for each way of not knowing. NotKnown
# names its case and fills in the details; nothing else words a reason.
WHY_NOT_KNOWN = {
    "not_a_sha": "GITHUB_SHA ({sha}) is not a full commit SHA",
    "other_event": "a {event} event on {ref} is neither a pull request nor a push to a branch",
    "no_head": "the pull request event gives no head commit SHA",
    "not_a_merge": (
        "the commit checked out is not a merge of the pull request's head with one other"
        " parent (its parents: {parents})"
    ),
    "created": "the push created branch {branch}: there is nothing before it to compare with",
    "deleted": "the push deleted branch {branch}",
    "forced": "the push to {branch} was forced: what it replaced may be gone from the history",
    "after": "the push's after commit, {after}, is not the commit checked out, {sha}",
    "no_lookup": "on main, nothing was given to look up the last run that published an image",
    "no_runs": "on main, the API lists no earlier run of this workflow for a push",
    "running": "on main, an earlier run, #{number}, has not finished ({status})",
    "none_published": "on main, none of the {count} newest runs published an image",
    "not_green": (
        "on main, the newest run that published an image, #{number}, ended {conclusion},"
        " not success: the server may run an image no green run describes"
    ),
    "lookup_failed": "the lookup of the last run that published an image failed ({error})",
    "bad_base": "on main, the lookup gave {base}, which is not a full commit SHA",
    "not_ancestor": (
        "on main, the published commit {base} is not an ancestor of this one,"
        " or is not in the checkout{detail}"
    ),
    "no_before": "the push to {branch} gives no before commit",
    "zero_before": "the push to {branch} gives a before commit of all zeros",
    "bad_before": "the push to {branch} gives a before that is not a full commit SHA: {before}",
    "git_failed": "git failed: `{asked}` ({error})",
    "unexpected": "something unexpected failed while reading the change ({error})",
}
# The longest reason printed, in characters, after escaping; a longer one is cut and ends
# with CUT. Long enough for two SHAs and git's first words, short enough for one log line.
REASON_BOUND = 300
# The longest changed path printed, in characters, after escaping. Git allows 4,096 bytes,
# which escaped can be six times as long; real paths here are under 200. 1,024 keeps every
# path anyone would read whole and keeps one odd name from filling the log.
PATH_BOUND = 1024
CUT = "... [cut]"


class NotKnown(Exception):
    """The changed paths cannot be known: CASE, a key of WHY_NOT_KNOWN, with its DETAILS."""

    def __init__(self, case: str, **details: object):
        super().__init__(case)
        self.case, self.details = case, details

    @property
    def reason(self) -> str:
        """The sentence for this case; never raises."""
        try:
            return WHY_NOT_KNOWN[self.case].format(**self.details)
        except Exception:
            return f"no reason could be worded for case {self.case}"


def _described(error: BaseException) -> str:
    """How ERROR failed, in one phrase: git's exit code and its own words, or the error's
    type and message. Never raises."""
    try:
        if isinstance(error, subprocess.CalledProcessError):
            said = error.stderr
            if isinstance(said, bytes):
                said = said.decode("utf-8", "replace")
            said = said.strip() if isinstance(said, str) else ""
            return f"exit {error.returncode}" + (f": {said}" if said else "")
        return f"{type(error).__name__}: {error}"
    except Exception:
        return type(error).__name__


def printable(
    text: str, token: str | None, bound: int = REASON_BOUND, backslash: str = "\\"
) -> str:
    """TEXT for one line of the log: TOKEN replaced by [token], line breaks and other control
    characters escaped (as `\\n`, `\\x1b`, `\\u2028`, `\\udcff`), each backslash of its own
    written as BACKSLASH, and cut at BOUND characters with CUT. The token goes first, so a
    cut can never leave part of it."""
    if token:
        text = text.replace(token, "[token]")
    line = "".join(
        char.encode("unicode_escape").decode("ascii")
        if unicodedata.category(char)[0] == "C" or unicodedata.category(char) in ("Zl", "Zp")
        else backslash
        if char == "\\"
        else char
        for char in text
    )
    if len(line) > bound:
        line = line[: bound - len(CUT)] + CUT
    return line


def say(line: str) -> None:
    """Prints LINE to stdout; when stdout cannot take it, its ASCII-escaped form; when it
    cannot take that either, nothing. Never raises: what is printed never moves image=."""
    for form in (str, lambda text: text.encode("ascii", "backslashreplace").decode("ascii")):
        try:
            sys.stdout.write(form(line) + "\n")
            return
        except Exception:
            continue


def needs_no_image_work(path: str) -> bool:
    """True when PATH is on NO_IMAGE_WORK. A path that is not plain (absolute, with an empty,
    `.` or `..` segment) is never on it."""
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    for entry in NO_IMAGE_WORK:
        if entry.endswith("/"):
            if path.startswith(entry) and len(path) > len(entry):
                return True
        elif path == entry:
            return True
    return False


def nothing_ships(paths: Iterable[str]) -> bool:
    """True only for a non-empty list of paths that are all on NO_IMAGE_WORK. An empty list
    cannot be told apart from a lookup that printed nothing, so it ships."""
    paths = list(paths)
    return bool(paths) and all(needs_no_image_work(path) for path in paths)


def run_git(*args: str, cwd: str | os.PathLike | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, stdin=subprocess.DEVNULL
    )
    return result.stdout.decode("utf-8", "surrogateescape")


def _ask(git: Git, *args: str) -> str:
    """GIT's answer to ARGS; when it fails, NotKnown says what git was asked and how it
    failed."""
    try:
        return git(*args)
    except Exception as error:
        asked = " ".join(("git", *args))
        raise NotKnown("git_failed", asked=asked, error=_described(error)) from None


def _diff(git: Git, base: str, sha: str) -> list[str]:
    out = _ask(git, "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", base, sha)
    return [path for path in out.split("\0") if path]


def github_api(api_url: str, token: str, urlopen=urllib.request.urlopen, timeout=20) -> Api:
    """GET requests to GitHub's REST API at API_URL ($GITHUB_API_URL) with TOKEN."""
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._/-]*)?", api_url or ""):
        raise ValueError("GITHUB_API_URL is not an https address")
    if not token:
        raise ValueError("no token for the API")

    def get(path: str, query: dict) -> object:
        url = api_url.rstrip("/") + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "cairn-ci-changes",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise ValueError(f"the API answered {response.status}")
            return json.loads(response.read().decode("utf-8"))

    return get


def workflow_file(workflow_ref: str, repository: str) -> str:
    """The file name of this workflow, from $GITHUB_WORKFLOW_REF
    (OWNER/REPO/.github/workflows/NAME.yml@REF)."""
    path = (workflow_ref or "").partition("@")[0]
    prefix = f"{repository}/.github/workflows/"
    name = path.removeprefix(prefix)
    if not repository or not path.startswith(prefix) or not re.fullmatch(r"[\w.-]+\.ya?ml", name):
        raise ValueError(f"not a workflow of {repository}: {workflow_ref!r}")
    return name


def _checked_run(item: object) -> dict:
    """A run as the list's documentation gives it, for a push to main; anything else raises."""
    if not isinstance(item, dict):
        raise ValueError("a run that is not an object")
    if not (isinstance(item.get("id"), int) and isinstance(item.get("run_number"), int)):
        raise ValueError("a run without an id or a number")
    if not _SHA.fullmatch(str(item.get("head_sha"))):
        raise ValueError(f"a run whose head commit is not a SHA: {item.get('head_sha')!r}")
    if item.get("event") != "push" or item.get("head_branch") != "main":
        event, branch = item.get("event"), item.get("head_branch")
        raise ValueError(
            f"a run that is not for a push to main: event {event!r}, branch {branch!r}"
        )
    if not isinstance(item.get("status"), str):
        raise ValueError("a run without a status")
    return item


def _published(api: Api, repository: str, run_id: int) -> bool:
    """Whether the job PUBLISH_JOB of run RUN_ID (its latest attempt) concluded success."""
    listing = api(
        f"/repos/{repository}/actions/runs/{run_id}/jobs", {"filter": "latest", "per_page": 100}
    )
    jobs = listing.get("jobs") if isinstance(listing, dict) else None
    if not isinstance(jobs, list) or listing.get("total_count") != len(jobs):
        total = listing.get("total_count") if isinstance(listing, dict) else None
        listed = len(jobs) if isinstance(jobs, list) else None
        raise ValueError(
            f"a job listing that is not one page of jobs: total_count {total!r}, jobs {listed}"
        )
    if not all(isinstance(job, dict) and isinstance(job.get("name"), str) for job in jobs):
        raise ValueError("a job without a name")
    publishing = [job for job in jobs if job["name"] == PUBLISH_JOB]
    if len(publishing) > 1:
        raise ValueError(f"{len(publishing)} jobs named {PUBLISH_JOB!r}")
    return bool(publishing) and publishing[0].get("conclusion") == "success"


def last_published(api: Api, repository: str, workflow: str, this_run: int | None) -> str | None:
    """find_base, with None where it raises NotKnown."""
    try:
        return find_base(api, repository, workflow, this_run)
    except NotKnown:
        return None


def find_base(api: Api, repository: str, workflow: str, this_run: int | None) -> str:
    """The head commit of the newest run of WORKFLOW on main, for a push, whose job
    PUBLISH_JOB concluded success, when that run concluded success too. Raises NotKnown when
    that run did not, when there is no such run among the RUNS_LOOKED_AT newest, or when an
    earlier run has not finished. THIS_RUN ($GITHUB_RUN_ID) is the run asking, which is in
    progress and is left out. Raises ValueError when an answer is not what the API's
    documentation gives."""
    if not _REPOSITORY.fullmatch(repository or ""):
        raise ValueError(f"not a repository: {repository!r}")
    listing = api(
        f"/repos/{repository}/actions/workflows/{workflow}/runs",
        {
            "branch": "main",
            "event": "push",
            "per_page": RUNS_LOOKED_AT,
            "exclude_pull_requests": "true",
        },
    )
    items = listing.get("workflow_runs") if isinstance(listing, dict) else None
    if not isinstance(items, list):
        raise ValueError("a run listing without its runs")
    runs = [_checked_run(item) for item in items]
    runs = [one for one in runs if one["id"] != this_run]
    numbers = [one["run_number"] for one in runs]
    # The documentation does not give the order; this reads the newest first, so it checks.
    if numbers != sorted(numbers, reverse=True) or len(set(numbers)) != len(numbers):
        raise ValueError("runs not listed newest first")
    going = [one for one in runs if one["status"] != "completed"]
    if going:  # an earlier run on main is still going: not known
        raise NotKnown("running", number=going[0]["run_number"], status=going[0]["status"])
    if not runs:
        raise NotKnown("no_runs")
    for one in runs[:RUNS_LOOKED_AT]:
        if _published(api, repository, one["id"]):
            if one.get("conclusion") == "success":
                return one["head_sha"]
            # Published, then failed or was cancelled (a refused or failed deploy): the VM may
            # run that image, which no green run describes. Not known.
            number, conclusion = one["run_number"], one.get("conclusion")
            raise NotKnown("not_green", number=number, conclusion=conclusion)
    raise NotKnown("none_published", count=len(runs[:RUNS_LOOKED_AT]))


def changed_paths(
    event_name: str,
    ref: str,
    sha: str,
    event: Mapping,
    git: Git = run_git,
    published: Callable[[], str | None] | None = None,
) -> list[str] | None:
    """find_paths, with None where it raises NotKnown."""
    try:
        return find_paths(event_name, ref, sha, event, git, published)
    except NotKnown:
        return None


def find_paths(
    event_name: str,
    ref: str,
    sha: str,
    event: Mapping,
    git: Git = run_git,
    published: Callable[[], str | None] | None = None,
) -> list[str]:
    """The paths the change touches. Raises NotKnown, with the reason, when they cannot be
    known, whatever goes wrong; raises nothing else. On main, PUBLISHED gives the head of the
    newest run that published an image (see find_base)."""
    try:
        return _find_paths(event_name, ref, sha, event, git, published)
    except NotKnown:
        raise
    except Exception as error:
        raise NotKnown("unexpected", error=_described(error)) from None


def _find_paths(event_name, ref, sha, event, git, published) -> list[str]:
    if not _SHA.fullmatch(sha or ""):
        raise NotKnown("not_a_sha", sha=sha)
    if event_name == "pull_request":
        head = (event.get("pull_request") or {}).get("head") or {}
        head_sha = head.get("sha") or ""
        if not _SHA.fullmatch(head_sha):
            raise NotKnown("no_head")
        listed = _ask(git, "rev-list", "--parents", "-n", "1", sha).split()
        others = [parent for parent in listed[1:] if parent != head_sha]
        if len(listed) != 3 or listed[0] != sha or head_sha not in listed[1:] or len(others) != 1:
            parents = " ".join(parent[:12] for parent in listed[1:]) or "none"
            raise NotKnown("not_a_merge", parents=parents)
        return _diff(git, others[0], sha)
    if event_name == "push" and ref.startswith("refs/heads/"):
        branch = ref.removeprefix("refs/heads/")
        if event.get("created"):
            raise NotKnown("created", branch=branch)
        if event.get("deleted"):
            raise NotKnown("deleted", branch=branch)
        if event.get("forced"):
            raise NotKnown("forced", branch=branch)
        if ref == MAIN:
            if event.get("after") != sha:
                raise NotKnown("after", after=event.get("after"), sha=sha)
            if published is None:
                raise NotKnown("no_lookup")
            try:
                base = published()
            except NotKnown:
                raise
            except Exception as error:
                raise NotKnown("lookup_failed", error=_described(error)) from None
            if not _SHA.fullmatch(base or ""):
                raise NotKnown("bad_base", base=base)
            asked = ("merge-base", "--is-ancestor", base, sha)
            try:
                git(*asked)  # raises when it is not one
            except subprocess.CalledProcessError as error:
                # Exit 1: not an ancestor. Another code: git's words say why (not in the checkout).
                detail = "" if error.returncode == 1 else f" ({_described(error)})"
                raise NotKnown("not_ancestor", base=base, detail=detail) from None
            except Exception as error:
                asked = " ".join(("git", *asked))
                raise NotKnown("git_failed", asked=asked, error=_described(error)) from None
            return _diff(git, base, sha)
        before = event.get("before")
        if not before:
            raise NotKnown("no_before", branch=branch)
        if before == _ZEROS:
            raise NotKnown("zero_before", branch=branch)
        if not _SHA.fullmatch(before):
            raise NotKnown("bad_before", branch=branch, before=before)
        if event.get("after") != sha:
            raise NotKnown("after", after=event.get("after"), sha=sha)
        return _diff(git, before, sha)
    raise NotKnown("other_event", event=event_name, ref=ref)


def verdict(paths: list[str] | None, why: str | None = None) -> tuple[bool, str]:
    """Whether the image jobs run for these changed PATHS (None: not known, for the reason
    WHY), and why."""
    if paths is None:
        return True, f"{NOT_KNOWN}: {why}" if why else NOT_KNOWN
    if not paths:
        return True, "no changed path was found"
    if nothing_ships(paths):
        return False, f"all {len(paths)} changed paths are on NO_IMAGE_WORK"
    off = [path for path in paths if not needs_no_image_work(path)]
    return True, f"{len(off)} of {len(paths)} changed paths are not on NO_IMAGE_WORK"


def decide(
    event_name: str,
    ref: str,
    sha: str,
    event: Mapping,
    git: Git = run_git,
    published: Callable[[], str | None] | None = None,
) -> tuple[bool, str]:
    """Whether the image jobs run for this event, and why."""
    try:
        return verdict(find_paths(event_name, ref, sha, event, git, published))
    except NotKnown as unknown:
        return verdict(None, unknown.reason)


def main(
    environ: Mapping[str, str] = os.environ, git: Git = run_git, api: Api | None = None
) -> int:
    """Writes image=true or image=false to $GITHUB_OUTPUT, then says why. Always exits 0
    when it could write: a failure before that writes image=true, so the whole gate runs.
    The API is asked only on a push to main, with $GITHUB_TOKEN, at $GITHUB_API_URL."""

    def published() -> str:
        client = api or github_api(environ.get("GITHUB_API_URL", ""), environ.get("GITHUB_TOKEN"))
        repository = environ.get("GITHUB_REPOSITORY", "")
        workflow = workflow_file(environ.get("GITHUB_WORKFLOW_REF", ""), repository)
        this_run = environ.get("GITHUB_RUN_ID", "")
        base = find_base(
            client, repository, workflow, int(this_run) if this_run.isdigit() else None
        )
        bases.append(base)
        return base

    bases: list[str] = []  # the base the lookup gave, said once image= is written
    paths = None
    try:
        with open(environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
            event = json.load(handle)
        args = (environ["GITHUB_EVENT_NAME"], environ["GITHUB_REF"], environ["GITHUB_SHA"])
        try:
            paths = find_paths(*args, event, git, published)
            run, reason = verdict(paths)
        except NotKnown as unknown:
            run, reason = verdict(None, unknown.reason)
    except Exception as error:
        run, reason = True, f"could not decide ({type(error).__name__}: {error})"
    line = f"image={'true' if run else 'false'}\n"
    output = environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(line)
    else:
        sys.stdout.write(line)
    # Everything below is for the log, after image= is written, and goes through say(), which
    # never raises. Each line is one line: the paths' odd characters are escaped and their own
    # backslashes doubled, so an escaped name cannot pass for another.
    token = environ.get("GITHUB_TOKEN")
    for base in bases[:1]:
        if isinstance(base, str) and _SHA.fullmatch(base):
            say(f"main: compared with {base}, the head of the newest run that published")
    # The list: "*" marks a path that is not on NO_IMAGE_WORK.
    for path in paths or []:
        mark = "  " if needs_no_image_work(path) else "* "
        try:
            say(mark + printable(path, token, PATH_BOUND, backslash="\\\\"))
        except Exception:
            say(mark + "(a path that could not be printed)")
    try:
        reason = printable(reason, token)
    except Exception:
        reason = "the reason could not be printed"
    say(f"image jobs {'run' if run else 'skipped'}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
