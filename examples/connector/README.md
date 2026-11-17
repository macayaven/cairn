# A reference connector

`connector.py` serves the Markdown files of one folder as items, by the connector protocol,
version 1 (`docs/connector-protocol.md`). It uses the Python standard library only, so you
can copy the file and change `items()` to read your own system.

- Each `*.md` file in the folder (not in its subfolders) is one item: the file name is its
  `id`, the contents its `text`, the file's modification time its `at`.
- Items come oldest first. The cursor names the last item given, so the next answer starts
  after it, and the same request always gets the same answer. A file copied in with an older
  modification time than the last item given (`cp -p`, `rsync -t`) comes before the cursor
  and is never served: `touch` it, so that its time is now.
- A file whose name is longer than 200 characters is no item: its name would be its id.
- An item is written once, by its id. A file you edit keeps its name, so the edit is not
  written again: save a new version under a new name.
- The secret is read from the environment, compared in constant time and never logged.
- It listens on 127.0.0.1 unless `--host` says otherwise. Anything that reaches it from
  another machine must go through HTTPS (a reverse proxy in front of it).

## 1. Run it

```sh
export NOTES_CONNECTOR_SECRET=...   # a long random value you make up
python examples/connector/connector.py --folder ~/notes --port 8811 \
    --secret-env NOTES_CONNECTOR_SECRET
```

## 2. Check it

`connector-check` asks the connector as a source would and prints one line per rule of the
protocol, `ok` or `FAILED`. It writes nothing.

```sh
uv run python -m cairn.server connector-check --url http://127.0.0.1:8811/ \
    --secret-env NOTES_CONNECTOR_SECRET
```

It exits 0 when every rule holds, 1 when one fails and 2 when it could not run.

## 3. Add it as a source

```sh
uv run python -m cairn.server source-add --db cairn.db --name notes --kind connector \
    --config '{"url": "http://127.0.0.1:8811/", "secret_env": "NOTES_CONNECTOR_SECRET"}'
uv run python -m cairn.server source-enable --db cairn.db --name notes --project notes
```

The process that syncs (`serve --sync-every`, or `source-sync`) needs the same variable in its
environment. The source starts off: switch it on with `source-enable`, or from the Sources
page, where the project proposed is the one named like the source (`notes` here). From the
switch-on on, each run asks for what is new and writes each item once.
