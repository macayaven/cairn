"""Shared test helpers extracted from test_ci_release_unit."""

from support.ci.ci_workflow import (
    not_a_pull_request,
)

CANDIDATE = not_a_pull_request("push", "refs/tags/v1.2.3-rc.1")
