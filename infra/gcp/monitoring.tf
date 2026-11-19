# One outside-in check: Google's probers fetch https://DOMAIN/healthz every minute, with
# certificate validation, and an email goes out when it keeps failing.

resource "google_monitoring_notification_channel" "email" {
  display_name = "${var.name} alerts"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_uptime_check_config" "healthz" {
  display_name = "${var.name} healthz"
  period       = "60s"
  timeout      = "10s"

  http_check {
    path         = "/healthz"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"

    labels = {
      project_id = var.project_id
      host       = var.domain
    }
  }
}

resource "google_monitoring_alert_policy" "healthz" {
  display_name = "${var.name} is not answering /healthz"
  combiner     = "OR"

  conditions {
    display_name = "Uptime check on https://${var.domain}/healthz is failing"

    # The shape Google documents for uptime alerts: count the probe regions that report a
    # failed check, and fire when more than one does for three minutes. A deploy restarts
    # cairn for a few seconds and stays under that.
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.healthz.uptime_check_id}\" AND resource.type=\"uptime_url\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "180s"

      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.*"]
      }

      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]

  documentation {
    mime_type = "text/markdown"
    content   = "cairn at https://${var.domain} failed its uptime check from more than one region for three minutes. Runbooks: infra/gcp/README.md in the cairn repository."
  }
}

# A second alert, on the replica. /healthz checks cairn's database, not its copy in the bucket.
# Litestream runs in the container `litestream` (files/host.sh), and every line it prints
# already reaches Cloud Logging as one entry in the log cos_containers. Litestream 0.5.18 logs
# a failed upload, compaction or retention step at level ERROR and keeps trying; when it
# cannot start it prints one line, "Error: ...", and exits. The condition matches either.
#
# scripts/rehearse_stack.py reads this filter and evaluates it on what Litestream prints with
# its replica refused, and on what it printed while healthy. It takes one form only: clauses
# joined by AND, `=` on a field, and one `=~` on jsonPayload.message, with no backslash.
resource "google_monitoring_alert_policy" "replica" {
  display_name = "${var.name} replica reports an error"
  combiner     = "OR"

  conditions {
    display_name = "Litestream on ${var.name} logged an error"

    condition_matched_log {
      filter = <<-EOT
        logName = "projects/${var.project_id}/logs/cos_containers"
        AND resource.type = "gce_instance"
        AND resource.labels.instance_id = "${google_compute_instance.cairn.instance_id}"
        AND jsonPayload."cos.googleapis.com/container_name" = "litestream"
        AND jsonPayload.message =~ "^(time=[^ ]+ level=ERROR|Error:) "
      EOT
    }
  }

  # Required for a log-match condition. A failing Litestream logs an error every 15 to 30
  # seconds; one email an hour says it is still failing. An incident closes 30 minutes (the
  # shortest Google allows) after the last matching line, and no email says so.
  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }

    auto_close = "1800s"
  }

  notification_channels = [google_monitoring_notification_channel.email.name]

  documentation {
    mime_type = "text/markdown"
    content   = "Litestream, which copies cairn's database at https://${var.domain} to its bucket, logged an error: the replica may be falling behind. Runbook: \"When the replica alert fires\" in infra/gcp/README.md in the cairn repository."
  }
}

# A third alert, on silence. The replica alert hears only what Litestream says; a Litestream
# that is gone says nothing, and neither does an idle database. Every 5 minutes
# cairn-replica-check.timer asks the replica how old its newest copy is (files/host.sh,
# replica-check) and writes the answer into the litestream container's log: "replica: current,
# newest NN s old", or an "Error: replica: ..." line that the alert above already matches.
# This metric counts the current lines; the policy below fires when none has arrived for 20
# minutes, which covers Litestream gone, the timer gone and the VM's logging gone alike.
# The filter takes the form scripts/rehearse_stack.py evaluates (see the replica alert).
resource "google_logging_metric" "replica_current" {
  name        = "${var.name}-replica-current"
  description = "Lines in which the replica check on ${var.name} found the replica current."
  filter      = <<-EOT
    logName = "projects/${var.project_id}/logs/cos_containers"
    AND resource.type = "gce_instance"
    AND resource.labels.instance_id = "${google_compute_instance.cairn.instance_id}"
    AND jsonPayload."cos.googleapis.com/container_name" = "litestream"
    AND jsonPayload.message =~ "^replica: current, "
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "replica_silent" {
  display_name = "${var.name} replica check has gone quiet"
  combiner     = "OR"

  conditions {
    display_name = "No current replica line from ${var.name} for 20 minutes"

    # Google arms an absence condition only once the metric has a point after the policy is
    # made or changed: on a new VM, after the first current line.
    condition_absent {
      filter   = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.replica_current.name}\" AND resource.type=\"gce_instance\""
      duration = "1200s"

      # One series for the policy, whatever the instance: the metric's filter already names
      # this VM, and a VM replaced by apply must not leave the old one's series open.
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger {
        count = 1
      }
    }
  }

  # A metric condition takes no notification_rate_limit (Google applies it to log-match
  # conditions only). One email when an incident opens and none when it closes, as above; a
  # second email needs the line back and then 20 more minutes without it. No auto_close: an
  # incident closes once a current line is counted again, and Google's default (seven days
  # without data) keeps it open, and quiet, while the silence lasts.
  alert_strategy {
    notification_prompts = ["OPENED"]
  }

  notification_channels = [google_monitoring_notification_channel.email.name]

  documentation {
    mime_type = "text/markdown"
    content   = "No line from the replica check on https://${var.domain} has said the replica is current for 20 minutes: Litestream, the check's timer or the VM's logging has stopped. Runbook: \"When the replica goes quiet\" in infra/gcp/README.md in the cairn repository."
  }
}

# A fourth alert, on a compaction that keeps not finishing. After a workspace is deleted
# `serve` compacts the database: it rewrites the file (VACUUM), then truncates the write-ahead
# log. Beside Litestream, which reads the log for as long as it runs, the second step does not
# finish, and cairn writes one line on its access log, in the cairn container's output:
# "cairn.access: compact not finished: another connection held the file in NN ms". Each such
# run has rewritten the whole database under the store's lock. Since the fix of decision 143,
# one line a day, or one after each restart, is expected; three within an hour is a server
# trying again at every poll, or one that keeps restarting. The metric counts the lines and
# the policy fires on the third within an hour. The filter takes the form
# scripts/rehearse_stack.py evaluates (see the replica alert), and
# tests/unit/test_infra_tf_unit.py holds it to the line serve writes.
resource "google_logging_metric" "compact_not_finished" {
  name        = "${var.name}-compact-not-finished"
  description = "Lines in which cairn on ${var.name} says a compaction did not finish."
  filter      = <<-EOT
    logName = "projects/${var.project_id}/logs/cos_containers"
    AND resource.type = "gce_instance"
    AND resource.labels.instance_id = "${google_compute_instance.cairn.instance_id}"
    AND jsonPayload."cos.googleapis.com/container_name" = "cairn"
    AND jsonPayload.message =~ "^cairn[.]access: compact not finished: "
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "compact_not_finished" {
  display_name = "${var.name} compaction keeps not finishing"
  combiner     = "OR"

  conditions {
    display_name = "More than two unfinished compactions on ${var.name} within an hour"

    # The sum of the lines over the last hour (Google slides the alignment period as a
    # window), above 2. Held for one minute because Google ignores evaluation_missing_data
    # when duration is zero; with it, an incident closes once the last hour holds no more
    # than two lines, also when the metric has no point at all.
    condition_threshold {
      filter                  = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.compact_not_finished.name}\" AND resource.type=\"gce_instance\""
      comparison              = "COMPARISON_GT"
      threshold_value         = 2
      duration                = "60s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"

      # One series for the policy, as for the silence alert: the metric's filter names this VM.
      aggregations {
        alignment_period     = "3600s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger {
        count = 1
      }
    }
  }

  # A metric condition takes no notification_rate_limit: one email when an incident opens,
  # none when it closes.
  alert_strategy {
    notification_prompts = ["OPENED"]
  }

  notification_channels = [google_monitoring_notification_channel.email.name]

  documentation {
    mime_type = "text/markdown"
    content   = "cairn at https://${var.domain} logged more than two unfinished compactions within an hour, and each one rewrote the whole database. Runbook: \"When a compaction keeps not finishing\" in infra/gcp/README.md in the cairn repository."
  }
}

# Four alerts on the host itself, one for each kind of line `host.sh host-check` writes every
# five minutes (cairn-host-check.timer) into the log of a running container: litestream, else
# cairn, else caddy. So the filters name no container. Each takes the form
# scripts/rehearse_stack.py evaluates (see the replica alert), and
# tests/unit/test_host_check_unit.py holds each to the line host.sh writes.
locals {
  host_alerts = {
    "disk" = {
      title = "a disk is above 80 percent"
      about = "The data disk or the boot disk of ${var.name} is more than 80 percent full."
    }
    "memory" = {
      title = "a container was killed for memory"
      about = "A container on ${var.name} reached its memory limit and was killed."
    }
    "restarts" = {
      title = "a unit keeps restarting"
      about = "systemd restarted cairn, Caddy or Litestream on ${var.name} three times or more within five minutes."
    }
    "certificate" = {
      title = "Caddy cannot obtain or renew a certificate"
      about = "Caddy on ${var.name} logged an error obtaining or renewing the certificate for ${var.domain}."
    }
  }
}

resource "google_monitoring_alert_policy" "host" {
  for_each = local.host_alerts

  display_name = "${var.name}: ${each.value.title}"
  combiner     = "OR"

  conditions {
    display_name = "host-check on ${var.name}: ${each.key}"

    condition_matched_log {
      filter = <<-EOT
        logName = "projects/${var.project_id}/logs/cos_containers"
        AND resource.type = "gce_instance"
        AND resource.labels.instance_id = "${google_compute_instance.cairn.instance_id}"
        AND jsonPayload.message =~ "^host-check: ${each.key}: "
      EOT
    }
  }

  # As for the replica alert: one email an hour while the lines go on (one every five
  # minutes), and the incident closes 30 minutes after the last one, without an email.
  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }

    auto_close = "1800s"
  }

  notification_channels = [google_monitoring_notification_channel.email.name]

  documentation {
    mime_type = "text/markdown"
    content   = "${each.value.about} The line itself says which. Runbook: \"When a host alert fires\" in infra/gcp/README.md in the cairn repository."
  }
}
