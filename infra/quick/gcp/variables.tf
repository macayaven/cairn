variable "project_id" {
  description = "Google Cloud project ID with billing enabled."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Enter a Google Cloud project ID, for example example-project."
  }
}

variable "zone" {
  description = "Compute Engine zone. Its region also holds the IPv4 address and backup bucket."
  type        = string
  default     = "us-central1-a"
  nullable    = false

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+-[a-z]$", var.zone))
    error_message = "Enter a Compute Engine zone, for example us-central1-a."
  }
}

variable "domain" {
  description = "Public DNS hostname for cairn, without a scheme, port or path."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.domain) <= 253 && can(regex("^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\\.)+[A-Za-z]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$", var.domain))
    error_message = "Enter a public hostname such as cairn.example.net."
  }
}

variable "acme_email" {
  description = "Contact email for Caddy's certificate account."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.acme_email) <= 254 && can(regex("^[-A-Za-z0-9._+%]+@[-A-Za-z0-9.]+\\.[A-Za-z]{2,}$", var.acme_email))
    error_message = "Enter an email address such as ops@example.net."
  }
}

variable "image" {
  description = "Published cairn image, with a tag or digest. Changing it replaces the VM."
  type        = string
  default     = "ghcr.io/macayaven/cairn:0.1.0"
  nullable    = false

  validation {
    condition     = length(var.image) <= 512 && can(regex("^[A-Za-z0-9][A-Za-z0-9._:/@-]*$", var.image))
    error_message = "Enter a registry image reference without whitespace."
  }
}

variable "serve_extra_args" {
  description = "Extra nonsecret arguments for cairn serve, one string per argument. Changing them replaces the VM."
  type        = list(string)
  default     = []
  nullable    = false

  validation {
    condition     = alltrue([for arg in var.serve_extra_args : can(regex("^[-A-Za-z0-9_.:/@=,+%?& ]+$", arg))])
    error_message = "Use nonempty serve arguments containing only letters, digits, spaces or -_.:/@=,+%?&."
  }
}
