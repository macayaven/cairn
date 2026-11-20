# Quick deploy on AWS or Google Cloud

Run cairn on one Ubuntu 24.04 LTS VM with the released image. Cloud-init copies this checkout's
Compose files and configuration to the VM. Caddy gets the HTTPS certificate, and Litestream uses
the VM's identity to replicate SQLite to a private, provider-encrypted bucket.

## It is not

- It runs one small VM.
- There is no updater that follows a tag or deploys from CI.
- There are no alerts or data disk snapshots.
- [The fuller Google Cloud setup](../gcp/README.md) includes CI deployment, a separate data disk
  and snapshots.

The bucket is what survives a VM replacement. The shipped [Litestream configuration](../../deploy/litestream.yml)
syncs changes every 10 seconds and expires old replica files. With healthy replication keeping up,
replacing the VM can lose the last 10 seconds of writes. If replication has stalled, recovery goes
back to the last successful upload. On a fresh disk, Compose restores the database from the bucket
before starting cairn.

## AWS

Use a commercial AWS region, OpenTofu 1.13 or newer, the AWS CLI with credentials for provisioning
the stack, and the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
and permissions for an interactive session. Have a domain whose DNS you can edit.

The stack creates a VPC and subnet, a `t3.small` with a 20 GiB encrypted `gp3` boot disk, a
reserved public IPv4 address, an instance profile, an S3 bucket and a Secrets Manager secret.
While it runs, budget for [EC2 instance time](https://aws.amazon.com/ec2/pricing/on-demand/),
[the disk](https://aws.amazon.com/ebs/pricing/), [the IPv4 address](https://aws.amazon.com/vpc/pricing/),
[S3 storage and requests](https://aws.amazon.com/s3/pricing/), [the secret and its API calls](https://aws.amazon.com/secrets-manager/pricing/),
and billable transfer.

1. Clone at the release tag.

   ```sh
   git clone --branch v0.1.0 https://github.com/macayaven/cairn.git
   cd cairn/infra/quick/aws
   ```

2. Copy the example and set its three lines, `region`, `domain` and `acme_email`.

   ```sh
   cp terraform.tfvars.example terraform.tfvars
   ```

3. Initialize the stack.

   ```sh
   tofu init
   ```

4. Review and apply the plan.

   ```sh
   tofu apply
   ```

5. Set your domain's A record to the printed address.

   ```sh
   tofu output -raw public_ipv4
   ```

   EC2 starts with a temporary public IPv4 address. Attaching the Elastic IP releases that
   address and can interrupt connections during first boot. Use the printed Elastic IP for DNS.

6. Wait for DNS and for this check to answer over HTTPS. If it stays down after DNS resolves,
   use the Session Manager command in step 7 to check [first-boot recovery](#first-boot-recovery).

   ```sh
   curl -fsS "$(tofu output -raw url)/healthz"
   ```

7. Print and run the Session Manager command locally. Print the key command locally and run it
   inside that session.

   ```sh
   tofu output -raw connect_command
   tofu output -raw first_key_command
   ```

The key command writes a root-owned file with mode 0600 and prints its path. Read it in a private
session without transcript logging. Use the API key to sign in at your HTTPS URL and keep the key
in your credential store. Then follow [first key cleanup](#first-key-cleanup).

To move to the current Ubuntu AMI, follow the backup steps in [Upgrade](#upgrade), then run
`tofu apply -replace=aws_instance.app` from the AWS stack directory.

### AWS settings

The settings secret starts without a version, so you can sign in with API keys from the start.
Prepare a local `cairn.env` for the cairn container's environment. From the AWS stack directory,
upload it without putting its value in your command line or OpenTofu state.
Follow the [provider sign-in steps](#provider-sign-in) to configure Google sign-in.

```sh
aws secretsmanager put-secret-value --region "$(tofu output -raw region)" \
  --secret-id "$(tofu output -raw settings_secret)" --secret-string file://./cairn.env
```

For secret-only changes, reload the settings in the Session Manager shell.

```sh
sudo systemctl restart cairn-quick.service
```

### AWS teardown

Save the bucket and region before destroying the stack. The destroy operation refuses to delete
a bucket with objects. Secrets Manager schedules the settings secret for deletion.

```sh
bucket="$(tofu output -raw backup_bucket)"
region="$(tofu output -raw region)"
tofu destroy
```

To remove a retained bucket too, empty it and retry the destroy. This deletes the backups.

```sh
aws s3 rm "s3://$bucket/" --region "$region" --recursive
tofu destroy
```

## Google Cloud

Use a billing-enabled project, OpenTofu 1.13 or newer, and the Google Cloud CLI. Sign in and set
[Application Default Credentials](https://docs.cloud.google.com/docs/authentication/provide-credentials-adc)
(`gcloud auth application-default login`) with permission to enable APIs and provision the stack;
the Google provider sets `billing_project` and `user_project_override = true` from `project_id`, so
you do not need a separate quota-project file. On a brand-new project where Service Usage and
Resource Manager are not active yet, enable both once before your first apply:

```sh
gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com \
  --project=PROJECT_ID
```

The account you use for SSH needs
[IAP tunnel access and OS Admin Login](https://docs.cloud.google.com/compute/docs/oslogin/set-up-oslogin#assign-oslogin-roles),
including permission to use the attached service account. An account from outside the project's
organisation also needs `roles/compute.osLoginExternalUser`, granted on the organisation; and where
the roles are granted on the instance alone, the CLI also needs to read the project
(`compute.projects.get`). Have a domain whose DNS you can edit.
The stack enables the Cloud OS Login API (`oslogin.googleapis.com`) for SSH.

The stack creates a VPC and subnet (with Private Google Access), an `e2-small` with Shielded VM
features and a 20 GiB `pd-balanced` boot disk, a reserved public IPv4 address, a service account, a
Cloud Storage bucket with a 7-day soft delete policy, and a Secret Manager secret. Rather than
creating custom project IAM roles (which suffer a 37-day GCP name lockout after teardown), it
binds predefined roles strictly to those two resources:

- **`roles/storage.objectUser` on the backup bucket:** grants object and multipart-upload
  operations needed by Litestream, plus object metadata edits, moves, restores and folder
  operations inside that bucket — never bucket deletion or IAM/policy changes.
- **`roles/secretmanager.secretAccessor` and `roles/secretmanager.viewer` on the settings
  secret:** lets the VM list and read secret versions, plus view that single secret's metadata, IAM
  policy and tags — never modify secrets/versions or read any other secret.

The default zone is `us-central1-a`. While it runs, budget for
[Compute Engine instance time](https://cloud.google.com/compute/vm-instance-pricing),
[the disk](https://cloud.google.com/compute/disks-image-pricing),
[the IPv4 address and billable transfer](https://cloud.google.com/vpc/network-pricing),
[replica storage, soft-deleted 10-second L0 files and requests](https://cloud.google.com/storage/pricing),
and [active secret versions and access operations](https://cloud.google.com/secret-manager/pricing).

1. Clone at the release tag.

   ```sh
   git clone --branch v0.1.0 https://github.com/macayaven/cairn.git
   cd cairn/infra/quick/gcp
   ```

2. Copy the example and set its three lines, `project_id`, `domain` and `acme_email`.

   ```sh
   cp terraform.tfvars.example terraform.tfvars
   ```

3. Initialize the stack.

   ```sh
   tofu init
   ```

4. Review and apply the plan. On a fresh project, Google Cloud API activation and service-account
   propagation can take up to a minute; if the first `tofu apply` stops with a propagation `403` or
   unknown service account error, wait a minute and run `tofu apply` again. Later applies ignore new
   Ubuntu 24.04 image releases so a routine run never replaces your VM; to move to a newer Ubuntu
   image on purpose (after saving a backup), run `tofu apply -replace=google_compute_instance.app`.
   Replacing the VM clears Caddy's certificate cache on the boot disk; avoid repeated recreations so
   you do not hit Let's Encrypt's limit of 5 duplicate certificates per week.

   ```sh
   tofu apply
   ```

5. Set your domain's A record to the printed address.

   ```sh
   tofu output -raw public_ipv4
   ```

6. Wait for DNS and for this check to answer over HTTPS. If it stays down after DNS resolves,
   use the IAP SSH command in step 7 to check [first-boot recovery](#first-boot-recovery).

   ```sh
   curl -fsS "$(tofu output -raw url)/healthz"
   ```

7. Print and run the IAP SSH command locally. Print the key command locally and run it inside
   the SSH session.

   ```sh
   tofu output -raw connect_command
   tofu output -raw first_key_command
   ```

The key command writes a root-owned file with mode 0600 and prints its path. Read it in a private
session, save the API key in your credential store, remove the temporary file on the VM
(`sudo rm -f /opt/cairn/cairn-first-key.*`), and sign in at your HTTPS URL.

### Google Cloud settings

The settings secret starts without a version, so you can sign in with API keys from the start.
Prepare a local `cairn.env` for the cairn container's environment. From the Google Cloud stack
directory, upload it without putting its value in your command line or OpenTofu state.
Follow the [provider sign-in steps](#provider-sign-in) to configure Google sign-in.

```sh
gcloud secrets versions add "$(tofu output -raw settings_secret)" \
  --project="$(tofu output -raw project_id)" --data-file=./cairn.env
```

For secret-only changes, reload the settings in the IAP SSH shell.

```sh
sudo systemctl restart cairn-quick.service
```

### Google Cloud teardown

Save the bucket name before destroying the stack. The destroy operation refuses to delete a
bucket with live objects. The project APIs stay enabled. The bucket keeps a 7-day soft delete policy
against accidental or VM-initiated deletion (note that Litestream's 10-second L0 files also stay in
soft delete for 7 days and incur standard Cloud Storage charges). Emptying live objects with
`gcloud storage rm --all-versions` lets `tofu destroy` delete the bucket immediately even while
soft-deleted objects remain; the soft-deleted bucket and objects stay recoverable and billed until
their 7 days expire, while the bucket name becomes available right away.

```sh
bucket="$(tofu output -raw backup_bucket)"
tofu destroy
```

To remove a retained bucket too, empty its live objects and retry the destroy. This deletes the live backups.

```sh
gcloud storage rm --all-versions "gs://$bucket/**"
tofu destroy
```

## First key cleanup

After copying the first key to your credential store, delete the file from the VM with
`sudo rm -- /opt/cairn/cairn-first-key.abc123`, substituting the exact path the key command printed.

## First-boot recovery

On either VM, `cairn-quick-provision.service` installs the packages and retries failures every
20 seconds. When installation succeeds, it enables and starts `cairn-quick.service`, which
loads the settings and starts Compose. Check both units and their logs from the VM session.

```sh
sudo systemctl status cairn-quick-provision.service cairn-quick.service
sudo journalctl -b -u cairn-quick-provision.service -u cairn-quick.service -u docker.service --no-pager
```

After fixing a package problem, trigger an immediate retry.

```sh
sudo systemctl restart cairn-quick-provision.service
```

After the packages are installed, use `sudo systemctl restart cairn-quick.service` to retry
starting Compose.

## Provider sign-in

This example uses Google sign-in at `cairn.example.net` and admits accounts from the
`example.net` Google Workspace domain.

1. Complete the quickstart through the HTTPS check. Create a Google OAuth web client for the
   accounts you want to admit and register `https://cairn.example.net/auth/google/callback` as
   its authorized redirect URI. Substitute your own public hostname and Workspace domain.
2. Set these variables in your local `cairn.env` with the values from the client you created.
   Upload it using [AWS settings](#aws-settings) or [Google Cloud settings](#google-cloud-settings).

   ```dotenv
   CAIRN_GOOGLE_CLIENT_ID=your-client-id
   CAIRN_GOOGLE_CLIENT_SECRET=your-client-secret
   ```

3. Add `serve_extra_args` to `terraform.tfvars`, with one nonsecret argument per string.
   The arguments go into the VM's boot configuration. Compose sets `--public-url` from `domain`.

   ```hcl
   serve_extra_args = ["--sign-in", "google", "--allowed-email-domain", "example.net"]
   ```

   Run `tofu apply`. Changing these arguments replaces the VM and keeps the address, bucket
   and settings secret. Read [Upgrade](#upgrade) for the backup steps and certificate implications
   before applying.
4. Check `/healthz` again. In a signed-out browser, open `https://cairn.example.net/login` and
   choose **Continue with Google**. After signing in with an account from the allowed Workspace
   domain, you return to cairn signed in. New users get a workspace of their own.

## Upgrade

Save a [cold copy of the database](../../docs/guides/run.md#back-it-up) from the Compose
`cairn` service's `/data` volume off the VM and note the running image, then set `image` in
`terraform.tfvars` to the next release or check out its tag in this clone and run `tofu apply`
(run `tofu init` first if the lock changed).
OpenTofu replaces the VM when the rendered boot configuration changes, keeping the address and
bucket, and the new cairn may migrate the restored database.
Replacement also removes Caddy's local `caddy-data` certificate volume. Repeated replacements
can hit [Let's Encrypt's limit on certificates for the same hostnames](https://letsencrypt.org/docs/rate-limits/#new-certificates-per-exact-set-of-identifiers).
Check the release notes before going back, because an older image can use an additive schema
update but needs the pre-upgrade backup after an incompatible one.

## What was tried

- The bootstrap tests used stand-ins for Docker, the cloud secret stores and the metadata firewall.
  The template tests compared the contents delivered by cloud-init with the files in the checkout.
- Both configurations passed the OpenTofu formatting check and initialized from local provider
  packages. `tofu validate` and `tofu test` with mocked providers pass for both, and the
  gate's job `quick` runs them on every change that touches these files.
- Neither path has been applied to a real account. VM boot, cloud access, certificate issuance
  and bucket-backed restore still need a live check.
