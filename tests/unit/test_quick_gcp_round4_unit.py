"""Regression checks for the Google Cloud quick-deploy fixes (Q1, Q2, Q3-GCP, Q4-GCP, Q7)."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QUICK = REPO / "infra" / "quick"
GCP = QUICK / "gcp"
PROVIDER = "registry.opentofu.org/hashicorp/google"


def gcp_readme_section() -> str:
    text = (QUICK / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^## Google Cloud\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match is not None
    return match[1]


def teardown_section() -> str:
    section = gcp_readme_section()
    match = re.search(r"^### Google Cloud teardown\n(.*?)(?=^### |\Z)", section, re.M | re.S)
    assert match is not None
    return match[1]


def test_q1_provider_uses_billing_project_and_enables_bootstrap_apis():
    versions = (GCP / "versions.tf").read_text(encoding="utf-8")
    provider_block = re.search(r'provider "google" \{(.*?)^\}', versions, re.M | re.S)
    assert provider_block is not None
    assert re.search(r"^\s+billing_project\s*=\s*var\.project_id$", provider_block[1], re.M)
    assert re.search(r"^\s+user_project_override\s*=\s*true$", provider_block[1], re.M)

    main = (GCP / "main.tf").read_text(encoding="utf-8")
    api_block = re.search(r'resource "google_project_service" "api" \{(.*?)^\}', main, re.M | re.S)
    assert api_block is not None
    apis = set(re.findall(r'^\s+"([^"]+)",$', api_block[1], re.M))
    assert {
        "serviceusage.googleapis.com",
        "cloudresourcemanager.googleapis.com",
        "oslogin.googleapis.com",
    } <= apis
    assert re.search(r"^\s+disable_on_destroy\s*=\s*false$", api_block[1], re.M)

    readme = " ".join(gcp_readme_section().split())
    assert "gcloud auth application-default login" in readme
    assert "serviceusage.googleapis.com" in readme
    assert "cloudresourcemanager.googleapis.com" in readme
    assert "retry" in readme.lower() or "run `tofu apply` again" in readme.lower()


def test_q2_predefined_roles_replace_custom_project_roles():
    tf_text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(GCP.glob("*.tf")))
    assert "google_project_iam_custom_role" not in tf_text

    main = (GCP / "main.tf").read_text(encoding="utf-8")
    replica_iam = re.search(
        r'resource "google_storage_bucket_iam_member" "replica" \{(.*?)^\}', main, re.M | re.S
    )
    assert replica_iam is not None
    assert re.search(r'^\s+role\s*=\s*"roles/storage\.objectUser"$', replica_iam[1], re.M)

    secret_iam = re.search(
        r'resource "google_secret_manager_secret_iam_member" "settings" \{(.*?)^\}',
        main,
        re.M | re.S,
    )
    assert secret_iam is not None
    assert '"roles/secretmanager.secretAccessor"' in secret_iam[1]
    assert '"roles/secretmanager.viewer"' in secret_iam[1]

    readme = gcp_readme_section()
    assert "roles/storage.objectUser" in readme
    assert "roles/secretmanager.secretAccessor" in readme
    assert "roles/secretmanager.viewer" in readme


def test_q4_gcp_boot_image_is_ignored_on_subsequent_applies():
    main = (GCP / "main.tf").read_text(encoding="utf-8")
    vm_block = re.search(r'resource "google_compute_instance" "app" \{(.*?)^\}', main, re.M | re.S)
    assert vm_block is not None
    assert "boot_disk[0].initialize_params[0].image" in vm_block[1]
    assert "replace_triggered_by" in vm_block[1]
    assert "tofu apply -replace=google_compute_instance.app" in gcp_readme_section()


def test_q7_replica_bucket_uses_seven_day_soft_delete():
    main = (GCP / "main.tf").read_text(encoding="utf-8")
    bucket_block = re.search(
        r'resource "google_storage_bucket" "replica" \{(.*?)^\}', main, re.M | re.S
    )
    assert bucket_block is not None
    assert re.search(r"^\s+retention_duration_seconds\s*=\s*604800$", bucket_block[1], re.M)

    teardown = " ".join(teardown_section().split())
    assert "7-day soft delete" in teardown or "7 days" in teardown
    assert 'gcloud storage rm --all-versions "gs://$bucket/**"' in teardown
    assert "tofu destroy" in teardown


def test_q3_gcp_lockfile_matches_versions_constraint_and_standard_platforms():
    versions = (GCP / "versions.tf").read_text(encoding="utf-8")
    constraint_match = re.search(r'google\s*=\s*\{.*?version\s*=\s*"([^"]+)"', versions, re.S)
    assert constraint_match is not None
    expected_constraint = constraint_match[1]
    assert expected_constraint == "~> 8.11.0"

    lock = (GCP / ".terraform.lock.hcl").read_text(encoding="utf-8")
    assert re.findall(r'provider "([^"]+)"', lock) == [PROVIDER]
    version_match = re.search(r'version\s*=\s*"([^"]+)"', lock)
    constraints_match = re.search(r'constraints\s*=\s*"([^"]+)"', lock)
    assert version_match is not None and constraints_match is not None
    assert constraints_match[1] == expected_constraint
    assert version_match[1].startswith("8.11.")
    hashes = lock[lock.index("hashes = [") : lock.index("]")]
    assert len(re.findall(r'"h1:[A-Za-z0-9+/]{43}="', hashes)) >= 5
    assert len(re.findall(r'"zh:[0-9a-f]{64}"', hashes)) >= 5


def test_gcp_sharp_edges_are_hardened_and_documented():
    main = (GCP / "main.tf").read_text(encoding="utf-8")
    subnet = re.search(r'resource "google_compute_subnetwork" "app" \{(.*?)^\}', main, re.M | re.S)
    assert subnet is not None
    assert re.search(r"^\s+private_ip_google_access\s*=\s*true$", subnet[1], re.M)

    vm_block = re.search(r'resource "google_compute_instance" "app" \{(.*?)^\}', main, re.M | re.S)
    assert vm_block is not None
    assert 'block-project-ssh-keys = "TRUE"' in " ".join(vm_block[1].split())
    assert "shielded_instance_config" in vm_block[1]
    assert re.search(r"^\s+enable_secure_boot\s*=\s*true$", vm_block[1], re.M)
    assert re.search(r"^\s+enable_vtpm\s*=\s*true$", vm_block[1], re.M)
    assert re.search(r"^\s+enable_integrity_monitoring\s*=\s*true$", vm_block[1], re.M)

    readme = " ".join(gcp_readme_section().split())
    assert "/opt/cairn/cairn-first-key.*" in readme
    assert "Let's Encrypt" in readme and "5" in readme
