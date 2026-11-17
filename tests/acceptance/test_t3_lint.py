"""T3 acceptance tests (frozen): `cairn lint`.

Written by the orchestrator before implementation. Implementers must not edit this file,
fixtures or gold files. Contract (adopted from the design advisor on 2026-11-10):

Inputs
- `--source SRC.md`: the source is chunked with source_id = the SRC file stem.
- `--pages DIR`: every DIR/<slug>.md is a page. Title = frontmatter `title`, else the first
  H1, else the slug; optional frontmatter `aliases`. The page body is the file minus frontmatter.
- A page is cairn-format when DIR/<slug>.claims.json exists, holding
  {"claims": [{"id", "text", "passage_ids": [...]}], "omitted_facts": [{"fact_id",
  "passage_id", "reason"}]} where fact_id = "<passage_id>#<Fact.start>". Otherwise the page is
  plain.
- DIR/manifest.json (optional): {"unassigned_passages": [passage_id, ...]}.
- `--entities FILE`: YAML {"entities": [{"name", "aliases": [...]}]}.

Entities
- A page's entity: its title or an alias, normalised (casefold, punctuation stripped, a
  trailing "-N" stripped), equal to an entity's normalised name or alias. No match makes it a
  topic page, where attribution does not apply.
- A span's entities: the entities named (whole word, casefold, by name or alias) in the deepest
  heading-path segment that names at least one entity. Empty when no segment names one.

Cairn-format guards (violations, exit 1)
- provenance.missing_passages: a claim with no span ids.
- provenance.unknown_passage: a claim cites an id that is not a source span.
- provenance.claim_not_in_body: the claim text (whitespace-collapsed) is not in the page body.
- grounding.fact_not_in_cited_passages: a fact extracted from the claim text does not occur
  (normalised) in any of the claim's cited spans.
- attribution.other_entity: on entity page X, a cited span's entities are non-empty and
  exclude X, and the span text does not mention X.
- retention.fact_dropped: a non-code fact of a cited span is neither in the page body
  (normalised) nor listed in omitted_facts.
- retention.code_line_dropped: a line of a cited span's code_block fact is not in the page body
  (normalised). One violation per missing line; code blocks never raise retention.fact_dropped.
- coverage.passage_unaccounted: a content span (paragraph, bullet, code, table, quote) is neither
  cited by any cairn page nor listed in unassigned_passages. Only checked when at least one
  cairn-format page exists.

Warnings (never fail)
- attribution.text_mention_only: a cited span's entities exclude X but its text mentions X.
- placement.candidate (both modes): on entity page X, a flag, path, or inline_code fact of at
  least 6 characters where every source span containing it names exactly one entity Y != X
  and does not mention X. `fact` holds the fact value.
- entity.duplicate: two or more pages share a normalised title.

Plain pages are measured, never failed. `--fail-under key=value` (keys: retention,
passage_coverage, code_line_retention; plain-mode metrics over all pages) exits 1 when the metric
is below the value. Exit codes: 0 clean, 1 violations or a threshold breach, 2 input or
schema error. The CLI prints the report as JSON on stdout.
"""

import functools
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cairn.facts import extract_facts
from cairn.lint import LintInputError, LintReport, fact_id, lint
from cairn.spans import Span, chunk

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SOURCE = FIXTURES / "runbook_synthetic.md"
ENTITIES = FIXTURES / "entities.yaml"
LINES = SOURCE.read_text(encoding="utf-8").splitlines()


@functools.cache
def source_spans() -> tuple[Span, ...]:
    return tuple(chunk(SOURCE.stem, SOURCE.read_text(encoding="utf-8")))


def line_of(marker: str) -> int:
    hits = [i + 1 for i, line in enumerate(LINES) if marker in line]
    assert len(hits) == 1, f"marker {marker!r} must match exactly one line, got {hits}"
    return hits[0]


def span(marker: str) -> Span:
    n = line_of(marker)
    (hit,) = [s for s in source_spans() if s.start_line <= n <= s.end_line]
    return hit


def sid(marker: str) -> str:
    return span(marker).id


def page(title: str, *blocks: str) -> str:
    return f"---\ntitle: {title}\n---\n# {title}\n\n" + "\n\n".join(blocks) + "\n"


def claim(cid: str, text: str, *markers: str) -> dict:
    return {"id": cid, "text": text, "passage_ids": [sid(m) for m in markers]}


QUILL = [
    (
        'Quill runs headless with `quill run "<task>" --json --workdir ./scratch`; `--json` '
        "prints one event per line and `--out last.txt` writes the final message to a file.",
        ('**Invoke:** `quill run "<task>"',),
    ),
    ("Pass `--max-seconds 300`; the default timeout is 5 min.", ("**Timeout:**",)),
    (
        "Turn the cache off with `--set cache.enabled=false --set cache.write=false`.",
        ("**Cache off:**",),
    ),
    ("Defaults live in ~/.config/quill/config.toml.", ("**Config:**",)),
    ("A 40-file run cost $1.15 and peaked at 1.2 GB of RAM.", ("**Cost:**",)),
]
LUMEN = [
    ('Lumen runs headless with `lumen exec "<task>" --format jsonl`.', ("**Invoke:** `lumen",)),
    (
        "`--read-only` and `--no-network` give a read-only check; add --quiet to drop progress "
        "lines.",
        ("**Safety:**", "**Quiet:**"),
    ),
    ("Lumen has no cache store.", ("**Cache:**",)),
    ("Settings live in /etc/lumen/settings.json with mode 0600.", ("**Settings:**",)),
    ("On 2026-03-12 Lumen ignored the `<Kind>: <subject>` label format.", ("**Known gap:**",)),
]
TABLE_ROWS = ["| Quill | 2.4.1 | `quill run` |", "| Lumen | 0.9.0-beta.2 | `lumen exec` |"]
TABLE = "| Tool | Version | Headless |\n|---|---|---|\n" + "\n".join(TABLE_ROWS)
CHECKED = "Checked on 2026-03-14 against Quill 2.4.1 and Lumen 0.9.0-beta.2."
FENCE = (
    '```bash\n# quill\nquill run "<q>" --json --set cache.enabled=false\n'
    '# lumen\nlumen exec "<q>" --format jsonl --read-only\n```'
)
INTRO = "Copy-paste commands for both tools:"
USAGE = "Two checks per day keep usage under 2,500 tokens. Docs: https://example.com/quill-lumen."
CACHE_OFF = QUILL[2][0]

Pages = dict[str, tuple[str, list[dict] | None]]


def good_pages() -> Pages:
    return {
        "quill": (
            page("Quill", *(t for t, _ in QUILL)),
            [claim(f"c{i}", t, *m) for i, (t, m) in enumerate(QUILL, 1)],
        ),
        "lumen": (
            page("Lumen", *(t for t, _ in LUMEN)),
            [claim(f"l{i}", t, *m) for i, (t, m) in enumerate(LUMEN, 1)],
        ),
        "tools": (
            page("Tool versions", CHECKED, TABLE),
            [
                claim("t1", CHECKED, "How one script drives"),
                claim("t2", TABLE_ROWS[0], "| Tool | Version"),
                claim("t3", TABLE_ROWS[1], "| Tool | Version"),
            ],
        ),
        "checks": (
            page("Read-only checks", INTRO, FENCE, USAGE),
            [claim("k1", INTRO, "```bash"), claim("k2", USAGE, "Two checks per day")],
        ),
    }


def default_unassigned() -> list[str]:
    dups = [s.id for s in source_spans() if s.text == "- **Duplicate:** keep this line."]
    return [sid("> Re-check versions"), *dups]


def write_set(
    root: Path, pages: Pages, unassigned: list[str] | None, omitted: dict | None = None
) -> Path:
    d = root / "pages"
    d.mkdir(parents=True)
    for slug, (body, claims) in pages.items():
        (d / f"{slug}.md").write_text(body, encoding="utf-8")
        if claims is not None:
            payload = {"claims": claims, "omitted_facts": (omitted or {}).get(slug, [])}
            (d / f"{slug}.claims.json").write_text(json.dumps(payload), encoding="utf-8")
    if unassigned is not None:
        manifest = {"unassigned_passages": unassigned}
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def run(d: Path) -> LintReport:
    return lint(SOURCE, d, ENTITIES)


def codes(report: LintReport) -> list[str]:
    return [v.code for v in report.violations]


def cli(d: Path, *extra: str, source: Path = SOURCE) -> subprocess.CompletedProcess:
    args = ["lint", "--source", str(source), "--pages", str(d), "--entities", str(ENTITIES)]
    return subprocess.run(
        [sys.executable, "-m", "cairn", *args, *extra], capture_output=True, text=True
    )


def test_good_set_is_clean(tmp_path):
    d = write_set(tmp_path, good_pages(), default_unassigned())
    report = run(d)
    assert report.violations == []
    assert report.warnings == []
    assert report.modes == dict.fromkeys(["checks", "lumen", "quill", "tools"], "cairn")
    res = cli(d)
    assert res.returncode == 0, res.stdout + res.stderr
    out = json.loads(res.stdout)
    assert {"violations", "warnings", "metrics", "modes"} <= set(out)
    assert out["violations"] == []


def test_dropped_code_line_fails_retention(tmp_path):
    pages = good_pages()
    body, claims = pages["checks"]
    pages["checks"] = (
        body.replace('# lumen\nlumen exec "<q>" --format jsonl --read-only\n', ""),
        claims,
    )
    assert 'lumen exec "<q>"' not in pages["checks"][0]
    d = write_set(tmp_path, pages, default_unassigned())
    report = run(d)
    assert codes(report) == ["retention.code_line_dropped"]
    v = report.violations[0]
    assert (v.page, v.span_id) == ("checks", sid("```bash"))
    assert cli(d).returncode == 1


def test_flag_cited_from_another_entity_fails_attribution(tmp_path):
    pages = good_pages()
    body, claims = pages["lumen"]
    pages["lumen"] = (
        body + "\n" + CACHE_OFF + "\n",
        [*claims, claim("l6", CACHE_OFF, "**Cache off:**")],
    )
    report = run(write_set(tmp_path, pages, default_unassigned()))
    assert codes(report) == ["attribution.other_entity"]
    v = report.violations[0]
    assert (v.page, v.claim_id, v.span_id) == ("lumen", "l6", sid("**Cache off:**"))
    assert "placement.candidate" in [w.code for w in report.warnings]


def test_flag_cited_from_own_section_but_absent_there_fails_grounding(tmp_path):
    pages = good_pages()
    body, claims = pages["lumen"]
    pages["lumen"] = (
        body + "\n" + CACHE_OFF + "\n",
        [*claims, claim("l6", CACHE_OFF, "**Cache:**")],
    )
    report = run(write_set(tmp_path, pages, default_unassigned()))
    assert codes(report) == ["grounding.fact_not_in_cited_passages"]
    v = report.violations[0]
    assert (v.page, v.claim_id) == ("lumen", "l6")
    assert "placement.candidate" in [w.code for w in report.warnings]


def test_claim_without_spans_fails_provenance(tmp_path):
    pages = good_pages()
    body, claims = pages["quill"]
    extra = {"id": "c6", "text": "Quill retries failed runs twice.", "passage_ids": []}
    pages["quill"] = (body + "\nQuill retries failed runs twice.\n", [*claims, extra])
    assert codes(run(write_set(tmp_path, pages, default_unassigned()))) == [
        "provenance.missing_passages"
    ]


def test_claim_absent_from_body_fails_provenance(tmp_path):
    pages = good_pages()
    body, claims = pages["quill"]
    extra = claim("c6", "Quill retries failed runs twice.", '**Invoke:** `quill run "<task>"')
    pages["quill"] = (body, [*claims, extra])
    assert codes(run(write_set(tmp_path, pages, default_unassigned()))) == [
        "provenance.claim_not_in_body"
    ]


def test_unknown_span_id_fails_provenance(tmp_path):
    pages = good_pages()
    body, claims = pages["quill"]
    extra = {
        "id": "c6",
        "text": "Quill retries failed runs twice.",
        "passage_ids": ["sp_0000000000000000"],
    }
    pages["quill"] = (body + "\nQuill retries failed runs twice.\n", [*claims, extra])
    assert codes(run(write_set(tmp_path, pages, default_unassigned()))) == [
        "provenance.unknown_passage"
    ]


def test_unaccounted_span_fails_coverage(tmp_path):
    unassigned = [i for i in default_unassigned() if i != sid("> Re-check versions")]
    report = run(write_set(tmp_path, good_pages(), unassigned))
    assert codes(report) == ["coverage.passage_unaccounted"]
    assert report.violations[0].span_id == sid("> Re-check versions")


def test_dropped_fact_fails_unless_declared_omitted(tmp_path):
    def five_minutes() -> Pages:
        pages = good_pages()
        body, claims = pages["quill"]
        for c in claims:
            c["text"] = c["text"].replace("5 min", "five minutes")
        pages["quill"] = (body.replace("5 min", "five minutes"), claims)
        return pages

    report = run(write_set(tmp_path / "a", five_minutes(), default_unassigned()))
    assert codes(report) == ["retention.fact_dropped"]
    v = report.violations[0]
    assert (v.page, v.span_id, v.fact) == ("quill", sid("**Timeout:**"), "5 min")

    timeout = span("**Timeout:**")
    five = next(f for f in extract_facts(timeout) if f.value == "5 min")
    omitted = {
        "quill": [
            {
                "fact_id": fact_id(timeout.id, five.start),
                "passage_id": timeout.id,
                "reason": "prose",
            }
        ]
    }
    report = run(write_set(tmp_path / "b", five_minutes(), default_unassigned(), omitted))
    assert report.violations == []
    assert report.metrics["cairn"]["omission_rate"] > 0


def test_duplicate_titles_warn_without_failing(tmp_path):
    pages = good_pages()
    pages["lumen-2"] = (page("Lumen", "Lumen has no cache store."), None)
    d = write_set(tmp_path, pages, default_unassigned())
    report = run(d)
    assert report.violations == []
    assert "entity.duplicate" in [w.code for w in report.warnings]
    assert report.modes["lumen-2"] == "plain"
    assert report.metrics["plain"]["duplicate_entity_rate"] > 0
    assert cli(d).returncode == 0


def test_plain_pages_are_measured_not_failed(tmp_path):
    pages: Pages = {
        "quill": (page("Quill", *(t for t, _ in QUILL)), None),
        "lumen": (page("Lumen", *(t for t, _ in LUMEN), CACHE_OFF), None),
    }
    d = write_set(tmp_path, pages, None)
    report = run(d)
    assert report.violations == []
    assert set(report.modes.values()) == {"plain"}
    candidates = [w for w in report.warnings if w.code == "placement.candidate"]
    assert [(w.page, w.fact) for w in candidates] == [
        ("lumen", "--set cache.enabled=false --set cache.write=false")
    ]
    plain = report.metrics["plain"]
    assert plain["placement_candidates"] == 1
    assert 0 < plain["retention"]["overall"] < 1
    assert report.metrics["cairn"] is None
    assert cli(d).returncode == 0


def test_metrics_shape_and_values_on_the_good_set(tmp_path):
    report = run(write_set(tmp_path, good_pages(), default_unassigned()))
    plain, cairn = report.metrics["plain"], report.metrics["cairn"]
    assert {
        "retention",
        "code_line_retention",
        "passage_coverage",
        "zero_fact_passages",
        "duplicate_entity_rate",
        "compression_ratio",
        "placement_candidates",
    } <= set(plain)
    assert {"overall", "by_kind"} <= set(plain["retention"])
    assert plain["retention"]["overall"] < 1.0  # the quote's "30 days" is on no page
    assert plain["code_line_retention"] == 1.0
    assert plain["placement_candidates"] == 0
    assert {
        "pages",
        "omission_rate",
        "passage_coverage",
        "grounding_violations",
        "attribution_violations",
        "provenance_violations",
    } <= set(cairn)
    assert (cairn["pages"], cairn["passage_coverage"], cairn["omission_rate"]) == (4, 1.0, 0.0)


def test_fail_under_thresholds(tmp_path):
    d = write_set(tmp_path, good_pages(), default_unassigned())
    assert cli(d, "--fail-under", "retention=0.99").returncode == 1
    assert cli(d, "--fail-under", "retention=0.5").returncode == 0


def test_input_errors_exit_2(tmp_path):
    bad_json = write_set(tmp_path / "a", good_pages(), default_unassigned())
    (bad_json / "quill.claims.json").write_text("{not json", encoding="utf-8")
    assert cli(bad_json).returncode == 2
    with pytest.raises(LintInputError):
        run(bad_json)

    no_claims_key = write_set(tmp_path / "b", good_pages(), default_unassigned())
    (no_claims_key / "quill.claims.json").write_text('{"omitted_facts": []}', encoding="utf-8")
    assert cli(no_claims_key).returncode == 2

    ok = write_set(tmp_path / "c", good_pages(), default_unassigned())
    assert cli(ok, "--fail-under", "bogus=1").returncode == 2
    assert cli(ok, source=tmp_path / "missing.md").returncode == 2
    assert cli(tmp_path / "no-such-dir").returncode == 2
