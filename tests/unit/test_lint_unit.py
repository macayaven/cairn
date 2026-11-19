"""Unit tests for cairn.lint (T3): the inner TDD loop under tests/acceptance/test_t3_lint.py.

Sources are tiny synthetic Markdown documents chunked with chunk(); pages are built inline.
"""

import json
import shutil
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.facts import extract_facts
from cairn.lint import (
    ClaimInput,
    Entity,
    Finding,
    LintInputError,
    OmittedFact,
    PageInput,
    fact_id,
    lint,
    lint_pages,
)
from cairn.spans import Span, chunk

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runbook_synthetic.md"


def the_span(spans: list[Span], needle: str, section: str | None = None) -> Span:
    (hit,) = [
        s
        for s in spans
        if needle in s.text
        and s.kind not in ("heading", "frontmatter")
        and (section is None or s.heading_path[-1] == section)
    ]
    return hit


def cites(cid: str, span: Span, text: str | None = None) -> ClaimInput:
    return ClaimInput(id=cid, text=span.text if text is None else text, span_ids=[span.id])


def cairn(slug: str, body: str, *claims: ClaimInput, omitted=()) -> PageInput:
    return PageInput(slug=slug, text=body, claims=list(claims), omitted_facts=list(omitted))


def plain(slug: str, body: str) -> PageInput:
    return PageInput(slug=slug, text=body)


def found(findings: list[Finding]) -> list[tuple]:
    return [(f.code, f.claim_id, f.span_id, f.fact) for f in findings]


# Matching, page bodies and titles


def by_kind(page_body: str) -> dict[str, float]:
    spans = chunk("flags", "Use --json and retry 5 times.\n")
    report = lint_pages([plain("p", page_body)], spans)
    return report.metrics["plain"]["retention"]["by_kind"]


def test_a_fact_occurs_only_as_a_whole_token():
    assert by_kind("Use --jsonl and retry 15 times.\n") == {"flag": 0.0, "number": 0.0}
    assert by_kind("USE --JSON, retry (5) times\n") == {"flag": 1.0, "number": 1.0}


def test_whitespace_case_and_thousands_separators_are_normalised():
    spans = chunk("s", "Keep usage under 2,500 tokens per Day.\n")
    report = lint_pages([plain("p", "keep usage under 2500\n   TOKENS per day\n")], spans)
    assert report.metrics["plain"]["retention"]["overall"] == 1.0


def test_the_page_body_excludes_front_matter():
    spans = chunk("s", "Pass --quiet always.\n")
    page = plain("p", "---\ntitle: Notes\nhint: --quiet\n---\n# Notes\n\nNothing.\n")
    metrics = lint_pages([page], spans).metrics["plain"]
    assert metrics["retention"]["overall"] == 0.0
    assert metrics["compression_ratio"] == len("# Notes\n\nNothing.\n") / len(
        "Pass --quiet always."
    )


def sharing_a_title(pages: list[PageInput]) -> set[str]:
    return {w.page for w in lint_pages(pages, []).warnings if w.code == "entity.duplicate"}


def test_the_title_is_the_front_matter_title_then_the_first_h1_then_the_slug():
    pages = [
        plain("a", "---\ntitle: Same\n---\n# Different\n"),
        plain("b", "## Sub\n\n# Same\n\n# Later\n"),
        plain("same", "No heading here.\n"),
        plain("d", "---\ntitle: Other\n---\n# Same\n"),
        plain("e", "```\n# Same\n```\n"),
    ]
    assert sharing_a_title(pages) == {"a", "b", "same"}


def test_titles_compare_case_folded_without_punctuation_or_a_trailing_number():
    pages = [
        plain("lumen", "# Lumen\n"),
        plain("lumen-2", "No title, so the slug.\n"),
        plain("x", "# LUMEN!\n"),
        plain("y", "# Lumen 2\n"),
    ]
    assert sharing_a_title(pages) == {"lumen", "lumen-2", "x"}
    report = lint_pages(pages, [])
    assert report.metrics["plain"]["duplicate_entity_rate"] == 0.5
    (warning,) = [w for w in report.warnings if w.page == "x"]
    assert "lumen, lumen-2" in warning.message


@pytest.mark.parametrize(
    "front_matter",
    [
        "title: [unclosed",  # not YAML
        "title: {a: 1}",  # a title that is not a scalar
        "aliases: {a: 1}",  # aliases that are neither a list nor a scalar
    ],
)
def test_unreadable_front_matter_is_an_input_error(front_matter):
    with pytest.raises(LintInputError, match="page p"):
        lint_pages([plain("p", f"---\n{front_matter}\n---\n# P\n")], [])


def test_two_pages_with_one_slug_are_an_input_error():
    with pytest.raises(LintInputError, match="duplicate page slug"):
        lint_pages([plain("a", "One.\n"), plain("a", "Two.\n")], [])


# Cairn-format guards: provenance, grounding, retention, coverage

MINI_TEXT = (
    "# Tool\n\n- Timeout: pass `--max-wait 30`; default 5 min.\n"
    "- Config at ~/.tool/config.toml.\n\n```sh\ntool run --fast\n# a comment\ntool check\n```\n"
)
MINI = chunk("mini", MINI_TEXT)
TIMEOUT = the_span(MINI, "Timeout")
CONFIG = the_span(MINI, "Config")
CODE = the_span(MINI, "tool run")


def test_provenance_problems_are_reported_once_per_claim_and_span_id():
    claims = [
        ClaimInput(
            id="c1", text="Default 5 min.", span_ids=[TIMEOUT.id, TIMEOUT.id, "sp_u", "sp_u"]
        ),
        ClaimInput(id="c2", text="Not on the page.", span_ids=[TIMEOUT.id]),
        ClaimInput(id="c3", text="Uses --made-up-flag.", span_ids=[]),
        ClaimInput(id="c4", text="Uses --other-flag.", span_ids=["sp_nope"]),
    ]
    body = (
        "# Tool\n\nDefault  5 min. Pass `--max-wait 30`.\nUses --made-up-flag. Uses --other-flag.\n"
    )
    report = lint_pages([cairn("t", body, *claims)], MINI, unassigned_spans=[CONFIG.id, CODE.id])
    # Grounding is skipped for a claim with no known span: provenance already fails it.
    assert found(report.violations) == [
        ("provenance.claim_not_in_body", "c2", None, None),
        ("provenance.missing_passages", "c3", None, None),
        ("provenance.unknown_passage", "c1", "sp_u", None),
        ("provenance.unknown_passage", "c4", "sp_nope", None),
    ]
    assert {v.page for v in report.violations} == {"t"}


def test_grounding_reports_each_ungrounded_fact_once_per_claim():
    text = "Pass `--max-wait 30` or --force, or --force again; default 5 min."
    report = lint_pages(
        [cairn("t", f"# Tool\n\n{text}\n", cites("c1", TIMEOUT, text))],
        MINI,
        unassigned_spans=[CONFIG.id, CODE.id],
    )
    assert found(report.violations) == [
        ("grounding.fact_not_in_cited_passages", "c1", None, "--force")
    ]


def test_grounding_checks_a_code_block_in_a_claim_line_by_line():
    text = "```sh\ntool run --fast\ntool explode\n```"
    body = f"# Tool\n\n{text}\n\nAlso: tool check\n"
    report = lint_pages(
        [cairn("t", body, cites("c1", CODE, text))], MINI, unassigned_spans=[TIMEOUT.id, CONFIG.id]
    )
    assert found(report.violations) == [
        ("grounding.fact_not_in_cited_passages", "c1", None, "tool explode")
    ]


def test_a_dropped_fact_is_reported_once_per_page_and_cited_span():
    body = "# Tool\n\nPass `--max-wait 30`. Also --max-wait 30 again.\n"
    claims = [
        cites("c1", TIMEOUT, "Pass `--max-wait 30`."),
        cites("c2", TIMEOUT, "Also --max-wait 30 again."),
    ]
    report = lint_pages([cairn("t", body, *claims)], MINI, unassigned_spans=[CONFIG.id, CODE.id])
    assert found(report.violations) == [("retention.fact_dropped", None, TIMEOUT.id, "5 min")]
    five = next(f for f in extract_facts(TIMEOUT) if f.value == "5 min")
    assert fact_id(TIMEOUT.id, five.start) in report.violations[0].message


def test_omitted_facts_excuse_only_the_fact_they_name():
    five = next(f for f in extract_facts(TIMEOUT) if f.value == "5 min")
    omitted = [OmittedFact(fact_id=fact_id(TIMEOUT.id, five.start), span_id=TIMEOUT.id, reason="x")]
    page = cairn(
        "t",
        "# Tool\n\nThe timeout is configurable.\n",
        cites("c1", TIMEOUT, "The timeout is configurable."),
        omitted=omitted,
    )
    report = lint_pages([page], MINI, unassigned_spans=[CONFIG.id, CODE.id])
    assert found(report.violations) == [
        ("retention.fact_dropped", None, TIMEOUT.id, "--max-wait 30")
    ]


def test_code_lines_are_retained_line_by_line_and_cannot_be_omitted():
    (block,) = extract_facts(CODE)
    page = cairn(
        "t",
        "# Tool\n\nRun the tool: tool run --fast\n",
        cites("c1", CODE, "Run the tool: tool run --fast"),
        omitted=[OmittedFact(fact_id=fact_id(CODE.id, block.start), span_id=CODE.id, reason="x")],
    )
    report = lint_pages([page], MINI, unassigned_spans=[TIMEOUT.id, CONFIG.id])
    assert found(report.violations) == [
        ("retention.code_line_dropped", None, CODE.id, "tool check")
    ]
    assert report.metrics["plain"]["code_line_retention"] == 0.5


def test_coverage_counts_content_spans_cited_by_cairn_pages_or_listed_unassigned():
    pages = [
        cairn("t", f"# Tool\n\n{TIMEOUT.text}\n", cites("c1", TIMEOUT)),
        plain("copy", f"{CONFIG.text}\n\n{CODE.text}\n"),  # plain pages never cite
    ]
    report = lint_pages(pages, MINI)
    assert found(report.violations) == [
        ("coverage.passage_unaccounted", None, s.id, None)
        for s in sorted([CONFIG, CODE], key=lambda s: s.id)
    ]
    assert {v.page for v in report.violations} == {""}
    report = lint_pages(pages, MINI, unassigned_spans=[CODE.id, "sp_not_a_span"])
    assert found(report.violations) == [("coverage.passage_unaccounted", None, CONFIG.id, None)]
    assert report.metrics["cairn"]["passage_coverage"] == 2 / 3


def test_without_cairn_pages_there_are_no_cairn_checks_or_metrics():
    report = lint_pages([plain("p", "Nothing.\n")], MINI)
    assert report.violations == []
    assert report.modes == {"p": "plain"}
    assert report.metrics["cairn"] is None


def test_findings_and_modes_do_not_depend_on_page_order():
    pages = [
        cairn("b", "# B\n\nNo claims match.\n", ClaimInput(id="x", text="Gone.", span_ids=[])),
        cairn("a", "# A\n\nGone too.\n", ClaimInput(id="y", text="Missing.", span_ids=[])),
    ]
    forward, backward = lint_pages(pages, MINI), lint_pages(pages[::-1], MINI)
    assert forward == backward
    assert list(forward.modes) == ["a", "b"]
    assert [v.page for v in forward.violations][:4] == ["", "", "", "a"]


# Entities: attribution and placement

GUIDE = chunk(
    "guide",
    """# Tools guide

Intro text.

## Alpha
- Run `--alpha-mode` for speed.
- Shared: `--shared-opt`.
- Bridge: Alpha calls Beta with `--bridge-port`.
- Short flags: `--abc` and `--abcd`.
- Binary at /opt/alpha/bin and bare --alpha-bare.

### Setup notes
- Install with `--prefix-dir`.

## Beta
- Beta prints `--verbose-log` output.
- Shared: `--shared-opt`.

## General
- Every tool accepts `--dry-run-all`.
- Beta notes: `--beta-hint`.
""",
)
ENTITIES = [Entity(name="Alpha"), Entity(name="Beta", aliases=["beta cli"])]
RUN = the_span(GUIDE, "Run `--alpha-mode`")
INSTALL = the_span(GUIDE, "Install with")
BRIDGE = the_span(GUIDE, "Bridge:")
VERBOSE = the_span(GUIDE, "Beta prints")
DRY_RUN = the_span(GUIDE, "Every tool accepts")
HINT = the_span(GUIDE, "Beta notes")
EVERY_SPAN = [s.id for s in GUIDE]


def lint_guide(*pages: PageInput):
    return lint_pages(pages, GUIDE, ENTITIES, unassigned_spans=EVERY_SPAN)


def page_citing(slug: str, title: str, *spans: Span) -> PageInput:
    claims = [cites(f"c{i}", s) for i, s in enumerate(spans, 1)]
    return cairn(slug, f"# {title}\n\n" + "\n\n".join(s.text for s in spans) + "\n", *claims)


def test_a_span_belongs_to_the_entities_of_its_deepest_heading_that_names_one():
    report = lint_guide(page_citing("beta", "Beta", RUN, INSTALL, VERBOSE, DRY_RUN))
    assert found(report.violations) == [
        ("attribution.other_entity", "c1", RUN.id, None),
        ("attribution.other_entity", "c2", INSTALL.id, None),
    ]
    assert "Alpha" in report.violations[0].message


def test_a_span_linked_to_the_page_only_by_a_text_mention_warns():
    report = lint_guide(page_citing("beta", "Beta", BRIDGE, HINT))
    assert report.violations == []
    assert found(report.warnings) == [
        ("attribution.text_mention_only", "c1", BRIDGE.id, None),
        ("attribution.text_mention_only", "c2", HINT.id, None),
    ]


def test_a_topic_page_has_no_attribution_or_placement_findings():
    report = lint_guide(page_citing("gamma", "Gamma", RUN, INSTALL))
    assert (report.violations, report.warnings) == ([], [])


def candidates(report) -> list[tuple[str, str | None]]:
    return [(w.page, w.fact) for w in report.warnings if w.code == "placement.candidate"]


def test_the_page_entity_is_a_normalised_title_or_alias():
    body = "Uses `--alpha-mode`.\n"
    report = lint_guide(
        plain("beta-cli", body),  # no title: the slug, which matches the alias "beta cli"
        plain("listed", f"---\ntitle: Listed\naliases: [Gamma, Beta CLI]\n---\n{body}"),
        plain("notes", f"---\ntitle: Notes\naliases: Beta CLI\n---\n{body}"),
        plain("x", f"# BETA-2\n\n{body}"),
        plain("y", f"# Betamax\n\n{body}"),
    )
    assert candidates(report) == [
        ("beta-cli", "--alpha-mode"),
        ("listed", "--alpha-mode"),
        ("notes", "--alpha-mode"),
        ("x", "--alpha-mode"),
    ]


def test_a_placement_candidate_sits_only_in_spans_about_one_other_entity():
    body = (
        "# Beta\n\nUse `--alpha-mode` and `--alpha-mode`, `--abc`, `--abcd`, /opt/alpha/bin, "
        "--alpha-bare, `--shared-opt`, `--dry-run-all`, `--bridge-port`, `--never-seen` and "
        "`--verbose-log`.\n"
    )
    report = lint_guide(plain("beta", body))
    assert sorted(fact for _, fact in candidates(report)) == [
        "--abcd",
        "--alpha-bare",
        "--alpha-mode",
        "/opt/alpha/bin",
    ]
    assert report.metrics["plain"]["placement_candidates"] == 4


# Metrics

METRICS = chunk(
    "m",
    "---\ntitle: Metrics source\n---\n# Metrics\n\nParagraph with --one and 5 min.\n\n"
    "- Bullet with no facts.\n\n```sh\necho one\n# comment\necho two\n```\n\n"
    "> Quote with 2.0.1 version.\n",
)


def test_plain_metrics_follow_the_documented_definitions():
    first = plain("p1", "# Metrics\n\nWe pass --one.\n\n    echo one\n")
    second = plain("p2", "---\ntitle: METRICS\n---\nNothing else.\n")
    content = sum(len(s.text) for s in METRICS if s.kind not in ("frontmatter", "heading"))
    assert lint_pages([first, second], METRICS).metrics["plain"] == {
        "retention": {"overall": 1 / 3, "by_kind": {"flag": 1.0, "number": 0.0, "version": 0.0}},
        "code_line_retention": 0.5,
        "passage_coverage": 2 / 3,
        "zero_fact_passages": 1,
        "duplicate_entity_rate": 0.5,
        "compression_ratio": (len(first.text) + len("Nothing else.\n")) / content,
        "placement_candidates": 0,
    }


def test_cairn_metrics_follow_the_documented_definitions():
    five, wait = (
        next(f for f in extract_facts(TIMEOUT) if f.value == v) for v in ("5 min", "--max-wait 30")
    )
    (path,) = extract_facts(CONFIG)
    omitted = [
        OmittedFact(fact_id=fact_id(TIMEOUT.id, five.start), span_id=TIMEOUT.id, reason="x"),
        OmittedFact(fact_id=fact_id(TIMEOUT.id, five.start), span_id=TIMEOUT.id, reason="again"),
        OmittedFact(fact_id="sp_bogus#0", span_id="sp_bogus", reason="unknown fact"),
        OmittedFact(fact_id=fact_id(CONFIG.id, path.start), span_id=CONFIG.id, reason="uncited"),
    ]
    page = cairn(
        "t", "# Tool\n\nConfigurable.\n", cites("c1", TIMEOUT, "Configurable."), omitted=omitted
    )
    report = lint_pages([page], MINI, unassigned_spans=[CONFIG.id])
    assert found(report.violations) == [
        ("coverage.passage_unaccounted", None, CODE.id, None),
        ("retention.fact_dropped", None, TIMEOUT.id, wait.value),
    ]
    assert report.metrics["cairn"] == {
        "pages": 1,
        "omission_rate": 0.5,
        "passage_coverage": 2 / 3,
        "provenance_violations": 0,
        "grounding_violations": 0,
        "attribution_violations": 0,
        "retention_violations": 1,
        "coverage_violations": 1,
    }


def test_empty_denominators_give_the_documented_defaults():
    metrics = lint_pages([cairn("t", "# T\n")], []).metrics
    assert metrics["plain"] == {
        "retention": {"overall": 1.0, "by_kind": {}},
        "code_line_retention": 1.0,
        "passage_coverage": 1.0,
        "zero_fact_passages": 0,
        "duplicate_entity_rate": 0.0,
        "compression_ratio": 0.0,
        "placement_candidates": 0,
    }
    assert metrics["cairn"]["omission_rate"] == 0.0
    assert metrics["cairn"]["passage_coverage"] == 1.0
    assert lint_pages([], []).metrics["plain"]["duplicate_entity_rate"] == 0.0


def test_a_verbatim_copy_of_the_fixture_retains_every_fact_and_code_line():
    text = FIXTURE.read_text(encoding="utf-8")
    metrics = lint_pages([plain("copy", text)], chunk(FIXTURE.stem, text)).metrics["plain"]
    assert set(metrics["retention"]["by_kind"].values()) == {1.0}
    assert (metrics["code_line_retention"], metrics["passage_coverage"]) == (1.0, 1.0)


# The file loader


def write_set(root: Path) -> tuple[Path, Path, Path]:
    """A valid input set: the MINI source, one cairn page, one plain page, a manifest and an
    entities file."""
    root.mkdir(parents=True, exist_ok=True)
    source = root / "mini.md"
    source.write_text(MINI_TEXT, encoding="utf-8")
    pages = root / "pages"
    pages.mkdir()
    (pages / "tool.md").write_text(f"---\ntitle: Tool\n---\n# Tool\n\n{TIMEOUT.text}\n", "utf-8")
    claims = [{"id": "c1", "text": TIMEOUT.text, "passage_ids": [TIMEOUT.id]}]
    (pages / "tool.claims.json").write_text(json.dumps({"claims": claims}), "utf-8")
    (pages / "notes.md").write_text("Plain notes.\n", "utf-8")
    (pages / "notes.txt").write_text("Not a page.\n", "utf-8")
    (pages / "manifest.json").write_text(json.dumps({"unassigned_passages": [CONFIG.id]}), "utf-8")
    entities = root / "entities.yaml"
    entities.write_text("entities:\n  - name: Tool\n    aliases: [the tool]\n", "utf-8")
    return source, pages, entities


def test_lint_loads_the_source_pages_sidecars_manifest_and_entities(tmp_path):
    report = lint(*write_set(tmp_path))
    expected = lint_pages(
        [
            plain("notes", "Plain notes.\n"),
            cairn(
                "tool", f"---\ntitle: Tool\n---\n# Tool\n\n{TIMEOUT.text}\n", cites("c1", TIMEOUT)
            ),
        ],
        MINI,
        [Entity(name="Tool", aliases=["the tool"])],
        [CONFIG.id],
    )
    assert report == expected
    assert report.modes == {"notes": "plain", "tool": "cairn"}
    assert found(report.violations) == [("coverage.passage_unaccounted", None, CODE.id, None)]


def test_lint_needs_neither_entities_nor_a_manifest(tmp_path):
    source, pages, _ = write_set(tmp_path)
    (pages / "manifest.json").write_text("{}", "utf-8")  # unassigned_passages defaults to []
    by_file = lint(source, pages)
    (pages / "manifest.json").unlink()
    assert lint(source, pages) == by_file
    assert sorted(v.span_id for v in by_file.violations) == sorted([CONFIG.id, CODE.id])


BROKEN = {
    "missing source": lambda source, pages, entities: source.unlink(),
    "source is a directory": lambda source, pages, entities: (source.unlink(), source.mkdir()),
    "source not UTF-8": lambda source, pages, entities: source.write_bytes(b"\xff\xfe\xfd"),
    "missing pages directory": lambda source, pages, entities: shutil.rmtree(pages),
    "pages path is a file": lambda s, pages, e: (shutil.rmtree(pages), pages.write_text("x")),
    "page not UTF-8": lambda source, pages, entities: (pages / "notes.md").write_bytes(b"\xff"),
    "page front matter not YAML": lambda s, pages, e: (pages / "notes.md").write_text(
        "---\ntitle: [x\n---\n"
    ),
    "sidecar not JSON": lambda s, pages, e: (pages / "tool.claims.json").write_text("{not json"),
    "sidecar not an object": lambda s, pages, e: (pages / "tool.claims.json").write_text("[]"),
    "sidecar without claims": lambda s, pages, e: (pages / "tool.claims.json").write_text(
        '{"omitted_facts": []}'
    ),
    "claim without passage_ids": lambda s, pages, e: (pages / "tool.claims.json").write_text(
        '{"claims": [{"id": "c1", "text": "x"}]}'
    ),
    "passage_ids not a list": lambda s, pages, e: (pages / "tool.claims.json").write_text(
        '{"claims": [{"id": "c1", "text": "x", "passage_ids": "sp_1"}]}'
    ),
    "omitted fact without a reason": lambda s, pages, e: (pages / "tool.claims.json").write_text(
        '{"claims": [], "omitted_facts": [{"fact_id": "sp_1#0", "passage_id": "sp_1"}]}'
    ),
    "manifest not JSON": lambda s, pages, e: (pages / "manifest.json").write_text("nope"),
    "manifest not an object": lambda s, pages, e: (pages / "manifest.json").write_text("[]"),
    "unassigned_passages not a list": lambda s, pages, e: (pages / "manifest.json").write_text(
        '{"unassigned_passages": "sp_1"}'
    ),
    "missing entities file": lambda source, pages, entities: entities.unlink(),
    "entities not YAML": lambda s, p, entities: entities.write_text("entities: [unclosed"),
    "entities key missing": lambda s, p, entities: entities.write_text("names: []\n"),
    "entity without a name": lambda s, p, entities: entities.write_text(
        "entities:\n  - aliases: [x]\n"
    ),
    "entity aliases not a list": lambda s, p, entities: entities.write_text(
        "entities:\n  - name: Tool\n    aliases: x\n"
    ),
}


@pytest.mark.parametrize("breakage", BROKEN.values(), ids=BROKEN.keys())
def test_lint_raises_lint_input_error_for_bad_input(tmp_path, breakage):
    source, pages, entities = write_set(tmp_path)
    lint(source, pages, entities)
    breakage(source, pages, entities)
    with pytest.raises(LintInputError):
        lint(source, pages, entities)


# The command line


def cli_set(root: Path) -> tuple[Path, Path]:
    """MINI and one plain page that copies the Timeout bullet: retention 2/3, code-line
    retention 0, span coverage 1/3."""
    root.mkdir(parents=True, exist_ok=True)
    source = root / "mini.md"
    source.write_text(MINI_TEXT, "utf-8")
    pages = root / "pages"
    pages.mkdir()
    (pages / "notes.md").write_text(f"# Notes\n\n{TIMEOUT.text}\n", "utf-8")
    return source, pages


def run_cli(capsys, source: Path, pages: Path, *extra: str) -> tuple[int, str, str]:
    code = main(["lint", "--source", str(source), "--pages", str(pages), *extra])
    out, err = capsys.readouterr()
    return code, out, err


def test_the_cli_prints_the_report_as_json_and_exits_0_when_clean(tmp_path, capsys):
    source, pages = cli_set(tmp_path)
    code, out, err = run_cli(capsys, source, pages)
    assert (code, err) == (0, "")
    assert json.loads(out) == lint(source, pages).model_dump(mode="json")


def test_the_cli_exits_1_on_violations(tmp_path, capsys):
    source, pages = cli_set(tmp_path)
    claims = {"claims": [{"id": "c1", "text": "Not in the body.", "passage_ids": [TIMEOUT.id]}]}
    (pages / "notes.claims.json").write_text(json.dumps(claims), "utf-8")
    code, out, _ = run_cli(capsys, source, pages)
    assert code == 1
    assert "provenance.claim_not_in_body" in [v["code"] for v in json.loads(out)["violations"]]


@pytest.mark.parametrize(
    ("extra", "exit_code"),
    [
        (["--fail-under", "retention=0.6", "code_line_retention=0"], 0),
        (["--fail-under", "passage_coverage=0.333"], 0),
        (["--fail-under", "retention=0.7"], 1),
        (["--fail-under", "retention=0.6", "--fail-under", "passage_coverage=0.5"], 1),
    ],
)
def test_fail_under_exits_1_when_a_plain_metric_is_below_its_threshold(
    tmp_path, capsys, extra, exit_code
):
    code, out, err = run_cli(capsys, *cli_set(tmp_path), *extra)
    assert code == exit_code
    assert json.loads(out)["violations"] == []
    assert ("below" in err) == bool(exit_code)


@pytest.mark.parametrize(
    "threshold",
    ["bogus=1", "retention", "retention=", "retention=abc", "retention=1.5", "retention=nan"],
)
def test_a_bad_threshold_exits_2_without_a_report(tmp_path, capsys, threshold):
    code, out, err = run_cli(capsys, *cli_set(tmp_path), "--fail-under", threshold)
    assert (code, out) == (2, "")
    assert "--fail-under" in err


def test_bad_input_exits_2_with_the_error_on_stderr(tmp_path, capsys):
    _, pages = cli_set(tmp_path)
    code, out, err = run_cli(capsys, tmp_path / "missing.md", pages)
    assert (code, out) == (2, "")
    assert "missing.md" in err


@pytest.mark.parametrize("argv", [[], ["lint"], ["lint", "--source", "x.md"], ["compile"]])
def test_usage_errors_exit_2(capsys, argv):
    assert main(argv) == 2
