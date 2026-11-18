#!/usr/bin/env python3
"""CI-only isolated metadata stand-in for the unchanged host rehearsal."""

import contextlib
import http.server
import os
import signal
import socket
import socketserver
import subprocess
import sys
import time
from pathlib import Path

NAMESPACE = "cairn-ci-meta"
HOST = "cairnmeta0"
PEER = "cairnmeta1"
ADDRESS = "169.254.169.254"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"cairn-ci-metadata-fixture\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class Server(http.server.HTTPServer):
    allow_reuse_address = True

    def server_bind(self):
        # HTTPServer.server_bind asks for the address's name before the socket listens.
        # Nothing here uses the name, and with no resolver answering the lookup alone can
        # take the ten seconds wait_ready allows.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def serve_process():
    return subprocess.Popen(
        [
            "ip",
            "netns",
            "exec",
            NAMESPACE,
            sys.executable,
            str(Path(__file__).resolve()),
            "--serve",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_ready(server):
    end = time.monotonic() + 10
    last_observation = "no probe completed"
    while time.monotonic() < end:
        status = server.poll()
        if status is not None:
            raise RuntimeError(f"metadata fixture server exited (exit code: {status})")
        try:
            with socket.create_connection((ADDRESS, 80), timeout=0.2) as client:
                client.sendall(b"GET / HTTP/1.0\r\n\r\n")
                response = client.recv(1024)
                if b"200 OK" in response:
                    return
                last_observation = "unexpected HTTP status" if response else "empty response"
        except OSError as error:
            last_observation = type(error).__name__
            if isinstance(error.errno, int):
                last_observation += f" errno={error.errno}"
        time.sleep(0.05)
    raise RuntimeError(
        f"metadata fixture did not become ready (last observation: {last_observation})"
    )


def run_rehearsal(command, guard=contextlib.nullcontext):
    process = None
    weak = False
    tail = b""
    try:
        with guard():
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True
            )
        while chunk := os.read(process.stdout.fileno(), 8192):
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            combined = tail + chunk
            weak |= b"WEAKER CHECK" in combined
            tail = combined[-11:]
        return process.wait(), weak
    finally:
        if process is not None:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            process.stdout.close()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--serve"]:
        Server((ADDRESS, 80), Handler).serve_forever()
        return 0
    if not args or args[0] != "--" or len(args) < 2:
        raise ValueError("expected -- followed by the rehearsal command")
    creating = False
    cancellation_pending = False

    def interrupted(signum, frame):
        nonlocal cancellation_pending
        if creating:
            cancellation_pending = True
        else:
            raise KeyboardInterrupt

    @contextlib.contextmanager
    def creation():
        nonlocal creating
        creating = True
        try:
            yield
        finally:
            creating = False
        if cancellation_pending:
            raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
    owned = False
    link = False
    server = None
    installed_rules = []
    try:
        with creation():
            subprocess.run(["ip", "netns", "add", NAMESPACE], check=True)
            owned = True
        with creation():
            subprocess.run(
                ["ip", "link", "add", HOST, "type", "veth", "peer", "name", PEER], check=True
            )
            link = True
        rule = ["-d", ADDRESS, "!", "-o", HOST, "-j", "REJECT"]
        for chain in ("OUTPUT", "FORWARD"):
            with creation():
                subprocess.run(["iptables", "-w", "-I", chain, "1", *rule], check=True)
                installed_rules.append(chain)
        for command in (
            ["ip", "link", "set", PEER, "netns", NAMESPACE],
            ["ip", "addr", "add", "169.254.169.253/30", "dev", HOST],
            ["ip", "link", "set", HOST, "up"],
            ["ip", "route", "add", ADDRESS + "/32", "dev", HOST],
            ["ip", "-n", NAMESPACE, "link", "set", "lo", "up"],
            ["ip", "-n", NAMESPACE, "addr", "add", ADDRESS + "/30", "dev", PEER],
            ["ip", "-n", NAMESPACE, "link", "set", PEER, "up"],
            ["ip", "-n", NAMESPACE, "route", "add", "default", "via", "169.254.169.253"],
        ):
            subprocess.run(command, check=True)
        with creation():
            server = serve_process()
        wait_ready(server)
        status, weak = run_rehearsal(args[1:], creation)
        if weak:
            print("metadata fixture: weaker check is forbidden", file=sys.stderr)
            return status or 1
        return status
    finally:
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        try:
            if server is not None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
        finally:
            try:
                try:
                    errors = []
                    for chain in reversed(installed_rules):
                        try:
                            subprocess.run(["iptables", "-w", "-D", chain, *rule], check=True)
                        except (OSError, subprocess.SubprocessError) as error:
                            errors.append(error)
                    if errors:
                        raise errors[0]
                finally:
                    if link:
                        subprocess.run(["ip", "link", "del", HOST], check=True)
            finally:
                try:
                    if owned:
                        subprocess.run(["ip", "netns", "del", NAMESPACE], check=True)
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
