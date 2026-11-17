# cairn v0 spec

Draft, 2026-11-10. The design was reviewed by an advisor model; decisions are recorded below with their reasons and the options they replaced.

## Problem

Compiled agent memory fails in two quiet ways:

- **Silent loss.** A flag, a version, or a command line in the source never reaches the page.
- **Misattribution.** A fact from one tool's section lands on another tool's page, and an agent that trusts the page runs the wrong command.

Both were observed in a hosted memory product on one source document (a dated anecdote, n = 1). Both can be measured deterministically, without a model in the loop.

## v0 deliverable: the linter first

1. **`cairn lint`** checks a set of compiled pages against their source document.
   - It works on any tool's pages ("plain" mode).
   - It enforces stricter guards on pages that cite their source spans ("cairn" mode).
2. **Next:** a compile adapter, so models can be scored on a public synthetic corpus. It is one structured LLM call with retry behind a thin protocol, and the OpenAI-compatible endpoint comes first.

## Data model

| Unit | Task | Definition |
|---|---|---|
| Span | T1 | The smallest citable unit: front matter, heading, paragraph, top-level list item with its nested items, fenced code block, table, or quote. It carries its heading path, its 1-based line range, and a stable content-hash id. |
| Fact | T2 | A typed token extracted from a span: code block, inline code, URL, path, ISO date, version, flag, or number with unit. Code is atomic. A code-block fact exposes its non-comment lines so it can be scored line by line. |
| Page | T3 | A Markdown file. A cairn-format page adds `<slug>.claims.json`: claims (text and passage ids) and declared `omitted_facts`. `manifest.json` lists `unassigned_passages`. |

## Guards (cairn mode) and warnings

| Code | Level | Meaning |
|---|---|---|
| `provenance.missing_passages` | fail | A claim cites no span. |
| `provenance.unknown_passage` | fail | A claim cites an id that isn't a source span. |
| `provenance.claim_not_in_body` | fail | The sidecar claims something the page doesn't say. |
| `grounding.fact_not_in_cited_passages` | fail | A fact in the claim is absent from every span it cites. |
| `attribution.other_entity` | fail | A claim on X's page cites a span about Y that never mentions X. |
| `retention.fact_dropped` | fail | A fact of a cited span is neither on the page nor declared omitted. |
| `retention.code_line_dropped` | fail | A command line from a cited code block is missing. |
| `coverage.passage_unaccounted` | fail | A content span is neither cited nor declared unassigned. |
| `attribution.text_mention_only` | warn | A span is linked to X only because its text mentions X. |
| `placement.candidate` | warn | A flag, path or long inline-code fact sits on X's page, but every source span containing it is about one other entity. This is a lower bound, confirmed by hand before publishing. |
| `entity.duplicate` | warn | Two pages share a normalised title. |

Plain-mode metrics (both modes): retention by fact kind, code-line retention, span coverage, zero-fact span count, duplicate-entity rate, compression ratio, and placement candidates.

Exit codes: 0 clean; 1 on violations, or when a `--fail-under` threshold is breached; 2 on bad input.

## Fair comparison rule

- A head-to-head against another tool uses **only plain-mode metrics**, on the same public synthetic corpus, with independently labelled gold.
- Cairn-only metrics are reported for cairn alone and labelled as not computable elsewhere.
- Before publishing numbers about another product, read its terms. Without a clear basis, publish cairn's own numbers and describe other tools' gaps only as dated anecdotes.

## Decisions

| Decision | Why | Rejected |
|---|---|---|
| Ship a deterministic linter before the compiler | It is the smallest artifact that yields credible numbers for any tool. | Building the compiler first |
| Add grounding (d) and coverage (e) guards | Attribution alone misses a flag cited from the right section that doesn't contain it, and per-page retention misses a block no page cites. | Attribution and retention only |
| Section-aware spans, code atomic and scored per line | Attribution needs heading context, and partial command loss must be visible. | Fixed-size chunks; whole-block code scoring |
| Normalise only whitespace, case and thousands separators | Anything smarter hides real loss; the rest goes to audit. | Unit and date conversion |
| SQLite as the source of truth, deterministic Markdown export (later) | Claim→span links and versions are relational, and Markdown-as-truth invites edits that break provenance. | Markdown as truth with a rebuildable index |
| Own thin LLM protocol | One structured call with retry, a short dependency list, and a trivial fake for tests. | Pydantic AI |
| Cut from v0: embeddings, auditor model, Anthropic adapter, Markdown import, usage tracking | None of them is needed for the first credible number. | Full product scope |
| Distinct MCP tool names (later) | Don't copy another product's API surface. | Mirroring existing tool names |

## Known limits

- **Prose facts with no extractable token** ("has no cache store") and negation flips are not caught by guards. An auditor model is v1 work.
- **Retention can be gamed** by concatenating sources. Compression ratio and omission rate are reported beside it.
- **Grading bias.** The extractor defines what counts as a fact. Gold labels are model-drafted pending maintainer review, and a held-out fixture the implementers never saw is reported separately.

## Process

- **Frozen contracts.** Acceptance tests, fixtures and gold are written before implementation and checked by hash. Implementers are fresh subagents working test-first against them, and they may not edit them.
- **Review.** Each task gets one reviewer from a different model vendor, plus a design arbiter whose findings must cite a failing test or a source line. Milestones get one extra review. At most two reviewers per task.
- **Privacy.** Public fixtures are synthetic. Real-data evals live in `private/`, which is gitignored.

## Tasks

| Task | Acceptance |
|---|---|
| T1 span chunker | `tests/acceptance/test_t1_spans.py` |
| T2 fact extractor | `tests/acceptance/test_t2_facts.py`: gold recall ≥ 0.95 and precision ≥ 0.90 |
| T3 `cairn lint` | `tests/acceptance/test_t3_lint.py`: a good page set passes; dropped code lines, cross-entity citations, ungrounded flags, uncited claims, unaccounted spans and undeclared drops each fail with the right code |
| T3 multi-source core | `tests/acceptance/test_t3_multisource.py`: one page may cite spans from many notes; grounding uses only the cited spans |
| W1 store and baseline compiler | `tests/acceptance/test_w1_store.py`: projects, hashed keys and sessions, notes with spans, versioned pages, FTS5 search, reads never write |
| W2 web service | `tests/acceptance/test_w2_server.py`: auth over API, UI and MCP; cairn's own MCP tools; REST; claim-to-span provenance in the UI; a setup page without secrets |
| W3 lint gate | `tests/acceptance/test_w3_gate.py`: the compiler saves only pages that pass `lint_pages` over their cited spans |
| W4 client integration | Done outside this repository, in the maintainer's own tooling: a renderer wrote a cairn MCP entry per client from an env file. The connect guide now has each client's steps. |
| W5 container image and CI/CD | `scripts/smoke_container.py` passes its 10 checks against the image on linux/amd64 and linux/arm64; `.github/workflows/ci-cd.yml` publishes to GHCR only from `main` and `v*` tags |
| T4 compile adapter and per-model numbers | `tests/acceptance/test_t4_llm_compile.py`: a model drafts claims for the new spans and only pages that pass the W3 gate are saved; unusable output and provider failures are rejections with a reason; the provider never leaks its key or host. `tests/acceptance/test_t4_coverage_guard.py`: under `strict_lint_gate` a draft that leaves a new content span uncited is rejected with `coverage.passage_unaccounted`, and the extractive default is untouched. Numbers per model: `evals/t4_compile.py` |

## LLM compile adapter (T4)

- **Seam.** `compile_note(..., compose=...)` takes a composer that drafts claims for the new spans; the extractive baseline is the default. `LLMComposer` offers the model only the new spans, raw, each with its id and heading path, and asks for JSON claims with span ids. cairn assigns the claim ids and renders the body, so every sentence on the page is a claim that cites its spans.
- **Gate.** The caller passes the gate, and a model's draft gets `strict_lint_gate`: the lint gate's guards, judged against the offered spans and those the page already cites, whether or not a claim cites them. A new content span that no claim cites fails `coverage.passage_unaccounted`. A citation of any other span, even one in the store, is `provenance.unknown_passage`.
- **Failures.** Unusable output (not JSON, the wrong shape, no claims, cut off at the token limit) is retried with the problem named, up to 3 calls. After that the compile is `rejected` with `compile.invalid_output`. A provider failure is `rejected` with `compile.provider_error`. Nothing is saved, and the reason is in the result.
- **Provider.** One OpenAI-compatible chat completion with temperature 0 and JSON mode. The endpoint, key and model come from `<PREFIX>_BASE_URL`, `<PREFIX>_API_KEY` and `<PREFIX>_MODEL` when the provider is built, with no default URL. Transient statuses and dropped connections are retried. Error messages never hold the key or the host.
- **Eval.** Each draft is scored twice: by the gate (saved or not), and by `lint_pages` over every content span of the note (spans cited, retention by fact kind, code-line retention, compression, ungrounded and verbatim claim shares). Source-level means cover only the runs that produced a draft, and the table counts them. The extractive baseline is the reference row. A report records the commit, whether the tree was dirty, and a sha256 of the code that ran.
- **Coverage (decided 2026-11-10).** A model's draft must cite every new content span. `lint_gate` lints only the spans a page cites, so it would save a draft that skips one. The LLM path therefore gates with `strict_lint_gate`. `serve` keeps `lint_gate` with the extractive composer, which cites every new span by construction. The guard lives in the gate, not in `compile_note`, so it holds only where the caller passes `strict_lint_gate`: the eval does today, and `serve` must once the composer reaches it.

## Web service (v0)

One Starlette process serves the REST API (`/api/*`), the MCP endpoint (`/mcp`, Streamable HTTP, stateless JSON) and the web UI over one SQLite file. Notes compile synchronously, so a note is searchable as soon as `note` returns. Auth middleware covers the API, the UI and the mounted MCP app. API keys and UI sessions are stored only as SHA-256 hashes. GET requests and read tools never write. Projects are isolated from each other; `recall` without a project searches all of them. Compiles are serialised, so two notes on one topic never both build on the same page version.
