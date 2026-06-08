# Memory Gateway — Architecture Specification v2

**Version:** 0.2  
**Scope:** Local development only  
**Purpose:** Maximize DeepSeek prefix cache hits across all local coding agents to reduce token consumption and context size  

---

## 1. Problem Statement

DeepSeek offers significant token discounts (~75%) when a request hits their server-side prefix cache. The cache activates when the leading tokens of a new prompt exactly match a previously seen prompt prefix. In practice, local coding agents each construct their prompts independently, breaking cache coherence even when they work on the same codebase. The Memory Gateway fixes this by becoming the single, canonical prompt-assembly point for all local agents.

**Token cost drivers without the gateway:**

| Driver | Problem |
|---|---|
| Each agent builds its own system prompt | Identical intent, different token layout → cache miss |
| File context injected in ad-hoc order | Same files, different insertion order → cache miss |
| No shared semantic cache | Agent A already answered this; Agent B asks again |
| No reuse of completed responses | Cold hit every time |
| Agents load entire repos | Massive token waste on irrelevant files |
| Long conversation history replayed | Weeks of session history re-sent every request |

---

## 2. System Overview

The Memory Gateway is an **OpenAI-compatible local proxy** that sits between all coding agents and DeepSeek. Agents point their `base_url` at `http://localhost:8765/v1` and interact as if talking directly to DeepSeek.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            LOCAL MACHINE                                      │
│                                                                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐                       │
│  │  Hermes  │  │OpenCode  │  │  Qoder   │  │VSCode  │                       │
│  │  Agent   │  │  Agent   │  │  Agent   │  │Agents  │                       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───┬────┘                       │
│       └─────────────┴─────────────┴─────────────┘                            │
│                          │                                                    │
│                          ▼ OpenAI-compatible API                              │
│              ┌────────────────────────────────────────────┐                   │
│              │           MEMORY GATEWAY                    │                   │
│              │             FastAPI :8765                   │                   │
│              │                                            │                   │
│              │  ┌──────────────────────────────────────┐  │                   │
│              │  │           Request Router             │  │                   │
│              │  │  (parse, identify agent, dispatch)   │  │                   │
│              │  └────────┬─────────────────────────────┘  │                   │
│              │           │                                │                   │
│              │  ┌────────▼────────┐                       │                   │
│              │  │  Exact Cache    │◄──────► Redis :6379   │                   │
│              │  │  (SHA-256 hit)  │                       │                   │
│              │  └────────┬────────┘                       │                   │
│              │           │ miss                            │                   │
│              │  ┌────────▼────────┐                       │                   │
│              │  │  Canonicalizer  │                       │                   │
│              │  │ (stable prompt) │                       │                   │
│              │  └────────┬────────┘                       │                   │
│              │           │                                │                   │
│              │  ┌────────▼─────────────────────────┐     │                   │
│              │  │  Repository Intelligence         │     │                   │
│              │  │  (tree-sitter parsing, file      │◄────┼──── Qdrant :6333  │
│              │  │   summaries, function/class      │     │     (repo index)  │
│              │  │   summaries, dependency graph)   │     │                   │
│              │  │  /search  /index                 │     │                   │
│              │  └────────┬─────────────────────────┘     │                   │
│              │           │                                │                   │
│              │  ┌────────▼─────────────────────────┐     │                   │
│              │  │  Project Memory Pack             │     │                   │
│              │  │  (architecture.md, roadmap.md,   │     │                   │
│              │  │   current_state.md,              │     │                   │
│              │  │   coding_rules.md,               │     │                   │
│              │  │   active_tasks.md,               │     │                   │
│              │  │   repo_summary.md)               │     │                   │
│              │  └────────┬─────────────────────────┘     │                   │
│              │           │                                │                   │
│              │  ┌────────▼─────────────────────────┐     │                   │
│              │  │  Semantic Cache (Phase 7)        │◄────┼──── Qdrant :6333  │
│              │  │  (disabled by default,           │     │     (semantic)    │
│              │  │   similarity >= 0.98)            │     │                   │
│              │  └────────┬─────────────────────────┘     │                   │
│              │           │                                │                   │
│              │  ┌────────▼────────┐                       │                   │
│              │  │  Artifact       │                       │                   │
│              │  │  Registry       │◄──────► Redis         │                   │
│              │  │  (reuse across  │                       │                   │
│              │  │   workers)      │                       │                   │
│              │  └─────────────────┘                       │                   │
│              │                                            │                   │
│              │  ┌──────────────────────────────────────┐  │                   │
│              │  │  Metrics Dashboard                   │  │                   │
│              │  │  /metrics  /metrics/cost             │  │                   │
│              │  │  /metrics/cache                      │  │                   │
│              │  └──────────────────────────────────────┘  │                   │
│              └───────────────────┬────────────────────────┘                   │
│                                  │                                            │
└──────────────────────────────────┼────────────────────────────────────────────┘
                                   │ HTTPS / DeepSeek API
                                   ▼
                       ┌───────────────────────┐
                       │      DEEPSEEK API     │
                       │   (prefix cache ✓)    │
                       └───────────────────────┘
```

---

## 3. Component Breakdown

### 3.1 Request Router
Entry point for all incoming `/v1/chat/completions` calls. Responsible for:
- Parsing the incoming request
- Attaching caller identity (`X-Agent-ID` header or inferred)
- Checking `X-Cache-Bypass` header
- Dispatching to the cache pipeline

### 3.2 Exact Cache (Redis)
Fast path. A SHA-256 hash of the **canonicalized prompt** is looked up in Redis.

- **Hit:** Return stored response immediately. No DeepSeek call.
- **Miss:** Continue to Canonicalizer.
- TTL: configurable per content type (1 hour for code context, 24 hours for stable Q&A).

### 3.3 Prompt Canonicalizer
The core value component. Reconstructs the prompt in a **stable, ordered format** to maximize DeepSeek prefix cache hits across agents.

Canonical prompt structure (always in this order):
```
[SYSTEM BLOCK]           ← stable, agent-agnostic instructions
[PROJECT CONTEXT]        ← workspace metadata (lang, framework, repo root)
[REPOSITORY CONTEXT]     ← file/function/class summaries from repo index
[MEMORY PACK]            ← project memory summaries (architecture, roadmap, etc.)
[FILE CONTEXT]           ← sorted file blocks (alphabetical by path)
[CONVERSATION]           ← prior turns (truncated to fit)
[CURRENT QUERY]          ← the actual question
```

Normalization rules:
- Timestamps → relative format (`2 hours ago`)
- Absolute paths → `<workspace>/path/to/file`
- UUIDs → `<uuid>`
- Duplicate whitespace collapsed
- Agent-specific noise stripped

### 3.4 Repository Intelligence (Phase 4)
Stops agents from loading entire repositories. Instead, the gateway provides indexed summaries at the file, function, and class level.

**Components:**
- **repo-indexer:** Walks the workspace, parses code files with tree-sitter, extracts AST-level summaries
- **tree-sitter parsers:** One per supported language (Python, TypeScript/JavaScript, Go, Rust, etc.)
- **Qdrant vector store:** File summaries, function signatures, class outlines stored as vectors for semantic search
- **Dependency graph:** Tracks import/require relationships between files

**Endpoints:**
- `POST /index` — Index or re-index the current repository
- `POST /search` — Semantic search over the repository index

**Integration with request flow:** When the canonicalizer builds the prompt, it queries the repository index for relevant file/function/class summaries related to the user's query, injecting them into the prompt context instead of full file contents.

### 3.5 Project Memory Pack (Phase 5)
Prevents context explosion by replacing weeks of conversation history with a compressed, nightly-generated project state.

**Generated files:**
```
memory/
├── architecture.md       ← project architecture overview
├── roadmap.md            ← current roadmap and milestones
├── current_state.md      ← current project state
├── coding_rules.md       ← project coding conventions
├── active_tasks.md       ← what's being worked on now
└── repo_summary.md       ← high-level repo summary
```

**Regeneration:** Nightly, or on-demand via `POST /memory/regenerate`.

**Integration:** The memory pack is injected into the stable prefix of every request, replacing the need to replay long conversation histories. Agents receive the memory pack instead of weeks of session context.

**Expected savings:** Huge. Solves the session-collapse problem across Hermes, OpenCode, Qoder, and VSCode agents.

### 3.6 Semantic Cache (Phase 7)
Moved to Phase 7. Deliberately deferred because Repository Intelligence and Memory Pack provide higher ROI earlier.

**What it stores:** Embedding of the user query → response  
**Similarity threshold:** `>= 0.98` (deliberately strict — false positives are worse than misses)  
**Disabled by default:** Must be explicitly enabled via config or `X-Enable-Semantic-Cache: true` header  
**Embedding model:** `sentence-transformers` (local, CPU-adequate)

**Flow when enabled:**
```
Exact Cache → Canonicalizer → Repository Intelligence → Memory Pack → Semantic Cache → DeepSeek
                                                                  ↓ hit
                                                            return cached response
```

### 3.7 Artifact Registry (Phase 8)
Allows different agent sessions (Worker 2, Worker 17) to reuse generated artifacts without another DeepSeek call.

**Stored artifacts:**
- Generated APIs
- Generated code
- Generated schemas
- Generated prompts
- Generated workflows

**Endpoints:**
- `POST /artifact/store` — Store a named artifact
- `POST /artifact/search` — Search for existing artifacts by content or metadata

### 3.8 Context Store
A shared registry where agents (or the gateway auto-populates) store:
- File contents (indexed by path)
- Project metadata (language, framework, active branch)
- Memory pack references

Stored in Redis (hot, small) and Qdrant (vector-indexed, for retrieval).

### 3.9 Metrics Dashboard (Phase 6)
Provides measurable ROI for all caching and optimization strategies.

**Endpoints:**
- `GET /metrics` — All metrics in one response
- `GET /metrics/cost` — Token and cost breakdown
- `GET /metrics/cache` — Per-tier cache hit/miss stats

**Display:**
```json
{
  "tokens_saved": 1234567,
  "estimated_cost_saved": 42.31,
  "cache_hit_rate": 67.2,
  "by_agent": { ... },
  "by_tier": { "exact": 45.0, "semantic": 22.2, "prefix_cache": 15.0 }
}
```

### 3.10 Stats Tracker
Logs per-request:
- Cache tier hit (exact / semantic / miss)
- Tokens sent vs. tokens that would have been sent without gateway
- Estimated cost saved
- Repository index usage
- Memory pack contribution

---

## 4. Sequence Diagrams

### 4.1 Exact Cache Hit
```
Agent          Gateway         Redis
  │                │              │
  │──POST /v1/──►  │              │
  │  chat/compl.   │              │
  │                │─hash lookup─►│
  │                │◄── HIT ──────│
  │◄── response ───│              │
  │   (< 5ms)      │              │
```

### 4.2 Repository Index + DeepSeek Miss
```
Agent     Gateway       Redis      Qdrant(Repo)    DeepSeek
  │           │            │            │              │
  │─POST──►   │            │            │              │
  │           │─hash──────►│            │              │
  │           │◄─MISS──────│            │              │
  │           │─CANONICALIZE            │              │
  │           │─query repo context─────►│              │
  │           │◄─summaries──────────────│              │
  │           │─inject memory pack      │              │
  │           │─POST /v1/chat───────────┼────────────►│
  │           │◄──── response ──────────┼─────────────│
  │           │─store hash────────►     │              │
  │◄─response─│                        │              │
```

### 4.3 Semantic Cache Hit (Phase 7, when enabled)
```
Agent     Gateway      Redis    Qdrant(Repo)  Qdrant(Sem)   DeepSeek
  │           │           │          │             │            │
  │─POST──►   │           │          │             │            │
  │           │─hash─────►│          │             │            │
  │           │◄─MISS─────│          │             │            │
  │           │─canonicalize          │             │            │
  │           │─repo enrich─────────►│             │            │
  │           │─memory pack           │             │            │
  │           │─embed query──────────────────────►│            │
  │           │◄── similar response ──────────────│            │
  │◄─response─│   (similarity ≥ 0.98)             │            │
```

---

## 5. Data Flow Summary

```
Incoming Request
    │
    ▼
[Parse + Identify Agent]
    │
    ▼
[Hash canonical prompt] ──► Redis lookup ──► HIT → return cached response
    │ MISS
    ▼
[Canonicalize prompt]
  → normalize timestamps, paths, UUIDs
  → sort file blocks alphabetically
  → prepend stable system prefix
  → inject project context
    │
    ▼
[Repository Intelligence]
  → query Qdrant repo index for relevant summaries
  → inject file/function/class context
  → inject dependency graph info
    │
    ▼
[Project Memory Pack]
  → load memory pack for current project
  → inject architecture, roadmap, current state, rules, tasks, repo summary
    │
    ▼
[Semantic Cache (if enabled)]
  → embed query → Qdrant lookup → HIT (≥0.98) → return + log
    │ MISS
    ▼
[POST to DeepSeek]
    │
    ▼
[Store response]
  → Redis (exact hash)
  → Qdrant semantic (query embedding + response)
    │
    ▼
[Log token savings + metrics]
[Return to Agent]
```

---

## 6. Deployment Model (Local)

All components run as Docker containers on the local machine via Docker Compose. No cloud dependencies.

```
memory-gateway/
├── docker-compose.yml       ← gateway + redis + qdrant
├── gateway/                 ← FastAPI app
│   ├── main.py
│   ├── router.py
│   ├── cache/
│   │   ├── exact.py         ← Redis client
│   │   └── semantic.py      ← Qdrant semantic cache (Phase 7)
│   ├── canonicalizer.py
│   ├── repository/
│   │   ├── indexer.py       ← repo-indexer (tree-sitter)
│   │   ├── parser.py        ← language-specific parsers
│   │   ├── search.py        ← /search endpoint
│   │   └── graph.py         ← dependency graph
│   ├── memory_pack/
│   │   ├── generator.py     ← nightly memory pack generator
│   │   └── templates/       ← markdown templates
│   ├── metrics.py           ← metrics dashboard
│   ├── artifact_registry.py ← artifact store/search
│   ├── context_store.py
│   └── stats.py
├── .env                     ← DEEPSEEK_API_KEY, thresholds
└── docs/
```

Ports:
- `8765` → Memory Gateway (exposed to agents)
- `6379` → Redis (internal only)
- `6333` → Qdrant (internal + optional dashboard)

---

## 7. Phase Mapping

| Phase | Component | Status |
|---|---|---|
| Phase 1 | OpenAI-Compatible Proxy (FastAPI, Docker, health, logging) | Foundation |
| Phase 2 | Redis Exact Cache (SHA-256, TTL, hit/miss) | Active |
| Phase 3 | Canonical Prompt Builder (normalization, stable prefix) | Active |
| Phase 4 | Repository Intelligence (tree-sitter, Qdrant, /search, /index) | Active |
| Phase 5 | Project Memory Pack (memory/ directory, nightly regen) | Active |
| Phase 6 | Metrics Dashboard (ROI measurement) | Active |
| Phase 7 | Semantic Cache (sentence-transformers, disabled by default) | Deferred |
| Phase 8 | Artifact Registry (cross-worker reuse) | Future |

---

## 8. Expected Savings by Phase

| Phase | Component | Expected Token Savings |
|---|---|---|
| Phase 2 | Exact Cache | 10–25% |
| Phase 3 | Canonical Prompt Builder | additional 10–20% |
| Phase 4 | Repository Intelligence | 20–40% |
| Phase 5 | Project Memory Pack | Huge (context collapse prevention) |
| Phase 7 | Semantic Cache | additional 5–15% |
| Phase 8 | Artifact Registry | additional variable |

Savings are cumulative and compound as more phases are implemented.
