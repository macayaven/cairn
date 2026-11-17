terraform {
  required_version = ">= 1.13.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.10"
    }
  }

  # Partial configuration: `tofu init -backend-config=backend.hcl` supplies the bucket and
  # prefix, so neither state nor the state bucket's name lives in this repository.
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = local.zone
}
