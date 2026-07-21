output "public_ip" {
  value = oci_core_instance.pricewatch.public_ip
}

output "ssh_command" {
  value = "ssh ubuntu@${oci_core_instance.pricewatch.public_ip}"
}

output "runner_status_commands" {
  value = [
    "systemctl status pricewatch-cycle.timer",
    "journalctl -u pricewatch-cycle.service -n 200 --no-pager"
  ]
}

output "pricewatch_db_public_ip" {
  value = try(oci_core_instance.pricewatch_db[0].public_ip, null)
}

output "pricewatch_db_ssh_command" {
  value = try("ssh ubuntu@${oci_core_instance.pricewatch_db[0].public_ip}", null)
}

output "pricewatch_db_x86_public_ip" {
  value = oci_core_instance.pricewatch_db_x86.public_ip
}

output "pricewatch_db_x86_ssh_command" {
  value = "ssh ubuntu@${oci_core_instance.pricewatch_db_x86.public_ip}"
}
