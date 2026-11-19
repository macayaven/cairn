# cairn on Google Cloud

One VM runs the same image self-hosted users run. OpenTofu builds everything around it.

**Status: applied once to a real project, on 2026-11-14, and only what that run showed is verified there.** One `e2-small` VM in `europe-west1` booted, served the domain over TLS, deployed by digest, replicated to the bucket and reported through guest attributes; the replica alert was applied the day after. Everything added since (the later alerts, the container limits, the registry cleanup, the audit and key settings among them) has not been applied, and the end of this file lists what is still unverified. CI formats, validates and plans it with OpenTofu (`tofu test`, against a mock provider and without a cloud project) and rehearses the VM's files and units on a runner. [What was checked](#what-was-checked-and-what-was-not) is at the end. Expect to fix something on the next apply.

## What it builds

| Piece | Details |
|---|---|
| VM | `e2-small`, Container-Optimized OS (stable), Shielded VM, in its own network |
| Data disk | 20 GB `pd-balanced`, separate from the boot disk, protected from destroy, mounted at `/mnt/disks/data` |
| Address | one static IPv4; ports 80 and 443 open; SSH only from Identity-Aware Proxy, with OS Login |
| Registry | one Artifact Registry Docker repository; the VM follows the tag `cairn:production` in it; a cleanup policy deletes old images ([Rollback](#rollback)) |
| Secret | one Secret Manager secret that holds cairn's env file |
| Replica | one bucket (uniform access, public access prevented, soft delete: whatever is deleted stays restorable for 7 days) |
| Snapshots | a daily snapshot of the data disk, kept 14 days |
| Monitoring | an uptime check on `https://DOMAIN/healthz` and an email alert; a second email alert when Litestream logs an error; a third when the replica check stops saying the replica is current; a fourth when a compaction keeps not finishing; four more on the host itself: a disk above 80 percent, a container killed for memory, a unit restarting in a loop, and Caddy failing to obtain or renew a certificate ([Alerts](#alerts)) |
| Identities | a VM service account, and a deployer service account that one GitHub Actions workflow reaches through Workload Identity Federation. The deployer can push images and read the VM's deploy status, nothing else. No keys. |

On the VM, cloud-init writes fifteen systemd units on every boot (COS keeps `/etc` on tmpfs). Three of them run containers:

- **`cairn`**: the image recorded on the data disk, always by digest, with its environment from the secret.
- **`caddy`** (2.11.9): TLS for the domain, HSTS, a 4 MiB body cap, logs without query strings. Certificates live on the data disk.
- **`litestream`** (0.5.18): replicates `/data/cairn.db` to the bucket every 10 seconds. The container runs from boot and waits inside for the database cairn creates on its first start.

cairn and Caddy share one Docker network that cannot reach the VM's metadata server; Litestream is alone on a second one that can ([why and how](#containers-and-the-metadata-server)).

The others prepare the ground: `cairn-disk` mounts the data disk and drops the record of a deployment that a reboot interrupted, `cairn-network` creates the two Docker networks and the firewall rules between containers and the metadata server, `cairn-env` fetches the secret into `/run/cairn/cairn.env` (tmpfs, mode 0600), `cairn-restore` restores the database from the replica when the file is missing, `cairn-update` (a timer, every minute) follows the deploy tag in the registry, and `cairn-replica-check` (a timer, every five minutes) says in Litestream's log how old the newest copy in the replica is ([Alerts](#alerts)), and `cairn-compact` (a timer, every night at 03:00 UTC) finishes the compaction after a deletion when one waits ([After a workspace is deleted](#after-a-workspace-is-deleted)), and `cairn-host-check` (a timer, every five minutes) checks the disks, containers killed for memory, units restarting in a loop and Caddy's certificate errors ([Alerts](#alerts)). cairn does not start until the secret is read and the restore step has succeeded.

`deploy/Caddyfile` and `deploy/litestream.yml` are used unchanged, so the VM and a self-hosted compose stack run the same configuration. The host logic is one script, [`files/host.sh`](files/host.sh).

## Bootstrap (once, by a person)

You need `gcloud`, OpenTofu 1.13 or later, and Docker if you push the first image by hand.

**1. Sign in and pick a project.**

```bash
gcloud auth login
gcloud auth application-default login   # OpenTofu and the restore drill use these
gcloud config set project PROJECT_ID    # create it and link billing first if it is new
```

**2. Enable the APIs.**

```bash
gcloud services enable \
  compute.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  storage.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudresourcemanager.googleapis.com monitoring.googleapis.com logging.googleapis.com \
  iap.googleapis.com oslogin.googleapis.com
```

**3. Create the state bucket.** State is never stored in this repository.

```bash
gcloud storage buckets create gs://STATE_BUCKET --location=europe-west1 \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://STATE_BUCKET --versioning
```

**4. Configure.** Both files are ignored by git. `github_repository_id` is required: `gh api repos/OWNER/NAME --jq .id`.

```bash
cd infra/gcp
cp terraform.tfvars.example terraform.tfvars    # edit it
printf 'bucket = "STATE_BUCKET"\nprefix = "cairn"\n' > backend.hcl
tofu init -backend-config=backend.hcl
```

`tofu init` takes the google provider at the version in `.terraform.lock.hcl`, which is in git, so CI checks these files and you apply them with the same provider. To move to a newer one, run `tofu init -upgrade` and commit the change to that file.

**5. Reserve the address and point DNS at it.** Doing this first means Caddy's first certificate request succeeds.

```bash
tofu apply -target=google_compute_address.cairn
tofu output -raw ip_address
```

Create an `A` record for the domain with that address at your DNS provider. Do not add an `AAAA` record: the VM has no IPv6.

**6. Apply the rest.**

```bash
tofu apply
```

**7. Create the secret's first version.** OpenTofu made an empty secret; the value never passes through OpenTofu or its state. The file holds `KEY=value` lines for cairn (no quotes, no `export`, Unix line endings). If cairn needs no settings yet, a single comment line will do. cairn does not start until one version exists.

```bash
gcloud secrets versions add "$(tofu output -raw env_secret)" --data-file=cairn.env
```

**8. Deploy the first image** (next section). Then mint an API key as the top-level README describes, from a shell on the VM. Under OS Login, `docker` needs `sudo`.

```bash
gcloud compute ssh cairn --zone ZONE --tunnel-through-iap
```

Project owners can connect as they are. Anyone else needs `roles/iap.tunnelResourceAccessor` and `roles/compute.osAdminLogin`, plus `roles/iam.serviceAccountUser` on the VM's service account.

**A second factor for SSH** is on by default (`os_login_2fa`): OS Login asks everyone who connects for a second factor (a security key, an authenticator code or a phone prompt) at every connection, and their Google account must have 2-Step Verification on. A script or a scheduled job can therefore not connect by SSH. Whether to keep it on is the operator's decision. `os_login_2fa = false` turns it off. A change of it changes the instance's metadata, not the VM.

**Remove what every new project starts with.** The VM runs in its own network with its own service account, so it uses neither the `default` network (whose rules let SSH and RDP in from anywhere) nor the default Compute Engine service account (which holds `roles/editor` on the whole project). Remove both, once, by hand; OpenTofu does not own them:

```bash
gcloud compute firewall-rules list --filter=network=default --format='value(name)' \
  | xargs -r -n 1 gcloud compute firewall-rules delete --quiet
gcloud compute networks delete default --quiet
NUMBER=$(gcloud projects describe PROJECT_ID --format="value(projectNumber)")
gcloud projects remove-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" --role=roles/editor
```

Anything else in the project that uses the default network or the default service account stops working; in a project that holds only cairn there is nothing else.

**Customer-managed keys** and the settings a reviewer expects are in [Audit, keys and network](#audit-keys-and-network).

The alert fires until `https://DOMAIN/healthz` answers 200. That is expected during bootstrap. Later it fires while cairn is down or answers 503: it cannot read its database, the database holds a schema version this cairn does not know, or a write failed and could not be undone (cairn then stays out of service until it is restarted: `sudo systemctl restart cairn.service` on the VM, after `sudo journalctl -u cairn` says why). It also answers 503 when one write has held the database for more than a minute (an hour while a compaction runs) and it answers `{"status":"stuck"}`: reads may still work, no write can. Restart cairn the same way: the write under way is lost, nothing committed is.

## Deploy

A deploy is a push to the registry. The VM follows one tag, `cairn:production` (`tofu output -raw deploy_image`). Nothing connects to the VM, and nothing about the VM changes.

```bash
IMAGE=$(tofu output -raw deploy_image)     # REGION-docker.pkg.dev/PROJECT/cairn/cairn:production
gcloud auth configure-docker "$(tofu output -raw artifact_registry_host)"
docker buildx build --platform linux/amd64 \
  -t "${IMAGE%:*}:$(git rev-parse --short HEAD)" -t "$IMAGE" --push ../..
```

The first tag, the commit's short SHA, is moved by nothing here. It is what you roll back to by name. (The registry does not forbid moving a tag: whoever may push can.)

Every minute the updater on the VM pulls the tag and reads the digest behind it. On a new digest it pulls `cairn@sha256:...`, records the deployment on the data disk, restarts cairn on that digest and waits up to 60 seconds for `/healthz` to answer 200 twice in a row. Then it either promotes the image or goes back to the one before. `/healthz` answers 200 only once cairn has opened its database and can read it, with the schema this cairn knows; an image that cannot, whether it exits at start or answers 503, is not promoted. Watch it:

```bash
gcloud compute ssh cairn --zone ZONE --tunnel-through-iap \
  --command 'sudo journalctl -u cairn-update.service -n 20 --no-pager'
```

What the updater holds to:

- **cairn runs a digest, never a tag.** What passed the health check is what keeps running, whatever the tag does next. The updater builds the reference itself from the image name in its configuration and the digest; nothing it reads can name another image or registry.
- **A boot only ever starts an image that passed its health check, or one a person pinned.** The proven image is one file on the data disk (`deploy/image`). It changes after a passing check, and when someone runs `pin`. The candidate is a separate record (`deploy/pending`) that carries the id of the boot that started it; a record from an earlier boot is ignored.
- **An unfinished deployment is finished or undone, never left.** If the updater is killed while the VM keeps running, the next run finds the record, makes sure the candidate is what runs, and gives it the health check: promote or roll back. If the VM restarts in the middle, it comes back on the proven image, the boot step drops the record, and the updater deploys the candidate again from the start. A candidate cut short by a restart twice is marked failed.
- A pull that fails is retried every minute. An image that fails its health check is not retried until the tag moves to another digest.

### Deploy from GitHub Actions

The `deploy` job in `.github/workflows/ci-cd.yml` runs on a push to `main`, after the tests, the infrastructure checks, both image smoke tests, `publish` and `verify` have passed, and, where the `production` environment has required reviewers, only once one of them approves it. Where it has none (a private repository on a free plan cannot have them: step 4 of the bootstrap), a green gate on `main` deploys with no person approving, and that is how the pre-release ran on 2026-11-16. `publish` itself waits for the tests, the infrastructure checks, the image check and the stack and host rehearsals, so nothing reaches GHCR past a failed one. `deploy` deploys exactly one thing: the digest of the multi-arch index `publish` pushed to GHCR. No tag is resolved anywhere along the way.

1. **Smoke test of the published bytes, without credentials.** `publish` rebuilds from the build cache, so what it pushes is not byte for byte what the `image` job tested. A separate job, `verify`, pulls the published digest for `linux/amd64`, the VM's platform, and runs `scripts/smoke_container.py` on it again. On an ARM64 release runner, QEMU runs that AMD64 image with a 300-second health budget; on an x64 runner it runs natively with 90 seconds. It has no `id-token` permission and no environment secrets, because it runs the image and test code: a compromised step there cannot ask for a deploy token. `deploy` runs only after it passes, and runs no image or test code itself, only pinned actions, two short shell steps written in the workflow (they check the settings and work out the registry's host) and `scripts/deploy_production.sh`.
2. **A token for one hour.** `google-github-actions/auth` exchanges the job's GitHub token for an access token of the deployer service account. No key exists. The identity provider accepts the token only from a push to `main`, from `ci-cd.yml`, in a job that runs in the `production` environment ([who can become the deployer](#what-the-deployer-can-do)). A reviewer approves that job only when required reviewers are configured.
3. **Copy, by digest.** `scripts/deploy_production.sh copy` copies `ghcr.io/OWNER/REPO@sha256:...` into Artifact Registry as `cairn:sha-COMMIT`, with `skopeo copy --all --preserve-digests`, then reads the copy back and compares digests. `--all` keeps the whole index: both platforms and the build attestations. `--preserve-digests` makes skopeo fail instead of writing anything whose digest differs.
4. **Move the tag, if this run is still current.** `check-head` asks the GitHub API for the head of `main` and refuses unless it is this run's commit, so a re-run of an old run, or a run overtaken by a newer push, deploys nothing. Then `promote` refuses a digest Artifact Registry does not hold, points `cairn:production` at the digest, reads the tag back, and logs the digest it replaced: that is the one to roll back to.
5. **Wait for the VM.** Before the move, the job reads the number of the VM's latest report. Then `wait` reads the status every 15 seconds for up to 15 minutes, and only a report with a higher number counts: the VM numbers its reports, so no clock is compared. It succeeds when such a report says the VM runs that digest, has promoted it, and `/healthz` answers. It fails at once when such a report says the VM rolled back from that digest, and says whether the earlier image is healthy or nothing is (the schema case below). It also fails at once on a report that says the VM is on hold; but a hold stops the timer that sends reports, so while a hold lasts in the same boot no new report comes and the job runs into its timeout instead. On timeout it fails and prints the last report it read.

Why copy instead of a second push: a push from `publish` to both registries would need Google credentials in a job that runs before review, and building again for Artifact Registry would produce a different digest from the one that was tested. A copy by digest moves the exact index, and checking the digest on both sides proves it.

**How the VM reports.** At the end of every updater run, and when a deployment starts, `host.sh` writes one guest attribute, `cairn/status`, in one request:

```
seq=42 updated=1794350000 running=sha256:... healthy=yes proven=sha256:... pending=none failed=none hold=no
```

`seq` grows by one with every report and is kept on the data disk (`deploy/status-seq`); a new blank data disk starts it again at 1, and a deploy waiting across that times out rather than deciding on an old report. `updated` is the VM's clock, for people. `running` is what the cairn container runs, `proven` the image that last passed its health check, `pending` a deployment in its health check, `failed` the digest the updater refuses until the tag moves, `hold` whether a person holds deployments (`host.sh hold`). Guest attributes are on in the instance metadata (`enable-guest-attributes`). Only processes that reach the metadata server can write them: the host and Litestream, not cairn or Caddy (if the metadata block works; [below](#containers-and-the-metadata-server)).

**The report is a hint, not proof.** Anything on the VM that reaches the metadata server can write `cairn/status`, Litestream included, so a compromised Litestream container or root on the host could report a digest as running and healthy when it is not. When the repository variable `CAIRN_PUBLIC_URL` is set (`https://DOMAIN`, no path), the job checks from outside as well: after the report, `GET https://DOMAIN/healthz` must answer 200 over TLS with a valid certificate, within 10 tries 6 seconds apart; redirects and plain HTTP do not count. That proves the public address serves a healthy cairn, not which digest it is: `/healthz` does not say. Read it yourself with either of:

```bash
gcloud compute instances get-guest-attributes cairn --zone ZONE --query-path=cairn/status
GCP_PROJECT_ID=PROJECT_ID GCP_ZONE=ZONE GCP_INSTANCE=cairn bash scripts/deploy_production.sh status
```

**Settings.** Two repository variables turn the job on; without them it is skipped, not failed. The four values that name the project or an account are secrets of the `production` environment, not variables: GitHub prints variable values in the job log and masks secrets. None of them is a credential, but the project id, the account email and the registry path (which contains the project id) are kept out of logs all the same: the deploy script prints tags, digests, commit SHAs, the public address and the VM's status lines, none of which holds those three (an error from `curl` can reach the log too; it names a status code or a host), and drops skopeo's own messages, which name the registry path. A skopeo failure shows as a fixed message with skopeo's exit status, for example `copy to Artifact Registry failed (skopeo exit 1)`; to see skopeo's own message, run the same `skopeo copy` or `skopeo inspect` by hand with your credentials.

| Name | Kind | Value |
|---|---|---|
| `GCP_INSTANCE` | repository variable | `tofu output -raw instance_name` |
| `GCP_ZONE` | repository variable | `tofu output -raw instance_zone` |
| `CAIRN_PUBLIC_URL` | repository variable, optional | `tofu output -raw url`; turns on the outside check of `/healthz` |
| `GCP_PROJECT_ID` | `production` environment secret | `tofu output -raw project_id` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `production` environment secret | `tofu output -raw workload_identity_provider` |
| `GCP_DEPLOYER_SERVICE_ACCOUNT` | `production` environment secret | `tofu output -raw deployer_service_account` |
| `GCP_ARTIFACT_REGISTRY_REPOSITORY` | `production` environment secret | `tofu output -raw artifact_registry_repository` |

The job's other safeguards: every action pinned to a commit, `id-token: write` on two jobs only: `deploy`, which uses it to sign in to Google Cloud and runs nothing but pinned actions, two shell steps written in the workflow and the deploy script, and `attest`, on a tag, which uses it to sign the release's build provenance and runs no code from the repository; and a concurrency group so that two deploys never run at once. A running deploy is never cancelled; a deploy still waiting for its turn is replaced by a newer one, so the newest push wins.

### First deploy (once, in order)

Each step says how to check it before going on. Run the `tofu` and `gh` commands from `infra/gcp` in a checkout of the repository.

1. **Project and billing.**

   ```bash
   gcloud projects create PROJECT_ID
   gcloud billing projects link PROJECT_ID --billing-account=BILLING_ACCOUNT_ID
   gcloud billing projects describe PROJECT_ID --format='value(billingEnabled)'   # True
   ```

2. **Everything in [Bootstrap](#bootstrap-once-by-a-person) steps 1 to 7**: APIs, state bucket, `terraform.tfvars` (with `github_repository` and `github_repository_id`), address and DNS, `tofu apply`, the secret's first version. Check: `tofu plan` reports no changes, and `dig +short DOMAIN` prints `tofu output -raw ip_address`.
3. **The VM reports before anything is deployed.** Within two minutes of boot:

   ```bash
   gcloud compute instances get-guest-attributes "$(tofu output -raw instance_name)" \
     --zone "$(tofu output -raw instance_zone)" --query-path=cairn/status
   ```

   prints a line with `running=none healthy=no proven=none`. If it says the attribute does not exist, the VM booted an older `host.sh` or without `enable-guest-attributes`: `tofu apply`, then restart the VM ([Changing the configuration](#changing-the-configuration)).
4. **The `production` environment with a required reviewer**, limited to `main`. On a public repository this works on every GitHub plan. On a private one it does not on most: GitHub Free has no environment secrets there, so the job stops at "Check the settings", and required reviewers on a private repository need GitHub Enterprise, without which the job runs unreviewed. Make the repository public before the first deploy, or check your plan first.

   ```bash
   gh api -X PUT repos/OWNER/REPO/environments/production --input - <<EOF
   {"reviewers": [{"type": "User", "id": $(gh api users/REVIEWER_LOGIN --jq .id)}],
    "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
   EOF
   gh api -X POST repos/OWNER/REPO/environments/production/deployment-branch-policies \
     -f name=main -f type=branch
   gh api repos/OWNER/REPO/environments/production --jq '[.protection_rules[].type]'
   # ["required_reviewers","branch_policy"]
   ```

5. **Secrets and variables.** Piped, so no value lands in your shell history or on screen:

   ```bash
   tofu output -raw project_id                   | gh secret set GCP_PROJECT_ID --env production
   tofu output -raw workload_identity_provider   | gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER --env production
   tofu output -raw deployer_service_account     | gh secret set GCP_DEPLOYER_SERVICE_ACCOUNT --env production
   tofu output -raw artifact_registry_repository | gh secret set GCP_ARTIFACT_REGISTRY_REPOSITORY --env production
   gh variable set GCP_INSTANCE --body "$(tofu output -raw instance_name)"
   gh variable set GCP_ZONE --body "$(tofu output -raw instance_zone)"
   gh variable set CAIRN_PUBLIC_URL --body "$(tofu output -raw url)"
   gh secret list --env production   # four names
   gh variable list                  # three names
   ```

6. **Protect `main`** ([below](#what-the-deployer-can-do)): whoever can change `ci-cd.yml` on `main` decides what the deploy job does, and, when configured, the reviewers of `production` approve each run of it.
7. **Deploy.** Push to `main` a change that touches something the image is built from, or re-run the newest `main` run if its `publish` job ran, and approve the `deploy` job in the Actions tab where the environment has required reviewers; without them it starts by itself. A push that changes only paths on `NO_IMAGE_WORK` in `scripts/ci_changes.py` builds and deploys nothing once the newest run on `main` that published an image has ended green. Check: the job's log has `deploy: the VM runs sha256:... and /healthz answers` and, with `CAIRN_PUBLIC_URL` set, ends with `deploy: https://DOMAIN/healthz answers 200 over TLS`; `curl -fsS https://DOMAIN/healthz` answers, and the uptime alert clears within a few minutes. Then mint an API key ([Bootstrap](#bootstrap-once-by-a-person), step 8).

## Rollback

**Automatic.** If the new image is not healthy after 60 seconds, the updater restarts the proven image, logs an error at journal priority `err` and leaves `cairn-update.service` failed. Whether that line reaches Cloud Logging is not confirmed yet ([Logs](#logs)); `journalctl -u cairn-update.service` on the VM always has it. The tag still names the failed digest, and the updater skips that digest for as long as it does. Move the tag, forward to a fix or back to the earlier image, so that the registry says what runs. To retry the same digest without moving the tag, remove `/mnt/disks/data/deploy/failed-image` on the VM.

**Manual.** Point the tag back at an earlier digest. The VM treats it as any other deploy, health check included:

```bash
IMAGE=$(tofu output -raw deploy_image)
gcloud artifacts docker images list "${IMAGE%:*}" --include-tags --sort-by=~CREATE_TIME --limit=5
gcloud artifacts docker tags add "${IMAGE%:*}@sha256:OLDER_DIGEST" "$IMAGE"
```

The registry keeps the newest 50 versions of the image (`registry_keep_versions`) and whatever the deploy tag names, and deletes every other image once it is 14 days old (`registry_delete_after_days`). A deploy pushes an index and its children, about five versions, so that is about the last ten deploys; an older digest is gone, and rolling back to that code means building and pushing it again. Whether the cleanup spares the children of an index it keeps has not been checked against Artifact Registry ([What was checked](#what-was-checked-and-what-was-not)).

`"${IMAGE%:*}:sha-COMMIT"` works as the source too: the deploy job copies every image it deploys under the full commit SHA. `tofu output -raw rollback_command` prints the last line with this project's names filled in; its placeholder is `DIGEST`, the whole digest (`sha256:` and the hex). The deploy job's log names the digest each deploy replaced (`cairn:production moved from sha256:... to sha256:...`; `from none` on the first deploy, and `already names` with no `moved` line when the tag was there already). To wait for the VM the way the job does, with your own `gcloud` credentials:

```bash
GCP_PROJECT_ID=PROJECT_ID GCP_ZONE=ZONE GCP_INSTANCE=cairn \
  bash scripts/deploy_production.sh wait sha256:OLDER_DIGEST
```

**Not by re-running an old deploy.** Before it moves the tag, the deploy job checks that its commit is still the head of `main`, and refuses otherwise (`this run is for commit ..., but main is now at ...`). A re-run of an old run therefore never puts an old image over newer commits; the command above is the rollback.

After a rollback, the next push to `main` that changes something the image is built from deploys the head of `main` again. A push that changes only paths on `NO_IMAGE_WORK` in `scripts/ci_changes.py` deploys nothing, and the VM keeps the image you rolled back to, when the run that published the image you rolled back from ended green; when that run failed after it published (its deploy refused, timed out or rolled back by the VM), the next push of any kind runs the whole gate and deploys the head of `main`. Hold pushes, or revert on `main` (which also makes the revert the head that deploys), until the fix is in.

Not offered: a manual `workflow_dispatch` that takes a digest. The identity provider refuses every event but `push` on purpose (`iam.tf`); opening it to manual runs would let anyone who can start a workflow get a deploy token without a commit on `main`.

**Data on rollback.** Moving the tag changes the code, never the data. Everything the newer image wrote stays in the database, and the older image keeps it, as long as it runs on the newer image's schema.

**Which upgrades a rollback undoes.** From schema 31 on, a database records the oldest schema version whose image runs on it. A newer image whose schema steps only add (tables, columns with defaults, indexes) leaves that number: the older image runs on the database it moved forward, so the updater's automatic rollback, or moving the tag back, works with nothing lost. A step that changes what an older image relies on raises the number to itself: the older image refuses the database and fails its health check, and the way back is the cold backup, below. The release's notes say which kind each update is, and the refusal names the version it needs and its own. Schema 31 (release 0.1.0) is of the second kind: no image before it runs on a database it updated.

**Across a schema change the older image cannot run on, a rollback is a restore.** cairn refuses to open a database whose schema version is newer than the code knows and whose recorded oldest version is too (`SchemaTooNew`, decision 4 in `docs/decisions.md`), so an older image fails its health check on such a database. If the updater's rollback is not healthy either, its line in `journalctl -u cairn-update` says to look for this one. It exits at start, before it listens, with one line and no traceback; `sudo journalctl -u cairn` on the VM shows it:

```text
SchemaTooNew: the database has schema version <found> and needs a cairn of schema version <floor> or later; this cairn knows up to <known>. Run the cairn that wrote it or a newer one, or restore the database from before the upgrade.
```

The database is left as it was. systemd starts cairn again every five seconds (`Restart=always`, `RestartSec=5` in `cairn.service`), and each start ends with the same line, until the image or the database is put right. Two cases:

- A new image migrated the database and then failed its own health check. The updater rolls back to the older proven image, which fails too: nothing healthy runs, and the deploy job (or `wait`) says so.
- A new image migrated the database, passed, and is now the proven image; later you move the tag back to the older one. The older image fails, and the updater goes back to the newer one, which is healthy. The newer image stays recorded as proven, so anything that restarts cairn (a reboot, a restore runbook) starts it again, and it migrates whatever database it finds.

Either way, in order of preference:

1. **Roll forward.** Fix the newer image and deploy it. It opens the migrated database, and nothing is lost.
2. **Restore to before the migration, with deployments on hold.** Every write made after the restore point is lost. Find the point first: the deploy job's "Point production" step shows when the tag moved, and `sudo journalctl -u cairn-update` on the VM shows when the newer image first started. Then, on the VM (`gcloud compute ssh cairn --zone ZONE --tunnel-through-iap`, then `sudo -i`):

   1. **Hold.** `bash /etc/cairn/host.sh hold` stops the updater's timer and any update in flight, and keeps the updater from changing anything, after reboots too, until you release it. It prints the state.
   2. **Stop the writer.** `systemctl stop cairn.service litestream.service`
   3. **Restore** to the point before the migration: move the database files aside and run `bash /etc/cairn/host.sh litestream restore -timestamp ... /data/cairn.db`, as in [Restore to a point in time](#restore-to-a-point-in-time).
   4. **Pin the older digest.** `bash /etc/cairn/host.sh pin sha256:OLDER_DIGEST` makes it the image cairn starts with, and drops the records of the newer image's deployment (pending, failed). Nothing is restarted yet.
   5. **Move the tag** to the same digest, from your machine: `tofu output -raw rollback_command`, with its `DIGEST` replaced by the whole older digest (`sha256:` and the hex).
   6. **Check.** `bash /etc/cairn/host.sh state` must show the tag and the proven image on the older digest, no pending deployment, and the hold.
   7. **Start on a new replica path.** Set a new `replica_path` in `terraform.tfvars`, `tofu apply`, and restart the VM (`gcloud compute instances stop`, then `start`). It boots the pinned image on the restored database; the hold is on the data disk, so the updater still changes nothing. Check that `https://DOMAIN/healthz` answers.
   8. **Release.** `bash /etc/cairn/host.sh release`. It refuses while the tag names anything but the pinned image, because the updater would deploy the tag within a minute; `release --force` overrides that.

   Hold pushes to `main` until the newer image is fixed: the next push that changes something the image is built from deploys forward again, and so does any push when the run that published the newer image did not end green; a new image that still carries the migration migrates the restored database again. A push that changes only paths on `NO_IMAGE_WORK` in `scripts/ci_changes.py` deploys nothing otherwise.

## Restore

Run the drill first if you have the time. It proves the replica restores before you depend on it:

```bash
python3 scripts/restore_drill.py --replica "$(tofu -chdir=infra/gcp output -raw replica_url)" --docker
```

It needs credentials that can read the bucket's objects. If your own account gets a 403, grant it `roles/storage.objectViewer` on the bucket.

### The VM is broken, the data disk is fine

```bash
tofu apply -replace=google_compute_instance.cairn
```

The new VM gets the same disk, address and configuration, and starts the proven image recorded on the data disk. This is also how the OS is upgraded: a new VM boots the current `cos-stable` image.

### The database is lost or damaged

Litestream restores the latest replica on its own whenever the database file is missing. Move the damaged files aside and let it:

```bash
gcloud compute ssh cairn --zone ZONE --tunnel-through-iap
sudo -i
bash /etc/cairn/host.sh hold               # stops the updater's timer and any update in flight; prints the state
# Read what it printed before going on:
# - "proven" names no image: cairn has never passed a deployment on this disk, so it holds no
#   database of yours, and cairn.service would not start. Stop here.
# - "pending" names an image: the hold cut a deployment short, and every start of cairn would
#   run that image, which nothing has judged. Make the proven one the image again first:
#     bash /etc/cairn/host.sh pin sha256:DIGEST      # the digest on the "proven" line
systemctl stop cairn.service litestream.service
systemctl is-active cairn-update.service cairn.service litestream.service   # three lines, none "active"
aside=/mnt/disks/data/aside-$(date -u +%Y%m%dT%H%M%SZ)
mkdir "$aside"                             # a new directory each time, so an earlier copy is never overwritten
cd /mnt/disks/data/cairn && mv cairn.db cairn.db-wal cairn.db-shm .cairn.db-litestream "$aside"/ 2>/dev/null
systemctl restart cairn-restore.service    # restores, because cairn.db is now missing
systemctl start cairn.service litestream.service
bash /etc/cairn/host.sh release            # only after /healthz answers: the updater runs again
```

The hold comes first because stopping the timer alone does not stop an update that already started: it would restart cairn while the files are being moved, and cairn would create an empty database that replication then follows. The pin matters when the hold lands in the middle of a deployment: the host rehearsal showed that the candidate is then left as the image cairn starts, with no verdict, until a pin or a forced release settles it.

`release` refuses when the deploy tag names another image than the one cairn starts, and says so. That is the case after the pin above (the tag still names the deployment the hold cut short), after an automatic rollback (the tag names the image that failed), and when someone pushed during the hold. Then either move the tag to the image cairn runs ([Rollback](#rollback), the manual command) and release, or run `bash /etc/cairn/host.sh release --force`: the updater takes up the tag's image within a minute and judges it like any deployment, going back to the proven one if it does not serve. An image recorded as failed is skipped; a pin clears that record, so after a pin it gets one more try.

Litestream 0.5 refuses to restore while `cairn.db` or its `-wal`/`-shm` files exist, which is why they are moved, not overwritten. Check `/healthz`, then delete the `aside-...` directory.

### Restore to a point in time

Not every moment of the last week can be restored. Litestream replays whole files, so a restore lands on the end of a file that still exists in the bucket (`deploy/litestream.yml` has the settings):

| How far back | Where a restore can land |
|---|---|
| the last 5 minutes | each upload that had writes: 10 seconds apart |
| from there to the oldest snapshot, 7 to 8 days back | the level-1 files: 30 seconds apart while there were writes, nothing through idle time |
| older than the oldest snapshot | nowhere. Litestream has deleted those files. |

`-timestamp` is exclusive: Litestream uses the files whose time, the time of the last write they hold, is strictly before the time you give. Ask for 08:00:00 and you get the last restore point before 08:00:00, which outside the last five minutes can be up to 30 seconds of writes earlier. Nothing after the time you give is included. These numbers come from Litestream's documentation and the settings; they have not been measured on this replica.

Look before you restore. Both commands only read, and both run while cairn is up:

```bash
sudo bash /etc/cairn/host.sh litestream ltx -level all /data/cairn.db | tail -n 40
sudo bash /etc/cairn/host.sh litestream restore -dry-run \
  -timestamp 2026-11-10T08:00:00Z -o /data/dry-run.db /data/cairn.db
```

The first lists the files in the replica with their level and creation time. The second prints the plan: the snapshot it would start from and the files it would replay, the last of which is where you land. `host.sh litestream` runs the Litestream image with the replica's settings on the one Docker network that can fetch storage credentials; a plain `docker run` cannot.

Then hold, stop the services and move the files aside, with the first lines of [The database is lost or damaged](#the-database-is-lost-or-damaged) up to and including the `mv`, and restore:

```bash
bash /etc/cairn/host.sh litestream restore -timestamp 2026-11-10T08:00:00Z /data/cairn.db
```

If the restore undoes a schema migration, follow the held procedure in [Rollback](#rollback) instead, so that the newer image cannot start on the restored database. Otherwise, do not start the services by hand afterwards. Give the replica a new path first, so the old history and the restored database do not mix: set `replica_path` (for example `cairn-20261110`) in `terraform.tfvars`, run `tofu apply`, and restart the VM (`gcloud compute instances stop`, then `start`). Everything comes back up replicating to the new path. The hold is on the data disk and is still there after the restart: when `/healthz` answers, end it with `bash /etc/cairn/host.sh release` ([above](#the-database-is-lost-or-damaged) says when it refuses and what to do then). How Litestream 0.5 behaves when an older database meets a newer replica at the same path has not been tested here; a new path avoids the question. Delete the old path when you no longer need it.

### The replica was deleted or tampered with

The VM's identity can delete replica files, because Litestream's own retention has to. What it cannot do is delete them for good. The bucket has soft delete set to 7 days (`replica_soft_delete_days`, the least Cloud Storage allows): everything deleted or overwritten in it stays restorable for that long, and Cloud Storage's documentation says it cannot be permanently deleted during that time. The VM's identity has no permission on the bucket itself, so it cannot shorten the window, and a shorter window would not apply to what is already deleted anyway. This is the backstop behind a compromised VM or a mistake.

If the database on the data disk is intact and trusted, you do not need the old replica at all: set a new `replica_path`, `tofu apply`, restart the VM, and Litestream starts a fresh replica from the live database.

If the database is gone or not trusted either, you have 7 days from the deletion to bring the replica back:

1. **Stop the writers**, so nothing new lands in the path. On the VM: `bash /etc/cairn/host.sh hold`, then `systemctl stop cairn.service litestream.service`, as the first lines of [The database is lost or damaged](#the-database-is-lost-or-damaged) do. The VM stays up: the restore needs it. If the VM itself is suspect, stop it instead (`gcloud compute instances stop cairn --zone ZONE`), disable its service account (`gcloud iam service-accounts disable "$(tofu output -raw vm_service_account)"`), and replace it while the account is still disabled.
2. **See what is there.** `tofu output -raw replica_url` is `gs://BUCKET/PATH`.

   ```bash
   gcloud storage ls --soft-deleted "gs://BUCKET/PATH/**" | tail -n 20
   ```

3. **Restore what was deleted since the damage began.** You need Storage Admin on the bucket. A bulk restore runs in the background and can take an hour or more to begin.

   ```bash
   gcloud storage restore "gs://BUCKET/PATH/**" --async --deleted-after-time="2026-11-09T00:00:00Z"
   ```

   Files that were overwritten, not deleted, still have a live object in the way. Add `--allow-overwrite` to put the soft-deleted version back over it. For a name that was overwritten more than once, list its generations with `--soft-deleted` and restore the one from before the damage: `gcloud storage restore "gs://BUCKET/PATH/FILE#GENERATION"`.
4. **Prove it restores** before relying on it: `python3 scripts/restore_drill.py --replica gs://BUCKET/PATH --docker`. The bulk restore also brings back files Litestream had deleted on purpose in that window (old snapshots and the files before them). That is more history than usual, and it should be harmless, but a restore from such a path has not been tested here. If the drill fails or the newest note is older than it should be, use the dry run from the section above to find a point that restores.
5. **Bring the database back and move on to a new path.** On the VM, with the services still stopped and the hold in place, move the database files aside and restore as in [Restore to a point in time](#restore-to-a-point-in-time), with `-timestamp` set before the damage or without it for the latest state; then change `replica_path`, restart the VM and release the hold, as that section says.

   If you stopped or replaced the VM in step 1, do not start it with its account enabled. At boot it would restore the newest state in the path if the database is missing, serve it, and let Litestream write into the path you just recovered, where its retention can delete again the older files the bulk restore brought back. Start it with the account still disabled: without credentials neither the settings step nor the restore step can finish, so cairn and Litestream do not start. Then, on the VM, take the hold, stop the waiting steps and the services (`systemctl stop cairn-env.service cairn-restore.service cairn.service litestream.service`), enable the account again, and restore by hand as above. This order is read from the units; it has not been run on a VM.

Past the 7 days the files are gone, and the daily disk snapshots (14 days, below) are what is left. A longer `replica_soft_delete_days` (up to 90) buys more time here, and keeps what a person deleted in cairn that much longer ([below](#how-long-a-deletion-stays-in-the-backups)).

### How long a deletion stays in the backups

What a person deletes in cairn leaves the database at once ([After a workspace is deleted](#after-a-workspace-is-deleted) says what is left in the log file and when it goes), but not the backups made before. With the shipped settings:

| Where | How long it keeps a deleted note |
|---|---|
| Litestream's files in the bucket | up to 8 days (`retention: 192h` in `deploy/litestream.yml`) |
| Then soft delete in the bucket | 7 more days (`replica_soft_delete_days`) |
| The replica bucket in all | up to 15 days |
| The daily disk snapshots | up to 14 days (`google_compute_resource_policy.data_daily`) |

Seven days is the least soft delete Cloud Storage allows. A longer window keeps more time to bring back a replica that was deleted or tampered with ([above](#the-replica-was-deleted-or-tampered-with)) and keeps deleted notes in the bucket for as long; that is the operator's trade.

### The data disk is lost

Two ways back, in order of preference:

1. **From the replica.** If the disk no longer exists, `tofu apply` creates a blank one and attaches it. Restart the VM: at boot it formats the blank disk, restores the database from the bucket and requests new certificates. The record of the proven image is gone with the disk, so within a minute the updater deploys whatever the tag names, as a first deployment.
2. **From a disk snapshot.** `gcloud compute snapshots list` shows the daily snapshots. Stop the VM, detach and delete the damaged disk, create a disk with the same name from the snapshot (`gcloud compute disks create cairn-data --source-snapshot=NAME --type=pd-balanced --zone ZONE`), attach it with `--device-name cairn-data`, set a new `replica_path` in `terraform.tfvars` and run `tofu apply` (which also puts the snapshot schedule back), then start the VM. The new path matters: the snapshot's database is older than what the replica holds, the case [above](#restore-to-a-point-in-time) avoids the same way. Snapshots are crash-consistent and up to 24 hours old; the replica is seconds old.

## Secret rotation

```bash
gcloud secrets versions add "$(tofu output -raw env_secret)" --data-file=cairn.env
gcloud compute ssh cairn --zone ZONE --tunnel-through-iap \
  --command 'sudo systemctl restart cairn-env.service cairn.service'
gcloud secrets versions list "$(tofu output -raw env_secret)"
gcloud secrets versions destroy OLD_VERSION --secret="$(tofu output -raw env_secret)"
```

The VM always reads the latest version, at boot and when `cairn-env.service` is restarted. Destroy the old version only after `/healthz` answers with the new one.

The VM checks the data it reads against the checksum Secret Manager sends with it (`dataCrc32c`); when they differ it writes nothing, and `cairn-env.service` fails with one line in its journal, so cairn does not start. Restart `cairn-env.service` to read the secret again. An answer without a checksum is written as before, with a line that says it was not checked.

## Changing the configuration

`tofu apply` updates the VM's `user-data`, and cloud-init reads it at boot. Changes to the domain, the extra arguments, the plans file, the Caddy or Litestream versions, or anything under `deploy/` and `files/` therefore need a restart of the VM after the apply:

```bash
gcloud compute instances stop cairn --zone ZONE && gcloud compute instances start cairn --zone ZONE
```

Growing the data disk needs the same restart: raise `data_disk_size_gb`, apply, restart. The file system grows at boot. Disks cannot shrink.

Changing `boot_disk_size_gb` replaces the VM: the boot disk is made with it. That is the step of [The VM is broken, the data disk is fine](#the-vm-is-broken-the-data-disk-is-fine), and `tofu plan` says so. The boot disk holds the pulled images and the containers' logs; after each promotion the updater removes every image older than three days, except the running image and the one before it, which a rollback needs.

**Plans.** To hold workspaces to plans, set `cairn_plans_file` in `terraform.tfvars` to a plans file on your machine (a copy of `deploy/plans.example.yaml`), relative to `infra/gcp` or absolute. `tofu apply` puts its content in the user-data. At boot cloud-init writes it to `/etc/cairn/plans.yaml`, owned by root with mode 0644, and cairn starts with that file mounted read-only and `--plans /etc/cairn/plans.yaml`: cairn's user can read it and cannot change it. Plans are not secret (a workspace's **Usage** page shows its plan's limits), so the user-data, which anyone who can read the instance's metadata can read, is an acceptable place for them. `tofu plan` refuses a file that is missing, empty, larger than 16 KiB, or not printable ASCII text that ends in exactly one line break, and says which. It also refuses `--plans` in `cairn_extra_args` while `cairn_plans_file` is set. After an edit of the file: `tofu apply`, then stop and start the VM as above. Without `cairn_plans_file` the file is empty, and cairn starts without plans.

**Brand.** To show your own name in place of cairn's, set `cairn_brand_name` in `terraform.tfvars`, and with it, if you want a logo, `cairn_brand_logo_file`, an SVG or a PNG on your machine (relative to `infra/gcp` or absolute), and `cairn_brand_logo_alt`, its alt text. `tofu apply` puts each value that is set in the user-data, base64. At boot cloud-init writes them to `/etc/cairn/brand/name`, `/etc/cairn/brand/logo` and `/etc/cairn/brand/logo-alt`, owned by root with mode 0644, and cairn starts with `--brand-name=NAME`, the logo mounted read-only with `--brand-logo /etc/cairn/brand/logo`, and `--brand-logo-alt=TEXT`. Each value reaches `serve` as one argument, as you wrote it: no shell expands it and nothing splits it, so a space, a quote, a backslash or a `$` arrives as it is. The brand is not secret (the sign-in page shows it), so the user-data, which anyone who can read the instance's metadata can read, is an acceptable place for it. `tofu plan` refuses what `serve` would refuse at start: a name that is not 1 to 60 characters or an alt text that is not 1 to 200, one that is only spaces, or one that holds a control character, a character that is not seen (a zero-width space, a direction mark), `<` or `>`; and a logo without a name, an alt text without a logo, and a name without `--sign-in` in `cairn_extra_args`. It also refuses `--brand-name`, `--brand-logo` and `--brand-logo-alt` in `cairn_extra_args`. Of the logo file it refuses one that is missing, empty or larger than 60 KiB (61,440 bytes), and it checks only how the file starts: the 16 bytes every PNG starts with, or an SVG whose first element is `<svg` with `xmlns="http://www.w3.org/2000/svg"`, after at most an XML declaration, white space and comments. It refuses an SVG with a document type declaration (`<!DOCTYPE`), which `serve` refuses so that reading a logo fetches nothing and expands nothing. `serve` reads the whole file at start and may still refuse one the plan took, such as an SVG that is not well-formed XML; on the VM that is a container that does not come up. `serve` itself takes a logo of up to 256 KB; the VM takes at most 60 KiB because the logo travels in the user-data next to the plans file, and Compute Engine takes at most 256 KB in one metadata value, which `tofu plan` also checks for the whole user-data. `--wall` and `--session-days` stay in `cairn_extra_args`, and `tofu plan` refuses either without `--sign-in`. It also refuses a `--session-days` that is not followed by a whole number from 1 to 365, written `--session-days 30` or `--session-days=30`, and a `--wall` with a value (`--wall=yes`): `--wall` takes none. A change of the brand goes as a change of the plans file does: `tofu apply`, then stop and start the VM as above. Unlike the plans file, which is written empty when unset, a brand value that is not set is not written at all; `/etc` is on tmpfs, so a value you remove is gone after the restart and cairn starts without its option.

### Before the next apply on an existing deployment

A deployment made before these defaults changed takes them at its next `tofu apply`. Read `tofu plan` first, and review:

- **`os_login_2fa` is now on by default.** An operator whose Google account has no 2-Step Verification is refused SSH through IAP until they switch it on or set `os_login_2fa = false` and apply again; the serial console and a start-up script are the ways back in. Both are instance metadata (`serial-port-enable`, `startup-script`) that a project owner can set with `gcloud` without SSH, and the next `apply` removes them again; neither has been tried on this VM, and an organisation policy that disables the serial port closes the first.
- **`boot_disk_size_gb` is now 20.** A VM made with a smaller boot disk is replaced (see above); set `boot_disk_size_gb = 10` to keep the one that runs.
- **`replica_soft_delete_days` is now 7.** Set `replica_soft_delete_days = 30` to keep what the bucket does today; 7 is the least Cloud Storage allows, and keeps deleted notes in the bucket for less time ([above](#how-long-a-deletion-stays-in-the-backups)).
- **`data_access_audit_logs` is on.** Its entries are billed as log volume past the free allowance.
- **The registry cleans up after itself.** It deletes every image that is neither among the newest `registry_keep_versions` nor named by `deploy_tag` once it is older than `registry_delete_after_days`.

## Audit, keys and network

**Audit logs.** Data Access audit logs for Cloud Storage and Secret Manager are on (`data_access_audit_logs`): who read, wrote or deleted the replica's objects, and who read the secret, with the identity that did it. Litestream's uploads and the replica check's listings each make an entry, so expect thousands a day; they are billed as log volume past the free allowance. The setting replaces any audit configuration set elsewhere for those two services.

**Retention.** The project's `_Default` log bucket keeps logs 30 days (`log_retention_days`, Google's own default); the audit logs above, the containers' logs and the system's go there. A longer retention is billed. This sets the existing bucket and does not delete it on `tofu destroy`.

**Network.** The subnet has Private Google Access on. Firewall rule logging (`firewall_logging`) and VPC flow logs (`vpc_flow_logs`) are off by default, because both are billed by volume.

### Customer-managed keys

For an operator who must hold the keys. Set `kms_key_name` to a Cloud KMS key in the same region as everything else, and the data disk, the boot disk, the replica bucket and the environment secret use it. Unset, Google manages the keys, as before. `tofu plan` refuses a key in another region.

The key needs `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key for three of Google's service agents of this project: Compute Engine's for the disks, Cloud Storage's for the bucket, Secret Manager's for the secret.

```bash
NUMBER=$(gcloud projects describe PROJECT_ID --format="value(projectNumber)")
gcloud beta services identity create --service=secretmanager.googleapis.com --project=PROJECT_ID
for agent in \
  service-PROJECT_NUMBER@compute-system.iam.gserviceaccount.com \
  service-PROJECT_NUMBER@gs-project-accounts.iam.gserviceaccount.com \
  service-PROJECT_NUMBER@gcp-sa-secretmanager.iam.gserviceaccount.com; do
  gcloud kms keys add-iam-policy-binding KEY --keyring=RING --location=REGION \
    --member="serviceAccount:${agent/PROJECT_NUMBER/$NUMBER}" \
    --role=roles/cloudkms.cryptoKeyEncrypterDecrypter
done
```

Grant it before the apply: a disk, a bucket or a secret that cannot use its key is not made. Disabling or destroying the key makes all four unreadable, the database and its replica included.

Not covered: the disk snapshots, the registry, the logs and the state bucket. On an existing deployment, setting it replaces the data disk, the VM and the secret, as far as can be read without a project: read `tofu plan` before `apply`. The data disk is protected from destroy, so the plan stops there; move the database to a new disk by the steps of [The data disk is lost](#the-data-disk-is-lost), and add the secret's version again ([Secret rotation](#secret-rotation)). None of this has been applied.

## What a regulated customer adds

These definitions are not ready as they stand for health or education records, or other data a law governs, even inside the customer's own Google Cloud account. What they do not do, and such a customer adds:

- **a signed agreement with Google** (for health data in the United States, Google's Business Associate Agreement), with the deployment kept to the products and regions it covers;
- **organisation policies** that hold the project to what this file sets by hand: resource locations, uniform bucket access, no public access, no serial port, allowed members;
- **VPC Service Controls** around Cloud Storage, Secret Manager and Artifact Registry, so that a stolen credential cannot copy the data out of the perimeter;
- **a locked log sink**: the audit logs routed to a bucket with a retention lock, for the years a law asks, where nobody with project access can delete them;
- customer-managed keys ([above](#customer-managed-keys)) on what they do not cover here, and a record of reads inside cairn itself, which cairn's own audit page does not keep.

## Teardown

1. Take a last copy if you want one: run the restore drill, or `gcloud storage cp -r` the replica path.
2. In `main.tf`, remove `prevent_destroy = true` from `google_compute_disk.data`.
3. Empty the replica bucket: `gcloud storage rm "gs://BUCKET/**"`. What you delete stays in soft delete, billed, for another 7 days. To skip that, turn soft delete off first (`gcloud storage buckets update gs://BUCKET --soft-delete-duration=0`); it only affects what is deleted afterwards.
4. `tofu destroy`.
5. Clean up what OpenTofu does not own: the scheduled disk snapshots (kept on purpose when the disk is deleted), the state bucket, and the DNS record.

Workload identity pools and custom roles are soft-deleted by Google, and their IDs cannot be reused for about a month. To rebuild sooner, change `name`.

## What the deployer can do

Two grants, and no other:

| Grant | Scope | What it allows |
|---|---|---|
| `roles/artifactregistry.writer` | the one repository | upload images, create and move tags. Not delete images or tags. |
| a custom role with `compute.instances.getGuestAttributes` only | the one instance | read the guest attributes the VM publishes: `cairn/status`, and the SSH host keys the guest environment publishes (public keys). Nothing else about the instance. |

It can change nothing on the VM: no right to set metadata (so it cannot replace `user-data`), to read the instance's metadata, serial console or disks, to stop or start it, or to act as the VM's service account; no project-wide role.

That is still real power, stated plainly: **the deployer ships the code that runs.** Whatever it pushes under `cairn:production` is started by the VM within a minute as the cairn container. That code gets cairn's env file (the whole secret is its environment) and the database, and can serve, change or leak both. An image that fails `/healthz` (no answer, or 503 because it cannot read the database) is rolled back; one that answers `/healthz` and misbehaves is not caught by anything here.

What a malicious image does not get, if the metadata block works: the VM's token. Without it there is no direct access to Secret Manager or to the bucket (it cannot delete the replica), and no way to change what the host runs. The block has not been confirmed on a real VM yet ([next section](#containers-and-the-metadata-server)). Until it is, assume a malicious image can also reach the VM's token, which is what it could do before this change.

Who can become the deployer. The identity provider accepts a GitHub token only when all of these hold:

| Claim | Must be | What it shuts out |
|---|---|---|
| `repository` and `repository_id` | the configured name and numeric id | other repositories, forks, and a deleted name registered again by someone else |
| `event_name` | `push` | `pull_request`; `pull_request_target`, whose `ref` is the base branch and would pass a check on the ref alone; manual, scheduled and release runs |
| `ref` | `refs/heads/main` | other branches, and every tag: a tag push runs the tagged commit's own copy of `ci-cd.yml`, so whoever could push a tag would choose what the token is used for |
| `workflow_ref` | `.github/workflows/ci-cd.yml` at `main` | every other workflow file in the repository |
| `environment` | `production` (`github_environment`) | every job that does not run in that environment. Required reviewers approve the job only when configured. A job without an environment has no such claim and gets nothing. |

So the controls that are left are in GitHub, not here: protect `main`, review changes to `ci-cd.yml`, and keep required reviewers on the `production` environment. Without required reviewers (a private repository on a free plan cannot have them) the environment claim still limits tokens to jobs that declare `environment: production`, but nobody approves them.

## Containers and the metadata server

Any process on a Compute Engine VM that can reach `169.254.169.254` (`metadata.google.internal`) can ask for the VM's access token, and that token reads the secret and writes the bucket. cairn and Caddy have no use for it, so they are kept away from that address:

- **Two Docker networks on named bridges.** `cairn` (`br-cairn`) holds cairn and Caddy. `cairn-backup` (`br-cairn-bkp`) holds Litestream and nothing else.
- **Two firewall rules.** A container's packets to the metadata server are forwarded by the host, and Docker hands every forwarded packet to its `DOCKER-USER` chain first. `host.sh network` puts two rules there, one for TCP and one for UDP: drop what is addressed to `169.254.169.254`, unless it comes in from `br-cairn-bkp` (this is how Litestream gets its Cloud Storage credentials) or goes to port 53 (DNS: the VM's resolver is that same address). The two rules do not depend on each other or on their order, and nothing is ever flushed, so steps that run at the same time cannot leave half a rule set.
- **Deny by default.** The rules do not name cairn's bridge, they name the one bridge that is allowed. Docker's default bridge is covered too, so a container started by hand with a plain `docker run` gets no token. For Litestream commands use `sudo bash /etc/cairn/host.sh litestream ...`.
- **The host is not affected.** Its own traffic is not forwarded, so `host.sh` still reads the secret and logs in to the registry.
- **Closed on failure.** cairn and Caddy require the unit that installs the rules, and `host.sh` checks the rules again right before it starts either container and puts back a missing one; if it cannot, the container is not started. The updater checks every minute as well, in case a Docker restart rebuilt its chains, and logs an error if it cannot put them back.
- **IPv4 only.** The VM has no IPv6 address. The IPv6 metadata address (`fd20:ce::254`) exists only on IPv6-only VMs.

What this does not do. Litestream still holds the VM's token, by design, so a compromised Litestream container can read the secret and delete replica files (soft delete is what covers the second). Taking that away needs a second identity, which one VM does not have. Root on the host is not contained by any of this.

**Unverified until a real boot.** The rules have run on a CI runner's Docker and iptables (the host rehearsal, below): there they kept the runner's own metadata address from cairn's network and from the default bridge, and left it reachable from Litestream's. They have not run on Container-Optimized OS. Four things can be wrong on a real VM: that Container-Optimized OS passes container traffic for the metadata server through `DOCKER-USER`, that its `iptables` accepts these rules, that Litestream still gets credentials from its own bridge, and that nothing else on the VM was relying on a container reaching the metadata server. The last one is about logs: current COS images run the logging agent as part of the OS, which is not affected, but the older container-based agent would be. Check all four on the first VM, before real data:

```bash
sudo iptables -w -nvL DOCKER-USER | head -n 5   # two DROP rules for 169.254.169.254 with "!br-cairn-bkp" and "dpt:!53";
                                                # their packet counters grow with each try below

# Each must print "blocked" after about five seconds. Neither prints what the metadata server
# answers: if the block does not work, the answer is the VM's access token, and it must not
# reach a terminal or its recording.
sudo docker exec cairn python -c "
import urllib.request as u
try:
    u.urlopen(u.Request('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token', headers={'Metadata-Flavor': 'Google'}), timeout=5).close()
    print('NOT BLOCKED: cairn reached the metadata server')
except Exception:
    print('blocked')"
sudo docker exec caddy sh -c "wget -T 5 -qO /dev/null --header 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/id && echo 'NOT BLOCKED: caddy reached the metadata server' || echo blocked"
# Must fail too: cairn cannot write the deploy status (guest attributes are on for host.sh).
sudo docker exec cairn python -c "import urllib.request as u; u.urlopen(u.Request('http://169.254.169.254/computeMetadata/v1/instance/guest-attributes/cairn/status', data=b'forged', method='PUT', headers={'Metadata-Flavor': 'Google'}), timeout=5)"

# Must still work: name resolution from cairn, and Litestream's uploads.
sudo docker exec cairn python -c "import socket; print(socket.gethostbyname('storage.googleapis.com'))"
sudo journalctl -u litestream -n 20 --no-pager  # no credential errors, no 403
```

And from your own machine, that container logs still arrive: `gcloud logging read 'logName:cos_containers' --freshness=10m --limit=5`.

## Logs

- **Cloud Logging**: container output is under log name `cos_containers` for the instance. The COS agent also exports journal entries logged at error priority, which is how the updater's errors are meant to arrive; that path is not confirmed on a live VM yet.
- **On the VM**: `sudo journalctl -u cairn -u caddy -u litestream -u cairn-update -u cairn-restore -u cairn-env -u cairn-replica-check -u cairn-compact -u cairn-host-check`, and `sudo docker ps`.
- **Query strings and invitation links**: Caddy's logs cut every query string and the token of an invitation link out of the logged address, and drop the `x-api-key`, `Referer` and `Location` headers. cairn's own container log holds no request line at all: `serve` starts uvicorn with its access log off and writes its own line per request (who called, what, which project, the status), so neither a sign-in code nor a key reaches Cloud Logging through the `cairn` container's output.

The audit log has a copy the server cannot change: each event the server stores is one JSON line from the logger `cairn.audit` in the container's output, written once the event's write has committed. Route those lines with a Cloud Logging sink to a Cloud Storage bucket with a locked retention policy (Bucket Lock), so that nobody, the server included, can edit or delete them before the retention ends. The bucket keeps what it received: an event committed just before the server's process dies has no line there, and one a command of the command line stores, such as `key-create` or `import`, has its line on that command's standard error, not in the container's output. The log in the database is rewritten when a note is forgotten or a project deleted, and pruned past `--audit-keep`.

## Alerts

All eight alerts send email to `alert_email`.

**The uptime alert** fires while `https://DOMAIN/healthz` fails from more than one region for three minutes. [Bootstrap](#bootstrap-once-by-a-person) says when that is expected and what `/healthz` checks. It is also the alert for a VM that is off or cannot boot: nothing answers.

**The replica alert** fires when the `litestream` container logs a line at level `ERROR`, or the line starting with `Error:` that Litestream prints when it cannot start. It reads the lines that already reach Cloud Logging (log `cos_containers`, this VM's instance, container `litestream`); nothing on the VM changes for it. Litestream 0.5.18 logs a failed upload, compaction or retention step at level `ERROR` and tries again, and logs a healthy start, compaction, snapshot or restart at `INFO`. With its replica refused, the stack rehearsal saw it log an error within seconds of a write and again after a restart. The `Error:` line comes from a container that exits at once and is removed (`docker run --rm`); whether Cloud Logging gets that line before the container is gone has not been seen. If it does not, a Litestream that cannot start counts among those that stop without a word, below, and `sudo journalctl -u litestream` on the VM still has the line. It sends at most one email an hour while the errors go on, and closes the incident 30 minutes after the last one, without an email. It also catches the `Error:` line of the replica check, below, while Litestream's container runs.

What the replica alert does not see. It fires on a failure Litestream reports. It does not see a Litestream that stops without a word or stalls silently, and an idle database writes nothing to the replica, so silence alone cannot be told from health. The replica check and the silence alert, below, are for that. A VM that is off sends no lines at all; the uptime alert covers that. The restore step at boot (`cairn-restore`) is another container: when it fails, cairn does not start, and the uptime alert fires.

**The replica check.** Every five minutes `cairn-replica-check` (`host.sh replica-check`) asks the replica how old its newest copy is and writes one line into the `litestream` container's log, which Cloud Logging receives as it receives Litestream's own: `replica: current, newest NN s old`, or `Error: replica: newest NN s old, over the bound` when a change to the database has waited more than 15 minutes for its copy. NN is the age of the newest transaction the replica holds, the largest timestamp `litestream ltx -json` lists (Litestream stamps each file with the time of the newest transaction in it, and a file compacted to a higher level keeps that time). An old copy is not stale by itself: while nothing writes to the database there is nothing to copy. The check tells the two apart by the database's own files: while Litestream copies, the last change to `cairn.db` and `cairn.db-wal` is never more than a few seconds newer than the newest copy. A later change is not copied yet, unless it is older than the start of the `litestream` container: a Litestream that stops checkpoints the database as it closes it, which moves the file's time with nothing new to copy, and a Litestream that starts copies what changed while it was down. A change not copied yet the check remembers it (in `/mnt/disks/data/replica-unconfirmed`) and says `Error:` once it is older than the bound with no copy at or after it. So an idle database stays current, a write seconds before a check is not an error, and a write Litestream does not copy is one 15 to 25 minutes after it was made. The check reads the replica as Litestream's restore would (a container of the pinned image on Litestream's network, the database mounted read-only): one `ltx` listing for each level it reads, levels 0, 1 and 9 in turn until one holds a file. When it cannot list the replica, or Litestream's container is not running, it writes no line to Cloud Logging, and its run fails in the journal (`sudo journalctl -u cairn-replica-check`).

**The silence alert** fires when no `replica: current` line has been counted for 20 minutes: a log-based metric counts those lines, and an absence condition watches it. That covers Litestream gone (no container to write into), the check's timer gone, the VM's logging gone, and a check that cannot list the replica. It emails once when an incident opens; Google's rate limit applies to log-match conditions only, so a second email needs a current line first and then 20 more minutes without one. The incident closes, without an email, once a current line is counted again. Google arms an absence condition only after the metric has had a point since the policy was made or changed, so on a new VM it is armed by the first current line. That line comes within two minutes of boot (the check's timer runs two minutes after boot, then every five minutes), also before cairn's first start: the `litestream` container runs from boot and waits inside for the database cairn creates, and with no database and no deployment yet the check writes `replica: current, no database yet, nothing to copy`, since there is nothing to copy. Once a deployment has been proven, a missing database is not current: the check writes no line, its run fails in the journal, and the alert fires after 20 minutes. Whether Google arms the condition on that first point has been read in its documentation, not seen on a VM.

**The compaction alert** fires when cairn logs `compact not finished` more than twice within an hour. It reads the lines that already reach Cloud Logging (log `cos_containers`, this VM's instance, container `cairn`); nothing on the VM changes for it. After a workspace is deleted, `serve` compacts the database, and beside Litestream the last step of a compaction does not finish ([When a compaction keeps not finishing](#when-a-compaction-keeps-not-finishing)). One such line a day, or one after each restart of cairn, is what a server beside Litestream writes since decision 143, and does not fire it. Three within an hour is a server that tries again at every poll (an image older than that fix) or one that keeps restarting. A log-based metric counts the lines, and the policy fires when their sum over the last hour is above 2. It emails once when an incident opens; the incident closes, without an email, once no more than two lines fall within the last hour, so within an hour of the last line.

**The host alerts** are four, one for each kind of line `cairn-host-check` (`host.sh host-check`) writes every five minutes, and only when it finds something:

| Alert | The line | When |
|---|---|---|
| disk | `host-check: disk: the data disk (/mnt/disks/data) is NN% full, over 80%` | the data disk, or the boot disk (`/var/lib/docker`), is more than 80 percent full (`df`) |
| memory | `host-check: memory: the container NAME was killed for memory` | Docker reported an `oom` event for a container since the last run: it reached its limit (`container_limits`) |
| restarts | `host-check: restarts: systemd restarted UNIT N times since the last check` | systemd restarted `cairn`, `caddy` or `litestream` three times or more since the last run (its `NRestarts`, which starts again at boot) |
| certificate | `host-check: certificate: Caddy logged N errors obtaining or renewing a certificate since the last check` | Caddy's log since the last run holds a line at level `error` from its `tls.obtain` or `tls.renew` logger |

The line goes into the log of the first container that runs, `litestream`, else `cairn`, else `caddy`, as the replica check's does, so the filters name the log, the instance and the line's start but no container. With none of the three running the line reaches the journal only, the run fails (`sudo journalctl -u cairn-host-check`), and the uptime alert is the one that fires. Each alert sends at most one email an hour while its lines go on, and closes the incident 30 minutes after the last one, without an email. No line starts with `Error:` or `replica: current, `, so neither replica alert reads them. Caddy renews a certificate 30 days before it expires and tries again after a failure, so the certificate alert comes weeks before the certificate stops working.

### When the replica alert fires

1. **Read the lines.** From your machine:

   ```bash
   gcloud logging read 'logName:cos_containers AND jsonPayload."cos.googleapis.com/container_name"="litestream"' --freshness=1h --limit=30 --format='value(jsonPayload.message)'
   ```

   or on the VM, `sudo journalctl -u litestream -n 50 --no-pager`. In each line, `msg=` names the step that failed and `error=` says why.
2. **Tell the cause.** What a refused bucket and a dead credential print on Google Cloud has not been seen here (the rehearsal refuses a directory, not a bucket); these are the shapes to look for:
   - **A refused bucket**: the error carries Cloud Storage's `Error 403` and names a permission, such as `storage.objects.create`. The VM's account has a token and the bucket says no: the bucket is gone, or the account lost its role on it (`iam.tf`; `tofu plan` shows a missing binding).
   - **An expired or missing credential**: the error is about the token, not the bucket (`oauth2`, `could not find default credentials`), or Litestream exits at start with `Error: cannot open store: initialize replica client` and systemd starts it again. The VM's account is disabled, or Litestream's network cannot reach the metadata server ([Containers and the metadata server](#containers-and-the-metadata-server)).
   - **A full disk**: `msg="disk full while staging ltx file, replication paused until space is freed"`. Grow the data disk ([Changing the configuration](#changing-the-configuration)).
3. **Check that the replica is current again.** After the fix, no `ERROR` follows. If an upload had failed, Litestream also logs `msg="sync recovered"` (or `msg="replica sync recovered"`); when the failure met only compaction and retention, it does not. Litestream tries again by itself within 5 minutes of the fix; `sudo systemctl restart litestream` makes it try at once. Each file in the replica carries the time of the newest write it holds, not the time it was uploaded: a write held back by the failure shows its own time once it is copied. On the VM, the newest files must hold the latest write, or the replica check must say current ([When the replica goes quiet](#when-the-replica-goes-quiet), step 2):

   ```bash
   sudo bash /etc/cairn/host.sh litestream ltx -level all /data/cairn.db | tail -n 5
   ```

4. **Prove it restores.** Only a restore drill proves a replica: a replica that takes uploads can still fail to restore. From your machine, as in [Restore](#restore):

   ```bash
   python3 scripts/restore_drill.py --replica "$(tofu -chdir=infra/gcp output -raw replica_url)" --docker
   ```

### When the replica goes quiet

The silence alert says that no line has said the replica is current for 20 minutes. It does not say why.

1. **Read the check's last runs.** On the VM:

   ```bash
   sudo journalctl -u cairn-replica-check -n 30 --no-pager
   sudo systemctl status litestream cairn-replica-check.timer --no-pager
   ```

   - The last line is `Error: replica: ...` and the replica alert fired too: Litestream runs and does not copy. Go to [When the replica alert fires](#when-the-replica-alert-fires).
   - `the litestream container is not running`: Litestream is down or restarting. `sudo journalctl -u litestream -n 50 --no-pager` says why; `sudo systemctl restart litestream` starts it again.
   - `could not list the replica`, with Litestream's error above it: the check cannot read the bucket. Read that error as step 2 of [When the replica alert fires](#when-the-replica-alert-fires) describes.
   - Nothing for the last 20 minutes, or the timer is `inactive`: the timer is not running. `sudo systemctl start cairn-replica-check.timer` (a restart of the VM starts it too).
   - `replica: current` lines every five minutes on the VM: the lines do not reach Cloud Logging. Check that the VM's other logs still arrive ([Logs](#logs)).
2. **Check that it is current again.** Litestream retries a failed upload by itself, without a restart: it waits 10 seconds, then twice as long after each failure, up to 5 minutes. So after a fix the next copy can be up to 5 minutes away; `sudo systemctl restart litestream` makes it try at once. The check says current again, and forgets the change it remembered, once the change is copied. Run the check once by hand and read its line; the incident closes once a current line is counted:

   ```bash
   sudo systemctl start cairn-replica-check.service; sudo journalctl -u cairn-replica-check -n 3 --no-pager
   ```

### When a compaction keeps not finishing

The compaction alert says that cairn logged `cairn.access: compact not finished: another connection held the file (...) in NN ms` more than twice within an hour. A line that says `(rewritten; the write-ahead log was not emptied)` is a compaction that rewrote the whole database file under the store's lock, so every write waited for it, and Litestream then copied the rewritten file again. A line that says `(no rewrite: only the write-ahead log was due)` rewrote nothing: it held writes for at most about 5 seconds while it tried to empty the log, which is all a rewrite that succeeded leaves due, at the next interval and after a restart alike. The step that did not finish is the last one, truncating the write-ahead log: Litestream reads the log for as long as it runs. Nothing is lost when it does not finish: the database is whole, the deletion stays recorded as due, and Litestream's own checkpoints fold the log into the file later. A compaction starts only with twice the database's size free on its disk; without it cairn logs `compact not started`, which this alert does not count, and the disk alert covers the disk. What was measured beside Litestream, and what is not known, is in [docs/compaction-beside-a-replica.md](../../docs/compaction-beside-a-replica.md).

1. **Read the lines.** From your machine:

   ```bash
   gcloud logging read 'logName:cos_containers AND jsonPayload."cos.googleapis.com/container_name"="cairn" AND jsonPayload.message:"cairn.access: compact "' --freshness=2d --limit=50 --format='value(timestamp,jsonPayload.message)'
   ```

   If there is one line, do nothing: that is the one try a day, or the one after a restart. Lines about 10 minutes apart are a server that tries at every poll. Lines that follow restarts of cairn are a restart loop: `sudo journalctl -u cairn -n 100 --no-pager` on the VM says why it restarts.
2. **See whether a compaction is still due.** On the VM, a read that changes nothing:

   ```bash
   sudo docker exec cairn python -c 'import sqlite3; con = sqlite3.connect("file:/data/cairn.db?mode=ro", uri=True); print(con.execute("SELECT name, value FROM settings WHERE name LIKE ?", ("store.compact%",)).fetchall())'
   ```

   `store.compact_due` is there while a deletion waits for a compaction, with the time of the deletion; `store.compacted_at` is when the last rewrite began. Beside Litestream `store.compact_due` stays, because no compaction of `serve`'s finishes there; [`host.sh compact`](#after-a-workspace-is-deleted) clears it.
3. **Check the running image.** The fix, a compaction that did not finish waits its interval before the next try, is commit `f14b49b` (pull request 23, merged into `main` on 2026-11-17). An image built before it tries again at every poll. Read the digest cairn runs (`running=` in the status line, [Deploy](#deploy)), then the tags of that digest in Artifact Registry:

   ```bash
   gcloud artifacts docker images list "$(tofu -chdir=infra/gcp output -raw deploy_image | cut -d: -f1)" --include-tags --format='value(version,tags)'
   ```

   The deploy job tags each image it copies `sha-COMMIT`, with the commit it was built from. In a checkout of the repository, `git merge-base --is-ancestor f14b49b COMMIT && echo has-the-fix` says whether that commit holds the fix. An image pushed by hand carries whatever tag its pusher gave it.
4. **What to do.** An image without the fix: deploy one from `main` ([Deploy](#deploy)); the lines then come once a day at most. An image with the fix and a restart loop: fix what restarts cairn; each start tries one compaction while one is due. Either way the incident closes by itself within an hour of the last line. To finish the compaction itself, and remove what the deleted workspace left in the log file, run the next runbook.

### When a host alert fires

The email names the kind; the line says what and where. To read the lines, from your machine:

```bash
gcloud logging read 'logName:cos_containers AND jsonPayload.message:"host-check: "' --freshness=1d --limit=30 --format='value(timestamp,jsonPayload.message)'
```

- **disk**: on the VM, `sudo df -h /mnt/disks/data /var/lib/docker`. The boot disk holds the images: `sudo docker image ls` and `sudo docker image prune -af --filter until=72h` (what a promotion runs, keeping the running image and the one before it). The data disk holds the database: compact after a deletion ([After a workspace is deleted](#after-a-workspace-is-deleted)), or grow it (`data_disk_size_gb`, then `tofu apply`; the file system grows at the next boot).
- **memory**: `sudo journalctl -u cairn -u caddy -u litestream --since -1h`. One kill is a request or a source larger than the limit; raise that container's `memory_mb` in `container_limits` (the three must fit the machine: `machine_type`), `tofu apply`, and restart the VM. Kills that come back are a leak or a load the machine is too small for.
- **restarts**: `sudo systemctl status cairn caddy litestream --no-pager` and `sudo journalctl -u UNIT -n 50 --no-pager` say why the unit exits. A cairn that exits on start after a deploy: roll back ([Rollback](#rollback)).
- **certificate**: `sudo docker logs --since 1h caddy 2>&1 | grep '"logger":"tls'`. Most often the domain's DNS no longer points at the VM's address, or port 80 or 443 is not reachable from outside; fix that and Caddy tries again by itself.

The alert closes by itself 30 minutes after the last line.

### After a workspace is deleted

Beside Litestream a compaction rewrites the database but never truncates the write-ahead log, so what a deleted workspace left in the log file stays there until later writes reach it: 5 to more than 8 minutes on a laptop, with no bound found ([docs/compaction-beside-a-replica.md](../../docs/compaction-beside-a-replica.md)). `host.sh compact` finishes it by stopping Litestream for the seconds a compaction takes.

**Every night.** `cairn-compact` (a timer, at 03:00 UTC, an hour before the daily snapshot of the data disk) runs `host.sh compact-if-due`. It reads the compaction record (`store.compact_due`) and runs `host.sh compact` when, and only when, a deletion waits for a compaction; otherwise it logs `compact: nightly: no deletion waits for a compaction; nothing to do`. After a compaction it logs one line of sizes, `compact: nightly: database N -> N bytes, write-ahead log N -> N bytes` (`sudo journalctl -u cairn-compact`). When it fails, or cannot read the record, it writes `Error: compact: the nightly compaction failed: ...` with the step that failed into the `litestream` container's log, where the replica alert reads it ([Alerts](#alerts)); Litestream is started again as by hand. So a deletion leaves the log file within a day without a person. The steps below are for a deletion whose data must leave the disk now. At step 4 the nightly run asks cairn for `compact --if-due`, not `compact`: `serve` has usually rewritten the file after the deletion already and left only the write-ahead log, so the night empties the log alone, and a deletion costs one rewrite, not two. A deletion no rewrite has covered yet gets the whole compaction, and `host.sh compact` by hand is always the whole compaction.

**When.** After a workspace is deleted, once the compaction alert has fired or cairn has logged `compact not finished` ([When a compaction keeps not finishing](#when-a-compaction-keeps-not-finishing), step 1): the truncating step did not happen. Beside Litestream that is every deletion, so it is worth running after any deletion whose data must leave the disk now, and not only after the alert. On the VM:

```bash
sudo bash /etc/cairn/host.sh compact
```

**What it prints**, one line per step, in this order:

- `compact: step 1/6: cairn answers /healthz`.
- `compact: step 2/6: replica: current, newest N s old`: the replica check's own line ([Alerts](#alerts)), run once.
- `compact: before: database N bytes, write-ahead log N bytes; a deletion waits for a compaction: yes, since TIME`: the two files' sizes and the compaction record (`store.compact_due`, read-only; `no` when no deletion waits).
- `compact: step 3/6: litestream.service stopped`.
- `compact`'s own lines, as the run guide shows them: how many old page versions it removed (`compact` also removes what the recorded `--keep-versions` bound says no page keeps), and `compacted /data/cairn.db: N -> N bytes`.
- `compact: step 4/6: compact finished`.
- `compact: after: database N bytes, write-ahead log 0 bytes; a deletion waits for a compaction: no`: read before Litestream starts again. A log of 0 bytes is the truncating step done; what the deleted workspace left in it is gone.
- `compact: litestream.service started again`, then `compact: step 5/6: litestream.service started; it was stopped for N s`.
- `compact: step 6/6: replica current again: it holds a copy made after the stop (N checks after the start)`: the replica's newest file was made after the stop, so Litestream has copied the compacted database.
- `compact: done`, and exit status 0.

**When a step fails**, the line starts with `ERROR:`, the command exits 1, and its last line names the first step that failed.

- **Step 1/6** (`cairn does not answer /healthz`) and **Step 2/6** (`the replica is not current`, after the check's own lines): nothing was stopped or changed. Bring cairn back ([Deploy](#deploy)) or the replica ([When the replica alert fires](#when-the-replica-alert-fires), [When the replica goes quiet](#when-the-replica-goes-quiet)) first, then run it again.
- **Step 3/6** (`could not stop litestream.service`): nothing was compacted, and Litestream was started again. `sudo systemctl status litestream --no-pager` says why it would not stop.
- **Step 4/6**: `compact: step 4/6: compact exited N (its lines are above); the deletion stays recorded as due`, and at the end `compact: failed at step 4/6 (compact)`. Litestream is started again and step 6 still runs. The database is whole. Read `compact`'s own line above it; if another connection held the file, run the command again.
- **Step 5/6**: `compact: litestream.service did not start: run 'sudo systemctl start litestream'; nothing reaches the replica until it runs`, then `compact: failed at step 5/6 (start)`. Do what it says, and read `sudo journalctl -u litestream -n 50 --no-pager`. Until it runs, the silence alert fires after 20 minutes.
- **Step 6/6** (`the replica holds no copy made after the stop`): 24 checks, 5 seconds apart, found none. Litestream runs but has not copied; go to [When the replica alert fires](#when-the-replica-alert-fires), step 1.

**Interrupted.** Ctrl-C, a `kill` and a lost SSH session start Litestream again before the command exits (`interrupted at step ...`, then `compact: litestream.service started again`). A `kill -9` or a VM that stops cannot: then `sudo systemctl start litestream` (a restart of the VM starts it too), and the silence alert fires if nobody does. An interrupt during step 4 leaves `compact` running inside the container; Litestream then starts beside it, and that compaction ends as not finished, with the deletion still due. Run the command again.

**How long Litestream is stopped.** With Compose on a laptop, the same stop, `compact` and start took 3.3 to 3.7 s for a 2.9 MB database (three runs, [docs/compaction-beside-a-replica.md](../../docs/compaction-beside-a-replica.md#the-sequence-run)). On the VM add the one second the command waits after the stop, and the time `compact` takes, which grows with the database: about as long as copying the file. It has not been run on the VM. While Litestream is stopped cairn takes writes as usual and Litestream copies them once it starts; a replica check that runs in that time fails in the journal only (`the litestream container is not running`), which the silence alert does not count for 20 minutes. In the three runs Litestream logged no error after the start, so no replica alert is expected.

## Monthly cost items

List prices for `europe-west1`, read on 2026-11-10 from Google's pricing pages (compute, disks and images, VPC network, Cloud Storage). The European regions get none of Google's always-free VM or storage allowance.

| Item | Scales with | Price |
|---|---|---|
| VM, `e2-small` | fixed | $13.45 a month |
| Boot disk, 20 GB `pd-balanced` | fixed | $2.00 a month |
| Data disk, 20 GB `pd-balanced` | disk size | $2.00 a month |
| Static IPv4 address | fixed | $3.65 a month |
| Replica bucket: storage and write requests | database size, write rate; everything Litestream deletes is billed for 7 more days (soft delete) | $1.40 a month for 5 GiB and about 260,000 writes at a 10 s sync interval |
| Network egress | traffic | $0.60 a month for 5 GiB |
| Disk snapshots, 14 daily | changed blocks | not priced yet |
| Artifact Registry storage | images pushed (older than 14 days deleted, except the newest 50 versions and what the deploy tag names) | not priced yet |
| Secret Manager: one secret, its versions, one read per boot | versions kept | not priced yet |
| Cloud Logging and Monitoring: container logs, one uptime check, eight alert policies (five of them log-based, two on log-based metrics), two log-based metrics | log volume | not priced yet beyond the free allowance |
| Domain and DNS | outside Google Cloud | not priced yet |

The priced rows come to about $23 a month.

The replica alert reads log lines that already arrive, so it adds no log volume. Google's pricing examples count a log-based alerting policy as one metric reference at $0.35 a month, with no charge for points returned, because it returns none ([example 7](https://cloud.google.com/stackdriver/observability-pricing-examples), read on 2026-11-15; the pricing page, not the example, is what bills).

The replica check and the silence alert are not priced yet: on 2026-11-16 neither the pricing page nor the examples could be read. What they add, to price against it: 288 log lines a day (one per run, about 50 bytes each); one log-based metric, which Google bills as a custom metric, with one time series that gets a point when a current line arrives; one alert condition on it; and Cloud Storage listings, which are Class A operations: one to three `ltx` listings a run, 8,640 to 25,920 a month. How many storage requests one `ltx` listing makes was not counted; it is at least one, and more when a level holds many files.

The compaction alert is not priced either. It adds no log line (it counts lines cairn already writes, one a day at most while a deletion waits), one log-based metric with one time series that gets a point when such a line arrives, and one alert condition on it.

The four host alerts are log-based alerting policies too: by the same example, $1.40 a month together. `host-check` adds a log line only when it finds something, none on a healthy host.

The replica bucket's price was read when the bucket kept 30 days of old object versions and Litestream kept seven days of snapshots; the bucket now keeps 7 days of soft-deleted files and Litestream keeps eight days, which is less storage, by a number not measured.

## What was checked, and what was not

Checked on 2026-11-10, without a cloud project, OpenTofu or a running Docker daemon:

- In the first pass: every resource and argument against the google provider 8.10.0 docs and the 8.0 upgrade guide; the Caddy 2.11 directives; the COS cloud-init, file system, logging and registry pages.
- In the review pass: the bucket's `soft_delete_policy`, the custom role and the identity provider's arguments and limits against the provider 8.10.0 docs; the Litestream 0.5 config keys (`levels`, `l0-retention`, `snapshot`), its `restore` and `ltx` flags and its retention rules against litestream.io; the four storage calls Litestream makes against the 0.5.18 source.
- The GitHub OIDC claims used in the identity provider's condition against GitHub's OIDC reference, the permissions in `roles/artifactregistry.writer` and the storage roles against Google's role references, Cloud Storage soft delete and `gcloud storage restore` against their documentation, and Docker's `DOCKER-USER` chain and bridge options against Docker's.
- The cloud-init template renders to YAML that PyYAML (cloud-init's parser) loads, with the script and config files intact byte for byte, and `config.env` sets exactly the settings `host.sh` reads.
- `bash -n` on `files/host.sh`. The updater, the boot path and the firewall step against stand-in `docker`, `systemctl`, `iptables` and `rm` commands, in runs made while the script was written and not kept in this repository (what CI runs now is the rehearsal below): plain deployments, refusals, and the updater killed at each step of a deployment (after the record is written, during the health check, between the two steps of a promotion, at two points of a rollback), each followed by a run in the same boot and by a reboot. The stand-ins say what a real Docker or systemd would do; they are not proof of it.
- The identity provider's condition, translated operator for operator and evaluated against sample tokens (pushes, both kinds of pull request, manual runs, other workflow files, a reused repository name). That checks the logic as written, not Google's evaluator.
- `docker compose config` on the self-hosted files (`deploy/README.md`).
- The replica alert (2026-11-15): its arguments against the google provider 8.11.0 schema and docs (`~> 8.10` resolved to 8.11.0 that day), Litestream's log levels against the 0.5.18 source, and its filter, read from `monitoring.tf`, against the lines Litestream printed in the stack rehearsal while its replica was refused (`scripts/rehearse_stack.py`, check `alerts_when_the_replica_refuses_writes`).
- The replica check and the silence alert (2026-11-16): the metric's and the policy's arguments against the google provider 8.11.0 schema (`tofu validate`), the absence condition and the rate limit against Google's alerting documentation, as a fetch tool summarized it (not read word for word); on a laptop with the pinned Litestream image, that `ltx -json` lists the replica and not the local copy, stamps a file compacted to level 1 with its transaction's time, works on a database mounted read-only, that a write with Litestream stopped moves the database's file time while the replica's newest stays and a Litestream started again copies it, that a Litestream that stops last moves the file's time with nothing to copy (hence the container's start in the rule), and that a line written into `/proc/1/fd/1` from `docker exec` shows in `docker logs` of a container that runs as uid 10001. With the replica refused, the same image showed Litestream's retry back-off: 10 seconds after the first failed upload, then twice as long after each failure, up to 5 minutes (after a 12-minute refusal it copied 5 minutes after the replica was writable again, after 80 seconds of refusal, 3 and 73 seconds after in two runs), each time with `msg="replica sync recovered"` and no restart. `host.sh replica-check` itself runs in the test suite against stand-ins (`tests/unit/test_replica_check_unit.py`), and its two lines are evaluated there against both filters read from `monitoring.tf`.
- The compaction alert (2026-11-17): the metric's and the policy's arguments against the google provider 8.11.0 schema (`tofu validate`); the sliding alignment period, its 25-hour limit and `evaluation_missing_data` (ignored when `duration` is zero, hence one minute) against Google's alerting documentation, as a fetch tool quoted it (not read word for word). Its filter, read from `monitoring.tf`, is evaluated in the test suite on the line `serve` itself writes for a compaction that did not finish, and not on a finished or failed one (`tests/unit/test_infra_tf_unit.py`).
- `host.sh compact` (2026-11-17): against stand-ins in the test suite (`tests/unit/test_host_compact_unit.py`): the order of its steps, the two refusals before anything is stopped, the trap on INT, TERM and HUP, a compaction or a start that fails, the bounded wait, and that every line [After a workspace is deleted](#after-a-workspace-is-deleted) quotes is one it prints. Its sequence through Compose, on a laptop, three times ([docs/compaction-beside-a-replica.md](../../docs/compaction-beside-a-replica.md#the-sequence-run)). The host rehearsal's check of it was written that day and had not run when this was written; nothing of it has run on the VM.
- A structural pass over the `.tf` files: balanced blocks, every variable, local and resource reference resolves, and the template gets exactly the values it uses.

**Rehearsed on a CI runner** (job `host` in `.github/workflows/ci-cd.yml`, `scripts/rehearse_host.py`). On an Ubuntu runner (x64 or ARM64), with real Docker, systemd and iptables, the stack and host jobs build and run the `linux/amd64` cairn image, using QEMU when the runner is ARM64. Under that emulation the guest kernel remains ARM64; this does not validate an AMD64 kernel or Container-Optimized OS. In the host job on an ARM64 runner only cairn's image is `linux/amd64`: Caddy and Litestream, started by the units, run native. The script renders `cloud-init.yaml.tftpl` (and compares the result with OpenTofu's `templatefile`), installs the files its `write_files` lists, and starts the units as `runcmd` does. It checks that the disk step formats a blank disk and refuses, unchanged, one that is not provably blank; that after `network` a container on cairn's network or Docker's default bridge cannot connect to `169.254.169.254` while one on the backup network can (in CI the target is a stand-in, `scripts/ci_metadata_fixture.py`, that answers at that address from a network namespace of its own, and a run that fell back to the weaker check fails; run by hand without it, on a machine where nothing answers at that address, only the rules themselves are checked); that the first deployment comes from the updater and cairn then serves through Caddy with HSTS, the right `--trusted-proxy` and a working key over REST and MCP; that a missing database is restored before cairn starts; and that the updater follows the tag, rolls back an image that never becomes healthy, settles a deployment an earlier boot left, and obeys hold, pin and release. It also runs this guide's restore commands, read from this file at run time and run unchanged as root: the block of [The database is lost or damaged](#the-database-is-lost-or-damaged) from a quiet state, from a hold that cut a deployment short (with the pin from its comment) and after an automatic rollback, each time finding a note written before the loss and checking what `release` and `release --force` do against the paragraph under the block; and the commands of [Restore to a point in time](#restore-to-a-point-in-time) (`host.sh litestream ltx`, the dry run, the restore with `-timestamp`), which bring back a note written before the time and not one written after. It checks that an updater killed inside a health check is settled by the next run in the same boot, that without a plans file cairn runs with no `--plans`, that with one rendered into the template cairn runs with it mounted read-only at `/etc/cairn/plans.yaml`, reads the bytes given as its own user and refuses a project past the plan's cap, that with a brand name holding a space, an alt text and a PNG logo rendered into the template cairn runs with each as one argument and reads the logo as its own user, that cairn and Caddy do not start when the metadata rules cannot be put back, and that a first deployment that fails on a blank disk leaves no proven image and `cairn.service` skipped. It runs the replica check with a bound of one minute: current while Litestream copies, in the `litestream` container's log; current for a database nothing writes to, past the bound; with the replica's directories read-only, current within the bound and then an `Error:` line in Litestream's log that the replica alert's filter matches, and once they are writable again, current after Litestream retries by itself, with no restart; with Litestream stopped, an `Error:` line in the journal only and a failed run; and current again once Litestream copies. It runs the command of [After a workspace is deleted](#after-a-workspace-is-deleted) as written, after a workspace is deleted: the log file 0 bytes after it, the deleted workspace's words in neither file, Litestream running and the replica current, a restore from the replica without those words; and, sent SIGTERM right after it stopped Litestream, Litestream running again. The stand-ins are a loop device for the data disk, a local registry for Artifact Registry, a directory for the bucket, a stub `docker-credential-gcr` and a fixed env file in place of `host.sh env`; after the point-in-time restore, moving the old replica directory away stands in for a new `replica_path` and a VM restart. It does not test Container-Optimized OS itself, cloud-init applying the file, Secret Manager, Cloud Storage (so neither soft delete nor the replica path of [The data disk is lost](#the-data-disk-is-lost)), disk snapshots, Artifact Registry authentication, the guest-attribute report, a real reboot, a Docker restart or a real certificate; the script's docstring has the full list.

**Run once on a real project** (2026-11-14: one `e2-small` VM in `europe-west1`, with the settings of [docs/pre-release.md](../../docs/pre-release.md)). What that one run showed, and nothing more:

- `tofu apply` created the 28 resources besides the address reserved first. It stopped once, at the replica bucket's IAM binding, seconds after it had created the custom role and the service account that binding names; a second `tofu apply` created the rest. If the first apply stops there, run it again.
- Container-Optimized OS booted the VM as written: cloud-init wrote the files, the disk step formatted the blank disk, the units started, and within two minutes `host.sh` reported `running=none healthy=no proven=none` through guest attributes.
- Caddy got a certificate for the domain, and http was redirected to https.
- Within about a minute of the first push to Artifact Registry, the updater ran the image by its digest, saw `/healthz` answer and reported the image proven.
- Litestream wrote to the replica bucket as the VM's service account, with the custom role.
- The containers' and the system's logs reached Cloud Logging.
- The replica it had written, copied from the bucket and restored on another machine with the pinned Litestream image, passed `scripts/restore_drill.py --db-file` (6 of 6 checks) and opened in cairn's store. The database held no note at that point. Later the same day, after a person had signed in through Google and an agent connected by URL had written a note, the replica was restored the same way and the drill found the note (`--min-notes 1`).
- The brand's three settings reached `serve`: the sign-in page showed the name and served the logo.

It did not show: the metadata block seen from inside a container, a rollback, a restore on the VM, a restart of Docker, the alert's email, or the deploy job from GitHub Actions (the image was pushed by hand, as [Deploy](#deploy) describes). The replica alert was added after that run and applied on 2026-11-15: one resource added, nothing else changed. Seen on the real project that day: its filter matched no entry of the last 24 hours, and the same filter with `INFO` in place of `ERROR` selected Litestream's own lines, so the log's name, the instance, the container's name and the regular expression read as the rehearsal assumed. Its email has not been seen. The replica check and the silence alert came after that and have not been applied: no current line has reached Cloud Logging, the metric has counted nothing, and the silence alert has never fired or sent an email. The compaction alert came after that and has not been applied: its metric has counted nothing and it has never fired. The audit configuration, the log bucket's retention and the other settings of [Audit, keys and network](#audit-keys-and-network) came after that too, two resources more, and have not been applied. The host alerts came after that too, one resource more (one block, four policies), and have not been applied.

Not checked, most likely to be wrong first:

1. `tofu plan` and `tofu apply` beyond that one run. CI runs `tofu fmt -check`, `tofu validate` and `tofu test`, which plans the brand's settings against a mock provider, so argument names and types have been through OpenTofu; one project has been planned and applied, with the one retry above.
2. The metadata block on a real VM ([above](#containers-and-the-metadata-server)): whether the rules take effect on COS, whether they are still there after a Docker restart (the updater puts them back within a minute if not), whether Litestream still gets credentials from its own bridge, and whether logs still reach Cloud Logging. If Litestream cannot authenticate, replication and the boot-time restore both stop, and cairn stays down by design.
3. The identity provider's condition against a real token: the exact form of `workflow_ref`, the `environment` claim being present for a job in an environment, and `repository_id` being a string. A wrong guess refuses the deploy; it does not let anyone in.
4. The updater against a real registry (the host rehearsal ran it against a local one: following the tag by digest, promoting, rolling back): that `docker pull` prints a `Digest: sha256:...` line for an Artifact Registry tag, what it prints when the tag does not exist yet (the updater expects "not found" or "manifest unknown" and otherwise logs an error every minute until the first push), and that `docker run` by digest works after a pull by digest.
5. The first boot on COS (the host rehearsal ran the disk step and the units in their order on an Ubuntu runner, not on COS): `blkid`, `mkfs.ext4`, `fsck.ext4`, `resize2fs` and `iptables` being present, unit ordering, the two alternative `ConditionPathExists=|` lines on `cairn.service`, and the `cairnhost` home directory for the registry login. If `blkid` is missing, the disk step stops without formatting anything.
6. Litestream at run time: Application Default Credentials from inside a container, the four-permission custom role being enough (if it logs a 403, the log names the missing permission; `roles/storage.objectUser` on the bucket is the wider fallback), behaviour after a restore, and the real spacing of restore points.
7. Recovery from soft delete: the bulk restore command, and Litestream restoring from a path that had files brought back.
8. Caddy at run time: that the filter also covers error entries in the default logger, and the certificate request.
9. The uptime alert's filter and aggregation, taken from Google's documented example.
10. The deploy job's rarer paths. It has run since 2026-11-15, on every push to `main` that ships an image, and deployed each time. Not seen failing, and most likely to break first: `skopeo` on the runner reading the Docker logins and copying an OCI index with attestation manifests into Artifact Registry; `compute.instances.getGuestAttributes` being allowed in a custom role and in an instance-level binding; `host.sh` writing guest attributes on COS; and `check-head` reading the branch through the API with the job's `GITHUB_TOKEN` (`contents: read`). `scripts/deploy_production.sh` and the status path of `host.sh` have run only against stand-in `skopeo`, `curl` and `gcloud` commands, in the test suite (`tests/unit/test_deploy_production_unit.py`, `test_deploy_status_contract_unit.py`): never against the real registry, GitHub or guest attributes (success, rollback with the earlier image healthy, rollback with nothing healthy, a digest the updater skips, stale reports, no status at all, timeout, a copy whose digest changes, a run that is not the head of `main`).
11. The replica alert on Google Cloud: that the `Error:` line of a Litestream that exits at start reaches Cloud Logging, and its email. On the real project its filter has matched entries only with `INFO` in place of `ERROR`: no error line has occurred there.
12. The replica check on Google Cloud: that a line written into the container's `/proc/1/fd/1` reaches Cloud Logging under the container's name on Container-Optimized OS, as Litestream's own lines do; `ltx` listing a bucket from the check's container, and what that costs; the log-based metric counting the lines; the absence condition firing after 20 minutes and closing when a line returns, and its email. The check judges by file times and the replica's timestamps, which come from the same clock; it trusts that Litestream makes a file within ten seconds of a write while it copies (seen on a laptop, not on the VM).
13. The compaction alert on Google Cloud: that cairn's line reaches Cloud Logging as one entry with the line in `jsonPayload.message`, as Litestream's do; the metric counting it; what a log-based counter reports for an hour with no matching line, and so whether the incident closes as written; the alert firing on the third line, and its email. The `gcloud artifacts docker images list` command of the runbook has not been run.
14. The host check on Google Cloud: that Docker on Container-Optimized OS (cgroup v2) reports an `oom` event when a container reaches its memory limit; `docker events --since` still holding five minutes of events; `NRestarts` counting `Restart=always` restarts as written; `df` on `/var/lib/docker` naming the boot disk's stateful partition; that a line written into Caddy's `/proc/1/fd/1` is accepted (Caddy's binary carries a file capability, which may make its process not dumpable); the four filters on real entries, and their emails. Caddy's error line was written here from its documented JSON form (`"level":"error"`, `"logger":"tls.renew"`), not seen.
15. The `litestream` container waiting inside for the database (`sh` as its process 1 until cairn's first start), and the silence alert arming on the first `replica: current, no database yet` line: read in Google's documentation, not seen.
16. The nightly compaction (`cairn-compact`) on the VM: it runs `host.sh compact`, which has not been run there either ([After a workspace is deleted](#after-a-workspace-is-deleted)).
