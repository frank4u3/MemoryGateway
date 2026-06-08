# Memory Gateway — Implementation Roadmap v2

**Goal:** Ship a working local gateway in the shortest path, validating token savings at each phase before adding complexity. Each phase builds on the previous one.

---

## Phase 1 — OpenAI-Compatible Proxy (Day 1–2)

**Goal:** All agents route through the gateway. Zero cache logic. Confirm zero regression.

**Deliverables:**
- FastAPI app with single route: `POST /v1/chat/completions`
- Transparent proxy to DeepSeek (pass request through, return response)
- `X-Agent-ID` header enforcement (required: hermes | opencode | qoder | vscode)
- `GET /v1/health` endpoint with component status
- Docker Compose: gateway + Redis + Qdrant (started but unused)
- `.env` with `DEEPSEEK_API_KEY`, `GATEWAY_PORT=8765`
- Basic request/response logging (agent ID, model, token count, latency)
- Streaming support (pass-through SSE)

**Success criteria:**
- All 4 agents can route through gateway with a one-line config change (`base_url`)
- Response identical to direct DeepSeek call
- No latency regression > 50ms
- Streaming works end-to-end

**Files to create:**
```
gateway/
├── main.py          ← FastAPI app, startup, lifespan
├── router.py        ← /v1/chat/completions handler
├── proxy.py         ← httpx async passthrough to DeepSeek
├── models.py        ← Pydantic request/response models
├── config.py        ← settings from .env
└── logger.py        ← structured request logging
docker-compose.yml
.env.example
```

---

## Phase 2 — Redis Exact Cache (Day 3–4)

**Goal:** Identical requests return instantly from Redis. First measurable token savings.

**Deliverables:**
- `cache/exact.py`: Redis client, SHA-256 hash-key generation, get/set/ttl
- Canonicalizer (minimal): sorts messages, normalizes whitespace, builds cache key
- Response stored in Redis on cache miss
- `x_gateway` extension fields added to response (`cache_tier`, `tokens_saved`, `cache_key`)
- `GET /v1/cache/stats` — basic hit/miss counters
- `DELETE /v1/cache/exact` — manual flush

**Key implementation detail — cache key:**
```python
import hashlib, json

def cache_key(request: ChatRequest) -> str:
    payload = {
        "model": request.model,
        "messages": canonicalize_messages(request.messages),
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }
    return "exact:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
```

**Expected savings:** 10–25%

**Success criteria:**
- Repeated identical request returns in < 5ms
- Stats endpoint shows hit/miss counts
- Cache survives gateway restart (Redis persists)

---

## Phase 3 — Canonical Prompt Builder (Day 5–7)

**Goal:** Increase DeepSeek prefix cache hits by normalizing prompt structure. Different agents asking the same thing about the same files produce identical cache keys.

**Deliverables:**
- `canonicalizer.py`: full implementation of canonical prompt builder
  - Stable system block (project-aware, agent-agnostic)
  - File blocks sorted alphabetically by path
  - Conversation history collapsed to last 4 turns
  - User query always last

- **Normalization rules:**
  - Timestamps → relative format (`2 hours ago`)
  - Absolute paths → `<workspace>/auth.py`
  - UUIDs → `<uuid>`
  - Duplicate whitespace → collapsed
  - Agent-specific noise → stripped

- `context_store.py`: Redis-backed file/snippet registry
  - `POST /v1/context/register`
  - `GET /v1/context`
  - `DELETE /v1/context/{id}`
  - `DELETE /v1/context` (flush by project)

- Updated cache key now uses canonical form, not raw request
- Tests and benchmark for canonicalizer

**Expected savings:** Additional 10–20%

**Success criteria:**
- Hermes and OpenCode asking the same question about the same file produce a cache hit
- Context store accepts file registrations and injects them into canonicalized prompts
- Cross-agent hit rate > 0 in stats

---

## Phase 4 — Repository Intelligence (Day 8–12)

**Goal:** Stop agents from loading entire repositories. Index the codebase at the file, function, and class level so the gateway provides targeted context instead of raw file dumps.

**This is where major savings begin.**

**Deliverables:**
- **repo-indexer:** Walks the workspace, parses code files with tree-sitter
  - Language-specific parsers (Python, TypeScript/JavaScript, Go, Rust)
  - AST extraction: function signatures, class outlines, imports
- **Qdrant vector store** for repository index:
  - File summaries (one per file)
  - Function summaries (signature + docstring + body summary)
  - Class summaries (outline + method list)
  - Dependency graph (import/require relationships)
- **Endpoints:**
  - `POST /index` — Index or re-index the current repository
  - `POST /search` — Semantic search over the repository index
- Integration with canonicalizer: when building the prompt, query repo index for relevant summaries instead of injecting full file contents

**Architecture:**
```
tree-sitter AST
    │
    ▼
repo-indexer ──► Qdrant (repo index collection)
    │
    ▼
/serach endpoint ◄── canonicalizer prompt builder
```

**Expected savings:** 20–40%

**Success criteria:**
- Indexing a 10,000-file repo completes in < 60 seconds
- `/search` returns relevant function summaries for a natural language query
- Prompt context size drops by 50%+ compared to loading full files
- Cross-agent cache hits increase (same function, different agents)

---

## Phase 5 — Project Memory Pack (Day 13–15)

**Goal:** Prevent context explosion. Replace weeks of conversation history with compressed, nightly-generated project state summaries.

**Deliverables:**
- **Memory pack generator** — generates six markdown files nightly:
  ```
  memory/
  ├── architecture.md       ← project structure, key components, data flow
  ├── roadmap.md            ← current milestones, completed items, next steps
  ├── current_state.md      ← active branch, recent changes, open issues
  ├── coding_rules.md       ← language, framework, style conventions
  ├── active_tasks.md       ← what each agent is currently working on
  └── repo_summary.md       ← high-level overview of the repo
  ```
- **Nightly regeneration** via cron or gateway scheduled task
- **On-demand regeneration** via `POST /memory/regenerate`
- Integration with canonicalizer: memory pack is injected into the stable prompt prefix
- Agents receive the memory pack instead of replaying weeks of conversation history

**Expected savings:** Huge. Solves the OpenCode/Qoder/VSCode session-collapse problem.

**Session-collapse problem:** When agents accumulate weeks of conversation history, every request becomes more expensive. The memory pack replaces this — stable state goes in the prefix (cache hits on DeepSeek), and only the current query changes.

**Success criteria:**
- Memory pack generated and stored on first project open
- Prompt token count drops by 60–80% compared to full conversation replay
- Memory pack content is injected as stable prefix → DeepSeek cache fires
- Agents can ask "what are we working on?" without loading full session history

---

## Phase 6 — Metrics Dashboard (Day 16–17)

**Goal:** Measure ROI of all optimizations. Every optimization must show measurable token savings.

**Deliverables:**
- `GET /metrics` — aggregate metrics
- `GET /metrics/cost` — token and cost breakdown
- `GET /metrics/cache` — per-tier cache hit/miss stats

**Response format:**
```json
{
  "tokens_saved": 1234567,
  "estimated_cost_saved": 42.31,
  "cache_hit_rate": 67.2,
  "by_agent": {
    "hermes":   { "tokens_saved": 400000, "hit_rate": 72.0 },
    "opencode": { "tokens_saved": 350000, "hit_rate": 65.0 },
    "qoder":    { "tokens_saved": 280000, "hit_rate": 60.0 },
    "vscode":   { "tokens_saved": 204567, "hit_rate": 55.0 }
  },
  "by_tier": {
    "exact":  { "hits": 4500, "tokens_saved": 900000 },
    "semantic": { "hits": 0, "tokens_saved": 0 },
    "prefix_cache": { "estimated_hits": 1200, "tokens_saved": 240000 }
  },
  "period": "last_7d"
}
```

**Not included (out of scope):**
- Auth / multi-user
- Persistent historical storage beyond Redis TTL
- Alerting
- Grafana / Prometheus integration

**Success criteria:**
- All metrics endpoints return correct, up-to-date data
- Dashboard loads in browser at `http://localhost:8765/dashboard`
- Token savings visible and attributable to specific phases

---

## Phase 7 — Semantic Cache (Day 18–20)

**Goal:** Near-duplicate questions (different wording, same intent) hit the cache. Deliberately deferred until Phases 4–6 are proven — Repository Intelligence has higher ROI.

**Requirements:**
- `sentence-transformers` (local embedding model, CPU-adequate)
- Qdrant collection for semantic cache
- Similarity threshold: `>= 0.98` (strict — false positives are worse than misses)

**IMPORTANT: Disabled by default.** Must be explicitly enabled via:
```env
SEMANTIC_CACHE_ENABLED=true
```
or per-request header:
```
X-Enable-Semantic-Cache: true
```

**Deliverables:**
- `cache/semantic.py`: Qdrant client + embedding generation
  - Embedder: `sentence-transformers` (local, free, CPU)
  - Cosine similarity search with configurable threshold (default 0.98)
  - Store query embedding + response on cache miss
- Semantic cache sits between canonicalizer + repo intelligence + memory pack and upstream call
- `DELETE /v1/cache/semantic` — flush collection
- Threshold and enabled flag exposed in `GET /v1/config`

**Flow when enabled:**
```
Exact Cache miss
    → Canonicalizer → Repo Intelligence → Memory Pack → Semantic Cache (≥0.98)
                                                              ↓ hit → return
                                                              ↓ miss → DeepSeek → store in cache
```

**Expected savings:** Additional 5–15%

**Success criteria:**
- Paraphrased version of a cached question returns a cache hit
- False positive rate (wrong answer returned) is zero at threshold 0.98
- Semantic hits visible separately in stats
- Disabling the feature via config returns to Phase 6 behavior

---

## Phase 8 — Artifact Registry (Day 21–23)

**Goal:** Allow different agent sessions to reuse generated artifacts without another DeepSeek call. This eventually becomes the most valuable feature.

**Deliverables:**
- `POST /artifact/store` — Store a named artifact with metadata
  ```json
  {
    "name": "api-schema-v3",
    "type": "generated_schema",
    "content": "...",
    "agent_id": "hermes",
    "project": "agent-platform",
    "tags": ["api", "v3", "auto-generated"]
  }
  ```
- `POST /artifact/search` — Search for existing artifacts
  ```json
  {
    "query": "API schema for user endpoints",
    "type": "generated_schema",
    "project": "agent-platform"
  }
  ```
- Artifacts stored in Qdrant (vector search) + Redis (metadata, hot cache)
- Cross-worker reuse: Worker 17 can reuse work from Worker 2 without another DeepSeek call

**Use cases:**
- Hermes generates an API schema → Qoder reuses it
- OpenCode generates a test suite → VSCode agent reuses it
- Generated prompts, workflows, and code snippets shared across sessions

**Expected savings:** Additional variable (compounds with all previous phases)

**Success criteria:**
- Artifact stored by one agent is retrievable by another agent
- Search returns relevant artifacts from natural language queries
- Artifact registry integrates with canonicalizer (inject relevant artifacts into prompt context)

---

## Milestone Summary

| Phase | Outcome | Expected Savings | Days |
|---|---|---|---|
| 1 — Proxy | All agents route through gateway | — | 1–2 |
| 2 — Exact Cache | Identical requests cached | 10–25% | 3–4 |
| 3 — Canonicalizer | Cross-agent cache hits | +10–20% | 5–7 |
| 4 — Repo Intelligence | Agents stop loading full repos | +20–40% | 8–12 |
| 5 — Memory Pack | Context explosion solved | Huge | 13–15 |
| 6 — Dashboard | ROI visible | — | 16–17 |
| 7 — Semantic Cache | Near-duplicate hits | +5–15% | 18–20 |
| 8 — Artifact Registry | Cross-worker reuse | Variable | 21–23 |

**Cumulative expected savings with all phases active:** 65–80%+ token reduction vs. baseline (no gateway).

---

## Priority Order Rationale

| Phase | Why at this position |
|---|---|
| Phase 1 | Foundation — everything depends on routing |
| Phase 2 | Simplest cache layer, immediate savings |
| Phase 3 | Enables cross-agent cache hits |
| Phase 4 | Highest remaining ROI — stops repo bloat |
| Phase 5 | Solves context collapse — enables stable sessions |
| Phase 6 | Measurement before optimization |
| Phase 7 | Lower ROI than Phases 4–5, high complexity |
| Phase 8 | Most valuable long-term, but requires all prior phases |

---

## Implementation Notes

### Phase Dependencies
- Phase 2 depends on Phase 1
- Phase 3 depends on Phase 1
- Phase 4 depends on Phase 3 (canonicalizer integration)
- Phase 5 depends on Phase 3 (canonicalizer integration)
- Phase 6 depends on Phase 2 (needs cache stats)
- Phase 7 depends on Phases 4–5 (should sit after repo + memory enrichment)
- Phase 8 depends on Phase 1 (needs the proxy)

### Testing Strategy
Each phase should include:
- Unit tests for new components
- Integration test that verifies the full request flow
- Benchmark for latency and token savings

### Rollback
Each phase is additive. Disabling a phase is a config change, not a code revert:
```env
EXACT_CACHE_ENABLED=true
CANONICALIZER_ENABLED=true
REPO_INTELLIGENCE_ENABLED=true
MEMORY_PACK_ENABLED=true
SEMANTIC_CACHE_ENABLED=false
ARTIFACT_REGISTRY_ENABLED=false
```
