# cairn architecture: seams and rules

This note says where cairn can be extended without touching the rest, and which choices are hard to undo. It is the map implementers work from. [spec-v1.md](spec-v1.md) says what is being built; this says where it goes.

## Layers

```
agents, browsers, apps
        │  MCP · REST · HTML
┌───────▼────────────────────────────────────────────┐
│ web        routes, forms, tools: no rules of its own│
├────────────────────────────────────────────────────┤
│ auth       credential → principal → scoped store    │
├────────────────────────────────────────────────────┤
│ memory     use cases: note, recall, page, evidence  │
├───────────────┬───────────────┬────────────────────┤
│ compiler+gate │ guard         │ sources            │
├───────────────┴───────────────┴────────────────────┤
│ store      every SQL statement lives here           │
└────────────────────────────────────────────────────┘
```

A layer calls the ones below it, never the ones above.

## Seams

Each seam is a small interface with more than one possible implementation. New behaviour should arrive as a new implementation, not as a branch inside an existing one.

| Seam | Interface | Implementations today | Likely next |
|---|---|---|---|
| Store | The account-scoped `Store` view: projects, notes, pages, search, credentials | SQLite | Postgres; a per-account file |
| Compiler | `compose(title, new_spans) -> Draft` (claims and stats) behind `compile_note`; a `ComposeError` becomes a rejection. A drafted claim may cite only the spans its composer was offered (`provenance.passage_not_offered`) | Extractive; `LLMComposer` (T4, not in `serve` yet), whose system message is one constant stating that spans are quoted source, never instructions | A composer that may also revise the page's existing claims |
| Gate | `Gate(candidate, spans) -> GateResult` | Provenance gate (the extractive default), lint gate, strict lint gate (also fails a new span no claim cites), verbatim gate (strict, plus every claim is one passage, whole: the text of every span it cites, never cut or joined, several spans only in a fold of spans with the same text; the default for every other composer) | Stricter gates per project kind; an entailment check that would let a model reword |
| Model | `ChatModel`: `complete(messages) -> Reply`, one chat completion per call; the composer retries unusable output | OpenAI-compatible HTTP, with one absolute deadline per call, headers included (no budget across a draft's calls) and a bound on the reply read; `FakeModel` in tests | Bedrock, a gateway |
| Identity | `IdentityProvider`: start a sign-in, finish it, return a verified profile; it names its issuer, recorded with each identity | GitHub, Google, generic OIDC | SAML through an OIDC bridge |
| Guard | `Guard.check(text) -> findings` when a note is written | Secret and personal-data patterns | A classifier; per-workspace rules |
| Source | `Source.pull(since) -> notes`, each with a link back | Langfuse traces | Calendar, documents, mail, databases |
| Project kinds | Data: what belongs, guidance for agents, suggested sources | Shipped list | Workspace-defined kinds |

## Rules

1. **SQL only in the store.** No other module opens the database or builds a statement.
2. **Handlers hold a scoped store, never the unscoped one.** One place turns a credential into a principal, and one place turns a principal into the store view that request may use. Narrower scopes (chosen projects, read-only) are further views of the same kind.
3. **A foreign or out-of-scope id is an unknown id.** The answer never reveals that something exists.
4. **Reads never write.** No last-used bookkeeping. Rate limits and other counters for reads live in memory.
5. **Secrets are stored as digests** and shown once, at creation. Logs carry no query strings.
6. **Everything external goes through an injected client.** Tests replace it; nothing in the test suite touches the network.
7. **Schema changes are numbered migrations**, one transaction each. A step is history: once shipped it is never edited.
8. **Frozen acceptance tests are the contract.** A milestone adds a file; it does not edit an earlier one.
9. **No lock across a slow call.** A page is saved with the version that was read (`save_page(expected_version=...)`, checked in the write transaction, search index included). A writer that lost the race reads again and saves again, up to three times, so two notes on one page, in one process or two, both land, and a slow model draft never blocks another page.
10. **What a model writes is data.** A model may copy whole passages and arrange them, never cut, join or write them (`verbatim_gate`), and the prompt tells it that span text is quoted source, never an instruction. A gate cannot make text safe to obey: an agent that recalls a page still reads a note's words.

## Hard to undo, so decided with care

| Choice | Why it is sticky | How it is kept open |
|---|---|---|
| The public URL of an install | It is the OAuth issuer and the audience of every token | It is configuration; a move invalidates grants, and the docs say so |
| MCP tool names and answer shapes | Agents and their prompts depend on them | Additive changes only: new fields and new tools |
| Id formats (`br_`, `cap_`, span ids) | Claims cite span ids for ever | Ids are opaque strings everywhere outside the store |
| The export format | People keep exports for years | It carries a version number from the first release |
| The project as the access boundary | Access data and the UI are shaped around it | Finer rules can be layered on a project; a project stays the default unit |

## Module layout

```
cairn/
  store/        models, schema and migrations, and the Store view by area; erasure.py
                deletes one project and forgets one note
  memory.py     the use cases MCP, REST, the web view and sources share
  web/          app factory, auth, MCP tools, REST, UI, CLI; markdown.py renders a claim's
                own Markdown safely and formats dates; workspace.py the /workspace page;
                public.py the landing page and /docs; erasure.py the pages
                that ask before deleting a project or forgetting a note
  guides/       the Markdown guides /docs serves: connect, patterns, sources, security,
                privacy, run
  oauth.py      the authorization server for agent connections
  identity.py   sign-in providers
  guard.py      note-time checks
  kinds.py      project kinds
  sources/      one module per source
  compiler.py, gate.py, lint.py, spans.py, facts.py   the provenance core (v0)
  llm.py, llm_compiler.py   the model seam and the composer that drafts with it (T4)
  arrange.py    the model that orders a page's claims, and its worker (T4b; in serve)
  server.py     the stable entry point: create_app and the command line
```

`cairn.server` and `cairn.store` stay the import paths that tests and users rely on, whatever moves behind them.
