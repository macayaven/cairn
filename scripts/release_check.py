"""Says whether a tag may be released, and prints the release's text.

    python3 scripts/release_check.py v0.1.0            # passes, or one line on stderr, exit 1
    python3 scripts/release_check.py v0.1.0 --notes    # the same check, then the section

A tag passes only when, in this order:
1. pyproject.toml's `[project] version` is a plain MAJOR.MINOR.PATCH (no pre-release, no
   local part, no leading zero);
2. the tag is `v` followed by that version, exactly;
3. CHANGELOG.md has exactly one section headed `## <version> - <YYYY-MM-DD>`, the date a
   real date, and that section has at least one entry: a line that starts with `- `.
   HTML comments (`<!-- ... -->`) are not part of the section: what they hide is no entry,
   and they are left out of the notes.
Otherwise it prints `release refused: ` and the first rule that failed, on one line, and exits
1. Job `release_check` of .github/workflows/ci-cd.yml runs it on a tag before anything is
published; job `release` runs it with --notes for the text of the GitHub release.

It reads only the two files and needs only the standard library, so the runner's python3
runs it before any environment is installed.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from datetime import date
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


def _section(version: str, changelog: str) -> str:
    """The text of VERSION's section, its heading and its comments left out."""
    headings = [m for m in HEADING.finditer(changelog) if m["version"] == version]
    if not headings:
        raise Refused(f"CHANGELOG.md has no section '## {version} - YYYY-MM-DD'")
    if len(headings) > 1:
        raise Refused(f"CHANGELOG.md has {len(headings)} sections for {version}")
    heading = headings[0]
    try:
        date.fromisoformat(heading["date"])
    except ValueError:
        raise Refused(
            f"CHANGELOG.md's section for {version} is dated {heading['date']}, which is not a date"
        ) from None
    rest = changelog[heading.end() :]
    end = NEXT_SECTION.search(rest)
    body = rest[: end.start()] if end else rest
    return COMMENT.sub("", body).strip("\n")


def check(tag: str, pyproject: str, changelog: str) -> str:
    """The version TAG releases; raises Refused with the first rule that fails."""
    version = version_of(pyproject)
    if not PLAIN.fullmatch(version):
        raise Refused(
            f"the version in pyproject.toml, {version!r}, is not a plain MAJOR.MINOR.PATCH"
        )
    if tag != f"v{version}":
        raise Refused(f"the tag {tag!r} is not v{version}, the version in pyproject.toml")
    if not ENTRY.search(_section(version, changelog)):
        raise Refused(
            f"CHANGELOG.md's section for {version} has no entry (a line that starts with '- ')"
        )
    return version


def notes(version: str, changelog: str) -> str:
    """VERSION's section of the changelog, for the release's text."""
    return _section(version, changelog) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release_check.py", description="Say whether a tag may be released."
    )
    parser.add_argument("tag", help="the tag's name, such as v0.1.0")
    parser.add_argument("--notes", action="store_true", help="print the changelog's section")
    parser.add_argument("--root", type=Path, default=Path("."), help="the repository (.)")
    args = parser.parse_args(argv)
    try:
        texts = []
        for name in ("pyproject.toml", "CHANGELOG.md"):
            path = args.root / name
            if not path.is_file():
                raise Refused(f"there is no {name}")
            texts.append(path.read_text(encoding="utf-8"))
        version = check(args.tag, *texts)
    except Refused as refusal:
        print(f"release refused: {refusal}", file=sys.stderr)
        return 1
    if args.notes:
        sys.stdout.write(notes(version, texts[1]))
    else:
        print(f"{args.tag} may be released: it is the version, and CHANGELOG.md has its section")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
