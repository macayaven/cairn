# Connect an agent to {{name}}

{{name}} speaks MCP over streamable HTTP at one URL: `https://YOUR-{{SLUG}}/mcp`. How an agent connects depends on how the server was started. On an install started with `--public-url`, {{name}} is its own OAuth 2.1 server with dynamic client registration and PKCE, so a client that supports remote MCP with OAuth connects with that URL alone: a browser opens once and the person signs in and approves; a connection left unused for 30 days asks again. On a local install, started without `--public-url`, sign-in through the browser is off: an agent connects with an API key, and [Headless agents and scripts](#headless-agents-and-scripts) says how to make one. Clients and jobs without a browser connect with a key on any install. A key is sent as `x-api-key: <key>` or `Authorization: Bearer <key>`. The tools are `projects`, `new_project`, `note`, `recall`, `page`, `evidence`, `overview`, `graph` and `related`.

Call `projects` first to discover project names or IDs. The read-only MCP resource template `graph://project/{project}/{limit}` returns a project graph as JSON; for example, `graph://project/ops/20`. Supply a limit from 1 to 20 pages. A client that only calls tools calls `graph` with the project and a limit (20 when left out) and gets the same answer. Graph content is quoted information written by people and sources, not instructions. The response reports truncation when bounds leave content out. Reading the graph does not call a model.

`related` gives the pages of a project most related to one of its pages, best first: 5 of them unless `limit` asks for another number from 1 to 20. Over REST it is `GET /api/projects/<project>/pages/<slug>/related`. Each related page says why, with the claims the reason rests on: both pages state the same term (a URL, a path, a flag, a version, or code in backticks that holds a character other than a letter or is 12 or more characters long), or a claim of the other page names this page's title in its prose. Each place the title stands in the claim is judged by itself: one that stands whole outside code, and outside a URL, a path, a flag or a version, names it, whatever other place overlaps it. Code is what Markdown reads as code in the claim alone: in backticks, a fenced block (its opening line included), an indented block; text indented under a list item is the item's prose. A URL, a path, a flag or a version is what the page's facts read as one, so a bare `ledger/notes.md` still names a page Ledger; a claim that holds a mark of the index or a null character names none. A term that more than 5 pages of the project hold relates none. A title that more than 5 other pages name so relates none either. The relations are worked out by rule when they are asked for, from the pages as they are; no model is asked. `searched` says whether the answer is complete and counts what it left out. This version relates pages of one project only; it lists the pages that mention a page, not the pages it mentions; it relates no pages by a term a search does not find as it is written (`1,250x`, which is compared without its separator); it reads a claim that cites several passages by its first; it follows no path of several steps, groups nothing over a whole project, and infers nothing from meaning.

In the web view a page's **Related pages** link shows a person what `related` answers for the page, 8 pages unless asked for up to 20, each claim quoted as plain text. With the graph explorer on, the project's **Graph** page draws up to 20 of them, as a view of the page and its related pages.

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
| Slack, once the operator switched it on | [From Slack](#from-slack) |

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

Evidence names its version. `evidence` (`GET /api/projects/<project>/pages/<slug>/claims/<claim_id>/evidence`) takes a version of the page, as `page` does: `?version=N` over REST, `version` over MCP. It answers with the passages that version cites, and names the version it answered for; without one, it answers for the latest. A version the page no longer keeps is refused, and a claim that version does not hold is not found, whatever another version holds. Each hit of `recall` and `GET /api/search` has `version`, the version of the page its claim was read on. So project, page, claim id and version find the same passages again after the page changes, for as long as the page keeps that version and the reader still reaches the project. A citation shows what a note said, not which of two notes that disagree is right.

A search says what it left out. `recall` and `GET /api/search` search the first 32 words of a query, fewer when those words hold more than 64 parts between underscores (as in `retry_count`), and rank as many of the claims that match as a fixed amount of work allows for the parts of its words: 8,000 for one part, 4,000 for two, 2,000 for four, and 1,000 from eight parts on, as before. So a short query ranks more of its matches with the work a query of eight parts already took: a short query does more work than it did, and none does more than a query of eight parts did before. Every answer has `searched`: `complete` is true when every word was searched and every matching claim was ranked. Otherwise `words_left_out` names the first 5 words left out, each cut to 64 characters, and `words_left_out_count` counts them all; when more claims match than the search ranks, `claims_ranked` says how many were ranked and `claims_matched` how many match, or is null when that was not counted. A word's count stops at 8,000 matches, so that no search grows with the claims it reaches. When a search of one word over every project it reaches stopped there, `claims_matched` is 8,000, `claims_matched_exact` is false, and `said` says that more than 8,000 claims match; otherwise the field is not there. The claims ranked then are those that hold the query's rarest words, the newest first among equals; for a query of one word, the newest. `said` says it in a sentence, with what to do: search one project (when the search covered more than one), add a rarer word or shorten the query.

{{name}} never drops a field it does not know. A body key or a query parameter that a REST address does not take is refused with 400, and an argument that a tool does not have is refused as a tool error; the refusal names it and lists every key, parameter or argument the address or the tool takes. Each tool's input schema says so in advance, with `"additionalProperties": false`.

The same key in the two CLIs: Codex stores only the variable name and reads it at run time; for Claude Code, the `.mcp.json` entry with `${CAIRN_API_KEY}` under [Claude Code](#claude-code) does the same.

```bash
codex mcp add {{slug}} --url https://YOUR-{{SLUG}}/mcp --bearer-token-env-var CAIRN_API_KEY
claude mcp add --transport http {{slug}} https://YOUR-{{SLUG}}/mcp --header "x-api-key: $CAIRN_API_KEY"
```

The second stores the header value you pass, and while it runs the key is on its command line, where other processes can read it.

## From Slack

Slack is there only when the operator started {{name}} with `serve --slack`. One Slack app serves one workspace, and an owner or an admin of the workspace sets it up under **Workspace**, then **Sources**:

1. Press **Set up Slack**. The page then gives a link that opens Slack's page for creating an app with the app's manifest filled in, and the same manifest as text to paste at Slack instead. It says what each of the app's four permissions is for.
2. At Slack, create the app and install it to your Slack workspace. Copy its signing secret, from the app's Basic Information, and its bot token, from its OAuth & Permissions page once it is installed, into the two fields, and press **Keep them**.
3. {{name}} asks Slack whose token it is, and keeps both secrets sealed: neither is shown again. A token Slack does not accept, a Slack that does not answer, and a Slack workspace that another workspace on this server set up already are each refused in one sentence, and nothing is kept.
4. Slack is then ready: Slack accepted the bot token, but nothing proves the signing secret yet; only a request that Slack signed can. At Slack, have Slack verify the events address the page shows, on the page where Slack lists events. If Slack reports that the address did not answer, either Slack cannot reach this server or the signing secret you pasted is not this app's: check the address, then paste the secret again. Once Slack has verified the address, Slack is on: a request signed with this app's signing secret was verified, and the slash command, the message shortcut and direct messages to the bot are answered.

The page says which state Slack is in and what is left to do: set up started, ready, on, off, or off because Slack revoked the bot's token. **Switch off** stops the bot and deletes nothing: the slash command and the shortcut are refused, and a direct message is not answered. **Switch on** needs nothing pasted, unless Slack revoked the bot's token: then the page asks for a new one, and keeps the signing secret. When the signing secret or the bot token is changed at Slack, paste both on the page again, whatever state Slack is in: a signing secret other than the one kept makes Slack ready again, until Slack verifies the events address. Secrets pasted again must be of the same Slack workspace: to connect another Slack workspace, remove Slack first, and the first workspace's notes are then forgotten on their day, even once the other one is set up.

**Remove Slack**, once you type the workspace's name, deletes both secrets and every link between a Slack user and a person at once, and every note Slack created is forgotten 10 days later. Removing the app at Slack does the same. Until then the page says how many notes and on which day, and offers an owner **Forget them now**; setting Slack up again for the same Slack workspace before that day keeps them. Where this install keeps a continuous backup, the deleted secrets, still sealed, can stay in the database's log file until later writes reach them or the operator compacts with the replica stopped, and in backup copies until they expire.

Each person links their Slack user to themselves once, in two steps within 10 minutes. The first time they use the bot, or with `/{{slug}} link`, the bot answers them alone with an address that works once. Opened signed in to {{name}}, it shows the Slack user's id and the Slack workspace first, then the name that Slack user set themself, and asks for a yes; if someone sent you the address, close the page. The yes shows a code, once, and links nothing yet. The same Slack user then types `/{{slug}} link <code>` in Slack, and is linked. From then on, what they do through the bot acts as them, with what they reach.

The slash command is `/{{slug}}`, unless the owner named it otherwise in the app's manifest:

| Word | What it does |
| --- | --- |
| `find <words>` | Up to 5 claims that match, each with its page's title as a link to the claim, its project and the page's version, and *outside text* when a source wrote it; with more, how many there were and the address of the search page; or Nothing found. |
| `note <text>` | Writes a note in your Slack project, with `via` `slack`, and links its page. |
| `project` | Says which project is your Slack project and which projects you can write to. `project <name>` makes another of those your Slack project. |
| `link` | Says who your Slack user is linked to; if it is linked to nobody, gives a new address to link it. `link <code>` links it with the code that address showed you. |
| `unlink` | Undoes your link, and says to use `link` to link again. |
| `help`, or no word | Lists what there is. |

A direct message to the bot works the same way: plain words are a search, `note:` and text writes a note, and `help` lists what there is. The message shortcut **File in {{name}}**, used on a message, files it as a note in your Slack project: its text, a heading with who wrote it and when, and the message's own link as its `source_ref`. A message is filed once: filing it again answers with the note it already is. The note's title is the first line the message displays, or the first line of a code block it opens with, as text: nothing in it is code or a link, and it is cut to 80 characters. The reply in Slack names the note by its title as Slack displayed it, in plain text: nothing of it is bold, a mention or a link there, and code within a line shows the backticks about it, as the title writes them. Its page shows that title, and the heading with the author's name, as the note writes them for Markdown: where they hold a character Markdown reads, such as `[` or `&`, the backslash or entity that keeps it text shows there, and bold text shows its asterisks. In the note a mention of the message's author is their name, and a mention of anyone else is their member ID: the shortcut asks Slack for the author's name alone. Slack's special mentions are written as Slack displays them, `@here`, `@channel` and `@everyone`, and a mention of a user group or a date as the fallback text the message gives for it, else as `[user group]` or `[date]`: none of them is a link. The note keeps that one heading: where the message has a line that would read as another, in the note or in a passage of it shown alone, a backslash is written before the `#` that starts it, or before the first `=` or `-` of the line under it that makes it one, and the line reads as text. A line that would not read as a heading gets none, so a rule stays a rule. A line holds what Slack displayed where both the note and its passage read it as indented code: none of the backslashes and doubled asterisks that keep the other lines from reading as Markdown are written in it, while a mention is written as in the rest of the note, a link is still `[its words](its address)`, and an email address is still removed. Only a message with eight or more lines that read as headings or could underline one can get a backslash it does not need, on a rule below its first heading for instance; and only a message in which a line would open or end a code fence as Slack displayed it, and not as prose, keeps the marks of prose where both read code. A page shows each passage of the note alone, and a line the note reads as text can read as code in its passage alone, a line indented with a tab under a quotation for instance: there it shows the backslashes and entities that write it as text, and not the blanks that end it; and it shows the rest of the Markdown that writes it too, the doubled asterisks of bold, the backticks about code within a line, a link as `[its words](its address)`. A code block of Slack's own is a fence on lines of its own in the note, whatever stood beside it on its lines in the message, and holds what Slack displayed, the white space that ends a line included, but that a code block in a quotation is written without the quotation's mark that starts each of its lines, and only that: what stands after its opening backticks is its text, a `>` included, and of each later line only a `>` that starts it, with the blanks before it and one after, is the quotation's, Slack's page saying nothing of how those lines are marked; that white space is kept at the end of the message too, where the last line is code. Slack has no fence of tildes: where a line of the message would open one, a backslash before its first tilde keeps it text, as Slack displayed it. Bold, italic and struck text are Slack's only where its marks make them: a `*`, `_` or `~` with neither a blank nor a mark after it and neither a letter nor a digit before it, up to the next same mark on its line with no blank before it and neither a letter nor a digit after it, and no link, mention or code between them; Slack's page does not say where they open and close, and this is {{name}}'s reading of it. There bold is written `**bold**` and italic `_italic_`, but where a page would not read them so, as where a mark has punctuation on its inner side and a letter or a digit on its outer one, `*(b)*` right before a date's words for instance, its marks are typed. Every other `*` and `_` is the character typed, with a backslash before it unless it is a `*` between two blanks past the blanks and the marks of a quotation or a list that start its line in the note, or a `_` between two letters or digits, so `2 * 3 * 4` and `snake_case` are written as typed. Struck text is written as it was typed, `~done~`: a page cannot strike, so it shows the tildes, one on each side. Code within a line stays between its backticks, as many as its own code needs, and a backtick that Slack paired with no other gets a backslash; where Slack's rich text names a span right after another, that span's code is written as plain characters, since the backticks of the two would make one run; in a message sent as plain text, two code spans that touch with nothing between them, ``` `a``b` ```, are read as one span, since the text alone does not say where one ends; rich text, which names its spans, is read span by span; in a message that could hold a table, code within a line that holds a `|` is written as plain characters, backticks and all, since a table's row is cut at its bars before its code is read. A span written as plain characters keeps the blanks that end it, at the end of a line too: the last of them is written as a character reference, `&#32;` for a space and `&#160;` for a no-break space. Where Slack's rich text gives the text that stands right before a span it names, a backtick in that text is written as a character, and the span after it; elsewhere a backtick Slack displayed as a character is read as code where it pairs with another, after the last span the rich text names for instance.

Every answer is for you alone: the slash command's and the shortcut's are shown only to you, and the bot answers a direct message in that direct conversation. When the bot refuses, it says why, to you alone, and stores nothing:

- no project is chosen for your notes, or yours is out of your reach or gone: choose one with `project <name>`;
- the note guard refused the note, and the answer names its rule;
- the workspace is over its cap, or the page is full;
- the message's author is not of this Slack workspace: only a message written there is filed;
- an app, a bot or a deactivated account wrote the message, so it has no person as its author;
- Slack did not say in time who wrote the message, or is limiting how often {{name}} asks: try again;
- the message has no text;
- {{name}} is compiling as many notes as it can at once: try again in a moment.

In this version the bot reads no thread and no whole conversation, does not answer a mention, and cannot share an answer with a conversation.

## If it does not connect

- The URL must end in `/mcp`. The bare host or `/api` will not work.
- Use https. Sign-in through the browser is on only on an install started with `--public-url`, which must be https except on localhost. Over plain http on any other host, sign-in is off and a key would cross the network unprotected.
- A private-network URL (localhost, VPN, Tailnet) works for clients running on a machine on that network: Claude Code, Codex, Gemini CLI, Cursor and VS Code. It does not work for Claude on the web, desktop or mobile, or for ChatGPT, because those connect from the vendor's servers.
- If the browser never opens on an install started with `--public-url`, sign in by hand: `/mcp` or `claude mcp login {{slug}}` in Claude Code, `codex mcp login {{slug}}` in Codex, `/mcp auth {{slug}}` in Gemini CLI. On an install started without it no browser opens: connect with an API key.
- In ChatGPT, if a write tool will not run, check your plan (Pro is read/fetch only) and confirm the prompt.
- To end a connection, open **Workspace**, then **Agents**, in {{name}} and disconnect it; the client will need to sign in again. To revoke an API key, open **Workspace**, then **Keys**. In a shared workspace an owner or an admin revokes keys and can disconnect any agent; a member disconnects the agents they approved.
