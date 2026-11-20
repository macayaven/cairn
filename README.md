# cairn

cairn is a memory service for AI agents. Agents write notes to it over MCP, and it compiles them into pages. Every claim on a page is a passage copied from a note, and it links to the exact lines it came from. cairn checks each page against its notes before it saves it, and keeps the last good version when a check fails. It runs as one container on your own computer, or on a server where people sign in, share a workspace and keep a project private. cairn's own code is open source under the MIT License. **Version 0.1.0 is the first release: early software, built test-first.**

## Run it on your computer

You need Docker Engine 28.3.3 or later (`docker version` prints it under Server), Compose 2.24 or later (`docker compose version` prints it) and a clone of this repository. cairn is published here on `127.0.0.1` only, and Engine 28.3.3 is the release that [closed a way](https://docs.docker.com/engine/release-notes/28/) for such a port to be reached from the local network after a firewall reload. A Linux distribution's own `docker.io` package can be older: [Docker's install pages](https://docs.docker.com/engine/install/) give the current Engine for each distribution. Docker Desktop carries its own Engine and Compose: check both the same way.

There are two ways to get the image, and the commands after this section are the same for both, apart from [Update](#update). **The released image** is pinned: `ghcr.io/macayaven/cairn:0.1.0` names one release and never moves to newer code. Pulling it needs no login:

```bash
git clone https://github.com/macayaven/cairn.git &&
  cd cairn/deploy &&
  echo 'COMPOSE_FILE=compose.local.yaml' > .env &&
  echo 'CAIRN_IMAGE=ghcr.io/macayaven/cairn:0.1.0' >> .env &&
  docker compose pull cairn &&
  docker compose up -d --no-build
```

The clone is there for the files in `deploy`; the code that runs is the image's. [Run it yourself](docs/guides/run.md#or-pull-a-released-image) says how to check what you pulled.

**Or build the source.** These commands follow the `main` branch as it moves, not a pinned release. Record `git rev-parse HEAD` with each build to identify the code you run:

```bash
git clone https://github.com/macayaven/cairn.git &&
  cd cairn/deploy &&
  echo 'COMPOSE_FILE=compose.local.yaml' > .env &&
  docker compose up -d --build
```

The `.env` file makes every `docker compose` command in this folder use `compose.local.yaml`: cairn alone, with no domain and no proxy; with `CAIRN_IMAGE` in it, Compose runs that image instead of building one. `--build` builds the image from this clone and names it `cairn:local`. Keep it on the first run: without it Compose starts whatever image already has that name on this computer, such as one an earlier clone left, and the code you just fetched is not what runs. The build takes a few minutes. cairn then answers at http://127.0.0.1:8765, on this computer only; nothing else on your network can reach it. If another program already uses port 8765, choose a free port, write it into `.env` before `up`, and use it wherever this guide says 8765. For port 18765:

```bash
echo 'CAIRN_PORT=18765' >> .env
```

`docker compose ps` shows the container as `(healthy)` once it serves.

### Make a key

cairn has no password. You sign in with an API key, and agents connect with one. Make the first key inside the container, copy it out, and delete the copy inside:

```bash
docker compose exec cairn python -m cairn.server key-create --db /data/cairn.db --label laptop --out /data/laptop.env
docker compose cp cairn:/data/laptop.env ./laptop.env
docker compose exec cairn rm /data/laptop.env
```

`deploy/laptop.env` now holds one line, `CAIRN_API_KEY=` and the key. Run `chmod 600 laptop.env` so that only you can read it: a copy out of a container keeps the file's permissions only where your file system does. git ignores the file. cairn keeps only a digest of the key: if you lose the file, make another key.

### Sign in

Open http://127.0.0.1:8765 and paste the key, the part of the line after `CAIRN_API_KEY=`. The welcome page offers to make projects for what you will use cairn for. You can skip it: a connected agent makes its own projects.

### Connect an agent

Agents reach cairn over MCP at http://127.0.0.1:8765/mcp and send the key in an `x-api-key` header. For Claude Code, put this in `.mcp.json` in the folder you start it from. It names the variable, not the key:

```json
{
  "mcpServers": {
    "cairn": { "type": "http", "url": "http://127.0.0.1:8765/mcp", "headers": { "x-api-key": "${CAIRN_API_KEY}" } }
  }
}
```

Then load the key into your shell and start Claude Code from it:

```bash
set -a; . ./laptop.env; set +a
claude
```

Run the first line from `deploy`, or give the path to `deploy/laptop.env` from where you are. [The connect guide](docs/guides/connect.md) has the steps for Codex, Cursor, VS Code, Gemini CLI and other clients.

### Write a note and see its page

Ask the agent to note something in cairn, for example a decision and its reason. The agent calls the `note` tool. cairn stores the note, splits it into passages, and files its claims on a page of the project the agent chose. The answer names the page and says `saved`.

In the browser, open **Projects**, then the project, then the page. Each claim has a number and a **See the source line** link. It opens the lines of the note the claim came from, with the note's own line numbers, who wrote it and when. **Sources**, at the end of the page, lists each note and the claims that cite it.

### Where the data lives

Everything cairn knows is in one SQLite database, `/data/cairn.db`, on a Docker volume named `cairn_cairn-data`. Removing the container loses nothing; removing the volume loses everything. `docker volume inspect cairn_cairn-data` shows where Docker keeps it. On macOS and Windows that folder is inside Docker's own virtual machine, so reach the files with `docker compose cp`, as the backups below do.

### Back it up

An export holds every project's description, every note as it was written and every page version cairn keeps. It does not hold keys or settings. Take one while cairn runs:

```bash
docker compose exec cairn python -m cairn.server export --db /data/cairn.db --account acct_default --out /data/export.zip
docker compose cp cairn:/data/export.zip ./cairn-export.zip
docker compose exec cairn rm /data/export.zip
```

A copy of the database holds every project with its notes and pages, the people, and a digest of each key. It does not hold the keys themselves, which are in the files you copied out (`laptop.env`), nor an environment file you gave cairn, with the credentials of sources in it. Keep those somewhere else. Note the commit the clone is at (`git rev-parse HEAD`): a copy is restored with the code that made it. The commands below stop cairn so the copy is whole, copy into a new folder every time, and start cairn again whether or not the copy worked:

```bash
(
  set -e
  BACKUP_DIR=$(mktemp -d "$PWD/cairn-backup.XXXXXX")
  restart_cairn() {
    BACKUP_STATUS=$?
    trap - EXIT
    if ! docker compose start cairn
    then
      printf 'Restart failed. Run docker compose start cairn and inspect its status. Backup directory: %s\n' "$BACKUP_DIR" >&2
      exit 1
    fi
    if [ "$BACKUP_STATUS" -eq 0 ]
    then
      printf 'Backup saved in %s\n' "$BACKUP_DIR"
    fi
    exit "$BACKUP_STATUS"
  }
  trap restart_cairn EXIT
  docker compose stop cairn
  if ! docker compose cp cairn:/data/. "$BACKUP_DIR/"
  then
    printf 'Copy failed. This is not a successful backup: %s\n' "$BACKUP_DIR" >&2
    exit 1
  fi
)
```

A copy that fails is not a backup: the commands say so and end with an error. Run them again, and they make a new folder. If cairn does not start again, run `docker compose start cairn` and look at `docker compose ps`.

To run cairn from a copy, follow [Restore a copy of the database](docs/guides/run.md#restore-a-copy-of-the-database) with the folder a backup printed. Its steps copy the backup into a new folder and start a second container on it, so the backup and the data of the running cairn stay as they are.

To bring an export back into a cairn whose workspace has no project of the same names, write it in as cairn's own user, import it and delete the copy:

```bash
docker compose exec -T cairn sh -c 'cat > /data/export.zip' < ./cairn-export.zip
docker compose exec cairn python -m cairn.server import --db /data/cairn.db --account acct_default --in /data/export.zip
docker compose exec cairn rm /data/export.zip
```

An export brings back no key, so in a new install make a key again, as above. Keep the backups somewhere other than this computer. [Run it yourself](docs/guides/run.md#back-it-up) says what an export holds and what it leaves out.

### Update

From the same clone's `deploy` folder, take a [cold database backup](#back-it-up) first and note the running image (`docker compose images -q cairn`) and source commit (`git rev-parse HEAD`). Then fetch the new code, build the image while cairn still runs and replace the container. Keep `.env`, `cairn.env` and any extra options as they were. The volume stays:

```bash
(
  set -e
  git pull --ff-only origin main
  docker compose build cairn
  docker compose up -d
)
```

With the released image there is nothing to build: after the same backup, change the version in the `CAIRN_IMAGE` line of `.env`, then run `docker compose pull cairn` and `docker compose up -d --no-build`.

A newer cairn may update the database when it starts. When the update only adds to the database, the earlier version still runs on it; when it changes what the earlier version relies on, that version refuses the database in one line that names the schema version it needs, and going back means going back to a backup taken before the update. The changelog says which kind each release is. Going back from 0.1.0 to a build before it needs the backup.

### Stop it

`docker compose down` stops cairn and removes the container. The volume stays, and `docker compose up -d` starts cairn on it again.

## Run it for other people

[deploy/README.md](deploy/README.md) runs cairn on a server that a domain points at, behind Caddy, which gets the certificate. To put that on one small VM on AWS or Google Cloud with three settings, backups to a bucket included, see the [quick deploy](infra/quick/README.md); it has been validated but not yet applied to a real account, and says what that leaves unproven. [infra/gcp](infra/gcp/README.md) is the fuller set-up for Google Cloud. It adds backups to object storage with Litestream, sign-in with GitHub, Google or your own identity provider, and caps per workspace from a plans file the operator writes. [Run it yourself](docs/guides/run.md) builds the source and runs it with `docker run` instead of Compose.

## Develop

You need [uv](https://docs.astral.sh/uv/), on Linux or macOS: the store takes Unix file locks, so Python on Windows cannot import it, and on Windows cairn runs in Docker or WSL. From the clone's top folder:

```bash
uv sync
uv run pytest tests/unit -o addopts="" -q -p no:cacheprovider
uv run ruff check . && uv run ruff format --check .
```

To run cairn from the source, without Docker:

```bash
uv run python -m cairn.server key-create --db runs/dev/cairn.db --label laptop --out runs/dev/laptop.env
uv run python -m cairn.server serve --db runs/dev/cairn.db --host 127.0.0.1 --port 8765
```

The browser tests in `tests/browser` start a server of their own on 127.0.0.1 and walk its pages in headless Chromium: axe-core's checks in the light and dark schemes, a 375-pixel screen, the keyboard, and the graph's labels. They make no request beyond that server. No other run collects them, so the commands above need no browser. To run them, fetch Playwright's Chromium once, then name the folder with `--run-browser`:

```bash
uv run python -m playwright install --only-shell chromium
uv run pytest tests/browser --run-browser
```

`runs/` is ignored by git. Tests come first: a change starts with a test that fails for the expected reason. Tests run offline, with a fake model and fake HTTP, and every fixture is synthetic. [CONTRIBUTING.md](CONTRIBUTING.md) says more, and [SECURITY.md](SECURITY.md) says how to report a vulnerability.

## What it does not promise

cairn has not been assessed against HIPAA, FERPA, COPPA or any rule like them, and as shipped it is not ready to hold health or education records about people who can be identified: [the privacy guide](docs/guides/privacy.md#records-the-law-protects) says what is missing.

A page keeps its newest 50 versions under `serve`: `--keep-versions N` keeps N, `--keep-versions all` keeps every one, and with a plans file the plans decide. A claim is found again by its project, page, claim id and version, which `evidence` takes, for as long as the page keeps that version. A citation shows what a note said, not which of two notes that disagree is right. What a source reads, and where the check that keeps two sources from writing one item stops, is in the [privacy guide](docs/guides/privacy.md#what-a-source-reads).

## Licence

cairn's own code is under the MIT License: see [LICENSE](LICENSE).

Third-party components keep the licences their owners gave them:

- The two typefaces in `src/cairn/static/fonts/` are under the SIL Open Font License 1.1. Their licence texts sit beside them, `src/cairn/static/fonts/atkinson-hyperlegible-mono.OFL.txt` and `src/cairn/static/fonts/atkinson-hyperlegible-next.OFL.txt`, and the image serves them with the fonts.
- The Python packages cairn installs are not in this repository; `uv.lock` names each one and its version. Most are under permissive licences (MIT, BSD, Apache-2.0). certifi, which carries the certificate authorities that HTTPS checks against, is under MPL-2.0. On Windows only, `mcp` also installs pywin32; read the licence files in its own distribution before you pass a Windows build on.
- In the image, each installed package keeps its licence file in its own folder under `/app/lib/python3.13/site-packages/`, the one whose name ends in `.dist-info`. The base image's Python keeps its licence at `/usr/local/lib/python3.13/LICENSE.txt`, and its Debian packages theirs under `/usr/share/doc/`.
- The files named `*-api.md` and `google-*.md` in `docs/` are notes on other services' public documentation, taken while cairn's readers for those services were written. Where they quote it, each quote has its page and the day it was read. The quotes remain their owners'; the notes around them are cairn's. Quotes from pages on developers.google.com and from GitHub Docs are excerpts used under the Creative Commons Attribution 4.0 International licence (https://creativecommons.org/licenses/by/4.0/), as those pages offer their text. What Google's Help Center, Granola's pages, Linear's pages and Langfuse's documentation say is given in cairn's own words, with the page linked.

## Guides

A running cairn serves these at `/docs`, from the Markdown in `src/cairn/guides`. The links here lead to `docs/guides`, the same guides as a server shows them without a brand, written by `uv run python scripts/render_guides.py`; a test fails when one is out of date.

| Guide | For | On a running server |
|---|---|---|
| [Connect an agent](docs/guides/connect.md) | Claude Code, Codex, the Claude apps, ChatGPT, Cursor, VS Code, Gemini CLI and scripts, by URL on an install with `--public-url`, or with a key | `/docs/connect` |
| [Patterns](docs/guides/patterns.md) | Laying out projects and connections: many agents with one project, many projects, sets of projects per use | `/docs/patterns` |
| [Bring in what you already have](docs/guides/sources.md) | Recipes for meetings, mail, calendar, documents and to-dos through an agent you already use, and sources the server reads itself (Langfuse traces, Granola meeting notes, Google Meet transcripts, the documents of a Google Drive folder, Google Calendar events, the Gmail messages a person labels, Linear issues once they are finished, the finished work of one GitHub repository, and any other system through a connector of your own), which owners and admins switch on and off | `/docs/sources` |
| [Security](docs/guides/security.md) | Who reaches what, how sign-in and agent connections work, what the checks catch and what they do not | `/docs/security` |
| [Run it yourself](docs/guides/run.md) | One container on your own machine: the `docker run` command, a key, signing in, where the data lives, backups and upgrades | `/docs/run` |
| [Privacy](docs/guides/privacy.md) | What cairn stores, where and for how long, export and deletion, and a section for the operator to fill in | `/docs/privacy` |
| [Deploying](deploy/README.md) | Running cairn for other people behind a domain, with backups | |
| [Architecture](docs/architecture.md) and [decisions](docs/decisions.md) | Where the code can be extended, and the calls made along the way | |

## What exists today

| Piece | What it does | Checked by |
|---|---|---|
| Chunker | Splits Markdown into headings and citable passages, with heading paths and stable ids | `tests/acceptance/test_t1_spans.py`, regression tests, a held-out set |
| Fact extractor | Finds flags, paths, numbers with units, dates, versions, URLs, inline code and code blocks; code is scored per line | `tests/acceptance/test_t2_facts.py`, a synthetic gold set, a held-out set |
| `python -m cairn lint` | Scores any page set against its sources: retention by fact type, code-line retention, passage coverage, duplicates, misplaced facts; pages that cite passages also get provenance, grounding and attribution checks | `tests/acceptance/test_t3_lint.py`, `test_t3_multisource.py` |
| Store | Many workspaces and projects in one SQLite file, versioned pages, FTS5 search; keys, browser sessions, agents' tokens and invitation links cairn issues, stored only as digests; with `--google-connect`, a Google refresh token stored sealed; a read writes nothing unless the operator asked for reads to be audited (`--audit-reads`) or for idle sign-ins to end (`--idle-minutes`) | `tests/acceptance/test_w1_store.py`, `test_h1_accounts.py` |
| Compiler and gate | Turns each note into page claims and saves a version only if the gate passes | `test_w1_store.py`, `test_w3_gate.py` |
| Arranging model | With `serve --model-env`, a model orders a page's claims and folds repeated ones; it answers with claim numbers, and cairn builds the page from text it already holds | `tests/acceptance/test_t4b_arrange.py` |
| Tidying without a model | On by default in `serve` (`--no-tidy` turns it off; `--model-env` puts a model in its place), cairn keeps each heading's claims together and folds repeated ones itself: no model, key or budget, and the page says it was arranged by cairn | `tests/acceptance/test_t4f_tidy.py` |
| LLM compile adapter | A chat model behind an OpenAI-compatible endpoint drafts the claims for a note's new passages, behind `verbatim_gate`: each claim is one passage it cites, whole, never cut, joined or written; claims may be arranged, and passages with the same text folded into one. Not used by `serve` | `tests/acceptance/test_t4_llm_compile.py`, `test_t4_coverage_guard.py`, `test_t5_grounding.py` |
| Web service | REST API, an MCP endpoint, and a web UI where every claim links to its source lines | `tests/acceptance/test_w2_server.py`, `test_p1_pages.py` |
| People and access | Sign-in with GitHub, Google or OIDC, open or limited to the people the operator names; workspaces, roles and invitations; projects private or shared; keys and agents limited to some projects or to reading | `test_h4_signin.py`, `test_h8_allowed_emails.py`, `test_t1_members.py`, `test_t2_project_sharing.py`, `test_s1_scopes.py` |
| Wall and brand | Signed out, only the sign-in page, under the operator's name and logo; a sign-in kept for days and renewed by use; `session-end` | `test_b1_wall.py` |
| Agent sign-in | OAuth 2.1 with dynamic client registration and PKCE, rotating refresh tokens | `test_h3_oauth.py` |
| Note guard | Refuses a note that holds a secret before any of it is stored | `test_g1_guard.py` |
| Your data | Export a workspace as one file, import it elsewhere, delete it | `test_h5_data.py` |
| Plans | Caps per workspace from a plans file the operator writes, enforced inside the write; the operator lists workspaces and sets a plan by hand | `test_h6_quotas.py`, `test_h10_workspace_list.py` |
| Google credential | `google-authorize` runs Google's consent flow for a Desktop app client on the computer whose browser the person uses, and writes that person's credential for a Google source to a new file with mode 0600, never overwriting one; it opens no database | `tests/acceptance/test_c8_google_authorize.py` |
| Container image | The whole service in one image for linux/amd64 and linux/arm64; non-root, database on a volume | `scripts/smoke_container.py`, which CI runs on both architectures |

Without `--model-env`, `serve` compiles with the **extractive baseline**: each claim is a verbatim source passage, so it passes the provenance and retention checks by construction.

The model behind `--model-env` speaks the OpenAI chat format by default, or Anthropic's Messages format with `<PREFIX>_PROVIDER=anthropic` (the run guide shows both). Its address (`<PREFIX>_BASE_URL`) must be reached over https, or over http on the same machine (localhost, 127.0.0.1 or [::1]); any other address is refused at startup (exit 2), so the API key and the claims never cross a network in clear. `<PREFIX>_REQUEST_EXTRA`, a JSON object, adds fields to every request: a model that reasons before it answers runs into the answer bound and is refused, so its server is told not to (`{"chat_template_kwargs": {"enable_thinking": false}}` on llama.cpp and vLLM). A page that holds a note any source the server reads brought (Langfuse, Granola, Google Meet, Google Calendar, Gmail, Google Drive, Linear, GitHub or a connector of your own), or a note whose `source_ref` says it came from a meeting, a mail thread, a calendar, a document or a to-do list (the sources guide gives the forms), is not sent to the model, and is tidied without it, unless `<PREFIX>_SOURCE_NOTES=send`, which sends it except in a project its people keep from every model; `withhold` is the default.

Try a model before you give it the worker: `evals/t4_compile.py --arrange`, with `--page-claims` once for each page size, arranges synthetic pages of several sizes and says how often the answer was valid, with tokens and seconds per page. Of six local models tried, one arranged pages of every size.

## Options of `serve` in a container

One image serves the web UI, the REST API and the MCP endpoint on port 8765. It runs as a non-root user and keeps its database on the `/data` volume. The compose files in `deploy/` start it, and [Run it yourself](docs/guides/run.md), served at `/docs/run`, builds the source and starts it with `docker run`, from the first key to backups and upgrades. Each release is also published as an image, such as `ghcr.io/macayaven/cairn:0.1.0`; the run guide says how to pull it and check what you pulled.

Arguments after the image name replace the default command, `serve --db /data/cairn.db --host 0.0.0.0`. With Compose they go in `CAIRN_EXTRA_ARGS` in `deploy/.env`, after the command the compose file already gives:
- **Behind a reverse proxy or Tailscale Serve:** add `--public-url https://cairn.example.net`. Form posts are then checked against that origin, and the session cookie is marked Secure. Add `--trusted-proxy` with the address or network the proxy's requests arrive from too. In a container that is the Docker network (`docker network inspect bridge` shows its range), never 127.0.0.1, even for a proxy on the same machine. No proxy is trusted otherwise, loopback included, and every client would share one count in the limits on sign-in and agent registration.
- **Other hostnames for `/mcp`:** add `--allowed-host NAME` for each one. Any other Host header gets 421.
- **A host directory instead of a volume:** run as the directory's owner, with `-v "$PWD/data:/data" --user "$(id -u):$(id -g)"`. If cairn may not write there, `serve` stops at start with one line that names the folder or the database file and says to run cairn as its owner.
- **No tidying:** add `--no-tidy`. cairn otherwise tidies each page a note arrives on, with no model ([the run guide](docs/guides/run.md#tidy-pages-without-a-model)).
- **No graph script:** add `--no-graph-explorer`. By default `serve` shows the graph explorer on a project's **Graph** page, the one page that loads a script (cairn's own, by a nonce and its hash); with `--no-graph-explorer`, the graph page is drawn by the server alone and runs no script ([the run guide](docs/guides/run.md#a-graph-to-explore)).

`python3 scripts/smoke_container.py cairn:local` checks the image Compose built end to end.

### Sign in with GitHub, Google or your own identity provider

Without these options, people sign in with an API key. With them, the login page offers one button per provider and no key form, and nobody needs a key to use the web UI. Sign-in needs `--public-url` (https, or http on localhost). Register an OAuth app at the provider whose callback URL is `<public URL>/auth/<provider>/callback`: `/auth/github/callback`, `/auth/google/callback`, or `/auth/sso/callback` for your own provider.

Client ids and secrets come only from the environment, never from arguments:

| Provider | Variables |
|---|---|
| `github` | `CAIRN_GITHUB_CLIENT_ID`, `CAIRN_GITHUB_CLIENT_SECRET` |
| `google` | `CAIRN_GOOGLE_CLIENT_ID`, `CAIRN_GOOGLE_CLIENT_SECRET` |
| `oidc` (Okta, Entra ID, Cognito, Keycloak, ...) | `CAIRN_OIDC_ISSUER`, `CAIRN_OIDC_CLIENT_ID`, `CAIRN_OIDC_CLIENT_SECRET`; optional `CAIRN_OIDC_NAME` (route, default `sso`), `CAIRN_OIDC_LABEL` (button), `CAIRN_OIDC_GROUPS_CLAIM` and `CAIRN_OIDC_ALLOWED_GROUPS` (comma-separated; needs the claim; set but blank stops `serve`) |

These are the arguments of `serve`, in place of the default command:

```text
# A service open to anyone with an account there: every new person gets a workspace of their own.
serve --db /data/cairn.db --public-url https://cairn.example.net --sign-in github --sign-in google

# An organisation's own install: everyone joins one shared workspace.
serve --db /data/cairn.db --public-url https://cairn.example.net --sign-in oidc --join default \
  --allowed-email-domain example.com
```

To let in only the people you name, with any provider, set `CAIRN_ALLOWED_EMAILS` to their addresses, separated by commas (`CAIRN_ALLOWED_EMAILS=ada@example.test,brook@example.test`). A person then signs in only when the email the provider verified is one of them, the whole address, ignoring case. It comes only from the environment, never from an argument; set but blank, holding an entry that is not one address, or set without `--sign-in`, it stops `serve`. It is checked on every request, not only at sign-in: once `serve` runs without someone, their open sessions, the keys they made and the agents they approved are refused as ended ones; nothing is deleted, so putting them back on the list restores them. `session-end --db PATH --email ADDRESS` ends that address's open sessions. With `--allowed-email-domain` as well, either admits. deploy/README.md, "Only the people you name", says more.

With sign-in on, five more options put the install under your organisation's name: `--wall` shows a browser that is not signed in the sign-in page and nothing else, while `/mcp`, `/api` and OAuth answer as before; `--brand-name NAME`, `--brand-logo FILE` (SVG or PNG) and `--brand-logo-alt TEXT` put your name and logo in place of cairn's wherever a signed-out browser can look and in each page's frame; `--session-days N` keeps a sign-in for N days in place of 12 hours, and a use more than a day after it was made or last renewed starts the N days again. The run guide's "Under your own name, behind the sign-in" says what each covers and what changes name.

`--join default` puts everyone in the one shared workspace; the first person to sign in owns it. With GitHub or Google, which anyone can join, cairn refuses that unless you also limit it with `--allowed-email-domain example.com` (repeatable) or `CAIRN_ALLOWED_EMAILS`. For Google an account admitted by its domain must then also belong to that Google Workspace domain. Your own provider may allow public self-registration too, so `--sign-in oidc --join default` needs one of four limits: `--allowed-email-domain`, `CAIRN_OIDC_ALLOWED_GROUPS` (with `CAIRN_OIDC_GROUPS_CLAIM`, the claim that lists a person's groups), `CAIRN_ALLOWED_EMAILS`, or `--closed-directory`, by which you state that only your own people can get an account at that provider; without one, `serve` exits and names them. Each identity is recorded with its provider's issuer: if you point `CAIRN_OIDC_ISSUER` at another issuer under the same `CAIRN_OIDC_NAME`, a sign-in whose subject was recorded under the old issuer is refused until you act. A person at the new issuer whose subject is new and whose verified email is an existing user's still becomes that user, as with any provider, so only move to an issuer that verifies the same addresses for the same people. cairn stores no password and no provider token. Pass the variables to Docker with `-e CAIRN_GITHUB_CLIENT_ID -e CAIRN_GITHUB_CLIENT_SECRET` so their values stay out of the command line.

## MCP tools

| Tool | Does |
|---|---|
| `projects` / `new_project` | List projects, or create one |
| `note` | Store a note and compile it; searchable as soon as it returns |
| `recall` | Search one project or all of them |
| `page` | Read a page, or any earlier version |
| `evidence` | Show the exact source lines behind a claim, as the latest version of its page cites them or, with `version`, as that version did |
| `overview` | Pages and note count of a project |

## Lint any page set

`python -m cairn lint` scores a set of pages against their sources: `uv run python -m cairn lint --help` lists its options. Exit codes: 0 clean, 1 violations or a threshold breach, 2 bad input.

## Score a model (T4 eval)

`evals/t4_compile.py` compiles every synthetic fixture in `tests/fixtures/` with each model, gates each page with `verbatim_gate` (the strict lint gate, plus every claim must be one passage it cites, whole: never cut, never joined; `--gate strict` or `--gate lint` names another), and scores every draft against all of the note's passages, rejected drafts included. The extractive baseline compiles every fixture first; a fixture it cannot save stops the run before any model is called. It prints a table per model next to the baseline, then each fixture against its baseline, and writes a JSON report to `private/t4/` (gitignored; the report holds the drafted claims, so `--out` elsewhere only on purpose). A provider failure is kept as its HTTP status and a fixed category, never the endpoint's text, and token means are marked incomplete instead of averaged when a call reported none. The exit code is 3 when the baseline failed a fixture and 4 when no model compile was saved; 1 when a model was skipped because its warm-up failed, and 2 for bad usage or a missing setting; otherwise a run that measured exits 0, however few drafts were saved. `--fail-under RATE` (0 to 1) exits 5 when a model saved a smaller share of its runs, and an `--out` outside `private/` prints a warning.

`uv run python evals/t4_compile.py --help` lists its options. The endpoint comes from `PREFIX_BASE_URL` and `PREFIX_API_KEY` (default prefix `CAIRN_LLM`), read from the environment or the env file. There is no default endpoint, except with `PREFIX_PROVIDER=anthropic`, which aims at Anthropic's API and needs the key. Without `--yes` the script only checks the configuration and prints the plan. `--probe` compiles one fixture per model. Each model is loaded in turn, so on a single-GPU server such as llama-swap a run evicts the model loaded there.

## CI/CD

`.github/workflows/ci-cd.yml` runs on pull requests, on pushes to `main` and to `v*` tags, and by hand. A draft pull request runs the tests and the infrastructure checks (items 1 and 2 below), and item 1 builds the image once for both platforms and scans it. A pull request that is ready for review also smoke-tests that build on both platforms and rehearses the stack and the host, and that is the gate a merge waits for. Marking a draft ready for review starts that run. Pushes to `main`, tags and manual runs run the whole gate too.

One exception: a change that touches only paths no image job reads runs no image check (item 3) and no stack or host rehearsal (item 4), so a pull request runs items 1 and 2 alone and a push to `main` publishes and deploys nothing; the server keeps the image it runs. Its image is still built for both platforms and scanned, as item 1 says. The list of those paths is `NO_IMAGE_WORK` in `scripts/ci_changes.py` (among them `docs/`, `tests/`, `evals/`, `examples/`, `CLAUDE.md` and `.gitignore`; the script has all eleven), and a first job, `changes`, runs that script. A change to `CHANGELOG.md` alone is not one of them: it is the release's own text, so a push of it to `main` builds and publishes like any other, and its commit can be tagged. On `main`, the change is measured from the head of the newest run on `main` that published an image, read from GitHub's API, so a change whose run failed or whose deploy was refused still ships with the next push. Any other path, or a change whose paths cannot be known (a tag, a manual run, a first or forced push, no run that published, an earlier run on `main` still going, a failed lookup), runs the whole gate. Items 1 and 2 always run.
1. **Tests:** ruff and the test suite. Beside them, on every change, drafts and documents included, job `image_build` builds the image once, for linux/amd64 and linux/arm64, with a build provenance and a bill of materials for each, in a job that may only read the repository, and keeps it as one archive with its digest (on a `v*` tag it builds nothing: it takes the image `main`'s run published for the tag's commit, item 5); and four scans that `publish` needs: the locked dependencies and `build-constraints.txt` (pip-audit: a known vulnerability with a fixed version fails), both platforms of that build (Trivy: HIGH or CRITICAL with a fix fails, and those with none are listed in the run's summary), `src/` (Bandit, at medium severity and confidence) and the whole history (gitleaks, its output redacted). Each ignored finding is listed in `.github/scanners/` or `.gitleaks.toml` with its reason, and an ignore of pip-audit's or Trivy's ends on its date. Job `browser` runs the browser tests (`tests/browser`) in headless Chromium on every change, and `publish` needs it.
2. **Infrastructure and deploy files:** OpenTofu formatting and validation, the host and deploy scripts, the compose files, Caddy's own check of its configuration, and the restore drill on a fresh database. The quick deploy's OpenTofu definitions (`infra/quick/aws`, `infra/quick/gcp`) are formatted, initialised from their lock files, validated and tested, with no cloud credentials, when a change touches them and on every tag (job `quick`).
3. **Image check:** runs `scripts/smoke_container.py` against each platform of that build, started for that platform. The scan, the image check and the rehearsals push the archive, by its digest, to a registry on their runner with the same commands `publish` uses, and take each platform from there by that platform's own digest, so what they check is what is published.
4. **Stack and host rehearsals:** with the build's linux/amd64 image, runs the shipped compose stack on the runner, with Caddy and Litestream, and restores a lost database into it (`scripts/rehearse_stack.py`); then sets up the VM's host on a throwaway runner, with its real units, data disk and updater (`scripts/rehearse_host.py`).
5. **Publish:** once items 1 to 4 have all passed, pushes to `main` and `v*` tags publish one multi-arch image, with provenance and an SBOM, to `ghcr.io/<owner>/<repo>`: the build items 1 and 3 checked, by its digest. The job that publishes it, one of the two that may write to the registry, builds nothing, checks out nothing and runs no image. A tag is published only from a public repository, when it is `v` and the version in `pyproject.toml`, `CHANGELOG.md` has a section for that version with an entry, dated no later than the day of the run, the tag's commit is on `main`, no tag of its `X.Y` is a higher version, and the version has no GitHub release yet: `scripts/release_check.py` checks it first. A push to `main` is published as `main` and `sha-<commit>`, never `latest`, and `sha-<commit>` is written once: if the registry already has it, the same digest is carried on and another digest stops the job. `main` is moved only when the run's commit is still the head of `main`, read just before it is moved: a run of an older commit, a retry among them, writes its `sha-<commit>` and leaves `main` to the newer commit's run. A `v*` tag builds no image: it promotes the digest that `main`'s run published as `sha-<commit>` for the tag's commit to `X.Y.Z` (a candidate, to `X.Y.Z-rc.N`), and writes no `sha-` tag, so a candidate and the release tagged on one commit carry `main`'s image, the same digest. When the registry has no `sha-<commit>` for the tag's commit, the tag's run stops before publishing anything and says to run it again once `main`'s run for that commit has published it; a commit `main`'s run never published (a change to documents alone, or an earlier commit of a push) cannot be released, so tag one it did. `X.Y.Z` never moves: if the registry already has that version, the same digest is carried on and another digest stops the job. After a release, and never after a candidate, job `aliases` points `latest` at the highest released version and each `X.Y` at its newest `X.Y.z`, one such job at a time across all runs, deciding from the releases and the registry as they are when it runs, so overlapping releases cannot move either backwards. When a waiting `aliases` is replaced by a newer one that then fails, `latest` and `X.Y` stay where they were: re-run the job `aliases` alone in any release's run, which decides from the releases and the registry as they are then and so moves every alias where it belongs. Job `aliases` moves `latest` first and then each `X.Y`: if it stops in between, `latest` has moved and an `X.Y` may not have, and running it again finishes the job. A repository whose registry is not ready sets the variable `CAIRN_PUBLISH` to `false`: the gate runs as before on pull requests and on `main`, and items 5 to 7 are skipped; a `v*` tag's run stops in `image_build`, since `main` published no `sha-<commit>` for it. This repository's package exists already; in a fresh fork, the first `publish` lists the package's tags before there is a package, which GHCR may refuse, so the package may need to be created first: this is not checked.
6. **Verify and deploy:** on `main` and on tags, the published image is smoke-tested. In a public repository the image is first pulled with no login, as the run guide does (a tag's version, or on `main` the commit's own tag), and must be the digest just published. On `main`, job `deploy` moves the production VM to it, in the GitHub environment `production`: whether it waits for a person's approval is that environment's setting (required reviewers), which the workflow cannot see, so set it in the repository's settings for a deploy to wait ([infra/gcp/README.md](infra/gcp/README.md)).
7. **Release:** a tag whose published image passed becomes a GitHub release, with the version's section of `CHANGELOG.md` as its text and the wheel and source archive attached. Job `package` builds them in a job that holds no permission at all, with the build backend and its dependencies pinned by hash in `build-constraints.txt`; job `release`, the only one that may write to the repository, builds and installs nothing, checks that it got one wheel and one source archive of the tag's version, and makes the release. A tag never deploys. `package` also writes `SHA256SUMS` over the files, which the release attaches; between the two, job `attest` checks each file against it, as `release` does, and gives the wheel, the source archive, `SHA256SUMS` and the image's digest each a build provenance attestation (the image's is made by the tag's run, which promoted it; the build provenance inside the image index records `main`'s build). A tag's run builds no image (job `package` builds only the release's files), so re-running all its jobs promotes the same digest again and finishes it, until its GitHub release exists, when `release_check` refuses the tag: from then on, re-run only the failed jobs, which GitHub follows with the jobs that need them, a failed `release` within the five days the run's files are kept and a failed `aliases` for as long as GitHub re-runs the run: 30 days from its first run, and 50 re-runs at most. After that, `aliases` runs with the next release, in that tag's run, or re-run alone in any release's run that GitHub still re-runs, and moves this version's aliases too, since it decides from every release and the registry as they are then. The release's text ends in a mark, an HTML comment GitHub does not show, with the id of the run that made it. Job `release` makes the release with separate requests and takes none back: it makes a draft with the files (`gh release create --draft`), compares the draft's title and notes with what it wrote and its files with the ones this run attested, names and bytes, publishes the draft with a request of its own, to the draft's id, and looks again; it succeeds only on a published release that carries that title and those notes and holds exactly those files, byte for byte. Each look lists the repository's releases, drafts included (a listing that fails stops the job), and takes the one for its tag as its own only when it carries this run's mark and this tag's pre-release flag. So a re-run of `release` starts from what an earlier attempt left: no release, and it makes the draft; this run's draft with this run's title and notes and exactly this run's files, and it publishes it; a published release with the same, and it leaves it as it is and succeeds, so that a release tag's `aliases` runs (a candidate's run has none). A publication GitHub made while its reply was lost fails the job and loses nothing: the re-run finds the release published. A draft that does not hold exactly this run's files, as when an upload was cut, stops the job, and a person deletes it on the releases page and re-runs the job, which makes the release again (the table below has the line). A release whose notes are not, byte for byte, the text the job wrote (the version's section of `CHANGELOG.md`, an empty line and the mark) stops the job too, a draft before it is published and a public one as it is: nothing is normalised, so a draft saved again in GitHub's web form can count as changed, since the form may rewrite its line ends. The title is compared as well: missing or empty it counts as the tag, since GitHub shows the tag for a release with no title, and any other title that is not the tag stops the job, before and after the publication (the table has the line for both). Any other release for the tag stops the job. The job's own text issues no delete of a release or of a release's file and replaces no file, and `gh release create`, asked for a draft, publishes nothing and does not delete the release it made when an upload fails. The job's one write to a release that exists is the publication, which sets `draft` to false and nothing else (for a candidate it says again that the release is not the latest): on a draft a person published meanwhile it changes nothing, and on one a person deleted it fails. On `main`, re-running every job builds again and `publish` stops, since `sha-<commit>` holds another digest: re-run only the failed jobs.

A release candidate tag `vX.Y.Z-rc.N` (N from 1) rehearses the release of `X.Y.Z` before it is made: it needs the version `X.Y.Z` in `pyproject.toml` and its section in `CHANGELOG.md`, runs every job a release runs but `aliases`, attestation included, and is refused once `vX.Y.Z` is tagged or released. It publishes `main`'s image for its commit as `X.Y.Z-rc.N` only, never `X.Y`, `X.Y.Z` or `latest`, and makes a GitHub pre-release of its own, not marked latest, with its own files, which carry the version `X.Y.Z`. The release tagged later on the same commit publishes the same image digest and makes a second GitHub release, with files of its own; the candidate's pre-release stays as it was.

To check a release's file or its image, with the GitHub CLI (for the image it reads the digest from the registry, so log in to `ghcr.io` first if it asks):

```bash
gh attestation verify cairn_memory-X.Y.Z-py3-none-any.whl --repo <owner>/<repo> --signer-workflow <owner>/<repo>/.github/workflows/ci-cd.yml --source-ref refs/tags/vX.Y.Z --source-digest <commit>
gh attestation verify oci://ghcr.io/<owner>/<repo>:X.Y.Z --repo <owner>/<repo> --signer-workflow <owner>/<repo>/.github/workflows/ci-cd.yml
gh attestation verify oci://ghcr.io/<owner>/<repo>:X.Y.Z --repo <owner>/<repo> --signer-workflow <owner>/<repo>/.github/workflows/ci-cd.yml --source-ref refs/heads/main --source-digest <commit>
```

`<commit>` is the full commit the tag `vX.Y.Z` names (`git rev-parse 'vX.Y.Z^{commit}'`): `--source-digest` checks that the attestation was made by a run of that commit, which is what ties the files and the image to the release's commit. `--source-ref` checks which ref that run was for: a release candidate tagged on the same commit builds and attests a wheel of its own under the same name, so the wheel's command names `refs/tags/vX.Y.Z`, and `refs/tags/vX.Y.Z-rc.N` when the file is a candidate's. Use `gh` 2.102.0 or later: from that version it compares `--source-ref` with the attestation's ref exactly and `--signer-workflow` with the whole workflow path, where earlier versions ignored the ref's case and took the workflow as a prefix (GHSA-4mq3-hpgx-9cx8, GHSA-wjmr-j3rp-mh2g). A pass for the wheel proves that these bytes were built and attested by this repository's `ci-cd.yml` in a run of the tag `vX.Y.Z` at that commit, unchanged since; it does not prove that they are the files the release attaches, since a re-run of job `package` builds files of its own and `attest` attests those too. A pass for the image proves less by itself, since the tag's run, which promotes the image and does not build it, attests its digest too; with `--source-ref refs/heads/main` a pass proves that `main`'s run of `ci-cd.yml` published and attested that digest, the run that built it, as the tag's run checked before it promoted it. Neither proves that the code or its dependencies are free of flaws, nor that a release candidate's files are the release's (its image is the release's, by digest).

Jobs run on GitHub's `ubuntu-latest` unless the repository variable `CAIRN_GATE_RUNNER` (the gate's jobs except `quick` and `browser`, and `package`) or `CAIRN_RELEASE_RUNNER` (the jobs that publish, release and deploy) names a runner of your own. Five jobs run the GitHub CLI, `gh`, which GitHub's runners have: `release_check` and `image_build`, on a tag, on the runner `CAIRN_GATE_RUNNER` names, and `publish`, on a push to `main`, `release` and `aliases`, on the runner `CAIRN_RELEASE_RUNNER` names. `image_build` refuses a `gh` before 2.102.0, which fixed how `gh attestation verify` matches `--signer-workflow` and `--source-ref` (GHSA-wjmr-j3rp-mh2g, GHSA-4mq3-hpgx-9cx8). Job `release` needs `gh` 2.102.0 or later too, on the runner `CAIRN_RELEASE_RUNNER` names: what `gh` does with a draft (it makes one and takes none back, and finds one by its tag) was read in that version's source and in no earlier one.

Three limits. `release_check.py` and job `aliases` each ask GitHub for at most 1,000 releases (`release_check.py` counts candidates' pre-releases too): a listing that reaches 1,000 stops the job and says so, and a person then raises both numbers together, before the next tag. A run re-run later keeps the workflow of its own commit, and with it the cap that commit had. `release_check.py` stops before anything is published; `aliases` stops after the version and its GitHub release are, moving neither `latest` nor `X.Y`. The build's archive and the release's files are kept as artifacts of the run for five days: re-running only the failed jobs of a run works within them; after them, `publish` still finishes when the registry already holds the digest, and a job re-run alone that needs an artifact fails (the scan, the image check and the rehearsals need the archive; `attest` and `release` need the files), so on `main` the next push publishes a new build. On a tag, after the five days: while the tag has no GitHub release, re-run all its jobs, or, once its image is published and `verify` has passed, job `package` alone, which builds the files again and which `attest` and `release` follow (`package` runs only after a `verify` that passed: when `verify` failed, re-run the failed jobs, and `package` follows it); either way a draft an earlier attempt left is published when its title and notes are this run's and it holds exactly the files built again, and otherwise stops `release` with its line and is deleted by hand. Once the release exists, `release_check` refuses a whole re-run and nothing is left to re-run but a failed `aliases`, which needs no artifact (a candidate's run has none). One case does not recover in its own run: a `release` recorded as failed although it made the release, re-run after the five days, compares the release with files `package` built again and stops unless they are the same bytes, and `aliases` runs only after a `release` that passed; another release's `aliases` then moves this version's aliases, as item 7 says. When nothing can be re-run (GitHub's 30 days or 50 re-runs are over, or `release_check` refuses the tag because a higher version of its `X.Y` is tagged), tag the next patch version. For a candidate it is the next candidate, while `vX.Y.Z` is not tagged; once it is, `release_check` refuses every candidate of it and there is nothing left to recover. The third limit is job `release`'s: no request compares and publishes at once, so a draft a person changes between the job's comparison and its publication is published as changed; the job compares again after it publishes, stops on the difference and deletes nothing, so the changed release stays public: do not edit this tag's release by hand while the `release` job runs.

Nothing is published from a pull request. Actions are pinned to commit SHAs, and Dependabot updates them, the base images and `uv.lock` weekly.

### When a run stops

Every stop the workflow writes itself prints one line that starts with `::error::`; a command that fails by itself (a pull, a call to GitHub) prints its own error and has no row. The table names each by its words, with `…` where the line gives a name or a number, up to where it says what to do. `scripts/release_check.py` and `scripts/oci_archive.py` print their own reasons after `release refused:` and `the build's archive is refused:`.

| The run says | What it means | Next step |
| --- | --- | --- |
| `pip-audit could not audit the dependencies (exit …)` | pip-audit itself failed, before it judged anything (its own words are in the log above) | Re-run the job once the cause the log names, often the network, is gone. |
| `no gitleaks archive is pinned for a runner of …` | The runner is neither x64 nor arm64, the two whose archives are pinned | Name an x64 or arm64 runner in `CAIRN_GATE_RUNNER`, or unset it. |
| `no oras archive is pinned for a runner of …` | The runner is neither x64 nor arm64, the two whose archives are pinned | Name an x64 or arm64 runner in the variable of that job's runner, or unset it. |
| `no trivy archive is pinned for a runner of …` | The runner is neither x64 nor arm64, the two whose archives are pinned | Name an x64 or arm64 runner in `CAIRN_GATE_RUNNER`, or unset it. |
| `…:… could not be read, so main's run has not published … (it is still running, publishing is off, or it built another commit of that push)` | The tag's commit has no `sha-<commit>` image yet | Re-run the tag's workflow once `main`'s run for that commit has published it, or tag a commit it did. |
| `gh is not on this runner, so main's attestation of … cannot be checked` | The gate's runner has no GitHub CLI | Install `gh` 2.102.0 or later on the runner `CAIRN_GATE_RUNNER` names, or unset it, then re-run the job. |
| `gh on this runner is …, before 2.102.0, which fixed how gh attestation verify matches --signer-workflow and --source-ref, so it is not trusted with main's attestation` | The runner's `gh` predates the fix of both advisories | Update `gh` on the runner `CAIRN_GATE_RUNNER` names, or unset it, then re-run the job. |
| `… has no attestation signed by this repository's ci-cd.yml for a run on refs/heads/main at …, so nothing shows it is main's image of this commit` | Job `attest_main` of `main`'s run for that commit has not attested this digest | Re-run `main`'s failed jobs for that commit until `attest_main` passes, then re-run the tag's workflow; never promote the digest by hand. |
| `gh attestation verify could not check …: the tool failed or GitHub's API could not be reached, as its own words above say` | The check did not run to an answer | Re-run the job. |
| `the registry's tags do not list ci after the push` | The runner's own registry did not keep the push | Re-run the job. |
| `…:… is sha256:…, not the build's …` | A registry holds another digest under the tag just written: the runner's own one in a scan or rehearsal, GHCR in `publish` | In a scan or rehearsal, re-run the job; in `publish`, find who wrote the tag before re-running anything. |
| `the copy read back from the registry is … (index, amd64, arm64), not the build's …` | oras copied something other than the build | Release nothing; re-run the job, and if it repeats, the copy step is wrong: fix it on a branch. |
| `… is a …, not one image` | A platform's digest names an index, not an image | The archive is wrong: re-run `image_build` and the jobs after it. |
| `… is built for …, not …` | The image's own config is for another platform | The archive is wrong: re-run `image_build` and the jobs after it. |
| `the tags asked for […] include a sha- tag, but a tag run writes no sha- tag: its image is main's` | The workflow's tag rules give a tag run a `sha-` tag | Fix the tag rules of `publish` on a branch; nothing was pushed. |
| `the tags asked for […] do not include …` | The workflow's tag rules lost the tag this run must write | Fix the tag rules of `publish` on a branch; nothing was pushed. |
| `the tags of … could not be listed, so … is not pushed: …` | GHCR did not list the package's tags (or, in a fresh fork, there is no package yet) | Re-run the job; in a fresh fork, create the package first. |
| `…:… is sha256:… already, not this run's build …: …` | The version, or the commit's `sha-` tag, is in the registry with another digest, and it never moves | On `main`, re-run only the failed jobs of the run that published it; for a version, tag the next version. |
| `…:… is written, but the head of main could not be read, so …:… is left as it was` | The first tag is pushed and `main` is not moved | Re-run the job; it moves `main` only if this commit is still the head. |
| `The publish job reported no image digest` | `publish` did not run to its end, or was skipped | Re-run `publish` and the jobs after it. |
| `The publish job reported no digest for the amd64 image` | `publish` did not run to its end | Re-run `publish` and the jobs after it. |
| `docker pull … with no login failed (exit …), so nobody can pull it as the run guide says now` | The package is not public, or GHCR failed | Make the package public in its settings on GitHub, then re-run the job. |
| `…, pulled with no login, is … by the digest the pull printed, not …, the digest publish pushed, so the tag was moved after publish` | Someone moved the tag after `publish` wrote it | Release nothing from this run; re-run the job once the package's page shows the tag at the digest `publish` pushed. |
| `package's artifact holds […], not one wheel, one source archive, the text and SHA256SUMS of …` | `package` handed on other files than the release's | Re-run `package` and the jobs after it; if it repeats, fix `package` on a branch. |
| `SHA256SUMS does not list exactly the wheel, the source archive and the text of …` | `package`'s checksums do not cover the release's files | Re-run `package` and the jobs after it; if it repeats, fix `package` on a branch. |
| `a file of package's artifact does not match its checksum in SHA256SUMS` | A file changed after `package` summed it | Release nothing; re-run `package` and the jobs after it. |
| `the repository's releases could not be listed, so whether … has one is not known` | GitHub's API did not answer | Re-run the job. |
| `the GitHub release for … is a draft that an earlier attempt of run … left unfinished, which this job neither deletes nor publishes` | A draft for the tag carries this run's mark and does not hold exactly the files this run attested: an earlier attempt's upload was cut, and it may hold none of them, some, or one cut off | Delete it on the repository's releases page and re-run the job, which makes the release again (once the five days the run's files are kept have passed, re-run `package` instead, which runs only after a `verify` that passed and which `release` follows). Do not publish it by hand: the job then finds a public release it cannot finish. |
| `the GitHub release for …, …, carries run …'s mark and differs from what that run wrote in its …` | The release's title is neither the tag nor empty, or its notes are not, byte for byte, the text the job wrote: a person edited them, or saved the notes again in GitHub's web form. The line says which, and whether the release is a draft or public; a public one was changed after the job compared it, or after an earlier attempt published it, and stays public as changed | A draft: delete it on the repository's releases page and re-run the job, which makes the release again. A public one: put back what the job wrote (the tag as the title, or none) and re-run the job, which then compares it again and writes nothing; left as it is, the job stays failed, and a release tag's `aliases` then runs with another release, as item 7 says. |
| `the files of the GitHub release for …, …, could not be downloaded, so they were not compared and nothing shows that they differ` | GitHub did not give the job the release's files to compare, before the publication (the line says a draft) or after it (public): that says nothing about the files | Re-run the job, which starts from what is listed then: it compares this run's draft and publishes it, or compares the published release. Delete nothing, unless a draft's download fails at every re-run: such a draft may hold a file whose upload was cut, which GitHub may not serve, and is then deleted on the releases page and the job re-run, as for an unfinished draft. |
| `GitHub does not list the release for … as this job has just left it` | Just after the job made the draft, or published it, the listing shows no release for the tag, or still a draft: a person deleted it in that moment, or the listing did not show the job's own write | Re-run the job, which starts from what is listed then: it makes the draft if there is no release, publishes this run's draft, or compares the published release. It never makes a second release in one attempt. |
| `a GitHub release for … exists and is not one this run can finish: …` | A release for the tag exists that this run did not make, or one it made that has changed | Look at it on the repository's releases page, delete it if nobody should publish it, and re-run the job. |
| `gh listed 1000 releases, the most it asks for, so a higher one may be missing` | The repository has reached the cap the workflow asks GitHub for | Raise the cap here and in `scripts/release_check.py` together on `main`, then run `aliases` in the run of a commit that has it. |
| `Set these secrets on the production environment:…` | Deploy has no value for the secrets it names | Set them on the GitHub environment `production`, then re-run `deploy`. |

## Planned
- Entity resolution: knowing that two names in two notes are one thing. Today the linter takes a hand-written list of names and aliases (`--entities`), and the compiler resolves nothing.
- Semantic search: search today is SQLite FTS5 full-text search, matching words, not meaning.

Design notes: [docs/spec-v0.md](docs/spec-v0.md).
