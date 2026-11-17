# Documentation search in the app

**Help** searches the guides of one published documentation release. It is off unless the
server is started with a release. Off, there is no `/help` page (the address answers as
any unknown address does) and no Help link. On, signed-in people see **Help** in the
footer.

A search is 1 to 200 characters, sent as a form body, never in the address. An empty or
longer search shows the search page again with that rule. Results are up to eight
passages of the guides that hold every word of the search (its first eight words,
ignoring case): those that hold it as typed come first, then the others, each in the
order of the guides. Each is shown rendered, as the guides are, with the headings above
it and a link to its lines at the release's commit; a link inside a passage to a heading
of its own guide goes to that guide's page. A search with no match says so. The helper calls no model, and does not
save or log what was searched for.

Passages are shown as `/docs` shows the guides: `{{name}}`, `{{slug}}` and `{{SLUG}}` are
filled in with the product's name. Under a brand, the operator's guide (`run`) is left
out, a passage that still names the software outside one of its identifiers is not shown
(`CAIRN_API_KEY` or `python -m cairn` stay as `/docs` keeps them; no passage of the other
five guides is left out today), and no passage links to the source repository.

## Switching Help on

In this order:

1. Make the release project and its two keys, then build and publish the release, as
   [Documentation releases](documentation-publisher.md) says. Revoke the publisher key
   once publication is complete.
2. The reader key is a **Read only** key on **Selected projects**, with only the release
   project selected: not a project set and not All projects. Help refuses any other key.
3. Put the release's artifact file (the `--output` of the publisher) on the server's host.
4. Put the reader key in the server process's environment, under a variable name of your
   choice, resolved at run time from a protected file. Never put the key on the command
   line.
5. Start the server with all three options (or none):

   ```sh
   python -m cairn.server serve --db /absolute/local/cairn.db \
     --docs-artifact /absolute/local/docs-release.json \
     --docs-release 0123456789abcdef0123456789abcdef01234567 \
     --docs-reader-env DOCS_READER
   ```

   The release is the full 40-character commit; the reader option is a variable's name,
   never its value. A missing or malformed option stops `serve` before it opens the
   database.
6. Sign in, open **Help** and search. If the page says documentation search is
   unavailable, the server's log says why (below).

`create_app` takes the same three settings as `docs_artifact`, `docs_release` and
`docs_reader_env`.

## What a search reads

The first search verifies the release: the artifact file is read and checked (its schema,
repository, commit, project, the six guides and their passages), then the project, its
pages, the passages their claims cite and those passages' notes are read through the
reader key's own view of the store, in the server process. Every page must be one of the
artifact's, at version 1 or at the no-model tidy's version 2; every claim must cite
passages of its page's note with the artifact's lines, text, title, `source_ref` and
`via`; every passage of the release must be cited.

Counts come first. At most one page head more than the artifact has pages is read
(`Store.first_page_heads`): a head holds no body and no claim text, and the project's
pages are not counted. A project with more pages than the artifact, or a page with more
claims than its note has passages, is refused there (`release_too_large`): however many
pages the project holds, that costs the same 12 rows of under 1,000 characters and the
same SQLite steps, the key's check included.

Every read after that is bounded by the artifact in rows and in text, and a text longer
than its bound is compared in SQL and refused before it is returned:

- one page's claims and metrics per artifact page (`Store.page_claims`), never its body,
  each at most 12 times its passages' text plus 512 characters per passage (a stored
  claim escapes characters outside ASCII);
- no more passages than the artifact has (`Store.spans_within`), each no longer than the
  artifact's longest passage or headings;
- no more notes than it has pages (`Store.note_sources`), never their text, each with a
  `source_ref` no longer than the artifact's longest.

So the text one verification reads is at most about 25 times the release's passage text,
plus 1 KiB per passage and the longest passage once per passage. A page's body never
counts.

One verification of the six real guides (19 pages) is 25 store reads, 48 SQL statements,
572 rows and 379,102 characters on an install without plans: the key's check (3 reads),
the heads (1), one read per page, the passages (1) and their notes (1). With plans,
reading the plan in force adds one to each check of the key.

The verified passages are kept in memory for 10 minutes (`VERIFY_EVERY`), then the
release is verified again. A search in between checks the reader key again (still a key,
still read-only, still reaching only the release project: 3 store reads, 4 statements)
and searches the kept passages. A failed verification is remembered for 60 seconds
(`RETRY_AFTER`): a search in that time checks the reader key and answers unavailable with
the same reason, reading nothing else; the first search after it verifies again. One
verification runs at a time; a search that arrives during one waits for it.
The work runs in a worker thread, not on the event loop. The reads go through no route,
so a plan's request rate limit does not count them and the access log does not record
them.

A release changed in the store after a verification is still searched until the next
one, at most 10 minutes later, and refused from then on. A revoked reader key is refused
at the next search.

## When it is unavailable

Each failed search writes one WARNING on the logger `cairn.web.docs_helper`:
`help unavailable: <reason>`. It never holds the key or the search.

| Reason | What failed |
| --- | --- |
| `settings` | The reader option is not a variable name (only through `create_app`) |
| `release` | The artifact file is missing, unreadable, or not this release |
| `reader_unknown` | The variable is empty, or the key is wrong or revoked |
| `reader_not_read_only` | The key can write |
| `reader_too_wide` | The key reaches more than the release project, or reaches it through a set |
| `project` | The key does not reach the release project, or its name, description or kind differ |
| `release_too_large` | The project has more pages than the artifact, a page more claims than its note has passages, or its claims cite more passages than the artifact has |
| `pages` | The project's pages are not exactly the artifact's, or a page has another version |
| `evidence` | A claim, passage or note differs from the artifact, or a passage is not cited |
| `form` | The search was not a form with one `q` field; the page shows the search rule |
| `error <class>` | Anything else; only the error's class is logged |

The documentation project is the owner's to manage: the checks find changes, they do not
prevent them. The public guides at `/docs` stay available whatever Help says.
