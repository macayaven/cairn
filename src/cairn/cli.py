"""Command-line entry point: `python -m cairn lint ...`. No `cairn` command is installed
(pyproject.toml has no [project.scripts]); the web service is `python -m cairn.server`.

`python -m cairn lint --source SRC --pages DIR [--entities FILE] [--fail-under KEY=VALUE ...]`
prints the lint report as JSON on stdout. Exit codes: 0 clean; 1 on violations or when a
plain-mode metric is below its --fail-under value; 2 on bad input, a bad threshold or a
usage error. Messages go to stderr.
"""

import argparse
import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cairn.lint import LintInputError, lint

# --fail-under keys: plain-mode metrics over all pages.
FAIL_UNDER: dict[str, Callable[[dict[str, Any]], float]] = {
    "retention": lambda plain: plain["retention"]["overall"],
    "passage_coverage": lambda plain: plain["passage_coverage"],
    "code_line_retention": lambda plain: plain["code_line_retention"],
}


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:  # argparse exits 2 on a usage error, 0 after --help
        return stop.code if isinstance(stop.code, int) else 2
    try:
        thresholds = [_threshold(item) for item in args.fail_under]
        report = lint(args.source, args.pages, args.entities)
    except LintInputError as error:
        print(f"cairn lint: error: {error}", file=sys.stderr)
        return 2
    print(report.model_dump_json(indent=2, by_alias=True))
    breached = False
    for key, minimum in thresholds:
        value = FAIL_UNDER[key](report.metrics["plain"])
        if value < minimum:
            print(f"cairn lint: {key} {value:.4f} is below --fail-under {minimum}", file=sys.stderr)
            breached = True
    return 1 if report.violations or breached else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cairn", description="A provenance-first memory compiler for AI agents."
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    command = commands.add_parser(
        "lint",
        help="score compiled pages against their source",
        description="Score DIR/<slug>.md pages against a source document. Pages with a "
        "DIR/<slug>.claims.json sidecar are cairn-format and must pass its guards.",
    )
    command.add_argument("--source", type=Path, required=True, metavar="SRC", help="source .md")
    command.add_argument("--pages", type=Path, required=True, metavar="DIR", help="page directory")
    command.add_argument("--entities", type=Path, metavar="FILE", help="entities YAML")
    command.add_argument(
        "--fail-under",
        action="extend",
        nargs="+",
        default=[],
        metavar="KEY=VALUE",
        help=f"exit 1 when a plain metric is below VALUE; keys: {', '.join(FAIL_UNDER)}",
    )
    return parser


def _threshold(item: str) -> tuple[str, float]:
    key, equals, raw = item.partition("=")
    if not equals or key not in FAIL_UNDER:
        raise LintInputError(
            f"--fail-under {item!r}: expected KEY=VALUE, KEY one of {', '.join(FAIL_UNDER)}"
        )
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not 0.0 <= value <= 1.0:  # NaN fails this too
        raise LintInputError(f"--fail-under {item!r}: VALUE must be a number from 0 to 1")
    return key, value
