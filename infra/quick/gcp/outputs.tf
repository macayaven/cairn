output "public_ipv4" {
  description = "Set the domain's A record to this address."
  value       = google_compute_address.app.address
}

output "url" {
  value = "https://${var.domain}"
}

output "project_id" {
  value = var.project_id
}

output "backup_bucket" {
  value = google_storage_bucket.replica.name
}

output "settings_secret" {
  description = "Secret ID to pass to gcloud. Contains no secret value."
  value       = google_secret_manager_secret.settings.secret_id
}

output "connect_command" {
  description = "Run locally to open an OS Login shell over IAP."
  value       = "gcloud compute ssh ${google_compute_instance.app.name} --project=${var.project_id} --zone=${var.zone} --tunnel-through-iap"
}

output "first_key_command" {
  description = "Run on the VM after HTTPS works. The command prints the key file's path."
  value       = "sudo /opt/cairn/bootstrap.sh first-key"
}
