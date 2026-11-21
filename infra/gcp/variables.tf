# Values that end up in /etc/cairn/config.env on the VM are sourced by bash there, so their
# validations also keep out quotes, dollars, backticks, backslashes and newlines.

variable "project_id" {
  description = "Google Cloud project that holds every resource."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a project ID (6-30 lowercase letters, digits or hyphens), not a project name or number."
  }
}

variable "region" {
  description = "Region for the VM, the disk snapshots, the bucket, the registry and the secret."
  type        = string
  default     = "europe-west1"

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region))
    error_message = "region must look like europe-west1."
  }
}

variable "zone" {
  description = "Zone for the VM and its data disk. Defaults to zone b of the region."
  type        = string
  default     = null

  validation {
    condition     = var.zone == null || can(regex("^[a-z]+-[a-z]+[0-9]+-[a-z]$", var.zone))
    error_message = "zone must look like europe-west1-b."
  }
}

variable "name" {
  description = "Prefix for resource names (VM, disk, service accounts, registry, secret)."
  type        = string
  default     = "cairn"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name))
    error_message = "name must be 3-20 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "domain" {
  description = "Public hostname, e.g. cairn.example.net. Its A record must point at the ip_address output."
  type        = string

  validation {
    condition     = can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,}$", var.domain))
    error_message = "domain must be a lowercase hostname without scheme, port or path."
  }
}

variable "alert_email" {
  description = "Address that receives the uptime alert."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$", var.alert_email))
    error_message = "alert_email must be an email address."
  }
}

variable "acme_email" {
  description = "Contact address for the certificate authority account. Defaults to alert_email."
  type        = string
  default     = null

  validation {
    condition     = var.acme_email == null || can(regex("^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$", var.acme_email))
    error_message = "acme_email must be an email address."
  }
}

variable "github_repository" {
  description = "The one GitHub repository allowed to deploy, as owner/name."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "github_repository must look like owner/name."
  }
}

variable "github_repository_id" {
  description = "Numeric ID of that repository (gh api repos/OWNER/NAME --jq .id). The identity provider checks it next to the name, so a deleted and re-registered repository name cannot deploy."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "github_repository_id must be the numeric repository ID, digits only."
  }
}

variable "github_workflow_path" {
  description = "The one workflow file in that repository whose runs may deploy. Tokens from any other workflow file are refused."
  type        = string
  default     = ".github/workflows/ci-cd.yml"

  validation {
    condition     = can(regex("^\\.github/workflows/[A-Za-z0-9._-]+\\.ya?ml$", var.github_workflow_path))
    error_message = "github_workflow_path must look like .github/workflows/NAME.yml."
  }
}

variable "github_environment" {
  description = "The GitHub environment the deploy job runs in. The identity provider gives a token only to jobs in it, so its required reviewers gate every deploy."
  type        = string
  default     = "production"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]{1,255}$", var.github_environment))
    error_message = "github_environment must be an environment name: letters, digits, dots, hyphens or underscores."
  }
}

variable "deploy_tag" {
  description = "The tag of the image `cairn` in the registry that the VM follows. Whatever digest it names is deployed; moving it back is a rollback."
  type        = string
  default     = "production"

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$", var.deploy_tag))
    error_message = "deploy_tag must be a valid image tag: letters, digits, dots, hyphens or underscores, at most 128 characters."
  }
}

variable "registry_keep_versions" {
  description = "How many of the newest versions of the image cairn the registry keeps whatever their age. A deploy pushes an index and its children (two platforms and their build attestations, about five versions), so 50 is about the last ten deploys."
  type        = number
  default     = 50

  validation {
    condition     = var.registry_keep_versions >= 10 && floor(var.registry_keep_versions) == var.registry_keep_versions
    error_message = "registry_keep_versions must be a whole number of at least 10."
  }
}

variable "registry_delete_after_days" {
  description = "Age in days after which the registry deletes an image that is neither among the newest registry_keep_versions nor named by deploy_tag, tagged or not."
  type        = number
  default     = 14

  validation {
    condition     = var.registry_delete_after_days >= 1 && floor(var.registry_delete_after_days) == var.registry_delete_after_days
    error_message = "registry_delete_after_days must be a whole number of days, at least 1."
  }
}

variable "machine_type" {
  description = "Compute Engine machine type. The image is built for x86-64 and arm64; COS stable here is the x86 family."
  type        = string
  default     = "e2-small"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]*-[a-z0-9-]+$", var.machine_type))
    error_message = "machine_type must look like e2-small."
  }

  # container_limits' defaults are sized for e2-small's 2 GiB; these three have less.
  validation {
    condition     = !contains(["e2-micro", "f1-micro", "g1-small"], var.machine_type)
    error_message = "machine_type must have at least the 2 GiB of e2-small: e2-micro, f1-micro and g1-small have less than the containers' memory limits (container_limits) and the host need."
  }
}

variable "container_limits" {
  description = "Memory (MiB, with no swap on top) and process limits of the three containers the VM runs, so that one of them cannot take the VM down. The defaults sum to 1.5 GiB and leave 512 MiB of e2-small's 2 GiB, the smallest machine machine_type takes, for the OS, Docker and the host's own short containers. Raise them with the machine. A change needs a restart of the VM (README, \"Changing the configuration\")."
  type = object({
    cairn      = object({ memory_mb = number, pids = number })
    caddy      = object({ memory_mb = number, pids = number })
    litestream = object({ memory_mb = number, pids = number })
  })
  default = {
    cairn      = { memory_mb = 1024, pids = 512 }
    caddy      = { memory_mb = 256, pids = 256 }
    litestream = { memory_mb = 256, pids = 256 }
  }

  validation {
    condition     = alltrue([for c in values(var.container_limits) : c.memory_mb >= 64 && floor(c.memory_mb) == c.memory_mb && c.pids >= 32 && floor(c.pids) == c.pids])
    error_message = "container_limits: each memory_mb must be a whole number of at least 64 and each pids a whole number of at least 32."
  }
}

variable "boot_disk_size_gb" {
  description = "Boot disk size. It holds the OS, the pulled container images and their logs, no data. A change replaces the VM (README, \"The VM is broken, the data disk is fine\")."
  type        = number
  default     = 20

  validation {
    condition     = var.boot_disk_size_gb >= 10 && floor(var.boot_disk_size_gb) == var.boot_disk_size_gb
    error_message = "boot_disk_size_gb must be a whole number of at least 10."
  }
}

variable "data_disk_size_gb" {
  description = "Size of the persistent data disk (database and certificates). Can grow, never shrink."
  type        = number
  default     = 20

  validation {
    condition     = var.data_disk_size_gb >= 10 && floor(var.data_disk_size_gb) == var.data_disk_size_gb
    error_message = "data_disk_size_gb must be a whole number of at least 10."
  }
}

variable "cairn_extra_args" {
  description = "Extra arguments appended to `serve`, e.g. \"--allowed-host mcp.example.net\". Split on spaces."
  type        = string
  default     = ""

  validation {
    condition     = can(regex("^[A-Za-z0-9 ._:/=@,+-]*$", var.cairn_extra_args))
    error_message = "cairn_extra_args may contain only letters, digits, spaces and . _ : / = @ , + -"
  }
}

variable "cairn_plans_file" {
  description = "A plans file on the machine that runs tofu (a copy of deploy/plans.example.yaml), relative to the directory tofu runs in or absolute. cloud-init installs it at /etc/cairn/plans.yaml and cairn starts with --plans /etc/cairn/plans.yaml. Empty: cairn starts without plans. A change needs a restart of the VM (README, \"Changing the configuration\")."
  type        = string
  default     = ""

  # The file's content travels in the VM's user-data, which anyone who may read the instance's
  # metadata can read. That is acceptable because plans are not secret: a workspace's Usage
  # page shows its plan's limits.
  # The checks keep it small and in the form the template's `content: |` writes byte for byte.
  validation {
    condition     = var.cairn_plans_file == "" || can(file(var.cairn_plans_file))
    error_message = "cairn_plans_file must name a file tofu can read as UTF-8, relative to the directory tofu runs in (infra/gcp) or absolute. Leave it empty for no plans."
  }

  validation {
    condition     = var.cairn_plans_file == "" ? true : try(file(var.cairn_plans_file) != "", true)
    error_message = "cairn_plans_file names an empty file. Copy deploy/plans.example.yaml and set its caps, or leave cairn_plans_file empty for no plans."
  }

  validation {
    condition     = var.cairn_plans_file == "" ? true : try(length(file(var.cairn_plans_file)) <= 16384, true)
    error_message = "cairn_plans_file is larger than 16 KiB; the VM's whole user-data must stay well under Compute Engine's metadata limit. Shorten its comments."
  }

  validation {
    condition     = var.cairn_plans_file == "" ? true : try(file(var.cairn_plans_file) == "" || can(regex("^[\t!-~]([\t\n -~]*[\t -~])?\n$", file(var.cairn_plans_file))), true)
    error_message = "cairn_plans_file must be printable ASCII text with Unix line breaks (tabs allowed), start with a character other than a space or a line break, and end with exactly one line break, so that cloud-init writes it byte for byte. Write other characters as \\u escapes inside double quotes."
  }
}

# The brand: the name, logo and logo text cairn shows in place of its own. Each value travels
# base64 in the VM's user-data and lands in a file under /etc/cairn/brand that host.sh hands
# to serve as one argument (main.tf, files/host.sh), so no shell evaluates it and nothing
# splits it. The checks on the name and the alt text are serve's own rules, refused at plan
# time instead of by a container that never comes up; the logo's checks see only how the file
# starts, and serve reads the whole file at start. main.tf's preconditions check the settings
# that go together.

variable "cairn_brand_name" {
  description = "The name cairn shows in place of its own (serve --brand-name), e.g. \"Knowledge Base\". 1 to 60 characters, not only spaces, with no control character, no character that is not seen (a zero-width space, a direction mark) and no < or >: what serve itself takes. Needs --sign-in in cairn_extra_args. Empty: cairn's own name. A change needs a restart of the VM (README, \"Changing the configuration\")."
  type        = string
  default     = ""

  # The brand travels in the VM's user-data, which anyone who may read the instance's
  # metadata can read. That is acceptable because the brand is not secret: the sign-in page
  # shows the name, the logo and its text to every visitor.
  # 60 counts code points, as the regex does; length() would count grapheme clusters.
  validation {
    condition     = var.cairn_brand_name == "" || (trimspace(var.cairn_brand_name) != "" && can(regex("^[^\\p{Cc}\\p{Cf}<>]{1,60}$", var.cairn_brand_name)))
    error_message = "cairn_brand_name must be 1 to 60 characters, not only spaces, with no control character (such as a tab or a line break), no character that is not seen (such as a zero-width space) and no < or >. Leave it empty to keep cairn's own name."
  }
}

variable "cairn_brand_logo_file" {
  description = "A logo on the machine that runs tofu, relative to the directory tofu runs in or absolute: an SVG or a PNG of at most 60 KiB (61,440 bytes). cloud-init installs it at /etc/cairn/brand/logo and cairn starts with --brand-logo. serve itself takes up to 256 KB; the VM takes less because the whole user-data, which carries the logo base64 next to the largest plans file, must stay under Compute Engine's 256 KB limit for one metadata value. tofu plan checks only how the file starts: the 16 bytes every PNG starts with (its signature and the IHDR chunk's length and name), or UTF-8 text whose first element is <svg with xmlns=\"http://www.w3.org/2000/svg\" among its attributes, after at most a byte order mark, an XML declaration, white space and comments, and no document type. serve reads the whole file at start (an SVG must be one well-formed XML document) and may still refuse one the plan took; it then exits, and on the VM that is a container that does not come up. Needs cairn_brand_name. Empty: no logo."
  type        = string
  default     = ""

  validation {
    condition     = var.cairn_brand_logo_file == "" || can(filebase64(var.cairn_brand_logo_file))
    error_message = "cairn_brand_logo_file must name a file tofu can read, relative to the directory tofu runs in (infra/gcp) or absolute. Leave it empty for no logo."
  }

  validation {
    condition     = var.cairn_brand_logo_file == "" ? true : try(filebase64(var.cairn_brand_logo_file) != "", true)
    error_message = "cairn_brand_logo_file names an empty file. Name an SVG or a PNG, or leave it empty for no logo."
  }

  # 61,440 is a multiple of 3, so this is exact: at most 61,440 bytes is at most 81,920
  # characters of base64.
  validation {
    condition     = var.cairn_brand_logo_file == "" ? true : try(length(filebase64(var.cairn_brand_logo_file)) <= 81920, true)
    error_message = "cairn_brand_logo_file is larger than 60 KiB (61,440 bytes). serve takes up to 256 KB, but on the VM the logo travels in the user-data next to the plans file, and the whole must stay under Compute Engine's 256 KB limit for one metadata value. Use a smaller file."
  }

  # How the file starts is all a pattern can see; serve reads the whole file at start
  # (cairn.web.brand.read_logo). The start of an SVG: at most a byte order mark, an XML
  # declaration (first, as XML has it), then white space and comments (with no -- inside, as
  # XML has it). White space is XML's four characters, not \s, which in RE2 also takes a form
  # feed. A document type declaration after that start is refused first, so its message is
  # the one shown (under `tofu test`, OpenTofu showed only the first check that failed).
  validation {
    condition     = var.cairn_brand_logo_file == "" ? true : try(!can(regex("^\\x{FEFF}?(<\\?xml[\\t\\r\\n ][^>]*\\?>)?([\\t\\r\\n ]|<!--([^-]|-[^-])*-->)*<!DOCTYPE", file(var.cairn_brand_logo_file))), true)
    error_message = "cairn_brand_logo_file has a document type declaration (<!DOCTYPE ...>). serve refuses a logo that has one, so that reading a logo fetches nothing and expands nothing. Remove the declaration: an SVG needs none."
  }

  # A PNG cannot go through file(), which wants UTF-8: its first 16 bytes, the signature and
  # the IHDR chunk's length and name, are read in base64, where the 22nd character holds the
  # last 2 bits of the R and 4 of the next byte. An SVG: the start above, then the root
  # element <svg, attribute by attribute with quoted values, one of them xmlns with the SVG
  # namespace.
  validation {
    condition     = var.cairn_brand_logo_file == "" ? true : try(length(regexall("^iVBORw0KGgoAAAANSUhEU[g-v]", filebase64(var.cairn_brand_logo_file))) > 0 || can(regex("^\\x{FEFF}?(<\\?xml[\\t\\r\\n ][^>]*\\?>)?([\\t\\r\\n ]|<!--([^-]|-[^-])*-->)*<svg([\\t\\r\\n ]+[^\\t\\r\\n =<>/\"']+[\\t\\r\\n ]*=[\\t\\r\\n ]*(\"[^\"<]*\"|'[^'<]*'))*[\\t\\r\\n ]+xmlns[\\t\\r\\n ]*=[\\t\\r\\n ]*(\"http://www\\.w3\\.org/2000/svg\"|'http://www\\.w3\\.org/2000/svg')([\\t\\r\\n ]+[^\\t\\r\\n =<>/\"']+[\\t\\r\\n ]*=[\\t\\r\\n ]*(\"[^\"<]*\"|'[^'<]*'))*[\\t\\r\\n ]*/?>", file(var.cairn_brand_logo_file))), true)
    error_message = "cairn_brand_logo_file must be a PNG (it starts with the PNG signature and the IHDR chunk) or an SVG: UTF-8 text whose first element is <svg with xmlns=\"http://www.w3.org/2000/svg\" among its attributes, after at most a byte order mark, an XML declaration, white space and comments."
  }
}

variable "cairn_brand_logo_alt" {
  description = "The logo's text alternative (serve --brand-logo-alt), e.g. \"Juniper Field\". The rules of cairn_brand_name, and longer: 1 to 200 characters, not only spaces, no control character, no character that is not seen, no < or >. Needs cairn_brand_logo_file. Empty: none."
  type        = string
  default     = ""

  validation {
    condition     = var.cairn_brand_logo_alt == "" || (trimspace(var.cairn_brand_logo_alt) != "" && can(regex("^[^\\p{Cc}\\p{Cf}<>]{1,200}$", var.cairn_brand_logo_alt)))
    error_message = "cairn_brand_logo_alt must be 1 to 200 characters, not only spaces, with no control character (such as a tab or a line break), no character that is not seen (such as a zero-width space) and no < or >. Leave it empty for no alt text."
  }
}

variable "caddy_image" {
  description = "Caddy image, pinned by tag and by the digest of its multi-platform index."
  type        = string
  default     = "caddy:2.11.9@sha256:0c2243efcf437ab7f7a978f1027fcf0d4f9be8a75df27b1b3fbc5dbe0ffa8cf0"

  validation {
    condition     = can(regex("^[a-z0-9][A-Za-z0-9._/:@-]*$", var.caddy_image))
    error_message = "caddy_image must be an image reference."
  }
}

variable "litestream_image" {
  description = "Litestream image, pinned. The configuration is written for 0.5."
  type        = string
  default     = "litestream/litestream:0.5.18@sha256:513bff62a88fc0c86c73d27333c41c54c89fe890a7c32a551600adff9309b11b"

  validation {
    condition     = can(regex("^[a-z0-9][A-Za-z0-9._/:@-]*$", var.litestream_image))
    error_message = "litestream_image must be an image reference."
  }
}

variable "replica_bucket_name" {
  description = "Name of the replication bucket. Defaults to PROJECT_ID-NAME-replica. Bucket names are global."
  type        = string
  default     = null

  validation {
    condition     = var.replica_bucket_name == null || can(regex("^[a-z0-9][a-z0-9_-]{1,61}[a-z0-9]$", var.replica_bucket_name))
    error_message = "replica_bucket_name must be 3-63 lowercase letters, digits, hyphens or underscores."
  }
}

variable "replica_soft_delete_days" {
  description = "How long the bucket keeps what was deleted or overwritten in it, restorable and not deletable for good. This is what stands between a compromised VM and the loss of the replica, and how long a deletion outlives Litestream's own retention in the bucket (README, \"How long a deletion stays in the backups\"). Cloud Storage allows 7 to 90 days; 7 is the least."
  type        = number
  default     = 7

  validation {
    condition     = var.replica_soft_delete_days >= 7 && var.replica_soft_delete_days <= 90 && floor(var.replica_soft_delete_days) == var.replica_soft_delete_days
    error_message = "replica_soft_delete_days must be a whole number from 7 to 90."
  }
}

variable "replica_path" {
  description = "Path inside the bucket that Litestream replicates to. Change it to start a new replica lineage, for example after restoring the database to an earlier point in time."
  type        = string
  default     = "cairn"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*$", var.replica_path))
    error_message = "replica_path must be lowercase path segments of letters, digits, dots, hyphens or underscores."
  }
}

variable "labels" {
  description = "Extra labels for resources that take them."
  type        = map(string)
  default     = {}
}

# --- a baseline a reviewer expects (README, "Bootstrap" and "Logs") --------------------------

variable "data_access_audit_logs" {
  description = "Data Access audit logs (data reads and writes) for Cloud Storage and Secret Manager in the project: who read or changed the replica's objects and who read the secret. On by default. Litestream's uploads and the replica check's listings each make an entry, billed as log volume past the free allowance."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "How many days the project's _Default log bucket keeps logs, the containers' and the audit logs among them. 30 is Google's default; longer is billed."
  type        = number
  default     = 30

  validation {
    condition     = var.log_retention_days >= 1 && var.log_retention_days <= 3650 && floor(var.log_retention_days) == var.log_retention_days
    error_message = "log_retention_days must be a whole number from 1 to 3650."
  }
}

variable "os_login_2fa" {
  description = "Ask for a second factor at every SSH connection through OS Login. On by default: a connection needs a person at a prompt, and unattended SSH (a script, a scheduled job) does not work. An operator whose Google account has no 2-Step Verification is refused SSH through IAP until they switch it on or set this variable to false; the serial console and a start-up script are the ways back in (README, \"Before the next apply on an existing deployment\")."
  type        = bool
  default     = true
}

variable "firewall_logging" {
  description = "Log every connection the two firewall rules allow. Off by default: each is a billed log entry."
  type        = bool
  default     = false
}

variable "vpc_flow_logs" {
  description = "VPC flow logs on the subnet, half of the flows sampled, every 5 minutes. Off by default: billed by volume."
  type        = bool
  default     = false
}

variable "kms_key_name" {
  description = "A Cloud KMS key (projects/P/locations/REGION/keyRings/R/cryptoKeys/K) for an operator who must hold the keys: the data disk, the boot disk, the replica bucket and the environment secret use it. Empty: Google manages the keys, as before. The key must be in region, and Google's service agents need roles/cloudkms.cryptoKeyEncrypterDecrypter on it (README, \"Customer-managed keys\"). Set on an existing deployment, it replaces the disks and the secret: read `tofu plan` first."
  type        = string
  default     = ""

  validation {
    condition     = var.kms_key_name == "" || can(regex("^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/locations/[a-z0-9-]+/keyRings/[A-Za-z0-9_-]+/cryptoKeys/[A-Za-z0-9_-]+$", var.kms_key_name))
    error_message = "kms_key_name must be a key's resource name, projects/PROJECT/locations/REGION/keyRings/RING/cryptoKeys/KEY, or empty."
  }
}
