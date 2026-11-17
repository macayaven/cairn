"""Sentences of infra/gcp/README.md and deploy/README.md about the replica alert (O3), each
pinned here so the guides cannot go false unnoticed, and tied to the file that makes it true:
infra/gcp/monitoring.tf for the alert, scripts/rehearse_stack.py for what was rehearsed,
infra/gcp/files/host.sh and deploy/compose.backup.yaml for the container."""

import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INFRA = (REPO / "infra" / "gcp" / "README.md").read_text(encoding="utf-8")
DEPLOY = (REPO / "deploy" / "README.md").read_text(encoding="utf-8")
MONITORING = (REPO / "infra" / "gcp" / "monitoring.tf").read_text(encoding="utf-8")
HOST_SH = (REPO / "infra" / "gcp" / "files" / "host.sh").read_text(encoding="utf-8")
COMPOSE_BACKUP = (REPO / "deploy" / "compose.backup.yaml").read_text(encoding="utf-8")
CLOUD_INIT = (REPO / "infra" / "gcp" / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
STACK = REPO / "scripts" / "rehearse_stack.py"
_spec = importlib.util.spec_from_file_location("rehearse_stack_for_o3", STACK)
rehearse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rehearse)


def flat(text: str) -> str:
    return " ".join(text.split())


def section(text: str, heading: str) -> str:
    """From `heading` to the next heading of the same or a higher level, flattened."""
    return flat(rehearse.guide_section(text, heading))


def says(text: str, *sentences: str) -> None:
    for sentence in sentences:
        assert flat(sentence) in text, sentence


def replica_policy() -> str:
    start = MONITORING.index('resource "google_monitoring_alert_policy" "replica"')
    return MONITORING[start:]


# infra/gcp/README.md


def test_the_table_of_what_is_made_names_the_four_alerts():
    says(
        flat(INFRA),
        "| Monitoring | an uptime check on `https://DOMAIN/healthz` and an email alert; a second"
        " email alert when Litestream logs an error; a third when the replica check stops"
        " saying the replica is current; a fourth when a compaction keeps not finishing"
        " ([Alerts](#alerts)) |",
    )
    assert MONITORING.count('resource "google_monitoring_alert_policy"') == 4


def test_the_alerts_section_says_what_each_alert_means():
    said = section(INFRA, "## Alerts")
    says(
        said,
        "All four alerts send email to `alert_email`.",
        "**The uptime alert** fires while `https://DOMAIN/healthz` fails from more than one"
        " region for three minutes.",
        "It is also the alert for a VM that is off or cannot boot: nothing answers.",
        "**The replica alert** fires when the `litestream` container logs a line at level"
        " `ERROR`, or the line starting with `Error:` that Litestream prints when it cannot"
        " start.",
        "It reads the lines that already reach Cloud Logging (log `cos_containers`, this VM's"
        " instance, container `litestream`); nothing on the VM changes for it.",
    )
    assert "email_address = var.alert_email" in MONITORING
    assert 'duration        = "180s"' in MONITORING and "threshold_value = 1" in MONITORING
    policy = replica_policy()
    assert "/logs/cos_containers" in policy
    assert "google_compute_instance.cairn.instance_id" in policy
    assert '"cos.googleapis.com/container_name" = "litestream"' in policy
    assert "level=ERROR|Error:" in policy
    assert "litestream exec litestream replicate" in HOST_SH


def test_the_alerts_section_says_what_litestream_logs_and_how_often_it_emails():
    said = section(INFRA, "## Alerts")
    says(
        said,
        "Litestream 0.5.18 logs a failed upload, compaction or retention step at level `ERROR`"
        " and tries again, and logs a healthy start, compaction, snapshot or restart at `INFO`.",
        "It sends at most one email an hour while the errors go on, and closes the incident 30"
        " minutes after the last one, without an email.",
        "With its replica refused, the stack rehearsal saw it log an error within seconds of a"
        " write and again after a restart.",
        "The `Error:` line comes from a container that exits at once and is removed (`docker"
        " run --rm`); whether Cloud Logging gets that line before the container is gone has"
        " not been seen. If it does not, a Litestream that cannot start counts among those"
        " that stop without a word, below, and `sudo journalctl -u litestream` on the VM still"
        " has the line.",
    )
    assert '$runner docker run --rm --name "$name"' in HOST_SH
    assert "  - path: /etc/systemd/system/litestream.service\n" in CLOUD_INIT
    stack = STACK.read_text(encoding="utf-8")
    assert 'self.compose("restart", "litestream")' in stack
    assert "alarmed(running)" in stack and "alarmed(at_start)" in stack
    assert 'period = "3600s"' in replica_policy() and 'auto_close = "1800s"' in replica_policy()
    assert "litestream/litestream:0.5.18" in COMPOSE_BACKUP


def test_the_alerts_section_says_what_the_replica_alert_does_not_see():
    said = section(INFRA, "## Alerts")
    says(
        said,
        "It fires on a failure Litestream reports. It does not see a Litestream that stops"
        " without a word or stalls silently, and an idle database writes nothing to the"
        " replica, so silence alone cannot be told from health. The replica check and the"
        " silence alert, below, are for that.",
        "A VM that is off sends no lines at all; the uptime alert covers that.",
        "The restore step at boot (`cairn-restore`) is another container: when it fails, cairn"
        " does not start, and the uptime alert fires.",
    )
    assert "litestream command cairn-restore" in HOST_SH


def test_the_runbook_says_where_to_look():
    said = section(INFRA, "### When the replica alert fires")
    says(
        said,
        "gcloud logging read 'logName:cos_containers AND"
        ' jsonPayload."cos.googleapis.com/container_name"="litestream"\' --freshness=1h'
        " --limit=30 --format='value(jsonPayload.message)'",
        "or on the VM, `sudo journalctl -u litestream -n 50 --no-pager`.",
        "`msg=` names the step that failed and `error=` says why.",
    )


def test_the_runbook_tells_a_refused_bucket_from_an_expired_credential():
    said = section(INFRA, "### When the replica alert fires")
    says(
        said,
        "What a refused bucket and a dead credential print on Google Cloud has not been seen"
        " here (the rehearsal refuses a directory, not a bucket); these are the shapes to look"
        " for:",
        "**A refused bucket**: the error carries Cloud Storage's `Error 403` and names a"
        " permission, such as `storage.objects.create`. The VM's account has a token and the"
        " bucket says no: the bucket is gone, or the account lost its role on it (`iam.tf`;"
        " `tofu plan` shows a missing binding).",
        "**An expired or missing credential**: the error is about the token, not the bucket"
        " (`oauth2`, `could not find default credentials`), or Litestream exits at start with"
        " `Error: cannot open store: initialize replica client` and systemd starts it again.",
        '**A full disk**: `msg="disk full while staging ltx file, replication paused until'
        ' space is freed"`.',
    )
    unit = CLOUD_INIT[CLOUD_INIT.index("/etc/systemd/system/litestream.service") :]
    unit = unit[: unit.index("  - path:")]
    assert "Restart=always\n" in unit  # "systemd starts it again"


def test_the_runbook_says_how_to_check_the_replica_is_current_and_what_proves_it():
    said = section(INFRA, "### When the replica alert fires")
    says(
        said,
        "After the fix, no `ERROR` follows. If an upload had failed, Litestream also logs"
        ' `msg="sync recovered"` (or `msg="replica sync recovered"`); when the failure met only'
        " compaction and retention, it does not.",
        "sudo bash /etc/cairn/host.sh litestream ltx -level all /data/cairn.db | tail -n 5",
        "Only a restore drill proves a replica:",
        'python3 scripts/restore_drill.py --replica "$(tofu -chdir=infra/gcp output -raw'
        ' replica_url)" --docker',
    )
    assert 'msg="sync recovered"' in rehearse.RECOVERED
    assert 'msg="replica sync recovered"' in rehearse.RECOVERED


def test_the_cost_table_counts_four_alert_policies_and_prices_the_log_based_one():
    said = section(INFRA, "## Monthly cost items")
    says(
        said,
        "| Cloud Logging and Monitoring: container logs, one uptime check, four alert policies"
        " (one of them log-based, two on log-based metrics), two log-based metrics | log volume"
        " | not priced yet beyond the free allowance |",
        "The replica alert reads log lines that already arrive, so it adds no log volume.",
        "Google's pricing examples count a log-based alerting policy as one metric reference at"
        " $0.35 a month, with no charge for points returned, because it returns none",
    )


def test_what_was_checked_names_the_replica_alerts_checks_and_gaps():
    start = INFRA.index("## What was checked, and what was not")
    said = flat(INFRA[start:])
    says(
        said,
        "The replica alert (2026-11-15): its arguments against the google provider 8.11.0"
        " schema and docs (`~> 8.10` resolved to 8.11.0 that day), Litestream's log levels"
        " against the 0.5.18 source, and its filter, read from `monitoring.tf`, against the"
        " lines Litestream printed in the stack rehearsal while its replica was refused"
        " (`scripts/rehearse_stack.py`, check `alerts_when_the_replica_refuses_writes`).",
        "The replica alert was added after that run and applied on 2026-11-15: one resource"
        " added, nothing else changed. Seen on the real project that day: its filter matched"
        " no entry of the last 24 hours, and the same filter with `INFO` in place of `ERROR`"
        " selected Litestream's own lines, so the log's name, the instance, the container's"
        " name and the regular expression read as the rehearsal assumed. Its email has not"
        " been seen.",
        "The replica alert on Google Cloud: that the `Error:` line of a Litestream that exits"
        " at start reaches Cloud Logging, and its email. On the real project its filter has"
        " matched entries only with `INFO` in place of `ERROR`: no error line has occurred"
        " there.",
    )
    assert "def alerts_when_the_replica_refuses_writes(self)" in STACK.read_text()


# deploy/README.md


def test_the_deploy_guide_says_where_the_same_lines_are_and_what_to_look_for():
    said = section(DEPLOY, "### Watch the replica")
    says(
        said,
        "Litestream says when it cannot replicate, in its own log: `docker compose logs"
        " litestream`.",
        'A line with `level=ERROR` means a step failed (`msg="sync error"`, `msg="monitor'
        ' error"`, `msg="compaction failed"`, `msg="l0 retention enforcement failed"`), and'
        " its `error=` part says why: a refused bucket, a credential that no longer works, a"
        " full disk.",
        'Litestream keeps trying, and logs `msg="sync recovered"` once an upload that had'
        " failed goes through again.",
        "A line that starts with `Error:` means it could not start at all.",
        "A healthy Litestream logs at `INFO` only.",
        "Nothing on this host sends you those lines.",
        "On the hosted VM an email goes out when one arrives, and another when a check of the"
        " replica's age has not found it current for 20 minutes",
        "Silence is not proof: an idle database writes nothing to the replica, and a"
        " Litestream that stops without a word logs nothing.",
    )
    assert "  litestream:\n" in COMPOSE_BACKUP


def test_the_deploy_guides_rehearsal_paragraph_names_the_replica_alert_check():
    said = section(DEPLOY, "### Rehearse the whole stack")
    says(
        said,
        "It refuses Litestream's replica while cairn takes a write, and checks that the"
        " replica alert's condition, read from `infra/gcp/monitoring.tf`, matches a line"
        " Litestream prints and none it printed while healthy, and that replication resumes"
        " once the replica is readable again.",
    )
    assert re.search(r"rehearsal\.alerts_when_the_replica_refuses_writes,", STACK.read_text())


# The replica check and the silence alert (R5)


def test_the_replica_check_is_described_as_host_sh_and_the_template_make_it():
    said = section(INFRA, "## Alerts")
    says(
        said,
        "Every five minutes `cairn-replica-check` (`host.sh replica-check`) asks the replica how"
        " old its newest copy is and writes one line into the `litestream` container's log,",
        "`replica: current, newest NN s old`, or `Error: replica: newest NN s old, over the"
        " bound` when a change to the database has waited more than 15 minutes for its copy.",
        "the check remembers it (in `/mnt/disks/data/replica-unconfirmed`)",
        "one `ltx` listing for each level it reads, levels 0, 1 and 9 in turn until one holds a"
        " file.",
        "unless it is older than the start of the `litestream` container",
    )
    assert "OnCalendar=*:0/5\n" in CLOUD_INIT
    assert "ExecStart=/bin/bash /etc/cairn/host.sh replica-check\n" in CLOUD_INIT
    assert re.search(
        r"^\s*replica_bound_seconds\s*=\s*900$",
        (REPO / "infra" / "gcp" / "main.tf").read_text(encoding="utf-8"),
        re.M,
    )
    assert 'line="replica: current, newest $((now - newest)) s old"' in HOST_SH
    assert 'line="Error: replica: newest $((now - newest)) s old, over the bound"' in HOST_SH
    assert "REPLICA_UNCONFIRMED=$DATA_MOUNT/replica-unconfirmed" in HOST_SH
    assert "for level in 0 1 9; do" in HOST_SH
    assert "docker inspect -f '{{.State.StartedAt}}' litestream" in HOST_SH
    assert "docker exec -i litestream sh -c 'cat >/proc/1/fd/1'" in HOST_SH


def test_the_silence_alert_is_described_as_monitoring_tf_makes_it():
    said = section(INFRA, "## Alerts")
    says(
        said,
        "**The silence alert** fires when no `replica: current` line has been counted for 20"
        " minutes: a log-based metric counts those lines, and an absence condition watches it.",
        "It emails once when an incident opens; Google's rate limit applies to log-match"
        " conditions only,",
    )
    start = MONITORING.index('resource "google_monitoring_alert_policy" "replica_silent"')
    policy = MONITORING[start:]
    assert 'duration = "1200s"' in policy
    assert 'notification_prompts = ["OPENED"]' in policy
    assert '=~ "^replica: current, "' in MONITORING


def test_the_runbook_for_silence_names_the_checks_own_words():
    said = section(INFRA, "### When the replica goes quiet")
    says(
        said,
        "`the litestream container is not running`: Litestream is down or restarting.",
        "`could not list the replica`, with Litestream's error above it:",
        "sudo journalctl -u cairn-replica-check -n 30 --no-pager",
    )
    assert "the litestream container is not running: the line above" in HOST_SH
    assert "replica check: could not list the replica" in HOST_SH


def test_the_runbooks_say_litestream_resumes_by_itself_within_its_backoff():
    """Measured with the pinned image: after failed uploads Litestream waits 10 s, then twice as
    long after each failure, up to 5 minutes, and resumes without a restart. The rehearsal
    waits for that, and the replica check, not a file's mtime, says when the copy is through:
    a replica file carries the time of the write it holds, not of its upload."""
    quiet = section(INFRA, "### When the replica goes quiet")
    says(
        quiet,
        "Litestream retries a failed upload by itself, without a restart: it waits 10 seconds,"
        " then twice as long after each failure, up to 5 minutes.",
        "`sudo systemctl restart litestream` makes it try at once.",
        "The check says current again, and forgets the change it remembered, once the change"
        " is copied.",
    )
    fires = section(INFRA, "### When the replica alert fires")
    says(
        fires,
        "Litestream tries again by itself within 5 minutes of the fix;"
        " `sudo systemctl restart litestream` makes it try at once.",
        "Each file in the replica carries the time of the newest write it holds, not the time"
        " it was uploaded:",
    )
    host = importlib.util.spec_from_file_location(
        "rehearse_host_for_o3", REPO / "scripts" / "rehearse_host.py"
    )
    module = importlib.util.module_from_spec(host)
    host.loader.exec_module(module)
    assert module.LITESTREAM_BACKOFF_CAP == 300
    # The rehearsal's refusal ends before the 5th failure (150 s), so the next try comes
    # within 80 s; measured 73 s. The wait leaves a margin and stays under the cap.
    assert 80 < module.RESUME_WAIT < module.LITESTREAM_BACKOFF_CAP


def test_what_was_checked_says_the_replica_check_was_not_applied_and_its_email_not_seen():
    said = flat(INFRA[INFRA.index("## What was checked, and what was not") :])
    says(
        said,
        "The replica check and the silence alert came after that and have not been applied: no"
        " current line has reached Cloud Logging, the metric has counted nothing, and the"
        " silence alert has never fired or sent an email.",
        "Its email has not been seen.",
    )
