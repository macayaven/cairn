"""Offline fixture tests: real HTTP handler, isolated command side effects."""

import importlib.util
import socket
import threading
from pathlib import Path
from unittest.mock import patch

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
