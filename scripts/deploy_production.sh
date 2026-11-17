#!/bin/bash
# Deploy one published cairn image to the hosted VM, by digest, and wait until the VM says it
# runs it healthy. The deploy job in .github/workflows/ci-cd.yml calls it; a person can call
# `status` and `wait` too (infra/gcp/README.md, "Deploy").
#
#   copy DIGEST     copy SOURCE_IMAGE@DIGEST, every platform and attestation, into Artifact
#                   Registry under COPY_TAG, and check that the copy has the same digest
#   check-head SHA  fail unless SHA is the current head of main: a re-run of an old run must
#                   not deploy its old image over newer commits
#   promote DIGEST  point the deploy tag at DIGEST, which must already be in Artifact
#                   Registry, and check that it does
#   seq             print the sequence number of the VM's latest status (0 if there is none)
#   wait DIGEST     poll the VM's status until a report newer than CAIRN_AFTER_SEQ says it
#                   runs DIGEST and is healthy. Fails at once when such a report says the VM
#                   rolled back from DIGEST or is on hold, and after CAIRN_DEPLOY_TIMEOUT
#                   seconds otherwise; either way it prints the last status it read
#   check-url       require GET CAIRN_PUBLIC_URL/healthz to answer 200 over TLS, with bounded
#                   retries: a check from outside that does not trust the VM's own report
#   status          print the VM's status once
#
# Settings, all from the environment:
#   CAIRN_ARTIFACT_REPOSITORY  REGION-docker.pkg.dev/PROJECT/REPOSITORY (copy, promote)
#   CAIRN_SOURCE_IMAGE         the published image without tag, e.g. ghcr.io/OWNER/REPO (copy)
#   CAIRN_COPY_TAG             the tag the copy gets in Artifact Registry, e.g. sha-0123abc (copy)
#   CAIRN_DEPLOY_TAG           the tag the VM follows; default production (promote)
#   GCP_PROJECT_ID, GCP_ZONE, GCP_INSTANCE  the VM (wait, status)
#   GCP_ACCESS_TOKEN           OAuth token for the Compute API; without it, `gcloud auth
#                              print-access-token` is asked (wait, status)
#   CAIRN_DEPLOY_TIMEOUT       seconds `wait` waits; default 900
#   CAIRN_AFTER_SEQ            `seq` as read before the tag moved; only later reports count.
#                              Without it, `wait` reads it itself when it starts
#   CAIRN_POLL_SECONDS         seconds between reads; default 15 (the API allows 10 a minute)
#   GITHUB_REPOSITORY, GITHUB_TOKEN  the repository and a token that reads it (check-head)
#   CAIRN_PUBLIC_URL           https://DOMAIN, the public address (check-url)
#   CAIRN_URL_ATTEMPTS, CAIRN_URL_INTERVAL  tries and seconds between them; default 10 and 6
#
# Registry credentials come from the Docker config (docker login), which skopeo reads.
# Nothing here prints the repository path, the project or a token: messages name tags and
# digests only. skopeo's own messages name the registry path, so they are dropped, and a
# failure is reported as a fixed message with skopeo's exit status. To see skopeo's message,
# run the same command by hand with your own credentials. Needs bash, curl, jq, sha256sum and skopeo (all on GitHub's Ubuntu runners).

set -euo pipefail

IMAGE_NAME=cairn
STATUS_ATTRIBUTE=cairn/status

log() { echo "deploy: $*"; }
fail() {
  echo "deploy: ERROR: $*" >&2
  exit 1
}

need() {
  local name
  for name in "$@"; do
    [ -n "${!name:-}" ] || fail "$name is not set"
  done
}

check_digest() {
  [[ $1 =~ ^sha256:[0-9a-f]{64}$ ]] || fail "'$1' is not a digest (sha256: and 64 hex digits)"
}

image() { printf '%s/%s' "$CAIRN_ARTIFACT_REPOSITORY" "$IMAGE_NAME"; }

# skopeo ARGS... with its stderr dropped (it names the registry path); returns its status.
skopeo_quiet() {
  skopeo "$@" 2>/dev/null
}

# The digest of what REF names: the sha256 of the manifest (or index) bytes as served.
# Fails with skopeo's exit status.
digest_of() {
  local sum rc=0
  sum=$(skopeo_quiet inspect --raw --retry-times 3 "docker://$1" | sha256sum) || rc=$?
  [ "$rc" -eq 0 ] || return "$rc"
  printf 'sha256:%s' "${sum%% *}"
}

cmd_copy() {
  local digest=$1 src target have
  need CAIRN_ARTIFACT_REPOSITORY CAIRN_SOURCE_IMAGE CAIRN_COPY_TAG
  check_digest "$digest"
  # Registries want lowercase repository names; GitHub owner names may have capitals.
  src="$(printf '%s' "$CAIRN_SOURCE_IMAGE" | tr '[:upper:]' '[:lower:]')@$digest"
  target="$(image):$CAIRN_COPY_TAG"
  log "copying $digest into Artifact Registry as $IMAGE_NAME:$CAIRN_COPY_TAG"
  # --all keeps the index with every platform and the attestations; --preserve-digests makes
  # skopeo fail instead of writing anything whose digest would differ from the source's.
  skopeo_quiet copy --quiet --all --preserve-digests --retry-times 3 \
    "docker://$src" "docker://$target" ||
    fail "copy to Artifact Registry failed (skopeo exit $?)"
  have=$(digest_of "$target") ||
    fail "could not read back $IMAGE_NAME:$CAIRN_COPY_TAG (skopeo exit $?)"
  [ "$have" = "$digest" ] ||
    fail "$IMAGE_NAME:$CAIRN_COPY_TAG is $have after the copy, expected $digest"
  log "Artifact Registry holds $digest as $IMAGE_NAME:$CAIRN_COPY_TAG"
}

cmd_promote() {
  local digest=$1 tag=${CAIRN_DEPLOY_TAG:-production} before have
  need CAIRN_ARTIFACT_REPOSITORY
  check_digest "$digest"
  # Refuses a digest the registry does not hold, before the tag is touched.
  have=$(digest_of "$(image)@$digest") ||
    fail "Artifact Registry does not hold $digest, or could not be read (skopeo exit $?)"
  [ "$have" = "$digest" ] || fail "Artifact Registry served $have for $digest"
  before=$(digest_of "$(image):$tag") || before=none
  if [ "$before" = "$digest" ]; then
    log "$IMAGE_NAME:$tag already names $digest"
    return 0
  fi
  skopeo_quiet copy --quiet --all --preserve-digests --retry-times 3 \
    "docker://$(image)@$digest" "docker://$(image):$tag" ||
    fail "could not move $IMAGE_NAME:$tag (skopeo exit $?)"
  have=$(digest_of "$(image):$tag") || fail "could not read back $IMAGE_NAME:$tag (skopeo exit $?)"
  [ "$have" = "$digest" ] || fail "$IMAGE_NAME:$tag names $have after the move, expected $digest"
  log "$IMAGE_NAME:$tag moved from $before to $digest"
  if [ "$before" != none ]; then
    log "to go back, point $IMAGE_NAME:$tag at $before again (infra/gcp/README.md, \"Rollback\")"
  fi
}

cmd_check_head() {
  local sha=$1 head
  need GITHUB_REPOSITORY GITHUB_TOKEN
  [[ $sha =~ ^[0-9a-f]{40}$ ]] || fail "'$sha' is not a commit SHA"
  # The header goes to curl on stdin, so the token is never in argv.
  head=$(printf 'header = "Authorization: Bearer %s"\n' "$GITHUB_TOKEN" |
    curl -fsS --max-time 20 -K - -H 'Accept: application/vnd.github+json' \
      "${GITHUB_API_URL:-https://api.github.com}/repos/$GITHUB_REPOSITORY/branches/main" |
    jq -er '.commit.sha') || fail "could not read the head of main"
  if [ "$head" != "$sha" ]; then
    fail "this run is for commit $sha, but main is now at $head. Refusing to deploy an older image over newer commits; the run for $head deploys that one. To roll back, use the command in infra/gcp/README.md, \"Rollback\"; re-running an old run is not a rollback."
  fi
  log "commit $sha is the head of main"
}

cmd_check_url() {
  local base attempts=${CAIRN_URL_ATTEMPTS:-10} interval=${CAIRN_URL_INTERVAL:-6} i code
  need CAIRN_PUBLIC_URL
  base=${CAIRN_PUBLIC_URL%/}
  [[ $base =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]] ||
    fail "CAIRN_PUBLIC_URL must be https://HOST, without a path"
  for ((i = 1; i <= attempts; i++)); do
    # --proto =https: no plain HTTP, not even through a redirect; redirects are not followed,
    # so only a 200 from this host counts. curl checks the certificate by default.
    code=$(curl -sS --proto =https --max-time 10 -o /dev/null -w '%{http_code}' \
      "$base/healthz" 2>/dev/null) || true
    if [ "$code" = 200 ]; then
      log "$base/healthz answers 200 over TLS"
      return 0
    fi
    log "$base/healthz: ${code:-no answer} (try $i of $attempts)"
    [ "$i" -lt "$attempts" ] && sleep "$interval"
  done
  fail "$base/healthz did not answer 200 over TLS after $attempts tries; the VM's report said healthy, but the public address does not"
}

access_token() {
  if [ -n "${GCP_ACCESS_TOKEN:-}" ]; then
    printf '%s' "$GCP_ACCESS_TOKEN"
  else
    gcloud auth print-access-token 2>/dev/null
  fi
}

# Prints the VM's status line, or fails (curl's own message goes to stderr; it holds no URL).
read_status() {
  local token body
  token=$(access_token) || return 1
  [ -n "$token" ] || return 1
  # The header goes to curl on stdin (printf is a builtin), so the token is never in argv.
  body=$(printf 'header = "Authorization: Bearer %s"\n' "$token" |
    curl -fsS --max-time 20 -K - \
      "https://compute.googleapis.com/compute/v1/projects/$GCP_PROJECT_ID/zones/$GCP_ZONE/instances/$GCP_INSTANCE/getGuestAttributes?queryPath=$STATUS_ATTRIBUTE") ||
    return 1
  printf '%s' "$body" | jq -er --arg ns "${STATUS_ATTRIBUTE%%/*}" --arg key "${STATUS_ATTRIBUTE#*/}" \
    '(.queryValue.items // [] | map(select(.namespace == $ns and .key == $key)) | .[0].value)
       // .variableValue // empty'
}

# field LINE NAME: the value of NAME=... in a status line.
field() {
  local word words
  read -r -a words <<<"$1"
  # The +"..." form keeps an empty line from tripping set -u in bash before 4.4.
  for word in ${words[@]+"${words[@]}"}; do
    case "$word" in
      "$2="*)
        printf '%s' "${word#*=}"
        return 0
        ;;
    esac
  done
}

# The seq of the latest report; 0 when none can be read yet (a VM that never reported).
cmd_seq() {
  local line seq
  need GCP_PROJECT_ID GCP_ZONE GCP_INSTANCE
  line=$(read_status 2>/dev/null) || line=""
  seq=$(field "$line" seq)
  [[ $seq =~ ^[0-9]+$ ]] || seq=0
  printf '%s\n' "$seq"
}

cmd_status() {
  need GCP_PROJECT_ID GCP_ZONE GCP_INSTANCE
  read_status || fail "could not read the VM's status"
}

cmd_wait() {
  local digest=$1 timeout=${CAIRN_DEPLOY_TIMEOUT:-900} poll=${CAIRN_POLL_SECONDS:-15}
  local after deadline line last="nothing yet" seq running healthy proven pending failed
  need GCP_PROJECT_ID GCP_ZONE GCP_INSTANCE
  check_digest "$digest"
  # Only a report numbered after `after` decides anything. The VM reports every minute, so
  # such a report arrives within about a minute of the move.
  after=${CAIRN_AFTER_SEQ:-$(cmd_seq)}
  [[ $after =~ ^[0-9]+$ ]] || fail "CAIRN_AFTER_SEQ must be a number"
  deadline=$((SECONDS + timeout))
  log "waiting up to $timeout s for a report after #$after that the VM runs $digest"
  while :; do
    if line=$(read_status); then
      seq=$(field "$line" seq)
      running=$(field "$line" running)
      healthy=$(field "$line" healthy)
      proven=$(field "$line" proven)
      pending=$(field "$line" pending)
      failed=$(field "$line" failed)
      last=$line
      if [[ ! $seq =~ ^[0-9]+$ ]]; then
        last="a status without a sequence number (an older host.sh?): $line"
      elif [ "$seq" -le "$after" ]; then
        last="no report after #$after yet; the latest: $line"
      elif [ "$running" = "$digest" ] && [ "$proven" = "$digest" ] && [ "$healthy" = yes ]; then
        log "the VM runs $digest and /healthz answers"
        return 0
      elif [ "$failed" = "$digest" ]; then
        if [ "$healthy" = yes ] && [ "$running" = "$proven" ]; then
          fail "the VM rolled back: $digest failed its health check, or was skipped because it failed before (to retry it, remove /mnt/disks/data/deploy/failed-image on the VM), and the VM runs the earlier image $proven, healthy. The tag still names the failed digest. Status: $line"
        fi
        fail "the VM rolled back from $digest and nothing healthy runs now. If $digest migrated the database, the earlier image refuses it (SchemaTooNew) and the way back is a restore (infra/gcp/README.md, \"Rollback\"). Status: $line"
      elif [ "$(field "$line" hold)" = yes ]; then
        fail "deployments are on hold on the VM (host.sh hold), so it will not deploy $digest. Release the hold there (infra/gcp/README.md, \"Rollback\") and re-run this job. Status: $line"
      elif [ "$pending" = "$digest" ]; then
        log "deploying: $digest is in its health check"
      fi
    else
      last="no status could be read (is enable-guest-attributes set and the new host.sh booted?)"
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      fail "the VM did not report $digest running and healthy within $timeout s. Last seen: $last"
    fi
    sleep "$poll"
  done
}

main() {
  local command=${1:-}
  [ $# -ge 1 ] && shift
  case "$command" in
    copy | promote | wait | check-head)
      [ $# -eq 1 ] || fail "usage: $0 $command DIGEST|SHA"
      case "$command" in
        check-head) cmd_check_head "$1" ;;
        copy) cmd_copy "$1" ;;
        promote) cmd_promote "$1" ;;
        wait) cmd_wait "$1" ;;
      esac
      ;;
    status)
      [ $# -eq 0 ] || fail "usage: $0 status"
      cmd_status
      ;;
    seq)
      [ $# -eq 0 ] || fail "usage: $0 seq"
      cmd_seq
      ;;
    check-url)
      [ $# -eq 0 ] || fail "usage: $0 check-url"
      cmd_check_url
      ;;
    *)
      echo "usage: $0 copy|promote|wait DIGEST, $0 check-head SHA, or $0 status|seq|check-url" >&2
      exit 2
      ;;
  esac
}

main "$@"
