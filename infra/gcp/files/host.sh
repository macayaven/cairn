#!/bin/bash
# cairn host helper for Container-Optimized OS.
#
# cloud-init writes this file to /etc/cairn/host.sh on every boot (COS keeps /etc on tmpfs) and
# the systemd units next to it call `bash /etc/cairn/host.sh COMMAND`. It needs only what COS
# ships: bash, coreutils, sed, curl, iptables, docker and docker-credential-gcr. No jq, no python.
#
#   disk            wait for the data disk, format it only if it is provably blank, mount it
#   boot-settle     drop the record of a deployment that an earlier boot left unfinished
#   network         create the two Docker networks; block the metadata server for containers
#   env             fetch the env-file secret into tmpfs (blocks until it succeeds)
#   restore         restore the database from the replica if it is missing (blocks likewise)
#   run-cairn       exec the cairn container in the foreground
#   run-caddy       exec the Caddy container in the foreground
#   run-litestream  exec the Litestream container in the foreground
#   litestream ARGS run one Litestream command with the replica's settings (for the runbooks)
#   replica-check   say how old the newest copy in the replica is, in Litestream's own log
#                   (cairn-replica-check.timer, every 5 minutes; the replica alerts read it)
#   compact-if-due  compact, when a deletion waits for one (cairn-compact.timer, nightly)
#   host-check      disks, memory kills, restart loops, certificates (cairn-host-check.timer)
#   compact         by hand, after a workspace is deleted: check that cairn answers and the
#                   replica is current, stop Litestream, run cairn's compact, start Litestream
#                   again whatever happened, and wait until the replica has copied it
#   update          finish or undo an unfinished deployment, then follow the deploy tag; on
#                   every exit, publish the deployment status as the guest attribute
#                   cairn/status (the deploy job reads it, scripts/deploy_production.sh)
#   hold            stop following the tag, now and after every reboot, until `release`;
#                   stops the timer and an update in flight (for restores and rollbacks)
#   pin DIGEST      while on hold: make DIGEST (sha256:...) the image cairn starts with
#   release         end the hold, but only if the tag names the pinned image (or --force)
#   state           print the deployment state, the tag's digest and the hold
#
# Settings come from /etc/cairn/config.env, rendered by OpenTofu. Nothing in it is secret.

set -euo pipefail

# shellcheck disable=SC1091
. /etc/cairn/config.env

# iptables, blkid and the ext4 tools live in sbin, which a unit's PATH may not include.
export PATH="$PATH:/usr/sbin:/sbin"

# docker-credential-gcr writes $HOME/.docker/config.json and root's own home is read-only.
export HOME=/home/cairnhost

DATA_MOUNT=/mnt/disks/data
DB_DIR=$DATA_MOUNT/cairn
CADDY_DIR=$DATA_MOUNT/caddy
ENV_DIR=/run/cairn
ENV_FILE=$ENV_DIR/cairn.env
# cloud-init writes it from OpenTofu's cairn_plans_file on every boot, empty when that is unset.
PLANS_FILE=/etc/cairn/plans.yaml
# cloud-init writes the brand here from OpenTofu's cairn_brand_* on every boot: name, logo and
# logo-alt, one file for each value that is set and none for the others.
BRAND_DIR=/etc/cairn/brand
METADATA=http://metadata.google.internal/computeMetadata/v1

# Deployment state. It lives on the data disk, so it survives a replaced VM. Every image in
# it is "$CAIRN_IMAGE@sha256:...": the updater resolves the deploy tag and runs the digest.
#   image         the last image that passed its health check. A boot starts this one.
#   pending       a deployment without a verdict: the candidate, then the id of the boot that
#                 started it. Written before cairn is switched, removed by the verdict.
#   failed-image  the last candidate that failed its health check; skipped until the tag moves.
#   interrupted   a candidate whose deployment a reboot cut short; it gets one more try.
#   hold          present while a person holds deployments (`hold` to `release`); the
#                 updater then changes nothing, in this boot and after reboots.
STATE_DIR=$DATA_MOUNT/deploy
IMAGE_FILE=$STATE_DIR/image
PENDING_FILE=$STATE_DIR/pending
FAILED_FILE=$STATE_DIR/failed-image
INTERRUPTED_FILE=$STATE_DIR/interrupted
HOLD_FILE=$STATE_DIR/hold
STATUS_SEQ_FILE=$STATE_DIR/status-seq

# Two bridges. cairn and Caddy share the first and cannot reach the metadata server; Litestream
# is alone on the second, which can, because that is where its storage credentials come from.
NETWORK=cairn
BRIDGE=br-cairn
BACKUP_NETWORK=cairn-backup
BACKUP_BRIDGE=br-cairn-bkp
METADATA_IP=169.254.169.254 # metadata.google.internal resolves to this address
CAIRN_UID=10001
HEALTH_SECONDS=60
LOG_OPTS="--log-driver json-file --log-opt max-size=10m --log-opt max-file=5"
# What deploy/compose.yaml gives cairn and Caddy and compose.backup.yaml gives Litestream: no
# Linux capability, and none gained by exec. Caddy alone gets one back (CADDY_CAPS), to bind
# ports 80 and 443. Every container this script starts gets them.
HARDENING="--cap-drop ALL --security-opt no-new-privileges:true"
CADDY_CAPS="--cap-add NET_BIND_SERVICE"

log() { echo "cairn-host: $*"; }
# "<3>" makes journald record the line at priority err, which the COS logging agent exports.
# A person at a terminal (hold, pin, release) gets the line without it.
err() {
  if [ -t 2 ]; then
    echo "cairn-host: ERROR: $*" >&2
  else
    echo "<3>cairn-host: ERROR: $*" >&2
  fi
}
die() {
  err "$*"
  exit 1
}

is_count() {
  case "$1" in
    '' | *[!0-9]*) return 1 ;;
  esac
  return 0
}

# limits NAME: the memory limit (with no swap on top of it) and the process limit of the
# long-running container NAME, from config.env (OpenTofu's container_limits), so that one
# container cannot take the VM down. Without a whole number above 0 the container does not
# start: `limits` fails, and its caller stops.
limits() {
  local prefix memory pids
  case "$1" in
    cairn) prefix=CAIRN memory=${CAIRN_MEMORY_MB:-} pids=${CAIRN_PIDS_LIMIT:-} ;;
    caddy) prefix=CADDY memory=${CADDY_MEMORY_MB:-} pids=${CADDY_PIDS_LIMIT:-} ;;
    litestream) prefix=LITESTREAM memory=${LITESTREAM_MEMORY_MB:-} pids=${LITESTREAM_PIDS_LIMIT:-} ;;
    *) return 1 ;;
  esac
  if ! is_count "$memory" || [ "$memory" -eq 0 ]; then
    err "${prefix}_MEMORY_MB is not a whole number above 0 in config.env"
    return 1
  fi
  if ! is_count "$pids" || [ "$pids" -eq 0 ]; then
    err "${prefix}_PIDS_LIMIT is not a whole number above 0 in config.env"
    return 1
  fi
  printf -- '--memory %sm --memory-swap %sm --pids-limit %s' "$memory" "$memory" "$pids"
}

# --- disk -----------------------------------------------------------------------------------

# /proc/mounts instead of mountpoint(1): one less tool to depend on.
is_mounted() { grep -q " $DATA_MOUNT ext4 " /proc/mounts; }

cmd_disk() {
  local dev=$DATA_DEVICE fstype rc nonzero
  for _ in $(seq 1 60); do
    [ -b "$dev" ] && break
    sleep 1
  done
  [ -b "$dev" ] || die "data disk $dev did not appear after 60 s"

  if ! is_mounted; then
    # Format only when two independent checks agree the disk is blank: blkid's low-level probe
    # finds nothing (exit status 2) and the first 4 MiB are all zero bytes. Anything else,
    # including a blkid error, stops here with the disk untouched.
    rc=0
    fstype=$(blkid -p -o value -s TYPE "$dev") || rc=$?
    if [ "$rc" -eq 2 ] && [ -z "$fstype" ]; then
      nonzero=$(head -c 4194304 "$dev" | tr -d '\000' | wc -c)
      [ "$nonzero" -eq 0 ] || die "$dev has no known file system but is not blank; not formatting"
      log "formatting blank data disk $dev as ext4"
      mkfs.ext4 -q -F -m 0 -L cairn-data \
        -E lazy_itable_init=0,lazy_journal_init=0,discard "$dev" </dev/null
    elif [ "$rc" -ne 0 ]; then
      die "blkid exited $rc on $dev; not touching the disk"
    elif [ "$fstype" != ext4 ]; then
      die "$dev holds a '$fstype' file system, expected ext4; not touching the disk"
    fi
    rc=0
    fsck.ext4 -p "$dev" || rc=$?
    [ "$rc" -le 1 ] || die "fsck.ext4 exited $rc on $dev; repair it by hand before mounting"
    mkdir -p "$DATA_MOUNT"
    mount -t ext4 -o discard,nodev,nosuid,noexec "$dev" "$DATA_MOUNT"
  fi

  # Grows the file system after the disk was resized; does nothing otherwise.
  resize2fs "$dev" >/dev/null 2>&1 || log "resize2fs skipped"

  mkdir -p "$DB_DIR" "$CADDY_DIR/data" "$CADDY_DIR/config" "$STATE_DIR"
  chown "$CAIRN_UID:$CAIRN_UID" "$DB_DIR"
  chmod 0700 "$DB_DIR" "$CADDY_DIR" "$STATE_DIR"
  log "data disk mounted at $DATA_MOUNT"
}

# --- network --------------------------------------------------------------------------------

# A network with a fixed bridge name, so the firewall rules can name the interface.
ensure_network() {
  local name=$1 bridge=$2 have
  if docker network inspect "$name" >/dev/null 2>&1; then
    have=$(docker network inspect -f '{{index .Options "com.docker.network.bridge.name"}}' "$name")
    [ "$have" = "$bridge" ] ||
      die "Docker network $name is not on bridge $bridge; run 'docker network rm $name' and restart the VM"
  else
    docker network create -o "com.docker.network.bridge.name=$bridge" "$name" >/dev/null
  fi
}

# Containers reach the metadata server through the host's FORWARD chain, where Docker hands
# every packet to DOCKER-USER first. Two rules there drop whatever a container sends to the
# metadata address over TCP or UDP, with two exceptions: anything that comes in from
# Litestream's bridge, and DNS (port 53), because the VM's resolver is that same address.
# The host itself is not forwarded and is not affected.
#
# Each rule stands alone: there is no order between them and nothing is ever flushed, so
# callers that overlap (a unit starting while the updater runs) cannot leave a broken set. At
# worst a rule is inserted twice. Nothing here survives a reboot, and a Docker restart may
# rebuild its chains, so this runs before every container start and from the updater.
# Returns non-zero when a rule is missing and cannot be added; the callers decide what stops.
metadata_rule() { # metadata_rule -C|-I PROTOCOL [POSITION]
  local op=$1 protocol=$2
  shift 2
  iptables -w "$op" DOCKER-USER "$@" ! -i "$BACKUP_BRIDGE" -d "$METADATA_IP" \
    -p "$protocol" ! --dport 53 -j DROP
}

metadata_block() {
  local protocol
  for protocol in tcp udp; do
    metadata_rule -C "$protocol" 2>/dev/null || metadata_rule -I "$protocol" 1 || return 1
  done
  metadata_rule -C tcp 2>/dev/null && metadata_rule -C udp 2>/dev/null
}

cmd_network() {
  ensure_network "$NETWORK" "$BRIDGE"
  ensure_network "$BACKUP_NETWORK" "$BACKUP_BRIDGE"
  metadata_block || die "could not block the metadata server for containers; nothing is started"
}

# --- env ------------------------------------------------------------------------------------

access_token() {
  curl -fsS --max-time 10 -H 'Metadata-Flavor: Google' \
    "$METADATA/instance/service-accounts/default/token" |
    sed -n 's/.*"access_token" *: *"\([^"]*\)".*/\1/p'
}

# crc32c_file FILE: the CRC-32C (Castagnoli) of FILE's bytes, as a decimal number, the form of
# Secret Manager's dataCrc32c. COS has no tool for it, so it is computed here, a table first.
crc32c_file() {
  local crc=4294967295 byte i k c table=()
  for ((i = 0; i < 256; i++)); do
    c=$i
    for ((k = 0; k < 8; k++)); do
      if ((c & 1)); then c=$(((c >> 1) ^ 0x82F63B78)); else c=$((c >> 1)); fi
    done
    table[i]=$c
  done
  for byte in $(od -An -v -tu1 "$1"); do
    crc=$((table[(crc ^ byte) & 255] ^ (crc >> 8)))
  done
  echo $((crc ^ 4294967295))
}

# Returns 1 when the secret cannot be read (the caller tries again) and 2 when it was read and
# its data does not match its checksum (the caller stops). Nothing of the data, its size or
# either checksum is printed.
fetch_env() {
  local token body flat data sum
  token=$(access_token) || return 1
  [ -n "$token" ] || return 1
  # The header goes to curl on stdin (printf is a builtin), so the token is never in argv.
  body=$(printf 'header = "Authorization: Bearer %s"\n' "$token" |
    curl -fsS --max-time 20 -K - \
      "https://secretmanager.googleapis.com/v1/$CAIRN_ENV_SECRET/versions/latest:access") ||
    return 1
  # {"name": "...", "payload": {"data": "<base64>", "dataCrc32c": "<digits>"}}
  flat=$(printf '%s' "$body" | tr -d ' \n\r\t')
  data=$(printf '%s' "$flat" | sed -n 's/.*"data":"\([^"]*\)".*/\1/p')
  sum=$(printf '%s' "$flat" | sed -n 's/.*"dataCrc32c":"\{0,1\}\([0-9]*\).*/\1/p')
  [ -n "$data" ] || return 1
  printf '%s' "$data" | base64 -d >"$ENV_FILE.new" || return 1
  if [ -z "$sum" ]; then
    log "the answer for $CAIRN_ENV_SECRET carries no checksum (dataCrc32c); not checked"
  elif [ "$(crc32c_file "$ENV_FILE.new")" != "$sum" ]; then
    rm -f "$ENV_FILE.new"
    return 2
  fi
  chmod 0600 "$ENV_FILE.new" && mv -f "$ENV_FILE.new" "$ENV_FILE"
}

cmd_env() {
  local rc
  umask 077
  mkdir -p "$ENV_DIR"
  chmod 0700 "$ENV_DIR"
  # Fail closed: cairn must never start without its settings, so keep trying instead; but
  # data that does not match its checksum is not tried again: it stops the start.
  while :; do
    rc=0
    fetch_env || rc=$?
    [ "$rc" -ne 0 ] || break
    if [ "$rc" -eq 2 ]; then
      die "the secret $CAIRN_ENV_SECRET's data does not match its checksum (dataCrc32c); not writing the env file. cairn does not start until a version that matches is read: restart cairn-env.service"
    fi
    err "could not read the latest version of $CAIRN_ENV_SECRET; retrying in 15 s"
    sleep 15
  done
  log "environment written to $ENV_FILE"
}

# --- restore --------------------------------------------------------------------------------

# litestream RUNNER NAME ARGS...: RUNNER is `exec` (replace this shell) or `command` (run and
# return). Runs as cairn's uid so the files it writes stay readable by cairn, and on the
# backup network, the only one allowed to fetch credentials from the metadata server.
litestream() {
  local runner=$1 name=$2 entry=""
  shift 2
  if [ -n "${LITESTREAM_WAIT:-}" ]; then
    entry="--entrypoint sh"
    set -- -c "$LITESTREAM_WAIT_SCRIPT" sh "$@"
  fi
  # shellcheck disable=SC2086
  $runner docker run --rm --name "$name" --network "$BACKUP_NETWORK" $LOG_OPTS $HARDENING \
    ${LITESTREAM_LIMITS:-} --user "$CAIRN_UID:$CAIRN_UID" \
    -e HOME=/tmp -e LITESTREAM_REPLICA_URL="$LITESTREAM_REPLICA_URL" \
    -v "$DB_DIR:/data" -v /etc/cairn/litestream.yml:/etc/litestream.yml:ro \
    $entry "$LITESTREAM_IMAGE" "$@"
}

# The litestream container runs from boot, so the replica check has a log to write into before
# cairn's first start (monitoring.tf: the silence alert arms on the first current line). It
# waits inside for the database cairn creates, then becomes Litestream. A stop ends the wait
# at once: sh as process 1 ignores SIGTERM without a trap.
LITESTREAM_WAIT_SCRIPT='trap "exit 0" TERM INT
until [ -f /data/cairn.db ]; do sleep 2 & wait $!; done
exec litestream "$@"'


cmd_restore() {
  # Exit 0 when the database already exists, when it was restored, or when no replica exists
  # yet. Any other outcome keeps cairn down: starting it would create an empty database and
  # replicate that over the real one.
  docker rm -f cairn-restore >/dev/null 2>&1 || true
  until litestream command cairn-restore \
    restore -if-db-not-exists -if-replica-exists /data/cairn.db; do
    err "restoring /data/cairn.db from $LITESTREAM_REPLICA_URL failed; retrying in 30 s"
    sleep 30
    docker rm -f cairn-restore >/dev/null 2>&1 || true
  done
  log "restore step done"
}

# For the runbooks: `host.sh litestream ltx -level all /data/cairn.db`, `host.sh litestream
# restore -timestamp ... /data/cairn.db`. A plain `docker run` lands on Docker's default
# bridge, which cannot reach the metadata server and so gets no storage credentials.
cmd_litestream() {
  [ $# -gt 0 ] || die "usage: host.sh litestream ARGS..."
  docker rm -f cairn-litestream-tool >/dev/null 2>&1 || true
  litestream command cairn-litestream-tool "$@"
}

# --- replica check --------------------------------------------------------------------------
#
# One line into the litestream container's log, which Cloud Logging receives (monitoring.tf):
#   replica: current, newest NN s old                  counted; 20 minutes without one alerts
#   Error: replica: newest NN s old, over the bound    matched by the replica alert
# NN is the age of the newest transaction the replica holds (the largest timestamp of `ltx
# -json`). Stale is not old: an idle database has nothing to copy. A change to cairn.db or
# its WAL more than REPLICA_GRACE newer than both that copy and the litestream container's
# start (a stopping Litestream checkpoints as it closes) is not copied yet; the first one seen
# is remembered, and Error once it is older than the bound. README, "Alerts", says why.
REPLICA_GRACE=10
REPLICA_UNCONFIRMED=$DATA_MOUNT/replica-unconfirmed

# In the Litestream image (Debian): newest copy, last change, Litestream's start (0: none),
# now, in epoch seconds.
replica_probe() {
  cat <<'PROBE'
newest=""
for level in 0 1 9; do
  listing=$(litestream ltx -json -level "$level" /data/cairn.db) || exit 1
  newest=$(printf '%s\n' "$listing" | sed -n 's/^ *"timestamp": *"\([^"]*\)".*/\1/p' | sort | tail -n 1)
  [ -z "$newest" ] || break
done
if [ -n "$newest" ]; then newest=$(date -u -d "$newest" +%s) || exit 1; else newest=0; fi
changed=$(stat -c %Y /data/cairn.db /data/cairn.db-wal 2>/dev/null | sort -n | tail -n 1)
started=$(date -u -d "$LITESTREAM_STARTED" +%s 2>/dev/null) || started=0
[ -n "$LITESTREAM_STARTED" ] || started=0
echo "$newest $changed $started $(date -u +%s)"
PROBE
}

# replica_said NAME: the probe's four numbers, from a container named NAME. On the backup
# network, the only one that gets storage credentials; the data read-only.
replica_said() {
  local name=$1 started
  docker rm -f "$name" >/dev/null 2>&1 || true
  started=$(docker inspect -f '{{.State.StartedAt}}' litestream 2>/dev/null) || started=""
  # shellcheck disable=SC2086
  docker run --rm --name "$name" --network "$BACKUP_NETWORK" $LOG_OPTS $HARDENING \
    --user "$CAIRN_UID:$CAIRN_UID" \
    -e HOME=/tmp -e LITESTREAM_REPLICA_URL="$LITESTREAM_REPLICA_URL" \
    -e LITESTREAM_STARTED="$started" \
    -v "$DB_DIR:/data:ro" -v /etc/cairn/litestream.yml:/etc/litestream.yml:ro \
    --entrypoint sh "$LITESTREAM_IMAGE" -c "$(replica_probe)"
}

cmd_replica_check() {
  local said newest changed started now extra since baseline line
  is_mounted || die "the data disk is not mounted"
  is_count "${REPLICA_BOUND_SECONDS:-}" || die "REPLICA_BOUND_SECONDS is not a number of seconds"
  if [ ! -f "$DB_DIR/cairn.db" ]; then
    # Nothing to copy is current: the line arms the silence alert on a new VM.
    replica_line "replica: current, no database yet, nothing to copy"
    return 0
  fi
  said=$(replica_said cairn-replica-check) ||
    die "replica check: could not list the replica (Litestream's error is above)"
  read -r newest changed started now extra <<<"$said" || true
  if ! is_count "$newest" || ! is_count "$changed" || ! is_count "$started" ||
    ! is_count "$now" || [ -n "${extra:-}" ]; then
    die "replica check: the probe printed '$said', not four numbers"
  fi

  since=$(first_line "$REPLICA_UNCONFIRMED")
  if ! is_count "$since" || [ "$since" -le $((newest + REPLICA_GRACE)) ]; then
    since="" # copied since, or nothing remembered
  fi
  baseline=$newest
  [ "$started" -le "$baseline" ] || baseline=$started
  if [ -z "$since" ] && [ "$changed" -gt $((baseline + REPLICA_GRACE)) ]; then
    since=$changed
  fi
  if [ -n "$since" ]; then
    write_state "$REPLICA_UNCONFIRMED" "$since"
  else
    rm -f "$REPLICA_UNCONFIRMED"
  fi

  if [ -n "$since" ] && [ $((now - since)) -gt "$REPLICA_BOUND_SECONDS" ]; then
    line="Error: replica: newest $((now - newest)) s old, over the bound"
  else
    line="replica: current, newest $((now - newest)) s old"
  fi
  replica_line "$line"
}

# replica_line LINE: into the journal, and into the log of Litestream's own container: its
# process 1's output is what Docker keeps and Container-Optimized OS sends to Cloud Logging,
# under the container's name.
replica_line() {
  echo "$1"
  printf '%s\n' "$1" | docker exec -i litestream sh -c 'cat >/proc/1/fd/1' ||
    die "the litestream container is not running: the line above reached the journal only"
}

# --- host check -----------------------------------------------------------------------------
#
# Every five minutes (cairn-host-check.timer): what can take the server down without cairn
# saying so. One line for each finding, into the log of a running container (litestream, else
# cairn, else caddy), which Cloud Logging receives; monitoring.tf has one alert for each kind:
#   host-check: disk: the data disk (PATH) is NN% full, over 80%
#   host-check: memory: the container NAME was killed for memory
#   host-check: restarts: systemd restarted UNIT N times since the last check
#   host-check: certificate: Caddy logged N errors obtaining or renewing a certificate ...
# State lives on tmpfs: restart counts start again with the boot, as systemd's do.
HOST_CHECK_DIR=/run/cairn-host-check
HOST_CHECK_DISK_PERCENT=80
HOST_CHECK_RESTARTS=3
HOST_CHECK_UNITS="cairn.service caddy.service litestream.service"

disk_percent() { df -P "$1" | awk 'NR == 2 { sub("%", "", $5); print $5 }'; }

# host_line LINE: into the journal, and into the log of the first container that takes it.
host_line() {
  local name
  echo "$1"
  for name in litestream cairn caddy; do
    printf '%s\n' "$1" | docker exec -i "$name" sh -c 'cat >/proc/1/fd/1' 2>/dev/null &&
      return 0
  done
  err "host check: no container runs to write into: the line above reached the journal only"
  return 1
}

cmd_host_check() {
  local now since failed=0 lines="" label path percent name unit count previous errors line
  now=$(date +%s)
  mkdir -p "$HOST_CHECK_DIR"
  since=$(first_line "$HOST_CHECK_DIR/since")
  is_count "$since" || since=$((now - 300))

  for label in data boot; do
    if [ "$label" = data ]; then
      is_mounted || continue
      path=$DATA_MOUNT
    else
      path=/var/lib/docker
    fi
    percent=$(disk_percent "$path") || percent=""
    if ! is_count "$percent"; then
      err "host check: could not read how full $path is"
      failed=1
    elif [ "$percent" -gt "$HOST_CHECK_DISK_PERCENT" ]; then
      lines="$lines
host-check: disk: the $label disk ($path) is $percent% full, over $HOST_CHECK_DISK_PERCENT%"
    fi
  done

  if names=$(docker events --since "$since" --until "$now" --filter event=oom \
    --format '{{.Actor.Attributes.name}}'); then
    while IFS= read -r name; do
      [ -n "$name" ] || continue
      lines="$lines
host-check: memory: the container $name was killed for memory"
    done <<<"$names"
  else
    err "host check: could not read Docker's events"
    failed=1
  fi

  for unit in $HOST_CHECK_UNITS; do
    count=$(systemctl show -p NRestarts --value "$unit") || count=""
    is_count "$count" || continue
    previous=$(first_line "$HOST_CHECK_DIR/restarts.$unit")
    is_count "$previous" || previous=0
    [ "$count" -ge "$previous" ] || previous=0
    if [ $((count - previous)) -ge "$HOST_CHECK_RESTARTS" ]; then
      lines="$lines
host-check: restarts: systemd restarted $unit $((count - previous)) times since the last check"
    fi
    write_state "$HOST_CHECK_DIR/restarts.$unit" "$count"
  done

  errors=$(docker logs --since "$since" --until "$now" caddy 2>&1 |
    grep '"level":"error"' | grep -c -E '"logger":"tls[.](obtain|renew)"' || true)
  if is_count "$errors" && [ "$errors" -gt 0 ]; then
    lines="$lines
host-check: certificate: Caddy logged $errors errors obtaining or renewing a certificate since the last check"
  fi

  write_state "$HOST_CHECK_DIR/since" "$now"
  if [ -z "$lines" ]; then
    log "host check: nothing found"
  else
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      host_line "$line" || failed=1
    done <<<"$lines"
  fi
  [ "$failed" = 0 ] || exit 1
}

# --- compact --------------------------------------------------------------------------------
#
# By hand, after a workspace is deleted (README, "After a workspace is deleted"): beside a
# running Litestream a compaction cannot truncate the write-ahead log. 1 /healthz and 2 the
# replica check, or nothing changes; 3 stop litestream.service; 4 cairn's compact; 5 start it
# again, by a trap on any exit (not SIGKILL); 6 at most COMPACT_CHECKS checks, 5 s apart, for
# a copy made after the stop. The record is store.compact_due, read-only.
COMPACT_CHECKS=24
COMPACT_STOPPED=""
COMPACT_STEP=""

# The record store.compact_due, read-only: its time, "" when no deletion waits, "?" when it
# could not be read.
compact_due() {
  local due
  due=$(docker exec cairn python -c 'import sqlite3; r = sqlite3.connect("file:/data/cairn.db?mode=ro", uri=True).execute("SELECT value FROM settings WHERE name = ?", ("store.compact_due",)).fetchone(); print(r[0] if r else "")') || due="?"
  printf '%s' "$due"
}

compact_sizes() {
  local due db wal
  due=$(compact_due)
  case "$due" in
    "?") due="unknown, the record could not be read" ;;
    "") due=no ;;
    *) due="yes, since $due" ;;
  esac
  db=$({ wc -c <"$DB_DIR/cairn.db"; } 2>/dev/null) || db=0
  wal=$({ wc -c <"$DB_DIR/cairn.db-wal"; } 2>/dev/null) || wal=0
  log "compact: $1: database ${db// /} bytes, write-ahead log ${wal// /} bytes; a deletion waits for a compaction: $due"
}

compact_start() {
  [ -n "$COMPACT_STOPPED" ] || return 0
  COMPACT_STOPPED=""
  systemctl start litestream.service && log "compact: litestream.service started again" && return 0
  err "compact: litestream.service did not start: run 'sudo systemctl start litestream'; nothing reaches the replica until it runs"
  return 1
}

cmd_compact() {
  local said newest stopped since failed="" rc=0 checks=0 signal
  is_mounted || die "the data disk is not mounted"
  COMPACT_STEP="1/6 (health)"
  healthz || die "compact: step 1/6: cairn does not answer /healthz; nothing was changed"
  log "compact: step 1/6: cairn answers /healthz"
  COMPACT_STEP="2/6 (replica)"
  # The check ends with die when it cannot run, and exits 0 on its Error line: read the line.
  said=$( (cmd_replica_check) 2>&1) || true
  if ! grep -q '^replica: current, ' <<<"$said"; then
    printf '%s\n' "$said" >&2
    die "compact: step 2/6: the replica is not current; nothing was changed"
  fi
  log "compact: step 2/6: $(grep '^replica: current, ' <<<"$said")"
  compact_sizes before

  COMPACT_STEP="3/6 (stop)"
  COMPACT_STOPPED=yes # set first: a stop cut off half-way is undone too
  trap 'compact_start || true' EXIT
  for signal in INT:130 TERM:143 HUP:129; do
    # shellcheck disable=SC2064
    trap "trap - INT TERM HUP; err \"compact: interrupted at step \$COMPACT_STEP\"; exit ${signal#*:}" "${signal%:*}"
  done
  stopped=$SECONDS
  systemctl stop litestream.service ||
    die "compact: step 3/6: could not stop litestream.service; nothing was compacted"
  log "compact: step 3/6: litestream.service stopped"
  # The stopped Litestream stamped its last copy before this second; any later one is new.
  sleep 1
  since=$(date -u +%s)

  COMPACT_STEP="4/6 (compact)"
  docker exec cairn python -m cairn.server compact --db /data/cairn.db || rc=$?
  if [ "$rc" -eq 0 ]; then
    log "compact: step 4/6: compact finished"
  else
    err "compact: step 4/6: compact exited $rc (its lines are above); the deletion stays recorded as due"
    failed="4/6 (compact)"
  fi
  compact_sizes after

  COMPACT_STEP="5/6 (start)"
  compact_start || die "compact: failed at step 5/6 (start)"
  trap - EXIT INT TERM HUP
  log "compact: step 5/6: litestream.service started; it was stopped for $((SECONDS - stopped)) s"

  COMPACT_STEP="6/6 (replica)"
  while :; do
    checks=$((checks + 1))
    newest=$(replica_said cairn-compact-check 2>/dev/null | cut -d' ' -f1) || newest=""
    if is_count "$newest" && [ "$newest" -ge "$since" ]; then
      log "compact: step 6/6: replica current again: it holds a copy made after the stop ($checks checks after the start)"
      break
    fi
    if [ "$checks" -ge "$COMPACT_CHECKS" ]; then
      err "compact: step 6/6: the replica holds no copy made after the stop after $COMPACT_CHECKS checks, 5 s apart"
      failed=${failed:-"6/6 (replica)"}
      break
    fi
    sleep 5
  done
  [ -z "$failed" ] || die "compact: failed at step $failed"
  log "compact: done"
}

# compact-if-due, from cairn-compact.timer once a night: `compact` when, and only when, a
# deletion waits for a compaction. On success one line with the sizes before and after. On
# failure Litestream runs again (compact's trap) and an "Error: compact: ..." line goes into
# the litestream container's log, which the replica alert matches (monitoring.tf), and into
# the journal.
nightly_failed() {
  local line="compact: the nightly compaction failed: $1"
  err "$line"
  printf 'Error: %s\n' "$line" | docker exec -i litestream sh -c 'cat >/proc/1/fd/1' ||
    err "the litestream container is not running: the line above reached the journal only"
  exit 1
}

cmd_compact_if_due() {
  local due said rc=0 failed before after
  is_mounted || nightly_failed "the data disk is not mounted"
  due=$(compact_due)
  case "$due" in
    "?") nightly_failed "whether a deletion waits could not be read (is cairn running?)" ;;
    "")
      log "compact: nightly: no deletion waits for a compaction; nothing to do"
      return 0
      ;;
  esac
  said=$( (cmd_compact) 2>&1) || rc=$?
  printf '%s\n' "$said"
  if [ "$rc" -ne 0 ]; then
    failed=$(printf '%s\n' "$said" | sed -n 's/^.*cairn-host: ERROR: compact: //p' | tail -n 1)
    nightly_failed "${failed:-exit $rc}"
  fi
  # "compact: before: database N bytes, write-ahead log N bytes; ..." and its "after:" twin.
  before=$(printf '%s\n' "$said" | sed -n 's/^cairn-host: compact: before: database \([0-9]*\) bytes, write-ahead log \([0-9]*\) bytes;.*/\1 \2/p')
  after=$(printf '%s\n' "$said" | sed -n 's/^cairn-host: compact: after: database \([0-9]*\) bytes, write-ahead log \([0-9]*\) bytes;.*/\1 \2/p')
  log "compact: nightly: database ${before% *} -> ${after% *} bytes, write-ahead log ${before#* } -> ${after#* } bytes"
}

# --- deployment state -----------------------------------------------------------------------

boot_id() { cat /proc/sys/kernel/random/boot_id; }

first_line() { sed -n 1p "$1" 2>/dev/null || true; }
proven_image() { first_line "$IMAGE_FILE"; }
pending_candidate() { first_line "$PENDING_FILE"; }
pending_boot() { sed -n 2p "$PENDING_FILE" 2>/dev/null || true; }

# write_state FILE LINE...: replace FILE in one rename, so no reader sees half a record.
write_state() {
  local file=$1
  shift
  printf '%s\n' "$@" >"$file.new"
  mv -f "$file.new" "$file"
}

# True for "$CAIRN_IMAGE@sha256:" plus 64 hex digits, the only kind of reference that is run.
is_digest_ref() {
  local digest=${1#"$CAIRN_IMAGE@sha256:"}
  [ "$digest" != "$1" ] && [ "${#digest}" -eq 64 ] || return 1
  case "$digest" in
    *[!0-9a-f]*) return 1 ;;
  esac
}

# What cairn.service must run now: the candidate while a deployment started in this boot has
# no verdict yet, otherwise the proven image. A record from an earlier boot never counts, so
# a VM that stops in the middle of a deployment comes back on the proven image.
image_to_run() {
  if [ -f "$PENDING_FILE" ] && [ "$(pending_boot)" = "$(boot_id)" ]; then
    pending_candidate
  else
    proven_image
  fi
}

# --- containers -----------------------------------------------------------------------------

registry_login() {
  mkdir -p "$HOME"
  docker-credential-gcr configure-docker --registries "$CAIRN_REGISTRY_HOST" >/dev/null
}

cmd_run_cairn() {
  local image limited
  limited=$(limits cairn) || exit 1
  metadata_block || die "the metadata server is not blocked for containers; not starting cairn"
  image=$(image_to_run)
  [ -n "$image" ] || die "nothing to run: no proven image and no deployment in progress"
  is_digest_ref "$image" || die "refusing to run '$image': not a digest under $CAIRN_IMAGE"
  registry_login
  # Caddy reaches cairn over $NETWORK, so cairn trusts that network's addresses, and nothing
  # else, to say who the client is (X-Forwarded-For; Caddy sets it and drops a client's own).
  # Docker chose the subnet when it made the network, so it is read here, which also works on
  # a VM whose network predates this. If it cannot be read, cairn still starts, trusting no
  # proxy: every client then shares one count in the per-client limits on sign-in and agent
  # registration, which one client can use up. That is worse than a working proxy setting
  # and better than not starting, so it is logged as an error.
  local proxies="" subnet
  for subnet in $(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$NETWORK"); do
    proxies="$proxies --trusted-proxy $subnet"
  done
  [ -n "$proxies" ] ||
    err "no subnet read for Docker network $NETWORK: cairn trusts no proxy, so all clients share one sign-in and registration limit"
  # The plans file, only when it has content: read-only at the same path in the container,
  # and named to serve. An empty one (no cairn_plans_file) gives cairn neither, as before.
  # The +-form expands an empty array to nothing under set -u on bash before 4.4 as well.
  local plans_mount=() plans_option=()
  if [ -s "$PLANS_FILE" ]; then
    plans_mount=(-v "$PLANS_FILE:/etc/cairn/plans.yaml:ro")
    plans_option=(--plans /etc/cairn/plans.yaml)
  fi
  # The brand, an option for each file that has content. The name and the alt text are read
  # whole and each given as one word, --brand-name=VALUE, so that a value starting with a dash
  # is still a value; nothing expands or splits them. The logo is mounted read-only at the
  # same path in the container, as the plans file is. With no brand nothing is added.
  local brand_mount=() brand_option=()
  if [ -s "$BRAND_DIR/name" ]; then
    brand_option+=("--brand-name=$(<"$BRAND_DIR/name")")
  fi
  if [ -s "$BRAND_DIR/logo" ]; then
    brand_mount=(-v "$BRAND_DIR/logo:/etc/cairn/brand/logo:ro")
    brand_option+=(--brand-logo /etc/cairn/brand/logo)
  fi
  if [ -s "$BRAND_DIR/logo-alt" ]; then
    brand_option+=("--brand-logo-alt=$(<"$BRAND_DIR/logo-alt")")
  fi
  # Arguments replace the image's default command, so the whole command is spelled out.
  # CAIRN_EXTRA_ARGS is split on spaces on purpose; OpenTofu validates its characters.
  # shellcheck disable=SC2086
  exec docker run --rm --name cairn --network "$NETWORK" $LOG_OPTS $HARDENING $limited \
    --env-file "$ENV_FILE" \
    -v "$DB_DIR:/data" ${plans_mount[@]+"${plans_mount[@]}"} \
    ${brand_mount[@]+"${brand_mount[@]}"} \
    "$image" serve --db /data/cairn.db --host 0.0.0.0 \
    --public-url "$CAIRN_PUBLIC_URL" $proxies \
    ${plans_option[@]+"${plans_option[@]}"} ${brand_option[@]+"${brand_option[@]}"} \
    $CAIRN_EXTRA_ARGS
}

cmd_run_caddy() {
  local limited
  limited=$(limits caddy) || exit 1
  metadata_block || die "the metadata server is not blocked for containers; not starting Caddy"
  # shellcheck disable=SC2086
  exec docker run --rm --name caddy --network "$NETWORK" $LOG_OPTS $HARDENING $CADDY_CAPS \
    $limited -p 80:80 -p 443:443 \
    -e CAIRN_DOMAIN="$CAIRN_DOMAIN" -e ACME_EMAIL="$ACME_EMAIL" \
    -v /etc/cairn/Caddyfile:/etc/caddy/Caddyfile:ro \
    -v "$CADDY_DIR/data:/data" -v "$CADDY_DIR/config:/config" \
    "$CADDY_IMAGE"
}

cmd_run_litestream() {
  LITESTREAM_LIMITS=$(limits litestream) || exit 1
  # cairn creates the database on its first start; Litestream needs the file to exist, and
  # waits for it inside its container (LITESTREAM_WAIT_SCRIPT).
  LITESTREAM_WAIT=1
  litestream exec litestream replicate
}

# --- update ---------------------------------------------------------------------------------
#
# A deployment is: record the candidate in `pending`, restart cairn (which now runs the
# candidate), wait for /healthz, then either promote (the candidate becomes `image`) or roll
# back (the record is dropped and cairn restarts on `image`). `image` changes only after a
# passing health check. Every step can be cut off by a kill or a reboot; settle and converge
# below bring any leftover state to one of the two outcomes on the next run.

# The image of the container named cairn, or nothing while there is none. A failed
# `docker inspect -f` still prints an empty line, hence the command substitution.
container_image() {
  local image
  image=$(docker inspect -f '{{.Config.Image}}' cairn 2>/dev/null) || image=""
  printf '%s' "$image"
}

# What is running, or, while the container is down, what cairn.service would run.
running_image() {
  local image
  image=$(container_image)
  [ -n "$image" ] || image=$(image_to_run)
  printf '%s' "$image"
}

healthz() {
  docker exec cairn python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3)" \
    >/dev/null 2>&1
}

# True once the container named cairn runs IMAGE and /healthz answered twice in a row.
wait_healthy() {
  local image=$1 deadline=$((SECONDS + HEALTH_SECONDS)) streak=0
  while [ "$SECONDS" -lt "$deadline" ]; do
    if [ "$(container_image)" = "$image" ] && healthz; then
      streak=$((streak + 1))
      [ "$streak" -ge 2 ] && return 0
    else
      streak=0
    fi
    sleep 2
  done
  return 1
}

# The image is written first: if this is cut off before the record is removed, the leftover
# record names the proven image and settle only has to remove it.
#
# Then the boot disk, which holds every pulled image: `docker image prune -a` removes each
# image no container uses and that was built more than three days ago. The running image is
# in use; the image before it is kept for a rollback by a container made from it and never
# started, cairn-previous. When settle finishes a promotion cut short, the candidate is
# already the proven image and the container from the first call stays as it is.
promote() {
  local previous
  previous=$(proven_image)
  write_state "$IMAGE_FILE" "$1"
  rm -f "$PENDING_FILE" "$FAILED_FILE" "$INTERRUPTED_FILE"
  log "now running $1"
  if [ -n "$previous" ] && [ "$previous" != "$1" ]; then
    docker rm -f cairn-previous >/dev/null 2>&1 || true
    docker create --name cairn-previous "$previous" >/dev/null 2>&1 || true
  fi
  docker image prune -af --filter until=72h >/dev/null 2>&1 || true
}

roll_back() {
  local candidate=$1 proven
  write_state "$FAILED_FILE" "$candidate"
  proven=$(proven_image)
  if [ -z "$proven" ]; then
    systemctl stop cairn.service || true
    rm -f "$PENDING_FILE" "$INTERRUPTED_FILE"
    die "$candidate was not healthy after $HEALTH_SECONDS s; there is no proven image, cairn is stopped"
  fi
  rm -f "$PENDING_FILE" "$INTERRUPTED_FILE"
  err "$candidate was not healthy after $HEALTH_SECONDS s; restarting the proven image $proven"
  systemctl restart cairn.service || true
  if wait_healthy "$proven"; then
    die "rolled back to $proven; the tag still names the failed image, move it to deploy again"
  fi
  die "rollback to $proven is not healthy either (a newer database schema needs a restore)"
}

# Give CANDIDATE one health window, then promote it or roll back. The restart is judged by
# the health check, not by its own exit status.
judge() {
  local candidate=$1
  [ "$(container_image)" = "$candidate" ] || systemctl restart cairn.service || true
  if wait_healthy "$candidate"; then
    promote "$candidate"
  else
    roll_back "$candidate"
  fi
}

# A record from an earlier boot: its deployment got no verdict and, because cairn.service
# ignores such a record, its candidate is not running. Drop it. The candidate gets one more
# try; the second time a reboot cuts the same candidate short it is marked failed instead,
# so an image that takes the VM down with it is not tried forever.
settle_stale() {
  local candidate started proven
  [ -f "$PENDING_FILE" ] || return 0
  started=$(pending_boot)
  [ "$started" != "$(boot_id)" ] || return 0
  candidate=$(pending_candidate)
  proven=$(proven_image)
  if ! is_digest_ref "$candidate"; then
    err "dropping an unreadable deployment record"
  elif [ "$candidate" = "$proven" ]; then
    # promote was cut off between its two steps; the health check had already passed.
    rm -f "$FAILED_FILE" "$INTERRUPTED_FILE"
    log "the deployment of $candidate had passed its health check before the restart"
  elif [ "$(cat "$INTERRUPTED_FILE" 2>/dev/null || true)" = "$candidate $started" ]; then
    : # this interruption is already counted; only the record is left to remove
  elif [ "$(first_line "$INTERRUPTED_FILE" | cut -d' ' -f1)" = "$candidate" ]; then
    write_state "$FAILED_FILE" "$candidate"
    rm -f "$INTERRUPTED_FILE"
    err "a restart cut the deployment of $candidate short for the second time; not trying it again until the tag moves"
  else
    write_state "$INTERRUPTED_FILE" "$candidate $started"
    err "a restart cut the deployment of $candidate short; back on ${proven:-nothing}, the updater tries it once more"
  fi
  rm -f "$PENDING_FILE"
}

# Runs in cairn-disk.service, once per boot, before cairn can start.
cmd_boot_settle() {
  is_mounted || die "the data disk is not mounted"
  settle_stale
}

# A deployment an earlier run of the updater started and did not finish.
settle() {
  local candidate
  settle_stale
  [ -f "$PENDING_FILE" ] || return 0
  candidate=$(pending_candidate)
  if ! is_digest_ref "$candidate"; then
    err "dropping an unreadable deployment record"
    rm -f "$PENDING_FILE"
  elif [ "$candidate" = "$(proven_image)" ]; then
    promote "$candidate" # cut off between its two steps; finish it
  else
    log "found a deployment of $candidate without a verdict; checking it now"
    judge "$candidate"
  fi
}

# The container must be the one image_to_run names. A rollback cut off before its restart
# leaves the failed candidate running with no record; this is where that is repaired.
converge() {
  local have want
  have=$(container_image)
  want=$(image_to_run)
  [ -n "$have" ] && [ "$have" != "$want" ] || return 0
  if [ -n "$want" ]; then
    err "the cairn container runs $have, not the proven image $want; restarting it"
    systemctl restart cairn.service || true
  else
    err "the cairn container runs $have and there is no proven image; stopping it"
    systemctl stop cairn.service || true
  fi
}

# Sets DESIRED to the digest reference the deploy tag names right now, or to nothing while
# the tag does not exist. Pulling an unchanged tag costs one manifest request.
resolve_tag() {
  local ref="$CAIRN_IMAGE:$CAIRN_DEPLOY_TAG" out digest
  DESIRED=""
  if ! out=$(docker pull "$ref" 2>&1); then
    case "$out" in
      *"manifest unknown"* | *"not found"*)
        log "nothing is tagged $ref yet"
        return 0
        ;;
    esac
    die "could not pull $ref, will retry: $(printf '%s\n' "$out" | tail -n 1)"
  fi
  digest=$(printf '%s\n' "$out" |
    sed -n 's/^Digest: \(sha256:[0-9a-f]\{64\}\)[[:space:]]*$/\1/p' | sed -n 1p)
  [ -n "$digest" ] || die "docker pull $ref printed no digest; not deploying"
  DESIRED="$CAIRN_IMAGE@$digest"
}

# --- status for the deploy job --------------------------------------------------------------
#
# One line in the guest attribute cairn/status, written in one request so a reader never sees
# half of it, at the end of every `update` run and when a deployment starts:
#
#   seq=N updated=EPOCH running=D healthy=yes|no proven=D pending=D failed=D hold=yes|no
#
# seq grows by one with every report and is kept on the data disk, so it never goes back
# while the disk lives; it is 0 while the disk is not mounted. A reader that saw seq N knows
# that any report with a higher seq was written after it, whatever the clocks say.
# updated is the VM's clock, for people.
# D is a digest (sha256:...), "none", or "other" for a reference that is not a digest of
# CAIRN_IMAGE. running is the container's image, healthy is /healthz answering at that moment.
# The deploy job reads it through compute.instances.getGuestAttributes, the one permission
# it holds on this VM. Only processes that reach the metadata server can write it: the host
# and Litestream, not cairn or Caddy. Best effort: a failure here never fails the updater.
STATUS_ATTRIBUTE=cairn/status

status_digest() {
  if [ -z "$1" ]; then
    printf 'none'
  elif is_digest_ref "$1"; then
    printf '%s' "${1#"$CAIRN_IMAGE@"}"
  else
    printf 'other'
  fi
}

next_status_seq() {
  local seq
  is_mounted || {
    printf '0'
    return 0
  }
  seq=$(first_line "$STATUS_SEQ_FILE")
  case "$seq" in
    '' | *[!0-9]*) seq=0 ;;
  esac
  seq=$((seq + 1))
  write_state "$STATUS_SEQ_FILE" "$seq" || return 1
  printf '%s' "$seq"
}

report_status() {
  local running healthy=no line seq
  seq=$(next_status_seq) || seq=0
  running=$(container_image)
  if [ -n "$running" ] && healthz; then
    healthy=yes
  fi
  line="seq=$seq updated=$(date -u +%s) running=$(status_digest "$running") healthy=$healthy"
  line="$line proven=$(status_digest "$(proven_image)")"
  line="$line pending=$(status_digest "$(pending_candidate)")"
  line="$line failed=$(status_digest "$(first_line "$FAILED_FILE")")"
  if [ -f "$HOLD_FILE" ]; then line="$line hold=yes"; else line="$line hold=no"; fi
  curl -fsS --max-time 5 -X PUT -H 'Metadata-Flavor: Google' --data "$line" \
    "$METADATA/instance/guest-attributes/$STATUS_ATTRIBUTE" >/dev/null 2>&1 ||
    log "could not publish the deployment status (is enable-guest-attributes set?)"
}

cmd_update() {
  local running failed
  # Every way out of this function, including `exit 0` and `die`, publishes the status.
  trap 'report_status || true' EXIT
  metadata_block || err "the metadata server is not blocked for containers"
  is_mounted || exit 0
  if [ -f "$HOLD_FILE" ]; then
    log "deployments are on hold ($(first_line "$HOLD_FILE")); not following the tag"
    exit 0
  fi
  # cairn waits for these two; while either is still working there is nothing to judge.
  # (Two calls: is-active with several units succeeds when any one of them is active.)
  systemctl is-active --quiet cairn-env.service || exit 0
  systemctl is-active --quiet cairn-restore.service || exit 0

  settle
  converge

  registry_login
  resolve_tag
  [ -n "$DESIRED" ] || exit 0
  # The tag moved off an image that failed or was interrupted: forget it, so that a later
  # move back to it is a new try with a clean count.
  failed=$(first_line "$FAILED_FILE")
  if [ -n "$failed" ] && [ "$failed" != "$DESIRED" ]; then
    rm -f "$FAILED_FILE"
    failed=""
  fi
  if [ -f "$INTERRUPTED_FILE" ] && [ "$(first_line "$INTERRUPTED_FILE" | cut -d' ' -f1)" != "$DESIRED" ]; then
    rm -f "$INTERRUPTED_FILE"
  fi
  running=$(running_image)
  [ "$DESIRED" != "$running" ] || exit 0
  [ "$DESIRED" != "$failed" ] || exit 0

  log "deploying $DESIRED (running: ${running:-nothing})"
  # By digest, so the reference cairn.service will run is known to the local Docker.
  docker pull "$DESIRED" >/dev/null || die "could not pull $DESIRED; will retry"
  write_state "$PENDING_FILE" "$DESIRED" "$(boot_id)"
  report_status || true
  judge "$DESIRED"
}

# --- hold, pin, release ---------------------------------------------------------------------
#
# For a person, on the VM, around a restore or a rollback across a schema migration
# (README, "Rollback"): hold, stop the writer, restore, pin the older digest, move the tag to
# it, check `state`, release. The hold is a file on the data disk, so it outlasts reboots;
# cairn-update.timer still starts at boot, and each run stops at the hold.

cmd_hold() {
  is_mounted || die "the data disk is not mounted"
  # The file first: an update that starts after this line stops at the hold.
  write_state "$HOLD_FILE" "held since $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  systemctl stop cairn-update.timer || true
  # Stops an update in flight. The updater is built to be killed at any step; whatever it
  # left (a pending record, a candidate running) is settled by `pin` or by the next run
  # after `release`.
  systemctl stop cairn-update.service || true
  report_status || true
  log "deployments are on hold. Nothing follows the tag until 'host.sh release'."
  cmd_state
}

cmd_pin() {
  local ref
  [ -f "$HOLD_FILE" ] || die "pin needs a hold first: host.sh hold"
  [ $# -eq 1 ] || die "usage: host.sh pin sha256:DIGEST"
  ref="$CAIRN_IMAGE@$1"
  is_digest_ref "$ref" || die "'$1' is not a digest (sha256: and 64 hex digits)"
  registry_login
  docker pull "$ref" >/dev/null || die "could not pull $ref"
  # The proven image is what every start of cairn.service runs while no deployment is in
  # flight. A pin is a decision by a person, so the records of earlier verdicts go with it.
  write_state "$IMAGE_FILE" "$ref"
  rm -f "$PENDING_FILE" "$FAILED_FILE" "$INTERRUPTED_FILE"
  report_status || true
  log "pinned $ref: cairn runs it from its next start. It is not restarted now."
}

cmd_release() {
  local proven
  [ -f "$HOLD_FILE" ] || {
    log "not on hold"
    return 0
  }
  proven=$(proven_image)
  registry_login
  resolve_tag
  if [ "$DESIRED" != "$proven" ] && [ "${1:-}" != --force ]; then
    die "the tag names ${DESIRED:-nothing} but cairn starts ${proven:-nothing}; a release hands the tag's image to the updater within a minute. Move the tag to the image cairn starts first (or: host.sh release --force)"
  fi
  rm -f "$HOLD_FILE"
  systemctl start cairn-update.timer
  report_status || true
  log "released: the updater follows the tag again"
}

cmd_state() {
  local tag_digest
  registry_login
  # In a subshell, because resolve_tag exits on a registry error.
  if tag_digest=$(resolve_tag >/dev/null 2>&1 && printf '%s' "$DESIRED"); then
    tag_digest=$(status_digest "$tag_digest")
  else
    tag_digest="unknown (the registry could not be read)"
  fi
  echo "tag $CAIRN_DEPLOY_TAG: $tag_digest"
  echo "proven (cairn starts it): $(status_digest "$(proven_image)")"
  echo "running: $(status_digest "$(container_image)")"
  echo "pending: $(status_digest "$(pending_candidate)")"
  echo "failed: $(status_digest "$(first_line "$FAILED_FILE")")"
  echo "interrupted: $(first_line "$INTERRUPTED_FILE")"
  echo "hold: $(first_line "$HOLD_FILE")"
}

# --- main -----------------------------------------------------------------------------------

case "${1:-}" in
  disk) cmd_disk ;;
  boot-settle) cmd_boot_settle ;;
  network) cmd_network ;;
  env) cmd_env ;;
  restore) cmd_restore ;;
  run-cairn) cmd_run_cairn ;;
  run-caddy) cmd_run_caddy ;;
  run-litestream) cmd_run_litestream ;;
  litestream)
    shift
    cmd_litestream "$@"
    ;;
  replica-check) cmd_replica_check ;;
  compact) cmd_compact ;;
  compact-if-due) cmd_compact_if_due ;;
  host-check) cmd_host_check ;;
  update) cmd_update ;;
  hold) cmd_hold ;;
  pin)
    shift
    cmd_pin "$@"
    ;;
  release)
    shift
    cmd_release "$@"
    ;;
  state) cmd_state ;;
  *)
    echo "usage: host.sh disk|boot-settle|network|env|restore|run-cairn|run-caddy|run-litestream|litestream ARGS|replica-check|compact|compact-if-due|host-check|update|hold|pin DIGEST|release [--force]|state" >&2
    exit 2
    ;;
esac
