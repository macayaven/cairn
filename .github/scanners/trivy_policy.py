"""Filter and summarize Trivy JSON reports using .github/scanners/trivy-ignore.yaml."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

_ENTRY_RE = re.compile(r"^\s*-\s+id:\s*(?P<id>\S+)")
_EXPIRED_AT_RE = re.compile(r"^\s+expired_at:\s*(?P<date>\d{4}-\d{2}-\d{2})\b")


def parse_trivy_report(path: Path) -> list[dict]:
    """Load one Trivy `--format json` report and validate its top-level structure."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("Results"), list):
        raise ValueError(f"{path}: expected a Trivy JSON object with a Results list")
    findings: list[dict] = []
    for result in data["Results"]:
        if not isinstance(result, dict):
            raise ValueError(f"{path}: expected object items inside Results")
        target = str(result.get("Target", ""))
        for vuln in result.get("Vulnerabilities") or ():
            if not isinstance(vuln, dict):
                raise ValueError(f"{path}: expected object items inside Vulnerabilities")
            findings.append(
                {
                    "id": str(vuln.get("VulnerabilityID", "")),
                    "package": str(vuln.get("PkgName", "")),
                    "installed": str(vuln.get("InstalledVersion", "")),
                    "fixed": str(vuln.get("FixedVersion") or "").strip(),
                    "severity": str(vuln.get("Severity", "")).upper(),
                    "target": target,
                }
            )
    return findings


def judge(
    findings: list[dict],
    *,
    severities: frozenset[str] = frozenset({"HIGH", "CRITICAL"}),
) -> tuple[list[dict], list[dict]]:
    """Split matching vulnerabilities into (fixed_findings, unfixed_findings)."""
    fixed: list[dict] = []
    unfixed: list[dict] = []
    seen_fixed: set[tuple[str, str, str, str]] = set()
    seen_unfixed: set[tuple[str, str, str]] = set()
    for item in findings:
        if item["severity"] not in severities:
            continue
        if item["fixed"]:
            key = (item["severity"], item["id"], item["package"], item["fixed"])
            if key not in seen_fixed:
                seen_fixed.add(key)
                fixed.append(item)
        else:
            key_u = (item["severity"], item["id"], item["package"])
            if key_u not in seen_unfixed:
                seen_unfixed.add(key_u)
                unfixed.append(item)
    return fixed, unfixed


def expired_ignores(path: Path, today: date | None = None) -> list[str]:
    """Return any Trivy ignore IDs whose `expired_at` date in trivy-ignore.yaml has passed."""
    if not path.is_file():
        return []
    today = today or date.today()
    expired: list[str] = []
    current_id: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        m_id = _ENTRY_RE.match(raw)
        if m_id:
            current_id = m_id.group("id").strip("\"'")
            continue
        m_date = _EXPIRED_AT_RE.match(raw)
        if current_id and m_date:
            if date.fromisoformat(m_date.group("date")) < today:
                expired.append(current_id)
            current_id = None
    return expired


def format_markdown(unfixed: list[dict], fixed: list[dict]) -> str:
    """Format a concise summary for $GITHUB_STEP_SUMMARY / stderr."""
    lines = ["### Image: HIGH and CRITICAL with no fixed version (listed, not failing)"]
    if not unfixed:
        lines.append("- None found.")
    else:
        lines.append("| Severity | CVE | Package | Installed | Target |")
        lines.append("| --- | --- | --- | --- | --- |")
        for item in sorted(unfixed, key=lambda x: (x["severity"], x["id"], x["package"])):
            lines.append(
                f"| {item['severity']} | {item['id']} | {item['package']} "
                f"| {item['installed']} | {item['target']} |"
            )
    if fixed:
        lines.append("")
        lines.append("### Image: HIGH and CRITICAL with a fixed version (failing)")
        lines.append("| Severity | CVE | Package | Installed | Fixed |")
        lines.append("| --- | --- | --- | --- | --- |")
        for item in sorted(fixed, key=lambda x: (x["severity"], x["id"], x["package"])):
            lines.append(
                f"| {item['severity']} | {item['id']} | {item['package']} "
                f"| {item['installed']} | {item['fixed']} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Trivy JSON scan reports.")
    parser.add_argument("reports", nargs="+", type=Path, help="Trivy JSON report paths")
    parser.add_argument("--summary", type=Path, default=None, help="Optional summary output file")
    parser.add_argument(
        "--ignorefile", type=Path, default=None, help="Optional trivy-ignore.yaml to validate"
    )
    args = parser.parse_args(argv)

    all_findings: list[dict] = []
    try:
        for report in args.reports:
            all_findings.extend(parse_trivy_report(report))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"trivy_policy error: {exc}", file=sys.stderr)
        return 2

    if args.ignorefile and (expired := expired_ignores(args.ignorefile)):
        print(
            f"trivy_policy error: expired Trivy ignore entries: {', '.join(expired)}",
            file=sys.stderr,
        )
        return 1

    fixed, unfixed = judge(all_findings)
    text = format_markdown(unfixed, fixed)
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)

    if fixed:
        for item in fixed:
            print(
                f"FAIL {item['severity']} {item['id']} in {item['package']} "
                f"({item['installed']} -> {item['fixed']})",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
