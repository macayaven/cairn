"""Shared test helpers extracted from test_ci_workflow_unit."""

import importlib.util
import re

import yaml

from support.paths import REPO_ROOT

REPO = REPO_ROOT

_spec = importlib.util.spec_from_file_location(
    "ci_changes_for_workflow", REPO / "scripts" / "ci_changes.py"
)

ci_changes = importlib.util.module_from_spec(_spec)

_spec.loader.exec_module(ci_changes)

_spec = importlib.util.spec_from_file_location(
    "release_check_for_workflow", REPO / "scripts" / "release_check.py"
)

release_check = importlib.util.module_from_spec(_spec)

_spec.loader.exec_module(release_check)

WORKFLOW = yaml.safe_load((REPO / ".github" / "workflows" / "ci-cd.yml").read_text())

JOBS = WORKFLOW["jobs"]

NIGHTLY_PATH = REPO / ".github" / "workflows" / "nightly.yml"

NIGHTLY_RAW = NIGHTLY_PATH.read_text(encoding="utf-8")

NIGHTLY_JOBS = yaml.safe_load(NIGHTLY_RAW)["jobs"]

ACTION_FILES = sorted((REPO / ".github" / "actions").glob("*/action.yml"))

ALWAYS_ON = (
    "lint",
    "dependencies",
    "static_analysis",
    "secrets",
    "quick",
    "browser",
)


def needs(job: dict) -> set[str]:
    value = job.get("needs", [])
    return {value} if isinstance(value, str) else set(value)


def ancestors(name: str, jobs: dict = JOBS) -> set[str]:
    """Every job `name` waits for, directly or through the jobs it needs."""
    direct = needs(jobs[name])
    return direct.union(*(ancestors(job, jobs) for job in direct))


def evaluate(expression: str, context: dict, functions: dict | None = None) -> bool:
    text = " ".join(expression.split())
    tokens = re.findall(r"'[^']*'|==|!=|&&|\|\||[!()]|\w+\(\)|[\w.]+", text)
    assert "".join(tokens) == text.replace(" ", ""), expression  # nothing was skipped
    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else None

    def take():
        nonlocal position
        position += 1
        return tokens[position - 1]

    def truthy(value) -> bool:
        return value not in (None, False, 0, "")

    def operand():
        token = take()
        if token == "(":
            value = either()
            assert take() == ")"
            return value
        if token == "!":
            return not truthy(operand())
        if token.startswith("'"):
            return token[1:-1]
        if token.endswith("()"):
            return (functions or {})[token]
        value = context
        for part in token.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value

    def comparison():
        value = operand()
        while peek() in ("==", "!="):
            same = take() == "=="
            # GitHub compares null as if it were an empty string.
            value = ((value or "") == (operand() or "")) == same
        return value

    def both():
        value = comparison()
        while peek() == "&&":
            take()
            right = comparison()
            value = right if truthy(value) else value
        return value

    def either():
        value = both()
        while peek() == "||":
            take()
            right = both()
            value = value if truthy(value) else right
        return value

    result = either()
    assert position == len(tokens), (expression, tokens[position:])
    return truthy(result)


STATUS_FUNCTIONS = ("success()", "always()", "failure()", "cancelled()")


def jobs_that_run(
    context: dict, outputs: dict | None = None, jobs: dict = JOBS, failed: frozenset = frozenset()
) -> set[str]:
    """Which jobs run, as GitHub documents `needs` and `if` (workflow syntax, jobs.<id>.needs;
    expressions, status check functions): a job's `if` without a status function means
    `success() && (if)`, and "a failure or skip applies to all jobs in the dependency chain
    from the point of failure or skip onwards", so success() is false when any job before it
    in its chain was skipped, unless that job's own condition says otherwise (always()). The
    `needs` context holds the jobs a job lists, with their result and, when they ran, their
    OUTPUTS. Nothing is cancelled here; a job that runs succeeds unless it is in FAILED, and
    a failed job is not in the set returned."""
    results: dict[str, str] = {}
    chain: dict[str, set[str]] = {}  # every job before this one, through needs
    while len(results) < len(jobs):
        ready = [n for n, job in jobs.items() if n not in results and needs(job) <= set(results)]
        assert ready, f"a cycle in needs: {set(jobs) - set(results)}"
        for name in ready:
            job = jobs[name]
            chain[name] = set().union(*({n} | chain[n] for n in needs(job)))
            seen = {
                n: {
                    "result": results[n],
                    "outputs": (outputs or {}).get(n, {}) if results[n] == "success" else {},
                }
                for n in needs(job)
            }
            before = [results[n] for n in chain[name]]
            status = {
                "success()": all(result == "success" for result in before),
                "always()": True,
                "failure()": any(result == "failure" for result in before),
                "cancelled()": False,
            }
            condition = job.get("if", "success()")
            if not any(function in condition for function in STATUS_FUNCTIONS):
                condition = f"success() && ({condition})"
            runs = evaluate(condition, {**context, "needs": seen}, status)
            results[name] = ("failure" if name in failed else "success") if runs else "skipped"
    return {name for name, result in results.items() if result == "success"}


REPOSITORY_VARIABLES = {"GCP_INSTANCE": "vm", "GCP_ZONE": "zone"}

SHA, BASE, HEAD, BEFORE = "c" * 40, "b" * 40, "d" * 40, "a" * 40


def pull_request(draft: bool) -> dict:
    return {
        "github": {
            "event_name": "pull_request",
            "ref": "refs/pull/7/merge",
            "ref_type": "branch",
            "sha": SHA,
            "event": {"pull_request": {"draft": draft, "head": {"sha": HEAD}}},
        },
        "vars": REPOSITORY_VARIABLES,
    }


def not_a_pull_request(event_name: str, ref: str) -> dict:
    event = {}
    if event_name == "push":
        event = {"before": BEFORE, "after": SHA, "created": False, "forced": False}
    return {
        "github": {
            "event_name": event_name,
            "ref": ref,
            "ref_type": "tag" if ref.startswith("refs/tags/") else "branch",
            "ref_name": ref.split("/", 2)[2],
            "sha": SHA,
            "event": event,
        },
        "vars": REPOSITORY_VARIABLES,
    }


CHEAP = {"test", "infra", "changes", "package_check", *ALWAYS_ON}

DRAFT_DEFAULT = CHEAP | {"image_build", "image_scan", "browser"}

THE_GATE = DRAFT_DEFAULT | {"image", "stack", "host"}

TAG_GATE = (THE_GATE - {"image", "stack", "host"}) | {"test_py312"}

EVENTS = {
    "a push to a draft pull request": pull_request(draft=True),
    "a pull request marked ready for review": pull_request(draft=False),
    "a push to a pull request that is ready": pull_request(draft=False),
    "a push to main": not_a_pull_request("push", "refs/heads/main"),
    "a manual run": not_a_pull_request("workflow_dispatch", "refs/heads/work"),
    "a v* tag": not_a_pull_request("push", "refs/tags/v1.0.0"),
}

PUBLISHED = "e" * 40  # the head of the newest run on main that published an image


def fake_git(paths: list[str]):
    """git as job `changes` sees it: the merge commit's parents, an ancestor check that
    passes, and the changed paths."""

    def git(*args: str) -> str:
        if args[0] == "rev-list":
            return f"{SHA} {BASE} {HEAD}\n"
        if args[0] == "merge-base":
            return ""
        assert args[0] == "diff", args
        return "".join(path + "\0" for path in paths)

    return git


def jobs_for(event: str, paths: list[str], base: str | None = PUBLISHED) -> set[str]:
    """Job `changes` decides with scripts/ci_changes.py; the other jobs follow its outputs.
    On main the paths are those between BASE, the head of the newest run that published an
    image (None: no such run found), and the commit pushed."""
    github = EVENTS[event]["github"]
    run, _ = ci_changes.decide(
        github["event_name"],
        github["ref"],
        github["sha"],
        github["event"],
        fake_git(paths),
        lambda: base,
    )
    known = None if event in ("a manual run", "a v* tag") else paths
    return jobs_that_run(
        EVENTS[event],
        {
            "changes": {
                "image": "true" if run else "false",
                "quick": "true" if ci_changes.quick_verdict(known) else "false",
                "web": "true" if ci_changes.web_verdict(known) else "false",
                "fuzz": "full" if ci_changes.fuzz_verdict(known) else "pr",
            }
        },
    )


RAW_WORKFLOW = (REPO / ".github" / "workflows" / "ci-cd.yml").read_text(encoding="utf-8")

TAG = not_a_pull_request("push", "refs/tags/v0.1.0")

MAIN_PUSH = not_a_pull_request("push", "refs/heads/main")


def expression(value: str) -> str:
    """The expression inside `${{ ... }}`."""
    found = re.fullmatch(r"\$\{\{ (.+) \}\}", value.strip())
    assert found, value
    return found[1]


def image_tags(context: dict) -> set[str]:
    """The tags docker/metadata-action makes from the publish job's `tags` and `flavor`, as its
    README documents them: `type=semver` works on a push of a semver tag only ({{version}}
    is MAJOR.MINOR.PATCH, {{major}}.{{minor}} the first two, and on a pre-release such as
    v1.2.3-rc.1 both give the whole version 1.2.3-rc.1); `type=ref,event=branch` gives
    the branch's name on a branch push; `type=sha` gives `sha-` and the short commit;
    `type=raw` gives its value; `enable` turns an entry off. `flavor: latest=false` adds no
    `latest` of its own. Only the entry types this workflow uses are modelled."""
    step = next(s for s in JOBS["publish"]["steps"] if s.get("id") == "meta")
    assert step["with"]["flavor"].strip() == "latest=false"
    github = context["github"]
    version = None
    if github["ref_type"] == "tag":
        found = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)(-rc\.\d+)?", github["ref_name"])
        version = found.groups() if found else None
    patterns = {"{{version}}": 3}
    made = set()
    for line in step["with"]["tags"].strip().splitlines():
        kind, *rest = line.split(",")
        attributes = dict(part.split("=", 1) for part in rest)
        enable = attributes.pop("enable", "true")
        if enable != "true" and not evaluate(expression(enable), context):
            continue
        if kind == "type=semver":
            parts = patterns[attributes.pop("pattern")]
            if version and version[3]:
                # A pre-release: every pattern gives the whole version (the action's README).
                made.add(github["ref_name"].removeprefix("v"))
            elif version:
                made.add(".".join(version[:parts]))
        elif kind == "type=raw":
            made.add(attributes.pop("value"))
        elif kind == "type=ref":
            assert attributes.pop("event") == "branch"
            if github["ref_type"] == "branch":
                made.add(github["ref_name"])
        elif kind == "type=sha":
            made.add(f"sha-{github['sha'][:7]}")
        else:
            raise AssertionError(line)
        assert attributes == {}, line
    return made


def release_tags(tag: str, tagged_before: list[str]) -> set[str]:
    """The image tags `publish` pushes for TAG when TAGGED_BEFORE are the repository's tags:
    job `release_check` refuses a tag lower than another of its line, with the script's own
    function; `publish` pushes the full version only (`aliases` moves X.Y and latest)."""
    release_check.is_highest(tag, [*tagged_before, tag])
    context = not_a_pull_request("push", f"refs/tags/{tag}")
    return image_tags({**context, "needs": {"release_check": {"outputs": {}}}})


def job_comment(name: str) -> str:
    """The comment lines just above job NAME, flattened."""
    lines = RAW_WORKFLOW[: RAW_WORKFLOW.index(f"\n  {name}:\n")].splitlines()
    comment = []
    for line in reversed(lines):
        if not line.startswith("  #"):
            break
        comment.insert(0, line.strip().removeprefix("#").strip())
    return " ".join(" ".join(comment).split())


TOKEN_DEFAULTS = {
    "actions/checkout": "token",
    "astral-sh/setup-uv": "github-token",
    "actions/upload-artifact": None,
    "actions/download-artifact": None,
    "actions/attest": "github-token",
    "docker/build-push-action": "github-token",
    "docker/metadata-action": "github-token",
    "docker/setup-qemu-action": None,
    "docker/setup-buildx-action": None,
    "docker/login-action": None,
    "./.github/actions/install-pinned-binary": None,
    "./.github/actions/stage-oci-image": None,
}


def reaches_the_token(step: dict) -> bool:
    """Whether STEP is handed the job's token: written in one of its values, or left to an
    input of its action whose default is the token."""
    explicit = yaml.safe_dump({k: step.get(k) for k in ("with", "env", "run")})
    if "github.token" in explicit or "secrets." in explicit:
        return True
    if "uses" not in step:
        return False
    action = step["uses"].split("@")[0]
    assert action in TOKEN_DEFAULTS, f"read {action}'s action.yml for inputs that default to it"
    if TOKEN_DEFAULTS[action] is None:
        return False
    return (step.get("with") or {}).get(TOKEN_DEFAULTS[action]) != ""


def rerun(names: set[str], jobs: dict = JOBS) -> set[str]:
    """The jobs GitHub runs again when NAMES are re-run: "When you re-run failed jobs or a
    specific job, GitHub also re-runs the jobs that depend on them. Jobs that aren't included
    keep their outputs from the previous attempt" (Re-running workflows and jobs)."""
    return set(names) | {n for n in jobs if ancestors(n, jobs) & set(names)}


EXPANSION = re.compile(
    r"\$\{[^}]*\}|\$\((?:[^()]|\([^)]*\))*\)|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9]|\{[a-z_]+\}"
)

ERROR = re.compile(r"::error::(.*?)(?:\"\s*(?:;|$|\n)|\"\)|'\))", re.M)


def stops() -> set[str]:
    """Each `::error::` line a step of the workflow or local composite actions can print, as
    the README's table names it: expansions as `…`, cut before its first `; `, where its next
    step starts."""
    action_steps = [
        step
        for p in ACTION_FILES
        for step in yaml.safe_load(p.read_text(encoding="utf-8"))["runs"]["steps"]
    ]
    all_steps = [
        *(step for job in JOBS.values() for step in job.get("steps", [])),
        *(step for job in NIGHTLY_JOBS.values() for step in job.get("steps", [])),
        *action_steps,
    ]
    return {
        EXPANSION.sub("…", found.group(1)).split("; ")[0]
        for step in all_steps
        for found in ERROR.finditer(step.get("run", "") + "\n")
    }
