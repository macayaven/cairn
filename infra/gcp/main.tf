# cairn on Google Cloud: one VM on Container-Optimized OS running the same image self-hosted
# users run, a separate data disk, a static address, a replication bucket, a private registry
# and one secret. See README.md for the bootstrap and the runbooks.

locals {
  zone = coalesce(var.zone, "${var.region}-b")

  labels = merge({ app = var.name, managed-by = "opentofu" }, var.labels)

  network_tag      = var.name
  data_device_name = "${var.name}-data"

  registry_host = "${var.region}-docker.pkg.dev"
  image_repo    = "${local.registry_host}/${var.project_id}/${google_artifact_registry_repository.cairn.repository_id}"

  # The one image the VM runs, and the one tag of it that the VM follows. A deploy is a push
  # that moves this tag; the updater resolves it to a digest and runs the digest.
  image        = "${local.image_repo}/cairn"
  deploy_image = "${local.image}:${var.deploy_tag}"

  bucket_name = coalesce(var.replica_bucket_name, "${var.project_id}-${var.name}-replica")
  replica_url = "gs://${google_storage_bucket.replica.name}/${var.replica_path}"

  env_secret = "projects/${var.project_id}/secrets/${google_secret_manager_secret.env.secret_id}"

  # Everything the VM runs is in this one document. cloud-init applies it on every boot,
  # because Container-Optimized OS keeps /etc on tmpfs. File contents are passed in as values,
  # so the shell script, the two shared config files and the plans file need no template
  # escaping. Without cairn_plans_file the plans file is empty, and host.sh gives cairn none.
  cloud_init = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    domain           = var.domain
    public_url       = "https://${var.domain}"
    acme_email       = coalesce(var.acme_email, var.alert_email)
    extra_args       = var.cairn_extra_args
    registry_host    = local.registry_host
    image            = local.image
    deploy_tag       = var.deploy_tag
    env_secret       = local.env_secret
    replica_url      = local.replica_url
    caddy_image      = var.caddy_image
    litestream_image = var.litestream_image
    data_device      = "/dev/disk/by-id/google-${local.data_device_name}"
    host_sh          = base64gzip(file("${path.module}/files/host.sh"))
    caddyfile        = file("${path.module}/../../deploy/Caddyfile")
    litestream_yml   = file("${path.module}/../../deploy/litestream.yml")
    plans_yaml       = var.cairn_plans_file == "" ? "" : file(var.cairn_plans_file)
    brand_files      = local.brand_files

    cairn_memory_mb       = var.container_limits.cairn.memory_mb
    cairn_pids_limit      = var.container_limits.cairn.pids
    caddy_memory_mb       = var.container_limits.caddy.memory_mb
    caddy_pids_limit      = var.container_limits.caddy.pids
    litestream_memory_mb  = var.container_limits.litestream.memory_mb
    litestream_pids_limit = var.container_limits.litestream.pids

    # How long a change may wait for its copy before the replica check says Error: 15
    # minutes. A fixed value, not a variable; the host rehearsal renders a shorter one.
    replica_bound_seconds = 900
  })

  # The operator's brand (cairn_brand_*): one write_files entry for each value that is set,
  # under /etc/cairn/brand, which host.sh reads and gives serve. Each is base64 with
  # `encoding: b64`, so cloud-init writes the exact bytes (a PNG is binary) and no YAML
  # quoting and no shell reads a name. The template takes the entries as one value at the
  # start of a line: with no brand it is "", and the user-data has not one byte more.
  brand = [
    { path = "/etc/cairn/brand/name", content = base64encode(var.cairn_brand_name) },
    { path = "/etc/cairn/brand/logo", content = var.cairn_brand_logo_file == "" ? "" : filebase64(var.cairn_brand_logo_file) },
    { path = "/etc/cairn/brand/logo-alt", content = base64encode(var.cairn_brand_logo_alt) },
  ]
  brand_files = join("", [
    for f in local.brand : "  - path: ${f.path}\n    permissions: \"0644\"\n    owner: root\n    encoding: b64\n    content: \"${f.content}\"\n\n" if f.content != ""
  ])

  # cairn_extra_args as host.sh hands it to serve: split on spaces, a run of spaces as one.
  extra_words = [for w in split(" ", var.cairn_extra_args) : w if w != ""]

  # The user-data's size in bytes (length() counts characters), for the precondition below.
  cloud_init_base64 = base64encode(local.cloud_init)
  user_data_bytes   = length(local.cloud_init_base64) / 4 * 3 - length(regexall("=", local.cloud_init_base64))
}

# --- network --------------------------------------------------------------------------------
# A dedicated network, so no default rule (such as default-allow-ssh from anywhere) applies.
# Ingress is denied unless one of the two rules below allows it.

resource "google_compute_network" "cairn" {
  name                    = var.name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "cairn" {
  name          = var.name
  region        = var.region
  network       = google_compute_network.cairn.id
  ip_cidr_range = "10.10.0.0/24"
}

resource "google_compute_firewall" "web" {
  name        = "${var.name}-allow-web"
  description = "HTTP (ACME challenge and redirect) and HTTPS from anywhere."
  network     = google_compute_network.cairn.id
  direction   = "INGRESS"

  source_ranges = ["0.0.0.0/0"]
  target_tags   = [local.network_tag]

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}

resource "google_compute_firewall" "iap_ssh" {
  name        = "${var.name}-allow-iap-ssh"
  description = "SSH only from Identity-Aware Proxy TCP forwarding."
  network     = google_compute_network.cairn.id
  direction   = "INGRESS"

  source_ranges = ["35.235.240.0/20"]
  target_tags   = [local.network_tag]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_address" "cairn" {
  name         = var.name
  region       = var.region
  address_type = "EXTERNAL"
  description  = "Static address for ${var.domain}"
}

# --- registry, secret, replication bucket ---------------------------------------------------

resource "google_artifact_registry_repository" "cairn" {
  location      = var.region
  repository_id = var.name
  format        = "DOCKER"
  description   = "cairn images, pushed by CI. The VM follows the tag ${var.deploy_tag} of the image cairn."
  labels        = local.labels
}

# OpenTofu creates the secret and its access rule but never a version, so the secret's value
# is never in state. Add versions with `gcloud secrets versions add` (README).
resource "google_secret_manager_secret" "env" {
  secret_id = "${var.name}-env"
  labels    = local.labels

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

resource "google_storage_bucket" "replica" {
  name     = local.bucket_name
  location = var.region
  labels   = local.labels

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  # The recovery boundary. Everything deleted or overwritten in this bucket stays restorable
  # for this long, and during that time nobody can delete it for good. The VM's identity
  # deletes objects (Litestream's retention needs that) but cannot change this policy: it has
  # no bucket-level permission at all (iam.tf). Object versioning is off on purpose: with it,
  # a delete makes a noncurrent version that the same identity could delete again, and there
  # would be two places to restore from. README, "The replica was deleted or tampered with".
  soft_delete_policy {
    retention_duration_seconds = var.replica_soft_delete_days * 86400
  }

  versioning {
    enabled = false
  }
}

# --- data disk and its snapshots ------------------------------------------------------------

resource "google_compute_disk" "data" {
  name   = local.data_device_name
  zone   = local.zone
  type   = "pd-balanced"
  size   = var.data_disk_size_gb
  labels = local.labels

  lifecycle {
    # The database lives here. To destroy it on purpose, follow "Teardown" in the README.
    prevent_destroy = true

    # A disk recreated by hand from a snapshot (README, "Restore") reports that snapshot as
    # its source. Without this, the next plan would want to replace it.
    ignore_changes = [snapshot]

    precondition {
      condition     = startswith(local.zone, "${var.region}-")
      error_message = "zone must be inside region."
    }
  }
}

resource "google_compute_resource_policy" "data_daily" {
  name        = "${var.name}-data-daily"
  region      = var.region
  description = "Daily snapshot of the cairn data disk, kept 14 days."

  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "04:00"
      }
    }

    retention_policy {
      max_retention_days    = 14
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }

    snapshot_properties {
      storage_locations = [var.region]
      labels            = local.labels
    }
  }
}

resource "google_compute_disk_resource_policy_attachment" "data_daily" {
  name = google_compute_resource_policy.data_daily.name
  disk = google_compute_disk.data.name
  zone = local.zone
}

# --- the VM ---------------------------------------------------------------------------------

resource "google_compute_instance" "cairn" {
  name         = var.name
  zone         = local.zone
  machine_type = var.machine_type
  tags         = [local.network_tag]
  labels       = local.labels

  # The VM is disposable: data is on the attached disk, the address is reserved, and the
  # running image is recorded on the data disk. Replacing it is how the OS gets upgraded.
  allow_stopping_for_update = true
  deletion_protection       = false

  boot_disk {
    auto_delete = true

    initialize_params {
      image = "cos-cloud/cos-stable"
      size  = var.boot_disk_size_gb
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.data.self_link
    device_name = local.data_device_name
    mode        = "READ_WRITE"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.cairn.self_link

    access_config {
      nat_ip = google_compute_address.cairn.address
    }
  }

  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  # Nothing here changes on a deploy: the VM follows a registry tag, and no identity other
  # than whoever applies this configuration can set instance metadata.
  # Guest attributes carry the updater's status (cairn/status, files/host.sh) out to the
  # deploy job, which may read them and nothing else on this VM (iam.tf).
  metadata = {
    user-data                 = local.cloud_init
    enable-oslogin            = "TRUE"
    block-project-ssh-keys    = "TRUE"
    enable-guest-attributes   = "TRUE"
    google-logging-enabled    = "true"
    google-monitoring-enabled = "true"
  }

  lifecycle {
    ignore_changes = [
      # A new cos-stable release must not replace a running VM. Replace it on purpose instead.
      boot_disk[0].initialize_params[0].image,
    ]

    # host.sh gives serve --plans from cairn_plans_file. A second --plans in the extra
    # arguments would name another file, and serve would read whichever came last.
    precondition {
      condition     = var.cairn_plans_file == "" || !can(regex("(^| )--plans( |=|$)", var.cairn_extra_args))
      error_message = "cairn_extra_args has --plans while cairn_plans_file is set. Remove --plans from cairn_extra_args: the VM gives serve --plans /etc/cairn/plans.yaml from cairn_plans_file."
    }

    # host.sh gives serve the brand from the three cairn_brand_* variables. In the extra
    # arguments a name could not hold a space, and a logo would name a file on the VM's host,
    # which the container cannot see.
    precondition {
      condition     = !can(regex("(^| )--brand-(name|logo|logo-alt)( |=|$)", var.cairn_extra_args))
      error_message = "cairn_extra_args has --brand-name, --brand-logo or --brand-logo-alt. Remove it from cairn_extra_args and set cairn_brand_name, cairn_brand_logo_file or cairn_brand_logo_alt instead: the VM gives serve those options from the three variables."
    }

    # What serve refuses at start, refused here instead: on the VM a refusal is a container
    # that never comes up.
    precondition {
      condition     = var.cairn_brand_logo_file == "" || var.cairn_brand_name != ""
      error_message = "cairn_brand_logo_file is set but cairn_brand_name is not. Set cairn_brand_name too (serve takes a logo only with a name), or leave cairn_brand_logo_file empty."
    }

    precondition {
      condition     = var.cairn_brand_logo_alt == "" || var.cairn_brand_logo_file != ""
      error_message = "cairn_brand_logo_alt is set but cairn_brand_logo_file is not. Set cairn_brand_logo_file too (the alt text belongs to a logo), or leave cairn_brand_logo_alt empty."
    }

    precondition {
      condition     = var.cairn_brand_name == "" || can(regex("(^| )--sign-in( |=|$)", var.cairn_extra_args))
      error_message = "cairn_brand_name is set but cairn_extra_args has no --sign-in. Add --sign-in PROVIDER (github, google or oidc) to cairn_extra_args, or leave cairn_brand_name empty: serve takes a brand only with sign-in."
    }

    precondition {
      condition     = !can(regex("(^| )--(wall|session-days)( |=|$)", var.cairn_extra_args)) || can(regex("(^| )--sign-in( |=|$)", var.cairn_extra_args))
      error_message = "cairn_extra_args has --wall or --session-days but no --sign-in. Add --sign-in PROVIDER (github, google or oidc) to cairn_extra_args, or remove --wall and --session-days: serve takes them only with sign-in."
    }

    # serve takes --session-days N only for a whole number N from 1 to 365. Each one is read,
    # as `--session-days N` (N is the next word) or `--session-days=N`.
    precondition {
      condition     = alltrue([for i, w in local.extra_words : !(w == "--session-days" || startswith(w, "--session-days=")) || can(regex("^0*([1-9][0-9]?|[12][0-9][0-9]|3[0-5][0-9]|36[0-5])$", w == "--session-days" ? concat(local.extra_words, [""])[i + 1] : trimprefix(w, "--session-days=")))])
      error_message = "cairn_extra_args gives --session-days something other than a whole number of days from 1 to 365, which serve refuses at start. Write it as --session-days 30 or --session-days=30, with a number from 1 to 365."
    }

    # --wall takes no value, and serve refuses --wall=VALUE at start. The precondition above
    # counts --wall= as a --wall, so the value is refused here.
    precondition {
      condition     = !anytrue([for w in local.extra_words : startswith(w, "--wall=")])
      error_message = "cairn_extra_args has --wall=VALUE, and serve refuses it at start: --wall takes no value. Write --wall alone."
    }

    # Compute Engine takes at most 256 KB in one metadata value. The caps on the plans file
    # and the logo keep the user-data under it (README, "Changing the configuration"); this
    # holds the line whatever else grows, cairn_extra_args included.
    precondition {
      condition     = local.user_data_bytes <= 262144
      error_message = "The VM's user-data is ${local.user_data_bytes} bytes, over Compute Engine's limit of 256 KB (262,144 bytes) for one metadata value. Make cairn_brand_logo_file or cairn_plans_file smaller, or shorten cairn_extra_args."
    }
  }

  depends_on = [
    google_project_iam_member.vm_log_writer,
    google_project_iam_member.vm_metric_writer,
    google_artifact_registry_repository_iam_member.vm_reader,
    google_storage_bucket_iam_member.vm_replica,
    google_secret_manager_secret_iam_member.vm_env,
  ]
}
