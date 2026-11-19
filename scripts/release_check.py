"""Says whether a tag may be released, and prints the release's text.

    python3 scripts/release_check.py v0.1.0            # passes, or one line on stderr, exit 1
    python3 scripts/release_check.py v0.1.0 --notes    # the same check, then the section

A tag passes only when, in this order:
0. with --repository-private VALUE, VALUE is exactly `false` (the event's
   repository.private): a release is made only from a public repository;
1. pyproject.toml's `[project] version` is a plain MAJOR.MINOR.PATCH (no pre-release, no
   local part, no leading zero);
2. the tag is `v` followed by that version, exactly;
3. CHANGELOG.md has exactly one section headed `## <version> - <YYYY-MM-DD>`, the date a
   real date and not after today (UTC; --today gives another), and that section has at
   least one entry: a line that starts with `- `.
   HTML comments (`<!-- ... -->`) are not part of the section: what they hide is no entry,
   and they are left out of the notes;
4. with --commit and --branch, git says the commit is the branch's head or one of its
   ancestors, and no tag of the same MAJOR.MINOR is a higher version. Of the repository's
   tags that are plain versions (`vMAJOR.MINOR.PATCH`, compared as numbers; other tags are
   ignored), the tag is `latest` only when it is the highest: --github-output gets
   `latest=true` or `latest=false`;
5. with --not-released, gh lists the repository's releases and none is the tag's.
Otherwise it prints `release refused: ` and the first rule that failed, on one line, and exits
1. Job `release_check` of .github/workflows/ci-cd.yml runs it on a tag with every flag before
anything is published; job `release` runs it with --notes for the text of the GitHub release.

It reads the two files, and runs git and gh only for their flags. It needs only the standard
library, so the runner's python3 runs it before any environment is installed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path

PLAIN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
HEADING = re.compile(r"^## (?P<version>\S+) - (?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})[ \t]*$", re.M)
NEXT_SECTION = re.compile(r"^#{1,2} ", re.M)
COMMENT = re.compile(r"<!--.*?-->[ \t]*\n?", re.S)
ENTRY = re.compile(r"^- ", re.M)


class Refused(Exception):
    """The tag may not be released; the message is the one line that says why."""


def version_of(pyproject: str) -> str:
    try:
        project = tomllib.loads(pyproject).get("project", {})
    except tomllib.TOMLDecodeError:
        raise Refused("pyproject.toml is not TOML") from None
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str):
        raise Refused("pyproject.toml has no project version")
    return version


def _section(version: str, changelog: str, today: date | None = None) -> str:
    """The text of VERSION's section, its heading and its comments left out. With TODAY, a
    section dated after it is refused."""
    headings = [m for m in HEADING.finditer(changelog) if m["version"] == version]
    if not headings:
        raise Refused(f"CHANGELOG.md has no section '## {version} - YYYY-MM-DD'")
    if len(headings) > 1:
        raise Refused(f"CHANGELOG.md has {len(headings)} sections for {version}")
    heading = headings[0]
    try:
        dated = date.fromisoformat(heading["date"])
    except ValueError:
        raise Refused(
            f"CHANGELOG.md's section for {version} is dated {heading['date']}, which is not a date"
        ) from None
    if today is not None and dated > today:
        raise Refused(
            f"CHANGELOG.md's section for {version} is dated {dated}, after today ({today}, UTC)"
        )
    rest = changelog[heading.end() :]
    end = NEXT_SECTION.search(rest)
    body = rest[: end.start()] if end else rest
    return COMMENT.sub("", body).strip("\n")


def utc_today() -> date:
    return datetime.now(UTC).date()


def check(tag: str, pyproject: str, changelog: str, today: date | None = None) -> str:
    """The version TAG releases; raises Refused with the first rule that fails. TODAY is the
    run's UTC date unless given."""
    version = version_of(pyproject)
    if not PLAIN.fullmatch(version):
        raise Refused(
            f"the version in pyproject.toml, {version!r}, is not a plain MAJOR.MINOR.PATCH"
        )
    if tag != f"v{version}":
        raise Refused(f"the tag {tag!r} is not v{version}, the version in pyproject.toml")
    if not ENTRY.search(_section(version, changelog, today or utc_today())):
        raise Refused(
            f"CHANGELOG.md's section for {version} has no entry (a line that starts with '- ')"
        )
    return version


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,  # the refusal is one line of its own
        text=True,
        check=False,
    )


def on_branch(root: Path, commit: str, branch: str) -> None:
    """Refuses COMMIT unless it is BRANCH's head or one of its ancestors."""
    found = _git(root, "merge-base", "--is-ancestor", commit, branch).returncode
    if found == 1:
        raise Refused(
            f"the tag's commit {commit} is not on {branch}:"
            " it is neither its head nor one of its ancestors"
        )
    if found != 0:
        raise Refused(f"git could not tell whether {commit} is on {branch}")


def _number(tag: str) -> tuple[int, int, int] | None:
    found = PLAIN.fullmatch(tag[1:]) if tag.startswith("v") else None
    return tuple(int(part) for part in found.groups()) if found else None


def is_highest(tag: str, tags: list[str]) -> bool:
    """Whether TAG is the highest of TAGS that are plain versions (`vMAJOR.MINOR.PATCH`,
    compared as numbers; other tags are ignored), and so is also published as `latest`.
    A tag lower than another of its own MAJOR.MINOR is refused."""
    mine = _number(tag)
    versions = {number: name for name in tags if (number := _number(name))}
    for number, name in sorted(versions.items(), reverse=True):
        if number[:2] == mine[:2] and number > mine:
            raise Refused(
                f"{name} is a higher version of {mine[0]}.{mine[1]} than {tag}:"
                " a release never goes backwards within its line"
            )
    return all(number <= mine for number in versions)


def _highest(tags: list[str]) -> str:
    return max((name for name in tags if _number(name)), key=_number)


RELEASES_ASKED = 1000


def run_gh(args: list[str]) -> tuple[int, str]:
    """gh's exit code and standard output; the job gives it GH_TOKEN and GH_REPO."""
    try:
        done = subprocess.run(
            ["gh", *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
    except OSError:
        return 127, ""
    return done.returncode, done.stdout


def not_released(tag: str, gh) -> None:
    """Refuses TAG if the repository has a GitHub release for it, or if gh cannot say."""
    code, out = gh(["release", "list", "--limit", str(RELEASES_ASKED), "--json", "tagName"])
    try:
        if code != 0:
            names = None
        elif not out.strip():  # a listing that succeeded and found nothing
            names = []
        else:
            names = [release["tagName"] for release in json.loads(out)]
    except (ValueError, TypeError, KeyError):
        names = None
    if names is None:
        raise Refused("gh could not list the repository's releases")
    if tag in names:
        raise Refused(f"{tag} already has a GitHub release: a released version is published once")
    if len(names) >= RELEASES_ASKED:
        raise Refused(
            f"gh listed {len(names)} releases, the most it asks for, so {tag} may have one"
        )


def public(private: str) -> None:
    """Refuses unless the event says the repository is not private, in exactly those words."""
    where = "a release is made only where the public reads it"
    if private == "true":
        raise Refused(f"the repository is private: {where}")
    if private != "false":
        raise Refused(f"the repository's visibility is not known ({private!r}): {where}")


def notes(version: str, changelog: str) -> str:
    """VERSION's section of the changelog, for the release's text."""
    return _section(version, changelog) + "\n"


def main(argv: list[str] | None = None, gh=run_gh) -> int:
    parser = argparse.ArgumentParser(
        prog="release_check.py", description="Say whether a tag may be released."
    )
    parser.add_argument("tag", help="the tag's name, such as v0.1.0")
    parser.add_argument("--notes", action="store_true", help="print the changelog's section")
    parser.add_argument("--root", type=Path, default=Path("."), help="the repository (.)")
    parser.add_argument(
        "--today", type=date.fromisoformat, help="YYYY-MM-DD, in place of the UTC date"
    )
    parser.add_argument("--commit", help="the tag's commit; with --branch")
    parser.add_argument(
        "--branch",
        help="refuse the tag unless --commit is this branch's head or an ancestor of it,"
        " and say whether the tag is the highest version (latest)",
    )
    parser.add_argument(
        "--repository-private",
        help="the event's repository.private; anything but 'false' refuses the tag first",
    )
    parser.add_argument(
        "--not-released",
        action="store_true",
        help="refuse a tag that already has a GitHub release (asks gh)",
    )
    parser.add_argument(
        "--github-output", type=Path, help="append latest=true|false here (with --branch)"
    )
    args = parser.parse_args(argv)
    if (args.commit is None) != (args.branch is None):
        parser.error("--commit and --branch come together")
    try:
        if args.repository_private is not None:
            public(args.repository_private)
        texts = []
        for name in ("pyproject.toml", "CHANGELOG.md"):
            path = args.root / name
            if not path.is_file():
                raise Refused(f"there is no {name}")
            texts.append(path.read_text(encoding="utf-8"))
        version = check(args.tag, *texts, today=args.today)
        said = ["it is the version", "CHANGELOG.md has its section"]
        latest = None
        if args.branch is not None:
            on_branch(args.root, args.commit, args.branch)
            said.append(f"its commit is on {args.branch}")
            tags = _git(args.root, "tag", "--list", "v*")
            if tags.returncode != 0:
                raise Refused("git could not list the repository's tags")
            names = tags.stdout.split()
            latest = is_highest(args.tag, names)
        if args.not_released:
            not_released(args.tag, gh)
            said.append("it has no GitHub release yet")
    except Refused as refusal:
        print(f"release refused: {refusal}", file=sys.stderr)
        return 1
    if latest is not None:
        if latest:
            said.append("it is the highest version, so it is also latest")
        else:
            said.append(f"{_highest(names)} is higher, so latest stays where it is")
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8") as out:
                out.write(f"latest={'true' if latest else 'false'}\n")
    if args.notes:
        sys.stdout.write(notes(version, texts[1]))
    else:
        print(f"{args.tag} may be released: {', '.join(said[:-1])}, and {said[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
