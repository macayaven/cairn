# cairn

cairn is a memory service for AI agents. Agents write notes to it over MCP, and it compiles them into pages. Every claim on a page is a passage copied from a note, and it links to the exact lines it came from. cairn checks each page against its notes before it saves it, and keeps the last good version when a check fails. It runs as one container on your own computer, or on a server where people sign in, share a workspace and keep a project private. cairn's own code is open source under the MIT License. **Pre-alpha, built test-first.**

## Run it on your computer

You need Docker with Compose 2.24 or later (`docker compose version` prints it) and a clone of this repository. The commands below run from the clone's `deploy` folder.

```bash
cd deploy
echo 'COMPOSE_FILE=compose.local.yaml' > .env
docker compose up -d --build
```

The `.env` line makes every `docker compose` command in this folder use `compose.local.yaml`: cairn alone, with no domain and no proxy. `--build` builds the image from this clone and names it `cairn:local`. Keep it on the first run: without it Compose starts whatever image already has that name on this computer, such as one an earlier clone left, and the code you just fetched is not what runs. The build takes a few minutes. cairn then answers at http://127.0.0.1:8765, on this computer only; nothing else on your network can reach it. If another program already uses port 8765, choose a free port, write it into `.env` before `up`, and use it wherever this guide says 8765. For port 18765:

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

`deploy/laptop.env` now holds one line, `CAIRN_API_KEY=` and the key, and only you can read it. git ignores the file. cairn keeps only a digest of the key: if you lose the file, make another key.

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

Run the first line from `deploy`, or give the path to `deploy/laptop.env` from where you are. [The connect guide](src/cairn/guides/connect.md) has the steps for Codex, Cursor, VS Code, Gemini CLI and other clients.

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

A copy of the database holds everything, keys included. Stop cairn first, so the copy is whole, and give each backup a folder of its own, named with the time. Copying into a folder that already holds a backup puts the new one inside the old one and leaves the old database on top:

```bash
docker compose stop cairn
docker compose cp cairn:/data/. "./cairn-backup-$(date +%Y%m%d-%H%M%S)"
docker compose start cairn
```

To bring an export back into a cairn whose workspace has no project of the same names, write it in as cairn's own user, import it and delete the copy:

```bash
docker compose exec -T cairn sh -c 'cat > /data/export.zip' < ./cairn-export.zip
docker compose exec cairn python -m cairn.server import --db /data/cairn.db --account acct_default --in /data/export.zip
docker compose exec cairn rm /data/export.zip
```

An export brings back no key, so in a new install make a key again, as above. Keep the backups somewhere other than this computer. [Run it yourself](src/cairn/guides/run.md#back-it-up) says what an export holds and what it leaves out.

### Update

Take a backup first. Then fetch the new code, build the image and replace the container. The volume stays:

```bash
git pull
docker compose build cairn
docker compose up -d
```

A newer cairn may update the database when it starts, and an older one then refuses to open it. Going back to an earlier version means going back to a backup taken before the update.

### Stop it

`docker compose down` stops cairn and removes the container. The volume stays, and `docker compose up -d` starts cairn on it again.

## Run it for other people

[deploy/README.md](deploy/README.md) runs cairn on a server that a domain points at, behind Caddy, which gets the certificate. It adds backups to object storage with Litestream, sign-in with GitHub, Google or your own identity provider, and caps per workspace from a plans file the operator writes. [Run it yourself](src/cairn/guides/run.md) runs the published image with `docker run` instead of Compose.

## Develop

You need [uv](https://docs.astral.sh/uv/). From the clone's top folder:

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

`runs/` is ignored by git. Tests come first: a change starts with a test that fails for the expected reason. Tests run offline, with a fake model and fake HTTP, and every fixture is synthetic. [CONTRIBUTING.md](CONTRIBUTING.md) says more, and [SECURITY.md](SECURITY.md) says how to report a vulnerability.

## Licence

cairn's own code is under the MIT License: see [LICENSE](LICENSE).

Third-party components keep the licences their owners gave them:

- The two typefaces in `src/cairn/static/fonts/` are under the SIL Open Font License 1.1. Their licence texts sit beside them, `src/cairn/static/fonts/atkinson-hyperlegible-mono.OFL.txt` and `src/cairn/static/fonts/atkinson-hyperlegible-next.OFL.txt`, and the image serves them with the fonts.
- The Python packages cairn installs are not in this repository; `uv.lock` names each one and its version. Most are under permissive licences (MIT, BSD, Apache-2.0). certifi, which carries the certificate authorities that HTTPS checks against, is under MPL-2.0. On Windows only, `mcp` also installs pywin32; read the licence files in its own distribution before you pass a Windows build on.
- In the image, each installed package keeps its licence file in its own folder under `/app/lib/python3.13/site-packages/`, the one whose name ends in `.dist-info`. The base image's Python keeps its licence at `/usr/local/lib/python3.13/LICENSE.txt`, and its Debian packages theirs under `/usr/share/doc/`.
- The files named `*-api.md` and `google-*.md` in `docs/` are notes on other services' public documentation, taken while cairn's readers for those services were written. Where they quote it, each quote has its page and the day it was read. The quotes remain their owners'; the notes around them are cairn's. Quotes from pages on developers.google.com and from GitHub Docs are excerpts used under the Creative Commons Attribution 4.0 International licence (https://creativecommons.org/licenses/by/4.0/), as those pages offer their text. What Google's Help Center, Granola's pages, Linear's pages and Langfuse's documentation say is given in cairn's own words, with the page linked.

## Guides

A running cairn serves these at `/docs`. The Markdown lives in the package, so the server and this repository show the same text.

| Guide | For | On a running server |
|---|---|---|
| [Connect an agent](src/cairn/guides/connect.md) | Claude Code, Codex, the Claude apps, ChatGPT, Cursor, VS Code, Gemini CLI and scripts, by URL or with a key | `/docs/connect` |
| [Patterns](src/cairn/guides/patterns.md) | Laying out projects and connections: many agents with one project, many projects, sets of projects per use | `/docs/patterns` |
| [Bring in what you already have](src/cairn/guides/sources.md) | Recipes for meetings, mail, calendar, documents and to-dos through an agent you already use, and sources the server reads itself (Langfuse traces, Granola meeting notes, Google Meet transcripts, the documents of a Google Drive folder, Google Calendar events, the Gmail messages a person labels, Linear issues once they are finished, the finished work of one GitHub repository, and any other system through a connector of your own), which owners and admins switch on and off | `/docs/sources` |
| [Security](src/cairn/guides/security.md) | Who reaches what, how sign-in and agent connections work, what the checks catch and what they do not | `/docs/security` |
| [Run it yourself](src/cairn/guides/run.md) | One container on your own machine: the `docker run` command, a key, signing in, where the data lives, backups and upgrades | `/docs/run` |
| [Privacy](src/cairn/guides/privacy.md) | What cairn stores, where and for how long, export and deletion, and a section for the operator to fill in | `/docs/privacy` |
| [Deploying](deploy/README.md) | Running cairn for other people behind a domain, with backups | |
| [Architecture](docs/architecture.md) and [decisions](docs/decisions.md) | Where the code can be extended, and the calls made along the way | |

## What exists today

| Piece | What it does | Checked by |
|---|---|---|
| Chunker | Splits Markdown into headings and citable passages, with heading paths and stable ids | `tests/acceptance/test_t1_spans.py`, regression tests, a held-out set |
| Fact extractor | Finds flags, paths, numbers with units, dates, versions, URLs, inline code and code blocks; code is scored per line | `tests/acceptance/test_t2_facts.py`, a synthetic gold set, a held-out set |
| `python -m cairn lint` | Scores any page set against its sources: retention by fact type, code-line retention, passage coverage, duplicates, misplaced facts; pages that cite passages also get provenance, grounding and attribution checks | `tests/acceptance/test_t3_lint.py`, `test_t3_multisource.py` |
| Store | Many workspaces and projects in one SQLite file, versioned pages, FTS5 search; keys, sessions and tokens stored only as digests; reads never write | `tests/acceptance/test_w1_store.py`, `test_h1_accounts.py` |
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

The model behind `--model-env` speaks the OpenAI chat format by default, or Anthropic's Messages format with `<PREFIX>_PROVIDER=anthropic` (the run guide shows both). Its address (`<PREFIX>_BASE_URL`) must be reached over https, or over http on the same machine (localhost, 127.0.0.1 or [::1]); any other address is refused at startup (exit 2), so the API key and the claims never cross a network in clear. `<PREFIX>_REQUEST_EXTRA`, a JSON object, adds fields to every request: a model that reasons before it answers runs into the answer bound and is refused, so its server is told not to (`{"chat_template_kwargs": {"enable_thinking": false}}` on llama.cpp and vLLM). A page that holds a note read from a Google source (Meet, Calendar, Gmail or Drive) or from a connector of your own, or a note whose `source_ref` says it came from a meeting, a mail thread, a calendar, a document or a to-do list (the sources guide gives the forms), is not sent to the model, and is tidied without it, unless `<PREFIX>_SOURCE_NOTES=send`, which sends it except in a project its people keep from every model; `withhold` is the default.

Try a model before you give it the worker: `evals/t4_compile.py --arrange`, with `--page-claims` once for each page size, arranges synthetic pages of several sizes and says how often the answer was valid, with tokens and seconds per page. Of six local models tried, one arranged pages of every size.

## Options of `serve` in a container

One image serves the web UI, the REST API and the MCP endpoint on port 8765. It runs as a non-root user and keeps its database on the `/data` volume. The compose files in `deploy/` start it, and [Run it yourself](src/cairn/guides/run.md), served at `/docs/run`, starts the published image with `docker run`, from the first key to backups and upgrades.

Arguments after the image name replace the default command, `serve --db /data/cairn.db --host 0.0.0.0`. With Compose they go in `CAIRN_EXTRA_ARGS` in `deploy/.env`, after the command the compose file already gives:
- **Behind a reverse proxy or Tailscale Serve:** add `--public-url https://cairn.example.net`. Form posts are then checked against that origin, and the session cookie is marked Secure. Add `--trusted-proxy` with the address or network the proxy's requests arrive from too. In a container that is the Docker network (`docker network inspect bridge` shows its range), never 127.0.0.1, even for a proxy on the same machine. No proxy is trusted otherwise, loopback included, and every client would share one count in the limits on sign-in and agent registration.
- **Other hostnames for `/mcp`:** add `--allowed-host NAME` for each one. Any other Host header gets 421.
- **A host directory instead of a volume:** run as the directory's owner, with `-v "$PWD/data:/data" --user "$(id -u):$(id -g)"`. If cairn may not write there, `serve` stops at start with one line that names the folder or the database file and says to run cairn as its owner.
- **No tidying:** add `--no-tidy`. cairn otherwise tidies each page a note arrives on, with no model ([the run guide](src/cairn/guides/run.md#tidy-pages-without-a-model)).

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

To let in only the people you name, with any provider, set `CAIRN_ALLOWED_EMAILS` to their addresses, separated by commas (`CAIRN_ALLOWED_EMAILS=ada@example.test,brook@example.test`). A person then signs in only when the email the provider verified is one of them, the whole address, ignoring case. It comes only from the environment, never from an argument; set but blank, holding an entry that is not one address, or set without `--sign-in`, it stops `serve`. It gates sign-in only: an open session, a key and an approved agent go on working. `session-end --db PATH --email ADDRESS` ends that address's open sessions. With `--allowed-email-domain` as well, either admits. deploy/README.md, "Only the people you name", says more.

With sign-in on, five more options put the install under your organisation's name: `--wall` shows a browser that is not signed in the sign-in page and nothing else, while `/mcp`, `/api` and OAuth answer as before; `--brand-name NAME`, `--brand-logo FILE` (SVG or PNG) and `--brand-logo-alt TEXT` put your name and logo in place of cairn's wherever a signed-out browser can look and in each page's frame; `--session-days N` keeps a sign-in for N days in place of 12 hours, and a use more than a day after it was made or last renewed starts the N days again. The run guide's "Under your own name, behind the sign-in" says what each covers and what changes name.

`--join default` puts everyone in the one shared workspace; the first person to sign in owns it. With GitHub or Google, which anyone can join, cairn refuses that unless you also limit it with `--allowed-email-domain example.com` (repeatable) or `CAIRN_ALLOWED_EMAILS`. For Google an account admitted by its domain must then also belong to that Google Workspace domain. Your own provider may allow public self-registration too, so `--sign-in oidc --join default` needs one of four limits: `--allowed-email-domain`, `CAIRN_OIDC_ALLOWED_GROUPS` (with `CAIRN_OIDC_GROUPS_CLAIM`, the claim that lists a person's groups), `CAIRN_ALLOWED_EMAILS`, or `--closed-directory`, by which you state that only your own people can get an account at that provider; without one, `serve` exits and names them. Each identity is recorded with its provider's issuer: if you point `CAIRN_OIDC_ISSUER` at another issuer under the same `CAIRN_OIDC_NAME`, a sign-in whose subject was recorded under the old issuer is refused until you act. A person at the new issuer whose subject is new and whose verified email is an existing user's still becomes that user, as with any provider, so only move to an issuer that verifies the same addresses for the same people. cairn stores no password and no provider token. Pass the variables to Docker with `-e CAIRN_GITHUB_CLIENT_ID -e CAIRN_GITHUB_CLIENT_SECRET` so their values stay out of the command line.

## MCP tools

| Tool | Does |
|---|---|
| `projects` / `new_project` | List projects, or create one |
| `note` | Store a note and compile it; searchable as soon as it returns |
| `recall` | Search one project or all of them |
| `page` | Read a page, or any earlier version |
| `evidence` | Show the exact source lines behind a claim |
| `overview` | Pages and note count of a project |

## Lint any page set

`python -m cairn lint` scores a set of pages against their sources: `uv run python -m cairn lint --help` lists its options. Exit codes: 0 clean, 1 violations or a threshold breach, 2 bad input.

## Score a model (T4 eval)

`evals/t4_compile.py` compiles every synthetic fixture in `tests/fixtures/` with each model, gates each page with `verbatim_gate` (the strict lint gate, plus every claim must be one passage it cites, whole: never cut, never joined; `--gate strict` or `--gate lint` names another), and scores every draft against all of the note's passages, rejected drafts included. The extractive baseline compiles every fixture first; a fixture it cannot save stops the run before any model is called. It prints a table per model next to the baseline, then each fixture against its baseline, and writes a JSON report to `private/t4/` (gitignored; the report holds the drafted claims, so `--out` elsewhere only on purpose). A provider failure is kept as its HTTP status and a fixed category, never the endpoint's text, and token means are marked incomplete instead of averaged when a call reported none. The exit code is 3 when the baseline failed a fixture and 4 when no model compile was saved; 1 when a model was skipped because its warm-up failed, and 2 for bad usage or a missing setting; otherwise a run that measured exits 0, however few drafts were saved. `--fail-under RATE` (0 to 1) exits 5 when a model saved a smaller share of its runs, and an `--out` outside `private/` prints a warning.

`uv run python evals/t4_compile.py --help` lists its options. The endpoint comes from `PREFIX_BASE_URL` and `PREFIX_API_KEY` (default prefix `CAIRN_LLM`), read from the environment or the env file. There is no default endpoint, except with `PREFIX_PROVIDER=anthropic`, which aims at Anthropic's API and needs the key. Without `--yes` the script only checks the configuration and prints the plan. `--probe` compiles one fixture per model. Each model is loaded in turn, so on a single-GPU server such as llama-swap a run evicts the model loaded there.

## CI/CD

`.github/workflows/ci-cd.yml` runs on pull requests, on pushes to `main` and to `v*` tags, and by hand. A draft pull request runs the tests and the infrastructure checks (items 1 and 2 below). A pull request that is ready for review also builds the image on both platforms and rehearses the stack and the host, and that is the gate a merge waits for. Marking a draft ready for review starts that run. Pushes to `main`, tags and manual runs run the whole gate too.

One exception: a change that touches only paths no image job reads builds no image, so a pull request runs items 1 and 2 alone and a push to `main` publishes and deploys nothing; the server keeps the image it runs. The list of those paths is `NO_IMAGE_WORK` in `scripts/ci_changes.py` (`docs/`, `tests/`, `evals/`, `examples/`, `CLAUDE.md`, `.gitignore`), and a first job, `changes`, runs that script. On `main`, the change is measured from the head of the newest run on `main` that published an image, read from GitHub's API, so a change whose run failed or whose deploy was refused still ships with the next push. Any other path, or a change whose paths cannot be known (a tag, a manual run, a first or forced push, no run that published, an earlier run on `main` still going, a failed lookup), runs the whole gate. Items 1 and 2 always run.
1. **Tests:** ruff and the test suite.
2. **Infrastructure and deploy files:** OpenTofu formatting and validation, the host and deploy scripts, the compose files, Caddy's own check of its configuration, and the restore drill on a fresh database.
3. **Image check:** builds the image for linux/amd64 and linux/arm64, then runs `scripts/smoke_container.py` against each.
4. **Stack rehearsal:** runs the shipped compose stack on the runner, with Caddy and Litestream, and restores a lost database into it (`scripts/rehearse_stack.py`).
5. **Publish:** once items 1 to 4 have all passed, pushes to `main` and `v*` tags publish one multi-arch image, with provenance and an SBOM, to `ghcr.io/<owner>/<repo>`. Tags are the version, `major.minor`, the branch and the commit.
6. **Verify and deploy:** on `main`, the published image is smoke-tested, and a `deploy` job that waits for approval moves the production VM to it ([infra/gcp/README.md](infra/gcp/README.md)).

Nothing is published from a pull request. Actions are pinned to commit SHAs, and Dependabot updates them, the base images and `uv.lock` weekly.

## Planned
- Entity resolution: knowing that two names in two notes are one thing. Today the linter takes a hand-written list of names and aliases (`--entities`), and the compiler resolves nothing.
- Semantic search: search today is SQLite FTS5 full-text search, matching words, not meaning.

Design notes: [docs/spec-v0.md](docs/spec-v0.md).
