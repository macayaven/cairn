# Two identities, each with only what it uses. No keys are created for either.
#
#   vm        what the VM runs as: write logs and metrics, pull from the one registry, read,
#             write and delete objects in the one bucket, read the one secret.
#   deployer  what one GitHub Actions job becomes through Workload Identity Federation: push
#             to the one registry, and read the one VM's guest attributes, where the updater
#             reports what it runs. Nothing else: it cannot change the VM, its metadata or its
#             service account. Moving the deploy tag is the deploy.

# --- VM service account ---------------------------------------------------------------------

resource "google_service_account" "vm" {
  account_id   = "${var.name}-vm"
  display_name = "cairn VM"
  description  = "Runtime identity of the cairn VM."
}

resource "google_project_iam_member" "vm_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_project_iam_member" "vm_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_artifact_registry_repository_iam_member" "vm_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.cairn.location
  repository = google_artifact_registry_repository.cairn.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.vm.email}"
}

# Exactly the four calls Litestream 0.5 makes on a replica (gs/replica_client.go): list, read,
# write, and delete for its own retention. No bucket-level permission, so the VM's identity
# cannot change the bucket's soft-delete policy, and no permission to restore, change or
# share objects. Deleting is still deleting: what stands behind it is the soft-delete window
# on the bucket (main.tf), which this identity cannot shorten or empty.
resource "google_project_iam_custom_role" "replica_writer" {
  role_id     = "${replace(var.name, "-", "_")}_replica_writer"
  title       = "cairn replica: Litestream"
  description = "List, read, create and delete objects. Bound on the replica bucket only."
  permissions = [
    "storage.objects.create",
    "storage.objects.delete",
    "storage.objects.get",
    "storage.objects.list",
  ]
}

resource "google_storage_bucket_iam_member" "vm_replica" {
  bucket = google_storage_bucket.replica.name
  role   = google_project_iam_custom_role.replica_writer.name
  member = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_secret_manager_secret_iam_member" "vm_env" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.env.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.vm.email}"
}

# --- GitHub Actions through Workload Identity Federation --------------------------------------

locals {
  github_workflow = "${var.github_repository}/${var.github_workflow_path}"

  # A token is accepted only when every line holds. All six claims are in GitHub's OIDC
  # reference; `workflow_ref` is "<owner>/<repo>/<path>@<ref>".
  #   repository, repository_id  the one repository, by name and by its immutable numeric id
  #                              (a deleted name can be registered again; an id cannot)
  #   event_name                 push only. This is what refuses pull_request_target, whose
  #                              `ref` is the base branch and would pass a check on `ref`
  #                              alone, and manual, scheduled and release runs.
  #   ref                        main only. Tags never deploy: whoever can push a tag would
  #                              otherwise choose the workflow file that gets the token.
  #   workflow_ref               the run's workflow file is the deploy workflow, read from
  #                              main. Another workflow file in the same repository gets no
  #                              token.
  #   environment                the job runs in the GitHub environment github_environment,
  #                              so a token exists only for a job its required reviewers
  #                              approved. A job without an environment has no such claim;
  #                              the condition then fails and no token is issued.
  github_condition = join(" && ", [
    "assertion.repository == \"${var.github_repository}\"",
    "assertion.repository_id == \"${var.github_repository_id}\"",
    "assertion.event_name == \"push\"",
    "assertion.ref == \"refs/heads/main\"",
    "assertion.workflow_ref == \"${local.github_workflow}@refs/heads/main\"",
    "assertion.environment == \"${var.github_environment}\"",
  ])
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "${var.name}-github"
  display_name              = "GitHub Actions"
  description               = "Federated identities for deploying cairn."
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "GitHub Actions OIDC"
  description                        = "Pushes to main of one repository, from one workflow file, in one environment."

  # Every claim the condition reads is mapped, so it shows in audit logs and can be used in
  # a binding. The binding below uses the repository id.
  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository"    = "assertion.repository"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.event_name"    = "assertion.event_name"
    "attribute.ref"           = "assertion.ref"
    "attribute.workflow_ref"  = "assertion.workflow_ref"
    "attribute.environment"   = "assertion.environment"
  }
  attribute_condition = local.github_condition

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  account_id   = "${var.name}-deployer"
  display_name = "cairn deployer"
  description  = "Impersonated by the GitHub Actions deploy job to push images and read the VM's deploy status. Cannot change the VM."
}

resource "google_service_account_iam_member" "deployer_federation" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}

# The deployer's first grant. roles/artifactregistry.writer uploads images and creates or
# moves tags; it cannot delete images or tags. The image it pushes under the deploy tag is the
# code the VM runs next (README, "What the deployer can do").
resource "google_artifact_registry_repository_iam_member" "deployer_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.cairn.location
  repository = google_artifact_registry_repository.cairn.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

# The deployer's second grant: read the guest attributes of the one VM, where files/host.sh
# reports which digest runs and whether it is healthy. One permission, bound on the instance,
# not the project. It reads what the VM published (cairn/status, and the SSH host keys the
# guest environment publishes, which are public); it cannot read the instance's metadata,
# serial console or disks, and cannot write anything.
resource "google_project_iam_custom_role" "deploy_status_reader" {
  role_id     = "${replace(var.name, "-", "_")}_deploy_status_reader"
  title       = "cairn deployer: read the VM's deploy status"
  description = "Read guest attributes. Bound on the cairn instance only."
  permissions = ["compute.instances.getGuestAttributes"]
}

resource "google_compute_instance_iam_member" "deployer_status" {
  project       = var.project_id
  zone          = google_compute_instance.cairn.zone
  instance_name = google_compute_instance.cairn.name
  role          = google_project_iam_custom_role.deploy_status_reader.name
  member        = "serviceAccount:${google_service_account.deployer.email}"

  # An instance's IAM policy goes with the instance. A replaced VM keeps its name, so none of
  # the arguments above change and this binding would not be made again in the same apply:
  # the deploy job could not read the new VM's status until a later apply noticed.
  lifecycle {
    replace_triggered_by = [google_compute_instance.cairn.instance_id]
  }
}
