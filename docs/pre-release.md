# A private pre-release of the hosted service

The hosted service on its own domain, with sign-in and plans switched on, open only to the people the operator names. The operator puts each workspace on its plan. It is the public deployment with one difference: one line in the environment says who may sign in. Opening it later means deleting that line.

Written 2026-11-13. [What is checked and what is not](#what-is-checked-and-what-is-not) is at the end.

## What the operator brings

| What | Used for | Where it goes |
|---|---|---|
| A cloud project with billing, and a domain | the VM, its disk, the replica bucket, TLS | `infra/gcp/terraform.tfvars` ([infra/gcp/README.md](../infra/gcp/README.md)) |
| `gcloud`, OpenTofu and Docker on the machine that deploys | the bootstrap, and pushing the image by hand while the repository is private | that machine |
| An OAuth app at GitHub (or Google), callback `https://DOMAIN/auth/github/callback` | sign-in | `CAIRN_GITHUB_CLIENT_ID`, `CAIRN_GITHUB_CLIENT_SECRET` in the env secret |
| The addresses that may sign in | the gate | `CAIRN_ALLOWED_EMAILS` in the env secret |
| A plans file | the caps each workspace is held to | `cairn_plans_file` in `terraform.tfvars` |

None of the secret values belongs in the repository, in `terraform.tfvars`, in `cairn_extra_args` or on a command line. They go in the env secret, which the VM reads at start ([infra/gcp/README.md](../infra/gcp/README.md), bootstrap step 7 and "Secret rotation").

## Settings

`infra/gcp/terraform.tfvars`:

```hcl
cairn_plans_file = "../../deploy/plans.yaml"
cairn_extra_args = "--sign-in github --join personal"
```

The env secret (`cairn.env`), names only:

```
CAIRN_GITHUB_CLIENT_ID=...
CAIRN_GITHUB_CLIENT_SECRET=...
CAIRN_ALLOWED_EMAILS=you@your.domain,a.tester@their.domain
```

The plans file: copy `deploy/plans.example.yaml` to `deploy/plans.yaml`, set the caps and keep `free` as the default. `cairn_plans_file` names it relative to `infra/gcp`; the VM gives `serve` its `--plans`, so `cairn_extra_args` has none.

## In order

1. **Deploy without sign-in first**, with `cairn_extra_args` empty and a one-line env secret. There are two ways to get the image onto the VM:
   - **By hand**, which works while the repository is private on any GitHub plan: the bootstrap in [infra/gcp/README.md](../infra/gcp/README.md), then its "Deploy" section (`docker buildx build ... --push` from a machine with Docker and `gcloud`). The VM picks the image up within a minute.
   - **From CI**, as "The hosted service: first deploy" in [deploy/README.md](../deploy/README.md) describes. The deploy job needs the `production` environment's secrets, and GitHub Free has none on a private repository: the job stops at "Check the settings". It needs a public repository or a plan that has them.

   Check: `curl -fsS https://DOMAIN/healthz` answers.
2. **Write the plans file and name it in `terraform.tfvars`** as `cairn_plans_file` (the settings above). Nothing is copied to the VM by hand: `tofu apply` puts the file in the VM's user-data, and at boot cloud-init installs it at `/etc/cairn/plans.yaml` and cairn starts with `--plans /etc/cairn/plans.yaml` ([infra/gcp/README.md, "Changing the configuration"](../infra/gcp/README.md#changing-the-configuration)). Check: `tofu plan` reports no error. It refuses a file that is missing, empty, larger than 16 KiB or not plain ASCII text, and `--plans` in `cairn_extra_args` beside it. It does not read the file as plans: one that `serve` cannot use still stops `serve` at start, and step 4's check finds that. The file reaches the VM with step 4's apply and restart.
3. **Register the OAuth app** with the callback above.
4. **Switch it on in one restart.** `serve` stops at start when a setting names something that is not there: the list without `--sign-in`, `--sign-in` without the client id. So put everything in place first, then restart once:
   1. Add a version of the env secret with the lines above (`gcloud secrets versions add`, as "Secret rotation" in the infrastructure guide shows) and do not restart yet: the VM reads the secret at boot.
   2. Set `cairn_plans_file` and `cairn_extra_args` in `terraform.tfvars` and run `tofu apply`.
   3. Stop and start the VM, as "Changing the configuration" says. At boot it reads the new arguments and the latest secret together.

   Check: `https://DOMAIN/login` shows one button and no key form. If the site answers 502 instead, `serve` refused a setting: systemd starts it again every five seconds, and `sudo journalctl -u cairn` on the VM has the line that names the variable or the file (it never prints a value).
5. **Sign in** with a listed address. Check: you land on `/welcome` in a workspace of your own. From a private window, sign in with an account whose address is not listed. Check: the sign-in page says "This install admits only the people its operator has listed." and `workspace-list` (below) shows no workspace for it.
6. **Put the workspace on its plan** (next section). Check: its **Usage** page names the plan and its limits.
7. **Connect an agent** by URL: `claude mcp add --transport http cairn https://DOMAIN/mcp`, approve it in the browser, write a note, recall it. This is the one connect by hand the definition of done still waits on.

## A workspace's plan

A workspace is on the plan the operator set with `plan-set`, else on the plans file's default. Nothing else changes it. From a shell on the VM:

```bash
sudo docker exec cairn python -m cairn.server plan-set --db /data/cairn.db --account ACCOUNT --plan solo
```

The ids come from `sudo docker exec cairn python -m cairn.server workspace-list --db /data/cairn.db`, which prints every workspace with its id, its name and the plan it is held to. `plan-set --default` puts a workspace back on the default plan: its notes and projects stay, writes that would go over the default plan's caps are refused, and from its next change a page keeps only as many earlier versions as that plan allows.

## Sources on the pre-release

A source (Granola, Google Meet, Google Calendar, Gmail, Google Drive, Langfuse, Linear, GitHub or a connector of your own) is read by the server with a credential from its own environment ([the sources guide](../src/cairn/guides/sources.md) says what each reads and how to get its credential). On the hosted VM that takes three things, and none of them happens by itself:

1. **The credential goes in the env secret**, as one more `KEY=value` line, in a new version of the secret. The VM passes that file to the container as Docker reads an env file: the value is taken as it is, so write no quotes. A Google credential is its JSON on one line: `GOOGLE_CREDENTIAL={"type": "service_account", ...}`. (With Compose on your own machine the same line goes in `deploy/cairn.env` between single quotes, as `deploy/README.md` says: Compose reads quotes, Docker's `--env-file` does not.) The VM reads the secret at boot, so restart it after adding the version.
2. **The server syncs in the background only when told to.** Add `--sync-every 300` to `cairn_extra_args` and run `tofu apply`. Without it a source is read only when you run `source-sync` by hand, and nothing arrives by itself.
3. **You add the source on the VM**, naming the variable, never the credential, and the workspace it is for. With `--join personal` each person has a workspace of their own, and a source added without `--workspace` goes to the default workspace, not to theirs. `workspace-list` prints the ids (it prints no address):

```bash
sudo docker exec cairn python -m cairn.server workspace-list --db /data/cairn.db
```

```bash
sudo docker exec cairn python -m cairn.server source-add --db /data/cairn.db --workspace ACCOUNT --name meetings --kind granola --config '{"key_env": "GRANOLA_API_KEY"}'
```

Then the workspace decides: an owner or an admin opens **Workspace**, then **Sources**, and switches it on. Every kind but Langfuse starts off and reads only what happens from then on. `sudo docker exec cairn python -m cairn.server source-list --db /data/cairn.db --workspace ACCOUNT` shows each of that workspace's sources, on or off, and its last run or the reason it failed.

For a personal Google account, not a Workspace one: Google Meet gives no transcripts through its API, so use Granola for meetings. A calendar or a Drive folder can be shared with a service account's own address, which needs no admin (the sources guide gives the steps, and says what Google's pages leave unstated about a shared calendar). Gmail needs one person's refresh token, which `google-authorize` obtains with a Desktop app client of your own, run on the computer whose browser that person uses (the sources guide gives the steps). Run it on your own computer, never with `docker exec` on the VM or inside the server's container, where the browser could not come back to it. While that client's consent screen is in "Testing", the token stops working after 7 days.

## Under your own name, behind a wall

To run the pre-release under your own name, with nothing public but the sign-in page, set these in `infra/gcp/terraform.tfvars`:

```hcl
cairn_extra_args      = "--sign-in google --join personal --wall --session-days 30"
cairn_brand_name      = "Knowledge Base"
cairn_brand_logo_file = "../../deploy/brand-logo.svg"
cairn_brand_logo_alt  = "Juniper Field"
```

The env secret holds `CAIRN_GOOGLE_CLIENT_ID` and `CAIRN_GOOGLE_CLIENT_SECRET` from an OAuth client at Google whose callback is `https://DOMAIN/auth/google/callback`. The logo is an SVG or a PNG of at most 60 KiB on your machine; [infra/gcp/README.md, "Changing the configuration"](../infra/gcp/README.md#changing-the-configuration) says how the three reach the VM and what `tofu plan` refuses. A change is a `tofu apply` and a restart of the VM, as for the plans file.

- A visitor who is not signed in gets the sign-in page and nothing else: your logo, your name and one button, for Google. There is no landing page and no guides for them.
- cairn's own name is on nothing a signed-out browser receives.
- Agents still connect by URL, because their addresses are not behind the wall: `claude mcp add --transport http cairn https://DOMAIN/mcp` works as in step 7. The person signs in once, in the browser the agent opens.
- A sign-in is remembered for the days `--session-days` gives, here 30, and each use renews it.

## Who can get in

`CAIRN_ALLOWED_EMAILS` names people by the address their provider has verified: the whole address, any case, nothing else ([deploy/README.md, "Only the people you name"](../deploy/README.md#only-the-people-you-name)). It gates sign-in and nothing else:

- Each listed person gets a workspace of their own (`--join personal`). They can invite others to it, but an invitation does not open the gate: the invited person must be listed too.
- With your own identity provider and `CAIRN_OIDC_ALLOWED_GROUPS`, the groups and the list both apply: a listed person outside the groups is refused.
- Taking an address off the list (a new version of the env secret, then a restart) stops the next sign-in. A session that is open, a key and an approved agent go on working. With `--session-days`, an open session can go on for as long as that browser is used. `sudo docker exec cairn python -m cairn.server session-end --db /data/cairn.db --email person@example.com` ends every session of that address in every workspace and prints how many; end their keys and agents on the **Keys** and **Agents** pages. To shut someone out fully, also delete their workspace or remove them from yours.
- The site is on the public internet. Without `--wall`, anyone can read the landing page and the guides; with it, a visitor gets the sign-in page and nothing else. Either way, anyone can start a sign-in (at most 30 starts a minute per address) and register an agent with the OAuth endpoint (`--registrations-per-hour`), since registration comes before a person approves anything. What nobody outside the list gets is a session or a workspace, and an agent is nothing until a signed-in person approves it.

## What is checked and what is not

Checked, offline, on every push:

- The gate: `tests/acceptance/test_h8_allowed_emails.py` (who gets in, that a refusal writes nothing and shows no address, the command line).
- The operator's list of workspaces: `tests/acceptance/test_h10_workspace_list.py`.
- The whole path on the real server process: `test_a_private_pre_release_admits_the_listed_and_the_operator_sets_the_plan` in `tests/system/test_journey.py` starts `serve` with the flags above against a stand-in for the provider, and walks steps 5 and 6: a stranger refused, the listed person in a workspace of their own on the default plan, the workspace in `workspace-list`, `plan-set` taking effect, no address in any output.

Not checked until it runs against the real services:

- The brand's way onto the VM is planned by `tofu test` against a mock provider and exercised by the host rehearsal in CI, on a runner and not on a real VM. It ran once on a real VM, on 2026-11-14 ([infra/gcp/README.md, "What was checked, and what was not"](../infra/gcp/README.md#what-was-checked-and-what-was-not)).
- Eight kinds of source have read a real account on the pre-release, each run by hand (`source-sync`) with its credential handed to that one process: GitHub, Linear, Langfuse, Granola and the four Google kinds (outside review, 2026-11-15 and 16). A connector has run only on a local server, and a long real Meet transcript has not been read. The background sync (`--sync-every`) is covered by the Langfuse acceptance test and has not been left running on the VM with a real account. A Google credential's JSON in the VM's env secret, as step 1 above writes it, has not been tried: the review handed each credential to one process instead.
- Sign-in against the real GitHub (Google's was seen on 2026-11-14 and again in the review, step 5). An assistant app has connected by OAuth and worked (the review, 2026-11-16); Claude Code connected by hand (step 7) has not been seen.
- Step 2's way onto the VM is exercised by the host rehearsal in CI (`scripts/rehearse_host.py`), on a runner and not on a real VM: there cairn starts with the plans file read-only at `/etc/cairn/plans.yaml`, reads it as its own user and holds a workspace to its cap. `tofu plan`'s checks of the file are checked as text, not run. What step 4 says the VM does when `serve` refuses a setting is read from `infra/gcp/files/host.sh` and the service's unit file, not seen on a VM.
