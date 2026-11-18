# Run it yourself

This guide runs cairn on your own machine as one container: the same software as a hosted install, with no plans and so no plan caps. It covers starting it, making a key, signing in, showing your own name behind the sign-in, tidying pages without a model, connecting a model, where your data lives, backing it up and upgrading. You need Docker.

## Start it

Build from the public source, with no registry sign-in. These commands follow the main branch as it moves, not a pinned release. Run them in a folder where you keep source checkouts, then record `git rev-parse HEAD` with the build so you know which code you run:

```bash
git clone https://github.com/macayaven/cairn.git &&
  cd cairn &&
  docker build -t cairn:local .
```

The repository's README starts the same source build with Docker Compose instead (`deploy/compose.local.yaml`), and its commands are `docker compose exec cairn ...` where this guide's are `docker exec cairn ...`. Choose one path: the commands below use Docker directly.

```bash
docker run -d --name cairn --restart unless-stopped \
  -p 127.0.0.1:8765:8765 -v cairn-data:/data \
  cairn:local
```

What each part does:

| Part | What it does |
|---|---|
| `-d --name cairn` | Runs it in the background under the name `cairn`, which the commands below use. |
| `--restart unless-stopped` | Starts it again after a reboot, unless you stopped it yourself. |
| `-p 127.0.0.1:8765:8765` | Serves it on port 8765 of this machine only, so nothing else on your network can reach it. On Linux that needs Docker Engine 28.3.3 or later: earlier releases could let machines on the same network segment reach a port published this way. |
| `-v cairn-data:/data` | Keeps the database on a Docker volume named `cairn-data`, so it outlives the container. |
| `cairn:local` | The image you just built from this clone. |

One process serves the web pages, the REST API and the MCP endpoint that agents connect to. It runs as a user without root rights.

Words after the image name replace its default command, `serve --db /data/cairn.db --host 0.0.0.0`, so repeat that command whenever you add to it:

- **On a host directory instead of a volume:** make the directory first (`mkdir -p data`), then replace `-v cairn-data:/data` with `-v "$PWD/data:/data" --user "$(id -u):$(id -g)"`, so cairn writes as the directory's owner. A directory Docker has to create is root's, and cairn could not write to it.
- **Behind a reverse proxy or Tailscale Serve:** add `serve --db /data/cairn.db --host 0.0.0.0 --public-url https://cairn.example.net`. Form posts are then checked against that address, and the session cookie is sent over HTTPS only. An agent connected through OAuth is bound to that address: change it, and every such agent has to connect again. Add `--trusted-proxy` with the address or network the proxy's requests arrive from, so that cairn counts each client by its own address rather than the proxy's. In a container that is never 127.0.0.1, even for a proxy on the same machine: its requests arrive from the Docker network, whose range `docker network inspect bridge` shows. The compose stack in `deploy/` does this for Caddy. Without the option cairn trusts no proxy, and the limits on sign-in and on agent registration count every client as one.

When cairn listens on an address that is not loopback, as the default command does inside the container, and `--public-url` is not https, it says at start, in one line of its log, that keys, tokens and session cookies travel in clear text on that listener unless a proxy in front of it ends TLS. It starts all the same: published on 127.0.0.1 as above, nothing else on your network reaches it, and behind a proxy that ends TLS, `--public-url https://...` says so and the line goes.

## Make a key

cairn has no password. You sign in with an API key, and agents connect with one. Make the first key inside the container, copy it out, and delete the copy inside:

```bash
docker exec cairn python -m cairn.server key-create --db /data/cairn.db --label laptop --out /data/laptop.env
docker cp cairn:/data/laptop.env ./laptop.env
docker exec cairn rm /data/laptop.env
```

`laptop.env` holds one line, `CAIRN_API_KEY=` and the key. Only you can read the file. cairn keeps only a digest of the key, so if you lose the file, make another key; `key-revoke --db /data/cairn.db --label laptop` revokes every key with that label.

A key made this way can read and write every project, export the workspace, and open a web session. For an agent that needs less, add `--project NAME` once for each project it may reach, and `--read-only` for a key that never writes; a project the workspace does not have is refused, and no key is made. `key-list --db /data/cairn.db` prints each key with what it reaches, as the Keys page shows it.

A key made from a web session opened with another key records that key, and so does an agent approved from such a session: `key-list` ends the key's line with `made with` and that key's label, or `made with a revoked key` once that key is gone. `key-revoke` prints what the key made, one line for each key and each agent, and leaves it working. `key-revoke --db /data/cairn.db --label laptop --with-made` revokes the key, every key made with it or with a key it made, and every agent approved with any of them, in one transaction, and ends every web session opened with those keys. Keys and agents made before the upgrade that began recording this are never taken; revoking a key made before then prints one more line, saying what it made before then was not recorded.

The audit log records `session.opened` when a person signs in or a key opens a web session, with how (the provider's name, or `key`), and `agent.approved` when a person or a key approves an agent, whether or not the agent then connects.

cairn counts the wrong keys and tokens each client address presents, to the REST API, to MCP and to the sign-in form. Once an address has presented 30 in a minute, a wrong key or token from it is answered 429 in place of 401 until the minute has passed. A correct one is never refused by this limit, from any address, so a script that keeps retrying a revoked key does not lock out the people and agents behind the same address. A request that carries no key at all never counts. Behind a reverse proxy, the address is the one `--trusted-proxy` lets cairn read.

## Sign in

Open http://127.0.0.1:8765 and paste the key: the part of the line after `CAIRN_API_KEY=`. cairn then asks what you will use it for and makes a project for each use.

To connect an agent, give it the address and let it read the key from the environment. For Claude Code, put this in `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "cairn": { "type": "http", "url": "http://127.0.0.1:8765/mcp", "headers": { "x-api-key": "${CAIRN_API_KEY}" } }
  }
}
```

Then load the key into your shell and start `claude` from it:

```bash
set -a; . ./laptop.env; set +a
claude
```

Or store the key in Claude Code's own config with `claude mcp add --transport http cairn http://127.0.0.1:8765/mcp --header "x-api-key: $CAIRN_API_KEY"`. A key typed into a command is on that command's line while it runs, where other processes on the machine can read it, so prefer `.mcp.json`.

The [connect guide](connect.md) has the steps for every other client. Once signed in, **Keys** in the workspace makes a key for each agent or app, and a key can reach only the projects you choose.

## Under your own name, behind the sign-in

When people sign in through GitHub, Google or your own identity provider (`--sign-in`, set up as the main README's "Sign in with GitHub, Google or your own identity provider" says), five more options of `serve` show them your organisation's name in place of cairn's and keep everything else behind the sign-in:

| Option | What it does |
|---|---|
| `--wall` | A browser that is not signed in is shown the sign-in page and nothing else. |
| `--brand-name NAME` | Your name, 1 to 60 characters with no `<` or `>`, in place of cairn's wherever a browser that is not signed in can look, and in what people signed in and their agents are told. |
| `--brand-logo FILE` | Your logo, an SVG or a PNG of at most 256 KB, on the sign-in page and in each page's frame. It needs `--brand-name`. |
| `--brand-logo-alt TEXT` | What the logo says, for people who cannot see it. Without it, the brand name. It needs `--brand-logo`. |
| `--session-days N` | A sign-in lasts N days, from 1 to 365, and use renews it (below). |

Each of them needs `--sign-in`. A value cairn cannot use, or an option that would do nothing, stops `serve` at start, and the last line it prints names the option. The logo is read once, at start, by cairn's own user, so that user must be able to read the file. Give it to the container read-only, and name it after the image:

```bash
docker run -d --name cairn --restart unless-stopped \
  -p 127.0.0.1:8765:8765 -v cairn-data:/data \
  -v "$PWD/logo.svg:/brand/logo.svg:ro" \
  -e CAIRN_GITHUB_CLIENT_ID -e CAIRN_GITHUB_CLIENT_SECRET \
  cairn:local \
  serve --db /data/cairn.db --host 0.0.0.0 --public-url https://kb.example.net \
  --sign-in github --wall --brand-name "Knowledge Base" --brand-logo /brand/logo.svg --session-days 30
```

**What the wall shows.** A browser that is not signed in and asks for any page, including one a later version adds, is sent to the sign-in page; a form it posts gets a one-line refusal. What agents and scripts use answers as it does without the wall: `/mcp`, `/api`, `/healthz`, the OAuth endpoints and their `/.well-known/` addresses. The sign-in page, the sign-in addresses under `/auth/` and the files under `/static/` stay open too, since a person signing in needs them. Every answer to a browser that is not signed in, except what agents and scripts use, carries `X-Robots-Tag: noindex`, so search engines leave it out. An agent still connects by its URL alone: it opens the sign-in page once, and you sign in and approve it.

**What the brand covers.** With `--brand-name`, nothing a browser that is not signed in can get names cairn. The sign-in page shows your logo on a light plate that stays light in the dark theme, your name and one button per provider. There is no landing page, and the guides are for people signed in. Once signed in, a person sees your name in each page's title, in the bar at the top and in the footer, and your logo, if you gave one, in the bar and as the browser tab's icon.

**What people signed in and their agents are told.** The pages and the guides say your name where they would say cairn's. A command or a config that adds the server to an agent names the connection after your name in lower case, each run of other characters a dash (`knowledge-base` for "Knowledge Base"), and an export's file name starts with that same name. An agent is told your name too: the MCP server gives it as its name and as the subject of its instructions, and no tool's description names cairn. What a person or an agent wrote, such as a note, is shown as they wrote it. They still see cairn's name where it must be typed exactly, in commands: the module in `python -m cairn.server`, variables such as `CAIRN_API_KEY`, and the files `cairn.db` and `cairn.env`. An agent still meets it where machines read it: its tokens start with `cairn.at.` and `cairn.rt.`, a refusal over MCP or the REST API names the note guard's rule `cairn_credential`, and a page tidied without a model says `"arranged_by": "cairn"` in its metrics, as it is recorded. This guide is not shown under a brand: `/docs/run` answers as a guide that does not exist, and no page links it, since it documents cairn for you and you have the repository's copy. The logs, the command line and the files on your machine keep cairn's name.

**What changes name.** Under a brand the session cookie is `__Host-session` in place of `cairn_session`, the cookie that carries a sign-in to the provider and back is `__Host-signin` in place of `cairn_signin`, and the OAuth scope is `memory` in place of `cairn`. The `__Host-` prefix keeps other hosts under your domain from setting either cookie; when `--public-url` is plain http, as on localhost, browsers refuse the prefix and the cookies are `session` and `signin`. So the day you switch a brand on, or off, everyone signed in signs in once more. Agents keep working: cairn takes either name of the scope from an agent, whichever it connected with.

**How long a sign-in lasts.** Without `--session-days`, a sign-in lasts 12 hours and use does not renew it. With `--session-days N`, it lasts N days, and a use more than a day after it was made or last renewed makes it last N days from then. So with `--session-days 30` a browser used at least once every four weeks stays signed in, across restarts of the server; with `--session-days 1` that use never comes in time, and a sign-in lasts one day. Switching workspace and taking an invitation make a sign-in as long. When you lower `--session-days` or remove it, `serve` cuts every sign-in at start so that it ends at most the new length from then; it never lengthens one, and keys and agents are not touched. To end a person's sign-in before then, run `docker exec cairn python -m cairn.server session-end --db /data/cairn.db --email ada@example.test`; it ends their sign-ins in every workspace and prints how many. Their keys and agents go on working until you end them on the **Keys** and **Agents** pages.

**Several browsers.** A person can be signed in on several browsers and machines at once, each with a sign-in of its own, and several sign-ins can be under way in one browser, in two tabs say: up to five at a time, each for ten minutes. When the sign-in page says "This sign-in is not valid any more", nobody was signed in or out by it: starting again signs the person in, and every sign-in they already have, in that browser or any other, stays.

## A private test instance

`--test-instance` tells people who are not signed in that this install is a private test instance. It needs `--sign-in` and one of four limits: `CAIRN_ALLOWED_EMAILS`, `--allowed-email-domain`, or, when your own identity provider is the only one, `CAIRN_OIDC_ALLOWED_GROUPS` or `--closed-directory`. Without one, `serve` stops at start, and the last line it prints names the option.

The sign-in page says that sign-in is limited to approved testers, and links to this guide before the sign-in buttons. So does the landing page, which an install without `--join default` shows to visitors. In the guides, the link at the top reads **Tester sign-in**. With `--wall`, the sign-in page links to no guide. Under a brand, a visitor sees the same pages as without the option.

The option changes what visitors are told, not who can sign in. An invitation does not let in an address that `CAIRN_ALLOWED_EMAILS` and `--allowed-email-domain` leave out, so list a tester's address before you invite them.

## Tidy pages without a model

A page grows note by note: a later note's claims go at its end, even when they belong under a heading the page already shows, and a line that two notes both have under the same heading shows twice. cairn tidies pages by itself: it puts each heading's claims together and folds the claims that repeat. It needs no model, no key and no budget, and nothing leaves the container. Every word stays the sources' own, the version before stays readable, and a tidied page says "Arranged by cairn". A tidy is a new version of the page: where `serve` runs with `--plans` and a plan keeps only the last versions of each page, it counts toward them like any other version, and the oldest goes as it would for a note.

It is on by default: cairn then tidies a page each time a note arrives on it, a moment after the note. A model connected with `--model-env` (below) arranges pages in its place.

After an upgrade from a version that did not tidy, the pages already there are tidied as notes arrive on them. To tidy them all in one pass, run `docker exec cairn python -m cairn.server arrange --db /data/cairn.db --account acct_default --tidy`; it prints one line per page, never a claim's words. Where `serve` runs with `--plans`, give the same file to this command too: it refuses to run without it.

To switch it off, replace the container, as an upgrade does; the data stays on the volume:

```bash
docker stop cairn
docker rm cairn
```

and start cairn again, repeating the default command with `--no-tidy`:

```bash
docker run -d --name cairn --restart unless-stopped \
  -p 127.0.0.1:8765:8765 -v cairn-data:/data \
  cairn:local \
  serve --db /data/cairn.db --host 0.0.0.0 --no-tidy
```

With the compose files in `deploy/`, add `--no-tidy` to `CAIRN_EXTRA_ARGS` in `deploy/.env` and run `docker compose up -d`. Pages already tidied keep their versions.

## Connect a model

cairn calls no model unless you connect one. A connected model puts each page's claims in order and says which ones repeat each other; the [privacy guide](privacy.md) says what it receives. cairn speaks two formats: an OpenAI-compatible endpoint (llama.cpp, vLLM, Ollama or a hosted gateway) and Anthropic's Messages API. Put the settings in a file, under a prefix of your own (here `ARRANGER`), and pick one of these two.

`openai.env`, for an OpenAI-compatible endpoint:

```text
ARRANGER_PROVIDER=openai
ARRANGER_BASE_URL=https://llm.example.net/v1
ARRANGER_API_KEY=your-endpoint-key
ARRANGER_MODEL=your-model
```

`anthropic.env`, for Anthropic's Messages API, here with Claude Haiku 5.5:

```text
ARRANGER_PROVIDER=anthropic
ARRANGER_API_KEY=your-anthropic-key
ARRANGER_MODEL=claude-haiku-5-5
```

- `ARRANGER_PROVIDER` is `openai`, the default, or `anthropic`.
- `ARRANGER_BASE_URL` is the endpoint's address, ending in `/v1`. Leave out `ARRANGER_BASE_URL` for Anthropic's own API; cairn then needs the key, and does not start without it. Set it for another endpoint that speaks Anthropic's format, which may need no key.
- The address is https, or plain http to the container itself only (localhost, 127.0.0.1 or [::1]), so the key and the claims never cross a network in clear. A model your host serves over plain http is refused at start: serve it over https, as Tailscale Serve or a reverse proxy does.
- `ARRANGER_MODEL` is the model's name as the endpoint knows it.
- In Anthropic's format cairn switches thinking off, since thinking uses up the same token bound as the answer. For a model that refuses that, add `ARRANGER_REQUEST_EXTRA={"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}` to the file; for an endpoint that does not know the field, `{"thinking": null}` leaves it out.

The model is sent a page's claims: the page's title, each claim's words and the heading it sits under. A page that holds a note read from a Google source (Google Meet, Google Calendar, Gmail or Google Drive) or from a connector of your own, or a note whose `source_ref` says it came from a meeting, a mail thread, a calendar, a document or a to-do list (the forms the sources guide gives), is not sent unless the file also says `ARRANGER_SOURCE_NOTES=send`, which sends it except in a project its people keep from every model. Google's rules for apps that read its users' data limit passing that data on to others, and whether your model may read it is yours to decide: a model on your own machine is a different case from one a provider hosts. A page that is not sent is still tidied, without the model, as when no model is connected, except in a workspace whose plan's model budget is spent: cairn then reads none of its pages until the budget is renewed. `ARRANGER_SOURCE_NOTES` is `withhold`, the default (also when it is unset or blank), or `send`, written in lower case; any other value stops cairn at start. When cairn starts with a model, its log says which of the two it runs with.

Do not name the variables `ANTHROPIC_API_KEY` or `ANTHROPIC_BASE_URL` in a shell where Claude Code runs: Claude Code and other tools read those names, and would send their own requests with that key or to that address. That is why the prefix is not `ANTHROPIC`: use a prefix of your own.

Then replace the container, as an upgrade does; the data stays on the volume:

```bash
docker stop cairn
docker rm cairn
```

and start cairn again with the file, repeating the default command with `--model-env` and the prefix:

```bash
docker run -d --name cairn --restart unless-stopped \
  -p 127.0.0.1:8765:8765 -v cairn-data:/data --env-file ./anthropic.env \
  cairn:local \
  serve --db /data/cairn.db --host 0.0.0.0 --model-env ARRANGER
```

`--env-file` hands the settings to the container, so the key is on no command line. Write each value as it is, without quotes: Docker keeps quotes as part of the value. Anyone who can run `docker` on this machine can read the settings with `docker inspect cairn`. A setting cairn cannot use stops it at start, and `docker logs cairn` names the variable, never its value.

## Sources the server reads

A source (Langfuse, Granola, Google Meet, Google Drive, Google Calendar, Gmail, Linear, GitHub or a connector of your own) is read by the server itself with credentials from its environment, so you add or remove it on the command line: `python -m cairn.server source-add --help` lists each kind's settings. After that the workspace decides: an owner or an admin switches it on or off under **Workspace**, then **Sources**, and chooses the projects it writes into. A Langfuse source is on as soon as it is added; a kind that starts off reads nothing until someone switches it on, and then only what happens from then on. On the command line, `source-enable --name NAME --project PROJECT` and `source-disable --name NAME` do the same. The [sources guide](sources.md#sources-the-server-reads) says more.

## Slack

`serve --slack` lets each workspace set up a Slack app of its own: people then file a Slack message as a note, write a note, and ask a bot to find something, each as the person of the workspace they linked their Slack user to. The [connect guide](connect.md#from-slack) says how an owner sets it up and what people do, and the [privacy guide](privacy.md#slack) says what it keeps.

`--slack` needs `--sign-in`, since a Slack user is linked to a person who signs in, and an `https://` `--public-url`, since Slack calls the server at `<public url>/integrations/slack/<install>/events`, `/commands` and `/actions`. It also needs `CAIRN_SLACK_KEY`, 32 random bytes in base64, the key every workspace's Slack secrets are sealed with: make it with `openssl rand -base64 32`. Without one of the three, `serve` stops at start, before it opens the database, and the last line it prints names what is missing, never the key. It is not offered under a brand yet: with `--brand-name`, `serve` stops the same way, since what the bot says, the shortcut's name and the command's name would name the software.

Slack cannot reach a local install that listens on 127.0.0.1, as the commands above start it, so this version is for an install with a public address, behind a reverse proxy that ends TLS.

Keep the key apart from the database and its backups, as you keep the environment file: a copy of the database holds each workspace's signing secret and bot token sealed, and without the key it opens neither. When the key is lost or changed, no secret sealed with the old one opens, and Slack's requests to the server are refused: each workspace's owner removes Slack on the **Sources** page, sets it up again and pastes both secrets. A backup taken before Slack is removed still holds what came through it: its notes, for as long as the backup is kept, and both secrets, sealed, which the key still opens.

## Notes compiled at once

`serve` compiles at most two notes at a time. A note that arrives while two are compiling waits its turn, in the order notes came, and is answered when its own compile is done, as before. Reads, `/healthz`, sign-in and everything else that is not a note's compile never wait behind them. Notes from sources, and the waiting notes `serve` files by itself, take their turns too. `--compiles N` sets how many at a time, 1 or more: a larger N lets more notes compile at once on a machine with the processors and memory for them, and N must be a whole number or `serve` stops at start.

## Notes waiting for their page

A note is stored first, then filed on its page. Until its outcome is recorded it is waiting: it is kept, but no page cites it yet and a search does not find it. A server stopped between the two leaves a note waiting, and so does a note whose page changed under it more often than a note is tried at once; the `note` call then answers `202` with the status `waiting` (an agent's tool gets the same as a result, not an error), and the note should not be sent again. `serve` files the waiting notes by itself: once it answers requests, then every three minutes, a few of each workspace's at a time, each under its workspace's plan. A note that keeps failing is tried five times at most and then left waiting; the log names it by its id, never by its text.

To see them:

```bash
docker exec cairn python -m cairn.server waiting --db /data/cairn.db
```

Each line names a note's id, its workspace, its project, when it was stored and how many tries it had. `--workspace NAME` lists one workspace's notes, and `--json` prints them as JSON. It never shows a note's text or title. "No note is waiting for its page." means there are none.

## How much history a page keeps

Each save of a page makes a new version of it, and each version holds the whole page. `serve` keeps the newest 50 versions of each page: a save that takes a page past 50 removes its oldest versions in the same step, and a save that fails removes nothing. Reading a page never removes a version. A page's latest version is always kept, and its address and the notes its claims cite stay as they are. `--keep-versions N` keeps the newest N (a whole number, 1 or more), and `--keep-versions all` keeps every version. Where `serve` runs with `--plans`, each plan's `versions_per_page` says how many versions a page keeps, and `--keep-versions` is refused.

When `serve` starts on a database whose pages hold more versions than it keeps, it says so in one line, with how many pages and that `--keep-versions all` keeps them. It removes nothing then: a page's older versions go when the page is next saved, or at a compaction.

`serve` records in the database how many versions it keeps, and the last `serve` to start is the one recorded. `compact` removes at once what that record says no page keeps, then rewrites the file so the space goes back to the disk:

```bash
docker exec cairn python -m cairn.server compact --db /data/cairn.db
```

Its first line says how many versions it removed and by what rule. On a database no `serve` of this version has run on, it removes no version and says so. With plans, each workspace keeps what its plan keeps; a workspace on a plan the plans file does not name keeps every version, and `compact` names it on a line of its own.

## A graph to explore

`serve --graph-explorer` makes a project's **Graph** page one to explore: its pages or its notes a batch of at most 20 at a time, with **Next batch** for the rest, a search within the view, a page's claims, the passages a claim cites, and a page for each note. That page is the only one that runs a script, cairn's own, and it works without it. Without the option the graph page is drawn by the server alone and runs no script.

## Numbers to watch

`stats` prints how much the install holds, and it only reads:

```bash
docker exec cairn python -m cairn.server stats --db /data/cairn.db
```

It shows the schema's number, the size of the database and of its write-ahead log, the free space on its disk, and, for the install and for each workspace by its id, how many projects, notes, pages, page versions, notes waiting for their page, keys and connected agents there are, when the newest note was stored, and how many sources of each kind are on and how many failed their last run. `--json` prints the same as one JSON object. It never prints a note's text, a title, a name, an address, a key's label or why a source failed. It runs beside a running server and changes nothing in the database. It refuses a database of an older version with one line that says to start the server once on it, since the server is what brings a database up to date.

`serve` also writes the install's numbers to its log: one line at start, then one every 15 minutes, `stats` followed by a JSON object with the install's totals and `requests`, how many responses it sent since the last line, by class (`2xx`, `3xx`, `4xx`, `5xx`, and `429`, which counts in `4xx` too). The line holds no workspace's id. `--stats-every MINUTES` changes how often, and `--stats-every 0` turns it off. No address of the server serves these numbers.

Low disk means the free space on the database's disk is under twice the database's size. Then the line is a warning, followed by one that says so: writes can fail when the disk is full, and a compaction or a backup copy of the database cannot run, since each needs about as much space again as the database. Free some space or move the database to a larger disk. Nothing else changes: the server keeps answering, and `/healthz` answers as before.

## Where your data lives

Everything cairn knows is in one SQLite database, `/data/cairn.db`, on the `cairn-data` volume. Beside it, while cairn runs, are its two working files, `cairn.db-wal` and `cairn.db-shm`, and `cairn.db.serve.lock`, an empty file by which one server holds the database. A server that is killed, rather than stopped, leaves them there, and the next start uses them. Nothing else is kept: the container holds no data of its own, so removing it and starting another on the same volume loses nothing.

A command that makes the database makes it readable by its owner only (mode 0600), and its two working files with it, and makes each folder it needs for it 0700. A database that others on the machine can read is left as it is: each command, and `serve` when it starts, says so in one line that names the file's mode and the `chmod 600` that makes it private. A `--db` that is a symbolic link is refused.

`docker volume inspect cairn-data` shows where Docker keeps the volume on disk. The [privacy guide](privacy.md) says what the database holds and for how long.

## Back it up

There are two kinds of backup.

**An export** holds the content of your workspace: every project's description, every note exactly as it was written, and every page version cairn keeps. It does not hold keys, agents' connections, sets, other settings, sources or the audit log, so after a restore you set those up again; of the settings it holds one, each project's **Models** choice. Take one from **Workspace**, then **Data and deletion**, or from the command line while cairn runs:

```bash
docker exec cairn python -m cairn.server export --db /data/cairn.db --account acct_default --out /data/export.zip
docker cp cairn:/data/export.zip ./cairn-export.zip
docker exec cairn rm /data/export.zip
```

`acct_default` is the workspace that a key made with `key-create` signs in to. To bring an export back, write it into the container as cairn's own user and import it (a file put there with `docker cp` is root's, and cairn could not read it):

```bash
docker exec -i cairn sh -c 'cat > /data/export.zip' < ./cairn-export.zip
docker exec cairn python -m cairn.server import --db /data/cairn.db --account acct_default --in /data/export.zip
docker exec cairn rm /data/export.zip
```

The workspace must not already have a project with the same name. Imported projects are private: only the workspace's owners reach them until you share them again. Each arrives at **Models** where the file says, and a project of Personal notes at never whatever it says. A file exported before cairn carried that choice does not say it, and the import says so: each then starts at **Models** as a new project of its kind does, a project of Personal notes at never, the others at allowed.

**A copy of the database** holds every project with its notes and pages, the people, and a digest of each key. It does not hold the keys themselves, which are in the files you wrote them to, nor the environment file you start cairn with and the credentials of sources in it. Keep those somewhere else. Note the image cairn runs from (`docker inspect cairn --format '{{.Image}}'` prints it): a copy is restored with the image that made it. The commands below stop cairn so the copy is whole, copy into a new folder every time, and start cairn again whether or not the copy worked:

```bash
(
  set -e
  BACKUP_DIR=$(mktemp -d "$PWD/cairn-backup.XXXXXX")
  restart_cairn() {
    BACKUP_STATUS=$?
    trap - EXIT
    if ! docker start cairn
    then
      printf 'Restart failed. Run docker start cairn and inspect its status. Backup directory: %s\n' "$BACKUP_DIR" >&2
      exit 1
    fi
    if [ "$BACKUP_STATUS" -eq 0 ]
    then
      printf 'Backup saved in %s\n' "$BACKUP_DIR"
    fi
    exit "$BACKUP_STATUS"
  }
  trap restart_cairn EXIT
  docker stop cairn
  if ! docker cp cairn:/data/. "$BACKUP_DIR/"
  then
    printf 'Copy failed. This is not a successful backup: %s\n' "$BACKUP_DIR" >&2
    exit 1
  fi
)
```

A copy that fails is not a backup: the commands say so and end with an error. Run them again, and they make a new folder. If cairn does not start again, run `docker start cairn` and look at `docker ps -a`.

## Restore a copy of the database

Take the folder a backup printed, and the image that made it: the image you noted, while this computer still has it, or `cairn:local` built from the same commit. Do not start a copy with a newer image first. A newer cairn may move the database forward, and the older one then refuses it.

Put the folder's path in the first line and the image in the second. The commands copy the backup into a new folder and start a second container on that folder, so the backup and the data of the running cairn stay as they are:

```bash
BACKUP_DIR="$PWD/cairn-backup.REPLACE_ME"
CAIRN_IMAGE=cairn:local
(
  set -e
  test -f "$BACKUP_DIR/cairn.db"
  RESTORE_DIR=$(mktemp -d "$PWD/cairn-restore.XXXXXX")
  cp -R "$BACKUP_DIR/." "$RESTORE_DIR/"
  docker run -d --name cairn-restored --pull never \
    -p 127.0.0.1:18765:8765 -v "$RESTORE_DIR:/data" \
    --user "$(id -u):$(id -g)" "$CAIRN_IMAGE"
)
```

Open http://127.0.0.1:18765 and sign in with a key you had before: the copy holds its digest. Add to the `docker run` line the environment file and the options your cairn runs with. If the port or the name `cairn-restored` is taken, choose others. If the copy or the start fails, the backup and the running cairn are as they were, and you can try again.

## Upgrade

These commands are for the direct Docker install above. For the Compose install, use the README's [Update steps](https://github.com/macayaven/cairn/blob/main/README.md#update) from `deploy`.

Take a [cold database backup](#back-it-up) first, and note the running image (`docker inspect cairn --format '{{.Image}}'`) and source commit (`git rev-parse HEAD`). From the same clone's top folder, fetch the new code and build while cairn still runs, then replace the container. The data stays on the volume:

```bash
(
  set -e
  git pull --ff-only origin main
  docker build -t cairn:local .
  docker stop cairn
  docker rm cairn
)
```

Then run the same `docker run` command you started it with, using `cairn:local` and keeping its volume, environment files, ports and options. A newer version may update the database when it starts. An older image refuses to open a database a newer one has updated, so going back to an earlier version means going back to a backup taken before the upgrade.

An upgrade keeps every page's address, so links to a page and the claims cited from it keep working. Since pages are filed by topic, a page that held notes of two topics before the upgrade keeps them both and takes the topic of its latest title: later notes of that topic join it, and later notes of the other topic build a page of their own. A note that no page cites when you upgrade, because it was stored and never filed or because the gate rejected it, waits for its page, and `serve` files it: it gets its page, or its outcome is recorded again and it waits no more.
