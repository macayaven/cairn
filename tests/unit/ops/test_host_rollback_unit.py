"""host.sh's rollback when the proven image is not healthy either: its line says how to tell
whether the schema is why (SchemaTooNew in cairn's journal, naming both versions) and that
going back then needs a restore, as infra/gcp/README.md says. Run from a copy of host.sh
against stand-ins (tests/unit/deploy_standins.py); the health check never passes."""

import subprocess

import pytest

from support.deploy_standins import BASH, IMAGE, MISSING_TOOLS, NEW, OLD, Standins
from support.paths import REPO_ROOT

REPO = REPO_ROOT
README = " ".join((REPO / "infra" / "gcp" / "README.md").read_text(encoding="utf-8").split())

pytestmark = pytest.mark.skipif(bool(MISSING_TOOLS), reason=f"needs {MISSING_TOOLS}")


def test_a_rollback_that_is_not_healthy_either_says_how_to_tell_the_schema_and_what_then(
    tmp_path,
):
    host = Standins(tmp_path).host()
    host.record("image", f"{IMAGE}@{OLD}")
    host.running(OLD, healthy=False)
    done = subprocess.run(
        [
            BASH,
            str(host.script),
            "eval",
            "wait_healthy() { return 1; }; roll_back " + f"{IMAGE}@{NEW}",
        ],
        env={**host.s.env({}), "HOST_ROOT": str(host.root)},
        cwd=host.root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 1
    said = done.stdout + done.stderr
    assert (
        f"rollback to {IMAGE}@{OLD} is not healthy either: if journalctl -u cairn shows"
        f" SchemaTooNew, {IMAGE}@{NEW} changed the schema past what {IMAGE}@{OLD} runs on (the"
        " line names both versions), and going back needs a restore from before the upgrade;"
        " otherwise look at journalctl -u cairn for why"
    ) in said
    assert "SchemaTooNew" in README and "a restore" in README
