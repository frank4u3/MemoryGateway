# Prioritized Fix List — Memory Gateway

**Date:** 2026-06-08
**Version:** 0.5.0

Items are **priority-ordered** within each tier. Estimated effort in person-hours.

---

## Tier 0 — Critical (Fix Immediately)

### F-001: Replace `redis.keys()` with `SCAN`
- **ID:** A-2, R-002
- **Files:** `gateway/cache/exact.py:64,76`, `gateway/stats.py:211`
- **Description:** `ExactCache.delete()`, `ExactCache.size()`, and `StatsTracker.reset()` use `KEYS` which blocks Redis. Replace with `SCAN` for iteration and use `DBSIZE` or a dedicated counter for `size()`.
- **Effort:** 2h
- **Risk:** Production Redis blocking on cache growth.

### F-002: Add `.dockerignore` and pin base images
- **ID:** D-5, D-6
- **Files:** (new) `.dockerignore`, `Dockerfile`
- **Description:** Create `.dockerignore` excluding `__pycache__/`, `.git/`, `tests/`, `venv/`, `.env`, `*.pyc`. Pin `python:3.12-slim` to a SHA256 digest.
- **Effort:** 0.5h
- **Risk:** Non-reproducible builds, bloated images.

### F-003: Add Docker resource limits and healthchecks
- **ID:** D-1, D-2, D-3, D-4
- **Files:** `docker-compose.yml`
- **Description:**
  - Add `deploy.resources.limits` to all services (e.g., gateway: 512MB RAM, 0.5 CPU).
  - Add `healthcheck` to gateway (`curl -f http://localhost:8765/v1/health`).
  - Add `healthcheck` to Qdrant (`curl -f http://localhost:6333/health`).
  - Set `stop_grace_period: 30s` for graceful shutdown.
- **Effort:** 1h
- **Risk:** OOM kills, silent startup failures.

---



## Tier 1 — High (Fix This Week)

### F-004: Add upstream retry with exponential backoff
- **ID:** A-1, R-003
- **Files:** `gateway/proxy.py`
- **Description:** Wrap `chat_completion()` with retry logic: 3 attempts, exponential backoff (1s, 2s, 4s) with jitter. Only retry on 5xx and network errors, not 4xx. Log each retry attempt.
- **Effort:** 3h
- **Risk:** Sustained upstream failures cause complete gateway outage.

### F-005: Add rate limiting
- **ID:** S-2, R-001
- **Files:** `gateway/router.py` (or new `gateway/middleware/ratelimit.py`)
- **Description:** Add per-agent token bucket rate limiter. Default: 60 requests/minute per agent, burst 10. Use Redis as the backing store (`redis-rate-limiter` pattern with `INCR` + `EXPIRE`). Return 429 with `Retry-After` header when exceeded.
- **Effort:** 4h
- **Risk:** Unbounded API spending.

### F-006: Add request size limits
- **ID:** S-3, R-001
- **Files:** `gateway/router.py`, `gateway/config.py`
- **Description:** Add `max_message_count` (default 200), `max_message_length` (default 128_000 chars), and `Settings.max_request_body_size` (default 10MB). Validate early in `chat_completions` before processing. Configure FastAPI's `max_request_size`.
- **Effort:** 2h
- **Risk:** OOM from oversized requests.

### F-007: Add graceful degradation for Redis/Qdrant
- **ID:** A-2, A-3, R-004
- **Files:** `gateway/main.py`
- **Description:** Wrap Redis and Qdrant client creation with retry (3 attempts, 2s delay). On final failure, log a critical warning and continue in degraded mode (caching disabled, indexing unavailable). Set `app.state.cache = None` and guard all access.
- **Effort:** 4h
- **Risk:** Gateway crash on dependency failure.

### F-008: Add request trace ID
- **ID:** L-1, R-009
- **Files:** `gateway/router.py`, `gateway/main.py`, `gateway/logger.py`
- **Description:** Generate a `X-Request-ID` (UUID hex) in middleware or at the start of each request handler. Include it in every log `extra` dict. Pass as `X-Request-Id` header to upstream. Include in error responses for client debugging.
- **Effort:** 2h
- **Risk:** Inability to correlate logs across components.

### F-009: Sanitize API keys from log output
- **ID:** S-4, R-006
- **Files:** `gateway/proxy.py`
- **Description:** Before logging upstream error bodies, sanitize with a regex that replaces `sk-[a-zA-Z0-9]{32,}` and `Bearer [a-zA-Z0-9._-]+` with `[REDACTED]`.
- **Effort:** 1h
- **Risk:** Credential leakage in log aggregation systems.

### F-010: Handle streaming errors gracefully
- **ID:** R-8, E-3, R-007
- **Files:** `gateway/router.py`
- **Description:** In `_handle_streaming`, if the upstream returns an error, yield an SSE-formatted error event (`data: {"error": {...}}\n\n`) before closing, and set a flag to return appropriate HTTP status if no data was sent. Don't return 200 on complete failure.
- **Effort:** 3h
- **Risk:** Silent data loss on streaming errors.

---



## Tier 2 — Medium (Fix This Sprint)

### F-011: Add process-level resource metrics
- **ID:** M-3
- **Files:** `gateway/metrics/prometheus.py`
- **Description:** Add Gauges for: `process_rss_bytes`, `process_cpu_seconds_total`, `process_open_fds`, `python_gc_objects_collected_total`. Update on each `/v1/metrics` scrape or use a background task every 15s.
- **Effort:** 2h
- **Risk:** No visibility into resource exhaustion.

### F-012: Update `cache_hit_rate` and `active_requests` gauges
- **ID:** M-4, M-5
- **Files:** `gateway/router.py`, `gateway/metrics/prometheus.py`
- **Description:** Call `active_requests.inc()` on request start, `active_requests.dec()` on completion (use `try/finally`). Update `cache_hit_rate.set()` after each request with the current rate from stats.
- **Effort:** 1h
- **Risk:** Defined but unused metrics mislead operators.

### F-013: Add Qdrant health/latency metrics
- **ID:** M-2
- **Files:** `gateway/indexer/qdrant_store.py`, `gateway/semantic_cache/store.py`
- **Description:** Add histograms for Qdrant query latency, upsert latency, and error counters per operation type. Expose Qdrant collection size as a gauge.
- **Effort:** 3h
- **Risk:** Blind to Qdrant performance degradation.

### F-014: Log warning on in-memory Qdrant mode
- **ID:** R-008
- **Files:** `gateway/main.py`
- **Description:** At startup, if `settings.qdrant_in_memory` is `True`, log a WARNING: "Qdrant in-memory mode — all vector data lost on restart. Set QDRANT_URL for persistence."
- **Effort:** 0.5h
- **Risk:** Silent data loss on restart.

### F-015: Pin dependencies with upper bounds
- **ID:** D-7
- **Files:** `requirements.txt`
- **Description:** Pin all dependencies to known-good versions with upper bounds: `fastapi>=0.115.0,<0.116.0`, `httpx>=0.28.0,<0.29.0`, etc. Test upgrade paths via CI.
- **Effort:** 1h
- **Risk:** Breaking changes from unpinned deps.

### F-016: Add retry startup for Redis connection
- **ID:** A-2
- **Files:** `gateway/main.py`
- **Description:** Wrap `aioredis.from_url()` in a retry loop: 3 attempts, 2s delay. Log each attempt. If all fail, start degraded (cache disabled) rather than crashing.
- **Effort:** 2h
- **Risk:** Gateway crash on transient Redis unavailability.

### F-017: Add `httpx` connection pool tuning
- **ID:** C-1
- **Files:** `gateway/main.py`
- **Description:** Ensure `max_connections` from settings is wired into `httpx.Limits`. Set `max_keepalive_connections` to a reasonable value (e.g., 20). Add connection timeout separate from total timeout.
- **Effort:** 0.5h
- **Risk:** Connection exhaustion under load.

### F-018: Add uvicorn workers config
- **ID:** D-8
- **Files:** `Dockerfile`, `docker-compose.yml`
- **Description:** Change `CMD` to use `--workers 2 --preload`. Document that workers > number of CPU cores causes contention. Note: `--preload` requires that modules are safe for forking (currently they are — connections are created in lifespan).
- **Effort:** 0.5h
- **Risk:** Single-worker throughput bottleneck.

### F-019: Add timeout to lifespan shutdown
- **ID:** R-5
- **Files:** `gateway/main.py`
- **Description:** Wrap `aclose()` calls in `asyncio.wait_for(..., timeout=10.0)` to prevent hanging on shutdown.
- **Effort:** 0.5h
- **Risk:** Gateway process hangs on shutdown.

### F-020: Make `DASHBOARD_HTML` a static file
- **ID:** C-5
- **Files:** `gateway/metrics/dashboard.py` → `gateway/static/dashboard.html`
- **Description:** Move the HTML/CSS/JS to a static file, serve with `Mount` or `StaticFiles`. Add Chart.js as a local vendored file or load from CDN with a fallback.
- **Effort:** 2h
- **Risk:** Maintenance burden of HTML-in-Python.

---



## Tier 3 — Low (Fix When Convenient)

### F-021: Restrict CORS to localhost or remove credentials
- **ID:** S-1
- **Files:** `gateway/main.py`
- **Description:** Change CORS to `allow_origins=["http://localhost:8765", "http://127.0.0.1:8765"]` or remove `allow_credentials=True` when using `["*"]`.
- **Effort:** 0.5h

### F-022: Add async file I/O to MemoryStore
- **ID:** A-5, R-011
- **Files:** `gateway/memory/store.py`
- **Description:** Replace `path.write_text()`, `open()`, `json.dump()` with `aiofiles` equivalents. Or wrap in `run_in_executor`.
- **Effort:** 2h

### F-023: Add pagination to context/artifact/memory search
- **ID:** R-9, R-10
- **Files:** `gateway/context/store.py`, `gateway/artifact/store.py`, `gateway/memory_layer/store.py`
- **Description:** Replace `SMEMBERS` + full iteration with cursor-based pagination or Redis SCAN. Add `offset`/`limit` parameters to search endpoints.
- **Effort:** 4h

### F-024: Log tree-sitter parser errors instead of swallowing
- **ID:** E-2
- **Files:** `gateway/indexer/parser.py`
- **Description:** Replace `except Exception: pass` with `except Exception as exc: logger.warning("parser_error", extra={"file": file_path, "error": str(exc)})`.
- **Effort:** 1h

### F-025: Reset Prometheus registry in test fixtures
- **ID:** M-1, R-010
- **Files:** `tests/test_metrics.py`
- **Description:** Call `REGISTRY.reset()` in fixture setup to prevent cross-test metric pollution.
- **Effort:** 0.5h

### F-026: Report MemoryStore Redis save failures
- **ID:** R-7
- **Files:** `gateway/memory/store.py`
- **Description:** Change bare `except Exception: pass` to log a warning when Redis save fails.
- **Effort:** 0.5h

### F-027: Normalize error response format
- **ID:** E-5
- **Files:** `gateway/router.py`, `gateway/main.py`
- **Description:** Ensure all errors use the same structure: `{"error": {"message": ..., "type": ..., "code": ...}}`. Remove mixed `detail` usage.
- **Effort:** 2h

### F-028: Split router.py into route modules
- **ID:** A-6
- **Files:** `gateway/router.py` → `gateway/routes/chat.py`, `routes/context.py`, `routes/artifact.py`, `routes/memory.py`, `routes/metrics.py`
- **Description:** Refactor the single large router into domain-specific route modules with separate `APIRouter` instances. Include each in `main.py`.
- **Effort:** 3h

### F-029: Default Qdrant to persistent mode with warning
- **ID:** A-4
- **Files:** `gateway/config.py`
- **Description:** Change `qdrant_in_memory` default to `False` and require explicit opt-in for in-memory. Or keep `True` but log a prominent WARNING at every startup.
- **Effort:** 0.5h

### F-030: Add per-operation Qdrant timeout
- **ID:** R-6
- **Files:** `gateway/indexer/qdrant_store.py`, `gateway/semantic_cache/store.py`
- **Description:** Add a configurable timeout (default 10s) for all Qdrant operations to prevent hangs.
- **Effort:** 1h

---

## Summary by Priority

| Tier | Count | Total Effort |
|------|-------|-------------|
| Tier 0 — Critical | 3 | 3.5h |
| Tier 1 — High | 7 | 19h |
| Tier 2 — Medium | 10 | 13h |
| Tier 3 — Low | 10 | 14.5h |
| **Total** | **30** | **50h** |

## Quick Wins (< 2h, High Impact)

| Fix | Effort | Impact |
|-----|--------|--------|
| F-002: `.dockerignore` + pin base image | 0.5h | Build reproducibility |
| F-009: Sanitize API keys from logs | 1h | Security |
| F-014: Warn on in-memory Qdrant | 0.5h | Prevent data loss confusion |
| F-017: Tune httpx connection pool | 0.5h | Resource efficiency |
| F-019: Shutdown timeout | 0.5h | Reliability |
| F-025: Reset Prometheus in tests | 0.5h | Test reliability |
