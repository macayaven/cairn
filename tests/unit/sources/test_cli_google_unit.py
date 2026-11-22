"""Unit tests for google-authorize's pieces (cairn.web.cli_google), each without a socket where
it can be: the client file, the kinds and their scopes, PKCE and the state, the URL, the
listener's decision, the exchange, the file. The whole command, as a process against a
stand-in Google, is tests/acceptance/test_c8_google_authorize.py; what that file cannot test
(the expiry of --wait, the logs) is tested here with an injected clock and an in-process run.
Every client, code and token is made up."""

import argparse
import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import queue
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from cairn.sources import KINDS, gmail, google_calendar, google_drive, google_meet
from cairn.sources.google_auth import AuthorizedUser, load_credential
from cairn.web import cli_google as g
from support.paths import REPO_ROOT

CLIENT_ID = "1234-unit" + "desktop" + ".apps.example.test"
CLIENT_SECRET = "unit-client-" + "Zx8sEcr" * 4
MARK = "unitFileMark"


def write_client(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "client.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def installed(**override: object) -> dict:
    fields = {
        "client_id": CLIENT_ID,
        "project_id": "unit-project",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": CLIENT_SECRET,
        "redirect_uris": ["http://localhost"],
    }
    fields.update(override)
    return {"installed": fields}


# The client file


def test_a_desktop_app_clients_file_is_read_with_googles_own_endpoints(tmp_path):
    client = g.read_client(write_client(tmp_path, installed()))
    assert (client.client_id, client.client_secret) == (CLIENT_ID, CLIENT_SECRET)
    assert client.auth_url == g.GOOGLE_AUTH_URL == "https://accounts.google.com/o/oauth2/v2/auth"
    assert client.token_url == g.GOOGLE_TOKEN_URL == "https://oauth2.googleapis.com/token"
    assert CLIENT_SECRET not in repr(client)


def test_a_file_with_only_the_two_values_is_enough(tmp_path):
    path = write_client(
        tmp_path, {"installed": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}}
    )
    client = g.read_client(path)
    assert (client.auth_url, client.token_url) == (g.GOOGLE_AUTH_URL, g.GOOGLE_TOKEN_URL)


MARKED = {
    "client_id": f"{MARK}-id",
    "client_secret": f"{MARK}-secret",
    "project_id": f"{MARK}-project",
    "auth_uri": f"https://{MARK}.example.test/auth",
}
REFUSED = {
    "not_json": (f"{{installed: {MARK}", "not JSON"),
    "not_utf8": (b"\xff\xfe" + MARK.encode(), "not JSON"),
    "a_list": (json.dumps([{"installed": MARKED}]), "does not hold a JSON object"),
    "web": (json.dumps({"web": MARKED}), "desktop app"),
    "neither": (json.dumps({"other": MARKED}), "installed"),
    "installed_a_list": (json.dumps({"installed": [MARK]}), "installed"),
    "no_client_id": (
        json.dumps({"installed": {k: v for k, v in MARKED.items() if k != "client_id"}}),
        "client_id",
    ),
    "client_id_blank": (json.dumps({"installed": {**MARKED, "client_id": "  "}}), "client_id"),
    "client_id_a_number": (json.dumps({"installed": {**MARKED, "client_id": 7}}), "client_id"),
    "no_client_secret": (
        json.dumps({"installed": {**MARKED, "client_secret": None}}),
        "client_secret",
    ),
    "client_secret_spaced": (
        json.dumps({"installed": {**MARKED, "client_secret": f"{MARK} x"}}),
        "client_secret",
    ),
}  # fmt: skip


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_a_client_file_cairn_cannot_use_is_one_line_naming_what_is_wrong(tmp_path, case):
    text, named = REFUSED[case]
    path = tmp_path / "client.json"
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    with pytest.raises(g.Refused) as refused:
        g.read_client(path)
    said = str(refused.value)
    assert refused.value.exit == 2
    assert "\n" not in said and MARK not in said
    assert named in said.lower() if named == "desktop app" else named in said
    assert str(path) in said


def test_a_web_client_is_told_to_create_a_desktop_app_client(tmp_path):
    with pytest.raises(g.Refused) as refused:
        g.read_client(write_client(tmp_path, {"web": MARKED}))
    assert "create a Desktop app client" in str(refused.value)


def test_a_missing_or_unreadable_client_file_is_one_line(tmp_path):
    missing = tmp_path / "nowhere.json"
    with pytest.raises(g.Refused) as refused:
        g.read_client(missing)
    assert str(refused.value) == (
        f"there is no client file at {missing}: --client names the JSON file the Cloud console"
        " gives for a Desktop app client"
    )
    with pytest.raises(g.Refused) as folder:
        g.read_client(tmp_path)
    assert str(tmp_path) in str(folder.value) and "\n" not in str(folder.value)


# Where the URL and the exchange go


GOOGLE = "https://google.example.test/default"
TAKEN = [
    "http://127.0.0.1:8080/o/oauth2/v2/auth",
    "http://127.0.0.1/token",
    "http://localhost:9/auth",
    "http://LOCALHOST:9/auth",
]
NOT_TAKEN = [
    None,
    7,
    "",
    "https://accounts.google.com/o/oauth2/auth",
    "https://accounts.other.example.test/o/oauth2/v2/auth",
    "https://127.0.0.1:8080/auth",  # this machine, but not over http
    "http://127.0.0.1.other.example.test:8080/auth",
    "http://localhost.other.example.test/auth",
    "http://127.0.0.1:8080@other.example.test/auth",
    "http://localhost@other.example.test/t",
    "http://user@127.0.0.1:8080/auth",
    "http://127.0.0.1:8080/auth?x=1",
    "http://127.0.0.1:8080/auth#x",
    "http://127.0.0.1:99999/auth",
    "http://127.0.0.1:8080/a uth",
    "http://[::1]:8080/auth",
    "ftp://127.0.0.1/auth",
]


@pytest.mark.parametrize("given", TAKEN)
def test_an_endpoint_on_this_machine_over_http_is_used_as_it_is(given):
    assert g.endpoint(given, GOOGLE) == given


@pytest.mark.parametrize("given", NOT_TAKEN)
def test_any_other_endpoint_is_googles_own(given):
    assert g.endpoint(given, GOOGLE) == GOOGLE


def test_the_files_endpoints_are_decided_one_by_one(tmp_path):
    local = "http://127.0.0.1:8080/o/oauth2/v2/auth"
    client = g.read_client(
        write_client(tmp_path, installed(auth_uri=local, token_uri="https://other.example.test/t"))
    )
    assert (client.auth_url, client.token_url) == (local, g.GOOGLE_TOKEN_URL)


# The kinds and their scopes


def test_the_kinds_are_the_google_ones_and_their_scopes_are_their_modules_own():
    assert g.KIND_NAMES == ("gmail", "google_calendar", "google_drive", "google_meet")
    assert g.SCOPES == {
        "gmail": gmail.SCOPES,
        "google_calendar": google_calendar.SCOPES,
        "google_drive": google_drive.SCOPES,
        "google_meet": google_meet.SCOPES,
    }
    for name in g.KIND_NAMES:
        assert g.SCOPES[name] is kinds_scopes()[name], name


def kinds_scopes() -> dict[str, list[str]]:
    """Every kind of KINDS whose module defines SCOPES: the kinds that sign in to Google."""
    import sys

    found = {}
    for name, kind in KINDS.items():
        scopes = getattr(sys.modules[kind.open.__module__], "SCOPES", None)
        if scopes is not None:
            found[name] = scopes
    return found


def test_every_kind_that_asks_google_for_a_scope_is_one_the_command_takes():
    assert set(kinds_scopes()) == set(g.KIND_NAMES)


@pytest.mark.parametrize(
    ("kinds", "scopes"),
    [
        (["gmail"], [gmail.SCOPE]),
        (["google_drive", "gmail"], [gmail.SCOPE, google_drive.SCOPE]),
        (["gmail", "google_drive", "gmail"], [gmail.SCOPE, google_drive.SCOPE]),
        (
            ["google_meet", "google_drive", "google_calendar", "gmail"],
            [gmail.SCOPE, google_calendar.SCOPE, google_drive.SCOPE, google_meet.SCOPE],
        ),
    ],
)
def test_the_scopes_are_each_once_in_the_order_of_the_kinds_names(kinds, scopes):
    assert g.scopes_for(kinds) == scopes


@pytest.mark.parametrize("value", ["granola", "langfuse", "calendar", "Gmail", ""])
def test_a_kind_that_is_not_google_is_refused_naming_the_kinds_it_takes(value):
    import argparse

    with pytest.raises(argparse.ArgumentTypeError) as refused:
        g.kind(value)
    said = str(refused.value)
    assert said == "not a Google kind: it takes gmail, google_calendar, google_drive or google_meet"
    assert g.kind("google_meet") == "google_meet"


@pytest.mark.parametrize("value", ["29", "901", "0", "-30", "soon", "30.5", "nan", ""])
def test_a_wait_out_of_its_bounds_is_refused(value):
    import argparse

    with pytest.raises(argparse.ArgumentTypeError) as refused:
        g.wait_seconds(value)
    assert str(refused.value) == "a whole number of seconds from 30 to 900"


def test_a_wait_at_its_bounds_is_taken():
    assert (g.wait_seconds("30"), g.wait_seconds("900"), g.WAIT) == (30, 900, 300)


@pytest.mark.parametrize("value", ["0", "65536", "-1", "http", ""])
def test_a_port_out_of_its_bounds_is_refused(value):
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        g.port_number(value)
    assert (g.port_number("1"), g.port_number("65535")) == (1, 65535)


# PKCE and the state


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def test_the_verifier_is_43_unreserved_characters_from_32_random_bytes():
    asked = []

    def token(n: int) -> str:
        asked.append(n)
        return b64url(bytes(range(n)))

    verifier = g.new_verifier(token)
    assert asked == [32]
    assert re.fullmatch(r"[A-Za-z0-9\-._~]{43,128}", verifier)
    assert len(verifier) == 43
    assert g.new_verifier() != g.new_verifier()


def test_the_challenge_is_the_verifiers_sha256_in_base64url_without_padding():
    """The RFC 7636 Appendix B vector, as the notes record it."""
    assert (
        g.challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )
    verifier = g.new_verifier()
    assert g.challenge(verifier) == b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    assert "=" not in g.challenge(verifier)


def test_the_state_is_256_bits_from_secrets():
    asked = []

    def token(n: int) -> str:
        asked.append(n)
        return "s" * 43

    assert g.new_state(token) == "s" * 43 and asked == [32]
    assert g.new_state.__defaults__[0].__module__ == "secrets"
    assert g.new_verifier.__defaults__[0].__module__ == "secrets"
    assert len(g.new_state()) >= 22 and g.new_state() != g.new_state()


# The URL


def test_the_url_has_exactly_its_parameters_each_once():
    url = g.authorization_url(
        g.GOOGLE_AUTH_URL, client_id=CLIENT_ID, redirect_uri="http://127.0.0.1:9004",
        scopes=[gmail.SCOPE, google_drive.SCOPE], state="st4te", challenge="ch4llenge",
    )  # fmt: skip
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == g.GOOGLE_AUTH_URL
    assert parts.fragment == ""
    pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    assert pairs == [
        ("client_id", CLIENT_ID),
        ("redirect_uri", "http://127.0.0.1:9004"),
        ("response_type", "code"),
        ("scope", f"{gmail.SCOPE} {google_drive.SCOPE}"),
        ("state", "st4te"),
        ("code_challenge", "ch4llenge"),
        ("code_challenge_method", "S256"),
        ("access_type", "offline"),
        ("prompt", "consent"),
    ]
    assert "+" not in url and "%20" in url  # a space as Google's examples write it
    assert " " not in url


def test_the_url_starts_from_a_local_endpoint_as_it_is():
    local = "http://localhost:8080/o/oauth2/v2/auth"
    url = g.authorization_url(
        local, client_id=CLIENT_ID, redirect_uri="http://127.0.0.1:1", scopes=[gmail.SCOPE],
        state="s", challenge="c",
    )  # fmt: skip
    assert url.startswith(local + "?")


# The listener's decision, without a socket

STATE = "rightState-" + "x" * 32


@pytest.mark.parametrize(
    "target",
    [
        f"/?code=4/abc&state={STATE}",
        f"/?state={STATE}&code=4%2Fabc&scope=x&authuser=0",
        f"?code=4/abc&state={STATE}",
    ],
)
def test_a_get_of_the_redirect_address_with_code_and_the_right_state_is_the_answer(target):
    decision = g.decide("GET", target, STATE)
    assert decision.outcome == "answer" and decision.code == "4/abc"
    assert (decision.status, decision.page) == (200, g.ANSWERED)
    assert "4/abc" not in repr(decision)


def test_an_error_with_the_right_state_is_the_person_or_google_refusing():
    decision = g.decide("GET", f"/?error=access_denied&state={STATE}", STATE)
    assert decision.outcome == "refused" and decision.code is None
    assert (decision.status, decision.page) == (200, g.REFUSED)
    both = g.decide("GET", f"/?error=access_denied&code=4/x&state={STATE}", STATE)
    assert both.outcome == "refused"


IGNORED = {
    "a wrong state": ("GET", f"/?code=4/abc&state=x{STATE}"),
    "a state cut short": ("GET", f"/?code=4/abc&state={STATE[:-1]}"),
    "a state not in ASCII": ("GET", f"/?code=4/abc&state={STATE}%C3%A9"),
    "no state": ("GET", "/?code=4/abc"),
    "a blank state": ("GET", "/?code=4/abc&state="),
    "the state twice": ("GET", f"/?code=4/abc&state={STATE}&state={STATE}"),
    "the code twice": ("GET", f"/?code=4/abc&code=4/def&state={STATE}"),
    "a blank code": ("GET", f"/?code=&state={STATE}"),
    "neither code nor error": ("GET", f"/?state={STATE}&x=1"),
    "an error with a wrong state": ("GET", f"/?error=access_denied&state=x{STATE}"),
    "an error without a state": ("GET", "/?error=access_denied"),
    "another path": ("GET", f"/callback?code=4/abc&state={STATE}"),
    "the favicon": ("GET", "/favicon.ico"),
    "a path with a slash more": ("GET", f"//?code=4/abc&state={STATE}"),
    "an absolute target": ("GET", f"http://127.0.0.1:1/?code=4/abc&state={STATE}"),
    "another method": ("POST", f"/?code=4/abc&state={STATE}"),
    "PUT": ("PUT", f"/?code=4/abc&state={STATE}"),
    "HEAD": ("HEAD", f"/?code=4/abc&state={STATE}"),
    "a method nobody knows": ("C8X", f"/?code=4/abc&state={STATE}"),
}


@pytest.mark.parametrize("case", sorted(IGNORED))
def test_anything_else_is_not_the_answer_and_gets_the_one_fixed_page(case):
    method, target = IGNORED[case]
    decision = g.decide(method, target, STATE)
    assert decision.outcome == "ignored" and decision.code is None
    assert decision.page == g.NOT_THE_ANSWER
    assert decision.status in (400, 404, 405)


def test_the_pages_are_fixed_text_that_hold_nothing_from_a_request():
    marks = "unitMk"
    pages = {g.decide(m, t.replace("abc", marks), STATE).page for m, t in IGNORED.values()}
    pages.add(g.decide("GET", f"/?code={marks}&state={STATE}", STATE).page)
    pages.add(g.decide("GET", f"/?error={marks}&state={STATE}", STATE).page)
    assert pages == {g.ANSWERED, g.REFUSED, g.NOT_THE_ANSWER}
    for page in pages:
        assert isinstance(page, bytes) and marks.encode() not in page
        assert page.startswith(b"<!doctype html>") and b"<script" not in page
        assert b"src=" not in page and b"href=" not in page  # no resource that could see the URL
    # The answer's page claims nothing: the exchange has not happened when it is served.
    assert b"saved" not in g.ANSWERED and b"terminal" in g.ANSWERED


# The listener, on a socket of this machine


def raw(port: int, data: bytes, timeout: float = 5.0) -> bytes:
    """`data` sent as it is, and what comes back until the listener closes the connection."""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(data)
        got = b""
        while chunk := sock.recv(65536):
            got += chunk
        return got


def body_of(response: bytes) -> bytes:
    return response.split(b"\r\n\r\n", 1)[1]


def closed(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    except ConnectionRefusedError:
        return True
    return False


class Clock:
    """A monotonic clock a test moves: each call returns the next value, then the last."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def test_the_listener_binds_127_0_0_1_on_a_free_port_or_the_one_given():
    with g.Listener(STATE, deadline=time.monotonic() + 30) as listener:
        host, port = listener.server.server_address
        assert host == "127.0.0.1" and port == listener.port > 0
        with g.Listener(STATE, port=0, deadline=time.monotonic() + 30) as other:
            assert other.port != port
        with pytest.raises(OSError):  # the port is taken: the caller says so in one line
            g.Listener(STATE, port=port, deadline=time.monotonic() + 30)
    assert closed(port)


def test_only_the_answer_ends_the_wait_each_request_gets_one_fixed_reply_and_nothing_is_logged(
    capsys,
):
    mark = "unitMk7q"
    others = [
        f"GET /?code=4/{mark}&state=x{mark} HTTP/1.1\r\nHost: x\r\nX-Mark: {mark}\r\n\r\n",
        f"GET /{mark}?code=4/{mark}&state={STATE} HTTP/1.0\r\n\r\n",
        f"PUT /?code=4/{mark}&state={STATE} HTTP/1.1\r\nHost: x\r\n\r\n",
        f"C8{mark} /?{mark} HTTP/1.1\r\nHost: x\r\n\r\n",
        f"GET /?{mark} HTTP/9.{mark}\r\n\r\n",
        f"{mark}\r\n\r\n",
        f"GET /?{mark}{'x' * 70000} HTTP/1.1\r\n\r\n",
        f"GET /?{mark} HTTP/1.1\r\nX-Long: {'y' * 70000}\r\n\r\n",
    ]
    with g.Listener(STATE, deadline=time.monotonic() + 30) as listener:
        with ThreadPoolExecutor(1) as pool:
            waiting = pool.submit(listener.wait)
            replies = [raw(listener.port, request.encode()) for request in others]
            assert not waiting.done()
            answer = raw(
                listener.port, f"GET /?code=4%2Fc0de&state={STATE} HTTP/1.1\r\n\r\n".encode()
            )
            decision = waiting.result(timeout=10)
        assert decision.outcome == "answer" and decision.code == "4/c0de"
    assert closed(listener.port)
    for reply in replies:
        assert mark.encode() not in reply
        assert body_of(reply) == g.NOT_THE_ANSWER, reply[:200]
        assert reply.startswith(
            (
                b"HTTP/1.0 400 ",
                b"HTTP/1.0 404 ",
                b"HTTP/1.0 405 ",
                b"HTTP/1.0 414 ",
                b"HTTP/1.0 431 ",
            )
        )
    assert answer.startswith(b"HTTP/1.0 200 OK\r\n") and body_of(answer) == g.ANSWERED
    head = answer.split(b"\r\n\r\n", 1)[0].decode()
    for header in (
        "Content-Type: text/html; charset=utf-8",
        f"Content-Length: {len(g.ANSWERED)}",
        "Cache-Control: no-store",
        "Referrer-Policy: no-referrer",
        "Connection: close",
    ):
        assert f"\r\n{header}\r\n" in f"{head}\r\n", header
    assert capsys.readouterr() == ("", "")  # no request line, no error page, no traceback


def test_a_refusal_with_the_right_state_ends_the_wait():
    refusal = f"GET /?error=access_denied&state={STATE} HTTP/1.1\r\n\r\n".encode()
    with (
        g.Listener(STATE, deadline=time.monotonic() + 30) as listener,
        ThreadPoolExecutor(1) as pool,
    ):
        waiting = pool.submit(listener.wait)
        reply = raw(listener.port, refusal)
        decision = waiting.result(timeout=10)
    assert decision.outcome == "refused" and body_of(reply) == g.REFUSED


def test_nobody_answering_ends_the_wait_at_the_deadline_by_the_injected_clock():
    clock = Clock(0.0, 0.0, 299.9, 300.0)
    with g.Listener(STATE, deadline=300.0, clock=clock, poll=0.01) as listener:
        handle = listener.server.handle_request

        def handled() -> None:  # each socket wait is the poll at most, never the 300 s left
            assert listener.server.timeout <= 0.01, listener.server.timeout
            handle()

        listener.server.handle_request = handled
        assert listener.wait() is None
    assert clock.values == [300.0]  # the clock decided, not the wall: read to the deadline
    assert closed(listener.port)


def test_a_silent_client_cannot_hold_the_listener_past_the_deadline():
    clock = Ticking(0.1)
    with (
        g.Listener(STATE, deadline=1.0, clock=clock, poll=0.01) as listener,
        socket.create_connection(("127.0.0.1", listener.port)),  # connected, and silent
    ):
        assert listener.wait() is None
    assert clock.reads <= 1.0 / clock.step + 5  # held to the deadline, on the clock
    assert closed(listener.port)


class Ticking:
    """A monotonic clock that moves `step` seconds each time it is read, and counts reads."""

    def __init__(self, step: float) -> None:
        self.step, self.reads = step, 0

    def __call__(self) -> float:
        self.reads += 1
        return self.reads * self.step


class Dripper:
    """A client on this machine that sends `data` one byte every `every` seconds, on a thread,
    until the listener cuts it (a send fails) or it has sent everything; `sent` counts the
    bytes it got out. It is connected when made, so it is ahead of any later client."""

    def __init__(self, port: int, data: bytes, every: float = 0.005) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port))
        self.data, self.every, self.sent = data, every, 0
        self.thread = threading.Thread(target=self._drip, daemon=True)
        self.thread.start()

    def _drip(self) -> None:
        try:
            for byte in self.data:
                self.sock.sendall(bytes([byte]))
                self.sent += 1
                # The real TCP peer drips a bounded byte stream to exercise the read deadline.
                time.sleep(self.every)
        except OSError:
            pass  # the listener cut it

    def close(self) -> None:
        self.sock.close()
        self.thread.join(timeout=60)


ANSWER = f"GET /?code=4%2Fc0de&state={STATE} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode()
DRIP = b"GET /?" + b"a" * 4000  # a request line that never ends: 20 s at 5 ms a byte


def test_a_client_that_drips_its_request_is_cut_at_its_connections_own_deadline():
    """A client that sends a byte just before each read would time out held the old listener
    for a whole request line and a hundred header lines. Now a connection has CONNECTION_TIME
    in all on the injected clock, which moves 0.1 s at each read: the listener stops reading
    the dripper after about 20 reads and takes the answer queued behind it. The dripper gets
    out at most 200 of its 4000 bytes (the old listener read all of them, for 20 s)."""
    clock = Ticking(0.1)
    with g.Listener(STATE, deadline=1e6, clock=clock, poll=0.01) as listener:
        dripper = Dripper(listener.port, DRIP)
        try:
            with ThreadPoolExecutor(1) as pool:
                waiting = pool.submit(listener.wait)
                reply = raw(listener.port, ANSWER, timeout=30)  # 30 s: a hang guard, no more
                decision = waiting.result(timeout=30)  # a hang guard, no more
        finally:
            dripper.close()
    assert dripper.sent < len(DRIP), dripper.sent  # cut; when is the clock's to say (reads)
    assert decision.outcome == "answer" and body_of(reply) == g.ANSWERED
    assert 2.0 / clock.step <= clock.reads <= 2 * (2.0 / clock.step) + 10  # cut on the clock
    assert g.CONNECTION_TIME == 2.0


def test_a_client_that_drips_cannot_hold_the_listener_past_the_commands_deadline():
    """The reviewer's case: the command's deadline comes before the connection's, and wait()
    returns at it, whatever the client does. The old listener returned after the dripper had
    sent all of its 4000 bytes."""
    clock = Ticking(0.1)
    with g.Listener(STATE, deadline=1.0, clock=clock, poll=0.01) as listener:
        dripper = Dripper(listener.port, DRIP)
        try:
            assert listener.wait() is None
        finally:
            dripper.close()
    assert dripper.sent < len(DRIP), dripper.sent  # cut; when is the clock's to say (reads)
    assert 1.0 / clock.step <= clock.reads <= 1.0 / clock.step + 5  # held to it, on the clock


def test_idle_clients_ahead_of_the_answer_each_have_their_connections_deadline_and_no_more():
    """Connections are served one after another, so each idle client ahead of the browser
    delays the answer by at most CONNECTION_TIME on the clock (the old listener gave each 10 s
    of the wall). Four idle clients and the clock moving 0.25 s a read: the answer is taken
    within five connections' worth of reads."""
    clock = Ticking(0.25)
    with g.Listener(STATE, deadline=1e6, clock=clock, poll=0.01) as listener:
        idle = [socket.create_connection(("127.0.0.1", listener.port)) for _ in range(4)]
        try:
            with ThreadPoolExecutor(1) as pool:
                waiting = pool.submit(listener.wait)
                reply = raw(listener.port, ANSWER, timeout=30)  # 30 s: a hang guard, no more
                decision = waiting.result(timeout=30)  # a hang guard, no more
        finally:
            for sock in idle:
                sock.close()
    assert decision.outcome == "answer" and body_of(reply) == g.ANSWERED
    assert 4 * (2.0 / clock.step) <= clock.reads <= 5 * (2.0 / clock.step + 2) + 5, clock.reads


def test_the_listener_is_closed_when_the_wait_is_stopped():
    port = None
    with (
        pytest.raises(KeyboardInterrupt),
        g.Listener(STATE, deadline=time.monotonic() + 30) as listener,
    ):
        port = listener.port
        raise KeyboardInterrupt
    assert closed(port)


# The exchange

CLIENT = g.Client(CLIENT_ID, CLIENT_SECRET)
CODE = "4/unit-code-" + "Qp" * 10
VERIFIER = "unit-verifier-" + "v" * 40
REDIRECT = "http://127.0.0.1:9004"
REFRESH = "1//unit-refresh-" + "Rf" * 12
ACCESS = "ya29.unit-access-" + "Ac" * 12
BODY_MARK = "unitBodyMark"


def granted(*kinds: str, extra: dict | None = None) -> dict:
    scopes = [s for kind in kinds for s in g.SCOPES[kind]]
    answer = {
        "access_token": ACCESS,
        "expires_in": 3599,
        "token_type": "Bearer",
        "scope": " ".join([*reversed(scopes), "openid"]),
        "refresh_token": REFRESH,
        "refresh_token_expires_in": 604799,
        "id_token": "not.read.here",
    }
    answer.update(extra or {})
    return answer


def token_endpoint(status: int, payload: object, seen: list) -> "httpx.MockTransport":
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, bytes):
            return httpx.Response(status, content=payload, headers={"Content-Type": "text/html"})
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def exchanged(status: int, payload: object, kinds=("gmail",), seen=None) -> str:
    seen = [] if seen is None else seen

    async def go() -> str:
        async with httpx.AsyncClient(transport=token_endpoint(status, payload, seen)) as http:
            return await g.exchange(
                http, CLIENT, code=CODE, verifier=VERIFIER, redirect_uri=REDIRECT,
                kinds=list(kinds),
            )  # fmt: skip

    return asyncio.run(go())


def refusal(status: int, payload: object, kinds=("gmail",)) -> str:
    with pytest.raises(g.Refused) as refused:
        exchanged(status, payload, kinds)
    said = str(refused.value)
    assert refused.value.exit == 1
    assert "\n" not in said and "nothing was written" in said
    for secret in (CLIENT_SECRET, CODE, VERIFIER, REFRESH, ACCESS, BODY_MARK):
        assert secret not in said, secret
    return said


def test_the_exchange_is_one_post_of_exactly_the_form_with_nothing_in_the_url():
    seen: list = []
    assert exchanged(200, granted("gmail"), seen=seen) == REFRESH
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == g.GOOGLE_TOKEN_URL and request.url.query == b""
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.headers["accept"] == "application/json"
    form = parse_qsl(request.content.decode(), keep_blank_values=True, strict_parsing=True)
    assert sorted(form) == sorted(
        [
            ("grant_type", "authorization_code"),
            ("code", CODE),
            ("client_id", CLIENT_ID),
            ("client_secret", CLIENT_SECRET),
            ("redirect_uri", REDIRECT),
            ("code_verifier", VERIFIER),
        ]
    )
    for secret in (CLIENT_SECRET, CODE, VERIFIER):
        assert secret not in str(request.url)


def test_the_exchange_goes_to_the_clients_token_url():
    seen: list = []
    local = g.Client(CLIENT_ID, CLIENT_SECRET, token_url="http://127.0.0.1:9/token")

    async def go() -> str:
        async with httpx.AsyncClient(transport=token_endpoint(200, granted("gmail"), seen)) as http:
            return await g.exchange(
                http, local, code=CODE, verifier=VERIFIER, redirect_uri=REDIRECT, kinds=["gmail"]
            )

    assert asyncio.run(go()) == REFRESH
    assert str(seen[0].url) == "http://127.0.0.1:9/token"


HTML = b"<html>" + BODY_MARK.encode() + b" upstream trouble</html>"


def error(code: str) -> dict:
    return {"error": code, "error_description": f"{BODY_MARK} said", "error_uri": BODY_MARK}


def test_the_three_named_refusals_each_say_their_own_thing():
    said = {code: refusal(400, error(code)) for code in g.EXCHANGE_SAID}
    assert {"invalid_grant", "invalid_client", "redirect_uri_mismatch"} <= set(said)
    assert len(set(said.values())) == len(said)
    for code, line in said.items():
        assert line == f"{g.EXCHANGE_SAID[code]}; nothing was written"
    assert "run the command again" in said["invalid_grant"].lower()
    assert "Desktop app" in said["redirect_uri_mismatch"]
    assert refusal(500, error("invalid_grant")) == said["invalid_grant"]  # the code decides
    assert refusal(401, error("invalid_client")) == said["invalid_client"]


@pytest.mark.parametrize(
    ("status", "payload", "said"),
    [
        (400, error(f"{BODY_MARK}_unknown"), "Google refused the code exchange (HTTP 400)"),
        (400, {"error": ["invalid_grant"]}, "Google refused the code exchange (HTTP 400)"),
        (400, HTML, "Google refused the code exchange (HTTP 400)"),
        (500, HTML, "Google's token endpoint answered HTTP 500, a fault on its side"),
        (503, error(f"{BODY_MARK}_x"), "Google's token endpoint answered HTTP 503, a fault on its"
                                       " side"),
        (302, b"", "the token endpoint answered with a redirect (HTTP 302), which cairn does not"
                   " follow"),
        (200, HTML, "the token endpoint's answer is not the documented shape"),
        (200, [REFRESH], "the token endpoint's answer is not the documented shape"),
        (200, httpx.ConnectError(BODY_MARK), "cannot reach the token endpoint (ConnectError)"),
        (200, b"x" * (g.MAX_RESPONSE_BYTES + 1), "the token endpoint's answer is larger than"
                                                 " cairn reads"),
    ],
)  # fmt: skip
def test_any_other_refusal_is_one_line_in_cairns_words(status, payload, said):
    assert refusal(status, payload) == f"{said}; nothing was written"


@pytest.mark.parametrize(
    ("status", "said"),
    [
        (500, "the token endpoint the client file names answered HTTP 500, a fault on its side"),
        (400, "the token endpoint the client file names refused the code exchange (HTTP 400)"),
    ],
)
def test_a_token_endpoint_that_is_not_googles_is_not_called_googles(status, said):
    local = g.Client(CLIENT_ID, CLIENT_SECRET, token_url="http://127.0.0.1:9/token")

    async def go() -> str:
        async with httpx.AsyncClient(transport=token_endpoint(status, HTML, [])) as http:
            return await g.exchange(
                http, local, code=CODE, verifier=VERIFIER, redirect_uri=REDIRECT, kinds=["gmail"]
            )

    with pytest.raises(g.Refused) as refused:
        asyncio.run(go())
    assert str(refused.value) == f"{said}; nothing was written"
    assert "Google" not in str(refused.value)


@pytest.mark.parametrize("token", [None, "", 7, "short", "has a space " + "x" * 20])
def test_an_answer_without_a_refresh_token_says_so_and_what_to_do(token):
    answer = granted("gmail")
    if token is None:
        del answer["refresh_token"]
    else:
        answer["refresh_token"] = token
    said = refusal(200, answer)
    assert said == g.NO_REFRESH_TOKEN
    assert "refresh token" in said and "run the command again" in said


def test_a_scope_left_unticked_is_named_by_its_kind():
    said = refusal(200, granted("gmail"), kinds=("gmail", "google_drive"))
    assert said == g.NOT_GRANTED.format(kinds="google_drive")
    assert "may" in said
    both = refusal(200, granted("google_meet"), kinds=("gmail", "google_drive", "google_meet"))
    assert both == g.NOT_GRANTED.format(kinds="gmail and google_drive")
    no_scope = granted("gmail")
    del no_scope["scope"]
    assert refusal(200, no_scope) == g.NOT_GRANTED.format(kinds="gmail")


def test_scopes_granted_in_another_order_and_with_others_are_enough():
    answer = granted("gmail", "google_calendar")
    assert exchanged(200, answer, kinds=("google_calendar", "gmail")) == REFRESH


# The file


def test_the_credential_is_one_line_of_exactly_the_four_fields_and_loads_as_it_is():
    line = g.credential_line(CLIENT, REFRESH)
    assert "\n" not in line
    assert json.loads(line) == {
        "type": "authorized_user",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH,
    }
    credential = load_credential("V", {"V": line})
    assert isinstance(credential, AuthorizedUser)
    assert (credential.client_id, credential.client_secret, credential.refresh_token) == (
        CLIENT_ID, CLIENT_SECRET, REFRESH,
    )  # fmt: skip


@pytest.fixture
def no_umask():
    """The widest umask, as the acceptance test runs the command: the mode given is all."""
    old = os.umask(0)
    try:
        yield
    finally:
        os.umask(old)


def test_the_file_is_made_0600_in_one_step_and_never_wider(tmp_path, monkeypatch, no_umask):
    opened, chmods = [], []
    real_open, real_fchmod = os.open, os.fchmod

    def spy_open(path, flags, mode=0o777, **kw):
        opened.append((Path(path), flags, mode))
        return real_open(path, flags, mode, **kw)

    def spy_fchmod(fd, mode):
        chmods.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return real_fchmod(fd, mode)

    monkeypatch.setattr(g.os, "open", spy_open)
    monkeypatch.setattr(g.os, "fchmod", spy_fchmod)
    out = tmp_path / "ada.json"
    fd = g.reserve(out)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600  # from the moment it exists
    g.write_credential(fd, out, g.credential_line(CLIENT, REFRESH))
    ((path, flags, mode),) = opened
    assert path == out and mode == 0o600
    assert flags & os.O_CREAT and flags & os.O_EXCL and flags & os.O_WRONLY
    assert all(before & 0o077 == 0 for before in chmods)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert out.read_text(encoding="utf-8") == g.credential_line(CLIENT, REFRESH) + "\n"


def test_a_file_that_exists_is_never_overwritten(tmp_path):
    out = tmp_path / "ada.json"
    out.write_text("Brook's notes\n", encoding="utf-8")
    with pytest.raises(g.Refused) as refused:
        g.reserve(out)
    assert refused.value.exit == 1 and str(out) in str(refused.value)
    assert "never overwrites" in str(refused.value)
    assert out.read_text(encoding="utf-8") == "Brook's notes\n"
    link = tmp_path / "link.json"
    link.symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(g.Refused):
        g.reserve(link)
    assert not (tmp_path / "elsewhere.json").exists()


def test_a_write_that_fails_leaves_nothing_behind(tmp_path):
    out = tmp_path / "ada.json"
    os.close(g.reserve(out))
    read_only = os.open(out, os.O_RDONLY)
    with pytest.raises(g.Refused) as refused:
        g.write_credential(read_only, out, g.credential_line(CLIENT, REFRESH))
    assert str(refused.value) == f"cannot write {out}: Bad file descriptor; nothing was written"
    assert not out.exists()
    with pytest.raises(OSError):
        os.fstat(read_only)  # closed too
    fd = g.reserve(out)
    g.discard(fd, out)
    assert not out.exists()
    with pytest.raises(OSError):
        os.fstat(fd)


# The command, run in this process: what the acceptance file cannot wait for or see


class Lines(io.StringIO):
    """A stream that also hands each line written to it to a queue, for the browser."""

    def __init__(self) -> None:
        super().__init__()
        self.queue: queue.Queue = queue.Queue()

    def write(self, text: str) -> int:
        for line in text.splitlines():
            if line:
                self.queue.put(line)
        return super().write(text)


def options(tmp_path: Path, **override) -> argparse.Namespace:
    given = {
        "client": write_client(tmp_path, installed()),
        "kinds": ["gmail"],
        "out": tmp_path / "ada.json",
        "port": None,
        "wait": 30,
    }
    given.update(override)
    return argparse.Namespace(**given)


def the_url(lines: Lines) -> str:
    while lines.queue.get(timeout=10) != g.OPEN:
        pass
    return lines.queue.get(timeout=10)


def params(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query, strict_parsing=True))


def comes_back(url: str, query: str) -> bytes:
    port = urlsplit(params(url)["redirect_uri"]).port
    return raw(port, f"GET /?{query} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())


def run_command(args, *, answer=(200, None), browser=None, clock=time.monotonic):
    """authorize(args) on a thread, with the token endpoint answering `answer` and `browser`
    (given the URL) playing the person: (exit, stdout, stderr, requests to the endpoint)."""
    out, err, seen = Lines(), Lines(), []
    status, payload = answer
    transport = token_endpoint(status, granted("gmail") if payload is None else payload, seen)
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(
            g.authorize, args, clock=clock, http=lambda: httpx.AsyncClient(transport=transport),
            poll=0.05, stdout=out, stderr=err,
        )  # fmt: skip
        if browser is not None:
            browser(the_url(out))
        code = running.result(timeout=20)
    return code, out.getvalue(), err.getvalue(), seen


AUTH_HERE = "http://127.0.0.1:9/o/oauth2/v2/auth"
TOKEN_HERE = "http://localhost:9/token"
ELSEWHERE = {
    "token_uri": (
        {"token_uri": TOKEN_HERE},
        f"The client file's token_uri, {TOKEN_HERE}, is on this machine, not Google's: the"
        " client secret and the code will be sent there.",
    ),
    "auth_uri": (
        {"auth_uri": AUTH_HERE},
        f"The client file's auth_uri, {AUTH_HERE}, is on this machine, not Google's: the"
        " address below opens there, not at Google.",
    ),
    "both": (
        {"auth_uri": AUTH_HERE, "token_uri": TOKEN_HERE},
        f"The client file's auth_uri and token_uri, {AUTH_HERE} and {TOKEN_HERE}, are on this"
        " machine, not Google's: the address below opens there, and the client secret and the"
        " code will be sent there.",
    ),
}


@pytest.mark.parametrize("case", sorted(ELSEWHERE))
def test_an_address_that_is_not_googles_is_said_on_stdout_before_the_address(tmp_path, case):
    """A real client file from Google never names this machine: when one does, the person is
    told in one line, on stdout, before the line that begins with OPEN, which address and
    what goes there. Never the secret, never the code."""
    given, notice = ELSEWHERE[case]
    args = options(tmp_path)
    args.client.write_text(json.dumps(installed(**given)), encoding="utf-8")
    code, out, err, _ = run_command(args, clock=Clock(0.0, 0.0, 31.0))
    lines = out.splitlines()
    at = lines.index(g.OPEN)
    assert lines[:at] == [notice]
    assert code == 1 and CLIENT_SECRET not in out + err
    assert g.TIMEOUT[0].format(seconds=30) in err  # the notice is stdout's, never stderr's


def test_the_address_is_for_this_run_and_this_person_only():
    """Anyone who has the printed address can finish the flow with their own Google account
    before the person does, and cairn would then read that account: the line before it says
    not to pass it on, after the words the acceptance test reads."""
    assert g.OPEN.startswith("Open this address in your browser: ")
    assert "for this run and for you alone" in g.OPEN and "do not pass it on" in g.OPEN
    assert "\n" not in g.OPEN


def test_the_next_steps_say_to_check_the_account_that_signed_in():
    said = " ".join(g.DONE)
    assert "check that the Google account" in said and "is the one you meant" in said
    assert g.DONE[1].startswith("Next: put the file's one line")  # the step the guide pins


def test_help_says_the_address_is_not_to_be_passed_on_and_where_to_write_the_file():
    from cairn.web import cli

    command = cli._parser()._subparsers._group_actions[0].choices[g.COMMAND]
    shown = " ".join(command.format_help().split())
    assert "do not pass it on" in shown
    assert "a folder only you can write to" in shown


def test_googles_own_addresses_get_no_notice(tmp_path):
    _code, out, _err, _ = run_command(options(tmp_path), clock=Clock(0.0, 0.0, 31.0))
    assert out.splitlines()[0] == g.OPEN


def test_the_whole_flow_writes_the_credential_and_says_no_secret_anywhere(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)  # the root logger, and every logger that inherits it
    httpx_logger = logging.getLogger("httpx")
    before = httpx_logger.level
    httpx_logger.setLevel(logging.NOTSET)  # as in a process no source client has touched
    shown = {}

    def browser(url: str) -> None:
        shown["url"] = url
        shown["page"] = comes_back(url, f"state={params(url)['state']}&code={CODE}&scope=x")

    args = options(tmp_path)
    code, out, err, seen = run_command(args, browser=browser)
    assert (code, err) == (0, "")
    assert body_of(shown["page"]) == g.ANSWERED
    (request,) = seen
    form = dict(parse_qsl(request.content.decode()))
    verifier = form["code_verifier"]
    assert form["code"] == CODE and g.challenge(verifier) == params(shown["url"])["code_challenge"]
    assert stat.S_IMODE(args.out.stat().st_mode) == 0o600
    assert args.out.read_text(encoding="utf-8") == g.credential_line(CLIENT, REFRESH) + "\n"
    assert out.splitlines()[-len(g.DONE) :] == [line.format(out=args.out) for line in g.DONE]
    said = out + err + caplog.text + repr([r.__dict__ for r in caplog.records])
    for secret in (CLIENT_SECRET, CODE, verifier, ACCESS, REFRESH):
        assert secret not in said
    assert [r for r in caplog.records if r.name.startswith(("httpx", "httpcore"))] == []
    assert httpx_logger.level == logging.NOTSET  # put back afterwards
    httpx_logger.setLevel(before)


def test_nobody_answering_within_wait_is_exit_1_saying_so_and_the_listener_is_closed(tmp_path):
    """--wait's expiry, which the acceptance file cannot wait for: the injected clock passes
    the deadline at once."""
    clock = Clock(1000.0, 1000.0 + 31)
    args = options(tmp_path, wait=30)
    code, out, err, seen = run_command(args, clock=clock)
    assert code == 1
    assert err.splitlines() == [g.TIMEOUT[0].format(seconds=30), g.TIMEOUT[1]]
    url = out.splitlines()[out.splitlines().index(g.OPEN) + 1]
    assert closed(urlsplit(params(url)["redirect_uri"]).port)
    assert seen == [] and not args.out.exists()


def test_the_timeout_says_what_the_browser_may_show_and_asserts_nothing_unstated():
    said = " ".join(g.TIMEOUT)
    assert "may" in said
    for code in (
        "redirect_uri_mismatch",
        "org_internal",
        "deleted_client",
        "admin_policy_enforced",
    ):
        assert code in said  # each listed by the notes among the errors shown, not sent back
    notes = (REPO_ROOT / "docs" / "google-oauth-installed.md").read_text()
    for code in (
        "redirect_uri_mismatch",
        "org_internal",
        "deleted_client",
        "admin_policy_enforced",
    ):
        assert f"`{code}`" in notes
    assert "user-facing error messages instead of" in " ".join(notes.split())
    # What a person who is not a test user sees is not stated: the line says what to check,
    # not what the browser showed.
    assert "test user" in said and "not a test user" not in said


def test_a_refusal_with_the_right_state_is_exit_1_and_nothing_is_sent(tmp_path):
    def browser(url: str) -> None:
        comes_back(
            url, f"error=access_denied&error_description={BODY_MARK}&state={params(url)['state']}"
        )

    args = options(tmp_path)
    code, out, err, seen = run_command(args, browser=browser)
    assert (code, err) == (1, g.REFUSED_SAID + "\n")
    assert BODY_MARK not in out + err and "access_denied" not in err
    assert seen == [] and not args.out.exists()


def test_an_out_that_appears_while_it_waits_is_not_overwritten_and_the_code_is_not_spent(tmp_path):
    args = options(tmp_path)

    def browser(url: str) -> None:
        args.out.write_text("Brook's file\n", encoding="utf-8")
        comes_back(url, f"code={CODE}&state={params(url)['state']}")

    code, _out, err, seen = run_command(args, browser=browser)
    assert code == 1 and "never overwrites" in err and len(err.splitlines()) == 1
    assert args.out.read_text(encoding="utf-8") == "Brook's file\n"
    assert seen == []


def test_a_refused_exchange_leaves_no_file(tmp_path):
    args = options(tmp_path)

    def browser(url: str) -> None:
        comes_back(url, f"code={CODE}&state={params(url)['state']}")

    code, _out, err, seen = run_command(args, answer=(400, error("invalid_grant")), browser=browser)
    assert (code, err) == (1, f"{g.EXCHANGE_SAID['invalid_grant']}; nothing was written\n")
    assert len(seen) == 1 and list(tmp_path.glob("ada*")) == []


def test_the_whole_exchange_is_bounded_and_running_out_of_time_is_said_in_words(
    tmp_path, monkeypatch
):
    """The client's timeout is per read, so a token endpoint that sends a byte now and then
    held the command, with the empty reserved file on disk, for as long as it liked. The
    exchange as a whole has EXCHANGE_TIME, the 30 seconds a source's request has; here it is
    made 0.5 s, and the endpoint drips 100 bytes at 50 ms each (5 s, ten times the bound)."""
    monkeypatch.setattr(g, "EXCHANGE_TIME", 0.5, raising=False)
    args = options(tmp_path)

    async def drip():
        for _ in range(100):
            # The endpoint must send some bytes before the total deadline expires.
            await asyncio.sleep(0.05)
            yield b" "

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=drip())

    out, err = Lines(), Lines()
    transport = httpx.MockTransport(handler)
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(
            g.authorize, args, http=lambda: httpx.AsyncClient(transport=transport),
            poll=0.05, stdout=out, stderr=err,
        )  # fmt: skip
        url = the_url(out)
        comes_back(url, f"code={CODE}&state={params(url)['state']}")
        code = running.result(timeout=20)  # a hang guard, no more
    assert code == 1  # and the line below: cut at 0.5 s, not read to the end of the drip
    assert err.getvalue() == (
        "the token endpoint did not answer within 0.5 seconds; nothing was written\n"
    )
    assert not args.out.exists()


def test_the_exchanges_bound_is_a_sources_requests():
    assert g.EXCHANGE_TIME == g.SOURCE_TIMEOUT == 30.0


def test_refusals_before_listening_print_nothing_on_stdout_and_bind_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "Listener", lambda *a, **k: pytest.fail("it listened"))
    (tmp_path / "taken.json").write_text("x", encoding="utf-8")
    cases = {
        "taken.json": (options(tmp_path, out=tmp_path / "taken.json"), "already exists"),
        "nowhere": (options(tmp_path, out=tmp_path / "nowhere" / "a.json"), "no folder"),
        "client": (options(tmp_path, client=tmp_path / "none.json"), "no client file"),
    }
    for named, (args, said) in cases.items():
        code, out, err, _ = run_command(args)
        assert (code, out) == (2, ""), named
        assert len(err.splitlines()) == 1 and said in err and named in err
    assert (tmp_path / "taken.json").read_text(encoding="utf-8") == "x"


def test_a_port_in_use_is_one_line_with_exit_2(tmp_path):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        code, out, err, _ = run_command(options(tmp_path, port=port))
    assert (code, out) == (2, "")
    assert err.startswith(f"cannot listen on 127.0.0.1:{port}: ") and len(err.splitlines()) == 1


def test_ctrl_c_while_it_waits_closes_the_listener_and_writes_nothing(tmp_path):
    calls = []

    def clock() -> float:
        calls.append(1)
        if len(calls) > 2:
            raise KeyboardInterrupt
        return 0.0

    args = options(tmp_path)
    code, out, err, seen = run_command(args, clock=clock)
    assert (code, err) == (130, g.STOPPED + "\n")
    url = out.splitlines()[out.splitlines().index(g.OPEN) + 1]
    assert closed(urlsplit(params(url)["redirect_uri"]).port)
    assert seen == [] and not args.out.exists()


# Whatever ends the command before the credential is written removes the file it reserved,
# and nothing it can be made to do ends in a traceback


def browsing(out: Lines) -> threading.Thread:
    """The person's browser on a thread: it comes back with the code once the URL is shown."""

    def go() -> None:
        url = the_url(out)
        comes_back(url, f"code={CODE}&state={params(url)['state']}")

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    return thread


class Escaped(Exception):
    """Raised by the handler a test puts in place before the command: if the command did not
    put its own in place, the signal ends the test in this exception, not the test run."""


@pytest.fixture
def signals_held():
    """SIGTERM and SIGHUP handled by a handler of the test's while it runs (put back after),
    so a command that does not handle them raises Escaped instead of ending pytest."""

    def escape(signum: int, frame: object) -> None:
        raise Escaped(signum)

    before = {s: signal.signal(s, escape) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield escape
    finally:
        for s, handler in before.items():
            signal.signal(s, handler)


def in_main_thread(args, handler) -> tuple[int, str, str]:
    """authorize(args) in this, the main thread, with the browser on another and the token
    endpoint answering through `handler`: (exit, stdout, stderr)."""
    out, err = Lines(), Lines()
    browser = browsing(out)
    code = g.authorize(
        args, http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)), poll=0.05,
        stdout=out, stderr=err,
    )  # fmt: skip
    browser.join(timeout=10)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize(("name", "exit"), [("SIGTERM", 143), ("SIGHUP", 129)])
def test_sigterm_and_sighup_end_it_as_ctrl_c_does_and_remove_the_file(
    tmp_path, signals_held, name, exit
):
    """The signal comes while the code is being exchanged, with the file reserved: exit 128
    plus the signal's number, STOPPED, the file removed, and the handlers in place before are
    put back."""
    number = getattr(signal, name)
    args = options(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert args.out.exists()  # reserved
        signal.raise_signal(number)
        return httpx.Response(200, json=granted("gmail"))

    code, _out, err = in_main_thread(args, handler)
    assert (code, err) == (exit, g.STOPPED + "\n")
    assert not args.out.exists()
    assert signal.getsignal(signal.SIGTERM) is signals_held
    assert signal.getsignal(signal.SIGHUP) is signals_held


def test_a_signal_ignored_before_it_runs_stays_ignored(tmp_path, signals_held):
    """nohup's SIGHUP: ignored before, so ignored while it runs, and the run goes on."""
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    args = options(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        signal.raise_signal(signal.SIGHUP)
        return httpx.Response(200, json=granted("gmail"))

    code, _out, err = in_main_thread(args, handler)
    assert (code, err) == (0, "")
    assert args.out.read_text(encoding="utf-8") == g.credential_line(CLIENT, REFRESH) + "\n"
    assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN


def test_ctrl_c_during_the_write_removes_the_file(tmp_path, monkeypatch):
    """The descriptor stays the command's until the write has returned: a Ctrl-C half way
    through it leaves no file holding half a credential."""

    def half(fd: int, out: Path, line: str) -> None:
        os.write(fd, line[:10].encode())
        raise KeyboardInterrupt

    monkeypatch.setattr(g, "write_credential", half)
    args = options(tmp_path)

    def browser(url: str) -> None:
        comes_back(url, f"code={CODE}&state={params(url)['state']}")

    code, _out, err, seen = run_command(args, browser=browser)
    assert (code, err) == (130, g.STOPPED + "\n")
    assert len(seen) == 1 and not args.out.exists()


class Unforeseen(BaseException):
    """Not an Exception: it goes on up, and the file is removed on its way."""


def test_an_exception_that_is_not_an_error_still_removes_the_file(tmp_path):
    def factory() -> httpx.AsyncClient:
        raise Unforeseen

    args = options(tmp_path)
    out, err = Lines(), Lines()
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(g.authorize, args, http=factory, poll=0.05, stdout=out, stderr=err)
        url = the_url(out)
        comes_back(url, f"code={CODE}&state={params(url)['state']}")
        with pytest.raises(Unforeseen):
            running.result(timeout=20)
    assert not args.out.exists()


# Each step made to raise what it was never meant to: one line naming the error's class
# (never its text), exit 1, the listener closed and no file left. The second value says
# whether the step comes after the browser is back.
STEPS = {
    "read_client": ("read_client", False),
    "Listener": ("Listener", False),
    "authorization_url": ("authorization_url", False),
    "wait": ("Listener.wait", False),
    "reserve": ("reserve", True),
    "the client": ("http", True),
    "the transport": ("transport", True),
    "exchange": ("exchange", True),
    "credential_line": ("credential_line", True),
    "write_credential": ("write_credential", True),
}


@pytest.mark.parametrize("step", sorted(STEPS))
def test_an_unexpected_error_at_any_step_is_one_line_naming_its_class_and_leaves_no_file(
    tmp_path, monkeypatch, step
):
    where, browsed = STEPS[step]

    def boom(*args: object, **kw: object):
        raise RuntimeError(f"{BODY_MARK} {CLIENT_SECRET}")

    async def aboom(*args: object, **kw: object):
        boom()

    def http() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=token_endpoint(200, granted("gmail"), []))

    if where == "Listener.wait":
        monkeypatch.setattr(g.Listener, "wait", boom)
    elif where == "http":
        http = boom
    elif where == "transport":

        def http() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(boom))

    elif where == "exchange":
        monkeypatch.setattr(g, "exchange", aboom)
    else:
        monkeypatch.setattr(g, where, boom)
    args = options(tmp_path)
    out, err = Lines(), Lines()
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(g.authorize, args, http=http, poll=0.05, stdout=out, stderr=err)
        if browsed:
            url = the_url(out)
            comes_back(url, f"code={CODE}&state={params(url)['state']}")
        code = running.result(timeout=20)
    said = err.getvalue()
    assert (code, said) == (1, g.UNEXPECTED.format(error="RuntimeError", then=g.NOTHING) + "\n")
    assert BODY_MARK not in said and CLIENT_SECRET not in said
    assert not args.out.exists()
    shown = out.getvalue().splitlines()
    if g.OPEN in shown:
        assert closed(urlsplit(params(shown[shown.index(g.OPEN) + 1])["redirect_uri"]).port)


def test_a_folder_it_may_not_look_into_is_one_line_with_exit_2(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0)
    try:
        code, out, err, _ = run_command(options(tmp_path, out=locked / "sub" / "ada.json"))
    finally:
        os.chmod(locked, 0o700)
    assert (code, out) == (2, "")
    assert err == f"cannot look into the folder {locked / 'sub'}: Permission denied\n"


class Broken(io.StringIO):
    """A stream whose reader has gone: every write fails, as a closed pipe's does."""

    def write(self, text: str) -> int:
        raise BrokenPipeError(32, "Broken pipe")


def test_stdout_that_cannot_be_written_is_a_refusal_in_words(tmp_path):
    args = options(tmp_path)
    err = Lines()
    code = g.authorize(args, poll=0.05, stdout=Broken(), stderr=err)
    assert (code, err.getvalue()) == (
        1,
        "cannot print on stdout (BrokenPipeError), so the address cannot be shown; nothing"
        " was written\n",
    )
    assert not args.out.exists()


def test_stdout_gone_after_the_write_keeps_the_credential_and_says_where_it_is(tmp_path):
    args = options(tmp_path)
    lines = Lines()

    class GoesAway(io.TextIOBase):
        def write(self, text: str) -> int:
            if text.startswith("Wrote"):
                raise BrokenPipeError(32, "Broken pipe")
            return lines.write(text)

    err = io.StringIO()
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(
            g.authorize, args,
            http=lambda: httpx.AsyncClient(transport=token_endpoint(200, granted("gmail"), [])),
            poll=0.05, stdout=GoesAway(), stderr=err,
        )  # fmt: skip
        url = the_url(lines)
        comes_back(url, f"code={CODE}&state={params(url)['state']}")
        code = running.result(timeout=20)
    assert (code, err.getvalue()) == (
        1,
        "cannot print on stdout (BrokenPipeError), so the next steps cannot be shown; the"
        f" credential is in {args.out}\n",
    )
    assert args.out.read_text(encoding="utf-8") == g.credential_line(CLIENT, REFRESH) + "\n"


def test_no_stdout_at_all_is_a_refusal_not_a_silent_wait(tmp_path, monkeypatch):
    """`>&-` leaves Python with no sys.stdout: print would drop the address without a word
    and the command would wait out --wait for a browser nobody can send."""
    monkeypatch.setattr(sys, "stdout", None)
    waited: list[object] = []
    monkeypatch.setattr(g.Listener, "wait", lambda listener: waited.append(listener))
    args = options(tmp_path)
    err = Lines()
    code = g.authorize(args, poll=0.05, stderr=err)
    assert waited == []  # refused before the wait for a browser, not after it
    assert (code, err.getvalue()) == (
        1,
        "there is no stdout, so the address cannot be shown; nothing was written\n",
    )
    assert not args.out.exists()


def test_a_stdout_pipe_whose_reader_has_gone_is_exit_1_and_one_line_as_a_process(tmp_path):
    """As a person's shell runs it: the reader of stdout is gone before the address is
    printed. One line on stderr and exit 1, not the interpreter's exit 120 and its
    "Exception ignored" when it flushes stdout on the way out."""
    args = options(tmp_path)
    source = str(Path(g.__file__).resolve().parents[2])
    env = {k: v for k, v in os.environ.items() if not k.startswith("CAIRN_")}
    env["PYTHONPATH"] = source
    process = subprocess.Popen(
        [sys.executable, "-m", "cairn.server", "google-authorize", "--client", str(args.client),
         "--kind", "gmail", "--out", str(args.out), "--wait", "30"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )  # fmt: skip
    process.stdout.close()
    try:
        _, said = process.communicate(timeout=20)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 1, said
    assert said.decode() == (
        "cannot print on stdout (BrokenPipeError), so the address cannot be shown; nothing"
        " was written\n"
    )
    assert not args.out.exists()


def test_a_refusal_on_a_stderr_that_cannot_be_written_still_exits_with_its_code(tmp_path):
    args = options(tmp_path, out=tmp_path / "nowhere" / "a.json")
    assert g.authorize(args, stdout=io.StringIO(), stderr=Broken()) == 2


def test_a_path_the_terminal_cannot_show_is_escaped_not_a_traceback(tmp_path):
    """A stdout that writes ASCII only, and --out in a folder whose name has an é: the next
    steps name the file with the é escaped, once the credential is written."""
    folder = tmp_path / "café"
    folder.mkdir()
    args = options(tmp_path, out=folder / "ada.json")
    raw_out = io.BytesIO()
    ascii_out = io.TextIOWrapper(raw_out, encoding="ascii", write_through=True)
    lines = Lines()

    class Both(io.TextIOBase):
        def write(self, text: str) -> int:
            written = ascii_out.write(text)
            lines.write(text)
            return written

    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(
            g.authorize, args,
            http=lambda: httpx.AsyncClient(transport=token_endpoint(200, granted("gmail"), [])),
            poll=0.05, stdout=Both(), stderr=io.StringIO(),
        )  # fmt: skip
        url = the_url(lines)
        comes_back(url, f"code={CODE}&state={params(url)['state']}")
        code = running.result(timeout=20)
    assert code == 0
    assert args.out.read_text(encoding="utf-8") == g.credential_line(CLIENT, REFRESH) + "\n"
    assert "caf\\xe9" in raw_out.getvalue().decode("ascii")


# The client file's values, and an address on this machine cairn cannot use

CLIENT_VALUES = {
    "a lone surrogate in client_id": ("client_id", "cid\ud800"),
    "a lone surrogate in client_secret": ("client_secret", "SECRET\ud800VALUE"),
    "a letter that is not ASCII": ("client_id", "cidé.apps.example.test"),
    "a control character": ("client_secret", "SECRET\x7fVALUE"),
    "too long": ("client_secret", "s" * 257),
}


@pytest.mark.parametrize("case", sorted(CLIENT_VALUES))
def test_client_values_are_printable_ascii_of_a_sane_length(tmp_path, case):
    name, value = CLIENT_VALUES[case]
    path = tmp_path / "client.json"
    path.write_text(json.dumps(installed(**{name: value})), encoding="utf-8")
    with pytest.raises(g.Refused) as refused:
        g.read_client(path)
    said = str(refused.value)
    assert refused.value.exit == 2 and "\n" not in said
    assert name in said and str(path) in said and value not in said
    assert "printable ASCII" in said


def test_the_bounds_of_a_client_value():
    assert g.CLIENT_VALUE.pattern == r"[\x21-\x7e]{1,256}"
    assert g.CLIENT_VALUE.fullmatch("x" * 256) and not g.CLIENT_VALUE.fullmatch("x" * 257)


LOCAL_UNUSABLE = {
    "token_uri too long": ("token_uri", "http://127.0.0.1:9/" + "a" * 70000),
    "auth_uri too long": ("auth_uri", "http://localhost:9/" + "a" * 2100),
    "token_uri not ASCII": ("token_uri", "http://localhost:9/té"),
}


@pytest.mark.parametrize("case", sorted(LOCAL_UNUSABLE))
def test_an_address_on_this_machine_cairn_cannot_use_is_refused_like_the_files_other_faults(
    tmp_path, case
):
    name, value = LOCAL_UNUSABLE[case]
    path = write_client(tmp_path, installed(**{name: value}))
    with pytest.raises(g.Refused) as refused:
        g.read_client(path)
    said = str(refused.value)
    assert refused.value.exit == 2 and "\n" not in said
    assert name in said and str(path) in said
    assert "aaaaaaaaaa" not in said and "é" not in said
    assert g.MAX_ADDRESS == 2048


def test_an_address_httpx_cannot_send_to_is_refused(tmp_path, monkeypatch):
    def refuse(url: str) -> None:
        raise httpx.InvalidURL("no")

    monkeypatch.setattr(g.httpx, "URL", refuse)
    path = write_client(tmp_path, installed(token_uri="http://127.0.0.1:9/token"))
    with pytest.raises(g.Refused) as refused:
        g.read_client(path)
    assert "token_uri" in str(refused.value) and refused.value.exit == 2


def test_the_reviewers_client_files_end_in_one_line_with_exit_2(tmp_path):
    """The three files that ended in a traceback: a lone surrogate in client_id or in
    client_secret, and a loopback token_uri of 70,000 characters. Each is refused before
    anything listens."""
    for name, value in [
        ("client_id", "cid\ud800"),
        ("client_secret", "SECRET\ud800VALUE"),
        ("token_uri", "http://127.0.0.1:9/" + "a" * 70000),
    ]:
        args = options(tmp_path, client=tmp_path / f"{name}.json")
        args.client.write_text(json.dumps(installed(**{name: value})), encoding="utf-8")
        code, out, err, _ = run_command(args)
        assert (code, out) == (2, ""), name
        assert len(err.splitlines()) == 1 and name in err
        assert not args.out.exists()
