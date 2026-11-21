"""C8 acceptance tests (frozen): `google-authorize`, the command that obtains one person's
Google credential for cairn's Google sources.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstring of the module that implements the command (src/cairn/web/
cli.py, or a module of cairn.web it names), and in the command's --help. Google's request and
response shapes are recorded in docs/google-oauth-installed.md (an installed, "Desktop app"
client); where those notes are silent or disagree, the contract below says which reading the
command takes, and the stand-in Google in this file follows it.

The command
- `python -m cairn.server google-authorize --client FILE --kind KIND [--kind KIND]... --out FILE
  [--port PORT] [--wait SECONDS]`. The person runs it on the machine whose browser they will
  use: Google sends the browser back to a listener on that machine. It opens no database and
  takes no --db.
- --client: the OAuth client JSON the Cloud Console downloads for a "Desktop app" client, one
  top-level key `installed` holding at least `client_id` and `client_secret`. A top-level `web`
  is refused with a message that says to create a Desktop app client. A file that is missing,
  not JSON, or lacks either value: exit 2, one line on stderr naming what is wrong, never a
  value from the file.
- --kind: one or more of gmail, google_calendar, google_drive, google_meet. The command asks
  for exactly the scopes those kinds ask for, each once, separated by spaces. Any other kind:
  exit 2, naming the kinds it takes.
- --out: where the credential is written. It must not exist (exit 2 before anything else
  happens: nothing listens, nothing is printed on stdout, the file is untouched) and its
  directory must. The command never overwrites it, also when it appears while the command
  waits.
- --port: the port to listen on; a free one when not given. --wait: seconds to wait for the
  person, default 300, 30 to 900; anything else is refused (exit 2).
- Wrong or missing options: exit 2, as the other commands.
- `google-authorize --help` says where to run it (the machine with the browser), what the
  client file is (a Desktop app client's) and that it never overwrites --out.

What it does, in order
1. It checks its options and the client file, and listens on 127.0.0.1 at the port, never on
   another address.
2. On stdout, a line that begins "Open this address in your browser:", then a line that is
   the authorization URL and nothing else: the authorization endpoint with exactly the
   parameters client_id, redirect_uri (`http://127.0.0.1:<port>`, no path, no trailing
   slash), response_type=code, scope, state (at least 128 bits from the system's random
   source, new on every run), code_challenge and code_challenge_method=S256 (PKCE: a verifier
   of 43 to 128 unreserved characters, the challenge its SHA-256 in base64url without
   padding), access_type=offline and prompt=consent; each once, and nothing else.
3. It waits for the browser. A GET of the redirect address that carries `code` and the right
   `state` in its query is the answer. Anything else is not: another `state` or none, another
   path (`/favicon.ico`), another method, a second answer after the first; each gets a short
   fixed page and changes nothing. No value from a request (path, parameter, header, method)
   is ever written into a page, the output or a log. `error` with the right `state` ends the
   wait: exit 1, saying in words that the person or Google refused, and nothing is sent.
4. One POST to the token endpoint, a form (application/x-www-form-urlencoded) of exactly
   grant_type=authorization_code, code, client_id, client_secret, redirect_uri (the same
   string as in the URL) and code_verifier. Nothing in its URL's query.
5. The answer must hold a `refresh_token`, and its `scope` must hold every scope asked for
   (the answer may list them in another order, with others, and with fields cairn does not
   read). When either fails: exit 1, a line on stderr that says which (the kind whose scope is
   missing, by name), never a token, and nothing is written.
6. It writes --out as one line of JSON, exactly {"type": "authorized_user", "client_id",
   "client_secret", "refresh_token"}, in a file created for it with mode 0600 and never
   readable by others even for a moment (it is never created wider and narrowed after), which
   cairn.sources.google_auth.load_credential loads and access_token uses as they are. Then it
   prints what to do next: put that line in the server's environment under the name the
   source's config gives as `credentials_env`, and delete the file. Exit 0.
7. Nobody answers within --wait: exit 1, saying so and what the browser may have shown
   instead. (The expiry is left to the implementer's unit tests: no test here waits 30
   seconds, and the contract allows no environment variable or hidden option to shorten it.)

Where it sends what
- The authorization endpoint is https://accounts.google.com/o/oauth2/v2/auth and the token
  endpoint https://oauth2.googleapis.com/token, whatever the client file's auth_uri and
  token_uri say (Google's own downloads name older addresses), except that an auth_uri or a
  token_uri on this machine (http, host 127.0.0.1 or localhost) is used as it is, for tests
  and stand-ins. Look-alikes are other hosts.
- The client secret, the code, the verifier and the tokens go to the token endpoint only, in
  the form, never in a URL; none of them is printed or written anywhere but --out.
- A refusal by the token endpoint (any error code, a 5xx, a body that is not JSON): exit 1 and
  exactly one line on stderr in words chosen by the error code (invalid_grant, invalid_client
  and redirect_uri_mismatch each have their own), never the response's text.

How this file runs the command: as a real process (`sys.executable -m cairn.server
google-authorize ...`, with the source this test imported first on PYTHONPATH), with a
stand-in Google on 127.0.0.1 and the test playing the browser over real HTTP. Every process
and listener it starts is stopped in a `finally`. A sitecustomize module on the child's
PYTHONPATH watches the child through Python's audit hooks: it logs every bind, every chmod
(with the file's mode just before it) and every file opened for writing, and it refuses every
name lookup and connection that is not to this machine, so no test can reach another host.
"""

import asyncio
import base64
import contextlib
import hashlib
import http.client
import json
import os
import queue
import re
import secrets
import socket
import stat
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

import httpx
import pytest

import cairn
from cairn import server
from cairn.sources.google_auth import AuthorizedUser, access_token, load_credential

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = {
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
    "google_calendar": "https://www.googleapis.com/auth/calendar.events.readonly",
    "google_drive": "https://www.googleapis.com/auth/drive.readonly",
    "google_meet": "https://www.googleapis.com/auth/meetings.space.readonly",
}
URL_PARAMS = {
    "client_id", "redirect_uri", "response_type", "scope", "state", "code_challenge",
    "code_challenge_method", "access_type", "prompt",
}  # fmt: skip
FORM_FIELDS = {"grant_type", "code", "client_id", "client_secret", "redirect_uri", "code_verifier"}
OPEN = "Open this address in your browser:"
UNRESERVED = re.compile(r"[A-Za-z0-9\-._~]{43,128}")
# Made-up values in a credential's shape, built here so no file holds one.
CLIENT_ID = "2718-c8ada" + "desktop" + ".apps.example.test"
CLIENT_SECRET = "c8-client-" + "Qw9sEcr" * 4
LINE_WAIT = 15.0  # seconds a test waits for a line the command should print at once
EXIT_WAIT = 20.0  # seconds a test waits for the command to end once it should


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# The stand-in Google


class Google:
    """Google's two endpoints on 127.0.0.1: GET /o/oauth2/v2/auth plays the person consenting
    (it issues a code bound to the request's challenge and redirect_uri and sends the browser
    back), POST /token checks the code exchange exactly and answers as `mode` says. What it
    finds wrong is kept in `problems` and answered with invalid_grant."""

    def __init__(self) -> None:
        self.mode: tuple = ("ok",)
        self.codes: dict[str, dict] = {}
        self.posts: list[dict] = []  # every request to /token: path, headers, form
        self.problems: list[str] = []
        self.refresh_tokens: dict[str, str] = {}  # refresh token -> client_id
        self.access_tokens: list[str] = []
        self.lock = threading.Lock()
        google = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def reply(self, status: int, body: bytes, kind: str, extra=()) -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                for name, value in extra:
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                parts = urlsplit(self.path)
                if parts.path != "/o/oauth2/v2/auth":
                    return self.reply(404, b"no", "text/plain")
                location = google.consent(parse_qs(parts.query))
                if location is None:
                    return self.reply(400, b"bad authorization request", "text/plain")
                self.reply(302, b"", "text/plain", [("Location", location)])

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8", "replace")
                status, body, kind = google.exchange(self.path, dict(self.headers), raw)
                self.reply(status, body, kind)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.port = self.http.server_address[1]
        self.auth_uri = f"http://127.0.0.1:{self.port}/o/oauth2/v2/auth"
        self.token_uri = f"http://127.0.0.1:{self.port}/token"
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def start(self) -> "Google":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)

    def consent(self, query: dict[str, list[str]]) -> str | None:
        """The person consents: a code for this request, and where the browser goes next."""
        one = {name: values[0] for name, values in query.items()}
        if "redirect_uri" not in one or "state" not in one:
            return None
        code = "4/c8code-" + secrets.token_urlsafe(18)
        with self.lock:
            self.codes[code] = {
                "client_id": one.get("client_id"),
                "redirect_uri": one["redirect_uri"],
                "challenge": one.get("code_challenge"),
                "method": one.get("code_challenge_method"),
                "scopes": one.get("scope", "").split(" "),
                "used": False,
            }
        # As Google's redirect may: the granted scopes and fields cairn does not read.
        back = {"state": one["state"], "code": code, "scope": one.get("scope", ""),
                "authuser": "0", "prompt": "consent"}  # fmt: skip
        return f"{one['redirect_uri']}?{urlencode(back)}"

    def exchange(self, path: str, headers: dict, raw: str) -> tuple[int, bytes, str]:
        form = parse_qs(raw, keep_blank_values=True)
        with self.lock:
            self.posts.append({"path": path, "headers": headers, "form": form, "raw": raw})
            wrong = self.check(path, headers, form)
            self.problems += wrong
        if wrong:
            return self.json(400, {"error": "invalid_grant"})
        code = form["code"][0]
        asked = self.codes[code]["scopes"]
        mode = self.mode
        if mode[0] == "error":  # ("error", code, status)
            payload = {"error": mode[1], "error_description": f"{mode[1]} c8BODYMARK said",
                       "error_uri": "https://example.test/c8BODYMARK"}  # fmt: skip
            return self.json(mode[2], payload)
        if mode[0] == "status":  # ("status", n, body)
            return mode[1], mode[2].encode(), "text/html"
        if mode[0] == "not_json":  # ("not_json", status)
            return mode[1], b"<html>c8BODYMARK upstream trouble</html>", "text/html"
        refresh = "1//c8refresh-" + secrets.token_urlsafe(24)
        access = "ya29.c8access-" + secrets.token_urlsafe(24)
        granted = asked if mode[0] != "grant_only" else mode[1]
        answer = {
            "access_token": access,
            "expires_in": 3599,
            "token_type": "Bearer",
            "scope": " ".join([*reversed(granted), "openid"]),
            "refresh_token": refresh,
            "refresh_token_expires_in": 604799,
            "id_token": "c8.not.read",
        }
        if mode[0] == "no_refresh":
            del answer["refresh_token"]
        with self.lock:
            self.refresh_tokens[refresh] = form["client_id"][0]
            self.access_tokens.append(access)
        return self.json(200, answer)

    def check(self, path: str, headers: dict, form: dict[str, list[str]]) -> list[str]:
        wrong = []
        if urlsplit(path).path != "/token" or urlsplit(path).query or "?" in path:
            wrong.append(f"the exchange went to {path!r}, with something in its URL")
        kind = {k.lower(): v for k, v in headers.items()}.get("content-type", "")
        if not kind.startswith("application/x-www-form-urlencoded"):
            wrong.append(f"the exchange is not a form: {kind!r}")
        if set(form) != FORM_FIELDS or any(len(v) != 1 for v in form.values()):
            return [*wrong, f"the form's fields are {sorted(form)}, not exactly {FORM_FIELDS}"]
        one = {name: values[0] for name, values in form.items()}
        issued = self.codes.get(one["code"])
        if one["grant_type"] != "authorization_code":
            wrong.append("grant_type is not authorization_code")
        if issued is None:
            return [*wrong, "a code the stand-in never issued"]
        if issued["used"]:
            wrong.append("a code used twice")
        issued["used"] = True
        if one["client_id"] != CLIENT_ID or issued["client_id"] != CLIENT_ID:
            wrong.append("another client_id")
        if one["client_secret"] != CLIENT_SECRET:
            wrong.append("another client_secret")
        if one["redirect_uri"] != issued["redirect_uri"]:
            wrong.append("a redirect_uri other than the authorization request's")
        verifier = one["code_verifier"]
        if not UNRESERVED.fullmatch(verifier):
            wrong.append("a code_verifier that is not 43 to 128 unreserved characters")
        if issued["method"] != "S256":
            wrong.append("the authorization request's method was not S256")
        if (
            b64url(hashlib.sha256(verifier.encode("ascii", "replace")).digest())
            != (issued["challenge"])
        ):
            wrong.append("a code_verifier whose S256 is not the challenge")
        return wrong

    def refresh(self, form: dict[str, list[str]]) -> tuple[int, dict]:
        """google_auth's refresh, as Google answers it: the refresh token and its client."""
        one = {name: values[0] for name, values in form.items()}
        expected = {"client_id", "client_secret", "grant_type", "refresh_token"}
        if set(form) != expected or one["grant_type"] != "refresh_token":
            return 400, {"error": "invalid_request"}
        if self.refresh_tokens.get(one["refresh_token"]) != one["client_id"] or (
            one["client_secret"] != CLIENT_SECRET
        ):
            return 400, {"error": "invalid_grant"}
        token = "ya29.c8refreshed-" + secrets.token_urlsafe(24)
        self.access_tokens.append(token)
        return 200, {"access_token": token, "expires_in": 3599, "token_type": "Bearer"}

    @staticmethod
    def json(status: int, payload: dict) -> tuple[int, bytes, str]:
        return status, json.dumps(payload).encode(), "application/json; charset=utf-8"


@pytest.fixture
def google():
    stand_in = Google().start()
    try:
        yield stand_in
    finally:
        stand_in.stop()


# The watch on the child process

GUARD = r"""
import json, os, sys, threading

_path = os.environ.get("C8_GUARD_LOG")
if _path:
    _fd = os.open(_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    _here = threading.local()
    _LOCAL = {"127.0.0.1", "localhost", "::1"}
    _WRITES = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

    def _log(*items):
        os.write(_fd, (json.dumps(items) + "\n").encode())

    def _host(value):
        return value.decode() if isinstance(value, bytes) else value

    def _hook(event, args):
        if getattr(_here, "busy", False):
            return
        _here.busy = True
        refuse = None
        try:
            if event == "socket.bind":
                address = args[1]
                _log("bind", list(address) if isinstance(address, tuple) else str(address))
            elif event == "socket.getaddrinfo":
                host = _host(args[0])
                if host is not None and host not in _LOCAL:
                    _log("blocked", host)
                    refuse = host
            elif event == "socket.connect":
                address = args[1]
                if isinstance(address, tuple) and _host(address[0]) not in _LOCAL:
                    _log("blocked", str(address[0]))
                    refuse = str(address[0])
            elif event == "os.chmod":
                target = args[0]
                try:
                    st = os.fstat(target) if isinstance(target, int) else os.stat(target)
                    _log("chmod", st.st_ino, st.st_mode & 0o777)
                except OSError:
                    pass
            elif event == "open":
                path, flags = args[0], args[2]
                if isinstance(path, (str, bytes)) and isinstance(flags, int) and flags & _WRITES:
                    _log("write", os.path.abspath(os.fsdecode(path)))
        finally:
            _here.busy = False
        if refuse is not None:
            raise OSError(f"the test lets no process reach {refuse}")

    _log("loaded", os.getpid())
    sys.addaudithook(_hook)
"""


class Watch:
    """What the guard saw the child do."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self) -> list[list]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]

    def of(self, kind: str) -> list[list]:
        return [entry[1:] for entry in self.entries() if entry[0] == kind]

    def loaded(self) -> bool:
        return bool(self.of("loaded"))


# The command, as a process


class Command:
    """`python -m cairn.server google-authorize ...` with its stdout read line by line on a
    thread (so a command that prints nothing fails the test instead of hanging it) and its
    stderr kept."""

    def __init__(self, tmp_path: Path, args: list[str]) -> None:
        guard = tmp_path / "c8guard"
        guard.mkdir(exist_ok=True)
        (guard / "sitecustomize.py").write_text(GUARD, encoding="utf-8")
        self.watch = Watch(tmp_path / "c8guard.log")
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        source = str(Path(cairn.__file__).resolve().parents[1])  # the cairn this test runs
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        env.update(
            PYTHONPATH=os.pathsep.join([str(guard), source]),
            PYTHONUNBUFFERED="1",
            PYTHONDONTWRITEBYTECODE="1",
            C8_GUARD_LOG=str(self.watch.path),
            CAIRN_SOURCE_PRIVATE_ADDRESSES="allow",
            HOME=str(home),
            TMPDIR=str(home),
        )
        self.lines: queue.Queue = queue.Queue()
        self.out: list[str] = []
        self.err: list[str] = []
        self.process = subprocess.Popen(
            [sys.executable, "-m", "cairn.server", "google-authorize", *args],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, cwd=tmp_path, umask=0,
        )  # fmt: skip
        self.readers = [
            threading.Thread(target=self._read, args=(self.process.stdout, True), daemon=True),
            threading.Thread(target=self._read, args=(self.process.stderr, False), daemon=True),
        ]
        for reader in self.readers:
            reader.start()

    def _read(self, stream, is_out: bool) -> None:
        for line in stream:
            (self.out if is_out else self.err).append(line)
            if is_out:
                self.lines.put(line)
        if is_out:
            self.lines.put(None)

    def line(self) -> str:
        try:
            line = self.lines.get(timeout=LINE_WAIT)
        except queue.Empty:
            raise AssertionError(
                f"the command printed no line on stdout within {LINE_WAIT:g} s; stderr so far:"
                f" {self.stderr!r}"
            ) from None
        if line is None:
            self.process.wait(timeout=EXIT_WAIT)
            for reader in self.readers:
                reader.join(timeout=EXIT_WAIT)
            raise AssertionError(
                f"the command ended (exit {self.process.returncode}) before printing the"
                f" authorization URL; stdout {self.stdout!r}, stderr {self.stderr!r}"
            )
        return line

    def url(self) -> str:
        """The line after "Open this address in your browser:", which is the URL alone."""
        while not self.line().startswith(OPEN):
            pass
        url = self.line().rstrip("\n")
        assert url == url.strip() and " " not in url, f"not a URL alone: {url!r}"
        return url

    def finish(self) -> int:
        try:
            code = self.process.wait(timeout=EXIT_WAIT)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"the command did not end within {EXIT_WAIT:g} s; stdout {self.stdout!r},"
                f" stderr {self.stderr!r}"
            ) from None
        for reader in self.readers:
            reader.join(timeout=EXIT_WAIT)
        return code

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=EXIT_WAIT)
        for reader in self.readers:
            reader.join(timeout=EXIT_WAIT)
        for stream in (self.process.stdout, self.process.stderr):
            with contextlib.suppress(OSError):
                stream.close()

    @property
    def stdout(self) -> str:
        return "".join(self.out)

    @property
    def stderr(self) -> str:
        return "".join(self.err)


@contextlib.contextmanager
def authorize(tmp_path: Path, *args: str):
    command = Command(tmp_path, list(args))
    try:
        yield command
    finally:
        command.stop()


def run_to_end(tmp_path: Path, *args: str) -> Command:
    """A run that should end by itself without a browser (a refusal of its options)."""
    with authorize(tmp_path, *args) as command:
        command.finish()
    return command


def client_file(tmp_path: Path, google: Google | None = None, **override) -> Path:
    """The Desktop app client as the Console downloads it, naming the stand-in's endpoints."""
    installed = {
        "client_id": CLIENT_ID,
        "project_id": "c8-ada-project",
        "auth_uri": google.auth_uri if google else "https://accounts.google.com/o/oauth2/auth",
        "token_uri": google.token_uri if google else "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": CLIENT_SECRET,
        "redirect_uris": ["http://localhost"],
    }
    installed.update(override)
    path = tmp_path / "client_secret_c8.json"
    path.write_text(json.dumps({"installed": installed}), encoding="utf-8")
    return path


def params_of(url: str) -> dict[str, str]:
    """The URL's query, each parameter once (asserted), and nothing else in the URL."""
    parts = urlsplit(url)
    assert parts.fragment == "", url
    pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    names = Counter(name for name, _ in pairs)
    assert set(names) == URL_PARAMS, f"the URL's parameters are {sorted(names)}"
    assert all(n == 1 for n in names.values()), f"a parameter given twice: {names}"
    return dict(pairs)


def base_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def get(port: int, target: str, method: str = "GET", headers: dict | None = None):
    """One request as a browser sends it: (status, headers, body). No proxy, no redirect."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=LINE_WAIT)
    try:
        connection.request(method, target, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.getheaders(), response.read()
    finally:
        connection.close()


def raw(port: int, data: bytes) -> bytes:
    """Bytes sent as they are, and whatever comes back until the listener closes."""
    with socket.create_connection(("127.0.0.1", port), timeout=LINE_WAIT) as sock:
        sock.sendall(data)
        got = b""
        with contextlib.suppress(OSError):
            while chunk := sock.recv(65536):
                got += chunk
        return got


def consent(google: Google, url: str) -> tuple[str, str]:
    """The person opens the URL and consents: the stand-in's redirect, as (Location, code)."""
    parts = urlsplit(url)
    target = f"{parts.path}?{parts.query}"
    status, headers, _ = get(google.port, target)
    assert status == 302
    location = dict(headers)["Location"]
    code = parse_qs(urlsplit(location).query)["code"][0]
    return location, code


def come_back(location: str) -> tuple[int, list, bytes]:
    """The browser follows Google's redirect to the listener."""
    parts = urlsplit(location)
    assert parts.hostname == "127.0.0.1"
    return get(parts.port, f"{parts.path or '/'}?{parts.query}")


def said_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def holds_any(text: str, values) -> list[str]:
    return [value for value in values if value and value in text]


def written_elsewhere(command: Command, tmp_path: Path, *keep: Path) -> dict[str, str]:
    """Every file under tmp_path, and every file the child opened for writing, but `keep`,
    the guard's own files and the client file: path -> text."""
    skip = {str(p.resolve()) for p in keep} | {str(command.watch.path.resolve())}
    paths = {str(p.resolve()) for p in tmp_path.rglob("*") if p.is_file()}
    paths |= {entry[0] for entry in command.watch.of("write") if not entry[0].startswith("/dev/")}
    found = {}
    for path in sorted(paths - skip):
        if "c8guard" in path or path.endswith("client_secret_c8.json"):
            continue
        with contextlib.suppress(OSError):
            found[path] = Path(path).read_text(encoding="utf-8", errors="replace")
    return found


def the_command_exists() -> None:
    """`google-authorize --help` exits 0: the refusals below are the command's, not
    argparse's answer to an unknown command."""
    with pytest.raises(SystemExit) as stopped:
        server.main(["google-authorize", "--help"])
    assert stopped.value.code == 0


# 1. The happy path


def run_happy(tmp_path: Path, google: Google, kinds: list[str], port: int | None = None) -> dict:
    """The whole flow, checked as the contract says; what a later check needs is returned."""
    out = tmp_path / "ada-google.json"
    args = ["--client", str(client_file(tmp_path, google)), "--out", str(out)]
    for kind in kinds:
        args += ["--kind", kind]
    if port is not None:
        args += ["--port", str(port)]
    asked = list(dict.fromkeys(SCOPE[k] for k in kinds))
    with authorize(tmp_path, *args) as command:
        url = command.url()
        assert command.watch.loaded(), "the guard did not load in the child"
        # The URL: the endpoint the client file names on this machine, and exactly these.
        assert base_of(url) == google.auth_uri
        params = params_of(url)
        redirect = params["redirect_uri"]
        listening = urlsplit(redirect).port
        assert redirect == f"http://127.0.0.1:{listening}"
        if port is not None:
            assert listening == port
        assert params["client_id"] == CLIENT_ID
        assert params["response_type"] == "code"
        scopes = params["scope"].split(" ")
        assert sorted(scopes) == sorted(asked) and len(scopes) == len(set(scopes))
        assert len(params["state"].encode()) >= 16  # 128 bits at the very least
        assert params["code_challenge_method"] == "S256"
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", params["code_challenge"])
        assert (params["access_type"], params["prompt"]) == ("offline", "consent")
        assert CLIENT_SECRET not in url
        # It listens on 127.0.0.1 only.
        binds = command.watch.of("bind")
        assert binds and all(entry[0][0] == "127.0.0.1" for entry in binds), binds
        # The person consents; the browser comes back.
        location, code = consent(google, url)
        _, headers, page = come_back(location)
        shown = page.decode("utf-8", "replace") + repr(headers)
        assert holds_any(shown, [code, params["state"]]) == []
        assert command.finish() == 0, command.stderr
    assert google.problems == []
    (post,) = google.posts
    form = {name: values[0] for name, values in post["form"].items()}
    assert set(post["form"]) == FORM_FIELDS
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == code
    assert (form["client_id"], form["client_secret"]) == (CLIENT_ID, CLIENT_SECRET)
    assert form["redirect_uri"] == redirect
    verifier = form["code_verifier"]
    assert UNRESERVED.fullmatch(verifier)
    assert b64url(hashlib.sha256(verifier.encode()).digest()) == params["code_challenge"]
    # The file: one line, exactly the four fields, 0600, never wider before.
    text = out.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 1
    document = json.loads(text)
    assert set(document) == {"type", "client_id", "client_secret", "refresh_token"}
    assert document["type"] == "authorized_user"
    assert (document["client_id"], document["client_secret"]) == (CLIENT_ID, CLIENT_SECRET)
    assert document["refresh_token"] in google.refresh_tokens
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    widened = [e for e in command.watch.of("chmod") if e[0] == out.stat().st_ino and e[1] & 0o077]
    assert widened == [], "the credential file was readable by others before it was narrowed"
    # What to do next.
    told = command.stdout
    assert "credentials_env" in told and "delete" in told.lower()
    # No secret anywhere but --out.
    hidden = [CLIENT_SECRET, code, verifier, document["refresh_token"], *google.access_tokens]
    assert holds_any(command.stdout + command.stderr, hidden) == []
    for path, content in written_elsewhere(command, tmp_path, out).items():
        assert holds_any(content, hidden) == [], f"a secret in {path}"
    assert command.watch.of("blocked") == []
    return {"document": document, "params": params, "verifier": verifier, "out": out}


def refreshed(google: Google, document: dict) -> str:
    """The file's content through google_auth as a source uses it, against the stand-in."""
    credential = load_credential("C8_GOOGLE", {"C8_GOOGLE": json.dumps(document)})
    assert isinstance(credential, AuthorizedUser)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == GOOGLE_TOKEN
        status, payload = google.refresh(parse_qs(request.content.decode()))
        return httpx.Response(status, json=payload)

    async def ask() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await access_token(
                credential, scopes=[SCOPE["gmail"]], subject=None, http=http,
                clock=lambda: datetime.now(UTC),
            )  # fmt: skip

    return asyncio.run(ask())


def test_one_kind_end_to_end_on_the_port_given(tmp_path, google):
    port = free_port()
    done = run_happy(tmp_path, google, ["gmail"], port=port)
    out = done["out"]
    credential = load_credential("C8_GOOGLE", {"C8_GOOGLE": out.read_text(encoding="utf-8")})
    assert isinstance(credential, AuthorizedUser)
    assert credential.refresh_token == done["document"]["refresh_token"]
    assert refreshed(google, done["document"]) in google.access_tokens


def test_two_kinds_ask_for_both_scopes_once_each(tmp_path, google):
    done = run_happy(tmp_path, google, ["gmail", "google_drive", "gmail"])
    assert sorted(done["params"]["scope"].split(" ")) == sorted(
        [SCOPE["gmail"], SCOPE["google_drive"]]
    )
    assert refreshed(google, done["document"]) in google.access_tokens


def test_each_run_has_its_own_state_and_verifier(tmp_path, google):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first = run_happy(tmp_path / "one", google, ["google_calendar"])
    google.posts.clear()
    second = run_happy(tmp_path / "two", google, ["google_meet"])
    assert first["params"]["state"] != second["params"]["state"]
    assert first["verifier"] != second["verifier"]
    assert first["params"]["code_challenge"] != second["params"]["code_challenge"]
    assert first["document"]["refresh_token"] != second["document"]["refresh_token"]


# 3. The listener


def test_only_the_real_answer_ends_the_wait_and_no_page_repeats_the_request(tmp_path, google):
    out = tmp_path / "ada-google.json"
    client = client_file(tmp_path, google)
    marks = [f"c8Mk{n}zq" for n in range(12)]
    with authorize(tmp_path, "--client", str(client), "--kind", "gmail", "--out", str(out)) as cmd:
        url = cmd.url()
        params = params_of(url)
        state = params["state"]
        port = urlsplit(params["redirect_uri"]).port
        pages = []

        def still_waiting(what: str) -> None:
            assert cmd.process.poll() is None, f"{what} ended the wait"
            assert google.posts == [], f"{what} reached the token endpoint"

        def send(target: str, method: str = "GET", headers: dict | None = None) -> bytes:
            _, got_headers, body = get(port, target, method, headers)
            pages.append(body + repr(got_headers).encode())
            return body

        forged = send(f"/?code=4/{marks[0]}&state=x{marks[0]}", headers={"X-C8": marks[1]})
        again = send(f"/?code=4/{marks[2]}&state=x{marks[2]}")
        assert forged == again, "the page for a wrong state is not a fixed page"
        still_waiting("a forged code with a wrong state")
        send(f"/?code=4/{marks[3]}")
        still_waiting("a code without a state")
        send(f"/favicon.ico?{marks[4]}={marks[4]}")
        still_waiting("/favicon.ico")
        send(f"/{marks[5]}?code=4/{marks[5]}&state={state}")
        still_waiting("the right state on another path")
        send(f"/?state={state}&{marks[6]}={marks[6]}")
        still_waiting("the right state with neither code nor error")
        send(f"/?error=access_denied&state=x{marks[7]}&error_description={marks[7]}")
        still_waiting("an error with a wrong state")
        send(f"/?code=4/{marks[8]}&state={marks[8]}", method="PUT")
        still_waiting("another method")
        pages.append(raw(port, f"C8{marks[9]} /?{marks[9]} HTTP/1.1\r\nHost: x\r\n\r\n".encode()))
        still_waiting("an unknown method")
        pages.append(raw(port, f"GET /?{marks[10]} HTTP/9.{marks[10]}\r\n\r\n".encode()))
        still_waiting("a malformed request line")
        pages.append(raw(port, f"{marks[11]}\r\n\r\n".encode()))
        still_waiting("a request line of one word")

        location, code = consent(google, url)
        pages.append(come_back(location)[2])
        # A second answer after the first: refused, or a fixed page; never a second exchange.
        with contextlib.suppress(OSError, http.client.HTTPException):
            pages.append(come_back(location)[2])
        assert cmd.finish() == 0, cmd.stderr
    assert google.problems == []
    assert [post["form"]["code"] for post in google.posts] == [[code]]
    for page in pages:
        assert holds_any(page.decode("utf-8", "replace"), [*marks, code, state]) == []
    assert holds_any(cmd.stdout + cmd.stderr, marks) == []
    assert out.exists()


# 4. The person refuses


def test_access_denied_with_the_right_state_ends_with_exit_1_and_writes_nothing(tmp_path, google):
    out = tmp_path / "ada-google.json"
    client = client_file(tmp_path, google)
    mark = "c8Denied7q"
    with authorize(tmp_path, "--client", str(client), "--kind", "gmail", "--out", str(out)) as cmd:
        params = params_of(cmd.url())
        port = urlsplit(params["redirect_uri"]).port
        target = f"/?error=access_denied&error_description={mark}&state={params['state']}"
        with contextlib.suppress(OSError, http.client.HTTPException):
            get(port, target)
        assert cmd.finish() == 1
    assert google.posts == []
    assert not out.exists()
    assert said_lines(cmd.stderr), "the refusal is said in words"
    assert mark not in cmd.stdout + cmd.stderr
    assert list(tmp_path.glob("ada-google*")) == []


# The runs below need no browser, or only the browser this file plays, and nothing from the
# test that checks them: they start together, once, on threads (each in its own folder, with
# its own stand-in), so the file stays fast. Each test checks its own run's outcome.

AHEAD: dict[str, Callable[[Path], Any]] = {}


def ahead(key: str):
    def register(job: Callable[[Path], Any]) -> Callable[[Path], Any]:
        AHEAD[key] = job
        return job

    return register


@pytest.fixture(scope="module")
def ran(tmp_path_factory) -> dict[str, Any]:
    base = tmp_path_factory.mktemp("c8ahead")

    def one(key: str) -> Any:
        folder = base / re.sub(r"[^A-Za-z0-9_.-]", "_", key)
        folder.mkdir()
        return AHEAD[key](folder)

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        futures = {key: pool.submit(one, key) for key in AHEAD}
    return {key: future.exception() or future.result() for key, future in futures.items()}


def outcome(ran: dict[str, Any], key: str) -> Any:
    result = ran[key]
    if isinstance(result, BaseException):
        raise result
    return result


# 5. and 6. The token endpoint says no, or says too little


def refused(tmp_path: Path, mode: tuple, kinds=("gmail",)) -> Command:
    """The whole flow against a stand-in that answers the exchange as `mode` says: exit 1,
    one exchange, nothing written, no secret said or written."""
    google = Google().start()
    try:
        google.mode = mode
        out = tmp_path / "ada-google.json"
        args = ["--client", str(client_file(tmp_path, google)), "--out", str(out)]
        for kind in kinds:
            args += ["--kind", kind]
        with authorize(tmp_path, *args) as command:
            url = command.url()
            location, _ = consent(google, url)
            with contextlib.suppress(OSError, http.client.HTTPException):
                come_back(location)
            assert command.finish() == 1, command.stderr
    finally:
        google.stop()
    assert google.problems == []
    assert len(google.posts) == 1
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("ada-google")) == []
    form = google.posts[0]["form"]
    hidden = [CLIENT_SECRET, form["code"][0], form["code_verifier"][0], *google.access_tokens,
              *google.refresh_tokens]  # fmt: skip
    assert holds_any(command.stdout + command.stderr, hidden) == []
    for path, content in written_elsewhere(command, tmp_path).items():
        assert holds_any(content, hidden) == [], f"a secret in {path}"
    return command


# The codes the notes list for the token endpoint (Google's, RFC 6749 5.2's, DPoP's), the
# contract's redirect_uri_mismatch, and one nobody lists.
TOKEN_ERRORS = (
    "invalid_grant", "invalid_client", "redirect_uri_mismatch", "invalid_request",
    "unauthorized_client", "unsupported_grant_type", "invalid_scope", "invalid_dpop_proof",
    "use_dpop_nonce", "c8BODYMARK_unknown",
)  # fmt: skip
REFUSALS = {
    **{code: ("error", code, 401 if code == "invalid_client" else 400) for code in TOKEN_ERRORS},
    "invalid_grant_as_500": ("error", "invalid_grant", 500),
    "status_500": ("status", 500, "<html>c8BODYMARK Internal Server Error</html>"),
    "status_503": ("status", 503, "c8BODYMARK unavailable"),
    "not_json_400": ("not_json", 400),
    "not_json_200": ("not_json", 200),
}
for _case, _mode in REFUSALS.items():
    ahead(f"refusal:{_case}")(lambda tmp, mode=_mode: refused(tmp, mode))
ahead("no_refresh")(lambda tmp: refused(tmp, ("no_refresh",)))
ahead("unticked")(
    lambda tmp: refused(tmp, ("grant_only", [SCOPE["gmail"]]), kinds=("gmail", "google_drive"))
)


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_refused_exchange_is_one_line_without_the_body_and_writes_nothing(ran, case):
    command = outcome(ran, f"refusal:{case}")
    assert len(said_lines(command.stderr)) == 1, command.stderr
    assert "c8BODYMARK" not in command.stdout + command.stderr


def test_the_three_named_refusals_say_three_different_things(ran):
    lines = []
    for code in ("invalid_grant", "invalid_client", "redirect_uri_mismatch"):
        (line,) = said_lines(outcome(ran, f"refusal:{code}").stderr)
        lines.append(line)
    assert len(set(lines)) == 3, lines


def test_an_answer_without_a_refresh_token_writes_nothing(ran):
    command = outcome(ran, "no_refresh")
    assert said_lines(command.stderr)
    assert "refresh" in command.stderr.lower()


def test_a_scope_left_unticked_names_its_kind_and_writes_nothing(ran):
    assert "google_drive" in outcome(ran, "unticked").stderr


# 7. --out


def test_an_existing_out_is_refused_before_anything_listens_and_is_untouched(tmp_path, google):
    the_command_exists()
    out = tmp_path / "ada-google.json"
    out.write_text("Ada's notes, not a credential\n", encoding="utf-8")
    os.chmod(out, 0o640)
    before = (out.read_bytes(), out.stat().st_mode, out.stat().st_mtime_ns)
    client = client_file(tmp_path, google)
    command = run_to_end(
        tmp_path, "--client", str(client), "--kind", "gmail", "--out", str(out),
        "--port", str(free_port()),
    )  # fmt: skip
    assert command.process.returncode == 2
    assert command.stdout == ""
    assert out.name in command.stderr
    assert command.watch.loaded() and command.watch.of("bind") == []
    assert command.watch.of("blocked") == []
    assert (out.read_bytes(), out.stat().st_mode, out.stat().st_mtime_ns) == before


def test_out_in_a_directory_that_does_not_exist_is_refused(tmp_path, google):
    the_command_exists()
    out = tmp_path / "nowhere" / "ada-google.json"
    client = client_file(tmp_path, google)
    command = run_to_end(tmp_path, "--client", str(client), "--kind", "gmail", "--out", str(out))
    assert command.process.returncode == 2
    assert command.stdout == ""
    assert "nowhere" in command.stderr
    assert command.watch.loaded() and command.watch.of("bind") == []
    assert not out.parent.exists()


def test_an_out_that_appears_while_it_waits_is_not_overwritten(tmp_path, google):
    out = tmp_path / "ada-google.json"
    client = client_file(tmp_path, google)
    with authorize(tmp_path, "--client", str(client), "--kind", "gmail", "--out", str(out)) as cmd:
        url = cmd.url()
        out.write_text("Brook's file, made meanwhile\n", encoding="utf-8")
        location, _ = consent(google, url)
        with contextlib.suppress(OSError, http.client.HTTPException):
            come_back(location)
        assert cmd.finish() != 0
    assert out.read_text(encoding="utf-8") == "Brook's file, made meanwhile\n"
    assert holds_any(cmd.stdout + cmd.stderr, [*google.refresh_tokens, CLIENT_SECRET]) == []


# 8. The client file

MARK = "c8FileMark"
MARKED = {
    "client_id": f"{MARK}-id",
    "client_secret": f"{MARK}-secret",
    "project_id": f"{MARK}-project",
    "auth_uri": f"https://{MARK}.example.test/auth",
}
NO_ID = {name: value for name, value in MARKED.items() if name != "client_id"}
CLIENT_FILES = {
    "missing": (None, None),
    "not_json": (f"{{installed: {MARK} = 1", None),
    "web": (json.dumps({"web": MARKED}), "desktop app"),
    "no_client_secret": (
        json.dumps({"installed": {**MARKED, "client_secret": None}}),
        "client_secret",
    ),
    "no_client_id": (json.dumps({"installed": NO_ID}), "client_id"),
    "installed_not_an_object": (json.dumps({"installed": [MARK]}), None),
    "a_list": (json.dumps([{"installed": MARKED}]), None),
}


def with_client_text(tmp_path: Path, text: str | None) -> Command:
    path = tmp_path / "client_secret_c8.json"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    out = tmp_path / "ada-google.json"
    return run_to_end(tmp_path, "--client", str(path), "--kind", "gmail", "--out", str(out))


for _case, (_text, _) in CLIENT_FILES.items():
    ahead(f"client:{_case}")(lambda tmp, text=_text: with_client_text(tmp, text))


@pytest.mark.parametrize("case", sorted(CLIENT_FILES))
def test_a_client_file_cairn_cannot_use_is_one_line_without_its_values(ran, case):
    command = outcome(ran, f"client:{case}")
    named = CLIENT_FILES[case][1]
    assert command.process.returncode == 2
    assert command.stdout == ""
    assert len(said_lines(command.stderr)) == 1, command.stderr
    assert MARK not in command.stderr
    if named is not None:
        assert named in command.stderr.lower()
    assert command.watch.loaded() and command.watch.of("bind") == []
    assert not (command.watch.path.parent / "ada-google.json").exists()


# 9. The kinds and 11. the other options: refused before anything listens


def with_options(tmp_path: Path, *args: str, drop: str | None = None) -> Command:
    """The command with a good client file, --kind gmail and a free --out (less `drop`),
    and `args` after them."""
    out = tmp_path / "ada-google.json"
    given = {"--client": str(client_file(tmp_path)), "--kind": "gmail", "--out": str(out)}
    if args and args[0] == "--kind":
        del given["--kind"]
    base = [part for name, value in given.items() if name != drop for part in (name, value)]
    return run_to_end(tmp_path, *base, *args)


NOT_GOOGLE = ["granola", "langfuse", "calendar"]
for _kind in NOT_GOOGLE:
    ahead(f"kind:{_kind}")(lambda tmp, kind=_kind: with_options(tmp, "--kind", kind))
ahead("kind:mixed")(lambda tmp: with_options(tmp, "--kind", "gmail", "--kind", "granola"))
ahead("kind:none")(lambda tmp: with_options(tmp, drop="--kind"))
WAITS_REFUSED = ["29", "901", "0", "-30", "soon"]
for _wait in WAITS_REFUSED:
    ahead(f"wait:{_wait}")(lambda tmp, wait=_wait: with_options(tmp, f"--wait={wait}"))
OPTIONS = {
    "--client": ("--client", []),
    "--out": ("--out", []),
    "--db": (None, ["--db", "cairn.db"]),
    "--open-browser": (None, ["--open-browser"]),
}
for _named, (_drop, _extra) in OPTIONS.items():
    ahead(f"option:{_named}")(lambda tmp, d=_drop, e=_extra: with_options(tmp, *e, drop=d))


def refused_before_listening(command: Command) -> None:
    assert command.process.returncode == 2, command.stderr
    assert command.stdout == ""
    assert command.watch.loaded() and command.watch.of("bind") == []
    assert not (command.watch.path.parent / "ada-google.json").exists()


@pytest.mark.parametrize("kind", [*NOT_GOOGLE, "mixed"])
def test_a_kind_that_is_not_google_is_refused_naming_the_kinds_it_takes(ran, kind):
    command = outcome(ran, f"kind:{kind}")
    refused_before_listening(command)
    for taken in SCOPE:
        assert taken in command.stderr


def test_no_kind_is_refused(ran):
    command = outcome(ran, "kind:none")
    refused_before_listening(command)
    assert "--kind" in command.stderr


@pytest.mark.parametrize("wait", WAITS_REFUSED)
def test_a_wait_out_of_its_bounds_is_refused(ran, wait):
    command = outcome(ran, f"wait:{wait}")
    refused_before_listening(command)
    assert "--wait" in command.stderr


@pytest.mark.parametrize("named", sorted(OPTIONS))
def test_wrong_or_missing_options_exit_2(ran, named):
    command = outcome(ran, f"option:{named}")
    refused_before_listening(command)
    assert named in command.stderr
    assert not (command.watch.path.parent / "cairn.db").exists()


def until_the_url(tmp_path: Path, *args: str) -> tuple[Command, str]:
    """The command up to its URL; it is ended there, before any browser comes back."""
    with authorize(tmp_path, *args) as command:
        url = command.url()
        assert command.watch.loaded()
    return command, url


WAITS_TAKEN = ["30", "900"]
for _wait in WAITS_TAKEN:
    ahead(f"wait_taken:{_wait}")(
        lambda tmp, wait=_wait: until_the_url(
            tmp,
            "--client",
            str(client_file(tmp)),
            "--kind",
            "gmail",
            "--out",
            str(tmp / "ada-google.json"),
            "--wait",
            wait,
        )
    )


@pytest.mark.parametrize("wait", WAITS_TAKEN)
def test_a_wait_at_its_bounds_is_taken(ran, wait):
    _, url = outcome(ran, f"wait_taken:{wait}")
    params_of(url)


# 10. Where it sends what

ELSEWHERE = {
    "googles_older_addresses": (
        "https://accounts.google.com/o/oauth2/auth", "https://accounts.google.com/o/oauth2/token"
    ),
    "another_host": (
        "https://accounts.other.example.test/o/oauth2/v2/auth",
        "https://oauth2.other.example.test/token",
    ),
    "a_lookalike_of_127": (
        "http://127.0.0.1.other.example.test:8080/auth", "http://127.0.0.1.other.example.test/t"
    ),
    "a_user_part": (
        "http://127.0.0.1:8080@other.example.test/auth", "http://localhost@other.example.test/t"
    ),
    "a_lookalike_of_localhost": (
        "http://localhost.other.example.test/auth", "http://localhost.other.example.test/t"
    ),
}  # fmt: skip
for _case, (_auth, _token) in ELSEWHERE.items():
    ahead(f"elsewhere:{_case}")(
        lambda tmp, a=_auth, t=_token: until_the_url(
            tmp,
            "--client",
            str(client_file(tmp, None, auth_uri=a, token_uri=t)),
            "--kind",
            "gmail",
            "--out",
            str(tmp / "ada-google.json"),
        )
    )


@pytest.mark.parametrize("case", sorted(ELSEWHERE))
def test_the_url_is_googles_own_whatever_the_file_names_elsewhere(ran, case):
    command, url = outcome(ran, f"elsewhere:{case}")
    assert base_of(url) == GOOGLE_AUTH
    params_of(url)
    assert command.watch.of("blocked") == []
    assert not (command.watch.path.parent / "ada-google.json").exists()


def test_an_auth_uri_on_localhost_is_used_as_it_is(tmp_path, google):
    local = f"http://localhost:{google.port}/o/oauth2/v2/auth"
    client = client_file(tmp_path, google, auth_uri=local)
    _, url = until_the_url(
        tmp_path, "--client", str(client), "--kind", "gmail",
        "--out", str(tmp_path / "ada-google.json"),
    )  # fmt: skip
    assert base_of(url) == local
    params_of(url)


def test_a_token_uri_on_another_host_is_not_obeyed(tmp_path, google):
    """The exchange goes to Google's own token address, never to the file's. The guard refuses
    the lookup in the child, so nothing leaves this machine; the test checks the guard is in
    place before it lets the browser come back."""
    out = tmp_path / "ada-google.json"
    other = "https://oauth2.other.example.test/token"
    client = client_file(tmp_path, google, token_uri=other)
    with authorize(tmp_path, "--client", str(client), "--kind", "gmail", "--out", str(out)) as cmd:
        url = cmd.url()
        assert cmd.watch.loaded(), "no guard in the child: the browser does not come back"
        location, _ = consent(google, url)
        with contextlib.suppress(OSError, http.client.HTTPException):
            come_back(location)
        assert cmd.finish() != 0
    blocked = {entry[0] for entry in cmd.watch.of("blocked")}
    assert blocked == {"oauth2.googleapis.com"}, blocked
    assert google.posts == []
    assert not out.exists()


def test_help_says_where_to_run_it_what_the_client_is_and_that_out_is_never_overwritten(capsys):
    with pytest.raises(SystemExit) as stopped:
        server.main(["google-authorize", "--help"])
    assert stopped.value.code == 0
    said = " ".join(capsys.readouterr().out.split()).lower()
    assert "browser" in said
    assert "desktop app" in said
    assert "overwrite" in said
