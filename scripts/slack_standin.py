#!/usr/bin/env python3
"""Run a synthetic Slack Web API on loopback or use SlackStandIn in a local test."""

import argparse
import hashlib
import hmac
import itertools
import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_LOOPBACK = "127.0.0.1"
_JSON = "application/json"
_FORM = "application/x-www-form-urlencoded"
_BODY_LIMIT = 1024 * 1024
_PRIVATE_FIELDS = {"authorization", "token", "signing_secret", "response_url"}


@dataclass(frozen=True)
class Call:
    method: str
    fields: dict


class _Handler(BaseHTTPRequestHandler):
    timeout = 3

    def do_GET(self):
        self._respond(dict(parse_qsl(urlsplit(self.path).query)))

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= _BODY_LIMIT:
                self._write(413, {"ok": False, "error": "body_too_large"})
                return
            raw = self.rfile.read(length).decode("utf-8")
            if self.headers.get_content_type() == _JSON:
                fields = json.loads(raw)
            else:
                fields = dict(parse_qsl(raw, keep_blank_values=True))
            if not isinstance(fields, dict):
                raise ValueError("expected a JSON object or form data")
        except (ValueError, TimeoutError):
            self._write(400, {"ok": False, "error": "bad_payload"})
            return
        self._respond(fields)

    def _respond(self, fields):
        path = urlsplit(self.path).path
        if path == "/response":
            method = "response_url"
        elif path.startswith("/api/"):
            method = path.removeprefix("/api/")
        else:
            self._write(404, {"ok": False, "error": "unknown_path"})
            return
        status, reply, retry_after = self.server.slack._answer(
            method, fields, self.headers.get("Authorization")
        )
        self._write(status, reply, retry_after)

    def _write(self, status, reply, retry_after=None):
        body = json.dumps(reply).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", _JSON)
        self.send_header("Content-Length", str(len(body)))
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class _Server(ThreadingHTTPServer):
    def server_bind(self):
        # No DNS lookup is needed for this numeric loopback address.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SlackStandIn:
    """Exercise Cairn against synthetic Slack calls without accessing a workspace."""

    team_id = "T00000001"
    team_domain = "cairn-review"
    app_id = "A00000001"
    bot_id = "B00000001"
    bot_user_id = "U00000001"
    user_id = "U00000002"
    external_user_id = "U00000003"
    conversation_id = "C00000001"

    # These are synthetic fixture values. They have no access to Slack.
    bot_token = "xoxb-standin-only"
    signing_secret = "cairn-standin-signing-secret"

    def __init__(self, *, port=0, clock=time.time):
        self.port = port
        self.clock = clock
        self._server = None
        self._calls = []
        self._limits = {}
        self._event_ids = itertools.count(1)
        self._lock = threading.Lock()
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def __enter__(self):
        self._server = _Server((_LOOPBACK, self.port), _Handler)
        self._server.slack = self
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._thread.join()
        self._server.server_close()
        self._server = None

    @property
    def origin(self):
        if self._server is None:
            raise RuntimeError("SlackStandIn must be running")
        return f"http://{_LOOPBACK}:{self._server.server_port}"

    @property
    def api_base(self):
        return f"{self.origin}/api"

    @property
    def response_url(self):
        return f"{self.origin}/response"

    @property
    def calls(self):
        with self._lock:
            return tuple(self._calls)

    def limit_next(self, method, *, retry_after):
        with self._lock:
            self._limits[method] = retry_after

    def _answer(self, method, fields, authorization):
        recorded = {
            key: value for key, value in fields.items() if key.lower() not in _PRIVATE_FIELDS
        }
        with self._lock:
            self._calls.append(Call(method, recorded))
            retry_after = self._limits.pop(method, None)
        if retry_after is not None:
            return 429, {"ok": False, "error": "ratelimited"}, retry_after
        if method == "response_url":
            return 200, {"ok": True}, None
        if authorization != f"Bearer {self.bot_token}":
            return 200, {"ok": False, "error": "invalid_auth"}, None
        if method == "auth.test":
            return (
                200,
                {
                    "ok": True,
                    "team_id": self.team_id,
                    "team": "Cairn Review",
                    "url": f"https://{self.team_domain}.slack.com/",
                    "user": "cairn",
                    "user_id": self.bot_user_id,
                    "bot_id": self.bot_id,
                },
                None,
            )
        if method == "users.info":
            return 200, self._user(fields.get("user")), None
        if method == "chat.postMessage":
            return (
                200,
                {"ok": True, "channel": fields.get("channel"), "ts": "1700000000.000002"},
                None,
            )
        return 404, {"ok": False, "error": "unknown_method"}, None

    def _user(self, user_id):
        users = {
            self.user_id: ("Ada Review", self.team_id, False, False),
            self.bot_user_id: ("Cairn Review Bot", self.team_id, True, False),
            self.external_user_id: ("Ben Outside", "T00000002", False, True),
        }
        if not isinstance(user_id, str) or user_id not in users:
            return {"ok": False, "error": "user_not_found"}
        name, team_id, is_bot, is_stranger = users[user_id]
        return {
            "ok": True,
            "user": {
                "id": user_id,
                "team_id": team_id,
                "real_name": name,
                "is_bot": is_bot,
                "is_stranger": is_stranger,
                "profile": {"display_name": name, "real_name": name},
            },
        }

    def signed_headers(self, body, *, content_type=_JSON, timestamp=None):
        timestamp = str(int(self.clock()) if timestamp is None else timestamp)
        signed = f"v0:{timestamp}:".encode("ascii") + body
        digest = hmac.new(self.signing_secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        return {
            "Content-Type": content_type,
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": f"v0={digest}",
        }

    def _send(self, url, body, *, content_type=_JSON, retry_num=None):
        endpoint = urlsplit(url)
        if (
            endpoint.scheme != "http"
            or endpoint.hostname != _LOOPBACK
            or endpoint.username is not None
            or endpoint.fragment
        ):
            raise ValueError("stand-in callbacks must use an HTTP loopback address")
        headers = self.signed_headers(body, content_type=content_type)
        if retry_num is not None:
            headers["X-Slack-Retry-Num"] = str(retry_num)
            headers["X-Slack-Retry-Reason"] = "http_timeout"
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=5) as reply:
                return reply.status, reply.read(_BODY_LIMIT)
        except HTTPError as reply:
            with reply:
                return reply.code, reply.read(_BODY_LIMIT)

    def verify_url(self, url, *, challenge="review-challenge"):
        return self._send(
            url, json.dumps({"type": "url_verification", "challenge": challenge}).encode()
        )

    def send_event(self, url, event, *, event_id=None, event_time=None, retry_num=None):
        """Reuse event_id, event_time and event to replay the same body."""
        envelope = {
            "type": "event_callback",
            "team_id": self.team_id,
            "api_app_id": self.app_id,
            "event_id": event_id or f"Ev{next(self._event_ids):010d}",
            "event_time": int(self.clock()) if event_time is None else event_time,
            "event": event,
        }
        return self._send(url, json.dumps(envelope).encode(), retry_num=retry_num)

    def send_command(self, url, *, text, command="/cairn", user_id=None):
        fields = {
            "command": command,
            "text": text,
            "team_id": self.team_id,
            "team_domain": self.team_domain,
            "channel_id": self.conversation_id,
            "user_id": user_id or self.user_id,
            "api_app_id": self.app_id,
            "response_url": self.response_url,
            "trigger_id": "synthetic-command-trigger",
        }
        return self._send(url, urlencode(fields).encode(), content_type=_FORM)

    def send_action(self, url, payload):
        fields = {
            "type": "message_action",
            "callback_id": "file_in_cairn",
            "team": {"id": self.team_id, "domain": self.team_domain},
            "channel": {"id": self.conversation_id},
            "user": {"id": self.user_id},
            "api_app_id": self.app_id,
            "response_url": self.response_url,
            "trigger_id": "synthetic-action-trigger",
        } | payload
        return self._send(
            url, urlencode({"payload": json.dumps(fields)}).encode(), content_type=_FORM
        )

    def revoke_bot(self, url, *, event_id=None):
        event = {"type": "tokens_revoked", "tokens": {"bot": [self.bot_user_id]}}
        return self.send_event(url, event, event_id=event_id)

    def uninstall(self, url, *, revoked_first=True):
        event = {"type": "app_uninstalled"}
        if revoked_first:
            return self.revoke_bot(url), self.send_event(url, event)
        return self.send_event(url, event), self.revoke_bot(url)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="loopback port (default: a free port)")
    args = parser.parse_args(argv)
    try:
        with SlackStandIn(port=args.port) as slack:
            print(slack.api_base, flush=True)
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
