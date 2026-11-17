# Documentation releases and the read-only helper

`python -m cairn.docs_publisher` builds a finite documentation artifact. It contacts no
service unless `--publish` is present. There is no model call, new API, schema step or
server configuration in this publisher.

## Release identity and approved files

The repository is `https://github.com/macayaven/cairn`. The release is a full,
lowercase 40-character commit equal to the checkout's `HEAD`. Origin must name that
repository. These are the entire whitelist, in this order:

1. `src/cairn/guides/connect.md`
2. `src/cairn/guides/patterns.md`
3. `src/cairn/guides/sources.md`
4. `src/cairn/guides/security.md`
5. `src/cairn/guides/run.md`
6. `src/cairn/guides/privacy.md`

There is no file argument, glob, recursive discovery, private directory, work order,
review, customer data, README or architecture document in the release. Each approved
file must be tracked as a regular file, have no symlink in its repository path, and
match the commit byte for byte. Staged changes to approved files are refused too.
Commit approved guide corrections before selecting the release; a dirty guide cannot
be published under an earlier commit.

The project name is exactly `cairn-docs-<release>`. Its description is exactly
`cairn documentation release <release>; cairn-docs-release-v1`. Its kind is `documents`
and its models setting is `never`. The project must exist already. The publisher
never creates a project, grants access, changes that setting or creates a credential.

## Operator setup and commands

An owner or deployer must do these steps through the existing application:

1. Create a dedicated documentation workspace containing only the release project,
   using the exact name, description and Documents kind above. Set Models to Never
   before publishing. Do not use a customer workspace or import customer content.
2. On `/keys`, make a publisher key with **Selected projects**, only that release
   project, and **Read and write**. Separately make a consumer key with **Selected
   projects**, only the same project, and **Read only**. Do not choose a project set
   or All projects. `/api/projects` must return exactly this project for either key.
   That check detects extra visible projects; the API does not expose a credential's
   reach, so the owner must also verify the selected scope in Keys.
3. Put the writer only in the publishing process's environment, resolved at runtime
   from a protected file such as `~/.config/cairn/cairn-docs.env`. Do not put
   its value in a command argument, client configuration, artifact or log. The
   examples below name the environment variable; they contain no credential.
4. Build and inspect the artifact locally. After explicit owner authorization for
   publication, publish to the selected HTTPS origin using the writer variable.
5. Verify the reader against the full artifact contract below. Revoke the publisher
   key when publication is complete. Give the helper only the separate reader key,
   the trusted artifact file and the expected full release commit
   ([Documentation search in the app](documentation-helper.md)). Provisioning,
   publication, credential creation/revocation and deployment are operator actions;
   none were executed by implementing this publisher.

Run from a local checkout outside synced storage. Replace `FULL_RELEASE_COMMIT` with
the checked-out commit:

```bash
uv run python -m cairn.docs_publisher --repo . --release FULL_RELEASE_COMMIT \
  --output private/docs-release.json

uv run python -m cairn.docs_publisher --repo . --release FULL_RELEASE_COMMIT \
  --output private/docs-release-publish.json --publish \
  --base-url https://cairn.example.test --key-env CAIRN_DOCS_WRITER
```

Publication and publisher reruns require the project to be in Models Never state; do
not change policy automatically to make a rerun pass. The helper calls no model.

The output file is created with mode 0600 and is never overwritten. When it exists
already, a run whose artifact has exactly the file's bytes accepts it and goes on, so a
second run of one release can name the same file; a file that holds anything else is
refused, in a sentence that names it, and left as it is.
Stdout is only `{"status":"dry-run","notes":N}` or
`{"status":"published","notes":N,"created":N}`. Exit 0 means that operation finished;
exit 2 is a sanitized failure. The presence of an artifact is not a publication
receipt: a failed publish can leave it and some accepted notes behind.

A retry uses the same checkout, full commit, project and output file.
The existing note API deduplicates on text and `source_ref`; repeating a release adds
no note or page version. A partial run can resume this way. A changed guide requires a
new commit and another project. A waiting, rejected or unexpected-version page fails
publication; it is not reported complete. The server may save its normal no-model
tidy as version 2; this one existing arrangement is accepted after full evidence
validation. Do not manually repair release content in
place: review the failure and publish a new release if its content must change.

## Artifact contract: cairn-docs-release-v1

UTF-8 JSON has these exact top-level fields:

| Field | Meaning |
| --- | --- |
| `schema` | The literal `cairn-docs-release-v1` |
| `repository` | The exact repository HTTPS URL above, without `.git` |
| `release` | Expected full commit |
| `project` | Object with the exact `name` and `description` above |
| `notes` | Ordered array of the following objects |

Each note has `path` (one whitelisted path), `start_line` and `end_line` (inclusive,
1-based original file lines), `page` (expected slug), and `payload`. `payload` has
exactly `text`, `title`, `topic`, `label` and `source_ref`, the fields sent to
`POST /api/projects/<project-name>/notes`.

Files are split into consecutive groups of at most 80 original lines, preserving
every byte of text and its final newline. Boundaries come from the complete file's
existing Markdown chunker: no fenced code, paragraph, list item, table, quote, heading
or front matter is divided. Each isolated group must reproduce the complete file's
passage kinds, exact text and translated line ranges; otherwise it is refused. Each
note is at most 16 KiB of UTF-8 text. An indivisible passage that exceeds a bound is
refused, not truncated or reinterpreted. Group lengths vary, and all original line
ranges remain contiguous. For guide `connect`, the first emitted group has `page`,
`title` and `topic` equal to `docs-connect-0001`; successive emitted groups end in
`0002`, `0003`, and so on, independently of their starting line. The same rule applies to each guide basename. `label` is
`cairn-docs-release-v1`. For example, a first three-line group has:

```text
https://github.com/macayaven/cairn/blob/<full-commit>/src/cairn/guides/connect.md#L1-L3
```

That is the exact `source_ref`, including both inclusive line numbers. It points to
original repository lines. Evidence's `start_line` and `end_line` are local to the
note. Translate to original file lines by adding `note.start_line - 1` to each.
The artifact is trusted local deployment input, produced from the verified release;
it is not obtained from a customer's workspace or an arbitrary URL.

## Read-only consumer contract

The helper (`cairn.web.docs_helper`) does not import the publisher and makes no HTTP
request. It reads through the reader key's own view of the store, in the server process,
with the permission checks every request with that key has. It never uses the writer key.

1. Validate the local artifact's schema, exact repository, expected full commit,
   exact release project name/description and finite whitelist. Reject stale or
   different commits. Check group ordering, contiguous original line ranges of at
   most 80 lines, matching title/topic/page, label, original text and exact
   commit/path/line `source_ref`. Reassemble all groups of each approved file first,
   run the existing whole-document Markdown chunker, and require every isolated
   group's passage kinds, text and translated line ranges to equal its complete-file
   passages. Reject divided or reinterpreted passages. Derive original section context
   from the complete file; a note can begin within an existing section.
2. The reader key must be a key, read-only, and reach exactly the release project, by
   its id: not a set holding it. The project must have the exact name, description and
   kind `documents`. The helper calls no model.
3. The project's pages must be exactly the artifact's, with no omission or extra. A
   partial publication is unavailable to the helper. Each page's title is its slug,
   at `version: 1`, or at `version: 2` with `metrics.arranged_by: "cairn"` (the
   existing no-model tidy). An unmarked second version, a model arrangement or any
   later version is refused. A page's `body` is never used.
4. Every passage a claim cites must belong to a note with the page's slug as its title,
   the artifact's exact `source_ref` and `via: "api"`. Its local line range and text
   must match a content passage of the artifact note. In v1 a content passage is a
   `paragraph`, `bullet`, `code`, `table` or `quote` from cairn's existing
   section-aware Markdown chunker; headings and front matter are context. Claim text
   must equal its cited passage text. Every expected content passage must be cited.
   Only validated passage text is shown, with the repository URL and translated
   original lines.

Step 3 counts first: at most one page head more than the artifact has pages is read,
with each page's claim count, and a project that holds more than the artifact is refused
before any page, passage or note is read. Each read after that is bounded by the
artifact's own counts and lengths, and never reads a page's body or a note's text
([Documentation search in the app](documentation-helper.md) has the bounds).
The helper does steps 2 to 4 at most once every 10 minutes and keeps the verified
passages until the next time; each search in between repeats step 2 only. A failed
verification is remembered for 60 seconds, during which searches repeat step 2 only.

The source URLs identify a release; they do not make server content cryptographically
immutable. The owner can still change or delete it. Revoking the publishing key removes
that key's writes, not an owner's powers. Strict version/provenance checks detect
unexpected changes and reject them. The helper must never infer permission to write
from its ability to read. There is no server-enforced project seal or atomic release
transaction in the existing API.

## Bounds and failures

The entire artifact and all six files are validated and scanned before the first
network request. The scanner uses the existing secret-pattern guard and additionally
refuses `.ts.net` hostnames; it cannot establish that arbitrary prose contains no
secret. Review the approved public guides before authorizing publication.

Bounds: six files, 128 KiB per file, 80 lines and 16 KiB per note, 128 notes/pages,
1,500 content passages across the release, 256 KiB per API response, and 4,096 requests
per run. Requests have a 20-second HTTP timeout and no retries or redirect following.
The publisher preflights every existing page and its evidence before writing, then
validates the complete release after writing. It requires saved/unchanged version 1
from a new note call; an unchanged duplicate may return the verified no-model tidy
version 2. An interruption can leave a partial release; it never becomes
usable merely because some pages exist. The helper applies the artifact's bounds
(six files, 128 KiB per file, 80 lines and 16 KiB per note, 128 notes, 1,500 content
passages, 512 KiB of artifact) and at most 80 claims per page and 80 passages per claim.
