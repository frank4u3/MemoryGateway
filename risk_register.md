# Risk Register — Memory Gateway

**Date:** 2026-06-08
**Version:** 0.5.0

Each risk is scored: **Likelihood** (1–5) × **Impact** (1–5) = **Risk Score** (1–25).  
Mitigation status: 🟢 Mitigated, 🟡 Partial, 🔴 Unmitigated.

---

## R-001: Unbounded upstream spending
| Field | Value |
|-------|-------|
| **Description** | No rate limiting, no request size caps, no cost controls. A misconfigured agent or infinite loop could generate thousands of DeepSeek API calls in minutes, incurring unbounded cost. |
| **Likelihood** | 3 (Moderate — agents can loop) |
| **Impact** | 5 (Very High — direct financial cost) |
| **Risk Score** | **15** 🔴 |
| **Mitigation** | Add per-agent rate limiting (token bucket), request size limits, daily spending caps. |
| **Owner** | Gateway maintainer |

---

## R-002: Redis KEYS command blocks production
| Field | Value |
|-------|-------|
| **Description** | `ExactCache.delete()`, `ExactCache.size()`, and `StatsTracker.reset()` use `redis.keys()` which is O(N) and blocks Redis on large datasets. With thousands of cached entries, this causes multi-second Redis pauses. |
| **Likelihood** | 4 (High — will happen as cache grows) |
| **Impact** | 4 (High — blocks all Redis operations) |
| **Risk Score** | **16** 🔴 |
| **Mitigation** | Replace `KEYS` with `SCAN` for iteration. Use `DBSIZE` or dedicated counters for `size()`. |
| **Owner** | Gateway maintainer |

---

## R-003: No upstream retry or circuit breaker
| Field | Value |
|-------|-------|
| **Description** | Every upstream failure (network blip, rate limit, server error) immediately propagates as 502 to the client. Without retries or circuit breaker, a transient outage causes sustained failures for all agents. |
| **Likelihood** | 4 (High — DeepSeek has rate limits and transient errors) |
| **Impact** | 3 (Medium — degraded UX, retries could recover) |
| **Risk Score** | **12** 🔴 |
| **Mitigation** | Add exponential backoff retry (3 attempts, jitter). Add circuit breaker after 5 consecutive failures. |
| **Owner** | Gateway maintainer |

---

## R-004: Graceful degradation failure on Redis/Qdrant outage
| Field | Value |
|-------|-------|
| **Description** | If Redis or Qdrant is unreachable at startup, the gateway crashes. There is no fallback to degraded mode (passthrough without caching). |
| **Likelihood** | 3 (Moderate — container restarts, network issues) |
| **Impact** | 3 (Medium — gateway is down) |
| **Risk Score** | **9** 🟡 |
| **Mitigation** | Add connection retry with backoff at startup. If all retries fail, start in degraded mode with logging. |
| **Owner** | Gateway maintainer |

---

## R-005: CORS wide-open policy
| Field | Value |
|-------|-------|
| **Description** | `allow_origins=["*"]` with `allow_credentials=True`. If the gateway is ever exposed beyond localhost, any website can make authenticated requests. |
| **Likelihood** | 1 (Low — local-only deployment) |
| **Impact** | 5 (Very High — credential theft, CSRF) |
| **Risk Score** | **5** 🟡 |
| **Mitigation** | Bind to `127.0.0.1` only in production, or restrict CORS to known origins. |
| **Owner** | Gateway maintainer |

---

## R-006: API key leakage in logs
| Field | Value |
|-------|-------|
| **Description** | `proxy.py` logs the full upstream error body. If DeepSeek returns the API key in an error message (e.g., malformed auth), it is written to logs in plaintext. |
| **Likelihood** | 2 (Low — requires specific error condition) |
| **Impact** | 4 (High — leaked credential) |
| **Risk Score** | **8** 🟡 |
| **Mitigation** | Sanitize log output: redact patterns that look like API keys (`sk-...`, `Bearer ...`) before logging. |
| **Owner** | Gateway maintainer |

---

## R-007: Streaming error silently drops response
| Field | Value |
|-------|-------|
| **Description** | In `_handle_streaming`, if DeepSeek returns an error during streaming, the client receives HTTP 200 with an incomplete/empty body. No error signal is sent to the client. |
| **Likelihood** | 3 (Moderate — upstream errors happen) |
| **Impact** | 2 (Low — client must detect truncated response) |
| **Risk Score** | **6** 🟡 |
| **Mitigation** | Send an error SSE event or terminate the stream with an error chunk. |
| **Owner** | Gateway maintainer |

---

## R-008: In-memory Qdrant data loss (default config)
| Field | Value |
|-------|-------|
| **Description** | `qdrant_in_memory` defaults to `True`. A gateway restart wipes the entire repo index, semantic cache, and artifact vectors. Users who don't configure production mode lose all cached data on restart. |
| **Likelihood** | 5 (Very High — every restart) |
| **Impact** | 2 (Low — data is regenerated on use) |
| **Risk Score** | **10** 🟡 |
| **Mitigation** | Log a warning on startup when in-memory mode is enabled. Document the need to set `QDRANT_URL` for production persistence. |
| **Owner** | Gateway maintainer |

---

## R-009: No trace ID for request correlation
| Field | Value |
|-------|-------|
| **Description** | Log entries lack a unique request identifier. Debugging issues across components (router → proxy → stats) requires manual correlation. |
| **Likelihood** | 5 (Very High — every request) |
| **Impact** | 2 (Low — operational inconvenience) |
| **Risk Score** | **10** 🟡 |
| **Mitigation** | Generate a `X-Request-ID` on each request, propagate to all log entries and downstream calls. |
| **Owner** | Gateway maintainer |

---

## R-010: Prometheus metrics global state across tests
| Field | Value |
|-------|-------|
| **Description** | Module-level Prometheus counters retain state between test runs. Tests that assert on metric values are flaky due to test order dependencies. |
| **Likelihood** | 4 (High — all test runs) |
| **Impact** | 1 (Low — test reliability only) |
| **Risk Score** | **4** 🟡 |
| **Mitigation** | Reset Prometheus registry in test fixtures, or refactor metrics into a class. |
| **Owner** | Gateway maintainer |

---

## R-011: MemoryStore synchronous I/O blocks event loop
| Field | Value |
|-------|-------|
| **Description** | `memory/store.py` uses `path.write_text()`, `json.dump`, `open()` synchronously. In an async context, this blocks the event loop worker. |
| **Likelihood** | 2 (Low — MemoryStore is rarely called) |
| **Impact** | 2 (Low — latency spike) |
| **Risk Score** | **4** 🟡 |
| **Mitigation** | Use `aiofiles` or `anyio` for async file operations. |
| **Owner** | Gateway maintainer |

---

## R-012: No Docker resource limits
| Field | Value |
|-------|-------|
| **Description** | No container has CPU/memory limits. A memory leak in any service can crash all containers. |
| **Likelihood** | 2 (Low — Python memory leaks are rare) |
| **Impact** | 4 (High — entire system down) |
| **Risk Score** | **8** 🔴 |
| **Mitigation** | Add `deploy.resources.limits` blocks to all services. |
| **Owner** | Gateway maintainer |

---

## R-013: No Qdrant healthcheck
| Field | Value |
|-------|-------|
| **Description** | Docker Compose does not check Qdrant health. If Qdrant fails, the gateway crashes at startup with no clear signal. |
| **Likelihood** | 2 (Low — Qdrant is stable) |
| **Impact** | 3 (Medium — startup failure) |
| **Risk Score** | **6** 🟡 |
| **Mitigation** | Add HTTP healthcheck for Qdrant on port 6333. |
| **Owner** | Gateway maintainer |

---

## Risk Heat Map

```
Impact →
5  | R-001(15) |           |           |           |
4  |           |           | R-002(16)|           | R-005(5*)
3  |           | R-003(12) |           |           |
2  |           |           | R-008(10)|           | R-009(10)
   |           |           | R-009(10)|           |
1  |           |           | R-010(4) | R-011(4)  |
   +-------------------------------------------------→ Likelihood
     1           2           3           4           5
```

\* R-005 (score 5) is low-probability but high-impact. It's acceptable for local-only deployment but must be fixed before any network exposure.

---

## Top 5 Risks by Score

| Rank | ID | Risk | Score | Status |
|------|----|------|-------|--------|
| 1 | R-002 | Redis KEYS command blocks production | 16 | 🔴 |
| 2 | R-001 | Unbounded upstream spending | 15 | 🔴 |
| 3 | R-003 | No upstream retry or circuit breaker | 12 | 🔴 |
| 4 | R-008 | In-memory Qdrant data loss | 10 | 🟡 |
| 5 | R-009 | No trace ID for request correlation | 10 | 🟡 |
