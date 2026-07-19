variable "oci_profile" {
  description = "Profile name in ~/.oci/config."
  type        = string
  default     = "DEFAULT"
}

variable "tenancy_ocid" {
  description = "OCI tenancy OCID. Usually the same tenancy value from ~/.oci/config."
  type        = string
}

variable "compartment_ocid" {
  description = "OCI compartment OCID to create resources in. Use tenancy OCID if you are not using a separate compartment."
  type        = string
}

variable "availability_domain_index" {
  description = "Which availability domain to use, zero-based."
  type        = number
  default     = 0
}

variable "instance_shape" {
  description = "Always Free Arm shape."
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "ocpus" {
  description = "OCPUs. Keep <= 2 total across Always Free A1 instances."
  type        = number
  default     = 2
}

variable "memory_gb" {
  description = "Memory in GB. Keep <= 12 total across Always Free A1 instances."
  type        = number
  default     = 12
}

variable "enable_shape_config" {
  description = "Use shape_config for flexible shapes such as VM.Standard.A1.Flex. Disable for fixed shapes such as VM.Standard.E2.1.Micro."
  type        = bool
  default     = true
}
variable "ubuntu_version" {
  description = "Ubuntu image version to search for. Override image_ocid if image lookup fails in your region."
  type        = string
  default     = "24.04"
}

variable "image_ocid" {
  description = "Optional explicit Ubuntu ARM image OCID. Leave blank to auto-select latest Canonical Ubuntu image for the shape."
  type        = string
  default     = ""
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key used to log into the VM."
  type        = string
  default     = "~/.ssh/id_rsa.pub"
}

variable "ssh_allowed_cidr" {
  description = "CIDR allowed to SSH into the VM. Replace with your public IP/32 after first setup."
  type        = string
  default     = "0.0.0.0/0"
}

variable "repo_url" {
  description = "Git repository URL for Pricewatch. Public repo works directly; private repo needs a deploy key or token-based URL."
  type        = string
  default     = "https://github.com/JohanLiebert-2004/pricewatch.git"
}

variable "repo_branch" {
  type    = string
  default = "master"
}


variable "site_url" {
  type    = string
  default = "https://web-pi-blush-48.vercel.app"
}

variable "crawl_schedule" {
  description = "systemd OnCalendar expression."
  type        = string
  default     = "*:0/30"
}
variable "backup_bucket_name" {
  description = "Private OCI Object Storage bucket for daily database backups."
  type        = string
  default     = "pricewatch-database-backups"
}

variable "db_instance_shape" {
  description = "Shape for the self-hosted Postgres/PostgREST instance. Always Free Arm shape, separate from the original E2.1.Micro runner."
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "db_ocpus" {
  description = "OCPUs for the DB instance. Dropped from the full 4 to 1 on 19 July after the full-size request exhausted a 48-attempt/~4h retry window with zero success in ap-sydney-1 - a smaller request has a better chance of finding free capacity, at the cost of headroom versus the original plan. Still well within the 4-OCPU Always Free A1 allowance, so room to grow back later if capacity eases."
  type        = number
  default     = 1
}

variable "db_memory_gb" {
  description = "Memory in GB for the DB instance. Dropped from 24 to 6 alongside db_ocpus (see its description) - still 6x the current pricewatch-db-x86 fallback's 1GB."
  type        = number
  default     = 6
}
