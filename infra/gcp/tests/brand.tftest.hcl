# cairn_brand_name, cairn_brand_logo_file and cairn_brand_logo_alt through a real plan, with a
# mock provider (no cloud project, no credentials): run `tofu -chdir=infra/gcp init
# -backend=false` once, then `tofu -chdir=infra/gcp test`. Every name and file is made up;
# tests/fixtures/ holds the logos.

mock_provider "google" {
  # The provider checks the format of a service account's name and email where other
  # resources use them; the mock's random strings would not pass.
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

# --- no brand: nothing about the VM changes ------------------------------------------------

run "without_a_brand_the_user_data_has_no_byte_more" {
  command = plan

  assert {
    condition     = local.brand_files == ""
    error_message = "with no brand variable set, brand_files is not empty"
  }

  assert {
    condition     = length([for f in yamldecode(local.cloud_init).write_files : f.path if startswith(f.path, "/etc/cairn/brand/")]) == 0
    error_message = "with no brand variable set, cloud-init writes a brand file"
  }
}

# --- each name arrives exactly ----------------------------------------------------------------

run "a_name_with_a_space" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "Knowledge Base"
  }
  assert {
    condition     = base64decode(one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/name"])) == "Knowledge Base"
    error_message = "the name does not arrive exactly"
  }
  assert {
    condition     = strcontains(local.cloud_init, "  - path: /etc/cairn/brand/name\n    permissions: \"0644\"\n    owner: root\n    encoding: b64\n    content: \"S25vd2xlZGdlIEJhc2U=\"\n\n  - path: /etc/systemd/system/cairn-disk.service\n")
    error_message = "the name's entry is not the one scripts/rehearse_host.py builds"
  }
}

run "a_name_with_an_ampersand" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "R&D notes"
  }
  assert {
    condition     = base64decode(one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/name"])) == "R&D notes"
    error_message = "the name does not arrive exactly"
  }
}

run "a_name_with_an_apostrophe" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "O'Brien's"
  }
  assert {
    condition     = base64decode(one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/name"])) == "O'Brien's"
    error_message = "the name does not arrive exactly"
  }
}

run "a_name_with_an_accent" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "Café notes"
  }
  assert {
    condition     = base64decode(one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/name"])) == "Café notes"
    error_message = "the name does not arrive exactly"
  }
  assert {
    condition     = one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/name"]) == "Q2Fmw6kgbm90ZXM="
    error_message = "the name does not travel as its UTF-8 bytes"
  }
}

run "a_name_with_a_dollar" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "$HOME notes"
  }
  assert {
    condition     = base64decode(one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/name"])) == "$HOME notes"
    error_message = "the name does not arrive exactly"
  }
}

run "a_name_with_backticks" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "`id` notes"
  }
  assert {
    condition     = base64decode(one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/name"])) == "`id` notes"
    error_message = "the name does not arrive exactly"
  }
}

run "a_name_with_a_double_quote" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "The \"Base\""
  }
  assert {
    condition     = base64decode(one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/name"])) == "The \"Base\""
    error_message = "the name does not arrive exactly"
  }
}

run "a_name_with_a_backslash" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "Notes\\Base"
  }
  assert {
    condition     = base64decode(one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/name"])) == "Notes\\Base"
    error_message = "the name does not arrive exactly"
  }
}

run "a_name_of_sixty_characters_of_four_bytes_each" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E"
  }
  assert {
    condition     = length(base64decode(one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/name"]))) == 60
    error_message = "a 60-character name does not arrive whole"
  }
}

# --- names refused at plan time ---------------------------------------------------------------

run "a_name_of_sixty_one_characters_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "Knowledge Base Knowledge Base Knowledge Base Knowledge Base K"
  }
  expect_failures = [var.cairn_brand_name]
}

run "a_name_with_a_tab_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "Knowledge\tBase"
  }
  expect_failures = [var.cairn_brand_name]
}

run "a_name_with_a_line_break_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "Knowledge\nBase"
  }
  expect_failures = [var.cairn_brand_name]
}

run "a_name_with_a_c1_control_character_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "Knowledge\u0085Base"
  }
  expect_failures = [var.cairn_brand_name]
}

run "a_name_with_angle_brackets_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "<b>Knowledge Base</b>"
  }
  expect_failures = [var.cairn_brand_name]
}

# serve refuses these at start (cairn.web.brand), so the plan refuses them first.
run "a_name_with_a_character_that_is_not_seen_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "Knowledge\u200BBase"
  }
  expect_failures = [var.cairn_brand_name]
}

run "a_name_of_nothing_but_spaces_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google"
    cairn_brand_name = "   "
  }
  expect_failures = [var.cairn_brand_name]
}

# --- the logo and its text alternative --------------------------------------------------------

run "an_svg_logo_and_its_alt_arrive_exactly" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo.svg"
    cairn_brand_logo_alt  = "Juniper Field"
  }
  assert {
    condition     = one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/logo"]) == filebase64("tests/fixtures/logo.svg")
    error_message = "the logo does not arrive exactly"
  }
  assert {
    condition     = base64decode(one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/logo-alt"])) == "Juniper Field"
    error_message = "the alt text does not arrive exactly"
  }
  assert {
    condition     = [for f in yamldecode(local.cloud_init).write_files : f.path if startswith(f.path, "/etc/cairn/brand/")] == ["/etc/cairn/brand/name", "/etc/cairn/brand/logo", "/etc/cairn/brand/logo-alt"]
    error_message = "the brand files are not written in the order scripts/rehearse_host.py builds them"
  }
  assert {
    condition     = alltrue([for f in yamldecode(local.cloud_init).write_files : f.permissions == "0644" && f.owner == "root" && f.encoding == "b64" if startswith(f.path, "/etc/cairn/brand/")])
    error_message = "a brand file is not root's, mode 0644, base64"
  }
}

run "a_png_logo_arrives_exactly" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in github"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo.png"
  }
  assert {
    condition     = one([for f in yamldecode(local.cloud_init).write_files : f.content if f.path == "/etc/cairn/brand/logo"]) == filebase64("tests/fixtures/logo.png")
    error_message = "the logo does not arrive exactly"
  }
}

run "an_svg_after_a_byte_order_mark_a_declaration_and_a_comment_is_taken" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-declared-no-doctype.svg"
  }
}

run "an_svg_with_attributes_before_its_namespace_is_taken" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-attributes-first.svg"
  }
}

# serve refuses each of these at start (cairn.web.brand.read_logo), so the plan refuses them
# first. Each file is under the cap and not empty, so only the format checks can refuse it.
run "an_svg_with_a_document_type_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-declared.svg"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "an_svg_with_a_document_type_between_comments_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-doctype-between-comments.svg"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "an_svg_with_no_namespace_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-no-namespace.svg"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "an_svg_root_in_another_namespace_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-other-namespace.svg"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "an_svg_with_the_namespace_only_on_a_child_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-namespace-on-a-child.svg"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "an_svg_with_a_space_before_its_declaration_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-space-before-declaration.svg"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "a_png_signature_without_the_ihdr_chunk_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-no-ihdr.png"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "a_logo_tofu_cannot_read_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/no-such-logo.svg"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "an_empty_logo_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/empty.svg"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "a_logo_at_the_cap_is_taken" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-at-cap.png"
  }
}

run "a_logo_one_byte_over_the_cap_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-over-cap.png"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "html_is_not_an_svg" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/not-a-logo.html"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "a_gif_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/not-a-logo.gif"
  }
  expect_failures = [var.cairn_brand_logo_file]
}

run "an_alt_of_two_hundred_characters_is_taken" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo.svg"
    cairn_brand_logo_alt  = "Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Junid"
  }
}

run "an_alt_of_two_hundred_and_one_characters_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo.svg"
    cairn_brand_logo_alt  = "Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Juniper Field, Junids"
  }
  expect_failures = [var.cairn_brand_logo_alt]
}

run "an_alt_with_a_character_that_is_not_seen_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo.svg"
    cairn_brand_logo_alt  = "Juniper\u200BField"
  }
  expect_failures = [var.cairn_brand_logo_alt]
}

run "an_alt_with_angle_brackets_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo.svg"
    cairn_brand_logo_alt  = "<Juniper Field>"
  }
  expect_failures = [var.cairn_brand_logo_alt]
}

# --- settings that belong together ------------------------------------------------------------

run "a_logo_without_a_name_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google"
    cairn_brand_logo_file = "tests/fixtures/logo.svg"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "an_alt_without_a_logo_is_refused" {
  command = plan
  variables {
    cairn_extra_args     = "--sign-in google"
    cairn_brand_name     = "Knowledge Base"
    cairn_brand_logo_alt = "Juniper Field"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "a_name_without_sign_in_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--join personal"
    cairn_brand_name = "Knowledge Base"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "a_wall_without_sign_in_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--join personal --wall"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "session_days_without_sign_in_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--session-days 30"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "a_wall_and_session_days_with_sign_in_are_taken" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --join personal --wall --session-days 30"
  }
}

# serve takes --session-days N for a whole number N from 1 to 365 and refuses anything else at
# start. Each run below has --sign-in, so only the check on the number can refuse it.
run "session_days_with_an_equals_sign_is_taken" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --session-days=30"
  }
}

run "session_days_of_1_and_of_365_are_taken" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --session-days 1  --session-days=365 --wall"
  }
}

run "session_days_with_no_number_at_the_end_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --session-days"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "session_days_with_no_number_before_another_option_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --session-days --wall"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "session_days_with_an_empty_equals_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --session-days= --wall"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "session_days_of_0_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --session-days 0"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "session_days_of_366_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --session-days=366"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "session_days_of_a_fraction_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --session-days 1.5"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "session_days_of_a_word_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --session-days=thirty"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "a_wall_with_a_value_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --wall=yes"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "a_second_session_days_out_of_range_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --session-days 30 --session-days 400"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "a_brand_name_in_the_extra_args_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --brand-name=Knowledge"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "a_brand_logo_in_the_extra_args_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --brand-logo /etc/cairn/logo.svg"
  }
  expect_failures = [google_compute_instance.cairn]
}

run "a_brand_logo_alt_in_the_extra_args_is_refused" {
  command = plan
  variables {
    cairn_extra_args = "--sign-in google --brand-logo-alt Juniper"
  }
  expect_failures = [google_compute_instance.cairn]
}

# --- the size of the whole user-data ----------------------------------------------------------

run "the_largest_plans_file_and_the_largest_logo_fit_together" {
  command = plan
  variables {
    domain                = "a-long-host-name-for-the-size-check.knowledge-base.example.com"
    name                  = "cairn-knowledge-base"
    replica_path          = "knowledge-base/replica-after-the-restore-of-2026-11-14"
    cairn_extra_args      = "--sign-in google --join personal --wall --session-days 30 --allowed-host cairn.example.org --sync-every 300 --allowed-host mcp.knowledge-base.example.com"
    cairn_plans_file      = "tests/fixtures/plans-largest.yaml"
    cairn_brand_name      = "\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E"
    cairn_brand_logo_file = "tests/fixtures/logo-at-cap.png"
    cairn_brand_logo_alt  = "\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E\U0001D50E"
  }
  assert {
    condition     = local.user_data_bytes <= 262144
    error_message = "the user-data is over Compute Engine's 256 KB limit for one metadata value"
  }
  assert {
    # host.sh travels gzip-compressed (about 15 KB); the plans file and the logo are most of
    # what is left, and without either the user-data is under 160,000 bytes.
    condition     = local.user_data_bytes > 200000
    error_message = "the size check did not see the largest plans file and logo"
  }
}

run "a_user_data_over_the_limit_is_refused" {
  command = plan
  variables {
    cairn_extra_args      = "--sign-in google ${join(" ", [for i in range(800) : "--allowed-host h${i}.knowledge-base.example.com"])}"
    cairn_plans_file      = "tests/fixtures/plans-largest.yaml"
    cairn_brand_name      = "Knowledge Base"
    cairn_brand_logo_file = "tests/fixtures/logo-at-cap.png"
  }
  expect_failures = [google_compute_instance.cairn]
}
