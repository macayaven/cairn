"""Stand-ins for the commands scripts/deploy_production.sh and infra/gcp/files/host.sh call.

Not a test module: the deploy tests import it. Each stand-in is a few lines of bash that
records its arguments (and, for curl, what it read on stdin) under a state directory and
answers from files the test prepared there. The scripts themselves run unchanged with the
system bash, the stand-ins first on PATH; jq, sha256sum, sed and the rest are the real ones.

    skopeo   a registry kept as files, one per reference: `inspect --raw` prints the bytes,
             `copy` copies them (or fails, or writes other bytes, when the test says so), and
             `inspect` of one named reference can be made to fail.
             Its error messages name the registry path, as skopeo's own do.
    curl     three callers, told apart by URL: the GitHub API (check-head), the VM's guest
             attribute (wait, seq, status) and /healthz (check-url). The Nth call of a kind
             gets the Nth prepared answer, and the last one once they run out. A PUT to the
             guest attribute (host.sh's report) is recorded and answered with success.
    gcloud   `auth print-access-token` prints a fake token.
    sleep    returns at once, so waits and retries cost nothing.
    docker   for host.sh's report, run-cairn and replica-check: `inspect` prints the running
             image, `exec` (the health check) exits with the prepared status, an `exec` that
             writes to /proc/1/fd/1 appends its stdin to the litestream container's log (or
             fails while there is no such container), `network inspect` prints the prepared
             subnets, and `run` is recorded and succeeds, or prints the prepared output of the
             replica check's probe and exits with its status (the Nth `run` the Nth prepared
             output, when the test prepared several). For host.sh's compact, an `exec` of
             `cairn.server compact` prints and exits as prepared (and can run a snippet or
             signal host.sh), and an `exec` that reads `store.compact_due` prints the Nth
             prepared record.
    systemctl
             for host.sh's compact: `stop` and `start` of litestream.service are recorded in
             order, can be made to fail, and make the litestream container absent or there.
    iptables, docker-credential-gcr
             for host.sh's run-cairn: recorded, and succeed (the metadata rules are "there",
             the registry login "works").

Every value in the environment is obviously fake; `run` checks that none of the three the
guide says stay out of logs (project id, account email, registry path) and neither token
is printed, and that no token is ever in a command's arguments.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from support.paths import REPO_ROOT

REPO = REPO_ROOT
DEPLOY_SCRIPT = REPO / "scripts" / "deploy_production.sh"
HOST_SCRIPT = REPO / "infra" / "gcp" / "files" / "host.sh"
README = REPO / "infra" / "gcp" / "README.md"
BASH = "/bin/bash"

PROJECT = "example-project"
ACCOUNT = "deployer@example.test"
REPOSITORY = f"europe-west1-docker.pkg.dev/{PROJECT}/cairn"
IMAGE = f"{REPOSITORY}/cairn"
SOURCE = "ghcr.io/Example-Owner/Cairn"  # capitals, as a GitHub owner may have
GCP_TOKEN = "fake-gcp-access-token"
GITHUB_TOKEN = "fake-github-token"
COMMIT = "1" * 40
NEWER_COMMIT = "2" * 40
PUBLIC_URL = "https://cairn.example.test"
NEVER_PRINTED = (PROJECT, ACCOUNT, REPOSITORY, GCP_TOKEN, GITHUB_TOKEN)
TOKENS = (GCP_TOKEN, GITHUB_TOKEN)

MISSING_TOOLS = [tool for tool in ("jq", "sha256sum") if shutil.which(tool) is None]


def manifest(name: str) -> bytes:
    """The bytes of a made-up image index; its digest is digest(name)."""
    return json.dumps({"schemaVersion": 2, "manifests": [], "name": name}).encode()


def digest(name: str) -> str:
    return "sha256:" + hashlib.sha256(manifest(name)).hexdigest()


NEW = digest("new")
OLD = digest("old")

PRELUDE = r"""#!/bin/bash
set -u
S=$STUB_STATE
n=$(cat "$S/count.$CALL" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" >"$S/count.$CALL"
printf '%s\n' "$@" >"$S/calls/$CALL.$n"
"""

# answer KIND: the Nth answer prepared for KIND, or the last one; nothing prepared is a
# connection that fails.
ANSWER = r"""
answer() {
  local k max f
  k=$(cat "$S/count.answer.$1" 2>/dev/null || echo 0)
  k=$((k + 1))
  echo "$k" >"$S/count.answer.$1"
  max=$(cat "$S/answers/$1.count" 2>/dev/null || echo 0)
  if [ "$max" -eq 0 ]; then
    echo "curl: (7) Failed to connect" >&2
    exit 7
  fi
  [ "$k" -le "$max" ] || k=$max
  f=$S/answers/$1.$k
  cat "$f.out"
  cat "$f.err" >&2
  exit "$(cat "$f.rc")"
}
"""

STUBS = {
    "curl": PRELUDE
    + ANSWER
    + r"""
cat >"$S/calls/curl.$n.stdin"
url=""
for a in "$@"; do
  case "$a" in http*) url=$a ;; esac
done
case "$url" in
  */branches/main) answer github ;;
  *getGuestAttributes*) answer status ;;
  */healthz) answer healthz ;;
  */guest-attributes/*) exit 0 ;;
esac
echo "curl: (6) Could not resolve host" >&2
exit 6
""",
    "skopeo": PRELUDE
    + r"""
key() {
  local ref=${1#docker://}
  printf '%s/registry/%s' "$S" "${ref//\//%}"
}
src=""
dst=""
for a in "$@"; do
  src=$dst
  dst=$a
done
case "$1" in
  inspect)
    if [ "$(cat "$S/skopeo-inspect-fails" 2>/dev/null)" = "${dst#docker://}" ]; then
      echo "level=fatal msg=\"reading manifest in ${dst#docker://}: timeout\"" >&2
      exit 2
    fi
    if [ -e "$(key "$dst")" ]; then
      cat "$(key "$dst")"
      exit 0
    fi
    echo "level=fatal msg=\"reading manifest in ${dst#docker://}: manifest unknown\"" >&2
    exit 1
    ;;
  copy)
    if [ -e "$S/skopeo-copy-fails" ]; then
      echo "level=fatal msg=\"copying to ${dst#docker://}: denied\"" >&2
      exit "$(cat "$S/skopeo-copy-fails")"
    fi
    if [ ! -e "$(key "$src")" ]; then
      echo "level=fatal msg=\"reading ${src#docker://}: manifest unknown\"" >&2
      exit 1
    fi
    if [ -e "$S/skopeo-copy-writes" ]; then
      cp "$S/skopeo-copy-writes" "$(key "$dst")"
    else
      cp "$(key "$src")" "$(key "$dst")"
    fi
    ;;
esac
""",
    "gcloud": PRELUDE
    + r"""
[ "$*" = "auth print-access-token" ] || exit 2
cat "$S/gcloud-token"
""",
    "sleep": PRELUDE,
    "docker": PRELUDE
    + r"""
case "$1" in
  inspect)
    if [ -e "$S/docker-running" ]; then
      cat "$S/docker-running"
      exit 0
    fi
    echo "Error: No such object: cairn" >&2
    exit 1
    ;;
  exec)
    case "$*" in
      *"/proc/1/fd/1"*)
        [ -e "$S/docker-no-litestream" ] && exit 1
        cat >>"$S/litestream-log"
        exit 0
        ;;
      *"cairn.server compact"*)
        echo "compact" >>"$S/events"
        printf '%s\n' "$*" >>"$S/compact-args"
        cat "$S/docker-compact-out" 2>/dev/null
        cat "$S/docker-compact-err" >&2 2>/dev/null
        # shellcheck disable=SC1091
        [ -e "$S/docker-compact-does" ] && . "$S/docker-compact-does"
        if [ -e "$S/docker-compact-signal" ]; then
          kill -s "$(cat "$S/docker-compact-signal")" "$PPID"
        fi
        exit "$(cat "$S/docker-compact-rc" 2>/dev/null || echo 0)"
        ;;
      *"store.compact_due"*)
        k=$(cat "$S/count.record" 2>/dev/null || echo 0)
        k=$((k + 1))
        echo "$k" >"$S/count.record"
        [ -e "$S/docker-record.$k" ] || k=last
        cat "$S/docker-record.$k" 2>/dev/null
        exit 0
        ;;
    esac
    exit "$(cat "$S/docker-healthz-rc" 2>/dev/null || echo 1)"
    ;;
  network)
    cat "$S/docker-network" 2>/dev/null
    exit 0
    ;;
  run)
    k=$(cat "$S/count.run" 2>/dev/null || echo 0)
    k=$((k + 1))
    echo "$k" >"$S/count.run"
    if [ -e "$S/docker-run-out.$k" ]; then
      cat "$S/docker-run-out.$k"
      exit "$(cat "$S/docker-run-rc.$k" 2>/dev/null || echo 0)"
    fi
    [ -e "$S/docker-run-out" ] || exit 0
    cat "$S/docker-run-out"
    exit "$(cat "$S/docker-run-rc")"
    ;;
esac
exit 1
""",
    # host.sh's compact stops and starts litestream.service: the stand-in records the order in
    # `events` and makes the litestream container absent while the unit is stopped.
    "systemctl": PRELUDE
    + r"""
case "$1 $2" in
  "stop litestream.service")
    echo "stop litestream" >>"$S/events"
    [ -e "$S/systemctl-stop-fails" ] && exit 1
    : >"$S/docker-no-litestream"
    ;;
  "start litestream.service")
    echo "start litestream" >>"$S/events"
    [ -e "$S/systemctl-start-fails" ] && exit 1
    rm -f "$S/docker-no-litestream"
    ;;
esac
exit 0
""",
    "iptables": PRELUDE,
    "docker-credential-gcr": PRELUDE,
}


def status_body(line: str) -> str:
    """What getGuestAttributes answers for queryPath=cairn/status."""
    item = {"namespace": "cairn", "key": "status", "value": line}
    return json.dumps({"queryPath": "cairn/status", "queryValue": {"items": [item]}})


def status_line(
    seq: int | str | None,
    running: str = "none",
    healthy: str = "no",
    proven: str = "none",
    pending: str = "none",
    failed: str = "none",
    hold: str = "no",
) -> str:
    """A status line in the shape the guide shows (README, "How the VM reports")."""
    head = "" if seq is None else f"seq={seq} "
    return (
        f"{head}updated=1794350000 running={running} healthy={healthy} proven={proven}"
        f" pending={pending} failed={failed} hold={hold}"
    )


@dataclass
class Result:
    rc: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + self.err


class Standins:
    """A state directory with stand-ins on PATH; `deploy(...)` runs the real script."""

    def __init__(self, root: Path):
        self.root = root
        self.bin = root / "bin"
        self.state = root / "state"
        for path in (self.bin, self.state / "calls", self.state / "answers"):
            path.mkdir(parents=True)
        (self.state / "registry").mkdir()
        (root / "home").mkdir()
        for name, body in STUBS.items():
            path = self.bin / name
            path.write_text(body.replace("$CALL", name), encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        (self.state / "gcloud-token").write_text(GCP_TOKEN, encoding="utf-8")

    # --- what the stand-ins answer ---------------------------------------------------------

    def answers(self, kind: str, *answers: tuple[str, int] | tuple[str, int, str]) -> None:
        """Answers for curl calls of KIND (github, status, healthz): (stdout, rc[, stderr])."""
        for i, answer in enumerate(answers, 1):
            out, rc, err = (*answer, "") if len(answer) == 2 else answer
            (self.state / "answers" / f"{kind}.{i}.out").write_text(out, encoding="utf-8")
            (self.state / "answers" / f"{kind}.{i}.err").write_text(err, encoding="utf-8")
            (self.state / "answers" / f"{kind}.{i}.rc").write_text(str(rc), encoding="utf-8")
        (self.state / "answers" / f"{kind}.count").write_text(str(len(answers)))

    def statuses(self, *lines: str) -> None:
        self.answers("status", *[(status_body(line), 0) for line in lines])

    def put(self, ref: str, content: bytes) -> None:
        """Make the registry hold CONTENT under REF (repository:tag or repository@digest)."""
        (self.state / "registry" / ref.replace("/", "%")).write_bytes(content)

    def holds(self, ref: str) -> bytes | None:
        path = self.state / "registry" / ref.replace("/", "%")
        return path.read_bytes() if path.exists() else None

    def set_file(self, name: str, content: str) -> None:
        (self.state / name).write_text(content, encoding="utf-8")

    # --- what the stand-ins saw ------------------------------------------------------------

    def calls(self, name: str) -> list[list[str]]:
        count = self.state / f"count.{name}"
        n = int(count.read_text()) if count.exists() else 0
        return [
            (self.state / "calls" / f"{name}.{i}").read_text(encoding="utf-8").splitlines()
            for i in range(1, n + 1)
        ]

    def curl_stdin(self) -> list[str]:
        return [
            path.read_text(encoding="utf-8")
            for path in sorted((self.state / "calls").glob("curl.*.stdin"))
        ]

    # --- running ---------------------------------------------------------------------------

    def env(self, overrides: dict[str, str | None]) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(self.root / "home"),
            "LC_ALL": "C",
            "STUB_STATE": str(self.state),
            # Everything the CI job puts in the environment, all fake.
            "CAIRN_ARTIFACT_REPOSITORY": REPOSITORY,
            "CAIRN_SOURCE_IMAGE": SOURCE,
            "CAIRN_COPY_TAG": f"sha-{COMMIT}",
            "GCP_PROJECT_ID": PROJECT,
            "GCP_ZONE": "europe-west1-b",
            "GCP_INSTANCE": "cairn",
            "GCP_ACCESS_TOKEN": GCP_TOKEN,
            "GCP_DEPLOYER_SERVICE_ACCOUNT": ACCOUNT,
            "GCP_WORKLOAD_IDENTITY_PROVIDER": (
                f"projects/{PROJECT}/locations/global/workloadIdentityPools/github/providers/gh"
            ),
            "GITHUB_REPOSITORY": "example-owner/cairn",
            "GITHUB_TOKEN": GITHUB_TOKEN,
            "CAIRN_PUBLIC_URL": PUBLIC_URL,
        }
        for name, value in overrides.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        return env

    def deploy(self, *args: str, **overrides: str | None) -> Result:
        """Run scripts/deploy_production.sh ARGS; check that nothing it printed leaks."""
        done = subprocess.run(
            [BASH, str(DEPLOY_SCRIPT), *args],
            env=self.env(overrides),
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        result = Result(done.returncode, done.stdout, done.stderr)
        for value in NEVER_PRINTED:
            assert value not in result.text, f"the script printed {value!r}:\n{result.text}"
        for name in STUBS:
            for argv in self.calls(name):
                for token in TOKENS:
                    assert not any(token in arg for arg in argv), f"{token!r} in {name}'s argv"
        return result

    # --- host.sh's report, run from a copy ---------------------------------------------------

    def host(self) -> HostReport:
        return HostReport(self)


# The only lines changed in the copy of host.sh, each found exactly once: where it reads its
# settings, where the data disk is, how it tells the disk is mounted, the home the registry
# login writes to, and where cloud-init puts the plans file and the brand. Everything after the
# "main" marker (the command dispatch) is replaced by a call to the function named on the
# command line, so `report_status` and `cmd_run_cairn` run as the units run them, and nothing
# else does.
HOST_EDITS = (
    (". /etc/cairn/config.env\n", '. "$HOST_ROOT/config.env"\n'),
    ("export HOME=/home/cairnhost\n", 'export HOME="$HOST_ROOT/home"\n'),
    ("DATA_MOUNT=/mnt/disks/data\n", 'DATA_MOUNT="$HOST_ROOT/data"\n'),
    ("PLANS_FILE=/etc/cairn/plans.yaml\n", 'PLANS_FILE="$HOST_ROOT/plans.yaml"\n'),
    ("BRAND_DIR=/etc/cairn/brand\n", 'BRAND_DIR="$HOST_ROOT/brand"\n'),
    (
        'is_mounted() { grep -q " $DATA_MOUNT ext4 " /proc/mounts; }',
        'is_mounted() { grep -q " $DATA_MOUNT ext4 " "$HOST_ROOT/mounts"; }',
    ),
)
SUBNET = "172.18.0.0/16"  # what the stand-in docker says the cairn network's subnet is
EXTRA_ARGS = "--sign-in github --join personal"  # CAIRN_EXTRA_ARGS in the copy's config.env
HOST_MAIN_MARKER = "# --- main ---"


def host_copy_text(text: str) -> str:
    for old, new in HOST_EDITS:
        assert text.count(old) == 1, f"host.sh no longer has exactly one {old.strip()!r}"
        text = text.replace(old, new)
    head, marker, _ = text.partition(HOST_MAIN_MARKER)
    assert marker, f"host.sh has no {HOST_MAIN_MARKER!r} line"
    return head + '"$@"\n'


class HostReport:
    """host.sh's state on a stand-in data disk, and its report_status run against it."""

    def __init__(self, standins: Standins):
        self.s = standins
        self.root = standins.root / "host"
        self.data = self.root / "data"
        (self.data / "deploy").mkdir(parents=True)
        self.script = self.root / "host.sh"
        self.script.write_text(host_copy_text(HOST_SCRIPT.read_text(encoding="utf-8")))
        (self.root / "config.env").write_text(
            f'CAIRN_IMAGE="{IMAGE}"\nCAIRN_DEPLOY_TAG="production"\n'
            'CAIRN_REGISTRY_HOST="europe-west1-docker.pkg.dev"\n'
            f'CAIRN_PUBLIC_URL="{PUBLIC_URL}"\nCAIRN_EXTRA_ARGS="{EXTRA_ARGS}"\n'
            'LITESTREAM_REPLICA_URL="gs://example-bucket/cairn"\n'
            'LITESTREAM_IMAGE="litestream/litestream:0.5.18"\n',
            encoding="utf-8",
        )
        self.mounted(True)

    def mounted(self, yes: bool) -> None:
        line = f"/dev/sdb {self.data} ext4 rw,relatime 0 0\n" if yes else ""
        (self.root / "mounts").write_text(line, encoding="utf-8")

    def record(self, name: str, *lines: str) -> None:
        """Write deploy/NAME (image, pending, failed-image, hold, status-seq)."""
        (self.data / "deploy" / name).write_text("".join(f"{x}\n" for x in lines))

    def running(self, digest_: str | None, healthy: bool) -> None:
        running = self.s.state / "docker-running"
        if digest_ is None:
            running.unlink(missing_ok=True)
        else:
            running.write_text(f"{IMAGE}@{digest_}\n", encoding="utf-8")
        self.s.set_file("docker-healthz-rc", "0" if healthy else "1")

    def report(self) -> str:
        """Run report_status; return the line it PUT to the guest attribute cairn/status."""
        before = len(self.s.calls("curl"))
        done = subprocess.run(
            [BASH, str(self.script), "report_status"],
            env={**self.s.env({}), "HOST_ROOT": str(self.root)},
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        puts = [argv for argv in self.s.calls("curl")[before:] if "PUT" in argv]
        assert len(puts) == 1, f"report_status made {len(puts)} PUTs"
        argv = puts[0]
        assert argv[-1].endswith("/instance/guest-attributes/cairn/status"), argv[-1]
        return argv[argv.index("--data") + 1]

    @property
    def plans_file(self) -> Path:
        """Where the copy reads the plans file cloud-init writes (/etc/cairn/plans.yaml)."""
        return self.root / "plans.yaml"

    @property
    def brand_dir(self) -> Path:
        """Where the copy reads the brand cloud-init writes (/etc/cairn/brand)."""
        return self.root / "brand"

    def run_cairn(self, plans: str | None, brand: dict[str, bytes] | None = None) -> list[str]:
        """Run cmd_run_cairn with NEW as the proven image, the plans file holding PLANS (None:
        no file at all) and BRAND's files (name, logo, logo-alt) as cloud-init writes them;
        return the arguments it gave `docker run`."""
        self.record("image", f"{IMAGE}@{NEW}")
        if plans is None:
            self.plans_file.unlink(missing_ok=True)
        else:
            self.plans_file.write_text(plans, encoding="utf-8")
        shutil.rmtree(self.brand_dir, ignore_errors=True)
        for name, content in (brand or {}).items():
            self.brand_dir.mkdir(exist_ok=True)
            (self.brand_dir / name).write_bytes(content)
        self.s.set_file("docker-network", f"{SUBNET} ")
        before = len(self.s.calls("docker"))
        done = subprocess.run(
            [BASH, str(self.script), "cmd_run_cairn"],
            env={**self.s.env({}), "HOST_ROOT": str(self.root)},
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        runs = [argv for argv in self.s.calls("docker")[before:] if argv[:1] == ["run"]]
        assert len(runs) == 1, f"cmd_run_cairn ran docker run {len(runs)} times"
        return runs[0]

    # --- replica-check -------------------------------------------------------------------------

    def bound(self, seconds: int) -> None:
        """REPLICA_BOUND_SECONDS in the copy's config.env, as the template writes it."""
        config = self.root / "config.env"
        kept = [
            line
            for line in config.read_text(encoding="utf-8").splitlines(keepends=True)
            if not line.startswith("REPLICA_BOUND_SECONDS=")
        ]
        config.write_text("".join(kept) + f'REPLICA_BOUND_SECONDS="{seconds}"\n', "utf-8")

    def database(self, there: bool) -> None:
        db = self.data / "cairn" / "cairn.db"
        if there:
            db.parent.mkdir(exist_ok=True)
            db.write_bytes(b"")
        else:
            db.unlink(missing_ok=True)

    def unconfirmed(self) -> str:
        """The change replica-check remembers as not yet copied, or ""."""
        path = self.data / "replica-unconfirmed"
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def litestream_log(self) -> list[str]:
        path = self.s.state / "litestream-log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def docker_runs(self) -> list[list[str]]:
        return [argv for argv in self.s.calls("docker") if argv[:1] == ["run"]]

    def replica_check(
        self,
        newest: int = 0,
        changed: int = 0,
        now: int = 0,
        *,
        started: int = 0,
        said: str | None = None,
        probe_rc: int = 0,
        litestream: bool = True,
    ) -> ReplicaCheck:
        """Run cmd_replica_check with the probe printing NEWEST CHANGED STARTED NOW (or SAID);
        STARTED is the litestream container's start, 0 while it does not run."""
        probe = f"{newest} {changed} {started} {now}\n"
        self.s.set_file("docker-run-out", probe if said is None else said)
        self.s.set_file("docker-run-rc", str(probe_rc))
        absent = self.s.state / "docker-no-litestream"
        if litestream:
            absent.unlink(missing_ok=True)
        else:
            absent.write_text("", encoding="utf-8")
        done = subprocess.run(
            [BASH, str(self.script), "cmd_replica_check"],
            env={**self.s.env({}), "HOST_ROOT": str(self.root)},
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        lines = [x for x in done.stdout.splitlines() if x.startswith(("replica:", "Error:"))]
        assert len(lines) <= 1, done.stdout
        return ReplicaCheck(done.returncode, done.stdout, done.stderr, lines[0] if lines else "")

    # --- compact -------------------------------------------------------------------------------

    def files(self, db: int, wal: int | None) -> None:
        """cairn.db of DB bytes and cairn.db-wal of WAL bytes (None: no log file)."""
        folder = self.data / "cairn"
        folder.mkdir(exist_ok=True)
        (folder / "cairn.db").write_bytes(b"d" * db)
        wal_file = folder / "cairn.db-wal"
        if wal is None:
            wal_file.unlink(missing_ok=True)
        else:
            wal_file.write_bytes(b"w" * wal)

    def events(self) -> list[str]:
        """What the stand-ins saw happen, in order: stop and start of litestream, compact."""
        path = self.s.state / "events"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def compact_args(self) -> list[str]:
        """The arguments of each `docker exec` that ran cairn's compact, in order."""
        path = self.s.state / "compact-args"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def compact(
        self,
        probes: list[str],
        *,
        healthy: bool = True,
        records: tuple[str, ...] = ("2026-11-17T04:27:54+00:00", ""),
        compact_rc: int = 0,
        compact_out: str = "compacted /data/cairn.db: 2891776 -> 1609728 bytes\n",
        compact_does: str = "",
        signal: str | None = None,
        stop_fails: bool = False,
        start_fails: bool = False,
    ) -> Result:
        """Run cmd_compact. PROBES are what the replica probe prints at each `docker run`, in
        order: the first is the check before anything is stopped, the others the checks after
        Litestream is started again (the last one repeats). RECORDS are what the compaction
        record read prints, before and after ("" for no deletion waiting). COMPACT_DOES is bash
        the stand-in compact runs (it sees $HOST_ROOT); SIGNAL is one it sends host.sh."""
        self.s.set_file("docker-healthz-rc", "0" if healthy else "1")
        for i, said in enumerate(probes, 1):
            self.s.set_file(f"docker-run-out.{i}", said)
        self.s.set_file("docker-run-out", probes[-1])
        self.s.set_file("docker-run-rc", "0")
        for i, value in enumerate(records, 1):
            self.s.set_file(f"docker-record.{i}", f"{value}\n")
        self.s.set_file("docker-record.last", f"{records[-1]}\n")
        self.s.set_file("docker-compact-out", compact_out)
        self.s.set_file("docker-compact-rc", str(compact_rc))
        self.s.set_file("docker-compact-does", compact_does)
        flags = {
            "docker-compact-signal": signal,
            "systemctl-stop-fails": "" if stop_fails else None,
            "systemctl-start-fails": "" if start_fails else None,
        }
        for name, value in flags.items():
            if value is None:
                (self.s.state / name).unlink(missing_ok=True)
            else:
                self.s.set_file(name, value)
        done = subprocess.run(
            [BASH, str(self.script), "cmd_compact"],
            env={**self.s.env({}), "HOST_ROOT": str(self.root)},
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return Result(done.returncode, done.stdout, done.stderr)


@dataclass
class ReplicaCheck(Result):
    line: str = ""
