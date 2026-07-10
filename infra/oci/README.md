# Oracle Cloud runner for Pricewatch

This provisions one Oracle Cloud Infrastructure VM and runs Pricewatch from
`systemd` every 30 minutes. It keeps Supabase and Vercel unchanged; only the
crawler/detector moves off GitHub Actions.

## Do not paste secrets into chat

OCI API access uses a local config file and private key. Keep both on your
machine only:

```text
C:\Users\tarun\.oci\config
C:\Users\tarun\.oci\oci_api_key.pem
```

Oracle's config file has these fields:

```ini
[DEFAULT]
user=ocid1.user.oc1...
fingerprint=aa:bb:cc:...
tenancy=ocid1.tenancy.oc1...
region=ap-sydney-1
key_file=C:\Users\tarun\.oci\oci_api_key.pem
```

The API private key is not the SSH key for the VM. Do not commit it.

## One-time prerequisites

Install Terraform and create an OCI API key in the Oracle console:

1. Oracle Console -> Profile -> User settings -> API Keys -> Add API Key.
2. Download the private key into `C:\Users\tarun\.oci\`.
3. Copy Oracle's config snippet into `C:\Users\tarun\.oci\config`.
4. Make sure you have an SSH public key at `C:\Users\tarun\.ssh\id_rsa.pub` or set `ssh_public_key_path`.

## Configure

Copy the example vars file:

```powershell
Copy-Item infra\oci\terraform.tfvars.example infra\oci\terraform.tfvars
```

Edit `infra\oci\terraform.tfvars` and fill in:

- `tenancy_ocid`
- `compartment_ocid` - use tenancy OCID if you are not using a separate compartment
- `database_url`
- `proxy_url` for Webshare, if Big W should continue using it
- optional Telegram/Resend values

`terraform.tfvars` is gitignored because it contains secrets.

## Deploy

```powershell
cd infra\oci
terraform init
terraform apply
```

After apply finishes, Terraform prints the VM IP and SSH command.

## Check the runner

```bash
sudo systemctl status pricewatch-cycle.timer
sudo journalctl -u pricewatch-cycle.service -n 200 --no-pager
```

The VM pulls the latest repo code before every run, then runs refresh/crawl for
each retailer and `python run.py detect`.

## Security notes

- The VM only exposes SSH. No public app/API port is opened.
- Replace `ssh_allowed_cidr = "0.0.0.0/0"` with your current public IP plus `/32` after setup.
- Secrets are written to `/opt/pricewatch.env` on the VM with `0600` permissions.
- Terraform state stores rendered cloud-init, so local `.tfstate` files are treated as secret and gitignored.
- If the repo becomes private, switch `repo_url` to a deploy-key or token flow before destroying GitHub Actions.
## Shape fallback

Sydney often has no free `VM.Standard.A1.Flex` capacity. The Terraform module
also supports `VM.Standard.E2.1.Micro` by setting:

```hcl
instance_shape = "VM.Standard.E2.1.Micro"
enable_shape_config = false
ocpus = 1
memory_gb = 1
```

The cloud-init template creates a 2GB swapfile so dependency installation can
complete on the micro VM.

Big W is skipped when `PROXY_URL` is blank. Add the Webshare proxy URL to
`terraform.tfvars` and re-apply before expecting Big W coverage from OCI.