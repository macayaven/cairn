"""scripts/release_check.py: a tag is released only when it is `v` + pyproject.toml's version,
that version is a plain MAJOR.MINOR.PATCH, and CHANGELOG.md has a dated section for it with
at least one entry. The section is the release's text."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("release_check", ROOT / "scripts/release_check.py")
release_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_check)

PYPROJECT = '[project]\nname = "x"\nversion = "1.2.3"\n'
CHANGELOG = """# Changelog

Words before the sections.

## Unreleased

## 1.2.3 - 2026-01-31

The text of the release.

- **One.** The first entry.
- Two.
<!-- 1.2.3: a later entry, when it lands -->

## 1.2.2 - 2025-12-01

- Older.
"""


def refused(tag: str, pyproject: str = PYPROJECT, changelog: str = CHANGELOG) -> str:
    with pytest.raises(release_check.Refused) as caught:
        release_check.check(tag, pyproject, changelog)
    line = str(caught.value)
    assert "\n" not in line
    return line


def test_a_tag_that_is_v_and_the_version_with_a_dated_section_passes():
    assert release_check.check("v1.2.3", PYPROJECT, CHANGELOG) == "1.2.3"


def test_a_tag_that_is_not_v_and_the_version_is_refused():
    for tag in ("v1.2.4", "1.2.3", "v1.2.3.0", "v1.2", "V1.2.3", "v1.2.3 ", ""):
        assert refused(tag) == f"the tag {tag!r} is not v1.2.3, the version in pyproject.toml"


def test_a_version_that_is_not_plain_major_minor_patch_is_refused():
    for version in ("1.2", "1.2.3rc1", "1.2.3-beta.1", "01.2.3", "1.2.3.4", "1.2.3+local", " "):
        pyproject = f'[project]\nversion = "{version}"\n'
        assert refused(f"v{version}", pyproject) == (
            f"the version in pyproject.toml, {version!r}, is not a plain MAJOR.MINOR.PATCH"
        )


def test_a_pyproject_without_a_version_or_not_toml_is_refused():
    assert refused("v1.2.3", '[project]\nname = "x"\n') == "pyproject.toml has no project version"
    assert refused("v1.2.3", "[project\n") == "pyproject.toml is not TOML"


def test_a_changelog_without_a_dated_section_for_the_version_is_refused():
    missing = "CHANGELOG.md has no section '## 1.2.3 - YYYY-MM-DD'"
    for changelog in (
        CHANGELOG.replace("## 1.2.3 - 2026-01-31", "## 1.2.4 - 2026-01-31"),
        CHANGELOG.replace("## 1.2.3 - 2026-01-31", "## 1.2.3"),
        CHANGELOG.replace("## 1.2.3 - 2026-01-31", "### 1.2.3 - 2026-01-31"),
        CHANGELOG.replace("## 1.2.3 - 2026-01-31", "## 1.2.3 - 31 January 2026"),
        CHANGELOG.replace("## 1.2.3 - 2026-01-31", "## 11.2.3 - 2026-01-31"),
        "",
    ):
        assert refused("v1.2.3", changelog=changelog) == missing
    bad = CHANGELOG.replace("2026-01-31", "2026-02-30")
    assert refused("v1.2.3", changelog=bad) == (
        "CHANGELOG.md's section for 1.2.3 is dated 2026-02-30, which is not a date"
    )
    twice = CHANGELOG + "\n## 1.2.3 - 2026-02-01\n\n- Again.\n"
    assert refused("v1.2.3", changelog=twice) == "CHANGELOG.md has 2 sections for 1.2.3"


def test_a_section_dated_after_today_is_refused_and_a_past_date_passes():
    """Today is the run's UTC date; a flag gives it, so that a test pins it."""
    today = date(2026, 1, 31)
    assert release_check.check("v1.2.3", PYPROJECT, CHANGELOG, today=today) == "1.2.3"
    assert release_check.check("v1.2.3", PYPROJECT, CHANGELOG, today=date(2030, 1, 1)) == "1.2.3"
    with pytest.raises(release_check.Refused) as caught:
        release_check.check("v1.2.3", PYPROJECT, CHANGELOG, today=date(2026, 1, 30))
    assert str(caught.value) == (
        "CHANGELOG.md's section for 1.2.3 is dated 2026-01-31, after today (2026-01-30, UTC)"
    )


def test_main_takes_today_from_a_flag_and_otherwise_from_the_utc_clock(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    future = CHANGELOG.replace("## 1.2.3 - 2026-01-31", "## 1.2.3 - 2999-01-01")
    (tmp_path / "CHANGELOG.md").write_text(future, encoding="utf-8")
    assert release_check.main(["v1.2.3", "--root", str(tmp_path)]) == 1
    assert capsys.readouterr().err.startswith(
        "release refused: CHANGELOG.md's section for 1.2.3 is dated 2999-01-01, after today ("
    )
    argv = ["v1.2.3", "--root", str(tmp_path), "--today", "2999-01-01"]
    assert release_check.main(argv) == 0
    assert release_check.main([*argv[:-1], "2998-12-31"]) == 1
    with pytest.raises(SystemExit) as caught:  # not a date: argparse refuses it
        release_check.main([*argv[:-1], "yesterday"])
    assert caught.value.code == 2


def test_a_section_without_an_entry_is_refused_and_a_comment_is_not_an_entry():
    empty = CHANGELOG.replace("- **One.** The first entry.\n- Two.\n", "")
    assert refused("v1.2.3", changelog=empty) == (
        "CHANGELOG.md's section for 1.2.3 has no entry (a line that starts with '- ')"
    )
    hidden = CHANGELOG.replace(
        "- **One.** The first entry.\n- Two.\n", "<!--\n- Not an entry.\n-->\n"
    )
    assert refused("v1.2.3", changelog=hidden).endswith(
        "has no entry (a line that starts with '- ')"
    )


def test_the_notes_are_the_section_without_its_heading_and_its_comments():
    assert release_check.notes("1.2.3", CHANGELOG) == (
        "The text of the release.\n\n- **One.** The first entry.\n- Two.\n"
    )


def test_main_says_one_line_and_exits_non_zero_for_a_refusal(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    assert release_check.main(["v1.2.4", "--root", str(tmp_path)]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == (
        "release refused: the tag 'v1.2.4' is not v1.2.3, the version in pyproject.toml\n"
    )
    assert release_check.main(["v1.2.4", "--notes", "--root", str(tmp_path)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("release refused: ")


def test_main_passes_and_prints_the_notes_on_request(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    assert release_check.main(["v1.2.3", "--root", str(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert out == "v1.2.3 may be released: it is the version, and CHANGELOG.md has its section\n"
    assert err == ""
    assert release_check.main(["v1.2.3", "--notes", "--root", str(tmp_path)]) == 0
    out, _ = capsys.readouterr()
    assert out == release_check.notes("1.2.3", CHANGELOG)


def test_main_refuses_a_missing_file_in_one_line(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    assert release_check.main(["v1.2.3", "--root", str(tmp_path)]) == 1
    assert capsys.readouterr().err == "release refused: there is no CHANGELOG.md\n"


def test_the_repository_itself_would_release_its_version(capsys):
    """The tag a release of this commit would carry passes against the files as they are."""
    import tomllib

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    today = ["--today", "2026-11-19"]  # the section's date: pinned, not the clock's
    assert release_check.main([f"v{version}", "--root", str(ROOT), *today]) == 0
    assert release_check.main([f"v{version}", "--notes", "--root", str(ROOT), *today]) == 0
    text = capsys.readouterr().out.split("\n", 1)[1]
    assert "<!--" not in text and "\n- " in text


def test_the_script_needs_nothing_but_the_standard_library():
    """The workflow runs it with the runner's python3, before any environment is installed."""
    source = (ROOT / "scripts" / "release_check.py").read_text(encoding="utf-8")
    imported = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    }
    allowed = {"__future__", "argparse", "datetime", "pathlib", "re", "sys", "tomllib"}
    # git and gh are what a runner has; the script runs them with the standard library.
    assert imported <= allowed | {"json", "subprocess"}


# --- the tag's place in the history: on main, and where it ranks among the versions --------
# The job checks out the whole history; these tests make a small repository instead.


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository with the two files: main has two commits, branch `work` one more of its
    own from the first. Git reads no configuration of this machine."""
    for name, value in {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
    }.items():
        monkeypatch.setenv(name, value)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(root), *args], stdout=subprocess.PIPE, text=True, check=True
        )
        return done.stdout.strip()

    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-q", "-m", "first")
    first = git("rev-parse", "HEAD")
    git("commit", "-q", "--allow-empty", "-m", "second")
    git("checkout", "-q", "-b", "work", first)
    git("commit", "-q", "--allow-empty", "-m", "never merged")
    git("checkout", "-q", "main")
    return root, git


def test_a_tag_on_main_or_an_ancestor_of_its_head_passes(repo, capsys):
    root, git = repo
    for commit in (git("rev-parse", "main"), git("rev-parse", "main~1")):
        git("tag", "-f", "v1.2.3", commit)
        argv = ["v1.2.3", "--root", str(root), "--commit", commit, "--branch", "main"]
        assert release_check.main(argv) == 0, capsys.readouterr().err


def test_a_tag_whose_commit_is_not_on_main_is_refused(repo, capsys):
    root, git = repo
    commit = git("rev-parse", "work")
    git("tag", "v1.2.3", commit)
    argv = ["v1.2.3", "--root", str(root), "--commit", commit, "--branch", "main"]
    assert release_check.main(argv) == 1
    assert capsys.readouterr().err == (
        f"release refused: the tag's commit {commit} is not on main:"
        " it is neither its head nor one of its ancestors\n"
    )


def test_a_commit_or_branch_git_cannot_find_is_refused(repo, capsys):
    root, git = repo
    head = git("rev-parse", "main")
    git("tag", "v1.2.3", head)
    for commit, branch in ((head, "origin/main"), ("f" * 40, "main")):
        argv = ["v1.2.3", "--root", str(root), "--commit", commit, "--branch", branch]
        assert release_check.main(argv) == 1
        assert capsys.readouterr().err == (
            f"release refused: git could not tell whether {commit} is on {branch}\n"
        )


def test_commit_and_branch_come_together():
    for argv in (["v1.2.3", "--commit", "c" * 40], ["v1.2.3", "--branch", "main"]):
        with pytest.raises(SystemExit) as caught:
            release_check.main(argv)
        assert caught.value.code == 2


def test_latest_is_the_highest_plain_version_compared_as_numbers():
    highest = release_check.is_highest
    # The first tag.
    assert highest("v0.1.0", ["v0.1.0"]) is True
    # A patch on an older line while a higher version exists: X.Y moves, latest does not.
    assert highest("v0.1.1", ["v0.1.0", "v0.2.0", "v0.1.1"]) is False
    # A higher version.
    assert highest("v0.2.0", ["v0.1.0", "v0.1.1", "v0.2.0"]) is True
    # Numbers, not text: 0.10.0 is above 0.9.0.
    assert highest("v0.10.0", ["v0.9.0", "v0.10.0"]) is True
    assert highest("v0.9.1", ["v0.9.0", "v0.10.0"]) is False
    # Tags that are not plain versions are ignored; the tag counts even if not listed.
    assert highest("v0.1.0", ["v9", "v9.0", "v9.0.0-rc1", "v09.0.0", "release-9", "x"]) is True


def test_a_tag_lower_than_another_of_its_line_is_refused():
    with pytest.raises(release_check.Refused) as caught:
        release_check.is_highest("v0.1.1", ["v0.1.0", "v0.1.2", "v0.2.0", "v0.1.1"])
    assert str(caught.value) == (
        "v0.1.2 is a higher version of 0.1 than v0.1.1: a release never goes backwards"
        " within its line"
    )


def test_main_reads_the_tags_and_writes_latest_for_the_job(repo, tmp_path, capsys):
    root, git = repo
    head = git("rev-parse", "main")
    outputs = tmp_path / "github_output"
    argv = ["--root", str(root), "--commit", head, "--branch", "main"]
    argv += ["--github-output", str(outputs)]
    git("tag", "v1.2.3", head)
    assert release_check.main(["v1.2.3", *argv]) == 0
    assert outputs.read_text() == "latest=true\n"
    assert capsys.readouterr().out == (
        "v1.2.3 may be released: it is the version, CHANGELOG.md has its section,"
        " its commit is on main, and it is the highest version, so it is also latest\n"
    )
    git("tag", "v1.3.0", "main~1")
    assert release_check.main(["v1.2.3", *argv]) == 0
    assert outputs.read_text() == "latest=true\nlatest=false\n"
    assert capsys.readouterr().out.endswith(
        "its commit is on main, and v1.3.0 is higher, so latest stays where it is\n"
    )
    git("tag", "v1.2.4", "main~1")
    assert release_check.main(["v1.2.3", *argv]) == 1
    assert capsys.readouterr().err.startswith("release refused: v1.2.4 is a higher version of 1.2")
    assert outputs.read_text() == "latest=true\nlatest=false\n"  # nothing for a refused tag


# --- a released version is released once ----------------------------------------------------
# The job asks gh for the repository's releases; here a function stands in for gh.


class FakeGh:
    def __init__(self, code: int = 0, out: str = "[]"):
        self.code, self.out, self.calls = code, out, []

    def __call__(self, args: list[str]) -> tuple[int, str]:
        self.calls.append(args)
        return self.code, self.out


def files(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    return ["--root", str(tmp_path)]


def test_a_tag_with_no_github_release_passes_and_gh_is_asked_for_the_list(tmp_path, capsys):
    gh = FakeGh(out='[{"tagName": "v1.2.2"}, {"tagName": "v1.2.30"}]')
    assert release_check.main(["v1.2.3", *files(tmp_path), "--not-released"], gh=gh) == 0
    assert gh.calls == [["release", "list", "--limit", "1000", "--json", "tagName"]]
    assert capsys.readouterr().out == (
        "v1.2.3 may be released: it is the version, CHANGELOG.md has its section,"
        " and it has no GitHub release yet\n"
    )


def test_a_repository_with_no_release_yet_passes_the_first_tag(tmp_path):
    """gh's manual does not say what `release list --json` prints when there is no release:
    `[]`, or nothing with exit 0. Both are a listing that succeeded and found none."""
    for out in ("[]\n", "", "\n"):
        gh = FakeGh(out=out)
        assert release_check.main(["v1.2.3", *files(tmp_path), "--not-released"], gh=gh) == 0


def test_a_tag_that_already_has_a_github_release_is_refused(tmp_path, capsys):
    gh = FakeGh(out='[{"tagName": "v1.2.4"}, {"tagName": "v1.2.3"}]')
    assert release_check.main(["v1.2.3", *files(tmp_path), "--not-released"], gh=gh) == 1
    assert capsys.readouterr().err == (
        "release refused: v1.2.3 already has a GitHub release:"
        " a released version is published once\n"
    )


def test_gh_that_fails_or_cannot_list_every_release_refuses_the_tag(tmp_path, capsys):
    full = json.dumps([{"tagName": f"v0.0.{n}"} for n in range(1000)])
    for gh, said in (
        (FakeGh(code=1, out=""), "gh could not list the repository's releases"),
        (FakeGh(out="not json"), "gh could not list the repository's releases"),
        (FakeGh(out='{"tagName": "v1.2.3"}'), "gh could not list the repository's releases"),
        (FakeGh(out=full), "gh listed 1000 releases, the most it asks for, so v1.2.3 may have one"),
    ):
        assert release_check.main(["v1.2.3", *files(tmp_path), "--not-released"], gh=gh) == 1
        assert capsys.readouterr().err == f"release refused: {said}\n"


def test_gh_is_not_asked_without_the_flag_or_for_a_tag_already_refused(tmp_path):
    gh = FakeGh(out='[{"tagName": "v1.2.3"}]')
    assert release_check.main(["v1.2.3", *files(tmp_path)], gh=gh) == 0
    assert release_check.main(["v1.2.4", *files(tmp_path), "--not-released"], gh=gh) == 1
    assert gh.calls == []


# --- a release is made only where the public reads it ---------------------------------------


def test_a_private_repository_or_one_whose_visibility_is_not_known_is_refused_first(
    tmp_path, capsys
):
    gh = FakeGh()
    for value, said in (
        ("true", "the repository is private: a release is made only where the public reads it"),
        (
            "",
            "the repository's visibility is not known (''): a release is made only where"
            " the public reads it",
        ),
        (
            "False",
            "the repository's visibility is not known ('False'): a release is made only"
            " where the public reads it",
        ),
    ):
        # Refused before anything else is looked at, even a tag that is not the version.
        for tag in ("v1.2.3", "v9.9.9"):
            argv = [tag, *files(tmp_path), "--repository-private", value, "--not-released"]
            assert release_check.main(argv, gh=gh) == 1
            assert capsys.readouterr().err == f"release refused: {said}\n"
    assert gh.calls == []
    argv = ["v1.2.3", *files(tmp_path), "--repository-private", "false"]
    assert release_check.main(argv) == 0


def test_the_docstring_names_the_job_that_writes_the_release_text():
    doc = " ".join(release_check.__doc__.split())
    assert "job `package` runs it with --notes for the text of the GitHub release." in doc
    assert "job `release` runs it" not in doc


# --- a release candidate: vX.Y.Z-rc.N --------------------------------------------------------
# A candidate runs the whole tag path, attestation included, before the release exists. Its
# files carry the version X.Y.Z; pyproject.toml and the changelog are those of X.Y.Z.


def test_a_candidate_of_the_version_passes_and_its_version_is_the_plain_one():
    for tag in ("v1.2.3-rc.1", "v1.2.3-rc.2", "v1.2.3-rc.10", "v1.2.3-rc.123"):
        assert release_check.check(tag, PYPROJECT, CHANGELOG) == "1.2.3", tag
        assert release_check.candidate(tag) is True
    assert release_check.candidate("v1.2.3") is False


def test_a_candidate_of_another_shape_is_refused():
    for tag in (
        "v1.2.3-rc.0",
        "v1.2.3-rc.01",
        "v1.2.3-rc1",
        "v1.2.3-rc.",
        "v1.2.3-RC.1",
        "v1.2.3-rc.1.1",
        "v1.2.3-rc.-1",
        "v1.2.3-beta.1",
    ):
        assert refused(tag) == (
            f"the tag {tag!r} is not v1.2.3, the version in pyproject.toml,"
            " nor a candidate of it (v1.2.3-rc.N, N from 1 with no leading zero)"
        ), tag
        assert release_check.candidate(tag) is False
    # A candidate of another version, or one without its hyphen, is refused as before.
    for tag in ("v1.2.4-rc.1", "v1.2.3rc.1"):
        assert refused(tag) == f"the tag {tag!r} is not v1.2.3, the version in pyproject.toml"


def test_a_candidate_needs_the_version_its_section_its_entry_and_a_date_not_in_the_future():
    assert refused("v1.2.3-rc.1", changelog=CHANGELOG.replace("## 1.2.3 - ", "## 1.2.4 - ")) == (
        "CHANGELOG.md has no section '## 1.2.3 - YYYY-MM-DD'"
    )
    empty = CHANGELOG.replace("- **One.** The first entry.\n- Two.\n", "")
    assert refused("v1.2.3-rc.1", changelog=empty).endswith(
        "has no entry (a line that starts with '- ')"
    )
    with pytest.raises(release_check.Refused) as caught:
        release_check.check("v1.2.3-rc.1", PYPROJECT, CHANGELOG, today=date(2026, 1, 30))
    assert "after today (2026-01-30, UTC)" in str(caught.value)
    pyproject = '[project]\nversion = "1.2.3rc1"\n'
    assert refused("v1.2.3-rc.1", pyproject).endswith("is not a plain MAJOR.MINOR.PATCH")


def test_the_notes_of_a_candidate_begin_with_the_sentence_that_says_what_it_is(tmp_path, capsys):
    said = (
        "This is release candidate v1.2.3-rc.2: its files carry the version 1.2.3 and are"
        " replaced by the release v1.2.3.\n\n"
    )
    assert release_check.notes("1.2.3", CHANGELOG, "v1.2.3-rc.2") == said + (
        release_check.notes("1.2.3", CHANGELOG)
    )
    assert release_check.main(["v1.2.3-rc.2", "--notes", *files(tmp_path)]) == 0
    assert capsys.readouterr().out == release_check.notes("1.2.3", CHANGELOG, "v1.2.3-rc.2")
    assert release_check.main(["v1.2.3-rc.2", *files(tmp_path)]) == 0
    assert capsys.readouterr().out == (
        "v1.2.3-rc.2 may be released: it is a candidate of the version, and CHANGELOG.md has"
        " its section\n"
    )


def test_a_candidate_is_never_latest_and_says_so_to_the_job(repo, tmp_path, capsys):
    root, git = repo
    head = git("rev-parse", "main")
    outputs = tmp_path / "github_output"
    argv = ["--root", str(root), "--commit", head, "--branch", "main"]
    argv += ["--github-output", str(outputs)]
    git("tag", "v1.2.3-rc.1", head)
    assert release_check.main(["v1.2.3-rc.1", *argv]) == 0
    assert outputs.read_text() == "latest=false\ncandidate=true\n"
    assert capsys.readouterr().out == (
        "v1.2.3-rc.1 may be released: it is a candidate of the version, CHANGELOG.md has its"
        " section, its commit is on main, and it is a candidate, so latest stays where it is\n"
    )


def test_a_candidate_comes_before_its_release_and_never_after_it(repo, tmp_path, capsys):
    root, git = repo
    head = git("rev-parse", "main")
    argv = ["--root", str(root), "--commit", head, "--branch", "main"]
    git("tag", "v1.2.2", "main~1")
    git("tag", "v1.2.3-rc.1", head)
    assert release_check.main(["v1.2.3-rc.1", *argv]) == 0
    capsys.readouterr()
    git("tag", "v1.2.3", head)
    git("tag", "v1.2.3-rc.2", head)
    assert release_check.main(["v1.2.3-rc.2", *argv]) == 1
    assert capsys.readouterr().err == (
        "release refused: v1.2.3 is tagged already: a candidate comes before its release,"
        " never after it\n"
    )
    # A higher version of its line: the release it stands for could never be made.
    git("tag", "-d", "v1.2.3")
    git("tag", "v1.2.4", head)
    assert release_check.main(["v1.2.3-rc.2", *argv]) == 1
    assert capsys.readouterr().err.startswith("release refused: v1.2.4 is a higher version of 1.2")


def test_a_candidate_is_released_once_and_never_after_its_release(tmp_path, capsys):
    argv = ["v1.2.3-rc.1", *files(tmp_path), "--not-released"]
    assert release_check.main(argv, gh=FakeGh(out='[{"tagName": "v1.2.3-rc.2"}]')) == 0
    capsys.readouterr()
    assert release_check.main(argv, gh=FakeGh(out='[{"tagName": "v1.2.3-rc.1"}]')) == 1
    assert capsys.readouterr().err == (
        "release refused: v1.2.3-rc.1 already has a GitHub release:"
        " a released version is published once\n"
    )
    assert release_check.main(argv, gh=FakeGh(out='[{"tagName": "v1.2.3"}]')) == 1
    assert capsys.readouterr().err == (
        "release refused: v1.2.3 already has a GitHub release: a candidate comes before its"
        " release, never after it\n"
    )


def test_a_release_after_its_candidates_is_checked_as_before(repo, tmp_path, capsys):
    """Candidates are not plain versions: the release that follows them ranks, writes its
    output and says the same as it would without them."""
    root, git = repo
    head = git("rev-parse", "main")
    outputs = tmp_path / "github_output"
    argv = ["--root", str(root), "--commit", head, "--branch", "main"]
    argv += ["--github-output", str(outputs), "--not-released"]
    for tag in ("v1.2.3-rc.1", "v1.2.3-rc.2", "v1.3.0-rc.1"):
        git("tag", tag, head)
    git("tag", "v1.2.3", head)
    gh = FakeGh(out='[{"tagName": "v1.2.3-rc.1"}, {"tagName": "v1.2.3-rc.2"}]')
    assert release_check.main(["v1.2.3", *argv], gh=gh) == 0
    assert outputs.read_text() == "latest=true\n"
    assert capsys.readouterr().out == (
        "v1.2.3 may be released: it is the version, CHANGELOG.md has its section,"
        " its commit is on main, it has no GitHub release yet, and it is the highest version,"
        " so it is also latest\n"
    )
    assert release_check.is_highest("v1.2.3", ["v1.2.3-rc.1", "v1.3.0-rc.1", "v1.2.3"]) is True
    assert release_check.notes("1.2.3", CHANGELOG).startswith("The text of the release.")
