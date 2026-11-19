# What a reviewer of a Google Cloud deployment expects, through a real plan with a mock
# provider (no cloud project, no credentials): run `tofu -chdir=infra/gcp init -backend=false`
# once, then `tofu -chdir=infra/gcp test`. Every name is made up.

mock_provider "google" {
  mock_resource "google_service_account" {
    defaults = {
      name   = "projects/example-project/serviceAccounts/cairn-sa@example-project.iam.gserviceaccount.com"
      email  = "cairn-sa@example-project.iam.gserviceaccount.com"
      member = "serviceAccount:cairn-sa@example-project.iam.gserviceaccount.com"
    }
  }
}

variables {
  project_id           = "example-project"
  domain               = "cairn.example.test"
  alert_email          = "ops@example.test"
  github_repository    = "example-owner/cairn"
  github_repository_id = "123456789"
}

# --- the registry cleans up after itself ------------------------------------------------------

run "the_registry_keeps_what_a_rollback_needs_and_deletes_the_rest" {
  command = plan

  assert {
    condition     = google_artifact_registry_repository.cairn.cleanup_policy_dry_run == false
    error_message = "the cleanup policy is only a dry run"
  }
  assert {
    condition     = one([for p in google_artifact_registry_repository.cairn.cleanup_policies : p.most_recent_versions[0].keep_count if p.id == "keep-newest" && p.action == "KEEP"]) == 50
    error_message = "the newest 50 versions are not kept"
  }
  assert {
    condition     = one([for p in google_artifact_registry_repository.cairn.cleanup_policies : p.condition[0].tag_prefixes if p.id == "keep-deploy-tag" && p.action == "KEEP"]) == tolist(["production"])
    error_message = "what the deploy tag names is not kept"
  }
  assert {
    condition     = one([for p in google_artifact_registry_repository.cairn.cleanup_policies : p.condition[0].older_than if p.id == "delete-untagged" && p.action == "DELETE" && p.condition[0].tag_state == "UNTAGGED"]) == "1209600s"
    error_message = "untagged images are not deleted after 14 days"
  }
  assert {
    condition     = one([for p in google_artifact_registry_repository.cairn.cleanup_policies : p.condition[0].older_than if p.id == "delete-old" && p.action == "DELETE" && p.condition[0].tag_state == "TAGGED"]) == "1209600s"
    error_message = "older tagged images are not deleted after 14 days"
  }
  assert {
    condition     = length(google_artifact_registry_repository.cairn.cleanup_policies) == 4
    error_message = "the registry has another cleanup rule"
  }
}

run "the_cleanup_follows_its_variables_and_the_deploy_tag" {
  command = plan
  variables {
    deploy_tag                 = "live"
    registry_keep_versions     = 80
    registry_delete_after_days = 30
  }
  assert {
    condition     = one([for p in google_artifact_registry_repository.cairn.cleanup_policies : p.most_recent_versions[0].keep_count if p.id == "keep-newest"]) == 80
    error_message = "registry_keep_versions is not the count kept"
  }
  assert {
    condition     = one([for p in google_artifact_registry_repository.cairn.cleanup_policies : p.condition[0].tag_prefixes if p.id == "keep-deploy-tag"]) == tolist(["live"])
    error_message = "the kept tag is not deploy_tag"
  }
  assert {
    condition     = toset([for p in google_artifact_registry_repository.cairn.cleanup_policies : p.condition[0].older_than if p.action == "DELETE"]) == toset(["2592000s"])
    error_message = "registry_delete_after_days is not the age deleted"
  }
}

run "a_cleanup_age_under_a_day_is_refused" {
  command = plan
  variables {
    registry_delete_after_days = 0
  }
  expect_failures = [var.registry_delete_after_days]
}

# --- the boot disk and the bucket's soft delete -----------------------------------------------

run "the_boot_disk_is_20_gb_and_soft_delete_7_days" {
  command = plan
  assert {
    condition     = google_compute_instance.cairn.boot_disk[0].initialize_params[0].size == 20
    error_message = "the boot disk is not 20 GB"
  }
  assert {
    condition     = google_storage_bucket.replica.soft_delete_policy[0].retention_duration_seconds == 604800
    error_message = "the bucket does not keep what is deleted for 7 days"
  }
}

# --- logs and audit ---------------------------------------------------------------------------

run "the_default_log_bucket_keeps_30_days_and_data_access_is_audited" {
  command = plan
  assert {
    condition     = google_logging_project_bucket_config.default.bucket_id == "_Default" && google_logging_project_bucket_config.default.retention_days == 30
    error_message = "the _Default log bucket does not keep 30 days"
  }
  assert {
    condition     = toset(keys(google_project_iam_audit_config.data_access)) == toset(["secretmanager.googleapis.com", "storage.googleapis.com"])
    error_message = "Data Access audit logs are not on for Cloud Storage and Secret Manager"
  }
  assert {
    condition     = alltrue([for c in google_project_iam_audit_config.data_access : toset([for l in c.audit_log_config : l.log_type]) == toset(["DATA_READ", "DATA_WRITE"])])
    error_message = "the audit logs do not record data reads and writes"
  }
}

run "log_retention_and_audit_follow_their_variables" {
  command = plan
  variables {
    log_retention_days     = 400
    data_access_audit_logs = false
  }
  assert {
    condition     = google_logging_project_bucket_config.default.retention_days == 400
    error_message = "log_retention_days is not the retention"
  }
  assert {
    condition     = length(google_project_iam_audit_config.data_access) == 0
    error_message = "audit configs are made with data_access_audit_logs off"
  }
}

# --- OS Login, the subnet, the firewall --------------------------------------------------------

run "by_default_no_second_factor_private_access_on_and_no_paid_logs" {
  command = plan
  assert {
    condition     = !contains(keys(google_compute_instance.cairn.metadata), "enable-oslogin-2fa")
    error_message = "the metadata names a second factor while os_login_2fa is off"
  }
  assert {
    condition     = google_compute_subnetwork.cairn.private_ip_google_access == true
    error_message = "Private Google Access is off"
  }
  assert {
    condition     = length(google_compute_subnetwork.cairn.log_config) == 0
    error_message = "VPC flow logs are on by default"
  }
  assert {
    condition     = length(google_compute_firewall.web.log_config) == 0 && length(google_compute_firewall.iap_ssh.log_config) == 0
    error_message = "firewall rule logging is on by default"
  }
}

run "a_second_factor_flow_logs_and_firewall_logs_when_asked" {
  command = plan
  variables {
    os_login_2fa     = true
    vpc_flow_logs    = true
    firewall_logging = true
  }
  assert {
    condition     = google_compute_instance.cairn.metadata["enable-oslogin-2fa"] == "TRUE" && google_compute_instance.cairn.metadata["enable-oslogin"] == "TRUE"
    error_message = "os_login_2fa does not ask for a second factor"
  }
  assert {
    condition     = length(google_compute_subnetwork.cairn.log_config) == 1
    error_message = "vpc_flow_logs does not turn flow logs on"
  }
  assert {
    condition     = length(google_compute_firewall.web.log_config) == 1 && length(google_compute_firewall.iap_ssh.log_config) == 1
    error_message = "firewall_logging does not log both rules"
  }
}

# --- customer-managed keys ----------------------------------------------------------------------

run "without_a_key_google_manages_the_keys_as_before" {
  command = plan
  assert {
    condition     = length(google_compute_disk.data.disk_encryption_key) == 0
    error_message = "the data disk names a key"
  }
  assert {
    # The provider computes the attribute when it is null, and the mock fills it with a random
    # string, so the value main.tf gives it is what is checked.
    condition     = local.boot_disk_key == null
    error_message = "the boot disk names a key"
  }
  assert {
    condition     = length(google_storage_bucket.replica.encryption) == 0
    error_message = "the bucket names a key"
  }
  assert {
    condition     = length(google_secret_manager_secret.env.replication[0].user_managed[0].replicas[0].customer_managed_encryption) == 0
    error_message = "the secret names a key"
  }
}

run "a_key_encrypts_both_disks_the_bucket_and_the_secret" {
  command = plan
  variables {
    kms_key_name = "projects/example-keys/locations/europe-west1/keyRings/cairn/cryptoKeys/data"
  }
  assert {
    condition     = google_compute_disk.data.disk_encryption_key[0].kms_key_self_link == "projects/example-keys/locations/europe-west1/keyRings/cairn/cryptoKeys/data"
    error_message = "the data disk does not use the key"
  }
  assert {
    condition     = google_compute_instance.cairn.boot_disk[0].kms_key_self_link == "projects/example-keys/locations/europe-west1/keyRings/cairn/cryptoKeys/data"
    error_message = "the boot disk does not use the key"
  }
  assert {
    condition     = google_storage_bucket.replica.encryption[0].default_kms_key_name == "projects/example-keys/locations/europe-west1/keyRings/cairn/cryptoKeys/data"
    error_message = "the bucket does not use the key"
  }
  assert {
    condition     = google_secret_manager_secret.env.replication[0].user_managed[0].replicas[0].customer_managed_encryption[0].kms_key_name == "projects/example-keys/locations/europe-west1/keyRings/cairn/cryptoKeys/data"
    error_message = "the secret does not use the key"
  }
}

run "a_key_that_is_not_a_key_name_is_refused" {
  command = plan
  variables {
    kms_key_name = "data"
  }
  expect_failures = [var.kms_key_name]
}

run "a_key_in_another_region_is_refused" {
  command = plan
  variables {
    kms_key_name = "projects/example-keys/locations/us-central1/keyRings/cairn/cryptoKeys/data"
  }
  expect_failures = [google_storage_bucket.replica]
}

# --- alerts on the host ------------------------------------------------------------------------

run "four_host_alerts_mail_the_existing_address" {
  command = plan
  assert {
    condition     = toset(keys(google_monitoring_alert_policy.host)) == toset(["certificate", "disk", "memory", "restarts"])
    error_message = "the host alerts are not disk, memory, restarts and certificate"
  }
  assert {
    condition     = alltrue([for p in google_monitoring_alert_policy.host : p.alert_strategy[0].notification_rate_limit[0].period == "3600s" && p.alert_strategy[0].auto_close == "1800s"])
    error_message = "a host alert mails more than hourly or does not close after 30 minutes"
  }
  assert {
    condition     = alltrue([for k, p in google_monitoring_alert_policy.host : strcontains(p.conditions[0].condition_matched_log[0].filter, "=~ \"^host-check: ${k}: \"")])
    error_message = "a host alert does not read its own kind of line"
  }
}
