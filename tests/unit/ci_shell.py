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
