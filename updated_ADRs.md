# Memory Gateway — Architecture Decision Records

---

## ADR-001: OpenAI-Compatible API Surface

**Decision:** The gateway exposes an OpenAI-compatible `/v1/chat/completions` endpoint, not a custom protocol.

**Rationale:** All four target agents (Hermes, OpenCode, Qoder, VSCode Agents) already speak OpenAI protocol. A compatible surface means zero agent code changes — only a `base_url` override. If the gateway were a custom protocol, each agent would need an adapter, multiplying integration effort by 4 and introducing drift.

**Trade-off:** We cannot expose gateway-specific features (context registration, cache warming) through the completions endpoint itself. These require separate endpoints that agents must explicitly call. This is acceptable — context registration is a setup action, not a hot-path operation.

---

## ADR-002: Redis for Exact Cache, Qdrant for Semantic Cache

**Decision:** Two separate stores, not one.

**Rationale:** Redis is the right tool for hash-key lookups — O(1), sub-millisecond, battle-tested, already in the stack. Qdrant is the right tool for vector similarity — it supports cosine distance natively and handles TTL at the collection level. Forcing one store to do both jobs would require either Redis-VSS (adds complexity) or Qdrant for exact lookups (wrong tool, unnecessary overhead).

**Trade-off:** Two stores = two failure points. Mitigated by: (a) both are already running as Docker services in the existing infrastructure, (b) the gateway degrades gracefully — if Qdrant is down, semantic cache is skipped; if Redis is down, exact cache is skipped. The gateway never hard-fails due to a cache store being unavailable.

---

## ADR-003: System Prompt Normalization is Non-Negotiable

**Decision:** The gateway strips agent-supplied system prompts and replaces them with a project-canonical system block. Agents cannot customize the system prompt via the completions request.

**Rationale:** The system prompt is the highest-value prefix cache anchor. If Hermes sends `"You are a helpful coding assistant"` and OpenCode sends `"You are an expert Python developer"`, DeepSeek's prefix cache never fires across agents. A shared, stable system block is required for cross-agent prefix cache hits. This is the primary mechanism through which the gateway earns its existence.

**Trade-off:** Agents lose per-request system prompt customization. Mitigation: agents may pass `X-Agent-Context` in the header (e.g., `"focus: test generation"`), and the gateway appends a one-line context note after the stable block. The stable block remains the same; only a trailing suffix varies. This preserves prefix cache hits on the fixed portion while allowing lightweight behavioral hints.

---

## ADR-004: Single Qdrant Collection Per Project (Not Per Agent)

**Decision:** Semantic cache entries are stored in a per-project Qdrant collection, not per-agent.

**Rationale:** Cross-agent reuse is the entire point of the semantic cache. If Hermes already answered "What does the seed_default_tenant function do?" and Qoder asks "Explain seed_default_tenant to me," the gateway should return Hermes's cached answer. Siloing by agent defeats this. Per-project collections are the right granularity — agents working on `agent-platform` share a collection; agents working on a different project do not cross-contaminate.

**Trade-off:** A cached response from one agent might be returned to a different agent that has different behavioral expectations. Mitigated by: (a) the high similarity threshold (0.92 for semantic cache, 0.98 in v2) ensures only very close matches fire, (b) responses are code/explanation, not agent-identity-dependent.

---

## ADR-005: Repository Intelligence First

**Decision:** Repository Intelligence (Phase 4) is implemented before Semantic Cache (Phase 7). Semantic caching is deferred until the repo index is proven.

**Rationale:** Repository retrieval has higher ROI than semantic caching. Loading entire repositories into context is the largest single source of token waste — agents routinely include thousands of lines of irrelevant code just to reference one function. A tree-sitter-based index that provides targeted function/class summaries saves 20–40% of tokens immediately. By comparison, semantic caching saves 5–15% and requires maintaining a vector similarity index that risks false positives.

**Trade-off:** Semantic cache is delayed by 3–4 phases. An agent asking a near-identical question in a different wording must wait for a DeepSeek call instead of getting a cached answer. Mitigated by: Exact Cache (Phase 2) already covers identical questions across agents after canonicalizer (Phase 3) normalizes the prompt. Semantic cache addresses a diminishing-returns edge case.

**Implementation order:**
```
Phase 2 (Exact Cache) → Phase 3 (Canonicalizer) → Phase 4 (Repo Intelligence)
  → not: Phase 4 (Semantic Cache)
```

**Evidence for higher ROI of repo intelligence:**
- Agents load 5,000–20,000 tokens of full file contents per request
- A function summary is typically 50–200 tokens (95%+ reduction)
- File summaries further compress irrelevant sections
- The same repo index powers /search endpoint for agent-invoked lookups

---

## ADR-006: Memory Pack Strategy

**Decision:** Project state is compressed into a nightly-generated set of six reusable markdown summaries (the "memory pack") rather than replaying full conversation history.

**Rationale:** Agents accumulate weeks of conversation history. Each new request re-sends thousands of tokens of prior turns, most of which are irrelevant to the current query. DeepSeek's prefix cache provides no savings here because the conversation prefix is constantly changing. By extracting stable project state into a memory pack that lives in the stable prefix, the gateway achieves two things: (a) dramatically smaller requests (60–80% fewer tokens), and (b) DeepSeek prefix cache hits on the memory pack portion of every request.

**Memory pack contents:**
```
memory/
├── architecture.md       ← project structure, components, data flow
├── roadmap.md            ← milestones, completed items, next steps
├── current_state.md      ← active branch, recent changes, open issues
├── coding_rules.md       ← language, framework, style conventions
├── active_tasks.md       ← per-agent current tasks
└── repo_summary.md       ← high-level repo overview
```

**Trade-off:** Nightly regeneration means the memory pack can be up to 24 hours stale. An agent working on a rapidly changing codebase might get slightly outdated architecture context. Mitigated by: (a) on-demand regeneration via `POST /memory/regenerate`, (b) individual file context is still injected fresh from the context store, only the high-level summaries are cached.

**Why this solves the session-collapse problem:**
- OpenCode, Qoder, and VSCode agents accumulate unbounded session histories
- After weeks of use, every request carries 10k+ tokens of prior turns
- The memory pack replaces the need to replay history — agents get structured state instead
- Session collapse (where agents lose context due to truncation) is prevented because the important state is always in the stable prefix, not in the truncation-prone conversation window

---

## ADR-007: Metrics-Driven Optimization

**Decision:** Every optimization phase must show measurable token savings before the next phase begins. The metrics dashboard (Phase 6) is implemented before Semantic Cache (Phase 7).

**Rationale:** Without measurement, optimization is guesswork. The gateway's entire value proposition is token and cost reduction, but different projects and usage patterns will see different savings from each phase. By implementing the metrics dashboard early (Phase 6, before the higher-complexity Phases 7–8), decisions about which optimizations to pursue are data-driven.

**Metrics tracked per phase:**
| Phase | Metric |
|---|---|
| Phase 2 — Exact Cache | Exact hit rate, tokens saved by exact cache |
| Phase 3 — Canonicalizer | Cross-agent hit rate, prefix cache estimated hits |
| Phase 4 — Repo Intelligence | Context token reduction vs. full files, /search usage |
| Phase 5 — Memory Pack | Prompt token reduction vs. full conversation replay |
| Phase 7 — Semantic Cache | Semantic hit rate, false positive rate |
| Phase 8 — Artifact Registry | Artifact reuse rate, tokens saved by reuse |

**Trade-off:** Building the dashboard is engineering effort that does not directly save tokens. Mitigated by: (a) the dashboard is a lightweight FastAPI endpoint + static HTML page, estimated at 1–2 days, (b) without it, we cannot prove the gateway works, (c) the dashboard serves as a forcing function for proper instrumentation from Phase 1 onward.

**Go/no-go gates:**
- Phase 3 proceeds only if Phase 2 shows ≥ 10% exact hit rate
- Phase 4 proceeds only if Phase 3 shows measurable cross-agent cache hits
- Phase 5 proceeds only if Phase 4 shows ≥ 20% token reduction from repo intelligence
- Phase 7 proceeds only if the dashboard data shows semantic cache would have saved additional tokens beyond exact + prefix

If a phase does not meet its threshold, the team re-evaluates before adding more complexity.

---

## ADR-008: Embedder is Local-First (Ollama / nomic-embed-text)

**Decision:** Embeddings for semantic cache are generated locally via Ollama + `nomic-embed-text`, not via a cloud API.

**Rationale:** Sending every query to DeepSeek's embedding endpoint to decide whether to skip DeepSeek is logically circular and adds latency + cost. `nomic-embed-text` via Ollama runs on the local machine (CPU-adequate for 768-dim, ~20ms/query), has no per-call cost, and produces high-quality embeddings for code-adjacent text.

**Update for v2:** With Repository Intelligence (Phase 4) also using Qdrant for the repo index, the embedder choice applies to both the repo indexer and the semantic cache. Both use the same local embedding model.

**Trade-off:** Requires Ollama running locally. Mitigation: `sentence-transformers` (Phase 7) is a lightweight alternative that does not require Ollama. If neither is available, semantic cache tier is disabled and the gateway continues with exact cache + upstream only.

---

## ADR-009: Scope Hard Limit — Local Development Only

**Decision:** The gateway is explicitly scoped to local development. No multi-user auth, no TLS, no horizontal scaling, no persistent metrics database.

**Rationale:** Adding production-readiness features prematurely (auth, TLS, HA Redis) would delay Phase 1 by weeks and add operational burden that provides zero value for a solo developer. The gateway runs on localhost, behind no network boundary. Security model: if someone is on your machine, you have bigger problems.

**If/when this changes:** Add TLS termination at Traefik (already in stack), move Redis/Qdrant behind private Docker network (already the default), add API key validation in the gateway middleware. All of these are one-day additions, not architectural changes.

**Note:** This decision is reinforced by the addition of Repository Intelligence (Phase 4) and Memory Pack (Phase 5). These features handle potentially sensitive source code. Running only on localhost ensures code data never leaves the machine.

---

## ADR-010: No LiteLLM Dependency in the Gateway

**Decision:** The gateway calls DeepSeek directly via `httpx`, not via the LiteLLM proxy.

**Rationale:** LiteLLM is already running in the stack as a separate service. Routing gateway → LiteLLM → DeepSeek adds a hop, a failure point, and couples two local services unnecessarily. The gateway's job is to cache, canonicalize, and provide repo intelligence; model routing is LiteLLM's job. These should remain separate concerns with a clean boundary.

**Future integration path:** If multi-model routing is needed (DeepSeek for some queries, local Qwen3 for others), add a routing layer inside the gateway that delegates to LiteLLM for model selection. Do not merge the two services.

---

## Scope Boundaries (What This Is Not)

| Out of scope | Why |
|---|---|
| Agent memory / long-term user memory | That's Qdrant + the agent's own memory system |
| RAG over the codebase | Repository Intelligence (Phase 4) is a targeted index, not general-purpose RAG |
| Multi-project routing | Single project per gateway instance for now |
| Prompt injection detection | Out of scope for local dev trust model |
| Response quality evaluation | No LLM-as-judge in the critical path |
| Production deployment | ADR-009: local dev only |
| Multi-model support | DeepSeek is the only LLM backend |
