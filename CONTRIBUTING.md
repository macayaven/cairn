# Contributing to cairn

cairn is under the MIT License. A change you send is under it too.

## Set up

You need [uv](https://docs.astral.sh/uv/) and Docker if you touch the image or the compose files. From the clone's top folder:

```bash
uv sync
uv run pytest tests/unit -o addopts="" -q -p no:cacheprovider
uv run ruff check . && uv run ruff format --check .
```

The unit tests take several minutes. `uv run pytest` runs every suite, acceptance and system tests included.

## How tests are written here

- **Test first.** Write the test, run it, and see it fail for the reason you expect. Then write the least code that makes it pass, and tidy with the tests green. No production code without a failing test that asked for it.
- **Offline and the same every run.** A model goes through the fake provider, and HTTP goes through `httpx.MockTransport`. Runs against real models are evals (`evals/`), not tests.
- **Synthetic fixtures only.** Notes, gold sets and eval output made from anyone's real notes never go into `tests/`, the docs or the examples. Keep them under `private/`, which git ignores.
- **Documents are checked too.** Many tests read the guides and the READMEs and check what they say against the code. When you change what a command does, change the sentence and its test together.

Some files are fixed: `tests/acceptance/`, `tests/fixtures/` and `tests/gold/` are the acceptance contract, and a change to them is a change of what cairn promises.

## Propose a change

1. Open an issue first for anything larger than a fix, so the approach can be agreed before the work.
2. Make a branch, and keep each commit to one step whose tests pass.
3. Open a pull request. Say what changed and why, how you tested it, and what you did not check. CI runs ruff and the tests; a pull request that is ready for review also builds the image and rehearses the compose stack.
4. Decisions that shape the design are recorded in `docs/decisions.md`, with the reason and what was turned down. A pull request that makes one adds it there.

Report a vulnerability privately, as [SECURITY.md](SECURITY.md) says, not in an issue.
