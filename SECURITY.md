# Security

## Report a vulnerability

Use GitHub's private vulnerability reporting on this repository: open the **Security** tab and choose **Report a vulnerability**. The report is seen only by the maintainers until a fix is published. Please do not open a public issue, pull request or discussion about it.

Say what you found, the version or commit, the steps that show it, and what an attacker gains. A report is acknowledged and then followed up in the same advisory.

## Which versions get fixes

The newest release gets fixes. A fix is made on `main` and published as the next release, a patch release such as `0.1.1` after `0.1.0` when it changes nothing else, and the changelog says which versions it concerns. Earlier releases get no fixes of their own: update to the newest.

An image tag with a full version, such as `0.1.0`, never moves, so an install pinned to one stays as it is until you change the pin. The tag `0.1` moves to each newer `0.1.x`, and `latest` names the highest release. An install built from `main` gets a fix by updating to `main`.

## Records the law protects

cairn has not been assessed against HIPAA, FERPA, COPPA or any rule like them, and as shipped it is not ready to hold health or education records about people who can be identified, children's least of all. [The privacy guide](docs/guides/privacy.md#records-the-law-protects) says what is missing and what an operator would have to add.

## What is in scope

The code in this repository: the server, its web pages, the REST API and the MCP endpoint, the image built from the `Dockerfile`, and the files in `deploy/`. An install someone else runs is theirs to secure; report a flaw in its setup to whoever runs it.

[The security guide](docs/guides/security.md), also served at `/docs/security`, says who reaches what in cairn, and what its checks catch and what they do not.
