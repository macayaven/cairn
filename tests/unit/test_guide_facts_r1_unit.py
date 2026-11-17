"""Sentences of deploy/README.md and infra/gcp/README.md about the health check and about
one server per database (R1), each pinned here so the guides cannot go false unnoticed: the
guide's sentence is in each test's docstring. The probes are the ones the files run (the
Dockerfile's HEALTHCHECK, host.sh's healthz), read from the files at test time and run
against a real cairn server on a loopback port, with made-up data."""

import json
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

from cairn.server import create_app
from cairn.store import Store
from cairn.web import cli

REPO = Path(__file__).resolve().parents[2]
DEPLOY = (REPO / "deploy" / "README.md").read_text(encoding="utf-8")
INFRA = (REPO / "infra" / "gcp" / "README.md").read_text(encoding="utf-8")
DOCKERFILE = (REPO / "Dockerfile").read_text(encoding="utf-8")
HOST_SH = (REPO / "infra" / "gcp" / "files" / "host.sh").read_text(encoding="utf-8")
COMPOSE = (REPO / "deploy" / "compose.yaml").read_text(encoding="utf-8")
MONITORING = (REPO / "infra" / "gcp" / "monitoring.tf").read_text(encoding="utf-8")


def one_line(text: str) -> str:
    return " ".join(text.split())


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def serving(store: Store) -> Iterator[int]:
    """cairn's app on a loopback port, served by uvicorn as `serve` serves it."""
    port = free_port()
    config = uvicorn.Config(
        create_app(store, public_url=f"http://127.0.0.1:{port}"),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30  # a hang guard on the real server, and nothing else
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(30)


def docker_probe() -> list[str]:
    """The Dockerfile's HEALTHCHECK command, with this Python for `python`."""
    found = re.search(r"^HEALTHCHECK .*?\\\n\s*CMD (\[.*\])$", DOCKERFILE, re.M)
    assert found, "no HEALTHCHECK ... CMD [...] in the Dockerfile"
    command = json.loads(found.group(1))
    assert command[0] == "python"
    return [sys.executable, *command[1:]]


def host_probe() -> list[str]:
    """host.sh's healthz: the `python -c` it runs in the container, with this Python."""
    found = re.search(
        r"^healthz\(\) \{\n  docker exec cairn (python -c .*?)>/dev/null", HOST_SH, re.M | re.S
    )
    assert found, "no healthz() in host.sh"
    command = shlex.split(found.group(1).replace("\\\n", " "))
    assert command[0] == "python"
    return [sys.executable, *command[1:]]


def run_on(command: list[str], port: int) -> int:
    """Run a probe against `port` instead of 8765, the port both files name."""
    script = command[-1]
    assert "8765" in script
    command = [*command[:-1], script.replace("8765", str(port))]
    return subprocess.run(command, capture_output=True, timeout=30).returncode


# deploy/README.md


def test_one_serve_per_database_as_the_deploy_guide_says():
    """One `serve` runs on a database, and cairn holds to it: a second `serve` on the same
    file (a scaled `cairn` service, a second stack on the same volume) stops at start with
    `another server is running on this database (/data/cairn.db): stop it before starting
    another`, exit code 2, and changes nothing."""
    guide = one_line(DEPLOY)
    assert "--db /data/cairn.db" in COMPOSE
    said = cli.ANOTHER_SERVER.format(db="/data/cairn.db")
    assert f"stops at start with `{said}`, exit code 2, and changes nothing" in guide
    assert "Never scale the `cairn` service" in guide
    assert "restart: unless-stopped" in COMPOSE  # "Compose would start the refused copy again"
    assert "run beside the server as before" in guide


def test_the_container_probe_as_the_deploy_guide_says(tmp_path):
    """The image's health check asks cairn's `/healthz` every 30 seconds. It answers 200
    only while cairn can read its database ..., and 503 when the database is closed ....
    After 3 failed checks in a row `docker compose ps` shows cairn as unhealthy."""
    guide = one_line(DEPLOY)
    assert "The image's health check asks cairn's `/healthz` every 30 seconds." in guide
    assert "After 3 failed checks in a row `docker compose ps` shows cairn as unhealthy." in guide
    assert "HEALTHCHECK --interval=30s " in DOCKERFILE and " --retries=3 " in DOCKERFILE
    assert "Compose does not restart it for that (it restarts cairn when cairn exits)" in guide
    said = "the store is unusable until it is opened again"
    assert f"`docker compose logs cairn` says so (`a rollback failed: {said}`)" in guide
    store_code = (REPO / "src" / "cairn" / "store" / "base.py").read_text(encoding="utf-8")
    assert f'"a rollback failed: {said}"' in store_code
    store = Store(tmp_path / "cairn.db")
    try:
        with serving(store) as port:
            assert run_on(docker_probe(), port) == 0
            store.close()  # closed underneath the server: 503
            assert run_on(docker_probe(), port) == 1
    finally:
        store.close()


def test_a_compaction_is_busy_not_broken_as_the_deploy_guide_says(tmp_path):
    """While a compaction holds the database it answers 200: cairn is busy, not broken."""
    assert "While a compaction holds the database it answers 200" in one_line(DEPLOY)
    store = Store(tmp_path / "cairn.db")
    try:
        # The store's lock is what compact holds for the whole rewrite of the file.
        with serving(store) as port, store._lock:
            assert run_on(docker_probe(), port) == 0
    finally:
        store.close()


# infra/gcp/README.md


def test_the_updater_promotes_on_200_only_as_the_hosted_guide_says(tmp_path):
    """... waits up to 60 seconds for `/healthz` to answer 200 twice in a row. ...
    `/healthz` answers 200 only once cairn has opened its database and can read it, with the
    schema this cairn knows; an image that cannot, whether it exits at start or answers 503,
    is not promoted."""
    guide = one_line(INFRA)
    assert "waits up to 60 seconds for `/healthz` to answer 200 twice in a row" in guide
    assert "whether it exits at start or answers 503, is not promoted" in guide
    assert "HEALTH_SECONDS=60" in HOST_SH and '[ "$streak" -ge 2 ]' in HOST_SH
    store = Store(tmp_path / "cairn.db")
    try:
        with serving(store) as port:
            assert run_on(host_probe(), port) == 0
            store.close()
            assert run_on(host_probe(), port) != 0  # urlopen raises on a 503
    finally:
        store.close()


def test_the_alert_as_the_hosted_guide_says():
    """The alert fires until `https://DOMAIN/healthz` answers 200. ... Later it fires while
    cairn is down or answers 503: it cannot read its database, the database holds a schema
    version this cairn does not know, or a write failed and could not be undone ...."""
    guide = one_line(INFRA)
    assert "The alert fires until `https://DOMAIN/healthz` answers 200." in guide
    assert "Later it fires while cairn is down or answers 503" in guide
    assert 'path         = "/healthz"' in MONITORING
    assert "`sudo systemctl restart cairn.service`" in guide


@pytest.mark.parametrize(
    "sentence",
    [
        "An image that fails `/healthz` (no answer, or 503 because it cannot read the"
        " database) is rolled back",
    ],
)
def test_the_hosted_guide_says_a_503_fails_the_check(sentence):
    assert sentence in one_line(INFRA)
