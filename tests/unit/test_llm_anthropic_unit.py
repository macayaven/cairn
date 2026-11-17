"""Unit tests for cairn.llm.AnthropicMessages: the inner TDD loop under
tests/acceptance/test_t4d_anthropic.py, for what that file leaves open. The class shares
OpenAICompatible's transport, so the bounds of a call (the deadline, the byte bound, the
identity encoding, the unread error body, the counted tries) are asked of it here too.

Every HTTP call goes through httpx.MockTransport; nothing leaves the process.
"""

import gzip
import json
import threading

import httpx
import pytest

from cairn.arrange import arrange_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import (
    DEFAULT_MAX_TOKENS,
    AnthropicMessages,
    LLMConfigError,
    LLMError,
    OpenAICompatible,
    Reply,
    failure,
    failure_text,
    from_env,
)
from cairn.store import Store

KEY = "sk-ant-unit-secret-value"
MODEL = "claude-haiku-5-5"
GATEWAY = "https://gateway.example.net/anthropic/v1"
HOST = "gateway.example.net"
HI = [{"role": "user", "content": "hi"}]
ENV = {"CLAUDE_PROVIDER": "anthropic", "CLAUDE_API_KEY": KEY, "CLAUDE_MODEL": MODEL}
SECRET_TEXT = b"private note text that must never reach an error message "


def message(content=None, stop="end_turn", usage=None, **fields) -> dict:
    """A Messages API reply: `content` blocks (one text block "{}" by default)."""
    body = {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "text", "text": "{}"}] if content is None else content,
        "stop_reason": stop,
        **fields,
    }
    if usage is not None:
        body["usage"] = usage
    return body


def recorder(*responses):
    """A MockTransport handler that answers with `responses` in turn and records requests."""
    seen, queue = [], list(responses)

    def handler(request):
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)

    return seen, handler


def claude(handler, **kwargs) -> AnthropicMessages:
    kwargs.setdefault("api_key", KEY)
    kwargs.setdefault("base_url", GATEWAY)
    return AnthropicMessages(MODEL, transport=httpx.MockTransport(handler), **kwargs)


def sent(request: httpx.Request) -> dict:
    return json.loads(request.content)


# The request


def test_several_system_messages_are_joined_with_a_blank_line_and_the_rest_kept_in_order():
    seen, handler = recorder(message())
    talk = [
        {"role": "system", "content": "First rule."},
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "system", "content": "Second rule."},
        {"role": "user", "content": "three"},
    ]
    claude(handler).complete(talk)
    body = sent(seen[0])
    assert body["system"] == "First rule.\n\nSecond rule."
    assert body["messages"] == [talk[1], talk[2], talk[4]]


def test_the_request_holds_cairns_fields_and_no_other():
    seen, handler = recorder(message())
    claude(handler).complete([{"role": "system", "content": "s"}, *HI])
    assert set(sent(seen[0])) == {"model", "max_tokens", "messages", "system", "thinking"}
    headers = seen[0].headers
    assert headers["accept-encoding"] == "identity"
    assert headers["content-type"] == "application/json"
    assert "authorization" not in headers


@pytest.mark.parametrize("json_mode", [True, False])
def test_json_mode_changes_nothing_in_the_request(json_mode):
    seen, handler = recorder(message(), message())
    claude(handler).complete(HI)
    claude(handler, json_mode=json_mode).complete(HI)
    assert seen[0].content == seen[1].content


@pytest.mark.parametrize("max_tokens", [0, -5])
def test_a_model_built_with_no_room_for_an_answer_is_refused(max_tokens):
    with pytest.raises(LLMConfigError, match="max_tokens"):
        AnthropicMessages(MODEL, api_key=KEY, max_tokens=max_tokens)


def test_bounded_zero_is_a_value_error():
    model = AnthropicMessages(MODEL, api_key=KEY)
    with pytest.raises(ValueError):
        model.bounded(0)
    assert model.bounded(1).name == MODEL


def test_extra_fields_are_copied_not_shared():
    seen, handler = recorder(message(), message())
    extra = {"output_config": {"effort": "low"}, "thinking": None}
    model = claude(handler, extra=extra)
    extra["output_config"]["effort"] = "max"  # the caller's dict changes after the build
    extra["metadata"] = {"user_id": "u"}
    model.complete(HI)
    model.bounded(64).complete(HI)
    for request in seen:
        body = sent(request)
        assert body["output_config"] == {"effort": "low"} and "metadata" not in body
        assert "thinking" not in body
    assert extra["thinking"] is None  # leaving a null field out does not touch the caller's


def test_a_null_extra_field_is_left_out_only_for_this_dialect():
    """OpenAICompatible sends its extra fields as given, null included: nothing changed."""
    seen = []

    def handler(request):
        seen.append(request)
        choice = {"message": {"content": "{}"}, "finish_reason": "stop"}
        return httpx.Response(200, json={"choices": [choice]})

    OpenAICompatible(
        GATEWAY, "lane-a", extra={"thinking": None}, transport=httpx.MockTransport(handler)
    ).complete(HI)
    assert sent(seen[0])["thinking"] is None


def test_the_repr_names_the_model_and_hides_the_endpoint_and_key():
    shown = repr(claude(recorder()[1]))
    assert shown == f"AnthropicMessages(model={MODEL!r})"
    assert KEY not in shown and HOST not in shown


# The reply


def test_the_text_is_every_text_block_in_order_joined_with_nothing_between():
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": '{"order": '},
        {"type": "redacted_thinking", "data": "x"},
        {"type": "text", "text": "[]}"},
        "not a block",
        {"type": "text", "text": None},
        {"text": "no type"},
    ]
    _, handler = recorder(message(blocks))
    assert claude(handler).complete(HI).text == '{"order": []}'


def test_a_message_with_no_text_block_reads_as_an_empty_text():
    _, handler = recorder(message([{"type": "thinking", "thinking": "", "signature": "s"}]))
    out = claude(handler).complete(HI)
    assert out == Reply(text="", finish_reason="stop")


@pytest.mark.parametrize(
    ("usage", "tokens"),
    [
        ({"input_tokens": 12, "output_tokens": 3}, (12, 3)),
        (
            {
                "input_tokens": 12,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 1000,
                "output_tokens": 3,
                "cache_creation": {"ephemeral_5m_input_tokens": 100},
                "service_tier": "standard",
            },
            (1112, 3),
        ),
        ({"input_tokens": 12, "cache_read_input_tokens": None, "output_tokens": 3}, (12, 3)),
        ({"cache_read_input_tokens": 50, "output_tokens": 3}, (None, 3)),  # no input count
        ({"input_tokens": True, "output_tokens": -1}, (None, None)),
        ("not usage", (None, None)),
        (None, (None, None)),  # no usage at all
    ],
    ids=["plain", "cache", "null-cache", "no-input", "bad-counts", "not-an-object", "none"],
)
def test_usage_counts_every_input_token_charged(usage, tokens):
    _, handler = recorder(message(usage=usage))
    out = claude(handler).complete(HI)
    assert (out.prompt_tokens, out.completion_tokens) == tokens


@pytest.mark.parametrize(
    ("stop", "read"),
    [
        ("end_turn", "stop"),
        ("max_tokens", "length"),
        ("refusal", "refusal"),
        ("stop_sequence", "stop_sequence"),
        (None, None),
    ],
)
def test_a_stop_reason_reads_in_cairns_words_where_it_has_them(stop, read):
    _, handler = recorder(message(stop=stop))
    assert claude(handler).complete(HI).finish_reason == read


def test_a_refusal_is_a_reply_not_an_error():
    body = message([], stop="refusal", stop_details={"category": "cyber"})
    _, handler = recorder(body)
    out = claude(handler, retries=0).complete(HI)
    assert out == Reply(text="", finish_reason="refusal")


def test_an_unknown_key_in_the_message_is_ignored():
    _, handler = recorder(message(container=None, diagnostics={"x": 1}, stop_sequence=None))
    assert claude(handler).complete(HI).text == "{}"


def test_a_reply_that_is_not_a_message_is_an_error_in_fixed_words():
    _, handler = recorder(httpx.Response(200, json={"content": "a string, not blocks"}))
    with pytest.raises(LLMError) as caught:
        claude(handler).complete(HI)
    assert str(caught.value) == "the endpoint's reply is not a message"
    assert failure(caught.value) == (None, "invalid_message")
    assert failure_text(caught.value) == "the endpoint's reply is not a message"


# Tries: the same as OpenAICompatible's, plus 529 for this dialect only


def test_529_is_tried_again_here_and_not_by_the_openai_dialect():
    pauses = []
    seen, handler = recorder(httpx.Response(529), httpx.Response(503), message())
    assert claude(handler, sleep=pauses.append).complete(HI).text == "{}"
    assert (len(seen), pauses) == (3, [1.0, 2.0])

    other = []

    def overloaded(request):
        other.append(request)
        return httpx.Response(529)

    model = OpenAICompatible(GATEWAY, "lane-a", transport=httpx.MockTransport(overloaded))
    with pytest.raises(LLMError, match="HTTP 529"):
        model.complete(HI)
    assert len(other) == 1


def test_a_try_that_could_not_connect_is_not_counted_as_sent():
    request = httpx.Request("POST", GATEWAY)
    refused = httpx.ConnectError(f"connection refused by {HOST}", request=request)
    slow = httpx.ConnectTimeout("connect timed out", request=request)
    _, handler = recorder(refused, slow, message())
    model = claude(handler, sleep=lambda s: None)
    assert model.complete(HI).text == "{}" and model.last_requests() == 1
    _, handler = recorder(refused, slow)
    with pytest.raises(LLMError) as caught:
        claude(handler, retries=1, sleep=lambda s: None).complete(HI)
    assert caught.value.requests == 0 and HOST not in str(caught.value)


def test_a_read_timeout_is_not_tried_again_and_is_counted():
    request = httpx.Request("POST", GATEWAY)
    seen, handler = recorder(httpx.ReadTimeout(f"timed out reading {HOST}", request=request))
    with pytest.raises(LLMError, match="did not answer within 30 s") as caught:
        claude(handler, timeout=30, sleep=lambda s: None).complete(HI)
    assert len(seen) == 1 and caught.value.requests == 1
    assert HOST not in str(caught.value) and caught.value.__cause__ is None


# One deadline per call, and a bound on what is read


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Drip(httpx.SyncByteStream):
    """A body sent in chunks, the clock moving `pause` seconds before each one."""

    def __init__(self, chunks: list[bytes], clock: FakeClock, pause: float = 0.0) -> None:
        self.chunks, self.clock, self.pause, self.sent = chunks, clock, pause, 0

    def __iter__(self):
        for chunk in self.chunks:
            self.clock.now += self.pause
            self.sent += 1
            yield chunk


def test_a_slow_drip_of_bytes_cannot_outlast_the_deadline():
    clock = FakeClock()
    body = Drip([SECRET_TEXT] * 1000, clock, pause=40.0)
    _, handler = recorder(httpx.Response(200, stream=body))
    with pytest.raises(LLMError) as caught:
        claude(handler, deadline=100, clock=clock, retries=0).complete(HI)
    assert "100 s" in str(caught.value) and "private" not in str(caught.value)
    assert body.sent == 3  # stopped at the first chunk past the deadline


class Stall(httpx.BaseTransport):
    """A server that never finishes its headers: the request waits until it is cancelled."""

    def __init__(self) -> None:
        self.waiting, self.cancelled = threading.Event(), threading.Event()

    def handle_request(self, request):
        self.waiting.set()
        self.cancelled.wait(5)  # a safety net for a broken implementation, never reached
        raise httpx.ReadError("the connection was closed")

    def close(self) -> None:
        self.cancelled.set()


def test_the_deadline_holds_while_the_headers_never_arrive():
    stall = Stall()

    def wait(done, seconds):
        assert stall.waiting.wait(5)
        return False

    model = AnthropicMessages(
        MODEL, api_key=KEY, deadline=30, transport=stall, wait=wait, clock=FakeClock()
    )
    with pytest.raises(LLMError, match=r"within its 30 s deadline") as caught:
        model.complete(HI)
    assert stall.cancelled.is_set() and caught.value.requests == 1


def test_a_reply_larger_than_the_bound_is_not_read_to_the_end():
    body = Drip([SECRET_TEXT] * 1000, FakeClock())
    _, handler = recorder(httpx.Response(200, stream=body))
    with pytest.raises(LLMError) as caught:
        claude(handler, max_response_bytes=1000).complete(HI)
    assert str(caught.value) == "the reply is larger than 1000 bytes"
    assert body.sent < 1000 // len(SECRET_TEXT) + 2


@pytest.mark.parametrize(
    ("status", "said"),
    [
        (200, "the reply is encoded, and only an unencoded one is read"),
        (529, "the endpoint answered HTTP 529"),  # an error body is never read at all
    ],
)
def test_an_encoded_reply_and_an_error_body_are_refused_unread(status, said):
    bomb = gzip.compress(SECRET_TEXT * 200_000)
    body = Drip([bomb[i : i + 1024] for i in range(0, len(bomb), 1024)], FakeClock())
    response = httpx.Response(status, stream=body, headers={"Content-Encoding": "gzip"})
    _, handler = recorder(response)
    with pytest.raises(LLMError) as caught:
        claude(handler, retries=0).complete(HI)
    assert str(caught.value) == said
    assert body.sent == 0  # not one byte was read


# from_env


def test_from_env_passes_the_options_callers_pass_today():
    seen, handler = recorder(message())
    clock, waited = FakeClock(), []

    def wait(done, seconds):
        waited.append(seconds)
        return done.wait(5)

    model = from_env(
        environ=ENV,
        prefix="CLAUDE",
        transport=httpx.MockTransport(handler),
        deadline=40.0,
        timeout=4.0,
        retries=0,
        max_tokens=512,
        json_mode=True,
        sleep=lambda s: None,
        clock=clock,
        wait=wait,
        max_response_bytes=2048,
    )
    assert (model.deadline, model.max_requests, model.max_response_bytes) == (40.0, 1, 2048)
    model.complete(HI)
    assert sent(seen[0])["max_tokens"] == 512 and waited == [40.0]
    assert seen[0].extensions["timeout"]["read"] == 4.0


def test_by_default_a_request_asks_for_the_default_bound():
    seen, handler = recorder(message())
    from_env(environ=ENV, prefix="CLAUDE", transport=httpx.MockTransport(handler)).complete(HI)
    assert sent(seen[0])["max_tokens"] == DEFAULT_MAX_TOKENS == 16_000


@pytest.mark.parametrize("value", ["not json sk-secret", "[1, 2]", '"sk-secret"'])
def test_request_extra_that_is_not_a_json_object_names_the_variable_only(value):
    env = {**ENV, "CLAUDE_REQUEST_EXTRA": value}
    with pytest.raises(LLMConfigError) as caught:
        from_env(environ=env, prefix="CLAUDE")
    said = str(caught.value)
    assert "CLAUDE_REQUEST_EXTRA" in said and "sk-secret" not in said and KEY not in said


@pytest.mark.parametrize("provider", ["", "  ", " openai ", "OpenAI"])
def test_a_blank_provider_is_the_default_and_the_value_is_read_trimmed(provider):
    env = {"LANE_PROVIDER": provider, "LANE_BASE_URL": GATEWAY, "LANE_MODEL": "lane-a"}
    model = from_env(environ=env, prefix="LANE")
    assert type(model) is OpenAICompatible


@pytest.mark.parametrize("provider", [" anthropic ", "Anthropic"])
def test_the_anthropic_provider_is_read_trimmed_and_in_any_case(provider):
    assert isinstance(from_env(environ={**ENV, "CLAUDE_PROVIDER": provider}, prefix="CLAUDE"),
                      AnthropicMessages)  # fmt: skip


def test_a_blank_key_is_no_key():
    env = {**ENV, "CLAUDE_API_KEY": "  "}
    with pytest.raises(LLMConfigError, match="CLAUDE_API_KEY"):
        from_env(environ=env, prefix="CLAUDE")
    seen, handler = recorder(message())
    there = {**env, "CLAUDE_BASE_URL": f" {GATEWAY} "}
    from_env(environ=there, prefix="CLAUDE", transport=httpx.MockTransport(handler)).complete(HI)
    assert "x-api-key" not in seen[0].headers


def test_a_loopback_endpoint_over_http_is_accepted_and_others_are_refused_in_fixed_words():
    local = {**ENV, "CLAUDE_BASE_URL": "http://127.0.0.1:8400/v1"}
    assert isinstance(from_env(environ=local, prefix="CLAUDE"), AnthropicMessages)
    for url in ("http://10.0.0.5:8400/v1", f"https://ada:hunter2@{HOST}/v1", "ftp://x/v1"):
        with pytest.raises(LLMConfigError) as caught:
            from_env(environ={**ENV, "CLAUDE_BASE_URL": url}, prefix="CLAUDE")
        said = str(caught.value)
        assert "CLAUDE_BASE_URL" in said and "https" in said and "localhost" in said
        for part in ("10.0.0.5", "ada", "hunter2", HOST, KEY):
            assert part not in said
    with pytest.raises(LLMConfigError) as caught:
        AnthropicMessages(MODEL, api_key=KEY, base_url="http://10.0.0.5:8400/v1")
    assert "10.0.0.5" not in str(caught.value) and "https" in str(caught.value)


def test_a_bad_deadline_is_named_as_such_not_as_the_base_url():
    with pytest.raises(LLMConfigError) as caught:
        from_env(environ=ENV, prefix="CLAUDE", deadline=0)
    assert "deadline" in str(caught.value) and "BASE_URL" not in str(caught.value)


# The arranging step, through it


FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)


def test_a_refusal_leaves_the_page_invalid_and_unchanged(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        project = store.create_project("alpha")
        for text in (FIRST, SECOND):
            note, _ = store.add_note(project.id, text)
            compile_note(store, note, gate=lint_gate)
        before = store.get_page(project.id, "drift-sync")
        refused = message([], stop="refusal", usage={"input_tokens": 300, "output_tokens": 0})
        seen, handler = recorder(refused, refused)
        result = arrange_page(store, project.id, "drift-sync", claude(handler))
        assert result.status == "invalid" and len(seen) == 2  # the first call and one retry
        assert store.get_page(project.id, "drift-sync").version == before.version
        assert store.usage().model_tokens_in == 600
    finally:
        store.close()
