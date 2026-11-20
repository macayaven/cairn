mock_provider "google" {
  mock_data "google_compute_image" {
    defaults = {
      self_link = "https://www.googleapis.com/compute/v1/projects/ubuntu-os-cloud/global/images/ubuntu-2404-example"
    }
  }
  mock_resource "google_service_account" {
    defaults = {
      email  = "cairn-quick@example-project.iam.gserviceaccount.com"
      member = "serviceAccount:cairn-quick@example-project.iam.gserviceaccount.com"
    }
  }
  mock_resource "google_secret_manager_secret" {
    defaults = {
      id = "projects/example-project/secrets/cairn-quick-env"
    }
  }
}

variables {
  project_id = "example-project"
  domain     = "cairn.example.net"
  acme_email = "ops@example.net"
}

run "three_inputs_with_safe_defaults" {
  command = plan

  assert {
    condition = (
      can(regex("^ghcr\\.io/macayaven/cairn:[0-9]+\\.[0-9]+\\.[0-9]+$", var.image)) &&
      google_compute_instance.app.machine_type == "e2-small" &&
      one(one(google_compute_instance.app.boot_disk).initialize_params).size == 20 &&
      one(one(google_compute_instance.app.boot_disk).initialize_params).type == "pd-balanced"
    )
    error_message = "The default VM must run a released image on e2-small with a 20 GiB balanced disk."
  }

  assert {
    condition = (
      google_compute_firewall.web.direction == "INGRESS" &&
      toset(google_compute_firewall.web.source_ranges) == toset(["0.0.0.0/0"]) &&
      one(google_compute_firewall.web.allow).protocol == "tcp" &&
      toset(one(google_compute_firewall.web.allow).ports) == toset(["80", "443"]) &&
      google_compute_firewall.iap_ssh.direction == "INGRESS" &&
      toset(google_compute_firewall.iap_ssh.source_ranges) == toset(["35.235.240.0/20"]) &&
      one(google_compute_firewall.iap_ssh.allow).protocol == "tcp" &&
      toset(one(google_compute_firewall.iap_ssh.allow).ports) == toset(["22"])
    )
    error_message = "Public traffic is limited to web ports. SSH is only available from IAP."
  }

  assert {
    condition = try(
      google_project_service.api["serviceusage.googleapis.com"].service == "serviceusage.googleapis.com" &&
      google_project_service.api["cloudresourcemanager.googleapis.com"].service == "cloudresourcemanager.googleapis.com" &&
      google_project_service.api["oslogin.googleapis.com"].service == "oslogin.googleapis.com" &&
      !google_project_service.api["oslogin.googleapis.com"].disable_on_destroy,
      false,
    )
    error_message = "The project must enable Service Usage, Resource Manager and OS Login APIs and keep them enabled on teardown."
  }

  assert {
    condition     = google_compute_subnetwork.app.private_ip_google_access == true
    error_message = "The subnet must enable Private Google Access."
  }

  assert {
    # With no customer key, the Google-managed encryption documented for every GCS bucket applies.
    # Seven-day soft delete matches infra/gcp as the recovery boundary without preventing teardown.
    condition = (
      google_storage_bucket.replica.force_destroy == false &&
      google_storage_bucket.replica.uniform_bucket_level_access &&
      google_storage_bucket.replica.public_access_prevention == "enforced" &&
      length(google_storage_bucket.replica.encryption) == 0 &&
      length(google_storage_bucket.replica.lifecycle_rule) == 0 &&
      one(google_storage_bucket.replica.soft_delete_policy).retention_duration_seconds == 604800
    )
    error_message = "The replica bucket must be private, use provider encryption, hold a 7-day soft delete policy and leave lifecycle retention to Litestream."
  }

  assert {
    condition = alltrue([for filename in fileset(path.module, "*.tf") :
      length(regexall("(resource|data|ephemeral)\\s+\"(google_service_account_key|google_secret_manager_secret_version|google_project_iam_custom_role)\"", file("${path.module}/${filename}"))) == 0
    ])
    error_message = "Do not create custom project roles or look up key or secret values in OpenTofu."
  }
}

run "identity_is_scoped_to_the_replica_and_settings" {
  command = apply

  assert {
    condition = (
      google_storage_bucket_iam_member.replica.bucket == google_storage_bucket.replica.name &&
      google_storage_bucket_iam_member.replica.member == google_service_account.app.member &&
      google_storage_bucket_iam_member.replica.role == "roles/storage.objectUser"
    )
    error_message = "Only the replica bucket grants the VM predefined object-user access."
  }

  assert {
    condition = (
      google_secret_manager_secret_iam_member.settings["roles/secretmanager.secretAccessor"].secret_id == google_secret_manager_secret.settings.secret_id &&
      google_secret_manager_secret_iam_member.settings["roles/secretmanager.secretAccessor"].member == google_service_account.app.member &&
      google_secret_manager_secret_iam_member.settings["roles/secretmanager.viewer"].secret_id == google_secret_manager_secret.settings.secret_id &&
      google_secret_manager_secret_iam_member.settings["roles/secretmanager.viewer"].member == google_service_account.app.member
    )
    error_message = "Only the settings secret grants the VM predefined viewer and accessor roles."
  }

  assert {
    condition = (
      one(google_compute_instance.app.service_account).email == google_service_account.app.email &&
      google_compute_instance.app.metadata["enable-oslogin"] == "TRUE" &&
      google_compute_instance.app.metadata["block-project-ssh-keys"] == "TRUE" &&
      google_compute_instance.app.metadata["user-data"] == local.cloud_init &&
      one(google_compute_instance.app.shielded_instance_config).enable_secure_boot &&
      one(google_compute_instance.app.shielded_instance_config).enable_vtpm &&
      one(google_compute_instance.app.shielded_instance_config).enable_integrity_monitoring &&
      output.connect_command == "gcloud compute ssh cairn-quick --project=example-project --zone=us-central1-a --tunnel-through-iap" &&
      output.first_key_command == "sudo /opt/cairn/bootstrap.sh first-key"
    )
    error_message = "The Ubuntu VM must use the scoped identity, Shielded VM features, blocked project SSH keys, shared cloud-init and OS Login through IAP."
  }
}
