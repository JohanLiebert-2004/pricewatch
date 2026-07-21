terraform {
  required_version = ">= 1.6.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 6.0.0"
    }
  }
}

provider "oci" {
  config_file_profile = var.oci_profile
}

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = var.ubuntu_version
  shape                    = var.instance_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

# Separate image lookup: the DB instance is Arm (A1.Flex) while the original
# runner may be x86 (E2.1.Micro) - Ubuntu image OCIDs differ per architecture.
data "oci_core_images" "ubuntu_arm" {
  count                    = var.enable_arm_db ? 1 : 0
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = var.ubuntu_version
  shape                    = var.db_instance_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

data "oci_core_images" "ubuntu_x86" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = var.ubuntu_version
  shape                    = "VM.Standard.E2.1.Micro"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

locals {
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[var.availability_domain_index].name
  image_id            = var.image_ocid != "" ? var.image_ocid : data.oci_core_images.ubuntu.images[0].id
  web_cloud_init = templatefile("${path.module}/cloud-init-web.yaml.tftpl", {
    repo_url             = var.repo_url
    repo_branch          = var.repo_branch
    site_url             = var.site_url
    backup_bucket_name   = var.backup_bucket_name
    ci_tunnel_public_key = trimspace(file(pathexpand(var.ci_tunnel_public_key_path)))
    db_private_ip        = var.production_db_private_ip
  })
  db_cloud_init = templatefile("${path.module}/cloud-init-db.yaml.tftpl", {
    repo_url           = var.repo_url
    repo_branch        = var.repo_branch
    backup_bucket_name = var.backup_bucket_name
    postgrest_version  = var.postgrest_version
  })
}

resource "oci_core_vcn" "pricewatch" {
  compartment_id = var.compartment_ocid
  display_name   = "pricewatch-vcn"
  cidr_blocks    = ["10.42.0.0/16"]
  dns_label      = "pricewatch"
}

resource "oci_core_internet_gateway" "pricewatch" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.pricewatch.id
  display_name   = "pricewatch-igw"
  enabled        = true
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.pricewatch.id
  display_name   = "pricewatch-public-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.pricewatch.id
  }
}

resource "oci_core_security_list" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.pricewatch.id
  display_name   = "pricewatch-public-sl"

  ingress_security_rules {
    protocol = "6"
    source   = var.ssh_allowed_cidr

    tcp_options {
      min = 22
      max = 22
    }
  }

  # Public web: SSR preview pages + image proxy (nginx, TLS via sslip.io)
  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"

    tcp_options {
      min = 80
      max = 80
    }
  }

  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"

    tcp_options {
      min = 443
      max = 443
    }
  }

  # PostgreSQL is private to the VCN. GitHub Actions reaches it through an
  # SSH local-forward on the web host; it is never exposed directly.
  ingress_security_rules {
    protocol = "6"
    source   = var.database_allowed_cidr

    tcp_options {
      min = 5432
      max = 5432
    }
  }

  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }
}

resource "oci_core_subnet" "public" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.pricewatch.id
  display_name               = "pricewatch-public-subnet"
  cidr_block                 = "10.42.1.0/24"
  dns_label                  = "public"
  route_table_id             = oci_core_route_table.public.id
  security_list_ids          = [oci_core_security_list.public.id]
  prohibit_public_ip_on_vnic = false
}

resource "oci_core_instance" "pricewatch" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.availability_domain
  display_name        = "pricewatch-runner"
  shape               = var.instance_shape

  dynamic "shape_config" {
    for_each = var.enable_shape_config ? [1] : []
    content {
      ocpus         = var.ocpus
      memory_in_gbs = var.memory_gb
    }
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = true
    display_name     = "pricewatch-vnic"
    hostname_label   = "pricewatch"
  }

  source_details {
    source_type = "image"
    source_id   = local.image_id
  }

  metadata = {
    ssh_authorized_keys = file(pathexpand(var.ssh_public_key_path))
    user_data           = base64encode(local.web_cloud_init)
  }

  lifecycle {
    # user_data only runs at first boot; the live VM has since been configured
    # by hand (secrets in /opt/pricewatch.env were deliberately removed from
    # Terraform state). Without this, any cloud-init template drift forces a
    # full instance replacement - destroying the provisioned VM and its IP.
    # Image data sources track the newest Ubuntu release. Changing that ID on
    # an existing boot volume is not an upgrade mechanism and can stop/reboot
    # a production instance, so upgrades are performed explicitly instead.
    ignore_changes = [metadata, source_details[0].source_id]
  }
}
# Self-hosted Postgres + PostgREST instance, replacing Supabase (its free-
# tier egress quota blew and locked the site out - see AGENT_STATE.md). Sized
# for a full production Postgres + all the existing web/bot/embed services
# with real headroom, unlike the original 1GB E2.1.Micro runner. Kept as a
# second instance rather than resizing the original in place, so the
# original stays up as a fallback until this one is verified stable.
resource "oci_core_instance" "pricewatch_db" {
  count               = var.enable_arm_db ? 1 : 0
  compartment_id      = var.compartment_ocid
  availability_domain = local.availability_domain
  display_name        = "pricewatch-db"
  shape               = var.db_instance_shape

  shape_config {
    ocpus         = var.db_ocpus
    memory_in_gbs = var.db_memory_gb
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = true
    display_name     = "pricewatch-db-vnic"
    hostname_label   = "pricewatch-db"
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.ubuntu_arm[0].images[0].id
  }

  metadata = {
    ssh_authorized_keys = file(pathexpand(var.ssh_public_key_path))
    user_data           = base64encode(local.db_cloud_init)
  }

  lifecycle {
    ignore_changes = [metadata, source_details[0].source_id]
  }
}

# Fallback DB host: a second Always Free E2.1.Micro (x86), same shape as the
# original runner (up to 2 allowed on this tier). Unlike A1.Flex, x86
# capacity has been available on demand, so this can provision immediately
# while the ARM pricewatch_db instance above keeps retrying in the
# background. Only 1GB RAM - much tighter than the 24GB ARM target, but
# enough to hold Postgres for the current 704MB DB if the ARM capacity
# shortage drags on. Whichever instance is ready first gets used; the other
# stays idle as a spare, per the same "keep the fallback around" pattern as
# the original runner vs. this migration.
resource "oci_core_instance" "pricewatch_db_x86" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.availability_domain
  display_name        = "pricewatch-db-x86"
  shape               = "VM.Standard.E2.1.Micro"

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = true
    display_name     = "pricewatch-db-x86-vnic"
    hostname_label   = "pricewatch-db-x86"
    private_ip       = var.production_db_private_ip
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.ubuntu_x86.images[0].id
  }

  metadata = {
    ssh_authorized_keys = file(pathexpand(var.ssh_public_key_path))
    user_data           = base64encode(local.db_cloud_init)
  }

  lifecycle {
    ignore_changes = [metadata, source_details[0].source_id]
  }
}

data "oci_objectstorage_namespace" "pricewatch" {
  compartment_id = var.compartment_ocid
}

resource "oci_objectstorage_bucket" "pricewatch_backups" {
  compartment_id = var.compartment_ocid
  namespace      = data.oci_objectstorage_namespace.pricewatch.namespace
  name           = var.backup_bucket_name
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  versioning     = "Enabled"
}

resource "oci_objectstorage_object_lifecycle_policy" "pricewatch_backups" {
  namespace = data.oci_objectstorage_namespace.pricewatch.namespace
  bucket    = oci_objectstorage_bucket.pricewatch_backups.name

  rules {
    name        = "delete-daily-backups-after-30-days"
    action      = "DELETE"
    target      = "objects"
    time_amount = 30
    time_unit   = "DAYS"
    is_enabled  = true
  }
}

resource "oci_identity_dynamic_group" "pricewatch_backup_writer" {
  compartment_id = var.tenancy_ocid
  name           = "pricewatch-backup-writer"
  description    = "Lets Pricewatch DB-hosting instances upload database backups only."
  matching_rule = "Any {${join(", ", concat(
    [
      "instance.id = '${oci_core_instance.pricewatch.id}'",
      "instance.id = '${oci_core_instance.pricewatch_db_x86.id}'"
    ],
    var.enable_arm_db ? ["instance.id = '${oci_core_instance.pricewatch_db[0].id}'"] : []
  ))}}"
}

resource "oci_identity_policy" "pricewatch_backup_writer" {
  compartment_id = var.tenancy_ocid
  name           = "pricewatch-backup-writer"
  description    = "Least-privilege Object Storage access for Pricewatch backups."
  statements = [
    "Allow dynamic-group ${oci_identity_dynamic_group.pricewatch_backup_writer.name} to read buckets in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group ${oci_identity_dynamic_group.pricewatch_backup_writer.name} to manage objects in compartment id ${var.compartment_ocid} where target.bucket.name = '${oci_objectstorage_bucket.pricewatch_backups.name}'",
    "Allow service objectstorage-ap-sydney-1 to manage object-family in compartment id ${var.compartment_ocid} where any {request.permission='BUCKET_INSPECT', request.permission='BUCKET_READ', request.permission='OBJECT_INSPECT', request.permission='OBJECT_DELETE', request.permission='OBJECT_VERSION_DELETE'}"
  ]
}
