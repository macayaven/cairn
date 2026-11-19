locals {
  region = substr(var.zone, 0, length(var.zone) - 2)
  cloud_init = templatefile("${path.module}/../cloud-init.yaml.tftpl", {
    quick_dir        = "${path.module}/.."
    serve_extra_args = var.serve_extra_args
    config = {
      CAIRN_CLOUD            = "gcp"
      CAIRN_DOMAIN           = var.domain
      ACME_EMAIL             = var.acme_email
      CAIRN_IMAGE            = var.image
      CAIRN_SETTINGS_SECRET  = google_secret_manager_secret.settings.id
      LITESTREAM_REPLICA_URL = "gs://${google_storage_bucket.replica.name}/cairn"
    }
  })
}

resource "google_project_service" "api" {
  for_each = toset([
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iap.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ])

  service            = each.key
  disable_on_destroy = false
}

data "google_compute_image" "ubuntu" {
  family  = "ubuntu-2404-lts-amd64"
  project = "ubuntu-os-cloud"

  depends_on = [google_project_service.api["compute.googleapis.com"]]
}

resource "google_compute_network" "app" {
  name                    = "cairn-quick"
  auto_create_subnetworks = false

  depends_on = [google_project_service.api["compute.googleapis.com"]]
}

resource "google_compute_subnetwork" "app" {
  name          = "cairn-quick"
  ip_cidr_range = "10.42.0.0/24"
  network       = google_compute_network.app.self_link
  stack_type    = "IPV4_ONLY"
}

resource "google_compute_address" "app" {
  name         = "cairn-quick"
  address_type = "EXTERNAL"

  depends_on = [google_project_service.api["compute.googleapis.com"]]
}

resource "google_compute_firewall" "web" {
  name          = "cairn-quick-web"
  network       = google_compute_network.app.self_link
  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["cairn-quick"]

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}

resource "google_compute_firewall" "iap_ssh" {
  name          = "cairn-quick-iap-ssh"
  network       = google_compute_network.app.self_link
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["cairn-quick"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_storage_bucket" "replica" {
  name                        = "cairn-quick-${var.project_id}"
  location                    = upper(local.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  # Google-managed encryption is the service default. Litestream manages retention.
  soft_delete_policy {
    retention_duration_seconds = 0
  }

  depends_on = [google_project_service.api["storage.googleapis.com"]]
}

resource "google_secret_manager_secret" "settings" {
  secret_id = "cairn-quick-env"

  replication {
    auto {}
  }

  depends_on = [google_project_service.api["secretmanager.googleapis.com"]]
}

resource "google_service_account" "app" {
  account_id   = "cairn-quick"
  display_name = "cairn quick VM"

  depends_on = [google_project_service.api["iam.googleapis.com"]]
}

resource "google_project_iam_custom_role" "replica" {
  role_id = "cairnQuickReplica"
  title   = "cairn replica objects"
  permissions = [
    "storage.objects.create",
    "storage.objects.delete",
    "storage.objects.get",
    "storage.objects.list",
  ]

  depends_on = [google_project_service.api["iam.googleapis.com"]]
}

resource "google_storage_bucket_iam_member" "replica" {
  bucket = google_storage_bucket.replica.name
  role   = google_project_iam_custom_role.replica.name
  member = google_service_account.app.member
}

resource "google_project_iam_custom_role" "settings" {
  role_id = "cairnQuickSettings"
  title   = "cairn settings secret versions"
  permissions = [
    "secretmanager.versions.access",
    "secretmanager.versions.list",
  ]

  depends_on = [google_project_service.api["iam.googleapis.com"]]
}

resource "google_secret_manager_secret_iam_member" "settings" {
  secret_id = google_secret_manager_secret.settings.secret_id
  role      = google_project_iam_custom_role.settings.name
  member    = google_service_account.app.member
}

# cloud-init writes the files on first boot. A changed bundle requires a fresh VM.
resource "terraform_data" "bootstrap" {
  input = sha256(local.cloud_init)
}

resource "google_compute_instance" "app" {
  name         = "cairn-quick"
  machine_type = "e2-small"
  tags         = ["cairn-quick"]

  boot_disk {
    auto_delete = true

    initialize_params {
      image = data.google_compute_image.ubuntu.self_link
      size  = 20
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.app.self_link

    access_config {
      nat_ip = google_compute_address.app.address
    }
  }

  metadata = {
    enable-oslogin = "TRUE"
    user-data      = local.cloud_init
  }

  service_account {
    email  = google_service_account.app.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  lifecycle {
    replace_triggered_by = [terraform_data.bootstrap]
  }

  depends_on = [
    google_compute_firewall.web,
    google_compute_firewall.iap_ssh,
    google_storage_bucket_iam_member.replica,
    google_secret_manager_secret_iam_member.settings,
    google_project_service.api["iap.googleapis.com"],
  ]
}
