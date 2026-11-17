output "ip_address" {
  description = "Static IPv4 address. Point the domain's A record here."
  value       = google_compute_address.cairn.address
}

output "url" {
  description = "Where cairn answers once DNS points at ip_address and an image is deployed."
  value       = "https://${var.domain}"
}

output "artifact_registry_repository" {
  description = "The registry repository. The VM runs the image <path>/cairn and nothing else."
  value       = local.image_repo
}

output "artifact_registry_host" {
  description = "Registry host for docker login."
  value       = local.registry_host
}

output "deploy_image" {
  description = "The tag the VM follows. Pushing an image under it deploys that image; pointing it back at an earlier digest rolls back."
  value       = local.deploy_image
}

output "workload_identity_provider" {
  description = "Resource name of the GitHub OIDC provider, for google-github-actions/auth `workload_identity_provider`."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  description = "Service account the deploy job impersonates, for google-github-actions/auth `service_account`. It can push to the registry and read the VM's guest attributes, nothing else."
  value       = google_service_account.deployer.email
}

output "project_id" {
  description = "The project, for the deploy job's GCP_PROJECT_ID secret."
  value       = var.project_id
}

output "vm_service_account" {
  description = "Service account the VM runs as."
  value       = google_service_account.vm.email
}

output "instance_name" {
  description = "VM name, for `gcloud compute ssh`."
  value       = google_compute_instance.cairn.name
}

output "instance_zone" {
  description = "VM zone."
  value       = google_compute_instance.cairn.zone
}

output "replica_url" {
  description = "Litestream replica URL, for scripts/restore_drill.py --replica."
  value       = local.replica_url
}

output "env_secret" {
  description = "Secret Manager secret that holds cairn's env file. Add versions with gcloud."
  value       = google_secret_manager_secret.env.secret_id
}

output "rollback_command" {
  description = "What a manual rollback is: point the deploy tag at an earlier digest. Replace DIGEST (sha256:...)."
  value       = "gcloud artifacts docker tags add ${local.image}@DIGEST ${local.deploy_image}"
}
