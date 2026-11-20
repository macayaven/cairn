"""A workflow step's `run`, run as GitHub runs it, for the tests that run one.

GitHub's workflow syntax reference (`defaults.run.shell`): with no shell named, a step on Linux
or macOS runs as `bash -e {0}`, without pipefail; `shell: bash` runs as
`bash --noprofile --norc -eo pipefail {0}`. The workflow names no shell and sets no defaults
(test_ci_workflow_unit checks this), so every step runs as `bash -e`, and a test that added
pipefail itself would hide a pipe whose first command fails.

PATH is the test's: the stand-ins' directory first, then the system's own directories only when
the step needs a real tool from them (jq, tar, cmp, python3 and the like, on macOS and on
GitHub's Ubuntu image alike). A test of what a step does when a tool is missing gives a PATH
that holds nothing else, since GitHub's Ubuntu image has gh, docker and skopeo in /usr/bin.
"""

import subprocess
from pathlib import Path

SHELLS = {
    None: ("/bin/bash", "-e"),
    "bash": ("/bin/bash", "--noprofile", "--norc", "-eo", "pipefail"),
}
SYSTEM = "/usr/bin:/bin"


def run_step(
    run: str,
    env: dict[str, str],
    cwd: Path | str | None = None,
    shell: str | None = None,
    args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*SHELLS[shell], "-c", run, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# gh for a test of what the last step of job `release` asks of it on a first attempt. GitHub
# lists no release for the tag until `gh release create` is asked for one; then that one, with
# the title and the text the step gave it and the files the step holds, a draft until the
# publication is sent. The listing and the read of the release by its id both answer through
# the step's own --jq expression (its last argument). Each write's arguments are kept, one a
# line, in $RUNNER_TEMP/gh-create and $RUNNER_TEMP/gh-publish.
FIRST_ATTEMPT_GH = r"""#!/bin/sh
given() {  # the argument of the creation that follows $1
  awk -v flag="$1" 'found { print; exit } $0 == flag { found = 1 }' "$RUNNER_TEMP/gh-create"
}
release() {  # the release the creation asked for, as GitHub holds it now
  draft=true
  [ ! -e "$RUNNER_TEMP/gh-publish" ] || draft=false
  pre=false
  ! grep -qx -- --prerelease "$RUNNER_TEMP/gh-create" || pre=true
  ls cairn_memory-*.whl cairn_memory-*.tar.gz SHA256SUMS | jq -R '{name: .}' | jq -s \
    --arg tag "$(sed -n 3p "$RUNNER_TEMP/gh-create")" --arg title "$(given --title)" \
    --rawfile body "$(given --notes-file)" --argjson draft "$draft" --argjson pre "$pre" \
    '{id: 7, tag_name: $tag, name: $title, draft: $draft, prerelease: $pre, body: $body,
      assets: .}'
}
case "$1 $2" in
  "release create") printf '%s\n' "$@" > "$RUNNER_TEMP/gh-create" ;;
  "api --method") printf '%s\n' "$@" > "$RUNNER_TEMP/gh-publish" ;;
  "release download") mkdir "$5" && cp cairn_memory-*.whl cairn_memory-*.tar.gz SHA256SUMS "$5"/ ;;
  "api --paginate")
    [ -e "$RUNNER_TEMP/gh-create" ] || exit 0
    while [ $# -gt 1 ]; do shift; done
    release | jq -s . | jq -r "$1" ;;
  "api repos/owner/repo/releases/7")
    [ "$3" = --jq ] && [ $# = 4 ] || exit 2
    release | jq -r "$4" ;;
  *) exit 2 ;;
esac
"""


def first_attempt_gh(bin_: Path) -> None:
    """The stand-in, and a `sleep` that fails the step: this GitHub lists each write at once,
    so the step has nothing to wait for, and a test never sleeps."""
    (bin_ / "gh").write_text(FIRST_ATTEMPT_GH)
    (bin_ / "gh").chmod(0o755)
    (bin_ / "sleep").write_text("#!/bin/sh\nexit 1\n")
    (bin_ / "sleep").chmod(0o755)
