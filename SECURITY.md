# Security

## Report a vulnerability

Use GitHub's private vulnerability reporting on this repository: open the **Security** tab and choose **Report a vulnerability**. The report is seen only by the maintainers until a fix is published. Please do not open a public issue, pull request or discussion about it.

Say what you found, the version or commit, the steps that show it, and what an attacker gains. A report is acknowledged and then followed up in the same advisory.

## What is in scope

The code in this repository: the server, its web pages, the REST API and the MCP endpoint, the image built from the `Dockerfile`, and the files in `deploy/`. An install someone else runs is theirs to secure; report a flaw in its setup to whoever runs it.

[The security guide](src/cairn/guides/security.md), also served at `/docs/security`, says who reaches what in cairn, and what its checks catch and what they do not.
