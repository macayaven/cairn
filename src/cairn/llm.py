"""A thin LLM protocol: one chat completion per call, a scripted fake for tests, and two
providers on one transport: an OpenAI-compatible endpoint (the default) and Anthropic's
Messages API.

Contract (frozen by tests/acceptance/test_t4_llm_compile.py):
- A `ChatModel` has a `name` and `complete(messages) -> Reply`; a message is {"role",
  "content"}. A `Reply` holds the text, the prompt and completion tokens (None when the
  endpoint doesn't report them) and the finish reason.
- `OpenAICompatible(base_url, model, ...)` posts to <base_url>/chat/completions, with a bearer
  key when one is given, temperature 0 and JSON mode ({"type": "json_object"}) by default.
  Statuses 429, 500, 502, 503 and 504, connection failures and dropped connections are tried
  again `retries` times, after 1, 2, 4, ... seconds; a read timeout is not, since a model
  that is too slow stays too slow.
- `from_env` reads <PREFIX>_BASE_URL, <PREFIX>_API_KEY and <PREFIX>_MODEL when it is called.
  There is no default endpoint (unless <PREFIX>_PROVIDER is "anthropic": see "Anthropic's
  Messages API" below). The base URL is https, or http on this machine (localhost,
  127.0.0.1 or [::1]), with no credentials in it (cairn.identity's rule): anything else is
  an LLMConfigError where the model is built, before any call, in BASE_URL_RULE's words,
  never the URL's.
- Secrets: an LLMError or LLMConfigError message never holds the key, the base URL or its
  host, and never chains the httpx error, whose text embeds the URL. An error body is never
  read: an HTTP failure is "the endpoint answered HTTP <status>" and nothing more.
  `failure(error)` reduces any LLMError to (status or None, a fixed category) and
  `failure_text(error)` to a fixed sentence, so a gateway's own words never travel further.
  Building a model holds the "httpx" and "httpcore" loggers at WARNING or above for good
  (CLIENT_LOGGERS): httpcore traces every response's headers at DEBUG, and an endpoint may
  echo the key in one.

Bounds (T5; unit-tested in tests/unit/test_llm_unit.py):
- One `deadline` per complete() call (DEADLINE, 900 s, by default) covers every try, every
  backoff and every byte read: each try's timeouts are capped at what is left of it, a backoff
  that would pass it ends the call, and the body is streamed and checked against it chunk by
  chunk, so a slow drip of bytes cannot extend a call. Each try also runs on a worker thread
  that the caller waits for only until the deadline (`wait=` to inject the waiting): a server
  that drips its headers, or never sends them, is cut off then, the client closed to cancel
  the request, so the call ends at its deadline whatever the network does. A cancelled worker
  thread is abandoned, not joined. The deadline is per call: LLMComposer makes up to
  `attempts` calls for a draft, and nothing bounds a note's compile as a whole. A successful
  reply's body is read up to `max_response_bytes` (MAX_RESPONSE_BYTES, 4 MiB); a longer one
  is an LLMError, and an error body is not read at all. The bound counts bytes as sent: the
  request asks for "Accept-Encoding: identity", and a reply with any other Content-Encoding
  is an LLMError before a byte of it is read, so a small compressed reply cannot expand in
  memory. The deadline, size and encoding errors
  carry no response text.

Counting requests (T4b; unit-tested in tests/unit/test_llm_unit.py):
- `OpenAICompatible.max_requests` is the most requests one complete() sends (1 + retries).
  After a complete(), `last_requests()` on the same thread is how many requests it sent,
  and an LLMError it raises carries the same count in `requests` (None on an LLMError from
  anywhere else), so a caller can pay for every try. A try counts as sent once it may have
  reached the endpoint: every try that started, less those that failed to connect
  (httpx.ConnectError, httpx.ConnectTimeout), which never wrote a byte of the request. A
  read timeout, a dropped or cut-off reply, a reply that is not a chat completion, any HTTP
  status and a try cut off at the deadline all count: the provider may have charged for it.

Bounding the answer (T4b; unit-tested in tests/unit/test_llm_unit.py):
- `OpenAICompatible.bounded(max_tokens)` is the same model asking for at most that many
  completion tokens (the tighter of it and the model's own `max_tokens`). A caller that knows
  how long a useful answer can be (cairn.arrange) asks a model for it when the model offers
  `bounded`; the ChatModel protocol itself stays `complete(messages)`.

Extra request fields (unit-tested in tests/unit/test_llm_unit.py):
- `OpenAICompatible(..., extra={...})` sends those fields with every request, beside cairn's
  own. They are for what a server needs in its own words, such as telling a lane that reasons
  aloud not to ({"chat_template_kwargs": {"enable_thinking": false}} on llama.cpp and vLLM;
  {"reasoning_effort": "none"} elsewhere). A field cairn sets or relies on cannot be given
  (RESERVED_FIELDS: the model, the messages, the temperature, JSON mode, the token bounds,
  streaming and the number of choices): LLMConfigError, naming the field. `from_env` reads
  them from <PREFIX>_REQUEST_EXTRA, a JSON object; a value that is not one, or that names a
  reserved field, is an LLMConfigError naming the variable, never its value.

Anthropic's Messages API (T4d; frozen by tests/acceptance/test_t4d_anthropic.py,
unit-tested in tests/unit/test_llm_anthropic_unit.py):
- `AnthropicMessages(model, *, api_key=None, base_url=ANTHROPIC_BASE_URL, ...)` posts to
  <base_url>/messages with `x-api-key` (only when a key is given; never a bearer header) and
  `anthropic-version: ANTHROPIC_VERSION`. It runs on the same transport as OpenAICompatible
  (_Transport), so everything "Bounds", "Counting requests" and "Bounding the answer" and
  the secrets rule say holds for it through the same code; the dialect supplies only the
  URL, the headers, the request body, the reading of a 200 body, the statuses tried again
  and the reserved extra fields.
- The request: `model`, `max_tokens` (always: DEFAULT_MAX_TOKENS, 16,000, unless the model
  was built or bounded with fewer; fewer than 1 is an LLMConfigError), cairn's non-system
  messages as `messages` (content a string), its system messages as the top-level `system`
  string (several joined by a blank line), and `thinking: {"type": "disabled"}`, then the
  extra fields. cairn itself sends no `temperature`, `top_p`, `top_k`, `response_format`,
  `stream` or `n`: current Claude models answer 400 to a sampling field (only `stream` is
  also refused in the extra fields; the others an operator may add there at their own
  risk). Thinking is off by default because thinking tokens are paid from the same
  `max_tokens` as the answer, and cairn.arrange bounds its answer at 32 + 32 per claim.
  `json_mode` is accepted and changes nothing.
- The reply: the text is every `text` block, in order, joined with nothing between (a
  `thinking` block, or any other, is skipped; none at all reads as ""); `prompt_tokens` is
  `usage.input_tokens` plus `cache_creation_input_tokens` and `cache_read_input_tokens` when
  present (None when `input_tokens` is missing, so cairn.arrange estimates it rather than
  undercounting); `completion_tokens` is `usage.output_tokens`; `stop_reason` "end_turn"
  reads "stop", "max_tokens" reads "length", any other is kept as it is ("refusal" arrives
  with HTTP 200 and is a Reply, which cairn.arrange finds "invalid"). Unknown keys are
  ignored. A 200 that is not an object with a `content` list is an LLMError, "the endpoint's
  reply is not a message" (category "invalid_message").
- Tries: as OpenAICompatible's, plus 529 (the API's "overloaded"): ANTHROPIC_RETRY_STATUSES.
- Extra fields: ANTHROPIC_RESERVED_FIELDS (the model, the messages, the system text,
  `max_tokens` and `stream`) cannot be given. `thinking` can: {"thinking": {"type":
  "adaptive"}, "output_config": {"effort": "low"}} switches it back on for a model that
  refuses "disabled". A field given as null is left out of the request, which is how an
  endpoint that does not know `thinking` is spared it ({"thinking": null}); an
  OpenAI-compatible request still sends a null as given.
- `from_env`: <PREFIX>_PROVIDER is "openai" (the default, unset or blank) or "anthropic",
  read trimmed and in any case; another value is an LLMConfigError naming the variable and
  the two values, never the value given. For "anthropic", <PREFIX>_MODEL is required;
  <PREFIX>_BASE_URL is optional (ANTHROPIC_BASE_URL by default) under the same rule and in
  the same words as any base URL; <PREFIX>_API_KEY is required when no base URL is given,
  refused before the first call naming the variable, and optional for an endpoint the
  operator names. <PREFIX>_REQUEST_EXTRA is read as for the other dialect, under this one's
  reserved fields.

Quoting source text in a request (T4b; shared by cairn.llm_compiler and cairn.arrange):
- `new_boundary()` is 16 random hex characters. `draw_tag(texts, draw)` draws one until it
  occurs in none of `texts`, BOUNDARY_DRAWS times at most (ValueError then), so quoted text
  can neither close the tag it is wrapped in nor forge another: it would have to guess it.
- `unwrap_reply(text)` drops <think>...</think> blocks and then one code fence around the
  whole reply, and strips what is left; it does not look for JSON inside prose.
"""

import copy
import json
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, Self

import httpx
from pydantic import BaseModel, ConfigDict

from cairn.identity import _https_or_loopback

Message = Mapping[str, str]

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
CONNECT_TIMEOUT = 10.0
CLIENT_LOGGERS = ("httpx", "httpcore")  # the loggers an HTTP client writes to
DEADLINE = 900.0  # seconds for one complete() call: every try, backoff and byte read
MAX_RESPONSE_BYTES = 4 * 2**20  # the most of a successful reply's body that is read
# The one sentence a refused base URL gets; the URL itself is never repeated.
BASE_URL_RULE = (
    "must be https, or http on this machine (localhost, 127.0.0.1 or [::1]), with no "
    "credentials in it, so the key and the claims never cross a network in clear"
)
# This module's own LLMError messages, matched from their start, and the fixed words each
# category is shown with. Whatever follows the first words is never kept.
_FAILURES = (
    (re.compile(r"the endpoint answered HTTP (\d{3})\b"), "http_status"),
    (re.compile(r"could not reach the endpoint\b"), "unreachable"),
    (re.compile(r"the endpoint did not answer within\b"), "timeout"),
    (re.compile(r"the call did not finish within its\b"), "deadline"),
    (re.compile(r"the reply is larger than\b"), "too_large"),
    (re.compile(r"the reply is encoded\b"), "encoded"),
    (re.compile(r"the endpoint's reply is not a chat completion\b"), "invalid_reply"),
    (re.compile(r"the endpoint's reply is not a message\b"), "invalid_message"),
    (re.compile(r"the request failed\b"), "request_failed"),
)
_FAILURE_TEXT = {
    "unreachable": "could not reach the endpoint",
    "timeout": "the endpoint did not answer in time",
    "deadline": "the call did not finish within its deadline",
    "too_large": "the reply is larger than the bound",
    "encoded": "the reply is encoded",
    "invalid_reply": "the endpoint's reply is not a chat completion",
    "invalid_message": "the endpoint's reply is not a message",
    "request_failed": "the request failed",
    "other": "the provider failed",
}
_RETRY_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError)
# The failures that prove a try never reached the endpoint: the connection was never made
# (refused, unresolved, or its TCP or TLS setup failed or timed out), so the request was never
# written. Every other failure may have come after the provider read it.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout)
_ASSIGNMENT = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)")
BOUNDARY_DRAWS = 16  # tries to draw a tag that occurs nowhere in the quoted text
# What cairn sets or relies on in a request: a bound on the answer is a bound on its cost.
RESERVED_FIELDS = frozenset(
    {
        "model",
        "messages",
        "temperature",
        "response_format",
        "max_tokens",
        "max_completion_tokens",
        "stream",
        "n",
    }
)
# Anthropic's Messages API (AnthropicMessages).
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2026-10-21"  # the anthropic-version header every request sends
DEFAULT_MAX_TOKENS = 16_000  # the Messages API needs a bound on every request
# 529 is the API's "overloaded"; the other statuses are tried again as for any endpoint.
ANTHROPIC_RETRY_STATUSES = RETRY_STATUSES | {529}
# What cairn sets in a Messages request. `thinking` is not among them: the operator may
# replace cairn's {"type": "disabled"}, or leave it out with null.
ANTHROPIC_RESERVED_FIELDS = frozenset({"model", "messages", "system", "max_tokens", "stream"})
# The stop reasons the rest of cairn reads in OpenAI's words; any other is kept as it is.
_ANTHROPIC_STOPS = {"end_turn": "stop", "max_tokens": "length"}
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCED = re.compile(r"```[\w-]*[ \t]*\n(.*?)\n?```", re.DOTALL)


class Reply(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None


class ChatModel(Protocol):
    name: str

    def complete(self, messages: Sequence[Message]) -> Reply: ...


class LLMError(RuntimeError):
    """The endpoint failed, or answered with something that is not a chat completion.
    `requests`: how many requests the client sent before it gave up; None: not counted."""

    requests: int | None = None


class LLMConfigError(ValueError):
    """Missing or malformed configuration. The message names the setting, never its value."""


class FakeModel:
    """A scripted ChatModel for tests: each call returns (or raises) the next item. A string
    becomes Reply(text=...). Each call's messages are copied into `requests`."""

    def __init__(self, replies: Iterable[str | Reply | BaseException], name: str = "fake") -> None:
        self.name = name
        self.requests: list[list[dict[str, str]]] = []
        self._replies = list(replies)

    def complete(self, messages: Sequence[Message]) -> Reply:
        self.requests.append([dict(message) for message in messages])
        if not self._replies:
            raise AssertionError(f"{self.name} has no scripted reply left")
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply if isinstance(reply, Reply) else Reply(text=reply)


class _Transport:
    """What one call is, whatever the dialect: the deadline, the counted tries, the bound on
    what is read, the identity encoding, the unread error body and the fixed error words (the
    module docstring's "Bounds", "Counting requests" and "Bounding the answer"). A dialect
    supplies only the URL's path and its own headers (to __init__), how a request body is
    built from messages (`_body`), how a 200 body becomes a Reply (`_reply`), which statuses
    are tried again (`retry_statuses`) and which extra fields it reserves (`reserved`).

    `timeout` bounds each network wait. Connecting gets at most CONNECT_TIMEOUT seconds, so an
    unreachable host fails in seconds, not after a full model timeout per try. `deadline`
    bounds the whole call, tries, headers and backoff included: each try waits at most what is
    left of it, a backoff that would pass it ends the call, and a request still running at it
    is cancelled. At most `max_response_bytes` of a reply are read; a longer one is an LLMError.
    """

    retry_statuses: frozenset[int] = RETRY_STATUSES
    reserved: frozenset[str] = RESERVED_FIELDS

    def __init__(
        self,
        base_url: str,
        model: str,
        path: str,
        *,
        headers: Mapping[str, str],
        extra: Mapping[str, Any] | None,
        max_tokens: int | None,
        timeout: float,
        deadline: float,
        max_response_bytes: int,
        retries: int,
        transport: httpx.BaseTransport | None,
        sleep: Callable[[float], None],
        clock: Callable[[], float],
        wait: Callable[[threading.Event, float], bool],
    ) -> None:
        if deadline <= 0 or max_response_bytes <= 0:
            raise LLMConfigError("the deadline and the response size bound must be positive")
        base = base_url.strip().rstrip("/")
        if not _https_or_loopback(base):  # the key and the claims never cross a network in clear
            raise LLMConfigError(f"the base URL {BASE_URL_RULE}")
        for name in CLIENT_LOGGERS:  # never a key a response header echoes, at any level
            client_log = logging.getLogger(name)
            client_log.setLevel(max(logging.WARNING, client_log.level))
        self.name = model
        self._url = f"{base}/{path}"
        # The size bound is the bytes sent, so nothing is compressed.
        self._headers = {"Accept-Encoding": "identity", **headers}
        self._extra = checked_extra(extra, self.reserved)
        self._max_tokens = max_tokens  # None: the dialect's own default
        self._timeout = timeout
        self.deadline = deadline
        self.max_response_bytes = max_response_bytes
        self._retries = max(0, retries)
        self.max_requests = self._retries + 1  # the most requests one complete() sends
        self._counted = threading.local()  # last_requests, per thread; a bounded copy shares it
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._wait = wait

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.name!r})"

    def bounded(self, max_tokens: int) -> Self:
        """This model, asking for at most `max_tokens` completion tokens (the tighter of that
        and its own cap). The original is unchanged. ValueError: fewer than 1."""
        if max_tokens < 1:
            raise ValueError("a completion bound is at least 1 token")
        view = copy.copy(self)
        own = self._max_tokens
        view._max_tokens = max_tokens if own is None else min(own, max_tokens)
        return view

    def _body(self, messages: Sequence[Message]) -> dict[str, Any]:
        raise NotImplementedError

    def _reply(self, content: bytes) -> Reply:
        raise NotImplementedError

    def complete(self, messages: Sequence[Message]) -> Reply:
        """One chat completion. How many requests it sent (each try that may have reached the
        endpoint: all but those that could not connect) is `last_requests()` on the same
        thread after it, and an LLMError's `requests`."""
        tries = [0]
        try:
            return self._complete(messages, tries)
        except LLMError as error:
            error.requests = tries[0]
            raise
        finally:
            self._counted.requests = tries[0]

    def last_requests(self) -> int | None:
        """The requests the last complete() on this thread sent, every retry included; None
        before any. Threads calling one model at once each read their own."""
        return getattr(self._counted, "requests", None)

    def _complete(self, messages: Sequence[Message], tries: list[int]) -> Reply:
        body = self._body(messages)
        end = self._clock() + self.deadline
        for attempt in range(self._retries + 1):
            last = attempt == self._retries
            left = end - self._clock()
            if left <= 0:
                raise self._past_deadline()
            wait = min(self._timeout, left)
            timeouts = httpx.Timeout(wait, connect=min(CONNECT_TIMEOUT, wait))
            tries[0] += 1  # sent, unless it could not connect (below)
            try:
                status, content = self._attempt(body, timeouts, end, left)
            except _RETRY_ERRORS as error:
                if isinstance(error, _NOT_SENT):
                    tries[0] -= 1  # no connection was made: not a byte of it was written
                if last:
                    raise LLMError(
                        f"could not reach the endpoint ({type(error).__name__})"
                    ) from None
            except httpx.TimeoutException as error:
                raise LLMError(
                    f"the endpoint did not answer within {wait:g} s ({type(error).__name__})"
                ) from None
            except httpx.HTTPError as error:
                raise LLMError(f"the request failed ({type(error).__name__})") from None
            else:
                if 200 <= status < 300:
                    return self._reply(content)
                if last or status not in self.retry_statuses:
                    raise LLMError(f"the endpoint answered HTTP {status}") from None
            pause = 2.0**attempt
            if self._clock() + pause >= end:
                raise self._past_deadline()
            self._sleep(pause)
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _attempt(
        self, body: dict[str, Any], timeouts: httpx.Timeout, end: float, left: float
    ) -> tuple[int, bytes]:
        """One try: (status, body read). It runs on a worker thread so that the deadline holds
        whatever the network does, headers included: when `left` seconds pass first, the
        client is closed, which cancels the request, and the call ends at once."""
        client = httpx.Client(transport=self._transport, timeout=timeouts)
        outcome: list[tuple[int, bytes] | BaseException] = []
        done = threading.Event()

        def work() -> None:
            try:
                with client.stream("POST", self._url, json=body, headers=self._headers) as response:
                    if not response.is_success:  # an error body is never read, let alone shown
                        outcome.append((response.status_code, b""))
                        return
                    encoding = response.headers.get("content-encoding", "").strip().lower()
                    if encoding not in ("", "identity"):  # never expanded, so never read
                        raise LLMError("the reply is encoded, and only an unencoded one is read")
                    outcome.append((response.status_code, self._read(response, end)))
            except BaseException as error:  # handed to the caller's thread below
                outcome.append(error)
            finally:
                done.set()

        threading.Thread(target=work, name="cairn-llm-request", daemon=True).start()
        finished = self._wait(done, left)
        client.close()
        if not finished:
            raise self._past_deadline()
        (result,) = outcome
        if isinstance(result, BaseException):
            raise result
        return result

    def _read(self, response: httpx.Response, end: float) -> bytes:
        """The body, read before `end`. A body longer than max_response_bytes, or still arriving
        at `end`, is an LLMError."""
        chunks, size, limit = [], 0, self.max_response_bytes
        for chunk in response.iter_bytes():  # identity only (checked first): nothing decoded
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise LLMError(f"the reply is larger than {limit} bytes") from None
            if self._clock() > end:
                raise self._past_deadline()
        return b"".join(chunks)

    def _past_deadline(self) -> LLMError:
        return LLMError(f"the call did not finish within its {self.deadline:g} s deadline")


class OpenAICompatible(_Transport):
    """A ChatModel behind an OpenAI-compatible /chat/completions endpoint, on cairn's transport
    (_Transport: the deadline, the counted tries and the bound on what is read)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 600.0,
        deadline: float = DEADLINE,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        json_mode: bool = True,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        retries: int = 2,
        extra: Mapping[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[threading.Event, float], bool] = threading.Event.wait,
    ) -> None:
        super().__init__(
            base_url,
            model,
            "chat/completions",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            extra=extra,
            max_tokens=max_tokens,
            timeout=timeout,
            deadline=deadline,
            max_response_bytes=max_response_bytes,
            retries=retries,
            transport=transport,
            sleep=sleep,
            clock=clock,
            wait=wait,
        )
        self._options: dict[str, Any] = {"temperature": temperature}
        if json_mode:
            self._options["response_format"] = {"type": "json_object"}

    def _body(self, messages: Sequence[Message]) -> dict[str, Any]:
        body = {
            **self._extra,
            "model": self.name,
            "messages": [dict(m) for m in messages],
            **self._options,
        }
        if self._max_tokens is not None:
            body["max_tokens"] = self._max_tokens
        return body

    def _reply(self, content: bytes) -> Reply:
        try:
            data = json.loads(content)
            choice = data["choices"][0]
            text = (choice.get("message") or {}).get("content")
            finish = choice.get("finish_reason")
        except (ValueError, LookupError, TypeError, AttributeError):
            raise LLMError("the endpoint's reply is not a chat completion") from None
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return Reply(
            text=text if isinstance(text, str) else "",
            prompt_tokens=_count(usage.get("prompt_tokens")),
            completion_tokens=_count(usage.get("completion_tokens")),
            finish_reason=finish if isinstance(finish, str) else None,
        )


class AnthropicMessages(_Transport):
    """A ChatModel behind Anthropic's Messages API (<base_url>/messages), or any endpoint that
    speaks it, on cairn's transport (_Transport: the deadline, the counted tries and the bound
    on what is read). Without `api_key` no x-api-key header is sent. `json_mode` is accepted
    for the callers that pass it and changes nothing: this API has no JSON mode cairn sends."""

    retry_statuses = ANTHROPIC_RETRY_STATUSES
    reserved = ANTHROPIC_RESERVED_FIELDS

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = ANTHROPIC_BASE_URL,
        timeout: float = 600.0,
        deadline: float = DEADLINE,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        json_mode: bool = True,
        max_tokens: int | None = None,
        retries: int = 2,
        extra: Mapping[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[threading.Event, float], bool] = threading.Event.wait,
    ) -> None:
        del json_mode  # accepted, and nothing to do: see the class docstring
        bound = DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens
        if bound < 1:
            raise LLMConfigError("max_tokens must be at least 1: this API needs room to answer")
        headers = {"anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        super().__init__(
            base_url,
            model,
            "messages",
            headers=headers,
            extra=extra,
            max_tokens=bound,
            timeout=timeout,
            deadline=deadline,
            max_response_bytes=max_response_bytes,
            retries=retries,
            transport=transport,
            sleep=sleep,
            clock=clock,
            wait=wait,
        )

    def _body(self, messages: Sequence[Message]) -> dict[str, Any]:
        """cairn's system messages as the top-level `system` (joined by a blank line), the
        others as `messages`, the bound, thinking off, then the extra fields; a null extra
        field is left out. cairn adds no sampling field and no response_format."""
        system = [m["content"] for m in messages if m["role"] == "system"]
        body: dict[str, Any] = {
            "model": self.name,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": m["role"], "content": m["content"]}
                for m in messages
                if m["role"] != "system"
            ],
        }
        if system:
            body["system"] = "\n\n".join(system)
        body["thinking"] = {"type": "disabled"}
        for name, value in self._extra.items():
            if value is None:
                body.pop(name, None)
            else:
                body[name] = value
        return body

    def _reply(self, content: bytes) -> Reply:
        try:
            data = json.loads(content)
            blocks = data["content"]
            if not isinstance(blocks, list):
                raise TypeError
        except (ValueError, LookupError, TypeError):
            raise LLMError("the endpoint's reply is not a message") from None
        text = "".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        prompt = _count(usage.get("input_tokens"))
        if prompt is not None:  # what was charged, cache writes and reads included
            for cached in ("cache_creation_input_tokens", "cache_read_input_tokens"):
                prompt += _count(usage.get(cached)) or 0
        stop = data.get("stop_reason")
        return Reply(
            text=text,
            prompt_tokens=prompt,
            completion_tokens=_count(usage.get("output_tokens")),
            finish_reason=_ANTHROPIC_STOPS.get(stop, stop) if isinstance(stop, str) else None,
        )


def failure(error: BaseException) -> tuple[int | None, str]:
    """The HTTP status (None when there is none) and a fixed category of a provider failure,
    read from the start of its message: nothing an endpoint or a gateway wrote is kept."""
    message = str(error)
    for pattern, category in _FAILURES:
        if match := pattern.match(message):
            return (int(match[1]) if match.groups() else None), category
    return None, "other"


def failure_text(error: BaseException) -> str:
    """A provider failure as a fixed sentence: its status, or its category's own words."""
    status, category = failure(error)
    return f"the endpoint answered HTTP {status}" if status else _FAILURE_TEXT[category]


def new_boundary() -> str:
    """A tag suffix for one request: 16 random hex characters."""
    return secrets.token_hex(8)


def draw_tag(texts: Iterable[str], draw: Callable[[], str] = new_boundary) -> str:
    """A tag from `draw` that occurs in none of `texts`. ValueError after BOUNDARY_DRAWS
    draws that all occur."""
    texts = list(texts)
    for _ in range(BOUNDARY_DRAWS):
        tag = draw()
        if tag and not any(tag in text for text in texts):
            return tag
    raise ValueError(f"no tag absent from the quoted text in {BOUNDARY_DRAWS} draws")


def unwrap_reply(text: str) -> str:
    """A reply without its <think>...</think> blocks and one code fence around all of it."""
    text = _THINK.sub("", text).strip()
    if fenced := _FENCED.fullmatch(text):
        text = fenced[1].strip()
    return text


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def checked_extra(extra: object, reserved: frozenset[str] = RESERVED_FIELDS) -> dict[str, Any]:
    """A copy of the extra request fields, or LLMConfigError: they must be an object whose
    names are text and none of `reserved` (RESERVED_FIELDS, the OpenAI-compatible dialect's,
    by default). The message names a field, never a value."""
    if extra is None:
        return {}
    if not isinstance(extra, Mapping) or not all(isinstance(name, str) for name in extra):
        raise LLMConfigError("extra request fields must be a JSON object")
    if taken := sorted(reserved.intersection(extra)):
        raise LLMConfigError(f"extra request fields cannot set {', '.join(taken)}: cairn does")
    return copy.deepcopy(dict(extra))


def from_env(
    model: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    prefix: str = "CAIRN_LLM",
    **options: Any,
) -> OpenAICompatible | AnthropicMessages:
    """A model configured from the environment, read now from `environ` (default os.environ).
    <prefix>_PROVIDER picks the dialect: "openai" (the default, unset or blank) or "anthropic";
    `options` go to that class. For "openai": <prefix>_BASE_URL, <prefix>_API_KEY (optional)
    and <prefix>_MODEL (unless `model` is given). For "anthropic": <prefix>_MODEL (unless
    `model` is given), <prefix>_BASE_URL (optional: Anthropic's API by default) and
    <prefix>_API_KEY (required for Anthropic's API, optional for an endpoint the operator
    names). Both read <prefix>_REQUEST_EXTRA, under the dialect's own reserved fields."""
    env = os.environ if environ is None else environ
    provider = env.get(f"{prefix}_PROVIDER", "").strip().lower()
    if provider == "anthropic":
        return _anthropic_from_env(model, env, prefix, options)
    if provider not in ("", "openai"):
        raise LLMConfigError(f"{prefix}_PROVIDER must be openai (the default) or anthropic")
    base_url = env.get(f"{prefix}_BASE_URL", "").strip()
    if not base_url:
        raise LLMConfigError(
            f"{prefix}_BASE_URL is not set: set it to the base URL of an OpenAI-compatible "
            "endpoint, ending in /v1"
        )
    name = (model or env.get(f"{prefix}_MODEL", "")).strip()
    if not name:
        raise LLMConfigError(f"no model: pass one or set {prefix}_MODEL")
    api_key = env.get(f"{prefix}_API_KEY", "").strip() or None
    _extra_from_env(env, prefix, options, RESERVED_FIELDS)
    try:
        return OpenAICompatible(base_url, name, api_key=api_key, **options)
    except LLMConfigError:
        raise LLMConfigError(f"{prefix}_BASE_URL {BASE_URL_RULE}") from None


def _anthropic_from_env(
    model: str | None, env: Mapping[str, str], prefix: str, options: dict[str, Any]
) -> AnthropicMessages:
    name = (model or env.get(f"{prefix}_MODEL", "")).strip()
    if not name:
        raise LLMConfigError(f"no model: pass one or set {prefix}_MODEL")
    base_url = env.get(f"{prefix}_BASE_URL", "").strip()
    api_key = env.get(f"{prefix}_API_KEY", "").strip() or None
    if base_url:
        if not _https_or_loopback(base_url.rstrip("/")):
            raise LLMConfigError(f"{prefix}_BASE_URL {BASE_URL_RULE}")
        options["base_url"] = base_url
    elif api_key is None:  # Anthropic's own API: refused before the first call, not at it
        raise LLMConfigError(
            f"{prefix}_API_KEY is not set: Anthropic's API needs a key (or set "
            f"{prefix}_BASE_URL to an endpoint that speaks its format)"
        )
    _extra_from_env(env, prefix, options, ANTHROPIC_RESERVED_FIELDS)
    return AnthropicMessages(name, api_key=api_key, **options)


def _extra_from_env(
    env: Mapping[str, str], prefix: str, options: dict[str, Any], reserved: frozenset[str]
) -> None:
    """<prefix>_REQUEST_EXTRA into options["extra"], unless the caller gave `extra`.
    LLMConfigError names the variable, and a reserved field, never a value."""
    given = env.get(f"{prefix}_REQUEST_EXTRA", "").strip()
    if given and "extra" not in options:
        try:
            options["extra"] = checked_extra(json.loads(given), reserved)
        except LLMConfigError as error:  # before ValueError, which it is
            raise LLMConfigError(f"{prefix}_REQUEST_EXTRA: {error}") from None
        except ValueError:  # not JSON
            raise LLMConfigError(
                f"{prefix}_REQUEST_EXTRA must be a JSON object of extra request fields"
            ) from None


def read_env_file(path: str | Path) -> dict[str, str]:
    """KEY=VALUE lines, as a shell would source them: blank lines and # comments are skipped,
    an `export ` prefix is allowed and one pair of matching quotes is stripped. Nothing is
    expanded. Errors name the file and line, never a value."""
    path = Path(path).expanduser()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise LLMConfigError(f"cannot read {path}: {error.strerror}") from None
    except UnicodeDecodeError:
        raise LLMConfigError(f"cannot read {path}: it is not UTF-8 text") from None
    values = {}
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT.fullmatch(stripped)
        if match is None:
            raise LLMConfigError(f"{path}:{number}: expected KEY=VALUE")
        key, value = match[1], match[2].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values
