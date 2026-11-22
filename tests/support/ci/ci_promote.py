"""Shared test helpers extracted from test_ci_promote_unit."""

import hashlib
import subprocess
import tarfile
from pathlib import Path

import yaml

from support.ci.ci_workflow import JOBS
from support.ci_shell import run_step
from support.paths import REPO_ROOT

REPO = REPO_ROOT

RECEIVE = "Push the build to a registry on the runner"

ALIAS = "Point latest and each X.Y at the highest release"

STAGE_ACTION = yaml.safe_load(
    (REPO / ".github" / "actions" / "stage-oci-image" / "action.yml").read_text(encoding="utf-8")
)

STAGE_STEPS = {s["name"]: s for s in STAGE_ACTION["runs"]["steps"]}


def step(job: str, name: str) -> dict:
    (found,) = [s for s in JOBS[job]["steps"] if s.get("name") == name]
    return found


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


ORAS = r"""#!/bin/bash
# oras against a registry kept as files: $REG/<tag> holds the manifest's bytes.
printf '%s\n' "$*" >> "$CALLS"
by_digest() {
  for f in "$REG"/*; do
    [ -e "$f" ] || continue
    sum="$(sha256sum < "$f")"
    if [ "sha256:${sum%% *}" = "$1" ]; then printf '%s\n' "$f"; return 0; fi
  done
  return 1
}
args=()
for a in "$@"; do
  case "$a" in
    --plain-http|--to-plain-http|--from-oci-layout|--to-oci-layout) ;;
    *) args+=("$a") ;;
  esac
done
case "${args[0]} ${args[1]}" in
  "repo tags")
    [ -z "$TAGS_FAIL" ] || exit 1
    ls "$REG" ;;
  "manifest fetch-config")
    if [ -n "$IMAGE_CONFIG" ]; then
      printf '%s' "$IMAGE_CONFIG"
    else
      ref="${args[2]}"
      mf="$(by_digest "${ref#*@}")" || exit 1
      cfg="$(python3 -c 'import json, sys
print(json.load(open(sys.argv[1]))["config"]["digest"])' "$mf")"
      cf="$(by_digest "$cfg")" || exit 1
      cat "$cf"
    fi ;;
  "manifest fetch")
    ref="${args[2]}"
    if [ -n "$IMAGE_MANIFEST" ] && [ "$ref" = "$IMAGE" ]; then
      printf '%s' "$IMAGE_MANIFEST"; exit 0
    fi
    if [[ $ref == *@* ]]; then f="$(by_digest "${ref#*@}")" || exit 1; cat "$f"
    elif [ -n "$FETCH_OTHER" ]; then printf 'other bytes'
    else [ -e "$REG/${ref##*:}" ] || exit 1; cat "$REG/${ref##*:}"; fi ;;
  "cp "*)
    if [[ " $* " == *" --to-oci-layout "* ]]; then
      # registry -> a layout directory DIR:REF: the layout $ORAS_LAYOUT holds, written as
      # oras 1.3.6 writes one (test_oci_archive_unit.as_oras_writes)
      [ -n "$ORAS_LAYOUT" ] || exit 1
      dir="${args[2]%:*}"
      mkdir -p "$dir"
      cp -R "$ORAS_LAYOUT/." "$dir/"
    else
      printf '%s' "$BUILT" > "$REG/${args[2]##*:}"
    fi ;;
  "tag "*)
    f="$(by_digest "${args[1]#*@}")" || exit 1
    cp "$f" "$REG/${args[2]}" ;;
  *) exit 2 ;;
esac
"""

GH = r"""#!/bin/bash
printf 'gh %s\n' "$*" >> "$CALLS"
if [ "$1" = api ]; then
  # the head of main, as GET repos/OWNER/REPO/branches/main's commit.sha gives it
  [ -z "$HEAD_FAILS" ] || exit 1
  printf '%s\n' "${MAIN_HEAD:-$GITHUB_SHA}"
  exit 0
fi
cat "$RELEASED"
"""

SHA256SUM = (
    '#!/bin/sh\nif [ -x /usr/bin/sha256sum ]; then exec /usr/bin/sha256sum "$@"; fi\n'
    'exec shasum -a 256 "$@"\n'
)


class Runner:
    """A runner's temp directory, a registry as files, and the stand-ins on PATH."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.reg = tmp_path / "registry"
        self.reg.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, text in (("oras", ORAS), ("gh", GH), ("sha256sum", SHA256SUM)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        for name in ("docker", "curl", "sleep"):  # the local registry is the files above
            (self.bin / name).write_text(
                '#!/bin/sh\nprintf "%s %s\\n" "$(basename "$0")" "$*" >> "$CALLS"\n'
            )
            (self.bin / name).chmod(0o755)
        self.calls = tmp_path / "calls"
        self.calls.write_text("")
        self.built = b'{"manifests":"the build"}'
        image = tmp_path / "image"
        image.mkdir()
        (tmp_path / "index.json").write_text("{}")
        with tarfile.open(image / "cairn.oci.tar", "w") as archive:
            archive.add(tmp_path / "index.json", arcname="index.json")
        self.archive = hashlib.sha256((image / "cairn.oci.tar").read_bytes()).hexdigest()

    def put(self, tag: str, data: bytes) -> None:
        (self.reg / tag).write_bytes(data)

    def tags(self) -> dict[str, str]:
        return {path.name: digest(path.read_bytes()) for path in self.reg.iterdir()}

    def oras_calls(self) -> list[str]:
        return [
            line
            for line in self.calls.read_text().splitlines()
            if not line.startswith(("gh ", "docker ", "curl ", "sleep "))
        ]

    def run(self, run: str, **env: str) -> subprocess.CompletedProcess:
        output = self.tmp / "github_output"
        output.write_text("")
        base = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "RUNNER_TEMP": str(self.tmp),
            "REG": str(self.reg),
            "CALLS": str(self.calls),
            "BUILT": self.built.decode(),
            "DIGEST": digest(self.built),
            "ARCHIVE": self.archive,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_REPOSITORY": "Owner/Repo",
        }
        return run_step(
            run,
            cwd=self.tmp,
            env={**base, **env},
        )


def run_aliases(runner: Runner, released: list[str]) -> subprocess.CompletedProcess:
    listed = runner.tmp / "released"
    listed.write_text("".join(f"{tag}\n" for tag in released))
    return runner.run(step("aliases", ALIAS)["run"], RELEASED=str(listed))


def manifest(version: str) -> bytes:
    return f"the index of {version}".encode()
