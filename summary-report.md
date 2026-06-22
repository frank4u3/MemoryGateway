# Memory Gateway — Project Summary Report

**Date:** 2026-06-21
**Version:** 0.5.0
**Repo:** `H:\memory-gateway`

---

## 1. Executive Summary

The Memory Gateway is a **cost-optimization proxy** that sits between AI coding agents (Hermes, OpenCode, Qoder, VSCode) and the DeepSeek API. Its primary value proposition: **reduce token consumption and API costs** through exact-match caching, prompt canonicalization, and prefix-cache optimization.

Currently running in **baseline mode** (passthrough, no caching), collecting usage data to establish a cost baseline. Once the baseline is frozen, caching is enabled and the gateway will serve cached responses instead of calling DeepSeek for identical/near-identical prompts — directly reducing token spend.

**Status:** The gateway is **alive and proxying requests**, but several load-bearing features are partially built and not yet wired into the live request path. The core pass-through proxy works. Caching and optimization features exist in the codebase but are not active.

---

## 2. Current Running State

| Component | Status |
|-----------|--------|
| Gateway process | Running on `localhost:8765` |
| Mode | `BASELINE_MODE=true` (passthrough, collecting data) |
| Redis | Running as Windows service (Automatic) |
| DeepSeek API | Connected via `.env` `DEEPSEEK_BASE_URL` |
| Health check | `GET /v1/health` returns `{"status":"ok"}` |
| Tests | 17/17 pass (`tests/test_cache.py`) |
| Auto-restart | Scheduled task `MemoryGateway` (SYSTEM, BootTrigger) + `_start.vbs` watchdog |

---

## 3. Architecture

```
gateway/
├── main.py              # FastAPI app factory + lifespan (all store init)
├── router.py            # All API endpoints (~1738 lines)
├── proxy.py             # httpx async proxy to DeepSeek
├── models.py            # Pydantic request/response models (ChatCompletionRequest, etc.)
├── config.py            # Settings from .env (pydantic-settings)
├── canonicalizer.py     # Prompt normalization + CanonicalRequest (unified state model)
├── logger.py            # Structured JSON logging
├── stats.py             # Redis-backed hit/miss/token/cost tracker
│
├── cache/               # Tier 1 — Redis exact-match cache
│   └── exact.py         # ExactCache (Redis SET/GET with SHA256 keys)
│
├── prefix_cache/        # Tier 2 — DeepSeek server-side prefix cache analytics
│   ├── builder.py       # Splits messages into prefix/tail blocks
│   ├── store.py         # Redis-backed prefix hit/miss tracking
│   └── schemas.py       # Pydantic models for prefix cache entries
│
├── semantic_cache/      # Tier 3 — Qdrant semantic similarity cache (disabled)
│   └── store.py         # SemanticCache with cosine similarity threshold
│
├── telemetry/           # Usage tracking and baseline comparison
│   ├── __init__.py      # TelemetryService agent stats
│   ├── baseline.py      # Daily snapshots + freeze + compare (cache_attribution)
│   └── dashboard.py     # HTML dashboard
│
├── memory_layer/        # Shared agent memory (permissioned search)
│   ├── schemas.py       # MemoryRecord, SearchMemoryRequest (aging fields added)
│   └── store.py         # Redis CRUD + search with aging-aware ranking
│
├── context/             # Reusable context blocks
├── artifact/            # Reusable artifacts (code, APIs, schemas)
├── learning/            # Learning layer (bug fixes, arch decisions)
├── memory/              # Project memory pack (generation, versioning, diff)
├── indexer/             # Qdrant code indexer (tree-sitter AST parsing)
├── metrics/             # Prometheus metrics + cost calculator
│
├── patterns/            # ⚠️ NEW — BusinessPattern recognition (NOT wired)
├── decisions/           # ⚠️ NEW — DecisionRecord store (NOT wired)
└── debug/               # ✅ Cache diagnostics endpoint (wired)
```

### Request Flow (non-streaming)

```
Client → router.py /v1/chat/completions
  → _get_agent_id()            # optional, defaults to "unknown"
  → _get_auth_header()          # passes through to DeepSeek
  → canonicalize_prompt()       # normalizes UUIDs, timestamps, paths
  → cache.get(cache_key)        # Redis exact-match lookup (skipped in baseline mode)
  → proxy.chat_completion()     # httpx POST to DeepSeek API
  → baseline_mode merge         # reasoning_content → content for reasoner models
  → return upstream_data
```

---

## 4. What Is Working (Live)

| Feature | Endpoint(s) | Notes |
|---------|------------|-------|
| Chat completions | `POST /v1/chat/completions` | Pass-through proxy, non-streaming + streaming |
| Health check | `GET /v1/health` | Returns component status |
| Models list | `GET /v1/models` | deepseek-v4-pro, deepseek-v4-flash, deepseek-chat, deepseek-reasoner |
| X-Agent-ID optional | all endpoints | Returns "unknown" if missing (no 422) |
| Streaming errors | `POST /v1/chat/completions` (stream) | SSE error events yielded to client |
| Baseline mode merge | `POST /v1/chat/completions` | reasoning_content merged into content |
| Baseline telemetry | `GET /v1/telemetry/baseline*` | Daily snapshots, export, finalize, compare |
| Cache stats | `GET /v1/cache/stats` | hit/miss/token/cost stats |
| Cache diagnostics | `GET /v1/debug/cache-report` | Variance, collision, prefix effectiveness analysis |
| Memory search | `POST /v1/memory/context` | Aging-aware ranking (decay × importance × recency) |
| Memory CRUD | `POST /v1/memory_layer/*` | Share, create, search, permissions |
| Learning layer | `POST /v1/learning/*` | Store, search, update |
| Artifact store | `POST /v1/artifact/*` | Store, search |
| Context store | `POST /v1/context/*` | Register, search, update |
| Memory pack | `GET/POST /v1/memory/*` | Generate, version, diff, rollback |
| Prometheus metrics | `GET /v1/metrics` | Counters, histograms, gauges |
| HTML dashboard | `GET /v1/telemetry/dashboard` | Embedded Chart.js dashboard |

---

## 5. Built But NOT Wired (exists on disk, no effect)

| Item | Files | What's needed to activate |
|------|-------|--------------------------|
| **Exact cache** (Tier 1) | `cache/exact.py` | Set `BASELINE_MODE=false` in `.env` — cache code is present in router.py, just disabled |
| **CanonicalRequest** unified model | `canonicalizer.py` | Router.py needs to call `canonicalize_request()` instead of `canonicalize_prompt()` — the function exists but isn't called |
| **Prefix cache tracking** (Tier 2) | `prefix_cache/` | Router.py needs to import and call `build_prefix()` + `PrefixCacheStore` after each upstream call |
| **Semantic cache** (Tier 3) | `semantic_cache/` | Set `SEMANTIC_CACHE_ENABLED=true` — code is present, just disabled |
| **Pattern store** | `patterns/` | (1) Initialize `PatternStore` in `main.py` lifespan, (2) add endpoints in `router.py` |
| **Decision store** | `decisions/` | (1) Initialize `DecisionStore` in `main.py` lifespan, (2) add endpoints in `router.py` |
| **Memory secondary indexes** | `memory_layer/store.py` | Add `_resolve_candidate_ids()`, tag/project Redis SET indexes — currently uses O(n) SMEMBERS scan |
| **Qdrant consolidation** | context/artifact/learning stores | Merge 3 collections into single `cbm_memory` with `memory_type` filter |
| **Tag filter** | memory_layer schemas/store | Add `tag_filter` to `SearchMemoryRequest`, `search()`, and router.py endpoint |

---

## 6. Known Bugs

| Bug | Location | Severity | Status |
|-----|----------|----------|--------|
| `record_miss` called unconditionally in baseline mode | `router.py` line ~366 | Low — inflates miss stats | Not fixed |
| O(n) SMEMBERS scan on memory search | `memory_layer/store.py` | Medium — degrades at scale | Tagged in commit `b071de7` |
| `build_upstream_messages` dead import | `router.py` line 10 | None — zero effect | Not fixed |
| CanonicalRequest unused on hot path | `router.py` | Low — no effect until router updated | Not fixed |

---

## 7. Roadmap to Production — Minimum Viable Gateway

### Phase A — Enable Tier 1 Cache (1 change) ⭐ HIGHEST ROI

Set `BASELINE_MODE=false` and `CACHE_ENABLED=true` in `.env`. This activates the existing exact-match cache code in `router.py`. Identical prompts will hit Redis instead of DeepSeek.

**Prerequisite:** Finalize the baseline first: `POST /v1/telemetry/baseline/finalize {"baseline_id":"week1"}` so you have a frozen reference point for ROI measurement.

### Phase B — Wire CanonicalRequest (1 file change) ⭐ HIGH ROI

Update `router.py`'s `_handle_non_streaming()` to call `canonicalize_request()` instead of `canonicalize_prompt()`. This unifies the cache key, prefix analysis, and upstream messages into a single CanonicalRequest object, eliminating duplicate canonicalization paths.

### Phase C — Wire Prefix Cache Tracking (1 file change)

Add prefix cache recording to `router.py`'s `_handle_non_streaming()` after each upstream call. Import `build_prefix`, `PrefixCacheStore` from `prefix_cache/`. This tracks DeepSeek's server-side prefix cache savings.

### Phase D — Enable Semantic Cache (1 config change)

Set `SEMANTIC_CACHE_ENABLED=true`. Existing semantic cache code activates, serving Qdrant-based cosine-similarity matches above the threshold.

### Phase E — Patterns + Decisions (3 files)

1. Initialize stores in `main.py` lifespan
2. Add 4 endpoints in `router.py`: `POST /v1/patterns/store`, `POST /v1/patterns/search`, `POST /v1/decisions/store`, `POST /v1/decisions/search`

### Phase F — Memory Layer Scale Fix

Add secondary Redis indexes (`mem_layer:tag:{tag}`, `mem_layer:project:{project}`) and `_resolve_candidate_ids()` to `memory_layer/store.py`. Add `tag_filter` to `SearchMemoryRequest` schema and router endpoint.

### Phase G — Qdrant Consolidation

Merge `code_index`, `artifact_index`, `learning_index` into single `cbm_memory` collection with `memory_type` filter for reduced RAM usage.

---

## 8. Quick Start

```powershell
# Kill anything on port 8765
$p = Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue
if ($p.OwningProcess) { taskkill /PID $p.OwningProcess /F }

# Start gateway
python -m uvicorn gateway.main:app --port 8765 --host 0.0.0.0

# Verify
curl http://localhost:8765/v1/health
curl http://localhost:8765/v1/models
```

---

## 9. Key Files for Reviewers

| File | Purpose |
|------|---------|
| `AUDIT.md` | Full change inventory, hot-path impact, data-loss account |
| `RECOVERY_PLAN.md` | Per-file triage: what was rebuilt vs dropped vs pending |
| `STASH_REPORT.md` | Investigation of stash/reflog/cache for lost edits (none recoverable) |
| `regression-core.diff` | 135-line diff of working-tree fixes (applied) |
| `regression-extras.diff` | 1691-line diff of new files (not applied) |
| `HANDOFF.md` | Current state, commits, endpoints, quick start |
