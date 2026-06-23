# Memory Gateway — Handoff Document

## Current State (2026-06-21)

**Gateway:** Running on `http://localhost:8765`
**Mode:** `BASELINE_MODE=true` (passthrough, collecting usage data)
**Redis:** Running as Windows service (Automatic start)
**DeepSeek:** Connected via `.env` DEEPSEEK_BASE_URL

## Recent Commits

```
d3cb677 recover: canonicalizer.py — CanonicalRequest dataclass and canonicalize_request()
6a8dd81 recover: telemetry/baseline.py — cache_attribution in compare_to_finalized
b071de7 recover: memory_layer/store.py — aging ranking, record_access, final_score
5788eaa recover: memory_layer/schemas.py — aging fields on MemoryRecord and MemorySearchResult
8f3369d fix: restore DeepSeek API key handling and router streaming fixes
```

All 5 commits are verified (health check passes, 17/17 tests pass).

## What Was Rebuilt (from session data loss)

| File | What was added |
|------|---------------|
| `gateway/canonicalizer.py` | `CanonicalRequest` dataclass + `canonicalize_request()` — unifies cache key, prefix, upstream messages into single object |
| `gateway/telemetry/baseline.py` | `cache_attribution` block in `compare_to_finalized()` — separates cache savings from natural variation; expanded `_aggregate()` and `_daily_breakdown()` with cost_spent/cost_saved/provider_calls |
| `gateway/memory_layer/schemas.py` | Aging fields: `decay_score`, `importance_score`, `access_count`, `last_accessed` on `MemoryRecord` and `MemorySearchResult` |
| `gateway/memory_layer/store.py` | `_compute_decay()`, `_final_score()` ranking, `record_access()` — aging-aware search; fields populated in `share()`/`create_inline()` |
| `gateway/router.py` | X-Agent-ID optional (returns "unknown"), streaming SSE error events, baseline mode reasoning_content merge, model normalization |

## What Was NOT Rebuilt (awaiting decision)

| File | Lost edit | Status |
|------|-----------|--------|
| `gateway/cache/exact.py` | EXACT_SIZE_KEY rolling counter for size() | Dropped — SCAN-based size() works |
| `gateway/stats.py` | record_upstream_call(), provider_calls | Dropped — needs synchronized router.py change |
| `gateway/main.py` | PatternStore/DecisionStore initialization | Dropped — stores aren't wired to endpoints |
| `gateway/context/store.py` | Qdrant consolidation to cbm_memory | Dropped — works with separate collections |
| `gateway/artifact/store.py` | Qdrant consolidation | Dropped |
| `gateway/learning/store.py` | Qdrant consolidation | Dropped |
| `gateway/indexer/qdrant_store.py` | memory_type parameter | Dropped |
| `gateway/prefix_cache/schemas.py` | effective_prefix_tokens fields | Dropped — module not wired |
| `gateway/prefix_cache/store.py` | HASH-based storage, rolling counters | Dropped — module not wired |
| `gateway/router.py` | CanonicalRequest usage in _handle_non_streaming | Pending — CanonicalRequest exists but router still uses old canonicalize_prompt |
| `gateway/router.py` | patterns/decisions endpoints | Pending — stores exist but no endpoints |
| `gateway/router.py` | tag_filter in memory search | Pending — needs schema+store+router sync |
| `gateway/memory_layer/store.py` | Redis secondary indexes (tag/project sets) | Pending — known O(n) scan on SMEMBERS KEY_IDS |

## New Modules (on disk, not wired)

| Module | Files | Status |
|--------|-------|--------|
| `gateway/debug/` | cache_diagnostics.py | Wired — GET /v1/debug/cache-report |
| `gateway/patterns/` | schemas.py, store.py, extractor.py | NOT wired — no endpoints, no main.py init |
| `gateway/decisions/` | schemas.py, store.py | NOT wired — no endpoints, no main.py init |
| `gateway/prefix_cache/` | schemas.py, store.py, builder.py, __init__.py | NOT wired — exists on disk, not imported by router.py or main.py |

## Pending Fixes (known bugs)

- `memory_layer/store.py` search uses O(n) `SMEMBERS KEY_IDS` — tagged in commit `b071de7`
- `router.py` calls `record_miss` unconditionally after every upstream call (even baseline mode)
- `router.py`'s `_handle_non_streaming` still calls `canonicalize_prompt` directly instead of `canonicalize_request` — `CanonicalRequest` exists but isn't used on the hot path
- `build_upstream_messages` imported in router.py but never called

## Quick Start

```powershell
# Kill anything on port 8765
$p = Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue
if ($p.OwningProcess) { taskkill /PID $p.OwningProcess /F }

# Start gateway
python -m uvicorn gateway.main:app --port 8765 --host 0.0.0.0

# Verify
curl http://localhost:8765/v1/health
```

## Auto-Restart

- **Scheduled Task:** `MemoryGateway` — runs at system startup as SYSTEM, executes `_start.vbs`
- **VBScript:** loops and restarts gateway every 5 seconds on crash
- **Redis:** Windows service, Automatic start

## Key API Endpoints

- `GET /v1/health`
- `POST /v1/chat/completions` — no X-Agent-ID required
- `GET /v1/telemetry/baseline/compare/{id}` — now includes `cache_attribution` block
- `GET /v1/debug/cache-report` — cache diagnostics
- `POST /v1/memory/context` — search with aging-aware ranking

## Configuration (.env)

```
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
GATEWAY_PORT=8765
REDIS_URL=redis://localhost:6379/0
BASELINE_MODE=true
CACHE_ENABLED=true
SEMANTIC_CACHE_ENABLED=false
AUTHORIZED_AGENTS=hermes,opencode,qoder,vscode
```

Note: `DEEPSEEK_API_KEY` was removed from `.env` — the proxy now passes the client's Authorization header directly to DeepSeek.

## Tests

```
python -m pytest tests/test_cache.py -v        # 17 tests, cached
python -m pytest tests/ -v                     # full suite
```

## Audit Files

- `AUDIT.md` — full change inventory, known-broken behavior, hot-path impact
- `STASH_REPORT.md` — stash/reflog/cache investigation (edits not recoverable)
- `RECOVERY_PLAN.md` — per-file triage: Rebuild/Drop/Ask Frank
- `regression-core.diff` — 135 lines, applied in commit 8f3369d
- `regression-extras.diff` — 1691 lines, NOT applied (new files: debug, patterns, decisions, prefix_cache, HANDOFF)

## In Progress / Next Action

**Session ended**: 2026-06-21
**Completed**: Pushed 7 commits including all 5 recovery commits to
GitHub frank4u3/MemoryGateway master. Code is now off-site safe.

**GitHub status**: origin/master is now current with local master.

**Untracked files NOT pushed** (scope creep, kept local only):
gateway/debug/, gateway/patterns/, gateway/decisions/,
gateway/prefix_cache/, _start.vbs, AUDIT.md, RECOVERY_PLAN.md,
STASH_REPORT.md, regression diffs

**Next action**: Wait for Frank and claude.ai session to design
proper two-repo VPS deployment setup (memory-gateway and agent-platform
as separate git clones on VPS host, volume-mounted into containers).
Do not make further code changes until that setup is confirmed.

**Open questions for Frank**:
- VPS /opt/ deployment structure: when does the VPS get proper
  git clones set up from GitHub?
- Should the scope-creep untracked dirs be deleted from local disk,
  or kept for reference?

**Known issues**: VPS container still has isolated git with no remote.
That will be resolved when proper VPS deployment is set up.
