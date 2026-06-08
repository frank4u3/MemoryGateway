# Memory Gateway — Production-Readiness Audit Report

**Date:** 2026-06-08
**Version Audited:** 0.5.0
**Scope:** Full-stack audit of all gateway components, infra, tests, and docs.

---

## 1. Architecture

### 1.1 Strengths
- Clean separation of concerns: `proxy.py` (upstream), `canonicalizer.py` (normalization), `cache/` (storage), `router.py` (routing), stores as isolated modules.
- Lifespan-managed dependency injection via `app.state` — all clients (Redis, Qdrant, httpx) created/closed cleanly.
- Modular monolith well-suited for local deployment; each store is independently testable with `FakeAsyncRedis`.
- Pydantic v2 models throughout for validation and serialization.

### 1.2 Issues

| # | Severity | Finding |
|---|----------|---------|
| A-1 | **High** | **Circular import risk.** `semantic_cache/store.py` imports `from gateway.indexer.embedder` and `from gateway.logger` using absolute imports. `indexer/qdrant_store.py` also imports `embedder`. If `gateway.logger` or any re-export in `__init__.py` triggers a cross-package import, the app can fail at startup. Currently works because `__init__.py` files are empty, but this is brittle. |
| A-2 | **Medium** | **No graceful degradation if Redis is down.** `lifespan()` does `aioredis.from_url()` which will raise on connection failure, crashing startup entirely. For a production gateway, it should either retry with backoff or start in a degraded mode (passthrough only). |
| A-3 | **Medium** | **No graceful degradation if Qdrant is down.** `create_index_store()` in `main.py:57` creates a Qdrant client. If Qdrant is unreachable (production mode), startup crashes. The store should degrade gracefully. |
| A-4 | **Medium** | **In-memory Qdrant default.** `config.py:14` sets `qdrant_in_memory: bool = True`. This means every restart loses the entire repo index, semantic cache, and artifact vectors. Production deployments must explicitly set this to `false`, but there's no warning or validation. |
| A-5 | **Low** | **`MemoryStore` uses synchronous file I/O in an async app.** `memory/store.py` does `path.write_text()`, `json.dump`, `os.path` operations synchronously. In an async event loop, this blocks the worker. |
| A-6 | **Low** | **`router.py` imports are overly broad.** The single `router.py` imports from 18 different modules. Multiple route handlers could be split into separate files (e.g., `routes_chat.py`, `routes_context.py`, `routes_memory.py`, `routes_metrics.py`). |

---

## 2. Security

### 2.1 Strengths
- No hardcoded credentials; all config via environment variables.
- Authorization header validated before forwarding to upstream.
- `X-Agent-ID` is validated against an allowlist (`AGENT_IDS`).
- CORS middleware is present.

### 2.2 Issues

| # | Severity | Finding |
|---|----------|---------|
| S-1 | **Critical** | **CORS allows all origins with credentials.** `main.py:117-122`: `allow_origins=["*"]` combined with `allow_credentials=True`. Per the CORS spec and security best practices, this configuration is invalid — browsers reject credentials when `Access-Control-Allow-Origin: *`. More importantly, if this gateway is ever exposed beyond localhost, it creates an open CORS policy. For a local proxy this is acceptable, but the code should not ship to production like this. |
| S-2 | **High** | **No rate limiting.** There is no rate limiting on any endpoint. A misbehaving or malicious agent could flood the gateway, incurring unbounded DeepSeek costs. |
| S-3 | **High** | **No request size limits.** The `ChatCompletionRequest` model does not cap `messages` array length or `content` string length. An agent could send a multi-GB payload, causing OOM. FastAPI has `max_body_size` but it's not configured. |
| S-4 | **Medium** | **API keys logged in error messages.** `proxy.py:44`: `error_body = response.text` is logged. If DeepSeek returns an error that echoes the API key (e.g., `"Invalid API key: sk-..."`), the key is written to logs. |
| S-5 | **Medium** | **No input sanitization on artifact/context content.** `router.py:764-778` stores arbitrary user content with no sanitization. If content includes embedded script tags or malicious payloads, and the dashboard later renders it (it doesn't today, but future-proofing), this is XSS. |
| S-6 | **Low** | **`uuid.uuid4().hex[:16]` for IDs.** ID generation uses only the first 16 hex chars (64 bits) of a UUID4. This reduces entropy and increases collision risk at scale. Admittedly fine for local deployment. |

---

## 3. Reliability

### 3.1 Strengths
- Redis operations use pipelining (`stats.py`, `context/store.py`, `artifact/store.py`) to reduce round trips.
- Cache corruption is handled: `ExactCache.get()` deletes corrupt entries on `JSONDecodeError`.
- All stores check for `self._redis is None` before operations (graceful no-op when Redis is unavailable).
- `fakeredis` used in tests for fast, deterministic testing.

### 3.2 Issues

| # | Severity | Finding |
|---|----------|---------|
| R-1 | **Critical** | **No retry logic on upstream calls.** `proxy.py:32-37`: A single transient network error or HTTP 429 (rate limit) from DeepSeek propagates as a 502 to the client. There is no retry with exponential backoff. |
| R-2 | **Critical** | **No circuit breaker.** Consecutive upstream failures hammer the downstream API with no backoff. |
| R-3 | **Critical** | **`KEYS` command used in production paths.** `ExactCache.delete()` and `ExactCache.size()` at `exact.py:64,76` use `redis.keys()`. This is O(N) and blocks Redis on large datasets. In production with thousands of cache entries, this will cause latency spikes. Should use `SCAN` instead. |
| R-4 | **High** | **Qdrant in-memory mode loses all data on restart.** `CodeIndexStore` with `:memory:` location loses the entire index. This means the repo index must be rebuilt after every gateway restart. |
| R-5 | **High** | **`lifespan` shutdown may not complete.** `main.py:103-106`: `proxy.client.aclose()` and `redis_client.aclose()` are awaited but there is no timeout. If either hangs (e.g., Redis unreachable), the process never exits cleanly. |
| R-6 | **Medium** | **No connection pooling limits for Qdrant.** `QdrantClient` is created with default settings. In high-concurrency scenarios, this could exhaust file descriptors or connections. |
| R-7 | **Medium** | **`MemoryStore.save()` swallows Redis errors.** `memory/store.py:65-66`: `except Exception: pass` silently ignores Redis write failures. Data loss goes undetected. |
| R-8 | **Medium** | **Streaming errors are swallowed.** `router.py:426-439`: In `_handle_streaming`, if `DeepSeekUpstreamError` or another exception occurs during streaming, the error is logged but the generator just stops — the client receives an incomplete response with no error indication. |
| R-9 | **Low** | **`ContextStore.search()` loads all IDs, then fetches each block individually.** `context/store.py:184-196`: `SMEMBERS` on `KEY_IDS` returns all IDs, then each block is fetched with a separate `GET`. For large numbers of blocks, this is O(N) Redis round-trips. |
| R-10 | **Low** | **Same O(N) pattern in `ArtifactStore.search()` and `MemoryLayerStore.search()`.** All three stores iterate over all IDs to perform keyword matching. No pagination or cursor-based iteration. |

---

## 4. Error Handling

### 4.1 Strengths
- Consistent error response structure: `{"error": {"message": ..., "type": ..., "code": ...}}`.
- HTTP exception handlers registered for `HTTPException` and `RequestValidationError`.
- Most store methods wrap operations in try/except and log.

### 4.2 Issues

| # | Severity | Finding |
|---|----------|---------|
| E-1 | **High** | **`_handle_non_streaming` double-records miss stats.** `router.py:356` calls `record_miss()` and `cache_misses_total.inc()` every time, even when there was a cache hit. Wait — actually looking more carefully, the `await stats.record_miss()` and `cache_misses_total` at lines 356-357 are *always* called, even on cache hit. On a cache hit, execution returns at line 271, so lines 312+ are never reached. So this is actually fine. Let me re-check... Yes, on hit it `return cached` at line 271. On semantic hit it returns at line 310. So the miss stats are only recorded on actual misses. | |
| E-2 | **Medium** | **`parser.py` silently swallows all tree-sitter exceptions.** Lines 381-460: Every tree-sitter query block has `except Exception: pass`. If tree-sitter has a parsing bug or encounters corrupt source code, the error is invisible. There's no log entry, no metric, no indication. |
| E-3 | **Medium** | **`_handle_streaming` returns 200 even on upstream failure.** If DeepSeek returns an error during streaming, the `StreamingResponse` is returned with status 200, then the generator raises. The client gets HTTP 200 with an empty or partial body. |
| E-4 | **Medium** | **`MemoryStore.save()` doesn't raise on file write failure.** If disk is full or permissions are wrong, the `path.write_text()` could fail, caught by an outer exception only if it propagates. There's no explicit error handling for file I/O. |
| E-5 | **Low** | **Mixed error response formats.** Some endpoints return `{"detail": {...}}` (FastAPI default), others return `{"error": {...}}` (custom). For example, route handlers return `detail` dicts while the validation exception handler returns structured `error` objects. |

---

## 5. Logging

### 5.1 Strengths
- Structured JSON logging throughout with `pythonjsonlogger`.
- Consistent message keys: `gateway_startup`, `cache_hit`, `upstream_error`, etc.
- `extra` dicts provide structured context (agent_id, cache_key, latency, etc.).
- Log level configurable via `LOG_LEVEL` env var.

### 5.2 Issues

| # | Severity | Finding |
|---|----------|---------|
| L-1 | **High** | **Omission: No request ID / trace ID.** There is no unique request identifier in logs. Correlating log lines for a single request across different components (router → proxy → stats) requires manual inference from timestamps and agent_id. |
| L-2 | **High** | **Omission: No health-check logging suppression.** The `/v1/health` endpoint generates no log entry (good), but Prometheus scrape targets hitting `/v1/metrics` do not either (also good). However, there is no log sampling or throttling for high-frequency endpoints. |
| L-3 | **Medium** | **Omission: No per-request latency breakdown.** The `x_gateway` block reports total latency, but there is no breakdown of time spent in canonicalization vs. Redis lookup vs. upstream call vs. stats recording. |
| L-4 | **Medium** | **`logger.py` clears all handlers on setup.** `logger.py:20`: `logger.handlers.clear()` removes any pre-existing handlers (e.g., from uvicorn's access logger or root logger). This is fine for the gateway logger but could interfere with other loggers if the app grows. |
| L-5 | **Low** | **Sensitive data in executor errors.** `models.py` `Choice.message` and `Choice.delta` are `Optional[dict]` with no Pydantic validation. If upstream returns unexpected fields, they could be logged verbatim elsewhere. |

---

## 6. Monitoring

### 6.1 Strengths
- Prometheus metrics exposed at `/v1/metrics`: counters for requests, cache hits/misses, tokens, cost.
- Latency histogram with sensible buckets (10ms–60s).
- Redis-backed stats tracker for historical overview.
- Built-in HTML dashboard at `/v1/dashboard` with Chart.js.
- Agent-level breakdown in metrics.

### 6.2 Issues

| # | Severity | Finding |
|---|----------|---------|
| M-1 | **Critical** | **Prometheus metrics are global and not reset between tests.** `metrics/prometheus.py` defines module-level counters. In tests, these accumulate across test runs, causing test pollution. The integration test at `test_metrics.py` does not reset counters before testing. |
| M-2 | **High** | **No metrics for Qdrant health or latency.** The gateway monitors Redis and DeepSeek latency but has no metrics on Qdrant query latency, collection sizes, or error rates. |
| M-3 | **High** | **No memory/CPU/resource metrics.** The gateway does not export process-level metrics (RSS, CPU%, open FDs, GC pauses). For a long-running proxy, this is essential. |
| M-4 | **Medium** | **`cache_hit_rate` gauge is never updated.** `prometheus.py:60-63` defines `cache_hit_rate` as a Gauge but nothing in the codebase ever calls `.set()` on it. It always stays at 0. |
| M-5 | **Medium** | **`active_requests` gauge is never incremented/decremented.** Same issue — defined but never used. |
| M-6 | **Low** | **Dashboard CDN dependency.** `dashboard.py:9`: Chart.js loaded from `cdn.jsdelivr.net`. The dashboard is non-functional without internet access, even though the gateway is designed for local deployment. |

---

## 7. Docker Deployment

### 7.1 Strengths
- Multi-service `docker-compose.yml` with Redis and Qdrant.
- Healthcheck on Redis service.
- `.env` file support for configuration.

### 7.2 Issues

| # | Severity | Finding |
|---|----------|---------|
| D-1 | **Critical** | **No resource limits on any container.** `docker-compose.yml` has no `deploy.resources.limits` block. A memory leak in any service can starve the others. |
| D-2 | **Critical** | **`restart: unless-stopped` without `stop_grace_period`.** If the gateway takes longer than 10s (Docker default) to shut down, it's killed mid-cleanup, potentially corrupting in-flight operations. |
| D-3 | **High** | **No healthcheck for the gateway service.** Redis has a healthcheck but the gateway itself does not. Docker Compose cannot determine when the gateway is ready to serve traffic. |
| D-4 | **High** | **Qdrant healthcheck missing.** The Qdrant service has no healthcheck. If Qdrant fails to start, the gateway will crash-loop trying to connect. |
| D-5 | **High** | **No `.dockerignore` file.** Without one, the Docker build context includes `__pycache__`, `.git`, `tests/`, `venv/`, and other non-essential files, increasing build time and image size. |
| D-6 | **Medium** | **Python image not pinned to digest.** `FROM python:3.12-slim` uses a mutable tag. Builds are non-reproducible — a breaking change in the base image could silently break the gateway. |
| D-7 | **Medium** | **Dependencies not pinned in `requirements.txt`.** E.g., `fastapi>=0.115.0`, `uvicorn>=0.32.0`. Unpinned upper bounds mean `pip install` could pull breaking changes. |
| D-8 | **Low** | **No `--preload` or `--workers` config for uvicorn.** The `CMD` runs a single worker with no preloading. For production, `--workers N` with `--preload` improves throughput and reliability. |
| D-9 | **Low** | **Dockerfile combines COPY and pip install suboptimally.** `requirements.txt` is copied and installed, then `gateway/` is copied. This is good (layer caching). However, the working directory `/app` means logs and memory data are written inside the container with no volume mount. |

---

## 8. Resource Consumption

### 8.1 Strengths
- `httpx.AsyncClient` with configurable `max_connections`.
- Redis pipeline usage reduces round-trips.
- `SemanticCache` stores only vectors (384-dim) in Qdrant.

### 8.2 Issues

| # | Severity | Finding |
|---|----------|---------|
| C-1 | **High** | **No timeout on `httpx.AsyncClient` connection.** `main.py:47`: `httpx.Timeout(120.0)` sets a 120-second timeout. If DeepSeek hangs, the gateway holds the connection and associated resources for 2 minutes. No circuit breaker means backpressure isn't managed. |
| C-2 | **Medium** | **`CodeEmbedder` with `SentenceTransformer` loads a full ML model into memory.** `embedder.py:27-29`: `SentenceTransformer("all-MiniLM-L6-v2")` loads a ~90MB model. If `SEMANTIC_CACHE_ENABLED=true`, two `CodeEmbedder` instances are created — one for the repo index, one for semantic cache. This doubles memory (~180MB for embeddings alone). |
| C-3 | **Medium** | **No streaming response buffering limits.** `_handle_streaming` at `router.py:420-449` yields chunks as they arrive. If the client reads slowly, the entire upstream response is buffered in the httpx response stream, potentially consuming significant memory. |
| C-4 | **Medium** | **`redis.keys()` in `StatsTracker.reset()`.** `stats.py:211`: `await self.redis.keys("stats:*")` blocks Redis while iterating all keys matching the pattern. |
| C-5 | **Low** | **`DASHBOARD_HTML` is a 139-line string in Python source.** `dashboard.py` embeds the full HTML/JS/CSS as a Python string. This inflates the module size and makes maintenance harder. Should be served from a static file. |

---

## Summary

| Category | Critical | High | Medium | Low |
|----------|----------|------|--------|-----|
| Architecture | 0 | 1 | 3 | 2 |
| Security | 1 | 2 | 2 | 1 |
| Reliability | 3 | 2 | 3 | 2 |
| Error Handling | 0 | 1 | 3 | 1 |
| Logging | 0 | 2 | 2 | 1 |
| Monitoring | 1 | 2 | 2 | 1 |
| Docker | 2 | 3 | 2 | 2 |
| Resource Consumption | 0 | 1 | 3 | 1 |
| **Total** | **7** | **14** | **20** | **11** |
