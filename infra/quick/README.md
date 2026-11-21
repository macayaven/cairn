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
reserved public IPv4 address, an instance profile, an S3 bucket with versioning and a Secrets
Manager secret.
It puts the subnet in the first Availability Zone that offers `t3.small`, and later applies
leave it there.
What is deleted or overwritten in the bucket stays restorable for at least 7 days: it becomes an
earlier version of the object. From the midnight UTC after those days a lifecycle rule makes that
version eligible for removal, and S3 removes it some time after that, with no deadline it states.
The VM's role can delete an object but not a version.
While it runs, budget for [EC2 instance time](https://aws.amazon.com/ec2/pricing/on-demand/),
[the disk](https://aws.amazon.com/ebs/pricing/), [the IPv4 address](https://aws.amazon.com/vpc/pricing/),
[S3 storage (replica files Litestream deleted stay at least 7 days) and requests](https://aws.amazon.com/s3/pricing/),
[the secret and its API calls](https://aws.amazon.com/secrets-manager/pricing/),
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
Compose reads the file, and takes a value as written only between single quotes. A single quote
inside is written `\'`, so a value must not end in an odd number of backslashes: the last takes the
closing quote, and the value runs on to the next single quote in the file, over the lines between,
which then set nothing. With no such quote, Compose stops with `unterminated quoted value`, the unit
fails and the boot passes that line on. With one, Compose reads on from what follows it: when it
cannot read that, it stops as well; when it can, it starts cairn with one setting where two were
meant. The check below then reads the same value when a comment or nothing follows that quote on its
line, and is quiet; other text there makes the line one it counts. Outside single quotes Compose
changes four kinds of value: it puts a variable's value in place of a `$` and the name after it, or
nothing when no variable has that name; where no quotes hold the value, it drops the blanks at
either end, and a `#` after a space with the rest of the line, unless the `#` is the value's first
character; and it removes double quotes around a value, reading `\n` and its like between them as
the characters they stand for. Compose reports one of these, the `$` before a name no variable has,
and the boot passes on what Compose says, as a line in the unit's journal that starts with
`cairn-quick: compose:`. The other changes come without a word from Compose, so once the stack is up
the boot compares the settings in the secret with what cairn's container was given. It reads the
lines written `NAME=value`, with nothing before the name and nothing between it and the equals sign,
and takes the value as written: what stands between single quotes that open right after the equals
sign, or else all that stands between the equals sign and the end of the line, blanks and double
quotes included. It names each of those that differs, the name and nothing of its value, on a line
that starts with `cairn-quick: settings check:`. A value in double quotes that runs over more than
one line is not read as one value: its first line is named as differing, and a later line of it that
looks like a setting can be named too. Compose reads more forms than that, among them a colon for
the equals sign, `export` or blanks before the name, a hyphen in the name, a name alone on its line
(its value then comes from the environment Compose runs in), blanks before the equals sign or
between it and a single quote, text after a closing single quote, and two backslashes together
between single quotes, so every other line that is not blank or a comment is counted on one line,
`cairn-quick: settings check: N line(s) of the settings not compared`. That line goes on with names
cairn was given: each one that a counted line begins with, after `export` or blanks if it has them,
a name alone on its line included; and any other setting cairn was given, with another value than
the image's own, that no line the check read holds, which adds one to N. It ends `; M unnamed` when
M of the counted lines begin with no name cairn was given: a setting cairn did not get, or text of
another line. Write the named ones as `NAME=value`. When two lines that the check reads set one
name, Compose takes the later, and the check compares that one. A start with no `settings check`
line shows that every setting cairn was given arrived as written, with two things aside: a carriage
return that ends its line, which Compose drops and the check does not count, and a value that a
backslash carried past its closing quote, which arrives as Compose and the check both read it. With
a `not compared` line and no other, that holds for every setting but those the line names and those
on the lines it counts; `cairn-quick: settings check: did not run` means that nothing was compared.
Follow the [provider sign-in steps](#provider-sign-in) to configure Google sign-in.

```sh
aws secretsmanager put-secret-value --region "$(tofu output -raw region)" \
  --secret-id "$(tofu output -raw settings_secret)" --secret-string file://./cairn.env
```

For secret-only changes, reload the settings in the Session Manager shell.

```sh
sudo systemctl restart cairn-quick.service
```

### AWS cold copy

The VM takes no `scp`, so a cold copy leaves it through the bucket, under `cold/`, a prefix of
its own. Litestream reads and writes only under `cairn/`. The VM's role can add an object under
`cold/` and can neither read nor delete one there. The bucket's lifecycle rule removes only what
was deleted or overwritten, so a cold copy stays until you delete it.

In the Session Manager session, open a root shell (`sudo -i`), stop the stack, pack the data
volume and send it:

```sh
cd /opt/cairn/deploy && . /opt/cairn/quick.env
docker compose stop
name="cairn-cold-$(date -u +%Y%m%dT%H%M%SZ).tgz"
tar -czf "/root/$name" -C "$(docker volume inspect cairn_cairn-data -f '{{.Mountpoint}}')" --exclude=.cairn.db-litestream .
/snap/bin/aws s3 cp "/root/$name" "${LITESTREAM_REPLICA_URL%/cairn}/cold/$name" --region "$AWS_REGION"
ls -l "/root/$name" && sha256sum "/root/$name"
```

On your machine, in the AWS stack directory, fetch the copy and compare its size and digest
with the two lines the VM printed, substituting the name it printed:

```sh
aws s3 cp "s3://$(tofu output -raw backup_bucket)/cold/cairn-cold-20261120T101500Z.tgz" . --region "$(tofu output -raw region)"
ls -l cairn-cold-20261120T101500Z.tgz && shasum -a 256 cairn-cold-20261120T101500Z.tgz
```

When both are the same, start the stack again in the root shell and remove the VM's own copy:

```sh
systemctl restart cairn-quick.service
rm /root/cairn-cold-20261120T101500Z.tgz
```

The archive holds the data volume as it was while nothing ran: the database with its
write-ahead log, and any file you wrote beside it. A cold copy you delete from your machine
(`aws s3 rm`) stays restorable for at least 7 days, like anything deleted in the bucket.

### AWS teardown

Save the bucket and region before destroying the stack. The destroy operation refuses to delete
a bucket with objects, or with earlier versions of them. Secrets Manager schedules the settings
secret for deletion. A bucket that stays keeps all it holds: the destroy removed the rule that
expired earlier versions.

```sh
bucket="$(tofu output -raw backup_bucket)"
region="$(tofu output -raw region)"
tofu destroy
```

To remove a retained bucket too, record that the backups may go and retry the destroy. OpenTofu
then empties the bucket itself, every version in it. This deletes the backups and the cold copies.

```sh
tofu apply -target=aws_s3_bucket.replica -var delete_backups=true
tofu destroy -var delete_backups=true
```

`aws s3 ls "s3://$bucket" --region "$region"` then answers that the bucket does not exist.

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
Cloud Storage bucket with a 7-day soft delete policy, and a Secret Manager secret.
What is deleted or overwritten in the bucket stays restorable for 7 days: that is the soft
delete policy, which nobody can cut short for an object already deleted. Rather than
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
Compose reads the file, and takes a value as written only between single quotes. A single quote
inside is written `\'`, so a value must not end in an odd number of backslashes: the last takes the
closing quote, and the value runs on to the next single quote in the file, over the lines between,
which then set nothing. With no such quote, Compose stops with `unterminated quoted value`, the unit
fails and the boot passes that line on. With one, Compose reads on from what follows it: when it
cannot read that, it stops as well; when it can, it starts cairn with one setting where two were
meant. The check below then reads the same value when a comment or nothing follows that quote on its
line, and is quiet; other text there makes the line one it counts. Outside single quotes Compose
changes four kinds of value: it puts a variable's value in place of a `$` and the name after it, or
nothing when no variable has that name; where no quotes hold the value, it drops the blanks at
either end, and a `#` after a space with the rest of the line, unless the `#` is the value's first
character; and it removes double quotes around a value, reading `\n` and its like between them as
the characters they stand for. Compose reports one of these, the `$` before a name no variable has,
and the boot passes on what Compose says, as a line in the unit's journal that starts with
`cairn-quick: compose:`. The other changes come without a word from Compose, so once the stack is up
the boot compares the settings in the secret with what cairn's container was given. It reads the
lines written `NAME=value`, with nothing before the name and nothing between it and the equals sign,
and takes the value as written: what stands between single quotes that open right after the equals
sign, or else all that stands between the equals sign and the end of the line, blanks and double
quotes included. It names each of those that differs, the name and nothing of its value, on a line
that starts with `cairn-quick: settings check:`. A value in double quotes that runs over more than
one line is not read as one value: its first line is named as differing, and a later line of it that
looks like a setting can be named too. Compose reads more forms than that, among them a colon for
the equals sign, `export` or blanks before the name, a hyphen in the name, a name alone on its line
(its value then comes from the environment Compose runs in), blanks before the equals sign or
between it and a single quote, text after a closing single quote, and two backslashes together
between single quotes, so every other line that is not blank or a comment is counted on one line,
`cairn-quick: settings check: N line(s) of the settings not compared`. That line goes on with names
cairn was given: each one that a counted line begins with, after `export` or blanks if it has them,
a name alone on its line included; and any other setting cairn was given, with another value than
the image's own, that no line the check read holds, which adds one to N. It ends `; M unnamed` when
M of the counted lines begin with no name cairn was given: a setting cairn did not get, or text of
another line. Write the named ones as `NAME=value`. When two lines that the check reads set one
name, Compose takes the later, and the check compares that one. A start with no `settings check`
line shows that every setting cairn was given arrived as written, with two things aside: a carriage
return that ends its line, which Compose drops and the check does not count, and a value that a
backslash carried past its closing quote, which arrives as Compose and the check both read it. With
a `not compared` line and no other, that holds for every setting but those the line names and those
on the lines it counts; `cairn-quick: settings check: did not run` means that nothing was compared.
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

When the settings secret cannot be read or Compose cannot start the stack, `cairn-quick.service`
fails, and its journal has the tool's own error on lines that start with
`cairn-quick: settings read:` or `cairn-quick: compose:`. The boot filters those lines: a syntax
error in the settings file is given by its line number, Compose's warning of a `$` before a name no
variable has is passed on without that name, and a line that repeats enough of a setting is left
out. A line is left out when it holds a whole line of the settings; or what follows a line's first
`=` or `:`, without the blanks and quotes at its ends, when that is 6 to 11 characters long; or 12
characters in a row of a settings line, its name included, the blanks and quotes at the line's ends
aside. So an error that names a setting whose name has 12 characters or more is not shown, and the
boot's own closing line (`Docker Compose up failed` or `could not read the cairn settings secret`)
always is. Less than that reaches the journal when a tool's error line holds it: a name of 11
characters or fewer, a whole value of 5 characters or fewer, a part of a value of 6 to 11, up to 11
characters in a row of a longer one, and a value that the tool writes otherwise than the file does.

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
`cairn` service's `/data` volume off the VM (on AWS, through the bucket: [AWS cold copy](#aws-cold-copy))
and note the running image, then set `image` in
`terraform.tfvars` to the next release or check out its tag in this clone and run `tofu apply`
(run `tofu init` first if the lock changed).
OpenTofu replaces the VM when the rendered boot configuration changes, keeping the address and
bucket, and the new cairn may migrate the restored database.
Replacement also removes Caddy's local `caddy-data` certificate volume. Repeated replacements
can hit [Let's Encrypt's limit on certificates for the same hostnames](https://letsencrypt.org/docs/rate-limits/#new-certificates-per-exact-set-of-identifiers).
The replica holds the database and nothing else. A plans file, a logo or operator notes that you
wrote onto the data volume (for `--plans`, `--brand-logo` or `--operator-notes`) are not on the
new VM, and `serve` does not start while one of those options names a file it cannot read, so
`/healthz` stays down after a replacement until each file is written again. While cairn is down,
a container of the `data-init` service mounts the volume as cairn's own user, as in
[Restore by hand](../../deploy/README.md#restore-by-hand). The same holds on the first boot of
a VM whose `serve_extra_args` name such a file.
Check the release notes before going back, because an older image can use an additive schema
update but needs the pre-upgrade backup after an incompatible one.

## What was tried

- The bootstrap tests used stand-ins for Docker, the cloud secret stores and the metadata firewall.
  The template tests compared the contents delivered by cloud-init with the files in the checkout.
- Both configurations passed the OpenTofu formatting check and initialized from local provider
  packages. `tofu validate` and `tofu test` with mocked providers pass for both, and the
  gate's job `quick` runs them on every change that touches these files.
- The Google Cloud path was applied once to a real project and destroyed (2026-11-20, with
  the image of `main`, since this version's did not exist yet): the apply, the first boot, a
  certificate, sign-in through IAP, the first key, a note, the replica in the bucket, the VM
  replaced and the database restored from the bucket, a restart, and the teardown as written.
  Not covered by it: a brand-new project whose APIs are off, a settings secret with a version,
  and this version's own image. The AWS path has not been applied to a real account: its VM
  boot, cloud access, certificate and bucket-backed restore still need a live check.
- The AWS files were run on a laptop (2026-11-20): the cloud-init rendered with made-up values,
  and its boot script run against Docker Compose 5.5.0 with a stand-in for the AWS CLI, an image
  built from this checkout and a directory in place of the bucket. A denied and an empty
  settings secret, a malformed settings file, a start, the first key, a note, the database
  restored after its volume was removed, and the cold copy commands went as this guide says.
  None of that touched AWS: S3, the VM's role, the metadata firewall, cloud-init and systemd
  were not part of it.
  The boot script has changed since that apply on Google Cloud (it now passes on its tools'
  errors, runs Compose without progress lines, and compares the settings after a start, counting the
  lines it does not read): the changed script has run only on the laptop.
