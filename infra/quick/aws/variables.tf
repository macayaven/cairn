variable "region" {
  description = "Commercial AWS region for the VM and its backup bucket."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.region)) && !startswith(var.region, "cn-")
    error_message = "Enter a commercial AWS region, for example us-east-1."
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
