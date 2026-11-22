"""The T4 eval script, run offline: models are httpx.MockTransport handlers, never a network.

evals/t4_compile.py is a script, not a package module, so it is loaded from its path.
"""

import importlib.util
import json
import re

import httpx
import pytest

from support.paths import REPO_ROOT

REPO = REPO_ROOT
FIXTURES = REPO / "tests" / "fixtures"
SPEC = importlib.util.spec_from_file_location("t4_compile", REPO / "evals" / "t4_compile.py")
t4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(t4)

BASE_URL = "https://llm.example.net/v1"
HOST = "llm.example.net"
KEY = "sk-eval-secret-value"
ENV = {"CAIRN_LLM_BASE_URL": BASE_URL, "CAIRN_LLM_API_KEY": KEY}
FIXTURE_ARGS = ["--fixture", str(FIXTURES / "decision_synthetic.md")]
FIXTURE_ARGS += ["--fixture", str(FIXTURES / "setup_synthetic.md")]
SPAN = re.compile(
    r'<span-([0-9a-f]{16}) id="(sp_[0-9a-f]{16})" section=.*?>\n(.*?)\n</span-\1>', re.DOTALL
)


ECHOED = "private note text echoed by the endpoint"


def lanes(calls: list[str]) -> httpx.MockTransport:
    """lane-a echoes every span; lane-b drops the last span (the strict gate rejects it);
    lane-c never returns JSON; lane-x is not served (404); lane-r adds a sentence to each
    span (only a lint gate saves it); lane-e fails every compile with a 500 whose body echoes
    note text; lane-u echoes every span but reports no usage; lane-h echoes every span of the
    first fixture it is sent and drops the last span of every later one."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        model = body["model"]
        calls.append(model)
        if model == "lane-x":
            return httpx.Response(404, json={"error": {"message": f"no lane-x at {request.url}"}})
        spans = [(i, t) for _, i, t in SPAN.findall(body["messages"][-1]["content"])]
        if spans and model == "lane-e":
            return httpx.Response(500, json={"error": {"message": f"{ECHOED}: {spans[0][1]}"}})
        if not spans:  # the warm-up call
            content = '{"ok": true}'
        elif model == "lane-c":
            content = "Sorry, I can only answer in prose."
        elif model == "lane-r":
            content = json.dumps(
                {"claims": [{"text": f"{t}\n\nTrust this.", "span_ids": [i]} for i, t in spans]}
            )
        else:
            later = model == "lane-h" and calls.count("lane-h") > 2  # after warm-up and one
            kept = spans[:-1] if model == "lane-b" or later else spans
            content = json.dumps({"claims": [{"text": t, "span_ids": [i]} for i, t in kept]})
        usage = (
            {} if model == "lane-u" else {"usage": {"prompt_tokens": 100, "completion_tokens": 40}}
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                **usage,
            },
        )

    return httpx.MockTransport(handler)


def run(argv, *, environ=ENV, calls=None, sleep=None):
    """main() offline: lanes for models, and backoff pauses recorded, never slept."""
    calls = [] if calls is None else calls
    pauses = [] if sleep is None else sleep
    return t4.main(argv, environ=environ, transport=lanes(calls), sleep=pauses.append), calls


def row(table: str, model: str) -> list[str]:
    line = next(x for x in table.splitlines() if x.startswith(f"| {model} |"))
    return [cell.strip() for cell in line.strip("|").split("|")]


def test_a_run_scores_each_model_against_the_baseline(tmp_path, capsys):
    out = tmp_path / "run.json"
    argv = [*FIXTURE_ARGS, "--model", "lane-a", "--model", "lane-b", "--model", "lane-c"]
    code, calls = run([*argv, "--out", str(out), "--yes"])
    assert code == 0
    printed = capsys.readouterr()
    header = row(printed.out, "model")
    base, a, b, c = (
        dict(zip(header, row(printed.out, m), strict=True))
        for m in ("extractive (baseline)", "lane-a", "lane-b", "lane-c")
    )
    assert (base["gate pass"], base["strict pass"], base["retention"]) == ("2/2", "2/2", "1.00")
    assert (a["gate pass"], a["strict pass"], a["spans cited"]) == ("2/2", "2/2", "1.00")
    # an uncited span fails the strict gate, and the rejected draft is still scored
    assert (b["gate pass"], b["strict pass"], b["fails gate/output/provider"]) == (
        "0/2",
        "0/2",
        "2/0/0",
    )
    assert b["scored"] == "2/2" and float(b["spans cited"]) < 1.0
    assert (c["gate pass"], c["fails gate/output/provider"], c["attempts"]) == (
        "0/2",
        "0/2/0",
        "3.0",
    )
    assert a["tokens in/out"] == "100/40"
    assert calls.count("lane-a") == 3  # one warm-up and one call per fixture

    report = json.loads(out.read_text(encoding="utf-8"))
    assert [s["model"] for s in report["summary"]] == [t4.BASELINE, "lane-a", "lane-b", "lane-c"]
    assert len(report["runs"]) == 8
    assert {f["name"] for f in report["fixtures"]} == {
        "decision_synthetic.md",
        "setup_synthetic.md",
    }
    assert report["prompt_version"] and report["settings"]["env_prefix"] == "CAIRN_LLM"
    assert report["settings"]["gate"] == "verbatim_gate"
    assert report["settings"]["provider"] == "openai"
    lane_b = next(s for s in report["summary"] if s["model"] == "lane-b")
    assert (lane_b["saved"], lane_b["strict_saved"], lane_b["gate_rejected"]) == (0, 0, 2)
    assert lane_b["violation_codes"] == {"coverage.passage_unaccounted": 2}
    for shown in (printed.out, printed.err, out.read_text(encoding="utf-8")):
        assert KEY not in shown and HOST not in shown


def test_without_yes_it_only_prints_the_plan(tmp_path, capsys):
    code, calls = run([*FIXTURE_ARGS, "--model", "lane-a", "--out", str(tmp_path / "x.json")])
    printed = capsys.readouterr()
    assert (code, calls) == (0, [])
    assert "dry run" in printed.out and "lane-a" in printed.out and "--yes" in printed.out
    assert "evict" in printed.out
    assert not (tmp_path / "x.json").exists()
    assert KEY not in printed.out and HOST not in printed.out


def test_a_probe_compiles_one_fixture_per_model(tmp_path, capsys):
    out = tmp_path / "probe.json"
    code, calls = run([*FIXTURE_ARGS, "--model", "lane-a", "--probe", "--out", str(out), "--yes"])
    assert (code, calls) == (0, ["lane-a", "lane-a"])
    assert len(json.loads(out.read_text(encoding="utf-8"))["runs"]) == 2  # baseline + lane-a


def test_a_model_whose_warm_up_fails_is_skipped_and_the_exit_code_says_so(tmp_path, capsys):
    out = tmp_path / "run.json"
    code, calls = run(
        [*FIXTURE_ARGS, "--model", "lane-x", "--model", "lane-a", "--out", str(out), "--yes"]
    )
    printed = capsys.readouterr()
    assert code == 1
    assert calls.count("lane-x") == 1
    assert "lane-x" in printed.err and "HTTP 404" in printed.err and HOST not in printed.err
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["warmups"]["lane-x"] == {"error": "HTTP 404", "status": 404}
    assert "no lane-x" not in printed.err + out.read_text(encoding="utf-8")
    assert row(printed.out, "lane-a")


# T5: the verbatim gate, paired fixtures, failing runs, safe errors, honest usage


def test_drafts_meet_the_verbatim_gate_unless_another_is_named(tmp_path, capsys):
    argv = [*FIXTURE_ARGS, "--model", "lane-r", "--model", "lane-a"]
    code, _ = run([*argv, "--out", str(tmp_path / "v.json"), "--yes"])
    printed = capsys.readouterr().out
    assert code == 0
    assert row(printed, "lane-r")[2] == "0/2"  # gate pass
    report = json.loads((tmp_path / "v.json").read_text(encoding="utf-8"))
    lane_r = next(s for s in report["summary"] if s["model"] == "lane-r")
    assert list(lane_r["violation_codes"]) == ["grounding.not_verbatim"]  # one per claim

    code, _ = run([*argv, "--gate", "lint", "--out", str(tmp_path / "l.json"), "--yes"])
    report = json.loads((tmp_path / "l.json").read_text(encoding="utf-8"))
    assert (code, report["settings"]["gate"]) == (0, "lint_gate")
    assert next(s for s in report["summary"] if s["model"] == "lane-r")["saved"] == 2


def test_each_fixture_is_reported_against_its_own_baseline(tmp_path, capsys):
    argv = [*FIXTURE_ARGS, "--model", "lane-a", "--model", "lane-c"]
    code, _ = run([*argv, "--out", str(tmp_path / "run.json"), "--yes"])
    printed = capsys.readouterr().out
    assert code == 0
    report = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert [f["fixture"] for f in report["by_fixture"]] == [
        "decision_synthetic.md",
        "setup_synthetic.md",
    ]
    decision = report["by_fixture"][0]
    assert decision["baseline"] == "saved"
    assert decision["models"]["lane-a"] == {"runs": 1, "saved": 1, "outcomes": {"saved": 1}}
    assert decision["models"]["lane-c"]["outcomes"] == {"invalid_output": 1}
    per_fixture = printed.split("Per fixture", 1)[1]
    header = row(per_fixture, "fixture")
    line = dict(zip(header, row(per_fixture, "decision_synthetic.md"), strict=True))
    assert (line["baseline"], line["lane-a"], line["lane-c"]) == ("saved", "1/1", "0/1")


def test_a_fixture_the_baseline_cannot_pass_stops_the_run_before_any_model_call(tmp_path, capsys):
    empty = tmp_path / "empty.md"
    empty.write_text("# Only a heading\n", encoding="utf-8")
    out = tmp_path / "run.json"
    argv = [*FIXTURE_ARGS, "--fixture", str(empty), "--model", "lane-a", "--out", str(out)]
    code, calls = run([*argv, "--yes"])
    assert (code, calls) == (t4.BASELINE_FAILED, [])
    assert "empty.md" in capsys.readouterr().err
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["baseline_failures"] == ["empty.md"]


def test_a_run_in_which_no_model_compile_succeeded_fails(tmp_path, capsys):
    argv = [*FIXTURE_ARGS, "--model", "lane-c", "--out", str(tmp_path / "run.json")]
    code, _ = run([*argv, "--yes"])
    assert code == t4.NO_MODEL_SAVED
    assert "no model compile was saved" in capsys.readouterr().err
    code, _ = run([*FIXTURE_ARGS, "--model", "lane-x", "--out", str(tmp_path / "x.json"), "--yes"])
    assert code == t4.NO_MODEL_SAVED  # every model skipped at warm-up


def test_a_provider_failure_keeps_its_status_and_category_never_its_body(tmp_path, capsys):
    out = tmp_path / "run.json"
    argv = [*FIXTURE_ARGS, "--model", "lane-e", "--model", "lane-a", "--out", str(out)]
    code, _ = run([*argv, "--yes"])
    printed = capsys.readouterr()
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert ECHOED not in text and ECHOED not in printed.out + printed.err
    lane_e = [r for r in json.loads(text)["runs"] if r["model"] == "lane-e"]
    assert [r["outcome"] for r in lane_e] == ["provider_error", "provider_error"]
    assert lane_e[0]["provider_failure"] == {"status": 500, "category": "http_status"}
    assert lane_e[0]["violations"] == ["compile.provider_error: -: HTTP 500"]


def test_a_run_that_measured_exits_zero_however_few_drafts_were_saved(tmp_path, capsys):
    argv = [*FIXTURE_ARGS, "--model", "lane-h", "--out", str(tmp_path / "run.json"), "--yes"]
    code, _ = run(argv)
    assert code == t4.DONE  # one fixture saved, one refused: the measurement still ran


@pytest.mark.parametrize(("rate", "expected"), [("0.5", 0), ("0.6", 5), ("0", 0), ("1", 5)])
def test_fail_under_sets_a_saved_rate_per_model(tmp_path, capsys, rate, expected):
    out = str(tmp_path / "run.json")
    argv = [*FIXTURE_ARGS, "--model", "lane-a", "--model", "lane-h", "--out", out, "--yes"]
    code, _ = run([*argv, "--fail-under", rate])
    assert code == expected and t4.BELOW_THRESHOLD == 5
    err = capsys.readouterr().err
    if expected:
        assert "lane-h saved 1/2 runs, below --fail-under" in err and "lane-a saved" not in err


@pytest.mark.parametrize("rate", ["-0.1", "1.5", "half"])
def test_fail_under_is_a_rate_between_0_and_1(rate):
    with pytest.raises(SystemExit) as caught:
        run([*FIXTURE_ARGS, "--model", "lane-a", "--fail-under", rate])
    assert caught.value.code == t4.BAD_USAGE


def test_a_report_written_outside_private_is_warned_about(tmp_path, capsys):
    run([*FIXTURE_ARGS, "--model", "lane-a", "--out", str(tmp_path / "run.json")])
    assert "outside private/" in capsys.readouterr().err
    inside = t4.REPO / "private" / "t4" / "run.json"
    for argv in ([], ["--out", str(inside)]):  # dry runs: nothing is written
        run([*FIXTURE_ARGS, "--model", "lane-a", *argv])
        assert "outside private/" not in capsys.readouterr().err


def test_backoff_pauses_go_through_the_injected_sleep(tmp_path):
    pauses = []
    argv = [*FIXTURE_ARGS, "--model", "lane-e", "--out", str(tmp_path / "run.json"), "--yes"]
    run(argv, sleep=pauses)
    assert pauses == [1.0, 2.0, 1.0, 2.0]  # two retries of the 500, for each of two fixtures


@pytest.mark.parametrize(
    ("message", "failure"),
    [
        (
            "the endpoint answered HTTP 503: anything at all",
            {"status": 503, "category": "http_status"},
        ),
        (
            "could not reach the endpoint (ConnectError)",
            {"status": None, "category": "unreachable"},
        ),
        (
            "the endpoint did not answer within 30 s (ReadTimeout)",
            {"status": None, "category": "timeout"},
        ),
        (
            "the call did not finish within its 900 s deadline",
            {"status": None, "category": "deadline"},
        ),
        ("the reply is larger than 1000 bytes", {"status": None, "category": "too_large"}),
        (
            "the endpoint's reply is not a chat completion",
            {"status": None, "category": "invalid_reply"},
        ),
        ("the request failed (WriteError)", {"status": None, "category": "request_failed"}),
        ("HTTP 500 said: echoed text", {"status": None, "category": "other"}),
    ],
)
def test_provider_failures_reduce_to_a_fixed_category(message, failure):
    assert t4.provider_failure(message) == failure


def test_usage_that_was_not_reported_is_called_incomplete_not_averaged(tmp_path, capsys):
    out = tmp_path / "run.json"
    argv = [*FIXTURE_ARGS, "--model", "lane-u", "--model", "lane-a", "--out", str(out)]
    code, _ = run([*argv, "--yes"])
    printed = capsys.readouterr().out
    assert code == 0
    header = row(printed, "model")
    u, a = (dict(zip(header, row(printed, m), strict=True)) for m in ("lane-u", "lane-a"))
    assert (u["tokens in/out"], a["tokens in/out"]) == ("incomplete", "100/40")
    summary = json.loads(out.read_text(encoding="utf-8"))["summary"]
    lane_u = next(s for s in summary if s["model"] == "lane-u")
    assert (lane_u["usage_complete"], lane_u["prompt_tokens_mean"]) == (False, None)
    assert lane_u["usage_incomplete_runs"] == 2


def test_missing_configuration_is_named_before_anything_is_sent(capsys):
    code, calls = run([*FIXTURE_ARGS, "--model", "lane-a", "--yes"], environ={})
    assert (code, calls) == (2, [])
    assert "CAIRN_LLM_BASE_URL" in capsys.readouterr().err


def test_an_env_file_and_prefix_supply_the_endpoint(tmp_path, capsys):
    env_file = tmp_path / "client.env"
    env_file.write_text(
        f"LLAMASWAP_BASE_URL={BASE_URL}\nLLAMASWAP_API_KEY={KEY}\n", encoding="utf-8"
    )
    argv = [*FIXTURE_ARGS, "--model", "lane-a", "--env-file", str(env_file)]
    argv += ["--env-prefix", "LLAMASWAP", "--out", str(tmp_path / "run.json"), "--yes"]
    code, calls = run(argv, environ={})
    assert code == 0 and calls
    assert KEY not in capsys.readouterr().out


def test_models_are_required(capsys):
    with pytest.raises(SystemExit) as stop:
        t4.main([*FIXTURE_ARGS, "--yes"], environ=ENV)
    assert stop.value.code == 2


def test_retention_is_also_reported_per_fact_kind(tmp_path, capsys):
    out = tmp_path / "run.json"
    argv = ["--fixture", str(FIXTURES / "decision_synthetic.md"), "--model", "lane-b"]
    code, _ = run([*argv, "--out", str(out), "--yes"])
    assert code == t4.NO_MODEL_SAVED  # lane-b saves nothing; its drafts are still scored
    by_kind = capsys.readouterr().out.split("Retention by fact kind", 1)[1]
    header = row(by_kind, "model")
    lane_b = dict(zip(header, row(by_kind, "lane-b"), strict=True))
    assert (lane_b["url"], lane_b["number"]) == ("0.00", "1.00")  # lane-b drops the last span
    summary = json.loads(out.read_text(encoding="utf-8"))["summary"]
    assert summary[1]["retention_by_kind"]["url"] == 0.0


def test_each_run_keeps_its_draft_and_the_report_is_written_after_every_model(tmp_path, capsys):
    out = tmp_path / "run.json"
    argv = ["--fixture", str(FIXTURES / "setup_synthetic.md"), "--model", "lane-a"]
    written = []
    real_write = t4._write

    def spy(report, path):
        written.append([s["model"] for s in report["summary"]])
        real_write(report, path)

    t4._write = spy
    try:
        code, _ = run([*argv, "--model", "lane-c", "--out", str(out), "--yes"])
    finally:
        t4._write = real_write
    assert code == 0
    assert written == [[t4.BASELINE, "lane-a"], [t4.BASELINE, "lane-a", "lane-c"]]
    runs = json.loads(out.read_text(encoding="utf-8"))["runs"]
    lane_a = next(r for r in runs if r["model"] == "lane-a")
    lane_c = next(r for r in runs if r["model"] == "lane-c")
    assert lane_a["claims"] and set(lane_a["claims"][0]) == {"id", "text", "span_ids"}
    assert lane_c["claims"] == []


def test_source_scores_say_how_many_runs_they_cover(tmp_path, capsys):
    argv = [*FIXTURE_ARGS, "--model", "lane-a", "--model", "lane-c"]
    code, _ = run([*argv, "--out", str(tmp_path / "run.json"), "--yes"])
    assert code == 0
    printed = capsys.readouterr().out
    header = row(printed, "model")
    a, c = (dict(zip(header, row(printed, m), strict=True)) for m in ("lane-a", "lane-c"))
    assert (a["scored"], c["scored"]) == ("2/2", "0/2")
    assert "only the runs that produced a draft" in printed
    summary = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))["summary"]
    assert [s["scored"] for s in summary] == [2, 2, 0]


def test_completions_are_capped_by_default_and_the_report_hashes_the_code(tmp_path, capsys):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}]})

    argv = ["--fixture", str(FIXTURES / "setup_synthetic.md"), "--model", "lane-a"]
    plan_code = t4.main(argv, environ=ENV, transport=httpx.MockTransport(handler))
    assert plan_code == 0 and "8192" in capsys.readouterr().out
    out = tmp_path / "run.json"
    t4.main(
        [*argv, "--out", str(out), "--yes"], environ=ENV, transport=httpx.MockTransport(handler)
    )
    assert bodies and all(b["max_tokens"] == 8192 for b in bodies)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{64}", report["code_sha256"])
    assert report["settings"]["max_tokens"] == 8192


# --arrange (T4b): the arranger, measured on the same fixtures

CLAIM = re.compile(r' id="(cl_[0-9a-f]+)"')


def arrangers(calls: list[str]) -> httpx.MockTransport:
    """lane-a answers with the claims reversed; lane-k keeps the order; lane-c answers in
    prose; lane-u reverses with no usage reported; lane-x is not served (404)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        model = body["model"]
        calls.append(model)
        if model == "lane-x":
            return httpx.Response(404, json={"error": {"message": "no such lane"}})
        ids = CLAIM.findall(body["messages"][-1]["content"])
        if not ids:  # the warm-up call
            content = '{"ok": true}'
        elif model == "lane-c":
            content = "Here is a better order, in words."
        else:
            content = json.dumps({"order": ids if model == "lane-k" else ids[::-1], "same": []})
        usage = (
            {} if model == "lane-u" else {"usage": {"prompt_tokens": 500, "completion_tokens": 60}}
        )
        choice = {"message": {"content": content}, "finish_reason": "stop"}
        return httpx.Response(200, json={"choices": [choice], **usage})

    return httpx.MockTransport(handler)


def test_arrange_measures_how_often_each_model_answers_with_an_arrangement(tmp_path, capsys):
    out = tmp_path / "arrange.json"
    argv = [*FIXTURE_ARGS, "--arrange", "--out", str(out), "--yes"]
    for lane in ("lane-a", "lane-k", "lane-c", "lane-u"):
        argv += ["--model", lane]
    calls: list[str] = []
    code = t4.main(argv, environ=ENV, transport=arrangers(calls))
    assert code == t4.DONE
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["eval"] == "t4-arrange" and report["prompt_version"] == t4.ARRANGE_PROMPT_VERSION
    pages = [page["page"] for page in report["pages"]]
    assert pages == ["decision_synthetic.md", "setup_synthetic.md", "all fixtures, one page"]
    summary = {s["model"]: s for s in report["summary"]}
    assert (summary["lane-a"]["valid"], summary["lane-a"]["runs"]) == (3, 3)
    assert summary["lane-a"]["arranged"] == 3 and summary["lane-k"]["unchanged"] == 3
    assert (summary["lane-c"]["valid"], summary["lane-c"]["invalid"]) == (0, 3)
    assert summary["lane-c"]["attempts_mean"] == 2  # asked once more, as serve does
    assert summary["lane-a"]["prompt_tokens_mean"] == 500
    assert summary["lane-u"]["usage_complete"] is False
    assert summary["lane-u"]["prompt_tokens_mean"] is None
    run = next(r for r in report["runs"] if r["model"] == "lane-a")
    # old positions, new order: the reversal, kept to the tree (the claim with no heading
    # stays first; the others, all under one heading, reversed)
    assert run["order"] == [0, *range(run["claims"] - 1, 0, -1)] and run["claims"] == 8
    assert run["moved"] == 6 and run["latency_s"] >= 0  # all but 0 and one of the rest
    printed = capsys.readouterr().out
    assert "| lane-a | 3 | 3/3 |" in printed and "| lane-c | 3 | 0/3 |" in printed
    assert KEY not in out.read_text(encoding="utf-8") + printed


def test_arrange_plans_without_yes_and_fails_when_no_answer_was_an_arrangement(tmp_path, capsys):
    argv = [*FIXTURE_ARGS, "--arrange", "--model", "lane-c"]
    calls: list[str] = []
    assert t4.main(argv, environ=ENV, transport=arrangers(calls)) == t4.DONE
    assert calls == [] and "arranger" in capsys.readouterr().out
    out = tmp_path / "arrange.json"
    code = t4.main([*argv, "--out", str(out), "--yes"], environ=ENV, transport=arrangers(calls))
    assert code == t4.NO_MODEL_SAVED
    skipped = t4.main(
        [*FIXTURE_ARGS, "--arrange", "--model", "lane-x", "--out", str(out), "--yes"],
        environ=ENV,
        transport=arrangers(calls),
    )
    assert skipped == t4.NO_MODEL_SAVED  # its warm-up failed, so nothing was measured


# --page-claims: pages of a chosen size, since a note costs the most on a large page


def test_arrange_measures_synthetic_pages_of_the_sizes_asked_for(tmp_path, capsys):
    out = tmp_path / "arrange.json"
    argv = ["--fixture", str(FIXTURES / "decision_synthetic.md"), "--arrange", "--model", "lane-a"]
    argv += ["--page-claims", "100", "--page-claims", "300"]
    calls: list[str] = []
    assert t4.main(argv, environ=ENV, transport=arrangers(calls)) == t4.DONE
    planned = capsys.readouterr().out
    assert calls == [] and "synthetic page, 300 claims" in planned

    code = t4.main([*argv, "--out", str(out), "--yes"], environ=ENV, transport=arrangers(calls))

    assert code == t4.DONE
    report = json.loads(out.read_text(encoding="utf-8"))
    pages = {page["page"]: page for page in report["pages"]}
    assert list(pages) == [
        "decision_synthetic.md",
        "synthetic page, 100 claims",
        "synthetic page, 300 claims",
    ]
    assert pages["synthetic page, 300 claims"]["fixtures"] == []
    runs = {run["page"]: run for run in report["runs"]}
    assert runs["synthetic page, 100 claims"]["claims"] == 100
    assert runs["synthetic page, 300 claims"]["claims"] == 300
    # The largest page cairn sends is sent: not refused for its size before any call.
    assert runs["synthetic page, 300 claims"]["status"] == "arranged"
    assert calls == ["lane-a"] * 4  # the warm-up, then one call a page


def test_a_synthetic_page_is_the_same_every_time_and_grows_note_by_note():
    notes = t4.synthetic_notes(300)
    assert notes == t4.synthetic_notes(300)
    assert len(notes) >= 20  # a page that large grew over many notes
    lines = [line for note in notes for line in note.splitlines() if line.startswith("- ")]
    assert len(lines) == 300
    assert 70 <= sum(map(len, lines)) / len(lines) <= 110  # claims the size of the fixtures'
    repeated = len(lines) - len(set(lines))
    assert 1 <= repeated <= 30  # a few lines say again what an earlier note said
    headings = [line for note in notes for line in note.splitlines() if line.startswith("## ")]
    assert len(set(headings)) < len(headings) / 4  # the notes share their sections
    small = [
        line for note in t4.synthetic_notes(7) for line in note.splitlines() if line[:2] == "- "
    ]
    assert len(small) == 7  # a size that is not a whole number of notes


def test_page_claims_is_for_the_arranger_only(capsys):
    with pytest.raises(SystemExit) as stopped:
        t4.main([*FIXTURE_ARGS, "--model", "lane-a", "--page-claims", "100"], environ=ENV)
    assert stopped.value.code == 2
    assert "--page-claims needs --arrange" in capsys.readouterr().err


def test_the_arranging_report_is_on_disk_after_every_page(tmp_path):
    """A long run on a far machine can lose its connection: what was measured must not go
    with it. When a page's request arrives, the pages before it are already in the report."""
    out = tmp_path / "arrange.json"
    on_disk: list[int | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        ids = CLAIM.findall(body["messages"][-1]["content"])
        if ids:  # an arranging call, not the warm-up
            written = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
            on_disk.append(len(written["runs"]) if written else None)
        content = json.dumps({"order": ids[::-1], "same": []}) if ids else '{"ok": true}'
        usage = {"prompt_tokens": 500, "completion_tokens": 60}
        choice = {"message": {"content": content}, "finish_reason": "stop"}
        return httpx.Response(200, json={"choices": [choice], "usage": usage})

    argv = [*FIXTURE_ARGS, "--arrange", "--model", "lane-a", "--out", str(out), "--yes"]
    assert t4.main(argv, environ=ENV, transport=httpx.MockTransport(handler)) == t4.DONE

    assert on_disk == [None, 1, 2]
    report = json.loads(out.read_text(encoding="utf-8"))
    assert len(report["runs"]) == 3 and report["summary"][0]["runs"] == 3


# Anthropic's Messages API as the endpoint (T4d)

CLAUDE_ENV = {"CAIRN_LLM_PROVIDER": "anthropic", "CAIRN_LLM_API_KEY": KEY}


def claude_lane(seen: list[httpx.Request]) -> httpx.MockTransport:
    """Anthropic's API, offline: echoes every span as a claim, in its Messages format."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        spans = [(i, t) for _, i, t in SPAN.findall(body["messages"][-1]["content"])]
        claims = [{"text": t, "span_ids": [i]} for i, t in spans]
        text = json.dumps({"claims": claims}) if spans else '{"ok": true}'
        return httpx.Response(
            200,
            json={
                "type": "message",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "output_tokens": 40},
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.parametrize("no_json_mode", [False, True])
def test_the_anthropic_provider_takes_every_option_the_eval_passes(tmp_path, capsys, no_json_mode):
    seen: list[httpx.Request] = []
    argv = [*FIXTURE_ARGS, "--model", "claude-haiku-5-5", "--max-tokens", "512"]
    argv += ["--timeout", "30", "--out", str(tmp_path / "run.json"), "--yes"]
    if no_json_mode:
        argv.append("--no-json-mode")
    code = t4.main(argv, environ=CLAUDE_ENV, transport=claude_lane(seen), sleep=lambda s: None)
    assert code == t4.DONE and len(seen) == 3  # a warm-up and one call per fixture
    for request in seen:
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        body = json.loads(request.content)
        assert body["max_tokens"] == 512 and "response_format" not in body
        assert request.extensions["timeout"]["read"] == 30.0
    printed = capsys.readouterr()
    shown = dict(zip(row(printed.out, "model"), row(printed.out, "claude-haiku-5-5"), strict=True))
    assert shown["gate pass"] == "2/2" and shown["tokens in/out"] == "100/40"
    assert KEY not in printed.out + printed.err
    report = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert report["settings"]["provider"] == "anthropic"  # a report says which format it ran


def test_the_plan_says_when_anthropics_own_api_is_the_endpoint(tmp_path, capsys):
    argv = [*FIXTURE_ARGS, "--model", "claude-haiku-5-5", "--out", str(tmp_path / "x.json")]
    assert t4.main(argv, environ=CLAUDE_ENV, transport=claude_lane([])) == 0
    out = capsys.readouterr().out
    assert "CAIRN_LLM_BASE_URL (not set: Anthropic's API)" in out and KEY not in out
    assert t4.main(argv, environ=ENV, transport=lanes([])) == 0
    assert "CAIRN_LLM_BASE_URL (set)" in capsys.readouterr().out


def test_no_json_mode_says_it_has_no_effect_on_anthropics_format():
    (action,) = [a for a in t4._parser()._actions if "--no-json-mode" in a.option_strings]
    assert "no effect" in action.help and "anthropic" in action.help
