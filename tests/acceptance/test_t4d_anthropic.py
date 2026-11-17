"""T4d acceptance tests (frozen): Anthropic's own API as a second provider.

Written by the orchestrator before implementation. Implementers must not edit this file.

Contract:
- `AnthropicMessages(model, *, api_key, base_url=ANTHROPIC_BASE_URL, ...)` is a ChatModel
  that posts one request to <base_url>/messages, with the key in `x-api-key` (no bearer
  header) and `anthropic-version: ANTHROPIC_VERSION`. cairn's system message travels as the
  top-level `system`, the other messages as `messages`. A request always carries `max_tokens`
  (DEFAULT_MAX_TOKENS unless the model was built or bounded with fewer) and never a sampling
  field or `response_format`: this API refuses them. Thinking is switched off unless the
  operator's extra request fields say otherwise, because thinking is paid from the same
  `max_tokens` as the answer.
- The reply's text is its `text` block, whatever blocks come before it; the tokens are
  `usage.input_tokens` and `usage.output_tokens`; `stop_reason` "end_turn" reads "stop" and
  "max_tokens" reads "length", the words the rest of cairn already acts on. A 200 that is
  not a message is an LLMError.
- What `OpenAICompatible` promises about a call holds here too: tries are counted
  (`max_requests`, `last_requests()`, `LLMError.requests`); 429 and 5xx are tried again, and
  so is 529; another status is not; an error never shows the key or the host; `bounded(n)`
  asks for at most n tokens.
- `from_env` builds it when <PREFIX>_PROVIDER is "anthropic": <PREFIX>_MODEL is required;
  <PREFIX>_BASE_URL is optional (Anthropic's API by default) and keeps the rule every base
  URL has, so a self-hosted install can name any endpoint that speaks this format;
  <PREFIX>_API_KEY is required for Anthropic's own API and optional for an endpoint the
  operator names (no key, no `x-api-key` header); <PREFIX>_REQUEST_EXTRA adds or replaces
  request fields, except the model, the messages, the system text, `max_tokens` and
  `stream`, and a field given as null is left out, which is how an endpoint that does not
  know `thinking` is spared it. The options callers pass today (a transport, a deadline,
  `json_mode`) are accepted. Without <PREFIX>_PROVIDER, or with "openai", nothing changes:
  an OpenAI-compatible endpoint stays the default. Any other value is an LLMConfigError
  that names the variable and the values it takes, never the value given.
- The arranging step works through it unchanged: a page is arranged, the answer is bounded,
  and the workspace pays for the tokens Anthropic reports.
"""

import json
import re

import httpx
import pytest

from cairn.arrange import Price, answer_bound, arrange_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import (
    ANTHROPIC_BASE_URL,
    ANTHROPIC_VERSION,
    DEFAULT_MAX_TOKENS,
    AnthropicMessages,
    LLMConfigError,
    LLMError,
    OpenAICompatible,
    Reply,
    from_env,
)
from cairn.store import Store

KEY = "sk-ant-test-0123456789abcdef"
MODEL = "claude-haiku-5-5"
GATEWAY = "https://gateway.example.net/anthropic/v1"
HOST = "gateway.example.net"
ENV = {"CLAUDE_PROVIDER": "anthropic", "CLAUDE_API_KEY": KEY, "CLAUDE_MODEL": MODEL}
HI = [{"role": "user", "content": "hi"}]
PRICE = Price(input_per_million=1_000_000, output_per_million=5_000_000)  # $1 and $5
FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
THINKING = {"type": "thinking", "thinking": "", "signature": "sig_0123"}


def message(text: str, stop: str = "end_turn", tokens=(12, 3), before=()) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_01",
            "type": "message",
            "role": "assistant",
            "model": MODEL,
            "content": [*before, {"type": "text", "text": text}],
            "stop_reason": stop,
            "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]},
        },
    )


def recording(*replies):
    """A transport that answers with `replies` in turn (the last one again after that) and
    the list of requests it saw."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = replies[min(len(seen), len(replies)) - 1]
        return reply(request) if callable(reply) else reply

    return httpx.MockTransport(handler), seen


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content)


def system_of(body: dict) -> str | None:
    """The system text of a request, in either form the API takes: a string or text blocks."""
    system = body.get("system")
    if isinstance(system, list):
        assert all(block["type"] == "text" for block in system)
        return "\n\n".join(block["text"] for block in system)
    return system


def quiet(seconds: float) -> None:
    """A sleep that does not wait, for the backoff between tries."""


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


# One request


def test_it_posts_one_message_request_and_reads_the_reply():
    transport, seen = recording(message('{"claims": []}'))
    model = AnthropicMessages(MODEL, api_key=KEY, transport=transport)

    out = model.complete([{"role": "system", "content": "Answer in JSON."}, *HI])
    assert out == Reply(
        text='{"claims": []}', prompt_tokens=12, completion_tokens=3, finish_reason="stop"
    )
    assert model.name == MODEL

    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == f"{ANTHROPIC_BASE_URL}/messages"
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == KEY
    assert "authorization" not in request.headers
    assert request.headers["anthropic-version"] == ANTHROPIC_VERSION
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", ANTHROPIC_VERSION)
    assert request.headers["content-type"].startswith("application/json")

    body = body_of(request)
    assert body["model"] == MODEL
    assert body["messages"] == HI  # the system message is not among them
    assert system_of(body) == "Answer in JSON."
    assert body["max_tokens"] == DEFAULT_MAX_TOKENS and DEFAULT_MAX_TOKENS > 0
    assert body["thinking"] == {"type": "disabled"}
    assert not body.get("stream")
    for refused in ("temperature", "top_p", "top_k", "response_format"):
        assert refused not in body


def test_a_request_without_a_system_message_sends_none():
    transport, seen = recording(message("{}"))
    AnthropicMessages(MODEL, api_key=KEY, transport=transport).complete(HI)
    body = body_of(seen[0])
    assert not body.get("system") and body["messages"] == HI


@pytest.mark.parametrize("base_url", [GATEWAY, GATEWAY + "/"])
def test_another_base_url_is_used_as_given(base_url):
    transport, seen = recording(message("{}"))
    AnthropicMessages(MODEL, api_key=KEY, base_url=base_url, transport=transport).complete(HI)
    assert str(seen[0].url) == f"{GATEWAY}/messages"


def test_the_answer_is_the_text_block_whatever_comes_before_it():
    transport, _ = recording(message('{"order": []}', before=[THINKING]))
    out = AnthropicMessages(MODEL, api_key=KEY, transport=transport).complete(HI)
    assert out.text == '{"order": []}'


def test_an_answer_cut_off_at_the_bound_reads_length():
    transport, _ = recording(message('{"order": [', stop="max_tokens", tokens=(900, 64)))
    out = AnthropicMessages(MODEL, api_key=KEY, transport=transport).complete(HI)
    assert out.finish_reason == "length"
    assert (out.prompt_tokens, out.completion_tokens) == (900, 64)


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, json={"type": "message", "role": "assistant"}),
        httpx.Response(200, json=["not", "a", "message"]),
        httpx.Response(200, text="<html>not json</html>"),
    ],
    ids=["no content", "not an object", "not JSON"],
)
def test_a_200_that_is_not_a_message_is_an_error(reply):
    transport, _ = recording(reply)
    model = AnthropicMessages(MODEL, api_key=KEY, transport=transport, retries=0)
    with pytest.raises(LLMError) as caught:
        model.complete(HI)
    assert caught.value.requests == 1


# The bound on the answer


def test_bounded_asks_for_at_most_that_many_tokens():
    transport, seen = recording(message("{}"))
    model = AnthropicMessages(MODEL, api_key=KEY, transport=transport)
    model.bounded(100).complete(HI)
    model.complete(HI)  # bounding makes another model; this one is as it was
    AnthropicMessages(MODEL, api_key=KEY, transport=transport, max_tokens=50).bounded(100).complete(
        HI
    )
    assert [body_of(r)["max_tokens"] for r in seen] == [100, DEFAULT_MAX_TOKENS, 50]
    assert model.bounded(100).name == MODEL


# Tries, and what an error shows


@pytest.mark.parametrize("status", [429, 500, 529])
def test_a_busy_or_failing_api_is_tried_again_and_every_try_is_counted(status):
    transport, seen = recording(httpx.Response(status, json={"type": "error"}), message("{}"))
    model = AnthropicMessages(MODEL, api_key=KEY, transport=transport, sleep=quiet)
    assert model.max_requests == 3  # one request and two more tries
    assert model.complete(HI).text == "{}"
    assert len(seen) == 2 and model.last_requests() == 2


def test_a_refused_request_is_not_tried_again():
    transport, seen = recording(httpx.Response(400, json={"type": "error"}))
    model = AnthropicMessages(MODEL, api_key=KEY, transport=transport, sleep=quiet)
    with pytest.raises(LLMError, match="400") as caught:
        model.complete(HI)
    assert len(seen) == 1 and caught.value.requests == 1


def test_a_model_without_tries_sends_one_request():
    transport, seen = recording(httpx.Response(529, json={"type": "error"}))
    model = AnthropicMessages(MODEL, api_key=KEY, transport=transport, retries=0, sleep=quiet)
    assert model.max_requests == 1
    with pytest.raises(LLMError) as caught:
        model.complete(HI)
    assert len(seen) == 1 and caught.value.requests == 1


def leaky_401(request: httpx.Request) -> httpx.Response:
    said = f"invalid x-api-key {KEY} for {request.url}"
    return httpx.Response(401, json={"type": "error", "error": {"message": said}})


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"cannot resolve {request.url.host}", request=request)


@pytest.mark.parametrize("handler", [leaky_401, unreachable], ids=["HTTP 401", "connect error"])
def test_errors_never_show_the_key_or_the_host(handler):
    model = AnthropicMessages(
        MODEL,
        api_key=KEY,
        base_url=GATEWAY,
        transport=httpx.MockTransport(handler),
        retries=0,
    )
    with pytest.raises(LLMError) as caught:
        model.complete(HI)
    shown = f"{caught.value} {caught.value!r} {model!r}"
    assert str(caught.value)
    assert KEY not in shown and HOST not in shown
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


# Choosing it from the environment


def test_from_env_builds_it_when_the_provider_says_anthropic():
    transport, seen = recording(message("{}"))
    model = from_env(environ=ENV, prefix="CLAUDE", transport=transport)
    assert isinstance(model, AnthropicMessages) and model.name == MODEL
    model.complete(HI)
    assert str(seen[0].url) == "https://api.anthropic.com/v1/messages"  # no base URL was set
    assert seen[0].headers["x-api-key"] == KEY

    there = from_env(
        environ={**ENV, "CLAUDE_BASE_URL": GATEWAY}, prefix="CLAUDE", transport=transport
    )
    there.complete(HI)
    assert str(seen[1].url) == f"{GATEWAY}/messages"
    assert from_env("claude-other", environ=ENV, prefix="CLAUDE").name == "claude-other"


@pytest.mark.parametrize("json_mode", [True, False])
def test_from_env_takes_the_options_callers_pass_today(json_mode):
    transport, seen = recording(message("{}"))
    model = from_env(
        environ=ENV, prefix="CLAUDE", transport=transport, deadline=30.0, json_mode=json_mode
    )
    model.complete(HI)
    assert model.deadline == 30.0
    assert "response_format" not in body_of(seen[0])


@pytest.mark.parametrize("missing", ["CLAUDE_API_KEY", "CLAUDE_MODEL"])
def test_from_env_names_what_is_missing(missing):
    env = {name: value for name, value in ENV.items() if name != missing}
    with pytest.raises(LLMConfigError, match=missing) as caught:
        from_env(environ=env, prefix="CLAUDE")
    assert KEY not in str(caught.value)


def test_an_endpoint_the_operator_names_needs_no_key():
    transport, seen = recording(message("{}"))
    env = {"LOCAL_PROVIDER": "anthropic", "LOCAL_MODEL": "lane-a", "LOCAL_BASE_URL": GATEWAY}
    model = from_env(environ=env, prefix="LOCAL", transport=transport)
    assert isinstance(model, AnthropicMessages)
    assert model.complete(HI).text == "{}"
    assert str(seen[0].url) == f"{GATEWAY}/messages"
    assert "x-api-key" not in seen[0].headers and "authorization" not in seen[0].headers
    assert seen[0].headers["anthropic-version"] == ANTHROPIC_VERSION

    AnthropicMessages("lane-a", base_url=GATEWAY, transport=transport).complete(HI)
    assert "x-api-key" not in seen[1].headers


def test_from_env_keeps_the_rule_for_a_base_url():
    env = {**ENV, "CLAUDE_BASE_URL": "http://gateway.example.net/anthropic/v1"}  # not https
    with pytest.raises(LLMConfigError, match="CLAUDE_BASE_URL") as caught:
        from_env(environ=env, prefix="CLAUDE")
    assert HOST not in str(caught.value)


def test_from_env_refuses_a_provider_it_does_not_know():
    with pytest.raises(LLMConfigError, match="CLAUDE_PROVIDER") as caught:
        from_env(environ={**ENV, "CLAUDE_PROVIDER": "claudius"}, prefix="CLAUDE")
    said = str(caught.value)
    assert "claudius" not in said
    assert "anthropic" in said and "openai" in said  # the values it takes


@pytest.mark.parametrize("provider", [None, "openai"])
def test_without_the_provider_or_with_openai_nothing_changes(provider):
    env = {"LANE_BASE_URL": "https://llm.example.net/v1", "LANE_MODEL": "lane-a"}
    if provider:
        env["LANE_PROVIDER"] = provider
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        choice = {"index": 0, "message": {"role": "assistant", "content": "{}"}}
        return httpx.Response(200, json={"choices": [{**choice, "finish_reason": "stop"}]})

    model = from_env(environ=env, prefix="LANE", transport=httpx.MockTransport(handler))
    assert isinstance(model, OpenAICompatible) and not isinstance(model, AnthropicMessages)
    model.complete(HI)
    assert str(seen[0].url) == "https://llm.example.net/v1/chat/completions"
    assert body_of(seen[0])["temperature"] == 0
    with pytest.raises(LLMConfigError, match="LANE_BASE_URL"):  # still no default endpoint
        from_env("lane-a", environ={"LANE_PROVIDER": provider} if provider else {}, prefix="LANE")


def test_extra_request_fields_can_switch_thinking_back_on():
    extra = {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}
    transport, seen = recording(message("{}"))
    env = {**ENV, "CLAUDE_REQUEST_EXTRA": json.dumps(extra)}
    from_env(environ=env, prefix="CLAUDE", transport=transport).complete(HI)
    body = body_of(seen[0])
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "low"}
    assert body["model"] == MODEL and body["messages"] == HI


def test_a_field_given_as_null_is_left_out():
    transport, seen = recording(message("{}"))
    env = {**ENV, "CLAUDE_REQUEST_EXTRA": json.dumps({"thinking": None})}
    from_env(environ=env, prefix="CLAUDE", transport=transport).complete(HI)
    AnthropicMessages(MODEL, api_key=KEY, extra={"thinking": None}, transport=transport).complete(
        HI
    )
    for request in seen:
        assert "thinking" not in body_of(request)
        assert body_of(request)["model"] == MODEL


@pytest.mark.parametrize("field", ["model", "messages", "system", "max_tokens", "stream"])
def test_extra_request_fields_cannot_replace_what_cairn_sends(field):
    env = {**ENV, "CLAUDE_REQUEST_EXTRA": json.dumps({field: "sk-surprise-value"})}
    with pytest.raises(LLMConfigError, match="CLAUDE_REQUEST_EXTRA") as caught:
        from_env(environ=env, prefix="CLAUDE")
    assert field in str(caught.value) and "sk-surprise-value" not in str(caught.value)
    with pytest.raises(LLMConfigError, match=field):
        AnthropicMessages(MODEL, api_key=KEY, extra={field: "x"})


# The arranging step, through it


def test_a_page_is_arranged_through_it_and_the_workspace_pays_what_anthropic_reports(store):
    project = store.create_project("alpha")
    for text in (FIRST, SECOND):
        note, _ = store.add_note(project.id, text)
        assert compile_note(store, note, gate=lint_gate).status == "saved"
    before = store.get_page(project.id, "drift-sync")
    intro, dry, delete, again, retries, limit = before.claims
    wanted = {
        "order": [intro.id, dry.id, retries.id, delete.id, limit.id],
        "same": [[intro.id, again.id]],
    }
    transport, seen = recording(message(json.dumps(wanted), tokens=(300, 40), before=[THINKING]))
    model = from_env(environ=ENV, prefix="CLAUDE", transport=transport)

    result = arrange_page(store, project.id, "drift-sync", model, price=PRICE)
    assert (result.status, result.moved, result.merged) == ("arranged", 1, 1)
    assert (result.draft.composer, result.draft.attempts) == (MODEL, 1)

    (request,) = seen
    body = body_of(request)
    assert body["max_tokens"] == answer_bound(len(before.claims))  # the answer is bounded
    assert body["thinking"] == {"type": "disabled"}
    assert system_of(body) and [m["role"] for m in body["messages"]] == ["user"]

    after = store.get_page(project.id, "drift-sync")
    assert [claim.id for claim in after.claims] == wanted["order"]
    used = store.usage()
    assert (used.model_tokens_in, used.model_tokens_out) == (300, 40)
    assert used.model_micros == 300 * 1 + 40 * 5  # $1 and $5 per million tokens
