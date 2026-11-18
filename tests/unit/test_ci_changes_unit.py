"""scripts/ci_changes.py: which changes need no image work, and how CI finds the changes.

The image jobs (`image`, `stack`, `host`, and after them `publish`, `verify`, `deploy`) are
skipped only when every changed path is on a short list of paths no image job reads. Any
other path, an empty list, or a change whose paths cannot be found runs the whole gate.
"""

import ast
import functools
import importlib.util
import io
import json
import re
import subprocess
import urllib.error
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
_spec = importlib.util.spec_from_file_location("ci_changes", SCRIPTS / "ci_changes.py")
ci_changes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_changes)

WORKFLOW = yaml.safe_load((REPO / ".github" / "workflows" / "ci-cd.yml").read_text())
# The jobs that build, rehearse, publish, verify or deploy the image: all skipped together.
GATED_JOBS = ("image", "stack", "host", "publish", "verify", "deploy")


# --- the verdict -----------------------------------------------------------------------------

VERDICTS = {
    "documents only": (["docs/spec-v1.md", "docs/decisions.md"], True),
    "tests only": (["tests/unit/test_store_unit.py", "tests/conftest.py"], True),
    "one document and one source file": (["docs/spec-v1.md", "src/cairn/store.py"], False),
    "a new top-level file": (["docs/spec-v1.md", "NOTICE"], False),
    "a rename out of docs/ into src/": (["docs/notes.md", "src/cairn/notes.md"], False),
    "an empty list": ([], False),
    "a path with spaces and unicode": (["docs/über die Seite.md"], True),
    "a source path with spaces and unicode": (["src/cairn/über die Seite.md"], False),
    "the workflow file": ([".github/workflows/ci-cd.yml"], False),
    "README.md at the top, which is in the image": (["README.md"], False),
    "infra/gcp/README.md, which the host rehearsal reads": (["infra/gcp/README.md"], False),
    "deploy/README.md, which the stack rehearsal reads": (["deploy/README.md"], False),
    "evals, examples, CLAUDE.md and .gitignore": (
        ["evals/t4_compile.py", "examples/connector/connector.py", "CLAUDE.md", ".gitignore"],
        True,
    ),
    "a directory named like one on the list, lower down": (["src/docs/x.md"], False),
    "a top-level file named like a directory on the list": (["docs"], False),
    "a path that climbs out of docs/": (["docs/../src/cairn/store.py"], False),
    "a path with an empty segment": (["docs//x.md"], False),
    "an absolute path": (["/docs/x.md"], False),
    "a different case": (["Docs/x.md"], False),
    "the Dockerfile": (["Dockerfile"], False),
    "the lock file": (["uv.lock"], False),
}


@pytest.mark.parametrize("name", VERDICTS)
def test_nothing_ships_only_when_every_path_is_on_the_list(name):
    paths, expected = VERDICTS[name]
    assert ci_changes.nothing_ships(paths) is expected, name


def test_the_list_is_short_and_every_entry_exists():
    # Six kinds of path, and the six files and folders that are for contributors only.
    assert len(ci_changes.NO_IMAGE_WORK) <= 12
    for entry in ci_changes.NO_IMAGE_WORK:
        target = REPO / entry.rstrip("/")
        assert target.is_dir() if entry.endswith("/") else target.is_file(), entry


# --- the proof that no image-related job reads a path on the list ---------------------------


def test_nothing_the_image_is_built_from_is_on_the_list():
    # .dockerignore is an allow-list: `*`, then `!PATH` for each thing the build may read.
    allowed = [
        line[1:].rstrip("/")
        for line in (REPO / ".dockerignore").read_text().splitlines()
        if line.startswith("!")
    ]
    assert allowed == ["pyproject.toml", "uv.lock", "README.md", "LICENSE", "src"]
    copied = re.findall(r"^COPY (?!--from)(.+?) \S+$", (REPO / "Dockerfile").read_text(), re.M)
    sources = [source for line in copied for source in line.split()]
    assert sources == ["pyproject.toml", "uv.lock", "README.md", "LICENSE", "src"]
    for path in [*allowed, "src/cairn/__init__.py", "Dockerfile", ".dockerignore"]:
        assert not ci_changes.needs_no_image_work(path), path


def step_text(step: dict) -> str:
    parts = [step.get("run", ""), step.get("uses", "")]
    for key in ("with", "env"):
        parts += [str(value) for value in (step.get(key) or {}).values()]
    return "\n".join(parts)


def repository_paths(text: str) -> set[str]:
    """Every word in TEXT that names a file or directory of the repository."""
    found = set()
    for word in re.findall(r"[\w.][\w./-]*", text):
        word = word.rstrip("./")
        if word and (REPO / word).exists() and word not in (".", ".."):
            found.add(word)
    return found


def test_no_step_of_an_image_related_job_names_a_path_on_the_list():
    named = set()
    for name in GATED_JOBS:
        for step in WORKFLOW["jobs"][name]["steps"]:
            for path in repository_paths(step_text(step)):
                named.add(path)
                assert not ci_changes.needs_no_image_work(path), (name, step.get("name"), path)
                assert not ci_changes.needs_no_image_work(path + "/x"), (name, path)
    # The walk saw what it should: the scripts these jobs run.
    assert {
        "scripts/smoke_container.py",
        "scripts/rehearse_stack.py",
        "scripts/rehearse_host.py",
        "scripts/deploy_production.sh",
    } <= named


def strings_in(path: Path) -> list[str]:
    """The string constants of a Python file, without its docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        if id(node) not in docstrings
    ]


def test_no_script_those_jobs_run_reads_a_path_on_the_list():
    # The scripts the steps name, and the scripts those load or run, by name.
    queue = [
        SCRIPTS / path.removeprefix("scripts/")
        for name in GATED_JOBS
        for step in WORKFLOW["jobs"][name]["steps"]
        for path in re.findall(r"scripts/[\w.-]+", step_text(step))
    ]
    seen: set[Path] = set()
    tops = [entry.rstrip("/") for entry in ci_changes.NO_IMAGE_WORK]
    mention = re.compile(r"(?<![\w/.-])(" + "|".join(map(re.escape, tops)) + r")(?![\w.-])")
    while queue:
        script = queue.pop()
        if script in seen:
            continue
        seen.add(script)
        text = script.read_text(encoding="utf-8")
        values = strings_in(script) if script.suffix == ".py" else [text]
        for value in values:
            assert not mention.search(value), (script.name, value[:120])
            queue += [SCRIPTS / v for v in re.findall(r"[\w-]+\.(?:py|sh)", value)]
        queue = [path for path in queue if path.is_file()]
    assert {path.name for path in seen} >= {
        "smoke_container.py",
        "rehearse_stack.py",
        "rehearse_host.py",
        "restore_drill.py",
        "deploy_production.sh",
    }
    assert "ci_changes.py" not in {path.name for path in seen}


# --- how the changed paths are found ---------------------------------------------------------

SHA = "c" * 40
BASE = "b" * 40
HEAD = "d" * 40
BEFORE = "a" * 40
ZEROS = "0" * 40


PUBLISHED = "e" * 40  # the head of the newest run on main that published an image
MAIN = "refs/heads/main"


class FakeGit:
    """Answers `rev-list --parents`, `merge-base --is-ancestor` and `diff`, and records what
    it was asked."""

    def __init__(self, parents=(BASE, HEAD), paths=("docs/x.md",), fail=False, ancestor=True):
        self.parents, self.paths, self.fail, self.calls = list(parents), paths, fail, []
        self.ancestor = ancestor

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if self.fail:
            raise subprocess.CalledProcessError(128, ["git", *args])
        if args[0] == "merge-base":
            assert args[1] == "--is-ancestor", args
            if not self.ancestor:
                raise subprocess.CalledProcessError(1, ["git", *args])
            return ""
        if args[0] == "rev-list":
            return " ".join([args[-1], *self.parents]) + "\n"
        if args[0] == "diff":
            return "".join(path + "\0" for path in self.paths)
        raise AssertionError(args)


def pr_event(draft=False, head=HEAD):
    return {"pull_request": {"draft": draft, "head": {"sha": head}}}


def push_event(**fields):
    return {
        "before": BEFORE,
        "after": SHA,
        "created": False,
        "deleted": False,
        "forced": False,
        **fields,
    }


def test_a_pull_request_is_its_merge_commit_against_the_base_parent():
    git = FakeGit(parents=(BASE, HEAD), paths=("docs/a b.md", "src/x.py"))
    paths = ci_changes.changed_paths("pull_request", "refs/pull/7/merge", SHA, pr_event(), git)
    assert paths == ["docs/a b.md", "src/x.py"]
    diff = git.calls[-1]
    assert diff[0] == "diff" and diff[-2:] == (BASE, SHA)
    assert {"--name-only", "-z", "--no-renames"} <= set(diff)


def test_the_base_parent_is_whichever_parent_is_not_the_head():
    git = FakeGit(parents=(HEAD, BASE))
    ci_changes.changed_paths("pull_request", "refs/pull/7/merge", SHA, pr_event(), git)
    assert git.calls[-1][-2:] == (BASE, SHA)


def test_a_push_to_another_branch_is_before_against_the_commit_pushed():
    git = FakeGit(paths=("docs/x.md",))
    paths = ci_changes.changed_paths("push", "refs/heads/work", SHA, push_event(), git)
    assert paths == ["docs/x.md"]
    assert git.calls[-1][-2:] == (BEFORE, SHA)


def test_a_push_to_main_is_the_last_published_head_against_the_commit_pushed():
    git = FakeGit(paths=("docs/x.md",))
    paths = ci_changes.changed_paths("push", MAIN, SHA, push_event(), git, lambda: PUBLISHED)
    assert paths == ["docs/x.md"]
    assert ("merge-base", "--is-ancestor", PUBLISHED, SHA) in git.calls
    assert git.calls[-1][-2:] == (PUBLISHED, SHA)  # not `before`


NEITHER = "is neither a pull request nor a push to a branch"
LOOKUP_FAILED = "the lookup of the last run that published an image failed"
NOT_GREEN = "not success: the server may run an image no green run describes"
UNKNOWN = {
    # case: (event name, ref, event, the reason the log gives)
    "a manual run": (
        "workflow_dispatch",
        MAIN,
        {},
        f"a workflow_dispatch event on refs/heads/main {NEITHER}",
    ),
    "a v* tag": (
        "push",
        "refs/tags/v1.0.0",
        push_event(),
        f"a push event on refs/tags/v1.0.0 {NEITHER}",
    ),
    "a first push (the ref is created)": (
        "push",
        MAIN,
        push_event(created=True),
        "the push created branch main: there is nothing before it to compare with",
    ),
    "a force push": (
        "push",
        MAIN,
        push_event(forced=True),
        "the push to main was forced: what it replaced may be gone from the history",
    ),
    "a deleted ref": ("push", MAIN, push_event(deleted=True), "the push deleted branch main"),
    "an after that is not the commit checked": (
        "push",
        MAIN,
        push_event(after="f" * 40),
        f"the push's after commit, {'f' * 40}, is not the commit checked out, {SHA}",
    ),
    "a before of zeros, on another branch": (
        "push",
        "refs/heads/w",
        push_event(before=ZEROS),
        "the push to w gives a before commit of all zeros",
    ),
    "no before, on another branch": (
        "push",
        "refs/heads/w",
        {"after": SHA},
        "the push to w gives no before commit",
    ),
    "a before that is not a SHA": (
        "push",
        "refs/heads/w",
        push_event(before="main"),
        "the push to w gives a before that is not a full commit SHA: main",
    ),
    "a pull request without a head": (
        "pull_request",
        "refs/pull/7/merge",
        {"pull_request": {}},
        "the pull request event gives no head commit SHA",
    ),
    "an event nobody thought of": (
        "schedule",
        MAIN,
        {},
        f"a schedule event on refs/heads/main {NEITHER}",
    ),
}


def not_known(event_name, ref, event, git=None, published=None, sha=SHA):
    """The NotKnown that find_paths raises for this change; fails if it finds paths."""
    git = git if git is not None else FakeGit()
    with pytest.raises(ci_changes.NotKnown) as caught:
        ci_changes.find_paths(event_name, ref, sha, event, git, published)
    return caught.value


@pytest.mark.parametrize("name", UNKNOWN)
def test_when_the_paths_cannot_be_known_they_are_none(name):
    event_name, ref, event, reason = UNKNOWN[name]
    git = FakeGit()
    found = ci_changes.changed_paths(event_name, ref, SHA, event, git, lambda: PUBLISHED)
    assert found is None, name
    assert not any(call[0] == "diff" for call in git.calls), name
    assert not_known(event_name, ref, event, published=lambda: PUBLISHED).reason == reason


NOT_A_MERGE = "the commit checked out is not a merge of the pull request's head with one other"
PR_SHAPES = {
    # the parents of the commit checked out: the reason the log gives
    "one parent": ((BASE,), f"{NOT_A_MERGE} parent (its parents: {BASE[:12]})"),
    "three parents": (
        (BASE, HEAD, "e" * 40),
        f"{NOT_A_MERGE} parent (its parents: {BASE[:12]} {HEAD[:12]} {'e' * 12})",
    ),
    "head is not a parent": (
        (BASE, "e" * 40),
        f"{NOT_A_MERGE} parent (its parents: {BASE[:12]} {'e' * 12})",
    ),
    "no parent": ((), f"{NOT_A_MERGE} parent (its parents: none)"),
}


@pytest.mark.parametrize("name", PR_SHAPES)
def test_a_pull_request_commit_that_is_not_a_merge_of_its_head_is_unknown(name):
    parents, reason = PR_SHAPES[name]
    git = FakeGit(parents=parents)
    assert ci_changes.changed_paths("pull_request", "r", SHA, pr_event(), git) is None
    assert not_known("pull_request", "r", pr_event(), FakeGit(parents=parents)).reason == reason


def refused():
    raise OSError("refused")


def no_git(*args):
    raise FileNotFoundError("no git here")


def broken_event():
    return ["not", "an", "object"]


GIT_DIFF = "git diff --name-only -z --no-renames --no-ext-diff"
MORE_UNKNOWN = {
    # case: (event name, ref, event, git, published, the reason the log gives)
    "GITHUB_SHA is not a SHA": (
        "push",
        MAIN,
        push_event(),
        FakeGit(),
        lambda: PUBLISHED,
        "GITHUB_SHA (main) is not a full commit SHA",
    ),
    "on main, nothing to look the base up with": (
        "push",
        MAIN,
        push_event(),
        FakeGit(),
        None,
        "on main, nothing was given to look up the last run that published an image",
    ),
    "on main, the lookup gives no base": (
        "push",
        MAIN,
        push_event(),
        FakeGit(),
        lambda: None,
        "on main, the lookup gave None, which is not a full commit SHA",
    ),
    "on main, the lookup gives a base that is not a SHA": (
        "push",
        MAIN,
        push_event(),
        FakeGit(),
        lambda: "main",
        "on main, the lookup gave main, which is not a full commit SHA",
    ),
    "on main, the lookup fails": (
        "push",
        MAIN,
        push_event(),
        FakeGit(),
        refused,
        "the lookup of the last run that published an image failed (OSError: refused)",
    ),
    "on main, the base is not an ancestor": (
        "push",
        MAIN,
        push_event(),
        FakeGit(ancestor=False),
        lambda: PUBLISHED,
        f"on main, the published commit {PUBLISHED} is not an ancestor of this one,"
        " or is not in the checkout",
    ),
    "on main, git cannot answer whether the base is an ancestor": (
        "push",
        MAIN,
        push_event(),
        FakeGit(fail=True),
        lambda: PUBLISHED,
        f"on main, the published commit {PUBLISHED} is not an ancestor of this one,"
        " or is not in the checkout (exit 128)",
    ),
    "on main, git cannot be run at all": (
        "push",
        MAIN,
        push_event(),
        no_git,
        lambda: PUBLISHED,
        f"git failed: `git merge-base --is-ancestor {PUBLISHED} {SHA}`"
        " (FileNotFoundError: no git here)",
    ),
    "a pull request, git fails": (
        "pull_request",
        "refs/pull/7/merge",
        pr_event(),
        FakeGit(fail=True),
        None,
        f"git failed: `git rev-list --parents -n 1 {SHA}` (exit 128)",
    ),
    "another branch, git fails": (
        "push",
        "refs/heads/work",
        push_event(),
        FakeGit(fail=True),
        None,
        f"git failed: `{GIT_DIFF} {BEFORE} {SHA}` (exit 128)",
    ),
    "the event is not an object": (
        "push",
        MAIN,
        broken_event(),
        FakeGit(),
        lambda: PUBLISHED,
        "something unexpected failed while reading the change"
        " (AttributeError: 'list' object has no attribute 'get')",
    ),
}


@pytest.mark.parametrize("name", MORE_UNKNOWN)
def test_every_other_way_of_not_knowing_says_why(name):
    event_name, ref, event, git, published, reason = MORE_UNKNOWN[name]
    sha = "main" if name == "GITHUB_SHA is not a SHA" else SHA
    assert ci_changes.changed_paths(event_name, ref, sha, event, git, published) is None
    assert not_known(event_name, ref, event, git, published, sha).reason == reason
    run, said = ci_changes.decide(event_name, ref, sha, event, git, published)
    assert run is True and said == f"the changed paths are not known: {reason}"


def test_a_failed_lookup_is_unknown():
    for event_name, ref, event in (
        ("pull_request", "refs/pull/7/merge", pr_event()),
        ("push", "refs/heads/work", push_event()),
        ("push", MAIN, push_event()),
    ):
        git = FakeGit(fail=True)
        found = ci_changes.changed_paths(event_name, ref, SHA, event, git, lambda: PUBLISHED)
        assert found is None, ref


def test_on_main_a_base_that_is_missing_bad_or_not_an_ancestor_is_unknown():
    def found(published, **git):
        return ci_changes.changed_paths("push", MAIN, SHA, push_event(), FakeGit(**git), published)

    assert found(None) is None  # no way to look
    assert found(lambda: None) is None  # looked, found none
    assert found(lambda: "main") is None
    assert found(lambda: PUBLISHED, ancestor=False) is None

    def refused():
        raise OSError("refused")

    assert found(refused) is None


def test_the_decision_runs_the_gate_unless_the_paths_are_known_and_all_on_the_list():
    def decide(event_name, ref, event, paths=("docs/x.md",), published=None, **git):
        git = FakeGit(paths=paths, **git)
        run, reason = ci_changes.decide(event_name, ref, SHA, event, git, published)
        assert reason
        return run

    found = lambda: PUBLISHED  # noqa: E731
    assert decide("push", MAIN, push_event(), published=found) is False
    assert decide("push", MAIN, push_event(), published=found, paths=("src/x.py",)) is True
    assert decide("push", MAIN, push_event(), published=found, paths=()) is True
    assert decide("push", MAIN, push_event(forced=True), published=found) is True
    assert decide("push", MAIN, push_event(), published=found, fail=True) is True
    assert decide("push", MAIN, push_event()) is True  # no base
    assert decide("pull_request", "refs/pull/7/merge", pr_event()) is False
    assert decide("workflow_dispatch", MAIN, {}) is True
    assert decide("push", "refs/tags/v1.0.0", push_event(), published=found) is True


# --- against a real repository, made here ----------------------------------------------------


GIT_SETTINGS = (
    *("-c", "user.name=Ada", "-c", "user.email=ada@example.test"),
    *("-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"),
)


def sh(cwd: Path, *args: str) -> str:
    command = ["git", *GIT_SETTINGS, *args]
    return subprocess.run(
        command, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    sh(tmp_path, "init", "-q")
    (tmp_path / "docs").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "docs" / "notes.md").write_text("one\ntwo\nthree\nfour\n")
    (tmp_path / "src" / "app.py").write_text("print(1)\n")
    sh(tmp_path, "add", "-A")
    sh(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


def test_real_git_lists_both_sides_of_a_rename_and_odd_names_as_they_are(repo):
    before = sh(repo, "rev-parse", "HEAD")
    sh(repo, "mv", "docs/notes.md", "src/notes.md")
    (repo / "docs" / "über die Seite.md").write_text("x\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "move")
    after = sh(repo, "rev-parse", "HEAD")
    git = functools.partial(ci_changes.run_git, cwd=repo)
    event = push_event(before=before, after=after)
    paths = ci_changes.changed_paths("push", "refs/heads/work", after, event, git)
    assert sorted(paths) == ["docs/notes.md", "docs/über die Seite.md", "src/notes.md"]
    assert ci_changes.decide("push", "refs/heads/work", after, event, git)[0] is True


def test_real_git_reads_a_merge_commit_against_its_base_parent(repo):
    sh(repo, "checkout", "-q", "-b", "work")
    (repo / "docs" / "new.md").write_text("x\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "a document")
    head = sh(repo, "rev-parse", "HEAD")
    sh(repo, "checkout", "-q", "main")
    (repo / "src" / "app.py").write_text("print(2)\n")  # main moved on meanwhile
    sh(repo, "commit", "-q", "-am", "source on main")
    sh(repo, "merge", "-q", "--no-ff", "-m", "merge", "work")
    merge = sh(repo, "rev-parse", "HEAD")
    git = functools.partial(ci_changes.run_git, cwd=repo)
    event = pr_event(head=head)
    assert ci_changes.changed_paths("pull_request", "r", merge, event, git) == ["docs/new.md"]
    assert ci_changes.decide("pull_request", "r", merge, event, git)[0] is False


def test_real_git_with_a_before_it_does_not_have_is_unknown(repo):
    after = sh(repo, "rev-parse", "HEAD")
    git = functools.partial(ci_changes.run_git, cwd=repo)
    event = push_event(before="1" * 40, after=after)
    assert ci_changes.changed_paths("push", "refs/heads/work", after, event, git) is None
    reason = not_known("push", "refs/heads/work", event, git, sha=after).reason
    # git's own words follow the exit code; they differ between versions of git.
    assert reason.startswith(f"git failed: `{GIT_DIFF} {'1' * 40} {after}` (exit 128: "), reason
    assert "\n" not in reason


def test_real_git_on_main_diffs_from_the_published_head_only_when_it_is_an_ancestor(repo):
    published = sh(repo, "rev-parse", "HEAD")
    (repo / "src" / "app.py").write_text("print(2)\n")  # a source change whose run failed
    sh(repo, "commit", "-q", "-am", "source")
    (repo / "docs" / "notes.md").write_text("five\n")  # then a document
    sh(repo, "commit", "-q", "-am", "document")
    head = sh(repo, "rev-parse", "HEAD")
    git = functools.partial(ci_changes.run_git, cwd=repo)
    event = push_event(before=sh(repo, "rev-parse", "HEAD~1"), after=head)
    paths = ci_changes.changed_paths("push", MAIN, head, event, git, lambda: published)
    assert paths == ["docs/notes.md", "src/app.py"]  # not only the document
    assert ci_changes.decide("push", MAIN, head, event, git, lambda: published)[0] is True
    # A published head on a line main does not contain (main was rewritten since).
    sh(repo, "checkout", "-q", "-b", "elsewhere", published)
    (repo / "docs" / "other.md").write_text("x\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "elsewhere")
    elsewhere = sh(repo, "rev-parse", "HEAD")
    assert ci_changes.changed_paths("push", MAIN, head, event, git, lambda: elsewhere) is None
    assert not_known("push", MAIN, event, git, lambda: elsewhere, head).reason == (
        f"on main, the published commit {elsewhere} is not an ancestor of this one,"
        " or is not in the checkout"
    )
    # A published commit the checkout does not have: git says so after the exit code.
    missing = "1" * 40
    reason = not_known("push", MAIN, event, git, lambda: missing, head).reason
    assert reason.startswith(
        f"on main, the published commit {missing} is not an ancestor of this one,"
        " or is not in the checkout (exit 128: "
    ), reason


# --- the job's entry point -------------------------------------------------------------------


TOKEN = "token-for-tests-only"
THIS_RUN = 500


def environment(tmp_path, event_name, ref, sha, event) -> dict:
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event))
    output = tmp_path / "output"
    output.write_text("earlier=1\n")
    return {
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_REF": ref,
        "GITHUB_SHA": sha,
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_API_URL": "https://api.example.test",
        "GITHUB_REPOSITORY": "octo/cairn",
        "GITHUB_WORKFLOW_REF": "octo/cairn/.github/workflows/ci-cd.yml@refs/heads/main",
        "GITHUB_RUN_ID": str(THIS_RUN),
        "GITHUB_TOKEN": TOKEN,
    }


def run_main(tmp_path, event_name, ref, sha, event, git, api=None) -> tuple[int, str]:
    environ = environment(tmp_path, event_name, ref, sha, event)
    code = ci_changes.main(environ, git, api)
    return code, (tmp_path / "output").read_text()


def test_main_writes_false_only_when_nothing_ships(tmp_path, capsys):
    api = FakeApi([run(9, head=PUBLISHED)], {9: published_job("success")})
    code, out = run_main(tmp_path, "push", MAIN, SHA, push_event(), FakeGit(), api)
    assert code == 0 and out == "earlier=1\nimage=false\n"
    said = capsys.readouterr().out
    assert "docs/x.md" in said and PUBLISHED in said and TOKEN not in said
    git = FakeGit(paths=("src/x.py",))
    code, out = run_main(tmp_path, "push", MAIN, SHA, push_event(), git, api)
    assert code == 0 and out.endswith("image=true\n")


def test_main_reads_the_runs_of_its_own_workflow_and_repository(tmp_path):
    api = FakeApi([run(THIS_RUN + 1, status="in_progress", conclusion=None, run_id=THIS_RUN)])
    run_main(tmp_path, "push", MAIN, SHA, push_event(), FakeGit(), api)
    assert api.calls[0][0] == "/repos/octo/cairn/actions/workflows/ci-cd.yml/runs"


def test_main_writes_true_and_exits_zero_on_anything_unexpected(tmp_path):
    def broken(*args):
        raise RuntimeError("no git")

    api = FakeApi([run(9, head=PUBLISHED)], {9: published_job("success")})
    code, out = run_main(tmp_path, "push", MAIN, SHA, push_event(), broken, api)
    assert code == 0 and out.endswith("image=true\n")
    # No token: the lookup on main cannot be made.
    environ = environment(tmp_path, "push", MAIN, SHA, push_event())
    del environ["GITHUB_TOKEN"]
    assert ci_changes.main(environ, FakeGit()) == 0
    assert (tmp_path / "output").read_text().endswith("image=true\n")
    # No event file at all.
    output = tmp_path / "out2"
    environ = {"GITHUB_EVENT_NAME": "push", "GITHUB_OUTPUT": str(output)}
    assert ci_changes.main(environ, FakeGit()) == 0
    assert output.read_text() == "image=true\n"


def main_says(tmp_path, capsys, event_name, ref, event, git, api=None) -> str:
    """The verdict line main() prints, after checking it wrote image=true and exited 0."""
    code, out = run_main(tmp_path, event_name, ref, SHA, event, git, api)
    assert code == 0 and out.endswith("image=true\n")
    said = capsys.readouterr().out
    assert TOKEN not in said
    return said.splitlines()[-1]


IN_THE_LOG = {
    # one case of each family: (event name, ref, event, git, API, the verdict line)
    "a manual run": (
        "workflow_dispatch",
        MAIN,
        {},
        FakeGit(),
        None,
        f"a workflow_dispatch event on refs/heads/main {NEITHER}",
    ),
    "a force push": (
        "push",
        MAIN,
        push_event(forced=True),
        FakeGit(),
        None,
        "the push to main was forced: what it replaced may be gone from the history",
    ),
    "a pull request that is not a merge of its head": (
        "pull_request",
        "refs/pull/7/merge",
        pr_event(),
        FakeGit(parents=(BASE,)),
        None,
        f"{NOT_A_MERGE} parent (its parents: {BASE[:12]})",
    ),
    "on main, no run published": (
        "push",
        MAIN,
        push_event(),
        FakeGit(),
        "no run at all",
        "on main, the API lists no earlier run of this workflow for a push",
    ),
    "on main, an earlier run has not finished": (
        "push",
        MAIN,
        push_event(),
        FakeGit(),
        "an earlier run has not finished (a deploy waiting)",
        "on main, an earlier run, #9, has not finished (waiting)",
    ),
    "on main, the newest run that published did not end green": (
        "push",
        MAIN,
        push_event(),
        FakeGit(),
        "the newest run failed after it published",
        f"on main, the newest run that published an image, #9, ended failure, {NOT_GREEN}",
    ),
    "on main, the published commit is not an ancestor": (
        "push",
        MAIN,
        push_event(),
        FakeGit(ancestor=False),
        "the newest successful run published",
        f"on main, the published commit {'9'.rjust(40, '0')} is not an ancestor of this one,"
        " or is not in the checkout",
    ),
    "the lookup failed": (
        "push",
        MAIN,
        push_event(),
        FakeGit(),
        "the API refuses (403)",
        f"{LOOKUP_FAILED} (HTTPError: HTTP Error 403: refused)",
    ),
    "git failed": (
        "push",
        "refs/heads/work",
        push_event(),
        FakeGit(fail=True),
        None,
        f"git failed: `{GIT_DIFF} {BEFORE} {SHA}` (exit 128)",
    ),
    "something unexpected": (
        "push",
        MAIN,
        ["not", "an", "object"],
        FakeGit(),
        None,
        "something unexpected failed while reading the change"
        " (AttributeError: 'list' object has no attribute 'get')",
    ),
}


@pytest.mark.parametrize("name", IN_THE_LOG)
def test_main_says_why_the_paths_are_not_known_on_its_verdict_line(tmp_path, capsys, name):
    event_name, ref, event, git, api, reason = IN_THE_LOG[name]
    api = BASES[api][0] if api else FakeApi()
    line = main_says(tmp_path, capsys, event_name, ref, event, git, api)
    assert line == f"image jobs run: the changed paths are not known: {reason}"


def test_main_never_prints_the_token_whatever_an_error_carries(tmp_path, capsys):
    api = FakeApi(fail=RuntimeError(f"401 for Bearer {TOKEN} at /repos/octo/cairn"))
    line = main_says(tmp_path, capsys, "push", MAIN, push_event(), FakeGit(), api)
    assert line == (
        "image jobs run: the changed paths are not known:"
        f" {LOOKUP_FAILED} (RuntimeError: 401 for Bearer [token] at /repos/octo/cairn)"
    )
    # git's words too, and the line that says the script could not decide at all.
    git_error = subprocess.CalledProcessError(128, ["git"], stderr=f"fatal: {TOKEN}\n".encode())

    def leaky(*args):
        raise git_error

    line = main_says(tmp_path, capsys, "push", "refs/heads/work", push_event(), leaky)
    assert line.endswith("(exit 128: fatal: [token])"), line
    environ = environment(tmp_path, "push", MAIN, SHA, push_event())
    environ["GITHUB_EVENT_PATH"] = f"/nowhere/{TOKEN}"
    assert ci_changes.main(environ, FakeGit()) == 0
    said = capsys.readouterr().out
    assert TOKEN not in said and "could not decide (FileNotFoundError" in said


def test_main_escapes_line_breaks_and_control_characters_in_a_reason(tmp_path, capsys):
    line_separator = chr(0x2028)
    api = FakeApi(fail=RuntimeError(f"line one\nline two\r\x1b[31mred\x00{line_separator}end"))
    line = main_says(tmp_path, capsys, "push", MAIN, push_event(), FakeGit(), api)
    assert line == (
        "image jobs run: the changed paths are not known:"
        f" {LOOKUP_FAILED} (RuntimeError: line one\\nline two\\r\\x1b[31mred\\x00\\u2028end)"
    )


def test_main_cuts_a_long_reason_and_says_it_was_cut(tmp_path, capsys):
    api = FakeApi(fail=RuntimeError("x" * 5000))
    line = main_says(tmp_path, capsys, "push", MAIN, push_event(), FakeGit(), api)
    prefix = "image jobs run: "
    reason = line.removeprefix(prefix)
    assert len(reason) == ci_changes.REASON_BOUND == 300
    assert reason.startswith(f"the changed paths are not known: {LOOKUP_FAILED} (RuntimeError: xx")
    assert reason.endswith("x... [cut]")


def test_a_token_is_hidden_before_the_line_is_cut():
    # Were the line cut first, the start of a token at the bound would be printed.
    text = "y" * 295 + TOKEN
    shown = ci_changes.printable(text, TOKEN)
    assert shown == "y" * 291 + "... [cut]"
    assert "token-" not in shown
    assert ci_changes.printable(f"a {TOKEN} b", TOKEN) == "a [token] b"
    assert ci_changes.printable("no token here", None) == "no token here"
    assert ci_changes.printable("no token here", "") == "no token here"
    # A path's odd bytes, as git gives them, are escaped as the path list escapes them.
    assert ci_changes.printable("docs/\udcff.md", None) == "docs/\\udcff.md"


def test_the_verdict_lines_for_known_paths_are_as_they_were(tmp_path, capsys):
    api = FakeApi([run(9, head=PUBLISHED)], {9: published_job("success")})
    run_main(tmp_path, "push", MAIN, SHA, push_event(), FakeGit(), api)
    assert capsys.readouterr().out.splitlines()[-1] == (
        "image jobs skipped: all 1 changed paths are on NO_IMAGE_WORK"
    )
    run_main(tmp_path, "push", MAIN, SHA, push_event(), FakeGit(paths=("src/x.py",)), api)
    assert capsys.readouterr().out.splitlines()[-1] == (
        "image jobs run: 1 of 1 changed paths are not on NO_IMAGE_WORK"
    )


# --- every line printed is safe to print, and printing cannot fail the script ----------------

LONG = "docs/" + "x" * 5000
PATH_LINES = {
    # a changed path, as git gives it: the line the log shows for it, word for word
    "a line break": ("docs/a\nb.md", "  docs/a\\nb.md"),
    "a carriage return": ("src/a\rb.py", "* src/a\\rb.py"),
    "an escape sequence": ("src/\x1b[31mred", "* src/\\x1b[31mred"),
    "a line separator": ("docs/a" + chr(0x2028) + "b.md", "  docs/a\\u2028b.md"),
    "a lone surrogate (a byte that is not UTF-8)": ("docs/\udcff.md", "  docs/\\udcff.md"),
    "a workflow command at the start": ("::warning::x", "* ::warning::x"),
    "a workflow command after a line break": (
        "docs/x\n::error::boom",
        "  docs/x\\n::error::boom",
    ),
    "a backslash of its own": ("docs/a\\nb.md", "  docs/a\\\\nb.md"),
    "n with a tilde": ("docs/a\N{LATIN SMALL LETTER N WITH TILDE}o.md", None),
    "an emoji": ("docs/\U0001f600.md", None),
    "a right-to-left name": ("docs/שלום.md", None),
}


@pytest.mark.parametrize("name", PATH_LINES)
def test_each_path_is_printed_on_one_line_of_its_own(tmp_path, capsys, name):
    path, expected = PATH_LINES[name]
    git = FakeGit(paths=("src/x.py", path, "docs/y.md"))
    code, out = run_main(tmp_path, "pull_request", "r", SHA, pr_event(), git)
    assert code == 0 and out.endswith("image=true\n")  # src/x.py ships, as before
    lines = capsys.readouterr().out.splitlines()
    mark = "  " if ci_changes.needs_no_image_work(path) else "* "
    assert lines[:3] == [
        "* src/x.py",
        expected if expected is not None else mark + path,  # readable names stay as they are
        "  docs/y.md",
    ]
    assert len(lines) == 4 and lines[3].startswith("image jobs run: ")
    # The mark keeps every path off the start of its line: no line the runner could read
    # as a workflow command.
    assert not any(line.startswith("::") for line in lines)


def test_a_path_with_only_names_on_the_list_keeps_its_verdict_whatever_it_prints(tmp_path):
    git = FakeGit(paths=("docs/a\nb.md", "docs/\udcff.md"))
    code, out = run_main(tmp_path, "pull_request", "r", SHA, pr_event(), git)
    assert code == 0 and out == "earlier=1\nimage=false\n"


def test_a_long_path_is_cut_and_says_so(tmp_path, capsys):
    run_main(tmp_path, "pull_request", "r", SHA, pr_event(), FakeGit(paths=(LONG,)))
    line = capsys.readouterr().out.splitlines()[0]
    assert len(line) == 2 + ci_changes.PATH_BOUND
    assert ci_changes.PATH_BOUND == 1024
    assert line.startswith("  docs/xxx") and line.endswith("x... [cut]")


class AsciiOnly(io.StringIO):
    """A stdout whose encoding lacks every character beyond ASCII."""

    def write(self, text):
        text.encode("ascii")  # raises UnicodeEncodeError
        return super().write(text)


class Closed(io.StringIO):
    """A stdout that takes nothing: a closed pipe."""

    def write(self, text):
        raise BrokenPipeError(32, "Broken pipe")


def test_a_line_stdout_cannot_encode_is_written_escaped(tmp_path, monkeypatch):
    stdout = AsciiOnly()
    monkeypatch.setattr("sys.stdout", stdout)
    git = FakeGit(paths=("docs/a\N{LATIN SMALL LETTER N WITH TILDE}o.md", "docs/\U0001f600.md"))
    code, out = run_main(tmp_path, "pull_request", "r", SHA, pr_event(), git)
    assert code == 0 and out == "earlier=1\nimage=false\n"
    assert stdout.getvalue().splitlines() == [
        "  docs/a\\xf1o.md",
        "  docs/\\U0001f600.md",
        "image jobs skipped: all 2 changed paths are on NO_IMAGE_WORK",
    ]


def test_a_stdout_that_takes_nothing_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdout", Closed())
    api = FakeApi([run(9, head=PUBLISHED)], {9: published_job("success")})
    code, out = run_main(tmp_path, "push", MAIN, SHA, push_event(), FakeGit(), api)
    assert code == 0 and out == "earlier=1\nimage=false\n"
    code, out = run_main(tmp_path, "pull_request", "r", SHA, pr_event(), FakeGit(("src/x",)))
    assert code == 0 and out == "earlier=1\nimage=true\n"


class Watching(io.StringIO):
    """A stdout that notes, for each line, whether $GITHUB_OUTPUT held image= by then."""

    def __init__(self, output: Path):
        super().__init__()
        self.output, self.seen = output, []

    def write(self, text):
        written = self.output.exists() and "image=" in self.output.read_text()
        self.seen.append((text, written))
        return super().write(text)


def test_image_is_written_before_anything_is_printed(tmp_path, monkeypatch):
    stdout = Watching(tmp_path / "output")
    monkeypatch.setattr("sys.stdout", stdout)
    api = FakeApi([run(9, head=PUBLISHED)], {9: published_job("success")})
    run_main(tmp_path, "push", MAIN, SHA, push_event(), FakeGit(), api)
    said = stdout.getvalue().splitlines()
    assert said == [
        f"main: compared with {PUBLISHED}, the head of the newest run that published",
        "  docs/x.md",
        "image jobs skipped: all 1 changed paths are on NO_IMAGE_WORK",
    ]
    assert stdout.seen and all(written for _, written in stdout.seen)


def test_a_base_that_is_a_number_is_not_said_and_fails_nothing(tmp_path, capsys):
    # The run's head passes the check as str(), but is not a string: not known, as before.
    api = FakeApi([run(9, head=int("1" * 40))], {9: published_job("success")})
    code, out = run_main(tmp_path, "push", MAIN, SHA, push_event(), FakeGit(), api)
    assert code == 0 and out.endswith("image=true\n")
    said = capsys.readouterr().out
    assert "main: compared with" not in said and "not known" in said


def test_without_github_output_the_image_line_comes_first_on_stdout(tmp_path, capsys):
    environ = environment(tmp_path, "push", MAIN, SHA, push_event())
    del environ["GITHUB_OUTPUT"]
    api = FakeApi([run(9, head=PUBLISHED)], {9: published_job("success")})
    assert ci_changes.main(environ, FakeGit(), api) == 0
    said = capsys.readouterr().out.splitlines()
    assert said[0] == "image=false"
    assert said[1].startswith("main: compared with ") and said[-1].startswith("image jobs")


# --- on main: the head of the newest run that published an image ---------------------------
# Two calls of GitHub's REST API, faked here: the runs of this workflow on main for a push
# (GET /repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs) and a run's jobs
# (GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs), with the fields their pages give.

RUNS_PATH = "/repos/octo/cairn/actions/workflows/ci-cd.yml/runs"


def run(number, conclusion="success", head=None, status="completed", run_id=None, **fields):
    return {
        "id": run_id if run_id is not None else number * 10,
        "run_number": number,
        "head_sha": head or f"{number:040x}",
        "head_branch": "main",
        "event": "push",
        "status": status,
        "conclusion": conclusion,
        **fields,
    }


def published_job(conclusion, name=None):
    """The jobs of a run whose publishing job ended with CONCLUSION."""
    others = ["what the change touches", "tests and lint", "image smoke (linux/amd64)"]
    jobs = [{"name": n, "status": "completed", "conclusion": "success"} for n in others]
    publish = name or ci_changes.PUBLISH_JOB
    return [*jobs, {"name": publish, "status": "completed", "conclusion": conclusion}]


class FakeApi:
    def __init__(self, runs=(), jobs=None, fail=None, listing=None, job_listing=None):
        self.runs, self.jobs, self.fail, self.calls = list(runs), jobs or {}, fail, []
        self.listing, self.job_listing = listing, job_listing

    def __call__(self, path: str, query: dict):
        self.calls.append((path, query))
        if self.fail is not None:
            raise self.fail
        if path == RUNS_PATH:
            if self.listing is not None:
                return self.listing
            return {"total_count": len(self.runs), "workflow_runs": self.runs}
        found = re.fullmatch(r"/repos/octo/cairn/actions/runs/(\d+)/jobs", path)
        assert found, path
        if self.job_listing is not None:
            return self.job_listing
        jobs = self.jobs.get(int(found.group(1)) // 10, [])
        return {"total_count": len(jobs), "jobs": jobs}


def last_published(api):
    return ci_changes.last_published(api, "octo/cairn", "ci-cd.yml", THIS_RUN)


def http_error(code):
    return urllib.error.HTTPError("https://api.example.test/x", code, "refused", {}, None)


TWO_OLD = run(7, head="7" * 40)
BASES = {
    # case: (the fake API, the base it gives, or None: not known)
    "the newest successful run published": (
        FakeApi([run(9), run(8)], {9: published_job("success"), 8: published_job("success")}),
        run(9)["head_sha"],
    ),
    "the newest successful run skipped the image jobs, the one before published": (
        FakeApi([run(9), run(8)], {9: published_job("skipped"), 8: published_job("success")}),
        run(8)["head_sha"],
    ),
    "the newest successful run has no publishing job at all": (
        FakeApi([run(9), run(8)], {9: published_job("success")[:3], 8: published_job("success")}),
        run(8)["head_sha"],
    ),
    "the newest run failed": (
        FakeApi([run(9, "failure"), run(8)], {8: published_job("success")}),
        run(8)["head_sha"],
    ),
    "the newest run failed before it published": (
        FakeApi(
            [run(9, "failure"), run(8)],
            {9: published_job("failure"), 8: published_job("success")},
        ),
        run(8)["head_sha"],
    ),
    # Published, maybe promoted, then failed (check-url, wait, a refused deploy): the VM may
    # run that image, which no green run describes, so the base is not known. Passing over
    # it would let a revert of that change look like "nothing ships" and stay off main.
    "the newest run failed after it published": (
        FakeApi(
            [run(9, "failure"), run(8)],
            {9: published_job("success"), 8: published_job("success")},
        ),
        None,
    ),
    "a run was cancelled after it published, below a green docs-only run": (
        FakeApi(
            [run(10), run(9, "cancelled"), run(8)],
            {
                10: published_job("skipped"),
                9: published_job("success"),
                8: published_job("success"),
            },
        ),
        None,
    ),
    "the newest run was cancelled": (
        FakeApi([run(9, "cancelled"), run(8)], {8: published_job("success")}),
        run(8)["head_sha"],
    ),
    "the newest run succeeded but its publish job failed (a later job allowed it)": (
        FakeApi([run(9), run(8)], {9: published_job("failure"), 8: published_job("success")}),
        run(8)["head_sha"],
    ),
    "this run itself is listed, in progress": (
        FakeApi(
            [run(10, None, status="in_progress", run_id=THIS_RUN), run(9)],
            {9: published_job("success")},
        ),
        run(9)["head_sha"],
    ),
    "no run at all": (FakeApi([]), None),
    "no run published": (FakeApi([run(9), run(8)], {9: published_job("skipped")}), None),
    "an earlier run has not finished (a deploy waiting)": (
        FakeApi([run(9, None, status="waiting"), TWO_OLD], {7: published_job("success")}),
        None,
    ),
    "the API refuses (403)": (FakeApi(fail=http_error(403)), None),
    "the API is rate-limited (429)": (FakeApi(fail=http_error(429)), None),
    "the API times out": (FakeApi(fail=TimeoutError("timed out")), None),
    "the API cannot be reached": (FakeApi(fail=urllib.error.URLError("no route")), None),
    "the listing is not a listing": (FakeApi(listing={"workflow_runs": "none"}), None),
    "a run without a head": (
        FakeApi([{k: v for k, v in run(9).items() if k != "head_sha"}]),
        None,
    ),
    "a head that is not a SHA": (FakeApi([run(9, head="main")]), None),
    "a run for another event": (FakeApi([run(9, event="pull_request")]), None),
    "a run for another branch": (FakeApi([run(9, head_branch="work")]), None),
    "runs not newest first": (
        FakeApi([run(8), run(9)], {8: published_job("success"), 9: published_job("success")}),
        None,
    ),
    "a job listing that is not one": (
        FakeApi([run(9)], job_listing={"jobs": None, "total_count": 1}),
        None,
    ),
    "more jobs than one page holds": (
        FakeApi([run(9)], job_listing={"total_count": 101, "jobs": published_job("success")}),
        None,
    ),
    "two jobs with the publishing job's name": (
        FakeApi([run(9)], {9: published_job("success") + published_job("success")[-1:]}),
        None,
    ),
    "more runs than are looked at, none published": (
        FakeApi(
            [run(n) for n in range(100, 100 - ci_changes.RUNS_LOOKED_AT, -1)],
            {n: published_job("skipped") for n in range(100, 70, -1)},
        ),
        None,
    ),
}


@pytest.mark.parametrize("name", BASES)
def test_the_base_on_main_and_the_verdicts_it_gives(name):
    api, base = BASES[name]
    try:
        found = last_published(api)
    except Exception:
        found = None  # changed_paths turns any failure into "not known"
    assert found == base, name

    def verdict(paths):
        git = FakeGit(paths=paths)
        published = functools.partial(last_published, api)
        run_it, _ = ci_changes.decide("push", MAIN, SHA, push_event(), git, published)
        if base is not None:
            assert git.calls[-1][-2:] == (base, SHA), name
        return run_it

    # A head that changes only documents: skipped only when a base was found.
    assert verdict(("docs/spec-v1.md",)) is (base is None), name
    # A head that ships: the gate runs either way.
    assert verdict(("docs/spec-v1.md", "src/cairn/store.py")) is True, name


NOT_FOR_MAIN = "a run that is not for a push to main"
NOT_ONE_PAGE = "a job listing that is not one page of jobs"
# The reason the log gives for each history in BASES whose base is not known, word for word.
BASES_WHY = {
    "the newest run failed after it published": (
        f"on main, the newest run that published an image, #9, ended failure, {NOT_GREEN}"
    ),
    "a run was cancelled after it published, below a green docs-only run": (
        f"on main, the newest run that published an image, #9, ended cancelled, {NOT_GREEN}"
    ),
    "no run at all": "on main, the API lists no earlier run of this workflow for a push",
    "no run published": "on main, none of the 2 newest runs published an image",
    "an earlier run has not finished (a deploy waiting)": (
        "on main, an earlier run, #9, has not finished (waiting)"
    ),
    "the API refuses (403)": f"{LOOKUP_FAILED} (HTTPError: HTTP Error 403: refused)",
    "the API is rate-limited (429)": f"{LOOKUP_FAILED} (HTTPError: HTTP Error 429: refused)",
    "the API times out": f"{LOOKUP_FAILED} (TimeoutError: timed out)",
    "the API cannot be reached": f"{LOOKUP_FAILED} (URLError: <urlopen error no route>)",
    "the listing is not a listing": f"{LOOKUP_FAILED} (ValueError: a run listing without its runs)",
    "a run without a head": (
        f"{LOOKUP_FAILED} (ValueError: a run whose head commit is not a SHA: None)"
    ),
    "a head that is not a SHA": (
        f"{LOOKUP_FAILED} (ValueError: a run whose head commit is not a SHA: 'main')"
    ),
    "a run for another event": (
        f"{LOOKUP_FAILED} (ValueError: {NOT_FOR_MAIN}: event 'pull_request', branch 'main')"
    ),
    "a run for another branch": (
        f"{LOOKUP_FAILED} (ValueError: {NOT_FOR_MAIN}: event 'push', branch 'work')"
    ),
    "runs not newest first": f"{LOOKUP_FAILED} (ValueError: runs not listed newest first)",
    "a job listing that is not one": (
        f"{LOOKUP_FAILED} (ValueError: {NOT_ONE_PAGE}: total_count 1, jobs None)"
    ),
    "more jobs than one page holds": (
        f"{LOOKUP_FAILED} (ValueError: {NOT_ONE_PAGE}: total_count 101, jobs 4)"
    ),
    "two jobs with the publishing job's name": (
        f"{LOOKUP_FAILED} (ValueError: 2 jobs named 'publish to GHCR')"
    ),
    "more runs than are looked at, none published": (
        "on main, none of the 30 newest runs published an image"
    ),
}


def find_base(api):
    return ci_changes.find_base(api, "octo/cairn", "ci-cd.yml", THIS_RUN)


def test_every_history_whose_base_is_not_known_has_its_reason():
    assert set(BASES_WHY) == {name for name, (_, base) in BASES.items() if base is None}


@pytest.mark.parametrize("name", BASES_WHY)
def test_on_main_the_reason_a_base_is_not_known(name):
    api, _ = BASES[name]
    found = not_known("push", MAIN, push_event(), FakeGit(), functools.partial(find_base, api))
    assert found.reason == BASES_WHY[name]


def every_reason_in_the_tables() -> dict[str, "ci_changes.NotKnown"]:
    found = {}
    for name, (event_name, ref, event, _) in UNKNOWN.items():
        found[name] = not_known(event_name, ref, event, published=lambda: PUBLISHED)
    for name, (parents, _) in PR_SHAPES.items():
        found[name] = not_known("pull_request", "r", pr_event(), FakeGit(parents=parents))
    for name, (event_name, ref, event, git, published, _) in MORE_UNKNOWN.items():
        sha = "main" if name == "GITHUB_SHA is not a SHA" else SHA
        found[name] = not_known(event_name, ref, event, git, published, sha)
    for name in BASES_WHY:
        published = functools.partial(find_base, BASES[name][0])
        found[name] = not_known("push", MAIN, push_event(), FakeGit(), published)
    return found


def test_every_reason_is_distinct_where_the_cases_differ():
    reasons = {name: error.reason for name, error in every_reason_in_the_tables().items()}
    names = sorted(reasons)
    for i, one in enumerate(names):
        for other in names[i + 1 :]:
            assert reasons[one] != reasons[other], (one, other, reasons[one])


def test_every_reason_the_script_can_give_is_in_the_tables():
    cases = {error.case for error in every_reason_in_the_tables().values()}
    assert cases == set(ci_changes.WHY_NOT_KNOWN)


def test_a_reason_that_cannot_be_formatted_still_says_something():
    assert ci_changes.NotKnown("no such case").reason
    assert ci_changes.NotKnown("other_event").reason  # its details are missing
    assert "other_event" in ci_changes.NotKnown("other_event").reason


def test_no_way_to_not_knowing_lacks_a_reason():
    # Walks the functions that decide "not known": every such path raises NotKnown with a
    # case that is in WHY_NOT_KNOWN; none returns None or falls off its end.
    tree = ast.parse((SCRIPTS / "ci_changes.py").read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in ("_find_paths", "find_base")
    }
    assert set(functions) == {"_find_paths", "find_base"}
    for name, function in functions.items():
        assert isinstance(function.body[-1], ast.Raise), name  # does not fall off its end
        for node in ast.walk(function):
            if isinstance(node, ast.Return):
                value = node.value
                assert value is not None, (name, node.lineno)
                assert not (isinstance(value, ast.Constant) and value.value is None), name
            if isinstance(node, ast.ExceptHandler):
                assert isinstance(node.body[-1], ast.Raise), (name, node.lineno)
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "NotKnown":
            first = node.args[0]
            assert isinstance(first, ast.Constant), node.lineno  # a case, named here
            assert first.value in ci_changes.WHY_NOT_KNOWN, first.value
            used.add(first.value)
    assert used == set(ci_changes.WHY_NOT_KNOWN)  # and no reason is left unused


def test_the_lookup_asks_for_pushes_to_main_and_stops_at_the_limit():
    api = BASES["more runs than are looked at, none published"][0]
    api.calls.clear()
    assert last_published(api) is None
    path, query = api.calls[0]
    assert path == RUNS_PATH
    assert query == {
        "branch": "main",
        "event": "push",
        "per_page": ci_changes.RUNS_LOOKED_AT,
        "exclude_pull_requests": "true",
    }
    jobs = api.calls[1:]
    assert len(jobs) == ci_changes.RUNS_LOOKED_AT == 30
    assert all(q == {"filter": "latest", "per_page": 100} for _, q in jobs)


def test_every_run_down_to_the_base_has_its_jobs_read_failed_ones_too():
    api = FakeApi([run(9, "failure"), run(8)], {8: published_job("success")})
    assert last_published(api) == run(8)["head_sha"]
    assert [path for path, _ in api.calls[1:]] == [
        "/repos/octo/cairn/actions/runs/90/jobs",
        "/repos/octo/cairn/actions/runs/80/jobs",
    ]


def test_real_git_a_revert_of_an_image_that_was_published_then_failed_would_hide_it(repo):
    # Why such a run makes the base unknown: A published and ended green; D changed a
    # document (skipped); B changed the source, published, was promoted and then failed;
    # R reverts B. From A, R changes only the document, so A as the base would skip the
    # gate while the VM runs B.
    a = sh(repo, "rev-parse", "HEAD")
    (repo / "docs" / "notes.md").write_text("five\n")
    sh(repo, "commit", "-q", "-am", "D")
    (repo / "src" / "app.py").write_text("print(2)\n")
    sh(repo, "commit", "-q", "-am", "B")
    sh(repo, "revert", "--no-edit", "HEAD")
    r = sh(repo, "rev-parse", "HEAD")
    git = functools.partial(ci_changes.run_git, cwd=repo)
    event = push_event(after=r)
    assert ci_changes.changed_paths("push", MAIN, r, event, git, lambda: a) == ["docs/notes.md"]
    api = FakeApi(
        [run(10, "failure", head="9" * 40), run(9, head="8" * 40), run(8, head=a)],
        {10: published_job("success"), 9: published_job("skipped"), 8: published_job("success")},
    )
    assert ci_changes.decide("push", MAIN, r, event, git, functools.partial(last_published, api))[0]


# --- the API client: urllib, the runner's API address, the job's token ----------------------


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_client_sends_the_token_to_the_runner_api_address_and_reads_json():
    sent = []

    def urlopen(request, timeout):
        sent.append((request, timeout))
        return FakeResponse(b'{"total_count": 0, "workflow_runs": []}')

    api = ci_changes.github_api("https://api.example.test", TOKEN, urlopen)
    assert api(RUNS_PATH, {"branch": "main", "per_page": 30}) == {
        "total_count": 0,
        "workflow_runs": [],
    }
    request, timeout = sent[0]
    assert request.full_url == f"https://api.example.test{RUNS_PATH}?branch=main&per_page=30"
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert request.get_header("Accept") == "application/vnd.github+json"
    assert request.get_header("X-github-api-version") == "2022-11-28"
    assert 0 < timeout <= 30


def test_the_client_refuses_what_it_cannot_trust():
    with pytest.raises(ValueError):
        ci_changes.github_api("http://api.example.test", TOKEN)  # not https
    with pytest.raises(ValueError):
        ci_changes.github_api("https://api.example.test", "")  # no token

    def refuses(request, timeout):
        raise http_error(403)

    with pytest.raises(urllib.error.HTTPError):
        ci_changes.github_api("https://api.example.test", TOKEN, refuses)(RUNS_PATH, {})


def test_the_workflow_file_comes_from_the_workflow_ref():
    ref = "octo/cairn/.github/workflows/ci-cd.yml@refs/heads/main"
    assert ci_changes.workflow_file(ref, "octo/cairn") == "ci-cd.yml"
    for bad in ("other/repo/.github/workflows/ci-cd.yml@refs/heads/main", "octo/cairn/x.yml", ""):
        with pytest.raises(ValueError):
            ci_changes.workflow_file(bad, "octo/cairn")


def test_the_publishing_job_is_found_by_the_display_name_this_workflow_gives_it():
    # The API names a job by its display name. If someone renames `publish`, this fails here
    # instead of every run on main passing over every earlier run (which would run the gate).
    jobs = WORKFLOW["jobs"]
    assert jobs["publish"]["name"] == ci_changes.PUBLISH_JOB
    assert [n for n, job in jobs.items() if job.get("name") == ci_changes.PUBLISH_JOB] == [
        "publish"
    ]
    # And it is the job that pushes the image: the only one that may write packages.
    pushes = [
        name
        for name, job in jobs.items()
        for step in job["steps"]
        if (step.get("with") or {}).get("push") is True
    ]
    assert pushes == ["publish"]
    writers = [
        n for n, job in jobs.items() if job.get("permissions", {}).get("packages") == "write"
    ]
    assert writers == ["publish"]


def test_the_files_for_people_who_contribute_need_no_image_work():
    """They are not in the image (.dockerignore lets in only what the build reads)."""
    for path in (
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "CHANGELOG.md",
        ".github/ISSUE_TEMPLATE/bug_report.md",
        ".github/pull_request_template.md",
    ):
        assert ci_changes.needs_no_image_work(path), path
    for path in ("README.md", "LICENSE", ".github/workflows/ci-cd.yml", ".github/dependabot.yml"):
        assert not ci_changes.needs_no_image_work(path), path


def test_a_release_tag_runs_every_job_without_asking_git_or_the_api(tmp_path):
    """A push of v0.1.0 publishes a release, so its image jobs always run, whatever the commit
    changed since the last image: the tag's changed paths are never looked up."""
    api = FakeApi([run(9, head=PUBLISHED)], {9: published_job("success")})
    git = FakeGit()  # its only changed path is docs/x.md, which alone would ship nothing
    code, out = run_main(tmp_path, "push", "refs/tags/v0.1.0", SHA, push_event(), git, api)
    assert code == 0 and out == "earlier=1\nimage=true\n"
    assert git.calls == [] and api.calls == []
