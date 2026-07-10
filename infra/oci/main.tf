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

locals {
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[var.availability_domain_index].name
  image_id            = var.image_ocid != "" ? var.image_ocid : data.oci_core_images.ubuntu.images[0].id
  cloud_init = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    repo_url              = var.repo_url
    repo_branch           = var.repo_branch
    database_url          = var.database_url
    proxy_url             = var.proxy_url
    telegram_bot_token    = var.telegram_bot_token
    telegram_chat_id      = var.telegram_chat_id
    resend_api_key        = var.resend_api_key
    resend_from           = var.resend_from
    site_url              = var.site_url
    crawl_schedule        = var.crawl_schedule
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

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_gb
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
    user_data           = base64encode(local.cloud_init)
  }
}