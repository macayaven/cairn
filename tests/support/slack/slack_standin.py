"""Shared test helpers extracted from test_slack_standin_unit."""

from importlib.util import module_from_spec, spec_from_file_location

from support.paths import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts/slack_standin.py"


def fixture_module():
    spec = spec_from_file_location("slack_standin", SCRIPT)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
