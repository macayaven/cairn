> Governed by ~/.claude/CLAUDE.md (canon)

# cairn: project facts

Provenance-first memory compiler for AI agents: notes → section-aware spans → extracted facts → compiled pages whose claims cite spans → search → MCP.

## Commands
- Setup: `uv sync`
- Tests: `uv run pytest`
- Lint/format: `uv run ruff check . && uv run ruff format --check .`
- Model eval (T4): `uv run python evals/t4_compile.py --model M ...`. It calls real models, so ask the maintainer before adding `--yes`: on llama-swap a run evicts the loaded model. Reports go to `private/`.

## Rules
- **TDD.** Write the failing test first, run it and see it fail for the expected reason, write the minimum code to pass, then refactor with tests green. No production code without a failing test that asked for it.
- **Tests are deterministic and offline.** LLMs go through the fake provider; HTTP goes through `httpx.MockTransport`. Real-model runs are evals, not tests.
- **Fixtures are synthetic.** Real notes, gold sets and eval output built from anyone's notes live in `private/` (gitignored) and never in `tests/`, docs or examples.
- **The product's own LLM calls** use API keys or a local OpenAI-compatible endpoint, never subscription CLIs.
- **Originality.** Don't copy the UI, branding, API names or text of any hosted memory product.
- **Memory stays local.** Nothing about this project is read from or written to a hosted memory service. This overrides the canon's memory section for this repository. Decisions go in `docs/decisions.md`; working notes that are not for the repository go under `private/` (gitignored).
- **Git.** No commits unless the maintainer asks; checkpoint finished tasks by staging.
