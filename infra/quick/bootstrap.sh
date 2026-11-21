#!/bin/bash
# systemd retries package installation on first boot and starts the stack on each boot.
set -euo pipefail
export PATH="$PATH:/snap/bin"
install_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

die() {
  printf 'cairn-quick: %s\n' "$*" >&2
  exit 1
}

install_host() (
  local cloud=$1 codename architecture staging
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update
  apt-get -o DPkg::Lock::Timeout=120 -y install ca-certificates curl iptables

  if [[ "$cloud" == aws ]]; then
    snap list amazon-ssm-agent >/dev/null 2>&1 || snap install amazon-ssm-agent --classic
    snap start amazon-ssm-agent
    snap list aws-cli >/dev/null 2>&1 || snap install aws-cli --classic
  fi

  install -m 0755 -d /etc/apt/keyrings
  staging=$(mktemp -d /etc/apt/keyrings/.cairn-quick.XXXXXX)
  trap 'rm -rf -- "$staging"' EXIT
  curl -fsSL --connect-timeout 10 --max-time 60 https://download.docker.com/linux/ubuntu/gpg \
    -o "$staging/docker.asc"
  codename=$(. /etc/os-release; printf '%s' "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
  architecture=$(dpkg --print-architecture)
  cat >"$staging/docker.sources" <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Architectures: $architecture
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  chmod 0644 "$staging/docker.asc" "$staging/docker.sources"
  mv -f -- "$staging/docker.asc" /etc/apt/keyrings/docker.asc
  mv -f -- "$staging/docker.sources" /etc/apt/sources.list.d/docker.sources
  apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update
  apt-get -o DPkg::Lock::Timeout=120 -y install \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
)

# Docker sends forwarded container traffic through DOCKER-USER before its own rules.
# The Docker unit's ExecStartPre can create this chain before the daemon's first start.
# DNS on port 53 still works when the host's resolver uses the same address on Google Cloud.
block_metadata() {
  local protocol
  if ! iptables -w -S DOCKER-USER >/dev/null 2>&1; then
    iptables -w -N DOCKER-USER || return 1
  fi
  for protocol in tcp udp; do
    local rule=(! -i br-cairn-backup -d 169.254.169.254 -p "$protocol" ! --dport 53 -j DROP)
    iptables -w -C DOCKER-USER "${rule[@]}" 2>/dev/null ||
      iptables -w -I DOCKER-USER 1 "${rule[@]}" || return 1
  done
}

aws_settings() {
  python3 - <<'PY'
import json
import os
import subprocess
import sys

aws = ["aws", "--no-cli-pager", "--output", "json", "secretsmanager"]
secret = ["--secret-id", os.environ["CAIRN_SETTINGS_SECRET"]]
page = []
while True:
    result = json.loads(subprocess.check_output([*aws, "list-secret-version-ids", *secret, *page]))
    if any("AWSCURRENT" in v.get("VersionStages", []) for v in result.get("Versions", [])):
        value = json.loads(subprocess.check_output([
            *aws, "get-secret-value", *secret, "--version-stage", "AWSCURRENT",
        ]))
        sys.stdout.buffer.write(value["SecretString"].encode("utf-8"))
        break
    next_token = result.get("NextToken")
    if not next_token:
        break
    page = ["--next-token", next_token]
PY
}

gcp_settings() {
  local token version
  local -a options=(-fsS --connect-timeout 5 --max-time 30)
  local url="https://secretmanager.googleapis.com/v1/$CAIRN_SETTINGS_SECRET"
  token=$(curl "${options[@]}" -H 'Metadata-Flavor: Google' \
    http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])') ||
    return 1
  [ -n "$token" ] || return 1
  # curl reads the authorization header from stdin, so the token is absent from argv.
  version=$(printf 'Authorization: Bearer %s\n' "$token" |
    curl "${options[@]}" -H @- "$url/versions?pageSize=1&filter=state:ENABLED" |
    python3 -c '
import json, sys
versions = json.load(sys.stdin).get("versions", [])
if versions:
    print(versions[0]["name"])
') ||
    return 1
  [ -n "$version" ] || return 0
  printf 'Authorization: Bearer %s\n' "$token" |
    curl "${options[@]}" -H @- "https://secretmanager.googleapis.com/v1/$version:access" |
    python3 -c '
import base64, json, sys

payload = json.load(sys.stdin)["payload"]
data = base64.b64decode(payload.get("data", ""), validate=True)
# Secret Manager uses CRC-32C (Castagnoli).
crc = 0xFFFFFFFF
for byte in data:
    crc ^= byte
    for _ in range(8):
        crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
if int(payload["dataCrc32c"]) != (crc ^ 0xFFFFFFFF):
    sys.exit("the data of the secret does not match its checksum")
sys.stdout.buffer.write(data)
'
}

# The tools' own error lines for the journal. Left out: a line holding a whole settings
# line, a value of 6 to 11 characters or 12 in a row of a settings line (its name included).
# Less gets through.
say_errors() {
  python3 - "$@" >&2 <<'PY'
import re, sys
label, errors, *settings = sys.argv[1:]
values = set()
for name in settings:
    try:
        lines = open(name, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        continue
    for line in lines:
        values.add(line.strip())
        for text in [line, *re.split("[=:]", line, maxsplit=1)[1:]]:
            text = text.strip().strip("'\"")
            if len(text) >= 6:
                values.update(text[i:i + 12] for i in range(max(1, len(text) - 11)))
values.discard("")
for line in open(errors, encoding="utf-8", errors="replace").read().splitlines()[-20:]:
    if line.startswith(("Traceback", " ")):
        continue
    if "variable is not set" in line:  # Compose names what follows a $ in a value
        line = "a settings value holds a $ that Compose took for a variable: put the value in single quotes"
    elif "cairn.env" in line:  # its syntax error quotes the file
        found = re.search(r"line \d+: [^\"']*", line)
        line = "the settings file, " + (found[0].strip() if found else "unreadable")
    if any(value in line for value in values):
        line = "(a line that repeats part of a setting was left out)"
    if line.strip():
        print(f"cairn-quick: {label}: {line}")
PY
}

# Compares the secret's settings with cairn's container's and prints names, never a value.
# Reads NAME=value as written, in single quotes (a quote as \', over lines too) or to line end,
# and counts the rest. A value in double quotes over lines is not read: its first line is named
# as differing, and a later line of it that looks like a setting can be named too.
check_settings() {
  local id image check
  [ -s cairn.env ] || return 0
  id=$(docker compose ps -q cairn 2>/dev/null) && [ -n "$id" ] &&
    image=$(docker inspect -f '{{.Image}}' "$id" 2>/dev/null) || return 1
  check=$(cat <<'PY'
import json, re, sys
given, own = ({p.partition("=")[0]: p.partition("=")[2] for p in json.loads(line)} for line in sys.stdin)
text = open("cairn.env", encoding="utf-8", newline="").read()
skip = re.compile(r"[ \t\r]*(#.*)?(\n|\Z)")
read = re.compile(r"([A-Za-z_]\w*)=(?:'((?:[^'\\]|\\[^\\])*)'[ \t]*(?:#.*)?|((?![ \t]*').*?))\r?(?:\n|\Z)")
holds, unread, at = {}, [], 0
while at < len(text):
    line = skip.match(text, at)
    if not line:
        line = read.match(text, at)
        if line:
            holds[line[1]] = line[3] if line[2] is None else line[2].replace("\\'", "'")
        else:
            line = re.compile(r".*(\n|\Z)").match(text, at)
            unread.append(line[0])
    at = line.end()
for setting, value in holds.items():
    if setting in given and given[setting] != value:
        print(f"cairn-quick: settings check: {setting} differs in cairn from what the secret holds."
              ' How to write a value: "AWS settings" or "Google Cloud settings" in'
              " infra/quick/README.md")
on = lambda n: any(map(re.compile(r"\ufeff?[ \t]*(export[ \t]+)?" + re.escape(n) + r"[ \t]*[=:]").match, unread))
names = sorted(n for n in given if on(n) or n not in holds and given[n] != own.get(n))
count = len(unread) + sum(not on(n) for n in names)
if count:
    print(f"cairn-quick: settings check: {count} line(s) of the settings not compared"
          + (": " + ", ".join(names) if names else ""))
PY
  )
  docker inspect -f '{{json .Config.Env}}' "$id" "$image" 2>/dev/null | python3 -c "$check" >&2 2>/dev/null
}

publish_env() {
  local name=$1
  if cmp -s "$staging/$name" "$name"; then
    chmod 0600 "$name"
  else
    mv -f -- "$staging/$name" "$name"
  fi
}

start_stack() (
  cd "$install_dir/deploy"
  umask 077
  block_metadata || die 'could not install the Docker metadata firewall'
  local engine read_settings file status
  engine=$(docker version --format '{{.Server.Version}}')
  dpkg --compare-versions "$engine" ge 28.3.3 || die 'Docker Engine 28.3.3 or later is required'
  case "${CAIRN_CLOUD:?}" in
    aws) read_settings=aws_settings ;;
    gcp) read_settings=gcp_settings ;;
    *) die 'unknown cloud in the bootstrap configuration' ;;
  esac

  staging=$(mktemp -d "$PWD/.bootstrap.XXXXXX")
  trap 'rm -rf -- "$staging"' EXIT
  # The value goes to the staged file and nowhere else. What the tool says goes to the journal.
  if ! "$read_settings" >"$staging/cairn.env" 2>"$staging/errors"; then
    say_errors 'settings read' "$staging/errors" "$staging/cairn.env" cairn.env || true
    die "could not read the cairn settings secret on $CAIRN_CLOUD. Check the VM identity and the selected version."
  fi
  cat >"$staging/.env" <<EOF
CAIRN_DOMAIN=${CAIRN_DOMAIN:?}
ACME_EMAIL=${ACME_EMAIL:?}
CAIRN_IMAGE=${CAIRN_IMAGE:?}
COMPOSE_FILE=compose.yaml:compose.backup.yaml:compose.quick.yaml
EOF
  # Compose splits the quoted arguments from .env into the serve command.
  if [[ -n "${CAIRN_QUICK_SERVE_ARGS:-}" ]]; then
    printf "CAIRN_EXTRA_ARGS='%s'\n" "$CAIRN_QUICK_SERVE_ARGS" >>"$staging/.env"
  fi
  printf 'LITESTREAM_REPLICA_URL=%s\n' "${LITESTREAM_REPLICA_URL:?}" >"$staging/litestream.env"
  if [[ "$CAIRN_CLOUD" == aws ]]; then
    printf 'AWS_REGION=%s\n' "${AWS_REGION:?}" >>"$staging/litestream.env"
  fi
  for file in .env litestream.env cairn.env; do
    publish_env "$file"
  done

  # Without its progress lines, what Compose prints is its warnings and its error.
  if ! docker compose --progress quiet up -d --pull missing >/dev/null 2>"$staging/errors"; then
    say_errors compose "$staging/errors" cairn.env || true
    if status=$(docker compose ps --all --format '{{.Service}} {{.State}} (exit={{.ExitCode}})' 2>/dev/null); then
      [ -z "$status" ] || printf '%s\n' "$status" >&2
    fi
    die 'Docker Compose up failed. Check registry access, the image, the settings file syntax and container status.'
  fi
  # What Compose said of a start that worked: it warns of a $ before a name no variable
  # has, and changes other values without a word. check_settings names those.
  say_errors compose "$staging/errors" cairn.env || true
  check_settings || echo 'cairn-quick: settings check: did not run' >&2
)

first_key() (
  cd "$install_dir/deploy"
  umask 077
  local key_file
  key_file=$(mktemp "$install_dir/cairn-first-key.XXXXXX")
  if ! docker compose exec -T cairn sh -eu >"$key_file" 2>/dev/null <<'KEY_SCRIPT'
directory=$(mktemp -d)
trap 'rm -rf -- "$directory"' EXIT
python -m cairn.server key-create --db /data/cairn.db --label operator --out "$directory/key.env" >/dev/null
cat "$directory/key.env"
KEY_SCRIPT
  then
    rm -f -- "$key_file"
    die 'could not create the first key. Check that the cairn container is running.'
  fi
  printf 'The key is in %s (mode 0600).\n' "$key_file"
)

case "${1:-start}" in
  install) install_host "${2:?missing cloud}" ;;
  firewall) block_metadata || die 'could not install the Docker metadata firewall' ;;
  start) start_stack ;;
  first-key) first_key ;;
  *) die 'expected install, firewall, start or first-key' ;;
esac
