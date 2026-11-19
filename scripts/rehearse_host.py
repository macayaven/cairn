"""Rehearse the VM's host setup on a throwaway Linux machine:
sudo python3 scripts/rehearse_host.py IMAGE --on-this-throwaway-machine

Renders infra/gcp/cloud-init.yaml.tftpl with rehearsal values, installs exactly the files its
write_files lists (same paths, same permissions), runs `systemctl daemon-reload`, and then
drives the real units and infra/gcp/files/host.sh with real Docker, real systemd, real
iptables and a real ext4 file system. It checks:

- a blank data disk is formatted ext4 and mounted with host.sh's options;
- a disk that is not provably blank (bytes without a file system, or another file system) is
  refused and left byte for byte as it was; an already formatted cairn disk is mounted, not
  formatted again;
- after `network`, a container on cairn's network (and on Docker's default bridge) cannot
  open a TCP connection to 169.254.169.254:80 while one on the backup network can, provided
  a plain container could reach that address before the rules went in. If nothing answers
  there at all, only the iptables rules are checked, and the PASS line says it is the weaker
  check;
- started as runcmd starts them, the units bring up Caddy and Litestream; an updater run
  while nothing is tagged changes nothing; the first deployment comes from
  cairn-update.service, as it would on a first boot; Caddy serves cairn
  over HTTPS with HSTS; cairn trusts exactly the subnet of the network host.sh made; a key
  made in the container works through Caddy over REST and MCP;
- Litestream replicates; with the services stopped and the database files moved aside,
  starting them again restores the database before cairn starts, and a note written earlier
  is found;
- the replica check (cairn-replica-check.service, run as its timer runs it, the template
  rendered with a bound of REPLICA_BOUND seconds): "replica: current" while Litestream
  copies, in the journal and in the litestream container's own log; still current with cairn
  stopped, so that nothing writes, once the newest copy is older than the bound; with the
  replica's directories made read-only while Litestream runs, current for a write within the
  bound and then an "Error:" line in Litestream's log that the replica alert's filter, read
  from infra/gcp/monitoring.tf, matches; with litestream.service stopped, an "Error:" line in
  the journal only and a failed run; current again once Litestream copies;
- the guide's "After a workspace is deleted" command (`host.sh compact`), read from
  infra/gcp/README.md and run as written after a workspace whose notes hold a made-up word is
  deleted in cairn's container: exit 0, the log 0 bytes and no deletion waiting after it,
  litestream.service active, the word in neither file, the replica check current, and a
  restore from the replica (`host.sh litestream restore -o`, beside the live database) whole,
  with the rehearsal's note and without the word; sent SIGTERM right after it stopped
  Litestream, it exits 143 and litestream.service is active again;
- the updater follows the deploy tag by digest, rolls back an image that never becomes
  healthy (and does not try it again), and settles a deployment an earlier boot left without
  a verdict (first interruption, second interruption, and one cut off inside a promotion);
- hold stops an update in flight (the record left names the candidate with this boot's id, and
  hold's printed "pending" line shows it) and makes the updater change nothing, pin sets the
  image cairn starts with, release refuses while the tag names another image unless forced,
  and after release the timer deploys the tag;
- the bash block of the guide's "The database is lost or damaged", read from
  infra/gcp/README.md at run time and run unchanged in one root shell, without its
  `gcloud compute ssh` and `sudo -i` lines, from three states: quiet; a hold that cuts a
  deployment short (the pin line from its comment is run with the digest hold printed on its
  "proven" line); after an automatic rollback. Each time a note written before is found after
  the restore, the files land in a new aside-... directory and earlier ones are unchanged, and
  release does what the paragraph under the block says: it ends the hold when the tag names
  the proven image, refuses otherwise, and after `release --force` the updater promotes the
  pinned-over candidate once it serves, or skips the image recorded as failed;
- "Restore to a point in time": `host.sh litestream ltx -level all` and the `-dry-run` restore
  exit 0 while cairn is up, write no file and restart nothing; after the block's first lines up
  to the `mv`, `restore -timestamp T` brings back a note written before T and not one written
  after;
- an updater killed (SIGKILL) inside a candidate's health check leaves a record with this
  boot's id, and the next run in the same boot promotes a candidate that serves and rolls back
  one that does not, leaving no record;
- the plans file (cairn_plans_file): as installed, without one, /etc/cairn/plans.yaml is
  empty and cairn runs with no --plans and no mount of it; with the template rendered again
  with a plans file (only that file of write_files changes, and it is written as cloud-init
  would) and cairn restarted, cairn runs with --plans /etc/cairn/plans.yaml mounted read-only,
  its own user (uid 10001) reads the bytes given and cannot write them, and the plan's cap is
  in force (a second project through Caddy is refused with 402); with the empty file back and
  cairn restarted, no --plans again;
- the brand (cairn_brand_*): as installed, nothing is under /etc/cairn/brand and cairn runs
  with no --brand-* option; with the template rendered again with a name holding a space, a
  PNG logo and an alt text, and sign-in on (serve takes a brand only with sign-in; the env
  file gets a made-up client), only config.env and the three brand files change, cairn runs
  with --brand-name=NAME and --brand-logo-alt=TEXT each as one argument and --brand-logo
  /etc/cairn/brand/logo mounted read-only, and its own user reads the logo's bytes and cannot
  write them; with the brand gone, no --brand-* again. This needs an image whose serve knows
  the --brand-* options: with one that does not, serve exits 2 and the check fails;
- with the metadata rules deleted, starting cairn puts them back first; with DOCKER-USER
  renamed so that no rule can be inserted, `host.sh run-cairn` and `run-caddy` refuse and start
  no container, and the updater logs the missing block;
- on a blank disk with the tag naming an image that never serves, the failed first deployment
  stops cairn, records the image as failed, leaves no proven image (`state` and the block's
  hold print "proven: none") and `systemctl start cairn.service` is skipped by its conditions;
- cleanup leaves no unit, file under /etc/cairn, container, network, iptables rule or renamed
  chain, mount or loop device of the rehearsal.

IMAGE must already be local. Writes to /etc, /run, /mnt/disks and /usr/local/bin, changes
iptables and attaches loop devices, so it refuses to run without --on-this-throwaway-machine,
without root, or when /etc/cairn or any other path it would make already exists. It removes
everything it made at the end, pass or fail. Standard library and PyYAML. Exit code 0 when
every check passes.

Stand-ins, and nothing else:
- the data disk is a loop device over a sparse file; DATA_DEVICE is a symlink to it under
  /run/cairn-rehearsal, re-pointed to a second loop device for the refusal cases;
- cairn-env.service gets a drop-in that replaces its ExecStart with a script that writes a
  one-comment env file to /run/cairn/cairn.env, so `host.sh env` is NOT exercised;
- docker-credential-gcr is a stub in /usr/local/bin that does nothing;
- the registry is a `registry:2` container on localhost:5000, on the engine's own platform
  whatever platform the rehearsed images are, and CAIRN_IMAGE points at it.
  The other versions of cairn are one-line images built FROM IMAGE: a label for a good one,
  an entrypoint that never serves for a bad one, and one that serves after 20 s for a
  deployment that must be caught inside its health check. Moving the tag is a manifest PUT, as
  `gcloud artifacts docker tags add` does;
- the replica is a directory on the data disk (file:///data/replica); after the
  point-in-time restore, "set a new replica_path, tofu apply, restart the VM" is replaced by
  moving the old replica directory away and starting cairn and Litestream;
- for the metadata block that cannot be put back, DOCKER-USER is renamed for a moment (the
  jump from FORWARD and the rules go with it, so the block stays in force) and renamed back;
- the name is `localhost`, so Caddy signs with its own authority;
- the metadata server is whatever answers at 169.254.169.254 on the runner, used only as a
  target for the block. The rehearsal connects to it and never reads an answer.
- cairn-update.timer is not started with the other units (runcmd starts it), so that every
  updater run is one the rehearsal starts itself; `release` is checked to start it, it is seen
  to deploy once, and then stopped. cairn-replica-check.timer is not started either: the
  rehearsal starts each run of the check itself;
- for a replica that refuses Litestream, its directories are made read-only (0555) for a
  while, so that Litestream and the check can still list it and Litestream cannot add a file.

What it does not cover:
- Container-Optimized OS itself: its read-only root, /etc on tmpfs, its Docker, iptables,
  systemd and kernel versions, its path layout, and gcr-online.target (the run prints its
  LoadState on the runner; where it is not found, the units' Wants= on it pulls in nothing);
- cloud-init applying the file: the rendering here is this script's (compared with OpenTofu's
  templatefile when `tofu` is on PATH), the files are written by this script, the `users:`
  entry (cairnhost) is not created, and runcmd's `--no-block` start is replaced by a blocking
  one;
- `host.sh env`: the metadata token and Secret Manager over HTTPS;
- Google Cloud Storage and the credentials Litestream gets on the backup network;
- Artifact Registry and its authentication (docker-credential-gcr), and its wording for a
  missing tag (registry:2 says "manifest unknown", which host.sh expects);
- the guest-attribute status report (on the runner every report fails, and host.sh logs
  "could not publish the deployment status" and carries on) and scripts/deploy_production.sh
  reading it;
- a real reboot: the boot id never changes, so an earlier boot is a pending record that names
  a made-up boot id, and the "boot" is a restart of cairn-disk.service, which systemd
  propagates to the units that require it;
- a real certificate, the firewall in front of the VM, and the uptime check;
- the replica check's lines reaching Cloud Logging, the log-based metric that counts them and
  the alert on their silence (here the lines are read from the journal and `docker logs`),
  the bound main.tf gives (900 s; here REPLICA_BOUND), and listing a bucket (here the replica
  is a directory);
- whether the block holds after a Docker daemon restart;
- the disk check past the first 4 MiB: a disk with no signature and zero bytes in its first
  4 MiB is formatted, by design of host.sh;
- disk growth (resize2fs runs but the loop device never grows);
- a schema migration between images (all the images here share one schema);
- an updater killed at the other steps of a deployment (before the restart, between the two
  steps of a promotion, inside a rollback) and finished in the same boot, and converge
  repairing a container that runs the wrong image;
- the steps of the runbooks that need Google Cloud: a new replica_path with tofu apply and a
  VM restart, "The replica was deleted or tampered with" (soft delete, gcloud storage
  restore), the disk-snapshot path of "The data disk is lost", and its replica path (here a
  blank disk has no replica to restore: the replica lives on the lost disk);
- how far back -timestamp lands (exclusive, the spacing of restore points): only that a note
  well before T is restored and one well after is not;
- Litestream on an older database at the same replica path (the guide avoids it);
- the units' own restart loop while the block cannot be put back: run-cairn and run-caddy are
  run directly for that case, and the block missing after a Docker restart is not produced;
- the DNS exception (port 53) and the UDP rule: both are checked as rules, not with traffic;
- the plans file's way through OpenTofu: cairn_plans_file's checks, the precondition against
  --plans in cairn_extra_args, and file() reading it (the rendering here is given the text
  directly; tests/unit/test_infra_tf_unit.py checks those as text), and cloud-init writing an
  empty file for an empty block (here it is this script that writes it);
- the brand's way through OpenTofu (its checks and base64 of the values: `tofu test`,
  infra/gcp/tests/brand.tftest.hcl, plans them), cloud-init decoding `encoding: b64` (here
  this script decodes it), what serve does with the brand, and --wall and --session-days.
"""

from __future__ import annotations

import base64
import functools
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

_spec = importlib.util.spec_from_file_location(
    "rehearse_stack", Path(__file__).with_name("rehearse_stack.py")
)
assert _spec and _spec.loader
stack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stack)
Failed = stack.Failed
expect = stack.expect
http = stack.http
trusted_networks = stack.trusted_networks

REPO = Path(__file__).resolve().parents[1]
GCP = REPO / "infra" / "gcp"
TEMPLATE = GCP / "cloud-init.yaml.tftpl"
FLAG = "--on-this-throwaway-machine"
USAGE = f"usage: sudo python3 scripts/rehearse_host.py IMAGE {FLAG}"

WORK = Path("/run/cairn-rehearsal")
DEVICE_LINK = str(WORK / "google-cairn-data")
ENV_SCRIPT = WORK / "write-env.sh"
ENV_DROP_IN = Path("/etc/systemd/system/cairn-env.service.d/rehearsal.conf")
STUB = Path("/usr/local/bin/docker-credential-gcr")
REGISTRY = "localhost:5000"
REGISTRY_CONTAINER = "cairn-rehearsal-registry"
REGISTRY_IMAGE = "registry:2"
CAIRN_IMAGE = f"{REGISTRY}/cairn"
DEPLOY_TAG = "production"
NAME = "localhost"
BASE = f"https://{NAME}"
WORD = "quartzlantern"  # a word no fixture uses, so a hit can only be the rehearsal's note

# Fixed by host.sh and the units; the rehearsal reads them, it does not choose them.
DATA_MOUNT = Path("/mnt/disks/data")
DB_DIR = DATA_MOUNT / "cairn"
CADDY_DIR = DATA_MOUNT / "caddy"
STATE_DIR = DATA_MOUNT / "deploy"
ENV_DIR = Path("/run/cairn")
HOST_SH = "/etc/cairn/host.sh"
METADATA_IP = "169.254.169.254"
NETWORKS = {"cairn": "br-cairn", "cairn-backup": "br-cairn-bkp"}
CONTAINERS = (
    "cairn",
    "caddy",
    "litestream",
    "cairn-restore",
    "cairn-litestream-tool",
    "cairn-replica-check",
)
# runcmd starts both; the rehearsal does not, so that every run of either is one it starts.
TIMERS = ("cairn-update.timer", "cairn-replica-check.timer", "cairn-compact.timer")
STOP_ORDER = (
    "cairn-update.timer",
    "cairn-update.service",
    "cairn-replica-check.timer",
    "cairn-replica-check.service",
    "cairn-compact.timer",
    "cairn-compact.service",
    "cairn.service",
    "caddy.service",
    "litestream.service",
    "cairn-restore.service",
    "cairn-env.service",
    "cairn-network.service",
    "cairn-disk.service",
)
MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)

ENV_STAND_IN = """\
# Written by scripts/rehearse_host.py: a stand-in for `host.sh env` (no Secret Manager here).
umask 077
mkdir -p /run/cairn
chmod 0700 /run/cairn
printf '# cairn host rehearsal: no settings\\n' > /run/cairn/cairn.env
"""
ENV_DROP_IN_TEXT = f"""\
# Written by scripts/rehearse_host.py: replaces `host.sh env` with {ENV_SCRIPT}.
[Service]
ExecStart=
ExecStart=/bin/sh {ENV_SCRIPT}
"""
STUB_TEXT = (
    "#!/bin/sh\n# Written by scripts/rehearse_host.py: a stand-in that does nothing.\nexit 0\n"
)
# Connects and closes: it never reads what answers, which on a real VM would be a token.
PROBE = (
    "import socket\n"
    "try:\n"
    f"    socket.create_connection(({METADATA_IP!r}, 80), 5).close()\n"
    "    print('reached')\n"
    "except OSError as error:\n"
    "    print('no answer:', type(error).__name__)\n"
)
GOOD_IMAGE = "FROM {base}\nLABEL cairn.rehearsal={label}\n"
# host.sh appends `serve --db ...` to the entrypoint; this one takes them and never serves,
# and exits on SIGTERM so that every stop does not wait out Docker's timeout.
BAD_IMAGE = (
    "FROM {base}\nLABEL cairn.rehearsal=never-serves\n"
    'ENTRYPOINT ["python", "-c", "import signal, sys, time; '
    'signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); time.sleep(10**9)"]\n'
)
# Serves, but only after 20 s: long enough to catch a deployment inside its health window,
# short enough to pass host.sh's 60 s check.
SLOW_IMAGE = (
    "FROM {base}\nLABEL cairn.rehearsal={label}\n"
    'ENTRYPOINT ["python", "-c", "import os, signal, sys, time; '
    "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); time.sleep(20); "
    "os.execvp('python', ['python', '-m', 'cairn.server', *sys.argv[1:]])\"]\n"
)
GUIDE = GCP / "README.md"
# DOCKER-USER's name while the rehearsal makes the metadata rules impossible to insert.
RENAMED_CHAIN = "CAIRN-REHEARSAL-USER"
# Where cloud-init writes the plans file (cairn_plans_file), and where cairn sees it.
PLANS_PATH = "/etc/cairn/plans.yaml"
# The plans file the rehearsal gives the template: one cap it can watch through Caddy without
# slowing a later check. The default workspace already holds a project, so a second is refused.
PLANS_TEXT = (
    "# Written by scripts/rehearse_host.py: one plan, at most one project.\n"
    "default: rehearsal\n"
    "plans:\n"
    "  rehearsal:\n"
    "    label: Rehearsal\n"
    "    projects: 1\n"
)
OVER_CAP_PROJECT = "rehearsal-over-cap"
# Where cloud-init writes the brand (cairn_brand_*), each only when it is set; the logo is at
# the same path in the container.
BRAND_NAME = "/etc/cairn/brand/name"
BRAND_LOGO = "/etc/cairn/brand/logo"
BRAND_ALT = "/etc/cairn/brand/logo-alt"
# The brand the rehearsal gives the template: a name with a space in it, a logo (a PNG, so
# binary, built by brand_logo()) and its text. serve takes a brand only with sign-in, so the
# check also turns sign-in on, with a made-up client in the env file.
BRAND_NAME_TEXT = "Knowledge Base"
BRAND_ALT_TEXT = "Juniper Field"
BRAND_EXTRA_ARGS = "--sign-in github"
BRAND_ENV = (
    "CAIRN_GITHUB_CLIENT_ID=rehearsal-client-id\n"
    "CAIRN_GITHUB_CLIENT_SECRET=rehearsal-client-secret\n"
)
CONFIG_ENV = "/etc/cairn/config.env"
REPLICA_BOUND = 60  # seconds; the template's replica_bound_seconds in the rehearsal
REPLICA_GRACE = 10  # host.sh's REPLICA_GRACE: the slack between a change and its copy
# Litestream 0.5.18, measured with the pinned image: after a failed upload it waits 10 s, then
# twice as long after each failure, up to LITESTREAM_BACKOFF_CAP, and resumes by itself. The
# refusal below ends before the 5th failure (150 s in), so the next try comes within 80 s of
# the replica being writable again (73 s measured); RESUME_WAIT is that with a margin.
LITESTREAM_BACKOFF_CAP = 300
RESUME_WAIT = 180
ENV_FILE = Path("/run/cairn/cairn.env")


def seen_script(path: str) -> str:
    """What cairn's own user sees of PATH, inside the container, as one line of JSON."""
    return (
        "import hashlib, json, os\n"
        f"path = {path!r}\n"
        "with open(path, 'rb') as handle:\n"
        "    data = handle.read()\n"
        "seen = {'sha256': hashlib.sha256(data).hexdigest(), 'uid': os.getuid()}\n"
        "seen['readable'] = os.access(path, os.R_OK)\n"
        "seen['writable'] = os.access(path, os.W_OK)\n"
        "print(json.dumps(seen))\n"
    )


# What cairn's own user sees of the plans file, inside the container.
PLANS_SEEN = seen_script(PLANS_PATH)


def brand_logo() -> bytes:
    """A 2 by 2 PNG in one green, made here: a logo that is binary and that serve can tell is a
    PNG by its content."""
    rows = b"".join(b"\x00" + b"\x2e\x6b\x4f" * 2 for _ in range(2))
    chunks = (
        (b"IHDR", (2).to_bytes(4, "big") * 2 + bytes([8, 2, 0, 0, 0])),
        (b"IDAT", zlib.compress(rows)),
        (b"IEND", b""),
    )
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")
        for kind, data in chunks
    )


# --- rendering the template -----------------------------------------------------------------
#
# OpenTofu's templatefile, for the forms cloud-init.yaml.tftpl uses and nothing else:
# ${name} and ${indent(N, chomp(name))}. Values are inserted once and never read again, as
# templatefile does: the Caddyfile and host.sh are full of ${...} of their own. Any other
# interpolation, any directive (%{), escape ($${, %%{) or strip marker (~) fails.

_OPENING = re.compile(r"\$\$\{|%%\{|\$\{|%\{")
_PLAIN = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*")
_INDENTED = re.compile(r"\s*indent\(\s*(\d+)\s*,\s*chomp\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\)\s*")


class _Interpolation(NamedTuple):
    start: int
    end: int
    name: str
    indent: int | None


def _interpolations(text: str) -> list[_Interpolation]:
    found = []
    position = 0
    while match := _OPENING.search(text, position):
        opening = match.group()
        if opening != "${":
            raise Failed(f"template form {opening!r} at offset {match.start()} is not supported")
        close = text.find("}", match.end())
        if close < 0:
            raise Failed(f"unclosed ${{ at offset {match.start()}")
        expression = text[match.end() : close]
        if plain := _PLAIN.fullmatch(expression):
            found.append(_Interpolation(match.start(), close + 1, plain.group(1), None))
        elif indented := _INDENTED.fullmatch(expression):
            name, spaces = indented.group(2), int(indented.group(1))
            found.append(_Interpolation(match.start(), close + 1, name, spaces))
        else:
            raise Failed(f"template expression ${{{expression}}} is not supported")
        position = close + 1
    return found


def _chomp(value: str) -> str:
    return re.sub(r"(?:\r\n|\n)+\Z", "", value)


def render_template(text: str, values: dict[str, str]) -> str:
    """templatefile(text, values) for the two forms the shipped template uses."""
    parts = []
    position = 0
    for found in _interpolations(text):
        if found.name not in values:
            raise Failed(f"the template uses {found.name!r}, which has no value: missing")
        value = values[found.name]
        if found.indent is not None:
            value = _chomp(value).replace("\n", "\n" + " " * found.indent)
        parts += [text[position : found.start], value]
        position = found.end
    parts.append(text[position:])
    return "".join(parts)


def template_names(text: str) -> set[str]:
    return {found.name for found in _interpolations(text)}


def templatefile_keys(main_tf: str) -> set[str]:
    """The names infra/gcp/main.tf passes to templatefile for the cloud-init document."""
    block = re.search(
        r"templatefile\([^,]*cloud-init\.yaml\.tftpl\",\s*\{(.*?)\n\s*\}\)", main_tf, re.S
    )
    expect(block is not None, "main.tf has no templatefile call for cloud-init.yaml.tftpl")
    assert block is not None
    return set(re.findall(r"^\s*([a-z_]+)\s*=", block.group(1), re.M))


def variable_defaults(tf: str, names: list[str]) -> dict[str, str]:
    found = {}
    for name in names:
        block = re.search(rf'variable "{re.escape(name)}" \{{(.*?)\n\}}', tf, re.S)
        default = block and re.search(r'^\s*default\s*=\s*"([^"]*)"', block.group(1), re.M)
        if not default:
            raise Failed(f"variables.tf has no default for {name}")
        found[name] = default.group(1)
    return found


def container_limit_defaults(tf: str) -> dict[str, tuple[int, int]]:
    """container_limits' default in variables.tf: (memory in MiB, process limit) by container."""
    block = re.search(r'variable "container_limits" \{(.*?)\n\}', tf, re.S)
    default = block and re.search(r"^\s*default\s*=\s*\{(.*?)^\s*\}", block.group(1), re.S | re.M)
    if not default:
        raise Failed("variables.tf has no default for container_limits")
    found = re.findall(
        r"^\s*([a-z]+)\s*=\s*\{\s*memory_mb\s*=\s*(\d+),\s*pids\s*=\s*(\d+)\s*\}",
        default.group(1),
        re.M,
    )
    return {name: (int(memory), int(pids)) for name, memory, pids in found}


def hardening_problems(name: str, config: dict[str, Any], limits: tuple[int, int]) -> list[str]:
    """What the container NAME, by its `docker inspect` HostConfig, lacks of what host.sh
    gives it: every capability dropped, Caddy's one back, no-new-privileges, and the memory
    (with no swap on top) and process limits of container_limits."""
    memory, pids = limits[0] * 1024 * 1024, limits[1]
    added = ["NET_BIND_SERVICE"] if name == "caddy" else []
    problems = []
    if (config.get("CapDrop") or []) != ["ALL"]:
        problems.append(f"{name} drops {config.get('CapDrop')}, not ['ALL']")
    if (config.get("CapAdd") or []) != added:
        problems.append(f"{name} adds {config.get('CapAdd')}, not {added}")
    if (config.get("SecurityOpt") or []) != ["no-new-privileges:true"]:
        problems.append(f"{name} has security options {config.get('SecurityOpt')}")
    if config.get("Memory") != memory:
        problems.append(f"{name} has a memory limit of {config.get('Memory')}, not {memory}")
    elif config.get("MemorySwap") != memory:
        problems.append(f"{name} has memory and swap of {config.get('MemorySwap')}, not {memory}")
    if config.get("PidsLimit") != pids:
        problems.append(f"{name} has a process limit of {config.get('PidsLimit')}, not {pids}")
    return problems


def rehearsal_values(repo: Path) -> dict[str, str]:
    """What OpenTofu would pass, with the rehearsal's stand-ins and the shipped image pins."""
    gcp = repo / "infra" / "gcp"
    pins = variable_defaults(
        (gcp / "variables.tf").read_text(encoding="utf-8"), ["caddy_image", "litestream_image"]
    )
    limits = container_limit_defaults((gcp / "variables.tf").read_text(encoding="utf-8"))
    return {
        **{f"{name}_memory_mb": str(memory) for name, (memory, _) in limits.items()},
        **{f"{name}_pids_limit": str(pids) for name, (_, pids) in limits.items()},
        "domain": NAME,
        "public_url": BASE,
        "acme_email": "ops@example.test",
        "extra_args": "",
        "registry_host": REGISTRY,
        "image": CAIRN_IMAGE,
        "deploy_tag": DEPLOY_TAG,
        "env_secret": "projects/cairn-rehearsal/secrets/cairn-env",  # never read: env is stood in
        "replica_url": "file:///data/replica",
        "caddy_image": pins["caddy_image"],
        "litestream_image": pins["litestream_image"],
        "data_device": DEVICE_LINK,
        # main.tf's base64gzip(file(...)): the bytes differ from Go's gzip, what they hold not.
        "host_sh": base64.b64encode(
            gzip.compress((gcp / "files" / "host.sh").read_bytes(), mtime=0)
        ).decode("ascii"),
        "caddyfile": (repo / "deploy" / "Caddyfile").read_text(encoding="utf-8"),
        "litestream_yml": (repo / "deploy" / "litestream.yml").read_text(encoding="utf-8"),
        "plans_yaml": "",  # cairn_plans_file's default; one check installs PLANS_TEXT instead
        "brand_files": "",  # no cairn_brand_*; one check renders brand_files(...) instead
        # main.tf gives 900; a minute here, so the replica check can be seen past its bound.
        "replica_bound_seconds": str(REPLICA_BOUND),
    }


def brand_files(name: str = "", logo: bytes = b"", alt: str = "") -> str:
    """main.tf's local.brand_files: one write_files entry for each brand value that is set, in
    this order, its bytes base64 so that cloud-init writes them exactly (a PNG is binary) and
    no YAML quoting or shell reads them. Nothing at all when none is set."""
    values = ((BRAND_NAME, name.encode("utf-8")), (BRAND_LOGO, logo), (BRAND_ALT, alt.encode()))
    return "".join(
        f'  - path: {path}\n    permissions: "0644"\n    owner: root\n'
        f'    encoding: b64\n    content: "{base64.b64encode(data).decode("ascii")}"\n\n'
        for path, data in values
        if data
    )


# --- reading write_files and runcmd ---------------------------------------------------------


class WriteFile(NamedTuple):
    path: str
    permissions: int
    content: str | bytes  # bytes for an entry with encoding: b64, as cloud-init decodes it


def _yaml() -> Any:
    try:
        import yaml
    except ImportError as error:
        raise Failed(
            "PyYAML is needed to read the cloud-init document: apt-get install python3-yaml"
        ) from error
    return yaml


def _document(rendered: str) -> dict[str, Any]:
    expect(rendered.startswith("#cloud-config\n"), "the document does not start #cloud-config")
    document = _yaml().safe_load(rendered)
    expect(isinstance(document, dict), "the cloud-init document is not a mapping")
    return document


def write_files(rendered: str) -> list[WriteFile]:
    """The files cloud-init would write, refusing any setting this script cannot honour."""
    files = []
    for entry in _document(rendered).get("write_files") or []:
        unknown = set(entry) - {"path", "permissions", "owner", "content", "encoding"}
        expect(not unknown, f"{entry.get('path')}: write_files setting {sorted(unknown)}")
        owner = entry.get("owner", "root:root")
        expect(owner in ("root", "root:root"), f"{entry['path']}: owner {owner!r} is not root")
        permissions = entry.get("permissions", "0644")
        if isinstance(permissions, str):
            expect(bool(re.fullmatch(r"0?[0-7]{3,4}", permissions)), f"permissions {permissions!r}")
            permissions = int(permissions, 8)
        content = entry.get("content", "")
        encoding = entry.get("encoding")
        expect(
            encoding in (None, "b64", "gz+b64"),
            f"{entry['path']}: encoding {encoding!r} is not b64 or gz+b64",
        )
        if encoding in ("b64", "gz+b64"):
            try:
                content = base64.b64decode(content, validate=True)
            except (TypeError, ValueError) as error:
                raise Failed(f"{entry['path']}: content is not base64: {error}") from error
        if encoding == "gz+b64":
            try:
                content = gzip.decompress(content)
            except (OSError, EOFError) as error:
                raise Failed(f"{entry['path']}: content is not gzip: {error}") from error
        files.append(WriteFile(entry["path"], permissions, content))
    return files


def unit_names(files: list[WriteFile]) -> list[str]:
    prefix = "/etc/systemd/system/"
    return [f.path[len(prefix) :] for f in files if f.path.startswith(prefix)]


def runcmd(rendered: str) -> list[str]:
    commands = _document(rendered).get("runcmd") or []
    return [c if isinstance(c, str) else " ".join(c) for c in commands]


def started_units(commands: list[str]) -> list[str]:
    units = []
    for command in commands:
        words = command.split()
        if words[:2] == ["systemctl", "start"]:
            units += [word for word in words[2:] if not word.startswith("-")]
    return units


# --- what the host says ---------------------------------------------------------------------


def parse_state(text: str) -> dict[str, str]:
    """`host.sh state`, keyed by the first word of each line: tag, proven, running, ..."""
    state = {}
    for line in text.splitlines():
        head, colon, value = line.partition(":")
        words = head.split()
        if colon and words and words[0] != "cairn-host":  # host.sh's own log lines
            state[words[0]] = value.strip()
    return state


def pending_record(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    return (lines[0] if lines else "", lines[1] if len(lines) > 1 else "")


def mount_entry(mounts: str, mountpoint: str) -> tuple[str, str, set[str]] | None:
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1] == mountpoint:
            return fields[0], fields[2], set(fields[3].split(","))
    return None


def metadata_drop_rules(listing: str) -> list[str]:
    """The protocols of the complete metadata-blocking rules in `iptables -S DOCKER-USER`."""

    def has(words: list[str], *sequence: str) -> bool:
        n = len(sequence)
        return any(words[i : i + n] == list(sequence) for i in range(len(words) - n + 1))

    found = []
    for line in listing.splitlines():
        words = line.split()
        if words[:2] != ["-A", "DOCKER-USER"]:
            continue
        protocol = next((p for p in ("tcp", "udp") if has(words, "-p", p)), None)
        if (
            protocol
            and has(words, "-d", f"{METADATA_IP}/32")
            and has(words, "!", "-i", NETWORKS["cairn-backup"])
            and has(words, "!", "--dport", "53")
            and has(words, "-j", "DROP")
        ):
            found.append(protocol)
    return found


def plans_argument(args: list[str]) -> str | None:
    """What --plans names in a container's command line ("" when nothing follows it), or None
    when the command has no --plans."""
    for i, arg in enumerate(args):
        if arg == "--plans":
            return args[i + 1] if i + 1 < len(args) else ""
        if arg.startswith("--plans="):
            return arg.split("=", 1)[1]
    return None


def option_values(args: list[str], option: str) -> list[str]:
    """Every value OPTION has in a command line, as argparse reads it: the word after OPTION,
    or what follows OPTION= in the same word."""
    found = []
    for i, arg in enumerate(args):
        if arg == option:
            found.append(args[i + 1] if i + 1 < len(args) else "")
        elif arg.startswith(option + "="):
            found.append(arg[len(option) + 1 :])
    return found


def mounts_at(mounts: list[dict[str, Any]] | None, destination: str) -> list[tuple[str, bool]]:
    """(source, writable) of each mount at DESTINATION in `docker inspect`'s .Mounts."""
    return [
        (str(m.get("Source", "")), bool(m.get("RW")))
        for m in mounts or []
        if m.get("Destination") == destination
    ]


def probe_reached(output: str) -> bool:
    lines = output.strip().splitlines()
    return bool(lines) and lines[-1].strip() == "reached"


def journal_cursor(said: str) -> str:
    """The cursor in `journalctl -n 0 --show-cursor` output, which may say "-- No entries --"."""
    for line in said.splitlines():
        if line.startswith("-- cursor: "):
            return line.removeprefix("-- cursor: ").strip()
    raise Failed(f"journalctl gave no cursor: {said[-200:]!r}")


def run_ended(log: str) -> bool:
    """Whether a unit's log (journalctl -o cat) holds systemd's own line for the end of a run."""
    for line in log.splitlines():
        if line.startswith("cairn-host"):
            continue
        if (
            line.endswith(": Deactivated successfully.")
            or ": Failed with result " in line
            or line.startswith(("Finished ", "Stopped "))
        ):
            return True
    return False


_REPLICA_LINE = re.compile(
    r"^replica: current, newest (\d+) s old$|^Error: replica: newest (\d+) s old, over the bound$"
)


def replica_lines(log: str) -> list[tuple[str, int]]:
    """The replica check's lines in LOG, in order: ("current" or "error", the newest copy's age
    in seconds). Only whole lines in host.sh's two shapes count."""
    found = []
    for line in log.splitlines():
        match = _REPLICA_LINE.match(line.strip())
        if match and match.group(1):
            found.append(("current", int(match.group(1))))
        elif match:
            found.append(("error", int(match.group(2))))
    return found


def first_difference(a: str, b: str) -> str:
    left, right = a.splitlines(), b.splitlines()
    for number, (one, other) in enumerate(zip(left, right, strict=False), start=1):
        if one != other:
            return f"line {number}: {one[:80]!r} != {other[:80]!r}"
    if len(left) != len(right):
        shorter = min(len(left), len(right))
        return f"line {shorter + 1}: one side ends ({len(left)} vs {len(right)} lines)"
    return "" if a == b else "the line endings differ"


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


# --- the guide's runbooks, as written -------------------------------------------------------
#
# The checks run the commands infra/gcp/README.md gives, read from it at run time: when the
# guide changes, the rehearsal runs the new text.


def guide_part(text: str, heading: str) -> str:
    """From `heading` to the next heading of the same or a higher level outside code blocks
    (a shell comment at the start of a line in a block is not a heading)."""
    level = len(heading.split(" ", 1)[0])
    start = text.index(f"\n{heading}\n") + 1
    lines = text[start:].splitlines(keepends=True)
    part, fenced = lines[:1], False
    for line in lines[1:]:
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and (found := re.match(r"(#+) ", line)) and len(found.group(1)) <= level:
            break
        part.append(line)
    return "".join(part)


def bash_blocks(text: str, heading: str) -> list[str]:
    """The ```bash blocks under `heading`, raw: comments and continuation lines as written."""
    return re.findall(r"^```bash\n(.*?)^```", guide_part(text, heading), re.S | re.M)


class Runbook(NamedTuple):
    hold: str
    pin: str  # as the block's comment gives it, with sha256:DIGEST in it
    middle: list[str]
    release: str
    forced_release: str  # from the paragraph under the block


class Followed(NamedTuple):
    held: dict[str, str]  # what `hold` printed
    pinned: str  # the digest the pin line was run with, or ""
    aside: str  # the aside-... directory the block made
    moved: list[str]  # what is in it
    statuses: list[str]  # "COMMAND-WORD EXIT" for each line after the hold and pin


LOST_DATABASE = "### The database is lost or damaged"
COMPACT_RUNBOOK = "### After a workspace is deleted"


def word_counts(folder: Path = DB_DIR, word: str = stack.DELETED_WORD) -> dict[str, int]:
    """How often WORD is in the raw bytes of cairn.db and cairn.db-wal (0 for a missing file)."""
    found = {}
    for name in ("cairn.db", "cairn.db-wal"):
        path = folder / name
        found[name] = path.read_bytes().count(word.encode()) if path.exists() else 0
    return found


POINT_IN_TIME = "### Restore to a point in time"


def _left_out(command: str) -> bool:
    """The lines that get the operator a root shell on the VM; the rehearsal is root already."""
    return command.startswith("gcloud compute ssh ") or command == "sudo -i"


def lost_database_runbook(text: str, heading: str = LOST_DATABASE) -> Runbook:
    blocks = bash_blocks(text, heading)
    expect(bool(blocks), f"no bash block under {heading!r}")
    commands = [c for c in stack._commands(blocks[0]) if not _left_out(c)]
    holds = [i for i, c in enumerate(commands) if re.match(r"bash \S*host\.sh hold\b", c)]
    releases = [i for i, c in enumerate(commands) if re.match(r"bash \S*host\.sh release\b", c)]
    expect(holds == [0], f"the block does not start with one hold: {commands}")
    expect(releases == [len(commands) - 1], f"the block does not end with one release: {commands}")
    pins = re.findall(r"^#\s+(bash \S*host\.sh pin \S+)", blocks[0], re.M)
    expect(len(pins) == 1, f"the block's comments give {len(pins)} pin commands")
    forced = re.findall(r"`(bash \S*host\.sh release --force)`", guide_part(text, heading))
    expect(bool(forced), f"the text under {heading!r} gives no `release --force` command")
    return Runbook(commands[0], pins[0], commands[1:-1], commands[-1], forced[0])


def pin_command(template: str, digest: str) -> str:
    """The block's pin line with the digest the "proven" line shows."""
    expect(bool(re.fullmatch(r"sha256:[0-9a-f]{64}", digest)), f"no digest to pin: {digest!r}")
    expect(template.count("sha256:DIGEST") == 1, f"no sha256:DIGEST in {template!r}")
    return template.replace("sha256:DIGEST", digest)


def through_the_move(middle: list[str]) -> list[str]:
    """The block's lines up to and including the `mv` ("Restore to a point in time")."""
    moves = [i for i, c in enumerate(middle) if re.search(r"(^|&&|;)\s*mv\s", c)]
    expect(bool(moves), f"no mv among {middle}")
    return middle[: moves[0] + 1]


def point_in_time(text: str) -> tuple[list[str], str]:
    """The two commands that only read, and the restore itself."""
    blocks = bash_blocks(text, POINT_IN_TIME)
    expect(len(blocks) >= 2, f"{POINT_IN_TIME!r} has {len(blocks)} bash blocks, not 2")
    restore = stack._commands(blocks[1])
    expect(len(restore) == 1, f"the restore block holds {restore}")
    return stack._commands(blocks[0]), restore[0]


def with_timestamp(command: str, stamp: str) -> str:
    changed, count = re.subn(r"(-timestamp\s+)\S+", lambda m: m.group(1) + stamp, command)
    expect(count == 1, f"{count} -timestamp arguments in {command!r}")
    return changed


MARK = "@@cairn-rehearsal-status"


def marked_script(commands: list[str]) -> str:
    """The commands in one shell, as an operator types them, each followed by its exit status
    on a line of its own: the commands themselves are run unchanged."""
    return "".join(f"{command}\nprintf '\\n{MARK} %s\\n' \"$?\"\n" for command in commands)


def marked_results(output: str) -> list[tuple[str, int]]:
    """(output, exit status) of each command of a marked script."""
    results, lines = [], []
    for line in output.splitlines():
        if found := re.fullmatch(rf"{MARK} (\d+)", line):
            results.append(("\n".join(lines).strip(), int(found.group(1))))
            lines = []
        else:
            lines.append(line)
    return results


def aside_names(names: list[str]) -> list[str]:
    return sorted(n for n in names if re.fullmatch(r"aside-\d{8}T\d{6}Z", n))


# --- refusing to run ------------------------------------------------------------------------


def image_argument(args: list[str]) -> str:
    rest = [a for a in args if a != FLAG]
    return rest[0] if len(rest) == 1 else ""


def refusal(args: list[str], *, is_root: bool, already_there: list[str]) -> str | None:
    """Why the rehearsal must not run here, or None."""
    rest = [a for a in args if a != FLAG]
    if FLAG not in args or len(rest) != 1 or rest[0].startswith("-"):
        return (
            f"{USAGE}\nIt writes to /etc, changes iptables and attaches loop devices: run it"
            " only on a machine you will throw away, such as a CI runner."
        )
    if not is_root:
        return "it must run as root (sudo): it installs systemd units and mounts a disk"
    if already_there:
        return "refusing: these exist already, so this is not a clean machine: " + ", ".join(
            already_there
        )
    return None


# --- the rehearsal --------------------------------------------------------------------------


def sh(
    *args: str, check: bool = True, timeout: float = 300, input: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=timeout, input=input
        )
    except subprocess.TimeoutExpired as error:
        raise Failed(f"{' '.join(args[:5])} ... did not finish in {timeout:.0f}s") from error
    if check and result.returncode != 0:
        said = (result.stderr or result.stdout).strip()[-800:]
        raise Failed(f"{' '.join(args[:5])} ... exited {result.returncode}: {said}")
    return result


def out(*args: str, timeout: float = 300) -> str:
    return sh(*args, timeout=timeout).stdout.strip()


ENGINE_PLATFORM = ("docker", "version", "--format", "{{.Server.Os}} {{.Server.Arch}}")


def engine_platform(said: str) -> str:
    """The Docker engine's own platform, from what ENGINE_PLATFORM printed."""
    words = said.split()
    if len(words) != 2:
        raise Failed(f"docker version did not name the engine's platform: {said!r}")
    return "/".join(words)


def registry_command(platform: str) -> list[str]:
    """Starts the stand-in registry on PLATFORM, the engine's own. It stands in for Artifact
    Registry and is not what is rehearsed: when the rehearsed images are another platform
    (DOCKER_DEFAULT_PLATFORM), an emulated registry:2 died at the first push."""
    return [
        "docker",
        "run",
        "-d",
        "--platform",
        platform,
        "--name",
        REGISTRY_CONTAINER,
        "-p",
        f"127.0.0.1:{REGISTRY.split(':')[1]}:5000",
        REGISTRY_IMAGE,
    ]


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as device:
        while chunk := device.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def first_line(path: Path) -> str:
    return pending_record(read(path))[0]


def local_images() -> set[str]:
    listing = sh(
        "docker", "images", "--format", "{{.Repository}}:{{.Tag}}", check=False
    ).stdout.split()
    return {name for name in listing if not name.endswith(":<none>")}


def install(f: WriteFile) -> None:
    """Write one write_files entry as cloud-init does: its path, its bytes, root's, its mode."""
    path = Path(f.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = f.content if isinstance(f.content, bytes) else f.content.encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(data)
    os.chown(path, 0, 0)
    os.chmod(path, f.permissions)


def render_with_tofu(template: Path, values: dict[str, str], work: Path) -> str | None:
    """templatefile() itself, when OpenTofu is installed; None when it is not."""
    if not shutil.which("tofu"):
        return None
    work.mkdir(parents=True, exist_ok=True)
    (work / "main.tf").write_text(
        'variable "values" {\n  type = map(string)\n}\n'
        'output "rendered" {\n'
        f"  value = templatefile({json.dumps(str(template))}, var.values)\n"
        "}\n",
        encoding="utf-8",
    )
    (work / "terraform.tfvars.json").write_text(json.dumps({"values": values}), encoding="utf-8")
    chdir = f"-chdir={work}"
    sh("tofu", chdir, "init", "-input=false", "-no-color", timeout=120)
    sh("tofu", chdir, "apply", "-auto-approve", "-input=false", "-no-color", timeout=120)
    return sh("tofu", chdir, "output", "-raw", "rendered", timeout=60).stdout


class Rehearsal:
    def __init__(self, image: str, files: list[WriteFile], rendered: str) -> None:
        self.image = image
        self.files = files
        self.rendered = rendered
        self.units = unit_names(files)
        self.tmp = Path(tempfile.mkdtemp(prefix="cairn-host-rehearsal-"))
        self.loops: list[tuple[str, Path]] = []
        self.key = ""
        self.refs: dict[str, str] = {}  # label -> CAIRN_IMAGE@sha256:...
        self.made_mnt_disks = not Path("/mnt/disks").exists()
        self.made_home = not Path("/home/cairnhost").exists()
        self.made_env_dir = not ENV_DIR.exists()
        self.installed = False
        self.data_device = ""
        self.uuid = ""
        # promote() prunes images by build date; what it takes on this machine is reported.
        self.images_before = local_images()
        self.boot_units = [u for u in started_units(runcmd(rendered)) if u not in TIMERS]

    # -- helpers ------------------------------------------------------------------------------

    def host(
        self, *args: str, check: bool = True, timeout: float = 300
    ) -> subprocess.CompletedProcess[str]:
        return sh("bash", HOST_SH, *args, check=check, timeout=timeout)

    def systemctl(
        self, *args: str, check: bool = True, timeout: float = 300
    ) -> subprocess.CompletedProcess[str]:
        return sh("systemctl", *args, check=check, timeout=timeout)

    def show(self, unit: str, prop: str) -> str:
        return out("systemctl", "show", "-p", prop, "--value", unit)

    def journal_mark(self) -> str:
        """A journal cursor: what is logged after this call can be read with journal_since."""
        return journal_cursor(out("journalctl", "-n", "0", "--show-cursor", "--no-pager"))

    def journal_since(self, unit: str, cursor: str, timeout: float = 15) -> str:
        """What UNIT logged after CURSOR, once systemd has logged the end of its run."""
        deadline = time.monotonic() + timeout
        while True:
            log = sh(
                "journalctl", "-u", unit, f"--after-cursor={cursor}", "-o", "cat", "--no-pager",
                check=False,
            ).stdout  # fmt: skip
            if run_ended(log) or time.monotonic() > deadline:
                return log
            time.sleep(1)

    def update(self, *, succeeds: bool = True, timeout: float = 400) -> str:
        cursor = self.journal_mark()
        result = self.systemctl("start", "cairn-update.service", check=False, timeout=timeout)
        log = self.journal_since("cairn-update.service", cursor)
        expect("cairn-host:" in log, f"no line of host.sh in the updater's log: {log[-600:]!r}")
        expect(
            (result.returncode == 0) == succeeds,
            f"cairn-update.service {'failed' if succeeds else 'succeeded'}"
            f" (exit {result.returncode}); its log:\n{log[-1200:]}",
        )
        return log

    def container(self) -> tuple[str, str]:
        """(image, id and start time) of the container named cairn; empty while there is none."""
        result = sh(
            "docker", "inspect", "-f", "{{.Config.Image}}|{{.Id}} {{.State.StartedAt}}", "cairn",
            check=False,
        )  # fmt: skip
        if result.returncode != 0:
            return "", ""
        image, _, started = result.stdout.strip().partition("|")
        return image, started

    def proven(self) -> str:
        return first_line(STATE_DIR / "image")

    def wait_serving(self, ref: str, timeout: float = 120) -> None:
        """cairn runs REF and answers /healthz through Caddy."""
        deadline = time.monotonic() + timeout
        said = ""
        while time.monotonic() < deadline:
            image, _ = self.container()
            if image == ref:
                try:
                    status, _, _ = http("GET", f"{BASE}/healthz")
                    if status == 200:
                        return
                    said = f"/healthz through Caddy answered {status}"
                except OSError as error:
                    said = f"/healthz through Caddy: {error}"
            else:
                said = f"cairn runs {image or 'nothing'}"
            time.sleep(2)
        raise Failed(f"cairn is not serving {ref} after {timeout:.0f}s ({said})")

    def api(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        status, _, raw = http(method, BASE + path, headers={"x-api-key": self.key}, body=body)
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, None

    def hits(self, word: str = WORD) -> int:
        status, found = self.api("GET", f"/api/search?q={word}&project=rehearsal")
        expect(status == 200, f"search through Caddy answered {status}")
        return len([hit for hit in found["hits"] if word in hit["text"]])

    def note(self, word: str) -> float:
        """Write a note holding WORD through Caddy; the time just before the write."""
        text = f"# Rehearsal {word}\n\nThe {word} is written down before the restore.\n"
        started = time.time()
        status, noted = self.api("POST", "/api/projects/rehearsal/notes", {"text": text})
        expect(status == 201 and noted["status"] == "saved", f"the note answered {status}")
        expect(self.hits(word) == 1, f"search did not find the note with {word}")
        return started

    def newest_replica_file(self) -> float:
        times = [
            (Path(root) / name).stat().st_mtime
            for root, _, names in os.walk(DB_DIR / "replica")
            for name in names
        ]
        return max(times, default=0.0)

    def wait_replicated(self, since: float) -> None:
        """Two of Litestream's sync intervals (10 s), then a replica file newer than SINCE."""
        time.sleep(25)
        newest = self.newest_replica_file()
        expect(newest > since, f"Litestream wrote no replica file in the 25 s after {since:.0f}")

    def replica_check(self) -> tuple[list[tuple[str, int]], list[tuple[str, int]], str, int]:
        """One run of cairn-replica-check.service, as its timer starts it: (its lines in the
        journal, its lines in the litestream container's log, the journal, its exit status)."""
        since = str(int(time.time()))
        cursor = self.journal_mark()
        result = self.systemctl("start", "cairn-replica-check.service", check=False)
        log = self.journal_since("cairn-replica-check.service", cursor, timeout=60)
        docker = sh("docker", "logs", "--since", since, "litestream", check=False)
        said = docker.stdout + docker.stderr if docker.returncode == 0 else ""
        return replica_lines(log), replica_lines(said), log, result.returncode

    def wait_current(self, timeout: float, what: str) -> list[tuple[str, int]]:
        """Run the check every 15 s until it says current, exits 0 and no longer remembers a
        change: the change was copied. A replica file's mtime cannot say so, since Litestream
        stamps it with the time of the write it holds, not of its upload."""
        unconfirmed = DATA_MOUNT / "replica-unconfirmed"
        deadline = time.monotonic() + timeout
        while True:
            lines, logged, log, status = self.replica_check()
            if status == 0 and [k for k, _ in lines] == ["current"] and not unconfirmed.exists():
                expect(logged == lines, f"the litestream container's log has {logged}, not {lines}")
                return lines
            expect(time.monotonic() < deadline, f"not within {timeout:.0f}s: {what}:\n{log[-600:]}")
            time.sleep(15)

    def replica_dirs_writable(self, writable: bool) -> int:
        """Make every directory of the replica read-only for Litestream (0555), or 0755 again;
        how many. Litestream can still list it, as the check must; it cannot add a file."""
        dirs = [Path(root) for root, _, _ in os.walk(DB_DIR / "replica")]
        for path in dirs:
            path.chmod(0o755 if writable else 0o555)
        return len(dirs)

    def tag_ref(self) -> str:
        return f"{CAIRN_IMAGE}@{self.tag_digest(DEPLOY_TAG)}"

    def wait_for(self, condition: Callable[[], bool], timeout: float, what: str) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(1)
        raise Failed(f"not within {timeout:.0f}s: {what}")

    def stop_timer(self) -> None:
        """Stop the timer and let a run it started finish: stopping a timer stops no run."""
        self.systemctl("stop", "cairn-update.timer", check=False)
        self.wait_for(
            lambda: self.show("cairn-update.service", "ActiveState") != "activating",
            300,
            "the updater's run ended after the timer stopped",
        )

    def baseline(self) -> str:
        """What each runbook check starts from; the proven image."""
        # A run the timer started just before the check ahead of this one stopped it may still
        # be going (stopping a timer stops no run), and it can still write the state read
        # below. Let it end first: CI saw this check begin while one was "activating".
        self.stop_timer()
        files = self.state_files()
        proven = files["image"]
        quiet = proven and not (files["pending"] or files["failed-image"] or files["hold"])
        expect(bool(quiet), f"not a quiet start: {files}")
        for unit, unwanted in (
            ("cairn-update.timer", "active"),
            ("cairn-update.service", "activating"),
        ):
            state = self.show(unit, "ActiveState")
            expect(state != unwanted, f"{unit} is {state} at the start")
        expect(self.tag_ref() == proven, "the tag does not name the proven image at the start")
        self.wait_serving(proven, timeout=120)
        return proven

    def as_written(self, commands: list[str], *, pipefail: bool = False) -> list[tuple[str, int]]:
        """Run the guide's COMMANDS unchanged in one root shell, as an operator types them;
        (output with stderr, exit status) of each. Each command and its status are printed."""
        script = WORK / "runbook.sh"
        prefix = "set -o pipefail\n" if pipefail else ""
        script.write_text(prefix + marked_script(commands), encoding="utf-8")
        try:
            ran = subprocess.run(
                ["bash", str(script)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, timeout=600, cwd="/", check=False,
            )  # fmt: skip
        except subprocess.TimeoutExpired as error:
            raise Failed(f"the guide's commands did not finish in 600s: {commands}") from error
        results = marked_results(ran.stdout)
        for command, (said, status) in zip(commands, results, strict=False):
            print(f"# $ {command}\n#   exit {status}", flush=True)
            for line in redact(said, [self.key]).splitlines()[-8:]:
                print(f"#   | {line}", flush=True)
        expect(
            len(results) == len(commands),
            f"the shell ran {len(results)} of {len(commands)} commands: {ran.stdout[-600:]!r}",
        )
        return results

    def asides(self) -> dict[str, dict[str, str]]:
        """{aside-... directory: {file under it: sha256}}."""
        found = {}
        for name in aside_names(os.listdir(DATA_MOUNT)):
            top = DATA_MOUNT / name
            found[name] = {
                str((Path(root) / file).relative_to(top)): sha256_of(str(Path(root) / file))
                for root, _, files in os.walk(top)
                for file in files
            }
        return found

    def follow_runbook(self, runbook: Runbook, middle: list[str]) -> Followed:
        """The block's hold; its pin when hold printed a pending deployment, with the digest of
        the "proven" line; then MIDDLE in one shell. Stops where the block says to stop."""
        before = self.asides()
        [(said, status)] = self.as_written([runbook.hold])
        expect(status == 0, f"hold exited {status}")
        held = parse_state(said)
        expect(
            held.get("proven", "none") != "none",
            f'hold printed "proven" {held.get("proven")!r}: the block says to stop here',
        )
        pinned = ""
        if held.get("pending", "none") != "none":
            [(said, status)] = self.as_written([pin_command(runbook.pin, held["proven"])])
            expect(status == 0, f"the pin exited {status}: {said[-300:]!r}")
            pinned = held["proven"]
        results = self.as_written(middle)
        statuses = []
        for command, (said, status) in zip(middle, results, strict=True):
            word = re.split(r"[\s=]", command)[0]
            statuses.append(f"{word} {status}")
            if command.startswith("systemctl is-active "):
                states = said.split()
                count = len([word for word in command.split() if word.endswith(".service")])
                expect(
                    len(states) == count and "active" not in states,
                    f"`systemctl is-active` printed {states}",
                )
        after = self.asides()
        changed = [name for name, files in before.items() if after.get(name) != files]
        expect(not changed, f"an earlier aside directory changed: {changed}")
        made = sorted(set(after) - set(before))
        expect(len(made) == 1, f"the block made these aside directories: {made}")
        moved = sorted({path.split("/", 1)[0] for path in after[made[0]]})
        expect("cairn.db" in moved, f"{made[0]} holds {moved}, not cairn.db")
        return Followed(held, pinned, made[0], moved, statuses)

    def restored(self, ref: str, words: list[str], cursor: str) -> None:
        """cairn serves REF again on a database restored after CURSOR, with every note."""
        log = self.journal_since("cairn-restore.service", cursor)
        expect("restore step done" in log, f"the restore unit logged:\n{log[-600:]}")
        self.wait_serving(ref)
        missing = [word for word in words if self.hits(word) != 1]
        expect(not missing, f"notes missing after the restore: {missing}")

    def refused_release(self, runbook: Runbook) -> str:
        [(said, status)] = self.as_written([runbook.release])
        expect(status != 0, f"release ended the hold while the tag names another image: {said!r}")
        expect("release --force" in said, f"release refused saying {said[-300:]!r}")
        expect((STATE_DIR / "hold").exists(), "a refused release removed the hold")
        timer = self.show("cairn-update.timer", "ActiveState")
        expect(timer != "active", "a refused release started the timer")
        return said.strip().splitlines()[-1]

    def loop(self, name: str, size: int) -> str:
        backing = self.tmp / f"{name}.img"
        with open(backing, "wb") as f:
            f.truncate(size)
        device = out("losetup", "--find", "--show", str(backing))
        self.loops.append((device, backing))
        return device

    def point_device_at(self, device: str) -> None:
        WORK.mkdir(parents=True, exist_ok=True)
        link = Path(DEVICE_LINK)
        link.unlink(missing_ok=True)
        link.symlink_to(device)

    def mounted(self) -> tuple[str, str, set[str]] | None:
        return mount_entry(Path("/proc/mounts").read_text(), str(DATA_MOUNT))

    def probe(self, network: str) -> bool:
        result = sh(
            "docker", "run", "--rm", "--network", network, "--entrypoint", "python",
            self.image, "-c", PROBE, check=False, timeout=60,
        )  # fmt: skip
        return probe_reached(result.stdout)

    def build(self, label: str, dockerfile: str) -> str:
        tag = f"cairn-rehearsal:{label}"
        sh(
            "docker",
            "build",
            "-q",
            "-t",
            tag,
            "-",
            input=dockerfile.format(base=self.image, label=label),
        )
        return tag

    def registry(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any, bytes]:
        return http(method, f"http://{REGISTRY}/v2/cairn/{path}", **kwargs)

    def push(self, local: str, label: str) -> str:
        """Push LOCAL as CAIRN_IMAGE:LABEL; the reference by the digest the registry holds."""
        sh("docker", "tag", local, f"{CAIRN_IMAGE}:{label}")
        sh("docker", "push", "-q", f"{CAIRN_IMAGE}:{label}")
        ref = f"{CAIRN_IMAGE}@{self.tag_digest(label)}"
        self.refs[label] = ref
        return ref

    def tag_digest(self, tag: str) -> str:
        status, headers, _ = self.registry(
            "HEAD", f"manifests/{tag}", headers={"Accept": ", ".join(MANIFEST_TYPES)}
        )
        digest = headers.get("Docker-Content-Digest", "") if status == 200 else ""
        expect(
            bool(re.fullmatch(r"sha256:[0-9a-f]{64}", digest)), f"no digest for :{tag} ({status})"
        )
        return digest

    def move_tag(self, ref: str) -> None:
        """Point the deploy tag at REF's manifest, as `gcloud artifacts docker tags add` does."""
        digest = ref.split("@", 1)[1]
        status, headers, body = self.registry(
            "GET", f"manifests/{digest}", headers={"Accept": ", ".join(MANIFEST_TYPES)}
        )
        expect(status == 200, f"reading the manifest {digest} answered {status}")
        status, _, said = self.registry(
            "PUT", f"manifests/{DEPLOY_TAG}",
            headers={"Content-Type": headers.get("Content-Type", "")}, body=body,
        )  # fmt: skip
        expect(status == 201, f"moving :{DEPLOY_TAG} answered {status}: {said[:200]!r}")
        expect(self.tag_digest(DEPLOY_TAG) == digest, f":{DEPLOY_TAG} does not name {digest}")

    def settle_jobs(self, timeout: float = 90) -> None:
        """Wait until systemd has no job queued for cairn.service, then a little longer."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            jobs = sh("systemctl", "list-jobs", "--no-legend", check=False).stdout
            if "cairn.service" not in jobs:
                break
            time.sleep(1)
        time.sleep(5)

    def state_files(self) -> dict[str, str]:
        return {
            name: read(STATE_DIR / name).strip()
            for name in ("image", "pending", "failed-image", "interrupted", "hold")
        }

    def boot_id(self) -> str:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    def short(self, ref: str) -> str:
        if not ref:
            return ""
        label = next((k for k, v in self.refs.items() if v == ref), "?")
        return f"{label}({ref.rsplit(':', 1)[-1][:12]})"

    def diagnostics(self, failed_check: str) -> None:
        print(f"--- diagnostics after {failed_check} ---", flush=True)
        lines = []
        for unit in self.units:
            state = sh("systemctl", "is-active", unit, check=False).stdout.strip()
            lines.append(f"{unit}: {state}")
        lines.append(sh("docker", "ps", "-a", check=False).stdout)
        lines.append(f"state files: {self.state_files()}")
        for unit in self.units:
            journal = sh(
                "journalctl", "-u", unit, "-n", "40", "--no-pager", "-o", "short-monotonic",
                check=False,
            ).stdout  # fmt: skip
            lines.append(f"--- journalctl -u {unit} ---\n{journal}")
        for name in ("cairn", "caddy", "litestream"):
            logs = sh("docker", "logs", "--tail", "20", name, check=False)
            lines.append(f"--- docker logs {name} ---\n{logs.stdout}{logs.stderr}")
        print(redact("\n".join(lines), [self.key]), flush=True)
        print("--- end of diagnostics ---", flush=True)

    # -- checks -------------------------------------------------------------------------------

    def installs_the_template_as_shipped(self) -> str:
        values = rehearsal_values(REPO)
        by_tofu = render_with_tofu(TEMPLATE, values, self.tmp / "tofu")
        if by_tofu is None:
            compared = "not compared with OpenTofu (tofu is not on PATH)"
        else:
            difference = first_difference(by_tofu, self.rendered)
            expect(not difference, f"OpenTofu renders it differently: {difference}")
            compared = "identical to OpenTofu's templatefile"
        commands = runcmd(self.rendered)
        expect("systemctl daemon-reload" in commands, f"runcmd does not reload systemd: {commands}")
        self.installed = True
        for f in self.files:
            install(f)
        # The stand-ins.
        WORK.mkdir(parents=True, exist_ok=True)
        ENV_SCRIPT.write_text(ENV_STAND_IN, encoding="utf-8")
        ENV_DROP_IN.parent.mkdir(parents=True, exist_ok=True)
        ENV_DROP_IN.write_text(ENV_DROP_IN_TEXT, encoding="utf-8")
        STUB.write_text(STUB_TEXT, encoding="utf-8")
        STUB.chmod(0o755)
        self.systemctl("daemon-reload")
        for unit in self.units:
            state = self.show(unit, "LoadState")
            expect(state == "loaded", f"{unit} is {state} after daemon-reload")
        installed = Path(HOST_SH).read_bytes()
        shipped = (GCP / "files" / "host.sh").read_bytes()
        expect(installed == shipped, "the installed host.sh differs from infra/gcp/files/host.sh")
        return (
            f"{len(self.files)} files from write_files ({len(self.units)} units) at their paths"
            f" and modes; rendering {compared}; host.sh byte for byte; all units loaded"
        )

    def formats_a_blank_disk(self) -> str:
        blank = self.loop("data", 1 << 30)
        self.data_device = blank
        self.point_device_at(blank)
        self.systemctl("start", "cairn-disk.service", timeout=180)
        entry = self.mounted()
        expect(entry is not None, f"nothing is mounted at {DATA_MOUNT}")
        assert entry is not None
        device, fstype, options = entry
        expect(
            device == blank and fstype == "ext4", f"{device} {fstype} is mounted at {DATA_MOUNT}"
        )
        wanted = {"discard", "nodev", "nosuid", "noexec"}
        expect(
            wanted <= options, f"mount options {sorted(options)} lack {sorted(wanted - options)}"
        )
        label = out("blkid", "-o", "value", "-s", "LABEL", blank)
        expect(label == "cairn-data", f"the file system's label is {label!r}")
        self.uuid = out("blkid", "-o", "value", "-s", "UUID", blank)
        for path, uid, mode in (
            (DB_DIR, 10001, 0o700),
            (CADDY_DIR, 0, 0o700),
            (STATE_DIR, 0, 0o700),
        ):
            info = path.stat()
            expect(
                info.st_uid == uid and info.st_mode & 0o777 == mode,
                f"{path} is uid {info.st_uid} mode {oct(info.st_mode & 0o777)}",
            )
        (DATA_MOUNT / "rehearsal-marker").write_text(self.uuid, encoding="utf-8")
        return (
            f"blank {blank} formatted ext4 (label cairn-data) and mounted"
            f" {','.join(sorted(wanted))}; cairn/, caddy/, deploy/ made with their owners and modes"
        )

    def never_formats_a_disk_that_is_not_blank(self) -> str:
        expect(self.mounted() is not None, "the data disk from the previous check is not mounted")
        sh("umount", str(DATA_MOUNT))
        other = self.loop("not-blank", 64 << 20)
        with open(other, "r+b") as device:
            device.write(b"cairn rehearsal: these bytes are not a file system.\n" * 20000)
        self.point_device_at(other)
        said = []
        for case, prepare, wanted in (
            ("bytes and no file system", None, "is not blank; not formatting"),
            ("a swap signature", ("mkswap", other), "holds a 'swap' file system"),
        ):
            if prepare:
                sh(*prepare)
            before = sha256_of(other)
            result = self.host("disk", check=False, timeout=120)
            expect(result.returncode != 0, f"`disk` accepted {case}: {result.stdout[-300:]}")
            expect(
                wanted in result.stderr, f"`disk` refused {case} saying {result.stderr[-300:]!r}"
            )
            expect(sha256_of(other) == before, f"`disk` changed a disk holding {case}")
            expect(self.mounted() is None, f"`disk` mounted a disk holding {case}")
            said.append(case)
        self.point_device_at(self.data_device)
        self.host("disk", timeout=120)
        entry = self.mounted()
        expect(
            entry is not None and entry[0] == self.data_device,
            "the cairn disk is not mounted again",
        )
        uuid = out("blkid", "-o", "value", "-s", "UUID", self.data_device)
        expect(
            uuid == self.uuid, f"the cairn disk was formatted again (UUID {uuid} != {self.uuid})"
        )
        expect(read(DATA_MOUNT / "rehearsal-marker") == self.uuid, "the marker file is gone")
        return (
            f"refused {' and '.join(said)} with the disk byte for byte unchanged and unmounted;"
            " the formatted cairn disk mounted again with its UUID and marker file"
        )

    def blocks_the_metadata_server_for_cairns_network(self) -> str:
        before = self.probe("bridge")  # nothing of cairn's touches iptables before this
        self.systemctl("start", "cairn-network.service", timeout=120)
        self.host("network", timeout=60)  # a second run must not add a second pair
        for network, bridge in NETWORKS.items():
            have = out(
                "docker", "network", "inspect", "-f",
                '{{index .Options "com.docker.network.bridge.name"}}', network,
            )  # fmt: skip
            expect(have == bridge, f"network {network} is on bridge {have!r}, not {bridge}")
        rules = metadata_drop_rules(out("iptables", "-w", "-S", "DOCKER-USER"))
        expect(sorted(rules) == ["tcp", "udp"], f"DOCKER-USER holds these metadata rules: {rules}")
        cairn, default, backup = (self.probe(n) for n in ("cairn", "bridge", "cairn-backup"))
        expect(not cairn, f"a container on cairn's network reached {METADATA_IP}:80")
        expect(not default, f"a container on Docker's default bridge reached {METADATA_IP}:80")
        if not before:
            expect(not backup, "the backup network reached an address a plain container could not")
            return (
                f"WEAKER CHECK: nothing answered at {METADATA_IP}:80 from a plain container before"
                " the rules, so the block is shown only by the rules: one TCP and one UDP DROP in"
                " DOCKER-USER (not from br-cairn-bkp, not port 53), not doubled by a second run;"
                " both networks on their named bridges"
            )
        expect(backup, f"a container on the backup network cannot reach {METADATA_IP}:80")
        return (
            f"{METADATA_IP}:80 answered a plain container before; after `network` it does not"
            " answer cairn's network or the default bridge, and does answer cairn-backup; one TCP"
            " and one UDP rule, not doubled by a second run; both networks on their named bridges"
        )

    def serves_cairn_from_the_real_units(self) -> str:
        sh(*registry_command(engine_platform(out(*ENGINE_PLATFORM))))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if self.registry("GET", "tags/list")[0] in (200, 404):
                    break
            except Exception:  # not listening yet: refused, reset or a broken answer
                pass
            time.sleep(1)
        first = self.push(self.image, "first")
        expect(not (STATE_DIR / "image").exists(), "the blank disk already names a proven image")
        self.systemctl("start", *self.boot_units, timeout=400)
        skipped = self.show("cairn.service", "ConditionResult")
        expect(
            skipped == "no",
            f"cairn.service started with no image recorded (ConditionResult={skipped})",
        )
        for unit in (
            "cairn-env.service",
            "cairn-restore.service",
            "caddy.service",
            "litestream.service",
        ):
            state = self.show(unit, "ActiveState")
            expect(state == "active", f"{unit} is {state} after the runcmd start")
        # Before anything is tagged (README, First deploy, step 3): the updater does nothing.
        untagged = self.update()
        expect("nothing is tagged" in untagged, f"with no tag the updater logged:\n{untagged}")
        files = self.state_files()
        expect(not files["image"] and not files["pending"], f"with no tag: {files}")
        expect(self.container() == ("", ""), "a cairn container runs with nothing tagged")
        self.move_tag(first)
        self.update()  # the first deployment, as cairn-update.timer would make it
        self.wait_serving(first)
        expect(self.proven() == first, f"the proven image is {self.proven()!r}")
        status, headers, _ = http("GET", f"{BASE}/healthz")
        expect(status == 200, f"https /healthz answered {status}")
        hsts = headers.get("Strict-Transport-Security", "")
        expect("max-age=" in hsts, "no Strict-Transport-Security header")
        subnets = out(
            "docker",
            "network",
            "inspect",
            "-f",
            "{{range .IPAM.Config}}{{.Subnet}} {{end}}",
            "cairn",
        ).split()
        command = json.loads(out("docker", "inspect", "-f", "{{json .Args}}", "cairn"))
        trusted = trusted_networks(command)
        expect(trusted == subnets and trusted, f"cairn trusts {trusted}; the network is {subnets}")
        for name, network in (
            ("cairn", "cairn"),
            ("caddy", "cairn"),
            ("litestream", "cairn-backup"),
        ):
            on = out(
                "docker", "inspect", "-f",
                "{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}", name,
            ).split()  # fmt: skip
            expect(on == [network], f"the {name} container is on {on}, not [{network}]")
            config = json.loads(out("docker", "inspect", "-f", "{{json .HostConfig}}", name))
            limits = container_limit_defaults(read(GCP / "variables.tf"))
            problems = hardening_problems(name, config, limits[name])
            expect(not problems, "; ".join(problems))
        made = DB_DIR / "rehearsal.env"
        sh(
            "docker", "exec", "cairn", "python", "-m", "cairn.server", "key-create",
            "--db", "/data/cairn.db", "--label", "rehearsal", "--out", "/data/rehearsal.env",
        )  # fmt: skip
        line = read(made)
        made.unlink()
        self.key = line.split("=", 1)[1].strip() if "=" in line else ""
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
        status, _, raw = http("POST", f"{BASE}/mcp", headers=headers, body=call)
        expect(status == 200, f"MCP through Caddy answered {status}")
        tools = len(json.loads(raw)["result"]["tools"])
        return (
            "cairn.service skipped until the first deployment; an update with no tag did"
            f" nothing; then cairn-update deployed"
            f" {self.short(first)} and it serves at {BASE} with HSTS; --trusted-proxy {trusted}"
            f" is the cairn network's subnet; cairn and Caddy on cairn, Litestream on"
            f" cairn-backup, all three with no capability (Caddy: NET_BIND_SERVICE back),"
            f" no-new-privileges and the default memory and process limits;"
            f" REST: project, note, 1 hit; MCP lists {tools} tools"
        )

    def restores_the_database_before_cairn_starts(self) -> str:
        replica = DB_DIR / "replica"
        deadline = time.monotonic() + 90
        count = 0
        while time.monotonic() < deadline and count == 0:
            time.sleep(5)
            count = sum(len(files) for _, _, files in os.walk(replica))
        expect(count > 0, f"Litestream replicated nothing to {replica} in 90 s")
        time.sleep(25)  # two more sync intervals, so the note is in the replica
        self.systemctl("stop", "cairn.service", "litestream.service", "cairn-restore.service")
        aside = DATA_MOUNT / "rehearsal-aside"
        aside.mkdir()
        moved = []
        for name in ("cairn.db", "cairn.db-wal", "cairn.db-shm", ".cairn.db-litestream"):
            if (DB_DIR / name).exists():
                shutil.move(str(DB_DIR / name), aside / name)
                moved.append(name)
        expect("cairn.db" in moved, f"there was no cairn.db to move aside ({moved})")
        cursor = self.journal_mark()
        self.systemctl("start", *self.boot_units, timeout=400)
        self.wait_serving(self.proven())
        expect((DB_DIR / "cairn.db").exists(), "cairn.db is not there after the restart")
        expect(self.hits() == 1, "the note is not in the restored database")
        restored = int(self.show("cairn-restore.service", "ActiveEnterTimestampMonotonic"))
        started = int(self.show("cairn.service", "ExecMainStartTimestampMonotonic"))
        expect(
            0 < restored <= started, f"cairn started at {started}, the restore ended at {restored}"
        )
        log = self.journal_since("cairn-restore.service", cursor)
        expect("restore step done" in log, f"the restore unit logged:\n{log[-600:]}")
        shutil.rmtree(aside)
        return (
            f"{count} replica files; with {', '.join(moved)} moved aside the runcmd start restored"
            f" the database {(started - restored) / 1e6:.1f}s before cairn started; key and note"
            " are there"
        )

    def tells_a_current_replica_from_one_that_stopped(self) -> str:
        """cairn-replica-check.service, run as its timer runs it, with the bound at
        REPLICA_BOUND: current while Litestream copies; current for a database nothing writes
        to, past the bound; an Error line in Litestream's own log, which the replica alert
        matches, when the replica refuses a write past the bound; current again once it is
        writable, after Litestream's back-off and without a restart; an Error line in the journal
        only, and a failed run, while Litestream is stopped; current again once it copies."""
        proven = self.proven()
        self.wait_serving(proven)
        unconfirmed = DATA_MOUNT / "replica-unconfirmed"
        alert = stack.parse_filter(
            stack.alert_filter(stack.MONITORING.read_text(encoding="utf-8")), stack.STAND_INS
        )

        # Copying.
        self.wait_replicated(self.note("amberfinch"))
        lines, logged, log, status = self.replica_check()
        expect(
            status == 0 and len(lines) == 1 and lines[0][0] == "current",
            f"while Litestream copies, the check exited {status}:\n{log[-600:]}",
        )
        expect(lines[0][1] <= REPLICA_BOUND, f"the newest copy is {lines[0][1]} s old")
        expect(logged == lines, f"the litestream container's log has {logged}, not {lines}")
        copying = lines[0][1]

        # Idle: cairn stopped, so nothing writes; past the bound the copy is still current.
        # Litestream may still make a file of its own (a checkpoint) a little after the last
        # write, so the check runs every 20 s until the newest copy is past the bound.
        self.systemctl("stop", "cairn.service", timeout=120)
        deadline = time.monotonic() + 300
        lines = []
        while not lines or lines[0][1] <= REPLICA_BOUND:
            expect(time.monotonic() < deadline, f"idle, yet the newest copy stays at {lines}")
            time.sleep(20)
            lines, logged, log, status = self.replica_check()
            expect(
                status == 0 and len(lines) == 1 and lines[0][0] == "current",
                f"for an idle database the check exited {status}:\n{log[-600:]}",
            )
        expect(not unconfirmed.exists(), f"idle, yet {unconfirmed} names a change")
        idle = lines[0][1]
        self.systemctl("start", "cairn.service")
        self.wait_serving(proven)

        # Refused: Litestream runs and cannot add a file; a write waits past the bound.
        dirs = self.replica_dirs_writable(False)
        try:
            time.sleep(REPLICA_GRACE + 2)  # the write lands after the slack of the last copy
            self.note("ashenkestrel")
            first, logged_first, log, status = self.replica_check()
            expect(
                status == 0 and [k for k, _ in first] == ["current"] and unconfirmed.exists(),
                f"a write not copied yet, within the bound: exit {status}:\n{log[-600:]}",
            )
            expect(logged_first == first, f"the container's log has {logged_first}, not {first}")
            time.sleep(REPLICA_BOUND + 5)
            lines, logged, log, status = self.replica_check()
            expect(
                status == 0 and [k for k, _ in lines] == ["error"],
                f"a write refused past the bound: exit {status}:\n{log[-600:]}",
            )
            expect(logged == lines, f"the litestream container's log has {logged}, not {lines}")
            docker = sh("docker", "logs", "--since", "5m", "litestream")
            said = (docker.stdout + docker.stderr).splitlines()
            error_line = next(x for x in said if x.startswith("Error: replica:"))
            expect(
                stack.matches(alert, stack.cos_entry(error_line)),
                f"the replica alert's filter does not match {error_line!r}",
            )
            refused = lines[0][1]
        finally:
            self.replica_dirs_writable(True)
        given_back, given_at = time.monotonic(), str(int(time.time()))
        self.wait_current(RESUME_WAIT, "Litestream copied the change once the replica was writable")
        resumed = time.monotonic() - given_back
        docker = sh("docker", "logs", "--since", given_at, "litestream")
        expect(
            stack.said_recovered((docker.stdout + docker.stderr).splitlines()),
            "Litestream copied again without logging that its sync recovered",
        )

        # Stopped: no container to write into; the line reaches the journal only.
        self.systemctl("stop", "litestream.service", timeout=120)
        time.sleep(REPLICA_GRACE + 2)
        self.note("cobaltwren")
        lines, logged, log, status = self.replica_check()
        expect(
            status != 0 and [k for k, _ in lines] == ["current"] and not logged,
            f"Litestream stopped, within the bound: exit {status}:\n{log[-600:]}",
        )
        time.sleep(REPLICA_BOUND + 5)
        lines, logged, log, status = self.replica_check()
        expect(
            status != 0 and [k for k, _ in lines] == ["error"] and not logged,
            f"Litestream stopped past the bound: exit {status}:\n{log[-600:]}",
        )
        expect("the litestream container is not running" in log, f"the journal:\n{log[-600:]}")
        stopped = lines[0][1]

        # Back.
        self.systemctl("start", "litestream.service")
        self.wait_current(120, "Litestream copied the change once it was started again")
        return (
            f"bound {REPLICA_BOUND} s: current while copying (newest {copying} s old, in the"
            f" litestream container's log too); idle with cairn stopped, current at {idle} s;"
            f" with the {dirs} replica directories read-only, current within the bound and"
            f" Error at {refused} s past it, in Litestream's log, matched by the replica alert's"
            f" filter; writable again, Litestream copied by itself, without a restart, and the"
            f" check said current {resumed:.0f}s later; with Litestream stopped, Error at"
            f" {stopped} s in the journal only and the run failed; current again once it copied"
        )

    def compacts_with_the_replica_paused(self) -> str:
        """The guide's "After a workspace is deleted", its command as written, after a
        workspace whose notes hold stack.DELETED_WORD is deleted in cairn's container: it exits
        0, says the log is 0 bytes and no deletion waits, and litestream.service runs again;
        the word is in neither file; the replica check says current; a restore from the replica
        (host.sh litestream restore -o, beside the live database) is whole, holds the
        rehearsal's note and not the word. Then the same command, sent SIGTERM right after it
        stopped Litestream: it exits 143 and litestream.service runs again."""
        proven = self.proven()
        self.wait_serving(proven)
        blocks = bash_blocks(GUIDE.read_text(encoding="utf-8"), COMPACT_RUNBOOK)
        expect(len(blocks) == 1, f"{COMPACT_RUNBOOK!r} has {len(blocks)} bash blocks, not 1")
        commands = stack._commands(blocks[0])
        expect(
            len(commands) == 1 and commands[0].endswith("host.sh compact"),
            f"the runbook's command is {commands}",
        )
        deleted = out("docker", "exec", "cairn", "python", "-c", stack.DELETE_A_WORKSPACE)
        expect(deleted.splitlines()[-1:] == ["True"], f"deleting the workspace said {deleted!r}")
        time.sleep(25)  # two of Litestream's sync intervals
        before = word_counts()
        expect(before["cairn.db-wal"] > 0, f"the deleted word is not in the log: {before}")

        [(said, status)] = self.as_written(commands)
        expect(status == 0, f"`{commands[0]}` exited {status}:\n{said[-1200:]}")
        lines = [line.removeprefix("cairn-host: ") for line in said.splitlines()]
        after = next((x for x in lines if x.startswith("compact: after: ")), "")
        expect(
            "write-ahead log 0 bytes;" in after and after.endswith("for a compaction: no"),
            f"the after line is {after!r}",
        )
        expect(lines[-1:] == ["compact: done"], f"the last line is {lines[-1:]}")
        stopped_for = next(
            (x.rsplit(" ", 2)[-2] for x in lines if x.startswith("compact: step 5/6: ")), "?"
        )
        expect(self.show("litestream.service", "ActiveState") == "active", "Litestream is down")
        counts = word_counts()
        expect(counts == {"cairn.db": 0, "cairn.db-wal": 0}, f"after the command: {counts}")
        self.wait_current(120, "the replica check after host.sh compact")

        # A restore from the replica, beside the live database: whole, the note, not the word.
        restored = DB_DIR / "compact-restored.db"
        try:
            self.host("litestream", "restore", "-o", f"/data/{restored.name}", "/data/cairn.db")
            check = sh(
                "python3", "-c",
                "import sqlite3, sys; con = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro',"
                " uri=True); print(con.execute('PRAGMA integrity_check').fetchone()[0])",
                str(restored),
            ).stdout.strip()  # fmt: skip
            raw = b"".join(
                path.read_bytes() for path in DB_DIR.glob(f"{restored.name}*") if path.is_file()
            )
        finally:
            for path in DB_DIR.glob(f"{restored.name}*"):
                path.unlink()
        expect(check == "ok", f"the restored database's integrity check says {check!r}")
        expect(WORD.encode() in raw, "the rehearsal's note is not in the restored database")
        expect(stack.DELETED_WORD.encode() not in raw, "the restored database holds the word")

        # Interrupted right after the stop: the trap starts Litestream again.
        proc = subprocess.Popen(
            ["bash", HOST_SH, "compact"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )  # fmt: skip
        seen = []
        assert proc.stdout is not None
        for line in proc.stdout:
            seen.append(line.rstrip("\n"))
            if "step 3/6: litestream.service stopped" in line:
                proc.terminate()
        code = proc.wait(timeout=120)
        expect(code == 143, f"interrupted, host.sh compact exited {code}: {seen[-6:]}")
        expect(any("litestream.service started again" in x for x in seen), f"it said {seen[-6:]}")
        expect(self.show("litestream.service", "ActiveState") == "active", "Litestream is down")
        self.wait_current(120, "the replica check after an interrupted host.sh compact")
        return (
            f"`{commands[0]}` as written: exit 0, Litestream stopped for {stopped_for} s, the log"
            f" 0 bytes after; the deleted word {before['cairn.db-wal']} times in the log before,"
            " in neither file after; the replica current; a restore from it whole, with the"
            " note and without the word; sent SIGTERM after the stop, it exited 143 and"
            " litestream.service ran again"
        )

    def follows_the_deploy_tag(self) -> str:
        good = self.push(self.build("good-2", GOOD_IMAGE), "good-2")
        self.move_tag(good)
        log = self.update()
        self.wait_serving(good, timeout=30)
        label = out(
            "docker", "inspect", "-f", '{{index .Config.Labels "cairn.rehearsal"}}', "cairn"
        )
        expect(label == "good-2", f"the cairn container's label is {label!r}")
        files = self.state_files()
        expect(files["image"] == good, f"the state file names {files['image']!r}")
        expect(not files["pending"], f"a pending record is left: {files['pending']!r}")
        state = parse_state(self.host("state").stdout)
        digest = good.split("@", 1)[1]
        expect(
            state.get("tag") == digest
            and state.get("proven") == digest
            and state.get("running") == digest,
            f"`state` says {state}",
        )
        expect(self.hits() == 1, "the note is not there after the update")
        expect("deploying" in log, f"the updater's log does not say it deployed:\n{log[-600:]}")
        return (
            f"moved the tag to {self.short(good)}; the updater pulled it by digest, cairn runs it,"
            " deploy/image and `state` name it"
        )

    def rolls_back_an_unhealthy_image(self) -> str:
        proven = self.proven()
        bad = self.push(self.build("never-serves", BAD_IMAGE), "never-serves")
        self.move_tag(bad)
        started = time.monotonic()
        log = self.update(succeeds=False)
        took = time.monotonic() - started
        failed_state = self.show("cairn-update.service", "ActiveState")
        expect(failed_state == "failed", f"cairn-update.service is {failed_state} after a rollback")
        self.wait_serving(proven, timeout=60)
        files = self.state_files()
        expect(files["failed-image"] == bad, f"failed-image names {files['failed-image']!r}")
        expect(files["image"] == proven, f"the proven image is now {files['image']!r}")
        expect(not files["pending"], "a pending record is left after the rollback")
        expect(
            "rolled back" in log, f"the updater's log does not say it rolled back:\n{log[-600:]}"
        )
        _, before = self.container()
        again = self.update()
        _, after = self.container()
        expect(after == before, "the second update restarted cairn")
        expect(
            "deploying" not in again,
            f"the second update tried the failed image again:\n{again[-600:]}",
        )
        expect(self.hits() == 1, "the note is not there after the rollback")
        return (
            f"{self.short(bad)} never served; after {took:.0f}s cairn runs {self.short(proven)}"
            " through Caddy again, failed-image names the bad digest, cairn-update.service is"
            " failed, and a second update leaves it alone"
        )

    def settles_a_deployment_an_earlier_boot_left(self) -> str:
        proven = self.proven()
        candidate = self.push(self.build("good-3", GOOD_IMAGE), "good-3")
        # 1. A deployment of CANDIDATE that a reboot cut short: the boot step drops the record
        # and counts the interruption, and cairn comes back on the proven image.
        (STATE_DIR / "pending").write_text(f"{candidate}\nrehearsal-earlier-boot-1\n")
        _, before = self.container()
        self.systemctl("restart", "cairn-disk.service", timeout=400)
        self.wait_serving(proven, timeout=120)
        _, after = self.container()
        expect(
            after != before,
            "cairn was not restarted after the boot step (Requires= did not propagate)",
        )
        files = self.state_files()
        expect(not files["pending"], f"the boot step left the record: {files['pending']!r}")
        expect(
            files["interrupted"] == f"{candidate} rehearsal-earlier-boot-1",
            f"interrupted says {files['interrupted']!r}",
        )
        # ... and the updater gives it one more try.
        self.move_tag(candidate)
        self.update()
        self.wait_serving(candidate, timeout=30)
        files = self.state_files()
        expect(
            files["image"] == candidate and not files["interrupted"], f"after the retry: {files}"
        )
        # 2. The same candidate cut short a second time is marked failed and not tried again.
        earlier = proven
        (STATE_DIR / "interrupted").write_text(f"{earlier} rehearsal-earlier-boot-2\n")
        (STATE_DIR / "pending").write_text(f"{earlier}\nrehearsal-earlier-boot-3\n")
        self.host("boot-settle", timeout=60)
        files = self.state_files()
        expect(files["failed-image"] == earlier, f"after a second interruption: {files}")
        expect(not files["pending"] and not files["interrupted"], f"records left: {files}")
        self.move_tag(earlier)
        _, before = self.container()
        log = self.update()
        _, after = self.container()
        expect(after == before and "deploying" not in log, "the updater tried a failed candidate")
        # 3. A record that names the proven image: promote was cut between its two steps.
        (STATE_DIR / "pending").write_text(f"{candidate}\nrehearsal-earlier-boot-4\n")
        self.host("boot-settle", timeout=60)
        files = self.state_files()
        expect(
            files["image"] == candidate and not files["pending"] and not files["failed-image"],
            f"after a record of the proven image: {files}",
        )
        return (
            "no real reboot (the boot id cannot change here): a record from a made-up earlier boot"
            " is dropped by the boot step and counted once, cairn comes back on the proven image,"
            " the updater retries it once and promotes it; a second interruption marks it failed"
            " and the updater skips it; a record of the proven image is just removed"
        )

    def hold_pin_and_release(self) -> str:
        proven = self.proven()
        bad = self.refs["never-serves"]
        oldest = self.refs["first"]
        # hold stops an update in flight: start one on the bad image, hold inside its window.
        self.move_tag(bad)
        cursor = self.journal_mark()
        self.systemctl("start", "--no-block", "cairn-update.service")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and pending_record(read(STATE_DIR / "pending"))[0] != bad:
            time.sleep(1)
        expect(
            pending_record(read(STATE_DIR / "pending"))[0] == bad,
            "the update never recorded the bad image",
        )
        held = self.host("hold", timeout=120)
        # What the guide tells the operator to read: the record names the candidate with this
        # boot's id, so every start of cairn runs it, and hold's own "pending" line shows it.
        record = pending_record(read(STATE_DIR / "pending"))
        expect(
            record == (bad, self.boot_id()),
            f"after the hold, pending holds {record}, not the candidate with this boot's id",
        )
        printed = parse_state(held.stdout).get("pending")
        expect(
            printed == bad.split("@", 1)[1], f'`hold` printed "pending: {printed}", not the digest'
        )
        update_state = self.show("cairn-update.service", "ActiveState")
        expect(update_state != "activating", "hold left the update running")
        stopped = self.journal_since("cairn-update.service", cursor)
        expect(
            "deploying" in stopped and "now running" not in stopped and run_ended(stopped),
            f"the update in flight did not end before its verdict: {stopped[-600:]!r}",
        )
        timer = self.show("cairn-update.timer", "ActiveState")
        expect(timer != "active", f"the timer is {timer} while held")
        state = parse_state(held.stdout)
        expect(
            state.get("hold", "").startswith("held since"), f"`hold` printed {held.stdout[-400:]!r}"
        )
        # The killed updater may have left a restart of cairn queued; let it land first.
        self.settle_jobs()
        # What the interrupted deployment left while held: the record (asserted above) and the
        # container, which depends on whether the hold landed before or after the restart.
        before_files, (running, before) = self.state_files(), self.container()
        expect(pending_record(before_files["pending"]) == record, "the record changed while held")
        left = (
            f"pending names {self.short(bad)} with this boot's id, and hold printed it on its"
            f' "pending" line; cairn runs {self.short(running) or "nothing"}'
        )
        # While held, an update changes nothing.
        log = self.update()
        expect("on hold" in log, f"the held update logged:\n{log[-400:]}")
        expect(
            self.state_files() == before_files and self.container()[1] == before,
            "a held update changed something",
        )
        # pin: the image cairn starts with, not restarted now.
        self.host("pin", oldest.split("@", 1)[1], timeout=120)
        files = self.state_files()
        expect(files["image"] == oldest, f"after pin the proven image is {files['image']!r}")
        expect(not files["pending"] and not files["failed-image"], f"pin left records: {files}")
        expect(self.container()[1] == before, "pin restarted cairn")
        self.systemctl("restart", "cairn.service")
        self.wait_serving(oldest)
        # release refuses while the tag names another image; the hold stays.
        refused = self.host("release", check=False, timeout=120)
        expect(refused.returncode != 0, "release ended the hold while the tag names another image")
        expect(
            "Move the tag to the image cairn starts first" in refused.stderr,
            f"release said {refused.stderr[-300:]!r}",
        )
        expect((STATE_DIR / "hold").exists(), "a refused release removed the hold")
        expect(
            self.show("cairn-update.timer", "ActiveState") != "active",
            "a refused release started the timer",
        )
        self.move_tag(oldest)
        self.host("release", timeout=120)
        expect(not (STATE_DIR / "hold").exists(), "release left the hold")
        expect(
            self.show("cairn-update.timer", "ActiveState") == "active",
            "release did not start the timer",
        )
        state = parse_state(self.host("state").stdout)
        digest = oldest.split("@", 1)[1]
        expect(
            state.get("tag") == digest and state.get("proven") == digest and not state.get("hold"),
            f"`state` says {state}",
        )
        # hold with the timer running stops it; release --force ends the hold anyway, and the
        # timer then deploys the tag.
        self.host("hold", timeout=120)
        timer = self.show("cairn-update.timer", "ActiveState")
        expect(timer != "active", f"hold left the running timer {timer}")
        self.move_tag(proven)
        self.host("release", "--force", timeout=120)
        expect(
            self.show("cairn-update.timer", "ActiveState") == "active",
            "release --force did not start the timer",
        )
        started = time.monotonic()
        try:
            # The verdict, not just the candidate running: promote writes deploy/image last.
            deadline = started + 180
            while time.monotonic() < deadline:
                files = self.state_files()
                if files["image"] == proven and not files["pending"]:
                    break
                time.sleep(2)
            self.wait_serving(proven, timeout=30)
        finally:
            self.systemctl("stop", "cairn-update.timer", check=False)
        files = self.state_files()
        expect(
            files["image"] == proven and not files["pending"],
            f"3 minutes after release --force the timer had not promoted the tag: {files}",
        )
        return (
            f"hold stopped an update in flight ({left}); a held update changed nothing; pin"
            " made the oldest digest the one cairn starts, without restarting it; release refused"
            " while the tag named another image (hold and stopped timer kept), released once the"
            " tag matched; a second hold stopped the running timer; release --force released"
            " anyway and the timer deployed the tag"
            f" in {time.monotonic() - started:.0f}s (timer stopped again)"
        )

    def the_lost_database_runbook_as_written(self) -> str:
        """ "The database is lost or damaged", its bash block run from three starting states."""
        runbook = lost_database_runbook(read(GUIDE))
        words = [WORD]
        said = []

        # (a) Quiet: the proven image runs and the tag names it. release just ends the hold.
        proven = self.baseline()
        words.append("mossgarnet")
        self.wait_replicated(self.note(words[-1]))
        cursor = self.journal_mark()
        run = self.follow_runbook(runbook, runbook.middle)
        expect(not run.pinned, f"hold printed a pending deployment in a quiet state: {run.held}")
        expect(self.proven() == proven, f"the proven image changed to {self.proven()!r}")
        self.restored(proven, words, cursor)
        try:
            [(released, status)] = self.as_written([runbook.release])
            expect(status == 0, f"release refused in the quiet state: {released[-300:]!r}")
            expect(not (STATE_DIR / "hold").exists(), "release left the hold")
            timer = self.show("cairn-update.timer", "ActiveState")
            expect(timer == "active", f"after release the timer is {timer}")
        finally:
            self.stop_timer()
        said.append(
            f"(a) quiet: no pin, moved {'+'.join(run.moved)} to {run.aside}, restored,"
            f" {len(words)} notes found, release ended the hold (exit codes:"
            f" {', '.join(run.statuses)})"
        )

        # (b) The hold cuts a deployment short: the pending branch of the comment applies.
        slow = self.push(self.build("slow-1", SLOW_IMAGE), "slow-1")
        words.append("tidewhistle")
        self.wait_replicated(self.note(words[-1]))
        self.move_tag(slow)
        self.systemctl("start", "--no-block", "cairn-update.service")
        self.wait_for(
            lambda: first_line(STATE_DIR / "pending") == slow and self.container()[0] == slow,
            90,
            "the updater recorded and started the slow candidate",
        )
        cursor = self.journal_mark()
        run = self.follow_runbook(runbook, runbook.middle)
        expect(
            run.held.get("pending") == slow.split("@", 1)[1],
            f'hold printed "pending: {run.held.get("pending")}" while the candidate was judged',
        )
        expect(
            run.pinned == proven.split("@", 1)[1] == run.held.get("proven"),
            f"pinned {run.pinned!r}; the proven image was {proven}",
        )
        expect(self.proven() == proven, f"after the pin the proven image is {self.proven()!r}")
        self.restored(proven, words, cursor)
        refusal = self.refused_release(runbook)
        cursor = self.journal_mark()
        try:
            [(forced, status)] = self.as_written([runbook.forced_release])
            expect(status == 0, f"release --force exited {status}: {forced[-300:]!r}")
            expect(not (STATE_DIR / "hold").exists(), "release --force left the hold")
            self.wait_for(
                lambda: self.proven() == slow and not (STATE_DIR / "pending").exists(),
                180,
                "after release --force the updater promoted the candidate",
            )
            self.wait_serving(slow, timeout=60)
        finally:
            self.stop_timer()
        log = self.journal_since("cairn-update.service", cursor)
        expect("deploying" in log and "now running" in log, f"the updater logged:\n{log[-600:]}")
        said.append(
            f"(b) held mid-deployment: pending named {self.short(slow)}, pinned the proven"
            f" digest, restored on {self.short(proven)}, release refused ({refusal!r}),"
            " release --force: the candidate was tried again and promoted"
        )
        proven = slow

        # (c) After an automatic rollback: failed-image names the tag's image.
        bad = self.refs["never-serves"]
        self.move_tag(bad)
        self.update(succeeds=False)
        files = self.state_files()
        expect(files["failed-image"] == bad and files["image"] == proven, f"rollback: {files}")
        self.wait_serving(proven)
        words.append("emberlark")
        self.wait_replicated(self.note(words[-1]))
        cursor = self.journal_mark()
        run = self.follow_runbook(runbook, runbook.middle)
        expect(not run.pinned, f"hold printed a pending deployment after a rollback: {run.held}")
        expect(
            run.held.get("failed") == run.held.get("tag") == bad.split("@", 1)[1],
            f"hold printed {run.held}",
        )
        self.restored(proven, words, cursor)
        refusal = self.refused_release(runbook)
        _, started = self.container()
        cursor = self.journal_mark()
        try:
            [(forced, status)] = self.as_written([runbook.forced_release])
            expect(status == 0, f"release --force exited {status}: {forced[-300:]!r}")
            log = self.journal_since("cairn-update.service", cursor, timeout=100)
            expect(run_ended(log), f"no timer run ended within 100s of release --force: {log!r}")
        finally:
            self.stop_timer()
        expect("deploying" not in log, f"the updater tried the failed image again:\n{log[-600:]}")
        files = self.state_files()
        expect(
            files["image"] == proven and files["failed-image"] == bad and not files["pending"],
            f"after the timer's run: {files}",
        )
        expect(self.container()[1] == started, "the timer's run restarted cairn")
        said.append(
            f"(c) after a rollback: no pin, restored on {self.short(proven)}, release"
            f" refused ({refusal!r}), release --force: a timer run skipped the failed"
            " image and cairn stayed on the proven one"
        )
        # Back to a quiet state for the next check: the tag on the proven image, and the next
        # update forgets the failed record (README, Rollback).
        self.move_tag(proven)
        self.update()
        expect(not self.state_files()["failed-image"], "the failed record outlived the tag move")
        asides = sorted(self.asides())
        return (
            "; ".join(said) + f"; aside directories {', '.join(asides)}, none overwritten by a"
            " later run"
        )

    def settles_a_deployment_in_the_same_boot(self) -> str:
        """The updater killed in a candidate's health check; the next run gives the verdict."""
        proven = self.baseline()
        said = []
        slow = self.push(self.build("slow-2", SLOW_IMAGE), "slow-2")
        for candidate, serves in ((slow, True), (self.refs["never-serves"], False)):
            self.move_tag(candidate)
            self.systemctl("start", "--no-block", "cairn-update.service")
            self.wait_for(
                lambda c=candidate: (
                    first_line(STATE_DIR / "pending") == c and self.container()[0] == c
                ),
                90,
                f"the updater recorded and started {self.short(candidate)}",
            )
            self.systemctl("kill", "--signal=SIGKILL", "cairn-update.service")
            self.wait_for(
                lambda: self.show("cairn-update.service", "ActiveState") != "activating",
                30,
                "the killed updater's unit stopped",
            )
            files = self.state_files()
            expect(
                pending_record(files["pending"]) == (candidate, self.boot_id())
                and files["image"] == proven,
                f"the kill did not land inside the health check: {files}",
            )
            log = self.update(succeeds=serves)
            expect("without a verdict" in log, f"the next run did not settle it:\n{log[-600:]}")
            files = self.state_files()
            expect(not files["pending"], f"a pending record is left: {files}")
            if serves:
                expect(files["image"] == candidate, f"the candidate was not promoted: {files}")
                proven = candidate
                said.append(f"{self.short(candidate)} promoted")
            else:
                expect(
                    files["image"] == proven and files["failed-image"] == candidate,
                    f"the candidate was not rolled back: {files}",
                )
                said.append(f"{self.short(candidate)} rolled back and recorded as failed")
            self.wait_serving(proven)
        self.move_tag(proven)
        self.update()
        expect(not self.state_files()["failed-image"], "the failed record outlived the tag move")
        return (
            "the updater was killed (SIGKILL) inside a candidate's health check, leaving a record"
            " with this boot's id; the next run settled it in the same boot: "
            + ", ".join(said)
            + "; no pending record either time"
        )

    def starts_no_container_without_the_metadata_block(self) -> str:
        """README, "Closed on failure"."""
        proven = self.baseline()

        def rules() -> list[str]:
            return sorted(metadata_drop_rules(out("iptables", "-w", "-S", "DOCKER-USER")))

        # 1. Missing rules are put back before cairn starts.
        for protocol in ("tcp", "udp"):
            sh(
                "iptables", "-w", "-D", "DOCKER-USER", "!", "-i", NETWORKS["cairn-backup"],
                "-d", METADATA_IP, "-p", protocol, "!", "--dport", "53", "-j", "DROP",
            )  # fmt: skip
        expect(rules() == [], f"the rules are still there after deleting them: {rules()}")
        self.systemctl("restart", "cairn.service")
        self.wait_serving(proven)
        expect(rules() == ["tcp", "udp"], f"run-cairn left these rules: {rules()}")
        # 2. Rules that cannot be put back: DOCKER-USER renamed, so neither -C nor -I finds it.
        # The jump from FORWARD and the rules move with the chain, so the block stays in force.
        self.systemctl("stop", "cairn.service", "caddy.service")
        sh("iptables", "-w", "-E", "DOCKER-USER", RENAMED_CHAIN)
        try:
            refused = {
                name: self.host(f"run-{name}", check=False, timeout=60)
                for name in ("cairn", "caddy")
            }
            containers = sh("docker", "ps", "-a", "--format", "{{.Names}}").stdout.split()
            log = self.update()
        finally:
            sh("iptables", "-w", "-E", RENAMED_CHAIN, "DOCKER-USER", check=False)
        for name, words in (("cairn", "not starting cairn"), ("caddy", "not starting Caddy")):
            result = refused[name]
            expect(
                result.returncode != 0 and words in result.stderr,
                f"run-{name} without the chain exited {result.returncode}:"
                f" {result.stderr[-300:]!r}",
            )
            expect(name not in containers, f"a {name} container started without the block")
        expect(
            "the metadata server is not blocked for containers" in log,
            f"the updater did not log the missing block:\n{log[-600:]}",
        )
        expect(rules() == ["tcp", "udp"], f"after the rename back: {rules()}")
        self.systemctl("start", "cairn.service", "caddy.service")
        self.wait_serving(proven)
        return (
            "with both rules deleted, a restart of cairn put them back (one pair) and it served;"
            " with DOCKER-USER renamed so no rule can be inserted, run-cairn and run-caddy refused"
            " and no container started, and the updater logged the missing block; renamed back,"
            " both started again"
        )

    def restores_to_a_point_in_time_as_the_guide_says(self) -> str:
        """ "Restore to a point in time": both looks while cairn is up, then the restore."""
        proven = self.baseline()
        runbook = lost_database_runbook(read(GUIDE))
        look, restore = point_in_time(read(GUIDE))
        before, after = "copperheron", "velvetmarten"
        self.wait_replicated(self.note(before))
        moment = int(time.time()) + 2
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment))
        time.sleep(max(0.0, moment + 2 - time.time()))
        written = self.note(after)
        self.wait_replicated(written)  # a replica file newer than the second note, so than T
        _, started = self.container()
        looks = [with_timestamp(c, stamp) if "-timestamp" in c else c for c in look]
        results = self.as_written(looks, pipefail=True)
        for command, (said, status) in zip(looks, results, strict=True):
            expect(status == 0, f"{command!r} exited {status}: {said[-400:]!r}")
        listing, plan = results[0][0], results[1][0]
        expect(bool(listing.strip()), "`ltx -level all` listed nothing")
        expect(not (DB_DIR / "dry-run.db").exists(), "the dry run wrote /data/dry-run.db")
        expect(self.container()[1] == started, "cairn was restarted during the looks")
        run = self.follow_runbook(runbook, through_the_move(runbook.middle))
        expect(not run.pinned, f"hold printed a pending deployment: {run.held}")
        [(said, status)] = self.as_written([with_timestamp(restore, stamp)])
        expect(status == 0, f"the restore exited {status}: {said[-400:]!r}")
        expect((DB_DIR / "cairn.db").exists(), "the restore wrote no cairn.db")
        # Stand-in for the new replica_path and the VM restart: the old replica moves away.
        shutil.move(str(DB_DIR / "replica"), DATA_MOUNT / "rehearsal-old-replica")
        self.systemctl("start", "cairn.service", "litestream.service")
        self.wait_serving(proven)
        expect(self.hits(before) == 1, f"the note written before {stamp} is not there")
        expect(self.hits(after) == 0, f"the note written after {stamp} is there")
        try:
            [(released, status)] = self.as_written([runbook.release])
            expect(status == 0, f"release refused: {released[-300:]!r}")
        finally:
            self.stop_timer()
        last = plan.strip().splitlines()[-1][:160] if plan.strip() else "nothing"
        return (
            f"`ltx -level all` listed {len(listing.splitlines())} lines and the dry run exited 0"
            f" (its last line: {last!r}), no dry-run.db written and cairn not restarted; with the"
            f" files moved to {run.aside}, `restore -timestamp {stamp}` brought back the note"
            " written before T and not the one written after; the new replica path is a stand-in"
            " (the old replica directory moved away, no tofu apply, no VM restart)"
        )

    def runs_with_the_plans_file_it_is_given(self) -> str:
        """cairn_plans_file. Without it (as installed, and as every check before ran) the file
        is empty and cairn runs without --plans; with it, cairn reads the file and is held to
        it. The stand-in for `tofu apply` and a VM restart: the template rendered again with
        a plans file, its one changed file written as cloud-init would, cairn restarted."""
        proven = self.baseline()
        installed = {f.path: f for f in self.files}
        empty = installed[PLANS_PATH]

        def command() -> list[str]:
            return json.loads(out("docker", "inspect", "-f", "{{json .Args}}", "cairn"))

        def mounted() -> list[tuple[str, bool]]:
            mounts = json.loads(out("docker", "inspect", "-f", "{{json .Mounts}}", "cairn"))
            return mounts_at(mounts, PLANS_PATH)

        expect(empty.content == "", f"write_files gives {PLANS_PATH} {empty.content!r}")
        expect(read(Path(PLANS_PATH)) == "", f"{PLANS_PATH} is not empty without a plans file")
        before = command()
        expect(plans_argument(before) is None, f"without a plans file cairn runs with {before}")
        expect(mounted() == [], f"without a plans file {PLANS_PATH} is mounted: {mounted()}")
        values = {**rehearsal_values(REPO), "plans_yaml": PLANS_TEXT}
        rendered = render_template(TEMPLATE.read_text(encoding="utf-8"), values)
        given = {f.path: f for f in write_files(rendered)}
        changed = sorted(p for p in set(given) | set(installed) if given.get(p) != installed.get(p))
        expect(changed == [PLANS_PATH], f"a plans file changed these files: {changed}")
        try:
            install(given[PLANS_PATH])
            self.systemctl("restart", "cairn.service")
            self.wait_serving(proven)
            args = command()
            expect(plans_argument(args) == PLANS_PATH, f"with a plans file cairn runs with {args}")
            expect(mounted() == [(PLANS_PATH, False)], f"{PLANS_PATH} is mounted as {mounted()}")
            seen = json.loads(out("docker", "exec", "cairn", "python", "-c", PLANS_SEEN))
            wanted = {
                "sha256": hashlib.sha256(PLANS_TEXT.encode("utf-8")).hexdigest(),
                "uid": 10001,
                "readable": True,
                "writable": False,
            }
            expect(seen == wanted, f"inside the container the plans file is {seen}")
            status, refused = self.api("POST", "/api/projects", {"name": OVER_CAP_PROJECT})
            limit = refused.get("limit") if isinstance(refused, dict) else None
            expect(
                status == 402 and limit == "projects",
                f"a project past the plan's cap answered {status} (limit {limit!r})",
            )
        finally:
            install(empty)
            self.systemctl("restart", "cairn.service")
            self.wait_serving(proven)
        after = command()
        expect(
            plans_argument(after) is None and mounted() == [],
            f"with the empty file back cairn runs with {after}",
        )
        return (
            f"without a plans file {PLANS_PATH} is empty and cairn runs with no --plans and no"
            " mount; with one, only that file of write_files changes, cairn runs with --plans"
            f" {PLANS_PATH} mounted read-only, uid 10001 reads its bytes (sha256 matches) and"
            " cannot write it, and a second project through Caddy is refused 402 at the plan's"
            " cap; with the empty file back, no --plans again"
        )

    def runs_under_the_brand_it_is_given(self) -> str:
        """cairn_brand_*. Without them (as installed) nothing is under /etc/cairn/brand and
        cairn runs with no --brand-* option. With a name holding a space, a PNG logo and an alt
        text rendered into the template (and sign-in on, which serve needs for a brand), cairn
        runs with each as one argument, exactly, and reads the logo as its own user. The
        stand-in for `tofu apply` and a VM restart: the files that change written as cloud-init
        would, cairn restarted. It needs a cairn whose serve knows the --brand-* options."""
        proven = self.baseline()
        installed = {f.path: f for f in self.files}
        logo = brand_logo()
        brand = [BRAND_NAME, BRAND_LOGO, BRAND_ALT]

        def command() -> list[str]:
            return json.loads(out("docker", "inspect", "-f", "{{json .Args}}", "cairn"))

        def mounted() -> list[tuple[str, bool]]:
            mounts = json.loads(out("docker", "inspect", "-f", "{{json .Mounts}}", "cairn"))
            return mounts_at(mounts, BRAND_LOGO)

        def branded(args: list[str]) -> list[str]:
            return [arg for arg in args if arg.startswith("--brand")]

        expect(not Path(BRAND_NAME).parent.exists(), "/etc/cairn/brand exists without a brand")
        before = command()
        expect(not branded(before), f"without a brand cairn runs with {before}")
        expect(mounted() == [], f"without a brand {BRAND_LOGO} is mounted: {mounted()}")
        values = {
            **rehearsal_values(REPO),
            "extra_args": BRAND_EXTRA_ARGS,
            "brand_files": brand_files(BRAND_NAME_TEXT, logo, BRAND_ALT_TEXT),
        }
        given = {
            f.path: f
            for f in write_files(render_template(TEMPLATE.read_text(encoding="utf-8"), values))
        }
        changed = sorted(p for p in set(given) | set(installed) if given.get(p) != installed.get(p))
        expect(changed == sorted([CONFIG_ENV, *brand]), f"the brand changed these files: {changed}")
        env = ENV_FILE.read_bytes()
        try:
            for path in (CONFIG_ENV, *brand):
                install(given[path])
            with open(ENV_FILE, "ab") as handle:  # keeps the file's mode, 0600
                handle.write(BRAND_ENV.encode("utf-8"))
            self.systemctl("restart", "cairn.service")
            self.wait_serving(proven)
            args = command()
            expect(
                f"--brand-name={BRAND_NAME_TEXT}" in args
                and option_values(args, "--brand-name") == [BRAND_NAME_TEXT],
                f"the name is not one argument: {branded(args)}",
            )
            expect(
                f"--brand-logo-alt={BRAND_ALT_TEXT}" in args
                and option_values(args, "--brand-logo-alt") == [BRAND_ALT_TEXT],
                f"the alt text is not one argument: {branded(args)}",
            )
            expect(
                option_values(args, "--brand-logo") == [BRAND_LOGO],
                f"cairn runs with {branded(args)}",
            )
            expect(mounted() == [(BRAND_LOGO, False)], f"{BRAND_LOGO} is mounted as {mounted()}")
            seen = json.loads(
                out("docker", "exec", "cairn", "python", "-c", seen_script(BRAND_LOGO))
            )
            wanted = {
                "sha256": hashlib.sha256(logo).hexdigest(),
                "uid": 10001,
                "readable": True,
                "writable": False,
            }
            expect(seen == wanted, f"inside the container the logo is {seen}")
        finally:
            install(installed[CONFIG_ENV])
            shutil.rmtree(Path(BRAND_NAME).parent, ignore_errors=True)
            with open(ENV_FILE, "wb") as handle:
                handle.write(env)
            self.systemctl("restart", "cairn.service")
            self.wait_serving(proven)
        after = command()
        expect(
            not branded(after) and mounted() == [],
            f"with the brand gone cairn runs with {branded(after)}",
        )
        return (
            "without a brand nothing is under /etc/cairn/brand and cairn runs with no --brand-*;"
            f" with one, only config.env (for {BRAND_EXTRA_ARGS}) and the three brand files of"
            f" write_files change, cairn runs with --brand-name={BRAND_NAME_TEXT!r} and"
            f" --brand-logo-alt={BRAND_ALT_TEXT!r} each as one argument and --brand-logo"
            f" {BRAND_LOGO} mounted read-only, and uid 10001 reads the PNG's bytes (sha256"
            " matches) and cannot write it; with the brand gone, no --brand-* again"
        )

    def a_first_deployment_that_fails_leaves_nothing_proven(self) -> str:
        """A blank disk, the tag on an image that never serves. Last: the disk is replaced."""
        bad = self.refs["never-serves"]
        runbook = lost_database_runbook(read(GUIDE))
        self.stop_timer()
        for unit in STOP_ORDER:
            self.systemctl("stop", unit, check=False, timeout=120)
        sh("umount", str(DATA_MOUNT))
        blank = self.loop("blank-again", 1 << 30)
        self.point_device_at(blank)
        self.move_tag(bad)
        self.systemctl("start", *self.boot_units, timeout=400)
        entry = self.mounted()
        expect(entry is not None and entry[0] == blank, f"{DATA_MOUNT} holds {entry}")
        expect(not (STATE_DIR / "image").exists(), "the blank disk names a proven image")
        skipped = self.show("cairn.service", "ConditionResult")
        expect(skipped == "no", f"cairn.service started on a blank disk ({skipped})")
        log = self.update(succeeds=False)
        expect(
            "there is no proven image, cairn is stopped" in log,
            f"the updater logged:\n{log[-600:]}",
        )
        files = self.state_files()
        expect(
            files["failed-image"] == bad and not files["image"] and not files["pending"],
            f"after the failed first deployment: {files}",
        )
        expect(self.container() == ("", ""), "a cairn container runs")
        stopped = self.show("cairn.service", "ActiveState")
        expect(stopped in ("inactive", "failed"), f"cairn.service is {stopped}")
        digest = bad.split("@", 1)[1]
        state = parse_state(self.host("state").stdout)
        expect(
            state.get("proven") == "none" and state.get("failed") == state.get("tag") == digest,
            f"`state` says {state}",
        )
        [(said, status)] = self.as_written([runbook.hold])
        printed = parse_state(said).get("proven")
        expect(status == 0 and printed == "none", f'the block\'s hold printed "proven: {printed}"')
        self.systemctl("start", "cairn.service")
        skipped = self.show("cairn.service", "ConditionResult")
        expect(skipped == "no", f"cairn.service was not skipped by its conditions ({skipped})")
        expect(self.container() == ("", ""), "systemctl start cairn.service started a container")
        return (
            f"on a blank disk {self.short(bad)} failed its first deployment: cairn stopped,"
            " failed-image names it, no image and no pending record, `state` says proven none,"
            ' the block\'s hold prints "proven: none" (where the block says stop), and'
            " `systemctl start cairn.service` is skipped by its conditions"
        )

    def cleanup(self) -> str:
        gone = sorted(self.images_before - local_images())
        if gone:
            print(f"# images present before the rehearsal and gone now: {gone}", flush=True)
        units = [u for u in STOP_ORDER if u in self.units]
        if self.installed:
            for unit in units:
                self.systemctl("stop", unit, check=False, timeout=120)
        for name in (*CONTAINERS, REGISTRY_CONTAINER):
            sh("docker", "rm", "-f", "-v", name, check=False, timeout=120)
        for network in NETWORKS:
            sh("docker", "network", "rm", network, check=False)
        if sh("iptables", "-w", "-S", RENAMED_CHAIN, check=False).returncode == 0:
            sh("iptables", "-w", "-E", RENAMED_CHAIN, "DOCKER-USER", check=False)
        for _ in range(10):
            listing = sh("iptables", "-w", "-S", "DOCKER-USER", check=False).stdout
            if not metadata_drop_rules(listing):
                break
            for protocol in ("tcp", "udp"):
                sh(
                    "iptables", "-w", "-D", "DOCKER-USER", "!", "-i", NETWORKS["cairn-backup"],
                    "-d", METADATA_IP, "-p", protocol, "!", "--dport", "53", "-j", "DROP",
                    check=False,
                )  # fmt: skip
        for _ in range(3):
            if self.mounted() is None:
                break
            sh("umount", str(DATA_MOUNT), check=False)
        if self.mounted() is None and DATA_MOUNT.exists():
            try:
                DATA_MOUNT.rmdir()
                if self.made_mnt_disks:
                    Path("/mnt/disks").rmdir()
            except OSError as error:
                print(f"cleanup: {error}", flush=True)
        for device, _ in self.loops:
            sh("losetup", "-d", device, check=False)
        if self.installed:
            for f in self.files:
                Path(f.path).unlink(missing_ok=True)
            shutil.rmtree("/etc/cairn", ignore_errors=True)
            shutil.rmtree(ENV_DROP_IN.parent, ignore_errors=True)
            STUB.unlink(missing_ok=True)
            shutil.rmtree(WORK, ignore_errors=True)
            if self.made_env_dir:
                shutil.rmtree(ENV_DIR, ignore_errors=True)
            if self.made_home:
                shutil.rmtree("/home/cairnhost", ignore_errors=True)
            sh("systemctl", "daemon-reload", check=False)
            sh("systemctl", "reset-failed", *units, check=False)
        listing = sh(
            "docker", "images", "--no-trunc", "--format", "{{.Repository}} {{.ID}}", check=False
        ).stdout.splitlines()
        images = [
            line.split()[1]
            for line in listing
            if len(line.split()) == 2 and line.split()[0] in (CAIRN_IMAGE, "cairn-rehearsal")
        ]
        if images:
            sh("docker", "rmi", "-f", *sorted(set(images)), check=False)
        # What is left.
        left = []
        for unit in self.units:
            state = sh("systemctl", "show", "-p", "LoadState", "--value", unit, check=False)
            if state.stdout.strip() != "not-found":
                left.append(f"unit {unit} ({state.stdout.strip()})")
        made = ["/etc/cairn", str(ENV_DROP_IN.parent), str(STUB), str(WORK), str(DATA_MOUNT)]
        made += [str(ENV_DIR)] if self.made_env_dir else []
        made += ["/home/cairnhost"] if self.made_home else []
        made += ["/mnt/disks"] if self.made_mnt_disks else []
        left += [p for p in made if Path(p).exists()]
        names = sh("docker", "images", "--format", "{{.Repository}}", check=False).stdout.split()
        left += [f"image {n}" for n in names if n in (CAIRN_IMAGE, "cairn-rehearsal")]
        containers = sh("docker", "ps", "-a", "--format", "{{.Names}}", check=False).stdout.split()
        left += [f"container {c}" for c in containers if c in (*CONTAINERS, REGISTRY_CONTAINER)]
        networks = sh(
            "docker", "network", "ls", "--format", "{{.Name}}", check=False
        ).stdout.split()
        left += [f"network {n}" for n in networks if n in NETWORKS]
        rules = metadata_drop_rules(sh("iptables", "-w", "-S", "DOCKER-USER", check=False).stdout)
        left += [f"iptables rule ({p})" for p in rules]
        if sh("iptables", "-w", "-S", RENAMED_CHAIN, check=False).returncode == 0:
            left.append(f"iptables chain {RENAMED_CHAIN} (DOCKER-USER renamed)")
        if self.mounted() is not None:
            left.append(f"mount at {DATA_MOUNT}")
        for _, backing in self.loops:
            if sh("losetup", "-j", str(backing), check=False).stdout.strip():
                left.append(f"loop device over {backing}")
        shutil.rmtree(self.tmp, ignore_errors=True)
        expect(not left, f"left behind: {', '.join(left)}")
        return (
            "no unit, /etc/cairn, stand-in, directory it made, image it built, container,"
            " network, iptables rule, mount or loop device left"
        )


def _guarded(check: Callable[[], str], rehearsal: Rehearsal) -> Callable[[], str]:
    """The check, with diagnostics printed the moment it fails and any error made a Failed."""

    @functools.wraps(check)
    def guarded() -> str:
        try:
            return check()
        except Exception as error:
            try:
                rehearsal.diagnostics(check.__name__)
            except Exception as also:  # diagnostics must never hide the failure itself
                print(f"(diagnostics failed: {also!r})", flush=True)
            if isinstance(error, Failed):
                raise
            raise Failed(f"{type(error).__name__}: {error}") from error

    return guarded


def already_there(files: list[WriteFile]) -> list[str]:
    found = [str(p) for p in (Path("/etc/cairn"), WORK, ENV_DROP_IN.parent, STUB) if p.exists()]
    found += [f.path for f in files if Path(f.path).exists()]
    if mount_entry(Path("/proc/mounts").read_text(), str(DATA_MOUNT)) is not None:
        found.append(f"a mount at {DATA_MOUNT}")
    names = sh("docker", "ps", "-a", "--format", "{{.Names}}", check=False).stdout.split()
    found += [f"container {n}" for n in names if n in (*CONTAINERS, REGISTRY_CONTAINER)]
    networks = sh("docker", "network", "ls", "--format", "{{.Name}}", check=False).stdout.split()
    found += [f"network {n}" for n in networks if n in NETWORKS]
    return found


def versions() -> None:
    for command in (
        ("uname", "-r"),
        ("docker", "version", "-f", "docker {{.Server.Version}} ({{.Server.Os}}/{{.Server.Arch}})"),
        ("docker", "info", "-f", "storage driver {{.Driver}}, cgroup {{.CgroupVersion}}"),
        ("iptables", "--version"),
        ("systemctl", "--version"),
        ("sh", "-c", "echo gcr-online.target: $(systemctl show -p LoadState gcr-online.target)"),
    ):
        result = sh(*command, check=False, timeout=30)
        said = (result.stdout or result.stderr).strip().splitlines()
        print(f"# {said[0] if said else ' '.join(command) + ': no output'}", flush=True)


def main(argv: list[str]) -> int:
    args = argv[1:]
    early = refusal(args, is_root=os.geteuid() == 0, already_there=[])
    if early:
        print(early, file=sys.stderr)
        return 2
    try:
        rendered = render_template(TEMPLATE.read_text(encoding="utf-8"), rehearsal_values(REPO))
        files = write_files(rendered)
    except Failed as failure:
        print(f"cannot read the template: {failure}", file=sys.stderr)
        return 2
    problem = refusal(args, is_root=True, already_there=already_there(files))
    if problem:
        print(problem, file=sys.stderr)
        return 2
    versions()
    rehearsal = Rehearsal(image_argument(args), files, rendered)
    checks = [
        rehearsal.installs_the_template_as_shipped,
        rehearsal.formats_a_blank_disk,
        rehearsal.never_formats_a_disk_that_is_not_blank,
        rehearsal.blocks_the_metadata_server_for_cairns_network,
        rehearsal.serves_cairn_from_the_real_units,
        rehearsal.restores_the_database_before_cairn_starts,
        rehearsal.tells_a_current_replica_from_one_that_stopped,
        rehearsal.compacts_with_the_replica_paused,
        rehearsal.follows_the_deploy_tag,
        rehearsal.rolls_back_an_unhealthy_image,
        rehearsal.settles_a_deployment_an_earlier_boot_left,
        rehearsal.hold_pin_and_release,
        rehearsal.the_lost_database_runbook_as_written,
        rehearsal.settles_a_deployment_in_the_same_boot,
        rehearsal.starts_no_container_without_the_metadata_block,
        rehearsal.restores_to_a_point_in_time_as_the_guide_says,
        rehearsal.runs_with_the_plans_file_it_is_given,
        rehearsal.runs_under_the_brand_it_is_given,
        rehearsal.a_first_deployment_that_fails_leaves_nothing_proven,
    ]
    passed = 0
    try:
        passed = stack.run_checks([_guarded(check, rehearsal) for check in checks])
    finally:
        try:
            print(f"PASS cleanup: {rehearsal.cleanup()}", flush=True)
        except Exception as failure:
            print(f"FAIL cleanup: {failure}", flush=True)
            passed = -1
    print(f"{max(passed, 0)}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
