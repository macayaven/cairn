# Changelog

What changed for someone who runs cairn or connects an agent to it. The reasons are in [docs/decisions.md](docs/decisions.md).

## Unreleased

The first public version.

- **Memory with provenance.** Agents write notes over MCP or REST; cairn compiles them into pages whose claims are passages copied from the notes, each opening the lines it came from. A page that lost a fact, or holds a claim its source does not, is rejected and the last good version stays.
- **Run it yourself.** One container and one SQLite file. `deploy/compose.local.yaml` runs it on one computer; `deploy/compose.yaml` adds a domain, TLS and, with `compose.backup.yaml`, a continuous backup.
- **People and workspaces.** Sign-in through Google, GitHub or an OIDC provider, workspaces with roles, private projects, invitations, an audit log, export and deletion.
- **Agents.** One address for every agent, approved once in the browser, with the projects it reaches and whether it may write; API keys for scripts.
- **Sources.** Readers that bring notes from Granola, Linear, GitHub, Langfuse, Gmail, Google Calendar, Drive and Meet, each previewed before it is switched on.
- **Limits an operator sets.** A plans file caps notes, storage, projects and requests per workspace. Nothing is sold.
- **Licence.** MIT for cairn's own code.
