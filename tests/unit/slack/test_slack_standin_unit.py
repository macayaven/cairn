"""The synthetic Slack API and callback sender use only loopback HTTP."""

import hashlib
import hmac
import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import httpx
import pytest

from support.slack.slack_standin import fixture_module


@contextmanager
def receiver():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(({key.lower(): value for key, value in self.headers.items()}, body))
            self.send_response(302 if self.path == "/redirect" else 200)
            if self.path == "/redirect":
                self.send_header("Location", "https://example.invalid/never-contact")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join()


def assert_signed(slack, headers, body):
    timestamp = headers["x-slack-request-timestamp"]
    expected = hmac.new(
        slack.signing_secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
    ).hexdigest()
    assert headers["x-slack-signature"] == "v0=" + expected


def test_standin_listens_without_resolving_its_loopback_address(monkeypatch):
    def unexpected_lookup(*args, **kwargs):
        raise AssertionError("looked up the loopback service name")

    monkeypatch.setattr(socket, "getfqdn", unexpected_lookup)
    with fixture_module().SlackStandIn() as slack:
        port = int(slack.origin.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass


def test_auth_test_uses_synthetic_bot_identity_and_records_no_credentials():
    with fixture_module().SlackStandIn() as slack, httpx.Client(trust_env=False) as api:
        auth = {"Authorization": f"Bearer {slack.bot_token}"}
        answer = api.post(f"{slack.api_base}/auth.test", headers=auth)
        unrelated = api.post(
            f"{slack.api_base}/auth.test",
            headers={"Authorization": "Bearer synthetic-unrelated"},
            data={"token": "synthetic-form-token"},
        )
        assert answer.status_code == 200
        assert answer.json() == {
            "ok": True,
            "team_id": slack.team_id,
            "team": "Cairn Review",
            "url": f"https://{slack.team_domain}.slack.com/",
            "user": "cairn",
            "user_id": slack.bot_user_id,
            "bot_id": slack.bot_id,
        }
        assert unrelated.json() == {"ok": False, "error": "invalid_auth"}
        assert [(call.method, call.fields) for call in slack.calls] == [
            ("auth.test", {}),
            ("auth.test", {}),
        ]


def test_users_info_distinguishes_known_local_outside_and_unknown_users():
    with fixture_module().SlackStandIn() as slack, httpx.Client(trust_env=False) as api:
        endpoint = f"{slack.api_base}/users.info"
        auth = {"Authorization": f"Bearer {slack.bot_token}"}
        local = api.get(endpoint, headers=auth, params={"user": slack.user_id}).json()
        outside = api.get(endpoint, headers=auth, params={"user": slack.external_user_id}).json()
        missing = api.get(endpoint, headers=auth, params={"user": "U00000009"}).json()
        malformed = api.post(endpoint, headers=auth, json={"user": []}).json()
        assert local["user"]["id"] == slack.user_id
        assert local["user"]["team_id"] == slack.team_id
        assert not local["user"].get("is_stranger", False)
        assert outside["user"]["team_id"] != slack.team_id
        assert outside["user"]["is_stranger"] is True
        assert missing == {"ok": False, "error": "user_not_found"}
        assert malformed == missing


def test_outbound_replies_and_rate_limit_are_recorded():
    with fixture_module().SlackStandIn() as slack, httpx.Client(trust_env=False) as api:
        endpoint = f"{slack.api_base}/chat.postMessage"
        auth = {"Authorization": f"Bearer {slack.bot_token}"}
        message = {"channel": slack.conversation_id, "text": "Synthetic search result"}
        slack.limit_next("chat.postMessage", retry_after=2)
        limited = api.post(endpoint, headers=auth, json=message)
        posted = api.post(endpoint, headers=auth, json=message)
        reply = {"text": "A private answer", "response_type": "ephemeral"}
        replied = api.post(slack.response_url, json=reply)
        assert limited.status_code == 429
        assert limited.headers["Retry-After"] == "2"
        assert posted.json()["channel"] == slack.conversation_id
        assert posted.json()["ok"] is True
        assert replied.status_code == 200
        assert [(call.method, call.fields) for call in slack.calls] == [
            ("chat.postMessage", message),
            ("chat.postMessage", message),
            ("response_url", reply),
        ]


def test_web_api_records_an_unexpected_call_without_serving_history():
    with fixture_module().SlackStandIn() as slack, httpx.Client(trust_env=False) as api:
        response = api.get(
            f"{slack.api_base}/conversations.history",
            headers={"Authorization": f"Bearer {slack.bot_token}"},
            params={"channel": slack.conversation_id},
        )
        assert response.status_code == 404
        assert [(call.method, call.fields) for call in slack.calls] == [
            ("conversations.history", {"channel": slack.conversation_id})
        ]


def test_callback_signatures_cover_the_exact_serialized_body():
    module = fixture_module()
    with module.SlackStandIn(clock=lambda: 1_700_000_000) as slack, receiver() as (url, requests):
        status, body = slack.verify_url(url + "/events", challenge="synthetic challenge")
        assert status == 200 and body == b""
        headers, sent = requests[0]
        assert json.loads(sent)["challenge"] == "synthetic challenge"
        assert headers["x-slack-request-timestamp"] == "1700000000"
        assert_signed(slack, headers, sent)


def test_command_and_shortcut_use_distinct_form_encodings_and_actors():
    module = fixture_module()
    with module.SlackStandIn() as slack, receiver() as (url, requests):
        slack.send_command(url + "/commands", text="find paper & boats")
        slack.send_action(
            url + "/actions", {"message": {"user": slack.external_user_id, "text": "Example"}}
        )
        command, action = [parse_qs(body.decode()) for _, body in requests]
        assert command["command"] == ["/cairn"]
        assert command["text"] == ["find paper & boats"]
        assert command["team_id"] == [slack.team_id]
        assert command["response_url"] == [slack.response_url]
        assert requests[0][0]["content-type"] == "application/x-www-form-urlencoded"
        assert requests[1][0]["content-type"] == "application/x-www-form-urlencoded"
        payload = json.loads(action["payload"][0])
        assert payload["type"] == "message_action"
        assert payload["callback_id"] == "file_in_cairn"
        assert payload["user"]["id"] == slack.user_id
        assert payload["message"]["user"] == slack.external_user_id
        for headers, body in requests:
            assert_signed(slack, headers, body)


@pytest.mark.parametrize(
    ("revoked_first", "expected"),
    [(True, ["tokens_revoked", "app_uninstalled"]), (False, ["app_uninstalled", "tokens_revoked"])],
)
def test_sends_full_uninstall_in_both_orders(revoked_first, expected):
    module = fixture_module()
    with module.SlackStandIn() as slack, receiver() as (url, requests):
        slack.uninstall(url + "/events", revoked_first=revoked_first)
        callbacks = [json.loads(body) for _, body in requests]
        assert [item["event"]["type"] for item in callbacks] == expected
        assert {item["type"] for item in callbacks} == {"event_callback"}
        assert {item["team_id"] for item in callbacks} == {slack.team_id}
        assert {item["api_app_id"] for item in callbacks} == {slack.app_id}
        assert len({item["event_id"] for item in callbacks}) == 2


def test_individual_bot_revocation_and_event_retry():
    module = fixture_module()
    with module.SlackStandIn() as slack, receiver() as (url, requests):
        slack.revoke_bot(url + "/events", event_id="Ev-fixture-1")
        slack.send_event(
            url + "/events",
            {"type": "tokens_revoked", "tokens": {"bot": [slack.bot_user_id]}},
            event_id="Ev-fixture-1",
            retry_num=1,
        )
        events = [json.loads(body) for _, body in requests]
        assert [event["event_id"] for event in events] == ["Ev-fixture-1", "Ev-fixture-1"]
        assert events[0]["event"]["tokens"] == {"bot": [slack.bot_user_id]}
        assert requests[1][0]["x-slack-retry-num"] == "1"


def test_event_replay_keeps_original_body_and_signs_with_new_delivery_time():
    module = fixture_module()
    delivery_time = 1_700_000_000
    with module.SlackStandIn(clock=lambda: delivery_time) as slack, receiver() as (url, requests):
        event = {"type": "message", "text": "A synthetic DM"}
        slack.send_event(url + "/events", event, event_id="Ev-original")
        delivery_time += 60
        slack.send_event(
            url + "/events", event, event_id="Ev-original", event_time=1_700_000_000, retry_num=2
        )
        assert requests[0][1] == requests[1][1]
        assert requests[0][0]["x-slack-request-timestamp"] == "1700000000"
        assert requests[1][0]["x-slack-request-timestamp"] == "1700000060"
        for headers, body in requests:
            assert_signed(slack, headers, body)


def test_callback_sender_is_loopback_only_and_does_not_follow_a_redirect():
    module = fixture_module()
    with module.SlackStandIn() as slack, receiver() as (url, requests):
        with pytest.raises(ValueError, match="loopback"):
            slack.verify_url("https://example.invalid/events", challenge="synthetic")
        status, _ = slack.verify_url(url + "/redirect", challenge="synthetic")
        assert status == 302
        assert len(requests) == 1
