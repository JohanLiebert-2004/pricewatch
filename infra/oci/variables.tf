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
  description = "CIDR allowed to SSH into the web role. GitHub-hosted runners require 0.0.0.0/0; password login is disabled and CI uses a forwarding-only key."
  type        = string
  default     = "0.0.0.0/0"
}

variable "ci_tunnel_public_key_path" {
  description = "Public key for the forwarding-only GitHub Actions SSH identity."
  type        = string
  default     = "~/.ssh/pricewatch_ci_tunnel_ed25519.pub"
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

variable "backup_bucket_name" {
  description = "Private OCI Object Storage bucket for daily database backups."
  type        = string
  default     = "pricewatch-database-backups"
}

variable "enable_arm_db" {
  description = "Provision the optional A1.Flex database replacement. Keep false until an owner-approved capacity/migration window."
  type        = bool
  default     = false
}

variable "database_allowed_cidr" {
  description = "CIDR allowed to connect directly to PostgreSQL. Keep private; GitHub Actions uses an SSH tunnel through the web host."
  type        = string
  default     = "10.42.0.0/16"
}

variable "production_db_private_ip" {
  description = "Stable VCN address of the production x86 DB, used by its VNIC and the forwarding-only CI SSH restriction."
  type        = string
  default     = "10.42.1.9"
}

variable "postgrest_version" {
  description = "Pinned PostgREST release installed by database cloud-init."
  type        = string
  default     = "14.15"
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
