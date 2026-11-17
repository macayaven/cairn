"""scripts/deploy_production.sh, the real script, against stand-in skopeo, curl, gcloud, sleep.

Each test prepares what the registry holds and what the VM, GitHub or the public address
answer (tests/unit/deploy_standins.py), runs one subcommand with the system bash, and checks
what infra/gcp/README.md ("Deploy from GitHub Actions", "Rollback") says it does. Every run
also checks that the project id, the account email, the registry path and the tokens are
never printed, and that no token is ever in a command's arguments.
"""

import pytest
from deploy_standins import (
    COMMIT,
    IMAGE,
    MISSING_TOOLS,
    NEW,
    NEWER_COMMIT,
    OLD,
    PUBLIC_URL,
    SOURCE,
    Standins,
    digest,
    manifest,
    status_line,
)

pytestmark = pytest.mark.skipif(
    bool(MISSING_TOOLS), reason=f"the deploy script needs {', '.join(MISSING_TOOLS)}"
)


@pytest.fixture
def s(tmp_path):
    return Standins(tmp_path)


def failed(result, message: str) -> None:
    """A failure branch: exit 1 and the script's own message, not a set -e death."""
    assert result.rc == 1, result.text
    errors = [line for line in result.err.splitlines() if line.startswith("deploy: ERROR: ")]
    assert any(message in line for line in errors), result.text


# --- copy ---------------------------------------------------------------------------------


def test_copy_copies_the_digest_under_the_commit_tag_and_reads_it_back(s):
    s.put(f"{SOURCE.lower()}@{NEW}", manifest("new"))
    result = s.deploy("copy", NEW)
    assert result.rc == 0, result.text
    assert f"deploy: Artifact Registry holds {NEW} as cairn:sha-{COMMIT}" in result.out
    assert s.holds(f"{IMAGE}:sha-{COMMIT}") == manifest("new")
    copy = next(argv for argv in s.calls("skopeo") if argv[0] == "copy")
    assert {"--all", "--preserve-digests"} <= set(copy)
    assert copy[-2:] == [f"docker://{SOURCE.lower()}@{NEW}", f"docker://{IMAGE}:sha-{COMMIT}"]


def test_copy_fails_when_the_copy_has_another_digest(s):
    s.put(f"{SOURCE.lower()}@{NEW}", manifest("new"))
    s.set_file("skopeo-copy-writes", manifest("tampered").decode())
    result = s.deploy("copy", NEW)
    failed(result, f"cairn:sha-{COMMIT} is {digest('tampered')} after the copy, expected {NEW}")


@pytest.mark.parametrize("status", [1, 3])
def test_a_skopeo_failure_is_a_fixed_message_with_its_exit_status_and_nothing_it_printed(s, status):
    s.put(f"{SOURCE.lower()}@{NEW}", manifest("new"))
    s.set_file("skopeo-copy-fails", str(status))
    result = s.deploy("copy", NEW)
    failed(result, f"copy to Artifact Registry failed (skopeo exit {status})")
    assert "level=fatal" not in result.text  # skopeo's own words, which name the registry


def test_copy_fails_when_the_copy_cannot_be_read_back(s):
    s.put(f"{SOURCE.lower()}@{NEW}", manifest("new"))
    s.set_file("skopeo-inspect-fails", f"{IMAGE}:sha-{COMMIT}")
    failed(s.deploy("copy", NEW), f"could not read back cairn:sha-{COMMIT} (skopeo exit 2)")


def test_copy_refuses_what_is_not_a_digest(s):
    failed(s.deploy("copy", "latest"), "'latest' is not a digest")
    assert s.calls("skopeo") == []


# --- check-head ---------------------------------------------------------------------------


def github(sha: str) -> tuple[str, int]:
    return (f'{{"name": "main", "commit": {{"sha": "{sha}"}}}}', 0)


def test_check_head_refuses_a_run_that_is_not_the_head_of_main(s):
    s.answers("github", github(NEWER_COMMIT))
    result = s.deploy("check-head", COMMIT)
    failed(result, f"this run is for commit {COMMIT}, but main is now at {NEWER_COMMIT}.")
    assert "re-running an old run is not a rollback" in result.err


def test_check_head_passes_the_head_of_main_and_sends_the_token_on_stdin(s):
    s.answers("github", github(COMMIT))
    result = s.deploy("check-head", COMMIT)
    assert result.rc == 0, result.text
    assert f"deploy: commit {COMMIT} is the head of main" in result.out
    assert s.calls("curl")[0][-1].endswith("/repos/example-owner/cairn/branches/main")
    assert any("fake-github-token" in stdin for stdin in s.curl_stdin())


def test_check_head_refuses_what_is_not_a_commit_sha(s):
    failed(s.deploy("check-head", "main"), "'main' is not a commit SHA")
    assert s.calls("curl") == []


def test_check_head_fails_when_github_cannot_be_read(s):
    s.answers("github", ("", 22, "curl: (22) The requested URL returned error: 403\n"))
    failed(s.deploy("check-head", COMMIT), "could not read the head of main")


# --- promote ------------------------------------------------------------------------------


def test_promote_moves_the_tag_and_logs_the_digest_it_replaced(s):
    s.put(f"{IMAGE}@{NEW}", manifest("new"))
    s.put(f"{IMAGE}:production", manifest("old"))
    result = s.deploy("promote", NEW)
    assert result.rc == 0, result.text
    assert f"deploy: cairn:production moved from {OLD} to {NEW}" in result.out
    assert f"point cairn:production at {OLD} again" in result.out
    assert s.holds(f"{IMAGE}:production") == manifest("new")


def test_promote_on_a_first_deploy_moves_the_tag_from_none(s):
    s.put(f"{IMAGE}@{NEW}", manifest("new"))
    result = s.deploy("promote", NEW)
    assert result.rc == 0, result.text
    assert f"deploy: cairn:production moved from none to {NEW}" in result.out
    assert "to go back" not in result.out


def test_promote_refuses_a_digest_the_registry_lacks_before_touching_the_tag(s):
    s.put(f"{IMAGE}:production", manifest("old"))
    failed(s.deploy("promote", NEW), f"Artifact Registry does not hold {NEW}")
    assert not any(argv[0] == "copy" for argv in s.calls("skopeo"))
    assert s.holds(f"{IMAGE}:production") == manifest("old")


def test_promote_refuses_a_digest_the_registry_serves_other_bytes_for(s):
    s.put(f"{IMAGE}@{NEW}", manifest("tampered"))
    failed(s.deploy("promote", NEW), f"Artifact Registry served {digest('tampered')} for {NEW}")
    assert not any(argv[0] == "copy" for argv in s.calls("skopeo"))


@pytest.mark.parametrize("status", [1, 4])
def test_promote_fails_when_skopeo_cannot_move_the_tag(s, status):
    s.put(f"{IMAGE}@{NEW}", manifest("new"))
    s.set_file("skopeo-copy-fails", str(status))
    failed(s.deploy("promote", NEW), f"could not move cairn:production (skopeo exit {status})")


def test_promote_fails_when_the_tag_names_another_digest_after_the_move(s):
    s.put(f"{IMAGE}@{NEW}", manifest("new"))
    s.set_file("skopeo-copy-writes", manifest("tampered").decode())
    failed(
        s.deploy("promote", NEW),
        f"cairn:production names {digest('tampered')} after the move, expected {NEW}",
    )


def test_promote_fails_when_the_tag_cannot_be_read_back(s):
    s.put(f"{IMAGE}@{NEW}", manifest("new"))
    s.set_file("skopeo-inspect-fails", f"{IMAGE}:production")
    failed(s.deploy("promote", NEW), "could not read back cairn:production (skopeo exit 2)")


def test_promote_leaves_a_tag_that_already_names_the_digest(s):
    s.put(f"{IMAGE}@{NEW}", manifest("new"))
    s.put(f"{IMAGE}:production", manifest("new"))
    result = s.deploy("promote", NEW)
    assert result.rc == 0, result.text
    assert f"deploy: cairn:production already names {NEW}" in result.out
    assert "moved" not in result.out
    assert not any(argv[0] == "copy" for argv in s.calls("skopeo"))


# --- seq and status -----------------------------------------------------------------------


def test_seq_is_the_number_of_the_latest_report(s):
    s.statuses(status_line(41))
    assert s.deploy("seq").out == "41\n"


@pytest.mark.parametrize("prepared", ["unreadable", "no seq"])
def test_seq_is_zero_while_no_numbered_report_can_be_read(s, prepared):
    if prepared == "no seq":
        s.statuses(status_line(None))
    result = s.deploy("seq")
    assert (result.rc, result.out) == (0, "0\n"), result.text


def test_status_without_a_token_asks_gcloud_for_one(s):
    s.statuses(status_line(7))
    result = s.deploy("status", GCP_ACCESS_TOKEN=None)
    assert result.rc == 0, result.text
    assert result.out == status_line(7) + "\n"
    assert s.calls("gcloud") == [["auth", "print-access-token"]]
    assert any("fake-gcp-access-token" in stdin for stdin in s.curl_stdin())


def test_status_fails_when_nothing_can_be_read(s):
    failed(s.deploy("status"), "could not read the VM's status")


# --- wait ---------------------------------------------------------------------------------


def wait(s, after="5", timeout="60", **env):
    return s.deploy("wait", NEW, CAIRN_AFTER_SEQ=after, CAIRN_DEPLOY_TIMEOUT=timeout, **env)


def test_wait_succeeds_on_a_later_report_that_runs_proves_and_serves_the_digest(s):
    s.statuses(
        status_line(5, running=OLD, healthy="yes", proven=OLD),
        status_line(6, running=NEW, healthy="no", proven=OLD, pending=NEW),
        status_line(7, running=NEW, healthy="yes", proven=NEW),
    )
    result = wait(s)
    assert result.rc == 0, result.text
    assert f"deploy: deploying: {NEW} is in its health check" in result.out
    assert result.out.endswith(f"deploy: the VM runs {NEW} and /healthz answers\n")


@pytest.mark.parametrize(
    "line",
    [
        status_line(6, running=NEW, healthy="yes", proven=OLD, pending=NEW),
        status_line(6, running=NEW, healthy="no", proven=NEW),
        status_line(6, running=OLD, healthy="yes", proven=NEW),
    ],
    ids=["not-promoted", "not-healthy", "not-running"],
)
def test_wait_needs_running_proven_and_healthy_all_three(s, line):
    s.statuses(line)
    result = wait(s, timeout="0")
    failed(result, f"the VM did not report {NEW} running and healthy within 0 s.")


def test_wait_fails_at_once_on_a_rollback_to_a_healthy_earlier_image(s):
    line = status_line(6, running=OLD, healthy="yes", proven=OLD, failed=NEW)
    s.statuses(line, status_line(7, running=NEW, healthy="yes", proven=NEW))
    result = wait(s)
    failed(result, f"the VM rolled back: {NEW} failed its health check")
    assert f"the VM runs the earlier image {OLD}, healthy." in result.err
    assert f"Status: {line}" in result.err
    assert len(s.calls("curl")) == 1  # at once: the later success is never read


def test_wait_fails_at_once_on_a_rollback_where_nothing_healthy_runs(s):
    s.statuses(status_line(6, running=OLD, healthy="no", proven=OLD, failed=NEW))
    result = wait(s)
    failed(result, f"the VM rolled back from {NEW} and nothing healthy runs now.")
    assert "SchemaTooNew" in result.err


def test_wait_says_how_to_retry_an_image_the_updater_skipped(s):
    """The updater skips a digest that failed before; its report names it under failed."""
    s.statuses(status_line(6, running=OLD, healthy="yes", proven=OLD, failed=NEW))
    result = wait(s)
    failed(result, "the VM rolled back:")
    assert "or was skipped because it failed before" in result.err
    assert "remove /mnt/disks/data/deploy/failed-image on the VM" in result.err


@pytest.mark.parametrize("seq", [5, 4])
def test_a_report_numbered_at_or_before_the_start_decides_nothing(s, seq):
    for stale in (
        status_line(seq, running=NEW, healthy="yes", proven=NEW),
        status_line(seq, running=OLD, healthy="no", proven=OLD, failed=NEW),
        status_line(seq, running=OLD, healthy="yes", proven=OLD, hold="yes"),
    ):
        s.statuses(stale)
        result = wait(s, timeout="0")
        failed(result, f"within 0 s. Last seen: no report after #5 yet; the latest: {stale}")


def test_a_stale_report_then_a_later_one_decides_on_the_later_one(s):
    s.statuses(
        status_line(5, running=NEW, healthy="yes", proven=NEW),
        status_line(6, running=NEW, healthy="yes", proven=NEW),
    )
    result = wait(s)
    assert result.rc == 0, result.text
    assert len(s.calls("curl")) == 2


def test_no_status_readable_decides_nothing_and_says_so_on_timeout(s):
    s.answers("status", ("", 22, "curl: (22) The requested URL returned error: 403\n"))
    result = wait(s, timeout="0")
    failed(result, "Last seen: no status could be read (is enable-guest-attributes set")


def test_a_report_without_a_sequence_number_decides_nothing(s):
    line = status_line(None, running=NEW, healthy="yes", proven=NEW)
    s.statuses(line)
    result = wait(s, timeout="0")
    failed(result, f"Last seen: a status without a sequence number (an older host.sh?): {line}")


def test_wait_fails_at_once_on_a_newer_report_of_a_hold(s):
    line = status_line(6, running=OLD, healthy="yes", proven=OLD, hold="yes")
    s.statuses(line, status_line(7, running=NEW, healthy="yes", proven=NEW))
    result = wait(s)
    failed(result, f"deployments are on hold on the VM (host.sh hold), so it will not deploy {NEW}")
    assert len(s.calls("curl")) == 1


def test_on_timeout_wait_prints_the_last_report_it_read(s):
    last = status_line(9, running=OLD, healthy="yes", proven=OLD)
    s.statuses(last)
    failed(wait(s, timeout="0"), f"within 0 s. Last seen: {last}")


def test_wait_reads_every_15_seconds_by_default_and_for_900_seconds(s):
    s.statuses(
        status_line(6, running=OLD, healthy="yes", proven=OLD),
        status_line(7, running=NEW, healthy="yes", proven=NEW),
    )
    result = s.deploy("wait", NEW, CAIRN_AFTER_SEQ="5")
    assert result.rc == 0, result.text
    assert f"deploy: waiting up to 900 s for a report after #5 that the VM runs {NEW}" in result.out
    assert s.calls("sleep") == [["15"]]


def test_wait_without_a_starting_number_reads_one_first(s):
    """As the guide's rollback by hand runs it: no CAIRN_AFTER_SEQ."""
    s.statuses(
        status_line(7, running=NEW, healthy="yes", proven=NEW),  # read as the start
        status_line(7, running=NEW, healthy="yes", proven=NEW),
        status_line(8, running=NEW, healthy="yes", proven=NEW),
    )
    result = s.deploy("wait", NEW, CAIRN_AFTER_SEQ=None, CAIRN_DEPLOY_TIMEOUT="60")
    assert result.rc == 0, result.text
    assert "a report after #7" in result.out
    assert len(s.calls("curl")) == 3


def test_wait_refuses_a_starting_number_that_is_not_one(s):
    failed(wait(s, after="five"), "CAIRN_AFTER_SEQ must be a number")


# --- check-url ----------------------------------------------------------------------------


def test_check_url_succeeds_on_a_200_over_https_without_following_redirects(s):
    s.answers("healthz", ("000", 7), ("301", 0), ("200", 0))
    result = s.deploy("check-url", CAIRN_URL_ATTEMPTS="5", CAIRN_URL_INTERVAL="1")
    assert result.rc == 0, result.text
    assert f"deploy: {PUBLIC_URL}/healthz answers 200 over TLS" in result.out
    calls = s.calls("curl")
    assert len(calls) == 3
    for argv in calls:
        assert argv[-1] == f"{PUBLIC_URL}/healthz"
        assert argv[argv.index("--proto") + 1] == "=https"
        assert not {"-L", "--location"} & set(argv)


def test_check_url_stops_after_ten_tries_six_seconds_apart(s):
    s.answers("healthz", ("503", 0))
    result = s.deploy("check-url")
    failed(result, f"{PUBLIC_URL}/healthz did not answer 200 over TLS after 10 tries")
    assert len(s.calls("curl")) == 10
    assert s.calls("sleep") == [["6"]] * 9


def test_check_url_stops_after_the_tries_it_is_given(s):
    s.answers("healthz", ("000", 7))
    result = s.deploy("check-url", CAIRN_URL_ATTEMPTS="3", CAIRN_URL_INTERVAL="2")
    failed(result, f"{PUBLIC_URL}/healthz did not answer 200 over TLS after 3 tries")
    assert f"deploy: {PUBLIC_URL}/healthz: 000 (try 3 of 3)" in result.out
    assert len(s.calls("curl")) == 3
    assert s.calls("sleep") == [["2"], ["2"]]


def test_check_url_refuses_an_address_with_a_path(s):
    failed(
        s.deploy("check-url", CAIRN_PUBLIC_URL=f"{PUBLIC_URL}/app"),
        "CAIRN_PUBLIC_URL must be https://HOST, without a path",
    )
    assert s.calls("curl") == []


# --- settings and usage -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "unset"),
    [
        (("copy", NEW), "CAIRN_SOURCE_IMAGE"),
        (("promote", NEW), "CAIRN_ARTIFACT_REPOSITORY"),
        (("check-head", COMMIT), "GITHUB_TOKEN"),
        (("wait", NEW), "GCP_INSTANCE"),
        (("check-url",), "CAIRN_PUBLIC_URL"),
    ],
)
def test_a_missing_setting_is_named(s, args, unset):
    failed(s.deploy(*args, **{unset: None}), f"{unset} is not set")


@pytest.mark.parametrize(
    "args", [("wait",), ("copy", NEW, OLD), ("status", "x"), ("check-url", "x")]
)
def test_a_wrong_number_of_arguments_is_refused(s, args):
    failed(s.deploy(*args), "usage: ")


def test_an_unknown_command_prints_the_usage(s):
    result = s.deploy("deploy")
    assert result.rc == 2
    assert "usage:" in result.err
