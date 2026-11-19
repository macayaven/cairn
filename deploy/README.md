# Self-hosting cairn with a domain

Two compose files for any Docker host that a domain points at. `compose.yaml` runs cairn and Caddy; `compose.backup.yaml`, merged over it, adds Litestream:

- **cairn**: the image from the top-level `Dockerfile`, database on a named volume.
- **Caddy** (2.11.9): gets and renews the certificate, redirects HTTP to HTTPS, sets HSTS, caps request bodies at 4 MiB, and keeps query strings and API keys out of its logs.
- **Litestream** (0.5.18), optional: replicates the database to object storage and restores it on a fresh host. It lives in the second file because backups need a bucket you may not have.

The same `Caddyfile` and `litestream.yml` run on the hosted Google Cloud VM (`infra/gcp`).

On one computer without a domain, a third file, `compose.local.yaml`, runs cairn alone on `http://127.0.0.1:8765`; the top-level README walks through it. Its `docker compose` commands for keys, plans, logs and updates are the ones below. It reads `CAIRN_IMAGE`, `CAIRN_EXTRA_ARGS` and `cairn.env` as `compose.yaml` does, and `CAIRN_PORT` in place of a domain. It uses the same project name, `cairn`, so both files keep the database on the same volume; run one of them at a time.

**Status:** `docker compose config` (Compose 5.1.2) accepts `compose.yaml` alone and merged with `compose.backup.yaml`, and the merged model shows cairn's dependency on the restore step as required. CI starts the stack on a runner and restores a lost database into it (`scripts/rehearse_stack.py`, below), with a directory in place of the bucket and Caddy's own certificate. A real domain, a real certificate and a real bucket are first met on your first `up`, so treat it as the test of those. That Compose refuses to start cairn after a failed restore is watched there too: with the replica unreadable to Litestream, `up -d` exits 1, cairn is created but never started, and the replica keeps every byte.

## Before you start

- Docker Engine with Compose 2.24 or later (`docker compose version`).
- A domain whose `A` record points at this host, and ports 80 and 443 reachable from the internet. Caddy needs both to get a certificate.

## Start

```bash
cd deploy
cat > .env <<'EOF'
CAIRN_DOMAIN=cairn.example.net
ACME_EMAIL=you@example.net
EOF
docker compose up -d
```

Without `CAIRN_IMAGE`, Compose builds the image from this checkout. To run a published image instead, add `CAIRN_IMAGE=ghcr.io/OWNER/REPO:TAG` to `.env`.

Then mint an API key and copy it out. The copy arrives with mode 0600:

```bash
docker compose exec cairn python -m cairn.server key-create --db /data/cairn.db --label laptop --out /data/laptop.env
docker compose cp cairn:/data/laptop.env ./laptop.env
docker compose exec cairn rm /data/laptop.env
```

Open `https://cairn.example.net`, sign in with that key, and read `/setup` to connect agents.

## Settings

| File | Read by | Contents |
|---|---|---|
| `deploy/.env` | Compose | `CAIRN_DOMAIN` and `ACME_EMAIL` (required); `CAIRN_IMAGE`; `CAIRN_EXTRA_ARGS`, extra arguments for `serve` such as `--allowed-host other.example.net` or `--plans /data/plans.yaml` (Plans, below); `CAIRN_SUBNET`, the private subnet of the stack's network (`10.213.47.0/24` unless set; cairn trusts it to name the client, so set another only when that one is taken on the host); `COMPOSE_FILE`, which turns backups on (below) |
| `deploy/cairn.env` | the cairn container, if the file exists | `KEY=value` lines for cairn's own environment |
| `deploy/litestream.env` | the Litestream containers; required once backups are on | `LITESTREAM_REPLICA_URL` and storage credentials |

All three match `*.env` in `.gitignore`. Keep them out of git.

Say who runs this install, where, and how to reach you: write it as a Markdown file onto the volume, as cairn's own user (`docker compose exec -T cairn sh -c 'cat > /data/operator-notes.md' < operator-notes.md`; a file put there with `docker compose cp` keeps your own owner and mode, and cairn may not be able to read it), and add `--operator-notes /data/operator-notes.md` to `CAIRN_EXTRA_ARGS`; it becomes the last section of `/docs/privacy`, in place of the list for you to fill in (64 KiB at most; a file cairn cannot read stops `serve`).

After a workspace is deleted, `serve` compacts the database at most once every `--compact-every` hours (24 by default; `0` turns it off, then run `compact` yourself); each run rewrites the whole file and holds every write until it ends (milliseconds for a small database, roughly as long as copying the file for a large one; reads go on); the access log records how long each run took, `compact done in N ms`. A run starts only with twice the database's size free on its disk, a second copy and as much again as a margin; without it the access log says `compact not started: the database's disk has N bytes free, a rewrite needs N`, nothing changes, and the next poll, 10 minutes later, checks again. Beside the replica service the last step of a run, truncating the write-ahead log, cannot finish: that service reads the log for as long as it runs. The access log then says `compact not finished: another connection held the file (rewritten; the write-ahead log was not emptied) in N ms`; the database has been rewritten all the same, and what stays due is emptying the log alone. Such a run is tried again when the interval has passed, and once at the first poll after a restart, never every 10 minutes, and neither the next try nor a restart rewrites the file again: the try empties the log, or says `compact not finished: another connection held the file (no rewrite: only the write-ahead log was due) in N ms` and holds writes for at most about 5 seconds, and once the log is emptied, `compact done (no rewrite: only the write-ahead log was due) in N ms`; a deletion after it starts a new rewrite. Until then what the deleted workspace left in the log file stays there; to remove it at once, stop the replica service for the seconds a compaction takes (Backups, "After a workspace is deleted", below).

`serve` tidies pages by default, with no model: a moment after a note arrives on a page, cairn puts each heading's claims together and folds the lines that repeat, as a new version, and the version before stays readable, unless a plan keeps only the last versions of each page (Plans, below): a tidy counts toward them like any other version, and the oldest goes as it would for a note. The run guide's "Tidy pages without a model" says more, and how to tidy the pages already there after an upgrade. Nothing leaves the container. To switch it off, add `--no-tidy` to `CAIRN_EXTRA_ARGS`.

One `serve` runs on a database, and cairn holds to it: a second `serve` on the same file (a scaled `cairn` service, a second stack on the same volume) stops at start with `another server is running on this database (/data/cairn.db): stop it before starting another`, exit code 2, and changes nothing. Never scale the `cairn` service: Compose would start the refused copy again and again. The other commands (`key-create`, `export`, `compact` and the rest) run beside the server as before.

The image's health check asks cairn's `/healthz` every 30 seconds. It answers 200 only while cairn can read its database and the database holds the schema this cairn knows, and 503 when the database is closed, cannot be read, holds another schema version, or cannot be used after a write that failed and could not be undone. While a compaction holds the database it answers 200: cairn is busy, not broken. Once one write has held the database for more than a minute (an hour while a compaction runs), it answers 503 with `{"status":"stuck"}`: reads may still work, but no write can be made, and cairn does not get out of it by itself. Restart it with `docker compose restart cairn`: the write under way is lost, nothing committed is. It answers 200 again as soon as that write lets go. After 3 failed checks in a row `docker compose ps` shows cairn as unhealthy. Compose does not restart it for that (it restarts cairn when cairn exits). After a write that could not be undone, `docker compose logs cairn` says so (`a rollback failed: the store is unusable until it is opened again`), and `docker compose restart cairn` brings it back.

### Compose limits

| Service | Memory | Processes | Memory variable | Process variable |
|---|---:|---:|---|---|
| cairn | 1024 MiB | 512 | `CAIRN_MEMORY_MB` | `CAIRN_PIDS_LIMIT` |
| Caddy | 256 MiB | 256 | `CADDY_MEMORY_MB` | `CADDY_PIDS_LIMIT` |
| Litestream | 256 MiB | 256 | `LITESTREAM_MEMORY_MB` | `LITESTREAM_PIDS_LIMIT` |
| Litestream restore | 256 MiB | 256 | `LITESTREAM_RESTORE_MEMORY_MB` | `LITESTREAM_RESTORE_PIDS_LIMIT` |
| Data initializer | 64 MiB | 32 | `CAIRN_DATA_INIT_MEMORY_MB` | `CAIRN_DATA_INIT_PIDS_LIMIT` |

Set overrides in `deploy/.env` as whole numbers. Memory limits are in MiB and include swap. A compaction or a large import can raise cairn's memory use. If the kernel kills a process at the memory limit, Docker applies the container's restart policy. A failed initializer or restore keeps cairn from starting. At the process limit, the kernel refuses new processes or threads.

### Only the people you name

Sign-in through GitHub, Google or your own provider is turned on with `--sign-in` in `CAIRN_EXTRA_ARGS` and the provider's `CAIRN_<PROVIDER>_CLIENT_ID` and `_CLIENT_SECRET` in `deploy/cairn.env` (the main README's "Sign in with GitHub, Google or your own identity provider"). Anyone with an account there can then sign in. To let in only the people you name, a private pre-release say, list their addresses in `deploy/cairn.env`, separated by commas, and run `docker compose up -d`:

```
CAIRN_ALLOWED_EMAILS=ada@example.test,brook@example.test
```

A person signs in only when the email their provider has verified is one of these: the whole address, ignoring case. `ada+notes@example.test` is another address, and so is a second address on the same account. There is no command-line option for the list, so no address lands in `CAIRN_EXTRA_ARGS` or in a process list, and `serve` prints none. If the variable is set but blank, holds an entry that is not one address, or is set without `--sign-in`, `serve` stops at start (exit 2) and names the variable; `docker compose logs cairn` says why.

- It is checked on every request, not only at sign-in. Once `serve` runs with someone taken off the list (`docker compose up -d` restarts it with the new list), their open sessions, the keys they made and the agents they approved are refused as ended ones; nothing is deleted, so putting them back on the list restores them. A key or an agent is checked by the address the person first signed in with. `docker compose exec cairn python -m cairn.server session-end --db /data/cairn.db --email ada@example.test` ends every session of that address in every workspace and prints how many; end their keys and agents on the **Keys** and **Agents** pages. To shut someone out for good, also remove them as a member.
- With `--join personal` (the default), every listed person gets a workspace of their own. With `--join default` they share one, and the list is enough of a limit for a shared workspace behind GitHub, Google or your own provider.
- With `--allowed-email-domain` as well, either admits: people at those domains as before, and the listed people from anywhere. `CAIRN_OIDC_ALLOWED_GROUPS` is not an "either": your provider's groups are checked first, so a listed person outside them is still refused.
- An invitation does not get round it: an invited person who is not listed is refused, and the invitation stays open.

To show the people you name your organisation's name in place of cairn's, with nothing but the sign-in page for anyone signed out, add the run guide's five options ("Under your own name, behind the sign-in") to `CAIRN_EXTRA_ARGS`. Put quotes round a name with a space in it; Compose keeps it one argument. A logo goes on the volume as cairn's own user, like the operator notes above (`docker compose exec -T cairn sh -c 'cat > /data/logo.svg' < logo.svg`), then:

```
CAIRN_EXTRA_ARGS=--sign-in github --wall --brand-name "Knowledge Base" --brand-logo /data/logo.svg --session-days 30
```

Run `docker compose up -d` after each change to `deploy/.env`. cairn reads the logo once, at start, so a new logo under the same file name needs `docker compose restart cairn`.

## Plans

A self-hosted cairn caps nothing. To cap each workspace by a plan, give `serve` a plans file:

1. Copy `deploy/plans.example.yaml` to `deploy/plans.yaml` and edit it. The caps in the example are placeholders, so set your own. A plan with `storage_bytes` must name `notes_per_month` too, since every note a workspace holds takes some space whatever its size and only that cap bounds how fast it grows; `serve` refuses a file with one that does not.
2. Put it in the data volume, written by cairn's own user so that cairn can read it: `docker compose exec -T cairn sh -c 'cat > /data/plans.yaml' < plans.yaml`. (`docker compose cp` keeps your file's owner and mode, and a file only you may read is one cairn cannot.)
3. In `deploy/.env`, add `--plans /data/plans.yaml` to `CAIRN_EXTRA_ARGS`, then run `docker compose up -d`. If `serve` cannot read or use the file, it stops at start (exit 2), and `docker compose logs cairn` says why.

Every workspace is on the file's `default` plan until you move it: `docker compose exec cairn python -m cairn.server plan-set --db /data/cairn.db --account ACCOUNT --plan NAME` (`--default` moves it back). The default workspace's id is `acct_default`, and one made when someone signs in gets `acct_` and 12 hex characters that nobody chose: `docker compose exec cairn python -m cairn.server workspace-list --db /data/cairn.db` prints every workspace's id with its name, how many people belong to it and the plan it is on now, as JSON. `usage --db /data/cairn.db --account ACCOUNT` prints the workspace's month as JSON. `serve` reads the file when it starts, so after an edit, copy the file again and run `docker compose restart cairn`. `source-sync` on the command line takes the same `--plans /data/plans.yaml`; without it, it caps nothing and says so.

## Sources

A source is a system the server reads notes from on its own: Langfuse, Granola, Google Meet, Google Drive, Google Calendar, Gmail, Linear, GitHub or a connector of your own. Its keys are variables of the server's environment, and its config names them: put a line such as `GRANOLA_API_KEY=...` in `deploy/cairn.env`, run `docker compose up -d`, and give the source `{"key_env": "GRANOLA_API_KEY"}`. You add or remove a source, and only you: `docker compose exec cairn python -m cairn.server source-add --db /data/cairn.db --name NAME --kind KIND --config JSON` (`source-add --help` lists each kind's settings), and `source-remove --name NAME`. Each acts on the default workspace unless `--workspace ACCOUNT` names another: with `--join personal` name the person's own, whose id `workspace-list` prints. What happens after is the workspace's decision: an owner or an admin switches it on or off under **Workspace**, then **Sources**, and chooses the projects it writes into. A Langfuse source is on as soon as it is added; a Granola source starts off and reads nothing until someone switches it on, and then only notes made from then on. Langfuse reads a trace marked while it was off once it is on again. A Google Meet source starts off too, and then reads only meetings that end from then on; its variable holds a Google credential's JSON, on one line between single quotes in `deploy/cairn.env`. A Google Drive source starts off too, and then reads the documents put into its one folder from then on, each once; its variable holds a Google credential in the same way. A Google Calendar source starts off too, and then reads only events that end from then on; its variable holds a Google credential's JSON in the same way. A Gmail source starts off too, and then reads only the messages that arrive from then on and that a person puts its one label on, each once; its variable holds a Google credential's JSON in the same way. `source-enable --name NAME --project PROJECT` and `source-disable --name NAME` do the same on the command line, and `source-list` shows which are on. `serve --sync-every SECONDS` reads every source that is on in the background.

## Backups

Create `deploy/litestream.env`:

```bash
LITESTREAM_REPLICA_URL=s3://my-bucket/cairn
LITESTREAM_ACCESS_KEY_ID=...
LITESTREAM_SECRET_ACCESS_KEY=...
```

Then turn the second compose file on in `deploy/.env` and start:

```bash
echo 'COMPOSE_FILE=compose.yaml:compose.backup.yaml' >> .env
docker compose up -d
docker compose ps      # litestream must stay "running"
```

With that line, every `docker compose` command in this directory uses both files, so no command can leave the backup half out by accident. (On Windows the separator is `;`.) The same thing without the `.env` line is `docker compose -f compose.yaml -f compose.backup.yaml up -d`, and then every later command needs both `-f` flags too.

What the second file adds:

- `litestream` uploads changes every 10 seconds. A restore brings back the latest state, or an earlier point in the last seven days. An earlier point is not any instant: it is the end of a file Litestream still has, which means 10 seconds apart for the last five minutes and 30 seconds apart before that, and only where there were writes (`litestream.yml` has the settings). `restore -dry-run -timestamp ...` shows where a restore would land.
- `litestream-restore` runs before cairn on every `docker compose up`. If the volume has no database and the replica has one, it restores it.
- cairn **requires** that step. If the restore fails, `docker compose up` stops with an error and cairn is not started, so an empty database never overwrites a good replica. Fix the cause (credentials, network, bucket) and run `up` again.
- `litestream.env` becomes required: without it Compose refuses to start anything.

Two things this does not cover. The check belongs to `docker compose up`: `up --no-deps cairn` and a bare `docker start` skip it, so do not use them on a host whose volume may be empty. And a command that names only `compose.yaml` runs cairn with no Litestream at all, which is why the `.env` line is the way to turn backups on.

Other storage:

- **S3-compatible** (Backblaze B2, MinIO and the like): `s3://bucket/cairn?endpoint=HOST`.
- **Google Cloud Storage**: `gs://bucket/cairn` (the scheme is `gs`, not `gcs`). Outside Google Cloud, mount a service-account key with a third file, and make the key readable by uid 10001:

  ```yaml
  # deploy/compose.gcs.yaml
  services:
    litestream: &gcs
      volumes:
        - ./gcs-key.json:/gcs-key.json:ro
      environment:
        GOOGLE_APPLICATION_CREDENTIALS: /gcs-key.json
    litestream-restore: *gcs
  ```

  and list it: `COMPOSE_FILE=compose.yaml:compose.backup.yaml:compose.gcs.yaml`. Do not call it `compose.override.yaml`: Compose reads that name only when no file list is given, so with `COMPOSE_FILE` set it would be ignored without a word.

Litestream 0.5 dropped client-side (age) encryption. Use the provider's server-side encryption.

### Watch the replica

Litestream says when it cannot replicate, in its own log: `docker compose logs litestream`. A line with `level=ERROR` means a step failed (`msg="sync error"`, `msg="monitor error"`, `msg="compaction failed"`, `msg="l0 retention enforcement failed"`), and its `error=` part says why: a refused bucket, a credential that no longer works, a full disk. Litestream keeps trying, and logs `msg="sync recovered"` once an upload that had failed goes through again. A line that starts with `Error:` means it could not start at all. A healthy Litestream logs at `INFO` only.

Nothing on this host sends you those lines. On the hosted VM an email goes out when one arrives, and another when a check of the replica's age has not found it current for 20 minutes ([infra/gcp/README.md, "Alerts"](../infra/gcp/README.md#alerts)); here, read the log or send it to an alerting tool of your own. Silence is not proof: an idle database writes nothing to the replica, and a Litestream that stops without a word logs nothing. Only a restore drill (below) shows that a replica restores.

### After a workspace is deleted

Beside a running `litestream` a compaction rewrites the database but cannot truncate the write-ahead log, so what a deleted workspace left in the log file stays there until later writes reach it; on a laptop that was 5 to more than 8 minutes, with no bound found ([docs/compaction-beside-a-replica.md](../docs/compaction-beside-a-replica.md)). To remove it at once, run these four commands in `deploy/`:

```bash
docker compose stop litestream
docker compose exec cairn python -m cairn.server compact --db /data/cairn.db
docker compose start litestream
sleep 15; docker compose exec litestream litestream ltx -level 0 /data/cairn.db | tail -n 1
```

`compact` says how many old page versions it removed and the database's size before and after, and exits 1 when it could not finish; the deletion then stays recorded as due, and running the four again is safe. Start `litestream` again whatever `compact` did: while it is stopped nothing reaches the replica, and cairn takes writes as usual. The last command prints the replica's newest file: its last column, the time Litestream made it, must be later than your `start`. An earlier time means Litestream has not copied yet; wait a few seconds and run that command again, and if it stays so, `docker compose logs litestream` says why. A write that arrives while `litestream` is stopped is copied once it starts.

### Check that a restore works

A backup you have not restored is a guess. From the repository root, with the same credentials in your environment:

```bash
python3 scripts/restore_drill.py --replica s3://my-bucket/cairn --docker
```

It restores the latest replica into a temporary directory, runs `PRAGMA integrity_check`, and prints the schema version, row counts and the newest note's time. It never touches the running database.

### Rehearse the whole stack

`scripts/rehearse_stack.py` runs these compose files as they ship, on one machine, with Litestream writing to a directory instead of a bucket, and checks from outside what the unit tests can only check against stand-ins. CI runs it on every pull request that is ready for review, and on `main`, unless the change touches only paths on `NO_IMAGE_WORK` in `scripts/ci_changes.py`. To run it yourself, on a machine that is not serving cairn (it publishes ports 80 and 443, writes `deploy/.env`, `deploy/litestream.env`, `deploy/laptop.env` and `deploy/plans.yaml`, and refuses to start when any of those or `deploy/cairn.env` exists):

```bash
docker build -t cairn:local . && python3 scripts/rehearse_stack.py cairn:local
```

It checks that Caddy serves cairn over HTTPS and sets HSTS, that Caddy's config holds the 4 MiB body cap, that the network cairn trusts is the one Compose made, that a key works through the proxy over REST and MCP, that cairn counts each client under its own address, that neither log holds a key, an invitation token or a query, that `docker compose config` refuses to go on without `CAIRN_DOMAIN`, `ACME_EMAIL` or, with backups on, `litestream.env`, and that a note survives losing the database. It refuses Litestream's replica while cairn takes a write, and checks that the replica alert's condition, read from `infra/gcp/monitoring.tf`, matches a line Litestream prints and none it printed while healthy, and that replication resumes once the replica is readable again. Then the database is lost: the volume is removed, Litestream restores it, cairn starts on it and the restore drill passes. Then it makes the restore fail (the replica is there but Litestream may not read it) and checks that `up -d` says so, cairn is not started and the replica is unchanged. It writes `deploy/.env` as this guide does and starts the stack with a plain `docker compose up -d`, and runs this guide's "Restore by hand" commands and its `docker compose` copies of the key and the plans file as they are written here. It runs this guide's "After a workspace is deleted" commands as written after a workspace is deleted, and checks that right after `compact` the log file is empty and the deleted workspace's words are in neither file, that a note written while `litestream` is stopped is copied once it starts, and that a database restored from the replica holds the other notes and none of those words. It removes everything it made.

It does not check a real certificate (the name is `localhost`), a bucket and its credentials, Caddy's body cap at work (cairn refuses the same size first), or `restore_drill.py --replica` (its container cannot reach a replica that is a directory on a volume).

### Restore by hand

On a fresh host this is automatic: configure `.env` (with the `COMPOSE_FILE` line) and `litestream.env`, run `docker compose up -d`, and the database comes back before cairn starts. To replace a damaged database on a running host, move it aside and start again:

```bash
docker compose down
docker compose run --rm --no-deps --entrypoint sh data-init -c \
  'mkdir -p /data/aside && cd /data && mv cairn.db cairn.db-wal cairn.db-shm .cairn.db-litestream aside/ 2>/dev/null; ls -la /data'
docker compose up -d
```

Litestream refuses to restore over an existing database, which is why the files are moved. Remove `aside/` once the restored service looks right.

## Update

Built from this checkout:

```bash
git pull
docker compose build cairn
docker compose up -d
```

Running a published image: change the tag in `CAIRN_IMAGE`, then

```bash
docker compose pull cairn
docker compose up -d
```

Both use the backup file whenever `.env` has the `COMPOSE_FILE` line.

Before you move to a new release, you can check that its image is the one this repository's workflow built, with the GitHub CLI (it reads the digest from the registry, so log in to `ghcr.io` first if it asks); a wheel or source archive from the release is checked the same way, by its path:

```bash
gh attestation verify oci://ghcr.io/<owner>/<repo>:X.Y.Z --repo <owner>/<repo> --signer-workflow <owner>/<repo>/.github/workflows/ci-cd.yml
gh attestation verify cairn_memory-X.Y.Z-py3-none-any.whl --repo <owner>/<repo> --signer-workflow <owner>/<repo>/.github/workflows/ci-cd.yml
```

A pass proves that these bytes are the ones this repository's `ci-cd.yml` built and attested in GitHub Actions, unchanged since; it does not prove that the code or its dependencies are free of flaws. `docker buildx imagetools inspect --raw ghcr.io/<owner>/<repo>:X.Y.Z` prints the image index, whose attestation manifests (platform `unknown/unknown`) hold each platform's build provenance and bill of materials; `docker buildx imagetools inspect ghcr.io/<owner>/<repo>:X.Y.Z --format '{{json .Provenance}}'`, and `'{{json .SBOM}}'`, print them. A tag `X.Y.Z-rc.N` is a release candidate, published to be tried before `X.Y.Z`: do not run one in production.

Once only, the first update to a `compose.yaml` that gives the network a fixed subnet: run `docker compose down`, then `docker compose up -d`, so that the network is made again with that subnet. `down` keeps the volumes, so the database and Caddy's certificates stay. Then check that `docker network inspect cairn_default -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}'` prints `10.213.47.0/24` (or your `CAIRN_SUBNET`): cairn trusts only that subnet to name the client, so on a network with another subnet every client shares one count in the limits on sign-in and agent registration.

The update to schema 18 counts the headings stored with each passage toward a workspace's passages, so an existing workspace's count rises and can pass its plan's cap; until some of its notes are removed or its plan changes, its new notes are refused.

An older image refuses to open a database with a newer schema. cairn exits at start, before it listens, with one line and no traceback, and leaves the database as it was; `docker compose logs cairn` shows it:

```text
SchemaTooNew: the database has schema version <found> and needs a cairn of schema version <floor> or later; this cairn knows up to <known>. Run the cairn that wrote it or a newer one, or restore the database from before the upgrade.
```

Docker starts the container again (`restart: unless-stopped`), and each start ends with the same line, until the image or the database is put right. From schema 31 on, a database records the oldest schema version whose image runs on it. An update whose schema steps only add (tables, columns with defaults, indexes) leaves that number, so the image before it still runs on the updated database: going back to it needs no restore. An update with a step that changes what an older image relies on raises the number to itself, the older image refuses the database with the line above, which names the version it needs, and going back means restoring a database from before the change. The release's notes say which kind each update is. Schema 31 (release 0.1.0) is of the second kind.

## The hosted service: first deploy

The hosted VM (`infra/gcp`) runs these same `Caddyfile` and `litestream.yml`, and CI deploys to it: a push to `main` copies the image it just published, by digest, into the Google Cloud project's registry, moves the `production` tag to it and waits until the VM reports that digest running and healthy. Once, in order, from `infra/gcp` in a checkout (the commands, and how to check each step, are in [infra/gcp/README.md, "First deploy"](../infra/gcp/README.md#first-deploy-once-in-order)):

1. **Project and billing.** `gcloud projects create PROJECT_ID`, then `gcloud billing projects link PROJECT_ID --billing-account=BILLING_ACCOUNT_ID`. Check: `gcloud billing projects describe PROJECT_ID --format='value(billingEnabled)'` prints `True`.
2. **Infrastructure.** Bootstrap steps 1 to 7 of `infra/gcp/README.md`: APIs, state bucket, `terraform.tfvars`, address and DNS, `tofu apply`, the env secret's first version. Check: `tofu plan` reports no changes.
3. **The VM reports.** `gcloud compute instances get-guest-attributes "$(tofu output -raw instance_name)" --zone "$(tofu output -raw instance_zone)" --query-path=cairn/status` prints a line with `running=none proven=none`.
4. **The `production` environment**, with a required reviewer and limited to `main`: the two `gh api` calls in `infra/gcp/README.md`, step 4. Check: `gh api repos/OWNER/REPO/environments/production --jq '[.protection_rules[].type]'` lists `required_reviewers` and `branch_policy`.
5. **Settings**, piped so no value is printed:

   ```bash
   tofu output -raw project_id                   | gh secret set GCP_PROJECT_ID --env production
   tofu output -raw workload_identity_provider   | gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER --env production
   tofu output -raw deployer_service_account     | gh secret set GCP_DEPLOYER_SERVICE_ACCOUNT --env production
   tofu output -raw artifact_registry_repository | gh secret set GCP_ARTIFACT_REGISTRY_REPOSITORY --env production
   gh variable set GCP_INSTANCE --body "$(tofu output -raw instance_name)"
   gh variable set GCP_ZONE --body "$(tofu output -raw instance_zone)"
   gh variable set CAIRN_PUBLIC_URL --body "$(tofu output -raw url)"   # optional: also check /healthz from outside
   ```

   Check: `gh secret list --env production` shows four names and `gh variable list` three.
6. **Deploy.** Protect `main`, push to it, and approve the `deploy` job. Check: the job's log has `deploy: the VM runs sha256:... and /healthz answers` and then, with `CAIRN_PUBLIC_URL` set as in step 5, ends with `deploy: https://DOMAIN/healthz answers 200 over TLS`; `curl -fsS https://DOMAIN/healthz` answers.
7. **cairn tells clients apart.** Its access log names no address, so ask the sign-in limit (30 starts a minute per address): from one machine, `for i in $(seq 31); do curl -s -o /dev/null -w '%{http_code} ' https://DOMAIN/auth/PROVIDER; done` ends in `429`, while within that minute the same URL from another network (a phone off Wi-Fi) still answers `302`. If that answers `429` too, cairn sees the proxy's address for everyone and the trusted network is wrong.

Until the two variables of step 5 exist, the `deploy` job is skipped and CI stays green. Once they exist, missing secrets fail the job instead. Rollback, and what happens to the data when a migration already ran, are in [infra/gcp/README.md, "Rollback"](../infra/gcp/README.md#rollback).

## Logs

`docker compose logs caddy` shows one JSON line per request. Caddy's logs carry no query strings (they are replaced with `?redacted`), no invitation tokens (`/join/redacted`) and no `x-api-key`, `Referer` or `Location` headers; cookies and `Authorization` are redacted by Caddy itself.

`docker compose logs cairn` shows cairn's own line per request: who called, what, which project and the status. It holds no request line, so no query string and no invitation link: `serve` starts uvicorn with its access log off. Docker keeps at most 50 MB of logs per service.

## HTTP/3

Off. The `Caddyfile` limits Caddy to HTTP/1.1 and HTTP/2 because only TCP ports are published. To turn it on, remove the `servers` block from the `Caddyfile`, add `- "443:443/udp"` to Caddy's ports, and open UDP 443 in your firewall.
