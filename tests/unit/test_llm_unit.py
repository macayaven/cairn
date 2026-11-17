"""Unit tests for cairn.llm: the inner TDD loop under tests/acceptance/test_t4_llm_compile.py.

Every HTTP call goes through httpx.MockTransport; nothing leaves the process.
"""

import gzip
import json
import threading

import httpx
import pytest

from cairn.llm import (
    DEADLINE,
    MAX_RESPONSE_BYTES,
    FakeModel,
    LLMConfigError,
    LLMError,
    OpenAICompatible,
    Reply,
    failure,
    failure_text,
    from_env,
    read_env_file,
)

BASE_URL = "https://llm.example.net/v1"
HOST = "llm.example.net"
KEY = "sk-unit-secret-value"
HI = [{"role": "user", "content": "hi"}]


def chat(content="{}", finish="stop", usage=None) -> dict:
    body = {
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}]
    }
    if usage is not None:
        body["usage"] = usage
    return body


def provider(handler, **kwargs) -> OpenAICompatible:
    kwargs.setdefault("api_key", KEY)
    return OpenAICompatible(BASE_URL, "lane-a", transport=httpx.MockTransport(handler), **kwargs)


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


# FakeModel


def test_fake_model_replays_its_script_and_records_each_request():
    boom = LLMError("down")
    fake = FakeModel(["one", Reply(text="two", prompt_tokens=5), boom], name="scripted")
    messages = [{"role": "user", "content": "hi"}]
    assert fake.complete(messages) == Reply(text="one")
    messages[0]["content"] = "changed"  # the fake keeps a copy, not the caller's list
    assert fake.complete(messages) == Reply(text="two", prompt_tokens=5)
    with pytest.raises(LLMError, match="down"):
        fake.complete(messages)
    assert [r[0]["content"] for r in fake.requests] == ["hi", "changed", "changed"]
    assert fake.name == "scripted"


def test_fake_model_fails_loudly_when_its_script_runs_out():
    with pytest.raises(AssertionError, match="no scripted reply"):
        FakeModel([]).complete(HI)


# OpenAICompatible: the request


def test_options_shape_the_request_body():
    seen, handler = recorder(chat())
    provider(handler, json_mode=False, max_tokens=512, temperature=0.2).complete(HI)
    body = json.loads(seen[0].content)
    assert "response_format" not in body
    assert (body["max_tokens"], body["temperature"]) == (512, 0.2)


def test_no_key_means_no_authorization_header():
    seen, handler = recorder(chat())
    provider(handler, api_key=None).complete(HI)
    assert "authorization" not in seen[0].headers


def test_a_base_url_must_be_http_or_https():
    with pytest.raises(LLMConfigError, match="http"):
        OpenAICompatible("ftp://llm.example.net/v1", "lane-a")


@pytest.mark.parametrize(
    "url",
    [
        "https://llm.example.net/v1",
        "http://127.0.0.1:8400/v1",
        "http://localhost:8400/v1",
        "http://[::1]:8400/v1",
    ],
)
@pytest.mark.parametrize("key", [None, KEY])
def test_a_base_url_over_https_or_on_this_machine_is_accepted(url, key):
    assert OpenAICompatible(url, "lane-a", api_key=key).name == "lane-a"


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5:8400/v1",
        "http://model.internal/v1",
        "https://ada:hunter2@llm.example.net/v1",  # credentials in the URL
    ],
)
@pytest.mark.parametrize("key", [None, KEY])
def test_a_base_url_that_would_cross_the_network_in_clear_is_refused(url, key):
    """Plain http off this machine would carry the key and the claims in clear: refused where
    the model is built, before any call, in words that name the rule and not the URL."""
    with pytest.raises(LLMConfigError) as caught:
        OpenAICompatible(url, "lane-a", api_key=key)
    said = str(caught.value)
    assert "https" in said and "localhost" in said
    for part in ("10.0.0.5", "model.internal", "ada", "hunter2", KEY):
        assert part not in said
    env = {"CAIRN_LLM_BASE_URL": url, "CAIRN_LLM_MODEL": "lane-a"}
    if key:
        env["CAIRN_LLM_API_KEY"] = key
    with pytest.raises(LLMConfigError) as caught:
        from_env(environ=env)
    said = str(caught.value)
    assert "CAIRN_LLM_BASE_URL" in said and "https" in said and "localhost" in said
    for part in ("10.0.0.5", "model.internal", "ada", "hunter2", KEY):
        assert part not in said


def test_the_repr_names_the_model_and_hides_the_endpoint_and_key():
    shown = repr(provider(recorder()[1]))
    assert "lane-a" in shown and KEY not in shown and HOST not in shown


# OpenAICompatible: the reply


def test_missing_usage_and_content_read_as_none_and_empty():
    _, handler = recorder({"choices": [{"message": {"content": None}}]})
    assert provider(handler).complete(HI) == Reply(text="")


def test_usage_is_read_when_present():
    _, handler = recorder(chat(usage={"prompt_tokens": 7, "completion_tokens": 2}))
    out = provider(handler).complete(HI)
    assert (out.prompt_tokens, out.completion_tokens, out.finish_reason) == (7, 2, "stop")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>gateway</html>"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
    ids=["not JSON", "no choices", "not an object"],
)
def test_a_reply_that_is_not_a_chat_completion_is_an_llm_error(response):
    _, handler = recorder(response)
    with pytest.raises(LLMError, match="not a chat completion"):
        provider(handler).complete(HI)


# OpenAICompatible: failures and retries


def test_transient_statuses_are_retried_with_backoff():
    pauses = []
    seen, handler = recorder(httpx.Response(503), httpx.Response(429), chat("{}"))
    out = provider(handler, retries=2, sleep=pauses.append).complete(HI)
    assert out.text == "{}"
    assert (len(seen), pauses) == (3, [1.0, 2.0])


def test_retries_run_out_with_the_last_status_named():
    seen, handler = recorder(httpx.Response(502), httpx.Response(502))
    with pytest.raises(LLMError, match="HTTP 502"):
        provider(handler, retries=1, sleep=lambda s: None).complete(HI)
    assert len(seen) == 2


def test_a_reply_and_a_failure_say_how_many_requests_the_client_sent():
    seen, handler = recorder(httpx.Response(503), httpx.Response(429), chat("{}"))
    model = provider(handler, retries=2, sleep=lambda s: None)
    assert model.max_requests == 3 and model.bounded(5).max_requests == 3
    assert model.last_requests() is None
    assert model.complete(HI).text == "{}" and model.last_requests() == 3 == len(seen)
    _, failing = recorder(httpx.Response(502), httpx.Response(502))
    with pytest.raises(LLMError) as caught:
        provider(failing, retries=1, sleep=lambda s: None).complete(HI)
    assert caught.value.requests == 2
    _, refused = recorder(httpx.Response(400))
    with pytest.raises(LLMError) as caught:
        provider(refused, retries=3, sleep=lambda s: None).complete(HI)
    assert caught.value.requests == 1
    assert LLMError("x").requests is None  # not counted


def test_a_try_that_could_not_connect_is_not_counted_as_sent():
    """A request counts as sent once it may have reached the endpoint: every try but one that
    failed to connect (ConnectError, ConnectTimeout), which never wrote a byte of it."""
    request = httpx.Request("POST", BASE_URL)
    refused = httpx.ConnectError("connection refused", request=request)
    slow = httpx.ConnectTimeout("connect timed out", request=request)
    _, handler = recorder(refused, slow, chat("{}"))
    model = provider(handler, retries=2, sleep=lambda s: None)
    assert model.complete(HI).text == "{}" and model.last_requests() == 1
    _, handler = recorder(refused, slow)
    with pytest.raises(LLMError) as caught:
        provider(handler, retries=1, sleep=lambda s: None).complete(HI)
    assert caught.value.requests == 0


@pytest.mark.parametrize(
    ("responses", "sent"),
    [
        ([httpx.ReadTimeout("read timed out")], 1),  # sent, then no answer in time
        ([httpx.RemoteProtocolError("peer closed the body early")] * 3, 3),  # cut off
        ([httpx.Response(200, content=b'{"choices": [')], 1),  # not a chat completion
        ([httpx.Response(503)] * 3, 3),  # answered with an error, each try
    ],
)
def test_a_try_that_may_have_reached_the_endpoint_is_counted_whatever_came_back(responses, sent):
    _, handler = recorder(*responses)
    with pytest.raises(LLMError) as caught:
        provider(handler, retries=2, sleep=lambda s: None).complete(HI)
    assert caught.value.requests == sent


@pytest.mark.parametrize("status", [400, 401, 404])
def test_client_errors_are_not_retried(status):
    seen, handler = recorder(httpx.Response(status, json={"error": {"message": "no such model"}}))
    with pytest.raises(LLMError) as caught:
        provider(handler, retries=3, sleep=lambda s: None).complete(HI)
    assert str(caught.value) == f"the endpoint answered HTTP {status}"  # status only, no body
    assert len(seen) == 1


def test_a_connection_failure_is_retried_then_reported_by_type_only():
    request = httpx.Request("POST", BASE_URL)
    failure = httpx.ConnectError(f"cannot connect to {HOST}", request=request)
    seen, handler = recorder(failure, failure)
    with pytest.raises(LLMError) as caught:
        provider(handler, retries=1, sleep=lambda s: None).complete(HI)
    assert "ConnectError" in str(caught.value) and HOST not in str(caught.value)
    assert len(seen) == 2


def test_a_read_timeout_is_not_retried():
    request = httpx.Request("POST", BASE_URL)
    seen, handler = recorder(httpx.ReadTimeout(f"timed out reading {HOST}", request=request))
    with pytest.raises(LLMError, match="did not answer within 30 s"):
        provider(handler, timeout=30, retries=2, sleep=lambda s: None).complete(HI)
    assert len(seen) == 1


def test_an_error_body_is_never_shown():
    echo = f"key {KEY} rejected at {BASE_URL}/chat/completions for host {HOST}: private text"
    _, handler = recorder(httpx.Response(403, json={"error": {"message": echo}}))
    with pytest.raises(LLMError) as caught:
        provider(handler).complete(HI)
    assert str(caught.value) == "the endpoint answered HTTP 403"


@pytest.mark.parametrize(
    ("message", "status", "category"),
    [
        ("the endpoint answered HTTP 503", 503, "http_status"),
        ("the endpoint answered HTTP 401: bad key sk-x for host", 401, "http_status"),
        ("could not reach the endpoint (ConnectError)", None, "unreachable"),
        ("the endpoint did not answer within 600 s (ReadTimeout)", None, "timeout"),
        ("the call did not finish within its 900 s deadline", None, "deadline"),
        ("the reply is larger than 4194304 bytes", None, "too_large"),
        ("the reply is encoded, and only an unencoded one is read", None, "encoded"),
        ("the endpoint's reply is not a chat completion", None, "invalid_reply"),
        ("the request failed (ReadError)", None, "request_failed"),
        ("a gateway's own words: private text", None, "other"),
    ],
)
def test_a_failure_reduces_to_its_status_and_a_fixed_category(message, status, category):
    assert failure(LLMError(message)) == (status, category)
    text = failure_text(LLMError(message))
    assert "private" not in text and "sk-x" not in text and "600" not in text
    assert text == (f"the endpoint answered HTTP {status}" if status else text)


# from_env and env files


def test_from_env_trims_values_skips_a_blank_key_and_passes_options():
    seen, handler = recorder(chat())
    env = {"CAIRN_LLM_BASE_URL": f" {BASE_URL} ", "CAIRN_LLM_API_KEY": " ", "CAIRN_LLM_MODEL": "m"}
    model = from_env(environ=env, transport=httpx.MockTransport(handler), json_mode=False)
    model.complete(HI)
    assert str(seen[0].url) == f"{BASE_URL}/chat/completions"
    assert "authorization" not in seen[0].headers
    assert "response_format" not in json.loads(seen[0].content)


def test_from_env_never_echoes_a_bad_base_url():
    with pytest.raises(LLMConfigError) as caught:
        from_env("m", environ={"CAIRN_LLM_BASE_URL": f"{HOST}:8400/v1"})
    assert "CAIRN_LLM_BASE_URL" in str(caught.value) and HOST not in str(caught.value)


def test_read_env_file_parses_shell_style_assignments(tmp_path):
    path = tmp_path / "client.env"
    path.write_text(
        "# llm endpoint\n\nexport LLAMASWAP_BASE_URL='https://llm.example.net/v1'\n"
        'LLAMASWAP_API_KEY="abc=def"\nPLAIN=value # not a comment\nEMPTY=\n',
        encoding="utf-8",
    )
    assert read_env_file(path) == {
        "LLAMASWAP_BASE_URL": "https://llm.example.net/v1",
        "LLAMASWAP_API_KEY": "abc=def",
        "PLAIN": "value # not a comment",
        "EMPTY": "",
    }


def test_read_env_file_names_a_bad_line_without_showing_it(tmp_path):
    path = tmp_path / "client.env"
    path.write_text(f"OK=1\n{KEY}\n", encoding="utf-8")
    with pytest.raises(LLMConfigError, match=r"client\.env:2") as caught:
        read_env_file(path)
    assert KEY not in str(caught.value)


def test_read_env_file_reports_a_missing_file(tmp_path):
    with pytest.raises(LLMConfigError, match="cannot read"):
        read_env_file(tmp_path / "absent.env")


@pytest.mark.parametrize(("timeout", "connect"), [(600, 10.0), (4, 4.0)])
def test_connecting_has_its_own_short_timeout(timeout, connect):
    seen, handler = recorder(chat())
    provider(handler, timeout=timeout).complete(HI)
    assert seen[0].extensions["timeout"] == {
        "connect": connect,
        "read": float(timeout),
        "write": float(timeout),
        "pool": float(timeout),
    }


# OpenAICompatible: one deadline per call, and a bound on what is read


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Drip(httpx.SyncByteStream):
    """A body sent in chunks, the clock moving `pause` seconds before each one."""

    def __init__(self, chunks: list[bytes], clock: FakeClock, pause: float = 0.0) -> None:
        self.chunks, self.clock, self.pause, self.sent = chunks, clock, pause, 0

    def __iter__(self):
        for chunk in self.chunks:
            self.clock.now += self.pause
            self.sent += 1
            yield chunk


SECRET_TEXT = b"private note text that must never reach an error message "


def test_defaults_bound_every_call():
    model = OpenAICompatible(BASE_URL, "m")
    assert (model.deadline, model.max_response_bytes) == (DEADLINE, MAX_RESPONSE_BYTES)
    assert 0 < DEADLINE <= 3600 and 0 < MAX_RESPONSE_BYTES <= 16 * 2**20


def test_a_slow_drip_of_bytes_cannot_outlast_the_deadline():
    clock = FakeClock()
    body = Drip([SECRET_TEXT] * 1000, clock, pause=40.0)
    _, handler = recorder(httpx.Response(200, stream=body))
    with pytest.raises(LLMError) as caught:
        provider(handler, deadline=100, clock=clock, retries=0).complete(HI)
    assert "100 s" in str(caught.value) and b"private" not in str(caught.value).encode()
    assert body.sent == 3  # stopped at the first chunk past the deadline


def test_a_reply_larger_than_the_bound_is_not_read_to_the_end():
    body = Drip([SECRET_TEXT] * 1000, FakeClock())
    _, handler = recorder(httpx.Response(200, stream=body))
    with pytest.raises(LLMError) as caught:
        provider(handler, max_response_bytes=1000).complete(HI)
    assert "1000 bytes" in str(caught.value) and "private" not in str(caught.value)
    assert body.sent < 1000 // len(SECRET_TEXT) + 2


def test_an_error_body_is_not_read():
    body = Drip([SECRET_TEXT] * 100_000, FakeClock())
    _, handler = recorder(httpx.Response(500, stream=body))
    with pytest.raises(LLMError, match="HTTP 500"):
        provider(handler, retries=0).complete(HI)
    assert body.sent == 0


def test_each_try_waits_at_most_what_is_left_of_the_deadline():
    clock = FakeClock()
    seen, handler = recorder(httpx.Response(503), chat())

    def slow(request):
        if not seen:
            clock.now += 69.0  # the first answer takes 69 s
        return handler(request)

    model = OpenAICompatible(
        BASE_URL,
        "m",
        timeout=600,
        deadline=100,
        clock=clock,
        sleep=clock.sleep,
        transport=httpx.MockTransport(slow),
    )
    model.complete(HI)
    first, second = (r.extensions["timeout"]["read"] for r in seen)
    assert (first, second) == (100.0, 30.0)  # 100 - 69 - 1 s of backoff


def test_a_backoff_that_would_pass_the_deadline_ends_the_call_instead():
    clock = FakeClock()
    pauses = []

    def sleep(seconds):
        pauses.append(seconds)
        clock.sleep(seconds)

    seen, handler = recorder(httpx.Response(503), httpx.Response(503), chat())
    with pytest.raises(LLMError, match=r"2\.5 s deadline"):
        provider(handler, retries=2, deadline=2.5, clock=clock, sleep=sleep).complete(HI)
    assert (len(seen), pauses) == (2, [1.0])


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
    waited = []

    def wait(done, seconds):  # the deadline comes while the request waits for its headers
        waited.append(seconds)
        assert stall.waiting.wait(5)
        return False

    model = OpenAICompatible(
        BASE_URL, "m", deadline=30, transport=stall, wait=wait, clock=FakeClock()
    )
    with pytest.raises(LLMError, match=r"within its 30 s deadline"):
        model.complete(HI)
    assert stall.cancelled.is_set()  # the request was cancelled, not left to run
    assert waited == [30.0]


def test_a_try_that_finishes_in_time_is_not_cancelled():
    _, handler = recorder(chat("ok"))
    waited = []

    def wait(done, seconds):
        waited.append(seconds)
        return done.wait(5)

    assert provider(handler, deadline=30, wait=wait, clock=FakeClock()).complete(HI).text == "ok"
    assert waited == [30.0]


# OpenAICompatible: nothing is decompressed, so the size bound is the bytes on the wire


BOMB = gzip.compress(SECRET_TEXT * 200_000)  # a small reply that would expand to ~11 MB


def test_the_request_asks_for_an_uncompressed_reply():
    seen, handler = recorder(chat("ok"))
    provider(handler).complete(HI)
    assert seen[0].headers["accept-encoding"] == "identity"


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (200, "the reply is encoded, and only an unencoded one is read"),
        (500, "the endpoint answered HTTP 500"),  # an error body is never read at all
    ],
)
def test_a_compressed_reply_is_refused_before_it_is_expanded(status, message):
    body = Drip([BOMB[i : i + 1024] for i in range(0, len(BOMB), 1024)], FakeClock())
    response = httpx.Response(status, stream=body, headers={"Content-Encoding": "gzip"})
    _, handler = recorder(response)
    with pytest.raises(LLMError) as caught:
        provider(handler, retries=0).complete(HI)
    assert str(caught.value) == message
    assert body.sent == 0  # not one byte was read


def test_an_identity_encoding_is_read_as_usual():
    response = httpx.Response(
        200, content=json.dumps(chat("ok")).encode(), headers={"Content-Encoding": "identity"}
    )
    _, handler = recorder(response)
    assert provider(handler).complete(HI).text == "ok"


# Quoting source text in a request (T4b: shared by the composer and the arranger)


def test_a_tag_is_drawn_again_while_it_occurs_in_the_text():
    from cairn.llm import BOUNDARY_DRAWS, draw_tag, new_boundary

    draws = iter(["ab", "cd", "ef"])
    assert draw_tag(["xxabxx", "cd"], lambda: next(draws)) == "ef"
    assert len(new_boundary()) == 16 and new_boundary() != new_boundary()
    with pytest.raises(ValueError, match=f"{BOUNDARY_DRAWS} draws"):
        draw_tag(["aaaa"], lambda: "a")


@pytest.mark.parametrize(
    ("reply", "body"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('<think>x {"b": 2}</think>\n {"a": 1} ', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ("<THINK>\n</think>```\n[1]\n```", "[1]"),
        ("Sure: ```json\n{}\n```", "Sure: ```json\n{}\n```"),  # a fence only when it is all
    ],
)
def test_a_reply_is_unwrapped_of_a_think_block_and_one_fence(reply, body):
    from cairn.llm import unwrap_reply

    assert unwrap_reply(reply) == body


def test_a_bounded_model_asks_for_at_most_that_many_tokens():
    seen, handler = recorder(chat(), chat(), chat())
    open_ended = provider(handler)
    bounded = open_ended.bounded(200)
    bounded.complete(HI)
    open_ended.complete(HI)  # the original is unchanged
    provider(handler, max_tokens=100).bounded(200).complete(HI)  # the tighter cap stays
    sent = [json.loads(request.content) for request in seen]
    assert sent[0]["max_tokens"] == 200 and "max_tokens" not in sent[1]
    assert sent[2]["max_tokens"] == 100
    assert bounded.name == "lane-a" and KEY not in repr(bounded)
    with pytest.raises(ValueError):
        open_ended.bounded(0)


# Extra request fields (a lane that reasons aloud is told not to, in its server's own words)

THINK_OFF = {"chat_template_kwargs": {"enable_thinking": False}}
RESERVED = [
    "model",
    "messages",
    "max_tokens",
    "max_completion_tokens",
    "temperature",
    "response_format",
    "stream",
    "n",
]


def test_extra_request_fields_go_out_with_every_request_bounded_or_not():
    seen, handler = recorder(chat(), chat())
    model = provider(handler, extra=THINK_OFF)
    model.complete(HI)
    model.bounded(64).complete(HI)
    first, second = (json.loads(request.content) for request in seen)
    assert first["chat_template_kwargs"] == {"enable_thinking": False}
    assert second["chat_template_kwargs"] == {"enable_thinking": False}
    assert (first["model"], first["temperature"], second["max_tokens"]) == ("lane-a", 0, 64)
    assert first["messages"] == HI


def test_without_extra_fields_the_request_is_what_it_was():
    seen, handler = recorder(chat())
    provider(handler).complete(HI)
    assert set(json.loads(seen[0].content)) == {
        "model",
        "messages",
        "temperature",
        "response_format",
    }


@pytest.mark.parametrize("field", RESERVED)
def test_an_extra_field_cannot_replace_what_cairn_sets(field):
    with pytest.raises(LLMConfigError) as caught:
        OpenAICompatible(BASE_URL, "lane-a", extra={field: 1})
    assert field in str(caught.value)


def test_from_env_reads_extra_request_fields_as_a_json_object():
    seen, handler = recorder(chat())
    env = {
        "CAIRN_LLM_BASE_URL": BASE_URL,
        "CAIRN_LLM_MODEL": "lane-a",
        "CAIRN_LLM_REQUEST_EXTRA": json.dumps(THINK_OFF),
    }
    from_env(environ=env, transport=httpx.MockTransport(handler)).complete(HI)
    assert json.loads(seen[0].content)["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize(
    "value",
    ["not json sk-secret", "[1, 2]", '"sk-secret"', '{"max_tokens": 5, "note": "sk-secret"}'],
    ids=["not-json", "a-list", "a-string", "a-reserved-field"],
)
def test_an_unusable_extra_setting_names_the_variable_and_never_its_value(value):
    env = {
        "CAIRN_LLM_BASE_URL": BASE_URL,
        "CAIRN_LLM_MODEL": "lane-a",
        "CAIRN_LLM_REQUEST_EXTRA": value,
    }
    with pytest.raises(LLMConfigError) as caught:
        from_env(environ=env)
    said = str(caught.value)
    assert "CAIRN_LLM_REQUEST_EXTRA" in said and "sk-secret" not in said
    assert "BASE_URL" not in said  # the address is fine: the message is about the right setting


def test_a_reserved_field_in_the_extra_setting_is_named():
    env = {
        "CAIRN_LLM_BASE_URL": BASE_URL,
        "CAIRN_LLM_MODEL": "lane-a",
        "CAIRN_LLM_REQUEST_EXTRA": '{"stream": true, "max_tokens": 5}',
    }
    with pytest.raises(LLMConfigError, match=r"CAIRN_LLM_REQUEST_EXTRA.*max_tokens, stream"):
        from_env(environ=env)
