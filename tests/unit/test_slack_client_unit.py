"""cairn.slack.client: the few calls cairn makes to Slack, each through an httpx client a test
hands in. Nothing here reaches Slack; every name and token is made up."""

import asyncio
import json

import httpx
import pytest

from cairn.slack.client import API, REPLY_HOSTS, SlackClient, SlackError

TOKEN = "xoxb-made-up-for-this-test"


def client(handler, **options) -> SlackClient:
    return SlackClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), **options)


def run(coroutine):
    return asyncio.run(coroutine)


def test_a_call_goes_to_the_method_with_the_token_in_the_header_and_nowhere_else():
    seen = []

    def slack(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "team_id": "T0MADEUP", "user_id": "U0BOT"})

    answer = run(client(slack).auth_test(TOKEN))

    assert answer["team_id"] == "T0MADEUP"
    (request,) = seen
    assert str(request.url) == f"{API}/auth.test" and request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(request.url) and TOKEN.encode() not in request.content


def test_an_answer_slack_marks_not_ok_is_an_error_named_by_slacks_own_word():
    def slack(request):
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    with pytest.raises(SlackError) as refused:
        run(client(slack).auth_test(TOKEN))

    assert refused.value.code == "invalid_auth"
    assert TOKEN not in str(refused.value) and TOKEN not in repr(refused.value)


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(500, text="oops"), "http_500"),
        (httpx.Response(200, text="not json"), "not_json"),
        (httpx.Response(200, json=["ok"]), "not_json"),
        (httpx.Response(200, json={"ok": False}), "unknown"),
    ],
)
def test_an_answer_that_is_not_slacks_is_an_error_too(response, code):
    with pytest.raises(SlackError) as refused:
        run(client(lambda request: response).auth_test(TOKEN))
    assert refused.value.code == code


def test_a_429_is_waited_out_by_its_retry_after_a_bounded_number_of_times():
    answers = [
        httpx.Response(429, headers={"retry-after": "7"}),
        httpx.Response(429, headers={"retry-after": "3"}),
        httpx.Response(200, json={"ok": True, "user": {"id": "U0ADA", "team_id": "T0MADEUP"}}),
    ]
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    found = run(client(lambda request: answers.pop(0), sleep=sleep).user(TOKEN, "U0ADA"))

    assert found["id"] == "U0ADA" and slept == [7, 3]


def test_a_third_429_is_given_up_on_and_a_long_wait_is_capped():
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    def slack(request):
        return httpx.Response(429, headers={"retry-after": "100000"})

    with pytest.raises(SlackError) as refused:
        run(client(slack, sleep=sleep).user(TOKEN, "U0ADA"))

    assert refused.value.code == "rate_limited" and slept == [30, 30]


def test_within_a_limit_nothing_is_waited_for_and_a_slow_slack_is_a_timeout():
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    def limited(request):
        return httpx.Response(429, headers={"retry-after": "1"})

    with pytest.raises(SlackError) as refused:
        run(client(limited, sleep=sleep).user(TOKEN, "U0ADA", within=1.5))
    assert refused.value.code == "rate_limited" and slept == []

    async def slow(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"ok": True, "user": {"id": "U0ADA"}})

    with pytest.raises(SlackError) as late:
        run(client(slow).user(TOKEN, "U0ADA", within=0.01))
    assert late.value.code == "timeout"


def test_a_message_is_posted_as_json_to_a_conversation():
    seen = []

    def slack(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    run(client(slack).post(TOKEN, "D0MADEUP", {"text": "Nothing found.", "blocks": []}))

    (request,) = seen
    assert str(request.url) == f"{API}/chat.postMessage"
    assert json.loads(request.content) == {
        "channel": "D0MADEUP", "text": "Nothing found.", "blocks": [],
    }  # fmt: skip


def test_a_reply_goes_only_to_slacks_reply_hosts_over_https_and_is_private_unless_said():
    seen = []

    def slack(request):
        seen.append(request)
        return httpx.Response(200, text="ok")

    slack_client = client(slack)
    run(slack_client.reply("https://hooks.slack.com/commands/T0/1/made-up", {"text": "Filed."}))

    (request,) = seen
    assert json.loads(request.content) == {
        "text": "Filed.", "response_type": "ephemeral", "replace_original": False,
    }  # fmt: skip
    assert "authorization" not in request.headers
    for address in (
        "http://hooks.slack.com/commands/T0/1/made-up",
        "https://hooks.slack.com.example.test/commands/T0/1/made-up",
        "https://example.test/hooks.slack.com/",
        "https://user@hooks.slack.com/commands/T0/1/made-up",
        "not an address",
    ):
        with pytest.raises(SlackError) as refused:
            run(slack_client.reply(address, {"text": "Filed."}))
        assert refused.value.code == "bad_reply_address", address
    assert len(seen) == 1
    assert "hooks.slack.com" in REPLY_HOSTS and "hooks.slack-gov.com" in REPLY_HOSTS


@pytest.mark.parametrize(
    "address",
    [
        "https://hoo\tks.slack.com/commands/T0/1/made-up",  # a control character in the host
        "https://hooks.slack.com\n/commands/T0/1/made-up",
        "https://hooks.slack.com/commands/T0/1/made\x00up",
        "https://hooks.slack.com:99999/commands/T0/1/made-up",
        "https://[hooks.slack.com/commands",
    ],
)
def test_an_address_that_is_not_a_url_at_all_is_a_bad_reply_address_and_nothing_is_sent(
    address,
):
    seen = []

    def slack(request):
        seen.append(request)
        return httpx.Response(200, text="ok")

    with pytest.raises(SlackError) as refused:
        run(client(slack).reply(address, {"text": "Filed."}))
    assert refused.value.code == "bad_reply_address"
    assert "hooks" not in str(refused.value) and seen == []


def test_a_reply_slack_does_not_take_is_an_error():
    with pytest.raises(SlackError) as refused:
        run(
            client(lambda request: httpx.Response(404, text="expired_url")).reply(
                "https://hooks.slack.com/commands/T0/1/made-up", {"text": "Filed."}
            )
        )
    assert refused.value.code == "http_404"


def test_another_base_and_other_reply_hosts_are_a_tests_to_give():
    seen = []

    def stand_in(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    slack_client = client(stand_in, api="http://127.0.0.1:9/api", reply_hosts={"127.0.0.1"})
    run(slack_client.auth_test(TOKEN))
    run(slack_client.reply("http://127.0.0.1:9/reply/1", {"text": "Filed."}))

    assert seen == ["http://127.0.0.1:9/api/auth.test", "http://127.0.0.1:9/reply/1"]


def test_the_client_has_no_call_that_reads_a_conversations_history():
    """D3: cairn takes what a payload carries. Nothing here names a method that reads one."""
    import inspect

    import cairn.slack.client as module

    source = inspect.getsource(module)
    for method in ("conversations.history", "conversations.replies", "search.messages"):
        assert method not in source
    assert sorted(n for n in vars(SlackClient) if not n.startswith("_")) == [
        "auth_test", "call", "post", "reply", "user",
    ]  # fmt: skip


def test_a_handlers_context_makes_the_client_with_the_servers_base_and_reply_hosts():
    """The switch carries where Slack is (cairn.web.slack.Slack.api, .reply_hosts), so that a
    journey test points the server at a stand-in on loopback; `serve` has no option for it."""
    from cairn.slack.dispatch import Context
    from cairn.web.slack import Slack

    assert Slack(key=b"k" * 32).api == API and Slack(key=b"k" * 32).reply_hosts == REPLY_HOSTS
    seen = []

    def stand_in(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    context = Context(
        install=None,
        view=None,
        memory_for=lambda user: None,
        http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(stand_in)),
        api="http://127.0.0.1:9/api",
        reply_hosts=frozenset({"127.0.0.1"}),
    )
    run(context.slack().auth_test(TOKEN))
    assert seen == ["http://127.0.0.1:9/api/auth.test"]
