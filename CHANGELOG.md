# Changelog

What changed for someone who runs cairn or connects an agent to it. The reasons are in [docs/decisions.md](docs/decisions.md).

Each release has a section headed `## <version> - <date>`, and the tag `v<version>`, pushed to a public repository on a commit of `main`, publishes it: `scripts/release_check.py` refuses a tag that is not the version in `pyproject.toml`, whose section here is missing, has no entry or is dated after the day the check runs (UTC), or whose version already has a release. The section is the GitHub release's text.

## Unreleased

## 0.1.0 - 2026-11-19

The first public version.

- **Memory with provenance.** Agents write notes over MCP or REST; cairn compiles them into pages whose claims are passages copied from the notes, each opening the lines it came from. A page that lost a fact, or holds a claim its source does not, is rejected and the last good version stays.
- **Evidence for a version.** A page keeps its versions, and `evidence` answers for the version a claim was read on. Each search hit names the version of its page, so what it cited can be asked for again after the page changes.
- **Run it yourself.** One container and one SQLite file. `deploy/compose.local.yaml` runs it on one computer; `deploy/compose.yaml` adds a domain, TLS and, with `compose.backup.yaml`, a continuous backup.
- **A versioned image.** Each release publishes a container image for linux/amd64 and linux/arm64 tagged with its version, with provenance and a bill of materials. The guides still build from source first.
- **Backups and upgrades.** The guides back up with cairn stopped, into a new folder each time, start it again whatever happened, and say how to restore a copy. An upgrade starts with that backup.
- **People and workspaces.** Sign-in through Google, GitHub or an OIDC provider, workspaces with roles, private projects, invitations, an audit log, export and deletion.
- **Agents.** One address for every agent, approved once in the browser, with the projects it reaches and whether it may write; API keys for scripts.
- **Sources.** Readers that bring notes from Granola, Linear, GitHub, Langfuse, Gmail, Google Calendar, Drive and Meet, each previewed before it is switched on. A member manages the sources of their own account. Two sources whose selections cairn cannot tell apart are refused.
- **From Slack.** Off unless switched on with `serve --slack`. Each workspace sets up a Slack app of its own from a manifest cairn writes; people then file a Slack message as a note, write a note, and ask the bot to find something, each as the person they linked their Slack user to, with a code shown to that person and typed in Slack. Removing the app deletes its secrets at once and forgets the notes Slack created 10 days later. It needs sign-in, a public `https://` address and a key, and is not offered under a brand yet. It is tested against a stand-in for Slack and has not been run against Slack itself.
- **Off unless switched on.** Connecting a Google account in the browser (`--google-connect`), a graph explorer (`--graph-explorer`) and a notice that an install is a private test instance (`--test-instance`).
- **Limits an operator sets.** A plans file caps notes, storage, projects and requests per workspace. Nothing is sold.
- **Licence.** MIT for cairn's own code.
