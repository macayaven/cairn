# The connector protocol, version 1

A connector is a small web service you run yourself, in any language. cairn asks it for what
is new in a system of yours, and it answers with a list of items. cairn does everything else:
the schedule, the switch, writing each item once, the bounds, the guard against secrets and
the deadlines. A connector runs no code inside cairn, and cairn never writes to a connector.

This document is the whole protocol. The words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are
used as RFC 2119 defines them. A rule marked "cairn" binds cairn; the others bind the
connector. `examples/connector/connector.py` is a reference connector that keeps every rule,
and `connector-check` (below) tells you which rules a connector keeps before a source reads it.

## The transport

- cairn sends each request as `POST` to the source's `url`, exactly as written, and nowhere else.
- The `url` MUST be `https`, or `http` only to `127.0.0.1` or `localhost`. It MUST NOT hold a
  user or password or a fragment, MUST be written as an HTTP client sends it (the host in lower
  case, anything else percent-encoded), MUST be at most 2,048 characters, and MUST NOT hold
  anything that looks like a credential. cairn refuses a source whose `url` breaks this.
- cairn sends the secret only in the `Authorization` header, as `Bearer <secret>`, and only to
  that `url`. The secret is read from an environment variable of the process that syncs; it is
  never in the source's settings. A connector MUST compare it in constant time and MUST NOT log
  it, nor put it in an answer, a cursor or an error.
- cairn never follows a redirect. An answer of 3xx fails the run.
- cairn waits at most 30 seconds to connect, and at most 30 seconds for each part of an answer,
  and stops asking when the run's own time for asking runs out. A connector SHOULD answer each
  request within 30 seconds; `connector-check` holds it to that.
- cairn sends one request at a time for a source. A run holds its source for at most 15
  minutes, longer than the 10 minutes a run is given, so two runs of one source overlap only
  when one outlives its hold.

## The request

The body is one JSON object, sent with `Content-Type: application/json` and
`Accept: application/json`:

- `protocol`: the number 1.
- `since`: a time in RFC 3339, in UTC with "Z". It is when the source was last switched on, and
  it stays the same until the source is switched off and on again.
- `cursor`: `null` on the first request after a switch-on; after that, the `cursor` of an
  earlier answer, sent back unchanged: the previous answer's, or, after a run was cut off, the
  cursor the unfinished answer was asked with.
- `limit`: the most items the answer may hold, a whole number from 1 to 200 (50 unless the
  source says otherwise).

A connector MUST ignore a key of the request it does not know.

```json
{
  "protocol": 1,
  "since": "2099-11-14T09:00:00Z",
  "cursor": "4098330000000000000:memo-2.md",
  "limit": 50
}
```

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "A request of the connector protocol, version 1",
  "type": "object",
  "required": ["protocol", "since", "cursor", "limit"],
  "properties": {
    "protocol": {"const": 1},
    "since": {"type": "string", "format": "date-time", "pattern": "Z$"},
    "cursor": {"type": ["string", "null"], "maxLength": 2000},
    "limit": {"type": "integer", "minimum": 1, "maximum": 200}
  }
}
```

## The answer

A connector that accepts the request MUST answer with status 200,
`Content-Type: application/json`, and a body of at most 4 MiB (4,194,304 bytes) that is one
JSON object:

- `protocol` (MUST): the number 1. Another value, or none, fails the run: cairn reads the
  connector as speaking another version.
- `items` (MUST): a list of at most `limit` items.
- `cursor` (MAY be left out, which counts as `null`): a string of at most 2,000 characters, or
  `null`. cairn sends it back as the next request's `cursor`. It MUST NOT hold the secret.
- `more` (MUST): `true` when the connector has more items after this answer, else `false`.

Each item is a JSON object:

- `id` (MUST): a string of 1 to 200 characters that names the item for as long as it exists.
  Ids MUST be unique within an answer.
- `text` (MUST): a string that is not blank. It becomes the note's text, as given. Besides the
  answer's 4 MiB, it meets the rules every note meets: the headings above any passage take at
  most 400 bytes in all, and a note's headings outnumber its passages by at most 8. It fits one
  page: its passages (its paragraphs, list items, code blocks, tables and quotes, as cairn
  counts them) hold at most 400,000 characters in all, and at most 2,000 of them. cairn does not
  cut an item's text. An item whose text breaks any of these is skipped with the reason; one
  past a page's bounds is skipped as longer than a page holds.
- `title` (MAY): a string of at most 200 characters.
- `topic` (MAY): a string of at most 200 characters.
- `source_ref` (MAY): a string of at most 500 characters saying where the item came from.
- `at` (MAY): when the item happened, in RFC 3339 with its zone. If given, it MUST NOT be
  before the request's `since`.

A key whose value is `null` counts as absent. cairn ignores any other key, of an item or of the
answer; a connector SHOULD NOT send one, since a later version may give it a meaning.

```json
{
  "protocol": 1,
  "items": [
    {
      "id": "memo-3.md",
      "text": "# Memo 3\n\nAda moved the north warehouse audit to Friday.\n",
      "title": "Memo 3: north warehouse audit",
      "topic": "warehouse",
      "source_ref": "notes/memo-3.md",
      "at": "2099-11-14T09:30:00Z"
    }
  ],
  "cursor": "4098331800000000000:memo-3.md",
  "more": true
}
```

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "An answer of the connector protocol, version 1",
  "type": "object",
  "required": ["protocol", "items", "more"],
  "properties": {
    "protocol": {"const": 1},
    "items": {"type": "array", "maxItems": 200, "items": {"$ref": "#/$defs/item"}},
    "cursor": {"type": ["string", "null"], "maxLength": 2000},
    "more": {"type": "boolean"}
  },
  "$defs": {
    "item": {
      "title": "An item",
      "type": "object",
      "required": ["id", "text"],
      "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 200},
        "text": {"type": "string", "pattern": "\\S"},
        "title": {"type": ["string", "null"], "maxLength": 200},
        "topic": {"type": ["string", "null"], "maxLength": 200},
        "source_ref": {"type": ["string", "null"], "maxLength": 500},
        "at": {"type": ["string", "null"], "format": "date-time"}
      }
    }
  }
}
```

The schemas say the shape; these rules go further:

- **The order.** A connector MUST give its items in an order that does not change, so that the
  cursor means "after this item". It SHOULD give them oldest first, so that an item added later
  comes after the ones before it.
- **`more` moves on.** An answer with `more` true MUST give at least one item, or a cursor other
  than the one it was sent. One that does neither fails the run.
- **The same request twice.** Asked a request it was asked before (after a run was cut off), a
  connector MUST give the items it gave before, in the same order: the same answer exactly when
  its `more` was true; when its `more` was false, it MAY add newer items after them.
- **The secret.** No answer, cursor, id or error holds the secret.

## Refusals and errors

- **401 or 403**: the secret was refused. A connector MUST answer one of these, with no item,
  to a request whose `Authorization` it does not accept. The run fails, naming the variable that
  holds the secret, and nothing more is asked until the next run.
- **429**: the connector asks cairn to slow down. A connector MAY send `Retry-After`; cairn reads
  it as "not before the next run", and asks nothing more in this run. A 429 after at least one
  answer was received in this run stops the run without failing it, keeps the cursor of the
  last answer whose items were all written or skipped, and the report says the connector asked
  to slow down. A 429 on the run's first request fails the run, with no wait and no second
  request; the next run asks again from the same cursor. cairn never waits inside a run for a
  429.
- **3xx, 5xx, any other status, a timeout, a network error, an answer past 4 MiB, an answer that
  is not JSON or not this shape**: the run fails and the cursor does not move. The next run asks
  the same request again.
- An answer that is not 200 MAY carry `{"error": "a short sentence"}`. cairn shows that
  sentence in the run's reason, on one line, when it is at most 200 characters and holds
  neither the secret nor anything that looks like a credential. cairn reads nothing else of an
  error's body, and at most 64 KiB of it.

```json
{"error": "the archive is being moved; try again after 10:00 UTC"}
```

## What cairn does with an answer

- Each item, in the connector's order, becomes a note in the source's project, written via
  "connector", with the label "connector". Its `text` is the note's text. Its `title`, when given, is the
  note's title; else the note's front matter, first heading or first line names it. A title is shown and stored at
  its first 80 characters, so put what tells items apart first. Its `source_ref`, when given,
  is the note's origin; else it is the source's name, a colon and the item's `id`.
- **Once per id.** An item is written the first time cairn stores it and never again, whatever
  the connector later sends for that `id`, for as long as the source exists. An edit to an item
  is not written: give the new version a new `id`. An item that was refused (it broke a rule
  here, or held the secret, or the note could not be stored) was never stored, so it is judged
  again when the connector sends it again. If a run dies between storing a note and recording
  its item, and the connector then sends other text for that `id`, a second note is written.
- **Bounds.** An item that breaks a rule of its own (no `id`, a blank `text`, a `text` longer
  than a page holds, a field past its bound, an `at` that is not a time with its zone) is
  skipped with a reason, and the others are written. The run's report counts the skips by reason; it never shows an item's text.
- An item whose `at` is before `since` is left out and not counted. An item without `at` is
  written.
- An answer that breaks a rule as a whole (its shape, its `protocol`, its cursor, `more` that
  does not move on) fails the run, and the cursor does not move.
- **The cursor** is kept once every item of its answer was written or skipped. A run cut off
  inside an answer asks that answer again next time, and the items it already wrote count as
  unchanged.
- **A run's bounds.** One run asks for at most 20 answers, and asks no more once it holds 16 MiB
  of answers. A run that reaches either bound while `more` is still true ends as a run that worked, keeps
  the cursor it reached, and its report says so; the next run goes on from there. If, in such a
  run, every item it was given had been written before, the report says that too: a connector
  that sends the same items again and again does not move on.
- After a source is switched off and on, the first request sends `cursor` null and the new
  `since`.
- cairn cannot know whose words a connector brings, so its notes go to no model unless the
  operator allows it.

## What a connector can rely on

- Each request is a `POST` to its `url`, with the secret as the `Bearer` of `Authorization`.
- `since` changes only when the source is switched on again. `cursor` is one the connector gave,
  unchanged, or `null`. `limit` is the same from one request to the next unless the source's
  settings change.
- cairn keeps a cursor only once every item of the answer that gave it was written or skipped;
  a run that stops before that sends the same request again next time.
- One request at a time for a source.
- cairn never writes to a connector and never sends it anything but the request above.

## Checking a connector

`connector-check` asks a connector as a source would and prints one line per rule, `ok` or
`FAILED`, always in this order:

1. the answer's shape and protocol: status 200, JSON, the shape above, `protocol` 1;
2. ids unique within an answer;
3. every item within its bounds;
4. no item's at before since;
5. the same request twice gives the same items first;
6. every answer with more moves on: it follows at most 20 answers, as a run does;
7. the cursor's size, and no secret in it;
8. a wrong secret is refused (401 or 403, no items): it sends a made-up secret;
9. answers within the deadline: at most 30 seconds each.

```sh
python -m cairn.server connector-check --url https://feed.example.test/items \
    --secret-env FEED_SECRET [--since TIME] [--limit N]
```

`--since` is 30 days ago unless given, and `--limit` is 50. It exits 0 when every rule holds, 1
when one fails, and 2 when it could not run (a bad option, or the variable unset). It never
prints the secret or an item's text, names an item only by its `id`, and writes nothing.

## Versions

Changelog:

- **Version 1**: the first version, as written here.
- **Version 1, a page's bounds stated**: the bound on an item's `text` in characters and
  passages is written out, and an item past it is skipped as longer than a page holds. The
  bound is not new: such an item was skipped before, as a full page.

A version 2 MAY add keys to the request, the answer or an item, MAY add statuses with a meaning
of their own, and MAY raise a bound. It MUST NOT change what a key of version 1 means, MUST NOT
lower a bound of version 1, MUST NOT write an item more than once per `id`, and MUST NOT send the
secret anywhere but the `Authorization` header to the source's `url`. A request of version 2
says `"protocol": 2`; a connector of version 1 answers `"protocol": 1`, and cairn does not read
that answer as version 2.
