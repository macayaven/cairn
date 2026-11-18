<!-- Generated from src/cairn/guides/patterns.md by scripts/render_guides.py. Edit that file, not this one, and run uv run python scripts/render_guides.py. -->
# Patterns: laying out projects and connections

Two questions decide a setup:

1. **What does each project hold?** A project is one body of memory with one purpose: a repository's engineering memory, a team's decisions, the facts an AI feature must keep consistent.
2. **Who or what reaches it?** Every agent and app has its own connection, and each connection reaches all projects or only the ones you choose, to read and write or only to read.

The project is also the unit of access. If two things should not see each other's notes, they need different projects.

Each project has a kind, such as a code repository, a team's runbooks or the facts an AI feature must keep consistent. The kind tells the agents that use the project what belongs in it and what to leave out. After you sign in, **/start** asks what you will use cairn for, makes a project of the right kind for each use, and shows the command or the key each agent and app needs. The patterns below are how to grow from there.

## Pick a pattern

| Your situation | Pattern |
|---|---|
| One person, a few agents, no need to separate anything | [Many agents, one project](#many-agents-one-project) |
| Several repositories or apps, each with its own agent | [Many agents, many projects](#many-agents-many-projects) |
| Work and personal, or several teams, in one install | [Project sets per use case](#project-sets-per-use-case) |

You can start with the first and move to the others later. Projects, sets and connections can all be added without touching what exists.

## Many agents, one project

One project holds everything, and every agent reaches it.

```
Claude Code ─┐
Codex ───────┼──▶  core
Cursor ──────┤
a CI job ────┘  (API key)
```

1. Create one project, for example `core`.
2. Connect each agent. On an install started with `--public-url`, an agent that has a browser at hand connects by URL alone:

   ```bash
   claude mcp add --transport http cairn https://YOUR-CAIRN/mcp
   codex mcp add cairn --url https://YOUR-CAIRN/mcp
   ```

   The browser opens once per agent. Leave **All projects** selected and approve. On a local install, started without `--public-url`, sign-in through the browser is off: give each agent a key instead, as in step 3 and [the connect guide](connect.md#headless-agents-and-scripts) shows.
3. For a job without a browser, create a key under **Workspace**, then **Keys**, and give it to the job as `CAIRN_API_KEY`.

What one agent notes, every other agent finds at once. Nothing is separated, which is the point and the limit of this pattern.

## Many agents, many projects

Each repository or app has its own project, and a shared project holds what everyone should read.

```
api agent ──▶ api        (write)        ┐
web agent ──▶ web        (write)        ├──▶ handbook (read only)
worker app ─▶ worker     (write, key)   ┘
your laptop ─▶ all projects (write)
```

1. Create one project per repository or app, named after it, and one shared project such as `handbook`.
2. Your own agents connect with **All projects**. They pick the project named after the repository they are working in; cairn's instructions tell them to.
3. Each app gets a key that reaches only its own project: **Keys → New key → Selected projects**, tick the app's project.
4. To let an app read the shared project without being able to change it, give it a second key: **Selected projects**, tick `handbook`, **Read only**.

A connection limited to chosen projects cannot see that the others exist, and it cannot create projects or keys.

## Project sets per use case

A set is a named group of projects. A connection can reach a set, and it then reaches whatever the set holds, including projects added later.

```
work laptop ──▶ set "work" ──▶ api, web, billing
phone ────────▶ journal
```

1. Create the projects, then a set under **Workspace**, then **Sets**: `work` with `api` and `web`.
2. Connect the work laptop's agent. On the approval page choose **Selected projects** and tick the set `work`.
3. Connect the phone's assistant and tick only `journal`.
4. When new work starts, create a project for it and add it to `work`. The work laptop's agent reaches it without reconnecting; the phone still does not.

Use sets when the same group of projects is given to several connections, or when the group will grow.

## A product team with AI features

A worked example that combines the three.

| Project | Kind | Holds | Reached by |
|---|---|---|---|
| One per service, such as `therapy`, `tracking`, `content` | Code repository | Decisions with reasons, errors with root causes, migration state | The set `engineering` |
| `handbook` | Team decisions and runbooks | Conventions, runbooks, how releases work | Everyone, read only for apps |
| `story-bible` | Memory for an AI feature | Characters, style rules, what a story must never contain | The story feature's key, write |
| `scene-rules` | Memory for an AI feature | Illustration rules, question-writing rules | The scene feature's key, write |

- Engineers' coding agents connect once each and tick the set `engineering` plus `handbook`.
- Each feature's worker gets a key limited to its own project, and a read-only key for `handbook`.
- Nothing about end users goes into any project. Set the workspace's guard to refuse personal-data patterns as well as secrets, and keep any source that carries such data disconnected.

## Private projects

A project made private is reached only by the person who made it, the people its rule names, and the workspace's owners. Admins don't reach it unless the rule names them. Its maker, an owner, or anyone its rule gives Read and write, and decide who reaches it, names people on the project's Who reaches it page, one at a time or several at once by email address. A credential that acts for no person reaches it too: a key made on the server's command line with `key-create`, and a source the operator added. Those belong to whoever runs the server. To everyone else it looks like a project that does not exist, with one limit: a project's name is unique in its workspace. Someone who cannot see a private project can still learn that its name is taken, because creating a project under that name is refused. The refusal says only that the name is not available, in the same sentence whether the project is visible or hidden, and tells nothing else about it. Pick names for private projects that are safe to be known.

## What a connection can never do

- Reach another workspace.
- Reach a project outside what was ticked when it was approved or created. Such a project looks exactly like one that does not exist.
- Write, if it was made read only.
- Create projects, keys or sets, unless it reaches all projects with write access.

An owner or an admin sees every connection and what it reaches under **Workspace**, on the **Agents** and **Keys** pages, and can remove any of them there. A member sees the agents they approved, and can disconnect those.
