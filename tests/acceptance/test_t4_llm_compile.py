"""T4 acceptance tests (frozen): an LLM compile adapter behind the W3 lint gate.

Written by the orchestrator before implementation. Implementers must not edit this file.

Contract:
- `compile_note(store, note, gate, *, compose=...)`: `compose(title, new_spans)` drafts
  the claims for the note's new content spans. The extractive baseline stays the default.
- `LLMComposer(model, attempts=3)` offers the model only the new content spans and asks for
  JSON {"claims": [{"text", "span_ids"}]}. cairn assigns the claim ids ("cl_" + 16 hex chars,
  distinct) and renders the body with `render_body`; the gate alone decides what is saved, and
  it sees only the offered spans and the spans the page already cites.
- Output that is never usable (not JSON, the wrong shape, no claims, a blank claim, cut off at
  the token limit) is retried with the problem named, then the compile is "rejected" with one
  "compile.invalid_output: -: ..." violation. A provider failure is "rejected" with one
  "compile.provider_error: -: ..." violation. A rejected compile saves nothing, and the
  result keeps the reason (`gate.violations`) and the rejected page (`candidate`).
- `result.draft` says how the claims were made: composer name, model calls, tokens, latency.
- `OpenAICompatible` posts one chat completion to <base_url>/chat/completions. `from_env` reads
  <PREFIX>_BASE_URL, <PREFIX>_API_KEY and <PREFIX>_MODEL at call time and has no default URL.
  Provider errors never contain the key or the endpoint's host, not even in their cause.
- Every synthetic fixture passes the gate under the extractive baseline, so a model is never
  scored against a fixture the gate itself cannot pass.
"""

import json
import re
from pathlib import Path

import httpx
import pytest

from cairn.compiler import CONTENT_KINDS, compile_note, render_body
from cairn.gate import lint_gate
from cairn.llm import FakeModel, LLMConfigError, LLMError, OpenAICompatible, Reply, from_env
from cairn.llm_compiler import LLMComposer
from cairn.store import Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RUNBOOK = (FIXTURES / "runbook_synthetic.md").read_text(encoding="utf-8")
NOTE = """\
# Drift sync

Drift 1.8.2 mirrors two buckets; checked on 2026-05-04.

- **Run:** `drift sync --src ./a --dst ./b`.
- **Dry run:** add --dry-run to list changes first.
- **Limit:** at most 500 files per batch, about 2 GB.

```bash
drift sync --src ./a --dst ./b --dry-run
drift sync --src ./a --dst ./b
```

> Keep `--delete` off until the dry run is clean.
"""
UPDATE = "# Drift sync\n\n- **Retry:** pass `--retries 3` on flaky links.\n"
CLAIM_ID = re.compile(r"cl_[0-9a-f]{16}")
BASE_URL = "https://llm.example.net/v1"
HOST = "llm.example.net"
KEY = "sk-test-0123456789abcdef"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def note_of(store, text, project="alpha"):
    b = store.get_project(project) or store.create_project(project)
    note, _ = store.add_note(b.id, text)
    return b, note, [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]


def reply(*claims) -> str:
    """The model's JSON for (text, cited spans) pairs."""
    return json.dumps(
        {"claims": [{"text": text, "span_ids": [s.id for s in cited]} for text, cited in claims]}
    )


def synthesized(spans) -> list:
    """A real synthesis of NOTE: reworded prose, two spans merged, every fact kept verbatim."""
    intro, run, dry, limit, code, quote = spans
    return [
        ("Drift 1.8.2 copies one bucket into another (verified 2026-05-04).", [intro]),
        (
            "Sync with `drift sync --src ./a --dst ./b`; pass --dry-run first to preview "
            "the changes.",
            [run, dry],
        ),
        ("Batches hold up to 500 files, roughly 2 GB.", [limit]),
        (
            "Preview, then apply:\n\n```bash\ndrift sync --src ./a --dst ./b --dry-run\n"
            "drift sync --src ./a --dst ./b\n```",
            [code],
        ),
        ("Leave `--delete` off until a dry run comes back clean.", [quote]),
    ]


def compose_with(model, **kwargs):
    return {"gate": lint_gate, "compose": LLMComposer(model, **kwargs)}


def prompt(messages) -> str:
    return "\n".join(message["content"] for message in messages)


# The compile adapter


def test_a_reworded_merged_page_that_keeps_every_fact_is_saved(store):
    project, note, spans = note_of(store, NOTE)
    claims = synthesized(spans)
    model = FakeModel(
        [Reply(text=reply(*claims), prompt_tokens=420, completion_tokens=180)], name="fake-model"
    )
    result = compile_note(store, note, **compose_with(model))
    assert (result.status, result.version) == ("saved", 1), result.gate.violations
    page = store.get_page(project.id, "drift-sync")
    assert [c.text for c in page.claims] == [text for text, _ in claims]
    assert [c.span_ids for c in page.claims] == [tuple(s.id for s in c) for _, c in claims]
    assert all(CLAIM_ID.fullmatch(c.id) for c in page.claims)
    assert len({c.id for c in page.claims}) == len(claims)
    assert page.body == render_body("Drift sync", page.claims, {s.id: s for s in spans})
    m = page.metrics
    assert (m["claims"], m["spans_cited"], m["retention"], m["code_line_retention"]) == (
        5,
        6,
        1.0,
        1.0,
    )
    assert result.draft.composer == "fake-model"
    assert (result.draft.attempts, result.draft.prompt_tokens) == (1, 420)
    assert (result.draft.completion_tokens, result.draft.latency_s >= 0) == (180, True)


def test_the_model_is_offered_every_new_content_span_and_nothing_else(store):
    _, note, spans = note_of(store, NOTE)
    model = FakeModel([reply(*synthesized(spans))])
    compile_note(store, note, **compose_with(model))
    (messages,) = model.requests
    text = prompt(messages)
    for span in spans:
        assert span.id in text and span.text in text
    heading = next(s for s in store.note_spans(note.id) if s.kind == "heading")
    assert heading.id not in text


def test_a_model_that_echoes_each_span_reproduces_the_baseline_on_the_runbook(store):
    project, note, spans = note_of(store, RUNBOOK)
    model = FakeModel([reply(*((s.text, [s]) for s in spans))])
    result = compile_note(store, note, **compose_with(model))
    assert result.status == "saved", result.gate.violations
    page = store.get_page(project.id, "quill-and-lumen-runbook")
    assert [c.text for c in page.claims] == [s.text for s in spans]
    assert len({c.id for c in page.claims}) == len(spans)  # the two identical bullets too
    m = page.metrics
    assert (m["retention"], m["code_line_retention"], m["span_coverage"]) == (1.0, 1.0, 1.0)


GATE_FAILURES = {
    "ungrounded flag": (
        1,
        "Sync with `drift sync --src ./a --dst ./b`; pass --dry-run and --force first.",
        "grounding.fact_not_in_cited_passages",
    ),
    "dropped command line": (
        3,
        "Apply:\n\n```bash\ndrift sync --src ./a --dst ./b\n```",
        "retention.code_line_dropped",
    ),
    "unit rewritten": (
        2,
        "Batches hold up to 500 files, roughly 2 gigabytes.",
        "retention.fact_dropped",
    ),
}


@pytest.mark.parametrize(("index", "text", "code"), GATE_FAILURES.values(), ids=GATE_FAILURES)
def test_a_page_the_gate_rejects_is_not_saved_and_the_reason_is_kept(store, index, text, code):
    project, note, spans = note_of(store, NOTE)
    claims = synthesized(spans)
    claims[index] = (text, claims[index][1])
    result = compile_note(store, note, **compose_with(FakeModel([reply(*claims)])))
    assert (result.status, result.version) == ("rejected", None)
    assert store.get_page(project.id, "drift-sync") is None
    assert [v.split(": ", 1)[0] for v in result.gate.violations] == [code]
    assert result.candidate.claims[index].text == text
    if code.startswith("grounding."):
        assert result.gate.violations[0].startswith(
            f"{code}: {result.candidate.claims[index].id}: "
        )


def test_a_citation_outside_the_offered_spans_is_rejected_even_if_the_span_exists(store):
    _, _, (foreign,) = note_of(store, "# Other\n\nBeta keeps 7 days of logs.\n", project="beta")
    project, note, spans = note_of(store, NOTE)
    claims = synthesized(spans)
    claims[0] = (claims[0][0], [*claims[0][1], foreign])
    result = compile_note(store, note, **compose_with(FakeModel([reply(*claims)])))
    assert result.status == "rejected"
    assert any(
        v.startswith("provenance.unknown_passage: ") and foreign.id in v
        for v in result.gate.violations
    )
    assert store.list_pages(project.id) == []


def test_an_update_offers_only_the_new_spans_and_keeps_existing_claims_first(store):
    project, first, old = note_of(store, NOTE)
    assert compile_note(store, first, gate=lint_gate).status == "saved"
    _, second, (new,) = note_of(store, UPDATE)
    model = FakeModel([reply(("Pass `--retries 3` when links are flaky.", [new]))])
    result = compile_note(store, second, **compose_with(model))
    assert (result.status, result.version) == ("saved", 2), result.gate.violations
    text = prompt(model.requests[0])
    assert new.id in text and not any(s.id in text for s in old)
    v1, v2 = (
        store.get_page(project.id, "drift-sync", version=1),
        store.get_page(project.id, "drift-sync"),
    )
    assert v2.claims[:-1] == v1.claims
    assert v2.claims[-1].text == "Pass `--retries 3` when links are flaky."


def test_a_note_with_nothing_new_is_unchanged_and_never_calls_the_model(store):
    _, note, _ = note_of(store, NOTE)
    compile_note(store, note, gate=lint_gate)
    model = FakeModel([])
    result = compile_note(store, note, **compose_with(model))
    assert (result.status, result.version) == ("unchanged", 1)
    assert model.requests == []


def test_unusable_output_is_retried_with_the_problem_named(store):
    _, note, spans = note_of(store, NOTE)
    model = FakeModel(
        [
            Reply(text="Sure! Here are the claims.", prompt_tokens=400, completion_tokens=10),
            Reply(text=reply(*synthesized(spans)), prompt_tokens=430, completion_tokens=170),
        ]
    )
    result = compile_note(store, note, **compose_with(model))
    assert result.status == "saved", result.gate.violations
    assert (result.draft.attempts, result.draft.prompt_tokens) == (2, 830)
    assert result.draft.completion_tokens == 180
    first, second = map(prompt, model.requests)
    assert first in second and len(second) > len(first)
    assert "JSON" in second.replace(first, "", 1)


UNUSABLE = {
    "not JSON": Reply(text="I could not do that."),
    "wrong shape": Reply(text='{"claims": "none"}'),
    "no claims": Reply(text='{"claims": []}'),
    "blank claim": Reply(text='{"claims": [{"text": "  ", "span_ids": []}]}'),
    "cut off": Reply(text='{"claims": [{"text": "Drift', finish_reason="length"),
}


@pytest.mark.parametrize("bad", UNUSABLE.values(), ids=UNUSABLE)
def test_output_that_is_never_usable_is_rejected_with_a_compile_violation(store, bad):
    project, note, _ = note_of(store, NOTE)
    model = FakeModel([bad] * 3)
    result = compile_note(store, note, **compose_with(model, attempts=3))
    assert (result.status, result.version) == ("rejected", None)
    assert len(result.gate.violations) == 1
    assert result.gate.violations[0].startswith("compile.invalid_output: -: ")
    assert result.draft.attempts == len(model.requests) == 3
    assert result.candidate is None
    assert store.list_pages(project.id) == []


def test_a_provider_failure_is_rejected_with_a_compile_violation(store):
    project, note, _ = note_of(store, NOTE)
    model = FakeModel([LLMError("the endpoint answered HTTP 503")])
    result = compile_note(store, note, **compose_with(model))
    assert (result.status, result.version) == ("rejected", None)
    assert len(result.gate.violations) == 1
    assert result.gate.violations[0].startswith("compile.provider_error: -: ")
    assert "HTTP 503" in result.gate.violations[0]
    assert result.draft.attempts == 1
    assert store.list_pages(project.id) == []


def test_every_synthetic_fixture_passes_the_gate_under_the_baseline(store):
    fixtures = sorted(FIXTURES.glob("*.md"))
    assert len(fixtures) >= 5
    for path in fixtures:
        _, note, spans = note_of(store, path.read_text(encoding="utf-8"), project=path.stem)
        result = compile_note(store, note, gate=lint_gate)
        assert result.status == "saved", (path.name, result.gate.violations)
        m = result.gate.metrics
        assert (m["claims"], m["retention"], m["code_line_retention"]) == (len(spans), 1.0, 1.0)


# The OpenAI-compatible provider


def completion(content: str, finish: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish,
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        },
    )


@pytest.mark.parametrize("base_url", [BASE_URL, BASE_URL + "/"])
def test_the_provider_posts_one_chat_completion_and_reads_the_reply(base_url):
    seen = []

    def handler(request):
        seen.append(request)
        return completion('{"claims": []}')

    model = OpenAICompatible(
        base_url, "lane-a", api_key=KEY, transport=httpx.MockTransport(handler)
    )
    out = model.complete([{"role": "user", "content": "hi"}])
    assert out == Reply(
        text='{"claims": []}', prompt_tokens=12, completion_tokens=3, finish_reason="stop"
    )
    (request,) = seen
    assert (request.method, str(request.url)) == ("POST", f"{BASE_URL}/chat/completions")
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert (body["model"], body["temperature"]) == ("lane-a", 0)
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["response_format"] == {"type": "json_object"}
    assert model.name == "lane-a"


def test_from_env_reads_the_endpoint_key_and_model_at_call_time():
    seen = []

    def handler(request):
        seen.append(request)
        return completion("{}")

    env = {"LLAMASWAP_BASE_URL": BASE_URL, "LLAMASWAP_API_KEY": KEY, "LLAMASWAP_MODEL": "lane-a"}
    model = from_env(environ=env, prefix="LLAMASWAP", transport=httpx.MockTransport(handler))
    model.complete([{"role": "user", "content": "hi"}])
    assert model.name == "lane-a"
    assert str(seen[0].url) == f"{BASE_URL}/chat/completions"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert from_env("lane-b", environ=env, prefix="LLAMASWAP").name == "lane-b"


def test_from_env_has_no_default_endpoint_and_names_what_is_missing():
    with pytest.raises(LLMConfigError, match="CAIRN_LLM_BASE_URL"):
        from_env("lane-a", environ={})
    with pytest.raises(LLMConfigError, match="CAIRN_LLM_MODEL"):
        from_env(environ={"CAIRN_LLM_BASE_URL": BASE_URL})


def leaky_401(request):
    return httpx.Response(401, json={"error": {"message": f"bad key {KEY} for {request.url}"}})


def unreachable(request):
    raise httpx.ConnectError(f"cannot resolve {request.url.host}", request=request)


@pytest.mark.parametrize("handler", [leaky_401, unreachable], ids=["HTTP 401", "connect error"])
def test_provider_errors_never_show_the_key_or_the_host(handler):
    model = OpenAICompatible(
        BASE_URL, "lane-a", api_key=KEY, transport=httpx.MockTransport(handler), retries=0
    )
    with pytest.raises(LLMError) as caught:
        model.complete([{"role": "user", "content": "hi"}])
    shown = f"{caught.value} {caught.value!r} {model!r}"
    assert str(caught.value)
    assert KEY not in shown and HOST not in shown
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
