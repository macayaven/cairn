"""host.sh checks the environment secret's checksum before it writes the env file: Secret
Manager answers with the data and its CRC-32C (`dataCrc32c`), and a mismatch stops cairn's
start with one line. Run from a copy of host.sh against stand-ins (deploy_standins.py): a
curl of its own here answers the metadata server's token and Secret Manager's access call.

The oracle is a CRC-32C written here in Python, independent of the bash one.
"""

import base64
import json
import stat
import subprocess

import pytest
from deploy_standins import BASH, Standins

VM_TOKEN = "fake-vm-access-token"
SECRET = "projects/example-project/secrets/cairn-env"
ENV_TEXT = b"CAIRN_SLACK_KEY=fake-slack-key\n# Fable Works\nCAIRN_X=caf\xc3\xa9 \xff\x00\n"

CURL = r"""#!/bin/bash
S=$STUB_STATE
n=$(cat "$S/count.curl" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" >"$S/count.curl"
printf '%s\n' "$@" >"$S/calls/curl.$n"
cat >"$S/calls/curl.$n.stdin"
for a in "$@"; do
  case "$a" in
    */service-accounts/default/token)
      printf '{"access_token":"%s","expires_in":3599}' "$VM_TOKEN"
      exit 0
      ;;
    https://secretmanager.googleapis.com/*) cat "$S/secret-answer"; exit 0 ;;
  esac
done
exit 6
"""


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def test_the_oracle_is_crc32c():
    assert crc32c(b"123456789") == 0xE3069283


@pytest.fixture
def host(tmp_path):
    standins = Standins(tmp_path)
    curl = standins.bin / "curl"
    curl.write_text(CURL, encoding="utf-8")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
    report = standins.host()
    text = report.script.read_text(encoding="utf-8")
    assert text.count("ENV_DIR=/run/cairn\n") == 1
    report.script.write_text(text.replace("ENV_DIR=/run/cairn\n", 'ENV_DIR="$HOST_ROOT/run"\n'))
    config = report.root / "config.env"
    config.write_text(
        config.read_text(encoding="utf-8") + f'CAIRN_ENV_SECRET="{SECRET}"\n', encoding="utf-8"
    )
    (report.root / "run").mkdir()
    return report


def answer(host, data: bytes, checksum: int | None, *, spaced: bool = False) -> None:
    payload = {"data": base64.b64encode(data).decode("ascii")}
    if checksum is not None:
        payload["dataCrc32c"] = str(checksum)  # int64 in JSON: a string of digits
    body = {"name": f"{SECRET}/versions/3", "payload": payload}
    text = json.dumps(body, indent=2 if spaced else None)
    host.s.set_file("secret-answer", text)


def run(host, function: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(host.script), function],
        env={**host.s.env({}), "HOST_ROOT": str(host.root), "VM_TOKEN": VM_TOKEN},
        cwd=host.root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def env_file(host):
    return host.root / "run" / "cairn.env"


@pytest.mark.parametrize("spaced", [False, True])
def test_a_matching_checksum_writes_the_env_file_byte_for_byte(host, spaced):
    answer(host, ENV_TEXT, crc32c(ENV_TEXT), spaced=spaced)
    done = run(host, "cmd_env")
    assert done.returncode == 0, done.stdout + done.stderr
    assert env_file(host).read_bytes() == ENV_TEXT
    assert "ERROR" not in done.stderr


def test_a_mismatch_stops_the_start_with_one_line_and_writes_nothing(host):
    wrong = crc32c(ENV_TEXT) ^ 1
    answer(host, ENV_TEXT, wrong)
    done = run(host, "cmd_env")
    assert done.returncode == 1
    assert not env_file(host).exists()
    assert not (host.root / "run" / "cairn.env.new").exists()
    errors = [line for line in done.stderr.splitlines() if "ERROR" in line]
    assert errors == [
        f"<3>cairn-host: ERROR: the secret {SECRET}'s data does not match its checksum"
        " (dataCrc32c); not writing the env file. cairn does not start until a version that"
        " matches is read: restart cairn-env.service"
    ]
    # Neither checksum, nor the size, nor any of the data is in what it printed.
    printed = done.stdout + done.stderr
    for value in (str(wrong), str(crc32c(ENV_TEXT)), str(len(ENV_TEXT)), "fake-slack-key"):
        assert value not in printed, value
    # It stopped at once: no retry, no wait.
    assert host.s.calls("sleep") == []


def test_the_checksum_of_a_one_byte_change_does_not_match(host):
    changed = ENV_TEXT.replace(b"Fable", b"Fablf")
    answer(host, changed, crc32c(ENV_TEXT))
    assert run(host, "cmd_env").returncode == 1
    assert not env_file(host).exists()


def test_an_answer_without_a_checksum_is_written_and_says_it_was_not_checked(host):
    answer(host, ENV_TEXT, None)
    done = run(host, "cmd_env")
    assert done.returncode == 0, done.stdout + done.stderr
    assert env_file(host).read_bytes() == ENV_TEXT
    assert f"the answer for {SECRET} carries no checksum (dataCrc32c); not checked" in done.stdout


def test_the_vm_token_never_reaches_a_commands_arguments(host):
    answer(host, ENV_TEXT, crc32c(ENV_TEXT))
    run(host, "cmd_env")
    for argv in host.s.calls("curl"):
        assert not any(VM_TOKEN in arg for arg in argv)
    assert any(VM_TOKEN in text for text in host.s.curl_stdin())


@pytest.mark.parametrize(
    "data", [b"", b"x", bytes(range(256)), b"KEY=value\n" * 400], ids=["empty", "1", "256", "4k"]
)
def test_the_bash_checksum_agrees_with_the_oracle(host, data):
    path = host.root / "data.bin"
    path.write_bytes(data)
    done = subprocess.run(
        [BASH, str(host.script), "crc32c_file", str(path)],
        env={**host.s.env({}), "HOST_ROOT": str(host.root)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str(crc32c(data))
