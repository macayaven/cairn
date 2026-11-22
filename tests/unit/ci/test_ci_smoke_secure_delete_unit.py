"""The shipped runtime keeps secure deletion: scripts/smoke_container.py asks the image's own
SQLite, with cairn's own probe, whether FTS5 takes `secure-delete`, and fails in one sentence
when it does not (the unit test that would notice, test_erasure_unit.py, skips on such a
SQLite instead of failing)."""

import importlib.util
import json
from pathlib import Path

import pytest

from cairn.store.schema import _FTS5_SECURE_DELETE
from support.paths import REPO_ROOT

REPO = REPO_ROOT
_spec = importlib.util.spec_from_file_location(
    "smoke_container_secure_delete", REPO / "scripts" / "smoke_container.py"
)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


def test_the_probe_agrees_with_the_one_the_erasure_tests_skip_on():
    """Run here, against this machine's SQLite: what the probe reports is cairn's own flag,
    and SQLite takes the option exactly when that flag says it does."""
    found = smoke.fts5_secure_delete()
    assert found["cairn"] is _FTS5_SECURE_DELETE
    assert found["fts5"] is _FTS5_SECURE_DELETE
    assert isinstance(found["sqlite"], str) and found["sqlite"]


def test_the_probe_runs_as_a_program_of_its_own(tmp_path):
    """What the container runs is the function's own source, then one JSON line."""
    namespace: dict = {}
    printed = []
    exec(smoke.SECURE_DELETE_PROBE, {"print": printed.append, **namespace})
    assert json.loads(printed[0]) == smoke.fts5_secure_delete()


class FakeSmoke(smoke.Smoke):
    def __init__(self):  # no docker, no temporary directory
        self.names = ["cairn-smoke-test-1"]


@pytest.mark.parametrize(
    "found",
    [
        {"sqlite": "3.40.1", "cairn": False, "fts5": False},
        {"sqlite": "3.46.1", "cairn": True, "fts5": False},
        {"sqlite": "3.46.1", "cairn": False, "fts5": True},
    ],
)
def test_the_smoke_check_fails_in_one_sentence_without_secure_delete(monkeypatch, found):
    asked = []

    def docker(*args, check=True):
        asked.append(args)
        return json.dumps(found)

    monkeypatch.setattr(smoke, "docker", docker)
    with pytest.raises(smoke.Failed) as failed:
        FakeSmoke().overwrites_what_the_index_drops()
    message = str(failed.value)
    assert message.count(". ") == 0 and message.endswith(".")
    assert "secure-delete" in message and found["sqlite"] in message
    assert asked == [("exec", "cairn-smoke-test-1", "python", "-c", smoke.SECURE_DELETE_PROBE)]


def test_the_smoke_check_passes_when_both_say_yes(monkeypatch):
    found = {"sqlite": "3.46.1", "cairn": True, "fts5": True}
    monkeypatch.setattr(smoke, "docker", lambda *args, check=True: json.dumps(found))
    assert "3.46.1" in FakeSmoke().overwrites_what_the_index_drops()


def test_the_smoke_run_includes_the_check():
    source = Path(smoke.__file__).read_text(encoding="utf-8")
    start = "checks: list[Callable[[], str]] = ["
    checks = source[source.index(start) + len(start) :]
    assert "smoke.overwrites_what_the_index_drops," in checks[: checks.index("]")]
