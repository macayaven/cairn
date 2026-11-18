"""Offline fixture tests: real HTTP handler, isolated command side effects."""

import importlib.util
import socket
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

PATH = Path(__file__).resolve().parents[2] / "scripts/ci_metadata_fixture.py"


def fixture():
    spec = importlib.util.spec_from_file_location("ci_metadata_fixture", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_http_responder_returns_same_constant_for_arbitrary_paths():
    m = fixture()
    server = m.Server(("127.0.0.1", 0), m.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        answers = []
        for path in ("/", "/not-a-file"):
            with socket.create_connection(server.server_address, timeout=2) as client:
                client.sendall(f"GET {path} HTTP/1.0\r\n\r\n".encode())
                data = b""
                while chunk := client.recv(4096):
                    data += chunk
                answers.append(data.split(b"\r\n\r\n", 1)[1])
        assert answers == [b"cairn-ci-metadata-fixture\n"] * 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_weak_check_fails_closed_and_owned_namespace_is_removed(capsys):
    m = fixture()
    commands = []

    def run(args, **kwargs):
        commands.append(args)

        class Result:
            returncode = 0

        return Result()

    with (
        patch.object(m.subprocess, "run", side_effect=run),
        patch.object(m, "serve_process") as serve,
        patch.object(m, "wait_ready"),
        patch.object(m, "run_rehearsal", return_value=(0, True)),
    ):
        assert m.main(["--", "actual-rehearsal"]) != 0
    assert ["ip", "netns", "del", m.NAMESPACE] in commands
    serve.return_value.terminate.assert_called_once()


def test_namespace_add_failure_never_deletes_existing_namespace():
    m = fixture()
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        raise m.subprocess.CalledProcessError(1, args)

    with (
        patch.object(m.subprocess, "run", side_effect=run),
        pytest.raises(m.subprocess.CalledProcessError),
    ):
        m.main(["--", "actual-rehearsal"])
    assert not any(c[:3] == ["ip", "netns", "del"] for c in commands)


def test_rehearsal_output_gate_detects_split_marker_and_preserves_status(capfd):
    import sys

    m = fixture()
    status, weak = m.run_rehearsal(
        [
            sys.executable,
            "-c",
            "import os,time; os.write(1,b'WEAKER '); time.sleep(.1); os.write(1,b'CHECK\\n')",
        ]
    )
    assert status == 0
    assert weak
    assert "WEAKER CHECK" in capfd.readouterr().out


def test_setup_failure_after_namespace_creation_cleans_owned_resources():
    m = fixture()
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        if args[:3] == ["ip", "addr", "add"]:
            raise m.subprocess.CalledProcessError(1, args)

    with (
        patch.object(m.subprocess, "run", side_effect=run),
        pytest.raises(m.subprocess.CalledProcessError),
    ):
        m.main(["--", "actual-rehearsal"])
    assert ["ip", "link", "del", m.HOST] in commands
    assert ["ip", "netns", "del", m.NAMESPACE] in commands


def test_cancel_during_namespace_creation_removes_only_created_namespace():
    import os
    import signal

    m = fixture()
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        if args == ["ip", "netns", "add", m.NAMESPACE]:
            os.kill(os.getpid(), signal.SIGTERM)

    with patch.object(m.subprocess, "run", side_effect=run), pytest.raises(KeyboardInterrupt):
        m.main(["--", "actual-rehearsal"])
    assert commands == [["ip", "netns", "add", m.NAMESPACE], ["ip", "netns", "del", m.NAMESPACE]]


def test_fixture_blocks_metadata_traffic_routed_outside_its_veth():
    m = fixture()
    commands = []

    def run(args, **kwargs):
        commands.append(args)

    with (
        patch.object(m.subprocess, "run", side_effect=run),
        patch.object(m, "serve_process"),
        patch.object(m, "wait_ready"),
        patch.object(m, "run_rehearsal", return_value=(0, False)),
    ):
        assert m.main(["--", "actual-rehearsal"]) == 0
    for chain in ("OUTPUT", "FORWARD"):
        rule = ["-d", m.ADDRESS, "!", "-o", m.HOST, "-j", "REJECT"]
        assert ["iptables", "-w", "-I", chain, "1", *rule] in commands
        assert ["iptables", "-w", "-D", chain, *rule] in commands


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        (
            ConnectionRefusedError(111, "private synthetic detail"),
            "ConnectionRefusedError errno=111",
        ),
        (TimeoutError("private synthetic detail"), "TimeoutError"),
        (b"", "empty response"),
        (
            b"HTTP/1.0 503 private synthetic detail\r\nX-Private: hidden\r\n\r\nhidden body",
            "unexpected HTTP status",
        ),
    ],
    ids=["refused", "timeout", "empty", "unexpected-status"],
)
def test_readiness_timeout_reports_only_safe_last_observation(observation, expected):
    m = fixture()
    server = Mock()
    server.poll.return_value = None
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    if isinstance(observation, OSError):
        connection = patch.object(m.socket, "create_connection", side_effect=observation)
    else:
        client.recv.return_value = observation
        connection = patch.object(m.socket, "create_connection", return_value=client)

    with (
        patch.object(m.time, "monotonic", side_effect=[0, 0, 10]),
        patch.object(m.time, "sleep"),
        connection,
        pytest.raises(RuntimeError) as error,
    ):
        m.wait_ready(server)
    assert (
        str(error.value) == f"metadata fixture did not become ready (last observation: {expected})"
    )


def test_readiness_timeout_uses_latest_observation_instead_of_stale_error():
    m = fixture()
    server = Mock()
    server.poll.return_value = None
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.recv.return_value = b""
    with (
        patch.object(m.time, "monotonic", side_effect=[0, 0, 0, 10]),
        patch.object(m.time, "sleep"),
        patch.object(
            m.socket,
            "create_connection",
            side_effect=[ConnectionRefusedError(111, "hidden"), client],
        ),
        pytest.raises(RuntimeError, match=r"last observation: empty response\)$"),
    ):
        m.wait_ready(server)


@pytest.mark.parametrize("status", [0, 2, -15])
def test_readiness_early_exit_reports_numeric_child_status(status):
    m = fixture()
    server = Mock()
    server.poll.return_value = status
    with (
        patch.object(m.time, "monotonic", side_effect=[0, 0]),
        patch.object(
            m.socket, "create_connection", side_effect=AssertionError("no probe after exit")
        ),
        pytest.raises(RuntimeError) as error,
    ):
        m.wait_ready(server)
    assert str(error.value) == f"metadata fixture server exited (exit code: {status})"


def test_readiness_still_accepts_existing_success_check_without_sleep():
    m = fixture()
    server = Mock()
    server.poll.return_value = None
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.recv.return_value = b"HTTP/1.0 200 OK\r\n\r\n"
    with (
        patch.object(m.time, "monotonic", side_effect=[0, 0]),
        patch.object(m.time, "sleep", side_effect=AssertionError("no sleep after readiness")),
        patch.object(m.socket, "create_connection", return_value=client),
    ):
        assert m.wait_ready(server) is None


def test_the_server_listens_without_looking_its_address_up():
    """http.server.HTTPServer asks for its address's name before it listens. With no
    resolver answering, that one lookup can take the ten seconds wait_ready allows."""
    m = fixture()
    with patch.object(m.socket, "getfqdn", side_effect=AssertionError("looked up")) as lookup:
        server = m.Server(("127.0.0.1", 0), m.Handler)
    try:
        assert lookup.call_count == 0
        with socket.create_connection(server.server_address, timeout=2):
            pass  # it listens: the connection is accepted into the backlog
    finally:
        server.server_close()
