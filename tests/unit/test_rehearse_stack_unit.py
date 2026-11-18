"""The parts of scripts/rehearse_stack.py that decide a pass or a fail, without Docker.

The rehearsal itself runs in CI, where Docker is. What it concludes from a log or a command
line is plain text handling, tested here.
"""

import importlib.util
import json
import re
import socket
import threading
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rehearse_stack.py"
spec = importlib.util.spec_from_file_location("rehearse_stack", SCRIPT)
rehearse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rehearse)


def test_a_secret_in_a_log_is_named_and_not_shown():
    secrets = {"the API key": "k-plain-value", "the invitation token": "tok-123", "empty": ""}
    assert rehearse.leaks("GET /join/redacted?redacted 200", secrets) == []
    assert rehearse.leaks('{"uri":"/join/tok-123"} x-api-key: k-plain-value', secrets) == [
        "the API key",
        "the invitation token",
    ]


def test_request_addresses_are_read_from_caddys_json_lines():
    log = "\n".join(
        [
            json.dumps({"level": "info", "request": {"uri": "/join/redacted?redacted"}}),
            "not json at all",
            json.dumps({"level": "info", "msg": "no request here"}),
            json.dumps({"request": {"uri": "/healthz"}}),
            json.dumps(["a", "list"]),
        ]
    )
    assert rehearse.logged_uris(log) == ["/join/redacted?redacted", "/healthz"]


def test_the_trusted_networks_are_read_from_a_command_line():
    command = ["serve", "--db", "/data/cairn.db", "--trusted-proxy", "10.213.47.0/24", "--host"]
    assert rehearse.trusted_networks(command) == ["10.213.47.0/24"]
    assert rehearse.trusted_networks(["serve", "--trusted-proxy"]) == []
    two = ["--trusted-proxy", "10.0.0.0/8", "--trusted-proxy", "127.0.0.1"]
    assert rehearse.trusted_networks(two) == ["10.0.0.0/8", "127.0.0.1"]


def test_service_states_are_read_from_either_shape_of_compose_ps_json():
    lines = "\n".join(
        [
            json.dumps({"Service": "litestream-restore", "State": "exited", "ExitCode": 1}),
            json.dumps({"Service": "cairn", "State": "created", "ExitCode": 0}),
        ]
    )
    expected = {"litestream-restore": ("exited", 1), "cairn": ("created", 0)}
    assert rehearse.ps_states(lines) == expected
    array = json.dumps([{"Service": "cairn", "State": "running", "ExitCode": None}])
    assert rehearse.ps_states(array) == {"cairn": ("running", 0)}
    assert rehearse.ps_states("") == {}


def test_the_override_changes_nothing_the_shipped_files_decide():
    """It adds a directory for the replica and the step that makes it writable; it names no
    command, port, image of the stack's own services, or network."""
    override = rehearse.OVERRIDE
    for shipped in ("command:", "ports:", "networks:", "caddy", "env_file"):
        assert shipped not in override, shipped
    assert override.count("rehearsal-replica:/replica") == 3


def _mounted_at_data(service: dict) -> bool:
    volumes = [str(volume) for volume in service.get("volumes", [])]
    tmpfs = service.get("tmpfs", [])
    tmpfs = [tmpfs] if isinstance(tmpfs, str) else list(tmpfs)
    return any(v.split(":")[1] == "/data" for v in volumes if ":" in v) or "/data" in tmpfs


@pytest.mark.parametrize("name", ["compose.yaml", "compose.backup.yaml", "compose.local.yaml", ""])
def test_no_service_of_the_cairn_image_gets_a_volume_nobody_named(name):
    """The image declares /data a volume, so a service that mounts nothing there is given a
    new unnamed volume by Docker each time its container is made. `down` leaves those, and
    the rehearsal brings the stack down three times with its named volumes kept."""
    deploy = SCRIPT.parents[1] / "deploy"
    text = (deploy / name).read_text() if name else rehearse.OVERRIDE
    checked = 0
    for service, settings in yaml.safe_load(text)["services"].items():
        if "CAIRN_IMAGE" not in str(settings.get("image", "")):
            continue
        checked += 1
        assert _mounted_at_data(settings), f"{service} in {name or 'the override'}"
    assert checked, "no service of the cairn image was read"


# What `docker inspect` prints for a container, in the form the rehearsal asks for: the
# service's name, then its mounts. The long name is one Docker made up.
MADE_UP = "3f1c9a0e" * 8
INSPECTED = "\n".join(
    [
        "cairn " + json.dumps([{"Type": "volume", "Name": "p_cairn-data", "Destination": "/data"}]),
        "caddy "
        + json.dumps(
            [
                {"Type": "bind", "Source": "/srv/deploy/Caddyfile", "Destination": "/etc/caddy"},
                {"Type": "volume", "Name": "p_caddy-data", "Destination": "/data"},
            ]
        ),
        "replica-init "
        + json.dumps(
            [
                {"Type": "volume", "Name": MADE_UP, "Destination": "/data"},
                {"Type": "volume", "Name": "p_rehearsal-replica", "Destination": "/replica"},
            ]
        ),
    ]
)


def test_a_volume_nobody_named_is_read_from_a_containers_mounts():
    assert rehearse.unnamed_volumes(INSPECTED, "p") == ["replica-init at /data"]


def test_a_container_with_no_mounts_and_an_empty_answer_hold_none():
    assert rehearse.unnamed_volumes("cairn []\n\ncaddy null", "p") == []
    assert rehearse.unnamed_volumes("", "p") == []


def test_another_projects_volume_is_not_this_projects_named_one():
    line = "cairn " + json.dumps([{"Type": "volume", "Name": "px_data", "Destination": "/data"}])
    assert rehearse.unnamed_volumes(line, "p") == ["cairn at /data"]


@pytest.fixture
def staged(monkeypatch):
    """A rehearsal whose commands are recorded and answered here: one container, holding
    what INSPECTED says."""
    calls = []

    def run(*args, env=None, check=True):
        calls.append(args)
        if args[:3] == ("docker", "ps", "-aq") and any("label=" in arg for arg in args):
            return "0a1b2c"
        if args[:2] == ("docker", "inspect"):
            return INSPECTED
        return ""

    monkeypatch.setattr(rehearse, "run", run)
    rehearsal = rehearse.Rehearsal("cairn:made-up")
    rehearsal.project = "p"
    yield rehearsal, calls
    rehearse.shutil.rmtree(rehearsal.tmp, ignore_errors=True)


def test_the_rehearsal_looks_at_its_containers_before_it_takes_them_down(staged):
    rehearsal, calls = staged

    rehearsal.compose("down")

    assert rehearsal.unnamed == {"replica-init at /data"}
    looked = next(i for i, args in enumerate(calls) if args[:2] == ("docker", "inspect"))
    down = next(i for i, args in enumerate(calls) if args[-1] == "down")
    assert looked < down
    assert "0a1b2c" in calls[looked]


def test_a_command_that_removes_no_container_is_not_looked_at(staged):
    rehearsal, calls = staged

    rehearsal.compose("ps", "-q", "cairn")

    assert rehearsal.unnamed == set()
    assert not [args for args in calls if args[:2] == ("docker", "inspect")]


def test_cleanup_fails_on_a_volume_nobody_named_and_says_where(staged, monkeypatch):
    rehearsal, _ = staged
    monkeypatch.setattr(rehearse, "unnamed_volumes", lambda inspected, project: [])
    rehearsal.unnamed = {"replica-init at /data"}

    with pytest.raises(rehearse.Failed, match="replica-init at /data"):
        rehearsal.cleanup()


def test_cleanup_passes_when_every_volume_had_a_name(staged, monkeypatch):
    rehearsal, _ = staged
    monkeypatch.setattr(rehearse, "unnamed_volumes", lambda inspected, project: [])

    assert "no container, volume" in rehearsal.cleanup()


def _serve_once(answer: bytes, *, read_body: bool) -> tuple[int, list[int], threading.Thread]:
    """A one-request HTTP server on loopback: (port, [bytes of body it read], its thread)."""
    listener = socket.create_server(("127.0.0.1", 0))
    read: list[int] = []

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            head = b""
            while b"\r\n\r\n" not in head:
                head += connection.recv(4096)
            head, _, body = head.partition(b"\r\n\r\n")
            length = int(re.search(rb"Content-Length: (\d+)", head).group(1))
            got = len(body)
            while read_body and got < length:
                got += len(connection.recv(65536))
            read.append(got)
            connection.sendall(answer)
        listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return listener.getsockname()[1], read, thread


def test_a_refusal_that_comes_before_the_body_is_read_as_its_status():
    refusal = b"HTTP/1.1 413 Request Entity Too Large\r\nConnection: close\r\n\r\n"
    port, read, thread = _serve_once(refusal, read_body=False)

    status = rehearse.announced(
        f"http://127.0.0.1:{port}/api/projects", 5 << 20, {"x-api-key": "x"}
    )

    thread.join(5)
    assert status == 413
    assert read == [0]  # the server closed without taking the body, as a proxy does


def test_a_body_the_server_takes_is_sent_whole():
    port, read, thread = _serve_once(
        b"HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n", read_body=True
    )

    status = rehearse.announced(f"http://127.0.0.1:{port}/api/projects", 200_000)

    thread.join(5)
    assert status == 401
    assert read == [200_000]


def test_later_checks_still_run_after_one_fails(capsys):
    ran = []

    def starts() -> str:
        ran.append("starts")
        return "up"

    def breaks() -> str:
        ran.append("breaks")
        raise rehearse.Failed("the answer was 500")

    def later() -> str:
        ran.append("later")
        return "fine"

    passed = rehearse.run_checks([starts, breaks, later])

    assert ran == ["starts", "breaks", "later"]
    assert passed == 2
    said = capsys.readouterr().out
    assert "FAIL breaks: the answer was 500" in said
    assert "PASS later" in said


def test_nothing_runs_when_the_stack_does_not_start(capsys):
    ran = []

    def starts() -> str:
        raise rehearse.Failed("the stack is not serving")

    def later() -> str:
        ran.append("later")
        return "fine"

    assert rehearse.run_checks([starts, later]) == 0
    assert ran == []
    assert "FAIL starts: the stack is not serving" in capsys.readouterr().out


# --- the replica alert's condition, evaluated as Cloud Logging would ---------------------------

MONITORING = SCRIPT.parents[1] / "infra" / "gcp" / "monitoring.tf"

# Lines Litestream 0.5.18 printed when its replica (a directory) was refused, at start and
# while running, and the line it prints when it cannot start (gs:// without credentials).
FAILING = [
    'time=2026-11-15T12:16:40.718Z level=ERROR msg="monitor error" system=store db=cairn.db'
    ' replica=file error="write ltx file: mkdir /replica/cairn/ltx: permission denied"'
    " consecutive_errors=1 backoff=1s",
    'time=2026-11-15T12:16:54.706Z level=ERROR msg="l0 retention enforcement failed"'
    ' system=store db=cairn.db path=/data/cairn.db error="fetch l1 files: open'
    ' /replica/cairn/ltx/1: permission denied"',
    'time=2026-11-15T12:17:40.063Z level=ERROR msg="sync error" system=store db=cairn.db'
    ' error="check database behind replica: get replica position: open /replica/cairn/ltx/0:'
    ' permission denied" consecutive_errors=1 backoff=1s',
    'Error: cannot open store: initialize replica client for "/data/cairn.db": failed to create'
    " GCS client (bucket: made-up-bucket): dialing: credentials: could not find default"
    " credentials.",
]
# Lines of a healthy Litestream: start, compaction, snapshot, retention, recovery, shutdown.
HEALTHY = [
    'time=2026-11-15T12:16:24.695Z level=INFO msg=litestream version=v0.5.18 level=""',
    'time=2026-11-15T12:16:24.699Z level=INFO msg="replicating to" type=file sync-interval=1s'
    " path=/replica/cairn",
    'time=2026-11-15T11:00:00.502Z level=INFO msg="compaction complete" system=store'
    " db=cairn.db level=3 txid.min=0000000000000021 txid.max=0000000000000022",
    'time=2026-11-15T10:51:25.908Z level=INFO msg="l0 retention enforced" system=store'
    " db=cairn.db deleted_count=1 max_l1_txid=0000000000000022",
    'time=2026-11-15T12:16:26.715Z level=INFO msg="snapshot complete" system=store db=cairn.db'
    " txid=0000000000000001 size=566",
    'time=2026-11-15T12:18:44.217Z level=INFO msg="sync recovered" system=store db=cairn.db'
    " previous_errors=6",
    'time=2026-11-15T12:18:56.615Z level=INFO msg="signal received, litestream shutting down"'
    " signal=terminated",
    "an error here, in the middle of a line: level=ERROR",
]


def clauses():
    return rehearse.parse_filter(
        rehearse.alert_filter(MONITORING.read_text(encoding="utf-8")), rehearse.STAND_INS
    )


def test_the_alert_filter_read_from_monitoring_tf_is_in_the_form_the_rehearsal_evaluates():
    """Equalities joined by AND, and one regular expression on the message: no more."""
    project = rehearse.STAND_INS["var.project_id"]
    instance = rehearse.STAND_INS["google_compute_instance.cairn.instance_id"]
    assert clauses() == [
        (("logName",), "=", f"projects/{project}/logs/cos_containers"),
        (("resource", "type"), "=", "gce_instance"),
        (("resource", "labels", "instance_id"), "=", instance),
        (("jsonPayload", "cos.googleapis.com/container_name"), "=", "litestream"),
        (("jsonPayload", "message"), "=~", "^(time=[^ ]+ level=ERROR|Error:) "),
    ]


@pytest.mark.parametrize(
    "condition",
    [
        'resource.type = "gce_instance" OR resource.type = "gce_disk"',
        'NOT resource.type = "gce_instance"',
        '-resource.type = "gce_instance"',
        '(resource.type = "gce_instance")',
        'resource.type : "gce"',
        'resource.type != "gce_instance"',
        'severity >= "ERROR"',
        'log_id("cos_containers")',
        "gce_instance",
        "resource.type = gce_instance",
        'jsonPayload.message =~ "a" AND jsonPayload.message =~ "b"',
        'resource.type =~ "gce"',
        'jsonPayload.message =~ "level=ERROR\\s"',
        'jsonPayload.message =~ "(?i)level=error"',
        'jsonPayload.message =~ "level=ERROR(?=x)"',
        'resource.labels.instance_id = "${var.other}"',
        'resource.labels.instance_id = "%{if true}x%{endif}"',
        'resource.labels.instance_id = "$${literal}"',
        'resource.type = "gce_instance" AND',
        'resource.type = "gce_instance" and jsonPayload.message =~ "x"',
        "",
    ],
)
def test_a_condition_outside_that_form_fails_instead_of_passing_unread(condition):
    with pytest.raises(rehearse.Failed):
        rehearse.parse_filter(condition, rehearse.STAND_INS)


def test_an_alert_filter_is_found_only_in_the_replica_policy():
    with pytest.raises(rehearse.Failed):
        rehearse.alert_filter('resource "google_monitoring_alert_policy" "healthz" {}')


def test_equalities_ignore_letter_case_and_the_regular_expression_does_not():
    """Cloud Logging: `=` compares ignoring letter case; `=~` is case sensitive and unanchored
    unless the pattern anchors it."""
    found = rehearse.parse_filter(
        'resource.type = "GCE_Instance" AND jsonPayload.message =~ "level=ERROR"', {}
    )
    assert rehearse.matches(found, rehearse.cos_entry("x level=ERROR y"))
    assert not rehearse.matches(found, rehearse.cos_entry("x level=error y"))
    assert not rehearse.matches(found, {("jsonPayload", "message"): "level=ERROR"})  # no type


def test_what_litestream_prints_on_a_failure_matches_and_a_healthy_line_does_not():
    found = clauses()
    for line in FAILING:
        assert rehearse.matches(found, rehearse.cos_entry(line)), line
    for line in HEALTHY:
        assert not rehearse.matches(found, rehearse.cos_entry(line)), line


def test_another_container_or_another_instance_does_not_match():
    found = clauses()
    assert not rehearse.matches(found, rehearse.cos_entry(FAILING[0], container="cairn"))
    assert not rehearse.matches(found, rehearse.cos_entry(FAILING[0], instance_id="1"))
    assert not rehearse.matches(found, rehearse.cos_entry(FAILING[0], project="other"))


def test_the_verdict_names_the_failing_lines_matched_and_any_healthy_line_matched():
    found = clauses()
    caught, false_alarms = rehearse.alert_verdict(found, HEALTHY, ["no", FAILING[2]])
    assert caught == [FAILING[2]] and false_alarms == []
    caught, false_alarms = rehearse.alert_verdict(found, [FAILING[0], *HEALTHY], HEALTHY)
    assert caught == [] and false_alarms == [FAILING[0]]


def test_replication_has_resumed_on_a_new_level_0_file_and_no_error_since():
    """A real rehearsal showed the "sync recovered" line is not always there: when the refusal
    met only compaction and retention, Litestream had no failed sync to recover from. What
    counts is the replica: a new upload, and no ERROR after it."""
    before = "0000000000000005-0000000000000005.ltx 0000000000000006-0000000000000006.ltx"
    after = "0000000000000006-0000000000000006.ltx 0000000000000007-0000000000000007.ltx"
    assert rehearse.resumed(before, after, HEALTHY[:7]) == ["0000000000000007-0000000000000007.ltx"]
    assert rehearse.resumed(before, after, [HEALTHY[2], FAILING[1]]) == []  # failed again
    assert rehearse.resumed(before, before.split()[0], []) == []  # retention only deleted
    assert rehearse.resumed("", "", []) == []


def test_the_sync_recovered_line_is_read_in_both_of_its_forms():
    assert rehearse.said_recovered([*FAILING[:3], HEALTHY[5]])
    assert rehearse.said_recovered(['time=x level=INFO msg="replica sync recovered"'])
    assert not rehearse.said_recovered(FAILING)
