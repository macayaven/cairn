#!/bin/bash
# cloud-init installs the host once. systemd starts the stack on first boot and each reboot.
set -euo pipefail
export PATH="$PATH:/snap/bin"
install_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

die() {
  printf 'cairn-quick: %s\n' "$*" >&2
  exit 1
}

install_host() {
  local cloud=$1 codename
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o Acquire::Retries=3 update
  apt-get -y install ca-certificates curl iptables

  if [[ "$cloud" == aws ]]; then
    snap list amazon-ssm-agent >/dev/null 2>&1 || snap install amazon-ssm-agent --classic
    snap start amazon-ssm-agent
    snap list aws-cli >/dev/null 2>&1 || snap install aws-cli --classic
  fi

  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  codename=$(. /etc/os-release; printf '%s' "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get -o Acquire::Retries=3 update
  apt-get -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

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
  local versions
  local args=(--no-cli-pager --output json)
  versions=$(aws "${args[@]}" secretsmanager list-secret-version-ids \
    --secret-id "$CAIRN_SETTINGS_SECRET" --include-deprecated --max-items 1 |
    python3 -c 'import json, sys; print(len(json.load(sys.stdin).get("Versions", [])))') ||
    return 1
  [ "$versions" != 0 ] || return 0
  aws "${args[@]}" secretsmanager get-secret-value --secret-id "$CAIRN_SETTINGS_SECRET" |
    python3 -c 'import json, sys; sys.stdout.buffer.write(json.load(sys.stdin)["SecretString"].encode("utf-8"))'
}

gcp_settings() {
  local token versions
  local -a options=(-fsS --connect-timeout 5 --max-time 30)
  local url="https://secretmanager.googleapis.com/v1/$CAIRN_SETTINGS_SECRET"
  token=$(curl "${options[@]}" -H 'Metadata-Flavor: Google' \
    http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])') ||
    return 1
  [ -n "$token" ] || return 1
  # curl reads the authorization header from stdin, so the token is absent from argv.
  versions=$(printf 'Authorization: Bearer %s\n' "$token" |
    curl "${options[@]}" -H @- "$url/versions?pageSize=1" |
    python3 -c 'import json, sys; print(len(json.load(sys.stdin).get("versions", [])))') ||
    return 1
  [ "$versions" != 0 ] || return 0
  printf 'Authorization: Bearer %s\n' "$token" |
    curl "${options[@]}" -H @- "$url/versions/latest:access" |
    python3 -c 'import base64, json, sys; sys.stdout.buffer.write(base64.b64decode(json.load(sys.stdin)["payload"]["data"], validate=True))'
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
  local engine read_settings file
  engine=$(docker version --format '{{.Server.Version}}')
  dpkg --compare-versions "$engine" ge 28.3.3 || die 'Docker Engine 28.3.3 or later is required'
  case "${CAIRN_CLOUD:?}" in
    aws) read_settings=aws_settings ;;
    gcp) read_settings=gcp_settings ;;
    *) die 'unknown cloud in the bootstrap configuration' ;;
  esac

  staging=$(mktemp -d "$PWD/.bootstrap.XXXXXX")
  trap 'rm -rf -- "$staging"' EXIT
  # A successful empty version listing is the only case in which an empty store is accepted
  # without reading a version. Suppress CLI errors that could include part of a response.
  if ! "$read_settings" >"$staging/cairn.env" 2>/dev/null; then
    die "could not read the cairn settings secret on $CAIRN_CLOUD. Check the VM identity and the latest version."
  fi
  cat >"$staging/.env" <<EOF
CAIRN_DOMAIN=${CAIRN_DOMAIN:?}
ACME_EMAIL=${ACME_EMAIL:?}
CAIRN_IMAGE=${CAIRN_IMAGE:?}
COMPOSE_FILE=compose.yaml:compose.backup.yaml:compose.quick.yaml
EOF
  printf 'LITESTREAM_REPLICA_URL=%s\n' "${LITESTREAM_REPLICA_URL:?}" >"$staging/litestream.env"
  if [[ "$CAIRN_CLOUD" == aws ]]; then
    printf 'AWS_REGION=%s\n' "${AWS_REGION:?}" >>"$staging/litestream.env"
  fi
  for file in .env litestream.env cairn.env; do
    publish_env "$file"
  done

  # Compose can include a malformed env file's contents in an error.
  docker compose pull >/dev/null 2>&1 ||
    die 'Docker Compose pull failed. Check registry access, the image and the settings file syntax.'
  docker compose up -d >/dev/null 2>&1 ||
    die 'Docker Compose up failed. Check the container status and logs with Docker.'
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
