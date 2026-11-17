"""T4 eval: compile the synthetic fixtures with each model, gate every page, score every draft.

This is an eval, not a test: it calls real models, so nothing is sent without --yes. Ask
before a run against a shared model server: one that holds a single model at a time evicts
whatever is loaded there, including a model in live use.

    uv run python evals/t4_compile.py --model LANE [--model LANE ...] \\
        --env-file model.env --env-prefix CAIRN_LLM \\
        [--fixture FILE ...] [--gate verbatim|strict|lint] [--probe] [--repeat N] \\
        [--max-tokens N] [--out FILE] [--arrange [--page-claims N ...]] --yes

The endpoint comes from <PREFIX>_BASE_URL and <PREFIX>_API_KEY (default prefix CAIRN_LLM),
read from the environment or --env-file; there is no default endpoint, except with
<PREFIX>_PROVIDER=anthropic, Anthropic's Messages API (cairn.llm.AnthropicMessages), whose own
address is the default and needs <PREFIX>_API_KEY; --no-json-mode has no effect on it.
<PREFIX>_REQUEST_EXTRA,
a JSON object, adds fields to every request (cairn.llm), for a lane that must be told not to
reason before it answers. Without --yes the
script checks that configuration and prints the plan, and sends nothing. Each call is capped
at --max-tokens completion tokens (8192), so a reply that never stops ends as unusable output
(cut off) instead of waiting out --timeout, if the lane reaches the cap within --timeout.

Each fixture is compiled into a fresh in-memory store with compile_note and, by default,
verbatim_gate, the gate compile_note gives model drafts: strict_lint_gate (lint_gate's
guards plus coverage) plus the rule that a claim is one passage, whole: never cut, never
joined, a fold only of passages with the same text. --gate strict or --gate lint names
another. The extractive baseline compiles every fixture first, through the same gate; a
fixture it cannot save stops the run before any model is called, since a model cannot be
scored against it. Per model, and for the baseline as a
reference row:
- gate pass: pages the gate saved / runs. Failures split into gate rejections, unusable
  output (compile.invalid_output) and provider errors (compile.provider_error).
- strict pass: saved pages that cite every content span. The strict gate already demands
  it, so it equals gate pass; it stays as a check on the gate.
- Source-level scores from lint_pages over every content span of the note, for saved and
  rejected drafts alike: share of spans cited, fact retention, code-line retention,
  compression, share of claims the grounding guard flags, share of claims copied verbatim.
  They are means over only the runs that produced a draft, which the `scored` column counts:
  a run that ends in unusable output or a provider error has no draft to score.
- Latency: the median seconds per run, and the first (warm-up) call, which includes loading
  the model. Tokens: mean prompt/completion tokens per run, as the endpoint reports them, and
  only when every call of every run reported both counts; otherwise the report says
  "incomplete" and counts the runs that were, rather than averaging what was reported.
Then per fixture, paired with its baseline: how many of each model's runs were saved.

A provider failure is kept as its HTTP status and a fixed category (provider_failure), never
the endpoint's error text, which may echo note text or a key.

Output: a Markdown table on stdout, progress on stderr, and a JSON file (default
private/t4/t4-<UTC time>.json; private/ is gitignored). The report holds the drafted claims,
so keep it under private/: it goes elsewhere only when --out says so. Model names are
recorded, never the endpoint URL or key. The report names the cairn commit, whether the tree
had uncommitted changes, and a sha256 of the code that ran (code_sha256), which ties numbers
from an uncommitted tree to exact code. Exit codes: 0 done; 1 a model was skipped because its
warm-up failed; 2 bad configuration or usage; 3 (BASELINE_FAILED) the baseline could not save
a fixture; 4 (NO_MODEL_SAVED) no model compile was saved; 5 (BELOW_THRESHOLD) with
--fail-under RATE, a model saved a smaller share of its runs than RATE (a model skipped at
warm-up saved none). Without --fail-under a run that measured exits 0 however few drafts were
saved: the exit code says whether the measurement ran, the report says how it went. --out
outside private/ prints a warning.

--arrange (T4b) measures the arranger (cairn.arrange) instead of the composer. Each fixture is
compiled extractively into its own page, and all of them into one more page ("all fixtures,
one page", when there are two or more); each run arranges a fresh copy of a page with
arrange_page, as `serve --model-env` does: at most ARRANGE_ATTEMPTS (2) calls, each answer
bounded by cairn.arrange.answer_bound (or --max-tokens, if lower), and no price. Per model:
how often the answer was a valid arrangement (arranged, unchanged, or refused only by the
gate), each status, the median seconds per page, the mean tokens in and out (only when every
call reported both), the mean calls and the claims moved and folded. Each run keeps the new
order as the old positions of the claims ("order") and the ids folded, never claim text. The
report goes to private/t4/t4-arrange-<UTC time>.json. Exit codes as above, with 4 when no
answer of any model was a valid arrangement and --fail-under applying to that share.

--page-claims N (repeatable, with --arrange) adds a page of exactly N claims, "synthetic
page, N claims", made of generated notes (synthetic_notes: the same text every time, no
one's notes). A note re-arranges the whole page it lands on, so what a note costs follows the
page's size, and the fixtures stop at some 50 claims: 100, 200 and 300 (the most cairn sends,
cairn.arrange.MAX_CLAIMS) show the rest of the curve. Per page, `runs` holds its claims, calls,
tokens and seconds.
"""

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import httpx

import cairn
from cairn.arrange import PROMPT_VERSION as ARRANGE_PROMPT_VERSION
from cairn.arrange import arrange_page
from cairn.compiler import (
    CONTENT_KINDS,
    CandidatePage,
    Composer,
    Gate,
    compile_note,
    extractive,
)
from cairn.gate import lint_gate, strict_lint_gate, verbatim_gate
from cairn.lint import ClaimInput, LintInputError, PageInput, lint_pages
from cairn.llm import (
    AnthropicMessages,
    ChatModel,
    LLMConfigError,
    LLMError,
    failure,
    from_env,
    read_env_file,
)
from cairn.llm_compiler import PROMPT_VERSION, LLMComposer
from cairn.spans import Span
from cairn.store import Store

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
BASELINE = "extractive (baseline)"
GATES: dict[str, Gate] = {"verbatim": verbatim_gate, "strict": strict_lint_gate, "lint": lint_gate}
DONE, WARMUP_FAILED, BAD_USAGE, BASELINE_FAILED, NO_MODEL_SAVED, BELOW_THRESHOLD = range(6)
PROVIDER_ERROR = "compile.provider_error"
WARMUP = [{"role": "user", "content": 'Reply with this JSON object and nothing else: {"ok": true}'}]
ARRANGE_ATTEMPTS = 2  # model calls per page, as serve's worker makes
ALL_PAGES = "all fixtures, one page"
VALID = ("arranged", "unchanged", "rejected")  # the answer was an arrangement
SIZES_TOPIC = "Arrange eval sizes"
NOTE_CLAIMS = 10  # claims a generated note adds to its page, in two sections of five
_SECTIONS = ("Decisions", "Facts", "Follow-ups", "Risks")
_SERVICES = (
    "ingest queue",
    "billing worker",
    "search index",
    "audit export",
    "session cache",
    "report builder",
    "mail relay",
)
_ZONES = ("north", "south", "east", "west", "central")
_LINES = (
    "The {service} keeps {n} replicas in the {zone} zone since operations review {k}.",
    "Rollouts of the {service} wait {n} green canary hours in the {zone} zone, as of change {k}.",
    "Ask the owners of the {service} whether {n} days of retention cover request {k}.",
    "A restart of the {service} in the {zone} zone took about {n} minutes in drill {k}.",
    "If the {service} falls behind by {n} minutes, page the {zone} on-call, per incident {k}.",
)
ARRANGE_COLUMNS = (
    "model",
    "pages",
    "valid",
    "arranged/unchanged/invalid/other",
    "p50 s",
    "cold s",
    "tokens in/out",
    "calls",
    "moved",
    "folded",
)
COLUMNS = (
    "model",
    "runs",
    "gate pass",
    "strict pass",
    "fails gate/output/provider",
    "scored",
    "spans cited",
    "retention",
    "code lines",
    "ungrounded claims",
    "verbatim claims",
    "compression",
    "p50 s",
    "cold s",
    "tokens in/out",
    "attempts",
)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the eval. `transport` and `sleep` (the providers' backoff) are for tests."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.page_claims and not args.arrange:
        parser.error("--page-claims needs --arrange")
    fixtures = [Path(f) for f in args.fixture] or sorted(FIXTURES.glob("*.md"))
    if args.probe:
        fixtures = fixtures[:1]
    try:
        env = dict(os.environ if environ is None else environ)
        if args.env_file:
            env.update(read_env_file(args.env_file))
        models = [
            from_env(
                name,
                environ=env,
                prefix=args.env_prefix,
                timeout=args.timeout,
                json_mode=not args.no_json_mode,
                max_tokens=args.max_tokens,
                transport=transport,
                sleep=sleep,
            )
            for name in args.model
        ]
    except LLMConfigError as error:
        print(f"t4_compile: {error}", file=sys.stderr)
        return BAD_USAGE
    if missing := [str(f) for f in fixtures if not f.is_file()]:
        print(f"t4_compile: no such fixture: {', '.join(missing)}", file=sys.stderr)
        return BAD_USAGE
    name = "t4-arrange" if args.arrange else "t4"
    out = args.out or REPO / "private" / "t4" / f"{name}-{_stamp()}.json"
    if not out.resolve().is_relative_to((REPO / "private").resolve()):
        print(
            "t4_compile: warning: --out is outside private/, and the report holds drafted claims",
            file=sys.stderr,
        )
    if not args.yes:
        print(
            _arrange_plan(args, fixtures, out) if args.arrange else _plan(args, fixtures, env, out)
        )
        return 0
    if args.arrange:
        return _run_arrange(args, fixtures, models, out)
    return _run(args, fixtures, models, out)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evals/t4_compile.py",
        description="Compile the synthetic fixtures with each model behind a gate.",
    )
    parser.add_argument(
        "--model", action="append", required=True, help="a model name, as the endpoint knows it"
    )
    parser.add_argument("--fixture", action="append", default=[], help="a Markdown fixture")
    parser.add_argument(
        "--gate", choices=list(GATES), default="verbatim", help="the gate for every draft"
    )
    parser.add_argument("--env-file", type=Path, help="KEY=VALUE file with the endpoint settings")
    parser.add_argument("--env-prefix", default="CAIRN_LLM", help="reads PREFIX_BASE_URL, ...")
    parser.add_argument("--attempts", type=int, default=3, help="model calls per draft (3)")
    parser.add_argument("--repeat", type=int, default=1, help="runs per fixture and model (1)")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds per call (600)")
    parser.add_argument(
        "--max-tokens", type=int, default=8192, help="completion token cap per call (8192)"
    )
    parser.add_argument(
        "--no-json-mode",
        action="store_true",
        help="omit response_format (no effect with PREFIX_PROVIDER=anthropic, which never sends"
        " it)",
    )
    parser.add_argument("--probe", action="store_true", help="only the first fixture, once")
    parser.add_argument("--out", type=Path, help="JSON report path (default private/t4/...)")
    parser.add_argument("--yes", action="store_true", help="send requests (else: dry run)")
    parser.add_argument(
        "--arrange", action="store_true", help="measure the arranger (cairn.arrange) instead"
    )
    parser.add_argument(
        "--page-claims",
        type=_size,
        action="append",
        default=[],
        metavar="N",
        help="with --arrange: also a generated page of exactly N claims (repeatable)",
    )
    parser.add_argument(
        "--fail-under",
        type=_rate,
        metavar="RATE",
        help="exit 5 when a model saves a smaller share of its runs (0 to 1)",
    )
    return parser


def _size(text: str) -> int:
    try:
        size = int(text)
    except ValueError:
        size = 0
    if size < 1:
        raise argparse.ArgumentTypeError("give a number of claims, 1 or more")
    return size


def _rate(text: str) -> float:
    try:
        rate = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("a rate is a number from 0 to 1") from None
    if not 0.0 <= rate <= 1.0:
        raise argparse.ArgumentTypeError("a rate is a number from 0 to 1")
    return rate


def _plan(args: argparse.Namespace, fixtures: list[Path], env: Mapping[str, str], out: Path) -> str:
    repeat = 1 if args.probe else args.repeat
    runs = len(fixtures) * repeat * len(args.model)
    key = f"{args.env_prefix}_API_KEY"
    # Without a base URL the models were built only for Anthropic's format, which has a default.
    base = (
        "set" if env.get(f"{args.env_prefix}_BASE_URL", "").strip() else "not set: Anthropic's API"
    )
    return "\n".join(
        [
            "T4 eval plan (dry run: nothing was sent)",
            f"- models, in order: {', '.join(args.model)}",
            "- each model is loaded once; a server holding one model can evict the one loaded",
            f"- fixtures: {len(fixtures)} ({', '.join(f.name for f in fixtures)}), "
            f"{repeat} run(s) each",
            f"- model calls: {len(args.model)} warm-up(s) plus {runs} compile(s), up to "
            f"{args.attempts} call(s) each; per call a {args.timeout:g} s timeout and at most "
            f"{args.max_tokens} completion tokens",
            f"- gate: {GATES[args.gate].__name__}; the baseline must save every fixture first",
            f"- endpoint: {args.env_prefix}_BASE_URL ({base}); key: {key} "
            f"({'set' if env.get(key, '').strip() else 'not set'}); values are never shown",
            f"- report: {out}",
            "Add --yes to run it.",
        ]
    )


def _run(args: argparse.Namespace, fixtures: list[Path], models: list[ChatModel], out: Path) -> int:
    """Compile with the baseline, then with each model in turn; rewrite the report after each
    model, so a run that dies midway keeps every model it finished. A fixture the baseline
    cannot save ends the run before any model is called."""
    started = _now()
    repeat = 1 if args.probe else args.repeat
    gate = GATES[args.gate]
    rows = [_compile(BASELINE, extractive, path, gate) for path in fixtures]
    warmups: dict[str, dict[str, Any]] = {}
    settings = {
        "gate": gate.__name__,
        "attempts": args.attempts,
        "repeat": repeat,
        "timeout_s": args.timeout,
        "json_mode": not args.no_json_mode,
        "max_tokens": args.max_tokens,
        "env_prefix": args.env_prefix,
        "provider": _provider(models),
        "probe": args.probe,
    }
    head = {
        "eval": "t4-compile",
        "started_at": started,
        **_commit(),
        "code_sha256": _code_sha256(),
        "prompt_version": PROMPT_VERSION,
        "settings": settings,
        "fixtures": [_fixture(path) for path in fixtures],
        "models": [model.name for model in models],
    }
    if failed := [row["fixture"] for row in rows if row["outcome"] != "saved"]:
        report = {**head, "finished_at": _now(), "baseline_failures": failed, "runs": rows}
        _write(report, out)
        print(
            f"t4_compile: the baseline could not save {', '.join(failed)}; no model was "
            f"called. Wrote {out}",
            file=sys.stderr,
        )
        return BASELINE_FAILED
    for model in models:
        clock = time.perf_counter()
        try:
            model.complete(WARMUP)
        except LLMError as error:
            failure = provider_failure(str(error))
            shown = _shown(failure)
            warmups[model.name] = {"error": shown, "status": failure["status"]}
            print(f"{model.name}: warm-up failed, skipped: {shown}", file=sys.stderr)
        else:
            warmups[model.name] = {"seconds": round(time.perf_counter() - clock, 3)}
            composer = LLMComposer(model, attempts=args.attempts)
            for _ in range(repeat):
                for path in fixtures:
                    row = _compile(model.name, composer, path, gate)
                    print(
                        f"{model.name}  {path.name}  {row['outcome']}  {row['latency_s']:.1f} s",
                        file=sys.stderr,
                    )
                    rows.append(row)
        report = {
            **head,
            "finished_at": _now(),
            "warmups": warmups,
            "summary": summarize(rows, warmups),
            "by_fixture": by_fixture(rows),
            "runs": rows,
        }
        _write(report, out)
    print(render(summarize(rows, warmups), by_fixture(rows)))
    print(f"wrote {out}", file=sys.stderr)
    if not any(row["outcome"] == "saved" for row in rows if row["model"] != BASELINE):
        print("t4_compile: no model compile was saved", file=sys.stderr)
        return NO_MODEL_SAVED
    if args.fail_under is not None and (low := below(rows, warmups, args.fail_under)):
        for name, saved, runs in low:
            print(
                f"t4_compile: {name} saved {saved}/{runs} runs, below --fail-under "
                f"{args.fail_under:g}",
                file=sys.stderr,
            )
        return BELOW_THRESHOLD
    return WARMUP_FAILED if any("error" in w for w in warmups.values()) else DONE


class ArrangePage(NamedTuple):
    """A page to arrange: its label, the fixtures it is made of (none for a synthetic page),
    its notes in the order they are taken, and the topic that puts them on one page."""

    label: str
    fixtures: list[str]
    notes: list[str]
    topic: str | None


def synthetic_notes(claims: int) -> list[str]:
    """Generated notes that compile, on one topic, to a page of exactly `claims` claims.

    The same text every time. Each note adds NOTE_CLAIMS list items (the last one the rest)
    under two of four headings, so a page grows as a real one does: note by note, the same
    sections returning. Every third note starts by saying again a line of the note before it,
    so there is something to fold. No line comes from anyone's notes.
    """
    notes: list[str] = []
    made = 0
    carried = ""  # the last line of the previous note's last section
    while made < claims:
        number = len(notes)
        sections = (_SECTIONS[number % 4], _SECTIONS[(number + 1) % 4])
        size = min(NOTE_CLAIMS, claims - made)
        half = (size + 1) // 2
        parts: list[str] = []
        for heading, count in zip(sections, (half, size - half), strict=True):
            if not count:
                continue
            lines = []
            for _ in range(count):
                line = _LINES[made % len(_LINES)].format(
                    service=_SERVICES[made % len(_SERVICES)],
                    zone=_ZONES[(made // 3) % len(_ZONES)],
                    n=2 + made % 9,
                    k=1000 + made,
                )
                lines.append(f"- {line}")
                made += 1
            if number % 3 == 2 and heading == sections[0] and carried:
                lines[0] = carried  # the first section here is the previous note's last
            carried = lines[-1]
            parts.append(f"## {heading}\n\n" + "\n".join(lines))
        notes.append("\n\n".join(parts) + "\n")
    return notes


def _arrange_pages(fixtures: Sequence[Path], sizes: Sequence[int] = ()) -> list[ArrangePage]:
    pages = [ArrangePage(f.name, [f.name], [f.read_text(encoding="utf-8")], None) for f in fixtures]
    if len(fixtures) > 1:
        notes = [f.read_text(encoding="utf-8") for f in fixtures]
        pages.append(ArrangePage(ALL_PAGES, [f.name for f in fixtures], notes, "Arrange eval"))
    for size in sizes:
        label = f"synthetic page, {size} claims"
        pages.append(ArrangePage(label, [], synthetic_notes(size), SIZES_TOPIC))
    return pages


def _arrange_plan(args: argparse.Namespace, fixtures: list[Path], out: Path) -> str:
    repeat = 1 if args.probe else args.repeat
    pages = _arrange_pages(fixtures, args.page_claims)
    return "\n".join(
        [
            "T4 arranger eval plan (dry run: nothing was sent)",
            f"- models, in order: {', '.join(args.model)}",
            "- each model is loaded once; a server holding one model can evict the one loaded",
            f"- pages: {len(pages)} ({', '.join(page.label for page in pages)}), "
            f"{repeat} run(s) each",
            f"- model calls: {len(args.model)} warm-up(s) plus up to "
            f"{len(pages) * repeat * len(args.model) * ARRANGE_ATTEMPTS} arranging call(s)",
            f"- report: {out}",
            "Add --yes to run it.",
        ]
    )


def _run_arrange(
    args: argparse.Namespace, fixtures: list[Path], models: list[ChatModel], out: Path
) -> int:
    """Arrange each page with each model in turn; rewrite the report after each page."""
    started = _now()
    repeat = 1 if args.probe else args.repeat
    pages = _arrange_pages(fixtures, args.page_claims)
    rows: list[dict[str, Any]] = []
    warmups: dict[str, dict[str, Any]] = {}
    head = {
        "eval": "t4-arrange",
        "started_at": started,
        **_commit(),
        "code_sha256": _code_sha256(),
        "prompt_version": ARRANGE_PROMPT_VERSION,
        "settings": {
            "attempts": ARRANGE_ATTEMPTS,
            "repeat": repeat,
            "timeout_s": args.timeout,
            "json_mode": not args.no_json_mode,
            "max_tokens": args.max_tokens,
            "env_prefix": args.env_prefix,
            "provider": _provider(models),
            "probe": args.probe,
        },
        "fixtures": [_fixture(path) for path in fixtures],
        "pages": [{"page": page.label, "fixtures": page.fixtures} for page in pages],
        "models": [model.name for model in models],
    }

    def save() -> None:
        summary = summarize_arrange(rows, warmups)
        report = {**head, "finished_at": _now(), "warmups": warmups, "summary": summary}
        _write({**report, "runs": rows}, out)

    for model in models:
        clock = time.perf_counter()
        try:
            model.complete(WARMUP)
        except LLMError as error:
            failure = provider_failure(str(error))
            warmups[model.name] = {"error": _shown(failure), "status": failure["status"]}
            print(f"{model.name}: warm-up failed, skipped: {_shown(failure)}", file=sys.stderr)
        else:
            warmups[model.name] = {"seconds": round(time.perf_counter() - clock, 3)}
            for _ in range(repeat):
                for page in pages:
                    row = _arrange(model, page)
                    print(
                        f"{model.name}  {page.label}  {row['status']}  {row['latency_s']:.1f} s",
                        file=sys.stderr,
                    )
                    rows.append(row)
                    save()  # after every page: a run cut short keeps what it measured
        save()
    summary = summarize_arrange(rows, warmups)
    print(render_arrange(summary))
    print(f"wrote {out}", file=sys.stderr)
    if not any(row["status"] in VALID for row in rows):
        print("t4_compile: no answer was a valid arrangement", file=sys.stderr)
        return NO_MODEL_SAVED
    if args.fail_under is not None:
        low = [
            s for s in summary if (s["valid"] / s["runs"] if s["runs"] else 0.0) < args.fail_under
        ]
        for s in low:
            print(
                f"t4_compile: {s['model']} arranged {s['valid']}/{s['runs']} pages, below "
                f"--fail-under {args.fail_under:g}",
                file=sys.stderr,
            )
        if low:
            return BELOW_THRESHOLD
    return WARMUP_FAILED if any("error" in w for w in warmups.values()) else DONE


def _arrange(model: ChatModel, page: ArrangePage) -> dict:
    """Arrange a fresh copy of one page: its notes compiled extractively, then the model."""
    label = page.label
    store = Store(":memory:")
    try:
        project = store.create_project("eval")
        slug = ""
        for note in page.notes:
            stored, _ = store.add_note(project.id, note, topic=page.topic)
            slug = compile_note(store, stored).slug
        before = store.get_page(project.id, slug)
        assert before is not None, f"the baseline saved no page for {label}"
        result = arrange_page(store, project.id, slug, model, attempts=ARRANGE_ATTEMPTS)
        after = store.get_page(project.id, slug)
    finally:
        store.close()
    place = {claim.id: n for n, claim in enumerate(before.claims)}
    kept = {claim.id for claim in after.claims} if after else set()
    draft = result.draft
    return {
        "model": model.name,
        "page": label,
        "claims": len(before.claims),
        "status": result.status,
        "problem": result.problem,
        "attempts": draft.attempts,
        "latency_s": round(draft.latency_s, 3),
        "prompt_tokens": draft.prompt_tokens,
        "completion_tokens": draft.completion_tokens,
        "usage_complete": draft.usage_complete,
        "moved": result.moved,
        "merged": result.merged,
        "order": [place[claim.id] for claim in after.claims] if after else [],
        "folded": [claim.id for claim in before.claims if claim.id not in kept],
    }


def summarize_arrange(rows: Sequence[dict[str, Any]], warmups: Mapping[str, dict]) -> list[dict]:
    """One entry per model, in the order the rows came."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["model"], []).append(row)
    summary = []
    for model, runs in groups.items():
        statuses = Counter(r["status"] for r in runs)
        incomplete = sum(not r["usage_complete"] for r in runs)
        summary.append(
            {
                "model": model,
                "runs": len(runs),
                "valid": sum(statuses[s] for s in VALID),
                "arranged": statuses["arranged"],
                "unchanged": statuses["unchanged"],
                "invalid": statuses["invalid"],
                "statuses": dict(statuses.most_common()),
                "latency_p50_s": statistics.median(r["latency_s"] for r in runs),
                "cold_start_s": warmups.get(model, {}).get("seconds"),
                "usage_complete": not incomplete,
                "prompt_tokens_mean": None
                if incomplete
                else _mean(r["prompt_tokens"] for r in runs),
                "completion_tokens_mean": None
                if incomplete
                else _mean(r["completion_tokens"] for r in runs),
                "attempts_mean": _mean(r["attempts"] for r in runs),
                "moved_mean": _mean(r["moved"] for r in runs),
                "merged_mean": _mean(r["merged"] for r in runs),
            }
        )
    return summary


def render_arrange(summary: Sequence[dict[str, Any]]) -> str:
    lines = ["| " + " | ".join(ARRANGE_COLUMNS) + " |", "|" + "---|" * len(ARRANGE_COLUMNS)]
    for s in summary:
        n = s["runs"]
        other = n - s["arranged"] - s["unchanged"] - s["invalid"]
        cells = [
            s["model"],
            str(n),
            f"{s['valid']}/{n}",
            f"{s['arranged']}/{s['unchanged']}/{s['invalid']}/{other}",
            _fixed(s["latency_p50_s"], 1),
            _fixed(s["cold_start_s"], 1),
            f"{_whole(s['prompt_tokens_mean'])}/{_whole(s['completion_tokens_mean'])}"
            if s["usage_complete"]
            else "incomplete",
            _fixed(s["attempts_mean"], 1),
            _fixed(s["moved_mean"], 1),
            _fixed(s["merged_mean"], 1),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "Valid: the answer was an arrangement (arranged, unchanged, or refused by the gate). "
        '"Other" counts every other status; the report lists them. Tokens read "incomplete" '
        "when a call did not report them: no mean is shown then."
    )
    return "\n".join(lines)


def below(
    rows: Sequence[Mapping[str, Any]], warmups: Mapping[str, Any], rate: float
) -> list[tuple[str, int, int]]:
    """(model, saved runs, runs) of each model whose saved share is under `rate`. A model
    skipped at warm-up has no saved run, so it is under any rate above 0."""
    low = []
    for name in warmups:
        runs = [r for r in rows if r["model"] == name]
        saved = sum(r["outcome"] in ("saved", "unchanged") for r in runs)
        if (saved / len(runs) if runs else 0.0) < rate:
            low.append((name, saved, len(runs)))
    return low


def _provider(models: Sequence[ChatModel]) -> str:
    """The format a run's models spoke, as <PREFIX>_PROVIDER names it."""
    return "anthropic" if any(isinstance(m, AnthropicMessages) for m in models) else "openai"


def provider_failure(message: str) -> dict[str, Any]:
    """The HTTP status (None when there is none) and a fixed category of an LLMError message:
    nothing the endpoint wrote is kept."""
    status, category = failure(LLMError(message))
    return {"status": status, "category": category}


def _shown(failure: Mapping[str, Any]) -> str:
    return f"HTTP {failure['status']}" if failure["status"] else failure["category"]


def _write(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _compile(model: str, compose: Composer, path: Path, gate: Gate) -> dict[str, Any]:
    """Compile one fixture into a fresh store and score the result."""
    store = Store(":memory:")
    try:
        project = store.create_project("eval")
        note, _ = store.add_note(project.id, path.read_text(encoding="utf-8"))
        spans = [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]
        result = compile_note(store, note, gate=gate, compose=compose)
    finally:
        store.close()
    violations, failure = [], None
    for violation in result.gate.violations if result.gate else []:
        code, _, message = violation.partition(": -: ")
        if code == PROVIDER_ERROR:  # keep the status and category, never the endpoint's text
            failure = provider_failure(message)
            violation = f"{PROVIDER_ERROR}: -: {_shown(failure)}"
        violations.append(violation)
    codes = Counter(v.split(": ", 1)[0] for v in violations)
    if result.status in ("saved", "unchanged"):
        outcome = result.status
    elif PROVIDER_ERROR in codes:
        outcome = "provider_error"
    elif "compile.invalid_output" in codes:
        outcome = "invalid_output"
    else:
        outcome = "rejected"
    draft = result.draft
    return {
        "model": model,
        "fixture": path.name,
        "outcome": outcome,
        "content_spans": len(spans),
        "attempts": draft.attempts if draft else 0,
        "latency_s": round(draft.latency_s, 3) if draft else 0.0,
        "prompt_tokens": draft.prompt_tokens if draft else None,
        "completion_tokens": draft.completion_tokens if draft else None,
        "usage_complete": draft.usage_complete if draft else True,
        "provider_failure": failure,
        "gate": result.gate.metrics if result.gate else {},
        "violation_codes": dict(codes),
        "violations": violations,
        "source": score(result.candidate, spans) if result.candidate else None,
        "claims": [c.model_dump(mode="json") for c in result.candidate.claims]
        if result.candidate
        else [],
    }


def score(candidate: CandidatePage, spans: Sequence[Span]) -> dict[str, Any] | None:
    """Source-level scores: lint_pages over every content span of the note."""
    claims = [ClaimInput(id=c.id, text=c.text, span_ids=list(c.span_ids)) for c in candidate.claims]
    try:
        report = lint_pages(
            [PageInput(slug=candidate.slug, text=candidate.body, claims=claims)], spans
        )
    except LintInputError:
        return None
    plain = report.metrics["plain"]
    content = {s.id: " ".join(s.text.split()) for s in spans}
    cited = {i for c in candidate.claims for i in c.span_ids} & content.keys()
    flagged = {v.claim_id for v in report.violations if v.code.startswith("grounding.")}
    verbatim = sum(
        any(content.get(i) == " ".join(c.text.split()) for i in c.span_ids)
        for c in candidate.claims
    )
    n = len(candidate.claims)
    return {
        "claims": n,
        "spans_cited": len(cited) / len(content) if content else 1.0,
        "uncited_spans": len(content) - len(cited),
        "retention": plain["retention"]["overall"],
        "retention_by_kind": plain["retention"]["by_kind"],
        "code_line_retention": plain["code_line_retention"],
        "compression_ratio": plain["compression_ratio"],
        "ungrounded_claims": len(flagged) / n if n else 0.0,
        "verbatim_claims": verbatim / n if n else 0.0,
    }


def summarize(rows: Sequence[dict[str, Any]], warmups: Mapping[str, dict]) -> list[dict]:
    """One entry per model, in the order the rows came."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["model"], []).append(row)
    summary = []
    for model, runs in groups.items():
        scored = [r["source"] for r in runs if r["source"]]
        outcomes = Counter(r["outcome"] for r in runs)
        codes: Counter[str] = Counter()
        for r in runs:
            codes.update(r["violation_codes"])
        incomplete = sum(not r["usage_complete"] for r in runs)
        summary.append(
            {
                "model": model,
                "runs": len(runs),
                "saved": outcomes["saved"],
                "strict_saved": sum(
                    r["outcome"] == "saved" and r["source"] and r["source"]["uncited_spans"] == 0
                    for r in runs
                ),
                "gate_rejected": outcomes["rejected"],
                "invalid_output": outcomes["invalid_output"],
                "provider_errors": outcomes["provider_error"],
                "scored": len(scored),
                **{
                    key: _mean(s[key] for s in scored)
                    for key in (
                        "spans_cited",
                        "retention",
                        "code_line_retention",
                        "ungrounded_claims",
                        "verbatim_claims",
                        "compression_ratio",
                    )
                },
                "latency_p50_s": statistics.median(r["latency_s"] for r in runs),
                "cold_start_s": warmups.get(model, {}).get("seconds"),
                # A mean of what was reported would read as the whole cost; say instead.
                "usage_complete": not incomplete,
                "usage_incomplete_runs": incomplete,
                "prompt_tokens_mean": None
                if incomplete
                else _mean(r["prompt_tokens"] for r in runs),
                "completion_tokens_mean": None
                if incomplete
                else _mean(r["completion_tokens"] for r in runs),
                "attempts_mean": _mean(r["attempts"] for r in runs),
                "violation_codes": dict(codes.most_common()),
                "retention_by_kind": {
                    kind: _mean(s["retention_by_kind"].get(kind) for s in scored)
                    for kind in sorted({k for s in scored for k in s["retention_by_kind"]})
                },
            }
        )
    return summary


def by_fixture(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each fixture with its baseline outcome and, per model, its runs, saves and outcomes."""
    fixtures: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = fixtures.setdefault(
            row["fixture"], {"fixture": row["fixture"], "baseline": None, "models": {}}
        )
        if row["model"] == BASELINE:
            entry["baseline"] = row["outcome"]
            continue
        model = entry["models"].setdefault(row["model"], {"runs": 0, "saved": 0, "outcomes": {}})
        model["runs"] += 1
        model["saved"] += row["outcome"] == "saved"
        model["outcomes"][row["outcome"]] = model["outcomes"].get(row["outcome"], 0) + 1
    return list(fixtures.values())


def render(summary: Sequence[dict[str, Any]], fixtures: Sequence[dict[str, Any]] = ()) -> str:
    """The summary as a Markdown table, each model's violation codes, retention by kind, then
    each fixture against its baseline."""
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for s in summary:
        n = s["runs"]
        cells = [
            s["model"],
            str(n),
            f"{s['saved']}/{n}",
            f"{s['strict_saved']}/{n}",
            f"{s['gate_rejected']}/{s['invalid_output']}/{s['provider_errors']}",
            f"{s['scored']}/{n}",
            *(
                _fixed(s[k])
                for k in (
                    "spans_cited",
                    "retention",
                    "code_line_retention",
                    "ungrounded_claims",
                    "verbatim_claims",
                    "compression_ratio",
                )
            ),
            _fixed(s["latency_p50_s"], 1),
            _fixed(s["cold_start_s"], 1),
            f"{_whole(s['prompt_tokens_mean'])}/{_whole(s['completion_tokens_mean'])}"
            if s["usage_complete"]
            else "incomplete",
            _fixed(s["attempts_mean"], 1),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "Spans cited to compression, and retention by kind, are means over only the runs that "
        "produced a draft (scored), rejected drafts included; a run with unusable output or a "
        "provider error has none. Latency, tokens and attempts cover every run. Tokens read "
        '"incomplete" when a call did not report them or failed: no mean is shown then.'
    )
    for s in summary:
        if s["violation_codes"]:
            codes = ", ".join(f"{code} x{n}" for code, n in s["violation_codes"].items())
            lines.append(f"- {s['model']}: {codes}")
    kinds = sorted({kind for s in summary for kind in s["retention_by_kind"]})
    lines += ["", "Retention by fact kind (means over the runs that have the kind):", ""]
    lines += ["| " + " | ".join(("model", *kinds)) + " |", "|" + "---|" * (len(kinds) + 1)]
    for s in summary:
        cells = [s["model"], *(_fixed(s["retention_by_kind"].get(k)) for k in kinds)]
        lines.append("| " + " | ".join(cells) + " |")
    models = list(dict.fromkeys(m for f in fixtures for m in f["models"]))
    if fixtures:
        lines += ["", "Per fixture, against its baseline (saved runs / runs):", ""]
        lines += ["| " + " | ".join(("fixture", "baseline", *models)) + " |"]
        lines += ["|" + "---|" * (len(models) + 2)]
        for f in fixtures:
            counts = (f["models"].get(m) for m in models)
            cells = [f"{c['saved']}/{c['runs']}" if c else "-" for c in counts]
            lines.append("| " + " | ".join((f["fixture"], f["baseline"] or "-", *cells)) + " |")
    return "\n".join(lines)


def _mean(values: Any) -> float | None:
    known = [v for v in values if v is not None]
    return statistics.fmean(known) if known else None


def _fixed(value: float | None, places: int = 2) -> str:
    return "-" if value is None else f"{value:.{places}f}"


def _whole(value: float | None) -> str:
    return "-" if value is None else str(round(value))


def _fixture(path: Path) -> dict[str, str]:
    return {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _code_sha256() -> str:
    """sha256 over cairn's Python files, as imported, and this script."""
    package = Path(cairn.__file__).resolve().parent
    files = {p.relative_to(package.parent).as_posix(): p for p in package.rglob("*.py")}
    files["evals/t4_compile.py"] = Path(__file__).resolve()
    digest = hashlib.sha256()
    for name in sorted(files):
        data = files[name].read_bytes()
        digest.update(f"{name}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()


def _commit() -> dict[str, Any]:
    """The cairn commit the run used, and whether the tree had uncommitted changes."""

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    status = git("status", "--porcelain")
    return {"cairn_commit": git("rev-parse", "HEAD"), "cairn_dirty": bool(status)}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
