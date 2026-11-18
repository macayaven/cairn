"""Rehearse cairn's shipped stack on one machine: python3 scripts/rehearse_stack.py IMAGE

Runs deploy/compose.yaml and deploy/compose.backup.yaml as they ship (Caddy in front of
cairn, Litestream beside it), with nothing changed but the environment and one override
file that gives Litestream a directory to replicate to instead of a bucket. Then it checks,
from outside, what the unit tests can only check against stand-ins:

- Caddy serves cairn over HTTPS on the configured name, redirects HTTP and sets HSTS, and
  the stack refuses a body over the cap;
- the network cairn trusts as its proxy is the network Compose made;
- a key made in the container works through the proxy, over REST and over MCP;
- cairn sees each client's own address through Caddy: one client's registrations run into
  the per-client limit while another client's still pass, and a forwarding header a client
  sends does not move it to another count;
- Caddy's real log holds no query string, no invitation token and no API key, and neither
  does cairn's;
- the replica alert's condition, read from infra/gcp/monitoring.tf: with the replica refused
  while cairn takes a write, and after a restart of Litestream, it matches a line Litestream
  prints, and the line Litestream prints when it cannot start (gs:// without credentials),
  and none of the lines it printed while healthy; given back, the replica takes writes again;
- a note written before the database is lost is there after Litestream restores it into
  an empty volume and cairn starts on it, and the restore drill passes on that database;
- Caddy's running config holds the 4 MiB body cap (`caddy adapt` in its container);
- `docker compose config` refuses without CAIRN_DOMAIN, without ACME_EMAIL and, with the
  backup file, without litestream.env; without CAIRN_IMAGE, cairn is built from the
  repository root as cairn:local;
- when the restore fails (the replica exists but Litestream may not read it), `up -d`
  reports the failure, cairn is not started, the volume stays without a database and the
  replica keeps every byte; once it is readable again, `up -d` brings the note back;
- deploy/.env written as the guide says, with COMPOSE_FILE, makes a plain
  `docker compose up -d` in deploy/ start the same model and services as the -f flags;
- the guide's "Restore by hand" commands, read from deploy/README.md and run as written over a
  damaged database, keep that file in /data/aside and bring the note back;
- the guide's `docker compose` copies, as written: the key comes out with mode 0600 and the
  caller as owner, and a 0600 plans file put in the volume is readable by cairn, which then
  starts with `--plans` from CAIRN_EXTRA_ARGS;
- the guide's "After a workspace is deleted" commands, read from deploy/README.md and run as
  written after a workspace whose notes hold a made-up word is deleted: right after `compact`
  the log file is empty and the word is in neither file, a note written while litestream is
  stopped is copied once it starts, the replica's newest file is newer than the start, and
  the database restored from the replica into an empty volume is whole, holds the
  rehearsal's notes and the paused one, and not the word.

What it does not cover: a real certificate (the name is `localhost`, so Caddy signs with its
own authority), a bucket (the replica is a directory, so the `gs://` path and its
credentials are not exercised), Caddy's body cap at work (cairn refuses the same size by its
announced length before Caddy has counted it, so the 413 seen here would come without
Caddy's; only Caddy's config is checked), scripts/restore_drill.py --replica (its container
mounts only its own temporary directory and a credentials file, so it cannot reach a replica
that is a directory on a volume; the drill runs here on a copy of the restored database),
Cloud Logging (the alert's condition is evaluated here, on stand-in entries around
Litestream's real lines), and infra/gcp/files/host.sh, which starts the containers itself on
the VM with its own network and updater. Every command keeps the replica file in the model,
also the plain ones from deploy/.env, whose COMPOSE_FILE lists it third, because the guide's
two files alone give Litestream nowhere to write a directory replica; those commands also get
COMPOSE_PROJECT_NAME, so they reach only the rehearsal's own project.

IMAGE must already be local: the script never builds or pulls cairn (Compose pulls Caddy and
Litestream by their pinned digests). It publishes ports 80 and 443 on this machine and writes
deploy/.env, deploy/litestream.env, deploy/laptop.env and deploy/plans.yaml, so it refuses to
start when any of those or deploy/cairn.env exists (an operator's own), and it removes
everything it made at the end, pass or fail. Standard library only. Exit code 0 when every
check passes.
"""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from http.client import HTTPException
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
NAME = "localhost"  # Caddy signs for it with its own authority: no certificate authority is asked
WORD = "quartzlantern"  # a word no fixture uses, so a hit can only be the rehearsal's note
JOIN_TOKEN = "rehearsalInvitationToken0123456789abcdefghijkl"  # shaped like one; it is not one
QUERY_WORD = "rehearsalquerysecret"
REPLICA_URL = "file:///replica/cairn"
README = DEPLOY / "README.md"
# Files the rehearsal writes into deploy/ and removes again. Any of them already there is an
# operator's own, so the rehearsal refuses to start rather than overwrite or delete it.
OPERATOR_FILES = (".env", "cairn.env", "litestream.env", "laptop.env", "plans.yaml")
# What an operator puts in deploy/.env. Left out of the environment of the commands that must
# take them from that file, or fail without them.
SETTINGS = (
    "CAIRN_IMAGE", "CAIRN_DOMAIN", "ACME_EMAIL", "CAIRN_EXTRA_ARGS", "CAIRN_SUBNET",
    "COMPOSE_FILE", "COMPOSE_PROJECT_NAME",
)  # fmt: skip
BODY_CAP = 4 * 1024 * 1024  # Caddyfile: max_size 4MiB
OVERRIDE = """\
# Written by scripts/rehearse_stack.py: Litestream replicates to a directory, not a bucket.
services:
  replica-init:
    image: ${CAIRN_IMAGE}
    restart: "no"
    user: "0:0"
    entrypoint: ["python", "-c", "import os; os.chown('/replica', 10001, 10001)"]
    # The image declares /data a volume: with nothing mounted there, each container of this
    # step would get an unnamed one, which `down` without -v leaves behind.
    tmpfs:
      - /data
    volumes:
      - rehearsal-replica:/replica
  litestream-restore:
    depends_on:
      replica-init:
        condition: service_completed_successfully
    volumes:
      - rehearsal-replica:/replica
  litestream:
    volumes:
      - rehearsal-replica:/replica
volumes:
  rehearsal-replica:
"""


class Failed(Exception):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise Failed(message)


def leaks(text: str, secrets: dict[str, str]) -> list[str]:
    """The names of the secrets whose value appears in `text`."""
    return sorted(name for name, value in secrets.items() if value and value in text)


def logged_uris(caddy_log: str) -> list[str]:
    """The request addresses Caddy's JSON access log holds, in order."""
    found = []
    for line in caddy_log.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        uri = entry.get("request", {}).get("uri") if isinstance(entry, dict) else None
        if isinstance(uri, str):
            found.append(uri)
    return found


def trusted_networks(command: list[str]) -> list[str]:
    """The values of every --trusted-proxy in a container's command line."""
    return [command[i + 1] for i, arg in enumerate(command[:-1]) if arg == "--trusted-proxy"]


def _commands(block: str) -> list[str]:
    """The commands of a shell block: continuation lines joined, blank lines and comments out."""
    commands: list[str] = []
    pending = ""
    for line in block.splitlines():
        stripped = line.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1].rstrip() + " "
            continue
        commands.append(pending + stripped)
        pending = ""
    if pending.strip():
        commands.append(pending.strip())
    return commands


def guide_block(text: str, heading: str) -> list[str]:
    """The commands of the first ```bash block under `heading` (a whole line of the guide)."""
    start = text.index(f"\n{heading}\n")
    block = re.search(r"```bash\n(.*?)```", text[start:], re.S)
    if block is None:
        raise Failed(f"no bash block under {heading!r}")
    return _commands(block.group(1))


def guide_lines(text: str, needle: str) -> list[str]:
    """Every `docker compose` command in `text` that mentions `needle`, inline or in a block,
    in the order the text gives them."""
    found = []
    for match in re.finditer(r"```[^\n]*\n(.*?)```|`([^`\n]+)`", text, re.S):
        block, inline = match.groups()
        commands = _commands(block) if block is not None else [inline]
        found += [c for c in commands if c.startswith("docker compose ") and needle in c]
    return found


def guide_section(text: str, heading: str) -> str:
    """The text from `heading` up to the next heading of the same or a higher level."""
    level = len(heading.split(" ", 1)[0])
    start = text.index(f"\n{heading}\n") + 1
    body = start + len(heading)
    for match in re.finditer(r"^(#+) ", text[body:], re.M):
        if len(match.group(1)) <= level:
            return text[start : body + match.start()]
    return text[start:]


def body_caps(adapted: Any) -> list[int]:
    """The max_size of every request_body handler in Caddy's adapted JSON config."""
    if isinstance(adapted, dict):
        own = [adapted["max_size"]] if adapted.get("handler") == "request_body" else []
        return own + [cap for value in adapted.values() for cap in body_caps(value)]
    if isinstance(adapted, list):
        return [cap for value in adapted for cap in body_caps(value)]
    return []


def ps_states(text: str) -> dict[str, tuple[str, int]]:
    """{service: (state, exit code)} from `docker compose ps --format json`, which prints one
    object per line (Compose 2.21 and later) or one array (earlier)."""
    text = text.strip()
    if not text:
        return {}
    if text.startswith("["):
        entries = json.loads(text)
    else:
        entries = [json.loads(line) for line in text.splitlines()]
    return {
        entry["Service"]: (entry["State"], int(entry.get("ExitCode") or 0)) for entry in entries
    }


# --- the replica alert's condition (infra/gcp/monitoring.tf), evaluated here --------------------
#
# The rehearsal reads the condition out of monitoring.tf and evaluates it on what Litestream
# printed, so the file and the check cannot drift apart. It takes one form only, the one the
# file uses, and refuses anything else instead of passing it unread:
#
#   PATH = "VALUE" AND PATH = "VALUE" AND ... AND jsonPayload.message =~ "PATTERN"
#
# clauses joined by AND alone; `=` on any field, compared ignoring letter case as Cloud
# Logging compares; exactly one `=~`, on jsonPayload.message, a case-sensitive unanchored
# search as in Cloud Logging. A path is names joined by dots, a name with other characters in
# double quotes. No backslash anywhere and no `(?` in the pattern, so RE2 and Python's `re`
# read it the same way. `${...}` is filled from the values given; anything else in HCL's
# template syntax is refused.

MONITORING = REPO / "infra" / "gcp" / "monitoring.tf"
ALERT_POLICY = 'resource "google_monitoring_alert_policy" "replica"'
# What OpenTofu fills in on the VM, made up: the entries below carry the same.
STAND_INS = {
    "var.project_id": "rehearsal-project",
    "google_compute_instance.cairn.instance_id": "4815162342",
}
MESSAGE = ("jsonPayload", "message")
# The lines Litestream 0.5.18 logs when a sync works again after errors (db.go, replica.go).
RECOVERED = ('msg="sync recovered"', 'msg="replica sync recovered"')

Clause = tuple[tuple[str, ...], str, str]

_NAME = r'(?:[A-Za-z_][A-Za-z0-9_]*|"[^"\\]+")'
_CLAUSE = re.compile(rf'({_NAME}(?:\.{_NAME})*)\s*(=~|=)\s*"([^"\\]*)"')


def alert_filter(tf: str) -> str:
    """The filter of the replica alert's log condition, as monitoring.tf writes it (a heredoc)."""
    if ALERT_POLICY not in tf:
        raise Failed(f"no {ALERT_POLICY} in monitoring.tf")
    found = re.search(r"filter\s*=\s*<<-(\w+)\n(.*?)\n\s*\1\n", tf[tf.index(ALERT_POLICY) :], re.S)
    if found is None:
        raise Failed("the replica alert has no filter = <<-EOT ... EOT")
    return found.group(2)


def parse_filter(text: str, values: dict[str, str]) -> list[Clause]:
    """The clauses of a filter in the one form above; Failed for anything else."""
    if "%{" in text or "$${" in text:
        raise Failed("the filter uses HCL template syntax beyond ${...}")

    def fill(match: re.Match[str]) -> str:
        if match.group(1) not in values:
            raise Failed(f"no value for ${{{match.group(1)}}}")
        return values[match.group(1)]

    text = re.sub(r"\$\{([^}]*)\}", fill, text)
    if "\\" in text:
        raise Failed("the filter has a backslash")
    parts, rest = [], text.strip()
    while True:
        found = _CLAUSE.match(rest)
        if found is None:
            raise Failed(f"outside the form at {rest[:60]!r}")
        path = tuple(name.strip('"') for name in re.findall(_NAME, found.group(1)))
        parts.append((path, found.group(2), found.group(3)))
        rest = rest[found.end() :].strip()
        if not rest:
            break
        joined = re.match(r"AND\s+", rest)
        if joined is None:
            raise Failed(f"clauses joined by something other than AND at {rest[:60]!r}")
        rest = rest[joined.end() :]
    searches = [clause for clause in parts if clause[1] == "=~"]
    if len(searches) != 1 or searches[0][0] != MESSAGE:
        raise Failed("the filter needs exactly one =~, on jsonPayload.message")
    if "(?" in searches[0][2]:
        raise Failed("the pattern has a group flag or a lookaround")
    return parts


def matches(clauses: list[Clause], entry: dict[tuple[str, ...], str]) -> bool:
    """Whether a log entry, {path: value}, matches every clause. A field the entry does not have
    matches nothing."""
    for path, op, value in clauses:
        have = entry.get(path)
        if have is None:
            return False
        if op == "=" and have.casefold() != value.casefold():
            return False
        if op == "=~" and re.search(value, have) is None:
            return False
    return True


def cos_entry(
    message: str,
    *,
    container: str = "litestream",
    instance_id: str = STAND_INS["google_compute_instance.cairn.instance_id"],
    project: str = STAND_INS["var.project_id"],
) -> dict[tuple[str, ...], str]:
    """One line of a container as Container-Optimized OS sends it to Cloud Logging: one entry per
    line in the log cos_containers of the VM's instance. Only the message is real here; on the
    VM host.sh names Litestream's container litestream."""
    return {
        ("logName",): f"projects/{project}/logs/cos_containers",
        ("resource", "type"): "gce_instance",
        ("resource", "labels", "instance_id"): instance_id,
        ("jsonPayload", "cos.googleapis.com/container_name"): container,
        MESSAGE: message,
    }


def alert_verdict(
    clauses: list[Clause], healthy: list[str], failing: list[str]
) -> tuple[list[str], list[str]]:
    """(the failing lines the alert matches, the healthy lines it matches): the first must not
    be empty and the second must be."""
    caught = [line for line in failing if matches(clauses, cos_entry(line))]
    false_alarms = [line for line in healthy if matches(clauses, cos_entry(line))]
    return caught, false_alarms


def resumed(before: str, after: str, since: list[str]) -> list[str]:
    """The level-0 files the replica gained between two listings (names split by spaces), if
    Litestream logged no ERROR in the lines printed since the newest of them landed; else []."""
    new = sorted(set(after.split()) - set(before.split()))
    if any(" level=ERROR " in line for line in since):
        return []
    return new


def said_recovered(lines: list[str]) -> bool:
    """Whether Litestream logged that a sync works again. It does so only when a sync had
    failed: an outage that only compaction or retention met ends without this line."""
    return any(said in line for line in lines for said in RECOVERED)


_TLS = ssl.create_default_context()
_TLS.check_hostname = False  # Caddy's own authority signed the certificate for `localhost`
_TLS.verify_mode = ssl.CERT_NONE


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_TLS))


def http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
) -> tuple[int, Any, bytes]:
    """(status, headers, body) of one request; an HTTP error status is returned, not raised."""
    headers = dict(headers or {})
    data = None
    if isinstance(body, dict):
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    elif isinstance(body, bytes):
        data = body
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=20) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def announced(url: str, size: int, headers: dict[str, str] | None = None) -> int:
    """The status a POST that announces `size` bytes is answered with.

    A server that refuses a body by its announced length answers and closes while the client
    is still sending; urllib reports that as a broken pipe and never reads the answer. This
    sends the head, gives the answer two seconds to come, sends the body only if none came,
    stops at the first refused write, and reads the answer either way.
    """
    parts = urllib.parse.urlsplit(url)
    secure = parts.scheme == "https"
    host = parts.hostname or ""
    sock: socket.socket = socket.create_connection(
        (host, parts.port or (443 if secure else 80)), 20
    )
    if secure:
        sock = _TLS.wrap_socket(sock, server_hostname=host)
    try:
        head = [
            f"POST {parts.path or '/'} HTTP/1.1",
            f"Host: {parts.netloc}",
            f"Content-Length: {size}",
            "Content-Type: application/json",
            "Connection: close",
            *(f"{name}: {value}" for name, value in (headers or {}).items()),
        ]
        sock.sendall(("\r\n".join(head) + "\r\n\r\n").encode())
        if not select.select([sock], [], [], 2)[0]:
            left, chunk = size, b"x" * 65536
            try:
                while left > 0:
                    sock.sendall(chunk[:left])
                    left -= len(chunk)
            except OSError:
                pass  # refused part-way: the answer is what says why
        answer = sock.recv(64)
    finally:
        sock.close()
    expect(answer.startswith(b"HTTP/1.1 "), f"no HTTP answer to a {size}-byte body: {answer!r}")
    return int(answer.split()[1])


def run(*args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    result = subprocess.run(args, capture_output=True, text=True, env=env, check=False)
    if check and result.returncode != 0:
        said = (result.stderr or result.stdout).strip()[-600:]
        raise Failed(f"{' '.join(args[:4])} ... exited {result.returncode}: {said}")
    return result.stdout.strip()


def finished(
    args: list[str], *, env: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """The process run to its end, whatever its exit code: for checks that expect a refusal."""
    return subprocess.run(
        args, capture_output=True, text=True, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
        check=False,
    )  # fmt: skip


def tail(result: subprocess.CompletedProcess[str], size: int = 600) -> str:
    return (result.stderr + result.stdout).strip()[-size:]


MOUNTS = '{{index .Config.Labels "com.docker.compose.service"}} {{json .Mounts}}'


def unnamed_volumes(inspected: str, project: str) -> list[str]:
    """The volumes nobody named among the mounts `docker inspect -f MOUNTS` printed, one line
    for each container, as `service at /path`. A volume a compose file names is called
    PROJECT_name; any other name is one Docker made up, or belongs to another project."""
    found = []
    for line in inspected.splitlines():
        service, _, mounts = line.strip().partition(" ")
        for mount in json.loads(mounts or "null") or []:
            named = str(mount.get("Name", "")).startswith(f"{project}_")
            if mount.get("Type") == "volume" and not named:
                found.append(f"{service} at {mount.get('Destination')}")
    return sorted(found)


DIGEST = """\
import hashlib, os
digest, count = hashlib.sha256(), 0
for root, dirs, files in os.walk('/replica'):
    dirs.sort()
    for name in sorted(files):
        path = os.path.join(root, name)
        digest.update(path.encode() + b'\\0')
        with open(path, 'rb') as handle:
            digest.update(handle.read())
        count += 1
print(count, digest.hexdigest()[:16])
"""
LOCK = """\
import os
s = os.stat('/replica/cairn')
print(s.st_uid, s.st_gid, oct(s.st_mode & 0o7777))
os.chown('/replica/cairn', 0, 0)
os.chmod('/replica/cairn', 0o700)
"""
LEVEL0 = """\
import os
path = '/replica/cairn/ltx/0'
print(' '.join(sorted(os.listdir(path))) if os.path.isdir(path) else '')
"""
DAMAGE = """\
import hashlib, os
with open('/data/cairn.db', 'r+b') as handle:
    handle.write(b'damaged by the rehearsal'.ljust(100, b'.'))
with open('/data/cairn.db', 'rb') as handle:
    print(hashlib.sha256(handle.read()).hexdigest(), ','.join(sorted(os.listdir('/data'))))
"""
ASIDE = """\
import hashlib, os
names = sorted(os.listdir('/data/aside'))
with open('/data/aside/cairn.db', 'rb') as handle:
    print(hashlib.sha256(handle.read()).hexdigest(), ','.join(names))
"""
# A workspace whose project name and notes hold DELETED_WORD, made and deleted in cairn's
# container, as an operator's tenant would be; the rehearsal's own notes stay.
DELETED_WORD = "zephyrquill"  # made up: no fixture and no other check uses it
PAUSED_WORD = "umberkite"  # in the note written while litestream is stopped
DELETE_A_WORKSPACE = f"""\
from cairn.store import Store
store = Store('/data/cairn.db')
account = store.create_account('{DELETED_WORD} workspace')
view = store.for_account(account.id)
project = view.create_project('{DELETED_WORD}-project')
for n in range(20):
    text = f'# {DELETED_WORD.title()} {{n}}\\n\\nThe {DELETED_WORD} keeps note {{n}}.\\n'
    view.add_note(project.id, text)
print(store.delete_account(account.id))
store.close()
"""
# The made-up word's count in the raw bytes of the database and of its log, their sizes, and
# whether SQLite finds the database whole; read-only.
TRACES = f"""\
import json, sqlite3
found = {{}}
for name in ('cairn.db', 'cairn.db-wal'):
    try:
        with open('/data/' + name, 'rb') as handle:
            data = handle.read()
    except FileNotFoundError:
        data = b''
    found[name] = [data.count(b'{DELETED_WORD}'), len(data)]
con = sqlite3.connect('file:/data/cairn.db?mode=ro', uri=True)
found['integrity'] = con.execute('PRAGMA integrity_check').fetchone()[0]
print(json.dumps(found))
"""
READS = """\
import os
s = os.stat('/data/plans.yaml')
ok = os.access('/data/plans.yaml', os.R_OK)
print(f"owner {s.st_uid}:{s.st_gid} mode {oct(s.st_mode & 0o777)}, read by uid {os.getuid()}:",
      'readable' if ok else 'unreadable')
"""


class Rehearsal:
    def __init__(self, image: str) -> None:
        self.image = image
        self.project = f"cairn-rehearsal-{os.getpid()}"
        self.tmp = Path(tempfile.mkdtemp(prefix="cairn-rehearsal-"))
        self.override = self.tmp / "compose.rehearsal.yaml"
        self.replica_env = DEPLOY / "litestream.env"
        self.wrote_env = False
        self.dotenv = DEPLOY / ".env"
        self.wrote: list[Path] = []  # other files written into deploy/, removed at the end
        self.unnamed: set[str] = set()  # volumes nobody named, seen on a container before it went
        self.key = ""
        self.base = f"https://{NAME}"
        self.env = {
            **os.environ,
            "CAIRN_IMAGE": image,
            "CAIRN_DOMAIN": NAME,
            "ACME_EMAIL": "ops@example.test",
        }

    # -- compose --------------------------------------------------------------------------------

    def compose(self, *args: str, check: bool = True) -> str:
        files = ["-f", str(DEPLOY / "compose.yaml"), "-f", str(DEPLOY / "compose.backup.yaml")]
        files += ["-f", str(self.override)]
        base = ["docker", "compose", "-p", self.project, "--project-directory", str(DEPLOY)]
        if "down" in args:
            self.look_at_volumes()
        return run(*base, *files, *args, env=self.env, check=check)

    def look_at_volumes(self) -> None:
        """Remember each volume nobody named on the rehearsal's containers. Called before they
        are removed: `down` without -v leaves such a volume on the machine, and from then on
        nothing about it says whose it was."""
        label = f"label=com.docker.compose.project={self.project}"
        containers = run("docker", "ps", "-aq", "--filter", label, check=False).split()
        if containers:
            inspected = run("docker", "inspect", "-f", MOUNTS, *containers, check=False)
            self.unnamed.update(unnamed_volumes(inspected, self.project))

    def compose_finished(self, *args: str) -> subprocess.CompletedProcess[str]:
        files = ["-f", str(DEPLOY / "compose.yaml"), "-f", str(DEPLOY / "compose.backup.yaml")]
        files += ["-f", str(self.override)]
        base = ["docker", "compose", "-p", self.project, "--project-directory", str(DEPLOY)]
        return finished([*base, *files, *args], env=self.env)

    def plain_env(self) -> dict[str, str]:
        """The environment of a plain `docker compose` in deploy/: every setting comes from
        deploy/.env. The project name alone is set here, so that the commands reach the
        rehearsal's own containers and volumes and never a real `cairn` project."""
        env = {name: value for name, value in os.environ.items() if name not in SETTINGS}
        return {**env, "COMPOSE_PROJECT_NAME": self.project}

    def plain(self, *args: str) -> str:
        """`docker compose ARGS` run in deploy/, as the guide runs it."""
        if "down" in args:
            self.look_at_volumes()
        result = finished(["docker", "compose", *args], env=self.plain_env(), cwd=DEPLOY)
        expect(
            result.returncode == 0,
            f"docker compose {' '.join(args[:3])} exited {result.returncode}: {tail(result)}",
        )
        return result.stdout.strip()

    def as_written(self, command: str) -> str:
        """One command copied from the guide, run by bash in deploy/ as the guide runs it."""
        result = finished(["bash", "-c", command], env=self.plain_env(), cwd=DEPLOY)
        expect(result.returncode == 0, f"`{command}` exited {result.returncode}: {tail(result)}")
        return result.stdout.strip()

    def write_dotenv(self, *extra: str) -> None:
        """deploy/.env as the guide has the operator write it: the two required settings, the
        image to run, and the COMPOSE_FILE line, with the rehearsal's replica file listed third
        (the guide's own two files have nowhere to put a directory replica)."""
        lines = [
            f"CAIRN_DOMAIN={NAME}",
            "ACME_EMAIL=ops@example.test",
            f"CAIRN_IMAGE={self.image}",
            f"COMPOSE_FILE=compose.yaml:compose.backup.yaml:{self.override}",
            *extra,
        ]
        self.dotenv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def on_volume(self, volume: str, script: str, *, root: bool = False) -> str:
        """Run Python from the cairn image on one of the rehearsal's volumes, at /data or
        /replica by its name, as cairn's user or as root."""
        mount = "/replica" if volume == "rehearsal-replica" else "/data"
        user = ["--user", "0:0"] if root else []
        return run(
            "docker", "run", "--rm", *user, "-v", f"{self.project}_{volume}:{mount}",
            "--entrypoint", "python", self.image, "-c", script,
        )  # fmt: skip

    def wait_replicated(self) -> str:
        """The number of files in the replica, once there are some and one more sync interval
        has passed, so that the latest note is in it."""
        deadline = time.monotonic() + 90
        replicated = "0"
        while time.monotonic() < deadline and replicated == "0":
            time.sleep(5)
            replicated = self.on_volume(
                "rehearsal-replica",
                "import os; print(sum(len(f) for _, _, f in os.walk('/replica')))",
            )
        expect(replicated != "0", "Litestream replicated nothing in 90 s")
        time.sleep(15)
        return replicated

    def container(self, service: str) -> str:
        found = self.compose("ps", "-q", service)
        expect(bool(found), f"no container for the service {service}")
        return found.splitlines()[0]

    def wait_healthy(self, timeout: float = 180) -> None:
        deadline = time.monotonic() + timeout
        said = ""
        while time.monotonic() < deadline:
            try:
                state = run(
                    "docker", "inspect", "-f",
                    "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
                    self.container("cairn"),
                )  # fmt: skip
                if state == "running healthy":
                    status, _, _ = http("GET", f"{self.base}/healthz")
                    if status == 200:
                        return
                    said = f"cairn is healthy; /healthz through Caddy answered {status}"
                else:
                    said = f"cairn is {state!r}"
            except (Failed, OSError) as error:
                said = str(error)
            time.sleep(2)
        logs = self.compose("logs", "--no-color", "--tail", "30", check=False)
        raise Failed(f"the stack is not serving after {timeout:.0f}s ({said}):\n{logs[-1500:]}")

    def api(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        status, _, raw = http(method, self.base + path, headers={"x-api-key": self.key}, body=body)
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, None

    def hits(self) -> int:
        status, found = self.api("GET", f"/api/search?q={WORD}&project=rehearsal")
        expect(status == 200, f"search answered {status}")
        return len([hit for hit in found["hits"] if WORD in hit["text"]])

    # -- checks ---------------------------------------------------------------------------------

    def starts_the_shipped_stack(self) -> str:
        for name in OPERATOR_FILES:
            expect(
                not (DEPLOY / name).exists(), f"deploy/{name} exists: this is not a scratch copy"
            )
        self.override.write_text(OVERRIDE, encoding="utf-8")
        self.replica_env.write_text(f"LITESTREAM_REPLICA_URL={REPLICA_URL}\n", encoding="utf-8")
        self.wrote_env = True
        self.compose("up", "-d", "--no-build")
        self.wait_healthy()
        return "compose.yaml and compose.backup.yaml as shipped; cairn healthy behind Caddy"

    def serves_https_on_the_name(self) -> str:
        status, headers, _ = http("GET", f"{self.base}/healthz")
        expect(status == 200, f"https /healthz answered {status}")
        hsts = headers.get("Strict-Transport-Security", "")
        expect("max-age=" in hsts, "no Strict-Transport-Security header")
        plain, headers, _ = http("GET", f"http://{NAME}/healthz")
        location = headers.get("Location", "")
        expect(plain in (301, 308) and location.startswith("https://"), f"http answered {plain}")
        large = announced(f"{self.base}/api/projects", 5 << 20, {"x-api-key": "x"})
        expect(large == 413, f"a 5 MiB body answered {large}, not 413")
        small = announced(f"{self.base}/api/projects", 1 << 20, {"x-api-key": "x"})
        expect(small == 401, f"a 1 MiB body with no real key answered {small}, not 401")
        return (
            f"https 200 with HSTS; http answers {plain} to https; the stack answers 413 to a"
            " 5 MiB body (cairn has the same cap as Caddy) and a 1 MiB one reaches the key check"
        )

    def trusts_the_network_compose_made(self) -> str:
        subnet = run(
            "docker", "network", "inspect", "-f",
            "{{range .IPAM.Config}}{{.Subnet}}{{end}}", f"{self.project}_default",
        )  # fmt: skip
        command = json.loads(
            run("docker", "inspect", "-f", "{{json .Args}}", self.container("cairn"))
        )
        trusted = trusted_networks(command)
        expect(trusted == [subnet], f"cairn trusts {trusted}, the network is {subnet}")
        return f"--trusted-proxy {subnet} is the network's own subnet"

    def makes_a_key_and_uses_it_through_the_proxy(self) -> str:
        cairn = self.container("cairn")
        made = "/data/rehearsal.env"
        run("docker", "exec", cairn, "python", "-m", "cairn.server", "key-create",
            "--db", "/data/cairn.db", "--label", "rehearsal", "--out", made)  # fmt: skip
        line = run("docker", "exec", cairn, "cat", made)
        run("docker", "exec", cairn, "rm", made)
        self.key = line.split("=", 1)[1].strip()
        expect(bool(self.key), "the key file held no key")
        status, _ = self.api("POST", "/api/projects", {"name": "rehearsal"})
        expect(status == 201, f"creating a project through Caddy answered {status}")
        note = f"# Harbour lantern\n\nThe {WORD} keeps the harbour tide tables.\n"
        status, noted = self.api("POST", "/api/projects/rehearsal/notes", {"text": note})
        expect(status == 201 and noted["status"] == "saved", f"the note answered {status}")
        expect(self.hits() == 1, "search did not find the note")
        headers = {
            "x-api-key": self.key,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        status, _, raw = http("POST", f"{self.base}/mcp", headers=headers, body=call)
        expect(status == 200, f"MCP through Caddy answered {status}")
        tools = len(json.loads(raw)["result"]["tools"])
        return f"REST: project, note, 1 hit; MCP lists {tools} tools, all over https://{NAME}"

    def sees_each_clients_own_address(self) -> str:
        client = {"client_name": "rehearsal", "redirect_uris": ["http://127.0.0.1:33418/callback"]}
        statuses = []
        for n in range(40):  # each try claims to come from somewhere else
            status, _, _ = http(
                "POST", f"{self.base}/register",
                headers={"X-Forwarded-For": f"198.51.100.{n + 1}"}, body=client,
            )  # fmt: skip
            statuses.append(status)
            if status == 429:
                break
        expect(
            statuses[-1] == 429, f"40 registrations from one client never met a limit: {statuses}"
        )
        passed = statuses.count(201)
        # Another client: a container on the same network, which reaches Caddy from its own
        # address. Were cairn counting Caddy's address for everyone, it would be refused too.
        caddy_ip = run(
            "docker", "inspect", "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", self.container("caddy"),
        )  # fmt: skip
        body = json.dumps(client)
        script = (
            "import socket, ssl\n"
            "ctx = ssl.create_default_context(); ctx.check_hostname = False\n"
            "ctx.verify_mode = ssl.CERT_NONE\n"
            f"sock = ctx.wrap_socket(socket.create_connection(({caddy_ip!r}, 443), 10),"
            f" server_hostname={NAME!r})\n"
            f"body = {body!r}.encode()\n"
            f"sock.sendall(b'POST /register HTTP/1.1\\r\\nHost: {NAME}\\r\\n'"
            " b'Content-Type: application/json\\r\\nConnection: close\\r\\n'"
            " + f'Content-Length: {len(body)}\\r\\n\\r\\n'.encode() + body)\n"
            "print(sock.recv(64).split()[1].decode())\n"
        )
        other = run(
            "docker", "run", "--rm", "--network", f"{self.project}_default",
            "--entrypoint", "python", self.image, "-c", script,
        )  # fmt: skip
        expect(other == "201", f"a second client was answered {other} while the first is limited")
        return f"one client: {passed} registrations then 429, whatever it claimed; another: 201"

    def keeps_secrets_out_of_the_logs(self) -> str:
        http("GET", f"{self.base}/join/{JOIN_TOKEN}?from={QUERY_WORD}")
        http("GET", f"{self.base}/search?q={QUERY_WORD}")
        self.api("GET", f"/api/search?q={QUERY_WORD}")
        time.sleep(1)
        secrets = {
            "the API key": self.key,
            "the invitation token": JOIN_TOKEN,
            "a query": QUERY_WORD,
        }
        caddy = self.compose("logs", "--no-color", "--no-log-prefix", "caddy")
        found = leaks(caddy, secrets)
        expect(not found, f"Caddy's log holds {', '.join(found)}")
        uris = logged_uris(caddy)
        expect("/join/redacted?redacted" in uris, f"no redacted invitation line in {uris[-6:]}")
        cairn = self.compose("logs", "--no-color", "--no-log-prefix", "cairn")
        found = leaks(cairn, secrets)
        expect(not found, f"cairn's log holds {', '.join(found)}")
        return f"{len(uris)} request lines in Caddy's log; no key, token or query in either log"

    def litestream_lines(self) -> list[str]:
        """Every line the litestream service's container has printed, oldest first."""
        logs = self.compose("logs", "--no-color", "--no-log-prefix", "litestream", check=False)
        return [line for line in logs.splitlines() if line.strip()]

    def write_a_note(self, text: str) -> None:
        status, _ = self.api("POST", "/api/projects/rehearsal/notes", {"text": text})
        expect(status == 201, f"a note written during the replica check answered {status}")

    def wait_for_lines(
        self, after: int, done: Callable[[list[str]], bool], seconds: float
    ) -> list[str]:
        """Litestream's lines from line `after` on, once `done` holds for them or time is up."""
        deadline = time.monotonic() + seconds
        while True:
            lines = self.litestream_lines()[after:]
            if done(lines) or time.monotonic() > deadline:
                return lines
            time.sleep(3)

    def alerts_when_the_replica_refuses_writes(self) -> str:
        """The replica alert's condition, read from infra/gcp/monitoring.tf, matches what
        Litestream prints when its replica refuses it, and nothing it printed while healthy.
        The stand-in bucket is a directory; refusing it is giving it to root, mode 0700, while
        Litestream runs as uid 10001, as LOCK does for the failed restore. Then it is given
        back, and replication has to resume."""
        clauses = parse_filter(alert_filter(MONITORING.read_text(encoding="utf-8")), STAND_INS)
        # Healthy: everything since the stack started, and a restart (a shutdown and a start).
        self.write_a_note("# Before the refusal\n\nA healthy upload.\n")
        self.wait_replicated()
        self.compose("restart", "litestream")
        time.sleep(15)
        healthy = self.litestream_lines()

        def alarmed(lines: list[str]) -> bool:
            return any(matches(clauses, cos_entry(line)) for line in lines)

        # Litestream cannot start at all: a gs:// replica and no Google credentials. On the VM
        # this is a token that cannot be had; it prints one line without a level and exits.
        cannot_start = self.litestream_without_credentials()
        owner = self.on_volume("rehearsal-replica", LOCK, root=True).split()
        try:
            self.write_a_note("# During the refusal\n\ncairn takes this write.\n")
            running = self.wait_for_lines(len(healthy), alarmed, 90)
            # The replica cannot be reached at start: Litestream restarted while it is refused.
            restarted_at = len(healthy) + len(running)
            self.compose("restart", "litestream")
            self.write_a_note("# Still refused\n\nAnother write.\n")
            at_start = self.wait_for_lines(restarted_at, alarmed, 90)
        finally:
            uid, gid, mode = owner
            unlock = "import os; path = '/replica/cairn'"
            unlock += f"; os.chown(path, {uid}, {gid}); os.chmod(path, {mode})"
            self.on_volume("rehearsal-replica", unlock, root=True)
        refused = running + at_start
        caught, false_alarms = alert_verdict(clauses, healthy, refused + cannot_start)
        for group, lines in (("refused", refused), ("cannot start", cannot_start)):
            for line in lines:
                mark = "matched" if line in caught else "not matched"
                print(f"  {group}, {mark}: {line[:400]}", flush=True)
        expect(not false_alarms, f"the alert matches healthy lines: {false_alarms[:3]}")
        expect(alarmed(running), f"no matched line while running: {running[-5:]}")
        expect(alarmed(at_start), f"no matched line after the restart: {at_start[-5:]}")
        expect(alarmed(cannot_start), f"no matched line when it cannot start: {cannot_start}")
        # Given back: a new write reaches the replica as a new level-0 file (Litestream's
        # backoff tops out at 5 minutes), and no ERROR follows for 20 seconds.
        given_back = restarted_at + len(at_start)
        before = self.on_volume("rehearsal-replica", LEVEL0, root=True)
        self.write_a_note("# After the refusal\n\nReplication resumes.\n")
        deadline, after = time.monotonic() + 330, before
        while not set(after.split()) - set(before.split()) and time.monotonic() < deadline:
            time.sleep(5)
            after = self.on_volume("rehearsal-replica", LEVEL0, root=True)
        expect(resumed(before, after, []) != [], "no new level-0 file in the replica in 330 s")
        landed = len(self.litestream_lines())
        time.sleep(20)
        since = self.litestream_lines()[landed:]
        new = resumed(before, after, since)
        expect(bool(new), f"replication did not resume: {since[-5:]}")
        expect(self.hits() == 1, "the rehearsal's note is not found once")
        healed = said_recovered(self.litestream_lines()[given_back:])
        named = [re.search(r'msg="([^"]+)"', line) for line in caught]
        messages = sorted({found.group(1) for found in named if found})
        return (
            f"{len(healthy)} healthy lines, none matched; with the replica refused Litestream"
            f" printed {len(refused)} lines, {sum(1 for line in refused if line in caught)}"
            f" matched ({', '.join(messages)}),"
            f" {sum(1 for line in at_start if line in caught)} of them after a restart; without"
            f" credentials it printed {len(cannot_start)} line(s), exited"
            f" {'and matched' if alarmed(cannot_start) else ''}; given back, the replica took"
            f" {len(new)} new level-0 file(s) and no ERROR followed for 20 s"
            f" ({'with' if healed else 'without'} a 'sync recovered' line)"
        )

    def litestream_without_credentials(self) -> list[str]:
        """What the litestream service prints when its replica is gs:// and no credentials
        exist: a one-off container of the service with only the replica's address changed."""
        name = f"{self.project}-no-credentials"
        args = ["run", "--rm", "--no-deps", "-T", "--name", name]
        args += ["-e", "LITESTREAM_REPLICA_URL=gs://cairn-rehearsal-made-up/cairn", "litestream"]
        files = ["-f", str(DEPLOY / "compose.yaml"), "-f", str(DEPLOY / "compose.backup.yaml")]
        files += ["-f", str(self.override)]
        base = ["docker", "compose", "-p", self.project, "--project-directory", str(DEPLOY)]
        try:
            result = subprocess.run(
                [*base, *files, *args], capture_output=True, text=True, env=self.env,
                stdin=subprocess.DEVNULL, timeout=120, check=False,
            )  # fmt: skip
        except subprocess.TimeoutExpired as error:
            raise Failed("litestream without credentials did not exit in 120 s") from error
        finally:
            self.look_at_volumes()
            run("docker", "rm", "-f", name, check=False)
        expect(result.returncode != 0, f"litestream without credentials exited 0: {tail(result)}")
        return [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]

    def restores_a_lost_database(self) -> str:
        deadline = time.monotonic() + 90
        replicated = "0"
        while time.monotonic() < deadline and replicated == "0":
            time.sleep(5)
            replicated = run(
                "docker", "run", "--rm", "-v", f"{self.project}_rehearsal-replica:/replica:ro",
                "--entrypoint", "python", self.image, "-c",
                "import os; print(sum(len(f) for _, _, f in os.walk('/replica')))",
            )  # fmt: skip
        expect(replicated != "0", "Litestream replicated nothing in 90 s")
        time.sleep(15)  # one more sync interval, so the note is in the replica
        self.compose("down")  # the containers go; the volumes stay
        run("docker", "volume", "rm", f"{self.project}_cairn-data")  # the database is lost
        self.compose("up", "-d", "--no-build")
        self.wait_healthy()
        expect(self.hits() == 1, "the note is not in the restored database")
        cairn = self.container("cairn")
        copy = (
            "import sqlite3\n"
            "src = sqlite3.connect('file:/data/cairn.db?mode=ro', uri=True)\n"
            "dst = sqlite3.connect('/data/drill.db')\n"
            "src.backup(dst); dst.close(); src.close()\n"
        )
        run("docker", "exec", cairn, "python", "-c", copy)
        run("docker", "cp", f"{cairn}:/data/drill.db", str(self.tmp / "drill.db"))
        run("docker", "exec", cairn, "rm", "/data/drill.db")
        drill = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "restore_drill.py"), "--db-file",
             str(self.tmp / "drill.db"), "--min-notes", "1"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        expect(drill.returncode == 0, f"the restore drill failed:\n{drill.stdout[-600:]}")
        return (
            f"{replicated} replica files; volume removed, restored by litestream-restore,"
            " cairn started on it, the key and the note are there, the drill passes"
        )

    def caddy_holds_the_body_cap(self) -> str:
        adapted = run(
            "docker", "exec", self.container("caddy"),
            "caddy", "adapt", "--config", "/etc/caddy/Caddyfile",
        )  # fmt: skip
        caps = body_caps(json.loads(adapted))
        expect(caps == [BODY_CAP], f"request_body max_size in Caddy's config: {caps}")
        return (
            f"`caddy adapt` in the running Caddy: one request_body, max_size {caps[0]} (4 MiB);"
            " configured, not exercised (cairn refuses the same size first)"
        )

    def refuses_missing_settings(self) -> str:
        expect(not self.dotenv.exists(), "deploy/.env exists, so the settings would come from it")
        env = {name: value for name, value in os.environ.items() if name not in SETTINGS}
        env |= {"CAIRN_DOMAIN": NAME, "ACME_EMAIL": "ops@example.test"}
        alone = ["docker", "compose", "--project-directory", str(DEPLOY)]
        alone += ["-f", str(DEPLOY / "compose.yaml")]
        refused = []
        for missing in ("CAIRN_DOMAIN", "ACME_EMAIL"):
            without = {name: value for name, value in env.items() if name != missing}
            result = finished([*alone, "config", "--quiet"], env=without)
            expect(
                result.returncode != 0 and missing in result.stderr,
                f"config without {missing} exited {result.returncode}: {tail(result)}",
            )
            refused.append(missing)
        # Without litestream.env: the shipped files copied to a directory that lacks it, so the
        # rehearsal's own litestream.env is not moved while its stack runs.
        copy = self.tmp / "deploy-copy"
        copy.mkdir(exist_ok=True)
        for name in ("compose.yaml", "compose.backup.yaml", "Caddyfile", "litestream.yml"):
            shutil.copy2(DEPLOY / name, copy / name)
        merged = ["docker", "compose", "--project-directory", str(copy)]
        merged += ["-f", str(copy / "compose.yaml"), "-f", str(copy / "compose.backup.yaml")]
        result = finished([*merged, "config", "--quiet"], env=env)
        expect(
            result.returncode != 0 and "litestream.env" in result.stderr,
            f"config with the backup file and no litestream.env exited {result.returncode}:"
            f" {tail(result)}",
        )
        (copy / "litestream.env").write_text(f"LITESTREAM_REPLICA_URL={REPLICA_URL}\n")
        result = finished([*merged, "config", "--quiet"], env=env)
        expect(result.returncode == 0, f"config with litestream.env failed: {tail(result)}")
        # No CAIRN_IMAGE: cairn (and data-init, from the same image) is built from the top.
        model = json.loads(
            run(*alone, "-f", str(DEPLOY / "compose.backup.yaml"), "config", "--format", "json",
                env=env)
        )  # fmt: skip
        for service in ("cairn", "data-init"):
            found = model["services"][service]
            context = Path(found.get("build", {}).get("context", "")).resolve()
            expect(
                found.get("image") == "cairn:local" and context == REPO,
                f"{service} without CAIRN_IMAGE: image {found.get('image')}, build from {context}",
            )
        return (
            f"config refuses without {' or '.join(refused)} (naming it) and, with the backup file,"
            " without litestream.env (and accepts it with one); with no CAIRN_IMAGE, cairn is"
            " cairn:local built from the repository root"
        )

    def refuses_to_start_cairn_after_a_failed_restore(self) -> str:
        """The restore fails because the replica exists but Litestream may not read it: the
        directory is given to root, mode 0700, while Litestream runs as uid 10001. That is how
        a bucket looks to credentials that lost read access, and a restore that cannot read the
        replica it was told exists must fail, not report that there is nothing to restore."""
        replicated = self.wait_replicated()
        self.compose("down")  # nothing writes to the replica from here on
        before = self.on_volume("rehearsal-replica", DIGEST, root=True)
        run("docker", "volume", "rm", f"{self.project}_cairn-data")  # the database is lost
        owner = self.on_volume("rehearsal-replica", LOCK, root=True).split()
        problems: list[str] = []
        try:
            up = self.compose_finished("up", "-d", "--no-build")
            time.sleep(10)  # time for anything Compose might still start
            states = ps_states(self.compose("ps", "-a", "--format", "json"))
            restore_log = self.compose(
                "logs", "--no-color", "--no-log-prefix", "--tail", "5", "litestream-restore",
                check=False,
            )  # fmt: skip
            database = self.on_volume(
                "cairn-data", "import os; print(os.path.exists('/data/cairn.db'))"
            )
        finally:
            # Whatever happened, nothing may write to the replica once it is readable again.
            self.compose("stop", check=False)
            uid, gid, mode = owner
            unlock = "import os; path = '/replica/cairn'"
            unlock += f"; os.chown(path, {uid}, {gid}); os.chmod(path, {mode})"
            self.on_volume("rehearsal-replica", unlock, root=True)
        after = self.on_volume("rehearsal-replica", DIGEST, root=True)
        restore = states.get("litestream-restore", ("absent", 0))
        cairn = states.get("cairn", ("absent", 0))[0]
        caddy = states.get("caddy", ("absent", 0))[0]
        said = tail(up, 300).splitlines()[-1:] or ["(nothing)"]
        if up.returncode == 0:
            problems.append("`up -d` exited 0")
        if restore[0] != "exited" or restore[1] == 0:
            problems.append(f"litestream-restore is {restore[0]}, exit code {restore[1]}")
        if cairn == "running":
            problems.append("cairn is running")
        if database != "False":
            problems.append("the volume has a cairn.db")
        if before != after:
            problems.append(f"the replica changed: {before} files/digest before, {after} after")
        seen = (
            f"`up -d` exited {up.returncode} ({said[0].strip()}); litestream-restore {restore[0]}"
            f" with code {restore[1]} ({' | '.join(restore_log.splitlines()[-2:])}); cairn"
            f" {cairn}, caddy {caddy}; cairn.db on the volume: {database}; replica (files,"
            f" digest) {before} before, {after} after"
        )
        # Bring the stack back for the later checks, unless cairn ran on an empty database:
        # then the stack stays down, so that nothing replicates that database over the note.
        if cairn != "running" and database == "False":
            self.compose("up", "-d", "--no-build")
            self.wait_healthy()
            if self.hits() != 1:
                problems.append("readable again, the replica did not bring the note back")
        if problems:
            states_line = ", ".join(f"{s}={v[0]}/{v[1]}" for s, v in sorted(states.items()))
            raise Failed(f"{'; '.join(problems)}. Seen: {seen}. States: {states_line}")
        return (
            f"replica of {replicated} files given to root 0700, unreadable to Litestream: {seen};"
            " readable again, `up -d` restored it and the note is there"
        )

    def starts_from_the_env_file_the_guide_describes(self) -> str:
        expect(not self.dotenv.exists(), "deploy/.env exists and is not the rehearsal's")
        with_flags = self.compose("config", "--hash", "*")
        running = sorted(self.compose("ps", "--services", "--status", "running").split())
        self.write_dotenv()
        self.wrote.append(self.dotenv)
        from_file = self.plain("config", "--hash", "*")
        expect(
            sorted(from_file.splitlines()) == sorted(with_flags.splitlines()),
            f"the model from deploy/.env differs from -f:\n{from_file}\n--\n{with_flags}",
        )
        self.compose("down")
        self.plain("up", "-d")
        self.wait_healthy()
        started = sorted(self.plain("ps", "--services", "--status", "running").split())
        expect(started == running, f"from deploy/.env: {started}; with -f flags: {running}")
        expect("litestream" in started, f"no litestream among {started}")
        expect(self.hits() == 1, "the note is not there after starting from deploy/.env")
        return (
            "deploy/.env with CAIRN_DOMAIN, ACME_EMAIL, CAIRN_IMAGE and COMPOSE_FILE (the guide's"
            " two files, then the rehearsal's replica file); plain `docker compose up -d` in"
            f" deploy/ started {', '.join(started)}: the same services as with -f, the same"
            f" config hash for all {len(from_file.splitlines())} services, and the note is there"
        )

    def restores_by_hand_as_the_guide_says(self) -> str:
        expect(self.dotenv in self.wrote, "needs the deploy/.env the previous check writes")
        commands = guide_block(README.read_text(encoding="utf-8"), "### Restore by hand")
        self.plain("stop")  # cairn lets go of the database before it is damaged
        damaged, held = self.on_volume("cairn-data", DAMAGE).split(" ", 1)
        listed = [self.as_written(command) for command in commands]
        # The move ends with `ls -la /data`: what it left beside aside/.
        left = [
            line.split()[-1]
            for line in "\n".join(listed).splitlines()
            if line.startswith(("-", "d")) and line.split()[-1] not in (".", "..", "aside")
        ]
        self.wait_healthy()
        expect(self.hits() == 1, "the note is not there after the restore by hand")
        kept, names = self.on_volume("cairn-data", ASIDE).split(" ", 1)
        expect(kept == damaged, "/data/aside/cairn.db is not the damaged file")
        return (
            f"{len(commands)} commands from the guide as written, over a damaged cairn.db (/data"
            f" held {held}): it is in /data/aside ({names}) byte for byte, left in /data:"
            f" {left or 'nothing'}; the replica was restored, the note is there"
        )

    def copies_files_in_and_out_as_the_guide_says(self) -> str:
        expect(self.dotenv in self.wrote, "needs the deploy/.env an earlier check writes")
        guide = README.read_text(encoding="utf-8")
        laptop, plans = DEPLOY / "laptop.env", DEPLOY / "plans.yaml"
        key_out = guide_lines(guide, "laptop.env")
        expect(len(key_out) == 3, f"the guide's key commands are {key_out}")
        self.wrote += [laptop, plans]
        try:
            for command in key_out:
                self.as_written(command)
            copied = laptop.stat()
            held = laptop.read_text(encoding="utf-8").partition("=")[2].strip()
        finally:
            laptop.unlink(missing_ok=True)  # it holds a working key
        mode = copied.st_mode & 0o777
        expect(mode == 0o600, f"./laptop.env arrived with mode {oct(mode)}")
        expect(copied.st_uid == os.getuid(), f"./laptop.env is owned by uid {copied.st_uid}")
        expect(bool(held), "./laptop.env holds no key")
        plans_in = guide_lines(guide_section(guide, "## Plans"), "/data/plans.yaml")
        expect(bool(plans_in), "the guide's Plans section has no command that copies the file in")
        shutil.copyfile(DEPLOY / "plans.example.yaml", plans)
        plans.chmod(0o600)
        try:
            self.as_written(plans_in[0])
            reads = self.plain("exec", "-T", "cairn", "python", "-c", READS)
            expect(
                reads.endswith(" readable"),
                f"after `{plans_in[0]}` from a 0600 file, /data/plans.yaml has {reads}",
            )
            self.write_dotenv("CAIRN_EXTRA_ARGS=--plans /data/plans.yaml")
            try:
                self.plain("up", "-d")
                self.wait_healthy()
                args = json.loads(
                    run("docker", "inspect", "-f", "{{json .Args}}", self.container("cairn"))
                )
                at = args.index("--plans") if "--plans" in args else -1
                expect(
                    at >= 0 and args[at + 1 : at + 2] == ["/data/plans.yaml"],
                    f"cairn runs with {args}",
                )
            finally:
                self.write_dotenv()
                self.plain("up", "-d")
                self.wait_healthy()
        finally:
            plans.unlink(missing_ok=True)
        return (
            f"`{key_out[1]}`: ./laptop.env mode 0600, owned by uid {copied.st_uid} (the caller);"
            f" `{plans_in[0]}` from a 0600 file: {reads}; `serve --plans /data/plans.yaml` from"
            " CAIRN_EXTRA_ARGS came up healthy"
        )

    def traces(self) -> dict[str, Any]:
        """TRACES, read in cairn's container."""
        return json.loads(self.plain("exec", "-T", "cairn", "python", "-c", TRACES))

    def compacts_with_the_replica_paused(self) -> str:
        """The guide's "After a workspace is deleted", its four commands as written, after a
        workspace whose notes hold DELETED_WORD is deleted: right after `compact` the log file
        is empty and the word is in neither file; a note written while litestream is stopped
        is copied once it starts; the replica's newest file is newer than the start; and a
        restore from the replica (the volume removed, as restores_a_lost_database does) holds
        the rehearsal's notes and the paused one, and not the word."""
        expect(self.dotenv in self.wrote, "needs the deploy/.env an earlier check writes")
        guide = README.read_text(encoding="utf-8")
        commands = guide_block(guide, "### After a workspace is deleted")
        expect(len(commands) == 4, f"the guide's commands are {commands}")
        deleted = self.plain("exec", "-T", "cairn", "python", "-c", DELETE_A_WORKSPACE)
        expect(deleted.splitlines()[-1:] == ["True"], f"deleting the workspace said {deleted!r}")
        before = self.traces()
        expect(before["cairn.db-wal"][0] > 0, f"the deleted word is not in the log: {before}")
        took = []  # each of the first three commands, timed alone

        def timed(command: str) -> str:
            began = time.monotonic()
            said = self.as_written(command)
            took.append(time.monotonic() - began)
            return said

        timed(commands[0])
        compacted = timed(commands[1])
        right_after = self.traces()  # between the commands, litestream still stopped
        status, _ = self.api(
            "POST", "/api/projects/rehearsal/notes",
            {"text": f"# While paused\n\nThe {PAUSED_WORD} arrives while litestream is stopped.\n"},
        )  # fmt: skip
        expect(status == 201, f"a note written while litestream is stopped answered {status}")
        start = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        timed(commands[2])
        newest = ""
        for _ in range(4):  # the guide: an earlier time means not copied yet; run it again
            said = self.as_written(commands[3]).split()
            newest = said[-1] if said else ""
            if newest[:19] >= start:
                break
        expect(newest[:19] >= start, f"the replica's newest file is {newest!r}, before {start}")
        expect(
            right_after["cairn.db-wal"] == [0, 0] and right_after["cairn.db"][0] == 0,
            f"right after compact: {right_after} (before: {before}); compact said {compacted!r}",
        )
        # The lost database: the volume goes, litestream-restore brings it back from the replica.
        self.plain("down")
        run("docker", "volume", "rm", f"{self.project}_cairn-data")
        self.plain("up", "-d")
        self.wait_healthy()
        restored = self.traces()
        status, found = self.api("GET", f"/api/search?q={PAUSED_WORD}&project=rehearsal")
        expect(status == 200, f"search answered {status}")
        paused_hits = len([hit for hit in found["hits"] if PAUSED_WORD in hit["text"]])
        expect(self.hits() == 1, "the rehearsal's note is not in the restored database")
        expect(paused_hits == 1, "the note written while litestream was stopped is not there")
        expect(restored["integrity"] == "ok", f"the restored database: {restored}")
        expect(
            restored["cairn.db"][0] == 0 and restored["cairn.db-wal"][0] == 0,
            f"the restored database holds the deleted word: {restored}",
        )
        return (
            f"{len(commands)} commands from the guide as written; the deleted word was in the log"
            f" {before['cairn.db-wal'][0]} times and the database {before['cairn.db'][0]} times"
            f" (log {before['cairn.db-wal'][1]} bytes); right after compact: log 0 bytes, word 0"
            f" in both; stop, compact and start took {sum(took):.1f}s"
            f" ({', '.join(f'{s:.1f}' for s in took)}); the replica's newest file is from"
            f" {newest}, after the start at {start}Z; restored from the replica: integrity ok, the"
            " rehearsal's note and the paused one there, the deleted word in neither file"
        )

    def cleanup(self) -> str:
        if self.override.exists():
            self.compose("down", "-v", "--remove-orphans", check=False)
        for path in self.wrote:
            path.unlink(missing_ok=True)
        if self.wrote_env:
            self.replica_env.unlink(missing_ok=True)
        shutil.rmtree(self.tmp, ignore_errors=True)
        left = run("docker", "ps", "-aq", "--filter", f"name={self.project}", check=False)
        volumes = run(
            "docker", "volume", "ls", "-q", "--filter", f"name={self.project}", check=False
        )
        expect(not left and not volumes, "rehearsal containers or volumes remain")
        expect(not self.replica_env.exists(), "deploy/litestream.env remains")
        left = [path.name for path in self.wrote if path.exists()]
        expect(not left, f"deploy/{', deploy/'.join(left)} remains")
        held = ", ".join(sorted(self.unnamed))
        expect(not held, f"a container held a volume nobody named: {held}")
        return (
            "no container, volume, file in deploy/ or temporary directory left; every volume a"
            " container held had a name"
        )


def run_checks(checks: list[Callable[[], str]]) -> int:
    """Run the checks in order, print a line for each, and return how many passed.

    The first check starts the stack: when it fails nothing else can run. Any later failure is
    reported and the rest still run, so one rehearsal shows everything that is wrong.
    """
    passed = 0
    for position, check in enumerate(checks):
        started = time.monotonic()
        try:
            detail = check()
        except (Failed, OSError, HTTPException, ValueError, KeyError, TypeError) as failure:
            print(f"FAIL {check.__name__}: {failure}", flush=True)
            if position == 0:
                break
            continue
        print(f"PASS {check.__name__} ({time.monotonic() - started:.1f}s): {detail}", flush=True)
        passed += 1
    return passed


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1].startswith("-"):
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2
    rehearsal = Rehearsal(argv[1])
    checks: list[Callable[[], str]] = [
        rehearsal.starts_the_shipped_stack,
        rehearsal.serves_https_on_the_name,
        rehearsal.trusts_the_network_compose_made,
        rehearsal.makes_a_key_and_uses_it_through_the_proxy,
        rehearsal.sees_each_clients_own_address,
        rehearsal.keeps_secrets_out_of_the_logs,
        rehearsal.caddy_holds_the_body_cap,
        rehearsal.refuses_missing_settings,
        rehearsal.alerts_when_the_replica_refuses_writes,
        rehearsal.restores_a_lost_database,
        rehearsal.refuses_to_start_cairn_after_a_failed_restore,
        rehearsal.starts_from_the_env_file_the_guide_describes,
        rehearsal.restores_by_hand_as_the_guide_says,
        rehearsal.copies_files_in_and_out_as_the_guide_says,
        rehearsal.compacts_with_the_replica_paused,
    ]
    passed = 0
    try:
        passed = run_checks(checks)
    finally:
        try:
            print(f"PASS cleanup: {rehearsal.cleanup()}")
        except Failed as failure:
            print(f"FAIL cleanup: {failure}")
            passed = -1
    print(f"{max(passed, 0)}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
