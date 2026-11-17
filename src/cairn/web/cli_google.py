"""`google-authorize` (C8): the command that obtains one person's Google credential, an
`authorized_user` document holding a refresh token, for cairn's Google sources.

Contract (frozen by tests/acceptance/test_c8_google_authorize.py; Google's request and
response shapes for an installed, "Desktop app" client are recorded in
docs/google-oauth-installed.md, with what Google's pages do not state marked as such and what
cairn decided marked **[cairn]**; the expiry of --wait and the logs are pinned by
tests/unit/test_cli_google_unit.py).

The command
- `python -m cairn.server google-authorize --client FILE --kind KIND [--kind KIND]... --out
  FILE [--port PORT] [--wait SECONDS]`, run by the person on the machine whose browser they
  will use: Google sends that browser back to a listener on this machine. It opens no
  database and takes no --db.
- --client: the JSON file the Cloud console gives for a "Desktop app" client, whose one
  top-level key `installed` holds `client_id` and `client_secret` (read_client). A `web`
  client is refused with a sentence that says to create a Desktop app client; a file that is
  missing, not JSON or lacks either value: exit 2, one line on stderr that names the file and
  what is wrong, never a value from it. The same for a value that is not printable ASCII
  without spaces of 1 to 256 characters (CLIENT_VALUE; the console's are about 75 and 35),
  and for an `auth_uri` or `token_uri` on this machine that cairn cannot use (past
  MAX_ADDRESS, 2048 characters, not ASCII, or not a URL httpx takes as that same host).
- --kind: gmail, google_calendar, google_drive or google_meet (KIND_NAMES), given once per
  kind. The scopes asked for are those kinds' own (SCOPES, read from each kind's module),
  each once, in the order of the kinds' names, joined by spaces. Another kind: exit 2.
- --out: a new file in a folder that exists. A path that exists (a link included), a folder
  that does not or one it may not look into: exit 2 before anything else, nothing listens
  and nothing is printed on stdout. A file that appears there while the command waits is
  never overwritten either.
- --port: the port on 127.0.0.1 (1 to 65535; a free one when left out; one in use: exit 2,
  one line). --wait: 30 to 900 seconds (WAIT, 300); anything else: exit 2.

What it does, in order
1. It checks --out and the client file, then listens on 127.0.0.1 at the port (Listener).
2. On stdout: OPEN (which says the address is for this run and this person only, as anyone
   who has it can finish the flow with their own account first), then the authorization URL
   alone (authorization_url): the authorization
   endpoint with exactly client_id, redirect_uri (`http://127.0.0.1:<port>`), response_type
   `code`, scope, state (32 bytes from `secrets`, new each run), code_challenge (the S256 of
   a verifier of 32 bytes from `secrets` in base64url, 43 characters) and
   code_challenge_method `S256`, access_type `offline` and prompt `consent`, each once; then
   a line saying how long it waits.
3. It waits (Listener.wait, decide). Only a GET of "/" whose query holds the state once is
   Google's redirect: with one non-empty `code` (and no `error`) the answer, with `error` a
   refusal (exit 1, REFUSED_SAID, nothing sent). Every other request (another state or none,
   another path, another method, a malformed request) gets NOT_THE_ANSWER and changes
   nothing. Each connection is one request, one of three fixed pages and closed; the
   standard library's error pages and request log, which repeat what a request held, are
   replaced. Connections are served one at a time, and each has one deadline in all:
   CONNECTION_TIME (2 seconds, as a browser sends its request at once) after it is taken,
   never past the command's. The request head is read with what is left set before every
   read, until its blank line, MAX_HEAD bytes or the deadline, and only then parsed; a
   connection still sending at its deadline is closed without an answer. So a client that
   sends nothing, or one byte at a time, delays the browser's answer by CONNECTION_TIME at
   most, and the wait ends at the command's deadline whatever clients do.
   The listener is closed once the answer is in, and on every other way out, Ctrl-C (exit
   130, STOPPED) included.
4. It makes --out (reserve: os.open with O_CREAT | O_EXCL and mode 0600, so the file is never
   readable by others, not even for a moment), then exchanges the code (exchange): one POST
   to the token endpoint, a form of exactly grant_type `authorization_code`, code, client_id,
   client_secret, redirect_uri (the URL's) and code_verifier, nothing in the URL's query.
5. The answer must hold a `refresh_token` and a `scope` holding every scope asked for
   (in any order, with others): otherwise exit 1, NO_REFRESH_TOKEN or NOT_GRANTED naming
   the kinds whose scope is missing. A refusal: exit 1 and one line chosen by the `error`
   code alone (EXCHANGE_SAID; invalid_grant, invalid_client and redirect_uri_mismatch each
   have their own), else by the status (a redirect, a 5xx, another refusal), never by the
   body.
6. It writes the document on one line (credential_line), exactly `type` "authorized_user",
   `client_id`, `client_secret` and `refresh_token`, as cairn.sources.google_auth's
   load_credential loads it, and prints DONE: put that line in the server's environment
   under the name the source's config gives as `credentials_env`, then delete the file, and
   first check that the Google account signed in with is the one meant. Exit 0. The file can
   be swapped by anyone who can write to its folder once it is written: --help says to give
   --out a folder only the person can write to, and nothing is refused over it.
7. Nobody answers within --wait: exit 1, TIMEOUT, which says so and what the browser may
   have shown instead (the errors Google's pages say are shown to the person and not sent
   back).

How it ends early (authorize)
- Whatever ends the command before the credential is written removes the file it reserved:
  a refusal, Ctrl-C (exit 130), SIGTERM (143) or SIGHUP (129), and any other exception on
  its way up. The descriptor stays the command's until the write has returned, so a stop
  during the write leaves no half a credential.
- SIGTERM and SIGHUP end it as Ctrl-C does (Signalled, exit 128 plus the signal's number;
  STOPPED) while it runs, and the handlers in place before are put back after. They are set
  only from the main thread, and a signal ignored before (nohup's SIGHUP) stays ignored.
- Nothing ends in a traceback. A stdout that cannot be written to, or none at all (`>&-`),
  is a refusal (exit 1), and the interpreter's flush on the way out does not turn it into
  exit 120 (run); a stderr that cannot, the exit code alone; a path the terminal cannot
  encode is shown escaped. An error nobody foresaw is still an error: exit 1, UNEXPECTED,
  one line naming its class and never its text.

Where it sends what
- The authorization endpoint is GOOGLE_AUTH_URL and the token endpoint GOOGLE_TOKEN_URL,
  whatever the client file's `auth_uri` and `token_uri` say, except that either on this
  machine (http, host 127.0.0.1 or localhost, no user, query or fragment) is used as it is,
  for tests and stand-ins (endpoint). Look-alikes are other hosts. When either is used, the
  command says so first, in one line on stdout before OPEN (not_googles): which address is
  not Google's and what goes there (the client secret and the code, for the token_uri),
  never the secret or the code. A refusal by such a token endpoint is never said to be
  Google's.
- The client secret, the code, the verifier and the tokens go only to the token endpoint, in
  the form, through the sources' client (cairn.sources.runner.source_client: a timeout, no
  redirect followed). The exchange as a whole, opening and closing the client included,
  has EXCHANGE_TIME (SOURCE_TIMEOUT, 30 seconds, as a source's request has): past it, exit
  1, one line saying the token endpoint did not answer in time, and the file is removed.
  None of them is printed, logged or written anywhere but --out; the "httpx" and "httpcore"
  loggers are held at WARNING while the exchange runs, as the sources hold them. The URL
  holds the client id, the state and the challenge, which are not secrets.
"""

import argparse
import asyncio
import contextlib
import hashlib
import http
import http.server
import io
import json
import logging
import os
import re
import secrets
import signal
import socket
import socketserver
import sys
import threading
import time
from base64 import urlsafe_b64encode
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import httpx

from cairn.sources import gmail, google_calendar, google_drive, google_meet
from cairn.sources.google_api import TooLarge, Unreachable, fetch
from cairn.sources.google_auth import GOOGLE_TOKEN_URL, MAX_RESPONSE_BYTES, TOKEN
from cairn.sources.runner import CLIENT_LOGGERS, SOURCE_TIMEOUT, source_client

COMMAND = "google-authorize"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
# The kinds the command takes, and the scopes each asks Google for, from the kind's own module.
SCOPES: dict[str, list[str]] = {
    "gmail": gmail.SCOPES,
    "google_calendar": google_calendar.SCOPES,
    "google_drive": google_drive.SCOPES,
    "google_meet": google_meet.SCOPES,
}
KIND_NAMES = tuple(sorted(SCOPES))
WAIT, WAIT_MIN, WAIT_MAX = 300, 30, 900  # seconds the command waits for the person
RANDOM_BYTES = 32  # of the state and of the verifier: 256 bits each
POLL = 1.0  # seconds the listener waits on a socket before it looks at the clock again
# Seconds one connection has, in all, to send its request head: a browser sends its request at
# once, so two seconds leave room for a loaded machine and still let no client hold the
# listener, which serves one connection at a time, for long.
CONNECTION_TIME = 2.0
MAX_HEAD = 65536  # bytes of a request head (its line and its headers), at most
# A client file's client_id and client_secret: printable ASCII without spaces, as the Cloud
# console gives them (an ID of about 75 characters, a secret of about 35), at most 256.
MAX_CLIENT_VALUE = 256
CLIENT_VALUE = re.compile(r"[\x21-\x7e]{1,256}")
MAX_ADDRESS = 2048  # characters of an auth_uri or token_uri on this machine, at most


class Refused(Exception):
    """The command stops here: the message is one line for stderr, `exit` the exit code."""

    def __init__(self, message: str, exit: int = 1) -> None:
        super().__init__(message)
        self.exit = exit


# The client file


@dataclass(frozen=True)
class Client:
    """A Desktop app client, and the two addresses the command uses for it."""

    client_id: str
    client_secret: str = field(repr=False)
    auth_url: str = GOOGLE_AUTH_URL
    token_url: str = GOOGLE_TOKEN_URL


def read_client(path: Path) -> Client:
    """The client in the file the Cloud console gives for a Desktop app client, or Refused
    (exit 2) in one line that names the file and what is wrong, never a value from it."""

    def wrong(what: str) -> Refused:
        return Refused(f"the client file {path} {what}", 2)

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Refused(
            f"there is no client file at {path}: --client names the JSON file the Cloud"
            " console gives for a Desktop app client",
            2,
        ) from None
    except UnicodeDecodeError:
        raise wrong("is not JSON: give the file as the Cloud console gave it") from None
    except OSError as error:
        raise Refused(f"cannot read the client file {path}: {error.strerror}", 2) from None
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        raise wrong("is not JSON: give the file as the Cloud console gave it") from None
    if not isinstance(document, dict):
        raise wrong("does not hold a JSON object: give the file as the Cloud console gave it")
    client = document.get("installed")
    if client is None and "web" in document:
        raise wrong(
            "is a Web application client's: create a Desktop app client in the Cloud console"
            " and give its file"
        )
    if not isinstance(client, dict):
        raise wrong('has no "installed" client: give the file of a Desktop app client')
    for name in ("client_id", "client_secret"):
        value = client.get(name)
        if not isinstance(value, str) or not value:
            raise wrong(f"has no {name} in its installed client: download the file again")
        if not CLIENT_VALUE.fullmatch(value):
            raise wrong(
                f"has a {name} that is not what the Cloud console gives (printable ASCII"
                f" without spaces, at most {MAX_CLIENT_VALUE} characters): download the file"
                " again"
            )
    addresses = {}
    for name, google in (("auth_uri", GOOGLE_AUTH_URL), ("token_uri", GOOGLE_TOKEN_URL)):
        try:
            addresses[name] = endpoint(client.get(name), google)
        except ValueError:
            raise wrong(
                f"has a {name} on this machine that cairn cannot use: give an http address on"
                f" 127.0.0.1 or localhost in printable ASCII, of at most {MAX_ADDRESS}"
                " characters, or leave it out"
            ) from None
    return Client(
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        auth_url=addresses["auth_uri"],
        token_url=addresses["token_uri"],
    )


def endpoint(given: object, google: str) -> str:
    """`given` when it is an address on this machine over http (host 127.0.0.1 or localhost,
    no user, query or fragment), for tests and stand-ins; else Google's own, `google`. An
    address on this machine that cairn cannot use (past MAX_ADDRESS, not ASCII, or one httpx
    does not take as that same host) is a ValueError, for the caller to refuse."""
    if not isinstance(given, str) or any(c.isspace() or not c.isprintable() for c in given):
        return google
    try:
        parts = urlsplit(given)
        host = parts.hostname
        parts.port  # noqa: B018 (a port out of range is a ValueError)
    except ValueError:
        return google
    if parts.scheme != "http" or host not in LOCAL_HOSTS or "@" in parts.netloc:
        return google
    if "?" in given or "#" in given:
        return google
    if len(given) > MAX_ADDRESS or not given.isascii():
        raise ValueError("an address on this machine cairn cannot use")
    try:
        sent_to = httpx.URL(given)
    except (httpx.InvalidURL, ValueError, TypeError):
        raise ValueError("an address on this machine httpx does not take") from None
    if sent_to.scheme != "http" or sent_to.host not in LOCAL_HOSTS:
        raise ValueError("an address httpx reads as another host")
    return given


# The kinds, --wait and --port


def scopes_for(kinds: list[str]) -> list[str]:
    """The scopes of `kinds`, each once, in the order of the kinds' names."""
    asked = set(kinds)
    return list(dict.fromkeys(s for name in KIND_NAMES if name in asked for s in SCOPES[name]))


def kind(value: str) -> str:
    if value not in SCOPES:
        raise argparse.ArgumentTypeError(
            f"not a Google kind: it takes {', '.join(KIND_NAMES[:-1])} or {KIND_NAMES[-1]}"
        )
    return value


def wait_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError:
        seconds = 0
    if not WAIT_MIN <= seconds <= WAIT_MAX:
        raise argparse.ArgumentTypeError(f"a whole number of seconds from {WAIT_MIN} to {WAIT_MAX}")
    return seconds


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("a port from 1 to 65535")
    return port


# PKCE, the state and the URL


def new_verifier(token: Callable[[int], str] = secrets.token_urlsafe) -> str:
    """A PKCE code verifier: 32 random bytes in base64url, 43 unreserved characters."""
    return token(RANDOM_BYTES)


def challenge(verifier: str) -> str:
    """The S256 challenge of `verifier`: its SHA-256 in base64url without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_state(token: Callable[[int], str] = secrets.token_urlsafe) -> str:
    """The request's `state`: 32 random bytes from the system's source, in base64url."""
    return token(RANDOM_BYTES)


def authorization_url(
    auth_url: str,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    challenge: str,
) -> str:
    """The address the person opens: `auth_url` with exactly these parameters, each once."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{auth_url}?{urlencode(params, quote_via=quote)}"


# What the listener answers: the decision for one request, and its three fixed pages


def _page(text: str) -> bytes:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>cairn</title>'
        f"</head><body><p>{text}</p></body></html>\n"
    ).encode()


ANSWERED = _page(
    "cairn has Google's answer. Close this tab and go back to the terminal: it says what"
    " happened next."
)
REFUSED = _page(
    "The authorisation was refused, so there is nothing to keep. Close this tab and go back to"
    " the terminal."
)
NOT_THE_ANSWER = _page("This is not the answer cairn is waiting for. Nothing has changed.")


@dataclass(frozen=True)
class Decision:
    """What the listener does with one request: `outcome` is "answer" (Google sent the code
    back), "refused" (Google sent an error back) or "ignored" (anything else)."""

    outcome: str
    status: int
    page: bytes
    code: str | None = field(default=None, repr=False)


def decide(method: str, target: str, state: str) -> Decision:
    """The decision for a request of `method` for `target` (the request line's target), when
    the command waits for `state`. Only a GET of the redirect address ("/", or nothing, then
    the query) whose query holds `state` once is Google's redirect; with `error` it is a
    refusal, with one non-empty `code` and no `error` the answer."""
    if method != "GET":
        return Decision("ignored", 405, NOT_THE_ANSWER)
    path, _, query = target.partition("?")
    if path not in ("", "/"):
        return Decision("ignored", 404, NOT_THE_ANSWER)
    given: dict[str, list[str]] = {}
    for name, value in parse_qsl(query, keep_blank_values=True):
        given.setdefault(name, []).append(value)
    states = given.get("state", [])
    if len(states) != 1 or not secrets.compare_digest(states[0].encode(), state.encode()):
        return Decision("ignored", 400, NOT_THE_ANSWER)
    if "error" in given:
        return Decision("refused", 200, REFUSED)
    codes = given.get("code", [])
    if len(codes) != 1 or not codes[0]:
        return Decision("ignored", 400, NOT_THE_ANSWER)
    return Decision("answer", 200, ANSWERED, code=codes[0])


# The listener


class _Handler(http.server.BaseHTTPRequestHandler):
    """One request a connection, answered with one of the three fixed pages. The standard
    library's defaults are each replaced where they would show or log a request: its error
    page quotes the request line or the method, and its log writes each request to stderr."""

    protocol_version = "HTTP/1.0"
    server: "_Server"

    def setup(self) -> None:
        super().setup()
        self.rfile.close()  # the head is read from the socket itself, under one deadline
        self.rfile = io.BytesIO()

    def handle(self) -> None:
        self.close_connection = True
        self.command = None
        head = self._head()
        if not head:  # silent, gone, or out of time: closed without an answer
            return
        if len(head) > MAX_HEAD:
            self._reply(414 if b"\n" not in head[:MAX_HEAD] else 431, NOT_THE_ANSWER)
            self._drain()
            return
        self.rfile = io.BytesIO(head)
        self.raw_requestline = self.rfile.readline()
        if not self.parse_request():  # it has answered, through send_error
            return
        # The target as the request line has it: parse_request folds a leading "//".
        target = self.requestline.split()[1]
        decision = self.server.receive(self.command, target)
        self._reply(decision.status, decision.page)

    def _head(self) -> bytes | None:
        """The request head as the client sent it: read until its blank line (or the end of a
        request line with no version), MAX_HEAD + 1 bytes or the client's end, before the
        connection's deadline (CONNECTION_TIME after it was taken, never past the command's).
        What is left until then is set before every read, and no read waits more than the
        poll, so the clock decides. None at the deadline, or when the connection fails."""
        server = self.server
        self.ends = ends = min(server.clock() + server.connection_time, server.deadline)
        head = b""
        while True:
            left = ends - server.clock()
            if left <= 0:
                return None
            self.connection.settimeout(min(left, server.poll))
            try:
                chunk = self.connection.recv(MAX_HEAD + 1 - len(head))
            except TimeoutError:
                continue
            except OSError:
                return None
            if not chunk:  # the client has sent all it will
                return head
            head += chunk
            tail = head[-len(chunk) - 3 :]
            if b"\n\n" in tail or b"\n\r\n" in tail or len(head) > MAX_HEAD:
                return head
            if b"\n" in chunk and len(head.partition(b"\n")[0].split()) < 3:
                return head  # a request line with no version: no headers follow

    def _drain(self) -> None:
        """After the answer to a head that was too large: the rest the client sent, read and
        dropped until it closes, MAX_HEAD more bytes or the connection's deadline, so the
        close does not reset the connection before the client has read the answer."""
        with contextlib.suppress(OSError):
            self.connection.shutdown(socket.SHUT_WR)
            dropped = 0
            while dropped <= MAX_HEAD and (left := self.ends - self.server.clock()) > 0:
                self.connection.settimeout(min(left, self.server.poll))
                try:
                    chunk = self.connection.recv(MAX_HEAD)
                except TimeoutError:
                    continue
                if not chunk:
                    return
                dropped += len(chunk)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None):
        self._reply(code, NOT_THE_ANSWER)

    def _reply(self, status: int, page: bytes) -> None:
        body = b"" if self.command == "HEAD" else page
        head = (
            f"HTTP/1.0 {status} {http.HTTPStatus(status).phrase}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(page)}\r\n"
            "Cache-Control: no-store\r\n"
            "Referrer-Policy: no-referrer\r\n"
            "Connection: close\r\n\r\n"
        )
        with contextlib.suppress(OSError):
            self.wfile.write(head.encode("ascii") + body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _Server(socketserver.TCPServer):
    """A TCP server on 127.0.0.1 (not http.server.HTTPServer, which looks the host's name up
    when it binds) that keeps the first answer or refusal it decides."""

    allow_reuse_address = False
    allow_reuse_port = False

    def __init__(
        self,
        port: int,
        state: str,
        deadline: float,
        clock: Callable[[], float],
        poll: float,
        connection_time: float,
    ) -> None:
        self.state, self.deadline, self.clock = state, deadline, clock
        self.poll, self.connection_time = poll, connection_time
        self.result: Decision | None = None
        super().__init__(("127.0.0.1", port), _Handler)

    def receive(self, method: str, target: str) -> Decision:
        decision = decide(method, target, self.state)
        if decision.outcome != "ignored" and self.result is None:
            self.result = decision
        return decision

    def handle_error(self, request: object, client_address: object) -> None:
        pass  # the default prints a traceback to stderr


class Listener:
    """The redirect's listener: bound to 127.0.0.1 at `port` (0: a free one) as soon as it is
    made, waiting until `deadline` on `clock` (monotonic seconds), and closed on leaving a
    `with` block, whatever ends it. It serves one connection at a time, each for at most
    `connection_time` (CONNECTION_TIME) on `clock`, so a connection ahead of the browser's
    delays the answer by that much at most; no socket is waited on for more than `poll`."""

    def __init__(
        self,
        state: str,
        *,
        port: int = 0,
        deadline: float,
        clock: Callable[[], float] = time.monotonic,
        poll: float = POLL,
        connection_time: float = CONNECTION_TIME,
    ) -> None:
        self.server = _Server(port, state, deadline, clock, poll, connection_time)
        self.port: int = self.server.server_address[1]
        self._poll = poll

    def wait(self) -> Decision | None:
        """Google's answer or refusal, or None when the deadline passes first, whatever
        clients do. Every other request is answered and changes nothing."""
        server = self.server
        while server.result is None:
            left = server.deadline - server.clock()
            if left <= 0:
                return None
            server.timeout = min(left, self._poll)
            server.handle_request()
        return server.result

    def close(self) -> None:
        self.server.server_close()

    def __enter__(self) -> "Listener":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# The exchange

# The refusals of the token endpoint with a sentence of their own, chosen by the `error` code
# alone (never its description or the rest of the body), whatever the HTTP status.
EXCHANGE_SAID = {
    "invalid_grant": (
        "Google refused the code (invalid_grant): it had expired or been used, or the verifier"
        " or the redirect address did not match. Run the command again"
    ),
    "invalid_client": (
        "Google refused the client (invalid_client): its client ID or its secret is wrong."
        " Download the Desktop app client's file again, or rotate its secret in the Cloud"
        " console"
    ),
    "redirect_uri_mismatch": (
        "Google refused the redirect address (redirect_uri_mismatch), which may not be one the"
        " client's type allows: create a Desktop app client and give its file"
    ),
    "invalid_scope": "Google refused a scope the command asked for (invalid_scope)",
    "unauthorized_client": (
        "Google does not let this client exchange a code (unauthorized_client): give the file"
        " of a Desktop app client"
    ),
}
NO_REFRESH_TOKEN = (
    "Google's answer holds no refresh token; nothing was written. Take the app's access away"
    " in the Google account's settings, then run the command again"
)
NOT_GRANTED = (
    "Google's answer does not grant the scope of {kinds}: the person may have left it"
    " unticked; nothing was written. Run the command again and grant every scope asked for"
)
NOTHING = "nothing was written"
# Seconds the whole exchange may take: the client's timeout is per read, so an endpoint that
# sends a byte now and then would never trip it. The bound is the one a source's request has.
EXCHANGE_TIME = SOURCE_TIMEOUT


def exchange_form(client: Client, *, code: str, verifier: str, redirect_uri: str) -> dict[str, str]:
    return {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client.client_id,
        "client_secret": client.client_secret,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }


async def exchange(
    http: httpx.AsyncClient,
    client: Client,
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
    kinds: list[str],
) -> str:
    """The refresh token Google gives for `code`: one POST of the form to the client's token
    address, or Refused (exit 1) in one line that holds nothing Google sent."""
    form = exchange_form(client, code=code, verifier=verifier, redirect_uri=redirect_uri)
    try:
        status, body = await fetch(
            http, "POST", client.token_url, data=form, headers={"Accept": "application/json"},
            limit=MAX_RESPONSE_BYTES,
        )  # fmt: skip
    except Unreachable as error:
        raise Refused(f"cannot reach the token endpoint ({error}); {NOTHING}") from None
    except TooLarge:
        raise Refused(
            f"the token endpoint's answer is larger than cairn reads; {NOTHING}"
        ) from None
    try:
        answer = json.loads(body)
    except (ValueError, RecursionError):
        answer = None
    if status != 200:
        raise Refused(_refusal(status, answer, googles=client.token_url == GOOGLE_TOKEN_URL))
    if not isinstance(answer, dict):
        raise Refused(f"the token endpoint's answer is not the documented shape; {NOTHING}")
    refresh_token = answer.get("refresh_token")
    if not isinstance(refresh_token, str) or not TOKEN.fullmatch(refresh_token):
        raise Refused(NO_REFRESH_TOKEN)
    scope = answer.get("scope")
    held = set(scope.split()) if isinstance(scope, str) else set()
    missing = [name for name in KIND_NAMES if name in kinds and not set(SCOPES[name]) <= held]
    if missing:
        raise Refused(NOT_GRANTED.format(kinds=" and ".join(missing)))
    return refresh_token


def _refusal(status: int, answer: object, *, googles: bool = True) -> str:
    """The line for a refusal; `googles` says whether the token endpoint was Google's own,
    so that one the client file names on this machine is never called Google's."""
    code = answer.get("error") if isinstance(answer, dict) else None
    if isinstance(code, str) and code in EXCHANGE_SAID:
        return f"{EXCHANGE_SAID[code]}; {NOTHING}"
    if 300 <= status < 400:
        return (
            f"the token endpoint answered with a redirect (HTTP {status}), which cairn does not"
            f" follow; {NOTHING}"
        )
    endpoint = "Google's token endpoint" if googles else "the token endpoint the client file names"
    if status >= 500:
        return f"{endpoint} answered HTTP {status}, a fault on its side; {NOTHING}"
    if googles:
        return f"Google refused the code exchange (HTTP {status}); {NOTHING}"
    return f"{endpoint} refused the code exchange (HTTP {status}); {NOTHING}"


# The file


def credential_line(client: Client, refresh_token: str) -> str:
    """The authorized_user document, on one line, as cairn.sources.google_auth loads it."""
    return json.dumps(
        {
            "type": "authorized_user",
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "refresh_token": refresh_token,
        }
    )


def reserve(out: Path) -> int:
    """`out`, made for the credential with mode 0600 in the one call that creates it (never a
    file that exists, never through a link), open for writing; or Refused (exit 1)."""
    try:
        return os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    except FileExistsError:
        raise Refused(
            f"{out} exists now, and google-authorize never overwrites a file; {NOTHING}. Run it"
            " again with another --out"
        ) from None
    except OSError as error:
        raise Refused(f"cannot create {out}: {error.strerror}; {NOTHING}") from None


def write_credential(fd: int, out: Path, line: str) -> None:
    """The line into the file `reserve` made, which is removed again when the write fails."""
    try:
        os.fchmod(fd, 0o600)  # exactly 0600, whatever the umask took away
        data = (line + "\n").encode("utf-8")
        while data:
            data = data[os.write(fd, data) :]
        os.close(fd)
    except OSError as error:
        discard(fd, out)
        raise Refused(f"cannot write {out}: {error.strerror}; {NOTHING}") from None


def discard(fd: int, out: Path) -> None:
    """The file `reserve` made, closed and removed."""
    with contextlib.suppress(OSError):
        os.close(fd)
    with contextlib.suppress(OSError):
        out.unlink()


# The command


def not_googles(client: Client) -> str | None:
    """The line that tells the person which of the client file's addresses is on this machine
    rather than Google's, and what goes there; None when both are Google's own. A real client
    file from Google never names this machine, and with a hostile file and something
    listening here, the client secret and the code would go to it."""
    auth = client.auth_url != GOOGLE_AUTH_URL
    token = client.token_url != GOOGLE_TOKEN_URL
    if auth and token:
        return (
            f"The client file's auth_uri and token_uri, {client.auth_url} and"
            f" {client.token_url}, are on this machine, not Google's: the address below opens"
            " there, and the client secret and the code will be sent there."
        )
    if token:
        return (
            f"The client file's token_uri, {client.token_url}, is on this machine, not"
            " Google's: the client secret and the code will be sent there."
        )
    if auth:
        return (
            f"The client file's auth_uri, {client.auth_url}, is on this machine, not Google's:"
            " the address below opens there, not at Google."
        )
    return None


OPEN = (
    "Open this address in your browser: it is for this run and for you alone, so do not pass"
    " it on (whoever signs in through it first chooses the Google account cairn reads)."
)
WAITING = (
    "It waits {seconds} seconds for that browser, on this machine, to come back to it. Ctrl-C"
    " stops it."
)
DONE = (
    "Wrote one person's Google credential to {out} (mode 0600).",
    "Next: put the file's one line in the server's environment under the name the source's"
    " config gives as credentials_env, then delete {out}.",
    "Before you do, check that the Google account you signed in with is the one you meant:"
    " the source reads as that account.",
)
TIMEOUT = (
    "Nobody came back within {seconds} seconds, so nothing was written.",
    "Google shows some errors in the browser and never sends them back here, so the browser"
    " may show one: the client is not a Desktop app client or does not allow this redirect"
    " address (redirect_uri_mismatch), is limited to its own organization (org_internal) or"
    " was deleted (deleted_client), or an administrator's policy does not allow a scope"
    ' (admin_policy_enforced). While the consent screen is in "Testing", the person must be'
    " one of its test users.",
)
REFUSED_SAID = (
    "The browser came back with a refusal (the person said no, or Google refused), so nothing"
    " was sent to Google and nothing was written."
)
STOPPED = "Stopped: nothing was written."
STOPPED_AFTER = "Stopped: the credential is in {out}."
UNEXPECTED = "google-authorize stopped on an error it did not foresee ({error}); {then}"
HELP = (
    "Obtain one person's Google credential for cairn's Google sources: an authorized_user"
    " document holding a refresh token. Run it on the machine whose browser the person will"
    " use, since Google sends that browser back to a listener on 127.0.0.1 here; it prints an"
    " address to open there, and the person signs in and consents at Google, so cairn never"
    " sees their password. --client is the JSON file the Google Cloud console gives for an"
    " OAuth client of the type Desktop app. The address is for this run and this person"
    " only: do not pass it on, since whoever signs in through it first chooses the Google"
    " account cairn reads. The credential is written to --out, a new file with mode 0600: the"
    " command never overwrites a file. Give --out a folder only you can write to, since"
    " someone who can write to the folder could swap the file once it is written. It opens no"
    " database."
)


def add_command(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser(
        COMMAND,
        help="obtain one person's Google credential for a Google source, in their browser",
        description=HELP,
        allow_abbrev=False,
    )
    parser.add_argument(
        "--client",
        type=Path,
        required=True,
        metavar="FILE",
        help="the JSON file the Cloud console gives for an OAuth client of the type Desktop app",
    )
    parser.add_argument(
        "--kind",
        type=kind,
        action="append",
        required=True,
        dest="kinds",
        metavar="KIND",
        help=f"a source kind the credential is for, one of {', '.join(KIND_NAMES)}; given"
        " once per kind, it asks for the scope of each",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        metavar="FILE",
        help="a new file for the credential, in a folder that exists and only you can write to;"
        " never overwritten",
    )
    parser.add_argument(
        "--port",
        type=port_number,
        metavar="PORT",
        help="the port on 127.0.0.1 the browser comes back to (a free one)",
    )
    parser.add_argument(
        "--wait",
        type=wait_seconds,
        default=WAIT,
        metavar="SECONDS",
        help=f"how long it waits for the person, {WAIT_MIN} to {WAIT_MAX} seconds ({WAIT})",
    )


def run(args: argparse.Namespace) -> int:
    code = authorize(args)
    _drop_a_gone_stdout()
    return code


def _drop_a_gone_stdout() -> None:
    """When the reader of stdout has gone, the interpreter's own flush on the way out would
    fail again and turn the exit code into 120 with an "Exception ignored" on stderr: what is
    left for stdout goes to the null device instead, as the Python docs advise for SIGPIPE."""
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.flush()
    except OSError:
        with contextlib.suppress(OSError, ValueError):
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, stream.fileno())
            os.close(null)


def authorize(
    args: argparse.Namespace,
    *,
    clock: Callable[[], float] = time.monotonic,
    http: Callable[[], httpx.AsyncClient] = source_client,
    poll: float = POLL,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """The command, as the module docstring says; `clock` is monotonic seconds, `http` makes
    the client the exchange is sent through."""
    stdout, stderr = stdout or sys.stdout, stderr or sys.stderr
    out: Path = args.out
    fd: int | None = None  # --out's descriptor, the command's until the write has returned
    written = False

    def say(*lines: str) -> None:
        if stdout is None:  # started with stdout closed: the address could not be shown
            raise Refused(f"there is no stdout, so the address cannot be shown; {NOTHING}")
        for line in lines:
            try:
                _print(line, stdout)
            except OSError as error:
                then = f"the credential is in {out}" if written else NOTHING
                raise Refused(
                    f"cannot print on stdout ({type(error).__name__}), so"
                    f" {'the next steps' if written else 'the address'} cannot be shown; {then}"
                ) from None

    def refuse(code: int, *lines: str) -> int:
        if stderr is None:  # started with stderr closed: there is nobody to tell
            return code
        for line in lines:
            with contextlib.suppress(OSError):  # with no stderr there is nobody to tell
                _print(line, stderr)
        return code

    try:
        with _signals_stop_it():
            if os.path.lexists(out):
                return refuse(2, f"{out} already exists: google-authorize never overwrites a file")
            try:
                folder = out.parent.is_dir()
            except OSError as error:
                return refuse(2, f"cannot look into the folder {out.parent}: {error.strerror}")
            if not folder:
                return refuse(
                    2,
                    f"there is no folder {out.parent}: --out names a new file in a folder that"
                    " exists",
                )
            client = read_client(args.client)
            state, verifier = new_state(), new_verifier()
            port = args.port or 0
            try:
                listener = Listener(
                    state, port=port, deadline=clock() + args.wait, clock=clock, poll=poll
                )
            except OSError as error:
                return refuse(
                    2,
                    f"cannot listen on 127.0.0.1:{port}: {error.strerror}; leave out --port for a"
                    " free one",
                )
            with listener:
                redirect_uri = f"http://127.0.0.1:{listener.port}"
                url = authorization_url(
                    client.auth_url, client_id=client.client_id, redirect_uri=redirect_uri,
                    scopes=scopes_for(args.kinds), state=state, challenge=challenge(verifier),
                )  # fmt: skip
                said = not_googles(client)
                say(*([said] if said else []), OPEN, url, WAITING.format(seconds=args.wait))
                decision = listener.wait()
            if decision is None:
                return refuse(1, TIMEOUT[0].format(seconds=args.wait), TIMEOUT[1])
            if decision.outcome != "answer" or decision.code is None:
                return refuse(1, REFUSED_SAID)
            fd = reserve(out)  # before the code is spent: a file that appeared meanwhile stays
            sent = {"code": decision.code, "verifier": verifier, "redirect_uri": redirect_uri}
            with _quiet_clients():
                refresh_token = asyncio.run(_exchange(http, client, kinds=args.kinds, **sent))
            try:
                write_credential(fd, out, credential_line(client, refresh_token))
            except Refused:
                fd = None  # write_credential has removed it
                raise
            fd, written = None, True
            say(*(line.format(out=out) for line in DONE))
            return 0
    except Refused as refused:
        return refuse(refused.exit, str(refused))
    except KeyboardInterrupt as stopped:  # Ctrl-C, or SIGTERM or SIGHUP (Signalled)
        code = stopped.exit if isinstance(stopped, Signalled) else 130
        return refuse(code, STOPPED_AFTER.format(out=out) if written else STOPPED)
    except Exception as error:  # a fault nobody foresaw: said by its class, never its text
        then = f"the credential is in {out}" if written else NOTHING
        return refuse(1, UNEXPECTED.format(error=type(error).__name__, then=then))
    finally:
        if fd is not None:  # whatever ended it before the credential was written
            discard(fd, out)


class Signalled(KeyboardInterrupt):
    """SIGTERM or SIGHUP while the command runs: it stops as Ctrl-C stops it, with exit 128
    plus the signal's number (143 for SIGTERM, 129 for SIGHUP), as a shell reports a process
    a signal ended, and as Ctrl-C's 130 is."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.exit = 128 + signum


STOP_SIGNALS = tuple(
    getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)
)


@contextlib.contextmanager
def _signals_stop_it() -> Iterator[None]:
    """SIGTERM and SIGHUP raise Signalled while the command runs, so they end it as Ctrl-C
    does: the listener closed, the reserved file removed. The handlers in place before are
    put back afterwards. Only the main thread can set a handler, so a run on another thread
    (the unit tests') sets none, and a signal ignored before (nohup's SIGHUP) stays ignored."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def stop(signum: int, frame: object) -> None:
        raise Signalled(signum)

    before: dict[int, Any] = {}
    try:
        for number in STOP_SIGNALS:
            handler = signal.getsignal(number)
            if handler is signal.SIG_IGN:
                continue
            before[number] = handler
            signal.signal(number, stop)
        yield
    finally:
        for number, handler in before.items():
            signal.signal(number, signal.SIG_DFL if handler is None else handler)


def _print(line: str, stream: TextIO) -> None:
    """`line` on `stream`. What the stream cannot encode (a path's letters on a terminal set
    to ASCII) is escaped, not the end of the command; a stream that cannot be written to at
    all is the caller's OSError."""
    try:
        print(line, file=stream, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), file=stream, flush=True)


async def _exchange(http: Callable[[], httpx.AsyncClient], client: Client, **sent: Any) -> str:
    """The exchange, opening and closing its client included, within EXCHANGE_TIME, or
    Refused (exit 1) saying the token endpoint did not answer in time."""
    try:
        async with asyncio.timeout(EXCHANGE_TIME) as limit:
            async with http() as session:
                return await exchange(session, client, **sent)
    except TimeoutError:
        if not limit.expired():
            raise
        raise Refused(
            f"the token endpoint did not answer within {EXCHANGE_TIME:g} seconds; {NOTHING}"
        ) from None


@contextlib.contextmanager
def _quiet_clients() -> Iterator[None]:
    """The "httpx" and "httpcore" loggers held at WARNING while the exchange runs, as the
    sources' client holds them (cairn.sources.runner.source_client): httpx logs each request
    at INFO and httpcore traces each response's headers at DEBUG. Put back afterwards."""
    clients = [logging.getLogger(name) for name in CLIENT_LOGGERS]
    levels = [client.level for client in clients]
    for client in clients:
        client.setLevel(max(logging.WARNING, client.level))
    try:
        yield
    finally:
        for client, level in zip(clients, levels, strict=True):
            client.setLevel(level)
