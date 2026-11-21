"""Says whether pip-audit's findings fail the gate (job `dependencies` of ci-cd.yml).

    python3 .github/scanners/audit_policy.py AUDIT.json IGNORES.toml [--summary FILE]

AUDIT.json is pip-audit's JSON report. A vulnerability with a fixed version fails the job
unless IGNORES.toml lists its id (or one of its aliases); one with no fixed version yet is
listed as a warning and fails nothing. Every entry of IGNORES.toml has an `id`, a `reason`
and a date `until`; an entry without them, or whose day has passed (UTC; --today gives
another), fails the job, so an ignore cannot stay forever. --summary appends the findings,
as Markdown, to FILE (the job's $GITHUB_STEP_SUMMARY). Exit 0 or 1; the lines that say why
are GitHub workflow commands. Standard library only: the runner's python3 runs it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import UTC, date, datetime

KEYS = {"id", "reason", "until"}


class Refused(Exception):
    """The ignore list is not one the gate takes; the message says why."""


def load_ignores(path, today: date) -> list[dict]:
    """The entries of the ignore list at PATH; raises Refused for one that lacks its id, its
    reason or its date, or whose date is before TODAY."""
    with open(path, "rb") as handle:
        entries = tomllib.load(handle).get("ignore", [])
    if not isinstance(entries, list):
        raise Refused("`ignore` is not a list of entries")
    for entry in entries:
        ident = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(ident, str) or not ident.strip():
            raise Refused("an ignore entry has no id")
        if extra := sorted(set(entry) - KEYS):
            raise Refused(f"the ignore entry for {ident} has unknown keys: {', '.join(extra)}")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise Refused(f"the ignore entry for {ident} gives no reason")
        until = entry.get("until")
        if "until" not in entry:
            raise Refused(f"the ignore entry for {ident} has no date `until`")
        if not isinstance(until, date) or isinstance(until, datetime):
            raise Refused(f"the ignore entry for {ident} has no date: `until` is {until!r}")
        if until < today:
            raise Refused(f"the ignore entry for {ident} ended on {until}: fix it or renew it")
    return entries


def judge(found: dict, ignores: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """(failing, unfixed, ignored): one line for each vulnerability of the report."""
    by_id = {entry["id"]: entry for entry in ignores}
    failing, unfixed, ignored = [], [], []
    for dependency in found["dependencies"]:
        name, version = dependency["name"], dependency.get("version", "?")
        for vuln in dependency.get("vulns", []):
            ident, fixes = vuln["id"], vuln.get("fix_versions") or []
            names = [ident, *(vuln.get("aliases") or [])]
            entry = next((by_id[n] for n in names if n in by_id), None)
            if entry is not None:
                ignored.append(
                    f"{name} {version}: {ident} is ignored until {entry['until']}:"
                    f" {entry['reason'].strip()}"
                )
            elif fixes:
                failing.append(f"{name} {version}: {ident} is fixed in {', '.join(fixes)}")
            else:
                unfixed.append(f"{name} {version}: {ident} has no fixed version yet")
    return failing, unfixed, ignored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="audit_policy.py", description=__doc__.split("\n")[0])
    parser.add_argument("report", help="pip-audit's JSON report")
    parser.add_argument("ignores", help="the ignore list (TOML)")
    parser.add_argument("--today", type=date.fromisoformat, help="YYYY-MM-DD, for tests")
    parser.add_argument("--summary", help="append the findings here, as Markdown")
    args = parser.parse_args(argv)
    today = args.today or datetime.now(UTC).date()
    try:
        ignores = load_ignores(args.ignores, today)
        with open(args.report, encoding="utf-8") as handle:
            found = json.load(handle)
        failing, unfixed, ignored = judge(found, ignores)
        audited = len(found["dependencies"])
    except Refused as refusal:
        print(f"::error::{refusal}")
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"::error::the audit report could not be read ({type(error).__name__}: {error})")
        return 1
    for line in failing:
        print(f"::error::{line}")
    for line in unfixed:
        print(f"::warning::{line}")
    for line in ignored:
        print(line)
    if not (failing or unfixed or ignored):
        print(f"{audited} dependencies audited: none has a known vulnerability")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as out:
            out.write(f"### Locked dependencies: {audited} audited\n\n")
            for title, lines in (
                ("Fixed upstream, so this run fails", failing),
                ("No fixed version yet (listed, not failing)", unfixed),
                ("Ignored, each with its reason and its last day", ignored),
            ):
                if lines:
                    out.write(f"{title}:\n\n" + "".join(f"- {line}\n" for line in lines) + "\n")
            if not (failing or unfixed or ignored):
                out.write("None has a known vulnerability.\n")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
