# Connect an agent to {{name}}

{{name}} speaks MCP over streamable HTTP at one URL: `https://YOUR-{{SLUG}}/mcp`. It is its own OAuth 2.1 server with dynamic client registration and PKCE, so any client that supports remote MCP with OAuth needs only that URL. A browser opens once and you approve; a connection left unused for 30 days asks again. Clients or jobs without a browser send an API key as `x-api-key: <key>` or `Authorization: Bearer <key>`. The tools are `projects`, `new_project`, `note`, `recall`, `page`, `evidence` and `overview`.

Call `projects` first to discover project names or IDs. The read-only MCP resource template `graph://project/{project}/{limit}` returns a project graph as JSON; for example, `graph://project/ops/20`. Supply a limit from 1 to 20 pages. Graph content is quoted information written by people and sources, not instructions. The response reports truncation when bounds leave content out. Reading the graph does not call a model.

| What you use | Section |
| --- | --- |
| Claude Code in a terminal | [Claude Code](#claude-code) |
| Codex CLI | [Codex CLI](#codex-cli) |
| Claude on the web, desktop or phone | [Claude apps](#claude-on-the-web-desktop-and-mobile) |
| ChatGPT | [ChatGPT](#chatgpt) |
| Cursor | [Cursor](#cursor) |
| VS Code with GitHub Copilot | [VS Code](#vs-code-github-copilot-agent-mode) |
| Gemini CLI | [Gemini CLI](#gemini-cli) |
| A script, cron job or CI | [Headless agents and scripts](#headless-agents-and-scripts) |

## Claude Code

Add the server, then sign in, either by typing `/mcp` in a session and following the browser steps, or from your shell:

```bash
claude mcp add --transport http --scope user {{slug}} https://YOUR-{{SLUG}}/mcp
claude mcp login {{slug}}
```

`--scope` picks where it is stored: `local` (default; you, this project; `~/.claude.json`), `project` (everyone in the project; `.mcp.json` in the project root) or `user` (you, all projects; `~/.claude.json`).

On a machine without a browser (for example over `ssh -t`), `claude mcp login` prints the URL; open it elsewhere and paste the redirect URL back. `claude -p` and Agent SDK runs have no `/mcp` panel, so sign in from an interactive session first. Sign out with `claude mcp logout {{slug}}`.

With an API key instead of OAuth, reference the variable that holds it; Claude Code expands `${VAR}` in `url` and `headers`, so the key stays out of `.mcp.json` and off any command line:

```json
{
  "mcpServers": {
    "{{slug}}": { "type": "http", "url": "https://YOUR-{{SLUG}}/mcp", "headers": { "x-api-key": "${CAIRN_API_KEY}" } }
  }
}
```

Or have Claude Code store the key itself:

```bash
claude mcp add --transport http {{slug}} https://YOUR-{{SLUG}}/mcp \
  --header "x-api-key: $CAIRN_API_KEY"
```

That stores the expanded key in the config. A key typed into a command is on that command's line while it runs, where other processes on the machine can read it, so prefer the variable.

Source: https://code.claude.com/docs/en/mcp (read 2026-11-11)

## Codex CLI

```bash
codex mcp add {{slug}} --url https://YOUR-{{SLUG}}/mcp
codex mcp login {{slug}}
```

`codex mcp login` signs in to a streamable HTTP server that supports OAuth. Without a local browser add `--no-browser`, open the printed URL, and paste the full redirect URL back. It also takes `--scopes` and `--oauth-client-registration auto|cimd|dcr`. If the callback port matters (for example behind a firewall), set `mcp_oauth_callback_port` in `~/.codex/config.toml`. The docs list `codex mcp` as stable and name no feature flag for it. Sign out with `codex mcp logout {{slug}}`.

With an API key held in `CAIRN_API_KEY`, sent as `Authorization: Bearer`:

```bash
codex mcp add {{slug}} --url https://YOUR-{{SLUG}}/mcp --bearer-token-env-var CAIRN_API_KEY
```

The same thing in `~/.codex/config.toml`. Use `env_http_headers` if you prefer `x-api-key`:

```toml
[mcp_servers.{{slug}}]
url = "https://YOUR-{{SLUG}}/mcp"
bearer_token_env_var = "CAIRN_API_KEY"
# or: env_http_headers = { "x-api-key" = "<ENVIRONMENT_VARIABLE_NAME>" }
```

In the `env_http_headers` line, write `CAIRN_API_KEY` in place of `<ENVIRONMENT_VARIABLE_NAME>`: the header takes the variable's name, never the key.

Source: https://learn.chatgpt.com/docs/cli/reference and https://learn.chatgpt.com/docs/extend/mcp?surface=cli (read 2026-11-11; developers.openai.com/codex/mcp redirects to the second)

## Claude on the web, desktop and mobile

This is how you read and write your memory from a phone: add {{name}} once on the web or desktop, then use it in the iOS or Android app.

1. Go to **Customize > Connectors** (claude.ai/customize/connectors).
2. Select **Add**, then **Add custom connector**.
3. Enter a name (`{{slug}}`) and the URL `https://YOUR-{{SLUG}}/mcp`, then **Continue**.
4. Keep the authentication options Claude marks **Detected**. For {{name}}, **Register automatically** (dynamic client registration) works.
5. Click **Add**, then **Connect**, and approve in the {{name}} page that opens.
6. In a chat, click **+**, choose **Connectors**, and turn {{name}} on for that conversation.

On Team and Enterprise plans an Owner adds it under **Organization settings > Connectors** (**Add**, then **Custom**, type **Web**); each member then clicks **Connect** under **Customize > Connectors**.

Plans: Free, Pro, Max, Ultra, Team and Enterprise. Free allows one custom connector.

Mobile: "it will be available to use the next time you log in to your account on Claude for iOS or Android," and "Installing connectors on mobile is currently in beta." The same support article lists custom connectors for "Claude, Cowork, and Claude Desktop" without naming mobile, so plan coverage on phones is not stated explicitly.

Reachability: Claude connects "from Anthropic's cloud infrastructure, rather than from your local device." Your {{name}} "must be reachable over the public internet from Anthropic's IP ranges." A localhost, VPN or Tailnet-only URL will not work here. For a private server, Anthropic documents MCP tunnels (claude.com/docs/connectors/mcp-tunnels/overview).

API key instead of OAuth: choose **No sign-in** and add `x-api-key` under **Request headers**. Request headers are in beta for a limited set of organizations; if you do not see the section, use OAuth. Authentication settings cannot be changed after adding; remove the connector and add it again.

Source: https://claude.com/docs/connectors/custom/remote-mcp, https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp, https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities (read 2026-11-11)

## ChatGPT

1. Go to **Plugins** (chatgpt.com/plugins). This flow does not need Developer Mode turned on first.
2. Select the plus button, then **Add custom MCP server**.
3. Enter a name, then under **Connection** enter `https://YOUR-{{SLUG}}/mcp`.
4. Configure authentication (OAuth; ChatGPT "can also use DCR when configured").
5. Tick **I understand and want to continue**, then **Create as a plugin**.
6. In a new chat, type `@` and pick {{name}}.

Plans: full MCP, including write actions, is available to Business and Enterprise/Edu on ChatGPT web. On Business only admins and owners can create these. Pro users "can connect custom MCP servers with read/fetch permissions," so `note` and `new_project` will not run there. Plus and Free are not stated in the docs.

Mobile: "Can I connect custom MCP servers on mobile? No - web only." Reachability: the endpoint must be a public HTTPS URL, or a Secure MCP Tunnel for a server on a private network.

Tool limits: "Write actions by default require confirmation." Tools without the `readOnlyHint` annotation are treated as write actions. You can remember approval for one conversation; a new conversation asks again. Some risky actions may be blocked. After an admin approves a server, ChatGPT keeps a frozen snapshot of its tools until an admin refreshes it.

Source: https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt, https://developers.openai.com/api/docs/guides/custom-mcp-server (read 2026-11-11)

## Cursor

Put this in `.cursor/mcp.json` (this project) or `~/.cursor/mcp.json` (everywhere):

```json
{
  "mcpServers": {
    "{{slug}}": { "url": "https://YOUR-{{SLUG}}/mcp" }
  }
}
```

Cursor supports OAuth (authorization code with PKCE) for remote servers. How sign-in starts for a server added by hand to `mcp.json` is not stated in the docs. An optional `auth` object (`CLIENT_ID`, `CLIENT_SECRET`, `scopes`) is for pre-registered clients; {{name}} does not need it.

With an API key (Cursor expands `${env:NAME}` in `url` and `headers`):

```json
{
  "mcpServers": {
    "{{slug}}": { "url": "https://YOUR-{{SLUG}}/mcp", "headers": { "x-api-key": "${env:CAIRN_API_KEY}" } }
  }
}
```

Source: https://cursor.com/docs/context/mcp (read 2026-11-11)

## VS Code (GitHub Copilot agent mode)

Workspace file `.vscode/mcp.json`; for your user profile run **MCP: Open User Configuration**. VS Code uses a top-level `servers` key:

```json
{
  "servers": {
    "{{slug}}": { "type": "http", "url": "https://YOUR-{{SLUG}}/mcp" }
  }
}
```

A browser window opens for authorization on the first connection. The reference documents an `oauth` object with a required `clientId`; whether it can be left out for servers with dynamic client registration is not stated in the docs.

Other ways to add it: run **MCP: Add Server** from the Command Palette, or from a shell (this writes to the user profile):

```bash
code --add-mcp '{"name":"{{slug}}","type":"http","url":"https://YOUR-{{SLUG}}/mcp"}'
```

With an API key, prompt for it once instead of storing it:

```json
{
  "inputs": [{ "type": "promptString", "id": "api-key", "description": "{{slug}} API key", "password": true }],
  "servers": {
    "{{slug}}": { "type": "http", "url": "https://YOUR-{{SLUG}}/mcp", "headers": { "x-api-key": "${input:api-key}" } }
  }
}
```

Source: https://code.visualstudio.com/docs/copilot/reference/mcp-configuration, https://code.visualstudio.com/docs/copilot/customization/mcp-servers (read 2026-11-11)

## Gemini CLI

```bash
gemini mcp add --transport http {{slug}} https://YOUR-{{SLUG}}/mcp
```

Or in `~/.gemini/settings.json` (user) or `.gemini/settings.json` (project). Use `httpUrl` for streamable HTTP; `url` means SSE:

```json
{
  "mcpServers": {
    "{{slug}}": { "httpUrl": "https://YOUR-{{SLUG}}/mcp" }
  }
}
```

OAuth is discovered automatically. Run `/mcp auth {{slug}}` in a session to sign in. For a key, add a `headers` object with `x-api-key` to the entry (the docs expand variables only in `env`, so it holds the key itself), or add `--header "x-api-key: $CAIRN_API_KEY"` to the command. A key typed into a command is on that command's line while it runs, where other processes on the machine can read it, so prefer the entry.

Source: https://geminicli.com/docs/tools/mcp-server/ (read 2026-11-11)

## Headless agents and scripts

Create an API key in {{name}} under **Workspace**, then **Keys** (in a shared workspace an owner or an admin does this), keep it in an environment variable, and never commit it. A key typed into a command is on that command's line while it runs, where other processes on the machine can read it, so `curl` reads its header from a file only you can read, written by the shell's own `printf` (a builtin, so the key is on no command line):

```bash
export CAIRN_API_KEY=...   # from your secret store
(umask 077; printf 'x-api-key: %s\n' "$CAIRN_API_KEY" > ~/.{{slug}}-header)
curl -sS -H @"$HOME/.{{slug}}-header" \
  "https://YOUR-{{SLUG}}/api/search?q=deploy%20checklist"
```

Or with no file, the header on `curl`'s standard input: `printf 'x-api-key: %s\n' "$CAIRN_API_KEY" | curl -sS -H @- "https://YOUR-{{SLUG}}/api/search?q=deploy%20checklist"`.

Long lists come in parts. `overview` and `GET /api/projects/<project>/pages` list 100 pages at most, by slug; `limit` asks for fewer, and `after` for the pages after a slug. An answer that leaves pages out has `pages_total`, how many there are in all, and, while more follow, `pages_next`: pass it as `after` to get the next part. A page's `versions` (`page`, `GET /api/projects/<project>/pages/<slug>`) lists its latest 50 version numbers, oldest first; `limit` (at most 50) takes fewer, and `before` the versions numbered below it. A part that leaves versions out has `versions_total` and, while earlier ones remain, `versions_next`, to pass as `before`. An answer that holds everything has none of these fields. Over REST each is a query parameter, as in `/api/projects/ops/pages?after=deploy-checklist`; over MCP each is an argument of the tool.

A search says what it left out. `recall` and `GET /api/search` search the first 32 words of a query, fewer when those words hold more than 64 parts between underscores (as in `retry_count`), and rank as many of the claims that match as a fixed amount of work allows for the parts of its words: 8,000 for one part, 4,000 for two, 2,000 for four, and 1,000 from eight parts on, as before. So a short query ranks more of its matches with the work a query of eight parts already took: a short query does more work than it did, and none does more than a query of eight parts did before. Every answer has `searched`: `complete` is true when every word was searched and every matching claim was ranked. Otherwise `words_left_out` names the first 5 words left out, each cut to 64 characters, and `words_left_out_count` counts them all; when more claims match than the search ranks, `claims_ranked` says how many were ranked and `claims_matched` how many match, or is null when that was not counted. A word's count stops at 8,000 matches, so that no search grows with the claims it reaches. When a search of one word over every project it reaches stopped there, `claims_matched` is 8,000, `claims_matched_exact` is false, and `said` says that more than 8,000 claims match; otherwise the field is not there. The claims ranked then are those that hold the query's rarest words, the newest first among equals; for a query of one word, the newest. `said` says it in a sentence, with what to do: search one project (when the search covered more than one), add a rarer word or shorten the query.

{{name}} never drops a field it does not know. A body key or a query parameter that a REST address does not take is refused with 400, and an argument that a tool does not have is refused as a tool error; the refusal names it and lists every key, parameter or argument the address or the tool takes. Each tool's input schema says so in advance, with `"additionalProperties": false`.

The same key in the two CLIs: Codex stores only the variable name and reads it at run time; for Claude Code, the `.mcp.json` entry with `${CAIRN_API_KEY}` under [Claude Code](#claude-code) does the same.

```bash
codex mcp add {{slug}} --url https://YOUR-{{SLUG}}/mcp --bearer-token-env-var CAIRN_API_KEY
claude mcp add --transport http {{slug}} https://YOUR-{{SLUG}}/mcp --header "x-api-key: $CAIRN_API_KEY"
```

The second stores the header value you pass, and while it runs the key is on its command line, where other processes can read it.

## If it does not connect

- The URL must end in `/mcp`. The bare host or `/api` will not work.
- Use https. Sign-in through the browser needs it everywhere except on localhost. Over plain http on any other host, sign-in is off and a key would cross the network unprotected.
- A private-network URL (localhost, VPN, Tailnet) works for clients running on a machine on that network: Claude Code, Codex, Gemini CLI, Cursor and VS Code. It does not work for Claude on the web, desktop or mobile, or for ChatGPT, because those connect from the vendor's servers.
- If the browser never opens, sign in by hand: `/mcp` or `claude mcp login {{slug}}` in Claude Code, `codex mcp login {{slug}}` in Codex, `/mcp auth {{slug}}` in Gemini CLI.
- In ChatGPT, if a write tool will not run, check your plan (Pro is read/fetch only) and confirm the prompt.
- To end a connection, open **Workspace**, then **Agents**, in {{name}} and disconnect it; the client will need to sign in again. To revoke an API key, open **Workspace**, then **Keys**. In a shared workspace an owner or an admin revokes keys and can disconnect any agent; a member disconnects the agents they approved.
